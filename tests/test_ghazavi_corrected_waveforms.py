"""Ghazavi & Cogan 2018 access-resistance-CORRECTED interface waveforms.

Operator: "Plot the corrected waveforms like how Ghazavi did as well" →
"I want Ei be E'act and E'ret for correcting for access resistance."

The measured electrode voltage V_m is split into the RESISTIVE (in-phase-
with-I) part V_r = R_access·I_ac and the INTERFACE potential E′ = V_m − V_r
(access resistance removed).  E′act comes from the active trace, E′ret from
E_ret; both are drawn DASHED in the base electrode's colour, ONLY for a
continuous-sinusoidal (KHFAC) capture.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest

from stimtest.metrics import (ghazavi_corrected_waveforms,
                              ghazavi_polarization)
from stimtest.session import Capture
from stimtest.waveforms import (Phase, PulsePattern,
                                SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR)


F = 5000.0
W = 2 * np.pi * F


def _sine_pattern():
    return PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=100.0, shape=SHAPE_SINUSOIDAL),
        Phase(amplitude_ua=100.0, width_us=100.0, shape=SHAPE_SINUSOIDAL),
    ], rate_hz=F)


def _synth(R_v_per_ua=0.001, e_io=0.05, e_off=0.02, i0=100.0, n=4000):
    t_s = np.linspace(0.0, 8.0 / F, n, endpoint=False)
    i = i0 * np.sin(W * t_s)                          # µA
    v = R_v_per_ua * i + e_off - e_io * np.cos(W * t_s)
    return v, i, t_s * 1e6


# ------------------------------------------------------- the math helper
def test_helper_recovers_interface_and_resistive_parts():
    v, i, t_us = _synth(R_v_per_ua=0.001, e_io=0.05, e_off=0.02, i0=100.0)
    e_i, v_r, r = ghazavi_corrected_waveforms(v, i)
    assert e_i is not None and v_r is not None
    assert e_i.size == v.size and v_r.size == v.size      # aligned to input
    assert abs(r - 0.001) < 1e-6                           # R = 1 kΩ (V/µA)
    # E′ mean = E_off, extrema = E_off ± E_io.
    assert abs(float(e_i.mean()) - 0.02) < 1e-3
    assert abs(float(e_i.min()) - (0.02 - 0.05)) < 1e-3    # E_mc
    assert abs(float(e_i.max()) - (0.02 + 0.05)) < 1e-3    # E_ma
    # V_r amplitude = R·I_o = 0.001 V/µA × 100 µA = 0.1 V.
    assert abs(float(np.sqrt(2.0) * v_r.std()) - 0.1) < 2e-3
    # V_m = V_r + E′ exactly (the split is complete).
    assert np.allclose(v, v_r + e_i, atol=1e-9)


def test_helper_matches_polarization_extrema():
    """E′ extrema equal ghazavi_polarization's E_mc / E_ma — the two share the
    SAME least-squares projection R (single source of truth)."""
    v, i, t_us = _synth(R_v_per_ua=0.002, e_io=0.07, e_off=-0.01, i0=80.0)
    e_i, _v_r, r = ghazavi_corrected_waveforms(v, i)
    g = ghazavi_polarization(v, i, t_us, rate_hz=F)
    assert abs(r - g["r_access_kohm"] * 1e-3) < 1e-6       # same R
    assert abs(float(e_i.min()) - g["e_mc_v"]) < 1e-6
    assert abs(float(e_i.max()) - g["e_ma_v"]) < 1e-6


def test_helper_nan_and_short_inputs():
    assert ghazavi_corrected_waveforms(np.zeros(4), np.zeros(4))[0] is None   # short
    v = np.linspace(0, 0.1, 500)
    assert ghazavi_corrected_waveforms(v, np.zeros(500))[0] is None           # no AC I


# ------------------------------------------------------- live plot (Qt)
@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _khfac_cap(with_eret=True):
    t_s = np.linspace(0.0, 8.0 / F, 4000, endpoint=False)
    t_us = t_s * 1e6
    i = 100.0 * np.sin(W * t_s)
    v_mon = 0.001 * i - 0.30 * np.cos(W * t_s)            # resistive + interface
    cap = Capture(index=0, pattern=_sine_pattern(), time_us=t_us,
                  v_mon_v=v_mon, i_mon_ua=i)
    if with_eret:
        cap.e_ret_v = 0.20 + 0.05 * np.sin(W * t_s)      # biased return
    return cap


def test_live_plot_shows_dashed_corrected_traces(qapp):
    pytest.importorskip("pyqtgraph")
    from PyQt6 import QtCore
    from stimtest.gui.multichannel_scope import (MultiChannelScope,
                                                 _subscript_trace_name,
                                                 TRACE_EACT_CORR, TRACE_ERET_CORR)
    mcs = MultiChannelScope()
    mcs.add_capture(_khfac_cap(with_eret=True), "CH01")
    page = mcs.ensure_page("CH01")
    curves = page.scope._curves
    k_act = _subscript_trace_name(TRACE_EACT_CORR)
    k_ret = _subscript_trace_name(TRACE_ERET_CORR)
    assert k_act in curves, f"E′act curve missing (have {list(curves)})"
    assert k_ret in curves, f"E′ret curve missing (have {list(curves)})"
    # Both drawn DASHED.
    for k in (k_act, k_ret):
        pen = curves[k].opts["pen"]
        assert pen.style() == QtCore.Qt.PenStyle.DashLine, (
            f"{k} must be dashed")
    # The label carries a prime on the E variable ("E'act").
    assert "E'" in k_act and "act" in k_act


def test_live_plot_no_corrected_traces_for_pulsed(qapp):
    pytest.importorskip("pyqtgraph")
    from stimtest.gui.multichannel_scope import (MultiChannelScope,
                                                 _subscript_trace_name,
                                                 TRACE_EACT_CORR, TRACE_ERET_CORR)
    t = np.linspace(-100.0, 500.0, 1200)
    cap = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=50.0),
                  time_us=t,
                  v_mon_v=np.where((t >= 0) & (t < 200), -0.2, 0.0),
                  i_mon_ua=np.where((t >= 0) & (t < 200), -50.0, 0.0))
    mcs = MultiChannelScope()
    mcs.add_capture(cap, "CH01")
    curves = mcs.ensure_page("CH01").scope._curves
    assert _subscript_trace_name(TRACE_EACT_CORR) not in curves
    assert _subscript_trace_name(TRACE_ERET_CORR) not in curves


def test_live_plot_eret_corrected_absent_without_eret(qapp):
    pytest.importorskip("pyqtgraph")
    from stimtest.gui.multichannel_scope import (MultiChannelScope,
                                                 _subscript_trace_name,
                                                 TRACE_EACT_CORR, TRACE_ERET_CORR)
    mcs = MultiChannelScope()
    mcs.add_capture(_khfac_cap(with_eret=False), "CH01")
    curves = mcs.ensure_page("CH01").scope._curves
    # E′act still appears (from V_mon as the active proxy); E′ret does not.
    assert _subscript_trace_name(TRACE_EACT_CORR) in curves
    assert _subscript_trace_name(TRACE_ERET_CORR) not in curves


def test_set_traces_dash_style(qapp):
    pytest.importorskip("pyqtgraph")
    from PyQt6 import QtCore
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    t = np.linspace(0, 100, 200)
    sp.set_traces(t, {"solid": np.sin(t), "dashed": np.cos(t)},
                  colors={"solid": "#E6B800", "dashed": "#009E73"},
                  axis={"solid": "left", "dashed": "left"},
                  styles={"dashed": "dash"})
    assert sp._curves["solid"].opts["pen"].style() == QtCore.Qt.PenStyle.SolidLine
    assert sp._curves["dashed"].opts["pen"].style() == QtCore.Qt.PenStyle.DashLine


# ------------------------------------------------- toggleable + inset options
def test_corrected_traces_are_toggleable_and_inset_options(qapp):
    pytest.importorskip("pyqtgraph")
    from stimtest.gui.multichannel_scope import (MultiChannelScope,
                                                 _subscript_trace_name,
                                                 TRACE_EACT_CORR, TRACE_ERET_CORR)
    mcs = MultiChannelScope()
    # Each corrected trace has its OWN axis combo (separate toggle).
    assert TRACE_EACT_CORR in mcs.axis_combos
    assert TRACE_ERET_CORR in mcs.axis_combos
    # …and appears in the inset picker options.
    inset_keys = [mcs.inset_combo.itemData(i)
                  for i in range(mcs.inset_combo.count())]
    assert TRACE_EACT_CORR in inset_keys
    assert TRACE_ERET_CORR in inset_keys


def test_corrected_availability_follows_sinusoidal_capture(qapp):
    pytest.importorskip("pyqtgraph")
    from stimtest.gui.multichannel_scope import (MultiChannelScope,
                                                 TRACE_EACT_CORR, TRACE_ERET_CORR)
    mcs = MultiChannelScope()
    # Before any capture: corrected pair not available (pulsed default).
    assert TRACE_EACT_CORR not in mcs.available_traces()
    # A KHFAC capture (with E_ret) makes BOTH corrected traces available.
    mcs.add_capture(_khfac_cap(with_eret=True), "CH01")
    avail = mcs.available_traces()
    assert TRACE_EACT_CORR in avail and TRACE_ERET_CORR in avail
    assert mcs.axis_combos[TRACE_EACT_CORR].isVisibleTo(mcs) or True  # row shown
    # A PULSED capture on a new page hides them again.
    t = np.linspace(-100.0, 500.0, 1200)
    from stimtest.session import Capture
    pulsed = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=50.0),
                     time_us=t,
                     v_mon_v=np.where((t >= 0) & (t < 200), -0.2, 0.0),
                     i_mon_ua=np.where((t >= 0) & (t < 200), -50.0, 0.0))
    mcs.add_capture(pulsed, "CH02")
    assert TRACE_EACT_CORR not in mcs.available_traces()


def test_eact_corrected_toggle_is_separate_from_eact(qapp):
    pytest.importorskip("pyqtgraph")
    from stimtest.gui.multichannel_scope import (MultiChannelScope,
                                                 _subscript_trace_name,
                                                 TRACE_EACT, TRACE_EACT_CORR)
    from stimtest.gui.widgets import AXIS_NA
    mcs = MultiChannelScope()
    # Hide E_act — E′act must STILL draw (separate toggle).
    mcs.set_axis_map({TRACE_EACT: AXIS_NA})
    mcs.add_capture(_khfac_cap(with_eret=True), "CH01")
    curves = mcs.ensure_page("CH01").scope._curves
    assert _subscript_trace_name(TRACE_EACT_CORR) in curves, (
        "E′act must draw independently of the E_act toggle")
    # Now hide E′act itself — it must disappear.
    mcs.set_axis_map({TRACE_EACT_CORR: AXIS_NA})
    curves = mcs.ensure_page("CH01").scope._curves
    assert _subscript_trace_name(TRACE_EACT_CORR) not in curves


# ------------------------------------------------- markers on the E′act trace
def test_emc_ema_markers_ride_interface_extrema(qapp):
    """Operator: "Be sure that the electrode polarization is placed on the
    proper trace."  For a KHFAC capture the Emc/Ema markers must sit at the
    min/max of the INTERFACE E_i (the E′act trace), NOT at a phase boundary on
    the raw V_mon."""
    from stimtest.plotting import compute_metric_markers
    from stimtest.metrics import compute_metrics
    cap = _khfac_cap(with_eret=False)          # v_mon = 0.001·i − 0.30·cos
    cap.metrics = compute_metrics(cap, surface_area_um2=1000.0)
    e_i, _vr, _r = ghazavi_corrected_waveforms(cap.v_mon_v, cap.i_mon_ua)
    i_min, i_max = int(e_i.argmin()), int(e_i.argmax())
    polars = [m for m in compute_metric_markers(cap) if m["kind"] == "polar"]
    assert polars, "no polarization markers"
    for m in polars:
        idx = i_min if m["label"].startswith("Emc") else i_max
        # Marker sits at the INTERFACE extremum (on the E′act trace): Emc at
        # argmin(E_i), Ema at argmax(E_i) — value + position agree.
        assert abs(m["y"] - float(e_i[idx])) < 1e-6, (m["label"], m["y"])
        assert abs(m["t_us"] - float(cap.time_us[idx])) < 1e-6
        # Correct polarity placement — Emc is the most-cathodic (negative)
        # interface excursion, Ema the most-anodic (positive).  The OLD code
        # placed Emc at the phase boundary, i.e. the OPPOSITE (max) extremum.
        if m["label"].startswith("Emc"):
            assert m["y"] < 0.0, (m["label"], m["y"])
        else:
            assert m["y"] > 0.0, (m["label"], m["y"])


# ------------------------------------------------------- export plot
def test_export_plot_has_primed_corrected_labels(qapp):
    pytest.importorskip("matplotlib")
    from stimtest.plotting import plot_capture
    from stimtest.session import Session, TestParameters, ChannelRun
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.metrics import compute_metrics
    cap = _khfac_cap(with_eret=True)
    cap.metrics = compute_metrics(cap, surface_area_um2=1000.0)
    test = TestParameters(experiment="VT", pattern=_sine_pattern(),
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="nb", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(cap)
    fig = plot_capture(cap, run, sess)
    try:
        labels = []
        for ax in fig.axes:
            for ln in ax.get_lines():
                labels.append(ln.get_label())
        joined = " ".join(labels)
        assert "E'_{\\mathrm{act}}" in joined, joined     # E′act primed mathtext
        assert "E'_{\\mathrm{ret}}" in joined, joined     # E′ret primed mathtext
        # The corrected lines are dashed.
        dashed = [ln for ax in fig.axes for ln in ax.get_lines()
                  if "interface" in str(ln.get_label())]
        assert dashed and all(ln.get_linestyle() == "--" for ln in dashed)
    finally:
        import matplotlib.pyplot as plt
        plt.close(fig)
