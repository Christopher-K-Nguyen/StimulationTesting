"""Tests for the channel-selector zoom feature.

Covers the API contract of :class:`stimtest.gui.channel_selector._GridCanvas`
zoom logic without needing an event-loop:

1. Default zoom is 1.0.
2. ``set_zoom`` clamps to ``[ZOOM_MIN, ZOOM_MAX]``.
3. ``zoom_in`` / ``zoom_out`` step multiplicatively by ``ZOOM_STEP``.
4. ``zoom_in`` past ZOOM_MAX clamps + emits ``zoomChanged`` only on
   actual change.
5. ``reset_zoom`` snaps to 1.0.
6. ``sizeHint`` scales linearly with zoom.
7. ``_layout_metrics`` shrinks the cell when zoom < 1.0 (zoom-out
   path) and leaves cell at the auto-fit value when zoom >= 1.0
   (zoom-in path is handled by sizeHint growing the widget).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    """Single QApplication for the module — needed because the
    canvas is a QWidget; instantiating one without an app raises.
    """
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    yield app


@pytest.fixture
def canvas(qapp):
    """A bare _GridCanvas on a 4×4 array. Used by every test."""
    from stimtest.electrode import ElectrodeArray
    from stimtest.gui.channel_selector import _GridCanvas
    arr = ElectrodeArray.utah_4x4()
    c = _GridCanvas(arr, layout="rect")
    return c


def test_default_zoom_is_one(canvas):
    """A freshly-constructed canvas reports zoom == 1.0."""
    assert canvas.zoom() == pytest.approx(1.0)


def test_set_zoom_clamps_to_max(canvas):
    """A zoom factor above ZOOM_MAX is clamped."""
    canvas.set_zoom(100.0)
    assert canvas.zoom() == pytest.approx(canvas.ZOOM_MAX)


def test_set_zoom_clamps_to_min(canvas):
    """A zoom factor below ZOOM_MIN is clamped."""
    canvas.set_zoom(0.001)
    assert canvas.zoom() == pytest.approx(canvas.ZOOM_MIN)


def test_zoom_in_scales_by_step(canvas):
    """``zoom_in`` multiplies the current zoom by ``ZOOM_STEP``."""
    canvas.set_zoom(1.0)
    canvas.zoom_in()
    assert canvas.zoom() == pytest.approx(1.0 * canvas.ZOOM_STEP)
    canvas.zoom_in()
    assert canvas.zoom() == pytest.approx(1.0 * canvas.ZOOM_STEP ** 2)


def test_zoom_out_scales_by_inverse_step(canvas):
    """``zoom_out`` divides the current zoom by ``ZOOM_STEP``."""
    canvas.set_zoom(1.0)
    canvas.zoom_out()
    assert canvas.zoom() == pytest.approx(1.0 / canvas.ZOOM_STEP)


def test_reset_zoom_returns_to_one(canvas):
    """``reset_zoom`` snaps to 1.0 from any starting value."""
    canvas.set_zoom(3.0)
    assert canvas.zoom() != pytest.approx(1.0)
    canvas.reset_zoom()
    assert canvas.zoom() == pytest.approx(1.0)


def test_zoom_changed_signal_fires_only_on_change(canvas):
    """``zoomChanged`` emits when the zoom factor changes; calling
    ``set_zoom`` with the current value is a no-op (no emit)."""
    received = []
    canvas.zoomChanged.connect(received.append)
    canvas.set_zoom(1.5)
    assert received == [pytest.approx(1.5)]
    canvas.set_zoom(1.5)  # same value — no-op
    assert received == [pytest.approx(1.5)]
    canvas.set_zoom(2.0)
    assert received == [pytest.approx(1.5), pytest.approx(2.0)]


def test_zoom_changed_does_not_emit_when_clamped_at_max(canvas):
    """Stepping zoom-in past ZOOM_MAX clamps to the cap, but if
    we're already AT the cap, the signal must NOT re-emit."""
    canvas.set_zoom(canvas.ZOOM_MAX)
    received = []
    canvas.zoomChanged.connect(received.append)
    canvas.zoom_in()  # already at max
    assert received == []


def test_size_hint_scales_with_zoom(canvas):
    """``sizeHint`` scales linearly with zoom factor — within
    ~1 px for integer rounding."""
    canvas.set_zoom(1.0)
    h1 = canvas.sizeHint()
    canvas.set_zoom(2.0)
    h2 = canvas.sizeHint()
    # Width and height should both roughly double (with a small
    # additive offset from the constant 2*PAD margin).
    pad = 2 * canvas.PAD
    assert (h2.width() - pad) == pytest.approx(2 * (h1.width() - pad), rel=0.02)
    assert (h2.height() - pad) == pytest.approx(2 * (h1.height() - pad), rel=0.02)


def test_size_hint_uses_reference_cell(canvas):
    """At zoom = 1.0, the sizeHint matches the
    grid-footprint × _ZOOM_REFERENCE_CELL math."""
    canvas.set_zoom(1.0)
    hint = canvas.sizeHint()
    # 4×4 rect array → x_footprint = 4, y_footprint = 4.
    expected_w = int(canvas._ZOOM_REFERENCE_CELL * 4 + 2 * canvas.PAD)
    expected_h = int(canvas._ZOOM_REFERENCE_CELL * 4 + 2 * canvas.PAD)
    assert hint.width() == expected_w
    assert hint.height() == expected_h


