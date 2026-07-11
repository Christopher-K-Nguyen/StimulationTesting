"""Conditional effective capacitance C_eff = I/(dV/dt).

Operator: "Show capacitance since it is so linear … but only when the
response is entirely capacitive, open circuit, or broken.  Do not include
access voltage or resistance [or electrode polarization]."

So each capture is classified ``normal`` / ``capacitive`` / ``open``:
  * normal     → access V/R + E_pol, C_eff = NaN
  * capacitive → linear ramp, no IR step → C_eff only (no access V/R / E_pol)
  * open       → V_mon railed at high impedance → C_eff only
"""
from __future__ import annotations

import math

import numpy as np

from stimtest.metrics import classify_response_and_ceff, compute_metrics
from stimtest.session import Capture
from stimtest.waveforms import (
    PulsePattern, Phase, SHAPE_RECTANGULAR, SHAPE_GAUSSIAN, SHAPE_SINUSOIDAL)


def _pat(amp_ua=-200.0):
    return PulsePattern(phases=[
        Phase(amplitude_ua=amp_ua, width_us=200, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=-amp_ua, width_us=200, shape=SHAPE_RECTANGULAR),
    ], rate_hz=50.0)


def _cap_mirrored_v(t, peak=-1.4):
    """Pure-capacitor biphasic V_mon: a linear charge ramp in phase 1 and a
    MIRRORED (opposite-slope) discharge ramp in phase 2, no IR step — the
    operator's open/capacitive signature."""
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = peak * (t[m1] / 200.0)                       # 0 → peak  (charge)
    m2 = (t > 200.0) & (t <= 400.0)
    v[m2] = peak - peak * ((t[m2] - 200.0) / 200.0)      # peak → 0  (mirror discharge)
    return v


def test_capacitive_linear_ramp_gives_ceff():
    t = np.linspace(-50.0, 450.0, 2000)
    v = _cap_mirrored_v(t)                # mirrored charge/discharge, NO IR step
    cls, ceff = classify_response_and_ceff(
        t, v, _pat(-200.0), onset_us=0.0, driving_v=1.4, compliance_v=9.0)
    assert cls == "capacitive"
    # slope = 1.4/200 = 0.007 V/µs → C = 200µA / 0.007 / 1000 ≈ 28.6 nF
    assert abs(ceff - (200.0 / 0.007 / 1000.0)) < 1.0


def test_normal_step_plus_ramp_is_normal_no_ceff():
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = -0.5 - 0.9 * (t[m] / 200.0)    # IR step (0.5 V) + ramp
    cls, ceff = classify_response_and_ceff(
        t, v, _pat(-200.0), onset_us=0.0, driving_v=1.4, compliance_v=9.0)   # significant access step
    assert cls == "normal"
    assert math.isnan(ceff)


def test_low_signal_near_noise_floor_is_not_broken():
    """A VT ramp STARTING near 0 µA: V_mon at 1 µA is ~3 mV (the averaged
    noise floor) with no resolvable IR step, so the classifier must DECLINE
    to classify (return 'normal') rather than flag it broken and stop the
    ramp (operator: "testing maximum VT starting at 0 µA, but it mistakenly
    thought the channel was broken" — V_mon was 2.7-3.5 mV at the 1 µA floor)."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = -0.003 * (t[m] / 200.0)        # tiny ~3 mV ramp, no IR step
    cls, ceff = classify_response_and_ceff(
        t, v, _pat(-1.0), onset_us=0.0, driving_v=0.003, compliance_v=9.0)
    assert cls == "normal"
    assert math.isnan(ceff)


def test_low_signal_guard_does_not_hide_a_real_broken_response():
    """The guard fires ONLY at the noise floor — a broken (curved, no-access)
    response at a NORMAL signal level (well above the guard) still classifies
    broken, so a genuinely dead electrode is still caught as the ramp climbs."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = -0.4 * (t[m] / 200.0) ** 2     # ~400 mV CURVED ramp, no IR step
    cls, _ = classify_response_and_ceff(
        t, v, _pat(-50.0), onset_us=0.0, driving_v=0.4, compliance_v=9.0)
    assert cls == "broken"


