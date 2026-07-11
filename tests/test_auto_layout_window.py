"""Auto-fit horizontal window sizing (``auto_layout_for_pulse``).

Operator (comparing to old MATLAB captures on a TBS1104B): the TBS2204B has a
different SEC/DIV grid (1-2-4 vs 1-2.5-5) AND 15 divisions vs 10, so the same
pulse frames a different — often TIGHTER — capture window.  The scope FLOORS
off-grid SEC/DIV writes to its native grid (verified live on the bench), so
the achievable windows are coarse (…600, 1500, 3000 µs on 15 divs).  The
operator chose the WIDER (MATLAB-style) window, implemented by lowering the
minimum-fill floor from 0.30 to 0.25 (``_AUTO_FIT_MIN_FILL``): a 400 µs pulse
now takes the 1500 µs step instead of dropping to 600 µs, while a 700 µs pulse
still rejects the near-empty 3000 µs step and stays at 1500 µs.
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


def test_default_min_fill_is_025():
    from stimtest.hardware.tektronix import TektronixOscilloscope
    assert TektronixOscilloscope._AUTO_FIT_MIN_FILL == 0.25


@pytest.mark.parametrize(
    "p1,iph,p2,dd, expected_span_us",
    [
        # 200/200 biphasic (the operator's exp_vt pattern, totalPulse 400):
        # was 600 µs at the old 0.30 floor; now the wider 1500 µs step.
        (200, 0, 200, 0, 1500),
        # 200/100/200/200 (totalPulse 700): stays 1500 µs — must NOT jump to
        # the near-empty 3000 µs step (fill 0.233 < 0.25).
        (200, 100, 200, 200, 1500),
        # 200/100/200 (totalPulse 500): 1500 µs.
        (200, 100, 200, 0, 1500),
        # A short 100/100 biphasic (totalPulse 200): tight 600 µs is correct
        # (200/(100·15)=0.133 < 0.25 rejects 100 µs/div; 40 µs/div fills 0.33).
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
