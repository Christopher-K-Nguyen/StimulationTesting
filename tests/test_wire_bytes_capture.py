"""OPT-IN capture of the raw CURVe? bytes off the wire.

``query_binary_values`` parses the IEEE-488.2 block internally, so the bytes
that actually crossed the link are never seen — which is precisely what you
need when a TRANSFER fault is suspected (a truncated block, a wrong declared
byte count, a stray terminator) rather than a scaling or decode error.

Default OFF: the decoded path is proven and this roughly doubles the stored
bytes, so it is a diagnostic you enable while chasing a fault.
"""
from __future__ import annotations

import numpy as np

from stimtest.hardware.tektronix import TektronixOscilloscope as T


def test_parses_a_definite_length_block():
    payload = bytes(range(0, 200))
    buf = b"#3200" + payload + b"\n"
    hdr, pl, tr = T.parse_ieee_block(buf)
    assert hdr == b"#3200"
    assert pl == payload
    assert tr == b"\n"


def test_parses_an_indefinite_length_block():
    payload = b"\x01\x02\x03"
    hdr, pl, tr = T.parse_ieee_block(b"#0" + payload + b"\r\n")
    assert hdr == b"#0"
    assert pl == payload


def test_a_truncated_block_is_visible_not_absorbed():
    """The whole point: a header declaring more than arrived must be
    detectable, not silently decoded into a short waveform."""
    payload = bytes(50)                      # only 50 of the declared 200
    hdr, pl, tr = T.parse_ieee_block(b"#3200" + payload)
    assert hdr == b"#3200"
    declared = int(hdr[2:])
    assert declared == 200
    assert len(pl) == 50 < declared          # mismatch is observable


def test_a_scpi_error_string_is_handed_back_whole():
    """A non-block response (what a SCPI error looks like) must not be parsed
    as if it were data."""
    hdr, pl, tr = T.parse_ieee_block(b"CURVE ERROR 2244\n")
    assert hdr == b""
    assert b"2244" in pl


class _FakeInst:
    """Minimal pyvisa stand-in that answers CURVe? with a real block."""

    def __init__(self, payload):
        self._payload = payload
        self.written = []

    def write(self, cmd):
        self.written.append(cmd)

    def read_raw(self):
        n = str(len(self._payload)).encode()
        return b"#" + str(len(n)).encode() + n + self._payload + b"\n"


def test_read_raw_path_decodes_identically_and_keeps_the_bytes():
    codes = np.array([-128, -1, 0, 1, 127], dtype=np.int8)
    payload = codes.tobytes()
    sc = T.__new__(T)                        # no hardware; exercise the reader
    sc._inst = _FakeInst(payload)
    arr, hdr, pl, tr, declared = sc._curve_via_read_raw("b", True)
    assert np.array_equal(arr, codes), arr
    assert pl == payload                     # the bytes are preserved verbatim
    assert declared == len(payload)
    assert sc._inst.written == ["CURVe?"]


def test_capture_wire_bytes_defaults_off():
    assert T.capture_wire_bytes is False
