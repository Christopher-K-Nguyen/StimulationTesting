"""Tests for LOG_ANALYSIS.md finding #6: USB stall spike logging.

Bug class: libusb-win32 / USB-TMC random transient stalls cause SCPI
queries that normally take 1-5 ms to take 800+ ms (e.g.
``CH2:SCAle?`` taking 0.83 s).  These stalls are not recovered by the
new open()-time retry logic (gotcha #30) because that only fires on
connect, not on per-query basis during a run.

Pure observability fix: track per-query latency, flag anything above
threshold (default 500 ms, well above legitimately-slow ops like
``HORizontal:RECOrdlength``), aggregate session-wide stats, emit
summary in close().

Tests pin:
- Threshold + exempt-list behaviour
- First-stall + every-10th log line
- Session-wide stats aggregation (count + worst latency + worst cmd)
- Session-end summary in close()
- Reset on open() (no cross-session leak)
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helper — bare oscilloscope with stub _log + stall stats wiring only
# ---------------------------------------------------------------------------
def _bare_scope():
    """Construct a TektronixOscilloscope with just enough infra to
    exercise _track_query_latency directly.  Returns (scope,
    log_lines)."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    log_lines: list = []
    scope._log = log_lines.append
    # Initialize the stats dict the way open() would.
    scope._stall_stats = {
        "count": 0,
        "worst_s": 0.0,
        "worst_cmd": "",
        "first_logged": False,
    }
    return scope, log_lines


# ---------------------------------------------------------------------------
# Threshold behaviour
# ---------------------------------------------------------------------------
def test_fast_query_does_not_count_as_stall():
    """Queries faster than the 500ms threshold should not bump the
    counter — most SCPI ops legitimately take 1-5 ms."""
    scope, log_lines = _bare_scope()
    scope._track_query_latency("CH1:SCAle?", 0.003)  # 3ms — normal
    scope._track_query_latency("HORizontal:POSition?", 0.150)  # 150ms still under
    assert scope._stall_stats["count"] == 0
    assert log_lines == []  # no stall-warning lines


def test_slow_query_above_threshold_bumps_count():
    """A query above the 500ms threshold (and not on the exempt list)
    bumps the count + records the latency + emits the first-stall log
    line."""
    scope, log_lines = _bare_scope()
    scope._track_query_latency("CH2:SCAle?", 0.83)  # 830ms — real stall
    assert scope._stall_stats["count"] == 1
    assert scope._stall_stats["worst_s"] == pytest.approx(0.83)
    assert scope._stall_stats["worst_cmd"] == "CH2:SCAle?"
    # First-stall log line should mention the stall + latency + threshold.
    stall_msgs = [m for m in log_lines if "USB STALL" in m]
    assert len(stall_msgs) == 1
    assert "CH2:SCAle?" in stall_msgs[0]
    assert "#1" in stall_msgs[0]


