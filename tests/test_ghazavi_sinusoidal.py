"""Ghazavi & Cogan 2018 sinusoidal (KHFAC) electrode-polarization method.

Operator: "Read the Ghazavi paper on high frequency stimulation … I want to
implement this in the biphasic symmetric sinusoidal … with no interphase,
discharge, and interpulse delays."

For a continuous symmetric-biphasic sinusoid there is no current-step edge, so
polarization is obtained by decomposing the measured voltage into a resistive
(in-phase-with-I) part and the interface potential — the least-squares
projection that equals their δ = −π/2 phase decomposition.  E_mc / E_ma then
drive the water-window limit exactly like the pulsed case.
"""
from __future__ import annotations

import numpy as np

from stimtest.metrics import (compute_metrics, ghazavi_polarization,
                              is_continuous_sinusoidal)
from stimtest.session import Capture
from stimtest.waveforms import Phase, PulsePattern, SHAPE_SINUSOIDAL
from stimtest.gui.widgets import metric_row_text as _mrt


def _sine_pattern(amp_ua=-100.0, width_us=100.0, rate_hz=5000.0):
    """Continuous symmetric-biphasic sinusoid: two 100 µs half-sines, no
    delays; rate 5 kHz → period 200 µs == total pulse → zero interpulse gap."""
    phs = [
        Phase(amplitude_ua=amp_ua, width_us=width_us, shape=SHAPE_SINUSOIDAL),
        Phase(amplitude_ua=-amp_ua, width_us=width_us, shape=SHAPE_SINUSOIDAL),
    ]
    return PulsePattern(phases=phs, rate_hz=rate_hz)


# ------------------------------------------------------- detection
def test_detects_continuous_sinusoidal():
    assert is_continuous_sinusoidal(_sine_pattern())


def test_zero_amplitude_start_is_still_continuous_sinusoidal():
    """REGRESSION: a 0 µA-START KHFAC template carries SIGNED ZEROS (-0.0/+0.0);
    the old ``a0*a1 >= 0`` check wrongly rejected it (0*0 = 0 → "same sign"), so
    the base pattern read as NON-sinusoidal and the KHFAC path (Ghazavi metrics,
    corrected E′act) was skipped for the 0 µA baseline.  copysign fixes it."""
    import math
    z = PulsePattern(phases=[
        Phase(amplitude_ua=math.copysign(0.0, -1), width_us=200.0,
              shape=SHAPE_SINUSOIDAL),
        Phase(amplitude_ua=math.copysign(0.0, 1), width_us=200.0,
              shape=SHAPE_SINUSOIDAL)], rate_hz=2500.0)
    assert is_continuous_sinusoidal(z) is True
    # A genuine same-sign (non-biphasic) pair is STILL rejected.
    same = PulsePattern(phases=[
        Phase(amplitude_ua=50.0, width_us=200.0, shape=SHAPE_SINUSOIDAL),
        Phase(amplitude_ua=50.0, width_us=200.0, shape=SHAPE_SINUSOIDAL)],
        rate_hz=2500.0)
    assert is_continuous_sinusoidal(same) is False


def test_rejects_when_interpulse_gap():
    # Same shape but a low rate → big idle interpulse → NOT continuous.
    assert not is_continuous_sinusoidal(_sine_pattern(rate_hz=50.0))


def test_rejects_rectangular_and_delayed():
    from stimtest.waveforms import SHAPE_RECTANGULAR
    rect = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=100.0, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=100.0, width_us=100.0, shape=SHAPE_RECTANGULAR),
    ], rate_hz=5000.0)
    assert not is_continuous_sinusoidal(rect)
    delayed = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=100.0, shape=SHAPE_SINUSOIDAL,
              delay_after_us=20.0),
        Phase(amplitude_ua=100.0, width_us=100.0, shape=SHAPE_SINUSOIDAL),
    ], rate_hz=4545.0)
    assert not is_continuous_sinusoidal(delayed)


# ------------------------------------------------------- the decomposition
def _synth(R_v_per_ua, e_io, e_off, i0=100.0, f=5000.0, cycles=8, n=4000):
    """Synthesize V_m(t) = R·I(t) + E_off − E_io·cos(ωt) for I = I₀·sin(ωt)."""
    t_s = np.linspace(0.0, cycles / f, n, endpoint=False)
    w = 2 * np.pi * f
    i = i0 * np.sin(w * t_s)                       # µA
    v = R_v_per_ua * i + e_off - e_io * np.cos(w * t_s)   # V
    return v, i, t_s * 1e6                          # time in µs


