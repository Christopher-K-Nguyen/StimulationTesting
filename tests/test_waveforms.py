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


def test_triphasic_2_3_1_ratio():
    """Triphasic with the default (2, 3, 1) magnitude ratio under
    cathodic-first polarity produces strict cathodic / anodic /
    cathodic alternation — phase 2 is the OPPOSITE polarity of
    phases 1 and 3. ``polarity`` refers to phase 1, not the
    excitation phase (this is the standard electrochemistry
    "cathodic-first" / "anodic-first" naming, applied to the LEADING
    phase).
    """
    p = PulsePattern.triphasic(amp_excite_ua=300, polarity=-1)
    amps = [ph.amplitude_ua for ph in p.phases]
    # Alternating signs: phase 1 cathodic, phase 2 anodic (opposite),
    # phase 3 cathodic. Magnitudes scale (2, 3, 1) so the largest-
    # magnitude (excitation) phase is phase 2 at 300 µA.
    assert amps[0] == pytest.approx(-200)
    assert amps[1] == pytest.approx(+300)
    assert amps[2] == pytest.approx(-100)
    # Charge per phase reported is the *excitation* phase (largest |amp|).
    assert p.charge_per_phase_nc == pytest.approx(60.0)  # 300 µA * 200 µs = 60 nC


def test_triphasic_anodic_first_alternation():
    """Anodic-first triphasic mirrors cathodic-first: phase 1 anodic,
    phase 2 cathodic, phase 3 anodic. Phase 2 is always the opposite
    polarity of phases 1 and 3 regardless of which polarity is
    chosen.
    """
    p = PulsePattern.triphasic(amp_excite_ua=300, polarity=+1)
    amps = [ph.amplitude_ua for ph in p.phases]
    assert amps[0] == pytest.approx(+200)
    assert amps[1] == pytest.approx(-300)
    assert amps[2] == pytest.approx(+100)


def test_triphasic_zero_ratio_rejected():
    """Zero ratio entries collapse a phase to zero current, breaking
    the alternating-sign invariant. The constructor must reject them
    rather than silently producing a degenerate triphasic.
    """
    with pytest.raises(ValueError, match="must be > 0"):
        PulsePattern.triphasic(amp_excite_ua=300, ratio=(2, 0, 1))
    with pytest.raises(ValueError, match="must be > 0"):
        PulsePattern.triphasic(amp_excite_ua=300, ratio=(0, 3, 1))


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


# ---------------------------------------------------------------------------
# PlexStim arbitrary-waveform (.pat) builder + validator
# ---------------------------------------------------------------------------
from stimtest.waveforms import (
    SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING, SHAPE_SINUSOIDAL,
    SHAPE_HALFPIPE, SHAPE_SPEEDBUMPS, SHAPE_EXP_DECAY,
    _PAT_MAX_PAIRS, build_pat_pairs, format_pat_lines, validate_pat_pairs,
    _PAT_MAX_FIXED_POINTS, _PAT_MIN_SAMPLE_PERIOD_US,
    build_pat_samples_fixed, format_pat_fixed_lines,
    validate_pat_samples_fixed,
)


def _amp_sum_charge_nc(pairs):
    """Discrete charge integral of a .pat pair list, in nC."""
    # amp is in nA, dur is in µs. Charge = sum(nA × µs) × 1e-6 nC/(nA·µs).
    return sum(a * d for a, d in pairs) * 1e-6


def test_pat_biphasic_rect_two_pairs_no_delay():
    """Biphasic with no inter-phase delay produces exactly 2 (amp, dur)
    pairs — one per rectangular phase. Charge balance is exact."""
    p = PulsePattern.biphasic(amplitude_ua=100, phase_width_us=200,
                              polarity=-1, interphase_us=0,
                              discharge_us=0)
    pairs = build_pat_pairs(p)
    assert len(pairs) == 2
    # Cathodic-first: first phase amp negative, second positive.
    assert pairs[0] == (-100_000, 200)
    assert pairs[1] == (+100_000, 200)
    # Charge balance — exact zero for symmetric biphasic.
    assert _amp_sum_charge_nc(pairs) == pytest.approx(0.0, abs=1e-12)


