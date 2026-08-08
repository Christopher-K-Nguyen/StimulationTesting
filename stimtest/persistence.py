"""Save / load session data.

Two formats are supported:

* ``.npz`` (NumPy zip) — fast, native, lossless for arrays + metadata JSON.
* ``.mat`` (MATLAB v7) — for parity with the original ``saveData`` workflow.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass, replace as _dc_replace
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from .config import DEPOLARIZATION_TIME_US
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
def _jsonable(obj):
    """Best-effort coercion to JSON-serializable types.

    ``test.extras`` is operator-supplied context (Setup snapshot, channel map,
    ramp policy) that is mostly plain data but is not guaranteed to be — and
    the .npz doubles as the crash-recovery artifact, so a metadata value must
    never be able to fail the save.  Anything unrecognised degrades to its
    ``repr`` rather than raising.
    """
    import numpy as _np
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, _np.generic):
        return obj.item()
    if isinstance(obj, _np.ndarray):
        return _jsonable(obj.tolist())
    try:
        from dataclasses import asdict as _asdict, is_dataclass
        if is_dataclass(obj) and not isinstance(obj, type):
            return _jsonable(_asdict(obj))
    except Exception:
        pass
    return repr(obj)


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

    # LP trims snapshot arrays to ``None`` after metrics are computed
    # (memory checkpoint).  ``np.asarray(None)`` yields a 0-d OBJECT array
    # that gets pickled into the .npz and then FAILS to load under numpy's
    # default ``allow_pickle=False`` — which would make a trimmed LP
    # session (and the per-capture incremental crash-recovery save it
    # relies on) unreadable.  Coerce None → empty float array so every
    # trace key stays a plain numeric array.
    def _np(a):
        return np.zeros(0) if a is None else np.asarray(a)

    for run_idx, run in enumerate(session.runs):
        captures_meta = []
        for cap_idx, c in enumerate(run.captures):
            tag = f"r{run_idx}_c{cap_idx}"
            arrays[f"{tag}_time_us"] = _np(c.time_us)
            arrays[f"{tag}_v_mon_v"] = _np(c.v_mon_v)
            arrays[f"{tag}_i_mon_ua"] = _np(c.i_mon_ua)
            if c.e_act_v is not None:
                arrays[f"{tag}_e_act_v"] = np.asarray(c.e_act_v)
            if c.e_ret_v is not None:
                arrays[f"{tag}_e_ret_v"] = np.asarray(c.e_ret_v)
            if getattr(c, "i_ideal_ua", None) is not None:
                arrays[f"{tag}_i_ideal_ua"] = _np(c.i_ideal_ua)
            _cd = _capture_to_dict(c)
            # RAW pre-conversion data (operator: "store the raw waveform data
            # before conversion and scaling").  The int8 CODE arrays go in as
            # arrays — one per channel, small next to the float traces — while
            # the decode constants and channel settings ride in the capture's
            # meta dict.  Splitting them this way keeps meta.json readable and
            # lets a reader pull just the constants without unpacking arrays.
            _raw = getattr(c, "raw_channels", None)
            if isinstance(_raw, dict) and _raw.get("channels"):
                _meta_ch = {}
                for _name, _rec in (_raw.get("channels") or {}).items():
                    if not isinstance(_rec, dict):
                        continue
                    _codes = _rec.get("codes")
                    if _codes is not None:
                        arrays[f"{tag}_raw_{_name}"] = np.asarray(
                            _codes, dtype=np.int8)
                    _meta_ch[_name] = {k: v for k, v in _rec.items()
                                       if k != "codes"}
                _cd["raw_channels"] = {
                    "channels": _meta_ch,
                    "scaling": _raw.get("scaling"),
                    "aliases": _raw.get("aliases"),
                }
            captures_meta.append(_cd)
        runs_meta.append({
            "configuration": asdict(run.configuration),
            "surface_area_um2": run.surface_area_um2,
            "started_at": run.started_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "label": run.label,
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
            # ``extras`` carries the run's whole descriptive context — the
            # Setup snapshot, the device->Plexon channel map, the E_pol time
            # delay, and (for VT) the resolved ramp policy.  It used to be
            # DROPPED, which made an archive un-self-describing: replaying a
            # bench run, there was no way to tell whether Adaptive or
            # Regression had produced it, what the water-window limits were, or
            # which cable map was in force.  Coerced through ``_jsonable`` so a
            # stray non-serializable value degrades to its repr instead of
            # failing the save (the .npz is the crash-recovery artifact — it
            # must never fail to write because of a metadata value).
            "extras": _jsonable(session.test.extras or {}),
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

    # NEVER rewrite the (large, possibly OneDrive-synced) npz when NO new
    # capture has landed since the last save (operator #6): a long PCC run's
    # multi-second capture cycle otherwise re-wrote a fresh ~10 MB snapshot of
    # IDENTICAL data every ``min_interval_s`` (the time throttle fired even
    # though the capture count was unchanged — the profile showed ~14 back-to-
    # back writes all at the same "18 caps").  The time throttle now only CAPS
    # the rate; it never TRIGGERS a redundant write of unchanged data.  This is
    # crash-recovery-safe: with no new data there is nothing new to recover.
    if new_captures <= 0:
        return (Path(path), last_save_at, last_capture_count, False)

    # First call (with ≥1 new capture) always writes (last_save_at is None).
    if last_save_at is not None and not (enough_time or enough_captures):
        return (Path(path), last_save_at, last_capture_count, False)

    written_path = save_session_npz(session, path, incomplete=True)
    return (written_path, now, current_capture_count, True)


def _pattern_dict(p) -> Dict[str, Any]:
    return {
        "rate_hz": p.rate_hz,
        "repetitions": p.repetitions,
        # Burst grouping (defaults = ordinary single-pulse; legacy .npz
        # without these keys load as non-burst).
        "pulses_per_burst": getattr(p, "pulses_per_burst", 1),
        "burst_period_us": getattr(p, "burst_period_us", 0.0),
        "interpulse_discharge_us": getattr(p, "interpulse_discharge_us", 0.0),
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


def _raw_from_npz(cap_dict: dict, arrays, tag: str):
    """Rebuild ``Capture.raw_channels`` from the npz.

    Returns None for captures saved before raw storage existed, so every
    consumer must guard.  Never raises — a diagnostic must not break a load.
    """
    try:
        raw = (cap_dict or {}).get("raw_channels")
        if not isinstance(raw, dict):
            return None
        chans = {}
        for name, rec in (raw.get("channels") or {}).items():
            rec = dict(rec or {})
            codes = arrays.get(f"{tag}_raw_{name}")
            if codes is not None:
                rec["codes"] = np.asarray(codes, dtype=np.int8)
            chans[name] = rec
        if not chans:
            return None
        return {"channels": chans,
                "scaling": raw.get("scaling"),
                "aliases": raw.get("aliases")}
    except Exception:
        return None


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
        pulses_per_burst=int(p.get("pulses_per_burst", 1)),
        burst_period_us=float(p.get("burst_period_us", 0.0)),
        interpulse_discharge_us=float(p.get("interpulse_discharge_us", 0.0)),
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
        # Legacy archives have no "extras" key -> {} (never None: callers do
        # ``test.extras.get(...)`` without guarding).
        extras=dict(meta["test"].get("extras") or {}),
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
            label=run_meta.get("label", ""),
        )
        for cap_idx, cap_meta in enumerate(run_meta.get("captures", [])):
            tag = f"r{run_idx}_c{cap_idx}"

            # Per-capture pattern: stored captures may have rescaled amplitudes
            cap_pat_d = cap_meta.get("pattern", p)
            cap_pat = PulsePattern(
                phases=[Phase(**ph) for ph in cap_pat_d["phases"]],
                rate_hz=cap_pat_d.get("rate_hz", pattern.rate_hz),
                repetitions=cap_pat_d.get("repetitions", 0),
                pulses_per_burst=int(cap_pat_d.get(
                    "pulses_per_burst", pattern.pulses_per_burst)),
                burst_period_us=float(cap_pat_d.get(
                    "burst_period_us", pattern.burst_period_us)),
                interpulse_discharge_us=float(cap_pat_d.get(
                    "interpulse_discharge_us", pattern.interpulse_discharge_us)),
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
                access_resistance_drift_p=metrics_d.get(
                    "access_resistance_drift_p", float("nan")),
                access_resistance_drift_flag=bool(
                    metrics_d.get("access_resistance_drift_flag", False)),
                shaped_access_v_per_phase=list(
                    metrics_d.get("shaped_access_v_per_phase", [])),
                shaped_access_r_kohm_per_phase=list(
                    metrics_d.get("shaped_access_r_kohm_per_phase", [])),
                return_shaped_access_v_per_phase=list(
                    metrics_d.get("return_shaped_access_v_per_phase", [])),
                return_shaped_access_r_kohm_per_phase=list(
                    metrics_d.get("return_shaped_access_r_kohm_per_phase", [])),
                polarization_per_phase_v=list(metrics_d.get("polarization_per_phase_v", [])),
                return_polarization_per_phase_v=list(metrics_d.get("return_polarization_per_phase_v", [])),
                charge_per_phase_nc=metrics_d.get("charge_per_phase_nc", float("nan")),
                charge_injection_mc_per_cm2=metrics_d.get("charge_injection_mc_per_cm2", float("nan")),
                # Per-capture pulse count + cumulative delivered charge —
                # added later; legacy archives lack the keys so default NaN
                # (the table then omits the cumulative row / falls back to
                # the capture index for the pulse count).
                n_pulses=metrics_d.get("n_pulses", float("nan")),
                cumulative_charge_nc=metrics_d.get("cumulative_charge_nc", float("nan")),
                cumulative_n_pulses=metrics_d.get("cumulative_n_pulses", float("nan")),
                # Precise-sampling time columns (PS/LP) — scheduled
                # (fixed cadence grid) + actual elapsed.  Legacy archives
                # lack the keys so default NaN.
                scheduled_time_s=metrics_d.get("scheduled_time_s", float("nan")),
                elapsed_time_s=metrics_d.get("elapsed_time_s", float("nan")),
                # Pulse effective capacitance + response class (shown only
                # for capacitive/open/broken responses) — legacy archives
                # lack the keys so default NaN / "normal".
                effective_capacitance_nf=metrics_d.get("effective_capacitance_nf", float("nan")),
                rc_fit_resistance_kohm=metrics_d.get("rc_fit_resistance_kohm", float("nan")),
                rc_fit_tau_us=metrics_d.get("rc_fit_tau_us", float("nan")),
                # Per-phase bad-response metrics (operator: "compute the
                # same metrics for other phases") — legacy archives lack
                # the keys so default empty.
                effective_capacitance_per_phase_nf=list(
                    metrics_d.get("effective_capacitance_per_phase_nf", [])),
                rc_fit_resistance_per_phase_kohm=list(
                    metrics_d.get("rc_fit_resistance_per_phase_kohm", [])),
                rc_fit_tau_per_phase_us=list(
                    metrics_d.get("rc_fit_tau_per_phase_us", [])),
                response_class=metrics_d.get("response_class", "normal"),
                driving_capacitance_mf_per_cm2=metrics_d.get("driving_capacitance_mf_per_cm2", float("nan")),
                # Harris 2019 chronopotentiometry decomposition — legacy npz
                # lack these keys (NaN default keeps the loader robust).
                c_dl_mf_per_cm2=metrics_d.get("c_dl_mf_per_cm2", float("nan")),
                faradaic_onset_us=metrics_d.get("faradaic_onset_us", float("nan")),
                faradaic_onset_v=metrics_d.get("faradaic_onset_v", float("nan")),
                capacitive_charge_nc=metrics_d.get("capacitive_charge_nc", float("nan")),
                faradaic_charge_nc=metrics_d.get("faradaic_charge_nc", float("nan")),
                faradaic_fraction=metrics_d.get("faradaic_fraction", float("nan")),
                # Driving impedance (Z_d = V_d/I_stim) + driving energy
                # (∫V·I over the pulse) — legacy archives lack the keys.
                driving_impedance_kohm=metrics_d.get("driving_impedance_kohm", float("nan")),
                driving_energy_uj=metrics_d.get("driving_energy_uj", float("nan")),
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
                # Ghazavi & Cogan 2018 sinusoidal (KHFAC) polarization —
                # legacy archives lack the keys so default "pulsed" / NaN.
                polarization_method=metrics_d.get(
                    "polarization_method", "pulsed"),
                # Operator-configurable E_pol time delay (µs) the metrics were
                # computed with — legacy npz default to the canonical 12 µs.
                depolarization_us=metrics_d.get(
                    "depolarization_us", DEPOLARIZATION_TIME_US),
                ghazavi_e_mc_v=metrics_d.get("ghazavi_e_mc_v", float("nan")),
                ghazavi_e_ma_v=metrics_d.get("ghazavi_e_ma_v", float("nan")),
                ghazavi_e_io_v=metrics_d.get("ghazavi_e_io_v", float("nan")),
                ghazavi_e_off_v=metrics_d.get("ghazavi_e_off_v", float("nan")),
                ghazavi_r_access_kohm=metrics_d.get(
                    "ghazavi_r_access_kohm", float("nan")),
                ghazavi_freq_khz=metrics_d.get("ghazavi_freq_khz", float("nan")),
                ghazavi_v_access_v=metrics_d.get(
                    "ghazavi_v_access_v", float("nan")),
                ghazavi_return_e_mc_v=metrics_d.get(
                    "ghazavi_return_e_mc_v", float("nan")),
                ghazavi_return_e_ma_v=metrics_d.get(
                    "ghazavi_return_e_ma_v", float("nan")),
                ghazavi_return_e_io_v=metrics_d.get(
                    "ghazavi_return_e_io_v", float("nan")),
                ghazavi_return_e_off_v=metrics_d.get(
                    "ghazavi_return_e_off_v", float("nan")),
                ghazavi_return_r_access_kohm=metrics_d.get(
                    "ghazavi_return_r_access_kohm", float("nan")),
                ghazavi_return_v_access_v=metrics_d.get(
                    "ghazavi_return_v_access_v", float("nan")),
                phase_angle_vmon_deg=metrics_d.get(
                    "phase_angle_vmon_deg", float("nan")),
                phase_angle_eret_deg=metrics_d.get(
                    "phase_angle_eret_deg", float("nan")),
                phase_angle_eact_deg=metrics_d.get(
                    "phase_angle_eact_deg", float("nan")),
                eis_freq_hz=metrics_d.get("eis_freq_hz", float("nan")),
                z_mag_ohm=metrics_d.get("z_mag_ohm", float("nan")),
                z_phase_deg=metrics_d.get("z_phase_deg", float("nan")),
                z_real_ohm=metrics_d.get("z_real_ohm", float("nan")),
                z_imag_ohm=metrics_d.get("z_imag_ohm", float("nan")),
            )

            status_d = cap_meta.get("status", {})
            status = CaptureStatus(
                good=status_d.get("good", True),
                reached_potential_limit=status_d.get("reached_potential_limit", False),
                exceeded_potential_limit=status_d.get("exceeded_potential_limit", False),
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
                i_ideal_ua=arrays.get(f"{tag}_i_ideal_ua"),
                raw_channels=_raw_from_npz(cap_meta, arrays, tag),
                metrics=metrics,
                status=status,
            )
            run.captures.append(cap)
        session.runs.append(run)
    return session


# ---------------------------------------------------------------------------
# PicoScope CSV import (POLARIS "Open" — external scope captures)
# ---------------------------------------------------------------------------
@dataclass
class PicoScopeRecording:
    """A raw multi-channel waveform loaded from a PicoScope CSV export.

    PicoScope CSVs have a two-row header (``Time,Channel A,Channel B,…`` then
    a units row ``(us),(V),(mV),…``) followed by a blank line and the data.
    The units VARY per recording (a channel may be exported in V on one file
    and mV on another), so every channel is normalised to **volts** here and
    the time vector to **microseconds**.  This is a ROLE-FREE container — the
    channels keep their PicoScope names (``Channel A`` …); POLARIS shows them
    as raw traces and the user can optionally assign roles for metrics.
    """
    path: Path
    time_us: np.ndarray
    channels: Dict[str, np.ndarray]            # name -> volts
    source_units: Dict[str, str]               # name -> exported unit ("V"/"mV")


_PICO_TIME_TO_US = {
    "s": 1e6, "ms": 1e3, "us": 1.0, "µs": 1.0, "μs": 1.0, "ns": 1e-3,
}


def _pico_unit(tok: str) -> str:
    """Normalise a header unit token like ``(mV)`` / ``(us)`` → ``mv`` / ``us``."""
    return (tok or "").strip().strip("()").strip().lower()


#: Scope-export file extensions POLARIS can open (besides PULSAR ``.npz``).
PICO_EXTENSIONS = (".csv", ".tsv", ".xls", ".xlsx", ".xlsm")


def _read_delimited_rows(path: Path):
    """Read a CSV/TSV into rows-of-string-cells, auto-detecting the
    delimiter (tab / comma / semicolon) from the first lines so a
    mislabelled extension still parses."""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        lines = fh.read().splitlines()
    sample = "\n".join(lines[:10])
    if "\t" in sample:
        delim = "\t"
    elif sample.count(";") > sample.count(","):
        delim = ";"
    else:
        delim = ","
    return [ln.split(delim) for ln in lines]


# Sheet names written by ``gamry_export`` into a PULSAR session .xlsx.
# Their presence is the tell that an .xlsx is a PULSAR EXPORT (a derived
# artifact of a .npz), NOT an external scope capture — so the PicoScope
# loader must reject it (else it misparses the metadata sheet into garbage
# channels) and the folder index must skip it.
_PULSAR_XLSX_SHEETS = frozenset(
    {"instrumentation", "parameters", "setup", "values"})


def is_pulsar_session_xlsx(path: Path | str) -> bool:
    """True iff ``path`` is a PULSAR session EXPORT workbook (not an
    external scope capture).  Detected by the distinctive sheet names
    ``gamry_export`` writes.  Returns False for non-Excel files, real
    PicoScope workbooks, or anything that can't be inspected (fails
    OPEN so a genuine capture is never wrongly excluded)."""
    path = Path(path)
    if path.suffix.lower() not in (".xlsx", ".xlsm"):
        return False                       # legacy .xls / CSV: treat as data
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            names = {s.strip().lower() for s in wb.sheetnames}
        finally:
            wb.close()
    except Exception:
        return False
    # ≥2 of the PULSAR sheets ⇒ a PULSAR export (a real PicoScope sheet
    # named e.g. "Setup" alone shouldn't false-positive).
    return len(names & _PULSAR_XLSX_SHEETS) >= 2


def _read_excel_rows(path: Path):
    """Read the first worksheet of an Excel file into rows-of-cells."""
    suffix = path.suffix.lower()
    if suffix == ".xls":
        # openpyxl can't read the legacy .xls binary format; pandas+xlrd can.
        try:
            import pandas as pd
            df = pd.read_excel(path, header=None, dtype=object,
                               sheet_name=0)
            return df.where(df.notna(), None).values.tolist()
        except Exception as e:
            raise ValueError(
                f"{path.name}: reading legacy .xls needs pandas+xlrd "
                f"({type(e).__name__}: {e}); re-save it as .xlsx") from e
    try:
        import openpyxl
    except Exception as e:
        raise ValueError(
            f"{path.name}: reading .xlsx needs openpyxl "
            f"({type(e).__name__}: {e})") from e
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb[wb.sheetnames[0]]
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
    finally:
        wb.close()
    return rows


def _picoscope_from_rows(rows, path: Path) -> PicoScopeRecording:
    """Build a :class:`PicoScopeRecording` from rows-of-cells (the shared
    parser for every scope-export format).  Robust to: the optional units
    row, blank separators, mV/V (and ms/s/ns time) units, trailing empty
    rows, and a variable channel count.  ``ValueError`` if no ``Time,…``
    header is found."""
    def _cell(c):
        return "" if c is None else str(c).strip()

    hdr_i = None
    for i, r in enumerate(rows):
        if r and _cell(r[0]).lower().startswith("time") and len(r) >= 2:
            hdr_i = i
            break
    if hdr_i is None:
        raise ValueError(
            f"{path.name}: no 'Time, Channel …' header — not a scope export")
    names = [_cell(c) for c in rows[hdr_i]]
    units = ["" for _ in names]
    data_start = hdr_i + 1
    if data_start < len(rows):
        cand = [_cell(c) for c in rows[data_start]]
        if cand and any("(" in c for c in cand):
            units = cand + [""] * (len(names) - len(cand))
            data_start += 1
    data = []
    for r in rows[data_start:]:
        if not any(_cell(c) for c in r):           # blank separator row
            continue
        vals = []
        for c in r[:len(names)]:
            try:
                vals.append(float(_cell(c)))
            except (TypeError, ValueError):
                vals.append(float("nan"))
        vals += [float("nan")] * (len(names) - len(vals))
        data.append(vals)
    if not data:
        raise ValueError(f"{path.name}: header present but no data rows")
    arr = np.asarray(data, dtype=float)
    good = ~np.all(np.isnan(arr), axis=1)          # drop padded all-NaN rows
    if good.any():
        arr = arr[good]
    ncol = min(len(names), arr.shape[1])
    if ncol < 2:
        raise ValueError(f"{path.name}: need a time column + ≥1 channel")
    tscale = _PICO_TIME_TO_US.get(_pico_unit(units[0]) if units else "us", 1.0)
    time_us = np.asarray(arr[:, 0], dtype=float) * tscale
    # A real scope export has a numeric time column.  An all-NaN time
    # column means we latched onto a non-data sheet (e.g. a metadata table
    # whose first column isn't time) — reject rather than render garbage.
    if not np.any(np.isfinite(time_us)):
        raise ValueError(
            f"{path.name}: time column is empty / non-numeric — not a "
            f"scope export")
    channels: Dict[str, np.ndarray] = {}
    source_units: Dict[str, str] = {}
    for c in range(1, ncol):
        nm = names[c] or f"Channel {c}"
        u = _pico_unit(units[c]) if c < len(units) else ""
        col = np.asarray(arr[:, c], dtype=float)
        if u == "mv":
            channels[nm] = col * 1e-3
            source_units[nm] = "mV"
        else:
            channels[nm] = col
            source_units[nm] = "V" if u in ("v", "") else u
    return PicoScopeRecording(path=path, time_us=time_us,
                              channels=channels, source_units=source_units)


def load_picoscope(path: Path | str) -> PicoScopeRecording:
    """Load a PicoScope (or compatible) scope export into a
    :class:`PicoScopeRecording`, dispatching by extension:

    * ``.csv`` / ``.tsv`` (and any other text) → delimited parse
      (delimiter auto-detected: tab / comma / semicolon);
    * ``.xls`` / ``.xlsx`` / ``.xlsm`` → first worksheet.

    All share the two-row-header (names + units) + data layout; every
    channel is normalised to volts and time to microseconds.
    """
    path = Path(path)
    if is_pulsar_session_xlsx(path):
        raise ValueError(
            f"{path.name} is a PULSAR session export (.xlsx), not a scope "
            f"capture. Open the matching .npz session instead.")
    if path.suffix.lower() in (".xls", ".xlsx", ".xlsm"):
        rows = _read_excel_rows(path)
    else:
        rows = _read_delimited_rows(path)
    return _picoscope_from_rows(rows, path)


def load_picoscope_csv(path: Path | str) -> PicoScopeRecording:
    """Back-compat alias for :func:`load_picoscope` (CSV was the first
    supported format; it now also reads TSV / XLS / XLSX)."""
    return load_picoscope(path)


#: Default current-monitor scaling for PicoScope captures (PlexStim default
#: I_mon = 2.5 mV/µA — see config.IMON_SCALING_DEFAULT).  Used to convert a
#: current-monitor VOLTAGE channel to µA; the operator can override per file.
PICO_CURRENT_SCALE_MV_PER_UA = 2.5

# Role tokens a PicoScope channel can be mapped to (POLARIS role selector).
PICO_ROLES = ("none", "v_mon", "i_mon", "e_act", "e_ret")


def _infer_pattern_from_current(time_us, signal, *, rate_hz: float = 50.0,
                                thresh_frac: float = 0.25):
    """Infer a :class:`PulsePattern` + pulse onset from a square-ish drive
    signal (the current monitor, in µA — or V_mon as a fallback).

    Contiguous segments above ``thresh_frac`` of the peak excursion are
    phases (amplitude = segment median, width = segment duration); the zero
    gaps between them are interphase delays.  A trailing discharge delay is
    mirrored from the interphase delay when one exists, so a symmetric
    biphasic-with-interphase gets its trailing access point.  Returns
    ``(pattern, onset_us)``.
    """
    from .waveforms import Phase, PulsePattern
    t = np.asarray(time_us, dtype=float)
    c = np.asarray(signal, dtype=float)
    _flat = PulsePattern(phases=[Phase(amplitude_ua=0.0, width_us=1.0)],
                         rate_hz=rate_hz)
    if t.size < 4 or c.size != t.size:
        return _flat, float(t[0]) if t.size else 0.0
    n0 = max(4, t.size // 20)
    c = c - float(np.median(c[:n0]))               # pre-pulse baseline
    pk = float(np.percentile(np.abs(c), 99))
    if not np.isfinite(pk) or pk <= 0:
        return _flat, float(t[0])
    idx = np.where(np.abs(c) > thresh_frac * pk)[0]
    if idx.size == 0:
        return _flat, float(t[0])
    groups = np.split(idx, np.where(np.diff(idx) > 5)[0] + 1)
    groups = [g for g in groups if g.size >= 3]
    if not groups:
        return _flat, float(t[0])
    onset = float(t[int(groups[0][0])])
    phases = []
    for gi, g in enumerate(groups):
        a, b = int(g[0]), int(g[-1])
        amp = float(np.median(c[a:b + 1]))
        width = max(float(t[b] - t[a]), 0.1)
        if gi < len(groups) - 1:
            delay = max(float(t[int(groups[gi + 1][0])] - t[b]), 0.0)
        else:
            delay = 0.0
        phases.append(Phase(amplitude_ua=amp, width_us=width,
                            delay_after_us=delay))
    iphs = [p.delay_after_us for p in phases[:-1] if p.delay_after_us > 0]
    if iphs and phases[-1].delay_after_us == 0:    # mirror trailing discharge
        phases[-1] = _dc_replace(phases[-1], delay_after_us=float(np.median(iphs)))
    return PulsePattern(phases=phases, rate_hz=rate_hz), onset


def picoscope_to_session(rec: "PicoScopeRecording", role_map: Dict[str, str],
                         *, current_scale_mv_per_ua: float = PICO_CURRENT_SCALE_MV_PER_UA,
                         surface_area_um2: float = 5000.0,
                         rate_hz: float = 50.0) -> "Session":
    """Build a one-capture :class:`Session` from a PicoScope recording and a
    channel→role map (``{channel_name: 'v_mon'|'i_mon'|'e_act'|'e_ret'}``),
    with the pulse pattern inferred from the current channel (or V_mon) and
    metrics computed — so POLARIS can show V_a / V_d / E_pol on a PicoScope
    capture once the operator assigns roles.

    The current-monitor channel is a VOLTAGE; ``current_scale_mv_per_ua``
    converts it to µA (default = PlexStim 2.5 mV/µA).  Charge / R_a depend
    on this scale; the voltage metrics (V_a / V_d / E_pol) depend only on
    the pattern TIMING (from the current edges) and the voltage channels, so
    they're correct even at the default scale.
    """
    from .session import Session, ChannelRun, TestParameters, Capture
    from .electrode import Configuration, ElectrodeArray
    from .metrics import compute_metrics
    t = np.asarray(rec.time_us, dtype=float)
    inv: Dict[str, str] = {}
    for ch, role in (role_map or {}).items():
        if role and role != "none" and ch in rec.channels:
            inv[role] = ch

    def _arr(role):
        ch = inv.get(role)
        return (np.asarray(rec.channels[ch], dtype=float)
                if ch is not None else None)

    v_mon = _arr("v_mon")
    e_act = _arr("e_act")
    e_ret = _arr("e_ret")
    cur_v = _arr("i_mon")
    scale = max(float(current_scale_mv_per_ua), 1e-9)
    i_ua = cur_v * (1000.0 / scale) if cur_v is not None else None

    drive = i_ua if i_ua is not None else v_mon
    if drive is not None:
        pattern, _onset = _infer_pattern_from_current(t, drive, rate_hz=rate_hz)
    else:
        from .waveforms import Phase, PulsePattern
        pattern = PulsePattern(phases=[Phase(amplitude_ua=0.0, width_us=1.0)],
                               rate_hz=rate_hz)

    cap = Capture(index=0, pattern=pattern)
    cap.time_us = t
    cap.v_mon_v = v_mon if v_mon is not None else np.zeros_like(t)
    cap.i_mon_ua = i_ua if i_ua is not None else np.zeros_like(t)
    cap.e_act_v = e_act
    cap.e_ret_v = e_ret
    try:
        compute_metrics(cap, surface_area_um2=surface_area_um2)
    except Exception:
        pass

    config = Configuration.monopolar(1)
    array = ElectrodeArray.utah_4x4()
    test = TestParameters(experiment="PicoScope", pattern=pattern,
                          configuration=config, array=array)
    session = Session(notebook="PicoScope",
                      subject=getattr(rec.path, "stem", "PicoScope"),
                      test=test)
    run = ChannelRun(configuration=config, surface_area_um2=surface_area_um2)
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
