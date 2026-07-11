"""Plexon Test Board device — top of the list, no electrodes.

Operator: "I also want the Plexon Test Board as a device option (top of the
list). Hide the electrode options since there are no electrodes on there.
Because the test board has no electrodes, disable area for waveform metrics."
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def test_test_board_is_first_in_catalog_and_combo(qapp):
    from stimtest.config import DEVICES
    from stimtest.gui.setup_tab import SetupTab
    assert next(iter(DEVICES)) == "Plexon Test Board"
    assert DEVICES["Plexon Test Board"].has_electrodes is False
    t = SetupTab()
    assert t.device_combo.itemText(0) == "Plexon Test Board"


def test_test_board_hides_electrode_rows_real_device_shows_them(qapp):
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    t.device_combo.setCurrentText("Plexon Test Board")
    # Every electrode-specific row is explicitly hidden.
    assert t._electrode_option_rows
    for w in t._electrode_option_rows:
        assert w.isHidden(), "electrode row should be hidden for test board"
    # A real electrode array shows them again.
    t.device_combo.setCurrentText("Blackrock Omnetics (4×4)")
    for w in t._electrode_option_rows:
        assert not w.isHidden(), "electrode row should show for a real device"


def test_test_board_forces_zero_area_and_neutral_coating(qapp):
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    t.device_combo.setCurrentText("Plexon Test Board")
    arr = t.current_array()
    assert arr.sites, "test board should still have selectable channels"
    assert all(s.surface_area_um2 == 0.0 for s in arr.sites)
    assert all(s.coating == "Test board" for s in arr.sites)
    # A real device restores a positive area.
    t.device_combo.setCurrentText("Blackrock Omnetics (4×4)")
    arr2 = t.current_array()
    assert arr2.sites[0].surface_area_um2 > 0.0


def test_zero_area_disables_density_metrics(qapp):
    # Q_inj (charge density) is NaN at zero area; raw Q_ph still finite.
    from stimtest.metrics import charge_injection_mc_per_cm2
    from stimtest.waveforms import PulsePattern, Phase
    import numpy as np
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-50.0, width_us=100.0),
        Phase(amplitude_ua=50.0, width_us=100.0),
    ], rate_hz=100.0)
    q_ph, q_inj = charge_injection_mc_per_cm2(pat, 0.0)
    assert np.isfinite(q_ph) and q_ph != 0.0
    assert np.isnan(q_inj)


def test_test_board_hides_eret_eact_scope_roles(qapp):
    from stimtest.gui.setup_tab import (
        SetupTab, ROLE_EACT, ROLE_ERET)
    t = SetupTab()

    def items(cb):
        return [cb.itemText(i) for i in range(cb.count())]

    # Real device: E_act / E_ret are offered; assign them to CH3/CH4.
    t.device_combo.setCurrentText("Blackrock Omnetics (4×4)")
    assert ROLE_ERET in items(t._role_combos["CH1"])
    t._role_combos["CH3"].setCurrentText(ROLE_ERET)
    t._role_combos["CH4"].setCurrentText(ROLE_EACT)
    # Test board: both roles vanish from every dropdown + the held rows reset.
    t.device_combo.setCurrentText("Plexon Test Board")
    for cb in t._role_combos.values():
        its = items(cb)
        assert ROLE_ERET not in its and ROLE_EACT not in its
    assert t._role_combos["CH3"].currentText() != ROLE_ERET
    assert t._role_combos["CH4"].currentText() != ROLE_EACT
    # Back to a real device: the stashed E_ret / E_act come back.
    t.device_combo.setCurrentText("Blackrock Omnetics (4×4)")
    assert ROLE_ERET in items(t._role_combos["CH1"])
    assert t._role_combos["CH3"].currentText() == ROLE_ERET
    assert t._role_combos["CH4"].currentText() == ROLE_EACT


def test_pre_run_damage_screen_skipped_for_zero_area(qapp):
    # The pre-run charge-density damage screen must no-op (return True) for a
    # zero-area test board rather than divide Q by 0.
    from stimtest.gui.main_window import MainWindow
    from stimtest.waveforms import PulsePattern, Phase
    w = MainWindow()
    tab = w.vt_tab
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-50.0, width_us=100.0),
        Phase(amplitude_ua=50.0, width_us=100.0),
    ], rate_hz=100.0)
    assert tab._pre_run_warning_check(pat, 0.0) is True
