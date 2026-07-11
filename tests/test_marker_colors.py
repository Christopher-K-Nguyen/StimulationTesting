"""Metric-marker glyphs + labels use per-kind colourblind-safe colours.

Operator: "vary the color of the markers and respective label, colorblind
safe."  ``plotting.MARKER_COLOURS`` is the single source of truth, shared by
the live plot (``multichannel_scope._style``) and the export
(``plotting._mpl_style``); each label is drawn in its glyph's colour.
"""
from __future__ import annotations

import pytest


def test_marker_colours_distinct_and_not_all_black():
    from stimtest.plotting import MARKER_COLOURS
    kinds = ("access", "driving", "polar", "badclass")
    cols = [MARKER_COLOURS[k] for k in kinds]
    assert len(set(cols)) == 4, f"marker colours must be distinct: {cols}"
    # only the bad-channel warning is black; the rest are coloured.
    assert cols.count("#000000") == 1
    # sanity: valid 6-hex colours
    for c in MARKER_COLOURS.values():
        assert c.startswith("#") and len(c) == 7


def _lab(hexstr):
    """sRGB hex → CIELAB (D65) for a perceptual-distance check."""
    h = hexstr.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def _lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _lin(r), _lin(g), _lin(b)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.0
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def _f(t):
        d = 6 / 29
        return t ** (1 / 3) if t > d ** 3 else t / (3 * d * d) + 4 / 29
    fx, fy, fz = _f(x), _f(y), _f(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _dE(a, b):
    la, lb = _lab(a), _lab(b)
    return sum((la[i] - lb[i]) ** 2 for i in range(3)) ** 0.5


def test_marker_colours_far_from_trace_colours():
    """Operator: "the colors for the marker/label need to be DIFFERENT from the
    trace colors."  Every marker hue must be perceptually well-separated from
    EVERY trace hue (the old driving=orange sat ~ΔE 15 from V_mon/E_ret in
    normal vision, ~6 under deuteranopia — the collision).  Assert a healthy
    normal-vision margin against all four traces for every marker kind."""
    from stimtest.plotting import MARKER_COLOURS
    from stimtest.gui.multichannel_scope import TRACE_COLOURS

    traces = list(TRACE_COLOURS.values())
    for kind, mc in MARKER_COLOURS.items():
        if mc == "#000000":
            continue  # black bad-channel warning is trivially distinct
        nearest = min(_dE(mc, tc) for tc in traces)
        assert nearest >= 25.0, (
            f"marker {kind}={mc} too close to a trace colour "
            f"(min ΔE={nearest:.1f} < 25)")


def test_live_and_export_style_maps_use_marker_colours():
    """Both style maps must resolve from MARKER_COLOURS (no hardcoded black)."""
    from stimtest.plotting import MARKER_COLOURS
    # Export map lives in plotting.plot_capture; assert the module references
    # MARKER_COLOURS (guards against a revert to literal colours).
    import inspect
    import stimtest.plotting as P
    src = inspect.getsource(P.plot_capture)
    assert "MARKER_COLOURS[" in src, "export _mpl_style must use MARKER_COLOURS"
    import stimtest.gui.multichannel_scope as M
    msrc = inspect.getsource(M)
    assert "MARKER_COLOURS[" in msrc, "live _style must use MARKER_COLOURS"


def test_export_labels_render_in_marker_colour(tmp_path):
    """End-to-end: a capture with access + polar markers exports with the
    label ``annotate`` colours matching the marker palette (not black)."""
    pytest.importorskip("matplotlib")
    import numpy as np
    from stimtest.session import Capture, Session, TestParameters, ChannelRun
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.waveforms import PulsePattern
    from stimtest.metrics import compute_metrics
    from stimtest.plotting import plot_capture, MARKER_COLOURS

    p = PulsePattern.biphasic(amplitude_ua=80.0)
    t = np.linspace(-100.0, 600.0, 700)
    v = np.where((t >= 0) & (t < 200), -0.4,
                 np.where((t >= 220) & (t < 420), 0.27, 0.0))
    c = Capture(index=0, pattern=p)
    c.time_us = t
    c.v_mon_v = v
    c.i_mon_ua = np.where((t >= 0) & (t < 200), -80.0,
                          np.where((t >= 220) & (t < 420), 80.0, 0.0))
    compute_metrics(c, surface_area_um2=5000.0)
    test = TestParameters(experiment="VT", pattern=p,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="nb", subject="s1", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.surface_area_um2 = 5000.0
    run.captures.append(c)
    sess.add_run(run)

    fig = plot_capture(c, run, sess)
    try:
        # Collect annotation text colours; at least one should be a marker
        # palette colour (blue/orange/purple) rather than black.
        import matplotlib
        palette = {MARKER_COLOURS[k].lower()
                   for k in ("access", "driving", "polar")}
        found = set()
        for ax in fig.axes:
            for ann in ax.texts:
                try:
                    rgba = matplotlib.colors.to_hex(ann.get_color()).lower()
                except Exception:
                    continue
                found.add(rgba)
        assert palette & found, (
            f"no marker-palette label colour found; got {found}")
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)
