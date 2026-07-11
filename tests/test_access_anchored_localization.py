"""Time-anchored access-point localization.

Operator bug: "the second access voltage/resistance is floating near the
beginning of the pulse!!"  Under the I_mon trigger the scope fires mid-pulse
(pulse onset ≠ 0) and the leading cathodic edge throws a big compliance
transient — sometimes a DOUBLE-BUMP in |dV/dt|.  The old localizer found the
N tallest global |dV/dt| peaks and mapped them to the labelled access points
in TIME ORDER, so two clustered leading-edge peaks shoved the trailing-phase-1
access point back to the pulse start.

The fix: when ``onset_us`` is given, each labelled boundary is localized by a
region-partitioned search anchored to its expected time (onset + cumulative
phase timing) — peak *i* ↔ label *i* by construction.  ``onset_us=None`` keeps
the legacy global-peak path for the calibration caller.
"""
from __future__ import annotations

import dataclasses

import numpy as np

from stimtest.metrics import access_voltage_and_resistance, access_index_labels
from stimtest.waveforms import PulsePattern


def _biphasic_with_delays(width_us=200.0, iph_us=60.0, dd_us=60.0):
    p = PulsePattern.biphasic(amplitude_ua=100.0, polarity=-1)
    ph0 = dataclasses.replace(p.phases[0], width_us=width_us,
                              delay_after_us=iph_us, amplitude_ua=-100.0)
    ph1 = dataclasses.replace(p.phases[1], width_us=width_us,
                              delay_after_us=dd_us, amplitude_ua=+100.0)
    return dataclasses.replace(p, phases=[ph0, ph1])


def _synthetic_vmon(onset, pat, *, lead_clutter=False):
    """A V_mon trace with the pulse beginning at ``onset`` µs (I_mon-trigger
    style).  Optionally injects a tall leading-edge compliance double-bump."""
    w = pat.phases[0].width_us
    iph = pat.phases[0].delay_after_us
    t = np.linspace(-200.0, onset + 2 * w + iph + 200.0, 6000)
    R, amp = 2000.0, 100e-6
    v = np.zeros_like(t)
    m1 = (t >= onset) & (t < onset + w)
    v[m1] = -R * amp - 0.0005 * (t[m1] - onset)
    a0 = onset + w + iph
    m2 = (t >= a0) & (t < a0 + w)
    v[m2] = +R * amp + 0.0005 * (t[m2] - a0)
    if lead_clutter:
        # A spike PAIR right at the cathodic leading edge — the pattern that
        # used to spawn two clustered global peaks and steal the trail-ph1
        # slot.  Taller than the genuine trailing IR steps.
        b1 = (t >= onset) & (t < onset + 4.0)
        v[b1] += -1.5
        b2 = (t >= onset + 4.0) & (t < onset + 8.0)
        v[b2] += +1.2
    return t, v


def test_anchored_places_all_access_points_at_their_boundaries():
    onset, w, iph = 100.0, 200.0, 60.0
    pat = _biphasic_with_delays(w, iph, 60.0)
    t, v = _synthetic_vmon(onset, pat, lead_clutter=True)
    labels = access_index_labels(pat)
    assert labels == [(0, "lead"), (0, "trail"), (1, "lead"), (1, "trail")]

    va, ra, idx = access_voltage_and_resistance(t, v, pat, onset_us=onset)
    times = [float(t[i]) for i in idx]
    expected = [onset, onset + w, onset + w + iph, onset + 2 * w + iph]
    for got, exp, (_, role) in zip(times, expected, labels):
        assert abs(got - exp) < 25.0, (role, got, exp)


def test_trailing_phase1_does_not_drift_to_pulse_start():
    """The reported symptom: V_a2 (trail-ph1) must sit near the phase-1 END,
    NOT back near the leading edge, even with leading-edge clutter."""
    onset, w = 100.0, 200.0
    pat = _biphasic_with_delays(w, 60.0, 60.0)
    t, v = _synthetic_vmon(onset, pat, lead_clutter=True)
    _, _, idx = access_voltage_and_resistance(t, v, pat, onset_us=onset)
    trail_ph1_t = float(t[idx[1]])
    # It belongs at ~onset+w (300 µs), and must be well past the leading edge.
    assert trail_ph1_t > onset + 0.5 * w, trail_ph1_t
    assert abs(trail_ph1_t - (onset + w)) < 25.0, trail_ph1_t


def test_onset_none_preserves_legacy_path_on_clean_data():
    """The calibration caller (onset_us=None) must keep working — on a clean
    onset≈0 biphasic the legacy global-peak path still localizes all four."""
    pat = _biphasic_with_delays(200.0, 60.0, 60.0)
    t, v = _synthetic_vmon(0.0, pat, lead_clutter=False)
    va, ra, idx = access_voltage_and_resistance(t, v, pat, onset_us=None)
    assert len(va) == len(ra) == len(idx) == 4
    assert all(0 <= i < t.size for i in idx)


def test_gaussian_has_no_access_points_like_sinusoidal():
    """Operator: "Gaussian shape should not have access points, like
    sinusoidal."  A gaussian phase ramps smoothly from ~4 % of peak at
    the truncated boundary — no current STEP, so no IR jump to read
    V_a / R_a off.  ``_phase_step_factors`` previously fell through to
    the rectangular-like (1.0, 1.0) default for gaussian, so every
    boundary produced a (meaningless) access point."""
    from stimtest.metrics import access_index_labels
    from stimtest.waveforms import (Phase, PulsePattern, SHAPE_GAUSSIAN,
                                    SHAPE_SINUSOIDAL)

    def _labels(shape):
        p = PulsePattern(phases=[
            Phase(amplitude_ua=-50.0, width_us=200.0, shape=shape,
                  delay_after_us=20.0),
            Phase(amplitude_ua=+50.0, width_us=200.0, shape=shape,
                  delay_after_us=20.0),
        ], rate_hz=100.0)
        return access_index_labels(p)

    assert _labels(SHAPE_SINUSOIDAL) == []          # the reference case
    assert _labels(SHAPE_GAUSSIAN) == [], \
        "gaussian phases must contribute NO access points"


def test_exp_increasing_has_trailing_access_only():
    """Companion fix: SHAPE_EXP_INCREASING (starts at ~0.7 % of peak,
    ends at peak — the mirror of exp-decay) also previously defaulted
    to rectangular-like; only its TRAILING edge carries a real step."""
    from stimtest.metrics import access_index_labels
    from stimtest.waveforms import Phase, PulsePattern, SHAPE_EXP_INCREASING

    p = PulsePattern(phases=[
        Phase(amplitude_ua=-50.0, width_us=200.0,
              shape=SHAPE_EXP_INCREASING, delay_after_us=20.0),
        Phase(amplitude_ua=+50.0, width_us=200.0,
              shape=SHAPE_EXP_INCREASING, delay_after_us=20.0),
    ], rate_hz=100.0)
    assert access_index_labels(p) == [(0, "trail"), (1, "trail")]
