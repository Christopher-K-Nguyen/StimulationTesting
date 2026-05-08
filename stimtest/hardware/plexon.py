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
        # Cache of the most-recent .pat file *content* keyed by the
        # pattern's phase tuple. When a fixed-amp pulsing loop
        # reloads the same pattern back-to-back we skip the disk
        # rewrite entirely — the file already has the right bytes.
        self._pat_signature: Optional[tuple] = None

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

    # ----- lifecycle -----
    def open(self) -> None:
        self._lib.ps_close_all_stim()
        res = self._lib.ps_init_all_stim()
        if res != self._PS_OK:
            info, _ = self._lib.ps_get_extended_error_info(res)
            # Decode the C string that the SDK returns so the message
            # is readable, not bytes-prefixed garbage.
            info_text = (info.decode(errors="replace")
                         if isinstance(info, (bytes, bytearray)) else str(info))
            # Plexon's own GUI ("Sim-2" / "Stimulator V2 Application")
            # holds an exclusive USB lock on the stimulator. While that
            # window is open, every PS_InitAllStim call from the SDK
            # comes back with "No Plexon Stimulator is detected." even
            # though the device is plugged in and powered. Surface the
            # most common fix in the error itself so users don't have
            # to guess — or grep through the codebase — when the
            # connection fails.
            hint = ""
            if "no plexon stimulator" in info_text.lower() or "not detected" in info_text.lower():
                hint = (" Hint: close the Plexon Sim-2 / Stimulator V2 "
                        "application if it's open — it holds an "
                        "exclusive USB lock on the device. Power-cycle "
                        "the stimulator if the SDK still can't see it.")
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

    def close(self) -> None:
        try:
            self._lib.ps_close_all_stim()
        except Exception:
            pass
        # Clean up the pinned .pat file so we don't leak temp files
        # across re-init cycles (and the next session starts fresh).
        if self._pat_path is not None:
            try:
                os.unlink(self._pat_path)
            except OSError:
                pass
            self._pat_path = None
        self._pat_signature = None

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
        # Some firmware revisions return PS_GetPeriod in µs even though
        # the docs say ms — we accept either reading by checking the
        # ratio rather than the absolute value. Tolerance is 1% of the
        # requested period so high-rate trains where rounding inside
        # the device matters more get a tighter check naturally.
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

        # PlexStim .pat: list of (time_us, amplitude_µA) breakpoints.
        # The signature short-circuits identical reloads but MUST cover
        # every parameter that affects the device's runtime behaviour
        # — not just the file-on-disk shape. Two patterns with
        # identical phases but different ``rate_hz`` or
        # ``repetitions`` need separate cache entries: the file
        # content matches, but the period/repetitions DLL calls below
        # apply different settings, so reusing the cache without
        # bumping it would leave the device in a state that doesn't
        # match the runner's intent.
        signature = (
            tuple(
                (int(round(ph.amplitude_ua)),
                 round(ph.width_us, 3),
                 round(ph.delay_after_us, 3))
                for ph in pattern.phases
            ),
            round(float(pattern.rate_hz), 6),
            int(pattern.repetitions),
        )
        # Lazy-allocate the pinned path on first use. ``delete=False``
        # because we want it to outlive the with-block; unlinked in
        # ``close()`` instead.
        if self._pat_path is None or signature != self._pat_signature:
            lines = ["0,0"]
            t = 0.0
            for ph in pattern.phases:
                t_start = t
                t_end = t + ph.width_us
                lines.append(f"{t_start:.0f},{int(round(ph.amplitude_ua))}")
                lines.append(f"{t_end:.0f},{int(round(ph.amplitude_ua))}")
                t = t_end + ph.delay_after_us
                lines.append(f"{t:.0f},0")
            content = "\n".join(lines)
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
            self._pat_signature = signature
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

    def set_repetitions(self, channel: int, n: int) -> None:
        self._validate_channel(channel, self.info.n_channels or 16)
        if n < 0:
            raise ValueError(f"repetitions must be ≥ 0 (0 = infinite), got {n}.")
        self._check(
            self._lib.ps_set_repetitions(self._stim_n, channel, int(n)),
            f"set_repetitions(ch={channel}, n={n})")
