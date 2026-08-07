"""Real Plexon PlexStim 2.0 driver.

Wraps the vendored ``pyplexstim`` library so the rest of the codebase only
sees the abstract :class:`stimtest.hardware.base.Stimulator` interface.

This module imports the DLL lazily so the rest of the project can still be
imported on systems where the Plexon SDK isn't installed (notably for unit
tests, CI, and offline GUI development).
"""
from __future__ import annotations

import functools
import os
import threading
import time
from pathlib import Path
from typing import Optional

from ..config import (
    IMON_SCALING_DEFAULT, IMON_SCALING_NIL, NIL_SERIAL_NUMBERS,
    VMON_SCALING_DEFAULT, VMON_SCALING_NIL,
)
from ..waveforms import PulsePattern
from .base import Stimulator, StimulatorInfo


def _vendored_bin_dir() -> str:
    return str(Path(__file__).with_name("pyplexstim") / "bin")


from .base import fmt_elapsed as _fmt_elapsed  # noqa: F401
# Single source of truth for the MATLAB ``getEndTime.m`` port lives
# in :mod:`stimtest.hardware.base`.  Re-exported under the leading-
# underscore name so existing in-module call sites keep working.


def _dll_locked(method):
    """Acquire ``self._dll_lock`` for the duration of ``method``.

    Applied to every public method on :class:`PlexonStimulator` that
    touches the PlexStim DLL, so concurrent calls from the worker
    thread (experiment runner) and the GUI thread (Connection-panel
    Close, scaling combo, prefs restore) serialise rather than
    racing the DLL's heap.

    CLAUDE.md §3 calls out the PlexStim DLL as not thread-safe
    ("single producer only").  Two threads hitting the DLL at the
    same instant has been observed corrupting its heap, killing
    the whole process with Windows status ``0xC0000374`` /
    exit code ``-1073740940`` and no Python traceback.

    The lock is re-entrant (``threading.RLock``) so a decorated
    method can call another decorated method on the same instance
    without deadlocking — e.g. ``reinit`` → ``close`` → ``open``.
    """
    @functools.wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._dll_lock:
            return method(self, *args, **kwargs)
    return wrapped


