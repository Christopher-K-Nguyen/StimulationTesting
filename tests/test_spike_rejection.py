"""Switching spikes / ringing must not mislead V_d or V_a at small currents.

Operator (with bench screenshots): "For small currents, spikes and ringing
is more apparent in the voltage response.  We need to ensure that the spike
does not mislead what the driving voltage/potentials, this is also important
for access voltage."

At small currents the phase-boundary switching transient (a sharp overshoot
+ a few µs of ringing) is LARGE relative to the signal, so a plain
max/argmin lands on the spike.  ``metrics._despike`` median-filters the
trace before reading settled values; the driving / access markers + the
metrics then track the SETTLED level, not the spike.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    import sys
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _spiky_small_current_capture():
    """Small (±10 µA) biphasic with interphase+discharge delays; ~20 mV IR
    steps, but ~120 mV switching SPIKES + ringing at every phase boundary."""
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=10.0)
    ph0 = dataclasses.replace(p.phases[0], amplitude_ua=-10.0,
                              width_us=200.0, delay_after_us=20.0)
    ph1 = dataclasses.replace(p.phases[1], amplitude_ua=+10.0,
                              width_us=200.0, delay_after_us=20.0)
    p = dataclasses.replace(p, phases=[ph0, ph1])
    t = np.linspace(-50.0, 560.0, 6000)
    v = np.zeros_like(t)

    def _ramp(mask, v0, v1):
        idx = np.where(mask)[0]
        if idx.size:
            v[idx] = np.linspace(v0, v1, idx.size)
    _ramp((t >= 0) & (t < 200), -0.018, -0.024)     # cathodic charge
    _ramp((t >= 220) & (t < 420), 0.016, 0.022)      # anodic charge
    for tb, amp in [(0, -0.13), (200, 0.11), (220, 0.12), (420, -0.10)]:
        v = v + np.exp(-((t - tb) / 0.6) ** 2) * amp            # spike
    for tb in [0, 200, 220, 420]:
        rr = (t >= tb) & (t < tb + 8)
        v[rr] += 0.03 * np.sin((t[rr] - tb) * 6) * np.exp(-(t[rr] - tb) / 3)
    v += np.random.default_rng(0).normal(0, 0.002, t.size)        # noise
    cap = Capture(index=0, pattern=p)
    cap.time_us = t
    cap.v_mon_v = v
    cap.i_mon_ua = np.zeros_like(t)
    for (a, b), amp in zip([(0, 200), (220, 420)], [-10, 10]):
        cap.i_mon_ua[(t >= a) & (t < b)] = amp
    return cap


def test_driving_voltage_rejects_spike(qapp):
    from stimtest.plotting import compute_metric_markers
    cap = _spiky_small_current_capture()
    vd = [m for m in compute_metric_markers(cap) if m["kind"] == "driving"]
    assert vd, "expected a V_d marker"
    val = abs(vd[0]["y"])
    # settled cathodic driving ≈ 0.024 V; the spike is ≈ 0.12 V.  V_d must
    # track the settled level, well below the spike.
    assert val < 0.05, f"V_d {val:.3f} V latched onto the ~0.12 V spike"
    assert 0.015 < val < 0.04, f"V_d {val:.3f} V should be ≈ the 0.024 V settled peak"


def test_access_voltage_rejects_spike(qapp):
    from stimtest.metrics import access_voltage_and_resistance, pulse_onset_us
    cap = _spiky_small_current_capture()
    t = cap.time_us
    onset = pulse_onset_us(t, cap.i_mon_ua, cap.v_mon_v)
    va, ra, acc = access_voltage_and_resistance(
        t, cap.v_mon_v, cap.pattern, onset_us=onset)
    # every access voltage must be small (≈ the ~20 mV IR step), never the
    # ~120 mV spike.
    for x in va:
        if np.isfinite(x):
            assert x < 0.06, f"access voltage {x:.3f} V latched onto a spike"


def test_despike_is_a_noop_on_clean_flat_plateau():
    """A clean (spike-free) trace is essentially unchanged — so normal
    captures don't regress."""
    from stimtest.metrics import _despike
    t = np.linspace(0, 200, 2000)
    v = np.full_like(t, -1.5)
    out = _despike(v, t)
    assert np.allclose(out, v, atol=1e-9)
