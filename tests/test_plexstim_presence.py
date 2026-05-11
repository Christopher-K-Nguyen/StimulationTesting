"""Tests for the stimulator-presence detector.

We can't actually plug a Plexon stimulator into CI, so the tests
validate the function's three-state contract by mocking the
PowerShell subprocess. The real-world signal we depend on
(``Get-PnpDevice -PresentOnly`` returning a friendly-name match)
is exercised on a developer machine separately when the user
runs the GUI.

Three states the probe must distinguish:
  * ``True``  — at least one PowerShell-output line matches one
    of the device-name hints.
  * ``False`` — PowerShell ran successfully but emitted nothing.
  * ``None``  — non-Windows host, missing PowerShell, timeout, or
    a non-zero return code (we can't tell either way).

Each test pins a single failure or success path so a regression
in one branch is unambiguously located.
"""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest


def test_non_windows_returns_none(monkeypatch):
    """``None`` is the canonical "couldn't tell" state — non-Windows
    must never claim a Plexon device is or isn't present."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is None


def test_powershell_match_returns_true(monkeypatch):
    """A PowerShell stdout line containing 'PlexStim' flips the
    probe to ``True``."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="PlexStim Stimulator V2\n",
            stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is True


def test_powershell_empty_stdout_returns_false(monkeypatch):
    """PowerShell ran clean and matched no devices → ``False``.
    This is the "ran the probe; the cable isn't plugged in" case
    — explicitly distinct from the ``None`` ambiguity."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", fake_run)
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is False


def test_powershell_nonzero_returncode_returns_none(monkeypatch):
    """A non-zero return code makes the probe indeterminate."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="error")
    monkeypatch.setattr(subprocess, "run", fake_run)
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is None


def test_powershell_missing_binary_returns_none(monkeypatch):
    """A box without PowerShell on PATH (rare but possible on
    stripped-down Windows installs) → ``None`` rather than False."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("powershell")
    monkeypatch.setattr(subprocess, "run", fake_run)
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is None


def test_powershell_timeout_returns_none(monkeypatch):
    """A loaded box / hung WMI service → ``None`` (unknown), not
    a false 'absent' claim."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="powershell", timeout=2.0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is None


def test_powershell_oserror_returns_none(monkeypatch):
    """OSError during subprocess.run → ``None``."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    def fake_run(*args, **kwargs):
        raise OSError(13, "Permission denied")
    monkeypatch.setattr(subprocess, "run", fake_run)
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is None


def test_match_is_case_insensitive(monkeypatch):
    """PowerShell's ``-match`` operator is case-insensitive by
    default; verify a lower-case 'plexstim' in stdout still flips
    True so a future PnP rename to lowercase wouldn't silently
    fail the probe."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="plexstim 2 hardware\n",
            stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is True


def test_only_whitespace_lines_filtered(monkeypatch):
    """PowerShell sometimes appends trailing blank lines; those
    must not count as matches."""
    import platform
    monkeypatch.setattr(platform, "system", lambda: "Windows")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="\n   \n\t\n",
            stderr="",
        )
    monkeypatch.setattr(subprocess, "run", fake_run)
    from stimtest.hardware.plexstim_detect import plexstim_device_present
    assert plexstim_device_present() is False
