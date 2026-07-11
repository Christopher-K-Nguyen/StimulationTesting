"""Tests for two scope-display behaviours:

1. POTENTIAL traces (E_ret / E_act) are shown RAW — their natural
   electrode REST POTENTIAL is preserved — while the zero-idle monitor
   traces (V_mon / I_mon) are baseline-subtracted to zero.  Operator
   spec: "be sure [no offset] for the other channels, UNLESS the
   waveforms are supposed to have a natural offset such as potential
   measurements."

2. Asymmetric pulse centering: the GUI plot frames the pulse with a
   SMALLER preceding than proceeding interpulse (operator preference),
   without ever cropping the pulse itself.
"""
from __future__ import annotations

import sys

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ----------------------------------------------------------------------
# #1 — E_ret raw (rest potential kept), V_mon zeroed
# ----------------------------------------------------------------------
def _capture_with_rest_potential():
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    t = np.linspace(-320.0, 320.0, 4000)
    v = np.full_like(t, 0.08)    # V_mon carries a +0.08 V DC offset
    v[1000:2000] = -0.15 + 0.08
    v[2200:3200] = +0.15 + 0.08
    i = np.zeros_like(t)
    i[1000:2000] = -50.0
    i[2200:3200] = +50.0
    eret = np.full_like(t, 0.35)  # E_ret sits at a +0.35 V rest potential
    eret[1000:2000] += -0.02
    eret[2200:3200] += +0.02
    cap = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=50.0))
    cap.time_us = t
    cap.v_mon_v = v
    cap.i_mon_ua = i
    cap.e_ret_v = eret
    return cap


def test_all_traces_shown_raw_no_subtraction(qapp):
    from stimtest.gui.multichannel_scope import (
        _ChannelPage, TRACE_VMON, TRACE_IMON, TRACE_ERET, TRACE_EACT)
    page = _ChannelPage("r0_c0")
    cap = _capture_with_rest_potential()
    page.set_capture(cap, {TRACE_VMON: True, TRACE_IMON: True,
                           TRACE_ERET: True, TRACE_EACT: False})
    curves = page.scope._curves

    # V_mon: RAW — the +0.08 V DC offset must be PRESERVED (no baseline
    # subtraction).  Operator-spec: int8→double conversion only.
    y_vmon = curves[page._trace_label(TRACE_VMON)].getData()[1]
    assert np.allclose(y_vmon, cap.v_mon_v), \
        "V_mon must be shown raw (no subtraction)"
    assert abs(float(np.median(y_vmon[:300])) - 0.08) < 1e-9, \
        "V_mon idle offset must be preserved, not zeroed"

    # E_ret: RAW — the +0.35 V rest potential is preserved.
    y_eret = curves[page._trace_label(TRACE_ERET)].getData()[1]
    assert np.allclose(y_eret, cap.e_ret_v), \
        "E_ret must be shown raw (rest potential preserved)"


# ----------------------------------------------------------------------
# #3 — asymmetric pulse centering
# ----------------------------------------------------------------------
def _imon_record():
    t = np.linspace(-320.0, 320.0, 6000)
    i = np.zeros_like(t)
    i[(t >= -200.0) & (t < 0.0)] = -50.0    # cathodic, before trigger
    i[(t >= 20.0) & (t < 230.0)] = +50.0    # anodic, after trigger
    return t, i


def test_asymmetric_xrange_preceding_smaller(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    t, i = _imon_record()
    xr = sp._asymmetric_pulse_xrange(t, {"I_mon": i})
    assert xr is not None
    x_min, x_max = xr
    t0, t1 = -200.0, 230.0   # pulse span
    # Pulse fully visible.
    assert x_min <= t0 and x_max >= t1
    # Preceding interpulse smaller than proceeding.
    preceding = t0 - x_min
    proceeding = x_max - t1
    assert preceding < proceeding


def test_set_traces_uses_acquisition_time_axis_unchanged(qapp):
    """set_traces plots the time axis AS-IS from the acquisition — it does
    NOT actively detect the pulse and shift the axis for "zero placement"
    (operator: "There should not be an active adjustment of time ... you
    should be converting the x values from acquisition / record length +
    interval + offset like my MATLAB code").

    Even with a clear pulse present (whose onset sits at NEGATIVE time in
    the trigger-relative record), the stored time axis must equal the
    acquisition time array — t=0 placement is the acquisition's job
    (trigger source + XZEro), not the display's.
    """
    from stimtest.gui.widgets import ScopePlot, HAS_PYQTGRAPH
    if not HAS_PYQTGRAPH:
        pytest.skip("pyqtgraph not available")
    sp = ScopePlot()
    t, i = _imon_record()           # cathodic onset at t=-200 (pre-trigger)
    sp.set_traces(t, {"I_mon": i})
    stored_t, _ = sp._curve_data["I_mon"]
    assert np.allclose(np.asarray(stored_t, float), np.asarray(t, float)), \
        "time axis must be passed through unchanged (no active t=0 shift)"


def test_asymmetric_xrange_falls_back_when_no_pulse(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    t = np.linspace(-320.0, 320.0, 6000)
    flat = np.zeros_like(t)   # no active pulse
    assert sp._asymmetric_pulse_xrange(t, {"X": flat}) is None


def test_asymmetric_xrange_handles_empty(qapp):
    from stimtest.gui.widgets import ScopePlot
    sp = ScopePlot()
    t = np.linspace(-10.0, 10.0, 100)
    assert sp._asymmetric_pulse_xrange(t, {}) is None
    assert sp._asymmetric_pulse_xrange(t, None) is None
