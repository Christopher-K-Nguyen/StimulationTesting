"""Pulse-onset anchoring for phase-time chains (Epol cursors + metrics).

The time axis is TRIGGER-relative.  With the I_mon-trigger fallback the
scope fires mid-pulse (the anodic edge for a cathodic-first pulse), so
chains anchored at t=0 sampled the WRONG phase: the operator saw Epol1
drawn where Epol2 belongs and Epol2 pushed off the end of the record.
``metrics.pulse_onset_us`` detects the true phase-1 onset from the data
and every phase-time chain anchors there; with a digital-sync trigger
the detector returns ≈0 and nothing changes.
"""
from __future__ import annotations

import numpy as np
import pytest


def _imon_trigger_capture():
    """Cathodic-first biphasic, 200 µs phases, 10 µs interphase, t=0 at
    the ANODIC edge (the I_mon-trigger fallback) → onset at −210 µs."""
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=50.0, phase_width_us=200.0,
                                polarity=-1, interphase_us=10.0,
                                discharge_us=100.0)
    t = np.linspace(-320.0, 320.0, 4000)
    i = np.zeros_like(t)
    i[(t >= -210.0) & (t < -10.0)] = -50.0   # cathodic before trigger
    i[(t >= 0.0) & (t < 200.0)] = +50.0      # anodic at trigger
    v = 0.004 * i                            # toy V_mon
    cap = Capture(index=0, pattern=pat)
    cap.time_us = t
    cap.i_mon_ua = i
    cap.v_mon_v = v
    return cap


def test_pulse_onset_detected_from_imon():
    from stimtest.metrics import pulse_onset_us
    cap = _imon_trigger_capture()
    onset = pulse_onset_us(cap.time_us, cap.i_mon_ua, cap.v_mon_v)
    assert onset == pytest.approx(-210.0, abs=1.0)


def test_pulse_onset_zero_for_digital_trigger():
    # Onset at t≈0 (digital sync) → detector ≈ 0 → legacy behavior.
    from stimtest.metrics import pulse_onset_us
    t = np.linspace(-100.0, 540.0, 4000)
    i = np.zeros_like(t)
    i[(t >= 0.0) & (t < 200.0)] = -50.0
    assert pulse_onset_us(t, i) == pytest.approx(0.0, abs=1.0)


def test_pulse_onset_idle_frame_falls_back_to_zero():
    from stimtest.metrics import pulse_onset_us
    t = np.linspace(-100.0, 100.0, 1000)
    assert pulse_onset_us(t, np.zeros_like(t), None) == 0.0


def test_epol_cursors_anchor_at_detected_onset():
    """Epol_k = onset + Σwidths(+delays) + 12 µs.  For the I_mon-trigger
    capture: Epol1 = −210+200+12 = +2 µs (NOT 212 — that's where Epol2's
    region lives), Epol2 = −210+200+10+200+12 = +212 µs (back inside the
    record — it previously fell off the end and vanished)."""
    from stimtest.plotting import _phase_end_times_us
    cap = _imon_trigger_capture()
    cursors = dict(_phase_end_times_us(cap))
    assert cursors["Epol1"] == pytest.approx(2.0, abs=1.5)
    assert cursors["Epol2"] == pytest.approx(212.0, abs=1.5)
    # Both inside the captured record → both get drawn.
    t = cap.time_us
    assert all(t[0] <= v <= t[-1] for v in cursors.values())
