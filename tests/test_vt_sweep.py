"""Tests for the Voltage-Transient multi-parameter sweep (rate / asymmetry)."""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, SweepPoint, VoltageTransientExperiment, pattern_for_sweep_point,
)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


# ---------------------------------------------------------------------------
# pattern_for_sweep_point
# ---------------------------------------------------------------------------
def test_sweep_point_overrides_rate_only():
    base = PulsePattern.biphasic(amplitude_ua=50, phase_width_us=200,
                                 rate_hz=50)
    out = pattern_for_sweep_point(base, SweepPoint(rate_hz=200))
    assert out.rate_hz == 200
    # Phases untouched when no width ratio is given.
    assert [p.width_us for p in out.phases] == [p.width_us for p in base.phases]
    assert [p.amplitude_ua for p in out.phases] == \
           [p.amplitude_ua for p in base.phases]


def test_sweep_point_none_rate_keeps_base_rate():
    base = PulsePattern.biphasic(amplitude_ua=50, rate_hz=37)
    out = pattern_for_sweep_point(base, SweepPoint(rate_hz=None))
    assert out.rate_hz == 37


def test_sweep_point_width_ratio_stays_charge_balanced():
    base = PulsePattern.biphasic(amplitude_ua=50, phase_width_us=200,
                                 rate_hz=50)  # symmetric, balanced
    out = pattern_for_sweep_point(base, SweepPoint(width_ratio=4.0))
    w1, w2 = out.phases[0].width_us, out.phases[1].width_us
    # Recharge phase is 4× wider than the excitation phase.
    assert w2 == pytest.approx(w1 * 4.0)
    # ...and its amplitude dropped so the net charge is still ~zero.
    assert out.net_charge_nc == pytest.approx(0.0, abs=1e-9)
    # The wider phase carries the lower magnitude.
    assert abs(out.phases[1].amplitude_ua) < abs(out.phases[0].amplitude_ua)


def test_sweep_point_width_ratio_ignored_for_triphasic():
    base = PulsePattern.triphasic(amp_excite_ua=60, phase_width_us=100,
                                  rate_hz=50)
    out = pattern_for_sweep_point(base, SweepPoint(width_ratio=3.0,
                                                   rate_hz=100))
    # Rate still applies; three-phase widths are left alone.
    assert out.rate_hz == 100
    assert [p.width_us for p in out.phases] == [p.width_us for p in base.phases]


# ---------------------------------------------------------------------------
# Runner end-to-end with a sweep
# ---------------------------------------------------------------------------
def _make_session():
    array = ElectrodeArray.utah_4x4()
    config = Configuration.bipolar(active=10, ret=11)
    pattern = PulsePattern.biphasic(amplitude_ua=5, polarity=-1, rate_hz=50)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=config, array=array)
    return Session(notebook="t", subject="s", test=test), config


def test_vt_sweep_produces_one_run_per_point():
    session, _config = _make_session()
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()

    points = [
        SweepPoint(rate_hz=50, label="50pps"),
        SweepPoint(rate_hz=200, label="200pps"),
        SweepPoint(rate_hz=200, width_ratio=2.0, label="200pps_asym2x"),
    ]
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(coarse_step_ua=40, fine_step_ua=10,
                        max_ua=300),
        sweep_points=points,
    )
    result = runner.run()

    # One ChannelRun per sweep point, each tagged with its label, and the
    # per-run pattern reflects the requested rate.
    assert len(session.runs) == 3
    assert [r.label for r in session.runs] == \
           ["50pps", "200pps", "200pps_asym2x"]
    assert session.runs[0].captures[0].pattern.rate_hz == 50
    assert session.runs[1].captures[0].pattern.rate_hz == 200
    # Asymmetric point: recharge phase wider than excitation phase.
    asym_pat = session.runs[2].captures[0].pattern
    assert asym_pat.phases[1].width_us > asym_pat.phases[0].width_us
    assert not result.aborted
    stim.close(); scope.close()


def test_vt_no_sweep_matches_single_run():
    """sweep_points=None keeps the original single-run behaviour."""
    session, _config = _make_session()
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(coarse_step_ua=40, max_ua=200),
        sweep_points=None,
    )
    runner.run()
    assert len(session.runs) == 1
    assert session.runs[0].label == ""
    stim.close(); scope.close()


# ---------------------------------------------------------------------------
# Persistence round-trip of the run label
# ---------------------------------------------------------------------------
def test_fit_scope_window_uses_1us_delay_and_widest_pulse():
    """_fit_scope_window sizes to the widest sweep pulse, delay fixed at 1 µs."""
    session, _config = _make_session()
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()

    calls = []

    def fake_fit(**kw):
        calls.append(kw)
        return (50e-6, 30.0)  # (scale_s, position_pct)

    # The simulator has no auto_layout_for_pulse; attach a recorder and
    # tell it there's no EXT input (the TBS1072C case).
    scope.auto_layout_for_pulse = fake_fit
    scope.info.has_ext_trigger = False

    base_total = session.test.pattern.total_pulse_us
    runner = VoltageTransientExperiment(
        session, stim, scope,
        sweep_points=[
            SweepPoint(rate_hz=50, label="sym"),
            SweepPoint(rate_hz=50, width_ratio=3.0, label="asym3x"),  # widest
        ],
    )
    runner._fit_scope_window()

    assert len(calls) == 1
    kw = calls[0]
    assert kw["digital_delay_us"] == 1.0
    assert kw["ext_trigger"] is False
    # Asymmetric point widens the recharge phase, so the fitted pulse
    # width must exceed the symmetric base total.
    assert kw["phase1_us"] > base_total
    stim.close(); scope.close()


def test_fit_scope_window_noop_without_helper():
    """On a backend lacking auto_layout_for_pulse (the sim), it's a no-op."""
    session, _config = _make_session()
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    assert not hasattr(scope, "auto_layout_for_pulse")
    runner = VoltageTransientExperiment(session, stim, scope)
    runner._fit_scope_window()  # must not raise
    stim.close(); scope.close()


def test_run_label_round_trips_through_npz(tmp_path):
    from stimtest.persistence import load_session_npz, save_session_npz

    session, _config = _make_session()
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(coarse_step_ua=40, max_ua=200),
        sweep_points=[SweepPoint(rate_hz=200, width_ratio=2.0,
                                 label="200pps_asym2x")],
    )
    runner.run()
    stim.close(); scope.close()

    path = tmp_path / "sweep.npz"
    save_session_npz(session, path)
    reloaded = load_session_npz(path)
    assert [r.label for r in reloaded.runs] == ["200pps_asym2x"]
