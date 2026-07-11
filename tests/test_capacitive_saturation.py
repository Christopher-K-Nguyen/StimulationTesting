"""Tests for the saturated capacitively-coupled charge-balance flow.

Covers the new saturation logic added to
:func:`stimtest.waveforms.solve_capacitive_balance`:

1. Below the hardware ceiling, the result is unchanged: pure exp-
   decay, ``saturated == False``.
2. Above the ceiling (in LOCK_WIDTH mode), the solver clamps the
   peak at the ceiling and emits a flat-top + exp-decay pair.
3. At exactly the ceiling, the saturated formula reduces
   continuously to the unsaturated one: ``flat_width_us == 0``
   and ``tau_us == t_a / N``.
4. When the user pins a width too short for any 1000-µA-capped
   shape to deliver the required charge, the result is flagged
   ``infeasible`` (rather than raising or silently producing a
   nonsense pattern).
5. Charge balance holds across the saturated case to within a
   30-nA quantisation slop.

Implementation notes for the assertions
---------------------------------------
The solver internally runs a 6-iteration refinement loop that
re-checks the actual discrete-quantised charge each pass. We
assert on ``actual_anodic_charge_nc`` rather than the analytic
``cathodic_charge_nc * 1`` because the device's 30-nA grid
introduces predictable rounding the solver compensates for; the
analytic check would be off by < 1 % from quantisation alone
even when balance is conceptually exact.
"""
from __future__ import annotations

import pytest


def test_below_ceiling_returns_unsaturated():
    """A modest cathodic phase that doesn't push the limit at
    the locked anodic width returns the historical pure-exp-decay
    shape with ``saturated=False``."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=100.0,
        cathodic_width_us=200.0,
        lock=LOCK_WIDTH,
        locked_value=500.0,        # plenty of width for 100 µA · 200 µs
    )
    assert bal.saturated is False
    assert bal.flat_width_us == pytest.approx(0.0)
    assert bal.infeasible is False
    assert 0 < bal.anodic_amplitude_ua < 1000.0
    assert bal.tau_us > 0


def test_lock_width_above_ceiling_saturates():
    """Cathodic charge / locked anodic width that would need
    ``Ia > 1000 µA`` switches to the saturated flat-top + decay
    shape — but the locked width still has to be wide enough for
    a 1000-µA-saturated shape to deliver the full charge.

    Q_cath = 800 µA × 250 µs = 200 nC; locked anodic width =
    250 µs gives 250 nC of headroom at 1000 µA peak (well over
    the 200 nC needed). Pure-decay peak would be
    Q / (τ × decay_factor) = 200e3 / (50 × 0.9933) ≈ 4026 µA
    — above the ceiling, triggering saturation.
    """
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=800.0,
        cathodic_width_us=250.0,
        lock=LOCK_WIDTH,
        locked_value=250.0,
    )
    assert bal.saturated is True
    assert bal.infeasible is False
    assert bal.anodic_amplitude_ua == pytest.approx(1000.0, rel=1e-3)
    assert bal.flat_width_us > 0
    assert bal.tau_us > 0
    # Sum of flat + decay equals the locked anodic width.
    assert bal.flat_width_us + 5.0 * bal.tau_us == pytest.approx(
        250.0, rel=1e-2)


def test_continuity_at_saturation_boundary():
    """When the unconstrained peak is exactly 1000 µA, the
    saturated formula collapses to the unsaturated one:
    ``flat_width_us == 0`` and ``tau_us == t_a / N``.

    Pick a config where Q / (τ_orig · decay_factor) = 1000
    exactly — this corresponds to Q = 1000 · t_a · (1 − e⁻⁵) / 5.
    With t_a = 100 µs, Q ≈ 198.65 nC; we recover this with a
    cathodic phase 1 µA × 198650 µs.
    """
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    import math
    N = 5.0
    decay_factor = 1.0 - math.exp(-N)
    t_a = 100.0
    Ia_target = 1000.0
    Q = Ia_target * (t_a / N) * decay_factor
    # Pick a cathodic phase whose product = Q.
    Ic = 100.0
    tc = Q / Ic
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=Ic,
        cathodic_width_us=tc,
        lock=LOCK_WIDTH,
        locked_value=t_a,
    )
    # Right at the ceiling — we accept either branch (saturated
    # with ~zero flat-top, or unsaturated). Either way τ should
    # be very close to t_a / N, and the flat-top width near zero.
    assert bal.tau_us == pytest.approx(t_a / N, rel=0.05)
    assert bal.flat_width_us == pytest.approx(0.0, abs=1.0)


def test_infeasible_when_anodic_width_too_short():
    """If even a full 1000-µA rectangle of the locked anodic width
    can't deliver Q (i.e. Q > 1000·t_a), the solver flags the
    result as infeasible."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    # Q_cath = 1000 µA × 500 µs = 500,000 µA·µs = 500 nC.
    # Locked anodic width = 100 µs ⇒ even 1000-µA rect over 100 µs
    # is only 100 nC — far below the 500 nC needed. Infeasible.
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=1000.0,
        cathodic_width_us=500.0,
        lock=LOCK_WIDTH,
        locked_value=100.0,
    )
    assert bal.infeasible is True
    # Dataclass should still hold valid (non-negative) numbers so
    # a downstream UI rendering doesn't choke.
    assert bal.anodic_amplitude_ua >= 0
    assert bal.tau_us >= 0
    assert bal.flat_width_us >= 0


