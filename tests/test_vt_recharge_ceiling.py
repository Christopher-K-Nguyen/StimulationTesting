"""VT-max recharge-aware ceiling.

Bug (exp_vt_max_pcc): a cap-coupled pattern has a rectangular cathodic
EXCITATION + an exp-decay anodic RECHARGE whose fast decay carries only ~20 %
of the charge a rectangle of the same peak would — so to stay charge-balanced
its PEAK runs several× the excitation.  The ramp scaled the whole pattern by
``amp / |excitation|``, so the RECHARGE phase hit the 1000 µA PlexStim rail at
an EXCITATION amplitude (~218 µA) far below ``max_ua`` (1000).  The ramp drove
past it, the device REJECTED the pattern ("Stimulator program error: Phase 2
amplitude exceeds max"), and the run erred instead of cleanly reporting
"hardware-limited".

Fix: ``_recharge_aware_max_ua`` caps the excitation ceiling at ``rail /
max_phase_ratio`` so the largest phase can never be driven past the rail — the
ramp stops cleanly at that excitation.
"""
from __future__ import annotations

import pytest

from stimtest.config import STIM_MAX_AMPLITUDE_UA
from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Session, TestParameters
from stimtest.waveforms import (
    Phase, PulsePattern, SHAPE_RECTANGULAR, SHAPE_EXP_DECAY)


def _cap_coupled(exc_ua=-50.0, recharge_peak_ua=230.0, w=200.0):
    """Rectangular cathodic excitation + exp-decay anodic recharge with a
    higher peak (ratio = recharge_peak / |exc|)."""
    return PulsePattern(
        phases=[Phase(amplitude_ua=exc_ua, width_us=w, shape=SHAPE_RECTANGULAR),
                Phase(amplitude_ua=recharge_peak_ua, width_us=w,
                      shape=SHAPE_EXP_DECAY)],
        rate_hz=50.0)


def _runner(pattern, max_ua=2000.0):
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(strategy="adaptive", coarse_step_ua=40.0, max_ua=max_ua),
        cathodic_limit_v=-0.8, anodic_limit_v=0.6)
    return runner, stim, scope


# --------------------------------------------------------------------------
# helper: the ceiling computation
# --------------------------------------------------------------------------
def test_helper_caps_asymmetric_cap_coupled():
    r, stim, scope = _runner(_cap_coupled(exc_ua=-50.0, recharge_peak_ua=230.0))
    p = r.session.test.pattern
    ratio = 230.0 / 50.0                       # 4.6
    eff = r._recharge_aware_max_ua(p)
    assert eff == pytest.approx(STIM_MAX_AMPLITUDE_UA / ratio, rel=1e-6)  # ~217
    assert eff < r._policy_max_ua              # genuinely capped below the rail
    # at that ceiling the LARGEST phase sits exactly on the rail
    scaled = r._pattern_at_amplitude(p, eff)
    peak = max(abs(ph.amplitude_ua) for ph in scaled.phases)
    assert peak == pytest.approx(STIM_MAX_AMPLITUDE_UA, rel=1e-6)
    stim.close(); scope.close()


def test_helper_symmetric_not_capped():
    p = PulsePattern.biphasic(amplitude_ua=100.0, phase_width_us=200.0,
                              symmetric=True)
    r, stim, scope = _runner(p)
    assert r._recharge_aware_max_ua(p) == pytest.approx(r._policy_max_ua)
    stim.close(); scope.close()


def test_helper_excitation_dominant_not_capped():
    # excitation IS the largest phase (recharge smaller) → ratio ≤ 1 → no cap
    p = _cap_coupled(exc_ua=-200.0, recharge_peak_ua=80.0)
    r, stim, scope = _runner(p)
    assert r._recharge_aware_max_ua(p) == pytest.approx(r._policy_max_ua)
    stim.close(); scope.close()


def test_helper_does_not_compound():
    # recomputing from the STABLE _policy_max_ua → same result every call
    p = _cap_coupled()
    r, stim, scope = _runner(p)
    a = r._recharge_aware_max_ua(p)
    r.ramp.max_ua = a                          # simulate a prior config lowering it
    b = r._recharge_aware_max_ua(p)
    assert a == pytest.approx(b)               # didn't compound off the lowered value
    stim.close(); scope.close()


# --------------------------------------------------------------------------
# end-to-end: the ramp NEVER builds a pattern past the rail
# --------------------------------------------------------------------------
def test_ramp_never_exceeds_rail_on_asymmetric():
    r, stim, scope = _runner(_cap_coupled(exc_ua=-5.0, recharge_peak_ua=23.0))
    result = r.run()
    caps = [c for run in result.session.runs for c in run.captures if c.pattern]
    assert caps, "ramp produced no captures"
    # EVERY capture's largest phase stays within the rail — the core guarantee
    for c in caps:
        peak = max(abs(ph.amplitude_ua) for ph in c.pattern.phases)
        assert peak <= STIM_MAX_AMPLITUDE_UA + 1e-6, \
            f"capture at {c.pattern.excitation_phase.amplitude_ua} µA has a "\
            f"{peak} µA phase — over the {STIM_MAX_AMPLITUDE_UA} µA rail"
    # and no capture recorded a device / validation rejection
    for c in caps:
        note = (getattr(c.status, "notes", "") or "").lower()
        assert "exceeds max" not in note and "program error" not in note, note
    stim.close(); scope.close()
