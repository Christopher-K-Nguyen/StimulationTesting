"""E_pol (Emc/Ema) marker position on shaped pulses matches the MATLAB method.

MATLAB ``getVoltageMetrics.m`` locates the potential excursion at
``potentialExcursion1_time = phaseWidth1 + depolTime`` and
``afterPhase2 + depolTime`` — NOMINAL phase times from t=0 (the time axis is
onset-aligned by the digital trigger).  Operator (screenshot): a sinusoid's
Emc/Ema landed ~13 µs late because ``pulse_onset_us`` fired at the 10 %-of-p2p
crossing (well into the smooth ramp), shifting the whole phase-time chain.  The
walk-back onset refinement returns ~0 for a pulse that starts at t=0, so E_pol
lands at the nominal phase times like MATLAB.
"""
from __future__ import annotations

import numpy as np

from stimtest.metrics import pulse_onset_us, ideal_current_ua, compute_metrics
from stimtest.plotting import compute_metric_markers
from stimtest.session import Capture, CaptureMetrics, CaptureStatus
from stimtest.waveforms import (PulsePattern, Phase, SHAPE_SINUSOIDAL,
                                SHAPE_GAUSSIAN, SHAPE_RECTANGULAR)
from stimtest.config import DEPOLARIZATION_TIME_US


def _cap(shape, W1=200.0, iph=20.0, W2=200.0, dd=20.0, noise=0.002):
    p0 = Phase(amplitude_ua=-1000.0, width_us=W1, delay_after_us=iph, shape=shape)
    p1 = Phase(amplitude_ua=+1000.0, width_us=W2, delay_after_us=dd, shape=shape)
    pat = PulsePattern(phases=[p0, p1], rate_hz=50.0)
    t = np.linspace(-300.0, 950.0, 3000)
    imon = ideal_current_ua(t, pat, onset_us=0.0)        # device-exact shape, onset=0
    vmon = imon / 1000.0 * 1.5
    rng = np.random.default_rng(1)
    vmon = vmon + rng.normal(0.0, noise, t.size)
    imon = imon + rng.normal(0.0, noise * 100, t.size)
    return Capture(index=5, pattern=pat, time_us=t, v_mon_v=vmon, i_mon_ua=imon,
                   metrics=CaptureMetrics(), status=CaptureStatus())


def _polar(cap):
    return {mk["label"]: mk["t_us"] for mk in compute_metric_markers(cap)
            if mk["kind"] == "polar"}


def test_onset_near_zero_for_onset_aligned_shaped_pulse():
    # A pulse that starts at t=0 must report onset ≈ 0 regardless of shape
    # (was ~13 µs late for the sine before the walk-back refinement).
    for shape in (SHAPE_SINUSOIDAL, SHAPE_GAUSSIAN, SHAPE_RECTANGULAR):
        cap = _cap(shape)
        onset = pulse_onset_us(cap.time_us, cap.i_mon_ua, cap.v_mon_v)
        assert abs(onset) < 3.0, (shape, onset)


def test_epol_at_nominal_phase_times_matlab_method():
    # MATLAB: Emc = phaseWidth1 + depol; Ema = phaseWidth1+iph+phaseWidth2+depol.
    depol = DEPOLARIZATION_TIME_US
    emc_expected = 200.0 + depol            # 212
    ema_expected = 200.0 + 20.0 + 200.0 + depol   # 432
    for shape in (SHAPE_SINUSOIDAL, SHAPE_GAUSSIAN, SHAPE_RECTANGULAR):
        pol = _polar(_cap(shape))
        assert abs(pol["Emc"] - emc_expected) < 4.0, (shape, pol)
        assert abs(pol["Ema"] - ema_expected) < 4.0, (shape, pol)


