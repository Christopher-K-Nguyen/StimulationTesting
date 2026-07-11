"""Per-capture pulse count + cumulative delivered charge.

Operator: "Number of pulses should be the duration of the pulsing
multiplied by the pulse rate" (chose per-capture = the averaging count)
AND "I also want cumulative charge at each capture, building upon previous
captures."

``ExperimentRunner._record_capture_dose`` sets, on each capture:
  * ``metrics.n_pulses``           = the scope averaging count (= pulses
                                     delivered to acquire that capture).
  * ``metrics.cumulative_charge_nc`` = Σ |Q_ph_i| × n_pulses_i over the
                                     run's captures up to + including this
                                     one (resets per ChannelRun).
"""
from __future__ import annotations

import math

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, ChannelRun, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner(navg=64):
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    scope._expected_acq_navg = navg
    runner = VoltageTransientExperiment(session, stim, scope,
                                        ramp=RampPolicy(strategy="adaptive"))
    return runner, stim, scope


def _cap(i, q_ph_nc):
    c = Capture(index=i, pattern=PulsePattern.biphasic(amplitude_ua=10.0 * (i + 1)))
    c.metrics.charge_per_phase_nc = q_ph_nc
    return c


def test_n_pulses_respects_runner_measured_value():
    """The runner measures the actual pulsing elapsed time (start_all →
    stop_all) × rate and PRE-SETS n_pulses; the helper must honour it and
    NOT override with the averaging count (operator: "the number of pulses
    must be calculated from the elapsed time of starting and stopping the
    pulsing")."""
    runner, stim, scope = _runner(navg=64)
    try:
        run = ChannelRun(configuration=Configuration.monopolar(1))
        c = _cap(0, 100.0)
        c.metrics.n_pulses = 5000.0   # e.g. 50 s pulsing × 100 Hz
        run.captures.append(c)
        runner._record_capture_dose(run, c)
        assert c.metrics.n_pulses == 5000.0   # NOT clobbered by navg=64
        assert c.metrics.cumulative_charge_nc == 100.0 * 5000.0
    finally:
        stim.close(); scope.close()


def test_n_pulses_falls_back_to_averaging_count_when_not_measured():
    """When the runner didn't measure a pulsing time (NaN — simulator /
    early abort), fall back to the scope averaging count."""
    runner, stim, scope = _runner(navg=64)
    try:
        run = ChannelRun(configuration=Configuration.monopolar(1))
        c = _cap(0, 100.0)   # n_pulses left NaN
        run.captures.append(c)
        runner._record_capture_dose(run, c)
        assert c.metrics.n_pulses == 64.0
    finally:
        stim.close(); scope.close()


def test_n_pulses_falls_back_to_one_without_averaging():
    runner, stim, scope = _runner(navg=0)   # no averaging count known
    try:
        run = ChannelRun(configuration=Configuration.monopolar(1))
        c = _cap(0, 100.0)
        run.captures.append(c)
        runner._record_capture_dose(run, c)
        assert c.metrics.n_pulses == 1.0
    finally:
        stim.close(); scope.close()


def test_cumulative_charge_builds_across_captures():
    runner, stim, scope = _runner(navg=10)
    try:
        run = ChannelRun(configuration=Configuration.monopolar(1))
        c1 = _cap(0, 100.0); run.captures.append(c1); runner._record_capture_dose(run, c1)
        c2 = _cap(1, 200.0); run.captures.append(c2); runner._record_capture_dose(run, c2)
        c3 = _cap(2, 300.0); run.captures.append(c3); runner._record_capture_dose(run, c3)
        # Each capture: |Q_ph| × n_pulses (10).  Cumulative builds.
        assert c1.metrics.cumulative_charge_nc == 100.0 * 10          # 1000
        assert c2.metrics.cumulative_charge_nc == (100.0 + 200.0) * 10  # 3000
        assert c3.metrics.cumulative_charge_nc == (100.0 + 200.0 + 300.0) * 10  # 6000
        # Cumulative N_pulse = running Σ n_pulses (10 each).
        assert c1.metrics.cumulative_n_pulses == 10
        assert c2.metrics.cumulative_n_pulses == 20
        assert c3.metrics.cumulative_n_pulses == 30
    finally:
        stim.close(); scope.close()


