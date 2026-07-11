"""Predictive V/div seed for VT amplitude steps (efficiency).

Before the FIRST capture of an amplitude step, ``_seed_scope_scales`` pre-grows
each rescale-managed voltage channel's V/div from the PREVIOUS capture's
observed half-range × the amplitude ratio (snapped UP), so the first read is
already in-view and the rescale loop converges in one pass instead of a
clip→upscale re-capture.  Anchored to the observed range (not a formula) so it
can't compound; only-grow; graceful under-seed (clip detector recovers).
"""
from __future__ import annotations

import numpy as np


def _setup():
    from stimtest.hardware.tektronix import TektronixOscilloscope as T
    from stimtest.experiments.voltage_transient import (
        VoltageTransientExperiment as VT)
    scope = T.__new__(T)
    scope._min_vdiv_v = 1e-3
    scope._adapt_state = {}
    scope._preamble_cache = {}
    scope._y_codes_per_div = {}
    scope.channel_aliases = {"vmon": "CH1", "imon": "CH2", "eret": "CH3"}
    scope._log = lambda *a, **k: None
    scope._w = lambda cmd: None
    scope._invalidate_preamble_cache = lambda *a, **k: None
    for ch, v in {"CH1": 0.05, "CH3": 0.01}.items():
        scope._adapt_state[ch] = scope._new_adapt_state()
        scope._adapt_state[ch]["last_scale"] = v
    runner = VT.__new__(VT)
    runner.scope = scope
    runner.session = None
    runner._emit = lambda ev: None
    runner._seed_prev_amp_ua = None
    runner._seed_prev_half_range = {}
    return runner, scope


def _last(scope, ch):
    return scope._adapt_state[ch]["last_scale"]


def test_no_seed_on_first_step():
    """No prior amplitude/range ⇒ the seed is a no-op (first step of a channel
    uses apply_default_scope_view's sizing)."""
    runner, scope = _setup()
    runner._seed_scope_scales(100.0)
    assert _last(scope, "CH1") == 0.05 and _last(scope, "CH3") == 0.01


def test_seed_grows_scale_by_amplitude_ratio():
    """Growing amplitude ⇒ V_mon/E_ret V/div pre-grow ~proportionally."""
    runner, scope = _setup()
    runner._seed_prev_amp_ua = 50.0
    # observed half-ranges at 50 µA: V_mon ±0.15 V, E_ret ±0.02 V
    runner._seed_prev_half_range = {"vmon": 0.15, "eret": 0.02}
    runner._seed_scope_scales(100.0)                 # ratio ×2
    # vmon ideal = 0.15×2/3 = 0.10 V/div → snapped up, and > the old 0.05
    assert _last(scope, "CH1") >= 0.10
    assert _last(scope, "CH1") > 0.05
    # eret ideal = 0.02×2/4 = 0.01 → not larger than current 0.01 → no shrink
    assert _last(scope, "CH3") >= 0.01


def test_seed_never_shrinks():
    """The seed only GROWS — a smaller predicted scale must not shrink the
    current one (shrinking is the rescale loop's job, with hysteresis)."""
    runner, scope = _setup()
    runner._seed_prev_amp_ua = 100.0
    runner._seed_prev_half_range = {"vmon": 0.02}     # tiny signal
    scope._adapt_state["CH1"]["last_scale"] = 0.5     # currently coarse
    runner._seed_scope_scales(110.0)                  # ratio ×1.1
    assert _last(scope, "CH1") == 0.5                 # unchanged (no shrink)


def test_no_seed_when_amplitude_not_increasing():
    runner, scope = _setup()
    runner._seed_prev_amp_ua = 100.0
    runner._seed_prev_half_range = {"vmon": 0.15}
    runner._seed_scope_scales(100.0)                  # equal → skip
    runner._seed_scope_scales(80.0)                   # decrease → skip
    assert _last(scope, "CH1") == 0.05


def test_stash_records_observed_half_range():
    """_stash_seed_range extracts the trimmed per-role half-range from the
    saved capture arrays for the next step's seed."""
    runner, scope = _setup()

    class _Cap:
        pass
    cap = _Cap()
    cap.v_mon_v = np.concatenate([np.full(500, -0.30), np.full(500, +0.30)])
    cap.e_ret_v = np.full(1000, 0.05)                 # flat → ~0 half-range
    cap.e_act_v = None
    runner._stash_seed_range(cap, -123.0)
    assert runner._seed_prev_amp_ua == 123.0
    # V_mon half-range ≈ (0.30 - (-0.30))/2 = 0.30
    assert abs(runner._seed_prev_half_range["vmon"] - 0.30) < 1e-6
    assert runner._seed_prev_half_range["eret"] < 1e-6


def test_seed_does_not_compound_across_steps():
    """Because the seed re-anchors to the OBSERVED range each step (via
    _stash_seed_range), a sub-linear signal doesn't inflate the scale step
    after step.  Simulate: amplitude doubles but the observed signal only
    grows 1.5× (saturating) — the seed tracks the real signal, not a
    compounding ratio."""
    runner, scope = _setup()
    # Step 1 converged at 0.05 V/div, observed half-range 0.12 V @ 50 µA.
    runner._seed_prev_amp_ua = 50.0
    runner._seed_prev_half_range = {"vmon": 0.12}
    runner._seed_scope_scales(100.0)                  # seed for 100 µA
    seed1 = _last(scope, "CH1")
    # The real capture at 100 µA came back only 1.5× bigger (saturating),
    # observed half-range 0.18 V (not 0.24) — stash the REAL range.

    class _Cap:
        v_mon_v = np.concatenate([np.full(500, -0.18), np.full(500, +0.18)])
        e_ret_v = None
        e_act_v = None
    runner._stash_seed_range(_Cap(), -100.0)
    runner._seed_scope_scales(200.0)                  # seed for 200 µA
    seed2 = _last(scope, "CH1")
    # seed2 anchors to 0.18 (real) × 2 / 3 = 0.12 → NOT seed1 × 2.  It tracks
    # the physical signal, so no runaway compounding.
    assert seed2 < seed1 * 2.0
