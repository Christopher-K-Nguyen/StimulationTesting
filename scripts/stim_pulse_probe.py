#!/usr/bin/env python
"""Standalone hardware probe — does the real PlexStim actually pulse?

Drives the real Plexon PlexStim + Tektronix scope with the EXACT
parameters from the failing ``session_001`` run:

    monopolar, active = CH1, no returns
    biphasic cathodic-first, 50 µA, 200 µs / 200 µs phases,
    20 µs interphase, 20 µs discharge, 50 Hz, repetitions = 0 (continuous)

It exercises the SAME sequence the VT runner now uses —
``set_monitor_channel`` → per-channel ``load_channel`` (active + zero on
unused) → ``PS_LoadAllChannels`` (the monopolar-commit fix) →
``PS_StartStimAllChannels`` — then captures one averaged frame and
reports the I_mon swing.  This isolates the stim+scope path from the
full GUI so the ``PS_LoadAllChannels`` fix can be verified directly.

RUN WITH PULSAR CLOSED — the PlexStim USB lock is exclusive.

    python scripts/stim_pulse_probe.py
"""
from __future__ import annotations

import sys

import numpy as np

# Windows consoles default to cp1252, which can't encode the ✓/✗/µ
# glyphs below — force UTF-8 so the verdict line never raises.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from stimtest.experiments.base import imon_trigger_level
from stimtest.hardware.plexon import PlexonStimulator
from stimtest.hardware.tektronix import TektronixOscilloscope
from stimtest.waveforms import PulsePattern

ACTIVE = 1
ALIASES = {"vmon": "CH1", "imon": "CH2", "eret": "CH3", "trigger": "CH4"}
IMON_VERDICT_MV = 20.0  # pk-pk above this on I_mon ⇒ real current


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    pattern = PulsePattern.rect(
        polarity=-1, amplitude_ua=50.0, phase_width_us=200.0,
        interphase_us=20.0, discharge_us=20.0, rate_hz=50.0)
    log("Pattern: biphasic cathodic-first 50 µA, 200/200 µs, 20 µs "
        "interphase, 20 µs discharge, 50 Hz (monopolar, active CH1)")

    stim = PlexonStimulator()
    scope = TektronixOscilloscope()
    stim.cmd_logger = log
    scope.cmd_logger = log

    try:
        log("\n--- Opening stimulator ---")
        stim.open()
        log(f"  {stim.info.description}  S/N {stim.info.serial_number}  "
            f"({stim.info.n_channels} ch)")

        log("\n--- Opening scope ---")
        scope.open()
        scope.channel_aliases = dict(ALIASES)

        log("\n--- Configuring scope (record-length realloc can take "
            "10-30 s on TBS2204B) ---")
        scope.configure_channels(ALIASES)
        scope.set_record_length(20000)
        scope.set_acquisition_mode("AVERAGE", n_avg=16)
        # Trigger on the I_mon current monitor (CH2) in AUTO mode — NOT the
        # CH4 digital sync.  Per the MATLAB reference the Plexon sync drives
        # the scope EXT BNC, never a vertical channel, so CH4 likely carried
        # nothing → NUMACq=0 → stale frame (the trap the earlier run fell
        # into).  Triggering on I_mon with AUTO force-fires a frame
        # regardless: a non-zero I_mon swing PROVES current; a flat trace
        # with NUMACq incrementing proves the stim genuinely isn't pulsing.
        amp_signed = -50.0   # cathodic-first 50 µA (matches the pattern)
        pw0_us = 200.0
        trig_level = imon_trigger_level(
            amp_signed, pw0_us,
            imon_v_per_ua=stim.info.imon_scaling_v_per_ua)
        trig_slope = "FALL" if amp_signed < 0 else "RISE"
        log(f"  I_mon trigger: CH2, {trig_level*1e3:+.1f} mV, {trig_slope}, AUTO")
        scope.set_trigger(source="CH2", level_v=trig_level, slope=trig_slope,
                          mode="AUTO", digital=False)

        log("\n--- Load (monopolar) + PS_LoadAllChannels commit + start ---")
        try:
            stim.stop_all()
        except Exception:
            pass
        stim.set_monitor_channel(ACTIVE)
        stim.load_channel(ACTIVE, pattern)
        zero = pattern.scaled(0.0)
        n_ch = int(stim.info.n_channels or 16)
        for ch in range(1, n_ch + 1):
            if ch == ACTIVE:
                continue
            try:
                stim.load_channel(ch, zero)
                stim.set_repetitions(ch, 0)
            except Exception as e:
                log(f"  unused ch{ch} load failed: {e}")
        log(">>> PS_LoadAllChannels (monopolar commit — THE FIX) <<<")
        stim.load_all_channels()
        log(">>> PS_StartStimAllChannels <<<")
        stim.start_all()

        log("\n--- Capturing one averaged frame ---")
        acq = scope.single_capture(timeout_s=15.0)

        log("\n--- Per-channel captured swing ---")
        for name, arr in sorted(acq.channels.items()):
            a = np.asarray(arr, dtype=float)
            if a.size:
                log(f"  {name}: min={a.min()*1e3:+.2f} mV  "
                    f"max={a.max()*1e3:+.2f} mV  "
                    f"pk-pk={(a.max()-a.min())*1e3:.2f} mV")

        imon = np.asarray(acq.channels.get("CH2", []), dtype=float)
        log("")
        if imon.size:
            ppk = float(imon.max() - imon.min()) * 1e3
            if ppk > IMON_VERDICT_MV:
                log(f"✓ I_mon shows {ppk:.1f} mV pk-pk — STIMULATOR IS "
                    f"PULSING. The earlier 'flat' reading was just an "
                    f"untriggered CH4 frame; the I_mon trigger reveals it.")
            else:
                log(f"✗ I_mon flat ({ppk:.1f} mV pk-pk) on an AUTO-FORCED "
                    f"capture — this DOES disambiguate: the stimulator is "
                    f"genuinely not outputting current (device/bench side).")
        else:
            log("✗ No I_mon (CH2) data captured.")
        return 0
    finally:
        log("\n--- Stopping + closing ---")
        try:
            stim.stop_all()
        except Exception:
            pass
        try:
            stim.close()
        except Exception:
            pass
        try:
            scope.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
