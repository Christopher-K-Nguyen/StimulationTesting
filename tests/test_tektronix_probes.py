"""Tests for the live-probe helpers in :mod:`stimtest.hardware.tektronix`.

The original audit flagged that ``stimtest/hardware/tektronix.py`` had
no test coverage beyond "is the DLL findable". These tests exercise
the model-adaptive probe layer — channel-count probe, EXT-trigger
probe, dialect selection, channel-count regex — using a mock pyvisa
instrument so the suite runs on any developer machine without real
scope hardware.

The probes are how the GUI grays out channels that don't exist and
falls back to a channel trigger when a scope lacks an EXT BNC. A
silent regression here would silently produce broken Setup-tab
state, which is exactly the class of bug this test file is meant
to catch.
"""
from __future__ import annotations

from typing import Any, List

import pytest

from stimtest.hardware.tektronix import (
    LEGACY, MODERN, TektronixOscilloscope,
    channel_count_from_model, select_dialect,
)


# ---------------------------------------------------------------------------
# Mock pyvisa "instrument" — just enough of the surface area used by
# the probes that we don't need pyvisa / a real scope.
# ---------------------------------------------------------------------------
class MockInstrument:
    """Records every write() and serves canned responses to queries.

    The test sets a ``query_responses`` dict mapping SCPI command →
    response string. Unknown queries raise ``MockInstrument.UnknownQuery``
    (analogue of pyvisa's IO error for an unrecognised header) so the
    probe logic exercises its error path.
    """

    class UnknownQuery(Exception):
        pass

    def __init__(self):
        self.query_responses: dict = {}
        self.writes: List[str] = []
        self.queries: List[str] = []

    def query(self, cmd: str) -> str:
        self.queries.append(cmd)
        if cmd in self.query_responses:
            v = self.query_responses[cmd]
            if isinstance(v, Exception):
                raise v
            return v
        raise self.UnknownQuery(f"unrecognised: {cmd!r}")

    def write(self, cmd: str) -> None:
        self.writes.append(cmd)


@pytest.fixture
def scope() -> TektronixOscilloscope:
    """Return a scope instance with a MockInstrument attached but no
    open() called — bypasses the pyvisa import chain. Probe methods
    don't need a live VISA backend, just ``self._inst``."""
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._inst = MockInstrument()
    return s


# ---------------------------------------------------------------------------
# probe_channel_count
# ---------------------------------------------------------------------------
class TestProbeChannelCount:
    """Walks ``CH<n>:PRObe:GAIN?`` from 1 upward, stopping at the first
    channel that doesn't respond cleanly. Drives the GUI's
    grey-out-unavailable-channels behaviour."""

    def test_four_channel_scope(self, scope):
        """TBS2204B-shaped response: CH1..CH4 answer with attenuation,
        CH5+ raises."""
        for n in (1, 2, 3, 4):
            scope._inst.query_responses[f"CH{n}:PRObe:GAIN?"] = "1.0"
        assert scope.probe_channel_count() == 4

    def test_two_channel_scope(self, scope):
        """TBS1052C-shaped response: only CH1 + CH2."""
        scope._inst.query_responses["CH1:PRObe:GAIN?"] = "1.0"
        scope._inst.query_responses["CH2:PRObe:GAIN?"] = "10.0"
        assert scope.probe_channel_count() == 2

    def test_eight_channel_scope(self, scope):
        """MSO6B-class scope with 8 input channels."""
        for n in range(1, 9):
            scope._inst.query_responses[f"CH{n}:PRObe:GAIN?"] = "1.0"
        assert scope.probe_channel_count(max_channels=8) == 8

    def test_no_instrument_returns_zero(self):
        """A scope without ``_inst`` attached returns 0 cleanly so the
        caller can fall back to the regex-from-model-name inference."""
        s = TektronixOscilloscope.__new__(TektronixOscilloscope)
        s._inst = None
        assert s.probe_channel_count() == 0

    def test_firmware_returning_error_string_inline(self, scope):
        """Some legacy firmware returns the error text instead of
        raising — the probe must recognise these substrings and
        stop counting."""
        scope._inst.query_responses["CH1:PRObe:GAIN?"] = "1.0"
        scope._inst.query_responses["CH2:PRObe:GAIN?"] = "1.0"
        scope._inst.query_responses["CH3:PRObe:GAIN?"] = (
            '110, "Undefined header; CH3"')
        # Even though CH3 returns a string, the probe should stop
        # at CH2 because CH3's response is not parseable as float
        # and contains "undefined".
        assert scope.probe_channel_count() == 2

    def test_empty_response_stops_count(self, scope):
        """Empty string from the scope (a sometimes-seen failure
        mode after a malformed *CLS) is treated as miss, not as
        ``float("") → 0``."""
        scope._inst.query_responses["CH1:PRObe:GAIN?"] = "1.0"
        scope._inst.query_responses["CH2:PRObe:GAIN?"] = ""
        assert scope.probe_channel_count() == 1


