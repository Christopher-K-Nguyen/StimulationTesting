r"""Capture plots in the style of the MATLAB ``getPlot_Tek.m`` exporter.

Mirrors the look-and-feel of the MATLAB Tektronix plot:

* MATLAB's default 4-color palette (``#0072BD``, ``#D95319``, ``#EDB120``,
  ``#7E2F8E``).
* Voltage signals on the **left** Y axis (``Voltage (V)``); the current
  trace is plotted on the **right** Y axis as **current density**
  (``A/cm²``) per the MATLAB code (line 272 of ``getPlot_Tek.m``).
* Both Y axes are forced **symmetric around zero** with a tick at zero.
* Horizontal dashed lines mark the **min and max** of the monitor channel,
  with text labels; cursors at end-of-phase / depolarization sample points
  are scattered and labelled.
* Title = subject (``_`` escaped to a single underscore is fine here, the
  MATLAB ``\_`` was a TeX escape that's not needed in matplotlib).
* Subtitle = capture name (``CH09 v 05 @ 246 µA`` etc.).
* Figure size: 1024×576 px (MATLAB's ``FIG_WIDTH``/``FIG_HEIGHT``); DPI
  defaults to 600 on TIFF / PNG export to match the MATLAB ``-r600`` flag.

Public entry points:

* :func:`plot_capture` — return a matplotlib ``Figure`` for one capture.
* :func:`export_capture_plot` — save one plot to disk.
* :func:`export_session_plots` — write the **final capture** of every
  ChannelRun to a folder, one file each (mirrors what ``runVoltageTransient``
  saves at the end of a sweep).
* :func:`export_session_summary_plots` — additional analysis plots:
  ``Q_inj`` vs ``I_stim`` overlay, ``V_d`` vs ``Q_inj``, etc., aggregated
  across runs. Useful for the IEEE NER paper-style summary figures.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

import matplotlib
matplotlib.use("Agg")  # safe default for headless export; Qt overrides if needed
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from .session import Capture, ChannelRun, Session


# ---------------------------------------------------------------------------
# Visual constants (matching MATLAB ``getPlot_Tek.m``)
# ---------------------------------------------------------------------------
MATLAB_COLORS = ("#0072BD", "#D95319", "#EDB120", "#7E2F8E",
                 "#77AC30", "#4DBEEE", "#A2142F")

#: Trace colours for the EXPORTED capture figure — MUST match the LIVE
#: experiment plot (``gui.multichannel_scope.TRACE_COLOURS``) so a saved .tif /
#: POLARIS look identical to the bench plot (operator: "have the same colors as
#: the experiment plot").  Order: V_mon (golden), I_mon (teal), E_act
#: (bluish-green), E_ret (vermillion).  gui imports plotting (not vice-versa),
#: so plotting can't import TRACE_COLOURS — a drift test
#: (test_export_plot_colors.py) asserts the two stay in sync.
_EXP_TRACE_COLORS = ("#E6B800", "#00B4C8", "#009E73", "#D55E00")

#: Per-KIND metric-marker colours (operator: "vary the color of the markers
#: and respective label, colorblind safe" + "the colors for the marker/label
#: need to be DIFFERENT from the trace colors").  The trace palette
#: (``TRACE_COLOURS``) already claims the warm/green/cyan arc — V_mon golden
#: (#E6B800), I_mon teal (#00B4C8), E_act bluish-green (#009E73), E_ret
#: vermillion (#D55E00) — so the markers live in the BLUE→PURPLE→PINK arc that
#: the traces don't touch.  Verified numerically (CIELAB ΔE, incl. Machado-2009
#: deuteranopia + protanopia sims): the OLD driving=orange (#E69F00) sat only
#: ΔE≈6 from V_mon/E_ret under deuteranopia (the operator's complaint); every
#: colour below is ΔE≥13 from EVERY trace under all three vision models
#: (≥40 for normal vision).  The two same-shape ``hbar`` kinds (access /
#: driving) take **blue ↔ reddish-purple** — the max-contrast pair of the arc
#: (ΔE 42 deuteranopia, vs blue↔purple's 12.8) — so they stay distinct without
#: relying on shape; ``polar`` (the ``+`` glyph) takes purple (its shape
#: already separates it from access where blue↔purple contrast is weakest);
#: ``badclass`` keeps black (the ``x`` glyph, shown ALONE on a bad channel, so
#: it never co-occurs with the others).  SHARED by the live plot
#: (``multichannel_scope._style``) and the export (``plotting._mpl_style``) so
#: the two never drift; each marker's LABEL is drawn in its glyph's colour.
#: Don't move any marker back into the trace arc (yellow/orange/green/cyan/
#: vermillion) — re-run the ΔE check if you re-pick.
#: (Operator: "make the marker and label a little darker" — the hues below are
#: the arc colours ×0.85, still ΔE≥42 from every trace, so the darkening
#: doesn't erode the separation.)
# Per-kind marker/label colours.  Driving (V_d) and polarization (Emc/Ema) are
# BOTH drawn with a "+" glyph, so their colours MUST be far apart (operator:
# "The color for marker and label of driving voltage and electrode
# polarization are too similar").  The two "+" kinds take the STRONG-contrast
# pair (blue ↔ reddish-purple, ΔE ≈ 42 under deuteranopia — gotcha #58);
# access (a DIFFERENT shape, hbar) takes the remaining purple, so the weak
# blue↔purple contrast never matters (the hbar vs + shape already
# distinguishes access from driving).  Same three CVD-safe colours as before —
# only the kind→colour assignment rotated, so the ΔE-vs-trace guarantees
# (tests/test_marker_colors.py) are unchanged.
MARKER_COLOURS = {
    # THREE clearly-distinct hue families so access (V_a/R_a) and polarization
    # (Emc/Ema) are never confusable (operator: "Access voltage/resistance and
    # electrode polarization colors are too similar" — the old purple
    # #6B2879 / reddish-purple #AD678E sat only ΔE≈34 apart AND #6B2879
    # collided with the dV/dt overlay trace #7E2F8E).  Now crimson / cobalt /
    # blue-violet.  The polar violet was BRIGHTENED #4B1FA8 → #7A0AF0
    # (operator: driving vs polarization "too similar"): the muted #4B1FA8
    # sat only sum|ΔRGB|=132 from cobalt driving AND collapsed to ΔE≈10 from
    # it under deuteranopia (a red-green-colourblind viewer couldn't tell
    # V_d from Emc/Ema).  #7A0AF0 clears sum|ΔRGB|=216 and stays ΔE≈23/32
    # (deut/prot) distinct from driving, while access↔polar ΔE≈110 and each
    # marker is ≥25 ΔE from EVERY trace hue (verified in
    # tests/test_marker_colors.py + test_marker_color_and_zero.py).
    "access":        "#B0143C",   # crimson red   (hbar — V_a / R_a)
    "driving":       "#1A56C4",   # cobalt blue   (hbar — V_d)
    "driving_other": "#1A56C4",   # cobalt blue   (bare bar, no label)
    "polar":         "#7A0AF0",   # blue-violet   (+ — Emc / Ema)
    "interphase":    "#7A0AF0",   # blue-violet   (marker removed, parity)
    "badclass":      "#000000",   # black — bad-channel (open/broken) warning
}

#: V_mon excursion (V) below which a NORMAL capture's access/E_pol/V_d markers
#: are culled when their value rounds to 0 at display precision — the noise-floor
#: pile-up on a few-µA capture (operator: CH03 at -3.8 µA, ±5 mV).  Matches the
#: classifier's low-signal floor (metrics._CLASSIFY_MIN_SIGNAL_V = 30 mV).
_MARKER_CULL_SIGNAL_FLOOR_V = 0.03

FIG_W_PX, FIG_H_PX = 1024, 576
DEFAULT_DPI = 600
SCREEN_DPI = 100


def _grid(ax, on: bool) -> None:
    """Enable/disable an axes grid WITHOUT the matplotlib gotcha where
    ``ax.grid(False, linestyle=…)`` re-ENABLES the grid because line
    properties were supplied ("First parameter to grid() is false, but line
    properties are supplied. The grid will be enabled").  That gotcha made
    POLARIS's gridline toggle a no-op — the grid was always on.  Pass the
    style ONLY when turning the grid ON.
    """
    if on:
        ax.grid(True, alpha=0.25, linestyle=":")
    else:
        ax.grid(False)


_VOLTAGE_WAVES = frozenset({"V_mon", "E_act", "E_ret"})
#: The subset of :data:`_VOLTAGE_WAVES` that are ELECTRODE POTENTIALS measured
#: vs a reference electrode (E_act / E_ret) — as opposed to V_mon, which is the
#: active-vs-return driving VOLTAGE.  Used to decide whether a volts axis
#: carries only potentials (→ "Potential vs <ref>") or a genuine voltage.
_POTENTIAL_WAVES = frozenset({"E_act", "E_ret"})


def _resolve_reference_label(reference_label: Optional[str]) -> str:
    """Axis-friendly reference-electrode name.  Falls back to ``Ag|AgCl`` (the
    catalog convention — see ``TestParameters.reference_electrode_label``)."""
    ref = (str(reference_label).strip() if reference_label else "")
    return ref or "Ag|AgCl"


def _resolve_return_label(return_label: Optional[str]) -> str:
    """Axis-friendly return / counter-electrode name.  Falls back to ``Pt``
    (the catalog default — ``TestParameters.counter_electrode_label``)."""
    ret = (str(return_label).strip() if return_label else "")
    return ret or "Pt"


def _voltage_axis_label(waves, *, potential_axis: bool = False,
                        reference_label: Optional[str] = None,
                        return_axis: bool = False,
                        return_label: Optional[str] = None,
                        brackets: bool = True) -> str:
    """Title for a y-axis carrying only VOLTS-unit waves (V_mon and/or the
    electrode potentials E_act / E_ret).

    Two optional relabels (operator), each for a SINGLE-quantity volts axis:
      * ``potential_axis`` — axis carries ONLY electrode potentials (E_act /
        E_ret, no raw V_mon): ``Potential vs <ref> [V]`` (they're potentials
        measured against the reference electrode).
      * ``return_axis`` — axis carries ONLY V_mon (no potentials):
        ``Voltage vs <return> [V]`` (V_mon is the active-vs-return driving
        voltage, so name the return / counter electrode).
    The two conditions are mutually exclusive; a MIXED axis (V_mon +
    potentials) stays the generic ``Voltage [V]``.  ``brackets`` picks the
    unit delimiter so each caller matches its own figure convention (live
    plot ``[V]`` / export ``(V)``)."""
    unit = "[V]" if brackets else "(V)"
    waves = set(waves or ())
    has_voltage = "V_mon" in waves
    has_potential = bool(waves & _POTENTIAL_WAVES)
    if potential_axis and has_potential and not has_voltage:
        return f"Potential vs {_resolve_reference_label(reference_label)} {unit}"
    if return_axis and has_voltage and not has_potential:
        return f"Voltage vs {_resolve_return_label(return_label)} {unit}"
    return f"Voltage {unit}"


def _axis_unit_label(waves, *, density: bool = False,
                     potential_axis: bool = False,
                     reference_label: Optional[str] = None,
                     return_axis: bool = False,
                     return_label: Optional[str] = None) -> Optional[str]:
    """Label a y-axis by the CONTENT routed to it, NOT a hardcoded role
    (operator: "the right axis should not be default 'Current monitor'.  If
    multiple voltage channels on one side → 'Voltage'.  If voltage and
    current on one axis → 'Unit'").  Returns ``None`` when the axis carries
    nothing (the caller should hide it).

    ``potential_axis`` / ``reference_label`` opt into ``Potential vs <ref>
    [V]`` for a potentials-only volts axis; ``return_axis`` / ``return_label``
    opt into ``Voltage vs <return> [V]`` for a V_mon-only volts axis (see
    :func:`_voltage_axis_label`)."""
    waves = set(waves or ())
    if not waves:
        return None
    units = {"V" if w in _VOLTAGE_WAVES else "A" for w in waves}
    if units == {"V"}:
        return _voltage_axis_label(waves, potential_axis=potential_axis,
                                   reference_label=reference_label,
                                   return_axis=return_axis,
                                   return_label=return_label,
                                   brackets=True)
    if units == {"A"}:
        return "Current density [A/cm²]" if density else "Current [µA]"
    return "Unit"          # mixed voltage + current on one axis


def _figsize_in(width_px: int = FIG_W_PX, height_px: int = FIG_H_PX,
                dpi: int = SCREEN_DPI) -> Tuple[float, float]:
    """Convert pixel dimensions to matplotlib's inches × dpi sizing."""
    return width_px / dpi, height_px / dpi


# ---------------------------------------------------------------------------
# Y-axis helpers — match the symmetric-around-zero behavior in MATLAB
# ---------------------------------------------------------------------------
def _nice_step(raw: float) -> float:
    """Round ``raw`` UP to the nearest 1 / 2 / 2.5 / 5 × 10ⁿ (MATLAB-style
    "nice" tick step)."""
    import math
    if not (raw > 0) or not math.isfinite(raw):
        return 1.0
    p = 10.0 ** math.floor(math.log10(raw))
    for m in (1.0, 2.0, 2.5, 5.0, 10.0):
        if raw <= m * p * (1.0 + 1e-9):
            return m * p
    return 10.0 * p


def _symmetric_ylim(ax, *, margin: float = 0.05) -> None:
    """Symmetric-about-zero Y range whose ENDS land exactly on a tick.

    Mirrors the ``yLimL_min = -yLimL_big`` block in the MATLAB code
    (lines 371-398), but — operator: "the axes need to begin and end with
    a tick" — the range is set to the OUTERMOST tick (``n × step``) rather
    than the raw data extent, so the top/bottom of the axis is a tick mark
    (no overhang past the last tick).  ``margin`` is fractional headroom
    added beyond the data before snapping (the capture plot passes a
    little so marker labels clear the trace).
    """
    y0, y1 = ax.get_ylim()
    big = max(abs(y0), abs(y1))
    if big == 0:
        return
    big = big * (1.0 + max(float(margin), 0.0))
    # ~3 intervals each side → nice, readable density.
    step = _nice_step(big / 3.0)
    if step <= 0:
        return
    import math
    n = max(int(math.ceil(big / step - 1e-9)), 1)
    ax.set_yticks([step * k for k in range(-n, n + 1)])
    # Range == outermost tick ⇒ the axis begins AND ends on a tick.
    ax.set_ylim(-n * step, n * step)


def _matlab_x_limits(time_us, *, target: int = 7):
    """X-range: the MATLAB ``getPlot_Tek.m`` / ``getAcutePlot3.m`` rule,
    verbatim (operator: "used the same time range adjustment as what I did
    in MATLAB")::

        xMin = round(min(time),1,'significant');
        xMax = round(max(time),1,'significant');
        xlim([xMin xMax]);

    i.e. the full captured time extent with each end rounded to 1
    significant figure — ``[-60, 600]`` for a ``[-64, 576] µs`` capture.
    One end may round INWARD (a small pre-trigger sliver clipped) and the
    other OUTWARD, exactly like MATLAB.  Ticks are laid on the MATLAB
    nice-multiples grid WITHIN ``[xmin, xmax]`` (MATLAB leaves auto ticks
    here; PULSAR draws explicit nice-multiples ticks for a matching sparse
    layout, gotcha #21).  Kept IDENTICAL to the live plot's
    ``widgets._matlab_x_range`` so a saved ``.tif`` frames a capture the
    same as the on-screen experiment plot.  Returns ``(ticks, xmin, xmax)``
    or ``None``.

    History: this REPLACED a half-tick floor/ceil grid (pad each side to the
    ``step/2`` boundary, never crop) that layered "one tick before x=0" /
    "pad 50 us" framing on top of the MATLAB rule.  The operator ultimately
    asked for the plain MATLAB rule; don't re-add the half-tick snapping.
    """
    import math
    t = np.asarray(time_us, dtype=float)
    if t.size < 2:
        return None
    lo = _round_significant(float(np.nanmin(t)), 1)
    hi = _round_significant(float(np.nanmax(t)), 1)
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return None
    step = _nice_step((hi - lo) / max(int(target), 1))
    if step <= 0:
        return [lo, hi], lo, hi
    # Nice-multiples ticks WITHIN [lo, hi] (endpoints included when they land
    # on the grid, which the 1-sig-fig rounding usually arranges).
    k0 = int(math.ceil(lo / step - 1e-9))
    k1 = int(math.floor(hi / step + 1e-9))
    ticks = [step * k for k in range(k0, k1 + 1)]
    if not ticks:
        ticks = [lo, hi]
    return ticks, lo, hi


def _round_significant(x: float, sig: int = 2) -> float:
    """Round ``x`` to ``sig`` significant digits, half AWAY from zero to
    match MATLAB ``round(x, sig, 'significant')`` (Python's ``round`` is
    half-to-even, which frames an exact-half time bound like -45 µs to -40
    instead of MATLAB's -50).  Kept in lock-step with
    ``widgets._round_sig`` / ``calibration._round_sig`` so the export,
    live-experiment, and calibration plots frame a capture identically."""
    import math
    if x == 0 or not np.isfinite(x):
        return 0.0
    d = int(math.ceil(math.log10(abs(x))))
    scale = 10.0 ** (d - sig)
    scaled = x / scale
    r = math.floor(scaled + 0.5) if scaled >= 0.0 else math.ceil(scaled - 0.5)
    return float(r * scale)


def _place_marker_labels_mpl(ax, fig, reqs, traces, *,
                             fontsize: float = 9.0) -> None:
    """Candidate-scoring placement of metric-marker labels on the exported
    capture plot — the matplotlib counterpart of the live
    ``ScopePlot.set_markers`` scorer.

    The old export stacked labels by an x-tier counter only; that let the
    bottom ``V_d`` collide with ``V_a2`` / ``R_a2`` and the right-side
    ``V_a4`` / ``R_a4`` / ``E_ma`` jumble together and into the axis
    (operator: "the labels are intersecting with each other, the plot,
    and the axis").  This places each label (sorted by x) at the lowest-
    penalty candidate among the four diagonal corners + pure up/down, at
    escalating outward tiers, scored by three penalties mirroring the
    live scorer:

      * **off-axes** — any part of the label box outside the data limits
        (hard; the trace must never push a tag off the figure or onto the
        spine).
      * **overlap** with an already-placed label box (the dominant term —
        spreads a cluster onto opposite sides / different tiers).
      * **trace intersection** — GRADED by the vertical overlap of the box
        with the local trace in its x-range, so a tag prefers the side
        that clips the waveform least.

    ``reqs`` is a list of dicts ``{x, y, text, prefer_below}``.
    ``traces`` is a list of ``(t_array, v_array)`` the labels must avoid —
    BOTH the left-axis voltage traces AND the right-axis current density
    mapped into left-axis coordinates (operator: "intersecting with the
    plot" — the current trace is part of the plot too; mirrors the live
    plot's both-axes avoidance).  Annotates ``ax`` in place; no leader
    lines (operator preference).
    """
    if not reqs:
        return
    xlim = ax.get_xlim()
    ylim = ax.get_ylim()
    x_span = max(float(xlim[1] - xlim[0]), 1e-9)
    y_span = max(float(ylim[1] - ylim[0]), 1e-9)
    fig_w_in, fig_h_in = (float(v) for v in fig.get_size_inches())
    # Usable axes extent is ~80 % of the figure after margins/titles.
    ax_w_pts = max(fig_w_in * 72.0 * 0.80, 1.0)
    ax_h_pts = max(fig_h_in * 72.0 * 0.80, 1.0)
    char_pts = 0.60 * fontsize       # mean glyph advance
    line_pts = 1.35 * fontsize       # line pitch
    dpx = x_span / ax_w_pts          # data-x per point
    dpy = y_span / ax_h_pts          # data-y per point
    _traces = [(np.asarray(t, dtype=float), np.asarray(v, dtype=float))
               for (t, v) in traces
               if t is not None and v is not None and len(t) and len(v)]
    gap_x = 0.012 * x_span
    MAX_TIER = 5

    def _vis_len(line):
        # Count RENDERED glyphs, not mathtext markup: "$V_{\mathrm{a4}}$
        # = 1.403 V" renders as ~13 chars, but len() counts ~27 (all the
        # ``$ { } _ \mathrm`` markup).  Over-counting inflated every box
        # ~2× and shoved left-anchored tags far from their markers.
        s = re.sub(r"\\mathrm|[${}\\^_]", "", str(line))
        return len(s)

    def _box_size(text):
        lines = str(text).split("\n")
        n = max(len(lines), 1)
        w = max((_vis_len(s) for s in lines), default=1) * char_pts * dpx
        h = n * line_pts * dpy
        return w, h

    def _trace_band(x0, x1):
        """Union (min, max) of every avoid-trace within [x0, x1]."""
        lo = hi = None
        for (tt, vv) in _traces:
            if tt.size == 0:
                continue
            m = (tt >= x0) & (tt <= x1)
            if np.any(m):
                seg = vv[m]
                slo, shi = float(np.min(seg)), float(np.max(seg))
            else:
                i = int(np.argmin(np.abs(tt - 0.5 * (x0 + x1))))
                slo = shi = float(vv[i])
            lo = slo if lo is None else min(lo, slo)
            hi = shi if hi is None else max(hi, shi)
        if lo is None:
            return None
        return lo, hi

    placed = []   # accumulated (x0, x1, y0, y1) boxes

    def _candidates(mx, my, lw, lh, prefer_below):
        # (h_anchor, side) × tier.  h_anchor: 'r' extends right of mx,
        # 'l' extends left, 'c' centred.  side: +1 above, -1 below.
        out = []
        for tier in range(MAX_TIER + 1):
            for side in ((-1, +1) if prefer_below else (+1, -1)):
                gy = 0.4 * lh + tier * (lh + 0.15 * lh)
                if side > 0:
                    y0 = my + gy
                    y1 = y0 + lh
                else:
                    y1 = my - gy
                    y0 = y1 - lh
                for h_anchor in ("r", "l", "c"):
                    if h_anchor == "r":
                        x0 = mx + gap_x
                        x1 = x0 + lw
                    elif h_anchor == "l":
                        x1 = mx - gap_x
                        x0 = x1 - lw
                    else:
                        x0 = mx - 0.5 * lw
                        x1 = mx + 0.5 * lw
                    out.append((x0, x1, y0, y1, h_anchor, side, tier))
        return out

    def _score(box, tier, mx, my):
        x0, x1, y0, y1 = box
        # PROXIMITY: keep the label NEAR its marker (operator: "make sure
        # the labels are near their markers … Emc and the access labels
        # very far away").  Penalise the gap from the marker to the box —
        # vertical (the dominant term, since tiers escalate vertically) and
        # horizontal — so among otherwise-clear spots the CLOSEST wins and
        # the scorer won't fling a tag across the plot to find open space.
        cy = 0.5 * (y0 + y1)
        v_gap = abs(cy - my) / y_span
        h_gap = max(0.0, x0 - mx, mx - x1) / x_span
        # Linear (gentle near the marker) + QUADRATIC (grows steeply with
        # distance) proximity — mirrors the live scorer (widgets.set_markers,
        # gotcha #67): the quadratic pull caps how far a tag can drift so a
        # noisy full-range trace can't fling the access labels into empty
        # space far from their markers (operator: "The placement of the
        # labels need to be better").
        pen = (tier * 0.20 + 6.0 * v_gap + 4.0 * h_gap
               + 34.0 * (v_gap * v_gap + h_gap * h_gap))
        # off-axes (graded, with a big flat term so a clip never wins)
        off = 0.0
        off += max(0.0, xlim[0] - x0) + max(0.0, x1 - xlim[1])
        off_y = max(0.0, ylim[0] - y0) + max(0.0, y1 - ylim[1])
        if off > 0 or off_y > 0:
            pen += 8.0 + 30.0 * ((off / x_span) + (off_y / y_span))
        # overlap with already-placed labels
        for (px0, px1, py0, py1) in placed:
            ox = min(x1, px1) - max(x0, px0)
            oy = min(y1, py1) - max(y0, py0)
            if ox > 0 and oy > 0:
                # Flat base RAISED (1.0 → 6.0) so ANY label-label overlap
                # costs more than a proximity tier-step — otherwise the
                # quadratic proximity pull (above) could make two close tags
                # tolerate a small overlap rather than escalate apart.
                # Readability (non-overlap) must beat proximity for close
                # markers.
                pen += 6.0 + 90.0 * (ox * oy) / (x_span * y_span)
        # graded trace intersection
        band = _trace_band(x0, x1)
        if band is not None:
            tlo, thi = band
            ov = min(y1, thi) - max(y0, tlo)
            if ov > 0:
                pen += 30.0 * (ov / max(y1 - y0, 1e-9))
        return pen

    for req in sorted(reqs, key=lambda r: r["x"]):
        mx = float(req["x"])
        my = float(req["y"])
        text = req["text"]
        lw, lh = _box_size(text)
        best = None
        for (x0, x1, y0, y1, h_anchor, side, tier) in _candidates(
                mx, my, lw, lh, bool(req.get("prefer_below", True))):
            s = _score((x0, x1, y0, y1), tier, mx, my)
            if best is None or s < best[0]:
                best = (s, x0, x1, y0, y1, h_anchor, side)
        _, x0, x1, y0, y1, h_anchor, side = best
        placed.append((x0, x1, y0, y1))
        # Anchor the text at the box's left edge, vertically centred, so a
        # multi-line tag (V_a + R_a) reads left-aligned inside its box.
        ax.annotate(text, (x0, 0.5 * (y0 + y1)),
                    xytext=(0, 0), textcoords="offset points",
                    fontsize=fontsize, color=req.get("color", "#000000"),
                    zorder=11,
                    ha="left", va="center", multialignment="left")


# ---------------------------------------------------------------------------
# Cursor / annotation pickers
# ---------------------------------------------------------------------------
def _capture_depol_us(capture: Capture) -> float:
    """The E_pol time delay (µs) this capture's metrics were computed with —
    ``capture.metrics.depolarization_us`` (operator-configurable), falling back
    to the canonical ``config.DEPOLARIZATION_TIME_US`` (12 µs) for a legacy
    capture / a capture with no metrics.  Keeps every marker + guide aligned
    to the SAME delay the value was computed with."""
    from .config import DEPOLARIZATION_TIME_US
    try:
        v = float(getattr(capture.metrics, "depolarization_us",
                          DEPOLARIZATION_TIME_US))
        if np.isfinite(v) and v >= 0:
            return v
    except Exception:
        pass
    return float(DEPOLARIZATION_TIME_US)


def _phase_end_times_us(capture: Capture) -> List[Tuple[str, float]]:
    """End-of-phase sample times (µs) — used for cursors on the plot.

    These are the cursor positions ``getPlot_Tek.m`` asks the user to set
    interactively. Here we pick them automatically since we know the pulse
    structure: end of each phase, plus a depolarization-time-after-phase
    sample. ``DEPOLARIZATION_TIME_US`` from ``config.py`` is the offset.
    """
    from .metrics import pulse_onset_us
    depol = _capture_depol_us(capture)
    out: List[Tuple[str, float]] = []
    # Anchor the chain at the DETECTED pulse onset, not t=0.  The time
    # axis is trigger-relative: with the I_mon-trigger fallback the
    # scope fires mid-pulse, so a chain from 0 put Epol1 where Epol2
    # belongs and pushed Epol2 off the end of the record (operator-
    # reported).  With the digital sync trigger the detector returns ≈0
    # and nothing changes.
    cursor = pulse_onset_us(capture.time_us, capture.i_mon_ua,
                            capture.v_mon_v)
    for k, ph in enumerate(capture.pattern.phases, start=1):
        cursor += ph.width_us
        out.append((f"Epol{k}", cursor + depol))
        cursor += ph.delay_after_us
    return out


def expected_epol_times_us(capture: Capture) -> List[Tuple[str, float]]:
    """Return ``(label, time_us)`` for each phase's EXPECTED electrode-
    polarization sample location — ``phase_end + DEPOLARIZATION_TIME_US``
    (12 µs) — but ONLY for phases that have a trailing recovery delay
    (interphase or discharge), where a quiet window actually exists to
    read E_pol.

    Operator: "put a vertical line at each expected location of electrode
    polarization (12 us after the phase) if there are interphase delays
    or discharge delay."  This is a GEOMETRIC reference computed from the
    programmed pattern timing (anchored at the detected pulse onset, like
    every other phase-time chain — gotcha #44), drawn as a vertical guide
    so the operator can eyeball whether the data-driven Emc/Ema marker
    landed where the pattern says it should.  It is especially useful for
    smooth waveforms (sinusoidal / gaussian) where the onset detector can
    skew and the data-driven marker may drift out of the delay window.

    The label mirrors the marker convention: cathodal phase → ``Emc``,
    anodal → ``Ema`` (with a numeric suffix when a polarity repeats).
    """
    from .metrics import pulse_onset_us
    depol = _capture_depol_us(capture)
    # NO guide for a BAD response (broken / open / capacitive).  A bad
    # electrode has NO meaningful electrode polarization, so compute_metrics
    # cleared access/E_pol and compute_metric_markers draws ONLY the bad-class
    # tag (gotcha #58/#86).  This geometric guide, however, is computed from
    # the programmed timing + the detected onset — and on a bad capture (often
    # a low-current, near-noise trace) the onset detector reads noise and
    # drops the "Emc"/"Ema" line into the interpulse, exactly where the
    # electrode polarization is NOT (operator: "even for broken, why are the
    # locations for electrode polarization incorrect?? Why is the marker
    # located in the interpulse").  Suppress it, matching the markers.
    try:
        rc = getattr(capture.metrics, "response_class", "normal")
        if rc and str(rc) != "normal":
            return []
    except Exception:
        pass
    # NO guide at ~0 µA (a baseline capture, e.g. the first capture of a
    # 0 µA-start VT-max ramp): no current is delivered, so there is no E_pol
    # to locate — and the onset detector reads pure noise, placing the guide
    # at a meaningless position (operator: "sinusoidal and gaussian … at
    # 0 µA, the electrode polarization are misplaced").  Same 0.5 µA gate as
    # ``compute_metric_markers``.
    try:
        if abs(float(capture.pattern.excitation_phase.amplitude_ua)) < 0.5:
            return []
    except Exception:
        pass
    phases = list(capture.pattern.phases)
    # Cathodal/anodal label per phase, de-duplicated the same way the
    # Emc/Ema markers are (Emc1/Emc2 only when a polarity repeats).
    pref = ["Ema" if float(getattr(ph, "amplitude_ua", 0.0)) > 0 else "Emc"
            for ph in phases]
    dup = {p: pref.count(p) > 1 for p in set(pref)}
    seen: dict = {}
    tags: List[str] = []
    for p in pref:
        if dup.get(p):
            seen[p] = seen.get(p, 0) + 1
            tags.append(f"{p}{seen[p]}")
        else:
            tags.append(p)
    cursor = pulse_onset_us(capture.time_us, capture.i_mon_ua,
                            capture.v_mon_v)
    out: List[Tuple[str, float]] = []
    for k, ph in enumerate(phases):
        cursor += ph.width_us
        # Only phases with a trailing delay get a guide — that's where a
        # quiet interpulse/interphase window exists to sample E_pol.
        if float(getattr(ph, "delay_after_us", 0.0)) > 0.0:
            out.append((tags[k], float(cursor + depol)))
        cursor += ph.delay_after_us
    return out


def _phase_peak_idx(t, v, base, t0, t1):
    """Index of the PEAK EXCURSION (max |v − base|) within the phase window
    ``[t0, t1]`` — used to place the driving-voltage marker on the actual
    waveform peak rather than at the NOMINAL ``phase_end − 1 µs`` sample
    (operator: "the driving voltage/potentials … are not on the peaks").

    The nominal phase timing is derived from the detected onset + programmed
    widths; an onset-detection skew of even ~1-2 µs slides that sample off
    the real peak — for a cathodic ramp the trough happens to sit at the end
    so it looked fine, but an anodic charge that peaks then rolls into the
    phase transition left the marker on the declining edge.  Finding the
    actual extreme is robust to that skew.  A small leading guard skips the
    IR-step / switching transient at the phase start so a leading overshoot
    can't masquerade as the peak; the trailing transition relaxes TOWARD
    baseline (smaller |excursion|) so it never wins.  Returns ``None`` when
    the window holds no samples."""
    guard = min(2.0, 0.25 * max(t1 - t0, 0.0))
    m = (t >= t0 + guard) & (t <= t1)
    if not np.any(m):
        m = (t >= t0) & (t <= t1)
        if not np.any(m):
            return None
    idx = np.nonzero(m)[0]
    seg = np.abs(v[idx] - base)
    # LAST occurrence of the max excursion: a real charging ramp has a
    # unique peak (so first==last), but a FLAT / settled plateau ties across
    # the whole phase — there the driving voltage is the value at the phase
    # END (the settled level, MATLAB getDriving2.m convention), so prefer
    # the last sample over the first.
    return int(idx[seg.size - 1 - int(np.argmax(seg[::-1]))])


def compute_metric_markers(capture: Capture) -> List[dict]:
    """V_a / V_d / Emc-Ema marker points on the V_mon trace — the SINGLE
    source of truth shared by the live ScopePlot
    (``multichannel_scope._refresh_traces``) and the exported matplotlib
    figure, so the two agree on position, value, and label.

    Each item is a dict ``{kind, label, t_us, y, text}`` (access entries
    also carry ``role`` = ``"lead"``/``"trail"``) where ``kind`` is one of:

      * ``"access"``  — access voltage at EVERY access point, leading AND
        trailing.  The COUNT is delay-dependent (operator: "the number of
        access voltage and resistance depends on the existences of
        interphase delay and discharge delay") — order is
        ``[lead-ph1, trail-ph1, lead-ph2, trail-ph2, …]`` where each
        trail/lead entry is present only where the current actually steps
        to / from zero (interphase delay → trail-ph1 + lead-ph2; discharge
        delay → trail-ph(last)).  Positions come from the DATA-DRIVEN
        ``access_idx`` (the |dV/dt| edge localizer in
        :func:`metrics.access_voltage_and_resistance`), so leading points
        sit just after each phase's IR step and trailing points sit in the
        following delay.  The tag carries V_a AND R_a.
      * ``"driving"`` — V_d, ONE marker at the END of the DRIVING phase
        (largest ``|amplitude_ua|``, ties → first phase, so a symmetric
        biphasic uses phase 1).  Value = V_mon change from the pre-pulse
        baseline (MATLAB ``getDriving2.m``).
      * ``"polar"``   — electrode polarization at ``phase_end + depol``,
        labelled by polarity: cathodic phase → ``Emc``, anodic → ``Ema``
        (MATLAB ``getAcutePlot3.m``).  A phase-number suffix
        (Emc1/Emc2) is added only when the same polarity repeats.

    The polarization + driving chains are anchored at the DETECTED pulse
    onset (``metrics.pulse_onset_us``) — the time axis is trigger-relative,
    so a chain from 0 mis-places every phase under the I_mon trigger.  The
    access markers need no anchor (the |dV/dt| edge localizer is
    data-driven).  Returns ``[]`` when there's no usable V_mon trace /
    pattern.
    """
    from .metrics import (pulse_onset_us, access_voltage_and_resistance,
                          access_index_labels, _despike)
    DEPOLARIZATION_TIME_US = _capture_depol_us(capture)
    out: List[dict] = []
    t = np.asarray(capture.time_us, dtype=float)
    v = (np.asarray(capture.v_mon_v, dtype=float)
         if capture.v_mon_v is not None else np.empty(0))
    phases = list(getattr(capture.pattern, "phases", []) or [])
    if t.size < 2 or v.size != t.size or not phases:
        return out
    # NO metric markers at ~0 µA (a baseline capture, e.g. the first capture of
    # a 0 µA-start VT-max ramp): with no delivered current there is no access
    # step, driving voltage, or polarization to mark — the extraction would
    # otherwise place markers (especially electrode polarization) at
    # meaningless, noise-driven locations (operator: "The experiment plot at
    # 0 µA can have markers, specifically electrode polarization, are at
    # incorrect locations").
    try:
        _amp0 = abs(float(capture.pattern.excitation_phase.amplitude_ua))
    except Exception:
        _amp0 = None
    # …EXCEPT a bad-response (open / broken / capacitive) capture: its single
    # class annotation is a DIAGNOSIS, not an amplitude-dependent measurement,
    # so it must show even below 0.5 µA (operator: an OPEN channel at -0.1 µA
    # showed "Response: OPEN" in the table but NO marker on the plot — the
    # ramp correctly stopped there, and the plot must say WHY).  The bad-class
    # branch below draws only that one marker; the normal access/E_pol/V_d
    # markers stay suppressed at low current.
    _rclass_early = getattr(capture.metrics, "response_class", "normal") or "normal"
    if _amp0 is not None and _amp0 < 0.5 and _rclass_early == "normal":
        return out
    onset = pulse_onset_us(t, capture.i_mon_ua, v)
    pre = v[t < (onset - 1.0)]
    base = float(np.median(pre)) if pre.size >= 4 else 0.0
    # DESPIKED V_mon for marker POSITIONS + values (driving / access /
    # polarization) — rejects switching spikes + ringing so a marker never
    # lands on a transient at small currents (operator: "spikes and ringing
    # … mislead … driving voltage … access voltage").  The access VALUES
    # (text) come from access_voltage_and_resistance, which despikes its own
    # reads; here v_ds is what the glyphs SIT on.
    v_ds = _despike(v, t)

    # ---- Active-electrode source for access V/R + E_pol ------------------
    # Operator: "access voltage, access resistance, and electrode
    # polarization … should be on Eact (based on the active electrode), if
    # the waveform is available."  Access V/R and polarization are properties
    # of the ACTIVE electrode, so when E_act (active vs reference) is recorded
    # we measure + place those markers on it; otherwise V_mon (active vs
    # return) is the proxy.  The DRIVING voltage V_d stays on V_mon (it's the
    # total active-vs-return driving potential, not an active-electrode
    # quantity).
    _ea = (np.asarray(capture.e_act_v, dtype=float)
           if capture.e_act_v is not None else np.empty(0))
    # Derive E_act = V_mon + E_ret when the active electrode wasn't digitised
    # directly but E_ret was — the differential identity (RAW), matching the
    # DERIVED E_act trace the live plot draws (multichannel_scope) AND the
    # active metrics (compute_metrics).  So the markers ride the SAME
    # active-electrode trace the operator sees (operator: "put the experiment
    # plot markers on the Eact waveform when appropriate").
    if _ea.size != t.size and capture.e_ret_v is not None and v.size == t.size:
        _er = np.asarray(capture.e_ret_v, dtype=float)
        if _er.size == t.size:
            _ea = v + _er
    use_eact = _ea.size == t.size and bool(np.any(np.isfinite(_ea)))
    if use_eact:
        v_active = _ea
        _pre_a = v_active[t < (onset - 1.0)]
        base_active = float(np.median(_pre_a)) if _pre_a.size >= 4 else 0.0
        v_ds_active = _despike(v_active, t)
    else:
        v_active, base_active, v_ds_active = v, base, v_ds

    # ---- Bad / degenerate electrode (capacitive / open / broken) ---------
    # The metrics CLEARED the access voltages, polarization, and driving
    # voltage for a non-normal response (gotcha #58 — no Faradaic water
    # window, so those numbers are meaningless).  The plot must agree:
    # DON'T draw access / E_pol / V_d markers (they'd imply a valid
    # measurement on an open channel — operator: "CH03/04/06/09 … clearly
    # open/bad … so capacitive with no resistance").  Show ONLY the
    # effective capacitance + the response class so the plot reads as "bad".
    rclass = getattr(capture.metrics, "response_class", "normal") or "normal"
    if rclass != "normal":
        ceff = getattr(capture.metrics, "effective_capacitance_nf", float("nan"))
        r_k = getattr(capture.metrics, "rc_fit_resistance_kohm", float("nan"))
        tau = getattr(capture.metrics, "rc_fit_tau_us", float("nan"))
        i = _phase_peak_idx(t, v_ds_active, base_active,
                            onset, onset + phases[0].width_us)
        if i is None:
            # Onset detection can miss on a near-noise low-current bad capture;
            # the DIAGNOSIS must still draw, so anchor it mid-record.
            i = int(t.size // 2)
        if i is not None:
            # broken → R ‖ C fit (R, C, τ); open / capacitive → pure C.
            # Variable typography (operator: "the capacitance marker label
            # should be a variable, i.e., italics and normal for subscripts"):
            # ``clauses`` drives marker_label_html (<i>C</i><sub>eff</sub>) and
            # marker_label_mathtext ($C_{\mathrm{eff}}$); the class word rides
            # the ``heading`` line above them.  ``C`` is the EFFECTIVE
            # capacitance (subscript "eff"); ``R``/``τ`` (RC-fit) stay bare
            # italic variables.
            clauses = []
            if np.isfinite(r_k):
                clauses.append(("R", "", f"{r_k:.0f} kΩ"))
            if np.isfinite(ceff):
                # BROKEN → this is the R‖C FIT capacitance, reported as a bare
                # ``C`` alongside R and τ (operator: "for broken, do not call
                # it Ceff").  OPEN / CAPACITIVE → the pure-capacitance
                # effective capacitance from the linear ramp → ``C_eff``.
                _c_sub = "" if rclass == "broken" else "eff"
                clauses.append(("C", _c_sub, f"{ceff:.3g} nF"))
            if np.isfinite(tau):
                clauses.append(("τ", "", f"{tau:.0f} µs"))
            # Plain-text fallback (consumers that don't do typography).
            parts = [f"{(f'{v}_{s}' if s else v)} = {val}"
                     for v, s, val in clauses]
            txt = rclass + (("  ·  " + ", ".join(parts)) if parts else "")
            out.append(dict(kind="badclass", label=rclass, heading=rclass,
                            clauses=clauses, t_us=float(t[i]),
                            y=float(v_ds_active[i]), text=txt))
        # Operator (#2): "if a channel is open, please still put value labels on
        # the plot."  Fall through to the standard access V/R + E_pol + V_d
        # markers below (re-derived from V_mon, so the cleared stored metrics
        # don't matter) so the operator sees the measured values alongside the
        # open/broken annotation — instead of returning only the class tag.
        # (These values ARE meaningful for a "could be broken but still driving
        # current" electrode; for a truly dead one they read near zero, which is
        # itself informative.)

    # ---- Access voltages + resistances: EVERY lead/trail point ----------
    # Recompute on V_mon to get the parallel (va, ra, access_idx) lists —
    # access_idx is the post-IR-step plateau sample index, so the marker
    # lands exactly where the access measurement was taken.
    labels: List = []
    acc_idx: List = []
    try:
        # Data-driven access labels (so a sinusoidal / gaussian vertical IR
        # step contributes an access marker — gotcha #122) — computed ONCE on
        # the active trace and passed to the extractor so the two stay
        # parallel.
        labels = access_index_labels(capture.pattern, time_us=t,
                                     v_trace=v_active, onset_us=onset)
        va_list, ra_list, acc_idx = access_voltage_and_resistance(
            t, v_active, capture.pattern, onset_us=onset, labels=labels)
        for i, idx in enumerate(acc_idx):
            if idx is None or idx < 0 or idx >= v_active.size:
                continue
            va_i = va_list[i] if i < len(va_list) else float("nan")
            if not np.isfinite(va_i):
                continue
            ra_i = ra_list[i] if i < len(ra_list) else float("nan")
            role = labels[i][1] if i < len(labels) else ""
            tag = f"V_a{i + 1}"
            clauses = [("V", f"a{i + 1}", f"{va_i:.3f} V")]
            if np.isfinite(ra_i):
                clauses.append(("R", f"a{i + 1}", f"{ra_i:.1f} kΩ"))
            out.append(dict(kind="access", label=tag,
                            t_us=float(t[idx]), y=float(v_ds_active[idx]),
                            text=_clauses_text(clauses), clauses=clauses,
                            role=role))
    except Exception:
        pass

    # ---- Polarization (Emc/Ema) + driving voltage (V_d) -----------------
    # Polarity prefixes (+ phase-number suffix only when a polarity repeats)
    amps, pref = [], []
    for ph in phases:
        try:
            a = float(ph.amplitude_ua)
        except Exception:
            a = 0.0
        amps.append(abs(a))
        pref.append("Ema" if a > 0 else "Emc")
    dup = {p: pref.count(p) > 1 for p in set(pref)}
    seen: dict = {}
    tags: List[str] = []
    for p in pref:
        if dup.get(p):
            seen[p] = seen.get(p, 0) + 1
            tags.append(f"{p}{seen[p]}")
        else:
            tags.append(p)
    drive_idx = max(range(len(amps)), key=lambda i: amps[i]) if amps else -1
    # The Emc/Ema VALUE is the CANONICAL per-phase E_pol the metrics table +
    # the water-window limit use (operator-spec: time method for delayed
    # phases, driving−leading for delay-less ones) — read it straight from
    # the capture so the on-plot marker can NEVER disagree with them.  For a
    # delayed phase that equals V at phase_end+depol, so the marker still
    # sits on the trace; for a delay-less phase it's the derived value shown
    # at the phase end.  Falls back to the raw trace sample if metrics
    # weren't computed.
    try:
        canon_pol = list(getattr(capture.metrics,
                                 "polarization_per_phase_v", []) or [])
    except Exception:
        canon_pol = []
    # Per-phase TRAILING-access time (the short-delay E_pol marker sits on the
    # post-IR-drop plateau — mirrors the metrics decision).
    _trail_time: dict = {}
    try:
        for _i, _idx in enumerate(acc_idx):
            if (_i < len(labels) and labels[_i][1] == "trail"
                    and _idx is not None and 0 <= int(_idx) < t.size):
                _trail_time[int(labels[_i][0])] = float(t[int(_idx)])
    except Exception:
        pass

    # Were per-phase E_pol metrics pre-computed?  If so, an explicit NaN means
    # UNDETERMINED (phase 2 with no interphase AND no discharge delay) → draw
    # no marker.  If NOT (raw capture without compute_metrics), fall back to the
    # trace value so the marker still appears.
    _metrics_have = len(canon_pol) >= len(phases)
    # ---- CONTINUOUS-SINUSOIDAL (KHFAC): polarization rides the CORRECTED
    # interface trace (E′act), not the raw V_mon (operator: "Be sure that the
    # electrode polarization is placed on the proper trace").  E_mc / E_ma are
    # the min / max of the INTERFACE potential E_i = V_active − R_access·I
    # (the same de-ohm'd waveform drawn as the E′act dashed trace), so their
    # markers sit at argmin / argmax of E_i — where the electrode is most
    # cathodic / anodic — rather than at a sinusoid phase boundary on the raw
    # trace.  The VALUE still comes from the canonical per-phase E_pol (== the
    # Ghazavi E_mc/E_ma), so value and marker agree.
    _khfac_e_i = None
    _khfac_imin = _khfac_imax = None
    try:
        from .metrics import (is_continuous_sinusoidal,
                              ghazavi_corrected_waveforms)
        if (is_continuous_sinusoidal(capture.pattern)
                and capture.i_mon_ua is not None):
            _i_raw = np.asarray(capture.i_mon_ua, dtype=float)
            if _i_raw.size == v_active.size:
                _ei_a, _vr_a, _r_a = ghazavi_corrected_waveforms(
                    v_active, _i_raw)
                if _ei_a is not None and _ei_a.size == t.size:
                    _khfac_e_i = _ei_a
                    _khfac_imin = int(np.nanargmin(_ei_a))
                    _khfac_imax = int(np.nanargmax(_ei_a))
    except Exception:
        _khfac_e_i = None
    cursor = onset
    drive_win = None
    for k, ph in enumerate(phases):
        t0 = cursor
        t_end = cursor + ph.width_us
        cursor = t_end + ph.delay_after_us
        # KHFAC: the Emc/Ema marker rides the INTERFACE extremum (on the E′act
        # corrected trace), NOT a raw-trace phase boundary — a cathodic phase
        # (Emc) → argmin(E_i), an anodic phase (Ema) → argmax(E_i).  The VALUE
        # is the canonical per-phase E_pol (== the Ghazavi E_mc/E_ma), which
        # equals E_i at that extremum, so the label and the marker agree.
        if _khfac_e_i is not None:
            _idx = _khfac_imin if tags[k].startswith("Emc") else _khfac_imax
            _vm = (float(canon_pol[k]) if (k < len(canon_pol)
                                           and np.isfinite(canon_pol[k]))
                   else float(_khfac_e_i[_idx]))
            _sub = tags[k][1:]
            _cl = [("E", _sub, f"{_vm:.3f} V")]
            out.append(dict(kind="polar", label=tags[k],
                            t_us=float(t[_idx]), y=float(_khfac_e_i[_idx]),
                            text=_clauses_text(_cl), clauses=_cl))
            if k == drive_idx:
                drive_win = (t0, t_end)
            continue
        # E_pol marker position mirrors the metrics decision (MATLAB / Cogan
        # 2008): d_k >= depol → phase_end + depol (quiet window); a SHORT delay
        # 0 < d_k < depol → the TRAILING access point; d_k == 0 → the phase end.
        d_k = ph.delay_after_us
        if d_k >= DEPOLARIZATION_TIME_US:
            pos_t = t_end + DEPOLARIZATION_TIME_US
        elif d_k > 0:
            pos_t = _trail_time.get(k, t_end)
        else:
            pos_t = t_end
        v_metric = float(canon_pol[k]) if k < len(canon_pol) else float("nan")
        if (_metrics_have and not np.isfinite(v_metric)) \
                or not (t[0] <= pos_t <= t[-1]):
            if k == drive_idx:
                drive_win = (t0, t_end)
            continue
        i = int(np.clip(np.searchsorted(t, pos_t), 0, v_active.size - 1))
        y_draw = float(v_ds_active[i])       # marker sits on the active trace
        val = v_metric if np.isfinite(v_metric) else y_draw   # fallback
        sub = tags[k][1:]  # strip the leading "E" -> "mc" / "ma" / "mc1"
        clauses = [("E", sub, f"{val:.3f} V")]
        out.append(dict(kind="polar", label=tags[k],
                        t_us=float(pos_t), y=y_draw,
                        text=_clauses_text(clauses), clauses=clauses))
        if k == drive_idx:
            drive_win = (t0, t_end)
    if drive_win is not None:
        ds, de = drive_win
        # On the DESPIKED trace so a switching spike isn't mistaken for the
        # driving peak (operator: "the spike does not mislead … the driving
        # voltage").
        i = _phase_peak_idx(t, v_ds, base, ds, de)
        if i is not None:
            vd = float(v_ds[i]) - base
            clauses = [("V", "d", f"{vd:+.3f} V")]
            out.append(dict(kind="driving", label="Vd",
                            t_us=float(t[i]), y=float(v_ds[i]),
                            text=_clauses_text(clauses), clauses=clauses))

    # KHFAC ACCESS VOLTAGE (operator: "What about access voltage?").  For a
    # continuous sinusoid there is no current-STEP edge, so the pulsed IR-step
    # access marker doesn't apply.  Ghazavi's access voltage is the AMPLITUDE
    # of the RESISTIVE drop V_r = R_access·I (== V_mon − E′act), which is
    # maximal at the CURRENT PEAK — where V_mon and the E′act interface trace
    # are most separated.  Mark it there (hbar glyph, like the pulsed V_a) on
    # the active trace, carrying V_a = V_ro AND R_a = R_access, so the plot and
    # the metric table agree.  The VALUES come straight from the metrics
    # (``ghazavi_v_access_v`` / ``ghazavi_r_access_kohm``).
    if _khfac_e_i is not None and capture.i_mon_ua is not None:
        _va = getattr(capture.metrics, "ghazavi_v_access_v", float("nan"))
        _ra = getattr(capture.metrics, "ghazavi_r_access_kohm", float("nan"))
        _iarr = np.asarray(capture.i_mon_ua, dtype=float)
        if np.isfinite(_va) and _iarr.size == t.size:
            _iac = _iarr - float(np.nanmean(_iarr))
            _ipk = int(np.nanargmax(np.abs(_iac)))       # peak |current|
            _acc_cl = [("V", "a", f"{_va:.3f} V")]
            if np.isfinite(_ra):
                _acc_cl.append(("R", "a", f"{_ra:.1f} kΩ"))
            out.append(dict(kind="access", label="Va",
                            t_us=float(t[_ipk]), y=float(v_ds_active[_ipk]),
                            text=_clauses_text(_acc_cl), clauses=_acc_cl))

    # DISCONTINUOUS SHAPED PULSE access voltage — at PEAK CURRENT (operator:
    # "change the discontinuous gaussian and sinusoidal to peak current because
    # there is no edge").  A smoothly-ramping gaussian / sinusoidal phase has no
    # current-step edge, so the ohmic drop is marked where it's largest — at the
    # phase's peak current — on the active trace (hbar, like the pulsed V_a).  A
    # phase riding a current OFFSET (a real 0→offset edge) is EXCLUDED by the
    # helper (its access is the classical edge measurement, drawn above).  Not
    # drawn for a KHFAC continuous sinusoid (helper self-excludes → the Ghazavi
    # V_a above covers it) or a bad-response capture (early return above).
    try:
        from .metrics import shaped_peak_access
        _shaped = (shaped_peak_access(capture.pattern, t, v_active,
                                      capture.i_mon_ua, onset_us=onset)
                   if capture.i_mon_ua is not None else [])
    except Exception:
        _shaped = []
    for _si, _e in enumerate(_shaped, start=1):
        _pk = int(_e["peak_idx"])
        if not (0 <= _pk < v_ds_active.size):
            continue
        _sc = [("V", "a", f"{float(_e['v_access_v']):.3f} V")]
        _srk = float(_e.get("r_access_kohm", float("nan")))
        if np.isfinite(_srk):
            _sc.append(("R", "a", f"{_srk:.1f} kΩ"))
        _slabel = f"Va{_si}" if len(_shaped) > 1 else "Va"
        out.append(dict(kind="access", label=_slabel,
                        t_us=float(t[_pk]), y=float(v_ds_active[_pk]),
                        text=_clauses_text(_sc), clauses=_sc))

    # SUBSEQUENT-phase driving voltage (operator: "also determine subsequent
    # driving voltage of other phases if there is an interphase delay that
    # precedes.  Use the ending interphase voltage of the voltage monitor in
    # calculating the voltage").  For each phase (other than the primary V_d
    # phase) that is PRECEDED BY AN INTERPHASE DELAY, draw a driving marker
    # whose REFERENCE is the ENDING-INTERPHASE V_mon (the recovered level at
    # the end of the preceding gap), not the pre-pulse baseline — matching
    # ``metrics._driving_voltage_per_phase``.  This is a deliberate, scoped
    # revival of the "other driving potentials" that gotcha #68 had removed;
    # it ONLY fires where an interphase delay actually precedes the phase, so
    # a delay-less biphasic still shows just the single V_d.  Uses V_mon
    # (``v_ds``) per the operator's explicit "voltage monitor".
    cursor2 = onset
    for k, ph in enumerate(phases):
        t0 = cursor2
        t_end = cursor2 + ph.width_us
        prior_delay = phases[k - 1].delay_after_us if k > 0 else 0.0
        cursor2 = t_end + ph.delay_after_us
        if k == 0 or prior_delay <= 0 or k == drive_idx:
            continue
        # Reference = V_mon at the END of the preceding interphase delay
        # (~1 µs before this phase starts, after the current has recovered).
        ref_t = t0 - 1.0
        if not (t[0] <= ref_t <= t[-1] and t[0] <= t_end <= t[-1]):
            continue
        ref_idx = int(np.clip(np.searchsorted(t, ref_t), 0, v_ds.size - 1))
        ref_val = float(v_ds[ref_idx])
        i = _phase_peak_idx(t, v_ds, ref_val, t0, t_end)
        if i is None:
            continue
        vd_k = float(v_ds[i]) - ref_val
        clauses = [("V", f"d{k + 1}", f"{vd_k:+.3f} V")]
        out.append(dict(kind="driving", label=f"Vd{k + 1}",
                        t_us=float(t[i]), y=float(v_ds[i]),
                        text=_clauses_text(clauses), clauses=clauses))

    # (REMOVED, operator request:
    #   * the ending-interphase-potential marker — "remove the ending
    #     interphase potential" (empty-circle → filled-circle → gone).
    #   The "other driving potentials" were re-added ABOVE but ONLY for
    #   phases preceded by an interphase delay, referenced to the ending-
    #   interphase V_mon — the single unconditional V_d is still the primary
    #   marker.  Don't re-add a ``kind="interphase"`` marker.)

    # ---- Cull near-zero markers on a low-signal (normal) capture ----------
    # On a few-µA "normal" capture (operator: CH03 at -3.8 µA, ±5 mV V_mon) the
    # access V_a / R_a, E_pol, and V_d ALL round to 0 at the DISPLAYED precision
    # — a dense pile of "V_a1 = 0.000 V / R_a1 = 0.0 kΩ / Emc = 0.000 V /
    # Vd = +0.000 V" labels that carry no information and overlap on the noise
    # floor ("Markers and label are poorly placed").  Each marker's ``clauses``
    # values are ALREADY formatted at display precision, so parsing them IS the
    # round-to-precision test.  Drop a marker only when EVERY clause rounds to
    # zero — never a large-signal capture (any non-zero clause keeps it), and
    # never the badclass diagnosis (it already returned above; guard defensive).
    def _all_clauses_zero(mk) -> bool:
        cls = mk.get("clauses") or []
        if not cls:
            return False
        for _var, _sub, _val in cls:
            _m = re.match(r"[+-]?\d*\.?\d+", str(_val).strip())
            if _m is None or abs(float(_m.group(0))) > 0.0:
                return False
        return True
    # GATE the cull on a genuinely LOW-SIGNAL capture (V_mon excursion below the
    # display/classifier floor).  A large-signal capture keeps EVERY marker even
    # if one value happens to round to zero (e.g. a settled interphase E_pol),
    # so this only strips the noise-floor pile-up on a few-µA capture.
    _v_peak = float(np.max(np.abs(v_ds - base))) if v_ds.size else 0.0
    if _v_peak < _MARKER_CULL_SIGNAL_FLOOR_V:
        out = [mk for mk in out
               if mk.get("kind") == "badclass" or not _all_clauses_zero(mk)]
    return out


# ---------------------------------------------------------------------------
# Marker label formatting — proper variable typography (operator: "Use
# proper variable formatting on the plot markers" — italic variable,
# upright subscript, matching MATLAB ``{\itV}_{acc}``).  ``clauses`` is a
# list of ``(var, sub, value)`` tuples; a marker can have more than one
# (e.g. access shows V_a AND R_a).
# ---------------------------------------------------------------------------
def _clauses_text(clauses) -> str:
    """Plain-text fallback — one clause per line (e.g. V_a then R_a)::

        V_a1 = 0.300 V
        R_a1 = 3.8 kΩ
    """
    return "\n".join(f"{(f'{var}_{sub}' if sub else var)} = {val}"
                     for var, sub, val in clauses)


def marker_label_html(marker: dict) -> Optional[str]:
    """HTML label for the live pyqtgraph plot — ``<i>V</i><sub>a1</sub> =
    0.300 V`` (italic variable, upright subscript).  Each clause is on its
    OWN LINE (``<br/>``) so the access RESISTANCE sits on a second line
    under V_a (operator: "Make the access resistance as a second line") —
    this also halves the tag's horizontal extent, easing intersection.
    Returns ``None`` when the marker carries no structured ``clauses``
    (caller falls back to the plain ``text``)."""
    from .gui.rich import var as _rv
    clauses = marker.get("clauses")
    if not clauses:
        return None
    lines = [f"{(_rv(v, s) if s else _rv(v))} = {val}" for v, s, val in clauses]
    heading = marker.get("heading")   # e.g. a bad-channel class word
    if heading:
        lines.insert(0, str(heading))
    return "<br/>".join(lines)


#: Greek variable letters → their mathtext command (a bare Unicode ``τ`` inside
#: ``$…$`` doesn't render reliably across matplotlib mathtext fonts, so map to
#: ``\tau``).  HTML uses the Unicode directly (``<i>τ</i>`` italicises fine).
_GREEK_MATHTEXT = {"τ": r"\tau", "μ": r"\mu", "µ": r"\mu", "Ω": r"\Omega"}


def marker_label_mathtext(marker: dict) -> str:
    """matplotlib mathtext label — ``$V_\\mathrm{a1}$ = 0.300 V`` (italic
    variable via math mode, upright subscript via ``\\mathrm``), one clause
    per line, with an optional plain ``heading`` line on top.  Greek variable
    letters are mapped to their mathtext command.  Falls back to the plain
    ``text`` when there are no ``clauses``."""
    clauses = marker.get("clauses")
    if not clauses:
        return marker.get("text", "")

    def _tex(v):
        return _GREEK_MATHTEXT.get(v, v)
    lines = [
        f"{(f'${_tex(v)}_{{\\mathrm{{{s}}}}}$' if s else f'${_tex(v)}$')} = {val}"
        for v, s, val in clauses]
    heading = marker.get("heading")
    if heading:
        lines.insert(0, str(heading))
    return "\n".join(lines)


#: Pulse-framing margins — preceding interpulse SMALLER than (or equal
#: to) the proceeding one, per operator preference.  Same values as the
#: live ScopePlot framing.
_PULSE_PRE_MARGIN_FRAC = 0.15
_PULSE_POST_MARGIN_FRAC = 0.55


def _pulse_span(capture: Capture):
    """Raw ``(t0, t1)`` µs of the active pulse (first→last sample whose
    |signal − median| clears 10 % of the robust p2p), from I_mon then
    V_mon; ``None`` when no pulse is detectable.  Shared by the framing
    window and the export's begin/end-on-a-tick X snap."""
    t = np.asarray(capture.time_us, dtype=float)
    if t.size < 8:
        return None
    for arr in (capture.i_mon_ua, capture.v_mon_v):
        if arr is None:
            continue
        a = np.asarray(arr, dtype=float)
        if a.size != t.size:
            continue
        p2p = float(np.percentile(a, 99.0) - np.percentile(a, 1.0))
        if not np.isfinite(p2p) or p2p <= 0.0:
            continue
        med = float(np.median(a))
        idx = np.flatnonzero(np.abs(a - med) > 0.10 * p2p)
        if not idx.size:
            continue
        return float(t[idx[0]]), float(t[idx[-1]])
    return None


def _pulse_xlim(capture: Capture):
    """X-window framing the active pulse with pre ≤ post interpulse.

    Returns ``(x_min, x_max)`` µs clamped to the data extent, or ``None``
    when no pulse is detectable (full-extent fallback).
    """
    sp = _pulse_span(capture)
    if sp is None:
        return None
    t = np.asarray(capture.time_us, dtype=float)
    t0, t1 = sp
    w = max(t1 - t0, 1e-9)
    x_min = max(float(t[0]), t0 - _PULSE_PRE_MARGIN_FRAC * w)
    x_max = min(float(t[-1]), t1 + _PULSE_POST_MARGIN_FRAC * w)
    if x_max > x_min:
        return (x_min, x_max)
    return None


def _channel_label(name: str) -> str:
    """Per-trace legend / axis label, with units in parentheses."""
    base = {
        "v_mon": "Voltage (V)",
        "i_mon": "Current Density (A/cm²)",
        "e_act": "Active Potential (V)",
        "e_ret": "Return Potential (V)",
    }
    return base.get(name, name)


def qinj_use_micro(q_inj_mc: float) -> bool:
    """True when Q_inj (mC/cm²) would round to 0.000 mC at 3 decimals — the
    heading should show µC/cm² instead (operator: "if the charge injection
    capacity is 0 mC/cm2 in the subtitle due to low precision, then use
    uC/cm2").  1 mC = 1000 µC.  Shared by the exported subtitle
    (``_capture_subtitle_mathtext``) and the live title (multichannel_scope)."""
    try:
        q = float(q_inj_mc)
    except (TypeError, ValueError):
        return False
    return bool(np.isfinite(q) and q != 0.0 and abs(q) < 5e-4)


def _capture_subtitle_mathtext(capture: Capture, run: ChannelRun,
                               lead: str = "") -> str:
    """Exported-plot subtitle mirroring the live experiment-plot heading
    (operator: "have the same subtitle as the experiment plot, e.g., Istim,
    Jstim, Qph, Qinj, etc").

    Same fields, same order as ``_ChannelPage`` (gui/multichannel_scope.py):
    ``I_stim`` (signed µA) · ``J_stim`` (A/cm², only when a surface area is
    set) · ``Q_ph`` (nC) · ``Q_inj`` (mC/cm²) · capture N.  Rendered in
    matplotlib mathtext (italic variable, upright subscript).  ``lead`` (the
    CHANNEL / config name) leads the line — the SUBJECT is the bold title above
    (operator: "move the name as title and channel as subtitle").
    """
    parts: List[str] = []
    if lead:
        parts.append(str(lead))
    try:
        amp_ua = float(capture.pattern.excitation_phase.amplitude_ua)
    except Exception:
        amp_ua = None
    if amp_ua is not None:
        parts.append(r"$I_{\mathrm{stim}}$ = %+.1f µA" % amp_ua)
        try:
            area_um2 = float(run.surface_area_um2) if run.surface_area_um2 else 0.0
        except (TypeError, ValueError):
            area_um2 = 0.0
        if area_um2 > 0:
            # J_stim = I_stim / A: amp_ua·1e-6 A / (area_um2·1e-8 cm²)
            #        = amp_ua / area_um2 × 100  A/cm²  (carries I_stim's sign).
            j = amp_ua / area_um2 * 100.0
            parts.append(r"$J_{\mathrm{stim}}$ = %+.2f A/cm$^2$" % j)
    try:
        q_ph = float(capture.metrics.charge_per_phase_nc)
    except Exception:
        q_ph = float("nan")
    try:
        q_inj = float(capture.metrics.charge_injection_mc_per_cm2)
    except Exception:
        q_inj = float("nan")
    if np.isfinite(q_ph):
        parts.append(r"$Q_{\mathrm{ph}}$ = %.2f nC" % q_ph)
    if np.isfinite(q_inj):
        if qinj_use_micro(q_inj):
            parts.append(r"$Q_{\mathrm{inj}}$ = %.3f µC/cm$^2$" % (q_inj * 1e3))
        else:
            parts.append(r"$Q_{\mathrm{inj}}$ = %.3f mC/cm$^2$" % q_inj)
    try:
        parts.append("capture %d" % (int(capture.index) + 1))
    except Exception:
        pass
    return "  ·  ".join(parts)


# ---------------------------------------------------------------------------
# Core plot
# ---------------------------------------------------------------------------
def plot_capture(capture: Capture, run: ChannelRun, session: Session,
                 *, fig: Optional[Figure] = None,
                 show_cursors: bool = True,
                 show_minmax: bool = False,
                 show_grid: bool = True,
                 density: bool = True,
                 deriv_overlays: Optional[set] = None,
                 potential_axis: bool = False,
                 return_axis: bool = False) -> Figure:
    """Render one capture on a matplotlib Figure.

    Layout matches ``getPlot_Tek.m``:
      * Left axis: V_mon / E_act / E_ret traces (volts).
      * Right axis: current density (A/cm²) when ``density`` (default, the
        MATLAB-faithful saved-figure convention), else raw current (µA).
        POLARIS passes ``density=False`` so its default right axis is
        "Current (µA)" — NOT "Current monitor" (operator).
      * Title = session subject; subtitle = ``CH active v returns @ amp µA``.

    ``show_minmax`` is retained for back-compat but is now IGNORED — the
    operator removed the two dashed right-axis min/max reference rails.
    """
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    ax_v = fig.add_subplot(111)
    ax_i = ax_v.twinx()
    # Draw the current-density (right-axis) trace BEHIND the voltage trace
    # (operator: "the current (density) plot should be in the back").  For
    # twinned axes matplotlib draws the SECOND axis (ax_i) ON TOP by
    # default; raise ax_v above ax_i and make ax_v's background
    # transparent so the behind-axis (ax_i) shows through.
    ax_v.set_zorder(ax_i.get_zorder() + 1)
    ax_v.patch.set_visible(False)

    time_us = np.asarray(capture.time_us)
    voltage_traces: List[Tuple[str, np.ndarray, str]] = []
    if capture.v_mon_v is not None and capture.v_mon_v.size:
        # Legend label spelled out (operator: "indicate the voltage in
        # the legend as voltage monitor") — V_mon IS the voltage monitor.
        voltage_traces.append(
            ("Voltage monitor", capture.v_mon_v, _EXP_TRACE_COLORS[0]))
    if capture.e_act_v is not None and capture.e_act_v.size:
        # "Active potential" / "Return potential" (operator: label the
        # E_act / E_ret traces as potentials).
        voltage_traces.append(
            ("Active potential", capture.e_act_v, _EXP_TRACE_COLORS[2]))
    elif (capture.e_ret_v is not None and capture.e_ret_v.size
          and capture.v_mon_v is not None
          and capture.v_mon_v.size == capture.e_ret_v.size):
        # DERIVED (calculated) E_act = V_mon + E_ret via the differential
        # identity ``V_mon = E_act − E_ret`` — the SAME trace the live plot
        # draws when the active electrode wasn't digitised directly but E_ret
        # was (operator: "when exporting the plot, include the calculated
        # Eact, if available").  Labelled "(calculated)" so the saved figure
        # is self-documenting.  Raw V_mon + raw E_ret (no subtraction),
        # consistent with every other trace.
        _e_act_calc = (np.asarray(capture.v_mon_v, dtype=float)
                       + np.asarray(capture.e_ret_v, dtype=float))
        voltage_traces.append(
            ("Active potential (calculated)", _e_act_calc, _EXP_TRACE_COLORS[2]))
    if capture.e_ret_v is not None and capture.e_ret_v.size:
        voltage_traces.append(
            ("Return potential", capture.e_ret_v, _EXP_TRACE_COLORS[3]))

    for name, y, color in voltage_traces:
        ax_v.plot(time_us, y, color=color, linewidth=1.4, label=name)

    # Ghazavi ACCESS-RESISTANCE-CORRECTED interface waveforms (E′act / E′ret)
    # for a continuous-sinusoidal (KHFAC) capture — the de-ohm'd interface
    # potentials (measured − R_access·I), DASHED in the base electrode's colour
    # (operator: "Plot the corrected waveforms like how Ghazavi did … Ei be
    # E'act and E'ret for correcting for access resistance").  Mirrors the live
    # experiment plot so the saved figure and the on-screen view never drift.
    try:
        from .metrics import (is_continuous_sinusoidal,
                              ghazavi_corrected_waveforms)
        _khfac = (getattr(capture, "pattern", None) is not None
                  and is_continuous_sinusoidal(capture.pattern))
    except Exception:
        _khfac = False
    if _khfac and capture.i_mon_ua is not None and capture.i_mon_ua.size:
        _i_raw = np.asarray(capture.i_mon_ua, dtype=float)
        _n = _i_raw.size
        # E′act — from the ACTIVE trace (recorded E_act, else derived
        # V_mon+E_ret, else V_mon as the active-vs-return proxy).
        _active = None
        if capture.e_act_v is not None and capture.e_act_v.size == _n:
            _active = np.asarray(capture.e_act_v, dtype=float)
        elif (capture.e_ret_v is not None and capture.e_ret_v.size == _n
              and capture.v_mon_v is not None and capture.v_mon_v.size == _n):
            _active = (np.asarray(capture.v_mon_v, dtype=float)
                       + np.asarray(capture.e_ret_v, dtype=float))
        elif capture.v_mon_v is not None and capture.v_mon_v.size == _n:
            _active = np.asarray(capture.v_mon_v, dtype=float)
        if _active is not None:
            _ei, _vr, _r = ghazavi_corrected_waveforms(_active, _i_raw)
            if _ei is not None:
                ax_v.plot(time_us, _ei, color=_EXP_TRACE_COLORS[2],
                          linewidth=1.4, linestyle="--",
                          label=r"Active interface ($E'_{\mathrm{act}}$)")
        # E′ret — from the RETURN trace (recorded E_ret only).
        if capture.e_ret_v is not None and capture.e_ret_v.size == _n:
            _ei_r, _vr_r, _r_r = ghazavi_corrected_waveforms(
                np.asarray(capture.e_ret_v, dtype=float), _i_raw)
            if _ei_r is not None:
                ax_v.plot(time_us, _ei_r, color=_EXP_TRACE_COLORS[3],
                          linewidth=1.4, linestyle="--",
                          label=r"Return interface ($E'_{\mathrm{ret}}$)")

    # Derivative overlays (Harris 2019) — dV/dt and/or 1/(dV/dt) drawn as
    # NORMALIZED dashed overlays on the voltage axis (operator: "derivative and
    # reciprocal of derivative as option traces … normalized overlay"), scaled to
    # the V_mon amplitude (shape only; their true V/µs and huge-reciprocal scales
    # don't fit the voltage axis).  Drawn only when requested via ``deriv_overlays``
    # (a subset of {"dvdt", "recip"}).
    _dov = set(deriv_overlays or ())
    if _dov:
        _vref = None
        if capture.v_mon_v is not None and capture.v_mon_v.size == time_us.size:
            _vref = np.asarray(capture.v_mon_v, dtype=float)
        elif capture.e_act_v is not None and capture.e_act_v.size == time_us.size:
            _vref = np.asarray(capture.e_act_v, dtype=float)
        if _vref is not None:
            try:
                from .metrics import charge_transfer_dedt
                _tt, _dedt = charge_transfer_dedt(
                    time_us, _vref, capture.pattern, onset_us=0.0)
            except Exception:
                _dedt = None
            if _dedt is not None:
                _rmax = float(np.nanmax(np.abs(_vref - np.nanmedian(_vref)))) or 1.0

                def _norm_ov(d):
                    d = np.asarray(d, dtype=float)
                    dc = d - np.nanmedian(d)
                    dmx = float(np.nanmax(np.abs(dc)))
                    return dc / dmx * _rmax if dmx > 1e-30 else None
                if "dvdt" in _dov:
                    _o = _norm_ov(_dedt)
                    if _o is not None:
                        ax_v.plot(time_us, _o, color="#7E2F8E", linewidth=1.1,
                                  linestyle="--", label=r"d$V$/d$t$ (norm.)")
                if "recip" in _dov:
                    _recip = 1.0 / np.where(np.abs(_dedt) < 1e-9, np.nan, _dedt)
                    _o = _norm_ov(_recip)
                    if _o is not None:
                        ax_v.plot(time_us, _o, color="#CC79A7", linewidth=1.0,
                                  linestyle=":", label=r"1/(d$V$/d$t$) (norm.)")

    # Current (or current density) on the right axis (MATLAB orange).
    # ``density`` selects the representation: A/cm² (saved-figure default)
    # vs raw µA (POLARIS default).  ``right_y`` is reused below for the
    # marker-avoidance mapping so the two never diverge.
    right_y = None
    if capture.i_mon_ua is not None and capture.i_mon_ua.size:
        if density:
            area_cm2 = max(run.surface_area_um2 * 1e-8, 1e-12)
            right_y = capture.i_mon_ua * 1e-6 / area_cm2
            right_label = "Current Density (A/cm²)"
            right_legend = "Current Density"
        else:
            right_y = np.asarray(capture.i_mon_ua, dtype=float)
            # NOT "Current monitor" (operator: "The capture view in POLARIS
            # should not be called 'Current monitor'").
            right_label = "Current (µA)"
            right_legend = "Current"
        ax_i.plot(time_us, right_y,
                  color=_EXP_TRACE_COLORS[1], linewidth=1.4,
                  label=right_legend)
        # (No min/max reference rails — operator removed the two dashed
        # right-axis horizontal lines + A/cm² labels; the current
        # extremes are already readable from the trace itself.)

    # X / Y limits are set HERE — BEFORE placing the marker labels — so
    # the candidate-scoring placer knows the final data bounds (it scores
    # against the axes edges to keep labels off the spines).  X frames the
    # pulse with a smaller (or equal) preceding interpulse than proceeding
    # (operator preference — same framing the live ScopePlot uses); falls
    # back to the full data extent when no pulse is detectable.  Y is
    # symmetric like MATLAB, with EXTRA headroom (0.18) so the metric
    # labels have room to sit outside the trace envelope.
    if time_us.size:
        # X range per MATLAB getAcutePlot3.m — round the FULL capture time
        # extent to 1 sig fig — made tick-complete (begin/end on a tick +
        # a tick before x = 0).  See _matlab_x_limits.
        _xt = _matlab_x_limits(time_us, target=7)
        if _xt is not None:
            _xticks, _xmn, _xmx = _xt
            ax_v.set_xticks(_xticks)
            ax_v.set_xlim(_xmn, _xmx)
    # margin=0 — matplotlib's autoscale already padded the data ~5 %, and
    # the begin/end-on-a-tick snap-up in _symmetric_ylim adds the rest of
    # the marker-label headroom, so no extra fractional margin is needed.
    _symmetric_ylim(ax_v, margin=0.0)
    _symmetric_ylim(ax_i, margin=0.0)

    # Metric cursors on the V_mon trace — V_a / V_d (horizontal-bar
    # glyph, marker "_") and Emc/Ema electrode polarization ("+" glyph),
    # from the SHARED picker (compute_metric_markers) so the saved figure
    # matches the live experiment plot (operator: "For access and driving
    # voltage plotting, use a horizontal bar symbol. For electrode
    # polarization, use plus symbols, and indicate if Emc or Ema").
    if show_cursors and voltage_traces:
        # Per-kind colourblind-safe glyph + label colours (operator: "vary the
        # color of the markers and respective label, colorblind safe") — the
        # glyph SHAPE still distinguishes the kind, colour adds redundancy.
        # SHARED with the live plot via ``MARKER_COLOURS``.
        _mpl_style = {
            "access":        (MARKER_COLOURS["access"], "_"),
            # V_d = PLUS, matching the live plot (operator: "Change the
            # driving voltage marker symbol as a plus instead of a
            # horizontal bar").
            "driving":       (MARKER_COLOURS["driving"], "+"),
            "driving_other": (MARKER_COLOURS["driving_other"], "_"),  # bare bar
            "polar":         (MARKER_COLOURS["polar"], "+"),
            "interphase":    (MARKER_COLOURS["interphase"], "o"),     # no label
            "badclass":      (MARKER_COLOURS["badclass"], "x"),
        }
        _vtr = np.asarray(voltage_traces[0][1], dtype=float)
        _tt = np.asarray(time_us, dtype=float)
        _x_span = max(float(_tt[-1] - _tt[0]), 1e-9)
        _x_win = 0.04 * _x_span

        def _env(xc):
            m = (_tt >= xc - _x_win) & (_tt <= xc + _x_win)
            if not np.any(m):
                return None, None
            seg = _vtr[m]
            return float(np.min(seg)), float(np.max(seg))

        _mk_sorted = sorted(
            (m for m in compute_metric_markers(capture)
             if _tt[0] <= m["t_us"] <= _tt[-1]),
            key=lambda m: m["t_us"])
        # Draw every glyph; collect the LABELLED ones for the placer.
        _label_reqs = []
        for _mk in _mk_sorted:
            t_us = _mk["t_us"]
            my = _mk["y"]
            color, marker = _mpl_style.get(_mk["kind"], ("0.4", "x"))
            if marker == "o":
                # Small CLOSED (filled) circle for the ending interphase
                # potential (operator: "change the marker for ending
                # interphase potential as a smaller closed circle") —
                # filled, and smaller than the metric glyphs.
                ax_v.scatter([t_us], [my], marker="o", s=9,
                             facecolors=color, edgecolors=color,
                             linewidths=1, zorder=10)
            else:
                # Smaller glyphs (operator: "make the markers smaller") — was
                # s=90 / lw=2.
                ax_v.scatter([t_us], [my], marker=marker, s=42,
                             color=color, linewidths=1.4, zorder=10)
            if _mk.get("no_label"):
                continue                 # glyph only — no annotation
            lo, hi = _env(t_us)
            if lo is None:
                lo = hi = my
            prefer_below = (my - lo) <= (hi - my)
            # Anchor at the marker GLYPH (not the trace envelope edge) so the
            # proximity term measures distance to the glyph and keeps the tag
            # NEAR it — matches the live ScopePlot placer (operator: "make
            # sure the labels are near their markers … Emc … very far away").
            # The trace-band penalty in the scorer still pushes the box off
            # the waveform; up/down then follows the OPEN side, flipping
            # automatically when polarity flips.
            _label_reqs.append(dict(
                x=t_us, y=my,
                text=marker_label_mathtext(_mk),
                color=color,                     # label matches its glyph
                prefer_below=prefer_below))
        # Candidate-scoring placement: avoids label↔label, label↔trace,
        # and label↔axis collisions (operator: "the labels are
        # intersecting with each other, the plot, and the axis").  The
        # avoid set includes the left-axis voltage trace(s) AND the
        # right-axis current density mapped into left-axis coordinates
        # (zeros are aligned and both axes are symmetric about 0 after
        # _symmetric_ylim, so the map is a simple half-range ratio).
        _avoid = [(_tt, np.asarray(yt, dtype=float))
                  for (_nm, yt, _c) in voltage_traces]
        if right_y is not None:
            _bigL = max(abs(v) for v in ax_v.get_ylim()) or 1.0
            _bigR = max(abs(v) for v in ax_i.get_ylim()) or 1.0
            _avoid.append((_tt, np.asarray(right_y, dtype=float)
                           * (_bigL / _bigR)))
        _place_marker_labels_mpl(ax_v, fig, _label_reqs, _avoid,
                                 fontsize=9.0)

    # ---- Axes / cosmetics ----
    # Axis TITLES, spines (the "box"), tick marks AND tick NUMBERS are all
    # BLACK (operator: "the axis, box, and numbering must be black") — only
    # the TRACES carry colour; the axis titles + legend disambiguate which
    # trace uses which scale.  Matches MATLAB's black-axes / coloured-lines
    # convention.  ``labelpad`` is the SAME on both y-axes so the gap between
    # the tick numbers and the axis title matches left-vs-right (operator:
    # "the space between the number and right y axis title must match the
    # left y axis" — the right was previously labelpad=18 vs the left's ~4).
    _YLABEL_PAD = 6
    ax_v.set_xlabel("Time (µs)", fontsize=14)
    # Left-axis title via the SHARED helper (single source of truth with the
    # live plot + overlay).  Normally "Voltage (V)"; "Potential vs <ref> (V)"
    # when only electrode potentials are on the axis + that option is on;
    # "Voltage vs <return> (V)" when only V_mon is on the axis + that option
    # is on (operator: V_mon is the active-vs-return driving voltage; E_act /
    # E_ret are potentials vs the reference).  Parentheses match this figure's
    # own "(V)" convention.
    _cap_waves: set = set()
    if any(n.startswith("Voltage") for n, _, _ in voltage_traces):
        _cap_waves.add("V_mon")
    if any("potential" in n.lower() for n, _, _ in voltage_traces):
        _cap_waves.add("E_act")     # any potential trace marks it "potential"
    _test = getattr(session, "test", None)
    # A device with NO electrodes (Plexon Test Board) has no reference / return
    # electrode, so the voltage axis stays plain "Voltage" — never the
    # reference-aware relabel (operator: "If the Test Board is connected, the
    # unit for the voltage channels can only be Voltage [V]").  The flag rides
    # the setup snapshot stamped into the session at run start.
    _snap = (getattr(_test, "extras", None) or {}).get("setup_snapshot") or {}
    _has_elec = bool(_snap.get("has_electrodes", True))
    ax_v.set_ylabel(
        _voltage_axis_label(
            _cap_waves, brackets=False,
            potential_axis=potential_axis and _has_elec,
            reference_label=getattr(_test, "reference_electrode_label", None),
            return_axis=return_axis and _has_elec,
            return_label=getattr(_test, "counter_electrode_label", None)),
        color="black", fontsize=14, labelpad=_YLABEL_PAD)
    # Right-axis label follows the density toggle ("Current Density (A/cm²)"
    # vs "Current monitor (µA)"); only shown when a current trace exists.
    _right_label = (right_label if right_y is not None
                    else ("Current Density (A/cm²)" if density
                          else "Current (µA)"))
    ax_i.set_ylabel(_right_label,
                    color="black", fontsize=14, rotation=-90,
                    labelpad=_YLABEL_PAD, va="bottom")
    ax_v.tick_params(axis="both", labelsize=12, colors="black")
    ax_i.tick_params(axis="y", labelsize=12, colors="black")
    for _sp in ax_v.spines.values():
        _sp.set_color("black")
    for _sp in ax_i.spines.values():
        _sp.set_color("black")

    # (X / Y limits were set earlier, before marker-label placement.)
    _grid(ax_v, show_grid)
    # (No x=0 or y=0 reference lines — operator removed both.)

    # Title + subtitle.  The BIG title is THIS run's channel/config —
    # one exported figure per channel must be titled by ITS channel
    # (operator: "each TIFF file is titled CH01" — session.subject was
    # the FIRST config's name, so every plot carried "CH01").  The
    # session identity moves to the subtitle.
    subject = session.subject or session.name
    amp = capture.pattern.excitation_phase.amplitude_ua
    config_name = run.configuration.display_name()
    # Subtitle mirrors the LIVE experiment-plot heading (operator: "have the
    # same subtitle as the experiment plot, e.g., Istim, Jstim, Qph, Qinj,
    # etc") — the same fields ``_ChannelPage`` builds in
    # gui/multichannel_scope.py, rendered in matplotlib mathtext (italic
    # variable, upright subscript).  Capture number is 1-BASED for the
    # operator-facing label.  The subject leads (identity — the config is
    # already the bold title above).
    # TITLE = subject (the electrode/session NAME); SUBTITLE = channel/config
    # + the metric fields (operator: "move the name as title and channel as
    # subtitle").  So the subtitle LEADS with ``config_name``.
    cap_label = _capture_subtitle_mathtext(capture, run, config_name)
    # Title + subtitle as a TIGHT two-line block at the very top (operator:
    # "remove the space between the title and subtitle").  Both are
    # ``fig.text`` with ``va="top"`` so the y is the TOP of each line and the
    # gap between them is controlled directly — the old suptitle (y=0.975) +
    # axes-title (~0.948) pairing left a ~0.027-figure-fraction gap that read
    # as a wide band.  Placed AFTER tight_layout so the reserved top band
    # (rect top below) doesn't get reclaimed.
    _title_y, _subtitle_y = 0.985, 0.952

    # Legend OUTSIDE the data area, centred below the plot (operator: "The
    # legend itself is intersecting with the plot").  ``loc="best"`` put it
    # over the rising trace; a figure-level legend in the reserved bottom
    # margin can never overlap the waveform AND survives both the
    # bbox_inches="tight" export and the interactive POLARIS viewer (which
    # has no tight-bbox crop — so the space MUST be reserved via the
    # tight_layout rect, not just clipped in on save).
    h_v, l_v = ax_v.get_legend_handles_labels()
    h_i, l_i = ax_i.get_legend_handles_labels()
    handles = h_v + h_i
    labels = l_v + l_i
    # Reserve a BOTTOM BAND for the legend that's BELOW the axes — the axes
    # (and the right-axis "Current Density" title, which lives inside the
    # axes area) end at the rect's bottom (0.12), and the legend sits in the
    # 0-0.12 band beneath it, so it can NEVER overlap the right-axis label
    # (operator: "the legend is still overlapping with the right y axis
    # label.  Have the legend outside the figure").  The reserve must be in
    # the tight_layout rect (not just a clipped save) because the POLARIS
    # viewer doesn't crop with bbox_inches="tight".
    fig.tight_layout(rect=(0, 0.12, 1, 0.935))
    # Two-line title block, placed AFTER tight_layout (absolute fig coords,
    # va="top" so the gap between the lines is exactly _title_y - _subtitle_y
    # minus the title's height — a tight single block).
    fig.text(0.5, _title_y, subject, ha="center", va="top",
             fontsize=14, fontweight="bold")
    fig.text(0.5, _subtitle_y, cap_label, ha="center", va="top",
             fontsize=11, color="0.35")
    if handles:
        # Anchor the legend's TOP at the band ceiling (y=0.11, just under the
        # axes) so it grows DOWNWARD into the reserved band, never up into
        # the axes / right-axis label.
        fig.legend(handles, labels, loc="upper center",
                   bbox_to_anchor=(0.5, 0.11), ncol=min(len(handles), 4),
                   fontsize=11, framealpha=0.9, frameon=True)
    return fig


# ---------------------------------------------------------------------------
# Multi-channel waveform overlay
# ---------------------------------------------------------------------------
# Names exactly as they should appear in the toggle UI and plot legend.
# Mapped to the corresponding ``Capture`` attribute so the plotter can
# fetch each trace generically.
WAVE_TYPES = ("V_mon", "I_mon", "E_act", "E_ret")
_WAVE_ATTR = {
    "V_mon": "v_mon_v",
    "I_mon": "i_mon_ua",
    "E_act": "e_act_v",
    "E_ret": "e_ret_v",
}


def plot_picoscope(rec, *, fig: Optional[Figure] = None,
                   enabled=None, show_grid: bool = True) -> Figure:
    """Plot a :class:`persistence.PicoScopeRecording` — raw channels vs time.

    Role-free: every channel shares one voltage axis (mV channels were
    normalised to V by the loader), labelled by its PicoScope name.
    ``enabled`` (set/list of channel names) filters which to draw; None =
    all.  Matches the app's MATLAB-style ticks + bracket axis labels +
    black axes; legend along the bottom.
    """
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    ax = fig.add_subplot(111)
    t = np.asarray(rec.time_us, dtype=float)
    names = [n for n in rec.channels if (enabled is None or n in enabled)]
    for i, nm in enumerate(names):
        y = np.asarray(rec.channels[nm], dtype=float)
        u = rec.source_units.get(nm, "V")
        lbl = nm if u in ("V", "") else f"{nm} [{u}→V]"
        ax.plot(t, y, color=MATLAB_COLORS[i % len(MATLAB_COLORS)],
                linewidth=1.2, label=lbl)
    ax.set_xlabel("Time [µs]", fontsize=14, color="black")
    ax.set_ylabel("Voltage [V]", fontsize=14, color="black")
    ax.tick_params(axis="both", labelsize=12, colors="black")
    for _sp in ax.spines.values():
        _sp.set_color("black")
    if t.size:
        _xt = _matlab_x_limits(t, target=7)
        if _xt is not None:
            ticks, xmn, xmx = _xt
            ax.set_xticks(ticks)
            ax.set_xlim(xmn, xmx)
    _grid(ax, show_grid)
    title = getattr(getattr(rec, "path", None), "stem", "") or "PicoScope capture"
    fig.text(0.5, 0.97, title, ha="center", va="top",
             fontsize=13, fontweight="bold")
    if names:
        h, l = ax.get_legend_handles_labels()
        fig.tight_layout(rect=(0, 0.07, 1, 0.94))
        fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 0.0),
                   ncol=min(len(names), 4), fontsize=11, frameon=True,
                   framealpha=0.9)
    else:
        fig.tight_layout(rect=(0, 0, 1, 0.94))
    return fig


def plot_charge_transfer(capture, *, area_um2: Optional[float] = None,
                         onset_us: Optional[float] = None,
                         fig: Optional[Figure] = None,
                         show_grid: bool = True) -> Figure:
    """Harris 2019 chronopotentiometry capacitive/Faradaic decomposition view.

    Three stacked panels vs time (the operator's "dE/dt view"):
      1. the active-electrode potential (E_act when recorded, else V_mon);
      2. **dE/dt** — CONSTANT ⇒ capacitive double-layer charging
         (``i_c = A·C_dl·dE/dt``); a DIP toward 0 ⇒ a Faradaic reaction holds
         the potential;
      3. **1/(dE/dt)** — the reciprocal derivative, where Faradaic reactions
         show as peaks (Harris Fig 8C/D).
    Annotates the capacitive‑dE/dt baseline + the Faradaic onset, and reports
    C_dl (double-layer capacitance, needs ``area_um2``) + the approximate
    Faradaic charge fraction.
    """
    from .metrics import (chronopotentiometry_charge_transfer,
                          charge_transfer_dedt, pulse_onset_us, phase_windows)
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    t = np.asarray(capture.time_us, dtype=float)
    _va = (capture.e_act_v if getattr(capture, "e_act_v", None) is not None
           else capture.v_mon_v)
    v = np.asarray(_va, dtype=float)
    pat = capture.pattern
    if onset_us is None:
        try:
            onset_us = pulse_onset_us(t, capture.i_mon_ua, v)
        except Exception:
            onset_us = 0.0
    _, dedt = charge_transfer_dedt(t, v, pat, onset_us=onset_us)
    try:
        exc_idx = max(range(len(pat.phases)),
                      key=lambda i: abs(float(pat.phases[i].amplitude_ua)))
        pw = phase_windows(t, pat, onset_us=onset_us)
        t0, t1 = float(pw[exc_idx].start_us), float(pw[exc_idx].end_us)
    except Exception:
        exc_idx, pw, t0, t1 = 0, [], onset_us, onset_us + 200.0
    ct = chronopotentiometry_charge_transfer(
        t, v, pat, onset_us=onset_us, area_um2=(area_um2 or 0.0),
        phase_idx=exc_idx)
    f0 = float(pw[0].start_us) if pw else t0
    f1 = float(pw[-1].end_us) if pw else t1
    margin = 0.12 * (f1 - f0) if f1 > f0 else 20.0
    win = (t >= f0 - margin) & (t <= f1 + margin)
    recip = 1.0 / np.where(np.abs(dedt) < 1e-6, np.nan, dedt)
    ax = fig.subplots(3, 1, sharex=True)
    ax[0].plot(t[win], v[win] * 1000, color=MATLAB_COLORS[0], lw=1.3)
    ax[0].set_ylabel("Potential [mV]", fontsize=12, color="black")
    ax[1].plot(t[win], dedt[win], color=MATLAB_COLORS[1], lw=1.1)
    ax[1].axhline(0, color="black", lw=0.4)
    if np.isfinite(ct.dedt_capacitive_v_per_s):
        body = (t >= t0) & (t <= t1)
        sgn = 1.0 if float(np.mean(dedt[body]) if np.any(body) else -1.0) >= 0 else -1.0
        ax[1].axhline(sgn * ct.dedt_capacitive_v_per_s * 1e-6, color="gray",
                      lw=0.9, ls="--")
    ax[1].set_ylabel("dE/dt [V/µs]\nflat=capacitive, dip→0=Faradaic",
                     fontsize=9, color="black")
    ax[2].plot(t[win], recip[win], color=MATLAB_COLORS[2], lw=1.0)
    ax[2].set_ylabel("1/(dE/dt)\nFaradaic = peak", fontsize=9, color="black")
    ax[2].set_xlabel("Time [µs]", fontsize=12, color="black")
    for a in ax:
        for b in (t0, t1):
            a.axvline(b, color="black", lw=0.4, ls=":")
        if np.isfinite(ct.faradaic_onset_us):
            a.axvline(ct.faradaic_onset_us, color=MATLAB_COLORS[3], lw=1.0,
                      ls="-.")
        _grid(a, show_grid)
        a.tick_params(axis="both", labelsize=11, colors="black")
        for _sp in a.spines.values():
            _sp.set_color("black")
    cdl = (f"{ct.c_dl_mf_per_cm2:.2f} mF/cm²"
           if np.isfinite(ct.c_dl_mf_per_cm2) else "n/a (set surface area)")
    ff = (f"{ct.faradaic_fraction * 100:.0f}%"
          if np.isfinite(ct.faradaic_fraction) else "n/a")
    fig.text(0.5, 0.985, "Capacitive / Faradaic decomposition — chronopotentiometry (Harris 2019)",
             ha="center", va="top", fontsize=11, fontweight="bold")
    fig.text(0.5, 0.955,
             f"C$_{{dl}}$ = {cdl}   ·   Faradaic charge ≈ {ff}   "
             f"(split is approximate)", ha="center", va="top", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    return fig


def plot_overlay(captures_by_channel: Dict,
                 *, fig: Optional[Figure] = None,
                 channels_enabled: Optional[Set] = None,
                 waves_enabled: Optional[Set[str]] = None,
                 axis_map: Optional[Dict[str, str]] = None,
                 inset_enabled: bool = False,
                 inset_traces: Optional[Set[str]] = None,
                 show_grid: bool = True,
                 title: str = "Channel waveform overlay",
                 density: bool = False,
                 areas_by_key: Optional[Dict] = None,
                 potential_axis: bool = False,
                 reference_label: Optional[str] = None,
                 return_axis: bool = False,
                 return_label: Optional[str] = None) -> Figure:
    """Overlay one capture per entry onto a shared time axis.

    ``captures_by_channel`` maps an arbitrary hashable key to the
    Capture that key should contribute to the overlay. The key
    doubles as the legend label, so callers should use a
    user-meaningful string — e.g. ``"CH05"`` for monopolar,
    ``"CH05 v 06"`` for bipolar, ``"CH05 v 06,09"`` for tripolar
    (matches :meth:`Configuration.display_name`). The legacy
    ``Dict[int, Capture]`` form is still accepted; integer keys
    render as ``"CHnn"``.

    Entries not in ``channels_enabled`` are skipped; waveforms not
    in ``waves_enabled`` are skipped per entry.

    ``axis_map`` (optional) routes each waveform to a specific Y
    axis: ``"left"`` (the V scale), ``"right"`` (the second / current
    scale), or ``"na"`` (skip — same effect as omitting from
    ``waves_enabled``). When ``axis_map`` is omitted, the legacy
    routing applies: V_mon / E_act / E_ret → left, I_mon → right.

    ``inset_enabled`` + ``inset_traces`` add a small below-plot inset
    mirroring the selected traces (matches the experiment-tab
    MultiChannelScope inset). The inset shares the X range of the
    main view via ``sharex``.

    Color encodes the entry; line style encodes waveform type.
    """
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    if inset_enabled and inset_traces:
        # Stack: main plot on top (3/4 height), inset on bottom
        # (1/4 height), with shared X.
        ax_v  = fig.add_subplot(4, 1, (1, 3))
        ax_in = fig.add_subplot(4, 1, 4, sharex=ax_v)
    else:
        ax_v  = fig.add_subplot(111)
        ax_in = None
    ax_i = ax_v.twinx()
    if channels_enabled is None:
        channels_enabled = set(captures_by_channel.keys())
    if waves_enabled is None:
        waves_enabled = set(WAVE_TYPES)
    if axis_map is None:
        # Legacy routing — V_mon / E_act / E_ret → left, I_mon → right.
        axis_map = {"V_mon": "left", "E_act": "left",
                    "E_ret": "left", "I_mon": "right"}
    inset_traces = set(inset_traces) if inset_traces else set()
    plotted_any = False
    left_waves: Set[str] = set()    # waveform types actually drawn per axis
    right_waves: Set[str] = set()
    # Stable order: sort by string repr so int keys order numerically
    # ("CH01", "CH02", ...) and string keys order lexicographically
    # ("CH05 v 06", "CH05 v 06,09", ...). tab20 wraps gracefully past
    # 20 entries.
    cmap = plt.get_cmap("tab20")
    keys = sorted((k for k in captures_by_channel if k in channels_enabled),
                  key=lambda x: (str(x)))
    # Per-wave line styles so same colour = same entry and same dash
    # = same waveform. Kept stable across runs.
    line_style = {"V_mon": "-", "E_act": "--", "E_ret": ":", "I_mon": "-."}
    for idx, key in enumerate(keys):
        cap = captures_by_channel[key]
        time_us = np.asarray(cap.time_us)
        if not time_us.size:
            continue
        color = cmap(idx % cmap.N)
        # Legend label: use the key as-is for strings; format ints
        # as "CHnn" for the legacy int-keyed callers.
        label_prefix = (f"CH{int(key):02d}"
                        if isinstance(key, int) else str(key))
        for wave in WAVE_TYPES:
            if wave not in waves_enabled:
                continue
            axis = axis_map.get(wave, "left")
            if axis == "na":
                continue
            data = getattr(cap, _WAVE_ATTR[wave], None)
            if data is None or not getattr(data, "size", 0):
                continue
            # Current-density toggle: convert the I_mon trace (µA) to A/cm²
            # using this entry's electrode area (operator: "choosing to do
            # current density plotting instead of current").  Falls back to
            # raw µA when no area is known for the key.
            if density and wave == "I_mon" and areas_by_key:
                _area_um2 = areas_by_key.get(key, float("nan"))
                if _area_um2 and np.isfinite(_area_um2) and _area_um2 > 0:
                    data = (np.asarray(data, dtype=float) * 1e-6
                            / (_area_um2 * 1e-8))
            target_ax = ax_i if axis == "right" else ax_v
            (right_waves if axis == "right" else left_waves).add(wave)
            target_ax.plot(time_us, data, color=color,
                           linestyle=line_style.get(wave, "-"),
                           linewidth=1.2 if wave != "I_mon" else 1.0,
                           label=f"{label_prefix} {wave}")
            plotted_any = True
            # Inset: mirror the trace if it's in the selection.
            if ax_in is not None and wave in inset_traces:
                ax_in.plot(time_us, data, color=color,
                           linestyle=line_style.get(wave, "-"),
                           linewidth=1.0)

    if not plotted_any:
        ax_v.text(0.5, 0.5,
                  "No traces to display.\n"
                  "Enable at least one channel and one waveform type.",
                  ha="center", va="center", color="#666",
                  transform=ax_v.transAxes, fontsize=10)
        ax_v.axis("off")
        ax_i.axis("off")
        return fig

    # Suppress the main plot's X label when an inset is shown — the
    # inset shares the X axis and gets the Time label instead so the
    # main plot's tick labels can be hidden cleanly.
    if ax_in is None:
        ax_v.set_xlabel("Time (µs)", fontsize=12)
    else:
        ax_v.tick_params(axis="x", labelbottom=False)
        ax_in.set_xlabel("Time (µs)", fontsize=11)
        # Inset y-label: generic "Inset" — but when the operator enabled a
        # reference-aware label AND the inset cleanly carries only potentials
        # / only V_mon, name it "Potential vs <ref>" / "Voltage vs <return>"
        # to match the main axis (gotcha #159).  Mixed / current insets keep
        # "Inset".
        _inset_lbl = "Inset"
        if potential_axis or return_axis:
            _cand = _axis_unit_label(
                inset_traces, density=density, potential_axis=potential_axis,
                reference_label=reference_label, return_axis=return_axis,
                return_label=return_label)
            if _cand and _cand.startswith(("Potential vs", "Voltage vs")):
                _inset_lbl = _cand
        ax_in.set_ylabel(_inset_lbl, fontsize=10)
        ax_in.tick_params(axis="both", labelsize=9)
        _grid(ax_in, show_grid)
    # Label each y-axis by what's actually ON it (operator: never default
    # "Current monitor"; "Voltage" if only voltage channels, "Unit" if
    # mixed).  Hide the right axis entirely when nothing is routed to it
    # (e.g. I_mon set to N/A) — no empty "Current monitor (µA)" axis.
    ax_v.set_ylabel(_axis_unit_label(left_waves, density=density,
                                     potential_axis=potential_axis,
                                     reference_label=reference_label,
                                     return_axis=return_axis,
                                     return_label=return_label)
                    or "Voltage [V]", fontsize=12)
    ax_v.tick_params(axis="both", labelsize=10)
    _right_lbl = _axis_unit_label(right_waves, density=density)
    if _right_lbl is None:
        ax_i.set_yticks([])
        ax_i.set_ylabel("")
        ax_i.spines["right"].set_visible(False)
    else:
        ax_i.set_ylabel(_right_lbl, fontsize=12, rotation=-90, labelpad=18,
                        va="bottom")
        ax_i.tick_params(axis="y", labelsize=10)
    _grid(ax_v, show_grid)
    ax_v.set_title(title)
    # Combined legend; place outside on the right when many channels
    # are visible so the trace area stays readable.
    h_v, l_v = ax_v.get_legend_handles_labels()
    h_i, l_i = ax_i.get_legend_handles_labels()
    handles = h_v + h_i
    labels = l_v + l_i
    if handles:
        ncol = 1 if len(handles) <= 8 else 2
        fig.tight_layout()
        # FIGURE legend flush to the top-right corner, then SHRINK the axes
        # so (a) the legend sits against the right edge with NO wasted
        # whitespace and (b) the right-axis label/ticks have room between the
        # plot and the legend (NO overlap).  A fixed ``tight_layout`` rect
        # over- or under-reserves because the legend's true width (channel
        # count × ncol) isn't known until it's drawn — so MEASURE the drawn
        # legend and reserve exactly its width (operator: "too much white
        # space on the right … legend overlapping the right axis").
        leg = fig.legend(handles, labels, loc="upper right",
                         bbox_to_anchor=(0.995, 0.99), fontsize=8, ncol=ncol,
                         framealpha=0.9, borderaxespad=0.0)
        try:
            fig.canvas.draw()
            x0 = leg.get_window_extent().transformed(
                fig.transFigure.inverted()).x0
            # Gap between the axes and the legend = room for the right-axis
            # ticks + rotated label (filled by them, so not whitespace).
            gap = 0.12 if _right_lbl is not None else 0.03
            fig.subplots_adjust(right=min(0.95, max(0.40, x0 - gap)))
        except Exception:
            fig.subplots_adjust(right=0.70 if _right_lbl is not None else 0.80)
    else:
        fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# Static export (single capture)
# ---------------------------------------------------------------------------
def export_capture_plot(capture: Capture, run: ChannelRun, session: Session,
                        path: Path | str, *, dpi: int = DEFAULT_DPI) -> Path:
    """Save a single capture's plot to disk (TIFF/PNG/PDF/SVG by extension).

    Default DPI is 600, matching MATLAB's ``-r600`` TIFF export. To match the
    pixel size at 600 dpi (10.24″ × 5.76″ × 600 = 6144 × 3456 px) the figure
    is created at 100 dpi screen size and matplotlib rescales on save.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    try:
        # Reference-aware left-axis label is automatic (matches the live plot).
        plot_capture(capture, run, session, fig=fig,
                     potential_axis=True, return_axis=True)
        _kw = {}
        if path.suffix.lower() in (".tif", ".tiff"):
            # LZW-compress TIFFs — uncompressed 6144×3456 RGBA is
            # ~83 MB per figure (16-channel sweep = 1.3 GB and a
            # minutes-long save burst); the mostly-white waveform plots
            # compress ~50-100×.  Lossless, journal-acceptable.
            _kw["pil_kwargs"] = {"compression": "tiff_lzw"}
        fig.savefig(str(path), dpi=dpi, bbox_inches="tight",
                    facecolor="white", **_kw)
    finally:
        plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Batch export across an entire session
# ---------------------------------------------------------------------------
def _final_capture(run: ChannelRun) -> Optional[Capture]:
    if not run.captures:
        return None
    good = [c for c in run.captures if c.status.good]
    if good:
        return max(good, key=lambda c: abs(c.pattern.excitation_phase.amplitude_ua))
    return run.captures[-1]


def _run_stem(run: ChannelRun) -> str:
    """Filename stem for one ChannelRun (``CH03`` / ``CH03_v_05_07``)."""
    if run.configuration.id in ("MP", "CG"):
        return f"CH{run.configuration.active:02d}"
    rets = "_".join(f"{r:02d}" for r in run.configuration.returns)
    return f"CH{run.configuration.active:02d}_v_{rets}"


def export_run_plot(session: Session, run: ChannelRun,
                    out_dir: Path | str, *, fmt: str = "tif",
                    dpi: int = DEFAULT_DPI) -> Optional[Path]:
    """Write ONE channel/combination's final-capture plot.

    The per-run unit of :func:`export_session_plots`, exposed so the
    runner worker can save each figure IN REAL TIME as its ChannelRun
    completes (operator request) instead of all-at-once after the
    session.  Returns the written path, or ``None`` when the run has no
    usable capture.
    """
    cap = _final_capture(run)
    if cap is None:
        return None
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lstrip(".").lower()
    fname = f"{session.name}_{_run_stem(run)}.{fmt}"
    return export_capture_plot(cap, run, session, out_dir / fname, dpi=dpi)


def export_session_plots(session: Session, out_dir: Path | str,
                         *, fmt: str = "tif", dpi: int = DEFAULT_DPI,
                         every_capture: bool = False,
                         parallel: bool | int = False) -> List[Path]:
    """Write per-channel plots from a session to ``out_dir``.

    Default behaviour mirrors the MATLAB ``runVoltageTransient`` end-of-sweep
    saving: one file per ChannelRun, showing the *final* (max-amplitude)
    capture. Set ``every_capture=True`` to write one file per capture
    (potentially hundreds for a long sweep).

    Parameters
    ----------
    session, out_dir, fmt, dpi, every_capture
        As before — single-process behaviour is unchanged.
    parallel : bool | int, default False
        ``False`` = sequential rendering on the calling thread.
        ``True`` = use ``min(os.cpu_count(), 8)`` worker processes.
        Integer = use exactly that many workers.

        Each worker holds its own matplotlib state (Agg backend, fork-safe)
        and produces one file. Useful for ``every_capture=True`` exports of
        large sessions (hundreds of plots) — typical 4–8× speedup. For the
        default "one plot per channel" case the overhead of spawning
        workers usually outweighs the saving, so leave it at ``False``.
    """
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lstrip(".").lower()

    # Build the (capture, run, output_path) work list once
    tasks: List[Tuple[Capture, ChannelRun, Path]] = []
    for run in session.runs:
        stem_base = _run_stem(run)
        if every_capture:
            for cap in run.captures:
                # 1-based capture number in the filename (cap001 = first)
                # to match the operator-facing plot label; ``cap.index``
                # stays the 0-based array index for data / metadata.
                fname = (f"{session.name}_{stem_base}"
                         f"_cap{int(cap.index) + 1:03d}.{fmt}")
                tasks.append((cap, run, out_dir / fname))
        else:
            cap = _final_capture(run)
            if cap is None:
                continue
            fname = f"{session.name}_{stem_base}.{fmt}"
            tasks.append((cap, run, out_dir / fname))

    if not parallel or len(tasks) <= 1:
        return [export_capture_plot(c, r, session, p, dpi=dpi)
                for c, r, p in tasks]
    return _parallel_export_plots(tasks, session, dpi, parallel)


def _parallel_export_plots(tasks, session: Session, dpi: int,
                           parallel: bool | int) -> List[Path]:
    """Run capture-plot exports across a ProcessPoolExecutor.

    Why ProcessPool not ThreadPool? matplotlib renders are CPU-bound
    (font shaping, AA rasterisation, PNG/TIFF compression) and Python
    holds the GIL for most of it. Processes give true parallelism and the
    Agg backend is fork-safe.

    Each task carries enough data that the worker doesn't need to import
    the experiment runner or hardware drivers — just the plotting module.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor, as_completed

    n_workers = parallel if isinstance(parallel, int) and parallel > 1 \
        else min(os.cpu_count() or 4, 8)

    payloads = [_capture_to_payload(c, r, session, p, dpi)
                for c, r, p in tasks]
    written: List[Path] = []
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = [ex.submit(_render_plot_payload, p) for p in payloads]
        for fut in as_completed(futures):
            try:
                written.append(fut.result())
            except Exception as e:
                # Surface but don't kill the batch — the caller still gets
                # whatever plots succeeded.
                print(f"[plot worker] {e}")
    return sorted(written)


def _capture_to_payload(cap: Capture, run: ChannelRun, session: Session,
                        path: Path, dpi: int) -> dict:
    """Slim payload for a worker — only the data the plotter needs.

    Avoids pickling the full ``Session`` (with all its runs and captures)
    over to every worker; each task gets just its own capture's arrays plus
    a few scalars for the title.
    """
    return {
        "time_us": np.asarray(cap.time_us),
        "v_mon_v": np.asarray(cap.v_mon_v),
        "i_mon_ua": np.asarray(cap.i_mon_ua),
        "e_act_v": (np.asarray(cap.e_act_v) if cap.e_act_v is not None else None),
        "e_ret_v": (np.asarray(cap.e_ret_v) if cap.e_ret_v is not None else None),
        "phase_widths_us": [ph.width_us for ph in cap.pattern.phases],
        "phase_amps_ua": [ph.amplitude_ua for ph in cap.pattern.phases],
        "phase_delays_us": [ph.delay_after_us for ph in cap.pattern.phases],
        "rate_hz": cap.pattern.rate_hz,
        "polarity": cap.pattern.polarity,
        "surface_area_um2": run.surface_area_um2,
        "config_label": run.configuration.display_name(),
        "subject": session.subject or session.name,
        "capture_index": cap.index,
        "out_path": str(path),
        "dpi": dpi,
    }


def _render_plot_payload(payload: dict) -> Path:
    """Worker entry point — render one capture and save it.

    ``plot_capture`` reads only a handful of attributes off ``run`` and
    ``session`` (``run.configuration.display_name()``,
    ``run.surface_area_um2``, ``session.subject``, ``session.name``), so we
    feed it duck-typed ``SimpleNamespace`` stand-ins instead of pickling
    the full ChannelRun and Session over from the parent process. ``Capture``
    and ``PulsePattern`` are real dataclasses and dirt cheap to rebuild.

    Lives at module top level so ``ProcessPoolExecutor`` (which uses
    ``spawn`` on Windows) can pickle the function reference.
    """
    from types import SimpleNamespace

    # Lazy imports inside the worker so each spawn pays them once and the
    # parent process doesn't drag matplotlib into the import graph just for
    # being a parent.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .session import Capture
    from .waveforms import Phase, PulsePattern

    pattern = PulsePattern(
        phases=[Phase(a, w, d) for a, w, d in zip(
            payload["phase_amps_ua"], payload["phase_widths_us"],
            payload["phase_delays_us"])],
        rate_hz=payload["rate_hz"],
    )
    cap = Capture(
        index=payload["capture_index"], pattern=pattern,
        time_us=payload["time_us"], v_mon_v=payload["v_mon_v"],
        i_mon_ua=payload["i_mon_ua"],
        e_act_v=payload["e_act_v"], e_ret_v=payload["e_ret_v"],
    )
    run = SimpleNamespace(
        configuration=SimpleNamespace(
            display_name=lambda label=payload["config_label"]: label),
        surface_area_um2=payload["surface_area_um2"],
    )
    session = SimpleNamespace(
        subject=payload["subject"], name=payload["subject"],
    )

    fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    try:
        # Reference-aware left-axis label is automatic (matches the live plot).
        plot_capture(cap, run, session, fig=fig,
                     potential_axis=True, return_axis=True)
        path = Path(payload["out_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(path), dpi=payload["dpi"], bbox_inches="tight",
                    facecolor="white")
        return path
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# Sweep / summary plots — Q_inj vs I_stim overlay, V_d vs Q_inj, etc.
# ---------------------------------------------------------------------------
def plot_qinj_vs_amplitude(session: Session, *, fig: Optional[Figure] = None,
                           show_grid: bool = True,
                           ) -> Figure:
    """Overlay ``Q_inj`` vs ``I_stim`` for every ChannelRun in the session.

    Useful for picking out which channels saturate early. Each run is one
    coloured line; the colour cycles through the MATLAB palette.
    """
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    ax = fig.add_subplot(111)
    for k, run in enumerate(session.runs):
        if not run.captures:
            continue
        amps = [c.pattern.excitation_phase.amplitude_ua for c in run.captures]
        qinjs = [c.metrics.charge_injection_mc_per_cm2 for c in run.captures]
        color = MATLAB_COLORS[k % len(MATLAB_COLORS)]
        ax.plot(np.abs(amps), qinjs, "-o", color=color, markersize=4,
                linewidth=1.2, label=run.configuration.display_name())
    ax.set_xlabel("|I_stim| (µA)", fontsize=14)
    ax.set_ylabel("Q_inj (mC/cm²)", fontsize=14)
    ax.tick_params(axis="both", labelsize=12)
    _grid(ax, show_grid)
    ax.set_title(f"Charge injection sweep — {session.name}", fontsize=13)
    if session.runs:
        ax.legend(loc="best", fontsize=10, framealpha=0.85)
    fig.tight_layout()
    return fig


def plot_vd_vs_qinj(session: Session, *, fig: Optional[Figure] = None,
                    show_grid: bool = True) -> Figure:
    """``V_d`` vs ``Q_inj`` scatter — the IEEE NER 2025 paper's Fig 4-style plot."""
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    ax = fig.add_subplot(111)
    for k, run in enumerate(session.runs):
        if not run.captures:
            continue
        qinjs = [c.metrics.charge_injection_mc_per_cm2 for c in run.captures
                 if np.isfinite(c.metrics.charge_injection_mc_per_cm2)]
        vds = [c.metrics.driving_voltage_v for c in run.captures
               if np.isfinite(c.metrics.driving_voltage_v)]
        n = min(len(qinjs), len(vds))
        if n == 0:
            continue
        color = MATLAB_COLORS[k % len(MATLAB_COLORS)]
        ax.plot(qinjs[:n], vds[:n], "o", color=color, markersize=5,
                label=run.configuration.display_name())
    ax.set_xlabel("Q_inj (mC/cm²)", fontsize=14)
    ax.set_ylabel("V_d (V)", fontsize=14)
    ax.tick_params(axis="both", labelsize=12)
    _grid(ax, show_grid)
    ax.set_title(f"Driving voltage vs charge injection — {session.name}",
                 fontsize=13)
    if session.runs:
        ax.legend(loc="best", fontsize=10, framealpha=0.85)
    fig.tight_layout()
    return fig


def export_session_summary_plots(session: Session, out_dir: Path | str,
                                 *, fmt: str = "tif",
                                 dpi: int = DEFAULT_DPI) -> List[Path]:
    """Write the Q_inj-vs-I and V_d-vs-Q_inj overlays. Returns paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lstrip(".").lower()
    written: List[Path] = []

    fig = plot_qinj_vs_amplitude(session)
    p1 = out_dir / f"{session.name}_qinj_vs_istim.{fmt}"
    fig.savefig(str(p1), dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    written.append(p1)

    fig = plot_vd_vs_qinj(session)
    p2 = out_dir / f"{session.name}_vd_vs_qinj.{fmt}"
    fig.savefig(str(p2), dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    written.append(p2)
    return written
