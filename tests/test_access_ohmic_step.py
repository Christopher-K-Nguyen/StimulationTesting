"""Access V/R extracts the SMALL OHMIC step, not the flattened capacitive tail.

Bug (exp_vt_max_pcc): on a CAPACITIVE / high-Z electrode V_mon after the current
edge is an EXPONENTIAL ramp that flattens (a leaky capacitor: it charges toward
``I·(R_ohmic + R_ct)``).  The old extraction fitted the localizer's LATE
settling window — in the shallow, flattened tail — and extrapolated that back to
the edge, OVER-reporting R_a (CH07 read ~20 kΩ at 6 µA with no visible iR step).
The near-edge window (``access_step_by_extrapolation``, ``after_start_us`` /
``after_win_us``) fits the constant-current slope right after the edge and
extracts the true small ohmic step instead (operator: "extract the small ohmic
step").  A healthy electrode (sharp iR step + LINEAR ramp) is unchanged.
"""
from __future__ import annotations

import numpy as np

from stimtest import metrics as M
from stimtest.waveforms import Phase, PulsePattern, SHAPE_RECTANGULAR


def _leaky_capacitor_vmon(t, onset, width, amp_ua, r_ohmic_k, r_ct_k, tau_us):
    """V_mon for a cathodic phase into R_ohmic in series with (R_ct ‖ C):
    an instantaneous ``I·R_ohmic`` step then an exponential charge toward
    ``I·(R_ohmic + R_ct)`` with time constant ``tau``."""
    v = np.zeros_like(t)
    ph = (t >= onset) & (t < onset + width)
    tt = t[ph] - onset
    step = amp_ua * r_ohmic_k * 1e-3            # µA·kΩ → mV → V (×1e-3)
    plateau = amp_ua * r_ct_k * 1e-3
    v[ph] = -(step + plateau * (1.0 - np.exp(-tt / tau_us)))
    return v


def _cap(t, v, amp_ua):
    from stimtest.session import Capture
    pat = PulsePattern(phases=[Phase(amplitude_ua=-abs(amp_ua), width_us=200.0,
                                     shape=SHAPE_RECTANGULAR),
                               Phase(amplitude_ua=+abs(amp_ua), width_us=200.0,
                                     shape=SHAPE_RECTANGULAR)], rate_hz=50.0)
    return Capture(index=0, time_us=t, v_mon_v=v, i_mon_ua=np.zeros_like(t),
                   e_act_v=None, e_ret_v=None, pattern=pat)


def test_capacitive_electrode_reads_ohmic_not_plateau():
    """A 1.5 kΩ-ohmic / 20 kΩ-R_ct leaky capacitor at 6 µA: the extraction
    must read ~R_ohmic (a few kΩ), NOT the ~21 kΩ plateau impedance."""
    dt = 0.08
    t = np.arange(-100.0, 500.0, dt)
    amp = 6.0
    v = _leaky_capacitor_vmon(t, 0.0, 200.0, -amp, r_ohmic_k=1.5, r_ct_k=20.0,
                              tau_us=50.0)
    cap = _cap(t, v, amp)
    onset = 0.0
    va, ra, _ = M.access_voltage_and_resistance(t, v, cap.pattern, onset_us=onset)
    r1 = ra[0]
    assert np.isfinite(r1)
    # Near the ohmic 1.5 kΩ, and FAR below the 21.5 kΩ plateau impedance.
    assert r1 < 6.0, f"expected the small ohmic step, got {r1:.1f} kΩ"
    assert r1 < 0.5 * 21.5


def test_healthy_electrode_unchanged():
    """Sharp iR step + shallow LINEAR ramp → the ohmic R is read correctly
    (the near-edge and late windows agree for a linear ramp)."""
    dt = 0.08
    t = np.arange(-100.0, 500.0, dt)
    amp = 200.0
    r_ohmic_k = 1.2
    v = np.zeros_like(t)
    ph = (t >= 0.0) & (t < 200.0)
    tt = t[ph]
    # Instantaneous iR step (1 sample) + a shallow LINEAR polarization ramp.
    step = -amp * r_ohmic_k * 1e-3
    v[ph] = step - 0.00005 * tt          # −50 µV/µs linear ramp
    cap = _cap(t, v, amp)
    va, ra, _ = M.access_voltage_and_resistance(t, v, cap.pattern, onset_us=0.0)
    assert abs(ra[0] - r_ohmic_k) < 0.15, f"expected ~{r_ohmic_k} kΩ, got {ra[0]:.2f}"
