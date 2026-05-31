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
from typing import Any, Dict, Optional, Tuple

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
def save_session_npz(session: Session, path: Path | str,
                     *, incomplete: bool = False) -> Path:
    """Pack a session into a single .npz file.

    Layout: a JSON metadata blob is stored under key ``meta.json``; each
    capture's arrays are stored under ``r{run}_c{capture}_{field}``.

    Parameters
    ----------
    session :
        Session dataclass to serialize.  Whatever's currently in
        ``session.runs[*].captures`` lands in the file — for an
        incremental save mid-run, this will be a partial set.
    path :
        Output .npz file path.  Parent directory created if missing.
    incomplete :
        When True, ``meta["incomplete"]`` is set so POLARIS / loaders
        know this is a mid-run snapshot, not a finished session.
        Cleared (False) on the final end-of-run save.  See
        :func:`save_session_npz_incremental` for the throttled wrapper
        used during a run.
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
                # Audit finding #19: previously dropped, so triangular
                # / hex arrays came back as ``"rect"`` after reload
                # and the Channel Map tab rendered them with the
                # wrong (col, row) packing. ``cable_map`` was also
                # silently None'd, breaking the NeuroNexus path once
                # it's re-enabled. JSON serialises dict keys as
                # strings, so we cast int → str on the way out and
                # cast back on the way in (see load_session_npz).
                "layout": session.test.array.layout,
                "cable_map": (
                    None if session.test.array.cable_map is None
                    else {str(k): int(v)
                          for k, v in session.test.array.cable_map.items()}
                ),
            },
        },
        "runs": runs_meta,
        # Mid-run snapshot marker.  False (the default) for finished
        # sessions.  True only when ``save_session_npz_incremental``
        # is writing during a run — loaders can surface a "[INCOMPLETE
        # RUN]" badge so the operator knows the data isn't final.
        # See ``save_session_npz_incremental`` for the throttled wrapper.
        "incomplete": bool(incomplete),
        # Task #57 — richer session metadata + per-run notes / tags.
        # Operator-typed free-text notes; multi-line; round-trips
        # verbatim including newlines.
        "notes": getattr(session, "notes", "") or "",
        # Categorical tags (lowercase, normalized).  Stored as a
        # list of strings so loaders can iterate.
        "tags": list(getattr(session, "tags", []) or []),
        # Reproducibility metadata snapshot — git hash, package
        # versions, OS, hardware identity, setup-snapshot SHA-256.
        # See stimtest/session_metadata.py for the field catalog
        # and capture_system_metadata() for the canonical builder.
        # Flat str → str dict to keep JSON round-trip trivial.
        "system_metadata": dict(
            getattr(session, "system_metadata", {}) or {}),
    }
    arrays["meta.json"] = np.frombuffer(
        json.dumps(meta, default=_default).encode("utf-8"), dtype=np.uint8,
    )
    np.savez_compressed(path, **arrays)
    return path


def save_session_npz_incremental(
    session: Session,
    path: Path | str,
    *,
    last_save_at: Optional[float] = None,
    min_interval_s: float = 2.0,
    last_capture_count: int = 0,
    min_capture_interval: int = 1,
) -> Tuple[Path, float, int, bool]:
    """Throttled mid-run save for crash recovery.

    Called by experiment runners after each capture.  Writes the
    same .npz the final save would (same path, same schema, same
    arrays) but marks ``incomplete=True`` in meta so loaders can
    distinguish a mid-run snapshot from a finished session.

    Throttling prevents per-capture saves from dominating runtime
    on long runs.  Default: write at most once every 2 seconds OR
    every capture (whichever is more often).  Tune via
    ``min_interval_s`` / ``min_capture_interval``.

    Parameters
    ----------
    session, path :
        Same as :func:`save_session_npz`.
    last_save_at :
        ``time.monotonic()`` timestamp from the previous successful
        save (None on first call).  Compared against ``min_interval_s``.
    min_interval_s :
        Minimum seconds between consecutive incremental writes.
        ``0`` disables the time throttle.
    last_capture_count :
        Total captures across all runs at the previous successful
        save.  Compared against ``min_capture_interval``.
    min_capture_interval :
        Minimum new captures since last save before writing.  ``1``
        means save after every new capture (most frequent).

    Returns
    -------
    (path, new_last_save_at, new_last_capture_count, did_write) :
        ``did_write`` is False when throttled — caller passes the
        existing throttle state through unchanged.  When True,
        ``new_last_save_at`` and ``new_last_capture_count`` are the
        updated throttle state to thread into the next call.

    Failures are LOGGED via the standard Python logging module and
    re-raised — callers should catch + log to LogPane + continue
    the run.  Losing the partial-save is annoying; aborting the run
    over a partial-save failure is worse.
    """
    import time
    import logging

    _log = logging.getLogger(__name__)

    # Throttle check: skip the write if we're inside the min-interval
    # AND haven't accumulated enough new captures.  Either condition
    # being satisfied (enough time OR enough captures) triggers a write.
    now = time.monotonic()
    current_capture_count = sum(len(r.captures) for r in session.runs)
    new_captures = current_capture_count - last_capture_count
    time_elapsed = (now - last_save_at) if last_save_at is not None else float("inf")

    enough_time = time_elapsed >= min_interval_s
    enough_captures = new_captures >= min_capture_interval

    # First call always writes (last_save_at is None).
    if last_save_at is not None and not (enough_time or enough_captures):
        return (Path(path), last_save_at, last_capture_count, False)

    written_path = save_session_npz(session, path, incomplete=True)
    return (written_path, now, current_capture_count, True)


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
                "ActiveDrivingVoltage": c.metrics.active_driving_voltage_per_phase_v,
                "ReturnDrivingVoltage": c.metrics.return_driving_voltage_per_phase_v,
                "InterpulsePotential": c.metrics.interpulse_potential_v,
                "AccessVoltage": c.metrics.access_voltage_per_phase_v,
                "AccessResistance": c.metrics.access_resistance_per_phase_kohm,
                "ReturnAccessVoltage": c.metrics.return_access_voltage_per_phase_v,
                "ReturnAccessResistance": c.metrics.return_access_resistance_per_phase_kohm,
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
            raise ValueError(f"{path} is not a PULSAR session .npz (no meta.json blob)")
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
            raise ValueError(f"{path} is not a PULSAR session .npz (no meta.json blob)")
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
    # ``cable_map`` and ``layout`` were added later (audit #19); legacy
    # archives won't carry them. ``.get`` with sane defaults makes the
    # loader robust against pre-fix .npz files. JSON serialises dict
    # keys as strings, so we cast the cable_map keys back to int here
    # (the in-memory contract is ``Dict[int, int]``).
    raw_cable = a.get("cable_map")
    cable_map: "Optional[dict[int, int]]"
    if raw_cable is None:
        cable_map = None
    else:
        try:
            cable_map = {int(k): int(v) for k, v in raw_cable.items()}
        except (TypeError, ValueError):
            # Corrupt / non-numeric entries → drop the override and
            # fall back to identity routing rather than blow up the
            # load.
            cable_map = None
    array = ElectrodeArray(
        name=a["name"], rows=a["rows"], cols=a["cols"],
        sites=[ElectrodePosition(**s) for s in a["sites"]],
        cable_map=cable_map,
        layout=a.get("layout", "rect"),
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

    # Task #57: notes / tags / system_metadata are NEW fields.
    # Defensive defaults make loading legacy .npz files (no key)
    # still work — meta.get() returns the default rather than
    # KeyError'ing.  Tags get sanitized to a list of strings.
    _notes = meta.get("notes", "") or ""
    _tags_raw = meta.get("tags", []) or []
    _tags = [str(t).strip().lower() for t in _tags_raw
             if isinstance(t, str) and str(t).strip()]
    _sysmd_raw = meta.get("system_metadata", {}) or {}
    _sysmd = {str(k): str(v) for k, v in _sysmd_raw.items()
              if isinstance(_sysmd_raw, dict)}

    session = Session(
        notebook=meta.get("notebook", ""),
        subject=meta.get("subject", ""),
        test=test,
        user_name=meta.get("user_name", ""),
        user_email=meta.get("user_email", ""),
        created_at=_parse_iso(meta.get("created_at")),
        finished_at=_parse_iso(meta.get("finished_at")),
        notes=_notes,
        tags=_tags,
        system_metadata=_sysmd,
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
            # Audit finding #6: save_session_npz writes every
            # CaptureMetrics field via ``asdict(c)`` (see
            # ``_capture_to_dict``), but the reload previously
            # restored only 12 of the 20 fields. The missing nine —
            # Shannon k, damage classification / criteria / band /
            # level, NeurostimML verdict / probability, and pre /
            # post return-electrode potentials — silently came back
            # at their dataclass defaults, so Viewer / damage
            # screens lost every per-capture verdict on reload.
            # Every ``.get`` below uses the dataclass default so a
            # legacy archive that lacks the key still loads.
            metrics = CaptureMetrics(
                driving_voltage_v=metrics_d.get("driving_voltage_v", float("nan")),
                active_driving_voltage_per_phase_v=list(
                    metrics_d.get("active_driving_voltage_per_phase_v", [])),
                return_driving_voltage_per_phase_v=list(
                    metrics_d.get("return_driving_voltage_per_phase_v", [])),
                access_voltage_per_phase_v=list(metrics_d.get("access_voltage_per_phase_v", [])),
                access_resistance_per_phase_kohm=list(metrics_d.get("access_resistance_per_phase_kohm", [])),
                return_access_voltage_per_phase_v=list(
                    metrics_d.get("return_access_voltage_per_phase_v", [])),
                return_access_resistance_per_phase_kohm=list(
                    metrics_d.get("return_access_resistance_per_phase_kohm", [])),
                polarization_per_phase_v=list(metrics_d.get("polarization_per_phase_v", [])),
                return_polarization_per_phase_v=list(metrics_d.get("return_polarization_per_phase_v", [])),
                charge_per_phase_nc=metrics_d.get("charge_per_phase_nc", float("nan")),
                charge_injection_mc_per_cm2=metrics_d.get("charge_injection_mc_per_cm2", float("nan")),
                effective_capacitance_nf=metrics_d.get("effective_capacitance_nf", float("nan")),
                driving_capacitance_mf_per_cm2=metrics_d.get("driving_capacitance_mf_per_cm2", float("nan")),
                # Interpulse potential — added later, so pre-existing
                # archives won't carry the key. The default float NaN
                # makes the loader robust to legacy npz files.
                interpulse_potential_v=metrics_d.get("interpulse_potential_v", float("nan")),
                # E_ret pre/post-pulse rest potentials — separate from
                # the combined ``interpulse_potential_v`` so a future
                # drift-watcher can compare the two halves per-capture.
                return_pre_pulse_potential_v=metrics_d.get(
                    "return_pre_pulse_potential_v", float("nan")),
                return_post_pulse_potential_v=metrics_d.get(
                    "return_post_pulse_potential_v", float("nan")),
                # Shannon-based damage screen.
                shannon_k_value=metrics_d.get(
                    "shannon_k_value", float("nan")),
                damage_classification=metrics_d.get(
                    "damage_classification", "insufficient_data"),
                damage_criteria=dict(metrics_d.get("damage_criteria", {})),
                damage_band=metrics_d.get("damage_band", ""),
                damage_level=int(metrics_d.get("damage_level", -1)),
                # NeurostimML (Li et al. 2024 RF-Partial-19) verdict.
                neurostimml_classification=metrics_d.get(
                    "neurostimml_classification", "model_not_installed"),
                neurostimml_probability=metrics_d.get(
                    "neurostimml_probability", float("nan")),
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
