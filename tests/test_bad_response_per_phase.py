"""Per-phase metrics for broken / open channels + E_act-based broken fit.

Operator: "For broken and open channels, compute the same metrics for other
phases.  Broken channels should be based on Eact, if possible."

* ``fit_parallel_rc`` / ``effective_capacitance_from_ramp`` gained a
  ``phase_idx`` — each phase fits over its own onset-anchored window, with
  the level just before the phase as its baseline.
* ``compute_metrics`` populates ``effective_capacitance_per_phase_nf`` /
  ``rc_fit_resistance_per_phase_kohm`` / ``rc_fit_tau_per_phase_us`` for a
  non-normal class (entry 0 mirrors the scalars); empty for normal.
* The BROKEN fit input is ``active_trace`` — E_act when recorded, else V_mon.
"""
from __future__ import annotations

import numpy as np

from stimtest.metrics import compute_metrics, fit_parallel_rc
from stimtest.session import Capture
from stimtest.waveforms import Phase, PulsePattern, SHAPE_RECTANGULAR


def _pat(amp_ua=-50.0):
    return PulsePattern(phases=[
        Phase(amplitude_ua=amp_ua, width_us=200, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=-amp_ua, width_us=200, shape=SHAPE_RECTANGULAR),
    ], rate_hz=50.0)


def _broken_biphasic_v(t, vinf1=-12.0, tau1=33.0, vinf2=10.0, tau2=50.0):
    """Exponential charge in phase 1 (0-200 µs), exponential swing toward
    the OPPOSITE asymptote in phase 2 (200-400 µs) — a broken R‖C driven by
    a biphasic current."""
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = vinf1 * (1.0 - np.exp(-t[m1] / tau1))
    v_end1 = vinf1 * (1.0 - np.exp(-200.0 / tau1))
    m2 = (t > 200.0) & (t <= 400.0)
    v[m2] = v_end1 + vinf2 * (1.0 - np.exp(-(t[m2] - 200.0) / tau2))
    v[t > 400.0] = v_end1 + vinf2 * (1.0 - np.exp(-200.0 / tau2))
    return v


def _capture(pat, v_mon, e_act=None):
    t = np.linspace(-50.0, 450.0, 2500)
    i = np.zeros_like(t)
    i[(t >= 0.0) & (t <= 200.0)] = pat.phases[0].amplitude_ua
    i[(t > 200.0) & (t <= 400.0)] = pat.phases[1].amplitude_ua
    c = Capture(index=0, pattern=pat)
    c.time_us = t
    c.v_mon_v = v_mon(t) if callable(v_mon) else v_mon
    c.i_mon_ua = i
    if e_act is not None:
        c.e_act_v = e_act(t) if callable(e_act) else e_act
    return c


# --------------------------------------------------- per-phase RC (broken)
def test_fit_parallel_rc_phase2_recovers_its_own_tau():
    pat = _pat(-50.0)
    t = np.linspace(-50.0, 450.0, 2500)
    v = _broken_biphasic_v(t, vinf1=-12.0, tau1=33.0, vinf2=10.0, tau2=50.0)
    r1, c1, tau1, r2a = fit_parallel_rc(t, v, pat, onset_us=0.0, amp_ua=50.0,
                                        phase_idx=0)
    r2_, c2, tau2, r2b = fit_parallel_rc(t, v, pat, onset_us=0.0, amp_ua=50.0,
                                         phase_idx=1)
    assert r2a > 0.99 and abs(tau1 - 33.0) < 6.0
    assert r2b > 0.99 and abs(tau2 - 50.0) < 8.0
    # Phase-2 R = V∞2 / I = 10 V / 50 µA = 200 kΩ.
    assert abs(r2_ - 200.0) < 30.0


def test_broken_capture_gets_per_phase_lists():
    pat = _pat(-50.0)
    c = _capture(pat, lambda t: _broken_biphasic_v(t))
    mt = compute_metrics(c, surface_area_um2=5000.0)
    # A SATURATING R‖C exponential PLATEAUS → open under the flatness rule
    # (dE/dt→0 late; the user's "broken = keeps polarizing" excludes it).  OPEN
    # electrodes still get the per-phase R‖C fit, which is what this pins.
    assert mt.response_class == "open"
    assert len(mt.effective_capacitance_per_phase_nf) == 2
    assert len(mt.rc_fit_resistance_per_phase_kohm) == 2
    assert len(mt.rc_fit_tau_per_phase_us) == 2
    # Entry 0 mirrors the scalars.
    assert mt.effective_capacitance_per_phase_nf[0] == mt.effective_capacitance_nf
    assert mt.rc_fit_resistance_per_phase_kohm[0] == mt.rc_fit_resistance_kohm
    assert mt.rc_fit_tau_per_phase_us[0] == mt.rc_fit_tau_us
    # Phase-2 fit is real and reflects ITS OWN τ.
    assert np.isfinite(mt.rc_fit_tau_per_phase_us[1])
    assert abs(mt.rc_fit_tau_per_phase_us[1] - 50.0) < 8.0


