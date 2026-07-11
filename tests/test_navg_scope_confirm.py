"""Average count is confirmed against the connected oscilloscope.

Operator: "When the average count is set, apply it on the oscilloscope, get
what is set, and if it is different, probably due to rounding … change the
input to reflect that" + "Always check with commands to the oscilloscope
about changing settings by getting what is set to confirm".

The Setup tab round-trips NUMAVg through the scope
(``Oscilloscope.set_average_count`` → writes ``ACQuire:NUMAVg`` + re-queries)
and reflects the DEVICE-reported value into the spinbox when it differs from
what the operator entered.  The write is debounced (typing / arrow-holding
coalesces into one round-trip) and touches only NUMAVg — never the
acquisition mode — so it can't trigger the slow SAMPLE↔AVERAGE reconfig.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")


@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _setup(_app):
    from stimtest.gui.setup_tab import SetupTab
    return SetupTab()


class _FakeScope:
    """Tektronix-like scope: NUMAVg snaps to a power-of-two grid, and
    ``set_average_count`` returns the DEVICE value after 'reading it back'."""
    def __init__(self, choices=(2, 4, 8, 16, 32, 64, 128, 256, 512)):
        self._choices = list(choices)
        self.applied = []

    def average_count_choices(self):
        return list(self._choices)

    def set_average_count(self, n):
        target = min(self._choices, key=lambda v: abs(v - int(n)))
        self.applied.append((int(n), target))
        return target


# --------------------------------------------------------------------- driver
def test_base_set_average_count_echoes(_app):
    """The base/simulator scope has no grid → echoes the request."""
    from stimtest.hardware.simulator import SimulatedOscilloscope
    s = SimulatedOscilloscope()
    assert s.set_average_count(100) == 100
    assert s.set_average_count(64) == 64


# ------------------------------------------------------------------- GUI logic
def test_confirm_reflects_scope_snapped_value(_app):
    st = _setup(_app)
    st._scope = _FakeScope()
    st.acq_navg_spin.setValue(100)          # off-grid request
    st._confirm_navg_on_scope()             # fire directly (bypass debounce)
    assert st.acq_navg_spin.value() == 128  # scope's confirmed value
    assert st._scope.applied[-1] == (100, 128)


def test_confirm_noop_when_already_on_grid(_app):
    st = _setup(_app)
    st._scope = _FakeScope()
    st.acq_navg_spin.setValue(64)           # already a supported value
    st._confirm_navg_on_scope()
    assert st.acq_navg_spin.value() == 64


def test_confirm_noop_without_scope(_app):
    st = _setup(_app)
    st._scope = None
    st.acq_navg_spin.setValue(100)
    st._confirm_navg_on_scope()
    assert st.acq_navg_spin.value() == 100  # left as typed


def test_confirm_noop_in_sample_mode(_app):
    st = _setup(_app)
    st._scope = _FakeScope()
    st.acq_mode_combo.setCurrentText("SAMPLE")
    st.acq_navg_spin.setValue(100)
    st._confirm_navg_on_scope()
    assert st.acq_navg_spin.value() == 100  # NUMAVg irrelevant in SAMPLE


def test_change_arms_debounce_timer(_app):
    st = _setup(_app)
    st._scope = _FakeScope()
    st.acq_mode_combo.setCurrentText("AVERAGE")
    # The count commits on Enter/return/focus-out (editingFinished), NOT per
    # keystroke — setValue alone must NOT arm the confirm.
    st.acq_navg_spin.setValue(120)
    assert not st._navg_confirm_timer.isActive(), (
        "typing (valueChanged) must not arm the scope round-trip")
    st.acq_navg_spin.editingFinished.emit()          # user pressed Enter / clicked out
    assert st._navg_confirm_timer.isActive()


def test_count_emits_on_commit_not_keystroke(_app):
    """acquisitionChanged fires on Enter/return/focus-out, NOT per keystroke
    (operator: log spam while typing the average count)."""
    st = _setup(_app)
    st.acq_mode_combo.setCurrentText("AVERAGE")
    got = []
    st.acquisitionChanged.connect(lambda m, n: got.append((m, n)))
    st.acq_navg_spin.setValue(64)                    # "typing" — must NOT emit
    assert got == [], "valueChanged (typing) must not emit acquisitionChanged"
    st.acq_navg_spin.editingFinished.emit()          # Enter / click-out → commit
    assert got and got[-1] == ("AVERAGE", 64)
    got.clear()
    st.acq_navg_spin.editingFinished.emit()          # no-op focus-out → skipped
    assert got == [], "an unchanged commit must not re-emit"


def test_confirm_rebroadcasts_confirmed_value(_app):
    st = _setup(_app)
    st._scope = _FakeScope()
    got = []
    st.acquisitionChanged.connect(lambda m, n: got.append((m, n)))
    st.acq_navg_spin.setValue(100)
    got.clear()
    st._confirm_navg_on_scope()
    # The confirmed value is re-broadcast so the inline editor + tabs update.
    assert got and got[-1] == ("AVERAGE", 128)


def test_apply_scope_capabilities_stores_scope(_app):
    """Connecting a scope stores it so the confirm can reach it; a
    disconnect (None) clears it."""
    st = _setup(_app)
    fake = _FakeScope()
    # Give the fake the minimal surface apply_scope_capabilities touches.
    fake.info = type("I", (), {"n_channels": 4, "has_ext_trigger": False})()
    fake.acquisition_modes = lambda: ["SAMPLE", "AVERAGE"]
    fake.max_average_count = lambda: 512
    st.apply_scope_capabilities(fake)
    assert st._scope is fake
    st.apply_scope_capabilities(None)
    assert st._scope is None
