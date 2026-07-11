"""Capture dropdown navigation + post-abort stimulator recovery.

* The per-channel capture nav is a DROPDOWN (operator: "change it to a
  dropdown list") and actually re-renders the selected capture — the old
  ◀/▶ arrows called set_index without a visibility map, so the waveform
  never changed (only the latest capture ever showed).
* A Stop/abort CLOSES the stimulator; the runner's ``preflight`` now
  self-heals by re-opening a closed device so a subsequent run doesn't
  fire DLL calls on a dead handle (operator: "After I aborted the
  experiment, I still get errors about the stimulator, likely because it
  is still closed and not initialized").
"""
from __future__ import annotations

import sys

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _cap(idx: int, amp: float):
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=amp)
    c = Capture(index=idx, pattern=p)
    t = np.linspace(-100.0, 500.0, 400)
    c.time_us = t
    c.v_mon_v = np.where((t >= 0) & (t < 200), -amp / 100.0, 0.0)
    c.i_mon_ua = np.where((t >= 0) & (t < 200), -amp, 0.0)
    return c


# ---------------------------------------------------------- capture dropdown
def test_capture_dropdown_lists_and_renders_each(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    for i, a in enumerate((50.0, 100.0, 150.0)):
        mcs.add_capture(_cap(i, a), "CH01")
    page = mcs._pages["CH01"]
    combo = page._nav_combo
    assert combo.count() == 3, "every capture must appear in the dropdown"
    assert combo.currentIndex() == 2, "auto-follow selects the latest"
    assert not page._nav_row_w.isHidden()

    # The curve key is the (now subscripted) trace label, not the bare
    # "V_mon" — resolve it the same way the widget does.
    from stimtest.gui.multichannel_scope import _subscript_trace_name
    key = _subscript_trace_name("V_mon")
    v_latest = page.scope._curve_data[key][1].copy()
    # Pick the FIRST capture (50 µA) from the dropdown.
    combo.setCurrentIndex(0)
    assert page.current_capture().index == 0
    assert page._auto_follow is False
    v_sel = page.scope._curve_data[key][1]
    assert not np.array_equal(v_latest, v_sel), \
        "selecting an older capture must re-render the waveform"
    assert round(float(np.min(v_sel)), 2) == -0.5   # the 50 µA trace


def test_single_capture_hides_nav_row(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(0, 50.0), "CH01")
    page = mcs._pages["CH01"]
    assert page._nav_row_w.isHidden(), \
        "no dropdown when there's nothing to pick between"


# ---------------------------------------------------- post-abort stim recovery
def _sp_runner():
    from stimtest.experiments.short_pulsing import (
        ShortPulsingExperiment, ShortPulsingPolicy)
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.waveforms import PulsePattern
    stim = SimulatedStimulator(); stim.open()
    sc = SimulatedOscilloscope(); sc.bind_stimulator(stim); sc.open()
    test = TestParameters(experiment="SP",
                          pattern=PulsePattern.biphasic(amplitude_ua=50.0),
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4(), duration_s=1.0)
    runner = ShortPulsingExperiment(
        Session(notebook="t", subject="s", test=test), stim, sc,
        amplitude_ua=50.0, policy=ShortPulsingPolicy(duration_s=1.0))
    return runner, stim, sc


def test_preflight_reopens_a_closed_stim(qapp):
    runner, stim, sc = _sp_runner()
    stim.close()                       # simulate the post-abort closed state
    assert stim.is_open is False
    runner.preflight()                 # must self-heal
    assert stim.is_open is True, \
        "preflight must re-open a stim closed by a prior abort"
    stim.close(); sc.close()


def test_preflight_leaves_open_stim_alone(qapp):
    runner, stim, sc = _sp_runner()
    assert stim.is_open is True
    runner.preflight()                 # no-op re-open
    assert stim.is_open is True
    stim.close(); sc.close()
