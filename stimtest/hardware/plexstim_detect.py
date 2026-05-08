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
    """What we know about the PlexStim SDK on this machine."""
    installed: bool = False
    install_path: Optional[Path] = None
    version: Optional[str] = None
    dll_path: Optional[Path] = None
    dll_loadable: bool = False
    detection_source: str = ""        # 'registry' | 'filesystem' | 'dll-only' | 'none'
    notes: List[str] = field(default_factory=list)

    def status_line(self) -> str:
        """One-line human-readable summary for status bars / log lines."""
        if self.installed and self.install_path:
            v = f" {self.version}" if self.version else ""
            return f"PlexStim SDK{v} found at {self.install_path}"
        if self.dll_loadable and self.dll_path:
            return (f"PlexStim DLL at {self.dll_path} loads, but the SDK "
                    f"installer doesn't appear to be registered. The USB "
                    f"driver may be missing.")
        return "PlexStim SDK not found"

    def as_dict(self) -> dict:
        return {
            "installed": self.installed,
            "install_path": str(self.install_path) if self.install_path else None,
            "version": self.version,
            "dll_path": str(self.dll_path) if self.dll_path else None,
            "dll_loadable": self.dll_loadable,
            "detection_source": self.detection_source,
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------
_REGISTRY_NAME_HINTS = ("plexstim", "stimulator v2", "plexon")
_DLL_NAMES = ("PlexStim64.dll", "PlexStim.dll")


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
        candidates.append(Path(root) / "Plexon Inc" / "PlexStim 2.0")
        candidates.append(Path(root) / "Plexon Inc" / "PlexStim")
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

    # ---- 4. Verify loadability ---------------------------------------
    if status.dll_path is not None:
        status.dll_loadable = _try_load_dll(status.dll_path)
        if not status.dll_loadable:
            status.notes.append(
                f"DLL at {status.dll_path} failed to load via ctypes — "
                f"the Visual C++ redistributable may be missing.")

    # The SDK is "installed" only if we found a real install (registry or
    # filesystem). The DLL-only path means we have just the bundled
    # binary — useful for offline work but missing the USB driver.
    status.installed = (status.detection_source in ("registry", "filesystem"))
    if not status.detection_source:
        status.detection_source = "none"
    return status


__all__ = ["PlexStimStatus", "detect_plexstim", "PLEXSTIM_INSTALLER_URL"]
