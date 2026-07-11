"""Tests for the rectangular-asymmetric auto-balance clamp.

When the user has ``CHARGE_BAL_AMP`` selected and configures a
cathodic phase whose mathematically-balanced anodic amplitude
would exceed ``STIM_MAX_AMPLITUDE_UA`` (1000 µA), the panel must:

* Clamp the actual delivered amplitude at 1000 µA so the device
  never gets asked to source current it cannot deliver.
* Surface a red warning that names the unclamped value and
  explains that charge balance is no longer achieved.

The clamp lives in :func:`PatternControlPanel.pattern` (after
``auto_balance``); the warning lives in the same method via
``_asym_balance_warn_lbl``.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def test_rect_asym_auto_amp_clamps_at_1000ua(qapp):
    """A cathodic phase that would need 5000 µA to balance gets
    clamped at the 1000 µA hardware ceiling, and the warning
    label is shown with the unclamped value mentioned."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
        CHARGE_BAL_AMP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_AMP)
    # Cathodic 1000 µA × 1000 µs = 1000 nC. Auto-adjust amp at
    # 200 µs anodic width would need 5000 µA. Above the ceiling.
    panel.phase_amp[0].setValue(-1000.0)
    panel.phase_width[0].setValue(1000.0)
    panel.phase_width[1].setValue(200.0)
    pat = panel.pattern()
    # Last phase amplitude clamped at the ceiling.
    last = pat.phases[-1]
    assert abs(last.amplitude_ua) == pytest.approx(1000.0, rel=1e-3)
    # Warning label populated with the right content. We check
    # ``text()`` rather than ``isVisible()`` because the panel
    # isn't shown in test (Qt's isVisible() requires a shown
    # parent tree); the panel calls ``setVisible(bool(text))``,
    # so a non-empty text is the canonical "warning is on".
    warn = panel._asym_balance_warn_lbl
    txt = warn.text()
    assert txt, "expected warning text to be set"
    assert "⚠" in txt
    assert "clamped" in txt.lower()
    # Mentions the unclamped value (5000 µA is what auto-balance
    # wanted; rounding through the spinbox display can produce
    # 4999.9 — accept either).
    assert any(s in txt for s in ("5000", "5 000", "4999"))


def test_rect_asym_auto_amp_no_warning_when_balance_fits(qapp):
    """A modest cathodic phase that auto-balances within the
    hardware limit produces no warning and no clamping."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
        CHARGE_BAL_AMP,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_AMP)
    # 100 µA × 200 µs cathodic, 200 µs anodic → balanced at 100 µA
    # (well below 1000 µA cap).
    panel.phase_amp[0].setValue(-100.0)
    panel.phase_width[0].setValue(200.0)
    panel.phase_width[1].setValue(200.0)
    pat = panel.pattern()
    last = pat.phases[-1]
    # Amplitude well below the ceiling, balance achieved.
    assert abs(last.amplitude_ua) == pytest.approx(100.0, rel=1e-3)
    # No warning — empty text means "not warning". (isVisible()
    # would also work in a shown widget tree, but tests don't
    # show the panel; checking text() is the reliable signal.)
    warn = panel._asym_balance_warn_lbl
    assert warn.text() == ""


def test_rect_asym_auto_width_warns_on_unusually_long_phase(qapp):
    """The width-side warning still fires when auto-balance would
    need a > 10 ms phase."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, ASYM_SHAPE_RECT,
        CHARGE_BAL_WID,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_RECT)
    panel.asym_shape_combo.setCurrentIndex(idx)
    panel.charge_mode.setCurrentText(CHARGE_BAL_WID)
    # 1000 µA × 1000 µs cathodic, 1 µA anodic amp → balance width
    # = 1 000 000 µs (1 s). Way past 10 ms threshold.
    panel.phase_amp[0].setValue(-1000.0)
    panel.phase_width[0].setValue(1000.0)
    panel.phase_amp[1].setValue(1.0)
    panel.pattern()
    warn = panel._asym_balance_warn_lbl
    txt = warn.text()
    # Same isVisible-without-show() limitation as the rect-asym
    # clamp test above; check the text directly.
    assert txt, "expected width warning text to be set"
    assert "long" in txt.lower()
