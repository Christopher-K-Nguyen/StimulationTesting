"""Metric-table N_pulse as a variable + Q_inj auto-scale to µC/cm².

Operator:
  * "N_pulse should be a variable, and fix the font color."
  * "If the charge injection capacity is 0 mC/cm2 in the subtitle due to low
    precision, then use uC/cm2."
"""
from __future__ import annotations

import sys

import numpy as np
import pytest


# ---- Q_inj auto-scale (pure) ---------------------------------------------
def test_qinj_use_micro_threshold():
    from stimtest.plotting import qinj_use_micro
    assert qinj_use_micro(0.0004) is True     # rounds to 0.000 mC → µC
    assert qinj_use_micro(0.0152) is False     # 0.015 mC displays fine
    assert qinj_use_micro(4.0) is False
    assert qinj_use_micro(0.0) is False        # exactly zero stays mC
    assert qinj_use_micro(float("nan")) is False


def test_subtitle_uses_microcoulomb_for_tiny_qinj():
    from stimtest.plotting import _capture_subtitle_mathtext
    from stimtest.session import Capture, ChannelRun, Configuration
    from stimtest.waveforms import PulsePattern
    cap = Capture(index=0,
                  pattern=PulsePattern.biphasic(amplitude_ua=-1.0, rate_hz=100.0))
    cap.metrics.charge_injection_mc_per_cm2 = 0.0004    # 0.4 µC/cm²
    run = ChannelRun(configuration=Configuration(id=0, active=1))
    txt = _capture_subtitle_mathtext(cap, run, "CH01")
    assert "µC/cm" in txt and "0.400" in txt
    assert "mC/cm" not in txt.split("inj")[1].split("·")[0]


# ---- N_pulse as a variable (metric table) --------------------------------
@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _capture_with_pulses():
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    from stimtest.metrics import compute_metrics
    cap = Capture(index=3,
                  pattern=PulsePattern.biphasic(amplitude_ua=-50.0, rate_hz=100.0))
    cap.time_us = np.linspace(-100.0, 600.0, 1400)
    cap.v_mon_v = np.zeros_like(cap.time_us)
    cap.i_mon_ua = np.zeros_like(cap.time_us)
    compute_metrics(cap, surface_area_um2=5000.0)
    cap.metrics.n_pulses = 128
    cap.metrics.cumulative_n_pulses = 256
    return cap


def test_metric_table_n_pulse_is_a_variable(_app):
    from stimtest.gui.widgets import MetricTable
    mt = MetricTable()
    mt.show_capture(_capture_with_pulses())
    labels = [mt.item(r, 0).text() for r in range(mt.rowCount())
              if mt.item(r, 0)]
    # N_pulse rendered as an italic variable + subscript (HTML), NOT plain text.
    assert "<i>N</i><sub>pulse</sub>" in labels
    assert "Cumulative <i>N</i><sub>pulse</sub>" in labels
    assert "N_pulse" not in labels                 # no plain-text version


def test_html_delegate_paints_theme_text_colour(_app):
    # The rich-text delegate must pull the text colour from the item's palette
    # (not QTextDocument's default black) so HTML labels match the plain rows
    # on a dark theme (operator: "fix the font color").  Assert the paint path
    # references option.palette for the Text role.
    import inspect
    from stimtest.gui.widgets import _HtmlItemDelegate
    src = inspect.getsource(_HtmlItemDelegate.paint)
    assert "option.palette.color" in src
    assert "ColorRole.Text" in src


# ---- HTML metric cells word-wrap (Cumulative N_pulse) ---------------------
def test_html_delegate_sizehint_wraps_to_column_width():
    """_HtmlItemDelegate.sizeHint must WRAP to the column width so a long HTML
    label (e.g. "Cumulative N_pulse") reports its 2-line height instead of a
    single line — else the 2nd line is clipped (operator: "The cumulative
    number of pulses is not wrapping … make sure that the tables allow for
    text wrapping")."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets, QtCore
    from stimtest.gui.widgets import _HtmlItemDelegate
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    table = QtWidgets.QTableWidget(1, 1)
    delg = _HtmlItemDelegate()
    item = QtWidgets.QTableWidgetItem("Cumulative <i>N</i><sub>pulse</sub>")
    table.setItem(0, 0, item)
    idx = table.model().index(0, 0)
    opt = QtWidgets.QStyleOptionViewItem()
    opt.font = table.font()
    opt.rect = QtCore.QRect(0, 0, 400, 20)          # WIDE → one line
    h_wide = delg.sizeHint(opt, idx).height()
    opt.rect = QtCore.QRect(0, 0, 64, 20)           # NARROW → must wrap
    h_narrow = delg.sizeHint(opt, idx).height()
    assert h_narrow > h_wide, (h_narrow, h_wide)
