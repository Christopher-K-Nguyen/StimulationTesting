"""The experiment-plot trace legend parks in the LEFT corner (upper / lower)
that is clear of the waveform AND the metric labels.

Operator: "make sure that the legend does NOT intersect with the plots or
labels … considering upper left or lower left."  ``ScopePlot._position_legend``
scores each left corner by trace-sample + label occupancy and anchors the
legend to the lower-cost one.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6 import QtWidgets  # noqa: E402
from stimtest.gui.widgets import ScopePlot  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _scope():
    sp = ScopePlot()
    sp.resize(500, 320)
    sp.show()
    QtWidgets.QApplication.processEvents()
    calls = []
    sp._place_legend = lambda *, corner: calls.append(corner)
    return sp, calls


def _set(sp, v):
    t = np.linspace(-60.0, 600.0, 600)
    sp.set_traces(t, {"Vmon": v(t)}, colors={"Vmon": "#E6B800"},
                  axis={"Vmon": "left"})
    sp._plot.getViewBox().setYRange(-1.0, 1.0, padding=0)
    sp._plot.getViewBox().setXRange(-60.0, 600.0, padding=0)
    QtWidgets.QApplication.processEvents()


def _neutral_marker():
    # A marker in the middle of the plot so it doesn't bias the corner choice.
    return [("V_d", 300.0, 0.0, "V_d = 0 V", "#000000", "hbar", None, True)]


def test_legend_goes_upper_left_when_trace_dips_lower_left(_app):
    sp, calls = _scope()
    # Cathodic dip in the LEFT region → lower-left occupied → legend to UL.
    def v(t):
        out = np.zeros_like(t)
        out[(t >= 0) & (t <= 120)] = -0.8
        return out
    _set(sp, v)
    sp.set_markers(_neutral_marker())
    assert calls and calls[-1] == "UL", calls


def test_legend_goes_lower_left_when_trace_rises_upper_left(_app):
    sp, calls = _scope()
    # Anodic rise in the LEFT region → upper-left occupied → legend to LL.
    def v(t):
        out = np.zeros_like(t)
        out[(t >= 0) & (t <= 120)] = +0.8
        return out
    _set(sp, v)
    sp.set_markers(_neutral_marker())
    assert calls and calls[-1] == "LL", calls


def test_legend_avoids_a_label_in_the_corner(_app):
    sp, calls = _scope()
    _set(sp, lambda t: np.zeros_like(t))    # flat trace — labels drive it
    # A label parked in the UPPER-LEFT region → legend should choose LL.
    sp.set_markers([("V_a1", -40.0, 0.85, "V_a1 = 0.9 V",
                     "#000000", "hbar", None, True)])
    assert calls and calls[-1] == "LL", calls


def test_legend_falls_back_to_right_when_both_left_corners_have_labels(_app):
    # Operator: "the legend can overlap the marker label."  When BOTH left
    # corners are occupied by marker labels, the legend must fall back to a
    # RIGHT corner instead of overlapping a label.
    sp, calls = _scope()
    _set(sp, lambda t: np.zeros_like(t))    # flat trace — labels drive it
    sp.set_markers([
        ("V_a1", -45.0, 0.9, "V_a1 = 0.9 V", "#000000", "hbar", None, True),
        ("V_a2", -45.0, -0.9, "V_a2 = 0.9 V", "#000000", "hbar", None, True),
    ])
    assert calls and calls[-1] in ("UR", "LR"), calls


def test_legend_call_happens_every_capture(_app):
    sp, calls = _scope()
    _set(sp, lambda t: np.zeros_like(t))
    sp.set_markers(_neutral_marker())
    assert calls, "legend was never (re)positioned"
    assert calls[-1] in ("UL", "LL")
