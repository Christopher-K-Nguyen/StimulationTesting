"""``TestParameters.extras`` must survive the .npz round-trip.

It carries the run's whole descriptive context — the Setup snapshot (water-
window limits, electrode/coating, scope channel roles and couplings), the
device->Plexon cable map, the E_pol time delay, and for VT the resolved ramp
policy.  All of it was being DROPPED on save, which made an archive
un-self-describing: replaying the bench runs there was no way to tell whether
Adaptive or Regression produced them, so the strategy had to be inferred from
the amplitude sequence.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.persistence import load_session_npz, save_session_npz
from stimtest.session import Capture, ChannelRun, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _session(extras=None):
    pat = PulsePattern.biphasic(amplitude_ua=-100.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4(),
                          extras=extras or {})
    s = Session(notebook="nb", subject="subj", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    c = Capture(index=0, pattern=pat)
    c.time_us = np.linspace(0.0, 10.0, 8)
    c.v_mon_v = np.zeros(8)
    c.i_mon_ua = np.zeros(8)
    run.captures.append(c)
    s.add_run(run)
    return s


def test_extras_round_trip(tmp_path):
    extras = {
        "ramp_strategy": "regression",
        "ramp_policy": {"strategy": "regression", "max_ua": 1000.0,
                        "reached_min_consecutive": 1},
        "setup_snapshot": {"cathodic_limit_v": -0.6, "anodic_limit_v": 0.6,
                           "has_electrodes": True,
                           "channel_couplings": {"CH1": "DC", "CH2": "AC"}},
        "channel_map": {1: 15, 8: 1},
        "depolarization_us": 12.0,
    }
    p = tmp_path / "x.npz"
    save_session_npz(_session(extras), p)
    got = load_session_npz(p).test.extras
    assert got["ramp_strategy"] == "regression"
    assert got["ramp_policy"]["max_ua"] == 1000.0
    assert got["setup_snapshot"]["cathodic_limit_v"] == -0.6
    assert got["setup_snapshot"]["channel_couplings"]["CH2"] == "AC"
    assert got["depolarization_us"] == 12.0
    # dict keys go through JSON, so int keys come back as strings — the
    # channel map is read by NAME everywhere, so that is fine, but assert it
    # so the behaviour is documented rather than surprising.
    assert set(got["channel_map"]) == {"1", "8"}


def test_legacy_archive_without_extras_loads(tmp_path):
    """No "extras" key -> {} (never None: callers do ``extras.get(...)``)."""
    p = tmp_path / "y.npz"
    save_session_npz(_session({}), p)
    got = load_session_npz(p).test.extras
    assert got == {} and got is not None


def test_unserializable_value_does_not_break_the_save(tmp_path):
    """The .npz is the crash-recovery artifact — a stray metadata value must
    never be able to fail the write."""
    class Odd:
        def __repr__(self):
            return "<Odd>"
    p = tmp_path / "z.npz"
    save_session_npz(_session({"weird": Odd(), "ok": 1}), p)
    got = load_session_npz(p).test.extras
    assert got["ok"] == 1
    assert "Odd" in str(got["weird"])


def test_vt_runner_stamps_the_ramp_policy():
    from stimtest.experiments.voltage_transient import (
        RampPolicy, VoltageTransientExperiment)
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    try:
        sess = _session({})
        VoltageTransientExperiment(
            sess, stim, scope,
            ramp=RampPolicy(strategy="regression", max_ua=750.0))
        x = sess.test.extras
        assert x["ramp_strategy"] == "regression"
        assert x["ramp_policy"]["max_ua"] == 750.0
    finally:
        stim.close(); scope.close()
