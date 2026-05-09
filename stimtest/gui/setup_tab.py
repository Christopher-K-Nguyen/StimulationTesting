"""Setup tab — choose device, connector, coating, scope mapping, experiment.

Layout (left = forms, right = device graphic + table):

    ┌──────────────────────────┬─────────────────────────┐
    │ Session                  │  ╔ Test device ════════╗│
    │ Test device dropdown     │  ║ <name> + description║│
    │ Connector dropdown       │  ║  geometry │  table  ║│
    │ Surface-area mode        │  ║   view    │ (edit)  ║│
    │   + area unit            │  ║ per-channel overrides║│
    │   + value (when 'Same')  │  ╚═════════════════════╝│
    │ Electrode coating        │                         │
    │   + custom field         │                         │
    │ Scope channel mapping    │                         │
    │ Experiment to run        │                         │
    │   [Open this tab >]      │                         │
    └──────────────────────────┴─────────────────────────┘

Whenever any control changes we rebuild an :class:`ElectrodeArray` and
emit ``arrayChanged`` so the experiment tabs can refresh their channel
grids. The dedicated *Open this tab* button emits ``experimentRequested``,
which :class:`MainWindow` connects to a ``QTabWidget.setCurrentWidget``.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt6 import QtCore, QtWidgets

from pathlib import Path

from ..config import (
    COATINGS, CONNECTORS, DEFAULT_SAVE_DIR, DEFAULT_SCOPE_MAP, DEVICES,
    EXPERIMENTS,
)
from ..electrode import ElectrodeArray
from . import rich
from .device_view import DeviceView
from .repeating_spinbox import RepeatingDoubleSpinBox, RepeatingSpinBox


CUSTOM_COATING_LABEL = "Custom…"
SAME_VAL = "Same for all electrodes"
DIFF_VAL = "Different per electrode"

# Per-channel role assignments for the oscilloscope mapping. The user
# picks one of these for each scope channel; "None" means the channel
# isn't being used. "Trigger" lets a scope channel act as the trigger
# source when the bench scope has no external trigger input — when no
# channel is set as Trigger, the runner falls back to the channel
# carrying the current monitor (I_mon).
ROLE_NONE   = "None"
ROLE_VMON   = "V_mon"
ROLE_IMON   = "I_mon"
ROLE_EACT   = "E_act"
ROLE_ERET   = "E_ret"
ROLE_TRIG   = "Trigger"
SCOPE_ROLES = (ROLE_NONE, ROLE_VMON, ROLE_IMON, ROLE_EACT, ROLE_ERET, ROLE_TRIG)
# Default (channel → role) wiring — matches the legacy layout
# (CH1 = V_mon, CH2 = I_mon, CH3 = E_ret, CH4 = E_act).
DEFAULT_CHANNEL_ROLES = {
    "CH1": ROLE_VMON, "CH2": ROLE_IMON,
    "CH3": ROLE_ERET, "CH4": ROLE_EACT,
}
# Backwards-compatible aliases — any older code/prefs that referenced the
# old constants keeps working.
SAME_AREA = SAME_VAL
DIFF_AREA = DIFF_VAL
UNITS = {
    "μm²": 1.0,
    "mm²": 1e6,        # 1 mm² = 1e6 μm²
    "cm²": 1e8,        # 1 cm² = 1e8 μm²
}


class SetupTab(QtWidgets.QWidget):
    """Top-level setup tab with rich-text labels and DeviceView."""
    arrayChanged = QtCore.pyqtSignal(object)         # ElectrodeArray
    aliasesChanged = QtCore.pyqtSignal(dict)         # {logical: 'CHx'}
    experimentRequested = QtCore.pyqtSignal(str)     # experiment code (VT/SP/LP/PS)
    savePathChanged = QtCore.pyqtSignal(str)         # absolute path to save dir
    # (acquisition_mode, n_avg) — runner-side code applies these to the
    # scope before each capture. n_avg is ignored for SAMPLE mode.
    acquisitionChanged = QtCore.pyqtSignal(str, int)
    # True when the user has "Same for all electrodes" ticked under
    # Surface area. VT listens so it can hide the Fixed charge density
    # mode (uniform-area arrays make that mode redundant with
    # Fixed current).
    sameAreaChanged = QtCore.pyqtSignal(bool)
    # Emitted when the user toggles "Save .xlsx after session".
    # MainWindow forwards to every experiment tab so the next run
    # picks up the new setting without restart.
    autoExportXlsxChanged = QtCore.pyqtSignal(bool)
    # Emitted when the notebook / session / notebook-toggle changes —
    # MainWindow uses it to repoint the on-disk log file at the new
    # session-stem-derived filename.
    sessionFilenameChanged = QtCore.pyqtSignal(str)
    # Emitted when the user toggles email notifications. MainWindow
    # forwards to every experiment tab so the next run picks up the
    # new setting without a restart.
    emailNotificationsChanged = QtCore.pyqtSignal(bool)
    # Emitted as ``(user_name, user_email)`` when the user finishes
    # editing either field. The runner uses these to address the
    # completion / failure email.
    userIdentityChanged = QtCore.pyqtSignal(str, str)
    # Emitted with the raw Session text so the runner can put it in
    # the email subject. Distinct from ``sessionFilenameChanged``,
    # which carries the composed log-filename stem.
    sessionSubjectChanged = QtCore.pyqtSignal(str)
    # Emitted as ``(enabled, fmt)`` whenever the user flips the
    # "Save plots after session" checkbox or picks a different file
    # format from the adjacent dropdown. Format is one of
    # ``png`` / ``jpg`` / ``tif`` / ``svg`` (lowercase, no dot).
    autoSavePlotsChanged = QtCore.pyqtSignal(bool, str)
    # Emitted as ``(cathodic_v, anodic_v, tolerance_v)`` whenever the
    # user adjusts a potential-limit spinbox or picks a different
    # coating that auto-fills new values. The VT runner uses these
    # in place of the catalog defaults so the user can override per
    # session.
    potentialLimitsChanged = QtCore.pyqtSignal(float, float, float)

    def __init__(self, connection_panel=None, parent=None):
        super().__init__(parent)
        # The connection / hardware panel is owned by MainWindow (it
        # holds the live stim/scope handles), but it lives visually
        # *inside* the Setup tab as a group box at the top. SetupTab
        # just embeds whatever widget it's handed.
        self._connection_panel = connection_panel

        # ---------------- left column controls ----------------
        # Session info. The user-visible label says "Session" but the
        # internal attribute keeps the historical name ``self.subject``
        # so saved .npz / prefs from earlier sessions still round-trip
        # without a migration shim.
        self.notebook = QtWidgets.QLineEdit("session_001")
        # Notebook toggle: when unchecked, the notebook field is
        # disabled and treated as empty for filename composition (so
        # "log.txt" becomes "<Session>_log.txt" instead of
        # "<Notebook>_<Session>_log.txt"). The .npz save still records
        # the notebook value if any was typed.
        self.notebook_check = QtWidgets.QCheckBox("Notebook:")
        self.notebook_check.setChecked(True)
        self.notebook_check.setToolTip(
            "Untick to omit the notebook prefix from the log filename "
            "(<Session>_log.txt instead of <Notebook>_<Session>_log.txt).")
        self.notebook_check.toggled.connect(self._on_notebook_toggled)
        self.notebook.editingFinished.connect(self._emit_session_filename)
        self.subject = QtWidgets.QLineEdit("electrode_a1")
        self.subject.editingFinished.connect(self._emit_session_filename)
        self.subject.editingFinished.connect(self._emit_session_subject)
        self.user_name = QtWidgets.QLineEdit()
        self.user_email = QtWidgets.QLineEdit()
        self.user_name.editingFinished.connect(self._emit_user_identity)
        self.user_email.editingFinished.connect(self._emit_user_identity)
        # Save path — folder where session .npz files land. Edited as
        # text or via the Browse… button. Defaults to the project's
        # ``data`` folder, made absolute on first emit.
        self.save_path = QtWidgets.QLineEdit(str(Path(DEFAULT_SAVE_DIR).resolve()))
        self.save_path_browse = QtWidgets.QPushButton("Browse…")
        self.save_path_browse.clicked.connect(self._on_browse_save_path)
        self.save_path.editingFinished.connect(self._emit_save_path)
        # Auto-export the saved session to a Gamry-DTA-style .xlsx
        # immediately after each run completes. The .npz save still
        # happens unconditionally; this just adds the workbook on top.
        # Default off — the .xlsx write is slower than the .npz one
        # and not everyone wants both formats every time.
        self.auto_export_xlsx = QtWidgets.QCheckBox(
            "Save .xlsx after session")
        # Email notification toggle — mirrors the MATLAB sendEmail /
        # sendError workflow. When checked AND ``user_email`` is set
        # AND SMTP credentials are configured (env vars or
        # ``~/.stimtest/email_config.json``), the runner emails the
        # user at the end of every run with the saved .npz attached.
        # Off by default — the user has to opt in.
        self.email_notifications = QtWidgets.QCheckBox(
            "Email notifications")
        self.email_notifications.setToolTip(
            "Email the User email address at the end of every run "
            "(success or failure). Requires SMTP credentials in env "
            "vars STIMTEST_SMTP_USER / STIMTEST_SMTP_PASSWORD or in "
            "~/.stimtest/email_config.json. Off by default.")
        self.email_notifications.toggled.connect(
            self._emit_email_notifications)
        # Auto-save channel / combination plots after each run. Off by
        # default; when on, the runner calls ``export_session_plots``
        # using the format picked from the adjacent dropdown. Mirrors
        # the manual "Export plot" button on the Results tab.
        self.auto_save_plots = QtWidgets.QCheckBox(
            "Save plots after session")
        self.auto_save_plots.setToolTip(
            "When checked, every experiment writes one plot per "
            "channel/combination next to the .npz session in the "
            "selected format. Manual export from the Results tab still "
            "works either way.")
        self.auto_save_plots_fmt = QtWidgets.QComboBox()
        # Display labels are uppercase but the underlying data is the
        # lowercase extension matplotlib expects.
        for label, ext in (("PNG", "png"), ("JPEG", "jpg"),
                           ("TIFF", "tif"), ("SVG", "svg")):
            self.auto_save_plots_fmt.addItem(label, ext)
        # Default to TIFF — matches MATLAB ``-r600`` and the catalog
        # plotting helpers' default extension.
        self.auto_save_plots_fmt.setCurrentIndex(2)
        # Greyed out until the toggle is on so the user reads the
        # combo as conditional on the checkbox.
        self.auto_save_plots_fmt.setEnabled(False)
        self.auto_save_plots.toggled.connect(
            self.auto_save_plots_fmt.setEnabled)
        self.auto_save_plots.toggled.connect(
            self._emit_auto_save_plots)
        self.auto_save_plots_fmt.currentIndexChanged.connect(
            self._emit_auto_save_plots)
        self.auto_export_xlsx.setToolTip(
            "When checked, every experiment automatically writes a "
            "Gamry-DTA-style .xlsx workbook next to the .npz session "
            "(same stem, .xlsx extension). Manual export from the "
            "Results tab still works either way.")
        self.auto_export_xlsx.toggled.connect(self._emit_auto_export_xlsx)

        # Device
        self.device_combo = QtWidgets.QComboBox()
        for k in DEVICES: self.device_combo.addItem(k)
        self.device_combo.currentTextChanged.connect(self._on_device_changed)

        # Connector
        self.connector_combo = QtWidgets.QComboBox()
        for k in CONNECTORS: self.connector_combo.addItem(k)
        self.connector_combo.currentTextChanged.connect(self._emit_array)

        # Surface area mode + units + value
        # Surface area: "Same for all electrodes" toggle (default on).
        # Same shape as the coating toggle so the two read consistently.
        self.area_mode = QtWidgets.QCheckBox("Same for all electrodes")
        self.area_mode.setChecked(True)
        self.area_unit = QtWidgets.QComboBox()
        self.area_unit.addItems(list(UNITS.keys()))
        self.area_value = RepeatingDoubleSpinBox()
        self.area_value.setRange(0.001, 1e9)
        self.area_value.setDecimals(3)
        self.area_value.setValue(5000.0)        # μm² default
        self.area_unit.currentTextChanged.connect(self._on_area_unit_changed)
        self.area_value.valueChanged.connect(self._emit_array)
        self.area_mode.toggled.connect(self._on_area_mode_changed)

        # Coating mode toggle — checked = "Same for all electrodes"
        # (the common case), unchecked surfaces the per-channel
        # override table. Replaces the older Same/Different dropdown
        # to take less horizontal room and read more naturally.
        self.coating_mode = QtWidgets.QCheckBox("Same for all electrodes")
        self.coating_mode.setChecked(True)
        self.coating_mode.toggled.connect(self._on_coating_mode_changed)

        # Each combo item shows the spelled-out coating ("Tungsten (W)")
        # but stores the short canonical name ("W") as userData. The
        # short name is what gets persisted to ElectrodePosition.coating
        # and round-tripped through prefs.
        self.coating_combo = QtWidgets.QComboBox()
        for short_name, coating in COATINGS.items():
            label = coating.display_name or short_name
            self.coating_combo.addItem(label, userData=short_name)
        # Custom… is the last entry, after Tungsten.
        self.coating_combo.addItem(CUSTOM_COATING_LABEL,
                                   userData=CUSTOM_COATING_LABEL)
        self.coating_custom = QtWidgets.QLineEdit()
        self.coating_custom.setPlaceholderText("Enter custom coating name…")
        self.coating_custom.setVisible(False)
        self.coating_combo.currentTextChanged.connect(self._on_coating_changed)
        self.coating_custom.editingFinished.connect(self._emit_array)

        # ----- water-window potential limits -----
        # Pre-filled from the selected coating's catalog values but
        # editable so the user can override them per session (different
        # reference electrode, different electrolyte, custom coating
        # not in the catalog). The polarization tolerance is a grace
        # band added to the limit before the runner declares "limit
        # hit" — same idea as MATLAB's TOL = 0.02 V in
        # ``changeCurrent_Fit.m``. Track whether the limit fields
        # have been hand-edited so refreshing them on a coating switch
        # only happens while the user is still on catalog defaults.
        self._limits_user_edited = False
        self.cathodic_limit_v = RepeatingDoubleSpinBox()
        self.cathodic_limit_v.setRange(-3.0, 0.0)
        self.cathodic_limit_v.setDecimals(3)
        self.cathodic_limit_v.setSingleStep(0.05)
        self.cathodic_limit_v.setSuffix(" V")
        self.cathodic_limit_v.setValue(-0.6)
        self.anodic_limit_v = RepeatingDoubleSpinBox()
        self.anodic_limit_v.setRange(0.0, 3.0)
        self.anodic_limit_v.setDecimals(3)
        self.anodic_limit_v.setSingleStep(0.05)
        self.anodic_limit_v.setSuffix(" V")
        self.anodic_limit_v.setValue(0.8)
        self.polarization_tol_v = RepeatingDoubleSpinBox()
        self.polarization_tol_v.setRange(0.0, 0.500)
        self.polarization_tol_v.setDecimals(3)
        self.polarization_tol_v.setSingleStep(0.005)
        self.polarization_tol_v.setSuffix(" V")
        # MATLAB's runVoltageTransient.m uses TOL = 0.02 V as the
        # default grace band on potential excursions; same default here.
        self.polarization_tol_v.setValue(0.020)
        for sp in (self.cathodic_limit_v, self.anodic_limit_v,
                   self.polarization_tol_v):
            sp.valueChanged.connect(self._on_limits_user_edited)

        # Oscilloscope channel mapping — inverted from the legacy
        # design: rows are the four scope channels, the dropdown picks
        # which waveform role the channel carries. ``self._role_combos``
        # maps channel name → its role QComboBox so current_aliases /
        # restore_prefs can iterate without naming each one.
        # Defaults to ``None`` for every channel: the user explicitly
        # opts in once a scope is connected, and the panel calls
        # :meth:`apply_default_scope_mapping` on first connect.
        self._role_combos: dict = {}
        # Row labels are kept around so apply_scope_capabilities can
        # hide CH3/CH4 rows on a 2-channel scope (TBS1072C, TBS1052C,
        # etc.) — both the label and the combo are hidden together so
        # the form doesn't show empty placeholder rows. Set in
        # ``_assemble_pages`` when the form is laid out.
        self._role_labels: dict = {}
        for ch in ("CH1", "CH2", "CH3", "CH4"):
            cb = QtWidgets.QComboBox()
            cb.addItems(SCOPE_ROLES)
            cb.setCurrentText(ROLE_NONE)
            cb.currentTextChanged.connect(self._on_role_changed)
            self._role_combos[ch] = cb

        # Oscilloscope acquisition controls — Sampling vs Average, plus
        # an averaging-count widget that's either a combo (for scopes
        # with a fixed list, e.g. TBS-series) or a spinbox (for scopes
        # accepting arbitrary values). The widget is rebuilt on scope
        # connect via :meth:`apply_scope_capabilities`.
        self.acq_mode_combo = QtWidgets.QComboBox()
        self.acq_mode_combo.addItems(["SAMPLE", "AVERAGE"])
        self.acq_mode_combo.setCurrentText("AVERAGE")
        self.acq_mode_combo.currentTextChanged.connect(self._on_acq_changed)
        # Default n_avg widget = a generic spinbox; replaced when the
        # scope reports a discrete list of choices.
        self.acq_navg_spin = RepeatingSpinBox()
        self.acq_navg_spin.setRange(2, 512)
        self.acq_navg_spin.setValue(16)
        self.acq_navg_spin.valueChanged.connect(self._on_acq_changed)
        self.acq_navg_combo: QtWidgets.QComboBox | None = None

        # Experiment picker
        self.experiment_combo = QtWidgets.QComboBox()
        for code, defn in EXPERIMENTS.items():
            self.experiment_combo.addItem(defn.label, userData=code)
        self.experiment_blurb = QtWidgets.QLabel()
        self.experiment_blurb.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.experiment_blurb.setWordWrap(True)
        self.experiment_blurb.setStyleSheet("color: #555; font-size: 9pt;")
        self.open_exp_btn = QtWidgets.QPushButton("Open this experiment tab →")
        self.experiment_combo.currentIndexChanged.connect(self._on_experiment_changed)
        self.open_exp_btn.clicked.connect(self._on_open_experiment)

        # ---------------- right column: device view ----------------
        self.device_view = DeviceView()
        self.device_view.mappingChanged.connect(lambda *_: self._emit_array())
        self.device_view.perChannelChanged.connect(self._emit_array)

        # ---------------- assemble forms ----------------
        # Session — Notebook / Subject / User / Save path.
        sess_form = rich.make_form()
        # Notebook row uses the toggleable checkbox AS its label so the
        # user can tick/untick without leaving the row. Untick disables
        # the field and drops the notebook prefix from the log filename.
        sess_form.addRow(self.notebook_check, self.notebook)
        sess_form.addRow(self._lbl("Session:"), self.subject)
        sess_form.addRow(self._lbl("User name:"), self.user_name)
        sess_form.addRow(self._lbl("User email:"), self.user_email)
        # Email-notifications checkbox sits directly under User email so
        # the user reads them as one unit (input + opt-in).
        sess_form.addRow(self._lbl(""), self.email_notifications)
        sp_row = QtWidgets.QHBoxLayout()
        # Zero the margins on the wrapping HBox so the line edit's
        # baseline lines up with the bare-spinbox / line-edit fields
        # above (Notebook / Subject / etc.). Default QHBoxLayout
        # contents margins push the wrapped widget visibly down.
        sp_row.setContentsMargins(0, 0, 0, 0)
        sp_row.setSpacing(6)
        sp_row.addWidget(self.save_path, stretch=1)
        sp_row.addWidget(self.save_path_browse)
        sp_w = QtWidgets.QWidget(); sp_w.setLayout(sp_row)
        sess_form.addRow(self._lbl("Save path:"), sp_w)
        # Empty label cell so the checkbox sits flush in the field
        # column without a phantom "title" beside it.
        sess_form.addRow(self._lbl(""), self.auto_export_xlsx)
        # Pair the auto-save-plots checkbox with the format dropdown on
        # one row so the user reads them as a single setting.
        plots_row = QtWidgets.QHBoxLayout()
        plots_row.setContentsMargins(0, 0, 0, 0)
        plots_row.setSpacing(6)
        plots_row.addWidget(self.auto_save_plots)
        plots_row.addWidget(self.auto_save_plots_fmt)
        plots_row.addStretch(1)
        plots_w = QtWidgets.QWidget(); plots_w.setLayout(plots_row)
        sess_form.addRow(self._lbl(""), plots_w)
        sess_box = QtWidgets.QGroupBox("Session"); QtWidgets.QVBoxLayout(sess_box).addLayout(sess_form)

        # Test device — keep its row labels LEFT-aligned (overriding
        # the project default) so the long "Surface area / Electrode
        # coating / Potential limits" rows breathe instead of sitting
        # flush against their multi-widget field column.
        dev_form = rich.make_form()
        dev_form.setLabelAlignment(
            QtCore.Qt.AlignmentFlag.AlignLeft |
            QtCore.Qt.AlignmentFlag.AlignVCenter)
        dev_form.addRow(self._lbl("Test device:"), self.device_combo)
        dev_form.addRow(self._lbl("Connector:"), self.connector_combo)

        # Surface area row — the value spinbox + unit combo sit RIGHT
        # NEXT to the "Same for all electrodes" checkbox, hugging it
        # left-to-right ("Same for all  [5000.000] [µm²]"). Trailing
        # ``addStretch`` keeps that group flush-left instead of spreading
        # across the field column. Same idea for the coating row below.
        area_row = QtWidgets.QHBoxLayout()
        area_row.setContentsMargins(0, 0, 0, 0)
        area_row.setSpacing(6)
        area_row.addWidget(self.area_mode)
        area_row.addWidget(self.area_value)
        area_row.addWidget(self.area_unit)
        area_row.addStretch(1)
        area_w = QtWidgets.QWidget(); area_w.setLayout(area_row)
        dev_form.addRow(self._lbl("Surface area:"), area_w)

        # Coating row: combo + custom field follow the "Same for all"
        # checkbox directly. The custom field auto-hides when the
        # selected coating isn't Custom… (see _on_coating_changed).
        coat_row = QtWidgets.QHBoxLayout()
        coat_row.setContentsMargins(0, 0, 0, 0)
        coat_row.setSpacing(6)
        coat_row.addWidget(self.coating_mode)
        coat_row.addWidget(self.coating_combo)
        coat_row.addWidget(self.coating_custom)
        coat_row.addStretch(1)
        coat_w = QtWidgets.QWidget(); coat_w.setLayout(coat_row)
        dev_form.addRow(self._lbl("Electrode coating:"), coat_w)

        # Potential limits — cathodic + anodic share the top row;
        # tolerance lives on its own line BELOW the cathodic limit so
        # it visually distinguishes the asymmetric +/- band from the
        # two distinct water-window endpoints. Each inline label uses
        # the project's standard "Name (variable) [unit]:" format
        # from rich.field_label.
        lim_top = QtWidgets.QHBoxLayout()
        lim_top.setContentsMargins(0, 0, 0, 0)
        lim_top.setSpacing(6)
        lim_top.addWidget(self._lbl(rich.field_label(
            "Cathodic limit", rich.E_LC, "V")))
        lim_top.addWidget(self.cathodic_limit_v)
        lim_top.addSpacing(12)
        lim_top.addWidget(self._lbl(rich.field_label(
            "Anodic limit", rich.E_LA, "V")))
        lim_top.addWidget(self.anodic_limit_v)
        lim_top.addStretch(1)

        lim_bot = QtWidgets.QHBoxLayout()
        lim_bot.setContentsMargins(0, 0, 0, 0)
        lim_bot.setSpacing(6)
        lim_bot.addWidget(self._lbl(rich.field_label(
            "Tolerance", unit_str="±V")))
        lim_bot.addWidget(self.polarization_tol_v)
        lim_bot.addStretch(1)

        lim_v = QtWidgets.QVBoxLayout()
        lim_v.setContentsMargins(0, 0, 0, 0)
        lim_v.setSpacing(2)
        lim_v.addLayout(lim_top)
        lim_v.addLayout(lim_bot)
        lim_w = QtWidgets.QWidget(); lim_w.setLayout(lim_v)
        dev_form.addRow(self._lbl("Potential limits:"), lim_w)

        dev_box = QtWidgets.QGroupBox("Test device parameters")
        QtWidgets.QVBoxLayout(dev_box).addLayout(dev_form)

        # Oscilloscope acquisition group — Mode + n_avg. n_avg widget
        # holder is the QHBoxLayout we keep a reference to so we can
        # swap the inner widget (combo vs spinbox) at scope-connect.
        acq_form = rich.make_form()
        acq_form.addRow("Mode:", self.acq_mode_combo)
        self._acq_navg_holder = QtWidgets.QHBoxLayout()
        self._acq_navg_holder.addWidget(self.acq_navg_spin, stretch=1)
        navg_w = QtWidgets.QWidget(); navg_w.setLayout(self._acq_navg_holder)
        self._acq_navg_label = QtWidgets.QLabel("Average count:")
        acq_form.addRow(self._acq_navg_label, navg_w)
        acq_box = QtWidgets.QGroupBox("Oscilloscope acquisition")
        QtWidgets.QVBoxLayout(acq_box).addLayout(acq_form)

        # Oscilloscope mapping — one row per scope channel, dropdown
        # picks the waveform role assigned to that channel. Labels
        # are kept in self._role_labels so 2-channel scopes can hide
        # the CH3/CH4 rows without rebuilding the layout.
        sf = rich.make_form()
        for ch in ("CH1", "CH2", "CH3", "CH4"):
            lbl = QtWidgets.QLabel(f"{ch}:")
            self._role_labels[ch] = lbl
            sf.addRow(lbl, self._role_combos[ch])
        # Stash the form so the channel-visibility helper can poke it.
        self._scope_role_form = sf
        scope_box = QtWidgets.QGroupBox("Oscilloscope channel mapping")
        sv = QtWidgets.QVBoxLayout(scope_box)
        sv.addLayout(sf)
        # Helper line explaining the Trigger / fallback behaviour.
        # Text gets refreshed by ``apply_scope_capabilities`` when a
        # scope without an EXT input connects (e.g. TBS1000C family).
        self._scope_trigger_hint = QtWidgets.QLabel()
        self._scope_trigger_hint.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._refresh_scope_trigger_hint(has_ext=True)
        hint = self._scope_trigger_hint
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #555; font-size: 9pt;")
        sv.addWidget(hint)

        # Experiment picker
        exp_form = rich.make_form()
        exp_form.addRow(self._lbl("Experiment:"), self.experiment_combo)
        exp_box = QtWidgets.QGroupBox("Experiment to run")
        ev = QtWidgets.QVBoxLayout(exp_box)
        ev.addLayout(exp_form)
        ev.addWidget(self.experiment_blurb)
        ev.addWidget(self.open_exp_btn)

        # ---------------- left column container ----------------
        left_w = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left_w)
        # Hardware (Connection) panel sits at the very top of Setup so
        # the user can connect/disconnect without leaving this tab.
        if self._connection_panel is not None:
            hw_box = QtWidgets.QGroupBox("Hardware")
            hwv = QtWidgets.QVBoxLayout(hw_box)
            hwv.addWidget(self._connection_panel)
            lv.addWidget(hw_box)
        lv.addWidget(sess_box)
        lv.addWidget(dev_box)
        lv.addWidget(scope_box)
        lv.addWidget(acq_box)
        lv.addWidget(exp_box)
        lv.addStretch(1)
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidget(left_w)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Don't let the splitter squeeze the form column so narrow that
        # the form fields clip — keep enough room for the widest row
        # (the Surface area row with mode + value + unit, and the
        # coating row with mode + selector + custom field), plus a
        # comfortable margin. 520 px holds those without truncation.
        left_scroll.setMinimumWidth(520)

        # ---------------- top-level split ----------------
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self._main_split = split
        split.addWidget(left_scroll)
        split.addWidget(self.device_view)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        # Make sure the user can't drag the divider all the way over and
        # lose the form column. The min-width above already provides a
        # hard floor; this stops Qt from collapsing the panel to zero.
        split.setCollapsible(0, False)

        outer = QtWidgets.QHBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.addWidget(split)

        # ---------------- wiring ----------------
        # Role combos already have their currentTextChanged hooked to
        # _on_role_changed at construction; nothing extra needed here.

        # Initial population
        self._on_device_changed(self.device_combo.currentText())
        self._on_experiment_changed(self.experiment_combo.currentIndex())
        QtCore.QTimer.singleShot(0, self._emit_aliases)
        QtCore.QTimer.singleShot(0, self._emit_limits)

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _lbl(html: str) -> QtWidgets.QLabel:
        l = QtWidgets.QLabel(html)
        l.setTextFormat(QtCore.Qt.TextFormat.RichText)
        # Render the text on the QLabel's vertical centerline so it
        # sits flush with the centerline of any spinbox / combo /
        # line-edit it shares a row with. Without this, the label
        # text draws to the top of its bounding box and looks
        # off-baseline against shorter inputs.
        l.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft |
                       QtCore.Qt.AlignmentFlag.AlignVCenter)
        return l

    @staticmethod
    def _set_form_row_visible(field_widget, visible: bool):
        """Hide / show a QFormLayout row by toggling label and field.

        Walks the parent's layout tree (via ``rich.find_form_layout``)
        rather than just the immediate ``parent.layout()`` so this
        works when the form is nested inside an outer VBox/HBox —
        which is the common case here (dev_form is added to dev_box's
        VBox layout via ``addLayout``).
        """
        field_widget.setVisible(visible)
        parent = field_widget.parentWidget()
        if parent is None:
            return
        form = rich.find_form_layout(parent.layout(), field_widget)
        if form is None:
            return
        label = form.labelForField(field_widget)
        if label is not None:
            label.setVisible(visible)

    def _on_browse_save_path(self):
        """Open a folder picker and update the save path field on accept."""
        cur = self.save_path.text() or str(Path(DEFAULT_SAVE_DIR).resolve())
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose folder for saved sessions", cur)
        if chosen:
            self.save_path.setText(chosen)
            self._emit_save_path()

    def _emit_save_path(self):
        path = self.save_path.text().strip()
        if path:
            self.savePathChanged.emit(path)

    def _emit_auto_export_xlsx(self, checked: bool):
        """User flipped the auto-export checkbox — broadcast."""
        self.autoExportXlsxChanged.emit(bool(checked))

    def current_auto_export_xlsx(self) -> bool:
        return bool(self.auto_export_xlsx.isChecked())

    def _emit_email_notifications(self, checked: bool):
        self.emailNotificationsChanged.emit(bool(checked))

    def current_email_notifications(self) -> bool:
        return bool(self.email_notifications.isChecked())

    def _emit_auto_save_plots(self, *_):
        """Toggle or format-combo edited — broadcast the (on, fmt) pair.
        Connected to both the checkbox toggle and the combo's index
        change, so the runner picks up either kind of edit on the next
        run."""
        self.autoSavePlotsChanged.emit(
            bool(self.auto_save_plots.isChecked()),
            self.current_auto_save_plots_format(),
        )

    def current_auto_save_plots(self) -> bool:
        return bool(self.auto_save_plots.isChecked())

    def current_auto_save_plots_format(self) -> str:
        ext = self.auto_save_plots_fmt.currentData()
        return str(ext) if ext else "tif"

    def _emit_user_identity(self):
        self.userIdentityChanged.emit(
            self.user_name.text().strip(),
            self.user_email.text().strip(),
        )

    def _emit_session_subject(self):
        self.sessionSubjectChanged.emit(self.subject.text().strip())

    def current_user_name(self) -> str:
        return self.user_name.text().strip()

    def current_user_email(self) -> str:
        return self.user_email.text().strip()

    def current_session_subject(self) -> str:
        return self.subject.text().strip()

    # ---- session-filename composition (drives the on-disk log path) ---
    @staticmethod
    def _sanitize_filename_part(s: str) -> str:
        """Reduce a free-text field to a safe filesystem-stem fragment.

        Replaces anything that isn't alphanumeric / dash / underscore /
        period with an underscore, collapses runs, and trims. Keeps the
        result short enough to combine with siblings inside Windows'
        260-char path cap without surprises.
        """
        import re
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", s.strip())
        cleaned = cleaned.strip("_")
        return cleaned[:80]

    def current_log_filename(self) -> str:
        """Compose the on-disk log filename from the current fields.

        With notebook toggle ON and both fields populated:
            ``<Notebook>_<Session>_log.txt``
        With notebook toggle OFF (or notebook field empty):
            ``<Session>_log.txt``
        With BOTH missing (truly fresh launch with cleared fields):
            ``log.txt``
        """
        session = self._sanitize_filename_part(self.subject.text())
        use_notebook = (self.notebook_check.isChecked()
                        and self.notebook.text().strip())
        if use_notebook:
            notebook = self._sanitize_filename_part(self.notebook.text())
            stem = f"{notebook}_{session}" if session else notebook
        else:
            stem = session
        return f"{stem}_log.txt" if stem else "log.txt"

    def _emit_session_filename(self, *_):
        """Re-emit the composed log filename when any contributing
        field (notebook text, session text, notebook toggle) changes.
        """
        self.sessionFilenameChanged.emit(self.current_log_filename())

    def _on_notebook_toggled(self, checked: bool):
        """Notebook toggle flipped: enable/disable the field, then
        re-emit the composed filename."""
        self.notebook.setEnabled(bool(checked))
        self._emit_session_filename()

    # ----------------------------------------------------------- acquisition
    def _on_acq_changed(self, *_):
        """Mode or count widget changed → re-emit the (mode, n_avg) pair."""
        n_avg = self._current_n_avg()
        self.acquisitionChanged.emit(self.acq_mode_combo.currentText(), n_avg)
        # Average count is only meaningful in AVERAGE mode — grey it out
        # otherwise so the user sees the value won't be used.
        is_avg = self.acq_mode_combo.currentText().upper() == "AVERAGE"
        self._acq_navg_label.setEnabled(is_avg)
        if self.acq_navg_combo is not None:
            self.acq_navg_combo.setEnabled(is_avg)
        self.acq_navg_spin.setEnabled(is_avg)

    def _current_n_avg(self) -> int:
        if self.acq_navg_combo is not None and self.acq_navg_combo.isVisible():
            data = self.acq_navg_combo.currentData()
            try:
                return int(data) if data is not None else int(self.acq_navg_combo.currentText())
            except (TypeError, ValueError):
                return int(self.acq_navg_spin.value())
        return int(self.acq_navg_spin.value())

    def _refresh_scope_trigger_hint(self, *, has_ext: bool) -> None:
        """Update the helper text under the channel-mapping form.

        With EXT available (TBS2000B/MSO/MDO/DPO):
          "Pick Trigger on a channel if your scope has no external
           trigger input ..."

        Without EXT (TBS1000C / TBS1000B-EDU):
          "This scope has no EXT trigger input. Set Trigger on the
           channel wired to your sync source ..."

        The hint is shown verbatim under the channel-mapping group
        box, so it should read like prose for the lab user, not a
        SCPI / driver-implementation comment.
        """
        if not hasattr(self, "_scope_trigger_hint"):
            return
        if has_ext:
            txt = (
                "Pick <b>Trigger</b> on a channel if your scope has no "
                "external trigger input. If no channel is set to "
                f"Trigger, the channel carrying {rich.I_MON} is used as "
                "the trigger source."
            )
        else:
            txt = (
                "<b>This scope has no EXT trigger input.</b> Set "
                "<b>Trigger</b> on the channel wired to your sync "
                f"source (or leave unset to fall back to {rich.I_MON})."
            )
        self._scope_trigger_hint.setText(txt)

    def _set_visible_scope_channels(self, n_channels: int) -> None:
        """Show/hide CH-role rows so only n_channels of them remain.

        Used by :meth:`apply_scope_capabilities` to hide CH3/CH4 on a
        2-channel scope (TBS1052C/1072C/1102C). Hidden channels are
        also reset to ``ROLE_NONE`` so they don't sneak into
        :meth:`current_aliases` and confuse the runner with a mapping
        the user can't actually honour.

        ``n_channels`` is clamped to [2, 4]: scopes outside that range
        aren't supported by this codebase (every PlexStim experiment
        wants at minimum V_mon and I_mon, and the catalog tops out at
        4-channel TBS2204B-class hardware).
        """
        n = max(2, min(4, int(n_channels)))
        for i, ch in enumerate(("CH1", "CH2", "CH3", "CH4"), start=1):
            visible = (i <= n)
            lbl = self._role_labels.get(ch)
            cb = self._role_combos.get(ch)
            if lbl is not None:
                lbl.setVisible(visible)
            if cb is not None:
                cb.setVisible(visible)
                # Resetting hidden channels to None means current_aliases
                # builds the {logical → physical} dict without ever
                # mapping a role onto a channel that doesn't exist.
                if not visible and cb.currentText() != ROLE_NONE:
                    cb.blockSignals(True)
                    try:
                        cb.setCurrentText(ROLE_NONE)
                    finally:
                        cb.blockSignals(False)
        # Re-emit so any listeners (the runner aliases path) see the
        # cleaned-up mapping immediately rather than waiting for the
        # next user click.
        self.aliasesChanged.emit(self.current_aliases())

    def apply_scope_capabilities(self, scope=None):
        """Rebuild the n_avg widget to match what the connected scope supports.

        Also adapts the channel-mapping form to the scope's channel
        count: 2-channel scopes hide CH3/CH4 rows so the user can't
        accidentally map E_act / E_ret onto channels that don't exist.

        If the scope returns a fixed list of choices (TBS-series:
        powers of two from 2 to 512), the spinbox is hidden and a
        combo box of those choices is shown instead. If the scope
        returns ``None`` (arbitrary), the spinbox stays visible with
        its max set from ``scope.max_average_count()``.
        """
        # Channel-count visibility: read from scope.info if present,
        # else default to all 4 visible (the user might be running
        # offline / simulated, where we don't constrain).
        n_channels = 4
        has_ext = True
        if scope is not None:
            try:
                n_channels = int(getattr(scope.info, "n_channels", 4) or 4)
            except (TypeError, ValueError, AttributeError):
                n_channels = 4
            try:
                has_ext = bool(getattr(scope.info, "has_ext_trigger", True))
            except AttributeError:
                has_ext = True
        self._set_visible_scope_channels(n_channels)
        self._refresh_scope_trigger_hint(has_ext=has_ext)

        modes = (scope.acquisition_modes() if scope is not None
                 else ["SAMPLE", "AVERAGE"])
        choices = (scope.average_count_choices() if scope is not None else None)
        max_n = (scope.max_average_count() if scope is not None else 512)

        # Mode combo — repopulate while preserving the selection.
        prev_mode = self.acq_mode_combo.currentText()
        self.acq_mode_combo.blockSignals(True)
        try:
            self.acq_mode_combo.clear()
            for m in modes:
                self.acq_mode_combo.addItem(m)
            if prev_mode in modes:
                self.acq_mode_combo.setCurrentText(prev_mode)
            else:
                self.acq_mode_combo.setCurrentIndex(
                    modes.index("AVERAGE") if "AVERAGE" in modes else 0)
        finally:
            self.acq_mode_combo.blockSignals(False)

        # n_avg widget — combo for fixed lists, spinbox otherwise.
        prev_navg = self._current_n_avg()
        # Tear down the previous combo if any.
        if self.acq_navg_combo is not None:
            self._acq_navg_holder.removeWidget(self.acq_navg_combo)
            self.acq_navg_combo.deleteLater()
            self.acq_navg_combo = None
        if choices:
            self.acq_navg_spin.setVisible(False)
            cb = QtWidgets.QComboBox()
            for v in choices:
                cb.addItem(str(v), userData=int(v))
            # Pick the choice closest to the previously-set count.
            nearest = min(choices, key=lambda v: abs(v - int(prev_navg)))
            cb.setCurrentText(str(nearest))
            cb.currentTextChanged.connect(self._on_acq_changed)
            self.acq_navg_combo = cb
            self._acq_navg_holder.insertWidget(0, cb, 1)
        else:
            self.acq_navg_spin.setVisible(True)
            self.acq_navg_spin.setMaximum(int(max_n))
            self.acq_navg_spin.setValue(min(int(prev_navg), int(max_n)))
        self._on_acq_changed()

    def apply_default_scope_mapping(self):
        """Set every channel-role combo to its catalog default.

        Called by ``MainWindow`` the first time a scope connects, so
        the user doesn't have to fill the four rows by hand. If the
        user has already overridden a row (it's not at "None"), that
        row is left alone so we don't stomp deliberate choices.
        """
        for ch, default_role in DEFAULT_CHANNEL_ROLES.items():
            cb = self._role_combos.get(ch)
            if cb is not None and cb.currentText() == ROLE_NONE:
                cb.setCurrentText(default_role)

    def clear_scope_mapping(self):
        """Reset every channel role to ``None`` — used when the
        oscilloscope is disconnected so the GUI doesn't claim a
        mapping it can't honour. Also restores all 4 rows to visible
        and resets the trigger hint to its 'EXT available' wording
        since 'no scope' means we don't know either yet."""
        for cb in self._role_combos.values():
            cb.setCurrentText(ROLE_NONE)
        self._set_visible_scope_channels(4)
        self._refresh_scope_trigger_hint(has_ext=True)

    def _on_role_changed(self, *_):
        """A role dropdown changed — re-emit aliases. Role uniqueness is
        not enforced (the user might want to inspect the same waveform
        on two channels), but the runner uses the *first* match for any
        given role, so duplicates are harmless from the GUI's side."""
        self._emit_aliases()

    # ---------------------------------------------------------------- slots
    def _on_device_changed(self, name: str):
        if name not in DEVICES:
            return
        dev = DEVICES[name]
        self.device_view.set_device(dev)
        # Apply device-recommended defaults to area / coating / connector
        self.connector_combo.blockSignals(True)
        self.connector_combo.setCurrentText(dev.default_connector)
        self.connector_combo.blockSignals(False)
        # Linear arrays are 1-D — connector wiring isn't a meaningful
        # choice (the channels just go in order), so hide the row.
        is_linear = (name == "Linear")
        self._set_form_row_visible(self.connector_combo, not is_linear)
        # area defaults are stored in μm²; respect current unit
        unit_factor = UNITS[self.area_unit.currentText()]
        self.area_value.blockSignals(True)
        self.area_value.setValue(dev.default_surface_area_um2 / unit_factor)
        self.area_value.blockSignals(False)
        if dev.default_coating in COATINGS:
            # Match by userData (short name), not by visible text — the
            # combo shows spelled-out labels but ``dev.default_coating``
            # is always the canonical short form.
            self.coating_combo.blockSignals(True)
            idx = self.coating_combo.findData(dev.default_coating)
            if idx >= 0:
                self.coating_combo.setCurrentIndex(idx)
            self.coating_combo.blockSignals(False)
            self.coating_custom.setVisible(False)
        self._emit_array()

    def _on_area_unit_changed(self, _new_unit: str):
        # Don't convert the displayed number — most users expect to retype
        # in the new unit. Just rebuild the array with the new value
        # interpreted in the new unit.
        self._emit_array()

    def _on_area_mode_changed(self, _checked: bool = True):
        # Checked = same area for all electrodes; unchecked = per-channel.
        diff = not self.area_mode.isChecked()
        self.area_value.setEnabled(not diff)
        self.area_unit.setEnabled(not diff)
        self._update_perchan_visibility()
        self.sameAreaChanged.emit(self.area_mode.isChecked())
        self._emit_array()

    def _on_coating_mode_changed(self, _checked: bool = True):
        # Checked = "Same for all electrodes"; unchecked = per-channel.
        diff = not self.coating_mode.isChecked()
        self.coating_combo.setEnabled(not diff)
        self.coating_custom.setEnabled(not diff)
        self._update_perchan_visibility()
        self._emit_array()

    def _update_perchan_visibility(self):
        """Per-channel override table is needed if EITHER area or coating
        is in Different mode. Each column also tracks its own mode — if
        only coating is per-channel, the Area column hides (and vice
        versa), so the user only sees fields they're meant to fill in."""
        area_diff = not self.area_mode.isChecked()
        coat_diff = not self.coating_mode.isChecked()
        diff = area_diff or coat_diff
        self.device_view.set_per_channel_columns(area=area_diff, coating=coat_diff)
        self.device_view.set_per_channel_visible(diff)

    def _on_coating_changed(self, label: str):
        # Match against the visible label of the Custom… item — text
        # selection (vs. userData) is what currentTextChanged hands us.
        self.coating_custom.setVisible(label == CUSTOM_COATING_LABEL)
        # Refresh the cathodic / anodic limit fields to the catalog
        # values for the newly-selected coating, but only if the user
        # hasn't manually adjusted them — once the user has typed
        # custom values we don't want a coating switch to silently
        # overwrite them. They can hit "Reset" by re-typing or by
        # selecting a coating that matches what they wanted.
        if not self._limits_user_edited:
            short = self.coating_combo.currentData()
            coat = COATINGS.get(short) if short else None
            if coat is not None:
                # Block our own edited-flag setter so the catalog
                # refresh doesn't get tagged as a user edit.
                for sp in (self.cathodic_limit_v, self.anodic_limit_v):
                    sp.blockSignals(True)
                try:
                    self.cathodic_limit_v.setValue(float(coat.cathodic_limit_v))
                    self.anodic_limit_v.setValue(float(coat.anodic_limit_v))
                finally:
                    for sp in (self.cathodic_limit_v, self.anodic_limit_v):
                        sp.blockSignals(False)
                self._emit_limits()
        self._emit_array()

    def _on_limits_user_edited(self, *_):
        """Fired when the user edits the limit / tolerance spinboxes.

        Sets the ``_limits_user_edited`` flag so a subsequent coating
        change leaves the user's hand-typed values alone. Also emits
        ``potentialLimitsChanged`` so the experiment tabs cache the
        new values for the next run.
        """
        self._limits_user_edited = True
        self._emit_limits()

    def _on_experiment_changed(self, idx: int):
        code = self.experiment_combo.itemData(idx)
        if code in EXPERIMENTS:
            self.experiment_blurb.setText(EXPERIMENTS[code].blurb)

    def _on_open_experiment(self):
        code = self.experiment_combo.currentData()
        if code:
            self.experimentRequested.emit(code)

    # ---------------------------------------------------------------- API
    def current_array(self) -> ElectrodeArray:
        """Build an ElectrodeArray from the current setup-tab state."""
        device_name = self.device_combo.currentText()
        mapping = self.device_view.current_mapping()

        # Resolve area in μm²
        unit_factor = UNITS.get(self.area_unit.currentText(), 1.0)
        area_um2 = float(self.area_value.value()) * unit_factor

        # Resolve coating
        coating_short = self.coating_combo.currentData()
        if coating_short == CUSTOM_COATING_LABEL:
            coating = self.coating_custom.text().strip() or "Custom"
        else:
            coating = coating_short or ""

        # Per-channel overrides — collected when EITHER area or coating
        # is set to Different. ElectrodeArray.from_mapping ignores keys
        # that aren't applicable, so a sparse override dict is fine.
        per_channel = (self.device_view.per_channel_overrides()
                       if ((not self.area_mode.isChecked()) or
                           (not self.coating_mode.isChecked()))
                       else None)

        return ElectrodeArray.from_mapping(
            name=device_name,
            mapping=mapping,
            surface_area_um2=area_um2,
            coating=coating,
            per_channel=per_channel,
        )

    def current_aliases(self) -> dict:
        """Build the {logical_name: 'CHx'} dict the runner uses.

        Logic:
        * Each role assigned to a channel maps that role to the channel.
        * If the user picked a channel as ``Trigger``, use it.
        * If no channel is set as ``Trigger``, fall back to whichever
          channel carries the current monitor (I_mon).
        * Channels set to ``None`` are simply not represented.
        """
        out: dict = {}
        explicit_trigger = None
        imon_ch = None
        for ch, combo in self._role_combos.items():
            role = combo.currentText()
            if role == ROLE_VMON: out["vmon"] = ch
            elif role == ROLE_IMON: out["imon"] = ch; imon_ch = ch
            elif role == ROLE_EACT: out["eact"] = ch
            elif role == ROLE_ERET: out["eret"] = ch
            elif role == ROLE_TRIG: explicit_trigger = ch
        out["trigger"] = explicit_trigger or imon_ch or None
        return out

    def _emit_array(self):
        try:
            arr = self.current_array()
        except Exception:
            return
        self.arrayChanged.emit(arr)

    def _emit_aliases(self):
        self.aliasesChanged.emit(self.current_aliases())

    def _emit_limits(self):
        c, a, t = self.current_potential_limits()
        self.potentialLimitsChanged.emit(c, a, t)

    # ------------------------------------------------------------- limits
    def current_potential_limits(self) -> tuple[float, float, float]:
        """Return ``(cathodic_v, anodic_v, tolerance_v)`` from the live UI.

        Used by the VT tab when constructing the runner so the
        water-window check honours whatever the user typed (or the
        catalog default if they haven't touched it). Tolerance is the
        grace band added to the limit before "limit hit" trips —
        mirrors MATLAB's ``TOL`` constant in
        ``changeCurrent_Fit.m``.
        """
        return (float(self.cathodic_limit_v.value()),
                float(self.anodic_limit_v.value()),
                float(self.polarization_tol_v.value()))

    # ------------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        """Snapshot every user-editable control on this tab."""
        return {
            "notebook": self.notebook.text(),
            "notebook_enabled": self.notebook_check.isChecked(),
            "subject": self.subject.text(),
            "user_name": self.user_name.text(),
            "user_email": self.user_email.text(),
            "save_path": self.save_path.text(),
            "auto_export_xlsx": self.auto_export_xlsx.isChecked(),
            "email_notifications": self.email_notifications.isChecked(),
            "auto_save_plots": self.auto_save_plots.isChecked(),
            "auto_save_plots_fmt": self.current_auto_save_plots_format(),
            "acq_mode": self.acq_mode_combo.currentText(),
            "acq_n_avg": self._current_n_avg(),
            "device": self.device_combo.currentText(),
            "connector": self.connector_combo.currentText(),
            "area_mode": self.area_mode.isChecked(),
            "area_unit": self.area_unit.currentText(),
            "area_value": self.area_value.value(),
            "coating_mode": self.coating_mode.isChecked(),
            "coating": self.coating_combo.currentData(),
            "coating_custom": self.coating_custom.text(),
            # NOTE: cathodic_limit_v / anodic_limit_v / polarization_tol_v
            # are intentionally NOT persisted. The user wants the
            # potential-limit fields to reset to the catalog defaults
            # for the selected coating every session — so any hand-typed
            # override is in-session only and disappears when the GUI
            # restarts. Saving them here would defeat the reset.
            # Per-channel role assignment (new format). The runner builds
            # the (vmon/imon/eret/eact/trigger) -> channel map in
            # current_aliases().
            "channel_roles": {ch: cb.currentText()
                              for ch, cb in self._role_combos.items()},
            "experiment": self.experiment_combo.currentData(),
            # Channel mapping is persisted as a list of lists so a hand-edited
            # mapping survives a restart even if the device default changes
            # later.
            "mapping": self.device_view.current_mapping().tolist(),
        }

    def restore_prefs(self, p: dict):
        """Apply a prefs dict back into the controls.

        Order matters: device first (so the mapping has the right shape),
        then mapping override, then the rest. We block signals during
        the bulk update and emit a single `arrayChanged` at the end so
        the experiment tabs only see one rebuild.
        """
        if not p:
            return
        # Device + connector + coating before everything else
        if "device" in p:
            self.device_combo.blockSignals(True)
            self.device_combo.setCurrentText(p["device"])
            self.device_combo.blockSignals(False)
            # Manually populate the device view since we suppressed the signal
            from ..config import DEVICES
            if p["device"] in DEVICES:
                self.device_view.set_device(DEVICES[p["device"]])
        if "connector" in p:
            self.connector_combo.setCurrentText(p["connector"])
        # Re-apply a saved hand-edited mapping over the device default
        if "mapping" in p and p["mapping"]:
            try:
                import numpy as np
                self.device_view.set_mapping(np.asarray(p["mapping"], dtype=int))
            except Exception:
                pass
        # Area + coating
        if "area_mode" in p:
            am = p["area_mode"]
            # New format: bool. Legacy: "Same…" / "Different…" string.
            if isinstance(am, bool):
                self.area_mode.setChecked(am)
            else:
                self.area_mode.setChecked(str(am) == SAME_VAL)
        if "area_unit" in p: self.area_unit.setCurrentText(p["area_unit"])
        if "area_value" in p:
            try: self.area_value.setValue(float(p["area_value"]))
            except (TypeError, ValueError): pass
        if "coating_mode" in p:
            cm = p["coating_mode"]
            # New format: bool (True = Same). Legacy format: string
            # ("Same for all electrodes" / "Different per electrode").
            if isinstance(cm, bool):
                self.coating_mode.setChecked(cm)
            else:
                self.coating_mode.setChecked(str(cm) == SAME_VAL)
        if "coating" in p:
            # Saved as the short / canonical name. Find by userData so
            # both old prefs ("SIROF") and new prefs work.
            idx = self.coating_combo.findData(p["coating"])
            if idx >= 0:
                self.coating_combo.setCurrentIndex(idx)
            else:
                # Legacy prefs may have stored the visible label instead;
                # try matching by display text as a fallback.
                idx = self.coating_combo.findText(p["coating"])
                if idx >= 0:
                    self.coating_combo.setCurrentIndex(idx)
        if "coating_custom" in p: self.coating_custom.setText(p["coating_custom"])
        # Potential limits + tolerance are NOT restored from prefs by
        # design — they reset to the catalog defaults each session.
        # The coating change above (or the device-default coating
        # applied by ``_on_device_changed``) refills them via
        # ``_on_coating_changed`` while ``_limits_user_edited`` is
        # still False from __init__, so the user always starts a
        # session with the catalog's nominal values.
        # Session
        if "notebook" in p: self.notebook.setText(p["notebook"])
        if "notebook_enabled" in p:
            try:
                self.notebook_check.setChecked(bool(p["notebook_enabled"]))
            except Exception:
                pass
        if "subject" in p: self.subject.setText(p["subject"])
        # Re-emit the composed log filename so the on-disk mirror
        # picks up the restored notebook/session strings on launch.
        QtCore.QTimer.singleShot(0, self._emit_session_filename)
        if "user_name" in p: self.user_name.setText(p["user_name"])
        if "user_email" in p: self.user_email.setText(p["user_email"])
        if "save_path" in p and p["save_path"]:
            self.save_path.setText(p["save_path"])
            QtCore.QTimer.singleShot(0, self._emit_save_path)
        if "auto_export_xlsx" in p:
            try:
                self.auto_export_xlsx.setChecked(bool(p["auto_export_xlsx"]))
                QtCore.QTimer.singleShot(0, lambda: self._emit_auto_export_xlsx(
                    self.auto_export_xlsx.isChecked()))
            except Exception:
                pass
        if "email_notifications" in p:
            try:
                self.email_notifications.setChecked(bool(p["email_notifications"]))
                QtCore.QTimer.singleShot(0, lambda: self._emit_email_notifications(
                    self.email_notifications.isChecked()))
            except Exception:
                pass
        if "auto_save_plots_fmt" in p:
            try:
                fmt = str(p["auto_save_plots_fmt"]).lower().lstrip(".")
                idx = self.auto_save_plots_fmt.findData(fmt)
                if idx >= 0:
                    self.auto_save_plots_fmt.setCurrentIndex(idx)
            except Exception:
                pass
        if "auto_save_plots" in p:
            try:
                self.auto_save_plots.setChecked(bool(p["auto_save_plots"]))
                # Combo enable-state is wired to the checkbox toggle
                # signal — manually setting ``setChecked`` to the same
                # value as the default doesn't fire ``toggled``, so
                # mirror it here for the initial state.
                self.auto_save_plots_fmt.setEnabled(
                    self.auto_save_plots.isChecked())
                QtCore.QTimer.singleShot(0, self._emit_auto_save_plots)
            except Exception:
                pass
        if "acq_mode" in p:
            try: self.acq_mode_combo.setCurrentText(str(p["acq_mode"]))
            except Exception: pass
        if "acq_n_avg" in p:
            try:
                n = int(p["acq_n_avg"])
                if self.acq_navg_combo is not None:
                    idx = self.acq_navg_combo.findData(n)
                    if idx >= 0:
                        self.acq_navg_combo.setCurrentIndex(idx)
                else:
                    self.acq_navg_spin.setValue(n)
            except (TypeError, ValueError):
                pass
        # Scope mapping
        # New per-channel role format
        if isinstance(p.get("channel_roles"), dict):
            for ch, role in p["channel_roles"].items():
                if ch in self._role_combos and role in SCOPE_ROLES:
                    self._role_combos[ch].setCurrentText(role)
        else:
            # Legacy format: {vmon_ch: 'CH1', imon_ch: 'CH2', ...}
            # — translate into role-by-channel before applying.
            legacy = {
                "vmon_ch": ROLE_VMON, "imon_ch": ROLE_IMON,
                "eret_ch": ROLE_ERET, "eact_ch": ROLE_EACT,
            }
            for key, role in legacy.items():
                ch = p.get(key)
                if ch in self._role_combos:
                    self._role_combos[ch].setCurrentText(role)
        # All 4 channel rows stay visible until a scope actually
        # connects (we don't constrain when no hardware is known).
        # apply_scope_capabilities will hide CH3/CH4 — and reset
        # their roles to None — when a 2-channel scope shows up,
        # so the role assignments restored above can never leak
        # into current_aliases() with the wrong scope.
        self._set_visible_scope_channels(4)
        # Experiment dropdown
        if "experiment" in p:
            idx = self.experiment_combo.findData(p["experiment"])
            if idx >= 0: self.experiment_combo.setCurrentIndex(idx)
        self._emit_array()
        self._emit_aliases()
        self._emit_limits()

    # ------------------------------------------------------------- view state
    def view_state(self) -> dict:
        """Snapshot the panel/column sizes the user can drag.

        Two pieces here: the SetupTab's top-level horizontal splitter
        (forms vs. device view) and the DeviceView's own splitter
        (geometry visualizer vs. mapping table) plus the column widths
        of its mapping and per-channel tables.
        """
        return {
            "main_split": list(self._main_split.sizes()),
            "device_view": self.device_view.view_state(),
        }

    def restore_view_state(self, view: dict):
        if not view:
            return
        sizes = view.get("main_split")
        if isinstance(sizes, (list, tuple)) and len(sizes) >= 2:
            try:
                self._main_split.setSizes([int(s) for s in sizes])
            except (TypeError, ValueError):
                pass
        if isinstance(view.get("device_view"), dict):
            self.device_view.restore_view_state(view["device_view"])
