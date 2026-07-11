"""The max-charge predictor considers ALL electrode-polarization
excursions, not a single worst-case scalar.

Operator: "Look at how my MATLAB code tried to consider all potential
excursions when predicting the maximum charge injection capacity."  Port
of ``changeCurrent_Fit.m`` lines 754-886 — the fit loops over every
excursion LOCATION × electrode (Active / Return) × limit, fits each one's
E_pol vs. current, solves for its limit crossing, and takes
``min(currentStim_guess)``: the ceiling is whichever excursion reaches the
water window FIRST.

The earlier Python predictor collapsed all excursions into one worst-case
``polarization_ratio`` per capture and fit that single envelope — which
loses each location's own trajectory and mispredicts when the dominant
excursion SWITCHES as current rises.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner(*, cathodic=-0.8, anodic=0.6, max_ua=2000.0):
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(strategy="adaptive", min_points_for_regression=2,
                        max_ua=max_ua),
        cathodic_limit_v=cathodic, anodic_limit_v=anodic)
    return runner, stim, scope


def _cap(pattern, amp_ua, *, active=(), returns=()):
    base = abs(pattern.excitation_phase.amplitude_ua) or 1.0
    p = pattern.scaled(amp_ua / base)
    c = Capture(index=0, pattern=p)
    c.metrics.polarization_per_phase_v = list(active)
    c.metrics.return_polarization_per_phase_v = list(returns)
    return c


def test_prediction_considers_return_electrode():
    """A return-electrode excursion that reaches its limit at a LOWER
    amplitude than the active must lower the predicted ceiling."""
    runner, stim, scope = _runner(cathodic=-0.8)
    try:
        pat = runner.session.test.pattern
        # active cathodic: -0.001·I -> reaches -0.8 at 800 µA
        # return cathodic: -0.0015·I -> reaches -0.8 at ~533 µA (limiting)
        caps = [_cap(pat, a, active=[-0.001 * a, 0.0],
                     returns=[-0.0015 * a, 0.0])
                for a in (100, 200, 300, 400)]
        target = runner._predict_target_regression(caps)
        assert target is not None
        assert 500.0 < target < 560.0, target   # return crosses first, not 800
    finally:
        stim.close(); scope.close()


def test_rising_cathodic_caught_under_flat_high_anodic():
    """The switching case the old worst-case-envelope approach missed: a
    flat, HIGH anodic phase (ratio ~0.97, never actually crosses +0.6)
    dominates the worst-case ratio at every amplitude, hiding a cathodic
    phase that is rising steeply toward -0.8.  Per-excursion fitting tracks
    the cathodic independently and predicts ITS crossing (~425 µA)."""
    runner, stim, scope = _runner(cathodic=-0.8, anodic=0.6)
    try:
        pat = runner.session.test.pattern
        # phase 0 = cathodic rising (-0.002·I + 0.05) -> crosses -0.8 at 425
        # phase 1 = anodic flat at +0.58 -> ratio 0.967 but NEVER reaches +0.6
        cath = {100: -0.15, 200: -0.35, 300: -0.55, 400: -0.75}
        caps = [_cap(pat, a, active=[cath[a], 0.58]) for a in (100, 200, 300, 400)]
        target = runner._predict_target_regression(caps)
        assert target is not None
        assert 400.0 < target < 460.0, target   # rising cathodic, not far/none
    finally:
        stim.close(); scope.close()


def test_secant_also_per_excursion():
    """The local-secant fast-approach is likewise per-excursion: the flat
    anodic gives no projection, the rising cathodic governs the step."""
    runner, stim, scope = _runner(cathodic=-0.8, anodic=0.6)
    try:
        pat = runner.session.test.pattern
        caps = [_cap(pat, a, active=[c, 0.58])
                for a, c in ((300, -0.55), (400, -0.75))]
        t = runner._local_secant_target(caps)
        assert t is not None
        # last two cathodic points slope -0.002 -> reaches -0.8 at 425
        assert 410.0 < t < 440.0, t
    finally:
        stim.close(); scope.close()


def test_min_across_excursions_is_taken():
    """When several excursions all eventually cross, the predictor returns
    the SMALLEST crossover (MATLAB ``min(currentStim_guess)``)."""
    runner, stim, scope = _runner(cathodic=-0.8, anodic=0.6)
    try:
        pat = runner.session.test.pattern
        # active cathodic crosses -0.8 at ~533; active anodic crosses +0.6
        # at 400 (steeper) -> min should be the anodic ~400.  Four points so
        # the early-fit ×0.9 dampening (n <= min_points+1) does not apply.
        amps = (100, 200, 300, 400)
        caps = [_cap(pat, a, active=[-0.0015 * a, 0.0015 * a]) for a in amps]
        # anodic 0.0015·I -> +0.6 at 400; cathodic -0.0015·I -> -0.8 at 533
        target = runner._predict_target_regression(caps)
        assert target is not None
        assert 380.0 < target < 420.0, target
    finally:
        stim.close(); scope.close()
