"""Pulse-shape control panel — biphasic / triphasic, symmetric / asymmetric.

Replaces the per-tab pattern QFormLayout in each experiment tab. Lets
the user pick:

* **Phase count**  — Biphasic (2 phases) or Triphasic (3 phases)
* **Symmetry**     — Symmetric (phase 2/3 are derived) or Asymmetric
                     (every phase amplitude/width is independently set)
* **Polarity**     — Cathodic-first or Anodic-first
* **Triphasic ratio** — three free-form floats, default 2 / -3 / 1, only
                     visible in Symmetric Triphasic mode
* **Per-phase amplitudes / widths** — visible in Asymmetric mode, one
                     spinbox pair per phase
* **Interphase delay**, **discharge delay**, **rate**
* **Charge-balance mode** — Off / Auto-adjust last phase amplitude /
                     Auto-adjust last phase width. When auto-adjust is
                     on, the chosen knob is recomputed live from the
                     other phases so the net charge per pulse is zero.

Public API
----------
* ``pattern() -> PulsePattern``   — current pattern (after charge balance)
* ``patternChanged(PulsePattern)`` — emitted on every control change
* ``current_prefs() / restore_prefs()`` — persistence

The panel does **not** know anything about which experiment is using it;
each tab can wrap it in its own QGroupBox with the right title.
"""
from __future__ import annotations

import math

from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import (
    DEFAULT_DISCHARGE_DELAY_US, DEFAULT_INTERPHASE_DELAY_US,
    DEFAULT_PHASE_WIDTH_US, DEFAULT_RATE_PPS,
    STIM_CURRENT_RESOLUTION_UA, STIM_CURRENT_UI_STEP_UA,
    STIM_MAX_AMPLITUDE_UA,
    STIM_TIME_RESOLUTION_US,
)
def _safe_set_spinbox_value(spinbox, value) -> None:
    """Best-effort ``spinbox.setValue(...)`` that swallows
    ``TypeError`` / ``ValueError`` from a malformed prefs entry.

    Detects whether the target is an integer spinbox (``QSpinBox``)
    or a double spinbox (``QDoubleSpinBox``) and casts the input
    accordingly — ``QSpinBox.setValue`` requires ``int`` and would
    silently no-op (raise TypeError caught here) if handed a float.

    Previously this pattern was hand-inlined ~13× across this file
    (and 4× in setup_tab.py) as
    ``try: sp.setValue(float(v)); except (TypeError, ValueError): pass``.
    A single helper makes the intent obvious — "restore this prefs
    value into this widget; if it doesn't parse, leave the widget
    at its current value" — and centralises any future logging or
    debug-traceback the team wants to add.
    """
    try:
        if isinstance(spinbox, QtWidgets.QSpinBox):
            spinbox.setValue(int(float(value)))
        else:
            spinbox.setValue(float(value))
    except (TypeError, ValueError):
        pass


from ..waveforms import (
    Phase, PulsePattern, shape_breakpoints,
    SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING,
    SHAPE_SINUSOIDAL, SHAPE_SPEEDBUMPS, SHAPE_BOWTIE, SHAPE_HALFPIPE,
    SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING, SHAPE_GAUSSIAN,
    LOCK_WIDTH, LOCK_AMPLITUDE,
    solve_capacitive_balance, _shape_duty,
)

#: Asymmetric biphasic shape options. The user-visible labels for
#: the rectangular and (pseudo-)capacitively-coupled entries can be
#: reworded freely between releases — the persisted short_codes
#: are what go into prefs / saved sessions, so renaming the labels
#: doesn't forfeit existing data. The ``cap_coupled`` short_code is
#: kept verbatim from when the entry was simply called
#: "Capacitively-coupled"; the visible label was later softened to
#: "Pseudo-capacitively-coupled" because the device-driven
#: rectangular flat + exponential decay is a pseudo-capacitive
#: approximation rather than a true RC discharge.
ASYMMETRIC_BIPHASIC_SHAPES = (
    ("Rectangular",                  "rectangular"),
    ("Pseudo-capacitively-coupled",  "cap_coupled"),
    # Mix and match — pick any per-phase shape independently.
    # Auto-balance arithmetic uses the per-phase shape duty
    # factors so charge balance still holds even when the two
    # phases have different shape factors (e.g. exp-decay
    # cathodic + rectangular anodic, Yip 2017's GA-optimal).
    ("Mix and match",                 "mix_match"),
)
# Note: five entries that used to live in the asymmetric dropdown
# (``linear_inc_dec``, ``linear_dec_inc``, ``linear_inc_inc``
# [Doğan RampUp], ``linear_dec_dec`` [Doğan RampDown], and
# ``exp_biphasic`` [Yip mirror-symmetric biphasic exponential])
# were moved into SYMMETRIC mode and removed from the asym dropdown.
# The four pair shapes live in SYMMETRIC (one amp + one width, with
# mirrored per-phase shapes via ``SYM_BIPHASIC_SHAPE_PAIRS``), and
# the two same-shape pairs (RampUp / RampDown) are reached via
# SYMMETRIC + Linear increasing / Linear decreasing (where both
# phases share the same ramp shape — exactly the Doğan definition).
# Old prefs files carrying those string ids are auto-migrated to
# the equivalent symmetric configuration in ``restore_prefs``.
ASYM_SHAPE_RECT        = "rectangular"
ASYM_SHAPE_CAP         = "cap_coupled"
#: Mix-and-match — user picks per-phase shape independently.
#: When selected the panel exposes two shape comboboxes (one
#: per phase) instead of pre-defined shape pairs. Charge balance
#: in this mode goes through ``PulsePattern.auto_balance`` with
#: shape-aware duty factors, so mixed-shape configurations
#: (e.g. rect cathodic + lin-decreasing anodic, Yip 2017's
#: GA-optimal) balance correctly.
ASYM_SHAPE_MIX_MATCH    = "mix_match"

#: Lock-mode labels for the cap-coupled charge-balance solver.
LOCK_MODE_OPTIONS = (
    ("Lock width (derive amplitude)", LOCK_WIDTH),
    ("Lock amplitude (derive width)", LOCK_AMPLITUDE),
)

#: τ-mode constants for the cap-coupled solver. ``"auto"`` keeps the
#: legacy behaviour where τ is derived from the locked geometry
#: (τ = t_a / EXP_DECAY_TAU_RATIO in LOCK_WIDTH; τ = Q/(I_a·decay)
#: in LOCK_AMPLITUDE). ``"manual"`` lets the user pin τ explicitly,
#: causing the solver to re-solve the *other* free parameter under
#: that constraint. Strings (not enum) so they round-trip through
#: prefs JSON.
TAU_MODE_AUTO   = "auto"
TAU_MODE_MANUAL = "manual"
TAU_MODE_OPTIONS = (
    ("Auto-derive τ",   TAU_MODE_AUTO),
    ("Manual τ",        TAU_MODE_MANUAL),
)

#: Default τ for manual mode. 100 µs sits in the typical PtIr
#: microelectrode RC range (50–200 µs for ~10 kΩ access × ~10 nF
#: double-layer; see Cogan 2008 §3.2). It's also numerically the
#: auto-derived τ for a typical t_a = 500 µs anodic phase under
#: the legacy ``EXP_DECAY_TAU_RATIO = 5`` rule, so the default
#: matches the auto-mode value users would otherwise see — no
#: surprise jump in waveform shape when they switch modes.
TAU_DEFAULT_US = 100.0
TAU_MIN_US     = 1.0       # spinbox floor — below 1 µs the
                           #     30 nA quantum dominates the
                           #     decay envelope; not useful.
TAU_MAX_US     = 10_000.0  # spinbox ceiling — 10 ms covers
                           #     macroelectrode RC time constants
                           #     and gives plenty of headroom for
                           #     intentionally-slow recharges.

# Human-readable label → shape constant. The dropdown uses these labels
# in the order shown; the first entry stays Rectangular so existing
# rectangular-only experiments keep their default behaviour.
SYMMETRIC_BIPHASIC_SHAPES = (
    ("Rectangular",       SHAPE_RECTANGULAR),
    ("Linear increasing", SHAPE_LINEAR_INCREASING),
    ("Linear decreasing", SHAPE_LINEAR_DECREASING),
    # Biphasic linear-pair waveforms — both phases share |amp|
    # and width but the SHAPE differs across phases. Placed
    # right after "Linear decreasing" per user preference so the
    # four linear options cluster together in the dropdown.
    ("Linear increasing → decreasing", "linear_inc_dec"),
    ("Linear decreasing → increasing", "linear_dec_inc"),
    ("Sinusoidal",        SHAPE_SINUSOIDAL),
    # Sahin & Tie (2007) compared 7 monophasic waveforms for
    # neural stimulation efficiency through practical (TiN /
    # IrOx) electrodes; ExpDec, LinDec, and Gaussian were the
    # three most-efficient when accounting for both the
    # strength–duration curve and the electrode's charge-injection
    # capacity. Adding the missing two completes Sahin's
    # monophasic comparison set in this dropdown.
    ("Gaussian",            SHAPE_GAUSSIAN),
    # Exponential family — increasing FIRST per user preference,
    # then decreasing, then the two mirrored-shape biphasic pairs.
    # Both pair shapes use ``SHAPE_EXP_DECAY`` and
    # ``SHAPE_EXP_INCREASING`` per phase and are recognised by
    # ``SYM_BIPHASIC_SHAPE_PAIRS`` below.
    ("Exponential increasing", SHAPE_EXP_INCREASING),
    ("Exponential decreasing", SHAPE_EXP_DECAY),
    ("Exponential increasing → decreasing", "exp_inc_dec"),
    ("Exponential decreasing → increasing", "exp_dec_inc"),
    # The "exotic" trio clusters at the bottom of the dropdown —
    # they're rarely the right answer for routine neural stim and
    # would clutter the commonly-used waveforms above.  Order within
    # the trio: Speedbumps DIRECTLY above Bowtie (operator: "Move the
    # speedbumps shape above bowtie shape in the list"), matching the
    # mix-and-match dropdowns' Speedbumps → Bowtie → Halfpipe order.
    ("Speedbumps",        SHAPE_SPEEDBUMPS),
    ("Bowtie",            SHAPE_BOWTIE),
    ("Halfpipe",          SHAPE_HALFPIPE),
)
#: Symmetric-mode shape ids whose two phases use DIFFERENT shapes.
#: The standard symmetric path uses ``PulsePattern.biphasic`` which
#: applies the same shape to both phases; these need a special
#: per-phase build path. Used by ``pattern()`` to detect when to
#: branch out of the standard symmetric build.
SYM_BIPHASIC_SHAPE_PAIRS = {
    "linear_inc_dec": (SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING),
    "linear_dec_inc": (SHAPE_LINEAR_DECREASING, SHAPE_LINEAR_INCREASING),
    # Exponential pairs — mirror-symmetric like Yip 2017's
    # biphasic-exponential, except now usable from the symmetric
    # dropdown (one amp + one width) rather than only from the
    # asymmetric path. ``exp_inc_dec``: cathodic ramps from
    # near-zero up to peak (exp_increasing), anodic decays from
    # peak (exp_decay). ``exp_dec_inc``: time-reversed.
    "exp_inc_dec":    (SHAPE_EXP_INCREASING, SHAPE_EXP_DECAY),
    "exp_dec_inc":    (SHAPE_EXP_DECAY,      SHAPE_EXP_INCREASING),
}
from . import rich
from .repeating_spinbox import RepeatingDoubleSpinBox, RepeatingSpinBox
from .widgets import enable_spreadsheet_paste


# Phase-count and symmetry combo entries. String values are stable across
# releases — they're saved into the prefs JSON.
BIPHASIC = "Biphasic"
TRIPHASIC = "Triphasic"
ARBITRARY = "Arbitrary"
SYMMETRIC = "Symmetric"
ASYMMETRIC = "Asymmetric"


# Arbitrary sub-mode entries
ARB_FIXED = "Fixed"        # one shared period, N amplitude values
ARB_VARIABLE = "Variable"  # N (amplitude, duration) pairs

# Hardware row caps and per-row width range. PlexStim 2.0 limits
# arb-pattern rows to 999 (fixed-period) or 499 (variable-period) and
# constrains durations to 1–65535 µs. The amplitude bound is the
# usual ±1000 µA ceiling enforced elsewhere.
ARB_MAX_ROWS_FIXED = 999
ARB_MAX_ROWS_VAR = 499
ARB_MIN_DURATION_US = 1
ARB_MAX_DURATION_US = 65535
ARB_DEFAULT_ROWS = 4

CHARGE_BAL_OFF = "Off (manual)"
CHARGE_BAL_AMP = "Auto-adjust last phase amplitude"
CHARGE_BAL_WID = "Auto-adjust last phase width"


# ---------------------------------------------------------------------------
# Phase-shape preview helpers
# ---------------------------------------------------------------------------
def _render_shape_pixmap(shape: str, *,
                         w_px: int = 80, h_px: int = 36,
                         bump_count: int = 2,
                         color: str = "#1976d2",
                         polarity: int = -1) -> QtGui.QPixmap:
    """Render a small biphasic-symmetric example of ``shape`` to a
    QPixmap. Used as both the dropdown-item icon (small) and the
    closed-combobox tooltip image (a larger version of the same).

    The preview shows a biphasic pulse — one ``shape``-rendered
    phase 0, a small interphase gap, then the matching phase 1
    (same shape unless a pair entry is selected), plus a faint
    dashed y=0 baseline so the user can read the polarity at a
    glance.

    ``polarity`` selects which sign goes first:
      * ``-1`` (default) — **cathodic-first**: phase 0 negative
        (below the y=0 line), phase 1 positive (above).
      * ``+1`` — **anodic-first**: phase 0 positive, phase 1
        negative. The icon flips vertically vs the cathodic-first
        version, matching how the live polarity dropdown affects
        the actual pattern build.

    For shape-pair entries (``linear_inc_dec`` / ``linear_dec_inc``
    / ``exp_inc_dec`` / ``exp_dec_inc``), the per-phase shapes
    differ — look the pair up in :data:`SYM_BIPHASIC_SHAPE_PAIRS`
    and render phase 0 / phase 1 with their respective shapes.
    Without this lookup the pair-shape ids would fall through to
    ``shape_breakpoints``'s unknown-shape branch (a flat
    rectangle), producing a misleading icon.

    Uses :func:`stimtest.waveforms.shape_breakpoints` so the preview
    is always faithful to the actual shape generator.
    """
    pm = QtGui.QPixmap(w_px, h_px)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pm)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        # Per-phase shape lookup. Same-shape biphasic uses
        # ``shape`` for both phases (the default symmetric
        # interpretation); pair-shape ids supply distinct
        # phase-0 / phase-1 shapes via SYM_BIPHASIC_SHAPE_PAIRS.
        if shape in SYM_BIPHASIC_SHAPE_PAIRS:
            p0_shape, p1_shape = SYM_BIPHASIC_SHAPE_PAIRS[shape]
        else:
            p0_shape = p1_shape = shape
        # τ for exp shapes: use the canonical W/N derivation so
        # both exp_decay (decays from peak) and exp_increasing
        # (rises to peak) render their characteristic shape at
        # the icon's small footprint. Passing 0 here lets
        # ``shape_breakpoints`` fall back to W/N internally.
        A, W = 1.0, 1.0
        # Polarity-aware sign assignment. ``polarity = -1`` →
        # phase-0 negative (cathodic), phase-1 positive (anodic).
        # ``polarity = +1`` → flipped.
        sign0 = -1.0 if polarity == -1 else +1.0
        bps_neg = shape_breakpoints(
            amplitude_ua=sign0 * A, width_us=W,
            shape=p0_shape, bump_count=bump_count, n_samples=24)
        bps_pos = shape_breakpoints(
            amplitude_ua=-sign0 * A, width_us=W,
            shape=p1_shape, bump_count=bump_count, n_samples=24)
        gap_w = 0.10 * W
        pulse_total = 2.0 * W + gap_w
        pad_x, pad_y = 4, 4
        plot_w = w_px - 2 * pad_x
        plot_h = h_px - 2 * pad_y
        y0 = pad_y + plot_h * 0.5

        def x_of(t: float) -> float:
            return pad_x + (t / pulse_total) * plot_w

        def y_of(a: float) -> float:
            return y0 - (a / A) * (plot_h * 0.5)

        # Faint dashed y=0 baseline.
        baseline_pen = QtGui.QPen(QtGui.QColor("#bbb"), 1,
                                  QtCore.Qt.PenStyle.DashLine)
        painter.setPen(baseline_pen)
        painter.drawLine(int(pad_x), int(y0),
                         int(w_px - pad_x), int(y0))

        # Build the polyline cathodic → gap → anodic.
        pen = QtGui.QPen(QtGui.QColor(color), 1.6)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QtGui.QPainterPath()
        # Lead-in zero baseline.
        path.moveTo(x_of(0.0), y_of(0.0))
        # Cathodic phase.
        for t, a in bps_neg:
            path.lineTo(x_of(t), y_of(a))
        # Step back to baseline before the gap (sample-and-hold edge).
        path.lineTo(x_of(W), y_of(0.0))
        # Interphase gap held at zero.
        path.lineTo(x_of(W + gap_w), y_of(0.0))
        # Anodic phase.
        for t, a in bps_pos:
            path.lineTo(x_of(W + gap_w + t), y_of(a))
        # Trailing baseline.
        path.lineTo(x_of(pulse_total), y_of(0.0))
        painter.drawPath(path)
    finally:
        painter.end()
    return pm


def _render_single_phase_pixmap(shape: str, *,
                                w_px: int = 60, h_px: int = 24,
                                color: str = "#1976d2",
                                polarity: int = -1) -> QtGui.QPixmap:
    """Render a SINGLE-PHASE preview of ``shape`` (just one polarity's
    side, no anodic+cathodic mirror) for use in the mix-and-match
    per-phase shape comboboxes.

    Each mix-and-match phase picker stands for ONE phase's shape
    choice, not a whole biphasic pulse — so the icon should
    show that one phase. Sized smaller (60×24 vs the biphasic
    80×36) since the dropdown is narrower and the user is comparing
    shape contours side-by-side.

    ``polarity`` is the sign of the amplitude: ``-1`` plots the
    icon in the NEGATIVE y half (cathodic — the default), ``+1``
    plots in the POSITIVE y half (anodic). The caller sets it
    per-phase so phase 1 and phase 2 icons reflect the user's
    Cathodic-first / Anodic-first dropdown:

    * Cathodic-first → phase 1 cathodic (-1), phase 2 anodic (+1)
    * Anodic-first  → phase 1 anodic (+1), phase 2 cathodic (-1)

    Uses :func:`stimtest.waveforms.shape_breakpoints` so the
    preview reflects the actual breakpoint generator. Curved
    shapes (exp / Gaussian / sin / halfpipe / ramps / bowtie)
    are sampled at 24 breakpoints — enough to read the shape
    contour at icon size without aliasing.
    """
    pm = QtGui.QPixmap(w_px, h_px)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pm)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        A, W = 1.0, 1.0
        sign = -1.0 if polarity < 0 else +1.0
        bps = shape_breakpoints(
            amplitude_ua=sign * A, width_us=W,
            shape=shape, n_samples=24)
        pad_x, pad_y = 4, 4
        plot_w = w_px - 2 * pad_x
        plot_h = h_px - 2 * pad_y
        y0 = pad_y + plot_h * 0.5

        def x_of(t: float) -> float:
            return pad_x + (t / W) * plot_w

        def y_of(a: float) -> float:
            return y0 - (a / A) * (plot_h * 0.5)

        # Faint dashed y=0 baseline.
        baseline_pen = QtGui.QPen(QtGui.QColor("#bbb"), 1,
                                  QtCore.Qt.PenStyle.DashLine)
        painter.setPen(baseline_pen)
        painter.drawLine(int(pad_x), int(y0),
                         int(w_px - pad_x), int(y0))

        pen = QtGui.QPen(QtGui.QColor(color), 1.6)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QtGui.QPainterPath()
        path.moveTo(x_of(0.0), y_of(0.0))
        for t, a in bps:
            path.lineTo(x_of(t), y_of(a))
        path.lineTo(x_of(W), y_of(0.0))
        painter.drawPath(path)
    finally:
        painter.end()
    return pm


def _render_asym_shape_pixmap(asym_shape: str, *,
                              w_px: int = 80, h_px: int = 36,
                              color: str = "#1976d2",
                              polarity: int = -1) -> QtGui.QPixmap:
    """Render a small ASYMMETRIC biphasic example to a QPixmap.

    ``polarity`` flips which phase goes first:
      * ``-1`` (default) — **cathodic-first**: phase 0 negative,
        phase 1 positive.
      * ``+1`` — **anodic-first**: signs swapped. Matches the
        live polarity dropdown so the icon updates when the user
        toggles polarity.

    Two flavours, mirroring :data:`ASYMMETRIC_BIPHASIC_SHAPES`:

      * ``"rectangular"`` — rectangular cathodic + rectangular anodic
        with mismatched amplitudes / widths (1.0 µA × 1.0 µs cathodic
        balanced by 0.4 µA × 2.5 µs anodic — typical asymmetric
        ratio that makes the visual asymmetry obvious).
      * ``"cap_coupled"`` — rectangular cathodic + exp-decay anodic.
        Anodic peaks at 1.0 µA, decays with τ = anodic_width / 5 over
        2 µs — the canonical capacitively-coupled recharge profile.

    In both cases the cathodic and anodic phases carry equal-magnitude
    integrated charge, so the preview is also a pedagogically-correct
    charge-balanced pulse. Used as the dropdown-item icon (small) and
    closed-combobox tooltip image (a larger version).
    """
    pm = QtGui.QPixmap(w_px, h_px)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pm)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        if asym_shape == ASYM_SHAPE_CAP:
            # Rectangular cathodic + exp-decay anodic.
            cath_w, anod_w = 1.0, 2.0
            bps_cath = shape_breakpoints(
                amplitude_ua=-1.0, width_us=cath_w,
                shape=SHAPE_RECTANGULAR, n_samples=2)
            bps_anod = shape_breakpoints(
                amplitude_ua=+1.0, width_us=anod_w,
                shape=SHAPE_EXP_DECAY,
                tau_us=anod_w / 5.0, n_samples=24)
        elif asym_shape == ASYM_SHAPE_MIX_MATCH:
            # Mix-and-match preview — show a representative mixed-
            # shape pulse so the user can tell at a glance that
            # this entry has per-phase shape pickers. Per user
            # spec: cathodic linear-increasing + anodic rectangular
            # (one of Yip 2017's energy-efficient configurations,
            # and a clear visual hint that the two phases needn't
            # share a shape). Auto-balance arithmetic gives the
            # anodic amplitude as |I_c|·t_c·duty_cath / (t_a·duty_anod);
            # at unit amplitude / width with cath duty 0.5 and
            # anod duty 1.0, anod amp = 0.5 — a short squat
            # rectangle paired with a triangular ramp.
            cath_w, anod_w = 1.5, 1.5
            bps_cath = shape_breakpoints(
                amplitude_ua=-1.0, width_us=cath_w,
                shape=SHAPE_LINEAR_INCREASING, n_samples=8)
            bps_anod = shape_breakpoints(
                amplitude_ua=+0.5, width_us=anod_w,
                shape=SHAPE_RECTANGULAR, n_samples=2)
        else:
            # Rectangular asymmetric: tall narrow cathodic + short wide
            # anodic. 1.0 µA × 1.0 µs == 0.4 µA × 2.5 µs (charge
            # balanced by construction).
            cath_w, anod_w = 1.0, 2.5
            bps_cath = shape_breakpoints(
                amplitude_ua=-1.0, width_us=cath_w,
                shape=SHAPE_RECTANGULAR, n_samples=2)
            bps_anod = shape_breakpoints(
                amplitude_ua=+0.4, width_us=anod_w,
                shape=SHAPE_RECTANGULAR, n_samples=2)
        # Polarity flip — invert every breakpoint amplitude so the
        # "cathodic" preview becomes "anodic" (and vice versa).
        # Keeps the per-shape contour but mirrors it across y=0,
        # matching how the live polarity dropdown affects the
        # actual pattern. Naming of ``bps_cath`` / ``bps_anod``
        # is preserved (they still represent phase 0 / phase 1);
        # only the SIGN changes.
        if polarity == +1:
            bps_cath = [(t, -a) for t, a in bps_cath]
            bps_anod = [(t, -a) for t, a in bps_anod]
        gap_w = 0.10 * cath_w
        pulse_total = cath_w + gap_w + anod_w
        pad_x, pad_y = 4, 4
        plot_w = w_px - 2 * pad_x
        plot_h = h_px - 2 * pad_y
        y0 = pad_y + plot_h * 0.5

        def x_of(t: float) -> float:
            return pad_x + (t / pulse_total) * plot_w

        def y_of(a: float) -> float:
            # Reference scale to ±1.0 µA so the cap-coupled exp-decay
            # peak (+1) and the rectangular cathodic peak (-1) both
            # touch the plot edges.
            return y0 - a * (plot_h * 0.5)

        # Faint dashed y=0 baseline.
        baseline_pen = QtGui.QPen(QtGui.QColor("#bbb"), 1,
                                  QtCore.Qt.PenStyle.DashLine)
        painter.setPen(baseline_pen)
        painter.drawLine(int(pad_x), int(y0), int(w_px - pad_x), int(y0))

        pen = QtGui.QPen(QtGui.QColor(color), 1.6)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        path = QtGui.QPainterPath()
        path.moveTo(x_of(0.0), y_of(0.0))
        # Cathodic phase
        for t, a in bps_cath:
            path.lineTo(x_of(t), y_of(a))
        # Step back to baseline at end of cathodic
        path.lineTo(x_of(cath_w), y_of(0.0))
        # Interphase gap
        path.lineTo(x_of(cath_w + gap_w), y_of(0.0))
        # Anodic phase
        for t, a in bps_anod:
            path.lineTo(x_of(cath_w + gap_w + t), y_of(a))
        # Trailing baseline.
        path.lineTo(x_of(pulse_total), y_of(0.0))
        painter.drawPath(path)
    finally:
        painter.end()
    return pm


def _render_pulse_style_pixmap(kind: str, *,
                               w_px: int = 80, h_px: int = 36,
                               color: str = "#1976d2") -> QtGui.QPixmap:
    """Render a small example waveform for the Pulse-style dropdown.

    Three flavours, matching the entries in the ``phase_count`` combo:

      * ``BIPHASIC``  — symmetric rectangular cathodic + anodic.
      * ``TRIPHASIC`` — rectangular cathodic / anodic / cathodic with
        the centre phase ~3× the outers (the IEEE NER 2:3:1 magnitude
        ratio used elsewhere in this widget).
      * ``ARBITRARY`` — an action-potential-shaped curve modelled on
        the example waveform in the PlexStim manual: gentle baseline,
        rapid depolarisation upstroke, sharp peak, exponential
        repolarisation, brief afterhyperpolarisation undershoot, and
        return to baseline. Drawn polyline-only (the user's
        ``arb_table`` will look nothing like this; the icon is just a
        nudge that ``Arbitrary`` lets you draw whatever you want).
    """
    pm = QtGui.QPixmap(w_px, h_px)
    pm.fill(QtCore.Qt.GlobalColor.transparent)
    painter = QtGui.QPainter(pm)
    try:
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        pad_x, pad_y = 4, 4
        plot_w = w_px - 2 * pad_x
        plot_h = h_px - 2 * pad_y
        y0 = pad_y + plot_h * 0.5

        # Faint dashed y=0 baseline (skipped for arbitrary because
        # the action-potential waveform has its own implied zero).
        if kind != ARBITRARY:
            baseline_pen = QtGui.QPen(QtGui.QColor("#bbb"), 1,
                                      QtCore.Qt.PenStyle.DashLine)
            painter.setPen(baseline_pen)
            painter.drawLine(int(pad_x), int(y0),
                             int(w_px - pad_x), int(y0))

        pen = QtGui.QPen(QtGui.QColor(color), 1.6)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        path = QtGui.QPainterPath()

        if kind == BIPHASIC:
            # Two equal-amplitude rectangular phases with a small gap.
            # cathodic ─ gap ─ anodic. Heights ±1 (full-scale).
            cath_w, gap_w, anod_w = 1.0, 0.10, 1.0
            total = cath_w + gap_w + anod_w
            def x(t): return pad_x + (t / total) * plot_w
            def y(a): return y0 - a * (plot_h * 0.45)
            path.moveTo(x(0.0), y(0.0))
            path.lineTo(x(0.0), y(-1.0)); path.lineTo(x(cath_w), y(-1.0))
            path.lineTo(x(cath_w), y(0.0)); path.lineTo(x(cath_w + gap_w), y(0.0))
            path.lineTo(x(cath_w + gap_w), y(+1.0))
            path.lineTo(x(total), y(+1.0)); path.lineTo(x(total), y(0.0))
        elif kind == TRIPHASIC:
            # 2:3:1 ratio cathodic / anodic / cathodic, equal widths.
            # The middle anodic phase is the largest (excitation).
            phw, gap = 0.6, 0.08
            ratios = (-1.0, +3.0/3.0 * 1.4, -0.45)   # scaled to fit ±1
            total = 3 * phw + 2 * gap
            def x(t): return pad_x + (t / total) * plot_w
            def y(a): return y0 - a * (plot_h * 0.45)
            t = 0.0
            path.moveTo(x(t), y(0.0))
            for i, r in enumerate(ratios):
                path.lineTo(x(t), y(r)); path.lineTo(x(t + phw), y(r))
                path.lineTo(x(t + phw), y(0.0))
                t += phw
                if i < 2:
                    path.lineTo(x(t + gap), y(0.0))
                    t += gap
        else:
            # ARBITRARY — action-potential-like waveform. Keys are
            # (time fraction, amplitude). Eight samples cover a
            # textbook Hodgkin-Huxley shape: pre-spike baseline,
            # threshold, fast rise, peak, fall through baseline,
            # afterhyperpolarisation, slow recovery.
            ap = [
                (0.00, -0.45),   # resting
                (0.18, -0.45),
                (0.22, -0.20),   # threshold approach
                (0.26, +1.00),   # peak (rapid Na+ influx)
                (0.32, +0.30),
                (0.42, -0.55),
                (0.55, -0.85),   # afterhyperpolarisation trough
                (0.75, -0.55),
                (1.00, -0.45),   # back to resting
            ]
            def x(t): return pad_x + t * plot_w
            def y(a): return y0 - a * (plot_h * 0.45)
            path.moveTo(x(ap[0][0]), y(ap[0][1]))
            for t, a in ap[1:]:
                path.lineTo(x(t), y(a))
        painter.drawPath(path)
    finally:
        painter.end()
    return pm


def _pixmap_to_html_img(pm: QtGui.QPixmap) -> str:
    """Convert a :class:`QPixmap` to an HTML ``<img>`` tag with the
    PNG bytes embedded as a base64 data URI. Used to bake a shape
    preview into a Qt rich-text tooltip — Qt's tooltip renderer
    supports ``data:image/png;base64,…`` sources via QTextDocument.
    """
    buf = QtCore.QBuffer()
    buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
    pm.save(buf, "PNG")
    b64 = bytes(buf.data().toBase64()).decode("ascii")
    buf.close()
    return f'<img src="data:image/png;base64,{b64}">'


