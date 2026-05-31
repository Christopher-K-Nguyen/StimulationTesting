"""Reusable Qt widgets used by the experiment tabs."""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets


def _round_sig(x: float, sig: int = 1) -> float:
    """Round ``x`` to ``sig`` significant figures.

    Mirrors the MATLAB ``round(x, sig, 'significant')`` call the
    calibration plot uses to compute its X-axis bounds (see
    :func:`stimtest.gui.calibration._round_sig`).  Keeping the two
    implementations identical means the experiment plot and the
    calibration plot frame the same captured waveform with the same
    rounded bounds, instead of one showing ``-123.5 µs … 478.3 µs``
    while the other shows ``-100 µs … 500 µs``.
    """
    if x == 0.0:
        return 0.0
    d = math.ceil(math.log10(abs(x)))
    factor = 10 ** (sig - d)
    return round(x * factor) / factor


def _matlab_nice_tick_step(span: float, target_count: int = 5) -> float:
    """Pick a tick spacing using MATLAB's "nice multiples" algorithm.

    pyqtgraph's default tick algorithm targets ~10 major ticks per
    axis; MATLAB targets ~5 and rounds the step to a multiple of
    ``{1, 2, 2.5, 5} × 10ⁿ``.  For a 0-2000 µs range the difference
    is stark:

      * pyqtgraph: step = 200 → ticks at 0, 200, 400, …, 2000
        (11 ticks — busy)
      * MATLAB:    step = 500 → ticks at 0, 500, 1000, 1500, 2000
        (5 ticks — clean)

    The experiment plot used pyqtgraph's default before; the
    operator (who reads MATLAB-style plots all day) found the dense
    200-step layout unreadable.  This algorithm produces MATLAB-
    equivalent steps without re-implementing the whole tick engine.

    Returns the chosen step (in axis units).  Returns 0 for a
    degenerate input span ≤ 0 so the caller can fall back to
    pyqtgraph's default.
    """
    span = float(span)
    if span <= 0.0 or not math.isfinite(span):
        return 0.0
    n = max(int(target_count), 1)
    # Rough step from "I want N ticks across span" — refined to the
    # next "nice" multiple below.
    rough = span / n
    # Decompose into mantissa × 10^exponent.
    exp = math.floor(math.log10(rough))
    mantissa = rough / (10 ** exp)
    # Round mantissa UP to the next "nice" value.  MATLAB's set is
    # {1, 2, 2.5, 5, 10}; the 10 case bumps the exponent by one and
    # restarts at mantissa 1.
    for nice in (1.0, 2.0, 2.5, 5.0, 10.0):
        if mantissa <= nice:
            mantissa = nice
            break
    return mantissa * (10 ** exp)


def _matlab_major_tick_values(min_val: float, max_val: float,
                              target_count: int = 5) -> List[float]:
    """Return tick positions across ``[min_val, max_val]`` using the
    MATLAB nice-multiples spacing from :func:`_matlab_nice_tick_step`.

    First tick is the largest multiple of the step ≤ ``min_val``;
    last is the smallest ≥ ``max_val``.  Both endpoints land on the
    grid when ``min_val`` / ``max_val`` are already on it, which is
    the common case because :func:`_round_sig` is applied to the
    bounds before this function gets called.
    """
    step = _matlab_nice_tick_step(max_val - min_val, target_count)
    if step <= 0.0:
        return []
    # Snap start to the largest multiple ≤ min_val to keep the grid
    # locked to round numbers (otherwise ticks slide off-pixel when
    # the user pans / zooms slightly).
    start = math.floor(min_val / step) * step
    out = []
    # Tiny epsilon so a tick that falls AT max_val isn't dropped by
    # floating-point < comparison.
    eps = step * 1e-9
    k = 0
    while True:
        t = start + k * step
        if t > max_val + eps:
            break
        if t >= min_val - eps:
            out.append(round(t, 12))   # tame fp noise on display
        k += 1
        # Safety cap — should never fire given the step algorithm
        # caps at ~10x target_count ticks, but defends against
        # pathological floats.
        if k > 1000:
            break
    return out


def _make_matlab_tick_override(orig_tickValues, target_count: int = 5):
    """Build a ``tickValues``-compatible callable that returns the
    MATLAB-style major level INSTEAD of pyqtgraph's denser level 0.

    pyqtgraph's ``AxisItem.tickValues(minVal, maxVal, size)`` returns
    a list of ``(spacing, [tick_positions, …])`` tuples — one per
    "level" (major / minor / sub-minor).  This wrapper REPLACES the
    first (major) level with the MATLAB-computed positions and
    drops every subsequent level (no minor / sub-minor ticks — same
    as the previous "major-only" override but with the better step
    selector applied).

    Returns a function compatible with ``AxisItem.tickValues``.
    """
    def _matlab_tick_values(minVal, maxVal, size, _orig=orig_tickValues,
                            _n=target_count):
        try:
            ticks = _matlab_major_tick_values(
                float(minVal), float(maxVal), _n)
        except Exception:
            ticks = []
        if not ticks:
            # Degenerate range or numerics blew up — fall back to
            # pyqtgraph's default level 0 so we never return an
            # empty tick list and leave the axis blank.
            levels = _orig(minVal, maxVal, size)
            return levels[:1] if levels else levels
        step = _matlab_nice_tick_step(maxVal - minVal, _n)
        return [(step, ticks)]
    return _matlab_tick_values

try:
    import pyqtgraph as pg
    HAS_PYQTGRAPH = True
except Exception:
    HAS_PYQTGRAPH = False

from ..electrode import ElectrodeArray
from ..session import Capture
from . import rich


# ---------------------------------------------------------------------------
# QTabWidget wheel-scroll suppression
# ---------------------------------------------------------------------------
class _SwallowWheelFilter(QtCore.QObject):
    """Event filter that eats QWheelEvent on whichever object it's
    installed on.

    Used by :func:`disable_tabbar_wheel_scroll` to prevent
    ``QTabWidget`` from changing the active tab on mouse-wheel
    scroll — surprising default behaviour that fires accidentally
    when the user is just scrolling a long form on the same page.
    The default focus-keyboard-arrows path (Ctrl + Tab, etc.) is
    untouched.
    """

    def eventFilter(self, obj, event):
        if event is not None and event.type() == QtCore.QEvent.Type.Wheel:
            return True   # eaten — event won't reach the tab bar
        return False


