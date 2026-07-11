"""Exported capture plot (plotting.plot_capture) — legend + marker-label
quality regression (operator: "The saved plot is bad. The labels are
intersecting with each other, the plot, and the axis … The legend itself
is intersecting with the plot … indicate the voltage in the legend as
voltage monitor").

Asserts:
  * the V_mon legend entry reads "Voltage monitor";
  * the legend is a FIGURE legend (outside the axes), not an axes legend
    sitting over the trace;
  * the metric-marker labels don't overlap each other;
  * the labels stay within the axes bounds (don't run off the spine).
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from stimtest import plotting
from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.metrics import compute_metrics
from stimtest.session import Capture, ChannelRun, Session, TestParameters
from stimtest.waveforms import Phase, PulsePattern, SHAPE_RECTANGULAR


def _biphasic_capture():
    """Cathodic-first biphasic with interphase + discharge delays — yields
    the full V_a1..V_a4 + E_mc/E_ma + V_d marker set (like the operator's
    CH01)."""
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-900.0, width_us=200, shape=SHAPE_RECTANGULAR,
              delay_after_us=20.0),
        Phase(amplitude_ua=+900.0, width_us=200, shape=SHAPE_RECTANGULAR,
              delay_after_us=20.0),
    ], rate_hz=50.0)
    t = np.linspace(-60.0, 470.0, 4000)
    v = np.zeros_like(t)
    i = np.zeros_like(t)
    # phase 1: IR step + cathodic charging ramp
    m1 = (t >= 0) & (t < 200)
    v[m1] = -1.4 - 1.1 * (t[m1] / 200.0)
    i[m1] = -900.0
    # interphase (200-220): relaxes toward ~ -0.8
    m_ip = (t >= 200) & (t < 220)
    v[m_ip] = -0.8
    # phase 2: IR step + anodic ramp
    m2 = (t >= 220) & (t < 420)
    v[m2] = 0.7 + 0.9 * ((t[m2] - 220) / 200.0)
    i[m2] = +900.0
    # discharge tail toward 0
    m_d = t >= 420
    v[m_d] = 0.15
    c = Capture(index=3, pattern=pat)
    c.time_us = t
    c.v_mon_v = v
    c.i_mon_ua = i
    compute_metrics(c, surface_area_um2=5000.0)
    return c


def _session_run(cap):
    test = TestParameters(experiment="VT", pattern=cap.pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    s = Session(notebook="nb", subject="electrode_a1", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.surface_area_um2 = 5000.0
    run.captures.append(cap)
    s.add_run(run)
    return s, run


def _render():
    cap = _biphasic_capture()
    sess, run = _session_run(cap)
    fig = plt.figure(figsize=plotting._figsize_in(), dpi=plotting.SCREEN_DPI)
    plotting.plot_capture(cap, run, sess, fig=fig)
    fig.canvas.draw()
    return fig


def test_subtitle_matches_experiment_heading_fields():
    """The exported subtitle mirrors the live experiment-plot heading —
    I_stim, J_stim, Q_ph, Q_inj, capture N (operator: "have the same subtitle
    as the experiment plot, e.g., Istim, Jstim, Qph, Qinj, etc")."""
    fig = _render()
    texts = [t.get_text() for t in fig.texts]
    subtitle = next((s for s in texts if "I_{\\mathrm{stim}}" in s), None)
    assert subtitle is not None, texts
    for field in ("I_{\\mathrm{stim}}", "J_{\\mathrm{stim}}",
                  "Q_{\\mathrm{ph}}", "Q_{\\mathrm{inj}}", "capture"):
        assert field in subtitle, (field, subtitle)
    plt.close(fig)


def test_legend_says_voltage_monitor_and_is_a_figure_legend():
    fig = _render()
    # Legend lives on the FIGURE (outside the axes), not on the axes.
    assert fig.legends, "expected a figure-level legend below the plot"
    assert not fig.axes[0].get_legend(), (
        "the axes should NOT carry its own legend (it overlapped the trace)")
    labels = [t.get_text() for lg in fig.legends for t in lg.get_texts()]
    assert "Voltage monitor" in labels
    assert "Current Density" in labels
    plt.close(fig)


def test_marker_labels_do_not_overlap_each_other():
    fig = _render()
    ax_v = fig.axes[0]
    rend = fig.canvas.get_renderer()
    boxes = [t.get_window_extent(rend) for t in ax_v.texts
             if t.get_text().strip()]
    assert len(boxes) >= 4, "expected several metric labels on a biphasic"
    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            ix = (min(boxes[a].x1, boxes[b].x1)
                  - max(boxes[a].x0, boxes[b].x0))
            iy = (min(boxes[a].y1, boxes[b].y1)
                  - max(boxes[a].y0, boxes[b].y0))
            # Allow a 2 px graze (anti-alias / rounding); flag real overlap.
            assert not (ix > 2 and iy > 2), (
                f"labels {a} and {b} overlap by {ix:.0f}×{iy:.0f}px")
    plt.close(fig)


def test_marker_labels_stay_within_axes_bounds():
    fig = _render()
    ax_v = fig.axes[0]
    rend = fig.canvas.get_renderer()
    ax_box = ax_v.get_window_extent(rend)
    for t in ax_v.texts:
        if not t.get_text().strip():
            continue
        b = t.get_window_extent(rend)
        # Labels may sit a few px outside (glyph padding) but never run
        # wholly off the spine.
        assert b.x1 <= ax_box.x1 + 6, "label runs off the right spine"
        assert b.x0 >= ax_box.x0 - 6, "label runs off the left spine"
        assert b.y1 <= ax_box.y1 + 6, "label runs off the top spine"
        assert b.y0 >= ax_box.y0 - 6, "label runs off the bottom spine"
    plt.close(fig)
