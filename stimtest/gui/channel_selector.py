"""Interactive channel selector with multi-active + Global Return square.

Top of the widget is a small toolbar:

    [Select all]  [Clear actives]                [☐ Global Return]

Below that is the array-geometry canvas. Click a disk to toggle it as
an *active* (multiple actives are allowed). The Global Return button on
the right represents the external counter / remote return electrode
that's wired up off-array; toggling it on signals to the combination
panel that monopolar / partial-multipolar configurations are valid.

Public API
----------
* signal ``activesChanged(list[int])``        — actives changed
* signal ``globalReturnChanged(bool)``        — Global Return toggled
* ``actives()`` / ``set_actives(list)``
* ``global_return()`` / ``set_global_return(bool)``
* ``set_array(array, layout)``
* ``selection()``  — backward-compat shim returning (first_active, [])
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from ..electrode import ElectrodeArray


ACTIVE_FILL = QtGui.QColor("#e53935")        # bright red
ACTIVE_DONE_FILL = QtGui.QColor("#43a047")   # green when test succeeds
UNUSED_FILL = QtGui.QColor("#bdbdbd")
DISK_OUTLINE = QtGui.QColor("#212121")
GLOBAL_FILL_ON = QtGui.QColor("#fbc02d")     # amber when active
GLOBAL_FILL_OFF = QtGui.QColor("#eeeeee")
GLOBAL_BORDER = QtGui.QColor("#5d4037")


# ---------------------------------------------------------------------------
# Inner canvas — pure-paint grid widget (no toolbar)
# ---------------------------------------------------------------------------
class _GridCanvas(QtWidgets.QWidget):
    """Custom-painted electrode grid with click-to-toggle-active behaviour."""

    activesChanged = QtCore.pyqtSignal(list)        # list of active channel #s
    PAD = 10
    TRI_Y_FACTOR = 0.86602540378

    def __init__(self, array: ElectrodeArray, layout: str = "rect", parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 180)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Expanding)
        self._array: ElectrodeArray = array
        self._layout: str = layout
        self._actives: List[int] = []
        self._completed: Set[int] = set()
        self._centres: Dict[int, QtCore.QPoint] = {}
        self._radius: int = 14
        # Hover-highlight overlay — populated by the combination panel
        # when the user hovers a row in the combos list. ``hl_active``
        # gets a thick cyan ring; ``hl_returns`` get a thinner one.
        self._hl_active: Optional[int] = None
        self._hl_returns: List[int] = []

    # --- public API -------------------------------------------------------
    def set_array(self, array: ElectrodeArray, layout: str = "rect"):
        self._array = array
        self._layout = layout if layout in ("rect", "triangular") else "rect"
        valid = set(array.channel_numbers)
        self._actives = [n for n in self._actives if n in valid]
        self._completed = {n for n in self._completed if n in valid}
        self.update()
        self.activesChanged.emit(list(self._actives))

    def actives(self) -> List[int]:
        return list(self._actives)

    def set_actives(self, channels: List[int]):
        valid = set(self._array.channel_numbers)
        self._actives = [int(c) for c in channels if int(c) in valid]
        self.update()
        self.activesChanged.emit(list(self._actives))

    def set_completed(self, channels):
        self._completed = set(int(n) for n in channels)
        self.update()

    def clear_actives(self):
        self._actives.clear()
        self.update()
        self.activesChanged.emit([])

    def set_highlight(self, active: Optional[int], returns: Optional[List[int]] = None):
        """Overlay-paint a combination on top of the regular selection.

        Use ``active=None`` (and any ``returns``) to clear the highlight.
        Doesn't change the actives list — purely visual feedback for
        hover events from the combination panel.
        """
        self._hl_active = int(active) if active else None
        self._hl_returns = [int(n) for n in (returns or []) if int(n) > 0]
        self.update()

    # --- geometry ---------------------------------------------------------
    def _disk_centre(self, site, cell: int, x0: int, y0: int) -> QtCore.QPoint:
        triangular = (self._layout == "triangular")
        row_offset = (cell // 2) if (triangular and site.row % 2 == 1) else 0
        row_y_pitch = (cell * self.TRI_Y_FACTOR) if triangular else cell
        cx = x0 + site.col * cell + cell // 2 + row_offset
        cy = y0 + int(site.row * row_y_pitch) + cell // 2
        return QtCore.QPoint(cx, cy)

    def _layout_metrics(self) -> Tuple[int, int, int]:
        rows = max((s.row for s in self._array.sites), default=0) + 1
        cols = max((s.col for s in self._array.sites), default=0) + 1
        triangular = (self._layout == "triangular")
        x_footprint = cols + (0.5 if triangular and rows > 1 else 0)
        y_footprint = (rows - 1) * (self.TRI_Y_FACTOR if triangular else 1) + 1
        avail_w = max(1, self.width() - 2 * self.PAD)
        avail_h = max(1, self.height() - 2 * self.PAD)
        cell = min(avail_w / x_footprint, avail_h / y_footprint)
        cell = int(max(28, min(72, cell)))
        grid_w = int(cell * x_footprint)
        grid_h = int(cell * y_footprint)
        x0 = (self.width() - grid_w) // 2
        y0 = (self.height() - grid_h) // 2
        return cell, x0, y0

    # --- paint ------------------------------------------------------------
    def paintEvent(self, _ev):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QtGui.QColor("#fafafa"))
        if not self._array.sites:
            return
        cell, x0, y0 = self._layout_metrics()
        self._radius = int(cell * 0.40)
        self._centres = {}
        font = p.font(); font.setPointSize(max(8, cell // 5)); font.setBold(True); p.setFont(font)
        active_set = set(self._actives)
        for s in self._array.sites:
            c = self._disk_centre(s, cell, x0, y0)
            self._centres[s.number] = c
            if s.number in active_set:
                fill = ACTIVE_DONE_FILL if s.number in self._completed else ACTIVE_FILL
                outline_w = 2
            else:
                fill = UNUSED_FILL
                outline_w = 1
            p.setBrush(fill)
            p.setPen(QtGui.QPen(DISK_OUTLINE, outline_w))
            p.drawEllipse(c, self._radius, self._radius)
            text_colour = (QtCore.Qt.GlobalColor.white if s.number in active_set
                           else QtCore.Qt.GlobalColor.black)
            p.setPen(text_colour)
            rect = QtCore.QRect(c.x() - self._radius, c.y() - self._radius,
                                2 * self._radius, 2 * self._radius)
            p.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, str(s.number))

        # ---- Hover-highlight overlay ----
        # Drawn AFTER the disks so the rings sit on top of the fill.
        if self._hl_active is not None or self._hl_returns:
            hl_active_col = QtGui.QColor("#00bcd4")    # cyan ring for the active
            hl_return_col = QtGui.QColor("#9c27b0")    # purple ring for returns
            if self._hl_active is not None and self._hl_active in self._centres:
                c = self._centres[self._hl_active]
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.setPen(QtGui.QPen(hl_active_col, 4))
                p.drawEllipse(c, self._radius + 4, self._radius + 4)
            for n in self._hl_returns:
                c = self._centres.get(n)
                if c is None: continue
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.setPen(QtGui.QPen(hl_return_col, 3, QtCore.Qt.PenStyle.DashLine))
                p.drawEllipse(c, self._radius + 4, self._radius + 4)
        p.end()

    # --- mouse ------------------------------------------------------------
    def _hit(self, pos: QtCore.QPoint) -> Optional[int]:
        for n, c in self._centres.items():
            dx = pos.x() - c.x(); dy = pos.y() - c.y()
            if dx * dx + dy * dy <= (self._radius + 2) ** 2:
                return n
        return None

    def mousePressEvent(self, ev: QtGui.QMouseEvent):
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        n = self._hit(ev.pos())
        if n is None:
            return
        if ev.modifiers() & QtCore.Qt.KeyboardModifier.ControlModifier:
            # Ctrl-click — explicit remove
            if n in self._actives:
                self._actives.remove(n)
                self.update()
                self.activesChanged.emit(list(self._actives))
            return
        # Plain left-click — toggle active membership
        if n in self._actives:
            self._actives.remove(n)
        else:
            self._actives.append(n)
        self.update()
        self.activesChanged.emit(list(self._actives))


# ---------------------------------------------------------------------------
# Outer container — toolbar + canvas + Global Return square
# ---------------------------------------------------------------------------
class ChannelSelector(QtWidgets.QWidget):
    """Container widget: select-all / clear-actives / global-return + canvas."""

    activesChanged = QtCore.pyqtSignal(list)
    globalReturnChanged = QtCore.pyqtSignal(bool)

    def __init__(self, array: ElectrodeArray, layout: str = "rect", parent=None):
        super().__init__(parent)
        self._array = array

        # Toolbar
        self.btn_all = QtWidgets.QPushButton("Select all")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_all.clicked.connect(self._select_all)
        self.btn_clear.clicked.connect(self._clear_actives)

        # Global Return — styled square toggle button. Visually distinct
        # from the on-array disks so the user reads it as the off-array
        # counter electrode rather than an electrode they can pick.
        self.btn_global = QtWidgets.QPushButton("Global\nReturn")
        self.btn_global.setCheckable(True)
        # Most rigs run with the off-array counter wired up by default;
        # start with it active so monopolar / partial-* modes are
        # immediately available without a click.
        self.btn_global.setChecked(True)
        self.btn_global.setFixedSize(72, 72)
        self.btn_global.setToolTip(
            "Off-array counter / remote return electrode. Toggle on when a "
            "global return is wired up — required for monopolar and "
            "partial-multipolar configurations."
        )
        self._restyle_global(False)
        self.btn_global.toggled.connect(self._on_global_toggled)
        # Initialize the styled fill to match the default-checked state.
        self._restyle_global(self.btn_global.isChecked())
        # Broadcast the initial state once on the next tick so any
        # listener that subscribes to globalReturnChanged after this
        # widget is constructed (e.g. the experiment tab's CombinationPanel)
        # still sees the default-checked value.
        QtCore.QTimer.singleShot(
            0, lambda: self.globalReturnChanged.emit(self.btn_global.isChecked()))

        # Inner canvas
        self.canvas = _GridCanvas(array, layout=layout)
        self.canvas.activesChanged.connect(self.activesChanged.emit)

        # ----- layout -----
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addWidget(self.btn_all)
        toolbar.addWidget(self.btn_clear)
        toolbar.addStretch(1)

        body = QtWidgets.QHBoxLayout()
        body.addWidget(self.canvas, stretch=1)
        side = QtWidgets.QVBoxLayout()
        side.addStretch(1)
        side.addWidget(self.btn_global, alignment=QtCore.Qt.AlignmentFlag.AlignCenter)
        side.addStretch(1)
        body.addLayout(side, stretch=0)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.addLayout(toolbar)
        outer.addLayout(body, stretch=1)

    # --- styling ----------------------------------------------------------
    def _restyle_global(self, on: bool):
        col = "#fbc02d" if on else "#eeeeee"
        text_col = "white" if on else "#5d4037"
        self.btn_global.setStyleSheet(
            f"QPushButton {{ background:{col}; color:{text_col}; "
            f"border: 2px solid #5d4037; border-radius: 4px; font-weight: bold; }}"
        )

    # --- public API -------------------------------------------------------
    def set_array(self, array: ElectrodeArray, layout: str = "rect"):
        self._array = array
        self.canvas.set_array(array, layout=layout)

    def actives(self) -> List[int]:
        return self.canvas.actives()

    def set_actives(self, channels: List[int]):
        self.canvas.set_actives(channels)

    def set_completed(self, channels):
        self.canvas.set_completed(channels)

    def set_highlight(self, active, returns=None):
        """Forwarded to the inner canvas — see :meth:`_GridCanvas.set_highlight`."""
        self.canvas.set_highlight(active, returns)

    def global_return(self) -> bool:
        return self.btn_global.isChecked()

    def set_global_return(self, on: bool):
        self.btn_global.setChecked(bool(on))

    def selection(self) -> Tuple[Optional[int], List[int]]:
        """Backward-compat shim. Returns first active + an empty returns list.

        The combination panel now drives the (active, returns) build-out
        from the actives list and the global-return flag, so the legacy
        ``returns`` value here is intentionally empty.
        """
        actives = self.canvas.actives()
        return (actives[0] if actives else None), []

    # --- slots ------------------------------------------------------------
    def _select_all(self):
        self.canvas.set_actives(list(self._array.channel_numbers))

    def _clear_actives(self):
        self.canvas.clear_actives()

    def _on_global_toggled(self, on: bool):
        self._restyle_global(on)
        self.globalReturnChanged.emit(bool(on))
