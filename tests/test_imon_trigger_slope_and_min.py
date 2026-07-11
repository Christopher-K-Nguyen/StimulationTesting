"""I_mon-trigger EDGE SLOPE (polarity-aware) + minimum-amplitude guard.

Operator, after a cathodal-first VT-max starting at 0 µA came back with the
pulse NOT aligned at t=0 (the I_mon leading edge landed ~one phase-width off):

  * "Remember to have falling edge with cathodal-first and rising edge with
    anodal-first" — and "This rising and falling edge is only when Imon is the
    trigger source."
  * "If Imon is the trigger source, require that [the amplitude] must be
    >= |0.1| uA" (for ANY shape — "not just rectangular").

Root cause of the misalignment (gotcha #56 / this file): a 0 µA-START ramp's
phase-1 amplitude is a SIGNED ZERO (``-0.0`` cathodal-first).  The old slope
test ``_amp_signed < 0`` (with an ``or 10.0`` fallback that collapses ``-0.0``
to ``+10.0``) read that as non-negative → RISE, so a cathodal-first pulse
triggered on the RISING I_mon edge (the anodic recovery), landing the leading
edge ~200 µs off t=0.  ``math.copysign`` recovers the polarity from the signed
zero → FALL.
"""
from __future__ import annotations

import math
import sys

import pytest


# --------------------------------------------------------------------------
# A. Pure helpers (no GUI) — imon_trigger_slope + imon_trigger_level sign
# --------------------------------------------------------------------------
def test_slope_follows_polarity_for_real_amplitudes():
    from stimtest.experiments.base import imon_trigger_slope
    assert imon_trigger_slope(-50.0) == "FALL"   # cathodal-first
    assert imon_trigger_slope(+50.0) == "RISE"   # anodal-first
    assert imon_trigger_slope(-0.1) == "FALL"
    assert imon_trigger_slope(+0.1) == "RISE"


def test_slope_honours_signed_zero_of_a_0ua_start_ramp():
    # The exact regression: a 0 µA-start ramp carries a signed-zero phase-1
    # amplitude (gotcha #56).  ``-0.0`` is cathodal-first → FALL; ``+0.0`` is
    # anodal-first → RISE.  ``-0.0 < 0`` is False, so a plain sign test (the
    # old code) wrongly returns RISE for the cathodal case.
    from stimtest.experiments.base import imon_trigger_slope
    assert imon_trigger_slope(-0.0) == "FALL"
    assert imon_trigger_slope(0.0) == "RISE"    # +0.0
    # Sanity: a plain ``< 0`` test would get the cathodal signed-zero wrong.
    assert (-0.0 < 0) is False


def test_trigger_level_sign_is_signed_zero_consistent():
    from stimtest.experiments.base import imon_trigger_level
    # Level sign must match the slope's polarity so FALL pairs with a negative
    # level and RISE with a positive one — including at the ±0.0 baseline.
    assert imon_trigger_level(-10.0, 200.0) < 0
    assert imon_trigger_level(+10.0, 200.0) > 0
    assert imon_trigger_level(-0.0, 200.0) < 0     # cathodal signed zero
    assert imon_trigger_level(0.0, 200.0) > 0      # anodal +0.0
    # Magnitudes for every NON-zero amplitude are unchanged by the copysign
    # switch (identical to the old ``-1 if x<0 else 1``).
    assert imon_trigger_level(-10.0, 200.0) == pytest.approx(
        -imon_trigger_level(+10.0, 200.0))


def test_min_amplitude_constant_is_a_tenth_microamp():
    from stimtest.experiments.base import IMON_TRIGGER_MIN_AMPLITUDE_UA
    assert IMON_TRIGGER_MIN_AMPLITUDE_UA == pytest.approx(0.1)


def test_continuous_slope_also_honours_signed_zero():
    # The continuous (KHFAC / no-interpulse) trigger uses the SAME
    # copysign-based slope, so a 0 µA-start continuous ramp is correct too:
    # ``+0.0`` anodal-first → RISE, ``-0.0`` cathodal-first → FALL.  A plain
    # ``amp > 0`` test would flip the anodal ``+0.0`` case to FALL.
    from stimtest.experiments.base import continuous_trigger_level_slope
    from stimtest.waveforms import SHAPE_SINUSOIDAL
    _lvl, slope_cath = continuous_trigger_level_slope(-0.0, SHAPE_SINUSOIDAL)
    _lvl, slope_anod = continuous_trigger_level_slope(0.0, SHAPE_SINUSOIDAL)
    assert slope_cath == "FALL"
    assert slope_anod == "RISE"
    # Non-zero amplitudes unchanged (regression against the existing suite).
    assert continuous_trigger_level_slope(-1.0, SHAPE_SINUSOIDAL)[1] == "FALL"
    assert continuous_trigger_level_slope(50.0, SHAPE_SINUSOIDAL)[1] == "RISE"


