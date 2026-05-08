#!/usr/bin/env python
"""Compare derivative E_pol vs time-based E_pol on a real saved session."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from stimtest.metrics import (access_voltage_and_resistance,
                              polarization_per_phase, driving_voltage_from_vmon)
from stimtest.persistence import load_session_npz


def main() -> int:
    npz = Path("data") / "vt_run.npz"
    if not npz.exists():
        print(f"Need {npz}; run run_cli.py --simulate first."); return 1
    session = load_session_npz(npz)
    run = session.runs[0]

    print(f"Session : {session.name}")
    print(f"Run     : {run.configuration.display_name()}, "
          f"{len(run.captures)} captures")
    print()
    print(f"{'cap#':>4} {'I_amp':>9} {'V_d':>8} "
          f"{'E_pol_t1(time)':>15} {'E_pol_t1(deriv)':>17} "
          f"{'V_a_trail1':>12} {'drv1-Va_t1':>12}")
    print("-" * 92)

    sample_indices = list(range(0, len(run.captures), 30)) + [len(run.captures) - 1]
    for idx in sample_indices:
        cap = run.captures[idx]
        if cap.time_us.size == 0:
            continue
        v_mon = cap.v_mon_v
        t = cap.time_us
        amp = cap.pattern.excitation_phase.amplitude_ua
        vd = driving_voltage_from_vmon(v_mon)

        va_list, ra_list, access_idx = access_voltage_and_resistance(
            t, v_mon, cap.pattern,
        )
        # E_pol via derivative method (samples V_mon at trailing plateau)
        e_pol_d = polarization_per_phase(t, v_mon, cap.pattern,
                                         method="derivative",
                                         access_idx=access_idx)
        # E_pol via time method (samples at 12 µs after phase end)
        e_pol_t = polarization_per_phase(t, v_mon, cap.pattern,
                                         method="time", depol_us=12.0)

        # Driving for phase 1 = min(V_mon) for cathodic-first
        v_filt_min = float(np.min(v_mon))
        v_a_trail_1 = va_list[1] if len(va_list) >= 2 else float("nan")
        drv_minus_va = v_filt_min + v_a_trail_1   # cathodic: driving negative,
                                                  # +V_a (positive) recovers up
        print(f"{cap.index:>4} {amp:>+8.1f}µA {vd:>+7.2f}V "
              f"{e_pol_t[0]:>+15.3f} {e_pol_d[0]:>+17.3f} "
              f"{v_a_trail_1:>+12.3f} {drv_minus_va:>+12.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
