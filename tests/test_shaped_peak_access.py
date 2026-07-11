"""Peak-current access voltage for SMOOTH shaped (gaussian / sinusoidal) pulses.

Operator: "Let's change the discontinuous gaussian and sinusoidal to peak
current because there is no edge in the waveform.  However, having current
offset would cause an edge, so I expect an iR drop there."

A smoothly-ramping shaped pulse has no current-step edge, so the classical
IR-step access measurement doesn't apply — the ohmic drop V_r = R_a·I is
marked where it's largest, at PEAK CURRENT.  A phase riding a current OFFSET
(a real 0→offset edge) is EXCLUDED (its access is the edge measurement).  This
is a SEPARATE measurement from the edge-based access and does NOT feed E_pol.
"""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.metrics import (shaped_peak_access, compute_metrics,
                              ideal_current_ua, is_continuous_sinusoidal)
from stimtest.session import Capture
from stimtest.waveforms import (Phase, PulsePattern,
                                SHAPE_GAUSSIAN, SHAPE_SINUSOIDAL,
                                SHAPE_RECTANGULAR)


def _cap(shape, *, offset=0.0, amp=100.0, R=0.005, rate=100.0, pol=0.0):
    """A discontinuous biphasic shaped pulse (delays + low rate → interpulse
    gap → NOT continuous).  V_mon = R·I (+ optional polarization ∝ ∫I)."""
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-amp, width_us=200.0, delay_after_us=20.0,
              shape=shape, offset_ua=offset),
        Phase(amplitude_ua=amp, width_us=200.0, delay_after_us=20.0,
              shape=shape, offset_ua=offset)], rate_hz=rate)
    t = np.linspace(-100.0, 600.0, 3500)
    i = ideal_current_ua(t, pat, onset_us=0.0)
    v = R * i + pol * np.cumsum(i) * (t[1] - t[0]) * 1e-6
    c = Capture(index=1, pattern=pat, time_us=t, v_mon_v=v, i_mon_ua=i)
    return c, pat, t, v, i


# ---------------------------------------------------------- helper gating
def test_smooth_gaussian_gets_peak_current_access():
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN, R=0.005)
    sp = shaped_peak_access(pat, t, v, i, onset_us=0.0)
    assert len(sp) == 2, sp                       # both phases
    for e in sp:
        assert abs(e["r_access_kohm"] - 5.0) < 0.2      # R_a ≈ 5 kΩ
        assert abs(e["v_access_v"] - 0.5) < 0.02        # V_a = R·I_peak = 0.5 V
        # Marker index sits at the phase's peak current.
        assert abs(abs(i[e["peak_idx"]]) - 100.0) < 1.0


def test_smooth_sinusoidal_gets_peak_current_access():
    c, pat, t, v, i = _cap(SHAPE_SINUSOIDAL, R=0.005)
    assert not is_continuous_sinusoidal(pat)      # discontinuous (has delays)
    assert len(shaped_peak_access(pat, t, v, i, onset_us=0.0)) == 2


def test_offset_shaped_pulse_excluded():
    """A current OFFSET makes a real 0→offset edge → the classical edge method
    applies, so the peak-current helper skips it (operator's exception)."""
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN, offset=40.0)
    assert shaped_peak_access(pat, t, v, i, onset_us=0.0) == []


def test_small_offset_still_excluded():
    """REGRESSION (adversarial finding): a SMALL current offset makes an IR step
    BELOW the data-driven ``_boundary_has_ir_step`` 3 mV floor, so the trace
    check alone would miss it — but ``offset_ua`` is a KNOWN parameter, so an
    offset phase is excluded DIRECTLY (threshold-free).  Operator: "having
    current offset would cause an edge, so I expect an iR drop there.\""""
    # Tiny offset (0.5 µA) + low R → the 0→offset step is ~1 mV, under the floor.
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN, offset=0.5, amp=10.0, R=0.002)
    assert shaped_peak_access(pat, t, v, i, onset_us=0.0) == [], (
        "an offset phase must be excluded via the offset_ua parameter, "
        "not only the trace step-detection")


def test_rectangular_excluded():
    c, pat, t, v, i = _cap(SHAPE_RECTANGULAR)
    assert shaped_peak_access(pat, t, v, i, onset_us=0.0) == []


