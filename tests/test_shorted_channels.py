"""Shorted-channel current boosting (operator: "indicate that channels will be
shorted together to apply more than 1 mA … after a channel reaches its 1 mA
limit, start using the other shorted channel … when the shorted channel is
unused, apply 0 µA that matches the stimulation pulse width").

N stimulator channels physically paralleled onto one electrode → the TOTAL
current can reach N × the 1 mA per-channel rail, distributed by SEQUENTIAL FILL
(fill the primary to 1 mA, then the next, …).  Each channel's loaded pattern
stays ≤ 1 mA; an unengaged channel gets a same-timing 0 µA pattern.
"""
from __future__ import annotations

import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment, distribute_sequential)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


# ---- sequential distribution ------------------------------------------------

def test_distribute_sequential_fills_one_channel_before_the_next():
    assert distribute_sequential(1500, 2) == [1000.0, 500.0]
    assert distribute_sequential(2500, 3) == [1000.0, 1000.0, 500.0]
    assert distribute_sequential(2000, 2) == [1000.0, 1000.0]      # both maxed
    assert distribute_sequential(500, 2) == [500.0, 0.0]           # 2nd unused
    assert distribute_sequential(0, 3) == [0.0, 0.0, 0.0]          # baseline
    # asymmetric per-channel cap (largest phase ≤ 1 mA)
    assert distribute_sequential(1200, 3, 500.0) == [500.0, 500.0, 200.0]


# ---- runner plumbing --------------------------------------------------------