def test_pat_biphasic_with_interphase_and_discharge_delays():
    """Inter-phase and discharge delays show up as zero-amp pairs."""
    p = PulsePattern.biphasic(amplitude_ua=100, phase_width_us=200,
                              polarity=-1, interphase_us=50,
                              discharge_us=300)
    pairs = build_pat_pairs(p)
    # 2 rect phases + 2 delay markers (interphase + discharge).
    assert len(pairs) == 4
    assert pairs[0] == (-100_000, 200)
    assert pairs[1] == (0, 50)            # interphase delay
    assert pairs[2] == (+100_000, 200)
    assert pairs[3] == (0, 300)           # discharge delay


def test_pat_triphasic_three_rect_phases():
    """Triphasic with no delays produces 3 rect pairs that alternate in
    sign and obey the magnitude ratio (2, 3, 1) by default."""
    p = PulsePattern.triphasic(amp_excite_ua=300, polarity=-1,
                                interphase_us=0, discharge_us=0)
    pairs = build_pat_pairs(p)
    assert len(pairs) == 3
    amps = [a for a, _ in pairs]
    # Cathodic-first triphasic: phase 1 cathodic, phase 2 anodic
    # (opposite of phase 1), phase 3 cathodic. Verify the alternation.
    assert amps[0] < 0
    assert amps[1] > 0
    assert amps[2] < 0


def test_pat_pair_count_under_500_for_curved_phase():
    """Curved shapes burn through the 499-pair budget but never exceed
    it — ``curved_sample_budget`` partitions the cap across phases."""
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100, width_us=200,
                  shape=SHAPE_SINUSOIDAL, delay_after_us=0),
            Phase(amplitude_ua=+50,  width_us=400,
                  shape=SHAPE_RECTANGULAR, delay_after_us=0),
        ],
        rate_hz=10.0, repetitions=0,
    )
    pairs = build_pat_pairs(p)
    assert 1 < len(pairs) <= _PAT_MAX_PAIRS
    # The curved phase must dominate the pair count for this pattern.
    assert len(pairs) >= 100


def test_pat_pair_count_with_two_curved_phases():
    """Two curved phases share the budget evenly. Total stays ≤ 499.

    Note: zero-amp pairs aren't *only* the inter-phase delay markers
    — a curved shape that starts at zero amplitude (e.g. linear
    ramp, sine) also has its first sampled pair at amp=0. We
    fingerprint the explicit delay markers by their distinctive
    durations (10 µs and 20 µs here) rather than by amp == 0 alone.
    """
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100, width_us=200,
                  shape=SHAPE_LINEAR_INCREASING, delay_after_us=10),
            Phase(amplitude_ua=+50,  width_us=400,
                  shape=SHAPE_HALFPIPE, delay_after_us=20),
        ],
        rate_hz=10.0, repetitions=0,
    )
    pairs = build_pat_pairs(p)
    assert len(pairs) <= _PAT_MAX_PAIRS
    # Delay markers — pair with amp == 0 AND the exact delay duration.
    has_inter = (0, 10) in pairs
    has_disch = (0, 20) in pairs
    assert has_inter and has_disch


def test_pat_speedbumps_uses_nine_pairs():
    """Speedbumps shape has a fixed 9-pair budget per phase."""
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100, width_us=500,
                  shape=SHAPE_SPEEDBUMPS, bump_count=2,
                  delay_after_us=0),
        ],
        rate_hz=10.0, repetitions=0,
    )
    pairs = build_pat_pairs(p)
    # Speedbumps produces 10 breakpoints → 9 pairs. No delay marker.
    assert len(pairs) == 9


def test_pat_durations_are_all_positive_integers():
    """Every duration must be ≥ 1 µs (PlexStim firmware refuses 0-µs
    pairs on some revisions)."""
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100, width_us=1, shape=SHAPE_RECTANGULAR,
                  delay_after_us=1),
            Phase(amplitude_ua=+100, width_us=1, shape=SHAPE_RECTANGULAR,
                  delay_after_us=0),
        ],
        rate_hz=10.0, repetitions=0,
    )
    pairs = build_pat_pairs(p)
    for amp_nA, dur_us in pairs:
        assert isinstance(dur_us, int)
        assert dur_us >= 1


