"""Tektronix oscilloscope driver (pyvisa, model-aware).

This driver is the *only* file in the project that talks SCPI. Every other
module sees the abstract :class:`stimtest.hardware.base.Oscilloscope`
interface and never knows whether it's driving a real scope, a simulated
scope, or some future make/model.

Model detection
---------------
On :meth:`open`, we send ``*IDN?`` and parse the comma-separated response:

    TEKTRONIX,TBS2204B,C019999,CF:91.1CT FV:v1.16

The driver is **adaptive across the Tek family** — it doesn't rely on
matching every model in a regex. Two complementary mechanisms:

* **Command-set probing** (:meth:`_probe_commands`): we send a cheap
  test query (``WFMOutpre:NR_Pt?``) and pick ``MODERN_CMDS`` if it
  answers; fall back to ``WFMPre:NR_Pt?`` / ``LEGACY_CMDS`` otherwise.
  Modern firmware on a future model we haven't heard of will Just Work;
  legacy firmware on a "modern" model number is still recognised.
  We also probe whether ``ACQuire:NUMAVg`` is supported so the
  acquisition setup path doesn't error on the oldest TDS firmware.
* **Channel-count parsing** (:func:`channel_count_from_model`): the
  Tek naming convention encodes channel count as the last digit of
  the 4-digit numeric part of the model name (TBS1072C = 2-channel,
  TBS2204B = 4-channel). Used by the GUI's Setup tab to grey out
  unavailable channels.

Dialect differences (used by the same single driver class):

* **Modern dialect** (``TBS1000C``, ``TBS2000B/C``, ``MSO``, ``MDO``,
  ``DPO``): uses ``WFMOutpre:`` for waveform preamble queries (XINcr?,
  YMUlt?, etc.) and supports ``DATa:SOUrce <ch>`` for picking the
  channel to read.
* **Legacy dialect** (``TBS1000``, ``TBS1000B``, ``TDS2000``,
  ``TDS3000``): uses ``WFMPre:`` for the same queries; the rest of
  the command surface is shared.

Adding new models
-----------------
For a new Tek model: usually nothing is needed — the probe at
:meth:`_probe_commands` figures out the right command set. If the model
also has a non-standard channel count or new SCPI surface, add it to
the ``_DB`` in ``tektronix_models.py``. Non-Tek vendors need a
brand-new driver class implementing
:class:`stimtest.hardware.base.Oscilloscope`.

Acquisition flow
----------------
:meth:`single_capture` runs one full sequence:

    1. Start continuous acquisition: ``ACQuire:STAte RUN``.
    2. Poll ``TRIGger:STATE?`` until the scope reports it has fired, or
       we hit ``timeout_ms``.
    3. For each channel that's been configured (via
       :meth:`configure_channels`), select it with ``DATa:SOUrce``, read
       the preamble (XINcr/XZEro/YMUlt/YOFf/YZEro), pull the binary curve
       with ``CURVe?``, and convert raw integers to volts using
       Tek's standard formula: ``V = (raw - YOFf) * YMUlt + YZEro``.
    4. Pack everything into a :class:`ScopeAcquisition` and return.
"""
from __future__ import annotations

import math
import time
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from ..config import DIGITAL_DELAY_US
from .base import Oscilloscope, ScopeAcquisition, ScopeInfo


from .base import fmt_elapsed as _fmt_elapsed  # noqa: E402,F401
# Re-export the canonical formatter as the historical underscore-
# prefixed name so existing in-module references and downstream
# imports (e.g. ``from ..hardware.tektronix import _fmt_elapsed``
# in calibration.py / experiment_tabs.py) keep working without
# touching every callsite.  Definitive copy lives in
# :mod:`stimtest.hardware.base`.




def channel_count_from_model(model: str) -> Optional[int]:
    """Infer the number of input channels from a Tek model number.

    Delegates to :func:`tektronix_models.channel_count_from_model`.
    Returns ``None`` for unrecognised models so callers can fall back to a
    live SCPI probe or a safe default (4).
    """
    n = _channel_count_from_model(model)
    return n if n in (2, 4) else None


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
# Canonical value lives in :mod:`stimtest.config`; re-exported here
# so existing ``from ..hardware.tektronix import DEFAULT_RECORD_LENGTH``
# imports (e.g. gui/experiment_tabs.py) keep working unchanged.
from ..config import DEFAULT_RECORD_LENGTH  # noqa: F401,E402


from .tektronix_models import (  # noqa: E402
    get_model_spec, snap_record_length,
    TekCommandSet, MODERN_CMDS, LEGACY_CMDS,
    channel_count_from_model as _channel_count_from_model,
)


