"""ScopePlot inset: I_mon → a SINGLE current axis (µA OR A/cm² per the unit
dropdown), NO density right axis, and no width squish.

Operator:
  * "For the inset with Imon, do not have a right axis like I previously
    asked.  Only change between current and current density based on the
    dropdown list unit."  (Reverses the earlier dual current-left /
    density-right request.)
  * "I am still getting the inset width being squished into a square
    occasionally."
  * "I want numbering on the y axis if there is a label on the inset."
"""
from __future__ import annotations

import numpy as np
import pytest

from PyQt6 import QtWidgets

pg = pytest.importorskip("pyqtgraph")
from stimtest.gui.widgets import ScopePlot  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _sp(_app):
    sp = ScopePlot()
    if sp._inset is None:
        pytest.skip("pyqtgraph not available")
    return sp


def test_inset_trace_need_not_be_in_main_plot(_app):
    """A trace routed to AXIS_NA is STORED (so the inset can draw it) but NOT
    drawn in the main plot (operator: "The inset trace should not have to also
    be in the main plot as well")."""
    from stimtest.gui.widgets import AXIS_LEFT, AXIS_NA
    sp = _sp(_app)
    t = np.linspace(-50, 250, 200)
    sp.set_traces(t, {"Vmon": np.sin(t / 40), "Imon": np.cos(t / 40)},
                  colors={"Vmon": "#E6B800", "Imon": "#00B4C8"},
                  axis={"Vmon": AXIS_LEFT, "Imon": AXIS_NA},
                  remove_missing=True)
    # I_mon is AXIS_NA → data stored, NOT a main-plot curve.
    assert "Imon" not in sp._curves
    assert "Imon" in sp._curve_data
    assert "Vmon" in sp._curves
    # …yet the inset can draw it.
    sp.set_inset_traces(["Imon"])
    sp.set_inset_visible(True)
    assert "Imon" in sp._inset_curves
    # Flipping I_mon back to a real axis re-draws it in the main plot.
    sp.set_traces(t, {"Vmon": np.sin(t / 40), "Imon": np.cos(t / 40)},
                  colors={"Vmon": "#E6B800", "Imon": "#00B4C8"},
                  axis={"Vmon": AXIS_LEFT, "Imon": "right"},
                  remove_missing=True)
    assert "Imon" in sp._curves


def test_imon_inset_ua_mode(_app):
    """µA mode: left axis = Current [µA], data drawn as-is, NO density right
    axis (right = numberless box mirror)."""
    sp = _sp(_app)
    t = np.linspace(-50, 250, 200)
    imon_ua = 100.0 * np.sin(np.linspace(0, 6, 200))
    sp.set_traces(t, {"Imon": imon_ua}, colors={"Imon": "#00B4C8"},
                  axis={"Imon": "right"})
    sp.set_inset_current_scale("Imon", density=False)
    sp.set_inset_traces(["Imon"])
    sp.set_inset_visible(True)
    assert sp._inset_is_current is True
    assert sp._inset_left_title.text() == "Current [µA]"
    assert sp._inset_right_title.text() == ""          # NO density right axis
    assert sp._inset.getAxis("left").style.get("showValues") is True
    # Right = numberless mirror: reserves width but shows no numbers.
    ir = sp._inset.getAxis("right")
    assert all(s == "" for s in ir.tickStrings([50.0, 100.0], 1.0, 50.0))
    # Data drawn as-is in µA (peak ≈ 100), not converted.
    y = sp._inset_curves["Imon"].getData()[1]
    assert abs(float(np.max(y)) - 100.0) < 1e-2


def test_imon_inset_density_mode(_app):
    """Density mode: left axis = Current Density [A/cm²], data drawn as-is
    (already A/cm² from the main plot's density mode), NO right axis."""
    sp = _sp(_app)
    t = np.linspace(-50, 250, 200)
    density = 2.0 * np.sin(np.linspace(0, 6, 200))     # A/cm² (main in density mode)
    sp.set_traces(t, {"Imon": density}, axis={"Imon": "right"})
    sp.set_inset_current_scale("Imon", density=True)
    sp.set_inset_traces(["Imon"])
    sp.set_inset_visible(True)
    assert sp._inset_is_current is True
    assert sp._inset_left_title.text() == "Current Density [A/cm²]"
    assert sp._inset_right_title.text() == ""          # NO right axis
    assert sp._inset.getAxis("left").style.get("showValues") is True
    ir = sp._inset.getAxis("right")
    assert all(s == "" for s in ir.tickStrings([1.0, 2.0], 1.0, 1.0))
    # Data drawn as-is in A/cm² (peak ≈ 2.0), NOT converted to µA.
    y = sp._inset_curves["Imon"].getData()[1]
    assert abs(float(np.max(y)) - 2.0) < 1e-2


def test_voltage_inset_keeps_voltage_axes(_app):
    sp = _sp(_app)
    t = np.linspace(-50, 250, 100)
    sp.set_traces(t, {"Vmon": 0.1 * np.sin(np.linspace(0, 6, 100))})
    sp.set_inset_current_scale(None)               # clear current mode
    sp.set_inset_traces(["Vmon"])
    sp.set_inset_visible(True)
    assert sp._inset_is_current is False
    assert sp._inset_left_title.text() == "Voltage [V]"
    assert sp._inset_right_title.text() == ""
    # Voltage inset right axis = numberless mirror: it RESERVES width (so the
    # plot columns align — showValues=True) but its tick STRINGS are all empty
    # (no visible numbers).  The left axis shows real numbers.
    ir = sp._inset.getAxis("right")
    assert all(s == "" for s in ir.tickStrings([0.05, 0.1], 1.0, 0.05))
    assert sp._inset.getAxis("left").style.get("showValues") is True  # labelled


