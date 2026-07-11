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
    panel.polarity.setCurrentText("Cathodal-first")
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
    panel.polarity.setCurrentText("Anodal-first")
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
    panel.polarity.setCurrentText("Cathodal-first")
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
    panel.polarity.setCurrentText("Cathodal-first")
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
    panel.polarity.setCurrentText("Cathodal-first")
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_amp[1].setValue(50.0)
    # Flip polarity.
    panel.polarity.setCurrentText("Anodal-first")
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
    panel.polarity.setCurrentText("Cathodal-first")
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


def test_amp_excite_display_sign_follows_polarity(qapp):
    """The symmetric-mode excitation amplitude spinbox (``amp_excite``,
    the driving control shown in the log line ``pattern = +5.0 µA …``)
    must carry the polarity-correct SIGN and render an explicit ``+``
    for positive values (user: "I want the GUI input says +5, not just
    5" / "the stimulation/first current amplitude matches sign").

    Cathodic-first → excitation NEGATIVE (``-50.0 µA``); Anodic-first →
    excitation POSITIVE, displayed WITH a leading ``+`` (``+50.0 µA``).
    The pattern's first phase amplitude must agree with the display.
    """
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()

    panel.polarity.setCurrentText("Cathodal-first")
    assert panel.amp_excite.value() < 0.0
    assert panel.amp_excite.lineEdit().text().lstrip().startswith("-")
    assert panel.pattern().phases[0].amplitude_ua < 0.0

    panel.polarity.setCurrentText("Anodal-first")
    assert panel.amp_excite.value() > 0.0
    # Explicit "+" prefix for a positive excitation.
    assert panel.amp_excite.lineEdit().text().lstrip().startswith("+")
    assert panel.pattern().phases[0].amplitude_ua > 0.0


def test_amp_excite_wrong_sign_entry_auto_flips(qapp):
    """Typing a wrong-sign value into ``amp_excite`` and committing
    auto-flips it to the polarity-correct sign at the same magnitude
    (user: "While anodic first, I was able to manually change the
    stimulation current from -5 to 5" — the sign must track polarity)."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    panel.polarity.setCurrentText("Anodal-first")
    # User types a negative value while anodic-first → commit flips to +.
    panel.amp_excite.setValue(-5.0)
    panel._on_amp_excite_value_changed()
    assert panel.amp_excite.value() == pytest.approx(+5.0, abs=1e-3)
    assert panel.amp_excite.lineEdit().text().lstrip().startswith("+")

    panel.polarity.setCurrentText("Cathodal-first")
    # Now a positive entry while cathodic-first → commit flips to -.
    panel.amp_excite.setValue(5.0)
    panel._on_amp_excite_value_changed()
    assert panel.amp_excite.value() == pytest.approx(-5.0, abs=1e-3)


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
    panel.polarity.setCurrentText("Cathodal-first")
    # Phase 2 typed positive and committed → flips to negative
    # (same convention as phase 0 under cathodic-first).
    _simulate_user_commit(panel.phase_amp[2], 100.0)
    assert panel.phase_amp[2].value() == pytest.approx(-100.0, abs=1e-3)


def test_polarity_display_is_cathodal_anodal(qapp):
    """Operator: 'Rename the pulse polarity as "cathodal/anodal" instead
    of "cathodic/anodic"' — the dropdown items carry the new wording."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    items = [panel.polarity.itemText(i) for i in range(panel.polarity.count())]
    assert items == ["Cathodal-first", "Anodal-first"], items


def _sign(x):
    import math
    return int(math.copysign(1.0, x))


def test_cap_coupled_zero_ua_cathodal_carries_signed_zero(qapp):
    """PCC BUG: a pseudo-capacitively-coupled (cap-coupled asymmetric)
    CATHODAL-first pattern started at 0 µA for a VT-max ramp was stored
    with a POSITIVE signed-zero excitation phase (+0.0), so
    ``_pattern_at_amplitude`` recovered polarity via
    ``copysign(1.0, +0.0) = +1.0`` and rebuilt the ramp ANODAL-first
    (operator: "despite setting as cathodal-first, this was anodal-first").

    The excitation phase must carry the polarity-correct SIGNED ZERO so
    the recovered polarity is cathodal (−1)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    from stimtest.experiments.base import ExperimentRunner
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.polarity.setCurrentText("Cathodal-first")
    panel.phase_amp[0].setValue(0.0)          # 0 µA excitation
    base = panel.pattern()
    # Excitation phase carries the cathodal signed-zero.
    assert _sign(base.phases[0].amplitude_ua) == -1, (
        f"cathodal 0 µA excitation should be -0.0, got "
        f"{base.phases[0].amplitude_ua!r}")
    # And the ramp rebuilds CATHODAL-first at a real amplitude.
    grown = ExperimentRunner._pattern_at_amplitude(None, base, 50.0)
    assert grown.phases[0].amplitude_ua == pytest.approx(-50.0), (
        "cathodal-set 0 µA cap-coupled ramp must grow CATHODAL, "
        f"got {grown.phases[0].amplitude_ua}")
    assert grown.phases[1].amplitude_ua == pytest.approx(+50.0)


def test_cap_coupled_zero_ua_anodal_carries_signed_zero(qapp):
    """Anodal-first cap-coupled 0 µA → +0.0 excitation → ramp grows ANODAL."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    from stimtest.experiments.base import ExperimentRunner
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.polarity.setCurrentText("Anodal-first")
    panel.phase_amp[0].setValue(0.0)
    base = panel.pattern()
    assert _sign(base.phases[0].amplitude_ua) == +1
    grown = ExperimentRunner._pattern_at_amplitude(None, base, 50.0)
    assert grown.phases[0].amplitude_ua == pytest.approx(+50.0)
    assert grown.phases[1].amplitude_ua == pytest.approx(-50.0)


def test_rect_asymmetric_zero_ua_cathodal_carries_signed_zero(qapp):
    """The same signed-zero fix applies to a RECT asymmetric 0 µA phase
    (operator: "audit the … asymmetric paths for the same 0µA signed-zero
    issue")."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, CHARGE_BAL_OFF,
    )
    from stimtest.experiments.base import ExperimentRunner
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.polarity.setCurrentText("Cathodal-first")
    panel.phase_amp[0].setValue(0.0)
    panel.phase_amp[1].setValue(0.0)
    base = panel.pattern()
    assert _sign(base.phases[0].amplitude_ua) == -1
    grown = ExperimentRunner._pattern_at_amplitude(None, base, 40.0)
    assert grown.phases[0].amplitude_ua == pytest.approx(-40.0)


def test_legacy_polarity_prefs_map_to_new_wording(qapp):
    """A prefs file saved before the rename carries "Anodic-first";
    restore must map it to "Anodal-first" (setCurrentText on an unknown
    item silently NO-OPS, which would lose the saved selection)."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    assert panel.polarity.currentText() == "Cathodal-first"   # default
    panel.restore_prefs({"polarity": "Anodic-first"})          # legacy text
    assert panel.polarity.currentText() == "Anodal-first"
    # New-format prefs round-trip unchanged.
    panel.restore_prefs({"polarity": "Cathodal-first"})
    assert panel.polarity.currentText() == "Cathodal-first"
