"""Marker labels must not overlap each other — measured on RENDERED geometry.

Operator: "I have told you to be better at placing marker labels without
overlapping with other labels."

The placers scored candidates against ESTIMATED label boxes, so the overlap
penalty was computed on fiction:

  * export (``plotting._place_marker_labels_mpl``) guessed the axes at "~80 %
    of the figure" and the label width at a mean glyph advance × character
    count — and was invoked BEFORE ``tight_layout``, which then resized the
    axes under the already-placed labels;
  * live (``widgets.ScopePlot.set_markers``) sized labels as a FIXED FRACTION
    of the view span, which is only right at one particular widget pixel size,
    so every Height +/− press or window resize silently rescaled them.

Measured on the real bench export (exp_vt_max_cathodal_test_dc, 16 channels,
matplotlib's own ``get_window_extent``): **7 label-label overlaps and 4 labels
sitting on a trace**.  After measuring the geometry and fixing the penalty
ORDER — off-axes > label-label overlap > trace intersection — it is **0
overlaps and 0 clipped labels**.

The residual trade is deliberate: a label crossing a trace line still reads;
two labels stacked on each other do not.
"""
from __future__ import annotations

import numpy as np
import pytest

matplotlib = pytest.importorskip("matplotlib")
matplotlib.use("Agg")

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest import plotting
from stimtest.session import Capture, ChannelRun, Session, TestParameters
from stimtest.waveforms import Phase, PulsePattern, SHAPE_RECTANGULAR


def _capture():
    """Biphasic WITH an interphase delay and a discharge delay, so the marker
    set includes the clustered V_a2/Emc/V_d and V_a4/Ema/V_d2 groups that the
    operator's screenshot showed colliding."""
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-500.0, width_us=200, shape=SHAPE_RECTANGULAR,
              delay_after_us=20.0),
        Phase(amplitude_ua=500.0, width_us=200, shape=SHAPE_RECTANGULAR,
              delay_after_us=20.0),
    ], rate_hz=200.0)
    t = np.linspace(-60.0, 600.0, 4000)
    v = np.zeros_like(t)
    i = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    m2 = (t >= 220.0) & (t <= 420.0)
    v[m1] = -1.0 - 0.0025 * t[m1]
    v[m2] = 1.0 + 0.0020 * (t[m2] - 220.0)
    i[m1] = -500.0
    i[m2] = 500.0
    c = Capture(index=7, pattern=pat)
    c.time_us = t; c.v_mon_v = v; c.i_mon_ua = i
    return c


def _session_run(cap):
    test = TestParameters(experiment="VT", pattern=cap.pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="nb", subject="subj", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(cap)
    sess.add_run(run)
    return sess, run


def _rendered_label_boxes(fig):
    fig.canvas.draw()
    rend = fig.canvas.get_renderer()
    out = []
    for ax in fig.axes:
        for a in ax.texts:
            if str(a.get_text()).strip():
                out.append((ax, a.get_text(), a.get_window_extent(rend)))
    return out


def _overlap(a, b):
    dx = min(a.x1, b.x1) - max(a.x0, b.x0)
    dy = min(a.y1, b.y1) - max(a.y0, b.y0)
    return (dx * dy) if (dx > 0 and dy > 0) else 0.0


def test_no_two_export_labels_overlap():
    cap = _capture()
    sess, run = _session_run(cap)
    fig = plotting.plot_capture(cap, run, sess,
                                potential_axis=True, return_axis=True)
    try:
        boxes = _rendered_label_boxes(fig)
        assert len(boxes) >= 5, f"expected a marker cluster, got {len(boxes)}"
        bad = []
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                ov = _overlap(boxes[i][2], boxes[j][2])
                if ov > 1.0:
                    bad.append((boxes[i][1].replace("\n", "|"),
                                boxes[j][1].replace("\n", "|"), round(ov, 1)))
        assert not bad, f"labels overlap: {bad}"
    finally:
        matplotlib.pyplot.close(fig)


def test_no_export_label_is_clipped_by_the_axes():
    """A clipped label is worse than an overlapping one, so off-axes must
    outrank the (now prohibitive) overlap penalty — otherwise making overlap
    expensive just pushes tags off the figure instead."""
    cap = _capture()
    sess, run = _session_run(cap)
    fig = plotting.plot_capture(cap, run, sess,
                                potential_axis=True, return_axis=True)
    try:
        clipped = []
        for ax, txt, bb in _rendered_label_boxes(fig):
            ab = ax.get_window_extent(fig.canvas.get_renderer())
            if (bb.x0 < ab.x0 - 0.5 or bb.x1 > ab.x1 + 0.5
                    or bb.y0 < ab.y0 - 0.5 or bb.y1 > ab.y1 + 0.5):
                clipped.append(txt.replace("\n", "|"))
        assert not clipped, f"labels clipped by the axes: {clipped}"
    finally:
        matplotlib.pyplot.close(fig)


def test_labels_are_placed_after_the_layout_is_final():
    """``tight_layout`` must not run after placement.  A label keeps its DATA
    coordinates but a fixed PIXEL size, so resizing the axes underneath it
    changes its size in data units and boxes that just cleared each other end
    up overlapping."""
    src = (plotting.__file__)
    with open(src, "r", encoding="utf-8") as fh:
        code = "\n".join(ln for ln in fh.read().splitlines()
                         if not ln.strip().startswith("#"))
    i_layout = code.index("fig.tight_layout(rect=(0, 0.12, 1, 0.935))")
    i_place = code.index("_place_marker_labels_mpl(_dax, fig")
    assert i_place > i_layout, (
        "marker labels are placed before tight_layout — the axes resize "
        "under them")


def test_export_placer_measures_rather_than_guesses():
    """Guard the two estimates that made the overlap penalty fiction."""
    with open(plotting.__file__, "r", encoding="utf-8") as fh:
        code = "\n".join(ln for ln in fh.read().splitlines()
                         if not ln.strip().startswith("#"))
    assert "get_window_extent(_rend)" in code, (
        "label boxes are no longer measured with matplotlib's renderer")
    assert "72.0 * 0.80" not in code, (
        "the '~80 % of the figure' axes guess is back")
