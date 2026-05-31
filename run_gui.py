#!/usr/bin/env python
"""PULSAR — GUI entry point.

Launches the PULSAR neural-stimulation characterization GUI. The
companion session viewer is launched separately via ``run_viewer.py``
(POLARIS).

Startup errors are mirrored to ``<repo>/test/pulsar_launch_error.log``
(or the project root if ``test/`` is empty / missing) and surfaced
via a tkinter message box so a double-click failure on Windows
(where the console window closes the instant the script exits) leaves
a readable diagnostic trail.  Logs live in the project tree — NOT in
``%APPDATA%`` or ``%TEMP%`` — so the operator can find them next to
the bench-test session files they were already opening.  Run from a
terminal to also see the traceback printed inline.
"""
from __future__ import annotations

import argparse
import faulthandler
import os
import sys
import traceback
from pathlib import Path


# ----- C-level crash trace via faulthandler -----
# Native crashes (HEAP_CORRUPTION in PyQt6 / numpy / plexon DLL —
# exit code -1073740940 / 0xC0000374 on Windows) bypass Python's
# ``try / except`` entirely.  ``faulthandler`` registers a SIGSEGV /
# SIGABRT / SIGFPE / SIGILL handler that writes the active Python
# stack frame(s) to a file BEFORE the process is fully killed,
# giving us at least a Python-level location even when the actual
# fault is in C code further down the stack.
#
# Output goes to the same project-tree directory as the regular
# crash log (``<repo>/test/pulsar_faulthandler.log`` when ``test/``
# exists and is non-empty, else ``<repo>/pulsar_faulthandler.log``)
# so all crash artefacts live next to the bench-test session files
# per user convention — NEVER ``%APPDATA%`` or ``%TEMP%``.
#
# Enabled at module-import time (before any other import) so the
# handler is live during the PyQt6 / numpy / scope-driver imports
# themselves — the most common crash sites in recent sessions.
def _enable_faulthandler() -> None:
    """Open a project-tree log file and route faulthandler to it.

    Best-effort: if no project-tree path is writable, fall back to
    stderr (which is at least visible from PULSAR.bat).  The file
    handle is intentionally LEAKED into module scope — closing it
    would deregister the handler the next time the GC runs.
    """
    try:
        script_dir = Path(__file__).resolve().parent
        candidates = []
        test_dir = script_dir / "test"
        if test_dir.is_dir():
            try:
                if any(test_dir.iterdir()):
                    candidates.append(test_dir / "pulsar_faulthandler.log")
            except OSError:
                pass
        candidates.append(script_dir / "pulsar_faulthandler.log")
        for log_path in candidates:
            try:
                # Open in append mode so multiple crashes accumulate
                # rather than overwriting the previous trace.  buffering=1
                # = line-buffered so the trace is on disk by the time
                # the SIGSEGV completes.
                _fh = open(log_path, "a", buffering=1, encoding="utf-8")
                _fh.write(
                    f"\n---- faulthandler armed at "
                    f"{__import__('datetime').datetime.now().isoformat()} ----\n"
                    f"Python: {sys.version.split()[0]} ({sys.executable})\n"
                )
                _fh.flush()
                faulthandler.enable(file=_fh, all_threads=True)
                # Hold the handle in a module-level global so the GC
                # doesn't close it on us.
                globals()["_FAULTHANDLER_FILE"] = _fh
                return
            except Exception:
                continue
        # No project-tree path writable — at least route to stderr.
        faulthandler.enable(all_threads=True)
    except Exception:
        pass


_enable_faulthandler()


