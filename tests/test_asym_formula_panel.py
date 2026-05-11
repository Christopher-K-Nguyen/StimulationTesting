"""Tests for the asymmetric locked-parameter min/max readout.

The label widget under the asymmetric-shape combo (kept its
historical attribute name ``_asym_formula_lbl`` for layout-
stability across the rename) used to render shape-specific
formulas. It now renders the **valid range for the locked
phase-2 parameter** so users can sanity-check whether their
typed values still fit the auto-balance / pseudo-cap-coupled
feasibility envelope.

Which phase-2 knob is "locked" depends on the shape + the active
charge-balance / lock combo:

* Rectangular asymmetric uses ``charge_mode``:
    - ``CHARGE_BAL_AMP`` → user types t_a; |I_a| auto-derived.
        Locked = t_a; min keeps |I_a| ≤ 1000 µA.
    - ``CHARGE_BAL_WID`` → user types |I_a|; t_a auto-derived.
        Locked = |I_a|; min keeps t_a ≤ W_max.
    - ``CHARGE_BAL_OFF`` → both knobs are user-controlled;
        readout is a "manual mode" message.
* Pseudo-capacitively-coupled uses ``cap_lock_combo``:
    - ``LOCK_WIDTH`` → locked = t_a; min = feasibility floor
        (Q / I_max so a 1000-µA flat-top of width t_a can
        deliver the cathodic charge).
    - ``LOCK_AMPLITUDE`` → locked = |I_a|; min ≈ 0,
        max = 1000 µA hardware cap.
* Linear inc-dec / dec-inc → no auto-balance, no lock concept;
    readout is empty.

We assert on the rendered HTML's content (substring matches)
rather than exact strings — the visual formatting is a UI-tweak
surface and exact-match would be brittle. The numeric values we
DO assert on are the locked-parameter floors / caps that
follow directly from the user's first-phase Ic, tc.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def test_readout_empty_in_symmetric_mode(qapp):
    """No asymmetric shape is active → the locked-range label has
    no text. Mirrors the visibility-of-the-row behaviour."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.pattern()
    assert panel._asym_formula_lbl.text() == ""


def test_readout_for_rect_asym_charge_bal_amp_shows_t_a_floor(qapp):
    """Rectangular asym, ``CHARGE_BAL_AMP``: |I_a| is auto-derived
    from t_a, so the locked parameter is t_a. Floor = Q / I_max
    keeps the auto-derived |I_a| within the 1000-µA hardware cap.

    For Ic=200 µA, tc=200 µs → Q = 40 000 µA·µs → t_a_min = 40 µs."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
        CHARGE_BAL_AMP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_AMP)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    # Locked parameter is t_a (the anodic width).
    assert "Locked" in txt
    assert "t" in txt and "a" in txt   # t_a (with a sub)
    # 200 × 200 / 1000 = 40 µs floor.
    assert "40.0" in txt
    # Hardware-cap mention so users know WHY the floor exists.
    assert "1000" in txt
    assert "µA" in txt


def test_readout_for_rect_asym_charge_bal_wid_shows_amp_floor(qapp):
    """Rectangular asym, ``CHARGE_BAL_WID``: t_a is auto-derived
    from |I_a|, so the locked parameter is |I_a|. Floor = Q / W_max
    keeps the auto-derived t_a inside the spinbox's hardware
    width ceiling.

    For Ic=100 µA, tc=400 µs → Q = 40 000 µA·µs. With the spinbox's
    width ceiling at 65535 µs the floor is ~0.61 µA — small but
    nonzero, and the readout should print it."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
        CHARGE_BAL_WID,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_WID)
    panel.phase_amp[0].setValue(-100.0)
    panel.phase_width[0].setValue(400.0)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    # Locked parameter is |I_a|.
    assert "Locked" in txt
    assert "I" in txt and "a" in txt   # I_a
    # Range mentioned in µA.
    assert "µA" in txt
    # The cap (max) should appear — 1000 µA is the spinbox's
    # post-polarity-narrow ceiling under cathodic-first.
    assert "1000" in txt


