"""Save / load session data.

Two formats are supported:

* ``.npz`` (NumPy zip) — fast, native, lossless for arrays + metadata JSON.
* ``.mat`` (MATLAB v7) — for parity with the original ``saveData`` workflow.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import numpy as np

from .session import Capture, ChannelRun, Session


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------
def _default(o: Any) -> Any:
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, datetime):
        return o.isoformat()
    if is_dataclass(o):
        return asdict(o)
    raise TypeError(f"Cannot JSON-encode {type(o)}")


def _capture_to_dict(c: Capture) -> Dict[str, Any]:
    d = asdict(c)
    # Strip the heavy arrays from the dict-form; we'll save them separately
    for arr_field in ("time_us", "v_mon_v", "i_mon_ua", "e_act_v", "e_ret_v"):
        d.pop(arr_field, None)
    return d


# ---------------------------------------------------------------------------
# .npz
# ---------------------------------------------------------------------------
def save_session_npz(session: Session, path: Path | str) -> Path:
    """Pack a session into a single .npz file.

    Layout: a JSON metadata blob is stored under key ``meta.json``; each
    capture's arrays are stored under ``r{run}_c{capture}_{field}``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    arrays: Dict[str, np.ndarray] = {}
    runs_meta = []
    for run_idx, run in enumerate(session.runs):
        captures_meta = []
        for cap_idx, c in enumerate(run.captures):
            tag = f"r{run_idx}_c{cap_idx}"
            arrays[f"{tag}_time_us"] = np.asarray(c.time_us)
            arrays[f"{tag}_v_mon_v"] = np.asarray(c.v_mon_v)
            arrays[f"{tag}_i_mon_ua"] = np.asarray(c.i_mon_ua)
            if c.e_act_v is not None:
                arrays[f"{tag}_e_act_v"] = np.asarray(c.e_act_v)
            if c.e_ret_v is not None:
                arrays[f"{tag}_e_ret_v"] = np.asarray(c.e_ret_v)
            captures_meta.append(_capture_to_dict(c))
        runs_meta.append({
            "configuration": asdict(run.configuration),
            "surface_area_um2": run.surface_area_um2,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "captures": captures_meta,
        })

    meta = {
        "notebook": session.notebook,
        "subject": session.subject,
        "user_name": session.user_name,
        "user_email": session.user_email,
        "created_at": session.created_at.isoformat(),
        "finished_at": session.finished_at.isoformat() if session.finished_at else None,
        "test": {
            "experiment": session.test.experiment,
            "duration_s": session.test.duration_s,
            "polarization_method": session.test.polarization_method,
            "counter_electrode_label": session.test.counter_electrode_label,
            "reference_electrode_label": session.test.reference_electrode_label,
            "target_charge_phase_nc": session.test.target_charge_phase_nc,
            "configuration": asdict(session.test.configuration),
            "pattern": _pattern_dict(session.test.pattern),
            "array": {
                "name": session.test.array.name,
                "rows": session.test.array.rows,
                "cols": session.test.array.cols,
                "sites": [asdict(s) for s in session.test.array.sites],
            },
        },
        "runs": runs_meta,
    }
    arrays["meta.json"] = np.frombuffer(
        json.dumps(meta, default=_default).encode("utf-8"), dtype=np.uint8,
    )
    np.savez_compressed(path, **arrays)
    return path


def _pattern_dict(p) -> Dict[str, Any]:
    return {
        "rate_hz": p.rate_hz,
        "repetitions": p.repetitions,
        "phases": [asdict(ph) for ph in p.phases],
    }