def test_recovers_known_r_and_polarization():
    R = 0.001          # V/µA = 1 kΩ
    e_io, e_off = 0.05, 0.02
    v, i, t_us = _synth(R, e_io, e_off, i0=100.0, f=5000.0)
    g = ghazavi_polarization(v, i, t_us, rate_hz=5000.0)
    assert abs(g["r_access_kohm"] - 1.0) < 1e-3, g          # 1 kΩ
    assert abs(g["e_io_v"] - e_io) < 1e-3, g
    assert abs(g["e_off_v"] - e_off) < 1e-3, g
    assert abs(g["e_mc_v"] - (e_off - e_io)) < 1e-3, g       # −0.03
    assert abs(g["e_ma_v"] - (e_off + e_io)) < 1e-3, g       # +0.07
    assert abs(g["freq_khz"] - 5.0) < 0.1, g                 # 5 kHz


def test_no_current_returns_nan():
    v = np.linspace(0, 0.1, 500)
    i = np.zeros(500)
    g = ghazavi_polarization(v, i, None)
    assert np.isnan(g["e_mc_v"]) and np.isnan(g["r_access_kohm"])


# ------------------------------------------------------- compute_metrics wiring
def test_compute_metrics_uses_sinusoidal_method():
    R, e_io, e_off = 0.001, 0.06, 0.0
    v, i, t_us = _synth(R, e_io, e_off, i0=100.0, f=5000.0)
    cap = Capture(index=0, pattern=_sine_pattern(),
                  time_us=t_us, v_mon_v=v, i_mon_ua=i)
    m = compute_metrics(cap, surface_area_um2=5000.0)
    assert m.polarization_method == "sinusoidal"
    # polarization_per_phase_v = [E_mc, E_ma] drives the water-window limit.
    assert len(m.polarization_per_phase_v) == 2
    e_mc, e_ma = m.polarization_per_phase_v
    assert e_mc < 0 < e_ma
    assert abs(m.ghazavi_e_io_v - e_io) < 2e-3
    assert abs(m.ghazavi_r_access_kohm - 1.0) < 1e-2
    # The pulsed access/driving-per-phase are dropped (no edges in a sinusoid).
    assert m.access_voltage_per_phase_v == []


def test_sinusoidal_epol_drives_ramp_limit():
    """The Ghazavi E_mc / E_ma feed the water-window stop: a large-amplitude
    sinusoid whose E_mc crosses the cathodic limit trips
    ``_potential_limit_hit`` (operator chose "drive the ramp limit")."""
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.experiments.voltage_transient import VoltageTransientExperiment
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters

    # E_io = 0.65 V about E_off = 0 → E_mc = −0.65 V, past the −0.6 cathodic
    # limit (near edge −0.58 with a 0.02 V tolerance).
    v, i, t_us = _synth(0.001, 0.65, 0.0, i0=500.0, f=5000.0)
    cap = Capture(index=0, pattern=_sine_pattern(amp_ua=-500.0),
                  time_us=t_us, v_mon_v=v, i_mon_ua=i)
    compute_metrics(cap, surface_area_um2=5000.0)

    test = TestParameters(experiment="VT", pattern=_sine_pattern(),
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="n", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    try:
        r = VoltageTransientExperiment(
            sess, stim, scope, configurations=[Configuration.monopolar(1)],
            cathodic_limit_v=-0.6, anodic_limit_v=0.8,
            polarization_tolerance_v=0.02)
        assert r._potential_limit_hit(cap) is True
        # A small-amplitude sinusoid (E_mc ≈ −0.05) stays well inside.
        v2, i2, t2 = _synth(0.001, 0.05, 0.0, i0=40.0, f=5000.0)
        cap2 = Capture(index=0, pattern=_sine_pattern(amp_ua=-40.0),
                       time_us=t2, v_mon_v=v2, i_mon_ua=i2)
        compute_metrics(cap2, surface_area_um2=5000.0)
        assert r._potential_limit_hit(cap2) is False
    finally:
        stim.close(); scope.close()


def test_pulsed_pattern_stays_pulsed():
    """A normal rectangular biphasic keeps the pulsed method (regression)."""
    from stimtest.waveforms import SHAPE_RECTANGULAR
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=100.0, shape=SHAPE_RECTANGULAR,
              delay_after_us=20.0),
        Phase(amplitude_ua=100.0, width_us=100.0, shape=SHAPE_RECTANGULAR,
              delay_after_us=80.0),
    ], rate_hz=50.0)
    n = 600
    t_us = np.linspace(-50.0, 250.0, n)
    v = np.zeros(n); i = np.zeros(n)
    cap = Capture(index=0, pattern=pat, time_us=t_us, v_mon_v=v, i_mon_ua=i)
    m = compute_metrics(cap, surface_area_um2=5000.0)
    assert m.polarization_method == "pulsed"
    assert np.isnan(m.ghazavi_e_mc_v)