class PatternControlPanel(QtWidgets.QGroupBox):
    """Pulse-shape control panel + live PulsePattern output."""

    patternChanged = QtCore.pyqtSignal(object)   # PulsePattern (LIVE — per keystroke)
    # patternCommitted fires ONLY when the user COMMITS an edit — Enter /
    # focus-out on a spinbox, or a discrete combo / checkbox / radio change —
    # NOT on every keystroke (operator: "when typing in the input, do not print
    # in the log pane with every key.  Only print after return/enter is pressed
    # or clicked out").  The LIVE preview stays on ``patternChanged``; the LOG
    # PANE listens to ``patternCommitted`` instead.
    patternCommitted = QtCore.pyqtSignal(object)  # PulsePattern (on commit)
    # Emitted alongside patternChanged with a True/False indicating
    # whether the charge-balance warning should be visible at all
    # (only meaningful in biphasic + asymmetric + manual mode).
    balanceWarningVisibility = QtCore.pyqtSignal(bool)
    # Emitted when the user toggles the auto-discharge checkbox AFTER
    # the (optional) "are you sure" warning dialog has resolved. The
    # connection panel listens for this signal to push the new state
    # to the live device and persist it to prefs. Multiple pattern
    # panels (one per experiment tab) keep each other in sync via the
    # main window's central handler — see ``set_auto_discharge_silent``
    # below for the programmatic-update entry point.
    autoDischargeToggled = QtCore.pyqtSignal(bool)
    # Emitted when the user edits the INLINE average-count spinbox that
    # sits beside the acquisition-time readout (operator: "add an input
    # that can change the average count").  MainWindow forwards the new
    # count into the Setup tab's ``acq_navg_spin`` — the single source
    # of truth — whose ``acquisitionChanged`` signal then re-broadcasts
    # to every experiment tab (and back into this panel via
    # ``set_acquisition_info``, which is a no-op when the value already
    # matches, so there is no signal loop).
    acqNavgEdited = QtCore.pyqtSignal(int)

    # Triphasic proportions are MAGNITUDES only — the polarity is set
    # by the polarity selector and the SIGNS alternate per phase
    # (phase 1 = polarity, phase 2 = -polarity, phase 3 = polarity).
    # Per IEEE NER paper convention the dominant excitation phase is
    # the middle one (3× the outers), giving a 2:3:1 magnitude ratio.
    DEFAULT_TRI_RATIO = (2.0, 3.0, 1.0)
    # PlexStim 2.0 current-source rails at ±1000 μA per channel — clamp
    # the spinboxes there so the user can't ask for amplitudes the
    # hardware can't deliver.
    AMP_RANGE = (-STIM_MAX_AMPLITUDE_UA, STIM_MAX_AMPLITUDE_UA)
    WIDTH_RANGE = (1.0, 5000.0)

    # Two-way toggle between pulse rate (pps) and pulse period (ms).
    # Same rate under the hood — only the display flips. ``rate_hz`` is
    # always the canonical value passed to the runner.
    UNIT_PPS = "pps"
    UNIT_MS = "ms"
    RATE_UNIT_CHOICES = (UNIT_PPS, UNIT_MS)

    def __init__(self, title: str = "Pulse pattern", parent=None):
        super().__init__(title, parent)
        self._suspend_signals = False
        # Remembered rate (in Hz, the canonical unit) while "No
        # interpulse delay" is on. Toggling on captures the user's
        # typed rate; toggling off restores it. None means "nothing
        # saved yet". Stored in Hz so a unit change while pinned still
        # restores the same rate when the user un-pins.
        self._saved_rate_hz: Optional[float] = None
        # Current rate-spinbox unit. Default = pps to match prior
        # behaviour and what the runner / hardware see at the boundary.
        self._rate_unit: str = self.UNIT_PPS
        # Approximate per-capture acquisition time readout (rendered to the
        # RIGHT of the rate unit combo).  The estimate = averaged-sweep
        # count / pulse rate — each averaged capture must wait through one
        # pulse per averaged sweep, and pulses arrive at the pulse rate
        # (same formula the runner uses to size its capture timeout,
        # gotcha #32).  The averaging count + mode come from the Setup-tab
        # oscilloscope acquisition selection, pushed in via
        # :meth:`set_acquisition_info`; these defaults keep a sane estimate
        # on screen before the first push.
        self._acq_mode: str = "AVERAGE"
        self._acq_n_avg: int = 16
        # ---- Login profile (gates restricted shapes) ----------------
        # Starts as ANONYMOUS (Profile.NONE) so the panel populates
        # its shape dropdowns WITHOUT the gated entries.  MainWindow
        # calls :meth:`set_profile` after a successful profile login
        # to unlock the restricted shapes (halfpipe, bowtie,
        # speedbumps) — see :mod:`stimtest.gui.admin` for the gate
        # function ``is_restricted_unlocked``.  Stored as the string
        # value of :class:`stimtest.gui.admin.Profile` so the
        # comparison works whether callers pass the enum or the raw
        # string from prefs.
        self._current_profile: str = "none"

        # ------ top row: phase count + symmetry + polarity ------
        # Pulse-style dropdown — same icon + HTML-tooltip treatment
        # as ``shape_combo`` further down. Each entry shows a small
        # waveform preview (rectangular biphasic, rectangular
        # triphasic, action-potential-shaped arbitrary) so the user
        # can recognise each option from the dropdown without
        # reading the labels. The closed combobox tooltip swaps to a
        # larger version of the same preview on hover.
        self.phase_count = QtWidgets.QComboBox()
        self.phase_count.setIconSize(QtCore.QSize(80, 28))
        self._phase_count_tooltip_html: dict = {}
        _phase_count_blurbs = {
            BIPHASIC: "Two-phase pulse (cathodic + anodic). The most "
                      "common stim pattern; charge-balanced by mirroring "
                      "phase 1 in symmetric mode.",
            TRIPHASIC: "Three-phase pulse with the middle phase opposite "
                       "in polarity. Default 2:3:1 magnitude ratio "
                       "(IEEE NER convention) puts the dominant excitation "
                       "phase in the middle.",
            ARBITRARY: "Free-form waveform — you type each phase's "
                       "amplitude (and optionally duration) into a table. "
                       "Use this to model custom shapes such as the "
                       "action-potential waveform shown in the preview.",
        }
        for kind in (BIPHASIC, TRIPHASIC, ARBITRARY):
            icon_pm = _render_pulse_style_pixmap(kind, w_px=80, h_px=28)
            self.phase_count.addItem(QtGui.QIcon(icon_pm), kind)
            tip_pm = _render_pulse_style_pixmap(kind, w_px=240, h_px=80)
            self._phase_count_tooltip_html[kind] = (
                f"<qt><div style='width: 280px'>"
                f"<b>{kind}</b><br>"
                f"{_pixmap_to_html_img(tip_pm)}<br>"
                f"<span style='color:#555;font-size:9pt;'>"
                f"{_phase_count_blurbs[kind]}</span></div></qt>"
            )
        self._refresh_phase_count_tooltip()
        # Refresh the box-level tooltip whenever the user picks a
        # different style (the dropdown-list icons are static).
        self.phase_count.currentTextChanged.connect(
            lambda *_: self._refresh_phase_count_tooltip())
        self.symmetry = QtWidgets.QComboBox()
        self.symmetry.addItems([SYMMETRIC, ASYMMETRIC])
        self.symmetry.setToolTip(
            "Biphasic only. ``Symmetric`` mirrors the second phase "
            "from the first (equal amplitude, equal width, opposite "
            "sign — guaranteed charge-balanced by construction). "
            "``Asymmetric`` exposes per-phase amplitude / width / "
            "shape inputs so you can build cap-coupled or unbalanced "
            "pulses.")
        self.polarity = QtWidgets.QComboBox()
        # Display wording is "Cathodal / Anodal" (operator: 'Rename the
        # pulse polarity as "cathodal/anodal" instead of
        # "cathodic/anodic"').  Every polarity KEY in this file matches
        # the common prefix ``startswith("Cathod")`` so both spellings
        # test identically; legacy prefs text ("Cathodic-first") is
        # mapped to the new item in ``restore_prefs``.  Scientific metric
        # names (cathodic water-window limit, E_mc, …) are NOT renamed —
        # the request targets the pulse-polarity wording only.
        self.polarity.addItems(["Cathodal-first", "Anodal-first"])
        self.polarity.setToolTip(
            "Sign of the LEADING phase. ``Cathodal-first`` makes "
            "phase 1 negative (electrode pulled toward the cathodic "
            "limit first). ``Anodal-first`` flips every phase's sign. "
            "Phase 2 (and phase 3 in triphasic) inherit the "
            "alternating-sign convention enforced in ``PulsePattern."
            "triphasic``.")

        # ------ symmetric (single-shape) controls ------
        # Hardware resolution: 0.1 μA on amplitudes, 1 μs on widths.
        self.amp_excite = self._dspin(*self.AMP_RANGE, 50.0,
                                      step=STIM_CURRENT_UI_STEP_UA, decimals=1,
                                      suffix=" " + rich.UA, force_sign=True)
        self.amp_excite.setToolTip(
            "Magnitude of the excitation phase (largest |Q_ph|). For "
            "symmetric biphasic that's both phases. For triphasic "
            "it's whichever phase has the largest ratio entry — see "
            "the ``applied:`` line below the ratio row. Hardware "
            "resolution: 0.1 µA.")
        self.width_shared = self._dspin(*self.WIDTH_RANGE, DEFAULT_PHASE_WIDTH_US,
                                        step=STIM_TIME_RESOLUTION_US, decimals=0,
                                        suffix=" " + rich.US)
        self.width_shared.setToolTip(
            "Phase width (every phase shares this width in the "
            "symmetric path). Hardware resolution: 1 µs.")
        # The Q_ph parameter and the (current / width / Q_ph) lock
        # toggle live in the VT tab's "Fixed charge/phase" mode (see
        # ``VoltageTransientTab._build_qph_lock_form``). The pattern
        # panel itself only owns the raw amp / width inputs; other
        # experiment tabs read whatever Q_ph the resulting pattern
        # carries via ``Phase.charge_nc``.
        # Phase-shape dropdown — biphasic symmetric only. Each entry
        # stores the shape *constant* as userData so we don't pattern-
        # match on the human-readable label later. Each item also gets
        # a small QIcon previewing the shape (cathodic-first symmetric
        # biphasic) so the dropdown list shows what every option looks
        # like; the closed combobox itself carries an HTML tooltip
        # that renders a larger version of the same preview on hover.
        self.shape_combo = QtWidgets.QComboBox()
        # Stretch the item dimensions so the preview icons render
        # cleanly. Without an explicit setIconSize the combobox uses a
        # ~16-px square which is too small to read curved shapes.
        self.shape_combo.setIconSize(QtCore.QSize(80, 28))
        # Cache larger pixmaps (one per shape) for the tooltip image
        # so we don't re-render every time the user wiggles the mouse
        # over the combobox.
        self._shape_tooltip_html: dict = {}
        # Filter out profile-restricted shapes (halfpipe, bowtie,
        # speedbumps) when the current profile lacks access.  See
        # :meth:`_filter_restricted_shapes` for the gate logic.
        for label, shape_id in self._filter_restricted_shapes(
                SYMMETRIC_BIPHASIC_SHAPES):
            icon_pm = _render_shape_pixmap(shape_id, w_px=80, h_px=28)
            self.shape_combo.addItem(QtGui.QIcon(icon_pm), label,
                                     userData=shape_id)
            tip_pm = _render_shape_pixmap(shape_id, w_px=240, h_px=80)
            self._shape_tooltip_html[shape_id] = (
                f"<qt><div style='width: 260px'>"
                f"<b>{label}</b><br>"
                f"{_pixmap_to_html_img(tip_pm)}<br>"
                f"<span style='color:#555;font-size:9pt;'>"
                f"Cathodal-first symmetric biphasic preview "
                f"(both phases share the shape; phase 2 is "
                f"this shape mirrored).</span></div></qt>"
            )
        self.shape_combo.setCurrentIndex(0)   # Rectangular by default
        self._refresh_shape_tooltip()
        self.shape_combo.currentIndexChanged.connect(self._on_shape_changed)
        # Refresh the box-level tooltip image whenever the user picks
        # a different shape (the dropdown-list icons are static).
        self.shape_combo.currentIndexChanged.connect(
            lambda *_: self._refresh_shape_tooltip())
        # Bump-count is hardcoded at 2 for SHAPE_SPEEDBUMPS — the user
        # asked to remove the per-shape spinbox. The widget still
        # exists internally so prefs round-trip cleanly, but it's not
        # added to the layout and not visible in the UI.
        self.bump_count = RepeatingSpinBox()
        self.bump_count.setRange(2, 2)
        self.bump_count.setValue(2)
        # Triphasic proportions: 3 small spinboxes. Range is 0.1 to
        # 100.0 — magnitudes only, no signed input, and STRICTLY
        # positive (zero would collapse a phase to no current and
        # break the strict-alternation sign invariant in
        # ``PulsePattern.triphasic``). The Polarity combo above
        # determines the actual sign of each phase (phase 1 takes
        # polarity, phase 2 the opposite, phase 3 polarity again).
        self.ratio_spins: List[RepeatingDoubleSpinBox] = []
        for v in self.DEFAULT_TRI_RATIO:
            sp = RepeatingDoubleSpinBox()
            sp.setRange(0.1, 100.0); sp.setDecimals(2); sp.setSingleStep(0.1)
            # Width sized to fit the widest legal value ("100.00") plus
            # the up/down arrow buttons with comfortable margin even at
            # higher Windows DPI scaling.
            sp.setValue(max(0.1, abs(v))); sp.setFixedWidth(110)
            self.ratio_spins.append(sp)

        # ------ asymmetric per-phase controls ------
        # Same hardware resolution as the symmetric path.
        self.phase_amp: List[QtWidgets.QDoubleSpinBox] = []
        self.phase_width: List[QtWidgets.QDoubleSpinBox] = []
        for i in range(3):
            self.phase_amp.append(self._dspin(*self.AMP_RANGE,
                                              -50.0 if i == 0 else 50.0,
                                              step=STIM_CURRENT_UI_STEP_UA,
                                              decimals=1, suffix=" " + rich.UA,
                                              force_sign=True))
            self.phase_width.append(self._dspin(*self.WIDTH_RANGE,
                                                DEFAULT_PHASE_WIDTH_US,
                                                step=STIM_TIME_RESOLUTION_US,
                                                decimals=0, suffix=" " + rich.US))
        # Asymmetric shape selector: just two options per the Cogan /
        # Liu convention. Rectangular = both phases rectangular (with
        # mismatched amp / width); Capacitively-coupled = rectangular
        # cathodic + exp-decay anodic with charge balance enforced by
        # the solver.
        self.asym_shape_combo = QtWidgets.QComboBox()
        # Same icon + HTML-tooltip treatment as the symmetric shape
        # combo — each list entry shows a small preview of the
        # asymmetric pulse it represents (rectangular vs
        # cap-coupled), and the closed combobox tooltip swaps to a
        # larger version on hover. Helps the user see at a glance
        # which mode they're picking BEFORE they commit to the
        # solver / asymmetric per-phase form.
        self.asym_shape_combo.setIconSize(QtCore.QSize(80, 28))
        self._asym_shape_tooltip_html: dict = {}
        _asym_blurbs = {
            ASYM_SHAPE_RECT: "Rectangular cathodic + rectangular anodic with "
                             "MISMATCHED amplitudes and widths (e.g. 1.0 µA × "
                             "1.0 µs cathodic balanced by 0.4 µA × 2.5 µs "
                             "anodic). Charge-balanced by construction.",
            ASYM_SHAPE_CAP:  "Rectangular cathodic + exp-decay anodic. "
                             "Anodic peaks at the cathodic amplitude (or "
                             "saturates at 1000 µA with a flat-top + decay "
                             "tail when the unconstrained peak would exceed "
                             "the hardware ceiling) and decays with "
                             "τ = anodic_width / 5 — the pseudo-"
                             "capacitively-coupled recharge profile, an "
                             "ideal-current-source approximation of the "
                             "true RC discharge described in "
                             "Cogan 2008 / Liu 2026.",
            ASYM_SHAPE_MIX_MATCH:
                             "Mix and match — pick any per-phase shape "
                             "independently from the two dropdowns "
                             "below. Auto-balance is shape-aware: the "
                             "panel uses each shape's duty factor when "
                             "computing the compensating amplitude or "
                             "width, so charge balance still holds "
                             "even when the two phases have different "
                             "shape factors (e.g. rect cathodic + "
                             "linear-decreasing anodic, Yip 2017's "
                             "GA-optimal cochlear-nerve waveform).",
        }
        for label, sid in ASYMMETRIC_BIPHASIC_SHAPES:
            icon_pm = _render_asym_shape_pixmap(sid, w_px=80, h_px=28)
            self.asym_shape_combo.addItem(QtGui.QIcon(icon_pm), label,
                                          userData=sid)
            tip_pm = _render_asym_shape_pixmap(sid, w_px=240, h_px=80)
            blurb = _asym_blurbs.get(sid, "")
            self._asym_shape_tooltip_html[sid] = (
                f"<qt><div style='width: 280px'>"
                f"<b>{label}</b><br>"
                f"{_pixmap_to_html_img(tip_pm)}<br>"
                f"<span style='color:#555;font-size:9pt;'>{blurb}</span>"
                f"</div></qt>"
            )
        self.asym_shape_combo.setCurrentIndex(0)
        self._refresh_asym_shape_tooltip()
        self.asym_shape_combo.currentIndexChanged.connect(
            self._on_asym_shape_changed)
        self.asym_shape_combo.currentIndexChanged.connect(
            lambda *_: self._refresh_asym_shape_tooltip())
        # Lock mode for cap-coupled: width-locked or amplitude-locked.
        # Default width-locked because users typically know how long
        # they want the recharge phase to last (timing-driven) more
        # often than they know the exact recharge peak amplitude.
        self.cap_lock_combo = QtWidgets.QComboBox()
        for label, lid in LOCK_MODE_OPTIONS:
            self.cap_lock_combo.addItem(label, userData=lid)
        self.cap_lock_combo.setCurrentIndex(0)
        self.cap_lock_combo.setToolTip(
            "Which phase-2 parameter the cap-coupled solver locks "
            "and which it derives.<br><br>"
            "<b>Lock width</b> — you set the recharge width; solver "
            "picks the amplitude that balances the charge.<br>"
            "<b>Lock amplitude</b> — you set the recharge peak; "
            "solver picks the width.<br><br>"
            "Width-locked is the default — most users know how "
            "long they want the recharge to last more often than "
            "they know the exact peak.")
        # ``_on_mode_changed`` re-evaluates the cap-coupled
        # lock state (greys out whichever phase-2 spinbox is being
        # auto-derived) AND ends with ``self._emit()``, so a single
        # connection covers both the visual lock update and the
        # downstream pattern rebuild. Was previously connected to
        # ``self._emit`` directly, which left the lock stale until
        # an unrelated control re-fired the visibility refresh.
        self.cap_lock_combo.currentIndexChanged.connect(
            self._on_mode_changed)
        # Live readout of derived τ + Q-balance for cap-coupled mode.
        # Updated each emit(); shown right of the lock combo.
        # ``RichText`` mode lets the saturated / infeasible
        # branches embed coloured spans (orange for saturation,
        # red for infeasibility) without restyling the whole label.
        self.cap_status_lbl = QtWidgets.QLabel("")
        self.cap_status_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.cap_status_lbl.setStyleSheet(
            "color: #1565c0; font-size: 9pt; padding-left: 6px;")

        # τ-mode combo + τ spinbox. Auto-derive (default) keeps the
        # legacy behaviour where the solver picks τ from t_a / N or
        # Q / (I_a · decay_factor) — backwards-compatible for every
        # session/.pat/.prefs that pre-dates this knob. Manual mode
        # lets users pin τ to a measured electrode time constant
        # (e.g. R_access · C_dl); the solver then re-derives the
        # *other* free parameter so charge balance still holds.
        # Decimals=1 prints "100.0 µs" — fine-grained enough to
        # tweak around an in-vivo measurement without overwhelming
        # the spinbox column with unhelpful precision.
        self.tau_mode_combo = QtWidgets.QComboBox()
        for label, tid in TAU_MODE_OPTIONS:
            self.tau_mode_combo.addItem(label, userData=tid)
        self.tau_mode_combo.setCurrentIndex(0)   # auto by default
        self.tau_mode_combo.currentIndexChanged.connect(
            self._on_tau_mode_changed)
        self.tau_mode_combo.setToolTip(
            "How the exp-decay time constant τ is picked.<br><br>"
            "<b>Auto</b> — solver derives τ from the locked phase-2 "
            "parameter and the charge-balance constraint. Use when "
            "you don't have a measured τ for the electrode.<br>"
            "<b>Manual</b> — pin τ to a value you typed (matched to "
            "EIS / current-step data, R_access · C_dl, or a model). "
            "The solver then re-derives the OTHER free parameter "
            "(width or amplitude) to keep charge balanced.")
        self.tau_us = self._dspin(TAU_MIN_US, TAU_MAX_US, TAU_DEFAULT_US,
                                  step=1.0, decimals=1)
        # Tooltip frames τ as a DESIGN parameter (the time
        # constant of the imitated RC discharge) rather than a
        # fitted electrode property. Cites the Weiland lumped
        # electrode model + the Srivastava/Troyk/Cogan AIROF
        # parameter set for two named values (fast pole and
        # slow zero) so users can pick a τ tied to a real
        # circuit model rather than a hand-wave. Real metal
        # electrodes exhibit constant-phase-element behaviour
        # — there is no single "true" τ — but for designing the
        # imitated decay these two characteristic values bracket
        # the regime cleanly. Empirical EIS / current-step data
        # for the user's specific electrode supersedes either.
        self.tau_us.setToolTip(
            "Anodic exp-decay time constant τ — the design knob "
            "for the imitated RC discharge.\n\n"
            "Named values from the Weiland lumped electrode "
            "model with AIROF parameters fit by Srivastava, "
            "Troyk, Cogan (EMBS 2004): C_dl = 0.012 µF, "
            "C_s = 0.1 µF, R_ct = 3.3 kΩ, R_st = 2.2 kΩ, "
            "R_a = R_b = 1.0 kΩ.\n\n"
            "  • Fast pole (interface settling):  τ ≈ 35 µs\n"
            "      = R_ct · (C_dl·C_s)/(C_dl+C_s)\n"
            "      Dominates the impulse response; settles by\n"
            "      ~5τ = 175 µs.\n"
            "  • Slow zero (Faradaic-branch RC): τ ≈ 330 µs\n"
            "      = R_ct · C_s\n"
            "      Governs the visible decay-tail envelope.\n"
            "  • Sputtered RuOx (Meyer, Cogan et al.; 200 µs\n"
            "      cathodic-pulse VT): the open-circuit recovery\n"
            "      (E_mc → 0) relaxes with τ ≈ 0.2–0.3 ms\n"
            "      (≈ 250 µs) — a good starting τ for a\n"
            "      RuOx-like pseudocapacitive coating.\n\n"
            "Real electrodes show CPE behaviour — no single τ "
            "fits. Pick the fast pole if you want stiff settling,\n"
            "the slow zero if you want a tail that resembles a\n"
            "typical AIROF discharge. Macroelectrodes scale up\n"
            "(R drops, C grows): expect τ in the 0.5–5 ms range.\n\n"
            "If you have an EIS / current-step measurement of "
            "your electrode, prefer that empirical τ over any of\n"
            "the named values above.\n\n"
            "References:\n"
            "  Weiland, Anderson, Humayun (2002). 'In vitro\n"
            "    electrical properties of iridium oxide versus\n"
            "    titanium nitride stimulating electrodes.'\n"
            "    IEEE Trans. Biomed. Eng. 49, 1574–1579.\n"
            "  Srivastava, Troyk, Cogan (2004). 'A laboratory\n"
            "    testing and driving system for AIROF\n"
            "    microelectrodes.' Proc. 26th Annual Int.\n"
            "    Conf. IEEE EMBS, San Francisco, pp. 4271–4274.\n\n"
            "Default 100 µs matches the auto-derived τ for a "
            "500 µs anodic width under the τ = t_a / 5 rule.")
        # ``valueChanged`` re-emits the pattern only when the
        # spinbox is enabled (i.e. manual mode); the
        # auto-mode update path is the read-back inside
        # ``pattern()`` after the solver runs, which calls
        # ``setValue`` with signals blocked.
        self.tau_us.valueChanged.connect(self._on_tau_value_changed)

        # ------ arbitrary-pattern controls ------
        # Sub-mode: Fixed (one period for all rows, 1-col table) or
        # Variable (per-row duration, 2-col table). Internal storage is
        # always (amp, duration) tuples — Fixed mode just substitutes
        # the shared period for every row's duration when pattern() is
        # built.
        self.arb_mode = QtWidgets.QComboBox()
        self.arb_mode.addItems([ARB_FIXED, ARB_VARIABLE])
        self.arb_mode.currentTextChanged.connect(self._on_arb_mode_changed)
        self.arb_mode.setToolTip(
            "Arbitrary-waveform authoring mode.<br><br>"
            "<b>Fixed period</b> — every row of the table uses the "
            "same width (the value in the Period spinbox). One "
            "column in the table, just amplitudes.<br>"
            "<b>Variable</b> — each row has its own width. Two "
            "columns in the table, (amplitude, duration) pairs. "
            "Use when the waveform needs unevenly-spaced "
            "breakpoints (e.g. action-potential mimics).")

        # Row count — auto-clamped to the per-mode hardware ceiling.
        self.arb_n_rows = RepeatingSpinBox()
        self.arb_n_rows.setRange(1, ARB_MAX_ROWS_FIXED)
        self.arb_n_rows.setValue(ARB_DEFAULT_ROWS)
        self.arb_n_rows.valueChanged.connect(self._on_arb_n_rows_changed)

        # Shared period for the Fixed sub-mode.
        self.arb_period_us = RepeatingDoubleSpinBox()
        self.arb_period_us.setRange(ARB_MIN_DURATION_US, ARB_MAX_DURATION_US)
        self.arb_period_us.setDecimals(0); self.arb_period_us.setSingleStep(1.0)
        self.arb_period_us.setValue(DEFAULT_PHASE_WIDTH_US)
        self.arb_period_us.setSuffix(" " + rich.US)
        self.arb_period_us.valueChanged.connect(self._emit)
        self.arb_period_us.setToolTip(
            "Width applied to every row of the arbitrary "
            "waveform when the authoring mode is Fixed period. "
            "Ignored in Variable mode (each row carries its own "
            "duration there). PlexStim 2.0 timing grid is 1 µs.")

        # Editable amplitude / duration table. The column count toggles
        # with the sub-mode. We pre-allocate ARB_MAX_ROWS_FIXED rows so
        # n_rows just changes which rows are *visible* — keeping any
        # off-screen values intact when the user later re-expands.
        self.arb_table = QtWidgets.QTableWidget(ARB_DEFAULT_ROWS, 2)
        self.arb_table.setHorizontalHeaderLabels([
            f"Amplitude [{rich.UA}]", f"Duration [{rich.US}]",
        ])
        hdr = self.arb_table.horizontalHeader()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.arb_table.verticalHeader().setDefaultSectionSize(22)
        self.arb_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
            QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked |
            QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed)
        self.arb_table.itemChanged.connect(lambda _it: self._emit())
        # Spreadsheet copy / cut / paste — author the (amp, dur) pairs
        # in Excel / Google Sheets and paste the whole block in one
        # shot. Single-column paste (just amplitudes, e.g. from a
        # CSV) lands in the Amplitude column when the user selects
        # column 0 first.
        enable_spreadsheet_paste(self.arb_table)

        # ------ delays + rate ------
        self.interphase_us = self._dspin(0.0, 5000.0, DEFAULT_INTERPHASE_DELAY_US,
                                         step=STIM_TIME_RESOLUTION_US, decimals=0,
                                         suffix=" " + rich.US)
        self.interphase_us.setToolTip(
            "Quiet interval between phase 1 and phase 2 (and, for "
            "triphasic, also between phase 2 and phase 3). Disabled "
            "when the ``Interphase delay`` checkbox to the right is "
            "off — the value is preserved across toggles.")
        self.discharge_us = self._dspin(0.0, 5000.0, DEFAULT_DISCHARGE_DELAY_US,
                                        step=STIM_TIME_RESOLUTION_US, decimals=0,
                                        suffix=" " + rich.US)
        self.discharge_us.setToolTip(
            "Post-pulse discharge / shorting interval — runs after "
            "the last active phase. Disabled when the ``Discharge "
            "delay`` checkbox to the right is off; the spinbox value "
            "is preserved across toggles.")
        # PlexStim 2.0 supports 0.008–100,000 pps. The actual ceiling is
        # ALSO bounded by the pulse width: one pulse + a 5 µs guaranteed
        # interpulse gap must fit inside one period.
        # _update_rate_max() recomputes the maximum after any
        # phase-width / interphase / discharge change.
        self.rate_pps = self._dspin(0.008, 100000.0, DEFAULT_RATE_PPS,
                                    suffix=" " + rich.PPS)
        self.rate_pps.setDecimals(3)
        self.rate_pps.setToolTip(
            "Pulse repetition rate. The maximum is bounded by the "
            "pulse width — one pulse + a 5 µs minimum interpulse gap "
            "must fit inside one period. Disabled when the "
            "``Interpulse delay`` checkbox is OFF (rate then pinned "
            "to the running max).")
        # Unit toggle — pps (rate) vs. s / ms / µs (period). Stored value
        # is always interpreted in the currently-selected unit; helpers
        # below convert to/from rate_hz at the pattern boundary.
        self.rate_unit_combo = QtWidgets.QComboBox()
        for u in self.RATE_UNIT_CHOICES:
            self.rate_unit_combo.addItem(u)
        self.rate_unit_combo.setCurrentText(self._rate_unit)
        self.rate_unit_combo.setToolTip(
            "Display unit for the rate spinbox. ``pps`` shows pulses-"
            "per-second; ``ms`` shows the equivalent period in "
            "milliseconds. The underlying rate (pps) is preserved "
            "when you toggle between units.")
        self.rate_unit_combo.currentTextChanged.connect(self._on_rate_unit_changed)
        # Approximate per-capture acquisition-time readout, sits to the
        # RIGHT of the unit combo (operator: "an output next to the pulse
        # rate to indicate what is the approximate acquisition time based
        # on the average count and the pulse rate").  The text SHOWS the
        # calculation (operator: "show the calculation"), e.g.
        # "64 ÷ 100 pps ≈ 0.64 s / capture".  Derived / read-only —
        # italicised (theme-safe: no hardcoded colour, just font style) so
        # it reads as a computed indicator, not an input.  Text is set by
        # :meth:`_update_acq_time_label`.
        self.acq_time_label = QtWidgets.QLabel("")
        self.acq_time_label.setObjectName("acq_time_label")
        _acqf = self.acq_time_label.font(); _acqf.setItalic(True)
        self.acq_time_label.setFont(_acqf)
        # Inline AVERAGE-COUNT editor beside the readout (operator: "add
        # an input that can change the average count").  Editing it emits
        # ``acqNavgEdited`` → MainWindow pushes the value into the Setup
        # tab's acq_navg_spin (the source of truth), whose signal chain
        # re-broadcasts to every tab.  ``set_acquisition_info`` keeps
        # this spin in sync (signal-guarded) and disables it in SAMPLE
        # mode, where the scope ignores the average count.
        self.acq_navg_inline = RepeatingSpinBox()
        self.acq_navg_inline.setObjectName("acq_navg_inline")
        self.acq_navg_inline.setRange(2, 512)
        self.acq_navg_inline.setValue(self._acq_n_avg)
        self.acq_navg_inline.setToolTip(
            "Oscilloscope average count (NUMAVg) used by the acquisition-"
            "time estimate to the right.  Editing here changes the SAME "
            "setting as Setup → Oscilloscope acquisition → Average count "
            "— the two stay in sync.")
        # Commit on Enter/return/focus-out (operator: "enter/return or clicking
        # out"), NOT per keystroke — editing this pushes the value into the
        # Setup spin, whose broadcast arms a scope round-trip; a per-keystroke
        # wiring would re-run that on every digit.
        self.acq_navg_inline.editingFinished.connect(
            self._on_inline_navg_changed)

        # Toggles for the three "have / pin" delay shortcuts. All three
        # use the "ON = HAS the delay" convention so a checked box reads
        # as the affirmative thing (per user spec):
        # * Interphase delay  — default ON; when off, T_iph forced to 0
        # * Discharge delay   — default ON; when off, T_dd forced to 0
        # * Interpulse delay  — default ON; when off, rate locked to
        #                       the maximum (1 e6 / (total + 5 µs)) and
        #                       the spinbox shows the running value.
        self.interphase_check = QtWidgets.QCheckBox("Interphase delay")
        self.interphase_check.setChecked(True)
        self.interphase_check.setToolTip(
            "Enable / disable the interphase delay. CHECKED keeps "
            "the spinbox to its left active and ``pattern()`` reads "
            "the value; UNCHECKED forces ``T_iph = 0`` while leaving "
            "the spinbox value preserved for later toggles.")
        self.interphase_check.toggled.connect(self._on_interphase_toggled)
        self.discharge_check = QtWidgets.QCheckBox("Discharge delay")
        self.discharge_check.setChecked(True)
        self.discharge_check.setToolTip(
            "Enable / disable the post-pulse discharge interval. "
            "CHECKED keeps the spinbox active and ``pattern()`` "
            "reads the value; UNCHECKED forces ``T_dd = 0``. The "
            "PlexStim auto-discharge mode (Connection panel) is a "
            "separate, hardware-side toggle.")
        self.discharge_check.toggled.connect(self._on_discharge_toggled)
        self.interpulse_check = QtWidgets.QCheckBox("Interpulse delay")
        self.interpulse_check.setChecked(True)
        self.interpulse_check.setToolTip(
            "Enable / disable a quiet interval between consecutive "
            "pulses. CHECKED lets you pick the rate freely (within "
            "the pulse-width ceiling). UNCHECKED forces 'no "
            "interpulse delay': the rate is pinned to the running "
            "maximum (1 / total_pulse_us) and the rate spinbox is "
            "disabled while the value tracks the pulse width live.")
        self.interpulse_check.toggled.connect(self._on_interpulse_toggled)

        # KHFAC discharge-as-short: REPLACE the existing trailing 0-µA
        # discharge step with a real PlexStim auto-discharge SHORT (no time
        # added).  Shown ONLY for a symmetric biphasic SINUSOIDAL pattern
        # (KHFAC); ENABLED only when Discharge Mode (auto-discharge) is on AND
        # a discharge delay exists.  When ON, pattern() stamps
        # ``interpulse_discharge_us`` = the discharge duration → build_pat_pairs
        # skips the 0-µA pair so the device idles + shorts it.
        self.interpulse_discharge_check = QtWidgets.QCheckBox(
            "Discharge between pulses (short, not 0 µA)")
        self.interpulse_discharge_check.setChecked(False)
        self.interpulse_discharge_check.setToolTip(
            "Continuous-sinusoidal (KHFAC) only — enabled only when Discharge "
            "Mode (auto-discharge) is On and a discharge delay is set.\n\n"
            "REPLACES the existing 0-µA discharge step between pulses with a "
            "REAL passive SHORT: the device idles the discharge duration "
            "(no time added) and the PlexStim auto-discharge drains the "
            "accumulated charge — real charge recovery / DC mitigation instead "
            "of a floating 0-µA step.\n\n"
            "The Ghazavi E_off / DC readout keeps computing (the discharge is "
            "windowed out).")
        self.interpulse_discharge_check.toggled.connect(self._emit)
        self.interpulse_discharge_check.setVisible(False)

        # ------ burst / pulse-train stimulation ------
        # Group ``pulses_per_burst`` pulses (at the intra-burst ``rate_hz``)
        # into a longer ``burst_period_us`` — burst stimulation (BurstDR /
        # theta-burst).  The whole group is HIDDEN unless the embedding tab
        # declares support (``set_burst_available`` — SP / CP / LP only; burst
        # is meaningless for VT, which ramps a single pulse's amplitude).
        # Availability flag; gated per experiment tab.  Starts False so a
        # freshly-built panel (or a VT tab) never emits a burst pattern.
        self._burst_available = False
        self.burst_enable_check = QtWidgets.QCheckBox(
            "Group pulses into bursts")
        self.burst_enable_check.setChecked(False)
        self.burst_enable_check.setToolTip(
            "Burst stimulation: deliver a GROUP of pulses (at the pulse rate "
            "above, the intra-burst rate) then idle for the rest of the burst "
            "period, repeating every burst period.  When OFF, ordinary "
            "continuous / periodic pulsing.")
        self.burst_enable_check.toggled.connect(self._on_burst_enable_toggled)
        # Pulses per burst — integer, ≥ 2 (1 = ordinary pulsing).
        self.pulses_per_burst_spin = RepeatingSpinBox()
        self.pulses_per_burst_spin.setRange(2, 999)
        self.pulses_per_burst_spin.setValue(5)
        self.pulses_per_burst_spin.setToolTip(
            "Number of pulses delivered in each burst, at the intra-burst "
            "pulse rate set above.")
        # Burst period in MILLISECONDS (the burst repeats every this long).
        # The whole burst (all N pulses) must fit inside this period.  Upper
        # bound 125 000 ms = the PlexStim PS_SetPeriod ceiling (device_period).
        # The MINIMUM is dynamically clamped to the burst span in
        # ``_refresh_burst`` so the burst is always valid (span ≤ period).
        self.burst_period_ms = self._dspin(
            0.02, 125000.0, 25.0, step=1.0, decimals=3, suffix=" ms")
        self.burst_period_ms.setToolTip(
            "Burst period — the burst (all its pulses) repeats every this "
            "long.  1000 / period(ms) = the burst repetition rate (Hz).  Must "
            "be long enough to fit all the pulses in the burst.")
        # Read-only italic readout: intra-rate, burst rate, inter-burst gap,
        # overall pulses/second.  Turns into a red warning when the burst is
        # too short to hold its pulses.
        self.burst_readout = QtWidgets.QLabel("")
        self.burst_readout.setWordWrap(True)
        _brf = self.burst_readout.font(); _brf.setItalic(True)
        self.burst_readout.setFont(_brf)
        self.pulses_per_burst_spin.valueChanged.connect(self._emit)
        self.burst_period_ms.valueChanged.connect(self._emit)

        # ------ charge-balance mode ------
        # Default to last-amp auto-balance per lab convention: when the
        # user enters asymmetric mode they almost always want the
        # second phase's amplitude rewritten so net charge is zero,
        # not the width (which controls timing of the next pulse).
        # Other options are still selectable.
        self.charge_mode = QtWidgets.QComboBox()
        self.charge_mode.addItems([CHARGE_BAL_AMP, CHARGE_BAL_WID, CHARGE_BAL_OFF])
        self.charge_mode.setCurrentText(CHARGE_BAL_AMP)
        self.charge_mode.setToolTip(
            "Charge-balance mode for asymmetric biphasic. "
            "``Auto-balance amplitude`` rewrites phase 2's amplitude "
            "so net charge is zero; ``Auto-balance width`` does the "
            "same with phase 2's width; ``Manual`` lets you type "
            "both freely (a header in the preview turns red if the "
            "imbalance exceeds 5 %).")

        # ------ assemble ------
        outer = QtWidgets.QVBoxLayout(self)
        # Tight margins so the pattern panel doesn't add visible empty
        # space below its last form row before the next sibling widget.
        outer.setContentsMargins(8, 4, 8, 4)
        # Spacing between top-level sections (top_form, sym/asym/arb,
        # delays, charge balance, discharge mode) is set to 0 so the
        # rows in different sub-forms read as one continuous list
        # instead of separating with an empty-line gap. The forms'
        # own ``verticalSpacing`` (2 px from ``rich.make_form``) still
        # provides per-row breathing room.
        outer.setSpacing(0)
        # Top row: phase count + symmetry + polarity. Symmetry is only
        # meaningful for biphasic — triphasic uses the ratio knobs to
        # express asymmetry — so we hide the symmetry combo whenever
        # phase_count == Triphasic. Stored as ``self._sym_label_widget``
        # so both the combo and its row label can hide together.
        self.top_form = rich.make_form()
        # Drop the form's default 4-px vertical contents margin so
        # the Pulse-style / Polarity rows butt directly against the
        # next section below (the symmetric / asymmetric / arbitrary
        # body) instead of leaving an empty-line gap between them.
        self.top_form.setContentsMargins(0, 0, 0, 0)
        top_phase = QtWidgets.QHBoxLayout()
        # Strip QHBoxLayout's default 9-px margins so the dropdown
        # sits flush against the left edge of ``tp_w``. Without this
        # the row's label (Pulse style:) and the row below it
        # (Polarity:) read as if they have different x-offsets, even
        # though both go through the same form-row label column.
        top_phase.setContentsMargins(0, 0, 0, 0)
        top_phase.setSpacing(4)
        top_phase.addWidget(self.phase_count, stretch=1)
        self._sym_separator = QtWidgets.QLabel(" / ")
        top_phase.addWidget(self._sym_separator)
        top_phase.addWidget(self.symmetry, stretch=1)
        tp_w = QtWidgets.QWidget(); tp_w.setLayout(top_phase)
        self.top_form.addRow("Pulse style:", tp_w)
        # Polarity row uses a manually-created QLabel (rather than
        # letting addRow auto-create one from a string) so we can
        # detach + reinsert the row cleanly via
        # ``QFormLayout.takeRow`` when the panel switches between
        # symmetric and asymmetric modes. In asymmetric mode the
        # polarity row migrates into ``asym_box``'s form (below the
        # Asymmetric-shape row) so the user reads
        # "Asymmetric shape → Polarity" together; symmetric mode
        # leaves it here in ``top_form`` so the order is
        # "Pulse style → Polarity → Phase shape".
        self._polarity_label = QtWidgets.QLabel("Polarity:")
        self.top_form.addRow(self._polarity_label, self.polarity)
        #: Current location of the polarity row — ``"top"`` (in
        #: ``top_form``) or ``"asym"`` (in the asym_box form).
        #: Tracked so ``_move_polarity_row`` knows which form to
        #: detach from before re-inserting.
        self._polarity_location = "top"
        outer.addLayout(self.top_form)

        # Symmetric / triphasic-ratio panel
        self.sym_box = QtWidgets.QWidget()
        self._sym_form = rich.make_form(self.sym_box)
        self._sym_form.setContentsMargins(0, 0, 0, 0)
        # Phase-shape row — placed FIRST in the symmetric form so it
        # sits directly below the Polarity row (which lives in the
        # parent ``top_form`` and renders above ``sym_box``). Per
        # user spec the shape selector should sit near the polarity
        # control since the two interact (the preview icons flip
        # sign when polarity changes). Visible only in biphasic
        # symmetric mode; triphasic stays rectangular by lab
        # convention. Stash the row label so
        # ``_on_phase_count_changed`` can hide both widgets together.
        self._shape_row_label = QtWidgets.QLabel("Phase shape:")
        self._sym_form.addRow(self._shape_row_label, self.shape_combo)
        # Amplitude row label is dynamic — "Stimulation current (I) [μA]:"
        # for biphasic, "Amplitude factor (I) [μA]:" for triphasic.
        self._amp_row_label = QtWidgets.QLabel()
        self._amp_row_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._sym_form.addRow(self._amp_row_label, self.amp_excite)
        # Inline warning shown when the I_mon-trigger minimum-magnitude
        # constraint clamps a 0 µA entry (operator: "When Imon is the trigger
        # source, current cannot be 0").  Red so it's visible on either theme
        # (NOT palette(mid) — invisible on dark, gotchas #87/#135).
        self._amp_min_mag_ua = 0.0        # 0 = unconstrained (digital trigger)
        self._amp_trigger_warn = QtWidgets.QLabel("")
        self._amp_trigger_warn.setWordWrap(True)
        self._amp_trigger_warn.setStyleSheet(
            "color: #c62828; font-size: 9pt; padding: 1px 4px;")
        self._amp_trigger_warn.setVisible(False)
        self._sym_form.addRow(self._amp_trigger_warn)
        self._sym_form.addRow(
            rich.field_label("Phase width", rich.T_PH, rich.US),
            self.width_shared,
        )
        # Slope readout for symmetric mode — shows the µA/µs slope of
        # each phase when the selected shape is a linear variant
        # (linear_increasing, linear_decreasing, linear_inc_dec,
        # linear_dec_inc). Empty for non-linear shapes. Helps users
        # design experiments where the rate-of-change of current is
        # what matters (e.g. Sahin & Tie 2007's strength-duration
        # arguments where the chronaxie depends on dI/dt).
        # ``RichText`` so the slope can use proper µ glyphs and
        # arrows. The label keeps a hidden state when no slope info
        # is relevant (non-linear shape selected) so the form
        # layout doesn't leave an empty row.
        self._sym_slope_lbl = QtWidgets.QLabel("")
        self._sym_slope_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._sym_slope_lbl.setWordWrap(True)
        self._sym_slope_lbl.setStyleSheet(
            "QLabel { color: #2e3b4e; font-size: 9pt; "
            "background: #f3f6fa; "
            "border: 1px solid #c7d2e0; "
            "border-radius: 4px; "
            "padding: 6px 8px; }"
        )
        self._sym_slope_label_widget = QtWidgets.QLabel("Slope:")
        self._sym_form.addRow(self._sym_slope_label_widget,
                              self._sym_slope_lbl)
        # Offset spinbox — baseline floor amplitude that the shape
        # never drops below. Turns linear-increasing into
        # trapezoidal-increasing (ramps from offset to peak
        # rather than 0 to peak); applies generically to any
        # non-rectangular shape. Hidden when the selected shape
        # is rectangular (offset is meaningless for a constant-
        # amplitude waveform).
        # Range: 0 .. STIM_MAX_AMPLITUDE_UA so users can't type a
        # value above the hardware ceiling. Default 0 (no offset
        # — current behaviour for all existing pulses).
        self.sym_offset_ua = self._dspin(
            0.0, STIM_MAX_AMPLITUDE_UA, 0.0,
            step=STIM_CURRENT_UI_STEP_UA, decimals=1,
            suffix=" " + rich.UA)
        self.sym_offset_ua.setToolTip(
            "Baseline FLOOR amplitude (magnitude) that the shape "
            "never drops below. Turns a linear-increasing ramp "
            "into a TRAPEZOIDAL waveform that ramps from "
            "``offset`` to peak rather than from 0 to peak; "
            "applies to any non-rectangular shape (exp / Gaussian "
            "/ sin / halfpipe / bowtie / linear). Sign auto-"
            "matches the phase polarity. Set to 0 for the "
            "historical 0-to-peak shapes.")
        self.sym_offset_ua.valueChanged.connect(self._emit)
        # ENABLE checkbox (operator: "a checkbox to enable the current offset
        # under certain shapes").  The offset is opt-in: unchecked → the
        # spinbox is disabled and the offset reads 0 (historical no-offset
        # behaviour).  The checkbox IS the form row's label widget, so the
        # existing non-rectangular shape-visibility code (which toggles
        # ``_sym_offset_label``) shows/hides it — the checkbox appears ONLY
        # for non-rectangular shapes ("under certain shapes").
        self.sym_offset_enable_chk = QtWidgets.QCheckBox("Current offset")
        self.sym_offset_enable_chk.setChecked(False)          # default OFF
        self.sym_offset_enable_chk.setToolTip(
            "Enable a baseline FLOOR current the shape ramps to / from "
            "instead of zero (a pedestal — turns a ramp / sine / gaussian "
            "into a trapezoidal-style waveform).  Only applies to "
            "non-rectangular shapes; unchecked = no offset (0 to peak).")
        self.sym_offset_ua.setEnabled(False)                  # gated by the box
        self.sym_offset_enable_chk.toggled.connect(
            self._on_sym_offset_enable_toggled)
        self._sym_offset_label = self.sym_offset_enable_chk   # row label = box
        self._sym_form.addRow(self._sym_offset_label, self.sym_offset_ua)
        # τ spinbox — time constant for the exponential shapes
        # (decay / increasing) and the exp-pair entries. Hidden
        # otherwise; the canonical τ = W / N (N = 5) derivation
        # remains the fallback when this value is 0.
        self.sym_tau_us = self._dspin(
            0.0, TAU_MAX_US, 0.0,
            step=1.0, decimals=1)
        self.sym_tau_us.setToolTip(
            "Time constant τ for the exponential shapes (decay / "
            "increasing / exp pairs). Default 0 falls back to the "
            "canonical τ = W / 5 derivation; set non-zero to pin "
            "τ explicitly (matches a measured electrode RC, etc.).")
        self.sym_tau_us.valueChanged.connect(self._emit)
        self._sym_tau_label = QtWidgets.QLabel(
            f"Time constant ({getattr(rich, 'TAU', 'τ')}) [{rich.US}]:")
        self._sym_form.addRow(self._sym_tau_label, self.sym_tau_us)
        # Speedbump-count row was removed — bump_count is hardcoded
        # at 2 internally. The placeholder labels below stay for
        # backward-compat with old visibility plumbing that referenced
        # them; they're never added to a form so they never render.
        self._bump_row_label = QtWidgets.QLabel("Speedbump count:")
        self._bump_row_label.setVisible(False)
        self.bump_count.setVisible(False)
        # Triphasic-only ratio row. The three magnitude spinboxes
        # share one row; on the row immediately BELOW we surface a
        # readout that (a) shows the SIGNED pattern that will actually
        # be applied — phase 1 = polarity, phase 2 = OPPOSITE polarity,
        # phase 3 = polarity — making the alternating-sign convention
        # in :meth:`PulsePattern.triphasic` visible to the user, and
        # (b) tags which phase carries the largest |Q_ph| (the
        # *excitation* phase the ramp scales against). Equal phase
        # widths in the symmetric path means largest |Q_ph| = largest
        # |ratio entry|.
        ratio_row = QtWidgets.QHBoxLayout()
        ratio_row.setContentsMargins(0, 0, 0, 0)
        ratio_row.setSpacing(4)
        for sp in self.ratio_spins:
            ratio_row.addWidget(sp)
        ratio_row.addStretch(1)
        self.ratio_w = QtWidgets.QWidget(); self.ratio_w.setLayout(ratio_row)
        self._sym_form.addRow("Triphasic ratio (a : b : c):", self.ratio_w)
        # Excitation / applied-pattern readout — moved below the
        # ratio row per user request so the spinboxes get the full
        # row width and the readout has its own dedicated line.
        self.ratio_excitation_label = rich.make_label("")
        self.ratio_excitation_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.ratio_excitation_label.setStyleSheet(
            "color: #1565c0; font-size: 9pt;")
        self._sym_form.addRow("", self.ratio_excitation_label)
        # Refresh the readout whenever any ratio entry OR the
        # polarity selector changes — the sign pattern depends on
        # both. (The polarity change connection is added later, after
        # ``self.polarity`` is hooked up.)
        for sp in self.ratio_spins:
            sp.valueChanged.connect(self._refresh_ratio_excitation_label)
        self._refresh_ratio_excitation_label()
        outer.addWidget(self.sym_box)

        # Asymmetric panel — top row picks the asymmetric biphasic
        # shape (Rectangular vs Capacitively-coupled), then the lock-
        # mode + tau readout for cap-coupled, then the per-phase
        # amp/width rows.
        self.asym_box = QtWidgets.QWidget()
        af = rich.make_form(self.asym_box)
        # Stash on self so other methods (notably
        # ``_move_polarity_row``) can insert / remove rows
        # without recomputing the form layout reference.
        self._asym_form = af
        af.setContentsMargins(0, 0, 0, 0)
        # Tighten the asymmetric form's row spacing — Phase 1 / 2 / 3
        # rows have identical structure (label + amp + width) and
        # the user reads them as a stacked group, so the default
        # 2-px verticalSpacing reads as wasted air between
        # otherwise-uniform rows.
        af.setVerticalSpacing(0)
        # Asymmetric-shape row
        self._asym_shape_label = QtWidgets.QLabel("Asymmetric shape:")
        af.addRow(self._asym_shape_label, self.asym_shape_combo)
        # Mix-and-match per-phase shape pickers — visible only when
        # the asymmetric shape is "mix_match". Lets the user pick
        # any per-phase shape independently (e.g. Yip 2017 GA-
        # optimal: rect cathodic + linear-decreasing anodic). The
        # auto-balance arithmetic in ``PulsePattern.auto_balance``
        # is shape-aware (uses ``_shape_duty`` per phase) so charge
        # balance still holds even when the two phases have
        # different shape-duty factors.
        # Population: same set of single-phase shapes available in
        # SYMMETRIC mode (rectangular, linear inc/dec, sin,
        # speedbumps, bowtie, halfpipe, gaussian, exp inc/dec).
        # Excludes the symmetric-only shape PAIRS (linear_inc_dec
        # etc.) since those don't make sense as a single-phase
        # selection.
        _MIX_MATCH_SHAPES = (
            ("Rectangular",       SHAPE_RECTANGULAR),
            ("Linear increasing", SHAPE_LINEAR_INCREASING),
            ("Linear decreasing", SHAPE_LINEAR_DECREASING),
            ("Sinusoidal",        SHAPE_SINUSOIDAL),
            ("Speedbumps",        SHAPE_SPEEDBUMPS),
            ("Bowtie",            SHAPE_BOWTIE),
            ("Halfpipe",          SHAPE_HALFPIPE),
            ("Gaussian",          SHAPE_GAUSSIAN),
            ("Exp decreasing",    SHAPE_EXP_DECAY),
            ("Exp increasing",    SHAPE_EXP_INCREASING),
        )
        self.mix_phase_shape_combo: List[QtWidgets.QComboBox] = []
        # Per-phase polarity from the current polarity dropdown so the
        # icons render in the correct y-half on first paint. Phase 1
        # carries the overall polarity sign; phase 2 carries the
        # opposite (the recharge phase). ``_refresh_shape_combo_icons``
        # below regenerates all of these when the user flips the
        # polarity dropdown.
        cath_first = self.polarity.currentText().startswith("Cathod")
        phase_signs = (-1, +1) if cath_first else (+1, -1)
        for i in range(2):
            cb = QtWidgets.QComboBox()
            # Same shape-preview treatment as the symmetric biphasic
            # dropdown: a small icon next to each label shows the
            # single-phase waveform in that shape. Helps users
            # visualise the per-phase choice without flipping
            # through the dropdown blind.
            cb.setIconSize(QtCore.QSize(60, 24))
            # Filter out profile-restricted shapes (halfpipe, bowtie,
            # speedbumps) for the per-phase mix-and-match dropdown
            # too — same gate as the symmetric combo above.
            for label, sid in self._filter_restricted_shapes(
                    _MIX_MATCH_SHAPES):
                # Render a SINGLE-PHASE preview (not the
                # cathodic+anodic biphasic that
                # ``_render_shape_pixmap`` produces) so each
                # mix-and-match icon shows what THIS phase
                # would carry under that selection. Use
                # ``_render_single_phase_pixmap`` for that.
                icon_pm = _render_single_phase_pixmap(
                    sid, w_px=60, h_px=24, polarity=phase_signs[i])
                cb.addItem(QtGui.QIcon(icon_pm), label, userData=sid)
            cb.setCurrentIndex(0)   # rectangular by default
            cb.currentIndexChanged.connect(self._emit)
            self.mix_phase_shape_combo.append(cb)
        # Pack the two pickers into a single horizontal row.
        mix_row = QtWidgets.QHBoxLayout()
        mix_row.setContentsMargins(0, 0, 0, 0)
        mix_row.setSpacing(4)
        mix_row.addWidget(QtWidgets.QLabel("Phase 1:"))
        mix_row.addWidget(self.mix_phase_shape_combo[0], stretch=1)
        mix_row.addSpacing(8)
        mix_row.addWidget(QtWidgets.QLabel("Phase 2:"))
        mix_row.addWidget(self.mix_phase_shape_combo[1], stretch=1)
        self._mix_phase_row_w = QtWidgets.QWidget()
        self._mix_phase_row_w.setLayout(mix_row)
        self._mix_phase_row_label = QtWidgets.QLabel("Per-phase shapes:")
        af.addRow(self._mix_phase_row_label, self._mix_phase_row_w)
        # Per-phase offset spinboxes for mix-and-match. Each phase
        # gets its own offset (so the user can have e.g. a flat
        # rect cathodic + a trapezoidal-rising anodic). Spinboxes
        # are individually hidden when the corresponding phase's
        # shape is rectangular — handled in
        # ``_refresh_mix_offset_visibility``. The whole row hides
        # outside mix-and-match mode (driven by
        # ``_on_asym_shape_changed`` / ``_on_mode_changed``).
        self.mix_phase_offset_ua: List[QtWidgets.QDoubleSpinBox] = []
        for i in range(2):
            sp = self._dspin(
                0.0, STIM_MAX_AMPLITUDE_UA, 0.0,
                step=STIM_CURRENT_UI_STEP_UA, decimals=1,
                suffix=" " + rich.UA)
            sp.setToolTip(
                f"Baseline FLOOR magnitude for phase {i + 1}. "
                f"Turns non-rectangular shapes into trapezoidal "
                f"variants. Ignored for rectangular phases (the "
                f"spinbox is hidden in that case).")
            sp.valueChanged.connect(self._emit)
            self.mix_phase_offset_ua.append(sp)
        mix_offset_row = QtWidgets.QHBoxLayout()
        mix_offset_row.setContentsMargins(0, 0, 0, 0)
        mix_offset_row.setSpacing(4)
        self._mix_offset_p0_label = QtWidgets.QLabel("Phase 1:")
        mix_offset_row.addWidget(self._mix_offset_p0_label)
        mix_offset_row.addWidget(self.mix_phase_offset_ua[0], stretch=1)
        mix_offset_row.addSpacing(8)
        self._mix_offset_p1_label = QtWidgets.QLabel("Phase 2:")
        mix_offset_row.addWidget(self._mix_offset_p1_label)
        mix_offset_row.addWidget(self.mix_phase_offset_ua[1], stretch=1)
        self._mix_offset_row_w = QtWidgets.QWidget()
        self._mix_offset_row_w.setLayout(mix_offset_row)
        self._mix_offset_row_label = QtWidgets.QLabel("Per-phase offset:")
        af.addRow(self._mix_offset_row_label, self._mix_offset_row_w)
        # Wire per-shape combo changes to refresh offset visibility
        # so the per-phase offset spinbox hides whenever the user
        # picks a rectangular shape for that phase.
        for cb in self.mix_phase_shape_combo:
            cb.currentIndexChanged.connect(
                self._refresh_mix_offset_visibility)
        # Cap each offset spinbox at |amplitude| − one step so the
        # offset transform inside ``shape_breakpoints`` never
        # collapses the shape to a flat line (which happens when
        # offset == |A|: the linear blend ``so + (A − so) · …``
        # degenerates to ``so`` for every sample, making LIN_INC /
        # SIN / EXP_* visually rectangular). Re-evaluated whenever
        # the user changes the corresponding ``phase_amp`` spinbox.
        for i in range(2):
            self.phase_amp[i].valueChanged.connect(
                lambda _v, idx=i: self._refresh_mix_offset_max(idx))
            # Initial cap so a fresh panel doesn't allow the
            # degenerate offset == amp configuration straight away.
            QtCore.QTimer.singleShot(
                0, lambda idx=i: self._refresh_mix_offset_max(idx))
        # Locked-parameter min/max readout — shows the valid
        # range for whichever phase-2 knob is auto-derived under
        # the current shape + charge-balance / lock combo
        # (e.g. in rect-asym CHARGE_BAL_AMP the user types t_a
        # and we report its feasibility window so the auto-
        # derived |I_a| stays within the 1000-µA hardware cap).
        # The widget keeps its historical attribute name
        # ``_asym_formula_lbl`` for layout-stability across the
        # rename — the actual content is now bounds, not
        # formulas. See :meth:`_refresh_locked_param_bounds`.
        # ``RichText`` mode lets us render the readout with
        # italic variable names + sub/superscripts via the
        # ``rich`` helpers.
        self._asym_formula_lbl = QtWidgets.QLabel("")
        self._asym_formula_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._asym_formula_lbl.setWordWrap(True)
        self._asym_formula_lbl.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        self._asym_formula_lbl.setStyleSheet(
            "QLabel { color: #2e3b4e; font-size: 9pt; "
            "background: #f3f6fa; "
            "border: 1px solid #c7d2e0; "
            "border-radius: 4px; "
            "padding: 6px 8px; }"
        )
        self._asym_formula_label_widget = QtWidgets.QLabel("Locked range:")
        af.addRow(self._asym_formula_label_widget,
                  self._asym_formula_lbl)
        # Lock-mode + tau-readout row — visible only when cap-coupled
        cap_row = QtWidgets.QHBoxLayout()
        cap_row.setContentsMargins(0, 0, 0, 0)
        cap_row.setSpacing(4)
        cap_row.addWidget(self.cap_lock_combo)
        cap_row.addWidget(self.cap_status_lbl, stretch=1)
        self._cap_lock_row_w = QtWidgets.QWidget()
        self._cap_lock_row_w.setLayout(cap_row)
        self._cap_lock_label = QtWidgets.QLabel("Cap-coupled:")
        af.addRow(self._cap_lock_label, self._cap_lock_row_w)
        # τ row — sits directly under the lock row so the user
        # sees the related controls grouped together. Mode combo
        # on the left, τ spinbox on the right; spinbox is
        # enable/disabled by ``_on_tau_mode_changed`` to mirror
        # the Auto/Manual selection.
        tau_row = QtWidgets.QHBoxLayout()
        tau_row.setContentsMargins(0, 0, 0, 0)
        tau_row.setSpacing(4)
        tau_row.addWidget(self.tau_mode_combo)
        tau_row.addWidget(self.tau_us, stretch=1)
        self._cap_tau_row_w = QtWidgets.QWidget()
        self._cap_tau_row_w.setLayout(tau_row)
        # rich.TAU is the rendered τ glyph already used elsewhere
        # in the GUI; falling back to the literal "τ" if the
        # helper isn't loaded keeps the panel constructible
        # in head-less / partial-import test contexts.
        self._cap_tau_label = QtWidgets.QLabel(
            f"Time constant ({getattr(rich, 'TAU', 'τ')}) [{rich.US}]:")
        af.addRow(self._cap_tau_label, self._cap_tau_row_w)
        # Per-phase amp/width rows
        self._phase_rows: List[QtWidgets.QWidget] = []
        for i in range(3):
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            # Use the project's standard "Name [unit]:" form so the
            # asymmetric rows match the rest of the parameter forms.
            row.addWidget(QtWidgets.QLabel(f"Amplitude [{rich.UA}]:"))
            row.addWidget(self.phase_amp[i], stretch=1)
            row.addSpacing(8)
            row.addWidget(QtWidgets.QLabel(f"Width [{rich.US}]:"))
            row.addWidget(self.phase_width[i], stretch=1)
            w = QtWidgets.QWidget(); w.setLayout(row)
            self._phase_rows.append(w)
            af.addRow(f"Phase {i+1}:", w)
        outer.addWidget(self.asym_box)

        # Arbitrary-pattern body
        self.arb_box = QtWidgets.QWidget()
        arb_form = rich.make_form(self.arb_box)
        arb_form.setContentsMargins(0, 0, 0, 0)
        arb_form.addRow("Sampling:", self.arb_mode)
        # Row count + period sit on the same row so the user can see
        # both at a glance. The period label hides itself when the
        # sub-mode is Variable (it's not used there).
        rp_row = QtWidgets.QHBoxLayout()
        rp_row.setContentsMargins(0, 0, 0, 0)
        rp_row.setSpacing(4)
        rp_row.addWidget(QtWidgets.QLabel("Rows:"))
        rp_row.addWidget(self.arb_n_rows)
        rp_row.addSpacing(12)
        # ``Pulse period (T_pulse) [µs]:`` — same abbreviation as
        # the main rate-row label so a user reading the
        # arbitrary-pattern form recognises the variable name from
        # the rectangular / cap-coupled paths above.
        self._arb_period_label = QtWidgets.QLabel(
            rich.field_label("Pulse period", rich.T_PULSE, rich.US))
        rp_row.addWidget(self._arb_period_label)
        rp_row.addWidget(self.arb_period_us)
        rp_row.addStretch(1)
        rp_w = QtWidgets.QWidget(); rp_w.setLayout(rp_row)
        arb_form.addRow("", rp_w)
        arb_form.addRow(self.arb_table)
        outer.addWidget(self.arb_box)

        # Delays + rate. Each row is spinbox + a per-row checkbox that
        # short-circuits the spinbox value (no-interphase / discharge /
        # no-interpulse). Wrap each row in a QHBoxLayout so the
        # checkbox sits flush right of its spinbox.
        delays = rich.make_form()
        delays.setContentsMargins(0, 0, 0, 0)
        # Drop the form's default 2-px verticalSpacing — interphase /
        # discharge / pulse-rate rows read as a tightly-coupled block
        # and the spinbox-row height already provides plenty of
        # visual separation. Same treatment applied to the charge-
        # balance + discharge-mode forms below so the bottom of the
        # panel doesn't accumulate empty-row gaps.
        delays.setVerticalSpacing(0)

        # Toggles are LEFT-anchored: each delay-row checkbox sits
        # immediately after the row label, before the spinbox.  The
        # three checkboxes share the same x-coordinate because they
        # all occupy the first slot of each row's field area.  The
        # rate row's unit combo (Hz / kHz / period) still follows
        # the spinbox on the right.
        rate_row = QtWidgets.QHBoxLayout()
        rate_row.setContentsMargins(0, 0, 0, 0)
        rate_row.setSpacing(4)
        rate_row.addWidget(self.interpulse_check)
        rate_row.addWidget(self.rate_pps, stretch=1)
        rate_row.addWidget(self.rate_unit_combo)
        # NOTE: the average-count editor + the acquisition-time readout
        # used to sit here on the rate row, but the (verbose) per-capture
        # calculation widened the whole panel — they now live on their own
        # "Average count" row just below (operator: "the calculation of
        # time per capture is making the panel too wide … Move the Average
        # count to below the pulse rate with the calculation on its right").
        rate_w = QtWidgets.QWidget(); rate_w.setLayout(rate_row)

        iph_row = QtWidgets.QHBoxLayout()
        iph_row.setContentsMargins(0, 0, 0, 0)
        iph_row.setSpacing(4)
        iph_row.addWidget(self.interphase_check)
        iph_row.addWidget(self.interphase_us, stretch=1)
        iph_w = QtWidgets.QWidget(); iph_w.setLayout(iph_row)
        delays.addRow(rich.field_label("Interphase delay", rich.T_IPH, rich.US),
                      iph_w)

        dd_row = QtWidgets.QHBoxLayout()
        dd_row.setContentsMargins(0, 0, 0, 0)
        dd_row.setSpacing(4)
        dd_row.addWidget(self.discharge_check)
        dd_row.addWidget(self.discharge_us, stretch=1)
        dd_w = QtWidgets.QWidget(); dd_w.setLayout(dd_row)
        delays.addRow(rich.field_label("Discharge delay", rich.T_DD, rich.US),
                      dd_w)
        # Row label flips between "Pulse rate (f_stim)" and
        # "Pulse period (T_pulse)" when the user toggles the unit
        # combo. We construct the QLabel by hand (rather than
        # letting QFormLayout build one from a string) so we
        # keep a handle and can rewrite its text later. The
        # abbreviations come from :mod:`stimtest.gui.rich` so a
        # future label flip picks up the same canonical form.
        self._rate_row_label = QtWidgets.QLabel(
            rich.field_label("Pulse rate", rich.F_STIM, rich.PPS))
        self._rate_row_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        delays.addRow(self._rate_row_label, rate_w)
        # Average count on its OWN form row directly below the pulse rate,
        # with the per-capture acquisition-time calculation to its right
        # (operator: "Move the Average count to below the pulse rate with
        # the calculation on its right").  Keeping the acq-time readout off
        # the rate row is what stops it widening the whole panel.
        avg_row = QtWidgets.QHBoxLayout()
        avg_row.setContentsMargins(0, 0, 0, 0)
        avg_row.setSpacing(6)
        avg_row.addWidget(self.acq_navg_inline)
        avg_row.addWidget(self.acq_time_label, stretch=1)
        avg_w = QtWidgets.QWidget(); avg_w.setLayout(avg_row)
        delays.addRow("Average count", avg_w)
        outer.addLayout(delays)

        # ------ burst / pulse-train group (below the timing form) ------
        # Hidden by default; ``set_burst_available`` reveals it on SP/CP/LP.
        self.burst_group = QtWidgets.QGroupBox("Burst stimulation (pulse train)")
        _burst_form = QtWidgets.QFormLayout(self.burst_group)
        _burst_form.setContentsMargins(8, 4, 8, 4)
        _burst_form.setVerticalSpacing(4)
        _burst_form.addRow(self.burst_enable_check)
        _burst_form.addRow(
            rich.field_label("Pulses per burst", rich.var("N", "burst")),
            self.pulses_per_burst_spin)
        _burst_form.addRow(
            rich.field_label("Burst period", rich.var("T", "burst")),
            self.burst_period_ms)
        _burst_form.addRow(self.burst_readout)
        self.burst_group.setVisible(False)
        outer.addWidget(self.burst_group)
        # KHFAC interpulse-discharge toggle (hidden unless a symmetric
        # biphasic sinusoid is active — see _refresh_interpulse_discharge).
        outer.addWidget(self.interpulse_discharge_check)
        # Initial enabled-state (spins greyed until the box is ticked).
        self._on_burst_enable_toggled(self.burst_enable_check.isChecked())

        # Auto-discharge mode toggle. Constructed here but laid out at
        # the BOTTOM of the panel (after the charge-balance row) per
        # user spec — keeps the run-affecting policy distinct from
        # the per-pulse timing fields above. Moved out of the
        # Connection panel so the pulse-pattern + discharge-policy
        # decision lives in one place. Default ON (the safe state);
        # the connection panel pushes the persisted preference back
        # into this checkbox at startup. Toggling fires
        # ``autoDischargeToggled``; the main window picks that up to
        # (a) sync sibling pattern panels in other experiment tabs
        # and (b) push to the live PlexStim device via the
        # connection panel.
        self.auto_discharge_combo = QtWidgets.QComboBox()
        # Each entry's userData is the underlying boolean state pushed
        # to ``Stimulator.set_auto_discharge`` and persisted in prefs.
        # Index 0 = ON (the safe default); index 1 = OFF.
        self.auto_discharge_combo.addItem("On",  userData=True)
        self.auto_discharge_combo.addItem("Off", userData=False)
        self.auto_discharge_combo.setCurrentIndex(0)
        # The two entries (``On`` / ``Off``) are short enough that the
        # default field-growth policy ballooned the combo across the
        # whole field column. Pin a tight max-width so it sits flush
        # against the label without consuming dead space.
        self.auto_discharge_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Fixed)
        self.auto_discharge_combo.setFixedWidth(80)
        self.auto_discharge_combo.setToolTip(
            "Discharge mode. ``On`` (default) — the stimulator "
            "actively shorts the electrode between pulses. ``Off`` — "
            "the electrode floats; residual charge per-pulse rounding "
            "accumulates and can drift the electrode-tissue interface "
            "out of the safe water window. Switching to Off the first "
            "time in a session raises a confirmation dialog. State is "
            "device-wide and synchronised across every experiment "
            "tab's parameters page.")
        # First-time-disable warning: the dialog fires once per
        # session unless the user re-enables (which re-arms it).
        self._auto_discharge_warned = False
        self.auto_discharge_combo.currentIndexChanged.connect(
            self._on_auto_discharge_combo_changed)

        # Apply initial enabled-state for the three checkboxes.
        self._on_interphase_toggled(self.interphase_check.isChecked())
        self._on_discharge_toggled(self.discharge_check.isChecked())
        self._on_interpulse_toggled(self.interpulse_check.isChecked())
        # Initial arbitrary-table layout (column count + period
        # visibility for the default Fixed sub-mode).
        self._on_arb_mode_changed(self.arb_mode.currentText())

        # Charge balance — only shown for biphasic + asymmetric (the only
        # mode where the user has independent control over both phase
        # amps/widths and might wind up with a net-charge imbalance).
        # Symmetric biphasic always balances by mirroring; triphasic
        # uses ratio knobs that the user can hand-tune to balance.
        self._charge_form = rich.make_form()
        self._charge_form.setContentsMargins(0, 0, 0, 0)
        self._charge_form.setVerticalSpacing(0)
        self._charge_form.addRow("Charge balance:", self.charge_mode)
        # Wrap in a widget so the whole row can be hidden together.
        self._charge_w = QtWidgets.QWidget()
        self._charge_w.setLayout(self._charge_form)
        outer.addWidget(self._charge_w)
        # Inline warning label that fires when the auto-balance
        # path can't satisfy charge balance within the device's
        # hardware envelope — i.e. ``last_amp`` adjustment would
        # need to exceed ``STIM_MAX_AMPLITUDE_UA``, or
        # ``last_width`` adjustment would need an unreasonably
        # long phase. Updated as a side-effect of ``pattern()``;
        # cleared whenever the balance is achievable. Shown
        # regardless of whether the user has the charge-balance
        # combo open, so a misconfiguration is loud.
        self._asym_balance_warn_lbl = QtWidgets.QLabel("")
        self._asym_balance_warn_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._asym_balance_warn_lbl.setWordWrap(True)
        self._asym_balance_warn_lbl.setStyleSheet(
            "color: #c62828; font-size: 9pt; padding: 2px 6px;")
        self._asym_balance_warn_lbl.setVisible(False)
        outer.addWidget(self._asym_balance_warn_lbl)

        # Discharge-mode dropdown — pinned at the BOTTOM of the
        # Pulse pattern box per user spec. Sits below the charge-
        # balance row so it's the last interactive control on the
        # panel. Form-row layout matches the Charge balance row above
        # so the labels line up vertically.
        self._discharge_mode_form = rich.make_form()
        self._discharge_mode_form.setContentsMargins(0, 0, 0, 0)
        self._discharge_mode_form.setVerticalSpacing(0)
        self._discharge_mode_form.addRow("Discharge mode:",
                                          self.auto_discharge_combo)
        dm_w = QtWidgets.QWidget()
        dm_w.setLayout(self._discharge_mode_form)
        outer.addWidget(dm_w)

        # ----- wiring -----
        self.phase_count.currentTextChanged.connect(self._on_mode_changed)
        self.symmetry.currentTextChanged.connect(self._on_mode_changed)
        # ``_on_polarity_changed`` flips the asymmetric phase signs to
        # match the new polarity, then emits — chaining the emit
        # itself instead of subscribing twice. Track the previous
        # text so we can detect a real flip vs. an idempotent
        # ``setCurrentText`` (e.g. during prefs restore).
        self._prev_polarity = self.polarity.currentText()
        self.polarity.currentTextChanged.connect(self._on_polarity_changed)
        # Polarity drives the SIGN side of the triphasic readout
        # (which signs land on which phase). Refresh the readout when
        # the user flips polarity so the displayed pattern stays in
        # sync. ``_on_polarity_changed`` already calls ``_emit``, so
        # this is a sibling signal connection rather than a chain.
        self.polarity.currentTextChanged.connect(
            self._refresh_ratio_excitation_label)
        self.charge_mode.currentTextChanged.connect(self._on_balance_changed)
        # Per-phase auto-sign-flip — fires on ``editingFinished``
        # (commit / Enter / focus-out), NOT on every keystroke.
        # The user wants to be able to TYPE the full number first
        # ("100", "-50", etc.) and only have the sign auto-flip
        # AFTER they finish entering it; firing on valueChanged
        # would flip mid-typing and corrupt partial digit input
        # (e.g. typing "1" auto-flips to "-1", then typing "0"
        # appends to display "-10", but the user intended "+100").
        # ``editingFinished`` is emitted on Enter / focus-out and
        # also after step button clicks once Qt commits the
        # pending text, so the user-visible behaviour matches
        # "after entering the value".
        for i in range(len(self.phase_amp)):
            self.phase_amp[i].editingFinished.connect(
                lambda idx=i: self._on_phase_amp_value_changed(idx))
        # The symmetric excitation amplitude follows the polarity sign too, so
        # the displayed value (e.g. +5 / -5) always matches the polarity.
        self.amp_excite.editingFinished.connect(
            self._on_amp_excite_value_changed)
        for w in (self.amp_excite, self.width_shared,
                  self.interphase_us, self.discharge_us, self.rate_pps,
                  *self.ratio_spins, *self.phase_amp, *self.phase_width):
            w.valueChanged.connect(self._emit)

        # Initial visibility
        self._on_mode_changed()
        # Initial sign sync: the default polarity is cathodic-first, so the
        # excitation amplitude must show its negative sign to match (the
        # phase_amp defaults already carry the right sign per index).
        self._sync_excite_amp_sign()
        # First render of the acquisition-time readout (default n_avg / mode
        # until the Setup tab pushes the real acquisition selection).
        self._update_acq_time_label()
        # Wire COMMIT signals for the log pane (Enter / focus-out on spinboxes,
        # discrete change on combos / checkboxes) — decoupled from the live
        # preview (patternChanged).
        self._wire_commit_signals()

    def _emit_committed(self, *_):
        """Emit ``patternCommitted`` (the log-pane trigger) when the user
        COMMITS an edit.  Guarded by ``_suspend_signals`` so a prefs restore /
        programmatic mode switch doesn't fire it."""
        if getattr(self, "_suspend_signals", False):
            return
        try:
            self.patternCommitted.emit(self.pattern())
        except Exception:
            pass

    def _wire_commit_signals(self) -> None:
        """Connect every pattern-input's COMMIT signal to ``_emit_committed``:
        spin boxes on ``editingFinished`` (Enter / focus-out — NOT per
        keystroke), combos on ``currentIndexChanged``, checkboxes / radios on
        ``toggled``.  Enumerated via ``findChildren`` so new inputs are covered
        automatically; the main-window log de-dupes on the pattern description,
        so a commit on a non-shape input (e.g. the acq-average spinbox) that
        leaves the pattern unchanged produces no line."""
        try:
            for sb in self.findChildren(QtWidgets.QAbstractSpinBox):
                sb.editingFinished.connect(self._emit_committed)
            for cb in self.findChildren(QtWidgets.QComboBox):
                cb.currentIndexChanged.connect(self._emit_committed)
            for bt in self.findChildren(QtWidgets.QAbstractButton):
                if bt.isCheckable():
                    bt.toggled.connect(self._emit_committed)
        except Exception:
            pass

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _dspin(lo: float, hi: float, val: float,
               step: float = 1.0, decimals: int = 2,
               suffix: str = "", force_sign: bool = False
               ) -> RepeatingDoubleSpinBox:
        sp = RepeatingDoubleSpinBox()
        sp.setRange(lo, hi); sp.setDecimals(decimals)
        # Set the explicit-sign policy BEFORE the first setValue so the
        # initial display already carries the ``+`` (signed-amplitude fields).
        sp._force_sign = bool(force_sign)
        sp.setSingleStep(step); sp.setValue(val); sp.setSuffix(suffix)
        return sp

    # ----------------------------------------------------------- mode changes
    def _on_mode_changed(self, *_):
        kind = self.phase_count.currentText()
        triphasic = (kind == TRIPHASIC)
        arbitrary = (kind == ARBITRARY)
        # Symmetry combo is only meaningful for biphasic.
        sym_combo_visible = not (triphasic or arbitrary)
        self.symmetry.setVisible(sym_combo_visible)
        self._sym_separator.setVisible(sym_combo_visible)
        if triphasic:
            asym = False
        elif arbitrary:
            asym = False
        else:
            asym = self.symmetry.currentText() == ASYMMETRIC
        # Move the polarity row between forms so it sits in the
        # right place for the current mode. Asymmetric biphasic
        # → polarity lives in ``asym_box`` (below the Asymmetric-
        # shape row). All other modes → polarity stays in
        # ``top_form`` (right after Pulse style).
        target = "asym" if asym else "top"
        try:
            self._move_polarity_row(target)
        except Exception:
            # Re-entrancy or layout-state quirks shouldn't block
            # mode changes — fall back silently.
            pass

        # Sym / asym / arb bodies are mutually exclusive.
        self.sym_box.setVisible((not asym) and (not arbitrary))
        self.asym_box.setVisible(asym and (not arbitrary))
        self.arb_box.setVisible(arbitrary)
        self.ratio_w.setVisible(triphasic)
        # Polarity isn't meaningful for arbitrary patterns — the user
        # types signed amplitudes directly into the table. Hide its row.
        self.polarity.setVisible(not arbitrary)
        pol_label = self.top_form.labelForField(self.polarity)
        if pol_label is not None:
            pol_label.setVisible(not arbitrary)
        # Ratio row's label (sibling to ratio_w in the form layout)
        lab = self._sym_form.labelForField(self.ratio_w)
        if lab is not None:
            lab.setVisible(triphasic)
        # The applied-pattern / excitation readout sits in its own row
        # immediately below the ratio row. Toggle both the field and
        # its (empty-string) label cell so the row fully collapses
        # when the user is on biphasic / arbitrary modes.
        self.ratio_excitation_label.setVisible(triphasic)
        ratio_excite_lab = self._sym_form.labelForField(self.ratio_excitation_label)
        if ratio_excite_lab is not None:
            ratio_excite_lab.setVisible(triphasic)

        # Amplitude row label depends on phase count
        if triphasic:
            self._amp_row_label.setText(
                rich.field_label("Amplitude factor", rich.var("I"), rich.UA))
        else:
            self._amp_row_label.setText(
                rich.field_label("Stimulation current", rich.I_STIM, rich.UA))

        # Hide the third asymmetric phase row when biphasic
        self._phase_rows[2].setVisible(triphasic)
        af = self.asym_box.layout()
        if isinstance(af, QtWidgets.QFormLayout):
            lab3 = af.labelForField(self._phase_rows[2])
            if lab3 is not None:
                lab3.setVisible(triphasic)

        # Asymmetric-shape row is meaningful only for biphasic +
        # asymmetric. Hide the row label and combo together when
        # transitioning out of that mode.
        asym_shape_visible = (not triphasic) and (not arbitrary) and asym
        self._asym_shape_label.setVisible(asym_shape_visible)
        self.asym_shape_combo.setVisible(asym_shape_visible)
        # Formula readout shares the asymmetric-shape's visibility:
        # both the label and the value-cell collapse together so
        # the form layout doesn't leave an empty row when the
        # panel is in symmetric / triphasic / arbitrary mode.
        self._asym_formula_label_widget.setVisible(asym_shape_visible)
        self._asym_formula_lbl.setVisible(asym_shape_visible)
        # Cap-coupled lock row visible only when both asymmetric AND
        # the cap-coupled shape is active. The τ-mode row follows
        # the same rule — both belong to the cap-coupled cluster
        # and either both make sense (cap-coupled active) or
        # neither does.
        is_cap = (asym_shape_visible
                  and self.asym_shape_combo.currentData() == ASYM_SHAPE_CAP)
        self._cap_lock_label.setVisible(is_cap)
        self._cap_lock_row_w.setVisible(is_cap)
        self._cap_tau_label.setVisible(is_cap)
        self._cap_tau_row_w.setVisible(is_cap)
        # Reflect the current τ-mode in the spinbox enabled state
        # (auto = read-only / grey; manual = editable). Idempotent
        # if the state already matches.
        manual_tau = (self.tau_mode_combo.currentData() == TAU_MODE_MANUAL)
        self._set_lock_state(self.tau_us, not manual_tau)
        # Mix-and-match per-phase shape pickers — visible only when
        # both asymmetric AND the mix_match shape is selected.
        is_mix_match = (asym_shape_visible
                        and self.asym_shape_combo.currentData()
                            == ASYM_SHAPE_MIX_MATCH)
        if hasattr(self, "_mix_phase_row_label"):
            self._mix_phase_row_label.setVisible(is_mix_match)
            self._mix_phase_row_w.setVisible(is_mix_match)
        if hasattr(self, "_mix_offset_row_label"):
            self._mix_offset_row_label.setVisible(is_mix_match)
            self._mix_offset_row_w.setVisible(is_mix_match)
            # Refresh per-phase offset visibility now that the
            # parent row's visibility is settled.
            if is_mix_match:
                self._refresh_mix_offset_visibility()

        # Charge balance row is meaningful ONLY for biphasic + asymmetric +
        # rectangular shape. Arbitrary mode lets the user craft any
        # charge profile they want; cap-coupled enforces balance via the
        # solver. So hide whenever any of those don't apply.
        cb_visible = ((not triphasic) and (not arbitrary) and asym
                      and not is_cap)
        self._charge_w.setVisible(cb_visible)
        if not cb_visible:
            # Disable any auto-adjust lock that the dropdown might
            # have left on a spinbox in the previous mode.
            for sp in (*self.phase_amp, *self.phase_width):
                self._set_lock_state(sp, False)

        # Phase-2 (last) lock states for cap-coupled vs rect-asym.
        # Two distinct ways the second phase ends up auto-derived:
        #
        #   * **Cap-coupled** — the user pins one of (amplitude,
        #     width) via the Lock combo and ``solve_capacitive_balance``
        #     computes the other plus τ. The auto-derived spinbox
        #     should be greyed out + read-only so a user reading the
        #     panel knows it's a computed value, not something to
        #     type into.
        #   * **Rect-asym auto-balance** — the charge-balance
        #     dropdown's "auto-adjust amplitude" / "auto-adjust width"
        #     mode similarly derives one knob to make Q_anod = Q_cath.
        #     The same lock convention applies (greyed out =
        #     auto-derived).
        #
        # We compute both lock states explicitly here so transitions
        # between the two modes never leave a stale lock applied —
        # e.g. switching cap-coupled → rect-asym without my code
        # would leave the cap-coupled lock on phase_amp[1] sticky
        # because the cleanup loop above only runs when
        # ``cb_visible`` flips to False, not back to True.
        if is_cap:
            # Cap-coupled: lock the OPPOSITE of whichever knob the
            # user pinned. ``LOCK_WIDTH`` means "I pin the width;
            # solve for amplitude" → the AMPLITUDE spinbox is the
            # auto-derived one, and gets greyed out. ``LOCK_AMPLITUDE``
            # is the reverse.
            lock_mode = self.cap_lock_combo.currentData() or LOCK_WIDTH
            self._set_lock_state(self.phase_amp[1],
                                 lock_mode == LOCK_WIDTH)
            self._set_lock_state(self.phase_width[1],
                                 lock_mode == LOCK_AMPLITUDE)
        elif cb_visible:
            # Rect-asym biphasic: re-apply the charge-balance
            # dropdown's own lock state. ``_on_balance_changed``
            # does this on dropdown changes; mirroring it here
            # makes the asym-shape transition (cap → rect) reach
            # a consistent state without waiting for the user to
            # touch the dropdown again.
            cb_mode = self.charge_mode.currentText()
            last_idx = 2 if triphasic else 1
            self._set_lock_state(self.phase_amp[last_idx],
                                 cb_mode == CHARGE_BAL_AMP)
            self._set_lock_state(self.phase_width[last_idx],
                                 cb_mode == CHARGE_BAL_WID)
        # else: cb_visible False (triphasic / arbitrary / symmetric)
        # — the cleanup loop above already cleared every spinbox.

        # Phase-shape row — visible only in biphasic symmetric mode.
        # Triphasic stays rectangular; asymmetric uses its own
        # (rectangular / capacitively-coupled) constraint set; arbitrary
        # is fully manual. Bump-count row visibility cascades from the
        # shape selection itself.
        shape_row_visible = (not triphasic) and (not arbitrary) and (not asym)
        self._shape_row_label.setVisible(shape_row_visible)
        self.shape_combo.setVisible(shape_row_visible)
        self._update_bump_count_visibility(shape_row_visible)
        # Offset / τ rows track the symmetric-shape row plus an
        # extra shape filter (offset: non-rectangular; τ:
        # exponential family). Delegate to the shape-aware handler
        # so the two checks live in one place.
        shape_id = self.shape_combo.currentData() or SHAPE_RECTANGULAR
        offset_applicable = (shape_row_visible
                             and shape_id != SHAPE_RECTANGULAR)
        tau_applicable = shape_row_visible and shape_id in {
            SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING,
            "exp_inc_dec", "exp_dec_inc",
        }
        if hasattr(self, "_sym_offset_label"):
            self._sym_offset_label.setVisible(offset_applicable)
            self.sym_offset_ua.setVisible(offset_applicable)
        if hasattr(self, "_sym_tau_label"):
            self._sym_tau_label.setVisible(tau_applicable)
            self.sym_tau_us.setVisible(tau_applicable)
        # Always clear any leftover read-only styling on amp/width —
        # the Q_ph / current / width lock UI now lives in the VT tab,
        # so the pattern panel's amp/width spinboxes should always be
        # plain editable inputs here. The VT lock UI sets / restores
        # styling on these widgets directly when its lock combo flips.
        self._set_lock_state(self.amp_excite, False)
        self._set_lock_state(self.width_shared, False)
        # Re-pin the per-phase sign locks. Polarity may have changed
        # via prefs-restore or symmetry/triphasic toggle without
        # firing ``_on_polarity_changed`` directly, so we apply
        # here too — idempotent + cheap (just setRange calls).
        self._apply_polarity_sign_locks()
        self._emit()

    def _on_sym_offset_enable_toggled(self, on: bool) -> None:
        """Enable / disable the symmetric current-offset spinbox (operator
        opt-in checkbox).  Unchecked → the spinbox is disabled and
        :meth:`pattern` reads the offset as 0."""
        if hasattr(self, "sym_offset_ua"):
            self.sym_offset_ua.setEnabled(bool(on))
        self._emit()

    def _on_asym_shape_changed(self, *_):
        """Asymmetric shape changed (rectangular vs capacitively-coupled).

        Two consequences:
         1. The cap-coupled lock-mode row + status label become
            visible only when shape == capacitively-coupled.
         2. Charge balance is enforced by construction in cap-coupled
            mode (the solver guarantees Q_anod = Q_cath), so the
            ``charge_mode`` dropdown is hidden — picking "Off" or
            "auto-adjust" makes no sense when balance is mathematically
            zero.
        Fires _emit() so the live preview rebuilds with the new shape.
        """
        is_cap = (self.asym_shape_combo.currentData() == ASYM_SHAPE_CAP)
        self._cap_lock_label.setVisible(is_cap)
        self._cap_lock_row_w.setVisible(is_cap)
        # τ row is part of the cap-coupled UI cluster — same
        # visibility rule as the lock combo.
        self._cap_tau_label.setVisible(is_cap)
        self._cap_tau_row_w.setVisible(is_cap)
        # Mix-and-match per-phase shape row visible only when the
        # corresponding asym shape is selected.
        is_mix_match = (self.asym_shape_combo.currentData()
                        == ASYM_SHAPE_MIX_MATCH)
        if hasattr(self, "_mix_phase_row_label"):
            self._mix_phase_row_label.setVisible(is_mix_match)
            self._mix_phase_row_w.setVisible(is_mix_match)
        if hasattr(self, "_mix_offset_row_label"):
            self._mix_offset_row_label.setVisible(is_mix_match)
            self._mix_offset_row_w.setVisible(is_mix_match)
            if is_mix_match:
                self._refresh_mix_offset_visibility()
        # Hide the charge-balance dropdown in cap-coupled mode (the
        # solver enforces balance directly).
        cb_visible = (not is_cap) and self._charge_w.isVisibleTo(self)
        if hasattr(self, "_charge_w"):
            self._charge_w.setVisible(
                bool(cb_visible) and self._asym_shape_label.isVisibleTo(self))
        self._emit()

    def _on_tau_mode_changed(self, *_):
        """τ mode changed (auto vs manual).

        When ``manual`` the spinbox becomes editable (the user pins
        τ). When ``auto`` the spinbox is grey + read-only and its
        value is set as a side-effect of every emit() to whatever
        τ the solver picked. Either way the change re-emits the
        pattern so the live preview updates immediately.
        """
        manual = (self.tau_mode_combo.currentData() == TAU_MODE_MANUAL)
        # Keep the spinbox enabled (so it's still tab-stop-able and
        # selectable for copy) but read-only in auto mode. Same UX
        # convention used by the rest of the panel for auto-derived
        # values (see ``_set_lock_state``).
        self._set_lock_state(self.tau_us, not manual)
        self._emit()

    def _on_tau_value_changed(self, *_):
        """τ spinbox value changed — re-emit only when in manual
        mode. In auto mode the spinbox is updated by ``pattern()``
        with signals blocked, so this handler never fires there."""
        if self.tau_mode_combo.currentData() == TAU_MODE_MANUAL:
            self._emit()

    def _refresh_mix_offset_visibility(self, *_):
        """Per-phase offset spinbox visibility in mix-and-match
        depends on the corresponding phase's shape: rectangular →
        hidden (offset has no effect), anything else → visible.

        The labels next to each spinbox track the same logic so
        the form row reads cleanly when one phase shows and the
        other is hidden. The whole offset row hides outside
        mix-and-match mode (driven by ``_on_mode_changed`` /
        ``_on_asym_shape_changed``)."""
        if not hasattr(self, "_mix_offset_row_w"):
            return
        # Are we even in mix-and-match? If the parent row is
        # hidden, leave the per-phase visibility alone — the row
        # is gone anyway.
        if not self._mix_offset_row_w.isVisibleTo(self):
            return
        for i, sp in enumerate(self.mix_phase_offset_ua):
            try:
                p_shape = self.mix_phase_shape_combo[i].currentData()
            except Exception:
                p_shape = SHAPE_RECTANGULAR
            applicable = (p_shape != SHAPE_RECTANGULAR)
            sp.setVisible(applicable)
            # Show / hide the matching "Phase N:" label too.
            lab = (self._mix_offset_p0_label if i == 0
                   else self._mix_offset_p1_label)
            lab.setVisible(applicable)

    def _refresh_mix_offset_max(self, phase_idx: int) -> None:
        """Cap the phase-N offset spinbox at ``|phase_amp[N]| −
        step`` so the offset transform can never collapse the
        shape to a flat line.

        Background: the per-phase offset transform in
        :func:`stimtest.waveforms.shape_breakpoints` (the
        ``_bps_with_offset`` linear-blend) clamps the effective
        offset magnitude to ``|A|`` and computes
        ``vals = so + (A − so) · (natural/A)``. When the user
        types ``offset == |A|``, ``so`` saturates to ``A`` and
        the ``(A − so)`` factor zeroes out — every sample becomes
        ``so = A``, producing a flat-line "phase" that visually
        looks like a rectangle even though the user picked a
        ramped / curved shape. Capping the spinbox max at
        ``|A| − one step`` keeps at least a one-quantum delta so
        the shape variation is always visible.

        Called from each ``phase_amp[i].valueChanged`` signal.
        Idempotent — safe to call before the panel is fully
        constructed."""
        if not (0 <= phase_idx < len(self.mix_phase_offset_ua)):
            return
        sp = self.mix_phase_offset_ua[phase_idx]
        if not (0 <= phase_idx < len(self.phase_amp)):
            return
        try:
            amp_abs = abs(float(self.phase_amp[phase_idx].value()))
        except Exception:
            return
        # One current-quantum margin so the visible shape always
        # carries at least one step's worth of ramp.
        margin = max(float(STIM_CURRENT_RESOLUTION_UA), 0.1)
        new_max = max(0.0, amp_abs - margin)
        # Avoid churning ``setRange`` for unchanged values (it
        # emits ``rangeChanged`` and can trip QDoubleSpinBox into
        # silently clamping value to a new range and re-emitting
        # ``valueChanged``, which would feedback into _emit).
        if abs(sp.maximum() - new_max) < 1e-9:
            return
        # Preserve the current value if it still fits the new range;
        # otherwise let setRange clamp it to the new max.
        cur = sp.value()
        sp.blockSignals(True)
        try:
            sp.setRange(0.0, new_max)
            if cur > new_max:
                sp.setValue(new_max)
        finally:
            sp.blockSignals(False)

    def _on_shape_changed(self, *_):
        """User picked a different phase shape — update bump-count row
        visibility and re-emit the pattern so the live preview updates.

        Per-shape visibility:
          * bump_count row — visible for SHAPE_SPEEDBUMPS only.
          * Offset row — visible for any non-rectangular shape (the
            baseline-floor concept doesn't apply to rectangles).
          * τ row — visible for the exponential shapes (decay /
            increasing / exp-pair) only.
        """
        # Derive whether we're in biphasic symmetric mode from
        # the state machine rather than from ``shape_combo.isVisible()``
        # — the latter returns False when the panel hasn't been
        # ``show()``ed (e.g. in headless tests), which would
        # spuriously hide all the dependent rows.
        try:
            kind = self.phase_count.currentText()
            sym = self.symmetry.currentText()
        except Exception:
            kind = sym = ""
        sym_row_active = (kind == BIPHASIC and sym == SYMMETRIC)
        self._update_bump_count_visibility(sym_row_active)
        # Offset / τ row visibility tied to the shape AND to
        # symmetric-row visibility — hide both when the panel is
        # outside biphasic symmetric mode (triphasic / arbitrary
        # / asymmetric paths don't use these widgets).
        shape_id = self.shape_combo.currentData() or SHAPE_RECTANGULAR
        offset_applicable = sym_row_active and (shape_id != SHAPE_RECTANGULAR)
        tau_applicable = sym_row_active and shape_id in {
            SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING,
            "exp_inc_dec", "exp_dec_inc",
        }
        if hasattr(self, "_sym_offset_label"):
            self._sym_offset_label.setVisible(offset_applicable)
            self.sym_offset_ua.setVisible(offset_applicable)
        if hasattr(self, "_sym_tau_label"):
            self._sym_tau_label.setVisible(tau_applicable)
            self.sym_tau_us.setVisible(tau_applicable)
        self._emit()

    def _refresh_shape_tooltip(self) -> None:
        """Update the closed-combobox tooltip to a larger preview of
        whichever shape is currently selected. The dropdown-list
        icons are set once at construction (one per shape), but the
        outer-combobox tooltip needs to track the selection so the
        hover shows the user *what they have right now*."""
        sid = self.shape_combo.currentData() or SHAPE_RECTANGULAR
        html = self._shape_tooltip_html.get(sid)
        if html:
            self.shape_combo.setToolTip(html)

    def _refresh_asym_shape_tooltip(self) -> None:
        """Same idea as :meth:`_refresh_shape_tooltip`, for the
        ASYMMETRIC biphasic shape dropdown. Picks the right cached
        HTML/preview for the current selection."""
        sid = self.asym_shape_combo.currentData() or ASYM_SHAPE_RECT
        html = self._asym_shape_tooltip_html.get(sid)
        if html:
            self.asym_shape_combo.setToolTip(html)

    def _refresh_phase_count_tooltip(self) -> None:
        """Update the closed-combobox tooltip on the Pulse-style
        dropdown to a larger preview of the currently-selected
        family (biphasic / triphasic / arbitrary)."""
        kind = self.phase_count.currentText() or BIPHASIC
        html = self._phase_count_tooltip_html.get(kind)
        if html:
            self.phase_count.setToolTip(html)

    def _update_bump_count_visibility(self, shape_row_visible: bool) -> None:
        """Speedbumps no longer expose a bump-count spinbox — it's
        hardcoded at 2 internally. Kept as a no-op so existing call
        sites don't need to be unwired."""
        self._bump_row_label.setVisible(False)
        self.bump_count.setVisible(False)

    def _move_polarity_row(self, target: str) -> None:
        """Move the Polarity row between ``top_form`` and
        ``_asym_form`` depending on whether we're in asymmetric
        biphasic mode.

        ``target`` is ``"top"`` (default — Polarity sits in
        ``top_form`` right after Pulse style) or ``"asym"``
        (Polarity migrates into ``asym_box`` directly below the
        Asymmetric-shape row, per the user-spec layout for
        asymmetric mode).

        Uses ``QFormLayout.takeRow`` to detach the row from its
        current form (preserving the widgets), then
        ``insertRow`` to drop it into the target form at the
        right position. No-op when already at the target.
        """
        if target == getattr(self, "_polarity_location", "top"):
            return
        # Locate the polarity row in its current form by scanning
        # field-role items for the polarity widget. QFormLayout
        # doesn't have a direct "getRow(widget)" API.
        src_form = (self.top_form if self._polarity_location == "top"
                    else self._asym_form)
        idx = -1
        for i in range(src_form.rowCount()):
            item = src_form.itemAt(
                i, QtWidgets.QFormLayout.ItemRole.FieldRole)
            if item is not None and item.widget() is self.polarity:
                idx = i
                break
        if idx < 0:
            return
        # Detach the row. ``takeRow`` removes label + field items
        # from the layout WITHOUT deleting the widgets, so we
        # can re-insert them in the target form below.
        src_form.takeRow(idx)
        # Re-insert into the target form. Position 1 in both
        # forms keeps the polarity row directly below the row
        # above it (top_form's Pulse style row in symmetric mode,
        # asym_box's Asymmetric-shape row in asymmetric mode).
        if target == "top":
            self.top_form.insertRow(
                1, self._polarity_label, self.polarity)
        else:   # "asym"
            self._asym_form.insertRow(
                1, self._polarity_label, self.polarity)
        self._polarity_location = target

    def _on_polarity_changed(self, text: str):
        """Flip the asymmetric phase amplitudes when polarity flips.

        In symmetric mode, polarity is applied at pattern-build time
        from the ``self.polarity`` dropdown (see :meth:`pattern`), so
        the spinbox magnitudes are unaffected. In **asymmetric** mode
        the user types signed amplitudes directly — when they flip
        the polarity dropdown we invert each phase amp's sign so the
        per-phase signs stay consistent with the new polarity choice.

        Two-step update each time:
          1. Flip the signed value of every phase amp so the existing
             magnitudes carry over to the new polarity (e.g. -300 µA
             cathodic → +300 µA after switching to Anodic-first).
          2. Re-apply the per-phase sign-locked range so the
             spinboxes only ACCEPT the polarity-correct sign on
             subsequent edits — this is what fixes the user-reported
             bug where the first amplitude could silently flip the
             waveform's polarity by typing the opposite sign.
        Same convention as the symmetric path: polarity = -1
        (Cathodic-first) puts the excitation phase on the negative
        rail; polarity = +1 (Anodic-first) puts it on the positive
        rail.
        """
        if text != self._prev_polarity:
            for sp in self.phase_amp:
                sp.blockSignals(True)
                try:
                    # Temporarily widen the range to the full
                    # ±MAX envelope before flipping the sign.
                    # If we flipped within the existing tight
                    # half-range (e.g. ``[-MAX, 0]`` from a prior
                    # Cathodic-first session), the post-flip
                    # positive value would land outside the
                    # range and Qt would clamp it to 0 — losing
                    # the magnitude the user originally typed.
                    # Widen → flip → re-tighten (in
                    # _apply_polarity_sign_locks below) preserves
                    # the magnitude across polarity flips.
                    sp.setRange(-STIM_MAX_AMPLITUDE_UA,
                                STIM_MAX_AMPLITUDE_UA)
                    sp.setValue(-sp.value())
                finally:
                    sp.blockSignals(False)
        self._prev_polarity = text
        # Re-tighten the per-phase ranges to the new polarity's
        # half-envelope. The values are already at their flipped
        # signs (above), so the tightening is a no-op for the
        # value but locks subsequent edits to the polarity-correct
        # sign — preventing the user-reported bug where typing a
        # contradicting sign silently flipped the waveform.
        self._apply_polarity_sign_locks()
        # Re-render the shape-preview icons in the dropdowns so
        # they reflect the new polarity (cathodic-first → anodic-
        # first flips the icon vertically). The shape combos are
        # the user's at-a-glance reference for "what will this
        # waveform look like" — staying in sync with the live
        # polarity choice avoids the user seeing a cathodic-first
        # icon while the panel is actually building an
        # anodic-first pulse.
        self._refresh_shape_combo_icons()
        self._emit()

    def _refresh_shape_combo_icons(self) -> None:
        """Regenerate the dropdown icons for the symmetric phase-
        shape combo and the asymmetric-shape combo using the
        current polarity. Called from ``_on_polarity_changed``
        whenever the user flips the polarity dropdown.

        Idempotent and cheap (each combo has ≤ 14 entries; each
        icon is a small 80×28 pixmap regenerated via the existing
        ``_render_*_pixmap`` helpers). Block ``currentIndexChanged``
        on each combo while rebuilding so the icon refresh doesn't
        trigger a spurious shape-changed signal."""
        polarity = -1 if self.polarity.currentText().startswith("Cathod") else +1
        # Symmetric biphasic shape combo.
        if hasattr(self, "shape_combo"):
            self.shape_combo.blockSignals(True)
            try:
                for i in range(self.shape_combo.count()):
                    sid = self.shape_combo.itemData(i)
                    if sid is None:
                        continue
                    pm = _render_shape_pixmap(
                        sid, w_px=80, h_px=28, polarity=polarity)
                    self.shape_combo.setItemIcon(i, QtGui.QIcon(pm))
            finally:
                self.shape_combo.blockSignals(False)
            # Refresh the closed-combo tooltip preview too so the
            # hover image matches.
            if hasattr(self, "_refresh_shape_tooltip"):
                self._refresh_shape_tooltip()
        # Asymmetric shape combo.
        if hasattr(self, "asym_shape_combo"):
            self.asym_shape_combo.blockSignals(True)
            try:
                for i in range(self.asym_shape_combo.count()):
                    sid = self.asym_shape_combo.itemData(i)
                    if sid is None:
                        continue
                    pm = _render_asym_shape_pixmap(
                        sid, w_px=80, h_px=28, polarity=polarity)
                    self.asym_shape_combo.setItemIcon(i, QtGui.QIcon(pm))
            finally:
                self.asym_shape_combo.blockSignals(False)
            if hasattr(self, "_refresh_asym_shape_tooltip"):
                self._refresh_asym_shape_tooltip()
        # Mix-and-match per-phase shape combos — each phase gets a
        # single-phase preview, signed per the user's polarity choice
        # so phase 1 and phase 2 icons sit in the correct y-half.
        # Cathodic-first: phase 1 cathodic (-1), phase 2 anodic (+1).
        # Anodic-first: the mirror image.
        if hasattr(self, "mix_phase_shape_combo"):
            phase_signs = ((-1, +1) if polarity == -1 else (+1, -1))
            for i, cb in enumerate(self.mix_phase_shape_combo):
                sign = phase_signs[i if i < 2 else 1]
                cb.blockSignals(True)
                try:
                    for j in range(cb.count()):
                        sid = cb.itemData(j)
                        if sid is None:
                            continue
                        pm = _render_single_phase_pixmap(
                            sid, w_px=60, h_px=24, polarity=sign)
                        cb.setItemIcon(j, QtGui.QIcon(pm))
                finally:
                    cb.blockSignals(False)

    def _apply_polarity_sign_locks(self) -> None:
        """Tighten each ``phase_amp`` spinbox's range to enforce the
        polarity dropdown's sign convention.

        The asymmetric phase-amp spinboxes were originally created
        with the broad symmetric range ``(-STIM_MAX_AMPLITUDE_UA,
        +STIM_MAX_AMPLITUDE_UA)`` so the user could type either
        sign. That broad range is the source of a UX bug: in
        Cathodic-first mode the user could type ``+300 µA`` into
        the excitation phase and the displayed waveform would
        silently flip to Anodic-first orientation, contradicting
        the selected polarity dropdown.

        This method narrows each spinbox to a HALF-RANGE keyed on
        the current polarity:

        * **Cathodic-first** (polarity = -1):
            phase 0 → ``[-MAX, 0]`` (excitation, negative rail)
            phase 1 → ``[0, +MAX]`` (recharge, positive rail)
            phase 2 → ``[-MAX, 0]`` (alternating; only used in
                                    triphasic asym, but kept
                                    consistent for forward compat)

        * **Anodic-first** (polarity = +1): mirror image of above.

        Setting ``setRange(-MAX, 0)`` on a spinbox automatically
        clamps any out-of-range typed value to the nearest valid
        value. Combined with the sign-flip in
        ``_on_polarity_changed``, the sign of every phase amp now
        tracks the polarity dropdown deterministically.

        Triphasic mode uses the ratio spinboxes (``ratio_spins``)
        rather than asymmetric per-phase amp/width inputs, so the
        constraint is applied unconditionally — extra defensive
        ranges on hidden spinboxes are harmless. The actual
        triphasic sign convention lives in
        :meth:`_refresh_ratio_excitation_label` and
        ``PulsePattern.triphasic`` and is unaffected by this
        method.
        """
        text = self.polarity.currentText()
        cathodic_first = text.startswith("Cathod")
        # Map each phase index → which sign it should carry.
        # Convention: phase 0 = excitation (matches polarity);
        # phase 1 = recharge (opposite); phase 2 (triphasic third
        # phase) = same as phase 0 again (the standard alternating
        # pattern McCreery / Liu use).
        sign_for_phase = {
            0: (-1 if cathodic_first else +1),
            1: (+1 if cathodic_first else -1),
            2: (-1 if cathodic_first else +1),
        }
        for i, sp in enumerate(self.phase_amp):
            wanted_sign = sign_for_phase.get(i, -1 if cathodic_first else +1)
            # Block signals so the auto-clamp triggered by
            # setRange (when the existing value falls outside the
            # new range) doesn't recursively re-emit and bounce
            # the panel through pattern() mid-update.
            sp.blockSignals(True)
            try:
                # Capture the magnitude and restore with the
                # polarity-correct sign. The spinbox range stays at
                # the FULL ``(-MAX, +MAX)`` window so the user can
                # type either sign — the auto-flip handler
                # (:meth:`_on_phase_amp_value_changed`) catches any
                # wrong-sign entry and converts it to the right
                # sign at the same magnitude. Previously we
                # narrowed the range to a half-range and let Qt
                # clamp, but that silently zeroed wrong-sign inputs
                # which surprised users.
                magnitude = abs(sp.value())
                target = (-magnitude if wanted_sign < 0 else +magnitude)
                sp.setRange(-STIM_MAX_AMPLITUDE_UA,
                            +STIM_MAX_AMPLITUDE_UA)
                if sp.value() != target:
                    sp.setValue(target)
            finally:
                sp.blockSignals(False)
        # The symmetric excitation amplitude carries the polarity sign too so
        # its displayed value flips with the dropdown (e.g. +5 anodic / -5
        # cathodic).  Signals stay blocked inside the helper; the polarity
        # dropdown's own change already triggers a pattern re-emit.
        self._sync_excite_amp_sign()

    def _wanted_phase_sign(self, phase_idx: int) -> int:
        """Return ``+1`` or ``−1`` indicating which sign the
        amplitude of ``phase_idx`` should carry under the current
        polarity dropdown selection. Convention:
        phase 0 = excitation (matches polarity), phase 1 = recharge
        (opposite), phase 2 (triphasic third phase) = back to
        excitation polarity (alternating pattern, McCreery / Liu).
        """
        cathodic_first = self.polarity.currentText().startswith("Cathod")
        sign_for_phase = {
            0: (-1 if cathodic_first else +1),
            1: (+1 if cathodic_first else -1),
            2: (-1 if cathodic_first else +1),
        }
        return sign_for_phase.get(phase_idx, -1 if cathodic_first else +1)

    def _on_phase_amp_value_changed(self, idx: int) -> None:
        """Auto-flip a phase-amplitude spinbox to match the polarity
        dropdown's sign convention. Typing ``+100`` in a cathodic-
        first phase 0 spinbox converts to ``-100`` automatically
        (and vice versa). Zero is left as-is.

        Wired to each ``phase_amp[i].editingFinished`` signal so
        the flip fires only AFTER the user commits the value
        (Enter, focus-out, or step-button click) — not on every
        digit they type. The mid-typing flip would corrupt
        partial inputs (e.g. ``"1"`` flipping to ``"-1"`` before
        the user finishes typing ``"100"``).
        """
        if not (0 <= idx < len(self.phase_amp)):
            return
        sp = self.phase_amp[idx]
        # Phase 0 is the excitation phase the trigger fires on — enforce the
        # I_mon-trigger minimum magnitude (0 µA → ±0.1, polarity-correct) before
        # the sign-flip logic (which would otherwise stamp a signed zero).
        if idx == 0 and self._enforce_min_amp_magnitude(sp, 0):
            self._emit()
            return
        value = float(sp.value())
        wanted_sign = self._wanted_phase_sign(idx)
        if value == 0.0:
            # Stamp the polarity-correct SIGNED ZERO (-0.0 cathodal /
            # +0.0 anodal) rather than leaving the spinbox's default
            # +0.0.  ``pattern()`` re-derives the sign every call
            # (load-bearing), but keeping the spinbox self-consistent
            # is defense-in-depth so a stale +0.0 never leaks into a
            # 0 µA cathodal VT-max ramp read.
            want0 = math.copysign(0.0, -1.0 if wanted_sign == -1 else +1.0)
            if math.copysign(1.0, value) != math.copysign(1.0, want0):
                sp.blockSignals(True)
                try:
                    sp.setValue(want0)
                finally:
                    sp.blockSignals(False)
            return
        actual_sign = -1 if value < 0 else +1
        if actual_sign != wanted_sign:
            # Suppress the inner setValue's valueChanged so the
            # downstream re-emit happens cleanly once, below.
            sp.blockSignals(True)
            try:
                sp.setValue(-value)
            finally:
                sp.blockSignals(False)
            # editingFinished doesn't cascade through setValue's
            # blocked valueChanged, so the pattern preview /
            # mix-offset cap won't auto-refresh. Trigger them
            # explicitly with the corrected value.
            try:
                self._refresh_mix_offset_max(idx)
            except Exception:
                pass
            self._emit()

    def _sync_excite_amp_sign(self) -> bool:
        """Force the SYMMETRIC excitation-amplitude spinbox to carry the sign
        of the current polarity (phase-0 = excitation sign), keeping its
        magnitude.  The display then reads e.g. ``+5`` (anodic-first) / ``-5``
        (cathodic-first) — operator: "when setting the polarity, the
        stimulation/first current amplitude matches sign … I want the GUI input
        says +5, not just 5".  The pattern math is unaffected (``pattern()``
        reads ``abs(amp_excite)`` and applies the polarity separately), so this
        is display-consistency only.  Returns True if the value changed."""
        sp = getattr(self, "amp_excite", None)
        if sp is None:
            return False
        want = self._wanted_phase_sign(0)          # phase 0 = excitation
        v = float(sp.value())
        target = want * abs(v)
        if v != target:
            sp.blockSignals(True)
            try:
                sp.setValue(target)
            finally:
                sp.blockSignals(False)
            return True
        return False

    def set_min_amp_magnitude(self, min_ua: float) -> None:
        """Require ``|amplitude| >= min_ua`` (0 disables the constraint).

        The experiment tab calls this with ``IMON_TRIGGER_MIN_AMPLITUDE_UA``
        (0.1 µA) when I_mon is the trigger source and 0 when a digital Trigger
        channel is selected (operator: "When Imon is the trigger source,
        current cannot be 0" — the I_mon signal can't trigger below ~0.1 µA;
        a digital sync fires regardless, so 0 µA / a VT-max-from-0 stays legal).
        Immediately clamps the current excitation amplitude if it violates.
        The amp field is a SIGNED spinbox with a symmetric ±1000 µA range, so
        the forbidden interval (−min, +min) can't be a plain ``setMinimum`` —
        it's enforced by intercepting the committed value here + on every edit.
        """
        self._amp_min_mag_ua = max(0.0, float(min_ua or 0.0))
        changed = self._enforce_min_amp_magnitude(self.amp_excite, 0)
        if getattr(self, "phase_amp", None):
            changed = self._enforce_min_amp_magnitude(self.phase_amp[0], 0) \
                or changed
        if changed:
            self._emit()

    def _enforce_min_amp_magnitude(self, sp, phase_idx: int) -> bool:
        """Clamp ``sp`` to ``±_amp_min_mag_ua`` (sign from the polarity) when
        its magnitude is below the minimum; toggle the inline warning.  Returns
        True if the value was changed.  No-op (and hides the warning) when the
        constraint is disabled (min = 0, i.e. a digital trigger)."""
        warn = getattr(self, "_amp_trigger_warn", None)
        mn = float(getattr(self, "_amp_min_mag_ua", 0.0) or 0.0)
        if mn <= 0.0 or sp is None:
            if warn is not None:
                warn.setVisible(False)
            return False
        if abs(float(sp.value())) >= mn:
            if warn is not None:
                warn.setVisible(False)
            return False
        want = self._wanted_phase_sign(phase_idx)
        sp.blockSignals(True)
        try:
            sp.setValue(want * mn)                 # ±min, polarity-correct
        finally:
            sp.blockSignals(False)
        if warn is not None:
            warn.setText(
                f"I_mon is the trigger source — current magnitude must be "
                f"≥ {mn:.1f} µA (the I_mon signal can't trigger below that). "
                f"Use a digital Trigger channel to test from 0 µA.")
            warn.setVisible(True)
        return True

    def _on_amp_excite_value_changed(self) -> None:
        """Auto-flip the excitation amplitude to the polarity sign after the
        user commits an edit (Enter / focus-out / step) — typing ``-5`` in
        anodic-first flips to ``+5``.  Mirrors ``_on_phase_amp_value_changed``
        for the symmetric single-knob path.  Also enforces the I_mon-trigger
        minimum magnitude (clamps a 0 µA commit to ±0.1 when constrained)."""
        changed = self._sync_excite_amp_sign()
        changed = self._enforce_min_amp_magnitude(self.amp_excite, 0) or changed
        if changed:
            self._emit()

    def _refresh_ratio_excitation_label(self, *_):
        """Re-render the applied-pattern + excitation-phase readout.

        Shows TWO things on one row:

          * The SIGNED ratio pattern that ``PulsePattern.triphasic``
            will actually apply, with phase 1 = polarity, phase 2 =
            -polarity (the OPPOSITE), and phase 3 = polarity. This
            makes the alternating-sign convention visible to the user
            so they don't have to mentally compute "what does the
            polarity selector do to phase 2?".
          * Which phase index (a / b / c) carries the largest |Q_ph|
            — the *excitation* phase the ramp scales against. For
            the symmetric path with shared phase widths the largest
            |Q_ph| is just the largest |ratio| entry.
        """
        try:
            vals = [float(sp.value()) for sp in self.ratio_spins]
        except Exception:
            self.ratio_excitation_label.setText("")
            return
        if not vals or all(v == 0 for v in vals):
            self.ratio_excitation_label.setText("")
            return
        # Apply the strict-alternation sign convention. Polarity comes
        # from the dropdown above: "Cathodic-first" → -1, anything
        # else → +1.
        polarity = (-1 if self.polarity.currentText().startswith("Cathod")
                    else +1)
        sign_pattern = (polarity, -polarity, polarity)
        signed_vals = [s * abs(v) for s, v in zip(sign_pattern, vals)]
        # Phase letter follows the row label "(a : b : c)" — pick by
        # largest magnitude (ties → first).
        idx = max(range(len(vals)), key=lambda i: abs(vals[i]))
        letter = "abc"[idx] if idx < 3 else f"#{idx + 1}"
        applied = " : ".join(f"<b>{v:+.2f}</b>" for v in signed_vals)
        self.ratio_excitation_label.setText(
            f"applied: {applied} "
            f"&nbsp;&nbsp;→ excitation phase: <b>{letter}</b>"
        )

    def set_amplitude_visible(self, visible: bool):
        """Hide/show the amplitude (Stimulation current) row.

        The VT tab calls this when the user picks Fixed charge
        density: the per-pulse current is then derived from
        Q_inj × area / phase_width per electrode, so a single
        editable amplitude field is misleading. The phase-shape
        controls (width, polarity, ratio, asymmetry) stay visible
        because they're still under the user's control.
        """
        self.amp_excite.setVisible(visible)
        self._amp_row_label.setVisible(visible)
        # Asymmetric per-phase amplitudes carry the same meaning —
        # also hide them in fixed-Q_inj mode so the user can't type a
        # current value the runner is going to overwrite anyway.
        for sp in self.phase_amp:
            sp.setVisible(visible)

    def _on_arb_mode_changed(self, mode: str):
        """Toggle column count + visibility of the period field."""
        is_fixed = (mode == ARB_FIXED)
        # Column count: 1 (amplitude) for Fixed; 2 (amp + duration) for Variable.
        self.arb_table.setColumnCount(1 if is_fixed else 2)
        if is_fixed:
            self.arb_table.setHorizontalHeaderLabels([f"Amplitude [{rich.UA}]"])
        else:
            self.arb_table.setHorizontalHeaderLabels([
                f"Amplitude [{rich.UA}]", f"Duration [{rich.US}]",
            ])
        # Row-count cap depends on the sub-mode (999 vs 499).
        cap = ARB_MAX_ROWS_FIXED if is_fixed else ARB_MAX_ROWS_VAR
        self.arb_n_rows.setMaximum(cap)
        if self.arb_n_rows.value() > cap:
            self.arb_n_rows.setValue(cap)
        # Period field is only used in Fixed mode.
        self.arb_period_us.setVisible(is_fixed)
        self._arb_period_label.setVisible(is_fixed)
        self._emit()

    def _on_arb_n_rows_changed(self, n: int):
        """Resize the table — keeps existing cell values intact."""
        n = max(1, int(n))
        self.arb_table.setRowCount(n)
        self._emit()

    def _on_balance_changed(self, *_):
        # Disable the auto-adjusted spinbox so the user can see it's
        # being driven by the panel rather than typed in.
        mode = self.charge_mode.currentText()
        # Find the "last phase" spinbox in the visible mode
        if self.symmetry.currentText() == SYMMETRIC:
            # In symmetric mode, the user only edits the excitation amp /
            # shared width; auto-balance is implied by the symmetry.
            # We still expose the dropdown so the user can opt out and
            # then switch to asymmetric to do something custom.
            self._set_lock_state(self.amp_excite, False)
            self._set_lock_state(self.width_shared, False)
        else:
            triphasic = self.phase_count.currentText() == TRIPHASIC
            last_idx = 2 if triphasic else 1
            self._set_lock_state(self.phase_amp[last_idx], mode == CHARGE_BAL_AMP)
            self._set_lock_state(self.phase_width[last_idx], mode == CHARGE_BAL_WID)
        self._emit()

    @staticmethod
    def _mirror_derived_spin(spin: QtWidgets.QDoubleSpinBox,
                             value: float) -> None:
        """Write a solver-derived value into a (greyed, read-only) spinbox
        for DISPLAY only.

        Signal-blocked so it can't recurse through ``valueChanged`` →
        ``_emit`` (the same guard the τ mirror uses), and range-clamped so
        an out-of-range derived value (e.g. a large saturated anodic width)
        doesn't raise.  Used from the cap-coupled ``pattern()`` branch to
        keep the auto-derived phase-2 knob showing the real value rather
        than a stale leftover."""
        try:
            v = max(spin.minimum(), min(spin.maximum(), float(value)))
        except (TypeError, ValueError):
            return
        spin.blockSignals(True)
        try:
            spin.setValue(v)
        finally:
            spin.blockSignals(False)

    @staticmethod
    def _set_lock_state(w: QtWidgets.QDoubleSpinBox, locked: bool):
        """Visually mark a spinbox as auto-derived / read-only.

        Locked-state styling needs to satisfy three constraints:

        1. **Distinguishable** at a glance from an editable
           spinbox — the user has to see "this value is computed
           for me, don't try to type into it" without reading
           a tooltip. Background tint + italic carries that.
        2. **Readable** — the displayed value (often a derived
           amplitude / width / τ that the user wants to inspect)
           must have enough contrast to actually be legible.
           Earlier versions only set ``background:#f0f0f0;
           font-style: italic;`` and let Qt's palette pick the
           text colour, which on several Windows themes drops to
           a low-contrast grey-on-grey that's effectively
           invisible. Setting ``color`` explicitly here pins
           the text to a near-black so the value is always
           legible regardless of system theme.
        3. **Affects every visual sub-widget** of the spinbox.
           ``QAbstractSpinBox`` is composed of a ``QLineEdit``
           (the value text) plus up/down buttons; we target
           both via the cascading rule + the explicit
           ``QAbstractSpinBox`` selector so the suffix glyph
           ("µA" / "µs") inherits the same colour as the digits.
        """
        w.setReadOnly(locked)
        if locked:
            w.setStyleSheet(
                # ``QAbstractSpinBox`` covers the spinbox itself,
                # the suffix, and the inner QLineEdit; setting
                # ``color`` here makes the digits + suffix render
                # in #222 (near-black) on a soft grey #ececec
                # background — high contrast (~13:1 luminance
                # ratio, well past WCAG AA) without losing the
                # visual cue that the field is auto-derived.
                "QAbstractSpinBox {"
                " background:#ececec;"
                " color:#222;"
                " font-style: italic;"
                " border:1px solid #c8c8c8;"
                "}"
                "QAbstractSpinBox::up-button,"
                "QAbstractSpinBox::down-button {"
                " background:#dcdcdc;"
                "}"
            )
        else:
            w.setStyleSheet("")

    # ----------------------------------------------------------- pattern build
    def pattern(self) -> PulsePattern:
        """Build a PulsePattern from the current control state."""
        polarity = -1 if self.polarity.currentText().startswith("Cathod") else +1
        kind = self.phase_count.currentText()
        triphasic = (kind == TRIPHASIC)
        arbitrary = (kind == ARBITRARY)
        # Triphasic is always handled via the ratio path — no asymmetric
        # per-phase entry. Biphasic uses whatever symmetry the user picked.
        # Arbitrary skips both branches and reads phases from the table.
        asym = (not triphasic) and (not arbitrary) and \
               (self.symmetry.currentText() == ASYMMETRIC)
        rate = self._current_rate_hz()
        # Honour the interphase / discharge / interpulse on-off
        # checkboxes: a CHECKED box means the corresponding delay is
        # enabled and the spinbox value is used; an UNCHECKED box
        # forces the delay to 0. The spinbox value is preserved in
        # the background so the user's typed value survives toggles.
        iph = (float(self.interphase_us.value())
               if self.interphase_check.isChecked() else 0.0)
        dd = (float(self.discharge_us.value())
              if self.discharge_check.isChecked() else 0.0)

        if arbitrary:
            # ---- Arbitrary path: read amp[+duration] from the table.
            n = self.arb_table.rowCount()
            is_fixed = self.arb_mode.currentText() == ARB_FIXED
            shared_us = float(self.arb_period_us.value())
            phases: List[Phase] = []
            for r in range(n):
                amp_item = self.arb_table.item(r, 0)
                amp_txt = amp_item.text().strip() if amp_item else ""
                if not amp_txt:
                    continue
                try:
                    amp = float(amp_txt)
                except ValueError:
                    continue
                if is_fixed:
                    width = shared_us
                else:
                    dur_item = self.arb_table.item(r, 1)
                    dur_txt = dur_item.text().strip() if dur_item else ""
                    try:
                        width = float(dur_txt)
                    except ValueError:
                        continue
                # Clamp duration to the hardware range to avoid silent
                # rejection later in the runner.
                width = max(ARB_MIN_DURATION_US,
                            min(ARB_MAX_DURATION_US, width))
                phases.append(Phase(amp, width, 0.0))
            # Discharge delay is appended after the last phase if the
            # discharge_check is on; same convention as the structured
            # bi-/triphasic paths.
            if phases and self.discharge_check.isChecked():
                last = phases[-1]
                phases[-1] = Phase(last.amplitude_ua, last.width_us, dd)
            pat = PulsePattern(phases=phases, rate_hz=rate)
        elif not asym:
            # ---- Symmetric path: derive every phase from one or two knobs.
            mag = abs(float(self.amp_excite.value()))
            w = float(self.width_shared.value())
            if not triphasic:
                # Symmetric biphasic: pick up the user's chosen phase
                # shape from the dropdown. Most shapes apply the same
                # value to both phases (mirrored polarity);
                # ``SYM_BIPHASIC_SHAPE_PAIRS`` lists the few entries
                # that pair DIFFERENT shapes per phase (linear inc-dec
                # / dec-inc) and need the manual-build path. Pure
                # bump_count is only consulted by PulsePattern when
                # shape == SHAPE_SPEEDBUMPS.
                shape_id = self.shape_combo.currentData() or SHAPE_RECTANGULAR
                bumps = int(self.bump_count.value())
                # Optional offset / τ from the symmetric-mode
                # spinboxes. Offset applies to any non-rectangular
                # shape (rectangular ignores it — its breakpoint
                # generator is unaffected). τ applies only to the
                # exponential shapes (decay / increasing) and the
                # exponential pair entries; everything else
                # ignores it. Both default to 0 when the widgets
                # haven't been laid out yet (early construction-
                # time emit path).
                # Offset is applied ONLY when its enable checkbox is checked
                # (operator opt-in); unchecked → 0 (historical no-offset).
                _offset_on = (getattr(self, "sym_offset_enable_chk", None)
                              is not None
                              and self.sym_offset_enable_chk.isChecked())
                sym_offset = (float(self.sym_offset_ua.value())
                              if (_offset_on and hasattr(self, "sym_offset_ua"))
                              else 0.0)
                sym_tau = float(getattr(self, "sym_tau_us", None).value()
                                if hasattr(self, "sym_tau_us") else 0.0)
                # τ is only meaningful for the exponential shapes;
                # zero it out for everything else so downstream
                # ``shape_breakpoints`` falls back to the canonical
                # τ = W / N derivation.
                _EXP_SHAPES = {SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING,
                               "exp_inc_dec", "exp_dec_inc"}
                if shape_id not in _EXP_SHAPES:
                    sym_tau = 0.0
                if shape_id in SYM_BIPHASIC_SHAPE_PAIRS:
                    # Biphasic shape pair: phase 0 and phase 1 use
                    # different shapes despite sharing |amp| and
                    # width. The two shape factors are equal (both
                    # ramps have duty 0.5; both exp shapes share
                    # (1−e^(−N))/N), so charge balance still holds
                    # by the same A·W·duty argument as the
                    # same-shape biphasic case.
                    p0_shape, p1_shape = SYM_BIPHASIC_SHAPE_PAIRS[shape_id]
                    # Cathodic-first (polarity = -1) → phase 0
                    # negative amp; anodic-first → phase 0 positive.
                    sign = -1.0 if polarity == -1 else +1.0
                    p0 = Phase(amplitude_ua=sign * mag, width_us=w,
                               delay_after_us=iph, shape=p0_shape,
                               tau_us=sym_tau, offset_ua=sym_offset)
                    p1 = Phase(amplitude_ua=-sign * mag, width_us=w,
                               delay_after_us=dd, shape=p1_shape,
                               tau_us=sym_tau, offset_ua=sym_offset)
                    pat = PulsePattern(phases=[p0, p1], rate_hz=rate)
                elif sym_offset > 0 or sym_tau > 0:
                    # Same-shape biphasic but with offset and/or τ
                    # set — build manually so the parameters flow
                    # into the Phase objects. ``PulsePattern.biphasic``
                    # doesn't expose these kwargs (and shouldn't,
                    # since most callers don't need them), so we
                    # roll the two phases by hand.
                    sign = -1.0 if polarity == -1 else +1.0
                    p0 = Phase(amplitude_ua=sign * mag, width_us=w,
                               delay_after_us=iph, shape=shape_id,
                               bump_count=bumps, tau_us=sym_tau,
                               offset_ua=sym_offset)
                    p1 = Phase(amplitude_ua=-sign * mag, width_us=w,
                               delay_after_us=dd, shape=shape_id,
                               bump_count=bumps, tau_us=sym_tau,
                               offset_ua=sym_offset)
                    pat = PulsePattern(phases=[p0, p1], rate_hz=rate)
                else:
                    pat = PulsePattern.biphasic(
                        amplitude_ua=mag, phase_width_us=w,
                        interphase_us=iph, discharge_us=dd,
                        polarity=polarity, rate_hz=rate,
                        shape=shape_id, bump_count=bumps,
                    )
            else:
                ratio = tuple(sp.value() for sp in self.ratio_spins)
                pat = PulsePattern.triphasic(
                    amp_excite_ua=mag, phase_width_us=w,
                    interphase_us=iph, discharge_us=dd,
                    polarity=polarity, rate_hz=rate,
                    ratio=ratio,
                )
        else:
            # ---- Asymmetric path. Two sub-modes:
            #   (a) Rectangular asymmetric — per-phase amp/width as
            #       typed (existing behaviour).
            #   (b) Capacitively-coupled — phase 1 is the rectangular
            #       cathodic, phase 2 is exp-decay anodic with charge
            #       balance enforced by the solver. The lock-mode combo
            #       picks which knob is fixed (width or amplitude); the
            #       other is derived along with τ.
            asym_shape = self.asym_shape_combo.currentData() or ASYM_SHAPE_RECT
            cap_coupled = (asym_shape == ASYM_SHAPE_CAP) and (not triphasic)
            phases = []
            if cap_coupled:
                # Phase 1: rectangular cathodic. Sign comes from the
                # user's signed amplitude in the spinbox (asymmetric
                # mode lets them pick polarity directly).
                I_c_signed = float(self.phase_amp[0].value())
                # Force the polarity-correct SIGNED ZERO so a 0 µA
                # cathodal-first template carries -0.0 (not +0.0).
                # ``_pattern_at_amplitude`` recovers polarity from a
                # 0 µA base via ``math.copysign(1.0, phases[0].amp)`` —
                # and copysign(1.0, +0.0)=+1.0 vs copysign(1.0, -0.0)=
                # -1.0 — so an unsigned +0.0 rebuilt a cathodal 0 µA
                # VT-max ramp as ANODAL-first (the pcc-run polarity bug).
                # ``math.copysign(abs(x), sign)`` also corrects a
                # mis-signed non-zero value.  Mirrors the symmetric path
                # (``_sync_excite_amp_sign``: sign * abs(v)).
                I_c_signed = math.copysign(
                    abs(I_c_signed),
                    -1.0 if self._wanted_phase_sign(0) == -1 else +1.0)
                t_c = float(self.phase_width[0].value())
                # Phase 2: exp-decay anodic. Lock-mode picks which of
                # (width, amplitude) is the user's input.
                lock = self.cap_lock_combo.currentData() or LOCK_WIDTH
                if lock == LOCK_WIDTH:
                    locked_value = float(self.phase_width[1].value())
                else:
                    locked_value = abs(float(self.phase_amp[1].value()))
                # τ-mode override. ``Auto`` keeps legacy behaviour
                # (None lets the solver derive τ from the locked
                # geometry). ``Manual`` pins τ to the spinbox value
                # so the solver re-derives the *other* free
                # parameter under that constraint. We read this
                # before the solver call so the override is in
                # place for the actual solve.
                tau_user_val: Optional[float] = None
                if self.tau_mode_combo.currentData() == TAU_MODE_MANUAL:
                    try:
                        tau_user_val = float(self.tau_us.value())
                    except (TypeError, ValueError):
                        tau_user_val = None
                # Solve. Polarity of the anodic phase is opposite of
                # cathodic by definition.
                bal = solve_capacitive_balance(
                    cathodic_amplitude_ua=I_c_signed,
                    cathodic_width_us=t_c,
                    lock=lock,
                    locked_value=locked_value,
                    tau_override_us=tau_user_val,
                )
                # Mirror the solver's chosen τ back into the
                # spinbox in auto mode so the user sees the live
                # value. Block signals to avoid a feedback loop
                # (the spinbox's ``valueChanged`` would otherwise
                # bounce back through ``_emit`` during this same
                # tick). In manual mode the user owns the value;
                # don't overwrite it.
                if self.tau_mode_combo.currentData() == TAU_MODE_AUTO:
                    self.tau_us.blockSignals(True)
                    try:
                        # Clamp to the spinbox range so a
                        # mid-saturation τ that briefly exceeds
                        # the ceiling doesn't error out the
                        # ``setValue`` call.
                        tau_show = float(bal.tau_us)
                        tau_show = max(self.tau_us.minimum(),
                                       min(self.tau_us.maximum(),
                                           tau_show))
                        self.tau_us.setValue(tau_show)
                    finally:
                        self.tau_us.blockSignals(False)
                # Anodic recharge phase is opposite polarity to the
                # cathodic phase. Cathodic-first → I_a > 0.  Derive from
                # ``_wanted_phase_sign(1)`` (the recharge sign) NOT from
                # ``I_c_signed < 0`` — the latter is False for a -0.0
                # cathodal zero, which would stamp the recharge phase
                # with the wrong signed-zero at 0 µA.
                anodic_sign = float(self._wanted_phase_sign(1))
                I_a_signed = anodic_sign * bal.anodic_amplitude_ua
                # Mirror the solver's DERIVED phase-2 value back into the
                # greyed-out (read-only) spinbox so the display reflects
                # reality instead of a stale leftover.  In LOCK_WIDTH the
                # AMPLITUDE is derived (→ ``phase_amp[1]``); in
                # LOCK_AMPLITUDE the WIDTH is derived (→ ``phase_width[1]``).
                # Signal-blocked + range-clamped, same as the τ mirror above.
                # This is what makes a 0-µA excitation SHOW a 0-µA recharge
                # (operator: "When the first phase is 0 µA, set the second
                # phase of PCC to be 0 µA as well") — the solver returns a 0
                # anodic amplitude for zero cathodic charge (gotcha #189),
                # but without this mirror the greyed spinbox kept its old
                # number (e.g. +0.1 µA).  The LOCK_AMPLITUDE branch leaves
                # ``phase_amp[1]`` (the user's locked input) untouched.
                if lock == LOCK_WIDTH:
                    self._mirror_derived_spin(self.phase_amp[1], I_a_signed)
                else:  # LOCK_AMPLITUDE — the WIDTH is the derived knob
                    self._mirror_derived_spin(self.phase_width[1],
                                              bal.anodic_width_us)
                phases.append(Phase(
                    amplitude_ua=I_c_signed, width_us=t_c,
                    delay_after_us=iph,
                    shape=SHAPE_RECTANGULAR,
                ))
                # Saturated cap-coupled = 1000-µA rectangular flat-top
                # followed by exp-decay from the same peak. Built as
                # TWO consecutive phases (rect + exp) with no
                # interphase delay between them (delay_after_us=0 on
                # the flat-top). The unsaturated path keeps the
                # historical single-phase exp-decay shape.
                if bal.saturated and bal.flat_width_us > 0:
                    phases.append(Phase(
                        amplitude_ua=I_a_signed,
                        width_us=bal.flat_width_us,
                        delay_after_us=0.0,
                        shape=SHAPE_RECTANGULAR,
                    ))
                    decay_w = max(0.0, bal.anodic_width_us - bal.flat_width_us)
                    phases.append(Phase(
                        amplitude_ua=I_a_signed,
                        width_us=decay_w,
                        delay_after_us=dd,
                        shape=SHAPE_EXP_DECAY,
                        tau_us=bal.tau_us,
                    ))
                else:
                    phases.append(Phase(
                        amplitude_ua=I_a_signed,
                        width_us=bal.anodic_width_us,
                        delay_after_us=dd,
                        shape=SHAPE_EXP_DECAY,
                        tau_us=bal.tau_us,
                    ))
                # Update the live status label so the user sees what
                # the solver picked. Three states:
                #   * normal       — single-line readout of τ + |Q|.
                #   * saturated    — adds a "saturated" tag + the
                #                    flat-top width so the user
                #                    knows the device is running at
                #                    its 1000-µA ceiling.
                #   * infeasible   — red warning text, no τ; tells
                #                    the user no balanced shape
                #                    exists for the locked width.
                # τ-source tag — adds "(manual)" / "(auto)" so the
                # user can tell at a glance whether the displayed τ
                # is what they pinned or what the solver picked.
                # Empty in older legacy paths; present from the τ-
                # control rollout onwards.
                tau_tag = " (manual)" if tau_user_val is not None else ""
                if bal.infeasible:
                    # Infeasibility under manual τ has a different
                    # remediation than the auto path: in
                    # LOCK_AMPLITUDE we need I_a · τ > Q (raise τ
                    # OR raise I_a). In LOCK_WIDTH we still need
                    # I_max · t_a > Q (raise t_a OR shorten the
                    # cathodic phase). Surface the right hint
                    # rather than a one-size-fits-all message.
                    if tau_user_val is not None and lock == LOCK_AMPLITUDE:
                        hint = (f"with τ = {tau_user_val:.0f} µs the anodic "
                                f"current never integrates up to "
                                f"{abs(bal.cathodic_charge_nc):.2f} nC "
                                f"(asymptotic ceiling = |I_a|·τ). "
                                f"Increase τ or |I_a|.")
                    else:
                        hint = (f"anodic width too short for 1000-µA "
                                f"flat-top + decay to deliver "
                                f"{abs(bal.cathodic_charge_nc):.2f} nC. "
                                f"Increase the anodic width or reduce "
                                f"the cathodic charge.")
                    self.cap_status_lbl.setText(
                        f"<span style='color:#c62828; font-weight:bold;'>"
                        f"⚠ Charge balance infeasible — {hint}</span>")
                elif bal.saturated and bal.flat_width_us > 0:
                    self.cap_status_lbl.setText(
                        f"<span style='color:#ef6c00;'>"
                        f"Saturated at {bal.anodic_amplitude_ua:.0f} µA "
                        f"</span>· flat = {bal.flat_width_us:.0f} µs · "
                        f"τ = {bal.tau_us:.0f} µs{tau_tag} · "
                        f"|Q| = {abs(bal.cathodic_charge_nc):.2f} nC")
                else:
                    self.cap_status_lbl.setText(
                        f"τ = {bal.tau_us:.0f} µs{tau_tag} · "
                        f"|Q| = {abs(bal.cathodic_charge_nc):.2f} nC"
                    )
            else:
                # ---- Rectangular / mix-and-match: per-phase path.
                #
                # The only two non-cap-coupled asymmetric entries left in
                # the dropdown are RECT and MIX_MATCH. RECT keeps both
                # phases rectangular (``phase_shapes`` falls through to
                # None below → SHAPE_RECTANGULAR per phase). MIX_MATCH
                # reads each phase's shape from its own combobox.
                # Triphasic always goes through the symmetric ratio
                # path, so the mix-match branch only fires for biphasic.
                if asym_shape == ASYM_SHAPE_MIX_MATCH and not triphasic:
                    # Mix and match — read each phase's shape from
                    # its own combobox. Auto-balance is shape-aware
                    # (``_shape_duty`` per phase) so the result is
                    # charge-balanced even when the two phases have
                    # different shape factors. Triphasic isn't
                    # supported here (only biphasic has the two
                    # mix-match comboboxes); fall through to None
                    # for triphasic to keep the rect path active.
                    try:
                        p0 = (self.mix_phase_shape_combo[0].currentData()
                              or SHAPE_RECTANGULAR)
                        p1 = (self.mix_phase_shape_combo[1].currentData()
                              or SHAPE_RECTANGULAR)
                    except Exception:
                        p0 = p1 = SHAPE_RECTANGULAR
                    phase_shapes = (p0, p1)
                else:
                    phase_shapes = None
                n = 3 if triphasic else 2
                # Bump-count + τ are panel-level scalars (one spinbox
                # each); pull them once for the whole loop so every
                # phase sees the same values. ``bump_count`` matters
                # only for SHAPE_SPEEDBUMPS; ``tau_us`` only for the
                # exponential shapes. Other shapes ignore them.
                try:
                    bumps_panel = int(self.bump_count.value())
                except Exception:
                    bumps_panel = 3
                try:
                    tau_panel = float(self.sym_tau_us.value())
                except Exception:
                    tau_panel = 0.0
                _EXP_SHAPES_LOCAL = {SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING}
                for i in range(n):
                    amp = float(self.phase_amp[i].value())
                    # Stamp the polarity-correct SIGNED ZERO so a 0 µA
                    # asymmetric phase carries -0.0 / +0.0 per its
                    # polarity — otherwise a 0 µA cathodal-first excitation
                    # phase is stored +0.0 and ``_pattern_at_amplitude``
                    # rebuilds the VT-max ramp ANODAL-first (same
                    # signed-zero bug as the cap-coupled path above).
                    amp = math.copysign(
                        abs(amp),
                        -1.0 if self._wanted_phase_sign(i) == -1 else +1.0)
                    w = float(self.phase_width[i].value())
                    # All phases except the last get the interphase delay;
                    # the last gets the discharge delay (matches Plexon order).
                    delay = iph if i < n - 1 else dd
                    if phase_shapes is not None and i < len(phase_shapes):
                        ph_shape = phase_shapes[i]
                    else:
                        ph_shape = SHAPE_RECTANGULAR
                    # In mix-and-match, pull the per-phase offset
                    # from its spinbox. Other asym modes don't have
                    # per-phase offset controls — leave at 0.
                    ph_offset = 0.0
                    if (asym_shape == ASYM_SHAPE_MIX_MATCH
                            and i < 2 and ph_shape != SHAPE_RECTANGULAR):
                        try:
                            ph_offset = float(
                                self.mix_phase_offset_ua[i].value())
                        except Exception:
                            ph_offset = 0.0
                    # Wire shape-specific parameters per phase. Without
                    # this, mix-and-match Speedbumps used the dataclass
                    # default ``bump_count=3`` instead of the user's
                    # typed value, and mix-and-match exp shapes ignored
                    # the τ spinbox entirely (defaulting to 0 → canonical
                    # τ = W/N) — making the second phase plot a shape
                    # that didn't match the user's parameter choices.
                    ph_bumps = (bumps_panel
                                if ph_shape == SHAPE_SPEEDBUMPS else 3)
                    ph_tau = (tau_panel
                              if ph_shape in _EXP_SHAPES_LOCAL else 0.0)
                    phases.append(Phase(amp, w, delay, shape=ph_shape,
                                        bump_count=ph_bumps,
                                        tau_us=ph_tau,
                                        offset_ua=ph_offset))
                self.cap_status_lbl.setText("")  # only meaningful in cap mode
            pat = PulsePattern(phases=phases, rate_hz=rate)

            # Cap-coupled tail-zero trim — opt-in safety net.
            #
            # After the n_samples-synced discrete-aware refinement
            # in ``solve_capacitive_balance`` the typical residual
            # is sub-pC (well below the PlexStim's 30 nA × 1 µs ≈
            # 30 pC quantisation floor — the device physically
            # can't compensate that small). When a residual is
            # large enough that it exceeds half the trim quantum
            # (so trimming would actually IMPROVE balance, not
            # over-correct it), zero out the matching duration of
            # the decay tail.
            #
            # Mechanics:
            #   1. Measure the discrete net charge.
            #   2. If excess anodic AND larger than half of one
            #      tail-sample's worth: trim by the matching
            #      number of µs (rounded to the device's 1-µs
            #      grid).
            #   3. Patch the exp-decay phase's ``tail_zero_us``
            #      so the .pat builder zeros the trailing
            #      breakpoints.
            #
            # Without the half-quantum guard, a sub-quantum
            # residual would force a one-quantum trim that
            # over-shoots into a LARGER negative residual.
            # Negative residuals (anodic deficit) aren't handled
            # here — they'd need additional charge, which the
            # device's quantisation can't deliver below 30 pC
            # anyway.
            if cap_coupled:
                try:
                    charges = pat.actual_phase_charges_nc()
                    residual_nc = sum(charges)   # signed, anodic + cathodic
                except Exception:
                    residual_nc = 0.0
                # Find the exp-decay phase (last one carrying tau_us > 0).
                exp_idx = None
                for k, ph in enumerate(pat.phases):
                    if ph.shape == SHAPE_EXP_DECAY and ph.tau_us > 0:
                        exp_idx = k
                if exp_idx is not None and abs(residual_nc) > 0.001:
                    # ``residual_nc > 0`` means signed sum is positive.
                    # Cathodic-first (cathodic<0, anodic>0): positive
                    # residual = excess anodic. Anodic-first (anodic<0,
                    # cathodic>0): positive residual = excess cathodic
                    # (deficit anodic) — can't fix by anodic trim.
                    # ``excess_anodic`` follows ``sign(anodic) ==
                    # sign(residual)``.
                    anodic_sign = (1.0 if pat.phases[exp_idx].amplitude_ua > 0
                                   else -1.0)
                    excess_nc = residual_nc * anodic_sign
                    if excess_nc > 0:
                        ph = pat.phases[exp_idx]
                        A = abs(ph.amplitude_ua)
                        W = ph.width_us
                        tau = ph.tau_us
                        if A > 0 and tau > 0 and W > 0:
                            import math as _math
                            tail_amp = A * _math.exp(-W / tau)
                            # Trim quantum = (charge of one trailing
                            # played sample) = tail_amp · dt, where
                            # dt is the breakpoint spacing the
                            # device-side ``curved_sample_budget``
                            # uses. shape_breakpoints rounds the
                            # requested ``tail_zero_us`` to the
                            # nearest integer-sample multiple, so any
                            # trim either zeros zero samples (no-op)
                            # or zeros K samples for K ∈ {1, 2, ...}.
                            # Skip the trim if residual < half a
                            # quantum — a 1-sample trim would
                            # OVERSHOOT, leaving a larger
                            # opposite-sign residual.
                            try:
                                n_curved = pat.curved_sample_budget()
                            except Exception:
                                n_curved = 497
                            dt_bp = (W / (n_curved - 1)
                                     if n_curved > 1 else W)
                            quantum_pc = tail_amp * dt_bp   # pC
                            excess_pc = excess_nc * 1000.0
                            if (tail_amp > 0
                                    and excess_pc >= quantum_pc * 0.5):
                                # Round trim to the nearest integer
                                # multiple of dt; cap at 5 sample-
                                # widths (bound on how aggressive
                                # the trim can get for outlier
                                # solver behaviour).
                                k = int(round(excess_pc / quantum_pc))
                                k = max(1, min(5, k))
                                trim_us = float(k) * dt_bp
                                import dataclasses as _dc
                                pat.phases[exp_idx] = _dc.replace(
                                    ph, tail_zero_us=trim_us)

        # ---- Charge balance ----
        # Only meaningful in biphasic + asymmetric + RECTANGULAR shape.
        # Cap-coupled enforces balance by construction in the
        # ``solve_capacitive_balance`` call above — applying
        # auto_balance again would double-correct.
        cap_active = (asym and not triphasic
                      and self.asym_shape_combo.currentData() == ASYM_SHAPE_CAP)
        mode = (self.charge_mode.currentText()
                if (asym and not triphasic and not cap_active)
                else CHARGE_BAL_OFF)
        # Captured BEFORE clamping so the warning below knows the
        # mathematically-required (pre-clamp) amplitude. ``None``
        # means no clamping happened — balance was achievable
        # within hardware limits.
        unclamped_amp_ua: Optional[float] = None
        if mode == CHARGE_BAL_AMP:
            try: pat = pat.auto_balance(adjust="last_amp")
            except ValueError: pass
            # Hardware-cap clamp. ``auto_balance(adjust="last_amp")``
            # produces the mathematically-correct amplitude, which can
            # exceed ``STIM_MAX_AMPLITUDE_UA``. The PlexStim won't
            # actually deliver more than 1000 µA per channel, so
            # we replace the last phase with a CLAMPED copy of itself
            # — charge balance is then broken (the user has asked
            # for more anodic charge than the device can deliver),
            # which the warning below makes visible. Clamping here
            # rather than relying on the runtime to fail keeps the
            # device safe and gives the user a clear "what just
            # happened" readout instead of a silent compliance hit.
            last_idx = (3 if triphasic else 2) - 1
            if 0 <= last_idx < len(pat.phases):
                last = pat.phases[last_idx]
                from ..config import STIM_MAX_AMPLITUDE_UA
                abs_amp = abs(float(last.amplitude_ua))
                if abs_amp > STIM_MAX_AMPLITUDE_UA + 1e-3:
                    unclamped_amp_ua = abs_amp
                    sign = -1.0 if last.amplitude_ua < 0 else 1.0
                    import dataclasses as _dc
                    pat.phases[last_idx] = _dc.replace(
                        last,
                        amplitude_ua=sign * STIM_MAX_AMPLITUDE_UA,
                    )
        elif mode == CHARGE_BAL_WID:
            try: pat = pat.auto_balance(adjust="last_width")
            except ValueError: pass

        # ---- If a phase param was auto-computed, push it back into the
        # disabled spinbox so the user sees the value the panel is using.
        # While we're here, check whether the auto-balanced last phase
        # exceeds the hardware envelope and surface a warning if so —
        # the auto_balance call itself produces the mathematically
        # correct ``last_amp`` / ``last_width``, but the device has its
        # own ceiling (1000 µA) and reasonable maximum-width that
        # mathematically-correct values can blow through.
        warning_html = ""
        if asym and mode != CHARGE_BAL_OFF:
            self._suspend_signals = True
            try:
                last_idx = (3 if triphasic else 2) - 1
                last = pat.phases[last_idx]
                if mode == CHARGE_BAL_AMP:
                    # ``last.amplitude_ua`` is now the clamped value
                    # (≤ 1000 µA). The spinbox shows that.
                    self.phase_amp[last_idx].setValue(last.amplitude_ua)
                    if unclamped_amp_ua is not None:
                        from ..config import STIM_MAX_AMPLITUDE_UA
                        warning_html = (
                            f"<b>⚠ Charge balance NOT achieved — anodic "
                            f"amplitude clamped at hardware limit.</b> "
                            f"Auto-adjust would need "
                            f"<b>{unclamped_amp_ua:.1f} µA</b> to balance "
                            f"the cathodic phase, but the PlexStim caps "
                            f"at {STIM_MAX_AMPLITUDE_UA:.0f} µA per "
                            f"channel. The anodic amplitude has been "
                            f"clamped to "
                            f"{STIM_MAX_AMPLITUDE_UA:.0f}&nbsp;µA, which "
                            f"leaves a residual cathodic-side imbalance "
                            f"of "
                            f"{(unclamped_amp_ua - STIM_MAX_AMPLITUDE_UA):.1f}"
                            f"&nbsp;µA × the anodic width. Reduce the "
                            f"cathodic amplitude / width, widen the "
                            f"anodic phase, or switch to the pseudo-"
                            f"capacitively-coupled shape (which "
                            f"saturates + decays while keeping "
                            f"balance).")
                else:
                    self.phase_width[last_idx].setValue(last.width_us)
                    # Width is the runner-side knob the device honours
                    # cleanly even at long durations, BUT a balance
                    # width comparable to the inter-pulse interval
                    # squeezes the duty cycle. Warn when the auto-
                    # balanced width exceeds 10 ms — well past anything
                    # a typical neurostim protocol needs.
                    abs_w = abs(float(last.width_us))
                    if abs_w > 10000.0:
                        warning_html = (
                            f"<b>⚠ Charge balance needs unusually long "
                            f"phase width.</b> Auto-adjust width = "
                            f"<b>{abs_w:.0f} µs</b> ({abs_w / 1000:.2f} "
                            f"ms). Verify this is intentional — typical "
                            f"protocols stay below 10 ms. Reducing the "
                            f"cathodic charge or increasing the anodic "
                            f"amplitude shortens the recharge phase.")
            finally:
                self._suspend_signals = False
        # Update the warning label on every emit. Cleared (and
        # hidden) when balance is fine, shown otherwise.
        # ``getattr`` guards against the construction-time race
        # where ``pattern()`` is called via ``_on_mode_changed``
        # before this label has been built — the connection from
        # ``cap_lock_combo.setCurrentIndex(0)`` fires that path
        # in __init__ before the rest of the panel is laid out.
        warn_lbl = getattr(self, "_asym_balance_warn_lbl", None)
        if warn_lbl is not None:
            warn_lbl.setText(warning_html)
            warn_lbl.setVisible(bool(warning_html))
        # ---- Burst / pulse-train: stamp the burst fields (ONE place, after
        # every branch built ``pat``).  Gated on availability + the enable box
        # so a non-burst tab (or an unticked box) always yields an ordinary
        # pattern (pulses_per_burst=1, burst_period_us=0).  ``scaled`` /
        # ``auto_balance`` preserve these downstream (tested), and the runner
        # honours ``device_period_us`` / ``build_burst_pat_pairs`` for them.
        if self._burst_enabled():
            n = int(self.pulses_per_burst_spin.value())
            period_us = float(self.burst_period_ms.value()) * 1000.0
            if n >= 2 and period_us > 0.0:
                pat.pulses_per_burst = n
                pat.burst_period_us = period_us
        # ---- KHFAC discharge-as-short: REPLACE the existing trailing 0-µA
        # discharge step with a real auto-discharge SHORT (operator: "do not
        # add a 1 µs step … replace a 0-µA step between pulses as discharge …
        # only when Discharge Mode is enabled").  No time is added — the field
        # carries the EXISTING discharge duration (the last phase's delay), and
        # build_pat_pairs skips its 0-µA pair so the device idles + shorts it.
        if self._interpulse_discharge_enabled(pat):
            try:
                _disch = float(pat.phases[-1].delay_after_us) if pat.phases \
                    else 0.0
            except Exception:
                _disch = 0.0
            if _disch > 0:
                pat.interpulse_discharge_us = _disch
        return pat

    # Minimum guaranteed dead-time between consecutive pulses (μs).
    # Hardware-conservative — the rate ceiling is computed from
    # 1 e6 / (total_pulse_µs + MIN_INTERPULSE_GAP_US).
    MIN_INTERPULSE_GAP_US = 5.0
    # Hardware envelope on rate: PlexStim 2.0 supports 0.008–100,000 pps.
    RATE_HZ_MIN = 0.008
    RATE_HZ_MAX = 100000.0

    # ----------------------------------------------------------- rate-unit helpers
    def _hz_to_unit(self, rate_hz: float, unit: Optional[str] = None) -> float:
        """Translate a rate in Hz into the spinbox's display value for
        the given unit (defaults to the currently-selected unit). Period
        mode inverts the rate; ``pps`` is identity.
        """
        u = unit or self._rate_unit
        if u == self.UNIT_PPS:
            return float(rate_hz)
        if rate_hz <= 0:
            return 0.0
        return 1e3 / rate_hz   # ms

    def _unit_to_hz(self, value: float, unit: Optional[str] = None) -> float:
        u = unit or self._rate_unit
        if u == self.UNIT_PPS:
            return float(value)
        if value <= 0:
            return self.RATE_HZ_MIN
        return 1e3 / float(value)   # ms -> Hz

    def _current_rate_hz(self) -> float:
        """Rate in Hz, derived from the spinbox and the selected unit."""
        return self._unit_to_hz(float(self.rate_pps.value()))

    def _set_rate_hz(self, rate_hz: float):
        """Display ``rate_hz`` in the spinbox using the current unit."""
        self.rate_pps.setValue(self._hz_to_unit(rate_hz))

    # ------------------------------------------------- acquisition-time readout
    def set_acquisition_info(self, mode: str, n_avg: int) -> None:
        """Push the Setup-tab oscilloscope acquisition selection so the
        per-capture acquisition-time readout beside the rate is accurate.

        Called from ``_BaseExperimentTab.set_acquisition`` — which is the
        single funnel for the Setup-tab ``acquisitionChanged`` signal and
        the Test-parameters entry sync — so every experiment tab's pattern
        panel tracks the same average count / mode the runner will apply.
        """
        self._acq_mode = str(mode or "AVERAGE")
        try:
            self._acq_n_avg = max(1, int(n_avg))
        except (TypeError, ValueError):
            self._acq_n_avg = 16
        # Keep the inline average-count spinbox in sync with the pushed
        # value.  Signal-guarded: without the block, setValue would fire
        # _on_inline_navg_changed → acqNavgEdited → Setup spin →
        # acquisitionChanged → back here (a needless round-trip; the
        # same-value no-op in the slot breaks the loop anyway, this just
        # avoids the churn).  The spin is meaningful only in AVERAGE
        # mode — SAMPLE captures are single sweeps, so grey it out.
        sp = getattr(self, "acq_navg_inline", None)
        if sp is not None:
            sp.blockSignals(True)
            try:
                sp.setValue(self._acq_n_avg)
            finally:
                sp.blockSignals(False)
            sp.setEnabled("AVER" in self._acq_mode.upper())
        self._update_acq_time_label()

    def _on_inline_navg_changed(self, n_avg: "int | None" = None) -> None:
        """The user COMMITTED the inline average-count spinbox (Enter/return/
        focus-out).

        Refresh the local estimate immediately, then publish the new
        count via ``acqNavgEdited`` so MainWindow can push it into the
        Setup tab's ``acq_navg_spin`` (single source of truth — the
        runner reads the Setup value at Start).  Same-value edits are
        dropped to keep the signal chain quiet.
        """
        # Wired to ``editingFinished`` (no value arg) — read the widget.
        if n_avg is None:
            n_avg = self.acq_navg_inline.value()
        try:
            n = max(1, int(n_avg))
        except (TypeError, ValueError):
            return
        if n == self._acq_n_avg:
            return
        self._acq_n_avg = n
        self._update_acq_time_label()
        self.acqNavgEdited.emit(n)

    def _update_acq_time_label(self) -> None:
        """Recompute the approximate per-capture acquisition-time readout.

        ``t ≈ sweeps / rate`` — an AVERAGE-mode capture waits through one
        pulse per averaged sweep (``sweeps = n_avg``); a SAMPLE-mode capture
        is a single sweep (``sweeps = 1``, average count irrelevant).  Pulses
        arrive at the pulse rate, so the time to fill the average is
        ``sweeps / rate_hz`` — the same estimate the runner uses to size its
        capture timeout (gotcha #32).  Overhead (trigger latency, USB-TMC
        transfer) is not modelled — this is the "approximate" figure the
        operator asked for.
        """
        lbl = getattr(self, "acq_time_label", None)
        if lbl is None:
            return
        rate_hz = self._current_rate_hz()
        is_avg = "AVER" in self._acq_mode.upper()
        sweeps = self._acq_n_avg if is_avg else 1
        if not (rate_hz > 0.0) or not math.isfinite(rate_hz):
            lbl.setText("")
            lbl.setToolTip("")
            return
        # BURST: the averager collects ``sweeps`` pulse-triggered frames at the
        # OVERALL pulse rate (pulses_per_burst / burst_period), which is far
        # slower than the intra-burst rate because of the inter-burst gaps — so
        # base the estimate on the overall rate + show it as pps.
        if self._burst_enabled():
            n = int(self.pulses_per_burst_spin.value())
            period_s = float(self.burst_period_ms.value()) / 1000.0
            eff_rate = (n / period_s) if (n >= 2 and period_s > 0.0) else rate_hz
            if eff_rate > 0.0:
                t_s = sweeps / eff_rate
                lead = f"{sweeps}" if is_avg else "1 sweep"
                lbl.setText(
                    f"{lead} ÷ {eff_rate:g} pps (overall) ≈ "
                    f"{self._fmt_acq_time(t_s)} / capture")
                lbl.setToolTip(
                    "Approximate acquisition time per captured waveform "
                    "(burst mode).<br>Estimate: "
                    f"{'{} averaged sweeps'.format(self._acq_n_avg) if is_avg else 'single sweep (SAMPLE mode)'}"
                    f" &divide; {eff_rate:g} pps overall pulse rate "
                    f"({n} pulses / {float(self.burst_period_ms.value()):g} ms "
                    "burst).<br>Overhead (trigger latency, transfer) is not "
                    "included.")
                return
        t_s = sweeps / rate_hz
        # Show the CALCULATION inline in terms of the CURRENTLY-SELECTED
        # rate unit (operator: "show the calculation" + "If the pulse rate
        # is set to pulse period, then have the calculation change
        # accordingly"):
        #   rate (pps)   AVERAGE →  "64 ÷ 100 pps ≈ 0.64 s / capture"
        #                SAMPLE  →  "1 sweep ÷ 100 pps ≈ 10 ms / capture"
        #   period (ms)  AVERAGE →  "64 × 10 ms ≈ 0.64 s / capture"
        #                SAMPLE  →  "1 sweep × 10 ms ≈ 10 ms / capture"
        # Numerically t = sweeps / rate = sweeps × period either way — the
        # DISPLAY just mirrors whichever the unit dropdown is showing so the
        # operator reads a calculation in the same terms they dialled in.
        lead = f"{sweeps}" if is_avg else "1 sweep"
        if self._rate_unit == self.UNIT_MS:
            period_disp = float(self.rate_pps.value())
            calc = f"{lead} × {period_disp:g} {self.UNIT_MS}"
            tip_op, tip_unit = "&times;", f"{period_disp:g} {self.UNIT_MS} period"
        else:
            calc = f"{lead} ÷ {rate_hz:g} pps"
            tip_op, tip_unit = "&divide;", f"{rate_hz:g} pps"
        lbl.setText(f"{calc} ≈ {self._fmt_acq_time(t_s)} / capture")
        if is_avg:
            basis = f"{self._acq_n_avg} averaged sweeps {tip_op} {tip_unit}"
        else:
            basis = f"single sweep (SAMPLE mode) {tip_op} {tip_unit}"
        lbl.setToolTip(
            "Approximate acquisition time per captured waveform.<br>"
            f"Estimate: {basis}.<br>"
            "Overhead (trigger latency, transfer) is not included.")

    @staticmethod
    def _fmt_acq_time(t_s: float) -> str:
        """Human-readable acquisition-time string with an adaptive unit."""
        if not (t_s > 0.0) or not math.isfinite(t_s):
            return ""
        if t_s < 1e-3:
            return f"{t_s * 1e6:.0f} µs"
        if t_s < 0.1:
            return f"{t_s * 1e3:.0f} ms"
        if t_s < 10.0:
            return f"{t_s:.2f} s"
        if t_s < 60.0:
            return f"{t_s:.1f} s"
        if t_s < 3600.0:
            m, s = divmod(int(round(t_s)), 60)
            return f"{m} min {s} s"
        h, rem = divmod(int(round(t_s)), 3600)
        return f"{h} h {rem // 60} min"

    def _on_rate_unit_changed(self, new_unit: str):
        """User flipped the unit toggle — convert the spinbox display
        without changing the underlying rate, and rebuild the spinbox
        bounds so the legal range reflects the new unit.
        """
        if new_unit == self._rate_unit:
            return
        # Capture rate_hz under the OLD unit so the conversion preserves
        # the pulse rate the user had dialled in.
        old_rate_hz = self._current_rate_hz()
        self._rate_unit = new_unit
        # Suffix + decimals + bounds + label flip per unit.
        self._suspend_signals = True
        try:
            self.rate_pps.setDecimals(3)
            # Label + suffix follow BOTH the unit AND the interpulse
            # on/off state (continuous mode shows "Pulse frequency [Hz]")
            # — single source of truth in ``_refresh_rate_label``.
            self._refresh_rate_label()
            # Rebuild bounds from the canonical rate envelope.
            # _update_rate_max below tightens further based on pulse width.
            self._reapply_rate_bounds()
            # Re-display the previous rate in the new unit.
            self._set_rate_hz(old_rate_hz)
        finally:
            self._suspend_signals = False
        # Pulse width may now allow a different ceiling — recompute.
        self._update_rate_max()
        self._emit()

    def _reapply_rate_bounds(self):
        """Set the spinbox's hardware-envelope min/max in current units.

        When unit = pps, larger rate = larger displayed value, so
        min/max map directly. When unit is a period, the relationship
        inverts (high rate = small period), so the spinbox min/max
        derive from the rate max/min respectively.
        """
        if self._rate_unit == self.UNIT_PPS:
            lo = self.RATE_HZ_MIN
            hi = self.RATE_HZ_MAX
        else:
            # Period bounds: lower bound = period at MAX rate, upper at MIN rate.
            lo = self._hz_to_unit(self.RATE_HZ_MAX)
            hi = self._hz_to_unit(self.RATE_HZ_MIN)
        self.rate_pps.setRange(lo, hi)

    def _update_rate_max(self):
        """Cap the rate spinbox so one full pulse + a 5 µs interpulse
        gap fits inside one period.

        max_rate = 1e6 / (total_pulse_µs + 5), clamped to the PlexStim
        100,000-pps hardware ceiling. Called every time ``_emit`` fires
        so the limit always reflects the current pulse width. The bound
        is computed in Hz and translated into the spinbox's current
        display unit so the cap moves with the user's view.

        When ``interpulse_check`` is OFF, the spinbox is also pinned
        to the running max — so the rate display tracks whatever the
        pulse width allows.
        """
        try:
            total_us = max(self.pattern().total_pulse_us, 1e-6)
        except Exception:
            return
        # The 5 µs minimum gap is a safety margin — when the user
        # explicitly turns interpulse delay OFF (unchecks the box) it
        # is waived so the next pulse can start immediately after the
        # discharge phase.
        gap_us = (self.MIN_INTERPULSE_GAP_US
                  if self.interpulse_check.isChecked() else 0.0)
        max_rate_hz = min(1e6 / (total_us + gap_us), self.RATE_HZ_MAX)
        max_rate_hz = max(max_rate_hz, self.RATE_HZ_MIN)
        self._suspend_signals = True
        try:
            if not self.interpulse_check.isChecked():
                # CONTINUOUS mode: the widths FOLLOW a typed frequency
                # (``_sync_widths_from_frequency``), so the spinbox ceiling
                # is the HARDWARE max — capping it at the current-width max
                # would block typing a higher frequency (whose whole point
                # is to shrink the widths).  The VALUE is pinned to the
                # ACHIEVED frequency ``1e6 / total`` — idempotent right
                # after a width-sync, and it corrects the display when the
                # sync clamped at the width spinboxes' minimums.
                if self._rate_unit == self.UNIT_PPS:
                    self.rate_pps.setMaximum(self.RATE_HZ_MAX)
                else:
                    self.rate_pps.setMinimum(
                        self._hz_to_unit(self.RATE_HZ_MAX))
                self._set_rate_hz(max_rate_hz)
                return
            # Translate the Hz ceiling into the spinbox's current unit.
            # In pps that's the upper bound; in period units that's the
            # LOWER bound (smaller period <=> higher rate).
            if self._rate_unit == self.UNIT_PPS:
                self.rate_pps.setMaximum(max_rate_hz)
            else:
                self.rate_pps.setMinimum(self._hz_to_unit(max_rate_hz))
            # Clamp behaviour — expressed in Hz so it's unit-agnostic.
            cur_hz = self._current_rate_hz()
            if cur_hz > max_rate_hz:
                self._set_rate_hz(max_rate_hz)
        finally:
            self._suspend_signals = False

    # ----------------------------------------------------------- delay toggles
    def _on_interphase_toggled(self, checked: bool):
        """Interphase delay: a CHECKED box keeps the spinbox enabled
        and ``pattern()`` reads its value; UNCHECKED disables the
        spinbox and ``pattern()`` forces ``T_iph = 0``."""
        self.interphase_us.setEnabled(checked)
        self._emit()

    def _on_discharge_toggled(self, checked: bool):
        """Discharge-delay (default on): disable the spinbox when off;
        ``pattern()`` uses 0 in that case."""
        self.discharge_us.setEnabled(checked)
        self._emit()

    def _on_interpulse_toggled(self, checked: bool):
        """Interpulse delay: a CHECKED box means the user picks the
        rate freely (within the pulse-width ceiling).  An UNCHECKED box
        forces "no interpulse delay" — the waveform is CONTINUOUS, so the
        knob becomes the pulse **FREQUENCY (Hz)** (operator: "allow the
        pulse rate to be called pulse frequency (Hz)") and stays EDITABLE
        in BOTH directions (operator: "don't disable width — it gives the
        user [the choice] to change width or frequency/period"):

          * editing the FREQUENCY rescales the phase WIDTHS so the pulse
            fills exactly one period (``_sync_widths_from_frequency``);
          * editing a WIDTH updates the displayed frequency (the
            ``_update_rate_max`` pin, ``1e6 / total_pulse_us``).

        Toggling the box back ON restores the rate the user had typed
        before unchecking.
        """
        if not checked:
            # Capture the user-typed rate (in Hz) so toggling on
            # again restores it even if the unit toggle moved
            # meanwhile.
            self._saved_rate_hz = self._current_rate_hz()
        else:
            # Restore the prior rate, clamped to whatever the current
            # pulse-width-derived ceiling allows.
            if self._saved_rate_hz is not None:
                self._suspend_signals = True
                try:
                    self._set_rate_hz(self._saved_rate_hz)
                finally:
                    self._suspend_signals = False
            self._saved_rate_hz = None
        # The knob stays EDITABLE in both modes (it was previously locked
        # while interpulse was OFF); the label + suffix flip between
        # "Pulse rate [pps]" and "Pulse frequency [Hz]".
        self.rate_pps.setEnabled(True)
        self._refresh_rate_label()
        self._emit()

    # --------------------------------------------------- burst / pulse-train
    def set_burst_available(self, available: bool) -> None:
        """Show / hide the burst group for the embedding experiment tab.

        SP / CP / LP support burst stimulation; VT / PS do not (VT ramps a
        single pulse's amplitude, so a burst is meaningless).  When hidden,
        ``pattern()`` never stamps the burst fields even if a stale pref left
        the enable box ticked, so a non-burst tab can't emit a burst."""
        self._burst_available = bool(available)
        grp = getattr(self, "burst_group", None)
        if grp is not None:
            grp.setVisible(self._burst_available)
        self._refresh_burst()

    def _on_burst_enable_toggled(self, checked: bool) -> None:
        """Enable the pulses-per-burst + burst-period spins only when burst
        mode is on; refresh the readout and re-emit so the preview + pattern
        pick up the change."""
        for w in (getattr(self, "pulses_per_burst_spin", None),
                  getattr(self, "burst_period_ms", None)):
            if w is not None:
                w.setEnabled(checked)
        self._refresh_burst()
        self._emit()

    def _burst_enabled(self) -> bool:
        """True when this panel should emit a burst pattern — the tab supports
        it AND the operator ticked the enable box.  All attr reads are
        getattr-guarded: ``pattern()`` can fire via ``_on_mode_changed`` during
        construction BEFORE the burst widgets / ``_burst_available`` exist."""
        chk = getattr(self, "burst_enable_check", None)
        return bool(getattr(self, "_burst_available", False)
                    and chk is not None and chk.isChecked())

    @staticmethod
    def _pattern_is_khfac_shape(pat) -> bool:
        """True for a continuous-sinusoidal (KHFAC) shape: exactly two phases,
        BOTH sinusoidal.  Mode-agnostic (works whichever mode built ``pat``).
        The interpulse discharge applies only to this regime."""
        try:
            phs = pat.phases
            return len(phs) == 2 and all(
                p.shape == SHAPE_SINUSOIDAL for p in phs)
        except Exception:
            return False

    def _discharge_mode_on(self) -> bool:
        """Discharge Mode = the PlexStim auto-discharge (``auto_discharge_combo``
        On/Off).  getattr-guarded (may not exist yet / in a test stub → don't
        block)."""
        cb = getattr(self, "auto_discharge_combo", None)
        if cb is None:
            return True
        try:
            return bool(cb.currentData())
        except Exception:
            return True

    def _interpulse_discharge_enabled(self, pat) -> bool:
        """True when ``pattern()`` should render the trailing discharge as a
        SHORT: the toggle is ticked, the pattern is a KHFAC sinusoid WITH a
        trailing discharge delay to replace, AND Discharge Mode is on."""
        chk = getattr(self, "interpulse_discharge_check", None)
        if not (chk is not None and chk.isChecked()
                and self._pattern_is_khfac_shape(pat)):
            return False
        try:
            if float(pat.phases[-1].delay_after_us) <= 0:
                return False          # no 0-µA step to replace
        except Exception:
            return False
        return self._discharge_mode_on()

    def _refresh_interpulse_discharge(self) -> None:
        """SHOW the discharge-as-short toggle for a symmetric biphasic
        sinusoidal pattern (KHFAC); ENABLE it only when Discharge Mode
        (auto-discharge) is on AND a discharge delay exists to replace
        (operator: "only enabled when the Discharge Mode is enabled").
        getattr-guarded — may fire during construction before the widgets
        exist."""
        chk = getattr(self, "interpulse_discharge_check", None)
        if chk is None:
            return
        try:
            sym = getattr(self, "symmetry", None)
            pc = getattr(self, "phase_count", None)
            sc = getattr(self, "shape_combo", None)
            show = (sym is not None and sym.currentText() == SYMMETRIC
                    and pc is not None and pc.currentText() == BIPHASIC
                    and sc is not None
                    and sc.currentData() == SHAPE_SINUSOIDAL)
        except Exception:
            show = False
        chk.setVisible(bool(show))
        dchk = getattr(self, "discharge_check", None)
        has_discharge = bool(dchk is not None and dchk.isChecked())
        chk.setEnabled(bool(show and self._discharge_mode_on()
                            and has_discharge))

    def _refresh_burst(self) -> None:
        """Clamp the burst-period MINIMUM to the burst span (so the burst is
        always valid — all N pulses fit inside the period, mirroring the
        ``_update_rate_max`` rate clamp) and render the italic burst summary
        (intra-burst rate · burst rate · inter-burst gap · overall pulses/s).
        Cleared when burst mode is off / unavailable."""
        lbl = getattr(self, "burst_readout", None)
        if lbl is None:
            return
        if not self._burst_enabled():
            lbl.setText("")
            lbl.setStyleSheet("")
            return
        try:
            pat = self.pattern()
        except Exception:
            lbl.setText("")
            return
        if not pat.is_burst:
            lbl.setText("")
            lbl.setStyleSheet("")
            return
        # Clamp the period floor to the burst span so N pulses always fit
        # (validate() would otherwise raise at run start).  Guard signals so
        # the resulting value bump doesn't re-enter _emit; re-read after.
        # CAP the floor at the spinbox MAX (= the 125 000 ms PS_SetPeriod
        # ceiling) — a huge N × slow intra-rate can make the span exceed it,
        # and letting setMinimum inflate the max would program a device period
        # ABOVE the hardware limit.  When the span genuinely exceeds the max
        # period the burst can't be validly programmed → warn (below).
        _period_max_ms = float(self.burst_period_ms.maximum())
        want_min_ms = min(max(0.02, pat.burst_span_us / 1000.0), _period_max_ms)
        if abs(self.burst_period_ms.minimum() - want_min_ms) > 1e-9:
            # SAVE/RESTORE the suspend flag (not an absolute reset) so a future
            # caller that reaches _refresh_burst while already suspended isn't
            # silently un-suspended mid-block.
            _prev_suspend = self._suspend_signals
            self._suspend_signals = True
            try:
                self.burst_period_ms.setMinimum(want_min_ms)
            finally:
                self._suspend_signals = _prev_suspend
            try:
                pat = self.pattern()   # value may have bumped up to the floor
            except Exception:
                return

        def _fmt_us(us: float) -> str:
            return (f"{us / 1000.0:.4g} ms" if abs(us) >= 1000.0
                    else f"{us:.0f} µs")

        # The span can still exceed the (capped) period when the burst is
        # physically too long for the 125 s device ceiling — warn, since
        # validate() will reject it at Start.
        if pat.burst_span_us > pat.burst_period_us + 1e-6:
            lbl.setStyleSheet("color: palette(bright-text);")
            lbl.setText(
                f"⚠ Burst too long for the device: {pat.pulses_per_burst} "
                f"pulses span {_fmt_us(pat.burst_span_us)} but the maximum "
                f"burst period is {_fmt_us(pat.burst_period_us)}.  Reduce the "
                f"pulse count or raise the intra-burst rate.")
            return

        lbl.setStyleSheet("")
        lbl.setText(
            f"{pat.pulses_per_burst} pulses @ {pat.rate_hz:.4g} pps  ·  "
            f"burst rate {pat.burst_rate_hz:.4g} /s  ·  inter-burst gap "
            f"{_fmt_us(pat.inter_burst_gap_us)}  ·  "
            f"{pat.effective_pulse_rate_hz:.4g} pps overall")

    def _refresh_rate_label(self) -> None:
        """Sync the rate-row label + spinbox suffix with the current
        (interpulse on/off, display unit) combination:

          * interpulse ON  + pps → ``Pulse rate (f_stim) [pps]``
          * interpulse OFF + pps → ``Pulse frequency (f_stim) [Hz]`` — a
            CONTINUOUS waveform's rate IS its frequency (operator; the
            "pps" display convention applies to pulse RATES, and the
            operator explicitly asked for Hz here).
          * period unit (ms) → ``Pulse period (T_pulse) [ms]`` either way.
        """
        continuous = not self.interpulse_check.isChecked()
        if self._rate_unit == self.UNIT_PPS:
            if continuous:
                self.rate_pps.setSuffix(" Hz")
                self._rate_row_label.setText(
                    rich.field_label("Pulse frequency", rich.F_STIM, "Hz"))
            else:
                self.rate_pps.setSuffix(" " + rich.PPS)
                self._rate_row_label.setText(
                    rich.field_label("Pulse rate", rich.F_STIM, rich.PPS))
        else:
            self.rate_pps.setSuffix(" " + self.UNIT_MS)
            self._rate_row_label.setText(
                rich.field_label("Pulse period", rich.T_PULSE, self.UNIT_MS))

    def _sync_widths_from_frequency(self) -> None:
        """CONTINUOUS mode (interpulse delay OFF): the user edited the
        pulse FREQUENCY — rescale the phase WIDTHS so the pulse fills
        exactly one period (``Σ widths = 1e6/f − Σ delays``), preserving
        the per-phase width RATIOS (all width knobs scale by one factor).
        Interphase / discharge delays are kept as typed.  Spinbox min/max
        clamp naturally; the ``_update_rate_max`` pin then corrects the
        displayed frequency to whatever total was actually achieved.
        Arbitrary-table patterns are skipped (their per-row durations
        aren't auto-scaled)."""
        if self.phase_count.currentText() == ARBITRARY:
            return
        try:
            pat = self.pattern()
            widths = float(sum(ph.width_us for ph in pat.phases))
            delays = float(sum(ph.delay_after_us for ph in pat.phases))
        except Exception:
            return
        f_user = self._current_rate_hz()
        if not (f_user > 0.0) or widths <= 0.0:
            return
        target_widths = 1e6 / f_user - delays
        if target_widths <= 0.0:
            return          # delays alone exceed the period; the pin corrects
        factor = target_widths / widths
        if abs(factor - 1.0) < 1e-9:
            return
        triphasic = self.phase_count.currentText() == TRIPHASIC
        asym = (not triphasic) and (self.symmetry.currentText() == ASYMMETRIC)
        self._suspend_signals = True
        try:
            if asym:
                # Per-phase widths: each snaps to its nearest 1 µs value
                # (the spinbox's decimals=0 grid); the pin then shows the
                # achieved frequency.
                for sp in self.phase_width:
                    sp.setValue(float(sp.value()) * factor)
            else:
                # Shared width (the common / KHFAC case): snap to the 1 µs
                # grid neighbour whose ACHIEVED frequency is NEAREST the
                # typed one (operator: "Have the pulse frequency snap to
                # the nearest to satisfy the time resolution").  Plain
                # width rounding is NOT the same thing — f = 1e6/total is
                # nonlinear in the width, so near the midpoint the closer
                # WIDTH can be the farther FREQUENCY.
                cur_w = float(self.width_shared.value())
                w_ideal = cur_w * factor
                per_unit = widths / max(cur_w, 1e-12)   # Σwidths per knob-µs
                lo_w = max(math.floor(w_ideal), int(self.width_shared.minimum()))
                hi_w = min(math.ceil(w_ideal), int(self.width_shared.maximum()))
                best_w, best_err = None, float("inf")
                for cand in {lo_w, hi_w}:
                    if cand <= 0:
                        continue
                    f_cand = 1e6 / (per_unit * cand + delays)
                    err = abs(f_cand - f_user)
                    if err < best_err:
                        best_w, best_err = cand, err
                if best_w is not None:
                    self.width_shared.setValue(float(best_w))
        finally:
            self._suspend_signals = False

    # ----------------------------------------------------------- auto-discharge
    def _on_auto_discharge_combo_changed(self, _index: int):
        """User picked a new entry in the discharge-mode dropdown.

        On the FIRST switch to ``Off`` in a session, surface a
        confirmation dialog. If the user backs out, revert the combo
        to ``On`` without firing the external signal. Otherwise emit
        ``autoDischargeToggled(bool)`` for the main window to
        (a) sync every other pattern panel, (b) push the new state to
        the live PlexStim device via the connection panel, and
        (c) persist the preference.
        """
        checked = bool(self.auto_discharge_combo.currentData())
        if not checked and not self._auto_discharge_warned:
            box = QtWidgets.QMessageBox(self)
            box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
            box.setWindowTitle("Disable auto-discharge?")
            box.setText("Disabling auto-discharge is risky.")
            box.setInformativeText(
                "With auto-discharge OFF the stimulator does NOT short "
                "the electrode between pulses. Any charge imbalance "
                "(per-pulse rounding from the 30 nA current grid, "
                "asymmetric phase shapes, capacitively-coupled pulses "
                "without a perfect anodic recharge) accumulates "
                "pulse-to-pulse and drifts the electrode-tissue "
                "interface DC offset.<br><br>"
                "<b>This can drive the electrode out of the safe water "
                "window and trigger irreversible faradaic reactions "
                "(electrode corrosion, tissue damage).</b><br><br>"
                "Only disable for short, instrumented experiments where "
                "you specifically need the electrode to float between "
                "pulses (e.g. measuring open-circuit potential).<br><br>"
                "Continue with auto-discharge OFF?")
            box.setTextFormat(QtCore.Qt.TextFormat.RichText)
            box.setStandardButtons(
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No)
            box.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
            choice = box.exec()
            if choice != QtWidgets.QMessageBox.StandardButton.Yes:
                # User backed out — flip the combo back to ``On``
                # (index 0) without firing this handler again.
                self.auto_discharge_combo.blockSignals(True)
                try:
                    self.auto_discharge_combo.setCurrentIndex(0)
                finally:
                    self.auto_discharge_combo.blockSignals(False)
                return
            self._auto_discharge_warned = True
        elif checked:
            # Re-enabling re-arms the warning so the next Off prompts
            # again. The cost of an unflagged charge runaway is much
            # higher than the cost of a confirmation click.
            self._auto_discharge_warned = False
        # Tell the rest of the app (main window → sibling panels →
        # connection panel → device).
        self.autoDischargeToggled.emit(checked)
        # Discharge Mode gates the "discharge between pulses (short)" toggle's
        # enabled state — refresh it now that the mode changed.
        self._refresh_interpulse_discharge()

    def set_auto_discharge_silent(self, checked: bool) -> None:
        """Programmatically update the discharge-mode dropdown state
        WITHOUT firing the user-change handler.

        Used by the main window to sync sibling pattern panels (one
        per experiment tab) when ANY of them flips the dropdown, and
        to apply the persisted preference at startup. The ``warned``
        flag is cleared on a transition to ``On`` so a subsequent
        manual switch to ``Off`` still raises the warning.
        """
        target_index = 0 if bool(checked) else 1
        if self.auto_discharge_combo.currentIndex() == target_index:
            return
        self.auto_discharge_combo.blockSignals(True)
        try:
            self.auto_discharge_combo.setCurrentIndex(target_index)
        finally:
            self.auto_discharge_combo.blockSignals(False)
        if checked:
            self._auto_discharge_warned = False
        # Discharge Mode gates the discharge-as-short toggle's enabled state.
        self._refresh_interpulse_discharge()

    def _emit(self, *_):
        # Refresh the KHFAC interpulse-discharge toggle visibility on every
        # change (incl. during suspend/restore) — cheap, side-effect-free.
        self._refresh_interpulse_discharge()
        if self._suspend_signals:
            return
        # CONTINUOUS mode, FREQUENCY edited (the signal came from the rate
        # spinbox): rescale the widths to match BEFORE the pin below reads
        # the total — otherwise ``_update_rate_max`` would clobber the typed
        # frequency with the stale ``1e6/old_total``.  Width edits don't
        # take this path (their sender is the width spinbox), so they keep
        # the width→frequency direction via the pin.
        try:
            _from_rate = self.sender() is self.rate_pps
        except Exception:
            _from_rate = False
        if _from_rate and not self.interpulse_check.isChecked():
            self._sync_widths_from_frequency()
        # Always re-clamp the rate ceiling first so the emitted pattern
        # already reflects the (possibly newly-tighter) max rate.
        self._update_rate_max()
        # Pseudo-cap-coupled feasibility envelope. Tightens the
        # anodic-width spinbox's minimum (in LOCK_WIDTH mode) so
        # the user can't type a value that would force the solver
        # into the infeasible regime. See
        # :meth:`_refresh_cap_coupled_bounds` for the math; called
        # before ``pattern()`` so the bounds are settled by the
        # time the pattern is read.
        self._refresh_cap_coupled_bounds()
        # Refresh the locked-parameter min/max readout (replaces
        # the earlier formula panel — users wanted a compact
        # "what range can I type here?" indicator). Idempotent +
        # cheap (a couple of arithmetic ops + a string format).
        # Runs even when the asymmetric form is hidden — no harm
        # because the label itself is hidden via
        # ``_on_mode_changed``, and the method early-returns when
        # asymmetric mode is inactive.
        self._refresh_locked_param_bounds()
        # Symmetric-mode slope readout for linear-variant shapes.
        # Idempotent — clears the label in non-symmetric / non-linear
        # contexts so the form row doesn't leave a stale value.
        self._refresh_sym_slope_readout()
        # Burst: clamp the burst-period floor to the span + refresh the burst
        # readout BEFORE the emit so the emitted pattern reflects the clamp.
        self._refresh_burst()
        self.patternChanged.emit(self.pattern())
        # Charge-balance warning is only meaningful when the user has
        # manual control over both phases — biphasic + asymmetric +
        # the "Off (manual)" charge-balance mode. Hide otherwise.
        triphasic = self.phase_count.currentText() == TRIPHASIC
        asym = (not triphasic) and (self.symmetry.currentText() == ASYMMETRIC)
        manual = self.charge_mode.currentText() == CHARGE_BAL_OFF
        self.balanceWarningVisibility.emit(asym and manual)
        # Refresh the approximate-acquisition-time readout — the rate may
        # have just changed (and _update_rate_max above may have clamped
        # it), so recompute after the pattern is emitted.
        self._update_acq_time_label()

    def _refresh_sym_slope_readout(self) -> None:
        """Render the per-phase slope (µA/µs) for linear shapes in
        symmetric biphasic mode.

        Active only when:
          * Phase count is biphasic.
          * Symmetry is symmetric.
          * The selected shape is one of the linear variants
            (LIN_INC, LIN_DEC, linear_inc_dec, linear_dec_inc).

        Hidden / cleared otherwise — the label widget keeps its
        attached form row visible (so the layout doesn't reflow on
        every shape change), but the text is empty so no row
        height is consumed.

        Slope per phase is ``|peak_amp| / phase_width`` µA/µs. We
        annotate each phase with an up- or down-arrow indicating
        the direction of current change in MAGNITUDE during the
        phase (cathodic phase ramping toward more-negative current
        is "up" in magnitude, etc.). For pure linear shapes
        (LIN_INC / LIN_DEC) both phases share the same direction.
        For the inc-dec / dec-inc pairs the phases have opposite
        directions.
        """
        widget = getattr(self, "_sym_slope_lbl", None)
        label_widget = getattr(self, "_sym_slope_label_widget", None)
        if widget is None or label_widget is None:
            return
        # Visibility / activity gates.
        try:
            kind = self.phase_count.currentText()
            sym = self.symmetry.currentText()
        except Exception:
            kind = sym = ""
        active = (kind == BIPHASIC and sym == SYMMETRIC)
        if not active:
            widget.setText("")
            label_widget.setVisible(False)
            widget.setVisible(False)
            return

        try:
            shape_id = self.shape_combo.currentData() or SHAPE_RECTANGULAR
        except Exception:
            shape_id = SHAPE_RECTANGULAR

        # Determine per-phase shape directions.
        # Map: shape_id → (phase_0_direction, phase_1_direction)
        # where direction is "up" (ramping up in magnitude) or
        # "down" (ramping down in magnitude).
        UP, DOWN = "up", "down"
        per_phase_dir = None
        if shape_id == SHAPE_LINEAR_INCREASING:
            per_phase_dir = (UP, UP)
        elif shape_id == SHAPE_LINEAR_DECREASING:
            per_phase_dir = (DOWN, DOWN)
        elif shape_id == "linear_inc_dec":
            per_phase_dir = (UP, DOWN)
        elif shape_id == "linear_dec_inc":
            per_phase_dir = (DOWN, UP)

        if per_phase_dir is None:
            # Non-linear shape — slope readout doesn't apply.
            widget.setText("")
            label_widget.setVisible(False)
            widget.setVisible(False)
            return

        try:
            mag = abs(float(self.amp_excite.value()))
            w = float(self.width_shared.value())
        except Exception:
            mag = w = 0.0
        if w <= 0:
            widget.setText("(width is zero)")
            label_widget.setVisible(True)
            widget.setVisible(True)
            return

        slope = mag / w   # µA / µs (magnitude)

        def _arrow(d: str) -> str:
            return "↑" if d == UP else "↓"

        d0, d1 = per_phase_dir
        html = (
            f"<b>Phase 1:</b> {slope:.3f}&nbsp;µA/µs {_arrow(d0)} "
            f"&nbsp;&nbsp; "
            f"<b>Phase 2:</b> {slope:.3f}&nbsp;µA/µs {_arrow(d1)}"
        )
        # Pure-LIN_INC / LIN_DEC have both phases ramping the same
        # direction; surface that note since it's a useful design
        # cue (the dI/dt has the same sign on both phases).
        if d0 == d1:
            same_word = "rising" if d0 == UP else "falling"
            html += (
                f"<br><span style='color:#666; font-size:8pt;'>"
                f"Both phases {same_word} in magnitude — "
                f"matches Sahin & Tie 2007's {'LinInc' if d0 == UP else 'LinDec'}"
                f" monophasic.</span>")
        else:
            html += (
                f"<br><span style='color:#666; font-size:8pt;'>"
                f"Mirrored linear pair — phase 1 ramps "
                f"{'up' if d0 == UP else 'down'}, phase 2 "
                f"{'up' if d1 == UP else 'down'} (the two-triangle "
                f"biphasic).</span>")
        widget.setText(html)
        label_widget.setVisible(True)
        widget.setVisible(True)

    def _refresh_locked_param_bounds(self) -> None:
        """Show the min / max of the LOCKED second-phase parameter
        that keeps charge balance achievable within hardware limits.

        Replaces the earlier formula readout — users wanted a
        compact "what range can I type here?" indicator rather
        than a derivation. The label sits under the asymmetric-
        shape combo and updates on every ``_emit``.

        Which value is "the locked parameter" depends on the
        shape + the active charge-balance / lock combo:

        * **Rectangular asymmetric** uses the ``charge_mode``
          dropdown:
              ``CHARGE_BAL_AMP`` — user types phase-2 WIDTH; the
                amplitude is auto-derived. Locked = width.
                Min = (|I_c|·t_c) / I_max so the auto-derived
                amplitude doesn't need to exceed the 1000-µA
                ceiling. Max = the spinbox's hardware ceiling
                (the device tops out at the per-phase width
                limit; nothing tighter applies).
              ``CHARGE_BAL_WID`` — user types phase-2 AMPLITUDE;
                width is auto-derived. Locked = amplitude.
                Min = (|I_c|·t_c) / W_max so the derived width
                fits inside the spinbox's hardware ceiling
                (typically 1 µs floor → effectively 0+ε floor
                here). Max = STIM_MAX_AMPLITUDE_UA.
              ``CHARGE_BAL_OFF`` — both phase-2 knobs are user-
                controlled; no "locked parameter" concept; the
                label reads "manual mode — no auto-derived
                bounds".

        * **Pseudo-capacitively-coupled** uses the ``cap_lock_combo``
          (LOCK_WIDTH / LOCK_AMPLITUDE):
              ``LOCK_WIDTH`` — locked = phase-2 width. Min = the
                feasibility floor (the same value
                ``_refresh_cap_coupled_bounds`` uses to clamp the
                spinbox's minimum), max = spinbox's hardware
                ceiling.
              ``LOCK_AMPLITUDE`` — locked = phase-2 amplitude.
                Min ≈ 0 (any positive amp produces a feasible
                pure-decay solution; t_a just grows). Max =
                STIM_MAX_AMPLITUDE_UA (1000 µA).

        * **Linear inc-dec / dec-inc** — both phase-2 knobs are
          user-controlled (no auto-balance), so no locked-
          parameter readout. Label is empty.

        Output format (single line, rich-text):
            "Locked t_a: 50.0 – 65535 µs  (auto-derived |I_a|
            stays ≤ 1000 µA hardware cap)"
        Tightens on every emit so the user can sanity-check
        whether their typed value still fits the envelope.
        """
        widget = getattr(self, "_asym_formula_lbl", None)
        if widget is None:
            return
        # Only show the bounds in asymmetric biphasic mode. Clear
        # the label otherwise (matches what
        # ``_on_mode_changed`` does for visibility).
        try:
            triphasic = (self.phase_count.currentText() == TRIPHASIC)
            arbitrary = (self.phase_count.currentText() == ARBITRARY)
            asym = (not triphasic and not arbitrary
                    and self.symmetry.currentText() == ASYMMETRIC)
        except Exception:
            asym = False
        if not asym:
            widget.setText("")
            return
        try:
            shape_id = self.asym_shape_combo.currentData() or ""
        except Exception:
            shape_id = ""

        try:
            from ..config import STIM_MAX_AMPLITUDE_UA
        except Exception:
            STIM_MAX_AMPLITUDE_UA = 1000.0
        try:
            Ic = abs(float(self.phase_amp[0].value()))
            tc = abs(float(self.phase_width[0].value()))
        except Exception:
            return
        # Q in µA·µs (numerically equal to nC). Used for both
        # auto-balance ("amplitude needs Q/t_a") and pseudo-cap-
        # coupled feasibility ("t_a min = Q / I_max") math.
        Q = Ic * tc

        def _sub(name, sub):
            return f"<i>{name}</i><sub>{sub}</sub>"

        # Pull the spinbox's actual range as the "hardware ceiling
        # / floor" we report — the underlying ``_dspin`` factory
        # clamps to PlexStim's per-phase width / amplitude limits,
        # so we don't have to re-derive them here.
        amp_max_hw = float(self.phase_amp[1].maximum() or STIM_MAX_AMPLITUDE_UA)
        amp_min_hw = abs(float(self.phase_amp[1].minimum() or 0.0))
        # Spinbox max/min are sign-locked under the polarity-fix
        # introduced earlier; treat them as magnitudes.
        amp_max_hw = max(abs(amp_max_hw), abs(amp_min_hw))
        wid_max_hw = float(self.phase_width[1].maximum() or 65535.0)
        wid_min_hw = float(self.phase_width[1].minimum() or 1.0)

        html = ""

        # All asymmetric shapes whose two phases share the same
        # shape-duty factor (rectangle 1.0, ramp 0.5, exp at canonical
        # τ ≈ 0.198) share the rect-asym auto_balance arithmetic —
        # the duty factors cancel in the A·W charge-balance equation,
        # so the locked-parameter floors (Q/I_max, Q/W_max) derived
        # for rect-asym hold verbatim. Treat them as rect-like for the
        # purposes of this readout. Cap-coupled has its own branch
        # (mixed-shape: rect cath + exp-decay anod), so it's
        # excluded.
        rect_like = (shape_id == ASYM_SHAPE_RECT)
        if rect_like:
            # Rect uses the charge_mode dropdown to pick which phase-2
            # knob is auto-derived (cap_coupled and mix_match have
            # their own dedicated branches below).
            mode = self.charge_mode.currentText()
            if mode == CHARGE_BAL_AMP:
                # Locked = width; |I_a| = Q / t_a is auto-derived.
                # Need |I_a| ≤ 1000 → t_a ≥ Q / 1000.
                if STIM_MAX_AMPLITUDE_UA > 0 and Q > 0:
                    ta_min = Q / STIM_MAX_AMPLITUDE_UA
                else:
                    ta_min = wid_min_hw
                ta_min = max(ta_min, wid_min_hw)
                html = (
                    f"<b>Locked {_sub('t','a')}:</b> "
                    f"{ta_min:.1f} – {wid_max_hw:.0f}&nbsp;µs<br>"
                    f"<span style='color:#666; font-size:8pt;'>"
                    f"Min keeps auto-derived |{_sub('I','a')}| "
                    f"≤ {STIM_MAX_AMPLITUDE_UA:.0f}&nbsp;µA "
                    f"hardware cap; max is the spinbox's hardware "
                    f"ceiling.</span>")
            elif mode == CHARGE_BAL_WID:
                # Locked = amp; t_a = Q / |I_a|. Need t_a ≤ W_max
                # → |I_a| ≥ Q / W_max.
                if wid_max_hw > 0 and Q > 0:
                    ia_min = Q / wid_max_hw
                else:
                    ia_min = 0.0
                ia_min = max(ia_min, 0.0)
                html = (
                    f"<b>Locked |{_sub('I','a')}|:</b> "
                    f"{ia_min:.3f} – {amp_max_hw:.0f}&nbsp;µA<br>"
                    f"<span style='color:#666; font-size:8pt;'>"
                    f"Min keeps auto-derived {_sub('t','a')} "
                    f"≤ {wid_max_hw:.0f}&nbsp;µs; max is the "
                    f"hardware ceiling.</span>")
            else:
                # Manual mode — no locked parameter.
                html = (
                    "<span style='color:#666;'>Manual charge "
                    "balance — both phase-2 knobs are user-"
                    "controlled. No auto-derived bounds.</span>")
        elif shape_id == ASYM_SHAPE_CAP:
            # Pseudo-cap-coupled uses the cap_lock_combo for the
            # explicit lock concept.
            try:
                lock_mode = self.cap_lock_combo.currentData() or LOCK_WIDTH
            except Exception:
                lock_mode = LOCK_WIDTH
            if lock_mode == LOCK_WIDTH:
                # Locked = t_a. Min = feasibility floor =
                # Q / I_max (else even a 1000-µA flat-top of
                # width t_a can't deliver Q). Max = spinbox max.
                if STIM_MAX_AMPLITUDE_UA > 0 and Q > 0:
                    ta_min = Q / STIM_MAX_AMPLITUDE_UA
                else:
                    ta_min = wid_min_hw
                ta_min = max(ta_min, wid_min_hw)
                html = (
                    f"<b>Locked {_sub('t','a')}:</b> "
                    f"{ta_min:.1f} – {wid_max_hw:.0f}&nbsp;µs<br>"
                    f"<span style='color:#666; font-size:8pt;'>"
                    f"Min is the feasibility floor — below it, "
                    f"even a {STIM_MAX_AMPLITUDE_UA:.0f}-µA "
                    f"flat-top of width {_sub('t','a')} can't "
                    f"deliver the cathodic charge.</span>")
            else:
                # LOCK_AMPLITUDE — locked = |I_a|. Min depends on
                # the τ-mode:
                #   * Auto τ: any positive amp produces a feasible
                #     pure-decay solution (t_a grows as needed).
                #   * Manual τ: I_a·τ must EXCEED Q for the decay
                #     integral to reach Q; floor = Q / τ.
                # Max = 1000 µA hw cap in either case.
                manual_tau = (self.tau_mode_combo.currentData()
                              == TAU_MODE_MANUAL)
                if manual_tau:
                    try:
                        tau_user = float(self.tau_us.value())
                    except (TypeError, ValueError):
                        tau_user = 0.0
                    if tau_user > 0 and Q > 0:
                        ia_min = Q / tau_user
                    else:
                        ia_min = 0.001
                    html = (
                        f"<b>Locked |{_sub('I','a')}|:</b> "
                        f"{ia_min:.3f} – {STIM_MAX_AMPLITUDE_UA:.0f}"
                        f"&nbsp;µA<br>"
                        f"<span style='color:#666; font-size:8pt;'>"
                        f"With manual τ = {tau_user:.0f} µs, the "
                        f"asymptotic charge ceiling is |{_sub('I','a')}|·τ; "
                        f"min keeps that &gt; "
                        f"{abs(Q*1e-3):.2f}&nbsp;nC.</span>")
                else:
                    html = (
                        f"<b>Locked |{_sub('I','a')}|:</b> "
                        f"{0.001:.3f} – {STIM_MAX_AMPLITUDE_UA:.0f}"
                        f"&nbsp;µA<br>"
                        f"<span style='color:#666; font-size:8pt;'>"
                        f"Any positive amplitude produces a feasible "
                        f"pure-decay solution; max is the "
                        f"hardware cap.</span>")
            # Pulse-rate / pulse-period range — appended to the
            # cap-coupled readout so the user sees the timing
            # envelope at a glance. Not added to the rect-asym
            # / linear branches because their pulse durations
            # are user-typed directly (no surprise from auto-
            # derived t_a in cap-coupled mode where shrinking
            # |I_a| stretches t_a → lowers the rate ceiling).
            #
            # Constraints:
            #   * Pulse must fit inside the period — leaves enough
            #     time for cathodic + interphase + flat + decay +
            #     discharge before the next pulse fires.
            #   * Plus a 5 µs interpulse gap when the interpulse-
            #     delay checkbox is on (waived to 0 when off, per
            #     ``_update_rate_max``).
            #
            # max_rate = 1e6 / (total_pulse_us + gap_us)
            # min_period = total_pulse_us + gap_us
            # min_rate / max_period are the hardware floor /
            # ceiling on the rate spinbox.
            try:
                total_us = float(self.pattern().total_pulse_us)
            except Exception:
                total_us = 0.0
            interpulse_on = self.interpulse_check.isChecked()
            gap_us = (self.MIN_INTERPULSE_GAP_US
                      if interpulse_on else 0.0)
            if total_us > 0:
                min_period_us = total_us + gap_us
                # max_rate clamped at the PlexStim 100 kHz hw cap;
                # min_rate is the spinbox's 0.008 Hz floor.
                max_rate_hz = min(1e6 / min_period_us,
                                  self.RATE_HZ_MAX)
                min_rate_hz = self.RATE_HZ_MIN
                # max_period = 1 / min_rate. Print in ms when it
                # exceeds 10 ms (otherwise µs reads cleaner).
                max_period_us = 1e6 / min_rate_hz if min_rate_hz > 0 else 0.0
                # Format the period range in a unit that's
                # readable for both ends. Min is typically
                # hundreds-to-thousands of µs (cap-coupled is
                # multi-µs already); max is seconds. Show min in
                # µs and max in s for the wide span.
                min_period_str = f"{min_period_us:.1f}&nbsp;µs"
                max_period_s = max_period_us * 1e-6
                if max_period_s >= 1.0:
                    max_period_str = f"{max_period_s:.1f}&nbsp;s"
                else:
                    max_period_str = f"{max_period_us:.0f}&nbsp;µs"
                # Format the rate range. Show in kpps / pps
                # depending on magnitude.
                if max_rate_hz >= 1000:
                    max_rate_str = f"{max_rate_hz*1e-3:.2f}&nbsp;kpps"
                else:
                    max_rate_str = f"{max_rate_hz:.1f}&nbsp;pps"
                min_rate_str = f"{min_rate_hz:.3f}&nbsp;pps"
                gap_note = (
                    "5&nbsp;µs interpulse gap" if interpulse_on
                    else "no interpulse gap (toggle is OFF)")
                html += (
                    "<br><br>"
                    f"<b>Pulse rate:</b> "
                    f"{min_rate_str} – {max_rate_str}<br>"
                    f"<b>Pulse period:</b> "
                    f"{min_period_str} – {max_period_str}<br>"
                    f"<span style='color:#666; font-size:8pt;'>"
                    f"Min period = pulse duration "
                    f"({total_us:.1f}&nbsp;µs) + {gap_note}; "
                    f"max rate is its reciprocal. "
                    f"Hardware floor = "
                    f"{self.RATE_HZ_MIN:.3f}&nbsp;pps; "
                    f"ceiling = "
                    f"{self.RATE_HZ_MAX*1e-3:.0f}&nbsp;kpps."
                    f"</span>")
        elif shape_id == ASYM_SHAPE_MIX_MATCH:
            # Mix-and-match — bounds depend on each phase's shape
            # duty + the per-phase offset. Compute Q_head with the
            # actual phase-1 shape/duty/offset, then derive the
            # locked-parameter floor from |I_a|·W_a·duty_a balance.
            try:
                p0_shape = (self.mix_phase_shape_combo[0].currentData()
                            or SHAPE_RECTANGULAR)
                p1_shape = (self.mix_phase_shape_combo[1].currentData()
                            or SHAPE_RECTANGULAR)
            except Exception:
                p0_shape = p1_shape = SHAPE_RECTANGULAR
            duty0 = _shape_duty(p0_shape)
            duty1 = _shape_duty(p1_shape)
            # Cathodic phase's effective signed charge (per µs of W):
            # ``Ic_signed · duty + so_signed · (1 − duty)`` where Ic is
            # the signed phase-1 amplitude and so is its signed
            # offset, both clamped to |Ic|. We work in magnitudes
            # (Q = |…|) since the locked-parameter floor is sign-
            # invariant — the auto-derived phase 2 amplitude
            # carries the opposite sign automatically.
            try:
                off0 = (abs(float(self.mix_phase_offset_ua[0].value()))
                        if p0_shape != SHAPE_RECTANGULAR else 0.0)
            except Exception:
                off0 = 0.0
            # Effective per-µs charge contribution from phase 1:
            #   |Q1| / W_1 = |Ic| · duty0 + min(|off0|, |Ic|) · (1 − duty0)
            so0 = min(off0, Ic)
            q_head_per_us = Ic * duty0 + so0 * (1.0 - duty0)
            Q_head = q_head_per_us * tc   # µA·µs ↔ nC

            mode = self.charge_mode.currentText()
            if mode == CHARGE_BAL_AMP:
                # Locked = phase-2 width. |I_a| = Q_head / (W_a · duty1)
                # must satisfy |I_a| ≤ STIM_MAX_AMPLITUDE_UA
                # → W_a ≥ Q_head / (I_max · duty1).
                if (STIM_MAX_AMPLITUDE_UA > 0 and duty1 > 0
                        and Q_head > 0):
                    ta_min = Q_head / (STIM_MAX_AMPLITUDE_UA * duty1)
                else:
                    ta_min = wid_min_hw
                ta_min = max(ta_min, wid_min_hw)
                html = (
                    f"<b>Locked {_sub('t','a')}:</b> "
                    f"{ta_min:.1f} – {wid_max_hw:.0f}&nbsp;µs<br>"
                    f"<span style='color:#666; font-size:8pt;'>"
                    f"Mix-and-match auto-balance derives "
                    f"|{_sub('I','a')}| from "
                    f"{_sub('Q','head')} / "
                    f"({_sub('W','a')}·duty_a) with the phase-2 "
                    f"shape's duty factor "
                    f"(duty_a = {duty1:.3f}). Min keeps the "
                    f"derived amplitude ≤ "
                    f"{STIM_MAX_AMPLITUDE_UA:.0f}&nbsp;µA hardware "
                    f"cap.</span>"
                )
            elif mode == CHARGE_BAL_WID:
                # Locked = phase-2 amplitude. t_a = Q_head /
                # (|I_a| · duty1) must satisfy t_a ≤ W_max
                # → |I_a| ≥ Q_head / (W_max · duty1).
                if wid_max_hw > 0 and duty1 > 0 and Q_head > 0:
                    ia_min = Q_head / (wid_max_hw * duty1)
                else:
                    ia_min = 0.0
                ia_min = max(ia_min, 0.0)
                html = (
                    f"<b>Locked |{_sub('I','a')}|:</b> "
                    f"{ia_min:.3f} – {amp_max_hw:.0f}&nbsp;µA<br>"
                    f"<span style='color:#666; font-size:8pt;'>"
                    f"Mix-and-match auto-balance derives "
                    f"{_sub('t','a')} from "
                    f"{_sub('Q','head')} / "
                    f"(|{_sub('I','a')}|·duty_a) with the phase-2 "
                    f"shape's duty factor "
                    f"(duty_a = {duty1:.3f}). Min keeps the "
                    f"derived width ≤ {wid_max_hw:.0f}&nbsp;µs.</span>"
                )
            else:
                # Manual mode — both phase-2 knobs are user-typed.
                html = (
                    f"<span style='color:#666;'>Mix-and-match, manual "
                    f"charge balance — both {_sub('t','a')} and "
                    f"|{_sub('I','a')}| are user-controlled "
                    f"(phase-2 shape duty = {duty1:.3f}). No "
                    f"auto-derived bounds.</span>"
                )
        else:
            # Defensive fallback for any unrecognised asymmetric
            # shape id — surface SOMETHING informative instead of
            # an empty field. Lists the shape and notes that no
            # locked-range readout is defined for it (typically
            # means a saved-prefs migration mismatch or a partially-
            # implemented shape entry).
            html = (
                f"<span style='color:#666;'>No locked-range "
                f"readout defined for shape {shape_id!r}.</span>"
            )
        widget.setText(html)

    def _refresh_cap_coupled_bounds(self) -> None:
        """Adjust the second-phase spinbox bounds so a user typing
        in the panel can't construct an infeasible pseudo-cap-coupled
        configuration.

        Feasibility constraint: in LOCK_WIDTH mode the solver can
        only balance ``Q_cath = |I_c| × t_c`` if there's enough
        anodic-width budget for a 1000-µA flat-top + exp-decay tail
        to deliver that charge. The minimum feasible
        ``t_a = Q_cath / I_max`` (in µA·µs ↔ µs at 1000 µA peak).
        Below that, even a full 1000-µA rectangle of width ``t_a``
        wouldn't deliver enough charge — the solver flags
        ``infeasible``.

        We dynamically set ``phase_width[1].minimum`` to that
        floor so:

        * If the user types a t_a below the floor, Qt clamps the
          spinbox to the floor automatically — no infeasible
          state is reachable through the GUI.
        * If the user changes ``I_c`` or ``t_c`` upward to a
          point where the EXISTING ``t_a`` would be infeasible,
          Qt re-clamps ``t_a`` upward at the new minimum.

        The floor is only applied when the panel is currently in
        biphasic + asymmetric + cap-coupled + LOCK_WIDTH mode.
        Other modes restore the spinbox's original (loose) lower
        bound.
        """
        # Cache the historical floor on first call so we can
        # restore it whenever the cap-coupled mode goes away.
        if not hasattr(self, "_phase_width_default_min"):
            try:
                self._phase_width_default_min = self.phase_width[1].minimum()
            except Exception:
                self._phase_width_default_min = 1.0

        triphasic = self.phase_count.currentText() == TRIPHASIC
        asym = (not triphasic
                and self.symmetry.currentText() == ASYMMETRIC)
        is_cap = (asym
                  and self.asym_shape_combo.currentData() == ASYM_SHAPE_CAP)
        # ``LOCK_WIDTH`` is the only branch where infeasibility can
        # occur — locked-amplitude pure-decay can stretch t_a as
        # needed, no minimum-width floor required.
        lock_width = (is_cap
                      and self.cap_lock_combo.currentData() == LOCK_WIDTH)
        if not lock_width:
            # Restore the default minimum when leaving cap-coupled
            # mode so the user can type small widths in other modes.
            self.phase_width[1].blockSignals(True)
            try:
                self.phase_width[1].setMinimum(
                    self._phase_width_default_min)
            finally:
                self.phase_width[1].blockSignals(False)
            return

        try:
            from ..config import STIM_MAX_AMPLITUDE_UA
            Ic = abs(float(self.phase_amp[0].value()))
            tc = abs(float(self.phase_width[0].value()))
        except Exception:
            return
        if STIM_MAX_AMPLITUDE_UA <= 0 or Ic == 0 or tc == 0:
            return
        # Q in µA·µs (numerically equal to nC). Minimum feasible
        # anodic width = Q / I_max.
        ta_min = (Ic * tc) / float(STIM_MAX_AMPLITUDE_UA)
        # Round up to the spinbox's display precision (decimals)
        # so Qt's clamping doesn't fight floating-point noise.
        decimals = self.phase_width[1].decimals()
        step = 10 ** (-decimals) if decimals > 0 else 1.0
        # Bump by one display step so the spinbox lands JUST above
        # the strict feasibility threshold — the solver's discrete-
        # quantisation refinement loop nudges around that boundary,
        # and landing exactly on it can flicker between feasible
        # and infeasible mid-edit.
        ta_min = ta_min + step
        # ``setMinimum`` will silently bump the current value up
        # if it falls below the new floor. Block signals around
        # the call so we don't recursively re-emit.
        self.phase_width[1].blockSignals(True)
        try:
            self.phase_width[1].setMinimum(
                max(self._phase_width_default_min, float(ta_min)))
        finally:
            self.phase_width[1].blockSignals(False)

    # ----------------------------------------------------------- profile gating
    def _filter_restricted_shapes(self, shapes):
        """Return ``shapes`` with profile-restricted entries removed
        when the current profile lacks access.

        ``shapes`` is an iterable of ``(label, shape_id)`` tuples
        (the same shape the SYMMETRIC_BIPHASIC_SHAPES /
        _MIX_MATCH_SHAPES constants use).  The restricted set is
        ``stimtest.gui.admin.RESTRICTED_SHAPES``, which extension
        packages populate at import time via
        ``register_extension_profile``.  Built-in admin sees
        everything; anonymous sees only the non-restricted base set.

        ``self._current_profile`` is a lowercase string ("none",
        "admin", or an extension-registered name like a
        collaborator package's tag).  We pass it directly to
        ``is_restricted_unlocked`` — that helper handles the
        built-in + extension-registered name lookup uniformly.
        """
        try:
            from .admin import (RESTRICTED_SHAPES,
                                is_restricted_unlocked)
            if is_restricted_unlocked(self._current_profile):
                return tuple(shapes)
            return tuple(
                (label, sid) for (label, sid) in shapes
                if sid not in RESTRICTED_SHAPES)
        except Exception:
            # Defensive: if the admin module fails to import (very
            # early in startup, or a stripped test env), fall back
            # to showing all shapes so the panel remains functional.
            return tuple(shapes)

    def set_profile(self, profile) -> None:
        """Update the panel's login profile and rebuild the shape
        dropdowns to add / remove the restricted entries.

        ``profile`` may be a :class:`stimtest.gui.admin.Profile` enum
        member, its string-value equivalent (the str-enum's
        ``.value``), or any extension-registered profile name string.

        A re-broadcast of the SAME profile name still triggers a
        rebuild — main_window calls this after auto-loading
        extensions so any newly-registered restricted shapes
        appear (or disappear) in the dropdowns.  The rebuild cost
        is trivial (a few combo box rows) so we don't bother with
        an early-return optimization.

        When a restricted shape is currently selected and the new
        profile loses access to it, the combo falls back to
        Rectangular and emits ``patternChanged`` so downstream
        widgets pick up the new pattern.
        """
        # Normalize input to the lowercase string form we store
        # internally.  Profile is a str-enum so members and raw
        # strings collapse to the same shape.
        try:
            new_profile = (profile.value
                           if hasattr(profile, "value")
                           else str(profile)).strip().lower()
        except Exception:
            new_profile = "none"
        self._current_profile = new_profile
        # Rebuild the symmetric biphasic shape combo, preserving
        # the user's current selection if still legal.  Safety note
        # (audit #6): ``_rebuild_shape_combo`` compares the post-
        # rebuild ``new_sid`` with the pre-rebuild ``prev_sid`` and
        # only emits ``patternChanged`` when they differ.  So a
        # re-broadcast of the SAME profile (used by main_window
        # after auto-loading extensions) won't spuriously fire
        # downstream pattern-rebuild work — the selection survives
        # the clear-and-repopulate.  If a future refactor changes
        # the default-selection logic, re-verify that property.
        self._rebuild_shape_combo(self.shape_combo, SYMMETRIC_BIPHASIC_SHAPES,
                                  preview_renderer=_render_shape_pixmap)
        # NOTE: ``asym_shape_combo`` (the parent dropdown for
        # asymmetric pulse shape — Rectangular / Cap-coupled /
        # Mix-and-match) is INTENTIONALLY not rebuilt here.  None of
        # the three entries in ``ASYMMETRIC_BIPHASIC_SHAPES`` are
        # restricted shape IDs — only the per-phase entries inside
        # the Mix-and-match flavour are (those live in
        # ``mix_phase_shape_combo`` below).  Audit #7 flagged this
        # as a future-proofing concern: if you ever add a restricted
        # ID to ASYMMETRIC_BIPHASIC_SHAPES, also add a
        # ``self._rebuild_shape_combo(self.asym_shape_combo, ...)``
        # call right here.
        # Rebuild each mix-and-match per-phase combo too.  These
        # exist only in asymmetric mode; the loop guard handles the
        # symmetric-mode (no list) case.
        if getattr(self, "mix_phase_shape_combo", None):
            # Inline copy of the _MIX_MATCH_SHAPES list — kept in
            # sync with the one in the constructor's asym block.
            _MIX = (
                ("Rectangular",       SHAPE_RECTANGULAR),
                ("Linear increasing", SHAPE_LINEAR_INCREASING),
                ("Linear decreasing", SHAPE_LINEAR_DECREASING),
                ("Sinusoidal",        SHAPE_SINUSOIDAL),
                ("Speedbumps",        SHAPE_SPEEDBUMPS),
                ("Bowtie",            SHAPE_BOWTIE),
                ("Halfpipe",          SHAPE_HALFPIPE),
                ("Gaussian",          SHAPE_GAUSSIAN),
                ("Exp decreasing",    SHAPE_EXP_DECAY),
                ("Exp increasing",    SHAPE_EXP_INCREASING),
            )
            cath_first = self.polarity.currentText().startswith("Cathod")
            phase_signs = (-1, +1) if cath_first else (+1, -1)
            for i, cb in enumerate(self.mix_phase_shape_combo):
                self._rebuild_shape_combo(
                    cb, _MIX,
                    preview_renderer=lambda sid, w_px, h_px, _p=phase_signs[i]:
                        _render_single_phase_pixmap(
                            sid, w_px=w_px, h_px=h_px, polarity=_p))

    def _rebuild_shape_combo(self, combo, shape_tuple,
                             *, preview_renderer) -> None:
        """Repopulate ``combo`` with the filtered shape list.

        Preserves the user's currently-selected shape if still
        legal after the filter.  Otherwise falls back to the first
        legal entry (always Rectangular by construction).
        """
        if combo is None:
            return
        prev_sid = combo.currentData()
        combo.blockSignals(True)
        try:
            combo.clear()
            filtered = self._filter_restricted_shapes(shape_tuple)
            for label, sid in filtered:
                try:
                    icon_pm = preview_renderer(sid, 80, 28)
                    combo.addItem(QtGui.QIcon(icon_pm), label, userData=sid)
                except Exception:
                    combo.addItem(label, userData=sid)
            # Restore prior selection if still legal.
            restored = False
            for i in range(combo.count()):
                if combo.itemData(i) == prev_sid:
                    combo.setCurrentIndex(i)
                    restored = True
                    break
            if not restored:
                # Previously-selected shape is now hidden — fall
                # back to Rectangular (index 0 by construction).
                combo.setCurrentIndex(0)
        finally:
            combo.blockSignals(False)
        # Re-emit patternChanged if the selection actually changed,
        # so downstream preview / runner state catches up.
        new_sid = combo.currentData()
        if new_sid != prev_sid:
            try:
                self.patternChanged.emit(self.pattern())
            except Exception:
                pass

    # ----------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        return {
            "phase_count": self.phase_count.currentText(),
            "symmetry": self.symmetry.currentText(),
            "polarity": self.polarity.currentText(),
            "amp_excite": self.amp_excite.value(),
            "width_shared": self.width_shared.value(),
            "ratio": [sp.value() for sp in self.ratio_spins],
            "phase_amp": [sp.value() for sp in self.phase_amp],
            "phase_width": [sp.value() for sp in self.phase_width],
            "interphase_us": self.interphase_us.value(),
            "discharge_us": self.discharge_us.value(),
            # Rate is persisted as Hz so the unit-toggle choice is
            # orthogonal to it; the unit just controls how the user
            # *sees* the value, not what gets saved.
            "rate_hz": self._current_rate_hz(),
            "rate_unit": self._rate_unit,
            "charge_mode": self.charge_mode.currentText(),
            # Per-row delay toggles. The current convention is "ON =
            # has the delay" for all three (interphase / discharge /
            # interpulse). Saved keys mirror that convention. The
            # legacy "no_interphase" / "no_interpulse" inverted keys
            # are still recognised on load (see ``restore_prefs``) so
            # old prefs files round-trip cleanly.
            "interphase_on": self.interphase_check.isChecked(),
            "discharge_on": self.discharge_check.isChecked(),
            "interpulse_on": self.interpulse_check.isChecked(),
            "interpulse_discharge_on": self.interpulse_discharge_check.isChecked(),
            # Arbitrary-pattern state — survives restart so a hand-built
            # waveform doesn't get wiped between sessions.
            "arb_mode": self.arb_mode.currentText(),
            "arb_n_rows": int(self.arb_n_rows.value()),
            "arb_period_us": float(self.arb_period_us.value()),
            "arb_table": self._dump_arb_table(),
            # Phase-shape state. ``shape`` (symmetric biphasic) and
            # ``asym_shape`` / ``cap_lock`` (asymmetric) are stored as
            # the userData strings so they survive label-text edits.
            # Pre-shape prefs files miss these keys; restore_prefs
            # falls back to the combo defaults in that case.
            "shape": self.shape_combo.currentData(),
            "bump_count": int(self.bump_count.value()),
            "asym_shape": self.asym_shape_combo.currentData(),
            "cap_lock": self.cap_lock_combo.currentData(),
            # τ-mode state for the cap-coupled solver. Pre-τ
            # prefs files miss these keys; ``restore_prefs``
            # falls back to the auto-mode default + 100 µs
            # spinbox value, matching what a fresh panel
            # would show.
            "cap_tau_mode": self.tau_mode_combo.currentData(),
            "cap_tau_us": float(self.tau_us.value()),
            # Mix-and-match per-phase shapes. Saved even when
            # mix_match isn't the active asym shape, so toggling
            # to and from mix_match preserves the user's choices.
            "mix_phase_shapes": [
                cb.currentData() for cb in self.mix_phase_shape_combo
            ],
            # Mix-and-match per-phase offset spinbox values. Same
            # round-trip rationale — kept even when not active.
            "mix_phase_offsets": [
                float(sp.value()) for sp in self.mix_phase_offset_ua
            ],
            # Symmetric-mode offset and τ spinbox values.
            "sym_offset_ua": float(self.sym_offset_ua.value()),
            "sym_offset_enable": bool(self.sym_offset_enable_chk.isChecked()),
            "sym_tau_us": float(self.sym_tau_us.value()),
            # Burst / pulse-train state (absent → OFF, so legacy prefs load as
            # non-burst).  ``_burst_available`` (the per-tab gate) is NOT
            # persisted — it's set by the embedding tab on construction.
            "burst_enabled": bool(self.burst_enable_check.isChecked()),
            "pulses_per_burst": int(self.pulses_per_burst_spin.value()),
            "burst_period_ms": float(self.burst_period_ms.value()),
        }

    def _dump_arb_table(self) -> List[List[str]]:
        """Snapshot the arbitrary-pattern table as a list of (amp[, dur]) lists."""
        n = self.arb_table.rowCount()
        cols = self.arb_table.columnCount()
        rows: List[List[str]] = []
        for r in range(n):
            row: List[str] = []
            for c in range(cols):
                it = self.arb_table.item(r, c)
                row.append(it.text() if it is not None else "")
            rows.append(row)
        return rows

    def restore_prefs(self, p: dict):
        if not p: return
        if "phase_count" in p: self.phase_count.setCurrentText(p["phase_count"])
        if "symmetry" in p: self.symmetry.setCurrentText(p["symmetry"])
        if "polarity" in p:
            # Legacy prefs carry the pre-rename wording ("Cathodic-first" /
            # "Anodic-first"); map those to the current "Cathodal / Anodal"
            # items — setCurrentText on a non-editable combo silently
            # NO-OPS for an unknown item, which would lose a saved
            # Anodal-first selection.
            _legacy_polarity = {"Cathodic-first": "Cathodal-first",
                                "Anodic-first": "Anodal-first"}
            _pol_text = str(p["polarity"])
            self.polarity.setCurrentText(
                _legacy_polarity.get(_pol_text, _pol_text))
        for key, sp in (("amp_excite", self.amp_excite),
                        ("width_shared", self.width_shared),
                        ("interphase_us", self.interphase_us),
                        ("discharge_us", self.discharge_us)):
            if key in p:
                _safe_set_spinbox_value(sp, p[key])
        # Rate / unit — accept both new (rate_hz + rate_unit) and the
        # legacy ``rate_pps`` key (which was always Hz under the hood).
        if "rate_unit" in p and p["rate_unit"] in self.RATE_UNIT_CHOICES:
            self.rate_unit_combo.setCurrentText(p["rate_unit"])
        rate_hz_val = None
        if "rate_hz" in p:
            try: rate_hz_val = float(p["rate_hz"])
            except (TypeError, ValueError): pass
        elif "rate_pps" in p:
            try: rate_hz_val = float(p["rate_pps"])
            except (TypeError, ValueError): pass
        if rate_hz_val is not None:
            try: self._set_rate_hz(rate_hz_val)
            except (TypeError, ValueError): pass
        if "ratio" in p and isinstance(p["ratio"], (list, tuple)):
            for sp, v in zip(self.ratio_spins, p["ratio"]):
                _safe_set_spinbox_value(sp, v)
        if "phase_amp" in p and isinstance(p["phase_amp"], (list, tuple)):
            for sp, v in zip(self.phase_amp, p["phase_amp"]):
                _safe_set_spinbox_value(sp, v)
        if "phase_width" in p and isinstance(p["phase_width"], (list, tuple)):
            for sp, v in zip(self.phase_width, p["phase_width"]):
                _safe_set_spinbox_value(sp, v)
        if "charge_mode" in p: self.charge_mode.setCurrentText(p["charge_mode"])
        # Delay-shortcut checkboxes. New keys ("interphase_on" /
        # "interpulse_on") are the inverse of the legacy keys
        # ("no_interphase" / "no_interpulse") — accept both so old
        # prefs files restore cleanly. Discharge has always used the
        # affirmative convention so its key is unchanged.
        if "interphase_on" in p:
            self.interphase_check.setChecked(bool(p["interphase_on"]))
        elif "no_interphase" in p:
            self.interphase_check.setChecked(not bool(p["no_interphase"]))
        if "discharge_on" in p:
            self.discharge_check.setChecked(bool(p["discharge_on"]))
        if "interpulse_on" in p:
            self.interpulse_check.setChecked(bool(p["interpulse_on"]))
        elif "no_interpulse" in p:
            self.interpulse_check.setChecked(not bool(p["no_interpulse"]))
        if "interpulse_discharge_on" in p:
            self.interpulse_discharge_check.setChecked(
                bool(p["interpulse_discharge_on"]))
        # Arbitrary-pattern state — apply mode first so the column count
        # is right when the table is repopulated.
        if "arb_mode" in p: self.arb_mode.setCurrentText(p["arb_mode"])
        if "arb_n_rows" in p:
            # Int spinbox — helper does float() then setValue;
            # QSpinBox.setValue accepts float and truncates.
            _safe_set_spinbox_value(self.arb_n_rows, p["arb_n_rows"])
        if "arb_period_us" in p:
            _safe_set_spinbox_value(self.arb_period_us,
                                    p["arb_period_us"])
        if isinstance(p.get("arb_table"), list):
            self._load_arb_table(p["arb_table"])
        # Phase shape selectors — match by userData so a label-text
        # change in code doesn't invalidate previously-saved prefs.
        # Each combo silently keeps its current selection if the saved
        # key isn't found; that handles old prefs files cleanly.
        def _select_by_data(combo: QtWidgets.QComboBox, val):
            if val is None: return
            for i in range(combo.count()):
                if combo.itemData(i) == val:
                    combo.setCurrentIndex(i); return
        # Auto-migrate prefs from before five asymmetric-dropdown
        # entries moved to symmetric mode. Each maps to a specific
        # ``shape`` value in the SYMMETRIC dropdown:
        #   * linear_inc_dec / linear_dec_inc → same id (pair shapes).
        #   * linear_inc_inc (Doğan RampUp)  → SHAPE_LINEAR_INCREASING.
        #   * linear_dec_dec (Doğan RampDown)→ SHAPE_LINEAR_DECREASING.
        #   * exp_biphasic   (Yip mirror)    → exp_dec_inc pair.
        # In every case: force SYMMETRIC and route the symmetric
        # ``shape`` slot. The asym_shape_combo's saved value isn't in
        # the dropdown anymore so it falls back to rectangular —
        # harmless since the symmetric path now owns the rendering.
        _ASYM_TO_SYM_SHAPE = {
            "linear_inc_dec": "linear_inc_dec",
            "linear_dec_inc": "linear_dec_inc",
            "linear_inc_inc": SHAPE_LINEAR_INCREASING,
            "linear_dec_dec": SHAPE_LINEAR_DECREASING,
            "exp_biphasic":   "exp_dec_inc",
        }
        old_asym = p.get("asym_shape")
        if old_asym in _ASYM_TO_SYM_SHAPE:
            self.symmetry.setCurrentText(SYMMETRIC)
            # Override the ``shape`` slot with the migrated value so
            # the _select_by_data call below picks it up. Don't
            # clobber an explicitly-saved shape if there is one.
            if "shape" not in p or p.get("shape") in (None, ""):
                p = {**p, "shape": _ASYM_TO_SYM_SHAPE[old_asym]}
        _select_by_data(self.shape_combo, p.get("shape"))
        _select_by_data(self.asym_shape_combo, p.get("asym_shape"))
        _select_by_data(self.cap_lock_combo, p.get("cap_lock"))
        # Mix-and-match per-phase shapes — pull from the ``mix_phase_shapes``
        # list (one entry per phase). Older prefs without this key keep
        # the panel's defaults (both phases rectangular).
        mix_shapes = p.get("mix_phase_shapes")
        if isinstance(mix_shapes, list):
            for i, sid in enumerate(mix_shapes):
                if i < len(self.mix_phase_shape_combo):
                    _select_by_data(self.mix_phase_shape_combo[i], sid)
        # Mix-and-match per-phase offsets + symmetric-mode offset
        # and τ. Older prefs files without these keys keep the
        # panel defaults (all zeros → no offset, canonical τ).
        mix_offsets = p.get("mix_phase_offsets")
        if isinstance(mix_offsets, list):
            for i, val in enumerate(mix_offsets):
                if i < len(self.mix_phase_offset_ua):
                    try:
                        self.mix_phase_offset_ua[i].setValue(float(val))
                    except (TypeError, ValueError):
                        pass
        if "sym_offset_ua" in p:
            try:
                self.sym_offset_ua.setValue(float(p["sym_offset_ua"]))
            except (TypeError, ValueError):
                pass
        # Offset-enable checkbox (absent in pre-checkbox prefs → default OFF).
        try:
            _en = bool(p.get("sym_offset_enable", False))
            self.sym_offset_enable_chk.setChecked(_en)
            self.sym_offset_ua.setEnabled(_en)
        except (TypeError, ValueError, AttributeError):
            pass
        if "sym_tau_us" in p:
            try:
                self.sym_tau_us.setValue(float(p["sym_tau_us"]))
            except (TypeError, ValueError):
                pass
        # τ state — load BEFORE ``_on_mode_changed`` fires so the
        # spinbox's enabled-state reflects the restored mode on
        # the very first emit (rather than briefly enabling the
        # spinbox then snapping back). Old prefs files without
        # these keys keep the panel's defaults.
        _select_by_data(self.tau_mode_combo, p.get("cap_tau_mode"))
        if "cap_tau_us" in p:
            _safe_set_spinbox_value(self.tau_us, p["cap_tau_us"])
        if "bump_count" in p:
            # bump_count is an int spinbox — wrap via float() in
            # the helper, the spinbox truncates if needed.
            _safe_set_spinbox_value(self.bump_count, p["bump_count"])
        # ``charge_per_phase`` / ``charge_lock`` keys from older prefs
        # files are silently ignored — the Q_ph lock UI now lives in
        # the VT tab and persists there. Pre-existing pulse parameters
        # (amp, width) still load above.
        # Burst / pulse-train — restore the value(s) first, then the enable
        # toggle (signals blocked so a mid-session restore doesn't spam
        # patternCommitted), then sync the enabled-state + readout explicitly.
        # Absent keys (legacy prefs) → burst stays OFF.
        if "pulses_per_burst" in p:
            self.pulses_per_burst_spin.blockSignals(True)
            try:
                self.pulses_per_burst_spin.setValue(int(p["pulses_per_burst"]))
            except (TypeError, ValueError):
                pass
            finally:
                self.pulses_per_burst_spin.blockSignals(False)
        if "burst_period_ms" in p:
            self.burst_period_ms.blockSignals(True)
            try:
                self.burst_period_ms.setValue(float(p["burst_period_ms"]))
            except (TypeError, ValueError):
                pass
            finally:
                self.burst_period_ms.blockSignals(False)
        if "burst_enabled" in p:
            self.burst_enable_check.blockSignals(True)
            try:
                self.burst_enable_check.setChecked(bool(p["burst_enabled"]))
            except Exception:
                pass
            finally:
                self.burst_enable_check.blockSignals(False)
        self._on_burst_enable_toggled(self.burst_enable_check.isChecked())
        self._on_mode_changed()
        self._on_balance_changed()
        # After restoring the amplitude + polarity, force the excitation
        # amplitude's DISPLAYED sign to match the restored polarity — a legacy
        # prefs file may have saved an unsigned/mismatched value.
        self._sync_excite_amp_sign()

    def _load_arb_table(self, rows):
        """Re-populate the arbitrary table from a list-of-lists snapshot."""
        self.arb_table.blockSignals(True)
        try:
            n = min(len(rows), self.arb_n_rows.maximum())
            self.arb_table.setRowCount(max(n, self.arb_table.rowCount()))
            for r, row in enumerate(rows[:n]):
                for c, val in enumerate(row[: self.arb_table.columnCount()]):
                    self.arb_table.setItem(
                        r, c, QtWidgets.QTableWidgetItem(str(val) if val else ""))
        finally:
            self.arb_table.blockSignals(False)
