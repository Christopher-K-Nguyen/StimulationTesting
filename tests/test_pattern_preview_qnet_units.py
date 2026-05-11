"""Tests for the pattern-preview header's Q_net display unit.

The per-phase charge magnitudes (Q_ph) typically read in
nanocoulombs — a 100 µA × 200 µs phase carries 20 nC, well-
matched to the nC unit. The NET charge (Q_net), however, is the
*residual* after charge balancing — saturated cap-coupled is
exact (0 pC), unsaturated cap-coupled and quantised rectangular
biphasic typically leave 0.1–10 pC of residual from 30-nA-grid
amplitude rounding. Reporting Q_net in nC would print ``+0.00 nC``
for nearly every balanced case, hiding the actual residual.

These tests assert that the preview header renders Q_net in
picocoulombs (× 1000 from the underlying nC value).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def test_q_net_displays_in_pc_for_imbalanced_biphasic(qapp):
    """Imbalanced biphasic (-100 µA / +99 µA, 200 µs each) carries
    a 200 pC net residual. The preview header should show that
    value in pC (200), NOT nC (0.20 → would round to 0.00 with
    .2f formatting and look balanced)."""
    from stimtest.gui.pattern_preview import PatternPreview
    from stimtest.waveforms import PulsePattern, Phase, SHAPE_RECTANGULAR
    phases = [
        Phase(amplitude_ua=-100.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
        Phase(amplitude_ua=99.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
    ]
    pat = PulsePattern(phases=phases, rate_hz=100.0)
    prev = PatternPreview()
    prev.set_pattern(pat)
    hdr = prev.header.text()
    # Q_net is in the header.
    assert "Q" in hdr and "net" in hdr
    # Net charge unit is pC, not nC, for the Q_net field. (Q_ph
    # phase magnitudes still appear in nC; the test checks for
    # the SPECIFIC Q_net = ... pC pattern.)
    assert "pC" in hdr
    # The actual residual is ~−200 pC (small discrete-sampling
    # offset off the analytical −200). Look for that magnitude
    # in the rendered text.
    import re
    m = re.search(r"net\s*</sub>\s*</b>\s*=\s*([+-]?\d+\.\d+)\s*pC", hdr)
    assert m is not None, f"couldn't parse Q_net pC value from header: {hdr}"
    qnet_pc = float(m.group(1))
    # Expect about −200 pC (sign convention: cathodic-first → net
    # negative when anodic too small).
    assert -210 < qnet_pc < -190


def test_q_net_displays_in_pc_for_perfectly_balanced_biphasic(qapp):
    """A balanced 100 µA / 100 µA biphasic at the device's
    quantum-aligned amplitudes has 0 pC net residual. The
    header should display ``+0.00 pC`` (or similar) in the
    pC unit, NOT print nC."""
    from stimtest.gui.pattern_preview import PatternPreview
    from stimtest.waveforms import PulsePattern, Phase, SHAPE_RECTANGULAR
    phases = [
        Phase(amplitude_ua=-100.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
        Phase(amplitude_ua=100.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
    ]
    pat = PulsePattern(phases=phases, rate_hz=100.0)
    prev = PatternPreview()
    prev.set_pattern(pat)
    hdr = prev.header.text()
    assert "pC" in hdr
    # The residual should round to zero at .2f precision.
    import re
    m = re.search(r"net\s*</sub>\s*</b>\s*=\s*([+-]?\d+\.\d+)\s*pC", hdr)
    assert m is not None, f"couldn't parse Q_net pC value: {hdr}"
    qnet_pc = float(m.group(1))
    assert abs(qnet_pc) < 1.0   # below 1 pC for an analytically-balanced pulse


def test_saturated_cap_coupled_collapses_two_anodic_subphases_in_qph(qapp):
    """The saturated pseudo-cap-coupled solver builds the anodic
    side as TWO ``Phase`` records — a rectangular flat-top pinned
    at the 1000-µA hardware ceiling, immediately followed (no
    interphase delay) by an exp-decay carrying the remaining
    charge. Internally the device needs both pairs; visually
    they're ONE conceptual second phase.

    The Q_ph readout in the preview header should reflect the
    LOGICAL view: cathodic + combined-anodic, not the
    device-level cathodic + flat + decay split."""
    from stimtest.gui.pattern_preview import PatternPreview
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.phase_amp[0].setValue(-1000.0)
    panel.phase_width[0].setValue(500.0)
    panel.phase_width[1].setValue(1250.0)
    pat = panel.pattern()
    # Underlying Phase list IS three phases — that's what the
    # device plays. The collapse is a display-only concern.
    assert pat.num_phases == 3
    prev = PatternPreview()
    prev.set_pattern(pat)
    hdr = prev.header.text()
    # Pull all Q_ph values rendered with the nC unit.
    import re
    nc_values = [float(v) for v in re.findall(r"([+-]?\d+\.\d+)\s*nC", hdr)]
    # Should be exactly TWO values (cathodic + combined anodic),
    # not three.
    assert len(nc_values) == 2, (
        f"expected 2 logical Q_ph values, got {len(nc_values)}: "
        f"{nc_values} from header {hdr!r}")
    # Cathodic (first) is negative, combined anodic (second) is
    # positive, and their magnitudes match within the
    # discrete-balance precision (~ sub-pC).
    assert nc_values[0] < 0
    assert nc_values[1] > 0
    assert abs(nc_values[0] + nc_values[1]) < 0.01   # < 10 pC


def test_unsaturated_cap_coupled_does_not_collapse_qph(qapp):
    """When the cap-coupled solver doesn't saturate (the anodic
    side is a single exp-decay phase), there's nothing to collapse —
    the pattern is already two phases (cath + decay) and the Q_ph
    summary should show two values directly."""
    from stimtest.gui.pattern_preview import PatternPreview
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    # Modest cathodic that doesn't saturate.
    panel.phase_amp[0].setValue(-100.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_width[1].setValue(500.0)
    pat = panel.pattern()
    assert pat.num_phases == 2   # unsaturated → 2 phases
    prev = PatternPreview()
    prev.set_pattern(pat)
    hdr = prev.header.text()
    import re
    nc_values = [float(v) for v in re.findall(r"([+-]?\d+\.\d+)\s*nC", hdr)]
    assert len(nc_values) == 2


def test_saturated_pair_detector_signature(qapp):
    """``_saturated_cap_coupled_pair`` should return the index of
    the flat-top phase only when the precise rect+decay+same-amp
    signature matches; otherwise None."""
    from stimtest.gui.pattern_preview import _saturated_cap_coupled_pair
    from stimtest.waveforms import (
        Phase, PulsePattern, SHAPE_RECTANGULAR, SHAPE_EXP_DECAY,
    )
    # Saturated cap-coupled signature: rect at +1000 followed by
    # exp_decay at +1000 with no gap.
    cath = Phase(amplitude_ua=-1000.0, width_us=500.0,
                 shape=SHAPE_RECTANGULAR, delay_after_us=20.0)
    flat = Phase(amplitude_ua=1000.0, width_us=300.0,
                 shape=SHAPE_RECTANGULAR, delay_after_us=0.0)
    decay = Phase(amplitude_ua=1000.0, width_us=900.0,
                  shape=SHAPE_EXP_DECAY, tau_us=180.0,
                  delay_after_us=20.0)
    pat_sat = PulsePattern(phases=[cath, flat, decay], rate_hz=10.0)
    assert _saturated_cap_coupled_pair(pat_sat) == 1

    # Negative case 1: no decay phase (rect-asym biphasic).
    pat_rect = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
        Phase(amplitude_ua=100.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
    ], rate_hz=10.0)
    assert _saturated_cap_coupled_pair(pat_rect) is None

    # Negative case 2: rect followed by exp-decay but with an
    # interphase delay between (not contiguous flat+decay).
    pat_with_gap = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
        Phase(amplitude_ua=1000.0, width_us=300.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),  # gap!
        Phase(amplitude_ua=1000.0, width_us=900.0,
              shape=SHAPE_EXP_DECAY, tau_us=180.0,
              delay_after_us=20.0),
    ], rate_hz=10.0)
    assert _saturated_cap_coupled_pair(pat_with_gap) is None

    # Negative case 3: rect+decay but opposite signs.
    pat_opposite = PulsePattern(phases=[
        Phase(amplitude_ua=-1000.0, width_us=300.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=0.0),
        Phase(amplitude_ua=+1000.0, width_us=900.0,
              shape=SHAPE_EXP_DECAY, tau_us=180.0,
              delay_after_us=20.0),
    ], rate_hz=10.0)
    assert _saturated_cap_coupled_pair(pat_opposite) is None


def test_q_phase_still_displays_in_nc(qapp):
    """The Q_ph (per-phase) values stay in nC — they're multi-nC
    magnitudes (100 µA × 200 µs = 20 nC) and reading those in pC
    would be unwieldy (20 000 pC)."""
    from stimtest.gui.pattern_preview import PatternPreview
    from stimtest.waveforms import PulsePattern, Phase, SHAPE_RECTANGULAR
    phases = [
        Phase(amplitude_ua=-100.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
        Phase(amplitude_ua=100.0, width_us=200.0,
              shape=SHAPE_RECTANGULAR, delay_after_us=20.0),
    ]
    pat = PulsePattern(phases=phases, rate_hz=100.0)
    prev = PatternPreview()
    prev.set_pattern(pat)
    hdr = prev.header.text()
    # Q_ph is rendered before Q_net in the summary line; check
    # both nC (Q_ph) and pC (Q_net) units coexist in the header.
    assert "nC" in hdr
    assert "pC" in hdr
    # And the per-phase charge values are recognisable nC magnitudes.
    # 100 µA × 200 µs = 20 nC; the actual rendered string contains
    # ``-20.0`` (cathodic) and ``+20.0`` (anodic), both followed by
    # nC.
    import re
    nc_values = re.findall(r"([+-]?\d+\.\d+)\s*nC", hdr)
    assert len(nc_values) >= 2, (
        f"expected ≥2 per-phase nC values in header, got "
        f"{nc_values} from {hdr!r}")
