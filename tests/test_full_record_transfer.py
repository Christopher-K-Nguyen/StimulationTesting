"""Verification transfers the WHOLE record; the experiment path does not.

``DATa:STOP`` is an ABSOLUTE sample index and the TBS2000 firmware leaves a
stale value in it -- observed stuck at 16624 against a 20000-point record, so
``CURVe?`` silently returned only ~83 % of the record (133 us of a 160 us
record at 8 ns/sample).

On the experiment's WIDE window the discarded tail is empty interpulse, which
is why it went unnoticed for so long.  On the verification tab's TIGHT window
the pulse fills the span, so the SAME truncation landed on the end of phase 2
and clipped its iR drop.

The pin is OPT-IN: the experiment keeps MATLAB parity (gotcha #82), where
pinning pulls in an off-screen record tail that can contain the next pulse's
onset -- the end-of-record artifact the rule exists to prevent.
"""
from __future__ import annotations

import pathlib

import pytest

from stimtest.hardware.tektronix import TektronixOscilloscope


class _Cmds:
    horiz_record = "HORizontal:RECOrdlength"


class _FakeScope(TektronixOscilloscope):
    def __init__(self, record=20000, applied=None):
        self.writes = []
        self._hw_record = record          # what the instrument reports
        self._record_length = record      # the driver's cache (may be None)
        self._applied = record if applied is None else applied
        self._logs = []
        self._cmds = _Cmds()

    def _w(self, cmd):
        self.writes.append(cmd)

    def _q(self, cmd):
        if cmd.startswith("DATa:STOP"):
            return str(self._applied)
        if "RECOrdlength" in cmd:
            return str(self._hw_record)
        raise AssertionError(f"unexpected query {cmd}")

    def _log(self, msg):
        self._logs.append(msg)

    def _invalidate_preamble_cache(self, channel=None):
        self.invalidated = True


def test_pins_data_stop_to_record_length():
    s = _FakeScope(record=20000)
    assert s.set_transfer_full_record() == 20000
    assert "DATa:STOP 20000" in s.writes


def test_invalidates_preamble_cache():
    """NR_Pt changes, so every channel's cached preamble is stale."""
    s = _FakeScope(record=20000)
    s.set_transfer_full_record()
    assert getattr(s, "invalidated", False) is True


def test_falls_back_to_query_when_record_unknown():
    s = _FakeScope(record=20000)
    s._record_length = None
    assert s.set_transfer_full_record() == 20000


def test_never_raises_on_failure():
    """A scope that rejects the write must not abort the sweep."""
    s = _FakeScope(record=20000)

    def _boom(cmd):
        raise RuntimeError("no such command")
    s._w = _boom
    assert s.set_transfer_full_record() is None


def _src(rel):
    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / rel).read_text(encoding="utf-8")


def test_verification_opts_in():
    src = _src("stimtest/gui/calibration.py")
    assert "set_transfer_full_record()" in src
    # ...right where the record length is configured.
    i_rec = src.find("set_record_length(DEFAULT_RECORD_LENGTH)")
    i_pin = src.find("set_transfer_full_record()")
    assert i_rec != -1 and i_pin != -1 and i_pin > i_rec


def test_experiment_path_does_not_pin():
    """MATLAB parity is preserved for experiments -- the pin is opt-in and
    must NOT appear in the runner/driver default paths."""
    for rel in ("stimtest/experiments/base.py",
                "stimtest/experiments/voltage_transient.py",
                "stimtest/gui/experiment_tabs.py"):
        assert "set_transfer_full_record" not in _src(rel), rel
    drv = _src("stimtest/hardware/tektronix.py")
    # The driver DEFINES it, but set_record_length must not call it.
    body = drv[drv.find("def set_record_length"):drv.find("def _refresh_record_length")]
    assert "set_transfer_full_record" not in body


def test_recovers_the_clipped_pulse_tail():
    """160 us record vs the 133 us that a stale DATa:STOP transferred: the
    125 us pulse only fits once the full record comes across."""
    record, xincr_us = 20000, 0.008
    stale_np, full_np = 16624, record
    pre_trigger_us = 16.0                     # ~10 % trigger position
    stale_end = stale_np * xincr_us - pre_trigger_us
    full_end = full_np * xincr_us - pre_trigger_us
    assert stale_end < 125.0 < full_end       # clipped before, fits after
    assert full_end - 125.0 > 15.0            # with real margin