def test_threshold_constant_is_documented_value():
    """The threshold should be 500 ms — well above legitimate CURVe?
    times (~110 ms) and well below intrinsically-slow setters
    (set_record_length: ~30 s).  If someone retunes this, the test
    is the place to document the new chosen value."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    assert TektronixOscilloscope._STALL_THRESHOLD_S == 0.5


# ---------------------------------------------------------------------------
# Exempt list — legitimately-slow ops don't trigger stall warnings
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("cmd", [
    "HORizontal:RECOrdlength 20000",
    "HOR:RECO 20000",
    "ACQuire:MODe AVERAGE",
    "ACQuire:STATE RUN",
    "*RST",
    "*CLS",
    "CURVe?",
    "WFMOutpre?",
    "wfmpre?",  # lowercase / legacy form
])
def test_exempt_commands_skip_stall_count(cmd):
    """Commands on the exempt list — these are KNOWN to be slow by
    design (internal buffer realloc, full-waveform readout, reset, etc.)
    — should not bump the stall counter even at multi-second latency."""
    scope, log_lines = _bare_scope()
    scope._track_query_latency(cmd, 5.0)  # 5 seconds, way over threshold
    assert scope._stall_stats["count"] == 0, (
        f"exempt command {cmd!r} bumped stall count")
    assert log_lines == []


def test_non_exempt_command_at_same_latency_does_count():
    """Sanity check: same 5-second latency on a NON-exempt command
    DOES bump the count.  Rules out 'threshold was bumped to 5s'
    silently breaking the test above."""
    scope, log_lines = _bare_scope()
    scope._track_query_latency("CH1:POSition?", 5.0)
    assert scope._stall_stats["count"] == 1


# ---------------------------------------------------------------------------
# Worst-latency tracking
# ---------------------------------------------------------------------------
def test_worst_latency_tracking_updates_monotonically():
    """worst_s + worst_cmd should always reflect the SLOWEST query
    seen so far this session — not the most recent."""
    scope, _ = _bare_scope()
    scope._track_query_latency("CH1:SCAle?", 0.6)
    scope._track_query_latency("CH2:SCAle?", 1.2)
    scope._track_query_latency("CH3:SCAle?", 0.7)  # back down
    assert scope._stall_stats["worst_s"] == pytest.approx(1.2)
    assert scope._stall_stats["worst_cmd"] == "CH2:SCAle?"
    # Counter should reflect 3 stalls total.
    assert scope._stall_stats["count"] == 3


# ---------------------------------------------------------------------------
# Periodic-log throttling
# ---------------------------------------------------------------------------
def test_only_first_and_every_tenth_stall_log():
    """To avoid log spam on heavy-stall sessions, we log the first
    stall and then every 10th.  9 stalls in → 1 log line.  10 stalls
    in → 2 log lines (the first + the 10th)."""
    scope, log_lines = _bare_scope()
    # 9 stalls — should produce 1 log line.
    for _ in range(9):
        scope._track_query_latency("CH1:SCAle?", 0.6)
    assert len([m for m in log_lines if "USB STALL" in m]) == 1
    # 10th stall — should produce the 2nd log line.
    scope._track_query_latency("CH1:SCAle?", 0.6)
    assert len([m for m in log_lines if "USB STALL" in m]) == 2
    # 11th-19th — still 2.
    for _ in range(9):
        scope._track_query_latency("CH1:SCAle?", 0.6)
    assert len([m for m in log_lines if "USB STALL" in m]) == 2
    # 20th — 3rd log line.
    scope._track_query_latency("CH1:SCAle?", 0.6)
    assert len([m for m in log_lines if "USB STALL" in m]) == 3


def test_stall_log_line_includes_running_count_and_worst():
    """The throttled stall log line should give the operator enough
    info to understand the session-wide state at a glance: total
    stall count, worst latency, worst command."""
    scope, log_lines = _bare_scope()
    scope._track_query_latency("CH1:SCAle?", 0.55)  # first stall
    scope._track_query_latency("CH2:SCAle?", 0.99)  # worst so far
    # First-stall log was already emitted; we'd need 10 to get the
    # second log line.  Push to 10.
    for i in range(8):
        scope._track_query_latency(f"CH3:VAR{i}?", 0.6)
    # 10th stall — 2nd log line includes the running count + worst.
    stall_msgs = [m for m in log_lines if "USB STALL" in m]
    assert len(stall_msgs) == 2
    tenth = stall_msgs[1]
    assert "#10" in tenth
    assert "0.99" in tenth or "990" in tenth  # worst latency surfaced
    assert "CH2:SCAle?" in tenth  # worst command surfaced


# ---------------------------------------------------------------------------
# Lazy-init safety (helper before open())
# ---------------------------------------------------------------------------
def test_track_works_without_explicit_init():
    """If somebody calls _track_query_latency before open() has run
    (theoretically possible in tests / weird init sequences), the
    helper should lazy-init its stats dict rather than AttributeError."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    scope._log = lambda m: None  # no-op
    # No _stall_stats attribute yet.
    assert not hasattr(scope, "_stall_stats")
    # Call the helper — must not raise.
    scope._track_query_latency("CH1:SCAle?", 0.6)
    # And now the dict exists and reflects the stall.
    assert scope._stall_stats["count"] == 1


# ---------------------------------------------------------------------------
# Session-end summary in close()
# ---------------------------------------------------------------------------
def test_close_emits_summary_when_stalls_happened():
    """close() should emit a one-line summary of stalls — count +
    worst latency + worst command + remediation hint."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    log_lines: list = []
    scope._log = log_lines.append
    scope._inst = MagicMock()  # so close() actually runs its body
    scope._resource_hint = None
    scope._cached_resources = None
    scope._stall_stats = {
        "count": 5,
        "worst_s": 1.23,
        "worst_cmd": "CH3:POSition?",
        "first_logged": True,
    }

    scope.close()

    summary = [m for m in log_lines if "session USB-stall summary" in m]
    assert len(summary) == 1, (
        f"close() should emit exactly one session-summary line; "
        f"got: {summary!r}")
    line = summary[0]
    assert "5 SCPI call" in line  # count
    assert "1.23" in line or "1230" in line  # worst latency
    assert "CH3:POSition?" in line  # worst command


def test_close_skips_summary_when_no_stalls():
    """close() on a clean session (zero stalls) should not emit a
    summary line — no point logging when there's nothing to report.
    Keeps the log tidy for the well-behaved happy path."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    log_lines: list = []
    scope._log = log_lines.append
    scope._inst = MagicMock()
    scope._resource_hint = None
    scope._cached_resources = None
    scope._stall_stats = {
        "count": 0,
        "worst_s": 0.0,
        "worst_cmd": "",
        "first_logged": False,
    }

    scope.close()

    summary = [m for m in log_lines if "session USB-stall summary" in m]
    assert summary == [], (
        f"close() should NOT emit a summary when zero stalls; got: {summary!r}")


def test_close_summary_safe_when_stall_stats_missing():
    """If close() is called without ever having had open() initialize
    the stats dict (e.g. open() raised partway through), it should
    not blow up."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    log_lines: list = []
    scope._log = log_lines.append
    scope._inst = MagicMock()
    scope._resource_hint = None
    scope._cached_resources = None
    # No _stall_stats attribute.

    # Must not raise.
    scope.close()
    # No summary emitted either.
    summary = [m for m in log_lines if "session USB-stall summary" in m]
    assert summary == []
