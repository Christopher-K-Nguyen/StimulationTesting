"""Current-offset (offset_ua pedestal) enable checkbox, shape-gated.

Operator: "In the test parameters, I want a checkbox to enable the current
offset … under certain shapes."  The offset is opt-in via
``sym_offset_enable_chk``, shown ONLY for non-rectangular shapes; unchecked →
the pattern carries no offset (0 to peak).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _panel_sine():
    from stimtest.gui.pattern_panel import (PatternControlPanel, BIPHASIC,
                                            SYMMETRIC)
    from stimtest.waveforms import SHAPE_SINUSOIDAL
    p = PatternControlPanel()
    p.phase_count.setCurrentText(BIPHASIC)
    p.symmetry.setCurrentText(SYMMETRIC)
    # select the sinusoidal symmetric shape
    for i in range(p.shape_combo.count()):
        if p.shape_combo.itemData(i) == SHAPE_SINUSOIDAL:
            p.shape_combo.setCurrentIndex(i)
            break
    p.sym_offset_ua.setValue(30.0)
    return p


def test_offset_disabled_by_default_zero_in_pattern(qapp):
    p = _panel_sine()
    assert p.sym_offset_enable_chk.isChecked() is False
    assert p.sym_offset_ua.isEnabled() is False        # spinbox gated off
    pat = p.pattern()
    # Even with the spinbox at 30 µA, the offset is NOT applied (unchecked).
    assert all(abs(float(ph.offset_ua)) < 1e-9 for ph in pat.phases), \
        [ph.offset_ua for ph in pat.phases]


def test_offset_applied_when_checkbox_enabled(qapp):
    p = _panel_sine()
    p.sym_offset_enable_chk.setChecked(True)
    assert p.sym_offset_ua.isEnabled() is True
    pat = p.pattern()
    assert all(abs(float(ph.offset_ua) - 30.0) < 1e-6 for ph in pat.phases), \
        [ph.offset_ua for ph in pat.phases]


def test_offset_checkbox_hidden_for_rectangular(qapp):
    from stimtest.gui.pattern_panel import (PatternControlPanel, BIPHASIC,
                                            SYMMETRIC)
    from stimtest.waveforms import SHAPE_RECTANGULAR, SHAPE_SINUSOIDAL
    p = PatternControlPanel()
    p.phase_count.setCurrentText(BIPHASIC)
    p.symmetry.setCurrentText(SYMMETRIC)
    p.show()                                            # realise visibility
    try:
        for i in range(p.shape_combo.count()):
            if p.shape_combo.itemData(i) == SHAPE_RECTANGULAR:
                p.shape_combo.setCurrentIndex(i); break
        assert p.sym_offset_enable_chk.isHidden() is True   # no offset for rect
        for i in range(p.shape_combo.count()):
            if p.shape_combo.itemData(i) == SHAPE_SINUSOIDAL:
                p.shape_combo.setCurrentIndex(i); break
        assert p.sym_offset_enable_chk.isHidden() is False  # shown for sine
    finally:
        p.hide()


def test_offset_enable_prefs_round_trip(qapp):
    p = _panel_sine()
    p.sym_offset_enable_chk.setChecked(True)
    prefs = p.current_prefs()
    assert prefs.get("sym_offset_enable") is True
    q = _panel_sine()
    assert q.sym_offset_enable_chk.isChecked() is False   # fresh default
    q.restore_prefs(prefs)
    assert q.sym_offset_enable_chk.isChecked() is True
    assert q.sym_offset_ua.isEnabled() is True
    # A prefs blob WITHOUT the key defaults OFF (back-compat).
    r = _panel_sine()
    r.restore_prefs({"sym_offset_ua": 12.0})
    assert r.sym_offset_enable_chk.isChecked() is False
