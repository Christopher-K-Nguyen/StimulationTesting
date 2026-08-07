"""Harris 2019 chronopotentiometry capacitive/Faradaic decomposition.

Under constant current the dE/dt slope tells the split: constant dE/dt =
capacitive charging (i_c = A·C_dl·dE/dt), a dip toward 0 = Faradaic.  The
double-layer capacitance C_dl is read from the constant-dE/dt window ONLY
(defensible, unlike the removed full-pulse C_eff); the charge split is
first-order/approximate.  Validated on real data: C_dl is amplitude-INDEPENDENT
(an electrode property) — that's the key correctness signature.

The slope MUST come from the ACTIVE-ELECTRODE POTENTIAL, not the driving
voltage (operator).  Harris's ``i_c = A·C_dl·dE/dt`` is written at ONE
interface; V_mon = E_act − E_ret, so ``dV_mon/dt`` also carries the return
electrode's charging.  ``compute_metrics`` therefore reports these fields only
when E_act was recorded or is derivable as V_mon + E_ret.
"""
from __future__ import annotations

import math

import numpy as np

from stimtest.metrics import (chronopotentiometry_charge_transfer,
                              compute_metrics)
from stimtest.session import Capture
from stimtest.waveforms import (PulsePattern, Phase, SHAPE_RECTANGULAR,
                                SHAPE_GAUSSIAN)

AREA = 2000.0            # µm²
T = np.linspace(-50.0, 450.0, 5000)


def _pat(amp_ua):
    return PulsePattern(phases=[
        Phase(amplitude_ua=amp_ua, width_us=200, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=-amp_ua, width_us=200, shape=SHAPE_RECTANGULAR),
    ], rate_hz=50.0)


def _ramp(slope_v_per_us, plateau_at_us=None):
    """Cathodic V_active: linear charge at ``slope`` over phase 1, optionally
    flattening to a plateau at ``plateau_at_us`` (a Faradaic dip)."""
    v = np.zeros_like(T)
    m1 = (T >= 0.0) & (T <= 200.0)
    v[m1] = -slope_v_per_us * T[m1]
    if plateau_at_us is not None:
        hold = (T > plateau_at_us) & (T <= 200.0)
        v[hold] = -slope_v_per_us * plateau_at_us
    return v


def test_c_dl_amplitude_independent():
    """C_dl = I/(A·dE/dt) — at DOUBLE the current the slope doubles, so C_dl is
    unchanged.  This amplitude-independence is the physical hallmark of a real
    double-layer capacitance (verified on the bench: 8.2–8.9 mF/cm² over
    55→960 µA)."""
    ct1 = chronopotentiometry_charge_transfer(
        T, _ramp(0.0005), _pat(-100.0), onset_us=0.0, area_um2=AREA)
    ct2 = chronopotentiometry_charge_transfer(
        T, _ramp(0.0010), _pat(-200.0), onset_us=0.0, area_um2=AREA)
    # I=100µA, dE/dt=0.0005 V/µs=500 V/s → C_dl = 100e-6/(2e-5·500) = 10 mF/cm².
    assert abs(ct1.c_dl_mf_per_cm2 - 10.0) < 0.5
    assert abs(ct2.c_dl_mf_per_cm2 - 10.0) < 0.5
    assert abs(ct1.c_dl_mf_per_cm2 - ct2.c_dl_mf_per_cm2) < 0.5


def test_pure_capacitor_has_low_faradaic_fraction():
    """A full-phase linear ramp (constant dE/dt, never dips) = purely
    capacitive → Faradaic fraction ≈ 0 and no resolvable Faradaic onset."""
    ct = chronopotentiometry_charge_transfer(
        T, _ramp(0.0005), _pat(-100.0), onset_us=0.0, area_um2=AREA)
    assert ct.faradaic_fraction < 0.05
    assert math.isnan(ct.faradaic_onset_us)


def test_faradaic_onset_and_split():
    """A ramp that PLATEAUS at 100 µs (dE/dt→0 = Faradaic reaction holds the
    potential) → Faradaic onset detected near 100 µs and a substantial
    Faradaic charge fraction."""
    ct = chronopotentiometry_charge_transfer(
        T, _ramp(0.0005, plateau_at_us=100.0), _pat(-100.0),
        onset_us=0.0, area_um2=AREA)
    assert np.isfinite(ct.faradaic_onset_us)
    assert 90.0 <= ct.faradaic_onset_us <= 120.0
    assert ct.faradaic_fraction > 0.3
    # Q_total = |I|·W = 100 µA · 200 µs = 20 nC.
    assert abs(ct.q_total_nc - 20.0) < 0.5
    assert ct.q_capacitive_nc + ct.q_faradaic_nc <= ct.q_total_nc + 1e-6