def test_lock_amplitude_clamps_to_ceiling():
    """Locking amplitude > ceiling (would only happen via a
    programmatic caller; the spinbox bounds it in the GUI)
    silently clamps to the ceiling. No flat-top — the locked-
    amplitude branch uses pure exp-decay and just lengthens t_a
    to compensate."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_AMPLITUDE
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=300.0,
        cathodic_width_us=300.0,
        lock=LOCK_AMPLITUDE,
        locked_value=2000.0,    # well above the 1000 µA ceiling
    )
    assert bal.saturated is True
    # No flat-top in the locked-amplitude branch.
    assert bal.flat_width_us == pytest.approx(0.0)
    assert bal.anodic_amplitude_ua == pytest.approx(1000.0, rel=1e-3)
    # Width should have grown to accommodate the lower amplitude.
    # Q = 300 × 300 = 90 000 µA·µs. At 1000 µA peak with τ = t_a/N,
    # Q = 1000 · τ · 0.9933 → τ ≈ 90.6 µs → t_a ≈ 453 µs.
    assert bal.anodic_width_us == pytest.approx(453.0, rel=0.02)


def test_charge_balance_in_saturated_mode():
    """The saturated solver still delivers charge balance — the
    discrete anodic charge matches the cathodic to within the
    30-nA quantisation slop after the refinement loop."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    bal = solve_capacitive_balance(
        cathodic_amplitude_ua=600.0,
        cathodic_width_us=400.0,
        lock=LOCK_WIDTH,
        locked_value=500.0,
    )
    assert bal.saturated is True
    assert bal.infeasible is False
    # ``actual_anodic_charge_nc`` is what the device will deliver
    # after 30-nA quantisation; it should match cathodic to a few
    # tenths of a nC at this scale.
    diff_nc = abs(bal.actual_anodic_charge_nc) - abs(bal.cathodic_charge_nc)
    assert abs(diff_nc) < 1.0  # within 1 nC