def test_cumulative_skips_nan_charge():
    runner, stim, scope = _runner(navg=10)
    try:
        run = ChannelRun(configuration=Configuration.monopolar(1))
        c1 = _cap(0, float("nan"))   # empty/bad capture
        run.captures.append(c1); runner._record_capture_dose(run, c1)
        c2 = _cap(1, 50.0)
        run.captures.append(c2); runner._record_capture_dose(run, c2)
        assert c1.metrics.cumulative_charge_nc == 0.0
        assert c2.metrics.cumulative_charge_nc == 50.0 * 10
    finally:
        stim.close(); scope.close()


def test_dose_persists_round_trip(tmp_path):
    from stimtest.persistence import save_session_npz, load_session_npz
    runner, stim, scope = _runner(navg=32)
    try:
        run = ChannelRun(configuration=Configuration.monopolar(1))
        c = _cap(0, 188.0)
        c.time_us = np.linspace(-10, 100, 50)
        c.v_mon_v = np.zeros(50)
        c.i_mon_ua = np.zeros(50)
        run.captures.append(c); runner._record_capture_dose(run, c)
        runner.session.add_run(run)
        path = tmp_path / "dose.npz"
        save_session_npz(runner.session, path)
        loaded = load_session_npz(path)
        lc = loaded.runs[0].captures[0]
        assert lc.metrics.n_pulses == 32.0
        assert lc.metrics.cumulative_charge_nc == 188.0 * 32
        assert lc.metrics.cumulative_n_pulses == 32.0
    finally:
        stim.close(); scope.close()


def test_fmt_cumulative_charge_autoscales():
    from stimtest.gui.widgets import _fmt_cumulative_charge
    assert _fmt_cumulative_charge(500.0).endswith("nC")
    assert _fmt_cumulative_charge(50_000.0).endswith("µC")
    assert _fmt_cumulative_charge(5_000_000.0).endswith("mC")


def test_n_pulses_survives_compute_metrics_end_to_end(monkeypatch):
    """END-TO-END: the VT runner sets n_pulses = measured pulsing-duration ×
    rate, and it MUST survive the compute_metrics call in the same capture.
    Regression for the wipe bug: n_pulses was set BEFORE compute_metrics, which
    builds a fresh CaptureMetrics and replaced cap.metrics — so the measured
    value was silently discarded and _record_capture_dose fell back to the
    averaging count (operator: "the number of pulses correlates with the
    duration of the pulsing not the trigger/average count")."""
    import time as _time
    navg = 8
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1, rate_hz=1000.0)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    scope._expected_acq_navg = navg
    runner = VoltageTransientExperiment(
        session, stim, scope, configurations=[Configuration.monopolar(1)],
        ramp=RampPolicy(strategy="increment", coarse_step_ua=5.0, max_ua=10.0),
        cathodic_limit_v=-0.6, anodic_limit_v=0.8)
    # Give each acquisition a REAL, non-zero duration so the runner's
    # start_all→stop_all clock measures a meaningful pulsing time.
    for _name in ("settle_one_acquisition", "single_capture"):
        _orig = getattr(scope, _name)
        def _slow(*a, _orig=_orig, **k):
            _time.sleep(0.03)
            return _orig(*a, **k)
        monkeypatch.setattr(scope, _name, _slow)
    try:
        result = runner.run()
        caps = [c for r in result.session.runs for c in r.captures]
        assert caps, "no captures produced"
        # At 1000 Hz with ≥30 ms pulsing per capture, n_pulses ≫ navg (8);
        # crucially it is NOT the averaging count.
        for c in caps:
            assert math.isfinite(c.metrics.n_pulses)
            assert c.metrics.n_pulses > navg, (c.metrics.n_pulses, navg)
    finally:
        stim.close(); scope.close()
