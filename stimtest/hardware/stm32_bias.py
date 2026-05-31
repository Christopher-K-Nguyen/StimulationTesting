"""STM32G474RE Nucleo-64 bias-module driver (IPB-001 firmware).

Speaks the protocol documented in ``stm32_bias_protocol.md``
(sibling file) — single source of truth for both this driver and
the matching STM32 firmware.

Transport: USB CDC ACM (ST-LINK virtual COM port) at 115200 8N1.
``pyserial`` (``serial.Serial``) is the underlying I/O layer.

See also
--------
:class:`stimtest.hardware.bias_module.BiasModule` — the abstract
base this implements.
``stm32_bias_protocol.md`` — wire protocol.
"""
from __future__ import annotations

import logging
import re
import time
from typing import List, Optional, Tuple

import numpy as np

from .base import fmt_elapsed
from .bias_module import (
    BIAS_LOG_DTYPE, BiasLogReadout, BiasModule, BiasModuleInfo,
)

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Baud rate per protocol §1.
_BAUD = 115_200

#: USB IDs the Nucleo's ST-LINK presents (per ST docs).  ``auto_discover``
#: prefers ports matching these; non-match is non-fatal (operator can
#: pick a port manually from the connector UI).
_ST_VID = 0x0483
_ST_PIDS = (0x374B,)  # ST-LINK/V2.1 with VCP.  Add new ST-LINK revisions here.

#: Time the driver gives the firmware to respond to ``*IDN?`` after
#: opening the port (USB CDC DTR toggle soft-resets the STM32; firmware
#: needs ~500 ms to re-init per the protocol's "Reset behaviour" note).
_OPEN_IDN_TIMEOUT_S = 1.0

#: Default response timeout for any single command.  The 65535-byte
#: maximum response size at 115200 baud takes ~5.7 s in the worst case;
#: 10 s gives headroom while still catching genuinely hung firmware.
_DEFAULT_RESPONSE_TIMEOUT_S = 10.0

#: Supported firmware version range.  Driver refuses to ``open()`` against
#: firmware outside this band so a forward/backward incompatibility surfaces
#: as a clean error rather than a mysterious protocol mismatch.
_SUPPORTED_FW_MIN = (0, 1, 0)
_SUPPORTED_FW_MAX = (1, 0, 0)  # exclusive upper bound

#: Header line we expect from ``LOG:DATA?``.  Used to anchor the CSV
#: parser; firmware MUST emit this exact string.
_LOG_DATA_HEADER = "t_us,bias_v,bias_a,elec_v"

#: Terminator line on ``LOG:DATA?`` response.
_LOG_DATA_END = "END"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IDN_RE = re.compile(
    r"^(?P<mfr>[^,]+),(?P<model>[^,]+),(?P<hwid>[^,]+),fw=(?P<fw>[0-9.]+)\s*$"
)

_FW_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def _parse_fw(fw: str) -> Tuple[int, int, int]:
    """Parse ``"0.1.0"`` to ``(0, 1, 0)``.  Raises on malformed."""
    m = _FW_RE.match(fw.strip())
    if not m:
        raise ValueError(f"Unparseable firmware version {fw!r}")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _fw_in_range(fw: Tuple[int, int, int],
                 lo: Tuple[int, int, int],
                 hi_exclusive: Tuple[int, int, int]) -> bool:
    return lo <= fw < hi_exclusive


