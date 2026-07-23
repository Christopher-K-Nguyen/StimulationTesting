"""Driving-voltage (V_d) marker placement on the experiment plot.

Operator spec: "Driving voltage is not plotted correctly.  Plot the
largest voltage change in V_mon.  For biphasic symmetric, V_d should be
the end [of the] first phase.  For asymmetric, V_d should be at the end
of the largest current phase."

So the V_d marker is ONE point at the END of the *driving* phase — the
phase with the largest |amplitude| (first-on-tie, so a symmetric
biphasic uses phase 1) — and its value is the V_mon change from the
pre-pulse baseline there.

Access voltage (V_a) is plotted at EVERY access point — leading AND
trailing — with the count adapting to the interphase / discharge delays
(operator: "You are forgetting to plot the trailing access voltage …
the number of access voltage and resistance depends on the existences
of interphase delay and discharge delay").
"""
from __future__ import annotations

import dataclasses
import sys

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _phase_windows(pattern, onset_us: float):
    """Replicate the marker code's phase-time walk: each phase starts
    where the previous one's post-delay ended, anchored at the detected
    pulse onset."""
    cur = onset_us
    out = []
    for ph in pattern.phases:
        t0 = cur
        t1 = cur + ph.width_us
        out.append((t0, t1))
        cur = t1 + ph.delay_after_us
    return out


def _capture(pattern, phase_iv):
    """Build a Capture whose V_mon/I_mon are flat plateaus filling each
    real phase window (so interphase delays are honoured)."""
    from stimtest.session import Capture
    from stimtest.metrics import compute_metrics
    t = np.linspace(-100.0, 600.0, 2800)
    c = Capture(index=0, pattern=pattern)
    c.time_us = t
    c.i_mon_ua = np.zeros_like(t)
    c.v_mon_v = np.zeros_like(t)
    for (a, b), (i_ua, v_v) in zip(_phase_windows(pattern, 0.0), phase_iv):
        m = (t >= a) & (t < b)
        c.i_mon_ua[m] = i_ua
        c.v_mon_v[m] = v_v
    compute_metrics(c, surface_area_um2=5000.0)
    return c


def _all_markers(cap):
    """Render the capture and return a list of (text, x, symbol) for
    every marker.  Both the bar and the plus are now custom QPainterPath
    glyphs (so they can share a pen width), so identify them by identity
    against the cached paths: ``"hbar"`` / ``"+"`` (else the pyqtgraph
    symbol string)."""
    from stimtest.gui.multichannel_scope import MultiChannelScope
    from stimtest.gui import widgets as _w
    mcs = MultiChannelScope()
    mcs.add_capture(cap, "CH01")
    items = getattr(mcs._pages["CH01"].scope, "_marker_items", [])
    out = []
    for i, it in enumerate(items):
        if hasattr(it, "toPlainText"):
            sp = items[i - 1]
            x = float(sp.getData()[0][0])
            sym = sp.opts.get("symbol")
            if sym is _w._hbar_symbol():
                sym = "hbar"
            elif sym is _w._vbar_symbol():
                sym = "vbar"
            elif sym is _w._plus_symbol():
                sym = "+"
            out.append((it.toPlainText(), x, sym))
    return out


def _vd_marker(cap):
    """(text, x) of the V_d marker, or None.  Rendered tags use HTML
    subscripts, so the plain-text form is ``Vd`` (no underscore)."""
    for text, x, _sym in _all_markers(cap):
        if "Vd" in text:
            return text, x
    return None


def test_vd_at_end_of_first_phase_for_symmetric(qapp):
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=80.0)
    cap = _capture(p, [(-80.0, -0.30), (80.0, 0.20)])
    win = _phase_windows(p, 0.0)
    res = _vd_marker(cap)
    assert res is not None, "a V_d marker must be drawn"
    text, x = res
    # value is the cathodic-phase change, at the end of phase 1
    assert "-0.300" in text
    assert abs(x - win[0][1]) <= 2.0, \
        f"V_d should sit at the end of phase 1 (~{win[0][1]}), got x={x}"


