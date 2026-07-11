"""Harris 2019 chronopotentiometry capacitive/Faradaic decomposition.

Under constant current the dE/dt slope tells the split: constant dE/dt =
capacitive charging (i_c = A·C_dl·dE/dt), a dip toward 0 = Faradaic.  The
double-layer capacitance C_dl is read from the constant-dE/dt window ONLY
(defensible, unlike the removed full-pulse C_eff); the charge split is
first-order/approximate.  Validated on real data: C_dl is amplitude-INDEPENDENT
(an electrode property) — that's the key correctness signature.
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


def _normal_capture(pat, v):
    i = np.zeros_like(T)
    i[(T >= 0.0) & (T <= 200.0)] = pat.phases[0].amplitude_ua
    i[(T > 200.0) & (T <= 400.0)] = pat.phases[1].amplitude_ua
    c = Capture(index=0, pattern=pat)
    c.time_us = T; c.v_mon_v = v; c.i_mon_ua = i
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
