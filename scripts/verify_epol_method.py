#!/usr/bin/env python
"""Verify the new derivative-based E_pol equals (driving − trailing access voltage).

Builds a synthetic biphasic capture with known driving potential and known
ohmic recovery, runs it through ``compute_metrics``, and checks that for each
phase:

    E_pol[k]  ==  driving_potential[k]  −  sign(driving) · V_a_trailing[k]

These should match to within numerical noise because the new derivative
method samples ``e_trace`` at the very plateau index that defined V_a in the
first place.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from stimtest.metrics import (
    access_voltage_and_resistance, access_index_labels,
    polarization_per_phase,
)
from stimtest.waveforms import PulsePattern


def _synthetic_biphasic(driving_neg=-5.0, driving_pos=4.0,
                        v_a_neg=2.0, v_a_pos=1.5,
                        e_pol_after_phase1=-3.0, e_pol_after_phase2=2.5,
                        phase_us=200.0, iph_us=20.0, dd_us=20.0,
                        sample_us=0.5):
    """A simulated cathodic-first biphasic E_act trace, ramps and recoveries
    drawn as straight lines so the derivative finds the corners cleanly.
    """
    pre_us = 50.0
    total = pre_us + 2 * phase_us + iph_us + dd_us + 50.0
    t = np.arange(-pre_us, total - pre_us, sample_us)
    v = np.zeros_like(t)

    # Pre-pulse: 0 V
    # Phase 1 (cathodic): jump down v_a_neg, ramp from -v_a_neg to driving_neg
    # over phase_us
    p1_start, p1_end = 0.0, phase_us
    p1_mask = (t >= p1_start) & (t < p1_end)
    p1_t = t[p1_mask]
    p1_frac = (p1_t - p1_start) / phase_us
    v[p1_mask] = -v_a_neg + (driving_neg - (-v_a_neg)) * p1_frac

    # Interphase: jump up by v_a_neg (trailing access of phase 1), then sit
    # at e_pol_after_phase1
    iph_start, iph_end = p1_end, p1_end + iph_us
    iph_mask = (t >= iph_start) & (t < iph_end)
    v[iph_mask] = e_pol_after_phase1

    # Phase 2 (anodic): jump up v_a_pos (leading access of phase 2), ramp
    # from e_pol + v_a_pos to driving_pos over phase_us
    p2_start, p2_end = iph_end, iph_end + phase_us
    p2_mask = (t >= p2_start) & (t < p2_end)
    p2_t = t[p2_mask]
    p2_frac = (p2_t - p2_start) / phase_us
    p2_y0 = e_pol_after_phase1 + v_a_pos
    v[p2_mask] = p2_y0 + (driving_pos - p2_y0) * p2_frac

    # Discharge delay: jump down by v_a_pos (trailing access of phase 2), sit
    # at e_pol_after_phase2
    dd_start = p2_end
    dd_mask = (t >= dd_start)
    v[dd_mask] = e_pol_after_phase2

    return t, v


def main() -> int:
    pattern = PulsePattern.biphasic(
        amplitude_ua=10.0, phase_width_us=200.0, interphase_us=20.0,
        discharge_us=20.0, polarity=-1,
    )
    driving_neg, driving_pos = -5.0, 4.0
    v_a_neg, v_a_pos = 2.0, 1.5
    e1, e2 = -3.0, 2.5      # = driving_neg + v_a_neg, driving_pos - v_a_pos
    t, e_act = _synthetic_biphasic(
        driving_neg=driving_neg, driving_pos=driving_pos,
        v_a_neg=v_a_neg, v_a_pos=v_a_pos,
        e_pol_after_phase1=e1, e_pol_after_phase2=e2,
    )

    va, ra, idx = access_voltage_and_resistance(t, e_act, pattern)
    labels = access_index_labels(pattern)
    print("Access detector results:")
    for k, ((phase, role), i) in enumerate(zip(labels, idx)):
        if 0 <= i < e_act.size:
            print(f"  [{k}] phase={phase} role={role:5s}  idx={i:5d}  "
                  f"e_act={e_act[i]:+.3f}V  V_a={va[k]:+.3f}V")
    print()

    # Algebraic check: E_pol_1 = driving_neg + v_a_neg = -3 V
    #                  E_pol_2 = driving_pos - v_a_pos = +2.5 V
    expected = [e1, e2]

    e_pol_deriv = polarization_per_phase(
        t, e_act, pattern, method="derivative", access_idx=idx,
    )
    e_pol_time = polarization_per_phase(
        t, e_act, pattern, method="time", depol_us=12.0,
    )
    e_pol_auto = polarization_per_phase(
        t, e_act, pattern, method="auto", access_idx=idx,
    )

    print(f"{'phase':<7} {'expected':>10} {'derivative':>12} {'time(12µs)':>12} {'auto':>10}")
    print("-" * 55)
    ok = True
    for k, exp in enumerate(expected):
        d, ti, au = e_pol_deriv[k], e_pol_time[k], e_pol_auto[k]
        match = abs(d - exp) < 0.05
        ok &= match
        marker = "OK" if match else "BAD"
        print(f"{k+1:<7} {exp:>+10.3f} {d:>+12.3f} {ti:>+12.3f} {au:>+10.3f}  {marker}")
    print()
    print("Algebraic equivalence:")
    print(f"  E_pol_1 expected = driving_neg + v_a_neg = {driving_neg} + {v_a_neg} = {driving_neg + v_a_neg}")
    print(f"  E_pol_2 expected = driving_pos - v_a_pos = {driving_pos} - {v_a_pos} = {driving_pos - v_a_pos}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
