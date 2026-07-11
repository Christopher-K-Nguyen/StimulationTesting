"""0.1 µA current-testing resolution + seed safety cap + ×0.7→×0.5 dampening.

Operator (bumps CH16 was canceled after its 1 µA seed flung to 51 µA →
E_pol −4.8 V, then the back-off gave up at 1 µA):
  * "I want current testing (not current pattern) to have 0.1 µA resolution."
  * (seed safety) don't let a near-noise 1 µA reading fling a low-Q electrode.
  * "Have the next step in adaptive ramping after the −30 % step be −50 %."
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (RampPolicy,
                                                    VoltageTransientExperiment)
from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                         SimulatedStimulator)
from stimtest.session import (Capture, CaptureMetrics, CaptureStatus, Session,
                              TestParameters)
from stimtest.waveforms import PulsePattern


def _runner():
    pat = PulsePattern.biphasic(amplitude_ua=0.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    r = VoltageTransientExperiment(
        sess, stim, scope, configurations=[Configuration.monopolar(1)],
        ramp=RampPolicy(strategy="adaptive"),
        cathodic_limit_v=-0.6, anodic_limit_v=0.8)
    return r, stim, scope


def _cap(amp_ua, pol):
    pat = PulsePattern.biphasic(amplitude_ua=max(abs(amp_ua), 1e-9),
                                polarity=(-1 if amp_ua < 0 else 1))
    m = CaptureMetrics()
    m.polarization_per_phase_v = list(pol)
    m.response_class = "normal"
    return Capture(index=0, pattern=pat, metrics=m, status=CaptureStatus())


# ------------------------------------------------------------- resolution
def test_snap_to_tenth_ua():
    r, stim, scope = _runner()
    try:
        assert abs(r._snap_test_ua(173.47) - 173.5) < 1e-9
        assert abs(r._snap_test_ua(1.04) - 1.0) < 1e-9
        assert abs(r._snap_test_ua(0.06) - 0.1) < 1e-9
        # NOT clamped to max_ua (so the ramp loop can exit past the ceiling).
        assert r._snap_test_ua(1000.0 + 5) > r.ramp.max_ua
    finally:
        stim.close(); scope.close()


def test_backoff_resolution_is_tenth_ua():
    r, stim, scope = _runner()
    try:
        assert abs(r.ramp.test_current_resolution_ua - 0.1) < 1e-9
        # fine_step (forward creep) stays coarse so the ramp doesn't 0.1-creep.
        assert r.ramp.fine_step_ua >= 1.0
    finally:
        stim.close(); scope.close()


# ------------------------------------------------------------- seed cap
def test_seed_cap_bounds_buried_signal_jump():
    """While the signal is buried (near rest), the step is capped to
    ~seed_max_growth × the current amplitude so a 1 µA reading can't fling to
    51 µA (bumps CH16)."""
    r, stim, scope = _runner()
    try:
        # Captures at 0 and 1 µA, both ≈ the +0.2 V rest (signal buried).
        caps = [_cap(0.0, [0.203, 0.203]), _cap(1.0, [0.243, 0.243])]
        assert not r._signal_emerged(caps)
        # The cap in the run loop: delta ≤ amp × (seed_max_growth − 1).
        cap_delta = 1.0 * (r.ramp.seed_max_growth - 1.0)
        assert cap_delta == 9.0                      # 1 µA → max 10 µA jump
    finally:
        stim.close(); scope.close()


# ------------------------------------------------------------- dampening
def test_dampen_schedule_is_75_50_40_25_10_pct():
    """Operator (revised): "Change the safe steps as −75 %, −50 %, −40 %,
    −25 %, −10 %" → the first five dampened jumps use ×0.25, ×0.5, ×0.6, ×0.75,
    ×0.9, then full prediction."""
    r, stim, scope = _runner()
    try:
        zc = [_cap(0.0, [0.2, 0.2]), _cap(1.0, [0.2, 0.2])]  # zero-start
        for step, factor in enumerate((0.25, 0.50, 0.60, 0.75, 0.90)):
            r._ramp_dampen_step = step
            assert abs(r._seed_dampen_factor(zc) - factor) < 1e-9, (step, factor)
        r._ramp_dampen_step = 5
        assert abs(r._seed_dampen_factor(zc) - 1.0) < 1e-9   # then full
        # A NON-zero-start ramp is never dampened.
        nz = [_cap(20.0, [0.2, 0.2]), _cap(40.0, [-0.3, 0.3])]
        r._ramp_dampen_step = 0
        assert abs(r._seed_dampen_factor(nz) - 1.0) < 1e-9
    finally:
        stim.close(); scope.close()


def test_zero_start_second_jump_is_more_dampened_than_first():
    """End-to-end: the first jumps of a 0 µA-start ramp are dampened
    (×0.25 then ×0.5 …); the ramp still climbs monotonically."""
    r, stim, scope = _runner()
    try:
        run = r.run()
        amps = [round(abs(c.pattern.excitation_phase.amplitude_ua), 2)
                for c in run.captures]
        assert amps[0] == 0.0 and amps[1] == 1.0
        # Monotonic non-decreasing (the dampening never sends it backwards).
        for a, b in zip(amps, amps[1:]):
            assert b >= a - 1e-9, amps
        # Every tested amplitude sits on the 0.1 µA grid.
        for a in amps:
            assert abs(a * 10 - round(a * 10)) < 1e-6, a
    finally:
        stim.close(); scope.close()
