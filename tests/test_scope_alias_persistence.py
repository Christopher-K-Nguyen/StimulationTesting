"""Scope channel→role assignments must survive a disconnect / restart.

Operator: "the choices for oscilloscope channels is not being remembered.
CH3 is constantly set to Eret."

Root cause: a scope disconnect called ``clear_scope_mapping``, which
wiped every role to ``None``; the next prefs save persisted those
``None``s, and on the following connect ``apply_default_scope_mapping``
re-applied the catalog default (CH3 = E_ret).  The fix keeps the user's
assignments across a disconnect — the map is only consumed at run time
(which needs a connected scope), so a stale map while disconnected is
harmless.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _setup_tab(qapp):
    from stimtest.gui.main_window import MainWindow
    st = MainWindow(simulate_default=True).setup_tab
    # HERMETIC: force a real electrode-bearing device so the scope-role
    # combos offer E_ret / E_act regardless of what a shared-sandbox prefs
    # file restored.  A restored "Plexon Test Board" (has_electrodes=False)
    # restricts the roles to V_mon / I_mon / Trigger (gotcha #89 / #152), and
    # applies that on prefs-restore — which would make these role-persistence
    # tests fail with E_ret / E_act unavailable.  These tests are about the
    # 4-channel-scope role behaviour, so a real array is the correct fixture
    # state; without this they were order-dependent on whether an earlier
    # test in the run wrote a test-board device to the sandbox prefs.
    st.device_combo.setCurrentText("Linear")
    return st


def test_disconnect_preserves_channel_roles(qapp):
    from stimtest.gui.setup_tab import ROLE_IMON
    st = _setup_tab(qapp)
    st._role_combos["CH3"].setCurrentText(ROLE_IMON)   # deliberate non-default
    st.clear_scope_mapping()                           # simulate disconnect
    assert st._role_combos["CH3"].currentText() == ROLE_IMON, \
        "disconnect must NOT wipe the user's channel→role choice"


def test_role_round_trips_through_prefs_after_disconnect(qapp):
    from stimtest.gui.setup_tab import ROLE_IMON
    st = _setup_tab(qapp)
    st._role_combos["CH3"].setCurrentText(ROLE_IMON)
    st.clear_scope_mapping()                           # disconnect THEN save
    prefs = st.current_prefs()
    assert prefs["channel_roles"]["CH3"] == ROLE_IMON, \
        "the saved mapping must carry the user's choice, not None"

    # Relaunch: restore, then reconnect (apply_default fills None rows only).
    st2 = _setup_tab(qapp)
    st2.restore_prefs(prefs)
    st2.apply_default_scope_mapping()
    assert st2._role_combos["CH3"].currentText() == ROLE_IMON, \
        "CH3 must be REMEMBERED across a restart, not reset to E_ret"


def test_two_channel_scope_hides_but_does_not_wipe_roles(qapp):
    """A 2-channel scope hides CH3/CH4 but must PRESERVE their stored
    role so the choice survives a restart and reappears when a 4-channel
    scope reconnects — and current_aliases must NOT map the hidden roles
    onto channels that don't exist (operator: scope channel settings not
    remembered)."""
    from stimtest.gui.setup_tab import ROLE_EACT, ROLE_ERET, ROLE_VMON, ROLE_IMON
    st = _setup_tab(qapp)
    st._role_combos["CH1"].setCurrentText(ROLE_VMON)
    st._role_combos["CH2"].setCurrentText(ROLE_IMON)
    st._role_combos["CH3"].setCurrentText(ROLE_ERET)
    st._role_combos["CH4"].setCurrentText(ROLE_EACT)

    st._set_visible_scope_channels(2)                  # 2-channel scope
    # roles preserved (not wiped)
    assert st._role_combos["CH3"].currentText() == ROLE_ERET
    assert st._role_combos["CH4"].currentText() == ROLE_EACT
    # but the alias map must not reference CH3/CH4
    aliases = st.current_aliases()
    assert "eret" not in aliases and "eact" not in aliases, aliases
    assert aliases.get("vmon") == "CH1" and aliases.get("imon") == "CH2"

    # the saved prefs carry the real choice, not None
    assert st.current_prefs()["channel_roles"]["CH3"] == ROLE_ERET

    # reconnecting a 4-channel scope re-exposes the preserved roles
    st._set_visible_scope_channels(4)
    assert st.current_aliases().get("eret") == "CH3"
    assert st.current_aliases().get("eact") == "CH4"


def _force_unconfigured_all_none(st):
    """Reset a tab to the fresh-install state: all roles None, configured
    flag clear (without the resets themselves flipping the flag)."""
    from stimtest.gui.setup_tab import ROLE_NONE
    for ch in ("CH1", "CH2", "CH3", "CH4"):
        cb = st._role_combos[ch]
        cb.blockSignals(True)
        cb.setCurrentText(ROLE_NONE)
        cb.blockSignals(False)
    st._scope_roles_user_configured = False


def test_apply_default_fills_all_on_fresh_setup(qapp):
    # First-ever connect with nothing configured → catalog defaults fill.
    from stimtest.gui.setup_tab import ROLE_VMON, ROLE_IMON, ROLE_ERET, ROLE_EACT
    st = _setup_tab(qapp)
    _force_unconfigured_all_none(st)
    st.apply_default_scope_mapping()
    assert st._role_combos["CH1"].currentText() == ROLE_VMON
    assert st._role_combos["CH2"].currentText() == ROLE_IMON
    assert st._role_combos["CH3"].currentText() == ROLE_ERET
    assert st._role_combos["CH4"].currentText() == ROLE_EACT


def test_apply_default_is_noop_once_configured(qapp):
    # Operator's bug: a deliberate None on CH3 must SURVIVE a reconnect's
    # apply_default — once the mapping is configured, defaults never stomp.
    from stimtest.gui.setup_tab import ROLE_VMON, ROLE_IMON, ROLE_NONE, ROLE_TRIG
    st = _setup_tab(qapp)
    _force_unconfigured_all_none(st)
    # User configures the mapping the way they want: CH3 = None.
    st._role_combos["CH1"].setCurrentText(ROLE_VMON)
    st._role_combos["CH2"].setCurrentText(ROLE_IMON)
    st._role_combos["CH3"].setCurrentText(ROLE_NONE)   # deliberate None
    st._role_combos["CH4"].setCurrentText(ROLE_TRIG)
    assert st._scope_roles_user_configured is True
    # A scope (re)connect calls apply_default_scope_mapping — must be a no-op.
    st.apply_default_scope_mapping()
    assert st._role_combos["CH3"].currentText() == ROLE_NONE, \
        "deliberate None on CH3 must NOT be re-filled with E_ret on connect"
    assert st._role_combos["CH4"].currentText() == ROLE_TRIG


def test_restored_roles_block_default_override_on_connect(qapp):
    # Full round-trip: save CH3=None among real roles, relaunch, restore,
    # THEN connect → apply_default must leave CH3 = None.
    from stimtest.gui.setup_tab import (ROLE_VMON, ROLE_IMON, ROLE_NONE,
                                        ROLE_TRIG)
    st = _setup_tab(qapp)
    _force_unconfigured_all_none(st)
    st._role_combos["CH1"].setCurrentText(ROLE_VMON)
    st._role_combos["CH2"].setCurrentText(ROLE_IMON)
    st._role_combos["CH3"].setCurrentText(ROLE_NONE)
    st._role_combos["CH4"].setCurrentText(ROLE_TRIG)
    prefs = st.current_prefs()
    assert prefs["channel_roles"]["CH3"] == ROLE_NONE

    st2 = _setup_tab(qapp)
    _force_unconfigured_all_none(st2)
    st2.restore_prefs(prefs)
    assert st2._scope_roles_user_configured is True
    st2.apply_default_scope_mapping()          # simulate the scope connect
    assert st2._role_combos["CH3"].currentText() == ROLE_NONE, \
        "CH3=None must be REMEMBERED across restart + reconnect"
    assert st2._role_combos["CH1"].currentText() == ROLE_VMON


def test_experiment_plot_uses_larger_fonts(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    # Axis TITLES are now Qt widgets (pyqtgraph's didn't render); check
    # the bottom QLabel + its font size, and the tick font in the axis.
    assert sp._bottom_title.text() == "Time [µs]"
    assert sp._bottom_title.font().pointSize() == sp._AXIS_LABEL_PT
    assert sp._left_title.text() == "Voltage [V]"
    tick = sp._plot.getAxis("left").style.get("tickFont")
    assert tick is not None and tick.pointSize() == sp._TICK_PT
