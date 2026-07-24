"""Gamry Echem Analyst-like session viewer.

Standalone window for visually inspecting saved sessions. Layout:

    ┌──────────────────────────┬────────────────────────────────────┐
    │  Sessions / Runs / Caps  │  Plot canvas (matplotlib)          │
    │  (tree on the left)      │  • Time / V / I / Active / Return │
    │                          │                                    │
    │                          ├────────────────────────────────────┤
    │                          │  Parameter / metric inspector      │
    └──────────────────────────┴────────────────────────────────────┘

* **File → Open…**             : load a PULSAR ``.npz`` session OR scope
                                 data (PicoScope-style ``.csv`` / ``.tsv`` /
                                 ``.xls`` / ``.xlsx``)
* **File → Open folder…**      : index a folder of sessions + scope-data
                                 files in the tree
* **Export → This plot…**      : save current capture to .tif/.png/.pdf
* **Export → All plots…**      : write a per-channel folder of .tif files
* **Export → Summary plots**   : Q_inj vs I_stim and V_d vs Q_inj overlays

Selecting a Run shows its summary plot (Q_inj vs amplitude). Selecting a
Capture shows the per-capture trace plot. Selecting a Session shows a
metadata page.
"""
from __future__ import annotations

import math
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

from ..persistence import (load_session_meta, load_session_npz,
                           load_picoscope, PICO_EXTENSIONS,
                           is_pulsar_session_xlsx)
