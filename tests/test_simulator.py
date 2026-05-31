"""Unit tests for the simulator backends and the VT runner end-to-end."""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import RampPolicy, VoltageTransientExperiment
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


def test_simulator_round_trip():
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    scope.bind_stimulator(stim)

    pat = PulsePattern.biphasic(100, polarity=-1)
    stim.load_channel(1, pat)
    stim.set_monitor_channel(1)

    acq = scope.single_capture()
    assert acq.time_us.size > 100
    assert "CH1" in acq.channels
    assert np.any(np.abs(acq.channels["CH1"]) > 1e-3)
    stim.close(); scope.close()


def test_vt_runner_end_to_end_finds_max_q_inj():
    array = ElectrodeArray.utah_4x4()
    config = Configuration.bipolar(active=10, ret=11)
    pattern = PulsePattern.biphasic(amplitude_ua=5, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=config, array=array)
    session = Session(notebook="t", subject="s", test=test)

    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()

    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(starting_ua=5, coarse_step_ua=20, fine_step_ua=5, max_ua=400),
    )
    result = runner.run()
    assert len(result.captures) > 0
    # End-to-end smoke: the runner should have walked through several
    # amplitudes, populated CaptureMetrics on each, and recorded a
    # finite ``max_q_inj``.  The simulator's electrode polarization
    # model is intentionally saturating — it asymptotes just below the
    # SIROF water window (e.g. -0.6 V cathodic) without crossing it,
    # mimicking a real electrode at its operating ceiling.  A
    # production sweep on real hardware reliably trips
    # ``reached_potential_limit`` because real electrode
    # polarization keeps climbing past the limit; the simulator
    # mathematically saturates, so we don't require the safety
    # interlocks to fire here.
    last = result.captures[-1]
    assert len(last.metrics.polarization_per_phase_v) > 0, (
        "polarization should be computed for the last capture")
    assert np.isfinite(result.max_q_inj)
    stim.close(); scope.close()
