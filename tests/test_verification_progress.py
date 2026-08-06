"""Verification progress must survive retries.

A channel that fails its checks re-runs its ENTIRE amplitude sweep (up to 3
attempts), so a running capture counter has no fixed denominator.  The old
``step_idx / (channels x amplitudes)`` overflowed on a real 16-channel run --
the operator saw ``step 113 / 64`` with the bar pinned at 100 % from roughly
channel 6 onwards, i.e. the bar was dead for most of the sweep.

Progress is therefore CHANNELS COMPLETED + the fraction of the current
channel's CURRENT attempt: bounded by construction, and monotonic so a retry
never drags the bar backwards.
"""
from __future__ import annotations

import pathlib

import pytest

from stimtest.gui.calibration import CalibrationTab

pct = CalibrationTab.sweep_progress_pct


def test_reaches_exactly_100_on_the_last_step():
    assert pct(ch_pos=15, n_channels=16, amp_i=3, n_amps=4) == 100


def test_starts_small_not_at_100():
    """The reported failure: the bar sat at 100 % for most of the sweep."""
    assert pct(ch_pos=0, n_channels=16, amp_i=0, n_amps=4) < 5


def test_channel_10_of_16_is_not_100_percent():
    """Exactly the screenshot: channel 10/16, first amplitude, attempt 2."""
    p = pct(ch_pos=9, n_channels=16, amp_i=0, n_amps=4)
    assert 55 <= p <= 62, p


def test_never_exceeds_100_however_many_retries():
    """The core regression -- retries used to push the numerator past the
    denominator.  Progress is retry-INDEPENDENT: it never sees the count."""
    for attempt in range(50):          # absurd retry count
        for amp_i in range(4):
            assert 0 <= pct(ch_pos=15, n_channels=16,
                            amp_i=amp_i, n_amps=4) <= 100


def test_monotonic_across_a_retry():
    """A retry restarts the within-channel fraction; the bar must hold, not
    jump backwards (which reads as a fault)."""
    last = 0
    seen = []
    for _attempt in range(3):
        for amp_i in range(4):
            last = pct(ch_pos=5, n_channels=16, amp_i=amp_i,
                       n_amps=4, last_pct=last)
            seen.append(last)
    assert seen == sorted(seen), seen


def test_monotonic_across_the_whole_sweep_with_retries():
    last = 0
    seen = []
    for ch_pos in range(16):
        n_attempts = 3 if ch_pos % 3 == 0 else 1     # some channels retry
        for _a in range(n_attempts):
            for amp_i in range(4):
                last = pct(ch_pos, 16, amp_i, 4, last_pct=last)
                seen.append(last)
    assert seen == sorted(seen)
    assert seen[-1] == 100


def test_single_channel_sweep():
    assert pct(0, 1, 0, 4) == 25
    assert pct(0, 1, 3, 4) == 100


def test_degenerate_counts_do_not_divide_by_zero():
    assert 0 <= pct(0, 0, 0, 0) <= 100


def _src(rel):
    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / rel).read_text(encoding="utf-8")


def test_sweep_uses_the_helper_not_step_over_total():
    src = _src("stimtest/gui/calibration.py")
    assert "self.sweep_progress_pct(" in src
    assert "100.0 * step_idx / max(1, total_steps)" not in src, \
        "the overflowing step/total progress formula is back"


def test_status_text_no_longer_shows_a_running_step_total():
    """``step 113 / 64`` was nonsense; the text now reports position within
    the current channel's attempt."""
    src = _src("stimtest/gui/calibration.py")
    assert "step {step_idx} / {total_steps}" not in src
    assert "amplitude {amp_i + 1} / {n_amps}" in src


def test_channel_denominator_is_the_count_not_the_last_channel_number():
    """``channels[-1]`` mis-rendered a channel SUBSET: sweeping {5, 7, 9}
    showed "Channel 7 / 9"."""
    src = _src("stimtest/gui/calibration.py")
    assert "Channel {ch} / {channels[-1]}" not in src
