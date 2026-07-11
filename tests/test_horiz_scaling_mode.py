"""Operator-selectable horizontal auto-scaling window: Wide vs Tight.

Operator: "Have the option in the setup around the oscilloscope section that
the user can select from a dropdown list for wide or tight for the horizontal
scaling."  Wide = wider capture window (more post-pulse recovery, MATLAB-like);
Tight = the pulse fills more of the screen.  ``set_horizontal_fit_mode`` sets
the ``auto_layout_for_pulse`` fill floor per-instance.
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


def test_mode_sets_fill_floor():
    s = _scope()
    s.set_horizontal_fit_mode("wide")
    assert s._auto_fit_min_fill == pytest.approx(s._HORIZ_FIT_WIDE)
    s.set_horizontal_fit_mode("tight")
    assert s._auto_fit_min_fill == pytest.approx(s._HORIZ_FIT_TIGHT)
    # tight must require MORE fill (→ tighter window) than wide.
    assert s._HORIZ_FIT_TIGHT > s._HORIZ_FIT_WIDE


def test_default_is_wide_when_never_set():
    # Before any set_horizontal_fit_mode call, auto_layout uses the class
    # default (= wide) via getattr fallback.
    assert _span_us.__module__          # sanity
    from stimtest.hardware.tektronix import TektronixOscilloscope as TO
    assert TO._AUTO_FIT_MIN_FILL == pytest.approx(TO._HORIZ_FIT_WIDE)


def test_400us_biphasic_wide_vs_tight():
    # The clear-cut case: 400 µs biphasic → 1500 µs (Wide) vs 600 µs (Tight).
    assert _span_us("wide", 200, 0, 200, 0) == 1500
    assert _span_us("tight", 200, 0, 200, 0) == 600


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
