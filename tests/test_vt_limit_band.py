"""The water-window limit trip uses the MATLAB acceptance-BAND semantics.

Operator: "The tolerance is the acceptable difference."  Port of
``getAcuteWaveformData2.m`` lines 289-301 — ``polarization_tolerance_v``
(= ``2 × ACCEPTABLE_EMC_RANGE`` = 0.020 V by default) is the half-width
of an acceptance band CENTERED on the limit::

    acceptLimitMin = limitPotential - tol      # -0.82 for a -0.80 limit
    acceptLimitMax = limitPotential + tol      # -0.78
    reached = acceptLimitMin <= E_mc <= acceptLimitMax

A cathodic ramp climbing from above enters the band at the NEAR edge
``acceptLimitMax = limit + tol`` (-0.78), so that is the trip point.

Regression guard for the operator bug: "Emc reached ~-0.8 V by capture
#4, but it kept increasing and stopped at -0.843 V by capture #10."  The
old code tripped at ``limit - tol`` (-0.82, the band's FAR edge), forcing
the ramp to creep 0.02 V past the limit before stopping.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner(*, tol=0.020, cathodic=-0.8, anodic=0.6):
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope, ramp=RampPolicy(strategy="adaptive"),
        cathodic_limit_v=cathodic, anodic_limit_v=anodic,
        polarization_tolerance_v=tol)
    return runner, stim, scope


def _cap(active=(), returns=()):
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=10.0))
    c.metrics.polarization_per_phase_v = list(active)
    c.metrics.return_polarization_per_phase_v = list(returns)
    return c


def test_cathodic_trips_at_near_edge_not_far_edge():
    """limit -0.8, tol 0.02 -> trip when E_mc reaches -0.78 (limit + tol),
    NOT -0.82 (the old far-edge behaviour that overshot)."""
    runner, stim, scope = _runner(tol=0.020, cathodic=-0.8)
    try:
        # Just short of the near edge: NOT reached.
        assert runner._potential_limit_hit(_cap(active=[-0.77, 0.0])) is False
        # At the near edge (within ±tol of the limit): reached.
        assert runner._potential_limit_hit(_cap(active=[-0.78, 0.0])) is True
        # At the limit itself: reached.
        assert runner._potential_limit_hit(_cap(active=[-0.80, 0.0])) is True
        # The OLD code did NOT trip here (-0.79 > -0.82 far edge) and kept
        # ramping — the exact -0.843 overshoot bug.  Now it trips.
        assert runner._potential_limit_hit(_cap(active=[-0.79, 0.0])) is True
    finally:
        stim.close(); scope.close()


def test_one_way_overshoot_past_band_still_trips():
    """A one-way ramp can't back off, so a step that overshoots PAST the
    band's far edge (-0.85 < -0.82) must STILL stop — never keep ramping."""
    runner, stim, scope = _runner(tol=0.020, cathodic=-0.8)
    try:
        assert runner._potential_limit_hit(_cap(active=[-0.85, 0.0])) is True
    finally:
        stim.close(); scope.close()


def test_anodic_trips_at_near_edge():
    """limit +0.6, tol 0.02 -> trip when E_mc reaches +0.58 (limit - tol)."""
    runner, stim, scope = _runner(tol=0.020, anodic=0.6)
    try:
        assert runner._potential_limit_hit(_cap(active=[0.0, 0.57])) is False
        assert runner._potential_limit_hit(_cap(active=[0.0, 0.58])) is True
        assert runner._potential_limit_hit(_cap(active=[0.0, 0.60])) is True
        assert runner._potential_limit_hit(_cap(active=[0.0, 0.65])) is True
    finally:
        stim.close(); scope.close()


