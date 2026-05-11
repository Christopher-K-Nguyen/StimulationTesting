"""Tests for the offset + τ parameters on symmetric and
mix-and-match shapes.

* **Offset**: baseline-floor amplitude that the shape never drops
  below — turns ``linear_increasing`` into a trapezoidal ramp
  from offset to peak. Applies to any non-rectangular shape;
  ignored for rectangular.
* **τ (tau)**: time constant for the exponential shapes (decay /
  increasing / exp-pair). 0 = canonical W/N derivation.

Both are exposed:
  * Symmetric mode: global offset + τ spinboxes that apply to
    both phases.
  * Mix-and-match asymmetric: per-phase offset spinboxes
    (rectangular phases hide the spinbox).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ---------------------------------------------------------------- offset semantics


def test_offset_turns_linear_inc_into_trapezoidal():
    """A linear-increasing shape with offset = 30 µA ramps from
    30 µA to peak rather than from 0 to peak — a trapezoidal
    waveform. The first breakpoint sits at offset, the last at
    peak."""
    from stimtest.waveforms import (
        shape_breakpoints, SHAPE_LINEAR_INCREASING,
    )
    bps = shape_breakpoints(amplitude_ua=100.0, width_us=200.0,
                            shape=SHAPE_LINEAR_INCREASING,
                            offset_ua=30.0,
                            n_samples=10)
    # First breakpoint near offset, last near peak.
    assert bps[0][1] == pytest.approx(30.0, abs=0.5)
    assert bps[-1][1] == pytest.approx(100.0, abs=0.5)


def test_offset_signs_match_phase_polarity():
    """Offset is stored as a magnitude; the sign comes from the
    phase's amplitude. With amp = −100 µA (cathodic) + offset =
    30, breakpoints ramp from −30 to −100."""
    from stimtest.waveforms import (
        shape_breakpoints, SHAPE_LINEAR_INCREASING,
    )
    bps = shape_breakpoints(amplitude_ua=-100.0, width_us=200.0,
                            shape=SHAPE_LINEAR_INCREASING,
                            offset_ua=30.0,
                            n_samples=10)
    assert bps[0][1] == pytest.approx(-30.0, abs=0.5)
    assert bps[-1][1] == pytest.approx(-100.0, abs=0.5)


def test_offset_clamped_at_peak_magnitude():
    """If the user types an offset larger than |A|, the
    breakpoint generator caps it at |A| so the shape doesn't
    invert (negative duty)."""
    from stimtest.waveforms import (
        shape_breakpoints, SHAPE_LINEAR_INCREASING,
    )
    bps = shape_breakpoints(amplitude_ua=100.0, width_us=200.0,
                            shape=SHAPE_LINEAR_INCREASING,
                            offset_ua=200.0,   # > |A|
                            n_samples=10)
    # Capped → first breakpoint sits at A (offset clamped to A),
    # rest sit at A too (the shape collapses to a flat line at peak).
    for _, a in bps:
        assert a == pytest.approx(100.0, abs=0.5)


def test_offset_ignored_for_rectangular():
    """Rectangular shapes have no offset concept — the breakpoint
    output is unchanged regardless of offset_ua value."""
    from stimtest.waveforms import shape_breakpoints, SHAPE_RECTANGULAR
    bps_no_offset = shape_breakpoints(
        amplitude_ua=100.0, width_us=200.0,
        shape=SHAPE_RECTANGULAR, offset_ua=0.0)
    bps_with_offset = shape_breakpoints(
        amplitude_ua=100.0, width_us=200.0,
        shape=SHAPE_RECTANGULAR, offset_ua=30.0)
    assert bps_no_offset == bps_with_offset


def test_offset_with_exp_decay():
    """Exp-decay + offset: the shape starts at peak, decays
    toward the offset (asymptote)."""
    from stimtest.waveforms import shape_breakpoints, SHAPE_EXP_DECAY
    bps = shape_breakpoints(amplitude_ua=100.0, width_us=200.0,
                            shape=SHAPE_EXP_DECAY, tau_us=40.0,
                            offset_ua=20.0, n_samples=20)
    # First breakpoint near peak (100), last near offset.
    assert bps[0][1] == pytest.approx(100.0, abs=1.0)
    # Last (boundary marker) is approximately offset + small
    # natural-floor term; check it's within a few µA of offset.
    assert bps[-1][1] < 25.0


# ---------------------------------------------------------------- symmetric dropdown


def test_symmetric_dropdown_exp_inc_before_exp_dec(qapp):
    """The symmetric dropdown lists 'Exponential increasing'
    before 'Exponential decreasing' per the user-spec ordering."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, SYMMETRIC_BIPHASIC_SHAPES,
    )
    panel = PatternControlPanel()
    labels = [label for label, _ in SYMMETRIC_BIPHASIC_SHAPES]
    inc_idx = labels.index("Exponential increasing")
    dec_idx = labels.index("Exponential decreasing")
    assert inc_idx < dec_idx


