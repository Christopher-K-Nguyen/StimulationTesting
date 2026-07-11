"""Tests for the VT 'Lock a parameter' master enable checkbox.

Operator request: in Fixed-charge/phase mode, the 3-way lock (one of
current / phase-width / charge-per-phase is auto-derived from the other
two) made it impossible to change two parameters without the third
forcing one of them to move.  The ``qph_lock_enable_chk`` checkbox
toggles the whole lock:

  * CHECKED (default) — historical behaviour: the selected radio's
    parameter is auto-derived; editing an unlocked one recomputes it.
  * UNCHECKED — current AND phase width are independent; charge/phase
    becomes a read-only ``I×W`` display and constrains nothing.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _vt(qapp):
    from stimtest.gui.experiment_tabs import VoltageTransientTab
    from stimtest.electrode import ElectrodeArray
    t = VoltageTransientTab(ElectrodeArray.utah_4x4())
    t.mode_combo.setCurrentText(t.MODE_FIXED_QPH)
    return t


def test_checkbox_exists_and_defaults_on(qapp):
    t = _vt(qapp)
    assert hasattr(t, "qph_lock_enable_chk")
    assert t.qph_lock_enable_chk.isChecked()  # default ON (preserve behaviour)
    assert t.qph_lock_current.isEnabled()     # radios live when locked


def test_locked_couples_current_and_width(qapp):
    t = _vt(qapp)
    t.qph_lock_enable_chk.setChecked(True)
    t.qph_lock_current.setChecked(True)   # current is the derived knob
    t.qph_qph.setValue(10.0)              # Q = 10 nC
    t.qph_width.setValue(200.0)           # W = 200 µs → I = 50 µA
    i_before = t.qph_current.value()
    t.qph_width.setValue(100.0)           # change W → I must move (locked)
    assert abs(t.qph_current.value() - i_before) > 1.0


def test_unlocked_keeps_current_and_width_independent(qapp):
    t = _vt(qapp)
    t.qph_lock_enable_chk.setChecked(False)
    # Radios disabled, charge/phase read-only, current & width free.
    assert not t.qph_lock_current.isEnabled()
    assert t.qph_qph.isReadOnly()
    t.qph_current.setValue(50.0)
    t.qph_width.setValue(200.0)
    i0 = t.qph_current.value()
    t.qph_width.setValue(123.0)           # change W → current must NOT move
    assert abs(t.qph_current.value() - i0) < 1e-6
    # Charge/phase just displays I×W/1000.
    assert abs(t.qph_qph.value() - 50.0 * 123.0 / 1000.0) < 1e-3


def test_relock_restores_coupling(qapp):
    t = _vt(qapp)
    t.qph_lock_enable_chk.setChecked(False)
    t.qph_lock_enable_chk.setChecked(True)   # re-lock
    assert t.qph_lock_current.isEnabled()
    t.qph_lock_width.setChecked(True)        # width is now derived
    t.qph_qph.setValue(8.0)
    t.qph_current.setValue(40.0)
    w_before = t.qph_width.value()
    t.qph_current.setValue(80.0)             # change I → W must move (locked)
    assert abs(t.qph_width.value() - w_before) > 1.0


def test_lock_enabled_prefs_round_trip(qapp):
    t = _vt(qapp)
    t.qph_lock_enable_chk.setChecked(False)
    prefs = t.current_prefs()
    assert prefs["qph_lock_enabled"] is False

    t2 = _vt(qapp)
    t2.restore_prefs(prefs)
    assert t2.qph_lock_enable_chk.isChecked() is False
    assert not t2.qph_lock_current.isEnabled()


def test_missing_pref_defaults_to_enabled(qapp):
    # Older prefs without the key → lock stays ON (no behaviour change).
    t = _vt(qapp)
    t.restore_prefs({"qph_lock_target": "qph"})  # no qph_lock_enabled key
    assert t.qph_lock_enable_chk.isChecked() is True