def _pat(iph, dd, W=200.0):
    from stimtest.waveforms import PulsePattern, Phase, SHAPE_RECTANGULAR
    p0 = Phase(amplitude_ua=-100.0, width_us=W, delay_after_us=iph,
               shape=SHAPE_RECTANGULAR)
    p1 = Phase(amplitude_ua=+100.0, width_us=W, delay_after_us=dd,
               shape=SHAPE_RECTANGULAR)
    return PulsePattern(phases=[p0, p1], rate_hz=50.0)


def _operator_epol(pat, *, driving, lead, trail):
    from stimtest.metrics import polarization_per_phase
    t = np.linspace(0.0, 900.0, 1000)
    etrace = t.copy()          # e_trace == time, so _time_sample returns the time
    return polarization_per_phase(
        t, etrace, pat, method="operator", depol_us=12.0, onset_us=0.0,
        driving_per_phase=driving, leading_access_per_phase=lead,
        trailing_epol_per_phase=trail)


def test_epol_decision_both_long_delays_use_time_method():
    # iph=20, dd=20 (both ≥ 12) → TIME method: phase_end + 12.
    r = _operator_epol(_pat(20.0, 20.0), driving=[-0.5, 0.5],
                       lead=[0.3, 0.3], trail=[np.nan, np.nan])
    assert abs(r[0] - 212.0) < 2.0 and abs(r[1] - 432.0) < 2.0, r


def test_epol_decision_short_interphase_uses_trailing_access():
    # iph=5 (<12, short) → phase-1 TRAILING access; dd=20 → phase-2 TIME.
    r = _operator_epol(_pat(5.0, 20.0), driving=[-0.5, 0.5],
                       lead=[0.3, 0.3], trail=[0.111, np.nan])
    assert abs(r[0] - 0.111) < 1e-6, r         # phase-1 trailing access value
    # phase-2 TIME sample = phase2_end + 12 = (200+5+200) + 12 = 417.
    assert abs(r[1] - 417.0) < 2.0, r


def test_epol_decision_no_interphase_phase1_driving_minus_lead():
    # iph=0, dd=0 → phase 1 = driving − leading (has pre-pulse lead);
    #              phase 2 = NaN (no leading, no discharge).
    r = _operator_epol(_pat(0.0, 0.0), driving=[-0.5, 0.5],
                       lead=[0.3, 0.3], trail=[np.nan, np.nan])
    assert abs(r[0] - (-0.2)) < 1e-6, r        # sgn(-) × (0.5 − 0.3)
    assert np.isnan(r[1]), r                    # UNDETERMINED


def test_epol_decision_no_interphase_but_discharge_uses_trailing():
    # iph=0 → phase 1 = driving − leading; dd=5 (short) → phase 2 TRAILING access
    # (operator: "unless there is a discharge delay then use second trailing").
    r = _operator_epol(_pat(0.0, 5.0), driving=[-0.5, 0.5],
                       lead=[0.3, 0.3], trail=[np.nan, 0.222])
    assert abs(r[0] - (-0.2)) < 1e-6, r
    assert abs(r[1] - 0.222) < 1e-6, r


def test_imon_trigger_negative_onset_still_detected():
    # I_mon-trigger fallback: the capture is shifted so the pulse begins at a
    # NEGATIVE time; the onset must track it (not clamp to 0).
    shape = SHAPE_RECTANGULAR
    p0 = Phase(amplitude_ua=-1000.0, width_us=200.0, delay_after_us=20.0, shape=shape)
    p1 = Phase(amplitude_ua=+1000.0, width_us=200.0, delay_after_us=20.0, shape=shape)
    pat = PulsePattern(phases=[p0, p1], rate_hz=50.0)
    t = np.linspace(-300.0, 950.0, 3000)
    shift = -80.0                            # pulse starts at t = -80 µs
    imon = ideal_current_ua(t, pat, onset_us=shift)
    vmon = imon / 1000.0 * 1.5
    onset = pulse_onset_us(t, imon, vmon)
    assert abs(onset - shift) < 3.0, onset
