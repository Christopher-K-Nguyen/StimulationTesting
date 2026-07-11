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
import os
from pathlib import Path
from PyInstaller.utils.hooks import (
    collect_data_files, collect_submodules, copy_metadata,
)

ROOT = Path(SPECPATH).parent.resolve()
APP_NAME = "StimulationTesting"

# INTERSTELLAR opt-in: the experimental interpulse-bias module is OFF by
# default (public installer).  When the build was started with
# ``build.py --with-interstellar`` it sets PULSAR_WITH_INTERSTELLAR=1,
# and we bundle a tiny sentinel file next to the frozen executable.  At
# runtime ``stimtest.feature_flags.interstellar_enabled()`` finds that
# file and turns the bias UI on.  See gotcha #103.
_WITH_INTERSTELLAR = (
    os.environ.get("PULSAR_WITH_INTERSTELLAR", "").strip().lower()
    in ("1", "true", "yes", "on"))


def _safe(fn, *a, **k):
    """Run a PyInstaller collect_* helper, returning ``[]`` if the target
    package isn't installed on the build machine.  Used for the VERSION-
    DEPENDENT transitive deps of scikit-learn / pandas (e.g. ``narwhals``
    exists only for scikit-learn >= 1.9) so the .spec bundles whatever the
    resolved versions actually pull without hard-failing on an absent one."""
    try:
        return list(fn(*a, **k) or [])
    except Exception:
        return []

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

# Optional startup splash images (PyInstaller ``Splash``).  Drop
# ``installer/PULSAR_splash.png`` / ``installer/POLARIS_splash.png`` and the
# bootloader paints them during the cold-start import gap; each app's
# ``launch()`` dismisses its splash via ``pyi_splash.close()`` once the main
# window paints (see stimtest/gui/main_window.py + viewer.py).  Absent files →
# no splash, exactly like the optional app icon above (graceful fallback).
_pulsar_splash_png = ROOT / "installer" / "PULSAR_splash.png"
_polaris_splash_png = ROOT / "installer" / "POLARIS_splash.png"

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
# (``pyplexstimlib.py``) and the package ``__init__.py`` are ALSO listed
# explicitly in ``hiddenimports`` below — PyPlexStim is normally a
# separate SDK install, so we don't leave its lazy import to PyInstaller's
# import-graph analysis.
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

# INTERSTELLAR opt-in sentinel (see _WITH_INTERSTELLAR at the top).  Only
# bundled for the experimental build; ``"."`` puts it in the bundle root
# (``sys._MEIPASS``) where feature_flags.interstellar_enabled() looks.
if _WITH_INTERSTELLAR:
    _interstellar_flag = ROOT / "installer" / "interstellar_enabled.flag"
    if _interstellar_flag.is_file():
        datas.append((str(_interstellar_flag), "."))

# matplotlib styles, font cache, scipy/numpy lazy data, pyqtgraph templates
datas += collect_data_files("matplotlib", includes=["mpl-data/**"])
datas += collect_data_files("pyqtgraph")
datas += collect_data_files("scipy", includes=["**/*.dat", "**/*.npy", "**/*.npz"])
# Egg-info / METADATA so pkg_resources / importlib.metadata lookups work
# inside the bundle (sklearn / pandas check their own version at import time).
datas += copy_metadata("scikit-learn", recursive=True)
datas += copy_metadata("scipy")
datas += copy_metadata("numpy")
datas += copy_metadata("pyqtgraph")
# pandas — tabular session export + the ML weakness analyzer.  It checks its
# version at import and pulls a couple of dependency metadata (joblib is a
# scikit-learn companion but harmless to pin here too).
datas += copy_metadata("pandas")

hiddenimports = []
hiddenimports += collect_submodules("scipy.signal")
hiddenimports += collect_submodules("scipy.ndimage")
hiddenimports += collect_submodules("scipy.io")
hiddenimports += collect_submodules("sklearn.ensemble")
hiddenimports += collect_submodules("sklearn.preprocessing")
hiddenimports += collect_submodules("sklearn.compose")
hiddenimports += collect_submodules("sklearn.pipeline")
# pandas — used by ``stimtest.ml.weakness_analysis`` (DataFrame / read_csv) and
# tabular session export.  PyInstaller ships a pandas hook, but list the core
# submodules explicitly (matching the sklearn/scipy treatment) so a deferred
# ``import pandas`` inside a function can't be dropped from the graph.
hiddenimports += collect_submodules("pandas")
# TRANSITIVE runtime deps of scikit-learn (``threadpoolctl``; ``narwhals`` on
# sklearn >= 1.9) and pandas (``python-dateutil`` → import name ``dateutil``;
# ``tzdata`` for Windows timezones).  Without these bundled, ``import sklearn``
# / ``import pandas`` fail at runtime in the FROZEN app (their own PyInstaller
# hooks cover most but NOT the newer ``narwhals``).  ``_safe`` skips any that
# the build machine's resolved versions don't pull, so the .spec stays correct
# across the ``scikit-learn>=1.3`` / ``pandas>=2.0`` range in pyproject.
for _imp in ("threadpoolctl", "narwhals", "dateutil", "tzdata"):
    hiddenimports += _safe(collect_submodules, _imp)
