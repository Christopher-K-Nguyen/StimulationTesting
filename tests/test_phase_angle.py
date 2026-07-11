"""Phase angle of the voltage waveforms (V_mon, E_ret, E_act) vs I_mon,
for continuous sinusoidal (KHFAC) captures.

Operator: "I want the phase angle difference measured in the voltage waveforms
(Vmon, Eret, Eact) versus Imon for continuous sinusoidal."

The impedance/EIS phase — single-bin lock-in at the drive frequency:
  * a purely RESISTIVE voltage (in phase with I) → ~0°;
  * a purely CAPACITIVE voltage (V lags I, I = C·dV/dt) → ~−90°;
  * only populated for ``polarization_method == "sinusoidal"``.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from stimtest.metrics import phase_angle_deg, compute_metrics  # noqa: E402
from stimtest.waveforms import (Phase, PulsePattern,  # noqa: E402
                                SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR)
from stimtest.session import Capture  # noqa: E402


F_HZ = 5_000.0
W = 2 * np.pi * F_HZ


def _axes(cycles=8, n=4000):
    t_s = np.linspace(0.0, cycles / F_HZ, n, endpoint=False)
    return t_s * 1e6, t_s          # time_us, time_s


# ------------------------------------------------------------ helper: math
def test_resistive_voltage_is_zero_degrees():
    t_us, t_s = _axes()
    i = np.sin(W * t_s)                     # current
    v = 3.0 * np.sin(W * t_s)              # V in phase with I → resistive
    phi = phase_angle_deg(v, i, t_us, F_HZ)
    assert abs(phi) < 1.0                   # ≈ 0°


def test_capacitive_voltage_lags_by_90():
    t_us, t_s = _axes()
    i = np.sin(W * t_s)
    # Capacitor: I = C dV/dt → V ∝ −cos(ωt) = sin(ωt − 90°): V LAGS I by 90°.
    v = -np.cos(W * t_s)
    phi = phase_angle_deg(v, i, t_us, F_HZ)
    assert abs(phi - (-90.0)) < 1.0         # ≈ −90° (EIS capacitive)


def test_intermediate_phase_and_sign():
    t_us, t_s = _axes()
    i = np.sin(W * t_s)
    # V lags I by 45° → φ = −45°.
    v = np.sin(W * t_s - np.deg2rad(45.0))
    phi = phase_angle_deg(v, i, t_us, F_HZ)
    assert abs(phi - (-45.0)) < 1.0
    # A LEADING voltage (V leads I by 30°) → φ = +30°.
    v2 = np.sin(W * t_s + np.deg2rad(30.0))
    assert abs(phase_angle_deg(v2, i, t_us, F_HZ) - 30.0) < 1.0


def test_noise_robustness():
    rng = np.random.default_rng(0)
    t_us, t_s = _axes(n=8000)
    i = np.sin(W * t_s) + 0.05 * rng.standard_normal(t_s.size)
    v = -np.cos(W * t_s) + 0.05 * rng.standard_normal(t_s.size)
    phi = phase_angle_deg(v, i, t_us, F_HZ)
    assert abs(phi - (-90.0)) < 5.0         # lock-in integrates out the noise


def test_returns_nan_on_bad_inputs():
    t_us, t_s = _axes()
    i = np.sin(W * t_s)
    v = np.sin(W * t_s)
    assert np.isnan(phase_angle_deg(v, i, t_us, 0.0))          # f ≤ 0
    assert np.isnan(phase_angle_deg(v, i, t_us, float("nan"))) # f NaN
    assert np.isnan(phase_angle_deg(v[:4], i[:4], t_us[:4], F_HZ))  # too short
    assert np.isnan(phase_angle_deg(np.zeros_like(v), i, t_us, F_HZ))  # no AC


# ---------------------------------------------- integration: compute_metrics
def _sin_pattern():
    return PulsePattern(phases=[
        Phase(amplitude_ua=-50.0, width_us=100.0, shape=SHAPE_SINUSOIDAL,
              delay_after_us=0.0),
        Phase(amplitude_ua=50.0, width_us=100.0, shape=SHAPE_SINUSOIDAL,
              delay_after_us=0.0),
    ], rate_hz=F_HZ)


def test_compute_metrics_populates_vmon_phase_for_sinusoid():
    pat = _sin_pattern()
    t_us, t_s = _axes()
    i_mon = 50.0 * np.sin(W * t_s)
    v_mon = -0.30 * np.cos(W * t_s) + 0.02 * np.sin(W * t_s)   # mostly capacitive
    cap = Capture(index=0, pattern=pat, time_us=t_us,
                  v_mon_v=v_mon, i_mon_ua=i_mon)
    m = compute_metrics(cap, surface_area_um2=1000.0)
    assert m.polarization_method == "sinusoidal"
    assert np.isfinite(m.phase_angle_vmon_deg)
    assert -95.0 < m.phase_angle_vmon_deg < -70.0    # capacitive-dominated
    # No E_ret / E_act recorded → those stay NaN.
    assert np.isnan(m.phase_angle_eret_deg)
    assert np.isnan(m.phase_angle_eact_deg)


def test_compute_metrics_eret_and_derived_eact_phase():
    pat = _sin_pattern()
    t_us, t_s = _axes()
    i_mon = 50.0 * np.sin(W * t_s)
    v_mon = -0.30 * np.cos(W * t_s)
    e_ret = 0.10 * np.sin(W * t_s)                    # resistive-ish return
    cap = Capture(index=0, pattern=pat, time_us=t_us,
                  v_mon_v=v_mon, i_mon_ua=i_mon, e_ret_v=e_ret)
    m = compute_metrics(cap, surface_area_um2=1000.0)
    assert np.isfinite(m.phase_angle_eret_deg)
    assert abs(m.phase_angle_eret_deg) < 5.0          # E_ret ~in phase → ~0°
    # E_act is DERIVED (V_mon + E_ret) since E_ret is recorded → finite.
    assert np.isfinite(m.phase_angle_eact_deg)


def test_uses_exact_rate_not_noisy_zero_crossing_freq():
    """REGRESSION (adversarial finding): the lock-in must use the EXACT drive
    fundamental ``rate_hz``, not the zero-crossing-measured ``ghazavi_freq_khz``
    — with a NON-integer number of captured cycles + noisy current the
    zero-crossing estimate is wrong, and projecting onto that bin returned a
    plausible-but-garbage phase (a purely capacitive electrode read as ~−20°).
    A capacitive V must still read ≈ −90°.  Also: the reported frequency is
    overwritten with the exact rate so the display + phase agree."""
    rng = np.random.default_rng(7)
    # 5.3 cycles at 5 kHz (deliberately NON-integer), noisy current so the
    # zero-crossing counter mis-measures the frequency.
    t_s = np.linspace(0.0, 5.3 / F_HZ, 4000, endpoint=False)
    t_us = t_s * 1e6
    i_mon = 50.0 * np.sin(W * t_s) + 3.0 * rng.standard_normal(t_s.size)
    v_mon = -0.30 * np.cos(W * t_s)                   # purely capacitive → −90°
    cap = Capture(index=0, pattern=_sin_pattern(), time_us=t_us,
                  v_mon_v=v_mon, i_mon_ua=i_mon)
    m = compute_metrics(cap, surface_area_um2=1000.0)
    assert abs(m.phase_angle_vmon_deg - (-90.0)) < 5.0
    # Reported frequency is the EXACT drive rate (5 kHz), not the noisy count.
    assert abs(m.ghazavi_freq_khz - 5.0) < 1e-6


def test_non_sinusoidal_leaves_phase_nan():
    pat = PulsePattern.biphasic(amplitude_ua=50.0, rate_hz=100.0)  # pulsed
    t_us, t_s = _axes()
    cap = Capture(index=0, pattern=pat, time_us=t_us,
                  v_mon_v=np.sin(W * t_s), i_mon_ua=np.sin(W * t_s))
    m = compute_metrics(cap, surface_area_um2=1000.0)
    assert m.polarization_method != "sinusoidal"
    assert np.isnan(m.phase_angle_vmon_deg)


# ------------------------------------------------------------ persistence
def test_phase_angle_round_trips_npz(tmp_path):
    from stimtest.persistence import save_session_npz, load_session_npz
    from stimtest.session import Session, TestParameters, ChannelRun
    from stimtest.electrode import Configuration, ElectrodeArray
    pat = _sin_pattern()
    t_us, t_s = _axes()
    cap = Capture(index=0, pattern=pat, time_us=t_us,
                  v_mon_v=(-0.3 * np.cos(W * t_s)),
                  i_mon_ua=(50.0 * np.sin(W * t_s)))
    cap.metrics = compute_metrics(cap, surface_area_um2=1000.0)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="nb", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(cap)
    sess.runs.append(run)
    p = save_session_npz(sess, tmp_path / "phase.npz")
    loaded = load_session_npz(p)
    got = loaded.runs[0].captures[0].metrics.phase_angle_vmon_deg
    assert abs(got - cap.metrics.phase_angle_vmon_deg) < 1e-6


# ------------------------------------------------------------ metric table
def test_metric_table_shows_phase_rows():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.widgets import MetricTable
    pat = _sin_pattern()
    t_us, t_s = _axes()
    cap = Capture(index=0, pattern=pat, time_us=t_us,
                  v_mon_v=(-0.3 * np.cos(W * t_s)),
                  i_mon_ua=(50.0 * np.sin(W * t_s)),
                  e_ret_v=(0.1 * np.sin(W * t_s)))
    cap.metrics = compute_metrics(cap, surface_area_um2=1000.0)
    tbl = MetricTable(); tbl.show_capture(cap)
    keys = [tbl.item(r, 0).text() for r in range(tbl.rowCount()) if tbl.item(r, 0)]
    # φ rows render as italic-var HTML with the trace subscript + [°] unit.
    assert any("Vmon" in k and "°" in k for k in keys)
    assert any("Eret" in k and "°" in k for k in keys)


def test_metric_table_shows_imon_reference_zero():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.widgets import MetricTable
    from stimtest.session import Capture
    pat = _sin_pattern()
    t_us, t_s = _axes()
    cap = Capture(index=0, pattern=pat, time_us=t_us,
                  v_mon_v=(-0.3 * np.cos(W * t_s)),
                  i_mon_ua=(50.0 * np.sin(W * t_s)))
    cap.metrics = compute_metrics(cap, surface_area_um2=1000.0)
    tbl = MetricTable(); tbl.show_capture(cap)
    rows = {tbl.item(r, 0).text(): tbl.item(r, 1).text()
            for r in range(tbl.rowCount())
            if tbl.item(r, 0) and tbl.item(r, 1)}
    # I_mon is the phase reference → shown as +0.0° (the anchor).
    imon = [(k, v) for k, v in rows.items() if "Imon" in k and "°" in k]
    assert imon, "no I_mon phase reference row"
    assert imon[0][1] == "+0.0"
    # It sits ABOVE the V_mon phase row (reference first).
    keys = [tbl.item(r, 0).text() for r in range(tbl.rowCount()) if tbl.item(r, 0)]
    i_imon = next(i for i, k in enumerate(keys) if "Imon" in k and "°" in k)
    i_vmon = next(i for i, k in enumerate(keys) if "Vmon" in k and "°" in k)
    assert i_imon < i_vmon
