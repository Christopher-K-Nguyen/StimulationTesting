"""Charge-per-phase (Q_ph) correctness for every pulse shape.

Operator: "Be sure that the charge/phase is calculated correctly for each
pulse shape." + "For all charge metrics, use the ideal pattern.  Only use the
realistic when comparing the error in the test parameters."

The reported Q_ph (``PulsePattern.charge_per_phase_nc`` / ``net_charge_nc``) is
the IDEAL continuous-waveform integral (``ideal_charge_nc`` — trapezoidal,
unquantized), so a linear-increasing 1000 µA / 200 µs reads a clean 100 nC
(not the device staircase's 99.6).  It correctly handles a non-canonical
``tau_us`` / ``tail_zero_us`` / ``offset_ua`` via the breakpoints (which the
analytic ``_shape_duty`` hardcodes / ignores).  The DEVICE-EXACT staircase
(``actual_phase_charges_nc``, 30/100 nA grid) is used ONLY to show the
quantization error in the test-parameters panel.
"""
from __future__ import annotations

import math

from stimtest.waveforms import (Phase, PulsePattern, SHAPE_RECTANGULAR,
                                SHAPE_SINUSOIDAL, SHAPE_BOWTIE, SHAPE_HALFPIPE,
                                SHAPE_GAUSSIAN, SHAPE_LINEAR_INCREASING,
                                SHAPE_EXP_DECAY, actual_charge_nc, ideal_charge_nc)


def _mono(shape, amp=100.0, width=200.0, **kw):
    """A single-phase pattern (huge interpulse) for isolating one phase's Q."""
    ph = Phase(amplitude_ua=amp, width_us=width, shape=shape, **kw)
    return PulsePattern(phases=[ph], rate_hz=10.0)


# ------------------------------------------- device-exact matches expectation
def test_rectangular_charge_is_amp_times_width():
    p = _mono(SHAPE_RECTANGULAR, amp=100.0, width=200.0)
    # 100 µA × 200 µs = 20000 µA·µs = 20 nC (rectangular, exact).
    assert abs(p.charge_per_phase_nc - 20.0) < 1e-6


def test_shaped_charge_is_below_peak_times_width():
    """Every non-rectangular shape delivers LESS than peak×width, by roughly
    its known duty — and the reported value equals the device integral."""
    peak_qw = 100.0 * 1e-3 * 200.0   # 20 nC if it ran at peak the whole width
    expect_duty = {
        SHAPE_SINUSOIDAL: 2.0 / math.pi,     # ≈0.637
        SHAPE_BOWTIE: 0.5,
        SHAPE_HALFPIPE: 1.0 - 2.0 / math.pi,  # ≈0.363
        SHAPE_LINEAR_INCREASING: 0.5,
        SHAPE_GAUSSIAN: 0.47,                  # ≈ (zero-tapered σ=W/5)
    }
    for shape, duty in expect_duty.items():
        q = _mono(shape).charge_per_phase_nc
        # Device left-Riemann is within a couple % of the analytic duty.
        assert abs(q - peak_qw * duty) < 0.06 * peak_qw, (shape, q, peak_qw * duty)
        assert q < peak_qw, (shape, q)         # always less than peak×width


def test_reported_equals_ideal_integral():
    """charge_per_phase_nc == the IDEAL trapezoidal integral (unquantized), NOT
    the device staircase (operator: charge metrics use the ideal pattern)."""
    p = _mono(SHAPE_SINUSOIDAL)
    # Compare against the pattern-level ideal (same n_samples budget the
    # property uses); the module-level ideal_charge_nc uses the default budget.
    assert abs(p.charge_per_phase_nc - abs(p.ideal_phase_charges_nc()[0])) < 1e-9
    assert abs(ideal_charge_nc(p.phases[0]) - abs(p.ideal_phase_charges_nc()[0])) < 0.05


def test_linear_increasing_reports_clean_ideal_charge():
    """The operator's exact case: linear-increasing 1000 µA / 200 µs is a
    triangle → ideal Q_ph = ½·1000·200 = 100.0 nC (a clean value), while the
    DEVICE 30 nA staircase delivers ~99.6 nC — the reported metric is IDEAL."""
    p = _mono(SHAPE_LINEAR_INCREASING, amp=1000.0, width=200.0)
    assert abs(p.charge_per_phase_nc - 100.0) < 1e-6, p.charge_per_phase_nc
    q_dev = abs(p.actual_phase_charges_nc(
        current_step_nA=p.device_current_step_nA())[0])
    assert 99.0 < q_dev < 100.0                     # device under-delivers
    assert p.charge_per_phase_nc > q_dev            # ideal > realistic


def test_symmetric_biphasic_ideal_net_is_exactly_zero():
    """A symmetric biphasic's IDEAL net charge is a clean 0 (balanced design),
    whereas the DEVICE residual is a small non-zero quantization error shown as
    the test-parameters 'error'."""
    p = PulsePattern(phases=[Phase(-1000.0, 200.0, 20.0, shape=SHAPE_LINEAR_INCREASING),
                             Phase(+1000.0, 200.0, 20.0, shape=SHAPE_LINEAR_INCREASING)],
                     rate_hz=50.0)
    assert abs(p.net_charge_nc) < 1e-6                       # ideal net = 0
    q_dev = p.actual_phase_charges_nc(current_step_nA=p.device_current_step_nA())
    # device per-phase magnitudes are ~99.6 (under the ideal 100).
    assert all(99.0 < abs(q) < 100.0 for q in q_dev)


# ------------------------------------- exp-decay tau is honoured (the real fix)
def test_exp_decay_charge_tracks_tau():
    """The analytic _shape_duty HARDCODES the canonical exp duty; the
    device-exact charge must instead track the ACTUAL tau_us — a short tau
    (fast decay) delivers LESS charge than a long tau."""
    short = _mono(SHAPE_EXP_DECAY, width=200.0, tau_us=20.0)   # τ = W/10
    long_ = _mono(SHAPE_EXP_DECAY, width=200.0, tau_us=100.0)  # τ = W/2
    q_short = short.charge_per_phase_nc
    q_long = long_.charge_per_phase_nc
    assert q_long > q_short * 1.5, (q_short, q_long)   # longer tau ⇒ more charge
    # And each matches the closed-form (tau/W)(1-e^(-W/tau)) × peak×width
    # to within the staircase/quantization error.
    for pat, tau in ((short, 20.0), (long_, 100.0)):
        duty = (tau / 200.0) * (1.0 - math.exp(-200.0 / tau))
        assert abs(pat.charge_per_phase_nc - 20.0 * duty) < 0.06 * 20.0, tau


def test_tail_zero_reduces_charge():
    """An exp-decay tail_zero_us (trailing region forced to 0) delivers LESS
    charge — the device integral counts the zeroed tail, _shape_duty didn't."""
    no_tail = _mono(SHAPE_EXP_DECAY, width=200.0, tau_us=40.0)
    with_tail = _mono(SHAPE_EXP_DECAY, width=200.0, tau_us=40.0, tail_zero_us=80.0)
    assert with_tail.charge_per_phase_nc < no_tail.charge_per_phase_nc
