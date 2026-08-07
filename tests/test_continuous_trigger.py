"""Continuous (no-interpulse-delay) waveform trigger + KHFAC metric-table.

Operator's diagnosis (confirmed by a bench log showing ``NUMACq = 0/64``):
for a pattern with NO interpulse delay the Plexon digital sync stays HIGH the
whole time it stimulates (no edge), and the amplitude-level I_mon trigger sits
above the tiny low-current signal — so the scope never triggers and every
capture times out on a stale frame (which the classifier then read as
"broken at −1 µA").  Fix: trigger on the I_mon analog edge —

  * RISE for anodal-first, FALL for cathodal-first;
  * level 0 for SINUSOIDAL / RECTANGULAR (clean zero-crossing), HALF the
    phase-1 peak for other shapes ("consider the other shapes").
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Create the QApplication at IMPORT time (before the non-Qt runner tests run)
# so the later MetricTable tests find a live app — creating one inline mid-run
# in this process crashed under offscreen.
from PyQt6 import QtWidgets  # noqa: E402
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
# Sandbox prefs (gotcha #100) — without this the app name defaults to the
# process basename and later modules read/write the console-dev prefs.
_APP.setApplicationName("pulsar-pytest")

from stimtest.waveforms import (Phase, PulsePattern, SHAPE_SINUSOIDAL,  # noqa: E402
                                SHAPE_RECTANGULAR, SHAPE_GAUSSIAN)
from stimtest.experiments.base import continuous_trigger_level_slope  # noqa: E402
from stimtest.gui.widgets import metric_row_text as _mrt


def _cont(shape, amp=-1.0, width=100.0, rate=5000.0):
    """Continuous (no-gap) symmetric biphasic: 2×width µs fills 1/rate."""
    return PulsePattern(phases=[
        Phase(amplitude_ua=amp, width_us=width, shape=shape, delay_after_us=0.0),
        Phase(amplitude_ua=-amp, width_us=width, shape=shape, delay_after_us=0.0),
    ], rate_hz=rate)


# ------------------------------------------------------ level/slope helper
def test_sinusoidal_zero_crossing_slope_by_polarity():
    assert continuous_trigger_level_slope(-1.0, SHAPE_SINUSOIDAL) == (0.0, "FALL")
    assert continuous_trigger_level_slope(50.0, SHAPE_SINUSOIDAL) == (0.0, "RISE")


def test_rectangular_is_zero_crossing():
    lvl, slope = continuous_trigger_level_slope(-10.0, SHAPE_RECTANGULAR)
    assert lvl == 0.0 and slope == "FALL"


def test_gaussian_uses_half_peak_signed():
    lvl, slope = continuous_trigger_level_slope(-10.0, SHAPE_GAUSSIAN,
                                                imon_v_per_ua=2.5e-3)
    assert slope == "FALL"
    assert abs(lvl - (0.5 * -10.0 * 2.5e-3)) < 1e-12     # −0.0125 V
    lvl2, slope2 = continuous_trigger_level_slope(10.0, SHAPE_GAUSSIAN,
                                                  imon_v_per_ua=2.5e-3)
    assert slope2 == "RISE" and lvl2 > 0


# ---------------------------------------------- runner per-step update path
def test_update_imon_trigger_continuous_sets_zero_crossing():
    from stimtest.experiments.voltage_transient import VoltageTransientExperiment
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    pat = _cont(SHAPE_SINUSOIDAL, amp=-1.0)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(sess, stim, scope)
    calls = []
    runner.scope.set_trigger_level = lambda v: calls.append(v)
    # Even with a digital trigger configured, the continuous branch wins.
    runner.trigger_source = "CH3"
    runner.trigger_is_digital = True
    runner.update_imon_trigger_level(-1.0, 100.0)
    stim.close(); scope.close()
    assert calls == [0.0]


def test_update_imon_trigger_pulsed_uses_amplitude_formula():
    from stimtest.experiments.voltage_transient import VoltageTransientExperiment
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    # Normal biphasic with a BIG interpulse gap (rate leaves idle time).
    pat = PulsePattern.biphasic(amplitude_ua=50.0, rate_hz=100.0)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(sess, stim, scope)
    calls = []
    runner.scope.set_trigger_level = lambda v: calls.append(v)
    runner.trigger_source = "CH2"
    runner.trigger_is_digital = False
    runner.update_imon_trigger_level(50.0, 200.0)
    stim.close(); scope.close()
    # NOT the continuous zero-crossing — the amplitude-based I_mon level.
    assert calls and calls[0] != 0.0


# ------------------------------------------------ pulse-width trigger plan
def test_plan_sinusoid_is_edge():
    from stimtest.experiments.base import continuous_trigger_plan
    plan = continuous_trigger_plan(_cont(SHAPE_SINUSOIDAL, amp=-1.0))
    assert plan["kind"] == "edge"
    assert plan["level_v"] == 0.0 and plan["slope"] == "FALL"


def test_plan_gaussian_is_pulse_width_with_sane_dwell():
    from stimtest.experiments.base import continuous_trigger_plan
    plan = continuous_trigger_plan(_cont(SHAPE_GAUSSIAN, amp=-10.0,
                                         width=100.0),
                                   imon_v_per_ua=2.5e-3)
    assert plan["kind"] == "pulse_width"
    assert plan["polarity"] == "NEGative"
    assert plan["when"] == "MOREthan"
    # Half-peak level, half-dwell qualification: the gaussian's dwell
    # beyond half peak is its FWHM (a large fraction of the 100 µs
    # phase), so the qualification width is 10-50 µs — far above a
    # noise blip, well below the lobe.
    w_us = plan["width_s"] * 1e6
    assert 5.0 <= w_us <= 50.0
    assert abs(plan["level_v"] - (0.5 * -10.0 * 2.5e-3)) < 1e-12


def _fake_tek(cmds):
    """Bare TektronixOscilloscope with a captured SCPI log (no VISA)."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    scope._cmds = cmds
    scope._trig_pulse_source = None
    scope._expected_trigger_source = "CH2"
    scope.writes = []
    scope._w = lambda cmd: scope.writes.append(cmd)
    scope._q = lambda cmd: "5.0e-05"
    scope._log = lambda msg: None
    return scope


