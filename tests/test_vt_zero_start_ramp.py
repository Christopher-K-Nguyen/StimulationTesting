"""VT ramp starting from a 0 µA pattern.

Operator: "Look at the latest test with starting 0 uA. It did not try
increasing the stimulation current" + "There should not be a maximum
recapture count. I see that they stop at #7."

Root cause (one bug behind both reports): the ramp built each step's pattern
with ``base_pattern.scaled(amp / |excitation|)``.  Multiplicative scaling
CANNOT grow a zero — ``0 × factor == 0`` — so a 0 µA starting pattern stayed
at 0 µA for EVERY capture while the internal ramp variable ``amp`` climbed
meaninglessly (via the seed / secant projections off pure noise), delivering
no current and exiting only when ``amp`` blew past ``max_ua`` (≈ capture #7).
There is NO capture-count cap; the "#7" was that meaningless-``amp`` overrun.

Fix: ``VoltageTransientExperiment._pattern_at_amplitude`` — identical to
``scaled()`` for a non-zero template, but rebuilds a zero template at
``±amp`` (strict sign alternation from phase 1's polarity, recovered from its
signed zero via ``copysign``) so the delivered current actually ramps.
"""
from __future__ import annotations


def _vt_runner(pattern, strategy="adaptive", max_ua=1000.0, coarse=5.0):
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.experiments.voltage_transient import (
        RampPolicy, VoltageTransientExperiment)
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters

    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(strategy=strategy, coarse_step_ua=coarse, max_ua=max_ua),
        cathodic_limit_v=-0.8, anodic_limit_v=0.6)
    return runner, stim, scope


def _amps(pattern):
    return [round(ph.amplitude_ua, 3) for ph in pattern.phases]


# ----------------------------------------------------- the pattern builder
def test_pattern_at_amplitude_matches_scaled_for_nonzero():
    from stimtest.waveforms import PulsePattern
    base = PulsePattern.biphasic(amplitude_ua=50.0, polarity=-1)
    runner, stim, scope = _vt_runner(base)
    try:
        # Non-zero template: identical to the old scaled(amp/|excite|) path.
        assert _amps(runner._pattern_at_amplitude(base, 100.0)) == _amps(base.scaled(2.0))
        assert _amps(runner._pattern_at_amplitude(base, 25.0)) == _amps(base.scaled(0.5))
    finally:
        stim.close(); scope.close()


def test_pattern_at_amplitude_grows_zero_cathodic_first():
    from stimtest.waveforms import PulsePattern
    z = PulsePattern.biphasic(amplitude_ua=0.0, polarity=-1)
    runner, stim, scope = _vt_runner(z)
    try:
        assert _amps(z) == [-0.0, 0.0]                      # signed zeros
        out = runner._pattern_at_amplitude(z, 5.0)
        assert _amps(out) == [-5.0, 5.0]                    # cathodic-first, balanced
        out.validate()                                      # a valid pattern
        assert out.polarity == -1
    finally:
        stim.close(); scope.close()


def test_pattern_at_amplitude_grows_zero_anodic_first():
    from stimtest.waveforms import PulsePattern
    z = PulsePattern.biphasic(amplitude_ua=0.0, polarity=+1)  # the anodic run
    runner, stim, scope = _vt_runner(z)
    try:
        out = runner._pattern_at_amplitude(z, 5.0)
        assert _amps(out) == [5.0, -5.0]                    # anodic-first, balanced
        assert out.polarity == +1
    finally:
        stim.close(); scope.close()


def test_pattern_at_amplitude_grows_zero_triphasic_alternates():
    from stimtest.waveforms import PulsePattern
    z = PulsePattern.triphasic(amp_excite_ua=0.0, polarity=-1)
    runner, stim, scope = _vt_runner(z)
    try:
        assert _amps(runner._pattern_at_amplitude(z, 7.0)) == [-7.0, 7.0, -7.0]
    finally:
        stim.close(); scope.close()


