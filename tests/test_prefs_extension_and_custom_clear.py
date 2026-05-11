"""Tests for the prefs file-extension change and the on-close
custom-entry cleanup.

* ``gui_prefs.setting`` replaces ``gui_prefs.json`` for the
  auto-save filename; legacy ``.json`` files are still loaded
  when no ``.setting`` exists.
* User-saved profiles use the ``.setting`` extension by default
  (Save dialog filter + auto-append), but ``.json`` files load
  cleanly via the same dialog filter.
* User-added "custom" entries (custom coatings, custom return /
  reference electrodes, custom devices + their state) are
  stripped from the prefs payload on GUI close so they don't
  survive into the next session.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ---------------------------------------------------------------- file extension


def test_prefs_filename_uses_setting_extension():
    """The auto-save filename ends with ``.setting``."""
    from stimtest.gui.prefs import PREFS_FILE, prefs_path
    assert PREFS_FILE.endswith(".setting")
    assert str(prefs_path()).endswith(".setting")


def test_user_facing_extension_constants_match():
    """The user-save / user-load helpers expose ``.setting`` as
    the canonical user-saved extension and ``.json`` as the
    accepted legacy fallback."""
    from stimtest.gui.prefs import (
        PREFS_USER_EXT, PREFS_USER_EXT_LEGACY, PREFS_USER_FILTER,
    )
    assert PREFS_USER_EXT == ".setting"
    assert PREFS_USER_EXT_LEGACY == ".json"
    # Filter lists ``*.setting`` first (preferred), with ``*.json``
    # offered as the legacy back-compat option.
    assert "*.setting" in PREFS_USER_FILTER
    assert "*.json" in PREFS_USER_FILTER
    assert PREFS_USER_FILTER.index("*.setting") < PREFS_USER_FILTER.index("*.json")


def test_load_prefs_reads_setting_file(tmp_path, monkeypatch):
    """``load_prefs`` reads the new ``gui_prefs.setting`` file."""
    from stimtest.gui import prefs as prefs_mod

    def _fake_dir():
        return tmp_path

    monkeypatch.setattr(prefs_mod, "prefs_dir", _fake_dir)
    # Write a .setting file in the fake prefs dir.
    new_file = tmp_path / "gui_prefs.setting"
    payload = {"setup_tab": {"device": "Linear"}}
    new_file.write_text(json.dumps(payload), encoding="utf-8")
    loaded = prefs_mod.load_prefs()
    assert loaded == payload


def test_load_prefs_falls_back_to_legacy_json(tmp_path, monkeypatch):
    """When ``gui_prefs.setting`` doesn't exist but the legacy
    ``gui_prefs.json`` does, ``load_prefs`` reads from it so
    upgrading users don't lose their saved state."""
    from stimtest.gui import prefs as prefs_mod
    monkeypatch.setattr(prefs_mod, "prefs_dir", lambda: tmp_path)
    legacy_file = tmp_path / "gui_prefs.json"
    payload = {"setup_tab": {"device": "Linear"}}
    legacy_file.write_text(json.dumps(payload), encoding="utf-8")
    loaded = prefs_mod.load_prefs()
    assert loaded == payload


def test_load_prefs_prefers_setting_over_legacy(tmp_path, monkeypatch):
    """When BOTH ``gui_prefs.setting`` and ``gui_prefs.json``
    exist (e.g. the user upgraded and then saved a session), the
    new ``.setting`` file wins."""
    from stimtest.gui import prefs as prefs_mod
    monkeypatch.setattr(prefs_mod, "prefs_dir", lambda: tmp_path)
    new_file = tmp_path / "gui_prefs.setting"
    legacy_file = tmp_path / "gui_prefs.json"
    new_payload = {"setup_tab": {"device": "Blackrock Omnetics (4×4)"}}
    legacy_payload = {"setup_tab": {"device": "Linear"}}
    new_file.write_text(json.dumps(new_payload), encoding="utf-8")
    legacy_file.write_text(json.dumps(legacy_payload), encoding="utf-8")
    assert prefs_mod.load_prefs() == new_payload


