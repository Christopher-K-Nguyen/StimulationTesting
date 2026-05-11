"""Tests for the symmetric-mode additions:

* Slope readout for any linear-variant shape (LIN_INC, LIN_DEC,
  linear_inc_dec, linear_dec_inc).
* Mix-and-match asymmetric shape with per-phase shape combos.
* Auto-migration of old prefs from when linear_inc_dec /
  linear_dec_inc lived in the asymmetric dropdown.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ---------------------------------------------------------------- slope readout


def test_slope_readout_for_lin_inc_in_symmetric_mode(qapp):
    """Symmetric biphasic + SHAPE_LINEAR_INCREASING shows a slope
    of |amp|/width µA/µs on both phases, with up-arrows
    indicating both ramp UP in magnitude."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import SHAPE_LINEAR_INCREASING
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    idx = panel.shape_combo.findData(SHAPE_LINEAR_INCREASING)
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(200.0)
    panel.width_shared.setValue(100.0)
    panel.pattern()
    txt = panel._sym_slope_lbl.text()
    # Slope = 200 / 100 = 2.000 µA/µs.
    assert "2.000" in txt
    assert "µA/µs" in txt
    # Both phases up-ramping.
    assert txt.count("↑") == 2   # up-arrow x 2


def test_slope_readout_for_linear_inc_dec_pair(qapp):
    """Symmetric biphasic + linear_inc_dec shows slopes of
    |amp|/width on each phase with ↑ on phase 1 and ↓ on
    phase 2 (the mirrored pair). The note text mentions
    'Mirrored linear pair' or similar."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    idx = panel.shape_combo.findData("linear_inc_dec")
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(150.0)
    panel.width_shared.setValue(50.0)
    panel.pattern()
    txt = panel._sym_slope_lbl.text()
    # Slope = 150 / 50 = 3.000 µA/µs.
    assert "3.000" in txt
    # Mirror pair → one up, one down.
    assert "↑" in txt   # up
    assert "↓" in txt   # down


def test_slope_readout_empty_for_non_linear_shape(qapp):
    """For non-linear shapes (rectangular, sin, gaussian) the
    slope readout is empty — slope concept doesn't apply."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import (
        SHAPE_RECTANGULAR, SHAPE_SINUSOIDAL, SHAPE_GAUSSIAN,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.amp_excite.setValue(100.0)
    panel.width_shared.setValue(100.0)
    for sid in (SHAPE_RECTANGULAR, SHAPE_SINUSOIDAL, SHAPE_GAUSSIAN):
        idx = panel.shape_combo.findData(sid)
        panel.shape_combo.setCurrentIndex(idx)
        panel.pattern()
        assert panel._sym_slope_lbl.text() == "", (
            f"shape={sid} should give empty slope readout, "
            f"got {panel._sym_slope_lbl.text()!r}")


def test_slope_readout_empty_in_asymmetric_mode(qapp):
    """The slope readout is symmetric-mode-only — it stays
    empty when the panel is in asymmetric mode regardless of
    which asym shape is selected."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.pattern()
    assert panel._sym_slope_lbl.text() == ""


# ---------------------------------------------------------------- mix and match


def test_mix_match_appears_in_asymmetric_dropdown(qapp):
    """The Mix-and-match entry is part of the asymmetric shape
    dropdown."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, ASYM_SHAPE_MIX_MATCH,
    )
    panel = PatternControlPanel()
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_MIX_MATCH)
    assert idx >= 0


