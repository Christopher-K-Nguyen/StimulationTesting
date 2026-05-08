"""Detect and force-close Plexon's Sim-2 / Stimulator V2 GUI.

Plexon's own desktop application holds an exclusive USB lock on the
PlexStim 2.0 hardware — while it's open, every ``PS_InitAllStim`` call
from the SDK fails with *"No Plexon Stimulator is detected."* even
though the device is plugged in and powered.

This module enumerates running processes that could be holding that
lock and (optionally) terminates them via ``taskkill``. Two filters:

1. **Process image-name substring match** against a known-name list
   (``StimulatorV2``, ``PlexStim``, ``Sim-2``, …). Plexon has shipped
   the GUI under a few different binary names across SDK versions —
   substring matching catches them all without needing to chase the
   exact spelling.
2. **Executable path under known Plexon install roots**
   (``C:\\PlexonSDKs`` or ``C:\\Program Files\\Plexon Inc``). Any
   process whose ``.exe`` lives there is a Plexon-shipped tool by
   construction; if it's running and the SDK can't talk to the
   device, that process is the most likely culprit.

The two filters are OR'd: a process matched by either is treated as
a blocker. We deliberately don't kill processes outside those install
roots — even if the name happens to contain "stim", that could be a
user-launched tool we shouldn't touch.

Auto-recovery is wired into :class:`stimtest.hardware.plexon.PlexonStimulator`
— ``open()`` calls :func:`close_blocking_processes` once when the
SDK reports "no stimulator detected" and retries the init. This module
also has a CLI entry point::

    python -m stimtest.hardware.plexstim_lock           # report only
    python -m stimtest.hardware.plexstim_lock --close   # detect + force-close

so a user can manually clear the lock without touching Task Manager.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List


# Known image names for Plexon's stim GUI across SDK revisions. Match
# is case-insensitive substring on the process name (``Process.exe``).
# The list is intentionally generous — any Plexon-shipped GUI launched
# while the SDK is in use will block the SDK lock.
_KNOWN_PROCESS_NAME_HINTS = (
    "stimulatorv2",       # newer installer label
    "plexstim",           # older binary name
    "stim-2", "stim2",    # actual current binary (e.g. Stim-2.exe at
                          # C:\Program Files (x86)\Plexon Inc\Stim-2\)
    "sim-2", "sim2",      # marketing name (different from binary)
    "stimulator v2",      # window-title fallback
)

# Filesystem roots where Plexon-shipped binaries legitimately live.
# Any running process whose ``.exe`` resolves under one of these is
# considered a Plexon-shipped tool and is eligible for force-close.
_PLEXON_INSTALL_ROOTS = (
    Path(r"C:\PlexonSDKs"),
    Path(r"C:\Program Files\Plexon Inc"),
    Path(r"C:\Program Files (x86)\Plexon Inc"),
)


@dataclass
class BlockingProcess:
    """One running process that could be holding the PlexStim USB lock."""
    name: str
    pid: int
    exe: str          # full path or '' when tasklist couldn't resolve it
    matched_via: str  # 'name' | 'install-path' | 'both'

    def display(self) -> str:
        return f"{self.name} (PID {self.pid})"


def _running_on_windows() -> bool:
    return platform.system() == "Windows"


def _enumerate_processes() -> List[dict]:
    """Single bulk enumeration of (name, pid, exe) for every process.

    Two strategies, used in order:

    1. **PowerShell ``Get-CimInstance Win32_Process``** — fast,
       always available on Windows 10/11. One subprocess call,
       structured output.
    2. **``wmic process get`` fallback** — wmic is deprecated but
       ships on enough installs that it's worth keeping as a
       backstop. Same single-call shape.

    Earlier versions called ``wmic`` per-PID after a name match —
    that turned an O(1) lookup into O(N) subprocesses (~200 calls,
    multi-minute wall-time). Don't go back to that.

    Returns a list of dicts with keys ``name``, ``pid``, ``exe``.
    Missing values come back as empty strings / ``None``.
    """
    if not _running_on_windows():
        return []
    rows = _enumerate_via_powershell()
    if rows:
        return rows
    return _enumerate_via_wmic()


def _enumerate_via_powershell() -> List[dict]:
    """Get name + pid + executable path via ``Get-CimInstance Win32_Process``.

    The CSV output is name, pid, path on each line. Uses ConvertTo-Csv
    so the parser is trivial (no JSON dependency, no quoting hell).
    """
    cmd = [
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "Get-CimInstance Win32_Process | "
        "Select-Object Name,ProcessId,ExecutablePath | "
        "ConvertTo-Csv -NoTypeInformation",
    ]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True, timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    rows: List[dict] = []
    for i, line in enumerate(out.splitlines()):
        if i == 0:
            continue  # CSV header
        parts = _parse_csv_line(line)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[1])
        except ValueError:
            continue
        rows.append({"name": parts[0], "pid": pid, "exe": parts[2]})
    return rows


def _enumerate_via_wmic() -> List[dict]:
    """Backstop: ``wmic process get name,processid,executablepath /format:csv``.

    wmic is marked deprecated by Microsoft but still ships on most
    Windows 10/11 installs. One call, no per-PID round-trips.
    """
    cmd = ["wmic", "process", "get",
           "Name,ProcessId,ExecutablePath", "/format:csv"]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.DEVNULL, text=True, timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return []
    rows: List[dict] = []
    header_seen = False
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if not header_seen:
            # wmic CSV: Node,ExecutablePath,Name,ProcessId
            header_seen = True
            continue
        if len(parts) < 4:
            continue
        # Order from wmic CSV: Node, ExecutablePath, Name, ProcessId
        exe = parts[1].strip()
        name = parts[2].strip()
        try:
            pid = int(parts[3].strip())
        except ValueError:
            continue
        rows.append({"name": name, "pid": pid, "exe": exe})
    return rows


def _parse_csv_line(line: str) -> List[str]:
    """Split a quoted CSV line into fields, honouring escaped quotes.

    Pure-Python so we don't pull csv module just for one line. Handles
    the common shape produced by PowerShell's ConvertTo-Csv:
    ``"field","field","field"``.
    """
    out: List[str] = []
    cur: List[str] = []
    in_quote = False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            if in_quote and i + 1 < len(line) and line[i + 1] == '"':
                cur.append('"'); i += 2; continue
            in_quote = not in_quote
        elif c == ',' and not in_quote:
            out.append("".join(cur)); cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return out


def _exe_under_plexon_root(exe: str) -> bool:
    if not exe:
        return False
    try:
        exe_p = Path(exe).resolve()
    except OSError:
        return False
    for root in _PLEXON_INSTALL_ROOTS:
        try:
            exe_p.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _name_matches(name: str) -> bool:
    lower = name.lower()
    return any(needle in lower for needle in _KNOWN_PROCESS_NAME_HINTS)


def find_blocking_processes() -> List[BlockingProcess]:
    """Enumerate running processes that look like Plexon's Sim-2 GUI.

    Returns an empty list on non-Windows or when no enumeration
    backend is available. Both filters (name substring + install
    root) are evaluated against a single bulk Win32_Process query,
    so the whole pass is one subprocess call regardless of process
    count.
    """
    matches: List[BlockingProcess] = []
    for row in _enumerate_processes():
        name = row["name"]
        pid = row["pid"]
        exe = row["exe"]
        name_hit = _name_matches(name)
        path_hit = _exe_under_plexon_root(exe)
        if not (name_hit or path_hit):
            continue
        matched = ("both" if (name_hit and path_hit)
                   else ("name" if name_hit else "install-path"))
        matches.append(BlockingProcess(
            name=name, pid=pid, exe=exe, matched_via=matched,
        ))
    return matches


def close_blocking_processes(timeout_s: float = 3.0) -> List[BlockingProcess]:
    """Force-close any running Plexon GUI process. Windows only.

    Returns the list of processes we successfully terminated. An empty
    list means either nothing was running or every taskkill call
    failed (e.g., admin elevation needed for a process owned by
    another user — uncommon for a stim GUI on a single-user lab box).

    Uses ``taskkill /F /PID <pid>`` so we only target the specific
    matched processes, not "every binary with this image name on the
    system" — important if Plexon's GUI is running multiple instances
    (rare) or if a similarly-named user tool happens to be open.
    """
    procs = find_blocking_processes()
    if not procs:
        return []
    closed: List[BlockingProcess] = []
    for p in procs:
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(p.pid)],
                stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                timeout=timeout_s,
            )
            closed.append(p)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            # Skip and try the next one — partial recovery is better
            # than aborting on the first failure.
            continue
    # Give the USB stack and the SDK a moment to release the lock
    # before the caller retries PS_InitAllStim.
    if closed:
        time.sleep(0.5)
    return closed


# ---------------------------------------------------------------------------
# CLI entry point: `python -m stimtest.hardware.plexstim_lock [--close]`
# ---------------------------------------------------------------------------
def _main(argv: List[str]) -> int:
    do_close = "--close" in argv or "-c" in argv
    if not _running_on_windows():
        print("plexstim_lock is Windows-only (no PlexStim USB driver elsewhere).")
        return 0
    procs = find_blocking_processes()
    if not procs:
        print("No Plexon GUI processes detected — SDK lock is free.")
        return 0
    print(f"Found {len(procs)} Plexon GUI process(es):")
    for p in procs:
        loc = p.exe or "(path unknown)"
        print(f"  - {p.display():28s} via {p.matched_via:15s} {loc}")
    if not do_close:
        print()
        print("Re-run with --close to force-terminate them.")
        return 0
    closed = close_blocking_processes()
    print(f"Force-closed {len(closed)} of {len(procs)} process(es).")
    if len(closed) < len(procs):
        print("Some processes could not be closed — try as Administrator, "
              "or close them manually via Task Manager.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))


__all__ = [
    "BlockingProcess",
    "find_blocking_processes",
    "close_blocking_processes",
]
