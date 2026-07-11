"""Tests for the per-tab BiasFeedbackPanel widget.

The panel combines a host-delegated BiasConnector + closed-loop
feedback config controls + a live status badge.  Tests pin:

* Construction without a host (initial Test-Parameters state, Connect
  disabled until ``set_bias_host`` runs).
* ``set_bias_host`` re-enables the connector and wires it to the
  shared driver lifecycle.
* ``feedback_config()`` returns None when the master Enable checkbox
  is off, otherwise a populated BiasFeedbackConfig matching the
  spinbox state.
* ``set_status()`` updates the badge across DISARMED / ARMED /
  SATURATED / V_MON FAIL states.
* Prefs round-trip preserves operator-visible state.
* The connection-panel surface still exposes the host facade (so
  per-tab connectors keep working after the in-panel widget was
  removed in this commit).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from PyQt6 import QtCore, QtWidgets


# ---------------------------------------------------------------------------
# Shared QApplication
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    yield app


# ---------------------------------------------------------------------------
# Helper — minimal host-shaped stub
# ---------------------------------------------------------------------------
class _HostStub(QtCore.QObject):
    """Minimal ConnectionPanel facsimile: exposes biasConnected /
    biasDisconnected + open / close + .bias attribute."""

    biasConnected = QtCore.pyqtSignal(object)
    biasDisconnected = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.bias = None
        self.open_calls = []
        self.close_calls = 0

    def open_bias_module(self, *, simulate, port=None):
        self.open_calls.append((simulate, port))
        # Simulate a successful open: stash a dummy bias object and
        # fire the signal.
        self.bias = MagicMock()
        info = MagicMock()
        self.biasConnected.emit(info)

    def close_bias_module(self):
        self.close_calls += 1
        self.bias = None
        self.biasDisconnected.emit()


# ---------------------------------------------------------------------------
# Construction defaults
# ---------------------------------------------------------------------------
def test_constructs_with_master_checkbox_off(qapp):
    """Default state: Enable checkbox off; per-config spinboxes
    disabled; status badge shows DISARMED."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    assert panel.enable_check.isChecked() is False
    assert panel.setpoint_spin.isEnabled() is False
    assert panel.tolerance_spin.isEnabled() is False
    assert panel.ki_spin.isEnabled() is False
    assert "DISARMED" in panel.status_badge.text()


def test_connector_starts_disabled_without_host(qapp):
    """Until set_bias_host() runs, the Connect button + Simulate
    checkbox are disabled so the operator can't open a parallel
    driver instance.  Per-tab connectors must always delegate to
    the shared host."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    assert panel._connector.connect_btn.isEnabled() is False
    assert panel._connector.simulate_check.isEnabled() is False


# ---------------------------------------------------------------------------
# set_bias_host wiring
# ---------------------------------------------------------------------------
def test_set_bias_host_enables_connector(qapp):
    """Plumbing in a host re-enables Connect + Simulate so the
    operator can actually open the driver."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    host = _HostStub()
    panel.set_bias_host(host)
    assert panel._connector.connect_btn.isEnabled() is True
    assert panel._connector.simulate_check.isEnabled() is True


def test_set_bias_host_propagates_open(qapp):
    """After set_bias_host, pressing the connector's Connect button
    delegates to host.open_bias_module."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    host = _HostStub()
    panel.set_bias_host(host)
    panel._connector.simulate_check.setChecked(True)
    # Manually trigger the connect action.
    panel._connector._do_connect()
    assert len(host.open_calls) == 1
    assert host.open_calls[0][0] is True  # simulate=True


def test_set_bias_host_idempotent(qapp):
    """Calling set_bias_host with the same host twice doesn't stack
    signal-slot connections (would cause double-mirrored state)."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    host = _HostStub()
    panel.set_bias_host(host)
    panel.set_bias_host(host)
    # Trigger a host-side connect — panel should only see the bias
    # mirrored once, not twice.
    host.open_bias_module(simulate=True)
    # Single mirror on the connector.
    assert panel._connector.bias is host.bias


def test_set_bias_host_none_detaches(qapp):
    """Passing None disconnects from the previous host and disables
    the Connect button again."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    host = _HostStub()
    panel.set_bias_host(host)
    panel.set_bias_host(None)
    assert panel._connector.connect_btn.isEnabled() is False


# ---------------------------------------------------------------------------
# feedback_config()
# ---------------------------------------------------------------------------
def test_feedback_config_returns_none_when_disabled(qapp):
    """Master Enable off → feedback_config() returns None.  Runners
    use this as the 'skip the loop entirely' signal."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    panel.enable_check.setChecked(False)
    assert panel.feedback_config() is None