# ---------------------------------------------------------------------------
# .mat (MATLAB)
# ---------------------------------------------------------------------------
def save_session_mat(session: Session, path: Path | str) -> Path:
    """Save in MATLAB v7 format. Mirrors the layout used by ``saveData.m``
    closely enough that downstream MATLAB scripts can load it as ``File``.

    Requires ``scipy.io.savemat``.
    """
    try:
        from scipy.io import savemat
    except ImportError as e:
        raise RuntimeError(
            "scipy is required for .mat export; pip install scipy"
        ) from e
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    runs = []
    for run in session.runs:
        captures = []
        for c in run.captures:
            captures.append({
                "Index": c.index,
                "Time": np.asarray(c.time_us),
                "VoltageMonitor": np.asarray(c.v_mon_v),
                "CurrentMonitor": np.asarray(c.i_mon_ua),
                "ActivePotential": np.asarray(c.e_act_v) if c.e_act_v is not None else np.array([]),
                "ReturnPotential": np.asarray(c.e_ret_v) if c.e_ret_v is not None else np.array([]),
                "Amplitude": c.pattern.excitation_phase.amplitude_ua,
                "ChargePhase": c.metrics.charge_per_phase_nc,
                "ChargeInjection": c.metrics.charge_injection_mc_per_cm2,
                "DrivingVoltage": c.metrics.driving_voltage_v,
                "AccessVoltage": c.metrics.access_voltage_per_phase_v,
                "AccessResistance": c.metrics.access_resistance_per_phase_kohm,
                "PotentialExcursion": c.metrics.polarization_per_phase_v,
                "ReturnExcursion": c.metrics.return_polarization_per_phase_v,
                "EffectiveCapacitance": c.metrics.effective_capacitance_nf,
                "DrivingCapacitance": c.metrics.driving_capacitance_mf_per_cm2,
            })
        runs.append({
            "ID": run.configuration.display_name(),
            "ActiveChannel": run.configuration.active,
            "Returns": list(run.configuration.returns),
            "ConfigurationID": run.configuration.id,
            "SurfaceArea": run.surface_area_um2,
            "Capture": captures,
        })

    File = {
        "Notebook": session.notebook,
        "Subject": session.subject,
        "Test": {
            "ID": session.test.experiment,
            "Experiment": session.test.experiment,
            "Duration": session.test.duration_s,
        },
        "Parameters": {
            "Configuration": asdict(session.test.configuration),
            "PolarizationMethod": session.test.polarization_method,
            "Pattern": _pattern_dict(session.test.pattern),
        },
        "Data": runs,
        "DateTimeCreated": session.created_at.isoformat(),
        "DateTimeModified": (session.finished_at or datetime.now()).isoformat(),
    }
    savemat(str(path), {"File": File})
    return path


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def load_session_meta(path: Path | str) -> Dict[str, Any]:
    """Quick load of just the metadata blob from a ``.npz`` session file."""
    path = Path(path)
    with np.load(path, allow_pickle=False) as z:
        if "meta.json" not in z:
            raise ValueError(f"{path} is not a stimtest session .npz")
        raw = z["meta.json"].tobytes().decode("utf-8")
        return json.loads(raw)


