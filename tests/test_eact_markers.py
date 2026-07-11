"""Access V/R + electrode-polarization markers ride the E_act trace when
the active-electrode waveform is recorded.

Operator: "access voltage, access resistance, and electrode polarization on
the experiment plot should be on Eact (based on the active electrode), if
the waveform is available."  The driving voltage V_d stays on V_mon (it's
the total active-vs-return driving potential, not an active-electrode
quantity).  Without E_act, everything falls back to V_mon.
"""
from __future__ import annotations

import numpy as np

from stimtest.plotting import compute_metric_markers
from stimtest.session import Capture
from stimtest.metrics import compute_metrics
from stimtest.waveforms import PulsePattern, Phase, SHAPE_RECTANGULAR


def _pat():
    return PulsePattern(phases=[
        Phase(amplitude_ua=-200.0, width_us=200, delay_after_us=20,
              shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=200.0, width_us=200, delay_after_us=20,
              shape=SHAPE_RECTANGULAR),
    ], rate_hz=50.0)


def _ir_ramp(t, peak):
    """IR step (0.4·peak) + linear ramp to peak in phase 1, mirrored in
    phase 2 — a normal electrode with a clear access step."""
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t < 200.0)
    v[m1] = peak * 0.4 + peak * 0.6 * (t[m1] / 200.0)
    m2 = (t >= 220.0) & (t < 420.0)
    v[m2] = -peak * 0.4 - peak * 0.6 * ((t[m2] - 220.0) / 200.0)
    return v


def _capture(with_eact):
    t = np.linspace(-100.0, 600.0, 2800)
    c = Capture(index=0, pattern=_pat())
    c.time_us = t
    i = np.zeros_like(t)
    i[(t >= 0.0) & (t < 200.0)] = -200.0
    i[(t >= 220.0) & (t < 420.0)] = 200.0
    c.i_mon_ua = i
    c.v_mon_v = _ir_ramp(t, -0.5)          # V_mon peaks near -0.5 V
    if with_eact:
        c.e_act_v = _ir_ramp(t, -0.3)      # E_act a DISTINCT trace, peaks -0.3
    compute_metrics(c, surface_area_um2=5000.0)
    return c, t


def _near_trace(marker, t, trace, tol=0.03):
    i = int(np.argmin(np.abs(t - marker["t_us"])))
    return abs(marker["y"] - float(trace[i])) <= tol


def test_access_epol_ride_eact_when_available():
    c, t = _capture(with_eact=True)
    markers = compute_metric_markers(c)
    acc = [m for m in markers if m["kind"] == "access"]
    pol = [m for m in markers if m["kind"] == "polar"]
    vd = [m for m in markers if m["kind"] == "driving"]
    assert acc, "expected access markers"
    assert pol, "expected polarization markers"
    ea = np.asarray(c.e_act_v)
    vm = np.asarray(c.v_mon_v)
    # Access + polarization markers sit on the E_act trace (≈ -0.3 peak),
    # NOT the V_mon trace (≈ -0.5 peak).
    for m in acc + pol:
        assert _near_trace(m, t, ea), f"{m['label']} not on E_act (y={m['y']})"
        assert not _near_trace(m, t, vm, tol=0.05) or _near_trace(m, t, ea), \
            f"{m['label']} should be on E_act, not V_mon"
    # V_d stays on V_mon.
    for m in vd:
        assert _near_trace(m, t, vm, tol=0.05), f"V_d not on V_mon (y={m['y']})"


def test_markers_fall_back_to_vmon_without_eact():
    c, t = _capture(with_eact=False)
    markers = compute_metric_markers(c)
    vm = np.asarray(c.v_mon_v)
    for m in markers:
        if m["kind"] in ("access", "polar", "driving"):
            assert _near_trace(m, t, vm, tol=0.05), \
                f"{m.get('label')} should be on V_mon (y={m['y']})"


def test_access_epol_ride_derived_eact_from_vmon_plus_eret():
    # No DIRECT E_act channel, but E_ret IS recorded → E_act is DERIVED
    # (V_mon + E_ret) for the trace AND the markers/metrics, so the markers
    # ride the derived active-electrode trace the operator sees — NOT V_mon.
    t = np.linspace(-100.0, 600.0, 2800)
    c = Capture(index=0, pattern=_pat())
    c.time_us = t
    i = np.zeros_like(t)
    i[(t >= 0.0) & (t < 200.0)] = -200.0
    i[(t >= 220.0) & (t < 420.0)] = 200.0
    c.i_mon_ua = i
    c.v_mon_v = _ir_ramp(t, -0.5)           # V_mon peaks near -0.5 V
    c.e_ret_v = np.full_like(t, 0.35)       # return rest potential +0.35 V
    # e_act_v stays None → must be derived from V_mon + E_ret.
    compute_metrics(c, surface_area_um2=5000.0)
    markers = compute_metric_markers(c)
    derived = np.asarray(c.v_mon_v) + np.asarray(c.e_ret_v)   # E_act
    vm = np.asarray(c.v_mon_v)
    acc = [m for m in markers if m["kind"] == "access"]
    pol = [m for m in markers if m["kind"] == "polar"]
    assert acc and pol, "expected access + polarization markers"
    for m in acc + pol:
        assert _near_trace(m, t, derived), \
            f"{m['label']} not on derived E_act (y={m['y']})"
        # Derived E_act sits +0.35 V above V_mon → clearly NOT on V_mon.
        assert not _near_trace(m, t, vm, tol=0.1), \
            f"{m['label']} landed on V_mon, not derived E_act"
    # V_d still on V_mon (total active-vs-return driving potential).
    for m in markers:
        if m["kind"] == "driving":
            assert _near_trace(m, t, vm, tol=0.05)
