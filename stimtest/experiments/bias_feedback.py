"""Closed-loop interpulse-bias feedback controller (host-side).

Reads the return-electrode potential (E_ret mean over the interpulse
window) from the scope, compares to a configured setpoint, and
adjusts the STM32 bias-module's programmed voltage to drive the
measured value toward the setpoint.  Host-side implementation per
the decision logged earlier in the project: STM32 stays a dumb DAC,
PULSAR runs the loop at ~10–20 Hz between scope captures.

Architecture
------------
Pure Python.  No Qt.  No direct hardware I/O — everything goes
through the abstract :class:`Oscilloscope` and :class:`BiasModule`
interfaces, so the controller can be unit-tested against in-process
mocks without a Nucleo or scope on the bench.

Control law (per design discussion)
-----------------------------------
**Integral-only with deadband.**  Each step:

    error = measured_v - setpoint_v
    if |error| <= tolerance_v:
        no adjustment (within deadband)
    else:
        bias_new = bias_old - k_i * error
        bias_new = clamp(bias_new, [bias_min_v, bias_max_v])

The discrete update ``bias_new = bias_old - k_i * error`` is
mathematically integral control: repeated application accumulates
``-k_i * error`` increments over time, which is a discrete integral
of the error signal.  ``k_i`` is dimensionless per step (V_bias /
V_error per iteration).  At ``k_i = 0.1`` each step removes ~10 %
of the error, so the loop converges to setpoint in ~10 steps —
plenty fast at our 10–20 Hz target, no overshoot for the typical
electrochemical drift time constant (seconds to minutes).

Sign convention
---------------
If ``measured > setpoint`` (E_ret is HIGHER than desired), the
controller LOWERS the bias to pull it down.  Equivalently:
``bias_new = bias_old - k_i * (measured - setpoint)``.  Positive
error → negative bias adjustment.  Assumes the bias module drives
the return electrode through a low-impedance path so that, in
equilibrium, ``V_bias ≈ E_ret``.

Safety / sanity gates
---------------------
* **NaN measurement** (scope returned no value — channel off,
  acquisition empty, SCPI error) → iteration is **skipped**.  No
  bias adjustment; logged with the reason.
* **V_mon sanity check** (per the "V_mon = 0 during interpulse"
  insight): if the bias is supposedly engaged and balanced, V_mon
  should be near zero between pulses.  A non-zero V_mon means
  unbalanced bias delivery or a broken hardware path — the E_ret
  reading is suspect.  Iteration is skipped + logged.  Threshold
  configurable; default 50 mV.
* **DAC saturation clamp**: the bias DAC's range
  ``[bias_min_v, bias_max_v]`` is hardware-bounded (typ. ±5 V).
  Updates are clamped at the rails; ``saturated=True`` in the
  step result so the loop's caller can surface a warning when
  the controller is "asking for more" than the hardware can give.

The controller does NOT own the bias module's master enable
(``BIAS:ENABle ON/OFF``) — the caller (experiment runner) is
responsible for arming + disarming the bias module around the
experiment lifecycle.  The controller only adjusts the
PROGRAMMED voltage; the STM32 decides when to drive it based
on the TTL trigger from PlexStim.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass
class BiasFeedbackConfig:
    """All knobs for the feedback loop in one immutable struct.

    Construct once at experiment start; pass to
    :class:`BiasFeedbackController`.  Hot-swap of config at runtime
    is not supported — make a new controller if the setpoint
    changes mid-run.
    """

    #: Target return-electrode potential (V).  The loop drives
    #: ``measured_v`` toward this value.
    setpoint_v: float

    #: ±deadband around the setpoint (V).  Errors within this
    #: window do not trigger bias adjustments.  Default ±5 mV per
    #: the project's interpulse-bias tolerance spec.
    tolerance_v: float = 5e-3

    #: Integral gain (dimensionless per step).  Larger values
    #: converge faster but risk oscillation around the deadband.
    #: 0.1 = each step removes ~10 % of the error.
    k_i: float = 0.1

    #: DAC saturation rails (V).  Defaults match a typical ±5 V
    #: bipolar DAC output stage; adjust per actual hardware.
    bias_min_v: float = -5.0
    bias_max_v: float = 5.0

    #: V_mon sanity threshold (V).  If |V_mon| over the gated
    #: interpulse window exceeds this, the bias is presumed
    #: unbalanced / broken and the iteration is skipped.  Set to
    #: ``float('inf')`` to disable the gate.  Default 50 mV is a
    #: loose check — real wiring should keep V_mon below ~5 mV.
    vmon_sanity_threshold_v: float = 0.05

    #: Scope-side gated measurement window (µs from trigger) over
    #: which to compute the channel mean.  Should bracket the
    #: stable portion of the interpulse interval (i.e., after the
    #: post-pulse RC discharge settles, before the next pulse).
    #: Caller picks based on pulse rate + pulse width; default
    #: (300, 450) µs suits the standard ~200 µs biphasic at 100 Hz.
    gating_window_us: Tuple[float, float] = (300.0, 450.0)

    #: Scope channel alias to read E_ret from.  Resolved via the
    #: scope's ``channel_aliases`` map (so callers pass the logical
    #: name "eret" rather than "CH3").
    channel: str = "eret"

    #: Scope channel alias for the V_mon sanity check.  Same
    #: resolution as ``channel``.  Pass an empty string to skip
    #: V_mon reads entirely (controller will set vmon_sane=True
    #: unconditionally and not consume a measure_mean round-trip).
    vmon_channel: str = "vmon"


@dataclass
class BiasFeedbackStep:
    """One iteration's outcome — diagnostic record for logging
    and the live status indicator.

    Captured for every :meth:`BiasFeedbackController.step` call,
    including skipped iterations.  The runner can log these to the
    session file for post-hoc analysis of how the loop behaved.
    """

    #: E_ret mean over the gated window (V).  NaN if the scope
    #: returned no measurement.
    measured_v: float

    #: ``measured_v - setpoint_v`` (V).  NaN propagates from
    #: measured_v.
    error_v: float

    #: True when |error| ≤ tolerance (no bias change made).
    in_deadband: bool

    #: Bias voltage BEFORE this iteration's potential update (V).
    bias_v_before: float

    #: Bias voltage AFTER this iteration (V).  Equals
    #: ``bias_v_before`` when in_deadband / skipped; otherwise
    #: equals the clamped result of the integral update.
    bias_v_after: float

    #: True when the integral update would have moved the bias
    #: outside [bias_min_v, bias_max_v] and was clamped.
    saturated: bool

    #: V_mon sanity check outcome.  True when V_mon was within
    #: threshold OR when vmon_channel was disabled.  False means
    #: the iteration was skipped due to a sanity failure.
    vmon_sane: bool

    #: True when the iteration didn't adjust the bias (any of:
    #: NaN measurement, V_mon insanity, in-deadband).
    skipped: bool

    #: Human-readable explanation of what happened — handy for
    #: surfacing in the LogPane or in a session-file trace.
    note: str = ""


class BiasFeedbackController:
    """Host-side integral-with-deadband closed-loop bias controller.

    Reads E_ret from the scope (via gated MEAN measurement),
    adjusts the STM32 bias DAC.  See module docstring for the
    control law and safety gates.

    Usage::

        controller = BiasFeedbackController(
            scope=conn.scope, bias_module=conn.bias,
            config=BiasFeedbackConfig(setpoint_v=0.30))
        controller.arm()  # set scope window + initial bias voltage
        # Inside the experiment runner's loop:
        while running:
            step = controller.step()
            if step.saturated:
                log.warning(f"bias saturated: {step.bias_v_after} V")
            # ... capture, etc.

    The controller does not start/stop the bias module's master
    enable — caller decides when to engage the DAC output via
    ``bias_module.set_bias_enabled(True/False)`` around the run.
    """

    def __init__(self, *, scope, bias_module,
                 config: BiasFeedbackConfig):
        self.scope = scope
        self.bias_module = bias_module
        self.config = config

        #: Running tally of steps that have been executed (across
        #: all categories — skipped, in-deadband, and adjusted).
        #: Useful for "ran for N iterations" diagnostics.
        self.step_count: int = 0

        #: Running tally of iterations that ACTUALLY adjusted the
        #: bias voltage (not skipped, not in-deadband).
        self.adjust_count: int = 0

    # ============================================================ lifecycle

    def arm(self) -> None:
        """Configure the scope's gating window and seed the bias
        module's programmed voltage to the setpoint.

        Idempotent — safe to call repeatedly.  Does NOT enable the
        bias module's master output (caller owns that).
        """
        self.scope.gate_measurement_window(*self.config.gating_window_us)
        self.bias_module.set_bias_voltage(self.config.setpoint_v)

    def disarm(self) -> None:
        """Release the scope's measurement gating so other code
        that uses MEASUrement queries gets the full-window default.

        Does NOT change the bias module's state — caller decides
        whether to disable the DAC.
        """
        self.scope.clear_measurement_gating()

    # ============================================================ step

    def step(self) -> BiasFeedbackStep:
        """Run one control iteration.

        Reads E_ret (and V_mon for sanity), decides whether to
        adjust the bias DAC, applies the adjustment if warranted,
        returns a :class:`BiasFeedbackStep` describing what
        happened.

        Never raises — measurement failures, sanity failures, and
        deadband no-ops all surface as ``skipped=True`` records
        rather than exceptions.  This keeps the caller's loop
        simple (no try/except around every step).
        """
        self.step_count += 1
        bias_before = self._safe_get_bias()

        # ---- V_mon sanity check (if enabled) ----------------------
        vmon_sane = True
        if self.config.vmon_channel:
            try:
                vmon_v = self.scope.measure_mean(self.config.vmon_channel)
            except Exception as e:
                # Scope query failed — treat as inconclusive and
                # skip rather than risk acting on bad data.
                return BiasFeedbackStep(
                    measured_v=float("nan"),
                    error_v=float("nan"),
                    in_deadband=False,
                    bias_v_before=bias_before,
                    bias_v_after=bias_before,
                    saturated=False,
                    vmon_sane=False,
                    skipped=True,
                    note=f"V_mon read raised: {type(e).__name__}: {e}",
                )
            # NaN V_mon: scope couldn't measure (e.g. no
            # acquisition yet) — don't fail the sanity check, but
            # also don't trust E_ret blindly.  Treat as sane and
            # continue; the E_ret NaN check below will catch a
            # broken acquisition.
            if math.isnan(vmon_v):
                pass
            elif abs(vmon_v) > self.config.vmon_sanity_threshold_v:
                return BiasFeedbackStep(
                    measured_v=float("nan"),
                    error_v=float("nan"),
                    in_deadband=False,
                    bias_v_before=bias_before,
                    bias_v_after=bias_before,
                    saturated=False,
                    vmon_sane=False,
                    skipped=True,
                    note=(
                        f"V_mon sanity check failed: |{vmon_v:.4f} V| > "
                        f"{self.config.vmon_sanity_threshold_v:.4f} V "
                        f"— bias delivery suspect, skipping"),
                )

        # ---- Primary measurement: E_ret over the gated window ----
        try:
            measured_v = self.scope.measure_mean(self.config.channel)
        except Exception as e:
            return BiasFeedbackStep(
                measured_v=float("nan"),
                error_v=float("nan"),
                in_deadband=False,
                bias_v_before=bias_before,
                bias_v_after=bias_before,
                saturated=False,
                vmon_sane=vmon_sane,
                skipped=True,
                note=f"E_ret read raised: {type(e).__name__}: {e}",
            )
        if math.isnan(measured_v):
            return BiasFeedbackStep(
                measured_v=float("nan"),
                error_v=float("nan"),
                in_deadband=False,
                bias_v_before=bias_before,
                bias_v_after=bias_before,
                saturated=False,
                vmon_sane=vmon_sane,
                skipped=True,
                note="E_ret read returned NaN — scope acquisition "
                     "not ready or gating cleared",
            )

        # ---- Error + deadband ------------------------------------
        error = measured_v - self.config.setpoint_v
        if abs(error) <= self.config.tolerance_v:
            return BiasFeedbackStep(
                measured_v=measured_v,
                error_v=error,
                in_deadband=True,
                bias_v_before=bias_before,
                bias_v_after=bias_before,
                saturated=False,
                vmon_sane=vmon_sane,
                skipped=True,
                note=(
                    f"in deadband: |{error * 1e3:+.2f} mV| ≤ "
                    f"{self.config.tolerance_v * 1e3:.2f} mV"),
            )

        # ---- Integral update + saturation clamp ------------------
        raw_new = bias_before - self.config.k_i * error
        clamped_new = max(self.config.bias_min_v,
                          min(self.config.bias_max_v, raw_new))
        saturated = clamped_new != raw_new

        # Apply.  If the apply fails, the loop continues — the
        # bias module is presumably temporarily unavailable, and
        # we'll try again next iteration.
        try:
            self.bias_module.set_bias_voltage(clamped_new)
        except Exception as e:
            return BiasFeedbackStep(
                measured_v=measured_v,
                error_v=error,
                in_deadband=False,
                bias_v_before=bias_before,
                bias_v_after=bias_before,
                saturated=saturated,
                vmon_sane=vmon_sane,
                skipped=True,
                note=(
                    f"set_bias_voltage({clamped_new:.4f}) raised: "
                    f"{type(e).__name__}: {e}"),
            )

        self.adjust_count += 1
        return BiasFeedbackStep(
            measured_v=measured_v,
            error_v=error,
            in_deadband=False,
            bias_v_before=bias_before,
            bias_v_after=clamped_new,
            saturated=saturated,
            vmon_sane=vmon_sane,
            skipped=False,
            note=(
                f"adjusted: error={error * 1e3:+.2f} mV → "
                f"bias {bias_before:.4f} V → {clamped_new:.4f} V"
                f"{' (SATURATED)' if saturated else ''}"),
        )

    # ============================================================ helpers

    def _safe_get_bias(self) -> float:
        """Read the bias module's currently-programmed voltage.

        On read failure, returns the configured setpoint as a
        best-guess so the step record carries a sensible number.
        The next step() will re-attempt the read.
        """
        try:
            return float(self.bias_module.get_bias_voltage())
        except Exception:
            return self.config.setpoint_v
