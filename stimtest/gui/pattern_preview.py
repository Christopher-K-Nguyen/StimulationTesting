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

import math

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from ..waveforms import (
    PulsePattern, SHAPE_RECTANGULAR, SHAPE_EXP_DECAY, shape_breakpoints,
)


def _saturated_cap_coupled_pair(pattern: "PulsePattern") -> Optional[int]:
    """Index ``i`` of a saturated-cap-coupled flat+decay phase pair.

    The saturated pseudo-capacitively-coupled solver builds the
    anodic side as TWO consecutive Phases — a rectangular flat-top
    pinned at the 1000-µA hardware ceiling, immediately followed
    (no interphase delay) by an exp-decay carrying the remaining
    charge. Internally that's two ``Phase`` records because the
    device needs distinct (amp, dur) pairs in the .pat to render
    both segments. Visually it's ONE conceptual second phase: a
    single anodic recharge whose envelope happens to start flat
    and then decay.

    For per-phase labelling (amplitude / width / Q_ph) we want to
    surface the conceptual single-phase view, not the device-level
    two-phase split. This helper detects the signature so the
    callers can collapse the rendering.

    Returns the index ``i`` such that phases[i] is the flat-top
    rect and phases[i+1] is the decay tail. Returns ``None`` when
    no such pair exists (which is the common case — non-saturated
    cap-coupled, rect-asym, biphasic, triphasic, etc.).

    Match conditions:
      * phases[i].shape == SHAPE_RECTANGULAR
      * phases[i+1].shape == SHAPE_EXP_DECAY
      * phases[i].delay_after_us == 0 (flat → decay is contiguous)
      * sign(phases[i].amplitude_ua) == sign(phases[i+1].amplitude_ua)
        and the magnitudes match within 1 µA (both pinned at the
        hardware ceiling). The sign-and-magnitude check rules out
        an accidental match in unrelated patterns where a rect
        anodic happens to precede an exp-decay cathodic.
    """
    if pattern is None or not getattr(pattern, "phases", None):
        return None
    phases = pattern.phases
    for i in range(len(phases) - 1):
        a = phases[i]
        b = phases[i + 1]
        if (a.shape == SHAPE_RECTANGULAR
                and b.shape == SHAPE_EXP_DECAY
                and float(a.delay_after_us) == 0.0
                and (a.amplitude_ua >= 0) == (b.amplitude_ua >= 0)
                and abs(a.amplitude_ua - b.amplitude_ua) < 1.0):
            return i
    return None
from . import rich


# Charge-imbalance tolerance: if |Q_net|/Q_max > this, show a warning.
# 5% mirrors the threshold used by ``checkBalance.m`` in the MATLAB code.
CHARGE_BALANCE_TOL = 0.05

# Unified font size for ALL plot text — axis labels, tick labels,
# legend, and every TextItem annotation. Keeping one number makes the
# preview read as a single composed figure rather than three different
# typographic systems competing for attention.
PLOT_FONT_PT = 11


class _LeaderArrow(QtWidgets.QGraphicsItem):
    """A leader line that ends in an isosceles-triangle arrowhead,
    drawn by a single ``paint()`` call inside a single
    :class:`QGraphicsItem`.

    The implementation follows Qt's own DiagramScene example
    (``Arrow`` class — https://doc.qt.io/archives/qt-5.15/qtwidgets-graphicsview-diagramscene-example.html ):
    ``paint()`` issues a ``drawLine`` for the line, computes the
    arrowhead geometry from the line angle, and then issues a
    ``drawPolygon`` for the filled head — both inside the same
    graphics item, with the line stroked by the pen and the head
    filled by the brush.

    **Aspect-ratio compensation** — the plot's data axes have
    different physical units (µs vs µA), so a triangle whose two
    "tip-to-wing" sides are equal in *data* units would look skewed
    on screen. To make the triangle *visually* isosceles regardless
    of zoom, the wing endpoints are computed in pixel coordinates
    inside ``paint()`` (using the painter's local-to-device
    transform) and converted back into data coordinates only at the
    very last step, when the polygon is fed to ``drawPolygon``. The
    head-length is also expressed in pixels so the arrow stays a
    consistent visual size at every zoom.
    """

    def __init__(self,
                 x_text: float, y_text: float,
                 x_target: float, y_target: float,
                 color: str,
                 head_size: float = 25.0,
                 half_tip_deg: float = 22.0,
                 line_width: float = 2.0,
                 parent=None):
        super().__init__(parent)
        # Use plain (x, y) floats internally — converted to QPointF
        # at draw time.
        self._tx, self._ty = float(x_text), float(y_text)
        self._px, self._py = float(x_target), float(y_target)
        self._qcolor = QtGui.QColor(color)
        # Hint at the arrowhead's data-coord size — used only to
        # size the bounding rect generously. The actual arrowhead
        # geometry is computed in pixel space inside ``paint()``.
        self._head_hint = float(head_size)
        self._half_tip_rad = math.radians(half_tip_deg)
        self._line_width = float(line_width)
        # Bounding rect — generous halo so the painter clip never
        # crops the head, regardless of where the wings land. The
        # halo scales with the leader length so even at heavy zooms
        # the bounds usually cover the (now-pixel-sized) arrowhead;
        # if the user zooms in extremely close the head may briefly
        # extend outside the bounds, with the only consequence being
        # a one-frame redraw delay on pan.
        leader_data = math.hypot(self._tx - self._px, self._ty - self._py)
        halo = max(self._head_hint, leader_data * 0.30)
        x_min = min(self._tx, self._px) - halo
        x_max = max(self._tx, self._px) + halo
        y_min = min(self._ty, self._py) - halo
        y_max = max(self._ty, self._py) + halo
        self._bounds = QtCore.QRectF(
            x_min, y_min, x_max - x_min, y_max - y_min)

    def boundingRect(self) -> QtCore.QRectF:
        return self._bounds

    def paint(self, painter: QtGui.QPainter, option, widget=None):
        # The painter's transform maps LOCAL (data) coords to DEVICE
        # (pixel) coords. m11 = pixels-per-data-x, m22 =
        # pixels-per-data-y; m22 is typically negative for y-up
        # plots since screen y grows downward.
        transform = painter.transform()
        sx = transform.m11()
        sy = transform.m22()
        if abs(sx) < 1e-9 or abs(sy) < 1e-9:
            return
        # Leader vector in DATA coords.
        data_dx = self._tx - self._px
        data_dy = self._ty - self._py
        if math.hypot(data_dx, data_dy) < 1e-12:
            return
        # Same vector in SCREEN coords. Working in pixels lets the
        # arrowhead come out as a true isosceles triangle on screen
        # regardless of the µs/µA aspect ratio.
        screen_dx = data_dx * sx
        screen_dy = data_dy * sy
        screen_len = math.hypot(screen_dx, screen_dy)
        if screen_len < 1e-6:
            return
        sux = screen_dx / screen_len
        suy = screen_dy / screen_len
        # Head length in pixels — proportional to the screen-space
        # leader length (so long leaders get bigger heads), floored
        # at 10 px so a short leader still has a visible head.
        head_px = max(10.0, screen_len * 0.18)
        cos_a = math.cos(self._half_tip_rad)
        sin_a = math.sin(self._half_tip_rad)
        # Wing offsets in SCREEN coords — both at distance head_px
        # from the tip, splayed ±half_tip from the leader direction.
        # Equal screen-space magnitude is what makes the triangle
        # visually isosceles.
        w1_sx = head_px * (sux * cos_a - suy * sin_a)
        w1_sy = head_px * (sux * sin_a + suy * cos_a)
        w2_sx = head_px * (sux * cos_a + suy * sin_a)
        w2_sy = head_px * (-sux * sin_a + suy * cos_a)
        # Convert wing OFFSETS back to data coords (using the same
        # signs of ``sx`` / ``sy`` so the wings end up in the same
        # visual quadrant the screen-space rotation placed them).
        w1_dx = w1_sx / sx
        w1_dy = w1_sy / sy
        w2_dx = w2_sx / sx
        w2_dy = w2_sy / sy
        # Stroke the line (cosmetic pen — constant pixel width).
        pen = QtGui.QPen(self._qcolor, self._line_width)
        pen.setCosmetic(True)
        pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.drawLine(QtCore.QPointF(self._tx, self._ty),
                         QtCore.QPointF(self._px, self._py))
        # Fill the isosceles triangle — NoPen so the polygon doesn't
        # carry a double-stroked outline that visually thickens it.
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QBrush(self._qcolor))
        head = QtGui.QPolygonF([
            QtCore.QPointF(self._px + w1_dx, self._py + w1_dy),
            QtCore.QPointF(self._px,         self._py),
            QtCore.QPointF(self._px + w2_dx, self._py + w2_dy),
        ])
        painter.drawPolygon(head)


