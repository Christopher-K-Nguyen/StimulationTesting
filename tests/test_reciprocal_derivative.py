"""Reciprocal Derivative Chronopotentiometry (RDC) — Musa et al. 2010.

Plots dt/dE vs the electrode potential E; a Faradaic reaction (potential
levels off, dE/dt → min) shows as a PEAK, capacitive charging as a flat
region.  Covers the metric (`reciprocal_derivative_curve`), the plot
(`plot_reciprocal_derivative`), and the POLARIS view-bar checkbox.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest

from stimtest.metrics import reciprocal_derivative_curve, RDCSegment
from stimtest.session import Capture
from stimtest.waveforms import Phase, PulsePattern, SHAPE_RECTANGULAR


def _biphasic_cap(faradaic_plateau_v=-0.45):
    """Cathodal-first biphasic V_mon with a Faradaic plateau (dE/dt≈0) in the
    cathodic phase → a peak in |dt/dE| at ``faradaic_plateau_v``."""
    dt = 0.5
    t = np.arange(-100.0, 500.0, dt)
    P = lambda a: Phase(amplitude_ua=a, width_us=200.0, shape=SHAPE_RECTANGULAR)
    pat = PulsePattern(phases=[P(-10.0), P(+10.0)], rate_hz=100.0)
    v = np.zeros_like(t)
    cath = (t >= 0) & (t < 200); tt = t[cath]
    ev = -0.05 - 0.004 * tt
    i = np.searchsorted(tt, 100.0)
    ev[(tt >= 100) & (tt < 140)] = ev[i]          # Faradaic hold
    ev[tt >= 140] = ev[i] - 0.004 * (tt[tt >= 140] - 140)
    v[cath] = ev
    an = (t >= 200) & (t < 400)
    if cath.sum() == an.sum():
        v[an] = -v[cath][::-1]
    return Capture(index=0, time_us=t, v_mon_v=v, i_mon_ua=np.zeros_like(t),
                   e_act_v=None, e_ret_v=None, pattern=pat)


# ---------------------------------------------------------------------------
# metric
# ---------------------------------------------------------------------------
def test_rdc_curve_has_one_segment_per_phase():
    segs, kind = reciprocal_derivative_curve(_biphasic_cap(), onset_us=0.0)
    assert len(segs) == 2
    assert {s.polarity for s in segs} == {"cathodic", "anodic"}
    assert kind == "v_mon"
    for s in segs:
        assert isinstance(s, RDCSegment)
        assert s.potential_v.size == s.dtde_ms_per_v.size > 0


def test_rdc_peak_lands_at_faradaic_plateau():
    """The |dt/dE| peak of the cathodic segment must sit at the plateau
    potential (where dE/dt → 0)."""
    segs, _ = reciprocal_derivative_curve(_biphasic_cap(-0.45), onset_us=0.0)
    cath = next(s for s in segs if s.polarity == "cathodic")
    fin = np.isfinite(cath.dtde_ms_per_v)
    e, d = cath.potential_v[fin], np.abs(cath.dtde_ms_per_v[fin])
    e_peak = e[int(np.argmax(d))]
    assert abs(e_peak - (-0.45)) < 0.05


def test_rdc_empty_for_unusable_capture():
    dt = 0.5
    t = np.arange(0.0, 5.0, dt)     # too few samples
    pat = PulsePattern(phases=[Phase(amplitude_ua=-10.0, width_us=200.0,
                                     shape=SHAPE_RECTANGULAR)], rate_hz=100.0)
    cap = Capture(index=0, time_us=t, v_mon_v=np.zeros_like(t),
                  i_mon_ua=np.zeros_like(t), e_act_v=None, e_ret_v=None,
                  pattern=pat)
    segs, _ = reciprocal_derivative_curve(cap, onset_us=0.0)
    assert segs == []


# ---------------------------------------------------------------------------
# plot
# ---------------------------------------------------------------------------
def test_plot_reciprocal_derivative_renders():
    import matplotlib
    matplotlib.use("Agg")
    from stimtest.plotting import plot_reciprocal_derivative
    fig = plot_reciprocal_derivative(_biphasic_cap(), cathodic_limit_v=-0.8,
                                     anodic_limit_v=0.6, reference_label="Ag|AgCl")
    ax = fig.axes[0]
    assert "dt/dE" in ax.get_ylabel()
    assert "Potential" in ax.get_xlabel() or "Voltage" in ax.get_xlabel()
    # two phase curves + zero line + two water-window vlines
    assert len(ax.get_lines()) >= 4


def test_plot_reciprocal_derivative_potential_axis_with_eact():
    import matplotlib
    matplotlib.use("Agg")
    from stimtest.plotting import plot_reciprocal_derivative
    cap = _biphasic_cap()
    cap.e_act_v = cap.v_mon_v.copy()          # recorded E_act → vs-reference axis
    fig = plot_reciprocal_derivative(cap, reference_label="Ag|AgCl")
    assert "vs Ag|AgCl" in fig.axes[0].get_xlabel()


# ---------------------------------------------------------------------------
# POLARIS view-bar checkbox
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def test_rdc_and_ct_are_mutually_exclusive(qapp):
    from stimtest.gui.viewer import _PlotViewBar
    bar = _PlotViewBar()
    bar.ct_check.setChecked(True)
    assert bar.charge_transfer() and not bar.reciprocal_derivative()
    bar.rdc_check.setChecked(True)            # turning RDC on turns CT off
    assert bar.reciprocal_derivative() and not bar.charge_transfer()
    bar.ct_check.setChecked(True)             # ...and vice versa
    assert bar.charge_transfer() and not bar.reciprocal_derivative()


def test_rdc_pref_round_trips(qapp):
    from stimtest.gui.viewer import _PlotViewBar
    bar = _PlotViewBar()
    bar.rdc_check.setChecked(True)
    p = bar.prefs()
    assert p["reciprocal_derivative"] is True
    bar2 = _PlotViewBar()
    bar2.restore(p)
    assert bar2.reciprocal_derivative() is True
