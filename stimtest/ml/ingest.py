"""Ingest legacy MATLAB ``File`` structs into the ML training CSV.

The MATLAB ``PlexStimTek`` workflow saves one ``.mat`` per characterization
session. Each file is a struct with sub-fields ``Parameters``, ``Test``, and
``Data[ch]`` — one entry per channel ramped during the sweep. ``Data[ch]``
records the maximum amplitude that the channel reached before the safety
limit was hit, plus the resulting ``ChargeInjection`` (mC/cm²).

This module walks one or more directories, opens every ``.mat`` it finds,
and emits one observation per channel that successfully reached a limit. The
result is appended to ``data/qinj_dataset.csv`` (or any path you choose) so
it can feed :class:`stimtest.ml.QinjPredictor`.

Heterogeneity handling
----------------------
The MATLAB code evolved over years; field names are not stable:

* Older files use ``BeforeDischarge``; newer ones use ``DischargeDelay``.
* Older files lack a ``Parameters`` struct entirely (we skip those — there
  isn't enough information to build a feature row).
* ``WorkingElectrode`` is sometimes a string (``'AIROF'``) and sometimes a
  nested struct with ``.Type``/``.LowerPotential``/``.UpperPotential``.
* ``Configuration`` is sometimes a bare string (``'MP'``) and sometimes a
  struct. Same for ``CounterElectrode``.

The helpers below dig through both shapes and return a uniform
:class:`QinjFeatures` row.
"""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import numpy as np
from scipy.io import loadmat

from .qinj_model import QinjFeatures, record_observation, DEFAULT_DATASET_PATH


# ---------------------------------------------------------------------------
# Folder-name heuristics for fields that aren't in the .mat file
# ---------------------------------------------------------------------------
_RETURN_MATERIAL_TOKENS = ("Ti", "Pt", "PtIr", "SS", "W", "Ir")
_PATTERN_TOKENS = {"BPH": "biphasic", "TPH": "triphasic", "MPH": "monophasic"}
_CONFIG_TOKENS = ("MP", "BP", "TP", "CG", "PBP", "PTP")


def _guess_from_path(path: Path) -> dict:
    """Pull return-material, polarity, config, and pattern hints from path."""
    stem = path.stem
    parent = path.parent.name
    out = {"return_material_hint": None, "polarity_hint": None,
           "config_hint": None, "pattern_hint": None}

    text = f"{parent}_{stem}"
    # Return material: longer tokens first to avoid Pt vs PtIr collision
    for mat in sorted(_RETURN_MATERIAL_TOKENS, key=len, reverse=True):
        if re.search(rf"(?<![A-Za-z]){mat}(?![A-Za-z])", text):
            out["return_material_hint"] = mat
            break
    # Polarity
    if re.search(r"(?<![A-Za-z])neg(?![A-Za-z])", text, re.IGNORECASE):
        out["polarity_hint"] = -1
    elif re.search(r"(?<![A-Za-z])pos(?![A-Za-z])", text, re.IGNORECASE):
        out["polarity_hint"] = +1
    # Config
    for cfg in _CONFIG_TOKENS:
        if re.search(rf"(?<![A-Za-z]){cfg}(?![A-Za-z])", text):
            out["config_hint"] = cfg
            break
    # Pattern
    for tok, ptype in _PATTERN_TOKENS.items():
        if tok in text:
            out["pattern_hint"] = ptype
            break
    return out


# ---------------------------------------------------------------------------
# Field-extraction helpers (cope with old vs new mat_struct shapes)
# ---------------------------------------------------------------------------
def _get(obj, name, default=None):
    return getattr(obj, name, default) if obj is not None else default


def _scalar(val, default=float("nan")):
    if val is None:
        return default
    if isinstance(val, np.ndarray):
        if val.size == 0:
            return default
        return val.item() if val.size == 1 else val.flat[0]
    return val


