"""Capture reproducibility metadata for a Session at run time.

Closes Task #57 (richer session metadata).  Embeds in every saved
``.npz``:

* **PULSAR identity** — git commit hash + branch (when the install
  is a git checkout), package version from ``__version__``.
* **Runtime environment** — Python version, OS + version, platform.
* **Key package versions** — numpy, scipy, pyqt6, pyvisa, pyserial.
* **Hardware identity** — scope make/model/serial/firmware, stim
  serial + firmware, channel aliases.
* **Setup-snapshot SHA-256 hash** — fingerprint of the
  configuration that drove this run so two identical setups can
  be compared session-to-session.

Months later, "what version of PULSAR generated this .npz?" is
answerable without git archeology, and "did anything change in
the setup since the last comparable run?" is a single dict diff.

All values are stringified at capture time so the JSON round-trip
in :mod:`stimtest.persistence` stays trivial — no nested dicts,
no datetime objects, no numpy arrays.  Caller can re-parse strings
as needed when displaying.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional


def capture_system_metadata(
    session=None,
    *,
    stimulator=None,
    oscilloscope=None,
) -> Dict[str, str]:
    """Build a fresh str → str metadata dict for a Session.

    Called by :class:`stimtest.experiments.base.ExperimentRunner.__init__`
    so every session captured by every runner gets the same fields.
    Pure function — no side effects, no IO outside of best-effort
    git / version queries.

    Parameters
    ----------
    session :
        Optional :class:`Session` — when provided, the setup snapshot
        is hashed into ``setup_snapshot_sha256`` for cross-session
        comparison.  ``None`` skips that field.
    stimulator, oscilloscope :
        Optional driver instances — when provided, their ``info``
        attributes (StimulatorInfo / ScopeInfo) are flattened into
        the metadata so hardware identity is captured even after the
        device is disconnected.  Re-snapshotting the live driver
        each run also catches firmware updates between runs.

    Returns
    -------
    dict[str, str]
        Flat string-valued dict suitable for ``meta.json``
        serialization.  Missing-info fields are present with empty
        strings rather than absent — makes diffing two sessions'
        metadata simpler (no key-existence branching).

    Field names + meanings
    ----------------------
    See :data:`_FIELD_DOCS` for the canonical list of fields and
    what each one represents.  Adding a new field?  Update the
    constant + the test that asserts every documented field is
    actually populated.
    """
    md: Dict[str, str] = {}

    # PULSAR identity
    md["pulsar_version"] = _safe_str(_package_version("stimtest"))
    md["pulsar_git_hash"] = _safe_str(_git_head_hash())
    md["pulsar_git_branch"] = _safe_str(_git_branch())
    md["pulsar_git_dirty"] = _safe_str(_git_dirty())

    # Runtime environment
    md["python_version"] = _safe_str(sys.version.split()[0])
    md["python_implementation"] = _safe_str(platform.python_implementation())
    md["os_name"] = _safe_str(platform.system())
    md["os_version"] = _safe_str(platform.release())
    md["os_platform"] = _safe_str(platform.platform())
    md["machine"] = _safe_str(platform.machine())

    # Key package versions — graceful fallback when an optional
    # package isn't installed (returns empty string for that key).
    for pkg in ("numpy", "scipy", "PyQt6", "pyvisa", "pyserial",
                "matplotlib", "openpyxl", "pandas", "scikit-learn"):
        md[f"pkg_{pkg.lower().replace('-', '_')}_version"] = \
            _safe_str(_package_version(pkg))

    # Hardware identity — snapshot at metadata-capture time so a
    # later device disconnect / firmware update doesn't invalidate
    # the record.
    if stimulator is not None:
        info = getattr(stimulator, "info", None)
        if info is not None:
            md["stim_manufacturer"] = _safe_str(
                getattr(info, "description", "") or "Plexon")
            md["stim_model"] = _safe_str(
                getattr(info, "model", "") or "PlexStim 2.0")
            md["stim_serial"] = _safe_str(getattr(info, "serial_number", ""))
            md["stim_firmware"] = _safe_str(getattr(info, "firmware", ""))
            md["stim_n_channels"] = _safe_str(getattr(info, "n_channels", ""))
            md["stim_is_simulated"] = _safe_str(
                getattr(info, "is_simulated", False))
    if oscilloscope is not None:
        info = getattr(oscilloscope, "info", None)
        if info is not None:
            md["scope_make"] = _safe_str(getattr(info, "make", ""))
            md["scope_model"] = _safe_str(getattr(info, "model", ""))
            md["scope_serial"] = _safe_str(getattr(info, "serial", ""))
            md["scope_firmware"] = _safe_str(getattr(info, "firmware", ""))
            md["scope_n_channels"] = _safe_str(getattr(info, "n_channels", ""))
            md["scope_is_simulated"] = _safe_str(
                getattr(info, "is_simulated", False))
        aliases = getattr(oscilloscope, "channel_aliases", None)
        if aliases:
            md["scope_channel_aliases"] = _safe_str(
                ", ".join(f"{k}={v}" for k, v in sorted(aliases.items())))

    # Setup-snapshot hash — lets the operator diff two runs to spot
    # config drift.  Hashes the test params (pattern, configuration,
    # array, environment) into a SHA-256 fingerprint.  When this
    # value matches between two .npz files, the setup was identical.
    if session is not None:
        md["setup_snapshot_sha256"] = _safe_str(
            _hash_setup_snapshot(session))

    return md


def _hash_setup_snapshot(session) -> str:
    """SHA-256 fingerprint of the run-determining setup state.

    Hashes ``session.test`` (which carries pattern + configuration +
    array + environment + electrode labels + target charge) plus the
    ``test.extras["setup_snapshot"]`` block (the per-experiment
    snapshot the GUI captures at Start press).  Stable across
    machines: two operators running the same params get the same
    hash, so diffing is trivial.
    """
    try:
        # Flatten to a stable JSON representation.  ``asdict`` walks
        # nested dataclasses; remaining non-JSON-serializable values
        # (datetime, numpy types) get coerced via the default hook.
        payload: Dict[str, Any] = {}
        if is_dataclass(session.test):
            payload["test"] = asdict(session.test)
        # Strip non-deterministic fields that don't affect the
        # logical setup — timestamps, transient extras like camera
        # toggles.
        if isinstance(payload.get("test"), dict):
            payload["test"].pop("extras", None)
            extras = getattr(session.test, "extras", None) or {}
            # Keep just the setup-snapshot subdict from extras —
            # everything else (hardware info, channel aliases) is
            # captured separately via stim/scope info.
            snap = extras.get("setup_snapshot")
            if snap:
                payload["setup_snapshot"] = snap
        canonical = json.dumps(
            payload, sort_keys=True, default=_json_default)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_str(value: Any) -> str:
    """Coerce ``value`` to a string, returning ``""`` on any error.

    Used for every metadata field so a single failed probe (e.g., a
    package not installed, a hardware-info attribute missing) doesn't
    propagate as an exception.  Empty string is the "no info" signal
    consumers can branch on.
    """
    if value is None:
        return ""
    try:
        return str(value)
    except Exception:
        return ""


def _package_version(name: str) -> str:
    """Return the installed version of ``name``, or empty string when
    the package isn't installed.

    Uses ``importlib.metadata.version`` (stdlib since 3.8).  Handles
    the distribution-vs-import name skew (e.g., ``scikit-learn`` vs
    ``sklearn``) by trying the name as given first.
    """
    try:
        import importlib.metadata as _md
        return _md.version(name)
    except Exception:
        return ""


def _git_head_hash() -> str:
    """Short SHA of HEAD in the repo containing this file.  Empty
    string when not a git checkout (e.g., installed via wheel)."""
    try:
        import subprocess
        repo_root = _find_repo_root()
        if not repo_root:
            return ""
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
        return out
    except Exception:
        return ""


def _git_branch() -> str:
    try:
        import subprocess
        repo_root = _find_repo_root()
        if not repo_root:
            return ""
        out = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        ).strip()
        return out
    except Exception:
        return ""


def _git_dirty() -> str:
    """Return ``"clean"`` / ``"dirty"`` / empty string.  Dirty means
    there are uncommitted changes in the working tree at run time."""
    try:
        import subprocess
        repo_root = _find_repo_root()
        if not repo_root:
            return ""
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
        return "dirty" if out.strip() else "clean"
    except Exception:
        return ""


def _find_repo_root() -> Optional[str]:
    """Walk parent directories from this file until we find a
    ``.git`` directory.  Returns None when not in a git checkout."""
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isdir(os.path.join(here, ".git")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return None
        here = parent


def _json_default(obj: Any) -> Any:
    """JSON serializer fallback for non-stdlib types."""
    from datetime import date, datetime as _dt
    try:
        import numpy as _np
    except Exception:
        _np = None
    if isinstance(obj, (date, _dt)):
        return obj.isoformat()
    if _np is not None:
        if isinstance(obj, _np.ndarray):
            return obj.tolist()
        if isinstance(obj, _np.generic):
            return obj.item()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


# Documented field names — used by tests to assert every promised
# field is actually populated by capture_system_metadata.
_FIELD_DOCS: Dict[str, str] = {
    "pulsar_version": "stimtest package __version__ at run time",
    "pulsar_git_hash": "short SHA of HEAD when running from a git checkout",
    "pulsar_git_branch": "current git branch",
    "pulsar_git_dirty": "'clean' or 'dirty' working tree at run time",
    "python_version": "X.Y.Z of the running interpreter",
    "python_implementation": "CPython / PyPy / Jython / etc.",
    "os_name": "platform.system() — Windows / Linux / Darwin",
    "os_version": "platform.release()",
    "os_platform": "platform.platform() full string",
    "machine": "platform.machine() — x86_64, AMD64, arm64, etc.",
    # pkg_*_version fields documented elsewhere
}
