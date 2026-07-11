"""Test-device panel: graphic + editable channel-mapping table.

Sits on the right side of the Setup tab. Layout:

    ┌───────────────────────────────────┐
    │  <Test device name>               │   <- bold header (rich text)
    │  one-line description             │
    │  ┌─────────────┐  ┌─────────────┐ │
    │  │ Visualizer  │  │ Channel-map │ │
    │  │ (geometry)  │  │ table       │ │
    │  │   [graphic] │  │ [editable]  │ │
    │  └─────────────┘  └─────────────┘ │
    │  per-channel area / coating opts  │
    └───────────────────────────────────┘

The table is a QTableWidget where each non-zero cell holds a 1-based
channel number; ``0`` cells are rendered as a dim "—". Cells are
editable — when the user types a new channel number (or 0 for empty),
``mappingChanged`` fires and the geometry visualizer updates. The
visualizer is a pure-QPainter widget so it scales nicely without
pulling in pyqtgraph just for a static layout.

Public API
----------
* ``set_device(DeviceDef)`` — load a built-in device's mapping.
* ``set_mapping(np.ndarray)`` — drive the table from outside.
* ``current_mapping() -> np.ndarray`` — read back what's in the table.
* signal ``mappingChanged(np.ndarray)`` — fires whenever the user edits
  the table or a built-in device is selected.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import DeviceDef
from . import rich
from .repeating_spinbox import RepeatingSpinBox
from .widgets import enable_spreadsheet_paste


# ---------------------------------------------------------------------------
# Geometry visualizer — paints filled circles at electrode positions
# ---------------------------------------------------------------------------
class _GeometryView(QtWidgets.QWidget):
    """Schematic 2-D view of the channel map.

    Renders one filled circle per non-zero cell of the mapping with the
    channel number drawn inside. Empty cells (mapping == 0) are skipped
    so multi-shank arrays show their inter-shank gaps. The whole view
    auto-scales to fit the widget while keeping a 1:1 cell aspect ratio.

    The ``layout`` argument switches between two packing modes:

    * ``"rect"`` — square lattice. Drawn with subtle grid lines so the
      table indices are easy to read off the canvas.
    * ``"triangular"`` — equilateral triangular lattice. Odd rows are
      offset by half a cell, vertical row pitch is ``cell × √3/2``, and
      grid lines are dropped because they no longer line up with the
      disks. Used for MicroProbes FMA-style arrays.
    """
    PAD = 8                  # pixel padding around the grid
    MIN_CELL = 16            # smallest rendered cell, px
    MAX_CELL = 64            # largest rendered cell, px
    TRI_Y_FACTOR = 0.86602540378  # √3/2 — equilateral row pitch

    def __init__(self, parent=None):
        super().__init__(parent)
        self._grid: np.ndarray = np.zeros((1, 1), dtype=int)
        self._layout: str = "rect"
        self.setMinimumSize(160, 120)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)

    def set_grid(self, grid: np.ndarray, layout: str = "rect"):
        self._grid = np.asarray(grid, dtype=int)
        self._layout = layout if layout in ("rect", "triangular") else "rect"
        self.update()

    def paintEvent(self, _ev):
        rows, cols = self._grid.shape
        if rows == 0 or cols == 0:
            return
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#fafafa"))

        triangular = (self._layout == "triangular")
        eff_rows = rows
        eff_cols = cols
        # x footprint of the bounding box: half-cell wider when alternating
        # rows are offset (any of those odd rows pushes content to the
        # right by cell/2).
        x_footprint_cells = eff_cols + (0.5 if triangular and eff_rows > 1 else 0)
        # y footprint: rect = cell per row; tri = √3/2 per row + 1 cell tail
        # so the bottom row's disks aren't clipped at the edge.
        y_footprint_cells = (eff_rows - 1) * (self.TRI_Y_FACTOR if triangular else 1) + 1

        avail_w = max(1, self.width() - 2 * self.PAD)
        avail_h = max(1, self.height() - 2 * self.PAD)
        cell = min(avail_w / x_footprint_cells, avail_h / y_footprint_cells)
        cell = int(max(self.MIN_CELL, min(self.MAX_CELL, cell)))

        grid_w = int(cell * x_footprint_cells)
        grid_h = int(cell * y_footprint_cells)
        x0 = (self.width() - grid_w) // 2
        y0 = (self.height() - grid_h) // 2

        if not triangular:
            # Subtle grid lines so empty cells still convey shape; only
            # makes sense on the rect lattice — they'd misalign with the
            # offset disks on the triangular one.
            p.setPen(QtGui.QPen(QtGui.QColor("#dcdcdc"), 1))
            for r in range(eff_rows + 1):
                y = y0 + r * cell
                p.drawLine(x0, y, x0 + eff_cols * cell, y)
            for c in range(eff_cols + 1):
                x = x0 + c * cell
                p.drawLine(x, y0, x, y0 + eff_rows * cell)

        # Electrode disks
        radius = int(cell * 0.38)
        font = p.font()
        font.setPointSize(max(7, cell // 5))
        p.setFont(font)
        # Float math so triangular spacing is exactly equidistant —
        # the previous ``cell // 2`` integer truncation drifted the
        # next-row diagonals to ~47.5 px when same-row was 48 px.
        cell_f = float(cell)
        for r in range(rows):
            row_offset = (cell_f / 2.0) if (triangular and r % 2 == 1) else 0.0
            row_y_pitch = (cell_f * self.TRI_Y_FACTOR) if triangular else cell_f
            for c in range(cols):
                ch = int(self._grid[r, c])
                if ch <= 0:
                    continue
                cx = int(round(x0 + c * cell_f + cell_f / 2.0 + row_offset))
                cy = int(round(y0 + r * row_y_pitch + cell_f / 2.0))
                # disk
                p.setBrush(QtGui.QColor("#1976d2"))
                p.setPen(QtGui.QPen(QtGui.QColor("#0d47a1"), 1))
                p.drawEllipse(QtCore.QPoint(cx, cy), radius, radius)
                # channel number (white, centered)
                p.setPen(QtCore.Qt.GlobalColor.white)
                rect = QtCore.QRect(cx - radius, cy - radius,
                                    2 * radius, 2 * radius)
                p.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, str(ch))
        p.end()


# ---------------------------------------------------------------------------
# DeviceView: header + table + geometry view + per-channel options
# ---------------------------------------------------------------------------
class DeviceView(QtWidgets.QGroupBox):
    """Right-side device panel with editable channel mapping."""

    mappingChanged = QtCore.pyqtSignal(object)         # np.ndarray of int
    perChannelChanged = QtCore.pyqtSignal()            # area/coating overrides edited

    def __init__(self, parent=None):
        super().__init__("Test device", parent)
        self._suspend_table_signals = False
        self._device: Optional[DeviceDef] = None
        # Layout for the geometry view ("rect" or "triangular"). Cached so
        # that hand-edits to the table don't reset it back to "rect".
        self._layout: str = "rect"

        # ----- header -----
        self.title_label = QtWidgets.QLabel()
        self.title_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.title_label.setWordWrap(True)
        f = self.title_label.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        self.title_label.setFont(f)
        self.desc_label = QtWidgets.QLabel()
        self.desc_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color: #555;")

        # ----- channel-map table -----
        self.table = QtWidgets.QTableWidget(0, 0)
        self.table.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
            QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked |
            QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.table.itemChanged.connect(self._on_table_item_changed)
        # Spreadsheet copy / cut / paste — users routinely build a
        # channel-map in Excel / Google Sheets and want to paste a
        # whole block in one shot rather than retype every cell.
        enable_spreadsheet_paste(self.table)

        # Resize controls
        self.rows_spin = RepeatingSpinBox(); self.rows_spin.setRange(1, 32); self.rows_spin.setValue(4)
        self.cols_spin = RepeatingSpinBox(); self.cols_spin.setRange(1, 32); self.cols_spin.setValue(4)
        # Commit on Enter/return/focus-out, NOT per keystroke (operator: "I
        # want pressing enter/return or clicking out") — resizing the grid +
        # emitting mappingChanged (which logs) shouldn't fire on every digit
        # while typing a size (e.g. "16" would otherwise resize 1 → 16).
        self.rows_spin.editingFinished.connect(self._on_resize)
        self.cols_spin.editingFinished.connect(self._on_resize)

        size_row = QtWidgets.QHBoxLayout()
        size_row.addWidget(QtWidgets.QLabel("Rows:"))
        size_row.addWidget(self.rows_spin)
        size_row.addSpacing(8)
        size_row.addWidget(QtWidgets.QLabel("Cols:"))
        size_row.addWidget(self.cols_spin)
        size_row.addStretch(1)

        # ----- geometry view -----
        self.geom = _GeometryView()

        # ----- "different per-channel" overrides table -----
        # Hidden by default; revealed when surface_area_mode == 'Different'
        self.perchan = QtWidgets.QTableWidget(0, 3)
        self.perchan.setHorizontalHeaderLabels(["Channel", "Area (μm²)", "Coating"])
        self.perchan.horizontalHeader().setStretchLastSection(True)
        self.perchan.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
            QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked |
            QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.perchan.itemChanged.connect(lambda _: self.perChannelChanged.emit())
        self.perchan.setVisible(False)
        # Same spreadsheet copy / cut / paste support as the
        # channel-map table — users with a long override list can
        # batch-paste from a spreadsheet column.
        enable_spreadsheet_paste(self.perchan)

        # ----- assemble -----
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(self.title_label)
        v.addWidget(self.desc_label)
        v.addLayout(size_row)
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.addWidget(self.geom)
        split.addWidget(self.table)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        v.addWidget(split, stretch=1)
        self._geom_table_split = split
        _perchan_lbl = QtWidgets.QLabel(
            "Per-channel overrides (shown when surface area or coating is "
            "<i>different per electrode</i>):")
        _perchan_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        _perchan_lbl.setWordWrap(True)
        v.addWidget(_perchan_lbl)
        v.addWidget(self.perchan, stretch=0)

        # initial empty state
        self.set_mapping(np.zeros((4, 4), dtype=int))

    # ----------------------------------------------------------- public API
    def set_device(self, device: DeviceDef):
        """Load a built-in device — populates header, table, and geometry."""
        self._device = device
        self._layout = getattr(device, "layout", "rect")
        # Header text — only the triangular geometries get a tag;
        # everything else is the bare device name.
        suffix = " <i>(triangular layout)</i>" if self._layout == "triangular" else ""
        self.title_label.setText(f"<b>{device.name}</b>{suffix}")
        self.desc_label.setText(device.description)
        # Mapping
        grid = np.array(device.mapping, dtype=int)
        # Lock whichever dimension is 1 by definition: an N×1 array
        # (Linear, vertical) keeps cols=1 — only the row count is
        # editable. A 1×N array (horizontal strip) keeps rows=1.
        # Multi-D arrays leave both editable.
        rows, cols = grid.shape
        if rows == 1 and cols > 1:        # 1×N strip
            self.rows_spin.setEnabled(False)
            self.cols_spin.setEnabled(True)
        elif cols == 1 and rows > 1:      # N×1 column (Linear)
            self.rows_spin.setEnabled(True)
            self.cols_spin.setEnabled(False)
        else:
            self.rows_spin.setEnabled(True)
            self.cols_spin.setEnabled(True)
        # Sync row/col spinners without re-triggering resize
        self.rows_spin.blockSignals(True); self.rows_spin.setValue(rows); self.rows_spin.blockSignals(False)
        self.cols_spin.blockSignals(True); self.cols_spin.setValue(cols); self.cols_spin.blockSignals(False)
        self.set_mapping(grid)

    def set_title(self, name: str, description: str = "") -> None:
        """Override the device-view header — used for user-named
        custom devices that share the ``Other (custom grid)``
        template but want their own display name. The triangular
        suffix follows the live ``_layout`` (toggled via
        :meth:`set_layout`). Doesn't touch the mapping or spinners
        — callers that need to reset those should do it explicitly
        via :meth:`set_mapping` / :meth:`set_layout`.
        """
        suffix = (" <i>(triangular layout)</i>"
                  if self._layout == "triangular" else "")
        self.title_label.setText(f"<b>{name}</b>{suffix}")
        if description:
            self.desc_label.setText(description)

    def set_mapping(self, grid: np.ndarray):
        """Programmatic mapping update — populates table, repaints geometry."""
        grid = np.asarray(grid, dtype=int)
        self._suspend_table_signals = True
        try:
            rows, cols = grid.shape
            self.table.setRowCount(rows); self.table.setColumnCount(cols)
            self.table.setHorizontalHeaderLabels([f"C{c+1}" for c in range(cols)])
            self.table.setVerticalHeaderLabels([f"R{r+1}" for r in range(rows)])
            for r in range(rows):
                for c in range(cols):
                    val = int(grid[r, c])
                    item = QtWidgets.QTableWidgetItem("—" if val == 0 else str(val))
                    item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                    if val == 0:
                        item.setForeground(QtGui.QBrush(QtGui.QColor("#bbb")))
                    self.table.setItem(r, c, item)
            self.table.resizeColumnsToContents()
        finally:
            self._suspend_table_signals = False
        self.geom.set_grid(grid, layout=self._layout)
        self.mappingChanged.emit(grid)

    def current_mapping(self) -> np.ndarray:
        """Read back the current channel grid as an int ndarray. ``0`` = empty."""
        rows, cols = self.table.rowCount(), self.table.columnCount()
        out = np.zeros((rows, cols), dtype=int)
        for r in range(rows):
            for c in range(cols):
                item = self.table.item(r, c)
                if item is None: continue
                t = item.text().strip()
                if not t or t == "—":
                    out[r, c] = 0
                else:
                    try:
                        out[r, c] = int(t)
                    except ValueError:
                        out[r, c] = 0
        return out

    def current_layout(self) -> str:
        """Currently active geometry hint — ``"rect"`` or ``"triangular"``.

        Set automatically by :meth:`set_device` from the catalog's
        :attr:`DeviceDef.layout`, and overridable through
        :meth:`set_layout` (used by the Custom-grid chooser on the
        Setup tab).
        """
        return self._layout

    def set_layout(self, layout: str):
        """Override the geometry hint and re-render the geometry view.

        Used by the "Grid type" chooser for the *Other (custom grid)*
        device, where the user gets to pick between square and
        hexagonal packing for their hand-built mapping. A no-op if the
        value is already current — saves an unnecessary repaint.
        """
        layout = layout if layout in ("rect", "triangular") else "rect"
        if layout == self._layout:
            return
        self._layout = layout
        # Refresh the title-bar suffix (only triangular gets the tag).
        if self._device is not None:
            suffix = " <i>(triangular layout)</i>" if layout == "triangular" else ""
            self.title_label.setText(f"<b>{self._device.name}</b>{suffix}")
        # Redraw the geometry view with the new packing.
        grid = self.current_mapping()
        self.geom.set_grid(grid, layout=self._layout)

    def set_per_channel_visible(self, visible: bool):
        """Show/hide the per-channel area/coating override table."""
        self.perchan.setVisible(visible)
        if visible:
            self._sync_perchan_rows()

    def set_per_channel_columns(self, area: bool, coating: bool):
        """Show only the override columns the user is editing.

        When the user has *Same for all electrodes* ticked for area, the
        Area column is meaningless and is hidden — likewise for coating.
        The Channel column is always visible. The table itself is only
        shown when at least one override column is visible (handled by
        :meth:`set_per_channel_visible`)."""
        self.perchan.setColumnHidden(1, not area)
        self.perchan.setColumnHidden(2, not coating)

    def per_channel_overrides(self) -> dict:
        """Return ``{channel_number: {'area_um2': float, 'coating': str}}``.

        Only returns rows where the user actually entered a value
        different from the device defaults. Empty channels are skipped.
        """
        out: dict = {}
        for r in range(self.perchan.rowCount()):
            ch_item = self.perchan.item(r, 0)
            if ch_item is None: continue
            try:
                ch = int(ch_item.text())
            except ValueError:
                continue
            if ch <= 0: continue
            row = {}
            area_item = self.perchan.item(r, 1)
            if area_item is not None and area_item.text().strip():
                try:
                    row["area_um2"] = float(area_item.text())
                except ValueError:
                    pass
            coat_item = self.perchan.item(r, 2)
            if coat_item is not None and coat_item.text().strip():
                row["coating"] = coat_item.text().strip()
            if row:
                out[ch] = row
        return out

    # ----------------------------------------------------------- internal
    def _on_table_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._suspend_table_signals:
            return
        # Re-paint geometry based on whatever the user typed and emit.
        grid = self.current_mapping()
        # Restyle the cell that just changed
        text = item.text().strip()
        if text == "" or text == "0":
            item.setText("—")
            item.setForeground(QtGui.QBrush(QtGui.QColor("#bbb")))
        else:
            item.setForeground(QtGui.QBrush(QtCore.Qt.GlobalColor.black))
        self.geom.set_grid(grid, layout=self._layout)
        self.mappingChanged.emit(grid)
        if self.perchan.isVisible():
            self._sync_perchan_rows()

    def _on_resize(self):
        rows = self.rows_spin.value()
        cols = self.cols_spin.value()
        old = self.current_mapping()
        new = np.zeros((rows, cols), dtype=int)
        # Preserve overlap region
        rr = min(rows, old.shape[0]); cc = min(cols, old.shape[1])
        new[:rr, :cc] = old[:rr, :cc]
        self.set_mapping(new)

    def _sync_perchan_rows(self):
        """Make the per-channel override table contain one row per non-zero
        channel currently in the map. Preserves any user-entered values."""
        existing: dict = {}
        for r in range(self.perchan.rowCount()):
            ch_item = self.perchan.item(r, 0)
            if ch_item is None: continue
            try:
                ch = int(ch_item.text())
            except ValueError:
                continue
            area_item = self.perchan.item(r, 1)
            coat_item = self.perchan.item(r, 2)
            existing[ch] = (
                area_item.text() if area_item else "",
                coat_item.text() if coat_item else "",
            )
        grid = self.current_mapping()
        channels = sorted({int(v) for v in grid.flatten() if v > 0})
        self.perchan.blockSignals(True)
        try:
            self.perchan.setRowCount(len(channels))
            for r, ch in enumerate(channels):
                area_str, coat_str = existing.get(ch, ("", ""))
                ch_item = QtWidgets.QTableWidgetItem(str(ch))
                ch_item.setFlags(ch_item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                ch_item.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.perchan.setItem(r, 0, ch_item)
                self.perchan.setItem(r, 1, QtWidgets.QTableWidgetItem(area_str))
                self.perchan.setItem(r, 2, QtWidgets.QTableWidgetItem(coat_str))
        finally:
            self.perchan.blockSignals(False)

    # ----------------------------------------------------------- view state
    def view_state(self) -> dict:
        """Snapshot splitter sizes + table column widths for restore.

        The user can resize the geometry/table split, the channel-map
        table columns, and the per-channel override table columns.
        Each is captured here so a re-launch lands on the same layout.
        """
        out: dict = {
            "table_col_widths":   [self.table.columnWidth(c)
                                   for c in range(self.table.columnCount())],
            "perchan_col_widths": [self.perchan.columnWidth(c)
                                   for c in range(self.perchan.columnCount())],
        }
        # Skip persisting splitter sizes if either pane has been
        # collapsed to zero — restoring the broken layout next
        # session would leave one side hidden.
        sp_sizes = list(self._geom_table_split.sizes())
        if all(int(s) > 0 for s in sp_sizes):
            out["geom_table_split"] = sp_sizes
        return out

    def restore_view_state(self, view: dict):
        if not view:
            return
        sizes = view.get("geom_table_split")
        if isinstance(sizes, (list, tuple)) and len(sizes) >= 2:
            try:
                int_sizes = [int(s) for s in sizes]
            except (TypeError, ValueError):
                int_sizes = None
            if int_sizes is not None and all(s > 0 for s in int_sizes):
                self._geom_table_split.setSizes(int_sizes)
        # Column widths — re-apply only if the count still matches the
        # current table; otherwise the stored layout is from a different
        # device geometry and shouldn't be force-fit onto this one.
        for key, table in (("table_col_widths", self.table),
                           ("perchan_col_widths", self.perchan)):
            widths = view.get(key)
            if not isinstance(widths, (list, tuple)):
                continue
            if len(widths) != table.columnCount():
                continue
            for c, w in enumerate(widths):
                try:
                    if int(w) > 0:
                        table.setColumnWidth(c, int(w))
                except (TypeError, ValueError):
                    pass
