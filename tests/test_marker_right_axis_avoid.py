"""Marker labels must avoid the RIGHT-axis (I_mon) trace, not only the
left-axis (voltage) traces.

Operator (with screenshot): "The first access voltage/resistance is
intersecting with the plot."  The R_a1 line sat exactly on the teal I_mon
trace.  V_mon is golden-yellow on the LEFT axis; I_mon is teal-cyan on the
RIGHT axis (a separate, zero-aligned ViewBox sharing the screen).  The
label-placement scorer used to skip every non-left curve
(``if self._curve_axis... != "left": continue``), so it was blind to I_mon
and happily placed a tag right on top of it.

The fix maps each right-axis curve into the left-axis coordinate frame
(both ViewBoxes share the screen with zeros aligned) and feeds it to the
same avoidance scorer.  This test forces a 1:1 axis mapping so a right-axis
value lands at a known left-axis position, then asserts the placed label
box does NOT contain the I_mon line.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")
from PyQt6 import QtWidgets

from stimtest.gui.widgets import ScopePlot


@pytest.fixture(scope="module")
def qapp():
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _label_box(sp, text):
    """Reconstruct the placed label's data-coord box from the TextItem,
    using the same geometry the scorer uses."""
    ti = [it for it in sp._marker_items
          if type(it).__name__ == "TextItem"][0]
    px, py = ti.pos().x(), ti.pos().y()
    ax, ay = float(ti.anchor.x()), float(ti.anchor.y())
    (x0, x1), (y0, y1) = sp._plot.getViewBox().viewRange()
    xs, ys = (x1 - x0), (y1 - y0)
    line_h = 0.052 * ys
    char_w = 0.0085 * xs
    n_lines = text.count("\n") + 1
    lab_h = n_lines * line_h
    lab_w = max(len(s) for s in text.split("\n")) * char_w
    bl = px - ax * lab_w
    br = px + (1.0 - ax) * lab_w
    bb = py - (1.0 - ay) * lab_h
    bt = py + ay * lab_h
    return bl, br, bb, bt


def test_label_avoids_right_axis_imon_trace(qapp):
    sp = ScopePlot()
    t = np.linspace(-50.0, 550.0, 1500)
    # V_mon: a brief access spike at the glyph level, recovering to 0 — so
    # the region just BELOW the glyph is clear of V_mon.
    vmon = np.zeros_like(t)
    vmon[(t >= 0.0) & (t < 15.0)] = -0.5
    glyph_y = -0.5
    # I_mon (RIGHT axis): a flat line at -0.65 — exactly where the label's
    # natural "just below the glyph" spot lands.  Left-axis-only avoidance
    # would place the tag right on it.
    imon_level = -0.65
    imon = np.full_like(t, imon_level)
    sp.set_traces(t, {"V_mon": vmon, "I_mon": imon}, axis={"I_mon": "right"})
    sp.align_y_zeros()
    # Force a 1:1 mapping so the right-axis value -0.65 lands at left -0.65.
    sp._plot.getViewBox().setYRange(-2.0, 0.5, padding=0)
    sp._right_vb.setYRange(-2.0, 0.5, padding=0)
    # Pin the X range too (independent of this test's purpose, the
    # right-axis Y-avoidance).  A FEATURE-FILLS-VIEW window — representative
    # of a real capture where the pulse occupies most of the plot — so
    # label placement is deterministic.  (The candidate-label-box width
    # scales with the x-span; a tiny feature in a very wide window is a
    # pathological case the operator's real plots never hit.)
    sp._plot.getViewBox().setXRange(-10.0, 40.0, padding=0)

    text = "V_a1 = 1.339 V\nR_a1 = 1.8 kOhm"
    sp.set_markers([("V_a1", 1.0, glyph_y, text, "#000000", "hbar", None, True)])

    bl, br, bb, bt = _label_box(sp, text)
    # The I_mon line at -0.65 must NOT fall inside the label's vertical span
    # (tiny epsilon so a tag merely touching the edge still passes).
    eps = 1e-3
    assert not (bb + eps <= imon_level <= bt - eps), (
        f"label box y[{bb:.3f},{bt:.3f}] sits on the I_mon line at "
        f"{imon_level} — right-axis trace was not avoided")


def test_epol_plus_label_clears_interpulse_trace(qapp):
    """Operator: "the second or subsequent electrode polarization marker
    labels … can overlap the traces during the interpulse."  The Emc/Ema "+"
    tag has a DOUBLED proximity pull toward its marker (which sits at the phase
    boundary on the flat interpulse level); the strengthened trace penalty must
    still keep the label vertically OFF the flat interpulse trace."""
    sp = ScopePlot()
    t = np.linspace(-50.0, 600.0, 1600)
    vmon = np.zeros_like(t)
    vmon[(t >= 0.0) & (t < 200.0)] = -0.4        # phase 1 (cathodic)
    vmon[(t >= 200.0) & (t < 400.0)] = +0.4      # phase 2 (anodic)
    # interpulse (t >= 400) flat at 0 — the 2nd E_pol marker sits here.
    sp.set_traces(t, {"V_mon": vmon}, axis={})
    sp.align_y_zeros()
    sp._plot.getViewBox().setYRange(-1.0, 1.0, padding=0)
    sp._plot.getViewBox().setXRange(-50.0, 600.0, padding=0)
    sp.set_markers([("Ema2", 405.0, 0.0, "Ema2 = 0.4 V",
                     "#7E2F8E", "+", None, True)])
    bl, br, bb, bt = _label_box(sp, "Ema2 = 0.4 V")
    eps = 1e-3
    assert not (bb + eps <= 0.0 <= bt - eps), (
        f"Ema label box y[{bb:.3f},{bt:.3f}] sits on the flat interpulse "
        f"trace at y=0 — the '+' proximity pull overrode trace avoidance")


def test_left_axis_trace_still_avoided(qapp):
    """Control: the existing left-axis (V_mon) avoidance is unaffected.

    V_mon is flat at -0.5 across the WHOLE window (not just one phase), so a
    label can only clear the trace by moving VERTICALLY off it — there is no
    pre-/post-pulse region at a different y to escape into horizontally.
    This makes the x-agnostic ``-0.5 in box`` check a faithful proxy for
    "the label sits on the trace."  (With a partial-width flat phase the
    proximity-aware placer legitimately parks the tag in the clear region at
    the marker's height — see ``_overlap_frac`` for the x-aware invariant.)
    """
    sp = ScopePlot()
    t = np.linspace(-50.0, 550.0, 1500)
    vmon = np.full_like(t, -0.5)              # V_mon flat across the WHOLE window
    sp.set_traces(t, {"V_mon": vmon}, axis={})
    sp.align_y_zeros()
    sp._plot.getViewBox().setYRange(-2.0, 0.5, padding=0)
    text = "V_a1 = 1.339 V\nR_a1 = 1.8 kOhm"
    sp.set_markers([("V_a1", 1.0, -0.5, text, "#000000", "hbar", None, True)])
    bl, br, bb, bt = _label_box(sp, text)
    eps = 1e-3
    assert not (bb + eps <= -0.5 <= bt - eps), (
        f"label box y[{bb:.3f},{bt:.3f}] sits on the V_mon line at -0.5")


def _overlap_frac(sp, ti):
    """Fraction of the y-span by which ``ti``'s label box vertically
    overlaps ANY trace (V_mon left + I_mon mapped from the right axis) in
    its x-range.  0 = the label is clear of every trace."""
    txt = ti.toPlainText().split("\n")
    px, py = ti.pos().x(), ti.pos().y()
    ax, ay = float(ti.anchor.x()), float(ti.anchor.y())
    (x0, x1), (y0, y1) = sp._plot.getViewBox().viewRange()
    xs, ys = (x1 - x0), (y1 - y0)
    lab_h = len(txt) * 0.052 * ys
    lab_w = max(len(s) for s in txt) * 0.0085 * xs
    bl, br = px - ax * lab_w, px + (1.0 - ax) * lab_w
    bb, bt = py - (1.0 - ay) * lab_h, py + ay * lab_h
    worst = 0.0
    rr = sp._right_vb.viewRange()[1]
    for name, (tx, ty) in sp._curve_data.items():
        tx = np.asarray(tx, float); ty = np.asarray(ty, float)
        if sp._curve_axis.get(name) != "left":          # map right -> left coords
            ty = y0 + (ty - rr[0]) / (rr[1] - rr[0]) * (y1 - y0)
        mm = (tx >= bl) & (tx <= br)
        if np.any(mm):
            worst = max(worst, min(bt, float(ty[mm].max())) - max(bb, float(ty[mm].min())))
    return max(0.0, worst) / ys


def _biphasic_with_interphase(polarity):
    """A biphasic pulse (sign = polarity for phase 1) with a brief
    interphase delay — V_mon charges in phase 1, recovers, charges the
    other way in phase 2; I_mon is the matching square on the right axis.
    Returns (ScopePlot, access-marker list)."""
    sp = ScopePlot()
    t = np.linspace(-60.0, 520.0, 2000)
    v = np.zeros_like(t)
    s = polarity
    m1 = (t >= 0.0) & (t < 200.0)
    v[m1] = s * (0.6 + (1.3 - 0.6) * (1 - np.exp(-(t[m1]) / 120.0)))
    m2 = (t >= 212.0) & (t < 420.0)
    v[m2] = -s * (0.05 + (0.75 - 0.05) * (1 - np.exp(-(t[m2] - 212.0) / 160.0)))
    imon = np.zeros_like(t)
    imon[m1] = -s * 200.0
    imon[m2] = s * 200.0
    sp.set_traces(t, {"V_mon": v, "I_mon": imon}, axis={"I_mon": "right"})
    sp.align_y_zeros()
    markers = [
        ("V_a1", 1.0, s * 0.6, "V_a1 = 0.587 V\nR_a1 = 1.7 kOhm", "#000000", "hbar", None, True),
        ("V_a2", 201.0, -s * 0.05, "V_a2 = 0.621 V\nR_a2 = 1.9 kOhm", "#000000", "hbar", None, True),
        ("V_a3", 213.0, -s * 0.05, "V_a3 = 0.624 V\nR_a3 = 1.9 kOhm", "#000000", "hbar", None, True),
        ("V_a4", 419.0, -s * 0.75, "V_a4 = 0.568 V\nR_a4 = 1.7 kOhm", "#000000", "hbar", None, True),
    ]
    return sp, markers


@pytest.mark.parametrize("polarity", [1, -1], ids=["cathodic-first", "anodic-first"])
def test_clustered_access_labels_clear_of_traces(qapp, polarity):
    """Every access label (incl. the V_a3 cluster at the phase-2 leading
    edge) must clear the waveform for BOTH polarities (operator: "the
    third access voltage/resistance can be placed better because it is
    intersecting with the plot.  Do remember to consider different
    polarity and patterns.")."""
    sp, markers = _biphasic_with_interphase(polarity)
    sp.set_markers(markers)
    texts = [it for it in sp._marker_items
             if type(it).__name__ == "TextItem"]
    overlaps = {ti.toPlainText().split("\n")[0]: _overlap_frac(sp, ti)
                for ti in texts}
    # No access label should sit appreciably on any trace (allow a sliver
    # for the data-coord box estimate).
    for label, ov in overlaps.items():
        assert ov < 0.03, f"{label} overlaps the trace by {ov:.3f} of y-span ({overlaps})"


def test_broken_channel_tall_label_stays_on_screen(qapp):
    """A broken-channel marker renders a 4-line html label (heading + R,
    C_eff, τ) but its PLAIN text is one comma-joined line.  The placement
    must reserve the RENDERED (4-line) height, not the 1-line plain text, or
    the lower lines (C_eff, τ) clip off-screen — the operator saw CH07 show
    only the resistance line.  The glyph is put near the bottom edge to force
    the issue.
    """
    import pyqtgraph as pg
    sp = ScopePlot()
    t = np.linspace(-100.0, 500.0, 1200)
    sp.set_traces(t, {"V_mon": np.zeros_like(t)}, axis={})
    sp._plot.getViewBox().setYRange(-4.0, 6.0, padding=0)     # CH07-like range
    (x0, x1), (y0, y1) = sp._plot.getViewBox().viewRange()
    html = ("broken<br/><i>R</i> = 977 kΩ<br/>"
            "<i>C</i><sub>eff</sub> = 0.12 nF<br/><i>τ</i> = 117 µs")
    text = "broken  ·  R = 977 kΩ, C_eff = 0.12 nF, τ = 117 µs"
    sp.set_markers([("broken", 200.0, -3.9, text, "#000000", "x", html, True)])
    ti = [it for it in sp._marker_items if isinstance(it, pg.TextItem)][0]
    py = ti.pos().y()
    ay = float(ti.anchor.y())
    line_h = 0.052 * (y1 - y0)
    lab_h = 4 * line_h                        # the 4 RENDERED lines
    bb = py - (1.0 - ay) * lab_h
    bt = py + ay * lab_h
    eps = 1e-3
    assert bb >= y0 - eps and bt <= y1 + eps, (
        f"4-line broken label y[{bb:.2f},{bt:.2f}] clips the view "
        f"[{y0},{y1}] — C_eff / τ would be cut off")
