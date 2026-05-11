"""Detect whether the Plexon PlexStim 2.0 SDK is installed on this PC.

The PlexStim SDK installer (``StimulatorV2Setup.exe`` from
https://plexon.com/wp-content/uploads/2017/06/StimulatorV2Setup.exe)
drops:

* the USB driver that exposes the stimulator as a Plexon-vendor device,
* a "Plexon Inc / PlexStim 2.0" entry in the Windows registry under
  ``HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall``,
* a ``PlexStim64.dll`` (and 32-bit ``PlexStim.dll``) under one of the
  following install folders, depending on the Plexon installer
  version:

  * ``C:\\PlexonSDKs\\<sdk-name>\\`` — current default (modern
    installer; the data folder ``C:\\PlexonData`` is created
    alongside),
  * ``C:\\Program Files\\Plexon Inc\\PlexStim 2.0\\`` — older
    layout still seen on long-lived workstations.

  The Python wrapper (PyPlexStim) is **NOT** part of the SDK
  installer — it ships separately. This package vendors a copy
  under ``stimtest/hardware/pyplexstim/`` so an end-user only
  needs to install the Plexon SDK itself.

The application *itself* ships with a vendored copy of ``PlexStim64.dll``
(see ``stimtest/hardware/pyplexstim/bin/``), but the DLL alone isn't
enough — without the SDK installed, the USB driver isn't on the system
and ``PS_InitAllStim()`` returns "no stimulators found" even when one is
plugged in. This module exists to tell the user that, *before* they
spend ten minutes wondering why their stimulator won't connect.

The detection has three levels and reports the most precise one it finds:

1. **Registry** — search both 32-bit and 64-bit Uninstall hives for an
   entry whose ``DisplayName`` contains "PlexStim" / "Plexon" /
   "Stimulator". Yields a real install path and version string when
   present.
2. **Common install paths** — fall back to ``%ProgramFiles%`` and
   ``%ProgramFiles(x86)%`` for ``Plexon Inc\\PlexStim 2.0\\``. Still gives
   the install path, but no version info.
3. **DLL loadability** — try to ``ctypes.CDLL`` the system or vendored
   ``PlexStim64.dll``. Just a sanity check that the binary at least
   resolves; it doesn't tell us whether the kernel-mode USB driver is
   installed.

The result is a dataclass so callers can present a friendly status line
("PlexStim 2.4.0 found at ..." / "PlexStim SDK not found — please run
StimulatorV2Setup.exe") rather than raw booleans.
"""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


PLEXSTIM_INSTALLER_URL = (
    "https://plexon.com/wp-content/uploads/2017/06/StimulatorV2Setup.exe"
)


