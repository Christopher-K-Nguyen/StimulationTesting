"""Trigger (horizontal) position is rounded to the NEAREST 10%.

Operator: "I want it rounded to the nearest 10%."  This is NEAREST (round-half),
NOT the old FLOOR (``int(pct/10)*10``) that was removed for collapsing a small
few-% offset DOWN to 0% and jamming the leading edge against the trigger.  The
auto-layout floors the pre-trigger offset at 1 division, so the minimum rounds
to 10% (never 0%) on both 15-div and 10-div scopes.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


def _bare_scope(divs):
    from stimtest.hardware.tektronix import TektronixOscilloscope, MODERN_CMDS
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._n_horiz_divs = divs
    s._cmds = MODERN_CMDS
    s._expected_horiz_scale_s = None
    s._expected_horiz_position_pct = None
    s._log = lambda *a, **k: None
    s._invalidate_preamble_cache = MagicMock()
    s._w = MagicMock()
    s.set_horizontal_scale = MagicMock(side_effect=lambda x: x)
    s.set_horizontal_position = MagicMock()
    s._q = MagicMock(side_effect=lambda q: "4e-5" if "SCA" in q.upper() else "10")
    return s


@pytest.mark.parametrize("divs", [15, 10])
@pytest.mark.parametrize("p1,p2", [(200, 200), (50, 50), (1000, 1000), (20, 20)])
def test_auto_layout_rounds_position_to_nearest_10(divs, p1, p2):
    s = _bare_scope(divs)
    s.auto_layout_for_pulse(phase1_us=float(p1), phase2_us=float(p2),
                            ext_trigger=False)
    pos = s.set_horizontal_position.call_args[0][0]
    assert pos % 10 == 0, (divs, p1, p2, pos)      # a clean multiple of 10 %
    assert pos >= 10.0, (divs, p1, p2, pos)        # never collapses to 0 %
    assert pos <= 100.0
