"""adapt_channel_scale must NOT coarsen past a scale where the signal
already fits the visible budget.

Operator (Jun 11 log, 0:00:36 block): "the third waveform has larger
vertical scaling despite that the second fits the screen."  Trace:

    attempt 1: ±5.08 V @ 1000 mV/div CLIPPED       → grow to 3400 (OK)
    attempt 2: ±13.87 V @ 3400 mV/div fit=in-view  → grew AGAIN to 4600 (BUG)
    attempt 3: ±13.8 V @ 4600 mV/div               → no change

At attempt 2 the signal occupied ±4.08 of the ±4.95-div budget (~82 %
fill — best fit), but the old grow trigger was ``ideal (half/divs) >
current``, which re-targets the divs fill (60 %) even when the waveform
already fits.  MATLAB getWaveform3.m accepts on ``isInView &&
~isTooTight`` and never coarsens past a fitting scale.  The fix gates the
grow path on genuine overflow of the visible budget
(``half_range > fit_divs × current_scale``); ``divs`` remains the sizing
target when an adjustment IS needed.
"""
from __future__ import annotations

import pytest


def _scope(current_scale: float, half_vert_divs: float = 5.0):
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._half_vert_divs = half_vert_divs
    s._log = lambda *a, **k: None
    s._inst = object()   # never touched (last_scale cached)
    writes = []
    s.set_channel_scale = lambda ch, vpd: writes.append((ch, vpd))
    s._adapt_state = {"CH1": s._new_adapt_state()}
    s._adapt_state["CH1"]["last_scale"] = current_scale
    return s, writes


def test_fitting_signal_is_not_coarsened():
    # The operator's exact case: ±13.87 V at 3400 mV/div (fits the
    # ±4.95-div budget: 13.87 < 16.83 V) — ideal fill target would be
    # 4600 mV/div, but the loop must KEEP 3400.
    s, writes = _scope(current_scale=3.4)
    out = s.adapt_channel_scale("CH1", v_min=-13.872, v_max=13.464,
                                divs=3.0, shrink_stable_count=1)
    assert out is None, f"must keep the fitting finer scale, wrote {out}"
    assert writes == []


def test_genuine_overflow_still_grows():
    # Signal half-range 0.6 V at 100 mV/div: 0.6 > 4.95×0.1 = 0.495 →
    # genuinely out of the visible budget → grow to half/divs = 0.2.
    s, writes = _scope(current_scale=0.1)
    out = s.adapt_channel_scale("CH1", v_min=-0.6, v_max=0.6,
                                divs=3.0, shrink_stable_count=1)
    assert out == pytest.approx(0.2)
    assert writes == [("CH1", pytest.approx(0.2))]


def test_small_signal_still_shrinks():
    # Shrink direction unaffected: ±136 mV at 1 V/div → fine-grid 50 mV.
    s, writes = _scope(current_scale=1.0)
    out = s.adapt_channel_scale("CH1", v_min=-0.136, v_max=0.136,
                                divs=3.0, shrink_stable_count=1)
    assert out == pytest.approx(0.05)


def test_fitting_signal_counts_as_settled():
    # The keep-finer-scale path participates in settle bookkeeping so the
    # loop converges (settled_count increments, history clears).
    s, writes = _scope(current_scale=3.4)
    st = s._adapt_state["CH1"]
    st["history"] = [3.4]
    for _ in range(2):   # _ADAPT_SETTLED_COUNT
        assert s.adapt_channel_scale("CH1", v_min=-13.8, v_max=13.4,
                                     divs=3.0) is None
    assert st["settled_count"] >= 2
    assert st["history"] == []


def test_force_grow_bypasses_fits_now_for_offset_clip():
    """Adversarial finding: an OFFSET-driven clip (E_act at a ~800 mV
    rest potential railing one side at 0.2 V/div, position 0) produces a
    doubled range [0.7, 2.5] V whose offset-blind half-range (0.9 V)
    still "fits" the ±4.95-div budget (0.99 V) — the fits-now veto would
    settle and save a rail-clipped trace.  The rescale loop passes
    ``force_grow=True`` whenever its clip / out-of-view detection fired
    (the doubled range is an extrapolation, not a faithful observation),
    which must bypass the veto and grow to the divs sizing target."""
    s, writes = _scope(current_scale=0.2)
    # Without force_grow: the veto keeps the finer scale (defect path).
    out = s.adapt_channel_scale("CH1", v_min=0.7, v_max=2.5,
                                divs=4.0, shrink_stable_count=1)
    assert out is None and writes == []
    # With force_grow: the grow fires to half/divs = 0.225 → grid 0.24.
    out = s.adapt_channel_scale("CH1", v_min=0.7, v_max=2.5,
                                divs=4.0, shrink_stable_count=1,
                                force_grow=True)
    assert out == pytest.approx(0.24)
    assert writes == [("CH1", pytest.approx(0.24))]


def test_base_class_accepts_force_grow():
    # Simulator-style scopes inherit the base no-op — it must accept the
    # kwarg so the shared rescale loop works on every backend.
    from stimtest.hardware.simulator import SimulatedOscilloscope
    s = SimulatedOscilloscope()
    assert s.adapt_channel_scale(
        "CH1", v_min=-1.0, v_max=1.0, force_grow=True) is None


def test_keep_finer_log_once_per_settle_streak():
    # The "keeping the finer scale" line fires once per settle streak —
    # near a grid boundary it would otherwise spam every LP/PS snapshot.
    s, writes = _scope(current_scale=3.4)
    logs = []
    s._log = lambda m: logs.append(m)
    for _ in range(4):
        s.adapt_channel_scale("CH1", v_min=-13.8, v_max=13.4, divs=3.0)
    keep_lines = [m for m in logs if "keeping the finer scale" in m]
    assert len(keep_lines) == 1, keep_lines