from ..plotting import (
    plot_capture, plot_charge_transfer, plot_reciprocal_derivative,
    plot_overlay, plot_picoscope,
    plot_qinj_vs_amplitude, plot_vd_vs_qinj,
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
KIND_PICO = "pico"          # a PicoScope CSV (raw external-scope capture)

# Line-style choices for the per-trace Styles… dialog.  MODULE-LEVEL (not a
# ViewerWindow class attr) because the ViewerWindow→ViewerPanel transplant
# loop copies only METHODS — a class DATA attr like this would be missing on
# ViewerPanel and _open_style_dialog would AttributeError (silently, via Qt's
# click handler → "Styles… does nothing").
_LINESTYLE_CHOICES = (("Solid", "-"), ("Dashed", "--"),
                      ("Dotted", ":"), ("Dash-dot", "-."))


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

        # Channel COLUMN (operator: "[the CH01-CH16 row] needs to be a
        # column").  A vertical, scrollable checkbox list — Gamry Echem
        # Analyst-style left control panel.  Populated dynamically by
        # ``set_available_entries`` / ``set_available_channels``.
        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        header.addWidget(QtWidgets.QLabel("Channels:"))
        self._channel_all = QtWidgets.QPushButton("All")
        self._channel_none = QtWidgets.QPushButton("None")
        self._channel_all.setFixedWidth(40)
        self._channel_none.setFixedWidth(46)
        self._channel_all.clicked.connect(lambda: self._set_all_channels(True))
        self._channel_none.clicked.connect(lambda: self._set_all_channels(False))
        header.addWidget(self._channel_all)
        header.addWidget(self._channel_none)
        header.addStretch(1)
        # The vertical layout that actually holds the per-channel checkboxes.
        # Kept named ``channel_row`` for back-compat with the insertion code;
        # it is now a QVBoxLayout (a column).  A trailing stretch keeps the
        # checkboxes top-aligned; new boxes insert just before it.
        self.channel_row = QtWidgets.QVBoxLayout()
        self.channel_row.setContentsMargins(0, 0, 0, 0)
        self.channel_row.setSpacing(2)
        self.channel_row.addStretch(1)
        _chan_container = QtWidgets.QWidget()
        _chan_container.setLayout(self.channel_row)
        self._channel_scroll = QtWidgets.QScrollArea()
        self._channel_scroll.setWidgetResizable(True)
        self._channel_scroll.setWidget(_chan_container)
        self._channel_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._channel_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        # Waveform-type controls — per-trace axis dropdown.  Now stacked
        # VERTICALLY (the bar lives in a left column, not a top row).
        self.wave_row = QtWidgets.QVBoxLayout()
        self.wave_row.setContentsMargins(0, 0, 0, 0)
        self.wave_row.setSpacing(4)
        self.wave_row.addWidget(QtWidgets.QLabel("Waveforms:"))
        for wname in WAVE_TYPES:
            pair = QtWidgets.QHBoxLayout()
            pair.setContentsMargins(0, 0, 0, 0)
            pair.setSpacing(4)
            lbl = QtWidgets.QLabel(wname)
            lbl.setStyleSheet(
                f"color: {TRACE_COLOURS.get(wname, '#000')}; "
                f"font-weight: bold;")
            lbl.setFixedWidth(46)
            self.axis_labels[wname] = lbl
            pair.addWidget(lbl)
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
            pair.addWidget(combo, 1)
            self.wave_row.addLayout(pair)
        self.wave_row.addSpacing(8)

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

        # Assemble the left control column: channels header → scrollable
        # channel checkbox column (takes the slack) → separator → waveform
        # controls.  A modest max width keeps it from eating the plot.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 0)
        outer.setSpacing(4)
        outer.addLayout(header)
        outer.addWidget(self._channel_scroll, 1)
        _sep = QtWidgets.QFrame()
        _sep.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        outer.addWidget(_sep)
        outer.addLayout(self.wave_row)
        self.setMaximumWidth(220)

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


class _PicoRoleBar(QtWidgets.QWidget):
    """Per-channel role selector for a PicoScope CSV capture.

    Hidden unless a PicoScope node is selected.  Lets the operator map each
    channel (A/B/C/D, which vary per file) to a role so POLARIS can compute
    metrics; default = all unmapped → raw display.  The current-monitor
    channel is a VOLTAGE, so a scale (mV/µA) converts it to current.
    """
    changed = QtCore.pyqtSignal()
    _ROLE_LABELS = [("—", "none"), ("V_mon", "v_mon"),
                    ("I_mon (current)", "i_mon"),
                    ("E_act", "e_act"), ("E_ret", "e_ret")]

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(8)
        lay.addWidget(QtWidgets.QLabel("Channel roles:"))
        self._combo_row = QtWidgets.QHBoxLayout()
        self._combo_row.setSpacing(8)
        lay.addLayout(self._combo_row)
        self._combos: Dict[str, QtWidgets.QComboBox] = {}
        self._saved_roles: Dict[str, str] = {}   # remembered across files
        lay.addStretch(1)
        lay.addWidget(QtWidgets.QLabel("Current scale:"))
        self.scale_spin = QtWidgets.QDoubleSpinBox()
        self.scale_spin.setRange(0.001, 1000.0)
        self.scale_spin.setDecimals(3)
        self.scale_spin.setValue(2.5)
        self.scale_spin.setSuffix(" mV/µA")
        self.scale_spin.setToolTip(
            "Current-monitor scaling — converts the channel mapped to I_mon "
            "(volts) into µA.  Affects charge / R_a only; the voltage metrics "
            "(V_a / V_d / E_pol) are scale-independent.")
        lay.addWidget(self.scale_spin)
        self.metrics_chk = QtWidgets.QCheckBox("Compute metrics")
        self.metrics_chk.setToolTip(
            "Infer the pulse pattern from the current channel and show the "
            "V_a / V_d / E_pol markers + metrics.  Needs V_mon assigned.")
        lay.addWidget(self.metrics_chk)
        self.scale_spin.valueChanged.connect(lambda *_: self.changed.emit())
        self.metrics_chk.toggled.connect(lambda *_: self.changed.emit())

    def set_channels(self, names) -> None:
        """Rebuild the per-channel combos for ``names`` (restoring any
        remembered role per channel name)."""
        while self._combo_row.count():
            it = self._combo_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        self._combos = {}
        for nm in names:
            box = QtWidgets.QWidget()
            h = QtWidgets.QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(2)
            h.addWidget(QtWidgets.QLabel(nm.replace("Channel ", "Ch ") + ":"))
            combo = QtWidgets.QComboBox()
            for lbl, tok in self._ROLE_LABELS:
                combo.addItem(lbl, tok)
            idx = combo.findData(self._saved_roles.get(nm, "none"))
            combo.setCurrentIndex(max(idx, 0))
            combo.currentIndexChanged.connect(self._on_combo)
            self._combos[nm] = combo
            h.addWidget(combo)
            self._combo_row.addWidget(box)

    def _on_combo(self, *_):
        for nm, c in self._combos.items():
            self._saved_roles[nm] = c.currentData()
        self.changed.emit()

    def roles(self) -> Dict[str, str]:
        return {nm: c.currentData() for nm, c in self._combos.items()}

    def current_scale(self) -> float:
        return float(self.scale_spin.value())

    def metrics_enabled(self) -> bool:
        return self.metrics_chk.isChecked()

    def has_vmon(self) -> bool:
        return any(c.currentData() == "v_mon" for c in self._combos.values())

    def prefs(self) -> dict:
        return {"roles": dict(self._saved_roles),
                "scale": self.current_scale(),
                "metrics": self.metrics_enabled()}

    def restore(self, p: dict) -> None:
        if not isinstance(p, dict):
            return
        self._saved_roles.update(p.get("roles") or {})
        try:
            self.scale_spin.setValue(float(p.get("scale", 2.5)))
        except Exception:
            pass
        self.metrics_chk.setChecked(bool(p.get("metrics", False)))


class _PlotViewBar(QtWidgets.QWidget):
    """Plot-view controls: user-settable X / Y axis ranges + the
    current-vs-current-density right-axis toggle.

    Operator requests (POLARIS review): "allow the user to set the axis
    range" (item 2) and a current-vs-current-density toggle defaulting to
    current (extra).  All controls default to Auto / Current so a freshly
    opened file renders exactly as before until the user overrides.
    """

    changed = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        def _spin():
            s = QtWidgets.QDoubleSpinBox()
            s.setRange(-1e9, 1e9)
            s.setDecimals(3)
            s.setMaximumWidth(90)
            s.setKeyboardTracking(False)
            s.valueChanged.connect(lambda *_: self._emit())
            return s

        # X range
        self.x_auto = QtWidgets.QCheckBox("X auto")
        self.x_auto.setChecked(True)
        self.x_auto.toggled.connect(self._on_auto)
        self.x_min = _spin(); self.x_max = _spin()
        lay.addWidget(self.x_auto)
        lay.addWidget(QtWidgets.QLabel("min")); lay.addWidget(self.x_min)
        lay.addWidget(QtWidgets.QLabel("max")); lay.addWidget(self.x_max)

        sep = QtWidgets.QFrame()
        sep.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        lay.addWidget(sep)

        # Y range — LEFT (voltage) axis.
        self.y_auto = QtWidgets.QCheckBox("Left Y auto")
        self.y_auto.setChecked(True)
        self.y_auto.toggled.connect(self._on_auto)
        self.y_min = _spin(); self.y_max = _spin()
        lay.addWidget(self.y_auto)
        lay.addWidget(QtWidgets.QLabel("min")); lay.addWidget(self.y_min)
        lay.addWidget(QtWidgets.QLabel("max")); lay.addWidget(self.y_max)

        sep2 = QtWidgets.QFrame()
        sep2.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        lay.addWidget(sep2)

        # Y range — RIGHT (current / density) axis (operator: "Allow for
        # changing the right axis range").
        self.ry_auto = QtWidgets.QCheckBox("Right Y auto")
        self.ry_auto.setChecked(True)
        self.ry_auto.toggled.connect(self._on_auto)
        self.ry_min = _spin(); self.ry_max = _spin()
        lay.addWidget(self.ry_auto)
        lay.addWidget(QtWidgets.QLabel("min")); lay.addWidget(self.ry_min)
        lay.addWidget(QtWidgets.QLabel("max")); lay.addWidget(self.ry_max)

        sep3 = QtWidgets.QFrame()
        sep3.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        lay.addWidget(sep3)

        # Current vs current density (right axis); default = current.
        lay.addWidget(QtWidgets.QLabel("Right axis:"))
        self.unit_combo = QtWidgets.QComboBox()
        self.unit_combo.addItem("Current (µA)", "current")
        self.unit_combo.addItem("Current density (A/cm²)", "density")
        self.unit_combo.setCurrentIndex(0)            # default current
        self.unit_combo.currentIndexChanged.connect(lambda *_: self._emit())
        lay.addWidget(self.unit_combo)

        sep4 = QtWidgets.QFrame()
        sep4.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        lay.addWidget(sep4)

        # Charge-transfer (dE/dt) decomposition view — Harris 2019
        # chronopotentiometry (capacitive vs Faradaic).  Swaps the normal
        # capture plot for the 3-panel dE/dt / reciprocal-derivative view.
        self.ct_check = QtWidgets.QCheckBox("Charge transfer (dE/dt)")
        self.ct_check.setToolTip(
            "Show the capacitive/Faradaic decomposition (dE/dt, 1/(dE/dt)) "
            "for the selected capture — Harris 2019 chronopotentiometry.")
        self.ct_check.toggled.connect(lambda *_: self._emit())
        lay.addWidget(self.ct_check)
        # Reciprocal derivative chronopotentiometry (RDC) — Musa 2010.  Plots
        # dt/dE vs the electrode potential E; peaks = Faradaic, flat =
        # capacitive.  Swaps the normal capture plot, like the CT view; the two
        # are mutually exclusive (both replace the main plot).
        self.rdc_check = QtWidgets.QCheckBox("Reciprocal deriv. (RDC)")
        self.rdc_check.setToolTip(
            "Show the reciprocal-derivative (dt/dE vs potential) analysis for "
            "the selected capture — Musa 2010; peaks mark Faradaic reactions, "
            "flat regions capacitive charging.")
        self.rdc_check.toggled.connect(self._on_rdc_toggled)
        self.ct_check.toggled.connect(self._on_ct_toggled)
        lay.addWidget(self.rdc_check)
        # dV/dt + 1/(dV/dt) as NORMALIZED-overlay option traces on the capture
        # plot (operator: "derivative and reciprocal of derivative as option
        # traces … normalized overlay").  Independent of the 3-panel view above.
        self.dvdt_check = QtWidgets.QCheckBox("dV/dt")
        self.dvdt_check.setToolTip("Overlay the derivative dV/dt (normalized, "
                                   "dashed) on the capture plot.")
        self.dvdt_check.toggled.connect(lambda *_: self._emit())
        lay.addWidget(self.dvdt_check)
        self.recip_check = QtWidgets.QCheckBox("1/(dV/dt)")
        self.recip_check.setToolTip("Overlay the reciprocal derivative "
                                    "1/(dV/dt) (normalized, dotted).")
        self.recip_check.toggled.connect(lambda *_: self._emit())
        lay.addWidget(self.recip_check)

        # The left-axis label ("Voltage [V]" vs "Potential vs <ref> [V]" vs
        # "Voltage vs <return> [V]") is AUTOMATIC (operator: "It should be
        # automatic") — decided per render from the axis content by the shared
        # ``plotting._voltage_axis_label`` helper, matching the live PULSAR
        # plot.  The old "Potential axis" / "Voltage vs return" checkboxes are
        # gone.

        sep5 = QtWidgets.QFrame()
        sep5.setFrameShape(QtWidgets.QFrame.Shape.VLine)
        lay.addWidget(sep5)
        # Median DESPIKE filter for the displayed traces (operator: "In POLARIS,
        # add an option to filter the data; I am seeing frequent spikes of the
        # same magnitude").  The periodic switching artifacts (a sharp overshoot
        # + a few µs of ringing at each phase boundary) are rejected by a median
        # filter (metrics._despike) applied to the plotted traces + the marker
        # reads.  DISPLAY-ONLY — the saved .npz is untouched.
        self.filter_check = QtWidgets.QCheckBox("Filter spikes")
        self.filter_check.setToolTip(
            "Median-filter the displayed traces to reject the periodic "
            "switching spikes / ringing.  Does NOT change the saved data.")
        self.filter_check.toggled.connect(self._on_filter_toggled)
        lay.addWidget(self.filter_check)
        self.filter_win = QtWidgets.QDoubleSpinBox()
        self.filter_win.setRange(1.0, 50.0)
        self.filter_win.setValue(4.0)
        self.filter_win.setSingleStep(1.0)
        self.filter_win.setDecimals(1)
        self.filter_win.setSuffix(" µs")
        self.filter_win.setEnabled(False)
        self.filter_win.setToolTip("Median-filter window (µs) — larger rejects "
                                   "wider spikes but blurs fast edges.")
        self.filter_win.editingFinished.connect(
            lambda: self._emit() if self.filter_check.isChecked() else None)
        lay.addWidget(self.filter_win)

        lay.addStretch(1)
        self._on_auto()

    # -- state queries --------------------------------------------------
    def _on_auto(self, *_):
        self.x_min.setEnabled(not self.x_auto.isChecked())
        self.x_max.setEnabled(not self.x_auto.isChecked())
        self.y_min.setEnabled(not self.y_auto.isChecked())
        self.y_max.setEnabled(not self.y_auto.isChecked())
        self.ry_min.setEnabled(not self.ry_auto.isChecked())
        self.ry_max.setEnabled(not self.ry_auto.isChecked())
        self._emit()

    def _emit(self, *_):
        self.changed.emit()

    def xrange(self):
        if self.x_auto.isChecked():
            return None
        lo, hi = self.x_min.value(), self.x_max.value()
        return (lo, hi) if hi > lo else None

    def yrange(self):
        if self.y_auto.isChecked():
            return None
        lo, hi = self.y_min.value(), self.y_max.value()
        return (lo, hi) if hi > lo else None

    def yrange_right(self):
        if self.ry_auto.isChecked():
            return None
        lo, hi = self.ry_min.value(), self.ry_max.value()
        return (lo, hi) if hi > lo else None

    def density(self) -> bool:
        return self.unit_combo.currentData() == "density"

    def _on_ct_toggled(self, on: bool) -> None:
        # CT and RDC both replace the main plot — keep them mutually exclusive.
        if on and self.rdc_check.isChecked():
            self.rdc_check.blockSignals(True)
            self.rdc_check.setChecked(False)
            self.rdc_check.blockSignals(False)

    def _on_rdc_toggled(self, on: bool) -> None:
        if on and self.ct_check.isChecked():
            self.ct_check.blockSignals(True)
            self.ct_check.setChecked(False)
            self.ct_check.blockSignals(False)
        self._emit()

    def charge_transfer(self) -> bool:
        return self.ct_check.isChecked()

    def reciprocal_derivative(self) -> bool:
        return self.rdc_check.isChecked()

    def deriv_overlays(self) -> set:
        s = set()
        if self.dvdt_check.isChecked():
            s.add("dvdt")
        if self.recip_check.isChecked():
            s.add("recip")
        return s

    def _on_filter_toggled(self, on: bool) -> None:
        self.filter_win.setEnabled(bool(on))
        self._emit()

    def filter_spikes(self) -> bool:
        return self.filter_check.isChecked()

    def filter_window_us(self) -> float:
        return float(self.filter_win.value())

    def prefs(self) -> dict:
        return {"x_auto": self.x_auto.isChecked(),
                "x_min": self.x_min.value(), "x_max": self.x_max.value(),
                "y_auto": self.y_auto.isChecked(),
                "y_min": self.y_min.value(), "y_max": self.y_max.value(),
                "ry_auto": self.ry_auto.isChecked(),
                "ry_min": self.ry_min.value(), "ry_max": self.ry_max.value(),
                "density": self.density(),
                "charge_transfer": self.charge_transfer(),
                "reciprocal_derivative": self.reciprocal_derivative(),
                "dvdt": self.dvdt_check.isChecked(),
                "recip": self.recip_check.isChecked(),
                "filter_spikes": self.filter_spikes(),
                "filter_window": self.filter_window_us()}

    def restore(self, p: dict) -> None:
        if not isinstance(p, dict):
            return
        try:
            self.x_auto.setChecked(bool(p.get("x_auto", True)))
            self.x_min.setValue(float(p.get("x_min", 0.0)))
            self.x_max.setValue(float(p.get("x_max", 0.0)))
            self.y_auto.setChecked(bool(p.get("y_auto", True)))
            self.y_min.setValue(float(p.get("y_min", 0.0)))
            self.y_max.setValue(float(p.get("y_max", 0.0)))
            self.ry_auto.setChecked(bool(p.get("ry_auto", True)))
            self.ry_min.setValue(float(p.get("ry_min", 0.0)))
            self.ry_max.setValue(float(p.get("ry_max", 0.0)))
            self.unit_combo.setCurrentIndex(1 if p.get("density") else 0)
            self.ct_check.setChecked(bool(p.get("charge_transfer", False)))
            self.rdc_check.setChecked(bool(p.get("reciprocal_derivative", False)))
            self.dvdt_check.setChecked(bool(p.get("dvdt", False)))
            self.recip_check.setChecked(bool(p.get("recip", False)))
            self.filter_win.setValue(float(p.get("filter_window", 4.0)))
            self.filter_check.setChecked(bool(p.get("filter_spikes", False)))
            self.filter_win.setEnabled(self.filter_check.isChecked())
        except Exception:
            pass


def _despike_capture(cap, win_us: float):
    """Return a shallow copy of ``cap`` with every displayed trace median-
    filtered (metrics._despike) to reject the periodic switching spikes /
    ringing (operator POLARIS "Filter spikes").  DISPLAY-ONLY — the stored
    capture / saved .npz is untouched; the plot markers (re-derived from the
    trace arrays by compute_metric_markers) then land on the settled trace
    instead of a spike.  The metric TABLE keeps the stored values."""
    import copy as _copy
    from stimtest import metrics as _m
    t = getattr(cap, "time_us", None)
    out = _copy.copy(cap)
    for _attr in ("v_mon_v", "i_mon_ua", "e_ret_v", "e_act_v"):
        arr = getattr(cap, _attr, None)
        if arr is not None and getattr(arr, "size", 0) >= 5:
            try:
                setattr(out, _attr, _m._despike(arr, t, win_us=float(win_us)))
            except Exception:
                pass
    return out


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
        self._pico: Dict[str, object] = {}        # path-string -> PicoScopeRecording
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
        self.open_file_btn = QtWidgets.QPushButton("Open…")
        self.open_folder_btn = QtWidgets.QPushButton("Open folder…")
        # Remove the selected file/folder from the view (operator: "Allow for
        # add or remove files from view").  Open ADDS without clearing, so the
        # tree accumulates files (Gamry Echem Analyst-style); Remove (button,
        # right-click, or Delete key) drops one back out.
        self.remove_btn = QtWidgets.QPushButton("Remove")
        self.remove_btn.setToolTip(
            "Remove the selected file or folder from the view (Delete key).")
        btn_row.addWidget(self.open_file_btn)
        btn_row.addWidget(self.open_folder_btn)
        btn_row.addWidget(self.remove_btn)
        left_layout.addLayout(btn_row)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Session / Run / Capture", "Info"])
        self.tree.setColumnWidth(0, 320)
        self.tree.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._on_tree_context_menu)
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
        # Hidden until a session/run overlay is selected (set per node kind
        # in _on_tree_item).
        self.trace_toggles.setVisible(False)
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
        # Per-trace line-style + color picker (operator: "Allow the choice
        # of choosing the plot line style and color after opening").  Opens
        # a dialog listing every trace currently on the plot.  Overrides are
        # stored by trace label and re-applied on every render in
        # _finish_render, so they survive capture/overlay re-renders.
        self._trace_styles: Dict[str, dict] = {}
        self.style_btn = QtWidgets.QPushButton("Styles…")
        self.style_btn.setToolTip(
            "Choose the line style and color of each trace on the plot.")
        self.style_btn.clicked.connect(self._open_style_dialog)
        top_row.addWidget(self.style_btn, stretch=0)
        top_row.addWidget(self.grid_toggle, stretch=0)
        top_row_w = QtWidgets.QWidget(); top_row_w.setLayout(top_row)
        pl.addWidget(top_row_w)
        # Axis-range + current/density controls (operator: "allow the user
        # to set the axis range" + current-vs-density toggle, default
        # current).  Re-renders the current view on any change.
        self.view_bar = _PlotViewBar(self)
        self.view_bar.changed.connect(self._on_view_changed)
        pl.addWidget(self.view_bar)
        # PicoScope per-channel role selector — hidden unless a PicoScope
        # CSV node is selected (raw display by default; assign roles +
        # "Compute metrics" to overlay V_a / V_d / E_pol).
        self.pico_role_bar = _PicoRoleBar(self)
        self.pico_role_bar.setVisible(False)
        self.pico_role_bar.changed.connect(self._on_pico_roles_changed)
        pl.addWidget(self.pico_role_bar)
        # Plot region: channel COLUMN on the LEFT, canvas on the right
        # (Gamry Echem Analyst-style control panel beside the plot).  The
        # trace toggles are shown ONLY for the multi-channel overlay
        # (session / run) view — hidden for single captures and PicoScope
        # data, where they don't apply (operator: "the channel toggle
        # messed up my viewing of the CSV").
        mid = QtWidgets.QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(4)
        mid.addWidget(self.trace_toggles, 0)
        mid.addWidget(self.canvas, 1)
        pl.addLayout(mid, 1)

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

        # File-info table (operator: "Have another table for file info:
        # notebook, subject, total runs, total captures, created").  Separate
        # from the experiment Parameters table.
        self.file_table = QtWidgets.QTableWidget(0, 2)
        self.file_table.setHorizontalHeaderLabels(["File", "Value"])
        self.file_table.horizontalHeader().setStretchLastSection(True)
        self.file_table.verticalHeader().setVisible(False)
        self.file_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

        # Order: File → Parameter → Metric (operator request).
        ipl.addWidget(self.file_table)
        ipl.addWidget(self.param_table)
        # Metric column: an edit bar (response-class override + hand-edit +
        # save) above the metric table (operator: "save changes to the metrics
        # when adjusting the values … set the channel/combo as Good/Broken/
        # Open").  The bar is hidden until an experiment capture/run is
        # selected (not for PicoScope / folder / session-metadata nodes).
        self._metric_edit_enabled = False
        self._dirty_paths: set = set()   # .npz paths with unsaved metric edits
        # Snapshot of each capture's metrics taken the FIRST time it's adjusted
        # (class override or hand-edit), keyed by id(cap), so the metric table
        # can show the ORIGINAL value alongside the new one (operator: "when
        # adjusting values, keep the original values alongside the new one").
        self._original_metrics: dict = {}
        _metric_col = QtWidgets.QWidget()
        _mcl = QtWidgets.QVBoxLayout(_metric_col)
        _mcl.setContentsMargins(0, 0, 0, 0)
        _mcl.setSpacing(2)
        self.metric_edit_bar = _MetricEditBar(self)
        self.metric_edit_bar.classChosen.connect(self._on_response_class_chosen)
        self.metric_edit_bar.editToggled.connect(self._on_metric_edit_toggled)
        self.metric_edit_bar.saveRequested.connect(self._on_save_metric_changes)
        self.metric_edit_bar.setVisible(False)
        _mcl.addWidget(self.metric_edit_bar)
        _mcl.addWidget(self.metric_table)
        ipl.addWidget(_metric_col)
        right_split.addWidget(info_panel)

        right_split.setStretchFactor(0, 4)
        right_split.setStretchFactor(1, 1)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)

        # ---- Wiring --------------------------------------------------
        self.open_file_btn.clicked.connect(self.on_open_file)
        self.open_folder_btn.clicked.connect(self.on_open_folder)
        self.remove_btn.clicked.connect(self._remove_selected_item)
        self.tree.currentItemChanged.connect(self._on_tree_item)
        # Delete key removes the selected file/folder from the view.
        _del = QtGui.QShortcut(QtGui.QKeySequence(
            QtCore.Qt.Key.Key_Delete), self.tree)
        _del.setContext(QtCore.Qt.ShortcutContext.WidgetWithChildrenShortcut)
        _del.activated.connect(self._remove_selected_item)

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
    def _on_view_changed(self) -> None:
        """Axis-range or current/density control changed — re-render the
        current view.  Density is read by the ``_show_*`` methods at render
        time; axis-range overrides are applied in :meth:`_finish_render`.
        Mirrors :meth:`_on_grid_toggled`'s re-render dispatch."""
        if self._overlay_captures_by_channel:
            try:
                self._refresh_overlay()
                return
            except Exception:
                pass
        selected = (self.tree.currentItem()
                    if hasattr(self, "tree") else None)
        if selected is not None:
            try:
                self._on_tree_item(selected, None)
            except Exception:
                pass

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
        a_open = m_file.addAction("&Open…")
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
            self, "Open", "",
            "Sessions & scope data (*.npz *.csv *.tsv *.xls *.xlsx *.xlsm);;"
            "PULSAR session (*.npz);;"
            "Scope data — CSV / TSV / Excel "
            "(*.csv *.tsv *.xls *.xlsx *.xlsm);;"
            "All files (*)")
        if path:
            self.load_session_file(Path(path))

    @QtCore.pyqtSlot()
    def on_open_folder(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "Open folder")
        if path:
            # Additive — accumulate folders/files in the view (Gamry-style).
            self.load_folder(Path(path), clear=False)

    def load_folder(self, folder: Path, *, clear: bool = True) -> None:
        """Index every ``.npz`` file in ``folder`` (one tree branch each).

        ``clear=True`` (default) replaces the view — used by the embedded
        Results tab, which re-points at the save directory.  ``clear=False``
        ADDS the folder as another branch so the standalone POLARIS can
        accumulate multiple folders / files (operator: "Allow for add or
        remove files from view").
        """
        # Stash the resolved folder path for ``current_prefs`` so a
        # re-launch can restore the same context. Stored even when
        # the folder turns out to be empty — opening it once still
        # signals user intent.
        try:
            self._last_open_path = str(Path(folder).resolve())
        except Exception:
            pass
        if clear:
            self.tree.clear()
            self._sessions.clear()
            self._pico.clear()
        else:
            # Additive open: if this folder is already a branch, just
            # re-select it instead of duplicating.
            existing = self._find_top_level_item_for_path(str(folder))
            if existing is not None:
                self.tree.setCurrentItem(existing)
                self.status_message.emit(f"{folder.name} already open")
                return
        npzs = sorted(folder.glob("*.npz"))
        # Scope-data files in any supported format (CSV / TSV / Excel) —
        # but ONLY genuine external captures, not PULSAR's own session
        # EXPORTS (operator: "only show what is opened/imported").  PULSAR
        # writes `<stem>.npz` + `<stem>.xlsx` (+ .tif/.txt) per session; the
        # .xlsx is a DERIVED export, not a scope capture, so skip any scope
        # file whose stem matches a sibling .npz, plus any .xlsx detected as
        # a PULSAR export by its sheet structure.
        _npz_stems = {p.stem for p in npzs}
        picos = sorted(
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in PICO_EXTENSIONS
            and p.stem not in _npz_stems
            and not is_pulsar_session_xlsx(p))
        if not npzs and not picos:
            # Empty folder: report via the status bar instead of a
            # modal popup. ``ResultsTab.refresh`` calls ``load_folder``
            # whenever the save directory is repointed (or just
            # selected, on a fresh install with no saved sessions),
            # and a blocking dialog there made the GUI feel broken.
            self.status_message.emit(f"No .npz / scope-data files in {folder}")
            return
        root = QtWidgets.QTreeWidgetItem(self.tree, [folder.name, ""])
        root.setData(0, ROLE_KIND, KIND_FOLDER)
        root.setData(0, ROLE_PATH, str(folder))
        for p in npzs:
            self._add_session_to_tree(p, parent=root)
        for p in picos:              # scope captures (CSV / TSV / Excel)
            self._add_pico_to_tree(p, parent=root)
        root.setExpanded(True)
        n = len(npzs) + len(picos)
        self.status_message.emit(f"Indexed {n} file(s) in {folder}")

    def load_session_file(self, path: Path) -> None:
        suffix = path.suffix.lower()
        if suffix != ".npz" and suffix not in PICO_EXTENSIONS:
            QtWidgets.QMessageBox.warning(
                self, "Unsupported",
                "POLARIS opens PULSAR .npz sessions and scope data "
                "(.csv / .tsv / .xls / .xlsx).")
            return
        # A PULSAR session .xlsx is an EXPORT of a .npz, not a scope
        # capture — opening it on the PicoScope path would misparse the
        # metadata sheets.  Redirect to the sibling .npz if it's there.
        if suffix in (".xlsx", ".xlsm") and is_pulsar_session_xlsx(path):
            sib = path.with_suffix(".npz")
            if sib.exists():
                self.status_message.emit(
                    f"{path.name} is a PULSAR export — opening {sib.name}")
                self.load_session_file(sib)
            else:
                QtWidgets.QMessageBox.information(
                    self, "PULSAR export",
                    f"{path.name} is a PULSAR session export (.xlsx), not a "
                    f"scope capture.\n\nOpen the matching .npz session "
                    f"instead.")
            return
        try:
            self._last_open_path = str(Path(path).resolve())
        except Exception:
            pass
        # Dedupe — opening an already-loaded file just re-selects it
        # (Open ADDS, so without this a repeat open would duplicate the
        # branch).
        existing = self._find_top_level_item_for_path(str(path))
        if existing is not None:
            self.tree.setCurrentItem(existing)
            self.status_message.emit(f"{path.name} already open")
            return
        if suffix in PICO_EXTENSIONS:
            self._add_pico_to_tree(path, parent=self.tree.invisibleRootItem(),
                                   select=True)
        else:
            self._add_session_to_tree(path,
                                      parent=self.tree.invisibleRootItem(),
                                      expand=True, eager=True)
        self.status_message.emit(f"Loaded {path.name}")

    # -----------------------------------------------------------------
    # Add / remove files from the view (operator: "Allow for add or
    # remove files from view").
    # -----------------------------------------------------------------
    def _find_top_level_item_for_path(self, path: str):
        """Return the top-level tree item whose ROLE_PATH matches ``path``
        (a session, PicoScope, or folder root), or None."""
        try:
            want = str(Path(path).resolve())
        except Exception:
            want = str(path)
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            it = root.child(i)
            p = it.data(0, ROLE_PATH)
            if not p:
                continue
            try:
                same = str(Path(p).resolve()) == want
            except Exception:
                same = str(p) == str(path)
            if same:
                return it
        return None

    def _on_tree_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        menu = QtWidgets.QMenu(self.tree)
        act_remove = menu.addAction("Remove from view")
        act_remove.setEnabled(item is not None)
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_remove and item is not None:
            self._remove_tree_item(item)

    def _remove_selected_item(self) -> None:
        self._remove_tree_item(self.tree.currentItem())

    def _remove_tree_item(self, item) -> None:
        """Remove ``item``'s owning top-level file/folder branch from the
        view and drop its cached data.  Removing a Run/Capture removes the
        whole owning session (the unit the user opened)."""
        if item is None:
            return
        # Walk up to the top-level branch (folder root, session, or pico).
        top = item
        while top.parent() is not None:
            top = top.parent()
        # Drop cached payloads for the branch + any descendants.
        def _drop(node):
            p = node.data(0, ROLE_PATH)
            if p:
                self._sessions.pop(p, None)
                self._pico.pop(p, None)
            for i in range(node.childCount()):
                _drop(node.child(i))
        _drop(top)
        label = top.text(0)
        idx = self.tree.indexOfTopLevelItem(top)
        if idx >= 0:
            self.tree.takeTopLevelItem(idx)
        # Clear the plot/tables if nothing is selected anymore.
        if self.tree.currentItem() is None:
            self.figure.clear()
            self._finish_render()
            self._set_metric_table([])
            self._set_param_table([])
            self._set_file_table([])
            if hasattr(self, "trace_toggles"):
                self.trace_toggles.setVisible(False)
            if hasattr(self, "pico_role_bar"):
                self.pico_role_bar.setVisible(False)
        self.status_message.emit(f"Removed {label} from view")

    def _add_pico_to_tree(self, path: Path, *, parent, select: bool = False):
        """Add a leaf tree node for a scope-data capture (CSV/TSV/Excel)."""
        item = QtWidgets.QTreeWidgetItem(
            parent, [path.name, f"Scope data ({path.suffix.lstrip('.').upper()})"])
        item.setData(0, ROLE_KIND, KIND_PICO)
        item.setData(0, ROLE_PATH, str(path))
        if select:
            self.tree.setCurrentItem(item)
        return item

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
                # 1-BASED display number (operator: "stop counting start at
                # 0") — cap.index stays the 0-based array index everywhere it
                # indexes data; only the human label adds 1.
                cap_label = f"#{cap.index + 1:03d}  {amp:+.1f} µA"
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
        # The PicoScope role selector is only relevant for a PicoScope node.
        if hasattr(self, "pico_role_bar"):
            self.pico_role_bar.setVisible(kind == KIND_PICO)
        # The channel/waveform toggle column applies ONLY to the
        # multi-channel OVERLAY (session / run).  Hide it for single
        # captures and PicoScope data (operator: "the channel toggle messed
        # up my viewing of the CSV … only have such toggles for multiple
        # sheets in an Excel file").
        if hasattr(self, "trace_toggles"):
            self.trace_toggles.setVisible(kind in (KIND_SESSION, KIND_RUN))
        if kind == KIND_SESSION:
            # Lazy-load the session into the tree if not done
            if current.childCount() == 1 and current.child(0).text(0) == "…loading":
                self._load_session_into_tree(current)
            self._show_session_metadata(current)
        elif kind == KIND_RUN:
            self._show_run_overlay(current)
        elif kind == KIND_CAPTURE:
            self._show_capture(current)
        elif kind == KIND_PICO:
            self._show_picoscope(current)
        elif kind == KIND_FOLDER:
            self.figure.clear()
            self._finish_render()
            self._set_metric_table([])
            self._set_param_table([])
            self._set_file_table([("Folder", current.data(0, ROLE_PATH))])
        # Always refresh the channel-map view so it reflects the
        # session that owns the current selection. ``_current_session``
        # walks the tree item up to its session root.
        self.map_panel.set_session(self._current_session())
        # Show/hide the metric-edit bar (response-class override + save) for
        # this node + sync its Response combo to the current class.
        self._update_metric_edit_bar()

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
        # Optional median DESPIKE of the DISPLAYED traces (operator "Filter
        # spikes") — a shallow copy so the stored capture + metric table are
        # untouched; the plot + its re-derived markers use the filtered arrays.
        cap_plot = cap
        if self.view_bar.filter_spikes():
            cap_plot = _despike_capture(cap, self.view_bar.filter_window_us())
        if self.view_bar.charge_transfer():
            # Harris 2019 capacitive/Faradaic decomposition (dE/dt) view — the
            # electrode surface area (for C_dl) comes from the run.
            plot_charge_transfer(
                cap_plot, area_um2=getattr(run, "surface_area_um2", None),
                fig=self.figure, show_grid=self._show_grid)
        elif self.view_bar.reciprocal_derivative():
            # Musa 2010 reciprocal-derivative (dt/dE vs E) view.  Water-window
            # limits (safe charge-injection window) + reference label come from
            # the session's coating props, drawn as vertical reference lines.
            _coat = (session.test.extras or {}).get("coating_props") or {}
            plot_reciprocal_derivative(
                cap_plot,
                cathodic_limit_v=_coat.get("cathodic_limit_v"),
                anodic_limit_v=_coat.get("anodic_limit_v"),
                reference_label=getattr(session.test, "reference_electrode_label",
                                        None),
                fig=self.figure, show_grid=self._show_grid)
        else:
            plot_capture(cap_plot, run, session, fig=self.figure,
                         show_grid=self._show_grid,
                         density=self.view_bar.density(),
                         deriv_overlays=self.view_bar.deriv_overlays(),
                         potential_axis=True, return_axis=True)
        self._finish_render()
        self._set_metric_table(_capture_metric_rows(cap, run),
                               originals=self._metric_originals(cap, run))
        self._set_param_table(_session_param_rows(session, run, cap))
        self._set_file_table(_session_file_rows(session))

    def _show_picoscope(self, item: QtWidgets.QTreeWidgetItem) -> None:
        """Render a PicoScope CSV's raw channels (role-free)."""
        path = item.data(0, ROLE_PATH)
        rec = self._pico.get(path)
        if rec is None:
            try:
                rec = load_picoscope(Path(path))
            except Exception as e:
                self.figure.clear()
                ax = self.figure.add_subplot(111)
                ax.text(0.5, 0.5, f"Could not load\n{Path(path).name}\n\n{e}",
                        ha="center", va="center", wrap=True)
                ax.axis("off")
                self._finish_render()
                self._set_metric_table([])
                self._set_param_table([])
                self._set_file_table([("File", Path(path).name),
                                      ("Error", str(e))])
                return
            self._pico[path] = rec
        # Populate the role-selector combos for THIS file's channels (only
        # rebuild when the channel set changes, to keep the user's picks).
        names = list(rec.channels)
        if getattr(self, "_pico_role_channels", None) != names:
            self.pico_role_bar.set_channels(names)
            self._pico_role_channels = names
        self.pico_role_bar.setVisible(True)
        # FILE-info table: the CSV path + per-channel source units + range.
        frows = [("File", Path(path).name),
                 ("Source", "PicoScope CSV"),
                 ("Samples", str(rec.time_us.size)),
                 ("Time span [µs]",
                  f"{float(rec.time_us.min()):.1f} … {float(rec.time_us.max()):.1f}"
                  if rec.time_us.size else "—")]
        for nm, arr in rec.channels.items():
            u = rec.source_units.get(nm, "V")
            rng = (f"[{float(np.nanmin(arr)):+.3f}, {float(np.nanmax(arr)):+.3f}] V"
                   if arr.size else "—")
            frows.append((f"{nm} ({u})", rng))
        self._set_file_table(frows)
        # METRICS path: roles assigned + "Compute metrics" on + a V_mon
        # channel → infer the pattern, compute metrics, render the full
        # capture plot.  Otherwise show the raw channels.
        if (self.pico_role_bar.metrics_enabled()
                and self.pico_role_bar.has_vmon()):
            try:
                from ..persistence import picoscope_to_session
                sess = picoscope_to_session(
                    rec, self.pico_role_bar.roles(),
                    current_scale_mv_per_ua=self.pico_role_bar.current_scale())
                run = sess.runs[0]
                cap = run.captures[0]
                cap_plot = (_despike_capture(cap, self.view_bar.filter_window_us())
                            if self.view_bar.filter_spikes() else cap)
                plot_capture(cap_plot, run, sess, fig=self.figure,
                             show_grid=self._show_grid,
                             density=self.view_bar.density(),
                             potential_axis=True, return_axis=True)
                self._finish_render()
                self._set_metric_table(_capture_metric_rows(cap, run))
                self._set_param_table(_session_param_rows(sess, run, cap))
                return
            except Exception as e:
                # Fall back to the raw view on any inference/metric error.
                self.status_message.emit(f"PicoScope metrics failed: {e}")
        plot_picoscope(rec, fig=self.figure, show_grid=self._show_grid)
        self._finish_render()
        self._set_metric_table([
            ("Assign channel roles + tick", "Compute metrics, above")])
        self._set_param_table([])

    def _on_pico_roles_changed(self) -> None:
        """Re-render the current PicoScope node when the role map / scale /
        metrics toggle changes."""
        item = self.tree.currentItem()
        if item is not None and item.data(0, ROLE_KIND) == KIND_PICO:
            self._show_picoscope(item)

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
        # Metric table for a channel/combo: the run summary (incl.
        # ACCUMULATED charge — operator: "for each channel/combo tab,
        # include accumulated charge") followed by the WAVEFORM metrics of
        # the representative (max-amplitude good) capture — NOT just the file
        # setup + max Q_inj (operator: "Metric table is all wrong … no
        # waveform metrics besides maximum Q_inj").
        rep = self._representative_capture(run)
        # Cumulative Q with the auto-scaled unit in the LABEL column (operator:
        # "have the units in the metric column and not value column").
        _cq_nc = _run_accumulated_charge_nc(run)
        if math.isfinite(_cq_nc):
            from .widgets import _split_cumulative_charge
            _cq_v, _cq_u = _split_cumulative_charge(_cq_nc)
            _cq_label, _cq_val = f"Cumulative Q [{_cq_u}]", _cq_v
        else:
            _cq_label, _cq_val = "Cumulative Q", "—"
        rows = [
            ("Channel/combo", run.configuration.display_name()),
            ("Captures", str(len(run.captures))),
            ("Max Q_inj [mC/cm²]", _fmt_or_dash(run.max_q_inj, ".3f")),
            ("Cumulative N_pulse", _fmt_pulses_or_dash(
                _run_cumulative_n_pulses(run))),
            (_cq_label, _cq_val),
            ("Time to complete", _fmt_duration_s(run.duration_s)),
        ]
        if rep is not None:
            rows.append(("———  representative capture  ———", ""))
            rows.extend(_capture_metric_rows(rep, run))
        self._set_metric_table(
            rows, originals=(self._metric_originals(rep, run)
                             if rep is not None else None))
        self._set_param_table(_session_param_rows(session, run, rep))
        self._set_file_table(_session_file_rows(session))

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
        areas: Dict[str, float] = {}
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
            areas[key] = getattr(run, "surface_area_um2", float("nan"))
        self._overlay_captures_by_channel = captures
        self._overlay_areas_by_key = areas
        self._overlay_reference_label = getattr(
            getattr(session, "test", None),
            "reference_electrode_label", "Ag|AgCl")
        self._overlay_return_label = getattr(
            getattr(session, "test", None),
            "counter_electrode_label", "Pt")
        # No electrodes (Plexon Test Board) → keep the axis plain "Voltage [V]".
        self._overlay_reference_aware = bool(
            ((getattr(getattr(session, "test", None), "extras", None) or {})
             .get("setup_snapshot") or {}).get("has_electrodes", True))
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
        areas: Dict[str, float] = {}
        _area = getattr(run, "surface_area_um2", float("nan"))
        for cap in run.captures:
            try:
                amp = cap.pattern.excitation_phase.amplitude_ua
                amp_label = f"{amp:+.1f} µA"   # always one decimal (operator)
            except Exception:
                amp_label = ""
            # 1-BASED entry key/label (operator: "stop counting start at 0")
            # — the key is also the legend prefix in plot_overlay, so this
            # makes the channel list, checkbox labels, AND legend 1-based.
            key = f"#{cap.index + 1:03d}"
            labels[key] = (f"{key} {amp_label}".strip()
                           if amp_label else key)
            captures[key] = cap
            areas[key] = _area
        self._overlay_captures_by_channel = captures
        self._overlay_areas_by_key = areas
        self._overlay_reference_label = getattr(
            getattr(session, "test", None),
            "reference_electrode_label", "Ag|AgCl")
        self._overlay_return_label = getattr(
            getattr(session, "test", None),
            "counter_electrode_label", "Pt")
        # No electrodes (Plexon Test Board) → keep the axis plain "Voltage [V]".
        self._overlay_reference_aware = bool(
            ((getattr(getattr(session, "test", None), "extras", None) or {})
             .get("setup_snapshot") or {}).get("has_electrodes", True))
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
                density=self.view_bar.density(),
                areas_by_key=getattr(self, "_overlay_areas_by_key", None),
                potential_axis=getattr(self, "_overlay_reference_aware", True),
                reference_label=getattr(
                    self, "_overlay_reference_label", "Ag|AgCl"),
                return_axis=getattr(self, "_overlay_reference_aware", True),
                return_label=getattr(
                    self, "_overlay_return_label", "Pt"),
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
        self._finish_render()

    def _show_session_metadata(self, item: QtWidgets.QTreeWidgetItem) -> None:
        path = item.data(0, ROLE_PATH)
        session = self._sessions.get(path)
        if session is None:
            try:
                meta = load_session_meta(Path(path))
            except Exception as e:
                QtWidgets.QMessageBox.critical(self, "Load failed", str(e))
                return
            # Metadata-only (not yet loaded): no waveform metrics; file info
            # in the File table, experiment in Parameters.
            self._set_metric_table([
                ("Select a channel/combo or capture", "for waveform metrics")])
            self._set_param_table([
                ("Experiment", str(meta.get("test", {}).get("experiment", "")))])
            self._set_file_table([
                ("Notebook", str(meta.get("notebook", ""))),
                ("Subject", str(meta.get("subject", ""))),
                ("Total runs", str(len(meta.get("runs", [])))),
                ("Created", str(meta.get("created_at", ""))),
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
        # Session level → no single-capture waveform metrics; file metadata
        # lives in the File table now (operator: separate file-info table).
        self._set_metric_table([
            ("Select a channel/combo or capture", "for waveform metrics")])
        self._set_param_table(_session_param_rows(session, None, None))
        self._set_file_table(_session_file_rows(session))

    # -----------------------------------------------------------------
    # Misc helpers (info-panel rendering)
    # -----------------------------------------------------------------
    def _finish_render(self) -> None:
        """Make the current figure's legend entries CLICKABLE to toggle their
        traces (operator: "Let the legend have checkbox per entry to toggle
        for viewing on the plot"), then redraw.  Used in place of a bare
        ``canvas.draw_idle()`` after every plot.  Clicking a legend entry
        hides/shows the matching trace(s) and dims the entry."""
        fig = self.figure
        # Apply user axis-range overrides (operator: "allow the user to set
        # the axis range").  X is shared across all axes; the Y override
        # applies to the primary (left / voltage) axis.  None = Auto = leave
        # the plotter's MATLAB-faithful range untouched.
        vb = getattr(self, "view_bar", None)
        if vb is not None and fig.axes:
            xr = vb.xrange()
            if xr is not None:
                for ax in fig.axes:
                    ax.set_xlim(*xr)
            yr = vb.yrange()
            if yr is not None:
                fig.axes[0].set_ylim(*yr)
            # Right (current / density) axis range — the twin axes, found
            # by its right-side y-label position (robust to inset axes).
            yr_r = vb.yrange_right()
            if yr_r is not None:
                for ax in fig.axes[1:]:
                    try:
                        if ax.yaxis.get_label_position() == "right":
                            ax.set_ylim(*yr_r)
                            break
                    except Exception:
                        pass
        # Apply per-trace style overrides (operator: line style + color
        # picker).  Match by label against the data lines on every axis.
        styles = getattr(self, "_trace_styles", {})
        if styles:
            for ax in fig.axes:
                for ln in ax.get_lines():
                    st = styles.get(ln.get_label())
                    if not st:
                        continue
                    if st.get("color"):
                        ln.set_color(st["color"])
                    if st.get("linestyle"):
                        ln.set_linestyle(st["linestyle"])
        legend = None
        if getattr(fig, "legends", None):
            legend = fig.legends[0]
        else:
            for ax in fig.axes:
                lg = ax.get_legend()
                if lg is not None:
                    legend = lg
                    break
        self._legend_map = {}
        if legend is not None:
            lines_by_label: Dict[str, list] = {}
            for ax in fig.axes:
                for ln in ax.get_lines():
                    lbl = ln.get_label()
                    if lbl and not lbl.startswith("_"):
                        lines_by_label.setdefault(lbl, []).append(ln)
            for legline, legtext in zip(legend.get_lines(),
                                        legend.get_texts()):
                origs = lines_by_label.get(legtext.get_text())
                if not origs:
                    continue
                # Sync the legend proxy to the (possibly restyled) data line
                # so the swatch reflects the chosen color / dash.
                legline.set_color(origs[0].get_color())
                legline.set_linestyle(origs[0].get_linestyle())
                legline.set_picker(6)
                legtext.set_picker(6)
                self._legend_map[legline] = origs
                self._legend_map[legtext] = origs
            if not getattr(self, "_legend_pick_connected", False):
                self.canvas.mpl_connect("pick_event", self._on_legend_pick)
                self._legend_pick_connected = True
        self.canvas.draw_idle()

    def _on_legend_pick(self, event) -> None:
        origs = getattr(self, "_legend_map", {}).get(event.artist)
        if not origs:
            return
        visible = not origs[0].get_visible()
        for ln in origs:
            ln.set_visible(visible)
        # Dim the legend entry (both its proxy line + text) when hidden.
        for art, mapped in self._legend_map.items():
            if mapped is origs:
                art.set_alpha(1.0 if visible else 0.3)
        self.canvas.draw_idle()

    # -- per-trace line style + color --------------------------------
    def set_trace_style(self, label: str, *, color: Optional[str] = None,
                        linestyle: Optional[str] = None,
                        rerender: bool = True) -> None:
        """Override a trace's color and/or line style by its legend label.

        Stored in ``self._trace_styles`` and re-applied on every render via
        :meth:`_finish_render`, so it survives capture / overlay switches.
        ``rerender`` re-applies immediately to the current figure."""
        st = self._trace_styles.setdefault(label, {})
        if color is not None:
            st["color"] = color
        if linestyle is not None:
            st["linestyle"] = linestyle
        if rerender:
            self._finish_render()

    def _current_trace_labels(self) -> List[str]:
        """Distinct legend labels of every data trace currently plotted."""
        seen: List[str] = []
        for ax in self.figure.axes:
            for ln in ax.get_lines():
                lbl = ln.get_label()
                if lbl and not lbl.startswith("_") and lbl not in seen:
                    seen.append(lbl)
        return seen

    def _open_style_dialog(self) -> None:
        """Dialog: pick each visible trace's line style + color (operator:
        "Allow the choice of choosing the plot line style and color after
        opening")."""
        labels = self._current_trace_labels()
        if not labels:
            QtWidgets.QMessageBox.information(
                self, "Trace styles",
                "No traces are plotted yet. Open a file and select a "
                "capture, channel/combo, or PicoScope node first.")
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Trace styles")
        form = QtWidgets.QGridLayout(dlg)
        form.addWidget(QtWidgets.QLabel("<b>Trace</b>"), 0, 0)
        form.addWidget(QtWidgets.QLabel("<b>Line style</b>"), 0, 1)
        form.addWidget(QtWidgets.QLabel("<b>Color</b>"), 0, 2)
        # Find the current color/style of each label from the live lines.
        cur = {}
        for ax in self.figure.axes:
            for ln in ax.get_lines():
                lbl = ln.get_label()
                if lbl in labels and lbl not in cur:
                    cur[lbl] = (ln.get_color(), ln.get_linestyle())
        rows = {}
        from matplotlib.colors import to_hex
        for i, lbl in enumerate(labels, start=1):
            form.addWidget(QtWidgets.QLabel(lbl), i, 0)
            combo = QtWidgets.QComboBox()
            for name, code in _LINESTYLE_CHOICES:
                combo.addItem(name, code)
            cur_ls = self._trace_styles.get(lbl, {}).get(
                "linestyle", cur.get(lbl, ("", "-"))[1])
            idx = combo.findData(cur_ls)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            form.addWidget(combo, i, 1)
            try:
                cur_col = to_hex(self._trace_styles.get(lbl, {}).get(
                    "color", cur.get(lbl, ("#1f77b4", ""))[0]))
            except Exception:
                cur_col = "#1f77b4"
            btn = QtWidgets.QPushButton(cur_col)
            btn.setStyleSheet(
                f"background-color:{cur_col}; color:#000;")

            def _pick(_=None, b=btn):
                c = QtWidgets.QColorDialog.getColor(
                    QtGui.QColor(b.text()), dlg, "Pick trace color")
                if c.isValid():
                    b.setText(c.name())
                    b.setStyleSheet(
                        f"background-color:{c.name()}; color:#000;")

            btn.clicked.connect(_pick)
            form.addWidget(btn, i, 2)
            rows[lbl] = (combo, btn)
        bb = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addWidget(bb, len(labels) + 1, 0, 1, 3)
        if dlg.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            for lbl, (combo, btn) in rows.items():
                self.set_trace_style(lbl, color=btn.text(),
                                     linestyle=combo.currentData(),
                                     rerender=False)
            self._finish_render()

    def _set_metric_table(self, rows: List[tuple], originals=None) -> None:
        # ``originals`` (dict: row-label → original value string) turns on a
        # third "Original" column that shows the pre-adjustment value alongside
        # the new one for any CHANGED row (operator: "when adjusting values,
        # keep the original values alongside the new one").  When absent the
        # table stays the historic 2 columns.
        _have_orig = bool(originals)
        self.metric_table.setColumnCount(3 if _have_orig else 2)
        self.metric_table.setHorizontalHeaderLabels(
            ["Metric", "Value", "Original"] if _have_orig
            else ["Metric", "Value"])
        self.metric_table.setRowCount(len(rows))
        # Whether the Value column is currently hand-editable (the edit bar's
        # "Edit values" checkbox).  Only rows tagged with a field name (a
        # 3-tuple) become editable, and only while the toggle is on.
        _editing = bool(getattr(self, "_metric_edit_enabled", False))
        _RO = (QtCore.Qt.ItemFlag.ItemIsEnabled
               | QtCore.Qt.ItemFlag.ItemIsSelectable)
        for i, row in enumerate(rows):
            k, v = row[0], row[1]
            field = row[2] if len(row) > 2 else None
            key_item = QtWidgets.QTableWidgetItem(str(k))
            key_item.setFlags(_RO)
            self.metric_table.setItem(i, 0, key_item)
            val_item = QtWidgets.QTableWidgetItem(str(v))
            if field:
                # Stash the CaptureMetrics field name so a hand-edit round-trips
                # (see ``_collect_metric_edits``).  Editable only when toggled.
                val_item.setData(QtCore.Qt.ItemDataRole.UserRole, field)
                flags = _RO
                if _editing:
                    flags |= QtCore.Qt.ItemFlag.ItemIsEditable
                val_item.setFlags(flags)
            else:
                val_item.setFlags(_RO)
            self.metric_table.setItem(i, 1, val_item)
            if _have_orig:
                # Original value — shown only when it differs from the new one
                # (an unchanged row leaves the cell blank).  Always read-only.
                ov = originals.get(str(k))
                cell = (str(ov) if (ov is not None and str(ov) != str(v))
                        else "")
                orig_item = QtWidgets.QTableWidgetItem(cell)
                orig_item.setFlags(_RO)
                try:                        # dim it (secondary reference)
                    orig_item.setForeground(self.palette().brush(
                        QtGui.QPalette.ColorRole.PlaceholderText))
                except Exception:
                    pass
                self.metric_table.setItem(i, 2, orig_item)
        self.metric_table.resizeColumnToContents(0)
        if _have_orig:
            self.metric_table.resizeColumnToContents(1)

    def _set_param_table(self, rows: List[tuple]) -> None:
        self.param_table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.param_table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(k)))
            self.param_table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(v)))
        self.param_table.resizeColumnToContents(0)

    def _set_file_table(self, rows: List[tuple]) -> None:
        self.file_table.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.file_table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(k)))
            self.file_table.setItem(i, 1, QtWidgets.QTableWidgetItem(str(v)))
        self.file_table.resizeColumnToContents(0)

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

    # -----------------------------------------------------------------
    # Metric editing — response-class override (Good/Broken/Open),
    # hand-edit metric values, save back to the .npz (operator request).
    # -----------------------------------------------------------------
    def _edit_target(self):
        """Resolve the current tree selection to a dict describing what the
        metric-edit bar acts on: ``session`` / ``run`` / ``path`` / ``cap_idx``
        (None for a channel/combo RUN node).  Returns None for any non-editable
        node (PicoScope / folder / session-metadata / nothing selected) — the
        class override + save apply to the CHANNEL/COMBO (all captures of the
        run), so a single-capture selection still targets its whole run."""
        item = self.tree.currentItem()
        if item is None:
            return None
        kind = item.data(0, ROLE_KIND)
        if kind not in (KIND_CAPTURE, KIND_RUN):
            return None
        path = item.data(0, ROLE_PATH)
        run_idx = item.data(0, ROLE_RUN)
        session = self._sessions.get(path)
        if session is None or run_idx is None or run_idx >= len(session.runs):
            return None
        return dict(session=session, run=session.runs[run_idx], path=path,
                    run_idx=run_idx, cap_idx=item.data(0, ROLE_CAP), kind=kind)

    def _run_area_um2(self, session, run) -> float:
        """Surface area (µm²) for a run's ACTIVE electrode — needed to recompute
        the (area-normalised) metrics.  Falls back to the first site / a neutral
        default so a recompute never divides by a missing area."""
        try:
            active = int(run.configuration.active)
            for s in session.test.array.sites:
                if int(getattr(s, "number", -1)) == active:
                    return float(getattr(s, "surface_area_um2", 0.0) or 0.0)
        except Exception:
            pass
        try:
            return float(session.test.array.sites[0].surface_area_um2)
        except Exception:
            return 5000.0

    def _ensure_original_metrics(self, cap) -> None:
        """Snapshot ``cap.metrics`` the FIRST time this capture is adjusted, so
        the metric table can show the original value alongside the new one
        (operator: "keep the original values alongside the new one").  Keyed by
        id(cap); the captures live in the loaded session so the id is stable."""
        import copy
        key = id(cap)
        if key not in self._original_metrics:
            try:
                self._original_metrics[key] = copy.deepcopy(cap.metrics)
            except Exception:
                pass

    def _metric_originals(self, cap, run):
        """Return {row-label: original value string} for a capture that has been
        adjusted (a snapshot exists), else None.  Built by re-running the row
        builder on the snapshot so the labels/formatting match the live rows
        exactly (so only genuinely-changed rows show an Original value)."""
        snap = self._original_metrics.get(id(cap))
        if snap is None:
            return None
        try:
            orig_rows = _capture_metric_rows(cap, run, m_override=snap)
        except Exception:
            return None
        return {str(r[0]): str(r[1]) for r in orig_rows}

    def _update_metric_edit_bar(self) -> None:
        """Show/hide the metric-edit bar for the current selection + sync the
        Response combo to the (representative) capture's current class."""
        bar = getattr(self, "metric_edit_bar", None)
        if bar is None:
            return
        tgt = self._edit_target()
        if tgt is None:
            bar.setVisible(False)
            return
        bar.setVisible(True)
        caps = tgt["run"].captures
        cls = "normal"
        if caps:
            _c = tgt["cap_idx"]
            cap = caps[_c] if (_c is not None and _c < len(caps)) else caps[0]
            cls = getattr(cap.metrics, "response_class", "normal") or "normal"
        bar.set_class(cls)

    @QtCore.pyqtSlot(str)
    def _on_response_class_chosen(self, cls: str) -> None:
        """Recompute EVERY capture of the selected channel/combo with the forced
        class, mark the session dirty, and re-render."""
        from ..metrics import recompute_capture_metrics
        tgt = self._edit_target()
        if tgt is None:
            return
        area = self._run_area_um2(tgt["session"], tgt["run"])
        for cap in tgt["run"].captures:
            self._ensure_original_metrics(cap)   # keep pre-override values
            try:
                recompute_capture_metrics(cap, area, class_override=cls)
            except Exception:
                pass
        self._dirty_paths.add(tgt["path"])
        self._on_tree_item(self.tree.currentItem(), None)   # re-render

    @QtCore.pyqtSlot(bool)
    def _on_metric_edit_toggled(self, on: bool) -> None:
        self._metric_edit_enabled = bool(on)
        self.metric_table.setEditTriggers(
            (QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
             | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked) if on
            else QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self._on_tree_item(self.tree.currentItem(), None)   # re-tag rows

    def _collect_metric_edits(self, cap) -> int:
        """Apply hand-edited Value cells (tagged with a CaptureMetrics field)
        to ``cap.metrics``.  Returns the number of fields changed."""
        self._ensure_original_metrics(cap)       # keep pre-edit values
        n = 0
        tbl = self.metric_table
        for r in range(tbl.rowCount()):
            it = tbl.item(r, 1)
            if it is None:
                continue
            field = it.data(QtCore.Qt.ItemDataRole.UserRole)
            if field not in _EDITABLE_METRIC_FIELDS:
                continue
            val = _parse_metric_value(it.text())
            if val is not None and hasattr(cap.metrics, field):
                setattr(cap.metrics, field, val)
                n += 1
        return n

    @QtCore.pyqtSlot()
    def _on_save_metric_changes(self) -> None:
        """Apply hand-edits to the displayed capture + save the session back to
        its source .npz (after a confirmation — it overwrites the file)."""
        from ..persistence import save_session_npz
        tgt = self._edit_target()
        if tgt is None:
            return
        session, path = tgt["session"], tgt["path"]
        caps = tgt["run"].captures
        if caps and self._metric_edit_enabled:
            _c = tgt["cap_idx"]
            cap = caps[_c] if (_c is not None and _c < len(caps)) else caps[0]
            self._collect_metric_edits(cap)
        resp = QtWidgets.QMessageBox.question(
            self, "Save changes",
            f"Overwrite\n{path}\nwith the edited metrics?\n\n(The raw waveforms "
            "are preserved — only the metrics + response class change.)")
        if resp != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            save_session_npz(session, path)
            self._dirty_paths.discard(path)
            try:
                self.status_message.emit(f"Saved metrics → {Path(path).name}")
            except Exception:
                pass
            self._on_tree_item(self.tree.currentItem(), None)
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "Save failed", str(e))

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
        self._finish_render()

    @QtCore.pyqtSlot()
    def _show_vd_overlay(self) -> None:
        s = self._current_session()
        if s is None:
            return
        plot_vd_vs_qinj(s, fig=self.figure,
                        show_grid=self._show_grid)
        self._finish_render()

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
        try:
            out["view_bar"] = self.view_bar.prefs()
        except Exception:
            pass
        try:
            out["pico_role_bar"] = self.pico_role_bar.prefs()
        except Exception:
            pass
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
        vb = p.get("view_bar")
        if isinstance(vb, dict):
            self.view_bar.restore(vb)
        prb = p.get("pico_role_bar")
        if isinstance(prb, dict):
            self.pico_role_bar.restore(prb)
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
              "_on_tree_context_menu", "_remove_selected_item",
              "_remove_tree_item", "_find_top_level_item_for_path",
              "_show_capture", "_show_run_overlay",
              "_show_session_metadata",
              # PicoScope CSV import (raw external-scope captures)
              "_add_pico_to_tree", "_show_picoscope",
              "_on_pico_roles_changed",
              "_set_metric_table", "_set_param_table", "_set_file_table",
              "_finish_render", "_on_legend_pick",
              "set_trace_style", "_current_trace_labels", "_open_style_dialog",
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
              "current_prefs", "restore_prefs",
              # Metric editing — response-class override + hand-edit + save.
              "_edit_target", "_run_area_um2", "_update_metric_edit_bar",
              "_on_response_class_chosen", "_on_metric_edit_toggled",
              "_collect_metric_edits", "_on_save_metric_changes",
              "_ensure_original_metrics", "_metric_originals"):
    setattr(ViewerPanel, _name, getattr(ViewerWindow, _name))