def test_symmetric_dropdown_linear_pairs_after_linear_decreasing(qapp):
    """The order should be: Linear increasing, Linear decreasing,
    Linear inc-dec, Linear dec-inc (the four linear options
    clustered consecutively)."""
    from stimtest.gui.pattern_panel import SYMMETRIC_BIPHASIC_SHAPES
    labels = [label for label, _ in SYMMETRIC_BIPHASIC_SHAPES]
    lin_inc = labels.index("Linear increasing")
    lin_dec = labels.index("Linear decreasing")
    lin_inc_dec = labels.index("Linear increasing → decreasing")
    lin_dec_inc = labels.index("Linear decreasing → increasing")
    # Linear options are consecutive in the expected order.
    assert lin_dec == lin_inc + 1
    assert lin_inc_dec == lin_dec + 1
    assert lin_dec_inc == lin_inc_dec + 1


def test_symmetric_dropdown_uses_spelled_out_labels(qapp):
    """The dropdown labels use the spelled-out 'increasing' /
    'decreasing' rather than 'inc' / 'dec' abbreviations."""
    from stimtest.gui.pattern_panel import SYMMETRIC_BIPHASIC_SHAPES
    labels = [label for label, _ in SYMMETRIC_BIPHASIC_SHAPES]
    # No standalone "Inc" / "Dec" abbreviation as a full word.
    for label in labels:
        words = label.replace("→", " ").split()
        for word in words:
            assert word not in ("Inc", "Dec", "Exp"), (
                f"Found abbreviation in label: {label!r}")


def test_symmetric_dropdown_has_exp_pair_shapes(qapp):
    """The exp_inc_dec and exp_dec_inc shape pairs are
    selectable from the symmetric dropdown."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, SYM_BIPHASIC_SHAPE_PAIRS,
    )
    panel = PatternControlPanel()
    assert "exp_inc_dec" in SYM_BIPHASIC_SHAPE_PAIRS
    assert "exp_dec_inc" in SYM_BIPHASIC_SHAPE_PAIRS
    assert panel.shape_combo.findData("exp_inc_dec") >= 0
    assert panel.shape_combo.findData("exp_dec_inc") >= 0


def test_exp_inc_dec_pair_builds_correct_per_phase_shapes(qapp):
    """``exp_inc_dec`` builds phase 0 = SHAPE_EXP_INCREASING +
    phase 1 = SHAPE_EXP_DECAY (a mirror-symmetric pair where the
    cathodic side rises into and the anodic side decays out of
    the phase boundary)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData("exp_inc_dec")
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(100.0)
    panel.width_shared.setValue(100.0)
    pat = panel.pattern()
    assert pat.phases[0].shape == SHAPE_EXP_INCREASING
    assert pat.phases[1].shape == SHAPE_EXP_DECAY


# ---------------------------------------------------------------- symmetric offset/τ widgets


def test_symmetric_offset_visible_only_for_non_rectangular(qapp):
    """The offset spinbox is hidden for SHAPE_RECTANGULAR and
    visible for any other shape."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import (
        SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING, SHAPE_GAUSSIAN,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    # Rectangular → hidden.
    idx_r = panel.shape_combo.findData(SHAPE_RECTANGULAR)
    panel.shape_combo.setCurrentIndex(idx_r)
    assert not panel.sym_offset_ua.isVisibleTo(panel)
    # Linear increasing → visible.
    idx_li = panel.shape_combo.findData(SHAPE_LINEAR_INCREASING)
    panel.shape_combo.setCurrentIndex(idx_li)
    assert panel.sym_offset_ua.isVisibleTo(panel)
    # Gaussian → visible.
    idx_g = panel.shape_combo.findData(SHAPE_GAUSSIAN)
    panel.shape_combo.setCurrentIndex(idx_g)
    assert panel.sym_offset_ua.isVisibleTo(panel)


def test_symmetric_tau_visible_only_for_exponential_shapes(qapp):
    """The τ spinbox is visible only for the exponential family
    (decay, increasing, exp pairs) and hidden for everything else."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import (
        SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING,
        SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING, SHAPE_GAUSSIAN,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    for sid in (SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING,
                SHAPE_GAUSSIAN):
        idx = panel.shape_combo.findData(sid)
        panel.shape_combo.setCurrentIndex(idx)
        assert not panel.sym_tau_us.isVisibleTo(panel), (
            f"τ should be hidden for shape {sid}")
    for sid in (SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING,
                "exp_inc_dec", "exp_dec_inc"):
        idx = panel.shape_combo.findData(sid)
        panel.shape_combo.setCurrentIndex(idx)
        assert panel.sym_tau_us.isVisibleTo(panel), (
            f"τ should be visible for shape {sid}")


