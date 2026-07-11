"""LP periodic-feature dropdown: None / Maximum VT sweep / Pause / both.

Operator: "Dropdown list for periodic feature: none, maximum VT sweep, pause,
maximum VT sweep + pause.  When not none, show and enable an input for time or
pulses."  The interval (time/pulses) + the at-start toggle show+enable when the
selection is not None; the pause duration shows+enables only when the selection
includes Pause.  One shared event schedule (the runner keys its period on
``_run_max_vt or _do_pause``).
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")


@pytest.fixture(scope="module")
def _lp_tab():
    # Build via a MainWindow so Qt keeps the whole widget tree alive across
    # tests (a standalone tab has its C++ children garbage-collected).
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    win = _lp_tab._win = MainWindow(simulate_default=True)   # hold a strong ref
    return win.lp_tab


def test_dropdown_has_the_four_options(_lp_tab):
    tab = _lp_tab
    items = [tab.periodic_combo.itemText(i)
             for i in range(tab.periodic_combo.count())]
    assert items == [tab.PERIODIC_NONE, tab.PERIODIC_MAXVT,
                     tab.PERIODIC_PAUSE, tab.PERIODIC_BOTH]


def test_none_disables_interval_and_hides_pause_duration(_lp_tab):
    tab = _lp_tab
    tab.periodic_combo.setCurrentText(tab.PERIODIC_NONE)
    assert tab.char_int.isEnabled() is False
    assert tab.do_event_at_start.isEnabled() is False
    assert tab._periodic_run_max_vt() is False
    assert tab._periodic_do_pause() is False


def test_maxvt_enables_interval_but_not_pause_duration(_lp_tab):
    tab = _lp_tab
    tab.periodic_combo.setCurrentText(tab.PERIODIC_MAXVT)
    assert tab.char_int.isEnabled() is True          # time/pulses input on
    assert tab.char_int_unit.isEnabled() is True
    assert tab.do_event_at_start.isEnabled() is True
    assert tab._periodic_run_max_vt() is True
    assert tab._periodic_do_pause() is False
    assert tab.pause_s.isEnabled() is False          # no pause → no duration


def test_pause_enables_interval_and_pause_duration(_lp_tab):
    tab = _lp_tab
    tab.periodic_combo.setCurrentText(tab.PERIODIC_PAUSE)
    assert tab.char_int.isEnabled() is True
    assert tab.pause_s.isEnabled() is True           # pause → duration on
    assert tab.pause_unit.isEnabled() is True
    assert tab._periodic_run_max_vt() is False
    assert tab._periodic_do_pause() is True


def test_both_enables_everything(_lp_tab):
    tab = _lp_tab
    tab.periodic_combo.setCurrentText(tab.PERIODIC_BOTH)
    assert tab.char_int.isEnabled() is True
    assert tab.pause_s.isEnabled() is True
    assert tab._periodic_run_max_vt() is True
    assert tab._periodic_do_pause() is True


def test_interval_row_hidden_when_none_shown_when_not(_lp_tab):
    tab = _lp_tab
    # Row visibility toggles with the selection (needs the tab shown for a
    # real isVisible; assert on the field-widget hidden state instead).
    tab.periodic_combo.setCurrentText(tab.PERIODIC_NONE)
    assert tab._pe_field.isHidden() is True
    tab.periodic_combo.setCurrentText(tab.PERIODIC_MAXVT)
    assert tab._pe_field.isHidden() is False
    # Pause duration row follows the pause inclusion.
    assert tab._pa_field.isHidden() is True          # max-VT only → no pause
    tab.periodic_combo.setCurrentText(tab.PERIODIC_BOTH)
    assert tab._pa_field.isHidden() is False


def test_lp_allows_multichannel_multiselect(_lp_tab):
    """Operator: "I wanted simultaneous pulsing if multichannel monopolar
    stimulation was selected."  LP must be MULTI-select (SINGLE_CONFIG False)
    so the combo panel actually lets the operator pick several channels —
    otherwise the simultaneous-pulsing path is unreachable dead code."""
    from stimtest.gui.experiment_tabs import LongPulsingTab
    assert LongPulsingTab.SINGLE_CONFIG is False
    # The combination panel must not be locked to single-select.
    assert _lp_tab.combo_panel._static_single_mode is False


def test_lp_multipolar_is_single_select(_lp_tab):
    """Operator (0.2.152): "only allow for one selection of channel/combo
    under (partial) multipolar configuration."  LP forces the (partial)
    multipolar kinds (BP/TP/PBP/PTP) to single-combo while keeping Monopolar
    multi-select (for simultaneous-channel pulsing).  The flag is LP-only —
    VT (also SINGLE_CONFIG False) keeps multipolar MULTI-select."""
    from PyQt6 import QtCore
    from stimtest.gui.experiment_tabs import LongPulsingTab, _BaseExperimentTab
    from stimtest.gui.combination_panel import (CombinationPanel, KIND_BP,
                                                KIND_MONO)
    from stimtest.electrode import ElectrodeArray
    assert LongPulsingTab.MULTIPOLAR_SINGLE is True
    assert _BaseExperimentTab.MULTIPOLAR_SINGLE is False      # VT/SP default
    assert _lp_tab.combo_panel._multipolar_single is True

    def _max_selectable(panel, kind, glob):
        panel.set_array(ElectrodeArray.utah_4x4())
        panel.set_global_return(glob)
        panel.set_actives([6])
        panel.kind_combo.setCurrentText(kind)
        lst = panel.combos_list
        for i in range(lst.count()):
            lst.item(i).setCheckState(QtCore.Qt.CheckState.Checked)
        return sum(1 for i in range(lst.count())
                   if lst.item(i).checkState() == QtCore.Qt.CheckState.Checked)

    lp = CombinationPanel(single_mode=False, multipolar_single=True)
    assert _max_selectable(lp, KIND_BP, False) == 1          # multipolar → single
    assert _max_selectable(lp, KIND_MONO, True) >= 1         # monopolar → multi
    assert lp._single_mode is False                          # after Monopolar

    vt = CombinationPanel(single_mode=False, multipolar_single=False)
    assert _max_selectable(vt, KIND_BP, False) > 1           # VT multipolar multi


def test_resolve_pulse_channels_multichannel_monopolar(_lp_tab):
    """Multi-channel MONOPOLAR → the full channel list (simultaneous pulsing);
    a single channel or any multipolar config → None (sequential chain)."""
    from stimtest.electrode import Configuration
    tab = _lp_tab
    mono = [Configuration.monopolar(1), Configuration.monopolar(3),
            Configuration.monopolar(5)]
    assert tab._resolve_pulse_channels(mono) == [1, 3, 5]
    # Single selection → sequential (None).
    assert tab._resolve_pulse_channels([Configuration.monopolar(1)]) is None
    # Empty → None.
    assert tab._resolve_pulse_channels([]) is None
    # Any MULTIPOLAR config (has returns) → None even with several (can't
    # co-run overlapping return paths → sequential chain).
    assert tab._resolve_pulse_channels(
        [Configuration.bipolar(1, 2), Configuration.bipolar(3, 4)]) is None
    assert tab._resolve_pulse_channels(
        [Configuration.monopolar(1), Configuration.bipolar(3, 4)]) is None


def test_legacy_prefs_migrate_to_dropdown(_lp_tab):
    tab = _lp_tab
    # Old prefs used two booleans; restore must map them to the dropdown.
    tab.restore_prefs({"do_max_before_pause": True, "do_pause": True})
    assert tab.periodic_combo.currentText() == tab.PERIODIC_BOTH
    tab.restore_prefs({"do_max_before_pause": True, "do_pause": False})
    assert tab.periodic_combo.currentText() == tab.PERIODIC_MAXVT
    tab.restore_prefs({"do_max_before_pause": False, "do_pause": True})
    assert tab.periodic_combo.currentText() == tab.PERIODIC_PAUSE
    tab.restore_prefs({"do_max_before_pause": False, "do_pause": False})
    assert tab.periodic_combo.currentText() == tab.PERIODIC_NONE
    # A new-style pref round-trips directly + re-applies the gate.
    tab.restore_prefs({"periodic_combo": tab.PERIODIC_PAUSE})
    assert tab.periodic_combo.currentText() == tab.PERIODIC_PAUSE
    assert tab.char_int.isEnabled() is True