# --------------------------------------------------------------------------
# B / C. GUI integration — _resolve_trigger_settings slope + amplitude guard
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _vt_imon(qapp, *, polarity="Cathodal-first", amp_ua=0.0):
    """A VT tab wired for the I_mon-edge trigger (no digital sync)."""
    from stimtest.gui.experiment_tabs import VoltageTransientTab
    from stimtest.electrode import ElectrodeArray
    t = VoltageTransientTab(ElectrodeArray.utah_4x4())
    t.pattern_panel.polarity.setCurrentText(polarity)
    t.pattern_panel.amp_excite.setValue(abs(amp_ua))
    t._trigger_is_digital = False
    t._trigger_source = "CH2"
    t._aliases = {"vmon": "CH1", "imon": "CH2", "trigger": "CH2"}
    return t


def test_resolve_slope_fall_for_cathodal_zero_start(qapp):
    # The reported bug: cathodal-first 0 µA start must resolve FALL (was RISE),
    # and the level sign must match (negative).
    t = _vt_imon(qapp, polarity="Cathodal-first", amp_ua=0.0)
    src, slope, level, digital, _note = t._resolve_trigger_settings()
    assert digital is False
    assert slope == "FALL"
    assert level < 0


def test_resolve_slope_rise_for_anodal_zero_start(qapp):
    t = _vt_imon(qapp, polarity="Anodal-first", amp_ua=0.0)
    _src, slope, level, digital, _note = t._resolve_trigger_settings()
    assert slope == "RISE"
    assert level > 0


def test_resolve_slope_fall_for_cathodal_real_amplitude(qapp):
    t = _vt_imon(qapp, polarity="Cathodal-first", amp_ua=5.0)
    assert float(t.pattern_panel.pattern().phases[0].amplitude_ua) == -5.0
    _src, slope, _level, _digital, _note = t._resolve_trigger_settings()
    assert slope == "FALL"


def test_digital_trigger_slope_is_unaffected(qapp):
    # Operator: "This rising and falling edge is only when Imon is the trigger
    # source."  A digital sync line fires active-high → RISE @ TTL regardless
    # of stim polarity.
    t = _vt_imon(qapp, polarity="Cathodal-first", amp_ua=0.0)
    t._trigger_is_digital = True
    t._trigger_source = "CH4"
    _src, slope, _level, digital, _note = t._resolve_trigger_settings()
    assert digital is True
    assert slope == "RISE"


def test_guard_blocks_imon_trigger_below_min(qapp):
    t = _vt_imon(qapp, polarity="Cathodal-first", amp_ua=0.0)
    msg = t._imon_trigger_amplitude_error()
    assert msg is not None
    assert "0.1" in msg and "I_mon" in msg


def test_guard_allows_at_and_above_min(qapp):
    # Exactly the minimum passes; well above passes.
    assert _vt_imon(qapp, amp_ua=0.1)._imon_trigger_amplitude_error() is None
    assert _vt_imon(qapp, amp_ua=5.0)._imon_trigger_amplitude_error() is None


def test_guard_is_magnitude_based_not_signed(qapp):
    # Operator clarification: "just as long as the MAGNITUDE is at least 0.1 uA"
    # — the guard is sign-agnostic.  A cathodal (NEGATIVE) amplitude whose
    # magnitude is >= 0.1 µA must PASS (a plain ``amp >= 0.1`` signed test would
    # wrongly block every cathodal pulse); only a sub-0.1 µA magnitude blocks.
    t = _vt_imon(qapp, polarity="Cathodal-first", amp_ua=5.0)
    assert float(t.pattern_panel.pattern().phases[0].amplitude_ua) == -5.0
    assert t._imon_trigger_amplitude_error() is None          # |−5| ≥ 0.1 → OK
    t2 = _vt_imon(qapp, polarity="Cathodal-first", amp_ua=0.1)
    assert float(t2.pattern_panel.pattern().phases[0].amplitude_ua) == -0.1
    assert t2._imon_trigger_amplitude_error() is None         # |−0.1| == min
    # Magnitude 0 (a signed-zero cathodal 0 µA start) blocks.
    t3 = _vt_imon(qapp, polarity="Cathodal-first", amp_ua=0.0)
    assert t3._imon_trigger_amplitude_error() is not None