@dataclass
class PlexStimStatus:
    """What we know about the PlexStim software stack on this machine."""
    installed: bool = False
    install_path: Optional[Path] = None
    version: Optional[str] = None
    dll_path: Optional[Path] = None
    dll_loadable: bool = False
    #: Path to the Stim-2 GUI executable, when found. The presence of
    #: this file is the *primary* signal that the Stimulator V2
    #: software is installed (rather than just the SDK / DLL alone).
    stim2_exe: Optional[Path] = None
    detection_source: str = ""        # 'registry' | 'filesystem' | 'dll-only' | 'none'
    notes: List[str] = field(default_factory=list)

    def status_line(self) -> str:
        """One-line human-readable summary for status bars / log lines."""
        if self.stim2_exe is not None:
            return f"Stim-2 found at {self.stim2_exe}"
        if self.installed and self.install_path:
            v = f" {self.version}" if self.version else ""
            return f"PlexStim SDK{v} found at {self.install_path}"
        if self.dll_loadable and self.dll_path:
            return (f"PlexStim DLL at {self.dll_path} loads, but the "
                    f"Stim-2 application doesn't appear to be installed. "
                    f"The USB driver may be missing.")
        return "Stim-2 application not found"

    def as_dict(self) -> dict:
        return {
            "installed": self.installed,
            "install_path": str(self.install_path) if self.install_path else None,
            "version": self.version,
            "dll_path": str(self.dll_path) if self.dll_path else None,
            "dll_loadable": self.dll_loadable,
            "stim2_exe": str(self.stim2_exe) if self.stim2_exe else None,
            "detection_source": self.detection_source,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------
_REGISTRY_NAME_HINTS = ("plexstim", "stimulator v2", "plexon",
                         "stim-2", "stim 2")
_DLL_NAMES = ("PlexStim64.dll", "PlexStim.dll")
#: Extra "evidence" filenames — the Stim-2 GUI executable. Plexon's
#: modern installer drops it as ``Stim-2.exe`` under
#: ``C:\Program Files (x86)\Plexon Inc\Stim-2\`` (rather than the
#: legacy ``PlexStim 2.0\`` folder). Finding this exe is a strong
#: signal that the user has the Stimulator V2 software installed,
#: even if our DLL search misses it because the modern installer
#: doesn't co-locate the SDK DLL with the application.
_STIM2_EXE_NAMES = ("Stim-2.exe", "Stim2.exe", "StimulatorV2.exe")


def _enumerate_uninstall_keys() -> List[dict]:
    """Walk the Windows ``Uninstall`` registry, yielding ``DisplayName``
    blocks that look like Plexon installations.

    Looks under both ``HKLM`` and ``HKCU``, and on 64-bit Windows under
    both the native and WOW6432Node mirrors so we catch 32-bit installs
    too. Returns empty on non-Windows or when winreg isn't available
    (PyInstaller-frozen builds always have it on Windows).
    """
    if platform.system() != "Windows":
        return []
    try:
        import winreg
    except ImportError:
        return []

    roots = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
         winreg.KEY_READ | winreg.KEY_WOW64_64KEY),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
         winreg.KEY_READ | winreg.KEY_WOW64_32KEY),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
         winreg.KEY_READ),
    ]
    matches: List[dict] = []
    for hive, subkey, access in roots:
        try:
            root = winreg.OpenKey(hive, subkey, 0, access)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    sub = winreg.OpenKey(root, name)
                except OSError:
                    continue
                try:
                    display = _try_get_value(sub, "DisplayName") or ""
                    if not any(hint in display.lower()
                               for hint in _REGISTRY_NAME_HINTS):
                        continue
                    matches.append({
                        "DisplayName": display,
                        "DisplayVersion": _try_get_value(sub, "DisplayVersion"),
                        "InstallLocation": _try_get_value(sub, "InstallLocation"),
                        "Publisher": _try_get_value(sub, "Publisher"),
                        "key_path": f"{subkey}\\{name}",
                    })
                finally:
                    winreg.CloseKey(sub)
        finally:
            winreg.CloseKey(root)
    return matches


def _try_get_value(key, name: str) -> Optional[str]:
    """Read a string value from a registry key; None on missing."""
    try:
        import winreg
        val, _typ = winreg.QueryValueEx(key, name)
        return str(val).strip() if val is not None else None
    except (FileNotFoundError, OSError):
        return None


#: Plexon's modern installer drops SDKs under ``C:\PlexonSDKs\<sdk-name>``
#: (with a sibling ``C:\PlexonData`` for runtime data). The folder name
#: under ``PlexonSDKs`` varies by version — e.g. ``PlexStim 2.0``,
#: ``PlexStim_SDK_v2.0_x64``, ``Stimulator V2 SDK``. Rather than hard-code
#: a guess, we walk every direct subdirectory and accept any whose name
#: hints at PlexStim / Stimulator.
_PLEXON_SDKS_ROOTS = (Path(r"C:\PlexonSDKs"),)
_SDK_FOLDER_NAME_HINTS = ("plexstim", "stimulator")


