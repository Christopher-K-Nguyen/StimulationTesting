"""SPACE thousands grouping (copy-safe) + centered, variable-formatted E_pol
x-line guide labels.

Operator: "separate thousands with a space instead of a comma", "do not let the
formatting of separators affect the value copied from tables for pasting", and
"Center the x line labels to the center with proper variable formatting".
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


# ---------------------------------------------------------------------------
# thousands grouping + copy-safety
# ---------------------------------------------------------------------------
def test_group_thousands_uses_space(qapp):
    from stimtest.gui.widgets import _group_thousands as g
    assert g(10486) == "10 486"
    assert g(1234567) == "1 234 567"
    assert g(-20486) == "-20 486"
    assert g(500) == "500"
    assert "," not in g(1234567)


def test_strip_group_sep_only_removes_thousands_space(qapp):
    from stimtest.gui.widgets import _strip_group_sep as s
    assert s("10 486") == "10486"            # thousands space dropped
    assert s("1 234 567") == "1234567"
    assert s("5.000 ms") == "5.000 ms"       # unit space PRESERVED
    assert s("-0.2 V") == "-0.2 V"
    assert s("1.05, 1.05, 1.1") == "1.05, 1.05, 1.1"


def test_grouped_value_round_trips_after_strip(qapp):
    from stimtest.gui.widgets import _group_thousands as g, _strip_group_sep as s
    assert float(s(g(10486))) == 10486.0
    assert float(s(g(1234567))) == 1234567.0


# ---------------------------------------------------------------------------
# time zone in the log pane + update banners
# ---------------------------------------------------------------------------
def test_wall_stamp_includes_time_zone(qapp):
    import re
    from stimtest.gui.widgets import _wall_stamp, _tz_abbrev
    s = _wall_stamp()
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", s)   # date time …
    assert s.endswith(_tz_abbrev())                          # … TZ
    assert _tz_abbrev()                                       # non-empty


def test_log_line_includes_time_zone(qapp):
    from stimtest.gui.widgets import LogPane, _tz_abbrev
    lp = LogPane(); lp.reset_clock(banner=False)
    lp.log("hello world")
    line = lp.toPlainText().strip()
    assert _tz_abbrev() in line
    assert line.startswith("[") and "hello world" in line


# ---------------------------------------------------------------------------
# E_pol guide-line labels — centered + variable-formatted
# ---------------------------------------------------------------------------
def test_epol_guide_label_html_variable_format(qapp):
    from stimtest.gui.widgets import _epol_guide_label_html as h
    out = h("Emc", "#7A0AF0")
    assert "<i>E</i>" in out and "<sub>mc</sub>" in out
    assert "#7A0AF0" in out
    assert h("Ema", "#000")  # anodal
    assert h("Emc1", "#000")  # numeric suffix


def test_epol_guides_centered_and_rendered(qapp):
    import pyqtgraph as pg
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot(); sp.resize(1000, 400); sp.show(); qapp.processEvents()
    sp._plot.setXRange(-100, 700, padding=0)
    sp._plot.setYRange(-1, 1, padding=0); qapp.processEvents()
    vb = sp._plot.getViewBox()
    xaxis_y = vb.mapRectToScene(vb.boundingRect()).bottom()
    sp.set_epol_guides([("Emc", 350.0), ("Ema", 560.0)]); qapp.processEvents()
    items = sp._epol_guide_items
    assert len(items) == 2
    for ln in items:
        ti = ln.label.textItem
        # HTML rendered (tags stripped in the plain text, subscript present)
        assert ti.toPlainText() in ("Emc", "Ema")
        assert "vertical-align" in ti.toHtml().lower()
        br = ti.mapToScene(ti.boundingRect()).boundingRect()
        lx = ln.getViewBox().mapViewToScene(pg.Point(ln.value(), 0)).x()
        # label horizontally CENTERED on the line (within a few px)
        assert abs(br.center().x() - lx) < 3.0
        # OPAQUE background so the dashed line can't show through the text
        assert ln.label.fill.color().alpha() == 255
        # OFFSET above the x-axis (a gap), not overlapping it
        assert br.bottom() < xaxis_y - 2.0
