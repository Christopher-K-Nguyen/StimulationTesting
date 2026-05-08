"""Gamry Echem Analyst-like session viewer.

Standalone window for visually inspecting saved sessions. Layout:

    ┌──────────────────────────┬────────────────────────────────────┐
    │  Sessions / Runs / Caps  │  Plot canvas (matplotlib)          │
    │  (tree on the left)      │  • Time / V / I / Active / Return │
    │                          │                                    │
    │                          ├────────────────────────────────────┤
    │                          │  Parameter / metric inspector      │
    └──────────────────────────┴────────────────────────────────────┘

* **File → Open .npz**         : load any saved session
* **File → Open folder…**      : index a folder of sessions in the tree
* **Export → This plot…**      : save current capture to .tif/.png/.pdf
* **Export → All plots…**      : write a per-channel folder of .tif files
* **Export → Summary plots**   : Q_inj vs I_stim and V_d vs Q_inj overlays

Selecting a Run shows its summary plot (Q_inj vs amplitude). Selecting a
Capture shows the per-capture trace plot. Selecting a Session shows a
metadata page.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets
import numpy as np

import matplotlib
matplotlib.use("QtAgg")          # let Qt drive the canvas in this window
# Submodules used by ChannelMapPanel — matplotlib does NOT auto-import
# these, so explicit imports here keep the panel from AttributeError-ing
# the first time the user clicks the Channel Map tab.
import matplotlib.cm        # noqa: F401  (used as matplotlib.cm.ScalarMappable)
import matplotlib.colors    # noqa: F401  (used as matplotlib.colors.Normalize)
import matplotlib.patches   # noqa: F401  (used as matplotlib.patches.Circle)
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg as FigureCanvas,
    NavigationToolbar2QT as NavigationToolbar,
)
from matplotlib.figure import Figure

from ..persistence import load_session_meta, load_session_npz
from ..plotting import (
    plot_capture, plot_overlay, plot_qinj_vs_amplitude, plot_vd_vs_qinj,
    export_capture_plot, export_session_plots, export_session_summary_plots,
    SCREEN_DPI, WAVE_TYPES, _figsize_in,
)
from ..session import Capture, ChannelRun, Session


# ---------------------------------------------------------------------------
# Tree item kinds — use Qt user roles so we can recover what was clicked
# ---------------------------------------------------------------------------
ROLE_KIND = QtCore.Qt.ItemDataRole.UserRole + 1
ROLE_PATH = QtCore.Qt.ItemDataRole.UserRole + 2
ROLE_RUN = QtCore.Qt.ItemDataRole.UserRole + 3
ROLE_CAP = QtCore.Qt.ItemDataRole.UserRole + 4
KIND_FOLDER = "folder"
KIND_SESSION = "session"
KIND_RUN = "run"
KIND_CAPTURE = "capture"


class ChannelTraceToggleBar(QtWidgets.QWidget):
    """Two rows of checkboxes: channels on top, waveform types below.

    Used by :class:`ViewerPanel` to drive the multi-channel overlay
    plot. Emits :pyattr:`selectionChanged` whenever any checkbox flips
    so the panel can re-render. ``set_available_channels`` rebuilds
    the channel-row checkboxes when a new session loads, preserving
    the selection state of channels that still exist.
    """

    selectionChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._channel_checks: Dict[int, QtWidgets.QCheckBox] = {}
        self._wave_checks: Dict[str, QtWidgets.QCheckBox] = {}

        # Channel row — populated dynamically by ``set_available_channels``.
        self.channel_row = QtWidgets.QHBoxLayout()
        self.channel_row.setContentsMargins(0, 0, 0, 0)
        self.channel_row.setSpacing(4)
        self.channel_row.addWidget(QtWidgets.QLabel("Channels:"))
        self._channel_all = QtWidgets.QPushButton("All")
        self._channel_none = QtWidgets.QPushButton("None")
        self._channel_all.setFixedWidth(48)
        self._channel_none.setFixedWidth(54)
        self._channel_all.clicked.connect(lambda: self._set_all_channels(True))
        self._channel_none.clicked.connect(lambda: self._set_all_channels(False))
        self.channel_row.addWidget(self._channel_all)
        self.channel_row.addWidget(self._channel_none)
        # Empty stretch in the channel row — checkbox widgets are
        # inserted between the All/None buttons and this stretch by
        # ``set_available_channels``.
        self._channel_row_stretch_index = self.channel_row.count()
        self.channel_row.addStretch(1)

        # Waveform-type row — fixed set, always visible.
        self.wave_row = QtWidgets.QHBoxLayout()
        self.wave_row.setContentsMargins(0, 0, 0, 0)
        self.wave_row.setSpacing(8)
        self.wave_row.addWidget(QtWidgets.QLabel("Waveforms:"))
        for wname in WAVE_TYPES:
            cb = QtWidgets.QCheckBox(wname)
            cb.setChecked(True)
            cb.toggled.connect(self._emit)
            self._wave_checks[wname] = cb
            self.wave_row.addWidget(cb)
        self.wave_row.addStretch(1)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 0)
        outer.setSpacing(2)
        outer.addLayout(self.channel_row)
        outer.addLayout(self.wave_row)

    # ----------------------------------------------------------- API
    def set_available_entries(self, entries) -> None:
        """Rebuild the toggle row from ``(key, label)`` pairs.

        ``entries`` may be a mapping ``{key: label}`` or any iterable
        of ``(key, label)`` pairs. Keys are the values returned by
        :meth:`enabled_keys` and used as the lookup into the overlay
        capture map; labels are the strings shown on each checkbox.
        Existing entries keep their checked state on rebuild; new
        entries default to checked. Entries that have disappeared
        get their checkboxes removed. Emits
        :pyattr:`selectionChanged` once the rebuild settles.

        Multipolar data: each Run becomes one entry with key =
        :meth:`Configuration.display_name` ("CH05", "CH05 v 06,09",
        ...) so two configurations sharing the same active channel
        no longer collide.
        """
        if isinstance(entries, dict):
            pairs = list(entries.items())
        else:
            pairs = list(entries)
        # Stable order: sort lexicographically by label so the row
        # reads predictably across rebuilds.
        pairs.sort(key=lambda kv: str(kv[1]))
        wanted_keys = {k for k, _ in pairs}
        # Remove keys that have disappeared.
        for k in list(self._channel_checks):
            if k not in wanted_keys:
                cb = self._channel_checks.pop(k)
                cb.setParent(None)
                cb.deleteLater()
        # Add any new entries, preserving previous checked-state. The
        # last item in self.channel_row is the trailing stretch; we
        # insert just before it so the row stays aligned-left.
        for key, label in pairs:
            if key in self._channel_checks:
                # Update the label in case the caller passed a different
                # one for the same key (e.g. capture index keys are
                # stable but the amp annotation might have shifted).
                self._channel_checks[key].setText(str(label))
                continue
            cb = QtWidgets.QCheckBox(str(label))
            cb.setChecked(True)
            cb.toggled.connect(self._emit)
            self._channel_checks[key] = cb
            self.channel_row.insertWidget(
                self.channel_row.count() - 1, cb)
        self._emit()

    # Back-compat shim — the older ``set_available_channels(ints)``
    # call is still used by callers that work with raw int channel
    # numbers (e.g. simple monopolar overlays). Wraps the new API.
    def set_available_channels(self, channels) -> None:
        self.set_available_entries(
            [(int(c), f"CH{int(c):02d}") for c in channels])

    def enabled_keys(self) -> set:
        """Set of keys (whatever type the caller used) currently ticked."""
        return {k for k, cb in self._channel_checks.items() if cb.isChecked()}

    # Back-compat alias for the int-keyed callers.
    def enabled_channels(self) -> set:
        return self.enabled_keys()

    def enabled_waves(self) -> set:
        return {w for w, cb in self._wave_checks.items() if cb.isChecked()}

    # ----------------------------------------------------------- internal
    def _set_all_channels(self, on: bool) -> None:
        for cb in self._channel_checks.values():
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
        self._emit()

    def _emit(self, *_):
        self.selectionChanged.emit()


class ViewerPanel(QtWidgets.QWidget):
    """Embeddable viewer body.

    All the actual viewing UI — tree, plot canvas, channel map, info
    tables, and the slot methods that drive them — lives here so it
    can be embedded in either the standalone :class:`ViewerWindow`
    or the main GUI's Results tab. The panel doesn't own a menu bar
    or status bar (those are QMainWindow features); the host widget
    is responsible for surfacing menu actions and status messages.

    Use the ``status_message`` signal to receive status text the
    panel would otherwise put in a status bar — wire it to a
    QStatusBar.showMessage or a QLabel.
    """

    #: Plain-text status messages (replaces direct QStatusBar access).
    #: Wire this to whatever the host wants to display the strings in.
    status_message = QtCore.pyqtSignal(str)

    def __init__(self, initial_path: Optional[Path] = None,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._sessions: Dict[str, Session] = {}   # path-string -> Session

        # ---- Central layout: tree | plot+info ------------------------
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(splitter)

        # Left: tree + open buttons
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)

        btn_row = QtWidgets.QHBoxLayout()
        self.open_file_btn = QtWidgets.QPushButton("Open .npz…")
        self.open_folder_btn = QtWidgets.QPushButton("Open folder…")
        btn_row.addWidget(self.open_file_btn)
        btn_row.addWidget(self.open_folder_btn)
        left_layout.addLayout(btn_row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Session / Run / Capture", "Info"])
        self.tree.setColumnWidth(0, 320)
        left_layout.addWidget(self.tree)
        splitter.addWidget(left)

        # Right: vertical splitter — plot canvas + info panel
        right_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        splitter.addWidget(right_split)

        # Plot canvas + toolbar + channel/waveform toggle bar.
        # The toggle bar sits ABOVE the canvas and lets the user pick
        # which channels and which waveform types (V_mon / I_mon /
        # E_act / E_ret) appear in the multi-channel overlay view.
        # All toggles default to ON when a session loads, so the
        # initial render shows every channel's representative trace.
        self.figure = Figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
        self.canvas = FigureCanvas(self.figure)
        self.toolbar = NavigationToolbar(self.canvas, self)
        self.trace_toggles = ChannelTraceToggleBar(self)
        self.trace_toggles.selectionChanged.connect(self._refresh_overlay)
        plot_panel = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(plot_panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.toolbar)
        pl.addWidget(self.trace_toggles)
        pl.addWidget(self.canvas)

        # Channel-map view — second tab on the right side. Renders the
        # device geometry (one disk per electrode in the array's
        # row/col grid) and optionally colors each disk by a per-channel
        # metric aggregated across that channel's run. The metric combo
        # picks which value to overlay; "None" shows just the geometry
        # with channel numbers.
        self.map_panel = ChannelMapPanel(self)
        # Tab widget so the user can flip between the time-series plot
        # and the channel-map heatmap without losing either's state.
        self.right_tabs = QtWidgets.QTabWidget()
        self.right_tabs.addTab(plot_panel, "Plot")
        self.right_tabs.addTab(self.map_panel, "Channel Map")
        right_split.addWidget(self.right_tabs)

        # Info panel: summary table + parameter table side-by-side
        info_panel = QtWidgets.QWidget()
        ipl = QtWidgets.QHBoxLayout(info_panel)
        ipl.setContentsMargins(4, 4, 4, 4)

        self.metric_table = QtWidgets.QTableWidget(0, 2)
        self.metric_table.setHorizontalHeaderLabels(["Metric", "Value"])
        self.metric_table.horizontalHeader().setStretchLastSection(True)
        self.metric_table.verticalHeader().setVisible(False)
        self.metric_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        self.param_table = QtWidgets.QTableWidget(0, 2)
        self.param_table.setHorizontalHeaderLabels(["Parameter", "Value"])
        self.param_table.horizontalHeader().setStretchLastSection(True)
        self.param_table.verticalHeader().setVisible(False)
        self.param_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        ipl.addWidget(self.metric_table)
        ipl.addWidget(self.param_table)
        right_split.addWidget(info_panel)

        right_split.setStretchFactor(0, 4)
        right_split.setStretchFactor(1, 1)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

        # ---- Wiring --------------------------------------------------
        self.open_file_btn.clicked.connect(self.on_open_file)
        self.open_folder_btn.clicked.connect(self.on_open_folder)
        self.tree.currentItemChanged.connect(self._on_tree_item)

        # Initial status — emitted via the signal so the host can
        # surface it (status bar, label, log, …).
        self.status_message.emit(
            "Open a .npz session or a folder to begin.")

        if initial_path is not None:
            p = Path(initial_path)
            if p.is_dir():
                self.load_folder(p)
            elif p.exists():
                self.load_session_file(p)


class ViewerWindow(QtWidgets.QMainWindow):
    """Standalone viewer window. Wraps :class:`ViewerPanel` with menus
    and a status bar so ``run_viewer.py`` (and the bundled
    ``StimulationTestingViewer.exe``) stay one-window apps.
    """

    def __init__(self, initial_path: Optional[Path] = None,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("StimulationTesting Viewer")
        self.resize(1400, 900)
        self._panel = ViewerPanel(initial_path=initial_path, parent=self)
        self.setCentralWidget(self._panel)
        # Forward panel status messages to the QStatusBar.
        self._panel.status_message.connect(self.statusBar().showMessage)
        self._build_menus()

    # Forward attribute access so existing call sites that do
    # ``window.tree`` / ``window.figure`` / etc. keep working.
    def __getattr__(self, name):
        # __getattr__ is only invoked when the standard lookup fails,
        # so it doesn't break ``self._panel`` access (set in __init__).
        return getattr(self._panel, name)

    # -----------------------------------------------------------------
    # Menus — only on the standalone window. The Results-tab embedding
    # exposes the same actions via its own button row.
    # -----------------------------------------------------------------
    def _build_menus(self) -> None:
        p = self._panel
        m_file = self.menuBar().addMenu("&File")
        a_open = m_file.addAction("Open &.npz…")
        a_open.triggered.connect(p.on_open_file)
        a_folder = m_file.addAction("Open &folder…")
        a_folder.triggered.connect(p.on_open_folder)
        m_file.addSeparator()
        a_quit = m_file.addAction("&Quit")
        a_quit.triggered.connect(self.close)

        m_export = self.menuBar().addMenu("&Export")
        a_this = m_export.addAction("Save &this plot…")
        a_this.triggered.connect(p.on_export_this_plot)
        a_all = m_export.addAction("Save &all per-channel plots…")
        a_all.triggered.connect(p.on_export_all_plots)
        a_summary = m_export.addAction("Save &summary plots…")
        a_summary.triggered.connect(p.on_export_summary_plots)

        m_view = self.menuBar().addMenu("&View")
        a_qinj = m_view.addAction("Show Q_inj vs I_stim")
        a_qinj.triggered.connect(p._show_qinj_overlay)
        a_vd = m_view.addAction("Show V_d vs Q_inj")
        a_vd.triggered.connect(p._show_vd_overlay)

    # -----------------------------------------------------------------
    # File / folder loading
    # -----------------------------------------------------------------
    @QtCore.pyqtSlot()
    def on_open_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open session", "", "Session files (*.npz);;All files (*)")
        if path:
            self.load_session_file(Path(path))

    @QtCore.pyqtSlot()
    def on_open_folder(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Open folder")
        if path:
            self.load_folder(Path(path))

    def load_folder(self, folder: Path) -> None:
        """Index every ``.npz`` file in ``folder`` (one tree branch each)."""
        self.tree.clear()
        self._sessions.clear()
        npzs = sorted(folder.glob("*.npz"))
        if not npzs:
            QtWidgets.QMessageBox.information(
                self, "No sessions",
                f"No .npz files found in {folder}")
            return
        root = QtWidgets.QTreeWidgetItem(self.tree, [folder.name, ""])
        root.setData(0, ROLE_KIND, KIND_FOLDER)
        root.setData(0, ROLE_PATH, str(folder))
        for p in npzs:
            self._add_session_to_tree(p, parent=root)
        root.setExpanded(True)
        self.status_message.emit(f"Indexed {len(npzs)} session(s) in {folder}")

    def load_session_file(self, path: Path) -> None:
        if path.suffix.lower() != ".npz":
            QtWidgets.QMessageBox.warning(
                self, "Unsupported", "Viewer expects a .npz session file.")
            return
        self._add_session_to_tree(path, parent=self.tree.invisibleRootItem(),
                                  expand=True, eager=True)
        self.status_message.emit(f"Loaded {path.name}")

    def _add_session_to_tree(self, path: Path, *, parent,
                             expand: bool = False, eager: bool = False) -> None:
        """Insert a placeholder for the session; expand to load when clicked."""
        item = QtWidgets.QTreeWidgetItem(parent, [path.name, ""])
        item.setData(0, ROLE_KIND, KIND_SESSION)
        item.setData(0, ROLE_PATH, str(path))
        # Add a stub child so the user gets the expand triangle
        QtWidgets.QTreeWidgetItem(item, ["…loading", ""])
        if eager:
            self._load_session_into_tree(item)
        if expand:
            item.setExpanded(True)

    def _load_session_into_tree(self, session_item: QtWidgets.QTreeWidgetItem) -> None:
        """Replace the stub child with one entry per Run+Capture."""
        path = session_item.data(0, ROLE_PATH)
        if path in self._sessions:
            session = self._sessions[path]
        else:
            try:
                session = load_session_npz(Path(path))
            except Exception as e:
                QtWidgets.QMessageBox.critical(
                    self, "Load failed", f"{Path(path).name}\n\n{e}")
                return
            self._sessions[path] = session
        # Clear stub
        session_item.takeChildren()
        # Update the second column with a quick summary
        n_runs = len(session.runs)
        n_caps = session.total_captures
        session_item.setText(1, f"{n_runs} runs, {n_caps} captures")
        for run_idx, run in enumerate(session.runs):
            label = run.configuration.display_name()
            qmax = run.max_q_inj
            qstr = f"max Q_inj = {qmax:.3f} mC/cm²" if np.isfinite(qmax) else ""
            r_item = QtWidgets.QTreeWidgetItem(session_item, [label, qstr])
            r_item.setData(0, ROLE_KIND, KIND_RUN)
            r_item.setData(0, ROLE_PATH, path)
            r_item.setData(0, ROLE_RUN, run_idx)
            for cap_idx, cap in enumerate(run.captures):
                amp = cap.pattern.excitation_phase.amplitude_ua
                qinj = cap.metrics.charge_injection_mc_per_cm2
                cap_label = f"#{cap.index:03d}  {amp:+.1f} µA"
                summary = f"Q_inj = {qinj:.3f} mC/cm²" if np.isfinite(qinj) else ""
                if cap.status.reached_potential_limit:
                    summary += "  [LIMIT]"
                elif cap.status.voltage_compliance:
                    summary += "  [COMPLIANCE]"
                c_item = QtWidgets.QTreeWidgetItem(r_item, [cap_label, summary])
                c_item.setData(0, ROLE_KIND, KIND_CAPTURE)
                c_item.setData(0, ROLE_PATH, path)
                c_item.setData(0, ROLE_RUN, run_idx)
                c_item.setData(0, ROLE_CAP, cap_idx)

    # -----------------------------------------------------------------
    # Tree selection → plot
    # -----------------------------------------------------------------
    @QtCore.pyqtSlot(QtWidgets.QTreeWidgetItem, QtWidgets.QTreeWidgetItem)
    def _on_tree_item(self, current, _previous):
        if current is None:
            return
        kind = current.data(0, ROLE_KIND)
        if kind == KIND_SESSION:
            # Lazy-load the session into the tree if not done
            if current.childCount() == 1 and current.child(0).text(0) == "…loading":
                self._load_session_into_tree(current)
            self._show_session_metadata(current)
        elif kind == KIND_RUN:
            self._show_run_overlay(current)
        elif kind == KIND_CAPTURE:
            self._show_capture(current)
        elif kind == KIND_FOLDER:
            self.figure.clear()
            self.canvas.draw_idle()
            self._set_metric_table([])
            self._set_param_table([("Folder", current.data(0, ROLE_PATH))])
        # Always refresh the channel-map view so it reflects the
        # session that owns the current selection. ``_current_session``
        # walks the tree item up to its session root.
        self.map_panel.set_session(self._current_session())

    # -----------------------------------------------------------------
    # Show a single capture
    # -----------------------------------------------------------------
    def _show_capture(self, item: QtWidgets.QTreeWidgetItem) -> None:
        path = item.data(0, ROLE_PATH)
        run_idx = item.data(0, ROLE_RUN)
        cap_idx = item.data(0, ROLE_CAP)
        session = self._sessions.get(path)
        if session is None:
            return
        run = session.runs[run_idx]
        cap = run.captures[cap_idx]
        plot_capture(cap, run, session, fig=self.figure)
        self.canvas.draw_idle()
        self._set_metric_table(_capture_metric_rows(cap, run))
        self._set_param_table(_session_param_rows(session, run, cap))

    def _show_run_overlay(self, item: QtWidgets.QTreeWidgetItem) -> None:
        """Show all captures of a single run as a waveform overlay.

        Each capture along the ramp becomes a toggle in the channel
        bar (keyed by capture index). The user can isolate any subset
        of the sweep — e.g. compare the first and last steps — with
        the same channel/waveform-type filters as the session view.
        """
        path = item.data(0, ROLE_PATH)
        run_idx = item.data(0, ROLE_RUN)
        session = self._sessions.get(path)
        if session is None:
            return
        run = session.runs[run_idx]
        self._set_overlay_for_run(run, session)
        self._set_metric_table([
            ("Channel", run.configuration.display_name()),
            ("Surface area", f"{run.surface_area_um2:.0f} µm²"),
            ("Captures", str(len(run.captures))),
            ("Max Q_inj", f"{run.max_q_inj:.3f} mC/cm²"),
        ])
        self._set_param_table(_session_param_rows(session, run, None))

    # --------- multi-channel waveform overlay ----------------------
    # Cached most-recent overlay context so the toggle bar can re-render
    # without re-walking the tree. Populated by ``_show_session_metadata``
    # / ``_show_run_overlay`` whenever they switch into overlay mode.
    _overlay_captures_by_channel: Dict[int, "Capture"] = {}
    _overlay_title: str = ""

    def _representative_capture(self, run: "ChannelRun") -> Optional["Capture"]:
        """Pick the capture that best summarises a run.

        Default: the last good capture. Falls back to the last capture
        regardless of status if every capture aborted (so the user still
        sees something rather than an empty plot).
        """
        for cap in reversed(run.captures):
            if cap.status.good and not cap.status.aborted:
                return cap
        return run.captures[-1] if run.captures else None

    def _set_overlay_for_session(self, session: "Session") -> None:
        """Build the per-configuration capture map for an entire session
        and repopulate the toggle bar.

        Keys are the run's ``Configuration.display_name()`` so multipolar
        runs (BP / TP / PBP / PTP / CG / PCG) get distinct entries even
        when they share the same active channel as another run in the
        same session — e.g. CH5-MP and CH5-BP-CH6 land in different
        toggle entries instead of overwriting each other.
        """
        captures: Dict[str, "Capture"] = {}
        labels: Dict[str, str] = {}
        for run in session.runs:
            cap = self._representative_capture(run)
            if cap is None:
                continue
            key = run.configuration.display_name()
            # If two runs (separate sessions or repeated configs)
            # share the same display name, suffix with the run index
            # in the session so neither gets dropped.
            if key in captures:
                key = f"{key} #{session.runs.index(run)}"
            captures[key] = cap
            labels[key] = key
        self._overlay_captures_by_channel = captures
        self._overlay_title = (f"{session.subject or session.notebook} — "
                               f"channel waveform overlay")
        self.trace_toggles.set_available_entries(labels)
        # set_available_entries emits selectionChanged -> triggers
        # _refresh_overlay automatically; nothing else to do here.

    def _set_overlay_for_run(self, run: "ChannelRun",
                             session: "Session") -> None:
        """Single-run overlay: every capture along the ramp on one plot.

        Uses ``cap-{index}-{amp}uA`` as the entry key so the toggle
        bar can switch individual sweep steps on/off independently
        of the session view's configuration-keyed entries. Labels
        embed the excitation amplitude so the user can spot which
        step in the ramp each line belongs to.
        """
        captures: Dict[str, "Capture"] = {}
        labels: Dict[str, str] = {}
        for cap in run.captures:
            try:
                amp = cap.pattern.excitation_phase.amplitude_ua
                amp_label = f"{amp:+.0f} µA"
            except Exception:
                amp_label = ""
            key = f"#{cap.index:03d}"
            labels[key] = (f"{key} {amp_label}".strip()
                           if amp_label else key)
            captures[key] = cap
        self._overlay_captures_by_channel = captures
        self._overlay_title = (
            f"{run.configuration.display_name()} — ramp overlay "
            f"({len(captures)} captures)")
        self.trace_toggles.set_available_entries(labels)

    @QtCore.pyqtSlot()
    def _refresh_overlay(self) -> None:
        """Re-render the overlay plot using the current toggle state."""
        if not self._overlay_captures_by_channel:
            return
        try:
            plot_overlay(
                self._overlay_captures_by_channel,
                fig=self.figure,
                channels_enabled=self.trace_toggles.enabled_keys(),
                waves_enabled=self.trace_toggles.enabled_waves(),
                title=self._overlay_title or "Channel waveform overlay",
            )
        except Exception as e:
            # Surface a clean message in the figure rather than crashing
            # the panel — most likely cause is a stale Capture object.
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"Overlay render failed:\n{e}",
                    ha="center", va="center", color="#a00",
                    transform=ax.transAxes)
            ax.axis("off")
        self.canvas.draw_idle()

    def _show_session_metadata(self, item: QtWidgets.QTreeWidgetItem) -> None:
        path = item.data(0, ROLE_PATH)
        session = self._sessions.get(path)
        if session is None:
            try:
                meta = load_session_meta(Path(path))
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Load failed", str(e))
                return
            self._set_metric_table([
                ("Notebook", str(meta.get("notebook", ""))),
                ("Subject", str(meta.get("subject", ""))),
                ("Created", str(meta.get("created_at", ""))),
                ("Runs", str(len(meta.get("runs", [])))),
            ])
            self._set_param_table([
                ("Experiment", str(meta.get("test", {}).get("experiment", ""))),
                ("Path", str(path)),
            ])
            return
        # Loaded session: default to the multi-channel waveform overlay
        # (one trace per channel, all waveform types on by default).
        # The user can switch to the Q_inj-vs-I_stim summary via the
        # View menu (or the Plot/Channel-Map tabs in the embedded
        # Results-tab variant).
        if session.runs:
            self._set_overlay_for_session(session)
        self._set_metric_table([
            ("Notebook", session.notebook),
            ("Subject", session.subject),
            ("Total runs", str(len(session.runs))),
            ("Total captures", str(session.total_captures)),
            ("Created", session.created_at.isoformat()),
        ])
        self._set_param_table(_session_param_rows(session, None, None))

    # -----------------------------------------------------------------
    # Misc helpers (info-panel rendering)
    # -----------------------------------------------------------------
    def _set_metric_table(self, rows: List[tuple]) -> None:
        self.metric_table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.metric_table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(k)))
            self.metric_table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(v)))
        self.metric_table.resizeColumnToContents(0)

    def _set_param_table(self, rows: List[tuple]) -> None:
        self.param_table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.param_table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(k)))
            self.param_table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(v)))
        self.param_table.resizeColumnToContents(0)

    # -----------------------------------------------------------------
    # Export menu actions
    # -----------------------------------------------------------------
    def _current_session(self) -> Optional[Session]:
        item = self.tree.currentItem()
        if item is None:
            return None
        path = item.data(0, ROLE_PATH)
        if not path:
            return None
        return self._sessions.get(path)

    @QtCore.pyqtSlot()
    def on_export_this_plot(self) -> None:
        item = self.tree.currentItem()
        if item is None or item.data(0, ROLE_KIND) != KIND_CAPTURE:
            QtWidgets.QMessageBox.information(
                self, "No capture", "Select a capture to export.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save plot", "", "TIFF (*.tif);;PNG (*.png);;PDF (*.pdf);;SVG (*.svg)")
        if not path:
            return
        session = self._sessions[item.data(0, ROLE_PATH)]
        run = session.runs[item.data(0, ROLE_RUN)]
        cap = run.captures[item.data(0, ROLE_CAP)]
        export_capture_plot(cap, run, session, Path(path))
        self.status_message.emit(f"Saved {path}")

    @QtCore.pyqtSlot()
    def on_export_all_plots(self) -> None:
        session = self._current_session()
        if session is None:
            QtWidgets.QMessageBox.information(
                self, "No session", "Select a session first.")
            return
        out = QtWidgets.QFileDialog.getExistingDirectory(self, "Output folder")
        if not out:
            return
        paths = export_session_plots(session, Path(out))
        QtWidgets.QMessageBox.information(
            self, "Done", f"Wrote {len(paths)} plot(s) to\n{out}")

    @QtCore.pyqtSlot()
    def on_export_summary_plots(self) -> None:
        session = self._current_session()
        if session is None:
            QtWidgets.QMessageBox.information(
                self, "No session", "Select a session first.")
            return
        out = QtWidgets.QFileDialog.getExistingDirectory(self, "Output folder")
        if not out:
            return
        paths = export_session_summary_plots(session, Path(out))
        QtWidgets.QMessageBox.information(
            self, "Done",
            "Wrote:\n" + "\n".join(str(p) for p in paths))

    # -----------------------------------------------------------------
    # View menu helpers
    # -----------------------------------------------------------------
    @QtCore.pyqtSlot()
    def _show_qinj_overlay(self) -> None:
        s = self._current_session()
        if s is None:
            return
        plot_qinj_vs_amplitude(s, fig=self.figure)
        self.canvas.draw_idle()

    @QtCore.pyqtSlot()
    def _show_vd_overlay(self) -> None:
        s = self._current_session()
        if s is None:
            return
        plot_vd_vs_qinj(s, fig=self.figure)
        self.canvas.draw_idle()


# Transplant the slot methods physically defined under ``ViewerWindow``
# onto ``ViewerPanel`` so the panel can be embedded directly (e.g. in
# the Results tab) without going through a window. Both classes end up
# with identical method tables; the signals/widgets they touch
# (``self.tree``, ``self.figure``, ``self.status_message``, …) live on
# the panel, and the window's ``__getattr__`` forwards bare attribute
# access through to its embedded panel — so a method bound to either
# class resolves the same widgets in both contexts.
for _name in ("on_open_file", "on_open_folder",
              "load_folder", "load_session_file",
              "_add_session_to_tree", "_load_session_into_tree",
              "_on_tree_item",
              "_show_capture", "_show_run_overlay",
              "_show_session_metadata",
              "_set_metric_table", "_set_param_table",
              "_current_session",
              "on_export_this_plot", "on_export_all_plots",
              "on_export_summary_plots",
              "_show_qinj_overlay", "_show_vd_overlay",
              # Overlay-mode helpers added in the multi-channel rewrite
              # — without these, the panel's signal-connect at __init__
              # time fails with AttributeError because the methods
              # were physically defined inside the ``ViewerWindow``
              # class block.
              "_representative_capture",
              "_set_overlay_for_session", "_set_overlay_for_run",
              "_refresh_overlay"):
    setattr(ViewerPanel, _name, getattr(ViewerWindow, _name))
del _name


# ---------------------------------------------------------------------------
# Side-panel info builders
# ---------------------------------------------------------------------------
def _capture_metric_rows(cap: Capture, run: ChannelRun) -> List[tuple]:
    m = cap.metrics
    rows = [
        ("Capture #", cap.index),
        ("Status", _status_text(cap)),
        ("Amplitude", f"{cap.pattern.excitation_phase.amplitude_ua:+.2f} µA"),
        ("Q_ph", f"{m.charge_per_phase_nc:.3f} nC"),
        ("Q_inj", f"{m.charge_injection_mc_per_cm2:.3f} mC/cm²"),
        ("V_d", f"{m.driving_voltage_v:.3f} V"),
        ("C_eff", f"{m.effective_capacitance_nf:.3f} nF"),
        ("C_d", f"{m.driving_capacitance_mf_per_cm2:.3f} mF/cm²"),
    ]
    if m.access_voltage_per_phase_v:
        rows.append(("V_a per phase",
                     ", ".join(f"{v:.3f}" for v in m.access_voltage_per_phase_v)))
    if m.access_resistance_per_phase_kohm:
        rows.append(("R_a per phase (kΩ)",
                     ", ".join(f"{r:.2f}" for r in m.access_resistance_per_phase_kohm)))
    if m.polarization_per_phase_v:
        rows.append(("E_pol active",
                     ", ".join(f"{e:.3f}" for e in m.polarization_per_phase_v)))
    if m.return_polarization_per_phase_v:
        rows.append(("E_pol return",
                     ", ".join(f"{e:.3f}" for e in m.return_polarization_per_phase_v)))
    return rows


def _session_param_rows(session: Session, run: Optional[ChannelRun],
                        cap: Optional[Capture]) -> List[tuple]:
    p = session.test.pattern
    rows: List[tuple] = []
    rows.append(("Notebook", session.notebook))
    rows.append(("Subject", session.subject))
    rows.append(("Experiment", session.test.experiment))
    rows.append(("Pattern",
                 "Triphasic" if p.is_triphasic else
                 ("Monophasic" if p.num_phases == 1 else "Biphasic")))
    rows.append(("Polarity",
                 "Cathodic-first" if p.polarity == -1 else "Anodic-first"))
    rows.append(("Rate", f"{p.rate_hz} Hz"))
    rows.append(("Reference", session.test.reference_electrode_label))
    rows.append(("Counter", session.test.counter_electrode_label))
    for k, ph in enumerate(p.phases, start=1):
        rows.append((f"Phase {k} amp", f"{ph.amplitude_ua:+.2f} µA"))
        rows.append((f"Phase {k} width", f"{ph.width_us:.0f} µs"))
        if ph.delay_after_us > 0:
            label = "Discharge delay" if k == p.num_phases else f"Interphase delay {k}"
            rows.append((label, f"{ph.delay_after_us:.0f} µs"))
    if run is not None:
        rows.append(("Configuration", run.configuration.id))
        rows.append(("Active", run.configuration.active))
        if run.configuration.returns:
            rows.append(("Returns",
                         ", ".join(str(r) for r in run.configuration.returns)))
        rows.append(("Surface area", f"{run.surface_area_um2:.0f} µm²"))
    extras = session.test.extras or {}
    coating = extras.get("coating_props")
    if coating:
        rows.append(("Coating", coating.get("name", "")))
        rows.append(("E_lc", f"{coating.get('cathodic_limit_v', 0):.2f} V"))
        rows.append(("E_la", f"{coating.get('anodic_limit_v', 0):.2f} V"))
    scope = extras.get("oscilloscope_info")
    if scope:
        rows.append(("Scope",
                     f"{scope.get('make', '')} {scope.get('model', '')}"))
    stim = extras.get("stimulator_info")
    if stim:
        rows.append(("Stimulator",
                     stim.get("description", "Plexon PlexStim")))
    return rows


def _status_text(cap: Capture) -> str:
    if cap.status.aborted:
        return "Aborted"
    if cap.status.voltage_compliance:
        return "Voltage compliance"
    if cap.status.reached_potential_limit:
        return "Limit reached"
    if not cap.status.good:
        return "Bad"
    return "OK"


# ---------------------------------------------------------------------------
# Channel-map panel
# ---------------------------------------------------------------------------
# Per-channel metric reductions. Each entry maps a label shown in the
# combo to a function that takes a :class:`ChannelRun` and returns
# either a finite float (the value to color) or ``float('nan')`` (no
# data — render the disk in neutral grey).
def _qinj_max(run):
    vals = [c.metrics.charge_injection_mc_per_cm2 for c in run.captures
            if c.status.good]
    return max(vals) if vals else float("nan")


def _qph_max(run):
    vals = [c.metrics.charge_per_phase_nc for c in run.captures
            if c.status.good]
    return max(vals) if vals else float("nan")


def _vd_max(run):
    vals = [c.metrics.driving_voltage_v for c in run.captures
            if c.status.good and np.isfinite(c.metrics.driving_voltage_v)]
    return max(vals) if vals else float("nan")


def _ra_mean(run):
    vals = []
    for c in run.captures:
        if not c.status.good:
            continue
        for r in (c.metrics.access_resistance_per_phase_kohm or ()):
            if np.isfinite(r):
                vals.append(r)
    return float(np.mean(vals)) if vals else float("nan")


def _amp_max(run):
    vals = [abs(c.pattern.excitation_phase.amplitude_ua) for c in run.captures]
    return max(vals) if vals else float("nan")


# (label, unit, reducer) — order matters because the first ("None")
# is the default.
_METRIC_OVERLAYS = [
    ("None",                  "",         None),
    ("Max Q_inj (mC/cm²)",    "mC/cm²",   _qinj_max),
    ("Max Q_ph (nC)",         "nC",       _qph_max),
    ("Max V_d (V)",           "V",        _vd_max),
    ("Mean R_a (kΩ)",         "kΩ",       _ra_mean),
    ("Max amplitude (µA)",    "µA",       _amp_max),
]


class ChannelMapPanel(QtWidgets.QWidget):
    """Renders the device channel map, optionally tinted by a metric.

    The session's :class:`ElectrodeArray` carries one
    :class:`ElectrodePosition` per electrode with ``(number, row,
    col)``. We draw a disk per electrode at its grid position, label
    it with the channel number, and (when a metric is selected)
    color it by the per-channel value reduced via
    :data:`_METRIC_OVERLAYS`.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._session: Optional[Session] = None
        self._figure = Figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
        self._canvas = FigureCanvas(self._figure)

        # Top control row: metric picker + colormap legend toggle.
        ctrl = QtWidgets.QHBoxLayout()
        ctrl.setContentsMargins(4, 4, 4, 0)
        ctrl.addWidget(QtWidgets.QLabel("Overlay:"))
        self.metric_combo = QtWidgets.QComboBox()
        for label, _unit, _fn in _METRIC_OVERLAYS:
            self.metric_combo.addItem(label)
        self.metric_combo.currentIndexChanged.connect(self._refresh)
        ctrl.addWidget(self.metric_combo)
        self.show_values_check = QtWidgets.QCheckBox("Show values")
        self.show_values_check.setChecked(True)
        self.show_values_check.toggled.connect(self._refresh)
        ctrl.addWidget(self.show_values_check)
        ctrl.addStretch(1)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(ctrl)
        v.addWidget(self._canvas, stretch=1)

    def set_session(self, session: Optional[Session]):
        """Hand a session in (or ``None`` to clear) and refresh."""
        if session is self._session:
            return
        self._session = session
        self._refresh()

    def _refresh(self, *_):
        """Re-render the grid based on the current session + metric."""
        self._figure.clear()
        ax = self._figure.add_subplot(111)
        session = self._session
        if session is None or session.test.array is None:
            ax.text(0.5, 0.5, "Select a session to view its channel map.",
                    ha="center", va="center", transform=ax.transAxes,
                    color="#666", fontsize=10)
            ax.axis("off")
            self._canvas.draw_idle()
            return
        array = session.test.array
        # Aggregate metric per channel by walking every Run that drove
        # that channel as its active. Channels that were never the
        # active simply get NaN and render neutral.
        idx = self.metric_combo.currentIndex()
        label, unit, reducer = _METRIC_OVERLAYS[idx]
        per_channel: Dict[int, float] = {}
        if reducer is not None:
            for run in session.runs:
                ch = run.configuration.active
                try:
                    val = reducer(run)
                except Exception:
                    val = float("nan")
                # If a channel appears in multiple runs, keep the
                # most extreme (max for forward metrics, mean for R_a
                # already aggregates inside the reducer).
                prev = per_channel.get(ch)
                if prev is None or (np.isfinite(val) and val > prev):
                    per_channel[ch] = val

        finite_vals = [v for v in per_channel.values() if np.isfinite(v)]
        # Color scale — use simple min/max normalization. Empty
        # finite_vals (no metric, or every channel is NaN) skips the
        # colorbar.
        cmap = matplotlib.colormaps.get_cmap("viridis")
        if finite_vals:
            vmin, vmax = min(finite_vals), max(finite_vals)
            if vmin == vmax:
                # Avoid divide-by-zero in normalization; nudge bounds.
                vmin, vmax = vmin - 1e-9, vmax + 1e-9
        else:
            vmin = vmax = 0.0

        # Geometry: draw row 0 at the TOP (matches the device-view
        # widget convention in setup_tab). Cell pitch is 1 unit; disk
        # radius 0.4 leaves visible gridlines between adjacent cells.
        radius = 0.4
        rows = max((s.row for s in array.sites), default=0) + 1
        cols = max((s.col for s in array.sites), default=0) + 1
        ax.set_xlim(-0.5, cols - 0.5)
        ax.set_ylim(rows - 0.5, -0.5)   # invert y so row 0 is on top
        ax.set_aspect("equal")
        ax.set_xticks(range(cols))
        ax.set_yticks(range(rows))
        ax.grid(True, linestyle=":", color="#cccccc", linewidth=0.5)
        ax.set_xlabel("column")
        ax.set_ylabel("row")
        title = (f"{array.name} — {label}" if reducer is not None
                 else array.name)
        ax.set_title(title)

        for site in array.sites:
            cx, cy = site.col, site.row
            val = per_channel.get(site.number, float("nan"))
            # Color: viridis when value is finite + we have a scale,
            # neutral light grey otherwise. Black border on every disk
            # for visual separation.
            if reducer is not None and np.isfinite(val) and finite_vals:
                color = cmap((val - vmin) / (vmax - vmin))
                edge = "black"
            else:
                color = "#dcdcdc"
                edge = "#888888"
            disk = matplotlib.patches.Circle(
                (cx, cy), radius, facecolor=color, edgecolor=edge,
                linewidth=1.0, zorder=2)
            ax.add_patch(disk)
            # Channel number always shown at the top half of the disk;
            # metric value (when applicable + show_values_check) below.
            txt_color = _readable_text_color(color) if reducer is not None \
                        and np.isfinite(val) and finite_vals else "#222"
            if reducer is not None and self.show_values_check.isChecked():
                ax.text(cx, cy - 0.10, str(site.number), ha="center",
                        va="center", color=txt_color, fontsize=8,
                        fontweight="bold", zorder=3)
                if np.isfinite(val):
                    ax.text(cx, cy + 0.18, f"{val:.2f}", ha="center",
                            va="center", color=txt_color, fontsize=7,
                            zorder=3)
            else:
                ax.text(cx, cy, str(site.number), ha="center",
                        va="center", color=txt_color, fontsize=9,
                        fontweight="bold", zorder=3)

        # Colorbar (only when a real metric is being shown).
        if reducer is not None and finite_vals:
            sm = matplotlib.cm.ScalarMappable(
                cmap=cmap,
                norm=matplotlib.colors.Normalize(vmin=vmin, vmax=vmax))
            sm.set_array([])
            cb = self._figure.colorbar(sm, ax=ax, fraction=0.046, pad=0.04)
            cb.set_label(label + (f" [{unit}]" if unit else ""))

        self._figure.tight_layout()
        self._canvas.draw_idle()


def _readable_text_color(rgba) -> str:
    """Return black or white depending on background luminance.

    Standard relative-luminance threshold (Y = 0.299 R + 0.587 G +
    0.114 B). Used so the channel number stays visible on every
    viridis-bin background.
    """
    r, g, b = rgba[0], rgba[1], rgba[2]
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return "#000000" if luminance > 0.55 else "#ffffff"


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
def launch(initial_path: Optional[Path] = None) -> int:
    """Open the viewer as a standalone application."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = ViewerWindow(initial_path=initial_path)
    win.show()
    return app.exec()


def main() -> int:                        # pragma: no cover
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("path", nargs="?", default=None,
                   help="Optional .npz file or folder to load on startup")
    args = p.parse_args()
    return launch(Path(args.path) if args.path else None)


if __name__ == "__main__":                 # pragma: no cover
    sys.exit(main())
