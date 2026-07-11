"""VT-max ramp SAFETY: the pcc electrode-damage fixes.

Operator (pcc bench run, emphatic): "the current was too high and not
properly incremented safely … some of these electrodes are ruined by the
continuous sinusoidal stimulation."  The bench data (exp_vt_max_cathodic_pcc)
showed catastrophic single-step flings — CH01 150→1000 µA, CH08 1→51 µA,
CH09 a 50 µA step → E_pol +12 V (1126% past the water window) — because the
adaptive predictor OVER-projects the crossover on a concave-UP low-Q / high-Z
electrode once its polarization signal emerges, and the seed cap's
``_signal_emerged`` gate turns OFF (a high-impedance electrode shows real
E_pol even at 1 µA).

Three coupled safety mechanisms (see gotchas):
  * EMERGED-SIGNAL GROWTH CAP — once the signal has emerged, bound the
    per-step growth, tightening as E_pol nears the limit.
  * BACK-OFF DAMAGE DETECTION — stop the back-off search (never thrash a
    ruined electrode) when E_pol RISES as the current is LOWERED.
  * (polarity signed-zero fix lives in test_polarity_sign_lock.py.)
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, ChannelRun, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner(*, cathodic=-0.6, anodic=0.6, tol=0.02, start_ua=1.0, ramp=None):
    pattern = PulsePattern.biphasic(amplitude_ua=start_ua, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=ramp or RampPolicy(strategy="adaptive", max_ua=1000.0),
        cathodic_limit_v=cathodic, anodic_limit_v=anodic,
        polarization_tolerance_v=tol)
    return runner, stim, scope


def _drive(runner, epol_of_amp):
    """Monkeypatch the runner to synthesize a capture whose active E_pol is a
    deterministic function of the excitation amplitude (concave-up etc.), so
    the ramp is hardware-free + repeatable."""
    def _fake_capture(config, pattern, capture_idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=capture_idx, pattern=pattern)
        c.metrics.polarization_per_phase_v = [epol_of_amp(amp), 0.0]
        c.metrics.charge_injection_mc_per_cm2 = amp / 100.0
        c.metrics.response_class = "normal"
        c.time_us = np.zeros(4); c.v_mon_v = np.zeros(4); c.i_mon_ua = np.zeros(4)
        return c
    runner._one_capture = _fake_capture
    runner._record_capture_dose = lambda run, cap: None
    runner.bias_step_if_armed = lambda: None
    runner.apply_default_scope_view = lambda *a, **k: None
    runner.arm_bias_feedback = lambda: None
    runner.disarm_bias_feedback = lambda: None
    runner._seed_scope_scales = lambda *a, **k: None
    return runner


def _amps_and_epols(run, limit):
    caps = [c for c in run.captures if not c.status.aborted]
    amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
    epols = [c.metrics.polarization_per_phase_v[0] for c in caps]
    ratios = [abs(e) / abs(limit) for e in epols]
    return amps, epols, ratios


# ---------------------------------------------------------------------------
# EMERGED-SIGNAL GROWTH CAP
# ---------------------------------------------------------------------------
def test_growth_cap_bounds_concave_up_overshoot():
    """A CONCAVE-UP low-Q electrode (E_pol ∝ amp²) fools the predictor into
    over-projecting the crossover → without a cap it flings the current far
    past the water window (the pcc CH01/CH09 catastrophe: E_pol to 2-12× the
    limit).  With the emerged-signal growth cap the WORST |E_pol| across the
    whole ramp stays modestly bounded (≤ ~1.6× the limit), so the electrode is
    never blasted to many times its water window."""
    limit = -0.6
    def epol(amp):
        return -0.6 * (amp / 300.0) ** 2      # crosses -0.6 at 300 µA, convex

    runner, stim, scope = _runner(cathodic=limit, start_ua=1.0)
    _drive(runner, epol)
    try:
        runner.run()
        amps, epols, ratios = _amps_and_epols(runner.session.runs[0], limit)
        worst = max(ratios)
        assert worst <= 1.6, (
            f"peak |E_pol| {worst:.2f}× the limit — growth cap failed to "
            f"bound the concave-up overshoot; amps={[round(a,1) for a in amps]}, "
            f"ratios={[round(r,2) for r in ratios]}")
        # And it still converges (reaches the band), not stuck below.
        assert any(r >= 0.95 for r in ratios), (
            f"ramp never reached the water window; ratios={[round(r,2) for r in ratios]}")
    finally:
        stim.close(); scope.close()


def test_growth_cap_prevents_high_impedance_fling():
    """A HIGH-IMPEDANCE electrode shows real E_pol even at 1 µA (so it
    "emerges" immediately and the seed cap turns OFF — the pcc CH08 1→51 µA
    fling).  The emerged-signal cap must still bound the first real step so
    E_pol never explodes."""
    limit = -0.6
    def epol(amp):
        # steep + slightly convex: 0.15 V at 1 µA already (emerged), crosses
        # -0.6 at ~13 µA.
        return -(0.15 * amp + 0.003 * amp * amp)

    runner, stim, scope = _runner(cathodic=limit, start_ua=1.0)
    _drive(runner, epol)
    try:
        runner.run()
        amps, epols, ratios = _amps_and_epols(runner.session.runs[0], limit)
        # No single step multiplies the amplitude by more than ~3.1× once the
        # signal has emerged (the emerged base cap is 3.0×; +0.1 for snap).
        growths = [amps[i + 1] / amps[i] for i in range(len(amps) - 1)
                   if amps[i] > 0]
        assert all(g <= 3.2 for g in growths), (
            f"a step grew the current more than 3.2× despite the emerged cap: "
            f"amps={[round(a,1) for a in amps]}")
        assert max(ratios) <= 1.6, (
            f"peak |E_pol| {max(ratios):.2f}× the limit; "
            f"amps={[round(a,1) for a in amps]}")
    finally:
        stim.close(); scope.close()


def test_growth_cap_does_not_stall_a_healthy_saturating_electrode():
    """A concave-DOWN saturating SIROF (E_pol ∝ √amp) is inherently SAFE (the
    predictor under-projects) — the cap must only SLOW it slightly, never
    prevent it from reaching the water window in a bounded capture count."""
    limit = -0.6
    def epol(amp):
        return -0.6 * (amp / 300.0) ** 0.5     # crosses -0.6 at 300 µA, concave-down

    runner, stim, scope = _runner(cathodic=limit, start_ua=5.0)
    _drive(runner, epol)
    try:
        runner.run()
        amps, epols, ratios = _amps_and_epols(runner.session.runs[0], limit)
        assert any(r >= 0.95 for r in ratios), (
            f"healthy electrode never reached the band; "
            f"ratios={[round(r,2) for r in ratios]}")
        # Bounded capture count (not an infinite fine-creep).
        assert len(amps) <= 25, f"took {len(amps)} captures — cap over-throttled"
    finally:
        stim.close(); scope.close()


# ---------------------------------------------------------------------------
# BACK-OFF DAMAGE DETECTION (non-monotonic E_pol)
# ---------------------------------------------------------------------------
def _direct_backoff(epol_of_amp, *, lo_amp, hi_amp, cathodic=-0.6, tol=0.02):
    from stimtest.session import ChannelRun
    runner, stim, scope = _runner(
        cathodic=cathodic, tol=tol,
        ramp=RampPolicy(strategy="adaptive", backoff_max_captures=6,
                        fine_step_ua=1.0, max_ua=1000.0))
    _drive(runner, epol_of_amp)
    base = PulsePattern.biphasic(amplitude_ua=1.0, polarity=-1)

    def _mk(amp):
        return runner._one_capture(Configuration.monopolar(1),
                                   base.scaled(amp), 0)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    lo_cap, hi_cap = _mk(lo_amp), _mk(hi_amp)
    run.captures.extend([lo_cap, hi_cap])
    runner.session.add_run(run)
    runner._backoff_to_band(Configuration.monopolar(1), base, run,
                            lo_amp=lo_amp, lo_cap=lo_cap,
                            hi_amp=hi_amp, hi_cap=hi_cap, capture_idx=2)
    return runner, run, stim, scope


def test_backoff_stops_on_damaged_electrode_nonmonotonic():
    """A DAMAGED electrode's E_pol RISES as the current is lowered (pcc CH05:
    60 µA @ 1.93 V → 30 µA @ 4.01 V).  The back-off must NOT thrash it — it
    stops on the first non-monotonic rise, notes DAMAGED, and accepts the
    tightest safe side."""
    limit = -0.6
    def epol(amp):
        # Monotonic-normal at/above 200 µA, but INVERTED below (surface
        # damage): lower current → HIGHER |E_pol|.
        if amp >= 200.0:
            return -0.6 * amp / 200.0          # ratio 1.0 at 200 µA (overshoot side)
        return -(0.66 + (200.0 - amp) * 0.02)  # rises steeply as amp falls

    # Bracket: lo 100 µA (damaged → E_pol -2.66, ratio 4.4!), hi 220 µA (-0.66).
    runner, run, stim, scope = _direct_backoff(epol, lo_amp=100.0, hi_amp=220.0,
                                               cathodic=limit)
    try:
        # A capture must carry the DAMAGED note.
        notes = " ".join((c.status.notes or "") for c in run.captures)
        assert "DAMAGED" in notes, (
            f"non-monotonic E_pol should flag DAMAGED; notes={notes!r}")
        # The run terminated (a capture is marked reached) — not left hanging.
        assert any(c.status.reached_potential_limit for c in run.captures)
    finally:
        stim.close(); scope.close()


def _cont_pattern():
    from stimtest.waveforms import PulsePattern, Phase, SHAPE_SINUSOIDAL
    return PulsePattern(
        phases=[Phase(-0.0, 100.0, 0.0, shape=SHAPE_SINUSOIDAL),
                Phase(+0.0, 100.0, 0.0, shape=SHAPE_SINUSOIDAL)],
        rate_hz=5000.0)


def _run_cont(epol_fn):
    """Drive a CONTINUOUS-sinusoidal VT-max ramp with a synthetic Ghazavi
    E_pol(amp), returning (amps, peak_ratio, blind_stopped)."""
    pat = _cont_pattern()
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope, ramp=RampPolicy(strategy="adaptive", max_ua=1000.0),
        cathodic_limit_v=-0.6, anodic_limit_v=0.6, polarization_tolerance_v=0.02)

    def _fake(cfg, p, i):
        amp = abs(float(p.excitation_phase.amplitude_ua))
        c = Capture(index=i, pattern=p)
        e = epol_fn(amp)
        c.metrics.polarization_per_phase_v = [e, -e]
        c.metrics.polarization_method = "sinusoidal"
        c.metrics.response_class = "normal"
        c.metrics.charge_injection_mc_per_cm2 = amp / 100.0
        c.time_us = np.zeros(4); c.v_mon_v = np.zeros(4); c.i_mon_ua = np.zeros(4)
        return c
    runner._one_capture = _fake
    runner._record_capture_dose = lambda run, cap: None
    runner.bias_step_if_armed = lambda: None
    runner.apply_default_scope_view = lambda *a, **k: None
    runner.arm_bias_feedback = lambda: None
    runner.disarm_bias_feedback = lambda: None
    runner._seed_scope_scales = lambda *a, **k: None
    try:
        runner.run()
        caps = [c for c in session.runs[0].captures if not c.status.aborted]
        amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
        ratios = [abs(c.metrics.polarization_per_phase_v[0]) / 0.6 for c in caps]
        blind = "SAFETY STOP" in (caps[-1].status.notes or "")
        return amps, max(ratios), blind
    finally:
        stim.close(); scope.close()


def test_continuous_ramp_is_structurally_gentle():
    """Continuous stim has NO interpulse rest, so its ramp is hard-capped to
    ``continuous_max_growth`` (1.5×) PER STEP regardless of the measured E_pol
    — even a concave-up electrode can only creep, never fling (the failure
    that ruined the electrodes)."""
    amps, peak, blind = _run_cont(lambda a: -0.6 * (a / 300.0) ** 2)
    growths = [amps[i + 1] / amps[i] for i in range(len(amps) - 1) if amps[i] > 0]
    assert all(g <= 1.55 for g in growths), (
        f"a continuous-ramp step grew > 1.5×: {[round(a, 1) for a in amps]}")
    assert peak <= 1.2, f"continuous overshoot {peak:.2f}× the limit"
    assert not blind and peak >= 0.9, "healthy electrode should reach the window"


def test_continuous_blind_ramp_stops_instead_of_flinging():
    """A continuous capture that washes out (E_pol pinned at ~0 — the bench
    NUMACq=0 failure) must STOP once the current climbs past the blind
    threshold, NOT ramp to the 1000 µA hardware ceiling blind."""
    amps, peak, blind = _run_cont(lambda a: 0.0)      # E_pol always 0 = washed
    assert blind, "flying-blind continuous ramp must SAFETY-STOP"
    assert max(amps) < 100.0, (
        f"blind ramp climbed to {max(amps)} µA before stopping")


def test_continuous_healthy_lowsignal_not_false_stopped():
    """A HEALTHY high-capacity electrode has a SMALL but RISING E_pol at low
    current — it must NOT be blind-stopped (E_pol grows step-to-step), and
    should reach its water window."""
    amps, peak, blind = _run_cont(lambda a: -0.6 * (a / 400.0) ** 2)
    assert not blind, "growing-E_pol electrode wrongly flagged flying-blind"
    assert peak >= 0.9, f"healthy electrode never reached the window ({peak:.2f})"


def test_zero_start_dampening_schedule_is_75_50_40_25_10():
    """Operator: "Change the safe steps as −75 %, −50 %, −40 %, −25 %, −10 %"
    → the first five zero-start jumps are scaled ×0.25, ×0.5, ×0.6, ×0.75,
    ×0.9; later jumps use the full prediction."""
    assert RampPolicy().seed_dampen_fractions == (0.25, 0.50, 0.60, 0.75, 0.90)


def test_backoff_normal_monotonic_does_not_false_flag_damage():
    """A NORMAL monotonic electrode (E_pol falls as current falls) must NEVER
    be flagged DAMAGED during a legitimate back-off search."""
    limit = -0.6
    def epol(amp):
        return -0.6 * amp / 200.0              # monotonic, crosses -0.6 at 200 µA
    runner, run, stim, scope = _direct_backoff(epol, lo_amp=180.0, hi_amp=220.0,
                                               cathodic=limit)
    try:
        notes = " ".join((c.status.notes or "") for c in run.captures)
        assert "DAMAGED" not in notes, (
            f"monotonic electrode wrongly flagged DAMAGED; notes={notes!r}")
        # It should land in-band normally.
        last = run.captures[-1]
        assert last.status.reached_potential_limit is True
        assert last.status.exceeded_potential_limit is False
    finally:
        stim.close(); scope.close()