def disable_tabbar_wheel_scroll(tab_widget: QtWidgets.QTabWidget) -> None:
    """Install a wheel-swallow event filter on ``tab_widget``'s tab
    bar so mouse-wheel rotation no longer flips to the next/previous
    tab.

    The filter instance is pinned as an attribute on the tab widget
    so it survives garbage collection. A module-level singleton
    would tempt cross-test races where the C++ side of the QObject
    is destroyed (e.g. when the QApplication is torn down between
    tests) but Python still holds the wrapper, leading to "wrapped
    C/C++ object has been deleted" on the next test's
    ``installEventFilter`` call.
    """
    bar = tab_widget.tabBar()
    if bar is None:
        return
    # Reuse the existing filter if one is already attached (idempotent).
    flt = getattr(tab_widget, "_swallow_wheel_filter", None)
    if flt is None or not isinstance(flt, _SwallowWheelFilter):
        flt = _SwallowWheelFilter(tab_widget)
        tab_widget._swallow_wheel_filter = flt
    bar.installEventFilter(flt)


# ---------------------------------------------------------------------------
# Spreadsheet copy / cut / paste support for QTableWidget
# ---------------------------------------------------------------------------
class _SpreadsheetClipboardFilter(QtCore.QObject):
    """Event filter that adds Ctrl+C / Ctrl+X / Ctrl+V to a ``QTableWidget``.

    Mirrors the spreadsheet convention so users can copy a block of
    cells out to Excel / Google Sheets and paste blocks back in:

    * **Ctrl+C** — copies the selected rectangle as TSV (tab between
      cells, newline between rows). Empty cells become empty fields,
      so a partial paste back round-trips losslessly.
    * **Ctrl+X** — copy then clear the selected cells.
    * **Ctrl+V** — paste TSV / CSV from the clipboard. The top-left
      cell of the current selection is the paste anchor; rows/cols
      that overflow the table are silently truncated. Cells outside
      the table's cleared range remain untouched.

    Installed via :func:`enable_spreadsheet_paste`. The filter is
    parented to the table so it dies cleanly when the table is
    destroyed; you don't need to track or disable it manually.
    """

    def eventFilter(self, obj, ev):
        if ev.type() != QtCore.QEvent.Type.KeyPress:
            return False
        if not isinstance(obj, QtWidgets.QTableWidget):
            return False
        # Ignore key strokes while a cell is in edit mode — Qt's
        # built-in editor handles its own clipboard interactions
        # (single-cell paste into the current line edit).
        if obj.state() == QtWidgets.QAbstractItemView.State.EditingState:
            return False
        ks = ev.keyCombination().toCombined() if hasattr(ev, "keyCombination") else (
            int(ev.modifiers()) | int(ev.key()))
        is_copy = ev.matches(QtGui.QKeySequence.StandardKey.Copy)
        is_cut  = ev.matches(QtGui.QKeySequence.StandardKey.Cut)
        is_paste = ev.matches(QtGui.QKeySequence.StandardKey.Paste)
        if is_copy or is_cut:
            self._copy_selection(obj, clear=is_cut)
            return True
        if is_paste:
            self._paste_selection(obj)
            return True
        return False

    @staticmethod
    def _copy_selection(table: QtWidgets.QTableWidget, *, clear: bool) -> None:
        """Copy the selected rectangle to the clipboard as TSV.

        ``clear=True`` also empties the cells (used by Cut). When the
        selection spans a non-rectangular shape (Qt allows multi-
        select), this flattens to the bounding rectangle and emits
        empty strings for cells the user didn't actually pick.
        """
        ranges = table.selectedRanges()
        if not ranges:
            return
        # Walk all selected ranges to find the bounding rectangle; copy
        # every cell whose (r, c) is inside ANY selected range. This
        # handles disjoint selections by flattening to a single block
        # whose unselected cells are blank.
        r0 = min(r.topRow() for r in ranges)
        r1 = max(r.bottomRow() for r in ranges)
        c0 = min(r.leftColumn() for r in ranges)
        c1 = max(r.rightColumn() for r in ranges)
        selected_cells = set()
        for rng in ranges:
            for rr in range(rng.topRow(), rng.bottomRow() + 1):
                for cc in range(rng.leftColumn(), rng.rightColumn() + 1):
                    selected_cells.add((rr, cc))
        rows: List[str] = []
        for r in range(r0, r1 + 1):
            cells = []
            for c in range(c0, c1 + 1):
                if (r, c) in selected_cells:
                    item = table.item(r, c)
                    text = "" if item is None else item.text()
                    # Strip the placeholder "—" that the device-mapping
                    # table uses for empty cells so a paste round-trip
                    # doesn't propagate Unicode dashes into spreadsheets.
                    cells.append("" if text == "—" else text)
                else:
                    cells.append("")
            rows.append("\t".join(cells))
        QtWidgets.QApplication.clipboard().setText("\n".join(rows))
        if clear:
            for (r, c) in selected_cells:
                if r >= table.rowCount() or c >= table.columnCount():
                    continue
                item = table.item(r, c)
                if item is not None:
                    item.setText("")

    @staticmethod
    def _paste_selection(table: QtWidgets.QTableWidget) -> None:
        """Paste TSV / CSV from the clipboard at the current anchor.

        The anchor is the top-left of the current selection (or the
        ``currentRow()`` / ``currentColumn()`` if nothing is
        selected). Tab is the preferred separator (Excel / Google
        Sheets default); commas are accepted as a fallback so users
        can paste from a CSV column. Newline-separated rows.
        """
        text = QtWidgets.QApplication.clipboard().text()
        if not text:
            return
        # Choose the separator: tab if it appears anywhere, else comma.
        # Excel / Google Sheets use tab; CSV-from-elsewhere uses comma.
        sep = "\t" if "\t" in text else (","  if "," in text else None)
        rows: List[List[str]] = []
        for line in text.splitlines():
            if not line:
                continue
            rows.append(line.split(sep) if sep else [line])
        if not rows:
            return
        # Anchor = top-left of the current selection, or current cell.
        ranges = table.selectedRanges()
        if ranges:
            r0 = min(r.topRow() for r in ranges)
            c0 = min(r.leftColumn() for r in ranges)
        else:
            r0 = max(0, table.currentRow())
            c0 = max(0, table.currentColumn())
        # Clip to the table's bounds — never grow the table on paste,
        # the user resizes it via the rows/cols spinners explicitly.
        n_rows = table.rowCount()
        n_cols = table.columnCount()
        for dr, line in enumerate(rows):
            r = r0 + dr
            if r >= n_rows:
                break
            for dc, value in enumerate(line):
                c = c0 + dc
                if c >= n_cols:
                    break
                item = table.item(r, c)
                if item is None:
                    item = QtWidgets.QTableWidgetItem()
                    table.setItem(r, c, item)
                item.setText(value.strip())


