"""PicoScope oscilloscope backend — block / rapid-block acquisition.

This is the second concrete :class:`~stimtest.hardware.base.Oscilloscope`
backend (the first is :mod:`stimtest.hardware.tektronix`).  It drives a Pico
Technology PicoScope over the native PicoSDK via the official ``picosdk`` Python
ctypes wrapper — there is **no VISA / SCPI / serial layer at all**.

Why a PicoScope backend exists
------------------------------
The Tektronix SCPI ``CURVe?`` transfers the *whole* hardware record, which on a
TBS is longer than the on-screen window, so PULSAR pulls in off-screen tail
samples (next-pulse onset / trigger re-arm) that contaminate the interpulse
tail — the customer "strange noise after the pulse" artifact (CLAUDE.md gotchas
#81/#82).  PicoScope **block mode eliminates this by construction**:
``RunBlock(preTriggerSamples, postTriggerSamples, timebase)`` captures *exactly*
``pre + post`` samples and nothing else, with the trigger at sample index
``pre`` — there is no record beyond the window you asked for, no ``DATa:STOP``,
and t=0 is deterministic (no XZEro / percent-position firmware quirk, gotcha
#22).  The driver sizes ``post = pulse + recovery`` so the capture ends just
after discharge and never reaches the re-arm region.

Architecture (mirrors the Tektronix driver)
--------------------------------------------
``tektronix.py`` is ONE class with a frozen :class:`TekCommandSet` injected for
per-dialect differences.  The Pico API has the same shape — every series
submodule (``picosdk.ps4000a``, ``ps5000a``, …) exposes parallel functions
(``ps4000aOpenUnit`` / ``ps5000aOpenUnit`` / …) with its OWN enum integer
values, range ladder, resolution support and timebase formula.  So ALL
contract logic (range-ladder snapping, window math, rapid-block averaging,
in-view/clip, time-axis build) lives ONCE here, and only the thin per-series
facts live in a frozen :class:`PicoSeriesAdapter` looked up from
:data:`PICO_SERIES`.

The behavioural differences from Tektronix the design absorbs:

* **Fixed range ladder** (±10 mV … ±50 V, 1-2-5 steps) replaces continuous
  V/div — :meth:`set_channel_scale` snaps UP to the smallest containing rung.
* **No hardware multi-trigger averaging** — Tek ``NUMAVg`` becomes SOFTWARE
  rapid-block: ``MemorySegments(N)`` + one ``RunBlock`` + ``GetValuesBulk`` +
  ``numpy.mean`` (``RATIO_MODE_AVERAGE`` is NOT this — it decimates within one
  frame).
* **Direct pre/post window** replaces SEC/DIV + record-length + position.
* **Analogue offset** is a real hardware input shift (better than Tek's
  display-only ``POSition`` for centring a DC-biased E_ret / E_act).

⚠ **PHASE-0 VERIFICATION.**  The *architecture* + the model-agnostic logic here
are solid, but a handful of exact per-series facts were researched from the
PicoSDK docs, NOT byte-verified against the installed ``picosdk`` submodule + a
live unit: the RANGE enum ordering / ladder, ``OpenUnit`` arity (ps5000a takes a
resolution arg, ps4000a does NOT), the ``analogueOffset`` sign convention, the
exact ``SetDataBuffer(s)`` / ``GetValuesBulk`` arg lists, and the timebase→
interval formula.  Every such spot is tagged ``# PHASE0:``.  The driver
**fails loudly** (never silently returns wrong data) if ``picosdk`` is absent or
a call errors — an unverified acquisition path must not masquerade as good data
on a safety-critical bench.

Nothing else in the app changes: the whole GUI / runners / calibration / export
talk to the scope ONLY through the abstract base class, so selecting this
backend is a factory + a Setup-tab dropdown (both default to Tektronix).
"""
from __future__ import annotations

import ctypes
import importlib
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .base import Oscilloscope, ScopeAcquisition, ScopeInfo