def test_layout_metrics_shrinks_cell_when_zoom_below_one(canvas):
    """At zoom < 1.0, ``_layout_metrics`` produces a smaller cell
    than the full auto-fit value, leaving whitespace around the
    grid (instead of growing the widget)."""
    canvas.resize(400, 400)
    canvas.set_zoom(1.0)
    cell_full, _, _ = canvas._layout_metrics()
    canvas.set_zoom(0.5)
    cell_half, _, _ = canvas._layout_metrics()
    # Cell at zoom=0.5 should be about half of the cell at zoom=1.0
    # (allowing for integer rounding + the 8-px floor). On a 400×400
    # widget with a 4×4 footprint, auto_cell ≈ 95, so 0.5× ≈ 47.
    assert cell_half < cell_full
    assert cell_half == pytest.approx(cell_full * 0.5, abs=2)


def test_layout_metrics_uses_auto_fit_when_zoom_above_one(canvas):
    """At zoom >= 1.0, ``_layout_metrics`` doesn't apply the
    multiplier — the QScrollArea is responsible for growing the
    widget instead. So the cell size matches what auto-fit alone
    would produce at the current widget dimensions."""
    canvas.resize(400, 400)
    canvas.set_zoom(1.0)
    cell_one, _, _ = canvas._layout_metrics()
    canvas.set_zoom(2.0)
    # Widget size unchanged in this test (no parent QScrollArea
    # to grow it), so _layout_metrics should report the same cell
    # as at zoom 1.0 — the zoom-in effect comes from the parent
    # widget growing via sizeHint, not from a per-cell multiplier.
    cell_two, _, _ = canvas._layout_metrics()
    assert cell_two == cell_one


def test_layout_metrics_floor_keeps_cell_above_8(canvas):
    """Even at minimum zoom on a tiny widget, the cell is clamped
    at 8 px so the disk + label remain renderable."""
    canvas.resize(80, 80)
    canvas.set_zoom(canvas.ZOOM_MIN)
    cell, _, _ = canvas._layout_metrics()
    assert cell >= 8


def test_zoom_set_twice_idempotent(canvas):
    """Setting the same zoom twice doesn't raise + doesn't
    re-emit ``zoomChanged`` (already covered by the signal test
    above; this is an explicit re-statement at the API level)."""
    canvas.set_zoom(1.5)
    canvas.set_zoom(1.5)
    assert canvas.zoom() == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Reset-view button tests (outer ChannelSelector container)
# ---------------------------------------------------------------------------

@pytest.fixture
def selector(qapp):
    """A full ChannelSelector with its QScrollArea + toolbar.
    Used by the reset-view tests."""
    from stimtest.electrode import ElectrodeArray
    from stimtest.gui.channel_selector import ChannelSelector
    arr = ElectrodeArray.utah_4x4()
    return ChannelSelector(arr, layout="rect")


def test_reset_view_resets_zoom_to_one(selector):
    """``Reset view`` button restores zoom = 1.0 from any
    starting value."""
    selector.canvas.set_zoom(2.5)
    assert selector.canvas.zoom() != pytest.approx(1.0)
    selector._reset_view()
    assert selector.canvas.zoom() == pytest.approx(1.0)


def test_reset_view_resets_scroll_position(selector):
    """``Reset view`` parks both scrollbars at zero, even when
    the user had scrolled far into a zoomed grid."""
    # Force a scroll position by zooming in (so the canvas is
    # bigger than the viewport) and writing a non-zero value
    # directly on the scrollbars.
    selector.canvas.set_zoom(3.0)
    selector.scroll.horizontalScrollBar().setValue(50)
    selector.scroll.verticalScrollBar().setValue(50)
    # Reset.
    selector._reset_view()
    assert selector.scroll.horizontalScrollBar().value() == 0
    assert selector.scroll.verticalScrollBar().value() == 0


def test_reset_view_button_is_labeled_reset_view(selector):
    """Confirms the user-visible button text changed from
    ``"1:1"`` to the more descriptive ``"Reset view"``."""
    assert selector.btn_zoom_reset.text() == "Reset view"


def test_reset_view_works_when_already_at_zoom_one(selector):
    """Calling reset at zoom = 1.0 is a no-op for the zoom side
    but the scroll bars must still snap to (0, 0)."""
    selector.canvas.set_zoom(1.0)
    selector.scroll.horizontalScrollBar().setValue(20)
    selector.scroll.verticalScrollBar().setValue(20)
    selector._reset_view()
    assert selector.canvas.zoom() == pytest.approx(1.0)
    assert selector.scroll.horizontalScrollBar().value() == 0
    assert selector.scroll.verticalScrollBar().value() == 0


def test_default_canvas_zoom_is_auto_fit(selector):
    """A fresh selector starts at zoom=1.0 (the auto-fit
    "fill the device" default the user expects)."""
    assert selector.canvas.zoom() == pytest.approx(1.0)
    assert selector.zoom_label.text() == "100%"
