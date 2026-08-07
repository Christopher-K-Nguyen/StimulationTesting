"""Experiment-plot viewing controls + channel/combo follow.

Two operator requests:

1. "Add the same viewing options in the experiment plot like the test
   parameters, e.g., x zoom, y zoom, height, and reset" — ScopePlot gets
   X/Y zoom, height +/-, and a Reset view button.
2. "If a different channel/combo was selected from the latest, do not
   automatically go to the new latest channel/combo when completed.
   Include a latest button … (and move to the latest until a different
   channel/combo is selected)" — MultiChannelScope follows the live
   channel/combo until the user pins a different entry; a Latest button
   re-arms the follow.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest
from stimtest.gui.widgets import metric_row_text as _mrt

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _cap(idx: int, amp: float = 50.0):
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=amp)
    c = Capture(index=idx, pattern=p)
    t = np.linspace(-100.0, 500.0, 1200)
    c.time_us = t
    c.v_mon_v = np.where((t >= 0) & (t < 200), -0.2, 0.0)
    c.i_mon_ua = np.where((t >= 0) & (t < 200), -amp, 0.0)
    return c


# --------------------------------------------------------------- ScopePlot
def test_scopeplot_x_zoom_shrinks_then_reset_restores(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    sp.set_traces(np.linspace(-100.0, 500.0, 1200),
                  {"Voltage": _cap(0).v_mon_v},
                  {"Voltage": "#E6B800"}, {"Voltage": "left"})
    vb = sp._plot.plotItem.vb

    def _xw():
        lo, hi = vb.viewRange()[0]
        return float(hi) - float(lo)

    w0 = _xw()
    sp._zoom_axis('x', 0.7)
    w1 = _xw()
    assert w1 < w0, "X+ must shrink the visible time window"
    sp._zoom_axis('x', 1.4)
    w2 = _xw()
    assert w2 > w1, "X- must widen it again"
    # Reset restores the cached pulse-framed default window.
    assert sp._default_xrange is not None
    sp.reset_view()
    lo, hi = vb.viewRange()[0]
    assert abs(lo - sp._default_xrange[0]) < 1.0
    assert abs(hi - sp._default_xrange[1]) < 1.0


def test_scopeplot_y_zoom_scales_left_axis(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    sp.set_traces(np.linspace(-100.0, 500.0, 1200),
                  {"Voltage": _cap(0).v_mon_v},
                  {"Voltage": "#E6B800"}, {"Voltage": "left"})
    vb = sp._plot.plotItem.vb

    def _yw():
        lo, hi = vb.viewRange()[1]
        return float(hi) - float(lo)

    h0 = _yw()
    sp._zoom_axis('y', 0.7)
    h1 = _yw()
    assert h1 < h0, "Y+ must shrink the voltage window"


def test_scopeplot_height_buttons_clamp(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    sp._change_height(80)
    assert sp.minimumHeight() >= sp._MIN_PLOT_H
    # Shrinking many times floors at _MIN_PLOT_H, never below.
    for _ in range(50):
        sp._change_height(-40)
    assert sp.minimumHeight() == sp._MIN_PLOT_H


def test_scopeplot_has_view_control_buttons(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    for attr in ("x_in_btn", "x_out_btn", "y_in_btn", "y_out_btn",
                 "tall_btn", "short_btn", "reset_view_btn"):
        assert hasattr(sp, attr), f"ScopePlot missing {attr}"


def test_inset_right_axis_symmetric_ticks_no_numbers(qapp):
    """Operator (clarified): "hide the ticks for a right axis — make
    symmetric ticks with the left axis."  MATLAB-box style: the inset's
    right axis shows a visible spine + tick MARKS at the SAME positions as
    the left axis, with the tick NUMBERS hidden — and keeps the reserved
    width so the inset still lines up with the main plot."""
    from PyQt6.QtCore import Qt
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    if getattr(sp, "_inset", None) is None:
        pytest.skip("build lacks the inset")
    sp.resize(700, 500); sp.show(); qapp.processEvents()
    t = np.linspace(-64.0, 576.0, 200)
    sp.set_traces(t, {"V_mon": 0.2 * np.sin(t / 40.0)},
                  axis={"V_mon": "left"})
    sp.set_inset_visible(True)
    sp.set_inset_traces(["V_mon"])
    qapp.processEvents()
    il = sp._inset.getAxis("left")
    ir = sp._inset.getAxis("right")
    main_r = sp._plot.getAxis("right")
    # Symmetric: right tick positions == left tick positions (the right
    # axis's tickValues is DELEGATED to the left's at construction).
    lo, hi = sp._inset.plotItem.vb.viewRange()[1]
    lt = [tuple(np.round(v[1], 9)) for v in il.tickValues(lo, hi, 200)]
    rt = [tuple(np.round(v[1], 9)) for v in ir.tickValues(lo, hi, 200)]
    assert lt == rt, (lt, rt)
    # Visible spine + marks (NOT NoPen), numbers hidden, tick length mirrored.
    # The mirror RESERVES text width (showValues=True) so the plot columns
    # align, but paints EMPTY tick strings → no visible numbers.
    assert ir.pen().style() != Qt.PenStyle.NoPen
    assert all(s == "" for s in ir.tickStrings([-0.1, 0.0, 0.1], 1.0, 0.1))
    assert ir.style["tickLength"] == il.style["tickLength"]
    # Width still reserved to match the main plot's right axis.
    assert abs(ir.width() - main_r.width()) < 0.5, (
        ir.width(), main_r.width())
    # And the LEFT axis KEEPS its numbering (operator: "keep the numbering
    # on the left axis of the inset").
    assert il.style["showValues"] is True


def test_inset_bottom_ticks_match_main_plot(qapp):
    """Operator: "the x axis ticks and numbering for the inset should match
    the figure above" — the inset bottom axis DELEGATES tickValues to the
    main plot's bottom axis (which carries the span-aware MATLAB override),
    so the two can never diverge."""
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    if getattr(sp, "_inset", None) is None:
        pytest.skip("build lacks the inset")
    sp.resize(760, 620); sp.show(); qapp.processEvents()
    t = np.linspace(-64.0, 576.0, 400)
    sp.set_traces(t, {"V_mon": 0.2 * np.sin(t / 40.0)},
                  axis={"V_mon": "left"})
    sp.set_inset_visible(True)
    sp.set_inset_traces(["V_mon"])
    qapp.processEvents()
    mb = sp._plot.getAxis("bottom")
    ib = sp._inset.getAxis("bottom")
    (x0, x1), _ = sp._plot.getViewBox().viewRange()
    mt = [tuple(np.round(v[1], 6)) for v in mb.tickValues(x0, x1, 500)]
    it = [tuple(np.round(v[1], 6)) for v in ib.tickValues(x0, x1, 500)]
    assert mt == it, (mt, it)
    # Inset bottom numbering stays ON (it matches the figure above).
    assert ib.style["showValues"] is True


def test_plot_and_inset_live_in_a_stretch_splitter(qapp):
    """Operator: "I allow to stretch between the experiment plot and inset
    to change their heights" — the two blocks are children of a vertical
    QSplitter and both get real space when the inset shows."""
    from PyQt6 import QtWidgets as QtW
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    if getattr(sp, "_inset", None) is None:
        pytest.skip("build lacks the inset")
    sp.resize(760, 620); sp.show(); qapp.processEvents()
    split = sp._plot_inset_split
    assert isinstance(split, QtW.QSplitter)
    assert split.count() == 2
    t = np.linspace(-64.0, 576.0, 200)
    sp.set_traces(t, {"V_mon": 0.2 * np.sin(t / 40.0)},
                  axis={"V_mon": "left"})
    sp.set_inset_visible(True)
    sp.set_inset_traces(["V_mon"])
    qapp.processEvents()
    sizes = split.sizes()
    assert all(s > 0 for s in sizes), sizes
    # The operator can drag: setSizes redistributes.
    total = sum(sizes)
    split.setSizes([total // 2, total - total // 2])
    qapp.processEvents()
    s2 = split.sizes()
    assert abs(s2[0] - s2[1]) <= max(2, total // 10), s2


def test_inset_not_shrunken_after_premature_toggle(qapp):
    """Operator: "Something happened to the inset to be small."  A
    premature ``set_inset_visible(True)`` (before the panel has any real
    geometry) reports a ~0 splitter total — seeding 30 % of that would
    lock a sliver-sized inset that never recovers.  The seed now DEFERS
    (and only then locks) until the splitter has a usable height, and
    ``showEvent`` retries once the panel is on screen — so the inset
    lands at a healthy fraction, not a sliver."""
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    if getattr(sp, "_inset", None) is None:
        pytest.skip("build lacks the inset")
    # Toggle BEFORE show/layout (the premature path).
    sp.set_inset_visible(True)
    sp.set_inset_traces(["V_mon"])
    assert getattr(sp, "_inset_split_seeded", False) is False, \
        "must not lock a seed while the splitter has no real height"
    # Now show at a real size — showEvent + set_inset_visible retry the seed.
    sp.resize(1000, 700); sp.show()
    for _ in range(5):
        qapp.processEvents()
    sizes = sp._plot_inset_split.sizes()
    total = sum(sizes)
    assert total > 0
    inset_h = sizes[1]
    # Healthy inset: at least its minimum, and a real fraction of the
    # height (not a locked sliver, not the whole panel).
    assert inset_h >= sp._inset.minimumHeight(), (sizes, sp._inset.minimumHeight())
    assert 0.2 <= inset_h / total <= 0.45, (inset_h, total, inset_h / total)


class _FakeWheel:
    """Stand-in for a QGraphicsSceneWheelEvent — just enough for the
    ViewBox.wheelEvent override to call ``ignore()``."""
    def __init__(self):
        self.ignored = False

    def ignore(self):
        self.ignored = True

    def accept(self):
        pass


def test_mouse_wheel_does_not_zoom_the_experiment_plot(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    sp.set_traces(np.linspace(-100.0, 500.0, 1200),
                  {"Voltage": _cap(0).v_mon_v},
                  {"Voltage": "#E6B800"}, {"Voltage": "left"})
    vb = sp._plot.getViewBox()
    before = [tuple(vb.viewRange()[0]), tuple(vb.viewRange()[1])]
    ev = _FakeWheel()
    vb.wheelEvent(ev)                       # what the scene would dispatch
    after = [tuple(vb.viewRange()[0]), tuple(vb.viewRange()[1])]
    assert ev.ignored, "wheel event must be ignored, not zoomed"
    assert after == before, "mouse wheel must NOT change the view range"


def test_disable_plot_wheel_zoom_helper(qapp):
    import pyqtgraph as pg
    from stimtest.gui.widgets import disable_plot_wheel_zoom
    pw = pg.PlotWidget()
    disable_plot_wheel_zoom(pw)
    ev = _FakeWheel()
    pw.getViewBox().wheelEvent(ev)
    assert ev.ignored
    # No-op safety on None / non-plot input.
    disable_plot_wheel_zoom(None)


# ------------------------------------------------------- channel/combo follow
def test_follows_latest_until_user_pins(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0), "CH01")
    assert mcs._entry_auto_follow is True
    assert mcs._latest_key == "CH01"
    assert mcs.entry_list.currentRow() == 0

    # A new channel's capture auto-follows.
    mcs.add_capture(_cap(0), "CH02")
    assert mcs.entry_list.currentRow() == 1     # moved to CH02

    # User pins CH01 → follow OFF, Latest button enabled.
    mcs.entry_list.setCurrentRow(0)
    assert mcs._entry_auto_follow is False
    assert mcs._entry_latest_btn.isEnabled() is True

    # A capture on a NEW channel must NOT move the pinned view.
    mcs.add_capture(_cap(0), "CH03")
    assert mcs.entry_list.currentRow() == 0     # still on CH01
    assert mcs._latest_key == "CH03"


# ------------------------------------- inset control mounted on the active page
# (operator: "move the inset option in [reset view's former] place")
def test_inset_control_detached_before_any_capture(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    # Placeholder shown → inset control detached (parented to mcs) + hidden.
    assert mcs._inset_controls.parentWidget() is mcs
    assert mcs._inset_controls.isHidden()


def test_inset_control_mounts_on_active_page(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0), "CH01")
    page = mcs.ensure_page("CH01")
    # Mounted into the active page's ScopePlot control row (next to Reset view).
    assert mcs._inset_controls.parentWidget() is page.scope
    assert not mcs._inset_controls.isHidden()
    # The check + combo moved with the container.
    assert mcs.inset_check.parentWidget() is mcs._inset_controls


def test_inset_control_follows_page_switch(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0), "CH01")
    mcs.add_capture(_cap(0), "CH02")
    p1 = mcs.ensure_page("CH01"); p2 = mcs.ensure_page("CH02")
    mcs.entry_list.setCurrentRow(0)             # show CH01
    assert mcs._inset_controls.parentWidget() is p1.scope
    mcs.entry_list.setCurrentRow(1)             # show CH02 → control follows
    assert mcs._inset_controls.parentWidget() is p2.scope


def test_inset_control_survives_clear(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0), "CH01")
    mcs.clear()
    # Not deleted with the torn-down page (a deleted C++ object would raise);
    # detached back to mcs + hidden.
    assert mcs._inset_controls.parentWidget() is mcs
    assert mcs._inset_controls.isHidden()
    # A subsequent capture re-mounts it on the new page.
    mcs.add_capture(_cap(0), "CH09")
    assert mcs._inset_controls.parentWidget() is mcs.ensure_page("CH09").scope


def test_scopeplot_mounts_extra_control_and_keeps_reset(qapp):
    from PyQt6 import QtWidgets
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    w = QtWidgets.QLabel("x")
    sp.mount_extra_control(w)
    assert w.parentWidget() is sp               # reparented into the control row
    assert sp.reset_view_btn is not None        # Reset view still present


def test_latest_button_rearms_and_jumps(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    for ch in ("CH01", "CH02", "CH03"):
        mcs.add_capture(_cap(0), ch)
    mcs.entry_list.setCurrentRow(0)             # pin CH01
    assert mcs._entry_auto_follow is False

    mcs._jump_latest_entry()
    assert mcs._entry_auto_follow is True
    assert mcs.entry_list.currentRow() == 2     # jumped to latest (CH03)
    # Following the latest → nothing to jump to → button disabled.
    assert mcs._entry_latest_btn.isEnabled() is False


def test_reselecting_latest_re_enables_follow(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0), "CH01")
    mcs.add_capture(_cap(0), "CH02")            # latest = CH02, row 1
    mcs.entry_list.setCurrentRow(0)             # pin CH01 → follow off
    assert mcs._entry_auto_follow is False
    mcs.entry_list.setCurrentRow(1)             # user reselects the latest
    assert mcs._entry_auto_follow is True       # follow re-armed


def test_go_to_latest_waveform_jumps_across_pages(qapp):
    """Operator: "I pressed 'Go to latest sample', but it did nothing.
    Also rename it to 'Go to latest waveform'" — the per-page button now
    jumps to the latest capture of the latest CHANNEL/COMBO (the old
    page-local jump had nothing newer on its own page when the user was
    pinned on an older combo)."""
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0), "CH01")
    mcs.add_capture(_cap(0), "CH02")
    mcs.add_capture(_cap(1), "CH02")            # latest waveform: CH02 #2
    mcs.entry_list.setCurrentRow(0)             # pin the OLD page (CH01)
    assert mcs._entry_auto_follow is False
    page1 = mcs._pages["CH01"]
    assert page1._nav_latest.text() == "Go to latest waveform"
    page1._nav_jump_latest()                    # press the button on CH01
    # Jumped to the LATEST combo's page…
    assert mcs.entry_list.currentRow() == 1, "did not jump to the latest combo"
    assert mcs._entry_auto_follow is True
    # …and that page shows ITS latest capture with follow re-armed.
    page2 = mcs._pages["CH02"]
    assert page2._current_idx == len(page2._captures) - 1
    assert page2._auto_follow is True


def test_capture_dropdown_is_wide(qapp):
    """Operator: "increase the width of the dropdown list for viewing
    different capture numbers"."""
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0), "CH01")
    page = mcs._pages["CH01"]
    assert page._nav_combo.minimumWidth() >= 260


