"""Driving impedance (Z_d = V_d/I_stim) + driving energy (∫V·I over pulse).

Operator request: add these two as capture metrics, surfaced in the
metric table + Gamry export.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

from stimtest.metrics import compute_metrics
from stimtest.session import Capture
from stimtest.waveforms import Phase, PulsePattern
from stimtest.gui.widgets import metric_row_text as _mrt


def _synthetic_biphasic():
    """Clean biphasic: −2 V/−50 µA cathodic (0–100 µs), +2 V/+50 µA anodic
    (100–200 µs).  V_d = 2 V, I = 50 µA → Z_d = 40 kΩ; energy = 2·(2 V·50 µA)
    ·100 µs = 20 nJ."""
    dt = 0.5
    t = np.arange(-60.0, 260.0, dt)
    v = np.zeros_like(t); i = np.zeros_like(t)
    cath = (t >= 0) & (t < 100); an = (t >= 100) & (t < 200)
    v[cath] = -2.0; i[cath] = -50.0
    v[an] = 2.0; i[an] = 50.0
    pat = PulsePattern(
        phases=[Phase(amplitude_ua=-50.0, width_us=100.0, delay_after_us=0.0),
                Phase(amplitude_ua=50.0, width_us=100.0, delay_after_us=0.0)],
        rate_hz=1000.0, repetitions=0)
    return Capture(index=0, pattern=pat, time_us=t, v_mon_v=v, i_mon_ua=i)


def test_driving_impedance_is_vd_over_istim():
    cap = _synthetic_biphasic()
    m = compute_metrics(cap, surface_area_um2=2000.0)
    assert m.driving_voltage_v == pytest.approx(2.0, abs=1e-6)
    # Z_d = |V_d| / |I_stim| = 2 V / 50 µA = 40 kΩ.
    assert m.driving_impedance_kohm == pytest.approx(40.0, rel=1e-3)


def test_driving_energy_integral_over_pulse():
    cap = _synthetic_biphasic()
    m = compute_metrics(cap, surface_area_um2=2000.0)
    # 2 phases × (2 V × 50 µA) × 100 µs = 2 × 1e-4 W × 1e-4 s = 2e-8 J = 20 nJ.
    assert m.driving_energy_uj == pytest.approx(0.020, rel=0.02)  # µJ
    assert m.driving_energy_uj > 0


def test_energy_excludes_trailing_artifact():
    """A perturbation in the trailing interpulse region (after the pulse)
    must NOT contribute to the driving energy — the integral windows to the
    pulse extent [onset, last-phase-end].  (Kept SMALLER than the pulse so
    onset detection still locks onto the real pulse; making the trailing
    region robust to a LARGER artifact is the separate deferred noise fix.)"""
    cap = _synthetic_biphasic()
    base = compute_metrics(cap, surface_area_um2=2000.0).driving_energy_uj
    cap2 = _synthetic_biphasic()
    art = cap2.time_us > 220.0
    cap2.v_mon_v[art] = 0.5
    cap2.i_mon_ua[art] = 5.0
    m2 = compute_metrics(cap2, surface_area_um2=2000.0)
    assert m2.driving_energy_uj == pytest.approx(base, rel=1e-6)


def test_persist_round_trip(tmp_path: Path):
    from stimtest.electrode import Configuration
    from stimtest.persistence import save_session_npz, load_session_npz
    from stimtest.session import ChannelRun, Session, TestParameters
    from stimtest.electrode import ElectrodeArray
    cap = _synthetic_biphasic()
    compute_metrics(cap, surface_area_um2=2000.0)
    test = TestParameters(experiment="VT", pattern=cap.pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    s = Session(notebook="t", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    run.captures.append(cap)
    s.add_run(run)
    p = tmp_path / "z.npz"
    save_session_npz(s, p)
    lc = load_session_npz(p).runs[0].captures[0]
    assert lc.metrics.driving_impedance_kohm == pytest.approx(40.0, rel=1e-3)
    assert lc.metrics.driving_energy_uj == pytest.approx(cap.metrics.driving_energy_uj, rel=1e-6)


def test_gamry_summary_has_zd_and_energy():
    from stimtest.gamry_export import _summary_headers_units, _summary_row, _fmt
    headers, units = _summary_headers_units()
    assert "Energy" in headers
    # Z_d header is the rich plain label for Z subscript d.
    from stimtest.gui.rich import plain_label as _L
    assert _L("Z", "d") in headers
    zi = headers.index(_L("Z", "d"))
    ei = headers.index("Energy")
    assert units[zi] == "kΩ" and units[ei] == "µJ"
    cap = _synthetic_biphasic()
    compute_metrics(cap, surface_area_um2=2000.0)
    row = _summary_row(cap)
    assert len(row) == len(headers)
    assert row[zi] == _fmt(cap.metrics.driving_impedance_kohm)
    assert row[ei] == _fmt(cap.metrics.driving_energy_uj)


def test_fmt_energy_autoscales():
    from stimtest.gui.widgets import _fmt_energy
    assert _fmt_energy(0.02).endswith("nJ")     # 20 nJ
    assert _fmt_energy(5.0).endswith("µJ")      # 5 µJ
    assert _fmt_energy(5000.0).endswith("mJ")   # 5 mJ
    assert _fmt_energy(float("nan")) == ""


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def test_metric_table_shows_zd_and_energy(qapp):
    from stimtest.gui.widgets import MetricTable
    cap = _synthetic_biphasic()
    compute_metrics(cap, surface_area_um2=2000.0)
    tbl = MetricTable()
    tbl.show_capture(cap)
    labels = [_mrt(tbl, r)[0] for r in range(tbl.rowCount())
              if tbl.item(r, 0)]
    joined = " ".join(labels)
    assert "Z" in joined and "d" in joined        # Z_d row present
    assert any("energy" in l.lower() for l in labels)
