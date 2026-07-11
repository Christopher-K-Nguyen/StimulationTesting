"""View → Theme: light / dark / system colour switching.

Operator: "let in View to change between light mode and dark mode … and
system."  The theme is a Fusion palette applied at the QApplication level;
plots stay white in both themes (see gui/theme.py).

NOTE: these tests use ``apply_theme(..., repolish=False)`` / avoid the live
``_on_theme_selected`` path.  ``repolish=True`` re-installs a fresh Fusion style,
which forces a GLOBAL widget re-polish — harmless in the real app (one window)
but a segfault in a full-suite run, where earlier MainWindow tests leave
lingering (not-yet-GC'd) windows for the re-polish to walk.  The live re-polish
is validated visually via the render scripts, not the automated suite.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")
pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets, QtGui  # noqa: E402
from PyQt6.QtTest import QTest      # noqa: E402


@pytest.fixture(scope="module")
def _app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


@pytest.fixture(scope="module", autouse=True)
def _restore_global_palette(_app):
    # Restore ONLY the palette at teardown — NEVER setStyle: a fresh setStyle
    # forces a global widget re-polish that segfaults on stale/leaked widgets
    # from other MainWindow test modules in a full run.
    orig_pal = QtGui.QPalette(_app.palette())
    yield
    try:
        _app.setPalette(orig_pal)
    except Exception:
        pass


# ---- pure palette helpers (no global re-polish) --------------------------
def test_dark_and_light_palettes_differ_in_lightness():
    from stimtest.gui.theme import _dark_palette, _light_palette
    Win = QtGui.QPalette.ColorRole.Window
    Txt = QtGui.QPalette.ColorRole.Text
    dark_win = _dark_palette().color(Win).lightness()
    light_win = _light_palette().color(Win).lightness()
    assert dark_win < 96 < light_win
    # Text contrasts its window in each theme.
    assert _dark_palette().color(Txt).lightness() > 160     # light text on dark
    assert _light_palette().color(Txt).lightness() < 96      # dark text on light


def test_apply_palette_only_sets_the_palette(_app):
    # ``apply_palette_only`` sets the palette WITHOUT setStyle (no global
    # re-polish) — the construction-time path.
    from stimtest.gui.theme import apply_palette_only
    Win = QtGui.QPalette.ColorRole.Window
    assert apply_palette_only(_app, "dark") == "dark"
    assert _app.palette().color(Win).lightness() < 96
    assert apply_palette_only(_app, "light") == "light"
    assert _app.palette().color(Win).lightness() > 160


def test_resolve_scheme(_app):
    from stimtest.gui.theme import resolve_scheme
    assert resolve_scheme("light") == "light"
    assert resolve_scheme("dark") == "dark"
    assert resolve_scheme("system") in ("light", "dark")
    assert resolve_scheme("bogus") in ("light", "dark")   # unknown → system


def test_theme_persists_as_a_section_dict(_app):
    # load_prefs drops non-dict top-level keys, so the theme must round-trip as
    # a ``{"theme": {"mode": ...}}`` SECTION (regression: a bare string was
    # dropped on load → the choice never persisted).
    from stimtest.gui.prefs import load_prefs, save_prefs
    prefs = load_prefs() or {}
    prefs["theme"] = {"mode": "dark"}
    save_prefs(prefs)
    assert ((load_prefs() or {}).get("theme") or {}).get("mode") == "dark"
    prefs = load_prefs() or {}
    prefs["theme"] = {"mode": "system"}       # reset the sandbox pref
    save_prefs(prefs)


# ---- MainWindow menu integration (ONE window, construction is repolish=False)
@pytest.fixture(scope="module")
def _win(_app):
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    QTest.qWait(300)
    yield w
    w.close()


def test_theme_menu_has_three_exclusive_actions(_win):
    assert set(_win._theme_actions) == {"system", "light", "dark"}
    assert all(a.isCheckable() for a in _win._theme_actions.values())
    # Exactly one is checked (an action group is exclusive).
    checked = [m for m, a in _win._theme_actions.items() if a.isChecked()]
    assert len(checked) == 1


def test_apply_saved_theme_syncs_the_menu_radio(_win):
    # Persist "dark", re-run the startup applier (repolish=False), and the menu
    # radio + _theme_mode reflect it — the persistence → menu round-trip.
    from stimtest.gui.prefs import load_prefs, save_prefs
    prefs = load_prefs() or {}
    prefs["theme"] = {"mode": "dark"}
    save_prefs(prefs)
    _win._apply_saved_theme()
    assert _win._theme_mode == "dark"
    assert _win._theme_actions["dark"].isChecked()
    # reset
    prefs = load_prefs() or {}
    prefs["theme"] = {"mode": "system"}
    save_prefs(prefs)
    _win._apply_saved_theme()