def test_readout_for_rect_asym_charge_bal_off_shows_manual_message(qapp):
    """Rectangular asym, ``CHARGE_BAL_OFF``: there is no locked
    parameter — the user controls both phase-2 knobs. Readout
    surfaces a "manual mode" message so the user knows the row
    isn't broken; it's just not applicable."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
        CHARGE_BAL_OFF,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_OFF)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    assert "Manual" in txt or "manual" in txt
    assert "no auto-derived bounds" in txt.lower() or "user-" in txt.lower()


def test_readout_for_pseudo_cap_lock_width_shows_feasibility_floor(qapp):
    """Pseudo-cap-coupled, ``LOCK_WIDTH``: locked = t_a. Min is
    the feasibility floor (= Q / I_max + one display step), below
    which even a 1000-µA flat-top can't deliver the cathodic
    charge.

    For Ic=300 µA, tc=200 µs → Q = 60 000 → strict floor = 60 µs.
    ``_refresh_cap_coupled_bounds`` bumps the spinbox minimum by
    one display step (1.0 µs at decimals=0) above the strict
    boundary to avoid the solver flickering between feasible and
    infeasible mid-edit, so the actual displayed floor is 61 µs."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    from stimtest.waveforms import LOCK_WIDTH
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    lock_idx = panel.cap_lock_combo.findData(LOCK_WIDTH)
    panel.cap_lock_combo.setCurrentIndex(lock_idx)
    panel.phase_amp[0].setValue(-300.0)
    panel.phase_width[0].setValue(200.0)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    assert "Locked" in txt
    # 300 × 200 / 1000 = 60 µs strict floor; +1 µs spinbox-step
    # safety bump = 61 µs displayed. Accept either rounding so a
    # later cosmetic tweak to the bump doesn't break the test.
    assert any(v in txt for v in ("61.0", "60.0"))
    # Should mention the feasibility floor / 1000 µA cap.
    assert "feasibility" in txt.lower() or "1000" in txt


def test_readout_for_pseudo_cap_lock_amplitude_shows_amp_range(qapp):
    """Pseudo-cap-coupled, ``LOCK_AMPLITUDE``: locked = |I_a|.
    Any positive amplitude is feasible (t_a stretches as
    needed), so min ≈ 0; max = 1000 µA hardware cap."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    from stimtest.waveforms import LOCK_AMPLITUDE
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    lock_idx = panel.cap_lock_combo.findData(LOCK_AMPLITUDE)
    panel.cap_lock_combo.setCurrentIndex(lock_idx)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    assert "Locked" in txt
    assert "I" in txt and "a" in txt   # I_a
    # Hardware cap should appear.
    assert "1000" in txt
    assert "µA" in txt


# Linear inc-dec / dec-inc tests previously here were removed
# when those shapes moved from the asymmetric dropdown to the
# symmetric dropdown. The asymmetric locked-range readout no
# longer applies to them; instead the symmetric mode shows a
# slope readout — see ``test_paper_pattern_shapes`` for that
# coverage.


def test_readout_visibility_tracks_asymmetric_mode(qapp):
    """The locked-range row should be populated in asymmetric
    biphasic mode and empty in symmetric / triphasic / arbitrary
    modes — same rule as the older formula readout. We check
    the widget's text rather than ``isVisible()`` because the
    panel isn't shown in test (``isVisible()`` requires a shown
    parent tree)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, TRIPHASIC, SYMMETRIC, ASYMMETRIC,
        ASYM_SHAPE_RECT, CHARGE_BAL_AMP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_AMP)
    panel.phase_amp[0].setValue(-100.0)
    panel.phase_width[0].setValue(200.0)
    panel.pattern()
    # Asymmetric biphasic + rect + AMP charge-balance → locked
    # range is populated.
    assert panel._asym_formula_lbl.text() != ""
    # Switch to symmetric → readout clears.
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.pattern()
    assert panel._asym_formula_lbl.text() == ""
    # Switch to triphasic → readout stays clear (no asymmetric
    # concept in triphasic mode at the moment).
    panel.phase_count.setCurrentText(TRIPHASIC)
    panel.pattern()
    assert panel._asym_formula_lbl.text() == ""


