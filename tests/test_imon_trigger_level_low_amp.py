"""The I_mon trigger threshold must always be CROSSABLE.

Bench (verification, NIL stimulator, 25 uA): ``NUMACq = 0/64`` on every
capture, V_mon spanning four ADC codes, I_mon shrinking randomly 16 -> 9 -> 6
codes across retries -- interpulse NOISE, not a pulse.  R read 120 ohm and
r2 = -3231, while 50 uA and above captured cleanly.

Two independent ways the old formula produced an uncrossable or
undiscriminable threshold:

  * ABOVE 20 uA the level was a FRACTION of amplitude (``amp x 0.25``).  That
    shrinks with the signal while the I_mon noise floor stays constant, so at
    25 uA it reached 6.25 mV -- indistinguishable from noise.
  * AT OR BELOW the branch point the level was ``(amp + 3.5) mV/uA``, which
    assumes a DEFAULT-preset stim (2.5 mV/uA).  On a NIL device (1 mV/uA)
    that sits ABOVE the peak: 23.5 mV against a 20 mV signal.

Operator: "change the threshold for low amplitude trigger adjustment to
25 uA and below, not 20 uA" -- plus the peak-fraction cap that makes that
branch reachable on a NIL device.
"""
from __future__ import annotations

import pytest

from stimtest.experiments.base import (_IMON_TRIG_LOW_AMP_UA,
                                       _IMON_TRIG_PEAK_FRAC_MAX,
                                       imon_trigger_level)

NIL = 1e-3          # V per uA
DEFAULT = 2.5e-3
PHASE = 50.0        # us -- the verification pulse


def _level(amp, k):
    return abs(imon_trigger_level(amp_ua_signed=-amp,
                                  phase_width_us=PHASE, imon_v_per_ua=k))


# ------------------------------------------------- the operator's boundary
def test_low_amp_branch_is_25_ua():
    assert _IMON_TRIG_LOW_AMP_UA == 25.0


def test_25_ua_uses_the_low_amplitude_branch():
    """25 uA must NOT fall into the fraction-of-amplitude branch, which put
    it at 6.25 mV -- inside the noise."""
    assert _level(25.0, NIL) > 6.25e-3 * 1.5


# ------------------------------------------- always crossable, both presets
@pytest.mark.parametrize("k,name", [(NIL, "NIL"), (DEFAULT, "Default")])
@pytest.mark.parametrize("amp", [10.0, 20.0, 25.0, 50.0, 100.0, 200.0])
def test_threshold_is_below_the_expected_peak(amp, k, name):
    """A threshold above the peak can never fire -- NUMACq stays 0."""
    level, peak = _level(amp, k), amp * k
    assert level < peak, f"{name} {amp} uA: {level*1e3:.2f} mV >= peak {peak*1e3:.2f} mV"


@pytest.mark.parametrize("amp", [10.0, 20.0, 25.0])
def test_low_amp_capped_at_peak_fraction_on_nil(amp):
    """On NIL the raw formula overshoots, so the cap is what rescues it."""
    assert _level(amp, NIL) == pytest.approx(_IMON_TRIG_PEAK_FRAC_MAX * amp * NIL)


# --------------------------------------------- noise-floor discrimination
def test_25_ua_clears_the_observed_noise_floor():
    """Bench interpulse noise was ~6-16 ADC codes (order 10 mV).  The old
    6.25 mV threshold sat inside it; the new one must not."""
    assert _level(25.0, NIL) > 12e-3


# -------------------------------------------- MATLAB parity where it fits
@pytest.mark.parametrize("amp", [10.0, 20.0, 25.0])
def test_default_preset_keeps_the_matlab_exact_value(amp):
    """The cap must only LOWER an unreachable threshold.  Where the
    MATLAB-exact value already fits (Default preset), it is untouched."""
    assert _level(amp, DEFAULT) == pytest.approx((amp + 3.5) * 1e-3)


def test_no_scaling_supplied_leaves_formula_unclamped():
    """Callers that pass no scaling get the historical value."""
    amp = 20.0
    assert abs(imon_trigger_level(amp_ua_signed=-amp,
                                  phase_width_us=PHASE)) == pytest.approx(
        (amp + 3.5) * 1e-3)


# ------------------------------------------------------------- polarity
def test_sign_follows_amplitude_including_signed_zero():
    """Cathodal-first (negative, incl. -0.0) pairs with the FALL slope."""
    import math
    assert imon_trigger_level(amp_ua_signed=-50.0, phase_width_us=PHASE) < 0
    assert imon_trigger_level(amp_ua_signed=+50.0, phase_width_us=PHASE) > 0
    assert math.copysign(1.0, imon_trigger_level(
        amp_ua_signed=-0.0, phase_width_us=PHASE)) < 0
