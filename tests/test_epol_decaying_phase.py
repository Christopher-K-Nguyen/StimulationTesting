"""E_pol for decaying-current phases (exp-decay, linear-decreasing) that
have NO trailing IR step and NO interphase/discharge delay.

Operator: "For exponential decay or linear decreasing phases, there is no
trailing access voltage to be obviously seen or determined … let the end of
the phase be the electrode polarization … use the absolute minimum of the
derivative in that phase to pinpoint the electrode polarization."

So `polarization_per_phase(method="operator")` reads E_pol at the FLATTEST
point (min |dV/dt|) in the latter half of such a phase — where the current
has decayed so V is the settled interface polarization with no ohmic drop —
instead of returning NaN.
"""
import numpy as np
import pytest

from stimtest.metrics import (
    polarization_per_phase, _phase_current_ends_near_zero,
)
from stimtest.waveforms import (
    Phase, PulsePattern,
    SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING,
    SHAPE_SINUSOIDAL, SHAPE_EXP_DECAY,
)


# --------------------------------------------------------------------------
# helper: which phase shapes taper to ~0 at their end (no trailing IR step)
# --------------------------------------------------------------------------
def test_helper_true_for_decaying_shapes():
    for shape in (SHAPE_EXP_DECAY, SHAPE_LINEAR_DECREASING, SHAPE_SINUSOIDAL):
        ph = Phase(amplitude_ua=-50.0, width_us=200.0, shape=shape)
        assert _phase_current_ends_near_zero(ph) is True, shape


def test_helper_false_for_nondecaying_shapes():
    # rectangular holds peak to the end; linear-increasing ends at peak
    for shape in (SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING):
        ph = Phase(amplitude_ua=-50.0, width_us=200.0, shape=shape)
        assert _phase_current_ends_near_zero(ph) is False, shape


def test_helper_false_when_offset_floor_makes_a_trailing_step():
    # a non-zero current OFFSET floor means the current ends at the floor,
    # i.e. there IS a real current-off step at the phase boundary
    ph = Phase(amplitude_ua=-50.0, width_us=200.0,
               shape=SHAPE_EXP_DECAY, offset_ua=10.0)
    assert _phase_current_ends_near_zero(ph) is False


# --------------------------------------------------------------------------
# a delay-less exp-decay / linear-decreasing phase gets a FINITE settled E_pol
# --------------------------------------------------------------------------
def _decaying_capture(shape, plateau_v=-0.5, width_us=200.0, tau_rise_us=25.0):
    """Synthetic active-electrode trace for a monophasic decaying-current
    pulse: V rises fast (IR + polarization) then flattens toward `plateau_v`
    as the current tapers.  onset at t=0, no trailing delay."""
    p = PulsePattern(phases=[Phase(amplitude_ua=-50.0, width_us=width_us,
                                   delay_after_us=0.0, shape=shape)],
                     rate_hz=100.0)
    t = np.arange(-50.0, width_us + 50.0, 1.0)
    v = np.zeros_like(t)
    inside = (t >= 0.0) & (t <= width_us)
    # charging-style rise that flattens: V(t) = plateau*(1 - e^{-t/tau})
    v[inside] = plateau_v * (1.0 - np.exp(-t[inside] / tau_rise_us))
    # after the phase, hold the last value (interpulse; irrelevant here)
    v[t > width_us] = v[inside][-1]
    return t, v, p


@pytest.mark.parametrize("shape", [SHAPE_EXP_DECAY, SHAPE_LINEAR_DECREASING])
def test_decaying_phase_epol_is_finite_and_settled(shape):
    t, v, p = _decaying_capture(shape, plateau_v=-0.5)
    res = polarization_per_phase(t, v, p, method="operator", onset_us=0.0)
    assert len(res) == 1
    epol = res[0]
    assert np.isfinite(epol), "decaying-phase E_pol must not be NaN"
    # the flattest point sits near the plateau (settled interface potential)
    assert epol == pytest.approx(-0.5, abs=0.03)


def test_decaying_phase_reads_the_flat_end_not_the_steep_rise():
    # the steep early rise (large |dV/dt|) must NOT be picked; the settled
    # near-end value (small |dV/dt|) is far more negative than mid-rise
    t, v, p = _decaying_capture(SHAPE_EXP_DECAY, plateau_v=-0.8,
                                tau_rise_us=20.0)
    epol = polarization_per_phase(t, v, p, method="operator",
                                  onset_us=0.0)[0]
    # near the plateau, well past the +/-0.4 the trace holds at mid-rise
    assert epol < -0.7


def test_rectangular_delayless_phase_is_not_settled_read():
    # a rectangular delay-less phase holds peak to the end -> NOT case 3a;
    # with no clean leading access to subtract, operator method -> NaN
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-50.0, width_us=100.0, delay_after_us=0.0,
                  shape=SHAPE_RECTANGULAR),
            Phase(amplitude_ua=+50.0, width_us=100.0, delay_after_us=0.0,
                  shape=SHAPE_RECTANGULAR),
        ],
        rate_hz=100.0,
    )
    t = np.arange(-50.0, 250.0, 1.0)
    v = np.zeros_like(t)
    # phase 2 (the delay-less, no-lead phase) -> NaN
    res = polarization_per_phase(t, v, p, method="operator", onset_us=0.0,
                                 driving_per_phase=[float("nan")] * 2,
                                 leading_access_per_phase=[float("nan")] * 2)
    assert not np.isfinite(res[1])
