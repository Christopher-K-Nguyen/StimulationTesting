"""Tests for the symmetric-biphasic linear shape pairs.

Two biphasic shape pairs carry per-phase ramp profiles whose two
phases use DIFFERENT shapes despite sharing |amp| and width:

  * ``"linear_inc_dec"`` — phase 0 ramps from 0 → I_peak (linear
    increasing), phase 1 ramps from I_peak → 0 (linear decreasing).
    Cathodic-first → cathodic ramps in magnitude from 0 to I_cath;
    anodic phase ramps from I_anod down to 0.
  * ``"linear_dec_inc"`` — time-reversed: phase 0 starts at I_peak
    and ramps down, phase 1 starts at 0 and ramps up.

Both shapes live in the SYMMETRIC dropdown (one amp + one width
spinbox); the panel's symmetric-path code recognises them via
``SYM_BIPHASIC_SHAPE_PAIRS`` and builds phases with mirrored
shapes per phase. They were briefly classified as asymmetric in
an earlier iteration but moved to symmetric since the user only
adjusts a single amp + width.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def test_linear_inc_dec_assigns_increasing_then_decreasing(qapp):
    """``"linear_inc_dec"`` (symmetric mode) puts
    SHAPE_LINEAR_INCREASING on phase 0 and SHAPE_LINEAR_DECREASING
    on phase 1."""
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
    idx = panel.shape_combo.findData("linear_inc_dec")
    assert idx >= 0, "linear_inc_dec should be in the symmetric dropdown"
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(200.0)
    panel.width_shared.setValue(150.0)
    pat = panel.pattern()
    assert pat.num_phases == 2
    assert pat.phases[0].shape == SHAPE_LINEAR_INCREASING
    assert pat.phases[1].shape == SHAPE_LINEAR_DECREASING


def test_linear_dec_inc_assigns_decreasing_then_increasing(qapp):
    """``"linear_dec_inc"`` puts SHAPE_LINEAR_DECREASING on phase 0
    and SHAPE_LINEAR_INCREASING on phase 1 — mirror of inc-dec."""
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
    idx = panel.shape_combo.findData("linear_dec_inc")
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(200.0)
    panel.width_shared.setValue(150.0)
    pat = panel.pattern()
    assert pat.num_phases == 2
    assert pat.phases[0].shape == SHAPE_LINEAR_DECREASING
    assert pat.phases[1].shape == SHAPE_LINEAR_INCREASING


def test_linear_shape_charge_balance_holds(qapp):
    """A symmetric biphasic linear pair (same |peak|, same width)
    is charge-balanced by construction — both ramps share the
    same 0.5 shape factor → equal A·W products → equal
    integrated charges. Validate that the discrete delivered
    charges balance to within the device's quantisation floor."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData("linear_inc_dec")
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(200.0)
    panel.width_shared.setValue(150.0)
    pat = panel.pattern()
    charges = pat.actual_phase_charges_nc()
    # Equal-magnitude opposite-sign per phase (each is A·W/2 in
    # absolute terms because the ramp integrates to half the
    # rectangle area). Tolerance covers the discrete-sampling
    # asymmetry between LIN_INC and LIN_DEC left-Riemann sums.
    assert abs(abs(charges[0]) - abs(charges[1])) < 0.5
    assert abs(sum(charges)) < 0.5


def test_linear_inc_dec_phase_breakpoints_actually_ramp(qapp):
    """Sanity check the breakpoint generation: for linear
    inc-dec at -100 µA peak, phase 0's breakpoints should span
    (0, 0) → (W, -100), confirming the cathodic side actually
    ramps in magnitude from zero to peak (rather than being held
    flat as a rectangular pulse)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import shape_breakpoints
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData("linear_inc_dec")
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(100.0)
    panel.width_shared.setValue(200.0)
    pat = panel.pattern()
    p0 = pat.phases[0]
    bps = shape_breakpoints(
        amplitude_ua=p0.amplitude_ua, width_us=p0.width_us,
        shape=p0.shape, n_samples=10)
    # First breakpoint is at (0, 0) — ramp starts from 0.
    assert bps[0][0] == 0.0
    assert abs(bps[0][1]) < 1e-6
    # Last breakpoint is at (W, -100) — ramp ends at the peak.
    assert bps[-1][0] == pytest.approx(200.0)
    assert bps[-1][1] == pytest.approx(-100.0, rel=1e-3)


def test_linear_dec_inc_phase_breakpoints_actually_ramp(qapp):
    """Mirror sanity check: linear dec-inc's phase 0 ramps from
    peak DOWN to zero, phase 1 from zero UP to peak."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    from stimtest.waveforms import shape_breakpoints
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodic-first")
    idx = panel.shape_combo.findData("linear_dec_inc")
    panel.shape_combo.setCurrentIndex(idx)
    panel.amp_excite.setValue(100.0)
    panel.width_shared.setValue(200.0)
    pat = panel.pattern()
    # Phase 0: starts at peak, decays to 0.
    p0 = pat.phases[0]
    bps0 = shape_breakpoints(
        amplitude_ua=p0.amplitude_ua, width_us=p0.width_us,
        shape=p0.shape, n_samples=10)
    assert bps0[0][1] == pytest.approx(-100.0, rel=1e-3)
    assert abs(bps0[-1][1]) < 1e-6
    # Phase 1: starts at 0, ramps up to peak.
    p1 = pat.phases[1]
    bps1 = shape_breakpoints(
        amplitude_ua=p1.amplitude_ua, width_us=p1.width_us,
        shape=p1.shape, n_samples=10)
    assert abs(bps1[0][1]) < 1e-6
    assert bps1[-1][1] == pytest.approx(100.0, rel=1e-3)
