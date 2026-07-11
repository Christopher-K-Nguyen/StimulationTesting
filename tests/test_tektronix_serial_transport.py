"""Transport-aware SCPI robustness for serial / RS232(-to-USB) scopes.

Operator hit poor comms on a TPS2014B over RS232-to-USB despite a matched
baud rate.  The driver now detects an ASRL/COM resource (``_is_serial``),
tunes the serial link in ``open()``, and adds per-command robustness —
error-queue drain after every write + query validation/retry — GATED to
serial so the USB-TMC hot path stays on the plain fast lane.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest


def _scope(is_serial: bool):
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._inst = MagicMock()
    s._is_serial = is_serial
    s._timeout_ms = 10000
    s.serial_flow_control = None
    s.serial_baud_rate = None
    s._log = lambda *a, **k: None
    s._track_query_latency = lambda *a, **k: None
    return s


# ---- transport detection ----
def test_resource_is_serial():
    from stimtest.hardware.tektronix import TektronixOscilloscope as T
    assert T._resource_is_serial("ASRL3::INSTR") is True
    assert T._resource_is_serial("COM4") is True
    assert T._resource_is_serial("USB0::0x0699::0x0368::C0::INSTR") is False
    assert T._resource_is_serial("") is False
    assert T._resource_is_serial(None) is False


# ---- _w: error-queue drain ONLY on serial ----
def test_usb_write_does_not_drain():
    s = _scope(is_serial=False)
    s._w("CH1:SCAle 1.0")
    s._inst.write.assert_called_once_with("CH1:SCAle 1.0")
    s._inst.query.assert_not_called()   # no SYSTem:ERRor? on USB-TMC


def test_serial_write_drains_error_queue():
    s = _scope(is_serial=True)
    s._inst.query.return_value = "0"   # *ESR? = 0 → no error bits
    s._w("CH1:SCAle 1.0")
    s._inst.write.assert_called_once_with("CH1:SCAle 1.0")
    s._inst.query.assert_called_once_with("*ESR?")   # portable status query


def test_serial_write_raises_on_scpi_error():
    s = _scope(is_serial=True)
    s._inst.query.return_value = "16"   # *ESR? execution-error bit set
    with pytest.raises(RuntimeError, match="Tek SCPI error"):
        s._w("CH1:SCAle 99999")


def test_serial_write_swallows_garbled_error_queue():
    # A garbled / unparseable status read must NOT become a failure.
    s = _scope(is_serial=True)
    s._inst.query.return_value = "garbage~~"
    s._w("CH1:SCAle 1.0")   # must not raise


def test_serial_write_ignores_non_error_esr_bits():
    # OPC (bit 0) / PON (bit 7) are NOT error bits → no raise.
    s = _scope(is_serial=True)
    s._inst.query.return_value = "129"   # PON(128) | OPC(1), no error bits
    s._w("CH1:SCAle 1.0")   # must not raise


# ---- _q: validate + retry ONLY on serial ----
def test_usb_query_single_no_retry():
    s = _scope(is_serial=False)
    s._inst.query.return_value = "64"
    assert s._q("ACQuire:NUMACq?") == "64"
    assert s._inst.query.call_count == 1


def test_serial_query_returns_first_nonempty():
    s = _scope(is_serial=True)
    s._inst.query.return_value = "64"
    assert s._q("ACQuire:NUMACq?") == "64"
    assert s._inst.query.call_count == 1


def test_serial_query_retries_on_empty(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    s = _scope(is_serial=True)
    s._inst.query.side_effect = ["", "", "64"]   # empty, empty, good
    assert s._q("ACQuire:NUMACq?") == "64"
    assert s._inst.query.call_count == 3


def test_serial_query_raises_after_all_empty(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    s = _scope(is_serial=True)
    s._inst.query.return_value = ""
    with pytest.raises(RuntimeError, match="empty response"):
        s._q("ACQuire:NUMACq?")
    assert s._inst.query.call_count == 3   # _SERIAL_QUERY_RETRIES


def test_serial_query_retries_on_exception(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_: None)
    s = _scope(is_serial=True)
    s._inst.query.side_effect = [Exception("timeout"), "64"]
    assert s._q("ACQuire:NUMACq?") == "64"


# ---- serial transport tuning (defensive) ----
def test_configure_serial_transport_applies_settings():
    s = _scope(is_serial=True)
    s.serial_baud_rate = 19200
    s.serial_flow_control = "rts_cts"
    s._configure_serial_transport()
    assert s._inst.timeout >= 5000              # floored to _SERIAL_MIN_TIMEOUT_MS
    assert s._inst.query_delay == pytest.approx(0.10)
    assert s._inst.send_end is True
    assert s._inst.baud_rate == 19200


def test_serial_defaults_to_hard_flagging_both_ends():
    # Manual: no flagging → input-buffer overrun despite matched baud
    # (the TPS2014B symptom); soft flagging locks up on binary CURVe?
    # data.  Default must be RTS/CTS on the host AND
    # RS232:HARDFlagging ON on the scope.
    s = _scope(is_serial=True)
    s._configure_serial_transport()             # no operator override
    assert s._inst.flow_control is not None
    s._inst.write.assert_any_call("RS232:HARDFlagging ON")


def test_serial_explicit_non_rtscts_skips_scope_side_flagging():
    s = _scope(is_serial=True)
    s.serial_flow_control = "xon_xoff"          # operator override
    s._configure_serial_transport()
    for call in s._inst.write.call_args_list:
        assert "HARDFlagging" not in str(call)


def test_configure_serial_transport_survives_missing_attr():
    # A backend that rejects an attribute must NOT abort the connect.
    s = _scope(is_serial=True)

    class _Picky:
        def __setattr__(self, name, value):
            if name == "query_delay":
                raise AttributeError(name)
            object.__setattr__(self, name, value)

    s._inst = _Picky()
    s._configure_serial_transport()             # must not raise
    assert getattr(s._inst, "send_end", None) is True


# ---- Model-aware record length, NEVER pin DATa:STOP --------------------
def test_set_record_length_fixed_family_query_only_no_datastop():
    """Fixed-record families (TBS1000/B, TDS, TPS — 2500 pts): a 20 000
    request SNAPS to 2500; the SET is skipped (HORizontal:RECOrdlength is
    query-only there) and DATa:STOP is NOT pinned (operator: "I do not want
    DATa:STOP")."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._inst = MagicMock()
    s._is_serial = True
    s._log = lambda *a, **k: None
    s._track_query_latency = lambda *a, **k: None
    s._invalidate_preamble_cache = lambda *a, **k: None
    s._record_length = None
    s.info = MagicMock()
    s.info.model = "TPS2014B"
    from stimtest.hardware.tektronix_models import get_series_spec
    s._cmds = get_series_spec("TPS2014B").commands
    s._inst.query.side_effect = (
        lambda cmd: "0" if "ESR" in cmd else "2500")
    s.set_record_length(20000)   # snaps to 2500 (the only valid choice)
    sent = [str(c).upper() for c in s._inst.write.call_args_list]
    assert not any("RECORDLENGTH 2" in c for c in sent), \
        f"fixed-record family: RECOrdlength is query-only, no write: {sent}"
    assert not any("DATA:STOP" in c for c in sent), \
        f"must NOT pin DATa:STOP: {sent}"
    assert s._record_length == 2500


def test_set_record_length_tbs2000_writes_record_no_datastop():
    """Deep-memory families (TBS2000B / TBS1000C): a 20 000 request snaps to
    20000 and IS written to HORizontal:RECOrdlength (operator: "know model
    series record length choices so that the appropriate record length is
    used") — but DATa:STOP is NOT pinned."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._inst = MagicMock()
    s._is_serial = False
    s._log = lambda *a, **k: None
    s._w_checked = MagicMock()
    s._invalidate_preamble_cache = lambda *a, **k: None
    s._record_length = None
    s.info = MagicMock()
    s.info.model = "TBS2204B"
    from stimtest.hardware.tektronix_models import get_series_spec
    s._cmds = get_series_spec("TBS2204B").commands
    s._q = MagicMock(return_value="20000")
    s.set_record_length(20000)
    writes = [str(c.args[0]).upper() for c in s._w_checked.call_args_list]
    assert any("RECORDLENGTH 20000" in w for w in writes), \
        f"TBS2000 must WRITE the snapped record length: {writes}"
    assert not any("DATA:STOP" in w for w in writes), \
        f"must NOT pin DATa:STOP: {writes}"
    assert s._record_length == 20000