def test_clear_resets_follow_state(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0), "CH01")
    mcs.entry_list.setCurrentRow(0)
    mcs.clear()
    assert mcs._entry_auto_follow is True
    assert mcs._latest_key is None
    assert mcs._entry_latest_btn.isEnabled() is False


# ------------------------------------------------- marker / axis-title fixes
def test_set_markers_picks_less_intersecting_side(qapp):
    """A label whose RIGHT side would be buried in the trace, with a CLEAR
    left side, must be placed to the LEFT (operator: "the third access
    voltage clearly needs to be on the left side of the marker because it
    is intersecting the plot").  The trace-intersection penalty is graded,
    so the scorer prefers the side that clips the trace less."""
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    sp._plot.setXRange(0.0, 200.0, padding=0)
    sp._plot.setYRange(-1.0, 1.0, padding=0)
    # Left-axis trace BURIES the marker's RIGHT side (a ±0.6 V zigzag for
    # x≥100 spans any vertically-offset label box) but is CLEAR on the LEFT
    # (flat at +0.95 V for x<100, far from the y≈0 label).
    tx = np.linspace(0.0, 200.0, 400)
    ty = np.full_like(tx, 0.95)
    right = tx >= 100.0
    ty[right] = np.where(np.arange(int(right.sum())) % 2 == 0, -0.6, 0.6)
    sp.set_traces(tx, {"Vmon": ty}, {"Vmon": "#E6B800"}, {"Vmon": "left"})
    sp.set_markers([("Va", 100.0, 0.0, "Va = 0.2 V", "#000000", "hbar",
                     "<i>V</i><sub>a</sub> = 0.2 V")])
    texts = [it for it in sp._marker_items if hasattr(it, "toPlainText")]
    assert len(texts) == 1
    assert texts[0].pos().x() < 100.0, \
        "label must avoid the buried right side and go LEFT"


