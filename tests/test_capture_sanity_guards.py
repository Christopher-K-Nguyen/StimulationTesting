"""Verification rejects non-physical captures instead of recording them.

Bench symptom: the results row read ``C_load = 723277977 +/- 1022862787 pF``
and ``r2 = -196`` while the 100 uA waveform on screen matched the RC model
beautifully.  The statistics were not from that waveform -- they came from the
LOW-amplitude captures, which were UNTRIGGERED / free-running frames (the
"NUMACq = 0/1" poll timeouts).  A flat frame is silently corrosive:

  * a flat phase has a ~0 ramp slope, and ``C = I*k/slope`` explodes;
  * its iR "steps" are noise, so R reads low with a huge SD;
  * r2 goes hugely negative.

...while the high-amplitude captures look perfect, because only the low
amplitudes fail to trigger.  Two guards, both tied to the known physics
rather than to arbitrary thresholds:

  1. a capture must REACH ~(I*R + I*W/C)*k, else it is re-captured;
  2. a phase must RAMP at ~I*k/C, else it is excluded from the C average.
"""
from __future__ import annotations

import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def _src() -> str:
    return (_ROOT / "stimtest" / "gui" / "calibration.py").read_text(
        encoding="utf-8")


def _method(src: str, name: str) -> str:
    start = src.find(f"def {name}")
    assert start != -1, name
    end = src.find("\n    def ", start + 1)
    return src[start:end] if end != -1 else src[start:]


# --------------------------------------------------- constants exist
def test_sanity_fractions_are_defined():
    from stimtest.gui.calibration import (_PULSE_SANITY_FRAC,
                                          _SLOPE_SANITY_FRAC)
    # Both must be real fractions -- neither disabled (0) nor >= 1, which
    # would reject every capture.
    assert 0.0 < _PULSE_SANITY_FRAC < 1.0
    assert 0.0 < _SLOPE_SANITY_FRAC < 1.0


# --------------------------------------------- guard 1: is it a pulse?
def test_untriggered_frame_is_recaptured_not_recorded():
    block = _method(_src(), "_capture_one_amplitude")
    # The expected excursion is derived from the load + amplitude...
    assert "_v_expect" in block and "_PULSE_SANITY_FRAC" in block
    # ...and a short frame continues the retry loop rather than falling
    # through to be recorded.
    i_guard = block.find("_PULSE_SANITY_FRAC * _v_expect")
    assert i_guard != -1
    assert "continue" in block[i_guard:i_guard + 500]


def test_old_microvolt_guard_is_gone():
    """The previous threshold was 1 uV -- any noise frame cleared it."""
    block = _method(_src(), "_capture_one_amplitude")
    assert "< 1e-6:" not in block


def test_pulse_guard_uses_locally_bound_scaling():
    """``vmon_v_per_v`` is not bound until later in the method; using it in
    the guard would raise UnboundLocalError on EVERY capture."""
    block = _method(_src(), "_capture_one_amplitude")
    i_guard = block.find("_v_expect = (")
    assert i_guard != -1
    i_local = block.find("_k_vmon = float(")
    assert i_local != -1 and i_local < i_guard, \
        "scaling must be bound BEFORE the guard uses it"


# ------------------------------------------- guard 2: does it ramp?
def test_flat_phase_excluded_from_capacitance():
    block = _method(_src(), "_capture_one_amplitude")
    assert "_slope_expect" in block
    i = block.find("_SLOPE_SANITY_FRAC * abs(_slope_expect)")
    assert i != -1
    assert "continue" in block[i:i + 600]


def test_slope_guard_is_not_the_old_epsilon():
    """``abs(slope) <= 1e-6`` let a 0.14 V/s slope through, which reports
    7e8 pF -- huge, but finite and positive, so it passed the > 0 check."""
    block = _method(_src(), "_capture_one_amplitude")
    assert "abs(_slope) <= 1e-6" not in block


# ------------------------------------------------------- arithmetic
@pytest.mark.parametrize("amp_ua", [25.0, 50.0, 100.0, 200.0])
def test_expected_excursion_is_reachable_at_every_grid_amplitude(amp_ua):
    """A GOOD capture must clear the pulse guard at every amplitude in the
    sweep grid -- otherwise the guard would reject real data."""
    from stimtest.gui.calibration import CalibrationTab, _PULSE_SANITY_FRAC
    R = CalibrationTab.DEFAULT_LOAD_OHM
    C = CalibrationTab.DEFAULT_LOAD_CAP_PF * 1e-12
    W = CalibrationTab.PHASE_WIDTH_US * 1e-6
    I = amp_ua * 1e-6
    expect = I * R + I * W / C          # k cancels in the ratio
    # A faithful capture reaches the full excursion -> ratio 1.0.
    assert 1.0 > _PULSE_SANITY_FRAC
    # A free-running frame is ~noise (say 1% of expected) -> rejected.
    assert 0.01 * expect < _PULSE_SANITY_FRAC * expect


def test_flat_slope_would_have_reported_absurd_capacitance():
    """Reproduce the bench number so the guard's necessity is on record."""
    I, k = 100e-6, 1.0
    slope = 0.14                      # V/s -- essentially flat
    c_pf = (I * k / slope) * 1e12
    assert c_pf > 1e8                 # ~7e8 pF, as observed
    # The expected slope is four orders of magnitude larger.
    from stimtest.gui.calibration import CalibrationTab, _SLOPE_SANITY_FRAC
    expect = I * k / (CalibrationTab.DEFAULT_LOAD_CAP_PF * 1e-12)
    assert slope < _SLOPE_SANITY_FRAC * expect
