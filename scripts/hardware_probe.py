#!/usr/bin/env python
"""Quick check that real hardware is reachable end-to-end.

Run from the repo root with the project installed (``pip install -e .``):

    python scripts/hardware_probe.py

What it does (in order, each step independent so an early failure
doesn't mask later ones):

1. **PlexStim SDK detection** — registry + filesystem walk via
   ``stimtest.hardware.plexstim_detect.detect_plexstim``. Reports the
   install path, DLL path, version, and whether ctypes can resolve
   the binary.
2. **Stimulator open** — calls ``open_stimulator(simulate=False)``,
   reports the firmware version + channel count, then closes.
3. **VISA backend enumeration** — lists every USB-TMC-ish resource
   pyvisa can see, so we know the scope is actually on the bus.
4. **Tektronix scope open** — auto-detects the first Tek match, reads
   ``*IDN?``, prints make/model/firmware, then disconnects.

A non-zero exit code means at least one step failed; the printed
diagnostics tell you which.

This script never sends a stim pulse or arms the scope — it's
read-only on the hardware side. Safe to run on a benchtop with the
electrode in saline, in air, or with the channel disconnected.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

# Allow `python scripts/hardware_probe.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Tiny output helpers — keeps the report readable on a Windows console
# (no Unicode box-drawing, no colors).
# ---------------------------------------------------------------------------
def _hr(label: str) -> None:
    print()
    print("=" * 60)
    print(f"  {label}")
    print("=" * 60)


def _ok(msg: str) -> None:
    print(f"  [OK]    {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL]  {msg}")


def _info(msg: str) -> None:
    print(f"          {msg}")


# ---------------------------------------------------------------------------
# Step 1 — PlexStim SDK detection
# ---------------------------------------------------------------------------
def step_detect_sdk() -> bool:
    _hr("1. Plexon PlexStim 2.0 SDK detection")
    try:
        from stimtest.hardware.plexstim_detect import detect_plexstim
    except Exception as e:
        _fail(f"could not import plexstim_detect: {e}")
        return False

    s = detect_plexstim()
    if s.installed:
        _ok(f"SDK installed via {s.detection_source}")
    else:
        _fail("SDK install not detected")
    if s.install_path:
        _info(f"install_path: {s.install_path}")
    if s.version:
        _info(f"version:      {s.version}")
    if s.dll_path:
        _info(f"dll_path:     {s.dll_path}")
        _info(f"dll_loadable: {s.dll_loadable}")
    elif s.installed:
        _info("(no DLL located inside install path)")
    for note in s.notes:
        _info(f"note: {note}")
    return s.installed and s.dll_loadable


# ---------------------------------------------------------------------------
# Step 2 — Open the stimulator and read identity
# ---------------------------------------------------------------------------
def step_open_stim() -> bool:
    _hr("2. Stimulator open + identity")
    try:
        from stimtest.hardware import open_stimulator
    except Exception as e:
        _fail(f"could not import open_stimulator: {e}")
        return False

    stim = None
    try:
        stim = open_stimulator(simulate=False)
        # Detect whether the factory silently fell back to the simulator.
        kind = type(stim).__name__
        if "Simulated" in kind:
            _fail(f"factory returned {kind} — real Plexon driver "
                  f"failed to load (check warnings above)")
            return False
        stim.open()
        info = getattr(stim, "info", None)
        if info is None:
            _info("no .info object on the stimulator instance")
        else:
            _ok(f"opened {kind}")
            for fld in ("make", "model", "firmware_version", "n_channels",
                        "serial_number"):
                v = getattr(info, fld, None)
                if v is not None:
                    _info(f"{fld:18s}: {v}")
        return True
    except Exception:
        _fail("exception while opening stimulator:")
        traceback.print_exc()
        return False
    finally:
        if stim is not None:
            try:
                stim.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Step 3 — Enumerate VISA resources
# ---------------------------------------------------------------------------
def step_visa_enumerate() -> bool:
    _hr("3. VISA resource enumeration (scope discovery)")
    try:
        import pyvisa
    except Exception as e:
        _fail(f"pyvisa not importable: {e}")
        return False

    # Try the system NI-VISA backend first (better USB-TMC throughput),
    # fall back to pure-Python pyvisa-py if NI isn't installed.
    backends = [("@ni", "NI-VISA"), ("@py", "pyvisa-py")]
    found_any = False
    for spec, label in backends:
        try:
            rm = pyvisa.ResourceManager(spec)
        except Exception as e:
            _info(f"{label}: not available ({type(e).__name__}: {e})")
            continue
        try:
            res = rm.list_resources()
        except Exception as e:
            _info(f"{label}: list_resources failed ({e})")
            rm.close()
            continue
        if res:
            _ok(f"{label} listed {len(res)} resource(s):")
            for r in res:
                _info(f"  {r}")
            found_any = True
        else:
            _info(f"{label}: backend up but no resources visible")
        rm.close()
    if not found_any:
        _fail("no VISA resources visible — the scope may be off, "
              "unplugged, or claimed by another program")
    return found_any


# ---------------------------------------------------------------------------
# Step 4 — Open the scope and read *IDN?
# ---------------------------------------------------------------------------
def step_open_scope() -> bool:
    _hr("4. Tektronix scope open + identity")
    try:
        from stimtest.hardware import open_oscilloscope
    except Exception as e:
        _fail(f"could not import open_oscilloscope: {e}")
        return False

    scope = None
    try:
        scope = open_oscilloscope(simulate=False)
        kind = type(scope).__name__
        if "Simulated" in kind:
            _fail(f"factory returned {kind} — real Tek driver failed "
                  f"to bind (check warnings above)")
            return False
        scope.open()
        info = getattr(scope, "info", None)
        if info is None:
            _info("no .info object on the scope instance")
        else:
            _ok(f"opened {kind}")
            for fld in ("make", "model", "firmware_version", "n_channels",
                        "serial_number", "resource"):
                v = getattr(info, fld, None)
                if v is not None:
                    _info(f"{fld:18s}: {v}")
        return True
    except Exception:
        _fail("exception while opening scope:")
        traceback.print_exc()
        return False
    finally:
        if scope is not None:
            try:
                scope.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------
def main() -> int:
    print("PULSAR — hardware probe")
    print(f"Python: {sys.version.splitlines()[0]}")

    # SDK detection and VISA enumeration are informational — they tell
    # us *how* the hardware is wired up, but they're not pass/fail
    # gates. The real verdicts are "stim opened" and "scope opened":
    # if the device opens and reports identity, the system is fine
    # regardless of registry quirks or pyvisa-py's list_resources
    # behavior on Windows USB-TMC.
    sdk_info  = step_detect_sdk()
    stim_ok   = step_open_stim()
    visa_info = step_visa_enumerate()
    scope_ok  = step_open_scope()

    _hr("Summary")
    print(f"  [{'OK  ' if stim_ok  else 'FAIL'}] stimulator open")
    print(f"  [{'OK  ' if scope_ok else 'FAIL'}] scope open")
    print(f"  [INFO ] SDK detected via registry/filesystem: {sdk_info}")
    print(f"  [INFO ] VISA enumeration listed >=1 resource:  {visa_info}")
    if not (stim_ok and scope_ok):
        print()
        print("  At least one device failed to open — look for [FAIL]")
        print("  lines above for the cause.")
    return 0 if (stim_ok and scope_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
