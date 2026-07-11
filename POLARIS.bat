@echo off
REM ===================================================================
REM POLARIS launcher - double-click-friendly standalone session viewer.
REM
REM POLARIS is the same viewer embedded in PULSAR's Results tab, run as
REM its own window (run_viewer.py).  Open a PULSAR .npz session or a
REM PicoScope .csv capture - or a folder of either - and click through
REM the tree to inspect waveforms + metrics.
REM
REM Usage:
REM   POLARIS.bat                       open empty, then File -> Open
REM   POLARIS.bat path\to\file.npz      open a session
REM   POLARIS.bat path\to\file.csv      open a PicoScope capture
REM   POLARIS.bat path\to\folder        index every .npz / .csv in it
REM
REM Interpreter selection mirrors PULSAR.bat: prefer Python 3.13, fall
REM back through 3.12-3.10, then `py -3`, then plain `python` on PATH;
REM for each candidate probe `<py> -c "import PyQt6,numpy,matplotlib"`
REM (POLARIS renders with matplotlib) and accept the first that works.
REM Honors the same PULSAR_PYTHON override as PULSAR.bat.
REM ===================================================================

setlocal enabledelayedexpansion

REM --- Step 1: work from the project root (where stimtest\ lives) ----
cd /d "%~dp0"

REM --- Step 2: pick a Python with the viewer dependencies installed --
set "PY="

REM Step 2a: honor an explicit per-machine interpreter override - the
REM same PULSAR_PYTHON env var PULSAR.bat uses, so one setting drives
REM both launchers.  Set once with:
REM     setx PULSAR_PYTHON "C:\path\to\venv\Scripts\python.exe"
if defined PULSAR_PYTHON (
    "%PULSAR_PYTHON%" -c "import PyQt6,numpy,matplotlib" >nul 2>&1
    if !errorlevel! equ 0 (
        set PY="%PULSAR_PYTHON%"
        echo Selected interpreter from PULSAR_PYTHON: "%PULSAR_PYTHON%"
    ) else (
        echo PULSAR_PYTHON was set but that Python failed the dependency probe; falling back to auto-detect.
    )
)

for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import PyQt6,numpy,matplotlib" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY=py -%%V"
            echo Selected Python %%V via py launcher.
        )
    )
)

if not defined PY (
    py -3 -c "import PyQt6,numpy,matplotlib" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY=py -3"
        echo Selected default Python 3.x via py launcher.
    )
)

if not defined PY (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        python -c "import PyQt6,numpy,matplotlib" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY=python"
            echo Selected Python on PATH.
        )
    )
)

if not defined PY (
    echo.
    echo ERROR: no Python found with PyQt6 + numpy + matplotlib installed.
    echo.
    echo Diagnostics:
    where python 2>nul
    where py 2>nul
    py -0p 2>nul
    echo.
    echo To fix: pick the Python you want POLARIS to use and run
    echo     py -3.13 -m pip install PyQt6 numpy matplotlib
    echo replacing 3.13 with your preferred version ^(3.10-3.13 supported^).
    echo.
    pause
    exit /b 1
)

REM --- Step 3: launch (forward any path/args to run_viewer.py) -------
%PY% run_viewer.py %*
set "RC=%ERRORLEVEL%"

REM --- Step 4: pause on failure so the message stays readable --------
if not "%RC%"=="0" (
    echo.
    echo ----------------------------------------------------------------
    echo POLARIS exited with code %RC%.
    if "%RC%"=="-1073740940" (
        echo This is STATUS_HEAP_CORRUPTION ^(0xC0000374^) - a C-level
        echo crash inside a native module, not a Python exception.
        echo Try a different Python version:
        echo     py -3.13 -m pip install --upgrade PyQt6 numpy matplotlib
    ) else (
        echo If you saw a stack trace above, copy it.
    )
    echo ----------------------------------------------------------------
    pause
)
exit /b %RC%
