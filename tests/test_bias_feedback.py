"""Tests for the closed-loop interpulse-bias feedback controller.

Pure-Python integration tests against in-process scope + bias
mocks — no hardware needed.  Verifies:

* Control law: error → bias adjustment with correct sign and gain
* Deadband: in-window iterations don't adjust
* Saturation: DAC clamp engages, saturated flag set
* V_mon sanity: out-of-window V_mon → skip
* NaN measurement: NaN → skip
* Exception safety: scope/bias raises → skip + note, no crash
* Convergence: repeated steps drive measured → setpoint
* arm/disarm: configures scope window + seeds setpoint
* Step counters: step_count + adjust_count track correctly
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

import pytest

from stimtest.experiments.bias_feedback import (
    BiasFeedbackConfig, BiasFeedbackController, BiasFeedbackStep,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------
@dataclass
class _MockScope:
    """Bare-bones scope stub exposing just measure_mean,
    gate_measurement_window, and clear_measurement_gating.

    ``eret_v`` / ``vmon_v`` are the values to return for the
    "eret" / "vmon" channels.  Tests set them per scenario.

    A callable can be assigned to ``eret_v`` (or ``vmon_v``) for
    per-call dynamic behavior — useful for the convergence test
    where E_ret moves as the bias changes.
    """
    eret_v: float = 0.30
    vmon_v: float = 0.0
    raise_on_measure: Optional[Exception] = None
    gated_windows: List[tuple] = field(default_factory=list)
    cleared: bool = False
    measure_calls: List[str] = field(default_factory=list)

    def measure_mean(self, channel: str) -> float:
        self.measure_calls.append(channel)
        if self.raise_on_measure is not None:
            raise self.raise_on_measure
        if channel in ("eret", "CH3"):
            v = self.eret_v
        elif channel in ("vmon", "CH1"):
            v = self.vmon_v
        else:
            return float("nan")
        return v(self) if callable(v) else v

    def gate_measurement_window(self, t_us_start: float, t_us_end: float):
        self.gated_windows.append((t_us_start, t_us_end))

    def clear_measurement_gating(self):
        self.cleared = True


@dataclass
class _MockBias:
    """Bare-bones bias-module stub: tracks programmed voltage and
    every set/get call.  Enable state is stored but the controller
    doesn't read it (controller doesn't own master enable)."""
    programmed_v: float = 0.0
    raise_on_set: Optional[Exception] = None
    raise_on_get: Optional[Exception] = None
    set_calls: List[float] = field(default_factory=list)
    get_calls: int = 0

    def set_bias_voltage(self, v: float) -> None:
        if self.raise_on_set is not None:
            raise self.raise_on_set
        self.set_calls.append(v)
        self.programmed_v = float(v)

    def get_bias_voltage(self) -> float:
        self.get_calls += 1
        if self.raise_on_get is not None:
            raise self.raise_on_get
        return self.programmed_v


def _make_controller(*, scope=None, bias=None, **cfg_overrides):
    """Build a controller against fresh mocks with sensible defaults."""
    scope = scope if scope is not None else _MockScope()
    bias = bias if bias is not None else _MockBias()
    cfg = BiasFeedbackConfig(
        setpoint_v=cfg_overrides.pop("setpoint_v", 0.30),
        **cfg_overrides,
    )
    return BiasFeedbackController(scope=scope, bias_module=bias, config=cfg), scope, bias


# ---------------------------------------------------------------------------
# arm / disarm
# ---------------------------------------------------------------------------
def test_arm_configures_scope_window():
    """arm() should place the cursors at the configured window."""
    ctrl, scope, bias = _make_controller(
        gating_window_us=(250.0, 400.0))
    ctrl.arm()
    assert scope.gated_windows == [(250.0, 400.0)]


def test_arm_seeds_bias_to_setpoint():
    """arm() should program the bias DAC to the setpoint as a
    starting point, so the loop starts from setpoint and corrects
    from there."""
    ctrl, scope, bias = _make_controller(setpoint_v=0.42)
    ctrl.arm()
    assert bias.programmed_v == pytest.approx(0.42)
    assert 0.42 in bias.set_calls


def test_disarm_clears_scope_gating():
    """disarm() should clear the gating so subsequent MEASU queries
    use the full acquisition window."""
    ctrl, scope, bias = _make_controller()
    ctrl.arm()
    ctrl.disarm()
    assert scope.cleared is True


# ---------------------------------------------------------------------------
# step — control law
# ---------------------------------------------------------------------------
def test_step_in_deadband_does_not_adjust():
    """When |error| ≤ tolerance, no bias change is made."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.30, tolerance_v=5e-3)
    bias.programmed_v = 0.30
    scope.eret_v = 0.302  # 2 mV error, within ±5 mV deadband
    step = ctrl.step()

    assert step.in_deadband is True
    assert step.skipped is True
    assert step.bias_v_after == pytest.approx(bias.programmed_v)
    assert bias.set_calls == []  # no set_bias_voltage call


