"""Seamless continuous-sinusoid (KHFAC) device rendering.

Operator: a continuous biphasic sinusoid "looks like a little long flat step
between pulses" — improve the pattern generation so it's seamless between
pulses (besides the intentional discharge).

Root cause: the device sample-and-holds ``build_pat_pairs`` with a LEFT hold,
so each half-sine's leading ``(0, 0)`` breakpoint became one flat-zero step —
at the mid-pulse and period-boundary zero-crossings this stacked into a
visible flat step.  Fix: a MIDPOINT hold ``(a_k + a_{k+1})/2`` for
``MIDPOINT_HOLD_SHAPES`` (sinusoidal), so the staircase steps THROUGH the
crossings.  ``actual_charge_nc`` uses the SAME gate so the device charge model
stays byte-consistent with the emitted pairs.  Scoped to sinusoidal (the
symmetric, inherently charge-balanced KHFAC case) — the cap-coupled EXP_DECAY
solver + every other shape keep the LEFT hold.
"""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.waveforms import (
    PulsePattern, build_pat_pairs, build_burst_pat_pairs, actual_charge_nc,
    MIDPOINT_HOLD_SHAPES, SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR, SHAPE_EXP_DECAY,
)


def _continuous_sinusoid(amp=100.0, w=100.0, discharge=5.0, rate=5000.0):
    return PulsePattern.biphasic(
        amplitude_ua=amp, phase_width_us=w, interphase_us=0.0,
        discharge_us=discharge, rate_hz=rate, symmetric=True,
        shape="sinusoidal", shape2="sinusoidal")


# --------------------------------------------------------------------------
# no leading flat-zero step — only the discharge is flat
# --------------------------------------------------------------------------
def test_only_flat_step_is_the_discharge():
    p = _continuous_sinusoid(discharge=5.0)
    pairs = build_pat_pairs(p)
    zeros = [(i, a, d) for i, (a, d) in enumerate(pairs) if a == 0]
    # exactly ONE zero-amp pair — the trailing discharge (5 µs), nothing else
    assert len(zeros) == 1, zeros
    assert zeros[0][2] == 5           # the discharge duration
    # the very first pair (start of the cathodic half-sine) is NON-zero
    assert pairs[0][0] != 0


def test_no_discharge_means_no_flat_step_at_all():
    p = _continuous_sinusoid(discharge=0.0)
    pairs = build_pat_pairs(p)
    zeros = [p_ for p_ in pairs if p_[0] == 0]
    assert zeros == []                # perfectly seamless, no flat dwell


def test_staircase_steps_through_the_zero_crossing():
    # the cathodic→anodic mid-pulse crossing: the sign flips −→+ with NO
    # zero-amplitude pair in between (a clean step through zero)
    p = _continuous_sinusoid(discharge=0.0)
    pairs = build_pat_pairs(p)
    signs = [(-1 if a < 0 else (1 if a > 0 else 0)) for a, _ in pairs]
    assert 0 not in signs             # never dwells at zero
    # exactly one −→+ transition (the mid-pulse crossing), consecutive
    flips = [i for i in range(1, len(signs)) if signs[i] != signs[i - 1]]
    assert len(flips) == 1
    i = flips[0]
    assert signs[i - 1] == -1 and signs[i] == +1


# --------------------------------------------------------------------------
# charge is preserved (balance + reported ideal)
# --------------------------------------------------------------------------
def test_symmetric_sinusoid_stays_charge_balanced():
    p = _continuous_sinusoid()
    qc = actual_charge_nc(p.phases[0])
    qa = actual_charge_nc(p.phases[1])
    assert qc + qa == pytest.approx(0.0, abs=1e-6)


def test_reported_ideal_charge_unchanged():
    # ideal_charge (trapezoidal on-curve) is the smooth 2·A·W/π and must be
    # untouched by the DEVICE-rendering change
    p = _continuous_sinusoid(amp=100.0, w=100.0)
    q_ideal = p.ideal_phase_charges_nc()[0]
    assert abs(q_ideal) == pytest.approx(2 * 100.0 * 100.0 / np.pi * 1e-3,
                                         rel=1e-3)