def test_zero_tolerance_trips_exactly_at_limit():
    """tol 0 collapses the band to the limit itself."""
    runner, stim, scope = _runner(tol=0.0, cathodic=-0.8, anodic=0.6)
    try:
        assert runner._potential_limit_hit(_cap(active=[-0.799, 0.0])) is False
        assert runner._potential_limit_hit(_cap(active=[-0.800, 0.0])) is True
        assert runner._potential_limit_hit(_cap(active=[0.599, 0.0])) is False
        assert runner._potential_limit_hit(_cap(active=[0.600, 0.0])) is True
    finally:
        stim.close(); scope.close()


def test_return_polarization_series_also_checked():
    """The return electrode's E_pol can trip the limit too."""
    runner, stim, scope = _runner(tol=0.020, cathodic=-0.8)
    try:
        assert runner._potential_limit_hit(
            _cap(active=[-0.5, 0.0], returns=[-0.79, 0.0])) is True
    finally:
        stim.close(); scope.close()


def test_nan_values_are_ignored():
    runner, stim, scope = _runner(tol=0.020, cathodic=-0.8)
    try:
        assert runner._potential_limit_hit(
            _cap(active=[float("nan"), -0.5])) is False
    finally:
        stim.close(); scope.close()


def test_reached_limit_flag_is_set_before_the_capture_is_emitted():
    """``reached_potential_limit`` must be on the capture at EMIT time so the
    LIVE metrics table shows "Limit reached? yes" for the limit-reaching
    capture — not set only at the sweep-ending break, which runs AFTER the
    capture has already been emitted to the GUI (operator: "limit reached
    should be true when a potential limit is reached").
    """
    runner, stim, scope = _runner(tol=0.020, cathodic=-0.8)
    try:
        # A single capture whose active E_pol is well past the -0.78 near
        # edge → the sweep should flag it AND stop.
        crossing = _cap(active=[-0.9, 0.0])
        crossing.metrics.response_class = "normal"   # not a bad-response early stop
        runner._one_capture = lambda config, pattern, idx: crossing

        # Record the flag as seen at emit time (a copy, so a later mutation
        # of the shared object can't retroactively "fix" the assertion).
        seen = []
        real_emit = runner._emit
        def _rec(ev):
            cap = getattr(ev, "capture", None)
            if cap is not None:
                seen.append((ev.kind, bool(cap.status.reached_potential_limit)))
            return real_emit(ev)
        runner._emit = _rec

        runner.run()

        cap_flags = [flag for kind, flag in seen if kind == "capture"]
        assert cap_flags, "no capture event was emitted"
        assert cap_flags[0] is True, (
            "the limit-reaching capture must carry reached_potential_limit=True "
            "at emit time (live table), not only after the break")
        assert crossing.status.reached_potential_limit is True
    finally:
        stim.close(); scope.close()


def test_flag_stays_false_when_below_the_band():
    """A capture that never reaches the band keeps the flag False even after
    the sweep exits (e.g. a current-limited channel that maxes out short of
    the water window)."""
    runner, stim, scope = _runner(tol=0.020, cathodic=-0.8)
    try:
        below = _cap(active=[-0.5, 0.0])          # nowhere near -0.78
        below.metrics.response_class = "normal"
        calls = {"n": 0}
        def _one(config, pattern, idx):
            calls["n"] += 1
            return below
        runner._one_capture = _one
        # Force a quick exit so we don't loop forever below the limit.  The
        # ramp starts at the pattern amplitude (10 µA), so max_ua = 10 gives
        # one capture then amp>max_ua (starting_ua was removed).
        runner.ramp.max_ua = 10.0
        seen = []
        real_emit = runner._emit
        runner._emit = lambda ev: (
            seen.append(bool(ev.capture.status.reached_potential_limit))
            if getattr(ev, "capture", None) is not None else None,
            real_emit(ev))[-1]
        runner.run()
        assert seen and seen[0] is False
        assert below.status.reached_potential_limit is False
    finally:
        stim.close(); scope.close()


