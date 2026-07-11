"""Shape-dependent current resolution.

Operator: "Please keep the current resolution at 0.1 µA for rectangular
shapes.  I do not trust the 30 nA resolution of the stimulator but will
use it for non-rectangular shapes."

So when a pattern is rendered to the device ``.pat``:
  * a RECTANGULAR phase's amplitude is quantized to the 0.1 µA (100 nA) grid,
  * a SHAPED (ramp / exp / sine / …) phase uses the device's native 30 nA.
The choice is PER-PHASE, so a rectangular phase inside a mixed pulse (e.g.
the rect cathodic of a rect + exp-decay cap-coupled pair) still lands on
the trusted 0.1 µA grid.
"""
from __future__ import annotations

import pytest

from stimtest.waveforms import (
    Phase, PulsePattern, build_pat_pairs,
    SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING,
    SHAPE_EXP_DECAY,
)


def _rate(): return 20.0


def test_rectangular_amplitude_quantized_to_tenth_microamp():
    # 50.013 µA is off the 0.1 µA grid -> must snap to exactly 50.0 µA.
    p = PulsePattern(phases=[
        Phase(amplitude_ua=-50.013, width_us=200, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=50.013, width_us=200, shape=SHAPE_RECTANGULAR),
    ], rate_hz=_rate())
    assert p.device_current_step_nA() == 100
    amps = sorted({a for a, _ in build_pat_pairs(p)})
    assert amps == [-50000, 50000]          # ±50.000 µA on the 100 nA grid
    assert all(a % 100 == 0 for a in amps)


def test_nonrectangular_uses_thirty_nanoamp_grid():
    p = PulsePattern(phases=[
        Phase(amplitude_ua=-50.013, width_us=200, shape=SHAPE_LINEAR_INCREASING),
        Phase(amplitude_ua=50.013, width_us=200, shape=SHAPE_LINEAR_INCREASING),
    ], rate_hz=_rate())
    # Any shaped phase -> pattern-level step is the fine 30 nA grid.
    assert p.device_current_step_nA() == 30
    pairs = build_pat_pairs(p)
    # Every shaped-phase amplitude is on the 30 nA grid.
    assert all(a % 30 == 0 for a, _ in pairs), [a for a, _ in pairs]


def test_mixed_pattern_quantizes_each_phase_by_its_own_shape():
    # Rect cathodic + exp-decay anodic: the RECT phase stays on 0.1 µA,
    # the exp-decay phase uses 30 nA — per-phase, not per-pattern.
    cath = Phase(amplitude_ua=-100.0, width_us=200, shape=SHAPE_RECTANGULAR)
    anod = Phase(amplitude_ua=50.0, width_us=400, shape=SHAPE_EXP_DECAY, tau_us=80.0)
    p = PulsePattern(phases=[cath, anod], rate_hz=10.0)
    pairs = build_pat_pairs(p)
    # First pair is the rect cathodic — exactly -100.000 µA (0.1 µA grid).
    assert pairs[0] == (-100_000, 200)
    # The exp-decay pairs are on the 30 nA grid.
    for a, _ in pairs[1:]:
        assert a % 30 == 0


def test_validation_floor_is_per_shape():
    # Rectangular phase below 0.1 µA -> rejected.
    with pytest.raises(ValueError):
        PulsePattern(phases=[
            Phase(amplitude_ua=-0.05, width_us=200, shape=SHAPE_RECTANGULAR),
        ], rate_hz=_rate()).validate()
    # Non-rectangular phases at 0.05 µA (> 30 nA floor) -> accepted.
    PulsePattern(phases=[
        Phase(amplitude_ua=-0.05, width_us=200, shape=SHAPE_LINEAR_INCREASING),
        Phase(amplitude_ua=0.05, width_us=200, shape=SHAPE_LINEAR_DECREASING),
    ], rate_hz=_rate()).validate()


def test_auto_balance_rectangular_lands_on_tenth_microamp_grid():
    # All-rect asymmetric pulse -> auto_balance computes on the 0.1 µA grid.
    p = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=200, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=40.0, width_us=500, shape=SHAPE_RECTANGULAR),
    ], rate_hz=10.0)
    balanced = p.auto_balance(adjust="last_amp")
    last_nA = round(balanced.phases[-1].amplitude_ua * 1000.0)
    assert last_nA % 100 == 0, (
        f"rectangular auto-balance must land on the 0.1 µA grid, "
        f"got {last_nA} nA")
