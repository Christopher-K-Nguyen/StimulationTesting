# Building the PULSAR Windows installer

This folder packages the Python application as a single `.exe` installer
that drops the **PULSAR** GUI, the **POLARIS** viewer,
and all their dependencies under `Program Files\StimulationTesting`. It
also detects whether the **Plexon PlexStim 2.0 SDK** is installed and
offers to download it during setup.

## Rename ledger

The user-facing product was renamed **StimulationTesting → PULSAR**
(GUI) and **StimulationTesting Viewer → POLARIS** (viewer).
The rename intentionally stops short of touching anything that would
break existing installations or external links. The table below is the
authoritative list of what changed vs. what stayed.

| Surface | Value | Why |
|---|---|---|
| **AppName** (window titles, Start Menu folder, Add/Remove Programs) | `PULSAR` | Canonical brand. |
| **Viewer display name** (Start Menu + desktop shortcut labels) | `POLARIS` | Matches the viewer's `setWindowTitle`. |
| **Installer .exe filename** | `PULSAR-Setup-<version>.exe` | First thing the user sees on download. |
| **DefaultDirName** | `%ProgramFiles%\StimulationTesting` | **NOT renamed.** Existing installations upgrade in place (the AppId GUID drives upgrade detection, not the directory). |
| **Launcher .exe filenames** | `StimulationTesting.exe`, `StimulationTestingViewer.exe` | **NOT renamed.** Preserves existing users' Start Menu / desktop / scripted-launch shortcuts. The PyInstaller `.spec` and the `.iss` `AppExeName` / `ViewerExeName` both stay on the old names; if you ever do rename them, ship a matching `[InstallDelete]` step to clean up the obsolete files. |
| **`%APPDATA%\StimulationTesting\`** prefs / cache dir | unchanged | Renaming would orphan every existing installation's calibration, setup, electrode-potential cache, and NeurostimML model. Separate migration. |
| **GitHub repo URL** (`github.com/Bortz1234/StimulationTesting`) | unchanged | Update-check, About-dialog hyperlink, contribute-data issue URL all key off this. GitHub redirects from a renamed repo, but the codebase still hardcodes the canonical name. |
| **Python package** (`import stimtest`) | unchanged | Module path. Renaming would break every internal import. |
| **AppId GUID** | `6E5CDD7E-9D2E-4D38-8B2E-1E2A0BD8C9F1` (unchanged) | Drives Inno Setup's upgrade detection. Preserving it means existing installations get an in-place upgrade rather than a sibling install. |

If you're contributing a release, what you'll see referencing "PULSAR"
is the installer's user-visible surface (window titles, Start Menu,
Add/Remove Programs, downloaded `.exe` filename). What you'll see still
referencing "StimulationTesting" is the codebase / repo / file-system /
launcher filenames — left alone deliberately, per the table above.

## What gets built

| Artifact | Purpose |
|----------|---------|
| `dist/StimulationTesting/StimulationTesting.exe` | Main GUI (PULSAR — Setup / VT / SP / LP / PS / Results tabs) |
| `dist/StimulationTesting/StimulationTestingViewer.exe` | POLARIS — Echem-Analyst-style session viewer |
| `dist/StimulationTesting/*.dll`, `*.pyd`, etc. | Shared Python / Qt / scipy / matplotlib runtime |
| `dist/StimulationTesting/stimtest/hardware/pyplexstim/bin/PlexStim64.dll` | Vendored PlexStim DLL (used as a fallback) |
| `Output/PULSAR-Setup-<version>.exe` | The signed-able single-file installer that goes to end users |

## Prerequisites

You only need these on the **build** machine, not on end users':

1. **Python 3.11+** with the project's runtime requirements installed
   plus PyInstaller:

   ```powershell
   python -m pip install -r requirements.txt
   python -m pip install -e .[build]      # adds pyinstaller
   ```

2. **Inno Setup 6** — the open-source Windows installer authoring tool:
   https://jrsoftware.org/isinfo.php

   The default install path is detected automatically; pass
   `--iscc "<path>\ISCC.exe"` to `build.py` if you put it elsewhere.

3. **No PlexStim SDK required to build.** The build only *bundles* the
   already-vendored `PlexStim64.dll` and writes a registry-search step
   into the installer. End users without the SDK are prompted during
   `Setup.exe` to download it.

`build.py` runs a **pre-flight check** before invoking PyInstaller —
it confirms the runtime deps import, PyInstaller is installed, and
the vendored DLLs exist. It then runs a **post-build smoke test** that
launches the frozen `StimulationTesting.exe --simulate
--skip-prereq-check` for ~6 seconds; if the EXE crashes (missing
hidden import, broken Qt resource, etc.) the build aborts before
Inno Setup runs, so you never package a broken bundle.

## One-shot build

```powershell
# from the project root
python installer/build.py
```

That runs PyInstaller, then Inno Setup, and prints the path of the final
installer. Useful flags:

| Flag | What it does |
|------|--------------|
| `--clean` | Wipe `installer/build/` and `installer/dist/` first (forces a from-scratch rebuild) |
| `--skip-preflight` | Skip the dependency / DLL pre-flight check (use only when iterating fast) |
| `--skip-smoke` | Skip the post-build EXE launch test |
| `--skip-installer` | Stop after PyInstaller — useful when iterating on the spec |
| `--iscc <path>` | Override the path to `ISCC.exe` |

## Manual two-step build

If you want to drive the steps yourself:

```powershell
# 1. PyInstaller (~30–60 s)
pyinstaller installer/StimulationTesting.spec `
    --workpath installer/build `
    --distpath installer/dist `
    --noconfirm

# 2. Inno Setup
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" `
    installer/StimulationTesting.iss
```

## How the prerequisite checks work

The `[Components]` page lets the user untick any of the three; whatever
stays checked is verified after the file copy and, if missing, fetched.

| # | Prereq | Detection | If missing |
|---|--------|-----------|------------|
| 1 | **MS Visual C++ 2015–2022 Redist (x64)** — required by PyQt6 / numpy / scipy DLLs | `HKLM\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64\Installed = 1` | Silently downloads `vc_redist.x64.exe` from `aka.ms/vs/17/release` and installs with `/quiet /norestart`. Tolerates exit codes 0 / 1638 (newer present) / 3010 (reboot needed). |
| 2 | **Plexon PlexStim 2.0 SDK** — needed for a real stimulator | Walks `Uninstall` keys in **both** `HKLM` and `HKLM32` (PlexStim's installer is 32-bit) for a `DisplayName` matching `PlexStim`, `Stimulator V2`, or `Plexon Inc` | Prompts user, then downloads `StimulatorV2Setup.exe` from Plexon's canonical URL and launches it interactively. |
| 3 | **NI-VISA / any IVI VISA** — required for real Tek scopes over USB | Looks for `System32\visa64.dll`, then `HKLM\SOFTWARE\IVI Foundation\VISA\Win64`, then `HKLM\SOFTWARE\National Instruments\NI-VISA` (TekVISA / Keysight / R&S all satisfy this) | Prompts user; opens the NI-VISA download page in the browser. See [NI-VISA installation](#ni-visa-installation) below. |

All downloads use PowerShell `Invoke-WebRequest` so no extra installer
machinery is needed; failures fall back to a clear message with the
manual URL.

### NI-VISA installation

NI-VISA is required to communicate with the Tektronix oscilloscope (and any
other VISA-compatible instrument) over USB. Without it, the scope will fail
to connect with `VI_ERROR_LIBRARY_NFOUND`.

**Download:** https://www.ni.com/en/support/downloads/drivers/download.ni-visa.html

**Steps:**
1. Go to the link above — you will need a free NI account to download.
2. Download the latest **NI-VISA** release (~600 MB).
3. Run the installer and follow the prompts.
4. **Restart your computer** after installation.
5. Reconnect the scope via USB — it should now be detected automatically.

NI-VISA installs the `visa64.dll` system library that pyvisa uses to talk to
instruments. It also installs the proper USB-TMC driver for Tektronix scopes.
It is free and does not require a paid NI license.

> **Note for developers:** If you do not want to install NI-VISA, the pure-Python
> `pyvisa-py` backend is bundled but requires an additional USB driver step on
> Windows (see the project README for details). NI-VISA is strongly recommended
> for production lab use.

### Runtime re-check (PlexStim only)

[`stimtest.hardware.plexstim_detect`](../stimtest/hardware/plexstim_detect.py)
re-runs the PlexStim check on every GUI launch (skipped when `--simulate`
or `--skip-prereq-check` is passed). It additionally:
- searches common install paths (`Program Files\Plexon Inc\PlexStim 2.0\`),
- tries to load the DLL via `ctypes.CDLL` to catch a missing Visual
  C++ runtime,
- reports a friendly summary in a `QMessageBox` if anything is off.

The two-layer design means: even if the user clicked "No, skip download"
during install, they still get a reminder the first time they launch the
app, with the same download URL.

## Customising

* **App version** — bump `__version__` in `stimtest/__init__.py`. That's
  the single source of truth; `build.py` reads it and passes the value
  to Inno Setup via `ISCC /DAppVersion=…`, overriding the `.iss` file's
  `#ifndef AppVersion` fallback. You also need to update
  `pyproject.toml`'s `version = "…"` to match — `build.py` aborts the
  build if the two disagree (audit finding #32). The `.iss` fallback
  value is build-time-only and harmless to leave stale.
  PyInstaller doesn't need a separate version bump unless you want the
  Windows Explorer "Details" tab to show one (set `version_info.txt` in
  the spec).
* **Icon** — drop a `.ico` next to `run_gui.py`, point `EXE(...icon=...)`
  at it in the spec, and add `SetupIconFile=` in the `[Setup]` section
  of the `.iss`.
* **Code signing** — Inno Setup supports `SignTool=` in `[Setup]` and a
  matching `[CodeSigning]` section. Out of scope here, but the script is
  signing-friendly.

## Troubleshooting

* **`ImportError` when running the frozen `.exe`** — usually a missing
  hidden import. Add it to `hiddenimports=[...]` in the spec, rebuild.
* **`ISCC: cannot find ...\dist\StimulationTesting`** — you ran ISCC
  before PyInstaller, or PyInstaller failed silently. Run
  `python installer/build.py` to chain them in order.
* **Big installer size** — the bundled folder is ~250 MB uncompressed,
  ~80 MB compressed (lzma2 ultra). Most of that is PyQt6 + scipy + numpy
  + matplotlib. Removing matplotlib (delete the import in `plotting.py`
  and switch to pyqtgraph for export) is the biggest win, but the
  matplotlib-styled plots are a feature so leave them in.
* **PlexStim download fails** — the installer falls back to printing
  the URL so the user can grab it manually. Plexon's site occasionally
  returns 403 to non-browser user-agents; PowerShell's `Invoke-WebRequest`
  is generally accepted but not guaranteed.