# ---------------------------------------------------------------------------
# "Limit exceeded?" — distinct from "Limit reached?"  (operator: "I saw a
# channel stop at -0.644 V … and said limit reached")
# ---------------------------------------------------------------------------
def test_exceeded_flag_distinguishes_overshoot_from_in_band():
    """reached = E_pol within ±tol of the limit; exceeded = E_pol PAST the
    band's far edge (|E_pol| > |limit| + tol)."""
    runner, stim, scope = _runner(tol=0.020, cathodic=-0.6)
    try:
        # In-band (−0.60 ± 0.02 = [−0.62, −0.58]): reached, NOT exceeded.
        in_band = _cap(active=[-0.60, 0.0])
        assert runner._potential_limit_hit(in_band) is True
        assert runner._potential_limit_exceeded(in_band) is False
        # −0.644 (the operator's case): PAST the −0.62 far edge → BOTH
        # reached (it entered/passed the band) AND exceeded (overshoot).
        over = _cap(active=[-0.644, 0.0])
        assert runner._potential_limit_hit(over) is True
        assert runner._potential_limit_exceeded(over) is True
        # Near edge exactly (−0.58): reached, not exceeded.
        near = _cap(active=[-0.58, 0.0])
        assert runner._potential_limit_hit(near) is True
        assert runner._potential_limit_exceeded(near) is False
        # Below the whole band (−0.55): neither.
        below = _cap(active=[-0.55, 0.0])
        assert runner._potential_limit_hit(below) is False
        assert runner._potential_limit_exceeded(below) is False
    finally:
        stim.close(); scope.close()


