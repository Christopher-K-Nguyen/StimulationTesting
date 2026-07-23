@echo off
REM ===================================================================
REM PULSAR launcher - double-click-friendly.
REM
REM This .bat exists because the .py file association on Windows is
REM brittle: it can be unset by other Python installs, Windows Store
REM updates, or uninstalls of older 3.x versions.  When it breaks,
REM double-clicking run_gui.py does NOTHING - no console, no message,
REM no clue.  cmd.exe is always associated with .bat, so this script
REM always runs regardless of what happened to the .py association.
REM
REM Interpreter selection:
REM   The bundled runtime per the project's pyproject.toml is Python
REM   3.13.  Older Python 3.10-3.12 also work.  PyQt6 wheels for
REM   Python 3.14 are very new and on some installs corrupt the heap
REM   at startup (exit code -1073740940 / 0xC0000374
REM   STATUS_HEAP_CORRUPTION) - so we EXPLICITLY prefer 3.13 first
REM   and fall back through older 3.x versions before considering
REM   the system's default `py -3` or plain `python` on PATH.
REM
REM   For each candidate we probe `<py> -c "import PyQt6"` before
REM   committing - pinning a Python version doesn't help if that
REM   version is missing the GUI deps.  First version with a
REM   working PyQt6 import wins.
REM
REM On failure: PAUSE so the operator can read the traceback instead
REM of staring at a closed window.  On success: exit immediately,
REM same as running from PowerShell.
REM ===================================================================

setlocal enabledelayedexpansion

REM --- Step 1: work from the project root (where stimtest\ lives) ----
cd /d "%~dp0"

REM --- Step 2: pick a Python with the GUI dependencies installed ----
REM The probe imports a REPRESENTATIVE set of the heavy deps
REM (PyQt6 + numpy + scipy + pyqtgraph), NOT just PyQt6.  A Python with
REM PyQt6 but only a PARTIAL dependency set (e.g. PyQt6 + numpy but no
REM scipy) would pass a PyQt6-only probe and then crash at runtime on
REM the first missing import - the classic "numpy/scipy not installed"
REM failure when the launcher lands on the wrong interpreter.
set "PY="

REM Step 2a: honor an explicit per-machine interpreter override.
REM   PULSAR_PYTHON, if set, is the full path to the python.exe that
REM   has the GUI deps - typically a project venv that lives OUTSIDE
REM   this (possibly OneDrive-synced) folder.  Checked FIRST.  Because
REM   it's an environment variable it stays machine-local, so this
REM   launcher file itself stays portable across every machine that
REM   syncs the repo.  Set it once with:
REM       setx PULSAR_PYTHON "C:\path\to\venv\Scripts\python.exe"
if defined PULSAR_PYTHON (
    "%PULSAR_PYTHON%" -c "import PyQt6,numpy,scipy,pyqtgraph" >nul 2>&1
    if !errorlevel! equ 0 (
        set PY="%PULSAR_PYTHON%"
        echo Selected interpreter from PULSAR_PYTHON: "%PULSAR_PYTHON%"
    ) else (
        echo PULSAR_PYTHON was set but that Python failed the dependency probe; falling back to auto-detect.
    )
)

for %%V in (3.13 3.12 3.11 3.10) do (
    if not defined PY (
        py -%%V -c "import PyQt6,numpy,scipy,pyqtgraph" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY=py -%%V"
            echo Selected Python %%V via py launcher.
        )
    )
)

REM Fall back to the default 3.x if no pinned version had PyQt6.
if not defined PY (
    py -3 -c "import PyQt6,numpy,scipy,pyqtgraph" >nul 2>&1
    if !errorlevel! equ 0 (
        set "PY=py -3"
        echo Selected default Python 3.x via py launcher ^(no pinned version had PyQt6^).
    )
)

REM Last resort: plain `python` on PATH.  Often the same as `py -3`
REM but can differ if the user has a venv active or a Microsoft Store
REM Python install.  Only used when neither `py -3.X` nor `py -3`
REM produced a working PyQt6 import.
if not defined PY (
    where python >nul 2>&1
    if !errorlevel! equ 0 (
        python -c "import PyQt6,numpy,scipy,pyqtgraph" >nul 2>&1
        if !errorlevel! equ 0 (
            set "PY=python"
            echo Selected Python on PATH ^(neither py -3.X nor py -3 had PyQt6^).
        )
    )
)

if not defined PY (
    echo.
    echo ERROR: no Python installation found with PyQt6 installed.
    echo.
    echo Diagnostics:
    where python 2>nul
    where py 2>nul
    py -0p 2>nul
    echo.
    echo To fix: pick the Python you want PULSAR to use and run
    echo     py -3.13 -m pip install PyQt6
    echo replacing 3.13 with your preferred version ^(3.10-3.13 are
    echo supported, 3.13 is recommended^).
    echo.
    pause
    exit /b 1
)

