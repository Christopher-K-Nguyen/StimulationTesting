"""R_load extraction: capture EVERY iR drop, and size the fit windows in TIME.

Two defects this pins, both found by feeding a KNOWN series-RC board
(4990 ohm + 4700 pF) through the real extraction and comparing against ground
truth (the IR step across a current edge is exactly R*delta_i, because the
capacitor voltage is continuous across the edge):

1. **A missing iR drop.**  ``access_index_labels`` emitted a trailing access
   point for the LAST phase only when a discharge delay followed it, so a
   pattern with ``discharge_us = 0`` silently dropped its last drop — the
   verification pulse (50/25/50, discharge 0) read only 3 of its 4.  The
   current DOES step to zero at the end of the pulse whenever an interpulse
   gap follows (operator: "be sure to capture all the iR drops in the
   waveform and average them").

2. **A sample-sized pre-edge window.**  The post-edge fit window is in TIME
   (us) but the pre-edge window was in SAMPLES, so it shrank with the
   timebase and collapsed inside the stimulator's current slew (measured on
   the bench: 0.64 us 10-90%).  The "baseline" line then got fitted on the
   transition and the step read LOW - worst on TRAILING edges, where the
   pre-edge region is a ramp rather than a flat baseline.
"""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.metrics import (access_index_labels,
                              access_voltage_and_resistance)
from stimtest.waveforms import PulsePattern

R_TRUE = 4990.0
C_TRUE = 4700e-12
AMP_UA = 100.0
W, GAP = 50.0, 25.0


def _pattern(discharge_us=0.0, rate_hz=50.0):
    return PulsePattern.biphasic(amplitude_ua=AMP_UA, phase_width_us=W,
                                 polarity=-1, interphase_us=GAP,
                                 discharge_us=discharge_us, rate_hz=rate_hz)


def _board(dt_us=0.0075, tau_us=0.29, t1=134.0):
    """Series R+C driven by a slewed current source.  tau 0.29 us == the
    measured 0.64 us 10-90% bench rise."""
    t = np.arange(-16.0, t1, dt_us)
    I = AMP_UA * 1e-6
    ideal = np.zeros_like(t)
    ideal[(t >= 0.0) & (t < W)] = -I
    ideal[(t >= W + GAP) & (t < 2 * W + GAP)] = +I
    i = np.empty_like(ideal)
    a = 1.0 - np.exp(-dt_us / tau_us)
    acc = 0.0
    for k in range(ideal.size):
        acc += a * (ideal[k] - acc)
        i[k] = acc
    vc = np.concatenate(
        ([0.0], np.cumsum(0.5 * (i[1:] + i[:-1]) * dt_us * 1e-6))) / C_TRUE
    return t, i * R_TRUE + vc


def _extract(t, v, pat):
    va, ra, idx = access_voltage_and_resistance(t, v, pat, onset_us=0.0)
    mags = [float(x) for x in va if np.isfinite(x)]
    return mags, [float(t[i]) for i in idx]


# ------------------------------------------------- 1. every iR drop
def test_all_four_ir_drops_are_captured_without_a_discharge_delay():
    """50/25/50 with discharge 0 has FOUR current edges -> four iR drops."""
    pat = _pattern(discharge_us=0.0)
    t, v = _board()
    mags, times = _extract(t, v, pat)
    assert len(mags) == 4, f"expected 4 iR drops, got {len(mags)} at {times}"
    # ...one per current edge: 0, 50, 75, 125 us
    for want, got in zip((0.0, 50.0, 75.0, 125.0), times):
        assert abs(got - want) < 3.0, f"edge at {got:.1f} us, expected ~{want}"


def test_last_phase_trailing_label_emitted_without_discharge():
    labels = access_index_labels(_pattern(discharge_us=0.0))
    assert (1, "trail") in labels, labels


def test_discharge_delay_still_emits_its_trailing_point():
    """The pre-existing discharge path is unchanged."""
    labels = access_index_labels(_pattern(discharge_us=20.0))
    assert (1, "trail") in labels


def test_continuous_pattern_gets_no_end_of_pulse_trailing_point():
    """No interpulse gap (KHFAC) -> the last phase runs into the NEXT pulse,
    so the boundary is a phase-1 lead, not a step back to zero."""
    # 50 us phases, no delays, rate 10 kHz -> period 100 us == the pulse.
    pat = PulsePattern.biphasic(amplitude_ua=AMP_UA, phase_width_us=W,
                                polarity=-1, interphase_us=0.0,
                                discharge_us=0.0, rate_hz=10_000.0)
    assert not pat.has_interpulse_gap()
    assert (1, "trail") not in access_index_labels(pat)


def test_clipped_record_does_not_add_a_garbage_drop():
    """A transfer that ends before the last edge must not contribute a step."""
    pat = _pattern(discharge_us=0.0)
    t, v = _board(t1=117.0)          # clipped short of the 125 us edge
    labels = access_index_labels(pat, time_us=t, v_trace=v, onset_us=0.0)
    assert (1, "trail") not in labels


# ------------------------------------------- 2. time-based pre-window
@pytest.mark.parametrize("dt_us", [0.0075, 0.075, 0.15])
def test_resistance_recovered_across_timebases(dt_us):
    """The SAME board must read the SAME R at any sample interval -- a
    sample-sized window made the answer timebase-dependent."""
    t, v = _board(dt_us=dt_us)
    mags, _ = _extract(t, v, _pattern())
    R = float(np.mean(mags)) / (AMP_UA * 1e-6)
    assert abs(R - R_TRUE) / R_TRUE < 0.10, f"dt={dt_us}: R={R:.0f}"


def test_leading_and_trailing_drops_agree():
    """A sample-sized pre-window biased TRAILING edges low (they sit on a
    ramp, not a flat baseline): 0.429 vs 0.386 V.  They must now agree."""
    t, v = _board()
    mags, _ = _extract(t, v, _pattern())
    assert len(mags) == 4
    spread = (max(mags) - min(mags)) / float(np.mean(mags))
    assert spread < 0.05, f"iR drops disagree by {spread:.1%}: {mags}"


def test_voltage_drop_matches_ohms_law():
    """The extracted drop IS I*R -- the capacitor ramp must cancel."""
    t, v = _board()
    mags, _ = _extract(t, v, _pattern())
    step = float(np.mean(mags))
    assert abs(step - R_TRUE * AMP_UA * 1e-6) / (R_TRUE * AMP_UA * 1e-6) < 0.10
