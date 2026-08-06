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
    """Models ``DATa:STOP`` as real instrument state.

    ``applied`` is the value the firmware STARTS with -- the stale bound
    (16624 on the bench) that truncated the transfer.  Writes move it, so a
    pin/restore round-trip is observable rather than assumed.
    """

    def __init__(self, record=20000, applied=None):
        self.writes = []
        self._hw_record = record          # what the instrument reports
        self._record_length = record      # the driver's cache (may be None)
        self._applied = record if applied is None else applied
        self._logs = []
        self._cmds = _Cmds()

    def _w(self, cmd):
        self.writes.append(cmd)
        if cmd.startswith("DATa:STOP "):
            self._applied = int(cmd.split()[-1])

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


# ---------------------------------------------------------------------------
# The pin is DEVICE state on a SHARED scope -- it MUST be undone (gotcha #82).
# ---------------------------------------------------------------------------

def test_restore_puts_back_the_pre_pin_value():
    """The firmware's stale bound is the state the experiment path expects."""
    s = _FakeScope(record=20000, applied=16624)
    assert s.set_transfer_full_record() == 20000
    assert s._applied == 20000                     # pinned
    assert s.restore_transfer_window() == 16624    # and put back
    assert s._applied == 16624
    assert "DATa:STOP 16624" in s.writes


def test_restore_is_a_noop_when_nothing_was_pinned():
    """Safe to call unconditionally -- the experiment start-up hook does."""
    s = _FakeScope(record=20000, applied=16624)
    assert s.restore_transfer_window() is None
    assert not [w for w in s.writes if w.startswith("DATa:STOP")]
    assert s._applied == 16624                     # untouched


def test_repeat_pin_keeps_the_original_stash():
    """A second pin must not overwrite the stash with our OWN pinned value,
    or the restore would write 20000 back and never actually un-pin."""
    s = _FakeScope(record=20000, applied=16624)
    s.set_transfer_full_record()
    s.set_transfer_full_record()
    assert s.restore_transfer_window() == 16624


def test_restore_is_idempotent():
    """Second call is a no-op -- the stash is consumed."""
    s = _FakeScope(record=20000, applied=16624)
    s.set_transfer_full_record()
    assert s.restore_transfer_window() == 16624
    n_before = len(s.writes)
    assert s.restore_transfer_window() is None
    assert len(s.writes) == n_before


def test_failed_restore_still_clears_the_stash():
    """A scope that rejects the write must not leave a phantom pending
    un-pin that a later caller believes it still owns."""
    s = _FakeScope(record=20000, applied=16624)
    s.set_transfer_full_record()

    def _boom(cmd):
        raise RuntimeError("no such command")
    s._w = _boom
    assert s.restore_transfer_window() is None
    assert getattr(s, "_prev_data_stop", None) is None


def test_restore_invalidates_preamble_cache():
    s = _FakeScope(record=20000, applied=16624)
    s.set_transfer_full_record()
    s.invalidated = False
    s.restore_transfer_window()
    assert s.invalidated is True


def test_base_oscilloscope_has_noop_defaults():
    """Simulator / PicoScope backends must not need an override."""
    from stimtest.hardware.base import Oscilloscope
    assert Oscilloscope.set_transfer_full_record(object()) is None
    assert Oscilloscope.restore_transfer_window(object()) is None


def test_open_clears_the_stash():
    """A reconnect re-enumerates the device -- a stale bound must not be
    written back into a fresh session."""
    src = _src("stimtest/hardware/tektronix.py")
    body = src[src.find('self._w("DATa:STARt 1")'):]
    body = body[:body.find("def ")]
    assert "_prev_data_stop = None" in body


def _code_lines(rel):
    """Source with comment-only lines stripped.

    A bare substring search over the whole file is NOT a behaviour guard:
    these very invariants are described in prose right next to the code, so
    ``"restore_transfer_window" in src`` matches the COMMENT explaining why it
    must not be called.  (That bit us once already this session.)
    """
    out = []
    for line in _src(rel).splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def test_verification_does_not_unpin():
    """The pin is deliberately LEFT IN PLACE.

    Operator: "we did that DATa:STOP because even on our setup, the waveform
    was being clipped, and the record length was incomplete."  The firmware's
    stale absolute DATa:STOP truncates ``CURVe?`` to a prefix and cuts the end
    of the pulse off, on the EXPERIMENT path too — so un-pinning after the
    sweep would reintroduce the very clipping the pin fixes.
    """
    code = _code_lines("stimtest/gui/calibration.py")
    assert "set_transfer_full_record()" in code
    assert "restore_transfer_window" not in code, \
        "verification un-pins DATa:STOP — that reintroduces the clipping"


def test_experiment_start_does_not_unpin():
    """The experiment path must not clear a pin it depends on."""
    code = _code_lines("stimtest/gui/experiment_tabs.py")
    assert "restore_transfer_window" not in code, \
        "the experiment start un-pins DATa:STOP — reintroduces truncation"


def test_restore_helper_still_exists_but_is_uncalled():
    """Kept + tested but dormant, so a future caller has a correct inverse
    available without anyone wiring it in by accident."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    assert callable(TektronixOscilloscope.restore_transfer_window)
    for rel in ("stimtest/gui/calibration.py",
                "stimtest/gui/experiment_tabs.py",
                "stimtest/experiments/base.py",
                "stimtest/experiments/voltage_transient.py"):
        assert "restore_transfer_window" not in _code_lines(rel), rel


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
