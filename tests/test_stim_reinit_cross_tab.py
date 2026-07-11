"""Re-initializing a SHARED stimulator that another tab closed.

Operator: "I am having issues with wanting to run a different experiment
after I aborted one.  It seems that the stimulator stays closed and won't
let another experiment run."

Root cause: the GUI keyed the Start-time re-init on a PER-TAB
``_stim_needs_init`` flag, but the stimulator object is SHARED across all
experiment tabs.  Aborting in tab A closed the shared stim and set tab
A's flag; tab B's flag stayed False, so tab B's Start skipped re-init and
ran on a closed device.  The fix keys re-init on the device's own
``is_open`` state (``_reinit_stim_if_closed``), so ANY tab's Start
re-opens a device any other tab closed.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _two_tabs_sharing_one_stim(qapp):
    from stimtest.gui.main_window import MainWindow
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    w = MainWindow(simulate_default=True)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    w._on_connected(stim, scope)
    # Grab two DIFFERENT experiment tabs that share the one stim.
    tab_a = w._exp_tab_by_code["SP"][0]
    tab_b = w._exp_tab_by_code["VT"][0]
    assert tab_a._stim is stim and tab_b._stim is stim
    return w, tab_a, tab_b, stim, scope


def test_other_tab_reopens_a_stim_closed_by_first_tab(qapp):
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        # Tab A aborts → its _on_finished closes the SHARED stim and sets
        # ONLY tab A's flag.  Emulate that end state:
        stim.close()
        tab_a._stim_needs_init = True
        # Tab B never knew — its flag is the default False.
        tab_b._stim_needs_init = False
        assert not stim.is_open

        # Tab B's Start path must re-open the shared device anyway.
        did = tab_b._reinit_stim_if_closed()
        assert did is True
        assert stim.is_open, "tab B's Start must re-open the shared stim"
    finally:
        stim.close(); scope.close()


def test_no_reinit_on_normal_back_to_back_run(qapp):
    # A NORMAL end-of-run sets NEITHER Stop-flag, so the next Start on an
    # already-open device must NOT re-open it (avoids the
    # ps_close_all_stim cascade, gotcha #29c).
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        assert stim.is_open
        tab_b._stim_needs_init = False
        tab_b._stim_needs_close_after_run = False
        did = tab_b._reinit_stim_if_closed()
        assert did is False
        assert stim.is_open
        assert tab_b._stim_needs_init is False
    finally:
        stim.close(); scope.close()


def test_stop_forces_reinit_even_when_device_reports_open(qapp):
    """Operator: "Reinitialize the stimulator when pressing Start if the
    experiment has been Stopped."  Even if the device still reports OPEN
    (the Stop's deferred close raced Start or failed silently), a set
    Stop-flag must force a clean close+open."""
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        assert stim.is_open
        # Emulate Stop pressed but the close not yet reflected in is_open.
        tab_b._stim_needs_close_after_run = True
        did = tab_b._reinit_stim_if_closed()
        assert did is True, "a Stop must force reinit even when is_open"
        assert stim.is_open, "reinit ends with the device open + ready"
        # Both Stop-flags cleared so the NEXT (normal) Start won't reinit.
        assert tab_b._stim_needs_close_after_run is False
        assert tab_b._stim_needs_init is False
    finally:
        stim.close(); scope.close()


def test_stop_flag_via_needs_init_also_forces_reinit(qapp):
    # The other Stop signal: _on_finished sets _stim_needs_init=True after
    # closing on Stop.  If close left the device reporting open, that flag
    # alone must still force the reinit.
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        assert stim.is_open
        tab_b._stim_needs_init = True
        assert tab_b._reinit_stim_if_closed() is True
        assert stim.is_open
        assert tab_b._stim_needs_init is False
    finally:
        stim.close(); scope.close()


def test_closed_device_reopens_even_without_the_flag(qapp):
    # The is_open state — not the flag — drives the decision.
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        stim.close()
        tab_b._stim_needs_init = False          # flag says "fine"
        assert not stim.is_open                 # but device says "closed"
        assert tab_b._reinit_stim_if_closed() is True
        assert stim.is_open
    finally:
        stim.close(); scope.close()
