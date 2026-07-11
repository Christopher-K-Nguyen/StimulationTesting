"""Runner-side pause/resume (operator: "a pause button in between start and
stop … stop pulsing, hold, then resume … continues where it left off").

Verifies the shared ``ExperimentRunner.wait_if_paused`` checkpoint:
  * not paused → returns True, no stim halt;
  * paused → halts stim (stop_all), blocks until resume, restarts on resume;
  * aborted while paused → returns False (caller breaks);
  * reports the paused wall-clock in ``_last_pause_duration_s`` so time-bounded
    runners can exclude it from the run duration.
"""
from __future__ import annotations

import threading
import time

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.short_pulsing import (ShortPulsingExperiment,
                                                ShortPulsingPolicy)
from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                         SimulatedStimulator)
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner():
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    test = TestParameters(experiment="SP", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    r = ShortPulsingExperiment(sess, stim, scope, amplitude_ua=-50.0,
                               policy=ShortPulsingPolicy(duration_s=5.0,
                                                         capture_interval_s=1.0))
    return r, stim, scope


class _CountingStim:
    """Wraps a stim to count stop_all / start_all calls."""
    def __init__(self, inner):
        self._inner = inner
        self.stop_calls = 0
        self.start_calls = 0

    def stop_all(self):
        self.stop_calls += 1
        return self._inner.stop_all()

    def start_all(self):
        self.start_calls += 1
        return self._inner.start_all()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def test_wait_if_paused_noop_when_not_paused():
    r, stim, scope = _runner()
    try:
        assert r.wait_if_paused() is True
        assert r._last_pause_duration_s == 0.0
    finally:
        stim.close(); scope.close()


def test_pause_halts_and_resume_restarts():
    r, stim, scope = _runner()
    counting = _CountingStim(stim)
    r.stim = counting
    try:
        r.pause(True)
        assert r.paused is True
        result = {}

        def _worker():
            result["ret"] = r.wait_if_paused(
                restart=lambda: r.stim.start_all())

        th = threading.Thread(target=_worker); th.start()
        time.sleep(0.3)                      # worker is now blocked in pause
        assert th.is_alive()                 # still holding
        assert counting.stop_calls >= 1      # stimulation halted
        assert counting.start_calls == 0     # not restarted yet
        r.pause(False)                       # RESUME
        th.join(timeout=2.0)
        assert not th.is_alive()
        assert result["ret"] is True         # continue
        assert counting.start_calls == 1     # pulsing restarted
        assert r._last_pause_duration_s > 0.0
    finally:
        stim.close(); scope.close()


def test_abort_while_paused_returns_false():
    r, stim, scope = _runner()
    try:
        r.pause(True)
        result = {}

        def _worker():
            result["ret"] = r.wait_if_paused()

        th = threading.Thread(target=_worker); th.start()
        time.sleep(0.2)
        assert th.is_alive()
        r.abort()                            # Stop while paused
        th.join(timeout=2.0)
        assert not th.is_alive()
        assert result["ret"] is False        # caller should break
    finally:
        stim.close(); scope.close()


def test_sp_run_pauses_and_completes():
    """End-to-end: pause partway through an SP run, resume, and the run
    still finishes (the pause time is excluded from the duration)."""
    r, stim, scope = _runner()
    try:
        done = {}

        def _run():
            done["result"] = r.run()

        th = threading.Thread(target=_run); th.start()
        time.sleep(0.4)
        r.pause(True)
        time.sleep(0.5)
        r.pause(False)
        th.join(timeout=30.0)
        assert not th.is_alive(), "SP run did not finish after resume"
        assert done["result"] is not None
        assert done["result"].aborted is False
    finally:
        stim.close(); scope.close()