def _extract_coating(P) -> str:
    """Return the active electrode's coating name (e.g. 'AIROF')."""
    we = _get(P, "WorkingElectrode")
    if we is None:
        return ""
    if isinstance(we, str):
        return we
    if hasattr(we, "_fieldnames"):
        return str(_get(we, "Type", "")) or ""
    return str(we)


def _extract_return_material(P, hint: Optional[str]) -> str:
    """Return the counter/return electrode material."""
    ce = _get(P, "CounterElectrode")
    if ce is not None:
        if isinstance(ce, str):
            return ce
        if hasattr(ce, "_fieldnames"):
            mat = str(_get(ce, "Type", "")) or ""
            if mat:
                return mat
    return hint or ""


def _extract_config_id(P, hint: Optional[str]) -> str:
    cfg = _get(P, "Configuration")
    if cfg is None:
        return hint or "MP"
    if isinstance(cfg, str):
        return cfg
    if hasattr(cfg, "_fieldnames"):
        cid = str(_get(cfg, "ID", "")) or ""
        if cid:
            return cid
    return hint or "MP"


def _extract_environment(P) -> str:
    env = _get(P, "Environment")
    if env is None:
        return "PBS"
    s = str(env).strip().lower() if not isinstance(env, np.ndarray) else \
        str(env.item() if env.size else "").strip().lower()
    if not s:
        return "PBS"
    if "saline" in s or "pbs" in s or "electrolyte" in s:
        return "PBS"
    if "cortex" in s or "in vivo" in s or "vivo" in s:
        return "cortex"
    return s[:32]


def _extract_pattern_type(P, hint: Optional[str]) -> str:
    """Detect biphasic vs triphasic vs monophasic from the parameter struct."""
    if hint:
        return hint
    a1 = _scalar(_get(P, "Amplitude1"), 0)
    a2 = _scalar(_get(P, "Amplitude2"), 0)
    a3 = _scalar(_get(P, "Amplitude3"), 0)
    if a3 and a3 != 0:
        return "triphasic"
    if a2 and a2 != 0:
        return "biphasic"
    return "monophasic"


def _extract_polarity(P, hint: Optional[int]) -> int:
    pol = _scalar(_get(P, "Polarity"), 0)
    try:
        pol_i = int(round(float(pol)))
    except (TypeError, ValueError):
        pol_i = 0
    if pol_i in (-1, 1):
        return pol_i
    if hint in (-1, 1):
        return hint
    return -1


# ---------------------------------------------------------------------------
# One observation per channel entry
# ---------------------------------------------------------------------------
_FAILURE_STATUSES = (
    "voltage compliance reached",
    "max current reached",
    "max amplitude reached",
    "aborted",
    "error",
)


def _is_valid_channel(ch_obj, qinj: float) -> bool:
    """Filter out channels that didn't finish a successful sweep."""
    if not (qinj is not None and np.isfinite(qinj) and qinj > 0):
        return False
    status = _scalar(_get(ch_obj, "Status"), "")
    if isinstance(status, np.ndarray):
        status = str(status.item() if status.size else "")
    s = str(status).lower()
    if any(bad in s for bad in _FAILURE_STATUSES):
        return False
    return True