def test_open_railed_low_current_is_open():
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = -8.5                            # railed near 9 V compliance
    cls, ceff = classify_response_and_ceff(
        t, v, _pat(-5.0), onset_us=0.0, driving_v=8.5,   # only 5 µA
        compliance_v=9.0)
    assert cls == "open"                  # 8.5 V / 5 µA = 1.7 MΩ ≫ 50 kΩ


def test_low_impedance_near_compliance_not_open():
    # A functional low-Z electrode legitimately near compliance at HIGH
    # current must NOT be misread as open.
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = -7.8                            # near rail, but at 1000 µA
    cls, _ = classify_response_and_ceff(
        t, v, _pat(-1000.0), onset_us=0.0, driving_v=7.8, compliance_v=9.0)   # 7.8 V / 1000 µA = 7.8 kΩ
    assert cls == "normal"


def test_highly_polarizable_low_z_modest_step_is_normal():
    # Operator: "CH16 is a good channel" but it was mislabelled "broken".  A
    # HIGHLY-POLARISABLE healthy electrode: a REAL ohmic IR step (72 mV) at a
    # LOW impedance (8.6 kΩ), then a CURVED Faradaic ramp to a much larger peak
    # (426 mV).  Its IR fraction (~0.17) is just UNDER the 0.20 "clear step"
    # threshold and its ramp is curved (not straight), so the old logic called
    # it broken.  The low impedance + real (modest ≥ 0.10) step keep it NORMAL.
    # (The truly-bad CH03/04/06/09 were all ≥ 268 kΩ = extreme-Z, unaffected.)
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    tau = t[m1] / 200.0
    v[m1] = -0.072 - 0.354 * tau ** 1.5      # 72 mV IR step + curved ramp → 426 mV
    m2 = (t > 200.0) & (t <= 400.0)
    tau2 = (t[m2] - 200.0) / 200.0
    v[m2] = -0.426 + 0.426 * tau2 ** 1.5      # anodic recovery toward 0
    cls, ceff = classify_response_and_ceff(
        t, v, _pat(-50.0), onset_us=0.0, driving_v=0.426, compliance_v=12.0)
    assert cls == "normal", cls              # 8.5 kΩ + real step → healthy
    assert math.isnan(ceff)


def test_extreme_voltage_high_impedance_is_bad_even_with_step():
    """An EXTREME voltage per unit current (very high effective impedance) is
    a bad electrode even when an ohmic step is present — operator: "CH03 is
    also bad for its extreme voltage" (11.9 V / 50 µA ≈ 236 kΩ).  The same
    waveform at HIGH current (low Z) is a functional electrode → normal, so
    it's the IMPEDANCE, not the raw voltage, that flags bad."""
    t = np.linspace(-50.0, 450.0, 2000)
    m = (t >= 0.0) & (t <= 200.0)
    v = np.zeros_like(t)
    v[m] = -4.0 - 8.0 * (t[m] / 200.0)     # 4 V step + ramp to 12 V
    cls_hi, _ = classify_response_and_ceff(
        t, v, _pat(-50.0), onset_us=0.0, driving_v=12.0, compliance_v=15.0)  # 240 kΩ
    assert cls_hi != "normal"              # extreme impedance → bad
    cls_lo, _ = classify_response_and_ceff(
        t, v, _pat(-1000.0), onset_us=0.0, driving_v=12.0, compliance_v=15.0)  # 12 kΩ
    assert cls_lo == "normal"


def test_saturating_exponential_is_open_with_rc_fit():
    """A SATURATING R‖C exponential PLATEAUS — V=V∞(1−e^(−t/τ)) reaches I·R and
    stops changing (dE/dt→0) — so under the FLATNESS rule (Harris 2019
    chronopotentiometry) it is OPEN, not broken.  OPEN electrodes still get the
    parallel-R‖C fit (the plateau IS an R·C: R = V∞/I, C = τ/R, τ — operator:
    "fit the exponential response with RC … report all three")."""
    from stimtest.metrics import compute_metrics, fit_parallel_rc
    from stimtest.session import Capture
    t = np.linspace(-50.0, 450.0, 2000)
    # V∞ = 12 V, τ = 33 µs, at 50 µA → R = 12/50µA = 240 kΩ, C = τ/R ≈ 0.14 nF.
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = -12.0 * (1.0 - np.exp(-t[m1] / 33.0))
    i = np.zeros_like(t)
    i[(t >= 0.0) & (t <= 200.0)] = -50.0
    i[(t > 220.0) & (t <= 420.0)] = 50.0
    c = Capture(index=0, pattern=_pat(-50.0))
    c.time_us = t; c.v_mon_v = v; c.i_mon_ua = i
    mt = compute_metrics(c, surface_area_um2=5000.0)
    assert mt.response_class == "open"                       # plateaus → open
    assert abs(mt.rc_fit_resistance_kohm - 240.0) < 30.0     # R ≈ 240 kΩ
    assert np.isfinite(mt.effective_capacitance_nf)          # RC-fit C (not NaN)
    assert abs(mt.rc_fit_tau_us - 33.0) < 6.0                # τ ≈ 33 µs

    # The standalone helper recovers the same values.
    r_k, c_n, tau, r2 = fit_parallel_rc(
        t, v, _pat(-50.0), onset_us=0.0, amp_ua=50.0)
    assert r2 > 0.99 and abs(r_k - 240.0) < 30.0 and abs(tau - 33.0) < 6.0


