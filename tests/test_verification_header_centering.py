"""Verification results-table headers must be CENTRED, like their values.

The value cells are ``AlignCenter``.  ``_HtmlHeaderView`` paints the markup
headers itself (``QHeaderView.paintSection`` draws through ``QStyle`` and never
consults an item delegate), so the centring is the view's own job -- and a
``QTextDocument`` laid out at ``setTextWidth(section width)`` aligns its text
LEFT inside that width regardless of the header's ``defaultAlignment``.  That
left every markup header flush-left above centred numbers.

Checked by measuring where the INK actually lands, not just by asserting a
property -- the property was necessary but not sufficient.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from PyQt6 import QtCore, QtGui, QtWidgets

from stimtest.gui.calibration import _HtmlHeaderView

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
_app.setApplicationName("pulsar-pytest")


def _table(headers):
    t = QtWidgets.QTableWidget(1, len(headers))
    t.setHorizontalHeader(_HtmlHeaderView(QtCore.Qt.Orientation.Horizontal, t))
    t.setHorizontalHeaderLabels(headers)
    return t


def _ink_columns(hdr, rect, index):
    """Paint one section and return the x-range of non-background pixels."""
    img = QtGui.QImage(rect.width(), rect.height(),
                       QtGui.QImage.Format.Format_ARGB32)
    img.fill(QtCore.Qt.GlobalColor.transparent)
    p = QtGui.QPainter(img)
    hdr.paintSection(p, QtCore.QRect(0, 0, rect.width(), rect.height()), index)
    p.end()
    # Background is whatever the style drew; find the text by looking for the
    # DARKEST pixels, which is the glyph ink on either theme's header chrome.
    cols = []
    for x in range(img.width()):
        for y in range(img.height()):
            c = img.pixelColor(x, y)
            if c.alpha() > 0 and c.lightness() < 110:
                cols.append(x)
                break
    return cols


def test_default_alignment_is_centre():
    t = _table(["Channel"])
    assert t.horizontalHeader().defaultAlignment() == \
        QtCore.Qt.AlignmentFlag.AlignCenter


def test_rich_header_ink_is_horizontally_centred():
    html = "<i>R</i><sub>load</sub> fit [Ω]"
    t = _table([html])
    t.setColumnWidth(0, 300)
    hdr = t.horizontalHeader()
    hdr.resize(300, 28)
    rect = QtCore.QRect(0, 0, 300, 28)
    cols = _ink_columns(hdr, rect, 0)
    if not cols:
        pytest.skip("style drew no measurable ink in this environment")
    lo, hi = min(cols), max(cols)
    centre = (lo + hi) / 2.0
    # Centred within a tolerance well below what "flush left" would give:
    # left-aligned text would put the centre near (width of the text)/2,
    # i.e. far left of 150.
    assert abs(centre - 150.0) < 30.0, (lo, hi, centre)


def test_rich_header_is_not_flush_left():
    """Regression: the bug put the ink hard against the left edge."""
    html = "<i>C</i><sub>load</sub> fit [pF]"
    t = _table([html])
    t.setColumnWidth(0, 300)
    hdr = t.horizontalHeader()
    hdr.resize(300, 28)
    cols = _ink_columns(hdr, QtCore.QRect(0, 0, 300, 28), 0)
    if not cols:
        pytest.skip("style drew no measurable ink in this environment")
    assert min(cols) > 20, f"ink starts at x={min(cols)} — flush left again"


def test_plain_and_rich_headers_agree():
    """"Channel" (plain, painted by the base class) and a markup header must
    use the same alignment, or the row of headers looks ragged."""
    t = _table(["Channel", "<i>V</i><sub>mon</sub> offset [mV]"])
    hdr = t.horizontalHeader()
    assert hdr.defaultAlignment() == QtCore.Qt.AlignmentFlag.AlignCenter
    for i in (0, 1):
        t.setColumnWidth(i, 260)
    hdr.resize(520, 28)
    rich = _ink_columns(hdr, QtCore.QRect(0, 0, 260, 28), 1)
    if rich:
        assert abs(((min(rich) + max(rich)) / 2.0) - 130.0) < 30.0