def _crash_log_path() -> Path:
    """Path the startup-error log goes to.

    Per user convention (see CLAUDE.md gotcha #18 and the
    ``reference_test_files_dir`` memory): error logs live in the
    PROJECT TREE alongside bench-test session files, NOT in
    ``%APPDATA%`` or ``%TEMP%``.  Scattering logs across
    roaming-profile directories makes them hard to find when the
    operator is trying to read a crash trace — they're already in
    ``<repo>\\test\\`` looking at ``.npz`` / ``.txt`` from a run, and
    that's where they want the crash log too.

    Resolution order:

      1. ``<repo>/test/``  — preferred when it exists AND is
         non-empty (i.e., the user has been actively bench-testing
         and saving session files there).  Matches the directory
         convention in the ``reference_test_files_dir`` memory.
      2. ``<repo>/``       — the script's own directory.  Always
         writable for a dev checkout / editable install, and the
         second place the operator looks when troubleshooting.
      3. ``~/.stimtest/``  — last-resort fallback ONLY when the
         project tree is read-only (e.g. a Program Files install
         the runtime is forbidden from writing to).  Even this is
         a per-user directory, not a roaming / temp one, so the
         log stays findable.

    ``%APPDATA%`` and ``%TEMP%`` are deliberately NOT in the list —
    the user explicitly told us to stop putting error logs there.

    We don't reuse ``stimtest.gui.prefs.prefs_dir`` here for two
    reasons: (1) ``prefs_dir`` returns ``%APPDATA%\\StimulationTesting``,
    which violates the rule above (prefs / calibration / electrode-
    history STILL belong in APPDATA per the existing convention —
    only error logs move out), and (2) importing stimtest is exactly
    what may have just failed (e.g. PyQt6 missing), so the crash
    handler has to work without it.
    """
    script_dir = Path(__file__).resolve().parent
    candidates = []

    # 1. <repo>/test/  — only if non-empty (active bench-testing).
    test_dir = script_dir / "test"
    if test_dir.is_dir():
        try:
            # ``any(iterdir())`` short-circuits on the first entry,
            # so this is O(1) for a non-empty directory.
            if any(test_dir.iterdir()):
                candidates.append(test_dir)
        except OSError:
            pass

    # 2. <repo>/  — the project root (script's parent).  Always
    #    writable for a dev checkout; the user can grep for the
    #    log next to README.md / pyproject.toml without hunting.
    candidates.append(script_dir)

    # 3. ~/.stimtest/  — per-user fallback, used only if the
    #    project tree refuses writes.  Better than %TEMP% (the log
    #    would get garbage-collected) and better than %APPDATA%
    #    (the user told us not to).
    candidates.append(Path.home() / ".stimtest")

    for d in candidates:
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".pulsar_write_probe"
            probe.write_text("x", encoding="utf-8")
            try:
                probe.unlink()
            except Exception:
                pass
            return d / "pulsar_launch_error.log"
        except Exception:
            continue
    # All candidates failed — return the script-dir path anyway so
    # the caller has *something* to print; the subsequent write_text
    # in _report_startup_error will fail silently if the dir really
    # is read-only.
    return script_dir / "pulsar_launch_error.log"