def test_driver_pulse_width_scpi_and_level_routing():
    from stimtest.hardware.tektronix_models import MODERN_CMDS
    scope = _fake_tek(MODERN_CMDS)
    ok = scope.set_trigger_pulse_width("CH2", -0.0125, "NEGative", 50e-6)
    assert ok is True
    joined = "\n".join(scope.writes)
    assert "TRIGger:A:TYPe PULSEWidth" in joined
    assert "TRIGger:A:PULSEWidth:SOUrce CH2" in joined
    assert "TRIGger:A:PULSEWidth:POLarity NEGative" in joined
    assert "TRIGger:A:PULSEWidth:WHEN MOREthan" in joined
    assert any("TRIGger:A:PULSEWidth:WIDth" in w for w in scope.writes)
    # Threshold goes to the per-channel LOWerthreshold register.
    assert any("TRIGger:A:LOWerthreshold:CH2" in w for w in scope.writes)
    # And per-step level updates now route there too (NOT TRIGger:A:LEVel).
    scope.writes.clear()
    scope.set_trigger_level(-0.025)
    assert any("LOWerthreshold:CH2" in w for w in scope.writes)
    assert not any("TRIGger:A:LEVel" in w for w in scope.writes)


def test_driver_pulse_width_declines_on_legacy():
    from stimtest.hardware.tektronix_models import LEGACY_CMDS
    scope = _fake_tek(LEGACY_CMDS)
    assert scope.set_trigger_pulse_width("CH2", -0.0125, "NEGative",
                                         50e-6) is False
    assert scope.writes == []          # nothing written — clean decline


def test_simulator_declines_pulse_width():
    from stimtest.hardware.simulator import SimulatedOscilloscope
    s = SimulatedOscilloscope()
    assert s.set_trigger_pulse_width("CH2", 0.0, "NEGative", 50e-6) is False


# -------------------------------------------- classifier exemption (KHFAC)
def test_continuous_sinusoid_never_broken():
    from stimtest.metrics import classify_response_and_ceff
    pat = _cont(SHAPE_SINUSOIDAL, amp=-1.0)
    t = np.linspace(-50.0, 250.0, 3000)
    # A big-swing V_mon that WOULD read "broken" for a shaped pulse
    # (0.5 V / 1 µA = 0.5 MΩ ≥ the 0.30 MΩ shaped-broken threshold).
    v = 0.5 * np.sin(2 * np.pi * t / 200.0)
    cls, _ = classify_response_and_ceff(
        t, v, pat, onset_us=0.0, driving_v=0.5, compliance_v=12.0)
    assert cls == "normal"


# --------------------------------------------- metric-table label changes
def test_metric_table_sinusoidal_has_frequency_not_method():
    from stimtest.gui.widgets import MetricTable
    from stimtest.session import Capture
    c = Capture(index=0, pattern=_cont(SHAPE_SINUSOIDAL, amp=-50.0))
    c.metrics.polarization_method = "sinusoidal"
    c.metrics.ghazavi_freq_khz = 5.0
    tbl = MetricTable(); tbl.show_capture(c)
    keys = [_mrt(tbl, r)[0] for r in range(tbl.rowCount()) if tbl.item(r, 0)]
    assert not any("Method" in k for k in keys)
    assert any("frequency" in k.lower() for k in keys)


def test_metric_table_normal_has_rate_and_period():
    from stimtest.gui.widgets import MetricTable
    from stimtest.session import Capture
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=50.0,
                                                       rate_hz=1000.0))
    tbl = MetricTable(); tbl.show_capture(c)
    keys = [_mrt(tbl, r)[0] for r in range(tbl.rowCount()) if tbl.item(r, 0)]
    assert any("Pulse rate" in k for k in keys)
    assert any("Pulse period" in k for k in keys)
