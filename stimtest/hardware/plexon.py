"""Real Plexon PlexStim 2.0 driver.

Wraps the vendored ``pyplexstim`` library so the rest of the codebase only
sees the abstract :class:`stimtest.hardware.base.Stimulator` interface.

This module imports the DLL lazily so the rest of the project can still be
imported on systems where the Plexon SDK isn't installed (notably for unit
tests, CI, and offline GUI development).
"""
from __future__ import annotations

import os
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
        # User's auto-discharge preference, applied on every open() so
        # the setting survives reinit cycles. ``None`` means "leave at
        # the SDK default (enabled)" — the panel hasn't pushed an
        # explicit preference yet.
        self._auto_discharge_pref: Optional[bool] = None

    # ----- internal helpers -----
    def _check(self, result: int, what: str) -> None:
        """Raise ``RuntimeError`` if a PlexStim DLL call returned non-OK.

        Mirrors the MATLAB ``checkPlexStimError`` pattern: every set/load
        call passes through here so a failure can't pass silently and
        leave the device in a half-programmed state. The DLL's extended
        error info gives a human-readable reason which we surface in
        the message.
        """
        if result == self._PS_OK:
            return
        try:
            info, _ = self._lib.ps_get_extended_error_info(result)
        except Exception:
            info = f"code={result}"
        raise RuntimeError(f"PlexStim {what} failed: {info}")

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
            print(
                f"[plexon] SDK reported 'no stimulator detected'; "
                f"force-closed Plexon GUI process(es) holding the USB "
                f"lock: {names}. Retrying PS_InitAllStim...",
                file=sys.stderr, flush=True,
            )

    # ----- lifecycle -----
    def open(self) -> None:
        self._lib.ps_close_all_stim()
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
                    hint = (" Hint: the Plexon Sim-2 / Stimulator V2 "
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

        is_nil = any(s in str(serial) for s in NIL_SERIAL_NUMBERS)
        self.info = StimulatorInfo(
            serial_number=str(serial),
            firmware=str(fw),
            description=str(desc),
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

    def close(self) -> None:
        try:
            self._lib.ps_close_all_stim()
        except Exception:
            pass
        # PS_CloseAllStim drops device-side patterns; track that.
        self._loaded_channels = set()
        self._validated_channels = set()
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

        self._load_arbitrary(channel, pattern)

        # PS_SetPeriod / PS_GetPeriod both operate in **milliseconds**
        # per the MATLAB SDK docs (Help/PS_SetPeriod.m: "Period - period
        # value in milliseconds, valid values are from 0.020 ms <= Period
        # <= 125,000 ms"). Earlier this was computed in microseconds —
        # off by 1000 — which made the device store a 20-second period
        # for a requested 50 Hz train. Use the float as-is so we keep
        # sub-ms precision for high-rate (>1 kHz) trains.
        period_ms = 1e3 / pattern.rate_hz
        self._check(
            self._lib.ps_set_period(self._stim_n, channel, period_ms),
            f"set_period(ch={channel}, period={period_ms:.3f} ms)")
        self._check(
            self._lib.ps_set_repetitions(self._stim_n, channel, int(pattern.repetitions)),
            f"set_repetitions(ch={channel}, n={pattern.repetitions})")
        self._check(
            self._lib.ps_load_channel(self._stim_n, channel),
            f"load_channel(ch={channel})")

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
        self._loaded_channels.add(int(channel))

    def loaded_channels(self) -> set:
        """Channels with a pattern currently loaded (PlexStim-side)."""
        return set(self._loaded_channels)

    def _load_arbitrary(self, channel: int, pattern: PulsePattern) -> None:
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
        from ..waveforms import build_pat_pairs, format_pat_lines

        # Signature covers ONLY the .pat content (phase tuple) — rate
        # and repetitions are programmed through PS_SetPeriod /
        # PS_SetRepetitions, NOT embedded in the .pat file, so a rate
        # change alone shouldn't force a rewrite + fsync. Previously
        # rate/reps were folded into the signature and every sweep
        # step that touched the rate burned ~5-50 ms re-flushing
        # bytes the DLL already had.
        content_signature = tuple(
            (int(round(ph.amplitude_ua)),
             round(ph.width_us, 3),
             round(ph.delay_after_us, 3),
             ph.shape,
             int(ph.bump_count),
             round(ph.tau_us, 3),
             round(getattr(ph, "tail_zero_us", 0.0), 3),
             round(getattr(ph, "offset_ua", 0.0), 3))
            for ph in pattern.phases
        )
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
            pairs = build_pat_pairs(pattern)
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

        self._check(
            self._lib.ps_set_pattern_type(self._stim_n, channel, PS_PATTERN_ARB),
            f"set_pattern_type(ch={channel}, ARB)")
        self._check(
            self._lib.ps_load_arb_pattern(self._stim_n, channel, self._pat_path),
            f"load_arb_pattern(ch={channel}, file={self._pat_path})")
        # Read-back: confirm the device is actually in arbitrary mode.
        got_type, res = self._lib.ps_get_pattern_type(self._stim_n, channel)
        self._check(res, f"get_pattern_type(ch={channel}) read-back")
        if int(got_type) != PS_PATTERN_ARB:
            raise RuntimeError(
                f"PlexStim ch{channel}: requested ARB pattern but device "
                f"reports type {got_type}.")

    def set_monitor_channel(self, channel: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        self._check(
            self._lib.ps_set_monitor_channel(self._stim_n, channel),
            f"set_monitor_channel(ch={channel})")
        got, res = self._lib.ps_get_monitor_channel(self._stim_n)
        self._check(res, "get_monitor_channel read-back")
        if int(got) != int(channel):
            raise RuntimeError(
                f"PlexStim monitor-channel mismatch: requested {channel}, "
                f"device reports {got}.")

    def start_channel(self, channel: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        self._check(
            self._lib.ps_start_stim_channel(self._stim_n, channel),
            f"start_stim_channel(ch={channel})")

    def stop_channel(self, channel: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        self._check(
            self._lib.ps_stop_stim_channel(self._stim_n, channel),
            f"stop_stim_channel(ch={channel})")

    def stop_all(self) -> None:
        self._check(
            self._lib.ps_stop_stim_all_channels(self._stim_n),
            "stop_stim_all_channels")

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
        self._check(
            self._lib.ps_start_stim_all_channels(self._stim_n),
            "start_stim_all_channels")

    def set_repetitions(self, channel: int, n: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        if n < 0:
            raise ValueError(f"repetitions must be ≥ 0 (0 = infinite), got {n}.")
        self._check(
            self._lib.ps_set_repetitions(self._stim_n, channel, int(n)),
            f"set_repetitions(ch={channel}, n={n})")

    # ----- auto-discharge -----
    def set_auto_discharge(self, enabled: bool) -> None:
        """Push the user's auto-discharge preference to the device.

        Cached on the instance so a later ``open() / reinit()`` (which
        wraps PS_InitAllStim and resets the device-side flag) can
        re-apply it transparently.
        """
        self._auto_discharge_pref = bool(enabled)
        self._check(
            self._lib.ps_set_auto_discharge(self._stim_n,
                                            bool(enabled)),
            f"set_auto_discharge(enabled={enabled})")

    def get_auto_discharge(self) -> Optional[bool]:
        try:
            val, res = self._lib.ps_get_auto_discharge(self._stim_n)
            if res != self._PS_OK:
                return None
            return bool(val)
        except Exception:
            return None
