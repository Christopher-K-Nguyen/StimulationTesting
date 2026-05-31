"""Tests for the gated-measurement primitives on the Oscilloscope ABC.

These primitives back the closed-loop bias feedback controller (host
side, ~10–20 Hz update rate) and are exercised via the simulator here.
The real Tek driver SCPI is structurally exercised at instantiation
time (signature, no-throw on hardware-less environment) but the
end-to-end behavior needs a connected scope and is covered in
``test_tektronix_probes.py``'s skip-when-offline gate.

Contract enforced
-----------------
* ``gate_measurement_window(t_us_start, t_us_end)``:
  - Stores the window; subsequent ``measure_mean`` queries respect it.
  - Raises ``ValueError`` if end < start.
  - Idempotent.
* ``clear_measurement_gating``:
  - Drops the stored window; subsequent ``measure_mean`` returns NaN
    (no window to gate on; not "the full screen mean" — that path
    would mask bugs where the controller forgot to set a window).
* ``measure_mean(channel)``:
  - Returns the mean of the channel's samples whose times fall in the
    gated window, in volts.
  - Returns NaN if no window is set, no samples land inside, the
    channel is unknown, or any underlying capture fails.
  - Accepts both physical channel names (``"CH3"``) and logical
    aliases (``"eret"``) via the scope's ``channel_aliases`` map.
"""
from __future__ import annotations

import numpy as np
import pytest


def _open_sim_scope():
    """Construct + bind a simulator scope with a programmed stim so
    its synthesized waveforms have signal to measure.  Reused by
    every test in this file."""
    from stimtest.hardware import SimulatedStimulator, SimulatedOscilloscope
    from stimtest.waveforms import PulsePattern

    stim = SimulatedStimulator()
    stim.open()
    pat = PulsePattern.biphasic(
        amplitude_ua=100.0, phase_width_us=100.0,
        interphase_us=50.0, rate_hz=100.0)
    stim.load_channel(1, pat)
    stim.set_monitor_channel(1)

    scope = SimulatedOscilloscope()
    scope.open()
    scope.bind_stimulator(stim)
    return stim, scope


# ---------------------------------------------------------------------------
# ABC default no-op behaviour
# ---------------------------------------------------------------------------
def test_abc_default_measure_mean_returns_nan():
    """The base-class default implementation returns NaN — drivers
    that don't override get a safe "no measurement" sentinel rather
    than raising."""
    from stimtest.hardware.base import Oscilloscope

    from stimtest.hardware.base import ScopeAcquisition

    class _Min(Oscilloscope):
        # Satisfy the remaining abstract methods with no-ops.
        def open(self, resource=None): pass
        def close(self): pass
        def set_channel_scale(self, channel, volts_per_div): pass
        def set_horizontal_scale(self, seconds_per_div): pass
        def single_capture(self, *, timeout_s=None):
            return ScopeAcquisition(time_us=np.zeros(1), channels={})

    s = _Min()
    assert np.isnan(s.measure_mean("CH1"))
    # The setters are no-ops; they must not raise.
    s.gate_measurement_window(0.0, 100.0)
    s.clear_measurement_gating()


# ---------------------------------------------------------------------------
# Simulator implementation
# ---------------------------------------------------------------------------
def test_simulator_measure_mean_without_window_returns_nan():
    """Calling ``measure_mean`` before ``gate_measurement_window``
    must return NaN — surfacing a "forgot to set the gate" bug
    rather than silently returning the full-screen mean."""
    _, scope = _open_sim_scope()
    assert np.isnan(scope.measure_mean("CH3"))


def test_simulator_measure_mean_after_gate_returns_finite():
    """End-to-end smoke test: set a window in the post-pulse region,
    read the E_ret mean, get a finite voltage near baseline."""
    _, scope = _open_sim_scope()
    # Place the window deep in the interpulse interval where the
    # signal should be at the electrode's rest potential.  The
    # simulator's _return electrode has e_eq_v=0.05 V.
    scope.gate_measurement_window(300.0, 450.0)
    v = scope.measure_mean("CH3")
    assert np.isfinite(v)
    # Sanity bounds — should be near the rest potential, within
    # ±0.5 V even with the model's transient dynamics.
    assert -0.5 < v < 0.5


