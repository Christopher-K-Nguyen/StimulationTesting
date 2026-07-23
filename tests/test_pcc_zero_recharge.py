"""PCC (cap-coupled) zero-excitation → zero recharge.

Operator: "When the first phase is 0 µA, then set the second phase of PCC to
be 0 µA as well."

A capacitively-coupled pattern is a rectangular cathodic EXCITATION + an
exp-decay anodic RECHARGE sized by ``solve_capacitive_balance`` to
charge-balance the excitation.  With zero cathodic charge there is nothing to
balance, so the recharge must be 0 µA.  ``LOCK_WIDTH`` mode already derived a
0-µA peak, but ``LOCK_AMPLITUDE`` mode KEPT the user's locked peak (e.g.
230 µA) with a degenerate 0-width phase — so the recharge stayed live at 0 µA
excitation.  The fix lives in the solver (single source of truth) so every
caller — GUI ``pattern()``, preview, VT-ramp base pattern — collapses the
recharge to 0.
"""
from __future__ import annotations

import sys

import pytest

from stimtest.waveforms import (
    Phase, PulsePattern, solve_capacitive_balance,
    LOCK_WIDTH, LOCK_AMPLITUDE, SHAPE_RECTANGULAR, SHAPE_EXP_DECAY)


# ---------------------------------------------------------------------------
# solver — the single source of truth
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("lock,locked", [(LOCK_WIDTH, 200.0),
                                         (LOCK_AMPLITUDE, 230.0)])
def test_zero_cathodic_gives_zero_recharge_both_lock_modes(lock, locked):
    bal = solve_capacitive_balance(cathodic_amplitude_ua=0.0,
                                   cathodic_width_us=200.0,
                                   lock=lock, locked_value=locked)
    assert bal.anodic_amplitude_ua == 0.0
    assert bal.anodic_width_us > 0.0          # well-formed (not a 0-width phase)
    assert bal.tau_us > 0.0
    assert not bal.infeasible and not bal.saturated


def test_signed_zero_cathodal_gives_zero_recharge():
    # A VT-max zero-start ramp carries a signed-zero excitation (gotcha #56);
    # abs() collapses it, so the recharge is 0 in LOCK_AMPLITUDE too.
    bal = solve_capacitive_balance(cathodic_amplitude_ua=-0.0,
                                   cathodic_width_us=200.0,
                                   lock=LOCK_AMPLITUDE, locked_value=230.0)
    assert bal.anodic_amplitude_ua == 0.0


def test_zero_width_cathodic_also_gives_zero_recharge():
    # Q = Ic * tc, so a 0-width cathodic is also zero charge.
    bal = solve_capacitive_balance(cathodic_amplitude_ua=50.0,
                                   cathodic_width_us=0.0,
                                   lock=LOCK_AMPLITUDE, locked_value=230.0)
    assert bal.anodic_amplitude_ua == 0.0


def test_nonzero_cathodic_still_balances():
    """Regression: the Q>0 path is untouched — a real cathodic still solves a
    non-zero, charge-balanced recharge."""
    bal = solve_capacitive_balance(cathodic_amplitude_ua=-50.0,
                                   cathodic_width_us=200.0,
                                   lock=LOCK_AMPLITUDE, locked_value=230.0)
    assert bal.anodic_amplitude_ua == pytest.approx(230.0)
    assert bal.anodic_width_us > 0.0


# ---------------------------------------------------------------------------
# pattern — well-formed at 0 µA
# ---------------------------------------------------------------------------
def test_zero_pcc_pattern_is_valid_and_balanced():
    bal = solve_capacitive_balance(cathodic_amplitude_ua=-0.0,
                                   cathodic_width_us=200.0,
                                   lock=LOCK_AMPLITUDE, locked_value=230.0)
    p = PulsePattern(phases=[
        Phase(amplitude_ua=-0.0, width_us=200.0, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=+bal.anodic_amplitude_ua,
              width_us=bal.anodic_width_us, shape=SHAPE_EXP_DECAY,
              tau_us=bal.tau_us),
    ], rate_hz=50.0)
    p.validate()                              # raises if malformed
    assert p.phases[1].amplitude_ua == 0.0
    assert p.net_charge_nc == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# GUI end-to-end — the LOCK_AMPLITUDE path the operator hit
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def test_gui_pcc_lock_amplitude_zero_excitation_emits_zero_recharge(qapp):
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_CAP)
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_CAP)
    panel.asym_shape_combo.setCurrentIndex(idx)
    lock_idx = panel.cap_lock_combo.findData(LOCK_AMPLITUDE)
    panel.cap_lock_combo.setCurrentIndex(lock_idx)
    panel.phase_amp[1].setValue(230.0)        # locked recharge peak
    panel.phase_width[0].setValue(200.0)
    panel.phase_amp[0].setValue(0.0)          # excitation = 0 µA
    p = panel.pattern()
    assert len(p.phases) >= 2
    # excitation (phase 0) is 0 µA and the recharge (last phase) is too
    assert abs(p.phases[0].amplitude_ua) == 0.0
    assert abs(p.phases[-1].amplitude_ua) == 0.0
