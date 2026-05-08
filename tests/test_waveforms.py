"""Unit tests for waveform construction."""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.waveforms import Phase, PulsePattern


def test_biphasic_symmetric_charge_balanced():
    p = PulsePattern.biphasic(amplitude_ua=100, phase_width_us=200, polarity=-1)
    q1 = p.phases[0].charge_nc
    q2 = p.phases[1].charge_nc
    assert q1 < 0 and q2 > 0
    assert q1 + q2 == pytest.approx(0.0, abs=1e-9)


def test_triphasic_2_neg3_1_ratio():
    p = PulsePattern.triphasic(amp_excite_ua=300, polarity=-1)
    amps = [ph.amplitude_ua for ph in p.phases]
    # Ratio should be 2 : -3 : 1, with cathodic-first the largest-magnitude phase
    # comes out negative.
    assert amps[1] == pytest.approx(-300)
    assert amps[0] == pytest.approx(200)
    assert amps[2] == pytest.approx(100)
    # Charge per phase reported is the *excitation* phase (largest |amp|)
    assert p.charge_per_phase_nc == pytest.approx(60.0)  # 300 µA * 200 µs = 60 nC


def test_pattern_polarity():
    cathodic = PulsePattern.biphasic(100, polarity=-1)
    anodic = PulsePattern.biphasic(100, polarity=+1)
    assert cathodic.polarity == -1
    assert anodic.polarity == +1


def test_to_timeseries_shape():
    p = PulsePattern.biphasic(100, phase_width_us=200, interphase_us=20,
                              discharge_us=20, polarity=-1)
    t, i = p.to_timeseries(t_pre_us=50, t_post_us=100, sample_period_us=0.5)
    # Phases should appear at the right places
    assert t[0] == pytest.approx(-50.0)
    assert t.size == i.size
    pre_zero = np.allclose(i[t < 0], 0)
    assert pre_zero
    cathodic_segment = i[(t >= 5) & (t <= 195)]
    assert (cathodic_segment < 0).all()


def test_scaled_pattern_preserves_widths():
    p = PulsePattern.biphasic(100, polarity=-1)
    p2 = p.scaled(2.0)
    for a, b in zip(p.phases, p2.phases):
        assert b.width_us == a.width_us
        assert b.amplitude_ua == a.amplitude_ua * 2