def test_align_y_zeros_fits_axis_text_space(qapp):
    """align_y_zeros pins each axis's reserved tick-text width to its
    actual numbers so the rotated axis TITLE hugs them symmetrically
    (operator: "the right axis label is too far from the numbering — match
    the gap like the left axis")."""
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot(); sp.resize(800, 400); sp.show(); qapp.processEvents()
    t = np.linspace(-100.0, 400.0, 600)
    sp.set_traces(t, {"Vmon": np.where((t > 0) & (t < 200), -0.2, 0.0),
                      "Imon": np.where((t > 0) & (t < 200), -2.0, 0.0)},
                  {"Vmon": "#E6B800", "Imon": "#00B4C8"},
                  {"Vmon": "left", "Imon": "right"})
    sp.align_y_zeros(); qapp.processEvents()
    for which in ("left", "right"):
        ax = sp._plot.getAxis(which)
        assert ax.style.get("autoExpandTextSpace") is False
        ttw = ax.style.get("tickTextWidth")
        assert isinstance(ttw, (int, float)) and ttw > 0


def test_density_right_axis_label_uses_brackets(qapp):
    """The current-density right-axis label puts the scale INSIDE the
    brackets (operator: "I want the current density scale in brackets,
    e.g., [A/cm2 = 50 uA]").  5000 µm² × 0.01 = 50 µA per A/cm².

    The I_mon unit dropdown defaults to µA (app-wide "default current"),
    so density is engaged by picking the "Density [A/cm²]" item."""
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.set_surface_area_um2(5000.0)
    mcs.imon_unit_combo.setCurrentIndex(1)          # pick A/cm²
    mcs.add_capture(_cap(0, amp=100.0), "CH01")
    page = mcs.ensure_page("CH01")
    assert page.scope._right_title.text() == "Current Density [A/cm² = 50 µA]"


