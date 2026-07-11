"""LP tracking plot is fed even when snapshot arrays are trimmed.

Operator: "Like PS, have LP have a similar plot of tracking metrics."

LP already CONSTRUCTS the shared TrackingPlot sub-tab (same as PS) — the
real defect was that it stayed EMPTY during a live run: LP trims a
snapshot's raw arrays (`v_mon_v` … `time_us` → None) to save memory
(gotcha #11), which crashed the multichannel-scope render on `None.size`;
`_on_capture` renders the scope BEFORE feeding the tracking plot, so the
swallowed exception starved the tracking curves.  Fixes: the scope render
NONE-guards / early-outs on a trimmed capture, and `_on_capture` isolates
the scope render from the tracking feed.
"""
from __future__ import annotations

import os
from datetime import datetime

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# No prefs in a headless run → skip the MODAL first-launch admin dialog.
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
pytest.importorskip("PyQt6")
pytest.importorskip("pyqtgraph")

from PyQt6 import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _snapshot(idx, *, trim):
    from stimtest.metrics import compute_metrics
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=-50.0)
    c = Capture(index=idx, pattern=p)
    t = np.linspace(-50.0, 450.0, 1500)
    c.time_us = t
    m = (t >= 0.0) & (t <= 200.0)
    v = np.zeros_like(t)
    v[m] = -0.5 + (-1.2 + 0.5) * (1.0 - np.exp(-t[m] / 80.0))   # IR + charge
    c.v_mon_v = v
    c.i_mon_ua = np.where(m, -50.0, 0.0)
    c.timestamp = datetime.now()
    c.kind = "snapshot"
    compute_metrics(c, surface_area_um2=5000.0)
    if trim:                       # LP drops the raw arrays post-metrics
        c.v_mon_v = c.i_mon_ua = c.e_act_v = c.e_ret_v = c.time_us = None
    return c


# ---------------------------------------------- scope survives a trimmed cap
def test_scope_add_capture_survives_trimmed_snapshot(_app):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    # A fully-trimmed snapshot must NOT raise in the render path.
    mcs.add_capture(_snapshot(0, trim=True), "CH01")
    mcs.add_capture(_snapshot(1, trim=True), "CH01")
    assert len(mcs._pages["CH01"]._captures) == 2
    mcs.deleteLater()


# ---------------------------------------------- LP tracking is fed
def test_lp_has_tracking_tab_and_is_fed_on_trimmed_snapshots(_app):
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    lp = w.lp_tab
    # LP owns a TrackingPlot, exposed as a "Tracking" sub-tab (like PS).
    assert getattr(lp, "tracking_plot", None) is not None
    labels = {t.tabText(i)
              for t in lp.findChildren(QtWidgets.QTabWidget)
              for i in range(t.count()) if t.tabText(i)}
    assert "Tracking" in labels, labels

    tp = lp.tracking_plot
    for i in range(4):
        lp._on_capture(_snapshot(i, trim=True), "CH01")   # trimmed = the real LP case
    n_series = len(tp._series)
    n_samples = sum(len(v) for v in tp._series.values())
    assert n_series > 0, "tracking plot got no series from trimmed snapshots"
    assert n_samples == 4 * n_series, (n_series, n_samples)
    w.close()


# ---------------------------------------------- per-metric L/R axis checkboxes
def test_tracking_axis_checkboxes_are_mutually_exclusive(_app):
    """Operator: "two checkboxes for left or right axes plotting for each
    metric.  Only one of the two can be selected, selecting the other will
    immediately deselect the other" — plus untick-both = hidden."""
    from stimtest.gui.tracking_plot import (TrackingPlot, AXIS_LEFT,
                                            AXIS_NA, AXIS_RIGHT)
    tp = TrackingPlot()
    short = next(iter(tp._metric_left_chk))
    L = tp._metric_left_chk[short]
    R = tp._metric_right_chk[short]

    L.setChecked(True)
    assert (tp._metric_axes[short], L.isChecked(), R.isChecked()) == \
        (AXIS_LEFT, True, False)
    # Selecting R immediately deselects L.
    R.setChecked(True)
    assert (tp._metric_axes[short], L.isChecked(), R.isChecked()) == \
        (AXIS_RIGHT, False, True)
    # Unticking the active side hides the metric.
    R.setChecked(False)
    assert (tp._metric_axes[short], L.isChecked(), R.isChecked()) == \
        (AXIS_NA, False, False)
    tp.deleteLater()


def test_tracking_axis_checkboxes_round_trip_prefs(_app):
    from stimtest.gui.tracking_plot import TrackingPlot, AXIS_RIGHT
    tp = TrackingPlot()
    short = next(iter(tp._metric_right_chk))
    tp._metric_right_chk[short].setChecked(True)
    assert tp._metric_axes[short] == AXIS_RIGHT
    p = tp.current_prefs()
    tp2 = TrackingPlot()
    tp2.restore_prefs(p)
    assert tp2._metric_axes[short] == AXIS_RIGHT
    assert tp2._metric_right_chk[short].isChecked() is True
    assert tp2._metric_left_chk[short].isChecked() is False
    tp.deleteLater(); tp2.deleteLater()


def test_tracking_default_axes_reflected_on_checkboxes(_app):
    from stimtest.gui.tracking_plot import TrackingPlot, AXIS_LEFT, AXIS_RIGHT
    tp = TrackingPlot()
    for short, axis in tp._metric_axes.items():
        if axis in (AXIS_LEFT, AXIS_RIGHT):
            assert tp._metric_left_chk[short].isChecked() == (axis == AXIS_LEFT)
            assert tp._metric_right_chk[short].isChecked() == (axis == AXIS_RIGHT)
    tp.deleteLater()


def test_ps_tracking_still_fed(_app):
    """PS keeps its arrays (no trim) — tracking was always fed; guard it
    against a regression from the LP fix."""
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    ps = w.ps_tab
    tp = ps.tracking_plot
    for i in range(3):
        ps._on_capture(_snapshot(i, trim=False), "CH01")
    assert sum(len(v) for v in tp._series.values()) == 3 * len(tp._series)
    w.close()