class PlexonStimulator(Stimulator):
    """Concrete PlexStim driver."""

    def __init__(self, stim_index: int = 1,
                 dll_path: Optional[str] = None):
        # Lazy import so this file can be loaded without the DLL present
        from .pyplexstim.pyplexstimlib import PyPlexStim, PS_OK

        # ``dll_path`` lets the GUI point at a user-installed PlexStim
        # SDK location instead of the vendored ``bin`` directory. ``None``
        # falls back to the vendored copy so existing call sites keep
        # working unchanged.
        path = dll_path or _vendored_bin_dir()
        self._lib = PyPlexStim(plexstim_dll_path=path)
        if not self._lib.dll_init:
            raise RuntimeError(
                f"Could not load PlexStim DLL from {path!r}. "
                f"Plexon SDK installed?"
            )
        self._PS_OK = PS_OK
        self._stim_n = stim_index
        self.info = StimulatorInfo()
        # Pinned .pat path. Created once on the first load and reused
        # for every subsequent ``_load_arbitrary`` call so a long
        # sweep doesn't churn through hundreds of tempfile create /
        # unlink syscalls. Cleaned up by ``close()``.
        self._pat_path: Optional[str] = None
        # Cache of the most-recent .pat file *content signature* —
        # covers ONLY the phase tuple (what's actually in the .pat
        # file). Rate and repetitions are programmed via separate
        # SDK calls (PS_SetPeriod / PS_SetRepetitions) and don't
        # invalidate the file; previously we conflated all three in
        # one signature, so every rate change forced a rewrite + fsync
        # even though the bytes were unchanged. Splitting the
        # signatures saves ~5-50 ms / sweep step on slow storage.
        self._pat_content_signature: Optional[tuple] = None
        # Set of channel numbers currently in the "pattern loaded"
        # state on the device. Mutated by load_channel; cleared by
        # open() and close() since both go through PS_InitAllStim /
        # PS_CloseAllStim which wipe device-side patterns. Used by
        # the experiment runners to decide whether a configuration
        # change requires a reinit (see Stimulator.loaded_channels).
        self._loaded_channels: set = set()
        # ``_is_open`` — DEVICE-LEVEL open/close state tracking.
        # Set True after a successful ``ps_init_all_stim``; False after
        # ``ps_close_all_stim`` or an init failure.  Used by
        # :meth:`open` to SKIP ``ps_close_all_stim`` when the device is
        # already known-closed.
        #
        # **Why this matters**: calling ``ps_close_all_stim`` multiple
        # times in quick succession on an already-closed device has
        # been observed to HEAP_CORRUPT the vendor DLL (Windows
        # 0xC0000374).  Worst case observed: 4 calls within 1 second
        # — GUI Stop close + GUI Start open's internal close + runner
        # reinit close + runner reinit open's internal close.  The
        # last call crashes.  With this flag, the GUI's open after a
        # GUI close sees ``_is_open=False`` and skips the redundant
        # close, dropping the call count to 1 (the init itself).
        #
        # The flag is also wiped by :meth:`__init__` so a fresh
        # ``PlexonStimulator()`` correctly skips the close on first
        # ``open()`` (nothing to close yet).
        self._is_open: bool = False
        # Running-state flag — True between a start (start_all /
        # start_channel) and the next stop_all / abort_all.  Makes
        # ``stop_all`` IDEMPOTENT so the sweep's redundant
        # PS_StopStimAllChannels calls (the per-capture ``finally`` stop
        # + the next step's explicit pre-load stop, gotcha #15) collapse
        # to a single DLL round-trip instead of firing twice back-to-back.
        # Invariant: ``_is_running == False`` ⇒ the device is genuinely
        # stopped (only the real-stop paths clear it; any start sets it),
        # so skipping a stop when False can never leave the device live.
        self._is_running: bool = False
        # Channels that have successfully passed period / repetitions
        # read-back validation since the most recent open(). The
        # read-back catches a class of firmware quirks (silent rounding
        # during an active stim, .pat-load races) — once a channel has
        # passed once after open(), subsequent sweep steps run with
        # the SAME firmware path and we can skip the 2 extra USB
        # round trips per step (~80-120 ms saved on a 50-step sweep).
        # Set is wiped by open()/close() since reinit changes the
        # firmware state.
        self._validated_channels: set = set()
        # Value-keyed per-channel timing caches.  PS_SetPeriod (rate) and
        # PS_SetRepetitions are SEPARATE device state — NOT embedded in
        # the .pat file (see _load_arbitrary's content_signature comment)
        # — and the device PERSISTS them across .pat reloads and
        # stop/start cycles.  So once a channel's rate + repetitions are
        # programmed they don't need re-setting every sweep step
        # (operator: "rate and repetitions do not need to be set for
        # every iteration if all involved channels are set at the
        # beginning").  load_channel / set_repetitions skip the DLL call
        # when the requested value already matches the cache; a genuine
        # rate / reps CHANGE (different value) re-programs and re-caches.
        # Wiped by open()/close() since PS_InitAllStim resets the device.
        self._channel_period_ms: dict = {}
        self._channel_reps: dict = {}
        # Per-channel .pat CONTENT signature currently loaded ON THE
        # DEVICE.  The device retains each channel's arbitrary pattern
        # across stop/start cycles (only open()/close()/reinit() —
        # PS_InitAllStim — clears it), so re-issuing
        # ps_set_pattern_type / ps_load_arb_pattern / ps_load_channel for
        # a channel that already holds the EXACT same pattern is pure
        # redundant USB-TMC traffic.  A VT sweep reloads the 15 static
        # zero-amplitude unused channels on EVERY amplitude step even
        # though their pattern never changes (operator: efficiency — that
        # was ~120 s of a 15-min 16-channel run).  load_channel skips the
        # ~4 DLL round-trips when (content, period, reps) all match this
        # cache AND the channel is already in ``_loaded_channels``.
        # Wiped by open()/close() alongside the timing caches.
        self._channel_content_sig: dict = {}
        # User's auto-discharge preference, applied on every open() so
        # the setting survives reinit cycles. ``None`` means "leave at
        # the SDK default (enabled)" — the panel hasn't pushed an
        # explicit preference yet.
        self._auto_discharge_pref: Optional[bool] = None
        # Optional command logger.  When set, every SDK call routed
        # through ``_check`` logs its ``what`` description (which already
        # includes the SDK function name and arg summary).  The GUI's
        # LogPane wires this up after a successful Initialize so the
        # user sees every PlexStim call in their session log.
        from typing import Callable as _Cb
        self.cmd_logger: Optional[_Cb[[str], None]] = None
        # Serialise every DLL call across threads.
        # CLAUDE.md §3 calls out the PlexStim DLL as not thread-safe
        # ("single producer only").  In practice the bench has two
        # producers in flight at the same time:
        #   * the worker thread driving an experiment runner
        #   * the GUI thread responding to Close / scaling-combo /
        #     prefs-restore actions on the Connection panel
        # Without a lock, those two streams of DLL calls can land at
        # the same instant — the DLL has been observed corrupting its
        # heap (Windows status 0xC0000374 / -1073740940) when this
        # happens, killing the whole process with no Python traceback.
        # An RLock (re-entrant) lets a public method call another
        # public method on the same instance without deadlock —
        # several of them do, e.g. ``reinit`` → ``close`` → ``open``.
        self._dll_lock = threading.RLock()

    def _log(self, msg: str) -> None:
        """Forward to ``self.cmd_logger`` if one is set; silent otherwise."""
        cb = self.cmd_logger
        if cb is None:
            return
        try:
            cb(msg)
        except Exception:
            pass

    @property
    def is_open(self) -> bool:
        """True after a successful ``open()``; False before or after ``close()``."""
        return self.info.n_channels > 0

    # ----- internal helpers -----
    def _check(self, result: int, what: str,
               since: Optional[float] = None) -> None:
        """Raise ``RuntimeError`` if a PlexStim DLL call returned non-OK.

        Mirrors the MATLAB ``checkPlexStimError`` pattern: every set/load
        call passes through here so a failure can't pass silently and
        leave the device in a half-programmed state. The DLL's extended
        error info gives a human-readable reason which we surface in
        the message.

        Pass ``since=time.perf_counter()`` *before* the SDK call to have
        the log line include the elapsed duration in MATLAB-style
        auto-unit format (us / ms / s / min / h).
        """
        ts = (f"   ({_fmt_elapsed(time.perf_counter() - since)})"
              if since is not None else "")
        if result == self._PS_OK:
            self._log(f"[stim] {what}   OK{ts}")
            return
        try:
            info, _ = self._lib.ps_get_extended_error_info(result)
        except Exception:
            info = f"code={result}"
        self._log(f"[stim] {what}   FAILED: {info}{ts}")
        raise RuntimeError(f"PlexStim {what} failed: {info}")

    def _invoke(self, fn_name: str, *args, what: str) -> int:
        """Call ``self._lib.<fn_name>(*args)``, time it, log + check.

        Convenience wrapper for the common pattern of
        ``self._check(self._lib.fn(args), 'label', since=time.perf_counter())``.
        Returns the raw SDK result code (0 on OK) so callers that need
        the value (e.g. read-back queries) can still see it.
        """
        fn = getattr(self._lib, fn_name)
        t0 = time.perf_counter()
        result = fn(*args)
        self._check(result, what, since=t0)
        return result

    @staticmethod
    def _validate_channel(channel: int, n_channels: int) -> None:
        if not (1 <= channel <= n_channels):
            raise ValueError(
                f"Channel {channel} out of range — device has "
                f"{n_channels} channels (1-based).")

    def _extended_error_text(self, res) -> str:
        """Read PS_GetExtendedErrorInfo and decode the C string.

        The SDK returns the error string as ``bytes`` on most builds;
        decoding lets the caller log a human-readable message rather
        than ``b'No Plexon...'``. Errors during the readback itself
        fall back to the integer error code so we never lose the
        original failure context.
        """
        try:
            info, _ = self._lib.ps_get_extended_error_info(res)
        except Exception:
            return f"code={res}"
        if isinstance(info, (bytes, bytearray)):
            return info.decode(errors="replace").strip()
        return str(info).strip()

    @staticmethod
    def _error_means_locked(message: str) -> bool:
        """True if the SDK message points at the Sim-2 USB-lock case.

        Two variants seen in practice: ``"No Plexon Stimulator is
        detected."`` (most common) and a generic ``"not detected"``
        suffix on some firmware revisions. Match both.
        """
        m = message.lower()
        return ("no plexon stimulator" in m) or ("not detected" in m)

    def _auto_recover_locked_device(self) -> None:
        """Force-close the Plexon GUI (if running) and pause briefly.

        Imported lazily so a non-Windows environment, or one without
        the lock module, doesn't break the rest of the driver. Logs
        a brief notice to stderr so the user sees what happened — the
        upstream caller (GUI or CLI) doesn't get a structured signal
        otherwise. Failures here are swallowed: recovery is a
        best-effort step, and the caller will re-raise the original
        SDK error if the retry also fails.
        """
        try:
            from .plexstim_lock import close_blocking_processes
        except Exception:
            return
        try:
            closed = close_blocking_processes()
        except Exception:
            return
        if closed:
            import sys
            names = ", ".join(p.display() for p in closed)
            msg = (f"[plexon] SDK reported 'no stimulator detected'; "
                   f"force-closed Plexon GUI process(es) holding the USB "
                   f"lock: {names}. Retrying PS_InitAllStim...")
            # Route to the LogPane (and the .txt mirror) when a logger
            # is wired; fall back to stderr for CLI / pre-connect use.
            self._log(msg)
            if self.cmd_logger is None:
                print(msg, file=sys.stderr, flush=True)

    # ----- lifecycle -----
    @_dll_locked
    def open(self) -> None:
        # ---- Skip redundant ps_close_all_stim ---------------------
        # The vendor DLL has been observed to HEAP_CORRUPT (Windows
        # 0xC0000374) when ``ps_close_all_stim`` fires multiple times
        # within ~1 second across thread boundaries.  Worst-case
        # cascade we hit before adding the ``_is_open`` flag was 4
        # calls per GUI Stop/Start cycle:
        #
        #   1. GUI ``_on_finished`` close() — PS_CloseAllStim (#1)
        #   2. GUI ``_start_runner_body`` open() — PS_CloseAllStim
        #      embedded in this method (#2)
        #   3. Runner ``reinit()`` at top of run() — close() (#3)
        #   4. Runner ``reinit()`` — open() embedded close (#4)
        #
        # The 4th call crashed the DLL.  ``_is_open`` tracks
        # device-level state so we can skip ``ps_close_all_stim``
        # when the device is already closed (the prior close already
        # cleared it; the DLL has nothing to close again).
        #
        # The init below ALWAYS runs — it's the actual "open" work,
        # and it's idempotent on the device side (a fresh init
        # cleanly re-establishes the USB session even if a previous
        # init was active).
        if self._is_open:
            # Device thinks it's currently open.  This is a true
            # "reopen" — close first, then init.  Logs as one SDK
            # call rather than skipped.
            self._lib.ps_close_all_stim()
        # else: device is already closed (fresh __init__, or a prior
        # close() set the flag); skip the redundant close.
        self._is_open = False  # cleared during the init transition
        res = self._lib.ps_init_all_stim()
        if res != self._PS_OK:
            info_text = self._extended_error_text(res)
            # Plexon's own GUI ("Sim-2" / "Stimulator V2 Application")
            # holds an exclusive USB lock on the stimulator. While
            # that window is open, every PS_InitAllStim call from the
            # SDK comes back with "No Plexon Stimulator is detected."
            # even though the device is plugged in and powered. Try
            # to auto-recover: force-close the Plexon GUI (limited to
            # processes whose binary lives under a Plexon install
            # root) and retry the init exactly once. This mirrors what
            # the user would have to do manually via Task Manager.
            if self._error_means_locked(info_text):
                self._auto_recover_locked_device()
                self._lib.ps_close_all_stim()
                res = self._lib.ps_init_all_stim()
                if res != self._PS_OK:
                    info_text = self._extended_error_text(res)
            if res != self._PS_OK:
                hint = ""
                if self._error_means_locked(info_text):
                    hint = (" Hint: the Plexon Stim-2 / Stimulator V2 "
                            "application appears to still be holding "
                            "the USB lock. Close it manually via Task "
                            "Manager (or run "
                            "`python -m stimtest.hardware.plexstim_lock "
                            "--close`), then retry. Power-cycle the "
                            "stimulator if it's still not detected.")
                raise RuntimeError(f"PS_InitAllStim failed: {info_text}.{hint}")

        n_stim, _ = self._lib.ps_get_n_stim()
        if n_stim < self._stim_n:
            raise RuntimeError(f"Stimulator {self._stim_n} not present (found {n_stim})")

        n_ch, _ = self._lib.ps_get_n_channels(self._stim_n)
        serial, _ = self._lib.ps_get_serial_number(self._stim_n)
        fw, _ = self._lib.ps_get_fw_version(self._stim_n)
        desc, _ = self._lib.ps_get_description(self._stim_n)

        def _to_str(v):
            if isinstance(v, (bytes, bytearray)):
                return v.decode(errors="replace").strip("\x00").strip()
            return str(v)

        serial_str = _to_str(serial)
        desc_str = _to_str(desc)
        fw_str = _to_str(fw)

        is_nil = any(s in serial_str for s in NIL_SERIAL_NUMBERS)
        self.info = StimulatorInfo(
            serial_number=serial_str,
            firmware=fw_str,
            description=desc_str,
            n_channels=int(n_ch),
            vmon_scaling_v_per_v=VMON_SCALING_NIL if is_nil else VMON_SCALING_DEFAULT,
            imon_scaling_v_per_ua=IMON_SCALING_NIL if is_nil else IMON_SCALING_DEFAULT,
            is_simulated=False,
        )
        # PS_InitAllStim wipes any patterns the device had from a
        # previous session, so the loaded-channel set starts empty.
        self._loaded_channels = set()
        # Firmware state was just reset by PS_InitAllStim — every
        # channel must re-pass read-back validation before we can
        # trust it to skip the per-step verification round trips.
        self._validated_channels = set()
        # PS_InitAllStim leaves the device stopped — reset the
        # running-state flag so the first stop_all after a (re)open isn't
        # wrongly skipped/fired.
        self._is_running = False
        # PS_InitAllStim also resets the device-side period / repetitions,
        # so the value-keyed timing caches must be dropped — the next
        # load_channel on each channel re-programs from scratch.
        self._channel_period_ms = {}
        self._channel_reps = {}
        # …and clears every channel's loaded pattern, so the per-channel
        # content cache must be dropped too (else load_channel would skip
        # a genuinely-needed reload onto a freshly-wiped device).
        self._channel_content_sig = {}
        # Re-apply the user's auto-discharge preference. The SDK
        # resets this to its default (enabled) on PS_InitAllStim,
        # so a reinit between configs would silently re-enable it
        # if we didn't push the user's choice back here.
        if self._auto_discharge_pref is not None:
            try:
                self._lib.ps_set_auto_discharge(
                    self._stim_n, bool(self._auto_discharge_pref))
            except Exception:
                pass
        # Trigger mode — PULSAR controls start/stop PROGRAMMATICALLY via
        # PS_StartStimAllChannels / PS_StopStimAllChannels, both of which
        # REQUIRE PS_TRIG_SOFT (0) and return error 4 in any other mode.
        # PS_InitAllStim resets the trigger mode and the power-on default
        # is NOT guaranteed to be soft across firmware revisions, so we
        # (re)assert it on every open.  Symptom of a device left in
        # PS_TRIG_PULSE / PS_TRIG_LEVEL: the stim "starts" but waits for a
        # hardware trigger that never arrives, so it never actually
        # pulses (no output, no digital-sync edge → scope NUMACq stays 0).
        # Read the prior mode first so the session log records what the
        # device came up in (diagnostic for the "armed but not pulsing"
        # class of bug).
        from .pyplexstim.pyplexstimlib import PS_TRIG_SOFT
        try:
            _prior_mode, _gm_res = self._lib.ps_get_trigger_mode(self._stim_n)
            if _gm_res == self._PS_OK:
                _mode_name = {0: "SOFT", 1: "PULSE", 2: "LEVEL"}.get(
                    int(_prior_mode), "?")
                self._log(f"[stim] trigger mode at init = "
                          f"{int(_prior_mode)} ({_mode_name})")
        except Exception:
            pass
        try:
            self._invoke("ps_set_trigger_mode", self._stim_n, PS_TRIG_SOFT,
                         what="set_trigger_mode(PS_TRIG_SOFT=0)")
            _new_mode, _res = self._lib.ps_get_trigger_mode(self._stim_n)
            if _res == self._PS_OK and int(_new_mode) != PS_TRIG_SOFT:
                self._log(
                    f"[stim] ⚠ trigger mode read-back = {int(_new_mode)}, "
                    f"expected {PS_TRIG_SOFT} (SOFT) — programmatic "
                    f"start/stop may not work")
        except Exception:
            # _check already logged the FAILED line; the first start_all
            # will surface the hard error 4 if soft mode didn't take.
            # Don't abort the whole open over the trigger-mode set.
            pass
        # Device-level open state.  Set TRUE only after a successful
        # ps_init_all_stim + post-init reads succeed (we made it
        # past every raise above).  The matching FALSE assignments
        # live in :meth:`close` and at the top of :meth:`open` (the
        # init transition).  See ``_is_open`` docstring in __init__
        # for why this tracking matters (avoiding redundant
        # ps_close_all_stim cascades that HEAP_CORRUPT the DLL).
        self._is_open = True

    @_dll_locked
    def close(self) -> None:
        # Skip ps_close_all_stim when the device is already known-
        # closed.  Repeated close calls on an already-closed device
        # have been observed to HEAP_CORRUPT the vendor DLL — see
        # ``_is_open`` docstring in __init__ for the full failure
        # mode + call-cascade trace.  This guard is the device-level
        # half of the fix (the open()-side guard is in :meth:`open`).
        if self._is_open:
            try:
                self._lib.ps_close_all_stim()
            except Exception:
                pass
            self._is_open = False
        # PS_CloseAllStim drops device-side patterns; track that.
        # Done unconditionally (mirrors the always-clear behaviour of
        # the prior implementation, even when the skip-close branch
        # ran — defensive against a previous .load_channel that
        # somehow bypassed the flag).
        self._loaded_channels = set()
        self._validated_channels = set()
        self._is_running = False
        self._channel_period_ms = {}
        self._channel_reps = {}
        self._channel_content_sig = {}
        # Clean up the pinned .pat file so we don't leak temp files
        # across re-init cycles (and the next session starts fresh).
        if self._pat_path is not None:
            try:
                os.unlink(self._pat_path)
            except OSError:
                pass
            self._pat_path = None
        self._pat_content_signature = None

    # ----- programming -----
    @_dll_locked
    def load_channel(self, channel: int, pattern: PulsePattern) -> None:
        # ALL patterns are programmed via the arbitrary-waveform path so
        # the on-device behaviour is identical regardless of phase count
        # or symmetry. The PlexStim rectangular fast-path is rejected
        # by the firmware whenever phases are asymmetric, the discharge
        # delay needs to be observed before auto-discharge, or a
        # non-default ratio is in play — issues that bit us when we
        # tried to keep both code paths. Going straight to the .pat
        # file means one tested code path with no surprises.
        self._validate_channel(channel, self.info.n_channels or 16)
        # Catch out-of-range / sub-resolution parameters BEFORE they
        # reach the DLL — the firmware historically crashed when fed
        # negative widths or amplitudes past the ±1000 µA limit.
        pattern.validate()

        # PS_SetPeriod / PS_GetPeriod both operate in **milliseconds**
        # per the MATLAB SDK docs (Help/PS_SetPeriod.m: "Period - period
        # value in milliseconds, valid values are from 0.020 ms <= Period
        # <= 125,000 ms"). Earlier this was computed in microseconds —
        # off by 1000 — which made the device store a 20-second period
        # for a requested 50 Hz train. Use the float as-is so we keep
        # sub-ms precision for high-rate (>1 kHz) trains.
        #
        # Burst-aware: the DEVICE period is the BURST period in burst mode,
        # else the ordinary 1e6/rate.  ``device_period_us`` == 1e6/rate_hz
        # for a non-burst pattern, so this is byte-identical for every
        # existing (single-pulse) pattern; a burst programs the burst cycle
        # here and the arb pattern below carries the N intra-burst pulses.
        period_ms = pattern.device_period_us / 1000.0
        _ch = int(channel)
        _reps = int(pattern.repetitions)
        sig = self._content_signature(pattern)
        # ---- Per-channel content cache (operator: efficiency) ----------
        # The device retains each channel's loaded arbitrary pattern +
        # period + repetitions across stop/start cycles — only
        # open()/close()/reinit() (PS_InitAllStim) clears them.  So when
        # this channel ALREADY holds this exact (content, period, reps),
        # re-issuing ps_set_pattern_type / ps_load_arb_pattern /
        # ps_load_channel is pure redundant USB-TMC traffic (~4 round
        # trips, ~120 ms).  This is what collapses a VT sweep's reload of
        # the 15 static zero-amplitude unused channels EVERY amplitude
        # step down to once per configuration.  ``_loaded_channels`` gates
        # the skip so a channel that was never committed (or was wiped by
        # a reinit) still gets a full load.  A subsequent
        # commit_loaded_channels (PS_LoadAllChannels) re-arms the retained
        # pattern, so the unused channels keep ticking in cadence.
        if (_ch in self._loaded_channels
                and self._channel_content_sig.get(_ch) == sig
                and self._channel_period_ms.get(_ch) == period_ms
                and self._channel_reps.get(_ch) == _reps):
            return False   # cache hit — device already holds this pattern

        self._load_arbitrary(channel, pattern, content_signature=sig)

        # Value-keyed timing cache: re-program the rate only when it
        # differs from what this channel already holds (the device
        # persists PS_SetPeriod across .pat reloads + stop/start).  A
        # sweep that keeps the same rate every step thus programs it once
        # per channel; a genuine rate change re-programs.
        if self._channel_period_ms.get(_ch) != period_ms:
            self._invoke("ps_set_period", self._stim_n, channel, period_ms,
                         what=f"set_period(ch={channel}, period={period_ms:.3f} ms)")
            self._channel_period_ms[_ch] = period_ms
        if self._channel_reps.get(_ch) != _reps:
            self._invoke("ps_set_repetitions",
                         self._stim_n, channel, _reps,
                         what=f"set_repetitions(ch={channel}, n={_reps})")
            self._channel_reps[_ch] = _reps
        self._invoke("ps_load_channel", self._stim_n, channel,
                     what=f"load_channel(ch={channel})")

        # Read-back validation: the MATLAB code did this for every
        # critical setting. If the device silently rounds, ignores, or
        # overwrites a value (which has happened with .pat-load races
        # and with set_period during an active stim), we want to know
        # NOW, not after a captured trace looks wrong.
        #
        # **Hot-loop optimisation**: a sweep programs each channel
        # dozens of times in quick succession with the same firmware
        # path. The first programming verifies the path works; every
        # subsequent step exercising the same channel doesn't add new
        # failure modes, so we skip the 2 extra USB round trips after
        # the first success on each channel. ~80-120 ms saved on a
        # 50-step sweep × N channels. Reset by open()/close() since
        # PS_InitAllStim resets firmware state.
        #
        # Some firmware revisions return PS_GetPeriod in µs even though
        # the docs say ms — we accept either reading by checking the
        # ratio rather than the absolute value. Tolerance is 1% of the
        # requested period so high-rate trains where rounding inside
        # the device matters more get a tighter check naturally.
        if int(channel) not in self._validated_channels:
            got_period, res = self._lib.ps_get_period(self._stim_n, channel)
            self._check(res, f"get_period(ch={channel}) read-back")
            got = float(got_period)
            candidates = (period_ms, period_ms * 1000.0)  # accept ms or µs reading
            tolerance = max(period_ms * 0.01, 1e-3)
            if not any(abs(got - c) <= max(tolerance, c * 0.01) for c in candidates):
                raise RuntimeError(
                    f"PlexStim period mismatch on ch{channel}: requested "
                    f"{period_ms:.3f} ms, device reports {got} (neither "
                    f"{period_ms:.3f} ms nor {period_ms * 1000:.0f} µs).")
            got_reps, res = self._lib.ps_get_repetitions(self._stim_n, channel)
            self._check(res, f"get_repetitions(ch={channel}) read-back")
            if int(got_reps) != int(pattern.repetitions):
                raise RuntimeError(
                    f"PlexStim repetitions mismatch on ch{channel}: requested "
                    f"{pattern.repetitions}, device reports {got_reps}.")
            self._validated_channels.add(int(channel))
        # Track the loaded state so the runner can decide whether
        # the next configuration's return-channel set requires a
        # reinit. See Stimulator.loaded_channels for the rule.
        self._loaded_channels.add(_ch)
        # Record what this channel now holds on the device so a later
        # identical load_channel (same content + period + reps) can skip
        # the DLL round-trips above.  Set LAST, only after a fully
        # successful load + read-back, so a partial / failed load never
        # leaves a stale "already loaded" cache entry.
        self._channel_content_sig[_ch] = sig
        return True    # actually uploaded a (new/changed) pattern

    @staticmethod
    def _content_signature(pattern: PulsePattern) -> tuple:
        """Hashable per-channel signature of a pattern's .pat CONTENT.

        Covers ONLY what ends up in the .pat file (the phase geometry) —
        NOT rate / repetitions, which are programmed separately via
        PS_SetPeriod / PS_SetRepetitions and tracked by their own caches.
        Used by :meth:`load_channel` (skip an already-loaded channel) and
        :meth:`_load_arbitrary` (skip the shared-file rewrite).  Must stay
        in lock-step with the pair list ``build_pat_pairs`` renders.

        **CRITICAL — the amplitude is quantised to the DEVICE nA grid EXACTLY
        as ``build_pat_pairs`` does** (``int(round(amp_ua*1000/step))*step``,
        step = 100 nA rectangular / 30 nA shaped, ``waveforms.build_pat_pairs``
        line ~2424), NOT rounded to integer µA.  An integer-µA amplitude was a
        SAFETY bug: the per-channel content cache in :meth:`load_channel`
        short-circuits (``return False`` → ``ps_load_channel`` SKIPPED) on a
        signature match, so a fine VT/PS ramp step — the 0.1 µA testing
        resolution (`voltage_transient._snap_test_ua`) + the back-off/oscillate
        0.1 µA increments — from e.g. 50.0 → 50.4 µA COLLIDED under int-µA
        rounding (both → 50): the upload was skipped, the electrode kept
        delivering the OLD lower current, and the runner captured believing the
        NEW current was applied → read E_pol below the water-window band → and
        stepped the amplitude UP again, advancing PAST the true crossing (drive
        an electrode past its water window → physically ruined).  The nA-grid
        signature is identical iff the rendered .pat is identical, so the
        efficiency skip (gotcha #65) is preserved for a genuinely-unchanged
        pattern while every device-distinct amplitude forces a real reload.
        Tests: `tests/test_plexon_timing_cache.py`.
        """
        from ..config import (STIM_CURRENT_STEP_RECT_NA,
                              STIM_CURRENT_STEP_FINE_NA)
        from ..waveforms import SHAPE_RECTANGULAR

        def _amp_nA(ph) -> int:
            step = (STIM_CURRENT_STEP_RECT_NA if ph.shape == SHAPE_RECTANGULAR
                    else STIM_CURRENT_STEP_FINE_NA)
            return int(round(float(ph.amplitude_ua) * 1000.0 / step)) * step

        phase_sig = tuple(
            (_amp_nA(ph),
             round(ph.width_us, 3),
             round(ph.delay_after_us, 3),
             ph.shape,
             int(ph.bump_count),
             round(ph.tau_us, 3),
             round(getattr(ph, "tail_zero_us", 0.0), 3),
             round(getattr(ph, "offset_ua", 0.0), 3))
            for ph in pattern.phases
        )
        # Burst grouping CHANGES the .pat content (N pulses + intra-burst
        # gaps), and the intra-burst gap derives from rate_hz — so a burst
        # signature must fold in both the pulse count and the gap, else a
        # burst-parameter change would be missed by the content cache and
        # the device would keep delivering the stale burst.  A non-burst
        # pattern gets a constant ``(1,)`` marker, so every existing
        # single-pulse signature is unchanged relative to itself.
        if getattr(pattern, "pulses_per_burst", 1) > 1:
            burst_sig = (int(pattern.pulses_per_burst),
                         round(pattern.intra_burst_gap_us, 3))
        else:
            burst_sig = (1,)
        return (burst_sig,) + phase_sig

    def loaded_channels(self) -> set:
        """Channels with a pattern currently loaded (PlexStim-side)."""
        return set(self._loaded_channels)

    def _load_arbitrary(self, channel: int, pattern: PulsePattern,
                        content_signature: tuple | None = None) -> None:
        """Write the .pat file for ``pattern`` and load it onto ``channel``.

        The path is pinned per stimulator instance — created on the
        first call and overwritten in place on every subsequent call.
        Long ramp sweeps used to churn through one
        ``tempfile.NamedTemporaryFile(...) → os.unlink`` cycle per
        step (~50 disk syscalls for a 50-step VT sweep × N
        channels); reusing the file cuts that to a single create
        plus N rewrites of the same path. ``close()`` removes the
        file when the device is shut down.
        """
        from .pyplexstim.pyplexstimlib import PS_PATTERN_ARB
        import tempfile

        # PlexStim .pat: list of (amp_nA, duration_µs) pairs. The DLL
        # holds each amp for its duration (sample-and-hold staircase),
        # so any waveform — rectangular, ramp, sine, halfpipe, bowtie,
        # speedbumps — reduces to a sufficiently-fine pair sequence.
        # ``build_pat_pairs`` does the rendering AND validates the
        # output against the PlexStim 2.0 SDK constraints (≤ 499 pairs,
        # every duration ≥ 1 µs, every amplitude in the documented
        # int32 nA range) so a bad pattern raises here rather than
        # silently truncating on the device.
        from ..waveforms import build_burst_pat_pairs, format_pat_lines

        # Signature covers ONLY the .pat content (phase tuple) — rate
        # and repetitions are programmed through PS_SetPeriod /
        # PS_SetRepetitions, NOT embedded in the .pat file, so a rate
        # change alone shouldn't force a rewrite + fsync. Previously
        # rate/reps were folded into the signature and every sweep
        # step that touched the rate burned ~5-50 ms re-flushing
        # bytes the DLL already had.  The caller (load_channel) usually
        # passes the precomputed signature so it isn't built twice.
        if content_signature is None:
            content_signature = self._content_signature(pattern)
        # Lazy-allocate the pinned path on first use. ``delete=False``
        # because we want it to outlive the with-block; unlinked in
        # ``close()`` instead.
        if (self._pat_path is None
                or content_signature != self._pat_content_signature):
            # Documented PlexStim variable format (per the user
            # guide, §8.5.2): first line "Variable", then alternating
            # amp (nA) / duration (µs) lines. ``build_pat_pairs``
            # constructs the pair list; ``format_pat_lines`` flattens
            # it to the line-oriented form the DLL expects.
            # Burst-aware: build_burst_pat_pairs tiles the pulse N times
            # (intra-burst gaps between) for a burst, and is exactly
            # build_pat_pairs for a single-pulse pattern.
            pairs = build_burst_pat_pairs(pattern)
            lines = format_pat_lines(pairs)
            content = "\n".join(lines) + "\n"
            # Flush + fsync after every write so the bytes are
            # guaranteed on disk before ``ps_load_arb_pattern`` runs.
            # Without it, on slow / network-mounted backing stores
            # the DLL can race ahead and read a truncated file —
            # silently programming a partial waveform that the
            # subsequent ``get_pattern_type`` read-back wouldn't
            # catch (it only checks pattern KIND, not contents).
            if self._pat_path is None:
                with tempfile.NamedTemporaryFile(
                    suffix=".pat", delete=False, mode="w") as fh:
                    fh.write(content)
                    fh.flush()
                    os.fsync(fh.fileno())
                    self._pat_path = fh.name
            else:
                # Overwrite in place. The PlexStim DLL re-reads the
                # file on every ``ps_load_arb_pattern`` call so a fresh
                # write here is always picked up.
                with open(self._pat_path, "w") as fh:
                    fh.write(content)
                    fh.flush()
                    os.fsync(fh.fileno())
            self._pat_content_signature = content_signature
        # else: file content already matches this pattern — skip the
        # rewrite and go straight to the DLL load.

        self._invoke("ps_set_pattern_type",
                     self._stim_n, channel, PS_PATTERN_ARB,
                     what=f"set_pattern_type(ch={channel}, ARB)")
        self._invoke("ps_load_arb_pattern",
                     self._stim_n, channel, self._pat_path,
                     what=f"load_arb_pattern(ch={channel}, "
                          f"file={self._pat_path})")
        # Read-back: confirm the device is actually in arbitrary mode.
        got_type, res = self._lib.ps_get_pattern_type(self._stim_n, channel)
        self._check(res, f"get_pattern_type(ch={channel}) read-back")
        if int(got_type) != PS_PATTERN_ARB:
            raise RuntimeError(
                f"PlexStim ch{channel}: requested ARB pattern but device "
                f"reports type {got_type}.")

    @_dll_locked
    def set_monitor_channel(self, channel: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        self._invoke("ps_set_monitor_channel", self._stim_n, channel,
                     what=f"set_monitor_channel(ch={channel})")
        got, res = self._lib.ps_get_monitor_channel(self._stim_n)
        self._check(res, "get_monitor_channel read-back")
        if int(got) != int(channel):
            raise RuntimeError(
                f"PlexStim monitor-channel mismatch: requested {channel}, "
                f"device reports {got}.")

    @_dll_locked
    def start_channel(self, channel: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        self._invoke("ps_start_stim_channel", self._stim_n, channel,
                     what=f"start_stim_channel(ch={channel})")
        self._is_running = True

    @_dll_locked
    def stop_channel(self, channel: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        self._invoke("ps_stop_stim_channel", self._stim_n, channel,
                     what=f"stop_stim_channel(ch={channel})")

    @_dll_locked
    def stop_all(self, *, force: bool = False) -> None:
        # Idempotent: skip the redundant PS_StopStimAllChannels when we
        # already know the device is stopped (see _is_running).  The sweep
        # issues TWO stops between steps — the per-capture ``finally`` stop
        # + the next step's explicit pre-load stop (gotcha #15) — and on
        # the normal path the second is a no-op.  This collapses it to ONE
        # DLL round-trip while the explicit stop STILL fires if the finally
        # was skipped (running would still be True then), so the
        # belt-and-suspenders safety is preserved.
        #
        # ``force=True`` BYPASSES that idempotence guard — for an operator
        # Stop / Pause the cost of one redundant DLL call is nothing, while
        # the cost of skipping a real stop because the flag went stale is an
        # electrode that keeps pulsing after the user pressed Stop.  The
        # optimization is for the sweep's internal double-stop only.
        if not self._is_running and not force:
            return
        # ESCALATE rather than raise.  PS_StopStimAllChannels is documented to
        # return 4 ("wrong trigger mode — not PS_TRIG_SOFT") and to let an
        # in-flight pulse / arbitrary waveform COMPLETE; PS_AbortAll carries
        # neither restriction ("cease immediately even if there is a pulse or
        # arbitrary waveform in progress").  ``_invoke`` RAISES on a non-OK
        # code, and every caller wraps the stop in try/except — so a refused
        # stop would be swallowed and the electrode would keep pulsing with
        # nothing shown to the operator.  A stop that cannot stop must fall
        # through to the abort, not disappear.
        try:
            self._invoke("ps_stop_stim_all_channels", self._stim_n,
                         what="stop_stim_all_channels")
        except Exception as exc:
            self._log(f"stop_stim_all_channels FAILED ({exc}) — escalating "
                      f"to PS_AbortAll")
            try:
                self._invoke("ps_abort_all", what="abort_all(escalated)")
            finally:
                self._is_running = False
            return
        self._is_running = False

    @_dll_locked
    def abort_all(self) -> None:
        """Emergency cease-all-stim — wraps ``PS_AbortAll``.

        Per the PlexStim API manual: "Causes all stimulation to
        cease immediately even if there is a pulse or arbitrary
        waveform in progress."  Unlike ``ps_stop_stim_all_channels``
        this has no trigger-mode requirement (the soft-stop returns
        error 4 outside ``PS_TRIG_SOFT``), and it interrupts a pulse
        in flight rather than letting it complete.
        """
        self._invoke("ps_abort_all", what="abort_all")
        # Abort halts everything → device is now stopped.  Always fires
        # (emergency cease), but keeps _is_running coherent so a follow-up
        # stop_all collapses to a no-op.
        self._is_running = False

    @_dll_locked
    def set_trigger_mode(self, mode: int) -> None:
        """Set the device trigger mode (0=SOFT, 1=PULSE, 2=LEVEL).

        PULSAR starts and stops stimulation PROGRAMMATICALLY, which the
        SDK only permits in ``PS_TRIG_SOFT`` (0): both
        ``PS_StartStimAllChannels`` and ``PS_StopStimAllChannels`` return
        error 4 ("wrong trigger mode") in any other mode.  :meth:`open`
        asserts SOFT on every connect (``PS_InitAllStim`` resets it and
        the power-on default isn't guaranteed soft across firmware
        revisions); this method exists for explicit callers and tests.
        """
        self._invoke("ps_set_trigger_mode", self._stim_n, int(mode),
                     what=f"set_trigger_mode({int(mode)})")

    @_dll_locked
    def load_all_channels(self) -> None:
        """Commit every channel's staged parameters in one call
        (``PS_LoadAllChannels``).

        Mirrors the MONOPOLAR commit in the MATLAB original
        (``loadPattern.m``: ``isempty(channelReturn_arr) ->
        PS_LoadAllChannels``).  Used by
        :meth:`ExperimentRunner.commit_loaded_channels` for configs with
        NO return channels; multipolar configs commit per-channel via
        :meth:`load_channel` instead so the return channels stay
        unloaded (passive sinks).
        """
        self._invoke("ps_load_all_channels", self._stim_n,
                     what="load_all_channels")

    @_dll_locked
    def start_all(self) -> None:
        """Synchronously start every loaded channel.

        Wraps ``PS_StartStimAllChannels``: a single SDK / USB round
        trip that fires every channel on the same firmware tick. The
        per-channel ``start_channel`` loop staggers starts by tens to
        hundreds of microseconds (one round-trip each), which makes
        the device's digital sync output fire 16 times per pulse
        cycle when all channels are programmed — chaotic on the scope.
        Use this whenever multiple channels share a pattern and the
        downstream consumer (scope trigger, TTL gating) expects one
        edge per cycle.
        """
        self._invoke("ps_start_stim_all_channels", self._stim_n,
                     what="start_stim_all_channels")
        self._is_running = True

    @_dll_locked
    def set_repetitions(self, channel: int, n: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        if n < 0:
            raise ValueError(f"repetitions must be ≥ 0 (0 = infinite), got {n}.")
        _ch = int(channel)
        # Value-keyed cache (see __init__): skip when the channel already
        # holds this repetition count.  The sweep runners call this with
        # n=0 ("infinite") right after load_channel — which already
        # programmed reps=0 from the pattern (default repetitions=0) — so
        # the redundant re-set collapses to a no-op rather than a USB
        # round-trip per step.
        if self._channel_reps.get(_ch) == int(n):
            return
        self._invoke("ps_set_repetitions", self._stim_n, channel, int(n),
                     what=f"set_repetitions(ch={channel}, n={n})")
        # Read-back confirm (operator: "Do the same checks with the
        # PlexStim … getting what is set to confirm").  The standalone
        # setter now mirrors ``load_channel``'s reps validation — a silent
        # mismatch means the wrong number of pulses per burst, so surface
        # it NOW rather than after a capture looks wrong.  Fires only on a
        # GENUINE change (the value cache above collapses the sweep's
        # no-op re-sets), so the extra USB round-trip is rare.  Confirm
        # BEFORE caching so a failed read-back never leaves a wrong
        # "already set" entry.
        got_reps, res = self._lib.ps_get_repetitions(self._stim_n, channel)
        self._check(res, f"get_repetitions(ch={channel}) read-back")
        if int(got_reps) != int(n):
            raise RuntimeError(
                f"PlexStim repetitions mismatch on ch{channel}: requested "
                f"{n}, device reports {got_reps}.")
        self._channel_reps[_ch] = int(n)

    # ----- auto-discharge -----
    @_dll_locked
    def set_auto_discharge(self, enabled: bool) -> None:
        """Push the user's auto-discharge preference to the device.

        Cached on the instance so a later ``open() / reinit()`` (which
        wraps PS_InitAllStim and resets the device-side flag) can
        re-apply it transparently.
        """
        self._auto_discharge_pref = bool(enabled)
        self._invoke("ps_set_auto_discharge", self._stim_n, bool(enabled),
                     what=f"set_auto_discharge(enabled={enabled})")
        # Read-back confirm (operator: "Do the same checks with the
        # PlexStim … getting what is set to confirm").  Non-fatal ⚠ log
        # (like the trigger-mode read-back in open()) — auto-discharge is a
        # preference, not a per-capture safety value, so a mismatch is
        # surfaced in the session log without aborting.
        try:
            got, res = self._lib.ps_get_auto_discharge(self._stim_n)
            if res == self._PS_OK and bool(got) != bool(enabled):
                self._log(
                    f"[stim] ⚠ auto-discharge read-back = {bool(got)}, "
                    f"expected {bool(enabled)}")
        except Exception:
            pass

    @_dll_locked
    def get_auto_discharge(self) -> Optional[bool]:
        try:
            val, res = self._lib.ps_get_auto_discharge(self._stim_n)
            if res != self._PS_OK:
                return None
            return bool(val)
        except Exception:
            return None