def test_guard_ignored_for_digital_trigger(qapp):
    # A digital Trigger channel fires on the sync line regardless of current,
    # so 0 µA is fine there — the guard must NOT block it.
    t = _vt_imon(qapp, polarity="Cathodal-first", amp_ua=0.0)
    t._trigger_is_digital = True
    t._trigger_source = "CH4"
    assert t._imon_trigger_amplitude_error() is None


# --------------------------------------------------------------------------
# D. INPUT-LEVEL enforcement — the excitation-current field can't be 0 µA
#    when I_mon is the trigger source (operator: "current cannot be 0").
# --------------------------------------------------------------------------
def _vt(qapp, polarity="Cathodal-first"):
    from stimtest.gui.experiment_tabs import VoltageTransientTab
    from stimtest.electrode import ElectrodeArray
    t = VoltageTransientTab(ElectrodeArray.utah_4x4())
    t.pattern_panel.polarity.setCurrentText(polarity)
    return t


def _phase0(t):
    return float(t.pattern_panel.pattern().phases[0].amplitude_ua)


def test_imon_trigger_clamps_zero_to_min_magnitude(qapp):
    t = _vt(qapp, "Cathodal-first")
    t.set_digital_trigger(False)                 # I_mon is the trigger source
    t.pattern_panel.amp_excite.setValue(0.0)
    t.pattern_panel._on_amp_excite_value_changed()
    # 0 µA clamped to -0.1 µA (cathodal), magnitude == the 0.1 µA minimum.
    assert abs(_phase0(t)) == pytest.approx(0.1)
    assert _phase0(t) < 0                          # sign follows polarity
    # The inline warning is shown (isHidden reflects the explicit hide state;
    # isVisible would be False on an unshown widget — gotcha #103).
    assert not t.pattern_panel._amp_trigger_warn.isHidden()


def test_imon_trigger_clamp_follows_polarity(qapp):
    t = _vt(qapp, "Anodal-first")
    t.set_digital_trigger(False)
    t.pattern_panel.amp_excite.setValue(0.0)
    t.pattern_panel._on_amp_excite_value_changed()
    assert _phase0(t) == pytest.approx(0.1)        # anodal → +0.1


def test_imon_trigger_keeps_real_amplitude_and_hides_warning(qapp):
    t = _vt(qapp, "Cathodal-first")
    t.set_digital_trigger(False)
    t.pattern_panel.amp_excite.setValue(5.0)
    t.pattern_panel._on_amp_excite_value_changed()
    assert _phase0(t) == pytest.approx(-5.0)       # unchanged
    assert t.pattern_panel._amp_trigger_warn.isHidden()


def test_digital_trigger_allows_zero_current(qapp):
    t = _vt(qapp, "Cathodal-first")
    t.set_digital_trigger(True)                    # digital sync — 0 µA OK
    t.pattern_panel.amp_excite.setValue(0.0)
    t.pattern_panel._on_amp_excite_value_changed()
    assert abs(_phase0(t)) < 1e-9                  # stays 0 µA
    assert t.pattern_panel._amp_trigger_warn.isHidden()


def test_switching_to_imon_clamps_an_existing_zero(qapp):
    # Selecting the I_mon trigger while the field already holds 0 µA must
    # immediately clamp it (not wait for the next edit).
    t = _vt(qapp, "Cathodal-first")
    t.set_digital_trigger(True)
    t.pattern_panel.amp_excite.setValue(0.0)
    t.pattern_panel._on_amp_excite_value_changed()
    assert abs(_phase0(t)) < 1e-9
    t.set_digital_trigger(False)                   # → immediate clamp
    assert abs(_phase0(t)) == pytest.approx(0.1)
    # …and switching back lifts the constraint (0 µA allowed again).
    t.set_digital_trigger(True)
    t.pattern_panel.amp_excite.setValue(0.0)
    t.pattern_panel._on_amp_excite_value_changed()
    assert abs(_phase0(t)) < 1e-9
    assert t.pattern_panel._amp_trigger_warn.isHidden()