def load_session_npz(path: Path | str) -> "Session":
    """Reverse of :func:`save_session_npz` — rebuild a full Session from disk.

    The metadata blob (``meta.json``) carries dataclass-shaped dicts for
    every Configuration / ElectrodeArray / CaptureMetrics; the heavy
    waveform arrays live alongside under ``r{run}_c{cap}_{field}`` keys.
    We reconstruct the dataclass tree, then attach the arrays back onto
    each :class:`Capture`.
    """
    from .electrode import Configuration, ElectrodeArray, ElectrodePosition
    from .session import (Capture, CaptureMetrics, CaptureStatus, ChannelRun,
                          Session, TestParameters)
    from .waveforms import Phase, PulsePattern

    path = Path(path)
    with np.load(path, allow_pickle=False) as z:
        if "meta.json" not in z:
            raise ValueError(f"{path} is not a stimtest session .npz")
        meta = json.loads(z["meta.json"].tobytes().decode("utf-8"))
        arrays = {k: z[k] for k in z.files if k != "meta.json"}

    # ----- pattern -----
    p = meta["test"]["pattern"]
    pattern = PulsePattern(
        phases=[Phase(**ph) for ph in p["phases"]],
        rate_hz=p.get("rate_hz", 50.0),
        repetitions=p.get("repetitions", 0),
    )

    # ----- array -----
    a = meta["test"]["array"]
    array = ElectrodeArray(
        name=a["name"], rows=a["rows"], cols=a["cols"],
        sites=[ElectrodePosition(**s) for s in a["sites"]],
    )

    # ----- top-level configuration (the session-default) -----
    cfg_d = meta["test"]["configuration"]
    cfg = Configuration(
        id=cfg_d["id"], active=cfg_d["active"],
        returns=tuple(cfg_d.get("returns", ())),
        counter_electrode_label=cfg_d.get("counter_electrode_label", "Pt counter"),
    )

    test = TestParameters(
        experiment=meta["test"]["experiment"],
        pattern=pattern,
        configuration=cfg,
        array=array,
        duration_s=meta["test"].get("duration_s", 60.0),
        polarization_method=meta["test"].get("polarization_method", "time"),
        counter_electrode_label=meta["test"].get("counter_electrode_label", "Pt counter"),
        reference_electrode_label=meta["test"].get("reference_electrode_label", "Ag|AgCl"),
        target_charge_phase_nc=meta["test"].get("target_charge_phase_nc", float("inf")),
    )

    session = Session(
        notebook=meta.get("notebook", ""),
        subject=meta.get("subject", ""),
        test=test,
        user_name=meta.get("user_name", ""),
        user_email=meta.get("user_email", ""),
        created_at=_parse_iso(meta.get("created_at")),
        finished_at=_parse_iso(meta.get("finished_at")),
    )

    # ----- runs and captures -----
    for run_idx, run_meta in enumerate(meta.get("runs", [])):
        rcfg_d = run_meta["configuration"]
        rcfg = Configuration(
            id=rcfg_d["id"], active=rcfg_d["active"],
            returns=tuple(rcfg_d.get("returns", ())),
            counter_electrode_label=rcfg_d.get("counter_electrode_label", "Pt counter"),
        )
        run = ChannelRun(
            configuration=rcfg,
            surface_area_um2=run_meta.get("surface_area_um2", 5000.0),
            started_at=_parse_iso(run_meta.get("started_at")) or session.created_at,
            finished_at=_parse_iso(run_meta.get("finished_at")),
        )
        for cap_idx, cap_meta in enumerate(run_meta.get("captures", [])):
            tag = f"r{run_idx}_c{cap_idx}"

            # Per-capture pattern: stored captures may have rescaled amplitudes
            cap_pat_d = cap_meta.get("pattern", p)
            cap_pat = PulsePattern(
                phases=[Phase(**ph) for ph in cap_pat_d["phases"]],
                rate_hz=cap_pat_d.get("rate_hz", pattern.rate_hz),
                repetitions=cap_pat_d.get("repetitions", 0),
            )

            metrics_d = cap_meta.get("metrics", {})
            metrics = CaptureMetrics(
                driving_voltage_v=metrics_d.get("driving_voltage_v", float("nan")),
                access_voltage_per_phase_v=list(metrics_d.get("access_voltage_per_phase_v", [])),
                access_resistance_per_phase_kohm=list(metrics_d.get("access_resistance_per_phase_kohm", [])),
                polarization_per_phase_v=list(metrics_d.get("polarization_per_phase_v", [])),
                return_polarization_per_phase_v=list(metrics_d.get("return_polarization_per_phase_v", [])),
                charge_per_phase_nc=metrics_d.get("charge_per_phase_nc", float("nan")),
                charge_injection_mc_per_cm2=metrics_d.get("charge_injection_mc_per_cm2", float("nan")),
                effective_capacitance_nf=metrics_d.get("effective_capacitance_nf", float("nan")),
                driving_capacitance_mf_per_cm2=metrics_d.get("driving_capacitance_mf_per_cm2", float("nan")),
            )

            status_d = cap_meta.get("status", {})
            status = CaptureStatus(
                good=status_d.get("good", True),
                reached_potential_limit=status_d.get("reached_potential_limit", False),
                voltage_compliance=status_d.get("voltage_compliance", False),
                aborted=status_d.get("aborted", False),
                notes=status_d.get("notes", ""),
            )

            cap = Capture(
                index=cap_meta.get("index", cap_idx),
                pattern=cap_pat,
                timestamp=_parse_iso(cap_meta.get("timestamp")) or session.created_at,
                time_us=arrays.get(f"{tag}_time_us", np.zeros(0)),
                v_mon_v=arrays.get(f"{tag}_v_mon_v", np.zeros(0)),
                i_mon_ua=arrays.get(f"{tag}_i_mon_ua", np.zeros(0)),
                e_act_v=arrays.get(f"{tag}_e_act_v"),
                e_ret_v=arrays.get(f"{tag}_e_ret_v"),
                metrics=metrics,
                status=status,
            )
            run.captures.append(cap)
        session.runs.append(run)
    return session


def _parse_iso(s):
    """Return a datetime from an ISO string, or None on missing/bad input."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Excel (Gamry-DTA-style)
# ---------------------------------------------------------------------------
def save_session_xlsx(session, path, **kwargs):
    """Convenience re-export of :func:`stimtest.gamry_export.save_session_xlsx`.

    Workbook layout:
      Sheet 1 ("Instrumentation") : stimulator + scope identity, channel routing
      Sheet 2 ("Parameters")      : pulse pattern, configuration, water window
      Sheet 3..N                  : one per ChannelRun, with SUMMARY + CURVE
                                    tables in Gamry .DTA tab-delimited style
    """
    from .gamry_export import save_session_xlsx as _impl
    return _impl(session, path, **kwargs)


def save_session_dta(session, out_dir, **kwargs):
    """Convenience re-export of :func:`stimtest.gamry_export.save_session_dta`.

    Emits one tab-delimited ``.DTA`` text file per ChannelRun, following the
    Gamry Echem Analyst preamble + TABLE convention. Returns a list of paths.
    """
    from .gamry_export import save_session_dta as _impl
    return _impl(session, out_dir, **kwargs)