def enable_spreadsheet_paste(table: QtWidgets.QTableWidget) -> None:
    """Install copy / cut / paste support on ``table``.

    Adds Ctrl+C, Ctrl+X, Ctrl+V handlers that read / write tab-
    separated text on the clipboard, matching the convention every
    spreadsheet application uses. The table itself stays
    cell-editable for direct typing — paste only fires when the
    table has focus and is NOT in edit mode (so an in-progress
    cell-editor's own paste isn't intercepted).

    Call this once after constructing the table; the filter is
    parented to ``table`` so it cleans up automatically.
    """
    flt = _SpreadsheetClipboardFilter(table)
    table.installEventFilter(flt)


# ---------------------------------------------------------------------------
# Live oscilloscope plot
# ---------------------------------------------------------------------------
AXIS_LEFT = "left"
AXIS_RIGHT = "right"
#: Hidden — used when a trace is unmapped or the user picked "N/A"
#: from the per-trace dropdown. The plot skips drawing it on either
#: axis (and it doesn't appear in the legend or the inset).
AXIS_NA = "na"


class ScopePlot(QtWidgets.QWidget):
    """Multi-trace scope view (pyqtgraph if available, else QPainter fallback).

    Supports two Y axes — left and right. Each curve is assigned via
    the ``axis`` argument to :meth:`set_traces` (default ``"left"``).
    The right axis is implemented as a second :class:`pg.ViewBox`
    linked to the main plot's X axis so the two share a common time
    base; resize / pan / zoom of the X axis stays synchronised.

    A small inset plot (hidden by default) sits below the main view.
    Toggle it via :meth:`set_inset_visible`; pick which traces it
    mirrors via :meth:`set_inset_traces`. The inset shares the main
    plot's X range (also via :class:`pg.ViewBox` linking) so panning
    one pans the other.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        # Curve registry — name → PlotDataItem; axis registry — name →
        # AXIS_LEFT / AXIS_RIGHT. These survive ``clear()`` so a
        # set_traces with a previously-seen curve name reuses the
        # existing PlotDataItem (avoids legend churn).
        self._curves: Dict[str, "pg.PlotDataItem"] = {}
        self._curve_axis: Dict[str, str] = {}
        self._curve_color: Dict[str, str] = {}
        self._curve_data: Dict[str, tuple] = {}   # name → (time_us, y)
        self._inset_curves: Dict[str, "pg.PlotDataItem"] = {}
        self._inset_visible = False
        self._inset_trace_names: set = set()
        if HAS_PYQTGRAPH:
            pg.setConfigOptions(antialias=True, background="w", foreground="k")
            # ---------- main plot with dual Y axes ----------
            self._plot = pg.PlotWidget()
            # Gridlines default OFF on experiment plots — toggled
            # globally by the main window's View → Gridlines action
            # via :meth:`set_grid_visible`.
            self._grid_visible = False
            self._plot.showGrid(x=False, y=False)
            # Axis lines and tick numbers stay BLACK (pyqtgraph
            # default).  Trace colour identity lives in the legend
            # and in the trace pens; tinting the axes was tried
            # briefly and got confusing when the operator remapped a
            # trace to the other axis via the dropdown.
            self._plot.setLabel("bottom", "Time [µs]")
            # Inline ``Name [unit]`` format on every axis.  pyqtgraph's
            # ``units=`` kwarg renders as ``"Voltage (V)"`` with the
            # SI-prefix machinery; we want fixed square brackets
            # ``[V]`` so the unit stays put regardless of zoom.  The
            # right-axis label grows a single-line conversion hint
            # at runtime when an electrode area is configured (see
            # :meth:`set_axis_labels` callers in multichannel_scope).
            self._plot.setLabel("left", "Voltage [V]")
            # Styled legend — white background with light-grey border,
            # matching the calibration plot.  pyqtgraph's default is
            # an unstyled overlay that the curves can wash out.  The
            # legend carries the trace-colour identity:
            #   * Voltage          — yellow  (#E6B800)
            #   * Current /        — cyan    (#00B4C8)
            #     Current Density
            #   * Active Potential — green   (#009E73)
            #   * Return Potential — red     (#D55E00)
            # All four are from Wong's colourblind-safe palette (see
            # ``TRACE_COLOURS`` in multichannel_scope.py for the
            # canonical mapping).
            self._legend = self._plot.addLegend(
                offset=(10, 10),
                brush=pg.mkBrush(255, 255, 255, 200),
                pen=pg.mkPen("#cccccc"),
            )
            # Right-axis ViewBox — linked to the main viewbox's X so
            # zoom / pan stays in sync, but its Y is independent. Auto
            # resizes to match the main viewbox's geometry.
            self._right_vb = pg.ViewBox()
            self._plot.showAxis("right")
            self._plot.scene().addItem(self._right_vb)
            self._plot.getAxis("right").linkToView(self._right_vb)
            self._right_vb.setXLink(self._plot.plotItem.vb)
            self._plot.setLabel("right", "Current [µA]")
            self._plot.plotItem.vb.sigResized.connect(self._sync_right_geometry)
            # Disable pyqtgraph's auto-SI-prefix on every axis.
            # Without this, pyqtgraph rescales the tick numbers when
            # the visible range is much smaller than the label's
            # nominal unit — e.g. a V_mon trace at ±0.1 V renders
            # as ``±100`` on the tick column with ``(×0.001)`` appended
            # to the axis label.  The operator sees both ``[V]`` AND
            # ``×0.001`` and has to do mental arithmetic to recover
            # the real values.  Forcing ``enableAutoSIPrefix(False)``
            # keeps the tick numbers in the same units the label
            # advertises (V on the left, µA on the right, µs on the
            # bottom).
            for _ax_name in ("bottom", "left", "right"):
                try:
                    self._plot.getAxis(_ax_name).enableAutoSIPrefix(False)
                except Exception:
                    pass
            # Major-only ticks on every axis.  pyqtgraph's default
            # renders three tick levels (major + minor + sub-minor),
            # producing labels like ``-123.5, -100, -76.5, -50, -23.5,
            # 0, 23.5 …`` once the auto-range picks a non-round window.
            # We expose ONLY the major level so the operator sees a
            # clean, proportional set of integer-friendly tick numbers
            # (``-200, -100, 0, 100, 200`` and so on).  Same trick the
            # calibration plot uses.
            # Use the MATLAB-style "nice multiples" tick step (5 ticks,
            # step from {1, 2, 2.5, 5}×10ⁿ) instead of pyqtgraph's
            # default level-0 step (~10 ticks).  Without this, a
            # 0-2000 µs trace gets ticks every 200 µs (11 ticks —
            # busy); MATLAB picks every 500 µs (5 ticks — clean) and
            # the operator (who reads MATLAB plots all day) found the
            # dense layout unreadable.  Same algorithm applied to the
            # left + right axes so voltage / current density ticks are
            # equally clean.
            for _ax_name in ("bottom", "left", "right"):
                try:
                    _ax = self._plot.getAxis(_ax_name)
                    _ax.tickValues = _make_matlab_tick_override(
                        _ax.tickValues, target_count=5)
                except Exception:
                    pass
            layout.addWidget(self._plot, stretch=1)
            # ---------- inset plot (hidden by default) ----------
            self._inset = pg.PlotWidget()
            self._inset.setVisible(False)
            self._inset.setMinimumHeight(80)
            self._inset.setMaximumHeight(220)
            self._inset.showGrid(x=False, y=False)
            # IMPORTANT: use the bracket-style label ``Time [µs]`` —
            # NOT ``setLabel(..., units="µs")``.  Passing ``units=``
            # hands pyqtgraph the unit, which then engages the auto-
            # SI-prefix scaler (``Time (kµs)`` with ``×1000`` on small
            # ranges).  The main plot above uses the bracket form for
            # exactly this reason; the inset was missing it, so the
            # operator saw ``Time (kµs)`` / ``(×0.001)`` on the inset
            # while the main plot above read ``Time [µs]`` cleanly.
            self._inset.setLabel("bottom", "Time [µs]")
            self._inset.setLabel("left", "Inset")
            # Disable the auto-SI-prefix on every inset axis — same
            # treatment as the main plot above (see the ``for _ax_name``
            # block).  Without this, pyqtgraph rescales the tick
            # numbers (and appends ``(×0.001)`` to the axis label)
            # when the visible y-range is much smaller than 1 V, which
            # is the common case on E_ret traces (~5-10 mV swings).
            for _ax_name in ("bottom", "left"):
                try:
                    self._inset.getAxis(_ax_name).enableAutoSIPrefix(False)
                except Exception:
                    pass
            # MATLAB-style nice ticks on the inset too — same target
            # count as the main plot so the two read consistently.
            for _ax_name in ("bottom", "left"):
                try:
                    _iax = self._inset.getAxis(_ax_name)
                    _iax.tickValues = _make_matlab_tick_override(
                        _iax.tickValues, target_count=5)
                except Exception:
                    pass
            self._inset.plotItem.vb.setXLink(self._plot.plotItem.vb)
            layout.addWidget(self._inset, stretch=0)
        else:
            label = QtWidgets.QLabel("pyqtgraph not installed; install for live plots.")
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
            self._plot = None
            self._right_vb = None
            self._inset = None

    # ----------------------------------------------------------- internals
    def _sync_right_geometry(self):
        """Keep the right-axis ViewBox glued to the main viewbox.

        ``sigResized`` fires when the user drags the window or the
        embedding splitter; we mirror the geometry so right-axis
        curves stay aligned with their left-axis siblings.
        """
        if self._plot is None or self._right_vb is None:
            return
        vb = self._plot.plotItem.vb
        self._right_vb.setGeometry(vb.sceneBoundingRect())
        self._right_vb.linkedViewChanged(vb, self._right_vb.XAxis)

    # ----------------------------------------------------------- public API
    def set_grid_visible(self, visible: bool) -> None:
        """Toggle gridlines on the main scope view and its inset.
        Called by the main window's View → Gridlines action so
        every experiment plot's grid flips together. No-op when
        pyqtgraph isn't available."""
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        self._grid_visible = bool(visible)
        alpha = 0.3 if self._grid_visible else 0.0
        self._plot.showGrid(x=self._grid_visible, y=self._grid_visible,
                            alpha=alpha)
        if self._inset is not None:
            self._inset.showGrid(x=self._grid_visible,
                                 y=self._grid_visible,
                                 alpha=0.25 if self._grid_visible else 0.0)

    def clear(self):
        """Drop every plotted curve from both axes and the inset."""
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        # Remove from whichever axis owns the curve.
        for name, curve in list(self._curves.items()):
            if self._curve_axis.get(name) == AXIS_RIGHT:
                self._right_vb.removeItem(curve)
            else:
                self._plot.removeItem(curve)
            if self._legend is not None:
                try:
                    self._legend.removeItem(curve)
                except Exception:
                    pass
        for curve in self._inset_curves.values():
            self._inset.removeItem(curve)
        self._curves.clear()
        self._curve_axis.clear()
        self._curve_color.clear()
        self._curve_data.clear()
        self._inset_curves.clear()

    def set_traces(self, time_us: np.ndarray,
                   traces: Dict[str, np.ndarray],
                   colors: Optional[Dict[str, str]] = None,
                   axis: Optional[Dict[str, str]] = None,
                   *, remove_missing: bool = False):
        """Plot or update each trace; assign each to a Y axis.

        ``axis[name]`` selects ``"left"`` or ``"right"`` for that
        trace; missing keys default to ``"left"``. When a previously-
        plotted curve switches axis between calls, it's smoothly
        re-parented (no flicker) — same for color and data.

        ``remove_missing=True`` (opt-in) also drops any currently
        plotted curve whose name is NOT in ``traces`` — turning
        :meth:`set_traces` into the declarative "this is the complete
        set of traces I want now" operation that the multichannel
        scope view needs.  Replaces the previous ``clear() + set_traces``
        pattern: instead of destroying every PlotDataItem and re-
        creating them every capture (O(N) re-allocations), we only
        touch the curves that actually change (typically zero on a
        steady-state visibility set, just `setData` on existing curves).
        Per-capture cost drops from ~3-5 ms to <0.5 ms.
        """
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        colors = colors or {}
        axis = axis or {}
        if remove_missing:
            stale = [name for name in self._curves if name not in traces]
            for name in stale:
                curve = self._curves.pop(name, None)
                if curve is None:
                    continue
                if self._curve_axis.get(name) == AXIS_RIGHT:
                    self._right_vb.removeItem(curve)
                else:
                    self._plot.removeItem(curve)
                if self._legend is not None:
                    try:
                        self._legend.removeItem(curve)
                    except Exception:
                        pass
                self._curve_axis.pop(name, None)
                self._curve_color.pop(name, None)
                self._curve_data.pop(name, None)
                # Inset curve mirrors the main one by name; drop too.
                _inset_curve = self._inset_curves.pop(name, None)
                if _inset_curve is not None:
                    self._inset.removeItem(_inset_curve)
        for name, y in traces.items():
            target_axis = axis.get(name, AXIS_LEFT)
            target_color = colors.get(name, "k")
            self._curve_color[name] = target_color
            self._curve_data[name] = (time_us, y)
            if name in self._curves:
                # Existing curve: update data and (if changed) move to
                # the target axis / re-pen the colour.
                self._curves[name].setData(time_us, y)
                self._curves[name].setPen(pg.mkPen(color=target_color, width=2))
                if self._curve_axis.get(name) != target_axis:
                    self._move_curve(name, target_axis)
            else:
                pen = pg.mkPen(color=target_color, width=2)
                curve = pg.PlotDataItem(time_us, y, pen=pen, name=name)
                if target_axis == AXIS_RIGHT:
                    self._right_vb.addItem(curve)
                    # Right-axis curves live in a separate ViewBox
                    # that the legend doesn't know about, so add the
                    # entry manually.  Left-axis curves are added by
                    # ``plotItem.addItem`` below, which auto-
                    # registers them with the legend (because the
                    # curve was constructed with ``name=``) — calling
                    # ``self._legend.addItem`` again there produced
                    # the visible duplicates ("two V_mon, two E_ret").
                    if self._legend is not None:
                        try:
                            self._legend.addItem(curve, name)
                        except Exception:
                            pass
                else:
                    self._plot.plotItem.addItem(curve)
                self._curves[name] = curve
                self._curve_axis[name] = target_axis
        # Snap the X-axis range to round numbers derived from the time
        # axis — port of MATLAB ``getPlot.m`` (and the same routine the
        # calibration plot uses, see
        # :meth:`CalibrationDialog._update_acq_plot`):
        #
        #     xMin = round(min(time), 1, 'significant');
        #     xMax = round(max(time), 1, 'significant');
        #     xlim([xMin xMax]);
        #
        # Without this, pyqtgraph's auto-range picks the literal data
        # extents (``-123.45 µs`` … ``478.32 µs``), and the major-only
        # tick override above is forced to lay ticks on awkward
        # endpoints.  Rounding the *range* first lets the tick
        # algorithm land on clean integers like ``-100, 0, 100, 200``.
        try:
            if time_us is not None:
                _t = np.asarray(time_us, dtype=float)
                if _t.size:
                    x_min = _round_sig(float(_t.min()), 1)
                    x_max = _round_sig(float(_t.max()), 1)
                    # Defensive: when both round to the same value
                    # (e.g. a single-sample capture), fall back to the
                    # raw extents so setXRange doesn't see zero width.
                    if x_min == x_max:
                        x_min = float(_t.min())
                        x_max = float(_t.max())
                    self._plot.setXRange(x_min, x_max, padding=0)
        except Exception:
            pass
        # Refresh inset traces if the inset is showing — a previously-
        # selected trace's data may have changed.
        if self._inset_visible:
            self._refresh_inset()

    def _move_curve(self, name: str, target_axis: str) -> None:
        """Re-parent ``name`` from its current axis to ``target_axis``.

        Used when the user flips a curve between left and right
        without recreating the plot. Also re-registers the curve with
        the legend so the swatch order stays predictable.
        """
        curve = self._curves.get(name)
        if curve is None:
            return
        old = self._curve_axis.get(name, AXIS_LEFT)
        if old == target_axis:
            return
        # Drop the old hosting + its legend entry if any, then re-add
        # to the new host.  ``plotItem.addItem`` re-registers the
        # curve with the attached legend automatically when the
        # curve's ``name`` is set, so we never call
        # ``self._legend.addItem`` for left-axis curves — only the
        # right-axis branch needs the explicit add (the right
        # ViewBox isn't connected to the legend's plot).
        if old == AXIS_RIGHT:
            self._right_vb.removeItem(curve)
            if self._legend is not None:
                try:
                    self._legend.removeItem(curve)
                except Exception:
                    pass
        else:
            self._plot.plotItem.removeItem(curve)
        if target_axis == AXIS_RIGHT:
            self._right_vb.addItem(curve)
            if self._legend is not None:
                try:
                    self._legend.addItem(curve, name)
                except Exception:
                    pass
        else:
            self._plot.plotItem.addItem(curve)
        self._curve_axis[name] = target_axis

    def set_axis_for(self, name: str, axis: str) -> None:
        """Move an already-plotted curve to ``axis`` without re-data."""
        if name not in self._curves:
            return
        self._move_curve(name, axis)

    def align_y_zeros(self) -> None:
        """Stretch the left Y-axis so its 0-line coincides with the
        right Y-axis's 0-line.

        Port of MATLAB ``getPlot.m`` lines 60-67 (same routine the
        calibration plot uses): take the right axis's ``[min, max]``
        as authoritative, compute the ratio ``min/max``, then expand
        the left axis to match.  Result: a horizontal line drawn
        through 0 V on the left axis also passes through 0 µA (or
        0 A/cm²) on the right — invaluable when comparing the V_mon
        polarity to the I_mon polarity at a phase boundary.

        No-op when pyqtgraph is missing, the right axis is empty
        (``ratio`` undefined), or both axes already share a zero
        within numerical noise.
        """
        if not HAS_PYQTGRAPH or self._plot is None or self._right_vb is None:
            return
        left_vb = self._plot.plotItem.vb
        right_vb = self._right_vb
        # Force a fresh autorange computation so viewRange() reports
        # the data's actual extent and not last frame's frozen limits.
        try:
            left_vb.enableAutoRange(axis=left_vb.YAxis)
            right_vb.enableAutoRange(axis=right_vb.YAxis)
            left_vb.updateAutoRange()
            right_vb.updateAutoRange()
        except Exception:
            return
        try:
            yliml = left_vb.viewRange()[1]
            ylimr = right_vb.viewRange()[1]
            yl_lo, yl_hi = float(yliml[0]), float(yliml[1])
            yr_lo, yr_hi = float(ylimr[0]), float(ylimr[1])
        except Exception:
            return
        if yr_hi == 0.0:
            return
        ratio = yr_lo / yr_hi
        if yl_hi * ratio < yl_lo:
            new_lo, new_hi = yl_hi * ratio, yl_hi
        else:
            new_lo, new_hi = yl_lo, (yl_lo / ratio if ratio != 0.0 else yl_hi)
        try:
            left_vb.enableAutoRange(axis=left_vb.YAxis, enable=False)
            left_vb.setYRange(new_lo, new_hi, padding=0)
        except Exception:
            pass

    def set_axis_labels(self, *, left: Optional[str] = None,
                        right: Optional[str] = None) -> None:
        """Update the visible axis text. ``None`` leaves the existing
        label alone so the caller can change one side without
        clobbering the other.

        ``left`` / ``right`` are passed verbatim — HTML (``<br/>``,
        ``<sup>``, ``<sub>``) renders.  Conventions in this codebase:

        * single line: ``"Voltage [V]"``, ``"Current [µA]"``
        * two lines (axis name + conversion hint):
          ``"Current Density [A/cm²]<br/>(1 A/cm² = 50 µA)"``

        ``pyqtgraph`` rejects empty strings (collapses the label),
        so callers should always pass a non-empty value if they
        intend the axis to have a label."""
        if self._plot is None:
            return
        if left is not None:
            self._plot.setLabel("left", left)
        if right is not None:
            self._plot.setLabel("right", right)

    # --------------------------------------------------------------- inset
    def set_inset_visible(self, visible: bool) -> None:
        """Show or hide the small inset plot below the main view."""
        if self._inset is None:
            return
        self._inset_visible = bool(visible)
        self._inset.setVisible(self._inset_visible)
        if self._inset_visible:
            self._refresh_inset()

    def is_inset_visible(self) -> bool:
        return self._inset_visible

    def set_inset_traces(self, names) -> None:
        """Pick which curve names appear in the inset.

        Names are matched against the keys passed to
        :meth:`set_traces`. The inset draws each selected trace with
        the same colour as the main plot but a thinner pen so the
        eye reads them as a focused subset of the same data.
        """
        if self._inset is None:
            return
        self._inset_trace_names = {str(n) for n in (names or [])}
        if self._inset_visible:
            self._refresh_inset()

    def inset_traces(self) -> set:
        return set(self._inset_trace_names)

    def _refresh_inset(self) -> None:
        """Synchronise the inset's curves with the current selection."""
        if self._inset is None:
            return
        # Remove curves that are no longer selected.
        for name in list(self._inset_curves.keys()):
            if name not in self._inset_trace_names:
                self._inset.removeItem(self._inset_curves[name])
                del self._inset_curves[name]
        # Add or update selected curves.
        for name in self._inset_trace_names:
            data = self._curve_data.get(name)
            if data is None:
                continue
            time_us, y = data
            color = self._curve_color.get(name, "k")
            pen = pg.mkPen(color=color, width=1)
            if name in self._inset_curves:
                self._inset_curves[name].setData(time_us, y)
                self._inset_curves[name].setPen(pen)
            else:
                self._inset_curves[name] = self._inset.plot(
                    time_us, y, pen=pen, name=name)


