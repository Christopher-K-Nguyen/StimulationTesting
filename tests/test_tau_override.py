"""Tests for the user-pinnable τ in pseudo-capacitively-coupled mode.

The asymmetric biphasic "pseudo-cap-coupled" shape pairs a
rectangular cathodic phase with an exp-decay anodic phase whose
time constant τ governs how the decay integrates to the
cathodic charge. By default τ is auto-derived from the locked
geometry (τ = t_a / N in LOCK_WIDTH; τ = Q / (I_a · decay) in
LOCK_AMPLITUDE), but users sometimes want to pin τ explicitly to
match a measured electrode RC value.

The new τ-mode dropdown + τ spinbox in the pattern panel lets
them do that. ``Auto`` keeps the legacy behaviour;
``Manual`` overrides τ in the solver and re-derives the *other*
free parameter under that constraint.

What we cover here:

* Solver-level: ``solve_capacitive_balance`` honors the
  ``tau_override_us`` kwarg in both LOCK_WIDTH (re-solves I_a)
  and LOCK_AMPLITUDE (re-solves t_a) branches.
* Solver-level: the manual-τ saturation path bisects t_flat
  correctly when the user's τ would push the unsaturated peak
  above 1000 µA.
* Solver-level: LOCK_AMPLITUDE manual-τ flags ``infeasible``
  when ``|I_a| · τ < Q_cath`` (the asymptotic ceiling never
  reaches the required charge).
* Panel-level: switching τ-mode toggles the spinbox enable
  state; manual τ flows into the solver call; auto-mode reads
  back the solver-derived τ into the spinbox.
* Panel-level: prefs round-trip ``cap_tau_mode`` and
  ``cap_tau_us``.

The default τ value is 100 µs — it sits in the typical PtIr
microelectrode range (50–200 µs) and matches the auto-derived
τ for a 500 µs anodic width under the τ = t_a / 5 rule, so
flipping from auto to manual on a default-config panel doesn't
visibly change the waveform.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ---------------------------------------------------------------- solver-level


def test_solver_default_path_unchanged_without_override():
    """Calling without ``tau_override_us`` (or with None) produces
    the same result as the legacy solver. Regression guard so the
    new path doesn't drift from auto-derived behaviour."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    bal_legacy = solve_capacitive_balance(
        cathodic_amplitude_ua=200.0, cathodic_width_us=200.0,
        lock=LOCK_WIDTH, locked_value=500.0,
    )
    bal_explicit_none = solve_capacitive_balance(
        cathodic_amplitude_ua=200.0, cathodic_width_us=200.0,
        lock=LOCK_WIDTH, locked_value=500.0,
        tau_override_us=None,
    )
    assert bal_legacy.tau_us == pytest.approx(bal_explicit_none.tau_us)
    assert bal_legacy.anodic_amplitude_ua == pytest.approx(
        bal_explicit_none.anodic_amplitude_ua)


def test_solver_lock_width_manual_tau_solves_for_amplitude():
    """LOCK_WIDTH + manual τ → the solver re-derives I_a from
    Q / (τ · (1 − exp(−t_a/τ))). With t_a = 500 µs, τ = 100 µs the
    decay factor is (1 − exp(−5)) ≈ 0.9933, so I_a should match
    Q / (100 · 0.9933).

    For Ic=200 µA, tc=200 µs → Q = 40 000 µA·µs → I_a ≈ 402.7 µA."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=200.0, cathodic_width_us=200.0,
        lock=LOCK_WIDTH, locked_value=500.0,
        tau_override_us=100.0,
    )
    # τ stays at the user's value (subject to discrete-quantisation
    # refinement, but with 30 nA quanta on a ~400 µA peak the drift
    # is tiny).
    assert bal.tau_us == pytest.approx(100.0, rel=1e-6)
    # I_a from Q / (τ · (1 − exp(−5))). Allow 2 % for the discrete-
    # charge refinement that adjusts I_a to land Q exactly.
    assert bal.anodic_amplitude_ua == pytest.approx(402.7, rel=0.02)
    assert not bal.saturated
    assert not bal.infeasible


def test_solver_lock_width_manual_tau_saturates_when_amp_exceeds_cap():
    """LOCK_WIDTH + manual τ + small t_a → I_a from the
    closed-form would exceed 1000 µA. The solver clamps to the
    hardware ceiling and bisects for t_flat such that the
    flat + decay carry the full Q.

    For Ic=1000 µA, tc=500 µs → Q = 500 000. t_a = 500 µs,
    τ = 100 µs. Pure decay would need I_a ≈ 5 000 µA — way past
    the 1000-µA cap. So the result should report saturation
    with t_flat positive and the user's τ preserved."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=1000.0, cathodic_width_us=500.0,
        lock=LOCK_WIDTH, locked_value=500.0,
        tau_override_us=100.0,
    )
    assert bal.saturated
    # τ pinned at the user's value despite saturation.
    assert bal.tau_us == pytest.approx(100.0, rel=1e-6)
    # Peak clamped at the hardware ceiling.
    assert bal.anodic_amplitude_ua == pytest.approx(1000.0, rel=1e-3)
    # Flat-top width should be positive and < t_a.
    assert bal.flat_width_us > 0.0
    assert bal.flat_width_us < 500.0
    # Charge balance still holds (within discrete refinement).
    assert abs(bal.actual_anodic_charge_nc) == pytest.approx(
        abs(bal.cathodic_charge_nc), rel=0.05)


