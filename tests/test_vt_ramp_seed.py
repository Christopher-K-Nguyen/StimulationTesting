"""Adaptive VT ramp: the pre-regression SEED jump.

Operator: "Why is adaptive ramping strategy so slow? It takes over a
dozen captures to reach the potential limit."  Before there were enough
points to fit a regression, the ramp crawled up by a fixed coarse step.
Now it SEEDS a conservative proportional jump — assuming polarization is
~linear, the amplitude that reaches the limit (ratio = 1) is
``current / ratio``; it jumps to ``seed_fraction`` × that, a safe
UNDERSHOOT that also gives the regression a high-amplitude anchor.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner(strategy):
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        # Ramp starts at the pattern's amplitude (10 µA) — starting_ua removed.
        ramp=RampPolicy(strategy=strategy,
                        coarse_step_ua=20.0, seed_fraction=0.7, max_ua=2000.0),
        cathodic_limit_v=-0.8, anodic_limit_v=0.6)
    return runner, stim, scope


def _cap_with_ratio(pattern, cath_v):
    """A capture whose cathodic E_pol = ``cath_v`` (ratio = |cath_v|/0.8)."""
    c = Capture(index=0, pattern=pattern)
    c.metrics.polarization_per_phase_v = [cath_v, 0.0]
    c.metrics.return_polarization_per_phase_v = []
    return c


def _cap_at(pattern, amp_ua, cath_v):
    """A capture at amplitude ``amp_ua`` with cathodic E_pol ``cath_v``."""
    base = abs(pattern.excitation_phase.amplitude_ua) or 1.0
    p = pattern.scaled(amp_ua / base)
    return _cap_with_ratio(p, cath_v)


def test_secant_takes_a_real_step_not_a_fine_creep():
    """The bug from exp_vt_max: the saturating (concave-down) E_pol made the
    global poly fit under-predict the crossover and the ramp crept by
    ~fine_step near the limit, burning 6-10 captures.  The local secant must
    project a REAL step toward the limit instead."""
    runner, stim, scope = _runner("adaptive")   # cathodic limit −0.8 V
    try:
        pat = runner.session.test.pattern
        # Saturating polarization: slope decreasing with amplitude.
        caps = [
            _cap_at(pat, 100.0, -0.30),   # ratio 0.375
            _cap_at(pat, 400.0, -0.60),   # ratio 0.75
            _cap_at(pat, 600.0, -0.72),   # ratio 0.90
        ]
        delta = runner._next_step(caps[-1], current_amp_ua=600.0, captures=caps)
        # Must be a real step (≫ fine_step), not a creep.
        assert delta > 10.0 * runner.ramp.fine_step_ua, \
            f"secant should take a real step toward the limit, got {delta} µA"
        # Safe: never leaps past half the remaining headroom to max_ua.
        assert 600.0 + delta <= 600.0 + (runner.ramp.max_ua - 600.0) * 0.5 + 1e-6
    finally:
        stim.close(); scope.close()


def test_secant_undershoots_for_saturating_data():
    """For concave-down (saturating) polarization the secant projection must
    land BELOW the true ratio = 1 crossover — never overshoot the water
    window in a single step."""
    runner, stim, scope = _runner("adaptive")
    try:
        pat = runner.session.test.pattern
        caps = [
            _cap_at(pat, 300.0, -0.50),   # ratio 0.625
            _cap_at(pat, 600.0, -0.72),   # ratio 0.90  (slope flattening)
        ]
        t = runner._local_secant_target(caps)
        assert t is not None and t > 600.0
        # Local slope (0.90−0.625)/(600−300)=0.000917/µA → target ≈ 600 +
        # (1−0.90)/0.000917 ≈ 709 µA.  The TRUE crossover for saturating data
        # is HIGHER than this linear projection, so stepping here undershoots.
        assert 690.0 < t < 730.0, t
    finally:
        stim.close(); scope.close()


def test_seed_jump_is_large_and_undershoots():
    runner, stim, scope = _runner("adaptive")
    try:
        pat = runner.session.test.pattern
        # At 10 µA the cathodic E_pol is only 10 % of the -0.8 V limit
        # (ratio 0.1) -> seed target = 10 / 0.1 * 0.7 = 70 µA -> delta ~60.
        cap = _cap_with_ratio(pat, -0.08)
        delta = runner._next_step(cap, current_amp_ua=10.0, captures=[cap])
        assert delta > 40.0, \
            f"seed jump should leap toward the limit, got {delta} µA"
        # Safe UNDERSHOOT: the projected next amplitude stays below the
        # linearly-extrapolated limit (10/0.1 = 100 µA).
        assert 10.0 + delta <= 100.0 + 1e-6, \
            "seed must undershoot the projected limit, never overshoot"
    finally:
        stim.close(); scope.close()


def test_seed_far_faster_than_fixed_coarse_step():
    runner, stim, scope = _runner("adaptive")
    try:
        pat = runner.session.test.pattern
        cap = _cap_with_ratio(pat, -0.08)
        delta = runner._next_step(cap, current_amp_ua=10.0, captures=[cap])
        # The old behaviour returned the fixed coarse step (20 µA); the
        # seed must be substantially larger so the ramp converges in a
        # few captures instead of a dozen.
        assert delta > runner.ramp.coarse_step_ua
    finally:
        stim.close(); scope.close()


def test_direction_of_travel_projects_from_positive_descending():
    """Dead-zone fix: a still-POSITIVE but DESCENDING cathodic excursion is
    projected toward the CATHODIC limit by DIRECTION OF TRAVEL (slope sign),
    so the ramp jumps out of the low-amplitude anodic-baseline zone from
    capture 2 instead of crawling until the value crosses zero (operator:
    ~42 % of captures were wasted in the warm-up)."""
    runner, stim, scope = _runner("adaptive")           # cathodic limit -0.8
    try:
        pat = runner.session.test.pattern
        # Two positive-but-DESCENDING captures (anodic-baseline-dominated).
        caps = [_cap_at(pat, 55.0, +0.15), _cap_at(pat, 126.0, +0.05)]
        t = runner._local_secant_target(caps)
        assert t is not None and t > 126.0, (
            "a positive-descending cathodic excursion must project toward the "
            "cathodic limit (direction of travel), not be skipped")
        # slope=(0.05-0.15)/(126-55)=-0.001408; cross=126+(-0.8-0.05)/-0.001408≈730
        assert 680.0 < t < 780.0, t
        # regression can't fit positive-only data (no cathodic root) → seed
        # branch → the direction-of-travel secant drives a big step out of the
        # dead-zone, not the fixed coarse step.
        delta = runner._next_step(caps[-1], current_amp_ua=126.0, captures=caps)
        assert delta > runner.ramp.coarse_step_ua
    finally:
        stim.close(); scope.close()


def test_convexity_guard_halves_an_accelerating_projection():
    """A rare ACCELERATING (concave-up) excursion — |slope| increasing — would
    make the linear secant OVERSHOOT; the convexity guard halves that
    projection so it can never fling past the crossover."""
    runner, stim, scope = _runner("adaptive")
    try:
        pat = runner.session.test.pattern
        # Accelerating descent: slope -0.001 then -0.003 (|slope| increasing).
        caps = [_cap_at(pat, 100.0, -0.10),
                _cap_at(pat, 200.0, -0.20),   # slope -0.001
                _cap_at(pat, 300.0, -0.50)]   # slope -0.003 (accelerating)
        # Raw secant to -0.8: 300 + (-0.8+0.5)/-0.003 = 400; guard halves the
        # 100 µA projection delta → 350.
        t = runner._local_secant_target(caps)
        assert t is not None
        assert abs(t - 350.0) < 5.0, t   # halved, not the raw 400
    finally:
        stim.close(); scope.close()


def test_max_ua_clamped_to_hardware_rail():
    """RampPolicy.max_ua is bounded to the PlexStim hardware rail (1000 µA) so
    the ramp can never target a pattern above it (a >1000 µA pattern raises
    ValueError in PulsePattern.validate → the run aborts).  A legacy / Long
    Pulsing ramp with max_ua > 1000 (or omitting it → the dataclass default)
    is clamped down (gotcha #56; audit finding)."""
    from stimtest.config import STIM_MAX_AMPLITUDE_UA
    from stimtest.experiments.voltage_transient import RampPolicy
    assert RampPolicy().max_ua == STIM_MAX_AMPLITUDE_UA == 1000.0
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    try:
        # A legacy ramp above the rail is clamped by the runner's __init__.
        runner = VoltageTransientExperiment(
            session, stim, scope, ramp=RampPolicy(max_ua=1500.0))
        assert runner.ramp.max_ua == STIM_MAX_AMPLITUDE_UA
        # The default fallback ramp is bounded too.
        assert VoltageTransientExperiment(
            session, stim, scope).ramp.max_ua <= STIM_MAX_AMPLITUDE_UA
    finally:
        stim.close(); scope.close()


def test_snap_to_ceiling_for_hardware_limited():
    """Hardware-limited: when even the UNDERSHOOTING secant projects the
    crossover BEYOND max_ua, jump straight to max_ua in ONE step instead of
    creeping by ~coarse_step near the top (operator: the anodic CH01/CH02 crept
    763→813→863→913→963)."""
    runner, stim, scope = _runner("adaptive")           # cathodic limit -0.8
    try:
        runner.ramp.max_ua = 1000.0
        pat = runner.session.test.pattern
        # Saturating near the top: secant (900-700 slope) projects ~1900 µA.
        caps = [_cap_at(pat, 700.0, -0.50), _cap_at(pat, 900.0, -0.55)]
        delta = runner._next_step(caps[-1], current_amp_ua=900.0, captures=caps)
        assert abs((900.0 + delta) - 1000.0) < 1e-6, (
            f"should snap to max_ua in one step, got next amp {900.0 + delta}")
    finally:
        stim.close(); scope.close()


def test_snap_to_ceiling_does_not_fire_for_a_real_crosser():
    """A channel that actually crosses BELOW max_ua has an UNDERSHOOTING secant
    below max_ua, so snap-to-ceiling must NOT fire early (a premature jump to
    the ceiling would overshoot the crossover / damage the electrode)."""
    runner, stim, scope = _runner("adaptive")
    try:
        runner.ramp.max_ua = 1000.0
        pat = runner.session.test.pattern
        caps = [_cap_at(pat, 200.0, -0.50), _cap_at(pat, 300.0, -0.65)]
        delta = runner._next_step(caps[-1], current_amp_ua=300.0, captures=caps)
        assert 300.0 + delta < 700.0, (
            f"must not snap to ceiling for a sub-max crosser, got {300.0 + delta}")
    finally:
        stim.close(); scope.close()


def test_hardware_limited_flag_on_non_crossing_ramp():
    """When the ramp maxes out WITHOUT reaching the water window (limits the
    simulator can't reach), the last capture is flagged hardware-limited so the
    reported max Q_inj is read as a lower bound (operator safety hardening)."""
    pattern = PulsePattern.biphasic(amplitude_ua=5.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(coarse_step_ua=50.0, fine_step_ua=10.0, max_ua=300.0),
        cathodic_limit_v=-3.0, anodic_limit_v=3.0)   # unreachable by the sim
    try:
        result = runner.run()
        caps = [c for c in result.captures if not c.status.aborted]
        assert caps
        assert not any(c.status.reached_potential_limit for c in caps)
        if getattr(caps[-1].metrics, "response_class", "normal") == "normal":
            assert "hardware-limited" in (caps[-1].status.notes or ""), \
                "a non-crossing normal ramp must be flagged hardware-limited"
    finally:
        stim.close(); scope.close()


def test_seed_does_not_fire_when_already_near_limit():
    runner, stim, scope = _runner("adaptive")
    try:
        pat = runner.session.test.pattern
        # ratio ~0.95 -> seed target = current/0.95*0.7 < current -> no big
        # jump; falls back to the coarse step (regression takes over next).
        cap = _cap_with_ratio(pat, -0.76)
        delta = runner._next_step(cap, current_amp_ua=100.0, captures=[cap])
        assert delta <= runner.ramp.coarse_step_ua + 1e-6
    finally:
        stim.close(); scope.close()
