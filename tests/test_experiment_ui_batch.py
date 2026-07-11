"""Batch of experiment-UX fixes:

* STOP halts a capture promptly — the scope's blocking waits honour an
  abort-check hook (operator: "When pressing STOP, the program should
  immediately stop when it is safely possible").
* Access voltage + driving voltage are drawn on the live plot.
* The x-axis gets more ticks on a short (~440 µs) pulse window.
* The metrics table word-wraps so multi-value rows aren't clipped.
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


# ---------------------------------------------------------- abort responsiveness
def test_scope_abort_hook_short_circuits_wait(qapp):
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    stim = SimulatedStimulator(); stim.open()
    sc = SimulatedOscilloscope(); sc.bind_stimulator(stim); sc.open()
    # With the abort hook firing, a long wait returns almost immediately.
    sc.set_abort_check(lambda: True)
    t0 = time.monotonic()
    sc.capture_while_running(wait_s=5.0)
    assert time.monotonic() - t0 < 1.0, "abort must short-circuit the wait"
    # Cleared hook → the wait runs its course.
    sc.set_abort_check(lambda: False)
    t0 = time.monotonic()
    sc.capture_while_running(wait_s=0.3)
    assert time.monotonic() - t0 >= 0.25
    stim.close(); sc.close()


def test_runner_installs_abort_hook(qapp):
    # Building any runner wires the scope's abort-check to the runner's
    # aborted flag.
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
    assert sc._should_abort() is False
    runner.abort()
    assert sc._should_abort() is True, \
        "the scope must see the runner's abort flag"
    stim.close(); sc.close()


# ---------------------------------------------------- access / driving markers
def _cap_with_metrics():
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    from stimtest.metrics import compute_metrics
    p = PulsePattern.biphasic(amplitude_ua=80.0)
    c = Capture(index=0, pattern=p)
    t = np.linspace(-100.0, 500.0, 2000)
    c.time_us = t
    c.v_mon_v = np.where((t >= 0) & (t < 200), -0.3,
                         np.where((t >= 200) & (t < 400), 0.25, 0.0))
    c.i_mon_ua = np.where((t >= 0) & (t < 200), -80.0,
                          np.where((t >= 200) & (t < 400), 80.0, 0.0))
    compute_metrics(c, surface_area_um2=5000.0)
    return c


def test_access_and_driving_voltage_markers_on_plot(qapp):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    mcs = MultiChannelScope()
    mcs.add_capture(_cap_with_metrics(), "CH01")
    sc = mcs._pages["CH01"].scope
    texts = [it.toPlainText() for it in getattr(sc, "_marker_items", [])
             if hasattr(it, "toPlainText")]
    # Tags render with HTML subscripts, so the plain-text form drops the
    # underscore (V_a1 -> "Va1", V_d -> "Vd").
    assert any("Va" in x for x in texts), "access-voltage marker missing"
    assert any("Vd" in x for x in texts), "driving-voltage marker missing"
    # Electrode-polarization cursors are now labelled by polarity:
    # Emc (cathodic) / Ema (anodic) instead of the generic "Epol".
    assert any(("Emc" in x or "Ema" in x) for x in texts), \
        "Emc/Ema polarization cursors should remain"


def test_set_markers_explicit_text_form(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    sp.set_traces(np.linspace(-100.0, 500.0, 100),
                  {"Voltage": np.zeros(100)}, {"Voltage": "#E6B800"},
                  {"Voltage": "left"})
    # 4-tuple → custom text; 3-tuple → "label = y V".
    sp.set_markers([("Va1", 50.0, -0.2, "V_a1 = 0.300 V"),
                    ("Epol1", 100.0, 0.1)])
    texts = [it.toPlainText() for it in sp._marker_items
             if hasattr(it, "toPlainText")]
    assert "V_a1 = 0.300 V" in texts
    assert any(t.startswith("Epol1 = ") for t in texts)


# ------------------------------------------------------------ x-tick density
def test_short_pulse_window_gets_more_x_ticks(qapp):
    from stimtest.gui.widgets import _make_matlab_tick_override
    sparse = _make_matlab_tick_override(lambda a, b, c: [], target_count=5)
    xaxis = _make_matlab_tick_override(
        lambda a, b, c: [], target_count=5,
        short_span_target=10, short_span_threshold=2000.0)
    n_sparse = len(sparse(0.0, 748.0, 400)[0][1])    # 440 µs window
    n_x = len(xaxis(0.0, 748.0, 400)[0][1])
    assert n_x > n_sparse, "x-axis must be denser on a short pulse window"
    # Long window stays sparse (operator's earlier 2000 µs preference).
    assert (len(xaxis(0.0, 3400.0, 400)[0][1])
            == len(sparse(0.0, 3400.0, 400)[0][1]))


# ----------------------------------------------------------- table word wrap
def test_metric_table_word_wraps(qapp):
    from stimtest.gui.widgets import MetricTable
    mt = MetricTable()
    assert mt.wordWrap() is True
    # Populating a capture with multi-value rows must not raise and rows
    # are sized to content.
    mt.show_capture(_cap_with_metrics())
    assert mt.rowCount() > 0