def test_solver_lock_amplitude_manual_tau_solves_for_width():
    """LOCK_AMPLITUDE + manual τ → the solver re-derives t_a from
    t_a = −τ · ln(1 − Q/(I_a · τ)).

    For Ic=200 µA, tc=200 µs → Q = 40 000 µA·µs. With I_a=500 µA,
    τ = 100 µs → ratio = 40 000 / (500·100) = 0.8 → t_a = −100·ln(0.2)
    ≈ 160.94 µs."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_AMPLITUDE
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=200.0, cathodic_width_us=200.0,
        lock=LOCK_AMPLITUDE, locked_value=500.0,
        tau_override_us=100.0,
    )
    assert bal.tau_us == pytest.approx(100.0, rel=1e-6)
    assert bal.anodic_amplitude_ua == pytest.approx(500.0, rel=1e-3)
    # t_a = −100·ln(0.2) ≈ 160.94 µs. Allow 2 % for refinement.
    assert bal.anodic_width_us == pytest.approx(160.94, rel=0.02)
    assert not bal.infeasible


def test_solver_lock_amplitude_manual_tau_infeasible_when_iat_below_q():
    """LOCK_AMPLITUDE + manual τ → the asymptotic charge ceiling
    is I_a · τ. If that's below Q_cath, no t_a can deliver the
    required charge — flagged ``infeasible``.

    For Ic=200 µA, tc=200 µs → Q = 40 000 µA·µs. With I_a=100 µA,
    τ = 100 µs → I_a·τ = 10 000 < 40 000 → infeasible."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_AMPLITUDE
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=200.0, cathodic_width_us=200.0,
        lock=LOCK_AMPLITUDE, locked_value=100.0,
        tau_override_us=100.0,
    )
    assert bal.infeasible


def test_solver_zero_or_negative_tau_override_falls_back_to_auto():
    """Passing ``tau_override_us=0`` (or a negative value) is
    treated as "no override" — same path as ``None``. Lets a
    caller pass the spinbox value blindly without a separate
    None-check when the user is in auto mode (which still
    reads ``tau_us`` from the spinbox via ``getattr``)."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    bal_none = solve_capacitive_balance(
        cathodic_amplitude_ua=200.0, cathodic_width_us=200.0,
        lock=LOCK_WIDTH, locked_value=500.0,
        tau_override_us=None,
    )
    bal_zero = solve_capacitive_balance(
        cathodic_amplitude_ua=200.0, cathodic_width_us=200.0,
        lock=LOCK_WIDTH, locked_value=500.0,
        tau_override_us=0.0,
    )
    bal_negative = solve_capacitive_balance(
        cathodic_amplitude_ua=200.0, cathodic_width_us=200.0,
        lock=LOCK_WIDTH, locked_value=500.0,
        tau_override_us=-50.0,
    )
    assert bal_none.tau_us == pytest.approx(bal_zero.tau_us)
    assert bal_none.tau_us == pytest.approx(bal_negative.tau_us)


# ---------------------------------------------------------------- panel-level


def test_panel_default_tau_mode_is_auto(qapp):
    """A fresh panel starts in Auto-derive τ mode — preserves the
    historical behaviour for users who don't touch the new
    control."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, TAU_MODE_AUTO,
    )
    panel = PatternControlPanel()
    assert panel.tau_mode_combo.currentData() == TAU_MODE_AUTO


def test_panel_default_tau_value_is_100us(qapp):
    """The τ spinbox defaults to 100 µs — the typical
    PtIr-microelectrode RC range and the auto-derived value for
    a 500 µs t_a + N=5 setup."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    assert panel.tau_us.value() == pytest.approx(100.0)


def test_panel_manual_tau_flows_into_solver(qapp):
    """Switching to Manual τ + setting a value should flow that τ
    into the solver. Verified by checking the τ that ends up on
    the resulting Phase: in pure-decay mode the third (last)
    phase carries the user's τ."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
        TAU_MODE_MANUAL,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_width[1].setValue(500.0)
    # Switch to manual τ + pick a non-default τ.
    tau_idx = panel.tau_mode_combo.findData(TAU_MODE_MANUAL)
    panel.tau_mode_combo.setCurrentIndex(tau_idx)
    panel.tau_us.setValue(250.0)
    pat = panel.pattern()
    # Anodic phase carries τ. Pure-decay path → 1 cath rect + 1
    # anod exp-decay (+ optional zero-amp delay phases). Find the
    # exp-decay phase.
    exp_phases = [p for p in pat.phases if getattr(p, "tau_us", 0) > 0]
    assert exp_phases, "expected at least one exp-decay phase"
    assert exp_phases[0].tau_us == pytest.approx(250.0, rel=1e-3)