def auto_discover() -> List[str]:
    """Enumerate likely STM32 bias-module COM ports on this machine.

    Returns a list of port names ordered by preference:

    1. Ports whose USB descriptor matches the ST-LINK VID/PID first.
    2. Other USB CDC ports (anything with ``USB`` in the description)
       next, so an alternative dev-board (e.g., a custom adapter) still
       shows up for manual selection.
    3. Everything else (legacy COM, Bluetooth virtual ports) last.

    Does NOT open any port — descriptor inspection only, safe to call
    repeatedly from a polling timer.

    Returns
    -------
    list[str]
        Port names (e.g. ``["COM5", "COM3"]`` on Windows,
        ``["/dev/ttyACM0", ...]`` on Linux).  Empty list if pyserial
        isn't installed or no ports are present.
    """
    try:
        from serial.tools import list_ports
    except ImportError:
        _log.warning(
            "pyserial not installed; STM32 bias module auto-discover disabled. "
            "Install with: pip install pyserial>=3.5")
        return []

    st_links: List[str] = []
    other_usb: List[str] = []
    others: List[str] = []
    for p in list_ports.comports():
        vid = getattr(p, "vid", None)
        pid = getattr(p, "pid", None)
        if vid == _ST_VID and pid in _ST_PIDS:
            st_links.append(p.device)
        elif "USB" in (p.description or "").upper():
            other_usb.append(p.device)
        else:
            others.append(p.device)
    return st_links + other_usb + others


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class STM32BiasModule(BiasModule):
    """Concrete bias-module driver speaking the v1.0 STM32 protocol.

    Constructor takes a serial port (e.g., ``"COM5"``).  Call
    :meth:`open` to actually open the port, query ``*IDN?``, and
    populate :attr:`info`.

    Example::

        ports = auto_discover()
        bias = STM32BiasModule(port=ports[0])
        bias.open()
        try:
            bias.reset()
            bias.set_bias_voltage(0.25)
            bias.set_bias_enabled(True)
            bias.start_log()
            # ... run experiment ...
            bias.stop_log()
            readout = bias.read_log()
        finally:
            bias.close()
    """

    def __init__(self, *, port: str,
                 response_timeout_s: float = _DEFAULT_RESPONSE_TIMEOUT_S,
                 cmd_logger=None):
        self._port = port
        self._response_timeout_s = float(response_timeout_s)
        self._serial = None  # set in open()
        self.info: BiasModuleInfo = BiasModuleInfo(port=port)

        #: Optional per-command logger (callable taking one string).
        #: Mirrors the ``cmd_logger`` hook on :class:`Stimulator` and
        #: :class:`Oscilloscope` so the GUI can pipe traffic into the
        #: LogPane for debugging.  ``None`` means no per-command log
        #: lines (only INFO/WARNING/ERROR via the module's ``_log``).
        self.cmd_logger = cmd_logger

    # ============================================================ lifecycle

    def open(self) -> None:
        """Open the serial port, soft-reset-tolerant ``*IDN?`` handshake."""
        try:
            import serial  # pyserial
        except ImportError as e:
            raise RuntimeError(
                "pyserial is not installed; cannot open STM32 bias module. "
                "Install with: pip install pyserial>=3.5"
            ) from e

        if self._serial is not None and self._serial.is_open:
            _log.debug("STM32BiasModule.open: port already open, skipping")
            return

        # Open with a short per-byte read timeout — actual response
        # framing is driven by line termination (\n), so the timeout
        # is just the inter-byte stall guard.
        self._serial = serial.Serial(
            port=self._port,
            baudrate=_BAUD,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.5,           # per-byte read timeout
            write_timeout=2.0,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        self._log_cmd(f"[bias] open {self._port} @ {_BAUD} baud")

        # Drain any stale bytes from a previous session (some USB stacks
        # buffer unread data even after the previous handle closes).
        try:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
        except Exception:
            pass

        # Handshake — query *IDN? with a longer-than-usual timeout to
        # cover the USB-CDC DTR soft-reset that some hosts trigger
        # when the port opens.  Per protocol §1, firmware must be ready
        # within 500 ms; we give it 1000 ms.
        t0 = time.monotonic()
        idn = ""
        while time.monotonic() - t0 < _OPEN_IDN_TIMEOUT_S:
            try:
                idn = self._query("*IDN?", timeout_s=0.5)
                if idn:
                    break
            except (TimeoutError, OSError):
                continue
        if not idn:
            self.close()
            raise RuntimeError(
                f"STM32 bias module on {self._port} did not respond to "
                f"*IDN? within {_OPEN_IDN_TIMEOUT_S} s.  Check the cable, "
                f"verify the right COM port, and confirm IPB firmware is "
                f"loaded.")

        m = _IDN_RE.match(idn)
        if not m:
            self.close()
            raise RuntimeError(
                f"Unexpected *IDN? response: {idn!r}.  Expected format: "
                f"<mfr>,<model>,<hwid>,fw=<X.Y.Z>.  Is the right firmware "
                f"loaded on this Nucleo?")

        fw = _parse_fw(m.group("fw"))
        if not _fw_in_range(fw, _SUPPORTED_FW_MIN, _SUPPORTED_FW_MAX):
            self.close()
            raise RuntimeError(
                f"STM32 bias module firmware {m.group('fw')!r} is outside "
                f"the supported range "
                f"[{_SUPPORTED_FW_MIN[0]}.{_SUPPORTED_FW_MIN[1]}.{_SUPPORTED_FW_MIN[2]}, "
                f"{_SUPPORTED_FW_MAX[0]}.{_SUPPORTED_FW_MAX[1]}.{_SUPPORTED_FW_MAX[2]}).  "
                f"Update the firmware or upgrade PULSAR.")

        # Query log buffer capacity so callers can plan how often to
        # drain.  Failure here is non-fatal — fall back to 0 and let
        # downstream code treat it as "capacity unknown, drain often".
        try:
            cap_str = self._query("LOG:CAPacity?")
            log_cap = int(cap_str)
        except (ValueError, TimeoutError, OSError) as e:
            _log.warning("LOG:CAPacity? failed (%s); proceeding with 0", e)
            log_cap = 0

        self.info = BiasModuleInfo(
            manufacturer=m.group("mfr"),
            model=m.group("model"),
            hardware_id=m.group("hwid"),
            firmware=m.group("fw"),
            port=self._port,
            log_capacity=log_cap,
            is_simulated=False,
        )
        self._log_cmd(
            f"[bias] connected: {self.info.manufacturer} {self.info.model} "
            f"{self.info.hardware_id} fw={self.info.firmware} "
            f"log_capacity={self.info.log_capacity}")

        # Drain any startup errors so downstream pop_error() calls
        # start from a clean queue.
        startup_errs = self.drain_errors()
        for code, msg in startup_errs:
            _log.warning("STM32 bias startup error: %d %s", code, msg)

    def close(self) -> None:
        """Send ``*RST`` (best-effort) and close the serial port."""
        if self._serial is None:
            return
        try:
            if self._serial.is_open:
                # Best-effort reset; ignore failures since we're closing anyway.
                try:
                    self._send("*RST")
                except Exception:
                    pass
                self._serial.close()
                self._log_cmd(f"[bias] close {self._port}")
        finally:
            self._serial = None

    def reset(self) -> None:
        self._command("*RST")

    # ============================================================ bias config

    def set_bias_voltage(self, v: float) -> None:
        self._command(f"BIAS:VOLTage {v:.6g}")

    def get_bias_voltage(self) -> float:
        return float(self._query("BIAS:VOLTage?"))

    def set_bias_enabled(self, enabled: bool) -> None:
        self._command(f"BIAS:ENABle {'ON' if enabled else 'OFF'}")

    def get_bias_enabled(self) -> bool:
        return self._query("BIAS:ENABle?").strip() in ("1", "ON")

    def set_bias_holdoff_us(self, us: float) -> None:
        self._command(f"BIAS:HOLDoff {int(us)}")

    def set_compliance_current(self, amps: float) -> None:
        self._command(f"BIAS:CURRent:LIMit {amps:.6g}")

    # ============================================================ trigger

    def set_trigger_polarity(self, polarity: str) -> None:
        p = polarity.strip().upper()
        if p not in ("LOW", "HIGH"):
            raise ValueError(
                f"polarity must be 'LOW' or 'HIGH', got {polarity!r}")
        self._command(f"TRIGger:POLarity {p}")

    def set_trigger_source(self, source: str) -> None:
        s = source.strip().upper()
        if s not in ("GPIO", "MAN", "CONT"):
            raise ValueError(
                f"source must be 'GPIO', 'MAN', or 'CONT', got {source!r}")
        self._command(f"TRIGger:SOURce {s}")

    def set_trigger_watchdog_ms(self, ms: float) -> None:
        self._command(f"TRIGger:WATCHdog {int(ms)}")

    # ============================================================ measure (snapshot)

    def read_bias_voltage(self) -> float:
        return float(self._query("MEASure:VOLTage?"))

    def read_bias_current(self) -> float:
        return float(self._query("MEASure:CURRent?"))

    def read_electrode_potential(self) -> float:
        s = self._query("MEASure:ELECtrode?").strip().upper()
        if s == "NAN":
            return float("nan")
        return float(s)

    def read_bias_state(self) -> bool:
        return self._query("MEASure:STATe?").strip() in ("1", "ON")

    # ============================================================ buffered log

    def set_log_rate_hz(self, hz: float) -> None:
        self._command(f"LOG:RATE {int(hz)}")

    def get_log_capacity(self) -> int:
        return int(self._query("LOG:CAPacity?"))

    def clear_log(self) -> None:
        self._command("LOG:CLEar")

    def start_log(self) -> None:
        self._command("LOG:STARt")

    def stop_log(self) -> None:
        self._command("LOG:STOP")

    def get_log_state(self) -> str:
        return self._query("LOG:STATe?").strip().upper()

    def get_log_points(self) -> int:
        return int(self._query("LOG:POINts?"))

    def get_log_overflow(self) -> bool:
        return self._query("LOG:OVERflow?").strip() in ("1", "ON")

    def read_log(self) -> BiasLogReadout:
        """Drain the firmware buffer via ``LOG:DATA?``.

        Parses the multi-line CSV block, terminated by ``END\\n``,
        into a structured ``np.ndarray`` with dtype
        :data:`BIAS_LOG_DTYPE`.  Returns a :class:`BiasLogReadout`
        carrying samples + overflow flag + active sample rate.

        Raises
        ------
        TimeoutError
            If the response exceeds 65 535 bytes without an ``END\\n``
            terminator (firmware bug or wire corruption).
        RuntimeError
            If the response header doesn't match the expected
            ``t_us,bias_v,bias_a,elec_v`` line.
        """
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("STM32 bias module not open")

        # Cache the rate at readout time so the returned object is
        # self-describing (caller can rebuild the time axis without
        # a follow-up query).
        rate_hz = float(self._query("LOG:RATE?"))
        # Query overflow BEFORE LOG:DATA? — once we drain, the
        # overflow latch might or might not reset depending on
        # firmware; reading it first is the conservative choice.
        overflowed = self.get_log_overflow()

        # Send the data query and stream-parse the response line by line.
        self._send("LOG:DATA?")
        lines: List[str] = []
        deadline = time.monotonic() + self._response_timeout_s
        bytes_read = 0
        while time.monotonic() < deadline:
            line = self._readline()
            if line is None:
                continue
            bytes_read += len(line) + 1  # +1 for the LF we stripped
            if bytes_read > 65_535:
                raise TimeoutError(
                    f"LOG:DATA? response exceeded 65 535 bytes without "
                    f"END terminator; firmware bug or wire corruption")
            stripped = line.strip()
            if stripped == _LOG_DATA_END:
                break
            lines.append(stripped)
        else:
            raise TimeoutError(
                f"LOG:DATA? did not return END within "
                f"{self._response_timeout_s:.1f} s")

        if not lines:
            # Empty buffer (clear-then-immediate-drain).  Return an
            # empty structured array of the right dtype.
            samples = np.empty((0,), dtype=BIAS_LOG_DTYPE)
            return BiasLogReadout(
                samples=samples, n_points=0,
                overflowed=overflowed, sample_rate_hz=rate_hz)

        header = lines[0]
        if header != _LOG_DATA_HEADER:
            raise RuntimeError(
                f"LOG:DATA? header mismatch: expected "
                f"{_LOG_DATA_HEADER!r}, got {header!r}")

        body = lines[1:]
        n = len(body)
        samples = np.empty((n,), dtype=BIAS_LOG_DTYPE)
        for i, row in enumerate(body):
            parts = row.split(",")
            if len(parts) != 4:
                raise RuntimeError(
                    f"LOG:DATA? row {i} malformed: {row!r} (expected 4 "
                    f"comma-separated fields)")
            try:
                samples[i] = (
                    float(parts[0]),
                    float(parts[1]),
                    float(parts[2]),
                    float("nan") if parts[3].upper() == "NAN" else float(parts[3]),
                )
            except ValueError as e:
                raise RuntimeError(
                    f"LOG:DATA? row {i} unparseable: {row!r} ({e})")

        return BiasLogReadout(
            samples=samples, n_points=n,
            overflowed=overflowed, sample_rate_hz=rate_hz)

    # ============================================================ error queue

    def pop_error(self) -> Tuple[int, str]:
        resp = self._query("SYSTem:ERRor?")
        # Response format: <code>,"<msg>"
        m = re.match(r"^\s*(-?\d+)\s*,\s*\"(.*)\"\s*$", resp)
        if not m:
            # Malformed — surface as a synthetic error rather than raising,
            # so a bad response can't crash a polling drain.
            return (-999, f"Malformed SYSTem:ERRor? response: {resp!r}")
        return (int(m.group(1)), m.group(2))

    # ============================================================ low-level I/O

    def _send(self, cmd: str) -> None:
        """Write a command line + ``\\n`` to the wire.  Does not read response."""
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("STM32 bias module not open")
        self._log_cmd(f"[bias] > {cmd}")
        self._serial.write((cmd + "\n").encode("ascii"))
        self._serial.flush()

    def _readline(self, timeout_s: Optional[float] = None) -> Optional[str]:
        """Read one ``\\n``-terminated line.  Returns the line WITHOUT
        the trailing newline, or ``None`` on timeout.

        Uses the pre-set per-byte timeout on the serial object; the
        ``timeout_s`` arg is the upper bound on total wait time before
        we give up and return ``None``.
        """
        if self._serial is None or not self._serial.is_open:
            raise RuntimeError("STM32 bias module not open")
        deadline = time.monotonic() + (
            timeout_s if timeout_s is not None else self._response_timeout_s)
        buf = bytearray()
        while time.monotonic() < deadline:
            byte = self._serial.read(1)
            if not byte:
                continue
            if byte == b"\n":
                return buf.decode("ascii", errors="replace")
            buf.extend(byte)
        return None

    def _query(self, cmd: str, *, timeout_s: Optional[float] = None) -> str:
        """Send a query and return the response line (without trailing ``\\n``)."""
        t0 = time.monotonic()
        self._send(cmd)
        line = self._readline(timeout_s=timeout_s)
        elapsed = time.monotonic() - t0
        if line is None:
            raise TimeoutError(
                f"No response to {cmd!r} within "
                f"{timeout_s if timeout_s is not None else self._response_timeout_s:.1f} s")
        self._log_cmd(f"[bias] < {line}  ({fmt_elapsed(elapsed)})")
        return line

    def _command(self, cmd: str) -> None:
        """Send a setter and verify the ``OK\\n`` response.  Raises on ``ERR``."""
        resp = self._query(cmd).strip().upper()
        if resp == "OK":
            return
        if resp == "ERR":
            # Drain the error queue and surface in the exception so the
            # operator sees what went wrong rather than just "ERR".
            errs = self.drain_errors()
            err_str = "; ".join(f"{c} {m}" for c, m in errs) if errs else "(empty queue)"
            raise RuntimeError(
                f"STM32 bias module rejected {cmd!r}: {err_str}")
        raise RuntimeError(
            f"STM32 bias module returned unexpected response to {cmd!r}: "
            f"{resp!r} (expected OK or ERR)")

    def _log_cmd(self, msg: str) -> None:
        """Forward to the optional ``cmd_logger`` callable, if set."""
        if self.cmd_logger is None:
            return
        try:
            self.cmd_logger(msg)
        except Exception:
            # Logger crashes must not propagate up the I/O path.
            _log.exception("cmd_logger raised")
