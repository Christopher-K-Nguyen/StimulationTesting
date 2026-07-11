"""Input-time missing-save-folder CHOICE dialog.

Operator: "I want a pop up with choices for the user to choose when an
inputted directory does not exist."  When the user INPUTS (types / browses) a
save folder that does not exist, ``MainWindow._on_save_path_changed`` pops a
**Create / Choose a different folder… / Cancel** dialog (via
``_resolve_missing_save_dir``) instead of SILENTLY creating a possibly-mistyped
path.  During restore / startup / headless it preserves the historical
best-effort create (that path is a remembered/default location, not a fresh
"input").

The modal itself is bypassed under ``PULSAR_SKIP_*`` (headless), so these tests
exercise the ROUTING + the pure helpers, stubbing the dialog where a decision
is needed.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")


@pytest.fixture(scope="module")
def _win():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    return MainWindow(simulate_default=True)


def test_suppressed_env_detected(_win, monkeypatch):
    monkeypatch.setenv("PULSAR_SKIP_OVERWRITE_PROMPT", "1")
    assert _win._save_path_prompts_suppressed() is True


def test_headless_missing_dir_is_created_not_prompted(_win, tmp_path, monkeypatch):
    """Headless / suppressed → NON-interactive branch: historical best-effort
    create, and the choice dialog is NEVER invoked."""
    target = tmp_path / "newfolder"
    assert not target.exists()
    called = {"n": 0}
    monkeypatch.setattr(_win, "_resolve_missing_save_dir",
                        lambda p: (called.__setitem__("n", called["n"] + 1), p)[1])
    _win._on_save_path_changed(str(target))
    assert target.exists(), "non-interactive path should best-effort create"
    assert called["n"] == 0, "no choice dialog in the suppressed/headless path"
    assert _win.save_dir == target


def test_interactive_missing_dir_routes_through_choice_dialog(_win, tmp_path, monkeypatch):
    """A genuine post-startup USER input of a non-existent folder routes through
    the choice dialog; the RESOLVED folder is applied, not the typed one."""
    monkeypatch.setattr(_win, "_log_setup_changes", True, raising=False)
    monkeypatch.setattr(_win, "_save_path_prompts_suppressed", lambda: False)
    chosen = tmp_path / "picked"
    chosen.mkdir()
    monkeypatch.setattr(_win, "_resolve_missing_save_dir", lambda p: chosen)
    typed = tmp_path / "typo_does_not_exist"
    _win._on_save_path_changed(str(typed))
    assert _win.save_dir == chosen
    assert not typed.exists(), "the typed non-existent path must NOT be created"


def test_interactive_cancel_keeps_previous_save_dir(_win, tmp_path, monkeypatch):
    """Cancel → keep the previous save dir, do NOT create the typed folder."""
    prev = tmp_path / "prev"
    prev.mkdir()
    _win.save_dir = prev
    monkeypatch.setattr(_win, "_log_setup_changes", True, raising=False)
    monkeypatch.setattr(_win, "_save_path_prompts_suppressed", lambda: False)
    monkeypatch.setattr(_win, "_resolve_missing_save_dir", lambda p: None)
    typed = tmp_path / "nope"
    _win._on_save_path_changed(str(typed))
    assert _win.save_dir == prev, "Cancel must keep the previous save dir"
    assert not typed.exists()


def test_existing_dir_applies_without_dialog(_win, tmp_path, monkeypatch):
    """An existing inputted folder applies directly — the dialog is never
    consulted, regardless of interactivity."""
    monkeypatch.setattr(_win, "_log_setup_changes", True, raising=False)
    monkeypatch.setattr(_win, "_save_path_prompts_suppressed", lambda: False)
    called = {"n": 0}
    monkeypatch.setattr(_win, "_resolve_missing_save_dir",
                        lambda p: (called.__setitem__("n", called["n"] + 1), p)[1])
    good = tmp_path / "exists"
    good.mkdir()
    _win._on_save_path_changed(str(good))
    assert _win.save_dir == good
    assert called["n"] == 0, "existing folder must not trigger the dialog"
