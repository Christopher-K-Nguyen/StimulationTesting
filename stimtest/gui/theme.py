"""Light / dark / system colour-theme switching for the PULSAR GUI.

Operator: "let in View to change between light mode and dark mode … and
system."

Design
------
The colour theme is applied at the **QApplication** level as a *Fusion* style
+ an explicit ``QPalette``.  Fusion is used (rather than the native platform
style) because it honours a custom palette fully on every platform — the native
Windows style ignores palette overrides for many controls, so light/dark
wouldn't apply reliably there.

Only the GUI **chrome** (menus, tables, panels, backgrounds, the rotated
``_AxisTitle`` axis labels that live in the plot margins) follows the palette.
The pyqtgraph plot interiors are intentionally WHITE with black ticks/traces in
both themes (``setBackground("w")`` / ``foreground="k"``) — they mirror the
exported MATLAB-style figures and stay print-ready regardless of the GUI theme,
so nothing plot-side needs to change on a theme switch.

Three modes:
  * ``"system"`` — follow the OS colour scheme (Qt 6.5+ ``styleHints()
    .colorScheme()``); auto-updates when the OS toggles (see
    :func:`connect_system_scheme`).
  * ``"light"`` / ``"dark"`` — force that palette regardless of the OS.
"""
from __future__ import annotations

from typing import Optional

from PyQt6 import QtGui, QtWidgets, QtCore

#: Valid theme-mode strings (also the order shown in the View → Theme menu).
THEME_MODES = ("system", "light", "dark")
DEFAULT_THEME = "system"


def _dark_palette() -> QtGui.QPalette:
    """A neutral dark palette (the widely-used Qt-Fusion dark scheme)."""
    C = QtGui.QColor
    Role = QtGui.QPalette.ColorRole
    Grp = QtGui.QPalette.ColorGroup
    p = QtGui.QPalette()
    p.setColor(Role.Window, C(0x35, 0x35, 0x35))
    p.setColor(Role.WindowText, C(0xF0, 0xF0, 0xF0))
    p.setColor(Role.Base, C(0x23, 0x23, 0x23))
    p.setColor(Role.AlternateBase, C(0x3A, 0x3A, 0x3A))
    p.setColor(Role.ToolTipBase, C(0x2A, 0x2A, 0x2A))
    p.setColor(Role.ToolTipText, C(0xF0, 0xF0, 0xF0))
    p.setColor(Role.Text, C(0xF0, 0xF0, 0xF0))
    p.setColor(Role.Button, C(0x35, 0x35, 0x35))
    p.setColor(Role.ButtonText, C(0xF0, 0xF0, 0xF0))
    p.setColor(Role.BrightText, C(0xFF, 0x55, 0x55))
    p.setColor(Role.Link, C(0x4D, 0x9C, 0xE8))
    p.setColor(Role.Highlight, C(0x2A, 0x82, 0xDA))
    p.setColor(Role.HighlightedText, C(0xFF, 0xFF, 0xFF))
    p.setColor(Role.PlaceholderText, C(0x9A, 0x9A, 0x9A))
    disabled = C(0x7F, 0x7F, 0x7F)
    for role in (Role.WindowText, Role.Text, Role.ButtonText):
        p.setColor(Grp.Disabled, role, disabled)
    p.setColor(Grp.Disabled, Role.Highlight, C(0x50, 0x50, 0x50))
    p.setColor(Grp.Disabled, Role.HighlightedText, disabled)
    return p


def _light_palette() -> QtGui.QPalette:
    """Fusion's standard LIGHT palette."""
    fusion = QtWidgets.QStyleFactory.create("Fusion")
    if fusion is not None:
        return fusion.standardPalette()
    # Fusion unavailable (very unusual) — hand-build a plain light palette.
    C = QtGui.QColor
    Role = QtGui.QPalette.ColorRole
    p = QtGui.QPalette()
    p.setColor(Role.Window, C(0xF0, 0xF0, 0xF0))
    p.setColor(Role.WindowText, C(0x1A, 0x1A, 0x1A))
    p.setColor(Role.Base, C(0xFF, 0xFF, 0xFF))
    p.setColor(Role.Text, C(0x1A, 0x1A, 0x1A))
    p.setColor(Role.Button, C(0xE8, 0xE8, 0xE8))
    p.setColor(Role.ButtonText, C(0x1A, 0x1A, 0x1A))
    p.setColor(Role.Highlight, C(0x2A, 0x82, 0xDA))
    p.setColor(Role.HighlightedText, C(0xFF, 0xFF, 0xFF))
    return p