def test_small_signal_cluster_labels_do_not_overlap(qapp):
    """At low current the signal is a thin y-band and every marker clusters;
    the tier-stacking placement must still keep label boxes from overlapping
    (operator: "small current … the marker labels intersect with each other
    and the plot")."""
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot(); sp.resize(1000, 480)
    sp._plot.setXRange(-50.0, 520.0, padding=0)
    sp._plot.setYRange(-0.022, 0.022, padding=0)   # ±22 mV like the screenshot
    t = np.linspace(-50.0, 520.0, 1200)
    v = np.where((t > 0) & (t < 200), -0.012,
                 np.where((t > 220) & (t < 420), 0.010, 0.0))
    sp.set_traces(t, {"Vmon": v}, {"Vmon": "#E6B800"}, {"Vmon": "left"})
    mk = [
        ("Va1",   5.0, -0.012, "Va1=0.012 V\nRa1=2.3 kΩ", "#000", "hbar", None),
        ("Va2", 205.0,  0.004, "Va2=0.016 V\nRa2=3.1 kΩ", "#000", "hbar", None),
        ("Va3", 222.0,  0.006, "Va3=0.007 V\nRa3=1.4 kΩ", "#000", "hbar", None),
        ("Emc", 212.0, -0.005, "Emc=-0.005 V",            "#000", "+",    None),
        ("Vd",  199.0, -0.012, "Vd=-0.013 V",             "#000", "hbar", None),
        ("Va4", 419.0,  0.010, "Va4=0.017 V\nRa4=3.5 kΩ", "#000", "hbar", None),
        ("Ema", 432.0,  0.001, "Ema=0.002 V",             "#000", "+",    None),
    ]
    sp.set_markers(mk)
    (x0, x1), (y0, y1) = sp._plot.getViewBox().viewRange()
    xs, ys = x1 - x0, y1 - y0
    texts = [it for it in sp._marker_items if hasattr(it, "toPlainText")]
    assert len(texts) == 7
    boxes = []
    for it in texts:
        p = it.pos(); txt = it.toPlainText()
        w = max(len(s) for s in txt.split("\n")) * 0.0085 * xs
        h = (txt.count("\n") + 1) * 0.052 * ys
        ax_, ay_ = it.anchor.x(), it.anchor.y()
        boxes.append((p.x() - ax_ * w, p.x() + (1 - ax_) * w,
                      p.y() - (1 - ay_) * h, p.y() + ay_ * h))
    overlaps = 0
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            bl, br, bb, bt = boxes[a]
            pl, pr, pb, pt = boxes[b]
            if (min(br, pr) - max(bl, pl) > 0
                    and min(bt, pt) - max(bb, pb) > 0):
                overlaps += 1
    assert overlaps == 0, f"{overlaps} overlapping label pairs at small signal"


