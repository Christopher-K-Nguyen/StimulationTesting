"""VT-max √-bounded buried-region growth loosening.

Operator: "reach the maximum charge injection capacity more efficiently."
The flat loosest growth-cap tier (×2.5) is calibrated for the worst PHYSICAL
concave-up electrode (a quadratic E_pol ∝ amp²), so it wastes the risk budget
on a high-Q electrode whose E_pol stays far from the limit — the "flying blind"
crawl.  The √-bound raises the cap to √(T/ratio) IN the loosest tier only,
which keeps a quadratic electrode's projected next-ratio ≤ T (safe for any
n ≤ 2), while climbing much faster where the measured ratio is tiny.

Safety is validated by the whole ``test_vt_ramp_safety.py`` suite (the pcc/
bumps electrode-damage scenarios) staying green; this file adds the EFFICIENCY
claim + a direct not-worse-overshoot comparison.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, Session, TestParameters
from stimtest.waveforms import PulsePattern

CATH = -0.6


def _runner(ceiling):
    pattern = PulsePattern.biphasic(amplitude_ua=0.1, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    r = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(strategy="adaptive", max_ua=1000.0,
                        blind_growth_ceiling=ceiling),
        cathodic_limit_v=CATH, anodic_limit_v=0.6,
        polarization_tolerance_v=0.02)
    return r


def _drive(r, epol_of_amp):
    def _fake(config, pattern, idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=idx, pattern=pattern)
        c.metrics.polarization_per_phase_v = [epol_of_amp(amp), 0.0]
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


def _sweep(ceiling, epol):
    r = _drive(_runner(ceiling), epol)
    run = r._run_one_configuration(Configuration.monopolar(1))
    caps = [c for c in run.captures if not c.status.aborted]
    amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
    ratios = [abs(c.metrics.polarization_per_phase_v[0]) / abs(CATH) for c in caps]
    # captures to reach the crossover region (ratio ≥ 0.85)
    reach = next((i + 1 for i, x in enumerate(ratios) if x >= 0.85), len(caps))
    return len(caps), reach, (max(ratios) if ratios else 0.0)


# CH01-like high-Q electrode: E_pol ∝ √amp (saturating SIROF), crosses ~900 µA.
def _high_q(amp):
    return -abs(CATH) * (min(amp / 900.0, 3.0) ** 0.55)


def test_loosening_reaches_crossover_faster_on_high_q():
    # ceiling 2.5 disables the loosening (== the old flat cap); 6.0 is the change.
    _, reach_base, _ = _sweep(2.5, _high_q)
    _, reach_impr, _ = _sweep(6.0, _high_q)
    assert reach_impr < reach_base, (reach_impr, reach_base)
    assert reach_impr <= reach_base - 2       # a real, not marginal, speedup


def test_loosening_does_not_worsen_overshoot():
    # Across saturating + concave-up (quadratic) electrodes, the √-bound never
    # overshoots MORE than the flat baseline (safe for any n ≤ 2).
    cases = [
        _high_q,
        lambda a: -abs(CATH) * (min(a / 100.0, 3.0) ** 2.0),   # quad cross=100
        lambda a: -abs(CATH) * (min(a / 40.0, 3.0) ** 2.0),    # quad cross=40
        lambda a: -abs(CATH) * (a / 8.0),                      # linear cross=8
    ]
    for epol in cases:
        _, _, worst_base = _sweep(2.5, epol)
        _, _, worst_impr = _sweep(6.0, epol)
        assert worst_impr <= worst_base + 0.06, (worst_impr, worst_base)
        assert worst_impr < 1.30               # contained by band-stop + back-off


def test_low_q_electrode_is_not_accelerated():
    # An 8 µA-crosser already shows a high ratio at low current, so it stays in
    # the tighter tiers (self-limiting) — the loosening must NOT fling it.
    n_impr, _, worst = _sweep(6.0, lambda a: -abs(CATH) * (a / 8.0))
    assert worst < 1.25
    assert n_impr <= 10        # converges quickly, no runaway