def test_khfac_marker_labels_match_values_both_polarities():
    """Audit finding: for an ANODIC-FIRST continuous sinusoid (KHFAC), the
    Emc/Ema plot markers must show the matching value (Emc negative/cathodic,
    Ema positive/anodic) — the fixed [E_mc, E_ma] order swapped them.
    polarization_per_phase_v is now ordered by phase polarity."""
    from stimtest.plotting import compute_metric_markers
    from stimtest.waveforms import SHAPE_SINUSOIDAL

    def _khfac(polarity):
        return PulsePattern(phases=[
            Phase(amplitude_ua=200.0 * polarity, width_us=100.0,
                  delay_after_us=0.0, shape=SHAPE_SINUSOIDAL),
            Phase(amplitude_ua=-200.0 * polarity, width_us=100.0,
                  delay_after_us=0.0, shape=SHAPE_SINUSOIDAL),
        ], rate_hz=5000.0)                       # period == pulse → no interpulse gap

    for polarity in (-1, +1):
        pat = _khfac(polarity)
        t = np.linspace(0.0, 200.0, 2000)
        i = 200.0 * np.sin(2 * np.pi * t / 200.0)
        v = i / 1000.0 + 0.35 * np.cos(2 * np.pi * t / 200.0)   # quadrature interface
        cap = Capture(index=3, pattern=pat, time_us=t, v_mon_v=v, i_mon_ua=i)
        m = compute_metrics(cap, surface_area_um2=1000.0)
        assert m.polarization_method == "sinusoidal"
        for mk in compute_metric_markers(cap):
            if mk["kind"] != "polar":
                continue
            lab = mk["label"]
            val = float(mk["clauses"][0][2].split()[0])
            if lab.startswith("Emc"):
                assert val < 0, (polarity, lab, val)
            elif lab.startswith("Ema"):
                assert val > 0, (polarity, lab, val)


def test_access_voltage_equals_r_times_io():
    """V_ro (access voltage amplitude) = R_access · I_o (Ghazavi eq 4)."""
    import numpy as np
    from stimtest.metrics import ghazavi_polarization
    f = 5000.0
    w = 2 * np.pi * f
    t_s = np.linspace(0.0, 8.0 / f, 4000, endpoint=False)
    t_us = t_s * 1e6
    i = 50.0 * np.sin(w * t_s)                        # 50 µA amplitude
    R = 5000.0                                        # 5 kΩ access
    v = R * 50e-6 * np.sin(w * t_s) - 0.30 * np.cos(w * t_s)  # resistive + cap
    g = ghazavi_polarization(v, i, t_us, rate_hz=f)
    assert abs(g["r_access_kohm"] - 5.0) < 0.05      # 5 kΩ
    # V_ro = R·I_o = 5000 Ω · 50 µA = 0.25 V.
    assert abs(g["v_access_v"] - 0.25) < 0.005


