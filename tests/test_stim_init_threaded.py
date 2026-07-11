"""Stimulator initialization runs OFF the GUI thread (CWRU freeze fix).

``PS_InitAllStim`` is a blocking DLL call that can hang for many seconds —
or indefinitely — when the Plexon Sim-2 / Stim-2 GUI is holding the
exclusive USB lock (or the device is wedged after that GUI was force-killed
mid-session).  A CWRU operator hit exactly this: the GUI froze ("Not
Responding") at Initialize and they force-quit + relaunched 13 times.

``ConnectionPanel._do_initialize_stim`` now runs ``open()`` on a worker
thread (mirroring the scope-connect flow), marshals the result back to the
GUI thread via ``_stimInitResult``, and logs a "close Sim-2 / power-cycle"
hint via a watchdog if the init is still running after ~15 s.  The window
stays responsive throughout.

Pinned invariants:
  * The Initialize call RETURNS before the blocking ``open()`` completes
    (proves it doesn't block the GUI thread).
  * ``self._stim`` is assigned on the GUI callback, never on the worker.
  * A second init while one is in flight is a guarded no-op (the PlexStim
    DLL is single-producer).
  * A worker-side exception resets the UI cleanly (stim None, button
    re-enabled) instead of leaving the panel wedged.
  * The watchdog logs an actionable hint only while an init is in flight.
"""
from __future__ import annotations

import os
import sys
import time
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets  # noqa: E402

from stimtest.gui import connection_panel as cp  # noqa: E402
from stimtest.gui.connection_panel import ConnectionPanel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("pulsar-pytest")   # sandbox prefs (gotcha #100)
    return app


def _pump_until(app, pred, timeout=6.0):
    """Spin the event loop until ``pred()`` is true or the timeout lapses.

    Delivers the cross-thread ``_stimInitResult`` signal + runs the GUI
    callback.  Returns whether the predicate became true.
    """
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        app.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    app.processEvents()
    return pred()


class _SlowSimStim:
    """A real simulator stimulator whose ``open()`` sleeps first.

    Delegates everything else to the inner sim stim so the GUI callback's
    success path (info read, scaling preset, auto-discharge) runs for real.
    The sleep lets the test observe the in-flight state BEFORE ``open()``
    returns — i.e. prove the call didn't block the GUI thread.
    """

    def __init__(self, delay_s: float):
        self._inner = cp.open_stimulator(simulate=True)
        self._delay_s = delay_s
        self.opened = False

    def open(self):
        time.sleep(self._delay_s)
        self._inner.open()
        self.opened = True

    def __getattr__(self, name):   # info / set_auto_discharge / close / …
        return getattr(self._inner, name)


# ---------------------------------------------------------------------------
# Signal + constant surface
# ---------------------------------------------------------------------------
def test_signal_and_watchdog_constant_exist(qapp):
    conn = ConnectionPanel()
    assert hasattr(conn, "_stimInitResult")
    assert isinstance(ConnectionPanel._STIM_INIT_WATCHDOG_MS, int)
    assert ConnectionPanel._STIM_INIT_WATCHDOG_MS > 0
    assert conn._stim_init_in_flight is False


# ---------------------------------------------------------------------------
# The Initialize call does NOT block the GUI thread
# ---------------------------------------------------------------------------
def test_initialize_returns_before_open_completes(qapp, monkeypatch):
    conn = ConnectionPanel()
    slow = _SlowSimStim(delay_s=0.4)
    monkeypatch.setattr(cp, "open_stimulator", lambda simulate=True: slow)

    t0 = time.monotonic()
    conn._do_initialize_stim()            # simulate checkbox on by default
    call_dt = time.monotonic() - t0

    # The call returned near-instantly even though open() sleeps 0.4 s —
    # the blocking work is on the worker thread, not the GUI thread.
    assert call_dt < 0.25
    assert conn._stim_init_in_flight is True
    # ``_stim`` is assigned on the GUI callback, which hasn't run yet
    # (the worker is still inside the sleeping open()).
    assert conn._stim is None
    assert slow.opened is False

    # Pump the loop: worker finishes open(), emits, GUI callback assigns.
    assert _pump_until(qapp, lambda: not conn._stim_init_in_flight)
    assert slow.opened is True
    assert conn._stim is slow
    assert conn.close_btn.isEnabled() is True
    assert conn.init_btn.isEnabled() is False   # open → use Close to re-init