def test_vd_at_end_of_largest_current_phase_for_asymmetric(qapp):
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=80.0)
    ph0 = dataclasses.replace(p.phases[0], amplitude_ua=-40.0, width_us=200.0)
    ph1 = dataclasses.replace(p.phases[1], amplitude_ua=120.0, width_us=100.0)
    pa = dataclasses.replace(p, phases=[ph0, ph1])
    cap = _capture(pa, [(-40.0, -0.10), (120.0, 0.50)])
    win = _phase_windows(pa, 0.0)
    res = _vd_marker(cap)
    assert res is not None, "a V_d marker must be drawn"
    text, x = res
    # phase 2 carries the larger |current| (120 µA) -> V_d there
    assert "+0.500" in text
    assert abs(x - win[1][1]) <= 2.0, \
        f"V_d should sit at the end of phase 2 (~{win[1][1]}), got x={x}"


def test_va_uses_hbar_vd_plus_and_polarization_vbar(qapp):
    """Operator glyph convention (latest): V_a keeps the horizontal-bar
    glyph; V_d is a "+"; electrode polarization is a VERTICAL bar ("vbar")
    labelled Emc (cathodic) / Ema (anodic) ("Change the electrode polarization
    marker as vertical bar instead of a plus symbol").  V_d vs E_pol
    disambiguate by shape (+ vs |) + colour + label."""
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=80.0)
    cap = _capture(p, [(-80.0, -0.30), (80.0, 0.20)])
    marks = _all_markers(cap)
    by_kind = {}
    for text, _x, sym in marks:
        head = text.split("=")[0].strip()
        by_kind[head] = sym
    # access -> horizontal bar (HTML subscripts -> plain "Va1"/"Vd")
    assert by_kind.get("Va1") == "hbar", f"got {by_kind}"
    # driving -> "+"
    assert by_kind.get("Vd") == "+", f"got {by_kind}"
    # polarization -> VERTICAL bar, cathodic phase = Emc, anodic phase = Ema
    assert by_kind.get("Emc") == "vbar", f"got {by_kind}"
    assert by_kind.get("Ema") == "vbar", f"got {by_kind}"


def test_access_markers_include_leading_and_trailing(qapp):
    """Both interphase + discharge delays present → 4 access markers
    (lead-ph1, trail-ph1, lead-ph2, trail-ph2).  The TRAILING access
    voltages must be plotted (operator: "You are forgetting to plot the
    trailing access voltage")."""
    from stimtest.waveforms import PulsePattern
    from stimtest import plotting
    p = PulsePattern.biphasic(amplitude_ua=80.0)
    assert all(ph.delay_after_us > 0 for ph in p.phases), \
        "fixture assumes interphase + discharge delays"
    cap = _capture(p, [(-80.0, -0.30), (80.0, 0.20)])
    acc = [m for m in plotting.compute_metric_markers(cap)
           if m["kind"] == "access"]
    roles = [m["role"] for m in acc]
    assert len(acc) == 4, f"expected 4 access points, got {roles}"
    assert roles.count("trail") == 2, f"two trailing points expected: {roles}"
    assert roles.count("lead") == 2, f"two leading points expected: {roles}"
    # the tag carries both V_a and R_a
    assert all("R_a" in m["text"] for m in acc)