def test_step_positive_error_lowers_bias():
    """When measured > setpoint, the bias should be REDUCED
    (negative correction)."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.30, tolerance_v=5e-3, k_i=0.1)
    bias.programmed_v = 0.30
    scope.eret_v = 0.40   # +100 mV error
    step = ctrl.step()

    assert step.in_deadband is False
    assert step.skipped is False
    # bias_new = 0.30 - 0.1 * 0.10 = 0.29
    assert step.bias_v_after == pytest.approx(0.29)
    assert bias.programmed_v == pytest.approx(0.29)
    assert step.error_v == pytest.approx(0.10)


def test_step_negative_error_raises_bias():
    """When measured < setpoint, the bias should be INCREASED
    (positive correction)."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.30, tolerance_v=5e-3, k_i=0.1)
    bias.programmed_v = 0.30
    scope.eret_v = 0.20   # -100 mV error
    step = ctrl.step()

    # bias_new = 0.30 - 0.1 * (-0.10) = 0.31
    assert step.bias_v_after == pytest.approx(0.31)


def test_step_clamps_at_upper_rail():
    """Update that would exceed bias_max_v gets clamped, saturated=True."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.0, tolerance_v=1e-3, k_i=1.0,
        bias_min_v=-5.0, bias_max_v=5.0)
    bias.programmed_v = 4.9
    scope.eret_v = -1.0  # error = -1 → bias += 1 → 5.9, clamped to 5.0
    step = ctrl.step()

    assert step.saturated is True
    assert step.bias_v_after == pytest.approx(5.0)
    assert bias.programmed_v == pytest.approx(5.0)


def test_step_clamps_at_lower_rail():
    """Update that would go below bias_min_v gets clamped."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.0, tolerance_v=1e-3, k_i=1.0,
        bias_min_v=-5.0, bias_max_v=5.0)
    bias.programmed_v = -4.9
    scope.eret_v = 1.0  # error = +1 → bias -= 1 → -5.9, clamped to -5.0
    step = ctrl.step()

    assert step.saturated is True
    assert step.bias_v_after == pytest.approx(-5.0)


# ---------------------------------------------------------------------------
# step — safety gates
# ---------------------------------------------------------------------------
def test_step_nan_measurement_is_skipped():
    """NaN E_ret → skip, no bias change, descriptive note."""
    ctrl, scope, bias = _make_controller()
    bias.programmed_v = 0.30
    scope.eret_v = float("nan")
    step = ctrl.step()

    assert step.skipped is True
    assert math.isnan(step.measured_v)
    assert step.bias_v_after == pytest.approx(0.30)
    assert bias.set_calls == []
    assert "NaN" in step.note or "nan" in step.note.lower()


def test_step_vmon_sanity_failure_is_skipped():
    """V_mon outside the threshold → skip + flag vmon_sane=False."""
    ctrl, scope, bias = _make_controller(
        vmon_sanity_threshold_v=0.05)  # 50 mV
    bias.programmed_v = 0.30
    scope.eret_v = 0.40       # would normally trigger an adjust
    scope.vmon_v = 0.100       # 100 mV — fails sanity (> 50 mV)
    step = ctrl.step()

    assert step.skipped is True
    assert step.vmon_sane is False
    assert bias.set_calls == []  # no bias change despite E_ret error
    assert "sanity" in step.note.lower()


def test_step_vmon_within_threshold_proceeds_normally():
    """V_mon within threshold → does NOT block adjustment."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.30, k_i=0.1,
        vmon_sanity_threshold_v=0.05)
    bias.programmed_v = 0.30
    scope.eret_v = 0.40
    scope.vmon_v = 0.001  # 1 mV — well within 50 mV threshold
    step = ctrl.step()

    assert step.vmon_sane is True
    assert step.skipped is False
    assert step.bias_v_after == pytest.approx(0.29)


def test_step_vmon_check_can_be_disabled():
    """Empty vmon_channel → no V_mon read at all, always sane."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.30, k_i=0.1, vmon_channel="")
    bias.programmed_v = 0.30
    scope.eret_v = 0.40
    # Even with a wildly out-of-range V_mon, vmon read should
    # be skipped entirely.
    scope.vmon_v = 99.0
    step = ctrl.step()

    assert step.vmon_sane is True
    # And measure_mean was only called for E_ret, never V_mon.
    assert scope.measure_calls == ["eret"]