# ---------------------------------------------------------------------------
# Per-series adapter — the ONLY place model-specific facts live.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PicoSeriesAdapter:
    """Frozen per-series facts (the Pico analogue of ``TekCommandSet``).

    ``RANGE_MV`` index == the series' RANGE enum integer, so
    ``RANGE_MV[range_enum]`` is that rung's ± full-scale in millivolts and the
    driver can both snap a requested V/div to a rung AND convert ADC → volts
    from the stored rung.

    ``open_takes_resolution`` captures the ONE structural ``OpenUnit`` signature
    difference we know of: ``ps5000aOpenUnit(handle, serial, resolution)`` has a
    3rd resolution arg; ``ps4000aOpenUnit(handle, serial)`` does not (the 4000A
    series is fixed-resolution).
    """
    key: str                                   # registry key, e.g. "ps4000a"
    module_name: str                           # "picosdk.ps4000a"
    fn_prefix: str                             # "ps4000a"
    # PHASE0: verify the exact ladder + ordering for the operator's model.
    RANGE_MV: Tuple[int, ...] = (
        10, 20, 50, 100, 200, 500,
        1000, 2000, 5000, 10000, 20000, 50000,
    )
    open_takes_resolution: bool = False        # ps5000a: True; ps4000a: False
    supports_resolution: bool = False          # SetDeviceResolution available?
    has_ext_trigger: bool = False              # dedicated EXT/AUX BNC?
    # The LEGACY block-mode API (ps4000, ps5000, ps6000 non-'a') takes an extra
    # int16 ``oversample`` arg in GetTimebase2 (after the interval out-param) AND
    # RunBlock (after ``timebase``).  The modern 'a'/'A' drivers (ps4000a /
    # ps5000a / ps2000a) dropped it.  PHASE0: ps4000 needs its OWN full spike
    # (different enums / ranges / no analogueOffset) before use — this flag only
    # fixes the block-mode arity so the shared path is structurally correct.
    block_takes_oversample: bool = False
    default_resolution_bits: int = 12
    n_vert_divs: int = 10                       # VIRTUAL grid (no physical divs)
    # ns/sample for timebase index 0, and the divisor so
    # interval_ns ≈ base_interval_ns * (timebase + 1) OR the model formula.
    # Only a SEED for the GetTimebase2 search — GetTimebase2's returned value
    # is always the ground truth.  PHASE0: refine per model if the seed search
    # is slow (correctness is unaffected — the search converges regardless).
    seed_min_interval_ns: float = 12.5         # ~80 MSa/s class

    def timebase_seed(self, res_bits: int, target_dt_s: float) -> int:
        """A cheap starting timebase index for the GetTimebase2 search.

        Deliberately conservative (biases toward a FINER interval than needed)
        so the search only ever has to step UP.  GetTimebase2's returned
        interval — not this — defines the real sample interval.
        """
        target_ns = max(float(target_dt_s) * 1e9, self.seed_min_interval_ns)
        # interval_ns ≈ seed_min * (tb + 1)  →  tb ≈ target/seed_min - 1
        tb = int(target_ns / self.seed_min_interval_ns) - 1
        return max(tb, 0)


# The operator has a 4000-series → ps4000a is the primary target.  Additional
# series are one-line entries each (each needs its own PHASE-0 spike before use).
PICO_SERIES: Dict[str, PicoSeriesAdapter] = {
    # PHASE0: 4000A models (4224A/4444/4824A) are fixed-resolution (12-14 bit),
    # OpenUnit(handle, serial) with NO resolution arg, mostly no EXT BNC on the
    # 8-channel 4824A.  Range ladder per model (some cap at ±50 V).
    "ps4000a": PicoSeriesAdapter(
        key="ps4000a", module_name="picosdk.ps4000a", fn_prefix="ps4000a",
        open_takes_resolution=False, supports_resolution=False,
        has_ext_trigger=False, default_resolution_bits=12,
        seed_min_interval_ns=12.5),
    # Older 4000 series (4224/4262) uses the DIFFERENT ``ps4000`` module — its
    # block-mode calls take the legacy ``oversample`` arg.
    "ps4000": PicoSeriesAdapter(
        key="ps4000", module_name="picosdk.ps4000", fn_prefix="ps4000",
        open_takes_resolution=False, supports_resolution=False,
        has_ext_trigger=True, default_resolution_bits=12,
        block_takes_oversample=True, seed_min_interval_ns=12.5),
    # Flexible-resolution 5000A (kept for completeness / a future 5000 unit).
    "ps5000a": PicoSeriesAdapter(
        key="ps5000a", module_name="picosdk.ps5000a", fn_prefix="ps5000a",
        RANGE_MV=(10, 20, 50, 100, 200, 500,
                  1000, 2000, 5000, 10000, 20000),
        open_takes_resolution=True, supports_resolution=True,
        has_ext_trigger=True, default_resolution_bits=12,
        seed_min_interval_ns=8.0),
    # Entry 2000A (2/4-channel).
    "ps2000a": PicoSeriesAdapter(
        key="ps2000a", module_name="picosdk.ps2000a", fn_prefix="ps2000a",
        open_takes_resolution=False, supports_resolution=False,
        has_ext_trigger=True, default_resolution_bits=8,
        seed_min_interval_ns=2.0),
}

# App-facing physical channel name → PicoScope channel enum index (A=0..D=3).
_PHYS_TO_IDX: Dict[str, int] = {"CH1": 0, "CH2": 1, "CH3": 2, "CH4": 3}
_IDX_TO_PHYS: Dict[int, str] = {v: k for k, v in _PHYS_TO_IDX.items()}

# picosdk status codes for USB3 power negotiation (OpenUnit returns these when
# a USB3 device is on a port that can't supply full power but can still run).
_PICO_POWER_SUPPLY_NOT_CONNECTED = 286
_PICO_USB3_0_DEVICE_NON_USB3_0_PORT = 282
_PICO_OK = 0