def test_access_marker_count_depends_on_delays(qapp):
    """Operator: "the number of access voltage and resistance depends on
    the existences of interphase delay and discharge delay."  Drop the
    interphase delay → the fused ph1→ph2 boundary yields one fewer access
    point (3 instead of 4)."""
    from stimtest.waveforms import PulsePattern
    from stimtest import plotting
    p = PulsePattern.biphasic(amplitude_ua=80.0)
    ph0 = dataclasses.replace(p.phases[0], delay_after_us=0.0)
    p2 = dataclasses.replace(p, phases=[ph0, p.phases[1]])
    cap = _capture(p2, [(-80.0, -0.30), (80.0, 0.20)])
    acc = [m for m in plotting.compute_metric_markers(cap)
           if m["kind"] == "access"]
    assert len(acc) == 3, \
        f"no-interphase → 3 access points, got {[m['role'] for m in acc]}"
    # still has the discharge-trailing point
    assert any(m["role"] == "trail" for m in acc)


def test_marker_labels_use_italic_var_upright_subscript(qapp):
    """Operator: "Use proper variable formatting on the plot markers" —
    italic variable, upright subscript (MATLAB ``{\\itV}_{acc}`` style)."""
    from stimtest.waveforms import PulsePattern
    from stimtest import plotting
    p = PulsePattern.biphasic(amplitude_ua=80.0)
    cap = _capture(p, [(-80.0, -0.30), (80.0, 0.20)])
    marks = plotting.compute_metric_markers(cap)
    htmls = [h for h in (plotting.marker_label_html(m) for m in marks) if h]
    maths = [plotting.marker_label_mathtext(m) for m in marks]
    # live (pyqtgraph) HTML — <i>var</i><sub>sub</sub>
    assert any("<i>V</i><sub>a1</sub>" in h for h in htmls)
    assert any("<i>R</i><sub>a1</sub>" in h for h in htmls)   # access shows R too
    assert any("<i>E</i><sub>mc</sub>" in h for h in htmls)
    assert any("<i>V</i><sub>d</sub>" in h for h in htmls)
    # exported (matplotlib) mathtext — italic var via math mode, upright
    # subscript via \mathrm
    assert any(r"$V_{\mathrm{a1}}$" in m for m in maths)
    assert any(r"$E_{\mathrm{mc}}$" in m for m in maths)


def test_set_markers_separates_overlapping_labels(qapp):
    """Two markers at the SAME point must not have their text tags drawn
    on top of each other, and there are NO dashed leader lines (operator:
    "I do not like these dashed lines … position the labels without
    intersecting with each other")."""
    from stimtest.gui.widgets import ScopePlot
    from pyqtgraph import PlotCurveItem
    sp = ScopePlot()
    # Realistic on-screen view so the candidate scorer can place tags
    # (markers far outside the view make every candidate equally off-screen).
    sp._plot.setXRange(0.0, 200.0, padding=0)
    sp._plot.setYRange(-1.0, 1.0, padding=0)
    sp.set_markers([
        ("A", 100.0, 0.0, "A = 1 V", "#0072B2", "+", "<i>A</i> = 1 V"),
        ("B", 100.0, 0.0, "B = 2 V", "#D55E00", "+", "<i>B</i> = 2 V"),
    ])
    texts = [it for it in sp._marker_items if hasattr(it, "toPlainText")]
    assert len(texts) == 2
    pts = [(it.pos().x(), it.pos().y()) for it in texts]
    # The two tags must NOT be drawn on top of each other — the candidate
    # scorer puts them on opposite sides/corners, so they differ in x
    # and/or y (here: opposite horizontal sides of the shared glyph).
    assert (abs(pts[0][0] - pts[1][0]) > 1e-9
            or abs(pts[0][1] - pts[1][1]) > 1e-9), \
        "coincident markers' labels must not overlap"
    # NO leader lines anymore — the dashed connectors were removed.
    assert not any(isinstance(it, PlotCurveItem) for it in sp._marker_items), \
        "leader lines must be gone"


