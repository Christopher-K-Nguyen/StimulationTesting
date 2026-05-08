"""Real-time stim-pattern preview widget.

Used inside every experiment tab next to the pattern parameter form.
Whenever the user changes any pattern control (amplitude, phase width,
interphase delay, polarity, biphasic/triphasic), the parent tab calls
:meth:`set_pattern` with a freshly built :class:`PulsePattern` and we:

  1. Plot ``I_stim(t)`` for one full pulse, with the discharge interval
     dimmed so the eye picks the active phases first.
  2. Annotate phase widths and amplitudes inline.
  3. Compute charge per phase and *net* charge across the full pulse;
     render a ⚠ warning header if |Q_net| / max(|Q_phase|) > tolerance.

We use pyqtgraph because it's already a runtime dep and its embedded
Qt widget is dramatically faster than a matplotlib FigureCanvas for
the 5–30 Hz UI updates this preview demands.
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtWidgets

from ..waveforms import PulsePattern
from . import rich


# Charge-imbalance tolerance: if |Q_net|/Q_max > this, show a warning.
# 5% mirrors the threshold used by ``checkBalance.m`` in the MATLAB code.
CHARGE_BALANCE_TOL = 0.05


class PatternPreview(QtWidgets.QWidget):
    """Live preview of the stim pulse with charge-balance check."""

    # Cap the preview so it doesn't stretch into a tall figure when the
    # parameters page is given a lot of vertical room. Keep enough height
    # for the curve + per-phase annotations to be readable, but not so
    # much that the actual parameter forms get pushed off-screen.
    MIN_HEIGHT = 170
    MAX_HEIGHT = 240

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMaximumHeight(self.MAX_HEIGHT)
        # Vertical policy = Preferred so the widget hugs its preferred
        # height instead of expanding to fill all available space; the
        # max-height cap above provides the hard ceiling.
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Preferred)

        # ----- header / warning bar -----
        self.header = QtWidgets.QLabel()
        self.header.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.header.setWordWrap(True)
        self.header.setStyleSheet("padding: 4px;")
        # Whether the charge-balance summary should ever appear. The
        # owning panel toggles this whenever the user picks a mode where
        # they have no manual control over balance (symmetric biphasic,
        # triphasic, or auto-adjust on) — the header is moot then.
        self._show_balance: bool = True

        # ----- plot -----
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", f"<i>t</i> [{rich.US}]")
        self.plot.setLabel("left", f"<i>I</i><sub>stim</sub> [{rich.UA}]")
        # The user can scroll horizontally (wheel zoom + drag-to-pan on
        # the x-axis) so they can inspect a single pulse closely or
        # scroll across multiple pulses. The y-axis stays locked because
        # the y-range is tied to the amplitude values; allowing y-zoom
        # would just clip the curve.
        self.plot.setMouseEnabled(x=True, y=False)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        vb = self.plot.getViewBox()
        if vb is not None:
            vb.setMouseEnabled(x=True, y=False)
            vb.setMenuEnabled(False)
            # Re-emit X-range changes so the scroll bar can mirror them.
            vb.sigXRangeChanged.connect(self._on_view_range_changed)
        # y=0 axis marker — solid black so it reads as a definitive
        # zero-current baseline. Width 1 keeps it from competing with
        # the curve. The interpulse-gap line uses a thick *dotted*
        # style instead so the two never look alike.
        self.plot.addLine(y=0, pen=pg.mkPen("#000", width=1,
                                            style=QtCore.Qt.PenStyle.SolidLine))
        self.curve = self.plot.plot(pen=pg.mkPen("#1976d2", width=2))

        # Phase annotation labels — created lazily, max 3 (triphasic)
        self._phase_labels: list[pg.TextItem] = []
        # Dotted segment(s) + label representing the interpulse gap.
        # Two segments are drawn — one BEFORE the current pulse (to show
        # the gap between the previous pulse and this one) and one
        # AFTER (the gap before the next pulse). Stored as a list so
        # they can be cleared together. Created on demand by
        # set_pattern; cleared the same way the phase labels are.
        self._interpulse_curves: list[pg.PlotDataItem] = []
        self._interpulse_label: Optional[pg.TextItem] = None

        # Horizontal scroll bar — synced with the plot's X range so
        # dragging the bar pans the plot and vice-versa. Resolution is
        # in integer microseconds (Qt scrollbars are int-based).
        self.scroll = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal)
        self.scroll.valueChanged.connect(self._on_scroll_changed)
        self._suppress_scroll_sync = False

        # Reset-view button — restores the default zoom (single pulse,
        # 0 → end-of-pulse, no interpulse delay).
        self.reset_btn = QtWidgets.QToolButton()
        self.reset_btn.setText("Reset view")
        self.reset_btn.setToolTip("Restore the default time range "
                                  "(0 → end of one pulse)")
        self.reset_btn.clicked.connect(self.reset_view)

        bottom_row = QtWidgets.QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.addWidget(self.scroll, stretch=1)
        bottom_row.addWidget(self.reset_btn)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addWidget(self.header)
        v.addWidget(self.plot, stretch=1)
        v.addLayout(bottom_row)

        # Cached default x range — set by set_pattern, used by reset_view
        # and the scrollbar synchronisation.
        self._default_x_range: Tuple[float, float] = (0.0, 1.0)
        self._data_x_range: Tuple[float, float] = (0.0, 1.0)

        # Initial blank state
        self._set_header_ok("No pulse pattern set.")

    # -----------------------------------------------------------------
    def set_balance_visible(self, visible: bool):
        """Kept for back-compat. The header is now always visible —
        Q_ph / Q_net / period are always useful info, regardless of
        whether the charge-balance toggle is meaningful in the current
        mode. The warning styling (red bg) is still gated by the
        underlying tolerance check inside set_pattern."""
        self._show_balance = True   # always show
        self.header.setVisible(True)

    def set_pattern(self, pattern: Optional[PulsePattern]):
        """Re-render given a fresh pattern (called every time a control changes)."""
        # Clear any previous annotations
        for lab in self._phase_labels:
            self.plot.removeItem(lab)
        self._phase_labels.clear()
        # Clear interpulse-gap overlay (any previous before/after segments)
        for c in self._interpulse_curves:
            self.plot.removeItem(c)
        self._interpulse_curves.clear()
        if self._interpulse_label is not None:
            self.plot.removeItem(self._interpulse_label)
            self._interpulse_label = None

        if pattern is None or pattern.num_phases == 0:
            self.curve.setData([], [])
            self._set_header_ok("No pulse pattern set.")
            return

        # Render the current pulse plus one before AND one after, with
        # explicit zero-current samples in the interpulse gaps so the
        # baseline drops to 0 µA between pulses (instead of a sloped
        # connector). Each repetition carries pre-pulse and post-pulse
        # zero padding; pyqtgraph then draws a flat y=0 segment across
        # the gap.
        period_us = (1e6 / pattern.rate_hz) if pattern.rate_hz > 0 else 0.0
        if period_us > 0:
            import numpy as _np
            # Half-period worth of dead time padded around each pulse.
            pad_us = max((period_us - pattern.total_pulse_us) / 2.0, 1.0)
            t_one, i_one = pattern.to_timeseries(
                t_pre_us=pad_us, t_post_us=pad_us, sample_period_us=0.5)
            ts = [t_one - period_us, t_one, t_one + period_us]
            ys = [i_one, i_one, i_one]
            t = _np.concatenate(ts); i = _np.concatenate(ys)
            order = _np.argsort(t); t = t[order]; i = i[order]
        else:
            t, i = pattern.to_timeseries(
                t_pre_us=20.0, t_post_us=40.0, sample_period_us=0.5)
        self.curve.setData(t, i)
        # Cache the data extent so the scrollbar can use it as its outer bound.
        self._data_x_range = (float(t[0]), float(t[-1]))
        # Hard-clamp the view to the data range — the user can scroll
        # within [-period .. +period+post] but not beyond. ViewBox's
        # setLimits also stops drag-pans from drifting into empty space.
        vb = self.plot.getViewBox()
        if vb is not None:
            vb.setLimits(xMin=self._data_x_range[0],
                         xMax=self._data_x_range[1])

        # Per-phase annotations:
        #   * Amplitude label sits OUTSIDE the bar (above for positive
        #     amps, below for negative) so it never overlaps the curve.
        #     Just the value, no leading variable name.
        #   * Width label sits at y ≈ 0 just below the axis line.
        #     Same — value only.
        cursor = 0.0
        amp_max_abs = max((abs(p.amplitude_ua) for p in pattern.phases), default=1.0)
        offset = max(amp_max_abs * 0.18, 5.0)
        for n, ph in enumerate(pattern.phases):
            # Amplitude label sits IN LINE with the amplitude — its
            # baseline rests on the horizontal segment at the bar top
            # (or bottom, for negative phases). For a positive amp the
            # label's bottom edge lands at y = amplitude and the text
            # rises above the curve; for a negative amp the label's
            # top edge lands at y = amplitude and the text falls below.
            # Either way, one edge is co-linear with the amplitude line.
            if ph.amplitude_ua >= 0:
                amp_y = ph.amplitude_ua
                amp_anchor = (0.5, 1.0)     # bottom of label sits at amp_y
            else:
                amp_y = ph.amplitude_ua
                amp_anchor = (0.5, 0.0)     # top of label sits at amp_y
            amp_lab = pg.TextItem(
                html=(f"<span style='color:#0d47a1;font-size:9pt;'>"
                      f"<b>{ph.amplitude_ua:+.1f} {rich.UA}</b></span>"),
                anchor=amp_anchor,
            )
            amp_lab.setPos(cursor + ph.width_us / 2, amp_y)
            self.plot.addItem(amp_lab)
            self._phase_labels.append(amp_lab)

            # Width label — at the y=0 axis (just below). Plain value.
            w_lab = pg.TextItem(
                html=(f"<span style='color:#444;font-size:8pt;'>"
                      f"{ph.width_us:.0f} {rich.US}</span>"),
                anchor=(0.5, 0.0),
            )
            w_lab.setPos(cursor + ph.width_us / 2, -offset * 0.4)
            self.plot.addItem(w_lab)
            self._phase_labels.append(w_lab)

            cursor += ph.width_us + ph.delay_after_us

        # Force a y-range that includes the amplitude labels above the
        # bars (and below for negative amps). pyqtgraph's auto-range
        # only counts curve-data extent; the `+offset`-anchored TextItems
        # don't enlarge the bounds, so we have to add headroom by hand.
        # The padding scales with the amp span (so big pulses get a
        # proportional cushion) but is floored at 30 µA — enough for the
        # 9-pt label glyph to clear the plot edge even on high-DPI
        # Windows where rendered text is larger than its nominal pt size.
        amp_min = min(0, *(p.amplitude_ua for p in pattern.phases))
        amp_max = max(0, *(p.amplitude_ua for p in pattern.phases))
        amp_span = max(amp_max - amp_min, 1.0)
        y_pad = max(amp_span * 0.6, 30.0)
        if vb is not None:
            vb.enableAutoRange(y=False)
        self.plot.setYRange(amp_min - y_pad, amp_max + y_pad, padding=0)

        # ---- period / interpulse-delay overlay ----
        # Period (one pulse + dead time) = 1e6 / rate. The interpulse
        # gap is what's left after the actual pulse ends. Drawn as a
        # dashed segment at y=0 so the user can see where the next
        # pulse will start. ``period_us`` is referenced again below in
        # the summary line, so it's computed here unconditionally.
        period_us = (1e6 / pattern.rate_hz) if pattern.rate_hz > 0 else 0.0
        interpulse_us = max(period_us - pattern.total_pulse_us, 0.0)
        if interpulse_us > 0:
            # Thick *dotted* line so the interpulse gap reads clearly
            # against the gridlines and against the solid y=0 axis
            # marker (so the two never look identical). 5 px wide is
            # the minimum that keeps the round dots visible at typical
            # Windows DPI scaling.
            ipulse_pen = pg.mkPen("#666", width=5,
                                  style=QtCore.Qt.PenStyle.DotLine)
            # Two segments — one to the left of t=0 (gap from the
            # previous pulse) and one to the right of t=total_pulse
            # (gap before the next pulse). Drawing both makes the
            # cadence visible regardless of how the user pans the view.
            for x_gap in (
                [-interpulse_us, 0.0],
                [pattern.total_pulse_us,
                 pattern.total_pulse_us + interpulse_us],
            ):
                self._interpulse_curves.append(
                    self.plot.plot(x_gap, [0.0, 0.0], pen=ipulse_pen)
                )
            self._interpulse_label = pg.TextItem(
                html=f"<span style='color:#555;font-size:8pt;'>"
                     f"interpulse {rich.DELTA_BIG}<i>t</i> "
                     f"= {interpulse_us:.0f} {rich.US}</span>",
                anchor=(0.5, 0),
            )
            ymin_local = min(0, *(p.amplitude_ua for p in pattern.phases))
            self._interpulse_label.setPos(
                pattern.total_pulse_us + interpulse_us / 2,
                ymin_local * 1.15 - 1,
            )
            self.plot.addItem(self._interpulse_label)

        # ---- charge balance ----
        q_phase_nc = [ph.charge_nc for ph in pattern.phases]
        q_net_nc = float(sum(q_phase_nc))
        q_max_nc = max(abs(q) for q in q_phase_nc) if q_phase_nc else 0.0
        ratio = abs(q_net_nc) / q_max_nc if q_max_nc > 0 else 0.0

        # Render summary line — bold green if balanced, red+⚠ if not
        summary = (
            f"{rich.var('Q', 'ph')} = "
            + " | ".join(f"{q:+.1f} {rich.NC}" for q in q_phase_nc)
            + f" &nbsp;&nbsp; <b>{rich.var('Q', 'net')}</b> = {q_net_nc:+.2f} {rich.NC}"
            + f" &nbsp;&nbsp; rate = {pattern.rate_hz:g} {rich.PPS}"
            + f" &nbsp;&nbsp; pulse = {pattern.total_pulse_us:.0f} {rich.US}"
            + f" &nbsp;&nbsp; period = {period_us:.0f} {rich.US}"
        )

        # Always display the per-phase charges, net charge, and period.
        # Only the colour treatment differs: green when balanced, red
        # when |Q_net| / Q_max exceeds the tolerance. No bullet glyph
        # in front of either message.
        if q_max_nc == 0 or ratio <= CHARGE_BALANCE_TOL:
            self._set_header_ok(summary)
        else:
            # Pre-pend a warning glyph + the word WARNING so the red
            # band reads unambiguously as "you must address this"
            # before the user clicks Start. The detail row keeps the
            # exact ratio + tolerance so they can see how far past the
            # band they are.
            self._set_header_warn(
                f"⚠ <b>WARNING — charge imbalance</b> "
                f"(|{rich.var('Q', 'net')}|/{rich.var('Q', 'max')} "
                f"= {ratio*100:.1f}%, tol {CHARGE_BALANCE_TOL*100:.0f}%)"
                f"<br>{summary}"
            )

        # ---- default view: 0 → end-of-pulse (no interpulse delay) ----
        # The user can pan / wheel-zoom, and clicking Reset View comes
        # back to this framing.
        self._default_x_range = (0.0, float(pattern.total_pulse_us))
        self.plot.setXRange(*self._default_x_range, padding=0.02)
        self._update_scrollbar_from_view()

    # -----------------------------------------------------------------
    def reset_view(self):
        """Restore the default framing — single pulse, no interpulse delay."""
        if self._default_x_range[1] > self._default_x_range[0]:
            self.plot.setXRange(*self._default_x_range, padding=0.02)
            self._update_scrollbar_from_view()

    # ---- scrollbar synchronisation ----------------------------------
    def _on_scroll_changed(self, value: int):
        """Scrollbar moved → pan the plot."""
        if self._suppress_scroll_sync:
            return
        vb = self.plot.getViewBox()
        if vb is None: return
        x_min, x_max = vb.viewRange()[0]
        width = x_max - x_min
        new_min = float(value)
        new_max = new_min + width
        self._suppress_scroll_sync = True
        try:
            vb.setXRange(new_min, new_max, padding=0)
        finally:
            self._suppress_scroll_sync = False

    def _on_view_range_changed(self, _vb=None, _range=None):
        """Plot view changed (mouse pan / zoom) → update the scrollbar."""
        if self._suppress_scroll_sync:
            return
        self._update_scrollbar_from_view()

    def _update_scrollbar_from_view(self):
        """Reflect the plot's X range in the scrollbar's value/page/range."""
        vb = self.plot.getViewBox()
        if vb is None: return
        x_min, x_max = vb.viewRange()[0]
        width = max(x_max - x_min, 1.0)
        data_min, data_max = self._data_x_range
        # Bounds are pinned to the data extent — the user shouldn't
        # be able to scroll into empty space beyond the rendered
        # before/current/after pulse triplet.
        scrollbar_min = int(data_min)
        scrollbar_max = int(data_max)
        page_step = max(1, int(width))
        # The scrollbar's value is the LEFT edge of the visible window;
        # its max is data_max - page_step.
        self._suppress_scroll_sync = True
        try:
            self.scroll.setMinimum(scrollbar_min)
            self.scroll.setMaximum(max(scrollbar_min, scrollbar_max - page_step))
            self.scroll.setPageStep(page_step)
            self.scroll.setSingleStep(max(1, page_step // 10))
            self.scroll.setValue(int(x_min))
        finally:
            self._suppress_scroll_sync = False

    # -----------------------------------------------------------------
    def _set_header_ok(self, html: str):
        # Higher-contrast green: dark text on a brighter wash.
        self.header.setStyleSheet(
            "padding: 4px; background-color: #c8e6c9; "
            "border: 1px solid #66bb6a; border-radius: 3px; "
            "color: #1b5e20; font-weight: bold;"
        )
        self.header.setText(html)
        self.header.setVisible(self._show_balance)

    def _set_header_warn(self, html: str):
        # Stronger red — saturated background, white text — so a missed
        # imbalance can't get lost against a similarly-coloured plot bg.
        self.header.setStyleSheet(
            "padding: 4px; background-color: #c62828; "
            "border: 1px solid #b71c1c; border-radius: 3px; "
            "color: white; font-weight: bold;"
        )
        self.header.setText(html)
        self.header.setVisible(self._show_balance)
