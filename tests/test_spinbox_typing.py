"""Tests for the suffix-aware spinbox validation fix.

Reproduces the bug the user reported: typing a digit while only
the value (NOT the suffix) is selected used to be rejected by
the spinbox's ``validate`` method, because the suffix stayed
attached to the partially-edited string and ``float("5 µA")``
fails. The fix strips the suffix / prefix before parsing so
both partial-selection edits and full-selection edits work.

We can't simulate the actual selection-and-keystroke ordering
without a full Qt event loop, but we CAN exercise the validate
and valueFromText paths directly with the same string Qt would
produce after a digits-only-replacement. That's what the bug
manifested as.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def test_validate_accepts_partial_edit_with_suffix(qapp):
    """``validate("5 µA", _)`` returns Acceptable when the
    suffix is " µA" (the digits-only-selection edit case)."""
    from PyQt6 import QtGui
    from stimtest.gui.repeating_spinbox import RepeatingDoubleSpinBox
    sp = RepeatingDoubleSpinBox()
    sp.setRange(0.0, 1000.0)
    sp.setSuffix(" µA")
    state, _t, _p = sp.validate("5 µA", 1)
    assert state == QtGui.QValidator.State.Acceptable
    state, _t, _p = sp.validate("100 µA", 3)
    assert state == QtGui.QValidator.State.Acceptable


def test_validate_accepts_value_without_suffix(qapp):
    """The full-selection-overwrite case (no suffix attached)
    still works."""
    from PyQt6 import QtGui
    from stimtest.gui.repeating_spinbox import RepeatingDoubleSpinBox
    sp = RepeatingDoubleSpinBox()
    sp.setRange(0.0, 1000.0)
    sp.setSuffix(" µA")
    state, _t, _p = sp.validate("250", 3)
    assert state == QtGui.QValidator.State.Acceptable


def test_validate_intermediate_for_partial_minus_with_suffix(qapp):
    """Mid-edit string ``"- µA"`` is intermediate, not invalid —
    user might be typing ``-100``."""
    from PyQt6 import QtGui
    from stimtest.gui.repeating_spinbox import RepeatingDoubleSpinBox
    sp = RepeatingDoubleSpinBox()
    sp.setRange(-1000.0, 1000.0)
    sp.setSuffix(" µA")
    state, _t, _p = sp.validate("- µA", 1)
    assert state == QtGui.QValidator.State.Intermediate


def test_value_from_text_strips_suffix(qapp):
    """``valueFromText("5 µA")`` parses to 5.0, not falls back
    to the spinbox's current value."""
    from stimtest.gui.repeating_spinbox import RepeatingDoubleSpinBox
    sp = RepeatingDoubleSpinBox()
    sp.setRange(0.0, 1000.0)
    sp.setDecimals(1)
    sp.setSuffix(" µA")
    sp.setValue(100.0)
    assert sp.valueFromText("5 µA") == pytest.approx(5.0)
    assert sp.valueFromText("250.5 µA") == pytest.approx(250.5)


def test_value_from_text_strips_prefix(qapp):
    """Prefix handling — symmetric counterpart of the suffix fix."""
    from stimtest.gui.repeating_spinbox import RepeatingDoubleSpinBox
    sp = RepeatingDoubleSpinBox()
    sp.setRange(-1000.0, 1000.0)
    sp.setPrefix("± ")
    sp.setValue(50.0)
    assert sp.valueFromText("± 25") == pytest.approx(25.0)


def test_repeating_spinbox_suffix_handling(qapp):
    """Same fix applied to the int-valued ``RepeatingSpinBox``."""
    from PyQt6 import QtGui
    from stimtest.gui.repeating_spinbox import RepeatingSpinBox
    sp = RepeatingSpinBox()
    sp.setRange(0, 1000)
    sp.setSuffix(" pulses")
    state, _t, _p = sp.validate("42 pulses", 2)
    assert state == QtGui.QValidator.State.Acceptable
    assert sp.valueFromText("42 pulses") == 42


def test_validate_with_partial_suffix_chunk(qapp):
    """Intermediate edit where the suffix is missing the leading
    space (``"5µA"``) — the loose-match branch in
    :func:`_strip_affixes` should handle this."""
    from PyQt6 import QtGui
    from stimtest.gui.repeating_spinbox import RepeatingDoubleSpinBox
    sp = RepeatingDoubleSpinBox()
    sp.setRange(0.0, 1000.0)
    sp.setSuffix(" µA")
    state, _t, _p = sp.validate("5µA", 1)
    assert state == QtGui.QValidator.State.Acceptable


def test_validate_rejects_truly_invalid_text(qapp):
    """Garbage input that isn't suffix-related is still rejected."""
    from PyQt6 import QtGui
    from stimtest.gui.repeating_spinbox import RepeatingDoubleSpinBox
    sp = RepeatingDoubleSpinBox()
    sp.setRange(0.0, 1000.0)
    sp.setSuffix(" µA")
    state, _t, _p = sp.validate("hello µA", 5)
    assert state == QtGui.QValidator.State.Invalid


def test_validate_with_thousands_separator_and_suffix(qapp):
    """The narrow-space group separator AND the suffix coexist —
    a pasted "1 000 µA" should validate cleanly."""
    from PyQt6 import QtGui
    from stimtest.gui.repeating_spinbox import RepeatingDoubleSpinBox
    sp = RepeatingDoubleSpinBox()
    sp.setRange(0.0, 100000.0)
    sp.setDecimals(0)
    sp.setSuffix(" µA")
    state, _t, _p = sp.validate("1 000 µA", 5)
    assert state == QtGui.QValidator.State.Acceptable
    assert sp.valueFromText("1 000 µA") == pytest.approx(1000.0)
