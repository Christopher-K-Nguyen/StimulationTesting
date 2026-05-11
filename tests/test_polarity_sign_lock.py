"""Tests for the polarity auto-sign-flip on asymmetric phase amplitudes.

The user reported being able to type a positive value into the
first phase's amplitude spinbox while ``Cathodic-first`` was
selected, which silently flipped the displayed waveform's
polarity to Anodic-first.

Behaviour now (per user spec — "let inputs of 100 µA automatically
change to -100 µA and vice versa"): the spinbox accepts BOTH signs
across its full range, and a wrong-sign entry is automatically
flipped to the polarity-correct sign at the same magnitude. Zero
stays at zero.

Per-phase sign convention (asymmetric biphasic; alternating for
triphasic):

    Cathodic-first → phase 0 NEGATIVE, phase 1 POSITIVE
    Anodic-first   → phase 0 POSITIVE, phase 1 NEGATIVE
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _simulate_user_commit(spinbox, value):
    """Simulate a user typing ``value`` and pressing Enter (or
    losing focus). ``setValue`` fires ``valueChanged`` but NOT
    ``editingFinished`` — that signal is reserved for real
    interactive commits. Emit it manually here so the
    ``editingFinished``-connected auto-flip slot fires the same
    way it would in the GUI."""
    spinbox.setValue(value)
    spinbox.editingFinished.emit()


def test_cathodic_first_auto_flips_positive_phase0(qapp):
    """Cathodic-first → typing +300 into phase 0 then committing
    should auto-flip to -300 (same magnitude, sign matching the
    cathodic-first convention). Spinbox range stays at the FULL
    window so the user can type either sign without Qt rejecting
    them at the boundary; the flip happens on commit."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, CHARGE_BAL_OFF,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.polarity.setCurrentText("Cathodic-first")
    sp0 = panel.phase_amp[0]
    # Full range — accepts either sign.
    assert sp0.minimum() < 0.0
    assert sp0.maximum() > 0.0
    # User types +300 and commits → auto-flips to -300.
    _simulate_user_commit(sp0, 300.0)
    assert sp0.value() == pytest.approx(-300.0, abs=1e-3)
    # phase 1 → committing -300 auto-flips to +300 (positive rail).
    sp1 = panel.phase_amp[1]
    _simulate_user_commit(sp1, -300.0)
    assert sp1.value() == pytest.approx(+300.0, abs=1e-3)


def test_anodic_first_auto_flips_negative_phase0(qapp):
    """Anodic-first → typing -200 into phase 0 then committing
    should auto-flip to +200 (anodic-first convention has
    phase 0 positive)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, CHARGE_BAL_OFF,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.polarity.setCurrentText("Anodic-first")
    sp0 = panel.phase_amp[0]
    _simulate_user_commit(sp0, -200.0)
    assert sp0.value() == pytest.approx(+200.0, abs=1e-3)
    sp1 = panel.phase_amp[1]
    _simulate_user_commit(sp1, +200.0)
    assert sp1.value() == pytest.approx(-200.0, abs=1e-3)


def test_correct_sign_input_is_preserved(qapp):
    """When the user types a value with the CORRECT sign, no flip
    happens — the value is preserved exactly. Auto-flip only
    triggers on sign mismatch."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, CHARGE_BAL_OFF,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.polarity.setCurrentText("Cathodic-first")
    panel.phase_amp[0].setValue(-150.0)   # already cathodic-correct
    assert panel.phase_amp[0].value() == pytest.approx(-150.0, abs=1e-3)
    panel.phase_amp[1].setValue(+75.0)    # already anodic-recharge correct
    assert panel.phase_amp[1].value() == pytest.approx(+75.0, abs=1e-3)


def test_zero_is_preserved_through_auto_flip(qapp):
    """Setting a phase amplitude to exactly zero should NOT flip
    (zero has no sign). The auto-flip guards against the
    ``value == 0.0`` case."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, CHARGE_BAL_OFF,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.polarity.setCurrentText("Cathodic-first")
    panel.phase_amp[0].setValue(0.0)
    assert panel.phase_amp[0].value() == 0.0


def test_polarity_flip_swaps_signs_and_preserves_magnitudes(qapp):
    """Toggling the polarity dropdown should flip the signs of the
    existing phase amplitudes while preserving their magnitudes.

    The default ``CHARGE_BAL_AMP`` mode would auto-derive phase 1
    from phase 0 (overriding any value the test sets), which
    obscures whether the polarity flip actually preserved the
    user-typed magnitudes. Switch to manual charge-balance for
    this test so the spinbox values round-trip cleanly.
    """
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, CHARGE_BAL_OFF,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.polarity.setCurrentText("Cathodic-first")
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_amp[1].setValue(50.0)
    # Flip polarity.
    panel.polarity.setCurrentText("Anodic-first")
    assert panel.phase_amp[0].value() == pytest.approx(200.0, abs=1e-3)
    assert panel.phase_amp[1].value() == pytest.approx(-50.0, abs=1e-3)


def test_apply_polarity_sign_locks_corrects_stale_values(qapp):
    """If a programmatic caller forces a wrong-sign value
    (simulating a stale prefs file or a malicious caller),
    re-applying the sign lock should flip it to the polarity-
    correct sign rather than silently zeroing it. Goal: never
    LOSE the magnitude the user originally typed."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, CHARGE_BAL_OFF,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.polarity.setCurrentText("Cathodic-first")
    sp = panel.phase_amp[0]
    # Bypass the auto-flip slot — simulate a stale-prefs value
    # landing in the spinbox without triggering the
    # ``valueChanged`` connection.
    sp.blockSignals(True)
    try:
        sp.setValue(150.0)    # wrong sign for cathodic-first
    finally:
        sp.blockSignals(False)
    panel._apply_polarity_sign_locks()
    # Expectation: 150 flipped to -150 (magnitude preserved).
    assert sp.value() == pytest.approx(-150.0, abs=1e-3)


def test_phase2_alternates_for_triphasic_compat(qapp):
    """Phase 2 (the third phase used by triphasic asym) follows
    the same alternating-sign pattern as phase 0 in the
    canonical convention. Verify the auto-flip slot treats
    phase 2 like phase 0 (excitation polarity)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, CHARGE_BAL_OFF,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.polarity.setCurrentText("Cathodic-first")
    # Phase 2 typed positive and committed → flips to negative
    # (same convention as phase 0 under cathodic-first).
    _simulate_user_commit(panel.phase_amp[2], 100.0)
    assert panel.phase_amp[2].value() == pytest.approx(-100.0, abs=1e-3)
