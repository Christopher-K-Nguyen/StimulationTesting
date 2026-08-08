"""The IR-step read must not flip class on a one-sample onset jitter.

Operator: "Fix the misclassification."  On the AC bench run
(exp_vt_max_cathodal_test_ac) CH08 alternated normal <-> capacitive at
essentially constant amplitude:

    #9   281.3 µA  onset -0.08 µs  v_ir 0.1760  access_frac 0.1091  -> normal
    #10  281.4 µA  onset -0.16 µs  v_ir 0.1600  access_frac 0.0992  -> capacitive

Identical V_peak (1.6128 V).  ``access_frac`` — the classifier's PRIMARY
normal-vs-bad discriminator — was a SINGLE SAMPLE read at a fixed
``onset + _ACCESS_T_US``, which lands on the still-RISING edge, so it moved
~9 % per sample while ``pulse_onset_us`` routinely jitters by a sample.  This
electrode sits right on ``_ACCESS_FRAC_LOW`` (0.10), so the jitter alone
decided the class.

It matters because a non-normal class makes ``compute_metrics`` CLEAR the
capture's E_pol — which is how the VT-max ramp lost its proximity signal and
flung CH08 from 281 µA to the 1000 µA rail, driving the electrode to 2.74× its
water window.

The read is now a MEDIAN over a window centred on the same point, which divides
the jitter sensitivity by the sample count.

Paired with a BORDERLINE band (``_ACCESS_FRAC_BORDERLINE``): just below the
threshold the decision defers to the effective impedance instead, which
separates good electrodes from dead ones by ~50x (CH08 ~5.7 kΩ vs 268 kΩ-1.7 MΩ)
rather than access_frac's ~1.3x.  A robust read alone could not fix a
knife-edge threshold; the two together do.

Measured on the real archives: CH08 goes from FOUR misclassifications to ZERO
(uniformly normal across all 18 captures), and every known-bad electrode
(CH02/CH03/CH10/CH13) is still flagged open/broken.
"""
from __future__ import annotations

import numpy as np

from stimtest.metrics import classify_response_and_ceff
from stimtest.waveforms import Phase, PulsePattern, SHAPE_RECTANGULAR


def _pat(amp=-281.0):
    return PulsePattern(phases=[
        Phase(amplitude_ua=amp, width_us=200, shape=SHAPE_RECTANGULAR,
              delay_after_us=20.0),
        Phase(amplitude_ua=-amp, width_us=200, shape=SHAPE_RECTANGULAR,
              delay_after_us=20.0),
    ], rate_hz=200.0)


def _trace(shift_us=0.0, *, dt=0.08, ir_frac=0.22, peak=1.61):
    """A CH08-like capture: a modest ohmic step that RISES over ~1 µs (so the
    fixed-offset read lands mid-rise, exactly the fragile case), then a
    polarization ramp that carries the trace to ``peak``.  ``ir_frac`` is the
    step as a fraction of ``peak`` BEFORE the ramp is added, so the measured
    access_frac lands near — but above — the _ACCESS_FRAC_LOW line.
    ``shift_us`` slides the whole waveform to emulate onset jitter."""
    t = np.arange(-60.0, 560.0, dt)
    v = np.zeros_like(t)
    tt = t - shift_us
    edge = (tt >= 0.0) & (tt <= 200.0)
    # ohmic step ramping in over ~1 µs, then a linear polarization ramp
    step = ir_frac * peak * np.clip(tt / 1.0, 0.0, 1.0)
    ramp = (peak - ir_frac * peak) * np.clip(tt / 200.0, 0.0, 1.0)
    v[edge] = -(step[edge] + ramp[edge])
    back = (tt > 220.0) & (tt <= 420.0)
    v[back] = (peak * 0.9) * np.clip((tt[back] - 220.0) / 200.0, 0.0, 1.0)
    return t, v


def _classify(shift_us, **kw):
    t, v = _trace(shift_us, **kw)
    return classify_response_and_ceff(
        t, v, _pat(), onset_us=0.0,
        driving_v=float(np.max(np.abs(v))), compliance_v=9.0)[0]


def test_class_is_stable_across_sub_sample_onset_error():
    """A borderline electrode must classify the SAME for onset errors of a few
    samples in either direction — the exact CH08 failure."""
    classes = {round(sh, 3): _classify(sh, ir_frac=0.35)
               for sh in (-0.16, -0.08, 0.0, 0.08, 0.16)}
    assert len(set(classes.values())) == 1, (
        f"class flips with onset jitter: {classes}")


def test_borderline_electrode_reads_normal():
    """An electrode with a real (if modest) ohmic step and a low impedance is
    functional — it must not be suppressed as capacitive/broken, which would
    clear its E_pol and blind the ramp."""
    assert _classify(0.0) == "normal"


def test_a_true_no_step_capacitor_is_still_caught():
    """The robust read must not blunt the discriminator: a genuine pure
    capacitor (NO ohmic step at all) must still be classified non-normal."""
    t = np.arange(-60.0, 560.0, 0.08)
    v = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    m2 = (t > 220.0) & (t <= 420.0)
    v[m1] = -1.61 * (t[m1] / 200.0)            # pure linear ramp from zero
    v[m2] = -1.61 + 1.61 * ((t[m2] - 220.0) / 200.0)
    cls, _ = classify_response_and_ceff(
        t, v, _pat(), onset_us=0.0,
        driving_v=float(np.max(np.abs(v))), compliance_v=9.0)
    assert cls != "normal", cls


def test_knife_edge_electrode_no_longer_flips():
    """The CH08 case itself: an IR step sitting ON the threshold, with a
    healthy impedance.  Before the borderline band this alternated class with
    the onset jitter; now the impedance decides and it is stable."""
    # ±1 SAMPLE (0.08 µs here) — the jitter actually observed between adjacent
    # captures on the bench (CH08 #9 onset -0.08 vs #10 -0.16).
    #
    # ``ir_frac`` is set so the swept access_frac spans ~0.082-0.112, matching
    # the real CH08, whose measured range across every capture was
    # 0.0796-0.1431 — i.e. it straddled _ACCESS_FRAC_LOW (0.10) but never fell
    # below the borderline floor (0.075).  That is the regime the band exists
    # for.  RESIDUAL BOUND, stated plainly: an electrode whose access_frac
    # crosses the FLOOR within one sample can still change class; the band
    # widens the stable region, it does not make the threshold disappear.
    classes = {round(sh, 3): _classify(sh, ir_frac=0.18)
               for sh in (-0.08, 0.0, 0.08)}
    assert set(classes.values()) == {"normal"}, classes


def test_borderline_rescue_needs_a_healthy_impedance():
    """The band must not become a blanket amnesty — a high-impedance electrode
    with the same weak IR step stays BAD.  ``peak`` drives the effective
    impedance (V/I) at the fixed test current."""
    t, v = _trace(0.0, ir_frac=0.16, peak=60.0)     # 60 V at 281 uA ≈ 213 kΩ
    cls, _ = classify_response_and_ceff(
        t, v, _pat(), onset_us=0.0,
        driving_v=float(np.max(np.abs(v))), compliance_v=9.0)
    assert cls != "normal", cls