# ---------------------------------------------------------------------------
# Channel grid (interactive electrode picker)
# ---------------------------------------------------------------------------
class ChannelGrid(QtWidgets.QWidget):
    """Click-to-pick visual representation of an electrode array."""
    selectionChanged = QtCore.pyqtSignal(int, list)   # active, returns

    def __init__(self, array: ElectrodeArray, parent=None):
        super().__init__(parent)
        self.array = array
        self._active: Optional[int] = None
        self._returns: List[int] = []
        self._buttons: Dict[int, QtWidgets.QPushButton] = {}
        grid = QtWidgets.QGridLayout(self)
        grid.setSpacing(4)
        for s in array.sites:
            btn = QtWidgets.QPushButton(f"{s.number}")
            btn.setFixedSize(48, 48)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, n=s.number: self._on_click(n))
            self._buttons[s.number] = btn
            grid.addWidget(btn, s.row, s.col)
        self._refresh_styles()

    def _on_click(self, n: int):
        mods = QtWidgets.QApplication.keyboardModifiers()
        shift = bool(mods & QtCore.Qt.KeyboardModifier.ShiftModifier)
        if not shift:
            self._active = n
            self._returns = [r for r in self._returns if r != n]
        else:
            if n == self._active:
                return
            if n in self._returns:
                self._returns.remove(n)
            else:
                self._returns.append(n)
        self._refresh_styles()
        self.selectionChanged.emit(self._active or -1, list(self._returns))

    def _refresh_styles(self):
        for n, btn in self._buttons.items():
            if n == self._active:
                btn.setStyleSheet("background:#e57373;color:white;font-weight:bold;")
            elif n in self._returns:
                btn.setStyleSheet("background:#81c784;color:white;font-weight:bold;")
            else:
                btn.setStyleSheet("")
            btn.setChecked(n == self._active or n in self._returns)

    def selection(self):
        return self._active, list(self._returns)

    def set_selection(self, active: int, returns: List[int]) -> None:
        self._active = active
        self._returns = list(returns)
        self._refresh_styles()


