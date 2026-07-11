"""Continuous Pulsing (CP) — manual start/stop experiment.

Operator: "I want a 'Continuous Pulsing' experiment, where the user just
starts and stops the pulsing manually rather than a fixed number of
pulses."  CP = Short-Term Pulsing with an unbounded duration; the GUI
Stop button (the runner's abort flag) is the DESIGNED end, so the result
reports a clean completion (``aborted=False``), not an abort.
"""
from __future__ import annotations

import threading
import time

import pytest


def _build_runner(interval_s: float = 0.05):
    from stimtest.experiments.continuous_pulsing import (
        ContinuousPulsingExperiment, ContinuousPulsingPolicy)
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.waveforms import PulsePattern

    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    pattern = PulsePattern.biphasic(amplitude_ua=50.0, rate_hz=1000.0)
    array = ElectrodeArray.utah_4x4()
    config = Configuration.monopolar(1)
    test = TestParameters(experiment="CP", pattern=pattern,
                          configuration=config, array=array,
                          duration_s=0.0)
    session = Session(notebook="t", subject="cp", test=test)
    runner = ContinuousPulsingExperiment(
        session, stim, scope, amplitude_ua=50.0,
        policy=ContinuousPulsingPolicy(capture_interval_s=interval_s))
    return runner, stim, scope


def test_cp_runs_until_stop_and_reports_clean_completion():
    runner, stim, scope = _build_runner()
    result_box = {}

    def _go():
        result_box["result"] = runner.run()

    t = threading.Thread(target=_go, daemon=True)
    t.start()
    # Let it take a few snapshots, then "press Stop".
    deadline = time.time() + 10.0
    while time.time() < deadline:
        if runner.session.total_captures >= 2:
            break
        time.sleep(0.02)
    runner.abort()   # the GUI Stop button's path
    t.join(timeout=10.0)
    assert not t.is_alive(), "CP runner must exit promptly on Stop"

    result = result_box["result"]
    assert result is not None
    # Stop is the NORMAL end for CP — not an abort.
    assert result.aborted is False
    assert result.error is None
    assert len(result.captures) >= 2, "snapshots should accumulate"
    stim.close(); scope.close()


def test_cp_policy_is_unbounded():
    runner, stim, scope = _build_runner()
    assert runner.policy.duration_s == float("inf")
    stim.close(); scope.close()


def test_cp_registered_as_experiment():
    from stimtest.config import EXPERIMENTS
    assert "CP" in EXPERIMENTS
    assert "Continuous Pulsing" in EXPERIMENTS["CP"].label