def test_triphasic_polarization_suffixes_repeat_polarity(qapp):
    """A cathodic/anodic/cathodic triphasic gets Emc1, Ema, Emc2 (the
    repeated polarity is disambiguated with a phase-number suffix)."""
    import dataclasses
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=80.0)
    ph0 = dataclasses.replace(p.phases[0], amplitude_ua=-60.0, width_us=100.0)
    ph1 = dataclasses.replace(p.phases[1], amplitude_ua=120.0, width_us=100.0)
    ph2 = dataclasses.replace(p.phases[0], amplitude_ua=-60.0, width_us=100.0,
                              delay_after_us=0.0)
    tri = dataclasses.replace(p, phases=[ph0, ph1, ph2])
    cap = _capture(tri, [(-60.0, -0.20), (120.0, 0.50), (-60.0, -0.20)])
    heads = {text.split("=")[0].strip() for text, _x, _s in _all_markers(cap)}
    assert "Emc1" in heads and "Emc2" in heads, heads
    assert "Ema" in heads, heads


# ---------------------------------------------------------------- no-label glyphs
def _asym_biphasic_with_delays():
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=200.0)
    ph0 = dataclasses.replace(p.phases[0], width_us=200.0,
                              delay_after_us=60.0, amplitude_ua=-200.0)  # driving
    ph1 = dataclasses.replace(p.phases[1], width_us=200.0,
                              delay_after_us=60.0, amplitude_ua=+100.0)
    return dataclasses.replace(p, phases=[ph0, ph1])


def test_subsequent_driving_marker_for_interphase_preceded_phase(qapp):
    """Operator (#120): the primary V_d PLUS a subsequent driving marker for
    each phase preceded by an interphase delay (referenced to the ending-
    interphase V_mon).  The bare "other driving" bars and the ending-
    interphase circle stay removed (gotcha #68's earlier removals)."""
    from stimtest.plotting import compute_metric_markers
    p = _asym_biphasic_with_delays()            # ph2 preceded by a 60 µs iphase
    cap = _capture(p, [(-200.0, -0.7), (100.0, 0.35)])
    marks = compute_metric_markers(cap)
    kinds = [m["kind"] for m in marks]
    dlabels = {m["label"] for m in marks if m["kind"] == "driving"}
    assert "Vd" in dlabels                        # primary V_d (phase 1)
    assert "Vd2" in dlabels                       # subsequent (phase 2)
    assert kinds.count("driving") == 2
    assert "driving_other" not in kinds           # removed
    assert "interphase" not in kinds              # removed


def test_open_channel_shows_annotation_and_value_labels(qapp):
    """Open electrode: the plot shows the response-class + capacitance
    annotation AND the measured value labels (operator #2: "if a channel is
    open, please still put value labels on the plot" — previously it showed the
    class tag ONLY; the values are re-derived from V_mon)."""
    from stimtest.plotting import compute_metric_markers
    p = _asym_biphasic_with_delays()
    cap = _capture(p, [(-200.0, -0.7), (100.0, 0.35)])
    cap.metrics.response_class = "open"
    cap.metrics.effective_capacitance_nf = 0.12
    mks = compute_metric_markers(cap)
    bad = [m for m in mks if m["kind"] == "badclass"]
    assert len(bad) == 1, [m["kind"] for m in mks]
    txt = bad[0]["text"]
    assert "open" in txt and "0.12" in txt and "nF" in txt   # capacitance
    assert "kΩ" not in txt                                   # open → no R
    # request #2: value labels are ALSO drawn alongside the class annotation
    assert any(m["kind"] in ("access", "driving", "polar") for m in mks), \
        [m["kind"] for m in mks]


def test_broken_channel_plot_shows_rc_fit(qapp):
    """A BROKEN (exponential) electrode shows the parallel-R‖C fit R, C, τ
    (operator: "fit the exponential response with RC … report all three") —
    now ALONGSIDE the re-derived value labels (operator #2)."""
    from stimtest.plotting import compute_metric_markers
    p = _asym_biphasic_with_delays()
    cap = _capture(p, [(-200.0, -0.7), (100.0, 0.35)])
    cap.metrics.response_class = "broken"
    cap.metrics.effective_capacitance_nf = 0.13
    cap.metrics.rc_fit_resistance_kohm = 258.0
    cap.metrics.rc_fit_tau_us = 33.0
    mks = compute_metric_markers(cap)
    bad = [m for m in mks if m["kind"] == "badclass"]
    assert len(bad) == 1, [m["kind"] for m in mks]
    txt = bad[0]["text"]
    assert ("broken" in txt and "258" in txt and "0.13" in txt
            and "33" in txt)


