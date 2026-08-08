"""The IDEAL (programmed) current as a toggleable trace on the LIVE plot.

The programmed current — what the stimulator was ASKED to deliver — was already
reconstructed for the driving-energy integral (``metrics.ideal_current_ua``,
gotcha #117) and drawn on the exported / POLARIS figure, but there was no way
to see it DURING a run.  It is now a trace-toggle option beside I_mon.

Why it matters live: I_mon carries switching spikes and a turn-on skew at small
phase widths, so programmed-vs-delivered diverging is the quick read on whether
the electrode is actually getting the pattern.  Same units and axis as I_mon so
the two overlay directly; DASHED so which is which is unambiguous.

Off by default — it is a comparison overlay, not part of the normal view.
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
    MultiChannelScope, TRACE_IIDEAL, TRACE_IMON, ALL_TOGGLE_TRACES,
    ALL_IDEAL_TRACES, DEFAULT_TRACE_AXIS, AXIS_NA, _subscript_trace_name)

_app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

AMP = 300.0


def _cap(idx=0, *, with_metrics=True, area_um2=2000.0):
    from stimtest.metrics import compute_metrics
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=-AMP, polarity=-1,
                              phase_width_us=200, interphase_us=20,
                              discharge_us=20, rate_hz=200.0)
    c = Capture(index=idx, pattern=p)
    t = np.linspace(-60.0, 600.0, 2000)
    v = np.zeros_like(t)
    i = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    m2 = (t >= 220.0) & (t <= 420.0)
    v[m1] = -1.0 - 0.002 * t[m1]
    v[m2] = 1.0
    i[m1] = -AMP
    i[m2] = AMP
    c.time_us = t
    c.v_mon_v = v
    c.i_mon_ua = i
    if with_metrics:
        compute_metrics(c, surface_area_um2=area_um2)
    return c


def _curves(mcs):
    out = {}
    for pg in mcs._pages.values():
        sc = getattr(pg, "scope", None)
        if sc is not None:
            out.update(getattr(sc, "_curve_data", {}))
    return out


def _axis_of(mcs, key):
    for pg in mcs._pages.values():
        sc = getattr(pg, "scope", None)
        if sc is not None and key in getattr(sc, "_curve_axis", {}):
            return sc._curve_axis[key]
    return None


def test_ideal_current_is_a_toggle_option_and_off_by_default():
    assert TRACE_IIDEAL in ALL_TOGGLE_TRACES
    assert TRACE_IIDEAL in ALL_IDEAL_TRACES
    assert DEFAULT_TRACE_AXIS[TRACE_IIDEAL] == AXIS_NA
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(), "CH01")
    assert TRACE_IIDEAL in mcs.axis_combos
    assert mcs.visibility()[TRACE_IIDEAL] is False
    key = _subscript_trace_name(TRACE_IIDEAL)
    assert key not in _curves(mcs), "drawn despite defaulting to N/A"


def test_ideal_current_renders_at_the_programmed_amplitude():
    """Not normalized (unlike dV/dt) — it is a real current in µA, so it must
    overlay I_mon at the same scale."""
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(), "CH01")
    mcs.axis_combos[TRACE_IIDEAL].setCurrentIndex(2)      # Right y-axis = on
    _app.processEvents()
    assert mcs.visibility()[TRACE_IIDEAL] is True
    key = _subscript_trace_name(TRACE_IIDEAL)
    cur = _curves(mcs)
    assert key in cur, list(cur)
    y = np.asarray(cur[key][1], dtype=float)
    assert abs(float(np.nanmax(np.abs(y))) - AMP) < 1.0, float(np.nanmax(np.abs(y)))
    # Both polarities present — it is the full biphasic program, not |I|.
    assert float(np.nanmin(y)) < -0.5 * AMP
    assert float(np.nanmax(y)) > 0.5 * AMP


def test_ideal_current_shares_the_imon_axis():
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(), "CH01")
    mcs.axis_combos[TRACE_IIDEAL].setCurrentIndex(2)
    _app.processEvents()
    ideal_axis = _axis_of(mcs, _subscript_trace_name(TRACE_IIDEAL))
    imon_axis = _axis_of(mcs, _subscript_trace_name(TRACE_IMON))
    assert ideal_axis == imon_axis, (ideal_axis, imon_axis)


def test_ideal_current_is_dashed():
    """Programmed vs measured must be visually unambiguous — same hue as I_mon,
    distinguished by the dash (the convention the corrected E' pair uses).

    ``styles`` is consumed by ``ScopePlot.set_traces`` at draw time rather than
    stored, so assert at that boundary."""
    mcs = MultiChannelScope()
    mcs.add_capture(_cap(), "CH01")
    seen = {}
    for pg in mcs._pages.values():
        sc = pg.scope
        orig = sc.set_traces

        def _spy(*a, _o=orig, **kw):
            seen.update(kw.get("styles") or {})
            return _o(*a, **kw)
        sc.set_traces = _spy
    mcs.axis_combos[TRACE_IIDEAL].setCurrentIndex(2)
    _app.processEvents()
    key = _subscript_trace_name(TRACE_IIDEAL)
    assert seen.get(key) == "dash", seen


def test_falls_back_to_rebuilding_when_metrics_never_ran():
    """A capture loaded from a legacy .npz (or one whose metrics were never
    computed) carries no ``i_ideal_ua``; the trace must rebuild it from the
    pattern rather than silently vanish."""
    c = _cap(with_metrics=False)
    assert getattr(c, "i_ideal_ua", None) is None
    mcs = MultiChannelScope()
    mcs.add_capture(c, "CH01")
    mcs.axis_combos[TRACE_IIDEAL].setCurrentIndex(2)
    _app.processEvents()
    key = _subscript_trace_name(TRACE_IIDEAL)
    cur = _curves(mcs)
    assert key in cur, list(cur)
    y = np.asarray(cur[key][1], dtype=float)
    assert abs(float(np.nanmax(np.abs(y))) - AMP) < 1.0


def test_follows_the_current_density_unit_toggle():
    """In density mode I_mon is converted to A/cm²; the ideal current must be
    converted the same way or the two would no longer overlay."""
    area = 2000.0
    mcs = MultiChannelScope()
    mcs.set_surface_area_um2(area)
    mcs.add_capture(_cap(area_um2=area), "CH01")
    mcs.axis_combos[TRACE_IIDEAL].setCurrentIndex(2)
    # switch the I_mon unit dropdown to density
    mcs.imon_unit_combo.setCurrentIndex(1)
    _app.processEvents()
    key = _subscript_trace_name(TRACE_IIDEAL)
    cur = _curves(mcs)
    assert key in cur, list(cur)
    y = np.asarray(cur[key][1], dtype=float)
    expect = AMP * 100.0 / area          # A/cm² = µA × 100 / area_um2
    assert abs(float(np.nanmax(np.abs(y))) - expect) < 0.01 * expect