def test_exceeded_flag_round_trips_through_npz(tmp_path):
    """The new CaptureStatus.exceeded_potential_limit persists + reloads."""
    import numpy as np
    from stimtest.persistence import load_session_npz, save_session_npz
    from stimtest.session import (Capture, ChannelRun, Session,
                                  TestParameters)
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=50.0)
    test = TestParameters(experiment="VT", pattern=p,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="n", subject="s", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    cap = Capture(index=0, pattern=p)
    cap.time_us = np.linspace(-50, 450, 100)
    cap.v_mon_v = np.zeros(100); cap.i_mon_ua = np.zeros(100)
    cap.status.reached_potential_limit = True
    cap.status.exceeded_potential_limit = True
    run.captures.append(cap); sess.add_run(run)
    path = tmp_path / "exceeded.npz"
    save_session_npz(sess, path)
    loaded = load_session_npz(path)
    st = loaded.runs[0].captures[0].status
    assert st.reached_potential_limit is True
    assert st.exceeded_potential_limit is True


# ---------------------------------------------------------------------------
# Bidirectional back-off on overshoot (task #101 — operator: "quitting too
# soon … enough current to reduce"; "stopped at -0.644 … did not try again")
# ---------------------------------------------------------------------------
def _epol_runner(epol_of_amp, *, cathodic=-0.6, tol=0.02, backoff=True):
    """A VT runner whose captures' E_pol is a deterministic function of the
    excitation amplitude, so the back-off search is testable without real
    hardware.  ``_one_capture`` is monkeypatched to synthesize a Capture."""
    from stimtest.experiments.voltage_transient import RampPolicy
    runner, stim, scope = _runner(tol=tol, cathodic=cathodic)
    runner.ramp = RampPolicy(strategy="adaptive", backoff_on_overshoot=backoff,
                             backoff_max_captures=6, coarse_step_ua=5.0,
                             fine_step_ua=1.0, max_ua=1000.0)

    def _fake_capture(config, pattern, capture_idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=capture_idx, pattern=pattern)
        e = epol_of_amp(amp)
        c.metrics.polarization_per_phase_v = [e, 0.0]
        c.metrics.charge_injection_mc_per_cm2 = amp / 100.0   # ~ Q ∝ amp
        c.metrics.response_class = "normal"
        import numpy as np
        c.time_us = np.zeros(4); c.v_mon_v = np.zeros(4); c.i_mon_ua = np.zeros(4)
        return c

    runner._one_capture = _fake_capture
    # Neutralize side-effecting helpers we don't exercise here.
    runner._record_capture_dose = lambda run, cap: None
    runner.bias_step_if_armed = lambda: None
    runner.apply_default_scope_view = lambda *a, **k: None
    runner.arm_bias_feedback = lambda: None
    runner.disarm_bias_feedback = lambda: None
    runner._seed_scope_scales = lambda *a, **k: None
    return runner, stim, scope


def _direct_backoff(epol_of_amp, *, lo_amp, hi_amp, cathodic=-0.6, tol=0.02,
                    max_recaptures=6):
    """Drive ``_backoff_to_band`` directly with a synthetic E_pol(amp) so
    the search is deterministic and hardware-free."""
    import numpy as np
    from stimtest.experiments.voltage_transient import RampPolicy
    from stimtest.session import ChannelRun
    from stimtest.waveforms import PulsePattern
    runner, stim, scope = _runner(tol=tol, cathodic=cathodic)
    runner.ramp = RampPolicy(strategy="adaptive", backoff_max_captures=max_recaptures,
                             fine_step_ua=1.0)
    runner._record_capture_dose = lambda run, cap: None
    runner.bias_step_if_armed = lambda: None
    base = PulsePattern.biphasic(amplitude_ua=1.0, polarity=-1)

    def _fake_capture(config, pattern, capture_idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=capture_idx, pattern=pattern)
        c.metrics.polarization_per_phase_v = [epol_of_amp(amp), 0.0]
        c.metrics.charge_injection_mc_per_cm2 = amp / 100.0
        c.metrics.response_class = "normal"
        c.time_us = np.zeros(4); c.v_mon_v = np.zeros(4); c.i_mon_ua = np.zeros(4)
        return c
    runner._one_capture = _fake_capture

    def _mk(amp):
        return _fake_capture(Configuration.monopolar(1),
                             base.scaled(amp), 0)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    lo_cap, hi_cap = _mk(lo_amp), _mk(hi_amp)
    run.captures.extend([lo_cap, hi_cap])
    runner.session.add_run(run)
    idx = runner._backoff_to_band(Configuration.monopolar(1), base, run,
                                  lo_amp=lo_amp, lo_cap=lo_cap,
                                  hi_amp=hi_amp, hi_cap=hi_cap, capture_idx=2)
    return runner, run, stim, scope


def test_backoff_search_lands_in_band():
    """Gentle E_pol slope: back-off finds an in-band amplitude between the
    safe floor (180 µA, E_pol -0.54) and the overshoot (220 µA, -0.66)."""
    def epol(amp):
        return -0.60 * amp / 200.0          # limit -0.60 at 200 µA
    runner, run, stim, scope = _direct_backoff(epol, lo_amp=180.0, hi_amp=220.0)
    try:
        last = run.captures[-1]
        assert last.status.reached_potential_limit is True
        e = last.metrics.polarization_per_phase_v[0]
        assert -0.62 <= e <= -0.58, (e, [round(abs(
            c.pattern.excitation_phase.amplitude_ua), 1) for c in run.captures])
        assert last.status.exceeded_potential_limit is False
    finally:
        stim.close(); scope.close()


def test_backoff_search_recaptures_downward():
    """The search must issue at least one RE-CAPTURE strictly between the
    bracket ends (it doesn't just accept the overshoot)."""
    def epol(amp):
        return -0.60 * amp / 200.0
    runner, run, stim, scope = _direct_backoff(epol, lo_amp=180.0, hi_amp=220.0)
    try:
        # captures[0:2] are the seeded bracket; [2:] are back-off re-captures.
        recap_amps = [abs(c.pattern.excitation_phase.amplitude_ua)
                      for c in run.captures[2:]]
        assert recap_amps, "no back-off re-capture happened"
        assert all(180.0 < a < 220.0 for a in recap_amps), recap_amps
    finally:
        stim.close(); scope.close()


def test_backoff_steep_slope_converges_and_marks_reached():
    """A STEEP slope with a bracket already narrower than the 0.1 µA testing
    resolution can't land exactly in-band; the search breaks immediately and
    marks the terminal (safe-side) capture 'reached' so the run ends on a real
    condition (not short)."""
    def epol(amp):
        # 2.0 V per µA near the limit → the 0.04 V band is ~0.02 µA wide,
        # narrower than the 0.1 µA testing resolution → no capture lands in it.
        return -(0.60 + (amp - 200.0) * 2.0)
    # Pre-narrowed bracket (0.04 µA < the 0.1 µA testing resolution): the
    # search breaks immediately and accepts the SAFE side.  lo → −0.56 V
    # (below band), hi → −0.64 V (past the −0.62 far edge).
    runner, run, stim, scope = _direct_backoff(epol, lo_amp=199.98, hi_amp=200.02)
    try:
        # lo_cap (the safe side, index 0 of the seeded bracket) is accepted.
        lo_cap = run.captures[0]
        assert lo_cap.status.reached_potential_limit is True
        assert "crossover bracketed" in (lo_cap.status.notes or "")
        assert lo_cap.status.exceeded_potential_limit is False
    finally:
        stim.close(); scope.close()


# ---------------------------------------------------------------------------
# Back-off DECREMENT gate (operator, RECURRING + emphatic: "the maximum VT
# testing is not continuing to DECREMENT the current if it exceeds the
# potential limits").  The entry gate used ``> fine_step_ua`` (1 µA), so a
# FINE-approach overshoot (~1 µA above the last safe amp) was skipped and the
# ramp stopped on the overshoot; it now uses the 0.1 µA testing resolution and
# handles ``last_safe_amp is None`` (first-capture overshoot → 0 µA floor).
# ---------------------------------------------------------------------------
def test_backoff_fires_for_a_fine_overshoot():
    """A convex (accelerating) E_pol makes the secant OVER-project → the ramp
    steps just past the far edge.  Back-off must engage and land the FINAL
    accepted capture in / below the band (NOT stop on the overshoot)."""
    def epol(amp):
        # crosses -0.60 at 300 µA, accelerating (convex) so the predictor
        # overshoots the crossover amplitude.
        return -0.60 * (amp / 300.0) ** 2
    runner, stim, scope = _epol_runner(epol, cathodic=-0.6, tol=0.02)
    try:
        runner.run()
        caps = [c for c in runner.session.runs[0].captures
                if not c.status.aborted]
        final = caps[-1]
        # The run must NOT terminate on an exceeded capture — it decremented.
        assert final.status.exceeded_potential_limit is False, [
            round(abs(c.pattern.excitation_phase.amplitude_ua), 1) for c in caps]
        assert final.status.reached_potential_limit is True
    finally:
        stim.close(); scope.close()


def test_backoff_when_first_capture_overshoots_no_safe_floor():
    """A steep electrode whose VERY FIRST tested amplitude already overshoots
    (``last_safe_amp is None``): the ramp must still decrement, bracketing
    down from the 0 µA safe floor instead of stopping on the overshoot."""
    def epol(amp):
        return -0.09 * amp          # -0.90 V at 10 µA (way past the -0.62 far edge)
    runner, stim, scope = _epol_runner(epol, cathodic=-0.6, tol=0.02)
    try:
        runner.run()
        caps = [c for c in runner.session.runs[0].captures
                if not c.status.aborted]
        amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
        # At least one capture DECREMENTED below the 10 µA start (back-off).
        assert any(a < 10.0 for a in amps[1:]), amps
        assert caps[-1].status.exceeded_potential_limit is False
    finally:
        stim.close(); scope.close()