def test_no_area_or_zero_amp_is_nan():
    ct0 = chronopotentiometry_charge_transfer(
        T, _ramp(0.0005), _pat(-100.0), onset_us=0.0, area_um2=0.0)
    assert math.isnan(ct0.c_dl_mf_per_cm2)
    ctz = chronopotentiometry_charge_transfer(
        T, _ramp(0.0005), _pat(0.0), onset_us=0.0, area_um2=AREA)
    assert math.isnan(ctz.c_dl_mf_per_cm2)


def _normal_capture(pat, v, *, e_act=True, e_ret=None):
    """``e_act=True`` mirrors the trace into ``e_act_v`` — i.e. an ideal return
    (no polarization), so V_mon == E_act numerically while the analysis sees a
    genuine active-electrode potential.  ``e_act=False`` gives the common
    V_mon/I_mon-only capture, where the decomposition must be withheld."""
    i = np.zeros_like(T)
    i[(T >= 0.0) & (T <= 200.0)] = pat.phases[0].amplitude_ua
    i[(T > 200.0) & (T <= 400.0)] = pat.phases[1].amplitude_ua
    c = Capture(index=0, pattern=pat)
    c.time_us = T; c.v_mon_v = v; c.i_mon_ua = i
    if e_act:
        c.e_act_v = np.asarray(v, dtype=float)
    if e_ret is not None:
        c.e_ret_v = np.asarray(e_ret, dtype=float)
    return c


def test_compute_metrics_populates_for_normal():
    """A NORMAL capture (IR step + polarization ramp) gets the decomposition
    fields via compute_metrics."""
    v = np.zeros_like(T)
    m = (T >= 0.0) & (T <= 200.0)
    v[m] = -0.3 - 0.0004 * T[m]        # 0.3 V IR step + capacitive ramp
    c = _normal_capture(_pat(-200.0), v)
    mt = compute_metrics(c, surface_area_um2=AREA)
    assert mt.response_class == "normal"
    assert np.isfinite(mt.c_dl_mf_per_cm2)
    assert mt.c_dl_mf_per_cm2 > 0


def test_non_normal_and_shaped_leave_ct_nan():
    """Charge-transfer decomposition is skipped for a non-normal class (open,
    here a flat plateau) and for a non-rectangular (non-constant-current)
    pulse — the i_c = A·C_dl·dE/dt model doesn't apply."""
    # Flat plateau → open → no decomposition.
    vflat = np.zeros_like(T); m = (T >= 0.0) & (T <= 200.0)
    vflat[m] = -0.10 * np.clip(T[m] / 15.0, 0.0, 1.0)
    c_open = _normal_capture(_pat(-1.0), vflat)
    mo = compute_metrics(c_open, surface_area_um2=AREA)
    assert mo.response_class == "open"
    assert math.isnan(mo.c_dl_mf_per_cm2)
    # Shaped (gaussian) pulse — not constant current → skipped even if normal.
    shaped = PulsePattern(phases=[
        Phase(amplitude_ua=-200.0, width_us=200, shape=SHAPE_GAUSSIAN),
        Phase(amplitude_ua=200.0, width_us=200, shape=SHAPE_GAUSSIAN),
    ], rate_hz=50.0)
    v = np.zeros_like(T); m = (T >= 0.0) & (T <= 200.0)
    v[m] = -0.3 - 0.0004 * T[m]
    c_sh = _normal_capture(shaped, v)
    ms = compute_metrics(c_sh, surface_area_um2=AREA)
    assert math.isnan(ms.c_dl_mf_per_cm2)