# ----------------------------------------- plot↔table capture sync (desync fix)
def test_capture_changed_emitted_on_per_capture_navigation(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0, amp=50.0), "CH01")
    mcs.add_capture(_cap(1, amp=100.0), "CH01")   # two captures, one page
    page = mcs.ensure_page("CH01")
    seen = []
    mcs.captureChanged.connect(lambda c: seen.append(c))
    assert page.set_index(0) is True               # navigate back to capture 0
    assert seen, "navigating the plot must re-point the metrics table"
    assert seen[-1] is page.current_capture()
    assert page.current_capture().index == 0


def test_capture_changed_emitted_on_entry_switch(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0, amp=50.0), "CH01")
    mcs.add_capture(_cap(0, amp=80.0), "CH02")
    seen = []
    mcs.captureChanged.connect(lambda c: seen.append(c))
    mcs.entry_list.setCurrentRow(0)                # switch to CH01's page
    assert seen, "switching entries must re-point the metrics table"


def test_metric_table_drops_active_qualifier_without_eact(qapp):
    """No E_act/E_ret → V_mon IS active-vs-return, so the table shows a
    SINGLE V_d (matching the plot) and no misleading 'active' qualifier
    (operator: "There should not be any 'Vd active'")."""
    from stimtest.gui.widgets import MetricTable
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=100.0))
    c.metrics.driving_voltage_v = 2.684
    c.metrics.access_voltage_per_phase_v = [1.5, 1.4]
    c.metrics.access_resistance_per_phase_kohm = [1.6, 1.7]
    c.metrics.polarization_per_phase_v = [-0.83, 0.15]
    tbl = MetricTable(); tbl.show_capture(c)
    labels = " ".join(_mrt(tbl, i)[0] for i in range(tbl.rowCount()))
    vals = [_mrt(tbl, i)[1] for i in range(tbl.rowCount())]
    assert "active" not in labels                  # no "active" qualifier
    assert "2.684" in vals                          # single V_d = max|V_mon|


