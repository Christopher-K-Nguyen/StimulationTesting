"""Galvanostatic EIS: impedance math, frequency limits, adaptive cycles,
Gamry-style modes, and an end-to-end simulator sweep.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

from PyQt6 import QtWidgets  # noqa: E402
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
_APP.setApplicationName("pulsar-pytest")

from stimtest.metrics import complex_impedance
from stimtest.experiments.galvanostatic_eis import (
    GalvanostaticEISExperiment, GalvanostaticEISPolicy, eis_frequencies,
    eis_frequency_limits, eis_cycles_for, eis_resolve_mode, EIS_MODES,
    _grid_half_width_us)
from stimtest.waveforms import SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR


# ------------------------------------------------------- complex_impedance
F = 1000.0
W = 2 * np.pi * F


def _axes(cycles=10, n=4000):
    t_s = np.linspace(0.0, cycles / F, n, endpoint=False)
    return t_s * 1e6, t_s


def test_impedance_resistive():
    t_us, t_s = _axes()
    i = 50.0 * np.sin(W * t_s)                 # 50 µA
    v = 1e4 * 50e-6 * np.sin(W * t_s)         # V = R·I, R = 10 kΩ → 0.5 V
    z = complex_impedance(v, i, t_us, F)
    assert abs(z["z_mag_ohm"] - 1e4) < 50.0    # 10 kΩ
    assert abs(z["z_phase_deg"]) < 1.0         # resistive → 0°
    assert abs(z["z_real_ohm"] - 1e4) < 50.0
    assert abs(z["z_imag_ohm"]) < 50.0


def test_impedance_capacitive_negative_imag():
    t_us, t_s = _axes()
    i = 50.0 * np.sin(W * t_s)
    v = -0.5 * np.cos(W * t_s)                  # V lags I 90° (capacitive)
    z = complex_impedance(v, i, t_us, F)
    assert abs(z["z_phase_deg"] - (-90.0)) < 1.0
    assert z["z_imag_ohm"] < 0                  # reactive, negative
    assert abs(z["z_real_ohm"]) < 50.0
    assert abs(z["z_mag_ohm"] - 1e4) < 50.0


def test_impedance_nan_on_bad_input():
    t_us, t_s = _axes()
    i = np.sin(W * t_s)
    for z in (complex_impedance(np.sin(W * t_s), i, t_us, 0.0),
              complex_impedance(np.zeros_like(i), i, t_us, F)):
        assert all(np.isnan(v) for v in z.values())


# --------------------------------------------------------- frequency grid
def test_frequency_limits_by_shape():
    assert eis_frequency_limits(SHAPE_RECTANGULAR)[1] == 500_000.0
    assert eis_frequency_limits(SHAPE_SINUSOIDAL)[1] == 125_000.0


def test_frequencies_clamped_sorted_deduped():
    freqs = eis_frequencies(1.0, 100_000.0, 10, SHAPE_SINUSOIDAL)
    assert freqs == sorted(freqs)
    assert freqs[0] >= 1.0
    assert freqs[-1] <= 125_000.0 + 1
    # Every returned value is an exact 1 µs-grid frequency (no dupes).
    widths = [_grid_half_width_us(f) for f in freqs]
    assert len(set(widths)) == len(widths)


def test_frequencies_clamp_to_shape_max():
    # A 200 kHz request on a SINE probe clamps to the 125 kHz shape max.
    freqs = eis_frequencies(1.0, 200_000.0, 10, SHAPE_SINUSOIDAL)
    assert max(freqs) <= 125_000.0 + 1
    # The SQUARE probe reaches higher.
    fsq = eis_frequencies(1.0, 200_000.0, 10, SHAPE_RECTANGULAR)
    assert max(fsq) > 125_000.0


# ----------------------------------------------------------- adaptive cycles
def test_cycles_reduce_as_frequency_decreases():
    hi = eis_cycles_for(10_000.0, target_capture_s=0.5, min_cycles=2, max_cycles=32)
    mid = eis_cycles_for(20.0, target_capture_s=0.5, min_cycles=2, max_cycles=32)
    lo = eis_cycles_for(1.0, target_capture_s=0.5, min_cycles=2, max_cycles=32)
    assert hi == 32                             # capped at max
    assert lo == 2                              # floored at min
    assert mid == 10                            # 20 · 0.5 = 10, in-band
    assert lo <= mid <= hi                       # monotone with frequency


def test_cycles_floor_and_cap():
    assert eis_cycles_for(0.1, min_cycles=3, max_cycles=50) == 3
    assert eis_cycles_for(1e9, min_cycles=3, max_cycles=50) == 50


# ---------------------------------------------------- Gamry "Optimize for"
def test_modes_are_monotonic():
    fast, normal, low = (eis_resolve_mode(m) for m in ("fast", "normal", "low_noise"))
    assert fast["min_cycles"] <= normal["min_cycles"] <= low["min_cycles"]
    assert fast["max_cycles"] <= normal["max_cycles"] <= low["max_cycles"]
    assert fast["target_capture_s"] <= normal["target_capture_s"] <= low["target_capture_s"]
    assert fast["settle_cycles"] <= normal["settle_cycles"] <= low["settle_cycles"]


def test_policy_resolved_uses_mode_then_override():
    p = GalvanostaticEISPolicy(mode="fast")
    assert p.resolved()["min_cycles"] == EIS_MODES["fast"]["min_cycles"]
    p2 = GalvanostaticEISPolicy(mode="fast", min_cycles=7)   # explicit override
    assert p2.resolved()["min_cycles"] == 7
    assert p2.resolved()["max_cycles"] == EIS_MODES["fast"]["max_cycles"]


# --------------------------------------------------- end-to-end simulator
def _sim_runner(policy):
    from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
    from stimtest.session import Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.waveforms import PulsePattern
    test = TestParameters(
        experiment="EIS", pattern=PulsePattern.biphasic(amplitude_ua=10.0, rate_hz=1000.0),
        configuration=Configuration.monopolar(1), array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    return GalvanostaticEISExperiment(sess, stim, scope, policy=policy), stim, scope


def test_sweep_runs_and_produces_one_capture_per_frequency():
    policy = GalvanostaticEISPolicy(freq_min_hz=10.0, freq_max_hz=10_000.0,
                                    points_per_decade=4, amplitude_ua=10.0,
                                    mode="fast")
    runner, stim, scope = _sim_runner(policy)
    result = runner.run()
    stim.close(); scope.close()
    assert not result.aborted
    caps = result.session.runs[0].captures
    assert len(caps) >= 6                        # ~3 decades × 4/decade, deduped
    # Every capture carries its frequency + a finite impedance point.
    for c in caps:
        assert np.isfinite(c.metrics.eis_freq_hz)
        assert np.isfinite(c.metrics.z_mag_ohm)
        assert np.isfinite(c.metrics.z_phase_deg)
    # Frequencies are strictly increasing (sorted sweep).
    fr = [c.metrics.eis_freq_hz for c in caps]
    assert fr == sorted(fr)


def test_persistence_round_trips_impedance(tmp_path):
    from stimtest.persistence import save_session_npz, load_session_npz
    policy = GalvanostaticEISPolicy(freq_min_hz=100.0, freq_max_hz=1000.0,
                                    points_per_decade=3, mode="normal")
    runner, stim, scope = _sim_runner(policy)
    result = runner.run()
    stim.close(); scope.close()
    p = save_session_npz(result.session, tmp_path / "eis.npz")
    loaded = load_session_npz(p)
    c0 = loaded.runs[0].captures[0].metrics
    src = result.session.runs[0].captures[0].metrics
    assert abs(c0.eis_freq_hz - src.eis_freq_hz) < 1e-6
    assert abs(c0.z_mag_ohm - src.z_mag_ohm) < 1e-3 or (
        np.isnan(c0.z_mag_ohm) and np.isnan(src.z_mag_ohm))


# ------------------------------------------------------------ GUI: plots
def test_bode_nyquist_plots_accept_spectrum():
    from stimtest.gui.eis_plots import BodePlot, NyquistPlot
    freqs = [1, 10, 100, 1000, 10000]
    zmag = [5e4, 2e4, 8e3, 3e3, 1e3]
    zph = [-80, -70, -55, -30, -10]
    zre = [8e3, 7e3, 6e3, 2.5e3, 9e2]
    zim = [-4.9e4, -1.8e4, -5e3, -1e3, -2e2]
    b = BodePlot(); b.set_spectrum(freqs, zmag, zph, zre, zim)
    n = NyquistPlot(); n.set_spectrum(freqs, zmag, zph, zre, zim)
    assert len(b._z_mag) == 5 and len(b._z_phase) == 5
    assert len(n._z_real) == 5
    # Bode magnitude is log-log (the repo's first log axes).
    assert b.mag_plot.getViewBox().state["logMode"] == [True, True]
    b.set_grid_visible(True); n.set_grid_visible(True)
    b.deleteLater(); n.deleteLater()


# ------------------------------------------------------------ GUI: tab
def test_eis_tab_builds_and_registers():
    from stimtest.electrode import ElectrodeArray
    from stimtest.gui.experiment_tabs import GalvanostaticEISTab
    tab = GalvanostaticEISTab(ElectrodeArray.utah_4x4())
    assert tab.LOG_TAG == "EIS" and tab.SINGLE_CONFIG is True
    assert tab.experiment_type() == "EIS"
    assert [lbl for lbl, _ in tab._extra_experiment_tabs()] == ["Bode", "Nyquist"]
    # prefs round-trip
    tab.eis_mode.setCurrentIndex(2); tab.eis_shape.setCurrentIndex(1)
    p = tab.current_prefs()
    tab2 = GalvanostaticEISTab(ElectrodeArray.utah_4x4())
    tab2.restore_prefs(p)
    assert tab2.eis_mode.currentData() == "low_noise"
    assert tab2.eis_shape.currentData() == "rectangular"
    tab.deleteLater(); tab2.deleteLater()


def test_eis_in_experiment_registry():
    from stimtest.config import EXPERIMENTS
    assert "EIS" in EXPERIMENTS
    assert EXPERIMENTS["EIS"].label == (
        "Galvanostatic Electrochemical Impedance Spectroscopy")


def test_eis_capture_feeds_spectrum_plots():
    from stimtest.electrode import ElectrodeArray
    from stimtest.gui.experiment_tabs import GalvanostaticEISTab
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    tab = GalvanostaticEISTab(ElectrodeArray.utah_4x4())
    # Two synthetic EIS captures → the Bode/Nyquist should show 2 points.
    for i, (f, zm, zp, zr, zi) in enumerate((
            (100.0, 8e3, -40.0, 6e3, -5e3),
            (1000.0, 3e3, -20.0, 2.8e3, -1e3))):
        c = Capture(index=i, pattern=PulsePattern.biphasic(amplitude_ua=10.0))
        c.metrics.eis_freq_hz = f; c.metrics.z_mag_ohm = zm
        c.metrics.z_phase_deg = zp; c.metrics.z_real_ohm = zr
        c.metrics.z_imag_ohm = zi
        tab._on_capture(c, "CH01")
    assert len(tab.bode_plot._z_mag) == 2
    assert len(tab.nyquist_plot._z_real) == 2
    tab.deleteLater()
