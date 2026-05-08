# Building the StimulationTesting Windows installer

This folder packages the Python application as a single `.exe` installer
that drops the GUI, the viewer, and all their dependencies under
`Program Files\StimulationTesting`. It also detects whether the **Plexon
PlexStim 2.0 SDK** is installed and offers to download it during setup.

## What gets built

| Artifact | Purpose |
|----------|---------|
| `dist/StimulationTesting/StimulationTesting.exe` | Main GUI (Setup / VT / SP / LP / PS / Results tabs) |
| `dist/StimulationTesting/StimulationTestingViewer.exe` | Echem-Analyst-style session viewer |
| `dist/StimulationTesting/*.dll`, `*.pyd`, etc. | Shared Python / Qt / scipy / matplotlib runtime |
| `dist/StimulationTesting/stimtest/hardware/pyplexstim/bin/PlexStim64.dll` | Vendored PlexStim DLL (used as a fallback) |
| `Output/StimulationTesting-Setup-<version>.exe` | The signed-able single-file installer that goes to end users |

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
| 3 | **NI-VISA / any IVI VISA** — recommended for real Tek scopes | Looks for `System32\visa64.dll`, then `HKLM\SOFTWARE\IVI Foundation\VISA\Win64`, then `HKLM\SOFTWARE\National Instruments\NI-VISA` (TekVISA / Keysight / R&S all satisfy this) | Optional. Prompts user; opens NI's download page in the browser (NI gates direct URLs behind login). The app falls back to bundled `pyvisa-py` if no VISA runtime is present. |

All downloads use PowerShell `Invoke-WebRequest` so no extra installer
machinery is needed; failures fall back to a clear message with the
manual URL.

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

* **App version** — change `AppVersion` at the top of `StimulationTesting.iss`.
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