def _report_startup_error(exc: BaseException) -> None:
    """Write the traceback to disk and pop a message box.

    Best-effort: every step is wrapped in try/except because the
    whole point of this function is to give the user *something*
    even when everything else has gone wrong.  Specifically:

    * The log write tries ``<repo>/test/``, then ``<repo>/``, then
      ``~/.stimtest/`` (see :func:`_crash_log_path`) — never
      ``%APPDATA%`` or ``%TEMP%`` per user convention.
    * The message box uses tkinter (stdlib) so it works even when
      PyQt6 — the most likely root cause of a startup failure — is
      missing or broken.
    """
    log_path = _crash_log_path()
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    diag = (
        f"PULSAR failed to launch.\n"
        f"\n"
        f"Python:   {sys.version.split()[0]} ({sys.executable})\n"
        f"CWD:      {os.getcwd()}\n"
        f"Script:   {Path(__file__).resolve()}\n"
        f"sys.path[0]: {sys.path[0] if sys.path else '(empty)'}\n"
        f"\n"
        f"---- TRACEBACK ----\n"
        f"{tb_text}"
    )
    # Always print to stderr (visible if launched from a terminal).
    try:
        sys.stderr.write(diag)
        sys.stderr.flush()
    except Exception:
        pass
    # Mirror to disk so a double-click failure leaves a trail.
    try:
        log_path.write_text(diag, encoding="utf-8")
    except Exception:
        log_path = None  # type: ignore[assignment]
    # Pop a message box via tkinter (stdlib — works even when PyQt6
    # itself is the import that failed).
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        short = type(exc).__name__ + ": " + (str(exc) or "(no message)")
        body = short + "\n\n"
        # Targeted advice for the PyQt6-missing case — most common
        # cause of startup failure, especially on machines with
        # multiple Python installs.  The self-rescue path in main()
        # already tries to re-exec under a working interpreter; if
        # we reach this handler with a PyQt6 ImportError, it means
        # NO Python install on the system has PyQt6, so the user
        # has to install it themselves.
        is_pyqt6_missing = (
            isinstance(exc, ImportError)
            and "PyQt6" in (str(exc) or "")
        )
        if is_pyqt6_missing:
            body += (
                f"This Python ({sys.version.split()[0]}) is missing "
                f"PyQt6, and no other installed Python on this "
                f"machine has it either.\n\n"
                f"To fix, open a terminal and run:\n"
                f"    pip install PyQt6\n"
                f"OR if you have multiple Python versions, target the "
                f"one this app should use:\n"
                f"    py -3.13 -m pip install PyQt6\n\n"
            )
        if log_path is not None:
            body += f"Full traceback written to:\n{log_path}\n\n"
        body += "Run from a terminal (`python run_gui.py`) to see the full output."
        messagebox.showerror("PULSAR — startup failed", body)
        root.destroy()
    except Exception:
        # tkinter unavailable too (very unusual on a stock Python).
        # The stderr write and the log file are still the user's
        # diagnostic; nothing more we can do here.
        pass


def _find_python_with_pyqt6():
    """Hunt for a Python install that can ``import PyQt6``.

    Returns the launcher command (list of strings) to invoke that
    Python, or ``None`` if none of the candidates work.  Tried in
    order:

      1. ``pyw -3.13`` — the version this project bundles (the
         ``pyw.exe`` Python-launcher variant is windowed, no console).
      2. ``pyw -3.12``, ``-3.11``, ``-3.10`` — older but still
         supported.
      3. ``pyw -3`` — fallback to whatever the system's default 3.x
         points at.
      4. The console-attached ``py.exe`` equivalents of each above —
         used when ``pyw.exe`` isn't on PATH but ``py.exe`` is (some
         minimal Python installs.)

    Each candidate is probed with ``<py> -c "import PyQt6"`` and
    accepted on exit-0.  Probe timeout is 10 s per candidate so a
    pathologically slow launcher can't hang the rescue.

    This is the self-rescue path for the
    ``<repo>/test/pulsar_launch_error.log`` (or ``<repo>/pulsar_
    launch_error.log``) scenario where ``pythonw.exe`` on PATH
    resolves to a Python install (e.g. a newly-installed 3.14 from
    python.org or the Microsoft Store) that doesn't have PyQt6 in
    its ``site-packages``.  Without this rescue the user gets a
    silent failure (VBS launcher), a console-with-traceback failure
    (BAT launcher), or a tkinter error dialog — none of which let
    them ACTUALLY USE THE APP without manual intervention.
    """
    import subprocess
    candidates = []
    for ver in ("3.13", "3.12", "3.11", "3.10"):
        candidates.append(["pyw.exe", f"-{ver}"])
    candidates.append(["pyw.exe", "-3"])
    for ver in ("3.13", "3.12", "3.11", "3.10"):
        candidates.append(["py.exe", f"-{ver}"])
    candidates.append(["py.exe", "-3"])
    # Windows-only suppression of the brief console flash the launcher
    # would otherwise show during the probe subprocess.
    creation_flags = 0
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    for cmd in candidates:
        try:
            result = subprocess.run(
                cmd + ["-c", "import PyQt6"],
                capture_output=True,
                timeout=10,
                creationflags=creation_flags,
            )
            if result.returncode == 0:
                return cmd
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
    return None


