"""Calibration: skip the first completed averaged acquisition per amplitude.

Operator (CWRU): "the calibration runs too quickly … skip the first
completed acquisition."  After ``load_channel`` + ``start_channel`` the
scope's averager is still flushing the PREVIOUS amplitude's frames, so the
first completed average is a stale blend; calibration now discards it and
keeps the NEXT (pure) one.  The old one-time setup-discard frame is removed
(the per-amplitude skip subsumes it).  Averaging stays at 64 (the operator
reverted the brief "use the highest" change).
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


def test_skip_first_capture_before_kept_capture():
    block = _method(_src(), "_capture_one_amplitude")
    skip_pos = block.find("Skip the FIRST completed averaged acquisition")
    loop_pos = block.find("for _attempt in range(5)")
    assert skip_pos != -1, "skip-first throwaway capture missing"
    assert loop_pos != -1
    assert skip_pos < loop_pos, "skip-first must precede the kept-capture loop"
    # Two capture_single_sequence calls: the skip-first + the kept one.
    assert block.count("capture_single_sequence(") >= 2


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
