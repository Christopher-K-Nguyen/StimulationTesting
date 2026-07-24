"""Long numeric values in the scope response log render in scientific notation
(operator #4: "use scientific notation for long values such as … 200000000").
Only the LOGGED copy is reformatted; short / already-scientific / non-numeric
fields are untouched.
"""
from __future__ import annotations

from stimtest.hardware.tektronix import _sci_notation_long_numbers


def test_wfmoutpre_sample_rate_becomes_scientific():
    resp = ('1;8;BINARY;RI;MSB;"Ch1, DC coupling, 1.000V/div, 200.0us/div, '
            '20000 points, Average mode";16624;Y;"s";160.0000E-9;-1.6000E-3;0;'
            '"V";40.0000E-3;0.0E+0;0.0E+0;20000;200000000')
    out = _sci_notation_long_numbers(resp)
    # the 9-digit sample rate → compact scientific.
    assert "2.0E+8" in out
    assert "200000000" not in out
    # short numbers + already-scientific + text fields are all untouched.
    assert "20000 points" in out          # inside the quoted preamble string
    assert ";20000;" in out               # the record-length field stays plain
    assert "16624" in out
    assert "160.0000E-9" in out
    assert "-1.6000E-3" in out
    assert "40.0000E-3" in out
    assert "0.0E+0" in out


def test_helper_various_tokens():
    assert _sci_notation_long_numbers("200000000") == "2.0E+8"
    assert _sci_notation_long_numbers("123456789") == "1.2346E+8"
    assert _sci_notation_long_numbers("2500000") == "2.5E+6"      # exactly 7 digits
    # < 7 digits, already-scientific, and decimals-only left alone.
    assert _sci_notation_long_numbers("20000") == "20000"
    assert _sci_notation_long_numbers("16624") == "16624"
    assert _sci_notation_long_numbers("160.0000E-9") == "160.0000E-9"
    assert _sci_notation_long_numbers("1.000V/div") == "1.000V/div"
