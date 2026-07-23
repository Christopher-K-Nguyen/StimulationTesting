"""Tabbed scope view — one sub-tab per channel + global waveform toggles.

Replaces the single :class:`stimtest.gui.widgets.ScopePlot` in each
experiment tab. Layout per sub-tab::

    ┌────────────────────────────────────────────────┐
    │ ☑V_mon  ☑I_mon  ☑E_act  ☑E_ret  [colour key]   │   global toggles
    ├────────────────────────────────────────────────┤
    │                                                │
    │           ScopePlot (pyqtgraph)                │   per-channel
    │                                                │
    └────────────────────────────────────────────────┘

The wrapping ``QTabWidget`` shows a tab for every channel that has
either been selected as active, has captures recorded, or has been
explicitly registered via :meth:`ensure_tab`. Tabs that finish a run
get a small green ✓ in their title so the user can see at a glance
which channels are done.

Per-capture metrics are shown in the experiment tab's *right-hand*
:class:`stimtest.gui.widgets.MetricTable` panel (``metrics_side``);
the page itself only carries the scope plot — a previous inline
metric table below the plot was redundant with the side panel and
has been removed.

Visibility checkboxes are global to the pane — toggling V_mon hides
the V_mon trace in every sub-tab. State is exposed via :meth:`prefs`
/ :meth:`restore_prefs` so the experiment tabs can persist it.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from ..session import Capture
from . import rich
from .widgets import AXIS_LEFT, AXIS_NA, AXIS_RIGHT, ScopePlot


# Trace identifiers — keep the strings stable; they're used as plot keys
# AND as prefs JSON keys.
TRACE_VMON = "V_mon"
TRACE_IMON = "I_mon"
TRACE_EACT = "E_act"
TRACE_ERET = "E_ret"
ALL_TRACES = (TRACE_VMON, TRACE_IMON, TRACE_EACT, TRACE_ERET)

# Ghazavi & Cogan 2018 access-resistance-CORRECTED interface waveforms —
# shown ONLY for continuous-sinusoidal (KHFAC) captures (operator: "Plot the
# corrected waveforms like how Ghazavi did … I want Ei be E'act and E'ret for
# correcting for access resistance").  E′act = active_trace − R_a·I_ac (the
# active interface), E′ret = E_ret − R_ret·I_ac (the return interface).  The
# key carries an apostrophe on the variable (``E'_act`` → ``<i>E'</i><sub>act
# </sub>``); drawn DASHED in its base electrode's colour to read as the
# de-ohm'd companion of the measured trace.
TRACE_EACT_CORR = "E'_act"
TRACE_ERET_CORR = "E'_ret"
ALL_CORRECTED_TRACES = (TRACE_EACT_CORR, TRACE_ERET_CORR)

# Chronopotentiometry derivative traces (Harris 2019 — capacitive/Faradaic
# analysis; operator: "Have that derivative and reciprocal of derivative as
# option traces").  dE/dt (constant = capacitive, dip → 0 = Faradaic) and its
# reciprocal 1/(dE/dt) (Faradaic = peak), computed from the active trace.  Their
# native scale (V/µs, and the huge 1/(dE/dt)) doesn't fit the V or I axes, so
# they are drawn as a NORMALIZED OVERLAY (scaled to the visible voltage range —
# shape only, dashed) when toggled on (operator chose "normalized overlay").
TRACE_DEDT = "dV/dt"
TRACE_RECIP_DEDT = "1/(dV/dt)"
# The RECIPROCAL 1/(dV/dt) overlay was REMOVED from the LIVE experiment plot
# (operator: "Remove the reciprocal trace option in the experiment plot").  It
# stays available in POLARIS (plotting.plot_capture deriv_overlays + the RDC
# view).  ``TRACE_RECIP_DEDT`` is kept defined so the _refresh_traces guard +
# the colour map don't KeyError; it's just no longer in the toggle/inset set.
ALL_DERIV_TRACES = (TRACE_DEDT,)


def _subscript_trace_name(trace: str) -> str:
    """``"V_mon"`` -> ``"<i>V</i><sub>mon</sub>"`` for HTML-rendering
    surfaces (the pyqtgraph legend's LabelItem).  Subscripting the
    monitor / electrode names makes the legend match the subscripted
    markers + title instead of showing a bare underscore (operator: "If
    V_mon, I_mon, E_ret, and E_act are not going to be with subscripts,
    then remove the underscore").  Names without an underscore pass
    through unchanged."""
    # Differential: the operator ``d`` is UPRIGHT, the variable ITALIC
    # (operator: "for the differential, keep the variable in italics, but keep
    # the 'd' in normal font") — d<i>V</i>/d<i>t</i>, not <i>dV/dt</i>.
    if trace == TRACE_DEDT:
        return "d<i>V</i>/d<i>t</i>"
    if trace == TRACE_RECIP_DEDT:
        return "1/(d<i>V</i>/d<i>t</i>)"
    if "_" in trace:
        base, _, sub = trace.partition("_")
        return rich.var(base, sub)
    return trace


class _RichTextItemDelegate(QtWidgets.QStyledItemDelegate):
    """Paint each combo item's text as HTML (so trace names render in
    variable format — ``V`` italic + ``mon`` subscript — instead of the raw
    ``<i>V</i><sub>mon</sub>`` markup)."""

    def paint(self, painter, option, index):
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        html = opt.text
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget else QtWidgets.QApplication.style()
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        # Theme-aware text colour — a bare QTextDocument defaults to BLACK,
        # which is invisible on a dark popup background.  Use the item
        # palette's (Highlighted)Text so it reads on any theme.
        selected = bool(opt.state & QtWidgets.QStyle.StateFlag.State_Selected)
        color = opt.palette.color(
            QtGui.QPalette.ColorRole.HighlightedText if selected
            else QtGui.QPalette.ColorRole.Text)
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setHtml(f'<span style="color:{color.name()}">{html}</span>')
        rect = style.subElementRect(
            QtWidgets.QStyle.SubElement.SE_ItemViewItemText, opt, widget)
        painter.save()
        painter.translate(rect.topLeft())
        painter.translate(
            0.0, max(0.0, (rect.height() - doc.size().height()) / 2.0))
        doc.drawContents(painter, QtCore.QRectF(
            0, 0, rect.width(), rect.height()))
        painter.restore()

    def sizeHint(self, option, index):
        # A COMPACT per-row size from the text only.  Do NOT floor the height
        # at ``option.rect.height()`` — during the popup's sizing pass that
        # rect can be the whole viewport, which made every row gigantic and
        # blew the dropdown up to full-screen height (operator screenshot).
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(opt.font)
        doc.setHtml(opt.text)
        return QtCore.QSize(int(doc.idealWidth()) + 12,
                            int(doc.size().height()) + 6)


class _RichComboBox(QtWidgets.QComboBox):
    """A non-editable combo whose items + closed display render as HTML, so
    trace names appear in variable format (operator: "Change the inset
    dropdown list to show variables instead of plain text")."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setItemDelegate(_RichTextItemDelegate(self))

    def paintEvent(self, _ev):
        painter = QtWidgets.QStylePainter(self)
        opt = QtWidgets.QStyleOptionComboBox()
        self.initStyleOption(opt)
        html = opt.currentText
        opt.currentText = ""
        # Frame + arrow WITHOUT the plain text …
        painter.drawComplexControl(
            QtWidgets.QStyle.ComplexControl.CC_ComboBox, opt)
        # … then the current item's HTML into the field rect.
        rect = self.style().subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_ComboBox, opt,
            QtWidgets.QStyle.SubControl.SC_ComboBoxEditField, self)
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(self.font())
        color = self.palette().color(
            QtGui.QPalette.ColorRole.ButtonText
            if self.isEnabled() else QtGui.QPalette.ColorRole.Text)
        doc.setHtml(f'<span style="color:{color.name()}">{html}</span>')
        painter.save()
        painter.translate(rect.topLeft())
        painter.translate(
            2.0, max(0.0, (rect.height() - doc.size().height()) / 2.0))
        doc.drawContents(painter, QtCore.QRectF(
            0, 0, rect.width(), rect.height()))
        painter.restore()
# Trace colour palette — Wong's colourblind-safe set
# (https://www.nature.com/articles/nmeth.1618) so red and green stay
# distinguishable under deuteranopia / protanopia.  Mapping:
#   * Voltage (V_mon)            → golden yellow
#   * Current / density (I_mon)  → teal-cyan
#   * Active Potential (E_act)   → bluish-green
#   * Return Potential (E_ret)   → vermillion (orange-red)
# The active / return assignment is "green = active electrode, red =
# return electrode" — green reads as the "live" colour to most
# operators, while red marks the path the cathodic current returns
# along.  Operators learn the mapping once and it carries across
# every plot (POLARIS viewer, experiment scope, calibration plot).
TRACE_COLOURS = {
    TRACE_VMON: "#E6B800",   # Voltage          — golden yellow
    TRACE_IMON: "#00B4C8",   # Current/density  — teal-cyan
    TRACE_EACT: "#009E73",   # Active Potential — bluish-green
    TRACE_ERET: "#D55E00",   # Return Potential — vermillion (red)
    # Corrected (interface) waveforms share their base electrode's hue —
    # dashed line disambiguates measured-vs-corrected (see set_traces styles).
    TRACE_EACT_CORR: "#009E73",   # E′act — active interface (bluish-green)
    TRACE_ERET_CORR: "#D55E00",   # E′ret — return interface (vermillion)
    # Derivative overlays — distinct from the trace-palette hues (purple arc).
    TRACE_DEDT: "#7E2F8E",        # dV/dt          — purple
    TRACE_RECIP_DEDT: "#CC79A7",  # 1/(dV/dt)      — reddish-purple
}
# Default per-trace Y-axis assignment. I_mon goes to the right axis so
# its µA range doesn't compress the V/E traces on the left axis;
# everything else is voltage and shares the left axis.
DEFAULT_TRACE_AXIS = {
    TRACE_VMON: AXIS_LEFT,
    TRACE_IMON: AXIS_RIGHT,
    TRACE_EACT: AXIS_LEFT,
    TRACE_ERET: AXIS_LEFT,
    # Corrected interface traces default to the left voltage axis (visible),
    # so they auto-show for a KHFAC capture; the user can re-assign / hide
    # them independently of E_act / E_ret via their own toggle-bar combo.
    TRACE_EACT_CORR: AXIS_LEFT,
    TRACE_ERET_CORR: AXIS_LEFT,
    # Derivative overlays default OFF (N/A) — the operator opts into them; when
    # on, they're normalized to the left (voltage) axis, not its true scale.
    TRACE_DEDT: AXIS_NA,
    TRACE_RECIP_DEDT: AXIS_NA,
}
# The full set of traces the toggle bar + inset picker offer (base + the
# Ghazavi corrected interface traces + the derivative overlays).  The corrected
# pair is only AVAILABLE for continuous-sinusoidal captures (see
# ``MultiChannelScope.set_corrected_shape``); the derivative pair is always
# available (any capture has a differentiable V_mon).
ALL_TOGGLE_TRACES = ALL_TRACES + ALL_CORRECTED_TRACES + ALL_DERIV_TRACES


class _ChannelPage(QtWidgets.QWidget):
    """One sub-tab: scope plot for a single channel-or-combination key.

    The ``key`` argument is the stable identifier used by
    :class:`MultiChannelScope` to route captures: ``int`` for legacy
    monopolar callers (rendered as ``"CH05"`` in the tab title) and
    ``str`` for combination labels (e.g. ``"CH05 v 06"`` for a
    bipolar pair). Two combinations sharing an active channel get
    distinct sub-tabs, which fixes the previous behaviour where they
    collided on a single CHnn tab.

    Per-capture metric numbers are rendered by the experiment tab's
    right-side :class:`stimtest.gui.widgets.MetricTable`
    (``metrics_side``) — the page itself carries only the scope plot
    now. A previous inline ``MetricTable`` below the plot duplicated
    the side panel and has been removed.
    """

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        # Back-compat alias — callers who reach in for ``page.channel``
        # still get the int when the key is one, otherwise the
        # full combination label.
        self.channel = key
        self.scope = ScopePlot()
        # Full per-key capture history. Previously this stored only
        # ``_latest`` and every new capture overwrote the prior one,
        # leaving the user no way to inspect mid-run captures after
        # the latest landed. Storing a list lets the navigation row
        # below page through every snapshot taken on this channel /
        # combo. ``_latest`` stays as a back-compat alias pointing at
        # the most-recent (== last in list) entry.
        self._captures: List[Capture] = []
        self._current_idx: int = -1

        # ----- capture-history nav row -----
        # A DROPDOWN of every capture taken on this channel / combo
        # (operator: "change it to a dropdown list").  Replaces the old
        # ◀ / ▶ arrows, which were broken — they called ``set_index``
        # without a visibility map, so the guard skipped ``_refresh_
        # traces`` and the waveform never changed (only the last capture
        # ever rendered).  Hidden when only one capture has landed.
        self._nav_combo = QtWidgets.QComboBox()
        self._nav_combo.setToolTip(
            "Pick which capture of this channel / combination to view.")
        # Wide enough for the full "Capture NN  ·  ±NNN µA  ·  kind  ·
        # latest" entry text (operator: "increase the width of the dropdown
        # list for viewing different capture numbers"); the popup also
        # sizes itself to the longest entry.
        self._nav_combo.setMinimumWidth(280)
        self._nav_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._nav_combo.currentIndexChanged.connect(
            self._on_nav_combo_changed)
        # Guard so a programmatic rebuild of the combo doesn't re-fire
        # ``set_index`` (and clobber the user's selection / auto-follow).
        self._nav_combo_updating = False
        self._nav_latest = QtWidgets.QToolButton()
        self._nav_latest.setText("Go to latest waveform")
        self._nav_latest.setToolTip(
            "Jump to the most-recent waveform — the latest capture of the "
            "latest channel/combo — and re-arm auto-follow so the view "
            "snaps to new captures as they arrive.")
        self._nav_latest.clicked.connect(self._nav_jump_latest)
        self._nav_kind = QtWidgets.QLabel("")
        self._nav_kind.setStyleSheet("color: #888; font-style: italic;")
        # Auto-follow flag — when True (default), incoming captures
        # snap the page to the newest one. The user picking an older
        # capture from the dropdown disables follow until they hit Latest.
        self._auto_follow: bool = True
        # Last render context (visibility / axis map / inset / area) from
        # the most recent ``set_capture`` / ``refresh_visibility`` — the
        # dropdown re-renders through this since it carries no map.
        self._last_ctx: Optional[dict] = None

        nav_row = QtWidgets.QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(4)
        nav_row.addWidget(QtWidgets.QLabel("Capture:"))
        nav_row.addWidget(self._nav_combo)
        nav_row.addWidget(self._nav_kind, stretch=1)
        nav_row.addWidget(self._nav_latest)
        self._nav_row_w = QtWidgets.QWidget()
        self._nav_row_w.setLayout(nav_row)
        self._nav_row_w.setVisible(False)

        # Plot title — single-line summary of the rendered capture.
        # Shows: channel / configuration · I_stim · current density
        # (when area is set) · Q_ph · Q_inj · capture #.  Carried as
        # its own QLabel rather than pyqtgraph's PlotItem title
        # because the pyqtgraph title row's visibility behaviour is
        # flaky across versions (same reason calibration uses a
        # separate label).  Default-empty; populated by ``set_capture``
        # / ``set_index`` each time a capture is rendered.
        self._title_label = QtWidgets.QLabel("")
        self._title_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._title_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._title_label.setStyleSheet(
            "QLabel { font-size: 13pt; font-weight: bold; padding: 2px; }")
        self._title_label.setVisible(False)

        # The inline metric table that used to sit below the scope
        # plot was redundant with the experiment tab's right-side
        # ``metrics_side`` panel and has been removed.  The page is
        # now just nav row + title + scope plot.  No vertical splitter
        # is needed (there is no second pane to size against), so the
        # scope simply takes all remaining vertical space via
        # ``stretch=1``.
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.addWidget(self._nav_row_w)
        v.addWidget(self._title_label)
        v.addWidget(self.scope, stretch=1)

    def _format_title(self, capture: Capture,
                      surface_area_um2: Optional[float] = None) -> str:
        """Build the single-line plot-title HTML for ``capture``.

        Combines:
          * Channel / configuration (from ``self.key`` — e.g. ``"CH05"``
            for legacy monopolar or ``"CH05 v 06"`` for bipolar)
          * I_stim (excitation-phase amplitude in µA)
          * Current density (A/cm²) when ``surface_area_um2 > 0``
          * Q_ph (charge per phase, nC) from ``capture.metrics``
          * Q_inj (charge injection capacity, mC/cm²) from ``capture.metrics``
          * Capture # (``capture.index`` + 1, for 1-based display)
        """
        parts = []
        # Channel / configuration label.
        if isinstance(self.key, int):
            parts.append(f"<b>CH{self.key:02d}</b>")
        else:
            parts.append(f"<b>{self.key}</b>")
        # I_stim — pull from the pattern's excitation phase, SIGNED
        # (operator: "Have Istim and Jstim in the heading above the
        # experiment plot reflect the polarity") — a cathodal-first
        # pulse reads −1000.0 µA here, matching the metrics table's
        # signed I_stim row.
        try:
            amp_ua = float(capture.pattern.excitation_phase.amplitude_ua)
        except Exception:
            amp_ua = None
        if amp_ua is not None:
            parts.append(
                f"<i>I</i><sub>stim</sub> = {amp_ua:.1f} µA")
            # Current density (A/cm²) requires a positive area.
            try:
                area_um2 = float(surface_area_um2) if surface_area_um2 else 0.0
            except (TypeError, ValueError):
                area_um2 = 0.0
            if area_um2 > 0:
                # J_stim = I_stim / A;  amp_ua × 1e-6 A / (area_um2 ×
                # 1e-8 cm²) = amp_ua / area_um2 × 100  A/cm².  Carries
                # I_stim's sign (same polarity convention).
                density = amp_ua / area_um2 * 100.0
                parts.append(
                    f"<i>J</i><sub>stim</sub> = {density:.2f} A/cm<sup>2</sup>")
        # Q_ph (nC) and Q_inj (mC/cm²) — from CaptureMetrics.
        try:
            m = capture.metrics
            q_ph = float(m.charge_per_phase_nc)
            q_inj = float(m.charge_injection_mc_per_cm2)
        except Exception:
            q_ph = q_inj = None
        import math
        if q_ph is not None and math.isfinite(q_ph):
            parts.append(
                f"<i>Q</i><sub>ph</sub> = {q_ph:.2f} nC")
        if q_inj is not None and math.isfinite(q_inj):
            # Auto-scale to µC/cm² when Q_inj would round to 0.000 mC — SHARED
            # with the exported subtitle (operator: "if the charge injection
            # capacity is 0 mC/cm2 … due to low precision, then use uC/cm2").
            from ..plotting import qinj_use_micro
            if qinj_use_micro(q_inj):
                parts.append(
                    f"<i>Q</i><sub>inj</sub> = {q_inj * 1e3:.3f} µC/cm<sup>2</sup>")
            else:
                parts.append(
                    f"<i>Q</i><sub>inj</sub> = {q_inj:.3f} mC/cm<sup>2</sup>")
        # Capture # — 1-based for the operator-facing display.
        try:
            cap_num = int(capture.index) + 1
            parts.append(f"capture #{cap_num}")
        except Exception:
            pass
        title = "  ·  ".join(parts)
        # When the capture is aborted / has no trace data, append an
        # explicit marker so the operator immediately sees WHY the
        # plot below is blank (most common cause: scope trigger
        # timeout, which leaves cap.v_mon_v with size = 0 and every
        # metric at NaN).  The detailed reason is logged separately
        # from the runner via an ``ExperimentEvent(kind="log")`` —
        # see ``_one_capture`` for the scope-capture-error branch.
        try:
            aborted = bool(getattr(capture.status, "aborted", False))
            no_data = (getattr(capture, "v_mon_v", None) is None
                       or capture.v_mon_v.size == 0)
            if aborted or no_data:
                note = (getattr(capture.status, "notes", "") or "").strip()
                tag = ("  ·  <span style='color:#d33;'>"
                       "<b>aborted</b></span>")
                if note:
                    tag += f" — {note}"
                title += tag
        except Exception:
            pass
        return title

    def _set_title_from(self, capture: Optional[Capture],
                        surface_area_um2: Optional[float] = None) -> None:
        """Update the plot-title text from ``capture`` (or hide on None)."""
        if capture is None:
            self._title_label.setVisible(False)
            self._title_label.setText("")
            return
        self._title_label.setText(self._format_title(capture, surface_area_um2))
        self._title_label.setVisible(True)

    # ---- back-compat shim: callers and tests reach for ``_latest`` ----
    @property
    def _latest(self) -> Optional[Capture]:
        """The most-recent capture in the history (last appended)."""
        if not self._captures:
            return None
        return self._captures[-1]

    def capture_count(self) -> int:
        """Number of captures currently held for this channel / combo."""
        return len(self._captures)

    def current_capture(self) -> Optional[Capture]:
        """The capture currently rendered into the scope + metrics
        table — may be older than the latest if the user navigated
        back via the prev / next buttons."""
        if 0 <= self._current_idx < len(self._captures):
            return self._captures[self._current_idx]
        return None

    def set_capture(self, capture: Capture, visible: Dict[str, bool],
                    axis_map: Optional[Dict[str, str]] = None,
                    inset_visible: bool = False,
                    inset_traces: Optional[set] = None,
                    surface_area_um2: Optional[float] = None,
                    title_area_um2: Optional[float] = None):
        """Append ``capture`` to the history and (when auto-following)
        render it. Use ``set_index`` to navigate to a different
        capture without appending.

        ``surface_area_um2`` (when > 0) switches the I_mon trace and
        right-axis label from raw current (µA) to current density
        (A/cm²).  ``None`` / 0 leaves the µA presentation in place.
        """
        self._captures.append(capture)
        if self._auto_follow or self._current_idx < 0:
            self._current_idx = len(self._captures) - 1
        # The TITLE's J_stim uses the RAW area (``title_area_um2``) so it shows
        # whenever a surface area is set — INDEPENDENT of the I_mon TRACE's
        # µA/density toggle, which drives ``surface_area_um2`` (gotcha #126).
        if title_area_um2 is None:
            title_area_um2 = surface_area_um2
        # Remember the render context so the dropdown can re-render an
        # older capture (it carries no visibility map of its own).
        self._last_ctx = dict(
            visible=visible, axis_map=axis_map,
            inset_visible=inset_visible, inset_traces=inset_traces,
            surface_area_um2=surface_area_um2, title_area_um2=title_area_um2)
        self._refresh_traces(visible, axis_map, inset_visible, inset_traces,
                             surface_area_um2=surface_area_um2)
        cap = self.current_capture()
        if cap is not None:
            # Per-capture metrics are rendered by the experiment tab's
            # right-side ``metrics_side`` panel (subscribed to the same
            # capture stream via the ``CaptureBus``) — the inline table
            # that used to live below the scope plot was removed.
            self._set_title_from(cap, surface_area_um2=title_area_um2)
        self._refresh_nav_row()

    def set_index(self, idx: int,
                  visible: Optional[Dict[str, bool]] = None,
                  axis_map: Optional[Dict[str, str]] = None,
                  inset_visible: bool = False,
                  inset_traces: Optional[set] = None,
                  surface_area_um2: Optional[float] = None,
                  title_area_um2: Optional[float] = None) -> bool:
        """Render the capture at ``idx`` (0-based). Returns True on
        success, False if the index is out of range. Disables
        auto-follow so subsequent ``set_capture`` calls don't snap
        the view back to latest — the user explicitly chose this
        capture and probably wants to stay on it."""
        if not (0 <= idx < len(self._captures)):
            return False
        self._current_idx = idx
        self._auto_follow = (idx == len(self._captures) - 1)
        # Re-render the selected capture.  The dropdown / Latest button
        # call this WITHOUT a visibility map, so fall back to the last
        # context we saw (the old arrows didn't do this — that's why
        # only the latest capture ever rendered).
        _area = surface_area_um2
        _title_area = title_area_um2
        if visible is None and self._last_ctx is not None:
            _ctx = self._last_ctx
            _area = _ctx.get("surface_area_um2")
            _title_area = _ctx.get("title_area_um2")
            self._refresh_traces(
                _ctx.get("visible"), _ctx.get("axis_map"),
                _ctx.get("inset_visible", False), _ctx.get("inset_traces"),
                surface_area_um2=_area)
        elif visible is not None:
            self._refresh_traces(visible, axis_map, inset_visible,
                                 inset_traces, surface_area_um2=surface_area_um2)
        cap = self.current_capture()
        if cap is not None:
            # Metric numbers come from the experiment tab's right-side
            # panel; this page only owns the scope plot now.  Title uses the
            # RAW area so J_stim shows regardless of the trace µA/density toggle.
            if _title_area is None:
                _title_area = _area
            self._set_title_from(cap, surface_area_um2=_title_area)
            # Tell the experiment tab to re-point the metrics table at the
            # capture we just navigated to (set_index is only ever called
            # by the per-capture nav on the VISIBLE page).
            _parent = getattr(self, "_scope_parent", None)
            if _parent is not None:
                try:
                    _parent.captureChanged.emit(cap)
                except Exception:
                    pass
        self._refresh_nav_row()
        return True

    def refresh_visibility(self, visible: Dict[str, bool],
                           axis_map: Optional[Dict[str, str]] = None,
                           inset_visible: bool = False,
                           inset_traces: Optional[set] = None,
                           surface_area_um2: Optional[float] = None,
                           title_area_um2: Optional[float] = None):
        cap = self.current_capture()
        if cap is not None:
            if title_area_um2 is None:
                title_area_um2 = surface_area_um2
            # Keep the render context current so a later dropdown pick
            # re-renders with the right visibility / axis map.
            self._last_ctx = dict(
                visible=visible, axis_map=axis_map,
                inset_visible=inset_visible, inset_traces=inset_traces,
                surface_area_um2=surface_area_um2, title_area_um2=title_area_um2)
            self._refresh_traces(visible, axis_map, inset_visible, inset_traces,
                                 surface_area_um2=surface_area_um2)
            # The title's J_stim uses the RAW area so it appears whenever an
            # area is set — the µA/density toggle only affects the I_mon trace.
            self._set_title_from(cap, surface_area_um2=title_area_um2)

    # ----- nav-row helpers -----
    def _on_nav_combo_changed(self, idx: int):
        """User picked a capture from the dropdown — render it."""
        if self._nav_combo_updating:
            return
        if 0 <= idx < len(self._captures):
            self.set_index(idx)   # re-renders via the stored context

    def _nav_jump_latest(self):
        """Jump to the latest WAVEFORM — the most-recent capture of the
        most-recent channel/combo, not just of THIS page (operator:
        "I pressed 'Go to latest sample', but it did nothing" — they were
        pinned on an older combo, and the old page-local jump had nothing
        newer on its own page).  Re-arms BOTH follow levels: the entry
        list snaps back to the live channel/combo AND that page snaps to
        its newest capture."""
        # Re-arm this page's follow regardless of where we jump.
        self._auto_follow = True
        parent = getattr(self, "_scope_parent", None)
        if parent is not None:
            try:
                parent._jump_latest_entry()      # → latest channel/combo page
                lk = getattr(parent, "_latest_key", None)
                page = parent._pages.get(lk) if lk is not None else None
                if page is not None and page._captures:
                    page.set_index(len(page._captures) - 1)
                    page._auto_follow = True
                    return
            except Exception:
                pass
        # Standalone / no parent: page-local latest.
        if self._captures:
            self.set_index(len(self._captures) - 1)

    def _capture_combo_label(self, i: int, c) -> str:
        """One dropdown entry — ``Capture N  ·  ±amp µA  ·  kind``."""
        parts = [f"Capture {i + 1}"]
        try:
            amp = float(c.pattern.excitation_phase.amplitude_ua)
            parts.append(f"{amp:+.1f} µA")   # always one decimal (operator)
        except Exception:
            pass
        kind = getattr(c, "kind", "") or ""
        if kind:
            parts.append(str(kind))
        if i == len(self._captures) - 1:
            parts.append("latest")
        return "  ·  ".join(parts)

    def _refresh_nav_row(self):
        """Rebuild the capture dropdown + the optional kind tag. Hide the
        whole row when ≤ 1 captures (nothing to pick between)."""
        n = len(self._captures)
        if n <= 1:
            self._nav_row_w.setVisible(False)
            return
        self._nav_row_w.setVisible(True)
        idx = max(0, min(self._current_idx, n - 1))
        # Rebuild the combo to match the capture list, guarded so the
        # programmatic update doesn't re-enter ``set_index``.
        self._nav_combo_updating = True
        try:
            self._nav_combo.clear()
            for i, c in enumerate(self._captures):
                self._nav_combo.addItem(self._capture_combo_label(i, c))
            self._nav_combo.setCurrentIndex(idx)
        finally:
            self._nav_combo_updating = False
        cap = self.current_capture()
        # Optional capture-kind tag — set by the experiment runner
        # via ``Capture.kind`` (e.g. 'pre_char', 'post_char',
        # 'snapshot'). Renders as italic grey when present so the
        # user can tell at a glance what category they're viewing.
        kind = getattr(cap, "kind", "") if cap is not None else ""
        self._nav_kind.setText(f"— {kind}" if kind else "")

    def _trace_label(self, trace: str) -> str:
        """Legend / curve-key label for ``trace``.

        Previously this baked the unit into the legend
        (``"I_mon (µA)"``) but the unit is already shown on the
        second line of the axis label, and baking it in here meant
        the legend lied when I_mon was rescaled to A/cm² (the
        axis correctly read ``Current density / A/cm²`` while
        the legend still said ``I_mon (µA)``).  Returning the bare
        trace name keeps the legend honest in both modes and makes
        the curve key stable across the µA ↔ A/cm² swap so
        ScopePlot reuses the existing PlotDataItem rather than
        recreating it.

        The monitor / electrode names are SUBSCRIPTED to match the
        subscripted markers + title (operator: "If V_mon, I_mon, E_ret,
        and E_act are not going to be with subscripts, then remove the
        underscore" — we subscript them).  ``V_mon`` →
        ``<i>V</i><sub>mon</sub>``; pyqtgraph's LegendItem (a LabelItem)
        renders the HTML.  The HTML string is still a STABLE curve key.
        """
        return _subscript_trace_name(trace)

    def _refresh_traces(self, visible: Dict[str, bool],
                        axis_map: Optional[Dict[str, str]] = None,
                        inset_visible: bool = False,
                        inset_traces: Optional[set] = None,
                        surface_area_um2: Optional[float] = None):
        cap = self.current_capture()
        if cap is None: return
        # A TRIMMED snapshot (LP drops the raw arrays after metrics are
        # extracted, gotcha #11) has no waveform to draw — ``time_us`` is
        # None.  Leave the previous frame on screen and return; the
        # metrics table + Tracking plot are fed elsewhere and don't need
        # the arrays.  (Without this, the render crashed on ``None.size``
        # / ``set_traces(None, …)`` and — since ``_on_capture`` renders
        # BEFORE feeding the Tracking plot — starved LP's tracking curves.)
        if getattr(cap, "time_us", None) is None:
            return
        axis_map = axis_map or DEFAULT_TRACE_AXIS
        traces: Dict[str, np.ndarray] = {}
        axis: Dict[str, str] = {}
        # A trace's data is computed when it's shown in the MAIN plot OR
        # selected for the INSET — so the inset can draw a trace that's HIDDEN
        # from the main plot (operator: "The inset trace should not have to also
        # be in the main plot as well").  ``_want`` gates the (sometimes
        # expensive) array build; ``_axis_for`` routes a visible trace to its
        # real axis and an inset-only (hidden) trace to AXIS_NA — which
        # ``ScopePlot.set_traces`` STORES for the inset but does NOT draw.
        _inset_roles = set(inset_traces or ())

        def _want(_role: str) -> bool:
            return bool(visible.get(_role, True)) or _role in _inset_roles

        def _axis_for(_role: str, _default: str) -> str:
            return (axis_map.get(_role, _default)
                    if visible.get(_role, True) else AXIS_NA)
        # Decide once whether to present I_mon as raw current (µA) or
        # current density (A/cm²).  Conversion needs a strictly-
        # positive area; anything else falls back to µA.
        try:
            _area_um2 = float(surface_area_um2) if surface_area_um2 else 0.0
        except (TypeError, ValueError):
            _area_um2 = 0.0
        _use_density = _area_um2 > 0.0
        # ALL traces are shown RAW — operator spec: "There should be no
        # subtraction in V_mon and I_mon.  You should be able to convert
        # the int8 data to double without any other processing, besides
        # the optional moving average."  The raw int8→double conversion
        # (Tek formula in _read_channel) already yields the true voltage
        # — the per-channel preamble-cache invalidation fix removed the
        # stale-YOFf acquisition offset, and the leading-edge work
        # removed the cathodic-contaminated display baseline — so V_mon /
        # I_mon idle at ~0 on their own and need NO baseline subtraction.
        # The optional moving average is applied upstream in the runner
        # (``_smooth_acquisition``), so ``cap.*`` already carries it when
        # enabled.  Do NOT reintroduce per_capture_baseline here.
        # NONE-guard every raw array: a TRIMMED snapshot (LP drops V_mon /
        # I_mon / E_act / E_ret to save memory over a multi-hour run,
        # gotcha #11) has ``cap.v_mon_v is None``.  Without the guard the
        # scope render raised AttributeError on ``None.size`` — and because
        # ``_on_capture`` renders the scope BEFORE feeding the Tracking
        # plot, that swallowed exception starved LP's tracking curves (they
        # never got fed).  A trimmed snapshot simply draws no waveform here;
        # its metrics still flow to the metric table + Tracking plot.
        if (_want(TRACE_VMON) and cap.v_mon_v is not None
                and cap.v_mon_v.size):
            k = self._trace_label(TRACE_VMON)
            traces[k] = np.asarray(cap.v_mon_v, dtype=float)
            axis[k] = _axis_for(TRACE_VMON, AXIS_LEFT)
        if _want(TRACE_IMON) and cap.i_mon_ua is not None and cap.i_mon_ua.size:
            k = self._trace_label(TRACE_IMON)
            _i = np.asarray(cap.i_mon_ua, dtype=float)
            if _use_density:
                # A/cm² = (i_mon_ua × 1e-6 A/µA) / (area_um2 × 1e-8 cm²/µm²)
                #       = i_mon_ua × 100 / area_um2
                traces[k] = _i * (100.0 / _area_um2)
            else:
                # ``i_mon_ua`` is already in microamps — no conversion.
                traces[k] = _i
            axis[k] = _axis_for(TRACE_IMON, AXIS_RIGHT)
        # E_ret — RAW (its non-zero electrode rest potential is real
        # information, not an offset to scrub).  Compute the array whenever it's
        # AVAILABLE (E_act derivation + the corrected traces below reuse
        # ``_e_ret_arr``), even if E_ret itself is neither shown nor inset.
        _e_ret_arr = None
        if cap.e_ret_v is not None and cap.e_ret_v.size:
            _e_ret_arr = np.asarray(cap.e_ret_v, dtype=float)
        if _want(TRACE_ERET) and _e_ret_arr is not None:
            k = self._trace_label(TRACE_ERET)
            traces[k] = _e_ret_arr
            axis[k] = _axis_for(TRACE_ERET, AXIS_LEFT)
        # E_act — prefer the recorded trace when present, otherwise
        # derive it from V_mon + E_ret using the differential identity
        # ``V_mon = E_act − E_ret``  →  ``E_act = V_mon + E_ret``.  This
        # gives the operator an active-electrode trace whenever the
        # instrumentation amp is wired to one electrode but not both,
        # without needing a third channel on the scope.
        if _want(TRACE_EACT):
            k = self._trace_label(TRACE_EACT)
            if cap.e_act_v is not None and cap.e_act_v.size:
                # Recorded E_act — RAW.
                traces[k] = np.asarray(cap.e_act_v, dtype=float)
                axis[k] = _axis_for(TRACE_EACT, AXIS_LEFT)
            elif (_e_ret_arr is not None
                  and cap.v_mon_v is not None
                  and cap.v_mon_v.size == _e_ret_arr.size):
                # Derived E_act = RAW V_mon + RAW E_ret — the pure
                # differential identity ``E_act = V_mon + E_ret``.  No
                # subtraction, consistent with every other trace.
                traces[k] = (np.asarray(cap.v_mon_v, dtype=float)
                             + _e_ret_arr)
                axis[k] = _axis_for(TRACE_EACT, AXIS_LEFT)
        # Ghazavi ACCESS-RESISTANCE-CORRECTED interface waveforms — ONLY for a
        # continuous-sinusoidal (KHFAC) capture (operator: "Plot the corrected
        # waveforms like how Ghazavi did … Ei be E'act and E'ret for correcting
        # for access resistance").  E′act / E′ret are the measured active /
        # return voltage with the resistive (in-phase-with-I) iR drop removed;
        # they're the interface potential the water-window limit really cares
        # about.  Drawn DASHED in the base electrode's colour.
        # E′act / E′ret are FIRST-CLASS toggleable traces + inset options — each
        # has its OWN axis-map entry (independent of E_act / E_ret), so drawing
        # is gated on ``axis_map[key] != AXIS_NA`` (its own toggle), NOT the base
        # trace's visibility (operator: "Eact and E'act should be separate").
        _corr_styles: Dict[str, str] = {}
        try:
            from ..metrics import (is_continuous_sinusoidal,
                                   ghazavi_corrected_waveforms)
            _is_sinus = (getattr(cap, "pattern", None) is not None
                         and is_continuous_sinusoidal(cap.pattern))
        except Exception:
            _is_sinus = False
        # Tell the parent whether the corrected pair should be OFFERED (rows +
        # inset options) for this capture's shape.  Idempotent + UI-only, so no
        # repaint recursion (the drawing below is independent of availability).
        _parent = getattr(self, "_scope_parent", None)
        if _parent is not None and hasattr(_parent, "set_corrected_shape"):
            try:
                _parent.set_corrected_shape(_is_sinus)
            except Exception:
                pass
        if (_is_sinus and cap.i_mon_ua is not None and cap.i_mon_ua.size):
            _i_raw = np.asarray(cap.i_mon_ua, dtype=float)
            _n_i = _i_raw.size
            # --- E′act: from the ACTIVE trace (recorded E_act, else derived
            #     V_mon+E_ret, else V_mon as the active-vs-return proxy) ---
            _active = None
            if cap.e_act_v is not None and cap.e_act_v.size == _n_i:
                _active = np.asarray(cap.e_act_v, dtype=float)
            elif (_e_ret_arr is not None and _e_ret_arr.size == _n_i
                  and cap.v_mon_v is not None and cap.v_mon_v.size == _n_i):
                _active = np.asarray(cap.v_mon_v, dtype=float) + _e_ret_arr
            elif cap.v_mon_v is not None and cap.v_mon_v.size == _n_i:
                _active = np.asarray(cap.v_mon_v, dtype=float)
            _act_axis = axis_map.get(TRACE_EACT_CORR, AXIS_LEFT)
            if _active is not None and (_act_axis != AXIS_NA
                                        or TRACE_EACT_CORR in _inset_roles):
                _ei, _vr, _r = ghazavi_corrected_waveforms(_active, _i_raw)
                if _ei is not None:
                    k = self._trace_label(TRACE_EACT_CORR)
                    traces[k] = _ei
                    axis[k] = _act_axis      # AXIS_NA (inset-only) ⇒ not drawn
                    _corr_styles[k] = "dash"
            # --- E′ret: from the RETURN trace (recorded E_ret only) ---
            _ret_axis = axis_map.get(TRACE_ERET_CORR, AXIS_LEFT)
            if (_e_ret_arr is not None and _e_ret_arr.size == _n_i
                    and (_ret_axis != AXIS_NA
                         or TRACE_ERET_CORR in _inset_roles)):
                _ei_r, _vr_r, _r_r = ghazavi_corrected_waveforms(
                    _e_ret_arr, _i_raw)
                if _ei_r is not None:
                    k = self._trace_label(TRACE_ERET_CORR)
                    traces[k] = _ei_r
                    axis[k] = _ret_axis
                    _corr_styles[k] = "dash"
        # --- Derivative overlays (Harris 2019): dV/dt + 1/(dV/dt) from the
        # active trace (V_mon preferred, else E_act), drawn as a NORMALIZED
        # overlay — scaled to the V_mon amplitude, centred at 0 (SHAPE ONLY;
        # their V/µs and huge-reciprocal scales don't fit the V or I axes).
        # Operator: "Have that derivative and reciprocal of derivative as option
        # traces … normalized overlay."  Only computed when toggled on.
        _dedt_axis = axis_map.get(TRACE_DEDT, AXIS_NA)
        _recip_axis = axis_map.get(TRACE_RECIP_DEDT, AXIS_NA)
        if (_dedt_axis != AXIS_NA or _recip_axis != AXIS_NA
                or TRACE_DEDT in _inset_roles
                or TRACE_RECIP_DEDT in _inset_roles):
            _vref = None
            if cap.v_mon_v is not None and cap.v_mon_v.size == cap.time_us.size:
                _vref = np.asarray(cap.v_mon_v, dtype=float)
            elif cap.e_act_v is not None and cap.e_act_v.size == cap.time_us.size:
                _vref = np.asarray(cap.e_act_v, dtype=float)
            if _vref is not None:
                try:
                    from ..metrics import charge_transfer_dedt
                    _tt, _dedt = charge_transfer_dedt(
                        cap.time_us, _vref, cap.pattern, onset_us=0.0)
                except Exception:
                    _dedt = None
                if _dedt is not None:
                    _rmax = float(np.nanmax(np.abs(_vref - np.nanmedian(_vref)))) or 1.0

                    def _norm_overlay(d):
                        d = np.asarray(d, dtype=float)
                        dc = d - np.nanmedian(d)
                        dmx = float(np.nanmax(np.abs(dc)))
                        return dc / dmx * _rmax if dmx > 1e-30 else None
                    # SOLID lines (operator: "Make the traces for derivative
                    # and reciprocal solid lines, not dashed") — no style entry,
                    # so set_traces draws them solid.  (The corrected E′act/E′ret
                    # overlays STAY dashed via their own _corr_styles entries.)
                    if _dedt_axis != AXIS_NA or TRACE_DEDT in _inset_roles:
                        _o = _norm_overlay(_dedt)
                        if _o is not None:
                            k = self._trace_label(TRACE_DEDT)
                            traces[k] = _o; axis[k] = _dedt_axis
                    if (_recip_axis != AXIS_NA
                            or TRACE_RECIP_DEDT in _inset_roles):
                        _recip = 1.0 / np.where(np.abs(_dedt) < 1e-9,
                                                np.nan, _dedt)
                        _o = _norm_overlay(_recip)
                        if _o is not None:
                            k = self._trace_label(TRACE_RECIP_DEDT)
                            traces[k] = _o; axis[k] = _recip_axis
        # Cache the colour map on first build — it's constant for
        # the lifetime of this widget so rebuilding the dict on
        # every capture was pure overhead.  Includes the corrected
        # (E′act / E′ret) labels so their dashed curves are coloured.
        if not hasattr(self, "_trace_colours_cache"):
            self._trace_colours_cache = {
                self._trace_label(t): TRACE_COLOURS[t]
                for t in (ALL_TRACES + ALL_CORRECTED_TRACES + ALL_DERIV_TRACES)}
        # Declarative update — ``set_traces(remove_missing=True)``
        # both updates the curves we want and drops the curves we
        # don't, in a single pass.  Avoids the destroy-and-recreate
        # cost of ``clear() + set_traces(...)`` (~3-5 ms → <0.5 ms
        # per capture) by reusing the cached PlotDataItem objects
        # whenever the trace set hasn't actually changed.
        self.scope.set_traces(cap.time_us, traces,
                              colors=self._trace_colours_cache,
                              axis=axis,
                              styles=_corr_styles,
                              remove_missing=True)
        # (Metric cursors are drawn LAST — after align_y_zeros — so the
        # label-placement collision/edge logic sees the FINAL view range,
        # not this-or-last-frame's stale range.  See below.)
        # Right-axis label tracks the I_mon presentation chosen above.
        # Density mode bakes the µA equivalent of 1 A/cm² for this
        # specific electrode area straight into the label so the
        # operator can read the right-axis ticks in raw current
        # without mental arithmetic.
        #
        # Conversion: 1 A/cm² × area (cm²) × 1e6 µA/A
        #           = 1 × (area_um2 × 1e-8) × 1e6
        #           = area_um2 × 0.01   µA
        if _use_density:
            ua_per_density = _area_um2 * 0.01
            # Density->current conversion baked into the bracketed unit so
            # the operator can read the right-axis ticks as raw current too
            # (operator: "I want the current density scale in brackets,
            # e.g., [A/cm2 = 50 uA]").
            self.scope.set_axis_labels(
                right=f"Current Density [A/cm² = {ua_per_density:g} µA]")
        else:
            self.scope.set_axis_labels(right="Current [µA]")
        # Left-axis title: "Voltage [V]" normally, but "Potential vs
        # <ref> [V]" when only electrode potentials (E_act / E_ret, incl. their
        # corrected E′act / E′ret variants) are on the left axis — no raw V_mon
        # — and "Voltage vs <return> [V]" when only V_mon is on the axis
        # (operator: V_mon is a driving voltage; E_act / E_ret are potentials
        # vs the reference electrode).  This is AUTOMATIC (operator: "I do not
        # want those checkboxes.  It should be automatic") — always applied;
        # the SHARED ``plotting._voltage_axis_label`` helper is self-gating, so
        # a MIXED voltage+current (or V_mon+potential) axis stays plain
        # "Voltage [V]".  Derivative overlays (dV/dt) are normalized SHAPE-only
        # curves, not a volts quantity, so they don't count here.  Pushed
        # through every refresh so a trace / area / electrode-name change
        # updates it live, and kept as the single source of truth with the
        # export / POLARIS figures.
        _parent = getattr(self, "_scope_parent", None)
        # ON by default, but OFF (→ plain "Voltage [V]") when the device has no
        # electrodes — the Plexon Test Board has no reference / return electrode
        # to name (operator: "If the Test Board is connected, the unit for the
        # voltage channels can only be Voltage [V]").
        _ref_aware = bool(getattr(_parent, "_reference_aware_labels", True))
        _pot_on = _ref_aware
        _ret_on = _ref_aware
        _ref = (getattr(_parent, "_reference_label", "") if _parent else "") \
            or "Ag|AgCl"
        _ret = (getattr(_parent, "_return_label", "") if _parent else "") \
            or "Pt"
        _left_keys = {k for k, a in axis.items() if a == AXIS_LEFT}
        _left_waves: set = set()
        if self._trace_label(TRACE_VMON) in _left_keys:
            _left_waves.add("V_mon")
        if ({self._trace_label(TRACE_EACT),
             self._trace_label(TRACE_EACT_CORR)} & _left_keys):
            _left_waves.add("E_act")
        if ({self._trace_label(TRACE_ERET),
             self._trace_label(TRACE_ERET_CORR)} & _left_keys):
            _left_waves.add("E_ret")
        from ..plotting import _voltage_axis_label
        self.scope.set_axis_labels(
            left=_voltage_axis_label(_left_waves, potential_axis=_pot_on,
                                     reference_label=_ref, return_axis=_ret_on,
                                     return_label=_ret, brackets=True))
        # Align the 0 V tick with the 0 µA / 0 A·cm⁻² tick — port of
        # MATLAB getPlot.m, same routine the calibration plot uses.
        # Skipped silently if pyqtgraph is missing or the right axis
        # has no data this frame.
        self.scope.align_y_zeros()
        # ---- Metric cursors on the live plot (MATLAB getPlot style) ----
        # Drawn AFTER align_y_zeros so the label placement (which avoids
        # the trace, the screen edges, AND other labels by choosing a
        # side / corner per marker) sees the FINAL view range.  V_a / V_d
        # use a horizontal bar, electrode polarization a "+" (Emc/Ema);
        # all black (the glyph shape distinguishes the kind).
        try:
            from ..plotting import (compute_metric_markers, marker_label_html,
                                     MARKER_COLOURS)
            # Per-kind colourblind-safe colours (operator: "vary the color of
            # the markers and respective label, colorblind safe") — SHARED with
            # the export via ``MARKER_COLOURS``; each label inherits its glyph's
            # colour (``_color`` below).
            _style = {
                "access":        (MARKER_COLOURS["access"], "hbar"),
                # V_d glyph is a PLUS (operator: "Change the driving voltage
                # marker symbol as a plus instead of a horizontal bar") — the
                # reddish-purple colour still separates it from the purple
                # polarization plus.
                "driving":       (MARKER_COLOURS["driving"], "+"),
                "driving_other": (MARKER_COLOURS["driving_other"], "hbar"),
                "polar":         (MARKER_COLOURS["polar"], "vbar"),
                "interphase":    (MARKER_COLOURS["interphase"], "o"),
                "badclass":      (MARKER_COLOURS["badclass"], "x"),
            }
            _markers = []
            for _mk in compute_metric_markers(cap):
                _color, _sym = _style.get(
                    _mk["kind"], (MARKER_COLOURS["badclass"], "x"))
                _no_label = bool(_mk.get("no_label"))
                _markers.append((
                    _mk["label"], _mk["t_us"], _mk["y"], _mk["text"],
                    _color, _sym,
                    None if _no_label else marker_label_html(_mk),
                    not _no_label,                       # 8th: draw_label
                ))
            # The scope parks the legend clear of the waveform + these labels.
            self.scope.set_markers(_markers)
            # No E_pol guide lines (operator: "remove the x line and their
            # label") — clear any that a prior render drew.
            try:
                self.scope.set_epol_guides([])
            except Exception:
                pass
        except Exception:
            # Cursor decoration must never break the waveform render.
            pass
        # Inset state: pass through to the plot. The inset trace
        # selection comes in as raw role tags (V_mon / I_mon / ...);
        # translate to the unit-suffixed names that ScopePlot uses
        # internally.  NOT filtered by main-plot visibility — an inset trace
        # need NOT be shown in the main plot (operator: "The inset trace should
        # not have to also be in the main plot as well").  Its data was stored
        # (as a data-only AXIS_NA trace) above, so the inset can draw it.
        if inset_traces:
            inset_keys = {self._trace_label(t) for t in inset_traces}
        else:
            inset_keys = set()
        # Inset LEFT-axis title for a voltage / potential inset — mirror the
        # main-axis "Potential vs <ref>" / "Voltage vs <return>" options
        # (gotcha #159).  The inset is single-select, so its content is the one
        # inset trace; map its role to a wave set and reuse the SAME shared
        # helper the main axis uses.  Ignored by the plot when the inset shows
        # current (I_mon).  ``_pot_on`` / ``_ret_on`` / ``_ref`` / ``_ret`` /
        # ``_voltage_axis_label`` were computed for the main label above.
        _inset_wave: set = set()
        for _t in (inset_traces or ()):
            if _t == TRACE_VMON:
                _inset_wave.add("V_mon")
            elif _t in (TRACE_EACT, TRACE_EACT_CORR):
                _inset_wave.add("E_act")
            elif _t in (TRACE_ERET, TRACE_ERET_CORR):
                _inset_wave.add("E_ret")
        self.scope.set_inset_voltage_label(
            _voltage_axis_label(_inset_wave, potential_axis=_pot_on,
                                reference_label=_ref, return_axis=_ret_on,
                                return_label=_ret, brackets=True))
        # If the I_mon (current) curve is in the inset, tell the plot which
        # unit it's in so the inset's SINGLE left axis is labelled accordingly
        # — Current [µA] or Current Density [A/cm²] per the experiment tab's
        # unit dropdown.  NO density right axis (operator: "for the inset with
        # Imon, do not have a right axis … Only change between current and
        # current density based on the dropdown list unit").  The stored curve
        # data is ALREADY in the chosen unit (``_use_density`` tracks the
        # dropdown), so the inset draws it as-is.
        try:
            _imon_key = self._trace_label(TRACE_IMON)
            if _imon_key in inset_keys:
                self.scope.set_inset_current_scale(
                    _imon_key, density=_use_density)
            else:
                self.scope.set_inset_current_scale(None)
        except Exception:
            pass
        self.scope.set_inset_traces(inset_keys)
        self.scope.set_inset_visible(bool(inset_visible))


class MultiChannelScope(QtWidgets.QWidget):
    """A tab-per-channel scope view with global waveform toggles.

    Two control groups along the top bar:

    * **Per-trace axis dropdowns** — one combo per trace (V_mon /
      I_mon / E_act / E_ret) with three options: ``N/A`` (hides the
      trace), ``Left y-axis``, ``Right y-axis``. Default routing:
      I_mon → right (so its µA range doesn't compress the V-scale
      traces); V_mon / E_act / E_ret → left. Hidden in the toolbar
      entirely when the scope doesn't capture that trace
      (see :meth:`set_available_traces`).
    * **Inset** — checkbox plus a multi-select dropdown (a
      QToolButton with a popup of checkable trace names) that
      controls a small below-plot inset. Useful for focusing on a
      subset of waveforms while keeping the full set visible above.
    """

    #: Emitted with the Capture now SHOWN in the plot whenever the user
    #: navigates (per-capture dropdown / prev / next, the entry list, or the
    #: Latest button).  The experiment tab connects this to the metrics-side
    #: table so the numbers always match the displayed waveform (operator:
    #: "the plot values [are] not matching with the table values" — the table
    #: used to stay on the last LIVE capture while the plot was navigated).
    captureChanged = QtCore.pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        # ----- top bar: per-trace axis dropdowns -----
        self.axis_combos: Dict[str, QtWidgets.QComboBox] = {}
        # Trace-name labels next to each combo — kept around so we can
        # show/hide them as a unit when ``set_available_traces`` runs.
        self.axis_labels: Dict[str, QtWidgets.QLabel] = {}
        # Per-trace axis assignment (live state; mirrored to prefs).
        # Visibility is encoded as AXIS_NA — no separate flag.
        self._axis_map: Dict[str, str] = dict(DEFAULT_TRACE_AXIS)
        # Which traces the scope is actually capturing. Default to all
        # four (+ the always-available derivative overlays); ``set_available_traces``
        # narrows the base once the experiment tab pushes its alias map, and
        # ``_recompute_available`` re-adds the derivatives.
        self._available_traces: set = set(ALL_TRACES) | set(ALL_DERIV_TRACES)
        # BASE availability (from the alias map) vs the CORRECTED interface
        # pair (E′act / E′ret), which is only available when a continuous-
        # sinusoidal capture is being shown (``set_corrected_shape``).  The
        # effective ``_available_traces`` is recomputed from both.
        self._base_available: set = set(ALL_TRACES)
        self._corrected_shape_ok: bool = False
        # Electrode surface area for the current run — pushed by the
        # experiment tab at run start.  ``None`` / 0 → plot I_mon in
        # µA; a positive value → plot current density (A/cm²) on the
        # right axis, with the unit on the second line of the label.
        self._surface_area_um2: Optional[float] = None
        # I_mon PLOTTING UNIT — the ``imon_unit_combo`` next to the I_mon
        # axis dropdown lets the operator choose µA vs A/cm² (operator:
        # "unit dropdown list by Imon in experiment tab to select plotting
        # in uA or A/cm2").  Default "uA" (matches the app-wide "default
        # current" preference, gotcha #49).  The A/cm² choice only takes
        # effect when a surface area is set; ``_effective_area_um2`` folds
        # the two together so the raw ``_surface_area_um2`` stays intact.
        self._imon_unit: str = "uA"
        # LEFT-AXIS LABEL — AUTOMATIC reference-aware relabel (operator: "I do
        # not want those checkboxes.  It should be automatic").  When only
        # electrode potentials (E_act / E_ret, incl. their corrected variants)
        # are on the left axis and no V_mon, it reads "Potential vs <ref> [V]"
        # (V_mon is a driving voltage; E_act / E_ret are potentials vs the
        # reference electrode); when only V_mon is on the axis it reads
        # "Voltage vs <return> [V]"; a mixed axis stays plain "Voltage [V]".
        # The electrode NAMES are pushed per run from the Setup tab (reference
        # short name / return coating); they fall back to Ag|AgCl / Pt.
        self._reference_label: str = "Ag|AgCl"
        self._return_label: str = "Pt"
        # AUTOMATIC reference-aware relabel is ON unless the device has no
        # electrodes (Plexon Test Board) — then the voltage axis stays plain
        # "Voltage [V]" (operator: "If the Test Board is connected, the unit for
        # the voltage channels can only be Voltage [V]").  Pushed per run from
        # the Setup snapshot's ``has_electrodes``.
        self._reference_aware_labels: bool = True

        # Trace toggles laid out as a COLUMN (operator) — one row per trace:
        # [coloured name][axis dropdown], both FIXED width so every dropdown
        # lines up at the same left edge AND the same width (operator: "same
        # width") regardless of label length ("1/(dV/dt)" is wider than
        # "V_mon").  The I_mon current/density unit dropdown goes on its own
        # indented row below (the narrow left column can't fit it inline).
        trace_col = QtWidgets.QVBoxLayout()
        trace_col.setSpacing(2)
        _LBL_W, _COMBO_W = 80, 112
        for trace in ALL_TOGGLE_TRACES:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(4)
            # Coloured trace name in VARIABLE format (V italic + mon
            # subscript via HTML), same hue as the curve in the plot.
            lbl = QtWidgets.QLabel(_subscript_trace_name(trace))
            lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
            lbl.setStyleSheet(
                f"color: {TRACE_COLOURS[trace]}; font-weight: bold;")
            lbl.setFixedWidth(_LBL_W)
            self.axis_labels[trace] = lbl
            row.addWidget(lbl)
            # Single dropdown: ``N/A`` hides the trace, the two axis entries
            # place it on the left or right y-axis.
            axis_combo = QtWidgets.QComboBox()
            axis_combo.addItem("N/A",          userData=AXIS_NA)
            axis_combo.addItem("Left y-axis",  userData=AXIS_LEFT)
            axis_combo.addItem("Right y-axis", userData=AXIS_RIGHT)
            default_axis = DEFAULT_TRACE_AXIS.get(trace, AXIS_LEFT)
            axis_combo.setCurrentIndex(
                {AXIS_NA: 0, AXIS_LEFT: 1, AXIS_RIGHT: 2}[default_axis])
            axis_combo.setFixedWidth(_COMBO_W)
            _is_deriv = trace in ALL_DERIV_TRACES
            axis_combo.setToolTip(
                (f"{trace}: a NORMALIZED overlay (scaled to fit the voltage "
                 f"axis — shape only, dashed) when set to an axis; N/A hides it."
                 if _is_deriv else
                 f"{trace} placement on the scope: pick "
                 f"<b>N/A</b> to hide it, <b>Left y-axis</b> for the "
                 f"main voltage scale, <b>Right y-axis</b> for the "
                 f"second (current / alternate) scale."))
            axis_combo.currentIndexChanged.connect(
                lambda _idx, t=trace: self._on_axis_changed(t))
            self.axis_combos[trace] = axis_combo
            row.addWidget(axis_combo)
            row.addStretch(1)
            trace_col.addLayout(row)
            # I_mon unit dropdown — µA vs current density (A/cm²), enabled only
            # when a surface area is set — on its OWN row, aligned under the
            # axis dropdown (won't fit inline in the narrow left column).
            if trace == TRACE_IMON:
                self.imon_unit_combo = QtWidgets.QComboBox()
                # Show JUST the unit (operator) — the row already reads "I_mon",
                # so the descriptive "Current …" text is redundant.
                self.imon_unit_combo.addItem("µA", userData="uA")
                self.imon_unit_combo.addItem("A/cm²", userData="density")
                self.imon_unit_combo.setToolTip(
                    "Plot the I_mon trace as raw current (µA) or current "
                    "density (A/cm²).  Density requires a surface area to be "
                    "set in Setup — otherwise only µA is available.")
                self.imon_unit_combo.setSizeAdjustPolicy(
                    QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToContents)
                self.imon_unit_combo.currentIndexChanged.connect(
                    self._on_imon_unit_changed)
                # Right-align the (narrow) unit dropdown so its RIGHT edge lines
                # up with the axis dropdowns' right edge (operator: "aligned to
                # the right with the other lists").  Mirror the axis row geometry
                # [label _LBL_W][combo _COMBO_W]: an _LBL_W leader (matching the
                # trace label) + a fixed _COMBO_W slot that RIGHT-aligns the
                # narrow unit combo within it — so its right edge sits at exactly
                # the axis dropdowns' right edge regardless of column width.
                urow = QtWidgets.QHBoxLayout()
                urow.setSpacing(4)
                _unit_leader = QtWidgets.QWidget()
                _unit_leader.setFixedWidth(_LBL_W)
                urow.addWidget(_unit_leader)
                _unit_slot = QtWidgets.QHBoxLayout()
                _unit_slot.setContentsMargins(0, 0, 0, 0)
                _unit_slot.setSpacing(0)
                _unit_slot.addStretch(1)
                _unit_slot.addWidget(self.imon_unit_combo)
                _unit_slot_w = QtWidgets.QWidget()
                _unit_slot_w.setFixedWidth(_COMBO_W)
                _unit_slot_w.setLayout(_unit_slot)
                urow.addWidget(_unit_slot_w)
                urow.addStretch(1)
                trace_col.addLayout(urow)
                self._refresh_imon_unit_combo()
        self._trace_col = trace_col

        # NOTE: the left-axis label ("Voltage [V]" vs "Potential vs <ref> [V]"
        # vs "Voltage vs <return> [V]") is now AUTOMATIC (operator: "I do not
        # want those checkboxes.  It should be automatic") — decided per
        # refresh in ``_ChannelPage._refresh_traces`` from the axis content.
        # The old "Potential axis" / "Voltage vs return" checkboxes are gone.

        # ----- inset controls (placed ON TOP of the left column) -----
        self.inset_check = QtWidgets.QCheckBox("Inset")
        self.inset_check.setChecked(False)
        self.inset_check.setToolTip(
            "Show a compact inset plot below the main scope, mirroring "
            "the trace you pick from the dropdown next to this "
            "toggle. Useful for keeping a focused single-trace view "
            "alongside the full multi-trace plot.")
        self.inset_check.toggled.connect(self._on_inset_toggled)
        # SINGLE-select dropdown for the inset trace (operator: "For the
        # inset, only one trace can be selected").  A ``_RichComboBox`` so the
        # trace names render in VARIABLE format (V<sub>mon</sub>).  "(none)"
        # keeps the inset blank; userData carries the trace key ("" for none).
        self.inset_combo = _RichComboBox()
        self.inset_combo.addItem("(none)", userData="")
        for trace in ALL_TOGGLE_TRACES:
            self.inset_combo.addItem(_subscript_trace_name(trace),
                                     userData=trace)
        self.inset_combo.setToolTip(
            "Pick the ONE trace to mirror in the inset plot below the main "
            "scope.  '(none)' leaves the inset blank.")
        self.inset_combo.setSizeAdjustPolicy(
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.inset_combo.setMinimumContentsLength(6)
        self.inset_combo.setMaximumWidth(96)
        self.inset_combo.currentIndexChanged.connect(
            self._on_inset_traces_changed)
        # Disabled until the inset is toggled on — clearer affordance that the
        # dropdown is inert without the inset.
        self.inset_combo.setEnabled(False)
        # Inset controls live in a container widget that is MOUNTED at the far
        # right of the ACTIVE page's view-controls row (operator: "move the
        # inset option in [reset view's former] place") — see
        # ``_reposition_inset_controls``.  Kept parented to ``self`` when no
        # page is shown so a page teardown (``clear``) never deletes it.
        self._inset_controls = QtWidgets.QWidget(self)
        _ir = QtWidgets.QHBoxLayout(self._inset_controls)
        _ir.setContentsMargins(0, 0, 0, 0)
        _ir.setSpacing(4)
        _ir.addWidget(self.inset_check)
        _ir.addWidget(self.inset_combo)

        # ----- entry list + content stack -----
        # Replaces the previous QTabWidget — a left-side ``QListWidget``
        # carries one row per channel/combination, and a
        # ``QStackedWidget`` on the right shows the matching
        # ``_ChannelPage`` for the currently-selected list row. New
        # captures append rows to the list AND push their pages onto
        # the stack (rather than spawning sub-tabs across the top).
        # Pages are keyed by a stable identifier — either an ``int``
        # channel (legacy monopolar path) or a ``str`` combination
        # label (e.g. ``"CH05 v 06"`` for bipolar). Two configurations
        # that share an active channel get distinct rows because
        # their ``str`` keys differ.
        self._pages: Dict[object, _ChannelPage] = {}
        # View → Gridlines state, forwarded to every page's ScopePlot AND
        # re-applied to any page created later (gotcha: the toggle used to
        # do nothing because MultiChannelScope had no set_grid_visible, so
        # main_window's `getattr(widget, "set_grid_visible")` was None).
        self._grid_visible = False
        # ``_keys_in_order`` maintains the insertion order so a list
        # row at index ``i`` corresponds to the i-th page on the stack
        # — Qt doesn't expose a direct row→key lookup so we keep one.
        self._keys_in_order: List[object] = []
        self._completed: set = set()
        # ----- channel/combo follow state -----
        # When True (default), a new capture auto-selects its entry so
        # the view tracks the live channel/combo.  The user selecting a
        # DIFFERENT entry pins the view there (follow OFF) until they
        # press "Latest" — operator: "If a different channel/combo was
        # selected from the latest, do not automatically go to the new
        # latest … Include a latest button … (and move to the latest
        # until a different channel/combo is selected)."  Mirrors the
        # per-page capture follow on _ChannelPage.  ``_latest_key`` is
        # the key of the most-recent capture — what "Latest" jumps to.
        # ``_programmatic_select`` guards auto-selection so it doesn't
        # read as a user pin in ``_on_entry_changed``.
        self._entry_auto_follow: bool = True
        self._latest_key = None
        self._programmatic_select: bool = False

        self.entry_list = QtWidgets.QListWidget()
        self.entry_list.setToolTip(
            "Channels and combinations under test. New entries appear "
            "as captures stream in; click an entry to view its scope "
            "trace and metric table on the right.")
        self.entry_list.setMaximumWidth(220)
        self.entry_list.currentRowChanged.connect(self._on_entry_changed)
        # "Latest" button — re-arms follow + jumps to the most-recent
        # channel/combo.  Lives under the entry list.
        self._entry_latest_btn = QtWidgets.QToolButton()
        self._entry_latest_btn.setText("⤓ Latest")
        self._entry_latest_btn.setToolTip(
            "Jump to the most recently captured channel / combination "
            "and re-arm auto-follow so the view snaps to new ones as "
            "they appear. Selecting a different entry above pins the "
            "view there until you press this.")
        self._entry_latest_btn.clicked.connect(self._jump_latest_entry)
        self._entry_latest_btn.setEnabled(False)
        self.content_stack = QtWidgets.QStackedWidget()
        # When the stack is empty we show a placeholder so the right
        # pane doesn't render as an empty grey rectangle pre-run.
        self._placeholder = QtWidgets.QLabel(
            "Captures from the experiment runner will appear here, "
            "one entry per channel / combination. Pick an entry on "
            "the left to view its trace.")
        self._placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: #888; padding: 32px;")
        self.content_stack.addWidget(self._placeholder)
        # Keep the inset control mounted on whichever page is shown (far right
        # of that page's view-controls row); detach it when the placeholder is
        # shown so a page teardown can't delete it.
        self.content_stack.currentChanged.connect(
            self._reposition_inset_controls)

        # Left column, top → bottom: the inset control, the aligned trace-axis
        # dropdowns, the axis-label options, then the channel/combination list
        # (stretch) with the Latest button below it.  Putting ALL the controls
        # in this narrow left column — rather than in a top strip above the
        # plot — lets the plot fill the FULL height on the right, removing the
        # empty band that used to sit above it (operator: "stretch the
        # experiment up, remove the empty space").
        left_col = QtWidgets.QWidget()
        left_v = QtWidgets.QVBoxLayout(left_col)
        left_v.setContentsMargins(0, 0, 0, 0)
        left_v.setSpacing(4)
        # (The inset control is no longer in the left column — it's mounted on
        # the active plot's control row, next to Reset view.)
        left_v.addLayout(self._trace_col)            # aligned trace dropdowns
        left_v.addWidget(self.entry_list, stretch=1)  # channel/combo list
        left_v.addWidget(self._entry_latest_btn)
        left_col.setMaximumWidth(215)

        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.addWidget(left_col)
        split.addWidget(self.content_stack)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([205, 520])
        self._main_split = split

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(split, stretch=1)
        # Initial state: the placeholder is shown → keep the inset control
        # detached + hidden until the first page appears (currentChanged then
        # mounts it on that page).
        self._reposition_inset_controls()

    # ---------------------------------------------------------- public API
    def visibility(self) -> Dict[str, bool]:
        """Per-trace visibility derived from the axis map.

        A trace is visible iff its axis is left / right (i.e. NOT
        ``AXIS_NA``). Traces the scope doesn't capture are
        unconditionally hidden — :meth:`set_available_traces` clears
        them via the same axis-map path.
        """
        return {t: (self._axis_map.get(t) != AXIS_NA
                    and t in self._available_traces)
                for t in ALL_TOGGLE_TRACES}

    def set_visibility(self, vis: Dict[str, bool]):
        """Compatibility shim for legacy prefs.

        New prefs encode visibility inside ``axis_map`` (``AXIS_NA`` =
        hidden) so this is only invoked when restoring an old profile
        with a separate ``visibility`` dict. Maps each ``False`` entry
        to ``AXIS_NA`` and each ``True`` entry back to the trace's
        default axis (so the user gets a sensible placement when the
        old prefs didn't carry an ``axis_map``).
        """
        for t, on in vis.items():
            if t not in self.axis_combos:
                continue
            if not on:
                self._set_axis(t, AXIS_NA)
            else:
                # Restore to the trace default if the current axis is
                # AXIS_NA; otherwise leave whatever the user picked.
                if self._axis_map.get(t) == AXIS_NA:
                    self._set_axis(t, DEFAULT_TRACE_AXIS.get(t, AXIS_LEFT))
        self._on_visibility_toggled()

    def axis_map(self) -> Dict[str, str]:
        """Per-trace ``axis`` snapshot — values are ``"left"``,
        ``"right"`` or ``"na"`` (hidden)."""
        return dict(self._axis_map)

    def set_axis_map(self, axes: Dict[str, str]) -> None:
        """Apply a saved per-trace axis assignment."""
        for trace, axis in axes.items():
            if trace not in self.axis_combos:
                continue
            if axis not in (AXIS_LEFT, AXIS_RIGHT, AXIS_NA):
                continue
            self._set_axis(trace, axis)
        # Push the new assignment into every existing page.
        self._on_visibility_toggled()

    def _set_axis(self, trace: str, axis: str) -> None:
        """Block-signals helper — sets the combo's index AND the
        cached ``_axis_map`` entry without firing the change signal.
        Used by :meth:`set_axis_map` / :meth:`set_visibility` so a
        bulk restore doesn't trigger N redundant repaints.
        """
        combo = self.axis_combos.get(trace)
        if combo is None:
            return
        idx = {AXIS_NA: 0, AXIS_LEFT: 1, AXIS_RIGHT: 2}.get(axis, 1)
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(idx)
        finally:
            combo.blockSignals(False)
        self._axis_map[trace] = axis

    def set_available_traces(self, available) -> None:
        """Tell the scope which traces the device is actually capturing.

        Hides the row entirely for traces that aren't in
        ``available`` — saves a row of dead controls when the scope
        is only mapped to V_mon / I_mon, for instance.

        **Per-spec rule:** if ``E_ret`` is captured, ``E_act`` is
        added too (the convention is that E_act is always plotted
        alongside E_ret when either is recorded — the user's note:
        "include E_act if E_ret is a channel").

        Pass an empty iterable or ``None`` to make every trace
        available (the default; useful for tests / legacy callers).
        """
        if not available:
            base_set = set(ALL_TRACES)
        else:
            base_set = {str(t) for t in available if t in ALL_TRACES}
        # E_ret implies E_act per the user spec.
        if TRACE_ERET in base_set:
            base_set.add(TRACE_EACT)
        self._base_available = base_set
        # Recompute the effective availability (base + the corrected pair when
        # a sinusoidal capture is shown) and repaint — an alias change is a
        # real run event.
        self._recompute_available(repaint=True)

    def set_corrected_shape(self, sinusoidal: bool) -> None:
        """Mark whether the CURRENTLY-shown capture is a continuous sinusoid,
        which is what makes the corrected interface pair (E′act / E′ret)
        AVAILABLE as toggle-bar rows + inset options.  Called per-capture from
        the page's ``_refresh_traces``; idempotent (a no-op when unchanged) so
        it never loops.  UI-only (no page repaint) — the current
        ``_refresh_traces`` already draws the corrected curves via the
        axis-map gate, so this only shows/hides the toggle-bar controls."""
        sinusoidal = bool(sinusoidal)
        if sinusoidal == self._corrected_shape_ok:
            return
        self._corrected_shape_ok = sinusoidal
        self._recompute_available(repaint=False)

    def _recompute_available(self, *, repaint: bool) -> None:
        """Rebuild ``_available_traces`` from the base alias set + the
        corrected pair (available only when a sinusoidal capture is shown AND
        its source electrode is captured), then show / hide the per-trace rows
        and prune the inset selection."""
        avail = set(self._base_available)
        if self._corrected_shape_ok:
            # E′act needs V_mon OR E_act; E′ret needs E_ret.
            if TRACE_VMON in avail or TRACE_EACT in avail:
                avail.add(TRACE_EACT_CORR)
            if TRACE_ERET in avail:
                avail.add(TRACE_ERET_CORR)
        # Derivative overlays are ALWAYS available (any capture is
        # differentiable) as long as a source trace (V_mon or E_act) is present.
        if TRACE_VMON in avail or TRACE_EACT in avail:
            avail.update(ALL_DERIV_TRACES)
        self._available_traces = avail
        # Show / hide the per-trace controls (label + axis combo) as a unit.
        # An unavailable trace keeps its saved axis choice; only the runtime
        # ``_available_traces`` check gates rendering.
        for trace in ALL_TOGGLE_TRACES:
            vis = trace in avail
            self.axis_labels[trace].setVisible(vis)
            self.axis_combos[trace].setVisible(vis)
        # Prune the inset selection: a trace that's no longer available
        # shouldn't stay selected, and its combo row is hidden so it can't be
        # re-picked while absent.
        self.inset_combo.blockSignals(True)
        try:
            for i in range(1, self.inset_combo.count()):      # skip "(none)"
                trace = self.inset_combo.itemData(i)
                self.inset_combo.view().setRowHidden(i, trace not in avail)
            cur = self.inset_combo.currentData()
            if cur and cur not in avail:
                self.inset_combo.setCurrentIndex(0)           # → "(none)"
        finally:
            self.inset_combo.blockSignals(False)
        if repaint:
            self._on_visibility_toggled()

    def available_traces(self) -> set:
        """Snapshot of the currently-captured traces (after the
        E_ret → E_act expansion)."""
        return set(self._available_traces)

    def inset_enabled(self) -> bool:
        return bool(self.inset_check.isChecked())

    def set_inset_enabled(self, on: bool) -> None:
        self.inset_check.setChecked(bool(on))   # triggers _on_inset_toggled

    def inset_traces(self) -> List[str]:
        # SINGLE-select: the combo carries at most one trace (or "(none)").
        cur = self.inset_combo.currentData()
        return [cur] if cur else []

    def set_inset_traces(self, traces) -> None:
        # Single-select: take the FIRST requested trace (backward-compatible
        # with the old multi-select prefs, which stored a list).  Unknown /
        # empty → "(none)".
        names = list(traces or [])
        want = names[0] if names else ""
        idx = self.inset_combo.findData(want) if want else 0
        if idx < 0:
            idx = 0
        self.inset_combo.blockSignals(True)
        try:
            self.inset_combo.setCurrentIndex(idx)
        finally:
            self.inset_combo.blockSignals(False)
        self._on_visibility_toggled()

    def ensure_tab(self, key) -> _ChannelPage:
        """Backward-compatible alias for :meth:`ensure_page`."""
        return self.ensure_page(key)

    def set_grid_visible(self, visible: bool) -> None:
        """Forward the View → Gridlines toggle to EVERY channel/combo page's
        ScopePlot (the main window walks ``tab.multichan_scope`` and calls this;
        without it the experiment scope's grid never toggled).  Remembers the
        state so a page created LATER (a new channel streaming in mid-run) also
        gets it."""
        self._grid_visible = bool(visible)
        for page in self._pages.values():
            setter = getattr(getattr(page, "scope", None), "set_grid_visible", None)
            if callable(setter):
                try:
                    setter(self._grid_visible)
                except Exception:
                    pass

    def ensure_page(self, key) -> _ChannelPage:
        """Get/create the entry-list row + stacked-widget page for ``key``.

        ``key`` is either an ``int`` channel number (legacy monopolar
        path — rendered as ``"CH05"`` in the row label) or a ``str``
        combination label (multipolar path — used as the row label
        verbatim, e.g. ``"CH05 v 06"`` for a bipolar pair). Two
        configurations that share an active channel produce distinct
        rows because their ``str`` keys differ — the previous
        ``int``-only key collapsed them into one.

        **Timing**: this is the only entry-creation path. Called by
        :meth:`add_capture` (first capture for a key — including
        failed / aborted captures) AND by :meth:`add_pending` (an
        attempt starts but no capture has landed yet). Either way,
        the row appears as soon as ANY attempt is made, never
        deferred to completion. The ✓ marker (via
        :meth:`mark_completed`) is purely a status tag added on top
        of an already-existing row; it never creates rows.
        """
        if key in self._pages:
            return self._pages[key]
        page = _ChannelPage(key)
        page._scope_parent = self    # so per-capture nav can emit captureChanged
        # Apply the current gridlines state so a page created after the View →
        # Gridlines toggle still shows the grid.
        if self._grid_visible:
            try:
                page.scope.set_grid_visible(True)
            except Exception:
                pass
        self._pages[key] = page
        self._keys_in_order.append(key)
        self.content_stack.addWidget(page)
        item = QtWidgets.QListWidgetItem(self._title(key))
        item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
        self.entry_list.addItem(item)
        # Auto-select the very first entry so the user never sees the
        # placeholder once captures start streaming. Subsequent
        # entries don't steal focus — that's
        # :meth:`add_capture`'s job.  Programmatic so it doesn't read
        # as a user pin.
        if self.entry_list.count() == 1:
            self._select_entry_row(0)
        return page

    def add_pending(self, key) -> _ChannelPage:
        """Pre-emptively create an entry for ``key`` before any
        capture has arrived.

        Useful at "attempt start" — the experiment runner has just
        begun a configuration and we want the row to appear in the
        list immediately, not wait for the first acquired waveform.
        The right-side page is empty (its scope shows no curves and
        the metric table is blank) until the first :meth:`add_capture`
        fills it in.

        Idempotent: calling on a key that already exists is a no-op.
        Returns the page either way so callers can attach further
        state if they wish.
        """
        return self.ensure_page(key)

    def add_capture(self, capture: Capture, key):
        """Drop a capture into ``key``'s page and focus it in the list.

        **Adds the entry on every capture, not on completion.** The
        row appears as soon as the FIRST capture (good or bad) lands
        for ``key``, so the user watches the entry list grow as
        attempts happen — never deferred until a configuration
        finishes. ``capture.status.good`` is *not* gated here; a
        failed acquisition still spawns the row, with whatever
        partial data it carries rendered into the page.
        """
        page = self.ensure_page(key)
        page.set_capture(capture, self.visibility(),
                         axis_map=self._axis_map,
                         inset_visible=self.inset_enabled(),
                         inset_traces=set(self.inset_traces()),
                         surface_area_um2=self._effective_area_um2(),
                         title_area_um2=self._surface_area_um2)
        # This key is now the "latest" — what the Latest button jumps to.
        self._latest_key = key
        # Only pull the selection to the new capture when auto-follow is
        # armed. If the user has pinned a DIFFERENT entry, leave their
        # view put (operator: don't auto-jump to the new latest) — the
        # row still appears in the list and its page fills in silently.
        if self._entry_auto_follow:
            idx = self._keys_in_order.index(key)
            self._select_entry_row(idx)
        # Sync the metrics table to the capture ACTUALLY ON SCREEN — but
        # ONLY when this capture's page is the one currently displayed.
        # In a multi-config sweep a capture can arrive for a channel the
        # user ISN'T looking at (they pinned a completed channel while the
        # next one ramps); without this guard the table showed that
        # ramping channel's tiny early capture (e.g. 5 µA) while the plot
        # stayed on the pinned high-amplitude one — every value mismatched
        # (operator: "why are the plot values not matching the table").
        # captureChanged → metrics_side.show_capture is the SINGLE table
        # source now; ``_on_capture`` no longer pushes the raw capture.
        if page is self.content_stack.currentWidget():
            cap = page.current_capture()
            if cap is not None:
                try:
                    self.captureChanged.emit(cap)
                except Exception:
                    pass
        self._refresh_latest_btn()

    def set_surface_area_um2(self, area_um2: Optional[float]) -> None:
        """Set (or clear) the electrode surface area used for I_mon
        rescaling.  ``None`` / 0 → I_mon plots as raw current (µA);
        a positive value → I_mon plots as current density (A/cm²)
        with the unit on the second line of the right-axis label.

        Idempotent: a no-change call returns without touching any
        page.  When the area actually changes, every existing page
        is asked to redraw with the new presentation so a mid-run
        toggle takes effect immediately.
        """
        try:
            new_area = float(area_um2) if area_um2 else None
            if new_area is not None and new_area <= 0:
                new_area = None
        except (TypeError, ValueError):
            new_area = None
        if new_area == self._surface_area_um2:
            return
        self._surface_area_um2 = new_area
        # A/cm² is only offered when an area exists — refresh the unit
        # combo's enabled/visible state (and fall back to µA if the area
        # was cleared while density was selected).
        self._refresh_imon_unit_combo()
        # Push the new presentation into every existing page so the
        # axis label + I_mon scaling update without waiting for the
        # next capture.
        self._rerender_all_pages()

    def _rerender_all_pages(self) -> None:
        """Re-render every existing page with the current global toggles.
        Used when a display-only setting changes mid-run (surface area, the
        left-axis label option, the reference name) so the change takes
        effect immediately instead of waiting for the next capture."""
        vis = self.visibility()
        for page in self._pages.values():
            try:
                page.refresh_visibility(
                    vis, axis_map=self._axis_map,
                    inset_visible=self.inset_enabled(),
                    inset_traces=set(self.inset_traces()),
                    surface_area_um2=self._effective_area_um2(),
                    title_area_um2=self._surface_area_um2)
            except Exception:
                pass

    def set_reference_label(self, name: Optional[str]) -> None:
        """Set the reference-electrode name used in the "Potential vs <ref>
        [V]" left-axis label (pushed per run from the Setup tab; falls back
        to ``Ag|AgCl``).  The relabel is automatic, so a name change always
        re-renders the affected pages."""
        ref = (str(name).strip() if name else "") or "Ag|AgCl"
        if ref == self._reference_label:
            return
        self._reference_label = ref
        self._rerender_all_pages()

    def reference_label(self) -> str:
        """The reference-electrode name currently used for the potential-axis
        label."""
        return self._reference_label

    def set_return_label(self, name: Optional[str]) -> None:
        """Set the return / counter-electrode name used in the "Voltage vs
        <return> [V]" left-axis label (pushed per run from the Setup tab;
        falls back to ``Pt``).  The relabel is automatic, so a name change
        always re-renders the affected pages."""
        ret = (str(name).strip() if name else "") or "Pt"
        if ret == self._return_label:
            return
        self._return_label = ret
        self._rerender_all_pages()

    def return_label(self) -> str:
        """The return-electrode name currently used for the return-axis
        label."""
        return self._return_label

    def set_reference_aware_labels(self, enabled: bool) -> None:
        """Enable/disable the AUTOMATIC reference-aware left-axis relabel
        ("Potential vs <ref> [V]" / "Voltage vs <return> [V]").  Disabled (→
        plain "Voltage [V]") for a device with NO electrodes — the Plexon Test
        Board — because it has no reference / return electrode to name
        (operator: "If the Test Board is connected, the unit for the voltage
        channels can only be Voltage [V]").  Re-renders on change."""
        val = bool(enabled)
        if val == getattr(self, "_reference_aware_labels", True):
            return
        self._reference_aware_labels = val
        self._rerender_all_pages()

    def reference_aware_labels(self) -> bool:
        """Whether the reference-aware left-axis relabel is currently active."""
        return self._reference_aware_labels

    def surface_area_um2(self) -> Optional[float]:
        """Return the RAW area set by the experiment tab (independent of the
        unit choice), or ``None`` when unset."""
        return self._surface_area_um2

    def _effective_area_um2(self) -> Optional[float]:
        """The area to pass to the render layer: the raw area ONLY when the
        operator picked A/cm² AND an area is set; ``None`` (→ µA) otherwise.
        Keeps ``_surface_area_um2`` intact so switching back to density
        doesn't need the area re-pushed."""
        if self._imon_unit == "density" and self._surface_area_um2:
            return self._surface_area_um2
        return None

    def _refresh_imon_unit_combo(self) -> None:
        """Enable/disable the A/cm² item based on whether an area is set,
        forcing µA when no area exists (operator: "Hide the A/cm² option if
        there is no area inputted").  The density row is HIDDEN (0-height)
        without an area so the dropdown offers µA only."""
        combo = getattr(self, "imon_unit_combo", None)
        if combo is None:
            return
        has_area = bool(self._surface_area_um2)
        combo.blockSignals(True)
        # Show/hide the density row via its model item (index 1).
        view = combo.view()
        try:
            view.setRowHidden(1, not has_area)
        except Exception:
            pass
        item = combo.model().item(1)
        if item is not None:
            item.setEnabled(has_area)
        if not has_area and self._imon_unit == "density":
            self._imon_unit = "uA"
        combo.setCurrentIndex(0 if self._imon_unit == "uA" else 1)
        combo.blockSignals(False)

    def _on_imon_unit_changed(self, _idx: int) -> None:
        """Operator picked µA vs A/cm² — restyle the I_mon axis + re-render
        every page with the chosen presentation."""
        combo = getattr(self, "imon_unit_combo", None)
        if combo is None:
            return
        unit = combo.currentData()
        # Guard: a disabled density pick (no area) snaps back to µA.
        if unit == "density" and not self._surface_area_um2:
            unit = "uA"
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
        self._imon_unit = unit or "uA"
        vis = self.visibility()
        for page in self._pages.values():
            try:
                page.refresh_visibility(
                    vis, axis_map=self._axis_map,
                    inset_visible=self.inset_enabled(),
                    inset_traces=set(self.inset_traces()),
                    surface_area_um2=self._effective_area_um2(),
                    title_area_um2=self._surface_area_um2)
            except Exception:
                pass

    def mark_completed(self, key):
        """Tag ``key``'s entry as finished — small ✓ at the end of
        the row label — so the user sees at a glance which channel /
        combination is done.
        """
        self._completed.add(key)
        if key in self._pages:
            try:
                idx = self._keys_in_order.index(key)
            except ValueError:
                return
            item = self.entry_list.item(idx)
            if item is not None:
                item.setText(self._title(key))

    def _reposition_inset_controls(self, *_) -> None:
        """Mount the inset control on the ACTIVE page's view-controls row (far
        right, next to Reset view); detach it (parented to self, hidden) when
        the placeholder — or any non-page widget — is shown, so a page teardown
        never deletes it (operator: "move the inset option in [reset view's
        former] place")."""
        ctrls = getattr(self, "_inset_controls", None)
        if ctrls is None:
            return
        page = self.content_stack.currentWidget()
        scope = getattr(page, "scope", None)
        mount = getattr(scope, "mount_extra_control", None)
        if callable(mount):
            mount(ctrls)
            ctrls.setVisible(True)
        else:
            ctrls.setParent(self)
            ctrls.setVisible(False)

    def clear(self):
        """Drop every entry and reset to the placeholder view."""
        self._pages.clear()
        self._completed.clear()
        self._keys_in_order.clear()
        self.entry_list.clear()
        # Reset follow state for the next run.
        self._entry_auto_follow = True
        self._latest_key = None
        self._refresh_latest_btn()
        # Detach the inset control back to self BEFORE tearing down pages, so a
        # page that currently hosts it isn't deleted with the control attached.
        # (setCurrentIndex(0) below then fires currentChanged → it stays detached
        # + hidden on the placeholder.)
        self._inset_controls.setParent(self)
        self._inset_controls.setVisible(False)
        # Tear down every page widget except the placeholder at index
        # 0; the placeholder stays so a subsequent run can reuse it.
        while self.content_stack.count() > 1:
            w = self.content_stack.widget(self.content_stack.count() - 1)
            self.content_stack.removeWidget(w)
            w.deleteLater()
        self.content_stack.setCurrentIndex(0)

    # ---------------------------------------------------------- helpers
    def _trace_label(self, trace: str) -> str:
        # QCheckBox text is PLAIN (no HTML rendering — a <sub> tag would
        # show as literal angle brackets).  So here we DROP the underscore
        # rather than subscript it (operator: "… then remove the
        # underscore"): "V_mon" -> "Vmon".  The legend (which DOES render
        # HTML) subscripts instead — see module ``_subscript_trace_name``.
        return trace.replace("_", "")

    def _title(self, key) -> str:
        """Tab title — accepts ``int`` (legacy CHnn) or ``str``
        (verbatim combination label). Appends a green ✓ when the
        run for that key has finished."""
        check = " ✓" if key in self._completed else ""
        if isinstance(key, int):
            return f"CH{key:02d}{check}"
        return f"{key}{check}"

    def _on_visibility_toggled(self, *_):
        vis = self.visibility()
        for page in self._pages.values():
            page.refresh_visibility(
                vis, axis_map=self._axis_map,
                inset_visible=self.inset_enabled(),
                inset_traces=set(self.inset_traces()),
                surface_area_um2=self._effective_area_um2(),
                title_area_um2=self._surface_area_um2)

    def _on_entry_changed(self, row: int) -> None:
        """A list row became current — swap the stacked content.

        Stack widget index 0 is the placeholder; the page for list
        row ``i`` lives at stack index ``i + 1``. ``row == -1`` (no
        selection) falls back to the placeholder so the right pane
        reads as "no entry selected" instead of staying on whichever
        page was last visible.

        **Follow handling:** when the change was USER-initiated (not the
        programmatic auto-follow guard), selecting an entry that ISN'T
        the latest pins the view there (follow OFF); selecting the latest
        re-arms follow. Programmatic selections (auto-follow, first-entry
        focus, the Latest button) leave the follow flag untouched.
        """
        if row < 0 or row >= len(self._keys_in_order):
            self.content_stack.setCurrentIndex(0)
            return
        # +1 because the placeholder occupies stack index 0.
        self.content_stack.setCurrentIndex(row + 1)
        # Re-point the metrics table at the newly-shown page's capture so the
        # numbers match the displayed waveform.
        page = self._pages.get(self._keys_in_order[row])
        if page is not None:
            cap = page.current_capture()
            if cap is not None:
                try:
                    self.captureChanged.emit(cap)
                except Exception:
                    pass
        if not self._programmatic_select:
            # User clicked a row.  Follow only if it IS the latest.
            key = self._keys_in_order[row]
            self._entry_auto_follow = (key == self._latest_key)
            self._refresh_latest_btn()

    def _select_entry_row(self, idx: int) -> None:
        """Programmatically focus list row ``idx`` WITHOUT it counting as
        a user pin — the ``_programmatic_select`` guard tells
        ``_on_entry_changed`` to leave the follow flag alone."""
        if idx < 0 or idx >= self.entry_list.count():
            return
        prev = self._programmatic_select
        self._programmatic_select = True
        try:
            self.entry_list.setCurrentRow(idx)
        finally:
            self._programmatic_select = prev

    def _jump_latest_entry(self) -> None:
        """Latest button: re-arm auto-follow and jump to the most-recent
        channel/combo."""
        self._entry_auto_follow = True
        if self._latest_key is not None and self._latest_key in self._pages:
            try:
                idx = self._keys_in_order.index(self._latest_key)
            except ValueError:
                idx = -1
            if idx >= 0:
                self._select_entry_row(idx)
        self._refresh_latest_btn()

    def _refresh_latest_btn(self) -> None:
        """Enable the Latest button only when the view is pinned OFF the
        latest entry (i.e. there is somewhere to jump back to)."""
        btn = getattr(self, "_entry_latest_btn", None)
        if btn is None:
            return
        btn.setEnabled(self._latest_key is not None
                       and not self._entry_auto_follow)

    def _on_axis_changed(self, trace: str) -> None:
        """User picked a new option for the trace's dropdown.

        ``userData`` is one of ``AXIS_NA`` / ``AXIS_LEFT`` /
        ``AXIS_RIGHT``; we cache it and trigger a full repaint of
        every channel page (the visibility derives from
        ``axis != AXIS_NA``, so toggling NA on/off effectively shows
        and hides the trace).
        """
        combo = self.axis_combos.get(trace)
        if combo is None:
            return
        new_axis = combo.currentData() or AXIS_LEFT
        if new_axis not in (AXIS_NA, AXIS_LEFT, AXIS_RIGHT):
            new_axis = AXIS_LEFT
        self._axis_map[trace] = new_axis
        self._on_visibility_toggled()

    def _on_inset_toggled(self, on: bool) -> None:
        """Inset checkbox toggled — show/hide the inset plot AND
        enable/disable the single-select dropdown next to it."""
        self.inset_combo.setEnabled(bool(on))
        self._on_visibility_toggled()

    def _on_inset_traces_changed(self, *_) -> None:
        """The inset trace selection changed in the combo."""
        self._on_visibility_toggled()

    # --------------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        """Snapshot the global toggles for save/restore across sessions.

        Visibility is encoded inside ``axis_map`` (``AXIS_NA`` = hidden)
        rather than as a separate key — collapses what used to be two
        round-trips into one.
        """
        return {
            "axis_map": self.axis_map(),
            "inset_enabled": self.inset_enabled(),
            "inset_traces": self.inset_traces(),
            "imon_unit": self._imon_unit,
        }

    def restore_prefs(self, p: dict) -> None:
        """Apply saved toggles. Defensive on every key — a missing or
        mistyped value is ignored so a stale prefs file from a
        previous version never breaks the launch.

        **Backward compat**: pre-AXIS_NA prefs carried a separate
        ``visibility`` dict (bool per trace). When present, we merge
        it into the axis map by forcing hidden traces to ``AXIS_NA``
        so the new format absorbs the legacy state cleanly.
        """
        if not isinstance(p, dict) or not p:
            return
        axes = dict(p.get("axis_map") or {})
        vis = p.get("visibility")
        if isinstance(vis, dict):
            for trace, on in vis.items():
                if trace in ALL_TRACES and not on:
                    axes[trace] = AXIS_NA
        if axes:
            self.set_axis_map(axes)
        if "inset_enabled" in p:
            try:
                self.set_inset_enabled(bool(p["inset_enabled"]))
            except (TypeError, ValueError):
                pass
        traces = p.get("inset_traces")
        if isinstance(traces, (list, tuple, set)):
            self.set_inset_traces(traces)
        unit = p.get("imon_unit")
        if unit in ("uA", "density"):
            self._imon_unit = unit
            self._refresh_imon_unit_combo()
        # Legacy prefs may carry "potential_axis_label" / "return_axis_label"
        # from the removed checkboxes — the relabel is automatic now, so they
        # are simply ignored.