# ---------------------------------------------------------------------------
# Metric table
# ---------------------------------------------------------------------------
class _HtmlItemDelegate(QtWidgets.QStyledItemDelegate):
    """Delegate that paints table cells as rich text via QTextDocument.

    Used by :class:`MetricTable` so cells with HTML tags
    (``<i>``, ``<sub>``, ``<sup>``) render with real italic /
    sub / superscript glyphs instead of leaving the literal angle
    brackets in the cell.  ``QTableWidgetItem`` itself only does
    plain text; without this delegate ``<i>Q</i><sub>inj</sub>``
    shows up verbatim.
    """

    def paint(self, painter, option, index):
        text = index.data() or ""
        if "<" not in text or ">" not in text:
            # Plain text — fall through to the default fast path.
            super().paint(painter, option, index)
            return
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(text)
        doc.setTextWidth(option.rect.width())
        painter.save()
        # Background (selection highlight, alternating rows, etc.).
        opt = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""   # we'll draw the text ourselves below
        style = opt.widget.style() if opt.widget else QtWidgets.QApplication.style()
        style.drawControl(QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
                          opt, painter, opt.widget)
        # Centre the document vertically inside the cell.
        text_rect = style.subElementRect(
            QtWidgets.QStyle.SubElement.SE_ItemViewItemText,
            opt, opt.widget)
        painter.translate(text_rect.topLeft())
        painter.setClipRect(text_rect.translated(-text_rect.topLeft()))
        ctx = QtGui.QAbstractTextDocumentLayout.PaintContext()
        # Respect selection foreground colour.
        if option.state & QtWidgets.QStyle.StateFlag.State_Selected:
            ctx.palette.setColor(
                QtGui.QPalette.ColorRole.Text,
                option.palette.color(
                    QtGui.QPalette.ColorGroup.Active,
                    QtGui.QPalette.ColorRole.HighlightedText))
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index):
        text = index.data() or ""
        if "<" not in text or ">" not in text:
            return super().sizeHint(option, index)
        doc = QtGui.QTextDocument()
        doc.setDefaultFont(option.font)
        doc.setHtml(text)
        return QtCore.QSize(int(doc.idealWidth()) + 8,
                            int(doc.size().height()))


