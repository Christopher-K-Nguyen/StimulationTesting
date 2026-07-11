"""Experiment / POLARIS X-range = the MATLAB rule, verbatim.

Operator: "used the same time range adjustment as what I did in MATLAB."
The MATLAB (``getPlot_Tek.m`` / ``getAcutePlot3.m``) is literally::

    xMin = round(min(time),1,'significant');
    xMax = round(max(time),1,'significant');
    xlim([xMin xMax]);

i.e. the full captured time extent with each end rounded to 1 significant
figure — nothing more.  One end may round INWARD (a small sliver clipped)
and the other OUTWARD, exactly like MATLAB.  This REPLACED an earlier
half-tick floor/ceil grid (never-crop + pad-half-a-tick) that layered extra
framing on top of the MATLAB rule.

Both the live PULSAR plot (``widgets._matlab_x_range``) and the POLARIS
export (``plotting._matlab_x_limits``) follow the identical rule so a saved
``.tif`` frames a capture the same as the on-screen experiment plot.  The
rounding is half-AWAY-from-zero (MATLAB), not Python's half-to-even, so an
exact-half bound like -45 µs frames to -50 (not -40).
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from stimtest import plotting


def _matlab_round_1sig(x: float) -> float:
    """Reference MATLAB ``round(x, 1, 'significant')`` (half away from zero)."""
    if x == 0.0:
        return 0.0
    d = math.ceil(math.log10(abs(x)))
    scale = 10.0 ** (d - 1)
    s = x / scale
    r = math.floor(s + 0.5) if s >= 0.0 else math.ceil(s - 0.5)
    return r * scale


# (data_lo, data_hi, expected_xmin, expected_xmax) — expected = MATLAB
# round(_,1,'significant') of each end.
_CASES = [
    (-64.0, 576.0, -60.0, 600.0),     # 20k×32ns record, 10 % trigger (the real case)
    (-45.15, 594.82, -50.0, 600.0),   # exp_vt_max capture
    (-45.0, 540.0, -50.0, 500.0),     # exact-half lo (-45→-50 away-from-zero); 540→500
    (-320.0, 320.0, -300.0, 300.0),   # symmetric
    (-6.4, 193.6, -6.0, 200.0),       # tiny pre-trigger
    (0.0, 440.0, 0.0, 400.0),         # no pre-trigger
    (-128.0, 512.0, -100.0, 500.0),   # 20 % trigger
]


@pytest.mark.parametrize("lo, hi, exp_min, exp_max", _CASES)
def test_export_matches_matlab_round_1sig(lo, hi, exp_min, exp_max):
    t = np.linspace(lo, hi, 5000)
    ticks, xmin, xmax = plotting._matlab_x_limits(t)
    assert xmin == pytest.approx(exp_min), (lo, hi, xmin)
    assert xmax == pytest.approx(exp_max), (lo, hi, xmax)
    # sanity: the expected values ARE the MATLAB reference of the data extent
    assert xmin == pytest.approx(_matlab_round_1sig(float(t.min())))
    assert xmax == pytest.approx(_matlab_round_1sig(float(t.max())))


@pytest.mark.parametrize("lo, hi, exp_min, exp_max", _CASES)
def test_live_matches_matlab_round_1sig(lo, hi, exp_min, exp_max):
    pytest.importorskip("pyqtgraph")
    from stimtest.gui import widgets
    t = np.linspace(lo, hi, 5000)
    lv = widgets._matlab_x_range(t)
    assert lv == pytest.approx((exp_min, exp_max)), (lo, hi, lv)


@pytest.mark.parametrize("lo, hi, exp_min, exp_max", _CASES)
def test_live_and_export_are_identical(lo, hi, exp_min, exp_max):
    """gotcha #68 invariant: the saved figure frames a capture identically
    to the on-screen experiment plot."""
    pytest.importorskip("pyqtgraph")
    from stimtest.gui import widgets
    t = np.linspace(lo, hi, 5000)
    _, ex_min, ex_max = plotting._matlab_x_limits(t)
    lv_min, lv_max = widgets._matlab_x_range(t)
    assert lv_min == pytest.approx(ex_min), (lo, hi)
    assert lv_max == pytest.approx(ex_max), (lo, hi)


@pytest.mark.parametrize("lo, hi, exp_min, exp_max", _CASES)
def test_export_ticks_within_limits_and_on_grid(lo, hi, exp_min, exp_max):
    t = np.linspace(lo, hi, 5000)
    ticks, xmin, xmax = plotting._matlab_x_limits(t)
    assert len(ticks) >= 2
    step = ticks[1] - ticks[0]
    eps = 1e-6
    for tk in ticks:
        assert xmin - eps <= tk <= xmax + eps, (tk, xmin, xmax)
        assert abs(round(tk / step) * step - tk) < eps, tk


def test_calibration_round_sig_is_half_away_from_zero():
    """The calibration plot shares the MATLAB half-away rounding so its X
    bounds match the experiment plot for the same waveform."""
    from stimtest.gui.calibration import _round_sig as cal_round
    from stimtest.gui.widgets import _round_sig as wid_round
    for x, expected in [(-45.0, -50.0), (-64.0, -60.0), (576.0, 600.0),
                        (250.0, 300.0), (-5.0, -5.0), (0.0, 0.0)]:
        assert cal_round(x, 1) == pytest.approx(expected), x
        assert wid_round(x, 1) == pytest.approx(expected), x
        assert cal_round(x, 1) == pytest.approx(wid_round(x, 1)), x


def test_degenerate_range_returns_none():
    # A single unique sample → hi <= lo after rounding → None (caller falls
    # back to the raw extents).
    t = np.zeros(10)
    assert plotting._matlab_x_limits(t) is None
