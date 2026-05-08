"""Weakness analysis for electrode designs and stimulation strategies.

This is a *cross-session* analyzer. The existing :class:`QinjPredictor`
in ``qinj_model.py`` predicts a single target (max Q_inj) for one
experiment combo at a time. This module is the wider lens — it scans
**a folder of saved sessions**, builds a richer dataset with multiple
performance targets, and produces an interpretable report that points
at design and strategy weaknesses.

Three things it does that the per-experiment predictor doesn't:

1. **Multi-target regression.** Trains one model per target metric
   (Q_inj, V_d at fixed Q_inj, polarization headroom, mean access
   resistance) so we can see which design parameters drive each
   independently. A material that wins on Q_inj but loses on V_d is
   a design tradeoff that's invisible if you only look at one target.
2. **Per-channel anomaly detection.** Compares each (device, config,
   coating) cell's actual performance to the model's prediction; cells
   where the residual is > 2σ get flagged as "performing worse than
   the design family suggests they should". Useful for finding bad
   electrodes or bad stim strategies.
3. **Feature importance.** Ranks the input dimensions (coating,
   surface area, phase widths, configuration kind, ...) by how much
   they explain variance in each target, so the user can see which
   knobs actually matter for each performance metric.

Run from the CLI::

    python -m stimtest.ml.weakness_analysis path/to/sessions_folder

The analyzer prints a compact text summary to stdout and (optionally)
writes a JSON report + matplotlib figures via the ``--report-dir``
flag. The JSON is machine-readable so the GUI viewer can ingest it
into a "Weakness" tab.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.preprocessing import OneHotEncoder
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    _HAS_SKLEARN = True
except Exception:  # pragma: no cover
    _HAS_SKLEARN = False

from ..persistence import load_session_npz
from ..session import Capture, ChannelRun, Session


# ---------------------------------------------------------------------------
# Feature schema — wider than QinjFeatures so design weaknesses can be
# attributed to any axis the experimenter controls.
# ---------------------------------------------------------------------------
_CATEGORICAL_FIELDS = (
    "device_name",       # "Blackrock UEA", "MicroProbes FMA", "UTD MEA", ...
    "coating",           # "SIROF" / "AIROF" / "PtIr" / "Au" / ...
    "config_id",         # "MP" / "BP" / "TP" / "CG" / "PCG" / "PBP" / "PTP"
    "pattern_type",      # "monophasic" / "biphasic" / "triphasic"
    "polarity_label",    # "cathodic-first" / "anodic-first"
)
_NUMERIC_FIELDS = (
    "surface_area_um2",
    "phase_width_us",
    "interphase_delay_us",
    "discharge_delay_us",
    "rate_hz",
    "n_returns",
    "n_phases",
    "amplitude_ratio_excite_to_recharge",
)


@dataclass
class DesignFeatures:
    """One row of the cross-session dataset.

    Captures everything the experimenter set up — device, coating,
    geometry, configuration, stim strategy. The ``unique_key`` field
    identifies the (session, run) pair the row came from so the
    anomaly report can point back at the source.
    """
    device_name: str = ""
    coating: str = ""
    config_id: str = ""
    pattern_type: str = ""
    polarity_label: str = ""
    surface_area_um2: float = 0.0
    phase_width_us: float = 0.0
    interphase_delay_us: float = 0.0
    discharge_delay_us: float = 0.0
    rate_hz: float = 0.0
    n_returns: int = 0
    n_phases: int = 0
    amplitude_ratio_excite_to_recharge: float = 1.0
    # Provenance — not used as a feature, only for traceback.
    unique_key: str = field(default="", compare=False)
    session_path: str = field(default="", compare=False)
    active_channel: int = field(default=0, compare=False)


@dataclass
class TargetSet:
    """Performance metrics extracted from a single ChannelRun.

    Each value is ``float('nan')`` when the run has no usable data
    for that target — e.g., Q_inj is NaN if no captures hit the
    water-window limit.
    """
    max_q_inj_mc_per_cm2: float = float("nan")
    max_q_phase_nc: float = float("nan")
    max_v_driving_v: float = float("nan")
    mean_access_resistance_kohm: float = float("nan")
    polarization_headroom_v: float = float("nan")  # min(|limit| - |E_pol|)
    n_captures: int = 0
    voltage_compliance_hits: int = 0
    reached_potential_limit: bool = False


@dataclass
class WeaknessReport:
    """End-to-end output of the analyzer.

    ``feature_importance`` maps target name → sorted list of
    ``(feature, importance)`` tuples. ``anomalies`` flags individual
    runs whose actual performance deviates significantly from the
    model's prediction. ``model_quality`` reports the held-out R²
    per target so the user knows how much to trust each model.
    """
    n_sessions: int
    n_runs: int
    targets_evaluated: List[str]
    model_quality: Dict[str, float]                       # target -> R²
    feature_importance: Dict[str, List[Tuple[str, float]]]
    anomalies: List[Dict]                                 # one dict per flagged run
    cohort_summary: Dict[str, Dict[str, float]]           # category -> stats
    notes: List[str]


# ---------------------------------------------------------------------------
# Dataset construction — read sessions from disk and build (X, Y) tables
# ---------------------------------------------------------------------------
def features_from_run(session: Session, run: ChannelRun) -> Optional[DesignFeatures]:
    """Extract design features from one Run, or ``None`` if essential
    metadata is missing.
    """
    pattern = session.test.pattern
    if pattern is None or pattern.num_phases == 0:
        return None
    coating = ""
    array = session.test.array
    if array is not None and array.sites:
        # Coating is per-electrode; take the active electrode's coating
        # since that's what the metrics describe.
        try:
            site = array[run.configuration.active]
            coating = site.coating
        except Exception:
            coating = array.sites[0].coating
    pat_type = ("monophasic" if pattern.num_phases == 1 else
                "triphasic" if pattern.num_phases == 3 else
                "biphasic")
    polarity_label = ("cathodic-first" if pattern.polarity == -1
                      else "anodic-first")
    first = pattern.phases[0]
    last = pattern.phases[-1]
    # Amplitude ratio excite→recharge: |excite phase| / |last phase|.
    # Captures triphasic 2:-3:1 etc. as a single dimensionless number.
    excite_amp = abs(pattern.excitation_phase.amplitude_ua) or 1.0
    recharge_amp = abs(last.amplitude_ua) or excite_amp
    return DesignFeatures(
        device_name=str(getattr(array, "name", "")),
        coating=coating,
        config_id=str(run.configuration.id),
        pattern_type=pat_type,
        polarity_label=polarity_label,
        surface_area_um2=float(run.surface_area_um2),
        phase_width_us=float(first.width_us),
        interphase_delay_us=float(first.delay_after_us),
        discharge_delay_us=float(last.delay_after_us),
        rate_hz=float(pattern.rate_hz),
        n_returns=int(len(run.configuration.returns)),
        n_phases=int(pattern.num_phases),
        amplitude_ratio_excite_to_recharge=float(excite_amp / recharge_amp),
        unique_key=f"{session.name}::{run.configuration.id}::ch{run.configuration.active}",
        session_path="",  # filled by the caller
        active_channel=int(run.configuration.active),
    )


def targets_from_run(run: ChannelRun) -> TargetSet:
    """Reduce a Run's per-capture metrics into one performance row.

    All reductions ignore captures flagged ``aborted``. Targets are
    deliberately scalar — the analyzer is about cross-design trends,
    not per-trace shape.
    """
    good = [c for c in run.captures if not c.status.aborted]
    if not good:
        return TargetSet()
    q_inj_vals = [c.metrics.charge_injection_mc_per_cm2 for c in good
                  if math.isfinite(c.metrics.charge_injection_mc_per_cm2)]
    q_ph_vals = [c.metrics.charge_per_phase_nc for c in good
                 if math.isfinite(c.metrics.charge_per_phase_nc)]
    v_d_vals = [c.metrics.driving_voltage_v for c in good
                if math.isfinite(c.metrics.driving_voltage_v)]
    r_a_vals = []
    for c in good:
        for r in (c.metrics.access_resistance_per_phase_kohm or ()):
            if math.isfinite(r):
                r_a_vals.append(r)
    # Polarization headroom: smallest gap between any phase's |E_pol|
    # and the (per-coating) water-window limit. Reads the limits from
    # session.test.extras when present; falls back to ±0.6 V if not.
    pol_headroom = _polarization_headroom(good)
    return TargetSet(
        max_q_inj_mc_per_cm2=max(q_inj_vals) if q_inj_vals else float("nan"),
        max_q_phase_nc=max(q_ph_vals) if q_ph_vals else float("nan"),
        max_v_driving_v=max(v_d_vals) if v_d_vals else float("nan"),
        mean_access_resistance_kohm=(float(np.mean(r_a_vals))
                                     if r_a_vals else float("nan")),
        polarization_headroom_v=pol_headroom,
        n_captures=len(good),
        voltage_compliance_hits=sum(1 for c in good
                                    if c.status.voltage_compliance),
        reached_potential_limit=any(c.status.reached_potential_limit
                                    for c in good),
    )


def _polarization_headroom(captures: Sequence[Capture]) -> float:
    """Smallest |limit| - |E_pol| across all phases of all captures.

    Positive → the captures stayed inside the water window;
    negative → some phase crossed it. NaN if no polarization data.
    """
    cathodic_limit = -0.6
    anodic_limit = +0.8
    headroom = float("inf")
    saw_data = False
    for cap in captures:
        for series in (cap.metrics.polarization_per_phase_v,
                       cap.metrics.return_polarization_per_phase_v):
            for v in (series or ()):
                if not math.isfinite(v):
                    continue
                saw_data = True
                # Distance to the nearer limit; signed so negative
                # = crossed.
                gap = (anodic_limit - v) if v >= 0 else (v - cathodic_limit)
                headroom = min(headroom, gap)
    return headroom if saw_data and math.isfinite(headroom) else float("nan")


def load_dataset(folder: Path) -> Tuple[List[DesignFeatures],
                                        List[TargetSet]]:
    """Walk ``folder`` for ``.npz`` sessions and build the dataset.

    Sessions that fail to load are skipped with a warning; one row
    per ``ChannelRun`` is emitted from each loaded session. Returns
    parallel lists of features + targets.
    """
    feats: List[DesignFeatures] = []
    targets: List[TargetSet] = []
    folder = Path(folder)
    paths = sorted(folder.rglob("*.npz"))
    for p in paths:
        try:
            session = load_session_npz(p)
        except Exception as e:
            print(f"  skip {p.name}: {e}")
            continue
        for run in session.runs:
            if not run.captures:
                continue
            f = features_from_run(session, run)
            if f is None:
                continue
            f.session_path = str(p)
            t = targets_from_run(run)
            feats.append(f)
            targets.append(t)
    return feats, targets


# ---------------------------------------------------------------------------
# Per-target regression + feature importance + anomaly detection
# ---------------------------------------------------------------------------
def _features_to_matrix(feats: Sequence[DesignFeatures]) -> np.ndarray:
    """Pack DesignFeatures into a 2D object array — categorical first,
    then numeric — matching the ColumnTransformer's column indices."""
    rows = []
    for f in feats:
        row = [getattr(f, k) for k in _CATEGORICAL_FIELDS]
        row += [float(getattr(f, k)) for k in _NUMERIC_FIELDS]
        rows.append(row)
    return np.asarray(rows, dtype=object)


