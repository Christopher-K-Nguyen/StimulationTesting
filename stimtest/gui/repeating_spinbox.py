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

from PyQt6 import QtCore, QtGui, QtWidgets


_INITIAL_DELAY_MS = 400      # ms between press and the first auto-repeat tick
_REPEAT_INTERVAL_MS = 60     # ms between subsequent ticks while still held


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


class RepeatingDoubleSpinBox(_HoldRepeatMixin, QtWidgets.QDoubleSpinBox):
    """QDoubleSpinBox with deterministic hold-to-step on the +/- buttons."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._install_repeat()


class RepeatingSpinBox(_HoldRepeatMixin, QtWidgets.QSpinBox):
    """QSpinBox with deterministic hold-to-step on the +/- buttons."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._install_repeat()