def test_symmetric_offset_flows_into_phase(qapp):
    """Symmetric offset spinbox value flows into both Phase
    objects' ``offset_ua`` field."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import SHAPE_LINEAR_INCREASING
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    idx = panel.shape_combo.findData(SHAPE_LINEAR_INCREASING)
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(100.0)
    panel.width_shared.setValue(200.0)
    panel.sym_offset_ua.setValue(25.0)
    pat = panel.pattern()
    assert pat.phases[0].offset_ua == pytest.approx(25.0)
    assert pat.phases[1].offset_ua == pytest.approx(25.0)


def test_symmetric_tau_flows_into_phase_for_exp_shapes(qapp):
    """Symmetric τ value flows into Phase.tau_us for exponential
    shapes."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import SHAPE_EXP_DECAY
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    idx = panel.shape_combo.findData(SHAPE_EXP_DECAY)
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(100.0)
    panel.width_shared.setValue(200.0)
    panel.sym_tau_us.setValue(60.0)
    pat = panel.pattern()
    assert pat.phases[0].tau_us == pytest.approx(60.0)
    assert pat.phases[1].tau_us == pytest.approx(60.0)


# ---------------------------------------------------------------- asymmetric dropdown pruning


def test_asymmetric_dropdown_is_pruned(qapp):
    """Asymmetric dropdown now contains only three entries:
    Rectangular, Pseudo-capacitively-coupled, Mix and match.
    The Doğan RampUp/Down and Yip exp-biphasic asymmetric
    entries were removed (their shape pairs now live in the
    symmetric dropdown)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, ASYMMETRIC_BIPHASIC_SHAPES,
    )
    panel = PatternControlPanel()
    asym_codes = [code for _, code in ASYMMETRIC_BIPHASIC_SHAPES]
    assert set(asym_codes) == {"rectangular", "cap_coupled", "mix_match"}


# ---------------------------------------------------------------- mix-and-match offset


def test_mix_match_per_phase_offset_spinboxes_exist(qapp):
    """Mix-and-match has two per-phase offset spinboxes — one
    per phase, just like the per-phase shape pickers."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    assert len(panel.mix_phase_offset_ua) == 2


def test_mix_match_offset_hidden_for_rectangular_phase(qapp):
    """The per-phase offset spinbox is hidden when the
    corresponding phase's shape is rectangular."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
        ASYM_SHAPE_MIX_MATCH,
    )
    from stimtest.waveforms import SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_MIX_MATCH)
    panel.asym_shape_combo.setCurrentIndex(idx)
    # Phase 0 rect, Phase 1 linear-increasing.
    i_rect = panel.mix_phase_shape_combo[0].findData(SHAPE_RECTANGULAR)
    panel.mix_phase_shape_combo[0].setCurrentIndex(i_rect)
    i_inc = panel.mix_phase_shape_combo[1].findData(SHAPE_LINEAR_INCREASING)
    panel.mix_phase_shape_combo[1].setCurrentIndex(i_inc)
    # Phase 0 spinbox hidden (rectangular), phase 1 visible.
    assert not panel.mix_phase_offset_ua[0].isVisibleTo(panel)
    assert panel.mix_phase_offset_ua[1].isVisibleTo(panel)


def test_mix_match_offset_flows_into_phase(qapp):
    """The mix-and-match per-phase offset spinbox value flows
    into the Phase's offset_ua field."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
        ASYM_SHAPE_MIX_MATCH, CHARGE_BAL_OFF,
    )
    from stimtest.waveforms import SHAPE_LINEAR_INCREASING
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_MIX_MATCH)
    panel.asym_shape_combo.setCurrentIndex(idx)
    # Set both phases to linear-increasing so offset applies.
    for cb in panel.mix_phase_shape_combo:
        i_inc = cb.findData(SHAPE_LINEAR_INCREASING)
        cb.setCurrentIndex(i_inc)
    panel.mix_phase_offset_ua[0].setValue(15.0)
    panel.mix_phase_offset_ua[1].setValue(40.0)
    pat = panel.pattern()
    assert pat.phases[0].offset_ua == pytest.approx(15.0)
    assert pat.phases[1].offset_ua == pytest.approx(40.0)


# ---------------------------------------------------------------- prefs round-trip


def test_offset_tau_prefs_round_trip(qapp):
    """Symmetric offset / τ and mix-and-match per-phase offsets
    round-trip through current_prefs / restore_prefs."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel1 = PatternControlPanel()
    panel1.sym_offset_ua.setValue(25.0)
    panel1.sym_tau_us.setValue(75.0)
    panel1.mix_phase_offset_ua[0].setValue(10.0)
    panel1.mix_phase_offset_ua[1].setValue(30.0)
    prefs = panel1.current_prefs()
    assert prefs["sym_offset_ua"] == pytest.approx(25.0)
    assert prefs["sym_tau_us"] == pytest.approx(75.0)
    assert prefs["mix_phase_offsets"] == [pytest.approx(10.0),
                                          pytest.approx(30.0)]
    panel2 = PatternControlPanel()
    panel2.restore_prefs(prefs)
    assert panel2.sym_offset_ua.value() == pytest.approx(25.0)
    assert panel2.sym_tau_us.value() == pytest.approx(75.0)
    assert panel2.mix_phase_offset_ua[0].value() == pytest.approx(10.0)
    assert panel2.mix_phase_offset_ua[1].value() == pytest.approx(30.0)
