"""Three operator metric changes:

  * #120 — subsequent-phase driving voltage (a phase preceded by an
    interphase delay) is drawn as its own driving marker, referenced to the
    ENDING-INTERPHASE V_mon.
  * #121 — driving energy is ∫ V_mon · IDEAL-pattern-current dt (not I_mon),
    so I_mon spikes / asynchronous starts don't corrupt it.
  * #122 — access V/R is allowed on a sinusoidal / gaussian pulse when the
    trace shows a genuine vertical IR step, but NOT on a smooth ramp.
"""
from __future__ import annotations

import numpy as np

from stimtest import metrics
from stimtest.metrics import (ideal_current_ua, _boundary_has_ir_step,
                              access_index_labels, compute_metrics)
from stimtest.plotting import compute_metric_markers
from stimtest.session import Capture, CaptureMetrics, CaptureStatus
from stimtest.waveforms import (PulsePattern, SHAPE_SINUSOIDAL,
                                SHAPE_RECTANGULAR)


# ============================================================= #121 energy
def _rect_biphasic(amp=-100.0):
    return PulsePattern.biphasic(amplitude_ua=amp, phase_width_us=200.0,
                                 interphase_us=20.0, discharge_us=20.0,
                                 polarity=-1)


def test_ideal_current_reconstructs_rectangular_biphasic():
    pat = _rect_biphasic(-100.0)
    t = np.linspace(-50.0, 500.0, 4000)
    i = ideal_current_ua(t, pat, onset_us=0.0)
    # phase 1 = −100 µA, phase 2 = +100 µA, zero in the gaps + interpulse.
    assert round(float(i.min()), 1) == -100.0
    assert round(float(i.max()), 1) == 100.0
    # the pre-pulse baseline and the trailing interpulse are exactly zero.
    assert float(i[t < -1.0].max()) == 0.0
    assert abs(float(i[t > 460.0].max())) < 1e-9


def test_driving_energy_uses_ideal_current_not_imon():
    """A spiky / wrong I_mon must NOT change the driving energy — it's
    computed from the ideal programmed current."""
    pat = _rect_biphasic(-100.0)
    t = np.linspace(-50.0, 500.0, 4000)
    i_ideal = ideal_current_ua(t, pat, onset_us=0.0)
    v = (i_ideal * 1e-6) * 2000.0                    # 2 kΩ access → V

    def _energy(i_mon):
        cap = Capture(index=0, pattern=pat, time_us=t, v_mon_v=v,
                      i_mon_ua=i_mon, metrics=CaptureMetrics(),
                      status=CaptureStatus())
        return compute_metrics(cap, surface_area_um2=1000.0).driving_energy_uj

    e_clean = _energy(i_ideal.copy())
    # I_mon scaled 3× (a mis-scaled monitor) + big switching spikes.  The
    # onset (first threshold crossing) is unchanged, so the ideal current is
    # identically anchored → the energy must NOT move: it ignores I_mon.
    i_spiky = i_ideal * 3.0
    i_spiky[500] += 800.0; i_spiky[1200] -= 900.0
    e_spiky = _energy(i_spiky)
    assert np.isfinite(e_clean) and e_clean > 0
    assert abs(e_clean - e_spiky) < 1e-9             # identical → uses ideal


def test_driving_energy_computes_without_imon():
    pat = _rect_biphasic(-100.0)
    t = np.linspace(-50.0, 500.0, 4000)
    v = (ideal_current_ua(t, pat, onset_us=0.0) * 1e-6) * 2000.0
    cap = Capture(index=0, pattern=pat, time_us=t, v_mon_v=v, i_mon_ua=None,
                  metrics=CaptureMetrics(), status=CaptureStatus())
    m = compute_metrics(cap, surface_area_um2=1000.0)
    assert np.isfinite(m.driving_energy_uj) and m.driving_energy_uj > 0


# ============================================================= #122 access IR
def test_ir_step_detector_pure_sine_vs_step():
    t = np.linspace(0.0, 200.0, 4000)
    sine = -0.3 * np.sin(np.pi * t / 200.0)
    assert _boundary_has_ir_step(t, sine, 100.0) is False   # smooth → no step
    step = sine.copy(); step[t >= 100.0] += 0.15            # vertical step
    assert _boundary_has_ir_step(t, step, 100.0) is True


def _sine_biphasic():
    return PulsePattern.biphasic(amplitude_ua=-100.0, phase_width_us=200.0,
                                 interphase_us=0.0, discharge_us=20.0,
                                 polarity=-1, symmetric=False,
                                 amplitude2_ua=100.0,
                                 shape=SHAPE_SINUSOIDAL, shape2=SHAPE_SINUSOIDAL)


def test_sinusoidal_nominal_labels_empty_without_trace():
    # Back-compat: nominal (no trace) → sinusoidal contributes NO access pts.
    pat = _sine_biphasic()
    assert access_index_labels(pat) == []


def test_sinusoidal_gets_access_label_when_trace_has_ir_step():
    pat = _sine_biphasic()
    t = np.linspace(-40.0, 460.0, 5000)
    # Sinusoidal V_mon with a genuine vertical IR step at the phase-1 leading
    # edge (onset = 0) — a real ohmic drop the operator observed.
    v = np.zeros_like(t)
    m1 = (t >= 0) & (t <= 200)
    v[m1] = -0.3 * np.sin(np.pi * (t[m1]) / 200.0)
    v[t >= 0] += -0.2 * (t[t >= 0] >= 0)              # a −0.2 V step at onset
    lab_nom = access_index_labels(pat)
    lab_dat = access_index_labels(pat, time_us=t, v_trace=v, onset_us=0.0)
    assert lab_nom == []                              # nominal still empty
    assert (0, "lead") in lab_dat                     # data-driven adds ph1 lead