# ---------------------------------------------------------------- custom-entry strip on close


def test_main_window_closeevent_strips_custom_entries(qapp, tmp_path,
                                                     monkeypatch):
    """When the main window closes, the custom-entry keys
    (custom coating / return / reference / device entries) are
    stripped from the saved prefs payload — so they don't
    survive into the next session."""
    # Re-route prefs writes to a temp dir so the test doesn't
    # touch the user's actual prefs file.
    from stimtest.gui import prefs as prefs_mod
    monkeypatch.setattr(prefs_mod, "prefs_dir", lambda: tmp_path)
    # Patch ``main_window``'s own reference to ``prefs_dir`` /
    # ``save_prefs`` after monkeypatching, in case it cached
    # them at import time.
    from stimtest.gui import main_window as mw_mod
    monkeypatch.setattr(mw_mod, "prefs_dir", lambda: tmp_path)
    # Build the main window. Pretend hardware isn't there.
    from stimtest.gui.main_window import MainWindow
    mw = MainWindow()
    # Inject some custom entries into the setup tab.
    setup = mw.setup_tab
    setup._coating_custom_entries.append("UserCoatingA")
    setup._return_custom_entries.append("UserReturnB")
    setup._reference_custom_entries.append("UserRefC")
    setup._device_custom_entries.append("UserDeviceD")
    setup._device_custom_state["UserDeviceD"] = {
        "layout": "rect", "mapping": [[1, 2], [3, 4]],
    }
    # Before close, the entries are present in the prefs snapshot.
    pre = mw._collect_prefs_payload()
    setup_pre = pre.get("setup", {})
    assert "UserCoatingA" in setup_pre.get("coating_custom_entries", [])
    assert "UserDeviceD" in setup_pre.get("device_custom_entries", [])
    # Trigger closeEvent — should write the prefs file but with
    # custom-entry keys removed from the setup section.
    from PyQt6 import QtGui
    ev = QtGui.QCloseEvent()
    mw.closeEvent(ev)
    # Read the saved file and verify the custom-entry keys are gone.
    saved_file = tmp_path / "gui_prefs.setting"
    assert saved_file.is_file(), f"prefs file not written: {tmp_path}"
    saved = json.loads(saved_file.read_text(encoding="utf-8"))
    setup_saved = saved.get("setup", {})
    for key in ("coating_custom_entries", "return_custom_entries",
                "reference_custom_entries", "device_custom_entries",
                "device_custom_state"):
        assert key not in setup_saved, (
            f"on-close strip should have removed {key!r} from "
            f"setup_tab prefs; got setup_tab keys "
            f"{list(setup_saved.keys())}")


def test_in_session_autosave_keeps_custom_entries(qapp, tmp_path,
                                                  monkeypatch):
    """Auto-saves DURING a session (via the Start-button hook)
    keep the custom entries so a mid-session crash doesn't lose
    them. The strip applies only to the close-time save."""
    from stimtest.gui import prefs as prefs_mod
    from stimtest.gui import main_window as mw_mod
    monkeypatch.setattr(prefs_mod, "prefs_dir", lambda: tmp_path)
    monkeypatch.setattr(mw_mod, "prefs_dir", lambda: tmp_path)
    from stimtest.gui.main_window import MainWindow
    mw = MainWindow()
    setup = mw.setup_tab
    setup._coating_custom_entries.append("UserCoatingX")
    setup._device_custom_entries.append("UserDeviceX")
    # Trigger the in-session save (what the Start button does).
    mw._save_prefs_from_tabs()
    saved_file = tmp_path / "gui_prefs.setting"
    assert saved_file.is_file()
    saved = json.loads(saved_file.read_text(encoding="utf-8"))
    setup_saved = saved.get("setup", {})
    # Custom entries SHOULD be present after in-session save.
    assert "UserCoatingX" in setup_saved.get("coating_custom_entries", [])
    assert "UserDeviceX" in setup_saved.get("device_custom_entries", [])