def test_step_vmon_nan_is_treated_as_sane():
    """NaN V_mon (scope can't measure yet) is INCONCLUSIVE, not
    a sanity failure — controller proceeds to E_ret check."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.30, k_i=0.1)
    bias.programmed_v = 0.30
    scope.eret_v = 0.40
    scope.vmon_v = float("nan")
    step = ctrl.step()

    assert step.vmon_sane is True
    assert step.skipped is False


# ---------------------------------------------------------------------------
# step — exception handling
# ---------------------------------------------------------------------------
def test_step_scope_exception_is_skipped_not_raised():
    """measure_mean raises → controller returns skipped, doesn't crash."""
    ctrl, scope, bias = _make_controller()
    bias.programmed_v = 0.30
    scope.raise_on_measure = RuntimeError("VISA timeout")
    step = ctrl.step()

    assert step.skipped is True
    assert step.bias_v_after == pytest.approx(0.30)
    assert "VISA timeout" in step.note or "RuntimeError" in step.note


def test_step_bias_set_exception_is_skipped_not_raised():
    """set_bias_voltage raises → controller returns skipped record;
    bias_v_after reflects the pre-write value, not the attempted one."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.30, k_i=0.1, vmon_channel="")
    bias.programmed_v = 0.30
    scope.eret_v = 0.40
    bias.raise_on_set = RuntimeError("serial port closed")
    step = ctrl.step()

    assert step.skipped is True
    assert step.bias_v_after == pytest.approx(0.30)
    assert "serial port" in step.note or "RuntimeError" in step.note


def test_step_bias_get_exception_falls_back_to_setpoint():
    """get_bias_voltage raises → controller uses setpoint as a
    best-guess for bias_v_before; doesn't crash."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.42, vmon_channel="")
    scope.eret_v = 0.42  # in deadband — but the get is what we're testing
    bias.raise_on_get = RuntimeError("bias_module not open")
    step = ctrl.step()

    # bias_v_before should be the setpoint fallback, not crash.
    assert step.bias_v_before == pytest.approx(0.42)


# ---------------------------------------------------------------------------
# Convergence
# ---------------------------------------------------------------------------
def test_repeated_steps_converge_to_setpoint():
    """Integration test: with a model where measured ≈ bias
    (electrode tracks the bias one-for-one), the loop should
    drive measured to setpoint in ~10–20 iterations."""
    setpoint = 0.30
    ctrl, scope, bias = _make_controller(
        setpoint_v=setpoint, tolerance_v=1e-3, k_i=0.5,
        vmon_channel="")
    # Start far from setpoint.
    bias.programmed_v = 0.10
    # Model: measured_v tracks bias.programmed_v plus a small
    # constant drift offset (the thing the loop is supposed to
    # remove).
    drift = 0.05
    scope.eret_v = lambda s: bias.programmed_v + drift

    # Run until deadband or 50 iterations.
    last_step = None
    for _ in range(50):
        last_step = ctrl.step()
        if last_step.in_deadband:
            break

    assert last_step is not None
    assert last_step.in_deadband is True, (
        f"controller didn't converge after 50 steps; "
        f"last measured={last_step.measured_v:.6f}, "
        f"last bias={last_step.bias_v_after:.6f}")
    # Final measured should be within deadband of setpoint.
    assert abs(last_step.measured_v - setpoint) <= 1e-3


def test_step_counters_track_correctly():
    """step_count counts every step; adjust_count only counts
    iterations that adjusted the bias."""
    ctrl, scope, bias = _make_controller(
        setpoint_v=0.30, tolerance_v=5e-3, k_i=0.1, vmon_channel="")
    bias.programmed_v = 0.30

    # Iteration 1: in deadband.
    scope.eret_v = 0.301
    ctrl.step()
    assert ctrl.step_count == 1
    assert ctrl.adjust_count == 0

    # Iteration 2: outside deadband → adjust.
    scope.eret_v = 0.40
    ctrl.step()
    assert ctrl.step_count == 2
    assert ctrl.adjust_count == 1

    # Iteration 3: NaN → skip, no adjust.
    scope.eret_v = float("nan")
    ctrl.step()
    assert ctrl.step_count == 3
    assert ctrl.adjust_count == 1
