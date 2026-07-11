"""dE/dt + 1/(dE/dt) as toggleable NORMALIZED-overlay traces (operator: "Have
that derivative and reciprocal of derivative as option traces … normalized
overlay … trace options in a column").

The two derivative traces join the experiment-plot trace-toggle column; off by
default (N/A), each renders as a dashed overlay scaled to the V_mon amplitude
(shape only — their native V/µs and huge-reciprocal scales don't fit the V/I
axes) when set to an axis.
"""
from __future__ import annotations

import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

import pytest

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets

from stimtest.gui.multichannel_scope import (
    MultiChannelScope, TRACE_DEDT, TRACE_RECIP_DEDT, ALL_TOGGLE_TRACES,
    ALL_DERIV_TRACES)

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _cap(idx=0, amp=50.0):
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=amp)
    c = Capture(index=idx, pattern=p)
    t = np.linspace(-100.0, 500.0, 1200)
    c.time_us = t
    v = np.zeros_like(t)
    m = (t >= 0) & (t < 200)
    v[m] = -0.2 * (t[m] / 200.0)        # curved cathodic ramp → non-trivial dV/dt
    c.v_mon_v = v
    c.i_mon_ua = np.where(m, -amp, 0.0)
    return c


def _scope_curve_keys(mcs):
    keys = set()
    for pg in mcs._pages.values():
        sc = getattr(pg, "scope", None)
        if sc is not None:
            keys |= set(getattr(sc, "_curve_data", {}).keys())
    return keys


def test_derivative_traces_are_toggle_options():
    assert ALL_DERIV_TRACES == (TRACE_DEDT, TRACE_RECIP_DEDT)
    assert TRACE_DEDT in ALL_TOGGLE_TRACES
    assert TRACE_RECIP_DEDT in ALL_TOGGLE_TRACES


def test_derivative_rows_exist_and_default_off():
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(), "CH01")
    assert TRACE_DEDT in mcs.axis_combos
    assert TRACE_RECIP_DEDT in mcs.axis_combos
    vis = mcs.visibility()
    assert vis[TRACE_DEDT] is False        # off by default (N/A)
    assert vis[TRACE_RECIP_DEDT] is False


def test_dedt_overlay_renders_and_is_normalized():
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(), "CH01")
    mcs.axis_combos[TRACE_DEDT].setCurrentIndex(1)     # Left y-axis = on
    _app.processEvents()
    assert mcs.visibility()[TRACE_DEDT] is True
    keys = _scope_curve_keys(mcs)
    dedt_key = next((k for k in keys if "dV/dt" in k and "1/" not in k), None)
    assert dedt_key is not None, keys
    # NORMALIZED: scaled to ~the V_mon amplitude (0.2 V), NOT the raw V/µs slope.
    for pg in mcs._pages.values():
        d = getattr(pg.scope, "_curve_data", {}).get(dedt_key)
        if d is not None:
            y = np.asarray(d[1], dtype=float)
            assert np.nanmax(np.abs(y)) <= 0.30
    mcs.axis_combos[TRACE_DEDT].setCurrentIndex(0)     # N/A → removed
    _app.processEvents()
    assert mcs.visibility()[TRACE_DEDT] is False


def test_reciprocal_overlay_renders():
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(), "CH01")
    mcs.axis_combos[TRACE_RECIP_DEDT].setCurrentIndex(1)
    _app.processEvents()
    assert mcs.visibility()[TRACE_RECIP_DEDT] is True
    assert any("1/(dV/dt)" in k for k in _scope_curve_keys(mcs))


# --------------------------------------------------- POLARIS (matplotlib)
def test_polaris_plot_capture_deriv_overlays():
    """plot_capture draws the derivative overlays when requested (POLARIS side —
    operator chose 'Both' plots)."""
    import matplotlib
    matplotlib.use("Agg")
    from stimtest import plotting
    from stimtest.session import ChannelRun, Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.waveforms import PulsePattern
    cap = _cap()
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.surface_area_um2 = 2000.0
    run.captures.append(cap)
    test = TestParameters(experiment="VT", pattern=PulsePattern.biphasic(50.0),
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)

    def _labels(fig):
        return [ln.get_label() for ax in fig.axes for ln in ax.get_lines()]

    fig0 = plotting.plot_capture(cap, run, session, deriv_overlays=None)
    assert not any("d$V$/d$t$" in l for l in _labels(fig0))   # off by default
    fig1 = plotting.plot_capture(cap, run, session,
                                 deriv_overlays={"dvdt", "recip"})
    labs = _labels(fig1)
    assert any("d$V$/d$t$ (norm.)" in l for l in labs)
    assert any("1/(d$V$/d$t$) (norm.)" in l for l in labs)


def test_polaris_view_bar_deriv_overlays_query():
    from stimtest.gui.viewer import ViewerPanel
    p = ViewerPanel()
    assert p.view_bar.deriv_overlays() == set()
    p.view_bar.dvdt_check.setChecked(True)
    p.view_bar.recip_check.setChecked(True)
    assert p.view_bar.deriv_overlays() == {"dvdt", "recip"}
    # prefs round-trip
    pr = p.view_bar.prefs()
    assert pr["dvdt"] is True and pr["recip"] is True
    p.view_bar.dvdt_check.setChecked(False)
    p.view_bar.restore(pr)
    assert p.view_bar.deriv_overlays() == {"dvdt", "recip"}