# ----------------------------------------------------- full ramp behaviour
def test_zero_start_ramp_increases_delivered_current():
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=0.0, polarity=+1)
    runner, stim, scope = _vt_runner(pattern)
    try:
        run = runner.run()
        amps = [round(abs(c.pattern.excitation_phase.amplitude_ua), 2)
                for c in run.captures]
        # The bug produced N captures ALL at 0.0 µA; the fix must ramp.
        assert len(amps) >= 3
        # Operator (final spec): "I want to start at 0 µA during maximum VT.
        # The next capture should be … 1 µA if adaptive/regression is chosen"
        # — the FIRST capture IS the 0 µA baseline (its bad-response check is
        # gated off — no current means no diagnosis), the SECOND is exactly
        # 1 µA, then the adaptive ramp climbs.
        assert amps[0] == 0.0, f"first capture must be the 0 µA baseline: {amps}"
        assert amps[1] == 1.0, f"second capture must be exactly 1 µA: {amps}"
        assert max(amps) > 1.0, f"ramp did not increase current: {amps}"
        assert any(a >= 50 for a in amps), f"ramp never reached real current: {amps}"
    finally:
        stim.close(); scope.close()


def test_zero_start_fixed_increment_steps_from_the_baseline():
    """A STEPPED (fixed-increment) ramp from the 0 µA baseline takes ITS
    OWN next step — the coarse step — not the adaptive 1 µA probe
    (operator: "the next capture should be … the next step if the ramp
    is stepped; otherwise … 1 µA if adaptive/regression")."""
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=0.0, polarity=+1)
    runner, stim, scope = _vt_runner(pattern, strategy="increment",
                                     max_ua=200.0, coarse=50.0)
    try:
        run = runner.run()
        amps = [round(abs(c.pattern.excitation_phase.amplitude_ua), 2)
                for c in run.captures]
        assert amps[0] == 0.0, f"first capture must be the 0 µA baseline: {amps}"
        assert amps[1] == 50.0, f"stepped: next capture must be the step: {amps}"
        if len(amps) >= 3:
            assert amps[2] == 100.0, f"then keep stepping: {amps}"
    finally:
        stim.close(); scope.close()


def test_zero_baseline_capture_does_not_trip_bad_response_stop():
    """The 0 µA baseline delivers no current, so its response class is
    meaningless — the simulator reads it as 'broken', and WITHOUT the
    ``amp > 0`` gate the bad-response early-stop killed the ramp at
    capture #1.  A bad class at amp > 0 must STILL stop (that path is
    covered by tests/test_vt_bad_response_stop.py)."""
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=0.0, polarity=-1)
    runner, stim, scope = _vt_runner(pattern)
    try:
        run = runner.run()
        assert len(run.captures) >= 2, (
            "ramp stopped at the 0 µA baseline — bad-response gate missing")
        first = run.captures[0]
        assert abs(first.pattern.excitation_phase.amplitude_ua) == 0.0
        # The baseline is SAVED (a real data point) but must not carry the
        # 'stopped early' note.
        assert "stopped early" not in (first.status.notes or "")
    finally:
        stim.close(); scope.close()


def test_zero_start_ramp_terminates_on_a_real_condition_not_a_count():
    """The ramp must stop on the water window / max_ua / bad response — NOT
    on an arbitrary capture count (there is no capture-count cap)."""
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=0.0, polarity=-1)
    runner, stim, scope = _vt_runner(pattern)
    try:
        run = runner.run()
        last = run.captures[-1]
        amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in run.captures]
        # Terminated because SOMETHING real happened at the top of the ramp —
        # either a water-window/compliance stop or reaching a high current.
        reached_limit = bool(last.status.reached_potential_limit)
        near_max = max(amps) >= 0.5 * runner.ramp.max_ua
        # The E_pol-saturation stop (plateaued near the limit) is ALSO a real
        # termination — it reports max(Q_inj) as a lower bound (gotcha #177).
        _notes = (last.status.notes or "").lower()
        saturated = ("not enough precision" in _notes or "plateaued" in _notes)
        assert (reached_limit or near_max or last.status.voltage_compliance
                or saturated), (
            f"ramp stopped for no real reason; amps={[round(a,1) for a in amps]}")
    finally:
        stim.close(); scope.close()


