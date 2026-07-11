"""VT stops ramping a NON-FARADAIC electrode early.

Operator: "the very apparent bad channel/combo (clearly capacitive
response with no resistance) STILL continues to be tested.  You can
easily test this by just linear regression of the first phase to give an
effective capacitance."

A capacitive / open / broken electrode has no Faradaic water window to
ramp toward — no resistance ⇒ no IR drop ⇒ E_pol is cleared (so the
water-window limit never trips) and a clean low-amplitude capacitive ramp
never reaches voltage compliance.  The old loop therefore ramped such a
channel all the way to ``max_ua``.  ``classify_response_and_ceff`` flags
the response (gotcha #58); the VT run loop now honours it and stops.
"""
from __future__ import annotations

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import (
    SimulatedOscilloscope, SimulatedStimulator)
from stimtest.session import Capture, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner():
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        # Ramp starts at the pattern's amplitude (10 µA) — starting_ua removed.
        ramp=RampPolicy(strategy="increment",
                        coarse_step_ua=10.0, max_ua=35.0))
    return runner, stim, scope


def _fake_capture(response_class):
    """Return a ``_one_capture`` stand-in that yields a capture pre-tagged
    with ``response_class`` (bypassing the real scope path)."""
    def _make(config, pattern, index):
        c = Capture(index=index, pattern=pattern)
        c.metrics.response_class = response_class
        if response_class != "normal":
            c.metrics.effective_capacitance_nf = 28.6
        return c
    return _make


def test_capacitive_response_stops_after_one_capture(monkeypatch):
    runner, stim, scope = _runner()
    try:
        monkeypatch.setattr(runner, "_one_capture",
                            _fake_capture("capacitive"))
        result = runner.run()
        run = result.session.runs[0]
        assert len(run.captures) == 1, (
            "a capacitive (no-resistance) electrode must stop after ONE "
            f"capture, not ramp to max_ua; got {len(run.captures)}")
        assert "capacitive" in (run.captures[0].status.notes or "").lower()
    finally:
        stim.close(); scope.close()


def test_open_and_broken_also_stop_early(monkeypatch):
    for cls in ("open", "broken"):
        runner, stim, scope = _runner()
        try:
            monkeypatch.setattr(runner, "_one_capture", _fake_capture(cls))
            result = runner.run()
            assert len(result.session.runs[0].captures) == 1, (
                f"a {cls} electrode must stop after ONE capture")
        finally:
            stim.close(); scope.close()


def test_normal_response_is_not_stopped_early(monkeypatch):
    """Control: a normal Faradaic electrode must NOT be tripped by the
    bad-response early-exit — it keeps ramping (here to max_ua, since the
    fake captures carry no E_pol limit)."""
    runner, stim, scope = _runner()
    try:
        monkeypatch.setattr(runner, "_one_capture", _fake_capture("normal"))
        result = runner.run()
        # 5 → 15 → 25 → 35 µA = 4 captures before amp exceeds max_ua.
        assert len(result.session.runs[0].captures) >= 3, (
            "a normal electrode must not be stopped by the bad-response "
            f"check; got {len(result.session.runs[0].captures)} captures")
    finally:
        stim.close(); scope.close()
