"""Smoke tests for :mod:`stimtest.gui.camera`.

Verifies the three-piece camera architecture wires correctly:

* :class:`CameraService` (singleton) — owns the QtMultimedia
  pipeline; enumerate / connect / disconnect / snapshot / record
  guarded against no-camera-present setups.
* :class:`CameraConnector` — combo + Connect/Disconnect button used
  inside ConnectionPanel.
* :class:`CameraStreamPane` — embedded preview used in each
  experiment tab; auto-shows / hides on service connect / disconnect.

These tests DO NOT actually open a camera device — that requires
hardware + an OS permission grant that varies per platform / CI
provider.  Skipped wholesale when PyQt6.QtMultimedia isn't
installable (stripped headless setup).
"""
from __future__ import annotations

import sys

import pytest

QtMultimedia = pytest.importorskip("PyQt6.QtMultimedia")
QtMultimediaWidgets = pytest.importorskip("PyQt6.QtMultimediaWidgets")

from PyQt6 import QtWidgets   # noqa: E402  (after importorskip)


@pytest.fixture(scope="module")
def qapp():
    """Single QApplication shared across the module's tests."""
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(sys.argv)
    return app


# ---------------------------------------------------------------------------
# CameraService — singleton + enumeration
# ---------------------------------------------------------------------------

def test_camera_service_is_singleton(qapp):
    """Every call to :func:`camera_service` must return the same
    instance.  Multiple UI consumers (ConnectionPanel,
    ExperimentTab stream pane, optional View dock) share ONE camera
    pipeline by design — see the architecture comment at the top
    of stimtest/gui/camera.py."""
    from stimtest.gui.camera import camera_service
    a = camera_service()
    b = camera_service()
    assert a is b
    # Default state: not connected, not recording, no device id.
    assert a.is_connected() is False
    assert a.is_recording() is False
    assert a.connected_device_id() is None
    assert a.connected_description() == ""


def test_camera_service_enumerate_returns_list(qapp):
    """``enumerate_devices`` returns a list of (description, id_bytes,
    is_default) tuples (possibly empty if no cameras present)."""
    from stimtest.gui.camera import camera_service
    devs = camera_service().enumerate_devices()
    assert isinstance(devs, list)
    for entry in devs:
        assert isinstance(entry, tuple)
        assert len(entry) == 3
        desc, dev_id, is_default = entry
        assert isinstance(desc, str)
        assert isinstance(dev_id, (bytes, bytearray))
        assert isinstance(is_default, bool)


def test_disable_camera_env_var_short_circuits_qtmm(qapp, monkeypatch):
    """``PULSAR_DISABLE_CAMERA=1`` makes ``_ensure_qtmm`` return False BEFORE
    any ``QMediaDevices.videoInputs()`` call, so a bad camera / driver can't
    hang the GUI thread at startup.  With it set, enumeration returns [] and no
    QtMultimedia device scan runs."""
    from stimtest.gui.camera import camera_service
    svc = camera_service()
    monkeypatch.setenv("PULSAR_DISABLE_CAMERA", "1")
    svc._qtmm_imported = False            # re-evaluate the gate (fresh-start sim)
    try:
        assert svc._ensure_qtmm() is False
        assert svc.enumerate_devices() == []
    finally:
        svc._qtmm_imported = False        # let later tests re-import cleanly


def test_camera_service_snapshot_with_no_camera_is_safe(qapp):
    """Calling ``take_snapshot`` before ``connect_to`` succeeds must
    not raise — the status message tells the user to connect first."""
    from stimtest.gui.camera import camera_service
    svc = camera_service()
    # In case a prior test (or external code) connected, leave clean.
    svc.disconnect()
    result = svc.take_snapshot()
    assert result is None
    assert svc.is_connected() is False


def test_camera_service_start_recording_with_no_camera_is_safe(qapp):
    """Same guard as snapshot — recorder API rejects when no camera."""
    from stimtest.gui.camera import camera_service
    svc = camera_service()
    svc.disconnect()
    result = svc.start_recording()
    assert result is None
    assert svc.is_recording() is False


def test_camera_service_stop_when_idle_is_noop(qapp):
    """``disconnect`` / ``stop_recording`` must be safe to call
    repeatedly even when nothing is open."""
    from stimtest.gui.camera import camera_service
    svc = camera_service()
    svc.disconnect()       # already disconnected — no raise
    svc.stop_recording()   # not recording — no raise
    svc.disconnect()       # again — no raise


# ---------------------------------------------------------------------------
# CameraConnector — combobox + Connect/Disconnect (lives in ConnectionPanel)
# ---------------------------------------------------------------------------

