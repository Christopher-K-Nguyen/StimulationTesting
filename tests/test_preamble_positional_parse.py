"""Tests for LOG_ANALYSIS.md finding #5: WFMOutpre? batch-parse fallback
when ``HEADer OFF`` strips field names.

Bug: ``open()`` sets ``HEADer OFF`` (to reduce log chatter) which
makes WFMOutpre? responses values-only:

    1;8;BINARY;RI;MSB;"Ch1, ...";20000;Y;"s";32e-9;-320e-6;0;"V";8e-3;0;0

The name-based parser was looking for "FIELDNAME VALUE" per chunk
and found none — it grabbed chunks like "BINARY", "MSB", "RI" as
field names (with empty values) instead of as values for ENCDG /
BYT_OR / BN_FMT.  Required fields (YMULT / XINCR / etc.) were
absent → batch-parse failure → fallback to 9 per-field queries
(~450 ms per channel).

Fix: positional parse fallback when name-mode finds no required
fields.  Walks chunks against
:data:`TektronixOscilloscope._PREAMBLE_POSITIONAL_FIELDS` in order.

Tests pin both parse paths AND the fallback selection logic.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Real responses captured from session_001 log
# ---------------------------------------------------------------------------
# HEADer OFF response (pre-fix this triggered the fallback to per-field).
_HEADER_OFF_REAL = (
    '1;8;BINARY;RI;MSB;"Ch1, DC coupling, 200.0mV/div, 40.00us/div, '
    '20000 points, Average mode";20000;Y;"s";'
    '32.0000E-9;-320.0000E-6;0;"V";8.0000E-3;0.0E+0;0.0E+0;'
    '20000;200000000'
)

# HEADer ON response shape (per Tek programmer manual).  Same scope,
# same capture, just with the field-name prefix on each chunk.
_HEADER_ON_REAL = (
    ':WFMOUTPRE:BYT_NR 1;BIT_NR 8;ENCDG BINARY;BN_FMT RI;'
    'BYT_OR MSB;WFID "Ch1, DC coupling, 200.0mV/div, ...";'
    'NR_PT 20000;PT_FMT Y;XUNIT "s";'
    'XINCR 32.0000E-9;XZERO -320.0000E-6;PT_OFF 0;'
    'YUNIT "V";YMULT 8.0000E-3;YOFF 0.0E+0;YZERO 0.0E+0'
)


# ---------------------------------------------------------------------------
# HEADer ON path — original name-based parse
# ---------------------------------------------------------------------------
def test_name_mode_parses_header_on_response():
    """HEADer ON response is parsed by the name-based path; every
    documented field lands in the dict with its correct value."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    parsed = TektronixOscilloscope._parse_batch_preamble(_HEADER_ON_REAL)

    assert parsed["BYT_NR"] == "1"
    assert parsed["BIT_NR"] == "8"
    assert parsed["ENCDG"] == "BINARY"
    assert parsed["BN_FMT"] == "RI"
    assert parsed["BYT_OR"] == "MSB"
    assert parsed["NR_PT"] == "20000"
    assert parsed["XINCR"] == "32.0000E-9"
    assert parsed["XZERO"] == "-320.0000E-6"
    assert parsed["YMULT"] == "8.0000E-3"
    assert parsed["YOFF"] == "0.0E+0"
    assert parsed["YZERO"] == "0.0E+0"
    # String fields should have quotes stripped.
    assert parsed["XUNIT"] == "s"
    assert parsed["YUNIT"] == "V"


# ---------------------------------------------------------------------------
# HEADer OFF path — positional fallback
# ---------------------------------------------------------------------------
def test_positional_mode_parses_header_off_response():
    """HEADer OFF response (values-only) is parsed via the positional
    fallback.  Same field values land in the dict — downstream code
    that does ``out["YMULT"]`` works identically regardless of mode."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    parsed = TektronixOscilloscope._parse_batch_preamble(_HEADER_OFF_REAL)

    # Same assertions as the HEADer ON test — both modes converge on
    # the same dict shape.
    assert parsed["BYT_NR"] == "1"
    assert parsed["BIT_NR"] == "8"
    assert parsed["ENCDG"] == "BINARY"
    assert parsed["BN_FMT"] == "RI"
    assert parsed["BYT_OR"] == "MSB"
    assert parsed["NR_PT"] == "20000"
    assert parsed["XINCR"] == "32.0000E-9"
    assert parsed["XZERO"] == "-320.0000E-6"
    assert parsed["YMULT"] == "8.0000E-3"
    assert parsed["YOFF"] == "0.0E+0"
    assert parsed["YZERO"] == "0.0E+0"
    assert parsed["XUNIT"] == "s"
    assert parsed["YUNIT"] == "V"


def test_positional_mode_drops_trailing_fields_silently():
    """Some firmware appends extra fields past the documented
    16-field set (e.g. NR_FR, sample rate).  Those land past the
    known positional list and are silently dropped.  Required
    fields are still populated."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    # _HEADER_OFF_REAL has 18 fields total — 16 documented + 2 extra.
    parsed = TektronixOscilloscope._parse_batch_preamble(_HEADER_OFF_REAL)
    # All 16 documented fields are present.
    for field in TektronixOscilloscope._PREAMBLE_POSITIONAL_FIELDS:
        assert field in parsed, (
            f"positional parser missed documented field {field!r}; "
            f"got keys: {sorted(parsed.keys())}")