def _common_install_paths() -> List[Path]:
    """Filesystem fallbacks if the registry lookup didn't pan out."""
    candidates: List[Path] = []
    program_files_64 = os.environ.get("ProgramW6432") \
        or os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_32 = os.environ.get("ProgramFiles(x86)",
                                       r"C:\Program Files (x86)")
    for root in (program_files_64, program_files_32):
        if not root:
            continue
        # Modern installer (current default): installs the GUI under
        # ``Plexon Inc\Stim-2\``.
        candidates.append(Path(root) / "Plexon Inc" / "Stim-2")
        # Legacy install folders (older Plexon installers).
        candidates.append(Path(root) / "Plexon Inc" / "PlexStim 2.0")
        candidates.append(Path(root) / "Plexon Inc" / "PlexStim")
        candidates.append(Path(root) / "Plexon Inc" / "Stimulator V2")
    # Modern Plexon installers default to C:\PlexonSDKs\<sdk-name> rather
    # than under Program Files. The exact subfolder name varies across
    # SDK versions, so accept any direct child whose name mentions
    # "PlexStim" or "Stimulator". This makes auto-discovery work without
    # requiring the user to point at the DLL manually.
    for plex_root in _PLEXON_SDKS_ROOTS:
        try:
            if not plex_root.is_dir():
                continue
            for sub in plex_root.iterdir():
                if (sub.is_dir() and
                        any(h in sub.name.lower()
                            for h in _SDK_FOLDER_NAME_HINTS)):
                    candidates.append(sub)
        except OSError:
            # Permission / network-drive timeout / etc. — never let a
            # filesystem hiccup take down detection.
            continue
    return candidates


def _find_dll_in(folder: Path) -> Optional[Path]:
    if not folder.is_dir():
        return None
    for name in _DLL_NAMES:
        p = folder / name
        if p.is_file():
            return p
    # Some installers nest under a "bin/" subfolder — check one level deep.
    for sub in folder.iterdir():
        if sub.is_dir() and sub.name.lower() in ("bin", "lib"):
            for name in _DLL_NAMES:
                p = sub / name
                if p.is_file():
                    return p
    return None


def _find_stim2_exe_in(folder: Path) -> Optional[Path]:
    """Look for the Stim-2 GUI executable under ``folder``. The
    modern Plexon installer drops ``Stim-2.exe`` directly at the
    install root; older / SDK-style layouts may put it under a
    subfolder so we also peek one level deep."""
    if not folder.is_dir():
        return None
    for name in _STIM2_EXE_NAMES:
        p = folder / name
        if p.is_file():
            return p
    try:
        for sub in folder.iterdir():
            if not sub.is_dir():
                continue
            for name in _STIM2_EXE_NAMES:
                p = sub / name
                if p.is_file():
                    return p
    except OSError:
        pass
    return None


def _try_load_dll(dll_path: Path) -> bool:
    """Attempt to load the DLL. ``True`` if ctypes can resolve it.

    A successful load means the binary is well-formed and the immediate
    dependencies (Plexon's own helpers, Visual C++ runtime) are present.
    It does NOT prove the kernel-mode USB driver is installed — that
    requires the full SDK installer. So this is a *necessary* but not
    *sufficient* check.
    """
    try:
        from ctypes import CDLL
        CDLL(str(dll_path))
        return True
    except (OSError, FileNotFoundError, Exception):
        return False


