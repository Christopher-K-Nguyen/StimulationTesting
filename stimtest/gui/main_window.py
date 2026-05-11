"""PyQt6 main window — connects all the tabs together."""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import DEFAULT_SAVE_DIR
from ..electrode import ElectrodeArray
from .connection_panel import ConnectionPanel
from .experiment_tabs import (
    LongPulsingTab, ProgressiveStressTab, ShortPulsingTab, VoltageTransientTab,
)
from .prefs import (
    load_prefs, load_prefs_from, prefs_dir, prefs_path,
    save_prefs, save_prefs_to,
    PREFS_USER_EXT, PREFS_USER_EXT_LEGACY, PREFS_USER_FILTER,
)
from .results_tab import ResultsTab
from .setup_tab import SetupTab
from .widgets import LogPane


# Section keys used inside the prefs JSON file. Keep these stable across
# releases — renaming forfeits the user's last-saved values for that tab.
PREF_KEY_SETUP = "setup"
PREF_KEY_VT = "vt"
PREF_KEY_SP = "sp"
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
        self.setWindowTitle("PULSAR — Plexon PlexStim + Tektronix scope")
        self.resize(1500, 950)

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
        self.lp_tab = LongPulsingTab(array)
        self.ps_tab = ProgressiveStressTab(array)
        self.res_tab = ResultsTab(self.save_dir)

        # ``_current_exp_code`` must exist before the tabs are added —
        # adding a tab to an empty QTabWidget fires ``currentChanged``,
        # and our handler reads ``self._current_exp_code``. Set it
        # here, then point the per-experiment dict at it later once
        # the experiment widgets exist.
        self._current_exp_code: Optional[str] = None
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
        self.tabs.currentChanged.connect(
            lambda *_: self._refresh_button_row_placement())
        self.tabs.addTab(self.setup_tab, "Setup")
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
            "LP": (self.lp_tab, "Long-Term Pulsing"),
            "PS": (self.ps_tab, "Progressive Stress"),
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
        self._act_calibrate = run_menu.addAction("Cali&brate…")
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
        # Hidden in this build — the calibration dialog itself works
        # (the data-access bug that left it staring at None handles
        # has been fixed, see :meth:`_on_run_calibrate`), but the
        # broader sweep-flow / runner-side integration isn't ready
        # for end users yet. Flip this back to True once the wizard
        # is GUI-complete; the enable/disable logic in
        # ``_on_connected`` / ``_on_disconnected`` is preserved
        # above so the hardware gate Just Works at that point.
        self._act_calibrate.setVisible(False)
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
        act_last_cal = help_menu.addAction("&Last calibration…")
        act_last_cal.triggered.connect(self._on_help_last_calibration)
        help_menu.addSeparator()
        act_about = help_menu.addAction("&About…")
        act_about.triggered.connect(self._on_help_about)

        # Status bar
        self.statusBar().showMessage(
            "Ready. Use the Connection panel in the Setup tab to attach "
            "the stimulator and oscilloscope (or run in simulator mode).")

        # Wiring
        self.conn.connected.connect(self._on_connected)
        self.conn.disconnected.connect(self._on_disconnected)
        self.conn.log.connect(self.statusBar().showMessage)
        # Scope up/down → setup tab seeds default mapping or blanks it.
        self.conn.scopeConnected.connect(self._on_scope_connected)
        self.setup_tab.arrayChanged.connect(self._on_array_changed)
        self.setup_tab.potentialLimitsChanged.connect(self._on_limits_changed)
        self.setup_tab.autoExportXlsxChanged.connect(self._on_auto_export_xlsx_changed)
        self.setup_tab.autoSavePlotsChanged.connect(self._on_auto_save_plots_changed)
        self.setup_tab.sessionFilenameChanged.connect(self._on_log_filename_changed)
        self.setup_tab.emailNotificationsChanged.connect(
            self._on_email_notifications_changed)
        self.setup_tab.userIdentityChanged.connect(
            self._on_user_identity_changed)
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
        # VT hides Fixed charge density mode when all electrodes share
        # an area — same-area arrays make that mode redundant.
        self.setup_tab.sameAreaChanged.connect(self.vt_tab.set_same_area)
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
            self.setup_tab.current_auto_save_plots_format())
        # Also save prefs whenever the user clicks Start on any experiment
        # tab — that snapshot is what they actually want preserved if the
        # machine crashes mid-run. closeEvent (below) covers normal quit.
        for tab in self._experiment_tabs():
            tab.start_btn.clicked.connect(self._save_prefs_from_tabs)
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
        return [self.vt_tab, self.sp_tab, self.lp_tab, self.ps_tab]

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
        self.statusBar().showMessage(f"Connected to {scope.info.make} {scope.info.model}.")
        # Both hardware halves are up — unlock Run → Calibrate so the
        # user can launch the wizard.
        self._act_calibrate.setEnabled(True)
        self._act_calibrate.setToolTip(
            "Open the PlexStim test-board calibration wizard.")

    def _on_disconnected(self):
        for tab in self._experiment_tabs():
            tab.clear_hardware()
        self.statusBar().showMessage("Disconnected.")
        # One (or both) of the hardware halves dropped — re-lock
        # Run → Calibrate. The wizard needs both stim and scope live
        # to drive each channel + capture V_mon/I_mon.
        self._act_calibrate.setEnabled(False)
        self._act_calibrate.setToolTip(
            "Initialize the PlexStim and connect the oscilloscope "
            "first (Setup tab → Connection panel).")

    def _on_array_changed(self, array):
        for tab in self._experiment_tabs():
            tab.set_array(array)

    def _on_auto_export_xlsx_changed(self, on: bool):
        """Setup-tab toggle changed — broadcast to every experiment tab.
        Each tab caches the flag and applies it when constructing the
        next RunnerWorker, so a toggle mid-session affects subsequent
        runs without restart.
        """
        for tab in self._experiment_tabs():
            tab.set_auto_export_xlsx(on)

    def _on_auto_save_plots_changed(self, on: bool, fmt: str):
        """Setup-tab plots-auto-save toggle (or format combo) changed —
        broadcast to every experiment tab so the next ``RunnerWorker``
        picks up the new (on, fmt) pair."""
        for tab in self._experiment_tabs():
            tab.set_auto_save_plots(on, fmt)

    def _on_email_notifications_changed(self, on: bool):
        """Same forwarding shape as auto-export — every experiment tab
        caches the flag and applies it on the next RunnerWorker.
        """
        for tab in self._experiment_tabs():
            tab.set_email_notifications(on)

    def _on_user_identity_changed(self, name: str, email: str):
        """Forward the recipient name + email from the Setup tab into
        every experiment tab so the next run picks them up for the
        completion / failure email."""
        for tab in self._experiment_tabs():
            tab.set_user_identity(name, email)

    def _on_session_subject_changed(self, subject: str):
        """Forward the raw Session text into every experiment tab so
        the email subject line matches what the user typed."""
        for tab in self._experiment_tabs():
            tab.set_session_subject(subject)

    def _on_environment_changed(self, short_code: str, custom_text: str):
        """Forward the Setup-tab Environment selection to every
        experiment tab so the pre-run damage-screen modal picks up
        the right posture (info / warn / alert) for the next Start.
        """
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
        for tab in self._experiment_tabs():
            tab.set_potential_limits(cathodic_v, anodic_v, tolerance_v)

    def _on_aliases_changed(self, aliases):
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
        else:
            self.setup_tab.clear_scope_mapping()
            # Reset the acq widget to its sane no-scope defaults.
            self.setup_tab.apply_scope_capabilities(None)

    def _on_acq_changed(self, mode: str, n_avg: int):
        """Forward the Setup-tab acquisition selection to each experiment
        tab so the next ``_start_runner`` applies it to the scope."""
        for tab in self._experiment_tabs():
            tab.set_acquisition(mode, n_avg)

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

    def _on_save_path_changed(self, path: str):
        """Forward a save-path change from the Setup tab to every
        experiment tab so freshly-saved sessions land in the new folder."""
        try:
            p = Path(path).expanduser()
            p.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.statusBar().showMessage(f"Save path not usable: {e}")
            return
        self.save_dir = p
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

    def _on_log_filename_changed(self, filename: str):
        """Notebook / session field edited — repoint the log mirror.

        The save directory stays the same; only the file stem under
        it changes. Past log content under the OLD filename is left
        in place (we just stop writing to it).
        """
        self.log_pane.set_log_file(self.save_dir / filename)

    def _on_experiment_requested(self, code: str):
        """Setup tab's Experiment dropdown changed (or a startup-prefs
        path is seeding the current experiment). Swap the experiment
        tab and Test-parameters tab content WITHOUT changing the
        currently-focused tab — the user is presumably still mid-
        configuration on Setup."""
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
        self._current_exp_code = code
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
        try:
            self.setup_tab.restore_prefs(prefs.get(PREF_KEY_SETUP, {}))
        except Exception as e:
            self.statusBar().showMessage(f"Setup prefs ignored: {e}")
        for key, tab in (
            (PREF_KEY_VT, self.vt_tab), (PREF_KEY_SP, self.sp_tab),
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

    def closeEvent(self, ev):
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
            PREF_KEY_LP: self.lp_tab.current_prefs(),
            PREF_KEY_PS: self.ps_tab.current_prefs(),
            PREF_KEY_RESULTS: res_prefs,
            PREF_KEY_VIEW: self._collect_view_prefs(),
        }

    def _apply_prefs_payload(self, payload: dict) -> None:
        """Push a loaded profile back into every tab."""
        try:
            self.setup_tab.restore_prefs(payload.get(PREF_KEY_SETUP, {}))
        except Exception as e:
            self.statusBar().showMessage(f"Setup prefs ignored: {e}")
        for key, tab in (
            (PREF_KEY_VT, self.vt_tab), (PREF_KEY_SP, self.sp_tab),
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

    def _on_run_calibrate(self):
        """Run → Calibrate — open the PlexStim test-board calibration
        wizard. The wizard itself lives in
        :class:`stimtest.gui.calibration.CalibrationDialog` and walks
        the user through the test-board steps.

        The live hardware handles live on :attr:`self.conn` (the
        ConnectionPanel owns them); they're NOT stored on MainWindow.
        An earlier version of this slot read ``getattr(self, "_stim",
        None)`` and ``getattr(self, "_scope", None)``, which always
        returned ``None`` — the dialog opened but its hardware gate
        kept Run-sweep disabled even with both halves connected.
        Source the handles from ``self.conn`` directly.
        """
        try:
            from .calibration import CalibrationDialog
        except ImportError:
            QtWidgets.QMessageBox.information(
                self, "Calibration",
                "Calibration wizard not yet bundled with this build. "
                "Connect the PlexStim test board, run a known-current "
                "sweep on each channel, and compare the I_mon readback "
                "against the expected values. The wizard will automate "
                "this in a future release.")
            return
        dlg = CalibrationDialog(self,
                                stim=getattr(self.conn, "stim", None),
                                scope=getattr(self.conn, "scope", None))
        dlg.exec()

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
        """Help → Last calibration — shows when the per-channel
        PlexStim test-board calibration was last recorded. Reads
        the saved-at timestamp embedded in
        ``calibration.json`` (falling back to the file's mtime
        if the timestamp field is missing), or tells the user
        how to run one when the file doesn't exist."""
        from .calibration import calibration_path, last_calibration_datetime
        ts = last_calibration_datetime()
        path = calibration_path()
        if ts is None:
            body = (
                "<b>No calibration on record.</b><br><br>"
                "The per-channel PlexStim test-board calibration "
                "file does not exist yet at:<br>"
                f"<code>{path}</code><br><br>"
                "Plug the channel array into the PlexStim test "
                "board and open <b>Run → Calibrate…</b> to run the "
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
                f"<b>Last calibration recorded:</b><br>"
                f"<code>{ts.strftime('%Y-%m-%d %H:%M:%S')}</code> "
                f"({age_phrase})<br><br>"
                f"File: <code>{path}</code><br><br>"
                f"Re-run via <b>Run → Calibrate…</b> when the "
                f"PlexStim has been opened (firmware reset), the "
                f"channel array has been swapped, or your lab's "
                f"calibration interval has elapsed."
            )
        QtWidgets.QMessageBox.information(
            self, "Last calibration", body)

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


def launch(simulate: bool = False, save_dir: Optional[str] = None,
           skip_prereq_check: bool = False) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    # Block accidental wheel-scroll edits on combos / spinboxes.
    app._no_wheel_filter = _NoWheelInputs(app)
    app.installEventFilter(app._no_wheel_filter)
    if not skip_prereq_check and not simulate:
        # On real-hardware launches, give the user a heads-up if the
        # PlexStim SDK isn't installed. We do this *before* showing the
        # main window so the dialog can't get buried behind it.
        _check_plexstim_prereq(app)
    # Pass the caller's ``simulate`` value through verbatim. The
    # earlier form ``simulate or True`` always collapsed to ``True``
    # (the ``or`` short-circuits on the truthy literal), so the
    # "Use simulator" checkbox started checked regardless of the
    # CLI / API value — meaning real-hardware launches silently
    # came up in simulator mode until the user manually unticked.
    win = MainWindow(simulate_default=simulate, save_dir=save_dir)
    win.show()
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