def test_return_ghazavi_metrics_computed_when_eret_present():
    """Operator: "report the metrics for the active and return."  When E_ret is
    recorded, the return-electrode Ghazavi decomposition (E_mc/E_ma/E_io/E_off/
    R_access/V_access) is computed alongside the active set."""
    import numpy as np
    f = 5000.0
    w = 2 * np.pi * f
    t_s = np.linspace(0.0, 8.0 / f, 4000, endpoint=False)
    t_us = t_s * 1e6
    i = 100.0 * np.sin(w * t_s)
    v_mon = 0.001 * i - 0.30 * np.cos(w * t_s)
    e_ret = 0.20 + 0.05 * np.sin(w * t_s)             # biased, resistive-ish
    cap = Capture(index=0, pattern=_sine_pattern(), time_us=t_us,
                  v_mon_v=v_mon, i_mon_ua=i, e_ret_v=e_ret)
    m = compute_metrics(cap, surface_area_um2=1000.0)
    assert m.polarization_method == "sinusoidal"
    # Active set finite.
    assert np.isfinite(m.ghazavi_e_mc_v) and np.isfinite(m.ghazavi_r_access_kohm)
    # Return set finite (computed from E_ret vs I_mon).
    for name in ("ghazavi_return_e_mc_v", "ghazavi_return_e_ma_v",
                 "ghazavi_return_e_io_v", "ghazavi_return_e_off_v",
                 "ghazavi_return_r_access_kohm", "ghazavi_return_v_access_v"):
        assert np.isfinite(getattr(m, name)), name
    # No E_ret → return set stays NaN.
    cap2 = Capture(index=0, pattern=_sine_pattern(), time_us=t_us,
                   v_mon_v=v_mon, i_mon_ua=i)
    m2 = compute_metrics(cap2, surface_area_um2=1000.0)
    assert np.isnan(m2.ghazavi_return_e_mc_v)


def test_metric_table_shows_active_and_return_rows():
    """The KHFAC metric table shows the active set with an " active" qualifier
    AND a return set with a " return" qualifier when E_ret is present."""
    import numpy as np
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.widgets import MetricTable
    f = 5000.0
    w = 2 * np.pi * f
    t_s = np.linspace(0.0, 8.0 / f, 4000, endpoint=False)
    t_us = t_s * 1e6
    i = 100.0 * np.sin(w * t_s)
    v_mon = 0.001 * i - 0.30 * np.cos(w * t_s)
    e_ret = 0.20 + 0.05 * np.sin(w * t_s)
    cap = Capture(index=0, pattern=_sine_pattern(), time_us=t_us,
                  v_mon_v=v_mon, i_mon_ua=i, e_ret_v=e_ret)
    cap.metrics = compute_metrics(cap, surface_area_um2=1000.0)
    tbl = MetricTable(); tbl.show_capture(cap)
    keys = [_mrt(tbl, r)[0] for r in range(tbl.rowCount()) if tbl.item(r, 0)]
    joined = " ".join(keys)
    # E_mc appears for BOTH active and return (qualified).
    assert any("mc" in k and "active" in k for k in keys), keys
    assert any("mc" in k and "return" in k for k in keys), keys
    # R_access shows for both too.
    assert any("access" in k and "active" in k for k in keys)
    assert any("access" in k and "return" in k for k in keys)


def test_khfac_driving_voltage_is_ac_amplitude_not_dc_peak():
    """KHFAC driving voltage = AC amplitude V_mo (= √2·RMS), NOT max|V_mon|
    (which folds in a DC offset)."""
    import numpy as np
    from stimtest.metrics import compute_metrics
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern, Phase, SHAPE_SINUSOIDAL
    f = 5000.0
    w = 2 * np.pi * f
    t_s = np.linspace(0.0, 8.0 / f, 4000, endpoint=False)
    t_us = t_s * 1e6
    i_mon = 50.0 * np.sin(w * t_s)
    v_mon = 0.5 * np.sin(w * t_s) + 0.2          # amplitude 0.5 V, DC +0.2 V
    pat = PulsePattern(phases=[
        Phase(amplitude_ua=-50, width_us=100, shape=SHAPE_SINUSOIDAL,
              delay_after_us=0.0),
        Phase(amplitude_ua=50, width_us=100, shape=SHAPE_SINUSOIDAL,
              delay_after_us=0.0)], rate_hz=f)
    cap = Capture(index=0, pattern=pat, time_us=t_us,
                  v_mon_v=v_mon, i_mon_ua=i_mon)
    m = compute_metrics(cap, surface_area_um2=1000.0)
    assert m.polarization_method == "sinusoidal"
    # Amplitude 0.5, NOT max|V_mon| = 0.7 (DC-inflated).
    assert abs(m.driving_voltage_v - 0.5) < 0.02