class MetricTable(QtWidgets.QTableWidget):
    """Tabular view of the most recent capture metrics.

    Uses an HTML delegate ([_HtmlItemDelegate]) so each row label can
    carry inline rich-text formatting — italic variable names,
    ``<sub>`` for subscripts, ``<sup>`` for exponents — instead of
    the underscore-fallback plain-text representation (``V_d``,
    ``C_eff``) that some letters lacked Unicode subscripts for.
    """

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Metric", "Value"])
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setItemDelegate(_HtmlItemDelegate(self))

    def show_capture(self, c: Capture) -> None:
        m = c.metrics
        # Use HTML ``var(name, sub)`` so ``<i>V</i><sub>d</sub>`` etc.
        # render through the _HtmlItemDelegate above with proper
        # italic + subscript styling — works for every letter
        # regardless of Unicode-subscript availability.  Units are
        # wrapped in **square brackets** per the operator convention
        # used in scientific publications and the calibration plot's
        # axis labels (``Voltage [V]``, ``Current Density [A/cm²]``).
        V = rich.var
        rows = [
            ("Pulse #", str(c.index)),
            (f"Excitation amp [{V('I','stim')}, µA]",
                f"{c.pattern.excitation_phase.amplitude_ua:.2f}"),
            (f"{V('Q','ph')} [nC]", f"{m.charge_per_phase_nc:.2f}"),
            (f"{V('Q','inj')} [mC/cm<sup>2</sup>]",
                f"{m.charge_injection_mc_per_cm2:.3f}"),
            (f"{V('E','ip')} [V]", f"{m.interpulse_potential_v:.3f}"),
            (f"{V('C','eff')} [nF]", f"{m.effective_capacitance_nf:.2f}"),
            (f"{V('C','d')} [mF/cm<sup>2</sup>]",
                f"{m.driving_capacitance_mf_per_cm2:.3f}"),
        ]
        for k, vlist in (
            (f"{V('V','d')} active [V]", m.active_driving_voltage_per_phase_v),
            (f"{V('V','d')} return [V]", m.return_driving_voltage_per_phase_v),
            (f"{V('V','a')} active [V]", m.access_voltage_per_phase_v),
            (f"{V('R','a')} active [kΩ]", m.access_resistance_per_phase_kohm),
            (f"{V('V','a')} return [V]", m.return_access_voltage_per_phase_v),
            (f"{V('R','a')} return [kΩ]", m.return_access_resistance_per_phase_kohm),
            (f"{V('E','pol')} active [V]", m.polarization_per_phase_v),
            (f"{V('E','pol')} return [V]", m.return_polarization_per_phase_v),
        ):
            if vlist:
                rows.append((k, ", ".join(f"{x:.3f}" for x in vlist)))
        rows.append(("Limit reached?", "yes" if c.status.reached_potential_limit else "no"))
        rows.append(("Compliance?", "yes" if c.status.voltage_compliance else "no"))
        self.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.setItem(i, 0, QtWidgets.QTableWidgetItem(k))
            self.setItem(i, 1, QtWidgets.QTableWidgetItem(v))


