"""Spinbox subclasses that auto-repeat their step on press-and-hold.

QAbstractSpinBox already auto-repeats when the user clicks-and-holds the
embedded up/down arrow buttons, but the timing is governed by the
platform style (QStyle::SH_SpinBox_ClickAutoRepeatThreshold and
SH_SpinBox_ClickAutoRepeatRate) which produces noticeably different
behaviour across Windows / Linux / macOS — and on some styles the
threshold is high enough that "holding" feels unresponsive.

These subclasses replace that timing with a deterministic 400 ms
initial delay and 60 ms repeat interval so the user gets consistent,
crisp hold-to-step behaviour everywhere. Use them anywhere a regular
QSpinBox / QDoubleSpinBox would be used.
"""
from __future__ import annotations

import re

from PyQt6 import QtCore, QtGui, QtWidgets

# Matches an *incomplete* scientific-notation literal — the part after
# the mantissa has started but the exponent isn't yet a valid integer:
#   "1e"   "1e-"   "1e+"   "-2.5E"   "+.3E-"
# These would cause float() to raise ValueError, but they are legitimate
# intermediate keystrokes on the way to a valid number like "1e-3".
_SCI_INCOMPLETE_RE = re.compile(
    r'^[+-]?(\d+\.?\d*|\.\d+)[eE][+-]?$'
)


_INITIAL_DELAY_MS = 400      # ms between press and the first auto-repeat tick
_REPEAT_INTERVAL_MS = 60     # ms between subsequent ticks while still held

#: Group separator inserted every 3 digits in the spinbox display. We
#: use the **narrow no-break space** (U+202F) instead of a regular
#: space because it doesn't word-wrap and renders as a tighter gap —
#: SI / ISO 31-0 conventions for grouping digits, and matches the
#: typographic standard used in scientific publishing.
_GROUP_SEP = " "


def _format_with_thousands(value: float, decimals: int) -> str:
    """Format a numeric value with the SI narrow-space thousands
    separator. Decimal point stays as ``.`` (no localisation), so the
    result round-trips through ``float()`` after stripping the
    separator. Negative values keep their sign on the leading digit
    group.
    """
    if value != value:    # NaN
        return "nan"
    sign = "-" if value < 0 else ""
    av = abs(value)
    if decimals > 0:
        s = f"{av:.{decimals}f}"
        int_part, dec_part = s.split(".")
    else:
        int_part = f"{int(round(av))}"
        dec_part = ""
    # Insert the separator every 3 digits from the right.
    grouped = ""
    for i, ch in enumerate(reversed(int_part)):
        if i and i % 3 == 0:
            grouped = _GROUP_SEP + grouped
        grouped = ch + grouped
    return sign + grouped + ("." + dec_part if dec_part else "")


def _step_dir_for_pos(spinbox: QtWidgets.QAbstractSpinBox,
                      pos: QtCore.QPoint) -> int:
    """Return +1 if ``pos`` is over the up-arrow, -1 for the down-arrow,
    0 otherwise.

    Uses :class:`QStyle.subControlRect` so the same hit-test works
    regardless of whether the platform draws the spin buttons
    side-by-side, stacked, or with custom geometry.
    """
    opt = QtWidgets.QStyleOptionSpinBox()
    spinbox.initStyleOption(opt)
    style = spinbox.style()
    cc = QtWidgets.QStyle.ComplexControl.CC_SpinBox
    up = style.subControlRect(cc, opt,
                              QtWidgets.QStyle.SubControl.SC_SpinBoxUp,
                              spinbox)
    if up.contains(pos):
        return +1
    down = style.subControlRect(cc, opt,
                                QtWidgets.QStyle.SubControl.SC_SpinBoxDown,
                                spinbox)
    if down.contains(pos):
        return -1
    return 0


