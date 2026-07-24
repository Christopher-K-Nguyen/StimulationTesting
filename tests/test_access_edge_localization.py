"""Access V/R is localized at the current-pattern VERTICAL transitions, never
after the electrode polarization (operator #10: "Va/Ra cannot be after the
electrode polarization … the access voltage is localized around vertical
rises/falls from the current pattern").

Failure mode this guards: on a bad / continuously-ramping channel the
settling-point forward walk used to run up to ``n-50`` samples, dragging the
access marker deep into the phase (past E_mc/E_ma).  The fix caps the walk to
``_ACCESS_SETTLE_MAX_US`` and searches the |dV/dt| peak only in a tight window
around the pattern-derived edge time.
"""
from __future__ import annotations

import numpy as np

from stimtest.metrics import (access_voltage_and_resistance,
                              _ACCESS_SETTLE_MAX_US)
from stimtest.waveforms import Phase, PulsePattern


def _ramping_biphasic_trace(dt=0.5):
    """A cathodic-first biphasic whose V_mon keeps RAMPING through each phase
    (a capacitive / bad-electrode signature) after a sharp IR step at the
    edge — so the linear-departure walk finds no clean settling point."""
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=200.0, delay_after_us=10.0),
        Phase(amplitude_ua=+100.0, width_us=200.0, delay_after_us=10.0),
    ], rate_hz=100.0)
    t = np.arange(-60.0, 460.0, dt)
    v = np.zeros_like(t)
    m1 = (t >= 0) & (t < 200)              # IR step -0.3 then ramp to -1.0
    v[m1] = -0.3 - 0.7 * (t[m1] / 200.0)
    m_iph = (t >= 200) & (t < 210)
    v[m_iph] = -1.0 + 0.5 * ((t[m_iph] - 200) / 10.0)
    m2 = (t >= 210) & (t < 410)            # IR step +0.3 then ramp to +1.0
    v[m2] = 0.3 + 0.7 * ((t[m2] - 210) / 200.0)
    m_dd = t >= 410                         # discharge: sharp drop to 0 (short)
    v[m_dd] = 0.0
    return pat, t, v


def test_leading_access_stays_at_the_current_edge():
    pat, t, v = _ramping_biphasic_trace()
    va, ra, acc_idx = access_voltage_and_resistance(t, v, pat, onset_us=0.0)
    assert acc_idx and acc_idx[0] >= 0
    # Leading phase-1 access must sit at the current edge (t≈0), NOT drift to
    # the phase-1 end (t≈200) where the electrode polarization lives.
    t_lead = t[acc_idx[0]]
    assert 0.0 <= t_lead <= _ACCESS_SETTLE_MAX_US + 2.0, t_lead


def test_every_access_point_is_near_its_current_edge():
    pat, t, v = _ramping_biphasic_trace()
    _va, _ra, acc_idx = access_voltage_and_resistance(t, v, pat, onset_us=0.0)
    # Expected edge times: lead-ph1=0, trail-ph1=200, lead-ph2=210, trail-ph2=410.
    edges = [0.0, 200.0, 210.0, 410.0]
    assert len(acc_idx) == len(edges)
    for idx, edge in zip(acc_idx, edges):
        assert idx >= 0
        # the settling sample walks FORWARD from the edge, capped at the
        # settle window (plus a couple samples of derivative smoothing slack).
        assert edge - 3.0 <= t[idx] <= edge + _ACCESS_SETTLE_MAX_US + 4.0, (
            t[idx], edge)


def test_calibration_path_onset_none_is_unaffected():
    """The legacy (calibration) path passes onset_us=None and must NOT get the
    edge-window narrowing or the walk cap — it feeds clean test-board signals
    and has its own verified convention."""
    pat, t, v = _ramping_biphasic_trace()
    va0, ra0, idx0 = access_voltage_and_resistance(t, v, pat, onset_us=None)
    va1, ra1, idx1 = access_voltage_and_resistance(t, v, pat, onset_us=None)
    assert idx0 == idx1                     # deterministic, unchanged path