class PicoScopeOscilloscope(Oscilloscope):
    """A PicoScope :class:`Oscilloscope` backend (block / rapid-block mode).

    Construct with the picosdk series key (``"ps4000a"`` for the operator's
    4000-series), an optional Pico serial string (``resource``; None = first
    unit), and — for flexible-resolution series only — a resolution.
    """

    def __init__(self, series: str = "ps4000a",
                 resource: Optional[str] = None,
                 resolution_bits: Optional[int] = None):
        if series not in PICO_SERIES:
            raise ValueError(
                f"Unknown PicoScope series {series!r}; "
                f"known: {sorted(PICO_SERIES)}")
        self._ad = PICO_SERIES[series]
        self._resource = resource
        self._res_bits = int(resolution_bits
                             if resolution_bits is not None
                             else self._ad.default_resolution_bits)

        # SDK handles — bound in open().
        self.ps = None                          # the psX C-function namespace
        self._funcs = None                      # picosdk.functions (adc2mV, …)
        self._h = ctypes.c_int16()
        self._max_adc = 0                        # ADC full-scale count
        self._max_segments = 1                   # rapid-block ceiling

        # Per-channel vertical state.
        self._range: Dict[str, int] = {}         # phys ch -> RANGE_MV index
        self._offset_v: Dict[str, float] = {}    # phys ch -> analogueOffset (V)
        self._coupling: Dict[str, str] = {}      # phys ch -> "AC" | "DC"
        self._overflow: Dict[str, bool] = {}     # phys ch -> last-capture clip

        # Horizontal / acquisition state.
        self._timebase = 8
        self._dt_us = 0.0                        # realised sample interval (µs)
        self._record_length = 5000
        self._position_pct = 20.0
        self._acq_mode = "AVERAGE"
        self._n_avg = 16

        # Trigger state (I_mon channel-edge for the operator's bench).
        self._trig_source = "CH2"
        self._trig_slope = "RISE"
        self._trig_level_v = 0.0
        self._trig_mode = "NORMAL"

        # Virtual division grid (no physical divisions on a PicoScope).
        self._n_horiz_divs = 10.0
        self._n_vert_divs = float(self._ad.n_vert_divs)
        self._half_vert_divs = self._n_vert_divs / 2.0

        self.channel_aliases: Dict[str, str] = {
            "vmon": "CH1", "imon": "CH2", "eret": "CH3", "eact": "CH4"}
        self.info = ScopeInfo(make="Pico", is_simulated=False,
                              has_ext_trigger=self._ad.has_ext_trigger)
        self.cmd_logger: Optional[Callable[[str], None]] = None

    # ------------------------------------------------------------------
    # logging + SDK-call plumbing
    # ------------------------------------------------------------------
    def _log(self, msg: str) -> None:
        if self.cmd_logger is not None:
            try:
                self.cmd_logger(msg)
            except Exception:
                pass

    def _c(self, name: str):
        """Resolve the series-prefixed C function ``psX<name>``."""
        if self.ps is None:
            raise RuntimeError("PicoScope not open (call open() first).")
        return getattr(self.ps, f"{self._ad.fn_prefix}{name}")

    def _ck(self, status: int, what: str) -> None:
        """Raise a clear error on a non-OK PicoSDK status.

        We NEVER swallow an SDK error — an unverified/failed acquisition must
        surface, not return silent garbage on a safety-critical bench.
        """
        if int(status) != _PICO_OK:
            raise RuntimeError(
                f"PicoScope {self._ad.fn_prefix}{what} failed: "
                f"status={int(status)}")

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def open(self, resource: Optional[str] = None) -> None:
        # LAZY import — the app + tests must load without picosdk installed.
        try:
            self.ps = importlib.import_module(self._ad.module_name)
            from picosdk import functions as _functions  # noqa: WPS433
            self._funcs = _functions
        except Exception as e:  # ImportError or missing native SDK libs
            raise RuntimeError(
                f"PicoSDK Python wrapper unavailable for {self._ad.key}: {e!r}. "
                f"Install the native PicoSDK (psX000a runtime libraries) AND "
                f"`pip install picosdk`; both must match the app's 64-bit "
                f"process.") from e

        serial = (resource if resource is not None else self._resource)
        serial_b = serial.encode() if serial else None
        self._log(f"[scope] PicoScope open ({self._ad.key}, "
                  f"serial={serial or 'first-unit'}, "
                  f"res={self._res_bits}-bit)")

        if self._ad.open_takes_resolution:
            res_enum = self._resolution_enum(self._res_bits)
            st = self._c("OpenUnit")(ctypes.byref(self._h), serial_b, res_enum)
        else:
            # PHASE0: ps4000a OpenUnit(handle, serial) — 2 args, no resolution.
            st = self._c("OpenUnit")(ctypes.byref(self._h), serial_b)

        if int(st) in (_PICO_POWER_SUPPLY_NOT_CONNECTED,
                       _PICO_USB3_0_DEVICE_NON_USB3_0_PORT):
            # USB3 power negotiation — accept USB-only power.
            self._log(f"[scope] USB3 power negotiation (status {int(st)})")
            self._ck(self._c("ChangePowerSource")(self._h, st),
                     "ChangePowerSource")
        else:
            self._ck(st, "OpenUnit")

        # ADC full-scale + rapid-block segment ceiling.
        _max = ctypes.c_int16()
        self._ck(self._c("MaximumValue")(self._h, ctypes.byref(_max)),
                 "MaximumValue")
        self._max_adc = int(_max.value)
        try:
            _seg = ctypes.c_int32()
            self._c("GetMaxSegments")(self._h, ctypes.byref(_seg))
            self._max_segments = max(1, int(_seg.value))
        except Exception:
            self._max_segments = 1               # some series lack the query

        self.info = self._read_unit_info(serial)
        self._n_horiz_divs = 10.0
        self._log(f"[scope] PicoScope ready: {self.info.model} "
                  f"({self.info.n_channels}ch, maxADC={self._max_adc}, "
                  f"maxSeg={self._max_segments})")

    def close(self) -> None:
        if self.ps is None:
            return
        try:
            self._c("Stop")(self._h)
        except Exception:
            pass
        try:
            self._c("CloseUnit")(self._h)
        except Exception:
            pass
        # Clear cached state so a reconnect re-enumerates.
        self._max_adc = 0
        self._max_segments = 1
        self._range.clear()
        self._overflow.clear()
        self.ps = None
        self._log("[scope] PicoScope closed")

    def _read_unit_info(self, serial: Optional[str]) -> ScopeInfo:
        """Populate ScopeInfo from GetUnitInfo (best-effort)."""
        # PHASE0: the GetUnitInfo enum keys (PICO_VARIANT_INFO=3, BATCH_AND_
        # SERIAL=4, CAL_DATE=5, KERNEL_VERSION=...) are stable across series,
        # but confirm the exact call signature for ps4000a.
        def _info(kind: int) -> str:
            try:
                buf = ctypes.create_string_buffer(64)
                req = ctypes.c_int16()
                self._c("GetUnitInfo")(self._h, buf, ctypes.c_int16(64),
                                       ctypes.byref(req), ctypes.c_uint32(kind))
                return buf.value.decode(errors="replace").strip()
            except Exception:
                return ""
        variant = _info(3) or self._ad.key.upper()
        serial_str = _info(4) or (serial or "")
        firmware = _info(6) or ""
        n_ch = self._variant_channel_count(variant)
        return ScopeInfo(make="Pico", model=variant, serial=serial_str,
                         firmware=firmware, resource=(serial or ""),
                         n_channels=n_ch, is_simulated=False,
                         has_ext_trigger=self._ad.has_ext_trigger)

    @staticmethod
    def _variant_channel_count(variant: str) -> int:
        """Best-effort channel count from the model string (e.g. '4824A'→8,
        '4444'→4, '4224A'→2).  PHASE0: verify against the operator's model."""
        v = (variant or "").upper()
        for tag, n in (("4824", 8), ("4444", 4), ("4424", 4), ("4224", 2),
                       ("4262", 2)):
            if tag in v:
                return n
        # Fall back to the 4th char of a Pico model number when it encodes the
        # channel count (PicoScope naming: the last digit before a suffix).
        return 4

    def _resolution_enum(self, bits: int):
        """Map a bit count to the series' RESOLUTION enum (5000A only)."""
        # PHASE0: PS5000A_DEVICE_RESOLUTION dict — 8BIT=0,12=1,14=2,15=3,16=4.
        name = f"{self._ad.fn_prefix.upper()}_DR_{bits}BIT"
        table = getattr(self.ps, f"{self._ad.fn_prefix.upper()}_DEVICE_RESOLUTION",
                        None)
        if table is not None and name in table:
            return table[name]
        return int({8: 0, 12: 1, 14: 2, 15: 3, 16: 4}.get(bits, 1))

    # ------------------------------------------------------------------
    # vertical — DISCRETE RANGE LADDER (no arbitrary V/div)
    # ------------------------------------------------------------------
    def _range_for_peak_v(self, peak_v: float) -> int:
        """Smallest RANGE_MV rung whose ± full-scale contains ``peak_v``."""
        peak_mv = abs(float(peak_v)) * 1000.0
        for i, mv in enumerate(self._ad.RANGE_MV):
            if mv >= peak_mv:
                return i
        return len(self._ad.RANGE_MV) - 1        # clamp to the coarsest rung

    def _range_volts(self, ch: str) -> float:
        rng = self._range.get(ch, len(self._ad.RANGE_MV) - 1)
        return self._ad.RANGE_MV[rng] / 1000.0

    def _vpd(self, ch: str) -> float:
        """Virtual volts-per-division for a channel (range / half-grid)."""
        return self._range_volts(ch) / self._half_vert_divs

    def set_channel_scale(self, channel: str, volts_per_div: float) -> None:
        peak_v = float(volts_per_div) * self._half_vert_divs
        self._range[channel] = self._range_for_peak_v(peak_v)
        self._apply_channel(channel)

    def set_channel_position(self, channel: str, divisions: float) -> None:
        # analogueOffset is a REAL hardware input shift (volts).  A trace we
        # want centred at ``divisions`` above screen-centre needs the input
        # shifted DOWN by that many divisions' worth of volts.
        # PHASE0: verify the offset SIGN convention on hardware.
        self._offset_v[channel] = -float(divisions) * self._vpd(channel)
        self._apply_channel(channel)

    def set_channel_coupling(self, channel: str, coupling: str) -> None:
        self._coupling[channel] = str(coupling).upper()
        self._apply_channel(channel)

    def _apply_channel(self, ch: str) -> None:
        """Push a channel's (enabled, coupling, range, offset) to the device."""
        if self.ps is None:
            return
        idx = _PHYS_TO_IDX.get(ch)
        if idx is None:
            return
        rng = self._range.get(ch, len(self._ad.RANGE_MV) - 1)
        coupling_enum = self._coupling_enum(self._coupling.get(ch, "DC"))
        offset = self._clamp_offset(ch, self._offset_v.get(ch, 0.0))
        # PHASE0: SetChannel(handle, channel, enabled, coupling, range,
        # analogueOffset) — arg order confirmed for ps4000a/ps5000a.
        self._ck(self._c("SetChannel")(
            self._h, ctypes.c_int32(idx), ctypes.c_int16(1),
            ctypes.c_int32(coupling_enum), ctypes.c_int32(rng),
            ctypes.c_float(offset)), "SetChannel")

    def _coupling_enum(self, coupling: str) -> int:
        # PHASE0: PS4000A_COUPLING AC=0, DC=1 (confirm per series).
        table = getattr(self.ps, f"{self._ad.fn_prefix.upper()}_COUPLING", None)
        key = "AC" if str(coupling).upper() == "AC" else "DC"
        name = f"{self._ad.fn_prefix.upper()}_{key}"
        if table is not None and name in table:
            return table[name]
        return 0 if key == "AC" else 1

    def _clamp_offset(self, ch: str, offset_v: float) -> float:
        """Clamp analogueOffset to the device's allowed range for the rung.

        PHASE0: GetAnalogueOffset(handle, range, coupling, &max, &min) gives the
        EXACT bounds; until verified, clamp CONSERVATIVELY to a fraction of the
        rung full-scale.  The real device offset window is SMALLER than ± the
        full range near the coarse rungs (a ±20 V rung typically allows only a
        few volts of offset), so clamping to the full range is TOO PERMISSIVE —
        it can pass an offset the hardware rejects (SetChannel then raises via
        ``_ck``, which is loud, not silent, but still a failed capture).  A
        conservative ``_OFFSET_FRAC`` (0.5) of the rung is a safe subset until
        the real GetAnalogueOffset bounds are wired in Phase 0."""
        lim = self._range_volts(ch) * self._OFFSET_FRAC
        return float(max(-lim, min(lim, float(offset_v))))

    _OFFSET_FRAC = 0.5

    def _applied_offset(self, ch: str) -> float:
        """The analogueOffset ACTUALLY pushed to SetChannel for ``ch`` (after
        clamping) — the value the reconstruction + in-view math must use."""
        return self._clamp_offset(ch, self._offset_v.get(ch, 0.0))

    def set_channel_scale_and_position_for_range(
            self, channel: str, *, v_min: float, v_max: float,
            divs: float = 4.0):
        span = abs(float(v_max) - float(v_min))
        mid = 0.5 * (float(v_max) + float(v_min))
        # size the rung for the full excursion, then centre it via offset.
        self._range[channel] = self._range_for_peak_v(0.5 * span)
        self._offset_v[channel] = -mid          # PHASE0: sign on HW
        self._apply_channel(channel)
        return (self._vpd(channel),
                (-mid) / self._vpd(channel) if self._vpd(channel) else 0.0)

    def zero_all_channel_positions(self) -> None:
        for ch in _PHYS_TO_IDX:
            if ch in self._range:
                self._offset_v[ch] = 0.0
                self._apply_channel(ch)

    # ------------------------------------------------------------------
    # in-view / clip — reuse the shared MATLAB getWaveform2 formula
    # ------------------------------------------------------------------
    def channel_in_view(self, channel: str, v_min: float, v_max: float,
                        *, margin_divs: float = 3.95) -> Optional[bool]:
        vpd = self._vpd(channel)
        if vpd <= 0:
            return None
        # ``v_min/v_max`` are TRUE input volts (the offset is already removed in
        # _adc_to_volts).  The representable input window is centred at the
        # NEGATIVE of the applied hardware offset: the ADC spans ±range around
        # (V_in + offset)=0, i.e. V_in ∈ [-range-offset, +range-offset], centre
        # -offset.  (Was +offset — the shift the offset-subtraction bug hid.)
        vert_pos = -self._applied_offset(channel)
        lo = -margin_divs * vpd + vert_pos
        hi = +margin_divs * vpd + vert_pos
        return bool(v_min > lo and v_max < hi)

    def channel_is_clipped(self, channel: str, v_min: float, v_max: float,
                           *, margin_pct: float = 0.05) -> Optional[bool]:
        # Prefer the NATIVE hardware overflow flag from the last capture.
        if channel in self._overflow:
            return bool(self._overflow[channel])
        vpd = self._vpd(channel)
        if vpd <= 0:
            return None
        rail = self._half_vert_divs * vpd
        vert_pos = -self._applied_offset(channel)   # true-volts window centre
        near = margin_pct * rail
        return bool(v_max >= (vert_pos + rail - near)
                    or v_min <= (vert_pos - rail + near))

    # ------------------------------------------------------------------
    # horizontal — (timebase, pre, post); no off-screen tail
    # ------------------------------------------------------------------
    def set_record_length(self, n: int) -> None:
        n = int(n)
        if n <= 0:
            raise ValueError(f"record_length must be positive, got {n}.")
        # PHASE0: clamp to the per-segment capacity for the current N averaging
        # (memory splits N ways in rapid block); GetTimebase2's maxSamples is
        # the authority.  Recompute pre/post from the stored position.
        self._record_length = n

    def set_horizontal_scale(self, seconds_per_div: float) -> float:
        target_dt = (float(seconds_per_div) * self._n_horiz_divs) / max(
            self._record_length, 1)
        tb, dt_ns = self._find_timebase(target_dt)
        self._timebase = tb
        self._dt_us = dt_ns * 1e-3
        return float(seconds_per_div)

    def _find_timebase(self, target_dt_s: float) -> Tuple[int, float]:
        """Return (timebase_index, realised_interval_ns) whose GetTimebase2
        interval is ≤ ``target_dt_s`` — the RETURNED value is ground truth."""
        if self.ps is None:
            # host-side estimate only (tests / pre-open).
            ns = max(target_dt_s * 1e9, self._ad.seed_min_interval_ns)
            tb = self._ad.timebase_seed(self._res_bits, target_dt_s)
            return tb, ns
        tb = self._ad.timebase_seed(self._res_bits, target_dt_s)
        dt_ns = ctypes.c_float()
        mx = ctypes.c_int32()
        last_ns = self._ad.seed_min_interval_ns
        for _ in range(0, 1 << 20):              # bounded search
            if self._ad.block_takes_oversample:
                # legacy ps4000: GetTimebase2(h, tb, n, *interval, oversample,
                # *maxSamples, segment)
                st = self._c("GetTimebase2")(
                    self._h, ctypes.c_uint32(tb),
                    ctypes.c_int32(self._record_length),
                    ctypes.byref(dt_ns), ctypes.c_int16(0),
                    ctypes.byref(mx), ctypes.c_uint32(0))
            else:
                st = self._c("GetTimebase2")(
                    self._h, ctypes.c_uint32(tb),
                    ctypes.c_int32(self._record_length),
                    ctypes.byref(dt_ns), ctypes.byref(mx), ctypes.c_uint32(0))
            if int(st) == _PICO_OK and dt_ns.value > 0:
                last_ns = float(dt_ns.value)
                if last_ns <= target_dt_s * 1e9:
                    return tb, last_ns
            tb += 1
        return tb, last_ns

    def _run_block(self, pre: int, post: int) -> None:
        """Arm a block capture — handles the legacy ``oversample`` arg."""
        t_ind = ctypes.c_int32()
        if self._ad.block_takes_oversample:
            # legacy ps4000: RunBlock(h, pre, post, tb, oversample,
            # *timeIndisposedMs, segment, lpReady, param)
            self._ck(self._c("RunBlock")(
                self._h, ctypes.c_int32(pre), ctypes.c_int32(post),
                ctypes.c_uint32(self._timebase), ctypes.c_int16(0),
                ctypes.byref(t_ind), ctypes.c_uint16(0), None, None),
                "RunBlock")
        else:
            self._ck(self._c("RunBlock")(
                self._h, ctypes.c_int32(pre), ctypes.c_int32(post),
                ctypes.c_uint32(self._timebase), ctypes.byref(t_ind),
                ctypes.c_uint32(0), None, None), "RunBlock")

    def set_horizontal_position(self, percent: float) -> None:
        self._position_pct = float(max(0.0, min(100.0, percent)))

    def _pre_post(self) -> Tuple[int, int]:
        """Split the record into pre/post-trigger samples from the position."""
        n = int(self._record_length)
        pre = int(round(self._position_pct / 100.0 * n))
        pre = max(0, min(n, pre))
        return pre, n - pre

    # ------------------------------------------------------------------
    # trigger — I_mon channel-edge for the operator's bench
    # ------------------------------------------------------------------
    def set_trigger(self, source: str = "CH2", level_v: float = 0.0,
                    slope: str = "RISE", mode: str = "NORMAL") -> None:
        self._trig_source = str(source)
        self._trig_slope = str(slope).upper()
        self._trig_level_v = float(level_v)
        self._trig_mode = str(mode).upper()
        self._apply_trigger()

    def set_trigger_level(self, level_v: float) -> None:
        self._trig_level_v = float(level_v)
        self._apply_trigger()

    def _apply_trigger(self) -> None:
        if self.ps is None:
            return
        src_idx, thr_adc = self._trigger_source_and_threshold()
        direction = self._direction_enum(self._trig_slope)
        auto_ms = 0 if self._trig_mode == "NORMAL" else 1000
        # PHASE0: SetSimpleTrigger(handle, enable, source, threshold_adc,
        # direction, delay_samples, autoTrigger_ms).
        self._ck(self._c("SetSimpleTrigger")(
            self._h, ctypes.c_int16(1), ctypes.c_int32(src_idx),
            ctypes.c_int16(int(thr_adc)), ctypes.c_int32(direction),
            ctypes.c_uint32(0), ctypes.c_int16(int(auto_ms))),
            "SetSimpleTrigger")

    def _trigger_source_and_threshold(self) -> Tuple[int, int]:
        """(source enum, threshold in ADC counts of that source's range)."""
        src = self._trig_source.upper()
        if src.startswith("EXT") or src.startswith("AUX"):
            # PHASE0: EXTERNAL=4 / TRIGGER_AUX=5; EXT full-scale ±5 V; the exact
            # EXT_MAX_ADC constant is series-specific.
            idx = 4
            ext_full_scale_v = 5.0
            ext_max_adc = 32767
            thr = int(self._trig_level_v / ext_full_scale_v * ext_max_adc)
            return idx, thr
        # A data-channel edge trigger (I_mon): threshold in ADC of ITS range.
        ch = src if src in _PHYS_TO_IDX else self.channel_aliases.get(
            "imon", "CH2")
        idx = _PHYS_TO_IDX.get(ch, 1)
        thr = self._volts_to_adc(ch, self._trig_level_v)
        return idx, thr

    def _direction_enum(self, slope: str) -> int:
        # PHASE0: THRESHOLD_DIRECTION RISING=2, FALLING=3.
        table = getattr(self.ps,
                        f"{self._ad.fn_prefix.upper()}_THRESHOLD_DIRECTION", None)
        key = "FALLING" if str(slope).upper().startswith("FALL") else "RISING"
        name = f"{self._ad.fn_prefix.upper()}_{key}"
        if table is not None and name in table:
            return table[name]
        return 3 if key == "FALLING" else 2

    def _volts_to_adc(self, ch: str, volts: float) -> int:
        rng_v = self._range_volts(ch)
        if rng_v <= 0 or self._max_adc <= 0:
            return 0
        return int(round(float(volts) / rng_v * self._max_adc))

    # ------------------------------------------------------------------
    # acquisition mode / averaging
    # ------------------------------------------------------------------
    def set_acquisition_mode(self, mode: str = "AVERAGE", n_avg: int = 16) -> None:
        self._acq_mode = str(mode).upper()
        self._n_avg = max(1, int(n_avg))

    def acquisition_modes(self) -> List[str]:
        return ["SAMPLE", "AVERAGE"]

    def average_count_choices(self) -> Optional[List[int]]:
        return None                              # arbitrary N (bounded by segs)

    def max_average_count(self) -> int:
        return max(1, int(self._max_segments))

    def set_average_count(self, n_avg: int) -> int:
        self._n_avg = max(1, min(int(n_avg), self.max_average_count()))
        return self._n_avg

    def configure_channels(self, alias_to_phys: Dict[str, str]) -> None:
        self.channel_aliases = dict(alias_to_phys)
        if self.ps is None:
            return
        used = set(alias_to_phys.values())
        for ch, idx in _PHYS_TO_IDX.items():
            if ch in used:
                self._apply_channel(ch)          # enable + apply current state
            else:
                # PHASE0: disable an unused channel (enabled=0).
                try:
                    self._ck(self._c("SetChannel")(
                        self._h, ctypes.c_int32(idx), ctypes.c_int16(0),
                        ctypes.c_int32(1), ctypes.c_int32(0),
                        ctypes.c_float(0.0)), "SetChannel(disable)")
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # capture
    # ------------------------------------------------------------------
    def _enabled_phys_channels(self) -> List[str]:
        # Read data channels from the aliases (exclude a pure Trigger role).
        chans = []
        for role, ch in self.channel_aliases.items():
            if role == "trigger":
                continue
            if ch in _PHYS_TO_IDX and ch not in chans:
                chans.append(ch)
        return chans or ["CH1"]

    def _time_axis_us(self, npts: int, pre: int) -> np.ndarray:
        return (np.arange(npts, dtype=np.float64) - pre) * self._dt_us

    def _adc_to_volts(self, ch: str, raw: np.ndarray) -> np.ndarray:
        rng_v = self._range_volts(ch)
        if self._max_adc <= 0:
            return raw.astype(np.float64)
        # The ADC digitises (V_in + analogueOffset), so the raw→volts scaling
        # yields (V_in + offset); SUBTRACT the applied offset to recover the
        # TRUE input volts (the Tek contract: captured data is true volts, no
        # display-only shift baked in).  Without this, a DC-biased E_ret/E_act
        # centred via set_channel_position / _for_range came back shifted by the
        # offset, corrupting E_pol → the water-window safety check (audit HIGH).
        # PHASE0: verify the analogueOffset SIGN convention on hardware — if the
        # device digitises (V_in − offset), flip this to `+ offset`.
        adc_volts = raw.astype(np.float64) * (rng_v / self._max_adc)
        return adc_volts - self._applied_offset(ch)

    def single_capture(self, *, timeout_s: Optional[float] = None
                       ) -> ScopeAcquisition:
        return self.capture_single_sequence(n_acq=1, timeout_s=timeout_s or 30.0)

    def capture_single_sequence(self, *, n_acq: int = 16,
                                timeout_s: float = 30.0,
                                tick_fn=None) -> ScopeAcquisition:
        """Acquire an N-averaged frame via rapid block (N=1 → a single block).

        Arms the trigger ONCE, captures ``n_acq`` triggered segments back-to-
        back, transfers them, and SOFTWARE-averages per channel — the faithful
        analog of Tek NUMAVg (there is no hardware multi-trigger average).
        """
        if self.ps is None:
            raise RuntimeError("PicoScope not open.")
        n = max(1, int(n_acq if self._acq_mode == "AVERAGE" else 1))
        n = min(n, self.max_average_count())
        pre, post = self._pre_post()
        total = pre + post
        chans = self._enabled_phys_channels()

        # --- rapid-block memory layout -------------------------------------
        if n > 1:
            cap = ctypes.c_int32()
            self._ck(self._c("MemorySegments")(
                self._h, ctypes.c_uint32(n), ctypes.byref(cap)),
                "MemorySegments")
            self._ck(self._c("SetNoOfCaptures")(self._h, ctypes.c_uint32(n)),
                     "SetNoOfCaptures")

        # buffers: one int16 array per (channel, segment).
        ratio_none = self._ratio_mode_none()
        buffers: Dict[str, List] = {ch: [] for ch in chans}
        for ch in chans:
            idx = _PHYS_TO_IDX[ch]
            for seg in range(n):
                buf = (ctypes.c_int16 * total)()
                buffers[ch].append(buf)
                self._ck(self._c("SetDataBuffer")(
                    self._h, ctypes.c_int32(idx), ctypes.byref(buf),
                    ctypes.c_int32(total), ctypes.c_uint32(seg),
                    ctypes.c_int32(ratio_none)), "SetDataBuffer")

        # --- arm + run -----------------------------------------------------
        self._run_block(pre, post)

        deadline = time.perf_counter() + float(timeout_s)
        ready = ctypes.c_int16(0)
        while ready.value == 0:
            if self._should_abort() or time.perf_counter() > deadline:
                self._log("[scope] capture: abort/timeout waiting for trigger")
                break
            self._c("IsReady")(self._h, ctypes.byref(ready))
            if tick_fn is not None:
                tick_fn()
            time.sleep(0.001)
        # If the trigger never fired (abort/timeout), do NOT return silently-
        # partial buffers as if valid — surface it (matches the fail-loud
        # contract; the Tek soft-timeout reads the averaged frame, but here a
        # never-triggered block has NO valid data).
        if ready.value == 0 and not self._should_abort():
            try:
                self._c("Stop")(self._h)
            except Exception:
                pass
            raise RuntimeError(
                f"PicoScope capture timed out after {timeout_s:.1f}s waiting "
                f"for the trigger (no block completed).")

        # --- transfer ------------------------------------------------------
        overflow = (ctypes.c_int16 * max(n, 1))()
        got = ctypes.c_uint32(total)
        if n > 1:
            self._ck(self._c("GetValuesBulk")(
                self._h, ctypes.byref(got), ctypes.c_uint32(0),
                ctypes.c_uint32(n - 1), ctypes.c_uint32(0),
                ctypes.c_int32(ratio_none), ctypes.byref(overflow)),
                "GetValuesBulk")
        else:
            ovf = ctypes.c_int16()
            self._ck(self._c("GetValues")(
                self._h, ctypes.c_uint32(0), ctypes.byref(got),
                ctypes.c_uint32(0), ctypes.c_int32(ratio_none),
                ctypes.c_uint32(0), ctypes.byref(ovf)), "GetValues")
            overflow[0] = ovf
        try:
            self._c("Stop")(self._h)
        except Exception:
            pass

        npts = int(got.value) or total
        # --- average segments in software + convert to volts ---------------
        channels: Dict[str, np.ndarray] = {}
        for ch in chans:
            idx = _PHYS_TO_IDX[ch]
            frames = np.stack([
                np.frombuffer(buf, dtype=np.int16, count=npts).astype(np.float64)
                for buf in buffers[ch]], axis=0)
            mean_raw = frames.mean(axis=0)
            channels[ch] = self._adc_to_volts(ch, mean_raw)
            # native overflow flag (bit per channel across the segments).
            self._overflow[ch] = bool(
                any((int(overflow[s]) >> idx) & 1 for s in range(n)))

        time_us = self._time_axis_us(npts, pre)
        return ScopeAcquisition(
            time_us=time_us, channels=channels,
            sample_period_us=self._dt_us, record_length=npts,
            trigger_position_us=-pre * self._dt_us)

    def settle_one_acquisition(self, *,
                               timeout_s: Optional[float] = None) -> None:
        """Arm a throwaway block and wait for one fresh trigger, discard it."""
        if self.ps is None:
            return
        pre, post = self._pre_post()
        try:
            self._run_block(pre, post)
            deadline = time.perf_counter() + float(timeout_s or 30.0)
            ready = ctypes.c_int16(0)
            while ready.value == 0:
                if self._should_abort() or time.perf_counter() > deadline:
                    break
                self._c("IsReady")(self._h, ctypes.byref(ready))
                time.sleep(0.001)
        finally:
            try:
                self._c("Stop")(self._h)
            except Exception:
                pass

    def capture_while_running(self, wait_s: float = 0.0, *,
                              reset_before_run: bool = False,
                              tick_fn=None) -> ScopeAcquisition:
        # Every RunBlock re-arms fresh, so reset_before_run is implicit.
        remaining = max(float(wait_s), 0.0)
        while remaining > 0.0:
            if self._should_abort():
                break
            chunk = min(0.05, remaining)
            time.sleep(chunk)
            remaining -= chunk
            if tick_fn is not None:
                tick_fn()
        return self.capture_single_sequence(
            n_acq=self._n_avg if self._acq_mode == "AVERAGE" else 1,
            tick_fn=tick_fn)

    def _ratio_mode_none(self) -> int:
        table = getattr(self.ps, f"{self._ad.fn_prefix.upper()}_RATIO_MODE", None)
        name = f"{self._ad.fn_prefix.upper()}_RATIO_MODE_NONE"
        if table is not None and name in table:
            return table[name]
        return 0
