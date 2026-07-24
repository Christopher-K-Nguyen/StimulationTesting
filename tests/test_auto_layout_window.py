"""Auto-fit horizontal window sizing (``auto_layout_for_pulse``).

Operator (comparing to old MATLAB captures on a TBS1104B): the TBS2204B has a
different SEC/DIV grid (1-2-4 vs 1-2.5-5) AND 15 divisions vs 10, so the same
pulse frames a different capture window.  The scope FLOORS off-grid SEC/DIV
writes to its native grid (verified live on the bench), so the achievable
windows are coarse (…600, 1500, 3000 µs on 15 divs).  Operator #8 redefined
the two modes as GRID-STEP: TIGHT = the closest (smallest) SEC/DIV whose window
still fully contains the pulse (+ ≥1 div lead); WIDE (default) = ONE grid
increment larger.  So a 400 µs pulse → tight 600 µs / wide 1500 µs; a 700 µs
pulse → tight 1500 µs / wide 3000 µs.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _make_scope(divs, cmds):
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._n_horiz_divs = float(divs)
    s._cmds = cmds
    s._expected_horiz_scale_s = None
    s._expected_horiz_position_pct = None
    s._expected_trigger_is_digital = True
    s._log = lambda *a, **k: None
    s._invalidate_preamble_cache = MagicMock()
    s._w = MagicMock()
    s._q = MagicMock(side_effect=lambda q: "1")
    cap = {}
    s.set_horizontal_scale = MagicMock(
        side_effect=lambda x: cap.__setitem__("scale_s", x) or x)
    s.set_horizontal_position = MagicMock(
        side_effect=lambda p: cap.__setitem__("pos", p))
    s._cap = cap
    return s


def _span_us(divs, cmds, p1, iph, p2, dd):
    s = _make_scope(divs, cmds)
    s.auto_layout_for_pulse(phase1_us=p1, interphase_us=iph,
                            phase2_us=p2, discharge_us=dd, ext_trigger=True)
    scale_us = s._cap["scale_s"] * 1e6
    return round(scale_us * divs), round(scale_us * 1e6) / 1e6


def _tbs2204b():
    from stimtest.hardware.tektronix import MODERN_CMDS
    return 15, MODERN_CMDS


def test_default_mode_is_wide():
    # A fresh scope (no set_horizontal_fit_mode) frames at the WIDE window.
    divs, cmds = _tbs2204b()
    span, _scale = _span_us(divs, cmds, 200, 0, 200, 0)   # 400 µs biphasic
    assert span == 1500                                    # wide = 1500 (not tight 600)


@pytest.mark.parametrize(
    "p1,iph,p2,dd, expected_span_us",
    [
        # DEFAULT = WIDE (one grid step larger than the tightest that fits).
        # 200/200 biphasic (totalPulse 400): tight 600 → wide 1500 µs.
        (200, 0, 200, 0, 1500),
        # 200/100/200/200 (totalPulse 700): tight 1500 → wide 3000 µs (operator
        # #8: wide is one grid increment larger than tight, even if the pulse
        # then fills less of the screen).
        (200, 100, 200, 200, 3000),
        # 200/100/200 (totalPulse 500): tight 600 → wide 1500 µs.
        (200, 100, 200, 0, 1500),
        # A short 100/100 biphasic (totalPulse 200): tight 300 → wide 600 µs.
        (100, 0, 100, 0, 600),
    ],
)
def test_tbs2204b_window(p1, iph, p2, dd, expected_span_us):
    divs, cmds = _tbs2204b()
    span, _scale = _span_us(divs, cmds, p1, iph, p2, dd)
    assert span == expected_span_us, (p1, iph, p2, dd, span)


def test_400us_pulse_widened_vs_old_30pct():
    """The specific operator complaint: a 400 µs biphasic framed to only
    600 µs at the old 30 % floor; the 25 % floor gives the wider 1500 µs."""
    divs, cmds = _tbs2204b()
    span, _ = _span_us(divs, cmds, 200, 0, 200, 0)
    assert span == 1500          # wider MATLAB-style window
    # Sanity: the OLD 0.30 floor would have picked 600 µs here.
    s = _make_scope(divs, cmds)
    scale_us = None
    for cand in s._horiz_scale_candidates_s:
        f = 400.0 / (cand * 1e6 * divs)
        if f >= 0.30:
            scale_us = cand * 1e6
            break
    assert round(scale_us * divs) == 600
