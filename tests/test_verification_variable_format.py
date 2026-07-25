"""Verification results table uses VARIABLE FORMAT in its headers.

Operator, twice: "have R_load and C and other variables in proper variable
format" / "I have told you to use variable format".

``QHeaderView`` paints through ``QStyle::CE_Header`` and NEVER consults the
view's item delegate, so the ``_HtmlItemDelegate`` that formats CELLS cannot
format HEADERS -- a painting subclass (``_HtmlHeaderView``) is the only way to
get ``<i>R</i><sub>load</sub>`` into a table header.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

import pytest

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _headers(tab):
    m = tab.results_table.model()
    from PyQt6 import QtCore
    return [str(m.headerData(i, QtCore.Qt.Orientation.Horizontal,
                             QtCore.Qt.ItemDataRole.DisplayRole) or "")
            for i in range(tab.results_table.columnCount())]


def _tab(_app):
    from stimtest.gui.calibration import CalibrationTab
    return CalibrationTab(stim=None, scope=None)


def test_headers_use_italic_variables_with_subscripts(_app):
    tab = _tab(_app)
    try:
        hdrs = _headers(tab)
        joined = " | ".join(hdrs)
        for var, sub in (("V", "mon"), ("R", "load"),
                         ("C", "load"), ("I", "mon")):
            assert f"<i>{var}</i><sub>{sub}</sub>" in joined, (var, sub, joined)
        # The fit coefficients are variables too.
        assert "<i>a</i>" in joined and "<i>b</i>" in joined
    finally:
        tab.deleteLater()


def test_units_are_bracketed_not_parenthesised(_app):
    tab = _tab(_app)
    try:
        joined = " | ".join(_headers(tab))
        for unit in ("[mV]", "[Ω]", "[pF]", "[µA]"):
            assert unit in joined, unit
        # The old plain-text forms are gone.
        assert "R_load Fit" not in joined
        assert "V_mon Offset" not in joined
    finally:
        tab.deleteLater()


def test_plain_channel_header_untouched(_app):
    """A header with no markup keeps stock styling (base paintSection)."""
    tab = _tab(_app)
    try:
        assert _headers(tab)[0] == "Channel"
    finally:
        tab.deleteLater()


def test_plot_legend_and_axes_use_variable_format():
    """Operator: "the legend is not in variable format".

    pyqtgraph's LegendItem renders its label through a LabelItem, which
    accepts HTML -- so the same markup the table headers use works here.
    """
    import pathlib
    import stimtest.gui.calibration as c
    src = pathlib.Path(c.__file__).read_text(encoding="utf-8")
    # Legend entries: V_mon trace, I_mon trace, and the RC-model overlay.
    assert "rich.var('V', 'mon')} ({v_mon_phys})" in src
    assert "rich.var('I', 'mon')} ({i_mon_phys})" in src
    assert "RC model ({rich.var('V', 'mon')})" in src
    # Axis labels carry the same variables.
    assert 'setLabel("left", rich.var("V", "mon")' in src
    assert 'setLabel(rich.var("I", "mon")' in src
    # The old plain-text forms are gone from the legend/axis calls.
    assert 'name=f"V_mon (' not in src
    assert '"RC model (V_mon)"' not in src


def test_header_view_is_the_html_painter(_app):
    from stimtest.gui.calibration import _HtmlHeaderView
    tab = _tab(_app)
    try:
        assert isinstance(tab.results_table.horizontalHeader(), _HtmlHeaderView)
    finally:
        tab.deleteLater()


def test_paint_section_survives_a_bad_model(_app):
    """Painting must never raise into the GUI thread."""
    from PyQt6 import QtCore, QtGui
    from stimtest.gui.calibration import _HtmlHeaderView
    tab = _tab(_app)
    try:
        hv = tab.results_table.horizontalHeader()
        pm = QtGui.QPixmap(120, 24)
        painter = QtGui.QPainter(pm)
        try:
            # Real section, then an out-of-range one.
            hv.paintSection(painter, QtCore.QRect(0, 0, 120, 24), 2)
            hv.paintSection(painter, QtCore.QRect(0, 0, 120, 24), 99)
        finally:
            painter.end()
    finally:
        tab.deleteLater()
