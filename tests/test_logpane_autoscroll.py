"""LogPane tail-follow: auto-scroll to the latest line, but only while the
user is already at the bottom.

Operator: "automatically scrolled down to the latest line.  The user can
scroll up and down to look at other entries, but when the scrollbar has
reached the bottom, continue adjusting to the latest entries."
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets  # noqa: E402
from stimtest.gui.widgets import LogPane  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def _make_pane():
    pane = LogPane()
    pane.resize(240, 60)          # small viewport → real scrollbar range
    pane.show()
    QtWidgets.QApplication.processEvents()
    return pane


def _sb(pane):
    return pane.verticalScrollBar()


def _fill(pane, n, prefix="line"):
    for i in range(n):
        pane.log(f"{prefix} {i}")
    QtWidgets.QApplication.processEvents()


def test_follows_bottom_by_default(_app):
    pane = _make_pane()
    _fill(pane, 200)
    sb = _sb(pane)
    assert sb.maximum() > 0, "need a real scroll range for this test"
    # After a burst of lines with the view at the bottom, it stays pinned to
    # the newest line.
    assert sb.value() >= sb.maximum() - 4, (sb.value(), sb.maximum())
    pane.deleteLater()


def test_scrolled_up_is_not_yanked_down(_app):
    pane = _make_pane()
    _fill(pane, 200)
    sb = _sb(pane)
    # User scrolls UP to read history.
    parked = sb.maximum() // 2
    sb.setValue(parked)
    QtWidgets.QApplication.processEvents()
    assert sb.value() < sb.maximum() - 4       # genuinely not at bottom
    # New lines arrive — the view must NOT jump to the bottom.
    _fill(pane, 50, prefix="new")
    assert sb.value() < sb.maximum() - 4, (
        "log yanked the user down while scrolled up", sb.value(), sb.maximum())
    # The parked position is preserved (± the odd cap-eviction line).
    assert abs(sb.value() - parked) <= 2 * (sb.singleStep() or 14)
    pane.deleteLater()


def test_returning_to_bottom_resumes_follow(_app):
    pane = _make_pane()
    _fill(pane, 200)
    sb = _sb(pane)
    sb.setValue(sb.maximum() // 2)             # scroll up (pause follow)
    QtWidgets.QApplication.processEvents()
    _fill(pane, 10, prefix="paused")
    assert sb.value() < sb.maximum() - 4       # still paused
    # User scrolls back to the bottom → follow resumes.
    sb.setValue(sb.maximum())
    QtWidgets.QApplication.processEvents()
    _fill(pane, 30, prefix="resumed")
    assert sb.value() >= sb.maximum() - 4, (
        "follow did not resume after returning to bottom",
        sb.value(), sb.maximum())
    pane.deleteLater()


def test_empty_pane_follows_first_lines(_app):
    """A fresh (empty) pane must follow the very first lines (maximum()==0
    is treated as at-bottom)."""
    pane = _make_pane()
    _fill(pane, 5)
    sb = _sb(pane)
    assert sb.value() >= sb.maximum() - 4
    pane.deleteLater()


# ------------------------------------------------------- Go to latest line
# Operator: "I also want a 'Go to latest line' button on the log pane" — an
# overlay button in the pane's bottom-right corner, shown only while the
# view is parked OFF the bottom; clicking jumps to the newest line and
# resumes tail-follow.
def test_latest_line_button_hidden_while_following(_app):
    pane = _make_pane()
    _fill(pane, 200)
    assert pane._latest_btn.isVisible() is False    # at bottom → hidden


def test_latest_line_button_shows_when_scrolled_up_and_jumps(_app):
    pane = _make_pane()
    _fill(pane, 200)
    sb = _sb(pane)
    assert sb.maximum() > 0
    sb.setValue(sb.maximum() // 2)                  # scroll up
    QtWidgets.QApplication.processEvents()
    assert pane._latest_btn.isVisible() is True
    pane.go_to_latest_line()
    QtWidgets.QApplication.processEvents()
    assert sb.value() >= sb.maximum() - 4           # at the newest line
    assert pane._latest_btn.isVisible() is False    # hidden again
    # …and tail-follow resumed: new lines keep the view pinned.
    _fill(pane, 30, prefix="after")
    assert sb.value() >= sb.maximum() - 4
    pane.deleteLater()


# --------------------------------------------------------------- hanging indent
# Operator: "Can the log pane have hanging indentation in case the line exceeds
# the width?"  Each appended block gets leftMargin = indent, textIndent = -indent
# so the first visual line starts at the margin (under the timestamp) and every
# wrapped continuation line aligns under the message.
def _blocks(pane):
    doc = pane.document()
    out = []
    blk = doc.firstBlock()
    while blk.isValid():
        out.append(blk)
        blk = blk.next()
    return out


def test_hang_indent_computed_positive(_app):
    pane = LogPane()
    assert pane._hang_indent_px > 0
    pane.deleteLater()


def test_line_prefix_includes_wall_clock_date_and_time(_app):
    """Operator: "include the date and time".  Every line's prefix carries the
    wall-clock DATE + time of day AND the run-relative elapsed:
    ``[YYYY-MM-DD HH:MM:SS +H:MM:SS] message``."""
    import re
    pane = LogPane()
    pane.log("hello world")
    QtWidgets.QApplication.processEvents()
    text = pane.document().lastBlock().text()
    assert re.match(
        r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \+\d+:\d{2}:\d{2}\] "
        r"hello world$", text), text
    pane.deleteLater()


def test_every_block_gets_hanging_indent(_app):
    pane = _make_pane()
    pane.log("a short line")
    pane.log("a rather longer line that would wrap in a narrow log pane and "
             "should hang-indent its wrapped continuation portion")
    QtWidgets.QApplication.processEvents()
    indent = pane._hang_indent_px
    seen = 0
    for blk in _blocks(pane):
        if not blk.text().strip():
            continue
        bf = blk.blockFormat()
        assert abs(bf.leftMargin() - indent) < 0.5, blk.text()
        assert abs(bf.textIndent() + indent) < 0.5, blk.text()
        seen += 1
    assert seen >= 2
    pane.deleteLater()


def test_fifo_cap_enforced_manually(_app):
    """QTextEdit has no setMaximumBlockCount, so the 1000-line cap is
    enforced by trimming leading blocks — evict oldest, keep newest, and
    the surviving blocks keep their hanging indent."""
    pane = LogPane()
    for i in range(pane._max_blocks + 300):
        pane.log(f"line {i}")
    doc = pane.document()
    assert doc.blockCount() == pane._max_blocks
    assert f"line {pane._max_blocks + 299}" in doc.lastBlock().text()
    assert "line 0" not in doc.firstBlock().text()  # oldest evicted
    bf = doc.lastBlock().blockFormat()
    assert bf.leftMargin() > 0 and bf.textIndent() < 0
    pane.deleteLater()


def test_multiline_tabbed_summary_blocks_all_indented(_app):
    """A tabbed multi-line pattern summary (one block per '\\n') gets the
    hanging indent on EVERY line, including the tabbed continuation lines."""
    pane = _make_pane()
    pane.log("pattern =\n"
             "\tPhase 1: +50.0 uA x 200 us, rectangular\n"
             "\tInterphase delay: 100 us\n"
             "\tPhase 2: -50.0 uA x 200 us, rectangular\n"
             "\tRate: 50 pps")
    QtWidgets.QApplication.processEvents()
    indent = pane._hang_indent_px
    tabbed = [b for b in _blocks(pane) if b.text().startswith("\t")]
    assert len(tabbed) >= 3
    for blk in tabbed:
        bf = blk.blockFormat()
        assert abs(bf.leftMargin() - indent) < 0.5, blk.text()
        assert abs(bf.textIndent() + indent) < 0.5, blk.text()
    pane.deleteLater()
