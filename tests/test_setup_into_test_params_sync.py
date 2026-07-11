"""Tests for pulling Setup parameters into the active experiment tab
when the operator opens the Test parameters page.

Request: "When entering the Test parameters, load the Setup parameters,
if they have not already."

Some Setup params (channel aliases, water-window limits) don't re-fire
their change signal on prefs restore, so they could reach the
experiment tab stale.  ``MainWindow._on_top_tab_changed`` pulls the
current Setup state into the active experiment tab on entry to the Test
parameters page — guarded by ``_setup_dirty_for_test`` so it runs on a
pending Setup change (or first entry) but NOT on every tab switch.  The
array is deliberately excluded (``set_array`` clears channel
selections).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _fresh_window(qapp):
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    for _ in range(5):
        qapp.processEvents()  # drain the prefs-restore signal burst
    return w


def _instrument(tab):
    """Record which Setup-setters the entry-sync calls."""
    calls = []
    for name in ("set_environment", "set_aliases", "set_potential_limits",
                 "set_user_identity", "set_session_subject",
                 # oscilloscope settings
                 "set_acquisition", "set_trigger_source",
                 "set_digital_trigger"):
        orig = getattr(tab, name)

        def mk(n, o):
            def wrap(*a, **k):
                calls.append(n)
                return o(*a, **k)
            return wrap
        setattr(tab, name, mk(name, orig))
    return calls


def _enter_test_params(w, qapp):
    w.tabs.setCurrentWidget(w._test_params_tab)
    for _ in range(3):
        qapp.processEvents()


def _leave_to_setup(w, qapp):
    w.tabs.setCurrentWidget(w.setup_tab)
    for _ in range(3):
        qapp.processEvents()


def test_dirty_entry_pulls_setup_params(qapp):
    w = _fresh_window(qapp)
    tab = w._exp_tab_by_code[w._current_exp_code][0]
    calls = _instrument(tab)
    w._setup_dirty_for_test = True
    calls.clear()
    _enter_test_params(w, qapp)
    # The restore-gap params (aliases / limits) must be pulled in.
    assert "set_aliases" in calls
    assert "set_potential_limits" in calls
    # OSCILLOSCOPE settings (acquisition + trigger) must be pulled too —
    # the operator-reported gap.
    assert "set_acquisition" in calls
    assert "set_trigger_source" in calls
    assert "set_digital_trigger" in calls


def test_clean_entry_does_not_resync(qapp):
    w = _fresh_window(qapp)
    tab = w._exp_tab_by_code[w._current_exp_code][0]
    calls = _instrument(tab)
    # Already clean → entering must NOT re-pull (don't churn / clobber).
    w._setup_dirty_for_test = False
    _leave_to_setup(w, qapp)
    calls.clear()
    _enter_test_params(w, qapp)
    assert calls == []


def test_setup_change_rearms_the_sync(qapp):
    w = _fresh_window(qapp)
    tab = w._exp_tab_by_code[w._current_exp_code][0]
    calls = _instrument(tab)
    w._setup_dirty_for_test = False
    # A Setup parameter change marks the active tab's view stale.
    w._log_setup_changes = True
    w._log_setup_change("aliases: vmon=CH1")
    assert w._setup_dirty_for_test is True
    _leave_to_setup(w, qapp)
    calls.clear()
    _enter_test_params(w, qapp)
    assert "set_aliases" in calls


def test_entry_sync_never_touches_the_array(qapp):
    # set_array clears channel selections — the entry-sync must NOT call
    # it (genuine array changes go through the arrayChanged forwarder).
    w = _fresh_window(qapp)
    tab = w._exp_tab_by_code[w._current_exp_code][0]
    array_calls = []
    orig = tab.set_array
    tab.set_array = lambda *a, **k: (array_calls.append(1), orig(*a, **k))[1]
    w._setup_dirty_for_test = True
    _enter_test_params(w, qapp)
    assert array_calls == []


def test_entry_sync_pre_applies_scope_hardware(qapp):
    # Operator: "set all necessary oscilloscope settings when entering
    # the Test parameters tab, including record length."  The entry-sync
    # must invoke the HARDWARE pre-apply after the cache pulls.
    w = _fresh_window(qapp)
    tab = w._exp_tab_by_code[w._current_exp_code][0]
    calls = []
    tab.apply_scope_settings_on_entry = lambda: calls.append(1)
    w._setup_dirty_for_test = True
    _enter_test_params(w, qapp)
    assert calls, "apply_scope_settings_on_entry must run on dirty entry"


def test_apply_scope_settings_on_entry_writes_hardware(qapp):
    # Unit-level: the pre-apply pushes record length + acquisition +
    # trigger to the scope, and no-ops without a scope.
    from unittest.mock import MagicMock
    from stimtest.hardware.tektronix import DEFAULT_RECORD_LENGTH
    w = _fresh_window(qapp)
    tab = w._exp_tab_by_code[w._current_exp_code][0]
    # No scope → silent no-op.
    tab._scope = None
    tab.apply_scope_settings_on_entry()
    # Scope present → all three settings land.
    scope = MagicMock()
    tab._scope = scope
    tab.apply_scope_settings_on_entry()
    scope.set_record_length.assert_called_once_with(DEFAULT_RECORD_LENGTH)
    scope.set_acquisition_mode.assert_called_once()
    scope.set_trigger.assert_called_once()
    tab._scope = None  # don't leak the mock into other tests


def test_connect_rearms_the_entry_sync(qapp):
    # Connecting hardware must re-arm the dirty flag so the next entry
    # pre-applies the scope settings even when Setup hasn't changed.
    from unittest.mock import MagicMock
    w = _fresh_window(qapp)
    w._setup_dirty_for_test = False
    stim, scope = MagicMock(), MagicMock()
    scope.info.make, scope.info.model = "Tektronix", "TBS2204B"
    w._on_connected(stim, scope)
    assert w._setup_dirty_for_test is True
    for tab_entry in w._exp_tab_by_code.values():
        tab_entry[0].clear_hardware()  # don't leak mocks into other tests
