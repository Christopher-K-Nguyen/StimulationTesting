"""PyQt6 main window — connects all the tabs together."""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import DEFAULT_SAVE_DIR
from ..electrode import ElectrodeArray
from .connection_panel import ConnectionPanel
from .experiment_tabs import (
    ContinuousPulsingTab, GalvanostaticEISTab, LongPulsingTab,
    ProgressiveStressTab, ShortPulsingTab, VoltageTransientTab,
)
from .prefs import (
    load_prefs, load_prefs_from, prefs_dir, prefs_path,
    save_prefs, save_prefs_to,
    PREFS_USER_EXT, PREFS_USER_EXT_LEGACY, PREFS_USER_FILTER,
)
from .admin import (
    AdminCatalogDialog, apply_admin_catalog, _DEFAULT_HASH,
    prompt_login, prompt_first_launch_setup, CATALOG_KEYS, Profile,
    is_restricted_unlocked, is_admin,
    register_extension_profile, list_extension_profiles,
    get_extension_profile,
)
from .calibration import CalibrationTab
from .results_tab import ResultsTab
from .setup_tab import SetupTab
from .widgets import LogPane


# Section keys used inside the prefs JSON file. Keep these stable across
# releases — renaming forfeits the user's last-saved values for that tab.
PREF_KEY_SETUP = "setup"
PREF_KEY_VT = "vt"
PREF_KEY_SP = "sp"
PREF_KEY_CP = "cp"
PREF_KEY_LP = "lp"
PREF_KEY_PS = "ps"
PREF_KEY_VIEW = "view"
# Results tab → embedded ViewerPanel state (axis map, last-opened
# session, splitter sizes, channel-map metric). Same key is read by
# the standalone ``StimulationTestingViewer.exe`` launcher so the
# two share one persistent context.
PREF_KEY_RESULTS = "results"


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, simulate_default: bool = True, save_dir: Optional[str] = None):
        super().__init__()
        # Show the app version beside the name in the title bar (operator:
        # "put the version number by PULSAR on the window") so a bench
        # screenshot / bug report always says which build is running.
        try:
            from .. import __version__ as _pulsar_version
            self.setWindowTitle(f"PULSAR v{_pulsar_version}")
        except Exception:
            self.setWindowTitle("PULSAR")
        self.resize(1500, 950)

        # ---- Profile login state ------------------------------------
        # PULSAR ships with two built-in profiles (see :mod:`.admin`):
        #   * Profile.NONE  — anonymous (default at startup)
        #   * Profile.ADMIN — unlocks Manage Custom Catalog
        #
        # Extension packages (any installed ``stimtest_*`` package
        # whose ``__init__.py`` calls ``register_extension_profile``)
        # can register additional profiles at runtime — they appear
        # as plain string names in ``_current_profile``, not enum
        # members.  See ``_load_extensions`` near the end of
        # ``__init__`` for the auto-discovery.
        #
        # ``_current_profile`` is stored as a string so it can hold
        # either a built-in (``Profile.ADMIN.value`` == ``"admin"``)
        # or an extension name (e.g., the name a collaborator
        # package registered).
        #
        # Read ``_current_profile`` (string) directly; gate behavior
        # with ``is_restricted_unlocked`` / ``is_admin`` helpers from
        # ``stimtest.gui.admin``.  An earlier draft carried a derived
        # ``_admin_logged_in`` BOOLEAN alias for back-compat, but no
        # callers ever read it — audit finding #5 removed it to avoid
        # the desync risk of a write-only piece of state.
        self._current_profile: str = Profile.NONE.value
        self._admin_password_hash: str = _DEFAULT_HASH
        self._admin_catalog: dict = {k: [] for k in CATALOG_KEYS}
        # Login-history list (most-recent-first, lowercased).  Seeded
        # from prefs at load time; prepended on each successful login
        # via update_login_history.  Used to populate the login
        # dialog's Username dropdown so the operator doesn't retype
        # extension usernames every session.
        self._admin_login_history: list[str] = []
        # First-launch setup-completion flag.  Loaded from prefs;
        # gates whether ``_maybe_run_first_launch_setup`` shows the
        # one-time admin password setup dialog.  Starts False — a
        # fresh install (no prefs file) will see the dialog.
        self._admin_setup_completed: bool = False
        # Profiles the operator imported via Admin → Import Profile…
        # (plugin-host profiles distributed as .json files, e.g. CWRU).
        # Each entry is ``{name, display_name, password_hash, shapes}``.
        # Persisted in prefs and re-registered at launch by
        # ``_load_imported_profiles`` so an imported profile survives
        # restarts WITHOUT the pip package being installed.
        self._imported_profiles: list[dict] = []

        self.save_dir = Path(save_dir or DEFAULT_SAVE_DIR)
        array = ElectrodeArray.utah_4x4()

        # Connection panel — displayed *inside* the Setup tab now, so
        # MainWindow keeps the reference (it owns the stim/scope handles)
        # but doesn't add it to the central layout.
        self.conn = ConnectionPanel(simulate_default=simulate_default)

        # Tabs
        self.setup_tab = SetupTab(connection_panel=self.conn)
        self.vt_tab = VoltageTransientTab(array)
        self.sp_tab = ShortPulsingTab(array)
        self.cp_tab = ContinuousPulsingTab(array)
        self.lp_tab = LongPulsingTab(array)
        self.ps_tab = ProgressiveStressTab(array)
        self.eis_tab = GalvanostaticEISTab(array)
        self.res_tab = ResultsTab(self.save_dir)

        # Plumb the shared ConnectionPanel into each experiment tab as
        # the INTERSTELLAR (interpulse-bias) driver host, so every tab's
        # config panel shows/hides in lockstep with the single Connect
        # button in the Setup tab.  Done ONLY when the experimental
        # feature is enabled — the public build has no bias UI or wiring
        # at all (stimtest.feature_flags — gotcha #103).
        from ..feature_flags import interstellar_enabled
        if interstellar_enabled():
            for tab in (self.vt_tab, self.sp_tab, self.cp_tab, self.lp_tab,
                        self.ps_tab):
                try:
                    tab.set_bias_host(self.conn)
                except AttributeError:
                    pass

        # ``_current_exp_code`` must exist before the tabs are added —
        # adding a tab to an empty QTabWidget fires ``currentChanged``,
        # and our handler reads ``self._current_exp_code``. Set it
        # here, then point the per-experiment dict at it later once
        # the experiment widgets exist.
        self._current_exp_code: Optional[str] = None
        # When the operator opens the Test parameters page, pull the
        # current Setup parameters into the active experiment tab IF
        # they're stale (a Setup change is pending, or this is the first
        # entry).  Initialised True so the first entry always syncs —
        # this closes the gap where aliases / limits don't re-fire their
        # change signal on prefs restore and so never reached the tab.
        # Flipped True on every Setup change (in _log_setup_change),
        # cleared after the pull (in _on_top_tab_changed).
        self._setup_dirty_for_test: bool = True
        self.tabs = QtWidgets.QTabWidget()
        # Swallow mouse-wheel events on the tab bar so accidental
        # scrolling over the tabs doesn't flip the user between
        # Setup / Test parameters / Experiment / Results unexpectedly.
        # Tab navigation is still available via clicks and keyboard.
        from .widgets import disable_tabbar_wheel_scroll
        disable_tabbar_wheel_scroll(self.tabs)
        # Re-parent the active experiment's Start/Pause/Stop row to
        # whichever top-level tab the user just clicked into (Test
        # parameters or the experiment view) — the buttons should
        # appear in the same bottom-of-tab position regardless of
        # which one is focused.
        self.tabs.currentChanged.connect(self._on_top_tab_changed)
        self.tabs.addTab(self.setup_tab, "Setup")
        # Calibration tab is created lazily — only when the user clicks
        # "Run Calibration" on the ConnectionPanel (or Run → Calibrate in
        # the menu).  Keeping it out of the initial tab bar trims the
        # default chrome and matches the workflow: most sessions don't
        # need a fresh verification sweep on every launch.  See
        # :meth:`_open_calibration_tab` for the on-demand wire-up.
        self.cal_tab: Optional[CalibrationTab] = None
        # "Test parameters" — top-level tab, sits directly to the
        # right of Setup. Hosts whichever experiment-tab's
        # ``params_page`` is currently active (was an inner sub-tab
        # inside each ExperimentTab; promoted here per user spec).
        # Content is replaced by ``_show_experiment`` whenever the
        # active experiment changes; the wrapper widget itself is a
        # plain QVBoxLayout container so we can re-parent the
        # params_page in/out cleanly.
        self._test_params_tab = QtWidgets.QWidget()
        self._test_params_layout = QtWidgets.QVBoxLayout(self._test_params_tab)
        self._test_params_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs.addTab(self._test_params_tab, "Test parameters")
        # Experiment tabs are inserted on-demand by :meth:`_show_experiment`
        # so the user only sees the one they're actually running. This
        # avoids the four-tabs-of-similar-stuff clutter and matches the
        # MATLAB workflow where you pick an experiment and then drive it.
        self.tabs.addTab(self.res_tab, "Results")
        # All known experiment tabs, indexed by their code.
        self._exp_tab_by_code = {
            "VT": (self.vt_tab, "Voltage Transient"),
            "SP": (self.sp_tab, "Short-Term Pulsing"),
            "CP": (self.cp_tab, "Continuous Pulsing"),
            "LP": (self.lp_tab, "Long-Term Pulsing"),
            "PS": (self.ps_tab, "Progressive Stress"),
            "EIS": (self.eis_tab, "Galvanostatic Electrochemical Impedance Spectroscopy"),
        }

        # Shared log pane — pinned to the bottom of the central widget so
        # it's visible from every tab (Setup, any experiment, Results).
        # Each experiment tab gets its ``log_pane`` reference replaced
        # with this shared instance below, so all `self.log_pane.log(...)`
        # call sites and the worker's ``log_msg`` signal connections all
        # write into the one window-bottom panel.
        self.log_pane = LogPane()
        # Direct the on-disk mirror at the session-stem-derived
        # filename. ``_on_save_path_changed`` and
        # ``_on_log_filename_changed`` repoint this whenever the user
        # picks a new save directory or edits the notebook / session
        # fields.
        self.log_pane.set_log_file(
            self.save_dir / self.setup_tab.current_log_filename())
        # Thorough session banner — captures the environment so a future
        # post-mortem against a saved .txt log has all the version /
        # backend info needed to reproduce behaviour or pin a regression.
        try:
            import sys as _sys, platform as _plat, datetime as _dt
            try:
                import numpy as _np
                _np_ver = _np.__version__
            except Exception:
                _np_ver = "<unavailable>"
            try:
                from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR
                _qt_str = f"Qt {QT_VERSION_STR} (PyQt {PYQT_VERSION_STR})"
            except Exception:
                _qt_str = "<unavailable>"
            try:
                import pyqtgraph as _pg
                _pg_ver = _pg.__version__
            except Exception:
                _pg_ver = "<unavailable>"
            try:
                import pyvisa as _pv
                _pv_ver = _pv.__version__
            except Exception:
                _pv_ver = "<unavailable>"
            self.log_pane.log(
                "================================================================")
            self.log_pane.log(
                f"PULSAR session start  {_dt.datetime.now().isoformat(timespec='seconds')}")
            self.log_pane.log(
                "================================================================")
            self.log_pane.log(
                f"Python {_sys.version.split()[0]}  ·  "
                f"{_plat.platform()}  ·  "
                f"NumPy {_np_ver}  ·  "
                f"{_qt_str}  ·  "
                f"pyqtgraph {_pg_ver}  ·  "
                f"pyvisa {_pv_ver}")
            self.log_pane.log(
                f"Mode: {'simulator' if simulate_default else 'live hardware'}  ·  "
                f"Save dir: {self.save_dir}  ·  "
                f"Log file: {self.setup_tab.current_log_filename()}")
            self.log_pane.log(
                "================================================================")
        except Exception:
            pass
        log_box = QtWidgets.QGroupBox("Log")
        log_v = QtWidgets.QVBoxLayout(log_box)
        log_v.setContentsMargins(4, 2, 4, 4)
        log_v.addWidget(self.log_pane)

        # Hand the shared log pane to each experiment tab so all of
        # their existing self.log_pane.log(...) calls write here.
        for tab in self._experiment_tabs():
            tab.log_pane = self.log_pane

        # Layout — the connection panel now lives inside the Setup tab,
        # so the central widget is just the tab bar plus the shared log.
        # A vertical splitter between them lets the user drag the log
        # taller / shorter at will.
        central = QtWidgets.QWidget(); self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)
        main_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        main_split.addWidget(self.tabs)
        main_split.addWidget(log_box)
        # Tab area takes the lion's share by default; the log starts
        # short but can be dragged taller.
        main_split.setStretchFactor(0, 5)
        main_split.setStretchFactor(1, 1)
        # Don't let either pane collapse to zero — always at least a
        # row of tabs and a few log lines visible.
        main_split.setCollapsible(0, False)
        main_split.setCollapsible(1, False)
        # Initial pixel sizes (Qt picks reasonable values from these
        # ratios; the splitter handle remains draggable).
        main_split.setSizes([800, 150])
        v.addWidget(main_split)
        # Keep a reference so the prefs save/restore can serialise it.
        self._main_split = main_split

        # File menu — explicit save/load for named profiles. The
        # in-process auto-save (closeEvent + post-run) still writes the
        # default ``gui_prefs.json``; these menu items let the user
        # snapshot the full state to a file of their choosing and
        # reload it later. Useful for switching between subjects /
        # electrode arrays without retyping every parameter.
        file_menu = self.menuBar().addMenu("&File")
        act_save = file_menu.addAction("&Save settings…")
        act_save.setShortcut("Ctrl+S")
        act_save.triggered.connect(self._on_save_settings)
        act_load = file_menu.addAction("&Load settings…")
        act_load.setShortcut("Ctrl+O")
        act_load.triggered.connect(self._on_load_settings)
        # Reset to default — restores every tab to the values captured
        # at startup *before* any saved prefs were applied. The reset
        # also overwrites the on-disk prefs file so a subsequent close
        # doesn't immediately re-save the user's old state. See
        # :meth:`_on_reset_to_default`.
        act_reset = file_menu.addAction("&Reset to default")
        act_reset.triggered.connect(self._on_reset_to_default)
        file_menu.addSeparator()
        act_quit = file_menu.addAction("&Quit")
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)

        # Edit menu — standard text / table editing actions. Each
        # action dispatches to whatever widget currently has focus
        # (line edit, spin-box editor, table cell, plain-text log
        # pane, etc.). The dispatchers handle each widget type
        # specifically so the right "undo" / "select-all" semantic
        # applies regardless of which input the user clicked into.
        edit_menu = self.menuBar().addMenu("&Edit")
        act_undo = edit_menu.addAction("&Undo")
        act_undo.setShortcut("Ctrl+Z")
        act_undo.triggered.connect(self._on_edit_undo)
        act_redo = edit_menu.addAction("&Redo")
        act_redo.setShortcut("Ctrl+Y")
        act_redo.triggered.connect(self._on_edit_redo)
        edit_menu.addSeparator()
        act_cut = edit_menu.addAction("Cu&t")
        act_cut.setShortcut("Ctrl+X")
        act_cut.triggered.connect(self._on_edit_cut)
        act_copy = edit_menu.addAction("&Copy")
        act_copy.setShortcut("Ctrl+C")
        act_copy.triggered.connect(self._on_edit_copy)
        act_paste = edit_menu.addAction("&Paste")
        act_paste.setShortcut("Ctrl+V")
        act_paste.triggered.connect(self._on_edit_paste)
        edit_menu.addSeparator()
        act_select_all = edit_menu.addAction("Select &All")
        act_select_all.setShortcut("Ctrl+A")
        act_select_all.triggered.connect(self._on_edit_select_all)

        # Run menu — experiment-side actions. Connection control
        # is intentionally not in this menu: the stim has Initialize /
        # Close while the scope has Connect / Disconnect, and the two
        # sides have independent lifecycles, so we leave those buttons
        # in the Setup-tab Connection panel rather than papering over
        # the asymmetry with a one-size-fits-all menu entry.
        # Start / Pause / Stop / Single capture are DISABLED (greyed
        # out) until the user clicks Start on a tab; they enable
        # while a run is live and grey out again on finish — a stale
        # "Stop" with no runner attached would just be confusing.
        # Calibrate is gated on hardware availability (needs both
        # the PlexStim initialized AND the scope connected — see
        # ``_act_calibrate.setEnabled(False)`` below and the flip
        # in ``_on_connected``/``_on_disconnected``). Open Viewer
        # stays enabled all the time since it doesn't require an
        # active run or live hardware.
        run_menu = self.menuBar().addMenu("&Run")
        self._run_menu = run_menu
        self._act_start = run_menu.addAction("&Start")
        self._act_start.setShortcut("F5")
        self._act_start.triggered.connect(self._on_run_start)
        self._act_pause = run_menu.addAction("&Pause")
        self._act_pause.setShortcut("F6")
        self._act_pause.triggered.connect(self._on_run_pause)
        # Audit finding #9 — runner-side ``pause()`` doesn't exist
        # yet. Hide the menu entry (and along with it the F6
        # shortcut) so users can't summon a non-functional control.
        # The slot, the experiment-tab ``pause_btn`` wiring, and the
        # enable-flip in ``_refresh_run_menu_enabled`` all stay in
        # place — flipping ``setVisible(True)`` re-exposes
        # everything once the runner gets a real implementation.
        self._act_pause.setVisible(False)
        self._act_stop = run_menu.addAction("S&top")
        self._act_stop.setShortcut("F7")
        self._act_stop.triggered.connect(self._on_run_stop)
        self._act_capture = run_menu.addAction("Single &capture")
        self._act_capture.setShortcut("F8")
        self._act_capture.triggered.connect(self._on_run_single_capture)
        # Greyed out off-run, enabled while a run is live —
        # ``_refresh_run_menu_enabled`` is called from
        # ``_on_run_state_changed`` so the menu tracks the run-locked
        # flag every experiment tab emits.
        for act in (self._act_start, self._act_pause,
                    self._act_stop, self._act_capture):
            act.setEnabled(False)
        run_menu.addSeparator()
        self._act_calibrate = run_menu.addAction("&Stimulator Verification…")
        self._act_calibrate.triggered.connect(self._on_run_calibrate)
        # Calibrate needs BOTH hardware sides up — the PlexStim has
        # to deliver pulses and the scope has to capture V_mon /
        # I_mon. Start disabled; flip on via ``_on_connected`` (which
        # the ConnectionPanel only fires after the stim is fully
        # initialized AND the scope is connected) and back off via
        # ``_on_disconnected``.
        self._act_calibrate.setEnabled(False)
        self._act_calibrate.setToolTip(
            "Initialize the PlexStim and connect the oscilloscope "
            "first (Setup tab → Connection panel).")
        # Re-exposed per user request. The dialog itself works (the
        # data-access bug that left it staring at None handles has
        # been fixed, see :meth:`_on_run_calibrate`); the broader
        # sweep-flow / runner-side integration is still maturing,
        # so users running the wizard on real hardware should treat
        # it as best-effort until further validation lands. The
        # enable/disable gate in ``_on_connected`` / ``_on_disconnected``
        # still requires both stim + scope live before the action
        # becomes clickable.
        self._act_calibrate.setVisible(True)
        run_menu.addSeparator()
        self._act_open_viewer = run_menu.addAction("Open &Viewer")
        self._act_open_viewer.triggered.connect(self._on_run_open_viewer)

        # View menu — GUI scale (zoom) and font-size presets.
        # Affects all widgets via a top-level stylesheet that sets
        # the base ``font-size`` in points; Qt's layout engine
        # propagates the font size to padding / spacing inside
        # most widget styles, so a single setStyleSheet call gives
        # a uniform "zoom" feel across the entire GUI without
        # touching individual widgets.
        view_menu = self.menuBar().addMenu("&View")
        # Default state — captured at __init__ time so we can
        # restore the "100%" baseline. ``_base_font_pt`` is the
        # user-picked base size (Small / Medium / Large preset);
        # ``_scale_factor`` multiplies it for the live zoom level.
        # The effective font is ``base_pt × scale_factor``.
        self._base_font_pt: float = 10.0
        self._scale_factor: float = 1.0
        # Zoom actions — Ctrl+= / Ctrl+- / Ctrl+0 mirror every
        # browser's zoom shortcut so the user doesn't have to
        # learn new keys.
        act_zoom_in = view_menu.addAction("Zoom &In")
        act_zoom_in.setShortcut("Ctrl++")
        act_zoom_in.triggered.connect(self._on_view_zoom_in)
        # Also bind Ctrl+= so users with keyboards that need Shift
        # for the literal "+" can zoom in without the modifier
        # gymnastics (this is the de-facto browser convention).
        act_zoom_in_alt = QtGui.QAction(self)
        act_zoom_in_alt.setShortcut("Ctrl+=")
        act_zoom_in_alt.triggered.connect(self._on_view_zoom_in)
        self.addAction(act_zoom_in_alt)
        act_zoom_out = view_menu.addAction("Zoom &Out")
        act_zoom_out.setShortcut("Ctrl+-")
        act_zoom_out.triggered.connect(self._on_view_zoom_out)
        act_zoom_reset = view_menu.addAction("&Reset Zoom")
        act_zoom_reset.setShortcut("Ctrl+0")
        act_zoom_reset.triggered.connect(self._on_view_zoom_reset)
        view_menu.addSeparator()
        # Font Size submenu — four presets. The active preset is
        # check-marked; picking one switches the baseline pt that
        # the zoom multiplier scales. Defaults to Medium (10pt).
        font_menu = view_menu.addMenu("&Font Size")
        self._font_size_actions: Dict[float, "QtGui.QAction"] = {}
        for label, pt in (("&Small (9pt)", 9.0),
                           ("&Medium (10pt)", 10.0),
                           ("&Large (12pt)", 12.0),
                           ("&Extra Large (14pt)", 14.0)):
            act = font_menu.addAction(label)
            act.setCheckable(True)
            act.triggered.connect(
                lambda _c=False, p=pt: self._set_base_font_pt(p))
            self._font_size_actions[pt] = act
        # Mark the default (Medium) as initially checked so the
        # menu reads correctly even before the user changes anything.
        if 10.0 in self._font_size_actions:
            self._font_size_actions[10.0].setChecked(True)
        view_menu.addSeparator()
        # Theme submenu — Light / Dark / System (operator: "let in View to
        # change between light mode and dark mode … and system").  Applies a
        # Fusion palette to the GUI chrome; the plots stay white in both
        # themes.  Exclusive (radio) via a QActionGroup; persisted under the
        # ``theme`` pref and re-applied at launch (see _apply_saved_theme).
        theme_menu = view_menu.addMenu("&Theme")
        self._theme_group = QtGui.QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: Dict[str, "QtGui.QAction"] = {}
        for _mode, _label in (("system", "&System"),
                              ("light", "&Light"),
                              ("dark", "&Dark")):
            act = theme_menu.addAction(_label)
            act.setCheckable(True)
            self._theme_group.addAction(act)
            act.triggered.connect(
                lambda _c=False, m=_mode: self._on_theme_selected(m))
            self._theme_actions[_mode] = act
        view_menu.addSeparator()
        # Gridlines toggle — applies to every experiment plot
        # (pattern preview, staircase, tracking, scope). Default
        # OFF so the trace area stays clean. The Viewer has its
        # own independent gridlines toggle since matplotlib's
        # rendering pipeline differs from pyqtgraph's.
        self._act_grid = view_menu.addAction("&Gridlines")
        self._act_grid.setCheckable(True)
        self._act_grid.setChecked(False)
        self._act_grid.setShortcut("Ctrl+G")
        self._act_grid.toggled.connect(self._on_view_grid_toggled)
        # Camera Monitor dock — additional floatable preview, useful
        # when the operator wants the bench camera on a second
        # monitor or when no experiment tab is active.  The PRIMARY
        # camera UI lives in the ConnectionPanel (device picker) +
        # each experiment tab's embedded CameraStreamPane (live
        # preview beneath the scope plot); this dock is an OPTIONAL
        # extra view that shares the same singleton
        # ``camera_service()`` — no additional camera I/O.
        view_menu.addSeparator()
        self._act_camera = view_menu.addAction("&Camera Monitor (dock)")
        self._act_camera.setCheckable(True)
        self._act_camera.setChecked(False)
        self._act_camera.setShortcut("Ctrl+Shift+C")
        self._act_camera.toggled.connect(self._on_view_camera_toggled)
        # The dock + the StreamPane it wraps are built lazily —
        # see _ensure_camera_dock().
        self._camera_dock: Optional[QtWidgets.QDockWidget] = None
        self._camera_stream_pane = None   # CameraStreamPane; lazy

        # Window menu — basic window-state controls. Mirrors the
        # native window-decoration buttons so users with menu-only
        # navigation (or accessibility tools that surface menu
        # actions) can still maximize / minimize the GUI.
        # Restore is the inverse of Maximize/Minimize: from a
        # maximized OR minimized window it pops back to the prior
        # un-maximised, un-minimised "normal" geometry — the same
        # behaviour as the middle decoration button on most window
        # managers (the one that toggles between "maximize" and
        # "restore down").
        window_menu = self.menuBar().addMenu("&Window")
        act_min = window_menu.addAction("Mi&nimize")
        act_min.setShortcut("Ctrl+M")
        act_min.triggered.connect(self.showMinimized)
        act_max = window_menu.addAction("Ma&ximize")
        act_max.setShortcut("Ctrl+Shift+M")
        act_max.triggered.connect(self._on_window_toggle_maximize)
        act_normal = window_menu.addAction("&Restore")
        act_normal.triggered.connect(self.showNormal)

        # Help menu — version / bug-report / update-check entry
        # points. Short list intentionally — no docs / shortcuts
        # entries today; those can land here once they exist.
        help_menu = self.menuBar().addMenu("&Help")
        act_updates = help_menu.addAction("Check for &updates…")
        act_updates.triggered.connect(self._on_help_check_updates)
        act_bug = help_menu.addAction("&Report a bug…")
        act_bug.triggered.connect(self._on_help_report_bug)
        # Privacy-first electrode-data contribution flow. Opens a
        # dialog that builds an anonymous JSON payload, lets the
        # user opt in to academic-attribution fields per-checkbox,
        # and pre-fills a GitHub Issue body with the result. The
        # dialog never uploads anything by itself; the user reviews
        # the issue on github.com and clicks Submit there. See
        # :class:`stimtest.gui.contribute_dialog.ContributeDialog`
        # for the full flow + privacy guarantees.
        act_contribute = help_menu.addAction(
            "&Contribute electrode data…")
        act_contribute.triggered.connect(self._on_help_contribute_data)
        # Reset the learned per-coating open-circuit potentials.  The
        # operator hit bad Pt values accumulated by earlier PULSAR
        # builds (the baseline-subtraction bugs since fixed) and turned
        # OFF the reference toggle to avoid them — this clears the
        # contaminated store so future captures re-learn from scratch.
        act_reset_ocp = help_menu.addAction(
            "&Reset learned electrode potentials…")
        act_reset_ocp.triggered.connect(self._on_help_reset_potentials)
        # Tissue-damage prediction info — opens an explainer dialog
        # describing the Shannon equation + the modified-Shannon
        # macro/micro caps + the Li et al. 2024 NeurostimML web
        # tool, with clickable DOI links and the web-tool URL.
        # See :meth:`_on_help_damage_info` for the rendered body.
        act_damage = help_menu.addAction(
            "&Tissue damage prediction info…")
        act_damage.triggered.connect(self._on_help_damage_info)
        # NeurostimML local-model installer. Opens a confirmation
        # dialog that warns about pickle-deserialization risk,
        # surfaces the source URL + on-disk path + SHA256 once
        # installed, and exposes Install / Re-download / Uninstall
        # buttons. See :meth:`_on_help_install_neurostimml`.
        act_install_ml = help_menu.addAction(
            "&Install NeurostimML model…")
        act_install_ml.triggered.connect(self._on_help_install_neurostimml)
        # Last-calibration timestamp — opens a small info dialog
        # with the saved-at date for the per-channel PlexStim
        # calibration. When no calibration has been recorded yet,
        # the dialog tells the user how to run one (Run →
        # Calibrate… opens the wizard). Useful as a quick "do I
        # need to re-calibrate?" check without leaving the GUI.
        act_last_cal = help_menu.addAction("&Last stimulator verification…")
        act_last_cal.triggered.connect(self._on_help_last_calibration)
        help_menu.addSeparator()
        act_about = help_menu.addAction("&About…")
        act_about.triggered.connect(self._on_help_about)

        # Admin menu — login/logout and catalog management.
        # "Manage Custom Catalog…" is hidden until the admin logs in.
        admin_menu = self.menuBar().addMenu("Ad&min")
        self._act_admin_login = admin_menu.addAction("Log &In…")
        self._act_admin_login.triggered.connect(self._on_admin_login)
        self._act_admin_logout = admin_menu.addAction("Log &Out")
        self._act_admin_logout.triggered.connect(self._on_admin_logout)
        self._act_admin_logout.setVisible(False)
        admin_menu.addSeparator()
        self._act_admin_catalog = admin_menu.addAction(
            "&Manage Custom Catalog…")
        self._act_admin_catalog.triggered.connect(self._on_admin_catalog)
        self._act_admin_catalog.setEnabled(False)
        admin_menu.addSeparator()
        # Plugin-host profile import / export (e.g. the CWRU custom
        # shapes).  Import is OPEN to all users — it only ADDS a
        # profile + its shapes to the registry; logging in to USE them
        # still requires the password.  Export is ADMIN-gated because
        # distributing a profile is a privileged action.
        self._act_import_profile = admin_menu.addAction(
            "Import &Profile…")
        self._act_import_profile.triggered.connect(self._on_import_profile)
        self._act_export_profile = admin_menu.addAction(
            "&Export Profile…")
        self._act_export_profile.triggered.connect(self._on_export_profile)
        self._act_export_profile.setEnabled(False)  # admin-gated

        # Status bar
        self.statusBar().showMessage(
            "Ready. Use the Connection panel in the Setup tab to attach "
            "the stimulator and oscilloscope (or run in simulator mode).")

        # Per-input change logging — gate every "operator typed / picked
        # X" message in the Setup-tab and PatternPanel handlers so the
        # restore-from-prefs signal burst at startup doesn't flood the
        # log with stale values the operator didn't actually touch.
        # Flipped to True at the end of __init__ AFTER
        # ``_load_prefs_into_tabs()`` + its priming calls return, so
        # every subsequent input event (user types in a spinbox,
        # picks a combo entry, toggles a checkbox) emits one
        # "Setup: <field> = <value>" line.  See ``_log_setup_change``.
        self._log_setup_changes = False

        # Wiring
        self.conn.connected.connect(self._on_connected)
        self.conn.disconnected.connect(self._on_disconnected)
        # Route ConnectionPanel events to BOTH the status bar (transient)
        # AND the LogPane (persistent + mirrored to the .txt session log).
        # Without the LogPane wire, all the high-level init / setup /
        # connect / disconnect messages would disappear after a couple of
        # seconds when the status bar's next message overwrites them.
        self.conn.log.connect(self.statusBar().showMessage)
        self.conn.log.connect(self.log_pane.log)
        # Scope up/down → setup tab seeds default mapping or blanks it.
        self.conn.scopeConnected.connect(self._on_scope_connected)
        # Run-Stimulator-Verification button on the ConnectionPanel: switch
        # focus to the embedded Calibration tab.
        self.conn.calibrationRequested.connect(self._on_run_calibrate)
        self.setup_tab.arrayChanged.connect(self._on_array_changed)
        # Electrode-config sub-inputs (return / reference electrode, geometry,
        # connector, remember-potential toggle) that don't change the generic
        # `array = …` description carry their OWN ready-to-log string here, so
        # every input + selection is indicated in the log pane (operator:
        # "changing the return electrode, there was not new text … make sure
        # that all input and selection are indicated").
        self.setup_tab.settingChanged.connect(self._log_setup_change)
        self.setup_tab.potentialLimitsChanged.connect(self._on_limits_changed)
        self.setup_tab.autoExportXlsxChanged.connect(self._on_auto_export_xlsx_changed)
        self.setup_tab.autoSavePlotsChanged.connect(self._on_auto_save_plots_changed)
        self.setup_tab.sessionFilenameChanged.connect(self._on_log_filename_changed)
        self.setup_tab.emailNotificationsChanged.connect(
            self._on_email_notifications_changed)
        self.setup_tab.userIdentityChanged.connect(
            self._on_user_identity_changed)
        self.setup_tab.smsRecipientChanged.connect(
            self._on_sms_recipient_changed)
        self.setup_tab.sessionSubjectChanged.connect(
            self._on_session_subject_changed)
        # Environment combo → forward to every experiment tab so
        # the pre-run damage-screen modal uses the right posture.
        # See :class:`stimtest.environments.EnvironmentPreset` for
        # the posture mapping (info / warn / alert) keyed off the
        # short_code.
        self.setup_tab.environmentChanged.connect(
            self._on_environment_changed)
        self.setup_tab.aliasesChanged.connect(self._on_aliases_changed)
        self.setup_tab.experimentRequested.connect(self._on_experiment_requested)
        # Pure-navigation request from the Setup tab's "Go to test
        # parameters tab →" button. The dropdown already fires
        # ``experimentRequested`` on selection change, so by the time
        # this signal arrives the right experiment's params page is
        # already loaded and we only need to switch tabs.
        self.setup_tab.testParamsRequested.connect(
            self._on_test_params_requested)
        self.setup_tab.savePathChanged.connect(self._on_save_path_changed)
        # Acquisition (mode + n_avg) flows from the Setup tab to every
        # experiment tab so the runner-side setup applies what the
        # user picked.
        self.setup_tab.acquisitionChanged.connect(self._on_acq_changed)
        self.setup_tab.horizontalScalingChanged.connect(
            self._on_horiz_scaling_changed)
        self.setup_tab.triggerSourceChanged.connect(self._on_trigger_source_changed)
        self.setup_tab.digitalTriggerChanged.connect(self._on_digital_trigger_changed)
        # The operator-facing trigger-edge selector was removed; the
        # experiment runner auto-resolves slope from the trigger-source
        # rules (digital → RISE, I_mon → phase-1 polarity) at run start.
        # VT hides Fixed charge density mode when all electrodes share
        # an area — same-area arrays make that mode redundant.
        self.setup_tab.sameAreaChanged.connect(self.vt_tab.set_same_area)
        # ---- Log-only setup signals (no other subscriber) -----------
        # These signals propagate state that doesn't need to be
        # forwarded to experiment tabs / hardware — they just need to
        # appear in the operator's session log so a post-mortem can
        # reconstruct what was changed.  Lambdas so the gate inside
        # ``_log_setup_change`` keeps the startup signal burst quiet.
        try:
            self.setup_tab.spargeGasChanged.connect(
                lambda gas: self._log_setup_change(f"sparge gas = {gas}"))
        except Exception:
            pass
        try:
            self.setup_tab.sameAreaChanged.connect(
                lambda on: self._log_setup_change(
                    f"electrodes share area = {'YES' if on else 'NO'}"))
        except Exception:
            pass
        # Auto-discharge plumbing. UI lives in each pattern panel
        # (below the rate row); persistence + device push lives in
        # the connection panel. Wire every pattern panel's toggle so
        # that flipping any one of them syncs every sibling AND
        # forwards to ConnectionPanel.apply_auto_discharge for the
        # device-side push + prefs save.
        self._auto_discharge_seed_pref()
        for tab in self._experiment_tabs():
            pp = getattr(tab, "pattern_panel", None)
            if pp is not None:
                pp.autoDischargeToggled.connect(self._on_auto_discharge_toggled)

        # Apply defaults
        self._on_array_changed(self.setup_tab.current_array())
        for tab in self._experiment_tabs():
            tab.set_save_dir(self.save_dir)
            # Wire the Setup-tab snapshot provider so every experiment
            # tab can stamp ``session.test.extras['setup_snapshot']``
            # at start-run time. The provider is the live SetupTab
            # method, so the snapshot reflects whatever the user has
            # changed up to the moment they hit Start.
            tab.set_setup_snapshot_provider(self.setup_tab.setup_snapshot)

        # Snapshot the *as-constructed* state of every tab BEFORE any
        # saved prefs are restored — this captures the code-defined
        # defaults exactly as if the user had just installed a fresh
        # build. The "File → Reset to default" handler later applies
        # this snapshot back to the live widgets (and overwrites the
        # auto-save file) when the user wants a clean slate.
        self._default_prefs_payload = self._collect_prefs_payload()

        # Restore previous-session prefs. Done last so all the wiring is in
        # place — e.g. setup_tab.restore_prefs will fire arrayChanged, and
        # we want that signal to actually reach the experiment tabs.
        self._load_prefs_into_tabs()
        # Prime the experiment tabs with the Setup tab's current
        # identity / session-subject text. ``QLineEdit.setText`` (used
        # by ``restore_prefs``) doesn't fire ``editingFinished``, so the
        # signal-driven path above wouldn't reach the tabs on launch.
        self._on_user_identity_changed(
            self.setup_tab.current_user_name(),
            self.setup_tab.current_user_email())
        self._on_session_subject_changed(
            self.setup_tab.current_session_subject())
        # Seed experiment tabs with the restored Environment value
        # — ``setText`` / ``setCurrentIndex`` calls in the prefs
        # restore path don't fire ``environmentChanged``, so we
        # publish the value once explicitly here so the tabs
        # don't enter their first run on the bare PBS default.
        try:
            self._on_environment_changed(
                self.setup_tab.current_environment_short(),
                self.setup_tab.current_environment_custom_text(),
            )
        except Exception:
            pass
        self._on_auto_save_plots_changed(
            self.setup_tab.current_auto_save_plots(),
            self.setup_tab.current_auto_save_plots_format(),
            self.setup_tab.current_auto_save_plots_dpi())
        # Discover and load any installed ``stimtest_*`` extension
        # packages.  Extensions register additional login profiles
        # (and the restricted shapes those profiles unlock) by
        # calling ``register_extension_profile`` in their
        # ``__init__.py``.  Done AFTER prefs restore so any
        # extension-owned prefs sections (the extension reads them
        # itself via ``load_prefs`` / its own key) see the same
        # restored payload the main app sees; done BEFORE the log
        # gate flip so the discovery messages don't pollute the
        # operator-facing setup log with one "extension loaded" line
        # per package on every launch.  Best-effort: an extension
        # that raises at import time is logged and skipped — PULSAR
        # continues to launch with the remaining extensions plus
        # the built-in Admin + None profiles.
        try:
            self._load_extensions()
        except Exception as _e:
            # Hard failure in the loader itself (not an individual
            # extension) — keep launching anyway, but record it.
            self.log_pane.log(
                f"Extension discovery failed: "
                f"{type(_e).__name__}: {_e}")
        # Re-register profiles imported via Admin → Import Profile… in
        # a prior session.  AFTER ``_load_extensions`` so a pip-
        # installed extension of the same name wins a hash collision
        # (the persisted copy is then a redundant no-op).
        try:
            self._load_imported_profiles()
        except Exception as _e:
            self.log_pane.log(
                f"Imported-profile reload failed: "
                f"{type(_e).__name__}: {_e}")
        # All prefs restored + downstream tabs primed.  From here on,
        # every Setup-tab / PatternPanel change reflects a user-driven
        # input event the operator should see in the log.  Flip the
        # gate so ``_log_setup_change`` starts emitting.
        self._log_setup_changes = True
        # Wire pattern-panel changes from every experiment tab through
        # a single debounced log slot so the operator gets a "Pattern:
        # …" line whenever they finish editing a parameter.  The log
        # listens to ``patternCommitted`` (Enter / focus-out / discrete
        # change) — NOT ``patternChanged`` (per keystroke) — so typing a
        # value doesn't spam a line per key (operator: "only print after
        # return/enter is pressed or clicked out of the input").  The
        # debounce still collapses any commit cascade into one line.
        for _tab in self._experiment_tabs():
            _pp = getattr(_tab, "pattern_panel", None)
            if _pp is not None:
                try:
                    _sig = getattr(_pp, "patternCommitted", None)
                    if _sig is not None:
                        _sig.connect(self._on_pattern_changed_log)
                    else:                       # back-compat for older panels
                        _pp.patternChanged.connect(self._on_pattern_changed_log)
                except Exception:
                    pass
                # Inline average-count edit (beside the acquisition-time
                # readout) → push into the Setup tab's spin, the single
                # source of truth.  Its acquisitionChanged signal then
                # re-broadcasts the value to EVERY tab's readout + logs
                # the change like any other Setup input.  Connected
                # post-restore so the construction burst can't fire it.
                try:
                    _pp.acqNavgEdited.connect(self._on_inline_navg_edited)
                except Exception:
                    pass
                # Live pulse-rate feed → the Setup tab's capture-time↔count
                # conversion (Setup has no rate of its own).  patternChanged
                # is per-keystroke so the derived count tracks the rate as the
                # user drags it; the setter no-ops on an unchanged rate.
                try:
                    _pp.patternChanged.connect(self._push_active_rate_to_setup)
                except Exception:
                    pass
            # Test-parameters inputs (channel/combo selection + de-selection,
            # duration, ramp mode, stop conditions, camera capture, …) carry
            # their own ready-to-log string via ``paramChanged`` (mirrors
            # SetupTab.settingChanged).  Connected HERE — after prefs
            # restore — so the construction/restore burst never logs; the
            # ``_log_setup_change`` gate is a second guard.
            try:
                _tab.paramChanged.connect(self._log_setup_change)
            except Exception:
                pass
        # Also: now that prefs are restored, log the resolved initial
        # setup state once so the session log starts with a complete
        # snapshot the operator can refer back to later.  Without this
        # the first log entries would be the runner messages, with no
        # record of what coating / channels / environment were in
        # effect at run start.
        try:
            self._log_initial_setup_snapshot()
        except Exception:
            pass
        # Seed the Setup tab's capture-time↔count conversion with the active
        # experiment's pulse rate (so a restored capture-time derives the
        # right count before the first pattern edit).
        try:
            self._push_active_rate_to_setup()
        except Exception:
            pass
        # Apply the saved colour theme (View → Theme: system / light / dark)
        # + set the menu radio + auto-follow the OS scheme in "system" mode.
        try:
            self._apply_saved_theme()
        except Exception:
            pass
        # Also save prefs whenever the user clicks Start on any experiment
        # tab — that snapshot is what they actually want preserved if the
        # machine crashes mid-run. closeEvent (below) covers normal quit.
        for tab in self._experiment_tabs():
            tab.start_btn.clicked.connect(self._save_prefs_from_tabs)
        # Auto-switch from Test parameters to the active Experiment tab
        # when the user clicks Start — they're done configuring and
        # want to watch the run.  Only switches when the click came
        # from Test parameters (or any other non-experiment tab); if
        # they're already on the experiment tab, we don't fight them.
        # Wired here in main_window rather than inside each
        # _BaseExperimentTab because only main_window knows the
        # top-level tab structure.
        for tab in self._experiment_tabs():
            tab.start_btn.clicked.connect(
                lambda *_, _t=tab: self._auto_switch_to_experiment_tab(_t))
        # Run-lock: when an experiment starts, disable the Setup tab so
        # the user can't reconfigure device / hardware mid-run. The tab
        # bar itself stays enabled, so Setup is still selectable for
        # viewing — only its inputs grey out.
        for tab in self._experiment_tabs():
            tab.runStateChanged.connect(self._on_run_state_changed)
        # Pause/Resume label sync: every experiment tab has a
        # checkable ``pause_btn``; mirror its toggled state on the
        # Run-menu Pause action so the menu reads "Pause" while
        # running and "Resume" while paused, regardless of which
        # control the user clicked to flip the state.
        for tab in self._experiment_tabs():
            pb = getattr(tab, "pause_btn", None)
            if pb is not None:
                pb.toggled.connect(self._on_pause_btn_toggled)

    def _experiment_tabs(self):
        return [self.vt_tab, self.sp_tab, self.cp_tab, self.lp_tab,
                self.ps_tab, self.eis_tab]

    # ----------------------------------------------------------- auto-discharge
    def _auto_discharge_seed_pref(self) -> None:
        """Push the persisted auto-discharge preference into every
        pattern panel's checkbox at startup.

        ``set_auto_discharge_silent`` blocks the toggled signal so
        seeding doesn't fire the warning dialog or re-emit the
        ``autoDischargeToggled`` signal.
        """
        try:
            pref = self.conn.auto_discharge_pref()
        except Exception:
            pref = True
        for tab in self._experiment_tabs():
            pp = getattr(tab, "pattern_panel", None)
            if pp is not None:
                pp.set_auto_discharge_silent(pref)

    def _on_auto_discharge_toggled(self, checked: bool) -> None:
        """A pattern panel's auto-discharge checkbox toggled.

        Synchronise sibling pattern panels (every experiment tab has
        its own checkbox; they must all read the same to avoid
        confusing the user) and push the new state to the live
        device + persist via :meth:`ConnectionPanel.apply_auto_discharge`.
        """
        for tab in self._experiment_tabs():
            pp = getattr(tab, "pattern_panel", None)
            if pp is not None:
                pp.set_auto_discharge_silent(checked)
        self.conn.apply_auto_discharge(checked)

    def _on_connected(self, stim, scope):
        for tab in self._experiment_tabs():
            tab.set_hardware(stim, scope)
        # Re-arm the Test-parameters entry sync: the scope settings
        # (record length / acquisition / trigger) couldn't have been
        # pre-applied while disconnected, so the next entry into Test
        # parameters must run the sync + hardware pre-apply even if
        # Setup itself hasn't changed since.
        self._setup_dirty_for_test = True
        # Embedded Calibration tab tracks the live handles too — only if
        # it has actually been instantiated (it's created lazily on first
        # "Run Calibration" press).
        if self.cal_tab is not None:
            self.cal_tab.set_hardware(stim=stim, scope=scope)
        # Route every SCPI command + every PlexStim SDK call through
        # the shared LogPane (which mirrors to disk).  The
        # ConnectionPanel wires a thread-safe logger BEFORE open()
        # so the connect handshake itself is captured; here we
        # only re-bind if a logger isn't already in place.  Avoids
        # mid-session identity churn of the cmd_logger reference
        # (the worker thread's QueuedConnection logger and the
        # GUI-thread direct logger both land at the same LogPane,
        # but it's confusing to swap them under our own feet once
        # connection is established).
        if getattr(scope, "cmd_logger", None) is None:
            try:
                scope.cmd_logger = self.log_pane.log
            except Exception:
                pass
        if getattr(stim, "cmd_logger", None) is None:
            try:
                stim.cmd_logger = self.log_pane.log
            except Exception:
                pass
        # Print a concise hardware summary to the log so every session
        # has its instrumentation captured in the .txt mirror.  Useful
        # later for matching .npz captures to the exact device + firmware.
        try:
            si = getattr(stim, "info", None)
            sc = getattr(scope, "info", None)
            if si is not None:
                self.log_pane.log(
                    f"[stim] connected: serial={getattr(si,'serial_number','')} "
                    f"firmware={getattr(si,'firmware','')} "
                    f"channels={getattr(si,'n_channels','?')} "
                    f"V_mon={getattr(si,'vmon_scaling_v_per_v','?')} V/V "
                    f"I_mon={getattr(si,'imon_scaling_v_per_ua','?')} V/uA")
            if sc is not None:
                self.log_pane.log(
                    f"[scope] connected: {getattr(sc,'make','')} "
                    f"{getattr(sc,'model','')} "
                    f"serial={getattr(sc,'serial','')} "
                    f"fw={getattr(sc,'firmware','')} "
                    f"channels={getattr(sc,'n_channels','?')} "
                    f"ext_trigger={getattr(sc,'has_ext_trigger','?')} "
                    f"divs={getattr(scope, '_n_horiz_divs', '?')}")
        except Exception:
            pass
        self.statusBar().showMessage(f"Connected to {scope.info.make} {scope.info.model}.")
        # Both hardware halves are up — unlock Run → Calibrate so the
        # user can jump to the Calibration tab.
        self._act_calibrate.setEnabled(True)
        self._act_calibrate.setToolTip(
            "Switch to the Verification tab to run the PlexStim test-board "
            "stimulator verification sweep.")

    def _on_disconnected(self):
        # Unwire the driver loggers BEFORE clearing handles so a final
        # close() command on either device still shows up in the log.
        for hw in (getattr(self.conn, "stim", None),
                   getattr(self.conn, "scope", None)):
            if hw is not None:
                try:
                    hw.cmd_logger = None
                except Exception:
                    pass
        for tab in self._experiment_tabs():
            tab.clear_hardware()
        # Clear cal-tab handles so the gate goes back to "not connected"
        # (only if the tab has been created).
        if self.cal_tab is not None:
            self.cal_tab.set_hardware(stim=None, scope=None)
        self.statusBar().showMessage("Disconnected.")
        # One (or both) of the hardware halves dropped — re-lock
        # Run → Calibrate. The verification sweep needs both stim and scope
        # live to drive each channel + capture V_mon / I_mon.
        self._act_calibrate.setEnabled(False)
        self._act_calibrate.setToolTip(
            "Initialize the PlexStim and connect the oscilloscope "
            "first (Setup tab → Connection panel).")

    def _on_calibration_done(self):
        """Done — CLOSE the verification tab (operator: "When pressing Done in
        the verification, close the verification tab") and return to Setup.

        Verification results are only persisted via the separate "Save
        verification…" button, so if a completed sweep hasn't been saved,
        PROMPT first (the operator lost a verification by clicking Done
        without saving).  Cancel aborts the close.  The tab is destroyed so a
        later "Run Verification" re-creates it fresh."""
        from PyQt6.QtWidgets import QMessageBox
        cal = self.cal_tab
        if cal is not None and getattr(cal, "has_unsaved_results", None) \
                and cal.has_unsaved_results():
            r = QMessageBox.question(
                self, "Save verification?",
                "This verification hasn't been saved to disk.  Save it "
                "before closing?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save)
            if r == QMessageBox.StandardButton.Cancel:
                return
            if r == QMessageBox.StandardButton.Save:
                cal._on_save_calibration()
                if cal.has_unsaved_results():   # save failed / nothing to save
                    return                       # keep the tab open
        try:
            self.conn._refresh_cal_label()
        except Exception:
            pass
        # Remove + destroy the verification tab so it's gone from the tab bar
        # (a later Run Verification re-creates it via _open_calibration_tab).
        if cal is not None:
            idx = self.tabs.indexOf(cal)
            if idx >= 0:
                self.tabs.removeTab(idx)
            cal.deleteLater()
            self.cal_tab = None
        idx = self.tabs.indexOf(self.setup_tab)
        if idx >= 0:
            self.tabs.setCurrentIndex(idx)

    # ---- Setup-input change logging --------------------------------
    def _log_setup_change(self, msg: str) -> None:
        """Log a Setup-tab / pattern-panel input change to the LogPane.

        No-op while ``self._log_setup_changes`` is False — the flag is
        flipped to True at the end of ``__init__`` AFTER
        ``_load_prefs_into_tabs()`` and its priming calls finish, so
        the startup signal burst (restored values cascading through
        every connected handler) doesn't flood the log with stale
        entries the operator didn't actually type.

        Each call emits exactly one line prefixed "Setup: " so the
        operator can later grep the .txt session log for every input
        event without false positives from runner / scope messages.
        """
        if not getattr(self, "_log_setup_changes", False):
            return
        # A Setup parameter changed → the active experiment tab's cached
        # view of Setup is now stale; re-pull it the next time the
        # operator opens the Test parameters page.
        self._setup_dirty_for_test = True
        try:
            self.log_pane.log(f"Setup: {msg}")
        except Exception:
            pass

    def _on_top_tab_changed(self, *_):
        """Top-level tab navigation hook.

        Keeps the Start/Pause/Stop row anchored to the focused tab AND,
        when the operator opens the Test parameters page, pulls the
        current Setup parameters into the active experiment tab if they
        haven't been synced since the last Setup change (the dirty
        flag).  This guarantees the Test parameters page always reflects
        Setup on entry.
        """
        self._refresh_button_row_placement()
        try:
            if (self.tabs.currentWidget() is self._test_params_tab
                    and getattr(self, "_setup_dirty_for_test", False)):
                self._sync_setup_into_active_tab()
                self._setup_dirty_for_test = False
        except Exception:
            pass

    def _sync_setup_into_active_tab(self) -> None:
        """Pull the current Setup-tab parameters into the ACTIVE
        experiment tab so the Test parameters page reflects Setup on
        entry — covering params whose widgets don't re-fire their change
        signal on prefs restore (aliases / limits) and so never reached
        the tab via the normal forwarder path.

        The ARRAY is deliberately NOT re-applied here: ``set_array``
        rebuilds the channel grid and CLEARS the operator's channel
        selections (``channel_selector._GridCanvas.set_array`` →
        ``_actives.clear()``), and genuine array changes already
        propagate via the ``arrayChanged`` forwarder.  Everything synced
        here is cache-only — no operator-input is clobbered.
        """
        if self._current_exp_code is None:
            return
        entry = self._exp_tab_by_code.get(self._current_exp_code)
        if not entry:
            return
        tab = entry[0]
        st = self.setup_tab
        for _fn in (
            lambda: tab.set_environment(
                st.current_environment_short(),
                st.current_environment_custom_text()),
            lambda: tab.set_aliases(st.current_aliases()),
            lambda: tab.set_potential_limits(
                *st.current_potential_limits()),
            lambda: tab.set_user_identity(
                st.current_user_name(), st.current_user_email()),
            lambda: tab.set_sms_recipient(
                st.current_user_phone(), st.current_user_carrier()),
            lambda: tab.set_session_subject(
                st.current_session_subject()),
            # OSCILLOSCOPE settings (operator: "setup settings, including
            # oscilloscope, was not set when I moved to Test parameters").
            # The acquisition mode/NUMAVg + trigger source/type are pushed
            # by the forwarders on interactive change, but — like aliases
            # / limits — they don't reliably re-fire on prefs restore or
            # scope-connect auto-mapping, so pull them here too.  Trigger
            # source + digital flag go together (coherent (source, type)
            # pair, mirroring the forwarders).
            lambda: tab.set_acquisition(*st.current_acquisition()),
            lambda: tab.set_trigger_source(st.current_trigger_source()),
            lambda: tab.set_digital_trigger(st.is_digital_trigger()),
            lambda: tab.set_session_identity(st.current_notebook(),
                                             st.current_session_stem()),
        ):
            try:
                _fn()
            except Exception:
                # Best-effort per param — a stale tab/Setup API (e.g. a
                # test stub) must not break tab navigation.
                pass
        # HARDWARE pre-apply (operator: "set all necessary oscilloscope
        # settings when entering the Test parameters tab, including
        # record length").  Runs AFTER the cache pulls above so the
        # just-synced acquisition / trigger state is what lands on the
        # scope.  Applies record length + acquisition + trigger; the
        # driver setters are idempotent so unchanged settings cost
        # nothing, and the method itself no-ops without a connected
        # scope or while a run is starting / in flight.
        try:
            tab.apply_scope_settings_on_entry()
        except Exception:
            pass

    def _describe_array(self, array) -> str:
        """One-line summary of an ElectrodeArray for the log pane.

        Captures the fields the operator most commonly tweaks
        (device label, n_sites, coating, surface area).  Best-effort:
        unknown / missing attributes fall back to repr().
        """
        if array is None:
            return "(none)"
        try:
            parts: List[str] = []
            label = (getattr(array, "device_label", None)
                     or getattr(array, "name", None)
                     or getattr(array, "label", None))
            if label:
                parts.append(str(label))
            sites = getattr(array, "sites", None) or []
            n = len(sites)
            if n:
                parts.append(f"{n} sites")
                # Coating + area from the first site — usually
                # identical across the array unless per-site overrides
                # are in use.  Mention "(per-site varies)" if not.
                _site0 = sites[0]
                coat = getattr(_site0, "coating", None)
                if coat:
                    coats = {getattr(s, "coating", None) for s in sites}
                    if len(coats) > 1:
                        parts.append(f"coating={coat}+others")
                    else:
                        parts.append(f"coating={coat}")
                area = getattr(_site0, "surface_area_um2", None)
                if area:
                    areas = {getattr(s, "surface_area_um2", None)
                             for s in sites}
                    if len(areas) > 1:
                        parts.append(f"area={area:.0f} µm² (varies)")
                    else:
                        parts.append(f"area={area:.0f} µm²")
            return ", ".join(parts) if parts else repr(array)
        except Exception:
            return repr(array)

    def _on_pattern_changed_log(self, pattern) -> None:
        """Debounced pattern-changed slot: emits one "Pattern: …" log
        line after the user stops fiddling with pattern inputs.

        ``patternChanged`` fires on every spinbox tick (`valueChanged`
        is per-tick, not per-edit), so a single amplitude edit can
        burst dozens of signals.  Logging each would drown the pane.
        We capture the latest pattern and arm a 400 ms QTimer; the
        slot only emits when the timer fires without being re-armed
        in the meantime.
        """
        self._pending_log_pattern = pattern
        # Lazy-create the debounce timer the first time we're called.
        t = getattr(self, "_pattern_log_timer", None)
        if t is None:
            t = QtCore.QTimer(self)
            t.setSingleShot(True)
            t.setInterval(400)
            t.timeout.connect(self._flush_pattern_change_log)
            self._pattern_log_timer = t
        t.start()  # re-arm; previous start is cancelled

    def _flush_pattern_change_log(self) -> None:
        """Emit the most recently received PulsePattern as a one-line
        summary.  Called by the debounce timer; harmless if the
        pending pattern is None (early signal before any wiring)."""
        if not getattr(self, "_log_setup_changes", False):
            return
        pat = getattr(self, "_pending_log_pattern", None)
        if pat is None:
            return
        try:
            # Multi-line tabbed body (one line per phase / delay / rate);
            # the "pattern =" header sits on its own line, the tabbed body
            # follows (operator: separated tabbed lines).
            desc = self._describe_pattern(pat)
            # De-dupe: a COMMIT (Enter / focus-out) on an input that leaves the
            # pattern UNCHANGED — e.g. a focus-out with no edit, or the inline
            # acq-average spinbox (not a pattern parameter) — must not log a
            # duplicate line (operator dislikes redundant log lines).
            if desc == getattr(self, "_last_logged_pattern_desc", None):
                return
            self._last_logged_pattern_desc = desc
            self._log_setup_change(f"pattern =\n{desc}")
        except Exception:
            pass

    def _describe_pattern(self, pat) -> str:
        r"""Multi-line, tab-indented summary of a PulsePattern for the log.

        ONE tabbed line per component — each phase (signed amplitude ×
        width + shape), each following delay (interphase between phases,
        discharge after the LAST phase — ``Phase.delay_after_us``), and
        the rate — so a multi-phase / shaped pattern is readable at a
        glance (operator: "separated lines (tabbed) for each phase,
        interphase delay, and discharge delay").  The caller prepends the
        ``pattern =`` header line, so this returns just the tabbed BODY
        (each line begins with a literal TAB).

        The SHAPE is shown per phase (``rectangular`` / ``sinusoidal`` /
        ``exp-decay`` / ``linear-increasing`` / …) so otherwise-identical
        phases are distinguishable (a raw shape id's underscore renders as
        a hyphen).  A zero delay is omitted (that phase has no following
        gap).  Rate is "pps" (operator), never "Hz".  Best-effort: any
        attribute lookup error degrades to a tabbed repr(pat).

        Example (biphasic sinusoidal, 20 µs interphase + discharge)::

            \tPhase 1: -50.0 µA × 200 µs, sinusoidal
            \tInterphase delay: 20 µs
            \tPhase 2: +50.0 µA × 200 µs, sinusoidal
            \tDischarge delay: 20 µs
            \tRate: 50 pps
        """
        if pat is None:
            return "\t(none)"
        try:
            phases = getattr(pat, "phases", []) or []
            n = len(phases)
            lines: List[str] = []
            for i, p in enumerate(phases):
                w = float(getattr(p, "width_us", 0.0) or 0.0)
                a = float(getattr(p, "amplitude_ua", 0.0) or 0.0)
                shape = str(getattr(p, "shape", None)
                            or "rectangular").replace("_", "-")
                lines.append(
                    f"\tPhase {i + 1}: {a:+.1f} µA × {w:.0f} µs, {shape}")
                # The delay FOLLOWING this phase: interphase between
                # phases, discharge after the last one (the last phase
                # always carries the discharge delay — even a monophasic
                # pulse, per pattern_panel.pattern()).
                d = float(getattr(p, "delay_after_us", 0.0) or 0.0)
                if d > 0.0:
                    kind = "Discharge" if i == n - 1 else "Interphase"
                    lines.append(f"\t{kind} delay: {d:.0f} µs")
            if not lines:
                return "\t(no phases)"
            rate = float(getattr(pat, "rate_hz", 0.0) or 0.0)
            _burst = bool(getattr(pat, "is_burst", False))
            if rate > 0:
                # "pps" (pulses per second), NOT "Hz" (operator).  In burst
                # mode the pulse rate IS the intra-burst rate — label it so.
                lines.append(
                    f"\t{'Intra-burst rate' if _burst else 'Rate'}: {rate:g} pps")
            if _burst:
                # The overall burst structure (N pulses / burst period).  The
                # burst REPETITION rate is bursts/s (not a pulse rate → not pps).
                lines.append(
                    f"\tBurst: {pat.pulses_per_burst} pulses / "
                    f"{pat.burst_period_us:.0f} µs "
                    f"(burst rate {pat.burst_rate_hz:g} /s, inter-burst gap "
                    f"{pat.inter_burst_gap_us:.0f} µs, "
                    f"{pat.effective_pulse_rate_hz:g} pps overall)")
            return "\n".join(lines)
        except Exception:
            return f"\t{pat!r}"

    def _log_initial_setup_snapshot(self) -> None:
        """Emit a one-time multi-line snapshot of the resolved setup at
        startup so the session .txt log starts with a complete record
        of what the operator was working with.

        Called once at the end of ``__init__`` after prefs restoration
        and the priming calls.  Operator-visible context for every
        run that follows.
        """
        # Force-enable logging for this snapshot — bypass the gate
        # because this call IS the first legitimate post-restore
        # message; the gate is for subsequent edits.
        original = getattr(self, "_log_setup_changes", False)
        self._log_setup_changes = True
        try:
            self.log_pane.log(
                "── Setup snapshot (initial / restored from prefs) ──")
            try:
                arr = self.setup_tab.current_array()
                self._log_setup_change(f"array = {self._describe_array(arr)}")
            except Exception:
                pass
            try:
                env = self.setup_tab.current_environment_short()
                custom = self.setup_tab.current_environment_custom_text()
                label = (custom.strip() or env) if env == "custom" else env
                self._log_setup_change(f"environment = {label}")
            except Exception:
                pass
            try:
                # SCPI mode name + the effective count (derived from the
                # capture time in time mode) — same payload the runner reads.
                mode, n_avg = self.setup_tab.current_acquisition()
                self._log_setup_change(
                    f"acquisition = {mode}, n_avg = {n_avg}")
            except Exception:
                pass
            try:
                aliases = self.setup_tab.current_aliases()
                if aliases:
                    desc = ", ".join(f"{k}={v}" for k, v in sorted(
                        aliases.items()))
                    self._log_setup_change(f"channel aliases: {desc}")
            except Exception:
                pass
            try:
                name = self.setup_tab.current_user_name()
                email = self.setup_tab.current_user_email()
                self._log_setup_change(
                    f"operator = {name or '(unset)'} "
                    f"<{email or 'no-email'}>")
            except Exception:
                pass
            try:
                subj = self.setup_tab.current_session_subject()
                self._log_setup_change(f"session subject = {subj}")
            except Exception:
                pass
        finally:
            self._log_setup_changes = original

    def _on_array_changed(self, array):
        # Many electrode sub-inputs (return / reference electrode, geometry,
        # connector) re-emit ``arrayChanged`` for their downstream metadata but
        # DON'T change this generic device/coating/area description — those get
        # their own explicit `settingChanged` log line instead.  De-dupe here so
        # a return-electrode tweak doesn't ALSO spam an identical `array = …`
        # line (operator dislikes redundant log spam).  Only log when the
        # description actually changed from the last one we logged.
        desc = self._describe_array(array)
        if desc != getattr(self, "_last_array_desc", None):
            self._last_array_desc = desc
            self._log_setup_change(f"array = {desc}")
        for tab in self._experiment_tabs():
            tab.set_array(array)

    def _on_auto_export_xlsx_changed(self, on: bool):
        """Setup-tab toggle changed — broadcast to every experiment tab.
        Each tab caches the flag and applies it when constructing the
        next RunnerWorker, so a toggle mid-session affects subsequent
        runs without restart.
        """
        self._log_setup_change(
            f"auto-export .xlsx = {'ON' if on else 'OFF'}")
        for tab in self._experiment_tabs():
            tab.set_auto_export_xlsx(on)

    def _on_auto_save_plots_changed(self, on: bool, fmt: str,
                                    dpi: int = 600):
        """Setup-tab plots-auto-save toggle (format combo or DPI spinbox)
        changed — broadcast to every experiment tab so the next
        ``RunnerWorker`` picks up the new (on, fmt, dpi) tuple."""
        self._log_setup_change(
            f"auto-save plots = {'ON' if on else 'OFF'}"
            + (f" (format = {fmt}, {int(dpi)} DPI)" if on else ""))
        for tab in self._experiment_tabs():
            tab.set_auto_save_plots(on, fmt, dpi)

    def _on_email_notifications_changed(self, on: bool):
        """Same forwarding shape as auto-export — every experiment tab
        caches the flag and applies it on the next RunnerWorker.
        """
        self._log_setup_change(
            f"email notifications = {'ON' if on else 'OFF'}")
        for tab in self._experiment_tabs():
            tab.set_email_notifications(on)

    def _on_user_identity_changed(self, name: str, email: str):
        """Forward the recipient name + email from the Setup tab into
        every experiment tab so the next run picks them up for the
        completion / failure email."""
        self._log_setup_change(
            f"operator = {name or '(unset)'} <{email or 'no-email'}>")
        for tab in self._experiment_tabs():
            tab.set_user_identity(name, email)

    def _on_sms_recipient_changed(self, phone: str, carrier: str):
        """Forward the run-end text-alert recipient (phone + carrier) from
        the Setup tab into every experiment tab."""
        self._log_setup_change(
            f"text alerts = {phone + ' (' + carrier + ')' if (phone and carrier) else '(off)'}")
        for tab in self._experiment_tabs():
            tab.set_sms_recipient(phone, carrier)

    def _on_session_subject_changed(self, subject: str):
        """Forward the raw Session text into every experiment tab so
        the email subject line matches what the user typed."""
        self._log_setup_change(f"session subject = {subject!r}")
        st = self.setup_tab
        for tab in self._experiment_tabs():
            tab.set_session_subject(subject)
            # Session identity for file naming + plot titles —
            # [notebook]_[session] stem and the notebook part (see
            # set_session_identity).  Pushed together with the subject
            # so the .npz / .xlsx / TIFF stems always track the fields.
            try:
                tab.set_session_identity(st.current_notebook(),
                                         st.current_session_stem())
            except Exception:
                pass

    def _on_environment_changed(self, short_code: str, custom_text: str):
        """Forward the Setup-tab Environment selection to every
        experiment tab so the pre-run damage-screen modal picks up
        the right posture (info / warn / alert) for the next Start.
        """
        _label = (custom_text.strip() or short_code
                  if short_code == "custom" else short_code)
        self._log_setup_change(f"environment = {_label}")
        for tab in self._experiment_tabs():
            try:
                tab.set_environment(short_code, custom_text)
            except Exception:
                # Defensive — a stale tab API (e.g. test stub)
                # shouldn't break the main signal path.
                pass

    def _on_limits_changed(self, cathodic_v: float, anodic_v: float,
                           tolerance_v: float):
        """Forward the user-edited water-window limits + tolerance from
        the Setup tab into every experiment tab so they can hand them
        off to the runner at start time.
        """
        self._log_setup_change(
            f"water-window limits = "
            f"[{cathodic_v:+.3f}, {anodic_v:+.3f}] V, "
            f"tolerance = {tolerance_v:.3f} V")
        for tab in self._experiment_tabs():
            tab.set_potential_limits(cathodic_v, anodic_v, tolerance_v)

    def _on_aliases_changed(self, aliases):
        try:
            desc = ", ".join(f"{k}={v}" for k, v in sorted(
                (aliases or {}).items()))
        except Exception:
            desc = repr(aliases)
        self._log_setup_change(f"channel aliases: {desc}")
        for tab in self._experiment_tabs():
            tab.set_aliases(aliases)

    def _on_scope_connected(self, up: bool):
        """Forward scope up/down to the setup tab so the channel-role
        dropdowns reflect whether a scope is actually attached."""
        if up:
            self.setup_tab.apply_default_scope_mapping()
            # Rebuild the n_avg widget to match this scope's capabilities
            # (TBS scopes get a combo of powers-of-two; arbitrary scopes
            # keep the spinbox).
            try:
                self.setup_tab.apply_scope_capabilities(self.conn.scope)
            except Exception as e:
                self.statusBar().showMessage(f"Could not query scope caps: {e}")
            # Apply the current Wide/Tight horizontal-window selection to the
            # freshly-connected scope (it reads it at run start).
            self._apply_horiz_scaling_to_scope(
                self.setup_tab.current_horizontal_scaling())
        else:
            self.setup_tab.clear_scope_mapping()
            # Reset the acq widget to its sane no-scope defaults.
            self.setup_tab.apply_scope_capabilities(None)

    def _on_acq_changed(self, mode: str, n_avg: int):
        """Forward the Setup-tab acquisition selection to each experiment
        tab so the next ``_start_runner`` applies it to the scope."""
        self._log_setup_change(
            f"acquisition = {mode}, n_avg = {n_avg}")
        for tab in self._experiment_tabs():
            tab.set_acquisition(mode, n_avg)

    def _on_inline_navg_edited(self, n_avg: int) -> None:
        """A pattern panel's INLINE average-count spinbox (beside the
        acquisition-time readout) was edited.

        Push the value into the Setup tab's ``acq_navg_spin`` — the
        single source of truth for the scope average count.  Setting it
        fires SetupTab's ``acquisitionChanged`` → :meth:`_on_acq_changed`
        → every tab's ``set_acquisition`` → each pattern panel's
        ``set_acquisition_info`` (which no-ops on the already-matching
        value), so all readouts stay in lockstep and the change is
        logged like any other Setup input.  A same-value ``setValue`` is
        a Qt no-op, so there is no signal loop.
        """
        try:
            self.setup_tab.acq_navg_spin.setValue(int(n_avg))
            # ``acq_navg_spin`` now commits on ``editingFinished`` (not
            # ``valueChanged``), so a programmatic ``setValue`` no longer
            # auto-fires the ``acquisitionChanged`` broadcast — trigger it
            # explicitly so an inline edit still reaches every tab + arms the
            # scope confirm (the no-op guard in ``_on_acq_changed`` keeps a
            # same-value push quiet).
            self.setup_tab._on_acq_changed()
        except Exception:
            pass

    def _on_horiz_scaling_changed(self, mode: str):
        """Apply the Setup-tab horizontal-window (Wide/Tight) selection to the
        shared scope — ``auto_layout_for_pulse`` reads it at the next run.  The
        scope is shared across every experiment tab, so setting it once here is
        enough; a scope that lacks the setter (simulator / legacy) is a no-op."""
        self._log_setup_change(f"horizontal window = {mode}")
        self._apply_horiz_scaling_to_scope(mode)

    def _apply_horiz_scaling_to_scope(self, mode: str) -> None:
        scope = getattr(self.conn, "scope", None)
        fn = getattr(scope, "set_horizontal_fit_mode", None)
        if callable(fn):
            try:
                fn(mode)
            except Exception:
                pass

    def _on_trigger_source_changed(self, source: str):
        """Forward the trigger-source toggle to each experiment tab."""
        self._log_setup_change(f"trigger source = {source}")
        for tab in self._experiment_tabs():
            tab.set_trigger_source(source)

    def _on_digital_trigger_changed(self, is_digital: bool):
        """Forward the TTL-vs-I_mon trigger-type flag to each experiment tab.

        Goes out in lockstep with ``_on_trigger_source_changed`` so the
        tabs always have a coherent (source, type) pair when the next
        run starts — without this, a stale flag would tell the runner
        to apply I_mon polarity logic to a TTL sync line (or vice
        versa).
        """
        self._log_setup_change(
            f"trigger type = {'digital (TTL sync)' if is_digital else 'I_mon (analog edge)'}")
        for tab in self._experiment_tabs():
            tab.set_digital_trigger(is_digital)

    # ``_on_trig_slope_changed`` was removed when the operator-facing
    # trigger-edge selector was deleted.  Each experiment tab now
    # resolves slope from the trigger-source rules at run start:
    #   * digital trigger (EXT or channel-Trigger) → RISE always
    #   * I_mon channel → RISE for anodic-first, FALL for cathodic-first
    # See ``_BaseExperimentTab._start_runner`` for the resolution.

    def _on_run_state_changed(self, running: bool):
        """An experiment tab has started or finished a run.

        While running we disable the Setup tab content (so the user
        can't reconfigure device, coating, or scope mapping mid-run)
        and the Results tab content (so a click on a saved file can't
        race the live capture pipeline). The QTabWidget itself stays
        enabled so the user can still switch tabs and watch progress.

        We also enable / disable the Run-menu run-control group
        (Start, Pause, Stop, Single capture) so those entries grey
        out when there's no live runner to drive — matching the
        in-tab button gating.

        On run-end we also re-render the return / reference OCP
        labels in the Setup tab. Each capture during the run pushes
        E_ret rest-window values into the per-coating learning bin
        (see :mod:`stimtest.electrode_potential_history`); a freshly
        crossed 10-sample threshold means the static catalog value
        the user saw before the run is now superseded by a learned
        running mean. Refreshing here is the cheapest way to surface
        that without wiring a per-capture signal into the Setup tab.
        """
        # View-only lock on Setup: disable the INPUTS (incl. the Hardware
        # panel) but keep the tab + its scroll area live so the operator can
        # scroll/read settings during a run (operator: "allow for scrolling
        # through … Setup and Test Parameters").  Falls back to the old
        # whole-widget disable if the tab lacks the method (test stubs).
        _setup_lock = getattr(self.setup_tab, "set_run_locked", None)
        if callable(_setup_lock):
            _setup_lock(running)
        else:
            self.setup_tab.setEnabled(not running)
        self.res_tab.setEnabled(not running)
        self._refresh_run_menu_enabled(running)
        if not running:
            # Best-effort refresh of the OCP readouts; missing
            # methods (e.g. on a cut-down stub during tests) are
            # tolerated silently.
            for fn_name in ("_refresh_return_potential_label",
                            "_refresh_reference_potential_label"):
                fn = getattr(self.setup_tab, fn_name, None)
                if callable(fn):
                    try:
                        fn()
                    except Exception:
                        pass

    def _refresh_run_menu_enabled(self, running: bool) -> None:
        """Toggle the Run menu's run-control items on/off as a run
        starts or finishes.

        Start / Pause / Stop / Single capture all share the live-run
        gating: they're meaningless without a runner attached, so we
        grey them out off-run and enable them during a run. Calibrate
        and Open Viewer aren't touched — they're useful regardless of
        run state.
        """
        for act in (self._act_start, self._act_pause,
                    self._act_stop, self._act_capture):
            act.setEnabled(running)
        # When the run ends, reset Pause's label back to "Pause" so
        # the next run starts from a known state. (During a run the
        # ``_on_pause_btn_toggled`` slot keeps it in sync with the
        # in-tab Pause button.)
        if not running:
            self._act_pause.setText("&Pause")

    def _on_pause_btn_toggled(self, paused: bool) -> None:
        """Sync the Pause/Resume menu label with the in-tab button.

        Each experiment tab's ``pause_btn`` is a checkable
        ``QPushButton``; we connect to its ``toggled`` signal for
        every tab so the menu text mirrors whichever button the
        user clicked. Yes — the same QAction's label flips between
        "Pause" while running and "Resume" while paused, so the
        menu reads naturally regardless of which state you're in.
        """
        self._act_pause.setText("&Resume" if paused else "&Pause")

    @staticmethod
    def _save_path_prompts_suppressed() -> bool:
        """Headless / first-launch bypass for the missing-folder choice
        dialog — same env vars as the run-start prompts
        (``_start_prompts_suppressed``)."""
        return bool(os.environ.get("PULSAR_SKIP_OVERWRITE_PROMPT")
                    or os.environ.get("PULSAR_SKIP_FIRST_LAUNCH_SETUP"))

    def _set_setup_save_path_text(self, text: str) -> None:
        """Set the Setup tab's save-path field WITHOUT re-emitting
        ``savePathChanged`` (the QLineEdit only emits on ``editingFinished``,
        so ``setText`` won't recurse — but block signals defensively), then
        re-evaluate the save-options enable state for the new path.  Best-
        effort; never raises."""
        try:
            sp = self.setup_tab.save_path
            blocked = sp.blockSignals(True)
            sp.setText(text)
            sp.blockSignals(blocked)
        except Exception:
            pass
        try:
            self.setup_tab._refresh_save_options()
        except Exception:
            pass

    def _resolve_missing_save_dir(self, p: Path) -> "Optional[Path]":
        """The INPUTTED save folder does not exist → modal with CHOICES.

        Operator: "I want a pop up with choices for the user to choose when an
        inputted directory does not exist."  Returns the folder to USE (always
        existing on return — created or chosen), or ``None`` to cancel and keep
        the previous save location.
          * **Create** → ``mkdir`` the entered path and use it;
          * **Choose a different folder…** → a folder browser (existing dirs
            only); reflects the pick back into the Setup field;
          * **Cancel** → ``None`` (caller reverts the field)."""
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Save folder does not exist")
        box.setText("The save folder you entered does <b>not exist</b>.")
        box.setInformativeText(
            f"<b>Folder:</b> {p}<br><br>"
            "Choose <b>Create</b> to make it now, <b>Choose a different "
            "folder…</b> to pick an existing one, or <b>Cancel</b> to keep "
            "your previous save location.")
        create = box.addButton("Create",
                               QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        choose = box.addButton("Choose a different folder…",
                               QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(create)
        box.exec()
        clicked = box.clickedButton()
        if clicked is create:
            try:
                p.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.statusBar().showMessage(f"Could not create folder: {e}")
                return None
            self._set_setup_save_path_text(str(p))   # normalize + re-enable
            return p
        if clicked is choose:
            start = str(p.parent) if p.parent.exists() else str(Path.home())
            chosen = QtWidgets.QFileDialog.getExistingDirectory(
                self, "Choose folder for saved sessions", start)
            if chosen:
                cp = Path(chosen)
                self._set_setup_save_path_text(str(cp))
                return cp
            return None                              # browser cancelled
        return None                                  # Cancel

    def _on_save_path_changed(self, path: str):
        """Forward a save-path change from the Setup tab to every
        experiment tab so freshly-saved sessions land in the new folder.

        When the INPUTTED folder does not exist, a genuine USER change (not
        the startup prefs-restore burst) pops a choice dialog — Create /
        Choose a different folder… / Cancel — via ``_resolve_missing_save_dir``
        (operator: "I want a pop up with choices … when an inputted directory
        does not exist") INSTEAD of silently creating a possibly-mistyped
        path.  On Cancel the previous save location is kept and the field is
        reverted.  During restore / headless the path is accepted as-is with
        NO modal and NO silent ``mkdir`` — the run-start directory guard
        (``_confirm_directory_before_start``) is the backstop, and the
        save-options stay disabled until the folder exists (gotcha #147)."""
        self._log_setup_change(f"save path = {path}")
        try:
            p = Path(path).expanduser()
        except Exception as e:
            self.statusBar().showMessage(f"Save path not usable: {e}")
            return
        if not p.exists():
            interactive = (getattr(self, "_log_setup_changes", False)
                           and not self._save_path_prompts_suppressed())
            if interactive:
                # Genuine post-startup USER input of a non-existent folder →
                # ask with choices instead of silently creating it.
                resolved = self._resolve_missing_save_dir(p)
                if resolved is None:
                    # Cancel → keep the previous save dir; revert the field so
                    # it doesn't linger on the non-existent typed path.
                    self._set_setup_save_path_text(str(self.save_dir))
                    self.statusBar().showMessage(
                        "Save path unchanged (folder does not exist).")
                    return
                p = resolved
            else:
                # Restore / startup / headless — NOT a fresh user "input" (a
                # remembered or default location).  Preserve the historical
                # best-effort create; never block launch on it.
                try:
                    p.mkdir(parents=True, exist_ok=True)
                except Exception as e:
                    self.statusBar().showMessage(f"Save path not usable: {e}")
                    return
        self.save_dir = p
        # Non-blocking OneDrive notice (operator: "do not tell the user not
        # to save to OneDrive, just warn them").  OneDrive can briefly lock
        # a file mid-write, which surfaces as a transient WinError 5 when a
        # .npz/.xlsx is written.  Saving there is ALLOWED — this only logs a
        # one-line heads-up, once per distinct OneDrive folder per session.
        self._maybe_warn_onedrive_save_dir(p)
        for tab in self._experiment_tabs():
            tab.set_save_dir(p)
        # Forward to the embedded Viewer too — without this, changing
        # the save path on the Setup tab would silently leave the
        # Results tab indexing the previous folder, and freshly-saved
        # sessions would never appear in the tree (audit finding #7).
        # ``ResultsTab.set_save_dir`` re-scans the folder so the user
        # sees the new contents immediately.
        if hasattr(self, "res_tab") and self.res_tab is not None:
            try:
                self.res_tab.set_save_dir(p)
            except Exception as e:
                # Indexing failure (e.g. permission denied on the new
                # folder) shouldn't abort the path change — the
                # experiment tabs have already accepted it and writes
                # there are what the user actually cares about.
                self.statusBar().showMessage(
                    f"Save path applied, but Viewer re-index failed: {e}")
        # Repoint the on-disk log mirror so subsequent log lines land
        # in the new save folder. Filename composes from notebook +
        # session fields (see ``SetupTab.current_log_filename``).
        self.log_pane.set_log_file(p / self.setup_tab.current_log_filename())
        self.statusBar().showMessage(f"Save path: {p}")

    @staticmethod
    def _is_onedrive_path(p: "Path") -> bool:
        """True if ``p`` is inside a OneDrive-synced folder.

        Prefers the OS-provided ``OneDrive*`` environment variables
        (the authoritative sync roots), and falls back to a
        case-insensitive ``OneDrive`` path component so a mapped or
        non-standard root is still recognised.  Never raises."""
        import os
        try:
            rp = p.resolve()
        except Exception:
            rp = p
        try:
            for var in ("OneDrive", "OneDriveCommercial", "OneDriveConsumer"):
                root = os.environ.get(var)
                if not root:
                    continue
                try:
                    root_p = Path(root).expanduser().resolve()
                except Exception:
                    continue
                try:
                    rp.relative_to(root_p)
                    return True
                except (ValueError, RuntimeError):
                    pass
            # Fallback: a literal "onedrive" component anywhere in the path.
            return any(part.lower().startswith("onedrive")
                       for part in rp.parts)
        except Exception:
            return False

    def _maybe_warn_onedrive_save_dir(self, p: "Path"):
        """Log a single non-blocking heads-up when the Save location is
        under OneDrive.  De-duped per distinct folder so it never nags."""
        try:
            if not self._is_onedrive_path(p):
                return
            key = str(p).lower()
            warned = getattr(self, "_onedrive_warned_paths", None)
            if warned is None:
                warned = set()
                self._onedrive_warned_paths = warned
            if key in warned:
                return
            warned.add(key)
            self.log_pane.log(
                "Note: the Save location is inside OneDrive. Saving works, "
                "but OneDrive can briefly lock a file mid-sync — if a save "
                "ever fails with 'Access is denied', just retry, or pick a "
                "non-synced local folder (e.g. C:\\PULSAR_data).")
        except Exception:
            # A logging convenience must never break the save-path change.
            pass

    def _on_log_filename_changed(self, filename: str):
        """Notebook / session field edited — repoint the log mirror.

        The save directory stays the same; only the file stem under
        it changes. Past log content under the OLD filename is left
        in place (we just stop writing to it).
        """
        self._log_setup_change(f"log filename = {filename}")
        self.log_pane.set_log_file(self.save_dir / filename)
        # The notebook field contributes to the session identity too —
        # keep the tabs' [notebook]_[session] stem in lock-step with
        # the log filename (same composition source).
        try:
            st = self.setup_tab
            for tab in self._experiment_tabs():
                tab.set_session_identity(st.current_notebook(),
                                         st.current_session_stem())
        except Exception:
            pass

    def _on_experiment_requested(self, code: str):
        """Setup tab's Experiment dropdown changed (or a startup-prefs
        path is seeding the current experiment). Swap the experiment
        tab and Test-parameters tab content WITHOUT changing the
        currently-focused tab — the user is presumably still mid-
        configuration on Setup."""
        self._log_setup_change(f"experiment = {code}")
        self._show_experiment(code)

    def _on_test_params_requested(self):
        """Setup tab's "Go to test parameters tab →" button. Pure
        navigation — the Test parameters tab's content was already
        updated by the dropdown's :meth:`_on_experiment_requested`
        the moment the selection changed, so this slot only needs
        to switch the active tab."""
        self.tabs.setCurrentWidget(self._test_params_tab)
        # Keep the Start / Pause / Stop button row attached to
        # whichever top-level tab the user just landed on.
        self._refresh_button_row_placement()

    def _auto_switch_to_experiment_tab(self, tab) -> None:
        """Switch focus to the running experiment's tab when Start is clicked.

        Wired to every experiment tab's ``start_btn.clicked``.  The
        intent: the user finishes configuring on Test parameters and
        clicks Start; the natural next step is to watch the live
        capture, so we move them to the experiment view without
        making them click a second tab.

        Guard: only switches when the currently-focused tab is NOT
        already the experiment tab.  Without that check, a user who
        clicked Start while already on the experiment tab (e.g. after
        a Stop / re-Start cycle) would briefly see the tab re-select
        itself — visually noisy and slightly disorienting.

        Best-effort: if the experiment tab isn't in the tab bar yet
        for some reason (it should be — _show_experiment inserted it
        the moment the experiment was picked) we just return.
        """
        idx = self.tabs.indexOf(tab)
        if idx < 0:
            return
        if self.tabs.currentIndex() == idx:
            return
        self.tabs.setCurrentIndex(idx)
        # Re-anchor the Start / Pause / Stop button row to the new
        # focused tab so the buttons stay in the same screen position.
        self._refresh_button_row_placement()

    def _show_experiment(self, code: str):
        """Insert / hide the experiment tab for ``code`` and rewire
        the ``Test parameters`` top-level tab's content to point at
        that experiment's params_page. Does NOT change the currently-
        focused tab; navigation is the caller's job (see
        :meth:`_on_test_params_requested` for the button click path
        and :meth:`_load_prefs_into_tabs` for the launch path).

        Tabs are removed via ``QTabWidget.removeTab`` rather than
        destroyed, so each tab keeps its widget state (selected
        channels, parameter values, captured data) for when the user
        comes back.
        """
        if code not in self._exp_tab_by_code:
            return
        # Remove any currently-shown experiment tab (everything between
        # Setup at index 0 and Results at the end). Identify it by widget
        # identity rather than name in case the user retitled.
        for code_other, (tab_other, _title) in self._exp_tab_by_code.items():
            if code_other == code: continue
            idx = self.tabs.indexOf(tab_other)
            if idx >= 0:
                self.tabs.removeTab(idx)
        # Insert the desired tab right before Results (always last)
        target_tab, target_title = self._exp_tab_by_code[code]
        if self.tabs.indexOf(target_tab) < 0:
            insert_at = self.tabs.indexOf(self.res_tab)
            if insert_at < 0:
                insert_at = self.tabs.count()
            self.tabs.insertTab(insert_at, target_tab, target_title)
        # Swap the Test parameters tab's content to point at the
        # active experiment's params_page. Take the previous occupant
        # off (without destroying it — its parent ExperimentTab still
        # holds a reference) and re-parent the new params_page in.
        while self._test_params_layout.count() > 0:
            old_item = self._test_params_layout.takeAt(0)
            old_widget = old_item.widget() if old_item is not None else None
            if old_widget is not None:
                old_widget.setParent(None)
        self._test_params_layout.addWidget(target_tab.params_page, stretch=1)
        # Prime the pattern preview now that this experiment's
        # params_page (which HOSTS the preview) is the active Test-
        # parameters content.  The preview's other render trigger is
        # the experiment-VIEW showEvent, which never fires for a user
        # who stays on Setup / Test parameters at launch — so without
        # this the preview is stuck on "No pulse pattern set." with
        # every field populated.  Idempotent; on the launch path this
        # runs AFTER restore_prefs, so it renders the restored values.
        try:
            target_tab.ensure_preview_rendered()
        except Exception:
            pass
        self._current_exp_code = code
        # The experiment-to-run changed → feed its pulse rate to Setup's
        # capture-time↔count conversion.
        self._push_active_rate_to_setup()
        # Re-anchor the Start / Pause / Stop button row to the
        # currently-focused top-level tab — the swap might have
        # invalidated the previous placement if the old experiment's
        # tab was the active one.
        self._refresh_button_row_placement()

    def _refresh_button_row_placement(self):
        """Re-parent the active experiment's ``_button_row_w`` to the
        bottom of whichever top-level tab is currently focused —
        either ``Test parameters`` or the experiment tab itself —
        so Start / Pause / Stop are always available in the same
        screen location regardless of which view the user is on.

        Setup and Results don't get the buttons (no experiment is
        being driven from there); the row is left attached to its
        most-recent host so re-entering Test parameters / the
        experiment tab brings the buttons back instantly.
        """
        if self._current_exp_code is None:
            return
        exp_tab, _ = self._exp_tab_by_code[self._current_exp_code]
        button_row = exp_tab._button_row_w
        cur_idx = self.tabs.currentIndex()
        test_idx = self.tabs.indexOf(self._test_params_tab)
        exp_idx = self.tabs.indexOf(exp_tab)
        if cur_idx == test_idx:
            target_parent = self._test_params_tab
            target_layout = self._test_params_layout
        elif cur_idx == exp_idx:
            target_parent = exp_tab
            target_layout = exp_tab.layout()
        else:
            # Setup / Results — leave the button row wherever it
            # currently lives.
            return
        if button_row.parentWidget() is target_parent:
            return  # already in the right place
        # Re-parent: detach from the old layout cleanly, then add to
        # the new one (which will append it after the existing
        # content — the experiment_page or the params_page above).
        button_row.setParent(None)
        target_layout.addWidget(button_row)

    # -------------------------------------------------------------- prefs
    def _load_prefs_into_tabs(self):
        """Read the prefs JSON and apply each section to its tab.

        Failures in any one section are swallowed so a malformed save
        for one tab doesn't break the whole launch. The user always
        sees code-defined defaults as a fallback.
        """
        prefs = load_prefs()
        if not prefs:
            self.statusBar().showMessage(
                f"Ready. (No saved prefs at {prefs_path()}.)")
            # Fresh install — still construct an experiment tab so its
            # Parameters / Experiment sub-tabs exist behind the scenes,
            # then focus Setup so the user starts there.
            code = self.setup_tab.experiment_combo.currentData() or "VT"
            self._show_experiment(code)
            self.tabs.setCurrentWidget(self.setup_tab)
            return
        # Admin catalog — load before setup_tab prefs so the custom
        # entries are already in the combos when restore_prefs runs.
        admin_prefs = prefs.get("admin", {})
        if isinstance(admin_prefs, dict):
            self._admin_password_hash = admin_prefs.get(
                "password_hash", self._admin_password_hash)
            # NOTE: extension-profile password hashes are NOT loaded
            # here.  Extension packages own their own credential
            # persistence (file outside the repo, env var, separate
            # prefs key with their own namespace, etc.) and call
            # ``register_extension_profile`` with the resolved hash
            # at import time.  See EXTENSION_PLUGIN_DESIGN.md for the
            # rationale (main PULSAR stays unaware of any specific
            # extension's credentials).
            # One-time migration: if the stored hash is the legacy
            # default for the old ``"admin"`` password, upgrade it
            # in place to the new factory default (``"Neuron01"``).
            # Users who explicitly set their own password still
            # have their custom hash and aren't touched.
            _LEGACY_ADMIN_HASH = (
                "8c6976e5b5410415bde908bd4dee15dfb167a9c8"
                "73fc4bb8a81f6f2ab448a918")
            if self._admin_password_hash == _LEGACY_ADMIN_HASH:
                self._admin_password_hash = _DEFAULT_HASH
            saved_catalog = admin_prefs.get("catalog", {})
            if isinstance(saved_catalog, dict):
                from .admin import CATALOG_KEYS as _CK
                for k in _CK:
                    entries = saved_catalog.get(k, [])
                    if isinstance(entries, list):
                        self._admin_catalog[k] = [
                            str(e) for e in entries if str(e).strip()]
            apply_admin_catalog(self.setup_tab, self._admin_catalog)
            # Restore login-history dropdown contents.  Sanitize:
            # only keep string entries, lowercased + stripped + non-
            # empty; cap at the same LOGIN_HISTORY_MAX the dialog
            # honors.  Malformed JSON (someone hand-edited
            # prefs.json) falls back to an empty list.
            saved_history = admin_prefs.get("login_history", [])
            if isinstance(saved_history, list):
                from .admin import LOGIN_HISTORY_MAX
                clean: list[str] = []
                for e in saved_history:
                    if not isinstance(e, str):
                        continue
                    norm = e.strip().lower()
                    if norm and norm not in clean:
                        clean.append(norm)
                    if len(clean) >= LOGIN_HISTORY_MAX:
                        break
                self._admin_login_history = clean

            # Track whether the operator has been through the
            # first-launch setup dialog.  Read here so the post-
            # legacy-migration check below knows whether to prompt.
            self._admin_setup_completed: bool = bool(
                admin_prefs.get("setup_completed", False))
            # If the loaded password_hash is custom (not the factory
            # default), the operator already explicitly set a
            # password — treat that as implicit setup completion so
            # we never nag.  Existing installs that have already
            # changed the password sail through.
            if self._admin_password_hash != _DEFAULT_HASH:
                self._admin_setup_completed = True
        else:
            # No admin prefs block at all — fresh install.  Default
            # state means "needs setup" (setup_completed defaults to
            # False; password_hash defaults to _DEFAULT_HASH already
            # from __init__).
            self._admin_setup_completed = False

        # Plugin-host profiles the operator imported in a prior
        # session (top-level prefs key, not under "admin").  Stash the
        # raw list now; ``_load_imported_profiles`` re-registers them
        # AFTER ``_load_extensions`` so a pip-installed extension of
        # the same name wins any hash collision.
        _imported = prefs.get("imported_profiles", [])
        if isinstance(_imported, list):
            self._imported_profiles = [
                p for p in _imported if isinstance(p, dict)]

        # First-launch setup prompt: shown exactly once when the
        # operator hasn't been through it AND the password is still
        # the factory default.  Modal — blocks the rest of the
        # prefs-restore flow until the operator answers.  See
        # ``_maybe_run_first_launch_setup`` for the full logic.
        self._maybe_run_first_launch_setup()

        try:
            self.setup_tab.restore_prefs(prefs.get(PREF_KEY_SETUP, {}))
        except Exception as e:
            self.statusBar().showMessage(f"Setup prefs ignored: {e}")
        for key, tab in (
            (PREF_KEY_VT, self.vt_tab), (PREF_KEY_SP, self.sp_tab),
            (PREF_KEY_CP, self.cp_tab),
            (PREF_KEY_LP, self.lp_tab), (PREF_KEY_PS, self.ps_tab),
        ):
            try:
                tab.restore_prefs(prefs.get(key, {}))
            except Exception as e:
                self.statusBar().showMessage(f"{key} prefs ignored: {e}")
        try:
            self.res_tab.restore_prefs(prefs.get(PREF_KEY_RESULTS, {}))
        except Exception as e:
            self.statusBar().showMessage(f"Results prefs ignored: {e}")
        self.statusBar().showMessage(
            f"Restored previous-session defaults from {prefs_path()}.")
        # Apply view state (window size/position, splitter sizes) AFTER
        # all tabs have restored — so any size hints in the layout are
        # honoured first and our saved sizes win the tie.
        self._restore_view_prefs(prefs.get(PREF_KEY_VIEW, {}))
        # Open the experiment tab that matches the dropdown so its
        # Parameters / Experiment sub-tabs are constructed and ready,
        # but always *focus* the Setup tab on launch so the user starts
        # with hardware / device picking.
        code = (self.setup_tab.experiment_combo.currentData()
                or self.setup_tab.current_prefs().get("experiment")
                or "VT")
        self._show_experiment(code)
        self.tabs.setCurrentWidget(self.setup_tab)

    def _save_prefs_from_tabs(self):
        """Snapshot every tab's prefs and write the auto-save JSON."""
        try:
            save_prefs(self._collect_prefs_payload())
        except Exception as e:
            # Never block close on a prefs-save failure
            self.statusBar().showMessage(f"Prefs save failed: {e}")

    def _collect_view_prefs(self) -> dict:
        """Snapshot window geometry + every panel/column the user can drag.

        Geometry is encoded via :meth:`saveGeometry` so dock state /
        maximised / multi-monitor positioning all round-trip. Splitter
        sizes are stored as plain int lists for portability — each
        widget owning a draggable splitter exposes its own
        ``view_state()`` here so additions pick up automatically.
        """
        out: dict = {}
        try:
            out["geometry_b64"] = bytes(self.saveGeometry().toBase64()).decode("ascii")
        except Exception:
            pass
        # Skip persisting if either pane has been collapsed to zero —
        # restoring that next session would hide the tab area or the
        # log pane entirely.
        _ms_sizes = list(self._main_split.sizes())
        if all(int(s) > 0 for s in _ms_sizes):
            out["main_split_sizes"] = _ms_sizes
        out["setup"] = self.setup_tab.view_state()
        # Per-experiment-tab splitter state. Stored under the tab's
        # experiment code so adding a new tab doesn't shift the others'
        # view state around.
        out["tabs"] = {
            tab.experiment_type(): tab.view_state()
            for tab in self._experiment_tabs()
        }
        # View-menu state (audit finding #16). Previously the font
        # preset, zoom factor, and gridlines toggle silently reset
        # on every launch — only window geometry survived. Persist
        # them alongside so a user who picked "Large 12pt @ 120 %
        # with grid on" gets that view back next session.
        out["base_font_pt"] = float(self._base_font_pt)
        out["scale_factor"] = float(self._scale_factor)
        if hasattr(self, "_act_grid"):
            out["gridlines"] = bool(self._act_grid.isChecked())
        # ---- Camera Monitor dock state ------------------------------
        # ``saveState()`` records the dock-area placement (left /
        # right / floating, plus the floating-window geometry)
        # separately from ``saveGeometry()``, which only covers the
        # main window itself.  Without this the dock would always
        # re-appear at the default right-side position regardless of
        # where the user dragged it last.
        try:
            out["state_b64"] = bytes(self.saveState().toBase64()).decode("ascii")
        except Exception:
            pass
        # Camera dock visibility — restore the open/closed state so a
        # user who finished a session with the dock open gets it open
        # next launch.
        if hasattr(self, "_act_camera"):
            out["camera_dock_visible"] = bool(self._act_camera.isChecked())
        # Camera connector state (selected device id).  Lives in the
        # ConnectionPanel (always constructed at MainWindow init), so
        # unlike the old lazy CameraPanel we can read it
        # unconditionally.  The shared camera service's connected
        # state is intentionally NOT persisted — re-launching
        # shouldn't auto-open a camera the user didn't explicitly
        # ask for this session.
        try:
            conn = getattr(self.conn, "camera_connector", None)
            if conn is not None:
                out["camera_panel"] = conn.current_prefs()
        except Exception:
            pass
        return out

    def _restore_view_prefs(self, view_prefs: dict):
        """Apply window geometry + every saved splitter / column size."""
        if not view_prefs:
            return
        if "geometry_b64" in view_prefs:
            try:
                ba = QtCore.QByteArray.fromBase64(
                    view_prefs["geometry_b64"].encode("ascii"))
                self.restoreGeometry(ba)
            except Exception:
                pass
        sizes = view_prefs.get("main_split_sizes")
        if isinstance(sizes, (list, tuple)) and len(sizes) == 2:
            try:
                int_sizes = [int(s) for s in sizes]
            except (TypeError, ValueError):
                int_sizes = None
            # Refuse to restore sizes containing 0 — would collapse
            # the tab area or the log pane and leave the user with a
            # broken layout. Skip and let the splitter fall back to
            # its built-in stretch-factor default.
            if int_sizes is not None and all(s > 0 for s in int_sizes):
                self._main_split.setSizes(int_sizes)
        if isinstance(view_prefs.get("setup"), dict):
            self.setup_tab.restore_view_state(view_prefs["setup"])
        tabs_view = view_prefs.get("tabs") or {}
        if isinstance(tabs_view, dict):
            for tab in self._experiment_tabs():
                code = tab.experiment_type()
                state = tabs_view.get(code)
                if isinstance(state, dict):
                    tab.restore_view_state(state)
        # View-menu state restore (audit #16). Each saved field is
        # optional so a legacy prefs file written before this fix
        # gracefully falls back to defaults.
        if "base_font_pt" in view_prefs:
            try:
                self._set_base_font_pt(float(view_prefs["base_font_pt"]))
            except (TypeError, ValueError):
                pass
        if "scale_factor" in view_prefs:
            try:
                self._set_scale_factor(float(view_prefs["scale_factor"]))
            except (TypeError, ValueError):
                pass
        if "gridlines" in view_prefs and hasattr(self, "_act_grid"):
            try:
                # ``setChecked`` fires the ``toggled`` signal which
                # propagates the new state down to every experiment
                # plot's ``set_grid_visible`` — exactly the live-
                # toggle path the user hits from the menu, so we
                # don't need a separate "apply" step here.
                self._act_grid.setChecked(bool(view_prefs["gridlines"]))
            except Exception:
                pass
        # ---- Camera connector restore ------------------------------
        # The CameraConnector lives in the ConnectionPanel and is
        # always constructed at MainWindow __init__ (not lazy), so
        # we can apply prefs directly without the old "pending
        # prefs" stash.
        cam_panel_prefs = view_prefs.get("camera_panel")
        if isinstance(cam_panel_prefs, dict):
            try:
                conn = getattr(self.conn, "camera_connector", None)
                if conn is not None:
                    conn.restore_prefs(cam_panel_prefs)
            except Exception:
                pass
        # Visibility — only auto-show the optional dock if last
        # session left it open.  Lazy-construction is triggered by
        # ``setChecked`` firing ``_on_view_camera_toggled``.
        if view_prefs.get("camera_dock_visible") and hasattr(self, "_act_camera"):
            try:
                self._act_camera.setChecked(True)
            except Exception:
                pass
        # Dock layout (positions of every QDockWidget that's been
        # constructed so far).  Applied AFTER setChecked above so
        # the camera dock exists when restoreState walks its name.
        if "state_b64" in view_prefs:
            try:
                ba = QtCore.QByteArray.fromBase64(
                    view_prefs["state_b64"].encode("ascii"))
                self.restoreState(ba)
            except Exception:
                pass

    def closeEvent(self, ev):
        # Close hardware connections before the window disappears so
        # the stimulator and oscilloscope are left in a clean state
        # (DLL handle released, VISA session closed).
        for attr in ("_stim", "_scope"):
            hw = getattr(self.conn, attr, None)
            if hw is not None:
                try:
                    hw.close()
                except Exception:
                    pass

        # On close, strip user-added "custom" entries (custom
        # coatings, custom return / reference electrodes, custom
        # devices + their state) so they don't survive into the
        # next session. The user asked for these to be ephemeral
        # — added during the session for convenience but reset
        # when the GUI exits, so the dropdowns return to a clean
        # catalog-only state on the next launch.
        #
        # Auto-save during the session (the Start-button hook
        # below in ``_save_prefs_from_tabs``) keeps the custom
        # entries so a mid-session crash doesn't lose them; the
        # strip applies ONLY to the final close-time save.
        try:
            payload = self._collect_prefs_payload()
            setup_section = payload.get(PREF_KEY_SETUP)
            if isinstance(setup_section, dict):
                for key in ("coating_custom_entries",
                            "return_custom_entries",
                            "reference_custom_entries",
                            "device_custom_entries",
                            "device_custom_state"):
                    setup_section.pop(key, None)
            save_prefs(payload)
        except Exception as e:
            # Never block close on a prefs-save failure.
            self.statusBar().showMessage(f"Prefs save failed: {e}")
        super().closeEvent(ev)

    # ------------------------------------------------------------- save/load
    def _collect_prefs_payload(self) -> dict:
        """Snapshot every tab into a single prefs dict.

        Same shape as the auto-save payload — sharing the structure
        means a saved profile and the auto-prefs file are
        interchangeable: the user can copy a profile over the default
        prefs file and the next launch will pick it up.
        """
        try:
            res_prefs = self.res_tab.current_prefs()
        except Exception:
            res_prefs = {}
        return {
            PREF_KEY_SETUP: self.setup_tab.current_prefs(),
            PREF_KEY_VT: self.vt_tab.current_prefs(),
            PREF_KEY_SP: self.sp_tab.current_prefs(),
            PREF_KEY_CP: self.cp_tab.current_prefs(),
            PREF_KEY_LP: self.lp_tab.current_prefs(),
            PREF_KEY_PS: self.ps_tab.current_prefs(),
            PREF_KEY_RESULTS: res_prefs,
            PREF_KEY_VIEW: self._collect_view_prefs(),
            "admin": {
                "password_hash": self._admin_password_hash,
                # Extension-profile password hashes are NOT persisted
                # here — extensions own their own credential storage.
                # See EXTENSION_PLUGIN_DESIGN.md.
                "catalog": self._admin_catalog,
                # Login dropdown history (most-recent-first,
                # lowercased, capped at admin.LOGIN_HISTORY_MAX).
                # Used to seed the login dialog's Username combobox.
                "login_history": list(self._admin_login_history),
                # First-launch setup completion flag.  Once True,
                # the one-time admin password setup dialog never
                # shows again — even if the operator chose to keep
                # the factory default password (in which case ok=False
                # was returned from prompt_first_launch_setup but we
                # still mark setup_completed so the dialog doesn't
                # nag).
                "setup_completed": bool(self._admin_setup_completed),
            },
            # Plugin-host profiles imported via Admin → Import Profile…
            # Re-registered at launch by ``_load_imported_profiles`` so
            # an imported profile (e.g. CWRU) survives restarts without
            # the pip package installed.  Each entry is
            # ``{name, display_name, password_hash, shapes}``.
            "imported_profiles": list(self._imported_profiles),
        }

    def _apply_prefs_payload(self, payload: dict) -> None:
        """Push a loaded profile back into every tab."""
        try:
            self.setup_tab.restore_prefs(payload.get(PREF_KEY_SETUP, {}))
        except Exception as e:
            self.statusBar().showMessage(f"Setup prefs ignored: {e}")
        for key, tab in (
            (PREF_KEY_VT, self.vt_tab), (PREF_KEY_SP, self.sp_tab),
            (PREF_KEY_CP, self.cp_tab),
            (PREF_KEY_LP, self.lp_tab), (PREF_KEY_PS, self.ps_tab),
        ):
            try:
                tab.restore_prefs(payload.get(key, {}))
            except Exception as e:
                self.statusBar().showMessage(f"{key} prefs ignored: {e}")
        try:
            self.res_tab.restore_prefs(payload.get(PREF_KEY_RESULTS, {}))
        except Exception as e:
            self.statusBar().showMessage(f"Results prefs ignored: {e}")
        # View state — splitter / column / window sizes — comes along
        # for the ride. A profile saved on a different monitor still
        # restores cleanly because Qt clamps the geometry to the
        # current screen.
        if isinstance(payload.get(PREF_KEY_VIEW), dict):
            self._restore_view_prefs(payload[PREF_KEY_VIEW])

    def _on_reset_to_default(self):
        """File → Reset to default — wipe user prefs and re-apply the
        as-constructed defaults captured at startup.

        Refuses to reset while a run is in progress (changing live
        widgets out from under the worker would corrupt its captured
        references). Asks for confirmation before nuking anything.

        After applying the default snapshot, also overwrites the
        auto-save prefs file so the user's old state can't sneak back
        in via :meth:`closeEvent` — otherwise closing right after a
        reset would persist whatever the live widgets currently show
        (which IS the defaults, so technically equivalent), but
        explicit save-now is clearer for forensic purposes.
        """
        # Run-lock check — same pattern as _on_load_settings.
        if any(getattr(t, "_runner", None) is not None
               for t in self._experiment_tabs()):
            QtWidgets.QMessageBox.information(
                self, "Reset blocked",
                "A run is in progress. Stop or wait for it to finish "
                "before resetting settings.")
            return
        reply = QtWidgets.QMessageBox.question(
            self, "Reset to default?",
            "Reset every Setup / Test-parameter / View setting to "
            "the built-in defaults?\n\n"
            "Your saved prefs file will be overwritten with the "
            "defaults too — there's no undo.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        defaults = getattr(self, "_default_prefs_payload", None)
        if not defaults:
            self.statusBar().showMessage(
                "Reset failed: no startup snapshot available.")
            return
        try:
            self._apply_prefs_payload(defaults)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Reset failed",
                f"Could not apply default prefs cleanly:\n\n{e}")
            return
        # Persist immediately so a subsequent close doesn't immediately
        # re-save the user's previously-loaded state. ``save_prefs``
        # writes to the auto-save path; failures are non-fatal — the
        # in-memory state is already reset.
        try:
            save_prefs(defaults)
        except Exception as e:
            self.statusBar().showMessage(
                f"Reset applied but save failed: {e}")
            return
        self.statusBar().showMessage(
            "Settings reset to defaults; auto-save file updated.")

    def _on_save_settings(self):
        """File → Save settings… — write current state to a user-picked file.

        Uses the ``.setting`` extension by default so the saved
        profile is recognisable as a stimtest settings file. The
        underlying format is still JSON; existing JSON profiles
        from earlier versions are still accepted on the Load
        side via the file filter.
        """
        # Default to the prefs directory so the user can find their
        # profiles next to the auto-save file.
        default_dir = str(prefs_dir())
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save settings",
            f"{default_dir}/profile{PREFS_USER_EXT}",
            PREFS_USER_FILTER,
        )
        if not path:
            return
        # Auto-append the canonical extension if the user didn't
        # type one. Accept either ``.setting`` (preferred) or
        # ``.json`` (back-compat with their existing saves).
        lower = path.lower()
        if not (lower.endswith(PREFS_USER_EXT)
                or lower.endswith(PREFS_USER_EXT_LEGACY)):
            path += PREFS_USER_EXT
        try:
            save_prefs_to(Path(path), self._collect_prefs_payload())
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Save failed",
                f"Could not write settings file:\n{path}\n\n{e}")
            return
        self.statusBar().showMessage(f"Settings saved to {path}")

    def _on_load_settings(self):
        """File → Load settings… — replace current state from a file.

        Refuses to load while a run is in progress — the experiment
        tab's run-lock signal disables most of the UI, but loading
        a profile mid-run would still pull the rug out from under
        the worker (which has captured references to the array,
        configuration, etc. at start time).
        """
        # Block load while anything is running. _on_run_state_changed
        # disables setup_tab/res_tab content during a run, so checking
        # whether either is currently disabled tells us a run is in
        # flight.
        if not self.setup_tab.isEnabled():
            QtWidgets.QMessageBox.information(
                self, "Run in progress",
                "Stop the running experiment before loading settings.")
            return
        default_dir = str(prefs_dir())
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load settings", default_dir,
            PREFS_USER_FILTER,
        )
        if not path:
            return
        try:
            payload = load_prefs_from(Path(path))
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Load failed",
                f"Could not read settings file:\n{path}\n\n{e}")
            return
        self._apply_prefs_payload(payload)
        self.statusBar().showMessage(f"Settings loaded from {path}")

    # ----------------------------------------------------------- Edit menu
    def _focused_editor(self):
        """Return the widget that currently has keyboard focus and
        looks editable (line edit, plain-text edit, table widget,
        spinbox-internal-line-edit). Returns ``None`` if focus is on
        something else — buttons, the menu bar itself, etc.
        """
        w = QtWidgets.QApplication.focusWidget()
        if w is None:
            return None
        # The internal QLineEdit of a QSpinBox / QDoubleSpinBox is what
        # actually owns focus when the user clicks into the spin field —
        # walk up to find the spin parent so cut/copy/paste hit the
        # value text, not the wrapper.
        if isinstance(w, QtWidgets.QLineEdit):
            return w
        if isinstance(w, (QtWidgets.QPlainTextEdit, QtWidgets.QTextEdit)):
            return w
        if isinstance(w, QtWidgets.QAbstractSpinBox):
            return w.lineEdit() or w
        # When a QTableWidget cell is in edit mode, focus is on its
        # internal editor (a QLineEdit). When the user is just
        # navigating cells without editing, focus is on the table
        # itself — return that so clipboard ops hit the spreadsheet
        # filter installed via :func:`enable_spreadsheet_paste`.
        if isinstance(w, QtWidgets.QTableWidget):
            return w
        return w

    @staticmethod
    def _safe_call(obj, method: str, *args):
        """Call ``obj.method(*args)`` only if the method exists. Used
        by the Edit-menu dispatchers so we can target widgets that
        have only some of the standard clipboard methods (e.g.,
        QPlainTextEdit has cut/copy/paste/undo/redo; QTableWidget
        doesn't natively, but its installed filter intercepts the
        keystroke we synthesise instead).
        """
        fn = getattr(obj, method, None)
        if callable(fn):
            try:
                fn(*args)
                return True
            except Exception:
                pass
        return False

    def _send_key(self, target, key, mod):
        """Synthesise a ``Ctrl+<key>`` event on ``target``. Used to
        fire the spreadsheet copy/cut/paste filter when the table
        itself owns focus (the filter listens for the actual key
        event, not a method call).
        """
        if target is None:
            return
        ev = QtGui.QKeyEvent(QtCore.QEvent.Type.KeyPress, key, mod)
        QtWidgets.QApplication.sendEvent(target, ev)

    def _on_edit_undo(self):
        w = self._focused_editor()
        self._safe_call(w, "undo")

    def _on_edit_redo(self):
        w = self._focused_editor()
        self._safe_call(w, "redo")

    def _on_edit_cut(self):
        w = self._focused_editor()
        if isinstance(w, QtWidgets.QTableWidget):
            self._send_key(w, QtCore.Qt.Key.Key_X,
                           QtCore.Qt.KeyboardModifier.ControlModifier)
            return
        self._safe_call(w, "cut")

    def _on_edit_copy(self):
        w = self._focused_editor()
        if isinstance(w, QtWidgets.QTableWidget):
            self._send_key(w, QtCore.Qt.Key.Key_C,
                           QtCore.Qt.KeyboardModifier.ControlModifier)
            return
        # QLabel and other read-only widgets don't have ``copy``; fall
        # back to copying the selection text via the clipboard.
        if hasattr(w, "selectedText"):
            text = w.selectedText() if callable(w.selectedText) else ""
            if text:
                QtWidgets.QApplication.clipboard().setText(text)
                return
        self._safe_call(w, "copy")

    def _on_edit_paste(self):
        w = self._focused_editor()
        if isinstance(w, QtWidgets.QTableWidget):
            self._send_key(w, QtCore.Qt.Key.Key_V,
                           QtCore.Qt.KeyboardModifier.ControlModifier)
            return
        self._safe_call(w, "paste")

    def _on_edit_select_all(self):
        w = self._focused_editor()
        if isinstance(w, QtWidgets.QTableWidget):
            w.selectAll()
            return
        self._safe_call(w, "selectAll")

    # ------------------------------------------------------------ Run menu
    def _push_active_rate_to_setup(self, *_):
        """Feed the Setup tab the pulse rate of the experiment that will RUN
        (the "Experiment to run" selection = ``_current_exp_code``), so its
        capture-time↔count conversion has a rate.  Setup has no rate of its
        own.  No-op-safe: the setter ignores an unchanged / invalid rate."""
        try:
            code = getattr(self, "_current_exp_code", None)
            entry = self._exp_tab_by_code.get(code) if code else None
            tab = entry[0] if entry else None
            pp = getattr(tab, "pattern_panel", None) if tab else None
            fn = getattr(pp, "effective_rate_hz", None) if pp else None
            if fn is not None:
                self.setup_tab.set_pulse_rate_hz(fn())
        except Exception:
            pass

    def _active_experiment_tab(self):
        """The experiment tab currently visible in the main tab bar,
        or the first experiment tab when the user is on a
        non-experiment tab (Setup / Test parameters / Results /
        Viewer). Used by Run-menu dispatchers so the keyboard
        shortcut still has a sensible target when the user is on a
        different tab.
        """
        tabs = self._experiment_tabs()
        if not tabs:
            return None
        current = self.tabs.currentWidget()
        if current in tabs:
            return current
        return tabs[0]

    def _on_run_start(self):
        tab = self._active_experiment_tab()
        if tab is None:
            return
        if hasattr(tab, "start_clicked"):
            tab.start_clicked()

    def _on_run_pause(self):
        tab = self._active_experiment_tab()
        if tab is None:
            return
        # ``pause_btn`` is a QPushButton with ``checkable=True`` —
        # clicking it toggles the paused state. Match that here so
        # the menu action and the in-tab button stay in sync.
        if hasattr(tab, "pause_btn") and tab.pause_btn.isEnabled():
            tab.pause_btn.click()

    def _on_run_stop(self):
        tab = self._active_experiment_tab()
        if tab is None:
            return
        if hasattr(tab, "stop_clicked"):
            tab.stop_clicked()

    def _on_run_single_capture(self):
        tab = self._active_experiment_tab()
        if tab is None:
            return
        # SP / LP have ``single_capture`` button-handlers; VT / PS
        # don't (those are sweep-only). Best-effort dispatch.
        for attr in ("single_capture_clicked", "_on_single_capture"):
            fn = getattr(tab, attr, None)
            if callable(fn):
                fn()
                return
        self.statusBar().showMessage(
            "Single capture isn't available for this experiment.")

    def _open_calibration_tab(self) -> "CalibrationTab":
        """Create the Calibration tab on first use, insert it directly to
        the right of Setup, and wire its ``doneRequested`` signal.

        Idempotent — repeated presses of "Run Calibration" just switch
        focus to the existing tab.
        """
        if self.cal_tab is None:
            self.cal_tab = CalibrationTab(
                stim=getattr(self.conn, "stim", None),
                scope=getattr(self.conn, "scope", None))
            # Insert at index right-after-Setup so the tab order stays
            # Setup → Calibration → Test parameters → … even if other
            # tabs were re-ordered earlier.
            insert_idx = self.tabs.indexOf(self.setup_tab) + 1
            self.tabs.insertTab(insert_idx, self.cal_tab, "Verification")
            self.cal_tab.doneRequested.connect(self._on_calibration_done)
        else:
            # Tab already exists — just refresh the handles in case the
            # connection state changed since last use.
            self.cal_tab.set_hardware(
                stim=getattr(self.conn, "stim", None),
                scope=getattr(self.conn, "scope", None))
        return self.cal_tab

    def _on_run_calibrate(self):
        """Run → Calibrate (menu) / "Run Calibration" button (Setup tab).

        Lazily instantiates the embedded :class:`CalibrationTab`, inserts
        it after Setup, and switches focus to it.  The tab persists after
        first creation so a subsequent press is just a tab switch.
        """
        cal = self._open_calibration_tab()
        idx = self.tabs.indexOf(cal)
        if idx >= 0:
            self.tabs.setCurrentIndex(idx)

    def _on_run_open_viewer(self):
        """Run → Open Viewer — focuses the embedded Viewer tab."""
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if w.__class__.__name__ in ("ViewerPanel", "ResultsTab"):
                self.tabs.setCurrentIndex(i)
                return
        self.statusBar().showMessage(
            "Viewer tab not found in this build.")

    # ----------------------------------------------------------- View menu
    def _on_view_zoom_in(self):
        """Bump the GUI scale up by 10 % (capped at 200 %)."""
        self._set_scale_factor(self._scale_factor * 1.1)

    def _on_view_zoom_out(self):
        """Bump the GUI scale down by 10 % (floored at 50 %)."""
        self._set_scale_factor(self._scale_factor / 1.1)

    def _on_view_zoom_reset(self):
        """Return to 100 % zoom (the user's chosen base font size
        with no scale multiplier)."""
        self._set_scale_factor(1.0)

    def _ensure_camera_dock(self) -> QtWidgets.QDockWidget:
        """Build the Camera Monitor dock + CameraStreamPane on first
        request.

        The dock is an OPTIONAL extra view of the same shared
        :func:`camera_service`.  The PRIMARY camera UI lives in the
        ConnectionPanel (device picker + connect) + each experiment
        tab's embedded ``CameraStreamPane`` (preview beneath the
        scope plot).  This dock is useful when the operator wants
        the bench camera on a second monitor while running an
        experiment on the main display.

        Lazy-constructed: the heavy QtMultimedia / QtMultimediaWidgets
        imports only land via ``camera_service()`` when something
        first calls it.

        The dock defaults to docked on the right side, floats with
        Ctrl+Shift+C (set on the menu action), and survives across
        show/hide toggles via ``QMainWindow.saveState()``.
        """
        if self._camera_dock is not None:
            return self._camera_dock
        from .camera import CameraStreamPane
        # The dock's CameraStreamPane forces ``always_visible`` —
        # the dock IS the always-on monitor, so hiding the preview
        # when the camera disconnects would defeat the purpose
        # (the user wants to see "no signal" not an empty pane).
        pane = CameraStreamPane(parent=self, allow_snapshot_button=True)
        pane.set_always_visible(True)
        dock = QtWidgets.QDockWidget("Camera Monitor", self)
        dock.setObjectName("CameraMonitorDock")  # for saveState/restoreState
        dock.setFeatures(
            QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
            | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        dock.setAllowedAreas(
            QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
            | QtCore.Qt.DockWidgetArea.RightDockWidgetArea
            | QtCore.Qt.DockWidgetArea.TopDockWidgetArea
            | QtCore.Qt.DockWidgetArea.BottomDockWidgetArea)
        dock.setWidget(pane)
        # Keep the menu-action checkbox in sync when the user closes
        # the dock via its X button (not via the menu).
        dock.visibilityChanged.connect(
            lambda visible: self._act_camera.setChecked(bool(visible)))
        # Initial placement: right side.  Persisted via
        # MainWindow.saveState() in the view-prefs round-trip.
        self.addDockWidget(QtCore.Qt.DockWidgetArea.RightDockWidgetArea, dock)
        self._camera_dock = dock
        self._camera_stream_pane = pane
        return dock

    def _on_view_camera_toggled(self, checked: bool) -> None:
        """Show / hide the Camera Monitor dock.  Lazy-constructs on
        first show; subsequent toggles just flip visibility.  Does
        NOT disconnect the camera — the shared
        :func:`camera_service` stays connected for the
        ConnectionPanel + experiment-tab consumers."""
        if checked:
            dock = self._ensure_camera_dock()
            dock.show()
            dock.raise_()
        else:
            if self._camera_dock is not None:
                self._camera_dock.hide()

    def _on_view_grid_toggled(self, checked: bool) -> None:
        """Broadcast the View → Gridlines toggle to every
        experiment plot. Each plot exposes a
        ``set_grid_visible(bool)`` method; we walk all experiment
        tabs and flip the four plots they host (pattern preview,
        staircase if present, tracking if present, multichannel
        scope). The Viewer has its own independent gridline
        toggle since matplotlib's rendering pipeline is separate.
        """
        # Probe each experiment tab for known plot-bearing attribute
        # names. ``staircase`` is the Progressive-Stress ramp viewer
        # (a sibling of pattern_preview in the figure switcher);
        # ``tracking_plot`` lives on LP / PS only; ``multichan_scope``
        # is the V_mon scope every experiment tab carries. Names are
        # picked to match the attribute the corresponding subclass
        # already assigns on construction.
        for tab in self._experiment_tabs():
            for attr in ("pattern_preview", "staircase",
                         "tracking_plot", "multichan_scope"):
                widget = getattr(tab, attr, None)
                if widget is None:
                    continue
                setter = getattr(widget, "set_grid_visible", None)
                if callable(setter):
                    try:
                        setter(checked)
                    except Exception:
                        # Plot toggles are cosmetic — never let a
                        # rendering hiccup escape into the menu
                        # action handler.
                        pass

    def _on_theme_selected(self, mode: str) -> None:
        """Apply + persist a View → Theme choice (system / light / dark)."""
        from .theme import apply_theme, THEME_MODES
        mode = mode if mode in THEME_MODES else "system"
        self._theme_mode = mode
        apply_theme(QtWidgets.QApplication.instance(), mode)
        # Keep the menu radio in sync (also covers a programmatic set).
        for m, act in getattr(self, "_theme_actions", {}).items():
            act.setChecked(m == mode)
        # Persist for next launch.  Prefs sections must be DICTS (load_prefs
        # drops non-dict top-level keys), so store under ``theme.mode``.
        try:
            from .prefs import load_prefs, save_prefs
            prefs = load_prefs() or {}
            prefs["theme"] = {"mode": mode}
            save_prefs(prefs)
        except Exception:
            pass
        # The rotated axis-title widgets cache a rendered pixmap; nudge them to
        # repaint in the new palette colour.
        try:
            self._refresh_axis_titles_for_theme()
        except Exception:
            pass

    def _apply_saved_theme(self) -> None:
        """Read the saved theme pref, apply it, set the menu radio, and (for
        ``system``) auto-follow later OS scheme changes.  Called once at the
        end of ``__init__``."""
        from .theme import (apply_palette_only, connect_system_scheme,
                            THEME_MODES)
        mode = "system"
        try:
            from .prefs import load_prefs
            mode = ((load_prefs() or {}).get("theme") or {}).get("mode", "system")
        except Exception:
            pass
        if mode not in THEME_MODES:
            mode = "system"
        self._theme_mode = mode
        # PALETTE ONLY — no setStyle.  launch() already installed Fusion before
        # the window (so the tree was built under it); a setStyle here would
        # force a GLOBAL widget re-polish, which is pointless (Fusion already
        # set) and a segfault hazard when stale widgets exist.
        apply_palette_only(QtWidgets.QApplication.instance(), mode)
        for m, act in getattr(self, "_theme_actions", {}).items():
            act.setChecked(m == mode)
        # While in "system" mode, re-apply when the OS toggles light/dark.
        # Hold the window via a WEAKREF in the callback — the signal lives on
        # the app's styleHints (process-lifetime), so a strong ``self`` capture
        # would keep every MainWindow alive forever.  That leaked each test's
        # window (hardware sims / timers / file handles) and crashed the suite
        # at teardown.  The weakref lets the window GC normally; the tiny
        # closure that stays connected is harmless.
        import weakref as _weakref
        _self_ref = _weakref.ref(self)

        def _follow_os_scheme():
            _w = _self_ref()
            if _w is not None and getattr(_w, "_theme_mode", "system") == "system":
                # Palette-only re-apply (Fusion already installed) + a repaint.
                apply_palette_only(QtWidgets.QApplication.instance(), "system")
                try:
                    _w._refresh_axis_titles_for_theme()
                except Exception:
                    pass
        connect_system_scheme(QtWidgets.QApplication.instance(),
                              _follow_os_scheme)

    def _refresh_axis_titles_for_theme(self) -> None:
        """Force every rotated ``_AxisTitle`` to repaint after a palette change
        so the plot-margin axis labels pick up the new theme text colour.
        (Its ``paintEvent`` reads ``palette().windowText()`` live, so an
        explicit ``update()`` is enough — no cache to invalidate.)"""
        from .widgets import _AxisTitle
        for w in self.findChildren(_AxisTitle):
            w.update()

    def _set_base_font_pt(self, pt: float) -> None:
        """Pick a new base font size from the Font Size submenu.
        The live zoom factor is preserved — e.g. switching from
        Medium (10pt) at 120 % to Large (12pt) gives an effective
        14.4pt font."""
        try:
            pt = float(pt)
        except (TypeError, ValueError):
            return
        self._base_font_pt = max(6.0, min(36.0, pt))
        # Sync the menu's check-marks so only the active preset
        # reads as checked.
        for p, act in getattr(self, "_font_size_actions", {}).items():
            act.setChecked(abs(p - self._base_font_pt) < 1e-3)
        self._apply_view_style()

    def _set_scale_factor(self, factor: float) -> None:
        """Pin the GUI zoom level. Clamped to [0.5×, 2.0×] so the
        user can't accidentally shrink the menus into illegibility
        or balloon the layout off-screen."""
        try:
            factor = float(factor)
        except (TypeError, ValueError):
            return
        self._scale_factor = max(0.5, min(2.0, factor))
        self._apply_view_style()

    def _apply_view_style(self) -> None:
        """Push the current base-font × zoom-factor into a
        top-level stylesheet so every child widget picks up the
        new size. Qt's default style propagates ``font-size``
        through padding/margin computations, so a single
        ``setStyleSheet`` here scales the WHOLE GUI's spacing
        without per-widget tweaks."""
        eff_pt = self._base_font_pt * self._scale_factor
        # Round to 1 decimal so the stylesheet doesn't carry
        # noisy 0.0000001 trailing digits in the rendered CSS.
        self.setStyleSheet(f"QWidget {{ font-size: {eff_pt:.1f}pt; }}")
        # Brief status-bar confirmation so the user gets feedback
        # on what just changed — especially useful when the
        # change is small (e.g. 100 % → 110 %).
        pct = int(round(self._scale_factor * 100.0))
        self.statusBar().showMessage(
            f"View: base font {self._base_font_pt:.0f}pt, "
            f"zoom {pct}% (effective {eff_pt:.1f}pt).",
            3000)

    # --------------------------------------------------------- Window menu
    def _on_window_toggle_maximize(self):
        """Toggle between maximized and normal. Same UX as a double-
        click on the title bar."""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    # ----------------------------------------------------------- Help menu
    def _on_help_check_updates(self):
        """Help → Check for updates — best-effort GitHub-Releases
        comparison against the installed ``stimtest.__version__``.
        Falls back to a "manual check" message when the network
        request can't reach the API (offline lab box, firewall, ...).

        When the local store has accumulated samples we also nudge
        the user to consider contributing them — a release upgrade
        is a natural moment to think about giving back data that
        helps refine the next release's catalog defaults. The nudge
        is a non-default secondary button on the dialog; clicking
        it opens the full :meth:`_on_help_contribute_data` flow.
        Anonymous-by-default + opt-in attribution still apply.
        """
        from urllib.request import Request, urlopen
        from urllib.error import URLError
        import json as _json
        try:
            from .. import __version__ as installed
        except Exception:
            installed = "unknown"
        api = ("https://api.github.com/repos/Bortz1234/"
               "StimulationTesting/releases/latest")
        try:
            req = Request(api, headers={"User-Agent": "stimtest"})
            with urlopen(req, timeout=4) as resp:
                payload = _json.load(resp)
            latest = str(payload.get("tag_name", "")).lstrip("vV")
        except (URLError, OSError, ValueError, _json.JSONDecodeError):
            self._show_update_dialog(
                title="Check for updates",
                text=(f"Couldn't reach the update server. You're "
                      f"running version {installed}; the latest is "
                      f"at https://github.com/Bortz1234/"
                      f"StimulationTesting/releases."),
                offer_contribute=True,
            )
            return
        if not latest:
            self._show_update_dialog(
                title="Check for updates",
                text=(f"You're running version {installed}. No "
                      f"release info found on the server yet — this "
                      f"project may still be pre-release."),
                offer_contribute=True,
            )
            return
        if latest == installed:
            self._show_update_dialog(
                title="Up to date",
                text=f"You're running the latest version ({installed}).",
                offer_contribute=True,
            )
            return
        self._show_update_dialog(
            title="Update available",
            text=(f"Installed: {installed}\nLatest: {latest}\n\n"
                  f"Download: https://github.com/Bortz1234/"
                  f"StimulationTesting/releases"),
            offer_contribute=True,
        )

    def _show_update_dialog(self, *, title: str, text: str,
                            offer_contribute: bool) -> None:
        """Render the update-check result with an optional
        "Contribute electrode data" secondary button.

        The contribute button only appears when the local store has
        at least one sample — there's no point nagging users on a
        clean install. The default action is OK (close); the
        contribute button is non-default so an Enter-press doesn't
        accidentally launch the contribution flow.
        """
        n_samples = self._help_data_sample_count() if offer_contribute else 0
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        # Use the standard Information icon so the dialog's chrome
        # matches the previous QMessageBox.information() look.
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        if n_samples > 0 and offer_contribute:
            box.setText(text)
            box.setInformativeText(
                f"You've collected {n_samples} electrode-potential "
                f"sample(s). Would you like to contribute them to "
                f"the project so future releases ship better catalog "
                f"defaults? You'll review and submit the GitHub Issue "
                f"yourself — the application never uploads anything "
                f"automatically.")
            ok_btn = box.addButton(QtWidgets.QMessageBox.StandardButton.Ok)
            contribute_btn = box.addButton(
                "Contribute electrode data…",
                QtWidgets.QMessageBox.ButtonRole.ActionRole)
            box.setDefaultButton(ok_btn)
            box.exec()
            if box.clickedButton() is contribute_btn:
                # Open the contribute dialog as a follow-up — same
                # entry point as the menu item so the two paths
                # behave identically.
                self._on_help_contribute_data()
            return
        # No samples to nudge about — fall back to the simple
        # one-line information message we showed before this hook.
        box.setText(text)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_help_report_bug(self):
        """Help → Report a bug — collect a short bug summary from the
        user and email it to the configured address. Uses the
        ``email_notifications`` infrastructure already wired into the
        Setup tab so the SMTP credentials live in one place.
        """
        # Lazy import — keeps the menu instant even if the email
        # module isn't loaded yet.
        try:
            from ..notifications import send_bug_report
        except Exception:
            send_bug_report = None
        # Quick three-field collector: title + steps + expected /
        # actual. ``QInputDialog.getMultiLineText`` is the closest
        # built-in match for the 1-screen form most users tolerate.
        title, ok = QtWidgets.QInputDialog.getText(
            self, "Report a bug", "Short summary (one line):")
        if not ok or not title.strip():
            return
        steps, ok = QtWidgets.QInputDialog.getMultiLineText(
            self, "Report a bug",
            "What were you trying to do? (Include steps, expected "
            "behaviour, and what actually happened.)")
        if not ok:
            return
        # Compose the report. We attach the installed version + the
        # last 50 lines of the log pane (if reachable) so the
        # maintainer has context without needing to ask.
        try:
            from .. import __version__ as installed
        except Exception:
            installed = "unknown"
        log_excerpt = ""
        try:
            tab = self._active_experiment_tab()
            if tab is not None and hasattr(tab, "log_pane"):
                full = tab.log_pane.toPlainText()
                lines = full.splitlines()
                log_excerpt = "\n".join(lines[-50:])
        except Exception:
            pass
        body = (
            f"Title: {title.strip()}\n"
            f"Reporter: {self.setup_tab.current_user_name() or 'anonymous'}\n"
            f"Email: {self.setup_tab.current_user_email() or '(none)'}\n"
            f"Version: {installed}\n\n"
            f"Description:\n{steps.strip() or '(none)'}\n\n"
            f"--- last 50 log lines ---\n{log_excerpt}\n"
        )
        if send_bug_report is None:
            QtWidgets.QMessageBox.information(
                self, "Report saved",
                "The notification module isn't available, so the report "
                "wasn't auto-sent. The text below was prepared for you — "
                "copy it into an email to the project maintainer:\n\n"
                + body)
            return
        try:
            send_bug_report(title=title.strip(), body=body)
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Send failed",
                f"Could not send the bug report — the email server "
                f"refused the request:\n\n{e}\n\n"
                f"Copy the report below into an email manually:\n\n"
                + body)
            return
        QtWidgets.QMessageBox.information(
            self, "Bug report sent",
            "Thanks — the report has been emailed to the maintainer.")

    def _on_help_reset_potentials(self):
        """Help → Reset learned electrode potentials… — wipe the
        per-coating learned-OCP store.

        The store (:mod:`stimtest.electrode_potential_history`)
        accumulates E_ret rest potentials per coating and, once enough
        samples land, uses the running mean as the live OCP for that
        coating.  Early PULSAR builds recorded WRONG values (the
        baseline-subtraction bugs, since fixed), so the Pt bin in
        particular is contaminated — the operator turned the reference
        toggle OFF to avoid it.  This clears the store so future
        captures re-learn cleanly.  Offers ALL or Pt-only.
        """
        try:
            from ..electrode_potential_history import summary, reset
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Reset learned potentials",
                f"Could not access the learned-potential store:\n\n{e}")
            return
        try:
            bins = summary()   # [(coating_key, n, ocp_or_None), …]
        except Exception:
            bins = []
        if bins:
            lines = "\n".join(
                f"  • {k}: {n} sample(s)"
                + (f", learned OCP {ocp:+.3f} V" if ocp is not None else "")
                for k, n, ocp in bins)
        else:
            lines = "  (the store is already empty)"
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Reset learned electrode potentials")
        box.setText(
            "Clear the open-circuit potentials PULSAR has learned per "
            "coating?\n\nCurrent store:\n" + lines
            + "\n\nThis cannot be undone. Future captures re-learn from "
              "scratch.")
        b_all = box.addButton(
            "Reset ALL", QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        b_pt = box.addButton(
            "Reset Pt only", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton(QtWidgets.QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(b_all)
        box.exec()
        clicked = box.clickedButton()
        if clicked is None or clicked not in (b_all, b_pt):
            return
        try:
            if clicked is b_pt:
                reset("Pt")
                what = "Pt"
            else:
                reset(None)
                what = "all coatings"
            QtWidgets.QMessageBox.information(
                self, "Reset learned potentials",
                f"Cleared the learned electrode potentials for {what}.")
            try:
                self.log_pane.log(
                    f"Learned electrode potentials reset ({what}).")
            except Exception:
                pass
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Reset learned potentials",
                f"Reset failed:\n\n{e}")

    def _on_help_contribute_data(self):
        """Help → Contribute electrode data… — open the contribution
        dialog.

        The dialog reads the per-coating sample bins from
        :mod:`stimtest.electrode_potential_history`, lets the user
        opt in (per-field) to academic-attribution metadata,
        previews the exact JSON payload, and offers two transports:

        * **Open GitHub Issue** — pre-fills a new-issue URL on the
          project repository and opens the user's browser. The user
          reviews and clicks Submit themselves; nothing is uploaded
          by the application.
        * **Save to file** — writes the JSON to disk so the user
          can attach it through whatever channel their institution
          allows.

        Failure path: a missing :mod:`PyQt6` would never reach this
        method (the GUI couldn't have started without it), and the
        dialog itself swallows export-side errors so a malformed
        local store doesn't crash the menu action.
        """
        try:
            from .contribute_dialog import ContributeDialog
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Contribute electrode data",
                f"Could not open the contribution dialog:\n\n{e}")
            return
        dlg = ContributeDialog(self.setup_tab, parent=self)
        dlg.exec()

    def _help_data_sample_count(self) -> int:
        """How many electrode-potential samples are currently in the
        local store. Helper for the update-check dialog so it can
        decide whether to surface a "would you like to contribute?"
        nudge alongside the version comparison.
        """
        try:
            from ..electrode_potential_history import all_bins
            return sum(len(v) for v in all_bins().values())
        except Exception:
            return 0

    def _on_help_damage_info(self):
        """Help → Tissue damage prediction info — explainer dialog.

        Renders a single rich-text panel with five sections:

        1. **Shannon equation** — definition + the 1.5–2.0 threshold
           range + the 63–66% false-negative-rate caveat from Li
           et al. 2024.
        2. **Modified Shannon caps** — the macroelectrode 30 µC/cm²
           cap (Cogan 2016 / FDA DBS limit) and the microelectrode
           4 nC/ph cap (McCreery, Pikov, Troyk 2010), with the GSA
           bands they apply to.
        3. **Vatsyayan–Dayeh universal model (2022)** — modern
           Shannon extension that incorporates electrode material,
           size, and inter-electrode spacing. Referenced for users
           who want material-aware screening; not implemented in
           this build.
        4. **NeurostimML (Li et al. 2024)** — the higher-accuracy
           ML prediction; hosted web tool URL plus a note that the
           local-inference path is available via Help → Install
           NeurostimML model… once the user grants permission.
        5. **0–4 damage scale + recommended reporting fields** —
           literature-standard scoring convention and the 11
           parameters every paper should disclose, sourced from
           §4 of Li et al. 2024.

        Every reference DOI is rendered as a live link so the user
        can read the source material without leaving the GUI.
        """
        try:
            from ..damage_models import (
                SHANNON_K_THRESHOLD,
                MACROELECTRODE_GSA_CM2_MIN,
                MACROELECTRODE_CHARGE_DENSITY_LIMIT_UC_PER_CM2,
                MICROELECTRODE_GSA_UM2_MAX,
                MICROELECTRODE_CHARGE_PER_PHASE_LIMIT_NC_PER_PHASE,
                NEUROSTIMML_WEB_URL,
                NEUROSTIMML_GITHUB_REPO,
                SHANNON_1992_DOI,
                MCCREERY_2010_DOI,
                COGAN_2016_DOI,
                VATSYAYAN_DAYEH_2022_DOI,
                LI_2024_DOI,
                DAMAGE_LEVELS,
                RECOMMENDED_REPORT_FIELDS,
            )
        except Exception:
            QtWidgets.QMessageBox.warning(
                self, "Tissue damage prediction info",
                "The damage_models module isn't available in this "
                "build.")
            return
        # Build the 0-4 scale table from the canonical constant so the
        # dialog stays in sync with damage_models.DAMAGE_LEVELS.
        damage_scale_rows = "\n".join(
            f"<tr><td><b>{lvl}</b></td><td>{label}</td>"
            f"<td>{desc}</td></tr>"
            for lvl, label, desc in DAMAGE_LEVELS
        )
        # Reporting fields render as a simple bulleted list.
        report_items = "\n".join(
            f"<li>{label}</li>"
            for _key, label in RECOMMENDED_REPORT_FIELDS
        )
        body = (
            f"<h3>Tissue damage prediction</h3>"
            f"<p>This GUI screens every captured pulse against the "
            f"<b>Shannon equation</b> and several refinements from "
            f"the neurostimulation safety literature, then surfaces "
            f"the verdict in the Viewer's per-capture metric table. "
            f"The screen is GUIDANCE — never a hard interlock.</p>"

            f"<h4>1. Shannon equation (1992)</h4>"
            f"<p><i>k</i> = log<sub>10</sub>(charge density) "
            f"+ log<sub>10</sub>(charge per phase), with charge "
            f"density in µC/cm²/ph and charge in µC/ph. The 1992 "
            f"paper draws a 2-D log-log boundary at "
            f"<i>k</i>&nbsp;=&nbsp;1.5–2.0; this build defaults to "
            f"<i>k</i>&nbsp;=&nbsp;<b>{SHANNON_K_THRESHOLD:.2f}</b>, "
            f"the most-cited threshold in the modern literature.</p>"
            f"<p style='color:#a00'><b>Critical caveat</b>: per Li "
            f"et al. 2024, the standalone Shannon equation has "
            f"<b>~64% accuracy</b> on a 385-entry curated database "
            f"and misclassifies <b>63–66% of damaging stimulation "
            f"parameters as safe</b> at <i>k</i>&nbsp;=&nbsp;1.85. "
            f"Treat a 'likely safe' verdict as a coarse first "
            f"screen, not a green light.</p>"
            f"<p>Reference: Shannon, R. V. <i>A model of safe levels "
            f"for electrical stimulation</i>. IEEE Trans. Biomed. "
            f"Eng. 39(4):424-426. "
            f"<a href=\"{SHANNON_1992_DOI}\">{SHANNON_1992_DOI}</a></p>"

            f"<h4>2. Modified Shannon caps</h4>"
            f"<p>The 2-D Shannon line under-predicts microelectrode "
            f"damage and is overly conservative for some macro-"
            f"electrodes, so two band-gated caps are commonly "
            f"applied alongside it:</p>"
            f"<ul>"
            f"<li><b>Macroelectrodes</b> "
            f"(GSA &gt; {MACROELECTRODE_GSA_CM2_MIN:.3f}&nbsp;cm²): "
            f"charge density &lt; "
            f"<b>{MACROELECTRODE_CHARGE_DENSITY_LIMIT_UC_PER_CM2:.0f}"
            f"&nbsp;µC/cm²/ph</b>. Matches the FDA limit for DBS "
            f"leads.</li>"
            f"<li><b>Microelectrodes</b> "
            f"(GSA &lt; {MICROELECTRODE_GSA_UM2_MAX:.0f}&nbsp;µm²): "
            f"charge per phase &lt; "
            f"<b>{MICROELECTRODE_CHARGE_PER_PHASE_LIMIT_NC_PER_PHASE:.0f}"
            f"&nbsp;nC/ph</b>. Microelectrodes can damage tissue "
            f"BELOW the Shannon line, so this cap is "
            f"complementary, not redundant.</li>"
            f"</ul>"
            f"<p>References:</p>"
            f"<ul>"
            f"<li>McCreery, D., Pikov, V., Troyk, P. R. (2010). "
            f"<i>Neuronal loss due to prolonged controlled-current "
            f"stimulation with chronically implanted "
            f"microelectrodes in the cat cerebral cortex</i>. "
            f"J. Neural Eng. 7(3):036005. "
            f"<a href=\"{MCCREERY_2010_DOI}\">{MCCREERY_2010_DOI}</a> "
            f"— original source of the 4 nC/ph microelectrode cap.</li>"
            f"<li>Cogan, S. F., Ludwig, K. A., Welle, C. G., "
            f"Takmakov, P. (2016). <i>Tissue damage thresholds "
            f"during therapeutic electrical stimulation</i>. "
            f"J. Neural Eng. 13(2):021001. "
            f"<a href=\"{COGAN_2016_DOI}\">{COGAN_2016_DOI}</a> "
            f"— review consolidating the Modified Shannon framework.</li>"
            f"</ul>"

            f"<h4>3. Vatsyayan–Dayeh universal model (2022)</h4>"
            f"<p>A modern Shannon extension that adds electrode "
            f"site material, electrode size, and inter-electrode "
            f"spacing through electrochemical-impedance and charge-"
            f"injection-capacity terms. Bridges the gap between "
            f"Shannon's purely-charge view and a fully feature-rich "
            f"ML model. <b>Not implemented in this build</b> — "
            f"flagged here as a future direction when material-"
            f"aware screening is wanted.</p>"
            f"<p>Reference: Vatsyayan, R. &amp; Dayeh, S. A. (2022). "
            f"<i>A universal model of electrochemical safety limits "
            f"in vivo for electrophysiological stimulation</i>. "
            f"Front. Neurosci. 16:972252. "
            f"<a href=\"{VATSYAYAN_DAYEH_2022_DOI}\">"
            f"{VATSYAYAN_DAYEH_2022_DOI}</a></p>"

            f"<h4>4. NeurostimML — higher-accuracy ML prediction</h4>"
            f"<p>Li <i>et al.</i> (2024) trained four ML algorithms "
            f"on a 385-entry curated database from 58 publications. "
            f"Their public web-portal model "
            f"(<b>RF-Partial-19</b>: Random Forest, 19 trees, 13 "
            f"features) hits <b>88% accuracy</b> with a <b>3% "
            f"false-negative rate</b> — vs Shannon's 63–66% FNR. "
            f"The most predictive features are <b>waveform shape</b>, "
            f"daily accumulated charge, charge density, charge per "
            f"phase, and total pulses.</p>"
            f"<p><b>Web tool</b> (no install required): "
            f"<a href=\"{NEUROSTIMML_WEB_URL}\">"
            f"{NEUROSTIMML_WEB_URL}</a></p>"
            f"<p><b>Local inference</b>: see "
            f"<i>Help → Install NeurostimML model…</i>. Once "
            f"installed, every capture in the Viewer carries the "
            f"ML verdict alongside the Shannon screen.</p>"
            f"<p>Source data + trained models: "
            f"<a href=\"{NEUROSTIMML_GITHUB_REPO}\">"
            f"{NEUROSTIMML_GITHUB_REPO}</a></p>"
            f"<p>Reference: Li, Y. <i>et al.</i> (2024). "
            f"<i>NeurostimML: a machine learning model for "
            f"predicting neurostimulation-induced tissue damage</i>. "
            f"J. Neural Eng. 21(3):036054. "
            f"<a href=\"{LI_2024_DOI}\">{LI_2024_DOI}</a></p>"

            f"<h4>5. Damage-level scale (literature standard)</h4>"
            f"<p>The McCreery / Shepherd / Li convention scores "
            f"histological / functional damage on a 0–4 scale. The "
            f"binary classifier in this GUI maps to these levels:</p>"
            f"<table border='1' cellpadding='4' cellspacing='0' "
            f"style='border-collapse: collapse;'>"
            f"<tr><th>Level</th><th>Label</th><th>Description</th></tr>"
            f"{damage_scale_rows}"
            f"</table>"
            f"<p>Levels 0–1 collapse to the binary 'non-damaging' "
            f"verdict; levels 2–4 collapse to 'damaging'.</p>"

            f"<h4>6. Recommended reporting</h4>"
            f"<p>Li et al. 2024 §4 Discussion recommends every "
            f"neural-stimulation publication explicitly disclose:</p>"
            f"<ul>{report_items}</ul>"

            f"<h4>Caveats</h4>"
            f"<ul>"
            f"<li>None of these models accounts for stimulation "
            f"frequency or stimulus duty cycle independently of "
            f"daily-accumulated-charge — Shannon himself flagged "
            f"this as a missing variable in the 1992 paper, and Li "
            f"et al. confirm it for the partial-feature ML model "
            f"too.</li>"
            f"<li>Shannon is calibrated on cat cortex; "
            f"applicability to peripheral nerve, spinal cord, or "
            f"different species is not guaranteed.</li>"
            f"<li>The verdict in the metric table is informational. "
            f"Cross-check with the published damage thresholds for "
            f"the specific tissue + electrode combination you're "
            f"working with.</li>"
            f"</ul>"
        )
        # Use a QMessageBox with rich-text + link-clickable flags so
        # the DOI / web-tool URLs open in the user's browser. The
        # ``setTextFormat`` + ``setTextInteractionFlags`` pair is
        # the same pattern :func:`_check_plexstim_prereq` uses for
        # its installer-link warning dialog.
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Tissue damage prediction info")
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        box.setTextFormat(QtCore.Qt.TextFormat.RichText)
        box.setText(body)
        box.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_help_install_neurostimml(self):
        """Help → Install NeurostimML model… — manage the local
        RF-Partial-19 pickle.

        Opens a modal dialog with:

        * **Status line** — installed-yes-or-no, plus the on-disk
          path and SHA256 fingerprint when present.
        * **Source URL** — the public Bleris Lab GitHub raw URL
          the install path fetches from. Editable so an offline
          lab can override to a vendored copy.
        * **Pickle warning** — a prominent line reminding the user
          that loading a pickle runs arbitrary Python code, so
          the source URL must be trusted.
        * **Install / Re-download / Uninstall** buttons.

        The actual download + verification logic lives in
        :func:`stimtest.neurostimml.install_model_from_url`; this
        method just drives it from a Qt dialog.
        """
        try:
            from .. import neurostimml
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self, "Install NeurostimML model",
                f"The neurostimml module isn't available in this "
                f"build:\n\n{e}")
            return

        # ---- modal dialog ----
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Install NeurostimML model")
        dlg.setMinimumWidth(640)

        intro = QtWidgets.QLabel(
            "<h3>NeurostimML — local inference</h3>"
            "<p>Install the <b>RF-Partial-19</b> Random Forest "
            "classifier from Li <i>et al.</i> 2024 so every "
            "captured pulse gets a higher-accuracy ML damage "
            "prediction (88% accuracy, 3% false-negative rate) "
            "alongside the built-in Shannon screen (~64% accuracy, "
            "63–66% FNR).</p>"
            "<p style='color:#a00'><b>Pickle trust note</b>: "
            "loading a Python pickle executes arbitrary code. "
            "Only install from a source URL you trust. The "
            "default URL points to the paper's public GitHub "
            "repository and is served over HTTPS; verify the "
            "SHA-256 below against any known-good fingerprint "
            "you have for the file before relying on the model.</p>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(QtCore.Qt.TextFormat.RichText)
        intro.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)

        # Live status box — refreshed by ``_refresh_status``.
        status = QtWidgets.QLabel("")
        status.setWordWrap(True)
        status.setTextFormat(QtCore.Qt.TextFormat.RichText)
        status.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        url_input = QtWidgets.QLineEdit(neurostimml.MODEL_DOWNLOAD_URL)
        url_input.setToolTip(
            "Source URL the install path fetches the pickle from. "
            "Override for an offline mirror or a vendored copy.")
        url_label = QtWidgets.QLabel("Source URL:")

        sha_input = QtWidgets.QLineEdit("")
        sha_input.setPlaceholderText(
            "Optional expected SHA-256 to verify against.")
        sha_input.setToolTip(
            "If you have a known-good SHA-256 fingerprint (e.g. from "
            "a release note), paste it here. The download is rejected "
            "if the digest doesn't match. Leave blank to accept "
            "whatever bytes the URL serves.")
        sha_label = QtWidgets.QLabel("Expected SHA-256 (optional):")

        btn_install = QtWidgets.QPushButton("Install / Re-download")
        btn_uninstall = QtWidgets.QPushButton("Uninstall")
        btn_close = QtWidgets.QPushButton("Close")
        btn_close.setDefault(True)
        btn_close.clicked.connect(dlg.accept)

        def _refresh_status():
            """Re-render the install-state line based on the
            current on-disk file. Called after install / uninstall
            so the user sees the result immediately.
            """
            if neurostimml.model_is_installed():
                p = neurostimml.model_path()
                try:
                    digest = neurostimml.compute_sha256(p)
                except Exception:
                    digest = "(unable to read)"
                status.setText(
                    f"<b style='color:#009E73'>Installed.</b> "
                    f"Path: <code>{p}</code><br>"
                    f"SHA-256: <code>{digest}</code>"
                )
                btn_uninstall.setEnabled(True)
            else:
                status.setText(
                    "<b style='color:#777'>Not installed.</b> "
                    "Click <i>Install / Re-download</i> below to "
                    "fetch the model.")
                btn_uninstall.setEnabled(False)

        def _on_install_clicked():
            """Drive ``install_model_from_url`` from the dialog,
            converting any thrown exception into a user-visible
            message rather than a stack trace.
            """
            url = url_input.text().strip()
            if not url:
                QtWidgets.QMessageBox.warning(
                    dlg, "URL required",
                    "Provide the source URL before installing.")
                return
            expected = sha_input.text().strip() or None
            # Confirm before downloading — the user reads the
            # source URL one more time and clicks through.
            confirm = QtWidgets.QMessageBox.question(
                dlg, "Confirm install",
                f"Download and install the NeurostimML model from:\n\n"
                f"{url}\n\n"
                f"Loading the resulting pickle will execute Python "
                f"code from this URL when inference runs. Proceed?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            QtWidgets.QApplication.setOverrideCursor(
                QtGui.QCursor(QtCore.Qt.CursorShape.WaitCursor))
            try:
                p, digest = neurostimml.install_model_from_url(
                    url, expected_sha256=expected,
                )
            except Exception as e:
                QtWidgets.QApplication.restoreOverrideCursor()
                QtWidgets.QMessageBox.critical(
                    dlg, "Install failed",
                    f"Could not install the NeurostimML model:\n\n{e}")
                return
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.information(
                dlg, "Installed",
                f"NeurostimML model written to:\n{p}\n\n"
                f"SHA-256: {digest}")
            _refresh_status()

        def _on_uninstall_clicked():
            """Delete the local pickle. Confirms before removal."""
            confirm = QtWidgets.QMessageBox.question(
                dlg, "Confirm uninstall",
                "Delete the local NeurostimML model?\n\n"
                "Existing capture metrics keep their previous "
                "verdicts; future captures fall back to the "
                "Shannon screen until the model is re-installed.",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.No,
                QtWidgets.QMessageBox.StandardButton.No,
            )
            if confirm != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            removed = neurostimml.uninstall_model()
            if removed:
                QtWidgets.QMessageBox.information(
                    dlg, "Uninstalled",
                    "Local NeurostimML model removed.")
            else:
                QtWidgets.QMessageBox.warning(
                    dlg, "Nothing to remove",
                    "No model file was present to remove.")
            _refresh_status()

        btn_install.clicked.connect(_on_install_clicked)
        btn_uninstall.clicked.connect(_on_uninstall_clicked)

        # Layout assembly.
        form = QtWidgets.QFormLayout()
        form.addRow(url_label, url_input)
        form.addRow(sha_label, sha_input)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(btn_install)
        btn_row.addWidget(btn_uninstall)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)
        v = QtWidgets.QVBoxLayout(dlg)
        v.addWidget(intro)
        v.addWidget(status)
        v.addLayout(form)
        v.addLayout(btn_row)
        _refresh_status()
        dlg.exec()

    def _on_help_last_calibration(self):
        """Help → Last stimulator verification — shows when the per-channel
        PlexStim test-board stimulator verification was last recorded. Reads
        the saved-at timestamp embedded in
        ``calibration.json`` (falling back to the file's mtime
        if the timestamp field is missing), or tells the user
        how to run one when the file doesn't exist."""
        from .calibration import calibration_path, last_calibration_datetime
        ts = last_calibration_datetime()
        path = calibration_path()
        if ts is None:
            body = (
                "<b>No stimulator verification on record.</b><br><br>"
                "The per-channel PlexStim test-board stimulator verification "
                "file does not exist yet at:<br>"
                f"<code>{path}</code><br><br>"
                "Plug the channel array into the PlexStim test "
                "board and open <b>Run → Stimulator Verification…</b> to run the "
                "amplitude sweep that fits the per-channel gain / "
                "offset, then save it from the wizard."
            )
        else:
            # ``datetime.isoformat`` gives "YYYY-MM-DDTHH:MM:SS.ffffff" —
            # split into the user-readable "YYYY-MM-DD HH:MM:SS"
            # form. Includes age in days so the user can decide
            # whether a re-calibration is overdue at a glance.
            age = datetime.now() - ts
            days = age.days
            if days <= 0:
                age_phrase = "today"
            elif days == 1:
                age_phrase = "1 day ago"
            else:
                age_phrase = f"{days} days ago"
            body = (
                f"<b>Last stimulator verification recorded:</b><br>"
                f"<code>{ts.strftime('%Y-%m-%d %H:%M:%S')}</code> "
                f"({age_phrase})<br><br>"
                f"File: <code>{path}</code><br><br>"
                f"Re-run via <b>Run → Stimulator Verification…</b> when the "
                f"PlexStim has been opened (firmware reset), the "
                f"channel array has been swapped, or your lab's "
                f"stimulator verification interval has elapsed."
            )
        QtWidgets.QMessageBox.information(
            self, "Last stimulator verification", body)

    # ------------------------------------------------- first-launch setup
    def _maybe_run_first_launch_setup(self) -> None:
        """Show the one-time admin password setup dialog if this is
        a first launch.

        Called from inside ``_load_prefs_into_tabs`` right after the
        admin prefs block.  No-op when:

        * ``_admin_setup_completed`` is already True (operator went
          through the dialog or has a custom password from before
          the dialog existed), OR
        * Tests / programmatic instantiation set the env var
          ``PULSAR_SKIP_FIRST_LAUNCH_SETUP=1`` (the test suite uses
          this to avoid spawning a modal dialog from headless
          fixtures).

        On a real first launch, shows the modal dialog and persists
        the outcome immediately so a crash before normal prefs save
        doesn't lose the setup-completed flag (re-prompting the
        operator who already answered would be annoying).
        """
        import os

        if self._admin_setup_completed:
            return
        # Test-friendly escape hatch: integration tests want to
        # construct a MainWindow without the modal blocking.  Set
        # the env var in the test fixture, not in production paths.
        if os.environ.get("PULSAR_SKIP_FIRST_LAUNCH_SETUP"):
            self.log_pane.log(
                "[admin] first-launch setup skipped via "
                "PULSAR_SKIP_FIRST_LAUNCH_SETUP env var")
            return

        try:
            new_hash, ok = prompt_first_launch_setup(self)
        except Exception as e:
            # Defensive — never let the setup dialog crash the
            # whole launch.  Log and fall through with default
            # password.
            self.log_pane.log(
                f"[admin] first-launch setup dialog failed: "
                f"{type(e).__name__}: {e}; keeping factory default")
            self._admin_setup_completed = True
            try:
                save_prefs(self._collect_prefs_payload())
            except Exception:
                pass
            return

        if ok and new_hash:
            self._admin_password_hash = new_hash
            self.log_pane.log(
                "[admin] first-launch password set; saved to prefs")
        else:
            self.log_pane.log(
                "[admin] first-launch setup skipped; factory default "
                "password remains in effect (change it via Admin → "
                "Manage Custom Catalog → Change password…)")
        # Either way, mark setup_completed so we don't re-prompt.
        self._admin_setup_completed = True
        try:
            save_prefs(self._collect_prefs_payload())
        except Exception as _e:
            self.log_pane.log(
                f"[admin] first-launch setup persist failed: "
                f"{type(_e).__name__}: {_e}")

    # -------------------------------------------------------------- profile
    def _on_admin_login(self) -> None:
        """Admin → Log In — prompt for password (and optionally a
        username, when an extension profile is registered) and apply
        the resolved profile.

        The dialog adapts automatically: in a default install with
        no extensions, it's password-only (classic admin login).
        With any extension installed, a Username field appears so
        the operator can pick between Admin and the extension
        profile.  Wrong credentials → "Incorrect password" message
        (the user-facing string deliberately mentions only the Admin
        profile — extensions are documented separately to the
        people who need them).
        """
        # Seed the dialog's Username dropdown with the user's prior
        # successful logins — convenience so they don't retype
        # extension usernames every session.  History persists in
        # prefs under ``admin.login_history`` (most-recent-first,
        # capped at admin.LOGIN_HISTORY_MAX).
        history = list(self._admin_login_history)
        profile_name, ok = prompt_login(
            self,
            admin_hash=self._admin_password_hash,
            history=history,
        )
        if not ok:
            # Cancel and wrong-password both return (NONE, False).
            # We err on the side of showing feedback — silent failure
            # on a wrong password is worse UX than the occasional
            # "Log in failed" popup after a cancel.
            # Failure-popup wording adapts to the install state so an
            # admin-only install (no extensions registered → no Username
            # field shown) doesn't reference a field the operator was
            # never asked for.  See audit finding #10.
            from .admin import _any_extension_registered
            if _any_extension_registered():
                msg = ("Incorrect username or password.\n\n"
                       "Leave Username blank (or type 'admin') and "
                       "enter the admin password.")
            else:
                msg = ("Incorrect password.\n\n"
                       "Enter the admin password to continue.")
            QtWidgets.QMessageBox.warning(self, "Log in failed", msg)
            return
        # Successful login — record the username in history (admin
        # path with blank username is silently dropped by
        # update_login_history).  Persist immediately so a crash
        # before normal save doesn't lose the entry.
        from .admin import update_login_history
        self._admin_login_history = update_login_history(
            self._admin_login_history, profile_name)
        try:
            save_prefs(self._collect_prefs_payload())
        except Exception as _e:
            self.log_pane.log(
                f"Login-history persist failed: "
                f"{type(_e).__name__}: {_e}")
        self._set_profile(profile_name)
        # Status bar message uses the extension's display_name when
        # one was registered (e.g., "CWRU collaborator" instead of
        # just "CWRU"); falls back to the uppercase profile name
        # for built-in Admin or extensions without a display_name.
        # Audit #17.
        from .admin import _extension_display_names
        display = _extension_display_names.get(
            profile_name.lower(), profile_name.upper())
        self.statusBar().showMessage(
            f"Logged in as {display}.", 4000)

    def _on_admin_logout(self) -> None:
        """Admin → Log Out — drop to anonymous (Profile.NONE)."""
        prev = self._current_profile
        self._set_profile(Profile.NONE.value)
        if prev != Profile.NONE.value:
            self.statusBar().showMessage(
                f"{str(prev).upper()} logged out.", 4000)

    def _set_profile(self, profile) -> None:
        """Apply ``profile`` to the live UI.

        ``profile`` may be a :class:`Profile` enum member, its string
        value (``"admin"`` / ``"none"``), or an extension profile
        name string (e.g., the name a collaborator package
        registered).  Internally we always store the lowercase
        string form so comparisons against either built-in or
        extension names work uniformly.
        """
        # Normalize to lowercase string form.  Profile is a str-enum
        # so ``Profile.ADMIN.value == "admin"`` — both members and
        # raw strings collapse to the same shape.
        try:
            name = (profile.value
                    if hasattr(profile, "value")
                    else str(profile)).strip().lower()
        except Exception:
            name = Profile.NONE.value
        self._current_profile = name
        # Menu visibility — Log In shows when logged out, Log Out
        # shows when logged in (any profile, built-in or extension).
        # Catalog editor is ADMIN-only because it can also change the
        # admin password.
        logged_in = (name != Profile.NONE.value)
        self._act_admin_login.setVisible(not logged_in)
        self._act_admin_logout.setVisible(logged_in)
        self._act_admin_catalog.setEnabled(is_admin(name))
        # Export Profile is admin-gated (distributing a profile is
        # privileged); Import stays open to all so a collaborator can
        # load a profile file they were given without admin rights.
        if hasattr(self, "_act_export_profile"):
            self._act_export_profile.setEnabled(is_admin(name))
        # Broadcast to every PatternPanel in every experiment tab so
        # the asymmetric / symmetric shape dropdowns add / remove the
        # restricted entries.  Best-effort: a stale tab that doesn't
        # expose ``pattern_panel`` (e.g. a test stub) is silently
        # skipped.
        for tab in self._experiment_tabs():
            pp = getattr(tab, "pattern_panel", None)
            if pp is None:
                continue
            try:
                pp.set_profile(name)
            except Exception as _e:
                self.log_pane.log(
                    f"Profile broadcast to {type(tab).__name__} failed: "
                    f"{type(_e).__name__}: {_e}")

    # ------------------------------------------- profile import / export
    def _register_profile_payload(self, data, *, source="(payload)"):
        """Validate a profile dict and register it via the plugin host.

        Returns ``(ok: bool, message: str)``.  Shared by the Import
        action and the startup re-load of persisted profiles.  Pure
        DATA — no code execution — so importing a profile file is safe:
        it only registers ``{name, password_hash, shapes}`` against the
        shape primitives already built into ``stimtest.waveforms``.
        """
        from ..waveforms import PHASE_SHAPES
        if not isinstance(data, dict):
            return False, f"Profile {source} is not a JSON object."
        name = str(data.get("name", "")).strip().lower()
        phash = str(data.get("password_hash", "")).strip().lower()
        display = data.get("display_name") or None
        raw_shapes = data.get("shapes") or []
        if isinstance(raw_shapes, str):
            return False, "Profile 'shapes' must be a list, not a string."
        if not name:
            return False, "Profile is missing a 'name'."
        # SHA-256 hex digest is exactly 64 lowercase hex chars.
        if len(phash) != 64 or any(c not in "0123456789abcdef" for c in phash):
            return False, (
                f"Profile {name!r} has an invalid password_hash "
                f"(expected a 64-char SHA-256 hex digest).")
        shapes = [str(s).strip() for s in raw_shapes if str(s).strip()]
        known = [s for s in shapes if s in PHASE_SHAPES]
        unknown = [s for s in shapes if s not in PHASE_SHAPES]
        try:
            register_extension_profile(
                name=name, password_hash=phash,
                shapes=set(known), display_name=display)
        except ValueError as e:
            return False, f"Could not register profile {name!r}: {e}"
        label = display or name.upper()
        msg = f"{label} ({len(known)} shape(s))"
        if unknown:
            msg += (f" — ignored {len(unknown)} unknown shape(s): "
                    f"{', '.join(unknown)}")
        return True, msg

    def _persist_imported_profile(self, data) -> None:
        """Add/replace a profile in the persisted ``imported_profiles``
        list (de-duped by name) and save prefs so it re-registers on
        the next launch."""
        name = str(data.get("name", "")).strip().lower()
        if not name:
            return
        record = {
            "name": name,
            "display_name": data.get("display_name") or name.upper(),
            "password_hash": str(
                data.get("password_hash", "")).strip().lower(),
            "shapes": [str(s) for s in (data.get("shapes") or [])],
        }
        self._imported_profiles = [
            p for p in self._imported_profiles
            if str(p.get("name", "")).strip().lower() != name]
        self._imported_profiles.append(record)
        try:
            self._save_prefs_from_tabs()
        except Exception as e:
            self.log_pane.log(f"Could not persist imported profile: {e}")

    def _load_imported_profiles(self) -> None:
        """Re-register profiles the operator imported in a prior session
        (persisted under prefs ``imported_profiles``).  Best-effort per
        profile; a collision with a same-name pip extension is logged
        and skipped (the extension already won)."""
        for rec in list(self._imported_profiles):
            ok, msg = self._register_profile_payload(rec, source="prefs")
            if ok:
                self.log_pane.log(f"Re-registered imported profile: {msg}")
            else:
                self.log_pane.log(f"Imported profile skipped: {msg}")

    def _on_import_profile(self) -> None:
        """Admin → Import Profile — load a ``.json`` profile file and
        register it.  OPEN to all users (logging in to USE the profile
        still needs the password).  Persists so it survives restarts."""
        import json
        from PyQt6.QtWidgets import QFileDialog, QMessageBox
        path, _ = QFileDialog.getOpenFileName(
            self, "Import profile", str(self.save_dir),
            "PULSAR profile (*.json *.pulsarprofile);;All files (*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as e:
            QMessageBox.warning(
                self, "Import profile",
                f"Could not read profile file:\n{e}")
            return
        ok, msg = self._register_profile_payload(data, source=path)
        if not ok:
            QMessageBox.warning(self, "Import profile", msg)
            return
        self._persist_imported_profile(data)
        # Re-broadcast the current profile so every PatternPanel + the
        # login dialog pick up the newly-registered profile / shapes.
        self._set_profile(self._current_profile)
        self.log_pane.log(f"Imported profile: {msg}")
        self.statusBar().showMessage(f"Imported profile: {msg}")
        QMessageBox.information(
            self, "Import profile",
            f"Imported {msg}.\n\nLog in with this profile's password "
            f"(Admin → Log In…) to use its shapes.")

    def _on_export_profile(self) -> None:
        """Admin → Export Profile — write a registered profile to a
        ``.json`` file for distribution.  Admin-gated."""
        import json
        from PyQt6.QtWidgets import (
            QFileDialog, QMessageBox, QInputDialog)
        if not is_admin(self._current_profile):
            QMessageBox.information(
                self, "Export profile",
                "Exporting a profile requires admin login "
                "(Admin → Log In…).")
            return
        names = list_extension_profiles()
        if not names:
            QMessageBox.information(
                self, "Export profile",
                "No profiles are registered to export.\n\nInstall or "
                "import a profile (e.g. CWRU) first.")
            return
        if len(names) == 1:
            chosen = names[0]
        else:
            chosen, ok = QInputDialog.getItem(
                self, "Export profile", "Profile to export:",
                names, 0, False)
            if not ok or not chosen:
                return
        prof = get_extension_profile(chosen)
        if prof is None:
            QMessageBox.warning(
                self, "Export profile", f"Profile {chosen!r} not found.")
            return
        payload = {
            "format": "pulsar-profile",
            "format_version": 1,
            "name": prof["name"],
            "display_name": prof["display_name"],
            "password_hash": prof["password_hash"],
            "shapes": prof["shapes"],
        }
        path, _ = QFileDialog.getSaveFileName(
            self, "Export profile",
            str(self.save_dir / f"{prof['name']}.pulsarprofile.json"),
            "PULSAR profile (*.json *.pulsarprofile)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
        except Exception as e:
            QMessageBox.warning(
                self, "Export profile",
                f"Could not write profile file:\n{e}")
            return
        self.log_pane.log(
            f"Exported profile {prof['name']!r} → {path}")
        self.statusBar().showMessage(f"Exported profile → {path}")
        QMessageBox.information(
            self, "Export profile",
            f"Exported {prof['display_name']} to:\n{path}\n\nShare this "
            f"file with collaborators and tell them the password "
            f"separately — the file contains only the hash, so it "
            f"can't be used to log in on its own.")

    # ---------------------------------------------------- extensions
    def _load_extensions(self) -> None:
        """Auto-discover and import installed ``stimtest_*``
        extension packages.

        VS Code-style plugin model: any top-level package whose
        ``__name__`` starts with ``stimtest_`` and lives on the
        active Python path is imported here.  The package's
        ``__init__.py`` is expected to call
        :func:`stimtest.gui.admin.register_extension_profile` (and
        any other future registration hooks PULSAR exposes) at
        import time, which is when its profile + restricted shape
        set become live.

        After all discovery completes, every PatternPanel is asked
        to re-apply the current profile so it sees any newly-
        registered restricted shapes.  Without this re-broadcast,
        a CWRU operator launching PULSAR with the extension already
        installed would still see the public shape list until they
        logged in (because PatternPanel built its filter set
        BEFORE the extension registered its shapes).

        Best-effort:

        * No extensions found → silent (this is the public-default
          install, not an error).
        * One extension raises at import → the failure is logged
          with the package name; remaining extensions still load.
        * The whole loader fails (e.g. ``pkgutil`` raises) → the
          caller in ``__init__`` logs it; PULSAR still launches
          with built-in profiles only.

        Extensions discovered via ``importlib.metadata.distributions``
        which sees BOTH wheel-installed packages AND editable
        installs (PEP 660 finder-based, where pkgutil.iter_modules
        misses them).  ``stimtest_cwru`` is the canonical example;
        ``stimtest_*`` namespacing reserves the prefix for first-
        party + invited-collaborator extensions.
        """
        import importlib
        import importlib.metadata as _md

        # ``distributions()`` enumerates every installed
        # distribution Python's metadata API can see — that's the
        # union of site-packages installs, --user installs, and
        # editable installs (via the .dist-info / .egg-info
        # generated by pip).  Filter by Distribution Name (the
        # ``[project] name`` from pyproject.toml) so an unrelated
        # package with a ``stimtest_*`` module file but a
        # different distribution name doesn't trip us.
        found: list[str] = []
        failed: list[tuple[str, str]] = []
        seen: set[str] = set()
        for _dist in _md.distributions():
            try:
                name = (_dist.metadata.get("Name") or "").strip()
            except Exception:
                continue
            # Case-insensitive prefix check — PEP 503 says distribution
            # names are normalized lowercase for indexing, but the
            # ``Name:`` metadata field PRESERVES the case the package
            # author wrote in pyproject.toml.  Empirically ``PyQt6``,
            # ``Markdown``, ``PyVISA-py`` all return mixed case here,
            # so an extension pyproject with ``name = "Stimtest_Lab"``
            # would be silently skipped under a case-sensitive match.
            # Lowercasing the comparison closes that hole.
            if not name or not name.lower().startswith("stimtest_"):
                continue
            # Distribution names use hyphens (``stimtest-cwru``)
            # while the actual import name uses underscores
            # (``stimtest_cwru``) — PEP 503 normalization.  We
            # always import via the underscore form, and use the
            # lowercased name so a ``Stimtest-Lab`` distribution
            # imports as ``stimtest_lab`` (matches what
            # ``register_extension_profile`` will see at registration
            # — it also lowercases internally).
            mod_name = name.lower().replace("-", "_")
            # De-dup defensively (multiple .dist-info dirs for the
            # same package can happen in messy environments).
            if mod_name in seen:
                continue
            seen.add(mod_name)
            # Never auto-load ourselves (defensive — main package
            # is "stimtest", no underscore, won't match the prefix
            # gate above, but kept for clarity).
            if mod_name == "stimtest":
                continue
            # Capture log records emitted by the extension's import-
            # time side effects so warnings like "registration failed
            # with ValueError" (per ``stimtest_cwru.__init__`` line
            # 71's ``_log.warning(...)``) end up in PULSAR's LogPane
            # instead of stderr where the operator never looks.  See
            # audit finding #11.  We attach a temporary
            # ``logging.Handler`` scoped to JUST this import, drop it
            # in a ``finally``, then replay every captured record into
            # the LogPane.
            import logging
            log_records: list = []
            class _CaptureHandler(logging.Handler):
                def emit(self_h, record):
                    log_records.append(record)
            _h = _CaptureHandler(level=logging.WARNING)
            _ext_logger = logging.getLogger(mod_name)
            _ext_logger.addHandler(_h)
            # Make sure WARNING-and-up actually propagate to our
            # handler — most extensions won't have set a level.
            _prev_level = _ext_logger.level
            if _prev_level > logging.WARNING or _prev_level == logging.NOTSET:
                _ext_logger.setLevel(logging.WARNING)
            try:
                importlib.import_module(mod_name)
                found.append(mod_name)
            except Exception as _e:
                failed.append((mod_name, f"{type(_e).__name__}: {_e}"))
            finally:
                _ext_logger.removeHandler(_h)
                _ext_logger.setLevel(_prev_level)
            # Replay captured warnings into the LogPane.  Format the
            # level + message but skip the noisy module path / line —
            # the operator sees ``[ext warn] stimtest_cwru: …`` which
            # is enough to act on.
            for rec in log_records:
                try:
                    msg = rec.getMessage()
                except Exception:
                    msg = str(rec.msg)
                self.log_pane.log(
                    f"[ext {rec.levelname.lower()}] {mod_name}: {msg}")

        if found:
            self.log_pane.log(
                f"Extensions loaded: {', '.join(sorted(found))}")
        for fname, ferr in failed:
            self.log_pane.log(
                f"Extension '{fname}' failed to load: {ferr}")

        # Re-broadcast the current profile so any pattern panels
        # built before extensions registered now see the full
        # restricted-shape set.  This is the bit that makes the
        # CWRU operator's launch experience seamless: they don't
        # have to log out + back in to see the extension's shapes
        # become AVAILABLE — they only need to log in once to
        # UNLOCK them.
        if found:
            self._set_profile(self._current_profile)

    def _on_admin_catalog(self) -> None:
        """Admin → Manage Custom Catalog — add / remove combo entries.
        ADMIN profile only (extension profiles do not get catalog
        access)."""
        if not is_admin(self._current_profile):
            return
        dlg = AdminCatalogDialog(
            self,
            catalog=self._admin_catalog,
            current_hash=self._admin_password_hash,
        )
        if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        new_catalog = dlg.catalog()
        new_hash = dlg.new_password_hash()

        # Sync additions and removals to the live Setup tab.
        for key in CATALOG_KEYS:
            old_entries = set(self._admin_catalog.get(key, []))
            new_entries = set(new_catalog.get(key, []))
            # Additions — inject into combo.
            added = new_entries - old_entries
            if added:
                apply_admin_catalog(
                    self.setup_tab,
                    {key: sorted(added)})
            # Removals — pull out of combo.
            removed = old_entries - new_entries
            from .admin import remove_from_admin_catalog
            for name in removed:
                remove_from_admin_catalog(
                    self.setup_tab, self._admin_catalog, key, name)

        self._admin_catalog = new_catalog
        self._admin_password_hash = new_hash
        # Persist immediately so changes survive a crash.
        try:
            save_prefs(self._collect_prefs_payload())
        except Exception:
            pass
        self.statusBar().showMessage("Admin catalog saved.", 4000)

    def _on_help_about(self):
        """Help → About — small modal with version + credits."""
        try:
            from .. import __version__ as installed
        except Exception:
            installed = "unknown"
        QtWidgets.QMessageBox.about(
            self, "About PULSAR",
            f"<b>PULSAR</b><br>"
            f"<span style='color:#666'>Neural-stimulation electrode "
            f"characterization suite</span><br>"
            f"Version: <code>{installed}</code><br><br>"
            f"Plexon PlexStim 2.0 + Tektronix TBS2200 / TBS2204B<br>"
            f"control + analysis pipeline. Companion viewer: "
            f"<b>POLARIS</b>.<br><br>"
            f"Source: <a href='https://github.com/Bortz1234/StimulationTesting'>"
            f"github.com/Bortz1234/StimulationTesting</a>"
        )


class _NoWheelInputs(QtCore.QObject):
    """Application-wide event filter that swallows wheel events on
    QComboBox / QAbstractSpinBox.

    Without this, an inadvertent mouse-wheel scroll while the cursor
    is over a dropdown or spinbox silently changes its value — a
    common UX trap, especially when the user is scrolling through a
    parameter form. The fix is to filter QEvent.Wheel on those widget
    types and refuse to let it through; clicking + holding still works
    via the embedded up/down arrow buttons (which our
    RepeatingDoubleSpinBox subclass auto-repeats).
    """
    def eventFilter(self, obj, ev):
        if ev.type() == QtCore.QEvent.Type.Wheel and isinstance(
                obj, (QtWidgets.QAbstractSpinBox, QtWidgets.QComboBox)):
            ev.ignore()
            return True
        return super().eventFilter(obj, ev)


def _wrap_tooltips_for_wrapping(root: QtWidgets.QWidget) -> None:
    """Walk the widget tree and re-render every plain-text tooltip
    as HTML so Qt's rich-text auto-wrapping kicks in.

    Without this pass, long tooltip strings render as a single line
    that can run nearly the full width of the screen — Qt only
    auto-wraps when the tooltip text is HTML-flagged. We wrap each
    existing tooltip in ``<qt><div style='width: 320px'>…</div></qt>``
    which (a) flips Qt into rich-text mode and (b) constrains the
    width so the wrapped lines stay readable.

    Called once after the main window finishes constructing, so every
    static tooltip set during ``__init__`` of every panel gets the
    treatment in a single pass.
    """
    targets = [root] + list(root.findChildren(QtWidgets.QWidget))
    for w in targets:
        try:
            tt = w.toolTip()
        except RuntimeError:
            continue
        if not tt:
            continue
        s = tt.lstrip()
        # Already-HTML tooltips are left alone — they may have been
        # hand-styled with explicit widths or markup.
        if s.startswith("<"):
            continue
        # Escape any literal ``<``/``>``/``&`` so the wrapped HTML
        # renders the original text faithfully.
        from html import escape as _html_escape
        wrapped = (f"<qt><div style='width: 320px'>"
                   f"{_html_escape(tt)}</div></qt>")
        try:
            w.setToolTip(wrapped)
        except RuntimeError:
            continue


def _single_instance_server_name() -> str:
    """Per-user QLocalServer name for the single-instance guard.

    Keyed on the login name so two different users on the same box
    (rare for a bench rig, but possible via fast-user-switching) don't
    block each other, while a second launch by the SAME user is caught.
    """
    try:
        import getpass
        user = getpass.getuser()
    except Exception:
        user = "user"
    safe = "".join(c for c in user if c.isalnum()) or "user"
    return f"PULSAR-stimtest-single-instance-{safe}"


def _acquire_single_instance(server_name: Optional[str] = None):
    """Single-instance guard.  The PlexStim 2.0 allows only ONE client
    (exclusive USB lock; the DLL is single-producer), so a second PULSAR
    window can't Initialize the stimulator ("No Plexon Stimulator is
    detected") and two PULSAR processes touching the DLL risk a heap-
    corruption race.  So at most one PULSAR window per user.

    Returns ``(status, server)``:
      * ``("primary", QLocalServer)`` — this is the first/only instance;
        keep the server alive for the process lifetime and listen on it.
      * ``("secondary", None)``       — another PULSAR is already running;
        it has been pinged to raise its window, so the caller must exit
        WITHOUT creating a second window.
      * ``("disabled", None)``        — the guard is bypassed
        (``PULSAR_ALLOW_MULTIPLE`` set, e.g. dev/test) or QtNetwork is
        unavailable; proceed normally with no server.

    Requires a live ``QApplication`` (QLocalSocket/Server need the Qt
    event machinery), so call it AFTER the app is constructed.
    """
    if os.environ.get("PULSAR_ALLOW_MULTIPLE"):
        return "disabled", None
    try:
        from PyQt6 import QtNetwork
    except Exception:
        return "disabled", None
    name = server_name or _single_instance_server_name()
    # Probe: can we reach an already-running instance's server?
    sock = QtNetwork.QLocalSocket()
    sock.connectToServer(name)
    if sock.waitForConnected(300):
        try:
            sock.write(b"raise")
            sock.flush()
            sock.waitForBytesWritten(300)
        except Exception:
            pass
        sock.disconnectFromServer()
        return "secondary", None
    sock.abort()
    # No live instance.  Clear any stale socket left by a crashed
    # instance, then claim the name.
    QtNetwork.QLocalServer.removeServer(name)
    server = QtNetwork.QLocalServer()
    if not server.listen(name):
        # Couldn't listen (unusual) — fail OPEN so launch still works.
        return "disabled", None
    return "primary", server


def _bring_to_front(win) -> None:
    """Un-minimise, show, and try to steal foreground for ``win`` (used
    when a second launch pings the running instance)."""
    try:
        if win.isMinimized():
            win.showNormal()
        else:
            win.show()
        win.raise_()
        win.activateWindow()
    except Exception:
        pass


def _close_startup_splash() -> None:
    """Dismiss the PyInstaller boot splash once the main window is up.

    ``pyi_splash`` is injected ONLY into the frozen app when a ``Splash()``
    was bundled (see installer/StimulationTesting.spec).  In a normal
    ``python run_gui.py`` run the import fails — swallowed, so this is a
    no-op outside the frozen build.
    """
    try:
        import pyi_splash  # type: ignore  # present only in the frozen app
        pyi_splash.close()
    except Exception:
        pass


def launch(simulate: bool = False, save_dir: Optional[str] = None,
           skip_prereq_check: bool = False) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    # Block accidental wheel-scroll edits on combos / spinboxes.
    app._no_wheel_filter = _NoWheelInputs(app)
    app.installEventFilter(app._no_wheel_filter)
    # ---- Single-instance guard (before any dialog / window) ----------
    # If a PULSAR is already running for this user, raise its window and
    # bow out — don't open a duplicate that would fight over the
    # stimulator's exclusive USB lock (operator: "prevent PULSAR from
    # opening another window").  Set PULSAR_ALLOW_MULTIPLE=1 to bypass.
    _si_status, _si_server = _acquire_single_instance()
    if _si_status == "secondary":
        try:
            sys.stderr.write(
                "PULSAR is already running — focusing the existing "
                "window instead of opening a second one.\n")
            sys.stderr.flush()
        except Exception:
            pass
        return 0
    if not skip_prereq_check and not simulate:
        # On real-hardware launches, give the user a heads-up if the
        # PlexStim SDK isn't installed. We do this *before* showing the
        # main window so the dialog can't get buried behind it.
        _check_plexstim_prereq(app)
    # Apply the saved colour theme (View → Theme: system / light / dark) BEFORE
    # building the window so the first paint is already the right palette — a
    # palette applied AFTER the widget tree is built leaves cached widget
    # palettes stale (dark-mode-still-looks-light).  MainWindow._apply_saved_theme
    # re-applies + wires the menu radio + OS-scheme auto-follow.
    try:
        from .theme import apply_theme
        from .prefs import load_prefs
        apply_theme(app, ((load_prefs() or {}).get("theme") or {}).get("mode",
                                                                       "system"),
                    repolish=False)      # before the window — nothing to repaint
    except Exception:
        pass
    # Pass the caller's ``simulate`` value through verbatim. The
    # earlier form ``simulate or True`` always collapsed to ``True``
    # (the ``or`` short-circuits on the truthy literal), so the
    # "Use simulator" checkbox started checked regardless of the
    # CLI / API value — meaning real-hardware launches silently
    # came up in simulator mode until the user manually unticked.
    win = MainWindow(simulate_default=simulate, save_dir=save_dir)
    win.show()
    _close_startup_splash()   # dismiss the PyInstaller boot splash (frozen app)
    # Force every static tooltip to render as wrapped HTML rather
    # than a single screen-spanning line. Run AFTER ``win.show()`` so
    # the entire widget tree is realized.
    _wrap_tooltips_for_wrapping(win)
    # Make every QLabel's text selectable so the user can copy
    # status / readout / hint text by mouse drag → Ctrl+C. Same
    # post-show pattern as the tooltip pass above.
    _make_labels_selectable(win)
    # USB hot-plug → re-probe the bus so the detection text in the
    # connection panel reflects what's actually plugged in. Only
    # registers a filter on Windows; the call is a no-op elsewhere.
    try:
        from .usb_hotplug import install_usb_hotplug_filter
        install_usb_hotplug_filter(
            app, lambda _arrived: win.conn.refresh_hardware_detection())
    except Exception:
        # Hot-plug refresh is a nicety; don't fail launch if the
        # filter can't install (e.g. unusual Qt build, sandboxing).
        pass
    # Now that the window exists, wire the single-instance server so a
    # SECOND launch ping (someone double-clicks PULSAR again) raises THIS
    # window instead of opening a duplicate.  Keep the server pinned on
    # the app so it lives for the whole process.
    if _si_server is not None:
        app._single_instance_server = _si_server

        def _on_second_launch():
            try:
                conn = _si_server.nextPendingConnection()
                if conn is not None:
                    conn.disconnectFromServer()
            except Exception:
                pass
            _bring_to_front(win)

        try:
            _si_server.newConnection.connect(_on_second_launch)
        except Exception:
            pass
    return app.exec()


def _make_labels_selectable(root: QtWidgets.QWidget) -> None:
    """Flip every ``QLabel`` in the tree to selectable + copyable text.

    By default ``QLabel`` is non-interactive — the user can't mouse-
    drag to select its text or hit ``Ctrl+C`` to copy. Setting the
    text-interaction flags to ``TextSelectableByMouse |
    TextSelectableByKeyboard`` lets the user grab any displayed
    string (potential readouts, summary lines, computed metrics,
    error / status text) without the developer having to opt each
    label in individually. Hyperlinks stay clickable via
    ``LinksAccessibleByMouse``.
    """
    flags = (QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
             | QtCore.Qt.TextInteractionFlag.TextSelectableByKeyboard
             | QtCore.Qt.TextInteractionFlag.LinksAccessibleByMouse)
    targets = [root] + list(root.findChildren(QtWidgets.QLabel))
    for w in targets:
        if not isinstance(w, QtWidgets.QLabel):
            continue
        try:
            w.setTextInteractionFlags(flags)
        except RuntimeError:
            continue


def _check_plexstim_prereq(app: "QtWidgets.QApplication") -> None:
    """Inspect the machine for the PlexStim SDK and warn if missing.

    Runs once at startup; uses the detector in
    ``stimtest.hardware.plexstim_detect``. The dialog is informational
    only — even if the SDK is missing, the user can still launch the app
    in simulator mode.
    """
    try:
        from ..hardware.plexstim_detect import (
            detect_plexstim, PLEXSTIM_INSTALLER_URL,
        )
    except Exception:
        return  # never block the GUI on a detection-side bug

    status = detect_plexstim()
    if status.installed and status.dll_loadable:
        return  # everything fine; no dialog

    box = QtWidgets.QMessageBox()
    box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
    box.setWindowTitle("Stim-2 application check")
    # Render the body as RichText so the installer link is a real
    # clickable hyperlink rather than a wall of plain URL text. The
    # message box honours ``setTextInteractionFlags`` for both the
    # primary and informative texts.
    box.setTextFormat(QtCore.Qt.TextFormat.RichText)
    box.setTextInteractionFlags(
        QtCore.Qt.TextInteractionFlag.TextBrowserInteraction)
    if status.installed and not status.dll_loadable:
        # Found in registry but DLL won't load — usually missing VC++ runtime.
        box.setText("PlexStim SDK is registered but the DLL failed to load.")
        box.setInformativeText(
            "This usually means the Microsoft Visual C++ Redistributable "
            "is missing. You can still run the application in simulator "
            "mode."
        )
    else:
        box.setText("Stim-2 application was not detected on this machine.")
        body = (
            "Connecting to a real Plexon PlexStim 2.0 stimulator requires "
            "the Stimulator V2 (Stim-2) installer, which also installs "
            "the USB driver.<br><br>"
            f"Download: <a href=\"{PLEXSTIM_INSTALLER_URL}\">"
            f"{PLEXSTIM_INSTALLER_URL}</a><br><br>"
            "You can still use the application in simulator mode."
        )
        box.setInformativeText(body)
    if status.notes:
        box.setDetailedText("\n".join(status.notes))
    box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
    box.exec()


def main() -> int:
    return launch()
