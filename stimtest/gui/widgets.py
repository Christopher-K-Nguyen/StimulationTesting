"""Reusable Qt widgets used by the experiment tabs."""
from __future__ import annotations

import math
import re
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
    if not math.isfinite(x):
        return x
    if x == 0.0:
        return 0.0
    d = math.ceil(math.log10(abs(x)))
    scale = 10.0 ** (d - sig)          # e.g. 100.0 for x≈576, sig=1
    scaled = x / scale
    # Round half AWAY from zero to match MATLAB ``round(x, sig,
    # 'significant')``.  Python's built-in ``round`` is half-to-EVEN, which
    # would frame a bound that lands on an exact sig-fig half (e.g. -45 µs)
    # to -40 instead of MATLAB's -50.  Multiply by the integer ``scale``
    # (not divide by 10**(sig-d)) so the result carries no fp noise.
    r = math.floor(scaled + 0.5) if scaled >= 0.0 else math.ceil(scaled - 0.5)
    return r * scale


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


def _snap_range_to_ticks(lo: float, hi: float, target_count: int = 5):
    """Round ``(lo, hi)`` OUTWARD to the nice-tick grid so an axis set to
    this range begins and ends exactly on a major tick (operator: "the
    axes need to begin and end with a tick").  Returns the snapped
    ``(first, last)``; falls back to the input on a degenerate span."""
    lo = float(lo); hi = float(hi)
    span = hi - lo
    step = _matlab_nice_tick_step(span, target_count)
    if step <= 0.0 or not math.isfinite(step):
        return lo, hi
    first = math.floor(lo / step + 1e-9) * step
    last = math.ceil(hi / step - 1e-9) * step
    if last <= first:
        return lo, hi
    return first, last


def _matlab_x_range(time_us, target_count: int = 10):
    """MATLAB ``getPlot_Tek.m`` / ``getAcutePlot3.m`` X-range — the FULL
    captured time extent with EACH END rounded to 1 significant figure and
    nothing else (operator: "used the same time range adjustment as what I
    did in MATLAB").  The MATLAB is literally::

        xMin = round(min(time),1,'significant');
        xMax = round(max(time),1,'significant');
        xlim([xMin xMax]);

    so ``round(-64,1sig) = -60`` and ``round(576,1sig) = 600`` → ``[-60,
    600]``.  This rounds INWARD on one end (a small pre-trigger sliver may
    be clipped) and OUTWARD on the other, exactly like MATLAB.  Ticks are
    drawn on the MATLAB nice-multiples grid within this range by the axis
    ``tickValues`` override, so the frame naturally begins/ends near a tick.
    Mirrors the POLARIS export's ``plotting._matlab_x_limits``.
    ``target_count`` is retained for call-site compatibility (unused now
    that the half-tick snapping is gone).  Returns ``(xmin, xmax)`` or
    ``None``.

    History: this REPLACED a half-tick floor/ceil grid that padded each
    side to the ``step/2`` boundary — an over-engineering layered on top of
    the MATLAB rule to satisfy "one tick before x=0" / "pad 50 us" requests.
    The operator ultimately asked for the plain MATLAB rule; don't re-add
    the half-tick snapping."""
    t = np.asarray(time_us, dtype=float)
    if t.size < 2:
        return None
    lo = _round_sig(float(np.nanmin(t)), 1)
    hi = _round_sig(float(np.nanmax(t)), 1)
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return None
    return lo, hi


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


def _make_matlab_tick_override(orig_tickValues, target_count: int = 5,
                               short_span_target: Optional[int] = None,
                               short_span_threshold: float = 0.0):
    """Build a ``tickValues``-compatible callable that returns the
    MATLAB-style major level INSTEAD of pyqtgraph's denser level 0.

    pyqtgraph's ``AxisItem.tickValues(minVal, maxVal, size)`` returns
    a list of ``(spacing, [tick_positions, …])`` tuples — one per
    "level" (major / minor / sub-minor).  This wrapper REPLACES the
    first (major) level with the MATLAB-computed positions and
    drops every subsequent level (no minor / sub-minor ticks — same
    as the previous "major-only" override but with the better step
    selector applied).

    ``short_span_target`` / ``short_span_threshold`` make the tick
    target SPAN-DEPENDENT: when the visible span is ``<=
    short_span_threshold`` the denser ``short_span_target`` is used,
    otherwise the sparse ``target_count``.  This is how the time axis
    gets MORE ticks on a short (~440 µs) pulse window without
    re-densifying a long (~2000 µs) one — the two preferences can't be
    met by a single fixed target (operator: "There are too few x ticks
    for a 440-us pulse").  Left at the defaults the behaviour is
    unchanged (fixed ``target_count`` everywhere).

    Returns a function compatible with ``AxisItem.tickValues``.
    """
    def _matlab_tick_values(minVal, maxVal, size, _orig=orig_tickValues,
                            _n=target_count, _sst=short_span_target,
                            _sth=short_span_threshold):
        n = _n
        try:
            span = abs(float(maxVal) - float(minVal))
            if _sst and _sth > 0.0 and 0.0 < span <= _sth:
                n = int(_sst)
        except Exception:
            n = _n
        try:
            ticks = _matlab_major_tick_values(
                float(minVal), float(maxVal), n)
        except Exception:
            ticks = []
        if not ticks:
            # Degenerate range or numerics blew up — fall back to
            # pyqtgraph's default level 0 so we never return an
            # empty tick list and leave the axis blank.
            levels = _orig(minVal, maxVal, size)
            return levels[:1] if levels else levels
        step = _matlab_nice_tick_step(maxVal - minVal, n)
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


#: Cached horizontal-bar scatter glyph (built lazily — needs a QApplication
#: only to *render*, but the QPainterPath itself is cheap).  Used for the
#: access-voltage / driving-voltage cursors (operator: "use a horizontal
#: bar symbol" for V_a / V_d, distinct from the ``+`` polarization glyph).
_HBAR_PATH = None


def _hbar_symbol():
    """A short horizontal bar drawn in pyqtgraph's unit symbol box
    (``[-0.5, 0.5]``).  pyqtgraph accepts a ``QPainterPath`` anywhere a
    symbol-name string is accepted; the marker pen strokes the line."""
    global _HBAR_PATH
    if _HBAR_PATH is None:
        p = QtGui.QPainterPath()
        p.moveTo(-0.5, 0.0)
        p.lineTo(0.5, 0.0)
        _HBAR_PATH = p
    return _HBAR_PATH


#: Cached "plus" scatter glyph built as TWO crossing LINE strokes (not
#: pyqtgraph's filled "+" polygon) so its arm thickness is the marker PEN
#: width — exactly like the horizontal bar.  That lets the bar and the
#: plus share one pen width and read at MATCHING thickness (operator: "I
#: want the horizontal bar symbol thicker and the plus symbol thinner —
#: matching thickness").
_PLUS_PATH = None


def _plus_symbol():
    """A ``+`` drawn as two crossing line strokes in the unit symbol box,
    so the marker pen — not a filled polygon — sets its thickness."""
    global _PLUS_PATH
    if _PLUS_PATH is None:
        p = QtGui.QPainterPath()
        p.moveTo(-0.5, 0.0)
        p.lineTo(0.5, 0.0)
        p.moveTo(0.0, -0.5)
        p.lineTo(0.0, 0.5)
        _PLUS_PATH = p
    return _PLUS_PATH