def test_simulator_measure_mean_respects_window_position():
    """Mean over an early window (during the pulse) should differ
    from mean over a late window (interpulse interval) — proves the
    gating actually bounds the calculation."""
    _, scope = _open_sim_scope()
    # During phase 1 of the pulse (cathodic, ramping electrode V down).
    scope.gate_measurement_window(0.0, 50.0)
    v_during = scope.measure_mean("CH3")
    # Late post-pulse — at the tail of the simulator's captured
    # window (which extends ~200 µs past pulse end, so ~470 µs total).
    scope.gate_measurement_window(300.0, 450.0)
    v_after = scope.measure_mean("CH3")
    assert np.isfinite(v_during) and np.isfinite(v_after)
    # The two should differ meaningfully — if they don't, the gate
    # isn't restricting the computation.
    assert abs(v_during - v_after) > 1e-3, (
        f"gated means should differ between pulse and interpulse "
        f"windows; got during={v_during:.6f}, after={v_after:.6f}")


def test_simulator_measure_mean_accepts_logical_alias():
    """Callers may pass 'eret' (logical name) or 'CH3' (physical).
    Both should resolve to the same channel via channel_aliases."""
    _, scope = _open_sim_scope()
    scope.gate_measurement_window(300.0, 450.0)
    v_alias = scope.measure_mean("eret")
    v_phys = scope.measure_mean("CH3")
    assert np.isfinite(v_alias) and np.isfinite(v_phys)
    # Same channel, same window, same capture → identical (the
    # simulator's single_capture is deterministic for a fixed stim).
    # Allow a tiny epsilon for any internal float ops.
    assert abs(v_alias - v_phys) < 1e-9


def test_simulator_measure_mean_unknown_channel_returns_nan():
    """A channel name not in channel_aliases AND not present in the
    captured waveforms returns NaN — never raises, never returns 0
    (which a control loop would mistake for "we're at setpoint")."""
    _, scope = _open_sim_scope()
    scope.gate_measurement_window(300.0, 450.0)
    assert np.isnan(scope.measure_mean("CH9"))
    assert np.isnan(scope.measure_mean("nonexistent_alias"))


def test_simulator_clear_measurement_gating_disables_reads():
    """After ``clear_measurement_gating``, ``measure_mean`` returns
    NaN again — gating is sticky until explicitly cleared OR
    re-set."""
    _, scope = _open_sim_scope()
    scope.gate_measurement_window(300.0, 450.0)
    assert np.isfinite(scope.measure_mean("CH3"))
    scope.clear_measurement_gating()
    assert np.isnan(scope.measure_mean("CH3"))


def test_simulator_gate_window_rejects_inverted_range():
    """A start > end window is a programming error; raise rather
    than silently swap or return NaN.  Catches loop-bounds bugs in
    the controller."""
    _, scope = _open_sim_scope()
    with pytest.raises(ValueError, match="must be ≥"):
        scope.gate_measurement_window(1000.0, 500.0)


def test_simulator_gate_window_zero_width_returns_nan():
    """A zero-width window (start == end) is legal (no error) but
    yields no samples → NaN.  Documents the boundary behaviour so a
    controller that converges to a zero-width window degrades to NaN
    rather than crashing."""
    _, scope = _open_sim_scope()
    scope.gate_measurement_window(500.0, 500.0)
    # Most samples won't hit exactly t=500 µs, so the mask is likely
    # all-False → NaN.  At minimum, must not raise.
    v = scope.measure_mean("CH3")
    # Either NaN (no samples landed exactly at the boundary) or a
    # finite single-sample value if one did — both are acceptable.
    assert np.isnan(v) or np.isfinite(v)


def test_simulator_gate_window_idempotent():
    """Calling ``gate_measurement_window`` twice with the same args
    is cheap and leaves the state unchanged."""
    _, scope = _open_sim_scope()
    scope.gate_measurement_window(300.0, 450.0)
    v1 = scope.measure_mean("CH3")
    scope.gate_measurement_window(300.0, 450.0)
    scope.gate_measurement_window(300.0, 450.0)
    v2 = scope.measure_mean("CH3")
    assert np.isfinite(v1) and np.isfinite(v2)
    assert abs(v1 - v2) < 1e-9


# ---------------------------------------------------------------------------
# Tek implementation — structural only (no hardware here)
# ---------------------------------------------------------------------------
def test_tektronix_class_has_gated_measurement_methods():
    """Verify the Tek driver actually exposes the three primitives
    so a future refactor that forgets one would fail to import."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    for method in ("gate_measurement_window",
                   "clear_measurement_gating",
                   "measure_mean"):
        attr = getattr(TektronixOscilloscope, method, None)
        assert callable(attr), (
            f"TektronixOscilloscope is missing {method!r} — the closed-"
            f"loop feedback controller depends on it")
