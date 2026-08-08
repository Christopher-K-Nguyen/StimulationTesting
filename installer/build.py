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
import glob
import os
import shlex
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

# RFC-3161 timestamp server — a timestamped signature stays valid after the
# signing cert expires.  Overridable via --timestamp-url / env.
DEFAULT_TIMESTAMP_URL = "http://timestamp.digicert.com"
# The two frozen app executables that end users actually run.  Both must be
# signed (the AV/SmartScreen heuristics flag the PyInstaller bootloader
# stub) — plus the final installer .exe (signed after Inno Setup).
APP_EXE_NAMES = ("StimulationTesting.exe", "StimulationTestingViewer.exe")


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
                "pyvisa", "sklearn", "pandas", "openpyxl",
                # scikit-learn / pandas transitive runtime deps that the
                # FROZEN app needs bundled (see the .spec's ``_safe`` block).
                "joblib", "threadpoolctl", "dateutil"):
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
        # ``--clean`` wipes ONLY the PyInstaller work dirs (build/ + dist/).
        # ``installer/Output/`` is DELIBERATELY NOT in this set — it is an
        # APPEND-ONLY version archive (operator: "include all versions of the
        # generated installers"): every ``PULSAR-Setup-<v>.exe`` is retained
        # across builds so Output keeps the full installer history, while only
        # the version-LESS delivery ZIPs (+ README / whitelist) are overwritten
        # to hold the LATEST build.  NEVER add "Output" to this clean list.
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