def test_readout_for_pseudo_cap_coupled_includes_pulse_rate_range(qapp):
    """In pseudo-cap-coupled mode the locked-range readout should
    also surface the pulse-rate / pulse-period envelope so users
    see the timing range that satisfies charge balance.

    The floor on the rate is the spinbox's hardware minimum
    (0.008 Hz); the ceiling is `1e6 / (pulse_duration + 5 µs)`
    when the interpulse-delay checkbox is on. Period range is
    just the reciprocal."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_width[1].setValue(500.0)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    # Both rate AND period should be present.
    assert "Pulse rate:" in txt
    assert "Pulse period:" in txt
    # The interpulse-gap note (default ON → 5 µs).
    assert "5" in txt and "interpulse gap" in txt.lower()
    # Hardware floor / ceiling mentioned for context.
    assert "0.008" in txt   # RATE_HZ_MIN
    assert "100" in txt and "kHz" in txt   # RATE_HZ_MAX = 100 kHz


def test_readout_rate_range_drops_gap_when_interpulse_off(qapp):
    """When the interpulse-delay checkbox is OFF the 5 µs gap is
    waived, so the min period drops to the bare pulse duration
    and the max rate goes up correspondingly. The readout's note
    text should also flip to "no interpulse gap"."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_width[1].setValue(500.0)
    panel.interpulse_check.setChecked(False)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    assert "no interpulse gap" in txt.lower()
    # And the affirmative "5 µs interpulse gap" string should
    # NOT be in the readout (it would mislead).
    assert "5\xa0µs interpulse gap" not in txt
    assert "5 µs interpulse gap" not in txt


def test_rate_range_in_readout_matches_spinbox_clamp(qapp):
    """The max-rate value in the readout should agree with the
    spinbox's actual maximum (set by ``_update_rate_max``) — both
    are derived from the same ``1e6 / (total_pulse + gap)``
    expression, so any drift would indicate a bug in one path or
    the other.

    For Ic=-1000 µA, tc=500 µs, ta=1250 µs the saturated solution
    runs ~1790 µs total + 5 µs gap → max rate ≈ 557 Hz."""
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
    panel.pattern()
    # Spinbox max (Hz) and the rounded value the readout prints
    # (one decimal place of Hz) should agree to within the
    # readout's own precision.
    spinbox_max_hz = float(panel.rate_pps.maximum())
    expected_str = f"{spinbox_max_hz:.1f}"
    txt = panel._asym_formula_lbl.text()
    # The readout prints the max with `.1f` precision in Hz when
    # below 1 kHz; the spinbox-derived string should be a
    # substring match.
    assert expected_str in txt, (
        f"expected '{expected_str}' (from spinbox max "
        f"{spinbox_max_hz} Hz) in readout, got: {txt!r}")


def test_rate_range_omitted_outside_cap_coupled(qapp):
    """The pulse-rate / period block is appended only in
    pseudo-cap-coupled mode — rect-asym and the linear shapes
    have user-typed pulse durations and don't need the
    auto-derived envelope shown."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
        CHARGE_BAL_AMP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_AMP)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    # Rect-asym readout has the locked-range section but NOT
    # the pulse-rate range.
    assert "Locked" in txt
    assert "Pulse rate:" not in txt
    assert "Pulse period:" not in txt


def test_readout_floor_updates_when_first_phase_changes(qapp):
    """Changing ``Ic`` or ``tc`` should re-derive the locked-
    parameter floor on the next emit (since ``_emit`` calls
    ``_refresh_locked_param_bounds`` after every spinbox tick).

    This is the critical behaviour for users who type their
    cathodic phase first and want to see the resulting anodic-
    width envelope update live."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
        CHARGE_BAL_AMP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_AMP)
    # Q = 100 × 200 = 20 000 → t_a_min = 20 µs.
    panel.phase_amp[0].setValue(-100.0)
    panel.phase_width[0].setValue(200.0)
    panel.pattern()
    assert "20.0" in panel._asym_formula_lbl.text()
    # Bumping Ic to 500 → Q = 100 000 → t_a_min = 100 µs.
    panel.phase_amp[0].setValue(-500.0)
    panel.pattern()
    assert "100.0" in panel._asym_formula_lbl.text()
