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
#: Auto-save filename. The ``.setting`` extension distinguishes
#: stimtest profiles from generic JSON in file managers / pickers,
#: while the contents stay JSON (so existing inspectors keep
#: working). See :func:`load_prefs` for the legacy ``.json``
#: migration path that catches users upgrading from older
#: versions of the app.
PREFS_FILE = "gui_prefs.setting"
PREFS_FILE_LEGACY = "gui_prefs.json"
#: Extension for user-saved settings profiles (via File → Save
#: settings…). Same format as the auto-save file — the
#: extension is a label not a content-type marker.
PREFS_USER_EXT = ".setting"
PREFS_USER_EXT_LEGACY = ".json"
PREFS_USER_FILTER = "Settings (*.setting);;JSON (*.json);;All files (*)"

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


def _legacy_prefs_path() -> Path:
    """Pre-v1 auto-save filename (``gui_prefs.json``). Read-only
    fallback so users upgrading from older builds don't lose their
    saved settings."""
    return prefs_dir() / PREFS_FILE_LEGACY


def load_prefs() -> Dict[str, Dict[str, Any]]:
    """Read the prefs JSON. Returns an empty dict on any failure (no nag).

    Tries the new ``gui_prefs.setting`` first; falls back to the
    legacy ``gui_prefs.json`` if the new file doesn't exist yet.
    The legacy file is left in place so a one-time crash-or-rollback
    can recover from it; the next successful close-time save creates
    the new ``.setting`` file alongside.
    """
    p = prefs_path()
    if not p.is_file():
        legacy = _legacy_prefs_path()
        if legacy.is_file():
            p = legacy
        else:
            return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        # Strip metadata that isn't a section.  LIST-valued sections are kept
        # as well as dict ones: ``imported_profiles`` is written by
        # ``_collect_prefs_payload`` as a LIST, and a dict-only filter silently
        # dropped it on load — so an imported profile (e.g. CWRU) never
        # survived a restart even though it was correctly written to disk.
        return {k: v for k, v in data.items()
                if isinstance(v, (dict, list)) and not k.startswith("_")}
    except Exception as e:
        _log.warning("Could not read prefs at %s: %s", p, e)
        return {}


def _preserve_unowned_sections(p: Path,
                               prefs: Dict[str, Dict[str, Any]]
                               ) -> Dict[str, Dict[str, Any]]:
    """Carry forward top-level sections the caller's payload doesn't mention.

    ``_write_prefs_atomic`` is a WHOLESALE REPLACE, but several sections are
    maintained by a read-modify-write against :func:`load_prefs` somewhere
    else entirely — ``theme`` (View menu) and ``stim_scaling_by_serial``
    (Verification / ConnectionPanel) are the current examples.  The main
    window's snapshot payload doesn't know about those, so every auto-save
    silently DELETED them: the theme could not survive an app close, and a
    verification's serial→preset record was lost on the next save.

    Rather than require every future section to be added to the snapshot —
    the exact omission that caused the bug — merge anything already on disk
    that the payload doesn't claim.  Keys the payload DOES carry always win,
    so a real update is never shadowed by the stale on-disk copy.

    Deliberately NOT applied to :func:`save_prefs_to`: an exported profile
    should be exactly what was snapshotted, not a union with whatever the
    user happened to pick as the destination file.
    """
    try:
        if not p.is_file():
            return prefs
        with p.open("r", encoding="utf-8") as f:
            existing = json.load(f)
        if not isinstance(existing, dict):
            return prefs
    except Exception:
        # An unreadable/corrupt prefs file must never block the save.
        return prefs
    merged = dict(prefs)
    for k, v in existing.items():
        if k.startswith("_") or k in merged:
            continue
        merged[k] = v
    return merged


def save_prefs(prefs: Dict[str, Dict[str, Any]]) -> None:
    """Atomically write the prefs JSON. Silent on failure (it's a convenience).

    Sections the payload doesn't mention are preserved — see
    :func:`_preserve_unowned_sections`.
    """
    p = prefs_path()
    try:
        _write_prefs_atomic(p, _preserve_unowned_sections(p, prefs))
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
    # Keep list-valued sections too — see the note in ``load_prefs``.
    return {k: v for k, v in data.items()
            if isinstance(v, (dict, list)) and not k.startswith("_")}


def _write_prefs_atomic(p: Path, prefs: Dict[str, Dict[str, Any]]) -> None:
    """Shared writer used by both auto-save and explicit save."""
    payload = dict(prefs)
    payload["_meta"] = {"version": PREFS_VERSION, "path": str(p)}
    tmp = p.with_suffix(p.suffix + ".tmp")
    p.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    tmp.replace(p)