def _relaunch_with(cmd: list, script: Path, argv_tail: list) -> int:
    """Re-exec ``script argv_tail`` under ``cmd`` (e.g.
    ``["pyw.exe", "-3.13"]``) and return an exit code suitable for
    the current process.

    Sets ``PULSAR_RESCUED=1`` in the child's environment so the child
    knows it was launched by the rescue path and DOESN'T recurse —
    if the child's PyQt6 import ALSO fails, it falls straight through
    to the crash handler instead of looping.

    Uses ``Popen`` (not ``run``) with no ``wait()`` so this process
    exits immediately while the child takes over the GUI — matches
    the detached-child semantics of the VBS launcher, so the user
    sees no extra console window or delay during the swap.
    """
    import subprocess
    env = dict(os.environ)
    env["PULSAR_RESCUED"] = "1"
    full_cmd = list(cmd) + [str(script)] + list(argv_tail)
    creation_flags = 0
    if sys.platform == "win32":
        # DETACHED_PROCESS + CREATE_NO_WINDOW so the child has no
        # console and is fully decoupled from this dying parent.
        creation_flags = (getattr(subprocess, "DETACHED_PROCESS", 0)
                          | getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        subprocess.Popen(full_cmd, env=env, creationflags=creation_flags,
                         close_fds=True)
        return 0
    except Exception:
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="PULSAR — GUI")
    parser.add_argument(
        "--simulate", action="store_true",
        help="Force simulator backends (no hardware required)",
    )
    parser.add_argument(
        "--save-dir", default=None,
        help="Default folder to save session data (defaults to ./data)",
    )
    parser.add_argument(
        "--skip-prereq-check", action="store_true",
        help="Don't show the PlexStim SDK warning dialog at startup.",
    )
    args = parser.parse_args()
    # ---- Self-rescue: PyQt6 missing in the current interpreter -----
    # The launcher (PULSAR.vbs / PULSAR.bat) picks whatever
    # ``pythonw.exe`` / ``python`` resolves to on PATH.  On a machine
    # with multiple Python installs (e.g. a freshly-installed 3.14
    # from python.org sitting next to the project-bundled 3.13), that
    # can land us on a Python that doesn't have PyQt6 in its
    # site-packages.  Symptom: silent failure on VBS, traceback on
    # BAT, ``ModuleNotFoundError: No module named 'PyQt6'`` in
    # ``pulsar_launch_error.log``.
    #
    # Rather than make the user diagnose ``where pythonw`` ↔ ``pip
    # show PyQt6``, do a quick preflight import here.  If it fails
    # AND we haven't already been re-launched, hunt for another
    # Python install (via the ``pyw -3.13`` / ``py -3.13`` Windows
    # launcher) that DOES have PyQt6, and re-exec there.
    #
    # The ``PULSAR_RESCUED`` env-var guard prevents infinite recursion:
    # if the rescued child's PyQt6 import ALSO fails (e.g. all installed
    # Pythons are missing PyQt6), we fall through to the regular
    # crash-handler path on the SECOND attempt rather than spinning
    # up subprocesses forever.
    if not os.environ.get("PULSAR_RESCUED"):
        try:
            import PyQt6  # noqa: F401  — preflight only
        except ImportError:
            alt = _find_python_with_pyqt6()
            if alt is not None:
                rc = _relaunch_with(alt, Path(__file__).resolve(),
                                    sys.argv[1:])
                # Successful detached re-launch — quit this process so
                # the child can take over.  If the spawn itself failed
                # (rc=1) we still fall through to the import below,
                # which will raise the same ImportError and land in
                # the outer crash handler.
                if rc == 0:
                    return 0
    # The launch import lives inside main() so an ImportError lands
    # in the outer try/except below and gets reported via the crash
    # handler — not silently killed by the double-click console
    # closing the instant the import fails.
    from stimtest.gui.main_window import launch
    return launch(simulate=args.simulate, save_dir=args.save_dir,
                  skip_prereq_check=args.skip_prereq_check)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        # argparse --help / explicit sys.exit() from main(): let it
        # through unchanged so the exit code is preserved.
        raise
    except BaseException as exc:  # noqa: BLE001 — we WANT to catch everything here
        _report_startup_error(exc)
        sys.exit(1)