def test_mix_match_per_phase_combos_visible_only_when_active(qapp):
    """The per-phase shape comboboxes are only visible when the
    asymmetric shape is set to Mix-and-match. For other asym
    shapes (rect, cap-coupled, etc.) they're hidden."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
        ASYM_SHAPE_RECT, ASYM_SHAPE_MIX_MATCH,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    # Default asym shape (rect) → mix-match row hidden.
    idx_rect = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx_rect)
    # The visibility flag is set; we check ``isVisibleTo()`` is False
    # via the configured-visibility state. Direct ``isVisible()``
    # requires a shown parent tree which the test panel lacks, so
    # check that the widget's ``isHidden()`` reflects the intent
    # via setVisible.
    # Instead use the ``isHidden`` test alongside the visible state
    # the panel sets via setVisible().
    # Easiest: inspect the widget's setVisible call path indirectly
    # by looking at the saved visibility flag — but Qt doesn't
    # expose that. Use ``isVisibleTo(panel)`` which works even
    # without a top-level show.
    assert not panel._mix_phase_row_label.isVisibleTo(panel)
    # Switch to mix-and-match → visible.
    idx_mm = panel.asym_shape_combo.findData(ASYM_SHAPE_MIX_MATCH)
    panel.asym_shape_combo.setCurrentIndex(idx_mm)
    assert panel._mix_phase_row_label.isVisibleTo(panel)


def test_mix_match_applies_per_phase_shapes_to_pattern(qapp):
    """When Mix-and-match is active, ``pattern()`` reads the
    per-phase shape from each combobox and applies it to the
    corresponding phase.

    Yip et al. (2017)'s GA-optimal cochlear-nerve waveform is
    rect cathodic + linear-decreasing anodic (mixed shape
    factors 1.0 and 0.5). Verify the pattern() output matches
    that selection."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
        ASYM_SHAPE_MIX_MATCH, CHARGE_BAL_OFF,
    )
    from stimtest.waveforms import (
        SHAPE_RECTANGULAR, SHAPE_LINEAR_DECREASING,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_MIX_MATCH)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    # Pick rect cathodic + linear-decreasing anodic.
    i_rect = panel.mix_phase_shape_combo[0].findData(SHAPE_RECTANGULAR)
    panel.mix_phase_shape_combo[0].setCurrentIndex(i_rect)
    i_dec = panel.mix_phase_shape_combo[1].findData(SHAPE_LINEAR_DECREASING)
    panel.mix_phase_shape_combo[1].setCurrentIndex(i_dec)
    panel.phase_amp[0].setValue(-100.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_amp[1].setValue(100.0)
    panel.phase_width[1].setValue(200.0)
    pat = panel.pattern()
    assert pat.phases[0].shape == SHAPE_RECTANGULAR
    assert pat.phases[1].shape == SHAPE_LINEAR_DECREASING


def test_mix_match_auto_balance_is_shape_aware(qapp):
    """Auto-balance with mixed shapes (rect cath + linear-dec
    anod) should produce charge-balanced output despite the
    different per-phase shape factors. Specifically, for
    Ic=100 µA, tc=200 µs, ta=200 µs:

      * Q_cath = |I_c| · t_c · duty_rect = 100·200·1.0 = 20000 µA·µs.
      * Q_anod = |I_a| · t_a · duty_lin_dec = |I_a|·200·0.5 = 100·|I_a|.
      * Balance: 100·|I_a| = 20000 → |I_a| = 200 µA.

    Without shape-awareness, auto_balance would give 100 µA
    (treating both as duty 1.0), leaving a 50 % charge
    imbalance. The shape-aware result is 200 µA — the test
    locks that in."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
        ASYM_SHAPE_MIX_MATCH, CHARGE_BAL_AMP,
    )
    from stimtest.waveforms import (
        SHAPE_RECTANGULAR, SHAPE_LINEAR_DECREASING,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_MIX_MATCH)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_AMP)
    i_rect = panel.mix_phase_shape_combo[0].findData(SHAPE_RECTANGULAR)
    panel.mix_phase_shape_combo[0].setCurrentIndex(i_rect)
    i_dec = panel.mix_phase_shape_combo[1].findData(SHAPE_LINEAR_DECREASING)
    panel.mix_phase_shape_combo[1].setCurrentIndex(i_dec)
    panel.phase_amp[0].setValue(-100.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_width[1].setValue(200.0)
    pat = panel.pattern()
    # Auto-balance with shape-duty awareness should give 200 µA
    # (= 2·|I_c|) at the rect-cath / lin-dec-anod config:
    # |I_a| · 200 · 0.5 = 100 · 200 · 1.0 → |I_a| = 200.
    assert abs(pat.phases[1].amplitude_ua) == pytest.approx(200.0, rel=1e-2)
    # Shape-aware balance ⇒ tiny net charge.
    charges = pat.actual_phase_charges_nc()
    assert abs(sum(charges)) < 0.5   # < 500 pC residual


# ---------------------------------------------------------------- prefs migration


def test_prefs_migration_lin_inc_dec_to_symmetric(qapp):
    """Old prefs files (saved before linear_inc_dec moved out
    of the asymmetric dropdown) carry ``asym_shape: linear_inc_dec``.
    On restore, the panel auto-migrates: switches to symmetric
    mode and sets the shape combo to linear_inc_dec."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    prefs = {
        "phase_count": "Biphasic",
        "symmetry": "Asymmetric",   # old config
        "asym_shape": "linear_inc_dec",
        "amp_excite": 250.0,
        "width_shared": 200.0,
    }
    panel.restore_prefs(prefs)
    # Auto-migrated to symmetric.
    assert panel.symmetry.currentText() == "Symmetric"
    # Shape combo selected the moved value.
    assert panel.shape_combo.currentData() == "linear_inc_dec"