for _dist in ("threadpoolctl", "narwhals", "python-dateutil", "tzdata", "joblib"):
    datas += _safe(copy_metadata, _dist)
datas += _safe(collect_data_files, "tzdata")
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

# ---------------------------------------------------------------------------
# Explicitly-bundled lazy / separately-installed dependencies.
# PyInstaller's static import graph misses imports that happen INSIDE
# functions (deferred for cold-launch speed) and backends loaded by name,
# so we list them here to GUARANTEE they're in the frozen build.
# ---------------------------------------------------------------------------
# PyPlexStim — the Plexon "Sim-2" SDK Python wrapper is NORMALLY installed
# SEPARATELY (it ships with Plexon's SDK).  The frozen app therefore can't
# rely on it being on the system, and PyInstaller can easily miss the lazy
# ``from .pyplexstim.pyplexstimlib import PyPlexStim`` buried inside
# ``stimtest.hardware.plexon`` methods.  Bundle the VENDORED copy
# explicitly; its 32/64-bit DLLs are already added to ``datas`` above.
hiddenimports += [
    "stimtest.hardware.pyplexstim",
    "stimtest.hardware.pyplexstim.pyplexstimlib",
]
# pyvisa — the NI-VISA C-API backend wrapper (``pyvisa.ctwrapper``) is
# imported only when a system NI-VISA is present, so it's outside the
# static graph (pyvisa_py above is the no-NI fallback).
hiddenimports += collect_submodules("pyvisa")
# openpyxl — .xlsx auto-export (gamry_export / persistence); the workbook /
# cell / styles writer submodules are imported lazily.
hiddenimports += collect_submodules("openpyxl")
# pyserial — STM32 bias module; the Windows backend + port enumerator are
# imported by name inside methods (stm32_bias.py).
hiddenimports += [
    "serial", "serial.serialwin32", "serial.tools.list_ports",
]
# QtMultimedia camera — lazy-imported only when the operator connects a
# camera (camera.py).  The PyQt6 hook collects the Qt multimedia PLUGINS;
# these Python modules still need to be in the graph.
hiddenimports += [
    "PyQt6.QtMultimedia", "PyQt6.QtMultimediaWidgets",
]
# matplotlib Qt backend (POLARIS viewer), pyqtgraph plot exporters (save
# figure as .tif/.png/.svg), and joblib (ML model persistence) — each
# selected / imported by name at runtime.
hiddenimports += ["matplotlib.backends.backend_qtagg", "joblib"]
hiddenimports += collect_submodules("pyqtgraph.exporters")

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

# PULSAR boot splash (only if the PNG is present).
gui_splash = (Splash(str(_pulsar_splash_png), binaries=gui_a.binaries,
                     datas=gui_a.datas, always_on_top=True)
              if _pulsar_splash_png.is_file() else None)

_gui_exe_toc = [gui_pyz, gui_a.scripts]
if gui_splash is not None:
    _gui_exe_toc.append(gui_splash)      # Splash object goes in the EXE …
_gui_exe_toc.append([])                   # … its .binaries go in COLLECT (onedir).

gui_exe = EXE(
    *_gui_exe_toc,
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

# POLARIS boot splash (only if the PNG is present).
viewer_splash = (Splash(str(_polaris_splash_png), binaries=viewer_a.binaries,
                        datas=viewer_a.datas, always_on_top=True)
                 if _polaris_splash_png.is_file() else None)

_viewer_exe_toc = [viewer_pyz, viewer_a.scripts]
if viewer_splash is not None:
    _viewer_exe_toc.append(viewer_splash)
_viewer_exe_toc.append([])

viewer_exe = EXE(
    *_viewer_exe_toc,
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
# Both splashes share the same Tk runtime, so ONE set of splash binaries in
# the shared onedir folder serves both exes — avoids duplicate-file churn.
_splash_binaries = None
if gui_splash is not None:
    _splash_binaries = gui_splash.binaries
elif viewer_splash is not None:
    _splash_binaries = viewer_splash.binaries

_coll_toc = [
    gui_exe,
    gui_a.binaries, gui_a.zipfiles, gui_a.datas,
    viewer_exe,
    viewer_a.binaries, viewer_a.zipfiles, viewer_a.datas,
]
if _splash_binaries is not None:
    _coll_toc.append(_splash_binaries)

coll = COLLECT(
    *_coll_toc,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