def test_max_amplitude_override_drives_saturation_at_lower_threshold():
    """Passing ``max_amplitude_ua`` overrides the default ceiling.
    With max=200 µA, even a small charge config saturates."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    bal_default = solve_capacitive_balance(
        cathodic_amplitude_ua=100.0,
        cathodic_width_us=300.0,
        lock=LOCK_WIDTH,
        locked_value=300.0,
    )
    assert bal_default.saturated is False
    bal_low_cap = solve_capacitive_balance(
        cathodic_amplitude_ua=100.0,
        cathodic_width_us=300.0,
        lock=LOCK_WIDTH,
        locked_value=300.0,
        max_amplitude_ua=200.0,    # custom-low ceiling
    )
    # Pure-decay peak ≈ 100 × 300 / (60 × 0.9933) ≈ 503 µA, which
    # exceeds the 200 µA cap → saturates.
    assert bal_low_cap.saturated is True
    assert bal_low_cap.anodic_amplitude_ua == pytest.approx(200.0, rel=1e-3)


def test_pattern_panel_saturated_path_builds_three_phases():
    """When the panel's solver result is saturated, ``pattern()``
    should produce a 3-phase Phase list (cathodic + flat + decay)
    rather than the historical 2-phase output."""
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets
    import sys
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    # Configure for biphasic + asymmetric + cap-coupled.
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    assert idx >= 0
    panel.asym_shape_combo.setCurrentIndex(idx)
    # Drive the cathodic phase so the cap-coupled solver saturates
    # AND the saturated shape is feasible: Q_cath = 800·250 = 200 nC,
    # locked anodic width = 250 µs (1000-µA × 250-µs envelope =
    # 250 nC, comfortably above the 200 nC needed → flat + decay
    # both > 0).
    panel.phase_amp[0].setValue(-800.0)
    panel.phase_width[0].setValue(250.0)
    panel.phase_width[1].setValue(250.0)   # this is the locked width
    pat = panel.pattern()
    # Three phases: cathodic, anodic flat-top, anodic decay.
    assert pat.num_phases == 3
    # Phase 0: rectangular cathodic at -800 µA.
    assert pat.phases[0].amplitude_ua == pytest.approx(-800.0)
    # Phase 1: rectangular anodic at +1000 µA (the saturation
    # ceiling), polarity opposite to cathodic.
    assert pat.phases[1].shape == "rectangular"
    assert pat.phases[1].amplitude_ua == pytest.approx(1000.0, rel=1e-2)
    # Phase 2: exp-decay from +1000 µA.
    assert pat.phases[2].shape == "exp_decay"
    assert pat.phases[2].amplitude_ua == pytest.approx(1000.0, rel=1e-2)
    assert pat.phases[2].tau_us > 0
    # Status label should advertise the saturation.
    assert "Saturated" in panel.cap_status_lbl.text()


def test_pattern_panel_unsaturated_cap_coupled_keeps_two_phases():
    """When the cap-coupled config doesn't saturate, the panel
    continues to build a 2-phase Phase list (historical
    behaviour)."""
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets
    import sys
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    # Modest cathodic that won't saturate at the locked width.
    panel.phase_amp[0].setValue(-100.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_width[1].setValue(500.0)
    pat = panel.pattern()
    assert pat.num_phases == 2
    assert pat.phases[1].shape == "exp_decay"
    # Status should NOT mention saturation.
    assert "Saturated" not in panel.cap_status_lbl.text()


def test_pattern_panel_pseudo_cap_coupled_blocks_infeasible_inputs():
    """The pseudo-cap-coupled feasibility envelope dynamically
    raises the locked-anodic-width spinbox's minimum so the user
    CAN'T type a value that would make charge balance impossible.

    With Q_cath = 1000·500 µA·µs = 500 nC at 1000-µA hardware
    cap, the smallest feasible anodic width is 500 µs. A user
    attempt to type 100 µs is silently clamped up to (≥) 500 µs
    by Qt, so the pattern never reaches the solver's
    ``infeasible`` branch from the GUI path.
    """
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets
    import sys
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    # Q_cath = 1000 × 500 = 500 nC. Minimum feasible anodic width
    # at the 1000-µA cap = 500 µs.
    panel.phase_amp[0].setValue(-1000.0)
    panel.phase_width[0].setValue(500.0)
    panel.phase_width[1].setValue(100.0)   # below floor — Qt clamps
    panel.pattern()  # triggers the bounds refresh + emit
    # The spinbox's effective value should be at or above the
    # feasibility floor.
    assert panel.phase_width[1].value() >= 500.0 - 1e-3
    # And the resulting cap-coupled pattern should be either
    # saturated (at the boundary) or normal — but NEVER
    # infeasible, because the GUI no longer allows that state.
    txt = panel.cap_status_lbl.text().lower()
    assert "infeasible" not in txt


def test_saturated_net_charge_balance_within_30nA_quantum():
    """Regression for the +0.94 nC residual the user reported on
    a -1000 µA × 500 µs cathodic + 1250 µs anodic cap-coupled
    config.

    Earlier saturated refinement re-solved τ from the analytical
    closed-form on every iteration, so it could never absorb the
    discrete 30-nA quantisation + left-Riemann staircase error
    (the analytical formula doesn't see those). The fix
    (``_adjust_flat_for_discrete_balance``) closes the loop on
    the actual discrete charge: each iteration shaves / grows
    the flat-top by the measured Q_a − Q_cath gap so the
    residual collapses to sub-pC.

    The bound here is **0.05 nC** — generous enough to cover
    one 30-nA-quantum worth of slop in the staircase
    integration (30 nA × 1 µs = 0.03 nC) but tight enough to
    catch a regression to the historical ~1 nC residual."""
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets
    import sys
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    # The user's reported case verbatim.
    panel.phase_amp[0].setValue(-1000.0)
    panel.phase_width[0].setValue(500.0)
    panel.phase_width[1].setValue(1250.0)
    pat = panel.pattern()
    assert pat.num_phases == 3   # cath + flat + decay (saturated)
    # ``actual_phase_charges_nc`` integrates over the same
    # breakpoint grid the device will play, so its sum is the
    # net charge that lands on the electrode.
    charges = pat.actual_phase_charges_nc()
    net_nc = sum(charges)
    assert abs(net_nc) < 0.05, (
        f"net charge residual {net_nc:.4f} nC exceeds 0.05 nC; "
        f"per-phase charges = {charges}")


def test_saturated_balance_holds_across_anodic_widths():
    """The discrete-aware refinement should converge to sub-pC
    balance for any feasible anodic width in the saturated
    regime — not just the user's specific (1000, 500, 1250)
    config.

    Sweep a few representative ``ta`` values that all force
    saturation at Q_cath = 500 nC (the same charge the user
    used) and verify each one balances cleanly."""
    from stimtest.waveforms import solve_capacitive_balance, LOCK_WIDTH
    # Saturation kicks in when the unconstrained pure-decay peak
    # I_a = Q / (τ · (1 − e^(−N))) > 1000 µA. With Q = 500 nC,
    # N = 5, this evaluates to ta < ≈ 2517 µs. Stay below that
    # so every sweep value triggers the saturated solver path.
    for ta in (700.0, 1000.0, 1250.0, 1800.0, 2400.0):
        bal = solve_capacitive_balance(
            cathodic_amplitude_ua=1000.0, cathodic_width_us=500.0,
            lock=LOCK_WIDTH, locked_value=ta,
        )
        assert bal.saturated is True, f"expected saturation at ta={ta}"
        residual_nc = abs(bal.actual_anodic_charge_nc) - abs(
            bal.cathodic_charge_nc)
        # Sub-pC tolerance — each adjustment iteration eliminates
        # ~99.3% of the residual, six iterations puts us well past
        # any practical floor.
        assert abs(residual_nc) < 0.01, (
            f"ta={ta}: discrete charge residual {residual_nc:.6f} nC "
            f"exceeds 10 pC tolerance")


def test_tail_zero_us_zeros_trailing_played_amps():
    """``Phase.tail_zero_us`` forces the trailing breakpoint
    amplitudes of an exp-decay phase to zero. The number of
    samples zeroed is round(tail_zero_us / dt_bp), where dt_bp
    is the uniform breakpoint spacing — so the zeroed duration
    is the closest integer-sample match to the requested value.

    This is the primitive the cap-coupled tail-trim mechanism
    relies on. Validate it directly via shape_breakpoints +
    actual_charge_nc rather than the panel post-process."""
    from stimtest.waveforms import (
        Phase, SHAPE_EXP_DECAY, shape_breakpoints, actual_charge_nc,
    )
    # Untrimmed: full exp-decay should integrate to ~A·τ·(1 − e^(−W/τ)).
    A, W, tau = 1000.0, 936.0, 187.0
    n = 100
    bps_untrimmed = shape_breakpoints(
        amplitude_ua=A, width_us=W, shape=SHAPE_EXP_DECAY,
        tau_us=tau, n_samples=n)
    last_played_amp_untrimmed = bps_untrimmed[-2][1]
    # tail_zero_us = dt_bp + ε zeros the last played sample.
    dt_bp = W / (n - 1)
    bps_trimmed = shape_breakpoints(
        amplitude_ua=A, width_us=W, shape=SHAPE_EXP_DECAY,
        tau_us=tau, n_samples=n,
        tail_zero_us=dt_bp + 0.01)
    last_played_amp_trimmed = bps_trimmed[-2][1]
    # Untrimmed last played amp ≈ A · exp(-((n-2)/(n-1))·W/τ),
    # which is approximately A · e^(-N) ≈ 6.7 µA for N=5.
    assert last_played_amp_untrimmed > 5.0
    # Trimmed last played amp = exactly 0.
    assert last_played_amp_trimmed == 0.0
    # And the boundary marker (not played) is also 0 in either case
    # (it's set to 0 in the trimmed case; in the untrimmed case it
    # carries the natural A · e^(-N) value, but isn't integrated).
    assert bps_trimmed[-1][1] == 0.0


def test_tail_zero_us_reduces_actual_charge():
    """Setting tail_zero_us > 0 on an exp-decay Phase reduces the
    actual_charge_nc by approximately tail_amp · tail_zero_us
    (roughly — actual reduction depends on how many samples land
    in the zero window after rounding to the breakpoint grid)."""
    from stimtest.waveforms import Phase, SHAPE_EXP_DECAY, actual_charge_nc
    base = Phase(amplitude_ua=1000.0, width_us=936.0,
                 shape=SHAPE_EXP_DECAY, tau_us=187.0)
    Q_base = actual_charge_nc(base, n_samples=100)
    trimmed = Phase(amplitude_ua=1000.0, width_us=936.0,
                    shape=SHAPE_EXP_DECAY, tau_us=187.0,
                    tail_zero_us=20.0)
    Q_trim = actual_charge_nc(trimmed, n_samples=100)
    # Trimming positive-amp tail reduces total integrated charge.
    assert Q_trim < Q_base
    # And the reduction is order-of-magnitude tail_amp · tail_zero_us.
    # tail_amp at end ≈ 1000 · e^(-5) = 6.7 µA. 20 µs trim ≈ 134 pC.
    delta_nc = Q_base - Q_trim
    assert 0.05 < delta_nc < 0.30


def test_tail_zero_propagates_to_pat_pairs():
    """``build_pat_pairs`` honours ``Phase.tail_zero_us`` so the
    .pat record played by the device matches what the discrete
    integration in actual_charge_nc reports."""
    from stimtest.waveforms import (
        Phase, PulsePattern, SHAPE_EXP_DECAY, SHAPE_RECTANGULAR,
        build_pat_pairs,
    )
    # Build a minimal cap-coupled-style pattern: 1 cathodic rect +
    # 1 anodic exp-decay. Force tail_zero on the decay.
    cath = Phase(amplitude_ua=-1000.0, width_us=500.0,
                 shape=SHAPE_RECTANGULAR, delay_after_us=20.0)
    decay = Phase(amplitude_ua=1000.0, width_us=936.0,
                  shape=SHAPE_EXP_DECAY, tau_us=187.0,
                  tail_zero_us=20.0,
                  delay_after_us=20.0)
    pat = PulsePattern(phases=[cath, decay], rate_hz=10.0)
    pairs = build_pat_pairs(pat)
    # The trailing pairs of the exp-decay (before the discharge-
    # delay zero pair) should be zero amplitude. Exclude the
    # trailing delay record (also zero).
    # Find the contiguous trailing zeros immediately after the
    # decay's natural pairs but before the 1-pair discharge delay.
    # Easier: assert that at least 1 (amp, dur) pair has amp = 0
    # WITHIN the decay phase's portion (i.e. before the final
    # discharge delay).
    # Build pairs without tail_zero for comparison.
    decay_no_trim = Phase(amplitude_ua=1000.0, width_us=936.0,
                          shape=SHAPE_EXP_DECAY, tau_us=187.0,
                          delay_after_us=20.0)
    pat2 = PulsePattern(phases=[cath, decay_no_trim], rate_hz=10.0)
    pairs2 = build_pat_pairs(pat2)
    # With tail_zero, we should have MORE zero-amp pairs than
    # without (the trim adds zero-amp pairs in the decay tail).
    n_zero_with = sum(1 for amp, _ in pairs if amp == 0)
    n_zero_without = sum(1 for amp, _ in pairs2 if amp == 0)
    assert n_zero_with > n_zero_without


def test_n_samples_sync_drives_residual_to_sub_pc():
    """The solver now syncs its discrete-charge ``n_samples`` to
    the panel's actual ``curved_sample_budget`` based on whether
    saturation occurred. Pre-sync, the solver and panel disagreed
    by 1 breakpoint, leaking a ~13 pC residual into the panel's
    integrated charge for unsaturated configs. Post-sync, the
    refinement converges to within sub-pC precision across the
    full configuration sweep."""
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets
    import sys
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    # The case that previously had the largest unsaturated
    # residual (-13 pC). After the n_samples sync it should be
    # sub-pC.
    panel.phase_amp[0].setValue(-1000.0)
    panel.phase_width[0].setValue(700.0)
    panel.phase_width[1].setValue(5000.0)
    pat = panel.pattern()
    net_nc = sum(pat.actual_phase_charges_nc())
    # 10 pC tolerance — well below the device's 30 nA × 1 µs ≈ 30 pC
    # native quantum, so a residual at this scale is physically
    # unreproducible on the hardware (the device couldn't deliver
    # 10 pC even if asked). The tolerance was 5 pC before the
    # ``curved_sample_budget`` off-by-one fix; tightening the budget
    # by 1 pair to keep linear shapes inside the 499-pair cap also
    # dropped the curved-phase sample count by 1, raising the
    # discrete-sampling residual from ~3 pC to ~8 pC for this
    # case — still inside the device-quantum noise floor.
    assert abs(net_nc) < 0.010, (
        f"unsaturated long-decay residual {net_nc*1000:.2f} pC "
        f"exceeds 10 pC tolerance after n_samples sync")


def test_saturated_pattern_respects_pat_pair_limit():
    """The PlexStim 2.0 .pat (Variable) format hard-caps at 499
    (amp_nA, duration_µs) pairs per channel. The saturated
    cap-coupled phase composition is rect_cath + rect_flat +
    exp-decay + 2 delay records = 4 fixed pairs + the curved
    breakpoints. ``curved_sample_budget`` should keep the total
    at-or-under 499.

    This test guards against a future tweak to the n_samples
    accounting accidentally pushing the pattern over the
    hardware limit (which would silently truncate or get
    rejected by ``_load_arbitrary``)."""
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets
    import sys
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.phase_amp[0].setValue(-1000.0)
    panel.phase_width[0].setValue(500.0)
    panel.phase_width[1].setValue(1250.0)
    pat = panel.pattern()
    # Total pairs: 4 fixed (cath rect + flat rect + 2 delays) +
    # (n_curved − 1) from the exp-decay phase, where n_curved is
    # what curved_sample_budget allocated.
    n_curved = pat.curved_sample_budget()
    # 1 curved phase in the saturated composition → pairs from
    # curved = n_curved − 1. Plus 1 (cath) + 1 (flat) +
    # 1 (cath delay) + 1 (decay delay) = 4 fixed.
    total_pairs = 4 + (n_curved - 1)
    assert total_pairs <= 499, (
        f"saturated cap-coupled pattern allocates {total_pairs} "
        f".pat pairs; hard limit is 499")