def _channel_features(P, ch_obj, hints: dict) -> Optional[Tuple[QinjFeatures, float]]:
    """Build (features, q_inj) from one Data[i] entry, or None if invalid."""
    qinj = _scalar(_get(ch_obj, "ChargeInjection"))
    if not _is_valid_channel(ch_obj, qinj):
        return None

    # Per-channel surface area can override the parameter-level default
    sa_default = _scalar(_get(P, "SurfaceArea"), 5000.0)
    sa_ch = _scalar(_get(ch_obj, "SurfaceArea"), sa_default)
    sa_um2 = float(sa_ch) if np.isfinite(sa_ch) and sa_ch > 0 else float(sa_default)

    # Pull pattern parameters
    pw1 = float(_scalar(_get(P, "PhaseWidth1"), 200))
    pw2 = float(_scalar(_get(P, "PhaseWidth2"), pw1))
    iph = float(_scalar(_get(P, "InterphaseDelay"), 20))
    dd = float(_scalar(_get(P, "DischargeDelay"),
                       _scalar(_get(P, "BeforeDischarge"), 20)))
    rate = float(_scalar(_get(P, "StimulationRate"), 50))

    config_id = _extract_config_id(P, hints["config_hint"])
    n_returns = {"MP": 0, "CG": 0, "BP": 1, "PBP": 1, "TP": 2, "PTP": 2}.get(config_id, 0)

    feats = QinjFeatures(
        coating=_extract_coating(P) or "AIROF",
        surface_area_um2=sa_um2,
        electrolyte=_extract_environment(P),
        phase_width_us=max(pw1, pw2),  # widest phase = excitation
        interphase_delay_us=iph,
        discharge_delay_us=dd,
        polarity=_extract_polarity(P, hints["polarity_hint"]),
        pattern_type=_extract_pattern_type(P, hints["pattern_hint"]),
        config_id=config_id,
        n_returns=n_returns,
        rate_hz=rate,
    )
    return feats, float(qinj)


# ---------------------------------------------------------------------------
# Top-level ingest
# ---------------------------------------------------------------------------
def ingest_mat_file(path: Path, *, csv_path: Path = DEFAULT_DATASET_PATH,
                    coating_override: Optional[str] = None) -> int:
    """Read one .mat file and append its valid channels to the dataset CSV.

    Returns the number of rows appended. ``coating_override`` lets the caller
    force a coating value (useful when the legacy file lacks ``Parameters``
    but the path makes it clear, e.g. ``…/SIROF/Data/…``).
    """
    raw = loadmat(str(path), struct_as_record=False, squeeze_me=True)
    file_obj = next((v for k, v in raw.items()
                     if not k.startswith("__")), None)
    if file_obj is None or not hasattr(file_obj, "_fieldnames"):
        return 0
    P = _get(file_obj, "Parameters")
    if P is None:
        return 0  # Older files without Parameters can't be ingested
    data = _get(file_obj, "Data")
    if data is None:
        return 0
    if not isinstance(data, np.ndarray):
        data = np.array([data])

    hints = _guess_from_path(path)
    notebook = str(_get(file_obj, "Notebook", "")).strip()
    subject = str(_get(file_obj, "Subject", "")).strip()
    note = f"ingested:{notebook}/{subject}".strip(":/")

    n = 0
    for ch in data.flat:
        if not hasattr(ch, "_fieldnames"):
            continue
        result = _channel_features(P, ch, hints)
        if result is None:
            continue
        feats, qinj = result
        if coating_override:
            feats = replace(feats, coating=coating_override)
        record_observation(feats, qinj, path=csv_path, notes=note)
        n += 1
    return n


