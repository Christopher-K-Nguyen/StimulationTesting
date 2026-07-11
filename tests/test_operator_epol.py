"""Operator-spec E_pol definition + plot/metric consistency.

Operator: "You should be using the time method when there is an interphase or
discharge delay available, otherwise, you use the subtraction of driving
potential with leading access voltage with respect for each phase."

So per phase:
  * trailing delay present  → TIME method (V at phase_end + depol)
  * delay-less              → driving_potential − leading_access (re-signed)

The same value must drive the metrics table, the on-plot Emc/Ema marker, AND
the water-window limit decision — previously the table/limit used the
derivative method while the plot marker used the time method, so they
disagreed (e.g. plot Emc −0.720 V vs table −0.835 V for the same pulse).
"""
from __future__ import annotations

import dataclasses

import numpy as np

from stimtest.metrics import polarization_per_phase
from stimtest.waveforms import PulsePattern


def _setval(t, v, tc, val, half=3.0):
    v[(t >= tc - half) & (t <= tc + half)] = val


def test_operator_uses_time_method_when_phase_has_trailing_delay():
    p = PulsePattern.biphasic(amplitude_ua=100.0, polarity=-1)
    ph0 = dataclasses.replace(p.phases[0], width_us=200.0,
                              delay_after_us=60.0, amplitude_ua=-100.0)
    ph1 = dataclasses.replace(p.phases[1], width_us=200.0,
                              delay_after_us=60.0, amplitude_ua=+100.0)
    p = dataclasses.replace(p, phases=[ph0, ph1])
    t = np.linspace(-100.0, 700.0, 8000)
    v = np.zeros_like(t)
    # phase1 ends at onset(0)+200 → sample 212 µs; phase2 ends at
    # 200+60+200=460 → sample 472 µs.
    _setval(t, v, 212.0, -0.50)
    _setval(t, v, 472.0, +0.30)
    res = polarization_per_phase(t, v, p, method="operator", onset_us=0.0)
    assert abs(res[0] - (-0.50)) < 0.05, res
    assert abs(res[1] - (+0.30)) < 0.05, res


def test_operator_uses_driving_minus_leading_when_no_delay():
    p = PulsePattern.biphasic(amplitude_ua=100.0, polarity=-1)
    ph0 = dataclasses.replace(p.phases[0], width_us=200.0,
                              delay_after_us=0.0, amplitude_ua=-100.0)  # NO delay
    ph1 = dataclasses.replace(p.phases[1], width_us=200.0,
                              delay_after_us=60.0, amplitude_ua=+100.0)
    p = dataclasses.replace(p, phases=[ph0, ph1])
    t = np.linspace(-100.0, 700.0, 8000)
    v = np.zeros_like(t)
    # phase1 (delay-less) → driving−leading; phase2 (delay) → time @ 412 µs
    # (phase2 ends at 0+200+0+200=400 → +12).
    _setval(t, v, 412.0, +0.30)
    res = polarization_per_phase(
        t, v, p, method="operator", onset_us=0.0,
        driving_per_phase=[2.2, 1.0], leading_access_per_phase=[1.4, 0.5])
    # cathodic phase 1: −(|2.2| − |1.4|) = −0.8
    assert abs(res[0] - (-0.80)) < 1e-6, res
    assert abs(res[1] - (+0.30)) < 0.05, res


def test_plot_marker_emc_equals_metric_value():
    """The on-plot Emc/Ema marker must report the SAME E_pol the metrics
    table uses — no more time-vs-derivative disagreement."""
    from stimtest.session import Capture
    from stimtest.metrics import compute_metrics
    from stimtest.plotting import compute_metric_markers

    p = PulsePattern.biphasic(amplitude_ua=200.0, polarity=-1)
    ph0 = dataclasses.replace(p.phases[0], width_us=200.0,
                              delay_after_us=60.0, amplitude_ua=-200.0)
    ph1 = dataclasses.replace(p.phases[1], width_us=200.0,
                              delay_after_us=60.0, amplitude_ua=+200.0)
    p = dataclasses.replace(p, phases=[ph0, ph1])
    t = np.linspace(-100.0, 700.0, 6000)
    # cathodic ramp during phase 1, anodic during phase 2, ~0 elsewhere
    v = np.zeros_like(t)
    v[(t >= 0) & (t < 200)] = -0.7
    v[(t >= 260) & (t < 460)] = +0.4
    i = np.zeros_like(t)
    i[(t >= 0) & (t < 200)] = -200.0
    i[(t >= 260) & (t < 460)] = +200.0
    cap = Capture(index=0, pattern=p)
    cap.time_us = t
    cap.v_mon_v = v
    cap.i_mon_ua = i
    compute_metrics(cap, surface_area_um2=5000.0)

    markers = compute_metric_markers(cap)
    polar = [m for m in markers if m["kind"] == "polar"]
    assert polar, "expected at least one Emc/Ema marker"
    pol = cap.metrics.polarization_per_phase_v
    # The first polar marker (phase 1) value must match polarization_per_phase_v[0].
    val = float(polar[0]["clauses"][0][2].split()[0])
    assert abs(val - pol[0]) < 1e-3, (val, pol[0])
