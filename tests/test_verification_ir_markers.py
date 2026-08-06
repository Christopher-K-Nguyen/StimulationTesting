"""Plus-symbol markers on the measured iR drops in the verification plot.

Operator: "I also want you to put plus symbols on the iR drops in the
verification plot."

These mark the exact points the R_load fit is built from -- one per current
edge, located by the shared |dV/dt| localizer and measured by the before/after
extrapolation.  Putting them on the trace is the fastest way to tell a bad R
fit (markers sitting off the edges) from a genuinely off-nominal board.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytest.importorskip("PyQt6")


def _src(rel):
    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / rel).read_text(encoding="utf-8")


CAL = "stimtest/gui/calibration.py"


def _capture_method(src):
    i = src.index("    def _capture_one_amplitude")
    m = re.compile(r"^    def ", re.M).search(src, i + 10)
    return src[i:m.start() if m else len(src)]


def test_plus_symbol_is_the_shared_two_stroke_path():
    """Same glyph the experiment plot uses for polarization -- two crossing
    LINE strokes, not pyqtgraph's filled '+' polygon."""
    from PyQt6 import QtGui
    from stimtest.gui.widgets import _plus_symbol
    p = _plus_symbol()
    assert isinstance(p, QtGui.QPainterPath)
    assert p.elementCount() == 4, p.elementCount()


def test_plot_uses_the_plus_symbol_and_access_colour():
    src = _src(CAL)
    assert "_plus_symbol()" in src
    assert 'MARKER_COLOURS.get("access"' in src
    # Drawn on the LEFT (voltage) axis, where V_mon lives -- not the right
    # current axis.
    assert "self._plot_widget.addItem(self._plot_ir_marks)" in src


def test_marker_state_is_initialised():
    src = _src(CAL)
    assert "self._plot_ir_marks = None" in src
    assert "self._last_ir_points: list = []" in src


def test_points_come_from_the_localizer_indices():
    """The marker x/y must be read from the SAME arrays the plot draws, via
    the localizer's own indices -- otherwise a marker can drift away from the
    value it represents."""
    body = _capture_method(_src(CAL))
    i = body.index("_va_list, _ra_list, _acc_idx = access_voltage_and_resistance")
    block = body[i:i + 1200]
    assert "for _k, _i in enumerate(_acc_idx" in block
    assert "float(t_us_arr[_ii])" in block
    assert "float(v_arr[_ii])" in block


def test_unmeasurable_drops_are_skipped():
    """A non-finite V_a means that edge was not measured; it must not get a
    marker implying it was."""
    body = _capture_method(_src(CAL))
    i = body.index("for _k, _i in enumerate(_acc_idx")
    assert "np.isfinite(_va_list[_k])" in body[i:i + 600]


def test_stale_markers_are_cleared_before_each_capture():
    """A capture whose extraction raises must show NO markers, not the
    previous capture's -- the clear has to precede the extraction."""
    body = _capture_method(_src(CAL))
    i_clear = body.index("self._last_ir_points = []")
    i_extract = body.index("access_voltage_and_resistance(")
    assert i_clear < i_extract, "stale markers survive a failed extraction"


def test_markers_hidden_when_there_are_none():
    src = _src(CAL)
    assert "self._plot_ir_marks.setVisible(False)" in src