def system_scheme() -> str:
    """Return the OS colour scheme as ``"light"`` / ``"dark"``.

    Uses ``QStyleHints.colorScheme()`` (Qt 6.5+).  Falls back to inspecting the
    current window colour's lightness, then to ``"dark"`` if all else fails
    (the bench GUI has always run dark)."""
    app = QtWidgets.QApplication.instance()
    try:
        scheme = app.styleHints().colorScheme()
        if scheme == QtCore.Qt.ColorScheme.Dark:
            return "dark"
        if scheme == QtCore.Qt.ColorScheme.Light:
            return "light"
    except Exception:
        pass
    try:
        win = app.palette().color(QtGui.QPalette.ColorRole.Window)
        return "dark" if win.lightness() < 128 else "light"
    except Exception:
        return "dark"


def resolve_scheme(mode: str) -> str:
    """Map a theme MODE to the concrete ``"light"``/``"dark"`` scheme to apply."""
    mode = (mode or DEFAULT_THEME).lower()
    if mode == "light":
        return "light"
    if mode == "dark":
        return "dark"
    return system_scheme()          # "system" (or anything unknown)


def apply_theme(app: Optional[QtWidgets.QApplication], mode: str,
                *, repolish: bool = True) -> str:
    """Apply the theme ``mode`` to ``app`` (Fusion style + palette).

    Returns the concrete scheme actually applied (``"light"``/``"dark"``).
    No-op-safe when ``app`` is None (e.g. a headless import).

    Fusion is used because it honours a custom palette on every platform (the
    native Windows style ignores palette overrides), so light/dark apply
    reliably.

    ``repolish`` (default True) installs a FRESH Fusion style instance, which
    forces a full widget RE-POLISH so a LIVE theme switch actually repaints
    already-built widgets (``setPalette`` alone leaves each widget's cached
    resolved palette stale — the dark-mode "still looks light" bug).  Pass
    ``repolish=False`` at STARTUP (launch → before the window, and the
    construction-time re-apply): there are no built widgets to repaint yet
    (launch) or the tree was just built under Fusion (construction), so the
    forced re-polish is wasted work + a needless crash surface on lazy
    widgets.  In that mode the style is switched to Fusion only if it isn't
    already."""
    if app is None:
        app = QtWidgets.QApplication.instance()
    if app is None:
        return resolve_scheme(mode)
    scheme = resolve_scheme(mode)
    pal = _dark_palette() if scheme == "dark" else _light_palette()
    try:
        if repolish:
            fusion = QtWidgets.QStyleFactory.create("Fusion")
            if fusion is not None:
                app.setStyle(fusion)      # fresh instance ⇒ full re-polish
        elif "fusion" not in app.style().objectName().lower():
            app.setStyle("Fusion")        # startup: switch only if needed
    except Exception:
        pass
    app.setPalette(pal)
    return scheme


def apply_palette_only(app: Optional[QtWidgets.QApplication], mode: str) -> str:
    """Set ONLY the theme palette (no ``setStyle``).

    Used at MainWindow-construction time: ``launch()`` already installed Fusion
    before the window, so the tree is built under it and only the palette needs
    (re-)asserting.  Crucially this NEVER calls ``setStyle`` — a fresh
    ``setStyle`` forces a GLOBAL widget re-polish that segfaults when stale /
    leaked widgets exist (a real hazard in a full test run; harmless but
    pointless in the real single-window app).  Returns the applied scheme."""
    if app is None:
        app = QtWidgets.QApplication.instance()
    if app is None:
        return resolve_scheme(mode)
    scheme = resolve_scheme(mode)
    app.setPalette(_dark_palette() if scheme == "dark" else _light_palette())
    return scheme


def connect_system_scheme(app: QtWidgets.QApplication, callback) -> None:
    """Call ``callback()`` when the OS colour scheme changes (Qt 6.5+).

    The caller uses this to re-apply the palette while in ``"system"`` mode.
    Idempotent-safe; silently no-ops on a Qt build without the signal."""
    try:
        app.styleHints().colorSchemeChanged.connect(lambda _scheme: callback())
    except Exception:
        pass
