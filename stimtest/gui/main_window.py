"""PyQt6 main window — connects all the tabs together."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

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


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, simulate_default: bool = True, save_dir: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("StimulationTesting — Plexon PlexStim + Tektronix scope")
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

        self.tabs = QtWidgets.QTabWidget()
        self.tabs.addTab(self.setup_tab, "Setup")
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
        self._current_exp_code: Optional[str] = None

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
        file_menu.addSeparator()
        act_quit = file_menu.addAction("&Quit")
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)

        # Status bar
        self.statusBar().showMessage("Ready. Click Connect to attach to hardware (or simulator).")

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
        self.setup_tab.aliasesChanged.connect(self._on_aliases_changed)
        self.setup_tab.experimentRequested.connect(self._on_experiment_requested)
        self.setup_tab.savePathChanged.connect(self._on_save_path_changed)
        # Acquisition (mode + n_avg) flows from the Setup tab to every
        # experiment tab so the runner-side setup applies what the
        # user picked.
        self.setup_tab.acquisitionChanged.connect(self._on_acq_changed)
        # VT hides Fixed charge density mode when all electrodes share
        # an area — same-area arrays make that mode redundant.
        self.setup_tab.sameAreaChanged.connect(self.vt_tab.set_same_area)

        # Apply defaults
        self._on_array_changed(self.setup_tab.current_array())
        for tab in self._experiment_tabs():
            tab.set_save_dir(self.save_dir)

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

    def _experiment_tabs(self):
        return [self.vt_tab, self.sp_tab, self.lp_tab, self.ps_tab]

    def _on_connected(self, stim, scope):
        for tab in self._experiment_tabs():
            tab.set_hardware(stim, scope)
        self.statusBar().showMessage(f"Connected to {scope.info.make} {scope.info.model}.")

    def _on_disconnected(self):
        for tab in self._experiment_tabs():
            tab.clear_hardware()
        self.statusBar().showMessage("Disconnected.")

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
        """
        self.setup_tab.setEnabled(not running)
        self.res_tab.setEnabled(not running)

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
        """User clicked 'Open this experiment tab' on the Setup tab."""
        self._show_experiment(code)

    def _show_experiment(self, code: str):
        """Insert / focus the experiment tab for ``code``, hiding any other
        experiment tab that's currently up.

        Tabs are removed via ``QTabWidget.removeTab`` rather than destroyed,
        so each tab keeps its widget state (selected channels, parameter
        values, captured data) for when the user comes back.
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
        self.tabs.setCurrentWidget(target_tab)
        self._current_exp_code = code

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
        out["main_split_sizes"] = self._main_split.sizes()
        out["setup"] = self.setup_tab.view_state()
        # Per-experiment-tab splitter state. Stored under the tab's
        # experiment code so adding a new tab doesn't shift the others'
        # view state around.
        out["tabs"] = {
            tab.experiment_type(): tab.view_state()
            for tab in self._experiment_tabs()
        }
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
                self._main_split.setSizes([int(s) for s in sizes])
            except (TypeError, ValueError):
                pass
        if isinstance(view_prefs.get("setup"), dict):
            self.setup_tab.restore_view_state(view_prefs["setup"])
        tabs_view = view_prefs.get("tabs") or {}
        if isinstance(tabs_view, dict):
            for tab in self._experiment_tabs():
                code = tab.experiment_type()
                state = tabs_view.get(code)
                if isinstance(state, dict):
                    tab.restore_view_state(state)

    def closeEvent(self, ev):
        self._save_prefs_from_tabs()
        super().closeEvent(ev)

    # ------------------------------------------------------------- save/load
    def _collect_prefs_payload(self) -> dict:
        """Snapshot every tab into a single prefs dict.

        Same shape as the auto-save payload — sharing the structure
        means a saved profile and the auto-prefs file are
        interchangeable: the user can copy a profile over the default
        prefs file and the next launch will pick it up.
        """
        return {
            PREF_KEY_SETUP: self.setup_tab.current_prefs(),
            PREF_KEY_VT: self.vt_tab.current_prefs(),
            PREF_KEY_SP: self.sp_tab.current_prefs(),
            PREF_KEY_LP: self.lp_tab.current_prefs(),
            PREF_KEY_PS: self.ps_tab.current_prefs(),
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
        # View state — splitter / column / window sizes — comes along
        # for the ride. A profile saved on a different monitor still
        # restores cleanly because Qt clamps the geometry to the
        # current screen.
        if isinstance(payload.get(PREF_KEY_VIEW), dict):
            self._restore_view_prefs(payload[PREF_KEY_VIEW])

    def _on_save_settings(self):
        """File → Save settings… — write current state to a user-picked file."""
        # Default to the prefs directory so the user can find their
        # profiles next to the auto-save file.
        default_dir = str(prefs_dir())
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save settings",
            f"{default_dir}/profile.json",
            "Settings (*.json);;All files (*)",
        )
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
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
            "Settings (*.json);;All files (*)",
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
    win = MainWindow(simulate_default=simulate or True, save_dir=save_dir)
    win.show()
    return app.exec()


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
    box.setWindowTitle("PlexStim SDK check")
    if status.installed and not status.dll_loadable:
        # Found in registry but DLL won't load — usually missing VC++ runtime.
        box.setText("PlexStim SDK is registered but the DLL failed to load.")
        box.setInformativeText(
            "This usually means the Microsoft Visual C++ Redistributable "
            "is missing. You can still run the application in simulator "
            "mode."
        )
    else:
        box.setText("PlexStim SDK was not detected on this machine.")
        body = (
            "Connecting to a real Plexon PlexStim 2.0 stimulator requires "
            "the SDK installer (which also installs the USB driver).\n\n"
            f"Download from:\n  {PLEXSTIM_INSTALLER_URL}\n\n"
            "You can still use the application in simulator mode."
        )
        box.setInformativeText(body)
    if status.notes:
        box.setDetailedText("\n".join(status.notes))
    box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
    box.exec()


def main() -> int:
    return launch()