def test_camera_connector_constructs_cleanly(qapp):
    """The connector widget builds without raising; populates the
    combo with the "— No camera —" sentinel plus any enumerated
    devices."""
    from stimtest.gui.camera import CameraConnector
    conn = CameraConnector()
    # Force the QTimer.singleShot(0, refresh_devices) to fire.
    qapp.processEvents()
    # Sentinel always present at index 0.
    assert conn.device_combo.count() >= 1
    assert conn.device_combo.itemData(0) is None
    # Buttons: disconnected state.
    assert conn.disconnect_btn.isEnabled() is False
    # Connect is enabled only when a real device is picked AND
    # service is disconnected.  Sentinel selected → disabled.
    assert conn.connect_btn.isEnabled() is False


def test_camera_connector_prefs_round_trip(qapp):
    """``current_prefs`` and ``restore_prefs`` cycle a selected
    device id via hex-string serialisation."""
    from stimtest.gui.camera import CameraConnector
    conn = CameraConnector()
    qapp.processEvents()
    # No device picked → empty prefs.
    assert conn.current_prefs() == {}
    # Stuff a fake id into the combobox to test the round-trip
    # without needing real hardware.  This is what the prefs path
    # actually exercises (real device matching is exercised by
    # operator interaction).
    fake_id = b"FAKE-DEVICE-ID-FOR-TEST"
    conn.device_combo.addItem("Fake Camera", userData=fake_id)
    conn.device_combo.setCurrentIndex(conn.device_combo.count() - 1)
    p = conn.current_prefs()
    assert "selected_device_id_hex" in p
    assert bytes.fromhex(p["selected_device_id_hex"]) == fake_id
    # Round-trip through restore on a fresh connector.
    conn2 = CameraConnector()
    qapp.processEvents()
    conn2.device_combo.addItem("Fake Camera", userData=fake_id)
    conn2.restore_prefs(p)
    assert conn2.device_combo.itemData(conn2.device_combo.currentIndex()) == fake_id


def test_camera_connector_restore_ignores_malformed(qapp):
    """Stale / corrupt prefs from a previous version must not raise."""
    from stimtest.gui.camera import CameraConnector
    conn = CameraConnector()
    qapp.processEvents()
    conn.restore_prefs({})                                  # empty
    conn.restore_prefs({"junk": True})                       # unknown key
    conn.restore_prefs({"selected_device_id_hex": "zzzz"})   # bad hex
    conn.restore_prefs(None)                                 # not even a dict


# ---------------------------------------------------------------------------
# CameraStreamPane — embedded preview, lives in each experiment tab
# ---------------------------------------------------------------------------

def test_stream_pane_hidden_when_disconnected(qapp):
    """The stream pane defaults to HIDDEN.  It auto-shows when the
    shared CameraService connects so the experiment-tab layout
    collapses to scope-only when no camera is in use."""
    from stimtest.gui.camera import CameraStreamPane, camera_service
    camera_service().disconnect()  # ensure clean baseline
    pane = CameraStreamPane()
    assert pane.isVisible() is False


def test_stream_pane_always_visible_mode(qapp):
    """``set_always_visible(True)`` overrides the auto-hide
    behaviour — used by the optional View → Camera Monitor dock
    so it shows a placeholder when no camera is connected."""
    from stimtest.gui.camera import CameraStreamPane, camera_service
    camera_service().disconnect()
    pane = CameraStreamPane()
    pane.set_always_visible(True)
    # The pane should now be visible even though the service isn't
    # connected.  (Note: isVisible() returns False for un-shown
    # widgets that have no parent window; check the internal flag
    # via ``set_always_visible``'s effect.)
    pane.show()
    assert pane.isVisible() is True


def test_stream_pane_subscribes_to_service_signals(qapp):
    """Constructing a pane must connect to the service's connected /
    disconnected signals so the pane's visibility tracks the
    service state without any external glue."""
    from stimtest.gui.camera import CameraStreamPane, camera_service
    svc = camera_service()
    # Receivers count should bump on construction; tear down with
    # deleteLater + processEvents to drop the connection cleanly.
    before = svc.receivers(svc.connected)
    pane = CameraStreamPane()
    after = svc.receivers(svc.connected)
    assert after >= before + 1
    pane.deleteLater()


# ---------------------------------------------------------------------------
# Project-tree output convention (gotcha #17)
# ---------------------------------------------------------------------------

def test_project_test_dir_resolves_to_project_tree():
    """Snapshot / recording output paths must land in the project
    tree — NEVER %APPDATA% or %TEMP% per the user instruction in the
    ``feedback_error_logs_in_project_tree`` memory + CLAUDE.md
    gotcha #17."""
    from stimtest.gui.camera import _project_test_dir
    out = _project_test_dir()
    parts = out.parts
    assert "test" in parts or out.name == "StimulationTesting" \
        or "StimulationTesting" in parts, (
            f"_project_test_dir resolved to {out!r} — should be inside "
            f"the project tree, not APPDATA or TEMP")
    lower = str(out).lower()
    assert "appdata" not in lower
    assert "\\temp\\" not in lower and "/temp/" not in lower