def test_panel_auto_tau_reads_back_solver_value(qapp):
    """In Auto-derive τ mode, ``pattern()`` writes the solver's
    chosen τ back into the spinbox so the user sees the live
    derived value. Default config: t_a = 500 µs, N=5 → τ = 100 µs."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
        TAU_MODE_AUTO,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    # Confirm we're in auto mode.
    assert panel.tau_mode_combo.currentData() == TAU_MODE_AUTO
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_width[1].setValue(500.0)
    panel.pattern()
    # Auto-derived τ for t_a = 500, N = 5 is 100 µs (give or take
    # the 30-nA-refinement nudge, which is < 1 µs at this charge).
    assert panel.tau_us.value() == pytest.approx(100.0, abs=2.0)


def test_panel_tau_spinbox_disabled_in_auto_mode(qapp):
    """In auto mode the τ spinbox should be read-only — the value
    is solver-derived and editing it would be a no-op until the
    user flips to manual. The visual cue (greyed out) is the
    panel's standard ``_set_lock_state`` behaviour."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, TAU_MODE_AUTO, TAU_MODE_MANUAL,
    )
    panel = PatternControlPanel()
    # Default = auto → spinbox should be locked.
    assert panel.tau_mode_combo.currentData() == TAU_MODE_AUTO
    assert panel.tau_us.isReadOnly() or not panel.tau_us.isEnabled()
    # Switch to manual → spinbox unlocks.
    idx = panel.tau_mode_combo.findData(TAU_MODE_MANUAL)
    panel.tau_mode_combo.setCurrentIndex(idx)
    assert not panel.tau_us.isReadOnly() and panel.tau_us.isEnabled()


def test_panel_prefs_roundtrip_tau_state(qapp):
    """``current_prefs`` / ``restore_prefs`` round-trip
    ``cap_tau_mode`` and ``cap_tau_us`` so a saved-and-reloaded
    session preserves the user's τ choice."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
        TAU_MODE_MANUAL,
    )
    panel1 = PatternControlPanel()
    panel1.phase_count.setCurrentText(BIPHASIC)
    panel1.symmetry.setCurrentText(ASYMMETRIC)
    idx = panel1.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel1.asym_shape_combo.setCurrentIndex(idx)
    tau_idx = panel1.tau_mode_combo.findData(TAU_MODE_MANUAL)
    panel1.tau_mode_combo.setCurrentIndex(tau_idx)
    panel1.tau_us.setValue(175.0)
    prefs = panel1.current_prefs()
    assert prefs["cap_tau_mode"] == TAU_MODE_MANUAL
    assert prefs["cap_tau_us"] == pytest.approx(175.0)
    # Round-trip through a fresh panel.
    panel2 = PatternControlPanel()
    panel2.restore_prefs(prefs)
    assert panel2.tau_mode_combo.currentData() == TAU_MODE_MANUAL
    assert panel2.tau_us.value() == pytest.approx(175.0)


def test_panel_old_prefs_without_tau_keys_load_cleanly(qapp):
    """Pre-τ prefs files don't contain ``cap_tau_mode`` / ``cap_tau_us``
    keys; ``restore_prefs`` should fall back to the panel's
    defaults rather than crashing."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, TAU_MODE_AUTO,
    )
    panel = PatternControlPanel()
    # Pretend we're loading a legacy prefs file — only the
    # cap-coupled keys that pre-date τ-control.
    legacy = {
        "asym_shape": "cap_coupled",
        "cap_lock": "width",
    }
    panel.restore_prefs(legacy)
    # Defaults preserved.
    assert panel.tau_mode_combo.currentData() == TAU_MODE_AUTO
    assert panel.tau_us.value() == pytest.approx(100.0)


def test_panel_locked_range_readout_uses_tau_in_manual_amp_lock(qapp):
    """In LOCK_AMPLITUDE + manual τ mode, the locked-range
    readout's |I_a| floor is no longer ≈0 — it has to satisfy
    I_a · τ > Q. Floor = Q / τ.

    For Ic=200 µA, tc=200 µs → Q = 40 000 µA·µs. τ = 100 µs →
    floor = 400 µA."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
        TAU_MODE_MANUAL,
    )
    from stimtest.waveforms import LOCK_AMPLITUDE
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    lock_idx = panel.cap_lock_combo.findData(LOCK_AMPLITUDE)
    panel.cap_lock_combo.setCurrentIndex(lock_idx)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(200.0)
    tau_idx = panel.tau_mode_combo.findData(TAU_MODE_MANUAL)
    panel.tau_mode_combo.setCurrentIndex(tau_idx)
    panel.tau_us.setValue(100.0)
    panel.pattern()
    txt = panel._asym_formula_lbl.text()
    # 40 000 / 100 = 400 µA floor — must show up.
    assert "400.000" in txt or "400" in txt
    # The manual-τ caption explains the new constraint.
    assert "manual" in txt.lower() or "asymptotic" in txt.lower()
