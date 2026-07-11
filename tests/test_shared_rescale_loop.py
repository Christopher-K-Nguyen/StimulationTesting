"""Every runner shares ExperimentRunner.rescale_to_fit.

Operator: "I want this same coarse/fine scaling (and positioning) for
the other experiments."  The full fit-the-view loop (coarse + fine
V/div, baseline-centred E_ret/E_act positioning, asymmetric V_mon/I_mon
positioning, percentile trim + stale-frame guards, fits-now no-coarsen
gate, final in-view verification) was extracted from VT's _one_capture
into the SHARED ``ExperimentRunner.rescale_to_fit`` (base.py).  These
tests pin that all four runners route through it — a runner quietly
reverting to a one-shot ``adapt_channel_scale`` regresses the operator
request.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_EXP = Path(__file__).resolve().parent.parent / "stimtest" / "experiments"


@pytest.mark.parametrize("runner_file", [
    "voltage_transient.py",
    "progressive_stress.py",
    "short_pulsing.py",
    "long_pulsing.py",
])
def test_runner_uses_shared_rescale_loop(runner_file):
    src = (_EXP / runner_file).read_text(encoding="utf-8")
    assert "self.rescale_to_fit(" in src, (
        f"{runner_file} must call the shared rescale_to_fit loop "
        f"(operator: same coarse/fine scaling + positioning everywhere)")


def test_shared_loop_lives_in_base():
    src = (_EXP / "base.py").read_text(encoding="utf-8")
    assert "def rescale_to_fit(" in src
    # The loop body actually moved (not a stub): its key stages exist.
    for marker in (
        "for _attempt in range(MAX_RECAPTURE + 1):",   # iterative loop
        "compute_scale_position_targets",               # positioning
        "_RESCALE_TRIM_PCT",                            # percentile trim
        "_RESCALE_STALE_FACTOR",                        # stale-frame guard
        "STILL OUT-OF-VIEW",                            # final verification
        "adapt_channel_scale",                          # coarse/fine scaling
    ):
        assert marker in src, f"rescale_to_fit is missing stage: {marker}"


def test_runners_have_no_one_shot_adapt_fallback():
    # The pre-extraction pattern was a bare per-channel
    # ``self.scope.adapt_channel_scale(...)`` after each snapshot —
    # scaling only, no positioning, no recapture.  It must be gone from
    # the snapshot runners (base.py owns the only call now).
    for runner_file in ("progressive_stress.py", "short_pulsing.py",
                        "long_pulsing.py"):
        src = (_EXP / runner_file).read_text(encoding="utf-8")
        assert "adapt_channel_scale" not in src, (
            f"{runner_file} still has a one-shot adapt_channel_scale — "
            f"use the shared rescale_to_fit instead")


def _fill_skip_runner():
    """A ShortPulsingExperiment wired to the simulator, ready to drive
    ``rescale_to_fit`` with monkeypatched clip / in-view / adapt decisions."""
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.experiments.short_pulsing import ShortPulsingExperiment
    from stimtest.waveforms import PulsePattern

    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    pattern = PulsePattern.biphasic(amplitude_ua=50.0)
    runner = ShortPulsingExperiment.__new__(ShortPulsingExperiment)
    runner.scope = scope
    runner._abort_requested = False
    runner._emit = lambda ev: None
    runner.session = None
    stim.load_channel(1, pattern); stim.start_all()
    return runner, stim, scope, pattern


def test_minor_fill_only_change_does_not_trigger_recapture():
    """A MINOR fill-only rescale (adapt returns a scale within ~30 % of the
    captured scale, in-view + NOT clipped) is applied but must NOT fire a verify
    re-capture — that re-capture is a full averager settle, the dominant VT-max
    cost the operator asked to cut.  Regression guard for the fill-only-skip."""
    runner, stim, scope, pattern = _fill_skip_runner()
    try:
        scope.channel_is_clipped = lambda *a, **k: False
        scope.channel_in_view = lambda *a, **k: True
        # Force a known captured scale (0.1 V/div) so the loop's _pre_adapt_vpd
        # is deterministic; adapt makes a MINOR fill tweak (0.09 = 0.9×).
        scope._cached_scale_pos = lambda ch: (0.1, 0.0)
        scope.adapt_channel_scale = lambda *a, **k: 0.09
        n_recap = {"n": 0}
        def _recap(_t):
            n_recap["n"] += 1
            return scope.capture_while_running(wait_s=0.0)
        acq = scope.capture_while_running(wait_s=0.0)
        runner.rescale_to_fit(acq, pattern=pattern, recapture=_recap,
                              timeout_s=1.0, context="(minor-fill)")
        assert n_recap["n"] == 0, (
            "a MINOR fill tweak on an in-view, not-clipped trace must NOT "
            f"re-capture — got {n_recap['n']} re-capture(s)")
    finally:
        stim.stop_all(); stim.close(); scope.close()


def test_significant_fill_rescale_does_recapture():
    """A SIGNIFICANT fill rescale (a small signal fine-fitting from a coarse
    scale, e.g. 0.1 → 0.02 V/div = 0.2×) MUST re-capture so the saved acq
    reflects the finer scale — else V_mon renders tiny / "not scaled" (operator
    regression: "Vmon was not scaling")."""
    runner, stim, scope, pattern = _fill_skip_runner()
    try:
        scope.channel_is_clipped = lambda *a, **k: False
        scope.channel_in_view = lambda *a, **k: True
        # Captured at 0.1 V/div; adapt fine-fits to 0.02 (0.2× — drastic) until
        # a re-capture updates the captured scale to 0.02, then it settles.
        scope._cached_scale_pos = lambda ch: (0.1, 0.0)
        scope.adapt_channel_scale = (
            lambda *a, **k: 0.02 if scope._cached_scale_pos("CH1")[0] > 0.05
            else None)
        n_recap = {"n": 0}
        def _recap(_t):
            n_recap["n"] += 1
            scope._cached_scale_pos = lambda ch: (0.02, 0.0)   # now at the fine scale
            return scope.capture_while_running(wait_s=0.0)
        acq = scope.capture_while_running(wait_s=0.0)
        runner.rescale_to_fit(acq, pattern=pattern, recapture=_recap,
                              timeout_s=1.0, context="(significant-fill)")
        assert n_recap["n"] >= 1, (
            "a drastic fill rescale (0.1 → 0.02 V/div) must re-capture so the "
            f"saved acq reflects the fine scale — got {n_recap['n']}")
    finally:
        stim.stop_all(); stim.close(); scope.close()


def test_dc_dominated_coarsen_back_does_not_recapture():
    """REGRESSION (exp_vt_max_cathodic_sin_cont): a DC-dominated role (a
    continuous-sinusoid V_mon with a large rest offset + small AC swing) makes
    ``adapt_channel_scale`` return a SWING-ONLY fine V/div — which reads as a
    "significant shrink" vs the coarse captured scale — but the coord path then
    COARSENS it back UP so the DC position fits ±5 div, leaving the FINAL scale
    ≈ the pre-write scale.  The re-capture decision must judge the FINAL scale
    (MINOR → fill-only, NO re-capture), NOT adapt's swing-only pick.  Before the
    fix, 128 of 134 captures burned all 5 rescale iters (~13 s each) on this
    thrash.  n_recap must be 0."""
    runner, stim, scope, pattern = _fill_skip_runner()
    try:
        scope.channel_is_clipped = lambda *a, **k: False
        scope.channel_in_view = lambda *a, **k: True
        # Captured at 0.14 V/div (coarse — sized for the DC bias).
        scope._cached_scale_pos = lambda ch: (0.14, 4.5)
        scope._vertical_grid_vpd = [0.06, 0.08, 0.10, 0.12, 0.14, 0.20]
        # adapt: V_mon (divs=3.0) → swing-only 0.06 V/div (a big shrink from
        # 0.14); I_mon (divs=4.0) → None (no change) so it can't force a
        # re-capture and confound the assertion.
        scope.adapt_channel_scale = (
            lambda *a, **k: 0.06 if k.get("divs") == 3.0 else None)
        # coord: DC-dominated → coarsen the swing-only 0.06 back to 0.14 so the
        # +4.5-div position offset fits (bias_ratio 3.0).
        scope.compute_scale_position_targets = (
            lambda lo, hi, **k: (0.14, 4.5, 3.0, "DC-dominated"))
        scope.set_channel_scale = lambda *a, **k: None
        scope.set_channel_position = lambda *a, **k: None
        n_recap = {"n": 0}
        def _recap(_t):
            n_recap["n"] += 1
            return scope.capture_while_running(wait_s=0.0)
        acq = scope.capture_while_running(wait_s=0.0)
        runner.rescale_to_fit(acq, pattern=pattern, recapture=_recap,
                              timeout_s=1.0, context="(dc-coarsen-back)")
        assert n_recap["n"] == 0, (
            "a swing-only V/div that the coord path coarsens back to ~the "
            "captured scale is a MINOR (fill-only) change — it must NOT "
            f"re-capture — got {n_recap['n']} re-capture(s)")
    finally:
        stim.stop_all(); stim.close(); scope.close()


def test_overflow_change_still_triggers_recapture():
    """A clip / out-of-view OVERFLOW must STILL re-capture to verify the grow
    (the safety-critical path — a clipped trace under-reports polarization).
    Confirms the fill-only skip didn't disable the escape re-capture."""
    runner, stim, scope, pattern = _fill_skip_runner()
    try:
        # The loop's clip flag is a LOCAL array check (not scope.channel_is_
        # clipped), so drive the OVERFLOW via the out-of-view path
        # (``channel_in_view`` False → ``_overflowed`` True).
        state = {"inview": False}
        scope.channel_is_clipped = lambda *a, **k: False
        scope.channel_in_view = lambda *a, **k: state["inview"]
        # Grow while out-of-view, then settle (None) once recapture cleared it.
        scope.adapt_channel_scale = (
            lambda *a, **k: 1.0 if not state["inview"] else None)
        n_recap = {"n": 0}
        def _recap(_t):
            n_recap["n"] += 1
            state["inview"] = True     # the grow brought the trace in-view
            return scope.capture_while_running(wait_s=0.0)
        acq = scope.capture_while_running(wait_s=0.0)
        runner.rescale_to_fit(acq, pattern=pattern, recapture=_recap,
                              timeout_s=1.0, context="(overflow)")
        assert n_recap["n"] >= 1, (
            "a clipped/out-of-view capture must re-capture to verify the grow "
            f"— got {n_recap['n']}")
    finally:
        stim.stop_all(); stim.close(); scope.close()


def test_rescale_to_fit_returns_acq_and_noops_on_simulator():
    """Smoke: on the simulator (no introspection, adapt no-op) the loop
    runs one pass and returns the same acq — converged immediately."""
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.experiments.short_pulsing import ShortPulsingExperiment
    from stimtest.waveforms import PulsePattern

    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    pattern = PulsePattern.biphasic(amplitude_ua=50.0)
    runner = ShortPulsingExperiment.__new__(ShortPulsingExperiment)
    runner.scope = scope
    runner._abort_requested = False
    events = []
    runner._emit = lambda ev: events.append(ev)
    runner.session = None

    stim.load_channel(1, pattern)
    stim.start_all()
    try:
        acq = scope.capture_while_running(wait_s=0.0)
        out = runner.rescale_to_fit(
            acq, pattern=pattern,
            recapture=lambda _t: scope.capture_while_running(wait_s=0.0),
            timeout_s=1.0, context="(test)")
        assert out is not None
        assert getattr(out, "channels", None)
    finally:
        stim.stop_all(); stim.close(); scope.close()
