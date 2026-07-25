"""Verification: discard the first N acquisitions per amplitude, keep the next.

Operator (CWRU): "the calibration runs too quickly … skip the first
completed acquisition", later deepened to "I want you to collect the third
sample" => discard TWO, keep the THIRD
(``CAL_DISCARD_ACQUISITIONS = 2``).  After ``load_channel`` +
``start_channel`` the early frames still carry the settings-change transient
(and, in AVERAGE mode, a stale blend of the PREVIOUS amplitude's pulses), so
discarding them leaves a capture accumulated purely from THIS amplitude.
The old one-time setup-discard frame is removed (the per-amplitude discard
subsumes it).  Averaging stays at 64 (the operator reverted the brief "use
the highest" change).
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def _src() -> str:
    return (_ROOT / "stimtest" / "gui" / "calibration.py").read_text(
        encoding="utf-8")


def _method(src: str, name: str) -> str:
    start = src.find(f"def {name}")
    assert start != -1, f"{name} not found"
    # Bound by the next 4-space-indented def (skips nested 8-space defs
    # like _clipped inside _capture_one_amplitude).
    end = src.find("\n    def ", start + 1)
    return src[start:end] if end != -1 else src[start:]


def test_discard_loop_precedes_kept_capture():
    block = _method(_src(), "_capture_one_amplitude")
    skip_pos = block.find("for _skip_i in range(")
    loop_pos = block.find("for _attempt in range(5)")
    assert skip_pos != -1, "per-amplitude discard loop missing"
    assert loop_pos != -1
    assert skip_pos < loop_pos, "discards must precede the kept-capture loop"
    # Two capture_single_sequence calls: the discard loop + the kept one.
    assert block.count("capture_single_sequence(") >= 2
    # The discard count is the class constant, not a magic number.
    assert "self.CAL_DISCARD_ACQUISITIONS" in block


def test_collects_the_third_sample():
    """Operator: "collect the third sample" -> discard 2, keep the 3rd."""
    from stimtest.gui.calibration import CalibrationTab
    assert CalibrationTab.CAL_DISCARD_ACQUISITIONS == 2


def test_pulse_rate_is_10_pps():
    """Operator: "Do 10 pps"."""
    from stimtest.gui.calibration import CalibrationTab
    assert CalibrationTab.PULSE_RATE_PPS == 10.0


def test_one_time_setup_discard_removed():
    # The old setup-time single throwaway log line is gone — the
    # per-amplitude skip-first subsumes it.
    assert "discarding 1st averaged frame" not in _src()


def test_averaging_pinned_at_64_not_scope_max():
    src = _src()
    assert "CAL_N_AVERAGES: int = 64" in src
    cn = _method(src, "_cal_navg")
    # _cal_navg returns the constant, NOT the scope's max (reverted).
    assert "max_average_count" not in cn
    assert "CAL_N_AVERAGES" in cn
