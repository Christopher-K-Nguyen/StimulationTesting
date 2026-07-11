"""Two operator requests:

1. While an experiment runs, the operator must still be able to scroll
   through / read the Setup and Test-parameters tabs.  The run-lock now
   disables only the INPUT content, leaving the tabs + their scroll areas
   enabled (operator: "allow for scrolling through the other tabs … Setup
   and Test Parameters").

2. Short Pulsing runs the SELECTED channel/combo only (single-config —
   operator: "do not capture all channels, just the selected channel"),
   and takes one EXTRA capture when the run ends (operator: "When SP ends,
   add another capture").
"""
from __future__ import annotations

import sys

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _main_window(qapp):
    from stimtest.gui.main_window import MainWindow
    return MainWindow(simulate_default=True)


# ---- 1. run-lock leaves Setup / Test-params scrollable -----------------
def test_setup_run_lock_keeps_tab_enabled_but_disables_inputs(qapp):
    w = _main_window(qapp)
    st = w.setup_tab
    st.set_run_locked(True)
    assert st.isEnabled()                                 # scroll area stays live
    assert st._run_lock_content.isEnabled() is False      # inputs locked
    st.set_run_locked(False)
    assert st._run_lock_content.isEnabled() is True


def test_params_run_lock_keeps_page_enabled_but_disables_inputs(qapp):
    w = _main_window(qapp)
    tab = w._exp_tab_by_code["VT"][0]
    tab._set_locked(True)
    assert tab.params_page.isEnabled()                    # page stays scrollable
    for inner in tab._params_run_lock_content:
        assert inner.isEnabled() is False                 # inputs locked
    tab._set_locked(False)
    for inner in tab._params_run_lock_content:
        assert inner.isEnabled() is True


def test_on_run_state_changed_keeps_setup_scrollable(qapp):
    w = _main_window(qapp)
    w._on_run_state_changed(True)
    assert w.setup_tab.isEnabled()                        # not greyed wholesale
    assert w.setup_tab._run_lock_content.isEnabled() is False
    w._on_run_state_changed(False)
    assert w.setup_tab._run_lock_content.isEnabled() is True


# ---- 2a. SP stays single-config (selected channel only) ----------------
def test_sp_and_cp_are_single_config(qapp):
    from stimtest.gui.experiment_tabs import (ShortPulsingTab,
                                              ContinuousPulsingTab,
                                              VoltageTransientTab)
    assert ShortPulsingTab.SINGLE_CONFIG is True          # selected channel only
    assert ContinuousPulsingTab.SINGLE_CONFIG is True
    assert VoltageTransientTab.SINGLE_CONFIG is False     # VT still does all


def test_sp_combo_panel_is_single_select(qapp):
    w = _main_window(qapp)
    sp = w._exp_tab_by_code["SP"][0]
    assert sp.combo_panel._static_single_mode is True


# ---- 2b. SP adds a final capture at the end of the run -----------------
def test_sp_adds_final_capture_at_end():
    from stimtest.experiments.short_pulsing import (
        ShortPulsingExperiment, ShortPulsingPolicy)
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray

    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    pat = PulsePattern.biphasic(amplitude_ua=100.0)
    sess = Session(notebook="nb", subject="subj",
        test=TestParameters(experiment="SP", duration_s=0.05,
            polarization_method="MP", counter_electrode_label="Pt",
            reference_electrode_label="", target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1), pattern=pat,
            array=ElectrodeArray.utah_4x4()))
    # Long cadence vs a short duration → the loop takes only the OPENING
    # snapshot; the END capture is the operator-requested extra.
    runner = ShortPulsingExperiment(
        sess, stim, scope, amplitude_ua=100.0,
        policy=ShortPulsingPolicy(capture_interval_s=60.0, duration_s=0.05))
    result = runner.run()
    assert result.aborted is False
    caps = result.session.runs[0].captures
    # One opening snapshot + one final capture = at least 2.
    assert len(caps) >= 2
    # Capture indices are contiguous (the final continues the numbering).
    assert [c.index for c in caps] == list(range(len(caps)))


def test_sp_abort_skips_final_capture():
    """An aborted SP run must NOT take the end capture (the operator asked
    it to stop)."""
    from stimtest.experiments.short_pulsing import (
        ShortPulsingExperiment, ShortPulsingPolicy)
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray

    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    pat = PulsePattern.biphasic(amplitude_ua=100.0)
    sess = Session(notebook="nb", subject="subj",
        test=TestParameters(experiment="SP", duration_s=5.0,
            polarization_method="MP", counter_electrode_label="Pt",
            reference_electrode_label="", target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1), pattern=pat,
            array=ElectrodeArray.utah_4x4()))
    runner = ShortPulsingExperiment(
        sess, stim, scope, amplitude_ua=100.0,
        policy=ShortPulsingPolicy(capture_interval_s=60.0, duration_s=5.0))
    runner.abort()                              # simulate Stop before the loop
    result = runner.run()
    # Aborted before any cadence capture → no captures, and crucially no
    # final capture was forced.
    assert result.aborted is True
    assert len(result.session.runs[0].captures) == 0