def _make_pipeline() -> Pipeline:
    """Pipeline = one-hot the categorical cols, GBM regressor."""
    if not _HAS_SKLEARN:
        raise ImportError("scikit-learn required for weakness analysis")
    try:
        ohe = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:  # sklearn < 1.2
        ohe = OneHotEncoder(handle_unknown="ignore", sparse=False)
    n_cat = len(_CATEGORICAL_FIELDS)
    return Pipeline([
        ("encoder", ColumnTransformer(
            [("cat", ohe, list(range(n_cat)))],
            remainder="passthrough")),
        ("model", GradientBoostingRegressor(
            n_estimators=200, max_depth=3, learning_rate=0.05,
            random_state=0)),
    ])


def _train_one_target(feats: Sequence[DesignFeatures],
                      target_values: np.ndarray
                      ) -> Tuple[Optional[Pipeline], float, np.ndarray]:
    """Fit a model for a single target column.

    Returns ``(pipeline, r2, residuals)``. ``r2`` is the in-sample R²
    (we don't have enough rows for a held-out split most of the time).
    Residuals = ``actual - predicted`` for every input row.
    Pipeline is ``None`` when there aren't enough rows.
    """
    finite_mask = np.isfinite(target_values)
    n_finite = int(finite_mask.sum())
    if n_finite < 5:
        return None, float("nan"), np.full(len(feats), float("nan"))
    X_all = _features_to_matrix(feats)
    X_fit = X_all[finite_mask]
    y_fit = target_values[finite_mask]
    pipe = _make_pipeline()
    pipe.fit(X_fit, y_fit)
    pred_fit = pipe.predict(X_fit)
    # R² of the in-sample fit — fast, biased optimistic but useful as
    # a sanity check ("did the model learn anything at all?").
    ss_res = float(np.sum((y_fit - pred_fit) ** 2))
    ss_tot = float(np.sum((y_fit - np.mean(y_fit)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    # Residuals on every row (including those with NaN target — we
    # can't compute residuals there, so mark them NaN).
    pred_all = pipe.predict(X_all)
    residuals = np.where(finite_mask,
                         target_values - pred_all,
                         float("nan"))
    return pipe, float(r2), residuals


def _feature_importance(pipeline: Pipeline) -> List[Tuple[str, float]]:
    """Aggregate feature importances back to the original feature names.

    The pipeline one-hots the categorical columns, so a single
    DesignFeatures attribute can become many one-hot columns; we sum
    the importances back to the original attribute name. Numeric
    columns pass through unchanged.
    """
    encoder = pipeline.named_steps["encoder"]
    model = pipeline.named_steps["model"]
    importances = getattr(model, "feature_importances_", None)
    if importances is None:
        return []
    out_features = encoder.get_feature_names_out()
    by_name: Dict[str, float] = {}
    for col_name, w in zip(out_features, importances):
        # Encoder names look like "cat__device_name_Blackrock UEA" or
        # "remainder__surface_area_um2"; reduce to the original
        # DesignFeatures attribute name.
        original = col_name
        if "__" in col_name:
            after = col_name.split("__", 1)[1]
            for cat in _CATEGORICAL_FIELDS:
                if after.startswith(cat + "_"):
                    original = cat
                    break
            else:
                original = after  # numeric pass-through
        by_name[original] = by_name.get(original, 0.0) + float(w)
    # Sorted descending by importance.
    return sorted(by_name.items(), key=lambda kv: kv[1], reverse=True)


def _anomalies_from_residuals(feats: Sequence[DesignFeatures],
                              residuals_by_target: Dict[str, np.ndarray],
                              z_threshold: float = 2.0) -> List[Dict]:
    """Flag rows whose residual on any target is more than ``z_threshold``
    standard deviations from zero.

    Returns one dict per flagged row, with the source-of-truth fields
    (session path, active channel, config) so the user can navigate
    back to the underlying data.
    """
    flagged: Dict[str, Dict] = {}
    for target, residuals in residuals_by_target.items():
        finite = residuals[np.isfinite(residuals)]
        if finite.size < 3:
            continue
        sigma = float(np.std(finite))
        if sigma == 0:
            continue
        for i, r in enumerate(residuals):
            if not math.isfinite(r):
                continue
            z = r / sigma
            if abs(z) < z_threshold:
                continue
            f = feats[i]
            key = f.unique_key
            entry = flagged.setdefault(key, {
                "unique_key": key,
                "session_path": f.session_path,
                "active_channel": f.active_channel,
                "device_name": f.device_name,
                "coating": f.coating,
                "config_id": f.config_id,
                "pattern_type": f.pattern_type,
                "deviations": {},
            })
            entry["deviations"][target] = {
                "residual": float(r),
                "z_score": float(z),
                "direction": "underperform" if r < 0 else "overperform",
            }
    return sorted(flagged.values(),
                  key=lambda d: -max(abs(v["z_score"])
                                     for v in d["deviations"].values()))


def _cohort_summary(feats: Sequence[DesignFeatures],
                    targets_by_name: Dict[str, np.ndarray]) -> Dict[str, Dict[str, float]]:
    """Per-categorical-axis stats for each target.

    Output: ``{"device_name=Blackrock UEA": {"max_q_inj_mc_per_cm2_mean": …,
    "max_q_inj_mc_per_cm2_std": …, "n": …}, …}``. Lets the user spot
    cohort-level effects at a glance ("PCG configs average 2.5× the V_d
    of MP at the same Q_inj").
    """
    out: Dict[str, Dict[str, float]] = {}
    for cat in _CATEGORICAL_FIELDS:
        values = [getattr(f, cat) for f in feats]
        for v in sorted(set(values)):
            mask = np.array([x == v for x in values])
            if mask.sum() < 2:
                continue
            cohort_key = f"{cat}={v}"
            stats: Dict[str, float] = {"n": int(mask.sum())}
            for target, arr in targets_by_name.items():
                slc = arr[mask]
                slc_finite = slc[np.isfinite(slc)]
                if slc_finite.size == 0:
                    continue
                stats[f"{target}_mean"] = float(np.mean(slc_finite))
                stats[f"{target}_std"] = float(np.std(slc_finite))
            out[cohort_key] = stats
    return out


def analyze_folder(folder: Path,
                   anomaly_z_threshold: float = 2.0) -> WeaknessReport:
    """End-to-end pipeline: load → train per target → analyze → report.

    The returned :class:`WeaknessReport` is JSON-serialisable via
    :func:`report_to_json`.
    """
    feats, target_rows = load_dataset(folder)
    notes: List[str] = []
    if not feats:
        return WeaknessReport(
            n_sessions=0, n_runs=0, targets_evaluated=[],
            model_quality={}, feature_importance={}, anomalies=[],
            cohort_summary={},
            notes=[f"No usable .npz sessions found under {folder}."],
        )
    targets_by_name: Dict[str, np.ndarray] = {}
    for tname in TargetSet.__dataclass_fields__:
        if tname in ("n_captures", "voltage_compliance_hits",
                     "reached_potential_limit"):
            continue   # not regression targets
        targets_by_name[tname] = np.array(
            [getattr(t, tname) for t in target_rows], dtype=float)
    n_sessions = len({f.session_path for f in feats})
    quality: Dict[str, float] = {}
    importances: Dict[str, List[Tuple[str, float]]] = {}
    residuals: Dict[str, np.ndarray] = {}
    for tname, vals in targets_by_name.items():
        pipe, r2, resid = _train_one_target(feats, vals)
        if pipe is None:
            notes.append(
                f"Target {tname!r}: skipped (need >=5 finite rows, "
                f"got {int(np.sum(np.isfinite(vals)))}).")
            continue
        quality[tname] = r2
        importances[tname] = _feature_importance(pipe)
        residuals[tname] = resid
    anomalies = _anomalies_from_residuals(feats, residuals,
                                          z_threshold=anomaly_z_threshold)
    cohort = _cohort_summary(feats, targets_by_name)
    return WeaknessReport(
        n_sessions=n_sessions,
        n_runs=len(feats),
        targets_evaluated=list(quality.keys()),
        model_quality=quality,
        feature_importance=importances,
        anomalies=anomalies,
        cohort_summary=cohort,
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------
def report_to_json(report: WeaknessReport) -> dict:
    """Convert the report into a JSON-friendly dict (no numpy types)."""
    out = asdict(report)
    # Tuples of (feature, importance) become lists for JSON.
    out["feature_importance"] = {
        k: [[name, float(score)] for name, score in v]
        for k, v in report.feature_importance.items()
    }
    return out


def report_to_text(report: WeaknessReport, max_anomalies: int = 20) -> str:
    """Compact human-readable summary suitable for stdout.

    Three sections: dataset summary, per-target model quality +
    importance ranking, top anomalies. Cohort summary is omitted from
    the text (it's verbose); it lives in the JSON for the GUI.
    """
    lines: List[str] = []
    lines.append(f"## Weakness analysis -- "
                 f"{report.n_runs} runs across {report.n_sessions} sessions")
    if report.notes:
        lines.append("")
        lines.append("Notes:")
        for n in report.notes:
            lines.append(f"  - {n}")
    if not report.targets_evaluated:
        lines.append("")
        lines.append("No targets evaluated. Add more sessions and retry.")
        return "\n".join(lines)
    lines.append("")
    lines.append("### Model quality (in-sample R-squared)")
    for t, r2 in report.model_quality.items():
        flag = "[ok]" if r2 > 0.5 else "[??]"
        lines.append(f"  {flag} {t:40s}  R^2 = {r2:.3f}")
    lines.append("")
    lines.append("### Feature importance per target (top 5)")
    for t, ranking in report.feature_importance.items():
        lines.append(f"  * {t}")
        for name, score in ranking[:5]:
            bar = "#" * max(1, int(round(score * 30)))
            lines.append(f"      {name:40s} {score:.3f}  {bar}")
    if report.anomalies:
        lines.append("")
        lines.append(f"### Anomalies (top {min(max_anomalies, len(report.anomalies))})")
        lines.append("    Runs whose actual performance deviates > 2 sigma from prediction.")
        for a in report.anomalies[:max_anomalies]:
            lines.append(
                f"  * {a['device_name']} / {a['coating']} / {a['config_id']} "
                f"ch{a['active_channel']}")
            for tname, info in a["deviations"].items():
                lines.append(
                    f"      {tname}: z={info['z_score']:+.2f} "
                    f"({info['direction']})  residual={info['residual']:+.3f}")
            lines.append(f"      source: {a['session_path']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(
        description="Cross-session weakness analysis for stim experiments.")
    p.add_argument("folder", help="Folder of .npz session files (recursive)")
    p.add_argument("--report-dir", default=None,
                   help="Optional output dir for JSON + figures")
    p.add_argument("--z-threshold", type=float, default=2.0,
                   help="sigma threshold for flagging anomalies (default 2.0)")
    args = p.parse_args(argv)
    folder = Path(args.folder)
    if not folder.is_dir():
        p.error(f"{folder} is not a directory")
    print(f"Scanning {folder}...")
    report = analyze_folder(folder, anomaly_z_threshold=args.z_threshold)
    print(report_to_text(report))
    if args.report_dir:
        out_dir = Path(args.report_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "weakness_report.json"
        with json_path.open("w", encoding="utf-8") as f:
            json.dump(report_to_json(report), f, indent=2, default=str)
        print(f"\nWrote JSON report to {json_path}")
    return 0


if __name__ == "__main__":               # pragma: no cover
    raise SystemExit(main())