del _name


# ---------------------------------------------------------------------------
# Side-panel info builders
# ---------------------------------------------------------------------------
def _fmt_duration_s(t_s: float) -> str:
    """Human-readable elapsed duration (``"45s"`` / ``"1m 23s"`` /
    ``"1h 05m 03s"``) or ``"—"`` for NaN / negative.  Used for the
    per-channel/combo "Time to complete" metric row."""
    try:
        if t_s is None or not np.isfinite(t_s) or t_s < 0:
            return "—"
    except (TypeError, ValueError):
        return "—"
    t = int(round(float(t_s)))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


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


# Response-class display ↔ internal string (operator: Good/Broken/Open).
# MODULE-level (not a class attr) so the ViewerWindow→ViewerPanel method-copy
# loop's methods can reference them (gotcha #70b — class data attrs aren't
# transplanted).
_CLASS_DISPLAY = {"normal": "Good", "broken": "Broken",
                  "open": "Open", "capacitive": "Capacitive"}
_CLASS_FROM_DISPLAY = {v: k for k, v in _CLASS_DISPLAY.items()}
# Editable metric Value cells → CaptureMetrics field + parse unit-suffix.  The
# metric-row builder tags these rows with the field name (3-tuple) so a
# hand-edit round-trips to the right scalar field.
_EDITABLE_METRIC_FIELDS = (
    "driving_voltage_v", "effective_capacitance_nf",
    "rc_fit_resistance_kohm", "rc_fit_tau_us",
)