def _vendored_dll() -> Optional[Path]:
    """Path to the DLL bundled inside the package, if any."""
    here = Path(__file__).resolve().parent
    p = here / "pyplexstim" / "bin" / "PlexStim64.dll"
    if p.is_file():
        return p
    p32 = here / "pyplexstim" / "bin" / "PlexStim.dll"
    return p32 if p32.is_file() else None


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------
def detect_plexstim() -> PlexStimStatus:
    """Best-effort detection of an installed PlexStim 2.0 SDK.

    Returns a fully populated :class:`PlexStimStatus`. Always succeeds —
    callers should branch on ``status.installed`` and
    ``status.dll_loadable`` rather than catching exceptions.
    """
    status = PlexStimStatus()

    # ---- 1. Registry --------------------------------------------------
    matches = _enumerate_uninstall_keys()
    for m in matches:
        loc = m.get("InstallLocation") or ""
        if loc:
            p = Path(loc)
            if p.is_dir():
                status.install_path = p
                status.version = m.get("DisplayVersion")
                status.detection_source = "registry"
                status.notes.append(
                    f"Registry hit: {m['DisplayName']} "
                    f"(key: {m['key_path']})")
                break
        else:
            # Registry says it's installed but didn't tell us where; remember
            # this as a soft signal and keep looking via the filesystem.
            status.notes.append(
                f"Registry mentions {m['DisplayName']} but has no "
                f"InstallLocation — falling back to filesystem search.")

    # ---- 2. Filesystem fallback --------------------------------------
    if status.install_path is None:
        for cand in _common_install_paths():
            if cand.is_dir():
                status.install_path = cand
                status.detection_source = "filesystem"
                status.notes.append(
                    f"Found {cand} via common-install-path search.")
                break

    # ---- 3. Locate a DLL ---------------------------------------------
    if status.install_path is not None:
        dll = _find_dll_in(status.install_path)
        if dll is not None:
            status.dll_path = dll

    if status.dll_path is None:
        # Fall back to the vendored copy that ships with the app
        v = _vendored_dll()
        if v is not None:
            status.dll_path = v
            if status.detection_source == "":
                status.detection_source = "dll-only"
            status.notes.append(f"Using vendored DLL: {v}")

    # ---- 3b. Look for the Stim-2 application -------------------------
    # This is the strong signal the user actually has the Stimulator V2
    # software (not just an orphan DLL). We check the previously-found
    # install path first, then sweep every common-install candidate
    # so we catch installs that the registry / DLL search missed
    # (which is exactly the case the user reported).
    search_roots = []
    if status.install_path is not None:
        search_roots.append(status.install_path)
    search_roots.extend(_common_install_paths())
    seen = set()
    for root in search_roots:
        rp = str(root)
        if rp in seen:
            continue
        seen.add(rp)
        exe = _find_stim2_exe_in(root)
        if exe is not None:
            status.stim2_exe = exe
            # If we'd previously had no install evidence, take the
            # exe's parent folder as the install path.
            if status.install_path is None:
                status.install_path = exe.parent
                status.detection_source = "filesystem"
            status.notes.append(f"Stim-2 GUI found: {exe}")
            break

    # ---- 4. Verify loadability ---------------------------------------
    if status.dll_path is not None:
        status.dll_loadable = _try_load_dll(status.dll_path)
        if not status.dll_loadable:
            status.notes.append(
                f"DLL at {status.dll_path} failed to load via ctypes — "
                f"the Visual C++ redistributable may be missing.")

    # "Installed" reflects the user-visible reality: did we find the
    # Stim-2 application on disk? That's the file the user has to
    # close before the SDK can grab the USB lock and the strongest
    # signal that the Plexon installer ran. Falls back to registry /
    # SDK-folder evidence so we still mark it installed for older
    # layouts that don't ship the GUI exe.
    status.installed = (status.stim2_exe is not None
                        or status.detection_source in ("registry", "filesystem"))
    if not status.detection_source:
        status.detection_source = "none"
    return status


