"""Tests for the paper-derived stimulation patterns.

Three sources contribute new shapes to the pattern panel:

* **Yip et al. (2017)** — energy-efficient cochlear-nerve
  stimulation. The in-vivo tested waveform is a mirror-symmetric
  biphasic-exponential: exp-decaying cathodic phase + exp-growing
  anodic phase, both with the same τ. Reported 25–26 % charge /
  energy savings vs rectangular in human CI subjects.

* **Doğan et al. (2025)** — power-efficient ramped stimulation
  in a fully-implantable cochlear implant. Three named ramped
  waveforms tested in a guinea-pig model:
    - **RampUp** — both phases ramp UP (0 → peak in magnitude).
    - **RampDown** — both phases ramp DOWN (peak → 0).
    - **RampLong** — anodic DOWN + cathodic UP (= our existing
      ``linear_dec_inc`` under anodic-first polarity).

* **Sahin & Tie (2007)** — non-rectangular waveforms with
  practical electrodes. Compared 7 monophasic waveforms; ExpDec,
  LinDec, and Gaussian were the three most-efficient when
  accounting for both the strength–duration curve and the
  electrode's charge-injection capacity. We expose the missing
  shapes (Gaussian, ExpDec, ExpInc) in the symmetric biphasic
  dropdown.

These tests verify the new shapes are correctly applied and
charge-balanced.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ---------------------------------------------------------------- base shapes


def test_shape_gaussian_breakpoints_peak_at_centre():
    """SHAPE_GAUSSIAN's breakpoints peak at the middle of the
    phase. With σ = W/5, the peak amplitude is the user's
    ``amplitude_ua`` (the Gaussian is normalised to peak = A,
    not unit-area)."""
    from stimtest.waveforms import shape_breakpoints, SHAPE_GAUSSIAN
    bps = shape_breakpoints(amplitude_ua=100.0, width_us=200.0,
                            shape=SHAPE_GAUSSIAN, n_samples=21)
    # Find the breakpoint closest to t = W/2.
    times = [t for t, _ in bps]
    amps = [a for _, a in bps]
    mid_idx = min(range(len(times)), key=lambda i: abs(times[i] - 100.0))
    # Peak amplitude at centre is the user's amplitude (within
    # discretisation rounding).
    assert amps[mid_idx] == pytest.approx(100.0, rel=0.02)
    # Endpoints are at low amplitude (Gaussian decays off the
    # ±2.5σ tails).
    assert abs(amps[0]) < 5.0    # ≈ 100 · exp(−3.125) = 4.4 µA
    assert abs(amps[-1]) < 5.0


def test_shape_exp_increasing_is_mirror_of_exp_decay():
    """SHAPE_EXP_INCREASING(A, W, τ) breakpoints at t are equal
    to SHAPE_EXP_DECAY(A, W, τ) breakpoints at (W - t) — the
    time-reversed shape used as Yip 2017's anodic phase."""
    from stimtest.waveforms import (
        shape_breakpoints, SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING,
    )
    A, W, tau = 100.0, 200.0, 40.0
    bps_dec = shape_breakpoints(amplitude_ua=A, width_us=W,
                                shape=SHAPE_EXP_DECAY, tau_us=tau,
                                n_samples=21)
    bps_inc = shape_breakpoints(amplitude_ua=A, width_us=W,
                                shape=SHAPE_EXP_INCREASING, tau_us=tau,
                                n_samples=21)
    # Same number of samples.
    assert len(bps_dec) == len(bps_inc)
    # ExpInc starts at A·exp(-W/τ) (small) and ends at A.
    assert bps_inc[0][1] < 1.0   # 100·exp(−5) ≈ 0.67
    assert bps_inc[-1][1] == pytest.approx(A, rel=1e-3)
    # ExpDec is the reverse: starts at A, ends at A·exp(-W/τ).
    assert bps_dec[0][1] == pytest.approx(A, rel=1e-3)
    assert bps_dec[-1][1] < 1.0
    # Mirror symmetry: amp[k] of inc == amp[n-1-k] of dec.
    for k in range(len(bps_dec)):
        assert bps_inc[k][1] == pytest.approx(
            bps_dec[len(bps_dec) - 1 - k][1], rel=1e-6)


