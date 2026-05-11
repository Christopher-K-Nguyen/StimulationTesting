"""Reusable Qt widgets used by the experiment tabs."""
from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

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
            self._plot.setLabel("bottom", "Time", units="µs")
            self._plot.setLabel("left", "Voltage", units="V")
            self._legend = self._plot.addLegend()
            # Right-axis ViewBox — linked to the main viewbox's X so
            # zoom / pan stays in sync, but its Y is independent. Auto
            # resizes to match the main viewbox's geometry.
            self._right_vb = pg.ViewBox()
            self._plot.showAxis("right")
            self._plot.scene().addItem(self._right_vb)
            self._plot.getAxis("right").linkToView(self._right_vb)
            self._right_vb.setXLink(self._plot.plotItem.vb)
            self._plot.setLabel("right", "Current", units="µA")
            self._plot.plotItem.vb.sigResized.connect(self._sync_right_geometry)
            layout.addWidget(self._plot, stretch=1)
            # ---------- inset plot (hidden by default) ----------
            self._inset = pg.PlotWidget()
            self._inset.setVisible(False)
            self._inset.setMinimumHeight(80)
            self._inset.setMaximumHeight(220)
            self._inset.showGrid(x=False, y=False)
            self._inset.setLabel("bottom", "Time", units="µs")
            self._inset.setLabel("left", "Inset")
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
                   axis: Optional[Dict[str, str]] = None):
        """Plot or update each trace; assign each to a Y axis.

        ``axis[name]`` selects ``"left"`` or ``"right"`` for that
        trace; missing keys default to ``"left"``. When a previously-
        plotted curve switches axis between calls, it's smoothly
        re-parented (no flicker) — same for color and data.
        """
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        colors = colors or {}
        axis = axis or {}
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
                else:
                    self._plot.plotItem.addItem(curve)
                self._curves[name] = curve
                self._curve_axis[name] = target_axis
                # Keep the legend in sync — right-axis curves wouldn't
                # auto-register with the main legend, so add manually.
                if self._legend is not None:
                    try:
                        self._legend.addItem(curve, name)
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
        if old == AXIS_RIGHT:
            self._right_vb.removeItem(curve)
        else:
            self._plot.plotItem.removeItem(curve)
        if target_axis == AXIS_RIGHT:
            self._right_vb.addItem(curve)
        else:
            self._plot.plotItem.addItem(curve)
        self._curve_axis[name] = target_axis

    def set_axis_for(self, name: str, axis: str) -> None:
        """Move an already-plotted curve to ``axis`` without re-data."""
        if name not in self._curves:
            return
        self._move_curve(name, axis)

    def set_axis_labels(self, *, left: Optional[str] = None,
                        right: Optional[str] = None,
                        left_units: Optional[str] = None,
                        right_units: Optional[str] = None) -> None:
        """Update the visible axis text. ``None`` leaves the existing
        label alone so the caller can change one side without
        clobbering the other."""
        if self._plot is None:
            return
        if left is not None:
            self._plot.setLabel("left", left, units=(left_units or ""))
        if right is not None:
            self._plot.setLabel("right", right, units=(right_units or ""))

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
class MetricTable(QtWidgets.QTableWidget):
    """Tabular view of the most recent capture metrics."""

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Metric", "Value"])
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

    def show_capture(self, c: Capture) -> None:
        m = c.metrics
        L = rich.plain_label
        rows = [
            ("Pulse #", str(c.index)),
            (f"Excitation amp ({L('I','stim')}, µA)",
                f"{c.pattern.excitation_phase.amplitude_ua:.2f}"),
            (f"{L('Q','ph')} (nC)", f"{m.charge_per_phase_nc:.2f}"),
            (f"{L('Q','inj')} (mC/cm²)", f"{m.charge_injection_mc_per_cm2:.3f}"),
            (f"{L('E','ip')} (V)", f"{m.interpulse_potential_v:.3f}"),
            (f"{L('C','eff')} (nF)", f"{m.effective_capacitance_nf:.2f}"),
            (f"{L('C','d')} (mF/cm²)", f"{m.driving_capacitance_mf_per_cm2:.3f}"),
        ]
        for k, vlist in (
            (f"{L('V','d')} active (V)", m.active_driving_voltage_per_phase_v),
            (f"{L('V','d')} return (V)", m.return_driving_voltage_per_phase_v),
            (f"{L('V','a')} active (V)", m.access_voltage_per_phase_v),
            (f"{L('R','a')} active (kΩ)", m.access_resistance_per_phase_kohm),
            (f"{L('V','a')} return (V)", m.return_access_voltage_per_phase_v),
            (f"{L('R','a')} return (kΩ)", m.return_access_resistance_per_phase_kohm),
            (f"{L('E','pol')} active (V)", m.polarization_per_phase_v),
            (f"{L('E','pol')} return (V)", m.return_polarization_per_phase_v),
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
        """
        from pathlib import Path
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

    def _raw_append(self, line: str) -> None:
        """Common path for both ``log()`` and the start banner —
        append to the pane and mirror to disk. Best-effort; disk
        errors are silently swallowed because the pane is the
        source of truth, the file is just a convenience copy.
        """
        self.appendPlainText(line)
        if self._log_file_path is not None:
            try:
                with self._log_file_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