# ---------------------------------------------------------------------------
# Hardware-presence detection (separate from software-install detection)
# ---------------------------------------------------------------------------
#
# :func:`detect_plexstim` answers "is the SOFTWARE installed?" — registry
# entries, files on disk, DLL loadability. None of that proves a
# physical stimulator is plugged in over USB.
#
# :func:`plexstim_device_present` answers the complementary question:
# "is a Plexon stimulator currently enumerated by the operating
# system?". Used by the Connection panel's stim-detect indicator dot
# (mirroring the scope-detect dot driven by the VISA enumerator) and
# refreshed by the USB-hot-plug filter in
# :mod:`stimtest.gui.usb_hotplug` whenever a USB device arrives or
# leaves.
#
# Returns:
#   * ``True``  — a Plexon-named device is plugged in.
#   * ``False`` — the probe ran and saw no matching device.
#   * ``None``  — the probe couldn't run (non-Windows host, missing
#                 PowerShell, timeout, etc.); the GUI surfaces this
#                 as a separate "unknown" state rather than collapsing
#                 it to "not present".
#
# We deliberately keep the probe shell-out-to-PowerShell rather than
# parsing pyusb / SetupAPI directly. PowerShell's ``Get-PnpDevice`` is
# always available on every supported Windows version (10+), it
# handles WOW64 quirks transparently, and the friendly-name match
# survives Plexon shipping different USB descriptors / VID-PIDs over
# the years without requiring us to maintain a static VID list.

#: PnP / friendly-name substrings that identify a Plexon stimulator.
#: Matching is case-insensitive. Order doesn't matter — any single
#: match flips the probe to "present". Conservative substring set so
#: a non-Plexon Plexon-Inc. lab device (a recording headstage, e.g.)
#: doesn't false-positive as a stimulator.
_PLEX_DEVICE_NAME_HINTS = (
    "plexstim",
    "stimulator v2",
    "stim-2",
    "stim 2",
)


def plexstim_device_present(timeout_s: float = 2.0) -> Optional[bool]:
    """Probe Windows PnP for a plugged-in Plexon stimulator.

    Parameters
    ----------
    timeout_s : float, optional
        Seconds to wait for the PowerShell probe to respond before
        giving up. The default 2 s is plenty on a healthy machine
        (PowerShell startup + ``Get-PnpDevice`` typically returns
        in well under a second), and a slow box just degrades to
        the ``None`` (unknown) state.

    Returns
    -------
    bool or None
        * ``True``  — at least one currently-present USB device's
          friendly-name matches one of
          :data:`_PLEX_DEVICE_NAME_HINTS`.
        * ``False`` — probe ran successfully and matched nothing.
        * ``None``  — probe couldn't run (non-Windows host,
          missing PowerShell, timeout, etc.).
    """
    if platform.system() != "Windows":
        return None
    # Build a single PowerShell one-liner that filters PnP devices to
    # the present-only set, matches against any of our hints (joined
    # into one regex alternation), and prints just the matched
    # friendly-name strings — one per line so the parser is trivial.
    # Hints are escaped for regex safety even though they're
    # alphanumeric today; cheap insurance if the list grows later.
    import re
    import subprocess
    pattern = "|".join(re.escape(h) for h in _PLEX_DEVICE_NAME_HINTS)
    ps_script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "Get-PnpDevice -PresentOnly | "
        f"Where-Object {{ $_.FriendlyName -match '{pattern}' "
        f"-or $_.Manufacturer -match 'plexon' }} | "
        "Select-Object -ExpandProperty FriendlyName"
    )
    try:
        # CREATE_NO_WINDOW (= 0x08000000) suppresses the brief
        # PowerShell window flash on non-frozen builds; it's a no-op
        # on non-Windows but we already short-circuited above.
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["powershell", "-NoProfile",
             "-NonInteractive",
             "-ExecutionPolicy", "Bypass",
             "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            creationflags=creation_flags,
        )
    except (FileNotFoundError, OSError):
        # PowerShell binary not on PATH; can't tell.
        return None
    except subprocess.TimeoutExpired:
        # PnP enumeration is generally fast — a timeout suggests a
        # very loaded machine or a hung WMI service. Surface as
        # "unknown" rather than "absent" so the user isn't told
        # the device isn't plugged in when actually we don't know.
        return None
    if result.returncode != 0:
        return None
    matches = [line.strip() for line in (result.stdout or "").splitlines()
               if line.strip()]
    return len(matches) > 0


__all__ = [
    "PlexStimStatus", "detect_plexstim", "PLEXSTIM_INSTALLER_URL",
    "plexstim_device_present",
]