def test_emitted_pairs_deliver_the_balanced_charge():
    # the ACTUAL emitted .pat pairs deliver ≈ the ideal 2·A·W/π per phase
    # (midpoint hold = trapezoidal ≈ exact) and the two phases cancel — the
    # meaningful device-charge invariant.  (actual_charge_nc uses a different
    # default n_samples than build_pat_pairs, so they agree only to ~1 %, not
    # bit-for-bit — a pre-existing sampling-budget mismatch, not this change.)
    p = _continuous_sinusoid(discharge=0.0)
    pairs = build_pat_pairs(p)
    w = 100
    q = [0.0, 0.0]      # per-phase µA·µs
    t = 0
    for a_nA, dur in pairs:
        idx = 0 if t < w else 1
        q[idx] += (a_nA / 1000.0) * float(dur)
        t += dur
    q_nc = [x * 1e-3 for x in q]
    ideal = 2 * 100.0 * 100.0 / np.pi * 1e-3
    assert abs(q_nc[0]) == pytest.approx(ideal, rel=0.02)
    assert q_nc[0] + q_nc[1] == pytest.approx(0.0, abs=1e-3)   # balanced


# --------------------------------------------------------------------------
# other shapes are UNCHANGED (left hold preserved)
# --------------------------------------------------------------------------
def test_gate_is_sinusoidal_only():
    assert SHAPE_SINUSOIDAL in MIDPOINT_HOLD_SHAPES
    assert SHAPE_RECTANGULAR not in MIDPOINT_HOLD_SHAPES
    assert SHAPE_EXP_DECAY not in MIDPOINT_HOLD_SHAPES


def test_rectangular_pairs_unchanged():
    # a rectangular biphasic holds peak the whole phase — first pair == peak
    p = PulsePattern.biphasic(amplitude_ua=100.0, phase_width_us=100.0,
                              interphase_us=0.0, discharge_us=0.0,
                              rate_hz=1000.0)
    pairs = build_pat_pairs(p)
    # 100 µA cathodic on the 0.1 µA (100 nA) rect grid → −100000 nA
    assert pairs[0][0] == -100000


def test_exp_decay_keeps_left_hold():
    # exp-decay starts at PEAK and decays; LEFT hold → first pair == peak,
    # midpoint would halve it toward the next sample.  Confirms exp-decay is
    # NOT midpoint-held (the cap-coupled solver depends on this).
    p = PulsePattern.biphasic(
        amplitude_ua=100.0, phase_width_us=100.0, interphase_us=0.0,
        discharge_us=0.0, rate_hz=1000.0, symmetric=False,
        shape="rectangular", shape2="exp_decay")
    pairs = build_pat_pairs(p)
    # find the first pair of the anodic (exp-decay) phase = first +amp pair
    anod = next(a for a, _ in pairs if a > 0)
    # exp-decay peak is +100 µA on the 30 nA shaped grid → ~ +99990 nA;
    # a midpoint hold would start well below the peak
    assert anod >= 99000        # ~ peak, i.e. left hold (not halved)


def test_burst_sinusoid_inherits_seamless_render():
    # a burst of sinusoids goes through build_burst_pat_pairs → build_pat_pairs,
    # so each tiled pulse is seamless too (only intra-burst gaps + discharge
    # are flat)
    # rate=2000 Hz → intra-burst period 500 µs; pulse span 200 µs → a real
    # 300 µs intra-burst gap between the 3 tiled pulses.
    p = _continuous_sinusoid(discharge=0.0, rate=2000.0)
    p.pulses_per_burst = 3
    p.burst_period_us = 2000.0
    pairs = build_burst_pat_pairs(p)
    # the only zero-amp pairs are the (N-1)=2 intra-burst gaps — each tiled
    # sinusoid pulse is itself seamless (no leading flat-zero step)
    zeros = [d for a, d in pairs if a == 0]
    assert len(zeros) == 2
    assert all(d == 300 for d in zeros)