class PatternPreview(QtWidgets.QWidget):
    """Live preview of the stim pulse with charge-balance check."""

    # Compact default — the preview should NOT dominate the parameters
    # page vertically. The parent ``params_box`` is already wrapped in
    # a ``QScrollArea`` (see ``_assemble_pages``); if the preview takes
    # all available height, the form rows below it (rate, polarity,
    # arbitrary table, etc.) get pushed off-screen. With a modest
    # ``sizeHint`` and a ``Preferred`` vertical policy the scroll area
    # provides a vertical bar exactly when the page overflows.
    MIN_HEIGHT = 120
    PREFERRED_HEIGHT = 150
    #: The PRIOR default height.  A saved view-state at exactly this value
    #: means the user never resized the preview, so it's treated as "use the
    #: current default" on restore (operator: "reduce the default height of
    #: the test parameter plot") — without this, an old saved 200 would keep
    #: overriding the smaller default.
    _LEGACY_DEFAULT_HEIGHT = 200
    #: Hard upper bound on the user-stretched height — keeps the
    #: preview from running off the screen on small monitors when the
    #: user drags hard.
    MAX_HEIGHT = 800
    #: Number of full pulse cycles drawn left-to-right in the preview.
    #: Centered around k=0 (so the central pulse sits at t=0), so an
    #: odd value places the same number of neighbour pulses on each
    #: side. Set high (21 = 10 each side of centre) so the train
    #: feels continuous when the user wheel-zooms out or pans — they
    #: can scroll through ~20 periods of cadence before hitting the
    #: data edge. Per-pulse annotations are drawn for the CENTRAL
    #: pulse only so the extra tiles don't clutter the labels.
    N_PULSES_DRAWN = 21

    #: In BURST mode each tiled unit is a whole burst (itself a mini-train of
    #: N pulses), so we cap the number of BURSTS drawn to keep the total
    #: point count bounded (N_PULSES_DRAWN // pulses_per_burst, ≤ this).
    N_BURSTS_DRAWN = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        # Horizontal Expanding (fill the column), vertical Preferred
        # (settle at sizeHint, let the QScrollArea handle overflow).
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Preferred)

        # ----- header / warning bar -----
        self.header = QtWidgets.QLabel()
        self.header.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.header.setWordWrap(True)
        self.header.setStyleSheet("padding: 4px;")
        # Hover tooltip — explains every entry the summary line (and
        # the optional charge-imbalance warning banner above it) can
        # render. Qt's QLabel only supports one tooltip per widget,
        # so we surface all the field meanings in a single
        # comprehensive tooltip rather than per-region hovers. Set
        # once at construction; the field labels themselves never
        # change so the tooltip text doesn't need to be refreshed
        # whenever the pattern is re-rendered.
        self.header.setToolTip(
            "<b>Charge-balance summary fields</b><br><br>"
            "<b>Q_ph</b> — charge per phase (nC). Absolute charge "
            "actually delivered in each phase, integrated on the "
            "discrete 30 nA + sample-and-hold staircase the device "
            "plays (not the analytic ideal). Cathodic and anodic "
            "phases are listed in delivery order with sign; a "
            "well-balanced biphasic pair has equal magnitudes and "
            "opposite signs.<br><br>"
            "<b>Q_net</b> — net residual charge per pulse (pC). "
            "Sum of Q_ph across all phases. Ideally zero (perfect "
            "charge balance); a non-zero residual accumulates at the "
            "electrode-tissue interface every pulse and can drive "
            "irreversible faradaic reactions and electrode "
            "degradation over many pulses.<br><br>"
            "<b>(X.XXX%)</b> next to Q_net — charge imbalance "
            "percentage, defined as |Q_net| / max(|Q_ph|) × 100. "
            "0 % = perfect balance, 100 % = total mismatch. "
            "Above 5 % the header turns red and a "
            "<i>charge imbalance</i> warning banner is added.<br><br>"
            "<b>rate</b> — pulse repetition rate (pulses per "
            "second).<br><br>"
            "<b>period</b> — time between consecutive pulse starts. "
            "Equal to 1 / rate.<br><br>"
            "<b>pulse</b> — total duration of one pulse including "
            "all phases and their inter-phase delays. Must be ≤ "
            "period or the next pulse begins before this one "
            "finishes.<br><br>"
            "<b>tol ≤ 5 %</b> (in the warning banner) — the "
            "imbalance tolerance the header trips on. The 5 % "
            "default is a conservative starting threshold for safe "
            "neural stimulation; tighter is better and most "
            "well-designed biphasic patterns sit at &lt; 0.1 %."
        )
        # Whether the charge-balance summary should ever appear. The
        # owning panel toggles this whenever the user picks a mode where
        # they have no manual control over balance (symmetric biphasic,
        # triphasic, or auto-adjust on) — the header is moot then.
        self._show_balance: bool = True

        # ----- plot -----
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        # Mouse wheel must NOT zoom (operator request) — pan via the
        # scrollbars / drag and zoom via the X±/Y± buttons instead.
        from .widgets import disable_plot_wheel_zoom
        disable_plot_wheel_zoom(self.plot)
        # Gridlines default OFF on experiment plots so subtle trace
        # features aren't obscured. The main window's View →
        # Gridlines action toggles them on/off for every experiment
        # plot at once via :meth:`set_grid_visible`. The Viewer has
        # its own independent toggle.
        self._grid_visible: bool = False
        self.plot.showGrid(x=False, y=False)
        # Axis labels — plain "Time" / "Current" with the unit in
        # square brackets. Earlier versions used italic ``<i>t</i>`` /
        # ``<i>I</i><sub>stim</sub>`` which read as "math notation"
        # rather than "x / y axis title"; the user-spec'd plain words
        # match the rest of the GUI's labelling convention.
        # ``font-size`` styles the axis-title text via pyqtgraph's
        # CSS-kwarg mechanism (kwargs after the label string become
        # inline style on the rendered <span>).
        label_style = {"font-size": f"{PLOT_FONT_PT}pt", "color": "#000"}
        self.plot.setLabel("bottom", f"Time [{rich.US}]", **label_style)
        self.plot.setLabel("left",   f"Current [{rich.UA}]", **label_style)
        # Tick labels use a separate font knob — pyqtgraph reads the
        # ``tickFont`` style entry off each AxisItem. Sized to match
        # the axis title so the ticks don't look smaller than the
        # label they live under.
        tick_font = QtGui.QFont()
        tick_font.setPointSize(PLOT_FONT_PT)
        for ax_name in ("bottom", "left"):
            ax = self.plot.getAxis(ax_name)
            if ax is not None:
                ax.setStyle(tickFont=tick_font)
        # Suppress the y-axis minor ticks.  pyqtgraph's default
        # AxisItem returns three tick levels (major + minor + sub-
        # minor) from ``tickValues``; the inner two render as small
        # tick marks between the labelled major ticks.  Override
        # ``tickValues`` on the left axis to expose ONLY the major
        # (first) level so the y-axis shows clean integer-µA ticks
        # without the busy minor-tick hair between them.  Bottom
        # axis is left untouched — minor ticks on the time axis
        # help judge phase widths.
        _y_axis = self.plot.getAxis("left")
        if _y_axis is not None:
            _orig_tick_values = _y_axis.tickValues
            def _major_only_y(minVal, maxVal, size, _orig=_orig_tick_values):
                levels = _orig(minVal, maxVal, size)
                return levels[:1] if levels else levels
            _y_axis.tickValues = _major_only_y
        # User can pan/zoom both axes — wheel + drag works on either.
        # The auto-fit y-range still gets set in set_pattern() so the
        # initial view covers the amp span; the user can then scroll
        # / zoom to inspect specific regions (useful when the
        # exp-decay tail is in the noise floor relative to peak).
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        vb = self.plot.getViewBox()
        if vb is not None:
            vb.setMouseEnabled(x=True, y=True)
            vb.setMenuEnabled(False)
            # Re-emit X-range changes so the scroll bar can mirror them.
            vb.sigXRangeChanged.connect(self._on_view_range_changed)
        # y=0 reference rule — DASHED so it reads as "this is the
        # zero-current axis line, not part of the waveform". Light
        # grey + thin so it never competes with the active phases or
        # the orange discharge marker that also sits at y=0. Per user
        # request — the previous solid version was removed; this dashed
        # version is the compromise that makes the baseline visible
        # without the heavy black rule that cluttered tall y-zooms.
        self.plot.addLine(y=0, pen=pg.mkPen("#888", width=1,
                                            style=QtCore.Qt.PenStyle.DashLine))
        # Add the legend BEFORE the curves so pyqtgraph picks up the
        # `name=` arguments on each plot() call automatically. If the
        # legend is added after, the entries don't appear unless we
        # manually call legend.addItem() per curve. Offset (-10, 10)
        # anchors top-right of the plot area with a small inset.
        # ``labelTextSize`` matches the unified plot font size so the
        # legend reads at the same weight as the axis labels.
        self._legend = self.plot.addLegend(offset=(-10, 10),
                                           labelTextSize=f'{PLOT_FONT_PT}pt')
        # Two traces — Actual (sample-and-hold staircase, drawn first
        # so the smooth Desired overlay sits on top) is what the
        # device delivers after 30 nA quantisation; Desired is the
        # mathematical ideal. For piecewise-rectangular shapes
        # (rectangular, speedbumps) the two coincide; for curved
        # shapes the staircase shows the discrete approximation.
        self.curve_actual = self.plot.plot(
            pen=pg.mkPen("#9e9e9e", width=2),
            name="Actual",
        )
        self.curve = self.plot.plot(
            pen=pg.mkPen("#1976d2", width=2),
            name="Desired",
        )

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

        # Debounce for the patternChanged signal path. Multiple rapid
        # emits within the timer's interval coalesce into ONE render.
        # Critical when the user types into amplitude / width spinboxes
        # (each keystroke fires patternChanged on the panel, and the
        # full remove + rebuild of phase labels stutters perceptibly
        # without debounce). 60 ms is below the human flicker
        # threshold but long enough to absorb typical keystroke
        # timing (~80-150 ms inter-stroke). Tests call
        # ``set_pattern`` directly and keep the synchronous path.
        self._render_debounce = QtCore.QTimer(self)
        self._render_debounce.setSingleShot(True)
        self._render_debounce.setInterval(60)
        self._render_debounce.timeout.connect(self._fire_pending_render)
        self._pending_pattern: Optional[PulsePattern] = None
        self._has_pending_pattern: bool = False

        # Horizontal scroll bar — synced with the plot's X range so
        # dragging the bar pans the plot and vice-versa. Resolution is
        # in integer microseconds (Qt scrollbars are int-based).
        self.scroll = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Horizontal)
        self.scroll.valueChanged.connect(self._on_scroll_changed)
        self._suppress_scroll_sync = False
        # Vertical scroll bar — same pattern, on the Y axis. The
        # scrollbar's "top" maps to the plot's y_max (positive amps);
        # see ``_update_scroll_y_from_view`` for the int-coordinate
        # mapping. Resolution is integer µA.
        self.scroll_y = QtWidgets.QScrollBar(QtCore.Qt.Orientation.Vertical)
        self.scroll_y.valueChanged.connect(self._on_scroll_y_changed)
        self._suppress_scroll_sync_y = False
        # Y-range view-change signal so the scrollbar follows mouse
        # zooms / pans on the y axis (the X version is wired below).
        vb_signals = self.plot.getViewBox()
        if vb_signals is not None:
            vb_signals.sigYRangeChanged.connect(self._on_y_range_changed)

        # Zoom buttons — separately scale X and Y around the current
        # view's centre. Useful when the user wants to drill in on
        # the cap-coupled discharge tail (X+) or get a wider voltage
        # window for cap-coupled patterns where the anodic peak runs
        # high (Y-).
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

        # Height step buttons — bring the +/- approach back per user
        # request (the drag-to-resize grip wasn't readily discoverable
        # against the styled panel and a couple of users couldn't tell
        # it was an interactive control). 40 px per click. Arrow
        # glyphs were removed per user spec — bare ``+`` and ``−``
        # next to the "Height:" label read cleanly without competing
        # with the Zoom row's X+/X−/Y+/Y− cluster.
        self.tall_btn  = _btn("+", "Increase plot height (40 px)",
                              lambda: self._change_height(40))
        self.short_btn = _btn("−", "Decrease plot height (40 px)",
                              lambda: self._change_height(-40))

        # Cadence-view button — frames several consecutive pulses so the
        # user sees the pulse train: how close pulses are (interpulse
        # spacing) and, when the interpulse delay is off, the back-to-back
        # continuous pulsing.  The preview already draws N_PULSES_DRAWN
        # tiled periods; this button just zooms out to a few of them in one
        # click (vs ~9 X− presses) and the scrollbar then pans along the
        # rest.  Operator: "continuous plotting … move along the x axis to
        # find the next pulses … expand the x axis scale to see how close
        # pulses are … see the pulsing with no interpulse delay".
        self.cadence_btn = _btn(
            "Cadence",
            "Show several consecutive pulses (the pulse train + interpulse "
            "spacing).  Drag / use the scrollbar to move along to the next "
            "pulses; X+/X− to zoom.",
            lambda: self.show_cadence_view())

        # Reset-view button — restores the default zoom (single pulse,
        # full y-amp range with header padding).
        self.reset_btn = QtWidgets.QToolButton()
        self.reset_btn.setText("Reset view")
        self.reset_btn.setToolTip("Restore the default time / amp range")
        self.reset_btn.clicked.connect(self.reset_view)

        # Plot + scrollbars laid out in a 2×2 grid:
        #   (0,0) plot                (0,1) vertical scrollbar
        #   (1,0) horizontal scroll   (1,1) <empty corner>
        # The plot row/column get all the stretch so the scrollbars
        # stay slim and pinned to the edges.
        plot_grid = QtWidgets.QGridLayout()
        plot_grid.setContentsMargins(0, 0, 0, 0)
        plot_grid.setSpacing(0)
        plot_grid.addWidget(self.plot,    0, 0)
        plot_grid.addWidget(self.scroll_y, 0, 1)
        plot_grid.addWidget(self.scroll,  1, 0)
        plot_grid.setColumnStretch(0, 1)
        plot_grid.setRowStretch(0, 1)

        # Plot-control button row: zoom cluster, height cluster, reset.
        # Moved ABOVE the plot per user request — keeps the controls
        # in view even when the user has resized the preview tall
        # enough that the plot itself is dominating the visible area.
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
        controls_row.addWidget(self.cadence_btn)
        controls_row.addStretch(1)
        controls_row.addWidget(self.reset_btn)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.addWidget(self.header)
        v.addLayout(controls_row)
        v.addLayout(plot_grid, stretch=1)

        # Cached default x / y ranges and data extents — set by
        # ``set_pattern``, used by reset_view, the scrollbars, and the
        # zoom buttons (so X+ / Y+ never zoom past the data).
        self._default_x_range: Tuple[float, float] = (0.0, 1.0)
        self._data_x_range: Tuple[float, float] = (0.0, 1.0)
        self._default_y_range: Tuple[float, float] = (-1.0, 1.0)
        self._data_y_range: Tuple[float, float] = (-1.0, 1.0)

        # Initial blank state
        self._set_header_ok("No pulse pattern set.")

    # -----------------------------------------------------------------
    def sizeHint(self):
        # Provide a modest default height so the preview is compact in
        # the params column and the scroll area below it can hand
        # vertical room back to the form rows. Width follows the parent
        # column (Expanding horizontally, see __init__).
        return QtCore.QSize(560, self.PREFERRED_HEIGHT)

    def set_grid_visible(self, visible: bool) -> None:
        """Toggle the plot's gridlines. Called by the main
        window's View → Gridlines action so every experiment
        plot's grid flips together."""
        self._grid_visible = bool(visible)
        self.plot.showGrid(x=self._grid_visible, y=self._grid_visible,
                           alpha=0.25 if self._grid_visible else 0.0)

    def _refresh_actual_legend_label(self, accuracy_pct: float) -> None:
        """Rebuild the ``Actual`` entry in the legend with the latest
        fidelity percentage.

        pyqtgraph's :class:`pyqtgraph.LegendItem` doesn't expose an
        in-place label rename, so we remove the existing entry for
        :attr:`curve_actual` and re-add it with the new text. The
        ``Desired`` entry is left untouched.

        ``accuracy_pct`` is the result of
        :meth:`stimtest.waveforms.PulsePattern.actual_vs_desired_accuracy_pct`.
        Values ≥99.95 % round to ``100.0 %``; we still display the
        decimal so the operator can see "100.0%" vs the rare
        "97.3%" outlier rather than a binary "match / mismatch".
        NaN (computation failed) renders as just ``"Actual"``.
        """
        import math
        if math.isnan(accuracy_pct):
            label = "Actual"
        else:
            # One decimal — enough to distinguish 99.0/99.5/99.9 %
            # at a glance without false precision.
            label = f"Actual ({accuracy_pct:.1f}%)"
        try:
            # ``removeItem`` accepts either the PlotDataItem or the
            # legend-row label string. Passing the item is the
            # robust form across pyqtgraph versions.
            self._legend.removeItem(self.curve_actual)
        except Exception:
            pass
        try:
            self._legend.addItem(self.curve_actual, label)
        except Exception:
            # As a last resort, fall back to the curve's own name
            # so at least the legend isn't blank.
            self.curve_actual.opts["name"] = label

    # -----------------------------------------------------------------
    def set_balance_visible(self, visible: bool):
        """Kept for back-compat. The header is now always visible —
        Q_ph / Q_net / period are always useful info, regardless of
        whether the charge-balance toggle is meaningful in the current
        mode. The warning styling (red bg) is still gated by the
        underlying tolerance check inside set_pattern."""
        self._show_balance = True   # always show
        self.header.setVisible(True)

    def set_pattern_debounced(self, pattern: Optional[PulsePattern]):
        """Coalesced ``set_pattern`` — defers the actual render to a
        60 ms QTimer, restarting it on each call so rapid bursts
        produce ONE render at the end. Wired to
        :attr:`PatternControlPanel.patternChanged` by the parent tab;
        direct callers and tests should use :meth:`set_pattern` to
        get synchronous behaviour."""
        self._pending_pattern = pattern
        self._has_pending_pattern = True
        # ``start()`` is restart-friendly: if the timer is already
        # running it resets the countdown rather than firing twice.
        self._render_debounce.start()

    def _fire_pending_render(self):
        if not self._has_pending_pattern:
            return
        pattern = self._pending_pattern
        self._has_pending_pattern = False
        self._pending_pattern = None
        self.set_pattern(pattern)

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
            self.curve_actual.setData([], [])
            self._set_header_ok("No pulse pattern set.")
            return

        # Render N_PULSES_DRAWN consecutive pulses centered on t=0:
        # the central pulse sits at t ∈ [0, total_pulse], the previous
        # pulse at t ∈ [-period, -period + total_pulse], and so on.
        # Each "tile" is one period of waveform — the active pulse
        # plus the trailing interpulse gap held at 0 µA — so
        # concatenating tiles end-to-end gives a continuous train.
        # A small left/right tail pads zero baseline beyond the
        # outermost pulses so the user sees a clean baseline run-in.
        # BURST detection.  In burst mode the repeating UNIT is a whole
        # burst (``pulses_per_burst`` pulses spaced at the intra-burst period)
        # and the TILE STRIDE is the burst period (== device_period_us); the
        # WITHIN-burst pulse spacing is the intra-burst period.  A non-burst
        # pattern keeps the "one pulse per period" model unchanged.
        is_burst = bool(getattr(pattern, "is_burst", False))
        if is_burst:
            n_pulses_in_burst = int(pattern.pulses_per_burst)
            # Cap the pulses DRAWN (shared by the Desired curve AND the Actual
            # staircase so the two never disagree on pulse count) — a
            # pathological 999-pulse burst can't blow the point budget.
            draw_pulses = min(n_pulses_in_burst, int(self.N_PULSES_DRAWN))
            intra_period_us = pattern.intra_burst_period_us   # == 1e6/rate_hz
            burst_span_us = pattern.burst_span_us
            inter_gap_us = pattern.inter_burst_gap_us
            period_us = pattern.device_period_us              # tile stride
            interpulse_us = inter_gap_us
            tile_post_us = inter_gap_us
            side_pad = max(inter_gap_us, 24.0)
            self._last_period_us = period_us
            self._last_total_pulse_us = float(burst_span_us)
            # Bound the drawn point count: each burst is itself a mini-train,
            # so cap the number of BURSTS tiled (N_PULSES_DRAWN total pulses).
            n_bursts = max(1, min(self.N_BURSTS_DRAWN,
                                  self.N_PULSES_DRAWN // max(1, n_pulses_in_burst)))
            n_left = n_bursts // 2
            n_right = n_bursts - n_left - 1
            offsets = [k * period_us for k in range(-n_left, n_right + 1)]
        else:
            n_pulses_in_burst = 1
            draw_pulses = 1
            intra_period_us = 0.0
            burst_span_us = 0.0
            inter_gap_us = 0.0
            period_us = (1e6 / pattern.rate_hz) if pattern.rate_hz > 0 else 0.0
            interpulse_us = max(period_us - pattern.total_pulse_us, 0.0)
            # Cache the cadence geometry for the "Cadence" view button (frames
            # several consecutive pulses so pulse-to-pulse spacing / back-to-
            # back no-interpulse-delay pulsing is visible in one click).
            self._last_period_us = period_us
            self._last_total_pulse_us = float(pattern.total_pulse_us)
            # ``tile_post_us`` MUST equal ``interpulse_us`` so each tile
            # spans exactly one period and adjacent tiles butt up cleanly.
            # Earlier this used ``max(interpulse_us, 24.0)`` to give a
            # hint of baseline at very-fast rates, but that pushed each
            # tile 24 µs past the period when the user disabled the
            # interpulse delay (interpulse_us = 0), which made the next
            # tile's active phases overlap the previous tile's tail —
            # the actual staircase rendered phantom samples in the gap.
            tile_post_us = interpulse_us
            # Outer (leading-edge) baseline: still gets a small floor so
            # the very leftmost pulse doesn't start flush against the
            # plot edge. This is OUTSIDE the tile chain, so it doesn't
            # cause overlap.
            side_pad = max(interpulse_us, 24.0)

            N = max(1, int(self.N_PULSES_DRAWN))
            n_left = N // 2
            n_right = N - n_left - 1
            # Pulse-centre offsets in time (-period, 0, +period for N=3).
            offsets = [k * period_us for k in range(-n_left, n_right + 1)]

        def _tile(t_one: np.ndarray, i_one: np.ndarray
                  ) -> Tuple[np.ndarray, np.ndarray]:
            """Repeat one pulse (one period of data) at every offset
            and prepend a short zero-baseline lead-in so the leftmost
            pulse's pre-pulse interpulse gap is visible. The trailing
            interpulse gap of the rightmost pulse is already inside
            the last tile (each tile = active phases + trailing gap),
            so no extra trailing pad is needed."""
            ts, ys = [], []
            # Leading-edge zero baseline (gap before the leftmost pulse).
            ts.append(np.array([offsets[0] - side_pad, offsets[0]]))
            ys.append(np.array([0.0, 0.0]))
            for off in offsets:
                ts.append(t_one + off)
                ys.append(i_one)
            return np.concatenate(ts), np.concatenate(ys)

        # ----- Desired pass (smooth math) -----
        # 0.05 µs sample spacing for the ACTIVE region only — fine
        # enough that curved shapes (sin / halfpipe / exp-decay) read
        # as smooth at any zoom. The trailing interpulse gap is
        # represented by ONLY two points (start and end at y=0) so
        # the per-tile sample count drops from ~400 000 (dense flat
        # zero baseline) to ~8 400 (active phases only). Across the
        # full N-pulse train this is the difference between an 8 M
        # point dataset (sluggish to draw and rebuild) and a ~180 k
        # point dataset (effectively instant).
        t_one_pulse, i_one_pulse = pattern.to_timeseries_desired(
            t_pre_us=0.0, t_post_us=0.0, sample_period_us=0.05,
        )
        if is_burst and t_one_pulse.size:
            # BURST UNIT = ``pulses_per_burst`` copies of the pulse spaced at
            # the intra-burst period, each followed by a 2-point zero-gap tail;
            # the LAST tail extends to the burst period (device_period) so the
            # unit ends EXACTLY at the stride and adjacent bursts butt cleanly
            # (the tile-span invariant).  Gaps stay 2-point tails for perf.
            # ``draw_pulses`` (capped in the geometry block, shared with the
            # Actual staircase) bounds the point budget.  Truncation only
            # happens for a single-burst draw (n_bursts == 1), where the
            # trailing flat still runs out to the burst period so the default
            # view frames it correctly.
            parts_t: list[np.ndarray] = []
            parts_i: list[np.ndarray] = []
            for k in range(draw_pulses):
                off = k * intra_period_us
                parts_t.append(t_one_pulse + off)
                parts_i.append(i_one_pulse)
                gap_end = ((k + 1) * intra_period_us
                           if k < draw_pulses - 1 else period_us)
                parts_t.append(np.array([t_one_pulse[-1] + off, gap_end]))
                parts_i.append(np.array([0.0, 0.0]))
            t_one_des = np.concatenate(parts_t)
            i_one_des = np.concatenate(parts_i)
        else:
            t_one_des, i_one_des = t_one_pulse, i_one_pulse
            # Tail samples: append the trailing interpulse gap as 2 points
            # at y=0. The first point repeats the active region's last
            # time so pyqtgraph drops a clean vertical / continuation line
            # there; the second point sits at the period boundary.
            if t_one_des.size:
                tail_t = np.array(
                    [t_one_des[-1], pattern.total_pulse_us + tile_post_us])
                tail_i = np.array([0.0, 0.0])
                t_one_des = np.concatenate([t_one_des, tail_t])
                i_one_des = np.concatenate([i_one_des, tail_i])
        t_des, i_des = _tile(t_one_des, i_one_des)
        # For all-rectangular patterns the Actual staircase is
        # *identical* to the Desired curve — drawing both just doubles
        # the line and clutters the legend, so hide Actual + the
        # legend entirely. Curved / cap-coupled / speedbumps patterns
        # keep both traces and the legend so the user can see the
        # quantisation difference at a glance.
        is_all_rect = all(ph.shape == SHAPE_RECTANGULAR
                          for ph in pattern.phases)
        if is_all_rect:
            self.curve_actual.setData([], [])
            self._legend.setVisible(False)
        else:
            # Build the staircase straight from breakpoints (NOT from
            # ``to_timeseries``'s resampled grid) so each (t_k, a_k) →
            # (t_{k+1}, a_k) hold segment is drawn as a true horizontal
            # segment with an implicit vertical jump at the next
            # breakpoint. Same per-tile + ``_tile`` repetition as the
            # Desired pass.
            # For a burst truncated to ``draw_pulses`` (N > N_PULSES_DRAWN),
            # feed the Actual staircase a pattern with the SAME reduced pulse
            # count so it doesn't render all N while the Desired shows only
            # draw_pulses (the two must agree on pulse count).
            _act_pattern = pattern
            if is_burst and draw_pulses < n_pulses_in_burst:
                import dataclasses as _dc
                _act_pattern = _dc.replace(pattern,
                                           pulses_per_burst=draw_pulses)
            t_one_act, i_one_act = self._actual_staircase_xy(
                _act_pattern, 0.0, tile_post_us)
            t_act, i_act = _tile(t_one_act, i_one_act)
            self.curve_actual.setData(t_act, i_act)
            self._legend.setVisible(True)
            # Compute the Actual-vs-Desired fidelity (NRMSE-based,
            # one-number summary) and embed it in the Actual legend
            # entry. pyqtgraph's LegendItem doesn't support
            # post-construction label edits in-place, so the entry is
            # removed and re-added with the new label.
            try:
                acc_pct = pattern.actual_vs_desired_accuracy_pct()
            except Exception:
                acc_pct = float("nan")
            self._refresh_actual_legend_label(acc_pct)
        self.curve.setData(t_des, i_des)
        # Legacy callers that read self.curve's data assume t/i map
        # to the rendered pulse — keep ``t``/``i`` pointing at the
        # Desired axes since downstream code (annotation positioning,
        # scrollbar) uses them for layout reference.
        t, i = t_des, i_des
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
        # Counter for interphase delays so triphasic patterns can
        # alternate the leader-arrow direction (1st interphase one
        # way, 2nd the opposite). Incremented every time we draw an
        # internal-delay annotation below.
        interphase_idx = 0
        # Vertical sign of the LAST interphase delay annotation drawn,
        # so the discharge label below can sit on the OPPOSITE side of
        # the y=0 axis (per user spec — discharge ↔ preceding interphase
        # always cross the axis to keep the gap and the discharge
        # visually distinct). ``None`` means no preceding interphase
        # was drawn (e.g. all internal delays were sub-resolution).
        last_interphase_sign: Optional[int] = None
        # Detect a saturated pseudo-capacitively-coupled flat+decay
        # pair so the per-phase labels render the LOGICAL
        # single-phase view (one amp label, one width annotation
        # spanning the combined duration). The underlying Phase
        # list still has two records — the device needs them to
        # play the saturation flat + decay separately — but to the
        # user this is one anodic recharge phase.
        sat_pair_i = _saturated_cap_coupled_pair(pattern)
        for n, ph in enumerate(pattern.phases):
            # Skip rendering the amplitude / width labels for the
            # decay phase of a saturated cap-coupled pair — the
            # flat-top phase's labels cover the combined logical
            # second phase. We still advance the cursor and handle
            # any trailing delay annotations so subsequent phases
            # land at the right x-position.
            if sat_pair_i is not None and n == sat_pair_i + 1:
                cursor += ph.width_us
                # Continue into the post-phase delay-annotation
                # block below by falling through, but skip the
                # label-draw / cursor-advance we just did.
                # (Re-implemented inline below.)
                is_internal = (n < len(pattern.phases) - 1)
                if is_internal and ph.delay_after_us >= 1.0:
                    pass   # delays handled in the unified path below
                last_interphase_sign = None  # placeholder; real
                # delay-annotation logic for this case lives in the
                # separate continuation block below.
                # For uniformity we delegate to the same code path
                # the non-skipped branch uses; structured as a
                # helper would over-engineer this — instead we let
                # the next iteration handle the post-decay delay.
                # Specifically: after the decay phase, the
                # discharge_delay annotation belongs to it. Nothing
                # to do here — the loop ends after this iteration
                # for cap-coupled (no further phases).
                continue
            # For the FLAT-top phase of a saturated pair, the
            # logical "second phase width" spans both the flat AND
            # the trailing decay. Compute the effective width up
            # front so the amplitude-label centring and the width
            # annotation share a consistent visible extent.
            if sat_pair_i is not None and n == sat_pair_i:
                effective_width = (ph.width_us
                                   + pattern.phases[n + 1].width_us)
            else:
                effective_width = ph.width_us
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
                html=(f"<span style='color:#0d47a1;font-size:{PLOT_FONT_PT}pt;'>"
                      f"<b>{ph.amplitude_ua:+.1f} {rich.UA}</b></span>"),
                anchor=amp_anchor,
            )
            # Centre the amp label across the effective (combined)
            # span so the saturated-pair flat+decay reads as one
            # phase. For non-saturated phases this is identical to
            # the historical ``cursor + width/2`` placement.
            amp_lab.setPos(cursor + effective_width / 2, amp_y)
            self.plot.addItem(amp_lab)
            self._phase_labels.append(amp_lab)

            # Width annotation — placed on the side of the y=0 axis
            # OPPOSITE the phase's polarity so it doesn't overlap the
            # active waveform: cathodic phase (negative amp) → label
            # ABOVE y=0; anodic phase → label BELOW y=0. Mid-y of the
            # phase region is always the empty side of the axis, so
            # the value text never collides with the curve.
            # ``offset * 0.7`` keeps the label clearly off the y=0
            # baseline (the prior 0.4 multiplier sat the text right
            # against the axis line, which read as overlap on small
            # plots).
            width_label_y = (offset * 0.7 if ph.amplitude_ua < 0
                             else -offset * 0.7)
            self._draw_dim(
                cursor, cursor + effective_width,
                width_label_y,
                self._format_time(effective_width),
                color="#444",
            )

            cursor += ph.width_us
            # Interphase / discharge-delay annotation. Every internal
            # delay > 0 between active phases gets an "interphase
            # delay = X µs" label centred in the gap; the LAST phase's
            # delay_after (a discharge / shorting period in biphasic,
            # or zero in triphasic) is left unlabelled here — it's
            # part of the pulse total — and the period gap that
            # follows is handled by the interpulse label below.
            is_internal = (n < len(pattern.phases) - 1)
            # ``>= 1`` avoids drawing a "0 µs" annotation when the
            # delay is below the device's 1 µs time resolution.
            if is_internal and ph.delay_after_us >= 1.0:
                # Interphase delays are usually narrow (5-50 µs vs the
                # ~200 µs phase widths), so a horizontally-centred
                # label collides with the adjacent phase plots. Use
                # a short diagonal leader to the RIGHT (always; the
                # next pulse is to the right of the gap so that
                # quadrant is the most "open"). Vertical direction
                # follows the polarity of the OUTGOING phase: anodic
                # phase ending into the gap → label UPPER-right
                # (above y=0, on the same side as the anodic peak);
                # cathodic phase ending → label LOWER-right.
                #
                # That gives the user-spec'd alternation for triphasic:
                #
                #   * Anodic-first (phases +,-,+): 1st interphase is
                #     UPPER (after anodic phase 1), 2nd is LOWER
                #     (after cathodic phase 2).
                #   * Cathodic-first (phases -,+,-): 1st interphase
                #     LOWER (after cathodic phase 1), 2nd UPPER
                #     (after anodic phase 2).
                #
                # For biphasic this still produces a sensible single
                # label per gap (above for anodic-first, below for
                # cathodic-first).
                gap_center_x = cursor + ph.delay_after_us / 2.0
                dx_step = max(35.0, ph.width_us * 0.22)
                # Always to the right. Vertical sign follows the
                # outgoing phase's polarity. ``offset * 4.0`` lifts
                # the annotation well clear of the active phases —
                # earlier 2.5× values still let the text sit close to
                # the curve at high y-zooms. The y-pad below is also
                # bumped to keep the new vertical reach inside the
                # default view.
                outgoing_sign = +1 if ph.amplitude_ua >= 0 else -1
                self._draw_dim_diagonal(
                    gap_center_x, 0.0,
                    self._format_time(ph.delay_after_us),
                    dx_us=dx_step,
                    dy=outgoing_sign * offset * 4.0,
                    color="#555",
                )
                interphase_idx += 1
                # Remember this interphase's vertical direction so the
                # discharge annotation can sit on the OPPOSITE side of
                # y=0 (per user spec). Triphasic overwrites this once
                # per gap so we always end up tracking the LAST
                # internal interphase (the one immediately preceding
                # the discharge).
                last_interphase_sign = outgoing_sign
            cursor += ph.delay_after_us

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
        # Y-padding has to clear the diagonal annotations whose far
        # ends sit at ``offset * 4`` ≈ ``amp_max_abs * 0.72`` above /
        # below y=0. ``amp_span * 0.85`` gives a comfortable margin
        # over that without making the active phases look tiny in
        # the resulting view.
        y_pad = max(amp_span * 0.85, 50.0)
        if vb is not None:
            vb.enableAutoRange(y=False)
        y_lo = amp_min - y_pad
        y_hi = amp_max + y_pad
        self._default_y_range = (float(y_lo), float(y_hi))
        # Data y-extent is wider than the default view so the user can
        # zoom out (Y−) to roughly 2.5× the amp span without hitting
        # an edge — useful when the user wants to see how a clipped
        # cap-coupled anodic peak compares to the cathodic baseline.
        zoom_out_factor = 2.5
        c = (y_hi + y_lo) / 2.0
        h = (y_hi - y_lo) / 2.0 * zoom_out_factor
        self._data_y_range = (float(c - h), float(c + h))
        if vb is not None:
            vb.setLimits(yMin=self._data_y_range[0],
                         yMax=self._data_y_range[1])
        self.plot.setYRange(y_lo, y_hi, padding=0)
        self._update_scroll_y_from_view()

        # ---- period / interpulse-delay overlay ----
        # Period (one pulse + dead time) = 1e6 / rate. The interpulse
        # gap is what's left after the active pulse ends. Drawn as a
        # thick *solid* segment at y=0 — the original dotted style
        # was confusable with the dashed Desired curve, and now that
        # both Desired and the interpulse line are solid the user
        # distinguishes them by colour (blue waveform vs grey gap)
        # and by the line being pinned to y=0.
        # Discharge marker — the LAST phase's ``delay_after_us``. Drawn
        # as a coloured solid segment at y=0 distinct from the grey
        # interpulse line so the user sees a clear visual change when
        # they toggle the "Discharge delay" checkbox: with the toggle
        # ON every pulse shows an orange post-pulse band; with the
        # toggle OFF the band disappears (because dd=0 zeroes out the
        # last phase's delay_after_us). Without this marker the only
        # difference between ON and OFF is a ~20 µs shift in where
        # the grey interpulse line starts, which is hard to see at
        # default zoom.
        last_dd = pattern.phases[-1].delay_after_us if pattern.phases else 0.0
        # ``>= 1`` avoids "0 µs" labels for sub-resolution discharge times.
        # SKIPPED for a burst: the single-pulse discharge/interpulse band
        # annotations + their diagonal-leader placement matrix assume ONE
        # pulse per period; the burst curve itself shows the intra/inter-burst
        # gaps, and a burst caption (below) states the grouping.
        if last_dd >= 1.0 and not is_burst:
            dd_pen = pg.mkPen("#fb8c00", width=3,
                              style=QtCore.Qt.PenStyle.SolidLine)
            # Phase N ends at ``total_pulse_us - last_dd``; the
            # discharge spans from there to ``total_pulse_us``.
            # Drawn on every tile so the cadence reads cleanly across
            # the multi-pulse train.
            dd_lo_local = pattern.total_pulse_us - last_dd
            dd_hi_local = pattern.total_pulse_us
            # Pin the marker AT y=0 so it stays on the baseline axis
            # under any y-zoom level — drawing it off-axis (in data
            # coords) made it visually drift relative to the
            # baseline whenever the user zoomed Y. The contrasting
            # orange colour on top of the black y=0 axis line is
            # enough to read as a distinct discharge indicator.
            dd_marker_y = 0.0
            for off in offsets:
                self._interpulse_curves.append(
                    self.plot.plot([off + dd_lo_local, off + dd_hi_local],
                                    [dd_marker_y, dd_marker_y], pen=dd_pen)
                )
            # ONE text annotation only — placed in the central pulse's
            # discharge zone. Layout side (above / below y=0) follows
            # the user-spec'd alternation rule:
            #
            #   * WITH interpulse delay → discharge sits on the OPPOSITE
            #     side of y=0 from the LAST PHASE's polarity (the
            #     interpulse annotation lives upper-RIGHT always, so
            #     the two land in opposite quadrants).
            #         Last phase anodic   → lower side
            #         Last phase cathodic → upper side
            #
            #   * NO interpulse delay → discharge sits on the SAME side
            #     as the last phase (interpulse area is empty, so that
            #     quadrant is free).
            #
            # Text positioning splits two ways depending on band width:
            #
            #   * SHORT discharge (< 1 ms) → diagonal-arrow callout
            #     pointing AT the band centre.  Leader length capped
            #     at 80 µs so even a near-1-ms discharge doesn't
            #     produce a multi-ms arrow that shoots off-screen.
            #     Without the cap, ``dx_mag = last_dd * 1.5`` grew
            #     linearly with the discharge delay (e.g. 15 ms
            #     discharge → 22.5 ms arrow), producing a gigantic
            #     leader that crossed the entire plot AND a text
            #     endpoint that landed inside the interphase-delay
            #     label's x range (``text_x = total_pulse −
            #     2·last_dd`` overlapped the interphase at ~250 µs
            #     last_dd).
            #
            #   * LARGE discharge (≥ 1 ms) → drop the arrow entirely
            #     and pin the text near the band START so it stays
            #     adjacent to the active phases the user is reading.
            #     Mirrors the interpulse "ip_large" branch directly
            #     above: at ≥ 1 ms the orange marker is wide enough
            #     to read as its own band at default zoom, and a
            #     leader arrow becomes visual clutter rather than
            #     useful guidance.
            has_interpulse = (interpulse_us >= 1.0)
            last_phase_sign = (+1 if pattern.phases[-1].amplitude_ua >= 0
                               else -1)
            if has_interpulse:
                discharge_sign = -last_phase_sign
            else:
                discharge_sign = +last_phase_sign

            dd_large = last_dd >= 1000.0
            if dd_large:
                # Target sits NEAR the band start (capped 25 µs in,
                # same cap the interpulse uses), so a 15-ms discharge
                # doesn't push the label into the middle of a long
                # flat run far from the user-edited phases.
                dd_target_x = dd_lo_local + min(last_dd / 2.0, 25.0)
                # Small horizontal nudge with the SAME cap (50 µs) as
                # the interpulse annotation — keeps the label close
                # to the band start regardless of how long the
                # discharge is.
                dx_step = +max(35.0, min(50.0, last_dd * 0.05))
                dy_factor = 0.7
                skip_arrow = True
            else:
                dd_target_x = (dd_lo_local + dd_hi_local) / 2.0
                # Cap leader at 80 µs — slightly more generous than
                # the interpulse 50 µs cap because the discharge band
                # is narrower (so we need a visible pointer) but still
                # tight enough to never overlap the interphase label.
                dx_mag = max(35.0, min(80.0, last_dd * 1.5))
                # LEFT when an interpulse exists (right side reserved
                # for the interpulse annotation); RIGHT otherwise.
                dx_step = -dx_mag if has_interpulse else +dx_mag
                dy_factor = 4.0
                skip_arrow = False

            self._draw_dim_diagonal(
                dd_target_x, dd_marker_y,
                self._format_time(last_dd),
                dx_us=dx_step,
                dy=discharge_sign * offset * dy_factor,
                color="#fb8c00",
                skip_arrow=skip_arrow,
            )

        if interpulse_us >= 1.0 and not is_burst:
            # Width 3 — visibly thicker than the 2-px Desired/Actual
            # traces so it reads as the "this is the gap" marker, but
            # not as dominant as the prior 5-px ridge.
            # ``>= 1`` skips the segments + label when the rate
            # parameters leave essentially no interpulse gap (within
            # device time resolution).
            ipulse_pen = pg.mkPen("#888", width=3,
                                  style=QtCore.Qt.PenStyle.SolidLine)
            # Draw one grey baseline segment per interpulse gap so the
            # entire train reads as a clear "active pulse + dead time"
            # cadence when the user pans / zooms out. Each pulse k
            # starts at offsets[k] and ends at offsets[k] +
            # total_pulse; the gap that follows it ends at
            # offsets[k] + period (= offsets[k+1] for interior tiles).
            for off in offsets:
                gap_lo = off + pattern.total_pulse_us
                gap_hi = off + period_us
                self._interpulse_curves.append(
                    self.plot.plot([gap_lo, gap_hi], [0.0, 0.0], pen=ipulse_pen)
                )
            # Also draw the outer left-side gap (before the leftmost
            # pulse) so the leading-edge cadence is visible too.
            left_gap_lo = offsets[0] - interpulse_us
            left_gap_hi = offsets[0]
            self._interpulse_curves.append(
                self.plot.plot([left_gap_lo, left_gap_hi], [0.0, 0.0],
                                pen=ipulse_pen)
            )
            # ONE text label only — placed in the gap that immediately
            # follows the central pulse (the one whose phase / width /
            # interphase labels the user reads). Keeps the annotations
            # set to a single pulse per user spec; the rest of the
            # gaps are visually obvious from the grey baseline above.
            #
            # Diagonal-leader style (same idiom as interphase /
            # discharge) so a SHORT interpulse delay doesn't render its
            # label on top of the active phases. UNIFIED rule across
            # biphasic and triphasic: the interpulse annotation
            # always sits UPPER-RIGHT regardless of polarity. This
            # mirrors the biphasic placement the user is already
            # comfortable with and pairs cleanly with the discharge
            # annotation's LEFT placement (see discharge block above)
            # so the two sit in opposite corners and never overlap.
            #
            # The leader's TARGET sits NEAR the GAP START (capped at
            # 25 µs into the gap) rather than at the geometric centre.
            # ``dx_mag_ip`` is capped at 50 µs so the text endpoint
            # stays close to the gap start — earlier versions used 80
            # µs which the user reported felt "too far away" from the
            # active pulse, especially for triphasic where the wider
            # active pulse pushed the right-side peek further out.
            ipulse_text = self._format_time(interpulse_us)
            ipulse_target_x = (pattern.total_pulse_us
                               + min(interpulse_us / 2.0, 25.0))
            dx_mag_ip = max(35.0, min(50.0, interpulse_us * 0.05))
            # Y-magnitude scales with how "wide" the gap is. When
            # the interpulse delay is large enough that the gap reads
            # as a long flat run (≥ 1 ms in absolute terms — typical
            # of <500 pps stim trains), drop the text DOWN close to
            # y=0 so the annotation sits naturally next to the gap
            # baseline rather than floating high above the active
            # phases. For SHORT gaps the annotation has to lift well
            # clear of the active phases and the leading edge of the
            # next tile, so we keep the high ``offset * 4`` factor.
            ip_large = interpulse_us >= 1000.0
            ip_dy_factor = 0.7 if ip_large else 4.0
            # Default: upper-RIGHT (unified across biphasic +
            # triphasic + large + small interpulse).
            dx_us = +dx_mag_ip
            dy = +offset * ip_dy_factor
            # Special case (per user spec): triphasic + SMALL
            # interpulse + cathodic-first + a discharge delay is
            # present. The cathodic-first triphasic discharge label
            # sits UPPER-LEFT (block above), so dropping the
            # interpulse to LOWER-RIGHT puts the two annotations on
            # opposite diagonals. Otherwise both would crowd the
            # upper region with the discharge text + interphase
            # arrows already living there.
            triphasic_small_cath_with_dd = (
                len(pattern.phases) == 3
                and not ip_large
                and pattern.polarity == -1
                and last_dd >= 1.0
            )
            if triphasic_small_cath_with_dd:
                dx_us = +dx_mag_ip
                dy = -offset * ip_dy_factor
            # When the interpulse delay is LARGE the gap reads as a
            # long flat run and an arrow pointer is visual clutter —
            # the user just needs the duration label sitting next to
            # y=0. ``skip_arrow=True`` places only the text, no
            # leader, no arrowhead.
            self._draw_dim_diagonal(
                ipulse_target_x, 0.0,
                ipulse_text,
                dx_us=dx_us,
                dy=dy,
                color="#555",
                skip_arrow=ip_large,
            )

        # ---- per-phase Q_ph (IDEAL) + charge balance (realistic error) ----
        # Operator: "For all charge metrics, use the ideal pattern.  Only use
        # the realistic when comparing the error in the test parameters."  So
        # the displayed per-phase Q_ph is the IDEAL continuous integral (the
        # clean as-designed charge — matches the saved / reported metric
        # ``PulsePattern.charge_per_phase_nc``), and the DEVICE staircase
        # residual (30/100 nA quantisation, on the SAME breakpoint budget the
        # .pat writer uses) is shown as Q_net = the realistic DELIVERED
        # imbalance — the error the operator inspects here in the test-
        # parameters panel.  A cap-coupled solve balances the DEVICE charge,
        # so its device Q_net ≈ 0; the ideal per-phase values read cleanly.
        q_phase_nc = pattern.ideal_phase_charges_nc()
        q_phase_dev = pattern.actual_phase_charges_nc(
            current_step_nA=pattern.device_current_step_nA())
        q_net_nc = float(sum(q_phase_dev))     # DEVICE residual = delivered error
        q_max_nc = max(abs(q) for q in q_phase_nc) if q_phase_nc else 0.0
        ratio = abs(q_net_nc) / q_max_nc if q_max_nc > 0 else 0.0

        # Collapse the saturated cap-coupled flat+decay pair into a
        # single conceptual second phase for the per-phase Q_ph
        # readout (matches the per-phase amplitude/width label
        # collapsing above). q_net is unaffected — it's the sum of
        # all sub-phases either way.
        q_phase_nc_logical = list(q_phase_nc)
        if sat_pair_i is not None and sat_pair_i + 1 < len(q_phase_nc):
            combined = (q_phase_nc[sat_pair_i]
                        + q_phase_nc[sat_pair_i + 1])
            q_phase_nc_logical = (
                q_phase_nc[:sat_pair_i]
                + [combined]
                + q_phase_nc[sat_pair_i + 2:])

        # Render summary line — bold green if balanced, red+⚠ if not.
        #
        # Q_ph (per-phase charge magnitude) typically reads in
        # nanocoulombs — a 100 µA × 200 µs phase delivers 20 nC,
        # so nC is the natural reporting unit for the per-phase
        # values. Q_net (the residual after balance) is two-to-
        # five orders of magnitude smaller — saturated cap-coupled
        # is exact (0 pC); unsaturated cap-coupled and quantised
        # rect biphasic typically leave 0.1–10 pC residuals from
        # 30-nA-grid rounding. Reporting Q_net in nC would print
        # ``+0.00 nC`` for nearly every balanced case, hiding the
        # actual residual. Switch Q_net to picocoulombs (× 1000)
        # so a 1.2 pC residual reads as ``+1.20 pC`` rather than
        # rounding to zero.
        q_net_pc = q_net_nc * 1000.0
        # Charge-imbalance percentage: 0 % when the cathodic + anodic
        # charges cancel exactly (|Q_net| = 0), trending toward 100 %
        # as one phase swamps the other. Same number as the
        # ``ratio`` computed above, just expressed as a percentage.
        # Three decimals because typical residuals are tens of pC on
        # tens of nC phases — the interesting variation is in the
        # 0.001–0.1 % regime, and ``.1f`` would collapse all
        # well-balanced patterns to a featureless "0.0 %".
        imbalance_pct = 100.0 * ratio
        # DEVICE-quantization error (operator: "use the realistic when
        # comparing the error in the test parameters").  The per-phase Q_ph
        # above is IDEAL; the device 30/100 nA staircase delivers slightly
        # less on a shaped phase (a linear-increasing 100 nC → ~99.6 nC).
        # Show the worst-case ideal-vs-delivered magnitude error so the
        # operator sees the deviation here, without polluting the reported
        # metric.  Omitted when negligible (rectangular / on-grid → ~0 %).
        _dev_errs = [abs(abs(d) - abs(i)) / abs(i)
                     for i, d in zip(q_phase_nc, q_phase_dev) if abs(i) > 1e-9]
        _max_dev_err = max(_dev_errs) if _dev_errs else 0.0
        _dev_note = (f" &nbsp;&nbsp; device Δ ≤ {_max_dev_err * 100:.2f}%"
                     if _max_dev_err > 5e-4 else "")
        if is_burst:
            # Intra-burst pulse rate (pps) + the burst grouping.  The BURST
            # repetition rate is bursts/second (not a pulse rate → "/s", not
            # pps, per the pps-label convention).
            _timing_html = (
                f" &nbsp;&nbsp; intra-burst rate = {pattern.rate_hz:g} {rich.PPS}"
                f" &nbsp;&nbsp; burst = {pattern.pulses_per_burst} pulses / "
                f"{self._format_time(pattern.burst_period_us)}"
                f" &nbsp;&nbsp; burst rate = {pattern.burst_rate_hz:g} /s"
                f" &nbsp;&nbsp; inter-burst gap = "
                f"{self._format_time(pattern.inter_burst_gap_us)}"
                f" &nbsp;&nbsp; {pattern.effective_pulse_rate_hz:g} "
                f"{rich.PPS} overall"
                f" &nbsp;&nbsp; pulse = "
                f"{self._format_time(pattern.total_pulse_us)}"
            )
        else:
            _timing_html = (
                f" &nbsp;&nbsp; rate = {pattern.rate_hz:g} {rich.PPS}"
                f" &nbsp;&nbsp; period = {self._format_time(period_us)}"
                f" &nbsp;&nbsp; pulse = "
                f"{self._format_time(pattern.total_pulse_us)}"
            )
        summary = (
            f"{rich.var('Q', 'ph')} = "
            + " | ".join(f"{q:+.1f} {rich.NC}" for q in q_phase_nc_logical)
            + f" &nbsp;&nbsp; <b>{rich.var('Q', 'net')}</b> = "
              f"{q_net_pc:+.2f} {rich.PC}"
              f" ({imbalance_pct:.3f}%)"
            + _dev_note
            + _timing_html
        )

        # Always display the per-phase charges, net charge,
        # imbalance %, and period. Only the colour treatment differs:
        # green when imbalance ≤ tolerance, red otherwise. No bullet
        # glyph in front of either message.
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
                f"({imbalance_pct:.2f}%, "
                f"tol ≤ {CHARGE_BALANCE_TOL*100:.0f}%)"
                f"<br>{summary}"
            )

        # ---- default view: just the CENTRAL pulse ----
        # The data extent covers ~21 periods so the user can pan /
        # wheel-zoom out to see the cadence run, but the default
        # framing zooms in on the active phases only — that's where
        # the user is actually editing parameters. A small peek
        # extends right just past the trailing interpulse gap so the
        # `interpulse delay` annotation for the central pulse is
        # visible without panning. ``Reset View`` brings them back here.
        # When an interpulse delay is present we floor the right peek
        # at 150 µs so the interpulse annotation (anchored ≤25 µs into
        # the gap with text ≤80 µs further right) fits comfortably
        # inside the default view even on short total-pulse patterns.
        if is_burst:
            # Frame ONE full burst (all N pulses) + a capped peek into the
            # inter-burst gap so both the grouping AND the gap read (operator:
            # "the default view should frame one burst … showing at least one
            # burst").
            peek = max(30.0, burst_span_us * 0.05)
            right = burst_span_us + min(inter_gap_us, burst_span_us * 0.3) + peek
            self._default_x_range = (-peek, float(right))
        else:
            peek = max(30.0, pattern.total_pulse_us * 0.15)
            if interpulse_us >= 1.0:
                peek = max(peek, 150.0)
            self._default_x_range = (
                -peek,
                float(pattern.total_pulse_us + peek),
            )
        self.plot.setXRange(*self._default_x_range, padding=0.0)
        self._update_scrollbar_from_view()
        self._update_scroll_y_from_view()

    # -----------------------------------------------------------------
    def reset_view(self):
        """Restore BOTH x- and y-axis ranges to the defaults captured
        in ``set_pattern`` (single pulse on x; amp ± padding on y).

        Disables auto-range first so pyqtgraph doesn't immediately
        re-fit on the next paint — that would undo our explicit
        defaults if there's a pending sigRangeChanged. Also forces a
        scrollbar resync so the bar handles return to their starting
        position even if the user dragged them mid-zoom.
        """
        vb = self.plot.getViewBox()
        if vb is not None:
            vb.enableAutoRange(x=False, y=False)
        if self._default_x_range[1] > self._default_x_range[0]:
            self.plot.setXRange(*self._default_x_range, padding=0)
        if self._default_y_range[1] > self._default_y_range[0]:
            self.plot.setYRange(*self._default_y_range, padding=0)
        self._update_scrollbar_from_view()
        self._update_scroll_y_from_view()

    # -----------------------------------------------------------------
    def show_cadence_view(self, n_pulses: float = 3.0):
        """Frame ``~n_pulses`` consecutive pulses so the pulse-to-pulse
        cadence is visible in one click — the interpulse spacing, or the
        back-to-back run when there's no interpulse delay.

        The preview already draws ``N_PULSES_DRAWN`` tiled periods (a
        continuous train); this just zooms the x-view out to a few of them,
        centred on the central pulse, clamped to the drawn extent.  The
        scrollbar / drag-pan then moves along to the further pulses, and
        X+/X− fine-tune the scale.  No-op until a pattern has been rendered.
        """
        period = getattr(self, "_last_period_us", 0.0)
        if not (period > 0.0):
            return
        total = getattr(self, "_last_total_pulse_us", period)
        centre = total / 2.0                    # centre of the central pulse
        half = max(float(n_pulses), 1.0) * period / 2.0
        d0, d1 = self._data_x_range
        lo = max(d0, centre - half)
        hi = min(d1, centre + half)
        if hi - lo < 1e-6:                        # degenerate → whole train
            lo, hi = d0, d1
        vb = self.plot.getViewBox()
        if vb is not None:
            vb.enableAutoRange(x=False)
        self.plot.setXRange(lo, hi, padding=0)
        self._update_scrollbar_from_view()

    # ---- Actual-curve staircase from breakpoints --------------------
    @staticmethod
    def _actual_staircase_xy(
        pattern: PulsePattern, t_pre_us: float, t_post_us: float,
        *, current_step_nA: int = 30,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Render the Actual curve as the EXACT sample-and-hold
        staircase the device receives via the arbitrary-waveform
        ``.pat`` file.

        Built directly from :func:`stimtest.waveforms.build_pat_pairs`
        — the same function that writes the .pat — so the Actual
        trace is byte-for-byte what the PlexStim DLL loads. This is
        the single source of truth: any quantisation (amplitude
        rounded to int nA, duration rounded to int µs), any offset
        / tail-zero / shape-specific handling that ``build_pat_pairs``
        applies shows up here automatically. Previously this method
        called ``shape_breakpoints`` and applied its OWN 30 nA
        quantisation, which could diverge from the .pat content in
        subtle ways (e.g. when ``build_pat_pairs`` floors a duration
        to ``max(1, int(round(...)))`` but the preview's float math
        used the unrounded value).

        ``current_step_nA`` is retained as a kwarg for API back-compat
        but is no longer honoured: amplitude quantisation is whatever
        ``build_pat_pairs`` produces (1 nA grid — what the .pat stores).
        The device's downstream 30 nA hardware step happens AFTER the
        .pat is loaded; for the preview, we show what gets loaded.

        Falls back to the legacy shape-breakpoint path when
        ``build_pat_pairs`` raises (typically because the pattern
        exceeds the 499-pair cap) so an oversized pattern still
        renders a best-effort staircase instead of an empty plot.
        """
        from ..waveforms import build_burst_pat_pairs

        times: list[float] = []
        amps: list[float] = []

        # Pre-pulse zero-amp baseline.
        times.extend([-t_pre_us, 0.0])
        amps.extend([0.0, 0.0])

        try:
            # Burst-aware: returns exactly ``build_pat_pairs`` for a non-burst
            # pattern, and the N-pulse burst (with intra-burst gap pairs, no
            # trailing gap) for a burst — so one burst renders as one
            # staircase ending at burst_span; the ``t_post`` baseline then
            # extends by the inter-burst gap to the burst period.
            pairs = build_burst_pat_pairs(pattern)
        except Exception:
            # Oversized or otherwise rejected pattern. Fall back to
            # the shape-breakpoint path so the user still sees an
            # approximate Actual trace alongside the warning the
            # validation layer surfaces elsewhere.
            return PatternPreview._actual_staircase_xy_fallback(
                pattern, t_pre_us, t_post_us,
                current_step_nA=current_step_nA)

        # Walk the (amp_nA, dur_us) pair stream the device sees. Each
        # pair is a sample-and-hold segment: render as (t, a), (t+dur,
        # a) so pyqtgraph draws a horizontal segment with an implicit
        # vertical jump to the next pair's amplitude.
        cursor = 0.0
        for amp_nA, dur_us in pairs:
            amp_ua = float(amp_nA) / 1000.0
            seg_end = cursor + float(dur_us)
            times.append(cursor);  amps.append(amp_ua)
            times.append(seg_end); amps.append(amp_ua)
            cursor = seg_end

        # Post-pulse zero-amp baseline.
        times.append(cursor);             amps.append(0.0)
        times.append(cursor + t_post_us); amps.append(0.0)

        return np.asarray(times, dtype=float), np.asarray(amps, dtype=float)

    @staticmethod
    def _actual_staircase_xy_fallback(
        pattern: PulsePattern, t_pre_us: float, t_post_us: float,
        *, current_step_nA: int = 30,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Legacy shape-breakpoint path — used only when
        :func:`build_pat_pairs` rejects the pattern (oversized). Same
        sample-and-hold staircase shape as the primary path, but
        synthesised from ``shape_breakpoints`` so the user still sees
        a best-effort Actual trace.

        Applies the SAME per-phase ``n_samples`` clamp the primary
        path uses (``min(n_samples, int(W))``) so the fallback
        doesn't silently render at a different sample density than
        the .pat would have used. Previously the fallback used the
        global ``curved_sample_budget()`` directly, which produced
        coarser segments on long-width phases. Keeping the clamps
        in sync prevents the two paths from drifting visually.
        """
        n_samples = pattern.curved_sample_budget()
        step_ua = float(current_step_nA) * 1e-3 if current_step_nA else 0.0

        times: list[float] = []
        amps:  list[float] = []
        times.extend([-t_pre_us, 0.0])
        amps.extend([0.0, 0.0])

        cursor = 0.0
        for ph in pattern.phases:
            # Per-phase clamp — matches the primary
            # ``build_pat_pairs`` path so the two staircase
            # renderers stay byte-aligned (no fallback-induced
            # sample-density drift on long-width phases).
            ph_n_samples = max(2, min(n_samples,
                                      int(round(float(ph.width_us)))))
            bps = shape_breakpoints(
                amplitude_ua=ph.amplitude_ua, width_us=ph.width_us,
                shape=ph.shape, bump_count=ph.bump_count,
                tau_us=ph.tau_us, n_samples=ph_n_samples,
                offset_ua=getattr(ph, "offset_ua", 0.0),
                tail_zero_us=getattr(ph, "tail_zero_us", 0.0),
            )
            if step_ua > 0:
                bps = [(t, round(a / step_ua) * step_ua)
                       for t, a in bps]
            for k in range(len(bps) - 1):
                t_k, a_k = bps[k]
                t_next, _ = bps[k + 1]
                times.append(cursor + t_k);    amps.append(a_k)
                times.append(cursor + t_next); amps.append(a_k)
            cursor += ph.width_us
            if ph.delay_after_us > 0:
                times.append(cursor);                       amps.append(0.0)
                times.append(cursor + ph.delay_after_us);   amps.append(0.0)
            cursor += ph.delay_after_us

        times.append(cursor);                amps.append(0.0)
        times.append(cursor + t_post_us);    amps.append(0.0)

        return np.asarray(times, dtype=float), np.asarray(amps, dtype=float)

    # ---- time-unit formatting helper --------------------------------
    @staticmethod
    def _format_time(us: float) -> str:
        """Render a duration in the unit that reads naturally:

          * < 1 µs        → ``"0 µs"`` (sub-resolution gap, rendered as 0)
          * < 1000 µs     → ``"<n> µs"`` (one decimal if < 100 µs)
          * < 1 second    → ``"<n.n> ms"`` (two decimals below 1 ms)
          * ≥ 1 second    → ``"<n.nn> s"``

        Used by the interpulse-delay annotation so a 19 600 µs gap
        reads as "19.6 ms" instead of an awkward five-digit µs count.
        """
        u = abs(float(us))
        if u < 1.0:
            return f"0 {rich.US}"
        if u < 1000.0:
            if u < 100.0:
                return f"{us:.1f} {rich.US}"
            return f"{us:.0f} {rich.US}"
        if u < 1_000_000.0:
            ms = us / 1000.0
            if abs(ms) < 10.0:
                return f"{ms:.2f} ms"
            return f"{ms:.1f} ms"
        s = us / 1_000_000.0
        return f"{s:.2f} s"

    # ---- duration label ---------------------------------------------
    def _draw_dim(self, x_start: float, x_end: float, y: float,
                  label_text: str, *, label_x: Optional[float] = None,
                  color: str = "#555") -> None:
        """Place a plain time-value text label centred on the
        (x_start, x_end) range. Used for per-phase widths and the
        interpulse delay. ``label_x`` overrides the centring (e.g.
        for the interpulse text, which the user wants pinned close to
        the pulse rather than centred in a long gap).
        """
        if x_end <= x_start or not label_text:
            return
        if label_x is None:
            label_x = (x_start + x_end) / 2.0
        lab = pg.TextItem(
            html=(f"<span style='color:{color};font-size:{PLOT_FONT_PT}pt;'>"
                  f"{label_text}</span>"),
            anchor=(0.5, 0.5),
        )
        lab.setPos(label_x, y)
        self.plot.addItem(lab)
        self._phase_labels.append(lab)

    def _draw_dim_diagonal(self, x_target: float, y_target: float,
                           label_text: str, *,
                           dx_us: float, dy: float,
                           color: str = "#555",
                           skip_arrow: bool = False) -> None:
        """Place a label diagonally offset from a target. The leader
        line and the arrowhead are drawn together by a SINGLE
        :class:`_LeaderArrow` graphics item that issues both
        ``drawLine`` (the line) and ``drawPolygon`` (the filled head)
        from one ``paint()`` call — exactly the pattern in Qt's
        DiagramScene Arrow example. There is no separately-drawn
        arrow appended to the line; they are parts of one annotation.

        The line's endpoints are anchored in data coords (so the
        text and the tip stay glued to their data targets at every
        zoom), and the arrowhead size scales with the leader length.

        ``skip_arrow=True`` places the text label only — no leader,
        no arrowhead. Used for the interpulse annotation when the
        gap is large enough that the label sits next to a long flat
        baseline and a pointer would be visual clutter rather than
        useful guidance.

        Text-anchor side flips with the sign of ``dx_us`` so the
        label ALWAYS extends OUTWARD from the gap.
        """
        if not label_text:
            return
        text_x = x_target + dx_us
        text_y = y_target + dy
        if not skip_arrow:
            leader_len = math.hypot(dx_us, dy)
            if leader_len >= 1e-9:
                # Arrowhead size scales with leader length — long
                # leaders get proportionally larger heads, short ones
                # stay compact, with a 15-data-unit floor so the head
                # never vanishes for a tiny leader.
                head_size = max(15.0, leader_len * 0.18)
                arrow = _LeaderArrow(
                    x_text=text_x, y_text=text_y,
                    x_target=x_target, y_target=y_target,
                    color=color,
                    head_size=head_size,
                    half_tip_deg=22.0,
                    line_width=2.0,
                )
                self.plot.addItem(arrow)
                self._phase_labels.append(arrow)
        # Anchor: x always 0.5 so the text's HORIZONTAL CENTER is
        # aligned with the arrow line's text-end x (per user spec —
        # "align the center of the annotation with the arrow line").
        # The vertical anchor flips with ``dy``: if the leader points
        # up to the text (dy >= 0) the text sits ABOVE the endpoint
        # with its bottom edge touching it; if the leader points down
        # the text sits BELOW with its top edge touching.
        anchor_x = 0.5
        anchor_y = 1.0 if dy >= 0 else 0.0
        html = (f"<span style='color:{color};font-size:{PLOT_FONT_PT}pt;'>"
                f"{label_text}</span>")
        lab = pg.TextItem(html=html, anchor=(anchor_x, anchor_y))
        # When ``skip_arrow`` is True (large interpulse — no leader)
        # we anchor the text in y at exactly y=0 instead of at
        # ``text_y`` (which is in data coords and would shift with
        # y-zoom). Combined with a y-anchor of 1.0/0.0 this pins the
        # text's bottom-edge / top-edge at the y=0 line, so the label
        # stays at a CONSTANT pixel offset from y=0 regardless of
        # how the user scales the plot.
        pos_y = 0.0 if skip_arrow else text_y
        lab.setPos(text_x, pos_y)
        self.plot.addItem(lab)
        self._phase_labels.append(lab)

    # ---- plot height resize -----------------------------------------
    def _set_height(self, new_h: int) -> None:
        """Pin the preview to ``new_h`` pixels (clamped to
        ``[MIN_HEIGHT, MAX_HEIGHT]``).

        Calling ``setMinimumHeight`` AND ``setMaximumHeight`` together
        is the standard Qt way to make a widget take a specific
        height inside a layout — ``setFixedHeight`` does the same
        thing under the hood, but going through min/max keeps
        subsequent +/- clicks symmetric (so we don't have to track an
        internal height variable separately from the widget state).
        """
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

    # ---- view-state persistence -------------------------------------
    def view_state(self) -> dict:
        """Snapshot the user-resizable preview height for prefs.

        The plot height is the one piece of preview UI state that
        survives between sessions — the user resizes via the ``↕+`` /
        ``↕−`` buttons and that choice should persist across launches.
        """
        return {"height": int(self.height() or self.PREFERRED_HEIGHT)}

    def restore_view_state(self, view: dict) -> None:
        """Apply a saved height (clamped to the legal range).

        A saved height equal to the PRIOR default is ignored, so reducing
        ``PREFERRED_HEIGHT`` actually takes effect for existing users whose
        prefs auto-saved the old default (operator: "reduce the default
        height of the test parameter plot").  A genuinely user-resized
        height (any other value) is still honored.
        """
        if not isinstance(view, dict):
            return
        h = view.get("height")
        if (isinstance(h, (int, float)) and h > 0
                and int(h) != self._LEGACY_DEFAULT_HEIGHT):
            self._set_height(int(h))

    # ---- zoom buttons -----------------------------------------------
    def _zoom_axis(self, axis: str, factor: float) -> None:
        """Scale the view range on one axis by ``factor`` around the
        view's current centre. ``factor < 1`` zooms in (range shrinks);
        ``factor > 1`` zooms out. The result is clamped to the data
        extent on that axis so the user can't zoom out into empty
        space beyond the rendered data.
        """
        vb = self.plot.getViewBox()
        if vb is None: return
        if axis == 'x':
            r0, r1 = vb.viewRange()[0]
            d0, d1 = self._data_x_range
        else:
            r0, r1 = vb.viewRange()[1]
            d0, d1 = self._data_y_range
        c = (r0 + r1) / 2.0
        half = max(1e-6, (r1 - r0) / 2.0 * factor)
        new_lo = max(d0, c - half)
        new_hi = min(d1, c + half)
        if new_hi - new_lo < 1e-6:
            return
        if axis == 'x':
            vb.setXRange(new_lo, new_hi, padding=0)
            self._update_scrollbar_from_view()
        else:
            vb.setYRange(new_lo, new_hi, padding=0)
            self._update_scroll_y_from_view()

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

    # ---- vertical (Y-axis) scrollbar synchronisation ----------------
    def _on_scroll_y_changed(self, value: int):
        """Vertical scrollbar moved → pan the plot's y range. The
        scrollbar's "top" maps to the data's y_max (positive amps),
        so a higher scrollbar value means the view top has dropped
        further below data_y_max."""
        if self._suppress_scroll_sync_y:
            return
        vb = self.plot.getViewBox()
        if vb is None: return
        y_min, y_max = vb.viewRange()[1]
        height = y_max - y_min
        data_y_max = self._data_y_range[1]
        new_y_max = float(data_y_max - value)
        new_y_min = new_y_max - height
        self._suppress_scroll_sync_y = True
        try:
            vb.setYRange(new_y_min, new_y_max, padding=0)
        finally:
            self._suppress_scroll_sync_y = False

    def _on_y_range_changed(self, _vb=None, _range=None):
        """Plot Y view changed (mouse wheel / drag) → resync scrollbar."""
        if self._suppress_scroll_sync_y:
            return
        self._update_scroll_y_from_view()

    def _update_scroll_y_from_view(self):
        """Reflect the plot's Y range in the vertical scrollbar's
        value/page/range. ``value`` is the distance (in µA) from the
        data's y_max down to the current view's y_max — i.e., 0 means
        the view is pinned to the top of the data, larger values
        mean the view has been panned downward."""
        vb = self.plot.getViewBox()
        if vb is None: return
        y_min, y_max = vb.viewRange()[1]
        height = max(y_max - y_min, 1.0)
        data_y_min, data_y_max = self._data_y_range
        page_step = max(1, int(height))
        scrollbar_max = max(0, int(data_y_max - data_y_min - height))
        self._suppress_scroll_sync_y = True
        try:
            self.scroll_y.setMinimum(0)
            self.scroll_y.setMaximum(scrollbar_max)
            self.scroll_y.setPageStep(page_step)
            self.scroll_y.setSingleStep(max(1, page_step // 10))
            self.scroll_y.setValue(int(data_y_max - y_max))
        finally:
            self._suppress_scroll_sync_y = False

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
