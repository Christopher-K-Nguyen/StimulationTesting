"""exp1 (exponential) regression in the max-Q_inj predictor — MATLAB parity.

MATLAB ``changeCurrent_Fit.m`` tries ``FIT_CELL = {'poly1','poly2','poly3',
'exp1'}``.  The Python predictor was missing ``exp1``; it's now the 4th fit,
tried LAST (only when no polynomial clears its R² gate) so a genuinely
exponential E_pol-vs-current curve still yields a crossover prediction.  Also
confirms there is NO "≥ 4 captures" requirement (MATLAB's ``sizeForFit = 4``):
the Python predicts from ``min_points_for_regression`` (2) points.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (RampPolicy,
                                                    VoltageTransientExperiment)
from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                         SimulatedStimulator)
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner():
    pat = PulsePattern.biphasic(amplitude_ua=-100.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    r = VoltageTransientExperiment(
        sess, stim, scope, configurations=[Configuration.monopolar(1)],
        ramp=RampPolicy(strategy="adaptive", max_ua=1000.0),
        cathodic_limit_v=-0.6, anodic_limit_v=0.8)
    return r, stim, scope


def test_exp1_solves_pure_exponential():
    r, stim, scope = _runner()
    try:
        x = np.array([50.0, 100.0, 200.0, 300.0])
        y = 0.02 * np.exp(0.01 * x)                # a=0.02, b=0.01
        got = r._fit_solve_exp1(x, y, 0.6)         # ln(0.6/0.02)/0.01 = 340.1
        assert got is not None and abs(got - 340.1) < 2.0, got
    finally:
        stim.close(); scope.close()


def test_polynomial_wins_over_exp1_when_it_fits():
    r, stim, scope = _runner()
    try:
        x = np.array([10.0, 20.0, 30.0, 40.0])
        y = 0.01 * x                                # perfectly linear → poly1
        got = r._solve_excursion_crossing(x, y, 0.6)
        assert abs(got - 60.0) < 1e-6, got         # poly1: 0.6/0.01 = 60
    finally:
        stim.close(); scope.close()


def test_exp1_rescues_when_polys_all_fail():
    r, stim, scope = _runner()
    try:
        # Sharp exponential that poly1 fits poorly (curved) — verify a finite
        # crossover comes back (exp1 or a higher poly), never None.
        x = np.array([20.0, 60.0, 120.0, 200.0, 300.0])
        y = 0.01 * np.exp(0.012 * x)
        got = r._solve_excursion_crossing(x, y, 0.6)
        assert got is not None and np.isfinite(got) and 0 < got <= 1000, got
    finally:
        stim.close(); scope.close()


def test_exp1_declines_mixed_sign_and_wrong_sign_target():
    r, stim, scope = _runner()
    try:
        assert r._fit_solve_exp1(np.array([1.0, 2.0, 3.0]),
                                 np.array([-0.1, 0.05, 0.2]), 0.6) is None
        # a>0 but a negative target → ln of ≤0 → no real crossover.
        x = np.array([50.0, 100.0, 200.0]); y = 0.02 * np.exp(0.01 * x)
        assert r._fit_solve_exp1(x, y, -0.6) is None
    finally:
        stim.close(); scope.close()


def test_no_four_capture_requirement():
    # MATLAB waited for sizeForFit=4; the Python predicts from 2 points.
    r, stim, scope = _runner()
    try:
        assert r.ramp.min_points_for_regression <= 2
        # A 2-point linear excursion yields a crossover (poly1 needs 2 pts).
        got = r._solve_excursion_crossing(np.array([10.0, 20.0]),
                                          np.array([0.1, 0.2]), 0.6)
        assert got is not None and abs(got - 60.0) < 1e-6, got
    finally:
        stim.close(); scope.close()
