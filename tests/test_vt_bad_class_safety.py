"""SAFETY: a broken/open/capacitive electrode that exceeds the water window must
STOP — even though its per-phase E_pol was cleared by compute_metrics.

Operator (exp_vt_max_check): "Why are CH02 and CH03 still having current
incremented despite greatly exceeding the potential limits?"  Root cause: a
bad-class capture has ``polarization_per_phase_v`` CLEARED, so the per-phase
``_potential_limit_hit`` check iterated an empty list and never tripped — a
BROKEN electrode (real, large polarization; CH03 hit V_mon −1.31 V vs a −0.60 V
limit) got ramped to max current unchecked.  The fix: fall back to the RAW
V_mon signed excursions vs the water window when E_pol is unavailable.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
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
    return VoltageTransientExperiment(
        session, stim, scope, ramp=RampPolicy(strategy="adaptive", max_ua=1000.0),
        cathodic_limit_v=-0.6, anodic_limit_v=0.8, polarization_tolerance_v=0.02)


def _bad_capture(vmon_peak, rclass="broken"):
    """A bad-class capture: E_pol CLEARED (as compute_metrics does), a cathodic
    V_mon excursion of ``vmon_peak`` V."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = vmon_peak * (t[m] / 200.0)      # ramps to vmon_peak, no IR step
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=200.0, polarity=-1))
    c.time_us = t; c.v_mon_v = v; c.i_mon_ua = np.zeros_like(t)
    c.metrics.response_class = rclass
    c.metrics.polarization_per_phase_v = []          # CLEARED (bad class)
    c.metrics.return_polarization_per_phase_v = []
    c.metrics.driving_voltage_v = abs(vmon_peak)
    return c


def test_broken_over_window_trips_the_limit_stop():
    """A BROKEN capture whose raw V_mon (−1.31 V) grossly exceeds the −0.60 V
    limit STOPS, even with its E_pol cleared (the CH02/CH03 fix)."""
    r = _runner()
    cap = _bad_capture(-1.31, rclass="broken")
    assert r._potential_limit_hit(cap) is True
    assert r._potential_limit_exceeded(cap) is True


def test_open_over_window_trips_the_limit_stop():
    """Same backstop for an 'open'-classified capture over the window."""
    r = _runner()
    cap = _bad_capture(-0.78, rclass="open")
    assert r._potential_limit_hit(cap) is True


def test_bad_class_under_window_does_not_trip():
    """A bad-class capture whose V_mon is BELOW the window does NOT trip — the
    backstop only fires on a genuine exceedance."""
    r = _runner()
    cap = _bad_capture(-0.30, rclass="broken")   # −0.30 V < −0.58 near edge
    assert r._potential_limit_hit(cap) is False
    assert r._potential_limit_exceeded(cap) is False


def test_normal_capture_still_uses_epol_not_raw_vmon():
    """A NORMAL electrode with a large IR access drop (raw V_mon over the
    window) but a SAFE E_pol must NOT trip — the backstop applies ONLY when
    E_pol is unavailable, so a normal electrode's access drop never false-stops
    it."""
    r = _runner()
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = -1.2                                  # raw V_mon −1.2 V (mostly access)
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=200.0, polarity=-1))
    c.time_us = t; c.v_mon_v = v; c.i_mon_ua = np.zeros_like(t)
    c.metrics.response_class = "normal"
    c.metrics.polarization_per_phase_v = [-0.40, 0.20]   # SAFE E_pol (present)
    c.metrics.return_polarization_per_phase_v = []
    assert r._potential_limit_hit(c) is False    # uses E_pol (−0.40 > −0.58)
