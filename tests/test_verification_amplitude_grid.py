"""The verification amplitude grid and its compliance ceiling.

Widened (operator, 0.2.228) from 25/50/100/200 to twelve points reaching
500 uA.  The binding physical constraint is the stimulator's voltage
compliance: on the 4.99 kOhm + 4700 pF test board a constant-current phase
drives ``I*R + I*W/C``, which grows LINEARLY with amplitude.  Past ~575 uA
that exceeds 9 V and the waveform CLIPS -- silently truncating the capacitive
ramp and corrupting both the R and C fits, with no error raised.

So the grid can never be extended by eye.  These tests fail if a future edit
pushes it into compliance.
"""
from __future__ import annotations

import pytest

from stimtest.config import STIM_VOLTAGE_COMPLIANCE_V, STIM_MAX_AMPLITUDE_UA
from stimtest.gui.calibration import CalibrationTab as C

GRID = C.DEFAULT_AMPLITUDE_GRID_UA


def _peak_v(i_ua, r_ohm, c_pf, w_us):
    """Peak |V| at the end of a constant-current phase: iR step + cap ramp."""
    i = i_ua * 1e-6
    return i * r_ohm + i * (w_us * 1e-6) / (c_pf * 1e-12)


def test_grid_is_the_twelve_point_sweep():
    assert GRID == (25.0, 50.0, 75.0, 100.0, 150.0, 200.0,
                    250.0, 300.0, 350.0, 400.0, 450.0, 500.0)


def test_grid_is_sorted_and_unique():
    assert list(GRID) == sorted(GRID)
    assert len(set(GRID)) == len(GRID)


def test_every_amplitude_stays_within_voltage_compliance():
    for i_ua in GRID:
        pk = _peak_v(i_ua, C.DEFAULT_LOAD_OHM, C.DEFAULT_LOAD_CAP_PF,
                     C.PHASE_WIDTH_US)
        assert pk < STIM_VOLTAGE_COMPLIANCE_V, (
            f"{i_ua} uA drives {pk:.2f} V, at/over the "
            f"{STIM_VOLTAGE_COMPLIANCE_V} V compliance — the trace will clip "
            f"and both R and C fits will be wrong")


def test_top_of_grid_keeps_real_headroom():
    """Not merely under compliance — enough margin that a board slightly
    off nominal, or a longer phase, does not tip it over."""
    pk = _peak_v(max(GRID), C.DEFAULT_LOAD_OHM, C.DEFAULT_LOAD_CAP_PF,
                 C.PHASE_WIDTH_US)
    assert pk <= 0.90 * STIM_VOLTAGE_COMPLIANCE_V, (
        f"top amplitude peaks at {pk:.2f} V "
        f"({100*pk/STIM_VOLTAGE_COMPLIANCE_V:.0f} % of compliance)")


def test_the_clip_point_is_where_we_think_it_is():
    """Documents the ceiling: ~575 uA on this board.  If the load constants
    change, this test changes with them and the grid must be re-checked."""
    lo = next(i for i in range(int(max(GRID)), 1200)
              if _peak_v(i, C.DEFAULT_LOAD_OHM, C.DEFAULT_LOAD_CAP_PF,
                         C.PHASE_WIDTH_US) >= STIM_VOLTAGE_COMPLIANCE_V)
    assert max(GRID) < lo
    assert 520 <= lo <= 620, lo


def test_grid_within_hardware_current_limit():
    assert max(GRID) <= STIM_MAX_AMPLITUDE_UA


def test_low_end_still_excludes_the_unreliable_points():
    """10 uA was dropped (I_mon peak below the trigger threshold, iR step in
    the noise floor).  Re-adding it would reintroduce a known systematic."""
    assert min(GRID) >= 25.0


def test_wider_span_improves_conditioning():
    """The R fit is a slope through the origin, so leverage goes as I^2.  The
    old 4-point grid leaned 75 % on a single point; the new one must not."""
    w = [a * a for a in GRID]
    top_share = max(w) / sum(w)
    assert top_share < 0.45, f"top amplitude carries {top_share:.0%} of the fit"
    old = (25.0, 50.0, 100.0, 200.0)
    old_w = [a * a for a in old]
    assert top_share < max(old_w) / sum(old_w)
