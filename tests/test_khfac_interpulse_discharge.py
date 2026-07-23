"""KHFAC discharge-as-short: REPLACE the existing 0-µA discharge step between
pulses with a real PlexStim auto-discharge SHORT (no time added).

Operator: "I do not want to add a 1-µs step for discharge.  I want to replace
a 0-µA step between pulses as discharge.  This toggle should only [be] enabled
when the Discharge Mode is enabled."

Design: ``PulsePattern.interpulse_discharge_us`` is a FLAG (value = the
existing discharge duration).  When > 0, ``build_pat_pairs`` SKIPS the trailing
discharge ``(0, discharge)`` pair, so the device plays only the pulse then
IDLES the discharge for the rest of the UNCHANGED ``device_period_us`` — and
the PlexStim auto-discharge (Discharge Mode) shorts that idle.  No time added;
``is_continuous_sinusoidal`` tolerates the flagged trailing discharge so the
Ghazavi E_off / DC readout keeps computing.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")

from stimtest.waveforms import PulsePattern, build_pat_pairs, SHAPE_SINUSOIDAL
from stimtest.metrics import is_continuous_sinusoidal, ghazavi_polarization


def _khfac(discharge_us=5.0, w=100.0, as_short=False):
    """Continuous symmetric biphasic sinusoid with a trailing discharge; rate
    set so the period exactly fits (pulse + discharge) → continuous."""
    period = 2 * w + discharge_us
    p = PulsePattern.biphasic(
        amplitude_ua=1000.0, phase_width_us=w, interphase_us=0.0,
        discharge_us=discharge_us, rate_hz=1e6 / period, symmetric=True,
        shape="sinusoidal", shape2="sinusoidal")
    if as_short:
        p.interpulse_discharge_us = discharge_us
    return p


# --------------------------------------------------------------------------
# backend: replace the 0-µA step, add no time, keep the period + classification
# --------------------------------------------------------------------------
def test_default_off_keeps_the_floating_discharge_step():
    p = _khfac(discharge_us=5.0, as_short=False)
    assert p.interpulse_discharge_us == 0.0
    zeros = [(a, d) for a, d in build_pat_pairs(p) if a == 0]
    assert zeros == [(0, 5)]                 # the floating 0-µA discharge step
    # a floating-discharge pattern is NOT continuous-sinusoidal (phase delay)
    assert is_continuous_sinusoidal(p) is False


def test_toggle_on_removes_the_pat_pair_no_time_added():
    off = _khfac(discharge_us=5.0, as_short=False)
    on = _khfac(discharge_us=5.0, as_short=True)
    pairs_off = build_pat_pairs(off)
    pairs_on = build_pat_pairs(on)
    # the trailing discharge 0-µA pair is REMOVED (replaced by a device-idle
    # short) — one fewer pair, no zero-amp pairs left
    assert [(a, d) for a, d in pairs_on if a == 0] == []
    assert len(pairs_on) == len(pairs_off) - 1
    assert pairs_on == pairs_off[:-1]
    # NO time added — the device period is identical
    assert on.device_period_us == pytest.approx(off.device_period_us)
    assert on.device_period_us == pytest.approx(205.0)   # 2*100 + 5
    # effective pulse rate unchanged (period unchanged)
    assert on.effective_pulse_rate_hz == pytest.approx(off.effective_pulse_rate_hz)


def test_toggle_on_keeps_khfac_classification():
    on = _khfac(discharge_us=5.0, as_short=True)
    # the flagged trailing discharge is tolerated → Ghazavi analysis applies
    assert is_continuous_sinusoidal(on) is True


def test_validate_ok_both_ways():
    _khfac(discharge_us=5.0, as_short=False).validate()
    _khfac(discharge_us=5.0, as_short=True).validate()


def test_scaled_and_persistence_round_trip():
    p = _khfac(discharge_us=5.0, as_short=True)
    assert p.scaled(0.5).interpulse_discharge_us == pytest.approx(5.0)
    from stimtest.persistence import _pattern_dict
    d = _pattern_dict(p)
    assert d["interpulse_discharge_us"] == pytest.approx(5.0)


def test_a_seamless_sinusoid_without_discharge_is_unaffected():
    # no discharge → nothing to replace; a bare continuous sinusoid is KHFAC
    p = PulsePattern.biphasic(
        amplitude_ua=1000.0, phase_width_us=100.0, interphase_us=0.0,
        discharge_us=0.0, rate_hz=5000.0, symmetric=True,
        shape="sinusoidal", shape2="sinusoidal")
    assert is_continuous_sinusoidal(p) is True
    assert p.device_period_us == pytest.approx(200.0)


# --------------------------------------------------------------------------
# Ghazavi robust windowing (unchanged): the discharge transient is windowed out
# --------------------------------------------------------------------------
def _sinusoid_capture(pts_per_period=200, n_periods=6, e_off=0.20, amp=0.05,
                      r_kohm=2.0, i_amp_ua=1000.0, discharge=False):
    n = n_periods * pts_per_period
    t = np.arange(n, dtype=float)
    ph = 2 * np.pi * (t % pts_per_period) / pts_per_period
    i = i_amp_ua * np.sin(ph)
    e_i = e_off + amp * np.sin(ph)
    if discharge:
        e_i[np.arange(0, n, pts_per_period)] = 0.0   # ~0.5 % shorted to 0
    v = e_i + (r_kohm / 1e3) * i
    return v, i


def test_robust_windows_out_the_discharge_transient():
    v_clean, i = _sinusoid_capture(discharge=False)
    v_disc, _ = _sinusoid_capture(discharge=True)
    clean = ghazavi_polarization(v_clean, i)
    biased = ghazavi_polarization(v_disc, i, robust=False)
    fixed = ghazavi_polarization(v_disc, i, robust=True)
    assert abs(fixed["e_off_v"] - clean["e_off_v"]) < \
        abs(biased["e_off_v"] - clean["e_off_v"])
    assert fixed["e_off_v"] == pytest.approx(clean["e_off_v"], abs=0.01)


def test_robust_leaves_a_clean_sinusoid_unchanged():
    v, i = _sinusoid_capture(discharge=False)
    a = ghazavi_polarization(v, i, robust=False)
    b = ghazavi_polarization(v, i, robust=True)
    assert b["e_off_v"] == pytest.approx(a["e_off_v"], abs=1e-9)


# --------------------------------------------------------------------------
# GUI toggle: stamp = discharge duration, gated on Discharge Mode + a discharge
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _sinusoid_panel(_app):
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, SYMMETRIC, BIPHASIC)
    pp = PatternControlPanel()
    pp.symmetry.setCurrentText(SYMMETRIC)
    pp.phase_count.setCurrentText(BIPHASIC)
    idx = pp.shape_combo.findData(SHAPE_SINUSOIDAL)
    pp.shape_combo.setCurrentIndex(idx)
    pp.discharge_check.setChecked(True)          # a discharge exists
    pp.set_auto_discharge_silent(True)           # Discharge Mode on
    pp._refresh_interpulse_discharge()
    return pp


def test_toggle_enabled_only_with_discharge_mode(_app):
    pp = _sinusoid_panel(_app)
    # Discharge Mode ON + discharge present → the toggle is enabled
    assert pp.interpulse_discharge_check.isEnabled() is True
    # turn Discharge Mode OFF → the toggle disables
    pp.set_auto_discharge_silent(False)
    pp._refresh_interpulse_discharge()
    assert pp.interpulse_discharge_check.isEnabled() is False


def test_toggle_stamps_the_discharge_duration(_app):
    pp = _sinusoid_panel(_app)
    pp.interpulse_discharge_check.setChecked(True)
    pat = pp.pattern()
    disch = pat.phases[-1].delay_after_us
    assert disch > 0
    assert pat.interpulse_discharge_us == pytest.approx(disch)   # = the discharge


def test_no_stamp_when_discharge_mode_off(_app):
    pp = _sinusoid_panel(_app)
    pp.interpulse_discharge_check.setChecked(True)
    pp.set_auto_discharge_silent(False)          # Discharge Mode OFF
    pat = pp.pattern()
    assert pat.interpulse_discharge_us == 0.0     # gated off → no stamp


def test_no_stamp_without_a_discharge(_app):
    pp = _sinusoid_panel(_app)
    pp.discharge_check.setChecked(False)          # no discharge to replace
    pp.interpulse_discharge_check.setChecked(True)
    pat = pp.pattern()
    assert pat.interpulse_discharge_us == 0.0


def test_prefs_round_trip(_app):
    from stimtest.gui.pattern_panel import PatternControlPanel
    pp = _sinusoid_panel(_app)
    pp.interpulse_discharge_check.setChecked(True)
    prefs = pp.current_prefs()
    assert prefs["interpulse_discharge_on"] is True
    pp2 = PatternControlPanel()
    pp2.restore_prefs(prefs)
    assert pp2.interpulse_discharge_check.isChecked() is True
