"""Device→Plexon cable channel translation (simple number conversion).

Operator: "When pulsing CH01, it should be device CH01 … this should not
require using the DLL, it should be simple converting the channel number."
The runner installs a device→Plexon map and every per-channel COMMAND targets
the mapped Plexon channel, while captures stay labelled by the device channel
(MATLAB ``channelStim = File.Parameters.Channels.Plexon(channelNum)``).
"""
from __future__ import annotations

import pytest

from stimtest.experiments.base import _ChannelMappedStimulator


class _FakeStim:
    def __init__(self):
        self.calls = []
        self.answer = 42

    def load_channel(self, ch, pat): self.calls.append(("load", ch))
    def set_monitor_channel(self, ch): self.calls.append(("mon", ch))
    def set_repetitions(self, ch, n): self.calls.append(("reps", ch, n))
    def start_channel(self, ch): self.calls.append(("start", ch))
    def stop_channel(self, ch): self.calls.append(("stop", ch))
    def start_all(self): self.calls.append(("start_all",))
    def stop_all(self): self.calls.append(("stop_all",))


def test_proxy_translates_per_channel_and_delegates_rest():
    f = _FakeStim()
    w = _ChannelMappedStimulator(f, {1: 9, 9: 1})   # swap device 1 <-> 9
    w.load_channel(1, None)          # device 1 -> Plexon 9
    w.set_monitor_channel(1)         # device 1 -> Plexon 9
    w.set_repetitions(1, 0)          # device 1 -> Plexon 9
    w.start_channel(9)               # device 9 -> Plexon 1
    w.stop_channel(2)                # device 2 unmapped -> identity
    w.start_all()                    # all-channel op: NOT translated
    w.stop_all()
    assert f.calls == [
        ("load", 9), ("mon", 9), ("reps", 9, 0),
        ("start", 1), ("stop", 2), ("start_all",), ("stop_all",),
    ]
    # Everything else delegates to the wrapped stimulator unchanged.
    assert w.answer == 42


def _runner():
    from stimtest.electrode import ElectrodeArray
    from stimtest.experiments.voltage_transient import VoltageTransientExperiment
    from stimtest.hardware.simulator import (
        SimulatedOscilloscope, SimulatedStimulator)
    from stimtest.session import Configuration, Session, TestParameters
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=50.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    return VoltageTransientExperiment(session, stim, scope), stim


def test_set_channel_map_noop_for_identity():
    runner, stim = _runner()
    runner.set_channel_map({})           # identity → no wrap
    assert runner.stim is stim
    runner.set_channel_map(None)         # None → no wrap
    assert runner.stim is stim
    runner.set_channel_map({3: 3})       # identity entry only → no wrap
    assert runner.stim is stim


def test_set_channel_map_wraps_and_is_idempotent():
    runner, stim = _runner()
    runner.set_channel_map({1: 9, 9: 1})
    assert isinstance(runner.stim, _ChannelMappedStimulator)
    assert object.__getattribute__(runner.stim, "_stim") is stim
    assert runner._channel_map == {1: 9, 9: 1}
    # Re-installing must unwrap the old proxy first (never double-wrap).
    runner.set_channel_map({2: 4, 4: 2})
    assert isinstance(runner.stim, _ChannelMappedStimulator)
    assert object.__getattribute__(runner.stim, "_stim") is stim
    assert runner._channel_map == {2: 4, 4: 2}
    # Back to identity unwraps entirely.
    runner.set_channel_map({})
    assert runner.stim is stim
