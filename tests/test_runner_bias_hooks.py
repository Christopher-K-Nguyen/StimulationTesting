"""Tests for Task #43 part 1: ExperimentRunner closed-loop hooks.

ExperimentRunner gains three new methods + two attributes that
runners (SP / LP / VT / PS) call at safe iteration points:

* ``bias_controller`` — optional BiasFeedbackController, set by the
  GUI worker before ``run()``.
* ``bias_armed`` — set by ``arm_bias_feedback`` / ``disarm_bias_feedback``.
* ``arm_bias_feedback()`` — returns True on success, False otherwise;
  swallows controller exceptions and disables the loop on arm failure.
* ``disarm_bias_feedback()`` — idempotent + safe in finally blocks.
* ``bias_step_if_armed()`` — cheap no-op when unarmed; emits a tagged
  ``[bias-step]`` log event otherwise.

The infrastructure is built; the per-runner wiring (where these
hooks fire in VT / SP / LP / PS) lands in follow-up commits.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers — concrete subclass + minimal session stub
# ---------------------------------------------------------------------------
def _make_runner():
    """Build a no-op concrete ExperimentRunner subclass with stub
    hardware so we can exercise the bias hooks without touching real
    scope / stim drivers."""
    from stimtest.experiments.base import ExperimentRunner
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern, Phase

    class _NoOpRunner(ExperimentRunner):
        def run(self):
            pass  # not exercised in these tests

    array = ElectrodeArray.utah_4x4()
    pat = PulsePattern.biphasic(amplitude_ua=10.0, rate_hz=100.0)
    del Phase  # silence the unused-import warning
    test = TestParameters(
        experiment="SP",
        pattern=pat,
        configuration=Configuration.monopolar(1),
        array=array,
    )
    session = Session(notebook="", subject="", test=test)

    stim = MagicMock()
    stim.info = None
    scope = MagicMock()
    scope.info = None
    scope.channel_aliases = {}

    return _NoOpRunner(session, stim, scope)


class _ControllerStub:
    """Match the BiasFeedbackController interface without the scope /
    bias-module dependencies."""

    def __init__(self, *, arm_raises=False, step_raises=False,
                 step_result=None):
        self.arm_calls = 0
        self.disarm_calls = 0
        self.step_calls = 0
        self.arm_raises = arm_raises
        self.step_raises = step_raises
        self.step_result = step_result
        # Lightweight config for arm-success logging.
        from stimtest.experiments.bias_feedback import BiasFeedbackConfig
        self.config = BiasFeedbackConfig(
            setpoint_v=0.30,
            tolerance_v=0.005,
            k_i=0.10,
            gating_window_us=(300.0, 450.0),
        )

    def arm(self):
        self.arm_calls += 1
        if self.arm_raises:
            raise RuntimeError("arm failed")

    def disarm(self):
        self.disarm_calls += 1

    def step(self):
        self.step_calls += 1
        if self.step_raises:
            raise RuntimeError("step failed")
        return self.step_result


def _make_step_result(*, saturated=False, vmon_sane=True, error_mv=0.0):
    from stimtest.experiments.bias_feedback import BiasFeedbackStep
    return BiasFeedbackStep(
        measured_v=0.30 + error_mv * 1e-3,
        error_v=error_mv * 1e-3,
        in_deadband=False,
        bias_v_before=0.30,
        bias_v_after=0.30 - 0.0005,
        saturated=saturated,
        vmon_sane=vmon_sane,
        skipped=False,
        note="",
    )


# ---------------------------------------------------------------------------
# Default state
# ---------------------------------------------------------------------------
def test_runner_starts_with_no_bias_controller():
    """ExperimentRunner.__init__ leaves bias_controller unset (None)
    and bias_armed False — feedback is opt-in by construction."""
    r = _make_runner()
    assert r.bias_controller is None
    assert r.bias_armed is False


# ---------------------------------------------------------------------------
# arm_bias_feedback
# ---------------------------------------------------------------------------
def test_arm_no_op_without_controller():
    """No controller → arm returns False, bias_armed stays False,
    no events emitted."""
    r = _make_runner()
    events = []
    r.subscribe(events.append)
    armed = r.arm_bias_feedback()
    assert armed is False
    assert r.bias_armed is False
    # No-op shouldn't fire any log events.
    log_events = [e for e in events if e.kind == "log"]
    assert log_events == []


def test_arm_success_sets_flag_and_logs():
    r = _make_runner()
    ctrl = _ControllerStub()
    r.bias_controller = ctrl
    events = []
    r.subscribe(events.append)
    armed = r.arm_bias_feedback()
    assert armed is True
    assert r.bias_armed is True
    assert ctrl.arm_calls == 1
    log_events = [e for e in events if e.kind == "log"]
    assert len(log_events) == 1
    assert "[bias] armed" in log_events[0].message
    assert "setpoint=0.300" in log_events[0].message


def test_arm_failure_disables_loop_for_rest_of_run():
    """If arm() raises, the runner logs the failure, drops the
    controller reference, and stays unarmed — subsequent
    bias_step_if_armed calls become no-ops."""
    r = _make_runner()
    ctrl = _ControllerStub(arm_raises=True)
    r.bias_controller = ctrl
    events = []
    r.subscribe(events.append)
    armed = r.arm_bias_feedback()
    assert armed is False
    assert r.bias_armed is False
    assert r.bias_controller is None  # dropped
    log_events = [e for e in events if e.kind == "log"]
    assert any("arm() raised" in e.message for e in log_events)
    # Subsequent step call is a no-op.
    r.bias_step_if_armed()
    # No bias-step log events emitted.
    step_events = [e for e in events if "[bias-step]" in e.message]
    assert step_events == []


# ---------------------------------------------------------------------------
# disarm_bias_feedback
# ---------------------------------------------------------------------------
def test_disarm_no_op_without_controller():
    """No controller → disarm is a no-op + safe (doesn't raise)."""
    r = _make_runner()
    r.disarm_bias_feedback()  # must not raise


def test_disarm_after_arm():
    r = _make_runner()
    ctrl = _ControllerStub()
    r.bias_controller = ctrl
    r.arm_bias_feedback()
    r.disarm_bias_feedback()
    assert ctrl.disarm_calls == 1
    assert r.bias_armed is False


def test_disarm_idempotent():
    """Multiple disarm calls are safe (finally-block scenarios may
    call it more than once during cleanup)."""
    r = _make_runner()
    ctrl = _ControllerStub()
    r.bias_controller = ctrl
    r.arm_bias_feedback()
    r.disarm_bias_feedback()
    r.disarm_bias_feedback()
    r.disarm_bias_feedback()
    # disarm() is called per request — the controller decides
    # idempotency (it does: see bias_feedback.disarm()).
    assert ctrl.disarm_calls == 3
    assert r.bias_armed is False


def test_disarm_swallows_controller_failure():
    """A teardown SCPI failure inside the controller's disarm()
    must NOT escape — letting it would mask the run's actual error
    in the GUI's "experiment failed" dialog."""
    r = _make_runner()
    ctrl = _ControllerStub()
    ctrl.disarm = MagicMock(side_effect=RuntimeError("teardown explode"))
    r.bias_controller = ctrl
    r.bias_armed = True
    # Must not raise.
    r.disarm_bias_feedback()
    assert r.bias_armed is False


# ---------------------------------------------------------------------------
# bias_step_if_armed
# ---------------------------------------------------------------------------
def test_step_no_op_when_unarmed():
    """The fast path: cheap early-out when bias_armed is False so
    runners can call this unconditionally in their inner loop."""
    r = _make_runner()
    ctrl = _ControllerStub(step_result=_make_step_result())
    r.bias_controller = ctrl
    # Not armed yet.
    r.bias_step_if_armed()
    assert ctrl.step_calls == 0


def test_step_emits_tagged_log_on_success():
    r = _make_runner()
    ctrl = _ControllerStub(step_result=_make_step_result(error_mv=+12.0))
    r.bias_controller = ctrl
    r.arm_bias_feedback()
    events = []
    r.subscribe(events.append)
    r.bias_step_if_armed()
    assert ctrl.step_calls == 1
    step_logs = [e for e in events if "[bias-step]" in e.message]
    assert len(step_logs) == 1
    msg = step_logs[0].message
    assert "measured=" in msg
    assert "error=" in msg
    assert "bias=" in msg


def test_step_log_flags_saturation():
    r = _make_runner()
    ctrl = _ControllerStub(step_result=_make_step_result(saturated=True))
    r.bias_controller = ctrl
    r.arm_bias_feedback()
    events = []
    r.subscribe(events.append)
    r.bias_step_if_armed()
    step_logs = [e for e in events if "[bias-step]" in e.message]
    assert any("SATURATED" in e.message for e in step_logs)


def test_step_log_flags_vmon_insanity():
    r = _make_runner()
    ctrl = _ControllerStub(step_result=_make_step_result(vmon_sane=False))
    r.bias_controller = ctrl
    r.arm_bias_feedback()
    events = []
    r.subscribe(events.append)
    r.bias_step_if_armed()
    step_logs = [e for e in events if "[bias-step]" in e.message]
    assert any("V_mon insane" in e.message for e in step_logs)


def test_step_swallows_outer_exception():
    """If step() raises at the outer level (e.g. controller-side
    bug, not a measurement failure), runner logs and continues —
    the loop must survive a transient controller error so the
    underlying experiment data still lands."""
    r = _make_runner()
    ctrl = _ControllerStub(step_raises=True)
    r.bias_controller = ctrl
    r.arm_bias_feedback()
    events = []
    r.subscribe(events.append)
    # Must not raise.
    r.bias_step_if_armed()
    # Outer error landed in the log.
    err_logs = [e for e in events
                if "outer-level" in e.message]
    assert len(err_logs) == 1
    # bias_armed still True — a one-off step failure doesn't disarm
    # the whole loop (next iteration may succeed).
    assert r.bias_armed is True


# ---------------------------------------------------------------------------
# Full lifecycle smoke
# ---------------------------------------------------------------------------
def test_full_lifecycle_arm_step_step_disarm():
    """End-to-end: arm → step → step → disarm.  Verifies the
    counter advances + bias_armed flips appropriately."""
    r = _make_runner()
    ctrl = _ControllerStub(step_result=_make_step_result())
    r.bias_controller = ctrl
    assert r.arm_bias_feedback() is True
    assert r.bias_armed is True
    r.bias_step_if_armed()
    r.bias_step_if_armed()
    assert ctrl.step_calls == 2
    r.disarm_bias_feedback()
    assert r.bias_armed is False
    assert ctrl.disarm_calls == 1
    # Subsequent step is a no-op (disarmed).
    r.bias_step_if_armed()
    assert ctrl.step_calls == 2