REM --- Step 2c: show version + last-update time ----------------------
REM   Print the PULSAR version and when the code was last updated so you
REM   can confirm at a glance you're running the freshly-updated code.
REM   Both are read LIVE from stimtest\__init__.py — the canonical
REM   __version__ string (bumped on every shippable change) and that
REM   file's last-modified timestamp (= when the code was last touched).
REM   Self-maintaining: there is NO hand-edited stamp here to keep in
REM   sync, so the banner can never lie about which version you have.
set "PULSAR_VERSION=unknown"
for /f tokens^=2^ delims^=^" %%v in ('findstr /b /c:"__version__" "%~dp0stimtest\__init__.py" 2^>nul') do set "PULSAR_VERSION=%%v"
set "PULSAR_UPDATED=unknown"
for %%f in ("%~dp0stimtest\__init__.py") do set "PULSAR_UPDATED=%%~tf"
REM   Append the local TIME ZONE (operator: "include the time zone").  The
REM   file mtime %%~tf is local time with no zone; derive a DST-aware
REM   abbreviation the same way the GUI log pane does (initials of the
REM   Daylight/Standard name, e.g. "Mountain Daylight Time" -> "MDT"), via a
REM   short PowerShell call.  Silently skipped if PowerShell is unavailable.
REM   (foreach, NOT a pipe — a "|" inside the for/f backtick needs fragile
REM   escaping that silently swallowed the result.)
set "PULSAR_TZ="
for /f "usebackq delims=" %%z in (`powershell -NoProfile -Command "$t=[TimeZoneInfo]::Local;$n=if((Get-Date).IsDaylightSavingTime()){$t.DaylightName}else{$t.StandardName};if($n.Contains(' ')){$a='';foreach($w in $n.Split(' ')){if($w.Length){$a+=$w.Substring(0,1)}};$a}else{$n}" 2^>nul`) do set "PULSAR_TZ=%%z"
if defined PULSAR_TZ set "PULSAR_UPDATED=%PULSAR_UPDATED% %PULSAR_TZ%"
echo.
echo ==================================================================
echo   PULSAR version %PULSAR_VERSION%   ^(code updated %PULSAR_UPDATED%^)
echo ==================================================================
echo.

REM --- Step 3: launch ------------------------------------------------
REM %* forwards any args you pass to the .bat itself, so you can do
REM e.g. `PULSAR.bat --simulate` or `PULSAR.bat --save-dir D:\runs`.
%PY% run_gui.py %*
set "RC=%ERRORLEVEL%"

REM --- Step 4: pause on failure so the message stays readable -------
REM   * Positive RC = Python raised SystemExit(N) or our crash handler
REM     returned N.  Traceback already printed above.
REM   * -1073740940 / 0xC0000374 = STATUS_HEAP_CORRUPTION - C-level
REM     crash inside a native module (PyQt6, numpy, plexon DLL, Tek
REM     VISA).  Bypasses the Python crash handler entirely (no
REM     pulsar_launch_error.log update), so the log file may be
REM     stale.  Common cause: PyQt6 wheel built for a different
REM     Python ABI than the interpreter loaded it.  Switching to
REM     a different Python version (we now prefer 3.13 above) often
REM     resolves this.
REM
REM   The log location is the project tree (NOT %APPDATA% per user
REM   convention): the Python crash handler prefers <repo>\test\
REM   if it exists, falling back to the repo root itself.
if not "%RC%"=="0" (
    echo.
    echo ----------------------------------------------------------------
    echo PULSAR exited with code %RC%.
    if "%RC%"=="-1073740940" (
        echo This is STATUS_HEAP_CORRUPTION ^(0xC0000374^) - a C-level
        echo crash inside a native module, NOT a Python exception, so
        echo no traceback was printed and pulsar_launch_error.log was
        echo NOT updated by this run.
        echo Try a different Python version:
        echo     py -3.13 -m pip install --upgrade PyQt6 numpy pyqtgraph
        echo or rebuild your venv against Python 3.13.
    ) else (
        echo If you saw a stack trace above, copy it.
        echo A copy is also written to one of:
        if exist "%~dp0test\pulsar_launch_error.log" (
            echo   %~dp0test\pulsar_launch_error.log
        ) else if exist "%~dp0pulsar_launch_error.log" (
            echo   %~dp0pulsar_launch_error.log
        ) else (
            echo   %~dp0test\pulsar_launch_error.log  ^(if test\ exists^)
            echo   %~dp0pulsar_launch_error.log       ^(otherwise^)
        )
    )
    echo ----------------------------------------------------------------
    pause
)
exit /b %RC%
