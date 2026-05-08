# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for StimulationTesting.

Builds two side-by-side Windows executables that share one set of binary
dependencies in a single output folder (``onedir`` mode):

* ``StimulationTesting.exe`` — the main GUI (``run_gui.py``)
* ``StimulationTestingViewer.exe`` — the Echem-Analyst-style viewer
  (``run_viewer.py``)

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

# ---------------------------------------------------------------------------
# Shared data files & hidden imports
# ---------------------------------------------------------------------------
plexstim_bin_src = ROOT / "stimtest" / "hardware" / "pyplexstim" / "bin"
plexstim_bin_dst = "stimtest/hardware/pyplexstim/bin"

datas = []
if plexstim_bin_src.exists():
    for dll in plexstim_bin_src.glob("*.dll"):
        datas.append((str(dll), plexstim_bin_dst))

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
    icon=None,
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
    icon=None,
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
