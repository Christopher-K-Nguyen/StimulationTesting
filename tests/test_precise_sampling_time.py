"""Precise periodic sampling + fixed/elapsed time columns (PS, LP).

Operator: "Look at how my MATLAB made sure to keep the right sampling
despite not using RateControl, which, in fact, did not keep more precise
time. This is to make sure the timing is precise. In the PS and LP
experiments, have the fixed time and elapsed time columns."

The fix (port of MATLAB ``runLongPulsing.m``):

* Captures fire on a FIXED cadence grid (``n × interval`` anchored to the
  run/step start), checked against a MONOTONIC elapsed clock — NOT
  ``now + interval`` (which drifts by the per-capture work time, the
  failure mode of MATLAB's ``rateControl``).
* Each capture records ``metrics.scheduled_time_s`` (the fixed grid time
  it aimed for) and ``metrics.elapsed_time_s`` (the actual monotonic
  elapsed when it fired).  Both round-trip in the .npz and appear as the
  "Fixed Time" / "Elapsed Time" columns in the Gamry SUMMARY.
* LP excludes re-characterization windows from pulsing time (MATLAB
  ``startTime = startTime + endPause``), so the snapshot grid stays clean
  across chars.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.long_pulsing import (
    LongPulsingExperiment, LongPulsingPolicy)
from stimtest.experiments.progressive_stress import (
    ProgressiveStressExperiment, StressPolicy)
from stimtest.experiments.voltage_transient import RampPolicy
from stimtest.hardware.simulator import (
    SimulatedOscilloscope, SimulatedStimulator)
from stimtest.session import (
    Capture, ChannelRun, Session, TestParameters)
from stimtest.waveforms import Phase, PulsePattern


def _session(exp: str, duration_s: float = 0.6) -> Session:
    arr = ElectrodeArray.utah_4x4()
    cfg = Configuration.monopolar(1)
    pat = PulsePattern(
        phases=[Phase(amplitude_ua=-20.0, width_us=100.0, delay_after_us=100.0),
                Phase(amplitude_ua=20.0, width_us=100.0, delay_after_us=0.0)],
        rate_hz=400.0, repetitions=0)
    test = TestParameters(experiment=exp, pattern=pat, configuration=cfg,
                          array=arr, duration_s=duration_s)
    return Session(notebook="nb", subject="el", test=test)


def _hw(navg: int = 2):
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    scope._expected_acq_navg = navg
    return stim, scope


# ----------------------------------------------------------------------
# PS: per-step fixed grid + global monotonic elapsed
# ----------------------------------------------------------------------
def test_ps_records_scheduled_and_elapsed():
    stim, scope = _hw()
    try:
        s = _session("PS")
        ps = ProgressiveStressExperiment(
            s, stim, scope,
            policy=StressPolicy(starting_ua=10.0, step_ua=10.0, t_step_s=0.12,
                                max_ua=30.0, sampling_period_s=0.05))
        ps.run()
        caps = [c for r in s.runs for c in r.captures]
        assert len(caps) >= 3
        # Every PS capture carries both finite times.
        for c in caps:
            assert math.isfinite(c.metrics.scheduled_time_s)
            assert math.isfinite(c.metrics.elapsed_time_s)
        # Elapsed is monotonic non-decreasing across the whole staircase
        # (a single global run anchor, not per-step).
        el = [c.metrics.elapsed_time_s for c in caps]
        assert all(b >= a - 1e-9 for a, b in zip(el, el[1:]))
        # Within one step the scheduled grid steps by exactly the sampling
        # interval (fixed grid, drift-free).
        first = [c for c in caps if c.status.notes == "step=10uA"]
        sched = [c.metrics.scheduled_time_s for c in first]
        if len(sched) > 1:
            assert all(abs((b - a) - 0.05) < 1e-6
                       for a, b in zip(sched, sched[1:]))
        # Actual elapsed never precedes the scheduled grid point it fired on.
        for c in caps:
            assert c.metrics.elapsed_time_s >= c.metrics.scheduled_time_s - 1e-6
    finally:
        stim.close(); scope.close()


# ----------------------------------------------------------------------
# LP: fixed snapshot grid (drift-free) + monotonic elapsed
# ----------------------------------------------------------------------
def test_lp_snapshots_on_fixed_grid():
    stim, scope = _hw()
    try:
        s = _session("LP", duration_s=0.5)
        lp = LongPulsingExperiment(
            s, stim, scope, amplitude_ua=20.0,
            policy=LongPulsingPolicy(duration_s=0.5,
                                     characterize_every_s=1e9,  # no char
                                     capture_during_pulsing_every_s=0.08))
        lp.run()
        snaps = [c for r in s.runs for c in r.captures
                 if c.status.notes == "snapshot"]
        assert len(snaps) >= 3
        sched = [c.metrics.scheduled_time_s for c in snaps]
        # Scheduled times land EXACTLY on the 0.08 grid (0, .08, .16, …):
        # no accumulated drift even though each capture takes wall time.
        assert all(abs(round(v / 0.08) * 0.08 - v) < 1e-6 for v in sched)
        # …and step by exactly one interval each (no skipped/duped slots
        # at this cadence).
        assert all(abs((b - a) - 0.08) < 1e-6 for a, b in zip(sched, sched[1:]))
        for c in snaps:
            assert math.isfinite(c.metrics.elapsed_time_s)
            assert c.metrics.elapsed_time_s >= c.metrics.scheduled_time_s - 1e-6
    finally:
        stim.close(); scope.close()


def test_lp_char_window_does_not_drift_snapshot_grid():
    """A re-characterization PAUSES pulsing; the snapshot grid must resume
    on the same clean pulsing-time grid afterwards (MATLAB pause
    compensation ``startTime = startTime + endPause``)."""
    stim, scope = _hw()
    try:
        s = _session("LP", duration_s=0.6)
        lp = LongPulsingExperiment(
            s, stim, scope, amplitude_ua=20.0,
            policy=LongPulsingPolicy(duration_s=0.6, characterize_every_s=0.2,
                                     capture_during_pulsing_every_s=0.06),
            ramp=RampPolicy(coarse_step_ua=10.0,
                            fine_step_ua=10.0, max_ua=20.0))
        lp.run()
        caps = [c for r in s.runs for c in r.captures]
        snaps = [c for c in caps if c.status.notes == "snapshot"]
        chars = [c for c in caps if c.status.notes.startswith("char@")]
        assert chars, "expected at least one characterization window"
        # Snapshots still on the 0.06 grid despite the char pause(s).
        for c in snaps:
            v = c.metrics.scheduled_time_s
            assert abs(round(v / 0.06) * 0.06 - v) < 1e-6
        # Char sub-captures are placed on the timeline (elapsed set) but
        # carry no scheduled grid point (a burst, not a cadence slot).
        for c in chars:
            assert math.isfinite(c.metrics.elapsed_time_s)
            assert math.isnan(c.metrics.scheduled_time_s)
    finally:
        stim.close(); scope.close()


# ----------------------------------------------------------------------
# Persistence round-trip
# ----------------------------------------------------------------------
def test_time_columns_persist_round_trip(tmp_path: Path):
    from stimtest.persistence import save_session_npz, load_session_npz
    s = _session("LP")
    run = ChannelRun(configuration=Configuration.monopolar(1))
    c = Capture(index=0, pattern=s.test.pattern)
    c.time_us = np.linspace(-10, 100, 40)
    c.v_mon_v = np.zeros(40)
    c.i_mon_ua = np.zeros(40)
    c.metrics.scheduled_time_s = 1.23
    c.metrics.elapsed_time_s = 1.2456
    run.captures.append(c)
    s.add_run(run)
    path = tmp_path / "time.npz"
    save_session_npz(s, path)
    loaded = load_session_npz(path)
    lc = loaded.runs[0].captures[0]
    assert lc.metrics.scheduled_time_s == pytest.approx(1.23)
    assert lc.metrics.elapsed_time_s == pytest.approx(1.2456)


def test_legacy_npz_defaults_time_columns_to_nan(tmp_path: Path):
    """An archive written before these fields existed loads with NaN
    (the loader uses the dataclass default)."""
    from stimtest.persistence import save_session_npz, load_session_npz
    s = _session("LP")
    run = ChannelRun(configuration=Configuration.monopolar(1))
    c = Capture(index=0, pattern=s.test.pattern)
    c.time_us = np.linspace(-10, 100, 10)
    c.v_mon_v = np.zeros(10)
    c.i_mon_ua = np.zeros(10)
    # leave scheduled/elapsed at their NaN defaults
    run.captures.append(c)
    s.add_run(run)
    path = tmp_path / "legacy.npz"
    save_session_npz(s, path)
    lc = load_session_npz(path).runs[0].captures[0]
    assert math.isnan(lc.metrics.scheduled_time_s)
    assert math.isnan(lc.metrics.elapsed_time_s)


# ----------------------------------------------------------------------
# Gamry SUMMARY columns
# ----------------------------------------------------------------------
def test_gamry_summary_has_time_columns():
    from stimtest.gamry_export import _summary_headers_units, _summary_row, _fmt
    headers, units = _summary_headers_units()
    assert "Fixed Time" in headers
    assert "Elapsed Time" in headers
    # both are seconds, positioned right after "Capture"
    fi = headers.index("Fixed Time")
    ei = headers.index("Elapsed Time")
    assert headers.index("Capture") < fi < ei
    assert units[fi] == "s" and units[ei] == "s"

    c = Capture(index=3, pattern=PulsePattern.biphasic(amplitude_ua=12.0))
    c.metrics.scheduled_time_s = 30.0
    c.metrics.elapsed_time_s = 30.05
    row = _summary_row(c)
    assert len(row) == len(headers)
    assert row[fi] == _fmt(30.0)      # fixed time rendered via shared fmt
    assert row[ei] == _fmt(30.05)     # elapsed time rendered