# ---------------------------------------------------------------------------
# End-to-end with the real simulator (no monkeypatch)
# ---------------------------------------------------------------------------
def test_real_simulator_init_end_to_end(qapp):
    conn = ConnectionPanel()
    conn._do_initialize_stim()
    assert _pump_until(qapp, lambda: conn._stim is not None)
    assert conn._stim_init_in_flight is False
    info = conn._stim.info
    assert info.is_simulated is True
    assert conn.close_btn.isEnabled() is True
    conn._do_close_stim()
    assert conn._stim is None


# ---------------------------------------------------------------------------
# In-flight guard — never start a second, concurrent init
# ---------------------------------------------------------------------------
def test_second_init_while_in_flight_is_a_noop(qapp, monkeypatch):
    from unittest.mock import MagicMock
    conn = ConnectionPanel()
    logs: list[str] = []
    conn.log.connect(logs.append)

    factory = MagicMock()
    monkeypatch.setattr(cp, "open_stimulator", factory)

    conn._stim_init_in_flight = True          # pretend one is running
    conn._do_initialize_stim()

    factory.assert_not_called()               # no second worker started
    assert any("already in progress" in m.lower() for m in logs)
    assert conn._stim_init_in_flight is True   # unchanged


# ---------------------------------------------------------------------------
# Worker-side failure resets the UI cleanly
# ---------------------------------------------------------------------------
def test_failed_open_resets_ui(qapp):
    conn = ConnectionPanel()
    logs: list[str] = []
    conn.log.connect(logs.append)
    conn._stim_init_in_flight = True
    conn.init_btn.setEnabled(False)

    conn._on_stim_init_result(RuntimeError("PS_InitAllStim failed: boom"))

    assert conn._stim is None
    assert conn._stim_init_in_flight is False
    assert conn.init_btn.isEnabled() is True   # user can retry
    assert conn.close_btn.isEnabled() is False
    assert any("open failed" in m.lower() for m in logs)


# ---------------------------------------------------------------------------
# A successful Initialize reconciles the pre-init PnP detection indicator
# (CWRU: stale amber "detection unavailable" next to green "Initialized")
# ---------------------------------------------------------------------------
def _fake_real_stim(serial="PLX00178"):
    info = types.SimpleNamespace(
        is_simulated=False, serial_number=serial,
        description="Plexon 14-20-A-10-F", firmware="0.0.6", n_channels=16)
    return types.SimpleNamespace(
        info=info, set_auto_discharge=lambda *a, **k: None)


def test_successful_init_reconciles_detection_indicator(qapp, monkeypatch):
    conn = ConnectionPanel()
    # Keep the callback headless: skip the scaling apply, the label rebuild,
    # and the calibrate-button refresh.  (The old "uncalibrated serial" modal
    # was removed — verification is optional, gotcha #165 companion.)
    monkeypatch.setattr(conn, "_apply_scaling_preset", lambda *a, **k: None)
    monkeypatch.setattr(conn, "_refresh_stim_label", lambda *a, **k: None)
    monkeypatch.setattr(conn, "_update_calibrate_btn", lambda *a, **k: None)

    # Reproduce the CWRU state: the pre-init PnP probe timed out to the amber
    # "Stimulator detection unavailable" state.
    conn._set_dot(conn.stim_detect_dot, cp._DOT_WARN)
    conn.stim_detect_text.setText("Stimulator detection unavailable")
    conn._stim_init_in_flight = True

    conn._on_stim_init_result(_fake_real_stim())

    # The successful open reconciles the detection line to green "detected".
    assert conn.stim_detect_text.text() == "Stimulator detected"
    assert "PLX00178" in conn.stim_detect_text.toolTip()


def test_simulator_init_does_not_claim_hardware_detected(qapp):
    """The reconciliation is real-hardware only — a sim init must NOT flip the
    detection line to 'Stimulator detected' (there's no physical device)."""
    conn = ConnectionPanel()
    conn.stim_detect_text.setText("Stimulator not detected")
    conn._do_initialize_stim()                       # simulator (default)
    assert _pump_until(qapp, lambda: conn._stim is not None)
    assert conn.stim_detect_text.text() != "Stimulator detected"


# ---------------------------------------------------------------------------
# Watchdog hint fires only while in flight
# ---------------------------------------------------------------------------
def test_watchdog_hint_only_when_in_flight(qapp):
    conn = ConnectionPanel()
    logs: list[str] = []
    conn.log.connect(logs.append)

    # Not in flight → silent.
    conn._stim_init_in_flight = False
    conn._on_stim_init_slow()
    assert logs == []

    # In flight → actionable hint mentioning Sim-2 / power-cycle.
    conn._stim_init_in_flight = True
    conn._on_stim_init_slow()
    assert len(logs) == 1
    hint = logs[0].lower()
    assert "sim-2" in hint or "stim-2" in hint
    assert "power-cycle" in hint