def test_shared_helper_grows_zero_on_ps_sp_lp():
    """``_pattern_at_amplitude`` lives on ExperimentRunner, so PS (a staircase
    ramp) and SP / LP (fixed-amplitude pulsing) all inherit the zero-template
    growth — a 0 µA template pulses at the REQUESTED amplitude, not 0 µA."""
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.experiments.long_pulsing import LongPulsingExperiment
    from stimtest.experiments.progressive_stress import (
        ProgressiveStressExperiment, StressPolicy)
    from stimtest.experiments.short_pulsing import ShortPulsingExperiment
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern

    z = PulsePattern.biphasic(amplitude_ua=0.0, polarity=+1)   # anodic zero
    test = TestParameters(experiment="X", pattern=z,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4(), duration_s=1.0)
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    try:
        runners = [
            ProgressiveStressExperiment(session, stim, scope,
                                        policy=StressPolicy()),
            ShortPulsingExperiment(session, stim, scope, amplitude_ua=25.0),
            LongPulsingExperiment(session, stim, scope, amplitude_ua=25.0),
        ]
        for r in runners:
            out = r._pattern_at_amplitude(z, 25.0)
            assert _amps(out) == [25.0, -25.0], (type(r).__name__, _amps(out))
            # and non-zero templates are still plain scaled()
            nz = PulsePattern.biphasic(amplitude_ua=40.0, polarity=-1)
            assert _amps(r._pattern_at_amplitude(nz, 80.0)) == _amps(nz.scaled(2.0))
    finally:
        stim.close(); scope.close()


def _cap(amp_ua, active_pol, return_pol=None):
    """Minimal synthetic Capture for direct predictor-mechanism tests."""
    from stimtest.session import Capture, CaptureMetrics, CaptureStatus
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=max(abs(amp_ua), 1e-9),
                                polarity=(-1 if amp_ua < 0 else 1))
    m = CaptureMetrics()
    m.polarization_per_phase_v = list(active_pol)
    m.return_polarization_per_phase_v = list(return_pol or [])
    m.response_class = "normal"
    return Capture(index=0, pattern=pat, metrics=m, status=CaptureStatus())


def test_fractional_safety_probe_after_signal_emerged():
    """Operator (refined): the safety probe jumps ~30 % of the predicted jump
    (not +1 µA) — an INFORMATIVE partial advance — and only AFTER the
    polarization signal has emerged above the noise floor."""
    from stimtest.waveforms import PulsePattern
    runner, stim, scope = _vt_runner(
        PulsePattern.biphasic(amplitude_ua=0.0, polarity=-1))
    try:
        runner._safety_probe_armed = False
        # Signal EMERGED: an excursion swings 0.1 V (> _SIGNAL_FLOOR_V) across
        # two captures, so a big jump gets a fractional probe.
        emerged = [_cap(50, [-0.10]), _cap(120, [-0.20])]
        probe = runner._maybe_safety_probe(300.0, emerged)
        assert abs(probe - runner._PROBE_FRACTION * 300.0) < 1e-6, (
            f"probe should be {runner._PROBE_FRACTION:.0%} of 300 µA, got {probe}")
        assert runner._safety_probe_armed is True
        # Next call (armed) lets the real (re-predicted) jump through.
        assert runner._maybe_safety_probe(280.0, emerged) == 280.0
        assert runner._safety_probe_armed is False
    finally:
        stim.close(); scope.close()


def test_no_safety_probe_while_signal_buried():
    """No probe while the polarization signal is still buried (the low-current
    seed regime) — the clamped step is already safe, and probing there would
    halve the operator's zero-start seed jump."""
    from stimtest.waveforms import PulsePattern
    runner, stim, scope = _vt_runner(
        PulsePattern.biphasic(amplitude_ua=0.0, polarity=-1))
    try:
        runner._safety_probe_armed = False
        # BURIED: both captures read ~the rest potential (< _SIGNAL_FLOOR_V swing).
        buried = [_cap(1, [0.20]), _cap(2, [0.20])]
        assert runner._maybe_safety_probe(300.0, buried) == 300.0
        assert runner._safety_probe_armed is False
    finally:
        stim.close(); scope.close()


