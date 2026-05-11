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
from . import rich


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
    """Two rows of toggles: channels on top, per-trace axis dropdowns
    below, mirroring the experiment-tab's MultiChannelScope.

    The wave-type row is one ``QComboBox`` per trace with three
    options: ``N/A`` (hide), ``Left y-axis``, ``Right y-axis``.
    Default routing matches MultiChannelScope: I_mon → right axis,
    V_mon / E_act / E_ret → left. The Viewer's overlay plot
    (``plot_overlay``) consumes :meth:`axis_map` to place each
    trace, replacing the legacy hardcoded "I_mon goes right".

    Inset toggle + multi-select dropdown sit in a third row, again
    matching the experiment scope so users see the same controls in
    both places. Emits :pyattr:`selectionChanged` whenever any
    control flips so the panel can re-render.
    """

    selectionChanged = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Lazy-import to avoid a circular dep with widgets.py.
        from .widgets import AXIS_LEFT, AXIS_NA, AXIS_RIGHT
        from .multichannel_scope import (
            DEFAULT_TRACE_AXIS, TRACE_COLOURS,
        )
        self._AXIS_LEFT = AXIS_LEFT
        self._AXIS_RIGHT = AXIS_RIGHT
        self._AXIS_NA = AXIS_NA
        self._channel_checks: Dict[int, QtWidgets.QCheckBox] = {}
        self.axis_combos: Dict[str, QtWidgets.QComboBox] = {}
        self.axis_labels: Dict[str, QtWidgets.QLabel] = {}
        self._axis_map: Dict[str, str] = dict(DEFAULT_TRACE_AXIS)
        self._available_traces: set = set(WAVE_TYPES)

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
        self._channel_row_stretch_index = self.channel_row.count()
        self.channel_row.addStretch(1)

        # Waveform-type row — per-trace axis dropdown (replaces the old
        # checkbox grid). Order matches WAVE_TYPES so layout is stable.
        self.wave_row = QtWidgets.QHBoxLayout()
        self.wave_row.setContentsMargins(0, 0, 0, 0)
        self.wave_row.setSpacing(8)
        self.wave_row.addWidget(QtWidgets.QLabel("Waveforms:"))
        for wname in WAVE_TYPES:
            lbl = QtWidgets.QLabel(wname)
            lbl.setStyleSheet(
                f"color: {TRACE_COLOURS.get(wname, '#000')}; "
                f"font-weight: bold;")
            self.axis_labels[wname] = lbl
            self.wave_row.addWidget(lbl)
            combo = QtWidgets.QComboBox()
            combo.addItem("N/A",          userData=AXIS_NA)
            combo.addItem("Left y-axis",  userData=AXIS_LEFT)
            combo.addItem("Right y-axis", userData=AXIS_RIGHT)
            default_axis = DEFAULT_TRACE_AXIS.get(wname, AXIS_LEFT)
            combo.setCurrentIndex(
                {AXIS_NA: 0, AXIS_LEFT: 1, AXIS_RIGHT: 2}[default_axis])
            combo.setToolTip(
                f"{wname} placement on the overlay: <b>N/A</b> hides "
                f"it, <b>Left y-axis</b> uses the voltage scale, "
                f"<b>Right y-axis</b> uses the second (current / "
                f"alternate) scale. Default: "
                f"<b>{'Right y-axis' if DEFAULT_TRACE_AXIS[wname] == AXIS_RIGHT else 'Left y-axis'}</b>.")
            combo.currentIndexChanged.connect(
                lambda _idx, t=wname: self._on_axis_changed(t))
            self.axis_combos[wname] = combo
            self.wave_row.addWidget(combo)
        self.wave_row.addSpacing(12)

        # Inset controls — same QToolButton + QMenu pattern as
        # MultiChannelScope, so users see identical UX in both places.
        self.inset_check = QtWidgets.QCheckBox("Inset")
        self.inset_check.setToolTip(
            "Show a compact inset plot below the main overlay, "
            "mirroring the trace(s) you pick from the dropdown next "
            "to this toggle.")
        self.inset_check.toggled.connect(self._on_inset_toggled)
        self.wave_row.addWidget(self.inset_check)
        self.inset_btn = QtWidgets.QToolButton()
        self.inset_btn.setText("(pick traces)")
        self.inset_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.inset_btn.setToolTip(
            "Pick which traces appear in the inset (multi-select).")
        self._inset_menu = QtWidgets.QMenu(self.inset_btn)
        self.inset_actions: Dict[str, QtGui.QAction] = {}
        for trace in WAVE_TYPES:
            act = self._inset_menu.addAction(trace)
            act.setCheckable(True)
            act.triggered.connect(self._on_inset_traces_changed)
            self.inset_actions[trace] = act
        self.inset_btn.setMenu(self._inset_menu)
        self.inset_btn.setEnabled(False)
        self.wave_row.addWidget(self.inset_btn)
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
        """Set of trace names not set to N/A. Each trace is also
        gated by ``_available_traces`` so disappearing roles drop
        out cleanly."""
        return {w for w in WAVE_TYPES
                if self._axis_map.get(w) != self._AXIS_NA
                and w in self._available_traces}

    def axis_map(self) -> Dict[str, str]:
        """Per-trace axis assignment — values are
        ``"left"`` / ``"right"`` / ``"na"``."""
        return dict(self._axis_map)

    def inset_enabled(self) -> bool:
        return bool(self.inset_check.isChecked())

    def inset_traces(self) -> List[str]:
        return [t for t, act in self.inset_actions.items() if act.isChecked()]

    def set_available_traces(self, available) -> None:
        """Limit the per-trace controls to traces actually present in
        the loaded session. Mirrors :meth:`MultiChannelScope.set_available_traces`
        including the ``E_ret → E_act`` auto-include rule.
        """
        from .multichannel_scope import TRACE_EACT, TRACE_ERET
        if not available:
            allowed = set(WAVE_TYPES)
        else:
            allowed = {str(t) for t in available if t in WAVE_TYPES}
        if TRACE_ERET in allowed:
            allowed.add(TRACE_EACT)
        self._available_traces = allowed
        for trace in WAVE_TYPES:
            visible = trace in allowed
            self.axis_labels[trace].setVisible(visible)
            self.axis_combos[trace].setVisible(visible)
        for trace, act in self.inset_actions.items():
            act.setVisible(trace in allowed)
            if trace not in allowed and act.isChecked():
                act.blockSignals(True)
                try:
                    act.setChecked(False)
                finally:
                    act.blockSignals(False)
        self._refresh_inset_button_label()
        self._emit()

    # ----------------------------------------------------------- internal
    def _set_all_channels(self, on: bool) -> None:
        for cb in self._channel_checks.values():
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
        self._emit()

    def _on_axis_changed(self, trace: str) -> None:
        combo = self.axis_combos.get(trace)
        if combo is None:
            return
        new_axis = combo.currentData() or self._AXIS_LEFT
        if new_axis not in (self._AXIS_NA, self._AXIS_LEFT, self._AXIS_RIGHT):
            new_axis = self._AXIS_LEFT
        self._axis_map[trace] = new_axis
        self._emit()

    def _on_inset_toggled(self, on: bool) -> None:
        self.inset_btn.setEnabled(bool(on))
        self._emit()

    def _on_inset_traces_changed(self, *_) -> None:
        self._refresh_inset_button_label()
        self._emit()

    def _refresh_inset_button_label(self) -> None:
        picked = self.inset_traces()
        if not picked:
            self.inset_btn.setText("(pick traces)")
        else:
            self.inset_btn.setText(", ".join(picked))

    def _emit(self, *_):
        self.selectionChanged.emit()

    # --------------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        """Snapshot the trace-toggle state for persistence.

        Mirrors :meth:`MultiChannelScope.current_prefs` so the
        round-trip format is identical between the experiment view
        and the Viewer — a saved Viewer prefs blob can be diffed
        against a saved experiment-view blob without translation.
        """
        return {
            "axis_map": self.axis_map(),
            "inset_enabled": self.inset_enabled(),
            "inset_traces": self.inset_traces(),
        }

    def restore_prefs(self, p: dict) -> None:
        """Apply a previously-saved trace-toggle snapshot.

        Defensive on every key — a missing or malformed value is
        ignored rather than raising, so a stale prefs file never
        blocks the launch.
        """
        if not isinstance(p, dict) or not p:
            return
        axes = p.get("axis_map")
        if isinstance(axes, dict):
            for trace, axis in axes.items():
                if trace not in self.axis_combos:
                    continue
                if axis not in (self._AXIS_NA, self._AXIS_LEFT, self._AXIS_RIGHT):
                    continue
                combo = self.axis_combos[trace]
                idx_map = {self._AXIS_NA: 0,
                           self._AXIS_LEFT: 1,
                           self._AXIS_RIGHT: 2}
                combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(idx_map[axis])
                finally:
                    combo.blockSignals(False)
                self._axis_map[trace] = axis
        if "inset_enabled" in p:
            try:
                self.inset_check.blockSignals(True)
                self.inset_check.setChecked(bool(p["inset_enabled"]))
                self.inset_btn.setEnabled(bool(p["inset_enabled"]))
            except (TypeError, ValueError):
                pass
            finally:
                self.inset_check.blockSignals(False)
        traces = p.get("inset_traces")
        if isinstance(traces, (list, tuple, set)):
            wanted = {str(t) for t in traces}
            for trace, act in self.inset_actions.items():
                act.blockSignals(True)
                try:
                    act.setChecked(trace in wanted)
                finally:
                    act.blockSignals(False)
            self._refresh_inset_button_label()
        # Single emission at the end so listeners only see one
        # consistent state, not three intermediate ones.
        self._emit()


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
        # Cached overlay context for the trace-toggle re-render.
        # The original ``_overlay_captures_by_channel`` /
        # ``_overlay_title`` were declared as CLASS attributes on
        # :class:`ViewerWindow`; the transplant block at the
        # bottom of this file copies METHODS (callables) onto
        # ``ViewerPanel`` but not class-level data attributes, so
        # the panel started life without these defaults — and the
        # restored-prefs path (added with the persistence feature)
        # fires ``trace_toggles.selectionChanged`` →
        # ``_refresh_overlay`` BEFORE any overlay has been built,
        # tripping ``AttributeError``. Seeding the attributes here
        # gives the panel the same starting state ViewerWindow
        # always had.
        self._overlay_captures_by_channel: Dict[object, "Capture"] = {}
        self._overlay_title: str = ""

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
        # Viewer-side gridlines toggle. Independent from the
        # main window's View → Gridlines action — the Viewer
        # uses matplotlib (different rendering pipeline from the
        # experiment plots' pyqtgraph) and is a results-review
        # surface where users often want a grid for read-off of
        # specific values, while during a live experiment a grid
        # would obscure subtle trace features. Default OFF (matches
        # the experiment plots) so the initial view is clean.
        self._show_grid: bool = False
        self.grid_toggle = QtWidgets.QCheckBox("Gridlines")
        self.grid_toggle.setChecked(self._show_grid)
        self.grid_toggle.setToolTip(
            "Show gridlines on the plot. Off by default; turn on "
            "when you want to read off a specific value at the "
            "expense of trace contrast.")
        self.grid_toggle.toggled.connect(self._on_grid_toggled)
        plot_panel = QtWidgets.QWidget()
        pl = QtWidgets.QVBoxLayout(plot_panel)
        pl.setContentsMargins(0, 0, 0, 0)
        # Top row: matplotlib toolbar + gridline toggle. Keeping
        # them on one line keeps vertical real estate for the plot
        # itself.
        top_row = QtWidgets.QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)
        top_row.addWidget(self.toolbar, stretch=1)
        top_row.addWidget(self.grid_toggle, stretch=0)
        top_row_w = QtWidgets.QWidget(); top_row_w.setLayout(top_row)
        pl.addWidget(top_row_w)
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

        # Held so :meth:`current_prefs` can serialise the splitter
        # sizes the user dragged. Two splitters: the outer (tree | plot)
        # and the inner (plot canvas | info-panel) on the right side.
        self._outer_split = splitter
        self._right_split = right_split

        # Last-opened-path memory — populated by ``load_folder`` /
        # ``load_session_file`` and read by ``current_prefs``. The
        # restore_prefs path uses this so a re-launch comes back to
        # whatever the user was looking at last.
        self._last_open_path: Optional[str] = None

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

    # -----------------------------------------------------------------
    def _on_grid_toggled(self, checked: bool) -> None:
        """Gridline checkbox flipped — store the new state and
        re-render whatever the current view is. The next plot
        call picks up the new ``self._show_grid`` value via the
        ``show_grid`` kwarg threaded through :func:`plot_capture`,
        :func:`plot_overlay`, :func:`plot_qinj_vs_amplitude`, and
        :func:`plot_vd_vs_qinj`.

        Audit findings #14 + #15:

        * **#14** — the previous fallback path emitted
          ``self.tree.itemClicked`` but the tree only wires
          ``currentItemChanged`` (see line 553). Toggling
          gridlines while viewing a single capture or a Q_inj /
          V_d analysis surface produced zero re-render. Fixed by
          calling :meth:`_on_tree_item` directly so the same
          render path the user originally took fires again.
        * **#15** — the Channel Map tab is its own matplotlib
          rendering surface (:class:`ChannelMapPanel`). It used to
          hardcode ``ax.grid(True, ...)``. Now its ``_refresh``
          consults ``parent._show_grid`` so flipping this toggle
          updates the Plot tab AND the Channel Map tab in
          lockstep.
        """
        self._show_grid = bool(checked)
        # Refresh the Channel Map tab whenever the toggle flips —
        # it might not be the currently-shown tab, but the user
        # could switch to it next, and the rebuild is cheap.
        if hasattr(self, "map_panel") and self.map_panel is not None:
            try:
                self.map_panel._refresh()
            except Exception:
                pass
        # Re-render whichever view is currently shown on the
        # Plot tab.
        if self._overlay_captures_by_channel:
            # An overlay is up — refresh it in place.
            try:
                self._refresh_overlay()
                return
            except Exception:
                pass
        # Otherwise the user is on a single capture / Q_inj /
        # V_d view. ``_on_tree_item`` is the same handler the
        # tree's currentItemChanged signal fires; re-invoke it
        # with the active item to re-run the appropriate
        # ``_show_*`` method and pick up the new ``_show_grid``.
        selected = (self.tree.currentItem()
                    if hasattr(self, "tree") else None)
        if selected is not None:
            try:
                self._on_tree_item(selected, None)
            except Exception:
                pass


class ViewerWindow(QtWidgets.QMainWindow):
    """Standalone viewer window. Wraps :class:`ViewerPanel` with menus
    and a status bar so ``run_viewer.py`` (and the bundled
    ``StimulationTestingViewer.exe``) stay one-window apps.

    Persists window geometry + the embedded panel's prefs to the same
    ``gui_prefs.json`` the main GUI uses (under the ``results`` key)
    so launching the standalone viewer twice — or launching it
    alongside the main GUI — gives the user a consistent context.
    """

    # Section name inside ``gui_prefs.json`` that the standalone
    # viewer reads + writes on its own. Matches the main window's
    # ``PREF_KEY_RESULTS`` so both apps round-trip through the same
    # blob of state without a translation layer.
    _PREFS_KEY = "results"

    def __init__(self, initial_path: Optional[Path] = None,
                 parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("POLARIS")
        self.resize(1400, 900)
        self._panel = ViewerPanel(initial_path=initial_path, parent=self)
        self.setCentralWidget(self._panel)
        # Forward panel status messages to the QStatusBar.
        self._panel.status_message.connect(self.statusBar().showMessage)
        self._build_menus()
        # Restore geometry + panel state from the shared prefs file.
        # Only run when the caller didn't force an ``initial_path`` —
        # that argument signals "open this file specifically", which
        # would override whatever was last open.
        self._restore_window_prefs(skip_last_open=initial_path is not None)

    # -----------------------------------------------------------------
    # Persistence — round-trips the standalone window's geometry
    # alongside the embedded panel's own state.
    # -----------------------------------------------------------------
    def _restore_window_prefs(self, *, skip_last_open: bool = False) -> None:
        """Load the ``results`` section from ``gui_prefs.json`` and
        apply it to both the window and the embedded panel.

        Failures are silent — a stale or unreadable prefs file just
        means the user gets a default-sized window.
        """
        try:
            from .prefs import load_prefs
            prefs = load_prefs() or {}
        except Exception:
            return
        section = prefs.get(self._PREFS_KEY) or {}
        if not isinstance(section, dict):
            return
        geom_b64 = section.get("window_geometry_b64")
        if isinstance(geom_b64, str) and geom_b64:
            try:
                ba = QtCore.QByteArray.fromBase64(geom_b64.encode("ascii"))
                self.restoreGeometry(ba)
            except Exception:
                pass
        viewer_section = section.get("viewer")
        if isinstance(viewer_section, dict):
            try:
                # When loading via CLI/argv we already have a tree
                # populated — don't let the saved last_open clobber
                # the user's explicit pick.
                if skip_last_open:
                    viewer_section = {k: v for k, v in viewer_section.items()
                                      if k != "last_open"}
                self._panel.restore_prefs(viewer_section)
            except Exception:
                pass

    def _save_window_prefs(self) -> None:
        """Write the standalone window's geometry + the panel's prefs
        back to ``gui_prefs.json`` under the ``results`` section.

        Read-modify-write so we don't clobber prefs that belong to
        the main GUI (Setup tab, experiment tabs, …) — the standalone
        Viewer only owns one section.
        """
        try:
            from .prefs import load_prefs, save_prefs
            prefs = load_prefs() or {}
            section = {"viewer": self._panel.current_prefs()}
            try:
                section["window_geometry_b64"] = bytes(
                    self.saveGeometry().toBase64()).decode("ascii")
            except Exception:
                pass
            prefs[self._PREFS_KEY] = section
            save_prefs(prefs)
        except Exception:
            # Persistence is a nicety — never block close on it.
            pass

    def closeEvent(self, ev):
        self._save_window_prefs()
        super().closeEvent(ev)

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
        # Stash the resolved folder path for ``current_prefs`` so a
        # re-launch can restore the same context. Stored even when
        # the folder turns out to be empty — opening it once still
        # signals user intent.
        try:
            self._last_open_path = str(Path(folder).resolve())
        except Exception:
            pass
        self.tree.clear()
        self._sessions.clear()
        npzs = sorted(folder.glob("*.npz"))
        if not npzs:
            # Empty folder: report via the status bar instead of a
            # modal popup. ``ResultsTab.refresh`` calls ``load_folder``
            # whenever the save directory is repointed (or just
            # selected, on a fresh install with no saved sessions),
            # and a blocking dialog there made the GUI feel broken.
            self.status_message.emit(f"No .npz sessions in {folder}")
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
        try:
            self._last_open_path = str(Path(path).resolve())
        except Exception:
            pass
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
        plot_capture(cap, run, session, fig=self.figure,
                     show_grid=self._show_grid)
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
        # Audit #25 — guard against NaN ``max_q_inj`` (all-aborted runs)
        # and non-finite ``surface_area_um2`` (legacy archives missing
        # the field).
        self._set_metric_table([
            ("Channel", run.configuration.display_name()),
            ("Surface area", _fmt_or_dash(
                run.surface_area_um2, ".0f", "µm²")),
            ("Captures", str(len(run.captures))),
            ("Max Q_inj", _fmt_or_dash(
                run.max_q_inj, ".3f", "mC/cm²")),
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
        """Re-render the overlay plot using the current toggle state.

        Passes the per-trace axis map AND the inset state through to
        :func:`plot_overlay` so the Viewer respects the user's
        N/A / Left / Right pick per trace, plus the optional inset
        with its multi-select trace subset.
        """
        if not self._overlay_captures_by_channel:
            return
        try:
            plot_overlay(
                self._overlay_captures_by_channel,
                fig=self.figure,
                channels_enabled=self.trace_toggles.enabled_keys(),
                waves_enabled=self.trace_toggles.enabled_waves(),
                axis_map=self.trace_toggles.axis_map(),
                inset_enabled=self.trace_toggles.inset_enabled(),
                inset_traces=set(self.trace_toggles.inset_traces()),
                show_grid=self._show_grid,
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
        plot_qinj_vs_amplitude(s, fig=self.figure,
                               show_grid=self._show_grid)
        self.canvas.draw_idle()

    @QtCore.pyqtSlot()
    def _show_vd_overlay(self) -> None:
        s = self._current_session()
        if s is None:
            return
        plot_vd_vs_qinj(s, fig=self.figure,
                        show_grid=self._show_grid)
        self.canvas.draw_idle()

    # -----------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------
    def current_prefs(self) -> dict:
        """Snapshot every Viewer setting worth remembering across runs.

        Mirrors :meth:`MultiChannelScope.current_prefs` for the trace
        bar so the two are diff-friendly, and adds Viewer-specific
        view state (last-opened path + splitter sizes).
        """
        out: dict = {
            "trace_toggles": self.trace_toggles.current_prefs(),
        }
        if getattr(self, "_last_open_path", None):
            out["last_open"] = self._last_open_path
        try:
            out["outer_split_sizes"] = list(self._outer_split.sizes())
        except Exception:
            pass
        try:
            out["right_split_sizes"] = list(self._right_split.sizes())
        except Exception:
            pass
        # Channel-map metric — small, persists which overlay the user
        # was last viewing. ``map_panel`` is a ChannelMapPanel.
        try:
            out["map_metric_index"] = int(self.map_panel.metric_combo.currentIndex())
            out["map_show_values"] = bool(self.map_panel.show_values_check.isChecked())
        except Exception:
            pass
        return out

    def restore_prefs(self, p: dict) -> None:
        """Apply a previously-saved Viewer snapshot.

        Defensive: every key is type-checked, and the last-opened
        path is verified to still exist before re-loading. Failures
        log via the status_message signal but never raise — a stale
        prefs file shouldn't block the next launch.
        """
        if not isinstance(p, dict) or not p:
            return
        # Trace toggles first so a re-render after load lands with
        # the user's preferred axis assignment.
        toggles = p.get("trace_toggles")
        if isinstance(toggles, dict):
            self.trace_toggles.restore_prefs(toggles)
        # Splitter sizes — guard against zero-collapse so a stored
        # "everything in the left pane" state can't render the plot
        # canvas invisible on next launch.
        outer = p.get("outer_split_sizes")
        if isinstance(outer, (list, tuple)) and len(outer) == 2:
            try:
                ints = [int(s) for s in outer]
                if all(s > 0 for s in ints):
                    self._outer_split.setSizes(ints)
            except (TypeError, ValueError):
                pass
        right = p.get("right_split_sizes")
        if isinstance(right, (list, tuple)) and len(right) == 2:
            try:
                ints = [int(s) for s in right]
                if all(s > 0 for s in ints):
                    self._right_split.setSizes(ints)
            except (TypeError, ValueError):
                pass
        # Channel-map controls.
        idx = p.get("map_metric_index")
        if isinstance(idx, int) and 0 <= idx < self.map_panel.metric_combo.count():
            self.map_panel.metric_combo.setCurrentIndex(idx)
        if "map_show_values" in p:
            try:
                self.map_panel.show_values_check.setChecked(bool(p["map_show_values"]))
            except (TypeError, ValueError):
                pass
        # Last-opened path goes last so its load_folder / load_session_file
        # call sees the restored toggles & splitter geometry already in place.
        last = p.get("last_open")
        if isinstance(last, str) and last:
            lp = Path(last)
            try:
                if lp.is_dir():
                    self.load_folder(lp)
                elif lp.is_file() and lp.suffix.lower() == ".npz":
                    self.load_session_file(lp)
            except Exception as e:
                self.status_message.emit(
                    f"Could not re-open last session: {e}")


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
              "_refresh_overlay",
              # Persistence — added so the embedded Results-tab
              # variant and the standalone ViewerWindow share one
              # snapshot/restore implementation.
              "current_prefs", "restore_prefs"):
    setattr(ViewerPanel, _name, getattr(ViewerWindow, _name))
del _name


# ---------------------------------------------------------------------------
# Side-panel info builders
# ---------------------------------------------------------------------------
def _fmt_or_dash(value: float, spec: str, unit: str = "") -> str:
    """Format ``value`` with ``spec`` or return ``"—"`` for NaN /
    non-finite. Audit finding #25 — every Viewer metric cell that
    previously read ``f"{x:.3f} nC"`` produced ``"nan nC"`` for
    aborted / pre-acquisition captures. Routing through this
    helper turns those cells into a clean em-dash that reads as
    "no data" to a human and doesn't trip a paper-figure
    auto-extraction script reading the table back.

    ``unit`` is appended with a leading space (if any) only on the
    formatted branch, so the dash stays on its own.
    """
    import math
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(v):
        return "—"
    suffix = f" {unit}" if unit else ""
    return format(v, spec) + suffix


def _capture_metric_rows(cap: Capture, run: ChannelRun) -> List[tuple]:
    m = cap.metrics
    L = rich.plain_label
    rows = [
        ("Capture #", cap.index),
        ("Status", _status_text(cap)),
        ("Amplitude", _fmt_or_dash(
            cap.pattern.excitation_phase.amplitude_ua, "+.2f", "µA")),
        (L('Q','ph'),  _fmt_or_dash(m.charge_per_phase_nc, ".3f", "nC")),
        (L('Q','inj'), _fmt_or_dash(m.charge_injection_mc_per_cm2,
                                    ".3f", "mC/cm²")),
        (L('E','ip'),  _fmt_or_dash(m.interpulse_potential_v, ".3f", "V")),
        (L('C','eff'), _fmt_or_dash(m.effective_capacitance_nf,
                                    ".3f", "nF")),
        (L('C','d'),   _fmt_or_dash(m.driving_capacitance_mf_per_cm2,
                                    ".3f", "mF/cm²")),
    ]
    if m.active_driving_voltage_per_phase_v:
        rows.append((f"{L('V','d')} active per phase",
                     ", ".join(f"{v:.3f}" for v in m.active_driving_voltage_per_phase_v)))
    if m.return_driving_voltage_per_phase_v:
        rows.append((f"{L('V','d')} return per phase",
                     ", ".join(f"{v:.3f}" for v in m.return_driving_voltage_per_phase_v)))
    if m.access_voltage_per_phase_v:
        rows.append((f"{L('V','a')} active",
                     ", ".join(f"{v:.3f}" for v in m.access_voltage_per_phase_v)))
    if m.access_resistance_per_phase_kohm:
        rows.append((f"{L('R','a')} active (kΩ)",
                     ", ".join(f"{r:.2f}" for r in m.access_resistance_per_phase_kohm)))
    if m.return_access_voltage_per_phase_v:
        rows.append((f"{L('V','a')} return",
                     ", ".join(f"{v:.3f}" for v in m.return_access_voltage_per_phase_v)))
    if m.return_access_resistance_per_phase_kohm:
        rows.append((f"{L('R','a')} return (kΩ)",
                     ", ".join(f"{r:.2f}" for r in m.return_access_resistance_per_phase_kohm)))
    if m.polarization_per_phase_v:
        rows.append((f"{L('E','pol')} active",
                     ", ".join(f"{e:.3f}" for e in m.polarization_per_phase_v)))
    if m.return_polarization_per_phase_v:
        rows.append((f"{L('E','pol')} return",
                     ", ".join(f"{e:.3f}" for e in m.return_polarization_per_phase_v)))
    # Tissue-damage screening (Shannon + modified Shannon). Only
    # surface when the metrics layer actually computed something —
    # an empty / pre-acquisition capture leaves
    # ``shannon_k_value`` as NaN and the classification as
    # ``"insufficient_data"``, in which case rendering the rows
    # would just clutter the panel with N/A entries. See
    # :mod:`stimtest.damage_models` for the full reference list and
    # the rationale for surfacing this as guidance rather than a
    # hard interlock.
    import math
    if math.isfinite(getattr(m, "shannon_k_value", float("nan"))):
        try:
            from ..damage_models import (
                CLASSIFICATION_LABELS, NEUROSTIMML_WEB_URL,
                DAMAGE_LEVEL_LABELS,
            )
        except Exception:
            CLASSIFICATION_LABELS = {}
            NEUROSTIMML_WEB_URL = ""
            DAMAGE_LEVEL_LABELS = {}
        # Shannon k-value gets its own row so a numeric reader can
        # see exactly where they sit relative to the 1.85 boundary.
        # Trailing arrow indicates the direction of safety (lower
        # is safer per the Shannon model).
        rows.append((
            f"{L('Shannon','k')}",
            f"{m.shannon_k_value:.3f}"))
        # Damage classification — single most-conservative verdict.
        # Plain-text label here; the live GUI render lives in
        # :class:`ChannelMapPanel` / future status badges where the
        # colour matters more.
        cls_label = CLASSIFICATION_LABELS.get(
            m.damage_classification, m.damage_classification)
        # Annotate with the band so the user understands why a
        # macro-cap or micro-cap criterion did or didn't apply.
        band_tag = ""
        band = getattr(m, "damage_band", "")
        if band == "macro":
            band_tag = " (macro band)"
        elif band == "micro":
            band_tag = " (micro band)"
        elif band == "meso":
            band_tag = " (meso band)"
        rows.append(("Damage screen", f"{cls_label}{band_tag}"))
        # 0–4 damage level mapped from the binary verdict, when
        # available. Stored as -1 to mean "no verdict"; we render
        # an em-dash in that case so the column doesn't read as a
        # default-zero "no damage" claim.
        level = getattr(m, "damage_level", -1)
        if isinstance(level, int) and level >= 0:
            level_label = DAMAGE_LEVEL_LABELS.get(level, f"Level {level}")
            rows.append(("Damage level (0–4)", f"{level} — {level_label}"))
        # Per-criterion bools — only surface when at least one is
        # True so the row count stays low for safe captures.
        crit = getattr(m, "damage_criteria", {}) or {}
        flags = [k for k, v in crit.items() if v]
        if flags:
            pretty = {
                "shannon": "Shannon k≥threshold",
                "macro_cap": "Macro charge density cap",
                "micro_cap": "Micro charge/phase cap",
            }
            rows.append((
                "Damage criteria fired",
                ", ".join(pretty.get(f, f) for f in flags)))
        # NeurostimML local-inference verdict (Li et al. 2024 RF-
        # Partial-19). Rendered alongside Shannon so a reader can
        # cross-compare the two classifiers. ``model_not_installed``
        # is rendered explicitly so the user knows the higher-
        # accuracy screen is available behind a one-time install
        # via Help → Install NeurostimML model….
        ml_class = getattr(m, "neurostimml_classification",
                            "model_not_installed")
        ml_prob = getattr(m, "neurostimml_probability", float("nan"))
        if ml_class == "model_not_installed":
            rows.append((
                "NeurostimML",
                "model not installed — see Help → Install NeurostimML model…"))
        else:
            ml_pretty = {
                "likely_safe":     "Likely safe",
                "likely_damaging": "Likely damaging",
            }.get(ml_class, ml_class)
            if math.isfinite(ml_prob):
                rows.append((
                    "NeurostimML",
                    f"{ml_pretty} (P = {ml_prob:.2f})"))
            else:
                rows.append(("NeurostimML", ml_pretty))
            # Optional extrapolation hint — recompute the feature
            # vector and check it against the per-feature training
            # ranges from Li et al. 2024 Table 2. We only render
            # the hint when a meaningful flag is present so safe
            # captures don't get a noisy "extrapolating: none" row.
            try:
                from ..neurostimml import (
                    build_feature_vector,
                    features_outside_training_range,
                )
                fv = build_feature_vector(
                    pattern=cap.pattern,
                    charge_per_phase_nc=m.charge_per_phase_nc,
                    charge_injection_mc_per_cm2=m.charge_injection_mc_per_cm2,
                    surface_area_um2=run.surface_area_um2,
                )
                if fv is not None:
                    flagged = features_outside_training_range(fv)
                    if flagged:
                        rows.append((
                            "NeurostimML extrapolation",
                            ", ".join(flagged)))
            except Exception:
                # Extrapolation hint is a nicety; never let it
                # break the metric-table rendering.
                pass
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
        self.metric_combo.setToolTip(
            "Which per-channel scalar to overlay on the channel "
            "map. \"None\" shows the geometry alone (one disk per "
            "electrode at its grid position, no colouring); the "
            "other entries colour each disk by that metric "
            "aggregated across the channel's run.")
        ctrl.addWidget(self.metric_combo)
        self.show_values_check = QtWidgets.QCheckBox("Show values")
        self.show_values_check.setChecked(True)
        self.show_values_check.toggled.connect(self._refresh)
        self.show_values_check.setToolTip(
            "Print the metric value (with unit) inside each "
            "channel disk. Off = colour-only — useful for "
            "denser arrays where the numeric labels overlap.")
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
        # Audit #15: honour the parent viewer's ``_show_grid`` toggle
        # rather than hardcoding the gridlines on. The channel-map
        # grid is mostly structural (one cell per electrode pitch),
        # but on dense arrays it overlaps the disk labels and the
        # user may want it off; flipping the View menu's "Gridlines"
        # checkbox now controls all four plot surfaces in lockstep.
        # Fall back to ``True`` when we can't reach the toggle (e.g.
        # the panel was constructed without a viewer parent in a
        # test harness).
        show_grid = True
        parent = self.parent()
        if parent is not None and hasattr(parent, "_show_grid"):
            show_grid = bool(parent._show_grid)
        ax.grid(show_grid, linestyle=":", color="#cccccc", linewidth=0.5)
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