def test_prefs_migration_lin_dec_inc_to_symmetric(qapp):
    """Same migration path for ``linear_dec_inc``."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    prefs = {
        "phase_count": "Biphasic",
        "symmetry": "Asymmetric",
        "asym_shape": "linear_dec_inc",
    }
    panel.restore_prefs(prefs)
    assert panel.symmetry.currentText() == "Symmetric"
    assert panel.shape_combo.currentData() == "linear_dec_inc"


def test_prefs_migration_does_not_clobber_existing_shape(qapp):
    """If the old prefs file ALSO has a ``shape`` key (some
    intermediate version saved both), the migration shouldn't
    silently overwrite the user's deliberate symmetric shape
    choice. We only fill in the shape from the moved-asym value
    if the existing shape slot is missing or empty."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    from stimtest.waveforms import SHAPE_RECTANGULAR
    panel = PatternControlPanel()
    prefs = {
        "phase_count": "Biphasic",
        "symmetry": "Asymmetric",
        "asym_shape": "linear_inc_dec",
        "shape": SHAPE_RECTANGULAR,   # explicit symmetric shape choice
    }
    panel.restore_prefs(prefs)
    # Migration switched to Symmetric.
    assert panel.symmetry.currentText() == "Symmetric"
    # But did NOT clobber the explicit shape choice.
    assert panel.shape_combo.currentData() == SHAPE_RECTANGULAR


def test_mix_match_prefs_round_trip(qapp):
    """Mix-and-match per-phase shape selections persist across
    a snapshot / restore cycle."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, ASYM_SHAPE_MIX_MATCH,
    )
    from stimtest.waveforms import (
        SHAPE_GAUSSIAN, SHAPE_EXP_INCREASING,
    )
    panel1 = PatternControlPanel()
    panel1.symmetry.setCurrentText("Asymmetric")
    idx = panel1.asym_shape_combo.findData(ASYM_SHAPE_MIX_MATCH)
    panel1.asym_shape_combo.setCurrentIndex(idx)
    i0 = panel1.mix_phase_shape_combo[0].findData(SHAPE_GAUSSIAN)
    panel1.mix_phase_shape_combo[0].setCurrentIndex(i0)
    i1 = panel1.mix_phase_shape_combo[1].findData(SHAPE_EXP_INCREASING)
    panel1.mix_phase_shape_combo[1].setCurrentIndex(i1)
    prefs = panel1.current_prefs()
    assert prefs["mix_phase_shapes"] == [SHAPE_GAUSSIAN, SHAPE_EXP_INCREASING]
    # Restore into a fresh panel.
    panel2 = PatternControlPanel()
    panel2.restore_prefs(prefs)
    assert (panel2.mix_phase_shape_combo[0].currentData()
            == SHAPE_GAUSSIAN)
    assert (panel2.mix_phase_shape_combo[1].currentData()
            == SHAPE_EXP_INCREASING)