class _HoldRepeatMixin:
    """Shared press-and-hold logic. Used by both spinbox subclasses."""

    def _install_repeat(self):
        self._held_step = 0
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(False)
        self._timer.timeout.connect(self._on_repeat_tick)

    def mousePressEvent(self, ev: QtGui.QMouseEvent):  # type: ignore[override]
        super().mousePressEvent(ev)  # type: ignore[misc]
        if ev.button() != QtCore.Qt.MouseButton.LeftButton:
            return
        d = _step_dir_for_pos(self, ev.pos())
        if d != 0:
            self._held_step = d
            self._timer.start(_INITIAL_DELAY_MS)

    def mouseReleaseEvent(self, ev: QtGui.QMouseEvent):  # type: ignore[override]
        super().mouseReleaseEvent(ev)  # type: ignore[misc]
        self._stop_repeat()

    def leaveEvent(self, ev: QtCore.QEvent):  # type: ignore[override]
        # Stop repeating if the mouse drifts off the spinbox while held —
        # mirrors the behaviour of an OS-level scrollbar arrow.
        super().leaveEvent(ev)  # type: ignore[misc]
        self._stop_repeat()

    def _stop_repeat(self):
        self._timer.stop()
        self._held_step = 0

    def _on_repeat_tick(self):
        if self._held_step > 0:
            self.stepUp()  # type: ignore[attr-defined]
        elif self._held_step < 0:
            self.stepDown()  # type: ignore[attr-defined]
        else:
            self._timer.stop()
            return
        # First tick used the initial delay; switch to the faster
        # repeat interval for subsequent ticks while still held.
        if self._timer.interval() != _REPEAT_INTERVAL_MS:
            self._timer.setInterval(_REPEAT_INTERVAL_MS)


def _strip_affixes(text: str, prefix: str, suffix: str) -> str:
    """Remove the spinbox's suffix and/or prefix from ``text`` if
    they're present at the appropriate end. Used by both
    :meth:`validate` and :meth:`valueFromText` so a partial-edit
    string like ``"5 µA"`` (suffix still attached after the user
    typed over a digits-only selection) parses cleanly.

    Why bother?
        Qt's default ``QAbstractSpinBox.validate`` strips the
        prefix / suffix before passing to subclasses' overrides.
        But because we OVERRIDE ``validate`` for the group-
        separator handling, Qt no longer auto-strips — the suffix
        comes through into our validator. Without this helper,
        typing over a digits-only selection (which is what
        double-click on a spinbox produces) leaves the suffix
        in place, and our regex-style cleaning would treat
        "5µA" as an invalid float and reject the keystroke. The
        user reported having to include the suffix in the
        selection to type at all — that's the symptom.
    """
    out = text
    if suffix and out.endswith(suffix):
        out = out[:-len(suffix)]
    elif suffix:
        # Loose match: Qt sometimes leaves a trailing partial
        # suffix ("µA" without the leading space) when the user
        # mid-edits across the boundary. Strip whatever leading
        # / trailing whitespace + suffix-ish characters remain.
        stripped = out.rstrip()
        if stripped.endswith(suffix.strip()):
            out = stripped[:-len(suffix.strip())]
    if prefix and out.startswith(prefix):
        out = out[len(prefix):]
    return out


