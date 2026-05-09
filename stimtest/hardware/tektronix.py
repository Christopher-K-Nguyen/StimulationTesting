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

#: Patterns that match the *modern* preamble dialect.
#:
#: Includes TBS1000C and TBS2000B/C families — both released after Tek
#: unified the basic-scope SCPI command set (WFMOutpre, the modern
#: HORizontal:POSition percent form, CH<x>:PRObe:GAIN). The older
#: TBS1000 / TBS1000B / TBS1000B-EDU still use WFMPre — leave them on
#: the LEGACY branch.
_MODERN_MODELS = re.compile(
    r"\b("
    r"TBS1[0-9]{3}C|"           # TBS1052C / TBS1072C / TBS1102C / etc.
    r"TBS2[0-9]{3}[A-Z]?|"      # TBS2074B / TBS2104B / TBS2204B / TBS2K-C
    r"MSO|MDO|DPO|"
    r"MSO[0-9]+|MDO[0-9]+|DPO[0-9]+|"
    r"TBS2KB|TBS2KBE|TBS2074B|TBS2104B|TBS2204B"
    r")\b",
    re.IGNORECASE,
)


def select_dialect(model: str) -> TekDialect:
    if _MODERN_MODELS.search(model or ""):
        return MODERN
    return LEGACY


