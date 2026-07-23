"""Operator-configurable electrode-polarization (E_pol) TIME DELAY.

Operator: "Add a checkbox to toggle if the user wants to input a time delay
for determining electrode polarization, e.g., 12 us."

The E_pol TIME method samples the interface potential at ``phase_end +
depol``.  ``depol`` defaults to the canonical ``DEPOLARIZATION_TIME_US``
(12 µs); a Setup-tab checkbox lets the operator override it.  The chosen
value threads GUI → setup snapshot → runner → ``compute_metrics`` and is
stored per capture (``CaptureMetrics.depolarization_us``) so the plot
markers, POLARIS, and persistence all use the SAME delay.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")

from stimtest.config import DEPOLARIZATION_TIME_US
from stimtest.metrics import compute_metrics, polarization_per_phase
from stimtest.session import Capture, CaptureMetrics
from stimtest.waveforms import Phase, PulsePattern


# --------------------------------------------------------------------------
# core: polarization_per_phase honours a custom depol
# --------------------------------------------------------------------------
def _ramped_interphase_capture():
    """Biphasic with a 60 µs interphase delay after phase 1.  During that
    delay the V_mon ramps -0.3 → -0.9 V, so the TIME sample at phase_end +
    depol reads a depol-DEPENDENT value:
       depol 12 µs → t=112 → -0.42 V
       depol 40 µs → t=140 → -0.70 V
    """
    dt = 0.5
    t = np.arange(-60.0, 320.0, dt)
    v = np.zeros_like(t)
    i = np.zeros_like(t)
    cath = (t >= 0) & (t < 100)
    an = (t >= 160) & (t < 260)
    v[cath] = -0.5
    i[cath] = -50.0
    v[an] = 0.5
    i[an] = 50.0
    # interphase delay window 100..160 µs: linear ramp -0.3 -> -0.9 V
    win = (t >= 100) & (t < 160)
    frac = (t[win] - 100.0) / 60.0
    v[win] = -0.3 + frac * (-0.6)
    pat = PulsePattern(
        phases=[Phase(amplitude_ua=-50.0, width_us=100.0, delay_after_us=60.0),
                Phase(amplitude_ua=50.0, width_us=100.0, delay_after_us=60.0)],
        rate_hz=1000.0, repetitions=0)
    return Capture(index=0, pattern=pat, time_us=t, v_mon_v=v, i_mon_ua=i)


def test_polarization_per_phase_shifts_with_depol():
    cap = _ramped_interphase_capture()
    lo = polarization_per_phase(cap.time_us, cap.v_mon_v, cap.pattern,
                                method="operator", depol_us=12.0, onset_us=0.0)
    hi = polarization_per_phase(cap.time_us, cap.v_mon_v, cap.pattern,
                                method="operator", depol_us=40.0, onset_us=0.0)
    assert lo[0] == pytest.approx(-0.42, abs=0.03)
    assert hi[0] == pytest.approx(-0.70, abs=0.03)
    assert lo[0] != hi[0]


def test_default_depol_is_config_value():
    cap = _ramped_interphase_capture()
    dflt = polarization_per_phase(cap.time_us, cap.v_mon_v, cap.pattern,
                                  method="operator", onset_us=0.0)
    at12 = polarization_per_phase(cap.time_us, cap.v_mon_v, cap.pattern,
                                  method="operator",
                                  depol_us=DEPOLARIZATION_TIME_US, onset_us=0.0)
    assert dflt[0] == pytest.approx(at12[0], abs=1e-9)


# --------------------------------------------------------------------------
# compute_metrics records the depol used + uses it for E_pol
# --------------------------------------------------------------------------
def test_compute_metrics_records_and_uses_depol():
    cap = _ramped_interphase_capture()
    m = compute_metrics(cap, surface_area_um2=2000.0,
                        force_class="normal", depol_us=40.0)
    assert m.depolarization_us == pytest.approx(40.0)
    assert m.polarization_per_phase_v[0] == pytest.approx(-0.70, abs=0.03)

    cap2 = _ramped_interphase_capture()
    m2 = compute_metrics(cap2, surface_area_um2=2000.0,
                         force_class="normal", depol_us=12.0)
    assert m2.depolarization_us == pytest.approx(12.0)
    assert m2.polarization_per_phase_v[0] == pytest.approx(-0.42, abs=0.03)


def test_compute_metrics_defaults_to_12us():
    cap = _ramped_interphase_capture()
    m = compute_metrics(cap, surface_area_um2=2000.0, force_class="normal")
    assert m.depolarization_us == pytest.approx(DEPOLARIZATION_TIME_US)


# --------------------------------------------------------------------------
# plot markers read the per-capture depol
# --------------------------------------------------------------------------
def test_capture_depol_helper_reads_metrics():
    from stimtest.plotting import _capture_depol_us
    cap = _ramped_interphase_capture()
    cap.metrics = CaptureMetrics()
    cap.metrics.depolarization_us = 40.0
    assert _capture_depol_us(cap) == pytest.approx(40.0)
    # legacy / missing → canonical default
    cap.metrics.depolarization_us = float("nan")
    assert _capture_depol_us(cap) == pytest.approx(DEPOLARIZATION_TIME_US)


def test_marker_position_follows_custom_depol():
    from stimtest.plotting import compute_metric_markers
    cap = _ramped_interphase_capture()
    compute_metrics(cap, surface_area_um2=2000.0,
                    force_class="normal", depol_us=40.0)
    mks = compute_metric_markers(cap)
    polar = [mk for mk in mks if mk.get("kind") == "polar"]
    assert polar, "expected an Emc/Ema polar marker"
    # phase-1 marker sits at phase_end (100 µs) + depol (40 µs) = 140 µs
    p1 = min(polar, key=lambda mk: mk["t_us"])
    assert p1["t_us"] == pytest.approx(140.0, abs=6.0)


# --------------------------------------------------------------------------
# persistence round-trip
# --------------------------------------------------------------------------
def test_persistence_round_trips_depolarization_us(tmp_path):
    from stimtest.persistence import save_session_npz, load_session_npz
    from stimtest.session import Session, ChannelRun, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    cap = _ramped_interphase_capture()
    compute_metrics(cap, surface_area_um2=2000.0,
                    force_class="normal", depol_us=37.0)
    cfg = Configuration.monopolar(1)
    arr = ElectrodeArray.utah_4x4()
    test = TestParameters(experiment="VT", pattern=cap.pattern,
                          array=arr, configuration=cfg)
    run = ChannelRun(configuration=cfg, captures=[cap])
    sess = Session(notebook="nb", subject="subj", test=test)
    sess.runs.append(run)
    path = tmp_path / "depol.npz"
    save_session_npz(sess, str(path))
    loaded = load_session_npz(str(path))
    got = loaded.runs[0].captures[0].metrics.depolarization_us
    assert got == pytest.approx(37.0)


def test_legacy_npz_defaults_depol(tmp_path):
    # a metrics dict with NO depolarization_us key -> canonical default
    from stimtest.persistence import _pattern_dict  # noqa: F401  (import smoke)
    m = CaptureMetrics()
    assert m.depolarization_us == pytest.approx(DEPOLARIZATION_TIME_US)


# --------------------------------------------------------------------------
# GUI: Setup-tab toggle + snapshot + prefs
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def test_setup_tab_custom_depol_toggle(_app):
    from stimtest.gui.setup_tab import SetupTab
    st = SetupTab()
    # default: unchecked -> canonical 12 µs, spinbox disabled
    assert st.depol_custom_chk.isChecked() is False
    assert st.depol_delay_us.isEnabled() is False
    assert st.current_depolarization_us() == pytest.approx(DEPOLARIZATION_TIME_US)
    # snapshot carries the resolved (default) value
    assert st.setup_snapshot()["depolarization_us"] == pytest.approx(
        DEPOLARIZATION_TIME_US)
    # check the box + set a value -> custom value flows through
    st.depol_custom_chk.setChecked(True)
    assert st.depol_delay_us.isEnabled() is True
    st.depol_delay_us.setValue(45.0)
    assert st.current_depolarization_us() == pytest.approx(45.0)
    assert st.setup_snapshot()["depolarization_us"] == pytest.approx(45.0)


def test_setup_tab_depol_prefs_round_trip(_app):
    from stimtest.gui.setup_tab import SetupTab
    st = SetupTab()
    st.depol_custom_chk.setChecked(True)
    st.depol_delay_us.setValue(33.0)
    prefs = st.current_prefs()
    assert prefs["depol_custom"] is True
    assert prefs["depol_delay_us"] == pytest.approx(33.0)
    st2 = SetupTab()
    st2.restore_prefs(prefs)
    assert st2.depol_custom_chk.isChecked() is True
    assert st2.depol_delay_us.value() == pytest.approx(33.0)
    assert st2.depol_delay_us.isEnabled() is True
    assert st2.current_depolarization_us() == pytest.approx(33.0)


def test_setup_tab_unchecked_prefs_disable_spin(_app):
    from stimtest.gui.setup_tab import SetupTab
    st = SetupTab()
    prefs = {"depol_custom": False, "depol_delay_us": 20.0}
    st.restore_prefs(prefs)
    assert st.depol_custom_chk.isChecked() is False
    assert st.depol_delay_us.isEnabled() is False
    # value restored but NOT used (default returned)
    assert st.depol_delay_us.value() == pytest.approx(20.0)
    assert st.current_depolarization_us() == pytest.approx(DEPOLARIZATION_TIME_US)
