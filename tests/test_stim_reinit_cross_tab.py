"""Reinitializing the SHARED stimulator at the start of EVERY run.

Operator lifecycle (CLAUDE.md gotcha #29b, revised): "Do not disconnect
the stimulator and oscilloscope when the experiment ends.  Do reinitialize
the stimulator when starting a new experiment."

So the stim + scope stay CONNECTED between runs (nothing is closed on Stop
or on a normal completion), and each new experiment's Start reinitializes
the STIMULATOR via ``_reinit_stim_for_new_run`` — a fresh ``PS_InitAllStim``
(``stim.open()`` does a single close+init on an already-open device).  The
stim is SHARED across every experiment tab, so ANY tab's Start reinitializes
whatever state a prior run in ANY tab left.
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


def test_start_reinitializes_an_open_device(qapp):
    # Every Start reinitializes the stim, even after a NORMAL run left it
    # open.  The device ends up open + freshly initialized.
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        assert stim.is_open
        opens_before = getattr(stim, "open_count", None)
        did = tab_b._reinit_stim_for_new_run()
        assert did is True
        assert stim.is_open, "reinit ends with the device open + ready"
        if opens_before is not None:
            # A real reinit re-ran open() on the simulator.
            assert stim.open_count > opens_before
    finally:
        stim.close(); scope.close()


def test_start_reopens_a_closed_device(qapp):
    # A device closed by a crash (or a close in another tab) is reopened.
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        stim.close()
        assert not stim.is_open
        did = tab_b._reinit_stim_for_new_run()
        assert did is True
        assert stim.is_open, "tab B's Start must reopen the shared stim"
    finally:
        stim.close(); scope.close()


def test_reinit_clears_legacy_stop_flags(qapp):
    # The legacy per-tab Stop flags (kept for back-compat) are cleared by a
    # reinit, so nothing downstream keys on stale state.
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        tab_b._stim_needs_init = True
        tab_b._stim_needs_close_after_run = True
        assert tab_b._reinit_stim_for_new_run() is True
        assert tab_b._stim_needs_init is False
        assert tab_b._stim_needs_close_after_run is False
        assert stim.is_open
    finally:
        stim.close(); scope.close()


def test_reinit_without_a_stim_object_raises(qapp):
    # Start with no stimulator initialized must raise a clear, actionable
    # error (the caller's wrapper rolls back the UI).
    w, tab_a, tab_b, stim, scope = _two_tabs_sharing_one_stim(qapp)
    try:
        tab_b._stim = None
        with pytest.raises(RuntimeError):
            tab_b._reinit_stim_for_new_run()
    finally:
        stim.close(); scope.close()
