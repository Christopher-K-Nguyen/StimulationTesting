"""Burst-stimulation model: a PulsePattern with pulses_per_burst>1 groups
N pulses (at the intra-burst rate_hz) into a longer burst_period_us, and
the device arb pattern tiles the pulse N times (device period = the burst
period).  See the burst design (BurstDR / theta-burst terminology)."""
import math

import pytest

from stimtest.waveforms import (
    PulsePattern, build_pat_pairs, build_burst_pat_pairs, SHAPE_RECTANGULAR,
)


def _biphasic_burst(pulses=5, intra_rate_hz=500.0, burst_period_us=25000.0,
                    amp=50.0, width=100.0):
    # a plain biphasic pulse, then mark it as a burst
    p = PulsePattern.biphasic(amplitude_ua=amp, phase_width_us=width,
                              interphase_us=0.0, discharge_us=0.0,
                              rate_hz=intra_rate_hz)
    p.pulses_per_burst = pulses
    p.burst_period_us = burst_period_us
    return p


def test_defaults_are_non_burst():
    p = PulsePattern.biphasic(amplitude_ua=50.0)
    assert p.pulses_per_burst == 1
    assert p.burst_period_us == 0.0
    assert p.is_burst is False
    # non-burst device period == ordinary 1e6/rate
    assert p.device_period_us == pytest.approx(1e6 / p.rate_hz)
    assert p.pulses_per_period == 1


def test_burst_timing_properties():
    # 5 pulses at 500 Hz intra-burst (=2000 µs spacing), burst period 25 ms
    p = _biphasic_burst(pulses=5, intra_rate_hz=500.0, burst_period_us=25000.0)
    assert p.is_burst is True
    assert p.intra_burst_period_us == pytest.approx(2000.0)   # 1e6/500
    # pulse = 100+100 µs (no delays) = 200 µs, gap = 2000-200 = 1800 µs
    assert p.total_pulse_us == pytest.approx(200.0)
    assert p.intra_burst_gap_us == pytest.approx(1800.0)
    # span = (5-1)*2000 + 200 = 8200 µs
    assert p.burst_span_us == pytest.approx(8200.0)
    # inter-burst gap = 25000 - 8200 = 16800 µs
    assert p.inter_burst_gap_us == pytest.approx(16800.0)
    assert p.burst_rate_hz == pytest.approx(1e6 / 25000.0)     # 40 Hz
    assert p.device_period_us == pytest.approx(25000.0)        # burst period
    assert p.pulses_per_period == 5


def test_burst_arb_tiles_pulse_n_times():
    p = _biphasic_burst(pulses=5, intra_rate_hz=500.0)
    single = build_pat_pairs(p)          # one pulse
    burst = build_burst_pat_pairs(p)     # whole burst
    # burst = N copies of the pulse + (N-1) intra-burst gap pairs
    assert len(burst) == 5 * len(single) + 4
    # the gap pairs are zero-current, ~1800 µs
    gaps = [d for (a, d) in burst if a == 0 and d > 1000]
    assert len(gaps) == 4
    assert all(abs(d - 1800) <= 1 for d in gaps)
    # no trailing inter-burst gap in the pairs (device period inserts it):
    assert burst[-len(single):] == single


def test_non_burst_build_is_unchanged():
    p = PulsePattern.biphasic(amplitude_ua=50.0)
    assert build_burst_pat_pairs(p) == build_pat_pairs(p)


def test_validate_rejects_burst_span_over_period():
    # span 8200 µs but period only 5000 µs -> invalid
    p = _biphasic_burst(pulses=5, intra_rate_hz=500.0, burst_period_us=5000.0)
    with pytest.raises(ValueError, match="Burst span"):
        p.validate()


def test_validate_accepts_well_formed_burst():
    p = _biphasic_burst()
    p.validate()   # should not raise


def test_validate_rejects_burst_without_period():
    p = PulsePattern.biphasic(amplitude_ua=50.0)
    p.pulses_per_burst = 3
    p.burst_period_us = 0.0
    with pytest.raises(ValueError, match="burst period"):
        p.validate()


def test_scaled_preserves_burst():
    p = _biphasic_burst(pulses=4, burst_period_us=20000.0)
    q = p.scaled(0.5)
    assert q.pulses_per_burst == 4
    assert q.burst_period_us == pytest.approx(20000.0)
    assert q.is_burst is True
    assert abs(q.phases[0].amplitude_ua) == pytest.approx(25.0)


