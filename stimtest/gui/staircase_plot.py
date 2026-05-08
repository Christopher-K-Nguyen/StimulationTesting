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
from PyQt6 import QtCore, QtWidgets

from . import rich


class StaircasePlot(QtWidgets.QWidget):
    """I_stim vs time staircase for Progressive Stress."""

    MIN_HEIGHT = 170
    MAX_HEIGHT = 240

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setMaximumHeight(self.MAX_HEIGHT)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Preferred)

        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", f"<i>t</i> [s]")
        self.plot.setLabel("left", f"<i>I</i><sub>stim</sub> [{rich.UA}]")
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
        self.header.setStyleSheet(
            "padding: 4px; background-color: #ffe0b2; "
            "border: 1px solid #ffb74d; border-radius: 3px; "
            "color: #4e342e; font-weight: bold;"
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

        # Reset-view button — restores both axes to the full data
        # extent (0 → end of staircase, 0 → max amplitude). Mirrors
        # the same button on the pulse-pattern preview so the two
        # plots feel symmetric. Sits in the bottom-right corner of
        # the grid, replacing the empty spacer that was there.
        self.reset_btn = QtWidgets.QToolButton()
        self.reset_btn.setText("Reset view")
        self.reset_btn.setToolTip("Restore both axes to the full "
                                  "staircase extent.")
        self.reset_btn.clicked.connect(self.reset_view)

        # Layout: [ plot | v_scroll ]   above   [ h_scroll | reset ]
        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(0)
        grid.addWidget(self.plot, 0, 0)
        grid.addWidget(self.v_scroll, 0, 1)
        grid.addWidget(self.h_scroll, 1, 0)
        grid.addWidget(self.reset_btn, 1, 1)
        plot_w = QtWidgets.QWidget(); plot_w.setLayout(grid)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addWidget(self.header)
        v.addWidget(plot_w, stretch=1)

        # Cached data extent so the scrollbars can use it as their
        # outer bound. Updated by set_policy().
        self._x_data = (0.0, 1.0)
        self._y_data = (0.0, 1.0)
        self._set_blank()

    # -----------------------------------------------------------------
    def set_policy(self, start_ua: float, step_ua: float,
                   t_step_s: float, max_ua: float):
        """Render the staircase for the given ramp parameters."""
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