def test_capacitance_marker_uses_variable_typography(qapp):
    """Operator: "the capacitance marker label should be a variable, i.e.,
    italics and normal for subscripts."  The bad-channel marker renders C as
    an italic variable with an UPRIGHT subscript — <i>C</i><sub>eff</sub> in
    the live HTML label and $C_\\mathrm{eff}$ in the matplotlib export — with
    the class word on a leading heading line.  R/τ (RC fit) are bare italic
    variables (τ → \\tau in mathtext)."""
    from stimtest.plotting import (compute_metric_markers, marker_label_html,
                                   marker_label_mathtext)
    p = _asym_biphasic_with_delays()

    # Open → pure capacitance: C_eff only.
    cap = _capture(p, [(-200.0, -0.7), (100.0, 0.35)])
    cap.metrics.response_class = "open"
    cap.metrics.effective_capacitance_nf = 0.12
    mk = compute_metric_markers(cap)[0]
    html = marker_label_html(mk)
    assert "<i>C</i><sub>eff</sub>" in html      # italic C, upright eff
    assert "open" in html                        # class word heading line
    mtext = marker_label_mathtext(mk)
    assert r"$C_{\mathrm{eff}}$" in mtext        # italic var, upright subscript

    # Broken → R‖C fit: R, C, τ all italic variables (τ → \tau in mathtext).
    # The capacitance is the R‖C FIT value, shown as a BARE ``C`` — NOT
    # ``C_eff`` (operator: "for broken, do not call it Ceff").  C_eff is
    # reserved for the pure-capacitance open/capacitive linear-ramp value.
    cap.metrics.response_class = "broken"
    cap.metrics.effective_capacitance_nf = 0.13
    cap.metrics.rc_fit_resistance_kohm = 258.0
    cap.metrics.rc_fit_tau_us = 33.0
    mk = compute_metric_markers(cap)[0]
    html = marker_label_html(mk)
    assert "<i>R</i>" in html and "<i>C</i> =" in html   # bare C, not C_eff
    assert "<sub>eff</sub>" not in html                  # NOT C_eff for broken
    assert "<i>τ</i>" in html
    mtext = marker_label_mathtext(mk)
    assert "$R$" in mtext and "$C$" in mtext             # bare C
    assert r"\mathrm{eff}" not in mtext                  # NOT C_eff for broken
    assert r"$\tau$" in mtext                     # Greek mapped, not bare "τ"


def test_metric_table_indicates_open_channel(qapp):
    """The metrics table shows a focused OPEN view — a "Response" row, no
    misleading V_d / E_pol / access rows."""
    from stimtest.gui.widgets import MetricTable
    p = _asym_biphasic_with_delays()
    cap = _capture(p, [(-200.0, -0.7), (100.0, 0.35)])
    cap.metrics.response_class = "open"
    cap.metrics.effective_capacitance_nf = 0.12
    tbl = MetricTable()
    tbl.show_capture(cap)
    keys = [tbl.item(r, 0).text() for r in range(tbl.rowCount())
            if tbl.item(r, 0)]
    vals = [tbl.item(r, 1).text() for r in range(tbl.rowCount())
            if tbl.item(r, 1)]
    assert "Response" in keys
    assert any("OPEN" in v for v in vals)
    # focused view — no per-phase access / polarization rows
    assert not any("access" in k.lower() or "pol" in k.lower() for k in keys)
    assert tbl.rowCount() <= 6