def test_smooth_sinusoidal_trace_still_no_access():
    """A clean sinusoidal V_mon (no vertical drop) must NOT invent access."""
    pat = _sine_biphasic()
    t = np.linspace(-40.0, 460.0, 5000)
    v = np.zeros_like(t)
    m1 = (t >= 0) & (t <= 200); v[m1] = -0.3 * np.sin(np.pi * t[m1] / 200.0)
    m2 = (t >= 200) & (t <= 400); v[m2] = 0.3 * np.sin(np.pi * (t[m2] - 200) / 200.0)
    assert access_index_labels(pat, time_us=t, v_trace=v, onset_us=0.0) == []


def test_small_signal_noisy_sinusoid_does_not_invent_access():
    """Adversarial (verified): a SMALL-SIGNAL sinusoid (±10 mV, e.g. −5 µA into
    ~2 kΩ) with realistic post-average noise must NOT manufacture a phantom
    access point — the whole-trace-p2p fractional test alone would trip on the
    noise floor.  The sustained-step + absolute-voltage floor rejects it."""
    pat = PulsePattern.biphasic(amplitude_ua=-5.0, phase_width_us=200.0,
                                interphase_us=20.0, discharge_us=100.0,
                                polarity=-1, symmetric=False, amplitude2_ua=5.0,
                                shape=SHAPE_SINUSOIDAL, shape2=SHAPE_SINUSOIDAL)
    t = np.linspace(-40.0, 560.0, 18750)              # ~32 ns sampling
    base = np.zeros_like(t)
    m1 = (t >= 0) & (t <= 200); base[m1] = -0.010 * np.sin(np.pi * t[m1] / 200.0)
    m2 = (t >= 220) & (t <= 420); base[m2] = 0.010 * np.sin(np.pi * (t[m2] - 220) / 200.0)
    rng = np.random.default_rng(0)
    invented = 0
    for _ in range(20):
        v = base + rng.normal(0.0, 1.0e-3, t.size)    # 1 mV residual noise
        if access_index_labels(pat, time_us=t, v_trace=v, onset_us=0.0):
            invented += 1
    assert invented == 0, f"{invented}/20 noise seeds invented access"
    # But a REAL ~12 mV vertical IR step at the onset IS still detected.
    v_step = base.copy(); v_step[t >= 0] += -0.012
    assert (0, "lead") in access_index_labels(pat, time_us=t, v_trace=v_step,
                                              onset_us=0.0)


# ============================================================= #120 driving markers
def _biphasic_vmon_with_interphase():
    """Symmetric biphasic (interphase + discharge), synthetic V_mon with a
    recovered interphase so phase-2 driving refs the ending-interphase."""
    pat = _rect_biphasic(-100.0)                      # 200/20/200/20, symmetric
    t = np.linspace(-40.0, 480.0, 5000)
    v = np.zeros_like(t)
    v[(t >= 0) & (t < 200)] = -0.30                   # phase 1 plateau
    v[(t >= 200) & (t < 220)] = 0.0                   # interphase (recovered)
    v[(t >= 220) & (t < 420)] = 0.25                  # phase 2 plateau
    i = ideal_current_ua(t, pat, onset_us=0.0)
    cap = Capture(index=0, pattern=pat, time_us=t, v_mon_v=v, i_mon_ua=i,
                  metrics=CaptureMetrics(), status=CaptureStatus())
    return cap


def test_subsequent_phase_driving_marker_present():
    cap = _biphasic_vmon_with_interphase()
    marks = compute_metric_markers(cap)
    labels = {mk["label"] for mk in marks if mk["kind"] == "driving"}
    assert "Vd" in labels                             # primary V_d (phase 1)
    assert "Vd2" in labels                            # subsequent (phase 2)
    # Vd2 is referenced to the ending-interphase (~0) → ≈ +0.25 V.
    vd2 = next(mk for mk in marks if mk.get("label") == "Vd2")
    assert abs(vd2["y"] - 0.25) < 0.03


def test_no_subsequent_driving_marker_without_interphase():
    """A delay-less biphasic (no interphase before phase 2) → only V_d."""
    pat = PulsePattern.biphasic(amplitude_ua=-100.0, phase_width_us=200.0,
                                interphase_us=0.0, discharge_us=20.0,
                                polarity=-1)
    t = np.linspace(-40.0, 460.0, 5000)
    v = np.zeros_like(t)
    v[(t >= 0) & (t < 200)] = -0.30
    v[(t >= 200) & (t < 400)] = 0.30
    i = ideal_current_ua(t, pat, onset_us=0.0)
    cap = Capture(index=0, pattern=pat, time_us=t, v_mon_v=v, i_mon_ua=i,
                  metrics=CaptureMetrics(), status=CaptureStatus())
    labels = {mk["label"] for mk in compute_metric_markers(cap)
              if mk["kind"] == "driving"}
    assert "Vd" in labels
    assert not any(l.startswith("Vd") and l != "Vd" for l in labels)