def test_pat_amplitudes_are_signed_int_nA():
    """Every amplitude is a plain int in nA (no floats / numpy scalars)."""
    p = PulsePattern.biphasic(amplitude_ua=123.4, phase_width_us=200,
                              polarity=-1)
    pairs = build_pat_pairs(p)
    for amp_nA, dur_us in pairs:
        assert isinstance(amp_nA, int)
        # 123.4 µA → 123_400 nA after round.
        if amp_nA != 0:
            assert abs(amp_nA) == 123_400


def test_pat_charge_balance_preserved_for_symmetric_biphasic():
    """A symmetric biphasic (rectangular both phases, equal amplitude
    and width) produces exactly-zero net charge in the .pat output."""
    p = PulsePattern.biphasic(amplitude_ua=200, phase_width_us=300,
                              polarity=-1, interphase_us=0,
                              discharge_us=100)
    pairs = build_pat_pairs(p)
    # Discharge marker is zero-amp so it doesn't affect the balance.
    assert _amp_sum_charge_nc(pairs) == pytest.approx(0.0, abs=1e-12)


def test_pat_charge_balance_within_quantisation_for_sinusoidal():
    """Sinusoidal phases approximate the analytic balance to within
    one quantum. The discrete staircase can't be analytically zero
    but stays within the per-step rounding error."""
    excite = Phase(amplitude_ua=-100, width_us=200,
                   shape=SHAPE_SINUSOIDAL, delay_after_us=0)
    # Anodic recovery: half-sine of opposite sign + same width carries
    # the same integrated charge by symmetry.
    recover = Phase(amplitude_ua=+100, width_us=200,
                    shape=SHAPE_SINUSOIDAL, delay_after_us=0)
    p = PulsePattern(phases=[excite, recover], rate_hz=10.0, repetitions=0)
    pairs = build_pat_pairs(p)
    # ~600 pairs of ~1 µs at ~100 µA each rounded to the nearest
    # nA — rounding budget ~ 0.5 nA · 600 µs · 1e-6 ≈ 3e-4 nC.
    assert abs(_amp_sum_charge_nc(pairs)) < 1.0


def test_pat_validate_rejects_too_many_pairs():
    with pytest.raises(ValueError, match="caps at"):
        validate_pat_pairs([(0, 1)] * (_PAT_MAX_PAIRS + 1))


def test_pat_validate_rejects_zero_duration():
    with pytest.raises(ValueError, match="below 1"):
        validate_pat_pairs([(100, 1), (0, 0)])


def test_pat_validate_rejects_overrange_amplitude():
    """1.5 mA = 1_500_000 nA exceeds the 1 mA PlexStim ceiling."""
    with pytest.raises(ValueError, match="exceeds"):
        validate_pat_pairs([(1_500_000, 100)])


def test_pat_validate_rejects_float_amplitude():
    """Non-int types in either position are rejected."""
    with pytest.raises(ValueError, match="plain ints"):
        validate_pat_pairs([(100.5, 200)])
    with pytest.raises(ValueError, match="plain ints"):
        validate_pat_pairs([(100, 200.0)])


def test_pat_validate_rejects_empty():
    with pytest.raises(ValueError, match="empty pattern"):
        validate_pat_pairs([])


def test_pat_format_lines_starts_with_variable_header():
    """The .pat file's first line is literally ``Variable`` per the
    PlexStim 2.0 SDK user guide §8.5.2."""
    pairs = [(-100_000, 200), (+100_000, 200)]
    lines = format_pat_lines(pairs)
    assert lines[0] == "Variable"
    # Subsequent lines alternate amp / duration.
    assert lines[1] == "-100000"
    assert lines[2] == "200"
    assert lines[3] == "100000"
    assert lines[4] == "200"
    assert len(lines) == 5  # header + 2 pairs × 2 lines