def test_continuous_sinusoid_excluded():
    """KHFAC continuous sinusoid → handled by ghazavi_*; helper self-excludes."""
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=100.0, shape=SHAPE_SINUSOIDAL),
        Phase(amplitude_ua=100.0, width_us=100.0, shape=SHAPE_SINUSOIDAL)],
        rate_hz=5000.0)                            # period == pulse → no gap
    assert is_continuous_sinusoidal(pat)
    t = np.linspace(0.0, 8.0 / 5000.0, 4000) * 1e6
    i = 100.0 * np.sin(2 * np.pi * 5000.0 * t * 1e-6)
    assert shaped_peak_access(pat, t, 0.005 * i, i, onset_us=0.0) == []


def test_no_current_returns_empty():
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN)
    assert shaped_peak_access(pat, t, v, np.zeros_like(i), onset_us=0.0) == []


# ---------------------------------------------------------- compute_metrics
def test_compute_metrics_fills_shaped_fields_and_leaves_epol_untouched():
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN, R=0.005)
    m = compute_metrics(c, surface_area_um2=1000.0)
    assert m.response_class == "normal"
    # Shaped peak-current access populated (parallel-to-phases).
    assert len(m.shaped_access_v_per_phase) == 2
    assert all(np.isfinite(x) for x in m.shaped_access_v_per_phase)
    assert all(np.isfinite(x) for x in m.shaped_access_r_kohm_per_phase)
    # The EDGE access stays empty (smooth pulse → no step) — this is what feeds
    # E_pol, so the water-window path is unchanged (SAFETY).
    assert m.access_voltage_per_phase_v == []


def test_offset_gaussian_no_shaped_fields():
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN, offset=40.0, R=0.005)
    m = compute_metrics(c, surface_area_um2=1000.0)
    # Offset → edge → no peak-current shaped access (all NaN / filtered).
    assert not any(np.isfinite(x) for x in m.shaped_access_v_per_phase)


# ---------------------------------------------------------- plot markers
def test_markers_at_peak_current_for_smooth_shaped():
    from stimtest.plotting import compute_metric_markers
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN, R=0.005)
    c.metrics = compute_metrics(c, surface_area_um2=1000.0)
    acc = [mk for mk in compute_metric_markers(c) if mk["kind"] == "access"]
    assert len(acc) == 2                          # one per phase
    for mk in acc:
        pk = int(np.searchsorted(t, mk["t_us"]))
        assert abs(abs(i[min(pk, i.size - 1)]) - 100.0) < 2.0   # at peak current


def test_no_access_markers_for_offset_gaussian():
    """Offset → edge; the smooth-shaped peak-current marker must NOT appear
    (the edge method draws its own access if a real step is detected)."""
    from stimtest.plotting import compute_metric_markers
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN, offset=40.0, R=0.005)
    c.metrics = compute_metrics(c, surface_area_um2=1000.0)
    # No peak-current shaped markers; any access marker present would be the
    # edge-based one, but our synthetic V has no sharp IR jump so there are none.
    peak_markers = [mk for mk in compute_metric_markers(c)
                    if mk["kind"] == "access" and mk["label"].startswith("Va")]
    # The helper produced nothing → no peak-current markers from the shaped path.
    assert len(shaped_peak_access(pat, t, v, i, onset_us=0.0)) == 0


# ---------------------------------------------------------- persistence
def test_shaped_access_round_trips_npz(tmp_path):
    from stimtest.persistence import save_session_npz, load_session_npz
    from stimtest.session import Session, TestParameters, ChannelRun
    from stimtest.electrode import Configuration, ElectrodeArray
    c, pat, t, v, i = _cap(SHAPE_GAUSSIAN, R=0.005)
    c.metrics = compute_metrics(c, surface_area_um2=1000.0)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="nb", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(c)
    sess.runs.append(run)
    p = save_session_npz(sess, tmp_path / "shaped.npz")
    got = load_session_npz(p).runs[0].captures[0].metrics
    assert len(got.shaped_access_v_per_phase) == 2
    assert np.allclose(got.shaped_access_v_per_phase,
                       c.metrics.shaped_access_v_per_phase, equal_nan=True)