def test_charge_transfer_round_trips(tmp_path):
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.persistence import load_session_npz, save_session_npz
    from stimtest.session import ChannelRun, Session, TestParameters
    v = np.zeros_like(T); m = (T >= 0.0) & (T <= 200.0)
    v[m] = -0.3 - 0.0004 * T[m]
    c = _normal_capture(_pat(-200.0), v)
    compute_metrics(c, surface_area_um2=AREA)
    assert np.isfinite(c.metrics.c_dl_mf_per_cm2)
    test = TestParameters(experiment="VT", pattern=_pat(-200.0),
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    s = Session(notebook="t", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(c); s.add_run(run)
    p = tmp_path / "ct.npz"; save_session_npz(s, p)
    lm = load_session_npz(p).runs[0].captures[0].metrics
    assert abs(lm.c_dl_mf_per_cm2 - c.metrics.c_dl_mf_per_cm2) < 1e-6
    assert abs(lm.faradaic_fraction - c.metrics.faradaic_fraction) < 1e-6


def test_plot_charge_transfer_builds():
    """The dE/dt decomposition view renders 3 panels without error."""
    import matplotlib
    matplotlib.use("Agg")
    from stimtest import plotting
    v = np.zeros_like(T); m = (T >= 0.0) & (T <= 200.0)
    v[m] = -0.3 - 0.0004 * T[m]
    c = _normal_capture(_pat(-200.0), v)
    fig = plotting.plot_charge_transfer(c, area_um2=AREA)
    assert len(fig.axes) == 3
    # Tolerates a missing area (C_dl just reads n/a) and a bad capture.
    fig2 = plotting.plot_charge_transfer(c, area_um2=None)
    assert len(fig2.axes) == 3


# --------------------------------------------------------------------------
# The slope source: active-electrode potential, never the driving voltage.
# --------------------------------------------------------------------------

def _capacitive_trace():
    v = np.zeros_like(T)
    m = (T >= 0.0) & (T <= 200.0)
    v[m] = -0.3 - 0.0004 * T[m]        # 0.3 V IR step + capacitive ramp
    return v


def test_vmon_only_capture_withholds_the_decomposition():
    """The common V_mon/I_mon-only setup: no active-electrode potential exists,
    so C_dl / Faradaic onset / split must all stay NaN.  ``dV_mon/dt`` is the
    DIFFERENCE of the two electrodes' slopes, so ``I/(A·dV_mon/dt)`` would be a
    series combination reported as if it were the active C_dl."""
    c = _normal_capture(_pat(-200.0), _capacitive_trace(), e_act=False)
    m = compute_metrics(c, surface_area_um2=AREA)
    assert m.response_class == "normal", "the gate must be the only reason"
    assert math.isnan(m.c_dl_mf_per_cm2)
    assert math.isnan(m.faradaic_onset_us)
    assert math.isnan(m.capacitive_charge_nc)


def test_recorded_eact_enables_the_decomposition():
    c = _normal_capture(_pat(-200.0), _capacitive_trace(), e_act=True)
    m = compute_metrics(c, surface_area_um2=AREA)
    assert np.isfinite(m.c_dl_mf_per_cm2) and m.c_dl_mf_per_cm2 > 0


def test_derived_eact_enables_the_decomposition():
    """E_act = V_mon + E_ret is the exact differential identity, so a derived
    active potential is as valid as a recorded one."""
    v = _capacitive_trace()
    e_ret = np.full_like(T, 0.05)                  # a real, offset return
    c = _normal_capture(_pat(-200.0), v, e_act=False, e_ret=e_ret)
    m = compute_metrics(c, surface_area_um2=AREA)
    assert np.isfinite(m.c_dl_mf_per_cm2) and m.c_dl_mf_per_cm2 > 0
    # A CONSTANT E_ret shifts the potential but not its slope, so C_dl matches
    # the recorded-E_act case exactly — the derivative is offset-invariant.
    ref = compute_metrics(_normal_capture(_pat(-200.0), v, e_act=True),
                          surface_area_um2=AREA)
    assert abs(m.c_dl_mf_per_cm2 - ref.c_dl_mf_per_cm2) < 1e-6


def test_polarizing_return_would_have_corrupted_c_dl():
    """Why the gate matters, quantitatively.  Give the return its own charging
    ramp: the true active C_dl is unchanged, but a V_mon-derived slope is the
    SUM of both ramps and under-reports C_dl.  The gate stops that number from
    ever being produced."""
    v_act = _capacitive_trace()                     # active: -0.4 mV/µs
    e_ret = np.zeros_like(T)
    m1 = (T >= 0.0) & (T <= 200.0)
    e_ret[m1] = 0.0004 * T[m1]                      # return charges the other way
    v_mon = v_act - e_ret                           # V_mon = E_act - E_ret
    truth = chronopotentiometry_charge_transfer(
        T, v_act, _pat(-200.0), onset_us=0.0, area_um2=AREA)
    wrong = chronopotentiometry_charge_transfer(
        T, v_mon, _pat(-200.0), onset_us=0.0, area_um2=AREA)
    assert np.isfinite(truth.c_dl_mf_per_cm2) and np.isfinite(wrong.c_dl_mf_per_cm2)
    # Slope doubles → C_dl halves.  A ~2x error, silently.
    assert wrong.c_dl_mf_per_cm2 < 0.6 * truth.c_dl_mf_per_cm2


def test_polaris_view_labels_a_vmon_only_capture_honestly():
    import matplotlib
    matplotlib.use("Agg")
    from stimtest import plotting
    c = _normal_capture(_pat(-200.0), _capacitive_trace(), e_act=False)
    fig = plotting.plot_charge_transfer(c, area_um2=AREA)
    assert len(fig.axes) == 3, "the dE/dt shape is still a useful view"
    texts = " ".join(t.get_text() for t in fig.texts)
    assert "needs E" in texts, "the withheld C_dl must say why"
    assert "mF/cm" not in texts, "no C_dl number from a V_mon slope"
    # With a real active potential the numbers come back.
    c2 = _normal_capture(_pat(-200.0), _capacitive_trace(), e_act=True)
    t2 = " ".join(t.get_text() for t in plotting.plot_charge_transfer(
        c2, area_um2=AREA).texts)
    assert "mF/cm" in t2