class RepeatingDoubleSpinBox(_HoldRepeatMixin, QtWidgets.QDoubleSpinBox):
    """QDoubleSpinBox with deterministic hold-to-step on the +/- buttons.

    Also formats the displayed value with a SI-style narrow-space
    thousands separator (so ``19560`` reads as ``19 560``). The value
    parses back through :meth:`valueFromText` after stripping any
    spaces, so the user can paste numbers with or without separators.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._install_repeat()

    def textFromValue(self, value: float) -> str:
        return _format_with_thousands(value, self.decimals())

    def valueFromText(self, text: str) -> float:
        # Strip suffix / prefix first so a typed-over partial edit
        # ("5 µA" with the suffix still attached) parses cleanly,
        # then strip group separators.
        cleaned = _strip_affixes(text, self.prefix(), self.suffix())
        cleaned = (cleaned.replace(_GROUP_SEP, "")
                          .replace(" ", "")
                          .replace(",", "")
                          .strip())
        if not cleaned:
            return self.value()
        try:
            return float(cleaned)
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):
        # Accept digits, a single decimal point, an optional leading
        # sign, and our group separator characters. Anything else is
        # invalid; partial input (e.g. "1 ") is "intermediate" so the
        # user can keep typing without Qt rejecting the keystroke.
        # Suffix / prefix are stripped first — see ``_strip_affixes``
        # for the rationale (Qt doesn't auto-strip when validate is
        # overridden, so we have to do it ourselves or typing over
        # a digits-only selection produces "5 µA" which our raw
        # validator rejects as not-a-float).
        cleaned = _strip_affixes(text, self.prefix(), self.suffix())
        cleaned = (cleaned.replace(_GROUP_SEP, "")
                          .replace(" ", "")
                          .replace(",", ""))
        # An empty string is "intermediate" (user is mid-edit), not invalid.
        if cleaned in ("", "-", "+", ".", "-.", "+."):
            return (QtGui.QValidator.State.Intermediate, text, pos)
        try:
            float(cleaned)
        except ValueError:
            # Incomplete scientific notation ("1e", "1e-", "2.5E+") is a
            # legitimate mid-keystroke state — mark Intermediate so Qt
            # doesn't reject the 'e'/'E' character as the user types.
            if _SCI_INCOMPLETE_RE.match(cleaned):
                return (QtGui.QValidator.State.Intermediate, text, pos)
            return (QtGui.QValidator.State.Invalid, text, pos)
        return (QtGui.QValidator.State.Acceptable, text, pos)


class RepeatingSpinBox(_HoldRepeatMixin, QtWidgets.QSpinBox):
    """QSpinBox with deterministic hold-to-step on the +/- buttons.

    Same SI narrow-space thousands-separator formatting as
    :class:`RepeatingDoubleSpinBox` (no fractional part).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._install_repeat()

    def textFromValue(self, value: int) -> str:
        return _format_with_thousands(float(value), 0)

    def valueFromText(self, text: str) -> int:
        # Strip suffix / prefix before parsing so partial edits over
        # a digits-only selection round-trip cleanly. See
        # :func:`_strip_affixes` for the rationale.
        cleaned = _strip_affixes(text, self.prefix(), self.suffix())
        cleaned = (cleaned.replace(_GROUP_SEP, "")
                          .replace(" ", "")
                          .replace(",", "")
                          .strip())
        if not cleaned:
            return self.value()
        try:
            return int(round(float(cleaned)))
        except ValueError:
            return self.value()

    def validate(self, text: str, pos: int):
        cleaned = _strip_affixes(text, self.prefix(), self.suffix())
        cleaned = (cleaned.replace(_GROUP_SEP, "")
                          .replace(" ", "")
                          .replace(",", ""))
        if cleaned in ("", "-", "+"):
            return (QtGui.QValidator.State.Intermediate, text, pos)
        try:
            int(round(float(cleaned)))
        except ValueError:
            return (QtGui.QValidator.State.Invalid, text, pos)
        return (QtGui.QValidator.State.Acceptable, text, pos)


class ScientificDoubleSpinBox(RepeatingDoubleSpinBox):
    """``RepeatingDoubleSpinBox`` that also formats very small or very
    large values in scientific notation.

    Inherits the hold-to-repeat buttons, SI thousands separator, and the
    ``validate`` fix that treats incomplete exponents ("1e", "2.5E-") as
    *Intermediate* so the user can type scientific notation naturally.

    Display rules (``textFromValue``):
    * ``0`` → fixed notation (``"0.00…"``)
    * ``1e-4 ≤ |v| < 1e6`` → fixed notation with thousands separator
    * otherwise → ``g``-format scientific notation (e.g. ``"1.23e-06"``)
      using the spinbox's current :meth:`decimals` setting for precision.

    ``valueFromText`` inherits the parent's implementation which calls
    ``float()`` on the stripped text, so ``"1e-3"``, ``"2.5E+6"``, etc.
    are parsed correctly without any extra code here.
    """

    def textFromValue(self, value: float) -> str:
        abs_v = abs(value)
        if abs_v == 0.0 or (1e-4 <= abs_v < 1e6):
            return _format_with_thousands(value, self.decimals())
        # Scientific notation: use the spinbox's decimals setting for the
        # number of significant digits shown after the mantissa point.
        prec = max(2, self.decimals())
        return f"{value:.{prec}e}"
