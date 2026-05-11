"""Interactive channel selector with multi-active + External Return square.

Top of the widget is a small toolbar:

    [Select all]  [Clear actives]                [☐ External Return]

Below that is the array-geometry canvas. Click a disk to toggle it as
an *active* (multiple actives are allowed). The External Return button on
the right represents the external counter / remote return electrode
that's wired up off-array; toggling it on signals to the combination
panel that monopolar / partial-multipolar configurations are valid.

Public API
----------
* signal ``activesChanged(list[int])``        — actives changed
* signal ``globalReturnChanged(bool)``        — External Return toggled
* ``actives()`` / ``set_actives(list)``
* ``global_return()`` / ``set_global_return(bool)``
* ``set_array(array, layout)``
* ``selection()``  — backward-compat shim returning (first_active, [])
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Set, Tuple

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from ..electrode import ElectrodeArray


# --- Channel-selection palette (Okabe–Ito, colourblind-safe) ----------
# Selected (clicked) channels: BLUE.  Active highlight ring (the live
# combo's active electrode under hover): GREEN.  Return highlight ring
# AND External-Return ON: RED. The Okabe-Ito set is the standard
# choice for deuteranopia / protanopia accessibility. References:
# https://thenode.biologists.com/data-visualization-with-flying-colors/
ACTIVE_FILL = QtGui.QColor("#0072B2")        # Okabe blue (selected)
ACTIVE_DONE_FILL = QtGui.QColor("#009E73")   # Okabe bluish-green (completed)
UNUSED_FILL = QtGui.QColor("#bdbdbd")
DISK_OUTLINE = QtGui.QColor("#212121")
GLOBAL_FILL_ON = QtGui.QColor("#D55E00")     # Okabe vermilion (External Return on)
GLOBAL_FILL_OFF = QtGui.QColor("#eeeeee")
GLOBAL_BORDER = QtGui.QColor("#5d4037")
# Hover-highlight rings — drawn on top of the disk fills when the
# user mouses over a combo row in the configurations list.
HL_ACTIVE_RING = QtGui.QColor("#009E73")     # Okabe bluish-green (active ring)
HL_RETURN_RING = QtGui.QColor("#D55E00")     # Okabe vermilion (return ring)


# ---------------------------------------------------------------------------
# Inner canvas — pure-paint grid widget (no toolbar)
# ---------------------------------------------------------------------------
class _GridCanvas(QtWidgets.QWidget):
    """Custom-painted electrode grid with click-to-toggle-active behaviour."""

    activesChanged = QtCore.pyqtSignal(list)        # list of active channel #s
    #: Emitted whenever ``set_zoom`` actually changes the zoom factor.
    #: Container widget connects to this so its zoom-percent label
    #: reflects the live state without polling.
    zoomChanged = QtCore.pyqtSignal(float)
    PAD = 10
    TRI_Y_FACTOR = 0.86602540378

    #: Zoom-factor bounds. Min 0.25 keeps cells legible (~12 px);
    #: max 5.0 lets the user inspect a single channel at a glance.
    ZOOM_MIN = 0.25
    ZOOM_MAX = 5.0
    #: Multiplicative step applied per zoom-in / zoom-out click.
    #: 1.25× produces a smooth 6-step path from min→default→max
    #: (0.25 → 0.31 → 0.39 → 0.49 → 0.61 → 0.76 → 0.96 → 1.0 (reset)
    #: → 1.25 → 1.56 → …) and matches the convention used by web
    #: browsers' Ctrl+= zoom.
    ZOOM_STEP = 1.25
    #: Reference cell size (px) used for the canvas's ``sizeHint``
    #: at zoom = 1.0. Picked at the middle of the historical
    #: auto-fit clamp range (28-72 px) so a typical 5×4 array
    #: produces a ~250 × 190 px hint — small enough that
    #: setWidgetResizable(True) lets the QScrollArea expand the
    #: canvas to fill the viewport at zoom 1.0.
    _ZOOM_REFERENCE_CELL = 50.0

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
        # User-controlled zoom factor. 1.0 = auto-fit (current
        # behavior). Set via :meth:`set_zoom`; affects both
        # ``sizeHint`` (so a parent QScrollArea grows the widget
        # past the viewport when zoomed in, producing scrollbars)
        # and the post-auto-fit cell multiplier in
        # ``_layout_metrics`` (so zoom-out shrinks cells within
        # the widget without requiring the widget itself to
        # shrink below viewport — QScrollArea with
        # setWidgetResizable(True) doesn't allow that anyway).
        self._zoom: float = 1.0
        # Hover-highlight overlay — populated by the combination panel
        # when the user hovers a row in the combos list. ``hl_active``
        # gets a thick cyan ring; ``hl_returns`` get a thinner one. If
        # the user has enabled apply-spacing, ``hl_spacing_label`` is
        # the integer gap value as a string — the canvas stretches a
        # double-headed arrow between the active and each return with
        # that label centred on the line, so the chosen spacing is
        # legible without consulting the dropdown.
        self._hl_active: Optional[int] = None
        self._hl_returns: List[int] = []
        self._hl_spacing_label: str = ""

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

    def set_highlight(self, active: Optional[int],
                      returns: Optional[List[int]] = None,
                      spacing_label: str = ""):
        """Overlay-paint a combination on top of the regular selection.

        Use ``active=None`` (and any ``returns``) to clear the
        highlight. Doesn't change the actives list — purely visual
        feedback for hover events from the combination panel.

        ``spacing_label``, when non-empty, switches on a double-headed
        arrow drawn between the active and each return with the label
        centred on the line. Empty string suppresses the arrow.
        """
        self._hl_active = int(active) if active else None
        self._hl_returns = [int(n) for n in (returns or []) if int(n) > 0]
        self._hl_spacing_label = str(spacing_label or "")
        self.update()

    # --- zoom -------------------------------------------------------------
    def zoom(self) -> float:
        """Current zoom factor. 1.0 = auto-fit (default)."""
        return self._zoom

    def set_zoom(self, factor: float) -> None:
        """Clamp ``factor`` to ``[ZOOM_MIN, ZOOM_MAX]`` and apply it.

        Calls ``updateGeometry`` so a parent QScrollArea re-queries
        ``sizeHint`` and grows / shrinks the widget appropriately,
        then ``update`` to repaint with the new cell size. Emits
        :pyattr:`zoomChanged` only when the value actually changes
        — clicking zoom-in at the cap doesn't fire the signal.
        """
        new_z = max(self.ZOOM_MIN, min(self.ZOOM_MAX, float(factor)))
        if abs(new_z - self._zoom) < 1e-9:
            return
        self._zoom = new_z
        # ``updateGeometry`` is the canonical signal that sizeHint
        # may have changed; QScrollArea picks it up and resizes.
        self.updateGeometry()
        self.update()
        self.zoomChanged.emit(self._zoom)

    def zoom_in(self) -> None:
        """Step the zoom up by :data:`ZOOM_STEP`. Used by the
        toolbar button + keyboard shortcut."""
        self.set_zoom(self._zoom * self.ZOOM_STEP)

    def zoom_out(self) -> None:
        """Step the zoom down by :data:`ZOOM_STEP`."""
        self.set_zoom(self._zoom / self.ZOOM_STEP)

    def reset_zoom(self) -> None:
        """Snap back to 1.0 (auto-fit)."""
        self.set_zoom(1.0)

    def sizeHint(self) -> QtCore.QSize:
        """Preferred canvas size, scaled by the current zoom.

        Computed from the array's grid footprint × a reference
        cell size of :data:`_ZOOM_REFERENCE_CELL` × the zoom
        factor. With a parent QScrollArea using
        ``setWidgetResizable(True)``:

        * **zoom = 1.0** — sizeHint is small (~250 × 190 px for a
          5×4 array). Most viewports are larger, so the
          QScrollArea expands the canvas to fill the viewport
          and ``_layout_metrics`` auto-fits cells. Behaves
          identically to the pre-zoom build.
        * **zoom > 1.0** — sizeHint scales linearly. When it
          exceeds the viewport, QScrollArea shows scrollbars
          and the canvas renders at the larger size.
        * **zoom < 1.0** — sizeHint shrinks but
          ``setWidgetResizable(True)`` enforces a minimum of
          viewport size. The widget stays at viewport-size; the
          zoom-out effect comes from the cell multiplier in
          ``_layout_metrics`` instead.
        """
        rows = max((s.row for s in self._array.sites), default=0) + 1
        cols = max((s.col for s in self._array.sites), default=0) + 1
        triangular = (self._layout == "triangular")
        x_footprint = cols + (0.5 if triangular and rows > 1 else 0)
        y_footprint = (rows - 1) * (self.TRI_Y_FACTOR if triangular else 1) + 1
        cell = self._ZOOM_REFERENCE_CELL * self._zoom
        return QtCore.QSize(int(cell * x_footprint + 2 * self.PAD),
                            int(cell * y_footprint + 2 * self.PAD))

    # --- geometry ---------------------------------------------------------
    def _disk_centre(self, site, cell: int, x0: int, y0: int) -> QtCore.QPointF:
        """Sub-pixel centre for ``site`` on the cached layout grid.

        Layout ``"triangular"`` uses alternating offset-rows hex
        packing — even rows align; odd rows shift right by half a
        cell. Built-in devices like the MicroProbes FMA use this so
        the array's bounding box stays roughly rectangular.

        Returns ``QPointF`` so the equidistant hex math is preserved
        at sub-pixel accuracy — antialiasing renders the disks at the
        true grid positions with no visible asymmetry.
        """
        triangular = (self._layout == "triangular")
        cell_f = float(cell)
        if triangular:
            row_offset = (cell_f / 2.0) if (site.row % 2 == 1) else 0.0
        else:
            row_offset = 0.0
        row_y_pitch = cell_f * self.TRI_Y_FACTOR if triangular else cell_f
        cx = x0 + site.col * cell_f + cell_f / 2.0 + row_offset
        cy = y0 + site.row * row_y_pitch + cell_f / 2.0
        return QtCore.QPointF(cx, cy)

    def _layout_metrics(self) -> Tuple[int, int, int]:
        rows = max((s.row for s in self._array.sites), default=0) + 1
        cols = max((s.col for s in self._array.sites), default=0) + 1
        triangular = (self._layout == "triangular")
        x_footprint = cols + (0.5 if triangular and rows > 1 else 0)
        y_footprint = (rows - 1) * (self.TRI_Y_FACTOR if triangular else 1) + 1
        avail_w = max(1, self.width() - 2 * self.PAD)
        avail_h = max(1, self.height() - 2 * self.PAD)
        # Auto-fit cell size — fills the widget bounding box. At
        # zoom = 1.0 this matches the pre-zoom behavior. The
        # historical 28-72 px clamp is now an 8-400 px clamp so
        # zoom-in (which already grew the widget via sizeHint)
        # can produce big cells, and zoom-out (which kept the
        # widget at viewport size but applies the multiplier
        # below) can produce small cells.
        auto_cell = min(avail_w / x_footprint, avail_h / y_footprint)
        # Zoom-out path: with setWidgetResizable(True), the widget
        # can't shrink below the QScrollArea's viewport. Instead
        # we shrink the cells WITHIN the widget by the zoom
        # multiplier — leaves whitespace around the grid but
        # makes "0.5×" actually look like 0.5×. Zoom-in path: the
        # widget already grew to ``sizeHint`` (which scales with
        # zoom), so the auto-fit naturally produces cells that
        # are zoom × the zoom-1 size — no extra multiplier here.
        if self._zoom < 1.0:
            cell = auto_cell * self._zoom
        else:
            cell = auto_cell
        cell = int(max(8, min(400, cell)))
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
            # QRectF accepts float coords; centres are now sub-pixel
            # so the text rect must be float-valued too.
            rect = QtCore.QRectF(c.x() - self._radius, c.y() - self._radius,
                                 2 * self._radius, 2 * self._radius)
            p.drawText(rect, QtCore.Qt.AlignmentFlag.AlignCenter, str(s.number))

        # ---- Hover-highlight overlay ----
        # Drawn AFTER the disks so the rings sit on top of the fill.
        # Layer order: spacing arrow first (so the strong active and
        # return rings can overdraw any arrow tip that brushes the
        # disk edge), then returns, then the active.
        if (self._hl_active is not None or self._hl_returns):
            hl_active_col = HL_ACTIVE_RING
            hl_return_col = HL_RETURN_RING
            # Spacing arrows — drawn first so the rings overpaint any
            # tip that happens to brush the disk edge.
            if (self._hl_spacing_label
                    and self._hl_active is not None
                    and self._hl_active in self._centres):
                src = self._centres[self._hl_active]
                for n in self._hl_returns:
                    dst = self._centres.get(n)
                    if dst is None: continue
                    self._draw_spacing_arrow(p, src, dst,
                                             self._hl_spacing_label)
            for n in self._hl_returns:
                c = self._centres.get(n)
                if c is None: continue
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.setPen(QtGui.QPen(hl_return_col, 3, QtCore.Qt.PenStyle.DashLine))
                p.drawEllipse(c, self._radius + 4, self._radius + 4)
            if self._hl_active is not None and self._hl_active in self._centres:
                c = self._centres[self._hl_active]
                p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                p.setPen(QtGui.QPen(hl_active_col, 4))
                p.drawEllipse(c, self._radius + 4, self._radius + 4)
        p.end()

    # --- spacing arrow ----------------------------------------------------
    def _draw_spacing_arrow(self, p: QtGui.QPainter,
                            p1: QtCore.QPoint, p2: QtCore.QPoint,
                            label: str):
        """Stretch a double-headed arrow from ``p1`` to ``p2`` with
        ``label`` rendered on a white pill in the middle of the line.

        Both arrow tips are pulled in by ``self._radius + 4`` so they
        touch the outer edge of the active/return highlight rings
        rather than overlapping the disks. Arrowheads are short
        isosceles triangles pointing AT the disks (so the arrow visually
        connects the active to the return — like ◀──N──▶).
        """
        color = QtGui.QColor("#1976d2")   # Material blue 700 — pops on grey/white
        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.hypot(dx, dy)
        if length < 1.0:
            return
        ux = dx / length
        uy = dy / length
        # Pull both endpoints in past the disk + highlight ring so the
        # arrow touches the ring's outer edge but doesn't overlap.
        margin = self._radius + 4
        sx = p1.x() + ux * margin
        sy = p1.y() + uy * margin
        ex = p2.x() - ux * margin
        ey = p2.y() - uy * margin
        if math.hypot(ex - sx, ey - sy) < 6.0:
            # Endpoints overlap once we account for the margins —
            # nothing meaningful to draw.
            return

        p.save()
        pen = QtGui.QPen(color, 2)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.setBrush(color)

        # Main shaft.
        p.drawLine(QtCore.QPointF(sx, sy), QtCore.QPointF(ex, ey))

        # Arrowheads at each end. Tips touch the disk-side endpoint;
        # bases sit ``head_len`` along the shaft toward the centre,
        # spread perpendicularly by ``head_w``.
        head_len = 9.0
        head_w   = 5.0
        nx = -uy   # perpendicular unit vector
        ny =  ux

        # Tail-end head (at p1 / active side) — tip at (sx,sy).
        tail_tip = QtCore.QPointF(sx, sy)
        tail_b1  = QtCore.QPointF(sx + ux * head_len + nx * head_w,
                                  sy + uy * head_len + ny * head_w)
        tail_b2  = QtCore.QPointF(sx + ux * head_len - nx * head_w,
                                  sy + uy * head_len - ny * head_w)
        p.drawPolygon(QtGui.QPolygonF([tail_tip, tail_b1, tail_b2]))

        # Head-end head (at p2 / return side) — tip at (ex,ey).
        head_tip = QtCore.QPointF(ex, ey)
        head_b1  = QtCore.QPointF(ex - ux * head_len + nx * head_w,
                                  ey - uy * head_len + ny * head_w)
        head_b2  = QtCore.QPointF(ex - ux * head_len - nx * head_w,
                                  ey - uy * head_len - ny * head_w)
        p.drawPolygon(QtGui.QPolygonF([head_tip, head_b1, head_b2]))

        # Centred label on a translucent-white pill so the shaft
        # doesn't bleed through the digits.
        if label:
            mid_x = (sx + ex) / 2.0
            mid_y = (sy + ey) / 2.0
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            fm = p.fontMetrics()
            text_w = fm.horizontalAdvance(label)
            text_h = fm.height()
            pad_x = 5
            pad_y = 2
            bg = QtCore.QRectF(mid_x - text_w / 2.0 - pad_x,
                               mid_y - text_h / 2.0 - pad_y,
                               text_w + 2 * pad_x,
                               text_h + 2 * pad_y)
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(QtGui.QColor(255, 255, 255, 235))
            p.drawRoundedRect(bg, 4, 4)
            p.setPen(color)
            p.drawText(bg, QtCore.Qt.AlignmentFlag.AlignCenter, label)
        p.restore()

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
# Outer container — toolbar + canvas + External Return square
# ---------------------------------------------------------------------------
class ChannelSelector(QtWidgets.QWidget):
    """Container widget: select-all / clear-actives / global-return + canvas."""

    activesChanged = QtCore.pyqtSignal(list)
    globalReturnChanged = QtCore.pyqtSignal(bool)

    def __init__(self, array: ElectrodeArray, layout: str = "rect", parent=None):
        super().__init__(parent)
        self._array = array

        # Toolbar — Select all / Clear on the left, then a zoom
        # group (zoom-out, percent label, zoom-in, reset). The
        # zoom group lives on the same row so the user reaches
        # it without leaving the channel-selector area.
        self.btn_all = QtWidgets.QPushButton("Select all")
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_all.clicked.connect(self._select_all)
        self.btn_clear.clicked.connect(self._clear_actives)

        # Zoom controls. Step is multiplicative (1.25× per click)
        # so successive clicks scale geometrically — matches web-
        # browser zoom UX. Both keyboard shortcuts (Ctrl+= /
        # Ctrl+-) and toolbar buttons drive the same underlying
        # ``set_zoom`` on the canvas.
        self.btn_zoom_out = QtWidgets.QPushButton("−")
        self.btn_zoom_out.setFixedWidth(28)
        self.btn_zoom_out.setToolTip(
            "Zoom out (Ctrl+-) — shrink the channel grid. Useful "
            "when a large multi-channel array doesn't fit at "
            "default size.")
        self.btn_zoom_in = QtWidgets.QPushButton("+")
        self.btn_zoom_in.setFixedWidth(28)
        self.btn_zoom_in.setToolTip(
            "Zoom in (Ctrl+=) — magnify the channel grid. "
            "When the zoomed grid exceeds the visible area, "
            "scrollbars appear so the user can pan around.")
        self.btn_zoom_reset = QtWidgets.QPushButton("Reset view")
        self.btn_zoom_reset.setToolTip(
            "Reset view (Ctrl+0) — restore the default channel-"
            "selection layout: zoom 100% (auto-fit so the entire "
            "device fills the visible area) AND scroll position "
            "to top-left so the whole grid is visible without "
            "panning. Use after zooming in / scrolling around to "
            "snap back to the canonical view.")
        # Live readout — updates whenever zoom changes (signal hook
        # added below). Width fixed so the surrounding layout
        # doesn't reflow as the percent string shrinks / grows
        # (e.g. "100%" → "62%").
        self.zoom_label = QtWidgets.QLabel("100%")
        self.zoom_label.setFixedWidth(48)
        self.zoom_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setStyleSheet("color: #555;")

        # External Return — styled square toggle button. Visually
        # distinct from the on-array disks so the user reads it as
        # the external counter electrode rather than an electrode
        # they can pick.
        self.btn_global = QtWidgets.QPushButton("External\nReturn")
        self.btn_global.setCheckable(True)
        # Most rigs run with the external counter wired up by default;
        # start with it active so monopolar / partial-* modes are
        # immediately available without a click.
        self.btn_global.setChecked(True)
        self.btn_global.setFixedSize(72, 72)
        self.btn_global.setToolTip(
            "External counter / remote return electrode. Toggle on "
            "when an external return is wired up — required for "
            "monopolar and partial-multipolar configurations."
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
        # Live percent readout — updates whenever the canvas's
        # zoom changes (incl. via keyboard shortcuts that
        # bypass the toolbar buttons).
        self.canvas.zoomChanged.connect(self._on_zoom_changed)
        # Wire toolbar buttons to canvas methods. ``Reset view``
        # goes through ``_reset_view`` rather than the bare
        # ``canvas.reset_zoom`` so the scroll position also
        # snaps back to (0, 0) — otherwise the user could be
        # scrolled deep into a zoomed grid, hit Reset, and end
        # up at zoom 100% but viewing an empty corner of the
        # canvas because the scroll offset was never cleared.
        self.btn_zoom_in.clicked.connect(self.canvas.zoom_in)
        self.btn_zoom_out.clicked.connect(self.canvas.zoom_out)
        self.btn_zoom_reset.clicked.connect(self._reset_view)

        # Keyboard shortcuts. ``Ctrl+=`` is the natural "zoom in"
        # binding (and most keyboards' Ctrl++ requires Shift, so
        # we accept both). ``Ctrl+-`` is the matching zoom out;
        # ``Ctrl+0`` resets the FULL view (zoom + scroll).
        # Parented to the outer container so the shortcuts work
        # regardless of which widget inside the channel selector
        # has focus.
        for keystroke, slot in (
            ("Ctrl++", self.canvas.zoom_in),
            ("Ctrl+=", self.canvas.zoom_in),
            ("Ctrl+-", self.canvas.zoom_out),
            ("Ctrl+0", self._reset_view),
        ):
            sc = QtGui.QShortcut(QtGui.QKeySequence(keystroke), self)
            sc.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(slot)

        # Scroll area — wraps the canvas so zoom-in beyond the
        # viewport produces scrollbars instead of clipping. With
        # ``setWidgetResizable(True)``, the canvas always fills
        # at least the viewport (so zoom = 1.0 still auto-fits)
        # but can grow past it when sizeHint scales up.
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidget(self.canvas)
        self.scroll.setWidgetResizable(True)
        # Plain frame — the surrounding QGroupBox already gives
        # the channel-selection panel its own border, so a
        # second sunken frame around the scroll area would
        # double up.
        self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        # ----- layout -----
        toolbar = QtWidgets.QHBoxLayout()
        toolbar.addWidget(self.btn_all)
        toolbar.addWidget(self.btn_clear)
        toolbar.addStretch(1)
        # Zoom group on the right side of the toolbar, mirroring
        # the typical "viewer-toolbar" pattern (file actions on
        # the left, view-state controls on the right).
        toolbar.addWidget(self.btn_zoom_out)
        toolbar.addWidget(self.zoom_label)
        toolbar.addWidget(self.btn_zoom_in)
        toolbar.addWidget(self.btn_zoom_reset)

        body = QtWidgets.QHBoxLayout()
        body.addWidget(self.scroll, stretch=1)
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
        # Use the same Okabe vermilion as the return-highlight ring so
        # the visual language is consistent: vermilion = "this is a
        # return path". White text reads cleanly on the saturated
        # vermilion; brown text reads on the off-state light grey.
        col = GLOBAL_FILL_ON.name() if on else GLOBAL_FILL_OFF.name()
        text_col = "white" if on else GLOBAL_BORDER.name()
        self.btn_global.setStyleSheet(
            f"QPushButton {{ background:{col}; color:{text_col}; "
            f"border: 2px solid {GLOBAL_BORDER.name()}; "
            f"border-radius: 4px; font-weight: bold; }}"
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

    def set_highlight(self, active, returns=None, spacing_label=""):
        """Forwarded to the inner canvas — see :meth:`_GridCanvas.set_highlight`."""
        self.canvas.set_highlight(active, returns, spacing_label)

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

    def _on_zoom_changed(self, factor: float):
        """Update the zoom-percent readout when the canvas's
        zoom factor changes. Called from
        :pyattr:`_GridCanvas.zoomChanged`, so a value reached
        via either the toolbar buttons or the keyboard shortcuts
        produces the same readout.
        """
        # Round to whole-number percent — matches browser zoom
        # convention and keeps the label width predictable.
        self.zoom_label.setText(f"{int(round(factor * 100))}%")

    def _reset_view(self):
        """Restore the default channel-selection view.

        Two-part reset, matching what a user reasonably expects
        from a "Reset view" button:

        1. Zoom factor → 1.0 (auto-fit; the entire device fills
           the available area without scrollbars at typical
           viewport sizes).
        2. QScrollArea scroll position → (0, 0) so the user
           lands looking at the top-left of the grid even when
           they had scrolled far away during a previous zoom-in
           session.

        ``canvas.reset_zoom`` is a no-op when zoom is already
        1.0; we still call ``ensureVisible`` so the
        scroll-position reset runs unconditionally — a user who
        merely panned around (without zooming) still gets their
        view snapped back.
        """
        self.canvas.reset_zoom()
        # Park the scroll bars at zero. ``QScrollArea.horizontalScrollBar``
        # / ``verticalScrollBar`` always return non-None objects (even
        # when the bars are hidden because nothing's overflowing),
        # so the calls are safe regardless of zoom level.
        self.scroll.horizontalScrollBar().setValue(0)
        self.scroll.verticalScrollBar().setValue(0)
