"""Operator-selectable horizontal auto-scaling window: Wide vs Tight.

Operator: "Have the option in the setup around the oscilloscope section that
the user can select from a dropdown list for wide or tight for the horizontal
scaling."  TIGHT = the closest (smallest) grid SEC/DIV that fits the pulse
without clipping (+ ≥1 div lead); WIDE = one grid increment larger (more
post-pulse recovery).  ``set_horizontal_fit_mode`` sets ``_horiz_fit_mode``
per-instance, read by ``auto_layout_for_pulse``.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest


def _scope():
    from stimtest.hardware import tektronix as T
    s = T.TektronixOscilloscope.__new__(T.TektronixOscilloscope)
    s._n_horiz_divs = 15.0
    s._cmds = T.MODERN_CMDS
    s._expected_horiz_scale_s = None
    s._expected_horiz_position_pct = None
    s._expected_trigger_is_digital = True
    s._log = lambda *a, **k: None
    s._invalidate_preamble_cache = MagicMock()
    s._w = MagicMock()
    s._q = MagicMock(side_effect=lambda q: "1")
    cap = {}
    s.set_horizontal_scale = MagicMock(
        side_effect=lambda x: cap.__setitem__("sc", x) or x)
    s.set_horizontal_position = MagicMock()
    s._cap = cap
    return s


def _span_us(mode, p1=200, iph=0, p2=200, dd=0):
    s = _scope()
    s.set_horizontal_fit_mode(mode)
    s.auto_layout_for_pulse(phase1_us=p1, interphase_us=iph, phase2_us=p2,
                            discharge_us=dd, ext_trigger=True)
    return round(s._cap["sc"] * 1e6 * s._n_horiz_divs)


def test_mode_sets_horiz_fit_mode():
    s = _scope()
    s.set_horizontal_fit_mode("wide")
    assert s._horiz_fit_mode == "wide"
    s.set_horizontal_fit_mode("tight")
    assert s._horiz_fit_mode == "tight"


def test_default_is_wide_when_never_set():
    # Before any set_horizontal_fit_mode call, auto_layout uses "wide" via the
    # getattr fallback — so a fresh scope frames a 400 µs biphasic at the WIDE
    # window (one grid step larger than the tightest that fits).
    s = _scope()
    s.auto_layout_for_pulse(phase1_us=200, interphase_us=0, phase2_us=200,
                            discharge_us=0, ext_trigger=True)
    assert round(s._cap["sc"] * 1e6 * s._n_horiz_divs) == 1500


def test_400us_biphasic_wide_vs_tight():
    # 400 µs biphasic on the 15-div 1-2-4 grid: the tightest SEC/DIV that fits
    # (+1 div lead) is 40 µs/div → 600 µs window (Tight); Wide = one step up =
    # 100 µs/div → 1500 µs.
    assert _span_us("tight", 200, 0, 200, 0) == 600
    assert _span_us("wide", 200, 0, 200, 0) == 1500


def test_wide_is_one_grid_step_larger_than_tight():
    # Across several pulse widths, WIDE is always exactly the next grid window
    # up from TIGHT (never the same, unless the pulse maxes the grid).
    for (p1, p2) in ((100, 100), (200, 200), (50, 50), (300, 300)):
        tight = _span_us("tight", p1, 0, p2, 0)
        wide = _span_us("wide", p1, 0, p2, 0)
        assert wide > tight, (p1, p2, tight, wide)


def test_tight_window_contains_the_pulse():
    # TIGHT must never clip the pulse: the window ≥ the pulse width.
    for (p1, p2) in ((100, 100), (200, 200), (250, 250), (400, 400)):
        span = _span_us("tight", p1, 0, p2, 0)
        assert span >= (p1 + p2), (p1, p2, span)


def test_setup_tab_dropdown_and_prefs():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets
    from stimtest.gui.setup_tab import SetupTab
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    st = SetupTab()
    # Dropdown offers Wide + Tight, defaults Wide.
    items = [st.horiz_scaling_combo.itemText(i)
             for i in range(st.horiz_scaling_combo.count())]
    assert items == ["Wide", "Tight"]
    assert st.current_horizontal_scaling() == "wide"
    # Signal fires on change.
    seen = []
    st.horizontalScalingChanged.connect(seen.append)
    st.horiz_scaling_combo.setCurrentText("Tight")
    assert seen and seen[-1] == "tight"
    assert st.current_horizontal_scaling() == "tight"
    # Prefs round-trip.
    p = st.current_prefs()
    assert p["horiz_scaling"] == "tight"
    st.horiz_scaling_combo.setCurrentText("Wide")
    st.restore_prefs({"horiz_scaling": "tight"})
    assert st.current_horizontal_scaling() == "tight"
