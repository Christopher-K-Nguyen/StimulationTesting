"""Unit tests for waveform metrics on synthetic captures."""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.metrics import (
    charge_injection_mc_per_cm2, compute_metrics,
    detect_capacitive_to_faradaic, driving_voltage_from_vmon,
)
from stimtest.session import Capture
from stimtest.waveforms import PulsePattern


def _synthetic_capture(amp_ua=200.0, r_a_kohm=2.0, c_dl_nf=60.0,
                       phase_us=200, interphase_us=20, discharge_us=20,
                       sample_us=0.4, t_pre=50.0, t_post=200.0):
    """Build a Capture whose V_mon trace looks like a real biphasic pulse."""
    pat = PulsePattern.biphasic(amp_ua, phase_us, interphase_us,
                                discharge_us, polarity=-1)
    t_us, i_ua = pat.to_timeseries(t_pre_us=t_pre, t_post_us=t_post,
                                   sample_period_us=sample_us)
    t_s = t_us * 1e-6
    i_a = i_ua * 1e-6
    # RC integration for E_dl
    e_dl = np.zeros_like(t_s)
    R_pol = 5e6
    for k in range(1, t_s.size):
        dt = t_s[k] - t_s[k - 1]
        de = (i_a[k - 1] - e_dl[k - 1] / R_pol) / (c_dl_nf * 1e-9)
        e_dl[k] = e_dl[k - 1] + de * dt
    v_mon = e_dl + i_a * (r_a_kohm * 1e3) * 2  # x2 for active+return
    cap = Capture(index=0, pattern=pat,
                  time_us=t_us, v_mon_v=v_mon,
                  i_mon_ua=i_ua, e_act_v=e_dl, e_ret_v=-e_dl)
    return cap


def test_charge_injection_units():
    pat = PulsePattern.biphasic(amplitude_ua=250, phase_width_us=200, polarity=-1)
    q_ph_nc, q_inj = charge_injection_mc_per_cm2(pat, surface_area_um2=5000)
    # 250 µA * 200 µs = 50 nC -> q_inj = 50 nC / (5000e-8 cm²)
    # = 1e-3 mC / 5e-5 cm² = 20 mC/cm²... wait, IEEE NER paper: 250 µA on 5000 µm² is 1 mC/cm²
    # Q_inj formula: Q_ph (nC) * 1e-6 mC/nC / (area cm²) = mC/cm²
    # 50 nC * 1e-6 = 5e-5 mC; area = 5000 * 1e-8 = 5e-5 cm²; ratio = 1.0 mC/cm² ✓
    assert q_ph_nc == pytest.approx(50.0)
    assert q_inj == pytest.approx(1.0, rel=1e-3)


def test_driving_voltage_recoverable_from_vmon():
    cap = _synthetic_capture(amp_ua=100)
    vd = driving_voltage_from_vmon(cap.v_mon_v)
    assert vd > 0
    # max |V_mon| should be at the peak of phase 1 (cathodic) — finite & positive
    assert np.isfinite(vd)


def test_compute_metrics_populates_all_fields():
    cap = _synthetic_capture(amp_ua=200)
    m = compute_metrics(cap, surface_area_um2=5000)
    assert np.isfinite(m.driving_voltage_v)
    assert np.isfinite(m.charge_injection_mc_per_cm2)
    assert m.charge_injection_mc_per_cm2 == pytest.approx(0.8, rel=1e-2)
    assert len(m.access_voltage_per_phase_v) == 4   # 2 phases × (leading, trailing)
    assert len(m.polarization_per_phase_v) == 2     # biphasic
    assert np.isfinite(m.driving_capacitance_mf_per_cm2)


def test_capacitive_to_faradaic_detector_fires():
    n = 200
    t = np.linspace(0, 1, n)
    # Build a current trace that decays initially then accelerates upward
    i = np.zeros(n)
    i[:100] = 1e-9 * np.exp(-t[:100] * 5)            # capacitive decay
    i[100:] = 1e-9 + 5e-8 * (t[100:] - t[100]) ** 2  # quadratic rise
    assert detect_capacitive_to_faradaic(t, i)


def test_capacitive_to_faradaic_detector_quiet():
    n = 200
    t = np.linspace(0, 1, n)
    i = 1e-9 * np.exp(-t * 5)  # pure decay
    assert not detect_capacitive_to_faradaic(t, i)
