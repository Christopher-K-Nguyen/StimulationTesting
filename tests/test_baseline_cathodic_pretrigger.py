"""Regression tests for the 'wrong offset' bug in per_capture_baseline.

Bug: the display baseline-subtracts V_mon / I_mon using
``per_capture_baseline``, which averaged the WHOLE pre-trigger window
(``t < -1 µs``).  With the I_mon trigger protocol (no digital sync
channel) the scope fires on the ANODIC current edge, so for a
cathodic-first pulse the CATHODIC phase fills the pre-trigger window.
Averaging it returned the cathodic level (≈ −amplitude) instead of the
idle level; subtracting that shifted the whole trace UP by ~one pulse
amplitude (interpulse 0 → +amplitude).

Fix: sample the LEADING edge of the record (earliest samples), which
``auto_layout_for_pulse`` guarantees is idle regardless of where the
trigger sits within the pulse.
"""
from __future__ import annotations

import numpy as np

from stimtest.readback_calibration import per_capture_baseline


def _imon_triggered_trace(amp=50.0, noise=1.0, seed=0):
    """Cathodic-first capture triggered on the anodic edge → the
    cathodic phase occupies the pre-trigger window (t < 0)."""
    t = np.linspace(-300.0, 300.0, 6000)
    y = np.zeros_like(t)
    y[(t >= -200.0) & (t < 0.0)] = -amp     # cathodic, pre-trigger
    y[(t >= 0.0) & (t < 200.0)] = +amp      # anodic, post-trigger
    y += np.random.default_rng(seed).normal(0.0, noise, y.size)
    return t, y


def test_cathodic_in_pretrigger_does_not_poison_baseline():
    t, y = _imon_triggered_trace(amp=50.0)
    bl = per_capture_baseline(y, t)
    # Old behaviour returned ~ -50 (the cathodic level). The leading
    # edge is idle, so the baseline must be ~0, NOT the cathodic level.
    assert abs(bl) < 3.0, f"baseline {bl} picked up the cathodic phase"


def test_displayed_interpulse_lands_near_zero():
    t, y = _imon_triggered_trace(amp=50.0)
    displayed = y - per_capture_baseline(y, t)
    idle_mask = t < -200.0
    assert abs(float(np.median(displayed[idle_mask]))) < 3.0


def test_genuine_dc_bias_is_still_removed():
    # Onset-triggered (pre-trigger idle) trace sitting on a +0.6 V DC
    # bias — the fix must STILL recover and remove that real bias.
    t = np.linspace(-100.0, 500.0, 6000)
    y = np.full_like(t, 0.6)
    y[(t >= 0.0) & (t < 200.0)] += -0.07
    y[(t >= 220.0) & (t < 420.0)] += +0.10
    y += np.random.default_rng(1).normal(0.0, 0.002, y.size)
    bl = per_capture_baseline(y, t)
    assert abs(bl - 0.6) < 0.02


def test_robust_without_time_axis():
    # No usable time axis → still uses the leading samples (idle by
    # the auto_layout leading-baseline guarantee).
    _, y = _imon_triggered_trace(amp=50.0)
    bl = per_capture_baseline(y, None)
    assert abs(bl) < 3.0


def test_too_short_returns_zero():
    assert per_capture_baseline(None, None) == 0.0
    assert per_capture_baseline(np.array([1.0, 2.0, 3.0]), None) == 0.0


def test_calibration_mirror_matches():
    # The calibration nested helper must agree with the shared one so
    # the experiment plot and the calibration plot define "idle" the
    # same way.  Compare on an I_mon-triggered trace.
    import numpy as _np
    t, y = _imon_triggered_trace(amp=50.0)

    # Re-implement the calibration helper's math inline (it's a nested
    # closure, not importable) and assert it matches the shared one.
    n = y.size
    order = _np.argsort(t)
    a = y[order]
    n_lead = max(8, min(800, n // 16))
    s = a[:n_lead]
    med = float(_np.median(s)); mad = float(_np.median(_np.abs(s - med)))
    if mad > 0:
        keep = _np.abs(s - med) <= 3.0 * 1.4826 * mad
        if int(_np.count_nonzero(keep)) >= 4:
            s = s[keep]
    cal_like = float(_np.mean(s))
    assert abs(cal_like - per_capture_baseline(y, t)) < 1e-9