def _class_display(cls) -> str:
    return _CLASS_DISPLAY.get(cls or "normal", "Good")


def _parse_metric_value(text):
    """Parse the leading number out of a metric Value cell (e.g. ``35.65 nF``,
    ``1.403 V``, ``−0.80``) → float, ignoring the unit suffix.  Returns None
    when there's no parseable number."""
    import re
    s = str(text).strip().replace("−", "-")   # unicode minus → ASCII
    m = re.match(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", s)
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


class _MetricEditBar(QtWidgets.QWidget):
    """POLARIS bar to OVERRIDE a channel/combo's response class
    (Good / Broken / Open / Capacitive), hand-EDIT metric values, and SAVE the
    changes back to the .npz (operator: "Allow the user to save changes to the
    metrics when adjusting the values … setting the channel/combo as
    Good/Broken/Open — the appropriate metrics are to be computed as well").

    Signals: ``classChosen(str)`` (internal class string), ``editToggled(bool)``,
    ``saveRequested()``."""

    classChosen = QtCore.pyqtSignal(str)
    editToggled = QtCore.pyqtSignal(bool)
    saveRequested = QtCore.pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(6)
        lay.addWidget(QtWidgets.QLabel("Response:"))
        self.class_combo = QtWidgets.QComboBox()
        for disp in ("Good", "Broken", "Open", "Capacitive"):
            self.class_combo.addItem(disp)
        self.class_combo.setToolTip(
            "Set this channel/combo's response class.  The appropriate metrics "
            "are recomputed for ALL its captures:\n"
            "  • Good → access V/R + electrode polarization (no C_eff)\n"
            "  • Broken → parallel R‖C fit (R, C, τ)\n"
            "  • Open / Capacitive → effective capacitance C_eff.")
        self.class_combo.currentTextChanged.connect(self._on_class)
        lay.addWidget(self.class_combo)
        self.edit_chk = QtWidgets.QCheckBox("Edit values")
        self.edit_chk.setToolTip("Hand-edit the metric Value column "
                                 "(C_eff / R / τ / V_d); applied on Save.")
        self.edit_chk.toggled.connect(self.editToggled)
        lay.addWidget(self.edit_chk)
        lay.addStretch(1)
        self.save_btn = QtWidgets.QPushButton("Save to file")
        self.save_btn.setToolTip("Write the edited metrics back to the source "
                                 ".npz (overwrites it after a confirmation).")
        self.save_btn.clicked.connect(self.saveRequested)
        lay.addWidget(self.save_btn)
        self._guard = False

    def _on_class(self, disp):
        if not self._guard:
            self.classChosen.emit(_CLASS_FROM_DISPLAY.get(disp, "normal"))

    def set_class(self, cls):
        self._guard = True
        try:
            self.class_combo.setCurrentText(_class_display(cls))
        finally:
            self._guard = False


def _capture_metric_rows(cap: Capture, run: ChannelRun,
                         m_override=None) -> List[tuple]:
    # ``m_override`` lets the caller build rows from a SNAPSHOT of the metrics
    # (the pre-adjustment "original") instead of the live ``cap.metrics`` — used
    # to fill the metric table's Original column alongside the new values.
    m = m_override if m_override is not None else cap.metrics
    L = rich.plain_label
    # Units live in the FIRST column (the label), matching the live PULSAR
    # metric table (operator: "Have the units in the metrics table in the first
    # column").  ``_fmt_or_dash`` is called with no unit so the value cell holds
    # just the number (or an em-dash for NaN).
    rows = [
        # 1-BASED for the operator (operator: "stop counting start at 0").
        ("Capture #", cap.index + 1),
        ("Status", _status_text(cap)),
        ("Amplitude [µA]", _fmt_or_dash(
            cap.pattern.excitation_phase.amplitude_ua, "+.2f")),
        (f"{L('Q','ph')} [nC]",  _fmt_or_dash(m.charge_per_phase_nc, ".3f")),
        # Charge imbalance Q_net (operator: "add charge imbalance (nC)").
        (f"{L('Q','net')} [nC]", _fmt_or_dash(cap.pattern.net_charge_nc, "+.3f")),
        (f"{L('Q','inj')} [mC/cm²]", _fmt_or_dash(m.charge_injection_mc_per_cm2,
                                                  ".3f")),
        (f"{L('E','ip')} [V]",  _fmt_or_dash(m.interpulse_potential_v, ".3f")),
        (f"{L('C','d')} [mF/cm²]",   _fmt_or_dash(m.driving_capacitance_mf_per_cm2,
                                                  ".3f")),
    ]
    # Response class + capacitance / R‖C fit (editable in POLARIS — the Response
    # combo overrides the class + recomputes; these mirror the result).  Rows
    # tagged with a 3rd element (field name) are hand-editable Value cells.
    _cls = getattr(m, "response_class", "normal") or "normal"
    rows.append(("Response", _class_display(_cls)))
    # TOTAL driving voltage (V_mon, all electrodes) — call it out as "total"
    # when the per-electrode active/return driving voltage rows are also shown
    # (operator: distinguish total vs active/return driving voltage).
    _vd_total = f"{L('V','d')} total [V]" if m.return_driving_voltage_per_phase_v \
        else f"{L('V','d')} [V]"
    rows.append((_vd_total, _fmt_or_dash(m.driving_voltage_v, ".3f"),
                 "driving_voltage_v"))
    if _cls != "normal" or math.isfinite(
            getattr(m, "effective_capacitance_nf", float("nan"))):
        # broken → R‖C FIT capacitance, labelled bare ``C`` (operator: "for
        # broken, do not call it Ceff"); open / capacitive → ``C_eff``.
        _c_lbl = "C" if _cls == "broken" else "C_eff"
        rows.append((f"{_c_lbl} [nF]", _fmt_or_dash(m.effective_capacitance_nf,
                                                    ".3g"),
                     "effective_capacitance_nf"))
        if math.isfinite(getattr(m, "rc_fit_resistance_kohm", float("nan"))):
            rows.append(("R (R‖C) [kΩ]",
                         _fmt_or_dash(m.rc_fit_resistance_kohm, ".1f"),
                         "rc_fit_resistance_kohm"))
        if math.isfinite(getattr(m, "rc_fit_tau_us", float("nan"))):
            rows.append(("τ (R‖C) [µs]",
                         _fmt_or_dash(m.rc_fit_tau_us, ".1f"),
                         "rc_fit_tau_us"))
        # Per-phase bad-response values for the OTHER phases (operator:
        # "for broken and open channels, compute the same metrics for
        # other phases") — the scalars above ARE phase 1.  Read-only rows.
        _cpp = list(getattr(m, "effective_capacitance_per_phase_nf", []) or [])
        _rpp = list(getattr(m, "rc_fit_resistance_per_phase_kohm", []) or [])
        _tpp = list(getattr(m, "rc_fit_tau_per_phase_us", []) or [])
        for _k in range(1, max(len(_cpp), len(_rpp), len(_tpp))):
            _rv = _rpp[_k] if _k < len(_rpp) else float("nan")
            _cv = _cpp[_k] if _k < len(_cpp) else float("nan")
            _tv = _tpp[_k] if _k < len(_tpp) else float("nan")
            if math.isfinite(_rv):
                rows.append((f"R (R‖C) ph{_k + 1} [kΩ]",
                             _fmt_or_dash(_rv, ".1f")))
            if math.isfinite(_cv):
                rows.append((f"{_c_lbl} ph{_k + 1} [nF]",
                             _fmt_or_dash(_cv, ".3g")))
            if math.isfinite(_tv):
                rows.append((f"τ (R‖C) ph{_k + 1} [µs]",
                             _fmt_or_dash(_tv, ".1f")))
    # Driving impedance Z_d = V_d/I_stim and driving energy (∫V·I over the
    # pulse) — operator request.  Shown when finite.
    if math.isfinite(getattr(m, "driving_impedance_kohm", float("nan"))):
        rows.append((f"{L('Z','d')} [kΩ]",
                     _fmt_or_dash(m.driving_impedance_kohm, ".3f")))
    if math.isfinite(getattr(m, "driving_energy_uj", float("nan"))):
        from .widgets import _split_energy
        _de_v, _de_u = _split_energy(m.driving_energy_uj)
        rows.append((f"Driving energy [{_de_u}]", _de_v))
    # Harris 2019 chronopotentiometry capacitive/Faradaic decomposition
    # (normal captures only) — C_dl + approximate Faradaic split.
    if math.isfinite(getattr(m, "c_dl_mf_per_cm2", float("nan"))):
        rows.append((f"{L('C', 'dl')} [mF/cm²]",
                     _fmt_or_dash(m.c_dl_mf_per_cm2, ".3f")))
    if math.isfinite(getattr(m, "faradaic_fraction", float("nan"))):
        rows.append(("Faradaic charge fraction",
                     f"{m.faradaic_fraction * 100:.0f}%"))
    if math.isfinite(getattr(m, "faradaic_onset_us", float("nan"))):
        _fo = f"{m.faradaic_onset_us:.0f} µs"
        if math.isfinite(getattr(m, "faradaic_onset_v", float("nan"))):
            _fo += f"  ({m.faradaic_onset_v:+.3f} V)"
        rows.append(("Faradaic onset", _fo))
    if m.active_driving_voltage_per_phase_v:
        rows.append((f"{L('V','d')} active per phase [V]",
                     ", ".join(f"{v:.3f}" for v in m.active_driving_voltage_per_phase_v)))
    if m.return_driving_voltage_per_phase_v:
        rows.append((f"{L('V','d')} return per phase [V]",
                     ", ".join(f"{v:.3f}" for v in m.return_driving_voltage_per_phase_v)))
    if m.access_voltage_per_phase_v:
        # Auto-scale V / mV / µV so a small access voltage doesn't read 0.000
        # (operator); unit lands in the label column.  See widgets helper.
        from .widgets import _fmt_voltage_list_auto
        _va_v, _va_u = _fmt_voltage_list_auto(m.access_voltage_per_phase_v)
        rows.append((f"{L('V','a')} active [{_va_u}]", _va_v))
    if m.access_resistance_per_phase_kohm:
        rows.append((f"{L('R','a')} active [kΩ]",
                     ", ".join(f"{r:.2f}" for r in m.access_resistance_per_phase_kohm)))
    if m.return_access_voltage_per_phase_v:
        from .widgets import _fmt_voltage_list_auto
        _rva_v, _rva_u = _fmt_voltage_list_auto(m.return_access_voltage_per_phase_v)
        rows.append((f"{L('V','a')} return [{_rva_u}]", _rva_v))
    if m.return_access_resistance_per_phase_kohm:
        rows.append((f"{L('R','a')} return [kΩ]",
                     ", ".join(f"{r:.2f}" for r in m.return_access_resistance_per_phase_kohm)))
    if m.polarization_per_phase_v:
        rows.append((f"{L('E','pol')} active [V]",
                     ", ".join(f"{e:.3f}" for e in m.polarization_per_phase_v)))
    if m.return_polarization_per_phase_v:
        rows.append((f"{L('E','pol')} return [V]",
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


def _run_accumulated_charge_nc(run: ChannelRun) -> float:
    """Total cathodic charge delivered across a channel/combo's captures.
    Prefers the per-capture running ``cumulative_charge_nc`` (VT/PS stamp
    it); else sums each capture's |charge_per_phase_nc|."""
    import math
    cums = [c.metrics.cumulative_charge_nc for c in run.captures
            if math.isfinite(getattr(c.metrics, "cumulative_charge_nc",
                                     float("nan")))]
    if cums:
        return max(cums)
    s = sum(abs(c.metrics.charge_per_phase_nc) for c in run.captures
            if math.isfinite(getattr(c.metrics, "charge_per_phase_nc",
                                     float("nan"))))
    return s if s > 0 else float("nan")


def _run_cumulative_n_pulses(run: ChannelRun) -> float:
    """Total pulses delivered across a channel/combo's captures.  Prefers the
    per-capture running ``cumulative_n_pulses`` (VT/PS stamp it); else sums
    each capture's ``n_pulses``."""
    import math
    cums = [c.metrics.cumulative_n_pulses for c in run.captures
            if math.isfinite(getattr(c.metrics, "cumulative_n_pulses",
                                     float("nan")))]
    if cums:
        return max(cums)
    s = sum(c.metrics.n_pulses for c in run.captures
            if math.isfinite(getattr(c.metrics, "n_pulses", float("nan"))))
    return s if s > 0 else float("nan")


def _fmt_pulses_or_dash(n: float) -> str:
    """Space-group a pulse count (operator: "separate thousands with a space
    instead of a comma"), or an em-dash when not recorded."""
    import math
    from .widgets import _group_thousands
    return _group_thousands(n) if math.isfinite(n) else "—"


def _fmt_charge_nc(q_nc: float) -> str:
    """Auto-scale a charge in nC to nC / µC / mC."""
    import math
    if not math.isfinite(q_nc):
        return "—"
    a = abs(q_nc)
    if a >= 1e6:
        return f"{q_nc / 1e6:.3f} mC"
    if a >= 1e3:
        return f"{q_nc / 1e3:.3f} µC"
    return f"{q_nc:.2f} nC"


def _session_uses_reference(session: Session) -> bool:
    """True iff ANY capture recorded a separate active/return potential
    (instrumentation amp) — i.e. an Ag|AgCl reference was actually used.
    When false the reference label should read N/A rather than the default
    'Ag|AgCl' (operator: "reference electrode says Ag|AgCl despite not
    used")."""
    for run in session.runs:
        for cap in run.captures:
            if ((cap.e_act_v is not None and len(cap.e_act_v))
                    or (cap.e_ret_v is not None and len(cap.e_ret_v))):
                return True
    return False


def _session_param_rows(session: Session, run: Optional[ChannelRun],
                        cap: Optional[Capture]) -> List[tuple]:
    """EXPERIMENT parameters only — file metadata (notebook/subject/…) lives
    in the separate File-info table (``_session_file_rows``)."""
    p = session.test.pattern
    rows: List[tuple] = []
    rows.append(("Experiment", session.test.experiment))
    rows.append(("Pattern",
                 "Triphasic" if p.is_triphasic else
                 ("Monophasic" if p.num_phases == 1 else "Biphasic")))
    rows.append(("Polarity",
                 "Cathodal-first" if p.polarity == -1 else "Anodal-first"))
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
    # Electrodes — counter is the RETURN electrode; the reference reads N/A
    # when no Ag|AgCl was actually used (no E_act/E_ret recorded).
    rows.append(("Return electrode", session.test.counter_electrode_label))
    rows.append(("Reference electrode",
                 session.test.reference_electrode_label
                 if _session_uses_reference(session) else "N/A (not used)"))
    # Rate LAST (operator: "Move Rate at the bottom of the parameters").
    rows.append(("Rate", f"{p.rate_hz} pps"))
    return rows


def _session_file_rows(session: Session) -> List[tuple]:
    """File / session metadata for the File-info table."""
    rows: List[tuple] = [
        ("Notebook", session.notebook or "—"),
        ("Subject", session.subject or "—"),
        ("Total runs", str(len(session.runs))),
        ("Total captures", str(session.total_captures)),
    ]
    created = getattr(session, "created_at", None)
    if created is not None:
        try:
            rows.append(("Created", created.strftime("%Y-%m-%d %H:%M")))
        except Exception:
            rows.append(("Created", str(created)))
    if getattr(session, "user_name", ""):
        rows.append(("Operator", session.user_name))
    if getattr(session, "save_dir", None):
        rows.append(("Save dir", str(session.save_dir)))
    return rows


def _status_text(cap: Capture) -> str:
    if cap.status.aborted:
        return "Aborted"
    if cap.status.voltage_compliance:
        return "Voltage compliance"
    if getattr(cap.status, "exceeded_potential_limit", False):
        return "Limit exceeded"
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
        # Pass line props ONLY when enabling — ``grid(False, linestyle=…)``
        # re-enables the grid (matplotlib gotcha), defeating the toggle.
        if show_grid:
            ax.grid(True, linestyle=":", color="#cccccc", linewidth=0.5)
        else:
            ax.grid(False)
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
def _close_startup_splash() -> None:
    """Dismiss the PyInstaller boot splash once the window is up.

    ``pyi_splash`` exists only in the frozen POLARIS build when a
    ``Splash()`` was bundled; the import fails (swallowed) in a normal
    ``python run_viewer.py`` run.
    """
    try:
        import pyi_splash  # type: ignore  # present only in the frozen app
        pyi_splash.close()
    except Exception:
        pass


def launch(initial_path: Optional[Path] = None) -> int:
    """Open the viewer as a standalone application."""
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = ViewerWindow(initial_path=initial_path)
    win.show()
    _close_startup_splash()   # dismiss the PyInstaller boot splash (frozen app)
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
