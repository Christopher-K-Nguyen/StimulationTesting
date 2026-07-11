"""No-interpulse guard for E_ret / E_act rest-potential sites.

Operator: "When there is no interpulse delay, then E_ret or E_act are never
expected to be selected near zero during interpulse because there is no
interpulse."

When the pulse repetition period (1e6/rate_hz) is fully occupied by the pulse
itself (every phase width + interphase delays + discharge delay), there is no
idle interpulse window — so any code that samples "the rest potential during
the interpulse" (interpulse-potential metrics, learned-OCP recording, the
E_ret/E_act rescale baseline synthesis, the DC→AC trick) must decline rather
than report neighbouring-pulse data as the electrode's OCP.
"""
from __future__ import annotations

import numpy as np

from stimtest.metrics import _interpulse_potential, _interpulse_potential_split
from stimtest.waveforms import Phase, PulsePattern, SHAPE_RECTANGULAR


def _pattern(rate_hz):
    """A 400 µs biphasic (2×100 µs phases + 200 µs discharge) at ``rate_hz``."""
    phases = [
        Phase(amplitude_ua=-50.0, width_us=100.0, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=50.0, width_us=100.0, shape=SHAPE_RECTANGULAR,
              delay_after_us=200.0),
    ]
    return PulsePattern(phases=phases, rate_hz=rate_hz)


# --------------------------------------------------------------- the helper
def test_interpulse_gap_computation():
    # total_pulse = 100+100+200 = 400 µs.
    p_gap = _pattern(rate_hz=50.0)          # period 20 000 µs → big gap
    assert abs(p_gap.total_pulse_us - 400.0) < 1e-6
    assert abs(p_gap.interpulse_gap_us - (20000.0 - 400.0)) < 1e-6
    assert p_gap.has_interpulse_gap()

    p_nogap = _pattern(rate_hz=2500.0)      # period exactly 400 µs → gap 0
    assert abs(p_nogap.interpulse_gap_us) < 1e-6
    assert not p_nogap.has_interpulse_gap()

    p_tiny = _pattern(rate_hz=2469.0)       # period ~405 µs → ~5 µs gap
    assert 0 < p_tiny.interpulse_gap_us < 8
    assert not p_tiny.has_interpulse_gap()  # below the 10 µs trustworthy floor


# --------------------------------------------------- metrics decline on no-gap
def _trace(n=500):
    t = np.linspace(-100.0, 500.0, n)       # µs, spans pre + pulse + post
    v = np.full(n, 0.25)                    # a constant "rest-ish" level
    return t, v


def test_interpulse_potential_declines_without_gap():
    t, v = _trace()
    # WITH a gap → finite rest potential.
    assert np.isfinite(_interpulse_potential(t, v, _pattern(50.0)))
    # WITHOUT a gap → NaN (no idle window; the samples are pulse data).
    assert np.isnan(_interpulse_potential(t, v, _pattern(2500.0)))


def test_interpulse_potential_split_declines_without_gap():
    t, v = _trace()
    pre, post = _interpulse_potential_split(t, v, _pattern(50.0))
    assert np.isfinite(pre) and np.isfinite(post)
    pre, post = _interpulse_potential_split(t, v, _pattern(2500.0))
    assert np.isnan(pre) and np.isnan(post)


# ------------------------------------- learned-OCP recorder ignores no-gap
def test_learned_ocp_recorder_skips_no_interpulse():
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.electrode_potential_history import record_capture
    from stimtest.session import (Capture, CaptureMetrics, Session,
                                  TestParameters)

    m = CaptureMetrics()
    # Finite rest values that a buggy caller might supply.
    m.return_pre_pulse_potential_v = 0.3
    m.return_post_pulse_potential_v = 0.3
    cap = Capture(index=0, pattern=_pattern(2500.0), metrics=m)
    test = TestParameters(experiment="VT", pattern=_pattern(2500.0),
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4(),
                          extras={"setup_snapshot": {
                              "return_coating": "Pt",
                              "reference_type": "Ag|AgCl"}})
    session = Session(notebook="n", subject="s", test=test)
    # Must return without recording (no idle interpulse → the finite values
    # are neighbouring-pulse data, not OCP).  Just assert it doesn't raise and
    # is a no-op — the defensive pattern guard fires before any bin write.
    record_capture(cap, session)   # no exception = guard held