# ---------------------------------------------------------------------------
# Status bar / log widget
# ---------------------------------------------------------------------------
class LogPane(QtWidgets.QPlainTextEdit):
    """Read-only message log shown at the bottom of the GUI.

    Every line written via :meth:`log` is shown in the pane AND
    appended to a configurable ``log.txt`` on disk so the user has a
    permanent record of what happened during a session — useful for
    debugging "why did this run abort?" the next morning.
    ``set_log_file(path)`` repoints the file when the save directory
    changes; passing ``None`` disables disk logging. The pane keeps
    only the last 1000 lines in memory but the file on disk is
    unbounded.

    **Time format** mirrors the MATLAB reference scripts
    (``getEndTime.m`` + the ``runPulsing.m`` / ``PlexStimTek.m``
    fprintf patterns): every line is prefixed with elapsed time
    ``[H:MM:SS]`` since the most recent :meth:`reset_clock` call,
    not the wall-clock time of day. A one-line banner with the
    wall-clock start is emitted at clock-reset so the user can still
    correlate with other logs. Pair with :meth:`tic` / :meth:`toc`
    for explicit timed sub-operations — the auto-scaled unit
    selector (``us`` / ``ms`` / ``s`` / ``min`` / ``h``) matches the
    MATLAB ``getEndTime.m`` thresholds exactly.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)
        self._log_file_path = None  # type: Optional[Path]
        # Persistent file handle for disk mirroring.  The original
        # implementation opened-write-closed on EVERY line which
        # cost 1-5 ms per write on Windows (per-op directory
        # journalling) and added up to several seconds across a
        # 1000-line sweep.  A long-lived handle with line-buffered
        # writes does the open() once per set_log_file() call and
        # lets the OS coalesce flushes.  Held open for the lifetime
        # of this widget or until set_log_file() points elsewhere.
        self._log_file_handle = None  # type: Optional[Any]
        # Elapsed-time origin and named ``tic`` markers.
        # ``_session_start`` is the ``time.monotonic()`` reading at
        # the most recent ``reset_clock()`` (lazy on first ``log()``
        # so the API is back-compat with callers that don't reset).
        # ``_session_start_wall`` keeps the corresponding wall-clock
        # ``datetime`` for the start banner. ``_tics`` stores
        # ``time.monotonic()`` markers keyed by user-chosen names.
        self._session_start: Optional[float] = None
        self._session_start_wall: Optional[datetime] = None
        self._tics: Dict[str, float] = {}

    def set_log_file(self, path) -> None:
        """Direct subsequent log writes to ``path`` (or ``None`` to
        disable disk logging). Existing content of the file is left
        alone — logs from past sessions in the same folder are
        appended to, not overwritten.

        Opens the file ONCE and keeps the handle in line-buffered
        mode (``buffering=1``) so each ``log()`` line flushes to
        disk without us paying an open/close round-trip per write.
        Any previous handle is closed first.
        """
        from pathlib import Path
        # Always close the previous handle, regardless of whether
        # the new path is None or a real path.
        if self._log_file_handle is not None:
            try:
                self._log_file_handle.close()
            except Exception:
                pass
            self._log_file_handle = None
        if path is None:
            self._log_file_path = None
            return
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Don't blow up the GUI just because the save path is
            # bad — disk logging is best-effort.
            self._log_file_path = None
            return
        self._log_file_path = p
        try:
            # buffering=1 → line-buffered text mode.  Each newline
            # triggers a flush, so a tail-er sees lines arrive in
            # real time without us paying the open-close cost of
            # the per-write approach.
            self._log_file_handle = p.open(
                "a", encoding="utf-8", buffering=1)
        except Exception:
            # Falls back to None → ``_raw_append`` re-opens per
            # line as before if the handle can't be obtained.
            self._log_file_handle = None

    # ------------------------------------------------------------- timing
    def reset_clock(self, *, banner: bool = True) -> None:
        """Reset the elapsed-time origin used by every subsequent
        :meth:`log` line.

        Called by experiment tabs at the start of each run so the
        ``[H:MM:SS]`` prefix counts from "Start clicked" — matching
        the MATLAB convention where each script begins with ``tic``.
        Clears any pending :meth:`tic` markers so a stale name from
        a previous run can't accidentally tick into the new one.

        ``banner=True`` (default) emits a separator line with the
        wall-clock start so the user can correlate the elapsed log
        with other log sources (system journal, scope timestamp).
        """
        self._session_start = time.monotonic()
        self._session_start_wall = datetime.now()
        self._tics.clear()
        if banner:
            stamp = self._session_start_wall.strftime("%Y-%m-%d %H:%M:%S")
            self._raw_append(f"--- Session started {stamp} ---")

    def _ensure_session_started(self) -> None:
        """Lazy clock init — for callers that just call ``log()``
        without an explicit reset (e.g. test fixtures, the connection
        panel before any run begins). Suppresses the banner so we
        don't dump a header into the middle of a paused widget."""
        if self._session_start is None:
            self.reset_clock(banner=False)

    @staticmethod
    def format_scaled(seconds: float) -> Tuple[float, str]:
        """Auto-scale a duration to a human-friendly (value, unit) pair.

        Mirrors MATLAB ``getEndTime.m`` exactly:

        * ``< 0.1 ms`` → ``us``
        * ``< 0.1 s``  → ``ms``
        * ``< 60 s``   → ``s``
        * ``< 60 min`` → ``min``
        * else         → ``h``

        Pair with the ``"%.2f %s"`` style used by ``runPulsing.m`` /
        ``PlexStimTek.m`` for log lines like
        ``"Experiment completed: 12.34 min"``.
        """
        s = max(0.0, float(seconds))
        if s < 1e-4:
            return s * 1e6, "us"
        if s < 0.1:
            return s * 1e3, "ms"
        if s < 60.0:
            return s, "s"
        if s < 3600.0:
            return s / 60.0, "min"
        return s / 3600.0, "h"

    @staticmethod
    def _format_elapsed_hms(seconds: float) -> str:
        """``H:MM:SS`` — the prefix shown on every log line."""
        s = max(0.0, float(seconds))
        h, rem = divmod(int(s), 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}"

    def tic(self, name: str = "") -> str:
        """Start a named timer (mirrors MATLAB ``tic``).

        Returns the ``name`` (auto-generated when empty) so callers
        can pair it directly with :meth:`toc`. Markers are scoped to
        this LogPane and cleared on :meth:`reset_clock` so a stale
        name from the previous run can't carry into the new one.
        """
        if not name:
            name = f"tic{len(self._tics)}"
        self._tics[name] = time.monotonic()
        return name

    def toc(self, name: str, *, label: Optional[str] = None,
            log: bool = True) -> float:
        """End a named timer and return its elapsed seconds.

        When ``log`` is True (default), also append a log line
        formatted like the MATLAB ``getEndTime.m`` callers:
        ``"<label> completed in X.YZ <unit>"``. ``label`` defaults
        to the timer name. Returns ``nan`` when the name is unknown
        (e.g. cleared by an intervening :meth:`reset_clock`).
        """
        start = self._tics.pop(name, None)
        if start is None:
            return float("nan")
        elapsed = time.monotonic() - start
        if log:
            value, unit = self.format_scaled(elapsed)
            tag = label if label is not None else name
            self.log(f"{tag} completed in {value:.2f} {unit}")
        return elapsed

    def log_elapsed(self, msg: str, since: float) -> None:
        """Append ``msg`` with an auto-scaled "(X.YZ unit)" suffix.

        ``since`` is a ``time.monotonic()`` marker (or the value
        returned from :meth:`tic` if the user grabbed it). Use this
        for one-off timed events that don't need a named tic/toc
        pair, e.g. ``log_elapsed("Connected to scope", t0)``.
        """
        elapsed = time.monotonic() - float(since)
        value, unit = self.format_scaled(elapsed)
        self.log(f"{msg} ({value:.2f} {unit})")

    # ------------------------------------------------------------- writes
    @QtCore.pyqtSlot(str)
    def log(self, msg: str) -> None:
        self._ensure_session_started()
        elapsed = time.monotonic() - (self._session_start or 0.0)
        line = f"[{self._format_elapsed_hms(elapsed)}] {msg}"
        self._raw_append(line)

    def log_now(self, msg: str) -> None:
        """Like :meth:`log` but force the GUI to repaint immediately.

        Use during long synchronous setup phases — e.g. the scope SCPI
        sequence at run start — where the next host-side call blocks
        the GUI thread for hundreds of ms.  Without an explicit event-
        loop tick, the operator sees no progress between log lines
        (the QPlainTextEdit only repaints when control returns to the
        event loop) and assumes the program is hung.

        File mirroring is unaffected: ``_raw_append`` writes to the
        line-buffered handle synchronously, so the on-disk log is
        always current regardless of which variant the caller used.
        This method exists purely to drive the on-screen repaint.

        Cost: one ``processEvents()`` call, typically 1-5 ms — orders
        of magnitude less than the SCPI round-trip that prompted the
        log line in the first place.
        """
        self.log(msg)
        try:
            app = QtWidgets.QApplication.instance()
            if app is not None:
                app.processEvents()
        except Exception:
            pass

    def _raw_append(self, line: str) -> None:
        """Common path for both ``log()`` and the start banner —
        append to the pane and mirror to disk. Best-effort; disk
        errors are silently swallowed because the pane is the
        source of truth, the file is just a convenience copy.
        Uses the long-lived line-buffered handle from
        :meth:`set_log_file` when available; falls back to per-write
        open() only if the handle can't be obtained.
        """
        self.appendPlainText(line)
        if self._log_file_handle is not None:
            try:
                self._log_file_handle.write(line + "\n")
                # Force the Python buffer to flush to the OS, then
                # ask the OS to commit to disk.  buffering=1 (line-
                # buffered) auto-flushes the Python buffer on '\n'
                # — but the OS page cache can still hold the line
                # for seconds.  When the GUI dies abruptly (HEAP_
                # CORRUPTION inside a native module: PyQt6, numpy,
                # plexon DLL — exit code -1073740940 with no Python
                # exception), the kernel doesn't always flush before
                # the process gets cleaned up, and the LAST few
                # logged scope SCPI commands that pinpoint the crash
                # site never reach disk.  An explicit flush+fsync
                # per line costs ~1 ms but guarantees the on-disk
                # log shows everything up to the very last write.
                try:
                    self._log_file_handle.flush()
                    import os as _os
                    _os.fsync(self._log_file_handle.fileno())
                except Exception:
                    pass
                return
            except Exception:
                # Stale handle (e.g. drive went away); drop and let
                # the fallback retry — re-opening once is cheaper
                # than dropping every line until next set_log_file.
                try:
                    self._log_file_handle.close()
                except Exception:
                    pass
                self._log_file_handle = None
        if self._log_file_path is not None:
            try:
                with self._log_file_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
                    try:
                        import os as _os
                        _os.fsync(f.fileno())
                    except Exception:
                        pass
            except Exception:
                pass
