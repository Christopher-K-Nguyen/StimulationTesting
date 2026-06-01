"""Tests for Task #43 part 2: SP runner closed-loop wiring + GUI
attach helper.

Pins:

* ShortPulsingExperiment calls ``arm_bias_feedback`` after
  ``apply_default_scope_view`` and before ``set_monitor_channel``
  (so the controller's gating-window SCPI writes don't get
  clobbered by the scope-view defaults).
* ShortPulsingExperiment calls ``bias_step_if_armed`` after each
  capture.
* ShortPulsingExperiment calls ``disarm_bias_feedback`` in the
  finally block BEFORE ``stop_all`` (so disarm order mirrors
  arm order).
* ``_BaseExperimentTab._attach_bias_controller_to_runner`` builds
  a controller when panel's master Enable is on AND a bias driver
  is open in the host — and short-circuits cleanly when either
  precondition fails.

Source-level tests (rather than running the actual runner) avoid
the SimulatedStimulator / SimulatedOscilloscope plumbing for the
ordering checks; an integration-style smoke uses MagicMock-based
fakes for the bias hooks.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Source-level call-site checks
# ---------------------------------------------------------------------------
def _read_sp_source() -> str:
    return (Path(__file__).resolve().parent.parent
            / "stimtest/experiments/short_pulsing.py").read_text(
                encoding="utf-8")


def test_sp_calls_arm_after_scope_view_before_start_all():
    """``arm_bias_feedback`` must land AFTER
    ``apply_default_scope_view`` (so the controller's MEASUrement
    gating writes aren't clobbered) and BEFORE ``start_all`` (so the
    bias DAC is preloaded with the setpoint before the stim fires)."""
    src = _read_sp_source()
    # All three landmarks present
    assert "apply_default_scope_view" in src
    assert "arm_bias_feedback" in src
    assert "start_all" in src
    # Ordering check: arm BETWEEN scope view + start_all.
    asv = src.index("apply_default_scope_view")
    arm = src.index("arm_bias_feedback")
    start_all = src.index("start_all")
    assert asv < arm < start_all, (
        f"Expected apply_default_scope_view ({asv}) < arm_bias_feedback "
        f"({arm}) < start_all ({start_all})")


def test_sp_calls_bias_step_after_capture_emit():
    """``bias_step_if_armed`` should land AFTER the capture event so
    the GUI's status badge updates land in the log AFTER the
    just-emitted metrics row."""
    src = _read_sp_source()
    assert "bias_step_if_armed" in src
    # Find the bias_step line; the line immediately before it (after
    # the next_capture_at advance) should be the capture emit OR a
    # comment block.  Pin the rough order by checking that the
    # FIRST occurrence of ``bias_step_if_armed`` follows the FIRST
    # ``kind="capture"`` emit.
    capture_emit = src.index('kind="capture"')
    bias_step = src.index("bias_step_if_armed")
    assert capture_emit < bias_step


def test_sp_disarms_before_stop_all_in_finally():
    """``disarm_bias_feedback`` should fire BEFORE ``stop_all`` in
    the finally block — mirrors arm order (arm after scope view,
    disarm before stim stop)."""
    src = _read_sp_source()
    # finally block has both
    finally_block = src.split("finally:")[1]
    assert "disarm_bias_feedback" in finally_block
    # disarm appears before stop_all in the finally block
    disarm_pos = finally_block.index("disarm_bias_feedback")
    stop_all_pos = finally_block.index("stop_all")
    assert disarm_pos < stop_all_pos


# ---------------------------------------------------------------------------
# _attach_bias_controller_to_runner — GUI-side helper
# ---------------------------------------------------------------------------
def _make_tab_with_bias_panel(qapp, *, enabled, has_driver):
    """Build a minimal tab stub exposing exactly the surface used by
    _attach_bias_controller_to_runner.

    Doesn't construct a real _BaseExperimentTab — that needs a full
    QApplication + 1000-line scaffold.  The helper only touches
    ``self._bias_feedback_panel`` + ``self._bias_host`` +
    ``self.log_pane`` + ``self._scope``.
    """
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    panel = BiasFeedbackPanel()
    panel.enable_check.setChecked(enabled)

    class _LogPaneStub:
        def __init__(self):
            self.lines = []
        def log(self, msg):
            self.lines.append(msg)

    class _HostStub:
        def __init__(self, has_driver):
            self.bias = MagicMock() if has_driver else None

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = panel
    tab._bias_host = _HostStub(has_driver)
    tab.log_pane = _LogPaneStub()
    tab._scope = MagicMock()
    return tab, panel


@pytest.fixture(scope="module")
def qapp():
    import sys
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    yield app


def test_attach_skipped_when_master_enable_off(qapp):
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    tab, _panel = _make_tab_with_bias_panel(
        qapp, enabled=False, has_driver=True)
    runner = MagicMock()
    runner.bias_controller = None

    _BaseExperimentTab._attach_bias_controller_to_runner(tab, runner)

    assert runner.bias_controller is None
    assert any("master Enable is off" in line
               for line in tab.log_pane.lines)


def test_attach_skipped_when_no_bias_driver_open(qapp):
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    tab, _panel = _make_tab_with_bias_panel(
        qapp, enabled=True, has_driver=False)
    runner = MagicMock()
    runner.bias_controller = None

    _BaseExperimentTab._attach_bias_controller_to_runner(tab, runner)

    assert runner.bias_controller is None
    assert any("no bias driver is open" in line
               for line in tab.log_pane.lines)


def test_attach_constructs_controller_when_both_ready(qapp):
    """Master Enable on + bias driver open → controller is constructed
    and pushed onto the runner."""
    from stimtest.experiments.bias_feedback import BiasFeedbackController
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    tab, panel = _make_tab_with_bias_panel(
        qapp, enabled=True, has_driver=True)
    panel.setpoint_spin.setValue(0.45)
    panel.tolerance_spin.setValue(10.0)
    panel.ki_spin.setValue(0.20)
    runner = MagicMock()
    runner.bias_controller = None

    _BaseExperimentTab._attach_bias_controller_to_runner(tab, runner)

    assert isinstance(runner.bias_controller, BiasFeedbackController)
    cfg = runner.bias_controller.config
    assert cfg.setpoint_v == pytest.approx(0.45)
    assert cfg.tolerance_v == pytest.approx(0.010)
    assert cfg.k_i == pytest.approx(0.20)
    # Log line confirms the operator's intent.
    assert any("closed-loop feedback enabled" in line
               for line in tab.log_pane.lines)


def test_attach_no_op_when_panel_missing(qapp):
    """Test stubs / partial construction → no panel attribute → no
    log lines, no controller, no exception."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    class _Stub:
        log_pane = MagicMock()

    tab = _Stub()
    runner = MagicMock()
    runner.bias_controller = None

    _BaseExperimentTab._attach_bias_controller_to_runner(tab, runner)

    assert runner.bias_controller is None
    tab.log_pane.log.assert_not_called()


