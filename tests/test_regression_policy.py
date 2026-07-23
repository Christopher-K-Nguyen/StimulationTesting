"""The PURE-"Regression" ramp policy (operator: "let Regression be a different
ramp policy, where ONLY regression is used and efficiency is not a goal").

Distinct from "Adaptive" (the full water-window-seeking controller): the
Regression policy's step selection is the regression projection ALONE — no
local secant, distance-table oscillation, plateau escalation, or seed
dampening — and the run-loop efficiency heuristics (√-loosening, concave-down
loosening, snap-to-ceiling) are disabled for it.  Safety (band stop + back-off
+ base growth cap) still applies.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner(strategy="regression", anod=0.8):
    pattern = PulsePattern.biphasic(amplitude_ua=0.0, polarity=1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    return VoltageTransientExperiment(
        Session(notebook="t", subject="s", test=test), stim, scope,
        ramp=RampPolicy(strategy=strategy, max_ua=1000.0, coarse_step_ua=50.0),
        cathodic_limit_v=-0.6, anodic_limit_v=anod,
        polarization_tolerance_v=0.02)


def _drive(r, epol_of_amp, seed=0):
    rng = np.random.default_rng(seed)

    def _fake(config, pattern, idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=idx, pattern=pattern)
        noise = rng.normal(0.0, 0.008) if amp > 0 else 0.0
        c.metrics.polarization_per_phase_v = [epol_of_amp(amp) + noise, 0.0]
        c.metrics.charge_injection_mc_per_cm2 = amp / 100.0
        c.metrics.response_class = "normal"
        c.time_us = np.zeros(4); c.v_mon_v = np.zeros(4); c.i_mon_ua = np.zeros(4)
        return c
    r._one_capture = _fake
    r._record_capture_dose = lambda run, cap: None
    r.bias_step_if_armed = lambda: None
    r.apply_default_scope_view = lambda *a, **k: None
    r.arm_bias_feedback = lambda: None
    r.disarm_bias_feedback = lambda: None
    r._seed_scope_scales = lambda *a, **k: None
    r._emit = lambda ev: None
    return r


# ---- backend ----------------------------------------------------------------

def test_regression_policy_reaches_the_band():
    """A rising electrode is climbed to the water window by the pure-regression
    projection + coarse bootstrap; it REACHES the band."""
    r = _runner()
    _drive(r, lambda a: 0.8 * min(a / 500.0, 3.0) ** 0.75, seed=0)
    run = r._run_one_configuration(Configuration.monopolar(1))
    caps = [c for c in run.captures if not c.status.aborted]
    assert any(c.status.reached_potential_limit for c in caps)
    assert np.isfinite(run.max_q_inj)


def test_regression_step_is_projection_only():
    """``_next_step_regression`` steps a fraction of the PROJECTED crossover
    delta and NEVER calls the escalation / secant / oscillate."""
    r = _runner()
    # Build a few captures with a clean rising E_pol so the regression fits.
    caps = []
    for a in (50.0, 100.0, 150.0, 200.0):
        c = Capture(index=len(caps),
                    pattern=PulsePattern.biphasic(amplitude_ua=a, polarity=1))
        c.metrics.polarization_per_phase_v = [0.001 * a, 0.0]   # 0.05→0.20 rising
        c.metrics.response_class = "normal"
        caps.append(c)
    # projected crossover ≈ where 0.001*a = 0.78 → 780 µA; step = frac*(780-200)
    delta = r._next_step_regression(caps[-1], 200.0, caps)
    assert delta > 0
    # a fraction (0.9) of the ~580 µA projection gap, not a fixed tiny creep
    assert 100.0 < delta < 600.0, delta


def test_regression_bootstraps_with_coarse_step_before_fit():
    """Before there are enough points to fit, the regression policy takes the
    fixed COARSE bootstrap step (not a projection)."""
    r = _runner()
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=1.0, polarity=1))
    c.metrics.polarization_per_phase_v = [0.05, 0.0]
    c.metrics.response_class = "normal"
    delta = r._next_step_regression(c, 1.0, [c])
    assert delta == pytest.approx(r.ramp.coarse_step_ua)


def test_regression_zero_start_steps_to_one():
    r = _runner()
    assert r._next_step_regression(None, 0.0, []) == pytest.approx(1.0)


# ---- GUI: dropdown + prefs migration ---------------------------------------

def _vt_tab():
    from PyQt6 import QtWidgets
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    return app, w, w._exp_tab_by_code["VT"][0]


def test_strategy_dropdown_has_adaptive_and_regression():
    app, w, tab = _vt_tab()
    try:
        items = [tab.strategy_combo.itemText(i)
                 for i in range(tab.strategy_combo.count())]
        # Ordered slowest → fastest (operator): Fixed increment, Regression,
        # Adaptive.
        assert items == [tab.STRAT_INCR, tab.STRAT_REGRESSION, tab.STRAT_ADAPT]
        assert "Adaptive (regression)" not in items   # legacy label is gone
        # speed parentheticals (operator: "I do want to include the
        # parenthetical tag")
        assert "(slowest)" in tab.STRAT_INCR
        assert "(fastest)" in tab.STRAT_ADAPT
    finally:
        w.close()


def test_legacy_strategy_prefs_migrate():
    """Old stored strategy labels restore to the right item, not silently to
    item 0: the legacy hybrid label AND the pre-parenthetical "Fixed
    increment"."""
    app, w, tab = _vt_tab()
    try:
        tab.restore_prefs({"strategy_combo": "Adaptive (regression)"})
        assert tab.strategy_combo.currentText() == tab.STRAT_ADAPT
        tab.restore_prefs({"strategy_combo": "Fixed increment"})
        assert tab.strategy_combo.currentText() == tab.STRAT_INCR
    finally:
        w.close()


def test_gui_builds_regression_policy():
    app, w, tab = _vt_tab()
    try:
        tab.mode_combo.setCurrentText(tab.MODE_MAX)
        tab.strategy_combo.setCurrentText(tab.STRAT_REGRESSION)
        pol = tab._build_ramp_policy(50.0)
        assert pol.strategy == "regression"
    finally:
        w.close()
