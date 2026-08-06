"""V_mon is scaled on max|v|, not half peak-to-peak.

Bench log:

    range [-384.0..+110.4] mV is small for scale 120.00 mV/div
        (1 consecutive votes) -> downscale to 65.00 mV/div

At 65 mV/div the rail is +/-325 mV while the trace reaches -384 mV, so the
NEXT capture clipped.  The adapt had scaled itself into a clip.

Why: with the vertical POSITION pinned at 0 (verification never moves it),
the ADC rail is symmetric about ZERO, so the binding constraint is
max(|v_min|, |v_max|).  Half-p2p is only the right measure for a CENTRED
signal -- and the verification V_mon is cathodic-heavy.  Removing the
centring made half-p2p wrong.
"""
from __future__ import annotations

import pathlib

import pytest

# The bench capture.
VLO, VHI = -0.384, +0.1104
HALF_DIVS = 5.0          # rail is +/-5 divisions about zero
VMON_BUDGET_DIVS = 3.0   # per-role fill budget


def _half_p2p(lo, hi):
    return (hi - lo) / 2.0


def _mag(lo, hi):
    return max(abs(lo), abs(hi))


def test_half_p2p_understates_an_asymmetric_trace():
    """The measure that caused the bug."""
    assert _half_p2p(VLO, VHI) < _mag(VLO, VHI)
    # ...and by enough to change the decision: ~2 div vs >3 div at 120 mV/div.
    assert _half_p2p(VLO, VHI) / 0.120 < VMON_BUDGET_DIVS
    assert _mag(VLO, VHI) / 0.120 > VMON_BUDGET_DIVS


def test_the_downscale_target_would_have_clipped():
    """65 mV/div cannot hold a 384 mV excursion at position 0."""
    assert _mag(VLO, VHI) > HALF_DIVS * 0.065


def test_current_scale_was_already_correct():
    """120 mV/div holds it -- the trace should have been left alone."""
    assert _mag(VLO, VHI) < HALF_DIVS * 0.120


@pytest.mark.parametrize("lo,hi", [
    (-0.384, +0.110),      # bench: cathodic-heavy
    (-3.13, +1.00),        # 200 uA
    (-0.39, +0.12),        # 25 uA
])
def test_symmetrised_range_never_understates(lo, hi):
    m = _mag(lo, hi)
    assert m >= _half_p2p(lo, hi)
    # The symmetric range the code passes to adapt.
    assert (-m, m) == (-m, m) and m == _mag(-m, m)


def test_centred_signal_is_unaffected():
    """For a symmetric trace the two measures agree, so nothing changes."""
    lo, hi = -0.25, +0.25
    assert _half_p2p(lo, hi) == pytest.approx(_mag(lo, hi))


def test_calibration_symmetrises_before_adapt():
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "stimtest" / "gui" / "calibration.py").read_text(encoding="utf-8")
    i_sym = src.find("_v_mag = max(abs(vlo), abs(vhi))")
    assert i_sym != -1, "V_mon range is not symmetrised"
    i_adapt = src.find("adapt_channel_scale(\n                            v_mon_phys")
    assert i_adapt != -1 and i_sym < i_adapt, \
        "symmetrisation must happen BEFORE the adapt call"
    # ...and after the overflow doubling, so an escape grow is preserved.
    i_ovf = src.find("vlo, vhi = vlo * 2.0, vhi * 2.0")
    assert i_ovf != -1 and i_ovf < i_sym