def test_gaussian_charge_factor_is_about_half(qapp):
    """A Gaussian's discrete charge integral is approximately
    half of a same-amp/width rectangle (duty ~0.495). The
    duty factor is exact at σ = W/5 with the boundary at ±2.5σ
    catching ~99% of the Gaussian mass."""
    from stimtest.waveforms import (
        shape_breakpoints, actual_charge_nc, Phase,
        SHAPE_GAUSSIAN, SHAPE_RECTANGULAR,
    )
    rect = Phase(amplitude_ua=100.0, width_us=200.0,
                 shape=SHAPE_RECTANGULAR)
    gauss = Phase(amplitude_ua=100.0, width_us=200.0,
                  shape=SHAPE_GAUSSIAN)
    Q_rect = actual_charge_nc(rect, n_samples=200)
    Q_gauss = actual_charge_nc(gauss, n_samples=200)
    ratio = Q_gauss / Q_rect
    # Expected ratio ≈ 0.4953 (the analytical Gaussian duty).
    assert 0.45 < ratio < 0.55


# ---------------------------------------------------------------- Yip 2017


def test_yip_exp_biphasic_phase_shapes(qapp):
    """Yip et al. (2017)'s mirror-symmetric biphasic-exponential
    is now reached via SYMMETRIC mode + the "Exponential
    decreasing → increasing" entry (phase 0 SHAPE_EXP_DECAY,
    phase 1 SHAPE_EXP_INCREASING). Was briefly an asymmetric
    entry (``exp_biphasic``) but moved to symmetric since both
    phases share |amp| and width."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData("exp_dec_inc")
    assert idx >= 0
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(100.0)
    panel.width_shared.setValue(54.0)
    pat = panel.pattern()
    assert pat.num_phases == 2
    assert pat.phases[0].shape == SHAPE_EXP_DECAY
    assert pat.phases[1].shape == SHAPE_EXP_INCREASING


def test_yip_exp_biphasic_charge_balance(qapp):
    """Yip's mirror-symmetric biphasic-exponential balances by
    construction — both phases share the same shape factor
    ((1−e^(−N))/N at canonical τ), so equal-magnitude A·W
    products give equal-magnitude integrated charges. The
    discrete sampling introduces a small asymmetry between
    exp-decay and exp-increasing left-Riemann sums (~120 pC at
    typical configs); allow that as a tolerance. Reached via
    SYMMETRIC + ``exp_dec_inc`` (phase 0 exp-decay, phase 1
    exp-increasing) since the moved asym entry was pruned."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData("exp_dec_inc")
    assert idx >= 0
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(200.0)
    panel.width_shared.setValue(150.0)
    pat = panel.pattern()
    charges = pat.actual_phase_charges_nc()
    # |Q_cath| ≈ |Q_anod| within left-Riemann discrete-sampling
    # asymmetry between exp-decay (start-at-peak) and exp-
    # increasing (end-at-peak) — ~0.2 nC at this scale.
    assert abs(abs(charges[0]) - abs(charges[1])) < 0.5


# ---------------------------------------------------------------- Doğan 2025


def test_dogan_rampup_both_phases_ramp_up(qapp):
    """Doğan et al. (2025) RampUp: both phases ramp UP from 0
    to peak (in magnitude). Now reached via SYMMETRIC mode +
    ``Linear increasing`` — the same shape applies to both
    phases, and signed amplitudes make phase 0 ramp 0 → −peak
    (cathodic) and phase 1 ramp 0 → +peak (anodic). Was briefly
    an explicit asymmetric ``linear_inc_inc`` entry but the
    symmetric path already covers the same shape pair."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import SHAPE_LINEAR_INCREASING
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData(SHAPE_LINEAR_INCREASING)
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(200.0)
    panel.width_shared.setValue(50.0)
    pat = panel.pattern()
    assert pat.num_phases == 2
    assert pat.phases[0].shape == SHAPE_LINEAR_INCREASING
    assert pat.phases[1].shape == SHAPE_LINEAR_INCREASING


def test_dogan_rampdown_both_phases_ramp_down(qapp):
    """Doğan RampDown: both phases ramp DOWN from peak to 0.
    Reached via SYMMETRIC + ``Linear decreasing`` for the same
    reason as RampUp above."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import SHAPE_LINEAR_DECREASING
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData(SHAPE_LINEAR_DECREASING)
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(200.0)
    panel.width_shared.setValue(50.0)
    pat = panel.pattern()
    assert pat.num_phases == 2
    assert pat.phases[0].shape == SHAPE_LINEAR_DECREASING
    assert pat.phases[1].shape == SHAPE_LINEAR_DECREASING