def test_feedback_config_returns_populated_when_enabled(qapp):
    """Master Enable on → returns a BiasFeedbackConfig matching the
    spinbox state.  Voltage spinboxes are in mV but the config is
    in V — verify the conversion."""
    from stimtest.experiments.bias_feedback import BiasFeedbackConfig
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    panel.setpoint_spin.setValue(0.40)
    panel.tolerance_spin.setValue(10.0)        # mV → 10 mV
    panel.ki_spin.setValue(0.20)
    panel.vmon_sanity_spin.setValue(25.0)      # mV → 25 mV
    panel.gate_lo_spin.setValue(200.0)
    panel.gate_hi_spin.setValue(400.0)
    panel.enable_check.setChecked(True)

    cfg = panel.feedback_config()
    assert isinstance(cfg, BiasFeedbackConfig)
    assert cfg.setpoint_v == pytest.approx(0.40)
    assert cfg.tolerance_v == pytest.approx(0.010)   # mV → V conversion
    assert cfg.k_i == pytest.approx(0.20)
    assert cfg.vmon_sanity_threshold_v == pytest.approx(0.025)
    assert cfg.gating_window_us == pytest.approx((200.0, 400.0))
    assert cfg.channel == "eret"
    assert cfg.vmon_channel == "vmon"


# ---------------------------------------------------------------------------
# Status badge
# ---------------------------------------------------------------------------
def test_status_badge_disarmed_on_none(qapp):
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    panel = BiasFeedbackPanel()
    panel.set_status(None)
    assert "DISARMED" in panel.status_badge.text()


def test_status_badge_armed_on_normal_step(qapp):
    """Normal step (vmon_sane, not saturated) → ARMED with bias +
    error in the text."""
    from stimtest.experiments.bias_feedback import BiasFeedbackStep
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    step = BiasFeedbackStep(
        measured_v=0.305, error_v=0.005, in_deadband=False,
        bias_v_before=0.30, bias_v_after=0.30 - 0.0005,
        saturated=False, vmon_sane=True, skipped=False,
        note="adjusted")
    panel.set_status(step)
    text = panel.status_badge.text()
    assert "ARMED" in text
    assert "bias=" in text
    assert "err=" in text


def test_status_badge_saturated(qapp):
    from stimtest.experiments.bias_feedback import BiasFeedbackStep
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    step = BiasFeedbackStep(
        measured_v=0.50, error_v=0.20, in_deadband=False,
        bias_v_before=4.95, bias_v_after=5.00,
        saturated=True, vmon_sane=True, skipped=False,
        note="rail")
    panel.set_status(step)
    assert "SATURATED" in panel.status_badge.text()


def test_status_badge_vmon_fail(qapp):
    """V_mon sanity failure surfaces a distinct V_MON FAIL badge —
    operator needs to know the bias delivery path is suspect, not
    just that the loop saturated."""
    from stimtest.experiments.bias_feedback import BiasFeedbackStep
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    step = BiasFeedbackStep(
        measured_v=float("nan"), error_v=float("nan"), in_deadband=False,
        bias_v_before=0.30, bias_v_after=0.30,
        saturated=False, vmon_sane=False, skipped=True,
        note="vmon insane")
    panel.set_status(step)
    assert "V_MON" in panel.status_badge.text()


