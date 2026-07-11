"""Exported plot includes the CALCULATED (derived) E_act.

Operator: "When exporting the plot, include the calculated Eact, if
available."

When the active electrode wasn't digitised directly but E_ret was, the live
plot derives E_act = V_mon + E_ret (the differential identity V_mon = E_act −
E_ret).  The exported matplotlib figure now does the same, labelling it
"Active potential (calculated)" so the saved figure is self-documenting.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.plotting import plot_capture
from stimtest.session import Capture, ChannelRun, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _cap(e_act=None, e_ret=None, n=400):
    t = np.linspace(-50.0, 250.0, n)
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    i = 50.0 * np.sin(np.linspace(0, 6, n))
    return Capture(index=0,
                   pattern=PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1),
                   time_us=t, v_mon_v=v, i_mon_ua=i, e_act_v=e_act, e_ret_v=e_ret)


def _labels(cap):
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    run = ChannelRun(configuration=Configuration.monopolar(1))
    sess = Session(notebook="n", subject="s", test=test)
    fig = plot_capture(cap, run, sess)
    out = []
    for ax in fig.axes:
        for ln in ax.get_lines():
            lb = ln.get_label()
            if lb and not lb.startswith("_"):
                out.append(lb)
    return out


def test_derived_eact_plotted_when_only_eret_recorded():
    n = 400
    eret = 0.02 * np.sin(np.linspace(0, 6, n))
    labels = _labels(_cap(e_act=None, e_ret=eret, n=n))
    assert any("calculated" in x for x in labels), labels
    assert "Voltage monitor" in labels


def test_recorded_eact_is_not_labelled_calculated():
    n = 400
    eret = 0.02 * np.sin(np.linspace(0, 6, n))
    eact = 0.05 * np.sin(np.linspace(0, 6, n))
    labels = _labels(_cap(e_act=eact, e_ret=eret, n=n))
    assert "Active potential" in labels
    assert not any("calculated" in x for x in labels), labels


def test_no_active_trace_without_eret():
    labels = _labels(_cap(e_act=None, e_ret=None))
    assert not any("Active" in x for x in labels), labels


def test_derived_eact_equals_vmon_plus_eret():
    """The derived trace is the pure identity V_mon + E_ret (no subtraction)."""
    n = 300
    cap = _cap(e_act=None, e_ret=0.03 * np.ones(n), n=n)
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    run = ChannelRun(configuration=Configuration.monopolar(1))
    sess = Session(notebook="n", subject="s", test=test)
    fig = plot_capture(cap, run, sess)
    want = cap.v_mon_v + cap.e_ret_v
    found = False
    for ax in fig.axes:
        for ln in ax.get_lines():
            if "calculated" in (ln.get_label() or ""):
                assert np.allclose(ln.get_ydata(), want)
                found = True
    assert found