def test_dogan_ramplong_is_existing_lin_dec_inc_under_anodic_first(qapp):
    """Doğan's RampLong (anodic DOWN, cathodic UP) is exactly
    the ``linear_dec_inc`` symmetric-biphasic shape pair under
    Anodic-first polarity — phase 0 anodic decreases (peak → 0),
    phase 1 cathodic increases (0 → −peak). Verify the
    equivalence so users picking ``Linear dec → inc`` from the
    symmetric dropdown get the right config without us adding a
    duplicate option."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import (
        SHAPE_LINEAR_DECREASING, SHAPE_LINEAR_INCREASING,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Anodic-first")
    idx = panel.shape_combo.findData("linear_dec_inc")
    assert idx >= 0
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(200.0)
    panel.width_shared.setValue(50.0)
    pat = panel.pattern()
    # Phase 0: positive amp + LIN_DEC → ramps +200 → 0 (anodic-DOWN).
    assert pat.phases[0].shape == SHAPE_LINEAR_DECREASING
    assert pat.phases[0].amplitude_ua > 0
    # Phase 1: negative amp + LIN_INC → ramps 0 → −200 (cathodic-UP).
    assert pat.phases[1].shape == SHAPE_LINEAR_INCREASING
    assert pat.phases[1].amplitude_ua < 0


def test_rampup_rampdown_charge_balance(qapp):
    """RampUp and RampDown both have phase 0 and phase 1 with
    matching shape factors (0.5 each), so charge balance via
    raw A·W products gives the right answer. Net charge stays
    sub-pC at quantisation noise floor. Reached via SYMMETRIC +
    ``Linear increasing`` / ``Linear decreasing`` since the
    explicit asymmetric entries were pruned (the symmetric path
    already covers the same shape pair)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import (
        SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    for sid in (SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING):
        idx = panel.shape_combo.findData(sid)
        assert idx >= 0
        panel.shape_combo.setCurrentIndex(idx)
        panel.amp_excite.setValue(200.0)
        panel.width_shared.setValue(50.0)
        pat = panel.pattern()
        net_nc = sum(pat.actual_phase_charges_nc())
        assert abs(net_nc) < 0.5, (
            f"shape={sid}: net residual {net_nc*1000:.2f} pC > 500 pC")


# ---------------------------------------------------------------- Sahin 2007


def test_sahin_gaussian_added_to_symmetric_biphasic_dropdown(qapp):
    """Sahin & Tie (2007) identified Gaussian as one of three
    most-efficient monophasic waveforms (alongside ExpDec and
    LinDec). The Gaussian was missing from the symmetric
    biphasic shape combo; verify it's now selectable."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    from stimtest.waveforms import SHAPE_GAUSSIAN
    panel = PatternControlPanel()
    idx = panel.shape_combo.findData(SHAPE_GAUSSIAN)
    assert idx >= 0, "SHAPE_GAUSSIAN not in symmetric biphasic dropdown"


def test_sahin_exp_shapes_added_to_symmetric_biphasic_dropdown(qapp):
    """Both ExpDec and ExpInc are now selectable in symmetric
    mode (was previously cap-coupled-only for ExpDec)."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    from stimtest.waveforms import SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING
    panel = PatternControlPanel()
    assert panel.shape_combo.findData(SHAPE_EXP_DECAY) >= 0
    assert panel.shape_combo.findData(SHAPE_EXP_INCREASING) >= 0


def test_symmetric_biphasic_with_gaussian_shape(qapp):
    """A symmetric biphasic with SHAPE_GAUSSIAN selected
    produces phases that carry the Gaussian shape on both
    sides (mirrored across zero), with charge balanced by
    construction."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import SHAPE_GAUSSIAN
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData(SHAPE_GAUSSIAN)
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(100.0)
    panel.width_shared.setValue(200.0)
    pat = panel.pattern()
    assert pat.phases[0].shape == SHAPE_GAUSSIAN
    assert pat.phases[1].shape == SHAPE_GAUSSIAN
    # Equal magnitude, opposite signs.
    assert pat.phases[0].amplitude_ua == -pat.phases[1].amplitude_ua
    # Charge-balanced: both Gaussians integrate to the same
    # amount, opposite signs.
    charges = pat.actual_phase_charges_nc()
    assert abs(sum(charges)) < 0.1   # < 100 pC


# ---------------------------------------------------------------- locked-range readout


def _disabled_test_locked_range_readout_for_new_asym_shapes(qapp):
    """Disabled — the old linear-asym / exp-biphasic asymmetric
    entries were pruned out of the asymmetric dropdown (those
    shape pairs now live in SYMMETRIC where they belong). The
    locked-range readout concept doesn't apply to symmetric
    mode (auto-balance isn't a thing there); leaving this stub
    so the test name remains searchable in git history.
    """
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
        CHARGE_BAL_AMP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    panel.charge_mode.setCurrentText(CHARGE_BAL_AMP)
    panel.phase_amp[0].setValue(-200.0)
    panel.phase_width[0].setValue(100.0)
    # No assertion — placeholder retained so the test name is
    # discoverable in git history; see method docstring above.
    del panel