# ---------------------------------------------------------------------------
# Toggle gating
# ---------------------------------------------------------------------------
def test_enable_toggle_gates_spinboxes(qapp):
    """Flipping the master Enable checkbox enables / disables the
    per-config spinboxes — clearer than just-ignored values."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    panel.enable_check.setChecked(True)
    assert panel.setpoint_spin.isEnabled() is True
    panel.enable_check.setChecked(False)
    assert panel.setpoint_spin.isEnabled() is False


def test_enable_off_resets_status_to_disarmed(qapp):
    """Flipping Enable off mid-run resets the status badge — avoids
    stale ARMED text after the operator cancels the loop."""
    from stimtest.experiments.bias_feedback import BiasFeedbackStep
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    panel.enable_check.setChecked(True)
    panel.set_status(BiasFeedbackStep(
        measured_v=0.30, error_v=0.0, in_deadband=True,
        bias_v_before=0.30, bias_v_after=0.30,
        saturated=False, vmon_sane=True, skipped=True,
        note=""))
    assert "ARMED" in panel.status_badge.text()
    panel.enable_check.setChecked(False)
    assert "DISARMED" in panel.status_badge.text()


# ---------------------------------------------------------------------------
# Prefs round-trip
# ---------------------------------------------------------------------------
def test_prefs_dict_captures_all_fields(qapp):
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    panel.enable_check.setChecked(True)
    panel.setpoint_spin.setValue(0.42)
    panel.tolerance_spin.setValue(7.5)
    panel.ki_spin.setValue(0.05)
    panel.vmon_sanity_spin.setValue(33.3)
    panel.gate_lo_spin.setValue(150.0)
    panel.gate_hi_spin.setValue(350.0)

    payload = panel.prefs_dict()
    assert payload["enabled"] is True
    assert payload["setpoint_v"] == pytest.approx(0.42)
    assert payload["tolerance_mv"] == pytest.approx(7.5)
    assert payload["k_i"] == pytest.approx(0.05)
    assert payload["vmon_sanity_mv"] == pytest.approx(33.3)
    assert payload["gate_lo_us"] == pytest.approx(150.0)
    assert payload["gate_hi_us"] == pytest.approx(350.0)


def test_prefs_round_trip_through_two_panels(qapp):
    """Prefs from one panel restore cleanly onto another — proves
    prefs_dict() ↔ restore_prefs() are symmetric."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    a = BiasFeedbackPanel()
    a.enable_check.setChecked(True)
    a.setpoint_spin.setValue(0.55)
    a.gate_lo_spin.setValue(120.0)
    a.gate_hi_spin.setValue(380.0)
    payload = a.prefs_dict()

    b = BiasFeedbackPanel()
    b.restore_prefs(payload)
    assert b.enable_check.isChecked() is True
    assert b.setpoint_spin.value() == pytest.approx(0.55)
    assert b.gate_lo_spin.value() == pytest.approx(120.0)
    assert b.gate_hi_spin.value() == pytest.approx(380.0)


def test_restore_prefs_tolerant_of_missing_keys(qapp):
    """Older prefs files won't have every key — restore should
    silently leave the missing fields at their construction defaults
    rather than raising."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    default_setpoint = panel.setpoint_spin.value()
    # Restore a payload with ONLY the enabled flag.
    panel.restore_prefs({"enabled": True})
    assert panel.enable_check.isChecked() is True
    # Setpoint unchanged.
    assert panel.setpoint_spin.value() == pytest.approx(default_setpoint)


def test_restore_prefs_ignores_bad_payload(qapp):
    """A non-dict payload (corrupt prefs file, schema drift) must
    not raise — partial-restore beats all-or-nothing."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    panel = BiasFeedbackPanel()
    panel.restore_prefs(None)  # type: ignore[arg-type]
    panel.restore_prefs("garbage")  # type: ignore[arg-type]
    panel.restore_prefs(42)  # type: ignore[arg-type]
    # Sanity: panel still works after the bad payloads.
    assert panel.enable_check.isChecked() is False


def test_restore_prefs_partial_on_bad_value_type(qapp):
    """A key with a bad value type (string where float expected)
    leaves THAT field at default but does NOT abort the rest of
    the restore."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    default_setpoint = panel.setpoint_spin.value()
    panel.restore_prefs({
        "enabled": True,
        "setpoint_v": "garbage",     # invalid — should be skipped
        "k_i": 0.15,                  # valid — should land
    })
    assert panel.enable_check.isChecked() is True
    assert panel.setpoint_spin.value() == pytest.approx(default_setpoint)
    assert panel.ki_spin.value() == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# ConnectionPanel — the INTERSTELLAR connector lives in Setup (below the
# oscilloscope) when the experimental feature is enabled.
# ---------------------------------------------------------------------------
def test_connection_panel_has_interstellar_connector_when_enabled(qapp):
    """With INTERSTELLAR enabled (the default when running from source),
    ConnectionPanel exposes ``self.bias_connector`` — the Connect /
    Disconnect control now lives in the Setup tab, below the
    oscilloscope, host-delegated to the panel's own driver facade.

    The DISABLED case (public build → no connector) is covered in
    ``tests/test_interstellar_feature_flag.py``."""
    from stimtest.gui.bias_panel import BiasConnector
    from stimtest.gui.connection_panel import ConnectionPanel
    conn = ConnectionPanel()
    assert hasattr(conn, "bias_connector"), (
        "ConnectionPanel should build the INTERSTELLAR connector when the "
        "feature is enabled (default from source).")
    assert isinstance(conn.bias_connector, BiasConnector)


def test_connection_panel_still_exposes_host_facade(qapp):
    """The bias UI moved per-tab, but ConnectionPanel still OWNS the
    driver — every per-tab connector talks to its facade methods +
    signals.  Make sure the facade surface is intact."""
    from stimtest.gui.connection_panel import ConnectionPanel
    conn = ConnectionPanel()
    assert hasattr(conn, "open_bias_module")
    assert hasattr(conn, "close_bias_module")
    assert hasattr(conn, "biasConnected")
    assert hasattr(conn, "biasDisconnected")
    assert hasattr(conn, "bias")  # mirror attribute
