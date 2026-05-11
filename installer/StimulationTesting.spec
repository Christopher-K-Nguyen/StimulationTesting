# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PULSAR (GUI) + POLARIS (viewer).

Builds two side-by-side Windows executables that share one set of binary
dependencies in a single output folder (``onedir`` mode):

* ``StimulationTesting.exe`` — the main GUI (``run_gui.py``); the
  running window's title bar reads "PULSAR".
* ``StimulationTestingViewer.exe`` — the Gamry Echem Analyst-style
  session viewer (``run_viewer.py``); the running window's title
  bar reads "POLARIS".

The launcher .exe filenames are intentionally retained under the
legacy "StimulationTesting" name for back-compat with installed-
base shortcuts (see ``installer/README.md`` rename ledger).

Both are ``--windowed`` (no console window). Run from the project root:

    pyinstaller installer/StimulationTesting.spec

Output goes to ``installer/dist/StimulationTesting/``. Inno Setup
(``installer/StimulationTesting.iss``) packages that folder into the
installer.

Notes on what we explicitly pull in
-----------------------------------
* ``stimtest.hardware.pyplexstim.bin.PlexStim64.dll`` is bundled as a
  data file (PyInstaller doesn't auto-detect the ctypes path because the
  DLL is loaded by string).
* ``pyqtgraph`` and ``matplotlib`` data folders ship implicitly via the
  hooks PyInstaller already has.
* sklearn / scipy hidden imports are listed below so the bundled binary
  doesn't ImportError when the ML predictor or peak finder is first
  touched.
"""
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files, collect_submodules, copy_metadata,
)

ROOT = Path(SPECPATH).parent.resolve()
APP_NAME = "StimulationTesting"

# Optional branded icon files. Drop ``installer/app.ico`` (and
# optionally ``installer/app_viewer.ico``) and PyInstaller will embed
# them in the produced EXE resources — the Inno Setup script picks
# the same files up via ``SetupIconFile`` for the installer's own
# icon. If the files aren't present we fall through to PyInstaller's
# default (the Python runtime icon) without erroring.
_app_icon = ROOT / "installer" / "app.ico"
_viewer_icon = ROOT / "installer" / "app_viewer.ico"
APP_ICON = str(_app_icon) if _app_icon.is_file() else None
VIEWER_ICON = (str(_viewer_icon) if _viewer_icon.is_file()
               else APP_ICON)  # fall back to the main icon

# ---------------------------------------------------------------------------
# Shared data files & hidden imports
# ---------------------------------------------------------------------------
plexstim_root_src = ROOT / "stimtest" / "hardware" / "pyplexstim"
plexstim_bin_src = plexstim_root_src / "bin"
plexstim_root_dst = "stimtest/hardware/pyplexstim"
plexstim_bin_dst = "stimtest/hardware/pyplexstim/bin"

datas = []
# PlexStim DLLs — both 32-bit and 64-bit so the wrapper can pick
# whichever matches the frozen Python's bitness. PyInstaller can't
# auto-detect these because they're loaded by string path via ctypes.
if plexstim_bin_src.exists():
    for dll in plexstim_bin_src.glob("*.dll"):
        datas.append((str(dll), plexstim_bin_dst))
    # Any non-DLL artifacts the SDK ships in /bin (e.g. a .lib import
    # library, .dat tables) — bundle them all so the runtime tree
    # mirrors the source layout exactly.
    for extra in plexstim_bin_src.glob("*"):
        if extra.is_file() and extra.suffix.lower() not in (".dll", ".pyc"):
            datas.append((str(extra), plexstim_bin_dst))
# PyPlexStim reference PDF — ship the SDK manual alongside the
# wrapper so users can find the C-API reference without re-installing
# Plexon's "Sim-2" SDK separately. The Python wrapper module
# (``pyplexstimlib.py``) and the package ``__init__.py`` are picked
# up automatically by PyInstaller's import-graph analysis since
# ``stimtest.hardware.plexon`` imports them at runtime.
if plexstim_root_src.exists():
    pdf = plexstim_root_src / "PyPlexStim.pdf"
    if pdf.is_file():
        datas.append((str(pdf), plexstim_root_dst))

# Optional ML assets — the QinjPredictor falls back gracefully if these
# are missing, but bundling them means Predictive mode works out of the
# box on a fresh install. Both go to the same ``data`` subfolder
# relative to the bundled app, matching the in-source layout.
ml_data_src = ROOT / "data"
for fname in ("qinj_dataset.csv", "qinj_model.pkl"):
    p = ml_data_src / fname
    if p.is_file():
        datas.append((str(p), "data"))

# matplotlib styles, font cache, scipy/numpy lazy data, pyqtgraph templates
datas += collect_data_files("matplotlib", includes=["mpl-data/**"])
datas += collect_data_files("pyqtgraph")
datas += collect_data_files("scipy", includes=["**/*.dat", "**/*.npy", "**/*.npz"])
# Egg-info / METADATA so pkg_resources / importlib.metadata lookups work
# inside the bundle (sklearn checks its own version at import time).
datas += copy_metadata("scikit-learn", recursive=True)
datas += copy_metadata("scipy")
datas += copy_metadata("numpy")
datas += copy_metadata("pyqtgraph")

hiddenimports = []
hiddenimports += collect_submodules("scipy.signal")
hiddenimports += collect_submodules("scipy.ndimage")
hiddenimports += collect_submodules("scipy.io")
hiddenimports += collect_submodules("sklearn.ensemble")
hiddenimports += collect_submodules("sklearn.preprocessing")
hiddenimports += collect_submodules("sklearn.compose")
hiddenimports += collect_submodules("sklearn.pipeline")
# pyvisa-py backend pulls things lazily, list them so the frozen build
# can still talk to a USB scope without a system NI-VISA install.
hiddenimports += [
    "pyvisa_py",
    "pyvisa_py.usb",
    "pyvisa_py.tcpip",
    "pyvisa_py.serial",
    "usb",
    "usb.core",
    "usb.backend.libusb1",
    "PyQt6.sip",
]

excludes = [
    "tkinter",
    "PySide6", "PyQt5", "PySide2",
    "IPython", "jedi", "pytest",
    # Avoid pulling Qt for matplotlib if user has it; we drive Qt directly.
    "matplotlib.tests", "scipy.tests",
]

# ---------------------------------------------------------------------------
# GUI executable (run_gui.py)
# ---------------------------------------------------------------------------
gui_a = Analysis(
    [str(ROOT / "run_gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
gui_pyz = PYZ(gui_a.pure)

gui_exe = EXE(
    gui_pyz,
    gui_a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # --windowed: no console for the GUI
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=APP_ICON,
)

# ---------------------------------------------------------------------------
# Viewer executable (run_viewer.py)
# ---------------------------------------------------------------------------
viewer_a = Analysis(
    [str(ROOT / "run_viewer.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
viewer_pyz = PYZ(viewer_a.pure)

viewer_exe = EXE(
    viewer_pyz,
    viewer_a.scripts,
    [],
    exclude_binaries=True,
    name=f"{APP_NAME}Viewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=VIEWER_ICON,
)

# ---------------------------------------------------------------------------
# Single COLLECT step: GUI + viewer share one folder of binaries.
# ---------------------------------------------------------------------------
# Merging the two analyses into one collected folder cuts the installer
# size in half (otherwise PyQt6 + Qt DLLs + numpy + scipy ship twice).
coll = COLLECT(
    gui_exe,
    gui_a.binaries, gui_a.zipfiles, gui_a.datas,
    viewer_exe,
    viewer_a.binaries, viewer_a.zipfiles, viewer_a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