def test_pat_asymmetric_cap_coupled_pattern():
    """Asymmetric cap-coupled (rect cathodic + exp-decay anodic) — the
    PlexStim path the MEMORY.md note insists must use the arbitrary
    waveform pipeline.

    Verifies the structural constraints only — that the rect pair
    leads, the exp-decay tail fits within the 499-pair cap, and
    every pair is well-formed. Charge balance for arbitrary tau is
    NOT analytically zero (that's :func:`solve_capacitive_balance`'s
    job, exercised separately).
    """
    cath = Phase(amplitude_ua=-100, width_us=200,
                 shape=SHAPE_RECTANGULAR, delay_after_us=0)
    anod = Phase(amplitude_ua=+50, width_us=400,
                 shape=SHAPE_EXP_DECAY, tau_us=80.0, delay_after_us=0)
    p = PulsePattern(phases=[cath, anod], rate_hz=10.0, repetitions=0)
    pairs = build_pat_pairs(p)
    # 1 rect pair + N exp-decay pairs.
    assert pairs[0] == (-100_000, 200)
    assert len(pairs) <= _PAT_MAX_PAIRS
    # Every pair is well-formed.
    for amp_nA, dur_us in pairs:
        assert isinstance(amp_nA, int)
        assert isinstance(dur_us, int)
        assert dur_us >= 1
        assert abs(amp_nA) <= 1_000_000
    # Exp-decay anodic tail decays monotonically from +50 µA toward
    # 0 — peak amp_nA is the second pair (first sample of the curve).
    anodic_pairs = pairs[1:]
    anodic_amps = [a for a, _ in anodic_pairs]
    assert max(anodic_amps) == anodic_amps[0]
    # The decay actually decreases over the phase.
    assert anodic_amps[-1] < anodic_amps[0]


def test_pat_load_arbitrary_uses_helper():
    """The PlexStim driver's ``_load_arbitrary`` consumes the same
    pair list the helper produces — verify the file content matches.
    Hardware-side calls are mocked out."""
    from unittest.mock import MagicMock
    import os
    from stimtest.hardware.plexon import PlexonStimulator
    # Build a fake PlexonStimulator instance bypassing __init__ — we
    # only need _load_arbitrary's pure file-write logic. Match the
    # actual SDK constant for PS_PATTERN_ARB by importing it.
    from stimtest.hardware.pyplexstim.pyplexstimlib import PS_PATTERN_ARB
    stim = PlexonStimulator.__new__(PlexonStimulator)
    stim._stim_n = 1
    stim._lib = MagicMock()
    stim._lib.ps_get_pattern_type.return_value = (PS_PATTERN_ARB, 0)
    stim._lib.ps_set_pattern_type.return_value = 0
    stim._lib.ps_load_arb_pattern.return_value = 0
    stim._pat_path = None
    stim._pat_content_signature = None
    stim._loaded_channels = set()
    stim._validated_channels = set()
    # Override _check to just pass through.  Accept the ``since=`` kwarg
    # that ``_invoke`` passes (the real _check signature gained it).
    stim._check = lambda res, ctx, **kw: None
    stim._channel_content_sig = {}

    p = PulsePattern.biphasic(amplitude_ua=100, phase_width_us=200,
                              polarity=-1, interphase_us=50,
                              discharge_us=0)
    stim._load_arbitrary(channel=1, pattern=p)
    # Read back the file the driver wrote and compare to the helper.
    assert stim._pat_path is not None
    with open(stim._pat_path) as fh:
        on_disk = fh.read()
    expected = "\n".join(format_pat_lines(build_pat_pairs(p))) + "\n"
    assert on_disk == expected
    # Cleanup the temp file.
    try:
        os.unlink(stim._pat_path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# PlexStim Fixed-format (.pat) builder + validator
# ---------------------------------------------------------------------------
def test_pat_fixed_biphasic_sample_count():
    """Biphasic 200/200 µs with no delay → 400 samples at 1 µs/sample."""
    p = PulsePattern.biphasic(amplitude_ua=100, phase_width_us=200,
                              polarity=-1, interphase_us=0,
                              discharge_us=0)
    samples = build_pat_samples_fixed(p, sample_period_us=1)
    assert len(samples) == 400
    # First half cathodic (-100 µA = -100_000 nA), second half anodic.
    assert samples[0] == -100_000
    assert samples[199] == -100_000
    assert samples[200] == +100_000
    assert samples[399] == +100_000


def test_pat_fixed_with_delays():
    """Inter-phase + discharge delays render as zero-amp samples."""
    p = PulsePattern.biphasic(amplitude_ua=100, phase_width_us=100,
                              polarity=-1, interphase_us=50,
                              discharge_us=50)
    samples = build_pat_samples_fixed(p, sample_period_us=1)
    # 100 cath + 50 zero + 100 anod + 50 zero = 300 samples.
    assert len(samples) == 300
    # Inter-phase delay block.
    assert samples[100:150] == [0] * 50
    # Discharge delay block.
    assert samples[250:300] == [0] * 50


def test_pat_fixed_respects_max_points():
    """Total > 999 µs at 1 µs/sample must not exceed _PAT_MAX_FIXED_POINTS."""
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100, width_us=2000,
                  shape=SHAPE_RECTANGULAR, delay_after_us=0),
            Phase(amplitude_ua=+100, width_us=2000,
                  shape=SHAPE_RECTANGULAR, delay_after_us=0),
        ],
        rate_hz=10.0, repetitions=0,
    )
    # 4000 µs at 1 µs/sample would overflow — builder rejects it.
    with pytest.raises(ValueError, match="caps at"):
        build_pat_samples_fixed(p, sample_period_us=1)
    # At 5 µs/sample: 4000/5 = 800 samples — fits.
    samples = build_pat_samples_fixed(p, sample_period_us=5)
    assert len(samples) == 800


