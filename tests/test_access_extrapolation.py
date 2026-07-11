"""Shared access-voltage method: before/after edge-extrapolation to the pure
IR step — used by BOTH the experiment metrics and the calibration.

Operator: "calibration should use the same method for access points as the
experiment" → both unified on ``access_step_by_extrapolation`` (the pure-IR
edge-extrapolation), which subtracts the cap ramp instead of reading the
settling plateau (which carries the cap charge accumulated past the edge).
"""
from __future__ import annotations

import numpy as np

from stimtest.metrics import access_step_by_extrapolation


def test_extrapolation_recovers_ir_not_plateau():
    # Series R-C load: after the edge, V_mon = IR + (I/C)·t (a linear cap ramp
    # on top of the pure IR step).  The two-sided extrapolation back to the edge
    # moment must recover the PURE IR, NOT the settling-plateau read (biased
    # high by the cap ramp).
    dt = 0.032                                  # µs (32 ns)
    t = np.arange(0.0, 220.0, dt)
    n = t.size
    edge = 3000                                 # edge sample (~96 µs)
    IR = 0.5                                     # pure IR step (V)
    cap_slope = 0.010                            # V/µs cap ramp
    v = np.zeros(n)
    idx = np.arange(n)
    post = idx >= edge
    dt_since = (idx - edge) * dt
    rise = np.clip(dt_since / 1.0, 0.0, 1.0)     # 1 µs source rise on the step
    v[post] = IR * rise[post] + cap_slope * dt_since[post]

    peak = edge + 10
    acc = edge + 50
    step = access_step_by_extrapolation(t, v, peak, acc)
    assert abs(step - IR) < 0.03 * IR, step      # within 3 % of the true IR

    # The naive settling-plateau read carries the cap charge → biased HIGH.
    plateau = abs(v[acc] - v[edge - 100])
    assert plateau > step


def test_extrapolation_nan_on_short_windows():
    t = np.arange(0.0, 1.0, 0.032)
    v = np.zeros_like(t)
    assert np.isnan(access_step_by_extrapolation(t, v, 2, 3))     # windows too short
    assert np.isnan(access_step_by_extrapolation(t, v, 5, 4))     # peak >= acc


def test_experiment_access_uses_extrapolation():
    # A biphasic pulse into an R-C load: the experiment access_voltage_and_
    # resistance must return the extrapolated IR step (≈ I·R), not the plateau.
    from stimtest.metrics import access_voltage_and_resistance
    from stimtest.waveforms import PulsePattern, Phase, SHAPE_RECTANGULAR
    R = 4990.0
    I = 100e-6
    IR = I * R                                   # 0.499 V
    cap_slope = 0.008                            # V/µs
    dt = 0.05
    t = np.arange(-30.0, 430.0, dt)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t < 200.0)
    v[m1] = -(IR + cap_slope * t[m1])            # cathodic: IR step + cap ramp
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=200, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=100.0, width_us=200, shape=SHAPE_RECTANGULAR),
    ], rate_hz=50.0)
    va, ra, _ = access_voltage_and_resistance(t, v, pat, onset_us=0.0)
    assert va, "expected an access point"
    # Leading phase-1 V_a ≈ pure IR (0.499 V), within ~10 % (localization slack).
    assert abs(va[0] - IR) < 0.10 * IR, va[0]
    assert abs(ra[0] - R / 1000.0) < 0.10 * (R / 1000.0), ra[0]   # ≈ 4.99 kΩ