def test_auto_balance_preserves_burst():
    p = _biphasic_burst(pulses=3, burst_period_us=30000.0)
    b = p.auto_balance()
    assert b.pulses_per_burst == 3
    assert b.burst_period_us == pytest.approx(30000.0)
    assert b.is_burst is True


def test_device_content_signature_is_burst_aware():
    from stimtest.hardware.plexon import PlexonStimulator as PS
    single = PulsePattern.biphasic(amplitude_ua=50.0, phase_width_us=100.0,
                                   interphase_us=0.0, discharge_us=0.0,
                                   rate_hz=500.0)
    burst5 = _biphasic_burst(pulses=5, intra_rate_hz=500.0)
    burst10 = _biphasic_burst(pulses=10, intra_rate_hz=500.0,
                              burst_period_us=40000.0)
    # a burst's .pat content differs from the single pulse, and from a
    # different pulse count — else the content cache would keep a stale burst
    assert PS._content_signature(single) != PS._content_signature(burst5)
    assert PS._content_signature(burst5) != PS._content_signature(burst10)
    # identical bursts -> identical signature (cache hit preserved)
    assert PS._content_signature(burst5) == PS._content_signature(
        _biphasic_burst(pulses=5, intra_rate_hz=500.0))


def test_effective_pulse_rate_hz():
    # non-burst: overall pulses/sec == rate_hz
    single = PulsePattern.biphasic(amplitude_ua=50.0, rate_hz=50.0)
    assert single.effective_pulse_rate_hz == pytest.approx(50.0)
    # burst: N pulses every burst period → pulses_per_burst × burst_rate
    #   5 pulses / 25 ms = 200 pulses/sec (NOT the 500 pps intra rate)
    burst = _biphasic_burst(pulses=5, intra_rate_hz=500.0,
                            burst_period_us=25000.0)
    assert burst.effective_pulse_rate_hz == pytest.approx(200.0)
    assert burst.effective_pulse_rate_hz == pytest.approx(
        burst.pulses_per_burst * burst.burst_rate_hz)


def test_device_period_uses_burst_cycle():
    # non-burst: device period == 1e3/rate ms (unchanged)
    single = PulsePattern.biphasic(amplitude_ua=50.0, rate_hz=50.0)
    assert single.device_period_us / 1000.0 == pytest.approx(1e3 / 50.0)
    # burst: device period == the burst period (ms)
    burst = _biphasic_burst(pulses=5, burst_period_us=25000.0)
    assert burst.device_period_us / 1000.0 == pytest.approx(25.0)


def test_persistence_round_trip():
    from stimtest.persistence import _pattern_dict
    from stimtest.waveforms import Phase
    p = _biphasic_burst(pulses=7, intra_rate_hz=333.0, burst_period_us=33000.0)
    d = _pattern_dict(p)
    assert d["pulses_per_burst"] == 7
    assert d["burst_period_us"] == pytest.approx(33000.0)
    # reconstruct exactly as load_session_npz does
    q = PulsePattern(
        phases=[Phase(**ph) for ph in d["phases"]],
        rate_hz=d["rate_hz"], repetitions=d["repetitions"],
        pulses_per_burst=int(d.get("pulses_per_burst", 1)),
        burst_period_us=float(d.get("burst_period_us", 0.0)),
    )
    assert q.is_burst and q.pulses_per_burst == 7
    assert q.burst_period_us == pytest.approx(33000.0)


def test_legacy_npz_loads_as_non_burst():
    from stimtest.persistence import _pattern_dict
    from stimtest.waveforms import Phase
    d = _pattern_dict(PulsePattern.biphasic(amplitude_ua=50.0))
    d.pop("pulses_per_burst"); d.pop("burst_period_us")   # pre-burst file
    q = PulsePattern(
        phases=[Phase(**ph) for ph in d["phases"]],
        rate_hz=d["rate_hz"], repetitions=d["repetitions"],
        pulses_per_burst=int(d.get("pulses_per_burst", 1)),
        burst_period_us=float(d.get("burst_period_us", 0.0)),
    )
    assert q.is_burst is False


def test_too_many_pulses_for_shaped_burst_raises():
    # a large N of many-point shaped pulses can exceed the SDK cap
    p = PulsePattern.biphasic(amplitude_ua=50.0, symmetric=False,
                              shape="sinusoidal", shape2="sinusoidal",
                              rate_hz=500.0)
    p.pulses_per_burst = 200
    p.burst_period_us = 500000.0
    with pytest.raises(ValueError):
        build_burst_pat_pairs(p)