def ingest_directory(root: Path | str, *,
                     csv_path: Path = DEFAULT_DATASET_PATH,
                     coating_override: Optional[str] = None,
                     name_filter: Optional[str] = "VT",
                     parallel: bool | int = False) -> dict:
    """Walk ``root`` recursively, ingest every ``.mat`` file with VT data.

    Skips files whose name doesn't contain ``name_filter`` (e.g. ``"VT"``
    excludes the OCP/EIS/CV electrochemistry files in the same folders).

    Parameters
    ----------
    parallel : bool | int, default False
        ``False`` = sequential (one process, one file at a time).
        ``True`` = use ``min(os.cpu_count(), 8)`` worker processes.
        Integer = use exactly that many workers.

        Workers parse their assigned ``.mat`` files concurrently and ship
        the extracted ``(features, q_inj, notes)`` tuples back to the
        parent. The parent then writes them all to the CSV in one
        sequential pass — concurrent writes to the same file are unsafe
        and would interleave rows. Typical 4–8× speedup on the 290-file
        AIROF/SIROF corpus.
    """
    root = Path(root)
    paths = [p for p in root.rglob("*.mat")
             if not name_filter or name_filter in p.name]
    if not paths:
        return {"files": 0, "rows_added": 0, "skipped": 0, "errors": []}

    results: List[List[Tuple[QinjFeatures, float, str]]]
    errors: List[Tuple[Path, str]] = []

    if parallel and len(paths) > 1:
        results, par_errors = _parallel_extract(paths, coating_override, parallel)
        errors.extend(par_errors)
    else:
        results = []
        for p in paths:
            try:
                results.append(_extract_rows(p, coating_override))
            except Exception as e:    # noqa: BLE001
                results.append([])
                errors.append((p, str(e)))

    # Sequential write step — append every row in one pass. Concurrent
    # writes to the same CSV would interleave bytes and corrupt rows.
    rows = 0
    skipped = 0
    for rowlist in results:
        if not rowlist:
            skipped += 1
            continue
        for feats, qinj, note in rowlist:
            record_observation(feats, qinj, path=csv_path, notes=note)
            rows += 1
    return {"files": len(paths), "rows_added": rows, "skipped": skipped,
            "errors": errors}


def _parallel_extract(paths, coating_override, parallel):
    """Fan ``.mat`` parsing across worker processes; collect rows."""
    import os
    from concurrent.futures import ProcessPoolExecutor, as_completed

    n_workers = parallel if isinstance(parallel, int) and parallel > 1 \
        else min(os.cpu_count() or 4, 8)

    results: List[List[Tuple[QinjFeatures, float, str]]] = []
    errors: List[Tuple[Path, str]] = []
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        # Map preserves submission order; each task gets the path + override
        future_to_path = {
            ex.submit(_extract_rows_worker, str(p), coating_override): p
            for p in paths
        }
        for fut in as_completed(future_to_path):
            p = future_to_path[fut]
            try:
                results.append(fut.result())
            except Exception as e:    # noqa: BLE001
                results.append([])
                errors.append((p, str(e)))
    return results, errors


def _extract_rows(path: Path, coating_override: Optional[str]
                  ) -> List[Tuple[QinjFeatures, float, str]]:
    """Read one ``.mat`` and return its ``(features, q_inj, notes)`` rows.

    Mirrors :func:`ingest_mat_file` but doesn't write to the CSV; instead
    returns the rows so the caller can write them in a single, sequential
    pass after parallel extraction. Keeps the I/O serialisation safe
    while letting the parsing fan out across cores.
    """
    raw = loadmat(str(path), struct_as_record=False, squeeze_me=True)
    file_obj = next((v for k, v in raw.items()
                     if not k.startswith("__")), None)
    if file_obj is None or not hasattr(file_obj, "_fieldnames"):
        return []
    P = _get(file_obj, "Parameters")
    if P is None:
        return []
    data = _get(file_obj, "Data")
    if data is None:
        return []
    if not isinstance(data, np.ndarray):
        data = np.array([data])
    hints = _guess_from_path(path)
    notebook = str(_get(file_obj, "Notebook", "")).strip()
    subject = str(_get(file_obj, "Subject", "")).strip()
    note = f"ingested:{notebook}/{subject}".strip(":/")
    rows: List[Tuple[QinjFeatures, float, str]] = []
    for ch in data.flat:
        if not hasattr(ch, "_fieldnames"):
            continue
        result = _channel_features(P, ch, hints)
        if result is None:
            continue
        feats, qinj = result
        if coating_override:
            feats = replace(feats, coating=coating_override)
        rows.append((feats, qinj, note))
    return rows


def _extract_rows_worker(path_str: str, coating_override: Optional[str]):
    """Process-pool entry point. Pickles only ``str`` and a small string,
    not the full ``Path`` object (works on every Python version)."""
    return _extract_rows(Path(path_str), coating_override)
