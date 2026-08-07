"""VT-max near-limit approach: the WIDE-SPAN SECANT step.

Operator: "I wanted to reach the maximum with reduced number of captures."

The bench run exp_vt_max_cathodal_test_dc showed where the captures actually
went — not the climb, but a STALL just outside the acceptance band:

    CH12  #7  -689.7 µA  Emc -0.5395   ← climb arrives (8 captures, fine)
          #8  -701.7     -0.5582
          …   nine captures, +48 µA total, Emc wandering inside its own
          #16 -749.7     -0.5728        ±10 mV scatter, never converging
          #17 -862.2     -0.6607   ← escaped by the growth cap's ×1.15
                                      amplitude jump (+112 µA) → EXCEEDED
          #18 -784.5     -0.5939   ← back-off

Eleven of nineteen captures in one crawl→overshoot→recover pattern; ~36 of the
run's 170 captures across the run.  Two coupled defects:

  * ``_oscillate_approach_step`` is DISTANCE-driven — a fixed volts→µA table
    with no knowledge of dE_pol/dI.  The real slope here is ~6-8e-4 V/µA, so
    the last 0.02-0.04 V needs 30-50 µA, but the table prescribed 3-12 µA and
    SHRANK the step as the reading drifted closer.
  * The escape was AMPLITUDE-driven (growth-cap ×1.15 on ~750-850 µA = +112…
    128 µA) → far too big → overshoot → back-off.

``_wide_secant_approach_step`` sizes the step from the MEASURED slope instead,
over a WIDE amplitude span, aiming at the band CENTRE.  Replaying all 14
reconstructed bench curves through the real ``run()``:

    noise   captures   max overshoot   channels past the far edge
    0 mV     -23.7%    1.075 → 0.975        4 → 0
    4 mV     -22.0%    1.075 → 1.029       19 → 3
    8 mV     -18.3%    1.113 → 1.062       16 → 7
   12 mV     -18.8%    1.116 → 1.070       19 → 11

i.e. strictly faster AND strictly safer at every noise level.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, Session, TestParameters
from stimtest.waveforms import PulsePattern

CATH, ANOD, TOL = -0.6, 0.6, 0.02


def _runner(*, start_ua=1.0, max_ua=1000.0):
    pattern = PulsePattern.biphasic(amplitude_ua=start_ua, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(strategy="adaptive", max_ua=max_ua),
        cathodic_limit_v=CATH, anodic_limit_v=ANOD,
        polarization_tolerance_v=TOL)
    return runner, stim, scope


def _drive(runner, epol_of_amp):
    def _fake_capture(config, pattern, capture_idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=capture_idx, pattern=pattern)
        c.metrics.polarization_per_phase_v = [epol_of_amp(amp), 0.0]
        c.metrics.charge_injection_mc_per_cm2 = amp / 100.0
        c.metrics.response_class = "normal"
        c.time_us = np.zeros(4); c.v_mon_v = np.zeros(4); c.i_mon_ua = np.zeros(4)
        return c
    runner._one_capture = _fake_capture
    runner._record_capture_dose = lambda run, cap: None
    runner.bias_step_if_armed = lambda: None
    runner.apply_default_scope_view = lambda *a, **k: None
    runner.arm_bias_feedback = lambda: None
    runner.disarm_bias_feedback = lambda: None
    runner._seed_scope_scales = lambda *a, **k: None
    runner.preflight = lambda: None
    return runner


def _caps(runner):
    caps = [c for c in runner.session.runs[0].captures if not c.status.aborted]
    amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
    epols = [c.metrics.polarization_per_phase_v[0] for c in caps]
    return amps, epols


def _bench_like(amp):
    """A saturating (concave-down) SIROF response matching the bench shape:
    ~0.20 V rest, crossing -0.58 near 800 µA with a shallow ~6e-4 V/µA slope
    there — the regime where the distance table crawled."""
    return 0.205 - 0.95 * (amp / 1000.0) ** 0.62


# ---------------------------------------------------------------------------
# Unit behaviour of the step itself
# ---------------------------------------------------------------------------
def _series(runner, pts):
    """Install a synthetic excursion series (amp → E_pol) on the runner."""
    runner._excursion_series = lambda captures: {("active", 0): list(pts)}
    runner._min_over_amp = lambda captures: None
    runner._epol_accelerating = lambda captures: False
    return runner


def test_targets_the_band_centre_not_the_near_edge():
    """The ±tolerance band exists to ABSORB measurement noise.  Aiming at its
    near edge spends that budget, so half the readings land outside and the
    ramp cannot converge; aiming at the centre puts the whole scatter inside.
    """
    runner, stim, scope = _runner()
    try:
        # Slope exactly -1e-3 V/µA over a wide span; now at 500 µA / -0.52 V
        # (86.7 % of the limit, i.e. inside the near-limit engage window).
        _series(runner, [(400.0, -0.42), (500.0, -0.52)])
        d = runner._wide_secant_approach_step([], 500.0)
        assert d is not None
        # centre (-0.60) is 0.08 V away at 1e-3 V/µA → 80 µA.
        # near edge (-0.58) would have been only 60 µA.
        assert abs(d - 80.0) < 1.0, d
    finally:
        stim.close(); scope.close()


def test_uses_a_wide_span_not_the_last_two_points():
    """A short-span slope is dominated by the E_pol scatter — that is what made
    the OLD local projection overshoot (gotcha #155), and on a plateaued
    electrode it reads slope ≈ 0 and projects an unbounded step.  Here the last
    two points are a 1 µA nudge with noise-level ΔE; the step must be derived
    from the wide pair, not from them."""
    runner, stim, scope = _runner()
    try:
        _series(runner, [(400.0, -0.42), (499.0, -0.5195), (500.0, -0.52)])
        d = runner._wide_secant_approach_step([], 500.0)
        assert d is not None
        # Wide slope 1e-3 → 80 µA.  The 1 µA pair's slope (5e-4) would give
        # 160 µA; a truly flat pair would give a far larger / unbounded step.
        assert abs(d - 80.0) < 5.0, d
    finally:
        stim.close(); scope.close()


def test_declines_without_a_wide_span_pair():
    """With only closely-spaced points there is no trustworthy slope, so the
    step declines and the caller falls back to the distance table.  This makes
    the change a strict refinement rather than a replacement."""
    runner, stim, scope = _runner()
    try:
        _series(runner, [(499.0, -0.4995), (500.0, -0.50)])
        assert runner._wide_secant_approach_step([], 500.0) is None
    finally:
        stim.close(); scope.close()


def test_declines_when_far_from_the_limit():
    """The regression/secant owns the fast far climb; this step engages only
    inside the same near-limit window as the distance table."""
    runner, stim, scope = _runner()
    try:
        _series(runner, [(100.0, -0.10), (200.0, -0.20)])   # 0.20 / 0.6 = 33 %
        assert runner._wide_secant_approach_step([], 200.0) is None
    finally:
        stim.close(); scope.close()


def test_declines_when_already_in_band():
    """An in-band excursion is owned by the per-capture band stop; never step
    further on account of it."""
    runner, stim, scope = _runner()
    try:
        _series(runner, [(400.0, -0.40), (500.0, -0.59)])
        assert runner._wide_secant_approach_step([], 500.0) is None
    finally:
        stim.close(); scope.close()


def test_convexity_guard_halves_an_accelerating_projection():
    """A wide secant UNDER-estimates the local slope on a concave-UP electrode
    — the one case where it could overshoot — so the projection is halved.
    This strictly reduces the step and can never create an overshoot."""
    runner, stim, scope = _runner()
    try:
        _series(runner, [(400.0, -0.42), (500.0, -0.52)])
        straight = runner._wide_secant_approach_step([], 500.0)
        assert straight is not None
        runner._epol_accelerating = lambda captures: True
        halved = runner._wide_secant_approach_step([], 500.0)
        assert abs(halved - straight / 2.0) < 1e-6, (straight, halved)
    finally:
        stim.close(); scope.close()


def test_never_projects_past_the_hardware_ceiling():
    """Without the clamp the run loop's ``while amp <= max_ua`` exits on the
    over-ceiling step and a hardware-limited channel is reported SHORT of the
    ceiling it could have been driven to — on the bench CH06 stopped at 970 µA
    / -0.560 V instead of 1000 µA / -0.578 V."""
    runner, stim, scope = _runner(max_ua=1000.0)
    try:
        # Very shallow slope → the raw projection lands far beyond 1000 µA.
        _series(runner, [(800.0, -0.520), (950.0, -0.530)])
        d = runner._wide_secant_approach_step([], 950.0)
        assert d is not None
        assert abs((950.0 + d) - 1000.0) < 1e-6, 950.0 + d
    finally:
        stim.close(); scope.close()


def test_respects_the_over_under_bracket():
    """Never step to or past a known over-amp — stay strictly below it."""
    runner, stim, scope = _runner()
    try:
        _series(runner, [(400.0, -0.42), (500.0, -0.52)])
        runner._min_over_amp = lambda captures: 520.0
        d = runner._wide_secant_approach_step([], 500.0)
        assert d is not None and 500.0 + d < 520.0, d
    finally:
        stim.close(); scope.close()


# ---------------------------------------------------------------------------
# End-to-end: fewer captures, no more overshoot
# ---------------------------------------------------------------------------
def _run_both(epol):
    """Same electrode, with and without the wide-secant step."""
    out = {}
    for tag, disable in (("old", True), ("new", False)):
        runner, stim, scope = _runner()
        try:
            _drive(runner, epol)
            if disable:
                runner._wide_secant_approach_step = lambda *a, **k: None
            runner.run()
            amps, epols = _caps(runner)
            out[tag] = (len(amps), amps, epols)
        finally:
            stim.close(); scope.close()
    return out


def test_bench_like_electrode_needs_fewer_captures():
    r = _run_both(_bench_like)
    n_old, _, _ = r["old"]
    n_new, amps, epols = r["new"]
    assert n_new < n_old, (
        f"no capture saving: old={n_old} new={n_new}; "
        f"amps={[round(a, 1) for a in amps]}")
    # And it still reaches the acceptance band.
    assert min(epols) <= CATH + TOL, epols


def test_bench_like_electrode_does_not_overshoot_more():
    """The capture saving must not be bought with electrode risk."""
    r = _run_both(_bench_like)
    worst_old = min(r["old"][2])
    worst_new = min(r["new"][2])
    assert abs(worst_new) <= abs(worst_old) + 1e-9, (
        f"peak |E_pol| grew: old={worst_old:.4f} new={worst_new:.4f}")


def test_saturating_plateau_does_not_stall():
    """The CH01/CH09 shape: E_pol goes essentially FLAT for tens of µA just
    outside the band, then resumes.  The distance table crawled 5 captures at
    +6 µA moving E_pol by 0.0001 V; the wide secant must not."""
    def epol(amp):
        if amp < 820.0:
            return 0.205 - 0.95 * (amp / 1000.0) ** 0.62
        if amp < 855.0:
            return -0.5607                      # measured dead-flat patch
        return -0.5607 - 5.7e-4 * (amp - 855.0)  # resumes at the bench slope

    r = _run_both(epol)
    n_old, _, _ = r["old"]
    n_new, amps, epols = r["new"]
    assert n_new <= n_old, (n_old, n_new)
    # No run of 4+ consecutive steps that each move E_pol by < 1 mV.
    stalled = 0
    worst_stall = 0
    for i in range(1, len(epols)):
        if abs(epols[i] - epols[i - 1]) < 1e-3 and amps[i] > amps[i - 1]:
            stalled += 1
            worst_stall = max(worst_stall, stalled)
        else:
            stalled = 0
    assert worst_stall < 4, (
        f"still crawling: {worst_stall} consecutive sub-mV steps; "
        f"amps={[round(a, 1) for a in amps]}")


def test_concave_up_electrode_still_bounded():
    """The dangerous case (the pcc fling): a concave-UP electrode must stay
    bounded — the convexity guard, the E_pol growth cap, the per-capture band
    stop and the back-off all remain in force."""
    def epol(amp):
        return -0.6 * (amp / 300.0) ** 2

    r = _run_both(epol)
    worst_old = min(r["old"][2])
    worst_new = min(r["new"][2])
    assert abs(worst_new) / abs(CATH) <= 1.6, worst_new
    assert abs(worst_new) <= abs(worst_old) + 1e-9, (worst_old, worst_new)
