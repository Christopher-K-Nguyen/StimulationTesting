"""Scope bandwidth policy: FULL on data channels, 20 MHz on the trigger.

CWRU bench: on a 2-channel scope, I_mon IS the trigger, and running it at
FULL bandwidth (an operator preference) made the trigger comparator miss the
small I_mon peak (level -11.5 mV vs an actual -8 mV peak) — the scope barely
triggered, producing garbage captures that then fit a degenerate verification
gain (gotcha #161).  The fix band-limits ONLY the trigger channel (clean
comparator, per the ``imon_trigger_level`` formula's 20 MHz design, gotcha
#6) while every data-only channel keeps full bandwidth.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")
from stimtest.gui.experiment_tabs import _apply_channel_bandwidths  # noqa: E402


class _MockScope:
    def __init__(self, bw_for_purpose=20.0, bw_full=float("inf")):
        self.calls = []
        self._p = bw_for_purpose
        self._f = bw_full

    def set_channel_bandwidth_for_purpose(self, ch, purpose):
        self.calls.append((ch, "purpose", purpose))
        return self._p

    def set_channel_bandwidth_full(self, ch):
        self.calls.append((ch, "full"))
        return self._f


def _kinds(results):
    return {ch: kind for (_alias, ch, _bw, kind) in results}


def test_two_channel_imon_trigger_gets_band_limited():
    sc = _MockScope()
    res = _apply_channel_bandwidths(
        sc, {"imon": "CH1", "vmon": "CH2", "trigger": "CH1"})
    # CH1 (I_mon AND trigger) → band-limited via for_purpose; CH2 (V_mon) full.
    assert ("CH1", "purpose", "trigger") in sc.calls
    assert ("CH2", "full") in sc.calls
    # CH1 is configured EXACTLY once (not double-set by the two roles).
    assert sum(1 for c in sc.calls if c[0] == "CH1") == 1
    k = _kinds(res)
    assert k["CH1"] == "trigger"
    assert k["CH2"] == "data"


def test_four_channel_digital_trigger_keeps_imon_full():
    sc = _MockScope()
    res = _apply_channel_bandwidths(
        sc, {"imon": "CH1", "vmon": "CH2", "eret": "CH3", "trigger": "CH4"})
    # I_mon (CH1) stays FULL (operator preference); the digital trigger (CH4)
    # is band-limited.  I_mon is NOT the trigger here.
    assert ("CH1", "full") in sc.calls
    assert ("CH4", "purpose", "trigger") in sc.calls
    k = _kinds(res)
    assert k["CH1"] == "data"
    assert k["CH4"] == "trigger"


def test_no_trigger_alias_all_full():
    sc = _MockScope()
    res = _apply_channel_bandwidths(sc, {"imon": "CH1", "vmon": "CH2"})
    assert all(c[1] == "full" for c in sc.calls)
    assert all(kind == "data" for (_a, _c, _b, kind) in res)


def test_none_bandwidth_is_skipped_in_results():
    class _NoBw:
        def set_channel_bandwidth_for_purpose(self, ch, p):
            return None

        def set_channel_bandwidth_full(self, ch):
            return None

    res = _apply_channel_bandwidths(_NoBw(), {"imon": "CH1", "trigger": "CH1"})
    assert res == []