def test_metric_table_keeps_active_return_with_eact(qapp):
    from stimtest.gui.widgets import MetricTable
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=100.0))
    c.metrics.active_driving_voltage_per_phase_v = [2.2, 2.1]
    c.metrics.return_driving_voltage_per_phase_v = [0.3, 0.2]   # E_act present
    c.metrics.polarization_per_phase_v = [-0.83, 0.15]
    c.metrics.return_polarization_per_phase_v = [-0.10, 0.05]
    tbl = MetricTable(); tbl.show_capture(c)
    labels = " ".join(_mrt(tbl, i)[0] for i in range(tbl.rowCount()))
    assert "active" in labels and "return" in labels


def test_epol_guide_lines_at_expected_locations(qapp):
    """Operator: "put a vertical line at each expected location of
    electrode polarization (12 us after the phase) if there are
    interphase delays or discharge delay."  Guides come from
    ``plotting.expected_epol_times_us`` (phase_end + 12 µs, only for
    phases with a trailing delay) and draw as dashed vertical
    InfiniteLines; passing [] wipes them."""
    import numpy as np
    from stimtest.gui.widgets import ScopePlot
    from stimtest.plotting import expected_epol_times_us
    from stimtest.session import Capture
    from stimtest.waveforms import Phase, PulsePattern, SHAPE_SINUSOIDAL

    p = PulsePattern(phases=[
        Phase(amplitude_ua=-50.0, width_us=200.0, shape=SHAPE_SINUSOIDAL,
              delay_after_us=20.0),
        Phase(amplitude_ua=+50.0, width_us=200.0, shape=SHAPE_SINUSOIDAL,
              delay_after_us=20.0),
    ], rate_hz=100.0)
    cap = Capture(index=0, pattern=p)
    t = np.linspace(-64.0, 576.0, 800)
    cap.time_us = t
    m = (t >= 0) & (t <= 200)
    cap.i_mon_ua = np.where(m, -50.0 * np.sin(np.pi * np.clip(t, 0, 200) / 200),
                            0.0)
    cap.v_mon_v = np.where(m, -0.2, 0.0)

    from stimtest.metrics import pulse_onset_us
    guides = expected_epol_times_us(cap)
    # Two delayed phases → two guides, labelled by polarity.
    assert [lbl for lbl, _ in guides] == ["Emc", "Ema"], guides
    # Spacing = phase-2 end − phase-1 end = width(200) + delay(20) = 220 µs.
    assert guides[1][1] - guides[0][1] == pytest.approx(220.0, abs=1e-6)
    # Each guide sits 12 µs past its phase end — INSIDE the 20 µs delay.
    onset = pulse_onset_us(cap.time_us, cap.i_mon_ua, cap.v_mon_v)
    p1_end, p2_end = onset + 200.0, onset + 420.0
    assert guides[0][1] == pytest.approx(p1_end + 12.0, abs=1e-6)
    assert guides[1][1] == pytest.approx(p2_end + 12.0, abs=1e-6)
    assert 0 < guides[0][1] - p1_end < 20.0   # inside the delay window
    assert 0 < guides[1][1] - p2_end < 20.0

    sp = ScopePlot()
    if getattr(sp, "_inset", None) is None and sp._plot is None:
        pytest.skip("no pyqtgraph plot")
    sp.resize(700, 500); sp.show(); qapp.processEvents()
    sp.set_traces(t, {"V_mon": cap.v_mon_v}, axis={"V_mon": "left"})
    sp.set_epol_guides(guides)
    assert len(sp._epol_guide_items) == 2
    sp.set_epol_guides([])
    assert len(sp._epol_guide_items) == 0


def test_gaussian_delayless_has_no_epol_guides(qapp):
    """A guide is drawn ONLY where a trailing delay exists (a quiet window
    to read E_pol).  No interphase/discharge delay → no guides."""
    import numpy as np
    from stimtest.plotting import expected_epol_times_us
    from stimtest.session import Capture
    from stimtest.waveforms import Phase, PulsePattern, SHAPE_GAUSSIAN

    p = PulsePattern(phases=[
        Phase(amplitude_ua=-50.0, width_us=200.0, shape=SHAPE_GAUSSIAN,
              delay_after_us=0.0),
        Phase(amplitude_ua=+50.0, width_us=200.0, shape=SHAPE_GAUSSIAN,
              delay_after_us=0.0),
    ], rate_hz=100.0)
    cap = Capture(index=0, pattern=p)
    t = np.linspace(-64.0, 576.0, 400)
    cap.time_us = t
    cap.i_mon_ua = np.zeros_like(t)
    cap.v_mon_v = np.zeros_like(t)
    assert expected_epol_times_us(cap) == []
