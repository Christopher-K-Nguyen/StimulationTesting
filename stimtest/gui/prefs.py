"""Persistent GUI preferences — last-used inputs become next-launch defaults.

Stored as a single JSON file under the user's app-config directory so it
survives app upgrades and can be inspected/edited by hand. Each tab
implements ``current_prefs()`` to dump its state and ``restore_prefs()``
to load it back; the prefs file is just a ``{section: {key: value}}``
dict, where the section name matches the tab.

Save triggers
-------------
* Window close (``MainWindow.closeEvent``) — covers the normal quit path.
* End of every experiment (the ``RunnerWorker.finished`` signal) — covers
  long-running sessions where the user might never hit "X" before the
  machine reboots overnight.

We deliberately do NOT save on every keystroke. The file is tiny but
spamming disk I/O on every spinbox tick is wasteful, and the close /
post-run hooks are sufficient because the user always finishes either
by closing the app or by running an experiment.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict

from PyQt6 import QtCore


PREFS_VERSION = 1
PREFS_FILE = "gui_prefs.json"

_log = logging.getLogger(__name__)


def prefs_dir() -> Path:
    """Return ``%APPDATA%\\StimulationTesting`` (or platform equivalent).

    Falls back to ``~/.stimtest`` if Qt's standard-paths machinery
    returns an empty string for any reason (rare, but defensive).
    """
    base = QtCore.QStandardPaths.writableLocation(
        QtCore.QStandardPaths.StandardLocation.AppConfigLocation)
    if not base:
        base = str(Path.home() / ".stimtest")
    p = Path(base) / "StimulationTesting"
    p.mkdir(parents=True, exist_ok=True)
    return p


def prefs_path() -> Path:
    return prefs_dir() / PREFS_FILE


def load_prefs() -> Dict[str, Dict[str, Any]]:
    """Read the prefs JSON. Returns an empty dict on any failure (no nag)."""
    p = prefs_path()
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # Strip metadata that isn't a section
        return {k: v for k, v in data.items() if isinstance(v, dict) and not k.startswith("_")}
    except Exception as e:
        _log.warning("Could not read prefs at %s: %s", p, e)
        return {}


def save_prefs(prefs: Dict[str, Dict[str, Any]]) -> None:
    """Atomically write the prefs JSON. Silent on failure (it's a convenience)."""
    p = prefs_path()
    try:
        _write_prefs_atomic(p, prefs)
    except Exception as e:
        _log.warning("Could not write prefs at %s: %s", p, e)


def save_prefs_to(path: Path, prefs: Dict[str, Dict[str, Any]]) -> None:
    """Write a named-profile prefs file to ``path`` (user-picked).

    Used by the File → Save settings… menu item, which lets the user
    keep multiple named profiles (e.g. one per electrode array, one
    per subject). Raises on failure so the GUI can surface a message
    box — silent failure is OK for the auto-save path but not when
    the user explicitly clicked Save.
    """
    _write_prefs_atomic(Path(path), prefs)


def load_prefs_from(path: Path) -> Dict[str, Dict[str, Any]]:
    """Read a named-profile prefs file from ``path``.

    Raises on a malformed/missing file so the GUI can show the user
    what went wrong.
    """
    p = Path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{p}: top-level JSON object expected.")
    return {k: v for k, v in data.items() if isinstance(v, dict) and not k.startswith("_")}


def _write_prefs_atomic(p: Path, prefs: Dict[str, Dict[str, Any]]) -> None:
    """Shared writer used by both auto-save and explicit save."""
    payload = dict(prefs)
    payload["_meta"] = {"version": PREFS_VERSION, "path": str(p)}
    tmp = p.with_suffix(p.suffix + ".tmp")
    p.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    tmp.replace(p)