def test_inset_current_to_voltage_transition(_app):
    """Switching current (density) → voltage swaps the LEFT title back to
    Voltage; the right axis stays a numberless box mirror throughout (no stale
    density formatter, no numbers)."""
    sp = _sp(_app)
    t = np.linspace(-50, 250, 100)
    sp.set_traces(t, {"Imon": np.full(100, 2.0)}, axis={"Imon": "right"})
    sp.set_inset_current_scale("Imon", density=True)
    sp.set_inset_traces(["Imon"]); sp.set_inset_visible(True)
    assert sp._inset_left_title.text() == "Current Density [A/cm²]"
    assert all(s == "" for s in sp._inset.getAxis("right").tickStrings(
        [1.0, 2.0], 1.0, 1.0))
    # → voltage: left title back to Voltage, right stays a numberless mirror.
    sp.set_traces(t, {"Vmon": 0.1 * np.sin(np.linspace(0, 6, 100))})
    sp.set_inset_current_scale(None)
    sp.set_inset_traces(["Vmon"])
    assert sp._inset_left_title.text() == "Voltage [V]"
    assert all(s == "" for s in sp._inset.getAxis("right").tickStrings(
        [0.05, 0.1], 1.0, 0.05))


def test_inset_not_squished_horizontal_expanding(_app):
    """Inset horizontal size policy is Expanding + has a min width, so it
    can't settle into a square (operator: 'squished into a square')."""
    sp = _sp(_app)
    assert (sp._inset.sizePolicy().horizontalPolicy()
            == QtWidgets.QSizePolicy.Policy.Expanding)
    assert sp._inset.minimumWidth() >= 120


def test_align_inset_axes_never_leaves_a_squish_reserve(_app):
    """``_align_inset_axes`` must NEVER leave the inset axes on a huge reserve
    — the persistent-squish cause (operator: 'even besides the first capture,
    the inset can be squished').  When the widget isn't laid out yet it pins a
    SANE default reserve (autoExpand OFF), never a garbage measurement and
    never autoExpand-on (which reserves a huge width for a flat trace)."""
    sp = _sp(_app)
    sp.set_inset_visible(True)
    sp.set_inset_traces(["Vmon"])
    t = np.linspace(-64, 576, 200)
    sp.set_traces(t, {"Vmon": 0.2 * np.sin(t / 40.0)}, axis={"Vmon": "left"})
    sp._align_inset_axes()
    il = sp._inset.getAxis("left")
    # autoExpand is OFF and the reserve is clamped small — no squish possible.
    assert il.style.get("autoExpandTextSpace") is False
    ttw = il.style.get("tickTextWidth")
    assert isinstance(ttw, (int, float)) and ttw <= 96, ttw


def test_inset_matches_main_width_when_enabled_before_show(_app):
    """First-capture regression: enabling the inset BEFORE the panel is shown
    must NOT leave it squished — once shown, the inset plot area matches the
    main plot's width (operator: 'I am still seeing the first capture inset
    being squished into a square')."""
    from PyQt6 import QtCore
    sp = ScopePlot()
    if sp._inset is None:
        pytest.skip("build lacks the inset")
    sp.set_inset_visible(True)
    sp.set_inset_traces(["Vmon"])
    t = np.linspace(-64, 576, 200)
    sp.set_traces(t, {"Vmon": 0.2 * np.sin(t / 40.0)}, axis={"Vmon": "left"})
    win = QtWidgets.QWidget()
    lay = QtWidgets.QVBoxLayout(win)
    lay.addWidget(sp)
    win.resize(900, 600)
    win.show()
    for _ in range(8):                       # let the deferred re-align fire
        _app.processEvents()
        QtCore.QThread.msleep(5)
    main_w = sp._plot.width()
    inset_w = sp._inset.width()
    # The inset fills essentially the whole block width, like the main plot —
    # NOT collapsed to a ~square (the bug had inset ≈ 209 vs main ≈ 824).
    assert inset_w >= 0.85 * main_w, (inset_w, main_w)
    win.close()


# ---------------------------------------------------------------------------
# Inset trace-picker combo (_RichComboBox) — dropdown must not blow up to
# full-screen height (operator screenshot: giant empty black popup).
# ---------------------------------------------------------------------------
def test_rich_combo_delegate_rows_are_compact(_app):
    """The delegate sizeHint must return a COMPACT per-row height even when
    the option rect is viewport-sized (the popup-sizing pass passes a huge
    rect; flooring the row at option.rect.height() made every row gigantic)."""
    from PyQt6 import QtCore, QtGui
    from stimtest.gui.multichannel_scope import _RichComboBox
    cb = _RichComboBox()
    cb.addItem("(none)", userData="")
    cb.addItem("<i>V</i><sub>mon</sub>", userData="V_mon")
    cb.addItem("<i>E</i><sub>ret</sub>", userData="E_ret")
    delegate = cb.itemDelegate()
    model = cb.model()
    opt = QtWidgets.QStyleOptionViewItem()
    opt.rect = QtCore.QRect(0, 0, 200, 900)      # viewport-sized (the bug trigger)
    opt.font = cb.font()
    heights = [delegate.sizeHint(opt, model.index(r, 0)).height()
               for r in range(cb.count())]
    assert max(heights) < 60, f"row heights not compact: {heights}"
    # Non-zero width too (text actually measured).
    assert all(delegate.sizeHint(opt, model.index(r, 0)).width() > 0
               for r in range(cb.count()))
    cb.deleteLater()