def test_attach_logs_when_feedback_config_raises(qapp):
    """A bad panel state shouldn't propagate — log + skip."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    class _ExplodingPanel:
        def feedback_config(self):
            raise RuntimeError("kaboom")

    class _LogPaneStub:
        def __init__(self):
            self.lines = []
        def log(self, msg):
            self.lines.append(msg)

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = _ExplodingPanel()
    tab._bias_host = None
    tab.log_pane = _LogPaneStub()
    tab._scope = MagicMock()
    runner = MagicMock()
    runner.bias_controller = None

    _BaseExperimentTab._attach_bias_controller_to_runner(tab, runner)

    assert runner.bias_controller is None
    assert any("feedback_config() raised" in line
               for line in tab.log_pane.lines)


# ---------------------------------------------------------------------------
# _on_bias_step_log — parses runner log lines into status badge updates
# ---------------------------------------------------------------------------
def test_on_bias_step_log_parses_normal_step(qapp):
    """A normal ``[bias-step] measured=... error=... bias=...`` line
    should land on the panel as an ARMED status."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    panel = BiasFeedbackPanel()
    panel.enable_check.setChecked(True)

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = panel

    line = ("[bias-step] measured=+0.3052 V, error=+5.20 mV, "
            "bias=+0.2995 V")
    _BaseExperimentTab._on_bias_step_log(tab, line)
    assert "ARMED" in panel.status_badge.text()


def test_on_bias_step_log_parses_saturated(qapp):
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    panel = BiasFeedbackPanel()
    panel.enable_check.setChecked(True)

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = panel
    line = ("[bias-step] measured=+0.5000 V, error=+200.00 mV, "
            "bias=+5.0000 V ⚠ SATURATED")
    _BaseExperimentTab._on_bias_step_log(tab, line)
    assert "SATURATED" in panel.status_badge.text()


def test_on_bias_step_log_parses_vmon_insane(qapp):
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    panel = BiasFeedbackPanel()
    panel.enable_check.setChecked(True)

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = panel
    line = ("[bias-step] measured=+nan V, error=+nan mV, "
            "bias=+0.3000 V ⚠ V_mon insane")
    # Note: NaN won't actually match the regex (it requires
    # [-+0-9.eE]+); the test isn't about NaN handling but about
    # the FLAGS path firing.
    line = ("[bias-step] measured=+0.3000 V, error=+0.00 mV, "
            "bias=+0.3000 V ⚠ V_mon insane")
    _BaseExperimentTab._on_bias_step_log(tab, line)
    assert "V_MON" in panel.status_badge.text()


def test_on_bias_step_log_ignores_non_bias_lines(qapp):
    """Non-matching log lines (scope SCPI, stim events, etc.) must
    not change the badge state."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    panel = BiasFeedbackPanel()
    initial = panel.status_badge.text()

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = panel
    for noise in (
        "[scope] > CH1:SCAle?   < 22.6e-3   (0.83 s)",
        "stim: started channel 5",
        "[bias] closed-loop feedback enabled: setpoint=0.300 V",
        "",
        "[bias-step] malformed",
    ):
        _BaseExperimentTab._on_bias_step_log(tab, noise)
    # Badge unchanged.
    assert panel.status_badge.text() == initial


def test_on_bias_step_log_safe_without_panel(qapp):
    """If the tab has no _bias_feedback_panel (test stubs / partial
    construction), the slot must not raise."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    class _Stub:
        pass

    tab = _Stub()
    # No _bias_feedback_panel.
    _BaseExperimentTab._on_bias_step_log(
        tab, "[bias-step] measured=+0.30 V, error=+0.00 mV, bias=+0.30 V")