def test_broken_fit_uses_eact_when_recorded():
    """Operator: "broken channels should be based on Eact, if possible" —
    with an E_act trace recorded, the RC fit reads E_act's τ, not V_mon's."""
    pat = _pat(-50.0)
    c = _capture(pat,
                 lambda t: _broken_biphasic_v(t, tau1=33.0),
                 e_act=lambda t: _broken_biphasic_v(t, vinf1=-6.0, tau1=60.0,
                                                    vinf2=5.0, tau2=70.0))
    mt = compute_metrics(c, surface_area_um2=5000.0)
    assert mt.response_class == "open"            # saturating R‖C plateaus → open
    assert abs(mt.rc_fit_tau_us - 60.0) < 8.0, (
        f"fit read V_mon's tau, not E_act's: {mt.rc_fit_tau_us}")
    assert abs(mt.rc_fit_tau_per_phase_us[1] - 70.0) < 10.0


# ------------------------------------------------- per-phase C (open/capacitive)
def test_capacitive_capture_gets_per_phase_ceff():
    pat = _pat(-200.0)
    t = np.linspace(-50.0, 450.0, 2500)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = -1.4 * (t[m1] / 200.0)                    # linear charge
    m2 = (t > 200.0) & (t <= 400.0)
    v[m2] = -1.4 + 1.4 * ((t[m2] - 200.0) / 200.0)    # mirrored discharge
    c = _capture(pat, v)
    mt = compute_metrics(c, surface_area_um2=5000.0)
    assert mt.response_class in ("capacitive", "open")
    assert len(mt.effective_capacitance_per_phase_nf) == 2
    assert mt.effective_capacitance_per_phase_nf[0] == mt.effective_capacitance_nf
    # Phase 2 has the same |slope| + same |I| → same C (within fit noise).
    c1, c2 = mt.effective_capacitance_per_phase_nf
    assert np.isfinite(c2)
    assert abs(c2 - c1) / c1 < 0.15, (c1, c2)
    # Pure capacitance: no per-phase R / τ.
    assert all(not np.isfinite(x) for x in mt.rc_fit_resistance_per_phase_kohm)


def test_normal_capture_has_empty_per_phase_lists():
    pat = _pat(-200.0)
    t = np.linspace(-50.0, 450.0, 2500)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    v[m1] = -0.5 + (-1.2 + 0.5) * (1 - np.exp(-t[m1] / 80.0))  # IR step + charge
    c = _capture(pat, v)
    mt = compute_metrics(c, surface_area_um2=5000.0)
    assert mt.response_class == "normal"
    assert mt.effective_capacitance_per_phase_nf == []
    assert mt.rc_fit_resistance_per_phase_kohm == []
    assert mt.rc_fit_tau_per_phase_us == []


# ----------------------------------------------------------- persistence
def test_per_phase_lists_round_trip(tmp_path):
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.persistence import load_session_npz, save_session_npz
    from stimtest.session import ChannelRun, Session, TestParameters
    pat = _pat(-50.0)
    c = _capture(pat, lambda t: _broken_biphasic_v(t))
    compute_metrics(c, surface_area_um2=5000.0)
    assert c.metrics.response_class == "open"     # saturating R‖C plateaus → open
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    s = Session(notebook="t", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(c)
    s.add_run(run)
    p = tmp_path / "pp.npz"
    save_session_npz(s, p)
    lm = load_session_npz(p).runs[0].captures[0].metrics
    assert len(lm.rc_fit_tau_per_phase_us) == 2
    np.testing.assert_allclose(lm.rc_fit_tau_per_phase_us,
                               c.metrics.rc_fit_tau_per_phase_us)
    np.testing.assert_allclose(lm.effective_capacitance_per_phase_nf,
                               c.metrics.effective_capacitance_per_phase_nf)