# ---------------------------------------------------------------------------
# Rotated axis-title widget
# ---------------------------------------------------------------------------
class _AxisTitle(QtWidgets.QWidget):
    """An axis title rendered as a Qt widget rather than pyqtgraph's axis
    label.

    pyqtgraph's axis TITLE doesn't render on some displays (operator
    repeatedly reported the axis labels MISSING even though the tick
    numbers and legend render fine, and reserving axis space didn't
    help).  The ScopePlot therefore paints its OWN titles around the
    plot — one of these widgets per side.  Colour follows the palette so
    it reads on dark and light themes.

    ``side`` = ``"left"`` (text reads bottom-to-top, rotated −90),
    ``"right"`` (top-to-bottom, +90), or ``"bottom"`` (horizontal, no
    rotation).  **All three sides use the SAME render path** — text →
    device-pixel-ratio pixmap → rotate → draw — so the bottom "Time [µs]"
    title is pixel-for-pixel the same size and weight as the left/right
    "Voltage [V]" / current titles (operator: "Time axis label is not the
    same font size as the y axis labels").  Previously the bottom was a
    plain QLabel whose direct-render path could look different from the
    sides' pixmap-rotate path at the same point size.
    """

    def __init__(self, text="", side="left", pt=12, parent=None):
        super().__init__(parent)
        self._text = text
        self._side = side
        self._pt = pt
        # Pin the widget's own font to ``pt`` so ``font().pointSize()``
        # reports it (tests + any external query) — the paint path uses
        # ``_font()`` which is the same size.
        f = self.font()
        f.setPointSize(pt)
        self.setFont(f)
        if side == "bottom":
            self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                               QtWidgets.QSizePolicy.Policy.Fixed)
        else:
            self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Fixed,
                               QtWidgets.QSizePolicy.Policy.Preferred)

    def setText(self, text: str) -> None:
        if text != self._text:
            self._text = text
            self.updateGeometry()
            self.update()

    def text(self) -> str:
        return self._text

    def _font(self) -> "QtGui.QFont":
        f = self.font()
        f.setPointSize(self._pt)
        return f

    def sizeHint(self):
        fm = QtGui.QFontMetrics(self._font())
        tw = fm.horizontalAdvance(self._text)
        th = fm.height()
        if self._side == "bottom":
            return QtCore.QSize(tw + 16, th + 6)
        return QtCore.QSize(th + 6, tw + 16)

    def minimumSizeHint(self):
        return self.sizeHint()

    def paintEvent(self, ev):
        if not self._text:
            return
        # Render the text HORIZONTALLY to a pixmap, then rotate the
        # PIXMAP.  Rotating the rendered pixmap works for BOTH signs,
        # whereas a direct ``painter.rotate(+90) + drawText`` silently
        # fails to paint (the −90 left title rendered, the +90 right one
        # didn't).  Left reads bottom-to-top (−90); right reads top-to-
        # bottom (+90) — opposite (verified: +90 image is the 180°
        # rotation of −90, IoU 1.0).  Bottom uses the SAME path with
        # angle 0 (identity transform) so it matches the sides exactly.
        f = self._font()
        fm = QtGui.QFontMetrics(f)
        tw = fm.horizontalAdvance(self._text) + 4
        th = fm.height() + 2
        # Build the source pixmap at the DISPLAY's device-pixel ratio so
        # every title renders at the SAME physical size regardless of
        # Windows scaling (125 %/150 %).  A DPR-1 pixmap has too few
        # physical pixels and comes out smaller + blurrier than a
        # DPR-correct one.  With the ratio set on the pixmap the painter
        # works in LOGICAL coordinates, so we draw into a logical
        # ``tw × th`` rect and position by the device-independent size.
        dpr = self.devicePixelRatioF() or 1.0
        pm = QtGui.QPixmap(max(1, round(tw * dpr)), max(1, round(th * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        pp = QtGui.QPainter(pm)
        pp.setFont(f)
        pp.setPen(self.palette().windowText().color())
        pp.drawText(QtCore.QRectF(0.0, 0.0, float(tw), float(th)),
                    int(QtCore.Qt.AlignmentFlag.AlignCenter), self._text)
        pp.end()
        angle = {"left": -90, "right": 90}.get(self._side, 0)
        if angle:
            rpm = pm.transformed(
                QtGui.QTransform().rotate(angle),
                QtCore.Qt.TransformationMode.SmoothTransformation)
        else:
            rpm = pm
        lw = rpm.width() / dpr
        lh = rpm.height() / dpr
        p = QtGui.QPainter(self)
        p.drawPixmap(int((self.width() - lw) / 2),
                     int((self.height() - lh) / 2), rpm)
        p.end()


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


def disable_plot_wheel_zoom(plot) -> None:
    """Stop the mouse wheel from zooming a pyqtgraph plot.

    pyqtgraph's ``ViewBox`` zooms on wheel rotation by default (operator:
    "Prevent mouse scrolling from zooming in and out of plots").  We
    replace the ViewBox's ``wheelEvent`` with one that ignores the
    rotation, so the wheel no longer zooms — drag-pan, the on-screen
    zoom buttons, and any enclosing scroll area keep working.

    ``plot`` may be a ``pg.PlotWidget`` / ``pg.PlotItem`` (we reach its
    ViewBox via ``getViewBox``) or a bare ``pg.ViewBox``.  No-op when
    pyqtgraph is missing or the object exposes no ViewBox.  Idempotent.
    """
    if not HAS_PYQTGRAPH or plot is None:
        return
    try:
        vb = plot.getViewBox() if hasattr(plot, "getViewBox") else plot
    except Exception:
        vb = plot
    if vb is None:
        return
    # Instance-level override shadows ViewBox.wheelEvent so the default
    # scale-on-wheel code never runs.  ``ignore`` (rather than swallow)
    # lets the wheel bubble up to an enclosing scroll area, so scrolling
    # the page still works when the cursor is over a plot.
    try:
        vb.wheelEvent = lambda ev, axis=None: ev.ignore()
    except Exception:
        pass


def _epol_guide_label_html(label: str, color: str) -> str:
    """Variable-format an E_pol guide label for HTML rendering.

    ``"Emc"`` / ``"Ema"`` / ``"Emc1"`` → ``<i>E</i><sub>mc</sub>`` (italic
    variable + upright subscript), wrapped in a ``<span>`` carrying the guide
    colour.  pyqtgraph's ``InfiniteLine`` renders its label as PLAIN text, so
    ``set_epol_guides`` forces this HTML onto the underlying text item.
    Matches the data-driven Emc/Ema marker typography (``plotting.
    marker_label_html`` → ``gui.rich.var``)."""
    from .rich import var
    lab = str(label).strip()
    inner = var("E", lab[1:]) if lab[:1].upper() == "E" and len(lab) > 1 else lab
    return f'<span style="color:{color}">{inner}</span>'


# ---------------------------------------------------------------------------
# Thousands grouping — SPACE separator (operator: "separate thousands with a
# space instead of a comma"), copy-safe (operator: "do not let the formatting
# of separators affect the value copied from tables for pasting").
# ---------------------------------------------------------------------------
_GROUP_SEP_RE = re.compile(r"(?<=\d) (?=\d)")  # a space BETWEEN two digits


def _group_thousands(value: float, decimals: int = 0) -> str:
    """Format a number with a SPACE thousands separator and a ``.`` decimal
    point (e.g. ``10486`` → ``"10 486"``).  Uses Python's ``_`` grouping then
    swaps in a space, so it round-trips through ``float()`` after
    :func:`_strip_group_sep`.  ``nan`` → ``"nan"`` (callers usually pre-guard
    with ``—``)."""
    if value != value:                       # NaN
        return "nan"
    return f"{value:_.{decimals}f}".replace("_", " ")


def _strip_group_sep(text: str) -> str:
    """Remove the thousands-group separator (a space BETWEEN two digits) so a
    copied cell pastes as a clean number — ``"10 486"`` → ``"10486"`` — while
    genuine spaces before a unit (``"5.000 ms"``, ``"-0.2 V"``) survive."""
    return _GROUP_SEP_RE.sub("", text)


def _tz_abbrev(dt=None) -> str:
    """Short LOCAL time-zone label (operator: "include the time zone").

    The abbreviation when it's short (``MDT`` / ``EST`` / ``UTC``); Windows'
    long ``%Z`` name (``Mountain Daylight Time``) is reduced to its initials
    (``MDT``); otherwise the UTC offset (``UTC-06:00``)."""
    now = (dt if (dt is not None and dt.tzinfo is not None)
           else datetime.now().astimezone())
    name = now.strftime("%Z")
    if name and " " in name:                       # "Mountain Daylight Time"
        ab = "".join(w[0] for w in name.split() if w[:1].isalpha()).upper()
        if ab:
            return ab
    if name and 1 <= len(name) <= 6 and not any(c.isdigit() for c in name):
        return name                                # already short: MDT / UTC
    off = now.strftime("%z")                       # "-0600"
    return f"UTC{off[:3]}:{off[3:]}" if len(off) == 5 else "UTC"


def _wall_stamp(dt=None) -> str:
    """``YYYY-MM-DD HH:MM:SS TZ`` — wall-clock time WITH the local time zone
    (operator: "include the time zone" in the log-pane + update text).  Pass a
    naive/aware ``datetime`` to stamp a specific moment (its local tz)."""
    now = dt if dt is not None else datetime.now()
    aware = now.astimezone() if now.tzinfo is None else now
    return f"{aware.strftime('%Y-%m-%d %H:%M:%S')} {_tz_abbrev(aware)}"


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
                    # Strip the thousands-group SPACE too (operator: "do not
                    # let the formatting of separators affect the value copied
                    # from tables") so "10 486" pastes as a clean number.
                    cells.append("" if text == "—" else _strip_group_sep(text))
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

    #: Font sizes for the experiment plot (operator: "Increase the font
    #: size on the experiment plot"). Axis labels + tick numbers + legend
    #: are bumped well above pyqtgraph's defaults for bench readability.
    _AXIS_LABEL_PT = 14
    _TICK_PT = 12
    _LEGEND_PT = 12
    #: Metric-marker tag font — matched to the tick / legend size so ALL
    #: in-plot text reads at one size (operator: "Match all texts on the
    #: experiment plot the same … match the marker font size with the
    #: plot").  Was pyqtgraph's small default (~9 pt).
    _MARKER_PT = 12
    #: Trace pen width (px).  The main plot AND the inset both use this so
    #: the two match (operator: "Match the line thickness of the inset
    #: plot with the line thickness in the main experiment plot") — the
    #: inset previously drew at width 1.
    _TRACE_PEN_WIDTH = 2
    #: Fractional headroom added to the left Y range in ``align_y_zeros``
    #: so the waveform + its outside-the-trace labels never clip the edge.
    _Y_MARGIN_FRAC = 0.12

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
        # Current inset (operator: "for the inset with Imon, do not have a right
        # axis … Only change between current and current density based on the
        # dropdown list unit").  When the I_mon curve is in the inset, its
        # SINGLE left axis reads ``Current [µA]`` OR ``Current Density [A/cm²]``
        # per the experiment tab's unit dropdown — there is NO separate density
        # right axis (that earlier dual-scale request was reversed).  The right
        # axis is the numberless box mirror, same as the voltage inset.
        # ``multichannel_scope`` sets these via :meth:`set_inset_current_scale`;
        # the stored ``_curve_data[label]`` is already in the chosen unit, so
        # the inset draws it as-is (no conversion).
        self._inset_current_label: Optional[str] = None
        self._inset_current_is_density = False
        self._inset_is_current = False
        # LEFT-axis title for the VOLTAGE inset (inset trace is V_mon or a
        # potential, not I_mon).  Overridable so the caller can apply the
        # "Potential vs <ref>" / "Voltage vs <return>" options (gotcha #159);
        # defaults to the historic "Voltage [V]".  Ignored while the inset
        # shows current.
        self._inset_voltage_label = "Voltage [V]"
        # Default X window applied by the last ``set_traces`` (the
        # pulse-framed range) — ``reset_view`` restores it.
        self._default_xrange: Optional[Tuple[float, float]] = None
        if HAS_PYQTGRAPH:
            # antialias=False — the trace pens are fully OPAQUE, but
            # antialiased edge pixels alpha-blend with whatever is
            # underneath, so crossing traces showed a mixed colour at
            # the intersection (operator: "I am seeing a blended color
            # when the lines intersect. Do not have them transparent
            # and blend colors").  Without antialiasing the top-drawn
            # trace fully covers — crisp scope-style rendering, no
            # colour mixing.
            pg.setConfigOptions(antialias=False, background="w",
                                foreground="k")
            # ---------- main plot with dual Y axes ----------
            self._plot = pg.PlotWidget()
            # Gridlines default OFF on experiment plots — toggled
            # globally by the main window's View → Gridlines action
            # via :meth:`set_grid_visible`.
            self._grid_visible = False
            self._plot.showGrid(x=False, y=False)
            # Axis lines and tick numbers stay BLACK (pyqtgraph default).
            # The axis TITLES are NOT pyqtgraph axis labels — they're Qt
            # widgets painted around the plot in ``_build_plot_box`` (a
            # rotated ``_AxisTitle`` left/right + a QLabel bottom).
            # pyqtgraph's own axis title didn't render on the operator's
            # display (repeatedly reported missing axis labels) even
            # though the tick numbers do, so we don't call ``setLabel``
            # for the titles at all — that also stops pyqtgraph reserving
            # title space.  The dynamic right-axis title (the µA↔A/cm²
            # density conversion) is routed to the Qt label by
            # :meth:`set_axis_labels`.
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
                offset=(8, 8),
                brush=pg.mkBrush(255, 255, 255, 200),
                pen=pg.mkPen("#cccccc"),
            )
            # Compact VERTICAL legend parked in a LEFT CORNER (operator: "make
            # sure the legend does NOT intersect with the plots or labels …
            # considering upper left or lower left").  ``_position_legend``
            # (called from ``set_markers`` after the traces + marker labels are
            # laid out) picks upper-left vs lower-left per capture — whichever
            # corner is clearer of the waveform AND the metric labels.  Single
            # column so the box stays narrow in the corner.
            try:
                self._legend.setColumnCount(1)
            except Exception:
                pass
            # Tighten the box around its entries (operator: "Can you [make]
            # the legend box tighter around the entries?").  Shrink the
            # layout's contents margins + inter-row spacing so the border
            # hugs the sample+label rows instead of leaving pyqtgraph's
            # default padding.
            try:
                self._legend.setSpacing(0)
            except Exception:
                pass
            try:
                _lay = self._legend.layout
                _lay.setContentsMargins(4, 2, 4, 2)
                _lay.setVerticalSpacing(0)
                _lay.setHorizontalSpacing(4)
            except Exception:
                pass
            self._place_legend(corner="UL")
            # Larger legend text for bench readability.
            try:
                self._legend.setLabelTextSize(f"{self._LEGEND_PT}pt")
            except Exception:
                pass
            # Right-axis ViewBox — linked to the main viewbox's X so
            # zoom / pan stays in sync, but its Y is independent. Auto
            # resizes to match the main viewbox's geometry.
            self._right_vb = pg.ViewBox()
            self._plot.showAxis("right")
            self._plot.scene().addItem(self._right_vb)
            # Draw the right ViewBox (current density) BEHIND the main
            # left ViewBox (voltage) so the current trace sits in the
            # back (operator: "the current (density) plot should be in
            # the back").  Added-to-scene order otherwise puts it on top.
            try:
                self._right_vb.setZValue(
                    self._plot.plotItem.vb.zValue() - 1)
            except Exception:
                pass
            self._plot.getAxis("right").linkToView(self._right_vb)
            self._right_vb.setXLink(self._plot.plotItem.vb)
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
            # (Axis TITLES are Qt widgets — see ``_build_plot_box`` — so
            # there's no pyqtgraph title space to reserve here.)
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
            _tick_font = QtGui.QFont()
            _tick_font.setPointSize(self._TICK_PT)
            for _ax_name in ("bottom", "left", "right"):
                try:
                    _ax = self._plot.getAxis(_ax_name)
                    if _ax_name == "bottom":
                        # The TIME axis gets a denser target for short
                        # pulse windows (≤ 2000 µs) so a ~440 µs pulse
                        # isn't left with only 4-5 ticks, while long
                        # windows keep the sparse MATLAB layout
                        # (operator: "too few x ticks for a 440-us
                        # pulse").  See _make_matlab_tick_override.
                        _ax.tickValues = _make_matlab_tick_override(
                            _ax.tickValues, target_count=5,
                            short_span_target=10,
                            short_span_threshold=2000.0)
                    else:
                        _ax.tickValues = _make_matlab_tick_override(
                            _ax.tickValues, target_count=5)
                    # Bigger tick numbers (operator request).
                    _ax.setStyle(tickFont=_tick_font)
                except Exception:
                    pass
            # ---------- viewing controls (zoom / height / reset) -------
            # Same affordances as the Test-parameters pulse preview
            # (operator: "Add the same viewing options in the experiment
            # plot like the test parameters, e.g., x zoom, y zoom,
            # height, and reset").  Zoom scales the visible range about
            # its centre; Height grows / shrinks the plot; Reset restores
            # the default pulse-framed X window + auto-fit Y.
            # ---------- inset plot (hidden by default) ----------
            # Created BEFORE the plot box so ``_build_plot_box`` can place it
            # in the SAME grid COLUMN as the main plot → the two are the same
            # width (operator: "make the inset the same width as the top
            # figure").  Its axis TITLES come from ``_AxisTitle`` widgets in
            # that grid — pyqtgraph's own ``setLabel`` doesn't render on the
            # operator's display (that's why the inset showed NO labels), the
            # same reason the main plot uses ``_AxisTitle`` widgets.  Only the
            # SI-prefix-off + MATLAB-tick + tick-font formatting stays here.
            self._inset = pg.PlotWidget()
            self._inset.setVisible(False)
            # EXPANDING both ways so the inset always fills its grid cell's
            # width (operator: "I am still getting the inset width being
            # squished into a square occasionally").  A pyqtgraph PlotWidget's
            # default size policy lets it settle to a near-square preferred
            # size when the layout under-constrains its width; forcing
            # horizontal Expanding + a minimum width keeps its plot area as
            # wide as the main plot (the two share the vertical splitter's full
            # width, minus the matched axis-title columns).
            self._inset.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Expanding)
            self._inset.setMinimumWidth(120)
            # Minimum only — the max-height cap was removed when the inset
            # moved into the plot/inset SPLITTER (operator: "I allow to
            # stretch between the experiment plot and inset to change
            # their heights"); the splitter handle now sets the height.
            self._inset.setMinimumHeight(80)
            self._inset.showGrid(x=False, y=False)
            for _ax_name in ("bottom", "left"):
                try:
                    self._inset.getAxis(_ax_name).enableAutoSIPrefix(False)
                except Exception:
                    pass
            _inset_tick_font = QtGui.QFont()
            _inset_tick_font.setPointSize(self._TICK_PT)
            for _ax_name in ("bottom", "left"):
                try:
                    _iax = self._inset.getAxis(_ax_name)
                    _iax.tickValues = _make_matlab_tick_override(
                        _iax.tickValues, target_count=5)
                    _iax.setStyle(tickFont=_inset_tick_font)
                except Exception:
                    pass
            # Inset RIGHT axis: tick MARKS mirror the LEFT axis (operator:
            # "hide the ticks for a right axis — make symmetric ticks with
            # the left axis") — MATLAB box style: same tick positions on
            # both sides, numbers only on the left.  Delegating tickValues
            # to the LEFT axis's (already MATLAB-wrapped) function makes
            # divergence impossible: both axes read the same ViewBox
            # y-range and axis height, so the positions are identical.
            # The numbers are hidden in ``_align_inset_axes``
            # (``showValues=False``).
            try:
                self._inset.getAxis("right").tickValues = (
                    self._inset.getAxis("left").tickValues)
            except Exception:
                pass
            # Inset BOTTOM axis: ticks + numbering MATCH THE MAIN PLOT's
            # (operator: "the x axis ticks and numbering for the inset
            # should match the figure above").  Late-bound delegation to
            # the main bottom axis's tickValues — which carries the
            # span-aware MATLAB override (denser 10-tick target for short
            # pulse windows) — so the two axes can never diverge; the
            # x-ranges are already identical via the setXLink below.
            try:
                self._inset.getAxis("bottom").tickValues = (
                    lambda *a, **k:
                        self._plot.getAxis("bottom").tickValues(*a, **k))
            except Exception:
                pass
            self._inset.plotItem.vb.setXLink(self._plot.plotItem.vb)
            layout.addLayout(self._build_view_controls())
            layout.addWidget(self._build_plot_box(), stretch=1)
            # Mouse wheel must NOT zoom (operator request) — use the
            # X+/X−/Y+/Y− buttons instead.  Applied to the main view, the
            # independent right-axis ViewBox, and the inset.
            disable_plot_wheel_zoom(self._plot)
            disable_plot_wheel_zoom(self._right_vb)
            disable_plot_wheel_zoom(self._inset)
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

    # ----------------------------------------------------------- view controls
    #: Plot-height clamp for the +/- buttons (px).
    _MIN_PLOT_H = 160
    _MAX_PLOT_H = 1200
    _HEIGHT_STEP = 40

    def _build_plot_box(self) -> "QtWidgets.QWidget":
        """Wrap ``self._plot`` in a grid with Qt-rendered axis TITLES
        around it — a rotated :class:`_AxisTitle` on the left / right and
        a horizontal one on the bottom — because pyqtgraph's own axis
        title doesn't render on the operator's display (the tick numbers
        do).  ALL THREE titles are ``_AxisTitle`` instances sharing one
        render path, so the bottom "Time [µs]" matches the left/right font
        size exactly (operator: "Time axis label is not the same font size
        as the y axis labels").  The titles live OUTSIDE the PlotWidget so
        they're guaranteed visible regardless of pyqtgraph / display-
        scaling quirks.  The Height +/- controls still resize ``self._plot``
        directly, and the grid's stretchy plot cell grows with it.

        MAIN PLOT and INSET are two blocks of a vertical **QSplitter**
        (operator: "I allow to stretch between the experiment plot and
        inset to change their heights") — drag the handle to trade height
        between them.  Each block is its own grid carrying its titles; the
        plot COLUMNS still align because the left titles are identical
        rotated widgets, the internal axis reserves are matched by
        ``_align_inset_axes``, and the inset block carries a right-side
        PAD (``_inset_right_pad``) whose width is synced to the main
        block's right title so the plot areas stay the same width."""
        # ----- main block: left title | plot | right title, bottom title
        main_w = QtWidgets.QWidget()
        mg = QtWidgets.QGridLayout(main_w)
        mg.setContentsMargins(0, 0, 0, 0)
        mg.setSpacing(2)
        self._left_title = _AxisTitle("Voltage [V]", side="left",
                                      pt=self._AXIS_LABEL_PT)
        self._right_title = _AxisTitle("Current [µA]", side="right",
                                       pt=self._AXIS_LABEL_PT)
        self._bottom_title = _AxisTitle("Time [µs]", side="bottom",
                                        pt=self._AXIS_LABEL_PT)
        mg.addWidget(self._left_title, 0, 0)
        mg.addWidget(self._plot, 0, 1)
        mg.addWidget(self._right_title, 0, 2)
        mg.addWidget(self._bottom_title, 1, 1)
        mg.setColumnStretch(1, 1)
        mg.setRowStretch(0, 1)
        if self._inset is None:
            self._plot_inset_split = None
            self._inset_block = None
            return main_w
        # ----- inset block: its own _AxisTitle widgets (they render;
        # pyqtgraph's setLabel doesn't) + a right pad mirroring the main
        # right title's width.
        inset_w = QtWidgets.QWidget()
        ig = QtWidgets.QGridLayout(inset_w)
        ig.setContentsMargins(0, 0, 0, 0)
        ig.setSpacing(2)
        self._inset_left_title = _AxisTitle("Voltage [V]", side="left",
                                            pt=self._AXIS_LABEL_PT)
        self._inset_bottom_title = _AxisTitle("Time [µs]", side="bottom",
                                              pt=self._AXIS_LABEL_PT)
        # RIGHT title — empty for the voltage inset (just reserves the same
        # width as the main plot's right title so the plot columns line up),
        # set to "Current Density [A/cm²]" for the I_mon inset.  A rotated
        # ``_AxisTitle`` (not a bare pad widget) so it can carry that text.
        self._inset_right_title = _AxisTitle("", side="right",
                                             pt=self._AXIS_LABEL_PT)
        ig.addWidget(self._inset_left_title, 0, 0)
        ig.addWidget(self._inset, 0, 1)
        ig.addWidget(self._inset_right_title, 0, 2)
        ig.addWidget(self._inset_bottom_title, 1, 1)
        ig.setColumnStretch(1, 1)
        ig.setRowStretch(0, 1)
        self._inset_block = inset_w
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        split.addWidget(main_w)
        split.addWidget(inset_w)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        split.setChildrenCollapsible(False)
        self._plot_inset_split = split
        # Hidden until the inset itself is shown (set_inset_visible) —
        # a hidden splitter child gives its space (and the handle) back
        # to the main plot automatically.
        inset_w.setVisible(False)
        return split

    def _build_view_controls(self) -> "QtWidgets.QHBoxLayout":
        """Zoom / height / reset button row — mirrors the pulse-preview
        controls so the experiment plot offers the same affordances."""
        def _btn(text, tip, cb):
            b = QtWidgets.QToolButton()
            b.setText(text); b.setToolTip(tip); b.setAutoRaise(False)
            b.clicked.connect(cb)
            return b
        self.x_in_btn  = _btn("X+", "Zoom in X axis (around view centre)",
                              lambda: self._zoom_axis('x', 0.7))
        self.x_out_btn = _btn("X−", "Zoom out X axis (around view centre)",
                              lambda: self._zoom_axis('x', 1.4))
        self.y_in_btn  = _btn("Y+", "Zoom in Y axis (around view centre)",
                              lambda: self._zoom_axis('y', 0.7))
        self.y_out_btn = _btn("Y−", "Zoom out Y axis (around view centre)",
                              lambda: self._zoom_axis('y', 1.4))
        self.tall_btn  = _btn("+", "Increase plot height (40 px)",
                              lambda: self._change_height(self._HEIGHT_STEP))
        self.short_btn = _btn("−", "Decrease plot height (40 px)",
                              lambda: self._change_height(-self._HEIGHT_STEP))
        self.reset_view_btn = QtWidgets.QToolButton()
        self.reset_view_btn.setText("Reset view")
        self.reset_view_btn.setToolTip(
            "Restore the default time window (pulse-framed) and auto-fit "
            "the voltage / current axes")
        self.reset_view_btn.clicked.connect(self.reset_view)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 2)
        row.setSpacing(4)
        row.addWidget(QtWidgets.QLabel("Zoom:"))
        row.addWidget(self.x_in_btn)
        row.addWidget(self.x_out_btn)
        row.addWidget(self.y_in_btn)
        row.addWidget(self.y_out_btn)
        row.addSpacing(8)
        row.addWidget(QtWidgets.QLabel("Height:"))
        row.addWidget(self.tall_btn)
        row.addWidget(self.short_btn)
        # Reset view sits right after Height − (operator).
        row.addWidget(self.reset_view_btn)
        row.addStretch(1)
        # Far-right slot for an EXTERNAL control the container mounts here — the
        # MultiChannelScope parks its inset toggle in this spot (operator: "move
        # the inset option in [reset view's former] place").
        self._ext_ctrl_slot = QtWidgets.QHBoxLayout()
        self._ext_ctrl_slot.setContentsMargins(0, 0, 0, 0)
        self._ext_ctrl_slot.setSpacing(4)
        row.addLayout(self._ext_ctrl_slot)
        return row

    def mount_extra_control(self, widget) -> None:
        """Host an external control widget (the inset toggle) at the far right
        of the view-controls row.  Adding it reparents it here, removing it from
        any previous page's slot — so the single global control follows the
        active page."""
        slot = getattr(self, "_ext_ctrl_slot", None)
        if slot is not None and widget is not None:
            slot.addWidget(widget)

    def _zoom_axis(self, axis: str, factor: float) -> None:
        """Scale the visible range on one axis by ``factor`` about the
        view centre. ``factor < 1`` zooms in, ``> 1`` zooms out.  X scales
        the shared time axis; Y scales BOTH the left (voltage) and right
        (current) view-boxes together so the dual scales stay aligned."""
        if not HAS_PYQTGRAPH or self._plot is None:
            return

        def _scale(vb, idx):
            if vb is None:
                return
            r0, r1 = vb.viewRange()[idx]
            c = (r0 + r1) / 2.0
            half = max(1e-12, (r1 - r0) / 2.0 * factor)
            if idx == 0:
                vb.setXRange(c - half, c + half, padding=0)
            else:
                vb.setYRange(c - half, c + half, padding=0)

        if axis == 'x':
            _scale(self._plot.plotItem.vb, 0)
        else:
            _scale(self._plot.plotItem.vb, 1)
            _scale(self._right_vb, 1)

    def _change_height(self, delta_px: int) -> None:
        """Grow / shrink the plot by ``delta_px`` (the +/- buttons).
        Raises / lowers the widget's minimum height so it expands within
        the experiment tab's scope/camera splitter; clamped to a sane
        range.  Accumulates off the minimum height we control (NOT the
        rendered ``height()``, which doesn't change until the layout
        re-flows) so repeated clicks step predictably."""
        base = self.minimumHeight() or self.height() or self._MIN_PLOT_H
        new_h = int(max(self._MIN_PLOT_H,
                        min(self._MAX_PLOT_H, base + delta_px)))
        self.setMinimumHeight(new_h)

    def reset_view(self) -> None:
        """Restore the default pulse-framed X window and auto-fit Y.

        The X default is whatever the last :meth:`set_traces` computed
        (cached in ``_default_xrange``); Y is re-auto-ranged and its
        zeros re-aligned via :meth:`align_y_zeros`."""
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        try:
            if (self._default_xrange is not None
                    and self._default_xrange[1] > self._default_xrange[0]):
                self._plot.setXRange(*self._default_xrange, padding=0)
            else:
                self._plot.getViewBox().enableAutoRange(axis='x')
            self._plot.plotItem.vb.enableAutoRange(axis='y')
            if self._right_vb is not None:
                self._right_vb.enableAutoRange(axis='y')
            # Re-align the dual-axis zeros after the auto-fit (no-op when
            # the right axis is empty — the left stays auto-ranged).
            self.align_y_zeros()
        except Exception:
            pass

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

    def _place_legend(self, *, corner: str) -> None:
        """Anchor the trace legend into a corner — ``"UL"``/``"LL"`` (LEFT,
        preferred per operator: "considering upper left or lower left") or
        ``"UR"``/``"LR"`` (RIGHT, fall-backs used only when both left corners
        would overlap a metric label).  ``_position_legend`` chooses which per
        capture so the legend never sits on the waveform OR a marker label."""
        if self._legend is None:
            return
        try:
            if corner == "LL":
                self._legend.anchor(itemPos=(0.0, 1.0), parentPos=(0.0, 1.0),
                                    offset=(8, -8))
            elif corner == "UR":
                self._legend.anchor(itemPos=(1.0, 0.0), parentPos=(1.0, 0.0),
                                    offset=(-8, 8))
            elif corner == "LR":
                self._legend.anchor(itemPos=(1.0, 1.0), parentPos=(1.0, 1.0),
                                    offset=(-8, -8))
            else:                                       # "UL" (default)
                self._legend.anchor(itemPos=(0.0, 0.0), parentPos=(0.0, 0.0),
                                    offset=(8, 8))
        except Exception:
            pass

    def _legend_data_size(self):
        """Legend box size in DATA coords ``(w, h)`` — measured from the
        rendered legend when possible, else a fraction of the view as a
        fallback (before the first paint the legend has no size yet)."""
        try:
            br = self._legend.boundingRect()
            px = self._plot.getViewBox().viewPixelSize()
            w = abs(br.width() * px[0]); h = abs(br.height() * px[1])
            if w > 0 and h > 0:
                return w, h
        except Exception:
            pass
        try:
            (x0, x1), (y0, y1) = self._plot.getViewBox().viewRange()
            return 0.28 * (x1 - x0), 0.30 * (y1 - y0)
        except Exception:
            return 0.0, 0.0

    def _position_legend(self, placed_boxes, avoid_traces) -> None:
        """Park the legend in whichever LEFT corner (upper / lower) is clearer
        of the waveform AND the metric labels (operator: legend must NOT
        intersect the plots or labels).  Scores each corner box by how many
        trace samples fall inside it plus a dominant penalty for overlapping
        any placed label; picks the lower-cost corner."""
        if self._legend is None or self._plot is None:
            return
        try:
            (x0, x1), (y0, y1) = self._plot.getViewBox().viewRange()
            xs, ys = x1 - x0, y1 - y0
            if xs <= 0 or ys <= 0:
                return
            lw, lh = self._legend_data_size()
            lw = min(lw if lw > 0 else 0.28 * xs, 0.6 * xs)
            lh = min(lh if lh > 0 else 0.30 * ys, 0.6 * ys)
            # Candidate corner boxes (bl, br, bb, bt).
            ul = (x0, x0 + lw, y1 - lh, y1)
            ll = (x0, x0 + lw, y0, y0 + lh)
            ur = (x1 - lw, x1, y1 - lh, y1)
            lr = (x1 - lw, x1, y0, y0 + lh)

            def _label_hit(z):
                bl, br, bb, bt = z
                for (_a, _b, _cc, _d) in (placed_boxes or []):
                    if not (_b < bl or _a > br or _d < bb or _cc > bt):
                        return True
                return False

            def _trace_cost(z):
                bl, br, bb, bt = z
                c = 0.0
                for _tx, _ty in avoid_traces:
                    m = (_tx >= bl) & (_tx <= br)
                    if np.any(m):
                        seg = _ty[m]
                        c += float(np.count_nonzero((seg >= bb) & (seg <= bt)))
                return c

            # Score ALL FOUR corners: overlapping a marker LABEL is the
            # dominant penalty, then the number of TRACE samples inside the
            # corner box (operator: "be careful with the legend to not
            # overlap the traces" — the old logic only left the left
            # corners on a LABEL hit, so a trace-filled left corner still
            # won over a completely clear right corner).  The left
            # preference survives as a small TIEBREAK bias only — equal
            # clarity → left corner; any real trace occupancy difference →
            # the clearer corner regardless of side.
            allc = [("UL", ul, 0.0), ("LL", ll, 0.0),
                    ("UR", ur, 0.5), ("LR", lr, 0.5)]
            best = min(
                allc,
                key=lambda t: ((1e6 if _label_hit(t[1]) else 0.0)
                               + _trace_cost(t[1]) + t[2]))[0]
            self._place_legend(corner=best)
        except Exception:
            pass

    def set_epol_guides(self, guides) -> None:
        """Draw faint DASHED VERTICAL guide lines at the EXPECTED electrode-
        polarization sample locations (operator: "put a vertical line at
        each expected location of electrode polarization (12 us after the
        phase) if there are interphase delays or discharge delay").

        ``guides`` is an iterable of ``(label, x_us)`` — one per phase that
        has a trailing interphase/discharge delay, at ``phase_end + 12 µs``
        (see :func:`plotting.expected_epol_times_us`).  These are a
        GEOMETRIC reference from the programmed pattern, drawn independently
        of the data-driven Emc/Ema glyph so the operator can see whether the
        measured marker landed where the pattern says it should — useful for
        smooth waveforms (sinusoidal / gaussian) where onset detection can
        skew the data-driven position.

        Previous guides are cleared first; pass an empty list to wipe.
        """
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        for _it in getattr(self, "_epol_guide_items", []):
            try:
                self._plot.removeItem(_it)
            except Exception:
                pass
        self._epol_guide_items = []
        try:
            x0, x1 = self._plot.getViewBox().viewRange()[0]
        except Exception:
            x0, x1 = None, None
        # Faint dashed grey — a reference, NOT a data trace, so it stays
        # visually subordinate to the waveform + the metric markers.  The
        # polar-marker purple keeps it associated with E_pol without adding
        # a new colour.
        from .. import plotting as _plt
        _guide_color = _plt.MARKER_COLOURS.get("polar", "#7E2F8E")
        for _g in (guides or []):
            try:
                label, x_us = _g[0], float(_g[1])
            except Exception:
                continue
            if x0 is not None and not (x0 <= x_us <= x1):
                continue   # outside the framed window — don't clutter
            pen = pg.mkPen(color=_guide_color, width=1,
                           style=QtCore.Qt.PenStyle.DashLine)
            # CENTER the label on the vertical line (operator: "Center the x
            # line labels to the center") — ``anchors`` x = 0.5 puts the
            # label's horizontal centre on the line (pyqtgraph's default
            # pushes it to whichever side has room).  Both list entries cover
            # the near-vertical / near-horizontal orientations.
            # OPAQUE background behind the label (operator: "I want the
            # background of the x line label to be opaque so that the line does
            # not interfere with the display").  Now that the label is CENTRED
            # on the line, the dashed line runs straight THROUGH the text; an
            # opaque fill matching the white scope plot blocks it so the label
            # stays legible.
            # Keep the label near the BOTTOM (by the x-axis) but OFFSET above
            # it (operator: "I wanted an offset from the x-axis" — not moved to
            # the top).  The centred anchor x=0.5 paired with the old anchor
            # y=0.0 hung the label DOWNWARD and straddled the x-axis; anchor
            # y=1.0 pins the label's BOTTOM edge to the position point so it
            # sits ABOVE it, and position 0.05 places that point a small gap
            # (~5 % of the plot height) above the x-axis.
            line = pg.InfiniteLine(
                pos=x_us, angle=90, pen=pen,
                label=str(label),
                labelOpts={"position": 0.05, "color": _guide_color,
                           "movable": False, "fill": (255, 255, 255, 255),
                           "anchors": [(0.5, 1.0), (0.5, 1.0)]})
            # Variable-format the label (operator: "proper variable
            # formatting") — "Emc" → E with an upright "mc" subscript,
            # italic E.  pyqtgraph's InfiniteLine renders its label as PLAIN
            # text, so force HTML on the underlying text item (a static label
            # with no ``{value}`` placeholder isn't re-formatted on view
            # changes, so the HTML sticks).
            try:
                _html = _epol_guide_label_html(str(label), _guide_color)
                line.label.textItem.setHtml(_html)
                # setHtml changed the rendered width (italic E + subscript vs
                # the plain "Emc" the anchor was first computed from), so
                # re-run the anchor placement to keep it centred on the line.
                line.label.updatePosition()
            except Exception:
                pass
            # ignoreBounds so a guide near the edge can't expand the
            # auto-range and squash the waveform (same rule as the markers).
            self._plot.addItem(line, ignoreBounds=True)
            self._epol_guide_items.append(line)

    def set_markers(self, markers) -> None:
        """Draw metric cursors on the LEFT axis — MATLAB ``getPlot``
        style (operator: "I want the metrics indicated on the plotting
        during the experiment like from my MATLAB code", including
        access voltage + driving voltage).

        After the labels are laid out, the trace legend is parked in whichever
        LEFT corner (upper / lower) is clear of the waveform AND the labels
        (``_position_legend``), so it never overlaps either.

        ``markers``: iterable of either

          * ``(label, x_us, y_v)`` — glyph at the point + a ``label = y V``
            tag (the y IS the displayed value), or
          * ``(label, x_us, y_v, text)`` — same glyph at ``(x, y)`` but the
            tag shows ``text`` verbatim (used for access / driving
            voltage, whose VALUE is a derived quantity rather than the y
            the marker sits at), optionally
          * ``(label, x_us, y_v, text, color)`` — also sets the pen /
            text colour so V_a / V_d read distinctly from the Epol
            cursors, optionally
          * ``(label, x_us, y_v, text, color, symbol)`` — also picks the
            glyph.  ``symbol`` is a pyqtgraph symbol name (e.g. ``"+"``
            for electrode-polarization Emc/Ema markers) OR the sentinel
            ``"hbar"`` for a horizontal-bar glyph (operator: "For access
            and driving voltage plotting, use a horizontal bar symbol.
            For electrode polarization, use plus symbols").  Defaults to
            ``"x"``, optionally
          * ``(label, x_us, y_v, text, color, symbol, html)`` — ``html``
            is a rich-text tag (italic variable, upright subscript —
            operator: "Use proper variable formatting on the plot
            markers") rendered via a ``TextItem(html=…)``.  Falls back to
            the plain ``text`` when ``html`` is ``None``.

        Labels hug their points but never intersect the trace or each
        other and use NO leader lines (operator: "I do not like these
        dashed lines … incorporate [the MATLAB waveform/polarity-based
        mitigation] but more intelligibly"): the glyph sits at the true
        ``(x, y)`` while its TEXT tag is placed just OUTSIDE the local
        trace envelope — above it where the trace runs low, below where it
        runs high (whichever side has more room) — and labels sharing an
        x-neighbourhood are stepped further into that clear margin so they
        don't stack.

        Previous markers are cleared first; pass an empty list to wipe.
        """
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        for _it in getattr(self, "_marker_items", []):
            try:
                self._plot.removeItem(_it)
            except Exception:
                pass
        self._marker_items = []

        # Parse into records first so we can lay out the labels before
        # drawing.  Each record: [x, y, text, color, symbol, html, draw_label].
        # An optional 8th tuple element ``draw_label=False`` draws the GLYPH
        # ONLY, with no text tag (operator: "mark … with no label") — used
        # for the ending-interphase circle and the bare driving bars.
        recs = []
        for _m in (markers or []):
            try:
                _m = tuple(_m)
                _label, _x, _y = _m[0], float(_m[1]), float(_m[2])
                _draw_label = bool(_m[7]) if len(_m) >= 8 else True
                _text = (_m[3] if len(_m) >= 4 and _m[3]
                         else f"{_label} = {_y:.3f} V")
                _color = _m[4] if len(_m) >= 5 and _m[4] else "#333333"
                _symbol = (_m[5] if len(_m) >= 6 and _m[5] else "x")
                _html = _m[6] if len(_m) >= 7 and _m[6] else None
                recs.append([_x, _y, _text, _color, _symbol, _html, _draw_label])
            except Exception:
                pass
        if not recs:
            # No markers (e.g. a transient wipe) — leave the legend where it
            # is; the next real ``set_markers`` re-picks its corner.
            return

        # Smart per-point placement (operator: keep each value NEXT TO its
        # point, but push it into the CLEAR side of the trace so it never
        # crosses the waveform or another label — NO dashed leader lines).
        # For each marker we read the local trace envelope (min/max of the
        # left-axis curves in a small x-window) and put the label just
        # OUTSIDE it on whichever side has more room; then we de-overlap
        # labels that share an x-neighbourhood by stepping them further
        # into the clear margin.
        try:
            (x0, x1), (y0, y1) = self._plot.getViewBox().viewRange()
        except Exception:
            x0, x1, y0, y1 = 0.0, 1.0, 0.0, 1.0
        x_span = max(x1 - x0, 1e-9)
        y_span = max(y1 - y0, 1e-9)
        x_win = 0.02 * x_span       # local-slope window AT the glyph
        # Offset of the label's nearest corner from the glyph — kept SMALL so
        # the tag sits right next to its marker (operator: "move the labels
        # closer to the markers").  Just enough to clear the glyph footprint
        # (~9 px half-size) without the text touching it.
        gap_x = 0.004 * x_span      # small horizontal offset from the glyph
        gap_y = 0.009 * y_span      # small vertical offset from the glyph
        line_h = 0.052 * y_span     # ~one text line (data coords, estimate)
        char_w = 0.0085 * x_span    # ~one char wide (data coords, estimate)

        # Traces the labels must avoid.  The markers sit on the LEFT
        # (voltage) axis — but I_mon lives on the RIGHT axis and shares the
        # screen, so a label that "clears" every voltage trace can still
        # land right on the I_mon curve (operator: "the first access
        # voltage/resistance is intersecting with the plot" — the R_a1 line
        # sat on the teal I_mon trace, which the old left-axis-only
        # avoidance was BLIND to).  Include right-axis curves too, mapping
        # their y into the LEFT-axis coordinate frame: the two ViewBoxes
        # share the screen with zeros aligned by ``align_y_zeros`` (run
        # before this), so a right value ``r`` appears at the same screen
        # position as left value ``l_lo + (r-r_lo)/(r_hi-r_lo)·(l_hi-l_lo)``.
        # All the box math below is in left-axis data units, so the mapped
        # right trace is compared on equal footing.
        try:
            _lly, _lhy = self._plot.getViewBox().viewRange()[1]
        except Exception:
            _lly, _lhy = y0, y1
        _rng_r = None
        if getattr(self, "_right_vb", None) is not None:
            try:
                _rng_r = self._right_vb.viewRange()[1]
            except Exception:
                _rng_r = None
        avoid_traces = []
        for _name, _xy in self._curve_data.items():
            _ax = self._curve_axis.get(_name, "left")
            try:
                _tx = np.asarray(_xy[0], dtype=float)
                _ty = np.asarray(_xy[1], dtype=float)
            except Exception:
                continue
            if not (_tx.size and _ty.size == _tx.size):
                continue
            if _ax != "left":
                # Map right-axis y -> left-axis data coords (same screen y).
                if _rng_r is None:
                    continue
                _rlo, _rhi = _rng_r
                if (_rhi - _rlo) == 0:
                    continue
                _ty = _lly + (_ty - _rlo) / (_rhi - _rlo) * (_lhy - _lly)
            avoid_traces.append((_tx, _ty))

        def _local_envelope(xc):
            lo = hi = None
            for _tx, _ty in avoid_traces:
                m = (_tx >= xc - x_win) & (_tx <= xc + x_win)
                if np.any(m):
                    seg = _ty[m]
                    smin, smax = float(np.min(seg)), float(np.max(seg))
                    lo = smin if lo is None else min(lo, smin)
                    hi = smax if hi is None else max(hi, smax)
            return lo, hi

        def _trace_overlap(bl, br, bb, bt):
            """How far (data units) the label box VERTICALLY overlaps the
            local trace inside its x-range.  GRADED — not the old binary
            in/out — so the scorer can prefer the side that clips the trace
            LESS (operator: "the third access voltage clearly needs to be
            on the LEFT side of the marker because it is intersecting the
            plot").  0.0 = clear of the trace."""
            worst = 0.0
            for _tx, _ty in avoid_traces:
                m = (_tx >= bl) & (_tx <= br)
                if np.any(m):
                    seg = _ty[m]
                    tlo, thi = float(np.min(seg)), float(np.max(seg))
                    ov = min(bt, thi) - max(bb, tlo)
                    # A FLAT trace is a zero-height band, so the band-overlap
                    # length above is 0 even when the line runs straight
                    # through the box — which let the proximity pull park a
                    # label right on a flat trace.  Floor it by how CENTRAL
                    # the band sits in the box (deepest = worst, 0 at the
                    # edge) whenever the band intersects the box vertically,
                    # so the scorer still pushes the tag clear of a flat line.
                    if thi >= bb and tlo <= bt:
                        mid = 0.5 * (tlo + thi)
                        central = max(0.0, min(mid - bb, bt - mid))
                        ov = max(ov, central)
                    if ov > worst:
                        worst = ov
            return worst

        # CANDIDATE-SCORING placement (operator: "position the labels left,
        # right, or one of the four corners of the marker … on opposite
        # sides if necessary").  For each glyph we try 6 placements — the
        # four corners plus pure left / right — and pick the one with the
        # lowest penalty for: running OFF-SCREEN, OVERLAPPING an already-
        # placed tag (the big one — this is what spreads clustered tags to
        # OPPOSITE sides), and INTERSECTING the trace.  A small preference
        # keeps an isolated tag on the trace's open side / to the right.
        # (ax 0=text right of pos / 1=left ; ay 1=above pos / 0=below.)
        _CANDS = [
            ("UR", 0.0, 1.0, +1, +1), ("UL", 1.0, 1.0, -1, +1),
            ("DR", 0.0, 0.0, +1, -1), ("DL", 1.0, 0.0, -1, -1),
            ("R", 0.0, 0.5, +1, 0),   ("L", 1.0, 0.5, -1, 0),
        ]
        # How many label-height/-width tiers a tag may escalate to clear a
        # cluster before it just accepts the least-bad spot (caps how far a
        # tag can fly from its glyph).
        _MAX_LABEL_TIER = 6
        plan = [None] * len(recs)
        placed_boxes = []   # (left, right, bottom, top) of placed tags
        for i in sorted(range(len(recs)), key=lambda i: recs[i][0]):
            _x, _y, _text, _color, _symbol, _html, _draw = recs[i]
            if not _draw:
                continue        # glyph-only marker — nothing to place
            # Estimate the label box from the RENDERED lines.  When an html
            # label is present it is what actually draws (multi-line via
            # ``<br/>``), NOT the single-line plain ``_text`` — e.g. the
            # broken-channel tag renders 4 lines ("broken" / R / C_eff / τ)
            # but its plain text is one comma-joined line.  Using ``_text``
            # under-counted the height (1 line) so the scorer placed the tall
            # tag against a plot edge and its lower lines (C_eff, τ) clipped
            # off-screen — the operator saw "resistance only" on CH07.  Count
            # the html's ``<br/>`` lines (tags stripped for width) instead.
            if _html:
                import re as _re
                _plain_lines = [
                    _re.sub(r"<[^>]+>", "", s)
                    for s in str(_html).replace("<br>", "<br/>").split("<br/>")]
            else:
                _plain_lines = str(_text).split("\n")
            n_lines = max(1, len(_plain_lines))
            lab_h = n_lines * line_h
            lab_w = max((len(s) for s in _plain_lines), default=8) * char_w
            best = None
            best_score = None
            # TIER STACKING: when the signal is small (low current) every
            # marker clusters in a thin y-band and the 6 fixed positions all
            # overlap.  So each candidate may escalate its offset outward in
            # TIERS — one label-height per tier vertically (corners), one
            # label-width per tier horizontally (pure L/R) — until it clears
            # the already-placed tags.  A small per-tier cost keeps the
            # CLOSEST free tier preferred; the flat per-overlap penalty makes
            # escalating cheaper than tolerating an overlap.  Off-screen
            # penalty caps how far it can go (operator: "small current … the
            # marker labels intersect with each other and the plot").
            for tier in range(_MAX_LABEL_TIER + 1):
                for ci, (_nm, ax, ay, dxs, dys) in enumerate(_CANDS):
                    if dys != 0:                 # corner: stack vertically
                        px = _x + dxs * gap_x
                        py = _y + dys * (gap_y + tier * (lab_h + 0.4 * line_h))
                    else:                        # pure L/R: stack horizontally
                        px = _x + dxs * (gap_x + tier * (lab_w + 2.0 * char_w))
                        py = _y
                    bl = px - ax * lab_w
                    br = px + (1.0 - ax) * lab_w
                    bb = py - (1.0 - ay) * lab_h
                    bt = py + ay * lab_h
                    score = 0.0
                    # OFF-SCREEN is a hard constraint: a flat 8.0 (well above
                    # the graded trace/overlap terms) + a graded amount, so a
                    # label NEVER prefers a tiny clip over an on-trace spot.
                    # (operator: "the first access voltage and resistance
                    # label is being clipped by the left axis" — its left
                    # placement clipped only a few px, so the old purely-
                    # graded 30× penalty was tiny and lost to the trace term.)
                    if bl < x0: score += 8.0 + (x0 - bl) / x_span * 30.0
                    if br > x1: score += 8.0 + (br - x1) / x_span * 30.0
                    if bb < y0: score += 8.0 + (y0 - bb) / y_span * 30.0
                    if bt > y1: score += 8.0 + (bt - y1) / y_span * 30.0
                    for pl, pr, pb, pt in placed_boxes:
                        ox = min(br, pr) - max(bl, pl)
                        oy = min(bt, pt) - max(bb, pb)
                        if ox > 0 and oy > 0:
                            # Flat base RAISED (1.0 → 6.0) so ANY label-label
                            # overlap costs more than a proximity tier-step —
                            # else the quadratic proximity pull could make two
                            # close tags tolerate a small overlap instead of
                            # escalating apart.  Readability (non-overlap)
                            # beats proximity for close markers.
                            score += 6.0 + (ox / x_span) * (oy / y_span) * 90.0
                    # GRADED trace-intersection penalty — the more of the
                    # label box over the waveform, the worse, so the scorer
                    # picks the side that clips the trace LEAST.  This is what
                    # makes up/down follow the OPEN side of the trace, so the
                    # placement flips automatically when the polarity flips.
                    _tov = _trace_overlap(bl, br, bb, bt)
                    if _tov > 0.0:
                        # GRADED (prefer the side that clips the trace least)
                        # PLUS a flat base once the overlap is MEANINGFUL
                        # (> ~15 % of the label height).  The flat base makes
                        # trace intersection DOMINATE the proximity pull — a
                        # label must never sit ON a trace just to be nearer its
                        # marker.  This matters most for the doubled-proximity
                        # Emc/Ema "+" tag: the 2nd+ electrode-polarization label
                        # sits at the phase boundary and its box extends into
                        # the flat INTERPULSE trace, which the modest graded-only
                        # penalty didn't outweigh (operator: "the second or
                        # subsequent electrode polarization marker labels … can
                        # overlap the traces during the interpulse").
                        score += (_tov / y_span) * 70.0
                        if _tov > 0.15 * lab_h:
                            # Flat base that DOMINATES the near-marker quadratic
                            # proximity pull (peaks ~34× a full-span gap²) so a
                            # label never sits ON a trace merely to be closer —
                            # it steps to the nearest OFF-trace spot instead
                            # (operator: "be better at placing labels" — tags
                            # were sitting on the steep E_act ramp).  A tier
                            # escalation (0.20 each) or a modest sideways move is
                            # always cheaper than this, so the scorer takes it.
                            score += 9.0
                    score += ci * 0.05                   # mild ordering bias
                    # NOTE: no separate "open side" bias.  The graded
                    # trace-overlap term above already drives up/down to the
                    # clear side of the waveform.  A prior ``(ay>0.5) !=
                    # open_above`` penalty used a SYMMETRIC-window open-side
                    # estimate that mis-fired at the pulse onset — V_a1's
                    # window caught the flat pre-pulse baseline (far ABOVE),
                    # so it wrongly judged "above not open" and pushed the
                    # near above-placement out to a far tier (operator: "the
                    # first access voltage/resistance label is too far from
                    # the marker").  Trace-overlap is the correct, local
                    # signal; the bias was redundant + buggy.
                    score += tier * 0.20                 # prefer the closest tier
                    # PROXIMITY: keep the label NEAR its marker (operator:
                    # "make sure the labels are near their markers").  Penalise
                    # the gap from the marker to the box — vertical (dominant)
                    # + horizontal — so among otherwise-clear spots the
                    # CLOSEST wins and a tag is never flung across the plot.
                    _cy = 0.5 * (bb + bt)
                    # The electrode-polarization tag (Emc / Ema — the ONLY
                    # marker drawn with the "+" glyph) gets a DOUBLED proximity
                    # pull so it stays right next to its marker (operator: "be
                    # sure that the electrode polarization marker label is near
                    # its marker").  Still below the trace-overlap (30×) and
                    # label-overlap (90×) terms, so it prefers the CLOSEST
                    # clear spot without ever sitting on the trace / another tag.
                    _pw = 2.0 if _symbol == "+" else 1.0
                    _gap_v = abs(_cy - _y) / y_span
                    _gap_h = max(0.0, bl - _x, _x - br) / x_span
                    score += _pw * 6.0 * _gap_v
                    # HORIZONTAL pull is stronger than vertical (operator:
                    # "improve the placement of labels" — a trailing access /
                    # driving tag was flying far to the RIGHT of its marker into
                    # open space; the marker's x is fixed, so a big horizontal
                    # gap is almost always avoidable by placing the tag directly
                    # above / below).  Weighting gap_h higher keeps the tag in
                    # the marker's x-column unless that's genuinely blocked.
                    score += _pw * 9.0 * _gap_h
                    # QUADRATIC proximity — near the marker the linear terms
                    # above dominate (gentle, so trace-avoidance still picks
                    # the local clear side), but this grows STEEPLY with
                    # distance so a label can NEVER fly across the plot into
                    # empty space to dodge the trace (operator: "The placement
                    # of the labels need to be better" — on a noisy sine
                    # capture the trace covered every nearby spot, so the 45×
                    # trace penalty pushed the access tags far from their
                    # markers; the quadratic pull caps how far they can go so
                    # they stay near-but-off the trace instead).
                    score += _pw * 34.0 * (_gap_v * _gap_v + _gap_h * _gap_h)
                    if best_score is None or score < best_score:
                        best_score = score
                        best = (px, py, ax, ay, bl, br, bb, bt)
            px, py, ax, ay, bl, br, bb, bt = best
            plan[i] = [px, py, ax, ay]
            placed_boxes.append((bl, br, bb, bt))

        for i, (_x, _y, _text, _color, _symbol, _html, _draw) in enumerate(recs):
            try:
                # The bar (V_a/V_d) and the plus (Emc/Ema) are BOTH stroked
                # line paths drawn with the SAME pen width, so they read at
                # matching thickness — the bar thicker than its old 2 px,
                # the plus thinner than pyqtgraph's filled "+" glyph
                # (operator: "horizontal bar thicker and plus thinner —
                # matching thickness").  "o" = a small CLOSED (filled) circle
                # (operator: "change the marker for ending interphase
                # potential as a smaller closed circle").
                # Smaller glyphs (operator: "make the markers smaller") —
                # were 18 / 14 / 6 / 12 px.
                _brush = pg.mkBrush(_color)
                if _symbol == "hbar":
                    sym = _hbar_symbol(); size = 11; pen_w = 2
                elif _symbol == "+":
                    sym = _plus_symbol(); size = 9; pen_w = 2
                elif _symbol == "o":
                    # Small FILLED dot — smaller than the bar / plus glyphs;
                    # an unlabelled position marker that shouldn't compete
                    # visually with the metric markers.
                    sym = "o"; size = 5; pen_w = 1   # filled (_brush = _color)
                else:
                    sym = _symbol; size = 8; pen_w = 1
                sp = pg.ScatterPlotItem(
                    [_x], [_y], symbol=sym, size=size,
                    pen=pg.mkPen(_color, width=pen_w),
                    brush=_brush)
                # ignoreBounds: glyphs / labels must NOT drive the ViewBox
                # auto-range, else an off-trace label would expand the
                # y-range every refresh and squash the waveform flat.
                self._plot.addItem(sp, ignoreBounds=True)
                self._marker_items.append(sp)
                # Glyph-only marker (no label requested) — done.
                if not _draw or plan[i] is None:
                    continue
                pos_x, pos_y, anchor_x, anchor_y = plan[i]
                # Marker tags render at the SAME size as the tick / legend
                # text so all in-plot text matches (operator: "match the
                # marker font size with the plot").
                # Semi-transparent WHITE background (alpha 0.2) so a label can
                # OVERLAP a trace without fully covering it (operator: "give
                # the marker labels a partially transparent white background …
                # alpha 0.2").
                _label_fill = pg.mkBrush(255, 255, 255, 51)   # 51/255 ≈ 0.2
                if _html:
                    ti = pg.TextItem(
                        html=(f'<span style="color:{_color};'
                              f'font-size:{self._MARKER_PT}pt;">{_html}</span>'),
                        anchor=(anchor_x, anchor_y), fill=_label_fill)
                else:
                    ti = pg.TextItem(_text, color=_color,
                                     anchor=(anchor_x, anchor_y),
                                     fill=_label_fill)
                    _mf = QtGui.QFont()
                    _mf.setPointSize(self._MARKER_PT)
                    ti.setFont(_mf)
                ti.setPos(pos_x, pos_y)
                self._plot.addItem(ti, ignoreBounds=True)
                self._marker_items.append(ti)
            except Exception:
                pass

        # Park the legend in the clearer LEFT corner (upper / lower) so it
        # never sits on the waveform OR a metric label (operator: "make sure
        # the legend does NOT intersect with the plots or labels … considering
        # upper left or lower left").  ``avoid_traces`` + ``placed_boxes`` are
        # the same trace / label geometry the label scorer just used.
        self._position_legend(placed_boxes, avoid_traces)

    def set_traces(self, time_us: np.ndarray,
                   traces: Dict[str, np.ndarray],
                   colors: Optional[Dict[str, str]] = None,
                   axis: Optional[Dict[str, str]] = None,
                   styles: Optional[Dict[str, str]] = None,
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
        styles = styles or {}

        def _pen_for(_name, _color):
            # ``styles[name] == "dash"`` draws a DASHED line (Ghazavi
            # corrected-waveform overlay E′act / E′ret — reads as the de-ohm'd
            # companion of the solid measured trace); default solid.
            if styles.get(_name) == "dash":
                return pg.mkPen(color=_color, width=self._TRACE_PEN_WIDTH,
                                style=QtCore.Qt.PenStyle.DashLine)
            return pg.mkPen(color=_color, width=self._TRACE_PEN_WIDTH)
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
        # NOTE: the time axis is taken AS-IS from the acquisition — it is
        # built in ``TektronixOscilloscope._read_channel`` as
        # ``t_us = (XZEro + n·XINcr)·1e6`` (record length + sample
        # interval + first-sample offset), a direct port of MATLAB
        # ``getTime.m`` (``XUNits = (idx-1)·XINcr + XZEro``), plus the
        # ``getTime2.m`` 1.2 µs digital-sync correction for digital
        # triggers so t=0 lands on the stim phase-1 onset.  There is NO
        # active software shift here for "zero placement" — proper t=0
        # comes from the acquisition (trigger source + XZEro), per
        # operator spec.  (A prior revision detected the pulse span and
        # slid the axis; that was an active adjustment and was removed.)
        for name, y in traces.items():
            target_axis = axis.get(name, AXIS_LEFT)
            target_color = colors.get(name, "k")
            self._curve_color[name] = target_color
            self._curve_data[name] = (time_us, y)
            if target_axis == AXIS_NA:
                # DATA-ONLY trace: keep ``_curve_data`` (+ colour) so the INSET
                # can draw it, but DON'T show it in the main plot — it's
                # selected for the inset while HIDDEN from the main plot
                # (operator: "The inset trace should not have to also be in the
                # main plot as well").  Remove any existing visible main-plot
                # curve; the inset copy (if any) is refreshed by _refresh_inset.
                _old = self._curves.pop(name, None)
                if _old is not None:
                    (self._right_vb
                     if self._curve_axis.get(name) == AXIS_RIGHT
                     else self._plot).removeItem(_old)
                    if self._legend is not None:
                        try:
                            self._legend.removeItem(_old)
                        except Exception:
                            pass
                self._curve_axis.pop(name, None)
                _ic = self._inset_curves.get(name)
                if _ic is not None:
                    _ic.setData(time_us, y)
                continue
            if name in self._curves:
                # Existing curve: update data and (if changed) move to
                # the target axis / re-pen the colour.
                self._curves[name].setData(time_us, y)
                self._curves[name].setPen(_pen_for(name, target_color))
                if self._curve_axis.get(name) != target_axis:
                    self._move_curve(name, target_axis)
            else:
                pen = _pen_for(name, target_color)
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
                    # X range per MATLAB getPlot_Tek.m / getAcutePlot3.m:
                    # xlim([round(min(time),1sig), round(max(time),1sig)]) —
                    # the full capture extent, each end rounded to 1 sig fig,
                    # nothing else (operator: "used the same time range
                    # adjustment as what I did in MATLAB").  SAME rule as the
                    # POLARIS export.  The pre<post interpulse asymmetry the
                    # operator wants comes from the ACQUISITION trigger
                    # position (auto_layout_for_pulse), not the plot framing.
                    _mxr = _matlab_x_range(_t, target_count=10)
                    if _mxr is not None:
                        x_min, x_max = _mxr
                    self._plot.setXRange(x_min, x_max, padding=0)
                    # Remember this window so "Reset view" can restore it
                    # after the user has zoomed / panned.
                    self._default_xrange = (x_min, x_max)
        except Exception:
            pass
        # Refresh inset traces if the inset is showing — a previously-
        # selected trace's data may have changed.
        if self._inset_visible:
            self._refresh_inset()

    #: Leading / trailing display margins as a fraction of the detected
    #: pulse width, for the asymmetric pulse centering.  Leading <
    #: trailing so the preceding interpulse reads SMALLER than the
    #: proceeding one (operator preference).
    _PULSE_PRE_MARGIN_FRAC = 0.15
    _PULSE_POST_MARGIN_FRAC = 0.55

    def _detect_pulse_span(self, t: np.ndarray, traces) -> Optional[tuple]:
        """Detect the active-pulse span ``(t0, t1)`` µs from the trace
        with the largest peak-to-peak swing — samples deviating > 10 %
        of its robust (1/99-percentile) p2p from its median.  Returns
        ``None`` when no pulse is detectable (e.g. an all-idle capture).

        Shared by the t=0 onset shift (slides ``t0`` to zero) and the
        asymmetric x-range framing.  The 1/99-percentile p2p keeps a
        brief compliance-switching transient from distorting the
        threshold.
        """
        try:
            best = None
            best_p2p = 0.0
            for arr in (traces or {}).values():
                a = np.asarray(arr, dtype=float)
                if a.size != t.size or a.size < 8:
                    continue
                p2p = float(np.percentile(a, 99.0) - np.percentile(a, 1.0))
                if p2p > best_p2p:
                    best_p2p = p2p
                    best = a
            if best is None or best_p2p <= 0.0:
                return None
            med = float(np.median(best))
            active = np.abs(best - med) > (0.10 * best_p2p)
            if not active.any():
                return None
            idx = np.flatnonzero(active)
            return (float(t[idx[0]]), float(t[idx[-1]]))
        except Exception:
            return None

    def _asymmetric_pulse_xrange(self, t: np.ndarray, traces) -> Optional[tuple]:
        """Frame the pulse with a smaller preceding than proceeding
        interpulse.  Returns ``(x_min, x_max)`` µs, or ``None`` to fall
        back to the full data extent.

        Called AFTER the t=0 onset shift, so the detected pulse start
        ``t0`` is ≈ 0 and the frame is ``[-pre·w, +post·w]``.  Never
        crops the pulse itself — the margins sit OUTSIDE ``[t0, t1]``.
        """
        try:
            tmin, tmax = float(t.min()), float(t.max())
            span = self._detect_pulse_span(t, traces)
            if span is None:
                return None
            t0, t1 = span
            w = max(t1 - t0, 1e-9)
            x_min = max(tmin, t0 - self._PULSE_PRE_MARGIN_FRAC * w)
            x_max = min(tmax, t1 + self._PULSE_POST_MARGIN_FRAC * w)
            if x_max <= x_min:
                return None
            return (x_min, x_max)
        except Exception:
            return None

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
        if yr_hi == 0.0 and yr_lo == 0.0:
            return
        # Make BOTH axes SYMMETRIC about zero and SNAP each to the nice-tick
        # grid (operator: "the axes need to begin and end with a tick" +
        # "same for PULSAR and POLARIS").  Two symmetric ranges both put 0
        # at screen centre, so the zeros still coincide — the alignment this
        # method has always guaranteed — AND the top/bottom of each axis now
        # land exactly on a tick, matching the POLARIS/matplotlib export
        # (which sizes both axes with the symmetric _symmetric_ylim).  The
        # ``_Y_MARGIN_FRAC`` headroom (so the waveform + its outside-the-
        # envelope labels stay on-screen) is folded into ``big`` before the
        # snap.
        big_l = max(abs(yl_lo), abs(yl_hi)) * (1.0 + self._Y_MARGIN_FRAC)
        big_r = max(abs(yr_lo), abs(yr_hi)) * (1.0 + self._Y_MARGIN_FRAC)
        if big_l <= 0.0 and big_r <= 0.0:
            return
        nl_lo, nl_hi = _snap_range_to_ticks(-big_l, big_l, target_count=6)
        nr_lo, nr_hi = _snap_range_to_ticks(-big_r, big_r, target_count=6)
        try:
            left_vb.enableAutoRange(axis=left_vb.YAxis, enable=False)
            right_vb.enableAutoRange(axis=right_vb.YAxis, enable=False)
            if big_l > 0.0:
                left_vb.setYRange(nl_lo, nl_hi, padding=0)
            if big_r > 0.0:
                right_vb.setYRange(nr_lo, nr_hi, padding=0)
        except Exception:
            pass
        # Pin each axis's reserved tick-text width to its ACTUAL tick
        # numbers so the Qt axis TITLE hugs them symmetrically (operator:
        # "the right axis label is too far from the numbering — match the
        # gap like the left axis").  pyqtgraph's autoExpandTextSpace floors
        # the reserve at ~30 px and never shrinks for SHORT numbers, so the
        # right-axis density ticks ("2", "−2") left a wide gap while the
        # longer left-axis voltage ticks ("−0.2") sat close.  Done after
        # both ranges are final and BEFORE set_markers (which reads the
        # settled geometry).
        self._fit_axis_text_space("left")
        self._fit_axis_text_space("right")
        # Keep the inset's plot area the same width as the main plot's (its
        # axis margins are re-derived here, after the main plot's are final).
        self._align_inset_axes()

    def _required_tick_width(self, ax, vb) -> float:
        """PIXEL width needed for ``ax``'s rendered tick strings over ``vb``'s
        y-range — measured from the strings actually shown (so it never clips
        a wide ``−2000`` nor over-reserves for a short ``2``).  Works for ANY
        axis (main plot OR inset).  Returns 0.0 on any failure."""
        if not HAS_PYQTGRAPH or ax is None or vb is None:
            return 0.0
        try:
            lo, hi = vb.viewRange()[1]
            if not (hi > lo):
                return 0.0
            # ``size`` is the axis length in PIXELS (vertical axis → its
            # height), NOT the data span — pass the wrong one and pyqtgraph
            # picks absurd tick spacing and renders 11-digit strings.
            size_px = max(float(ax.height()), 1.0)
            tvals = ax.tickValues(lo, hi, size_px)
            strings: list = []
            for _spacing, vals in tvals:
                got = ax.tickStrings(list(vals), getattr(ax, "scale", 1.0),
                                     _spacing)
                if got:
                    strings.extend(got)
            if not strings:
                return 0.0
            tf = ax.style.get("tickFont")
            if tf is None:
                tf = QtGui.QFont()
                tf.setPointSize(self._TICK_PT)
            fm = QtGui.QFontMetrics(tf)
            return float(max((fm.horizontalAdvance(str(s)) for s in strings),
                             default=0))
        except Exception:
            return 0.0

    def _fit_axis_text_space(self, which: str) -> None:
        """Size ``which`` ('left'/'right') MAIN-plot axis's reserved tick-text
        width to the rendered tick strings for its current range, so the axis
        TITLE sits the same small distance from the numbers on both sides.
        No-op without pyqtgraph."""
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        try:
            ax = self._plot.getAxis(which)
            vb = (self._right_vb if which == "right"
                  else self._plot.plotItem.vb)
            w = self._required_tick_width(ax, vb)
            if w > 0:
                ax.setStyle(autoExpandTextSpace=False,
                            tickTextWidth=int(w) + 6)
        except Exception:
            pass

    def set_axis_labels(self, *, left: Optional[str] = None,
                        right: Optional[str] = None) -> None:
        """Update the left / right axis TITLE text. ``None`` leaves a side
        unchanged.

        The titles are the Qt ``_AxisTitle`` widgets built in
        ``_build_plot_box`` (NOT pyqtgraph axis labels — those don't
        render on the operator's display).  ``<br/>`` in the incoming
        text (e.g. the two-line density label) is flattened to a space
        since the rotated single-line widget can't wrap; HTML tags are
        stripped for the same reason."""
        if self._plot is None:
            return

        def _plain(s: str) -> str:
            s = s.replace("<br/>", "  ").replace("<br>", "  ")
            # crude tag strip — the titles only ever carry <br/> in practice
            while "<" in s and ">" in s:
                a = s.index("<"); b = s.index(">", a)
                if b < a:
                    break
                s = s[:a] + s[b + 1:]
            return s
        if left is not None and getattr(self, "_left_title", None) is not None:
            self._left_title.setText(_plain(left))
        if right is not None and getattr(self, "_right_title", None) is not None:
            self._right_title.setText(_plain(right))

    # --------------------------------------------------------------- inset
    def set_inset_visible(self, visible: bool) -> None:
        """Show or hide the small inset plot below the main view."""
        if self._inset is None:
            return
        self._inset_visible = bool(visible)
        self._inset.setVisible(self._inset_visible)
        # The inset's axis-TITLE widgets live inside the inset BLOCK of the
        # plot/inset splitter — show / hide the whole block with the inset.
        for _t in (getattr(self, "_inset_left_title", None),
                   getattr(self, "_inset_right_title", None),
                   getattr(self, "_inset_bottom_title", None),
                   getattr(self, "_inset_block", None)):
            if _t is not None:
                _t.setVisible(self._inset_visible)
        if self._inset_visible:
            # Seed the splitter to a ~7:3 main:inset split the FIRST time the
            # inset appears at a REAL size (a QSplitter keeps its prior sizes
            # — with a hidden child at 0 — so without this the inset would
            # show at its bare minimum).  Later drags are the operator's to
            # keep.
            #
            # DEFER the seed (and the one-shot lock) until the splitter has a
            # usable total height: a premature toggle — e.g. before the
            # experiment tab has been shown / laid out — reports a tiny (or
            # zero) total, and seeding 30 % of that would lock a sliver-sized
            # inset that never recovers (operator: "Something happened to the
            # inset to be small").  Until then the splitter's 3:1 stretch
            # factors already give a sensible default, and we retry on the
            # next show.  ``_seed_inset_split`` is also called from the
            # widget's ``showEvent`` so the seed lands once the panel is
            # genuinely on screen.
            self._seed_inset_split()
            self._refresh_inset()
            self._align_inset_axes()

    def _seed_inset_split(self) -> None:
        """Seed the plot/inset splitter to ~7:3 once it has a real height.

        No-op if the inset is hidden, already seeded, or the splitter total
        is still too small to divide into a usable inset (see
        :meth:`set_inset_visible` for why the deferral matters).
        """
        if not getattr(self, "_inset_visible", False):
            return
        split = getattr(self, "_plot_inset_split", None)
        if split is None or getattr(self, "_inset_split_seeded", False):
            return
        try:
            total = sum(split.sizes())
            inset_min = self._inset.minimumHeight() if self._inset else 80
            # Require enough height for a comfortable inset before locking —
            # ~4× the inset minimum (or 320 px, whichever is larger).
            if total >= max(4 * inset_min, 320):
                inset_h = max(int(total * 0.3), inset_min)
                split.setSizes([total - inset_h, inset_h])
                self._inset_split_seeded = True
        except Exception:
            pass

    def showEvent(self, ev):   # noqa: N802 (Qt override)
        """Seed the inset splitter once the panel is genuinely on screen.

        The inset visibility may have been toggled on during construction
        (before any real geometry existed), so ``set_inset_visible``'s
        deferred seed retries here now that the splitter has a true height.
        """
        try:
            super().showEvent(ev)
        finally:
            self._seed_inset_split()
            # Re-align the inset axes now that the panel is genuinely on
            # screen: a first alignment attempted while the page was still
            # laid-out-at-zero (the first-capture case) was SKIPPED by the
            # geometry guard in ``_align_inset_axes`` and left the inset at a
            # default reserve; recompute against the real geometry so the
            # inset plot matches the main plot's width (operator: "the first
            # capture inset being squished into a square").  Deferred one tick
            # so the layout has settled the axis heights before we measure.
            if getattr(self, "_inset_visible", False):
                try:
                    QtCore.QTimer.singleShot(0, self._align_inset_axes)
                except Exception:
                    self._align_inset_axes()

    def resizeEvent(self, ev):   # noqa: N802 (Qt override)
        """Re-align the inset axes on any width change so the inset plot area
        keeps matching the main plot's (operator: "even besides the first
        capture, the inset can be squished").  Deferred so the new geometry
        has settled before the tick-width measurement runs."""
        try:
            super().resizeEvent(ev)
        finally:
            if getattr(self, "_inset_visible", False):
                try:
                    QtCore.QTimer.singleShot(0, self._align_inset_axes)
                except Exception:
                    pass

    def _align_inset_axes(self) -> None:
        """Align the inset's plot area with the main plot's, edge-for-edge, and
        configure its right axis for the current vs voltage mode.

        Both the VOLTAGE and CURRENT (I_mon) insets use a MATLAB-box-style
        right axis — a visible right SPINE with tick MARKS mirroring the LEFT
        axis's positions but NO numbers (``_apply_inset_labels`` →
        ``_restore_inset_right_mirror``).  The I_mon inset's SINGLE left axis
        reads Current [µA] or Current Density [A/cm²] per the dropdown; there is
        NO separate density right axis.  Either way the inset shares the main
        plot's
        width: set both left axes to the wider reserve, reserve the main plot's
        right-axis width on the inset right axis, and keep the inset's right
        TITLE column ≥ the main right title width so the two blocks' plot
        columns line up edge-for-edge (a collapsed right title would run the
        inset wider than the main plot)."""
        if self._inset is None or not self._inset_visible:
            return
        try:
            # Right-axis rendering (numbers/tickStrings + titles) FIRST so the
            # width math below sees the final axis state.
            self._apply_inset_labels()
            il = self._inset.getAxis("left")
            self._inset.showAxis("right")
            ir = self._inset.getAxis("right")
            ml = self._plot.getAxis("left")
            mr = self._plot.getAxis("right")
            _mvb = self._plot.plotItem.vb
            _ivb = self._inset.plotItem.vb
            # ALWAYS pin the inset axes to a fixed tick-text reserve — NEVER
            # leave ``autoExpandTextSpace=True`` on the inset (operator: "even
            # besides the first capture, the inset can be squished").  Root
            # cause of the PERSISTENT squish: when the inset shows a FLAT trace
            # (a monopolar E_ret near 0), pyqtgraph auto-ranges the y to a tiny
            # window and renders long decimal labels ("0.00102", "0.00099");
            # with autoExpand ON, the LEFT axis then reserves a huge width that
            # collapses the inset plot area to a square.  A previous
            # early-return geometry guard made this WORSE — it skipped the
            # reserve entirely, leaving autoExpand on.  So we now ALWAYS turn
            # autoExpand OFF with a clamped reserve.
            _WMAX = 90                      # real tick numbers need ≤ ~60 px
            # ``ready`` = geometry settled enough to MEASURE the rendered tick
            # strings.  When not ready (inset toggled before layout — the
            # first-capture case), fall back to a sane default reserve; the
            # showEvent / next capture re-run this with the measured widths.
            ready = (self.isVisible()
                     and _ivb.height() >= 40 and _mvb.height() >= 40
                     and self._plot.width() >= 80)
            if ready:
                lw = min(_WMAX, int(max(self._required_tick_width(ml, _mvb),
                             self._required_tick_width(il, _ivb), 8.0)) + 6)
                rw = min(_WMAX, int(max(self._required_tick_width(mr, self._right_vb),
                             self._required_tick_width(ir, _ivb), 8.0)) + 6)
                # Match the MAIN axes too so the plot columns line up
                # edge-for-edge (same total axis width → same plot x-extent).
                ml.setStyle(autoExpandTextSpace=False, tickTextWidth=lw)
                mr.setStyle(autoExpandTextSpace=False, tickTextWidth=rw)
            else:
                lw = rw = 46                # sane default until we can measure
            il.setStyle(autoExpandTextSpace=False, tickTextWidth=lw)
            ir.setStyle(autoExpandTextSpace=False, tickTextWidth=rw)
            # Keep the inset's right TITLE column matched to the main right
            # title — CAPPED at 40 px (a rotated ``_AxisTitle`` is never wider
            # than its line height ~25 px; a runaway value here would push the
            # inset plot column narrow and leave empty space to its right).
            rt = getattr(self, "_inset_right_title", None)
            if rt is not None:
                rt.setMinimumWidth(min(40, max(0, int(self._right_title.width()))))
        except Exception:
            pass

    def is_inset_visible(self) -> bool:
        return self._inset_visible

    def set_inset_current_scale(self, label: Optional[str], *,
                                density: bool = False) -> None:
        """Tell the inset that ``label`` is the I_mon (current) curve.  Its
        SINGLE left axis then reads ``Current [µA]`` or ``Current Density
        [A/cm²]`` per the ``density`` flag — there is NO separate density right
        axis (operator: "for the inset with Imon, do not have a right axis …
        Only change between current and current density based on the dropdown
        list unit").  The stored ``_curve_data[label]`` is ALREADY in the
        chosen unit (the main plot renders I_mon in whichever unit the dropdown
        selects), so the inset draws it as-is — no conversion.  The right axis
        is the numberless box mirror, same as the voltage inset.  Call with
        ``label=None`` to clear (the voltage inset).  ``multichannel_scope``
        drives this each refresh.
        """
        if self._inset is None:
            return
        self._inset_current_label = str(label) if label else None
        self._inset_current_is_density = bool(density)
        if self._inset_visible:
            self._refresh_inset()
            self._align_inset_axes()

    def set_inset_voltage_label(self, text: Optional[str]) -> None:
        """Override the inset's LEFT-axis title for the VOLTAGE inset (inset
        trace = V_mon or a potential, not I_mon).  Lets ``multichannel_scope``
        apply the "Potential vs <ref>" / "Voltage vs <return>" options
        (gotcha #159) to the inset just like the main axis.  ``None`` / empty
        restores "Voltage [V]".  Ignored while the inset shows current."""
        new = str(text) if text else "Voltage [V]"
        if new == getattr(self, "_inset_voltage_label", "Voltage [V]"):
            return
        self._inset_voltage_label = new
        if (self._inset is not None and self._inset_visible
                and not self._inset_is_current):
            self._apply_inset_labels()
            self._align_inset_axes()

    def _apply_inset_labels(self) -> None:
        """Set the inset axis TITLES + y-axis numbering for the current vs
        voltage inset (operator: "numbering on the y axis if there is a label
        on the inset")."""
        if self._inset is None:
            return
        il = self._inset.getAxis("left")
        ir = self._inset.getAxis("right")
        if self._inset_is_current:
            # SINGLE axis: Current [µA] OR Current Density [A/cm²] per the
            # dropdown unit — numbers on.  NO separate density right axis
            # (operator reversed that); the right axis is the numberless box
            # mirror, identical to the voltage inset.
            if getattr(self, "_inset_left_title", None) is not None:
                self._inset_left_title.setText(
                    "Current Density [A/cm²]"
                    if self._inset_current_is_density else "Current [µA]")
            try:
                il.setStyle(showValues=True)
            except Exception:
                pass
            if getattr(self, "_inset_right_title", None) is not None:
                self._inset_right_title.setText("")
            self._restore_inset_right_mirror(il, ir)
        else:
            # Voltage / potential inset — "Voltage [V]" by default, or the
            # caller's "Potential vs <ref>" / "Voltage vs <return>" override
            # (gotcha #159).  Numberless symmetric right mirror either way.
            if getattr(self, "_inset_left_title", None) is not None:
                self._inset_left_title.setText(
                    getattr(self, "_inset_voltage_label", None)
                    or "Voltage [V]")
            try:
                il.setStyle(showValues=True)
            except Exception:
                pass
            if getattr(self, "_inset_right_title", None) is not None:
                self._inset_right_title.setText("")
            self._restore_inset_right_mirror(il, ir)

    @staticmethod
    def _restore_inset_right_mirror(il, ir) -> None:
        """Right axis = numberless mirror of the left (tick MARKS only, no
        numbers).  Uses ``showValues=True`` with EMPTY tick strings (rather
        than ``showValues=False``) so the axis still RESERVES its
        ``tickTextWidth`` — a ``showValues=False`` axis reserves no text space,
        collapsing its width to ~0 and letting the inset plot column run wider
        than the main plot's (the misaligned-right / squish symptom).  The
        empty strings paint nothing, so it reads as a numberless mirror while
        keeping the SAME width as the main right axis for edge-for-edge
        alignment."""
        ir.tickValues = il.tickValues
        # Blank tick strings — reserves width (via tickTextWidth) but shows no
        # numbers.  ``values`` is the tick-value list; return one "" per tick.
        ir.tickStrings = (lambda values, scale, spacing:
                          ["" for _ in values])
        try:
            ir.setStyle(showValues=True,
                        tickLength=il.style.get("tickLength", -5))
            ir.setPen(il.pen())
        except Exception:
            pass

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
            self._align_inset_axes()

    def inset_traces(self) -> set:
        return set(self._inset_trace_names)

    def _refresh_inset(self) -> None:
        """Synchronise the inset's curves with the current selection."""
        if self._inset is None:
            return
        # Is the inset showing the CURRENT (I_mon) curve?  If so, its single
        # left axis reads Current [µA] or Current Density [A/cm²] per the
        # dropdown (see _apply_inset_labels); the data is drawn AS-IS from
        # ``_curve_data`` (already in the chosen unit — no conversion).
        self._inset_is_current = bool(
            self._inset_current_label
            and self._inset_current_label in self._inset_trace_names)
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
            # The inset draws the curve AS-IS: ``_curve_data`` already holds the
            # unit the main plot rendered (µA, or A/cm² in density mode), which
            # is exactly what the inset's single axis shows — no conversion.
            color = self._curve_color.get(name, "k")
            # Same pen width as the main plot (operator: "Match the line
            # thickness of the inset plot with the line thickness in the
            # main experiment plot").
            pen = pg.mkPen(color=color, width=self._TRACE_PEN_WIDTH)
            if name in self._inset_curves:
                self._inset_curves[name].setData(time_us, y)
                self._inset_curves[name].setPen(pen)
            else:
                self._inset_curves[name] = self._inset.plot(
                    time_us, y, pen=pen, name=name)
        # Titles + y-axis numbering follow the current/voltage mode.
        self._apply_inset_labels()


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
        # Draw the rich text in the THEME's text colour.  A fresh PaintContext
        # defaults to QTextDocument's own black Text role, so on a dark theme
        # the HTML/variable labels rendered off-colour (a washed purple) while
        # the plain-text rows used the palette windowText — a visible mismatch
        # (operator: "fix the font color").  Pull the colour from the item's
        # own palette (HighlightedText when selected, Text otherwise).
        _selected = bool(option.state & QtWidgets.QStyle.StateFlag.State_Selected)
        _role = (QtGui.QPalette.ColorRole.HighlightedText if _selected
                 else QtGui.QPalette.ColorRole.Text)
        ctx.palette.setColor(
            QtGui.QPalette.ColorRole.Text,
            option.palette.color(QtGui.QPalette.ColorGroup.Active, _role))
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


def _fmt_cumulative_charge(nc: float) -> str:
    """Auto-scale a charge in nanocoulombs to nC / µC / mC for display.

    Cumulative delivered charge spans a wide range across a run (one VT
    capture ≈ Q_ph × N_avg ≈ tens of µC; a full ramp ≈ hundreds of µC to
    a few mC), so a fixed unit would either lose precision or read as a
    huge number.  Picks the unit that keeps the value in a readable 1-3
    digit range.
    """
    a = abs(nc)
    if a < 1e3:
        return f"{nc:.1f} nC"
    if a < 1e6:
        return f"{nc / 1e3:.2f} µC"
    return f"{nc / 1e6:.3f} mC"


def _fmt_energy(uj: float) -> str:
    """Auto-scale a driving energy in microjoules to pJ / nJ / µJ / mJ.

    A single neural-stim pulse delivers tens of nanojoules (≈ V_d · I ·
    t_phase), so the raw µJ value reads as ``0.0xx``; pick the unit that
    keeps it in a readable 1-3 digit range.
    """
    if not np.isfinite(uj):
        return ""
    a = abs(uj)
    if a < 1e-3:
        return f"{uj * 1e6:.1f} pJ"
    if a < 1.0:
        return f"{uj * 1e3:.2f} nJ"
    if a < 1e3:
        return f"{uj:.3f} µJ"
    return f"{uj / 1e3:.3f} mJ"


def _pulse_rate_period_rows(pattern) -> list:
    """(label, value) rows for the pulse RATE + PERIOD (operator: "have
    pulse rate and pulse period on the metric measurements").

    Rate is shown in **pps** (not Hz — see the ``pulse-rate-label-pps``
    memory); period = 1 / rate, auto-scaled ms / µs / s so a wide rate
    range stays readable.  (The continuous-sinusoidal KHFAC view uses a
    "Pulse frequency [kHz]" row instead and never calls this.)  Returns
    ``[]`` for a non-positive / missing rate."""
    try:
        rate = float(getattr(pattern, "rate_hz", 0.0) or 0.0)
    except Exception:
        rate = 0.0
    if not (rate > 0):
        return []
    period_s = 1.0 / rate
    if period_s < 1e-3:
        per = f"{period_s * 1e6:.1f} µs"
    elif period_s < 1.0:
        per = f"{period_s * 1e3:.3f} ms"
    else:
        per = f"{period_s:.3f} s"
    return [("Pulse rate [pps]", f"{rate:g}"),
            ("Pulse period", per)]


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
        # Word-wrap long cells — the per-phase value lists (e.g. four
        # access voltages) overflow the column and previously had their
        # extra lines clipped (operator: "allow for word wrapping because
        # additional lines are cut off").  Rows are grown to fit the
        # wrapped content after every populate (see show_capture).
        self.setWordWrap(True)

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
        # First row: NUMBER OF PULSES delivered to acquire this capture
        # (= acquisition pulsing duration × pulse rate; operator spec).
        # Falls back to the capture index when the runner didn't record a
        # pulse count (continuous-pulsing SP/CP/LP), so the row still
        # carries an identifier.
        if np.isfinite(m.n_pulses):
            _first_row = (V("N", "pulse"), _group_thousands(m.n_pulses))
        else:
            _first_row = ("Capture #", str(c.index))
        # BAD / degenerate electrode (open / broken / capacitive): the access
        # V/R, E_pol, V_d (railed at compliance), and driving capacitance are
        # meaningless / artefactual — show a FOCUSED view that says the
        # channel is OPEN and reports only the identity, delivered charge,
        # response class, and effective capacitance (operator: "CH03 etc.
        # still tries to show other metrics and not indicate that it is
        # open").  compute_metrics already cleared the per-phase lists; this
        # also drops V_d / C_d / E_ip so nothing misleading remains.
        # CONTINUOUS SINUSOIDAL (KHFAC) — Ghazavi & Cogan 2018.  A sinusoid has
        # no current-step edge, so the pulsed access V/R + E_pol rows don't
        # apply; show the phase-decomposition metrics instead (E_mc / E_ma /
        # E_io / E_off / R_access) alongside the charge + frequency.
        if getattr(m, "polarization_method", "pulsed") == "sinusoidal":
            _sin_rows = [
                _first_row,
                (f"{V('I','stim')} [µA]",
                    f"{c.pattern.excitation_phase.amplitude_ua:.2f}"),
            ]
            # Pulse FREQUENCY (operator: "For continuous sinusoidal, call it
            # pulse frequency") — the continuous sinusoid's rate IS its
            # frequency.  (The "Method" row was removed per operator request.)
            _freq_khz = getattr(m, "ghazavi_freq_khz", float("nan"))
            if not np.isfinite(_freq_khz):
                _r = float(getattr(c.pattern, "rate_hz", 0.0) or 0.0)
                _freq_khz = _r / 1000.0 if _r > 0 else float("nan")
            if np.isfinite(_freq_khz):
                _sin_rows.append(("Pulse frequency [kHz]",
                                  f"{_freq_khz:.3f}"))
            if np.isfinite(m.charge_per_phase_nc):
                _sin_rows.append((f"{V('Q','ph')} [nC]",
                                  f"{m.charge_per_phase_nc:.2f}"))
            if np.isfinite(m.charge_injection_mc_per_cm2):
                _sin_rows.append((f"{V('Q','inj')} [mC/cm<sup>2</sup>]",
                                  f"{m.charge_injection_mc_per_cm2:.3f}"))
            # Signed potentials (E_mc / E_ma / E_io / E_off), V_access, R_access
            # — for the ACTIVE electrode, then the RETURN electrode when E_ret
            # is recorded (operator: "report the metrics for the active and
            # return" like the pulsed tests).  Active rows get an " active"
            # qualifier only when a return set is also shown.
            _has_ret_g = np.isfinite(getattr(m, "ghazavi_return_e_mc_v",
                                             float("nan")))
            _asfx = " active" if _has_ret_g else ""
            for _lbl, _val in (
                (f"{V('E','mc')}{_asfx} [V]", getattr(m, "ghazavi_e_mc_v", float("nan"))),
                (f"{V('E','ma')}{_asfx} [V]", getattr(m, "ghazavi_e_ma_v", float("nan"))),
                (f"{V('E','io')}{_asfx} [V]", getattr(m, "ghazavi_e_io_v", float("nan"))),
                (f"{V('E','off')}{_asfx} [V]", getattr(m, "ghazavi_e_off_v", float("nan"))),
                (f"{V('V','access')}{_asfx} [V]", getattr(m, "ghazavi_v_access_v", float("nan"))),
            ):
                if np.isfinite(_val):
                    _sin_rows.append((_lbl, f"{_val:+.3f}"))
            _ra = getattr(m, "ghazavi_r_access_kohm", float("nan"))
            if np.isfinite(_ra):
                _sin_rows.append((f"{V('R','access')}{_asfx} [kΩ]", f"{_ra:.3f}"))
            if _has_ret_g:
                for _lbl, _val in (
                    (f"{V('E','mc')} return [V]", getattr(m, "ghazavi_return_e_mc_v", float("nan"))),
                    (f"{V('E','ma')} return [V]", getattr(m, "ghazavi_return_e_ma_v", float("nan"))),
                    (f"{V('E','io')} return [V]", getattr(m, "ghazavi_return_e_io_v", float("nan"))),
                    (f"{V('E','off')} return [V]", getattr(m, "ghazavi_return_e_off_v", float("nan"))),
                    (f"{V('V','access')} return [V]", getattr(m, "ghazavi_return_v_access_v", float("nan"))),
                ):
                    if np.isfinite(_val):
                        _sin_rows.append((_lbl, f"{_val:+.3f}"))
                _rr = getattr(m, "ghazavi_return_r_access_kohm", float("nan"))
                if np.isfinite(_rr):
                    _sin_rows.append((f"{V('R','access')} return [kΩ]", f"{_rr:.3f}"))
            # PHASE ANGLE of each voltage waveform vs I_mon (operator: "phase
            # angle difference … (Vmon, Eret, Eact) versus Imon for continuous
            # sinusoidal").  Impedance/EIS phase: ~0° resistive, →−90°
            # capacitive.  I_mon is the phase REFERENCE, so ∠(I/I) = 0° by
            # definition — shown as the anchor row (operator: "the phase angle
            # for Imon should be 0 degrees") whenever a valid measurement
            # exists (finite V_mon phase).  Only recorded traces show a value.
            _phi_vmon = getattr(m, "phase_angle_vmon_deg", float("nan"))
            if np.isfinite(_phi_vmon):
                _sin_rows.append((f"{V('φ','Imon')} [°]", "+0.0"))
            for _lbl, _val in (
                (f"{V('φ','Vmon')} [°]", _phi_vmon),
                (f"{V('φ','Eret')} [°]", getattr(m, "phase_angle_eret_deg",
                                                  float("nan"))),
                (f"{V('φ','Eact')} [°]", getattr(m, "phase_angle_eact_deg",
                                                  float("nan"))),
            ):
                if np.isfinite(_val):
                    _sin_rows.append((_lbl, f"{_val:+.1f}"))
            _sin_rows.append(("Limit reached?",
                              "yes" if c.status.reached_potential_limit else "no"))
            _sin_rows.append(("Compliance exceeded?",
                              "yes" if c.status.voltage_compliance else "no"))
            self.setRowCount(len(_sin_rows))
            for _i, (_k, _v) in enumerate(_sin_rows):
                self.setItem(_i, 0, QtWidgets.QTableWidgetItem(_k))
                self.setItem(_i, 1, QtWidgets.QTableWidgetItem(_v))
            self.resizeRowsToContents()
            return
        _rclass = getattr(m, "response_class", "normal") or "normal"
        if _rclass != "normal":
            _bad_rows = [
                _first_row,
                (f"{V('I','stim')} [µA]",
                    f"{c.pattern.excitation_phase.amplitude_ua:.2f}"),
                ("Response", _rclass.upper()),
            ]
            # (Pulse rate/period intentionally OMITTED here — the bad-response
            # view stays a FOCUSED minimal diagnostic; they show on the normal
            # + sinusoidal views.)
            if np.isfinite(m.charge_per_phase_nc):
                _bad_rows.append((f"{V('Q','ph')} [nC]",
                                  f"{m.charge_per_phase_nc:.2f}"))
            # broken → parallel R‖C fit (R, C, τ); open / capacitive → pure C.
            _rk = getattr(m, "rc_fit_resistance_kohm", float("nan"))
            _tau = getattr(m, "rc_fit_tau_us", float("nan"))
            if np.isfinite(_rk):
                _bad_rows.append((f"{V('R','')} (R∥C) [kΩ]", f"{_rk:.0f}"))
            if np.isfinite(m.effective_capacitance_nf):
                # broken → R‖C FIT capacitance, shown as a bare ``C`` (operator:
                # "for broken, do not call it Ceff"); open / capacitive → the
                # pure-capacitance effective capacitance ``C_eff``.
                _c_lbl = V('C', '') if _rclass == "broken" else V('C', 'eff')
                _bad_rows.append((f"{_c_lbl} [nF]",
                                  f"{m.effective_capacitance_nf:.3g}"))
            if np.isfinite(_tau):
                _bad_rows.append(("τ (R∥C) [µs]", f"{_tau:.0f}"))
            # PER-PHASE values for the OTHER phases (operator: "for broken
            # and open channels, compute the same metrics for other
            # phases") — the scalars above ARE phase 1, so rows start at
            # phase 2.  Only finite entries are shown.
            _c_list = list(getattr(m, "effective_capacitance_per_phase_nf",
                                   []) or [])
            _r_list = list(getattr(m, "rc_fit_resistance_per_phase_kohm",
                                   []) or [])
            _t_list = list(getattr(m, "rc_fit_tau_per_phase_us", []) or [])
            _c_lbl2 = V('C', '') if _rclass == "broken" else V('C', 'eff')
            for _k2 in range(1, max(len(_c_list), len(_r_list),
                                    len(_t_list))):
                _rk2 = _r_list[_k2] if _k2 < len(_r_list) else float("nan")
                _ck2 = _c_list[_k2] if _k2 < len(_c_list) else float("nan")
                _tk2 = _t_list[_k2] if _k2 < len(_t_list) else float("nan")
                if np.isfinite(_rk2):
                    _bad_rows.append(
                        (f"{V('R','')} (R∥C) ph{_k2 + 1} [kΩ]",
                         f"{_rk2:.0f}"))
                if np.isfinite(_ck2):
                    _bad_rows.append(
                        (f"{_c_lbl2} ph{_k2 + 1} [nF]", f"{_ck2:.3g}"))
                if np.isfinite(_tk2):
                    _bad_rows.append(
                        (f"τ (R∥C) ph{_k2 + 1} [µs]", f"{_tk2:.0f}"))
            _bad_rows.append(("Compliance exceeded?",
                              "yes" if c.status.voltage_compliance else "no"))
            self.setRowCount(len(_bad_rows))
            for _i, (_k, _v) in enumerate(_bad_rows):
                self.setItem(_i, 0, QtWidgets.QTableWidgetItem(_k))
                self.setItem(_i, 1, QtWidgets.QTableWidgetItem(_v))
            self.resizeRowsToContents()
            return
        rows = [
            _first_row,
            (f"{V('I','stim')} [µA]",
                f"{c.pattern.excitation_phase.amplitude_ua:.2f}"),
            (f"{V('Q','ph')} [nC]", f"{m.charge_per_phase_nc:.2f}"),
            (f"{V('Q','inj')} [mC/cm<sup>2</sup>]",
                f"{m.charge_injection_mc_per_cm2:.3f}"),
        ]
        rows += _pulse_rate_period_rows(c.pattern)
        # Cumulative pulse count + cumulative cathodic charge delivered to the
        # electrode up to AND including this capture, building on the run's
        # earlier captures (operator: "add cumulative N_pulse to the metric
        # table above cumulative Q [formerly cumulative charge], which is in
        # nC").  Cumulative N_pulse is comma-grouped; cumulative Q is
        # auto-scaled nC / µC / mC.
        if np.isfinite(m.cumulative_n_pulses):
            rows.append(("Cumulative " + V("N", "pulse"),
                         _group_thousands(m.cumulative_n_pulses)))
        if np.isfinite(m.cumulative_charge_nc):
            rows.append(("Cumulative Q",
                         _fmt_cumulative_charge(m.cumulative_charge_nc)))
        rows += [
            (f"{V('E','ip')} [V]", f"{m.interpulse_potential_v:.3f}"),
            (f"{V('C','d')} [mF/cm<sup>2</sup>]",
                f"{m.driving_capacitance_mf_per_cm2:.3f}"),
        ]
        # Driving impedance Z_d = V_d / I_stim and driving energy
        # (∫V·I over the pulse) — operator request.  Shown when finite.
        if np.isfinite(m.driving_impedance_kohm):
            rows.append((f"{V('Z','d')} [kΩ]",
                         f"{m.driving_impedance_kohm:.3f}"))
        if np.isfinite(m.driving_energy_uj):
            rows.append(("Driving energy", _fmt_energy(m.driving_energy_uj)))
        # Harris 2019 chronopotentiometry capacitive/Faradaic decomposition
        # (normal captures only): C_dl (double-layer capacitance from the
        # constant-dE/dt window) + the APPROXIMATE Faradaic split.  Finite only
        # for a functional electrode on a rectangular pulse with a known area.
        if np.isfinite(m.c_dl_mf_per_cm2):
            rows.append((f"{V('C','dl')} [mF/cm<sup>2</sup>]",
                         f"{m.c_dl_mf_per_cm2:.3f}"))
        if np.isfinite(m.faradaic_fraction):
            rows.append(("Faradaic charge fraction",
                         f"{m.faradaic_fraction * 100:.0f}%"))
        if np.isfinite(m.faradaic_onset_us):
            _fo = f"{m.faradaic_onset_us:.0f} µs"
            if np.isfinite(m.faradaic_onset_v):
                _fo += f"  ({m.faradaic_onset_v:+.3f} V)"
            rows.append(("Faradaic onset", _fo))
        # Effective capacitance C_eff = I/(dV/dt) — shown ONLY for an
        # entirely-capacitive / open / broken response (operator: "show
        # capacitance since it is so linear … only when … entirely
        # capacitive, open circuit, or broken").  For those captures the
        # access V/R + E_pol lists are empty (suppressed in compute_metrics),
        # so their rows below auto-hide; the response class is appended so
        # the operator can see WHY (open vs capacitive).
        if np.isfinite(m.effective_capacitance_nf):
            _cls = getattr(m, "response_class", "") or ""
            _suffix = f"  ({_cls})" if _cls and _cls != "normal" else ""
            rows.append((f"{V('C','eff')} [nF]",
                         f"{m.effective_capacitance_nf:.3f}{_suffix}"))
        # The "active"/"return" qualifiers only mean something when a
        # SEPARATE E_act/E_ret (instrumentation amp) was recorded.  Without
        # it, V_mon IS the active-vs-return voltage, so drop the qualifier
        # and show a SINGLE V_d (= the V_mon driving voltage, matching the
        # plot's V_d marker) instead of a per-phase "V_d active" (operator:
        # "There should not be any 'Vd active' because Vd is active versus
        # return, so the Vmon gives Vd").
        _has_return = bool(m.return_polarization_per_phase_v
                           or m.return_driving_voltage_per_phase_v
                           or m.return_access_voltage_per_phase_v
                           or getattr(m, "return_shaped_access_v_per_phase", None))
        _act = " active" if _has_return else ""
        if _has_return:
            _driving_rows = (
                (f"{V('V','d')} active [V]", m.active_driving_voltage_per_phase_v),
                (f"{V('V','d')} return [V]", m.return_driving_voltage_per_phase_v),
            )
        else:
            _driving_rows = ()
            # PER-PHASE driving voltage (operator: "Add the driving voltage of
            # each phase to the metrics table") — matches the plot's V_d / V_d2
            # markers.  No "active" qualifier without a return electrode (V_mon
            # IS the active-vs-return voltage).  Falls back to the single total
            # driving voltage when the per-phase list is unavailable.
            if m.active_driving_voltage_per_phase_v:
                rows.append((f"{V('V','d')} [V]",
                             ", ".join(f"{x:.3f}" for x in
                                       m.active_driving_voltage_per_phase_v)))
            elif np.isfinite(m.driving_voltage_v):
                rows.append((f"{V('V','d')} [V]", f"{m.driving_voltage_v:.3f}"))
        for k, vlist in (
            *_driving_rows,
            (f"{V('V','a')}{_act} [V]", m.access_voltage_per_phase_v),
            (f"{V('R','a')}{_act} [kΩ]", m.access_resistance_per_phase_kohm),
            (f"{V('V','a')} return [V]", m.return_access_voltage_per_phase_v),
            (f"{V('R','a')} return [kΩ]", m.return_access_resistance_per_phase_kohm),
            (f"{V('E','pol')}{_act} [V]", m.polarization_per_phase_v),
            (f"{V('E','pol')} return [V]", m.return_polarization_per_phase_v),
        ):
            if vlist:
                rows.append((k, ", ".join(f"{x:.3f}" for x in vlist)))
        # PEAK-CURRENT access for SMOOTH shaped phases (gaussian / sinusoidal
        # with no edge) — a SEPARATE measurement, labelled "(peak I)" so it's
        # distinct from the edge-based V_a above (operator: "change the
        # discontinuous gaussian and sinusoidal to peak current").  NaN entries
        # (non-qualifying phases) are filtered out.
        for _slbl, _svl in (
            (f"{V('V','a')} (peak I){_act} [V]",
             getattr(m, "shaped_access_v_per_phase", None)),
            (f"{V('R','a')} (peak I){_act} [kΩ]",
             getattr(m, "shaped_access_r_kohm_per_phase", None)),
            (f"{V('V','a')} (peak I) return [V]",
             getattr(m, "return_shaped_access_v_per_phase", None)),
            (f"{V('R','a')} (peak I) return [kΩ]",
             getattr(m, "return_shaped_access_r_kohm_per_phase", None)),
        ):
            _fin = [x for x in (_svl or []) if np.isfinite(x)]
            if _fin:
                rows.append((_slbl, ", ".join(f"{x:.3f}" for x in _fin)))
        rows.append(("Limit reached?", "yes" if c.status.reached_potential_limit else "no"))
        # Distinct from "reached" — E_pol overshot PAST the acceptance band
        # (operator: differentiate a clean in-band stop from an overshoot).
        rows.append(("Limit exceeded?",
                     "yes" if getattr(c.status, "exceeded_potential_limit", False)
                     else "no"))
        rows.append(("Compliance exceeded?", "yes" if c.status.voltage_compliance else "no"))
        self.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.setItem(i, 0, QtWidgets.QTableWidgetItem(k))
            self.setItem(i, 1, QtWidgets.QTableWidgetItem(v))
        # Grow each row to fit its (now word-wrapped) content so the
        # multi-value per-phase cells show every line.
        self.resizeRowsToContents()


# ---------------------------------------------------------------------------
# Status bar / log widget
# ---------------------------------------------------------------------------
class LogPane(QtWidgets.QTextEdit):
    """Read-only message log shown at the bottom of the GUI.

    Every line written via :meth:`log` is shown in the pane AND
    appended to a configurable ``log.txt`` on disk so the user has a
    permanent record of what happened during a session — useful for
    debugging "why did this run abort?" the next morning.
    ``set_log_file(path)`` repoints the file when the save directory
    changes; passing ``None`` disables disk logging. The pane keeps
    only the last 1000 lines in memory but the file on disk is
    unbounded.

    **Base class**: a full ``QTextEdit`` (not ``QPlainTextEdit``) so
    the per-block HANGING INDENT actually renders — ``QPlainTextEdit``'s
    lightweight ``QPlainTextDocumentLayout`` silently ignores block
    ``leftMargin`` / ``textIndent`` (it stores them but never applies
    them at paint time), so a wrapped line falls back to the left
    margin.  ``QTextEdit``'s full ``QTextDocumentLayout`` honours them,
    giving the operator-requested hanging indent.  ``QTextEdit`` has no
    ``setMaximumBlockCount``, so the 1000-line FIFO cap is enforced
    manually in ``_raw_append`` (``_max_blocks``); the point-size /
    monospace / autoscroll / disk-mirror behaviour is otherwise
    identical.

    **Time format** — every line is prefixed with the WALL-CLOCK date +
    time of day AND the run-relative elapsed since the most recent
    :meth:`reset_clock` call:
    ``[YYYY-MM-DD HH:MM:SS +H:MM:SS] message`` (operator: "include the
    date and time" — the elapsed alone can't tell you WHEN a line
    happened).  The elapsed ``+H:MM:SS`` mirrors the MATLAB ``tic`` /
    ``getEndTime.m`` convention; the wall-clock date/time lets the log
    correlate with other sources (scope timestamps, the system journal).
    Pair with :meth:`tic` / :meth:`toc` for explicit timed sub-operations
    — the auto-scaled unit selector (``us`` / ``ms`` / ``s`` / ``min`` /
    ``h``) matches the MATLAB ``getEndTime.m`` thresholds exactly.
    """

    #: In-memory FIFO cap (lines kept in the pane; the on-disk log is
    #: unbounded).  ``QTextEdit`` has no ``setMaximumBlockCount``, so
    #: this is enforced by trimming leading blocks in ``_raw_append``.
    _max_blocks = 1000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        # We build every block programmatically (plain text + our own
        # block format); refuse rich-text paste so nothing injects markup,
        # and skip the undo stack (append-only read-only log → wasted RAM).
        self.setAcceptRichText(False)
        self.setUndoRedoEnabled(False)
        self.setLineWrapMode(QtWidgets.QTextEdit.LineWrapMode.WidgetWidth)
        # Monospace "programming/console" font for the log pane (operator
        # preference).  Ordered family fallback so it degrades gracefully
        # on any machine: Cascadia Mono (ships with Windows 11 / Terminal)
        # → Consolas (every Windows) → Courier New → the Qt Monospace
        # style-hint default.  The point size is preserved from the
        # inherited app font so the console doesn't jump larger/smaller.
        _log_font = QtGui.QFont()
        _log_font.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
        _log_font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        _log_font.setFixedPitch(True)
        _inherited_pt = self.font().pointSize()
        if _inherited_pt > 0:
            _log_font.setPointSize(_inherited_pt)
        self.setFont(_log_font)
        # Hanging indent (operator request): when a line is longer than the
        # pane width, its wrapped continuation lines align UNDER the message
        # (past the "[H:MM:SS] " timestamp) instead of falling back to the
        # left margin, so a wrapped entry reads as one visually-grouped block.
        # Applied per-block in `_raw_append` (the 1000-block FIFO cap means
        # each fresh block starts with the default format).  Width computed
        # from a representative single-digit-hour timestamp prefix in the
        # monospace font.  QPlainTextEdit has no document-wide default block
        # format, so per-append is the standard approach.
        self._hang_indent_px = 0.0
        self._recompute_hang_indent()
        # "Go to latest line" OVERLAY button (operator request) — a small
        # floating button in the pane's bottom-right corner, shown ONLY
        # while the view is parked OFF the bottom (same enable rule as the
        # scope view's ⤓ Latest button: there must be somewhere to jump
        # to).  Clicking scrolls to the newest line, which also re-arms the
        # tail-follow (follow keys on "scrollbar at bottom").  An overlay
        # child (not a layout row) so it costs no vertical space and every
        # embedding site gets it for free.
        self._latest_btn = QtWidgets.QToolButton(self)
        self._latest_btn.setText("Go to latest line")
        self._latest_btn.setToolTip(
            "Jump to the newest log line and resume auto-scrolling.")
        self._latest_btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._latest_btn.setAutoRaise(False)
        self._latest_btn.clicked.connect(self.go_to_latest_line)
        self._latest_btn.setVisible(False)
        self.verticalScrollBar().valueChanged.connect(
            self._refresh_latest_btn)
        self.verticalScrollBar().rangeChanged.connect(
            lambda *_: self._refresh_latest_btn())
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
        # True when the file :meth:`set_log_file` last opened ALREADY had
        # content (a prior session's log with the same dir + filename) — the
        # run-start overwrite confirmation reads this to warn before a prior
        # log is appended-to / replaced (operator: "even when writing to the
        # log file should ask if the user wants to replace it if the directory
        # and filename are the same").
        self._log_file_preexisted = False
        # Resolved log paths THIS session has already opened.  A path we've
        # written to this session is OURS — re-pointing at it (e.g. the user
        # edits the session filename, or a signal re-fires ``set_log_file``)
        # must NOT re-flag it as "pre-existing" just because THIS session grew
        # it (operator: "I was asked to replace a file … when I have not run
        # anything in that directory" — the victim was this session's own log).
        self._session_log_paths = set()  # type: set
        # Resolved log paths that had PRIOR content the FIRST time THIS session
        # touched them — i.e. a genuine previous session's log with the same
        # dir + filename.  STICKY: recorded once on first touch and never reset
        # by a later re-point, so the run-start overwrite prompt keeps flagging
        # a real prior log (operator: ask replace/append when dir+name match)
        # while NEVER flagging the session's OWN freshly-created log (it had no
        # content on first touch).  A ``replace`` (truncate) clears the entry.
        self._preexisting_log_paths = set()  # type: set
        # Resolved log paths we've already stamped a "created / reopened"
        # banner into this session (so the header fires once per file).
        self._log_created_paths = set()  # type: set
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

    def set_log_file(self, path, *, replace: bool = False) -> None:
        """Direct subsequent log writes to ``path`` (or ``None`` to
        disable disk logging).

        By default (``replace=False``) existing content of the file is left
        alone — logs from past sessions in the same folder are APPENDED to.
        Pass ``replace=True`` to TRUNCATE the file (start a fresh log) — used
        when the operator confirms replacing a prior session's log at run
        start (see :meth:`replace_log_file`).

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
            self._log_file_preexisted = False
            return
        p = Path(path)
        try:
            _key = str(p.resolve())
        except Exception:
            _key = str(p)
        # Record whether the target already has content BEFORE we open it —
        # a plain append open would silently accrete onto a prior session's
        # log with the same name.  A ``replace`` (truncate) makes it fresh, so
        # it no longer counts as pre-existing.  STICKY: once THIS session has
        # opened a path, it's OURS — a later re-point at the same path (a
        # filename-edit signal, a re-fired ``set_log_file``) never re-flags it
        # as pre-existing just because we grew it this session.
        _owned = _key in self._session_log_paths
        # A ``replace`` truncates the file to empty, so it should get a FRESH
        # "created" banner even if we banner'd this path earlier this session,
        # AND it is no longer a pre-existing overwrite victim.
        if replace:
            self._log_created_paths.discard(_key)
            self._preexisting_log_paths.discard(_key)
        try:
            _existed_with_content = bool(p.exists() and p.stat().st_size > 0)
            # STICKY per-path pre-existing flag.  On the FIRST touch this
            # session (``not _owned``), if the file already had PRIOR content,
            # record it as a genuine prior-session victim — ONCE.  Later
            # re-points (``_owned``) leave that record intact, so the flag no
            # longer flip-flops to False just because THIS session opened the
            # file for appending during setup (the bug: a real prior log was
            # un-flagged before the Start prompt ran).  The session's OWN log,
            # created fresh, has no content on first touch → never recorded →
            # never flagged (preserves the fix in the ctor comment above).
            if (not _owned) and (not replace) and _existed_with_content:
                self._preexisting_log_paths.add(_key)
            self._log_file_preexisted = (
                (not replace) and (_key in self._preexisting_log_paths))
        except Exception:
            self._log_file_preexisted = False
            _existed_with_content = False
        self._session_log_paths.add(_key)
        # DO NOT silently create the save directory here.  This is called on
        # every save-path / filename change (before run start), and a silent
        # ``mkdir`` would materialise a MISTYPED / non-existent save folder —
        # defeating the run-start "the save folder does not exist" confirmation
        # (operator: "I wanted a pop up about a non-existent directory").  When
        # the parent is missing, HOLD the path but defer opening; once the
        # folder actually exists (the operator confirms Create → the runner
        # mkdirs it), the per-line ``_raw_append`` fallback starts writing.
        self._log_file_path = p
        if not p.parent.exists():
            self._log_file_handle = None
            return
        try:
            # buffering=1 → line-buffered text mode.  Each newline
            # triggers a flush, so a tail-er sees lines arrive in
            # real time without us paying the open-close cost of
            # the per-write approach.  ``"w"`` truncates on replace.
            self._log_file_handle = p.open(
                "w" if replace else "a", encoding="utf-8", buffering=1)
        except Exception:
            # Falls back to None → ``_raw_append`` re-opens per
            # line as before if the handle can't be obtained.
            self._log_file_handle = None
        # Stamp a wall-clock CREATION banner into the file the first time we
        # open it this session (operator: "be sure that the log file has the
        # date and time of creation").  "created" for a fresh / truncated /
        # brand-new file; "reopened" when appending to a prior session's log.
        if (self._log_file_handle is not None
                and _key not in self._log_created_paths):
            self._log_created_paths.add(_key)
            _stamp = _wall_stamp()
            _verb = ("reopened (appending)"
                     if (_existed_with_content and not replace) else "created")
            self._raw_append(f"===== PULSAR session log {_verb} {_stamp} =====")

    def log_file_path(self):
        """The current on-disk log path (``None`` when disk logging is off)."""
        return self._log_file_path

    def log_file_preexisted(self) -> bool:
        """True when the current log file already had content when it was
        opened (a prior session's log with the same dir + filename)."""
        return bool(self._log_file_preexisted)

    def replace_log_file(self) -> None:
        """TRUNCATE the current log file (start it fresh) — called when the
        operator confirms replacing a prior session's log at run start."""
        if self._log_file_path is not None:
            self.set_log_file(self._log_file_path, replace=True)

    def mark_session_ended(self, note: str = "") -> None:
        """Stamp a wall-clock END banner into the log (operator: "be sure that
        the log file has the date and time of … ended").  Called when a run
        completes / the experiment finishes."""
        stamp = _wall_stamp()
        extra = f" — {note}" if note else ""
        self._raw_append(f"===== Session ended {stamp}{extra} =====")

    def mark_session_continued(self, reason: str = "") -> None:
        """Stamp a wall-clock CONTINUE banner into the log (operator: "when
        continuing with LP, add another date and time for continuing")."""
        stamp = _wall_stamp()
        tag = f" ({reason})" if reason else ""
        self._raw_append(f"===== Continuing{tag} {stamp} =====")

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
            stamp = _wall_stamp(self._session_start_wall)
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
        line = f"[{self._line_timestamp(elapsed)}] {msg}"
        self._raw_append(line)

    def _line_timestamp(self, elapsed_s: float) -> str:
        """The per-line timestamp: the WALL-CLOCK date + time of day, then the
        run-relative elapsed (operator: "include the date and time" — the
        elapsed alone can't tell you WHEN a line happened).  Format
        ``YYYY-MM-DD HH:MM:SS +H:MM:SS`` (the ``+`` marks the elapsed since the
        last :meth:`reset_clock`).  Used for BOTH the on-screen pane and the
        line-buffered on-disk .txt session log (same ``line`` feeds
        ``_raw_append``)."""
        wall = _wall_stamp()          # includes the local time zone
        return f"{wall} +{self._format_elapsed_hms(elapsed_s)}"

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

    def _recompute_hang_indent(self) -> None:
        """(Re)compute the hanging-indent width from the current font.

        The indent equals the pixel width of a representative timestamp
        prefix (``"[YYYY-MM-DD HH:MM:SS TZ +H:MM:SS] "`` — wall-clock date/time,
        local TIME ZONE, then the run-relative elapsed) so a wrapped
        continuation line lands right under the message text.  Called at
        construction and whenever the font / DPI changes.  Keep this sample in
        lock-step with the prefix built in :meth:`_line_timestamp` — the ``TZ``
        placeholder uses the real local abbreviation so its width matches."""
        try:
            self._hang_indent_px = float(
                self.fontMetrics().horizontalAdvance(
                    f"[0000-00-00 00:00:00 {_tz_abbrev()} +0:00:00] "))
        except Exception:
            self._hang_indent_px = 0.0

    def _apply_hang_indent(self, from_pos: int) -> None:
        """Give every block appended since ``from_pos`` a hanging indent.

        ``leftMargin = indent`` shifts the WHOLE block right; the
        matching ``textIndent = -indent`` pulls the FIRST visual line
        back to the margin — so line 1 starts at the timestamp and every
        wrapped line aligns under the message.  A single ``log()`` may
        add SEVERAL blocks at once (the tabbed multi-line pattern
        summary has one block per ``\\n``), so we select the whole
        just-added span and merge the format over every block it touches
        — ``mergeBlockFormat`` on a selection applies to all spanned
        blocks.  Uses a private document cursor so the widget's own text
        cursor / selection is untouched.  Best-effort — a formatting
        hiccup must never drop a log line.
        """
        if self._hang_indent_px <= 0:
            return
        try:
            cur = QtGui.QTextCursor(self.document())
            cur.setPosition(max(0, from_pos))
            cur.movePosition(QtGui.QTextCursor.MoveOperation.End,
                             QtGui.QTextCursor.MoveMode.KeepAnchor)
            bf = QtGui.QTextBlockFormat()
            bf.setLeftMargin(self._hang_indent_px)
            bf.setTextIndent(-self._hang_indent_px)
            cur.mergeBlockFormat(bf)
        except Exception:
            pass

    def _append_plain_blocks(self, line: str) -> int:
        """Append ``line`` as one paragraph per embedded ``\\n``.

        Returns the document position of the end BEFORE the insert, so
        the caller can hang-indent exactly the blocks it just added.
        A ``QTextEdit`` (unlike ``QPlainTextEdit.appendPlainText``) has
        no plain append, and ``QTextCursor.insertText`` treats ``\\n``
        inconsistently across Qt versions — so we split on ``\\n`` and
        insert explicit blocks.  The insert is grouped so it relayouts
        once.
        """
        doc = self.document()
        was_empty = doc.characterCount() <= 1  # only the implicit paragraph
        cur = QtGui.QTextCursor(doc)
        cur.movePosition(QtGui.QTextCursor.MoveOperation.End)
        start_pos = cur.position()
        cur.beginEditBlock()
        try:
            for i, part in enumerate(line.split("\n")):
                # Block 0 already exists in a fresh document, so the first
                # segment of the first-ever append reuses it; every other
                # segment starts a new paragraph.
                if i > 0 or not was_empty:
                    cur.insertBlock()
                if part:
                    cur.insertText(part)
        finally:
            cur.endEditBlock()
        return start_pos

    def _trim_to_cap(self) -> None:
        """Enforce the in-memory FIFO cap by removing leading blocks.

        Replaces ``QPlainTextEdit.setMaximumBlockCount`` (which
        ``QTextEdit`` lacks).  Removes whole blocks via ``NextBlock``
        moves (block-wise, so word wrap doesn't throw the count off).
        Best-effort — a trim failure must never drop the current line.
        """
        try:
            doc = self.document()
            over = doc.blockCount() - int(self._max_blocks)
            if over <= 0:
                return
            cur = QtGui.QTextCursor(doc)
            cur.movePosition(QtGui.QTextCursor.MoveOperation.Start)
            cur.movePosition(QtGui.QTextCursor.MoveOperation.NextBlock,
                             QtGui.QTextCursor.MoveMode.KeepAnchor, over)
            cur.removeSelectedText()
        except Exception:
            pass

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        """Recompute the hanging indent when the font changes."""
        try:
            if event is not None and event.type() == QtCore.QEvent.Type.FontChange:
                self._recompute_hang_indent()
        except Exception:
            pass
        super().changeEvent(event)

    # ----- "Go to latest line" overlay -------------------------------
    def go_to_latest_line(self) -> None:
        """Jump to the newest log line and resume auto-scrolling.

        Scrolling to the bottom IS the re-arm — the tail-follow in
        ``_raw_append`` keys on "scrollbar at (or within 4 px of) the
        bottom", so after this the pane snaps to each new line again.
        """
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._refresh_latest_btn()

    def _refresh_latest_btn(self, *_) -> None:
        """Show the overlay only while parked OFF the bottom (there's
        somewhere to jump to); hide it when following the tail."""
        btn = getattr(self, "_latest_btn", None)
        if btn is None:
            return
        try:
            sb = self.verticalScrollBar()
            off_bottom = (sb.maximum() > 0
                          and sb.value() < sb.maximum() - 4)
            btn.setVisible(off_bottom)
            if off_bottom:
                self._position_latest_btn()
        except Exception:
            pass

    def _position_latest_btn(self) -> None:
        """Park the overlay in the pane's bottom-right corner, clear of
        the vertical scrollbar."""
        btn = getattr(self, "_latest_btn", None)
        if btn is None:
            return
        try:
            hint = btn.sizeHint()
            sb_w = (self.verticalScrollBar().width()
                    if self.verticalScrollBar().isVisible() else 0)
            x = max(0, self.width() - hint.width() - sb_w - 10)
            y = max(0, self.height() - hint.height() - 10)
            btn.setGeometry(x, y, hint.width(), hint.height())
            btn.raise_()
        except Exception:
            pass

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        super().resizeEvent(event)
        self._position_latest_btn()

    def _raw_append(self, line: str) -> None:
        """Common path for both ``log()`` and the start banner —
        append to the pane and mirror to disk. Best-effort; disk
        errors are silently swallowed because the pane is the
        source of truth, the file is just a convenience copy.
        Uses the long-lived line-buffered handle from
        :meth:`set_log_file` when available; falls back to per-write
        open() only if the handle can't be obtained.
        """
        # Stick-to-bottom / tail-follow (operator: "automatically scrolled
        # down to the latest line … but when the user scrolls up, don't yank
        # them back; resume following once the scrollbar reaches the bottom
        # again").  Decide BEFORE the append whether the view is parked at the
        # bottom (within a few px, to tolerate line-rounding); an empty/short
        # log has maximum()==0 → treated as at-bottom so the first lines
        # follow.  A cursor-based plain-text append preserves the scroll
        # position, so we force it to the new bottom only when following, and
        # otherwise restore the user's parked position (clamped in case the
        # 1000-line cap evicted a top line and shrank the range).
        _sb = self.verticalScrollBar()
        _prev = _sb.value()
        _follow = _prev >= _sb.maximum() - 4
        # Position of the current end BEFORE the append, so the hanging
        # indent covers every block this append adds (a multi-line pattern
        # summary adds several at once).
        _end_before = self._append_plain_blocks(line)
        self._apply_hang_indent(_end_before)
        self._trim_to_cap()
        _sb.setValue(_sb.maximum() if _follow else min(_prev, _sb.maximum()))
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
