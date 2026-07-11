"""Short-Term Pulsing with several configs must save EVERY channel.

Operator bug: "running monopolar on all channels, but it only captured
CH01."  Each SP configuration runs as its own runner (the rewiring
pauses + Stop/Start stim lifecycle need that), but they all used to get
a fresh ``Session`` written to the SAME ``{stem}.npz`` filename — so the
configs overwrote one another and only one channel survived on disk.

The fix accumulates every queued configuration into ONE growing Session
(like VT) with a constant save filename: the first config creates the
session, later configs repoint ``session.test.configuration`` and the
runner appends its ChannelRun.  These tests pin the invariant at the
``_start_next_pending`` level (deterministic, no runner threads).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _sp_tab_with_sim_hw(qapp):
    from stimtest.gui.main_window import MainWindow
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    w = MainWindow(simulate_default=True)
    tab = w._exp_tab_by_code["SP"][0]
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    w._on_connected(stim, scope)
    return w, tab, stim, scope


def _drive_pending(tab, configs):
    """Run the SP queue through ``_start_next_pending`` deterministically.

    ``_start_runner`` is stubbed to (a) record the session + filename it
    was handed and (b) mimic the runner's ``add_run`` so accumulation is
    observable without spawning a worker thread.
    """
    from stimtest.session import ChannelRun
    seen = []

    def fake_start_runner(runner, save_name):
        cfg = runner.session.test.configuration
        seen.append({
            "session": runner.session,
            "save_name": save_name,
            "config": cfg.display_name(),
            "n_runs_before": len(runner.session.runs),
        })
        runner.session.add_run(
            ChannelRun(configuration=cfg, surface_area_um2=5000.0))

    tab._start_runner = fake_start_runner
    tab._pre_run_warning_check = lambda *a, **k: True
    tab._pending_configs = list(configs)
    tab._sp_session = None
    tab._sp_save_name = None
    tab._export_carryover_ids = set()
    # The real chain hops via _on_finished; drive it directly here.
    while tab._pending_configs:
        tab._start_next_pending()
    return seen


def test_sp_configs_share_one_growing_session(qapp):
    from stimtest.electrode import Configuration
    w, tab, stim, scope = _sp_tab_with_sim_hw(qapp)
    try:
        cfgs = [Configuration.monopolar(1), Configuration.monopolar(2),
                Configuration.monopolar(3)]
        seen = _drive_pending(tab, cfgs)

        assert len(seen) == 3
        # Same Session object handed to every config.
        s0 = seen[0]["session"]
        assert all(s["session"] is s0 for s in seen), \
            "all SP configs must share ONE session"
        # Each config repointed the live configuration.
        assert [s["config"] for s in seen] == ["CH01", "CH02", "CH03"]
        # Runs accumulated 0→1→2 as each config started (then its own
        # add_run brings it to 1→2→3).
        assert [s["n_runs_before"] for s in seen] == [0, 1, 2]
        assert len(s0.runs) == 3
        assert [r.configuration.display_name() for r in s0.runs] == \
            ["CH01", "CH02", "CH03"]
    finally:
        stim.close(); scope.close()


def test_sp_save_filename_constant_across_configs(qapp):
    from stimtest.electrode import Configuration
    w, tab, stim, scope = _sp_tab_with_sim_hw(qapp)
    try:
        seen = _drive_pending(
            tab, [Configuration.monopolar(c) for c in (1, 2, 3, 4)])
        names = {s["save_name"] for s in seen}
        assert len(names) == 1, \
            f"every config must save to ONE filename, got {names}"
    finally:
        stim.close(); scope.close()