# ---------------------------------------------------------------------------
# probe_external_trigger
# ---------------------------------------------------------------------------
class TestProbeExternalTrigger:
    """Tries setting ``TRIGger:A:EDGE:SOUrce EXT`` and reads back;
    returns True iff the scope echoes ``EXT*``. Critical for
    TBS1000C / EDU scopes that don't have an EXT BNC."""

    def test_scope_with_ext_input(self, scope):
        """TBS2204B-shape: accept EXT, echo back ``EXT``."""
        scope._inst.query_responses["TRIGger:A:EDGE:SOUrce?"] = "CH1"
        # After we write "TRIGger:A:EDGE:SOUrce EXT", the read-back
        # should match. The probe sequence is query → write → query;
        # the second query needs to return the new source.
        def smart_query(cmd):
            scope._inst.queries.append(cmd)
            if cmd == "TRIGger:A:EDGE:SOUrce?":
                # After EXT was written, return EXT; before, CH1.
                if "TRIGger:A:EDGE:SOUrce EXT" in scope._inst.writes:
                    return "EXT"
                return "CH1"
            raise scope._inst.UnknownQuery(cmd)
        scope._inst.query = smart_query
        assert scope.probe_external_trigger() is True

    def test_scope_without_ext_input(self, scope):
        """TBS1000C: scope rejects EXT and keeps the original CH1
        as its source. Probe must return False."""
        def smart_query(cmd):
            scope._inst.queries.append(cmd)
            if cmd == "TRIGger:A:EDGE:SOUrce?":
                # Always reply CH1 — the scope refused EXT silently
                # (or echoed CH1 because it's a 2-channel basic
                # scope and EXT isn't a valid source for it).
                return "CH1"
            raise scope._inst.UnknownQuery(cmd)
        scope._inst.query = smart_query
        assert scope.probe_external_trigger() is False

    def test_scope_with_ext5_attenuation_input(self, scope):
        """``EXT5`` and ``EXT10`` are attenuation modes of EXT — the
        BNC IS present, the readback just names which divider is
        active. The probe must accept them as ``has_ext_trigger``."""
        def smart_query(cmd):
            scope._inst.queries.append(cmd)
            if cmd == "TRIGger:A:EDGE:SOUrce?":
                if "TRIGger:A:EDGE:SOUrce EXT" in scope._inst.writes:
                    return "EXT5"
                return "CH1"
            raise scope._inst.UnknownQuery(cmd)
        scope._inst.query = smart_query
        assert scope.probe_external_trigger() is True

    def test_no_instrument_returns_false(self):
        """No live scope handle → False (the safe default for the
        downstream "use channel trigger" fallback)."""
        s = TektronixOscilloscope.__new__(TektronixOscilloscope)
        s._inst = None
        assert s.probe_external_trigger() is False


# ---------------------------------------------------------------------------
# Dialect selection — model-name regex fallback (live probe is tested
# implicitly by every integration test that opens the simulator).
# ---------------------------------------------------------------------------
class TestSelectDialect:
    """Used by tests / dry runs when no live probe is available."""

    @pytest.mark.parametrize("model", [
        "TBS2204B", "TBS2104B", "TBS2074B",       # modern TBS2000B
        "TBS1052C", "TBS1102C", "TBS1072C",       # TBS1000C (modern set)
        "MSO4054", "MDO3014", "DPO4054B",         # MSO/MDO/DPO families
    ])
    def test_modern_models_pick_wfmoutpre(self, model):
        """All models on the modern-dialect list resolve to
        ``WFMOutpre``-style preambles."""
        assert select_dialect(model) is MODERN

    @pytest.mark.parametrize("model", [
        "TBS1052B", "TBS1102B",                   # original TBS1000B
        "TBS1052B-EDU", "TBS1102B-EDU",           # EDU variants
        "TDS2014", "TDS3014C",                    # legacy TDS families
    ])
    def test_legacy_models_pick_wfmpre(self, model):
        """Original TBS1000 / TBS1000B / TBS1000B-EDU + TDS2000/3000
        all fall back to the ``WFMPre`` legacy preamble."""
        assert select_dialect(model) is LEGACY

    def test_empty_model_falls_back_to_legacy(self):
        """Defensive — a missing/empty model name (scope didn't
        answer *IDN? cleanly) should pick the safer legacy
        dialect."""
        assert select_dialect("") is LEGACY


# ---------------------------------------------------------------------------
# channel_count_from_model — last-digit-of-numeric naming convention
# ---------------------------------------------------------------------------
class TestChannelCountFromModel:
    """Tek's basic-scope naming encodes channel count as the last
    digit of the 4-digit numeric part. The regex backs up the live
    probe for scopes that can't respond to queries."""

    @pytest.mark.parametrize("model, expected", [
        ("TBS2204B", 4),    # 200 MHz, 4-channel
        ("TBS2104B", 4),    # 100 MHz, 4-channel
        ("TBS2074B", 4),    # 70 MHz,  4-channel
        ("TBS1052C", 2),    # 50 MHz,  2-channel
        ("TBS1072C", 2),    # 70 MHz,  2-channel
        ("TBS1102C", 2),    # 100 MHz, 2-channel
    ])
    def test_known_tbs_models(self, model, expected):
        assert channel_count_from_model(model) == expected

    def test_unknown_model_defaults_to_four(self):
        """MSO / MDO / DPO scopes don't follow the TBS naming
        convention; the helper conservatively returns 4 (the typical
        configuration) so callers don't grey out channels that exist."""
        assert channel_count_from_model("MSO4054") == 4
        assert channel_count_from_model("UnknownScope123") == 4

    def test_empty_model_returns_four(self):
        """Empty string → safe default rather than crash."""
        assert channel_count_from_model("") == 4
        assert channel_count_from_model(None) == 4