def run_inno(iscc: Path, app_version: str, *,
             with_interstellar: bool = False,
             with_cwru_profile: bool = False) -> None:
    """Compile the Inno Setup script with the version pinned to
    ``app_version`` (injected via ``/DAppVersion=…``).

    The .iss file guards its hardcoded fallback with ``#ifndef
    AppVersion``, so the value passed here always wins. This means
    a single bump of ``stimtest.__version__`` flows through
    ``[Setup] AppVersion``, ``OutputBaseFilename``, and the
    Add/Remove Programs version field with no hand-edits to the
    .iss required.

    ``with_interstellar`` passes ``/DWITH_INTERSTELLAR=1`` so the .iss
    appends ``-INTERSTELLAR`` to the installer filename — the
    experimental (bias-module-enabled) build gets a distinct artifact
    name so it's never confused with the public installer.
    """
    cmd = [str(iscc), f"/DAppVersion={app_version}"]
    # The .iss hard-errors (``#error MISSING_APP_ICON``) without
    # installer/app.ico unless BUILD_ALLOW_NO_ICON is set.  Auto-pass it
    # when the branded icon is absent so a dev / CWRU build compiles with
    # Inno's default icon instead of failing outright.  (Commit a real
    # installer/app.ico before a branded PUBLIC release to drop this.)
    if not (ROOT / "installer" / "app.ico").exists():
        cmd.append("/DBUILD_ALLOW_NO_ICON=1")
        print("[3/3] installer/app.ico missing → passing "
              "/DBUILD_ALLOW_NO_ICON=1 (fallback to Inno default icon)")
    if with_interstellar:
        cmd.append("/DWITH_INTERSTELLAR=1")
    if with_cwru_profile:
        # Bundles the CWRU login profile; the .iss names the artifact
        # ``PULSAR-Setup-<v>-CWRU.exe`` so it never collides with the public one.
        cmd.append("/DWITH_CWRU_PROFILE=1")
    cmd.append(str(ISS))
    print(f"[3/3] Inno Setup: {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=ROOT)


# ---------------------------------------------------------------------------
# Authenticode code-signing
# ---------------------------------------------------------------------------
# WHY: an UNSIGNED PyInstaller .exe is the #1 cause of antivirus / SmartScreen
# false positives — Windows treats it as an untrusted "unknown publisher" and
# the bootloader stub trips heuristic engines (it self-extracts + launches a
# bundled Python, which looks packer-like).  Signing the inner app exes AND
# the installer with an Authenticode certificate is the real fix.  Signing is
# OPT-IN (--sign): dev builds stay unsigned so you don't need a cert to test.


class SigningConfig:
    """Resolved signtool + certificate inputs for a signed build."""

    def __init__(self, signtool: Path, cert_args, timestamp_url: str):
        self.signtool = signtool
        self.cert_args = list(cert_args)       # signtool args that select the cert
        self.timestamp_url = timestamp_url


def find_signtool(hint: str | None) -> Path:
    """Locate ``signtool.exe`` (Windows SDK).  Honors --signtool, then PATH,
    then the newest Windows 10/11 SDK install."""
    if hint:
        p = Path(hint)
        if p.is_file():
            return p
        raise FileNotFoundError(f"--signtool path not found: {hint}")
    on_path = shutil.which("signtool")
    if on_path:
        return Path(on_path)
    pats = [
        r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe",
        r"C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe",
        r"C:\Program Files (x86)\Windows Kits\10\App Certification Kit\signtool.exe",
    ]
    found = []
    for pat in pats:
        found += glob.glob(pat)
    found = sorted(set(found), reverse=True)   # newest SDK bin\<ver>\ first
    if found:
        return Path(found[0])
    raise FileNotFoundError(
        "signtool.exe not found. Install the Windows 10/11 SDK (it ships "
        "signtool), or pass --signtool C:\\path\\to\\signtool.exe.")


def resolve_signing(args) -> SigningConfig:
    """Build a :class:`SigningConfig` from CLI / env, validating that a
    certificate source was actually provided.  Raises ``SystemExit`` with a
    clear message otherwise."""
    try:
        signtool = find_signtool(args.signtool)
    except FileNotFoundError as e:
        raise SystemExit(str(e))
    cert_args: list[str] = []
    if args.cert_thumbprint:
        # Cert lives in the Windows certificate store (institutional cert,
        # EV token, or an imported .pfx) — selected by its SHA-1 thumbprint.
        cert_args += ["/sha1", args.cert_thumbprint.replace(" ", "")]
    elif args.cert_file:
        cert_args += ["/f", args.cert_file]
        if args.cert_password:
            cert_args += ["/p", args.cert_password]
    if args.signtool_extra:
        # Passthrough for Azure Trusted Signing (/dlib … /dmdf …) or any
        # custom signtool invocation.
        cert_args += shlex.split(args.signtool_extra)
    if not cert_args:
        raise SystemExit(
            "--sign was requested but no certificate source was given.\n"
            "Provide ONE of:\n"
            "  --cert-thumbprint <SHA1>     (cert in the Windows cert store; "
            "recommended)\n"
            "  --cert-file <path.pfx> [--cert-password <pw>]\n"
            "  --signtool-extra \"<args>\"   (e.g. Azure Trusted Signing "
            "/dlib … /dmdf …)\n"
            "Env equivalents: PULSAR_SIGN_THUMBPRINT / PULSAR_SIGN_PFX "
            "(+ PULSAR_SIGN_PFX_PASSWORD) / PULSAR_SIGN_EXTRA.")
    return SigningConfig(signtool, cert_args, args.timestamp_url)


def sign_files(cfg: SigningConfig, files) -> None:
    """Authenticode-sign each file (SHA-256 + RFC-3161 timestamp), then
    verify.  Skips files that don't exist (defensive)."""
    for f in files:
        f = Path(f)
        if not f.is_file():
            print(f"      (skip sign — not found: {f})")
            continue
        cmd = [str(cfg.signtool), "sign", "/fd", "SHA256",
               "/tr", cfg.timestamp_url, "/td", "SHA256",
               *cfg.cert_args, str(f)]
        # Echo without leaking a /p password.
        shown = []
        redact = False
        for tok in cmd:
            shown.append("***" if redact else tok)
            redact = (tok == "/p")
        print(f"  sign: {f.name}")
        print("        " + " ".join(shown))
        subprocess.check_call(cmd)
        subprocess.check_call(
            [str(cfg.signtool), "verify", "/pa", str(f)])
    print(f"      OK — signed + verified {len([1 for f in files if Path(f).is_file()])} file(s).")


# ---------------------------------------------------------------------------
# Release packaging — bundle the installer(s) with the manual + a README ZIP
# ---------------------------------------------------------------------------
# WHY: collaborators (CWRU, UofU) should get ONE self-contained download —
# installer + full docs — not a bare .exe.  Per the operator's standing rule
# ("always put the installer in a ZIP with the manual and README"), every
# build assembles ``PULSAR.zip`` = installer + README.txt + the HTML manual,
# and (when the CWRU installer was built) ``PULSAR-CWRU.zip`` = + the IT
# whitelist note.  The ZIP filename is DELIBERATELY VERSION-LESS (operator:
# "exclude the version in the ZIP file … easier for collaborators to keep the
# same OneDrive link") — the stable name means a new build overwrites the same
# ZIP so the shared link never changes.  The VERSION still lives INSIDE: the
# installer ``PULSAR-Setup-<v>.exe`` name + the README/whitelist, which are
# RE-RENDERED here with the current version and freshly-computed SHA-256 so they
# can never drift.

MANUAL_HTML = ROOT / "installer" / "PULSAR-POLARIS-Manual.html"


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _make_folder(folder: Path, files) -> None:
    """Mirror the delivery set into a PLAIN FOLDER beside the ZIP.

    Operator: "Besides making the installers in ZIP, have it in folders in case
    the ZIP file changes with every change."  A ZIP is a single opaque blob —
    re-uploading it replaces the whole file on every build, so anyone syncing
    or linking it sees a full re-download and any in-progress download breaks.
    A folder updates FILE BY FILE: the shared link points at a directory whose
    contents change in place, so only the installer .exe actually re-syncs.

    Same version-LESS naming rule as the ZIP (see ``package_release``) — the
    folder name must stay stable across releases or the shared link dies.
    Stale members from a previous build are removed so the folder never
    accumulates an old installer alongside the new one.
    """
    folder.mkdir(parents=True, exist_ok=True)
    keep = {Path(f).name for f in files}
    for old in folder.iterdir():
        if old.is_file() and old.name not in keep:
            try:
                old.unlink()
            except OSError as exc:
                print(f"[package] ⚠ could not remove stale {old.name}: {exc}")
    for f in files:
        shutil.copy2(str(f), str(folder / Path(f).name))


def _make_zip(zip_path: Path, files) -> None:
    import zipfile
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(str(f), arcname=Path(f).name)


def _render_readme(app_version: str, pub_hash, cwru_hash, mon_year: str) -> str:
    cwru_contents = ("\n  (CWRU build also includes: PULSAR-CWRU-IT-whitelist-request.txt "
                     "— for your IT team)" if cwru_hash else "")
    cwru_hash_line = (f"\n    CWRU build:    PULSAR-Setup-{app_version}-CWRU.exe"
                      f"\n      {cwru_hash}" if cwru_hash else "")
    return f"""PULSAR & POLARIS — READ ME FIRST
================================

WHAT'S IN THIS ZIP
  - PULSAR-Setup-{app_version}.exe        the installer
  - PULSAR-POLARIS-Manual.html      the full user manual (open in any web browser)
  - README.txt                      this file{cwru_contents}

WHAT IT IS
  PULSAR is desktop software that characterizes neural-stimulation microelectrodes
  by driving a Plexon PlexStim 2.0 stimulator and a Tektronix oscilloscope over USB.
  POLARIS is its companion viewer for reviewing saved sessions. Normal operation
  needs no internet connection.

QUICK START
  1. Double-click PULSAR-Setup-{app_version}.exe.
  2. If Windows SmartScreen shows a blue "Windows protected your PC" box, click
     "More info" -> "Run anyway". This is expected for a new, unsigned in-house
     research tool with no download reputation — it is NOT a virus.
  3. Follow the installer prompts, then launch PULSAR (and POLARIS) from the
     Start Menu.
  4. No hardware attached? Tick "Use simulator" on the Setup tab to explore the app.

FULL DOCUMENTATION
  Open PULSAR-POLARIS-Manual.html in any web browser for the complete manual —
  annotated screenshots of every screen, all the settings, how to run each
  experiment, how to read the metrics, and troubleshooting. To make a printed
  copy, open it and use Ctrl+P -> "Save as PDF".

IF ANTIVIRUS BLOCKS OR QUARANTINES THE INSTALLER
  PyInstaller-packaged, unsigned applications commonly trip heuristic antivirus.
  If Windows Defender blocks it outright, give your IT department the installer's
  SHA-256:

    Public build:  PULSAR-Setup-{app_version}.exe
      {pub_hash or '(not produced in this build)'}{cwru_hash_line}

  Verify a download yourself in PowerShell:
    Get-FileHash .\\PULSAR-Setup-{app_version}.exe -Algorithm SHA256

NOTES
  - Installing over an existing PULSAR upgrades it IN PLACE (same folder, one
    Add/Remove Programs entry) and preserves your settings, calibration, and
    saved data. Close PULSAR and POLARIS before installing.
  - Save experiment data to a NON-OneDrive folder (e.g. C:\\PULSAR_data). OneDrive
    can intermittently lock files mid-write, which appears as a
    "[WinError 5] Access is denied" error when PULSAR saves .npz / .xlsx.

Solzbacher Microsystems Laboratory
Department of Electrical & Computer Engineering, University of Utah
Version {app_version} - {mon_year}
"""


def _render_whitelist(app_version: str, pub_hash, cwru_hash,
                      cwru_size_mb: float, date_str: str) -> str:
    return f"""To:      CWRU Information Security / IT Help Desk
From:    Christopher K. Nguyen — Solzbacher Lab, University of Utah
         [add your @utah.edu email + lab/PI name]
Re:      Whitelist request — PULSAR (research instrument-control application)
Date:    {date_str}


Summary
-------
A collaborator in your department is unable to install a small research
application, "PULSAR," because Windows Defender / SmartScreen flags the
installer as malware ("the file contains a virus or potentially unwanted
software"). This is a FALSE POSITIVE. I am the developer and am writing to
provide the details your team needs to verify the file and allow it.


What the software is
--------------------
PULSAR is open, in-house scientific software that controls a benchtop
neural-stimulation test setup — a Plexon PlexStim 2.0 stimulator and a
Tektronix oscilloscope, both attached over USB — to characterize
microelectrodes. It ships with a companion data viewer ("POLARIS"). It is
used only for laboratory instrument control and offline data analysis.


Why it is being flagged (false positive)
-----------------------------------------
The application is packaged with PyInstaller, which bundles a Python
runtime into a self-extracting executable. That packaging pattern is a
well-known and very common source of heuristic antivirus false positives,
independent of what the program actually does. The current build is also
not yet code-signed, so Windows treats it as an "unknown publisher" with no
download reputation. A code-signing certificate is being obtained through
University of Utah IT to resolve this permanently; in the meantime I am
requesting a manual allow.


Security posture
----------------
- The application controls USB-attached lab instruments and reads/writes
  local data files only. During normal experiment operation it makes NO
  outbound network connections and contains no telemetry or analytics.
- The only network activity is OPTIONAL and user-initiated: the installer
  can offer to download standard hardware drivers (National Instruments
  NI-VISA, Plexon Stim-2), and an optional analysis component can download
  a model file — only if the user explicitly chooses those steps.
- Source code and build scripts are available for review on request.


File identifiers (for whitelisting)
-----------------------------------
Product:    PULSAR / POLARIS  (package name: StimulationTesting)
Version:    {app_version}
Publisher:  Solzbacher Lab, University of Utah
Installer:  PULSAR-Setup-{app_version}-CWRU.exe   (~{cwru_size_mb:.0f} MB)

SHA-256 (CWRU build):
  {cwru_hash}

SHA-256 (public build, PULSAR-Setup-{app_version}.exe):
  {pub_hash}

VirusTotal scan: [upload the installer at https://www.virustotal.com and
  paste the permalink here — it will show only a few heuristic engines
  flag it, not the major vendors]


Request
-------
Please add a file/hash-based allow for the SHA-256 above so the installer
can run, OR advise the preferred process for approving internally developed
research software. Once the application is code-signed (in progress), you
will be able to allow it by trusted publisher, which is more durable.

I'm happy to provide the source, a build manifest, or a screen-share walk-
through. Thank you for your help.

Christopher K. Nguyen
Solzbacher Lab, University of Utah
[email] · [phone]
"""


def package_release(app_version: str, out_dir: Path) -> None:
    """Assemble the delivery ZIP(s) AND a matching plain FOLDER: installer +
    README + manual (+ the CWRU whitelist note).  Re-renders the README/whitelist to the current version
    with freshly-computed SHA-256 so they never drift from the actual .exe.
    Runs AFTER Inno Setup (and after signing, so the signed .exe is zipped).
    Skips gracefully if no installer for this version exists; warns loudly if
    the manual is missing (a release ZIP should always carry it)."""
    import datetime
    pub_exe = out_dir / f"PULSAR-Setup-{app_version}.exe"
    cwru_exe = out_dir / f"PULSAR-Setup-{app_version}-CWRU.exe"
    if not pub_exe.exists() and not cwru_exe.exists():
        print("[package] no installer for this version — skipping ZIP packaging.")
        return
    pub_hash = _sha256(pub_exe) if pub_exe.exists() else None
    cwru_hash = _sha256(cwru_exe) if cwru_exe.exists() else None
    today = datetime.date.today()

    readme = out_dir / "README.txt"
    readme.write_text(_render_readme(app_version, pub_hash, cwru_hash,
                                     today.strftime("%B %Y")), encoding="utf-8")

    if not MANUAL_HTML.is_file():
        print(f"[package] ⚠ manual not found at {MANUAL_HTML} — the ZIP will OMIT "
              f"it.  Regenerate the manual before a real release.")
    manual_part = [MANUAL_HTML] if MANUAL_HTML.is_file() else []

    # CWRU manual variant: the source manual keeps the CWRU-only restricted
    # shapes (Speedbumps / Bowtie / Halfpipe) commented out inside a
    # ``<!-- CWRU-ONLY:START … CWRU-ONLY:END -->`` block, so the PUBLIC manual
    # never shows them.  For the CWRU ZIP we strip just the two delimiter
    # strings, which reveals those rows.  No-op (identical to public) if the
    # markers aren't present, so it's safe across manual revisions.
    def _cwru_manual() -> list:
        if not (cwru_exe.exists() and MANUAL_HTML.is_file()):
            return manual_part
        src = MANUAL_HTML.read_text(encoding="utf-8")
        cwru_src = (src.replace("<!-- CWRU-ONLY:START", "")
                       .replace("CWRU-ONLY:END -->", ""))
        if cwru_src == src:
            return manual_part  # no markers — same file
        cwru_manual = out_dir / "PULSAR-POLARIS-Manual.html"
        cwru_manual.write_text(cwru_src, encoding="utf-8", newline="\n")
        return [cwru_manual]

    if pub_exe.exists():
        # Version-LESS ZIP name (operator: keep the OneDrive link stable across
        # releases) — the version is inside, on the installer .exe + README.
        zp = out_dir / "PULSAR.zip"
        members = [pub_exe, readme] + manual_part
        _make_zip(zp, members)
        print(f"[package] {zp.name}  ({zp.stat().st_size/1e6:.0f} MB): "
              + ", ".join(Path(m).name for m in members))
        fp = out_dir / "PULSAR"
        _make_folder(fp, members)
        print(f"[package] {fp.name}/  (folder mirror): "
              + ", ".join(Path(m).name for m in members))

    if cwru_exe.exists():
        wl = out_dir / "PULSAR-CWRU-IT-whitelist-request.txt"
        wl.write_text(_render_whitelist(
            app_version, pub_hash or "(public build not produced)", cwru_hash,
            cwru_exe.stat().st_size / 1e6, today.isoformat()), encoding="utf-8")
        zc = out_dir / "PULSAR-CWRU.zip"
        members = [cwru_exe, readme, wl] + _cwru_manual()
        _make_zip(zc, members)
        print(f"[package] {zc.name}  ({zc.stat().st_size/1e6:.0f} MB): "
              + ", ".join(Path(m).name for m in members))
        fc = out_dir / "PULSAR-CWRU"
        _make_folder(fc, members)
        print(f"[package] {fc.name}/  (folder mirror): "
              + ", ".join(Path(m).name for m in members))


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
    # ---- INTERSTELLAR experimental build (opt-in) ----
    # The interpulse-bias module is OFF in the DEFAULT (public) build.
    # Pass --with-interstellar to produce the EXPERIMENTAL installer that
    # bundles the runtime sentinel (so the frozen app shows the bias UI)
    # AND names the artifact ``PULSAR-Setup-<v>-INTERSTELLAR.exe``.
    p.add_argument("--with-interstellar", action="store_true",
                   default=(os.environ.get(
                       "PULSAR_WITH_INTERSTELLAR", "").strip().lower()
                       in ("1", "true", "yes", "on")),
                   help="Build the experimental installer WITH the "
                        "INTERSTELLAR interpulse-bias module enabled "
                        "(default: off — the public build hides it).")
    # ---- CWRU collaborator build (opt-in) ----
    p.add_argument("--with-cwru-profile", action="store_true",
                   default=(os.environ.get(
                       "PULSAR_WITH_CWRU_PROFILE", "").strip().lower()
                       in ("1", "true", "yes", "on")),
                   help="ALSO build the CWRU installer (bundles the CWRU login "
                        "profile → PULSAR-Setup-<v>-CWRU.exe) and produce its "
                        "-CWRU.zip with the IT whitelist note.")
    # ---- Release packaging (default ON) ----
    p.add_argument("--skip-package", action="store_true",
                   help="Skip assembling the delivery ZIP(s) "
                        "(installer + README + manual) after Inno Setup.")
    # ---- Authenticode code-signing (opt-in) ----
    p.add_argument("--sign", action="store_true",
                   help="Authenticode-sign the app exes + the installer "
                        "(needs a certificate — see --cert-* below).")
    p.add_argument("--signtool", default=os.environ.get("PULSAR_SIGNTOOL"),
                   help="Path to signtool.exe (default: auto-detect from the "
                        "Windows SDK / PATH).")
    p.add_argument("--cert-thumbprint",
                   default=os.environ.get("PULSAR_SIGN_THUMBPRINT"),
                   help="SHA-1 thumbprint of a cert in the Windows cert "
                        "store (institutional cert / EV token / imported "
                        ".pfx). Recommended.")
    p.add_argument("--cert-file", default=os.environ.get("PULSAR_SIGN_PFX"),
                   help="Path to a .pfx/.p12 certificate file.")
    p.add_argument("--cert-password",
                   default=os.environ.get("PULSAR_SIGN_PFX_PASSWORD"),
                   help="Password for --cert-file (prefer the env var "
                        "PULSAR_SIGN_PFX_PASSWORD so it isn't in shell "
                        "history).")
    p.add_argument("--signtool-extra",
                   default=os.environ.get("PULSAR_SIGN_EXTRA"),
                   help="Extra signtool args, e.g. Azure Trusted Signing "
                        "'/dlib <Azure.CodeSigning.Dlib.dll> /dmdf "
                        "<metadata.json>'.")
    p.add_argument("--timestamp-url",
                   default=os.environ.get("PULSAR_SIGN_TIMESTAMP",
                                          DEFAULT_TIMESTAMP_URL),
                   help=f"RFC-3161 timestamp server "
                        f"(default {DEFAULT_TIMESTAMP_URL}).")
    args = p.parse_args()

    # Resolve signing inputs UP FRONT (fail fast before a long PyInstaller
    # run if --sign was requested without a usable certificate).
    sign_cfg = resolve_signing(args) if args.sign else None

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
    # Propagate the INTERSTELLAR opt-in to the PyInstaller subprocess via
    # the env var the .spec reads (it bundles the runtime sentinel when
    # set).  Setting it here — after arg parsing — keeps the single
    # source of truth in ``--with-interstellar`` / PULSAR_WITH_INTERSTELLAR.
    if args.with_interstellar:
        os.environ["PULSAR_WITH_INTERSTELLAR"] = "1"
        print("      INTERSTELLAR experimental build: bias module ENABLED.")
    else:
        # Ensure a stale env value from the shell doesn't silently enable
        # it when the flag wasn't passed.
        os.environ.pop("PULSAR_WITH_INTERSTELLAR", None)
    run_pyinstaller(clean=args.clean)
    # Sign the inner app exes BEFORE Inno packages them, so the executables
    # that land on disk after install are signed too — not just the
    # installer.  (Inno Setup will bundle the already-signed exes.)
    if sign_cfg is not None:
        print("[sign] app executables…")
        sign_files(sign_cfg, [DIST_FOLDER / n for n in APP_EXE_NAMES])
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
    run_inno(iscc, app_version, with_interstellar=args.with_interstellar)
    # Optionally also build the CWRU collaborator installer (distinct artifact
    # name -CWRU.exe), so the packaging step below can produce its -CWRU.zip.
    if args.with_cwru_profile:
        run_inno(iscc, app_version, with_interstellar=args.with_interstellar,
                 with_cwru_profile=True)
    out = ROOT / "installer" / "Output"
    # Sign the produced installer(s) — this is the file end users download
    # and double-click, so it's the most important one to sign.
    if sign_cfg is not None and out.exists():
        print("[sign] installer(s)…")
        sign_files(sign_cfg, sorted(out.glob(f"PULSAR-Setup-{app_version}*.exe")))
    # Assemble the delivery ZIP(s): installer + README + manual (+ CWRU
    # whitelist).  Runs after signing so the SIGNED installer is what ships.
    if not args.skip_package and out.exists():
        package_release(app_version, out)
    if out.exists():
        # Output/ is an APPEND-ONLY version archive (operator: "include all
        # versions of the generated installers"); the version-LESS ZIPs hold
        # the LATEST build.  Report the two distinctly so it's clear the old
        # installers are retained on purpose.
        setups = sorted(out.glob("PULSAR-Setup-*.exe"))
        if setups:
            print(f"\n[archive] Output/ retains {len(setups)} installer "
                  f"version(s) (append-only history):")
            for setup in setups:
                mark = "   ← this build" if f"-{app_version}" in setup.name \
                    else ""
                print(f"    {setup.name:<40} "
                      f"({setup.stat().st_size/1e6:5.0f} MB){mark}")
        zips = sorted(out.glob("*.zip"))
        if zips:
            print(f"[latest]  delivery ZIP(s) (version-less, now hold "
                  f"v{app_version}):")
            for z in zips:
                print(f"    {z.name:<40} ({z.stat().st_size/1e6:5.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