def test_rc_fit_fields_round_trip(tmp_path):
    from stimtest.persistence import save_session_npz, load_session_npz
    from stimtest.session import Capture, ChannelRun, Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    pat = _pat(-50.0)
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t); m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = -12.0 * (1.0 - np.exp(-t[m1] / 33.0))
    i = np.zeros_like(t); i[(t >= 0.0) & (t <= 200.0)] = -50.0
    c = Capture(index=0, pattern=pat); c.time_us = t; c.v_mon_v = v; c.i_mon_ua = i
    compute_metrics(c, surface_area_um2=5000.0)
    assert c.metrics.response_class == "open"       # saturating exp plateaus → open
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    s = Session(notebook="t", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(c); s.add_run(run)
    p = tmp_path / "rc.npz"; save_session_npz(s, p)
    lm = load_session_npz(p).runs[0].captures[0].metrics
    assert abs(lm.rc_fit_resistance_kohm - c.metrics.rc_fit_resistance_kohm) < 1e-6
    assert abs(lm.rc_fit_tau_us - c.metrics.rc_fit_tau_us) < 1e-6
    assert lm.response_class == "open"


def _shaped_pat(shape, amp_ua=-200.0):
    return PulsePattern(phases=[
        Phase(amplitude_ua=amp_ua, width_us=200, shape=shape),
        Phase(amplitude_ua=-amp_ua, width_us=200, shape=shape),
    ], rate_hz=50.0)


def test_healthy_gaussian_not_classified_open():
    """Operator: a HEALTHY gaussian symmetric waveform must NOT be misread as
    open/capacitive — the linear-ramp / IR-step heuristics only hold for a
    rectangular current.  A moderate-voltage gaussian (well below any broken
    impedance) classifies normal."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = _cap_mirrored_v(t)                 # ~1.4 V ramp; would read capacitive if rectangular
    cls, ceff = classify_response_and_ceff(
        t, v, _shaped_pat(SHAPE_GAUSSIAN, -200.0),   # 1.4 V / 200 µA = 7 kΩ → healthy
        onset_us=0.0, driving_v=1.4, compliance_v=9.0)
    assert cls == "normal"
    assert math.isnan(ceff)


def test_railed_shaped_pulse_is_broken():
    """Operator (bumps CH04/07/14/15/16): a SHAPED pulse whose electrode drives
    an EXTREME voltage per current (here a gaussian railing to −8.5 V at 5 µA →
    1.7 MΩ) is now flagged broken — the shape-agnostic excursion-impedance
    detector catches broken/open electrodes the rectangular gate used to let
    read 'normal'."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t); m = (t >= 0.0) & (t <= 200.0)
    v[m] = -8.5                            # 8.5 V / 5 µA = 1.7 MΩ → broken/open
    cls, ceff = classify_response_and_ceff(
        t, v, _shaped_pat(SHAPE_GAUSSIAN, -5.0),
        onset_us=0.0, driving_v=8.5, compliance_v=9.0)
    assert cls == "broken"


def test_sinusoidal_pulse_not_classified_capacitive():
    """A sinusoidal symmetric waveform whose V_mon is a clean mirrored ramp
    (would read capacitive on a rectangular pulse) classifies normal."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = _cap_mirrored_v(t)                 # would be "capacitive" if rectangular
    cls, ceff = classify_response_and_ceff(
        t, v, _shaped_pat(SHAPE_SINUSOIDAL, -200.0),
        onset_us=0.0, driving_v=1.4, compliance_v=9.0)
    assert cls == "normal"
    assert math.isnan(ceff)


def test_curved_ramp_no_access_is_broken():
    """A CURVED (non-linear) ramp that starts from ~0 with NO ohmic IR step is
    a BROKEN electrode — not capacitive (the line isn't straight) and not
    normal (no access voltage).  Operator (CH03): "CH03 is still bad … not an
    open channel that exhibits pure capacitance … exhibits no access voltage."
    """
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    # Strongly curved (quadratic) charge from 0 — no IR step, and not a
    # straight line (high residual) → broken.
    v[m1] = -1.4 * (t[m1] / 200.0) ** 2
    m2 = (t > 200.0) & (t <= 400.0)
    v[m2] = -1.4 + 1.4 * ((t[m2] - 200.0) / 200.0) ** 2
    cls, _ = classify_response_and_ceff(
        t, v, _pat(-200.0), onset_us=0.0, driving_v=1.4, compliance_v=9.0)
    assert cls == "broken"           # no access + curved → degraded electrode
    assert cls != "capacitive"


def test_symmetric_straight_ramp_high_z_is_broken():
    """SUPERSEDES the old "straight symmetric ramp = open" (which was BACKWARDS).
    Under the FLATNESS rule (operator's electrode-array check + Harris 2019
    chronopotentiometry) a dead-straight high-Z ramp that KEEPS RAMPING (the
    potential never plateaus — dE/dt stays ≠0) is BROKEN, like the operator's
    CH01.  Only a FLAT plateau (dE/dt→0) is open.  (A LOW-Z straight mirrored
    ramp is still 'capacitive' — see test_capacitive_linear_ramp_gives_ceff.)"""
    t = np.linspace(-50.0, 450.0, 2000)
    v = _cap_mirrored_v(t, peak=-5.0)     # 0→-5 V charge, keeps ramping (not flat)
    cls, _ = classify_response_and_ceff(
        t, v, _pat(-50.0), onset_us=0.0, driving_v=5.0, compliance_v=9.0)  # 5 V / 50 µA = 100 kΩ
    assert cls == "broken"


def test_flat_plateau_is_open():
    """The operator's electrode-array OPEN signature (CH04): a fast step to a
    FLAT plateau (dE/dt→0 — the potential settles to I·R and stops changing →
    not charging → disconnected / resistive) is OPEN under the flatness rule,
    even at a LOW, non-railed voltage.  This is the case the old
    'straight ramp = open' logic got backwards (it's the plateau, not the
    straightness, that marks open)."""
    t = np.linspace(-50.0, 450.0, 4000)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = -0.10 * np.clip(t[m1] / 15.0, 0.0, 1.0)   # step to -100 mV in 15 µs → FLAT
    m2 = (t > 220.0) & (t <= 420.0)
    v[m2] = -0.10 + 0.10 * np.clip((t[m2] - 220.0) / 15.0, 0.0, 1.0)
    cls, _ = classify_response_and_ceff(
        t, v, _pat(-1.0), onset_us=0.0, driving_v=0.10, compliance_v=9.0)
    assert cls == "open"                  # 100 mV / 1 µA = 100 kΩ, flat plateau


def test_slope_asymmetric_straight_ramp_is_broken():
    """A straight phase-1 ramp whose phase-2 SLOPE is NOT a mirror (>2× off)
    is BROKEN, not open — a finite R carrying current unbalances the
    charge/discharge slopes.  NOTE the former RETURN-TO-BASELINE criterion
    was RETIRED (operator's PBP bench run: the pattern's DISCHARGE phase does
    the reset, so phase-2 end never returns to baseline for ANY channel —
    healthy included — which made the straight-ramp→open path dead code and
    misread the operator's straight open ramp CH04 as broken).  The slope
    MIRROR is the surviving symmetry test."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = -5.0 * (t[m1] / 200.0)                  # 0 → -5 V (charge)
    m2 = (t > 200.0) & (t <= 400.0)
    # Phase 2 rises at ~0.3× the phase-1 slope magnitude (ratio 0.3 <
    # 1/2.0) — NOT a mirror.
    v[m2] = -5.0 + 1.5 * ((t[m2] - 200.0) / 200.0)  # -5 V → -3.5 V (shallow)
    v[t > 400.0] = -3.5
    cls, _ = classify_response_and_ceff(
        t, v, _pat(-50.0), onset_us=0.0, driving_v=5.0, compliance_v=9.0)
    assert cls == "broken"               # non-mirror phase 2 → finite R → broken


def test_straight_high_z_ramp_off_baseline_is_broken():
    """A straight, no-access, HIGH-Z ramp that KEEPS RAMPING (0→-5→+1 V, never
    plateaus) is BROKEN under the flatness rule.  (Formerly asserted 'open'
    under the retired straight-ramp=open logic — that direction was backwards;
    a ramp is broken, only a flat plateau is open.)"""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = -5.0 * (t[m1] / 200.0)                  # 0 → -5 V (charge, ramping)
    m2 = (t > 200.0) & (t <= 400.0)
    v[m2] = -5.0 + 6.0 * ((t[m2] - 200.0) / 200.0)  # -5 V → +1 V
    v[t > 400.0] = 1.0
    cls, _ = classify_response_and_ceff(
        t, v, _pat(-50.0), onset_us=0.0, driving_v=5.0, compliance_v=9.0)
    assert cls == "broken"


def test_access_step_with_curved_ramp_is_normal():
    """Access voltage is the PRIMARY normal signal: an electrode with a real
    ohmic IR step is NORMAL even when its Faradaic ramp is CURVED (the
    straightness only splits open/broken among the NO-access cases).  Distinct
    from CH03, which is curved but has NO access → broken."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    # 0.5 V ohmic step + a strongly CURVED (quadratic) Faradaic ramp.
    v[m] = -0.5 - 0.9 * (t[m] / 200.0) ** 2
    cls, ceff = classify_response_and_ceff(
        t, v, _pat(-200.0), onset_us=0.0, driving_v=1.4, compliance_v=9.0)
    assert cls == "normal"
    assert math.isnan(ceff)


def _capture(v_mon, pat):
    t = np.linspace(-50.0, 450.0, v_mon.size)
    i = np.zeros_like(t)
    mm = (t >= 0.0) & (t <= 200.0)
    i[mm] = pat.phases[0].amplitude_ua
    c = Capture(index=0, pattern=pat)
    c.time_us = t; c.v_mon_v = v_mon; c.i_mon_ua = i
    return c


def test_compute_metrics_suppresses_access_and_epol_for_capacitive():
    pat = _pat(-200.0)
    t = np.linspace(-50.0, 450.0, 2000)
    v = _cap_mirrored_v(t)                # capacitive (mirrored ramps)
    c = _capture(v, pat)
    mt = compute_metrics(c, surface_area_um2=5000.0)
    assert mt.response_class == "capacitive"
    assert np.isfinite(mt.effective_capacitance_nf)
    assert mt.access_voltage_per_phase_v == []
    assert mt.access_resistance_per_phase_kohm == []
    assert mt.polarization_per_phase_v == []


def test_compute_metrics_keeps_access_for_normal():
    pat = _pat(-200.0)
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t); m = (t >= 0.0) & (t <= 200.0)
    # IR step + exponential charge → a normal electrode response.
    v[m] = -0.5 + (-1.2 + 0.5) * (1 - np.exp(-(t[m]) / 80.0))
    c = _capture(v, pat)
    mt = compute_metrics(c, surface_area_um2=5000.0)
    assert mt.response_class == "normal"
    assert math.isnan(mt.effective_capacitance_nf)
    assert len(mt.access_voltage_per_phase_v) >= 1


def test_ceff_persists_round_trip(tmp_path):
    from stimtest.persistence import save_session_npz, load_session_npz
    from stimtest.session import ChannelRun, Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    pat = _pat(-200.0)
    t = np.linspace(-50.0, 450.0, 2000)
    v = _cap_mirrored_v(t)
    c = _capture(v, pat)
    compute_metrics(c, surface_area_um2=5000.0)
    assert np.isfinite(c.metrics.effective_capacitance_nf)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    s = Session(notebook="t", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(c); s.add_run(run)
    p = tmp_path / "ceff.npz"
    save_session_npz(s, p)
    lc = load_session_npz(p).runs[0].captures[0]
    assert abs(lc.metrics.effective_capacitance_nf
               - c.metrics.effective_capacitance_nf) < 1e-6
    assert lc.metrics.response_class == "capacitive"


# ---------------------------------------------------------------------------
# Shaped-broken threshold — a functional HIGH-IMPEDANCE microelectrode must
# NOT be flagged broken (operator: a gaussian "is still quitting after
# designated 'broken' despite there being enough current to be reduced to sit
# within the potential limits").  Ground truth = the exp_vt_max archive: GOOD
# channels sit at 2-9 kΩ, BAD (open/broken) at 454-1004 kΩ.  A small functional
# microelectrode legitimately has 50-150 kΩ access R + polarization near its
# water window, which the old 0.05 MΩ (50 kΩ) shaped gate false-flagged; the
# 0.30 MΩ gate spares it while still catching the truly-open/broken.
# ---------------------------------------------------------------------------

def _shaped_ir_ramp_v(t, ir_v, ramp_v):
    """A FUNCTIONAL shaped-pulse V_mon: a fast IR step (``ir_v``) then a
    polarization ramp adding ``ramp_v`` over phase 1, mirrored in phase 2."""
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = -ir_v - ramp_v * (t[m1] / 200.0)
    m2 = (t > 200.0) & (t <= 400.0)
    peak = -(ir_v + ramp_v)
    v[m2] = peak - peak * ((t[m2] - 200.0) / 200.0)
    return v


def test_functional_highz_gaussian_microelectrode_not_broken():
    """A functional small gaussian microelectrode near its water window: V_mon
    excursion ~1.2 V at 10 µA = 120 kΩ.  Under the OLD 0.05 MΩ shaped gate this
    read broken (and the VT ramp quit, clearing E_pol); under 0.30 MΩ it is
    NORMAL, so the ramp keeps its E_pol and rides the water-window logic — the
    operator's "enough current to be reduced to sit within the limits" case."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = _shaped_ir_ramp_v(t, ir_v=0.4, ramp_v=0.8)      # 1.2 V / 10 µA = 120 kΩ
    cls, ceff = classify_response_and_ceff(
        t, v, _shaped_pat(SHAPE_GAUSSIAN, -10.0),
        onset_us=0.0, driving_v=1.2, compliance_v=9.0)
    assert cls == "normal", cls
    assert math.isnan(ceff)


def test_broken_shaped_still_caught_at_high_impedance():
    """The 0.30 MΩ gate still catches a genuinely broken/open shaped electrode
    (bench bad = 0.45-1.0 MΩ; here 3.5 V / 5 µA = 700 kΩ)."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t); m = (t >= 0.0) & (t <= 200.0)
    v[m] = -3.5                                          # 3.5 V / 5 µA = 700 kΩ
    cls, _ = classify_response_and_ceff(
        t, v, _shaped_pat(SHAPE_GAUSSIAN, -5.0),
        onset_us=0.0, driving_v=3.5, compliance_v=9.0)
    assert cls in ("broken", "open"), cls


def test_open_shaped_channel_caught_at_1ua_early_indicator():
    """Early bad-channel indicator (operator: "the shapes at 0 or 1 µA can be
    an early indicator of a bad channel/combo").  The shaped detector's gate is
    now 1 µA (was 5 µA), so an OPEN electrode reveals itself at the very first
    low-current capture — 0.6 V at 1 µA = 600 kΩ → open/broken — instead of
    ramping up before it's caught."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t); m = (t >= 0.0) & (t <= 200.0)
    v[m] = -0.6                                          # 0.6 V / 1 µA = 600 kΩ
    cls, _ = classify_response_and_ceff(
        t, v, _shaped_pat(SHAPE_GAUSSIAN, -1.0),
        onset_us=0.0, driving_v=0.6, compliance_v=9.0)
    assert cls in ("broken", "open"), cls


def test_functional_shaped_at_1ua_declined_by_signal_floor():
    """A FUNCTIONAL low-Z electrode at 1 µA has a tiny V_mon (~3 mV) below the
    30 mV signal floor, so the lowered 1 µA gate can't false-flag it — it's
    declined to 'normal' regardless of the raw excursion impedance."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t); m = (t >= 0.0) & (t <= 200.0)
    v[m] = -0.003                                        # 3 mV — below the floor
    cls, _ = classify_response_and_ceff(
        t, v, _shaped_pat(SHAPE_GAUSSIAN, -1.0),
        onset_us=0.0, driving_v=0.003, compliance_v=9.0)
    assert cls == "normal", cls