def channel_count_from_model(model: str) -> int:
    """Infer the number of input channels from a Tek model number.

    Tek's basic-scope naming convention is ``TBS<family><BW><n_ch><suffix>``
    where the digit just before the optional letter suffix encodes the
    channel count. Examples:

    * ``TBS1052C`` → 50 MHz, 2-channel
    * ``TBS1072C`` → 70 MHz, 2-channel
    * ``TBS1102C`` → 100 MHz, 2-channel
    * ``TBS2074B`` → 70 MHz, 4-channel
    * ``TBS2204B`` → 200 MHz, 4-channel

    Returns 4 when the model isn't recognised — the safe default for a
    DSO and what callers historically assumed before this helper
    existed. MSO/MDO/DPO models also return 4 (the typical
    configuration; channel count for those should really be queried
    via ``CH:LIST?`` or similar but isn't exposed by ``*IDN?``).
    """
    m = re.search(r"TBS[12]\d{3}([A-Z])?", model or "", re.IGNORECASE)
    if m:
        # The 4-digit numeric part: e.g. "1072" → channel digit is the
        # last one ('2'). For 4-digit BW like "1102" (representing
        # 100 MHz / 2-channel), it's still the last digit.
        digits = re.search(r"TBS[12](\d{3})", model, re.IGNORECASE)
        if digits:
            ch_digit = digits.group(1)[-1]
            try:
                n = int(ch_digit)
                if n in (2, 4):
                    return n
            except ValueError:
                pass
    return 4


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
#: Default record length applied to every scope on connect.
#: Sweet spot for stim-pulse characterization on the TBS2204B
#: (and TBS-series in general), where the supported quantized set is
#: {1 k, 2 k, 20 k, 200 k, 2 M, 5 M}. 20 k gives ~50 ns/pt at a 1 ms
#: window — 4 000 pts per 200 µs phase, well above what the metric
#: math needs (Cisnal-derivative access edge needs ~50 pts/phase,
#: E_pol-at-12-µs sampling needs ~5 pts/phase) and well below the
#: scope's 200 MHz analog bandwidth so we're not just sampling
#: front-end noise. 200 k and 2 M push the per-capture USB-TMC
#: transfer into the 1-3 second range with no information gain
#: (those rates are 100x and 1000x oversampled vs the scope's own
#: bandwidth). 1 k / 2 k would *downgrade* from the legacy
#: TBS1104B's 2 500-point default.
DEFAULT_RECORD_LENGTH = 20_000


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
        # Channel count parsed from the model number — TBS1072C and
        # other 2-channel models report n_channels=2, which the GUI's
        # Setup tab uses to grey out CH3/CH4 dropdowns. *IDN? doesn't
        # carry a channel count, and there's no clean SCPI for it
        # (CH:LIST? is GPIB-era and inconsistent across firmware), so
        # the safest move is parsing the well-defined model-number
        # convention (last digit of the 4-digit numeric part).
        n_ch = channel_count_from_model(model)
        self.info = ScopeInfo(
            make=make, model=model, serial=serial, firmware=firmware,
            resource=rsrc, n_channels=n_ch, is_simulated=False,
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
        # Enforce DEFAULT_RECORD_LENGTH (20 k) instead of inheriting
        # whatever the front panel was last set to. Two reasons:
        #   1. The TBS2204B can sit at 2 M from a previous user, which
        #      would multiply per-capture USB-TMC transfer time by 100x
        #      with no information gain (see the comment on the constant).
        #   2. The TBS-series quantises record length to a fixed set
        #      (1k / 2k / 20k / 200k / 2M / 5M), so the scope rounds our
        #      request silently to the nearest legal value — set_record
        #      _length reads it back and updates the cache to whatever
        #      actually landed.
        # If we can't write the record length (older firmware, scope
        # mid-acquisition), fall back to whatever's currently configured
        # rather than failing the connection.
        try:
            self.set_record_length(DEFAULT_RECORD_LENGTH)
        except Exception:
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

    # MATLAB ``setOscilloscope_Tek.m`` per-channel block, lines 381-446.
    # The MATLAB code enforces these on every channel the user enables
    # so the bench setup is reproducible regardless of front-panel
    # state from a previous session. We mirror the same set plus an
    # explicit BANdwidth FULl (the MATLAB code doesn't set this, but
    # the lab convention is full bandwidth — no 20 MHz hardware filter,
    # which would round the leading edges of stim pulses).
    _CHANNEL_DEFAULT_COMMANDS = (
        ("{ch}:COUPling DC",      "DC coupling (no AC blocking cap)"),
        ("{ch}:INVert OFF",       "no waveform inversion"),
        ("{ch}:POSition 0",       "vertical position 0 div"),
        ('{ch}:YUNit "V"',        "report data in volts"),
        # Probe attenuation. TBS2000B/MSO/MDO firmware uses
        # ``CH<x>:PRObe:GAIN <NRf>`` where gain = 1/attenuation, so
        # gain=1 means 1X probe. Older TBS1000/TDS2000 firmware uses
        # ``CH<x>:PRObe <NR1>`` with the attenuation value directly.
        # Send both — whichever the scope rejects errors silently and
        # the next one applies.
        ("{ch}:PRObe:GAIN 1",     "1X probe (modern firmware)"),
        ("{ch}:PRObe 1",          "1X probe (legacy firmware)"),
        ("{ch}:BANdwidth FULl",   "full bandwidth (no 20 MHz filter)"),
    )

    def apply_channel_defaults(self, channel: str) -> None:
        """Force DC / 1X / no-invert / full-BW / 0-pos / V-units on one channel.

        Tolerant: each SCPI write is wrapped in try/except because the
        TBS family's older firmware will reject the modern ``PRObe:GAIN``
        form (and vice versa); skipping a rejected line lets the next
        one run. The probe-gain pair is intentional belt-and-braces.

        Idempotent: safe to re-call before each acquisition. The user's
        bench convention is that NO channel ever has AC coupling, an
        inverted waveform, a non-1X probe, or a 20 MHz BW filter.
        """
        for tmpl, _why in self._CHANNEL_DEFAULT_COMMANDS:
            try:
                self._w(tmpl.format(ch=channel))
            except Exception:
                # Continue with the next setting — defensive setup
                # mustn't fail the whole connection on one bad SCPI.
                pass

    def set_horizontal_scale(self, seconds_per_div: float) -> None:
        self._w(f"HORizontal:SCAle {seconds_per_div:g}")

    def set_horizontal_position(self, percent: float) -> None:
        """Set the trigger position on the screen as a percentage.

        ``HORizontal:POSition`` on TBS2000B/MSO/MDO/DPO takes a
        percent value (0..100) where:

        * **0** → trigger at the far-left edge (no pre-trigger samples,
          full record is post-trigger)
        * **50** → trigger at screen centre (default; 50 % pre-, 50 %
          post-trigger)
        * **100** → trigger at the far-right edge (entire record is
          pre-trigger)

        This is **not** the same as the older TDS-series
        ``HORizontal:MAIN:POSition`` (which takes seconds and stores a
        separate "main horizontal delay" parameter that doesn't
        actually move the trigger marker on TBS-firmware). We send
        the modern percent form unconditionally — the dialect map at
        the top of this file declares that every supported scope is
        the modern family.
        """
        # Clamp on the host so a programmer error doesn't put the
        # scope into a state where the trigger is off-screen.
        pct = max(0.0, min(100.0, float(percent)))
        self._w(f"HORizontal:POSition {pct:g}")

    def set_channel_position(self, channel: str, divisions: float) -> None:
        """Set the per-channel vertical position (in divisions, +/- ~5)."""
        self._w(f"{channel}:POSition {divisions:g}")

    def set_trigger_level(self, level_v: float) -> None:
        """Direct ``TRIGger:A:LEVel`` setter (older firmware fallback)."""
        try:
            self._w(f"TRIGger:A:LEVel {level_v:g}")
        except Exception:
            self._w(f"TRIGger:LEVel {level_v:g}")

    # ----- adaptive scaling / layout (ported from MATLAB setDefaultScopeView3
    #       and adjustScale + improvements) ----------------------------------
    #: Horizontal-timebase quantisation. The TBS2000B/2204B family uses
    #: a 1-2-5 sequence per the programmer manual ("1 ns/div to 100
    #: s/div in a 1-2-5 sequence"); the older TPS / TDS3000 series uses
    #: 1-2.5-5. We use 1-2-5 here because every supported model
    #: (TBS-series + MSO/MDO/DPO modern dialects) is 1-2-5; the scope
    #: would silently round a 1-2.5-5 request anyway, but snapping on
    #: the host side means our returned (scale, position) tuple is
    #: what the scope actually applied.
    _TEK_TIMEBASE_GRID_SECONDS = tuple(
        m * (10 ** e)
        for e in range(-9, 2)            # 1 ns/div ... 10 s/div decades
        for m in (1.0, 2.0, 5.0)
    ) + (10.0, 20.0, 50.0)               # 10/20/50 s/div hand-completed

    #: Vertical scale grid. Tek scopes use a 1-2-5 sequence in volts/div
    #: from 1 mV up to 5 V. Mirrors MATLAB ``adjustScale.m`` VERT_SCALE
    #: but extends the high end to 5 V/div (TBS2204B accepts up to that).
    _TEK_VERTICAL_GRID_VPD = (
        1e-3, 2e-3, 5e-3,
        1e-2, 2e-2, 5e-2,
        1e-1, 2e-1, 5e-1,
        1.0, 2.0, 5.0,
    )

    @staticmethod
    def _snap_to_grid(value: float, grid: tuple, *,
                      direction: str = "ceil") -> float:
        """Round ``value`` to the nearest legal entry in ``grid``.

        ``direction='ceil'`` picks the smallest grid value >= ``value``
        (use this for vertical scale: never want to clip).
        ``direction='floor'`` picks the largest grid value <= ``value``
        (use this for horizontal scale: smaller per-div = more pulse
        on screen).
        """
        if value <= grid[0]:
            return grid[0]
        if value >= grid[-1]:
            return grid[-1]
        if direction == "ceil":
            for g in grid:
                if g >= value:
                    return g
            return grid[-1]
        # floor
        last = grid[0]
        for g in grid:
            if g > value:
                return last
            last = g
        return last

    def auto_layout_for_pulse(self, *,
                              phase1_us: float,
                              interphase_us: float = 0.0,
                              phase2_us: float = 0.0,
                              discharge_us: float = 0.0,
                              digital_delay_us: float = 1.5,
                              ext_trigger: bool = True,
                              target_fill: float = 0.5,
                              left_offset_divs: int = 3) -> Tuple[float, float]:
        """Pick a horizontal scale + position so the pulse fills the screen.

        Mirrors the MATLAB ``setDefaultScopeView3.m`` algorithm:

          1. Total pulse width = phase1 + interphase + phase2 + discharge.
          2. Choose timebase: smallest 1-2-5 step where the pulse
             occupies at least ``target_fill`` of the 10-div screen.
          3. Compute trigger position so the **pulse** starts
             ``left_offset_divs`` divisions in from the left edge
             — accounting for the Plexon's digital_delay_us trigger
             pre-delay when EXT-triggered.

        Improvements over MATLAB:

          * Discharge tail folded into the pulse-width calculation so
            the C-discharge phase doesn't fall off the right edge.
          * Uses the documented ``HORizontal:POSition`` (percent) form
            for TBS2000B-family scopes instead of the legacy
            ``HORizontal:MAIN:POSition`` (seconds), which on TBS
            firmware silently sets a "main delay" parameter that
            doesn't actually move the trigger marker on screen.
          * Returns ``(scale_s, position_pct)`` from the scope's
            readback so callers see exactly what landed (timebase is
            quantised on a 1-2-5/1-2-4 mix and percent is quantised
            to ~0.01% steps; both are clamped on the device side).
        """
        pulse_width_us = (
            float(phase1_us) + float(interphase_us)
            + float(phase2_us) + float(discharge_us)
        )
        # Target = fraction of the 10-division screen.
        ideal_scale_us_per_div = pulse_width_us / (10.0 * target_fill)
        ideal_scale_s = ideal_scale_us_per_div * 1e-6
        # Floor onto the grid: prefer slightly *more* zoom-in (smaller
        # s/div) so pulse always meets the fill target; if the floored
        # value would over-shrink, snap up one rung.
        scale_s = self._snap_to_grid(
            ideal_scale_s, self._TEK_TIMEBASE_GRID_SECONDS, direction="ceil")
        # Verify pulse still fits in 10 divs at this scale; if it
        # doesn't (rounding edge case), bump to the next grid step.
        if pulse_width_us / 1e6 > 10.0 * scale_s * 0.9:
            idx = self._TEK_TIMEBASE_GRID_SECONDS.index(scale_s)
            if idx + 1 < len(self._TEK_TIMEBASE_GRID_SECONDS):
                scale_s = self._TEK_TIMEBASE_GRID_SECONDS[idx + 1]

        # Position: pulse starts left_offset_divs from the left edge.
        # HORizontal:POSition takes a percentage (0=far-left,
        # 100=far-right) of the trigger marker.
        #
        # Pulse-start time relative to left edge   = left_offset_divs * timebase
        # Trigger marker time relative to left edge = pulse_start - digital_delay
        #     (the digital sync output fires `digital_delay_us` BEFORE
        #      the stim phase begins on Plexon EXT-triggered setups)
        # Position percent                          = trigger_time / window * 100
        scale_us = scale_s * 1e6
        window_us = scale_us * 10.0
        pulse_start_us = float(left_offset_divs) * scale_us
        trigger_time_us = pulse_start_us
        if ext_trigger:
            trigger_time_us = pulse_start_us - float(digital_delay_us)
        position_pct = max(0.0, min(100.0,
                            trigger_time_us / window_us * 100.0))

        self.set_horizontal_scale(scale_s)
        self.set_horizontal_position(position_pct)
        # Read back what the scope actually stored — TBS-series quantises
        # to its own 1-2-5 / 1-2-4 mix on the timebase (e.g. requesting
        # 500 µs/div lands at 400 µs/div on TBS2204B firmware ≥ 1.16),
        # and HORizontal:POSition rounds to ~0.01% steps. Returning the
        # readback rather than the request means callers / log lines
        # reflect what's on the screen.
        try:
            scale_s = float(self._q("HORizontal:SCAle?"))
        except Exception:
            pass
        try:
            position_pct = float(self._q("HORizontal:POSition?"))
        except Exception:
            pass
        return scale_s, position_pct

    def initial_channel_scales(self, *,
                               amp_ua: float,
                               imon_v_per_ua: float,
                               vmon_v_per_v: float,
                               load_r_ohm: float = 0.0,
                               load_c_pf: float = 0.0,
                               phase_us: float = 200.0,
                               headroom_divs: float = 3.0) -> Dict[str, float]:
        """Pick first-capture vertical scales from amplitude + load model.

        Returns a dict ``{"CH1": vmon_scale, "CH2": imon_scale}`` (the
        canonical mapping; callers can rename via configure_channels).

        The I_mon scale is exact (we know the device's mV/µA factor and
        the requested amplitude). The V_mon scale is best-effort: with
        ``load_r_ohm`` / ``load_c_pf`` provided (test-board scenario),
        we predict V_R + V_C at end of phase; for an electrode in
        saline (unknown impedance), pass zeros and we default to a
        generous 1 V/div which the runtime adapter will tighten on
        the next capture.

        Both scales are quantised onto the Tek 1-2-5 grid with at
        least ``headroom_divs`` divisions of margin so an unexpected
        peak doesn't clip on the first capture.
        """
        # I_mon: peak (V) = amp_ua × imon_v_per_ua
        imon_peak_v = abs(amp_ua) * float(imon_v_per_ua)
        imon_scale = self._snap_to_grid(
            max(imon_peak_v / max(headroom_divs, 1.0), 1e-3),
            self._TEK_VERTICAL_GRID_VPD, direction="ceil",
        )

        # V_mon: if a load model is supplied, predict the trace; else
        # pick a generous default (1 V/div) and let the runtime
        # adaptor refine after the first capture.
        if load_r_ohm > 0 or load_c_pf > 0:
            v_r = abs(amp_ua) * 1e-6 * float(load_r_ohm)
            v_c = (abs(amp_ua) * 1e-6 * float(phase_us) * 1e-6
                   / max(float(load_c_pf) * 1e-12, 1e-15)) if load_c_pf > 0 else 0.0
            vmon_peak_v = (v_r + v_c) * float(vmon_v_per_v)
            vmon_scale = self._snap_to_grid(
                max(vmon_peak_v / max(headroom_divs, 1.0), 1e-3),
                self._TEK_VERTICAL_GRID_VPD, direction="ceil",
            )
        else:
            # Unknown load — start at 1 V/div on V_mon. Adaptive scaling
            # will tighten this within 1-2 captures.
            vmon_scale = 1.0
        return {"CH1": vmon_scale, "CH2": imon_scale}

    def adapt_channel_scale(self, channel: str, *,
                            v_min: float, v_max: float,
                            divs: float = 4.0,
                            shrink_threshold: float = 0.30,
                            shrink_stable_count: int = 2) -> Optional[float]:
        """Post-capture autorange: snap CH<n>:SCAle to fit (v_min, v_max).

        Asymmetric hysteresis (improvement on MATLAB ``adjustScale.m``):

          * **Clip detection → instant downscale.** If the signal
            would clip at the current scale (peak > divs * scale on
            either side), pick the next legal scale that fits and
            apply immediately. No waiting period — the next capture
            would be useless data.
          * **Slow upscale on shrinking signal.** If the signal is
            using less than ``shrink_threshold`` of the current
            scale's range, only upscale (tighten) after seeing the
            same "should be smaller" verdict ``shrink_stable_count``
            captures in a row. Avoids flicker between adjacent steps
            in the 1-2-5 grid.

        Returns the scale that was applied, or ``None`` if no change
        was made.
        """
        # Internal per-channel state for hysteresis. Lazy-init so
        # callers don't need to pre-populate.
        if not hasattr(self, "_adapt_state"):
            self._adapt_state: Dict[str, Dict[str, float]] = {}
        st = self._adapt_state.setdefault(
            channel, {"shrink_count": 0, "last_scale": None})

        # Read current scale from the scope (or use cached). Using the
        # scope query is one extra round-trip but guarantees we
        # respect any front-panel changes the user made between
        # captures.
        try:
            current_scale = float(self._q(f"{channel}:SCAle?"))
        except Exception:
            current_scale = st["last_scale"] or 1.0

        peak = max(abs(v_min), abs(v_max))
        # 1. Clip check — needs immediate downscale to coarser.
        if peak > current_scale * divs:
            new_scale = self._snap_to_grid(
                peak / divs, self._TEK_VERTICAL_GRID_VPD, direction="ceil")
            if new_scale > current_scale:
                self.set_channel_scale(channel, new_scale)
                st["shrink_count"] = 0
                st["last_scale"] = new_scale
                return new_scale
            return None

        # 2. Possible upscale — peak < shrink_threshold * (4 divs).
        # Compute the optimal scale; if that's strictly smaller than
        # current, increment the stability counter and only apply
        # after shrink_stable_count consecutive votes.
        ideal_scale = self._snap_to_grid(
            max(peak / divs, 1e-3),
            self._TEK_VERTICAL_GRID_VPD, direction="ceil")
        if (ideal_scale < current_scale
                and peak < shrink_threshold * current_scale * divs):
            st["shrink_count"] += 1
            if st["shrink_count"] >= shrink_stable_count:
                self.set_channel_scale(channel, ideal_scale)
                st["shrink_count"] = 0
                st["last_scale"] = ideal_scale
                return ideal_scale
        else:
            # Signal is in a healthy range or would upscale by only
            # one step — reset the counter so flicker is suppressed.
            st["shrink_count"] = 0
        return None

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
        # Trigger-edge coupling DC matches MATLAB ``setOscilloscope_Tek.m``
        # line 478. AC coupling on the trigger path would hide low-rate
        # digital edges, which is exactly what we use for stim sync.
        try:
            self._w("TRIGger:A:EDGE:COUPling DC")
        except Exception:
            pass
        try:
            self._w(f"TRIGger:A:LEVel {level_v:g}")
        except Exception:
            self._w(f"TRIGger:LEVel {level_v:g}")
        self._w(f"TRIGger:A:MODe {mode.upper()}")
        # If the trigger source is one of the input channels, force the
        # same bench defaults on that channel that we apply to mapped
        # data channels — otherwise a 10X probe attenuation or AC
        # coupling on the trigger channel will quietly make us miss
        # edges from a 3.3 V digital sync line.
        src_u = source.upper()
        if src_u.startswith("CH") and src_u[2:].isdigit():
            self.apply_channel_defaults(src_u)
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
        # Make sure each wanted channel is enabled, then enforce the
        # canonical bench settings (DC / 1X / no-invert / full-BW /
        # 0-pos / V-units) so the run starts from a known state
        # regardless of front-panel leftovers.
        for ch in set(alias_to_phys.values()):
            try:
                self._w(f"SELect:{ch} ON")
            except Exception:
                pass
            self.apply_channel_defaults(ch)