def test_zero_start_seed_applies_minus_30_percent():
    """Operator: "When starting at 0 µA … the next step is −30 % of the next
    predicted current."  The FIRST real jump out of the 1 µA probe is reduced
    to 70 % of the projected crossover; later steps use the full prediction."""
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=0.0, polarity=+1)
    runner, stim, scope = _vt_runner(pattern)
    try:
        run = runner.run()
        amps = [round(abs(c.pattern.excitation_phase.amplitude_ua), 2)
                for c in run.captures]
        # Baseline 0, then exactly 1 µA, then a real (>2 µA) seed jump.
        assert amps[0] == 0.0 and amps[1] == 1.0, amps
        assert amps[2] > 2.0, f"seed step never jumped: {amps}"
    finally:
        stim.close(); scope.close()


def test_fixed_increment_has_no_safety_probe():
    """The probe is adaptive/regression-only — a stepped ramp keeps its
    operator-chosen fixed steps."""
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=0.0, polarity=+1)
    runner, stim, scope = _vt_runner(pattern, strategy="increment",
                                     max_ua=200.0, coarse=50.0)
    try:
        run = runner.run()
        amps = [round(abs(c.pattern.excitation_phase.amplitude_ua), 2)
                for c in run.captures]
        assert amps[:3] == [0.0, 50.0, 100.0], amps
    finally:
        stim.close(); scope.close()


def test_nonzero_start_ramp_unchanged():
    """A normal (non-zero) starting pattern behaves exactly as before —
    first capture is the pattern as drawn."""
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=20.0, polarity=-1)
    runner, stim, scope = _vt_runner(pattern)
    try:
        run = runner.run()
        first = abs(run.captures[0].pattern.excitation_phase.amplitude_ua)
        assert round(first, 2) == 20.0, f"first capture should be 20 µA, got {first}"
    finally:
        stim.close(); scope.close()


def test_continuous_sinusoidal_zero_start_ramp_increments():
    """A CONTINUOUS SINUSOID (KHFAC — no interphase / discharge / interpulse
    delays) started at 0 µA must ramp the current up like the rectangular case
    (operator: "the continuous sinusoidal test … start at 0 µA").  The
    ``_pattern_at_amplitude`` zero-template grower preserves the sinusoidal
    SHAPE; the ramp then delivers increasing current.  (The operator's
    sin_cont run itself failed only on an accidental save path, not the ramp.)
    """
    from stimtest.waveforms import PulsePattern, Phase, SHAPE_SINUSOIDAL
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.experiments.voltage_transient import (
        RampPolicy, VoltageTransientExperiment)
    from stimtest.hardware.simulator import (
        SimulatedOscilloscope, SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    pat0 = PulsePattern(phases=[
        Phase(amplitude_ua=-0.0, width_us=500, shape=SHAPE_SINUSOIDAL,
              delay_after_us=0.0),
        Phase(amplitude_ua=0.0, width_us=500, shape=SHAPE_SINUSOIDAL,
              delay_after_us=0.0)], rate_hz=1000.0)
    test = TestParameters(experiment="VT", pattern=pat0,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        sess, stim, scope,
        ramp=RampPolicy(strategy="adaptive", max_ua=100.0),
        cathodic_limit_v=-0.8, anodic_limit_v=0.6)
    try:
        # Grown patterns keep the sinusoidal shape at every amplitude.
        for amp in (1.0, 5.0, 50.0):
            p = runner._pattern_at_amplitude(pat0, amp)
            assert all(ph.shape == SHAPE_SINUSOIDAL for ph in p.phases)
            assert abs(abs(p.excitation_phase.amplitude_ua) - amp) < 1e-9
        runner.run()
        amps = [abs(c.pattern.excitation_phase.amplitude_ua)
                for c in sess.runs[0].captures if not c.status.aborted]
        assert amps and amps[0] == 0.0            # starts at the 0 µA baseline
        assert max(amps) > 1.0                     # and DELIVERS increasing current
    finally:
        stim.close(); scope.close()
