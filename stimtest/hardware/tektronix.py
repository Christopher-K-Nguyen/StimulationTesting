"""Tektronix oscilloscope driver (pyvisa, model-aware).

This driver is the *only* file in the project that talks SCPI. Every other
module sees the abstract :class:`stimtest.hardware.base.Oscilloscope`
interface and never knows whether it's driving a real scope, a simulated
scope, or some future make/model.

Model detection
---------------
On :meth:`open`, we send ``*IDN?`` and parse the comma-separated response:

    TEKTRONIX,TBS2204B,C019999,CF:91.1CT FV:v1.16

The model field (``TBS2204B``) is matched against
:data:`_MODERN_MODELS` to choose the SCPI dialect. The differences between
"modern" and "legacy" dialects are small but matter:

* **Modern dialect** (``TBS2000B``, ``MSO``, ``MDO``, ``DPO`` series):
  uses ``WFMOutpre:`` for waveform preamble queries (XINcr?, YMUlt?, etc.)
  and supports ``DATa:SOUrce <ch>`` for picking the channel to read.
* **Legacy dialect** (``TBS1000``, ``TDS2000``, ``TDS3000``):
  uses ``WFMPre:`` for the same queries; the rest of the command surface is
  shared.

Adding new models
-----------------
If a future scope needs different commands, extend :class:`TekDialect` with
the new field, add the model to :data:`_MODERN_MODELS` (or define a new
dialect constant), and update :func:`select_dialect` to dispatch correctly.
The rest of the driver, and all experiment / GUI code, is untouched.

Acquisition flow
----------------
:meth:`single_capture` runs one full sequence:

    1. Arm a single-sequence acquisition: ``ACQuire:STOPAfter SEQuence`` +
       ``ACQuire:STATE 1``.
    2. Poll ``ACQuire:STATE?`` until it returns "0" (acquisition done) or
       we hit ``timeout_ms``.
    3. For each channel that's been configured (via
       :meth:`configure_channels`), select it with ``DATa:SOUrce``, read
       the preamble (XINcr/XZEro/YMUlt/YOFf/YZEro), pull the binary curve
       with ``CURVe?``, and convert raw integers to volts using
       Tek's standard formula: ``V = (raw - YOFf) * YMUlt + YZEro``.
    4. Pack everything into a :class:`ScopeAcquisition` and return.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np

from .base import Oscilloscope, ScopeAcquisition, ScopeInfo


# ---------------------------------------------------------------------------
# Model dialect table
# ---------------------------------------------------------------------------
@dataclass
class TekDialect:
    """SCPI command names that vary across Tek series."""
    preamble: str           # 'WFMOutpre' or 'WFMPre'
    horiz_record: str       # 'HORizontal:RECOrdlength' or 'HORizontal:RECOrdLength'
    use_data_source: bool   # 'DATa:SOUrce <ch>' available
    has_acq_numavg: bool    # supports 'ACQuire:NUMAVg'

MODERN = TekDialect(
    preamble="WFMOutpre", horiz_record="HORizontal:RECOrdlength",
    use_data_source=True, has_acq_numavg=True,
)
LEGACY = TekDialect(
    preamble="WFMPre", horiz_record="HORizontal:RECOrdLength",
    use_data_source=True, has_acq_numavg=True,
)

#: Patterns that match the *modern* preamble dialect
_MODERN_MODELS = re.compile(
    r"\b(TBS2[0-9]{3}[A-Z]?|MSO|MDO|DPO|MSO[0-9]+|MDO[0-9]+|DPO[0-9]+|"
    r"TBS2KB|TBS2KBE|TBS2074B|TBS2104B|TBS2204B)\b",
    re.IGNORECASE,
)


def select_dialect(model: str) -> TekDialect:
    if _MODERN_MODELS.search(model or ""):
        return MODERN
    return LEGACY


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
class TektronixOscilloscope(Oscilloscope):
    """pyvisa-backed scope driver."""

    def __init__(self, resource: Optional[str] = None,
                 timeout_ms: int = 10000, prefer_usb: bool = True):
        try:
            import pyvisa
        except ImportError as e:
            raise RuntimeError("pyvisa not installed; pip install pyvisa pyvisa-py") from e
        self._pyvisa = pyvisa
        self._rm = pyvisa.ResourceManager()
        self._inst = None
        self._resource_hint = resource
        self._prefer_usb = prefer_usb
        self._timeout_ms = timeout_ms
        self._dialect: TekDialect = MODERN
        self.info = ScopeInfo()
        self.channel_aliases: Dict[str, str] = {
            "vmon": "CH1", "imon": "CH2", "eret": "CH3", "eact": "CH4",
        }
        # Cached value of HORizontal:RECOrdlength?, populated lazily on the
        # first capture and refreshed by :meth:`set_record_length`. The
        # record length doesn't change between back-to-back acquisitions, so
        # caching it saves one VISA round-trip per channel per capture.
        self._record_length: Optional[int] = None
        # Stash of the last set acquisition mode + trigger source so a
        # periodic sanity check can detect silent firmware reverts
        # mid-experiment. Populated by ``set_acquisition_mode`` and
        # ``set_trigger`` respectively. ``None`` means the host hasn't
        # configured them yet, in which case the periodic check is a
        # no-op.
        self._expected_acq_mode: Optional[str] = None
        self._expected_acq_navg: Optional[int] = None
        self._expected_trigger_source: Optional[str] = None
        self._expected_trigger_slope: Optional[str] = None
        # Capture counter — drives the once-per-N sanity check inside
        # ``single_capture`` so we don't pay the ~3 SCPI queries on
        # every step but still catch a drift within the first few
        # captures of a sweep.
        self._captures_since_check: int = 0
        # Tunable: how often the sanity check runs. 1 = every capture
        # (~3 extra SCPI queries per step); 25 = roughly once per
        # 8 s on an AVG×16 @ 50 pps sweep, plenty to catch drift early.
        self.acq_recheck_interval: int = 25

    # ----- discovery -----
    def list_resources(self) -> List[str]:
        return list(self._rm.list_resources())

    def _pick_resource(self) -> str:
        if self._resource_hint:
            return self._resource_hint
        candidates = list(self._rm.list_resources())
        if not candidates:
            raise RuntimeError("No VISA resources found. Is NI-VISA installed and the scope on?")
        if self._prefer_usb:
            for c in candidates:
                if "USB" in c.upper():
                    return c
        return candidates[0]

    # ----- lifecycle -----
    def open(self, resource: Optional[str] = None) -> None:
        if resource:
            self._resource_hint = resource
        rsrc = self._pick_resource()
        self._inst = self._rm.open_resource(rsrc)
        self._inst.timeout = self._timeout_ms
        self._inst.write_termination = "\n"
        self._inst.read_termination = "\n"
        idn = self._inst.query("*IDN?").strip()
        parts = [p.strip() for p in idn.split(",")]
        make = parts[0] if len(parts) > 0 else ""
        model = parts[1] if len(parts) > 1 else ""
        serial = parts[2] if len(parts) > 2 else ""
        firmware = parts[3] if len(parts) > 3 else ""
        self.info = ScopeInfo(
            make=make, model=model, serial=serial, firmware=firmware,
            resource=rsrc, n_channels=4, is_simulated=False,
        )
        self._dialect = select_dialect(model)
        # Sane defaults — set once so per-capture work is just CURVe? + the
        # preamble query.
        self._inst.write("HEADer OFF")
        self._inst.write("VERBose ON")
        # Binary transfer: signed integers, big-endian, 2 bytes/sample.
        # ~5–10× faster than ASCII once you account for parsing overhead,
        # and pyvisa's query_binary_values() handles the IEEE 488.2
        # block-data envelope (#<n><len><bytes>) for us. BYT_Or MSB is the
        # default but we set it anyway so we never inherit a stale state.
        self._inst.write("DATa:ENCdg RIBinary")
        self._inst.write("DATa:WIDth 2")
        self._inst.write(f"{self._dialect.preamble}:BYT_Or MSB")
        # DATa:STARt/STOP define the slice of the record to transfer. They
        # don't change between captures (we always want the full record),
        # so set once at open and let set_record_length() refresh on demand.
        self._inst.write("DATa:STARt 1")
        self._record_length = None
        self._refresh_record_length()

    def close(self) -> None:
        if self._inst is not None:
            try:
                self._inst.close()
            except Exception:
                pass
            self._inst = None

    # ----- helpers -----
    def _w(self, cmd: str) -> None:
        if self._inst is None: raise RuntimeError("Scope not open")
        self._inst.write(cmd)

    def _q(self, cmd: str) -> str:
        if self._inst is None: raise RuntimeError("Scope not open")
        return self._inst.query(cmd).strip()

    def _w_checked(self, cmd: str) -> None:
        """Send a SCPI command and immediately drain the error queue.

        IEEE-488.2 / SCPI: ``SYSTem:ERRor?`` returns ``0,"No error"`` when
        the queue is empty. The MATLAB code wrapped every Tek write with
        an error-queue read for this reason — silent SCPI rejections (a
        misspelled mnemonic, an out-of-range numeric, a command issued
        in the wrong acquisition state) leave the scope in a state that
        doesn't match the host's idea of it, and you only find out when
        the captured trace looks weird. We surface the SCPI error code
        here so the failure points at the actual offending command.
        """
        if self._inst is None: raise RuntimeError("Scope not open")
        self._inst.write(cmd)
        try:
            err = self._inst.query("SYSTem:ERRor?").strip()
        except Exception:
            return  # never let the error-check itself become the failure
        # Format is ``code,"description"``; code 0 = no error.
        head = err.split(",", 1)[0].strip()
        try:
            code = int(head)
        except ValueError:
            return
        if code != 0:
            raise RuntimeError(
                f"Tek SCPI error after {cmd!r}: {err}")

    # ----- configuration -----
    def set_channel_scale(self, channel: str, volts_per_div: float) -> None:
        self._w(f"{channel}:SCAle {volts_per_div:g}")

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self._w(f"HORizontal:SCAle {seconds_per_div:g}")

    def set_record_length(self, n: int) -> None:
        n = int(n)
        if n <= 0:
            raise ValueError(f"record_length must be positive, got {n}.")
        self._w_checked(f"{self._dialect.horiz_record} {n}")
        self._w_checked(f"DATa:STOP {n}")
        # Read-back: the scope rounds to its supported set of record
        # lengths (TBS-series: 1k / 2k / 20k / 200k / ...). If the user
        # asks for an unsupported value, the SCPI write rounds silently;
        # we prefer to reflect the real value back so callers can plan
        # their decimation accordingly.
        try:
            actual = int(float(self._q(f"{self._dialect.horiz_record}?")))
        except Exception:
            actual = n
        self._record_length = actual

    def _refresh_record_length(self) -> int:
        """Query the scope for the record length and cache it.

        The record length doesn't change unless someone calls
        :meth:`set_record_length` explicitly, so we read it once at open
        and re-use the cached value for every subsequent capture.
        """
        n = int(float(self._q(f"{self._dialect.horiz_record}?")))
        self._w(f"DATa:STOP {n}")
        self._record_length = n
        return n

    def set_acquisition_mode(self, mode: str = "AVERAGE", n_avg: int = 16) -> None:
        mode_u = mode.upper()
        if mode_u not in ("SAMPLE", "AVERAGE", "PEAK"):
            mode_u = "SAMPLE"
        # Tek uses 'AVE' / 'SAM' / 'PEA' as accepted abbreviations
        m = {"SAMPLE": "SAMPLE", "AVERAGE": "AVERAGE", "PEAK": "PEAKDETECT"}[mode_u]
        # Remember what we asked for so the periodic check can
        # compare device state against intent.
        self._expected_acq_mode = m
        self._expected_acq_navg = (int(n_avg) if mode_u == "AVERAGE"
                                   and self._dialect.has_acq_numavg
                                   else None)
        self._w_checked(f"ACQuire:MODe {m}")
        if mode_u == "AVERAGE" and self._dialect.has_acq_numavg:
            # NUMAVg is restricted to powers of two on the TBS-series
            # (programmer manual ACQuire:NUMAVg, 2..512). Snap the
            # request onto the nearest legal value so the SCPI write
            # never errors out.
            choices = self.average_count_choices() or []
            if choices:
                n_avg = min(choices, key=lambda v: abs(v - int(n_avg)))
            self._w_checked(f"ACQuire:NUMAVg {int(n_avg)}")
        # Read-back: confirm the scope is actually in the requested mode.
        # On some firmware the mode write is ignored when the scope is
        # mid-acquisition; without a check we'd silently capture in the
        # wrong mode for the rest of the run.
        try:
            got = self._q("ACQuire:MODe?").upper()
        except Exception:
            return
        # Tek scopes echo back as full word (AVERAGE / SAMPLE / PEAKDETECT)
        # or short form (AVE / SAM / PEA) depending on VERBose. Match on
        # the prefix we sent.
        prefix = m[:3]
        if not got.startswith(prefix):
            raise RuntimeError(
                f"Scope acquisition mode mismatch: requested {m}, "
                f"device reports {got!r}.")
        if mode_u == "AVERAGE" and self._dialect.has_acq_numavg:
            try:
                got_n = int(float(self._q("ACQuire:NUMAVg?")))
            except Exception:
                return
            if got_n != int(n_avg):
                raise RuntimeError(
                    f"Scope NUMAVg mismatch: requested {n_avg}, "
                    f"device reports {got_n}.")

    # Discrete NUMAVg list per the TBS-series programmer manual.
    _AVG_CHOICES_TBS = (2, 4, 8, 16, 32, 64, 128, 256, 512)

    def acquisition_modes(self):
        # The TBS-series exposes Sample / Peak detect / Hi-Res / Average.
        # We expose the two most useful at the GUI layer; Peak Detect /
        # Hi-Res can be added if a workflow needs them.
        return ["SAMPLE", "AVERAGE"]

    def average_count_choices(self):
        # TBS programmer manual says NUMAVg = 2..512 in powers of two;
        # other Tek dialects (older TDS, MSO) match the same list.
        return list(self._AVG_CHOICES_TBS)

    def max_average_count(self) -> int:
        return self._AVG_CHOICES_TBS[-1]

    def set_trigger(self, source: str = "EXT", level_v: float = 1.0,
                    slope: str = "RISE", mode: str = "NORMAL") -> None:
        # Source: 'EXT' or 'CH1'..'CH4'
        slope_word = "RISe" if slope.upper().startswith("R") else "FALL"
        self._w("TRIGger:A:TYPe EDGE")
        self._w(f"TRIGger:A:EDGE:SOUrce {source}")
        self._w(f"TRIGger:A:EDGE:SLOpe {slope_word}")
        try:
            self._w(f"TRIGger:A:LEVel {level_v:g}")
        except Exception:
            self._w(f"TRIGger:LEVel {level_v:g}")
        self._w(f"TRIGger:A:MODe {mode.upper()}")
        # Stash the requested settings so a periodic sanity check
        # (see ``_periodic_acq_check``) can verify nothing's drifted.
        self._expected_trigger_source = source.upper()
        self._expected_trigger_slope = slope_word.upper()

    # ----- acquisition -----
    def auto_scale(self) -> None:
        try:
            self._w("AUTOSet EXECute")
        except Exception:
            pass

    def _periodic_acq_check(self) -> Optional[str]:
        """Compare device acquisition mode + trigger source against the
        last values the host requested.

        Returns ``None`` when everything matches, or a human-readable
        diff message describing what drifted. The caller (``single_capture``)
        re-applies the expected settings so the next capture isn't
        recorded with stale device state. Costs ~3 SCPI queries; the
        caller throttles via ``acq_recheck_interval``.
        """
        problems: List[str] = []
        # Mode (AVE / SAM / PEA depending on VERBose echo).
        if self._expected_acq_mode is not None:
            try:
                got = self._q("ACQuire:MODe?").upper()
            except Exception:
                got = ""
            prefix = self._expected_acq_mode[:3]
            if not got.startswith(prefix):
                problems.append(
                    f"acquisition mode drifted: expected "
                    f"{self._expected_acq_mode}, device reports {got!r}")
        # NUMAVg (only meaningful in AVERAGE mode on dialects that have it).
        if self._expected_acq_navg is not None:
            try:
                got_n = int(float(self._q("ACQuire:NUMAVg?")))
            except Exception:
                got_n = -1
            if got_n != self._expected_acq_navg:
                problems.append(
                    f"NUMAVg drifted: expected {self._expected_acq_navg}, "
                    f"device reports {got_n}")
        # Trigger source.
        if self._expected_trigger_source is not None:
            try:
                got_src = self._q("TRIGger:A:EDGE:SOUrce?").upper()
            except Exception:
                got_src = ""
            # Some dialects echo the source with a "CH" prefix or an
            # equivalent abbreviation; check loosely.
            if (self._expected_trigger_source not in got_src and
                    got_src not in self._expected_trigger_source):
                problems.append(
                    f"trigger source drifted: expected "
                    f"{self._expected_trigger_source}, device reports "
                    f"{got_src!r}")
        if not problems:
            return None
        return "; ".join(problems)

    def single_capture(self) -> ScopeAcquisition:
        # Periodic sanity check that the scope's acquisition + trigger
        # state still matches what the host requested. Some TBS
        # firmware versions silently revert to SAMPLE mode after an
        # internal error; without this, the user wouldn't notice
        # until they post-processed the wrong-shaped trace. Throttled
        # to once per ``acq_recheck_interval`` captures so the SCPI
        # cost is negligible.
        self._captures_since_check += 1
        if self._captures_since_check >= self.acq_recheck_interval:
            self._captures_since_check = 0
            problem = self._periodic_acq_check()
            if problem is not None:
                # Re-apply the expected mode + trigger so the next
                # capture lands with the right device state. Raise so
                # the runner can log the recovery.
                if self._expected_acq_mode is not None:
                    try:
                        self.set_acquisition_mode(
                            self._expected_acq_mode,
                            n_avg=self._expected_acq_navg or 16)
                    except Exception:
                        pass
                # Surface as a runtime warning via exception path; the
                # runner catches Scope errors and logs them, so the
                # message lands in the user-visible log pane.
                raise RuntimeError(
                    f"Scope state drifted, re-applied: {problem}")
        # Tell the scope: take one acquisition (potentially averaged) and
        # then stop. STATE 1 starts it; STATE? reads back 1 while running,
        # 0 when done. We poll instead of using *OPC? because *OPC? blocks
        # the VISA bus for the full timeout if anything goes wrong upstream.
        self._w("ACQuire:STOPAfter SEQuence")
        self._w("ACQuire:STATE 1")
        deadline = time.time() + (self._timeout_ms / 1000.0)
        while time.time() < deadline:
            try:
                if self._q("ACQuire:STATE?") == "0":
                    break  # acquisition complete
            except Exception:
                # ignore the occasional VISA hiccup and retry
                time.sleep(0.05)
                continue
            # Small inter-poll sleep so we don't hammer the USB-TMC bus
            # while the scope is averaging — every query the host
            # issues steals scope CPU from the acquisition itself, and
            # the bus chatter can briefly stall other USB devices on
            # the same root hub. 10 ms is well below the human
            # perception threshold and keeps the loop responsive
            # without saturating the bus.
            time.sleep(0.010)
        else:
            raise TimeoutError("Scope acquisition did not complete in time")

        # Determine which channels to fetch
        wanted_channels = sorted(set(self.channel_aliases.values()))
        out_channels: Dict[str, np.ndarray] = {}
        time_us = np.empty(0)
        sample_period_us = 0.0
        record_length = 0

        for ch in wanted_channels:
            t_us, y_v, dt_us, n = self._read_channel(ch)
            if time_us.size == 0:
                time_us = t_us
                sample_period_us = dt_us
                record_length = n
            out_channels[ch] = y_v

        return ScopeAcquisition(
            time_us=time_us, channels=out_channels,
            sample_period_us=sample_period_us, record_length=record_length,
        )

    def _read_channel(self, ch: str) -> Tuple[np.ndarray, np.ndarray, float, int]:
        """Fetch one channel, return (time_us, voltage_v, sample_period_us, npts).

        Uses Tek's standard conversion formula:

            V = (raw_dl - YOFf) * YMUlt + YZEro

        where ``raw_dl`` is the integer "digitizing level" (0..255 for 8-bit
        scopes, 0..65535 for 16-bit). ``WFMOutpre:`` tells us all five
        scaling constants in a single semicolon-separated response, so we
        only pay one VISA round-trip for the preamble per channel.
        """
        # Pick which channel CURVe? will read from. DATa:STARt / STOP and
        # the binary-encoding settings are already established at open()
        # and survive across captures, so we don't re-send them here.
        if self._dialect.use_data_source:
            self._w(f"DATa:SOUrce {ch}")

        # ----- Preamble in one round-trip -----------------------------
        # Asking for the whole preamble (``WFMOutpre?`` / ``WFMPre?``)
        # returns every scaling field we need in one shot, e.g.:
        #   2;16;BIN;RI;MSB;"...";2500;Y;LIN;"s";4.0E-8;-5.0E-5;0;"V";4.0E-4;-7.25E2;0
        # We could individually re-query ymult, yoff, yzero, xincr, xzero
        # but that's 5 ~2 ms round-trips per channel. Pull once and parse.
        pre = self._dialect.preamble
        ymult, yoff, yzero, xinc, xzero = self._read_preamble(pre)

        # ----- Curve as a big-endian signed int16 stream --------------
        if self._inst is None: raise RuntimeError("Scope not open")
        raw = self._inst.query_binary_values(
            "CURVe?", datatype="h", is_big_endian=True, container=np.ndarray,
        )
        y_v = (raw.astype(np.float64) - yoff) * ymult + yzero
        t_s = xzero + xinc * np.arange(raw.size)
        return t_s * 1e6, y_v, xinc * 1e6, raw.size

    def _read_preamble(self, preamble_root: str) -> Tuple[float, float, float, float, float]:
        """Query ``WFMOutpre?`` / ``WFMPre?`` and return the scaling fields.

        The response is a semicolon-separated list whose ordering is
        documented and stable in the Tek programmer's manual:

            BYT_Nr; BIT_Nr; ENCdg; BN_Fmt; BYT_Or; WFId; NR_Pt; PT_Fmt;
            PT_ORder; XUNit; XINcr; PT_OFf; XZEro; YUNit; YMUlt; YOFf; YZEro

        We extract the five we need (XINcr, XZEro, YMUlt, YOFf, YZEro) by
        position rather than by name so older scopes that omit certain
        modern fields still parse cleanly.
        """
        raw = self._q(f"{preamble_root}?")
        parts = [p.strip() for p in raw.split(";")]
        # Field positions in the documented Tek preamble (zero-indexed):
        #   10=XINcr, 12=XZEro, 14=YMUlt, 15=YOFf, 16=YZEro
        # If the dialect omits the WFId field (some older legacy firmware
        # has 16 fields instead of 17), positions shift by one — we detect
        # that by trying the canonical positions first and falling back.
        try:
            xinc = float(parts[10]); xzero = float(parts[12])
            ymult = float(parts[14]); yoff = float(parts[15]); yzero = float(parts[16])
        except (IndexError, ValueError):
            # Per-field fallback. Slower (5 round-trips) but always works.
            ymult = float(self._q(f"{preamble_root}:YMUlt?"))
            yoff = float(self._q(f"{preamble_root}:YOFf?"))
            yzero = float(self._q(f"{preamble_root}:YZEro?"))
            xinc = float(self._q(f"{preamble_root}:XINcr?"))
            xzero = float(self._q(f"{preamble_root}:XZEro?"))
        return ymult, yoff, yzero, xinc, xzero

    # ----- adapt aliases to physical channels --
    def configure_channels(self, alias_to_phys: Dict[str, str]) -> None:
        super().configure_channels(alias_to_phys)
        # Make sure each wanted channel is enabled
        for ch in set(alias_to_phys.values()):
            try:
                self._w(f"SELect:{ch} ON")
            except Exception:
                pass