def test_required_field_check_drives_fallback_selection():
    """The selector for name-vs-positional is: if name-mode produced
    NONE of the required Y / X fields, switch to positional.  The
    HEADer OFF response has zero name matches (every chunk is a
    value), so the positional path fires."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    # Direct call — no field names, so name-mode should find nothing
    # required, and the positional fallback should populate the dict.
    parsed = TektronixOscilloscope._parse_batch_preamble(_HEADER_OFF_REAL)
    # Verify the positional path actually fired by checking we got
    # YMULT (not just BINARY / RI / MSB / Y which the buggy name-mode
    # would have grabbed as field names).
    assert "YMULT" in parsed
    # And the buggy name-mode artifacts shouldn't appear as keys.
    assert "BINARY" not in parsed  # this was a VALUE, not a field name


# ---------------------------------------------------------------------------
# Mixed / edge-case responses
# ---------------------------------------------------------------------------
def test_name_mode_preferred_when_both_paths_could_parse():
    """If name-mode populates the required fields, positional is
    skipped — name-mode is more robust against firmware that
    reorders fields."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    # Use the HEADer ON response — name-mode should suffice.
    parsed = TektronixOscilloscope._parse_batch_preamble(_HEADER_ON_REAL)
    # All required fields present from name-mode.
    for f in ("YMULT", "XINCR", "XZERO", "YOFF", "YZERO"):
        assert f in parsed


def test_empty_input_returns_empty_dict():
    from stimtest.hardware.tektronix import TektronixOscilloscope
    assert TektronixOscilloscope._parse_batch_preamble("") == {}
    assert TektronixOscilloscope._parse_batch_preamble(";;;") == {}


def test_quoted_strings_unwrapped_in_both_modes():
    """Quoted string values (WFID, XUNIT, YUNIT) have their
    surrounding double quotes stripped regardless of parse mode."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    on_parsed = TektronixOscilloscope._parse_batch_preamble(_HEADER_ON_REAL)
    off_parsed = TektronixOscilloscope._parse_batch_preamble(_HEADER_OFF_REAL)

    for parsed in (on_parsed, off_parsed):
        assert parsed["XUNIT"] == "s"  # not "\"s\""
        assert parsed["YUNIT"] == "V"
        # WFID's quoted contents should be unwrapped too.
        assert parsed["WFID"].startswith("Ch1")
        assert '"' not in parsed["WFID"]


def test_positional_handles_short_response():
    """A response with fewer than 16 chunks (e.g. some legacy
    firmware variant) should still populate whatever fields it
    can without raising.  Caller's missing-field check will then
    flag what's actually missing."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    short = "1;8;BINARY;RI;MSB"  # only 5 fields, no Y or X values
    parsed = TektronixOscilloscope._parse_batch_preamble(short)
    assert parsed["BYT_NR"] == "1"
    assert parsed["BIT_NR"] == "8"
    assert parsed["ENCDG"] == "BINARY"
    assert parsed["BN_FMT"] == "RI"
    assert parsed["BYT_OR"] == "MSB"
    # No YMULT, no XINCR — caller will raise the missing-field error.
    assert "YMULT" not in parsed
    assert "XINCR" not in parsed


def test_positional_field_list_documents_canonical_order():
    """Spot-check: the positional field list matches the order Tek's
    programmer manual documents (and what every TBS / TDS / MDO /
    DPO scope we've tested emits).  Catches reorderings of the
    constant."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    fields = TektronixOscilloscope._PREAMBLE_POSITIONAL_FIELDS
    # First five are byte-format header fields.
    assert fields[:5] == ("BYT_NR", "BIT_NR", "ENCDG", "BN_FMT", "BYT_OR")
    # Position 5 is the descriptive WFID string.
    assert fields[5] == "WFID"
    # Time-axis triple sits at 9-11 (after NR_PT, PT_FMT, XUNIT).
    assert fields[9:12] == ("XINCR", "XZERO", "PT_OFF")
    # Y-axis triple at the end.
    assert fields[13:16] == ("YMULT", "YOFF", "YZERO")
