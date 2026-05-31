#!/usr/bin/env python
"""End-to-end build script for the Windows installer.

Pipeline:
    0. Pre-flight: confirm PyInstaller is importable, the vendored
       PlexStim DLL is present, and the project's runtime requirements
       are installed. Bail with a clear message instead of producing
       a silently-broken bundle.
    1. Run PyInstaller against ``installer/StimulationTesting.spec`` to
       produce ``installer/dist/StimulationTesting/`` containing both
       ``StimulationTesting.exe`` and ``StimulationTestingViewer.exe``
       plus the shared binary deps.
    2. (Optional) Smoke-test the produced GUI EXE with
       ``--simulate --skip-prereq-check`` to confirm imports / widget
       construction don't blow up at runtime; abort if it crashes.
    3. Invoke Inno Setup's compiler (``ISCC.exe``) on
       ``installer/StimulationTesting.iss`` to package that folder into
       ``installer/Output/PULSAR-Setup-<version>.exe``. (The .iss
       filename retains the legacy "StimulationTesting" name; only
       the produced installer .exe carries the new PULSAR brand.)

Both PyInstaller and Inno Setup must be available on PATH (or pointed to
via ``--iscc``). Run from the project root:

    python installer/build.py
    python installer/build.py --skip-installer    # only PyInstaller
    python installer/build.py --skip-smoke        # don't launch the EXE after build
    python installer/build.py --iscc "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "installer" / "StimulationTesting.spec"
ISS = ROOT / "installer" / "StimulationTesting.iss"
DIST_FOLDER = ROOT / "installer" / "dist" / "StimulationTesting"
PLEXSTIM_BIN = ROOT / "stimtest" / "hardware" / "pyplexstim" / "bin"
INIT_PY = ROOT / "stimtest" / "__init__.py"
PYPROJECT = ROOT / "pyproject.toml"


def read_app_version() -> str:
    """Pull ``__version__`` out of ``stimtest/__init__.py`` without
    importing the package.

    Importing would drag in PyQt6 + numpy + scipy etc. just to read
    a string constant; a tiny regex scan is faster and works even on
    a build machine where the runtime stack isn't fully installed.
    Falls back to ``"0.0.0"`` if the line can't be parsed (the .iss
    has its own ``#ifndef AppVersion`` fallback so the build still
    completes — but the printed warning makes the failure visible).
    """
    import re
    try:
        text = INIT_PY.read_text(encoding="utf-8")
    except OSError as e:
        print(f"WARNING: could not read {INIT_PY}: {e}")
        return "0.0.0"
    m = re.search(r'^__version__\s*=\s*[\'\"]([^\'\"]+)[\'\"]',
                  text, re.MULTILINE)
    if not m:
        print(f"WARNING: __version__ not found in {INIT_PY}; "
              f"falling back to 0.0.0")
        return "0.0.0"
    return m.group(1)


def assert_version_consistency(app_version: str) -> None:
    """Verify ``pyproject.toml``'s ``version =`` matches ``app_version``.

    Audit finding #32: the project has THREE locations that need to
    stay in sync — ``stimtest/__init__.py:__version__`` (the
    canonical), ``pyproject.toml:version``, and the ``.iss``
    ``#ifndef AppVersion`` fallback. ``build.py`` already overrides
    the .iss fallback at compile time via ``ISCC /DAppVersion=…``,
    but ``pyproject.toml`` is hand-synced; a forgotten bump there
    silently ships a wheel / sdist with the wrong version. This
    guard catches that on every build: if the two disagree, abort
    with a clear message naming the file that needs editing.

    The ``.iss`` fallback is intentionally NOT checked here — it's
    a build-time-only safety net, never the source of truth, and
    drift there is harmless because the override always wins.
    """
    # Parse pyproject.toml properly instead of regex-scanning — a
    # comment-prefixed ``version = …`` line, a nested table key, or
    # an inline-table assignment could all evade the regex.  tomllib
    # has been stdlib since Python 3.11; build hosts must use 3.11+
    # (the runtime itself is 3.13+).
    try:
        import tomllib  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover — pre-3.11 build host
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            print(f"WARNING: tomllib not available (Python 3.11+ "
                  f"required, or `pip install tomli`); skipping "
                  f"version-consistency check.")
            return
    try:
        with PYPROJECT.open("rb") as f:
            data = tomllib.load(f)
    except OSError as e:
        print(f"WARNING: could not read {PYPROJECT}: {e}; "
              f"skipping version-consistency check.")
        return
    except Exception as e:
        print(f"WARNING: {PYPROJECT} is not valid TOML ({e}); "
              f"skipping version-consistency check.")
        return
    pyproject_version = ((data.get("project") or {}).get("version")
                         or None)
    if not pyproject_version:
        print(f"WARNING: [project].version not found in {PYPROJECT}; "
              f"skipping version-consistency check.")
        return
    if pyproject_version != app_version:
        raise SystemExit(
            f"Version drift detected:\n"
            f"    stimtest/__init__.py: {app_version!r}\n"
            f"    pyproject.toml:       {pyproject_version!r}\n"
            f"Edit pyproject.toml's ``version = …`` to match "
            f"``__version__`` and re-run the build."
        )
    print(f"      OK — pyproject.toml version {pyproject_version!r} "
          f"matches __version__.")


def preflight() -> None:
    """Sanity-check the build environment before launching PyInstaller.

    Catches the three failure modes that produce a silently-broken
    installer: PyInstaller not installed, the vendored PlexStim DLL
    missing (no fallback for end users without their own SDK), and a
    missing core runtime dependency that PyInstaller would happily
    skip but the runtime needs.
    """
    print("[0/3] Pre-flight checks…")
    # 1. PyInstaller importable.
    try:
        import PyInstaller  # noqa: F401
    except ImportError as e:
        raise SystemExit(
            "PyInstaller is not installed in the current Python "
            "environment.\n"
            "Install it with:\n"
            "    python -m pip install pyinstaller\n"
            f"(Original error: {e})"
        )
    # 2. Vendored PlexStim DLLs present.  We bundle both bitnesses;
    # ``pyplexstimlib.py`` picks PlexStim64.dll for a 64-bit Python
    # and PlexStim.dll for a 32-bit Python at runtime via
    # ``platform.architecture()``.  The frozen GUI is currently
    # 64-bit, so PlexStim64.dll is the one that MUST be present —
    # if a user only copied PlexStim.dll into bin/ the previous
    # "any *.dll" check would pass and the runtime would fail with
    # a confusing path error after install.
    have_64 = (PLEXSTIM_BIN / "PlexStim64.dll").is_file()
    have_32 = (PLEXSTIM_BIN / "PlexStim.dll").is_file()
    target_bits = "64" if sys.maxsize > 2**32 else "32"
    required = "PlexStim64.dll" if target_bits == "64" else "PlexStim.dll"
    if not (PLEXSTIM_BIN.is_dir() and (have_64 if target_bits == "64" else have_32)):
        raise SystemExit(
            f"Vendored PlexStim DLL missing for {target_bits}-bit "
            f"target:  {PLEXSTIM_BIN / required} not found.\n"
            f"Copy {required} into that folder before building "
            f"(the SDK ships it under its 'bin' directory).\n"
            f"Status: PlexStim64.dll present={have_64}, "
            f"PlexStim.dll present={have_32}."
        )
    # 3. Spot-check core runtime deps so a missing one fails here
    # instead of inside PyInstaller's hookloop.
    missing = []
    for mod in ("numpy", "scipy", "PyQt6", "pyqtgraph", "matplotlib",
                "pyvisa", "sklearn", "pandas", "openpyxl"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        raise SystemExit(
            f"Missing runtime dependencies: {', '.join(missing)}.\n"
            f"Install them with:\n"
            f"    python -m pip install -r requirements.txt"
        )
    print("      OK — PyInstaller present, DLLs vendored, "
          "runtime deps importable.")


def run_pyinstaller(clean: bool) -> None:
    if clean:
        for sub in ("build", "dist"):
            p = ROOT / "installer" / sub
            if p.exists():
                print(f"  removing {p}")
                shutil.rmtree(p, ignore_errors=True)
    cmd = [
        sys.executable, "-m", "PyInstaller", str(SPEC),
        "--workpath", str(ROOT / "installer" / "build"),
        "--distpath", str(ROOT / "installer" / "dist"),
        "--noconfirm",
    ]
    print("[1/3] PyInstaller:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=ROOT)
    if not DIST_FOLDER.exists():
        raise RuntimeError(f"Expected output at {DIST_FOLDER}")
    n_files = sum(1 for _ in DIST_FOLDER.rglob("*") if _.is_file())
    n_bytes = sum(f.stat().st_size for f in DIST_FOLDER.rglob("*") if f.is_file())
    print(f"      OK — {n_files} files, {n_bytes/1e6:.1f} MB "
          f"in {DIST_FOLDER}")


def smoke_test() -> None:
    """Launch the frozen GUI in simulator mode and kill it after a moment.

    A clean exit (or being killed by us after the timeout) means the
    bundle's import graph is intact and the main window constructed
    without crashing. Anything that ImportErrors or QExceptions out
    will show up as a non-zero exit code.
    """
    exe = DIST_FOLDER / "StimulationTesting.exe"
    if not exe.is_file():
        raise RuntimeError(f"Expected EXE at {exe}")
    print(f"[2/3] Smoke test: {exe.name} --simulate --skip-prereq-check …")
    proc = subprocess.Popen(
        [str(exe), "--simulate", "--skip-prereq-check"],
        cwd=DIST_FOLDER,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 6.0
    # If the EXE crashes early, ``poll()`` returns the exit code; we
    # surface that. If it stays alive past the deadline, treat that as
    # success and terminate it.
    while time.time() < deadline:
        rc = proc.poll()
        if rc is not None:
            output = (proc.stdout.read() or b"").decode("utf-8", "replace")
            raise SystemExit(
                f"Smoke test failed: GUI exited with code {rc} after "
                f"{time.time() - (deadline - 6.0):.1f}s.\n"
                f"--- stdout/stderr ---\n{output}"
            )
        time.sleep(0.25)
    print(f"      OK — EXE stayed up for 6 s; terminating.")
    proc.terminate()
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.kill()


def find_iscc(hint: str | None) -> Path:
    """Locate ``ISCC.exe`` either from the CLI flag or PATH or default
    install path."""
    if hint:
        p = Path(hint)
        if p.is_file():
            return p
        raise FileNotFoundError(f"--iscc path not found: {hint}")
    on_path = shutil.which("ISCC")
    if on_path:
        return Path(on_path)
    for default in (
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ):
        if default.is_file():
            return default
    raise FileNotFoundError(
        "ISCC.exe not found. Install Inno Setup 6 from "
        "https://jrsoftware.org/isinfo.php and rerun."
    )


def run_inno(iscc: Path, app_version: str) -> None:
    """Compile the Inno Setup script with the version pinned to
    ``app_version`` (injected via ``/DAppVersion=…``).

    The .iss file guards its hardcoded fallback with ``#ifndef
    AppVersion``, so the value passed here always wins. This means
    a single bump of ``stimtest.__version__`` flows through
    ``[Setup] AppVersion``, ``OutputBaseFilename``, and the
    Add/Remove Programs version field with no hand-edits to the
    .iss required.
    """
    cmd = [str(iscc), f"/DAppVersion={app_version}", str(ISS)]
    print(f"[3/3] Inno Setup: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=ROOT)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--clean", action="store_true",
                   help="Wipe installer/build and installer/dist first.")
    p.add_argument("--skip-installer", action="store_true",
                   help="Only run PyInstaller; skip Inno Setup compilation.")
    p.add_argument("--skip-smoke", action="store_true",
                   help="Skip the post-build smoke test of the frozen GUI.")
    p.add_argument("--skip-preflight", action="store_true",
                   help="Skip the pre-build dependency / DLL checks.")
    p.add_argument("--iscc", default=None,
                   help="Path to ISCC.exe (Inno Setup compiler).")
    args = p.parse_args()

    app_version = read_app_version()
    print(f"App version (from stimtest/__init__.py): {app_version}")
    # Audit #32 — assert pyproject.toml's hand-synced ``version =``
    # matches ``__version__``. Bails out with a clear message if
    # they drift, so we never ship a wheel / sdist with the wrong
    # version. Always runs (even with --skip-preflight) because
    # the check is essentially free and version drift is the kind
    # of bug that's invisible until someone runs ``pip show``.
    assert_version_consistency(app_version)
    if not args.skip_preflight:
        preflight()
    run_pyinstaller(clean=args.clean)
    if not args.skip_smoke:
        try:
            smoke_test()
        except SystemExit as e:
            # Smoke failure should bubble up — a broken bundle going
            # into Inno Setup is the worst-case outcome we're guarding
            # against.
            print(str(e))
            return 1
    if args.skip_installer:
        print("Skipping Inno Setup step (--skip-installer).")
        return 0
    iscc = find_iscc(args.iscc)
    run_inno(iscc, app_version)
    out = ROOT / "installer" / "Output"
    if out.exists():
        for setup in sorted(out.glob("*.exe")):
            size_mb = setup.stat().st_size / 1e6
            print(f"  installer: {setup} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