def test_pat_fixed_sample_period_floor():
    """Below 1 µs is rejected — that's the PlexStim hardware floor."""
    p = PulsePattern.biphasic(amplitude_ua=100, polarity=-1)
    with pytest.raises(ValueError, match="≥ 1 µs"):
        validate_pat_samples_fixed([0], sample_period_us=0)


def test_pat_fixed_sample_amplitudes_are_int():
    """Every sample is a plain ``int`` in nA (no floats / numpy scalars)."""
    p = PulsePattern.biphasic(amplitude_ua=123.4, polarity=-1)
    samples = build_pat_samples_fixed(p, sample_period_us=10)
    for s in samples:
        assert isinstance(s, int)


def test_pat_fixed_format_lines():
    """Fixed format header is literally ``Fixed`` followed by the
    sample period, then one amp per line."""
    samples = [-100_000, 0, 100_000]
    lines = format_pat_fixed_lines(samples, sample_period_us=2)
    assert lines[0] == "Fixed"
    assert lines[1] == "2"
    assert lines[2:] == ["-100000", "0", "100000"]
    assert len(lines) == 5  # header + period + 3 samples


def test_pat_fixed_validate_rejects_overrange():
    with pytest.raises(ValueError, match="exceeds"):
        validate_pat_samples_fixed([1_500_000], sample_period_us=1)


def test_pat_fixed_validate_rejects_too_many_points():
    with pytest.raises(ValueError, match="caps at"):
        validate_pat_samples_fixed(
            [0] * (_PAT_MAX_FIXED_POINTS + 1), sample_period_us=1)


def test_pat_fixed_validate_rejects_empty():
    with pytest.raises(ValueError, match="empty pattern"):
        validate_pat_samples_fixed([], sample_period_us=1)


def test_pat_fixed_charge_balance_within_quantum_for_symmetric_biphasic():
    """Symmetric biphasic at uniform 1 µs sampling produces exact-zero
    charge balance — every cathodic sample is mirrored by an anodic
    sample of identical magnitude."""
    p = PulsePattern.biphasic(amplitude_ua=200, phase_width_us=300,
                              polarity=-1, interphase_us=0,
                              discharge_us=0)
    samples = build_pat_samples_fixed(p, sample_period_us=1)
    # Charge per sample = nA × µs × 1e-6 nC/(nA·µs); sum must be zero.
    total_nC = sum(samples) * 1e-6
    assert total_nC == pytest.approx(0.0, abs=1e-12)


def test_pat_fixed_zero_duration_pattern_rejected():
    """A pattern with all-zero phase widths can't be sampled at any
    sample period — builder reports the zero total duration."""
    # Construct a pattern whose phases are all zero-width by violating
    # the validator at our level (not the live runtime which would
    # have rejected this earlier). We bypass with .__class__ to keep
    # the test focused on the build_pat_samples_fixed branch.
    p = PulsePattern.__new__(PulsePattern)
    p.phases = []
    p.rate_hz = 10.0
    p.repetitions = 0
    with pytest.raises(ValueError, match="zero total duration"):
        build_pat_samples_fixed(p, sample_period_us=1)
