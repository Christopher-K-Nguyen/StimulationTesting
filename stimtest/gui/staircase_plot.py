"""Staircase plot for the Progressive-Stress experiment.

Visualises ``I_stim`` vs time as a step function:

   I (µA) ▲
          │       ┌──────
          │       │
          │   ┌───┘
          │   │                I_stim(t)
          │ ┌─┘
          │ │
        0 ┴─┴────────────────────► t (s)
          0  t_step  2·t_step  …

Updated live whenever the user tweaks ``start_ua``, ``step_ua``,
``t_step_s``, or ``max_ua``. Read-only — same wheel-zoom-on-x +
locked-y model as the pattern preview.
"""
from __future__ import annotations

from typing import Tuple

import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from . import rich


# Unified font size for axis labels, tick labels, and the header.
# Matches :data:`stimtest.gui.pattern_preview.PLOT_FONT_PT` so the
# staircase reads at the same typographic weight as the pulse-
# pattern figure it sits next to in the Progressive Stress tab's
# figure switcher.
PLOT_FONT_PT = 11


# Unicode superscript digits (plus minus sign) so the top-axis
# "Number of Pulses" tick labels render as proper 10ⁿ powers
# without needing HTML rendering (pyqtgraph tick labels are
# plain text).
_SUPERSCRIPT_TR = str.maketrans("0123456789-", "⁰¹²³"
                                                "⁴⁵⁶⁷"
                                                "⁸⁹⁻")


def _format_scientific(value: float) -> str:
    """Format ``value`` (cumulative pulse count) in scientific
    notation using Unicode superscript exponents — e.g.
    ``3600`` → ``"3.6×10³"``. Small values (< 10) render as plain
    integers / one-decimal floats so the label doesn't clutter
    with a trivial ``10⁰`` exponent."""
    import math
    if not math.isfinite(value) or value <= 0:
        return "0"
    if value < 10.0:
        # Avoid the trivial mantissa-only scientific case where
        # ``1.2×10⁰`` reads worse than just ``1.2``.
        if value < 1.0:
            return f"{value:.2f}"
        return f"{value:.1f}".rstrip("0").rstrip(".") or "0"
    exp = int(math.floor(math.log10(value)))
    mant = value / (10.0 ** exp)
    # Drop trailing ".0" so "1.0×10³" reads as "1×10³".
    mant_str = f"{mant:.1f}"
    if mant_str.endswith(".0"):
        mant_str = mant_str[:-2]
    return f"{mant_str}×10" + str(exp).translate(_SUPERSCRIPT_TR)


def _scientific_pulse_ticks(t_lo: float, t_hi: float,
                            rate_hz: float) -> list:
    """Generate ``[(t, label)]`` ticks evenly spaced across the
    visible time range ``[t_lo, t_hi]`` (seconds), with each
    label rendered as the cumulative pulse count in SCIENTIFIC
    NOTATION (Unicode-superscript exponents — e.g.
    ``"7.2×10⁴"``)."""
    if rate_hz <= 0 or t_hi <= t_lo:
        return []
    step = (t_hi - t_lo) / 5.0
    ticks: list = []
    x = t_lo
    for _ in range(6):
        pulses = max(x * rate_hz, 0.0)
        ticks.append((x, _format_scientific(pulses)))
        x += step
    return ticks