def _runner(shorted, max_ua):
    pat = PulsePattern.biphasic(amplitude_ua=50.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    return VoltageTransientExperiment(
        Session(notebook="t", subject="s", test=test), stim, scope,
        ramp=RampPolicy(strategy="adaptive", max_ua=max_ua),
        cathodic_limit_v=-0.6, anodic_limit_v=0.8,
        shorted_channels=shorted)


def test_effective_rail_and_ramp_max_scale_with_shorted_count():
    r = _runner([1, 2, 3], max_ua=3000.0)
    assert r._shorted_n == 3
    assert r._effective_rail_ua == 3000.0
    assert r.ramp.max_ua == 3000.0           # caller passed N × rail; clamp allows it


def test_ramp_clamp_still_caps_at_effective_rail():
    # a caller asking for MORE than N × rail is clamped down to it.
    r = _runner([1, 2], max_ua=5000.0)
    assert r.ramp.max_ua == 2000.0           # 2 × 1000


def test_no_shorting_keeps_the_1ma_rail():
    r = _runner(None, max_ua=1000.0)
    assert r._shorted_n == 1
    assert r._effective_rail_ua == 1000.0
    assert r.ramp.max_ua == 1000.0


class _SpyStim:
    """Records (channel, excitation_µA, phase_widths) per load_channel."""
    def __init__(self):
        self.loads = []
    def load_channel(self, ch, pat):
        self.loads.append((ch, abs(pat.excitation_phase.amplitude_ua),
                           tuple(round(p.width_us, 3) for p in pat.phases)))
    def set_repetitions(self, ch, n):
        pass


def test_load_active_or_shorted_splits_sequentially():
    r = _runner([1, 2, 3], max_ua=3000.0)
    base = r.session.test.pattern
    logical = r._pattern_at_amplitude(base, 2500.0)   # total 2.5 mA
    r.stim = _SpyStim()
    group = r._load_active_or_shorted(logical, Configuration.monopolar(1))
    assert group == (1, 2, 3)                          # active first, then shorted
    amps = [(ch, round(a)) for ch, a, _w in r.stim.loads]
    assert amps == [(1, 1000), (2, 1000), (3, 500)]    # sequential fill


def test_unengaged_shorted_channel_gets_zero_with_matching_timing():
    r = _runner([1, 2, 3], max_ua=3000.0)
    base = r.session.test.pattern
    logical = r._pattern_at_amplitude(base, 1200.0)    # total 1.2 mA → [1000,200,0]
    widths = tuple(round(p.width_us, 3) for p in logical.phases)
    r.stim = _SpyStim()
    r._load_active_or_shorted(logical, Configuration.monopolar(1))
    by_ch = {ch: (a, w) for ch, a, w in r.stim.loads}
    assert round(by_ch[1][0]) == 1000
    assert round(by_ch[2][0]) == 200
    assert round(by_ch[3][0]) == 0                     # 3rd channel unused → 0 µA
    # …but its TIMING matches the pulse (same phase widths) so it ticks in cadence
    assert by_ch[3][1] == widths


def test_single_channel_path_unchanged_when_not_shorted():
    r = _runner(None, max_ua=1000.0)
    base = r.session.test.pattern
    r.stim = _SpyStim()
    group = r._load_active_or_shorted(base, Configuration.monopolar(1))
    assert group == (1,)
    assert [ch for ch, _a, _w in r.stim.loads] == [1]


# ---- GUI: control + wiring --------------------------------------------------

def _vt_tab():
    from PyQt6 import QtWidgets
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    tab = w._exp_tab_by_code["VT"][0]
    # HERMETIC: the "pulsar-pytest" prefs file is SHARED across every
    # MainWindow test (gotcha #100), and close() persists tab state — so a
    # prior test that left shorting ON would make this MainWindow restore it.
    # Establish a known unshorted baseline explicitly rather than trusting the
    # restored default.
    _reset_shorting(tab)
    return app, w, tab


def _reset_shorting(tab):
    tab.shorted_check.setChecked(False)
    tab.shorted_channels_edit.setText("")
    # setChecked(False) is a no-op (no `toggled`) when already unchecked, so
    # force the enabled-state / max-label refresh regardless.
    tab._on_shorted_changed()


def _close(w, tab):
    # Never leave shorting ON in the shared prefs file — close() would persist
    # it and break the next MainWindow test's default-state assumptions.
    _reset_shorting(tab)
    w.close()


def test_shorted_control_parses_and_updates_max_label():
    app, w, tab = _vt_tab()
    try:
        # Disabled + 1 mA by default.
        assert not tab.shorted_channels_edit.isEnabled()
        assert tab._shorted_channels_list() == []
        tab.shorted_check.setChecked(True)
        assert tab.shorted_channels_edit.isEnabled()
        tab.shorted_channels_edit.setText("1, 2, 3")
        assert tab._shorted_channels_list() == [1, 2, 3]
        assert "3.0 mA" in tab.shorted_max_label.text()
        # dedupe + out-of-range are dropped, order preserved.
        tab.shorted_channels_edit.setText("2, 2, 17, 4")
        assert tab._shorted_channels_list() == [2, 4]
        assert "2.0 mA" in tab.shorted_max_label.text()
        # Unchecking hides the list from the parser.
        tab.shorted_check.setChecked(False)
        assert tab._shorted_channels_list() == []
    finally:
        _close(w, tab)


def test_shorted_boosts_ramp_ceiling_in_max_mode():
    app, w, tab = _vt_tab()
    try:
        tab.mode_combo.setCurrentText(tab.MODE_MAX)
        tab.strategy_combo.setCurrentText(tab.STRAT_ADAPT)
        tab.max_ua.setValue(1000.0)
        # No shorting (hermetic baseline) → the ceiling is the user max.
        assert tab._build_ramp_policy(50.0).max_ua == 1000.0
        # Short 3 channels → ceiling boosted to 3 mA.
        tab.shorted_check.setChecked(True)
        tab.shorted_channels_edit.setText("1, 2, 3")
        assert tab._build_ramp_policy(50.0).max_ua == 3000.0
    finally:
        _close(w, tab)


def test_shorted_does_not_boost_fixed_single_shot():
    app, w, tab = _vt_tab()
    try:
        tab.mode_combo.setCurrentText(tab.MODE_FIXED_QPH)
        tab.shorted_check.setChecked(True)
        tab.shorted_channels_edit.setText("1, 2, 3")
        # A fixed single shot keeps its amplitude-derived ceiling (no ramp).
        assert tab._build_ramp_policy(50.0).max_ua == 50.0
    finally:
        _close(w, tab)


def test_shorted_prefs_round_trip():
    app, w, tab = _vt_tab()
    try:
        tab.shorted_check.setChecked(True)
        tab.shorted_channels_edit.setText("1, 4, 7")
        p = tab.current_prefs()
        assert p["shorted_check"] is True
        assert p["shorted_channels_edit"] == "1, 4, 7"
        tab.shorted_check.setChecked(False)
        tab.shorted_channels_edit.setText("")
        tab.restore_prefs(p)
        assert tab.shorted_check.isChecked()
        assert tab._shorted_channels_list() == [1, 4, 7]
    finally:
        _close(w, tab)