class TektronixOscilloscope(Oscilloscope):
    """pyvisa-backed scope driver."""

    def __init__(self, resource: Optional[str] = None,
                 timeout_ms: int = 10000, prefer_usb: bool = True):
        try:
            import pyvisa
        except ImportError as e:
            raise RuntimeError("pyvisa not installed; pip install pyvisa pyvisa-py") from e
        self._pyvisa = pyvisa
        import warnings
        # Cache the resources list from whichever backend succeeded so
        # ``_pick_resource()`` (called from ``open()``) doesn't have to
        # re-enumerate — each list_resources() on USB-TMC is 0.5-1.5 s.
        # Cleared after first use so a re-open re-enumerates (handles
        # the user plugging in a new scope between sessions).
        self._cached_resources: Optional[Tuple[str, ...]] = None
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            # Try each backend in order, picking the first one that can
            # enumerate at least one resource. On systems with NI-VISA +
            # libusbK (Zadig), visa32/64 sees nothing while @py finds the
            # scope via libusbK — so we probe rather than assume.
            self._rm = None
            for backend in ("@py", "C:\\Windows\\System32\\visa64.dll", ""):
                try:
                    rm = pyvisa.ResourceManager(backend) if backend else pyvisa.ResourceManager()
                    resources = tuple(rm.list_resources())
                    if resources:
                        self._rm = rm
                        self._cached_resources = resources
                        break
                    # Keep as fallback if no better option found
                    if self._rm is None:
                        self._rm = rm
                except Exception:
                    pass
            if self._rm is None:
                self._rm = pyvisa.ResourceManager("@py")
        self._inst = None
        self._resource_hint = resource
        self._prefer_usb = prefer_usb
        self._timeout_ms = timeout_ms
        self._cmds: TekCommandSet = MODERN_CMDS
        self.info = ScopeInfo()
        self.channel_aliases: Dict[str, str] = {
            "vmon": "CH1", "imon": "CH2", "eret": "CH3", "eact": "CH4",
        }
        # Cached value of HORizontal:RECOrdlength?, populated lazily on the
        # first capture and refreshed by :meth:`set_record_length`. The
        # record length doesn't change between back-to-back acquisitions, so
        # caching it saves one VISA round-trip per channel per capture.
        self._record_length: Optional[int] = None
        # Active DATa:WIDth.  HARDCODED to 1 (int8, 1 byte/sample) for
        # ALL acquisition modes and ALL scope families — see ``open``
        # for the rationale (TBS-series scopes report YOFf / YMUlt in
        # the preamble calibrated for the 8-bit ADC range regardless
        # of DATa:WIDth, so width=2 silently produces a 256× position
        # error in the conversion formula).  ``_read_channel`` reads
        # int8 unconditionally; this attribute is retained for
        # log readability only.
        self._data_width: int = 1
        # Stash of the last set acquisition mode + trigger source so a
        # periodic sanity check can detect silent firmware reverts
        # mid-experiment. Populated by ``set_acquisition_mode`` and
        # ``set_trigger`` respectively. ``None`` means the host hasn't
        # configured them yet, in which case the periodic check is a
        # no-op.
        self._expected_acq_mode: Optional[str] = None
        self._expected_acq_navg: Optional[int] = None
        self._expected_trigger_source: Optional[str] = None
        # True when the configured trigger source is a TTL digital sync
        # line — either the EXT BNC or a scope channel the operator
        # tagged with Role=Trigger (typically CH3/CH4 wired to the
        # Plexon digital sync).  Drives ``DIGITAL_DELAY_US`` application
        # in both ``_read_channel`` (time-axis shift) and
        # ``auto_layout_for_pulse`` (horizontal-position offset): the
        # Plexon digital-sync TTL fires ~1.2 µs *before* the actual
        # phase-1 stim onset regardless of which physical scope input
        # the sync wire lands on, so the 1.2 µs correction is the same
        # for EXT and channel-Trigger paths.  Defaults False; set by
        # :meth:`set_trigger`.
        self._expected_trigger_is_digital: bool = False
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
        # Time-axis cache — rebuilt only when xzero, xinc, or npts changes.
        # Keys: (xzero_s, xinc_s, npts).  The array is shared across channels
        # in the same acquisition and across back-to-back acquisitions at the
        # same timebase setting.
        self._time_cache: Optional[np.ndarray] = None
        self._time_cache_key: tuple = (float("nan"), float("nan"), 0)
        # Horizontal-divisions count.  Most Tek families use 10
        # (TBS1000, TDS200, etc.) but TBS2000B reports 15 via
        # ``HORizontal:DIVisions?`` (per the TBS2000B Programmer Manual
        # page 113).  Queried once in ``open()`` so the layout / position
        # math stays in sync with the actual display.  Default 10 is
        # what every Tek scope I've shipped against falls back to.
        self._n_horiz_divs: float = 10.0
        # Vertical-divisions count.  UNLIKE the horizontal axis, Tek
        # TBS / TDS scopes do NOT expose this via SCPI — there is no
        # ``VERTical:DIVisions?`` query.  It's fixed at 8 total divs
        # (±4 divs from screen centre) across the entire family, per
        # the TBS Programmer Manual ("The vertical scale is 8 divisions
        # tall"; the channel ``POSition`` ranges from -4.0 to +4.0
        # divs).  Hardcoded here as a class-level constant; if a
        # future Tek family changes this, override in the model
        # registry (``tektronix_models.py``).
        #
        # Used by :meth:`channel_is_clipped` and the in-view rescale
        # loop to detect when an observed (min, max) is sitting at
        # the ADC rail and the true peak is HIGHER than the captured
        # data shows.
        self._n_vert_divs: float = 8.0
        self._half_vert_divs: float = 4.0
        # Cached host-side horizontal state — these are what we LAST
        # WROTE to the scope via ``set_horizontal_scale`` /
        # ``set_horizontal_position``, NOT what the scope reports
        # back.  Used by :meth:`_read_channel` to compute the time
        # axis deterministically:
        #
        #   pre_trigger_s = (position_pct / 100) × scale_s × n_horiz_divs
        #   xzero_computed = -pre_trigger_s
        #   t_us = (xzero_computed + xinc × arange(npts)) × 1e6
        #
        # This is the SOURCE OF TRUTH for where t=0 falls on the
        # captured array, NOT the scope's ``WFMOutpre:XZEro?`` or
        # ``PT_Off?`` values (both of which have firmware quirks on
        # different TBS series).  We KNOW what we wrote — trust that.
        #
        # The position-based computation is authoritative whenever
        # both attrs are set (i.e. after the first call to
        # ``auto_layout_for_pulse`` or manual scale / position
        # writes).  When they're None (pre-setup or after a probe),
        # the legacy XZEro / PT_Off cross-check takes over so the
        # driver still produces a reasonable time axis without
        # cached state.
        self._expected_horiz_scale_s: Optional[float] = None
        self._expected_horiz_position_pct: Optional[float] = None
        # Last reported X / Y units from the preamble (e.g. "s" and "V").
        # Refreshed every time :meth:`_read_preamble` parses a batch
        # ``WFMOutpre?`` response.  Defaults chosen so the time-axis
        # logging works before the first capture.
        self._last_xunit: str = "s"
        self._last_yunit: str = "V"
        # Optional command/response logger. When set, every SCPI write
        # and query routes through this callback BEFORE/AFTER the SCPI
        # call, so the GUI's LogPane can mirror the full conversation.
        # ``None`` keeps the driver silent (default — CLI / test code).
        # The signature is ``logger(line: str) -> None``; lines are
        # already formatted with a ``[scope]`` prefix and arrow markers
        # so the user can grep them out of a noisy log.
        self.cmd_logger: Optional[Callable[[str], None]] = None

    def _log(self, msg: str) -> None:
        """Forward a line to ``self.cmd_logger`` if one is set.

        **Thread-safety contract.**  The driver MAY be called from
        any thread (the GUI's connect-button handler runs ``open()``
        on a background QThread, then per-capture ``_w`` / ``_q``
        run from either the GUI thread or an experiment worker).
        ``cmd_logger`` callbacks must therefore be safe to invoke
        from any thread.  Two patterns work:

          1. Pure-Python callback (writes to a list / queue / stdout) —
             always safe.
          2. Qt signal emit / ``QMetaObject.invokeMethod`` with
             ``Qt.QueuedConnection`` — the canonical "post to GUI
             thread" pattern.  The ConnectionPanel uses #2 (see
             ``_emit_log_from_worker``).

        Callers that wire a direct widget-mutation callback (e.g.
        ``my_qlabel.setText``) WILL crash when invoked from a non-
        GUI thread — that's a Qt invariant, not something this
        method can defend against.  Wrap the call.

        Wrapped in a try/except so a malformed logger never breaks
        an actual scope operation (the alternative would propagate
        and abort the capture in progress).
        """
        cb = self.cmd_logger
        if cb is None:
            return
        try:
            cb(msg)
        except Exception:
            pass

    # ----- discovery -----
    def list_resources(self) -> List[str]:
        return list(self._rm.list_resources())

    def _pick_resource(self) -> str:
        if self._resource_hint:
            return self._resource_hint
        # Re-use the resources list cached in __init__ when possible —
        # avoids a second list_resources() call (0.5-1.5 s) on the
        # same backend that just enumerated.  After consuming we keep
        # the cache cleared so a subsequent open() (e.g. user closed
        # then reopened) does enumerate again.
        if self._cached_resources is not None:
            candidates = list(self._cached_resources)
            self._cached_resources = None
        else:
            candidates = list(self._rm.list_resources())
        if not candidates:
            raise RuntimeError("No VISA resources found. Is NI-VISA installed and the scope on?")
        if self._prefer_usb:
            for c in candidates:
                if "USB" in c.upper():
                    return c
        return candidates[0]

    # ----- lifecycle -----
    @staticmethod
    def _is_libusb_stale_handle(exc: Exception) -> bool:
        """True if ``exc`` matches the libusb-win32 ``stale-handle''
        signature.

        Symptom (verbatim from a real failure):

        ::

            [Errno None] b'libusb0-dll:err [control_msg] sending
            control message failed, win error: The semaphore timeout
            period has expired.\\r\\n\\n'

        Trigger: a previous PULSAR process (typically one that
        crashed mid-run — see CLAUDE.md gotcha #28 / HEAP_CORRUPTION)
        left the scope's USB control endpoint claimed at the
        kernel level.  Windows eventually drops the claim, but it
        can take seconds to minutes — far longer than the operator
        is willing to wait.  Re-enumeration via a power-cycle of
        the scope clears it instantly; so does a short backoff +
        retry (because the second attempt finds the kernel has
        finally garbage-collected the dead claim).

        Matched substrings (case-insensitive, ANY hit returns True):

        * ``"semaphore timeout"`` — Windows' literal error text
        * ``"libusb0-dll:err"`` — libusb-win32 driver prefix
        * ``"control_msg"`` paired with ``"libusb"`` — the
          control-message path specifically (not all libusb
          errors are retryable; this one is)

        Conservative: returns False for any error not matching the
        signature, so non-libusb VISA errors (USBTMC violations,
        scope-side malformed responses, etc.) still propagate
        normally.
        """
        s = repr(exc).lower()
        if "semaphore timeout" in s:
            return True
        if "libusb0-dll:err" in s:
            return True
        if "libusb" in s and "control_msg" in s:
            return True
        return False

    def _release_partial_visa_session(self) -> None:
        """Close a half-opened VISA session + clear cached resources.

        Called between retries in ``open()`` so the next
        ``open_resource`` lands on a clean slate.  Best-effort — any
        close error is suppressed because the underlying handle may
        already be dead.
        """
        try:
            if self._inst is not None:
                self._inst.close()
        except Exception:
            pass
        self._inst = None
        # Force re-enumeration on the next pick_resource — the cached
        # list is from BEFORE the stale-handle cleared, so it may
        # still contain a now-invalid handle reference.
        self._cached_resources = None

    def open(self, resource: Optional[str] = None) -> None:
        # Total elapsed for the whole open() — surfaced at the end so
        # the user can see at-a-glance how long connecting took.
        _open_t0 = time.perf_counter()
        self._log("=" * 64)
        self._log("[scope] === Initializing oscilloscope ===")
        if resource:
            self._resource_hint = resource
        # ---- Step 1: pick + open VISA resource --------------------
        self._log("[scope] Step 1/6: Open VISA resource")
        _t0 = time.perf_counter()
        rsrc = self._pick_resource()
        self._log(f"[scope] pick_resource: {rsrc}   "
                  f"({_fmt_elapsed(time.perf_counter() - _t0)})")
        # ---- Steps 1b + 2 in a retry loop -------------------------
        # ``open_resource`` AND the first ``*IDN?`` can fail with the
        # libusb-win32 stale-handle error (see
        # :meth:`_is_libusb_stale_handle` docstring for the symptom).
        # The trigger is a previously crashed PULSAR process that left
        # the scope's USB control endpoint claimed at the kernel level
        # — Windows eventually drops the claim, but the operator hits
        # Connect long before that happens.  Retrying with a short
        # backoff usually works because the second attempt finds the
        # kernel has finally garbage-collected the dead claim.
        #
        # Three total attempts (initial + 2 retries) with exponential
        # backoff (1 s, 2 s, 4 s).  Total worst-case wall time
        # ≈ 7 s + per-attempt USB latency — bounded so a genuinely
        # broken device still fails fast for the operator.
        LIBUSB_BACKOFFS = (1.0, 2.0, 4.0)
        idn: Optional[str] = None
        _last_exc: Optional[Exception] = None
        for _retry_attempt, _backoff_s in enumerate(LIBUSB_BACKOFFS):
            try:
                _t0 = time.perf_counter()
                self._inst = self._rm.open_resource(rsrc)
                self._log(f"[scope] open_resource (VISA session)   "
                          f"({_fmt_elapsed(time.perf_counter() - _t0)})")
                self._inst.timeout = self._timeout_ms
                self._inst.write_termination = "\n"
                self._inst.read_termination = "\n"
                self._log(
                    f"[scope] VISA session config: "
                    f"timeout={self._timeout_ms} ms, "
                    f"write_term='\\n', read_term='\\n'")
                # ---- Step 2: identify device (*IDN?) --------------
                self._log("[scope] Step 2/6: Identify device (*IDN?)")
                idn = self._q("*IDN?")
                # Success — break out of the retry loop.
                if _retry_attempt > 0:
                    self._log(
                        f"[scope]   ✓ recovered on attempt "
                        f"{_retry_attempt + 1}/{len(LIBUSB_BACKOFFS)} "
                        f"after the libusb stale-handle cleared.")
                break
            except Exception as _open_exc:
                _last_exc = _open_exc
                # Non-libusb errors: propagate immediately — no
                # point retrying a malformed VISA resource string
                # or a missing device.
                if not self._is_libusb_stale_handle(_open_exc):
                    raise
                # Last attempt: give up + raise with operator-
                # friendly guidance baked into the message.
                if _retry_attempt >= len(LIBUSB_BACKOFFS) - 1:
                    self._log(
                        f"[scope]   ✗ libusb stale-handle persisted "
                        f"after {len(LIBUSB_BACKOFFS)} attempts.  "
                        f"Power-cycle the scope (front-panel power "
                        f"off → on) and unplug + replug the USB "
                        f"cable, then retry Connect.")
                    raise RuntimeError(
                        f"Scope USB endpoint is stuck (libusb "
                        f"stale-handle persisted after "
                        f"{len(LIBUSB_BACKOFFS)} retries).  "
                        f"Power-cycle the scope + replug USB, then "
                        f"retry.  Original error: "
                        f"{type(_open_exc).__name__}: {_open_exc!r}"
                    ) from _open_exc
                # Retry — log + clean up + sleep + go around.
                self._log(
                    f"[scope]   ⚠ libusb stale-handle on attempt "
                    f"{_retry_attempt + 1}/{len(LIBUSB_BACKOFFS)}: "
                    f"{type(_open_exc).__name__}: {_open_exc!r}")
                self._log(
                    f"[scope]     Likely a previous PULSAR process "
                    f"(often one that crashed mid-run) left the USB "
                    f"control endpoint half-claimed at the kernel "
                    f"level.  Retrying after {_backoff_s:.1f} s "
                    f"backoff while Windows garbage-collects the "
                    f"dead claim.")
                self._release_partial_visa_session()
                time.sleep(_backoff_s)
        # If we exited the loop without success AND without the
        # last-attempt raise (defensive — shouldn't happen), surface
        # the last captured exception so the operator gets a real
        # error rather than a silent failure downstream.
        if idn is None:
            raise RuntimeError(
                f"Scope open failed without a recoverable error path "
                f"(last exception: {_last_exc!r})")
        parts = [p.strip() for p in idn.split(",")]
        make = parts[0] if len(parts) > 0 else ""
        model = parts[1] if len(parts) > 1 else ""
        serial = parts[2] if len(parts) > 2 else ""
        firmware = parts[3] if len(parts) > 3 else ""
        self._log(
            f"[scope] identified: make={make!r}, model={model!r}, "
            f"serial={serial!r}, firmware={firmware!r}")
        # Channel count — start from the model number (Tek's naming
        # convention encodes the count unambiguously: TBS2204B → 4 ch,
        # TBS1052C → 2 ch), then verify against a live SCPI probe.
        # Unknown models (MSO/MDO/DPO variants, OEM rebrands, future
        # families) get the inferred value as a starting hint and the
        # live probe as the authoritative answer.
        spec = get_model_spec(model)
        n_ch_inferred = (spec.n_channels if spec else None) or \
            channel_count_from_model(model) or 4
        # Live probe: query CH<n>:PRObe:GAIN? for n = 1..8 and count
        # successful responses.  This is the authoritative source —
        # the model string is just an opening guess.  If the probe
        # disagrees with the model string (off by one, OEM rebrand,
        # malformed *IDN?), the probe wins and we log the mismatch
        # so it's visible without having to read source.
        _t0 = time.perf_counter()
        try:
            n_ch_probed = self.probe_channel_count(max_channels=8)
        except Exception as e:
            self._log(f"[scope]   ⚠ channel-count probe raised "
                      f"{type(e).__name__}: {e!r} — falling back to "
                      f"model-string inference ({n_ch_inferred} ch)")
            n_ch_probed = 0
        if n_ch_probed in (2, 4):
            n_ch = n_ch_probed
            if n_ch_probed != n_ch_inferred:
                self._log(
                    f"[scope]   ⚠ channel-count mismatch: model "
                    f"{model!r} parsed as {n_ch_inferred} ch but "
                    f"SCPI probe found {n_ch_probed} ch — trusting "
                    f"the probe.  Update tektronix_models.py if this "
                    f"model needs a new entry.")
            else:
                self._log(
                    f"[scope] channel count: {n_ch} (model + live "
                    f"probe agree)   "
                    f"({_fmt_elapsed(time.perf_counter() - _t0)})")
        else:
            # Probe returned something nonsensical (0, 1, 3, 5+) —
            # fall back to the model-string value.  Most likely the
            # scope had a stale error queue or the firmware doesn't
            # answer CH<n>:PRObe:GAIN?.
            n_ch = n_ch_inferred
            self._log(
                f"[scope]   ⚠ channel-count probe returned "
                f"{n_ch_probed} (expected 2 or 4) — falling back to "
                f"model-string inference ({n_ch_inferred} ch)")
        self.info = ScopeInfo(
            make=make, model=model, serial=serial, firmware=firmware,
            resource=rsrc, n_channels=n_ch, is_simulated=False,
        )
        # ---- Step 3: probe SCPI dialect --------------------------
        self._log("[scope] Step 3/6: Probe SCPI dialect "
                  "(preamble form, NUMAVg, horiz-position form)")
        _t0 = time.perf_counter()
        self._cmds = self._probe_commands(spec=spec, fallback_model=model)
        self._log(f"[scope] _probe_commands   "
                  f"({_fmt_elapsed(time.perf_counter() - _t0)})")
        # EXT-trigger presence from DB when known; live probe for unknown models.
        if spec is not None:
            self.info.has_ext_trigger = spec.has_ext_trigger
            self._log(
                f"[scope] EXT trigger support (from DB): "
                f"{self.info.has_ext_trigger}")
        else:
            self._log("[scope] probing EXT trigger support live…")
            _t0 = time.perf_counter()
            try:
                self.info.has_ext_trigger = self.probe_external_trigger()
            except Exception:
                # Default to False on probe failure — same rationale as
                # ScopeInfo.has_ext_trigger default.  Better to hide the
                # EXT toggle and let the operator pick a channel
                # trigger than to expose a non-existent BNC.
                self.info.has_ext_trigger = False
            self._log(
                f"[scope] EXT trigger support: "
                f"{self.info.has_ext_trigger}   "
                f"({_fmt_elapsed(time.perf_counter() - _t0)})")
        # ---- Step 4: SCPI defaults -------------------------------
        self._log("[scope] Step 4/6: Default scope state "
                  "(HEADer OFF, VERBose ON)")
        self._w("HEADer OFF")
        self._w("VERBose ON")
        # ---- Step 5: read immutable horizontal-divisions property -
        # Only the division count — it's a *fixed* model property
        # (TBS2000B = 15, most other families = 10) used by our layout
        # math, so we have to know it before any capture.  We don't
        # read horizontal scale/position/record-length, trigger
        # source/level/mode, or per-channel V/div here because the
        # Setup and Experiment tabs overwrite all of those when the
        # user starts a run — querying them at open() would just be
        # noise in the log.
        self._log("[scope] Step 5/6: Read horizontal division count "
                  "& look up vertical division count from model spec")
        try:
            self._n_horiz_divs = float(self._q("HORizontal:DIVisions?"))
            if not (1.0 <= self._n_horiz_divs <= 20.0):
                self._n_horiz_divs = 10.0   # sanity: ignore absurd values
        except Exception:
            self._n_horiz_divs = 10.0
        # Vertical-divisions — NOT queryable via SCPI on TBS/TDS/TPS
        # families (no ``VERTical:DIVisions?`` command exists).  Look
        # it up in the per-series spec instead.  TBS2000* family uses
        # 10 vert divs; everything else uses 8.  Critical for the
        # in-view rescale loop's clip detector AND for the runner's
        # ``MAX_FACTOR`` budget — using the wrong value silently
        # under-uses the screen (treating a TBS2204B's ±5-div display
        # as ±4 divs hides 25 % of the usable vertical headroom and
        # makes the "trace doesn't fit" check trip prematurely).
        #
        # MATLAB ``getWaveform2.m`` baked in 4.0 (and used 3.5-4.5 as
        # ``MAX_FACTOR`` candidates) because it targeted the TBS1104B
        # (8 vert divs); running the same code on a TBS2204B (10 vert
        # divs) without adapting MAX_FACTOR would leave the trace at
        # only ~80 % of the screen height for no reason.
        from .tektronix_models import get_series_spec
        _spec = get_series_spec(self.info.model or "")
        if _spec is not None:
            self._n_vert_divs = float(_spec.n_vert_divs)
            self._half_vert_divs = self._n_vert_divs / 2.0
            # If the per-series spec disagrees with the queried
            # horizontal count, prefer the SCPI answer (the scope
            # itself is the source of truth for horiz; the spec is
            # a fallback) but log the discrepancy.
            if (abs(self._n_horiz_divs - float(_spec.n_horiz_divs)) > 0.1):
                self._log(
                    f"[scope] note: SCPI HORizontal:DIVisions? = "
                    f"{self._n_horiz_divs:.0f} differs from model-spec "
                    f"{_spec.series_name} default {_spec.n_horiz_divs}; "
                    f"using SCPI value.")
        self._log(
            f"[scope] display grid: "
            f"{self._n_horiz_divs:.0f} horizontal × "
            f"{self._n_vert_divs:.0f} vertical divs "
            f"(±{self._half_vert_divs:.1f} from screen centre)")
        # Binary transfer: signed int8, MSB-first.
        # DATa:ENCdg RIBinary — R=MSB-first, I=signed integer, Binary=raw bytes.
        # DATa:WIDth is fixed at 1 (int8) for ALL acquisition modes, matching
        # the MATLAB reference (getWaveform_Tek.m / getCurve.m always use
        # DATa:WIDth 1).  The reason: Tektronix TBS-series scopes report YOFf
        # and YMUlt in the preamble calibrated for the 8-bit ADC range regardless
        # of DATa:WIDth.  When Width=2 the raw values are 256× larger but the
        # preamble offsets are not rescaled, so the formula
        #   (raw − YOFf) × YMUlt + YZEro
        # produces a factor-of-256 position error.  Staying at Width=1 keeps the
        # formula correct and matches MATLAB.  Coherent averaging of N int8
        # frames still gives sub-LSB precision through statistics.
        # ---- Step 6: configure data transfer protocol ------------
        # These three are the only writes that must stick across the
        # whole session — they tell the scope how every later CURVe?
        # response is encoded.  The Setup/Experiment tabs do NOT
        # touch them, so they belong here (not redundant).
        self._log("[scope] Step 6/6: Configure data transfer protocol "
                  "(DATa:ENCdg / WIDth / STARt)")
        self._w(self._cmds.data_encoding_cmd)
        self._w("DATa:WIDth 1")
        self._data_width = 1
        # DATa:STARt/STOP define the slice of the record to transfer. They
        # don't change between captures (we always want the full record),
        # so set once at open and let set_record_length() refresh on demand.
        self._w("DATa:STARt 1")
        self._record_length = None
        # NOTE: record length is intentionally NOT set here — the
        # Setup/Experiment/Calibration tabs each call
        # ``set_record_length`` with the value appropriate for that
        # run (2000 for calibration, 20 000 for experiments).  Writing
        # a placeholder during open() would just be overwritten a
        # moment later and clutter the connect log.  We still
        # ``_refresh_record_length`` cheaply so the cached value
        # matches whatever the front panel last had — without it the
        # very first downstream query sees ``None`` and round-trips
        # the scope.
        try:
            self._refresh_record_length()
        except Exception:
            pass
        # ---- Probe survey: log gain + type for every channel, warn
        # ---- if a real probe (not BNC) is reported anywhere.
        # ---- Hardware state — NOT overwritten by Setup / Experiment
        # ---- tabs, so it belongs at connect time per CLAUDE.md §3.
        # ---- The bench expects direct BNC on every input; a probe
        # ---- attached anywhere is worth surfacing once, here, rather
        # ---- than only warning later when the experiment runner
        # ---- happens to touch that channel.
        try:
            self._log_probe_survey()
        except Exception as e:
            self._log(f"[scope]   probe survey skipped: {e}")
        # Total open() elapsed.  Useful for spotting regression when
        # we add SCPI calls to the open path — most of the time should
        # be the initial open_resource + _probe_commands phases.
        self._log(
            f"[scope] === Initialization complete in "
            f"{_fmt_elapsed(time.perf_counter() - _open_t0)} ===")
        self._log("=" * 64)

    def _log_probe_survey(self) -> None:
        """One-shot scan of every channel's probe gain + type at connect.

        Emits one log line per channel ("gain=1, type=OTHER" → BNC,
        anything else → real probe), plus a ⚠ summary if any channel
        reports a real probe.  Cheap (≈2 queries × n_channels) and
        runs once per connect — see ``open()`` for the call site.
        """
        n_ch = int(getattr(self.info, "n_channels", 4) or 4)
        flagged: List[str] = []
        for i in range(1, n_ch + 1):
            ch = f"CH{i}"
            info = self.probe_info(ch)
            type_tok = info.get("type") or "—"
            gain = info.get("gain", 1.0)
            is_probe = bool(info.get("is_probe"))
            marker = " ⚠ probe" if is_probe else ""
            self._log(
                f"[scope]   {ch} probe: gain={gain:g}, "
                f"type={type_tok}{marker}")
            if is_probe:
                flagged.append(f"{ch} ({type_tok}, {gain:g}x)")
        if flagged:
            self._log(
                f"[scope]   ⚠ probes attached on: {', '.join(flagged)}.  "
                f"Bench convention is direct BNC → BNC on every input — "
                f"a real probe will make displayed volts disagree with "
                f"the BNC-tip voltage once apply_channel_defaults "
                f"forces PRObe:GAIN 1.")

    def close(self) -> None:
        if self._inst is not None:
            self._log("[scope] close")
            try:
                self._inst.close()
            except Exception:
                pass
            self._inst = None
        # Forget the resource we used and the cached enumeration so
        # the next ``open()`` re-discovers what's actually present.
        # Without this, a USB unplug-then-replug (which often changes
        # the resource string) would either get a stale hint or a
        # stale cached enumeration list and fail with a cryptic
        # "device not responding" instead of finding the new instance.
        self._resource_hint = None
        self._cached_resources = None

    # ----- helpers -----
    def _w(self, cmd: str) -> None:
        if self._inst is None: raise RuntimeError("Scope not open")
        t0 = time.perf_counter()
        self._inst.write(cmd)
        self._log(f"[scope] > {cmd}   ({_fmt_elapsed(time.perf_counter() - t0)})")

    def _q(self, cmd: str) -> str:
        if self._inst is None: raise RuntimeError("Scope not open")
        t0 = time.perf_counter()
        resp = self._inst.query(cmd).strip()
        self._log(f"[scope] > {cmd}   < {resp}   "
                  f"({_fmt_elapsed(time.perf_counter() - t0)})")
        return resp

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
        t0 = time.perf_counter()
        self._inst.write(cmd)
        self._log(f"[scope] > {cmd}   ({_fmt_elapsed(time.perf_counter() - t0)})")
        try:
            t0 = time.perf_counter()
            err = self._inst.query("SYSTem:ERRor?").strip()
            self._log(f"[scope] > SYSTem:ERRor?   < {err}   "
                      f"({_fmt_elapsed(time.perf_counter() - t0)})")
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

    # ----- capability probing ------------------------------------------
    def _probe_commands(self, *, spec=None,
                        fallback_model: str = "") -> TekCommandSet:
        """Auto-detect the Tek SCPI command set from live probe queries.

        Two dialect bases exist:

        * **Modern** (``MODERN_CMDS``) — TBS1000C, TBS2000B/C, MSO/MDO/DPO:
          ``WFMOutpre``, ``TRIGger:A:…``, ``HORizontal:POSition`` in %.
        * **Legacy** (``LEGACY_CMDS``) — TBS1000 / TBS1000B / TDS2000/3000:
          ``WFMPre``, ``TRIGger:MAIn:…``, ``HORizontal:MAIN:POSition`` in s.

        We probe three fields at runtime because firmware doesn't always
        match the naming convention, and model-DB entries for unknown
        models aren't available:

        1. **Preamble** (``WFMOutpre`` vs ``WFMPre``) — drives the entire
           base-dialect choice including trigger namespace.
        2. **NUMAVg** (``ACQuire:NUMAVg?``) — oldest TDS firmware lacks it.
        3. **Horiz-position form** (``HORizontal:POSition?``) — some
           legacy-preamble firmware still accepts the percent form.

        When *spec* is not ``None`` (known model in the DB), ``has_acq_numavg``
        is taken directly from ``spec.commands`` and the live probe is skipped.

        Always succeeds: falls back to ``MODERN_CMDS`` when the scope is
        unreachable so ``open()`` is never blocked by a probe failure.
        """
        import dataclasses

        if self._inst is None:
            return spec.commands if spec is not None else MODERN_CMDS

        # Helper: probe with logging so every command sent during the
        # connect handshake shows up in the log/.txt file.  Mirrors the
        # MATLAB connect-time printout where every fprintf and fgetl is
        # logged with elapsed time.
        def _logged_query(cmd: str) -> str:
            t0 = time.perf_counter()
            try:
                resp = self._inst.query(cmd).strip()
                self._log(f"[scope] > {cmd}   < {resp}   "
                          f"({_fmt_elapsed(time.perf_counter() - t0)})")
                return resp
            except Exception as e:
                self._log(f"[scope] > {cmd}   < ERROR: {e}   "
                          f"({_fmt_elapsed(time.perf_counter() - t0)})")
                raise

        def _logged_write(cmd: str) -> None:
            t0 = time.perf_counter()
            self._inst.write(cmd)
            self._log(f"[scope] > {cmd}   "
                      f"({_fmt_elapsed(time.perf_counter() - t0)})")

        def _probes_ok(prefix: str) -> bool:
            try:
                resp = _logged_query(f"{prefix}:NR_Pt?")
                int(float(resp))
                return True
            except Exception:
                try:
                    _logged_write("*CLS")
                except Exception:
                    pass
                return False

        # Preamble probe → entire base dialect (trigger namespace, etc.)
        if _probes_ok("WFMOutpre"):
            base = MODERN_CMDS
        elif _probes_ok("WFMPre"):
            base = LEGACY_CMDS
        else:
            base = spec.commands if spec is not None else MODERN_CMDS

        # NUMAVg — use DB value when available; live-probe otherwise.
        if spec is not None:
            has_acq_numavg = spec.commands.has_acq_numavg
        else:
            try:
                _logged_query("ACQuire:NUMAVg?")
                has_acq_numavg = True
            except Exception:
                try:
                    _logged_write("*CLS")
                except Exception:
                    pass
                has_acq_numavg = False

        # Horiz-position form — probe beats dialect base for edge cases
        # where firmware doesn't match the naming convention.
        try:
            val = _logged_query("HORizontal:POSition?")
            float(val)
            horiz_position = "HORizontal:POSition"
            horiz_position_unit = "percent"
        except Exception:
            try:
                _logged_write("*CLS")
            except Exception:
                pass
            horiz_position = base.horiz_position
            horiz_position_unit = base.horiz_position_unit

        return dataclasses.replace(
            base,
            has_acq_numavg=has_acq_numavg,
            horiz_position=horiz_position,
            horiz_position_unit=horiz_position_unit,
        )

    def probe_channel_count(self, max_channels: int = 8) -> int:
        """Detect the number of analog input channels on the live scope.

        *IDN?* doesn't carry a channel count and there's no portable
        Tek SCPI for it (``CH:LIST?`` is GPIB-era and inconsistent
        across firmware). The reliable probe is to query a per-channel
        attribute and count which channels respond without an error:

          * Try ``CH<n>:PRObe:GAIN?`` for n = 1..max_channels.
          * A scope with N channels answers cleanly for n ≤ N and
            raises an "Undefined header" / "Invalid argument" error
            for n > N. The error queue is the signal — we drain it
            after each probe and stop counting at the first miss.

        Returns 0 on probe failure (no instrument, total SCPI
        breakdown) so the caller can fall back to the regex.

        Walks 1..8 by default — Tek's catalog tops out at 8-channel
        scopes (MSO5K/6K series). Increase the cap only if a future
        model exceeds that.
        """
        if self._inst is None:
            return 0
        # Drain any pre-existing error before we start counting; an
        # error queued from earlier setup would otherwise look like
        # "channel 1 doesn't exist" on the first probe.
        try:
            self._inst.write("*CLS")
        except Exception:
            return 0
        count = 0
        for n in range(1, int(max_channels) + 1):
            try:
                # A query that returns a number on success and an
                # error on a non-existent channel. Probe GAIN rather
                # than SELect:CH<n>? because SELect is a setting that
                # some firmware echoes back even for non-existent
                # channels.
                resp = self._inst.query(f"CH{n}:PRObe:GAIN?").strip()
                # Some firmware returns the literal error string
                # rather than queueing an error. "Undefined header"
                # / "Invalid" / a leading minus + colon in the
                # response → channel doesn't exist.
                if not resp or any(s in resp.lower() for s in
                                    ("undefined", "invalid", "error",
                                     "execution")):
                    break
                # Should parse as a numeric attenuation (e.g. 0.1,
                # 1, 10, 100). If it doesn't, treat as miss.
                float(resp)
                count += 1
            except Exception:
                # Clear the error so the next probe starts clean,
                # then stop counting at the first miss.
                try:
                    self._inst.write("*CLS")
                except Exception:
                    pass
                break
        # Final *CLS to leave the error queue clean for downstream
        # setup code (DATa:ENCdg etc.).
        try:
            self._inst.write("*CLS")
        except Exception:
            pass
        return count

    def probe_external_trigger(self) -> bool:
        """Detect whether the scope has an EXT trigger BNC input.

        TBS2000B / TBS2000C / MSO / MDO / DPO scopes have an external
        trigger input on the rear BNC; TBS1000C and TBS1000B-EDU
        (the 2-channel basic scopes) do *not* — their valid trigger
        sources are CH1, CH2, and AC LINE only.

        Probe technique: save the current ``TRIGger:A:EDGE:SOUrce``,
        try setting it to ``EXT``, read back. If the scope accepted
        the value the readback returns ``EXT``; if it didn't, the
        readback either stays at the previous source or returns the
        scope's default fallback (typically ``CH1``). Either way we
        treat anything other than ``EXT`` as "no EXT input".

        Always restores the previous source on exit so an open()
        probe doesn't disrupt user-configured trigger state.
        Best-effort: any SCPI failure returns ``False`` so the GUI
        falls back to the channel-trigger path.
        """
        if self._inst is None:
            return False
        src_cmd = self._cmds.trig_edge_source

        def _strip_header(raw: str) -> str:
            """Pull just the source token out of a TRIGger?-style reply.

            ``probe_external_trigger`` runs BEFORE ``HEADer OFF`` is
            sent in :meth:`open`, so the scope's reply is the verbose
            form ``:TRIGGER:A:EDGE:SOURCE CH1`` instead of just
            ``CH1``.  Naïvely writing that back through ``f"{src_cmd}
            {prev}"`` produces malformed SCPI (the path appears
            twice) and the scope silently keeps whatever we set
            during the probe — typically AUX, because TBS2000-series
            firmware aliases EXT to AUX (Tek's external trigger BNC
            is named AUX in the SCPI namespace).  The user-visible
            symptom is "trigger ends up on AUX even though I asked
            for CHn" — the probe's failed restore left it stuck.
            We split on whitespace and take the last token, which is
            the actual source name in both verbose and bare forms.
            """
            if not raw:
                return ""
            return raw.split()[-1].strip()

        try:
            prev_raw = self._q(f"{src_cmd}?").strip()
        except Exception:
            return False
        prev = _strip_header(prev_raw)
        result = False
        try:
            try:
                self._inst.write("*CLS")
            except Exception:
                pass
            self._inst.write(f"{src_cmd} EXT")
            try:
                got_raw = self._q(f"{src_cmd}?").strip()
            except Exception:
                got_raw = ""
            got = _strip_header(got_raw).upper()
            # Require a literal ``EXT`` readback.  An earlier revision
            # also accepted ``AUX`` because TBS2000-series firmware
            # reports the EXT BNC as ``AUX`` — but that's now handled
            # by the model-spec database (TBS2000B / TBS2000C carry
            # ``has_ext_trigger = True`` in :data:`TEK_SERIES_SPECS`)
            # so the live probe doesn't run for those scopes.  Keeping
            # AUX-acceptance here means an *unknown* no-EXT scope whose
            # firmware happens to speak ``TRIG:SOURce EXT/AUX`` in the
            # SCPI namespace (silently aliasing to a non-existent BNC)
            # also looks positive — producing a phantom EXT toggle on
            # a scope that has nothing to plug into.  Strict matching
            # fails closed for unknown models, which is the safer
            # default per CLAUDE.md: any scope that legitimately has
            # EXT should be added to the spec DB so it doesn't depend
            # on this probe at all.
            result = got.startswith("EXT")
        finally:
            if prev:
                try:
                    self._inst.write(f"{src_cmd} {prev}")
                except Exception:
                    pass
            try:
                self._inst.write("*CLS")
            except Exception:
                pass
        return result

    # ----- configuration -----
    def set_channel_scale(self, channel: str, volts_per_div: float) -> None:
        self._w(f"{channel}:SCAle {volts_per_div:g}")
        if hasattr(self, "_adapt_state"):
            self._adapt_state.setdefault(
                channel, {"shrink_count": 0, "last_scale": None}
            )["last_scale"] = float(volts_per_div)

    def set_channel_scale_for_peak(self, channel: str,
                                   peak_v: float, *,
                                   divs: float = 4.0) -> Optional[float]:
        """Direct, hysteresis-free V/div sizing — pick the smallest grid
        scale that fits ``±peak_v`` in ``divs`` divisions.

        Mirrors the calibration plot's per-step pattern::

            scope.set_channel_scale(v_mon_phys, _vmon_vertical_scale(amp))

        but takes an *observed* peak (e.g. ``max(abs(trace.min()),
        abs(trace.max()))`` after a capture) instead of a formula-
        based expected peak — useful for experiments where the
        electrode impedance isn't known up front.

        Difference vs :meth:`adapt_channel_scale`:

        * **No shrink-hysteresis** — converges in one step regardless
          of direction (grow or shrink).  Calibration uses the same
          direct approach because amplitude changes monotonically
          across the sweep and there's no value in waiting for two
          consecutive votes before tightening.
        * **No repeat-detection lock** — every call can move the
          scale.  Suitable for runners that ramp amplitude (VT) or
          step through configurations (calibration); not for runners
          that re-acquire the same nominally-static signal where
          noisy edges might oscillate the scale.

        Returns the new V/div written, or ``None`` if no change was
        needed (current scale already optimal).
        """
        peak_abs = abs(float(peak_v))
        # Floor at the grid minimum so a tiny signal doesn't ask for
        # a scale narrower than the scope can deliver.
        ideal = self._snap_to_grid(
            max(peak_abs / max(float(divs), 1.0),
                self._TEK_VERTICAL_GRID_VPD[0]),
            self._TEK_VERTICAL_GRID_VPD, direction="ceil")
        # Use the cached value when available; otherwise read once.
        current = None
        if hasattr(self, "_adapt_state"):
            current = self._adapt_state.get(channel, {}).get("last_scale")
        if current is None:
            try:
                current = float(self._q(f"{channel}:SCAle?"))
            except Exception:
                current = None
        if current is not None and abs(ideal - current) <= 1e-12:
            return None
        self.set_channel_scale(channel, ideal)
        return ideal

    def channel_is_clipped(self, channel: str,
                           v_min: float, v_max: float,
                           *, margin_pct: float = 0.05) -> Optional[bool]:
        """Detect ADC-rail saturation on ``channel``.

        Queries the channel's current V/div + POSition and computes
        the physical rail voltages (``±_half_vert_divs × vpd`` around
        ``vertPos = -pos_divs * vpd``).  Returns True if ``v_max`` is
        within ``margin_pct`` of the top rail OR ``v_min`` is within
        ``margin_pct`` of the bottom rail.

        Critical for the in-view rescale loop's "should I expand?"
        decision: a saturated trace's observed (min, max) EQUALS the
        rail voltage regardless of the true signal peak, so sizing
        the new V/div from the observed range produces the same V/div
        as before and never expands.  Clip detection forces the loop
        into the COARSE-step-up branch (multiply V/div by a fixed
        factor) instead of the fine-fit branch, which gives room
        for the next capture to reveal the true peak.

        ``margin_pct`` (default 5 %) — fraction of the half-window
        at which a value counts as "at the rail".  5 % gives a 200 mV
        margin on a ±4 V window — wide enough to ignore noise
        flickering above a real peak, tight enough to catch genuine
        saturation.

        Returns ``True`` / ``False`` / ``None`` (last on SCPI error
        or malformed reply).
        """
        try:
            vpd = float(self._q(f"{channel}:SCAle?"))
            pos_divs = float(self._q(f"{channel}:POSition?"))
        except Exception:
            return None
        if not (np.isfinite(vpd) and np.isfinite(pos_divs)) or vpd <= 0.0:
            return None
        try:
            v_lo = float(v_min)
            v_hi = float(v_max)
        except (TypeError, ValueError):
            return None
        if not (np.isfinite(v_lo) and np.isfinite(v_hi)):
            return None
        if v_hi < v_lo:
            v_lo, v_hi = v_hi, v_lo
        # Physical rail voltages.  vertPos is the volts-space
        # coordinate of screen centre AFTER the POSition offset.
        vert_pos_v = -pos_divs * vpd
        rail_top = +self._half_vert_divs * vpd + vert_pos_v
        rail_bot = -self._half_vert_divs * vpd + vert_pos_v
        # Margin: e.g. 5 % of the half-window from each rail.
        half_window = self._half_vert_divs * vpd
        margin = float(margin_pct) * half_window
        clipped_top = v_hi >= (rail_top - margin)
        clipped_bot = v_lo <= (rail_bot + margin)
        return bool(clipped_top or clipped_bot)

    def channel_in_view(self, channel: str,
                        v_min: float, v_max: float,
                        *, margin_divs: float = 3.9) -> Optional[bool]:
        """SCPI in-view check — port of MATLAB ``getWaveform2.m`` lines
        307-362.

        Queries the channel's current V/div + POSition, builds the
        visible window in volts, and tests whether the observed
        ``[v_min, v_max]`` falls inside it.  The runner uses the return
        value to drive an iterative rescale + re-capture loop so the
        operator never sees a clipped trace.

        Window math::

            vertPos    = -pos_divs * vpd
            window_min = -margin_divs * vpd + vertPos
            window_max = +margin_divs * vpd + vertPos
            in_view    = (v_min > window_min) and (v_max < window_max)

        ``margin_divs`` defaults to **3.9** (MATLAB ``MAX_FACTOR``) — one
        tenth of a division shy of the scope's ±4-div edge so a trace
        that just touches the grid still flags as out-of-view.  The
        runner then rescales to fit it comfortably.

        Returns:
          * ``True``  — trace fits comfortably.
          * ``False`` — trace would clip; rescale needed.
          * ``None``  — scope refused / returned a malformed reply.
            Callers treat ``None`` like ``False`` would be unsafe (we'd
            rescale on every capture for a scope that briefly stalled);
            instead the convention is "can't tell" — the runner falls
            back to a single-pass fit-the-range write without iterating.
        """
        try:
            vpd = float(self._q(f"{channel}:SCAle?"))
            pos_divs = float(self._q(f"{channel}:POSition?"))
        except Exception:
            return None
        if not (np.isfinite(vpd) and np.isfinite(pos_divs)) or vpd <= 0.0:
            return None
        try:
            v_lo = float(v_min)
            v_hi = float(v_max)
        except (TypeError, ValueError):
            return None
        if not (np.isfinite(v_lo) and np.isfinite(v_hi)):
            return None
        if v_hi < v_lo:
            v_lo, v_hi = v_hi, v_lo
        margin = max(float(margin_divs), 0.0)
        # MATLAB ``getWaveform2.m``::
        #     vertPos        = -pos_factor * vertScale
        #     range_min_chk  = MAX_FACTOR * -vertScale + vertPos
        #     range_max_chk  = MAX_FACTOR *  vertScale + vertPos
        # A positive position (front-panel knob turned UP) shifts the
        # waveform's baseline UPWARD on screen — i.e. its electrical
        # midpoint sits BELOW screen-centre.  The volts-space window
        # therefore centres on ``-pos_divs * vpd``.
        vert_pos_v = -pos_divs * vpd
        win_lo = -margin * vpd + vert_pos_v
        win_hi = +margin * vpd + vert_pos_v
        return (v_lo > win_lo) and (v_hi < win_hi)

    def channel_clip_sides(self, channel: str,
                           v_min: float, v_max: float,
                           *, margin_divs: float = 3.9
                           ) -> Optional[Tuple[bool, bool, float, float, float]]:
        """Directional companion to :meth:`channel_in_view`.

        Returns a 5-tuple ``(below_out, above_out, pos_shift_divs,
        headroom_below_div, headroom_above_div)`` so the caller can
        decide between three possible actions:

        * ``(False, False, ...)`` — trace is fully in view, no
          adjustment needed.
        * ``(True,  False, ...)`` — trace exceeds the BOTTOM of the
          window only; the top half has slack.  A POSITION-only
          nudge by ``pos_shift_divs`` will recentre the trace
          without touching V/div (preserves ADC resolution).
        * ``(False, True,  ...)`` — trace exceeds the TOP only;
          same nudge fix, opposite sign.
        * ``(True,  True,  ...)`` — trace exceeds BOTH sides;
          position cannot save us, V/div must grow.  The caller
          falls through to the standard adapt-grow path.
        * ``None`` — SCPI failure / no introspection (simulator);
          treat as "can't tell" and leave the existing rescale
          logic to take care of it on the next attempt.

        ``pos_shift_divs`` is the **delta** to apply to the current
        ``CHx:POSition`` so the trace's midpoint
        ``(v_min + v_max) / 2`` lands at screen-center after the
        nudge.  Already clamped to ±5 div minus current position
        (so the caller can write it directly without re-clamping).
        Positive shift moves POSition more positive (front-panel
        knob UP = baseline UP on screen).

        ``headroom_below_div`` / ``headroom_above_div`` quantify
        how many divisions of slack remain on each side of the
        observed range relative to the visible window.  Useful
        for diagnostic logging — the operator can see "the trace
        has 0.3 div of slack at the top but is 0.5 div past the
        bottom" and know exactly why a position nudge was
        triggered.

        Same window math as :meth:`channel_in_view`; this method
        just exposes the directional inputs that the original
        collapses to a boolean.
        """
        try:
            vpd = float(self._q(f"{channel}:SCAle?"))
            pos_divs = float(self._q(f"{channel}:POSition?"))
        except Exception:
            return None
        if not (np.isfinite(vpd) and np.isfinite(pos_divs)) or vpd <= 0.0:
            return None
        try:
            v_lo = float(v_min)
            v_hi = float(v_max)
        except (TypeError, ValueError):
            return None
        if not (np.isfinite(v_lo) and np.isfinite(v_hi)):
            return None
        if v_hi < v_lo:
            v_lo, v_hi = v_hi, v_lo
        margin = max(float(margin_divs), 0.0)
        vert_pos_v = -pos_divs * vpd
        win_lo = -margin * vpd + vert_pos_v
        win_hi = +margin * vpd + vert_pos_v
        below_out = v_lo < win_lo
        above_out = v_hi > win_hi
        # Headroom in DIVISIONS — how much margin on each side.
        # Positive = slack (trace inside window edge), negative =
        # overshoot (trace beyond edge).  Useful for logging.
        headroom_below_div = (v_lo - win_lo) / vpd
        headroom_above_div = (win_hi - v_hi) / vpd
        # Position-only shift that would put the trace MIDPOINT at
        # screen centre.  Computed in DIVISIONS (the unit
        # ``CHx:POSition`` expects).  We compute the ABSOLUTE target
        # position and return the DELTA to apply — that way the
        # caller doesn't need to re-query pos_divs.
        mid = 0.5 * (v_lo + v_hi)
        target_pos_divs = -mid / vpd
        # Clamp the target to ±5 div (hardware limit) — if mid is so
        # extreme that even a max-knob position can't bring the
        # midpoint to center, we still return SOMETHING workable
        # rather than a delta that the scope would reject.
        target_pos_divs = max(-5.0, min(5.0, float(target_pos_divs)))
        pos_shift_divs = target_pos_divs - pos_divs
        return (below_out, above_out, pos_shift_divs,
                headroom_below_div, headroom_above_div)

    # ---- bias_ratio-based scale + position decision ----------------
    @staticmethod
    def compute_scale_position_targets(
            v_min: float, v_max: float,
            *, divs: float = 4.0,
            grid: tuple = None,
            position_limit_divs: float = 5.0,
    ) -> Optional[Tuple[float, float, float, str]]:
        """Compute coordinated (V/div, POSition) targets from observed range.

        Pure math, no SCPI.  Returns ``(vpd, pos_divs, bias_ratio,
        regime)`` or ``None`` on degenerate input.

        Decision is driven by the **bias_ratio** ``= 2|mean| / Vpp``
        — a dimensionless number that captures how DC-biased the
        signal is relative to its own swing.  This is the ratio
        that resolves the classic MATLAB pain point: POSition is
        in DIVISIONS (so 1 div = current V/div), which means
        adjusting POSition and V/div together is an iterative
        problem.  Computing bias_ratio first decouples them:

        * **R < 0.1 — AC-centered**: signal sits around zero.  V/div
          is sized for the swing only (``Vpp / (2*divs)``), POSition
          stays near 0.  Typical: V_mon / I_mon at any amplitude.
        * **0.1 ≤ R ≤ 1.0 — moderate bias**: both axes matter.  V/div
          sized for swing, POSition offsets the mean.  Typical:
          E_act / E_ret during a polarized pulse.
        * **R > 1.0 — DC-dominated**: the DC offset is bigger than
          half the swing.  V/div MUST be coarsened to
          ``|mean| / position_limit_divs`` so the position offset
          fits within ±5 div of the hardware front-panel limit;
          otherwise POSition clamps and the signal goes off-screen.
          Typical: E_ret at a heavily-polarized SIROF electrode
          (rest potential ~0.5 V vs Ag|AgCl, swing only ~25 mV).

        ``divs`` is the per-role screen-occupancy budget (V_mon /
        I_mon use 3 to leave headroom for the next amp's growing
        peak; E_act / E_ret use 4 since they don't grow monotonically).

        ``grid`` is the scope's 1-2-5 vertical-scale ladder; if
        ``None`` the caller's class default
        ``_TEK_VERTICAL_GRID_VPD`` is used (we still inherit the
        ceiling-snap behaviour).

        ``position_limit_divs`` is the front-panel ``CHx:POSition``
        hardware limit (always 5.0 on Tek TBS / TDS / TPS — NOT
        tied to ``_half_vert_divs`` which depends on series).
        """
        try:
            v_lo = float(v_min)
            v_hi = float(v_max)
        except (TypeError, ValueError):
            return None
        if not (np.isfinite(v_lo) and np.isfinite(v_hi)):
            return None
        if v_hi < v_lo:
            v_lo, v_hi = v_hi, v_lo
        rng = v_hi - v_lo
        mean = 0.5 * (v_lo + v_hi)
        # bias_ratio = 2|mean| / Vpp.  0 = pure AC, 1 = one-sided around
        # zero, > 1 = signal entirely off-zero.  Floor at zero for the
        # degenerate flat-trace case (Vpp = 0).
        if rng > 1e-12:
            bias_ratio = 2.0 * abs(mean) / rng
        else:
            bias_ratio = 0.0
        # Required V/div for the SWING to fit within ±divs divisions.
        vpd_for_swing = rng / (2.0 * max(float(divs), 1.0))
        # Required V/div for the POSITION offset to fit within ±5 div
        # (or whatever ``position_limit_divs`` says).
        vpd_for_position = (abs(mean) / max(position_limit_divs, 1e-9)
                            if abs(mean) > 0 else 0.0)
        # Use the local grid; fall back to a sane mini-grid if the
        # caller didn't supply one (kept generous so unit tests don't
        # need to know the Tek 1-2-5 sequence).
        local_grid = grid if grid is not None else (
            1e-3, 2e-3, 5e-3, 1e-2, 2e-2, 5e-2,
            1e-1, 2e-1, 5e-1, 1.0, 2.0, 5.0,
        )
        grid_min = local_grid[0]
        # Pick the LARGER of the two requirements — both must hold.
        # The MAX is exactly the "coarsen-up to allow position
        # centering" rule the prior code implemented implicitly.
        ideal_raw = max(vpd_for_swing, vpd_for_position, grid_min)
        # Snap UP to grid so the trace never extends past the
        # division it was sized for.
        # ``_snap_to_grid`` is a static method on the same class;
        # bypass via the unbound reference so this helper is
        # callable from contexts (tests, GUI) that don't have an
        # instance handy.
        vpd = TektronixOscilloscope._snap_to_grid(
            ideal_raw, local_grid, direction="ceil")
        # Position offset in divisions from screen centre.  Negative
        # sign because a positive-mean signal must be pushed DOWN to
        # land at centre.  Snap to 0.1 div (the operator-readable
        # resolution; finer values are pointless because the scope
        # display can't show them).
        if vpd > 0:
            pos_raw = -mean / vpd
            if mean > 0:
                pos_divs = math.ceil(pos_raw * 10.0) / 10.0
            else:
                pos_divs = math.floor(pos_raw * 10.0) / 10.0
            pos_divs = max(-position_limit_divs,
                           min(position_limit_divs, float(pos_divs)))
        else:
            pos_divs = 0.0
        # Regime label — exposed for diagnostic logging so the
        # operator can read off "why did V/div get coarsened?" without
        # having to re-derive bias_ratio from the observed range.
        if bias_ratio < 0.1:
            regime = "AC-centered"
        elif bias_ratio <= 1.0:
            regime = "moderate-bias"
        else:
            regime = "DC-dominated"
        return (vpd, pos_divs, bias_ratio, regime)

    def set_channel_scale_and_position_for_range(
            self, channel: str,
            *, v_min: float, v_max: float,
            divs: float = 4.0,
    ) -> Optional[Tuple[float, float]]:
        """Fine-scale + position a channel using its observed range.

        Direct port of MATLAB ``setFineScalePos2.m``.  Three steps:

          1. **Scale** sized to the trace's range (NOT absolute peak):
             ``scale = (v_max - v_min) / (2 * divs)``.  For a small
             AC swing sitting on a large DC bias (e.g. an E_ret trace
             at 0.45 V resting potential with ±25 mV of polarization
             ripple) the absolute-peak approach inflates V/div by
             10× — the trace gets squished into a fraction of a
             division.  Using the *range* instead keeps the V/div
             tight to the actual signal swing.
          2. **Position** offset by the trace's mean so the
             post-shift signal centers on screen:
             ``pos_divs = -mean(v_min, v_max) / scale``
             then snap to multiples of 0.1 divisions (the scope's
             native ``CHx:POSition`` resolution).
          3. Snap the scale up to the Tek 1-2-5 grid via
             :meth:`_snap_to_grid` so the SCPI write is accepted.

        Returns ``(scale_v_per_div, position_divs)`` actually written,
        or ``None`` when the inputs were degenerate (NaN / zero range
        on a constant trace).

        Use when the displayed channel carries a DC-biased signal
        (E_ret, E_act, or any electrode-potential reading where the
        rest potential matters).  For V_mon / I_mon — signals
        naturally centered at ≈ 0 — :meth:`set_channel_scale_for_peak`
        with the absolute peak is equivalent and simpler.
        """
        # Delegate the math to :meth:`compute_scale_position_targets`
        # so the SCPI-apply side stays tiny and the regime/bias_ratio
        # decision is uniformly available to callers that want it.
        # Back-compat: this method still returns a 2-tuple
        # ``(vpd, pos_divs)`` so existing callers (calibration sweep,
        # VT's older code path) don't break.  New code that wants the
        # bias_ratio + regime label should call the static helper
        # directly.
        targets = self.compute_scale_position_targets(
            v_min, v_max,
            divs=divs,
            grid=self._TEK_VERTICAL_GRID_VPD,
            position_limit_divs=5.0,
        )
        if targets is None:
            return None
        vpd, pos_divs, _bias_ratio, _regime = targets
        self.set_channel_scale(channel, vpd)
        self.set_channel_position(channel, pos_divs)
        return (vpd, pos_divs)

    # MATLAB ``setOscilloscope_Tek.m`` per-channel block, lines 381-446.
    # The MATLAB code enforces these on every channel the user enables
    # so the bench setup is reproducible regardless of front-panel
    # state from a previous session.
    #
    # NOTE: ``CHx:BANdwidth`` is NOT in this list — it's applied
    # separately via :meth:`set_channel_bandwidth` so the GUI / runner
    # can pick the right cutoff per channel purpose.  V_mon wants full
    # bandwidth (preserve stim-pulse leading edges); I_mon wants the
    # lower preset (20 MHz on TBS-series) so the trigger comparator
    # sees a clean signal that comfortably exceeds the MATLAB
    # ``setTriggerLevel.m`` threshold (which is sized for a low-noise
    # input, not a full-bandwidth one).
    _CHANNEL_DEFAULT_COMMANDS = (
        ("{ch}:COUPling DC",      "DC coupling (no AC blocking cap)"),
        ("{ch}:INVert OFF",       "no waveform inversion"),
        ("{ch}:POSition 0",       "vertical position 0 div"),
        ('{ch}:YUNit "V"',        "report data in volts"),
    )

    def probe_info(self, channel: str) -> Dict[str, object]:
        """Read what the scope thinks is plugged into ``channel``.

        Bench convention here is direct-BNC: the Plexon V_mon / I_mon
        outputs go straight to scope inputs via coax, no probe.  An
        **attenuating** probe on those lines silently changes the
        displayed volts (10x probe ⇒ readings 10× too small after we
        force ``PRObe:GAIN 1``) so detecting it is worth the SCPI
        round-trip.

        Returns a dict with:

          * ``gain`` — float, what the scope currently reports for
            attenuation (1.0 = 1x or plain BNC, 0.1 = 10x, 0.01 = 100x).
            Always present (Tek firmware universally supports
            ``CHx:PRObe:GAIN?``).
          * ``type`` — string, the probe-ID type token if the firmware
            supports ``PRObe:ID:TYPE?`` (e.g. ``"1X"``, ``"10X"``,
            ``"TPP0500"``, ``"OTHER"``).  Empty when unsupported.
            **Informational only** — we don't use it to decide
            ``is_probe`` because firmware behaviour varies: some
            scopes report ``"1X"`` for a plain BNC cable, others
            report ``"OTHER"``, and there's no portable way to tell
            them apart.
          * ``is_probe`` — ``True`` iff the gain is meaningfully
            different from 1.0.  A 1x probe and a plain BNC are
            electrically identical for our measurements (both
            deliver the BNC-tip voltage straight to the ADC), so we
            don't try to distinguish them — only an attenuating
            probe changes the readback, and that's what this flag
            is for.

        All queries are wrapped — any failure returns a populated dict
        with safe defaults so the caller can blanket-check without
        guarding.
        """
        info: Dict[str, object] = {"gain": 1.0, "type": "", "is_probe": False}
        try:
            raw = self._q(f"{channel}:PRObe:GAIN?").strip()
            info["gain"] = float(raw)
        except Exception:
            return info
        # Best-effort type token — informational only, never used as
        # a BNC-vs-probe discriminator (see docstring for why).
        try:
            type_raw = self._q(f"{channel}:PRObe:ID:TYPE?").strip().strip('"').upper()
            info["type"] = type_raw
        except Exception:
            pass
        if abs(float(info["gain"]) - 1.0) > 1e-3:
            info["is_probe"] = True
        return info

    def apply_channel_defaults(self, channel: str) -> None:
        """Force DC / 1X / no-invert / 0-pos / V-units on one channel.

        Does NOT touch bandwidth — see :meth:`set_channel_bandwidth`
        and :meth:`set_channel_bandwidth_for_purpose`.

        Before forcing ``PRObe:GAIN 1``, peek at what the scope thinks
        is currently attached.  Lab convention is direct BNC on every
        input; if a real probe is detected we emit a warning so the
        operator knows the override is masking a physical mismatch
        (e.g. a 10x probe on V_mon would otherwise read 1/10 the true
        voltage after our force-to-1x).
        """
        info = self.probe_info(channel)
        if info.get("is_probe"):
            type_tok = info.get("type") or f"{info.get('gain', 1.0):g}x"
            self._log(
                f"[scope]   ⚠ {channel}: scope reports a probe attached "
                f"(type={type_tok}, gain={info.get('gain', 1.0):g}).  "
                f"Bench convention is direct BNC → BNC; forcing "
                f"PRObe:GAIN 1 will make the displayed voltage match "
                f"the BNC-tip voltage, NOT the probe-tip voltage.  "
                f"If the cable on {channel} really is a probe, the "
                f"readings will be off by {info.get('gain', 1.0):g}.")
        for tmpl, _why in self._CHANNEL_DEFAULT_COMMANDS:
            try:
                self._w(tmpl.format(ch=channel))
            except Exception:
                pass
        # Probe attenuation — dialect-specific command from the command set.
        try:
            self._w(self._cmds.probe_cmd_tmpl.format(ch=channel))
        except Exception:
            pass

    def set_channel_bandwidth(self, channel: str,
                              option) -> Optional[float]:
        """Apply a :class:`TekBandwidthOption` to ``channel``.

        Returns the cutoff in MHz that was applied (for logging), or
        ``None`` if the scope rejected the command or the option was
        falsy.  Safe to call when no model spec is available — silent
        no-op in that case.
        """
        if option is None:
            return None
        try:
            self._w(f"{channel}:BANdwidth {option.scpi_value}")
            return float(option.cutoff_mhz)
        except Exception:
            return None

    def set_channel_bandwidth_for_purpose(self, channel: str,
                                          purpose: str) -> Optional[float]:
        """Apply the *limited* bandwidth this scope's series recommends.

        Lab convention: NO channel should be left at full bandwidth —
        the broadband noise drowns out small I_mon signals and rounds
        nothing on V_mon at any timescale we care about (stim pulse
        edges are >>20 MHz only on the leading sample, which 20 MHz
        attenuation barely touches).  So both ``"imon"`` and
        ``"vmon"`` map to ``series_spec.recommended_imon_bw`` (the
        20 MHz / ``ON`` preset on every TBS / TDS family in the
        database).  If the series spec ever drops that recommendation,
        we fall back to the *last* (most-limited) entry of
        ``bandwidth_options`` so we never silently leave the channel
        at full BW.

        ``purpose`` is retained for API stability; current behaviour
        ignores it but the parameter lets future revisions distinguish
        per-channel cutoffs without breaking callers.

        Returns the cutoff (MHz) applied, or ``None`` when the scope
        rejected the command / has no bandwidth options.
        """
        from .tektronix_models import get_series_spec as _get_series
        series_spec = _get_series(getattr(self.info, "model", "") or "")
        if series_spec is None or not series_spec.bandwidth_options:
            return None
        opt = series_spec.recommended_imon_bw
        if opt is None:
            # Defensive fallback: pick the most-limited option in the
            # tuple so we still avoid Full BW.  By convention
            # ``bandwidth_options`` is ordered full → most-limited,
            # so the last entry is the tightest filter.
            opt = series_spec.bandwidth_options[-1]
        # Last-line guard: never send "FULl" or "OFF" — both
        # disable the BW limit on Tek scopes.
        if opt is not None and opt.scpi_value.upper() in ("FULL", "OFF"):
            # Find the first non-Full / non-OFF option in the tuple.
            for alt in series_spec.bandwidth_options:
                if alt.scpi_value.upper() not in ("FULL", "OFF"):
                    opt = alt
                    break
            else:
                # No limited option exists for this series — leave the
                # scope alone rather than re-enable Full BW.
                return None
        _ = purpose
        return self.set_channel_bandwidth(channel, opt)

    def set_horizontal_scale(self, seconds_per_div: float) -> float:
        """Write the SEC/DIV setting and return what the scope actually applied.

        The TBS2000-family scope silently quantises any off-grid
        ``HORizontal:SCAle`` write to the nearest entry on its
        front-panel SEC/DIV grid (a **1-2-4** sequence on TBS2204B —
        verified empirically; the programmer manual's "1-2-5" claim is
        wrong).  Writing ``5e-6`` will land on 4 µs/div (or 10 µs/div)
        without raising an error, and a naive caller that assumes the
        write took effect verbatim will then mis-place the trigger
        marker and mis-compute the on-screen window width.

        We do a single ``HORizontal:SCAle?`` readback right after the
        write to capture the scope's actual choice.  Returns the
        readback value (seconds/div) so callers can update their
        own cached values without a second query.  If the readback
        fails for any reason, returns the requested value as a
        best-effort fallback and logs a warning.
        """
        self._w(f"{self._cmds.horiz_scale} {seconds_per_div:g}")
        try:
            applied = float(self._q(f"{self._cmds.horiz_scale}?"))
        except Exception as e:
            self._log(
                f"[scope]   ⚠ horizontal-scale readback failed "
                f"({type(e).__name__}: {e!r}) — assuming the requested "
                f"{seconds_per_div:g} s/div took effect")
            # Cache the requested value as best-effort so the time-
            # axis computation has SOMETHING to work with.
            self._expected_horiz_scale_s = float(seconds_per_div)
            return float(seconds_per_div)
        # Quiet log only when the scope rounded — saves chatter on
        # the common case where our host-side grid matched.  Threshold
        # is 1 % to absorb floating-point noise in the SCPI reply.
        if applied > 0 and abs(applied - float(seconds_per_div)) / applied > 0.01:
            self._log(
                f"[scope] horizontal scale: requested "
                f"{seconds_per_div:g} s/div → scope applied "
                f"{applied:g} s/div (off-grid request rounded by "
                f"firmware)")
        # Cache the readback (post-rounding) so the host-side time-
        # axis computation matches what the scope actually applied.
        self._expected_horiz_scale_s = float(applied)
        return applied

    def set_horizontal_position(self, percent: float) -> None:
        """Set the trigger position on the screen as a percentage.

        Always takes a **percent** value (0..100) where:

        * **0** → trigger at the far-left edge (no pre-trigger samples,
          full record is post-trigger)
        * **50** → trigger at screen centre (default; 50 % pre-, 50 %
          post-trigger)
        * **100** → trigger at the far-right edge (entire record is
          pre-trigger)

        The SCPI command we send is dialect-dependent:

        * **Modern** (``MODERN_CMDS``): ``HORizontal:POSition <pct>`` accepts percent.
        * **Legacy** (``LEGACY_CMDS``): ``HORizontal:POSition`` is not recognised;
          we convert the
          percent into a seconds offset using the current timebase
          (read via ``HORizontal:SCAle?``) and send
          ``HORizontal:MAIN:POSition <s>`` instead.

        On TBS-firmware ``HORizontal:MAIN:POSition`` and
        ``HORizontal:POSition`` are **two different parameters** —
        the legacy one sets a "main horizontal delay" that does NOT
        move the trigger marker on screen. So sending the wrong
        command on the wrong dialect silently looks like nothing
        happened. The dialect dispatch here is what keeps the lab
        bench (TBS2204B, modern) and a TDS3000-class collaborator
        (legacy) both seeing the same on-screen behaviour.
        """
        # Clamp on the host so a programmer error doesn't put the
        # scope into a state where the trigger is off-screen.
        pct = max(0.0, min(100.0, float(percent)))
        # Snap DOWN to the nearest 10 % step — matches the MATLAB
        # convention of parking the trigger on a grid division so the
        # user sees clean alignment, and the floor (vs. round-to-
        # nearest) keeps more leading pre-trigger samples than the
        # caller asked for rather than fewer.  Avoids odd values
        # like 47.3 % that the scope would otherwise honour to
        # several decimals.
        pct = int(pct / 10.0) * 10.0

        # Force the scope OUT of delay mode before writing the position.
        # When ``HORizontal:DELay:MODe`` is ON, the scope uses
        # ``DELay:TIMe`` as the trigger offset and IGNORES ``POSition``.
        # In that state, ``WFMOutpre:XZEro?`` comes back as 0 even when
        # we asked for a non-zero position — which then puts t=0 at the
        # left edge of the captured array instead of at the trigger
        # event.  Most TBS scopes ship with DELay:MODe ON by default,
        # so we explicitly turn it OFF here.  Silent on firmware that
        # doesn't accept the command.
        try:
            self._w("HORizontal:DELay:MODe OFF")
        except Exception:
            pass

        if self._cmds.horiz_position_unit == "percent":
            self._w(f"{self._cmds.horiz_position} {pct:g}")
            # Cache the floored % so the time-axis computation in
            # ``_read_channel`` can derive t=0 without trusting the
            # scope's XZEro / PT_Off readback.  Floored value (not
            # the raw input) so the cached % matches what the scope
            # actually applied.
            self._expected_horiz_position_pct = float(pct)
            return
        # Legacy seconds form — convert percent to a time offset.
        try:
            timebase_s = float(self._q(f"{self._cmds.horiz_scale}?"))
        except Exception:
            return
        position_s = (pct / 100.0) * timebase_s * float(self._n_horiz_divs)
        self._w(f"{self._cmds.horiz_position} {position_s:g}")
        self._expected_horiz_position_pct = float(pct)

    def set_channel_position(self, channel: str, divisions: float) -> None:
        """Set the per-channel vertical position (in divisions, +/- ~5)."""
        self._w(f"{channel}:POSition {divisions:g}")

    def set_trigger_level(self, level_v: float) -> None:
        # Hard rule: when the digital sync (EXT) is the trigger source, the
        # trigger level is owned by the front panel — the BNC is a clean TTL
        # edge and any fixed value we write would just be wrong. Silently
        # skip so callers further up the stack don't need an EXT branch.
        src = (self._expected_trigger_source or "").upper()
        if src.startswith("EXT"):
            return
        self._w(f"{self._cmds.trig_level} {level_v:g}")

    def set_cursors(self, phase1_us: float, interphase_us: float = 0.0,
                    source_channel: str = "CH1") -> None:
        """Place vertical-bar cursors matching the MATLAB setDefaultScopeView3 convention.

        Cursor 1 → end of phase 1  (phase1_us after trigger)
        Cursor 2 → midpoint of interphase gap  (phase1_us + interphase_us/2 after trigger)

        Positions are in seconds relative to t=0 (the trigger event), matching
        CURSor:VBArs:POSITION<n> semantics on all Tek TBS/TDS series.
        Silent no-op on any SCPI error so a missing feature never aborts a run.
        """
        try:
            self._w("CURSor:FUNCtion VBArs")
            self._w(f"CURSor:SELect:SOUrce {source_channel}")
            t1_s = float(phase1_us) * 1e-6
            t2_s = (float(phase1_us) + float(interphase_us) / 2.0) * 1e-6
            self._w(f"CURSor:VBArs:POSITION1 {t1_s:.6e}")
            self._w(f"CURSor:VBArs:POSITION2 {t2_s:.6e}")
        except Exception:
            pass

    # ----- gated measurement (closed-loop bias feedback) ---------------
    # Consumer: ``stimtest/experiments/bias_feedback.py``
    # ``BiasFeedbackController.step()`` calls ``gate_measurement_window``
    # once at controller setup, then ``measure_mean`` ~10–20× per second
    # to get the current E_ret mean over the interpulse window.  Each
    # query is one SCPI round-trip (~10–50 ms over USBTMC); much faster
    # than the ~150 ms ``CURVe?`` of a 20k-point waveform AND has the
    # scope-side averaging that beats 8-bit ADC quantization the same
    # way AVERAGE acquisition mode does.
    #
    # SCPI surface used:
    #   CURSor:FUNCtion VBArs              — switch to vertical (time) cursors
    #   CURSor:VBArs:POSITION1 <t_s>       — cursor 1 time (seconds from trigger)
    #   CURSor:VBArs:POSITION2 <t_s>       — cursor 2 time
    #   MEASUrement:IMMed:GATing CURSor    — bind MEASU to the cursor pair
    #   MEASUrement:IMMed:TYPe MEAN        — pick the statistic
    #   MEASUrement:IMMed:SOURce CH<n>     — pick the channel
    #   MEASUrement:IMMed:VALue?           — query: returns a float (V)
    #
    # The TBS-series MEASUrement:IMMed subsystem returns 9.91e+37 as the
    # "no result" sentinel (Tek convention for failed measurements;
    # documented in TBS2000 programmer's manual §3-86).  We treat any
    # value ≥ 1e30 as NaN.
    _TEK_MEAS_INVALID = 1e30

    def gate_measurement_window(self, t_us_start: float,
                                t_us_end: float) -> None:
        """Place cursors at the requested window and configure
        ``MEASU:IMMed`` to gate on them.  See base-class docstring.

        Idempotent — repeated calls with the same window are cheap;
        a new window just rewrites the cursor positions.  The
        MEASUrement subsystem stays armed across calls so subsequent
        :meth:`measure_mean` calls don't pay the gate-setup latency.
        """
        if t_us_end < t_us_start:
            raise ValueError(
                f"gate window end ({t_us_end} µs) must be ≥ start "
                f"({t_us_start} µs)")
        try:
            self._w("CURSor:FUNCtion VBArs")
            self._w(f"CURSor:VBArs:POSITION1 {t_us_start * 1e-6:.6e}")
            self._w(f"CURSor:VBArs:POSITION2 {t_us_end * 1e-6:.6e}")
            self._w("MEASUrement:IMMed:GATing CURSor")
        except Exception as e:
            # Don't silently swallow — gating failures break the
            # feedback loop's correctness contract.  Logger surfaces it.
            self._log(f"[scope] gate_measurement_window FAILED: "
                      f"{type(e).__name__}: {e}")
            raise

    def clear_measurement_gating(self) -> None:
        """Drop cursor gating; subsequent MEASU queries use the full
        acquisition window."""
        try:
            self._w("MEASUrement:IMMed:GATing OFF")
        except Exception as e:
            self._log(f"[scope] clear_measurement_gating: "
                      f"{type(e).__name__}: {e}")

    def measure_mean(self, channel: str) -> float:
        """Query ``MEASU:IMMed:VALue?`` after setting TYPe MEAN + SOURce.

        Returns NaN on any SCPI error or on the Tek "no-result"
        sentinel (≥ 1e30) — see the ``_TEK_MEAS_INVALID`` constant
        above for context.  Caller treats NaN as "skip this iteration"
        in the feedback loop.
        """
        try:
            self._w("MEASUrement:IMMed:TYPe MEAN")
            self._w(f"MEASUrement:IMMed:SOURce {channel}")
            raw = self._q("MEASUrement:IMMed:VALue?")
            v = float(raw)
            if abs(v) >= self._TEK_MEAS_INVALID:
                return float("nan")
            return v
        except Exception as e:
            self._log(f"[scope] measure_mean({channel}) FAILED: "
                      f"{type(e).__name__}: {e}")
            return float("nan")

    # ----- adaptive scaling / layout (ported from MATLAB setDefaultScopeView3
    #       and adjustScale + improvements) ----------------------------------
    #: Horizontal-timebase quantisation — built dynamically from the
    #: active command-set's ``timebase_grid_mantissas`` field so each
    #: scope family gets the sequence that matches its physical knob:
    #:
    #: * Modern TBS2000B / TBS2204B / MSO / MDO / DPO: ``(1, 2, 4)``
    #:   (verified empirically: 10 µs → 20 µs → 40 µs → 100 µs; the
    #:   programmer manual's "1-2-5 sequence" claim is wrong)
    #: * Legacy TBS1000B / TDS2000/3000: ``(1, 2.5, 5)``
    #:   (matches the MATLAB ``setOscillocopeView.m`` original — the
    #:   collaborator's scope is in this family)
    #:
    #: The scope silently rounds off-grid SCPI writes, so snapping on
    #: the host side to the *true* grid means our cached (scale,
    #: position) tuple matches what the scope actually applied —
    #: without this, downstream layout math (trigger-marker placement,
    #: window-width computation) drifts every time we picked an
    #: off-grid value.
    #:
    #: Class-level default kept for callers that read it before
    #: ``open()`` has populated ``self._cmds``; instance-level lookup
    #: ``self._timebase_grid_seconds`` returns the family-specific grid
    #: once the dialect is known.
    _TEK_TIMEBASE_GRID_SECONDS = tuple(
        m * (10 ** e)
        for e in range(-9, 2)            # 1 ns/div ... 10 s/div decades
        for m in (1.0, 2.0, 4.0)
    ) + (10.0, 20.0, 40.0)               # 10/20/40 s/div hand-completed

    @property
    def _timebase_grid_seconds(self) -> tuple:
        """Return the SEC/DIV grid for the connected scope family.

        Reads ``self._cmds.timebase_grid_mantissas`` and expands it
        across the standard decade range (1 ns/div … 50 s/div).  Falls
        back to the class-level ``_TEK_TIMEBASE_GRID_SECONDS`` when no
        dialect has been probed yet (e.g. ``_pick_resource`` running
        before ``open()`` has assigned ``self._cmds``).
        """
        cmds = getattr(self, "_cmds", None)
        mantissas = getattr(cmds, "timebase_grid_mantissas", None)
        if not mantissas:
            return self._TEK_TIMEBASE_GRID_SECONDS
        # Build the decades from the per-family mantissas. Hand-extend
        # the top end with a couple of extra steps so 10/20/40 (or
        # 10/25/50 on legacy) s/div remain reachable.
        grid = tuple(m * (10 ** e) for e in range(-9, 2) for m in mantissas)
        # Extend one decade up using the same mantissas × 10.
        grid = grid + tuple(m * 10.0 for m in mantissas)
        return grid

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

    #: Horizontal timebase candidates (class-level default; dialect-
    #: specific list lives behind :attr:`_horiz_scale_candidates_s`).
    #:
    #: The MATLAB ``setOscillocopeView.m`` original used a 1-2.5-5 grid
    #: ``[1000 500 250 100 50 25 10 5 2.5]`` µs/div which is correct for
    #: the TBS1000B / TDS-class collaborator scope (legacy dialect).
    #: The lab-bench TBS2204B (modern dialect) is on a 1-2-4 grid
    #: instead, so the scope would silently round MATLAB's 2.5/25/250 µs
    #: requests to 2/20/200 µs.  The instance-level property below
    #: picks the right list at run time based on the connected scope's
    #: family — this class constant is just a safe fallback for the
    #: pre-``open()`` window.
    _HORIZ_SCALE_CANDIDATES_S: tuple = tuple(
        v * 1e-6 for v in (
            1000.0, 400.0, 200.0, 100.0, 40.0, 20.0, 10.0, 4.0, 2.0, 1.0,
        )
    )

    @property
    def _horiz_scale_candidates_s(self) -> tuple:
        """Auto-fit candidate timebases for the connected scope family.

        Built from ``self._cmds.timebase_grid_mantissas`` and ordered
        largest → smallest so the auto-fit search picks the smallest
        scale where the pulse fills ≥ 30 % of the 10-div window.
        Range: 1 µs/div … 1000 µs/div (the auto-fit window of interest
        for stim pulses — anything wider gets handled at the
        per-experiment layer).  Falls back to the class-level list
        when ``self._cmds`` has not been populated yet.
        """
        cmds = getattr(self, "_cmds", None)
        mantissas = getattr(cmds, "timebase_grid_mantissas", None)
        if not mantissas:
            return self._HORIZ_SCALE_CANDIDATES_S
        # Build candidates across the µs–ms range that matters for stim
        # pulse layout: 1 µs/div ... 1 ms/div.  Ordered descending so
        # the auto-fit loop breaks at the first qualifying entry.
        values_us: list[float] = []
        for decade in (1e3, 1e2, 1e1, 1e0):  # 1000 µs ... 1 µs
            for m in mantissas:
                values_us.append(m * decade)
        values_us.sort(reverse=True)
        return tuple(v * 1e-6 for v in values_us)

    def auto_layout_for_pulse(self, *,
                              phase1_us: float,
                              interphase_us: float = 0.0,
                              phase2_us: float = 0.0,
                              discharge_us: float = 0.0,
                              digital_delay_us: float = DIGITAL_DELAY_US,
                              ext_trigger: Optional[bool] = None,
                              left_offset_divs: Optional[int] = None) -> Tuple[float, float]:
        """Pick a horizontal scale + position so the pulse fills the screen.

        Faithfully implements the MATLAB ``setOscillocopeView.m`` algorithm:

          1. Pulse width = phase1 + interphase + phase2 + discharge
             (the full active window, matching MATLAB's ``totalPulse``).
          2. Iterate the candidate timebases largest→smallest; choose the
             smallest entry where ``totalPulse / (10 × scale) ≥ 0.3``,
             i.e. the pulse fills at least 30 % of the 10-division window.
          3. Pre-trigger offset:
               * fill ≥ 0.4 → 3 divisions before the pulse start
               * fill <  0.4 → 4 divisions (more leading baseline so a
                              skinny pulse isn't crammed against the edge)
          4. **EXT trigger only**: add ``digital_delay_us`` to the position
             offset because the Plexon digital sync fires that many µs
             *before* the first phase begins.  When the trigger source is
             a channel (e.g. I_mon), the trigger IS the current edge —
             no delay applies.  Pass ``ext_trigger=None`` (the default)
             to let the driver decide from ``_expected_trigger_source``,
             so a caller that forgets the flag still gets the right
             behaviour.
          5. Position is sent *before* scale (MATLAB order).

        Returns ``(scale_s, position_pct)`` — the values read back from the
        scope after writing, so callers see what actually landed.
        """
        # Resolve the digital-sync flag.  When the caller doesn't
        # pass ``ext_trigger`` explicitly we read it from the scope's
        # stored ``_expected_trigger_is_digital``, which ``set_trigger``
        # populates for both EXT and channel-Trigger TTL paths.  The
        # historical name ``ext_trigger`` is preserved on the public
        # API for back-compat — semantically it means "is the trigger
        # a digital sync line that needs the 1.2 µs offset", which
        # covers both EXT BNC and a sync-on-channel routing.
        if ext_trigger is None:
            is_ext = bool(self._expected_trigger_is_digital)
        else:
            is_ext = bool(ext_trigger)

        pulse_width_us = (
            float(phase1_us) + float(interphase_us)
            + float(phase2_us) + float(discharge_us)
        )

        # --- 1. Pick timebase -------------------------------------------
        # Iterate from widest (1000 µs/div) to narrowest (1 µs/div on
        # 1-2-4 modern, 2.5 µs/div on 1-2.5-5 legacy), stopping at the
        # smallest scale where fill ≥ 0.3.  Candidate list is dialect-
        # specific (see ``_horiz_scale_candidates_s`` property) — the
        # TBS2204B knob is 1-2-4, the TBS1000B knob is 1-2.5-5, and
        # each would round the other's grid silently.  If the pulse is
        # so narrow that even the narrowest candidate can't reach 30 %
        # fill the loop exhausts and we stay at the narrowest entry.
        candidates = self._horiz_scale_candidates_s
        scale_s = candidates[0]
        fract = 0.0
        for cand_s in candidates:
            scale_us = cand_s * 1e6
            fract = pulse_width_us / (scale_us * float(self._n_horiz_divs))
            if fract >= 0.3:
                scale_s = cand_s
                break

        # --- 2. Pre-trigger offset (divisions) --------------------------
        # MATLAB convention: tighter fill → less leading baseline (3 divs);
        # looser fill → more leading baseline (4 divs) so the pulse doesn't
        # sit jammed against the trigger marker.
        scale_us = scale_s * 1e6
        if left_offset_divs is not None:
            offset_divs = int(left_offset_divs)
        else:
            offset_divs = 3 if fract >= 0.4 else 4
        shift_us = float(offset_divs) * scale_us
        if is_ext:
            shift_us += float(digital_delay_us)

        # --- 3. Write position first, then scale (MATLAB order) ---------
        # The position is a time offset from the left edge of the screen
        # to the trigger marker in seconds (``HORizontal:MAIN:POSition``
        # legacy form) or as a percentage of the window width (modern
        # ``HORizontal:POSition`` percent form).
        position_pct = max(0.0, min(100.0,
                            shift_us / (scale_us * float(self._n_horiz_divs))
                            * 100.0))
        self.set_horizontal_position(position_pct)
        self.set_horizontal_scale(scale_s)

        # --- 4. Read back what the scope actually stored ----------------
        try:
            scale_s = float(self._q(f"{self._cmds.horiz_scale}?"))
        except Exception:
            pass
        try:
            if self._cmds.horiz_position_unit == "percent":
                position_pct = float(self._q(f"{self._cmds.horiz_position}?"))
            else:
                position_s = float(self._q(f"{self._cmds.horiz_position}?"))
                window_s = scale_s * float(self._n_horiz_divs) if scale_s > 0 else 0.0
                if window_s > 0:
                    position_pct = max(0.0, min(100.0,
                                       position_s / window_s * 100.0))
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

        # V_mon: size the scale on I·R (the edge-step the calibration
        # actually measures), capped at a realistic compliance ceiling.
        #
        # The RC load produces V_load = V_R + V_C.  V_R = I·R is the
        # instantaneous step at each phase boundary — the signal of
        # interest.  V_C ramps slowly between steps: for a small cap
        # (4700 pF) and long phase (200 µs) the theoretical end-of-phase
        # V_C is enormous (e.g. 42 V at 1000 µA), but the stimulator's
        # compliance voltage limits the actual rail to ≈ ±15 V.  Using
        # the theoretical V_C would select a coarse scale (5 V/div)
        # where the I·R step (≤ 1.25 V at 1000 µA × 0.25 V_mon gain)
        # is only a few ADC counts — killing measurement resolution.
        #
        # Strategy: base the scale on max(v_r, compliance_estimate) so
        # the I·R step is always resolved, while still fitting the
        # compliance-limited V_C ramp on screen.
        COMPLIANCE_V = 15.0          # PlexStim typical compliance ceiling (V)
        if load_r_ohm > 0 or load_c_pf > 0:
            v_r = abs(amp_ua) * 1e-6 * float(load_r_ohm)
            if load_c_pf > 0:
                v_c_theory = (abs(amp_ua) * 1e-6 * float(phase_us) * 1e-6
                              / max(float(load_c_pf) * 1e-12, 1e-15))
                # Clamp to compliance — the stimulator clips here
                v_c = min(v_c_theory, COMPLIANCE_V)
            else:
                v_c = 0.0
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

    #: How many times the same scale candidate can come up before we
    #: stop adapting and lock in the current scale.  MATLAB
    #: ``getWaveform.m`` uses ``isScaleRepeated = any(vertScale_count > 1)``
    #: but its grid is much denser, so the same candidate is far less
    #: likely to come up by accident.  We allow 3 visits before locking
    #: so legitimate hunting (coarse → fine → re-check) can run its
    #: course without being mistaken for oscillation.
    _ADAPT_REPEAT_LIMIT: int = 3
    #: Hard cap on the number of distinct scales tried per channel
    #: before we give up adapting.  MATLAB ``getWaveform.m`` allows
    #: ``fineScale_count > 4`` *after* any number of coarse hops, so the
    #: total can easily reach 8-10.  Set high enough that legitimate
    #: coarse-then-fine convergence is never cut short, but pathological
    #: oscillation eventually hits the cap.
    _ADAPT_MAX_TRIES: int = 10
    #: After this many *consecutive* "no change needed" returns we
    #: consider the channel converged and clear the history, so a later
    #: change in signal magnitude can adapt again without tripping the
    #: repeat-detector on stale entries.
    _ADAPT_SETTLED_COUNT: int = 2

    def adapt_channel_scale(self, channel: str, *,
                            v_min: float, v_max: float,
                            divs: float = 4.0,
                            shrink_threshold: float = 0.30,
                            shrink_stable_count: int = 2) -> Optional[float]:
        """Post-capture autorange: snap CH<n>:SCAle to fit (v_min, v_max).

        Loop-safe coarse scaling that mirrors MATLAB ``getWaveform.m``:

          * **Clip → instant upscale.**  Signal exceeds the current scale
            on either side → grow to the next legal scale that fits.
            One step per capture so the next acquisition is usable.
          * **Shrink → hysteresis.**  Signal small for current scale →
            need ``shrink_stable_count`` consecutive votes before
            tightening, so a noisy capture doesn't flicker the scale.
          * **Repeat detection.**  Track the full sequence of picked
            scales; if a candidate has come up ``_ADAPT_REPEAT_LIMIT``
            times we lock in (signal is oscillating between two grid
            cells, neither perfect — accept).
          * **Grid-boundary clamps.**  At the min or max of the
            vertical-scale grid: accept current rather than try to go
            further.  Otherwise we'd loop forever asking for a scale
            the scope can't reach.
          * **Hard try cap.**  After ``_ADAPT_MAX_TRIES`` distinct
            scales have been written, lock in — prevents pathological
            signals from wedging the sweep in an endless adjust loop.

        Returns the new scale if one was applied, or ``None`` if the
        current scale was kept (either because it's already correct,
        the hysteresis hasn't unlocked yet, or we've decided not to
        adjust further per the loop-protection rules above).
        """
        # Internal per-channel state — hysteresis vote count, last
        # scale we wrote, and the history of every scale we've picked
        # on this channel since open().  Lazy-init.
        if not hasattr(self, "_adapt_state"):
            self._adapt_state: Dict[str, Dict[str, object]] = {}
        st = self._adapt_state.setdefault(
            channel, {"shrink_count": 0,
                      "last_scale": None,
                      "history": [],     # list of scales we've picked
                      "settled_count": 0,  # consecutive no-op returns
                      "locked": False})  # True after repeat / max-tries
        history: List[float] = st["history"]  # type: ignore[assignment]

        # Use cached scale — avoids a USB-TMC round-trip every capture.
        current_scale = st["last_scale"]
        if current_scale is None:
            try:
                current_scale = float(self._q(f"{channel}:SCAle?"))
            except Exception:
                current_scale = 1.0
            st["last_scale"] = current_scale

        # If we already decided to lock in on a previous call, stay put.
        if st["locked"]:
            return None

        # Mirror MATLAB ``setFineScalePos2.m``: scale = abs(range) / 2 / 4
        # = half the signal range divided by 4 divs.
        half_range = abs(v_max - v_min) / 2.0
        grid_min = self._TEK_VERTICAL_GRID_VPD[0]
        grid_max = self._TEK_VERTICAL_GRID_VPD[-1]
        ideal_scale = self._snap_to_grid(
            max(half_range / max(divs, 1.0), grid_min),
            self._TEK_VERTICAL_GRID_VPD, direction="ceil")

        # ---- Loop-protection gates ----------------------------------
        # 1) No change needed — already on the right grid cell.
        #    This is the GOOD path: count consecutive settles and, once
        #    we've seen a couple in a row, clear the history so a future
        #    signal-magnitude change can adapt again without tripping the
        #    repeat-detector on stale entries.  This is NOT a lock.
        if ideal_scale == current_scale:
            st["shrink_count"] = 0
            st["settled_count"] = int(st.get("settled_count", 0)) + 1
            if st["settled_count"] >= self._ADAPT_SETTLED_COUNT and history:
                # Converged — clear stale history so we can re-adapt later.
                st["history"] = []
            return None
        # Any non-settled call resets the settled counter.
        st["settled_count"] = 0

        # 2) Hard cap on distinct scales tried.  Set high enough that
        #    legitimate coarse-then-fine convergence (MATLAB allows
        #    fineScale_count>4 plus any number of coarse hops) is never
        #    cut short.
        if len(history) >= self._ADAPT_MAX_TRIES:
            self._log(
                f"[scope]   adapt {channel}: try-count cap reached "
                f"({len(history)} ≥ {self._ADAPT_MAX_TRIES}) — keeping "
                f"current {current_scale*1000:.2f} mV/div (locking).")
            st["locked"] = True
            return None
        # 3) Repeat detection — only treat as oscillation when the
        #    candidate has come up ``_ADAPT_REPEAT_LIMIT`` times AND the
        #    last write was an adjacent grid cell (i.e. we're flipping
        #    between two neighbours, not still hunting coarsely).
        if history.count(ideal_scale) >= self._ADAPT_REPEAT_LIMIT:
            try:
                grid = self._TEK_VERTICAL_GRID_VPD
                # Closest grid index to BOTH the current_scale (which
                # may be off-grid because we cache the requested value,
                # not the snapped one the scope actually applied) and
                # the ideal_scale (which is on-grid by construction
                # from ``_snap_to_grid`` — but use closest-match as a
                # belt-and-braces guard against subtle floating-point
                # mismatches in the grid lookup).
                def _closest(v):
                    return min(range(len(grid)),
                               key=lambda k: abs(grid[k] - float(v)))
                i_cur = _closest(current_scale)
                i_ideal = _closest(ideal_scale)
                adjacent = abs(i_cur - i_ideal) <= 1
            except (ValueError, AttributeError, TypeError):
                # Truly degenerate (empty grid, NaN, etc.) — fall
                # back to "not adjacent" so we DON'T accidentally
                # lock on a coarse-hunt step.  Better to allow one
                # more iteration than to freeze a hunt that's still
                # making progress.
                adjacent = False
            if adjacent:
                self._log(
                    f"[scope]   adapt {channel}: candidate "
                    f"{ideal_scale*1000:.2f} mV/div would be re-pick #"
                    f"{history.count(ideal_scale)+1} (adjacent to current "
                    f"{current_scale*1000:.2f} mV/div) — oscillation between "
                    f"neighbouring grid cells, locking.")
                st["locked"] = True
                return None
            # Non-adjacent re-pick is legitimate coarse hunting; allow it.

        # ---- Direction-specific paths -------------------------------
        if ideal_scale > current_scale:
            # Clip: grow scale.  But if we're already at the grid max,
            # accept (can't grow further; clipping is unavoidable here).
            if current_scale >= grid_max:
                self._log(
                    f"[scope]   adapt {channel}: signal range "
                    f"[{v_min*1000:+.1f}..{v_max*1000:+.1f}] mV exceeds "
                    f"grid max {grid_max*1000:.0f} mV/div — accepting clip "
                    f"(locking).")
                st["locked"] = True
                return None
            self._log(
                f"[scope]   adapt {channel}: signal range "
                f"[{v_min*1000:+.1f}..{v_max*1000:+.1f}] mV exceeds current "
                f"scale {current_scale*1000:.2f} mV/div → upscale to "
                f"{ideal_scale*1000:.2f} mV/div (immediate, to avoid clip; "
                f"try {len(history)+1}/{self._ADAPT_MAX_TRIES})")
            self.set_channel_scale(channel, ideal_scale)
            st["shrink_count"] = 0
            st["last_scale"] = ideal_scale
            history.append(ideal_scale)
            return ideal_scale

        # ideal_scale < current_scale  — Shrink with hysteresis.
        # If we're already at the grid min, accept; can't go finer.
        if current_scale <= grid_min:
            self._log(
                f"[scope]   adapt {channel}: at grid min "
                f"{grid_min*1000:.1f} mV/div — can't downscale further, "
                f"locking.")
            st["locked"] = True
            return None
        st["shrink_count"] += 1
        if st["shrink_count"] >= shrink_stable_count:
            self._log(
                f"[scope]   adapt {channel}: signal range "
                f"[{v_min*1000:+.1f}..{v_max*1000:+.1f}] mV is small for "
                f"scale {current_scale*1000:.2f} mV/div "
                f"({st['shrink_count']} consecutive votes) → downscale to "
                f"{ideal_scale*1000:.2f} mV/div  (try "
                f"{len(history)+1}/{self._ADAPT_MAX_TRIES})")
            self.set_channel_scale(channel, ideal_scale)
            st["shrink_count"] = 0
            st["last_scale"] = ideal_scale
            history.append(ideal_scale)
            return ideal_scale
        # Hysteresis hold — don't downscale yet, count this as a vote.
        self._log(
            f"[scope]   adapt {channel}: shrink vote "
            f"{st['shrink_count']}/{shrink_stable_count} for "
            f"{ideal_scale*1000:.2f} mV/div (current "
            f"{current_scale*1000:.2f} mV/div, hysteresis hold)")
        return None

    def reset_adapt_state(self, channel: Optional[str] = None) -> None:
        """Forget the adapt history so the autorange loop can run fresh.

        Call this between sweeps (or between configurations) — otherwise
        the ``locked`` flag and history would carry over from the prior
        run, blocking adaptation when the new run's signal magnitude
        differs.  Pass ``channel=None`` to reset ALL channels at once.
        """
        if not hasattr(self, "_adapt_state"):
            return
        if channel is None:
            self._adapt_state = {}
        else:
            self._adapt_state.pop(channel, None)

    def set_record_length(self, n: int) -> None:
        n = snap_record_length(int(n), getattr(self.info, "model", ""))
        if n <= 0:
            raise ValueError(f"record_length must be positive, got {n}.")
        # TBS2000-series scopes reallocate their internal capture buffer
        # when ``HORizontal:RECOrdlength`` changes; on a TBS2204B with
        # 20k records this internal step takes 10-30 seconds AFTER the
        # SCPI write returns its OPC.  Log a "this is going to take a
        # while" notice up front so the operator sees activity in the
        # log pane and on-disk session log during the otherwise-silent
        # gap.  Without this the user sees ``[scope] >
        # HORizontal:RECOrdlength 20000`` then nothing for 20+ s and
        # assumes the GUI has hung / crashed.
        _t0 = time.perf_counter()
        self._log(
            f"[scope] set_record_length({n}): writing — scope will "
            f"internally re-allocate the capture buffer; this can "
            f"take 10-30 s on TBS2000-series with large records.")
        self._w_checked(f"{self._cmds.horiz_record} {n}")
        self._w_checked(f"DATa:STOP {n}")
        try:
            actual = int(float(self._q(f"{self._cmds.horiz_record}?")))
        except Exception:
            actual = n
        self._record_length = actual
        _dt = time.perf_counter() - _t0
        self._log(
            f"[scope] set_record_length({n}): OK, scope confirmed "
            f"{actual} points  (total {_fmt_elapsed(_dt)})")

    def _refresh_record_length(self) -> int:
        """Query the scope for the record length and cache it.

        The record length doesn't change unless someone calls
        :meth:`set_record_length` explicitly, so we read it once at open
        and re-use the cached value for every subsequent capture.
        """
        n = int(float(self._q(f"{self._cmds.horiz_record}?")))
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
                                   and self._cmds.has_acq_numavg
                                   else None)
        # Switching to AVERAGE mode on a long-record-length capture
        # triggers another internal scope reconfiguration that the
        # SCPI write itself doesn't wait for.  Log a heartbeat at the
        # start so the operator sees something in the log during the
        # potentially-multi-second internal step.
        _t0 = time.perf_counter()
        self._log(
            f"[scope] set_acquisition_mode({mode_u}, n_avg={n_avg}): "
            f"writing — scope reconfiguration may take a few seconds "
            f"in AVERAGE mode with large record lengths.")
        # Re-assert the data-transfer format every time so a stale front-panel
        # state (ASCII encoding, width 2 from a previous user) never corrupts
        # a capture.  Width is always 1 (int8) — see open() for the rationale.
        self._w(self._cmds.data_encoding_cmd)   # e.g. DATa:ENCdg RIBinary
        self._data_width = 1
        self._w("DATa:WIDth 1")

        self._w_checked(f"ACQuire:MODe {m}")
        if mode_u == "AVERAGE" and self._cmds.has_acq_numavg:
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
        if mode_u == "AVERAGE" and self._cmds.has_acq_numavg:
            try:
                got_n = int(float(self._q("ACQuire:NUMAVg?")))
            except Exception:
                return
            if got_n != int(n_avg):
                raise RuntimeError(
                    f"Scope NUMAVg mismatch: requested {n_avg}, "
                    f"device reports {got_n}.")
        _dt = time.perf_counter() - _t0
        self._log(
            f"[scope] set_acquisition_mode({mode_u}, n_avg={n_avg}): "
            f"OK, scope confirmed   (total {_fmt_elapsed(_dt)})")

    def acquisition_modes(self):
        modes = ["SAMPLE", "AVERAGE"]
        if self._cmds.has_hires:
            modes.append("HIRES")
        return modes

    def average_count_choices(self):
        return list(self._cmds.acq_numavg_values)

    def max_average_count(self) -> int:
        vals = self._cmds.acq_numavg_values
        return vals[-1] if vals else 16

    def set_trigger(self, source: str = "EXT", level_v: float = 1.0,
                    slope: str = "RISE", mode: str = "NORMAL",
                    *, digital: Optional[bool] = None) -> None:
        slope_word = "RISe" if slope.upper().startswith("R") else "FALL"
        # ---- Validate the requested source against scope capability ----
        # Tek firmware silently substitutes AUX for an unavailable
        # channel (e.g. writing ``TRIGger:A:EDGE:SOUrce CH4`` to a
        # 2-channel TBS1052C / TBS2052B).  Catch this up front so a
        # mis-configured Setup tab doesn't quietly route the trigger
        # to the wrong input — and so the per-step trigger-level
        # update logic doesn't fight a phantom AUX trigger that the
        # signal can never reach.
        src_check = (source or "").upper().strip()
        if src_check.startswith("CH") and src_check[2:].isdigit():
            req_ch_n = int(src_check[2:])
            avail_n = int(getattr(self.info, "n_channels", 4) or 4)
            if req_ch_n < 1 or req_ch_n > avail_n:
                # Map to the highest available channel as a recovery
                # default — better than letting the scope fall back
                # to AUX which has no relationship to our signal.
                fallback = f"CH{avail_n}"
                self._log(
                    f"[scope]   ⚠ requested trigger source {source!r} is "
                    f"out of range for this {avail_n}-channel scope — "
                    f"using {fallback} instead.  Fix the I_mon channel "
                    f"assignment in the Setup tab to silence this.")
                source = fallback
        if self._cmds.trig_type_edge_cmd:
            self._w(self._cmds.trig_type_edge_cmd)
        self._w(f"{self._cmds.trig_edge_source} {source}")
        # ---- Verify the scope accepted what we just sent ----
        # Read back immediately and compare.  Tek firmware
        # substitutes AUX for any source it doesn't recognise without
        # raising a SCPI error, so a silent verify is the only way to
        # catch that.  Match is substring-tolerant ("CH4" vs "CHAN4"
        # / "CH4" reported by different firmware revisions).
        try:
            readback = self._q(
                f"{self._cmds.trig_edge_source}?").strip().upper()
            req = source.upper().strip()
            if req and req not in readback and readback not in req:
                self._log(
                    f"[scope]   ⚠ trigger source readback mismatch: "
                    f"asked for {source!r}, scope reports {readback!r}. "
                    f"This usually means the requested channel doesn't "
                    f"exist on this scope (firmware silently substitutes "
                    f"AUX) — check the I_mon channel mapping in Setup.")
        except Exception:
            pass
        self._w(f"{self._cmds.trig_edge_slope} {slope_word}")
        try:
            self._w(f"{self._cmds.trig_edge_coupling} DC")
        except Exception:
            pass
        # For EXT (digital sync from the stimulator) the signal is a clean
        # 3.3 V / 5 V TTL edge. Do NOT overwrite the trigger level — the user
        # has it set correctly on the front panel and any fixed value we write
        # would just be wrong. For channel triggers (I_mon etc.) we DO write
        # the computed level because the signal amplitude changes every step.
        src_upper = source.upper()
        if not src_upper.startswith("EXT"):
            try:
                self._w(f"{self._cmds.trig_level} {level_v:g}")
            except Exception:
                pass
        self._w(f"{self._cmds.trig_mode} {mode.upper()}")
        # Match MATLAB ``setOscilloscope.m`` line 328: send
        # ``ACQuire:STAte RUN`` once at setup so the scope is armed.
        # We do NOT send ``ACQuire:STOPAfter`` anywhere — MATLAB
        # doesn't either, and toggling STOPAfter/STATE mid-sweep
        # forces a costly re-arm that discards waveforms the scope
        # captured while we were busy with the stimulator.
        try:
            self._w(f"{self._cmds.acq_state} RUN")
        except Exception:
            pass
        # If the trigger source is one of the input channels, force the
        # same bench defaults on that channel that we apply to mapped
        # data channels — otherwise a 10X probe attenuation or AC
        # coupling on the trigger channel will quietly make us miss
        # edges from a 3.3 V digital sync line.
        src_u = source.upper()
        if src_u.startswith("CH") and src_u[2:].isdigit():
            self.apply_channel_defaults(src_u)
            # When a channel is the trigger source it's effectively the
            # I_mon path (current monitor) — apply the same low-pass BW
            # limit the rest of the I_mon setup uses so the trigger
            # comparator sees the clean filtered signal.
            try:
                self.set_channel_bandwidth_for_purpose(src_u, "imon")
            except Exception:
                pass
        # Stash the requested settings so a periodic sanity check
        # (see ``_periodic_acq_check``) can verify nothing's drifted.
        self._expected_trigger_source = source.upper()
        self._expected_trigger_slope = slope_word.upper()
        # Decide whether the source is a TTL digital sync line.  The
        # caller can force it via ``digital=``; when left ``None`` we
        # auto-detect from the source string: ``EXT`` is always
        # digital (it's the dedicated TTL BNC).  A channel name
        # (``CH3`` / ``CH4`` / etc.) can be EITHER an I_mon trigger
        # (no delay) OR a Plexon-sync-on-channel trigger (1.2 µs
        # delay) — the runner / GUI knows which and should pass
        # ``digital=True`` explicitly in the channel-Trigger case.
        if digital is not None:
            self._expected_trigger_is_digital = bool(digital)
        else:
            self._expected_trigger_is_digital = src_check.startswith("EXT")

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
                got_src = self._q(f"{self._cmds.trig_edge_source}?").upper()
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

    def single_capture(self, *,
                       timeout_s: Optional[float] = None) -> ScopeAcquisition:
        """Acquire a single averaged waveform.

        Parameters
        ----------
        timeout_s:
            Override for the default VISA timeout (``self._timeout_ms``,
            10 s).  Pass when the runner can predict the required wait
            from ``N_avg / rate_hz`` — e.g. at 1 Hz pulse rate with 64
            averages the scope needs ~64 s to accumulate the requested
            count, so the default 10 s budget is exhausted while the
            averager is still half-empty.  When ``None`` falls back to
            the VISA-session timeout the scope was opened with.
            Soft timeout (MATLAB ``getWaveform.m`` semantics): on
            timeout we read the latest rolling-average frame rather
            than raise.
        """
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
        # Start continuous acquisition and wait for the averager to
        # actually finish accumulating N_AVG frames before we read
        # ``CURVe?``.  The previous behaviour polled ``TRIGger:STATE?``
        # and exited the moment the scope reported a single trigger,
        # which meant in AVERAGE mode (the experiment default — N=64)
        # we read a 1-frame-into-the-average trace.  That trace
        # carries the FULL single-shot noise, not the √64 reduction
        # the operator asked for, and an unsettled rolling-average
        # produces the visible offset / squiggle the user reported.
        #
        # We now mirror the calibration plot's acquisition path
        # (``capture_single_sequence`` / MATLAB ``getWaveform.m``):
        #
        #   * AVERAGE mode → poll ``ACQuire:NUMACq?`` until the count
        #     reaches the configured N_AVG (or the soft deadline
        #     fires, at which point we read whatever the rolling
        #     averager has — matching MATLAB).
        #   * SAMPLE / PEAK → still poll ``TRIGger:STATE?`` for a
        #     single trigger; there's no averager so one frame IS
        #     the captured frame.
        self._w("ACQuire:STAte RUN")
        n_avg_target = int(self._expected_acq_navg or 0)
        is_average_mode = (
            (self._expected_acq_mode or "").upper() == "AVERAGE"
            and n_avg_target > 0
        )
        # Caller-supplied timeout wins.  The default falls back to the
        # VISA session timeout (10 s by default).  At low pulse rates
        # 10 s is too tight (e.g. 64 averages × 100 ms period = 6.4 s,
        # +trigger latency → close to the limit; 64 × 1 s would need
        # 64+ s).  Experiment runners should compute
        # ``n_avg / rate_hz + headroom`` and pass it down.
        if timeout_s is None:
            timeout_s = self._timeout_ms / 1000.0
        else:
            timeout_s = float(timeout_s)
        deadline = time.time() + timeout_s
        if is_average_mode:
            # Same loop body capture_single_sequence uses: poll
            # NUMACq? at ~50 ms cadence, break on count >= target,
            # never raise on timeout (read the rolling average
            # anyway, matching MATLAB getWaveform.m).
            count = 0
            n_polls = 0
            n_errors = 0
            t0 = time.perf_counter()
            self._log(
                f"[scope]   waiting for {n_avg_target} acquisitions "
                f"(AVERAGE mode, timeout {timeout_s:.1f} s)…")
            while time.time() < deadline:
                try:
                    raw = self._inst.query("ACQuire:NUMACq?").strip()
                    n_polls += 1
                    count = int(float(raw))
                    if count >= n_avg_target:
                        break
                except Exception:
                    n_errors += 1
                time.sleep(0.05)
            if count >= n_avg_target:
                self._log(
                    f"[scope]   acquired {count} frames in "
                    f"{_fmt_elapsed(time.perf_counter() - t0)} "
                    f"({n_polls} polls"
                    + (f", {n_errors} errors" if n_errors else "")
                    + ")")
            else:
                self._log(
                    f"[scope]   poll deadline ({timeout_s:.1f} s) "
                    f"reached, NUMACq = {count}/{n_avg_target} — "
                    f"reading the latest averaged frame anyway.")
        else:
            # SAMPLE / PEAK / unknown mode — single-trigger semantics.
            triggered = False
            while time.time() < deadline:
                if self._check_triggered():
                    triggered = True
                    break
                # 10 ms inter-poll keeps the loop responsive without
                # saturating the USB-TMC bus.
                time.sleep(0.010)
            if not triggered:
                raise TimeoutError(
                    "Scope was not triggered within the timeout. "
                    "Check trigger source, level, and cable connections.")

        # Determine which channels to fetch
        wanted_channels = sorted(set(self.channel_aliases.values()))
        out_channels: Dict[str, np.ndarray] = {}
        # Reusable time axis across the per-channel loop. The horizontal
        # scaling (XINCr / XZEro) is identical across all channels in
        # ONE acquisition — re-running ``xzero + xinc * np.arange(N)``
        # for every channel just burns ~100-200 µs / channel allocating
        # a fresh 20k-sample float64 array. Build it on the first
        # channel and pass it down to subsequent ``_read_channel`` calls.
        time_us = np.empty(0)
        sample_period_us = 0.0
        record_length = 0

        for ch in wanted_channels:
            t_us, y_v, dt_us, n = self._read_channel(
                ch, cached_time_us=time_us if time_us.size else None)
            if time_us.size == 0:
                time_us = t_us
                sample_period_us = dt_us
                record_length = n
            out_channels[ch] = y_v

        return ScopeAcquisition(
            time_us=time_us, channels=out_channels,
            sample_period_us=sample_period_us, record_length=record_length,
        )

    def _check_triggered(self) -> bool:
        """Return True if the scope has fired at least once since the last
        ``ACQuire:STAte RUN`` command.

        Queries ``TRIGger:STATE?``. The scope is considered *triggered*
        when the state is anything other than ``ARMED`` or ``READY`` (both
        mean "waiting for the first edge"). Returns True conservatively on
        any query failure so a VISA hiccup doesn't abort an otherwise
        good run.

        **Logging:** intentionally bypasses ``self._q`` and uses the
        raw ``self._inst.query`` directly so it does NOT route through
        ``_log``.  This is a fast-poll site (called every loop tick
        while waiting for the first trigger) and routing each call
        through the logger would dump dozens of
        ``[scope] > TRIGger:STATE? < READY`` lines per capture into
        the GUI log pane — pure noise that drowns out the useful
        log lines.  Same pattern as the ``NUMACq?`` poll in
        :meth:`capture_n_acquisitions`.
        """
        if self._inst is None:
            return True
        try:
            state = self._inst.query("TRIGger:STATE?").strip().upper()
            return state not in ("ARMED", "READY")
        except Exception:
            return True  # conservative: don't block on VISA hiccup

    def capture_while_running(self, wait_s: float = 0.0,
                              *, reset_before_run: bool = False,
                              tick_fn=None) -> ScopeAcquisition:
        """Acquire one averaged waveform using continuous-run mode (MATLAB style).

        Mirrors the MATLAB acquisition sequence:

          1. ``ACQuire:STOPAfter RUNSTop`` — continuous mode; scope keeps
             running and updating the waveform memory on every trigger.
          2. ``ACQuire:STAte STOP`` (when reset_before_run) — clears the
             averaging counter so this measurement starts fresh.
          3. ``ACQuire:STAte RUN`` — arm the trigger system.
          4. Sleep ``wait_s`` seconds — long enough for N_avg pulse periods
             to accumulate a clean average in waveform memory.
          5. Read all wanted channels while the scope is still running.

        Parameters
        ----------
        wait_s:
            How long to wait (seconds) before reading.  Should be at least
            ``N_avg / rate_hz`` so the average is fully accumulated.
        reset_before_run:
            Send ``ACQuire:STAte STOP`` before arming so the averaging counter
            resets to zero.  Prevents bleed from the previous amplitude step
            in a multi-cell calibration sweep.
        """
        # Per the lab convention: do NOT send ``ACQuire:STOPAfter
        # RUNSTop`` or cycle ``ACQuire:STATE STOP``/``RUN`` mid-sweep.
        # The scope was put into continuous mode at setup time and
        # is already free-running — toggling these forces a costly
        # re-arm and discards anything captured in the window we
        # were busy with the stimulator.  Just wait ``wait_s`` for
        # fresh frames to accumulate in the averager.

        # Step 4 — wait for N_avg pulse periods to accumulate.
        # Sleep in 50 ms chunks so a tick_fn (e.g. processEvents) can run.
        remaining = max(float(wait_s), 0.0)
        while remaining > 0.0:
            chunk = min(0.05, remaining)
            time.sleep(chunk)
            remaining -= chunk
            if tick_fn is not None:
                tick_fn()

        # Step 5 — read channels (scope still running; data is stable after wait)
        wanted_channels = sorted(set(self.channel_aliases.values()))
        out_channels: Dict[str, np.ndarray] = {}
        time_us = np.empty(0)
        sample_period_us = 0.0
        record_length = 0

        for ch in wanted_channels:
            t_us, y_v, dt_us, n = self._read_channel(
                ch, cached_time_us=time_us if time_us.size else None)
            if time_us.size == 0:
                time_us = t_us
                sample_period_us = dt_us
                record_length = n
            out_channels[ch] = y_v

        return ScopeAcquisition(
            time_us=time_us, channels=out_channels,
            sample_period_us=sample_period_us, record_length=record_length,
        )

    def capture_single_sequence(self, *, n_acq: int = 16,
                                timeout_s: float = 30.0,
                                tick_fn=None) -> ScopeAcquisition:
        """Acquire a waveform after polling that ``n_acq`` frames accumulated.

        Mirrors the MATLAB ``getWaveform.m`` acquisition loop:

          1. ``ACQuire:STOPAfter RUNSTop`` — keep scope in continuous mode.
          2. ``ACQuire:STAte STOP`` then ``ACQuire:STAte RUN`` — reset the
             acquisition counter and arm the trigger.
          3. Poll ``ACQuire:NUMACq?`` every ~50 ms until the reported count
             is ≥ ``n_acq`` or ``timeout_s`` elapses.
          4. Read all wanted channels from the stable waveform memory.

        ``ACQuire:NUMACq?`` returns the number of waveforms captured since the
        last arm — not the averaging count — so this works for both SAMPLE and
        AVERAGE modes.  Waiting for N acquisitions guarantees the scope has
        actually triggered (count = 0 means no triggers seen yet) and that the
        averager has accumulated the requested number of frames.

        Parameters
        ----------
        n_acq:
            Minimum number of acquired waveforms to wait for.  Should be at
            least equal to the configured ``ACQuire:NUMAVg`` count so the
            averaged waveform is fully populated before reading.
        timeout_s:
            Maximum wait in seconds.  MATLAB-style soft timeout: if
            ``NUMACq?`` doesn't reach ``n_acq`` in time we log the
            shortfall and read the latest waveform anyway (matching
            ``getWaveform.m`` which never raises here — it just
            reads whatever the rolling averager currently holds).
        tick_fn:
            Callable invoked on every poll so Qt can process events.
        """
        # Match MATLAB ``getWaveform.m`` (lines 246-271) exactly:
        # send ``ACQuire:STAte RUN`` (idempotent if the scope is
        # already running), poll ``NUMACq?`` until it reaches
        # ``n_acq`` OR ``timeout_s`` elapses, then read.  NO
        # ``STOPAfter`` and NO ``STATE STOP`` — those would force a
        # costly re-arm and discard whatever the scope captured
        # while we were uploading the pattern.
        self._w(f"{self._cmds.acq_state} RUN")

        # Step 3 — poll NUMACq? until enough frames have been captured.
        # MATLAB ``getWaveform.m`` logs this as ONE line ("Waiting for N
        # acquisitions…") then a summary line on success — NOT one log
        # entry per poll, otherwise a 30 s timeout floods the LogPane
        # with 600 identical lines.  We bypass ``self._q`` (which logs
        # every call) and call the raw VISA ``query`` directly inside
        # the poll loop, then emit a single before / after pair around
        # it so the operator still sees what the scope is doing.
        # Poll loop — MATLAB ``getWaveform.m`` semantics:
        #
        #   while count <= n_acq
        #       count = query(ACQuire:NUMACq?);
        #       if toc(t0) > timeout_s, break, end
        #   end
        #
        # MATLAB does NOT raise on timeout; it just bails out and
        # reads whatever waveform is in memory.  This works because
        # in continuous AVERAGE mode the scope is always updating a
        # rolling averaged frame, so the "latest" frame is always a
        # clean average of the last NUMAVg captures regardless of
        # whether the counter advanced during this particular poll
        # window.  We match that: log the outcome and read CURVe,
        # without raising.
        self._log(
            f"[scope]   waiting for {int(n_acq)} acquisitions "
            f"(timeout {timeout_s:.1f} s, MATLAB getWaveform.m semantics)…")
        t_poll_start = time.perf_counter()
        deadline = t_poll_start + float(timeout_s)
        count = 0
        n_polls = 0
        n_errors = 0
        last_err_msg = ""
        while time.time() < deadline:
            try:
                raw = self._inst.query("ACQuire:NUMACq?").strip()
                n_polls += 1
                count = int(float(raw))
                if count >= int(n_acq):
                    break
            except Exception as _poll_err:
                # Throttled error logging: emit the first failure
                # immediately, then every 50th to indicate a hung scope
                # without flooding the log with hundreds of identical
                # USB-TMC error lines.  ``n_errors`` accumulates so the
                # post-loop summary can report whether the timeout was
                # caused by a healthy "not enough frames yet" or by
                # persistent query failures (very different
                # diagnostics for the operator).
                n_errors += 1
                last_err_msg = f"{type(_poll_err).__name__}: {_poll_err}"
                if n_errors == 1 or (n_errors % 50) == 0:
                    self._log(
                        f"[scope]   NUMACq? poll error "
                        f"#{n_errors}: {last_err_msg}")
            time.sleep(0.05)
            if tick_fn is not None:
                tick_fn()
        if count >= int(n_acq):
            self._log(
                f"[scope]   acquired {count} frames in "
                f"{_fmt_elapsed(time.perf_counter() - t_poll_start)} "
                f"({n_polls} polls"
                + (f", {n_errors} errors" if n_errors else "")
                + ")")
        else:
            # MATLAB-style soft timeout: read whatever's in memory.
            self._log(
                f"[scope]   poll deadline ({timeout_s:.1f} s) reached, "
                f"NUMACq = {count}/{int(n_acq)} "
                + (f"after {n_errors} poll errors "
                   f"(last: {last_err_msg}) " if n_errors else "")
                + f"— reading the latest frame anyway.")

        # Step 4 — read all wanted channels.
        wanted_channels = sorted(set(self.channel_aliases.values()))
        out_channels: Dict[str, np.ndarray] = {}
        time_us = np.empty(0)
        sample_period_us = 0.0
        record_length = 0

        for ch in wanted_channels:
            t_us, y_v, dt_us, n = self._read_channel(
                ch, cached_time_us=time_us if time_us.size else None)
            if time_us.size == 0:
                time_us = t_us
                sample_period_us = dt_us
                record_length = n
            out_channels[ch] = y_v

        return ScopeAcquisition(
            time_us=time_us, channels=out_channels,
            sample_period_us=sample_period_us, record_length=record_length,
        )

    def _read_channel(self, ch: str,
                      cached_time_us: Optional[np.ndarray] = None,
                      ) -> Tuple[np.ndarray, np.ndarray, float, int]:
        """Fetch one channel, return (time_us, voltage_v, sample_period_us, npts).

        Uses Tek's standard conversion formula:

            V = (raw_dl - YOFf) * YMUlt + YZEro

        where ``raw_dl`` is the integer "digitizing level" (0..255 for 8-bit
        scopes, 0..65535 for 16-bit). ``WFMOutpre:`` tells us all five
        scaling constants in a single semicolon-separated response, so we
        only pay one VISA round-trip for the preamble per channel.

        ``cached_time_us`` lets ``single_capture`` reuse the time axis it
        already built for the first channel. The horizontal scaling is
        identical across all channels in one acquisition, so the array
        can be passed through and the per-channel arange + multiply
        skipped (~100-200 µs / channel on a 20k-sample record).
        """
        # Ensure the channel is displayed on screen.  CURVe? returns
        # error 2244 ("waveform not activated") for a channel that is
        # turned off (SELect:CH<x> OFF), even if DATa:SOUrce points to it.
        self._w(f"SELect:{ch} ON")

        # Pick which channel CURVe? will read from. DATa:STARt / STOP and
        # the binary-encoding settings are already established at open()
        # and survive across captures, so we don't re-send them here.
        if self._cmds.use_data_source:
            self._w(f"DATa:SOUrce {ch}")

        pre = self._cmds.preamble
        ymult, yoff, yzero, xinc, xzero, pt_off, is_signed, is_big_endian = (
            self._read_preamble(pre))

        # ----- Curve as a binary stream ----------------------------------
        # dtype and byte-order are derived from WFMOutpre:BN_Fmt? / BYT_Or?
        # rather than assumed from DATa:ENCdg, so we correctly handle
        # whatever the scope actually sends (signed vs unsigned, MSB vs LSB).
        #   width 2: 'h' (int16) or 'H' (uint16)
        #   width 1: 'b' (int8)  or 'B' (uint8)
        if self._inst is None: raise RuntimeError("Scope not open")
        # Always int8 (signed 'b' or unsigned 'B') — DATa:WIDth is fixed at 1.
        datatype = "b" if is_signed else "B"
        _t0 = time.perf_counter()
        raw = self._inst.query_binary_values(
            "CURVe?", datatype=datatype, is_big_endian=is_big_endian,
            container=np.ndarray,
        )
        self._log(
            f"[scope] > CURVe? ({ch}, dtype={datatype}, "
            f"{'MSB' if is_big_endian else 'LSB'})   "
            f"< {raw.size} samples, min={int(raw.min())}, max={int(raw.max())}   "
            f"({_fmt_elapsed(time.perf_counter() - _t0)})")
        y_v = (raw.astype(np.float64) - yoff) * ymult + yzero
        # Time axis — three methods (per MATLAB's getTime.m, getTime2.m,
        # and getSettings.m's batch path):
        #
        #   Method A:  t_us = (n × XINcr + XZEro) × 1e6        getTime.m
        #              t=0 at trigger event.  Used for channel triggers.
        #
        #   Method A':  Method A − DIGITAL_DELAY_US             getTime2.m
        #              For EXT trigger, shift left by 1.2 µs so t=0 lands
        #              on the actual stim phase-1 onset instead of the
        #              sync TTL edge.
        #
        #   Method B:  t_us = (n − PT_Off) × XINcr × 1e6        getSettings.m
        #              PT_Off is the trigger sample index from the
        #              preamble.  Equivalent to A when XZEro is correct;
        #              the integer field PT_Off is the fallback we use
        #              when the scope's XZEro looks suspect (some
        #              firmware reports XZEro = 0 even with a non-zero
        #              horizontal position).
        #
        # Cross-check: compute Method-A's implied PT_Off as
        # ``-xzero / xinc``.  If it disagrees with the preamble's PT_Off
        # by more than half a sample, the scope's XZEro is unreliable —
        # fall back to Method B's PT_Off-based formula.
        # Apply the Plexon digital-sync delay whenever the trigger is
        # a TTL sync line — that's true for the EXT BNC AND for any
        # scope channel the operator tagged with Role=Trigger (CH3 /
        # CH4 wired to the same sync wire just routed to a different
        # physical input).  The 1.2 µs offset between TTL edge and
        # actual phase-1 stim onset is a property of the stimulator,
        # not of which scope input the sync lands on, so the
        # correction is identical for both paths.  ``set_trigger``
        # populates ``_expected_trigger_is_digital`` from the
        # ``digital`` kwarg or, when the caller doesn't specify,
        # from ``source.startswith("EXT")``.
        is_digital = bool(self._expected_trigger_is_digital)
        digital_delay_us = DIGITAL_DELAY_US if is_digital else 0.0
        npts = raw.size

        # Time-axis source: trust the scope's ``XZEro`` (= time of
        # the first captured sample, relative to the trigger event).
        # This is what MATLAB does (see matlab_reference/getTime.m,
        # which queries WFMPre:XZEro? in a retry-until-sane loop and
        # explicitly comments OUT the PT_Off path at line 61 —
        # "PT_OFf = Settings.TriggerOffset" with a leading %).
        #
        # Why XZEro is authoritative (and NOT derivable from the
        # host-cached scale + position):
        #
        #   TBS2000-family scopes capture records SYMMETRIC around the
        #   trigger event, REGARDLESS of the displayed ``HORizontal:
        #   POSition`` percentage.  POSition only controls what slice
        #   of the captured record is VISIBLE on the scope screen — it
        #   does NOT change where the trigger sits within the record.
        #   So for a 20000-sample record at 32 ns/sample (= 640 µs total)
        #   on TBS2204B, the trigger is at sample 10000 (XZEro = -320 µs)
        #   even when POSition=20% (which would naively imply XZEro = -120
        #   µs).  Empirically confirmed in test/session_001_electrode_a1
        #   _log.txt:
        #     line 90:  HORizontal:POSition? < 20.0000   (scope position % = 20)
        #     line 292: WFMOutpre:XZEro?     < -320.0000E-6  (actual first sample at -320 µs)
        #
        # We previously tried two wrong approaches:
        #
        #   1. Trust PT_Off over XZEro on any disagreement — broke
        #      TBS2204B because its PT_Off always reports 0 (firmware
        #      quirk) even when XZEro correctly reports -320 µs.
        #      Symptom: pulse appeared at +320 µs instead of t=0.
        #
        #   2. Compute xzero from cached scale × position % — broke
        #      because the formula assumes the record is sized to fit
        #      the visible window with POSition determining the
        #      pre/post split, but TBS2000 actually captures a
        #      symmetric record independent of POSition.  Same +320 vs
        #      0 µs symptom on a different scope.
        #
        # The right answer: do what MATLAB does — trust XZEro.  Only
        # fall back to PT_Off when XZEro is *exactly* zero (the
        # original firmware quirk on TDS1000-era scopes that this
        # cross-check was originally added for).
        used_method = "A (XZEro)"
        xz_used = xzero
        if abs(xzero) < 0.5 * xinc:
            # XZEro is effectively zero — either no pre-trigger
            # configured OR the legacy firmware quirk where XZEro=0
            # is reported even with non-zero horizontal position.
            # Fall back to PT_Off only when it's non-zero (i.e.,
            # it's TELLING us there IS pre-trigger that XZEro is
            # hiding).
            if (np.isfinite(pt_off) and float(pt_off) > 0
                    and xinc > 0):
                xz_used = -float(pt_off) * float(xinc)
                used_method = "B (PT_Off; XZEro=0 firmware quirk)"

        cache_key = (xz_used, xinc, npts, digital_delay_us)
        if (cached_time_us is not None and cached_time_us.size == npts):
            t_us = cached_time_us
        elif cache_key == self._time_cache_key and self._time_cache is not None:
            t_us = self._time_cache
        else:
            t_us = (xz_used + xinc * np.arange(npts)) * 1e6 - digital_delay_us
            self._time_cache = t_us
            self._time_cache_key = cache_key
            try:
                _pt_str = (f"{pt_off:.1f}" if np.isfinite(pt_off)
                           else "<not reported>")
                # Route through self._log() so the line lands in the
                # LogPane AND the on-disk .txt log alongside the SCPI
                # traffic.  XUNIT / YUNIT come from the preamble — verify
                # they're seconds and volts before trusting the math.
                self._log(
                    f"[scope-time] ch={ch} "
                    f"XZEro={xzero*1e6:+.3f} us "
                    f"PT_Off={_pt_str} "
                    f"XINcr={xinc*1e6:.4f} us "
                    f"npts={npts} "
                    f"XUNit={self._last_xunit!r} "
                    f"YUNit={self._last_yunit!r} "
                    f"trig_src={src or '<unset>'} "
                    f"axis-method={used_method} "
                    f"digital_delay={digital_delay_us:.2f} us "
                    f"t_us=[{t_us[0]:+.3f} .. {t_us[-1]:+.3f}]"
                )
                if self._last_xunit and self._last_xunit.lower() not in ("s",):
                    self._log(
                        f"[scope]   ⚠ XUNit is {self._last_xunit!r} — "
                        f"time-axis math assumes seconds!")
                if self._last_yunit and self._last_yunit.lower() not in ("v", "volts"):
                    self._log(
                        f"[scope]   ⚠ YUNit is {self._last_yunit!r} — "
                        f"channel data may not be in volts!")
            except Exception:
                pass
        return t_us, y_v, xinc * 1e6, raw.size

    def _read_preamble(self, preamble_root: str):
        """Query the preamble in ONE batch round-trip and parse it.

        Issues a single ``WFMOutpre?`` (or ``WFMPre?`` on legacy) query
        that returns every preamble field semicolon-separated.  Mirrors
        MATLAB ``getSettings.m``'s batch path — 1 round-trip instead of
        the 6-8 that per-field queries take, which is ~5× faster per
        capture on USB-TMC (50-80 ms saved per acquisition).

        Returns ``(ymult, yoff, yzero, xinc, xzero, pt_off, is_signed,
        is_big_endian)``.  Falls back to per-field queries if the batch
        parse fails (e.g. unfamiliar firmware response format) — better
        to be slow than wrong.

        Response format (per TBS2000B Programmer Manual page 191, with
        ``VERBose ON`` enabled in :meth:`open`):

        .. code::

            :WFMOUTPRE:BYT_NR 1;BIT_NR 8;ENCDG BIN;BN_FMT RI;
            BYT_OR MSB;WFID "Ch1, DC coupling, ...";NR_PT 2000;
            PT_FMT Y;XUNIT "s";XINCR 1.25E-7;XZERO -7.5E-5;
            PT_OFF 600;YUNIT "V";YMULT 1.5625E-5;YOFF 0.0;YZERO 0.0

        WFID is double-quoted and contains commas, but no unescaped
        semicolons — so a plain ``split(';')`` is safe.  Each chunk is
        ``FIELDNAME VALUE``; we parse via regex so we're robust against
        firmware that omits the space or adds extra whitespace.
        """
        fields: Dict[str, str] = {}
        try:
            raw = self._q(f"{preamble_root}?")
            fields = self._parse_batch_preamble(raw)
            # Required-field lookup with explicit name in the error
            # so diagnostics show WHICH field the firmware omitted
            # instead of a bare KeyError.
            required = ("YMULT", "YOFF", "YZERO", "XINCR", "XZERO")
            missing = [k for k in required if k not in fields]
            if missing:
                raise KeyError(
                    f"preamble missing required field(s): "
                    f"{', '.join(missing)}  (got keys: "
                    f"{sorted(fields.keys())})")
            ymult = float(fields["YMULT"])
            yoff  = float(fields["YOFF"])
            yzero = float(fields["YZERO"])
            xinc  = float(fields["XINCR"])
            xzero = float(fields["XZERO"])
            pt_off = float(fields.get("PT_OFF", "nan"))
            is_signed     = fields.get("BN_FMT", "RI").upper().startswith("RI")
            is_big_endian = fields.get("BYT_OR", "MSB").upper().startswith("MSB")
            # Pull horizontal/vertical units the scope reports (XUNit /
            # YUNit) so downstream code can verify it's actually
            # seconds/volts and not (say) Hz/dB from a math channel.
            # Stored on the instance for callers that want to query.
            self._last_xunit = fields.get("XUNIT", "s")
            self._last_yunit = fields.get("YUNIT", "V")
            return (ymult, yoff, yzero, xinc, xzero, pt_off,
                    is_signed, is_big_endian)
        except Exception as e:
            self._log(f"[scope]   batch preamble parse failed ({e}); "
                      f"falling back to per-field queries.")
        # Slow fallback — per-field queries
        return self._read_preamble_per_field(preamble_root)

    @staticmethod
    def _parse_batch_preamble(raw: str) -> Dict[str, str]:
        """Parse a ``WFMOutpre?`` batch response into a NAME → value dict.

        Strips the leading ``:WFMOUTPRE:`` namespace, splits on
        semicolons, then a per-chunk regex pulls out ``FIELDNAME`` and
        ``VALUE``.  Quoted string values have their surrounding double
        quotes stripped.  Keys are upper-cased for stable lookup.
        """
        import re
        out: Dict[str, str] = {}
        text = raw.strip().lstrip(":").rstrip(".").strip()
        # Strip the leading namespace prefix from the first field (the
        # rest don't include it). Tolerate both modern and legacy forms.
        for prefix in ("WFMOUTPRE:", "WFMPRE:"):
            if text.upper().startswith(prefix):
                text = text[len(prefix):]
                break
        for chunk in text.split(";"):
            chunk = chunk.strip().lstrip(":")
            if not chunk:
                continue
            # Some VERBose firmware concatenates name + value with no
            # space (e.g. "BYT_NR2") — handle both cases with a regex.
            m = re.match(r"([A-Za-z_]+)\s*(.*)", chunk)
            if not m:
                continue
            name = m.group(1).upper()
            value = m.group(2).strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            out[name] = value
        return out

    def _read_preamble_per_field(self, preamble_root: str):
        """Slow per-field preamble query — fallback when batch parse fails.

        Same shape as the original implementation that pre-dated the
        batch optimisation: one query per field, 6-8 round-trips total.
        Kept so a future firmware that breaks the batch parser doesn't
        leave the driver unable to read its own captures.
        """
        ymult = float(self._q(f"{preamble_root}:YMUlt?"))
        yoff  = float(self._q(f"{preamble_root}:YOFf?"))
        yzero = float(self._q(f"{preamble_root}:YZEro?"))
        xinc  = float(self._q(f"{preamble_root}:XINcr?"))
        xzero = float(self._q(f"{preamble_root}:XZEro?"))
        try:
            pt_off = float(self._q(f"{preamble_root}:PT_Off?"))
        except Exception:
            pt_off = float("nan")
        try:
            is_signed = self._q(f"{preamble_root}:BN_Fmt?").strip().upper().startswith("RI")
        except Exception:
            is_signed = True
        try:
            is_big_endian = self._q(f"{preamble_root}:BYT_Or?").strip().upper().startswith("MSB")
        except Exception:
            is_big_endian = True
        # Refresh the cached X/Y units so the time-axis log line shows
        # them in the fallback path too.
        try:
            self._last_xunit = self._q(f"{preamble_root}:XUNit?").strip().strip('"')
        except Exception:
            pass
        try:
            self._last_yunit = self._q(f"{preamble_root}:YUNit?").strip().strip('"')
        except Exception:
            pass
        return ymult, yoff, yzero, xinc, xzero, pt_off, is_signed, is_big_endian

    # ----- adapt aliases to physical channels --
    def configure_channels(self, alias_to_phys: Dict[str, str]) -> None:
        super().configure_channels(alias_to_phys)
        used = set(alias_to_phys.values())
        # Turn OFF every channel first so only the mapped ones are active.
        # Unused enabled channels produce CURVe? errors and add noise.
        n_ch = int(getattr(self.info, "n_channels", 4) or 4)
        for i in range(1, n_ch + 1):
            ch = f"CH{i}"
            try:
                self._w(f"SELect:{ch} {'ON' if ch in used else 'OFF'}")
            except Exception:
                pass
        # Apply canonical bench defaults to each active channel.
        for ch in used:
            self.apply_channel_defaults(ch)