class StaircasePlot(QtWidgets.QWidget):
    """I_stim vs time staircase for Progressive Stress.

    Sized to match :class:`stimtest.gui.pattern_preview.PatternPreview`
    so the two figures behave identically inside the Progressive
    Stress tab's figure-switcher QTabWidget — same minimum height,
    same preferred height, same maximum, same size policy, same
    sizeHint. Switching between the "Pulse pattern" and "Staircase"
    tabs no longer makes the column reflow.
    """

    # Mirrors PatternPreview.MIN_HEIGHT / PREFERRED_HEIGHT / MAX_HEIGHT
    # one-for-one. Keep these constants in sync with that class so
    # the two figures occupy identical real estate in the QTabWidget
    # that hosts them.
    MIN_HEIGHT = 160
    PREFERRED_HEIGHT = 200
    MAX_HEIGHT = 800

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        # Same policy as PatternPreview — Expanding horizontally (fill
        # the column), Preferred vertically (settle at sizeHint, let
        # the parent QScrollArea handle overflow). Previously the
        # staircase pinned ``setMaximumHeight(240)`` which clipped it
        # below the pattern preview's range and made the tab heights
        # diverge.
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Preferred)

        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        # Mouse wheel must NOT zoom (operator request).
        from .widgets import disable_plot_wheel_zoom
        disable_plot_wheel_zoom(self.plot)
        # Gridlines default OFF on experiment plots so subtle
        # trace features aren't obscured. The main window's View
        # → Gridlines action toggles them on/off for every
        # experiment plot at once via :meth:`set_grid_visible`.
        self._grid_visible: bool = False
        self.plot.showGrid(x=False, y=False)
        # Axis labels:
        #   * bottom: ``Time (s)`` — staircase steps are tens-to-
        #     hundreds of seconds, so seconds is the natural unit.
        #   * top:    ``Number of Pulses`` — synthesised tick labels
        #     showing cumulative pulse count = time_s × rate_hz.
        #     Useful for reading "how many pulses has each step
        #     delivered" without leaving the staircase view.
        #   * left:   ``Current (µA)`` — what the staircase plots.
        #   * right:  ``Charge/Phase (nC/ph)`` — derived from the
        #     left-axis current via Q_ph = I · W_phase / 1000
        #     (in µA·µs/1000 = nC). Same tick positions as left
        #     axis; only the LABELS are converted.
        label_style = {"font-size": f"{PLOT_FONT_PT}pt", "color": "#000"}
        self.plot.setLabel("bottom", "Time (s)", **label_style)
        self.plot.setLabel("left", f"Current ({rich.UA})", **label_style)
        self.plot.showAxis("top")
        self.plot.getPlotItem().getAxis("top").setLabel(
            "Number of Pulses", **label_style)
        self.plot.showAxis("right")
        self.plot.getPlotItem().getAxis("right").setLabel(
            "Charge/Phase (nC/ph)", **label_style)
        # Tick-label font — pyqtgraph's AxisItem reads ``tickFont`` from
        # ``setStyle``. Sized to match the axis title so the ticks
        # don't visually drop below the label they sit under.
        tick_font = QtGui.QFont()
        tick_font.setPointSize(PLOT_FONT_PT)
        for ax_name in ("bottom", "left", "top", "right"):
            ax = self.plot.getAxis(ax_name)
            if ax is not None:
                ax.setStyle(tickFont=tick_font)
        # Mouse-wheel zoom enabled on both axes. The matched
        # scrollbars below let the user pan in either direction
        # without dragging — the staircase can be tall and wide.
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        vb = self.plot.getViewBox()
        if vb is not None:
            vb.setMouseEnabled(x=True, y=True)
            vb.setMenuEnabled(False)
            vb.sigXRangeChanged.connect(self._on_view_changed)
            vb.sigYRangeChanged.connect(self._on_view_changed)
        self.curve = self.plot.plot(pen=pg.mkPen("#c62828", width=2))

        self.header = QtWidgets.QLabel()
        self.header.setTextFormat(QtCore.Qt.TextFormat.RichText)
        # Header sized to match the axis/tick font so the figure reads
        # as one typographic system (rather than a tiny header + larger
        # axis labels, which was the previous mismatch).
        self.header.setStyleSheet(
            f"padding: 4px; background-color: #ffe0b2; "
            f"border: 1px solid #ffb74d; border-radius: 3px; "
            f"color: #4e342e; font-weight: bold; "
            f"font-size: {PLOT_FONT_PT}pt;"
        )

        # Horizontal + vertical scrollbars, synced with the plot's
        # X/Y view range. Resolution is integer microamps / seconds
        # (Qt scrollbars are int-based); fine enough for staircases.
        self.h_scroll = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal)
        self.h_scroll.valueChanged.connect(self._on_h_scroll)
        self.v_scroll = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Vertical)
        # Qt vertical scrollbars increase value top-to-bottom; we want
        # increasing y-values mapping to "scrolling down" through them
        # so the bar reads naturally.
        self.v_scroll.valueChanged.connect(self._on_v_scroll)
        self._suppress_sync = False

        # Reset / zoom / height buttons — mirrors the pulse-pattern
        # preview's control set so the two figures feel symmetric.
        # Same callbacks, same step factors, same tooltips. Built
        # via a small ``_btn`` helper for consistency with the
        # pattern-preview implementation.
        def _btn(text: str, tip: str, callback) -> QtWidgets.QToolButton:
            b = QtWidgets.QToolButton()
            b.setText(text); b.setToolTip(tip)
            b.setAutoRaise(False)
            b.clicked.connect(callback)
            return b

        self.x_in_btn  = _btn("X+",  "Zoom in X axis (around view centre)",
                              lambda: self._zoom_axis('x', 0.7))
        self.x_out_btn = _btn("X−",  "Zoom out X axis (around view centre)",
                              lambda: self._zoom_axis('x', 1.4))
        self.y_in_btn  = _btn("Y+",  "Zoom in Y axis (around view centre)",
                              lambda: self._zoom_axis('y', 0.7))
        self.y_out_btn = _btn("Y−",  "Zoom out Y axis (around view centre)",
                              lambda: self._zoom_axis('y', 1.4))
        self.tall_btn  = _btn("+", "Increase plot height (40 px)",
                              lambda: self._change_height(40))
        self.short_btn = _btn("−", "Decrease plot height (40 px)",
                              lambda: self._change_height(-40))
        self.reset_btn = _btn("Reset view",
                              "Restore both axes to the full "
                              "staircase extent.",
                              self.reset_view)

        # Plot + scrollbars in a 2x2 grid:
        #   (0,0) plot                (0,1) vertical scrollbar
        #   (1,0) horizontal scroll   (1,1) <empty>
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.plot, 0, 0)
        grid.addWidget(self.v_scroll, 0, 1)
        grid.addWidget(self.h_scroll, 1, 0)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(0, 1)
        plot_w = QtWidgets.QWidget(); plot_w.setLayout(grid)

        # Plot-control button row — Zoom cluster, Height cluster,
        # Reset. Matches the pulse-pattern preview layout.
        controls_row = QtWidgets.QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 2)
        controls_row.setSpacing(4)
        controls_row.addWidget(QtWidgets.QLabel("Zoom:"))
        controls_row.addWidget(self.x_in_btn)
        controls_row.addWidget(self.x_out_btn)
        controls_row.addWidget(self.y_in_btn)
        controls_row.addWidget(self.y_out_btn)
        controls_row.addSpacing(8)
        controls_row.addWidget(QtWidgets.QLabel("Height:"))
        controls_row.addWidget(self.tall_btn)
        controls_row.addWidget(self.short_btn)
        controls_row.addStretch(1)
        controls_row.addWidget(self.reset_btn)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addWidget(self.header)
        v.addLayout(controls_row)
        v.addWidget(plot_w, stretch=1)

        # Cached data extent so the scrollbars can use it as their
        # outer bound. Updated by set_policy().
        self._x_data = (0.0, 1.0)
        self._y_data = (0.0, 1.0)
        # Pulse-pattern context passed in via :meth:`set_policy`.
        # ``_rate_hz`` drives the TOP-axis tick labels (pulse count =
        # t_s × rate). ``_phase_width_us`` drives the RIGHT-axis tick
        # labels (Q_ph = I_µA · W_µs / 1000 = nC). The staircase
        # itself doesn't care about either for the main render — they
        # only feed the auxiliary axes.
        self._rate_hz: float = 0.0
        self._phase_width_us: float = 0.0
        # Hook the bottom-axis range change so the top axis's
        # pulse-count ticks always reflect the visible time range,
        # and the y-axis range change so the right-axis charge
        # ticks track the visible current range.
        vb_signals = self.plot.getViewBox()
        if vb_signals is not None:
            vb_signals.sigXRangeChanged.connect(
                self._refresh_top_axis_ticks)
            vb_signals.sigYRangeChanged.connect(
                self._refresh_right_axis_ticks)
        self._set_blank()

    # -----------------------------------------------------------------
    def set_grid_visible(self, visible: bool) -> None:
        """Toggle the plot's gridlines. Called by the main
        window's View → Gridlines action so every experiment
        plot's grid flips together."""
        self._grid_visible = bool(visible)
        self.plot.showGrid(x=self._grid_visible, y=self._grid_visible,
                           alpha=0.25 if self._grid_visible else 0.0)

    # -----------------------------------------------------------------
    def sizeHint(self):
        # Match PatternPreview.sizeHint exactly so the two figures
        # behave identically when stacked in a QTabWidget. Width
        # 560 + PREFERRED_HEIGHT is the same default the pattern
        # preview hands the layout; sharing it keeps the figure-
        # switcher tab from reflowing the column on switch.
        return QtCore.QSize(560, self.PREFERRED_HEIGHT)

    # ---- zoom buttons ----------------------------------------------
    def _zoom_axis(self, axis: str, factor: float) -> None:
        """Scale the view range on one axis by ``factor`` around the
        view's current centre. ``factor < 1`` zooms in (range
        shrinks); ``factor > 1`` zooms out. Result clamped to the
        data extent on that axis so the user can't zoom out into
        empty space beyond the rendered staircase. Mirrors the
        identical method on :class:`PatternPreview`."""
        vb = self.plot.getViewBox()
        if vb is None: return
        if axis == 'x':
            r0, r1 = vb.viewRange()[0]
            d0, d1 = self._x_data
        else:
            r0, r1 = vb.viewRange()[1]
            d0, d1 = self._y_data
        c = (r0 + r1) / 2.0
        half = max(1e-6, (r1 - r0) / 2.0 * factor)
        new_lo = max(d0, c - half)
        new_hi = min(d1, c + half)
        if new_hi - new_lo < 1e-6:
            return
        if axis == 'x':
            vb.setXRange(new_lo, new_hi, padding=0)
        else:
            vb.setYRange(new_lo, new_hi, padding=0)
        self._update_scrollbars_from_view()

    # ---- height-step buttons --------------------------------------
    def _set_height(self, new_h: int) -> None:
        """Pin the staircase plot to ``new_h`` pixels (clamped to
        ``[MIN_HEIGHT, MAX_HEIGHT]``). Mirrors
        :meth:`PatternPreview._set_height` so the two figures
        behave identically when the user clicks the ``+`` / ``−``
        height-step buttons."""
        new_h = max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, int(new_h)))
        if new_h == self.height():
            return
        self.setMinimumHeight(new_h)
        self.setMaximumHeight(new_h)
        self.PREFERRED_HEIGHT = new_h   # keep sizeHint coherent
        self.updateGeometry()

    def _change_height(self, delta_px: int) -> None:
        """Resize by ``delta_px`` (used by the +/- height buttons)."""
        self._set_height(self.height() + delta_px)

    # -----------------------------------------------------------------
    def set_policy(self, start_ua: float, step_ua: float,
                   t_step_s: float, max_ua: float,
                   *, rate_hz: float = 0.0,
                   phase_width_us: float = 0.0):
        """Render the staircase for the given ramp parameters.

        ``rate_hz`` and ``phase_width_us`` are the live pulse-pattern
        context from the parent panel. The staircase plot doesn't
        use them for the main curve, but they drive the auxiliary
        axes:

        * ``rate_hz`` × ``time_s`` → cumulative pulse count on the
          TOP x-axis.
        * Left-axis current × ``phase_width_us`` / 1000 →
          ``Charge/Phase`` (nC/ph) on the RIGHT y-axis.

        Defaults of 0 hide / blank the corresponding axis ticks so
        the legacy call signature (without these kwargs) still
        works and gracefully degrades."""
        # Stash the context first; tick refreshes below pick it up.
        self._rate_hz = float(rate_hz) if rate_hz else 0.0
        self._phase_width_us = (float(phase_width_us)
                                if phase_width_us else 0.0)
        if step_ua <= 0 or t_step_s <= 0 or start_ua > max_ua:
            self._set_blank()
            return
        # Build the (t, I) breakpoints. At each step boundary the
        # current jumps to the next level — represented by a duplicate
        # x value so pyqtgraph draws a vertical riser.
        ts: list[float] = [0.0]
        ys: list[float] = [start_ua]
        cur = start_ua
        t = 0.0
        while cur < max_ua - 1e-9:
            t += t_step_s
            ts.extend([t, t])           # vertical riser
            ys.extend([cur, min(cur + step_ua, max_ua)])
            cur = min(cur + step_ua, max_ua)
        # Tail — show one final step's worth of plateau at I_max so
        # the figure ends on a horizontal segment, not at the riser.
        ts.append(t + t_step_s)
        ys.append(cur)
        self.curve.setData(ts, ys)
        n_steps = max(1, int(round((max_ua - start_ua) / step_ua)))
        total_s = n_steps * t_step_s
        self.header.setText(
            f"Start = {start_ua:.1f} {rich.UA} &nbsp;·&nbsp; "
            f"Step = {step_ua:.1f} {rich.UA} &nbsp;·&nbsp; "
            f"<i>I</i><sub>max</sub> = {max_ua:.1f} {rich.UA} "
            f"&nbsp;·&nbsp; "
            f"<b>{n_steps}</b> steps &nbsp;·&nbsp; "
            f"~{total_s:.0f} s total"
        )
        # Cache data extent + clamp ViewBox so the user can't pan into
        # empty space, only navigate within the rendered staircase.
        self._x_data = (0.0, float(ts[-1]))
        self._y_data = (0.0, float(max(ys) * 1.05))
        if vb := self.plot.getViewBox():
            vb.setLimits(xMin=self._x_data[0], xMax=self._x_data[1],
                         yMin=self._y_data[0], yMax=self._y_data[1])
            self.plot.setXRange(*self._x_data, padding=0.02)
            self.plot.setYRange(*self._y_data, padding=0.02)
        self._update_scrollbars_from_view()
        # Recompute the auxiliary axes' tick labels now that the
        # ramp extent is set.
        self._refresh_top_axis_ticks()
        self._refresh_right_axis_ticks()

    # -----------------------------------------------------------------
    def reset_view(self):
        """Restore both axes to the full data extent.

        Pans + zooms the user has applied via the scrollbars / mouse
        wheel are dropped; the view goes back to the framing the
        staircase was rendered with. No-op when the plot is blank.
        """
        if self._x_data[1] <= self._x_data[0]:
            return
        if self._y_data[1] <= self._y_data[0]:
            return
        self.plot.setXRange(*self._x_data, padding=0.02)
        self.plot.setYRange(*self._y_data, padding=0.02)
        self._update_scrollbars_from_view()

    # -----------------------------------------------------------------
    def _set_blank(self):
        self.curve.setData([], [])
        self.header.setText("Set step + max amplitudes above to render the staircase.")
        # Clear the auxiliary axis ticks so a blank state doesn't
        # show stale labels.
        top_axis = self.plot.getPlotItem().getAxis("top")
        if top_axis is not None: top_axis.setTicks(None)
        right_axis = self.plot.getPlotItem().getAxis("right")
        if right_axis is not None: right_axis.setTicks(None)

    # -----------------------------------------------------------------
    def _primary_axis_tick_positions(self, primary: str) -> list:
        """Read the major-tick positions pyqtgraph would place on
        the bottom (``"bottom"``) or left (``"left"``) axis for
        the current view range.

        The auxiliary top / right axes mirror these positions so
        every tick on a paired axis lands at the same screen
        coordinate as its primary — top ticks share x-positions
        with bottom ticks, right ticks share y-positions with
        left ticks. That keeps the figure visually coherent
        (tick marks align in both directions) and means the
        secondary label is always reading the same underlying
        point as the primary, just expressed in different units.

        Returns ``[]`` if the axis or ViewBox isn't ready (called
        before the first layout pass) or if the range is
        degenerate.
        """
        axis = self.plot.getPlotItem().getAxis(primary)
        if axis is None:
            return []
        vb = self.plot.getViewBox()
        if vb is None:
            return []
        if primary == "bottom":
            lo, hi = vb.viewRange()[0]
            size_px = float(axis.size().width())
        else:  # "left"
            lo, hi = vb.viewRange()[1]
            size_px = float(axis.size().height())
        if hi <= lo:
            return []
        # Before the first paint, the axis size can be zero —
        # ``tickValues`` returns nothing in that case. Pass a
        # sane fallback so the auxiliary axis still gets ticks
        # on initial render.
        size_px = max(50.0, size_px)
        layers = axis.tickValues(float(lo), float(hi), size_px)
        if not layers:
            return []
        # ``tickValues`` returns a list of (spacing, [values...])
        # tuples sorted densest-spacing-first. The first entry is
        # the layer pyqtgraph paints with labels — the one we
        # mirror.
        return list(layers[0][1])

    def _refresh_top_axis_ticks(self, *_):
        """Recompute the top axis tick labels — same x-positions
        as the bottom axis's major ticks, each labelled with the
        cumulative pulse count in scientific notation (Unicode-
        superscript exponents). Mirroring bottom-tick positions
        (rather than generating independent evenly-spaced ones)
        keeps top and bottom tick marks visually aligned at
        every zoom level. Called from the bottom-axis
        ``sigXRangeChanged`` signal and from :meth:`set_policy`."""
        top_axis = self.plot.getPlotItem().getAxis("top")
        if top_axis is None:
            return
        if self._rate_hz <= 0:
            top_axis.setTicks(None)
            return
        positions = self._primary_axis_tick_positions("bottom")
        if not positions:
            top_axis.setTicks(None)
            return
        ticks = [(float(x),
                  _format_scientific(max(float(x) * self._rate_hz, 0.0)))
                 for x in positions]
        top_axis.setTicks([ticks])

    def _refresh_right_axis_ticks(self, *_):
        """Recompute the right axis tick labels (Charge/Phase,
        nC/ph) from the same y-positions as the left axis's
        major ticks, scaled by ``phase_width``. Q_ph = I_µA ·
        W_µs / 1000 → nC. Mirroring the left tick positions
        keeps the two y-axes' tick marks aligned. Called from
        the y-axis ``sigYRangeChanged`` signal."""
        right_axis = self.plot.getPlotItem().getAxis("right")
        if right_axis is None:
            return
        if self._phase_width_us <= 0:
            right_axis.setTicks(None)
            return
        positions = self._primary_axis_tick_positions("left")
        if not positions:
            right_axis.setTicks(None)
            return
        ticks = []
        for y in positions:
            q_nc = float(y) * self._phase_width_us / 1000.0
            if abs(q_nc) < 1:
                label = f"{q_nc:.2f}"
            elif abs(q_nc) < 100:
                label = f"{q_nc:.1f}"
            else:
                label = f"{q_nc:.0f}"
            ticks.append((float(y), label))
        right_axis.setTicks([ticks])

    # -------------------- scrollbar synchronisation ------------------
    def _on_view_changed(self, *_):
        if self._suppress_sync:
            return
        self._update_scrollbars_from_view()

    def _on_h_scroll(self, value: int):
        if self._suppress_sync: return
        vb = self.plot.getViewBox()
        if vb is None: return
        x_min, x_max = vb.viewRange()[0]
        width = x_max - x_min
        self._suppress_sync = True
        try:
            vb.setXRange(float(value), float(value) + width, padding=0)
        finally:
            self._suppress_sync = False

    def _on_v_scroll(self, value: int):
        """Vertical scroll: top of the bar = highest y (intuitive for
        a staircase that climbs upward)."""
        if self._suppress_sync: return
        vb = self.plot.getViewBox()
        if vb is None: return
        y_min, y_max = vb.viewRange()[1]
        height = y_max - y_min
        # Translate scrollbar (top-down) into y-axis (bottom-up):
        #   value=min  → window at top of data (highest y)
        #   value=max  → window at bottom (lowest y)
        data_lo, data_hi = self._y_data
        new_top = data_hi - (value - int(data_lo))
        new_bot = new_top - height
        self._suppress_sync = True
        try:
            vb.setYRange(new_bot, new_top, padding=0)
        finally:
            self._suppress_sync = False

    def _update_scrollbars_from_view(self):
        vb = self.plot.getViewBox()
        if vb is None: return
        (x_min, x_max), (y_min, y_max) = vb.viewRange()
        self._suppress_sync = True
        try:
            # Horizontal scrollbar — value = left edge of view in s.
            x_lo = int(self._x_data[0]); x_hi = int(self._x_data[1])
            x_page = max(1, int(x_max - x_min))
            self.h_scroll.setMinimum(x_lo)
            self.h_scroll.setMaximum(max(x_lo, x_hi - x_page))
            self.h_scroll.setPageStep(x_page)
            self.h_scroll.setSingleStep(max(1, x_page // 10))
            self.h_scroll.setValue(int(x_min))
            # Vertical scrollbar — value = (data_hi - top of view)
            y_lo = int(self._y_data[0]); y_hi = int(self._y_data[1])
            y_page = max(1, int(y_max - y_min))
            self.v_scroll.setMinimum(y_lo)
            self.v_scroll.setMaximum(max(y_lo, y_hi - y_page))
            self.v_scroll.setPageStep(y_page)
            self.v_scroll.setSingleStep(max(1, y_page // 10))
            # current "top of view" = y_max; scrollbar value = y_hi - y_max
            self.v_scroll.setValue(int(y_hi - y_max))
        finally:
            self._suppress_sync = False
