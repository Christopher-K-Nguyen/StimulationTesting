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

from typing import Dict, Optional

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
from .repeating_spinbox import (RepeatingDoubleSpinBox, RepeatingSpinBox,
                                ScientificDoubleSpinBox)


CUSTOM_COATING_LABEL = "Custom…"
#: Device-combo entry that triggers the custom-device name dialog.
#: Selecting this entry pops :class:`_CustomElectrodeNameDialog` (it
#: takes a generic ``kind_label``); on Confirm the typed name becomes
#: a new device-combo entry whose per-device state (layout + mapping)
#: lives in ``SetupTab._device_custom_state``.
CUSTOM_DEVICE_TRIGGER = "Other (custom grid)"
SAME_VAL = "Same for all electrodes"
DIFF_VAL = "Different per electrode"

# Estimated open-circuit potential of common return / counter electrode
# materials, expressed as VOLTS vs Ag|AgCl. Used to label the return
# coating dropdown so the user immediately sees roughly where the return
# electrode floats relative to a reference Ag|AgCl. Values mirror the
# ``refPotential_arr`` / ``counterPotential_arr`` tables in the MATLAB
# ``getReferenceElectrode.m`` for IN-VITRO (saline / PBS) measurements.
# In animal preparations every counter potential collapses toward 0 V;
# the in-vitro values are the right default for benchtop testing and
# the GUI tooltip surfaces the ``in-animal ≈ 0 V`` caveat.
#
# Mapping is keyed by the catalog ``Coating.name`` short codes — the
# same strings stored in ``COATINGS``. The Ir-based oxide coatings
# (SIROF, AIROF) take the elemental Ir value (0.1 V); TiN takes the
# elemental Ti value (0.0 V); PEDOT:PSS doesn't have a published OCP in
# the MATLAB catalog so it gets ``None`` and the label reads ``n/a``.
COATING_OCP_VS_AG_AG_CL_V: Dict[str, Optional[float]] = {
    "SIROF":         +0.100,   # Ir oxide — uses Ir baseline
    "AIROF":         +0.100,
    "TiN":           +0.000,   # Ti baseline
    "Pt":            +0.200,
    "PtIr (90/10)":  +0.200,
    "PtIr (80/20)":  +0.200,
    "PtIr (70/30)":  +0.200,
    "PEDOT:PSS":     None,
    "Au":            +0.300,
    "W":             -0.300,
    "Ti":            +0.000,   # passive TiO2 — pinned at Ti baseline
    "SS":            +0.000,   # 316L passive Cr-oxide surface
    # ``Custom`` return coating — out-of-catalog material. Treat as
    # 0 V vs Ag|AgCl so no automatic shift applies; the user can
    # hand-edit the cathodic / anodic limits if they need a different
    # baseline.
    "Custom":        +0.000,
}

# Reference-electrode catalog from MATLAB ``getReferenceElectrode.m``
# ``refList`` / ``refPotential_arr`` (in-vitro). The water-window
# limits in :data:`COATINGS` are stored in volts vs Ag|AgCl; selecting
# a different reference subtracts that reference's OCP from the
# displayed limits to translate them into ``V vs <ref>``. The keys are
# the human-readable names that show up in the GUI dropdown.
# Surface-area spinbox increment per unit. The values are picked so a
# single step is ~"a meaningful nudge" at the scale users typically
# work with: 100 µm² for sub-millimeter pads, 0.1 mm² for tissue-scale
# electrodes, 1 cm² for benchtop coupons. Keys must match the strings
# in :data:`UNITS`.
AREA_STEPS_BY_UNIT: Dict[str, float] = {
    "μm²": 100.0,
    "mm²": 0.1,
    "cm²": 1.0,
}

# Surface-area spinbox decimal places per unit. Matched to typical
# fab tolerances at each scale: µm² are quoted as integers (a 5000
# µm² pad isn't reported as 5000.000); mm² are typically two-decimal
# (0.05 mm² ≈ 50 000 µm²); cm² resolve to one decimal (0.1 cm² ≈
# 10 mm²). Live-updated by ``_on_area_unit_changed``.
AREA_DECIMALS_BY_UNIT: Dict[str, int] = {
    "μm²": 0,
    "mm²": 2,
    "cm²": 1,
}


REF_AG_AGCL = "Ag|AgCl"
#: Short code for an out-of-catalog material the user wants to label
#: themselves. Treated as 0 V vs Ag|AgCl (no shift) — the user can
#: edit the cathodic / anodic limits manually if they know better.
REF_CUSTOM = "Custom"
REFERENCE_ELECTRODES_OCP_V: Dict[str, float] = {
    REF_AG_AGCL:  0.000,
    "Pt":         +0.200,
    "PtIr":       +0.200,
    "SS":          0.000,
    "Ir":         +0.100,
    "Ti":          0.000,
    "W":          -0.300,
    "Au":         +0.300,
    REF_CUSTOM:    0.000,
}

# Spelled-out display names for the reference dropdown — the
# abbreviation in parentheses matches the convention used by the
# active-electrode coating combo (``Coating.display_name``). Keys are
# the canonical short codes from :data:`REFERENCE_ELECTRODES_OCP_V`;
# the short code is what gets stored as ``userData`` and persisted in
# prefs, so the user-visible label can change here without breaking
# existing saved profiles.
REFERENCE_ELECTRODES_DISPLAY: Dict[str, str] = {
    REF_AG_AGCL: "Silver / silver chloride (Ag|AgCl)",
    "Pt":        "Platinum (Pt)",
    "PtIr":      "Platinum-iridium (PtIr)",
    "SS":        "Stainless steel (SS)",
    "Ir":        "Iridium (Ir)",
    "Ti":        "Titanium (Ti)",
    "W":         "Tungsten (W)",
    "Au":        "Gold (Au)",
    REF_CUSTOM:  "Custom",
}

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

# Per-channel analog-bandwidth override (Setup tab, in line with the role
# dropdown).  "Auto" = the automatic policy (full BW on data channels, 20 MHz
# on the trigger channel for a clean comparator — gotcha #162); a concrete
# choice overrides that channel.
SCOPE_BW_AUTO  = "Auto"
SCOPE_BW_FULL  = "Full"
SCOPE_BW_20MHZ = "20 MHz"
SCOPE_BANDWIDTHS = (SCOPE_BW_AUTO, SCOPE_BW_FULL, SCOPE_BW_20MHZ)
# Per-channel input-coupling override.  "Auto" = the role-based default (DC for
# V_mon / I_mon / electrodes, AC for a distinct digital trigger — gotcha #83);
# "DC" / "AC" force that raw coupling.  "DC + AC" runs the capture-in-DC-then-AC
# fine-scale trick (gotcha #85): capture DC to read the rest potential, switch
# to AC so the small swing fine-scales, then sum the DC offset back on save.
# Setting "DC + AC" on an electrode channel (E_ret / E_act) is the ONLY control
# for that trick — the old global "Electrode coupling" dropdown in the
# acquisition group was REMOVED because the per-channel dropdown supersedes it
# (operator: "remove electrode coupling since the oscilloscope channels can set
# it").
SCOPE_COUP_AUTO  = "Auto"
SCOPE_COUP_DC    = "DC"
SCOPE_COUP_AC    = "AC"
SCOPE_COUP_DC_AC = "DC + AC"
SCOPE_COUPLINGS = (SCOPE_COUP_AUTO, SCOPE_COUP_DC, SCOPE_COUP_AC,
                   SCOPE_COUP_DC_AC)
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


class _CustomElectrodeNameDialog(QtWidgets.QDialog):
    """Pop-up that asks the user to type a name for a custom electrode.

    Triggered when the active / return / reference dropdown is set to
    its ``Custom…`` entry. The typed name becomes a new entry in the
    same dropdown and is selected automatically.

    Buttons:

    * **Confirm** — accept the current text and close. The caller
      reads :attr:`name` and registers it in the dropdown.
    * **Try Again** — clear the field and keep the dialog open, so
      the user can retype without dismissing first. The dialog stays
      modal during this — caller still loops on the result code.
    * **Cancel** — discard the name and close. The caller reverts
      the dropdown to the previously-selected entry.

    The dialog enforces non-empty input only — anything else is left
    to the caller (uniqueness check vs. existing dropdown entries,
    sanitisation for prefs, etc.).
    """

    # Custom result codes used in addition to ``QDialog.Accepted`` /
    # ``Rejected`` so the caller can distinguish "Try Again" from
    # "Cancel". Both close the dialog; the caller's loop decides
    # whether to re-show.
    RESULT_CONFIRM   = 1
    RESULT_TRY_AGAIN = 2
    RESULT_CANCEL    = 0

    def __init__(self, kind_label: str,
                 prefill: str = "",
                 parent: QtWidgets.QWidget | None = None):
        """``kind_label`` is the human-readable target for the title
        bar — e.g. "active electrode", "return electrode", "reference
        electrode". ``prefill`` seeds the input (used by Try Again to
        bring back the previously-typed name)."""
        super().__init__(parent)
        self.setWindowTitle(f"Name custom {kind_label}")
        self.setModal(True)
        self._result = self.RESULT_CANCEL

        prompt = QtWidgets.QLabel(
            f"Enter a name for the custom {kind_label}:")
        prompt.setWordWrap(True)
        self.name_edit = QtWidgets.QLineEdit()
        self.name_edit.setPlaceholderText(f"Custom {kind_label} name")
        self.name_edit.setText(prefill)
        self.name_edit.setMinimumWidth(260)
        self.name_edit.setToolTip(
            f"Name for this custom {kind_label}. Saved to the "
            f"catalog so it appears in the dropdown next session. "
            f"Use a descriptive name including geometry and "
            f"coating (e.g. 'UTD 4×4 Pt-black 5 ks').")

        # Three buttons in their own row so the user reads them
        # left-to-right: Confirm, Try Again, Cancel.
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        confirm = QtWidgets.QPushButton("Confirm")
        confirm.setDefault(True)
        confirm.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm)
        again = QtWidgets.QPushButton("Try Again")
        again.clicked.connect(self._on_try_again)
        btn_row.addWidget(again)
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.clicked.connect(self._on_cancel)
        btn_row.addWidget(cancel)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(prompt)
        layout.addWidget(self.name_edit)
        layout.addLayout(btn_row)
        self.name_edit.setFocus()

    @property
    def name(self) -> str:
        """Sanitised typed name — stripped, never empty when
        :attr:`result_code` is ``RESULT_CONFIRM``."""
        return self.name_edit.text().strip()

    @property
    def result_code(self) -> int:
        return self._result

    def _on_confirm(self):
        if not self.name:
            # Empty name — treat as Try Again so the user is forced
            # to type something before Confirm closes the dialog.
            self.name_edit.setFocus()
            return
        self._result = self.RESULT_CONFIRM
        self.accept()

    def _on_try_again(self):
        # Clear the field, keep the dialog open so the user can
        # retype. Returning here without ``accept()`` / ``reject()``
        # would leave the dialog open AND the caller blocked on
        # exec(); instead close with the Try-Again sentinel and let
        # the caller's loop re-instantiate.
        self._result = self.RESULT_TRY_AGAIN
        self.reject()

    def _on_cancel(self):
        self._result = self.RESULT_CANCEL
        self.reject()


class SetupTab(QtWidgets.QWidget):
    """Top-level setup tab with rich-text labels and DeviceView."""
    arrayChanged = QtCore.pyqtSignal(object)         # ElectrodeArray
    aliasesChanged = QtCore.pyqtSignal(dict)         # {logical: 'CHx'}
    experimentRequested = QtCore.pyqtSignal(str)     # experiment code (VT/SP/LP/PS)
    #: Fired by the "Go to test parameters tab →" button. Pure
    #: navigation request — the main window switches to the Test
    #: parameters tab WITHOUT touching the currently-loaded
    #: experiment content. The experiment swap itself is driven by
    #: :attr:`experimentRequested`, which now fires the moment the
    #: dropdown selection changes, so by the time the user clicks
    #: this button the Test parameters tab is already showing the
    #: right experiment's params page.
    testParamsRequested = QtCore.pyqtSignal()
    savePathChanged = QtCore.pyqtSignal(str)         # absolute path to save dir
    # (acquisition_mode, n_avg) — runner-side code applies these to the
    # scope before each capture. n_avg is ignored for SAMPLE mode.
    acquisitionChanged = QtCore.pyqtSignal(str, int)
    # Horizontal auto-fit window preference — "wide" (more post-pulse
    # recovery) or "tight" (the pulse fills more of the screen).  Applied to
    # the scope's ``set_horizontal_fit_mode`` (the ``auto_layout_for_pulse``
    # fill floor).
    horizontalScalingChanged = QtCore.pyqtSignal(str)
    # Trigger source selection — "EXT" for digital sync, the physical
    # channel name of a Trigger-role channel (e.g. "CH4"), or the
    # I_mon channel name (e.g. "CH2") as the fallback.
    triggerSourceChanged = QtCore.pyqtSignal(str)
    # True when the resolved trigger source is a TTL sync line (EXT
    # checkbox or a channel with Role=Trigger).  Distinguishes the two
    # different trigger physics: TTL sync = RISE @ 1.4 V regardless of
    # pulse polarity; I_mon = polarity-derived slope + amplitude-
    # derived level.  Emitted in lockstep with triggerSourceChanged
    # so the experiment tabs always have a consistent (source, type)
    # pair to act on.
    digitalTriggerChanged = QtCore.pyqtSignal(bool)
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
    # Emitted as ``(phone, carrier_key)`` when the user edits the phone or
    # picks a carrier — the runner uses these for run-end TEXT alerts.
    smsRecipientChanged = QtCore.pyqtSignal(str, str)
    # Emitted with the raw Session text so the runner can put it in
    # the email subject. Distinct from ``sessionFilenameChanged``,
    # which carries the composed log-filename stem.
    sessionSubjectChanged = QtCore.pyqtSignal(str)
    # Emitted as ``(enabled, fmt, dpi)`` whenever the user flips the
    # "Save plots after session" checkbox, picks a different file
    # format from the adjacent dropdown, or edits the DPI spinbox.
    # Format is one of ``png`` / ``jpg`` / ``tif`` / ``svg`` (lowercase,
    # no dot); ``dpi`` is the raster resolution for the saved figure
    # (ignored for the vector ``svg`` format).
    autoSavePlotsChanged = QtCore.pyqtSignal(bool, str, int)
    # Emitted as ``(cathodic_v, anodic_v, tolerance_v)`` whenever the
    # user adjusts a potential-limit spinbox or picks a different
    # coating that auto-fills new values. The VT runner uses these
    # in place of the catalog defaults so the user can override per
    # session.
    potentialLimitsChanged = QtCore.pyqtSignal(float, float, float)
    # Emitted as ``(short_code, custom_text)`` whenever the user
    # changes the Environment dropdown or edits the Custom-text
    # box. ``custom_text`` is "" for non-custom presets. Wired
    # into MainWindow so the runner-side warning synthesizer
    # (:mod:`stimtest.damage_warnings`) sees the same value the
    # GUI is showing without re-querying the combo.
    environmentChanged = QtCore.pyqtSignal(str, str)
    # Emitted with the short_code of the selected sparge-gas
    # preset (``"none"``, ``"n2"``, or ``"ar"``) whenever the
    # user changes the dropdown. Sparge gas is metadata only —
    # no model consumes it — but downstream consumers (the
    # electrode-potential learning store, the contribute-data
    # payload) read it for cohort provenance.
    spargeGasChanged = QtCore.pyqtSignal(str)
    # General-purpose "a Setup input changed" signal carrying a ready-to-log
    # human-readable "<field> = <value>" string.  Used for the electrode-
    # config inputs (return / reference electrode, geometry, connector,
    # remember-potential toggle) that otherwise only fold into the generic
    # ``arrayChanged`` metadata line and so were NOT individually indicated in
    # the log pane (operator: "changing the return electrode, there was not new
    # text in the log pane … make sure that all input and selection are
    # indicated").  MainWindow connects this straight to ``_log_setup_change``.
    settingChanged = QtCore.pyqtSignal(str)

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
        self.notebook.setToolTip(
            "Notebook / project identifier. Prefixed onto every "
            "session's filenames so a day's worth of captures from "
            "different electrodes share a common parent label. "
            "Untick the Notebook checkbox to omit the prefix.")
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
        self.subject.setToolTip(
            "Identifier for the electrode / sample under test. "
            "Suffixed onto the notebook prefix to form the unique "
            "filename (e.g. 'session_001_electrode_a1_VT.npz'). "
            "Change between electrodes so files don't overwrite "
            "each other.")
        self.user_name = QtWidgets.QLineEdit()
        self.user_name.setToolTip(
            "Operator name — recorded in every saved session as "
            "provenance metadata. Optional but recommended for "
            "multi-user labs.")
        self.user_email = QtWidgets.QLineEdit()
        self.user_email.setToolTip(
            "Operator email — recorded with the session metadata "
            "and used as attribution if you opt in to contribute "
            "anonymised electrode data. Optional.")
        self.user_name.editingFinished.connect(self._emit_user_identity)
        self.user_email.editingFinished.connect(self._emit_user_identity)
        # Optional phone + carrier for run-end TEXT notifications via the
        # email-to-SMS gateway (operator: "Allow for a phone number option
        # for text messages").  Like email, gated by the notifications
        # toggle below and the SMTP credentials.
        self.user_phone = QtWidgets.QLineEdit()
        self.user_phone.setPlaceholderText("e.g. 8015551234 (digits only)")
        self.user_phone.setToolTip(
            "Mobile number (digits only) for run-end text alerts via your "
            "carrier's email-to-SMS gateway. Needs the carrier set and the "
            "notifications toggle on. Optional.")
        self.user_carrier = QtWidgets.QComboBox()
        self.user_carrier.addItem("(no text)", "")
        from ..notifications import SMS_GATEWAYS
        # Friendly display labels for the common carriers; the userData is
        # the gateway key send_sms_via_gateway expects.
        _carrier_labels = {
            "att": "AT&T", "verizon": "Verizon", "tmobile": "T-Mobile",
            "sprint": "Sprint", "uscellular": "US Cellular", "boost": "Boost",
            "cricket": "Cricket", "metropcs": "Metro by T-Mobile",
            "virgin": "Virgin Mobile", "tracfone": "Tracfone",
            "alltel": "Alltel", "nextel": "Nextel",
            "cingular": "Cingular", "cingular2": "Cingular (alt)",
        }
        for _key in SMS_GATEWAYS:
            self.user_carrier.addItem(_carrier_labels.get(_key, _key.title()),
                                      _key)
        self.user_carrier.setToolTip(
            "Mobile carrier — selects the email-to-SMS gateway used to "
            "deliver the text. (Gateways can be unreliable; email is more "
            "dependable.)")
        self.user_phone.editingFinished.connect(self._emit_sms_recipient)
        self.user_carrier.currentIndexChanged.connect(
            self._emit_sms_recipient)
        # Institution / company affiliation. Persisted alongside the
        # user identity so the data-contribution dialog
        # (Help → Contribute electrode data…) can pre-populate the
        # "include institution" attribution field. Optional — left
        # blank for users who don't want to associate their data with
        # a lab name; the contribute flow defaults the include-toggle
        # to OFF so a non-empty value here is necessary AND
        # sufficient consent. Stored as a free-form string so
        # academic users can enter "Lab of <PI>, <University>" etc.
        # without dropdowns getting in the way.
        self.user_institution = QtWidgets.QLineEdit()
        self.user_institution.setPlaceholderText(
            "e.g. Solzbacher Lab, University of Utah")
        self.user_institution.setToolTip(
            "Optional. Used as attribution if you opt in to contribute "
            "anonymised electrode data via Help → Contribute electrode "
            "data… The value is saved with your session prefs but "
            "never auto-uploaded.")
        self.user_institution.editingFinished.connect(self._emit_user_identity)

        # ---- Environment (PBS / mISF / rat cortex / cell culture / …) ----
        # Drives the warning posture for the pre-run damage screen
        # (see :mod:`stimtest.damage_warnings`). The picker is a
        # combo populated from :data:`stimtest.environments.ENVIRONMENT_PRESETS`;
        # ``Custom`` reveals an adjacent free-form text box. The
        # short_code is what gets stamped into the setup snapshot
        # and persisted to prefs — display strings can change
        # between releases without forfeiting saved sessions.
        from ..environments import (
            ENVIRONMENT_PRESETS as _ENV_PRESETS,
            DEFAULT_ENVIRONMENT as _ENV_DEFAULT,
        )
        self.environment_combo = QtWidgets.QComboBox()
        self.environment_combo.setToolTip(
            "Electrolyte / environment the electrode sits in. "
            "Drives the pre-run damage-screen posture (info / warn "
            "/ alert) and the safe-stim envelope. PBS is the bench "
            "benchmark; tissue / cell-culture environments warn "
            "more aggressively because biological substrates are "
            "less forgiving than buffer.")
        for preset in _ENV_PRESETS:
            # User data is the stable short_code; the visible
            # text is the display name. Tooltip carries the
            # one-line description so a hover explains what
            # "mISF" or "aCSF" means without having to click.
            self.environment_combo.addItem(
                preset.display_name, userData=preset.short_code)
            idx = self.environment_combo.count() - 1
            self.environment_combo.setItemData(
                idx, preset.description,
                QtCore.Qt.ItemDataRole.ToolTipRole)
        # Default to PBS — the safest "I haven't picked anything
        # yet" choice (info posture, benchtop buffer).
        default_idx = self.environment_combo.findData(_ENV_DEFAULT)
        if default_idx < 0:
            default_idx = 0
        self.environment_combo.setCurrentIndex(default_idx)
        self.environment_combo.currentIndexChanged.connect(
            self._on_environment_changed)
        # Custom-text input — only visible when the user picks
        # the ``custom`` preset, mirroring the other Custom-
        # electrode pop-up fields elsewhere in the Setup tab.
        # Held inline rather than via a pop-up dialog because
        # environment is a session-level setting (not a per-
        # capture pick) and a one-shot pop-up would feel heavy.
        self.environment_custom = QtWidgets.QLineEdit()
        self.environment_custom.setPlaceholderText(
            "Describe your custom environment (e.g. 'modified PBS "
            "with 2 mM EDTA at 37 °C')")
        self.environment_custom.setVisible(False)
        self.environment_custom.editingFinished.connect(
            self._emit_environment)
        self.environment_custom.setToolTip(
            "Free-form description of a non-preset environment. "
            "Saved into the session metadata so later analysis can "
            "filter by it. Visible only when 'Custom' is the "
            "selected environment preset.")

        # ---- Gas sparging dropdown ----------------------------------------
        # Atmosphere control over the electrolyte. None (ambient
        # air) is the bench default; N₂ / Ar deoxygenate the
        # solution so the cathodic limit reflects water-only
        # reduction rather than O₂-driven cathodic chemistry. The
        # value is metadata only — no Shannon / NeurostimML model
        # has ever been trained on it — but downstream consumers
        # (the OCP learning store, the contribute-data payload,
        # the saved-session export) read it for cohort analysis,
        # and a future drift study could compare PtIr OCPs in
        # N₂-sparged PBS vs ambient PBS without having to re-tag
        # legacy data.
        from ..environments import (
            SPARGE_GAS_PRESETS as _SPARGE_PRESETS,
            DEFAULT_SPARGE_GAS as _SPARGE_DEFAULT,
        )
        self.sparge_gas_combo = QtWidgets.QComboBox()
        self.sparge_gas_combo.setToolTip(
            "Gas the electrolyte is sparged with. "
            "None = ambient air (default); N₂ / Ar deoxygenate "
            "the solution so the cathodic safe-stim limit reflects "
            "water-only reduction instead of O₂-driven cathodic "
            "chemistry. Metadata only — no model conditions on "
            "this today, but it's recorded for cohort analysis.")
        for short, display, tooltip in _SPARGE_PRESETS:
            self.sparge_gas_combo.addItem(display, userData=short)
            idx = self.sparge_gas_combo.count() - 1
            self.sparge_gas_combo.setItemData(
                idx, tooltip, QtCore.Qt.ItemDataRole.ToolTipRole)
        # Default to ``none`` — matches the freshly-prepared cell
        # state most users start from.
        default_idx = self.sparge_gas_combo.findData(_SPARGE_DEFAULT)
        if default_idx < 0:
            default_idx = 0
        self.sparge_gas_combo.setCurrentIndex(default_idx)
        self.sparge_gas_combo.currentIndexChanged.connect(
            self._on_sparge_gas_changed)
        # Save path — folder where session .npz files land. Edited as
        # text or via the Browse… button. Defaults to the project's
        # ``data`` folder, made absolute on first emit.
        self.save_path = QtWidgets.QLineEdit(str(Path(DEFAULT_SAVE_DIR).resolve()))
        self.save_path.setToolTip(
            "Folder where session .npz files (and optional .xlsx "
            "exports / autosaved plots) land. Use Browse… to pick "
            "interactively. Path is made absolute on commit.")
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
        self.auto_export_xlsx.setToolTip(
            "When checked, every experiment writes a "
            "Gamry-DTA-style .xlsx workbook next to the .npz "
            "session after the run completes. The .xlsx export "
            "is slower than the .npz one — off by default.")
        # Email notification toggle — mirrors the MATLAB sendEmail /
        # sendError workflow. When checked AND ``user_email`` is set
        # AND SMTP credentials are configured (env vars or
        # ``~/.stimtest/email_config.json``), the runner emails the
        # user at the end of every run with the saved .npz attached.
        # Off by default — the user has to opt in.
        self.email_notifications = QtWidgets.QCheckBox(
            "Notify on finish (email / text)")
        self.email_notifications.setToolTip(
            "At the end of every run (success or failure) email the User "
            "email and/or text the User phone (if a carrier is set). "
            "Requires SMTP credentials in env vars STIMTEST_SMTP_USER / "
            "STIMTEST_SMTP_PASSWORD or in ~/.stimtest/email_config.json. "
            "Off by default.")
        self.email_notifications.toggled.connect(
            self._emit_email_notifications)
        # Auto-save channel / combination plots after each run. Off by
        # default; when on, the runner calls ``export_session_plots``
        # using the format picked from the adjacent dropdown. Mirrors
        # the manual "Export plot" button on the Results tab.
        self.auto_save_plots = QtWidgets.QCheckBox(
            "Save plots during session")
        self.auto_save_plots.setToolTip(
            "When checked, each channel/combination's plot is written "
            "next to the .npz IN REAL TIME as that channel completes "
            "(no end-of-session save burst). Manual export from the "
            "Results tab still works either way.")
        self.auto_save_plots_fmt = QtWidgets.QComboBox()
        self.auto_save_plots_fmt.setToolTip(
            "File format for the auto-saved plots: PNG for "
            "quick review, SVG for vector quality, TIF "
            "for journals that require it. Only used when "
            "'Save plots during session' is on.")
        # Display labels match the actual file extension written
        # (operator: "Why is the toggle called TIFF when the files are
        # TIF") — the data value is the lowercase extension matplotlib
        # infers the format from.
        for label, ext in (("PNG", "png"), ("JPEG", "jpg"),
                           ("TIF", "tif"), ("SVG", "svg")):
            self.auto_save_plots_fmt.addItem(label, ext)
        # Default to TIF — matches MATLAB ``-r600`` and the catalog
        # plotting helpers' default extension.
        self.auto_save_plots_fmt.setCurrentIndex(2)
        # Raster resolution for the saved figure (matplotlib ``-rNNN``).
        # A fixed dropdown of the standard print resolutions (operator
        # request) rather than a free spinbox.  Default 600 (==
        # plotting.DEFAULT_DPI / MATLAB ``-r600``).  Disabled for the
        # vector ``svg`` format (DPI is meaningless there).
        self.auto_save_plots_dpi = QtWidgets.QComboBox()
        for _dpi in (300, 600, 900, 1200):
            self.auto_save_plots_dpi.addItem(f"{_dpi} DPI", _dpi)
        self.auto_save_plots_dpi.setCurrentIndex(1)   # 600
        self.auto_save_plots_dpi.setToolTip(
            "Resolution of the saved raster plots (matplotlib -rNNN). "
            "600 matches the lab's MATLAB export; raise it for print, "
            "lower it for smaller files. Not used for the vector SVG "
            "format. Only used when 'Save plots during session' is on.")
        # Greyed out until the toggle is on so the user reads the
        # combo + DPI as conditional on the checkbox.  The DPI combo is
        # ALSO greyed when the format is SVG (vector → DPI irrelevant);
        # ``_update_dpi_enabled`` is the single apply point for that
        # combined gate.
        self.auto_save_plots_fmt.setEnabled(False)
        self.auto_save_plots_dpi.setEnabled(False)
        self.auto_save_plots.toggled.connect(
            self.auto_save_plots_fmt.setEnabled)
        self.auto_save_plots.toggled.connect(
            self._update_dpi_enabled)
        self.auto_save_plots.toggled.connect(
            self._emit_auto_save_plots)
        self.auto_save_plots_fmt.currentIndexChanged.connect(
            self._update_dpi_enabled)
        self.auto_save_plots_fmt.currentIndexChanged.connect(
            self._emit_auto_save_plots)
        self.auto_save_plots_dpi.currentIndexChanged.connect(
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
        # The Plexon Test Board is FIRST in the list (operator: "top of the
        # list") but should NOT be the fresh-install DEFAULT — default to a
        # real electrode array so a new user lands on an electrode device
        # (with the coating/geometry/area options visible).  Set the index
        # BEFORE wiring the signal so this doesn't fire `_on_device_changed`
        # (the explicit call at the end of __init__ applies it); prefs restore
        # overrides it for returning users.
        _def_idx = self.device_combo.findText("Linear")
        if _def_idx >= 0:
            self.device_combo.setCurrentIndex(_def_idx)
        self.device_combo.currentTextChanged.connect(self._on_device_changed)
        self.device_combo.setToolTip(
            "Choose the electrode array model. Selecting a device "
            "auto-fills its recommended electrode area, default "
            "coating, and connector pinout (you can still override "
            "any of those below).")

        # Grid-type chooser — only meaningful for the *Other (custom
        # grid)* device, where the user authors the mapping by hand
        # and can tell us whether the physical packing is a square
        # rectangular lattice or an equilateral hexagonal one (which
        # changes how the geometry view draws disks, and tells the
        # combinations panel to suppress the orthogonal-vs-diagonal
        # toggle since hex neighbours are equidistant). Built-in
        # devices already carry their own ``layout`` flag so this
        # chooser is hidden for them.
        self.grid_type_combo = QtWidgets.QComboBox()
        self.grid_type_combo.addItem("Square (rectangular)", userData="rect")
        self.grid_type_combo.addItem("Hexagonal (triangular)",
                                     userData="triangular")
        self.grid_type_combo.setCurrentIndex(0)
        self.grid_type_combo.setToolTip(
            "Physical packing of your custom grid. Square = aligned "
            "rectangular lattice (every cell at integer (row, col) "
            "with equal pitch). Hexagonal = equilateral triangular "
            "lattice (odd rows offset by half a cell, vertical pitch "
            "× √3/2). Hexagonal hides the “Include diagonal” toggle "
            "in the configurations panel since every immediate "
            "neighbour is equidistant.")
        self.grid_type_combo.currentIndexChanged.connect(
            self._on_grid_type_changed)

        # Connector
        self.connector_combo = QtWidgets.QComboBox()
        for k in CONNECTORS: self.connector_combo.addItem(k)
        self.connector_combo.currentTextChanged.connect(
            lambda name: (self._emit_array(),
                          self.settingChanged.emit(f"cable / connector = {name}")))
        self.connector_combo.setToolTip(
            "Headstage connector / pinout. Maps each PlexStim port to "
            "the device pad it ultimately drives. The choice does not "
            "change device-side wiring — it only adjusts how the "
            "channel mapping is rendered in the GUI and saved files.\n\n"
            "Pinouts mirror the MATLAB getDeviceType.m arrays:\n"
            "  • Omnetics UTD  — omneticsUTD (identity 1:16)\n"
            "  • Omnetics NNX  — omneticsNNX (NeuroNexus re-order)\n"
            "  • Plexon (UTD)  — utd_plexon (PlexStim port order)\n"
            "  • Custom        — type your own pinout in the map below.")

        # Surface area mode + units + value
        # Surface area: "Same for all electrodes" toggle (default on).
        # Same shape as the coating toggle so the two read consistently.
        self.area_mode = QtWidgets.QCheckBox("Same for all")
        self.area_mode.setChecked(True)
        self.area_mode.setToolTip(
            "When checked, every electrode in the array uses the "
            "single area shown in the spinbox. Uncheck to expose a "
            "per-channel area column in the device-view table for "
            "arrays with mixed pad sizes.")
        self.area_unit = QtWidgets.QComboBox()
        self.area_unit.addItems(list(UNITS.keys()))
        self.area_unit.setToolTip(
            "Unit for the surface-area value. Switching units "
            "rescales the value so the underlying physical area "
            "stays the same.")
        self.area_value = RepeatingDoubleSpinBox()
        self.area_value.setRange(0.001, 1e9)
        self.area_value.setValue(5000.0)        # μm² default
        # Spinbox increment AND decimal precision both track the
        # chosen unit. Step: 100 µm², 0.1 mm², 1 cm². Decimals: 0
        # (integer µm²), 2 (hundredths of mm²), 1 (tenths of cm²).
        # Live-updated by ``_on_area_unit_changed`` whenever the user
        # picks a different unit. The matching unit string is also
        # pinned as the spinbox SUFFIX so the value reads as "5000 μm²"
        # inside the box rather than a bare number — same idea as the
        # rate spinbox showing " pps" / " ms".
        self.area_value.setSingleStep(
            AREA_STEPS_BY_UNIT.get(self.area_unit.currentText(), 1.0))
        self.area_value.setDecimals(
            AREA_DECIMALS_BY_UNIT.get(self.area_unit.currentText(), 3))
        self.area_value.setSuffix(f" {self.area_unit.currentText()}")
        self.area_value.setToolTip(
            "Geometric surface area of one electrode pad. Used as "
            "the per-channel default unless the per-channel area "
            "column is enabled. The value is stored in the unit "
            "shown in the dropdown to its right (also displayed "
            "inline as the spinbox suffix).")
        # Seed the "last unit" cache so the first unit-change correctly
        # converts the displayed value (otherwise it would treat the
        # current unit as both old and new and skip the rescale).
        self._area_last_unit: str = self.area_unit.currentText()
        self.area_unit.currentTextChanged.connect(self._on_area_unit_changed)
        # Commit on Enter/return/focus-out, NOT per keystroke (operator: "I
        # want pressing enter/return or clicking out" — logging + persistence
        # must not fire on every digit typed).
        self.area_value.editingFinished.connect(self._emit_array)
        self.area_mode.toggled.connect(self._on_area_mode_changed)

        # Coating mode toggle — checked = "Same for all electrodes".
        # HIDDEN + permanently checked (operator: the active/working
        # electrode coating is never per-channel-different, so the
        # "Same for all" checkbox was removed from the UI).  The widget
        # is kept constructed-but-invisible so every ``coating_mode
        # .isChecked()`` read downstream (device-view per-channel gate,
        # prefs round-trip, snapshot) still returns True and the
        # single-coating path is always taken.  Do NOT re-add it to the
        # coating row layout.
        self.coating_mode = QtWidgets.QCheckBox("Same for all")
        self.coating_mode.setChecked(True)
        self.coating_mode.setVisible(False)
        self.coating_mode.setToolTip(
            "When checked, every electrode uses the coating selected "
            "in the dropdown. Uncheck to expose a per-channel coating "
            "column in the device-view table.")
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
        self.coating_combo.setToolTip(
            "Active electrode material. Selecting a coating auto-"
            "fills the cathodic / anodic potential limits below from "
            "the catalog values (you can still hand-edit those). "
            "Pick ``Custom…`` to enter a coating name not in the "
            "catalog — the limits then stay at whatever you've "
            "typed.")
        self.coating_custom = QtWidgets.QLineEdit()
        self.coating_custom.setPlaceholderText("Enter custom coating name…")
        self.coating_custom.setVisible(False)
        self.coating_custom.setToolTip(
            "Free-form coating name used when the dropdown is set to "
            "``Custom…``. Saved to session metadata so analysis tools "
            "see it.")
        self.coating_combo.currentTextChanged.connect(self._on_coating_changed)
        self.coating_custom.editingFinished.connect(self._emit_array)

        # Electrode geometry combo + Rounded toggle. Geometry is the
        # shape of the active electrode site (circle, square,
        # rectangle, cone, ring, band) — see
        # :data:`stimtest.config.ELECTRODE_GEOMETRIES`. Rounded is
        # only meaningful for square / rectangle (filleted corners /
        # pill cap); the toggle is hidden for the other shapes.
        # The combo's userData carries the short geometry code that
        # round-trips into prefs and per-channel overrides.
        from ..config import (
            ELECTRODE_GEOMETRIES, ELECTRODE_GEOMETRY_CIRCLE,
            ELECTRODE_GEOMETRY_SQUARE, ELECTRODE_GEOMETRY_RECTANGLE,
        )
        self._SQUARE_OR_RECT_GEOMETRIES = {
            ELECTRODE_GEOMETRY_SQUARE, ELECTRODE_GEOMETRY_RECTANGLE,
        }
        self.geometry_combo = QtWidgets.QComboBox()
        for code, geom in ELECTRODE_GEOMETRIES.items():
            self.geometry_combo.addItem(geom.label, userData=code)
        # Default to circle, the catalog's most common pad shape and
        # the dataclass-level fallback in ``DeviceDef``.
        idx_circle = self.geometry_combo.findData(ELECTRODE_GEOMETRY_CIRCLE)
        if idx_circle >= 0:
            self.geometry_combo.setCurrentIndex(idx_circle)
        # Tooltip surfaces the per-geometry GSA formula so users
        # understand what their pad-area input means in each shape.
        tip_lines = ["Active electrode site geometry:"]
        for code, geom in ELECTRODE_GEOMETRIES.items():
            tip_lines.append(f"  • {geom.label} — {geom.notes}")
        self.geometry_combo.setToolTip("\n".join(tip_lines))
        self.geometry_combo.currentIndexChanged.connect(
            self._on_geometry_changed)
        self.geometry_rounded = QtWidgets.QCheckBox("Rounded")
        self.geometry_rounded.setToolTip(
            "Filleted corners (square) / pill-cap ends (rectangle). "
            "Only meaningful for the square and rectangle geometries; "
            "hidden for circle / cone / ring / band.")
        self.geometry_rounded.toggled.connect(
            lambda on: (self._emit_array(),
                        self.settingChanged.emit(
                            f"electrode corners = {'rounded' if on else 'square'}")))
        # Initial visibility: hidden because default geometry is
        # circle. ``_on_geometry_changed`` flips the visibility on
        # demand whenever the user picks square / rectangle.
        self.geometry_rounded.setVisible(False)

        # Return / counter-electrode toggle + coating selector. The
        # toggle gates whether the lab is using a separate return /
        # counter electrode (vs. routing return through one of the
        # on-array channels). When checked, the dropdown picks that
        # electrode's material from the same catalog used for the
        # active electrodes — return / counter electrodes are commonly
        # Pt or PtIr, but a SIROF reference is also possible. The
        # coating combo greys out when the toggle is off so the user
        # sees that the metadata is currently unused.
        self.return_enable = QtWidgets.QCheckBox("")
        self.return_enable.setChecked(False)
        self.return_enable.setToolTip(
            "Tick when the experiment uses a separate return / counter "
            "electrode (e.g. a Pt wire dropped into the saline bath) "
            "instead of routing return current through one of the "
            "on-array channels. The dropdown to the right then picks "
            "that electrode's material from the same catalog as the "
            "active electrodes.")
        self.return_coating = QtWidgets.QComboBox()
        # Filter out coatings that aren't realistic counter / return
        # electrode materials. PEDOT:PSS is a common ACTIVE-electrode
        # coating (high CSC, low impedance) but not a typical return
        # — exclude it here so the dropdown stays focused on viable
        # counter materials.
        _RETURN_EXCLUDED = {"PEDOT:PSS"}
        for short_name, coating in COATINGS.items():
            if short_name in _RETURN_EXCLUDED:
                continue
            label = coating.display_name or short_name
            self.return_coating.addItem(label, userData=short_name)
        # ``Custom`` lets the user pick an out-of-catalog return
        # material. No automatic limit shift is applied — the user
        # can hand-edit the cathodic / anodic limits if they know
        # the material's water-window endpoints.
        self.return_coating.addItem("Custom", userData=REF_CUSTOM)
        # Default to Pt, the most common counter-electrode material;
        # fall back to the first item if Pt isn't in the catalog.
        _pt_idx = self.return_coating.findData("Pt")
        self.return_coating.setCurrentIndex(_pt_idx if _pt_idx >= 0 else 0)
        self.return_coating.setEnabled(False)   # mirrors initial unchecked state
        self.return_coating.setToolTip(
            "Material of the return / counter electrode. The estimated "
            "open-circuit potential vs Ag|AgCl shown to the right is "
            "the in-vitro (saline / PBS) value from the MATLAB catalog "
            "in ``getReferenceElectrode.m``; in-animal preparations all "
            "counter potentials collapse to ≈ 0 V.")
        self.return_enable.toggled.connect(self._on_return_enable_changed)
        # When the reference toggle is OFF, the return-electrode coating
        # acts as the reference baseline — so a return-coating change
        # also re-shifts the potential limits via
        # ``_on_return_coating_changed``. The handler is a no-op for
        # the normal "reference on" case.
        self.return_coating.currentTextChanged.connect(
            self._on_return_coating_changed)
        # Output: estimated open-circuit potential of the return
        # electrode vs Ag|AgCl. Updated whenever the user picks a
        # different return coating; tooltip carries the underlying
        # catalog value plus the in-vivo caveat.
        self.return_potential_label = QtWidgets.QLabel("")
        self.return_potential_label.setTextFormat(
            QtCore.Qt.TextFormat.RichText)
        self.return_potential_label.setStyleSheet(
            "color: #1565c0; font-size: 9pt; padding-left: 8px;")
        self.return_potential_label.setToolTip(
            "Estimated open-circuit potential of the selected return "
            "electrode vs Ag|AgCl, in volts (in-vitro / saline). "
            "Source: MATLAB ``getReferenceElectrode.m`` "
            "``counterPotential_arr``. In-animal preparations the same "
            "table reads as 0 V for all counter materials.")
        self.return_coating.currentTextChanged.connect(
            self._refresh_return_potential_label)
        self.return_enable.toggled.connect(
            self._refresh_return_potential_label)
        # Custom-OCP spinbox — visible only when the user picks
        # ``Custom`` for the return coating. Lets the operator type
        # the open-circuit potential of an out-of-catalog material so
        # the limit-shift math (when the return acts as the reference
        # baseline) uses the right value. Saved to / restored from
        # prefs so a custom choice survives a restart.
        self.return_custom_ocp_v = ScientificDoubleSpinBox()
        self.return_custom_ocp_v.setRange(-2.0, 2.0)
        self.return_custom_ocp_v.setDecimals(3)
        self.return_custom_ocp_v.setSingleStep(0.05)
        self.return_custom_ocp_v.setSuffix(" V")
        self.return_custom_ocp_v.setValue(0.0)
        self.return_custom_ocp_v.setVisible(False)
        self.return_custom_ocp_v.setToolTip(
            "Custom return-electrode potential vs Ag|AgCl (V). Used "
            "only when the return coating is set to ``Custom`` — the "
            "value is the OCP that the limit-shift math applies when "
            "no separate reference electrode is enabled and the "
            "return is acting as the reference baseline.")
        # Commit on Enter/return/focus-out (operator: "enter/return or clicking
        # out"), NOT per keystroke — the OCP reshuffles the limit spinboxes,
        # logs, and persists, so it must fire once on commit, not every digit.
        self.return_custom_ocp_v.editingFinished.connect(
            self._on_return_coating_changed)
        self.return_custom_ocp_v.editingFinished.connect(
            self._refresh_return_potential_label)
        # Initial render (after both widgets exist).
        self._refresh_return_potential_label()

        # ----- reference electrode (3-electrode setup) -----
        # The reference electrode is the third terminal in a 3-electrode
        # cell — distinct from the return / counter electrode. Its OCP
        # vs Ag|AgCl shifts the displayed water-window limits: catalog
        # values in :data:`COATINGS` are stored in V vs Ag|AgCl, and
        # picking a different reference re-expresses the limit fields
        # in V vs <ref> (per MATLAB ``getReferenceElectrode.m``
        # ``windowRef = waterWindow - refPotential``).
        #
        # Toggle ON  → dropdown active, limits are in V vs the chosen
        #              reference. Default reference = Ag|AgCl
        #              (potential = 0 V), so the displayed numbers
        #              equal the catalog numbers.
        # Toggle OFF → dropdown greyed; limits are interpreted as V vs
        #              Ag|AgCl by convention (no reference shift). Any
        #              shift applied by the previous reference is
        #              reverted so the spinbox values return to the
        #              catalog scale.
        self.reference_enable = QtWidgets.QCheckBox("")
        self.reference_enable.setChecked(True)
        self.reference_enable.setToolTip(
            "Tick when an external reference electrode is in the "
            "cell (the third terminal in a 3-electrode setup, "
            "distinct from the return / counter electrode). The "
            "dropdown to the right picks its material; the displayed "
            "cathodic / anodic limits then shift from ``V vs Ag|AgCl`` "
            "(catalog) into ``V vs <chosen reference>``. Untick to "
            "treat the limits as ``V vs Ag|AgCl`` by convention.")
        self.reference_combo = QtWidgets.QComboBox()
        for short_name in REFERENCE_ELECTRODES_OCP_V.keys():
            label = REFERENCE_ELECTRODES_DISPLAY.get(short_name, short_name)
            self.reference_combo.addItem(label, userData=short_name)
        # Default to Ag|AgCl — same convention as the catalog limits,
        # so the spinbox values stay at the catalog numbers until the
        # user picks a different reference.
        _ag_idx = self.reference_combo.findData(REF_AG_AGCL)
        self.reference_combo.setCurrentIndex(_ag_idx if _ag_idx >= 0 else 0)
        self.reference_combo.setToolTip(
            "Material of the reference electrode. Selecting a "
            "non-Ag|AgCl reference subtracts that material's "
            "open-circuit potential vs Ag|AgCl (in vitro values from "
            "MATLAB ``refPotential_arr``) from the cathodic / anodic "
            "limit fields, re-expressing them in ``V vs <reference>``. "
            "In-animal preparations the same MATLAB table reads as "
            "0 V for every material.")
        # Cache the currently-applied reference potential vs Ag|AgCl —
        # used to convert spinbox values when the reference changes
        # (delta = new_pot - old_pot, then ``new_val = old_val − delta``).
        # Starts at 0 (Ag|AgCl) to match the catalog scale.
        self._current_ref_potential_v: float = 0.0
        # Custom-electrode trackers — user-added names per combo, in
        # insertion order. Persisted in prefs so a saved profile
        # round-trips without losing the user's named entries.
        self._coating_custom_entries: List[str] = []
        self._return_custom_entries: List[str] = []
        self._reference_custom_entries: List[str] = []
        # User-named custom devices — same idea as the electrode
        # trackers but for ``device_combo``. Each entry's per-device
        # state (mapping + layout) lives in ``_device_custom_state``
        # keyed by name; switching between custom devices saves the
        # outgoing state and restores the incoming one.
        self._device_custom_entries: List[str] = []
        self._device_custom_state: dict = {}
        # ``_prev_*_data`` caches the last non-trigger selection for
        # each combo so the Cancel button on the custom-name dialog
        # can revert cleanly. Initialised to the current selection of
        # each combo (catalog defaults).
        self._prev_coating_data = self.coating_combo.currentData()
        self._prev_return_data = self.return_coating.currentData()
        self._prev_reference_data = self.reference_combo.currentData()
        self._prev_device_name = self.device_combo.currentText()
        # Output: estimated open-circuit potential of the reference
        # electrode vs Ag|AgCl. Plain text — no rich format needed.
        self.reference_potential_label = QtWidgets.QLabel("")
        self.reference_potential_label.setTextFormat(
            QtCore.Qt.TextFormat.RichText)
        # Do NOT word-wrap (operator: "Do not wrap this") — wrapping broke the
        # readout mid-token ("+0.000 V vs Ag|" / "AgCl (no shift applied)").
        # The tag was shortened (task #150: "tested +X ± Y V", no
        # "(learned, N samples)"), so a single line no longer forces the
        # panel wide the way the old long text did.
        self.reference_potential_label.setWordWrap(False)
        self.reference_potential_label.setStyleSheet(
            "color: #1565c0; font-size: 9pt; padding-left: 8px;")
        self.reference_potential_label.setToolTip(
            "Open-circuit potential of the selected reference "
            "electrode vs Ag|AgCl (in vitro, from MATLAB "
            "``refPotential_arr``). The cathodic / anodic limit "
            "spinboxes below are shifted by this amount when a "
            "non-Ag|AgCl reference is selected.")
        self.reference_combo.currentTextChanged.connect(
            self._on_reference_changed)
        self.reference_enable.toggled.connect(
            self._on_reference_enable_changed)
        # Custom-OCP spinbox — visible only when the user picks
        # ``Custom`` for the reference. Lets the operator type the
        # open-circuit potential of an out-of-catalog reference so the
        # limit-shift math uses the right value rather than the
        # default 0 V (which would mean "no shift, same as Ag|AgCl").
        # Saved to / restored from prefs.
        self.reference_custom_ocp_v = ScientificDoubleSpinBox()
        self.reference_custom_ocp_v.setRange(-2.0, 2.0)
        self.reference_custom_ocp_v.setDecimals(3)
        self.reference_custom_ocp_v.setSingleStep(0.05)
        self.reference_custom_ocp_v.setSuffix(" V")
        self.reference_custom_ocp_v.setValue(0.0)
        self.reference_custom_ocp_v.setVisible(False)
        self.reference_custom_ocp_v.setToolTip(
            "Custom reference-electrode potential vs Ag|AgCl (V). "
            "Used only when the reference is set to ``Custom`` — the "
            "value is the OCP that the limit-shift math applies, "
            "re-expressing the cathodic / anodic limits as "
            "``V vs <custom reference>``.")
        # Commit on Enter/return/focus-out (operator), NOT per keystroke — the
        # OCP reshuffles the limit spinboxes + logs + persists.
        self.reference_custom_ocp_v.editingFinished.connect(
            self._on_reference_changed)
        # Initial render. Default reference = Ag|AgCl (0 V), so no
        # shift is applied to the catalog limits.
        self._refresh_reference_potential_label()

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
        self.cathodic_limit_v.setSingleStep(0.005)
        self.cathodic_limit_v.setSuffix(" V")
        self.cathodic_limit_v.setValue(-0.6)
        self.cathodic_limit_v.setToolTip(
            "Lower water-window potential E_lc (V vs Ag|AgCl). The "
            "Voltage-Transient runner stops the ramp once the most "
            "cathodic measured V_a − V_access drops below this "
            "value − polarization tolerance. Catalog default for the "
            "selected coating, but you can override it per session.")
        self.anodic_limit_v = RepeatingDoubleSpinBox()
        self.anodic_limit_v.setRange(0.0, 3.0)
        self.anodic_limit_v.setDecimals(3)
        self.anodic_limit_v.setSingleStep(0.005)
        self.anodic_limit_v.setSuffix(" V")
        self.anodic_limit_v.setValue(0.8)
        self.anodic_limit_v.setToolTip(
            "Upper water-window potential E_la (V vs Ag|AgCl). The "
            "Voltage-Transient runner stops the ramp once the most "
            "anodic measured V_a − V_access exceeds this value + "
            "polarization tolerance. Catalog default for the "
            "selected coating, but you can override it per session.")
        self.polarization_tol_v = RepeatingDoubleSpinBox()
        self.polarization_tol_v.setRange(0.0, 0.500)
        self.polarization_tol_v.setDecimals(3)
        self.polarization_tol_v.setSingleStep(0.005)
        self.polarization_tol_v.setSuffix(" V")
        # MATLAB's runVoltageTransient.m uses TOL = 0.02 V as the
        # default grace band on potential excursions; same default here.
        self.polarization_tol_v.setValue(0.020)
        self.polarization_tol_v.setToolTip(
            "Polarization tolerance — grace band added to the cathodic "
            "/ anodic limits before the runner declares 'limit hit'. "
            "Mirrors MATLAB ``runVoltageTransient.m`` ``TOL = 0.02 V``. "
            "Larger values let the ramp push slightly past the catalog "
            "limit before stopping.")
        for sp in (self.cathodic_limit_v, self.anodic_limit_v,
                   self.polarization_tol_v):
            # Commit on Enter/return/focus-out, NOT per keystroke (operator).
            # Also means a PROGRAMMATIC limit shift (reference-OCP reshuffle via
            # setValue) no longer false-fires this "user edited" handler.
            sp.editingFinished.connect(self._on_limits_user_edited)

        # Oscilloscope channel mapping — inverted from the legacy
        # design: rows are the four scope channels, the dropdown picks
        # which waveform role the channel carries. ``self._role_combos``
        # maps channel name → its role QComboBox so current_aliases /
        # restore_prefs can iterate without naming each one.
        # Defaults to ``None`` for every channel: the user explicitly
        # opts in once a scope is connected, and the panel calls
        # :meth:`apply_default_scope_mapping` on first connect.
        self._role_combos: dict = {}
        # Has the user (or restored prefs) configured the channel→role
        # mapping?  ``apply_default_scope_mapping`` (called on EVERY scope
        # connect) must NOT stomp the operator's choices once they've set
        # them — in particular a deliberate ``None`` on a channel.  The
        # flag flips True on the first real role assignment (restore_prefs
        # or a user combo change via ``_on_role_changed``) or after the
        # one-time catalog default is applied; from then on connect is a
        # no-op for the mapping.  (Operator: "The oscilloscope channel
        # choices are not being remembered … I have been using NONE for
        # CH3" — every connect re-filled CH3's None with the E_ret
        # default.)  Set BEFORE the combos wire ``_on_role_changed`` so
        # construction can't trip it.
        self._scope_roles_user_configured = False
        # Row labels are kept around so apply_scope_capabilities can
        # hide CH3/CH4 rows on a 2-channel scope (TBS1072C, TBS1052C,
        # etc.) — both the label and the combo are hidden together so
        # the form doesn't show empty placeholder rows. Set in
        # ``_assemble_pages`` when the form is laid out.
        self._role_labels: dict = {}
        # How many scope channels the connected scope physically has.
        # ``current_aliases`` only maps roles on CH1..CH``n`` so a hidden
        # channel's PRESERVED role (see ``_set_visible_scope_channels``)
        # never leaks to the runner.  Defaults to 4 (all available until a
        # scope says otherwise).
        self._n_visible_scope_channels = 4
        # Per-channel scope BANDWIDTH + COUPLING override dropdowns, laid out
        # in line with the role dropdown (operator).  Default "Auto" preserves
        # the automatic policies; a concrete choice overrides that channel.
        self._bw_combos: dict = {}
        self._coupling_combos: dict = {}
        # Row container widgets (role + bandwidth + coupling) so the whole row
        # can be hidden together on a 2-channel scope.
        self._scope_role_row_widgets: dict = {}
        for ch in ("CH1", "CH2", "CH3", "CH4"):
            cb = QtWidgets.QComboBox()
            cb.addItems(SCOPE_ROLES)
            cb.setCurrentText(ROLE_NONE)
            cb.currentTextChanged.connect(self._on_role_changed)
            self._role_combos[ch] = cb
            bw = QtWidgets.QComboBox()
            bw.addItems(SCOPE_BANDWIDTHS)
            bw.setToolTip(
                "Analog bandwidth for this channel.  Auto = full bandwidth on "
                "data channels, 20 MHz on the trigger channel (clean "
                "comparator).  Pick 20 MHz to quiet a noisy channel; Full for "
                "maximum edge detail.")
            bw.currentTextChanged.connect(
                lambda _t, c=ch: self._on_channel_scope_opt_changed(
                    "bandwidth", c))
            self._bw_combos[ch] = bw
            cp = QtWidgets.QComboBox()
            cp.addItems(SCOPE_COUPLINGS)
            cp.setToolTip(
                "Input coupling for this channel.  Auto = DC for V_mon / "
                "I_mon / electrodes, AC for a distinct digital trigger.  "
                "DC / AC force that coupling.  'DC + AC' captures in DC to "
                "read the rest potential, then AC-couples so the small swing "
                "fine-scales, summing the DC offset back — best for a "
                "DC-biased electrode (e.g. monopolar E_ret).")
            cp.currentTextChanged.connect(
                lambda _t, c=ch: self._on_channel_scope_opt_changed(
                    "coupling", c))
            self._coupling_combos[ch] = cp
        # When a no-electrode device (test board) is selected the E_act / E_ret
        # roles are removed from the scope-mapping dropdowns (operator: "when
        # the Test Board is selected, do not show Eret and Eact as options").
        # A channel that HELD one of those roles is stashed here so switching
        # back to a real electrode array restores it (honours gotcha #49 — the
        # operator's scope choices must be remembered).  Persisted in prefs.
        self._stashed_electrode_roles: dict = {}
        # Prior I_mon bandwidth choice, stashed while I_mon is the trigger (no
        # Trigger channel → bandwidth locked to 20 MHz) and restored when a
        # Trigger channel is re-added.
        self._imon_bw_stash = None
        # Initial state: all roles start at None → the bandwidth + coupling
        # dropdowns start disabled (the initial setCurrentText(None) above fires
        # BEFORE the slot is connected, so do it explicitly here).
        self._refresh_scope_option_enabled()

        # Oscilloscope acquisition controls — Sampling vs Average, plus
        # an averaging-count widget that's either a combo (for scopes
        # with a fixed list, e.g. TBS-series) or a spinbox (for scopes
        # accepting arbitrary values). The widget is rebuilt on scope
        # connect via :meth:`apply_scope_capabilities`.
        self.acq_mode_combo = QtWidgets.QComboBox()
        self.acq_mode_combo.addItems(["SAMPLE", "AVERAGE"])
        self.acq_mode_combo.setCurrentText("AVERAGE")
        self.acq_mode_combo.setToolTip(
            "Oscilloscope acquisition mode. SAMPLE captures every "
            "trigger as-is. AVERAGE coherently averages N consecutive "
            "captures (count set in the field to the right) — much "
            "lower noise floor, but the runner waits for N stable "
            "captures per step.")
        self.acq_mode_combo.currentTextChanged.connect(self._on_acq_changed)
        # Connected scope (set in ``apply_scope_capabilities``; None when
        # offline/disconnected) — used by the average-count confirm below.
        self._scope = None
        # Debounce for the average-count → scope round-trip confirm.  A
        # 300 ms single-shot coalesces typing / arrow-holding into ONE
        # NUMAVg write + read-back so we don't spam the scope mid-edit.
        self._navg_confirm_timer = QtCore.QTimer(self)
        self._navg_confirm_timer.setSingleShot(True)
        self._navg_confirm_timer.setInterval(300)
        self._navg_confirm_timer.timeout.connect(self._confirm_navg_on_scope)
        # Default n_avg widget = a generic spinbox; replaced when the
        # scope reports a discrete list of choices.
        self.acq_navg_spin = RepeatingSpinBox()
        self.acq_navg_spin.setRange(2, 512)
        self.acq_navg_spin.setValue(16)
        self.acq_navg_spin.setToolTip(
            "Number of captures to average together when AVERAGE mode "
            "is selected. Higher counts give cleaner traces but "
            "lengthen each ramp step.")
        # Commit on Enter/Return OR focus-out (``editingFinished``), NOT per
        # keystroke (operator: "I want pressing enter/return or clicking out" —
        # ``valueChanged`` fired the log + scope round-trip on every digit while
        # typing, and holding an arrow spammed it too).
        self.acq_navg_spin.editingFinished.connect(self._on_acq_changed)
        self._last_acq_emitted = None   # (mode, n_avg) — skip no-op commits
        self.acq_navg_combo: QtWidgets.QComboBox | None = None

        # Horizontal auto-scaling window — Wide (more post-pulse recovery,
        # MATLAB-like) vs Tight (the pulse fills more of the screen).  Drives
        # the scope's ``auto_layout_for_pulse`` fill floor.
        self.horiz_scaling_combo = QtWidgets.QComboBox()
        self.horiz_scaling_combo.addItems(["Wide", "Tight"])
        self.horiz_scaling_combo.setCurrentText("Wide")
        self.horiz_scaling_combo.setToolTip(
            "Horizontal (time) auto-scaling window.\n"
            "Wide: a wider capture window with more post-pulse recovery "
            "(MATLAB-style) — the pulse fills less of the screen.\n"
            "Tight: the pulse fills more of the screen, with less trailing "
            "recovery time.\n"
            "The scope's SEC/DIV grid is coarse, so the two modes pick "
            "different grid steps (e.g. a 400 µs biphasic → 1500 µs Wide vs "
            "600 µs Tight on a TBS2204B).")
        self.horiz_scaling_combo.currentTextChanged.connect(
            self._on_horiz_scaling_changed)

        # (The global "Electrode coupling" dropdown was removed — the
        # per-channel Coupling column's "DC + AC" item is the sole control for
        # the gotcha-#85 DC→AC trick now.)

        # Tracks whether the last connected scope had an EXT BNC input.
        # Default False so the toggle stays hidden at app launch until
        # a live scope confirms otherwise.  ``apply_scope_capabilities``
        # is the only writer.  This field is the in-process cache of
        # the live state; the prefs-restore path no longer reads it
        # (see :meth:`_restore_prefs` for why — stale prefs from a
        # previous session's scope would re-show the toggle on a
        # newly-connected no-EXT scope at startup).
        self._scope_has_ext: bool = False

        # Trigger-source toggle — checked = use EXT digital sync,
        # unchecked = use the I_mon channel as the trigger source.
        # Hidden by default; ``apply_scope_capabilities`` reveals it
        # only when the connected scope's ``info.has_ext_trigger`` is
        # True.  Without the initial hide, the toggle flashes on at
        # app startup (no scope yet) and stays visible until the user
        # connects a no-EXT scope, which makes it look like the GUI
        # is offering an option the hardware doesn't support.
        self.ext_trigger_check = QtWidgets.QCheckBox("Use digital sync trigger (EXT)")
        self.ext_trigger_check.setChecked(False)
        self.ext_trigger_check.setVisible(False)
        self.ext_trigger_check.setToolTip(
            "Checked: the scope triggers off the Plexon digital-sync "
            "output wired to the EXT BNC. "
            "Unchecked: the scope triggers off the Iₘₒₙ "
            "channel (useful when no EXT input is available or no digital "
            "sync cable is connected).")
        self.ext_trigger_check.toggled.connect(self._on_trigger_source_changed)

        # Trigger edge is NOT operator-selectable.  The slope is fully
        # determined by the trigger-source rule set (see
        # ``current_trigger_source`` for the three priorities):
        #
        #   * EXT BNC               → RISE  (TTL active-high)
        #   * Channel-Trigger TTL   → RISE  (TTL active-high)
        #   * I_mon channel         → FALL for cathodic-first patterns,
        #                             RISE for anodic-first
        #                             (auto-derived from the
        #                             excitation-phase polarity at run
        #                             start in the experiment tab)
        #
        # An operator-facing dropdown was previously rendered here,
        # but every code path in :class:`_BaseExperimentTab` resolves
        # the slope from the rules above and never read the combo's
        # value — leaving the control in the GUI only added a UX
        # trap where flipping it appeared to do something but didn't.

        # Experiment picker
        self.experiment_combo = QtWidgets.QComboBox()
        for code, defn in EXPERIMENTS.items():
            self.experiment_combo.addItem(defn.label, userData=code)
        self.experiment_combo.setToolTip(
            "Pick which experiment runner to drive: Voltage Transient "
            "(safe-current sweep), Current Sweep / Long Pulse (custom "
            "ramps), or Pattern Sweep (run a pattern with logged "
            "metrics). The blurb below updates with a one-line summary "
            "of the selected experiment.")
        self.experiment_blurb = QtWidgets.QLabel()
        self.experiment_blurb.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.experiment_blurb.setWordWrap(True)
        self.experiment_blurb.setStyleSheet("color: #555; font-size: 9pt;")
        self.experiment_blurb.setToolTip(
            "Brief description of the currently-selected experiment. "
            "Updated whenever the dropdown above changes.")
        self.open_exp_btn = QtWidgets.QPushButton("Go to test parameters tab →")
        self.open_exp_btn.setToolTip(
            "Jump to the Test parameters tab. The tab's content "
            "follows the Experiment dropdown above — picking a new "
            "experiment in the dropdown swaps the parameters page "
            "immediately, so by the time you click this button the "
            "right page is already loaded. This is a pure-"
            "navigation shortcut.")
        self.experiment_combo.currentIndexChanged.connect(self._on_experiment_changed)
        self.open_exp_btn.clicked.connect(self._on_open_experiment)

        # ---------------- right column: device view ----------------
        self.device_view = DeviceView()
        self.device_view.mappingChanged.connect(
            lambda *_: (self._emit_array(),
                        self.settingChanged.emit("channel mapping edited")))
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
        # Optional phone + carrier for run-end text alerts.
        sess_form.addRow(self._lbl("User phone:"), self.user_phone)
        sess_form.addRow(self._lbl("Carrier:"), self.user_carrier)
        sess_form.addRow(self._lbl("Institution/Company:"), self.user_institution)
        # Environment + sparge-gas were originally added to the
        # session form. They were moved BELOW the Potential-limits
        # / Tolerance row in the device-parameters form (see
        # ``dev_form.addRow(... "Environment:" ...)`` below) so
        # they read as part of the electrochemistry context (which
        # tissue / buffer + which atmosphere) rather than the
        # bookkeeping context (notebook / subject / save path).
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
        plots_row.addWidget(self.auto_save_plots_dpi)
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
        # Test-device row: combo + grid-type chooser sit side-by-side.
        # The grid-type chooser is auto-hidden when a built-in device
        # is selected (see :meth:`_on_device_changed`).
        dev_pick_row = QtWidgets.QHBoxLayout()
        dev_pick_row.setContentsMargins(0, 0, 0, 0)
        dev_pick_row.setSpacing(6)
        dev_pick_row.addWidget(self.device_combo, stretch=1)
        dev_pick_row.addWidget(self.grid_type_combo, stretch=0)
        dev_pick_w = QtWidgets.QWidget(); dev_pick_w.setLayout(dev_pick_row)
        dev_form.addRow(self._lbl("Test device:"), dev_pick_w)
        # Cable selection sits directly BELOW the device dropdown (operator:
        # "cable selection should be a dropdown list below the Device dropdown
        # list").  Labelled "Cable" (was "Connector") to match the cable-map
        # tree on the right — it's the device→Plexon channel translation.
        dev_form.addRow(self._lbl("Cable:"), self.connector_combo)

        # Cable channel-mapping tree (operator: "we need a map/tree of channel
        # mapping of the cable" + "show the cable mapping below the channel
        # mapping on the right side" + "allow for a custom option for the user
        # to change the mapping themselves").  Visualises the selected
        # connector's ``pin_to_channel`` — each connector PIN → the DEVICE
        # CHANNEL it drives.  Identity (pin N → CH N) for the standard Omnetics
        # UTD cable; re-ordered for NeuroNexus / PlexStim-port-side cables,
        # whose remapped rows are shown in BOLD.  READ-ONLY for the catalogued
        # cables; EDITABLE when the "Custom" connector is selected (the
        # operator types each pin's device channel, stored in
        # ``self._custom_cable_map`` + prefs).  The widget is laid out on the
        # RIGHT column, below the device geometry view (see the split assembly
        # below) — NOT in this left-hand device form.
        self._custom_cable_map: Dict[int, int] = {}
        self._cable_map_editing = False  # re-entrancy guard for itemChanged
        self.cable_map_tree = QtWidgets.QTreeWidget()
        self.cable_map_tree.setColumnCount(2)
        self.cable_map_tree.setHeaderLabels(["Connector pin", "Device channel"])
        self.cable_map_tree.setRootIsDecorated(False)
        self.cable_map_tree.setUniformRowHeights(True)
        self.cable_map_tree.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.cable_map_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
        self.cable_map_tree.setMaximumHeight(300)
        self.cable_map_tree.setToolTip(
            "Cable channel translation: each DEVICE channel you pulse (col 1) "
            "→ the PLEXON stim channel PULSAR commands (col 2).  Identity "
            "(CH N → CH N) means pulsing CH01 stimulates device CH01 — the "
            "test board + standard cables; re-ordered (bold) for cables that "
            "renumber.  Pick the “Custom” connector to type your own map.")
        self.cable_map_tree.itemChanged.connect(self._on_cable_map_item_changed)
        self.connector_combo.currentTextChanged.connect(
            self._refresh_cable_map_tree)
        self._refresh_cable_map_tree()

        # Coating row: combo + custom field follow the "Same for all"
        # checkbox directly. The custom field auto-hides when the
        # selected coating isn't Custom… (see _on_coating_changed).
        # Reordered to sit ABOVE the Surface area row per user
        # request — material first (it determines the catalog
        # defaults for area), then the area value, then geometry.
        coat_row = QtWidgets.QHBoxLayout()
        coat_row.setContentsMargins(0, 0, 0, 0)
        coat_row.setSpacing(6)
        # ``coating_mode`` ("Same for all") is intentionally NOT added —
        # it's hidden + permanently checked (operator: active/working
        # coating is never per-channel-different).
        coat_row.addWidget(self.coating_combo)
        coat_row.addWidget(self.coating_custom)
        coat_row.addStretch(1)
        coat_w = QtWidgets.QWidget(); coat_w.setLayout(coat_row)
        dev_form.addRow(self._lbl("Active/working electrode:"), coat_w)

        # Surface area row — the value spinbox + unit combo sit RIGHT
        # NEXT to the "Same for all electrodes" checkbox, hugging it
        # left-to-right ("Same for all  [5000.000] [µm²]"). Trailing
        # ``addStretch`` keeps that group flush-left instead of spreading
        # across the field column.
        area_row = QtWidgets.QHBoxLayout()
        area_row.setContentsMargins(0, 0, 0, 0)
        area_row.setSpacing(6)
        area_row.addWidget(self.area_mode)
        area_row.addWidget(self.area_value)
        area_row.addWidget(self.area_unit)
        area_row.addStretch(1)
        area_w = QtWidgets.QWidget(); area_w.setLayout(area_row)
        dev_form.addRow(self._lbl("Surface area:"), area_w)

        # Electrode geometry row — combo + Rounded toggle. Sits
        # right below the coating row so material + shape sit
        # together as a paired "physical electrode" section.
        geom_row = QtWidgets.QHBoxLayout()
        geom_row.setContentsMargins(0, 0, 0, 0)
        geom_row.setSpacing(6)
        geom_row.addWidget(self.geometry_combo)
        geom_row.addWidget(self.geometry_rounded)
        geom_row.addStretch(1)
        geom_w = QtWidgets.QWidget(); geom_w.setLayout(geom_row)
        dev_form.addRow(self._lbl("Electrode geometry:"), geom_w)

        # Return / counter-electrode row. Toggle on the left, coating
        # selector in the middle, estimated-OCP-vs-Ag|AgCl readout on
        # the right; matches the layout of the active coating row
        # above so the eye reads them as a paired (active, return)
        # section.
        ret_row = QtWidgets.QHBoxLayout()
        ret_row.setContentsMargins(0, 0, 0, 0)
        ret_row.setSpacing(6)
        ret_row.addWidget(self.return_enable)
        ret_row.addWidget(self.return_coating)
        # Custom-OCP spinbox — auto-hides when the coating isn't
        # ``Custom``. See :meth:`_refresh_return_potential_label`.
        ret_row.addWidget(self.return_custom_ocp_v)
        ret_row.addWidget(self.return_potential_label)
        ret_row.addStretch(1)
        ret_w = QtWidgets.QWidget(); ret_w.setLayout(ret_row)
        dev_form.addRow(self._lbl("Return/counter electrode:"), ret_w)

        # Reference-electrode row — sits IMMEDIATELY below the
        # return-coating row so the user reads them as a paired
        # (counter, reference) section. Same toggle + dropdown +
        # OCP-readout shape so the visual rhythm matches.
        ref_row = QtWidgets.QHBoxLayout()
        ref_row.setContentsMargins(0, 0, 0, 0)
        ref_row.setSpacing(6)
        ref_row.addWidget(self.reference_enable)
        ref_row.addWidget(self.reference_combo)
        # Custom-OCP spinbox — auto-hides when the reference isn't
        # ``Custom``. See :meth:`_refresh_reference_potential_label`.
        ref_row.addWidget(self.reference_custom_ocp_v)
        ref_row.addWidget(self.reference_potential_label)
        ref_row.addStretch(1)
        ref_w = QtWidgets.QWidget(); ref_w.setLayout(ref_row)
        dev_form.addRow(self._lbl("Reference electrode:"), ref_w)

        # "Remember return-electrode potential" toggle — gates whether
        # finished captures feed the return electrode's measured E_ret
        # rest potential into the per-coating learned-OCP store
        # (``electrode_potential_history.record_capture``).  Operator
        # wanted control over this after early builds recorded bad
        # values.  Default ON; the learned value is shown only as a
        # RECOMMENDATION and no longer overrides the user's set defaults
        # (see ``_effective_ref_potential_v``), so leaving it on is safe.
        # Round-trips through prefs under ``remember_return_potential``.
        # QCheckBox can't render rich text (no setTextFormat), so the label —
        # which needs SUBSCRIPTS on E_oc and E_ret (operator: "use subscript
        # for … learn E_oc from E_ret") — is a separate RichText QLabel paired
        # with a text-less checkbox; clicking the label toggles the box.
        self.remember_potential_chk = QtWidgets.QCheckBox()
        self.remember_potential_chk.setChecked(True)
        _rem_tip = (
            "When ON, each finished capture records the return "
            "electrode's measured rest potential (E_ret) into the "
            "per-coating learned-OCP store, building up a measured value "
            "over time.  It is shown as a \"tested\" annotation "
            "next to the reference / return readout but does NOT change "
            "your set water-window limits.  Turn OFF to stop recording — "
            "e.g. if you don't trust the current data (you can also clear "
            "it via Help → Reset learned electrode potentials).")
        self.remember_potential_chk.setToolTip(_rem_tip)
        self.remember_potential_label = QtWidgets.QLabel(
            "Remember return-electrode potential (learn "
            f"{rich.var('E', 'oc')} from {rich.E_RET})")
        self.remember_potential_label.setTextFormat(
            QtCore.Qt.TextFormat.RichText)
        self.remember_potential_label.setToolTip(_rem_tip)
        # Clicking the label toggles the checkbox (same UX as a native label).
        self.remember_potential_label.mousePressEvent = (
            lambda _e: self.remember_potential_chk.toggle())
        _rem_row = QtWidgets.QWidget()
        _rem_h = QtWidgets.QHBoxLayout(_rem_row)
        _rem_h.setContentsMargins(0, 0, 0, 0)
        _rem_h.setSpacing(6)
        _rem_h.addWidget(self.remember_potential_chk)
        _rem_h.addWidget(self.remember_potential_label)
        _rem_h.addStretch(1)
        # Re-render BOTH readouts when toggled so the recommendation tag
        # appears / disappears immediately on each.
        self.remember_potential_chk.toggled.connect(
            self._refresh_reference_potential_label)
        self.remember_potential_chk.toggled.connect(
            self._refresh_return_potential_label)
        self.remember_potential_chk.toggled.connect(
            lambda on: self.settingChanged.emit(
                f"remember return potential = {'ON' if on else 'OFF'}"))
        dev_form.addRow("", _rem_row)

        # Electrode-specific rows, held so `_on_device_changed` can HIDE them
        # for a bare test board (operator: "hide the electrode options since
        # there are no electrodes on there").  `QFormLayout.setRowVisible`
        # (Qt 6.4+) hides the field AND its label together.
        self._dev_form = dev_form
        self._electrode_option_rows = [coat_w, area_w, geom_w,
                                       ret_w, ref_w, _rem_row]

        # Potential limits — cathodic + anodic share the top row;
        # tolerance lives on its own line BELOW the cathodic limit so
        # it visually distinguishes the asymmetric +/- band from the
        # two distinct water-window endpoints. Each inline label uses
        # the project's standard "Name (variable) [unit]:" format
        # from rich.field_label.
        # ORIGINAL (unshifted, vs Ag|AgCl) value — ONE small italic line UNDER
        # EACH limit spinbox showing that limit's original value + the shift,
        # shown ONLY when a reference electrode has SHIFTED the displayed limits
        # (operator: "just show the original values below the adjusted limit" +
        # "the value and shifted under each limit").  Hidden when no shift.
        def _orig_lbl():
            _l = QtWidgets.QLabel("")
            _l.setTextFormat(QtCore.Qt.TextFormat.RichText)
            _l.setWordWrap(True)
            # ``palette(windowText)`` (NOT ``palette(mid)`` — invisible on dark
            # themes, gotcha #87); the italic marks it as a secondary annotation.
            _l.setStyleSheet("color: palette(windowText); font-size: 9pt;")
            _l.setVisible(False)
            return _l
        self.cathodic_original_label = _orig_lbl()
        self.anodic_original_label = _orig_lbl()

        def _limit_col(field_lbl, spin, orig_lbl):
            """Vertical mini-column: [label + spinbox] with the original value
            line UNDER it."""
            _row = QtWidgets.QHBoxLayout()
            _row.setContentsMargins(0, 0, 0, 0)
            _row.setSpacing(6)
            _row.addWidget(self._lbl(field_lbl))
            _row.addWidget(spin)
            _row.addStretch(1)
            _col = QtWidgets.QVBoxLayout()
            _col.setContentsMargins(0, 0, 0, 0)
            _col.setSpacing(2)
            _col.addLayout(_row)
            _col.addWidget(orig_lbl)
            return _col

        lim_top = QtWidgets.QHBoxLayout()
        lim_top.setContentsMargins(0, 0, 0, 0)
        lim_top.setSpacing(6)
        lim_top.addLayout(_limit_col(
            rich.field_label("Cathodic limit", rich.E_LC, "V"),
            self.cathodic_limit_v, self.cathodic_original_label))
        lim_top.addSpacing(16)
        lim_top.addLayout(_limit_col(
            rich.field_label("Anodic limit", rich.E_LA, "V"),
            self.anodic_limit_v, self.anodic_original_label))
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
        lim_v.setSpacing(4)
        lim_v.addLayout(lim_top)      # cathodic + anodic (each w/ original under)
        lim_v.addLayout(lim_bot)      # …then Tolerance
        lim_w = QtWidgets.QWidget(); lim_w.setLayout(lim_v)
        dev_form.addRow(self._lbl("Potential limits:"), lim_w)

        # Environment + Gas sparging directly under Tolerance so
        # the user reads the full electrochemistry context as one
        # block: water-window limits → tolerance band → buffer
        # / tissue context → atmosphere control. The combo + custom
        # text widget for Environment go on one form row; sparge
        # gas gets its own row underneath.
        env_row = QtWidgets.QHBoxLayout()
        env_row.setContentsMargins(0, 0, 0, 0)
        env_row.addWidget(self.environment_combo, stretch=1)
        env_row.addWidget(self.environment_custom, stretch=2)
        env_widget = QtWidgets.QWidget()
        env_widget.setLayout(env_row)
        dev_form.addRow(self._lbl("Environment:"), env_widget)
        # Gas sparging is metadata only — combo with no companion
        # text widget, so a glance at the Setup tab makes the
        # choice immediately obvious. See
        # :data:`stimtest.environments.SPARGE_GAS_PRESETS` for the
        # rationale on why the option exists at all.
        dev_form.addRow(self._lbl("Gas sparging:"), self.sparge_gas_combo)

        dev_box = QtWidgets.QGroupBox("Test device parameters")
        QtWidgets.QVBoxLayout(dev_box).addLayout(dev_form)

        # Oscilloscope acquisition group — Mode + n_avg. n_avg widget
        # holder is the QHBoxLayout we keep a reference to so we can
        # swap the inner widget (combo vs spinbox) at scope-connect.
        acq_form = rich.make_form()
        acq_form.addRow("Mode:", self.acq_mode_combo)
        self._acq_navg_holder = QtWidgets.QHBoxLayout()
        # Zero the wrapper margins so the inner combo/spinbox fills the whole
        # form field column — otherwise the QWidget wrapper's default layout
        # margins inset it and the "Average count" dropdown renders NARROWER
        # than the (unwrapped) Mode + Horizontal-window combos (operator:
        # "make the dropdown list width for average count the same as Mode and
        # Horizontal window").
        self._acq_navg_holder.setContentsMargins(0, 0, 0, 0)
        self._acq_navg_holder.addWidget(self.acq_navg_spin, stretch=1)
        navg_w = QtWidgets.QWidget(); navg_w.setLayout(self._acq_navg_holder)
        self._acq_navg_label = QtWidgets.QLabel("Average count:")
        acq_form.addRow(self._acq_navg_label, navg_w)
        acq_form.addRow("Horizontal window:", self.horiz_scaling_combo)
        acq_box = QtWidgets.QGroupBox("Oscilloscope acquisition")
        acq_vbox = QtWidgets.QVBoxLayout(acq_box)
        acq_vbox.addLayout(acq_form)
        acq_vbox.addWidget(self.ext_trigger_check)

        # Oscilloscope mapping — one row per scope channel, dropdown
        # picks the waveform role assigned to that channel. Labels
        # are kept in self._role_labels so 2-channel scopes can hide
        # the CH3/CH4 rows without rebuilding the layout.
        sf = rich.make_form()
        # Column header row (Role / Bandwidth / Coupling) above the channels.
        _hdr = QtWidgets.QHBoxLayout()
        _hdr.setContentsMargins(0, 0, 0, 0)
        _hdr.setSpacing(4)
        for _t, _stretch in (("Role", 2), ("Bandwidth", 1), ("Coupling", 1)):
            _hl = QtWidgets.QLabel(_t)
            _hl.setStyleSheet("color: #555; font-size: 8pt;")
            _hdr.addWidget(_hl, _stretch)
        _hdr_w = QtWidgets.QWidget()
        _hdr_w.setLayout(_hdr)
        self._scope_map_header = _hdr_w
        sf.addRow(QtWidgets.QLabel(""), _hdr_w)
        for ch in ("CH1", "CH2", "CH3", "CH4"):
            lbl = QtWidgets.QLabel(f"{ch}:")
            self._role_labels[ch] = lbl
            _row = QtWidgets.QHBoxLayout()
            _row.setContentsMargins(0, 0, 0, 0)
            _row.setSpacing(4)
            _row.addWidget(self._role_combos[ch], 2)
            _row.addWidget(self._bw_combos[ch], 1)
            _row.addWidget(self._coupling_combos[ch], 1)
            _rw = QtWidgets.QWidget()
            _rw.setLayout(_row)
            self._scope_role_row_widgets[ch] = _rw
            sf.addRow(lbl, _rw)
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
        # Held so the run-lock can disable the INNER form (inputs +
        # Hardware panel) while leaving the scroll area itself enabled —
        # otherwise the operator can't scroll/read Setup during a run
        # (operator: "allow for scrolling through … Setup and Test
        # Parameters" while an experiment runs).  See ``set_run_locked``.
        self._run_lock_content = left_w
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

        # ---------------- right column: device view + cable map ----------------
        # Operator: "show the cable mapping below the channel mapping on the
        # right side of the setup tab".  The electrode-geometry / channel view
        # (``device_view``) sits on top; the cable pin→channel map group sits
        # beneath it, editable when the "Custom" connector is selected.
        cable_box = QtWidgets.QGroupBox("Cable channel mapping")
        cbv = QtWidgets.QVBoxLayout(cable_box)
        self._cable_map_hint = QtWidgets.QLabel(
            "Device channel → Plexon channel.  Identity means pulsing CH01 "
            "stimulates device CH01.  Pick “Custom” (left) to edit.")
        self._cable_map_hint.setWordWrap(True)
        # NO ``color: palette(mid)`` — it's invisible on dark themes (operator:
        # "I cannot see the text … on dark mode").  Italic reads as a hint
        # while inheriting the theme's default (always-visible) text colour.
        _hf = self._cable_map_hint.font(); _hf.setItalic(True)
        self._cable_map_hint.setFont(_hf)
        cbv.addWidget(self._cable_map_hint)
        cbv.addWidget(self.cable_map_tree)
        right_w = QtWidgets.QWidget()
        rv = QtWidgets.QVBoxLayout(right_w)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.addWidget(self.device_view, 1)
        rv.addWidget(cable_box)

        # ---------------- top-level split ----------------
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self._main_split = split
        split.addWidget(left_scroll)
        split.addWidget(right_w)
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
        # Gate the save/export options on the initial save-path viability
        # (operator: "disable saving options" when there's no viable folder).
        self._refresh_save_options()
        QtCore.QTimer.singleShot(0, self._emit_aliases)
        QtCore.QTimer.singleShot(0, self._emit_limits)

    # ---------------------------------------------------------------- helpers
    def set_run_locked(self, locked: bool) -> None:
        """View-only lock for the duration of a run.

        Disables the form INPUTS (the scrollable left column — which holds
        the Hardware/Connection panel, session, device, scope, acquisition
        and experiment groups) plus the array/device view, so the operator
        can't reconfigure the device, coating, scope mapping, or hardware
        connection mid-run.  Crucially it leaves the SetupTab itself (and
        its enclosing scroll area) ENABLED, so the operator can still scroll
        through and READ every setting while the experiment runs (operator:
        "allow for scrolling through the other tabs … Setup and Test
        Parameters").  Replaces the old blunt ``setEnabled(False)`` on the
        whole tab, which propagated the disabled state to the scroll area
        and froze scrolling.
        """
        for w in (getattr(self, "_run_lock_content", None),
                  getattr(self, "device_view", None)):
            if w is not None:
                w.setEnabled(not locked)

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
        self._refresh_save_options()
        if path:
            self.savePathChanged.emit(path)

    def save_dir_viable(self, path_str: "str | None" = None) -> bool:
        """True ONLY when the save-path folder ALREADY EXISTS and is a
        writable directory.  An empty path, a file, or a non-existent path is
        NOT viable.

        Operator: "There is no viable directory path for saving, then disable
        saving options" — the sin_cont run saved into an ACCIDENTAL folder
        (``…\\GitHub\\test\\StimulationTesting\\…`` — a StimulationTesting
        folder nested inside the ``test`` folder).  The runner ``mkdir``s
        missing folders, so a typo'd path silently CREATES a wrong location;
        requiring the directory to already exist forces the operator to notice
        + confirm the destination (pick it via Browse…, which only offers
        existing folders) before a run can arm its saves.
        """
        import os
        p = (self.save_path.text() if path_str is None else path_str or "").strip()
        if not p:
            return False
        try:
            path = Path(p)
            return path.is_dir() and os.access(path, os.W_OK)
        except Exception:
            return False

    def _refresh_save_options(self) -> None:
        """Enable the save-related options ONLY when the save directory is
        viable; otherwise disable them + flag the path field so the user
        can't arm a save that will fail (operator: "disable saving options"
        when there is no viable directory path)."""
        viable = self.save_dir_viable()
        # Visual cue + tooltip on the path field.
        try:
            self.save_path.setStyleSheet(
                "" if viable else "QLineEdit { border: 1px solid #d9534f; }")
            self.save_path.setToolTip(
                "Folder where session .npz files (and optional .xlsx exports "
                "/ autosaved plots) land. Use Browse… to pick interactively. "
                "Path is made absolute on commit."
                if viable else
                "⚠ This folder can't be used for saving — it doesn't exist "
                "or isn't a writable folder.  Pick an EXISTING folder "
                "(Browse…); the save options stay disabled until then.")
        except Exception:
            pass
        # The auto-export / auto-save toggles are gated on viability.
        for w in (getattr(self, "auto_export_xlsx", None),
                  getattr(self, "auto_save_plots", None)):
            if w is not None:
                w.setEnabled(viable)
        # Format / DPI follow BOTH viability AND the auto-save toggle.
        if getattr(self, "auto_save_plots_fmt", None) is not None:
            self.auto_save_plots_fmt.setEnabled(
                viable and self.auto_save_plots.isChecked())
        self._update_dpi_enabled()

    def _emit_auto_export_xlsx(self, checked: bool):
        """User flipped the auto-export checkbox — broadcast."""
        self.autoExportXlsxChanged.emit(bool(checked))

    def current_auto_export_xlsx(self) -> bool:
        return bool(self.auto_export_xlsx.isChecked())

    def _emit_email_notifications(self, checked: bool):
        self.emailNotificationsChanged.emit(bool(checked))

    def current_email_notifications(self) -> bool:
        return bool(self.email_notifications.isChecked())

    def _update_dpi_enabled(self, *_):
        """Single apply point for the DPI combo's enabled state: enabled
        only when auto-save is ON **and** the format is a raster (not the
        vector SVG, where DPI is meaningless)."""
        on = bool(self.auto_save_plots.isChecked())
        is_svg = self.current_auto_save_plots_format() == "svg"
        viable = self.save_dir_viable()
        self.auto_save_plots_dpi.setEnabled(viable and on and not is_svg)

    def _emit_auto_save_plots(self, *_):
        """Toggle, format-combo, or DPI-combo edited — broadcast the
        (on, fmt, dpi) tuple.  Connected to all three so the runner picks
        up any edit on the next run."""
        self.autoSavePlotsChanged.emit(
            bool(self.auto_save_plots.isChecked()),
            self.current_auto_save_plots_format(),
            self.current_auto_save_plots_dpi(),
        )

    def current_auto_save_plots(self) -> bool:
        return bool(self.auto_save_plots.isChecked())

    def current_auto_save_plots_format(self) -> str:
        ext = self.auto_save_plots_fmt.currentData()
        return str(ext) if ext else "tif"

    def current_auto_save_plots_dpi(self) -> int:
        data = self.auto_save_plots_dpi.currentData()
        try:
            return int(data)
        except (TypeError, ValueError):
            return 600

    def _emit_user_identity(self):
        self.userIdentityChanged.emit(
            self.user_name.text().strip(),
            self.user_email.text().strip(),
        )

    def _emit_sms_recipient(self, *_):
        self.smsRecipientChanged.emit(
            self.current_user_phone(), self.current_user_carrier())

    def current_user_phone(self) -> str:
        # Keep digits only — gateways reject formatting.
        return "".join(ch for ch in self.user_phone.text() if ch.isdigit())

    def current_user_carrier(self) -> str:
        return str(self.user_carrier.currentData() or "")

    def _emit_session_subject(self):
        self.sessionSubjectChanged.emit(self.subject.text().strip())

    def current_user_name(self) -> str:
        return self.user_name.text().strip()

    def current_user_email(self) -> str:
        return self.user_email.text().strip()

    def current_user_institution(self) -> str:
        """Lab / company affiliation typed by the user.

        Returns the trimmed text or an empty string. Used by the
        contribute-electrode-data flow as a pre-populated attribution
        field; never touched by the runner / experiment side.
        """
        return self.user_institution.text().strip()

    # ---------------------------------------------------------- environment
    def current_environment_short(self) -> str:
        """Return the canonical short_code of the selected
        Environment preset (e.g. ``"pbs"``, ``"rat_cortex"``,
        ``"custom"``). Stable across releases — what gets
        persisted to prefs and stamped into saved sessions.
        """
        data = self.environment_combo.currentData()
        return str(data) if data else ""

    def current_environment_custom_text(self) -> str:
        """Free-form description for the ``custom`` preset.
        Empty for non-custom presets.
        """
        if self.current_environment_short() != "custom":
            return ""
        return self.environment_custom.text().strip()

    def current_environment_display(self) -> str:
        """Human-readable label for the active environment
        (preset display name, or ``Custom: <user text>`` when
        the user picked Custom and typed something).
        """
        try:
            from ..environments import display_name_for
        except Exception:
            return self.environment_combo.currentText()
        return display_name_for(
            self.current_environment_short(),
            self.current_environment_custom_text() or None,
        )

    def _on_environment_changed(self, *_):
        """Combo selection changed — toggle the custom-text box's
        visibility, then emit ``environmentChanged``. Custom-text
        edits emit through their own ``editingFinished`` slot."""
        is_custom = (self.current_environment_short() == "custom")
        self.environment_custom.setVisible(is_custom)
        # Clear stale custom text when the user moves AWAY from
        # ``custom`` — leaving it populated would silently get
        # picked up by the next ``custom`` selection and confuse
        # a user who just toggled away to "PBS" and back.
        if not is_custom:
            self.environment_custom.blockSignals(True)
            try:
                self.environment_custom.clear()
            finally:
                self.environment_custom.blockSignals(False)
        self._emit_environment()

    def _emit_environment(self):
        """Fire ``environmentChanged(short_code, custom_text)``."""
        self.environmentChanged.emit(
            self.current_environment_short(),
            self.current_environment_custom_text(),
        )

    # ---------------------------------------------------------- sparge gas
    def current_sparge_gas(self) -> str:
        """Return the canonical short_code of the selected
        sparge-gas preset (``"none"``, ``"n2"``, or ``"ar"``).

        Stable across releases — what gets persisted to prefs
        and stamped into ``setup_snapshot`` so saved sessions
        carry the value forward.
        """
        data = self.sparge_gas_combo.currentData()
        return str(data) if data else "none"

    def current_sparge_gas_display(self) -> str:
        """Human-readable label (e.g. ``"N₂ (nitrogen)"``) for the
        active sparge-gas selection. Used by exports + the
        contribute-data payload.
        """
        try:
            from ..environments import sparge_display_name_for
        except Exception:
            return self.sparge_gas_combo.currentText()
        return sparge_display_name_for(self.current_sparge_gas())

    def _on_sparge_gas_changed(self, *_):
        """Combo selection changed — fire ``spargeGasChanged``.
        No companion text widget here (unlike Environment), so
        the handler is dead-simple: emit the new short_code.
        """
        self.spargeGasChanged.emit(self.current_sparge_gas())

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

    def current_session_stem(self) -> str:
        """``<Notebook>_<Session>`` filesystem stem from the current
        fields — the SAME composition the log filename uses, shared by
        the .npz / .xlsx / plot exports (operator: "the file name should
        be '[notebook]_[session]'").  Empty string when both fields are
        blank.
        """
        session = self._sanitize_filename_part(self.subject.text())
        use_notebook = (self.notebook_check.isChecked()
                        and self.notebook.text().strip())
        if use_notebook:
            notebook = self._sanitize_filename_part(self.notebook.text())
            return f"{notebook}_{session}" if session else notebook
        return session

    def current_notebook(self) -> str:
        """Sanitized notebook field (empty when blank or toggled off)."""
        if not (self.notebook_check.isChecked()
                and self.notebook.text().strip()):
            return ""
        return self._sanitize_filename_part(self.notebook.text())

    def current_log_filename(self) -> str:
        """Compose the on-disk log filename from the current fields.

        With notebook toggle ON and both fields populated:
            ``<Notebook>_<Session>_log.txt``
        With notebook toggle OFF (or notebook field empty):
            ``<Session>_log.txt``
        With BOTH missing (truly fresh launch with cleared fields):
            ``log.txt``
        """
        stem = self.current_session_stem()
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
        """Mode change (combo) or count COMMIT (spin editingFinished) → re-emit
        the (mode, n_avg) pair.

        The count spin is wired to ``editingFinished`` (Enter/return/focus-out),
        NOT ``valueChanged``, so this fires once per commit rather than per
        keystroke (operator).  A no-op commit (focus-out with no change) is
        skipped via ``_last_acq_emitted`` so it doesn't re-log / re-confirm an
        unchanged value.  The enable/grey state is refreshed UNCONDITIONALLY."""
        mode = self.acq_mode_combo.currentText()
        n_avg = self._current_n_avg()
        # Average count is only meaningful in AVERAGE mode — grey it out
        # otherwise so the user sees the value won't be used.  (Always, even on
        # a skipped no-op, so the enable state can't drift.)
        is_avg = mode.upper() == "AVERAGE"
        self._acq_navg_label.setEnabled(is_avg)
        if self.acq_navg_combo is not None:
            self.acq_navg_combo.setEnabled(is_avg)
        self.acq_navg_spin.setEnabled(is_avg)
        # Skip a no-op commit (e.g. a focus-out that changed nothing) so it
        # doesn't re-emit the log line + re-run the scope round-trip.
        if (mode, n_avg) == getattr(self, "_last_acq_emitted", None):
            return
        self._last_acq_emitted = (mode, n_avg)
        self.acquisitionChanged.emit(mode, n_avg)
        # Confirm the count against the CONNECTED scope by actually setting
        # NUMAVg and reading back what it applied (operator: "Always check
        # with commands to the oscilloscope about changing settings by
        # getting what is set to confirm" + "if it is different, probably
        # due to rounding … change the input to reflect that").  Debounced
        # so a commit that snaps + re-broadcasts coalesces; the confirm itself
        # no-ops without a scope or in SAMPLE mode.  See
        # :meth:`_confirm_navg_on_scope`.
        t = getattr(self, "_navg_confirm_timer", None)
        if is_avg and t is not None:
            t.start()

    def _confirm_navg_on_scope(self) -> None:
        """Send the average count to the scope and read back what it set.

        Operator: "When the average count is set, apply it on the
        oscilloscope, get what is set, and if it is different, probably due
        to rounding … change the input to reflect that" + "Always check
        with commands to the oscilloscope about changing settings by
        getting what is set to confirm".

        Calls :meth:`Oscilloscope.set_average_count`, which WRITES
        ``ACQuire:NUMAVg`` and RE-QUERIES the device to return the value it
        actually applied (Tektronix snaps to its power-of-two grid).  If
        the confirmed value differs from what the operator entered, the
        spinbox is updated to the real value (signal-blocked so it doesn't
        re-fire) and the change is logged + re-broadcast so the inline
        editor and every experiment tab reflect it too.

        Touches ONLY NUMAVg (not ``ACQuire:MODe``) so it can't trigger the
        multi-second SAMPLE↔AVERAGE reconfiguration — safe to run on the
        GUI thread from the debounce.  No-op without a connected scope, in
        SAMPLE mode, or while a run is locking the tab.
        """
        scope = getattr(self, "_scope", None)
        if scope is None:
            return
        if self.acq_mode_combo.currentText().upper() != "AVERAGE":
            return
        requested = int(self.acq_navg_spin.value())
        try:
            applied = int(scope.set_average_count(requested))
        except Exception as exc:  # never let a scope hiccup break the GUI
            self.settingChanged.emit(
                f"average count: could not confirm on scope ({exc})")
            return
        if applied <= 0 or applied == requested:
            return
        self.acq_navg_spin.blockSignals(True)
        try:
            self.acq_navg_spin.setValue(applied)
        finally:
            self.acq_navg_spin.blockSignals(False)
        self.settingChanged.emit(
            f"average count = {applied} (oscilloscope set + confirmed; "
            f"you entered {requested})")
        # Re-broadcast the confirmed value so the pattern-panel inline
        # editor + every experiment tab's cached count reflect it.  Emit
        # directly (not via setValue) so this doesn't re-arm the confirm
        # timer.  Keep ``_last_acq_emitted`` coherent with the snapped value so
        # a later no-op focus-out on it is correctly skipped.
        self._last_acq_emitted = (self.acq_mode_combo.currentText(), applied)
        self.acquisitionChanged.emit(
            self.acq_mode_combo.currentText(), applied)

    def _on_horiz_scaling_changed(self, *_):
        """Horizontal-window (Wide/Tight) dropdown changed → re-emit."""
        self.horizontalScalingChanged.emit(self.current_horizontal_scaling())

    def current_horizontal_scaling(self) -> str:
        """The selected horizontal auto-fit window — ``"wide"`` or
        ``"tight"`` (lower-case, for the scope's ``set_horizontal_fit_mode``)."""
        return self.horiz_scaling_combo.currentText().strip().lower()

    def _on_trigger_source_changed(self, *_):
        """EXT-trigger checkbox toggled → re-emit the trigger source.

        Also emits ``digitalTriggerChanged`` so the experiment tabs
        learn about the source-type switch (TTL vs I_mon) atomically
        with the source-name update — otherwise a stale digital-
        trigger flag would mis-route the trigger setup at the next
        run start.
        """
        self.triggerSourceChanged.emit(self.current_trigger_source())
        self.digitalTriggerChanged.emit(self.is_digital_trigger())

    def current_trigger_source(self) -> str:
        """Return the configured trigger source.

        Priority order (matches the operator's three-path mental model):

        1. **EXT** — the digital-sync BNC checkbox is checked (and the
           checkbox is visible, i.e. the scope actually has an EXT
           input).  Returned as the literal string ``"EXT"``; the
           driver knows the firmware owns the trigger level in this
           mode.
        2. **Channel with Role = "Trigger"** — the operator assigned
           the ``Trigger`` role to a scope channel (typically CH3 or
           CH4 because the Plexon digital sync is wired there).  This
           is a TTL sync line, NOT a current monitor — slope is always
           RISE, level is always 1.4 V (TTL midpoint).  Use
           :meth:`is_digital_trigger` to detect this so the experiment
           tab applies the TTL settings instead of the I_mon
           polarity-derived ones.
        3. **I_mon channel** — fall back to whichever channel carries
           the ``I_mon`` role.  Slope auto-flips on phase-1 polarity
           (RISE for anodic-first, FALL for cathodic-first); level
           comes from :func:`imon_trigger_level` keyed on the stim
           amplitude.

        Final fallback is ``"CH2"`` only when no role assignment exists
        at all (defensive default — matches the legacy MATLAB I_mon
        channel mapping).
        """
        if self.ext_trigger_check.isChecked() and self.ext_trigger_check.isVisible():
            return "EXT"
        # Path 2: a channel explicitly tagged as the Trigger sync line.
        # Iterate CH1..CH4 in order so a multi-Trigger assignment (the
        # GUI permits non-unique roles) picks the lowest-numbered one,
        # but in practice CH3/CH4 are where the Plexon digital sync is
        # wired on this bench.
        for ch, cb in self._role_combos.items():
            if cb.currentText() == ROLE_TRIG:
                return ch
        # Path 3: I_mon fallback.
        for ch, cb in self._role_combos.items():
            if cb.currentText() == ROLE_IMON:
                return ch
        return "CH2"

    def is_digital_trigger(self) -> bool:
        """Return True when the trigger source is a digital sync line.

        Digital sync means a TTL edge — either the EXT BNC checkbox
        is checked, or a channel has been assigned Role=Trigger.  In
        either case the trigger setup is fixed:

        * **Slope**: RISE (sync lines are always active-high)
        * **Level**: 1.4 V (TTL midpoint, ignored by the firmware in
          EXT mode where it auto-sets, applied verbatim in channel-
          Trigger mode)

        When this returns False, the trigger source is the I_mon
        channel and the experiment tab must derive slope from phase-1
        polarity and level from :func:`imon_trigger_level`.

        This separation matters because I_mon and a TTL sync line
        have completely different waveform shapes — applying the
        cathodic-polarity slope rule to a sync line would make the
        scope fire on the falling edge of a TTL pulse (the end of the
        sync, not the start).
        """
        if self.ext_trigger_check.isChecked() and self.ext_trigger_check.isVisible():
            return True
        for cb in self._role_combos.values():
            if cb.currentText() == ROLE_TRIG:
                return True
        return False

    # ``current_trigger_slope`` was removed when the operator-facing
    # edge selector was deleted — the experiment tabs resolve slope
    # from the trigger-source / phase-1-polarity rule set internally
    # at run start (see ``_BaseExperimentTab._start_runner``).

    def current_acquisition(self) -> tuple[str, int]:
        """Return the ``(mode, n_avg)`` oscilloscope-acquisition pair —
        the same payload ``acquisitionChanged`` emits.  Used by
        MainWindow to pull the scope acquisition settings into the
        active experiment tab on entering the Test parameters page."""
        return (self.acq_mode_combo.currentText(), self._current_n_avg())

    def _current_n_avg(self) -> int:
        # Prefer the discrete-choice combo whenever it EXISTS — it holds
        # the user's selected averaging count.  Do NOT gate on
        # ``isVisible()``: a Qt widget reports not-visible whenever its
        # page isn't the active tab, and the acquisition combo lives on
        # the Setup tab.  The moment the user navigates to Test
        # parameters (which is exactly when the entry-sync calls
        # ``current_acquisition()`` to pull settings into the runner),
        # the Setup page is hidden → ``isVisible()`` is False → this used
        # to fall back to the stale hidden ``acq_navg_spin`` (still at the
        # prefs default) and silently lose the user's combo selection
        # (e.g. 64 → 32).  The spin is the fallback ONLY when no combo was
        # built (scope without a discrete NUMAVg list / no scope yet).
        if self.acq_navg_combo is not None:
            data = self.acq_navg_combo.currentData()
            try:
                return int(data) if data is not None else int(self.acq_navg_combo.currentText())
            except (TypeError, ValueError):
                return int(self.acq_navg_spin.value())
        return int(self.acq_navg_spin.value())

    def _refresh_cable_map_tree(self, *_a) -> None:
        """Populate the cable-map tree — one row per **device channel → Plexon
        stim channel** (operator: "I want the cable mapping to be like a tree
        that points to the location.  When pulsing CH01, it should be device
        CH01").  Column 0 is the channel the operator SELECTS / sees (the
        device channel); column 1 is the PLEXON stim channel the cable wires it
        to — the value PULSAR actually commands (MATLAB ``Channels.Plexon``).
        Rows where the two differ are bold (a re-mapped cable).  When the
        "Custom" connector is selected column 1 is EDITABLE — values come from
        ``self._custom_cable_map`` (identity default) and edits flow back
        through :meth:`_on_cable_map_item_changed`.  No-op before the tree
        exists / when the connector is unknown."""
        tree = getattr(self, "cable_map_tree", None)
        if tree is None:
            return
        name = self.connector_combo.currentText()
        is_custom = (name == "Custom")
        plexon = self._cable_pin_to_channel(name)   # device i → Plexon plexon[i]
        if plexon is None:
            return
        # Populating fires itemChanged for every setText/flag write — guard it
        # so a repopulate never reads back as a user edit.
        self._cable_map_editing = True
        try:
            tree.clear()
            tree.setEditTriggers(
                QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked
                | QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked
                if is_custom else
                QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
            tree.setSelectionMode(
                QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
                if is_custom else
                QtWidgets.QAbstractItemView.SelectionMode.NoSelection)
            _remap = 0
            for dev, plx in enumerate(plexon, start=1):
                plx = int(plx)
                it = QtWidgets.QTreeWidgetItem([f"CH{dev:02d}", f"CH{plx:02d}"])
                it.setTextAlignment(0, QtCore.Qt.AlignmentFlag.AlignLeft)
                it.setData(0, QtCore.Qt.ItemDataRole.UserRole, dev)
                if is_custom:                 # unlock col-1 for editing
                    it.setFlags(it.flags()
                                | QtCore.Qt.ItemFlag.ItemIsEditable)
                if plx != dev:                # non-identity → highlight
                    _remap += 1
                    _f = it.font(1); _f.setBold(True)
                    it.setFont(0, _f); it.setFont(1, _f)
                tree.addTopLevelItem(it)
            tree.resizeColumnToContents(0)
            # Header hints identity / re-mapped / custom at a glance.
            _tag = ("  (custom — double-click to edit)" if is_custom
                    else f"  ({_remap} re-mapped)" if _remap
                    else "  (identity)")
            tree.setHeaderLabels(["Device channel", "Plexon channel" + _tag])
        finally:
            self._cable_map_editing = False
        hint = getattr(self, "_cable_map_hint", None)
        if hint is not None:
            hint.setText(
                "Type each device channel's Plexon channel (1–16); the map "
                "stays a permutation (editing one swaps with the channel that "
                "held it)."
                if is_custom else
                "Device channel → Plexon channel.  Identity means pulsing "
                "CH01 stimulates device CH01.  Pick “Custom” (left) to edit.")

    def _cable_pin_to_channel(self, name: str) -> Optional[tuple]:
        """The 16-entry **device→Plexon** tuple for connector ``name`` (index
        ``i`` = device channel ``i+1`` → Plexon stim channel).

        For "Custom" this is ``self._custom_cable_map`` overlaid on identity
        (any device channel the operator hasn't re-assigned stays CH N → Plexon
        CH N); for a catalogued connector it's the frozen
        ``Connector.pin_to_channel`` array.  Returns ``None`` for an unknown
        connector."""
        if name == "Custom":
            return tuple(self._custom_cable_map.get(dev, dev)
                         for dev in range(1, 17))
        conn = CONNECTORS.get(name)
        return None if conn is None else tuple(conn.pin_to_channel)

    def current_channel_map(self) -> dict:
        """The active **device→Plexon** channel map as ``{device: plexon}``,
        NON-identity entries only (empty ⇒ identity / no translation).

        This is what the runner installs via
        :meth:`ExperimentRunner.set_channel_map` so that pulsing a device
        channel commands the Plexon stim channel the cable wires it to, while
        captures stay labelled by the device channel (operator: "when pulsing
        CH01, it should be device CH01").  The test board + every identity
        cable return ``{}`` → the runner never wraps."""
        plexon = self._cable_pin_to_channel(self.connector_combo.currentText())
        if plexon is None:
            return {}
        return {dev: int(plx) for dev, plx in enumerate(plexon, start=1)
                if int(plx) != dev}

    def _on_cable_map_item_changed(self, item, column) -> None:
        """Apply a user edit to the Custom cable map, keeping it a valid
        PERMUTATION (each device channel is driven by exactly ONE connector
        pin — the cable is physically a bijection).

        Editing pin P's device channel to CH C SWAPS channels with whichever
        pin currently drives CH C, so the map stays a permutation and a single
        edit is always possible (in a full identity map every channel is
        already taken, so a plain reject-on-duplicate rule would make ALL edits
        impossible).  Out-of-range / non-numeric input is rejected and the cell
        reverts.  Accepts ``CH07`` / ``7`` / ``ch 7``.  Only col-1 under the
        "Custom" connector is live."""
        if (self._cable_map_editing or column != 1
                or self.connector_combo.currentText() != "Custom"):
            return
        pin = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if pin is None:
            return
        pin = int(pin)
        # Resolve the FULL current permutation (identity for un-edited pins).
        cur = {p: int(self._custom_cable_map.get(p, p)) for p in range(1, 17)}
        old = cur[pin]
        raw = item.text(1).strip().upper().replace("CH", "").strip()
        try:
            ch = int(raw)
            if not (1 <= ch <= 16):
                raise ValueError("out of range")
        except ValueError:
            self._revert_cable_cell(item, old)
            return
        if ch == old:
            return
        # Swap: the pin that currently drives CH ``ch`` inherits P's old channel.
        victim = next((p for p, c in cur.items() if c == ch and p != pin), None)
        cur[pin] = ch
        if victim is not None:
            cur[victim] = old
        # Store only the non-identity entries (keeps the prefs blob compact).
        self._custom_cable_map = {p: c for p, c in cur.items() if c != p}
        # Update ONLY the two affected rows, in place.  Do NOT clear/repopulate
        # the tree here — that would delete ``item`` while its own itemChanged
        # signal is still on the stack (use-after-free risk on a real display).
        self._cable_map_editing = True
        try:
            self._set_cable_row(item, pin, ch)
            if victim is not None:
                vitem = self._cable_item_for_pin(victim)
                if vitem is not None:
                    self._set_cable_row(vitem, victim, old)
        finally:
            self._cable_map_editing = False
        self._emit_array()
        self.settingChanged.emit(
            f"custom cable map: pin {pin} → Plexon CH{ch:02d}"
            + (f" (swapped pin {victim} → CH{old:02d})"
               if victim is not None else ""))

    def _cable_item_for_pin(self, pin: int):
        tr = self.cable_map_tree
        for i in range(tr.topLevelItemCount()):
            it = tr.topLevelItem(i)
            if it.data(0, QtCore.Qt.ItemDataRole.UserRole) == pin:
                return it
        return None

    def _set_cable_row(self, item, pin: int, ch: int) -> None:
        """Set a row's device-channel text + bold-if-remapped (caller guards
        ``_cable_map_editing``)."""
        item.setText(1, f"CH{int(ch):02d}")
        _f = item.font(1); _f.setBold(int(ch) != int(pin))
        item.setFont(0, _f); item.setFont(1, _f)

    def _revert_cable_cell(self, item, prev: int) -> None:
        self._cable_map_editing = True
        try:
            item.setText(1, f"CH{int(prev):02d}")
        finally:
            self._cable_map_editing = False

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
        2-channel scope (TBS1052C/1072C/1102C).

        **Hidden channels keep their stored role** (operator: "the
        choices for oscilloscope channels is not being remembered") — the
        earlier version reset them to ``ROLE_NONE`` here, but that wiped
        the user's deliberate CH3/CH4 choice the moment a 2-channel scope
        (or a transient channel-count mis-probe) connected; the next prefs
        save then persisted the ``None`` and the choice was lost across
        restarts.  Instead we record the available count in
        ``self._n_visible_scope_channels`` and :meth:`current_aliases`
        SKIPS channels beyond it, so a hidden role can't leak onto a
        channel that doesn't exist AND the choice survives — reappearing
        intact when a 4-channel scope reconnects.

        ``n_channels`` is clamped to [2, 4]: scopes outside that range
        aren't supported by this codebase (every PlexStim experiment
        wants at minimum V_mon and I_mon, and the catalog tops out at
        4-channel TBS2204B-class hardware).
        """
        n = max(2, min(4, int(n_channels)))
        self._n_visible_scope_channels = n
        for i, ch in enumerate(("CH1", "CH2", "CH3", "CH4"), start=1):
            visible = (i <= n)
            lbl = self._role_labels.get(ch)
            # Hide the WHOLE row (role + bandwidth + coupling) together so a
            # 2-channel scope doesn't show orphaned bandwidth/coupling combos.
            row = getattr(self, "_scope_role_row_widgets", {}).get(ch)
            if lbl is not None:
                lbl.setVisible(visible)
            if row is not None:
                row.setVisible(visible)
            else:
                cb = self._role_combos.get(ch)
                if cb is not None:
                    cb.setVisible(visible)
        # Re-emit so any listeners (the runner aliases path) see the
        # cleaned-up mapping immediately rather than waiting for the
        # next user click.  current_aliases() already drops roles on
        # channels beyond ``n`` so the runner never sees a hidden one.
        self._refresh_scope_option_enabled()
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
        # Remember the connected scope so the average-count confirm
        # (``_confirm_navg_on_scope``) can round-trip NUMAVg through it.
        # ``None`` on disconnect → the confirm becomes a no-op.
        self._scope = scope
        # Channel-count visibility: read from scope.info if present,
        # else default to all 4 visible (the user might be running
        # offline / simulated, where we don't constrain).
        n_channels = 4
        has_ext = False   # safe default: hide EXT until scope confirms it
        if scope is not None:
            try:
                n_channels = int(getattr(scope.info, "n_channels", 4) or 4)
            except (TypeError, ValueError, AttributeError):
                n_channels = 4
            try:
                has_ext = bool(getattr(scope.info, "has_ext_trigger", False))
            except AttributeError:
                has_ext = False
        self._set_visible_scope_channels(n_channels)
        self._refresh_scope_trigger_hint(has_ext=has_ext)
        # EXT trigger checkbox — hide entirely when scope has no EXT BNC input
        # (e.g. TBS1000C, TBS1000B-EDU). Uncheck first so the trigger source
        # reverts to I_mon before the widget disappears.
        if not has_ext and self.ext_trigger_check.isChecked():
            self.ext_trigger_check.blockSignals(True)
            self.ext_trigger_check.setChecked(False)
            self.ext_trigger_check.blockSignals(False)
        self.ext_trigger_check.setVisible(has_ext)
        self._scope_has_ext = has_ext
        self._on_trigger_source_changed()

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

        # n_avg widget — ALWAYS an input spinbox with up/down arrows
        # (operator: "let average count be an input number with up and down
        # arrows … set the limit based on the oscilloscope model").  The range
        # is the connected model's NUMAVg floor/ceiling
        # (``average_count_choices`` min/max, or ``max_average_count``); the
        # scope snaps a typed count to its nearest supported value on apply
        # (``set_acquisition_mode``, gotcha #84).  No dropdown — older builds
        # showed a combo for fixed lists; that's gone.
        prev_navg = self._current_n_avg()
        # Drop any legacy combo left over from a prior rebuild.
        if self.acq_navg_combo is not None:
            self._acq_navg_holder.removeWidget(self.acq_navg_combo)
            self.acq_navg_combo.deleteLater()
            self.acq_navg_combo = None
        _lo = int(min(choices)) if choices else 2
        _hi = int(max_n) if max_n else (int(max(choices)) if choices else 512)
        if _hi < _lo:
            _hi = _lo
        self.acq_navg_spin.setVisible(True)
        self.acq_navg_spin.setRange(_lo, _hi)
        self.acq_navg_spin.setValue(max(_lo, min(int(prev_navg), _hi)))
        # Hover tip lists the connected model's TYPICAL NUMAVg choices
        # (operator: "the hover message should include the typical choices for
        # the oscilloscope model") — a typed off-grid value is snapped to the
        # nearest of these on apply.
        _tip = ("Number of captures to average together in AVERAGE mode. "
                "Higher = cleaner traces but a longer capture.")
        if choices:
            _tip += ("\n\nTypical values for this scope: "
                     + ", ".join(str(int(c)) for c in choices)
                     + f"  (range {_lo}–{_hi}; a typed value snaps to the "
                     "nearest supported count).")
        else:
            _tip += f"\n\nRange for this scope: {_lo}–{_hi}."
        self.acq_navg_spin.setToolTip(_tip)
        self._on_acq_changed()

    def _apply_scope_role_options(self, allow_electrode_roles: bool) -> None:
        """Show / hide the E_act + E_ret roles in the scope-mapping combos.

        Operator: "when the Test Board is selected, do not show Eret and Eact
        as options."  A bare test board has no active / return electrode, so
        only V_mon / I_mon / Trigger / None make sense.  Removing the two roles
        rebuilds each combo's item list; a channel that HELD E_act / E_ret is
        stashed (``self._stashed_electrode_roles``) and RESTORED when a real
        electrode array is re-selected (gotcha #49 — remember the operator's
        scope choices).  Signals are blocked so this programmatic re-fill never
        reads as a user change (would trip ``_scope_roles_user_configured``)."""
        roles = (SCOPE_ROLES if allow_electrode_roles else
                 tuple(r for r in SCOPE_ROLES
                       if r not in (ROLE_EACT, ROLE_ERET)))
        for ch, cb in self._role_combos.items():
            cur = cb.currentText()
            cb.blockSignals(True)
            cb.clear()
            cb.addItems(roles)
            if allow_electrode_roles and ch in self._stashed_electrode_roles:
                cb.setCurrentText(self._stashed_electrode_roles.pop(ch))
            elif cur in roles:
                cb.setCurrentText(cur)
            else:
                # ``cur`` was E_act / E_ret and we're hiding them — stash it so
                # a switch back to a real device restores the choice.
                if cur in (ROLE_EACT, ROLE_ERET):
                    self._stashed_electrode_roles[ch] = cur
                cb.setCurrentText(ROLE_NONE)
            cb.blockSignals(False)
        # Signals were blocked above, so _on_role_changed didn't fire — refresh
        # the bandwidth/coupling enabled-state directly (a channel forced to
        # None must grey them; a channel restored to a real role on switch-back
        # must re-enable them).
        self._refresh_scope_option_enabled()

    def apply_default_scope_mapping(self):
        """Fill the channel-role combos with their catalog defaults — but
        ONLY on a genuinely fresh setup, never overriding the operator.

        ``MainWindow`` calls this on EVERY scope connect.  The earlier
        version filled any combo at ``None`` with the default, so a
        DELIBERATE ``None`` (operator: "I have been using NONE for CH3")
        was re-filled with the catalog role (E_ret) on every reconnect —
        the choice "was not being remembered".  Now once the mapping has
        been configured — by restored prefs, by a user combo change, or by
        a prior call to this method — ``_scope_roles_user_configured`` is
        True and this is a NO-OP.  So defaults apply exactly once (a fresh
        install's first connect, when nothing is configured) and the
        operator's subsequent choices — including ``None`` — always
        survive a disconnect / reconnect / restart.
        """
        if self._scope_roles_user_configured:
            return
        for ch, default_role in DEFAULT_CHANNEL_ROLES.items():
            cb = self._role_combos.get(ch)
            if cb is not None and cb.currentText() == ROLE_NONE:
                cb.setCurrentText(default_role)
        # The mapping is now configured (by the default) — don't re-apply
        # on subsequent connects, so a later user-set None sticks.
        self._scope_roles_user_configured = True

    def clear_scope_mapping(self):
        """Restore all 4 channel rows to visible and reset the trigger
        hint when the oscilloscope disconnects — but KEEP the user's
        channel→role assignments.

        The roles used to be wiped to ``None`` here "so the GUI doesn't
        claim a mapping it can't honour", but that wipe was the cause of
        the operator bug "the choices for oscilloscope channels is not
        being remembered. CH3 is constantly set to Eret": a disconnect
        reset every role to ``None``, the next prefs save persisted those
        ``None``s, and on the following connect ``apply_default_scope_
        mapping`` re-applied the catalog default (CH3 = E_ret).  The
        channel→role map is only ever consumed at RUN time (which
        requires a connected scope), so a "stale" map while disconnected
        is harmless — and keeping it means the user's choices survive a
        disconnect / reconnect / restart and round-trip through prefs.
        Switching to a SMALLER scope is still handled:
        ``apply_scope_capabilities`` → ``_set_visible_scope_channels(n)``
        clears CH3/CH4 for a genuine 2-channel scope on reconnect.
        """
        self._set_visible_scope_channels(4)
        self._refresh_scope_trigger_hint(has_ext=True)

    def _on_role_changed(self, *_):
        """A role dropdown changed — re-emit aliases AND re-emit the
        trigger source when the EXT checkbox is unchecked.

        Role uniqueness is not enforced (the user might want to inspect
        the same waveform on two channels), but the runner uses the
        *first* match for any given role, so duplicates are harmless
        from the GUI's side.

        Trigger-source re-emit is critical when the operator moves the
        I_mon role between channels: ``current_trigger_source()``
        returns the channel assigned to I_mon, so moving I_mon from CH2
        to CH4 changes what the trigger source *should* be — but the
        experiment-tab cache (``_trigger_source``) only refreshes when
        ``triggerSourceChanged`` fires.  Without this re-emit, the tab
        keeps using whatever channel was active the last time the EXT
        checkbox was toggled (typically the default ``CH2``), so a run
        triggers on the wrong channel even though the Setup tab's
        dropdown shows the correct one.
        """
        # Any role change — a user pick OR a restore_prefs setCurrentText
        # — means the mapping is now configured; ``apply_default_scope_
        # mapping`` must not stomp it on the next connect.  (Not fired
        # during construction: the combos connect this slot AFTER their
        # initial ``setCurrentText(None)``.)
        self._scope_roles_user_configured = True
        # A channel set to None has nothing to configure → grey its Bandwidth +
        # Coupling dropdowns (operator: "if an oscilloscope channel is set to
        # none, disable the bandwidth and coupling dropdown lists").
        self._refresh_scope_option_enabled()
        self._emit_aliases()
        # Re-emit trigger source whenever a role change *could* affect
        # the resolved source.  When EXT is checked, none of the role
        # combos matter for the trigger — skip the emit to avoid
        # churn.  When EXT is unchecked, both ``Trigger`` and ``I_mon``
        # role changes can change which channel becomes the source, so
        # always re-emit.  digitalTriggerChanged also goes out because
        # promoting/demoting Role=Trigger flips the TTL-vs-I_mon
        # physics path even though the source name might stay the same
        # (e.g. CH2 stays I_mon but loses or gains the Trigger flag).
        if not (self.ext_trigger_check.isChecked()
                and self.ext_trigger_check.isVisible()):
            self.triggerSourceChanged.emit(self.current_trigger_source())
            self.digitalTriggerChanged.emit(self.is_digital_trigger())

    def _on_channel_scope_opt_changed(self, kind: str, ch: str) -> None:
        """A per-channel bandwidth / coupling dropdown changed — log it (these
        are hardware-setup preferences applied to the scope at run start, NOT
        part of the alias mapping, so they don't re-emit ``aliasesChanged``)."""
        combo = (self._bw_combos if kind == "bandwidth"
                 else self._coupling_combos).get(ch)
        val = combo.currentText() if combo is not None else "?"
        self.settingChanged.emit(f"{ch} {kind} = {val}")

    def _refresh_scope_option_enabled(self) -> None:
        """Grey the Bandwidth + Coupling dropdowns of any scope channel whose
        Role is None — there's nothing to configure on an unused channel
        (operator: "if an oscilloscope channel is set to none, disable the
        bandwidth and coupling dropdown lists").

        ALSO lock the I_mon channel's BANDWIDTH when there is NO Trigger channel:
        I_mon then becomes the trigger source and its bandwidth is forced to
        20 MHz at run start (gotcha #162), so editing it is meaningless
        (operator: "disable editing the Imon bandwidth if there is no trigger
        channel").  While locked the combo is pinned to "20 MHz" so the display
        is honest; the operator's prior choice is stashed and restored when a
        Trigger channel is re-added.

        Runs on every role change + construction + prefs restore + visible-
        channel changes."""
        has_trigger = any(rc.currentText() == ROLE_TRIG
                          for rc in self._role_combos.values())
        imon_ch = next((ch for ch, rc in self._role_combos.items()
                        if rc.currentText() == ROLE_IMON), None)
        for ch, rc in self._role_combos.items():
            role_used = rc.currentText() != ROLE_NONE
            bw = self._bw_combos.get(ch)
            cp = self._coupling_combos.get(ch)
            # I_mon-as-trigger (no Trigger channel) → its bandwidth is locked.
            imon_is_trigger = (ch == imon_ch and not has_trigger)
            if bw is not None:
                bw.setEnabled(role_used and not imon_is_trigger)
            if cp is not None:
                cp.setEnabled(role_used)
        self._sync_locked_imon_bandwidth(imon_ch, has_trigger)

    def _sync_locked_imon_bandwidth(self, imon_ch, has_trigger) -> None:
        """Pin the I_mon bandwidth combo to "20 MHz" while I_mon is the trigger
        (no Trigger channel), stashing the prior choice; restore it when a
        Trigger channel is re-added.  Value change is signal-guarded so it
        doesn't read as a user edit."""
        bw = self._bw_combos.get(imon_ch) if imon_ch is not None else None
        if bw is None:
            return
        if not has_trigger:
            # Entering the locked state — stash the prior (non-forced) choice.
            if getattr(self, "_imon_bw_stash", None) is None \
                    and bw.currentText() != SCOPE_BW_20MHZ:
                self._imon_bw_stash = bw.currentText()
            if bw.currentText() != SCOPE_BW_20MHZ:
                bw.blockSignals(True)
                bw.setCurrentText(SCOPE_BW_20MHZ)
                bw.blockSignals(False)
        else:
            # A Trigger channel exists → restore the stashed choice.
            stash = getattr(self, "_imon_bw_stash", None)
            if stash is not None:
                bw.blockSignals(True)
                bw.setCurrentText(stash)
                bw.blockSignals(False)
                self._imon_bw_stash = None

    def current_channel_bandwidths(self) -> dict:
        """``{CHx: 'Auto'|'Full'|'20 MHz'}`` for every VISIBLE scope channel.
        ``Auto`` means "use the automatic policy"; the run-start push passes
        only the concrete choices to the scope."""
        out = {}
        for i, ch in enumerate(("CH1", "CH2", "CH3", "CH4"), start=1):
            if i > self._n_visible_scope_channels:
                continue
            cb = self._bw_combos.get(ch)
            if cb is not None:
                out[ch] = cb.currentText()
        return out

    def current_channel_couplings(self) -> dict:
        """``{CHx: 'Auto'|'DC'|'AC'}`` for every VISIBLE scope channel."""
        out = {}
        for i, ch in enumerate(("CH1", "CH2", "CH3", "CH4"), start=1):
            if i > self._n_visible_scope_channels:
                continue
            cb = self._coupling_combos.get(ch)
            if cb is not None:
                out[ch] = cb.currentText()
        return out

    # ---------------------------------------------------------------- slots
    def _populate_connector_choices(self, dev) -> None:
        """Fill the cable dropdown with the connectors valid for ``dev``.

        A device may pin ``cable_choices`` to a whitelist (the Plexon
        Test Board offers only its two physical cables — "Large Black
        Omnetics" + "2×8 Pin Receptacle"); every other device gets the
        full :data:`CONNECTORS` list.  Preserves the current selection
        when it survives into the new list so a device→device→back
        round-trip doesn't drop the user's cable choice.  Callers must
        block signals around this (it's part of the device-change
        sequence which sets the connector itself).
        """
        choices = tuple(getattr(dev, "cable_choices", ()) or ()) or tuple(
            CONNECTORS.keys())
        current = self.connector_combo.currentText()
        # Only rebuild when the set actually changed — avoids needless
        # clear()/re-add churn (and the resulting currentTextChanged even
        # under blocked signals is a no-op, but the clear itself resets
        # the index).
        existing = [self.connector_combo.itemText(i)
                    for i in range(self.connector_combo.count())]
        if existing == list(choices):
            return
        self.connector_combo.clear()
        for k in choices:
            self.connector_combo.addItem(k)
        if current in choices:
            self.connector_combo.setCurrentText(current)

    def _apply_device_ui_constraints(self, dev) -> None:
        """Apply the STRUCTURAL, device-dependent UI constraints that must
        hold whether the device was picked interactively OR restored from
        prefs at startup.

        Three things, all keyed on the device:

        * the cable-dropdown whitelist (``_populate_connector_choices`` —
          the Plexon Test Board offers only its two physical cables);
        * electrode-option-row visibility (a bare test board hides
          coating / area / geometry / return + reference electrode);
        * scope-role filtering (``_apply_scope_role_options`` — a test
          board drops E_act / E_ret so only V_mon / I_mon / Trigger /
          None remain).

        Deliberately EXCLUDES the value defaults (area / coating /
        geometry / connector selection) that ``_on_device_changed`` also
        resets — those are the operator's to keep on a prefs restore, so
        restore_prefs must NOT reset them.  This is the shared source of
        truth so a RESTORED test board is constrained identically to an
        interactively-selected one (operator: "When the Test Board is
        selected only Vmon, Imon, and Trigger are the only available
        oscilloscope channels" — which also has to hold after a restart
        that restores the test board from prefs).
        """
        self._populate_connector_choices(dev)
        has_elec = getattr(dev, "has_electrodes", True)
        for _w in getattr(self, "_electrode_option_rows", []):
            self._set_form_row_visible(_w, has_elec)
        self._apply_scope_role_options(allow_electrode_roles=has_elec)

    def _on_device_changed(self, name: str):
        # Save outgoing state for any user-named custom device so a
        # round-trip (custom A → built-in → custom A) doesn't lose
        # the mapping or grid type the user typed.
        prev = self._prev_device_name
        if prev in self._device_custom_entries:
            self._device_custom_state[prev] = {
                "layout": self.device_view.current_layout(),
                "mapping": self.device_view.current_mapping().tolist(),
            }
        # ``Other (custom grid)`` is a TRIGGER — pop the name dialog,
        # add a new entry on Confirm, then recurse with the typed
        # name so the rest of this function applies the new state.
        # On Cancel revert to whatever was previously selected.
        if name == CUSTOM_DEVICE_TRIGGER:
            self._prompt_for_custom(
                "test device",
                self.device_combo,
                "_prev_device_name",
                self._device_custom_entries,
                on_confirm=self._on_custom_device_confirmed,
            )
            return
        # Branch: a user-named custom device → fall back to the
        # ``Other (custom grid)`` template, then override the title
        # and apply the per-device saved state.
        if name in self._device_custom_entries:
            self._apply_custom_device(name)
            self._prev_device_name = name
            self._emit_array()
            return
        # Built-in device — original flow.
        if name not in DEVICES:
            return
        dev = DEVICES[name]
        self.device_view.set_device(dev)
        # Apply device-recommended defaults to area / coating / connector
        self.connector_combo.blockSignals(True)
        # Structural, device-dependent UI constraints — cable-dropdown
        # whitelist (repopulated FIRST so the setCurrentText below picks
        # from the right list), electrode-row visibility, and scope-role
        # filtering.  Shared with restore_prefs (see the helper docstring).
        self._apply_device_ui_constraints(dev)
        self.connector_combo.setCurrentText(dev.default_connector)
        self.connector_combo.blockSignals(False)
        # Signals were blocked above, so the cable-map tree didn't follow the
        # new default connector — refresh it explicitly.
        self._refresh_cable_map_tree()
        # The Cable dropdown stays visible for EVERY device — it now drives the
        # device→Plexon channel translation (not just a cosmetic pinout), which
        # is meaningful for any array (operator: "cable selection should be a
        # dropdown list below the Device dropdown list").  (It used to hide for
        # the 1-D Linear device.)
        self._set_form_row_visible(self.connector_combo, True)
        # Electrode-row visibility + the E_act/E_ret scope-role filtering are
        # applied above via ``_apply_device_ui_constraints(dev)`` (shared with
        # restore_prefs so a restored test board is constrained too).
        # The Grid-type chooser is only meaningful for the *Other (custom
        # grid)* device or any user-named custom-device entry — built-
        # ins ship with a fixed layout in their ``DeviceDef.layout``.
        is_custom = (name == CUSTOM_DEVICE_TRIGGER
                     or name in self._device_custom_entries)
        self.grid_type_combo.setVisible(is_custom)
        self.grid_type_combo.blockSignals(True)
        try:
            current_layout = self.device_view.current_layout()
            idx = self.grid_type_combo.findData(current_layout)
            if idx >= 0:
                self.grid_type_combo.setCurrentIndex(idx)
        finally:
            self.grid_type_combo.blockSignals(False)
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
        # Apply per-device geometry default. Blackrock UEA /
        # MicroProbes FMA → cone (etched / tapered tips);
        # UTD MEA / NeuroNexus → circle (planar disk pads).
        self.geometry_combo.blockSignals(True)
        try:
            geom_idx = self.geometry_combo.findData(dev.default_geometry)
            if geom_idx >= 0:
                self.geometry_combo.setCurrentIndex(geom_idx)
        finally:
            self.geometry_combo.blockSignals(False)
        # Rounded default tracks the device too (most arrays don't
        # have rounded square/rect pads, so default_rounded is False
        # in DeviceDef).
        self.geometry_rounded.blockSignals(True)
        try:
            self.geometry_rounded.setChecked(bool(dev.default_rounded))
        finally:
            self.geometry_rounded.blockSignals(False)
        # Refresh Rounded-toggle visibility for the new geometry.
        # Inlined (rather than calling ``_on_geometry_changed``) to
        # avoid an extra ``_emit_array()`` — the trailing call below
        # is the canonical post-device-load emit.
        code = self.geometry_combo.currentData() or ""
        self.geometry_rounded.setVisible(
            code in self._SQUARE_OR_RECT_GEOMETRIES)
        self._prev_device_name = name
        self._emit_array()

    def _on_custom_device_confirmed(self, name: str) -> None:
        """Dialog Confirm callback — initialise per-device state for
        a new user-named custom device, apply it to the view, and
        emit ``arrayChanged`` so downstream listeners pick up the
        new device. Re-confirming an existing name is a no-op for the
        state dict (the saved layout / mapping survive)."""
        self._device_custom_state.setdefault(name, {
            "layout": "rect",
            "mapping": np.zeros((4, 4), dtype=int).tolist(),
        })
        self._apply_custom_device(name)
        self._prev_device_name = name
        self._emit_array()

    def _apply_custom_device(self, name: str) -> None:
        """Push a user-named custom device's saved state into the
        device view: title, layout, mapping, plus the grid-type
        chooser visibility. Falls back to the ``Other (custom grid)``
        DeviceDef as a template so all the other Setup-tab plumbing
        (connector, area, coating defaults) stays working.
        """
        state = self._device_custom_state.get(name) or {}
        # Use ``Other (custom grid)`` as a template for the other
        # tab fields (area, coating, connector defaults).
        template = DEVICES.get(CUSTOM_DEVICE_TRIGGER)
        if template is not None:
            # Don't call ``set_device`` — that would force the
            # template's mapping back. Just apply the side-effect
            # parts (defaults) and override the header.
            self.connector_combo.blockSignals(True)
            self.connector_combo.setCurrentText(template.default_connector)
            self.connector_combo.blockSignals(False)
            unit_factor = UNITS[self.area_unit.currentText()]
            self.area_value.blockSignals(True)
            self.area_value.setValue(
                template.default_surface_area_um2 / unit_factor)
            self.area_value.blockSignals(False)
            if template.default_coating in COATINGS:
                self.coating_combo.blockSignals(True)
                idx = self.coating_combo.findData(template.default_coating)
                if idx >= 0:
                    self.coating_combo.setCurrentIndex(idx)
                self.coating_combo.blockSignals(False)
                self.coating_custom.setVisible(False)
        # Grid type
        layout = state.get("layout", "rect")
        self.device_view.set_layout(layout)
        # Mapping — fall back to a 4x4 zero grid for fresh entries
        mapping = np.asarray(state.get("mapping",
                                        np.zeros((4, 4), dtype=int)),
                              dtype=int)
        self.device_view.set_mapping(mapping)
        # Override the title last (set_mapping triggers a redraw
        # whose text comes from the device's title field).
        self.device_view.set_title(
            name,
            description=("User-defined custom test device. Edit the "
                         "channel-map table below; pick the grid type "
                         "(square / hexagonal) from the chooser to the "
                         "right of the device dropdown."),
        )
        # Show the grid chooser since this is a custom device.
        self.grid_type_combo.setVisible(True)
        self.grid_type_combo.blockSignals(True)
        try:
            idx = self.grid_type_combo.findData(layout)
            if idx >= 0:
                self.grid_type_combo.setCurrentIndex(idx)
        finally:
            self.grid_type_combo.blockSignals(False)
        # Connector row visibility — custom devices aren't Linear so
        # the connector row stays visible.
        self._set_form_row_visible(self.connector_combo, True)

    def _on_grid_type_changed(self, _idx: int):
        """User changed the custom-device grid type — push the new
        layout to the device view and re-emit the array so downstream
        consumers (channel selector, combinations panel) pick up the
        change. Only fires when the chooser is interactive, which is
        only true while the *Other (custom grid)* device is selected.
        """
        layout = self.grid_type_combo.currentData() or "rect"
        self.device_view.set_layout(layout)
        self._emit_array()

    def _on_area_unit_changed(self, new_unit: str):
        # Preserve the PHYSICAL area when the user switches units.  E.g.
        # 5000 µm² → mm²  becomes 0.005 mm² (same area, different display),
        # NOT 5000 mm² (a 6-orders-of-magnitude larger area by accident).
        # The conversion uses the cached "old unit" we stashed last time
        # the combo settled; first call after construction has no cached
        # value, so we fall back to the current unit (no-op rescale).
        old_unit = getattr(self, "_area_last_unit", new_unit)
        old_factor = UNITS.get(old_unit, 1.0)
        new_factor = UNITS.get(new_unit, 1.0)

        natural_decimals = AREA_DECIMALS_BY_UNIT.get(
            new_unit, self.area_value.decimals())
        self.area_value.setSingleStep(
            AREA_STEPS_BY_UNIT.get(new_unit, self.area_value.singleStep()))
        self.area_value.setSuffix(f" {new_unit}")

        # Convert: value_um2 = old_value × old_factor; new_value = value_um2
        # / new_factor.  Block the signal so the rescale doesn't fire
        # ``_emit_array`` twice — we do that once below after the cache
        # update.
        if old_factor != new_factor:
            value_um2 = float(self.area_value.value()) * old_factor
            self.area_value.blockSignals(True)
            try:
                converted = value_um2 / new_factor
                # Widen decimal precision when the natural precision would
                # truncate the converted value to zero (e.g. 2000 µm² →
                # 0.002 mm² needs ≥3 decimals but mm² is natively 2).
                # Pick enough decimals to keep 3 significant figures while
                # never going below the unit's natural precision.
                if converted > 0:
                    import math
                    needed = max(0, 3 - int(math.floor(math.log10(converted))) - 1)
                    decimals_to_use = max(natural_decimals, needed)
                else:
                    decimals_to_use = natural_decimals
                self.area_value.setDecimals(decimals_to_use)
                # Widen the range when the converted value falls below the
                # spinbox minimum (e.g. 1 µm² → 1e-8 cm² is below the
                # default 0.001 lower bound).
                if converted < self.area_value.minimum():
                    self.area_value.setMinimum(min(self.area_value.minimum(),
                                                    converted / 10.0))
                self.area_value.setValue(
                    round(converted, max(decimals_to_use, 0)))
            finally:
                self.area_value.blockSignals(False)
        else:
            self.area_value.setDecimals(natural_decimals)

        self._area_last_unit = new_unit
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
        self.settingChanged.emit(
            "electrodes share coating = "
            + ("NO (per electrode)" if diff else "YES"))

    def _on_return_enable_changed(self, _checked: bool = True):
        # Toggling the return-electrode checkbox greys / un-greys the
        # paired coating dropdown so the user sees at a glance whether
        # the metadata is being captured. The actual array is
        # re-emitted so any downstream listener (session metadata,
        # log lines) sees the new state.
        self.return_coating.setEnabled(self.return_enable.isChecked())
        # Also reshuffle the potential limits — when the reference
        # electrode is off, the return electrode stands in as the
        # reference (per spec), so toggling the return on/off flips
        # the effective baseline. ``_apply_effective_reference_shift``
        # is a no-op when the reference toggle is on.
        self._apply_effective_reference_shift()
        self._emit_array()
        self.settingChanged.emit(
            "return electrode "
            + ("enabled" if self.return_enable.isChecked() else "disabled")
            + f" ({self.return_coating.currentText()})")

    def _on_return_coating_changed(self, *_):
        """Return-electrode coating dropdown changed.

        When the user picks the ``Custom`` trigger, pop the name
        dialog (loops on Try Again, reverts on Cancel). On Confirm
        the typed name becomes a new dropdown entry, selected. For
        catalog selections this just reshuffles the limits when the
        return acts as the reference (reference toggle off, return
        toggle on); for the normal "reference on" case it refreshes
        the return-OCP readout via the existing wiring.
        """
        # ``Custom`` is the trigger for the name dialog. Other
        # selections (catalog materials OR previously-added custom
        # entries) bypass the dialog and just apply directly.
        if self.return_coating.currentData() == REF_CUSTOM:
            self._prompt_for_custom("return",
                                     self.return_coating,
                                     "_prev_return_data",
                                     self._return_custom_entries,
                                     on_confirm=lambda _name: (
                                         self._apply_effective_reference_shift(),
                                         self._emit_array(),
                                         self.settingChanged.emit(
                                             "return electrode coating = "
                                             + self.return_coating.currentText()),
                                     ))
            return
        self._prev_return_data = self.return_coating.currentData()
        self._apply_effective_reference_shift()
        self._emit_array()
        self.settingChanged.emit(
            "return electrode coating = " + self.return_coating.currentText())

    def _apply_effective_reference_shift(self) -> None:
        """Compute the live effective-reference OCP, diff against the
        cached ``_current_ref_potential_v``, and shift the limit
        spinboxes by the difference. Idempotent — safe to call from
        any of the four handlers (reference toggle, reference combo,
        return toggle, return combo) since the diff is zero when
        nothing meaningful changed.
        """
        new_pot = self._effective_ref_potential_v()
        delta = new_pot - self._current_ref_potential_v
        self._shift_limits_by(delta)
        self._current_ref_potential_v = new_pot
        self._refresh_reference_potential_label()
        self._refresh_limits_original_label()

    def _refresh_return_potential_label(self, *_):
        """Update the right-of-dropdown readout that shows the return
        electrode's estimated open-circuit potential vs Ag|AgCl.

        Lookup precedence:

        1. **Custom** coating → user-edited spinbox value (no
           learning, no catalog).
        2. **Learned OCP** — once at least
           :data:`stimtest.electrode_potential_history.MIN_SAMPLES_FOR_LEARNED_OCP`
           E_ret pre/post-pulse rest values have been recorded for
           this coating's canonical bin (PtIr alloys collapse into a
           single ``PtIr`` bin), the running mean wins over the
           catalog. The label is annotated with "(learned, N
           samples)" so the user can tell at a glance whether they're
           seeing the table value or live data.
        3. **Catalog OCP** — :data:`COATING_OCP_VS_AG_AG_CL_V`
           (in-vitro values from the MATLAB
           ``getReferenceElectrode.m`` table).

        When the return-electrode toggle is OFF the label is blanked
        since the metadata is unused. When the selected coating has
        no catalog OCP and no learned data, the readout reads ``n/a``
        rather than rendering a misleading number.
        """
        short = self.return_coating.currentData()
        is_custom = (short == REF_CUSTOM)
        # Spinbox is visible only when the toggle is on AND ``Custom``
        # is the chosen coating; otherwise the catalog value applies.
        if hasattr(self, "return_custom_ocp_v"):
            self.return_custom_ocp_v.setVisible(
                bool(self.return_enable.isChecked()) and is_custom)
        if not self.return_enable.isChecked():
            self.return_potential_label.setText("")
            return
        if is_custom:
            ocp_v = float(self.return_custom_ocp_v.value())
            self.return_potential_label.setText(
                f"<b>{ocp_v:+.3f} V</b> <i>vs Ag|AgCl</i>"
            )
            return
        # The BOLD value is the CATALOG default — kept as the user's
        # baseline.  The learned mean is shown only as a RECOMMENDATION
        # (operator: "keep the default values I set originally"); it is
        # NOT bolded and never auto-applied.
        rec = self._recommendation_tag(short or "")
        ocp_v = COATING_OCP_VS_AG_AG_CL_V.get(short)
        if ocp_v is None:
            # No catalog value — show n/a, but still surface the learned
            # recommendation if one exists.
            self.return_potential_label.setText(
                f"<i>n/a vs Ag|AgCl</i>{rec}")
            return
        if rec:
            self.return_potential_label.setText(
                f"<b>{ocp_v:+.3f} V</b> <i>vs Ag|AgCl</i>{rec}")
            return
        # No learned recommendation yet — show catalog, plus a partial-
        # progress hint while a bin fills (only when recording is on).
        n = (self._learned_sample_count(short or "")
             if (hasattr(self, "remember_potential_chk")
                 and self.remember_potential_chk.isChecked()) else 0)
        if n > 0:
            from ..electrode_potential_history import (
                MIN_SAMPLES_FOR_LEARNED_OCP)
            self.return_potential_label.setText(
                f"<b>{ocp_v:+.3f} V</b> <i>vs Ag|AgCl</i> "
                f"<span style='color:#777;'>"
                f"(catalog; {n}/{MIN_SAMPLES_FOR_LEARNED_OCP} "
                f"samples toward learned)</span>"
            )
        else:
            self.return_potential_label.setText(
                f"<b>{ocp_v:+.3f} V</b> <i>vs Ag|AgCl</i>"
            )

    @staticmethod
    def _learned_sample_count(short: str) -> int:
        """Number of samples currently in the learning bin for
        ``short``. Wraps the storage module so the same lazy-import
        pattern as :meth:`_learned_ocp` applies. Returns 0 on any
        error so the caller can render "0 samples" without guarding.
        """
        if not short:
            return 0
        try:
            from ..electrode_potential_history import sample_count
            return int(sample_count(short))
        except Exception:
            return 0

    # ----------------------------------------------------------- reference electrode
    @staticmethod
    def _learned_ocp(short: str) -> Optional[float]:
        """Live OCP learned from past E_ret captures, or ``None``.

        Wraps :func:`stimtest.electrode_potential_history.learned_ocp_v`
        so the Setup tab can ask "do we have enough samples to
        replace the catalog value yet?" without committing the rest
        of the GUI to import the storage module. Returns the running
        mean once the per-coating bin has at least
        :data:`stimtest.electrode_potential_history.MIN_SAMPLES_FOR_LEARNED_OCP`
        entries; otherwise ``None`` so the caller falls back to the
        catalog. Import is lazy so a stripped-down test that doesn't
        touch the prefs directory pays nothing for the feature.

        The PtIr alloy collapse happens inside
        ``electrode_potential_history.canonical_key`` — passing
        ``"PtIr (90/10)"`` or ``"PtIr"`` here both consult the same
        bin, so once any one alloy hits the threshold every PtIr
        coating in the GUI starts using the learned value.
        """
        if not short:
            return None
        try:
            from ..electrode_potential_history import learned_ocp_v
        except Exception:
            return None
        try:
            return learned_ocp_v(short)
        except Exception:
            return None

    @staticmethod
    def _learned_ocp_std(short: str) -> Optional[float]:
        """Sample standard deviation of the learned OCP for ``short``, or
        ``None``.  Wraps
        :func:`stimtest.electrode_potential_history.learned_ocp_std_v`
        (same lazy-import + ≥ MIN_SAMPLES gate as :meth:`_learned_ocp`) so
        the "tested +X ± Y V" annotation can show the spread of the
        recorded rest potentials.  Returns ``None`` on any error / below
        threshold so the caller renders the mean without the ± term."""
        if not short:
            return None
        try:
            from ..electrode_potential_history import learned_ocp_std_v
        except Exception:
            return None
        try:
            return learned_ocp_std_v(short)
        except Exception:
            return None

    def _effective_ref_potential_v(self) -> float:
        """The reference-electrode OCP (V vs Ag|AgCl) currently in
        effect, in priority order:

        1. **Reference toggle ON** — use the dropdown's selection.
           The learned OCP (if 10+ E_ret samples have been recorded
           against that coating) wins over the catalog value;
           otherwise fall back to :data:`REFERENCE_ELECTRODES_OCP_V`.
        2. **Reference OFF, Return ON** — fall back to the return /
           counter electrode's coating OCP (per the user spec: "if
           there is no reference electrode, use the return electrode
           for reference"). Same learned-vs-catalog precedence as
           above, just consulting :data:`COATING_OCP_VS_AG_AG_CL_V`.
        3. **Both OFF** — 0 V, equivalent to "no shift applied to
           the catalog limits". Limits are shown raw vs Ag|AgCl.

        ``Custom`` reference / return short codes always go through
        the user-edited spinbox; the learning store is bypassed
        because there's no canonical bin to consult ("Custom" doesn't
        identify a real material).
        """
        # NOTE: the EFFECTIVE potential is the CATALOG / user-set default
        # — the learned OCP is deliberately NOT applied here (operator:
        # "Show the recommended potentials based on what has been
        # learned, but keep the default values that I had set originally
        # in the beginning").  The learned value is surfaced only as a
        # RECOMMENDATION in the readout label
        # (``_refresh_reference_potential_label``); it no longer shifts
        # the user's water-window limits.  ``Custom`` still reads the
        # user-typed spinbox.
        if self.reference_enable.isChecked():
            short = self.reference_combo.currentData() or REF_AG_AGCL
            if short == REF_CUSTOM:
                return float(self.reference_custom_ocp_v.value())
            return float(REFERENCE_ELECTRODES_OCP_V.get(short, 0.0))
        # Reference toggle is off — fall back to the return electrode.
        if (hasattr(self, "return_enable") and self.return_enable.isChecked()
                and hasattr(self, "return_coating")):
            short = self.return_coating.currentData() or ""
            if short == REF_CUSTOM:
                return float(self.return_custom_ocp_v.value())
            ocp = COATING_OCP_VS_AG_AG_CL_V.get(short)
            if ocp is not None:
                return float(ocp)
        return 0.0

    def _effective_ref_label(self) -> str:
        """Short human-readable tag for the currently-active reference.

        Used by :meth:`_refresh_reference_potential_label` to tell the
        user which baseline the limits are shown against. Returns
        ``"Ag|AgCl"`` when no shift is applied, the dropdown's short
        code when the reference toggle is on, ``"<short> (return)"``
        when falling back to the return electrode, etc.
        """
        if self.reference_enable.isChecked():
            return self.reference_combo.currentData() or REF_AG_AGCL
        if (hasattr(self, "return_enable") and self.return_enable.isChecked()
                and hasattr(self, "return_coating")):
            short = self.return_coating.currentData() or ""
            ocp = COATING_OCP_VS_AG_AG_CL_V.get(short)
            if ocp is not None:
                return f"{short} (return)"
        return REF_AG_AGCL

    def _shift_limits_by(self, delta_v: float) -> None:
        """Shift cathodic / anodic limit spinboxes by ``-delta_v``.

        ``new_value_vs_new_ref = old_value_vs_old_ref − delta`` where
        ``delta = new_ref_pot − old_ref_pot``. Block ``valueChanged``
        on the spinboxes so the user-edited flag does NOT get tagged
        — a reference change is a unit conversion, not a manual edit.
        """
        if abs(delta_v) < 1e-9:
            return
        for sp in (self.cathodic_limit_v, self.anodic_limit_v):
            sp.blockSignals(True)
        try:
            self.cathodic_limit_v.setValue(
                float(self.cathodic_limit_v.value()) - delta_v)
            self.anodic_limit_v.setValue(
                float(self.anodic_limit_v.value()) - delta_v)
        finally:
            for sp in (self.cathodic_limit_v, self.anodic_limit_v):
                sp.blockSignals(False)
        self._emit_limits()

    def _refresh_reference_potential_label(self) -> None:
        """Render the ``OCP vs Ag|AgCl`` output to the right of the
        reference dropdown.

        Shows three different forms depending on the effective
        reference (see :meth:`_effective_ref_potential_v`):

        * **Reference toggle ON** — "±X.XXX V vs Ag|AgCl
          (limits vs <short>)".
        * **Reference OFF, Return ON** — "±X.XXX V vs Ag|AgCl
          (limits vs <short> return)" so the user sees that
          the return electrode is standing in for the reference.
        * **Both OFF** — blanks the label since the limits are still
          on the catalog Ag|AgCl scale and there's nothing to report.
        """
        ref_on = self.reference_enable.isChecked()
        ret_on = (hasattr(self, "return_enable")
                  and self.return_enable.isChecked())
        # Show the custom-OCP spinbox only when the user has BOTH
        # enabled the reference AND picked ``Custom``. Otherwise the
        # catalog value (or 0 V for the disabled / n/a paths) drives
        # the limit-shift math and the spinbox stays hidden.
        ref_short = self.reference_combo.currentData() or REF_AG_AGCL
        if hasattr(self, "reference_custom_ocp_v"):
            self.reference_custom_ocp_v.setVisible(
                ref_on and ref_short == REF_CUSTOM)
        if ref_on:
            short = ref_short
            # The BOLD value is the CATALOG / user default (kept as-is —
            # the learned OCP no longer overrides it).  The learned mean
            # is appended only as a RECOMMENDATION via _recommendation_tag
            # (operator: "Show the recommended potentials … but keep the
            # default values I set originally").
            if short == REF_CUSTOM:
                pot_v = float(self.reference_custom_ocp_v.value())
                rec_tag = ""
            else:
                pot_v = REFERENCE_ELECTRODES_OCP_V.get(short, 0.0)
                rec_tag = self._recommendation_tag(short)
            if abs(pot_v) < 1e-9:
                self.reference_potential_label.setText(
                    f"<b>{pot_v:+.3f} V</b> <i>vs Ag|AgCl "
                    f"(no shift applied)</i>{rec_tag}"
                )
            else:
                self.reference_potential_label.setText(
                    f"<b>{pot_v:+.3f} V</b> <i>vs Ag|AgCl "
                    f"(limits vs {short})</i>{rec_tag}"
                )
            return
        if ret_on and hasattr(self, "return_coating"):
            short = self.return_coating.currentData() or ""
            if short == REF_CUSTOM:
                # Custom return material — read its user-typed OCP.
                ocp = float(self.return_custom_ocp_v.value())
                rec_tag = ""
            else:
                ocp = COATING_OCP_VS_AG_AG_CL_V.get(short)
                rec_tag = self._recommendation_tag(short)
            if ocp is not None:
                self.reference_potential_label.setText(
                    f"<b>{float(ocp):+.3f} V</b> <i>vs Ag|AgCl "
                    f"(limits vs {short} return)</i>{rec_tag}"
                )
                return
        # Both off, or return coating has no catalog OCP — leave the
        # label blank to match the historical behaviour.
        self.reference_potential_label.setText("")

    def _refresh_limits_original_label(self, *_) -> None:
        """Show the ORIGINAL (unshifted) water-window limits vs Ag|AgCl when a
        reference electrode has shifted them, so the operator sees the shift
        (operator: "Show the original potential limits so that the user can see
        how it is shifted, if it is shifted").

        The displayed spinbox limits are ``original − ref_pot`` (a reference at
        ``ref_pot`` V vs Ag|AgCl shifts the catalog Ag|AgCl limits DOWN by
        ``ref_pot``; see :meth:`_shift_limits_by`), so ``original = displayed +
        ref_pot``.  Blank when there is no shift (``ref_pot == 0`` — the
        displayed limits ARE the Ag|AgCl values)."""
        cl = getattr(self, "cathodic_original_label", None)
        al = getattr(self, "anodic_original_label", None)
        if cl is None or al is None:
            return

        def _blank():
            for _l in (cl, al):
                _l.setText("")
                _l.setVisible(False)

        ref = float(getattr(self, "_current_ref_potential_v", 0.0) or 0.0)
        if abs(ref) < 1e-9:
            _blank()                              # no reference shift
            return
        try:
            oc = float(self.cathodic_limit_v.value()) + ref
            oa = float(self.anodic_limit_v.value()) + ref
        except Exception:
            _blank()
            return
        # One line UNDER each limit: its own original (unshifted) value vs
        # Ag|AgCl (operator: "Remove 'shifted' underneath the limits").
        cl.setText(f"<i>original {oc:+.3f} V vs Ag|AgCl</i>")
        al.setText(f"<i>original {oa:+.3f} V vs Ag|AgCl</i>")
        cl.setVisible(True)
        al.setVisible(True)

    def _recommendation_tag(self, short: str) -> str:
        """HTML ``· tested X V`` tag for the learned/measured OCP of coating
        ``short`` — shown ONLY when the "Remember return-electrode potential"
        toggle is on and a learned value exists.  Empty string otherwise.
        Operator: label it "tested" (it's the OCP measured during testing) and
        DROP the "(learned, N samples)" parenthetical — it made the panel too
        wide.  Informational only: NEVER auto-applied to the limits (operator
        keeps their own defaults; see ``_effective_ref_potential_v``)."""
        if not (hasattr(self, "remember_potential_chk")
                and self.remember_potential_chk.isChecked()):
            return ""
        learned = self._learned_ocp(short)
        if learned is None:
            return ""
        # Append the sample spread as "± Y V" when we have it (operator:
        # "for the tested potential value, have standard deviation").  A
        # degenerate all-identical bin gives std 0.000; below the sample
        # threshold ``_learned_ocp_std`` returns None and we show just the
        # mean.
        std = self._learned_ocp_std(short)
        spread = (f" ± {float(std):.3f}"
                  if std is not None and float(std) == float(std) else "")
        return (f" &nbsp;·&nbsp; <span style='color:#0072B2;'>"
                f"tested {float(learned):+.3f}{spread} V</span>")

    def _on_reference_changed(self, *_):
        """Reference dropdown changed — shift the limit spinboxes by
        the difference between the OLD and NEW reference potentials,
        then refresh the OCP readout. The shift is a unit conversion
        (not a manual edit) so it doesn't set ``_limits_user_edited``.

        Picking the ``Custom`` trigger pops the name dialog (loops
        on Try Again, reverts on Cancel). On Confirm the typed name
        becomes a new dropdown entry, selected.
        """
        if self.reference_combo.currentData() == REF_CUSTOM:
            self._prompt_for_custom("reference",
                                     self.reference_combo,
                                     "_prev_reference_data",
                                     self._reference_custom_entries,
                                     on_confirm=lambda _name: (
                                         self._apply_effective_reference_shift(),
                                         self._emit_array(),
                                         self.settingChanged.emit(
                                             "reference electrode = "
                                             + self.reference_combo.currentText()),
                                     ))
            return
        self._prev_reference_data = self.reference_combo.currentData()
        self._apply_effective_reference_shift()
        self._emit_array()
        self.settingChanged.emit(
            "reference electrode = " + self.reference_combo.currentText())

    def _on_reference_enable_changed(self, _checked: bool = True):
        """Toggling the reference electrode on / off greys the
        dropdown and reverts (or re-applies) the reference shift on
        the limit spinboxes. When the reference is turned OFF and the
        return is ON, the return electrode takes over as the
        reference baseline — handled inside
        :meth:`_apply_effective_reference_shift` via
        :meth:`_effective_ref_potential_v`'s priority chain.
        """
        self.reference_combo.setEnabled(self.reference_enable.isChecked())
        self._apply_effective_reference_shift()
        self._emit_array()
        self.settingChanged.emit(
            "reference electrode "
            + ("enabled" if self.reference_enable.isChecked() else "disabled")
            + f" ({self.reference_combo.currentText()})")

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

    def _on_geometry_changed(self, *_):
        """Show / hide the Rounded checkbox based on the selected
        geometry, then re-emit the array so downstream listeners
        see the new geometry on every change.

        ``Rounded`` is meaningful only for square / rectangle
        geometries (filleted corners / pill cap). For circle, cone,
        ring, and band the toggle is hidden — its state stays
        whatever it was last set to so toggling away from and back
        to a square/rectangle preserves the user's choice."""
        code = self.geometry_combo.currentData() or ""
        rounded_visible = code in self._SQUARE_OR_RECT_GEOMETRIES
        self.geometry_rounded.setVisible(rounded_visible)
        self._emit_array()
        self.settingChanged.emit(
            f"electrode geometry = {self.geometry_combo.currentText()}"
            + (" (rounded)"
               if rounded_visible and self.geometry_rounded.isChecked() else ""))

    def _on_coating_changed(self, label: str):
        # ``Custom…`` is a *trigger*, not a real coating: when the
        # user picks it, pop the name dialog. On Confirm we add a
        # new entry below ``Custom…`` and switch the dropdown to it
        # so the typed name becomes the live selection. On Cancel we
        # revert to whatever was selected previously (cached in
        # :attr:`_prev_coating_data`). The legacy ``coating_custom``
        # QLineEdit is hidden — superseded by the dialog flow — but
        # kept around so the saved-prefs round-trip still works.
        if label == CUSTOM_COATING_LABEL:
            self.coating_custom.setVisible(False)
            self._prompt_for_custom("active",
                                     self.coating_combo,
                                     "_prev_coating_data",
                                     self._coating_custom_entries)
            return
        else:
            self.coating_custom.setVisible(False)
            self._prev_coating_data = self.coating_combo.currentData()
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
                # Catalog limits are stored in ``V vs Ag|AgCl``. If a
                # non-Ag|AgCl reference is active, shift the catalog
                # values by ``-ref_potential`` so the displayed limits
                # remain in the same ``V vs <ref>`` scale the user sees
                # in the rest of the form.
                ref_shift = self._current_ref_potential_v
                # Block our own edited-flag setter so the catalog
                # refresh doesn't get tagged as a user edit.
                for sp in (self.cathodic_limit_v, self.anodic_limit_v):
                    sp.blockSignals(True)
                try:
                    self.cathodic_limit_v.setValue(
                        float(coat.cathodic_limit_v) - ref_shift)
                    self.anodic_limit_v.setValue(
                        float(coat.anodic_limit_v) - ref_shift)
                finally:
                    for sp in (self.cathodic_limit_v, self.anodic_limit_v):
                        sp.blockSignals(False)
                self._emit_limits()
        self._emit_array()

    def _prompt_for_custom(self, kind_label: str,
                            combo: QtWidgets.QComboBox,
                            prev_attr: str,
                            tracker: list,
                            on_confirm=None) -> None:
        """Open the custom-name pop-up for ``combo`` and apply the
        result.

        ``kind_label`` is the human-readable target ("active",
        "return", "reference"). ``prev_attr`` is the instance attr
        that caches the previously-selected userData so a Cancel
        reverts cleanly. ``tracker`` is a list of user-added custom
        names so they can persist in prefs. ``on_confirm`` (optional)
        fires with the new userData after the dropdown updates — used
        by the return / reference paths to also refresh the OCP
        readout / re-shift limits.

        Loops on **Try Again**: the dialog re-opens with whatever the
        user typed last so they don't have to retype from scratch.
        ``setCurrentIndex`` calls block signals so revert / select
        round-trips don't recursively re-fire ``currentTextChanged``
        and re-open the dialog.
        """
        prev_data = getattr(self, prev_attr, None)
        prefill = ""
        while True:
            dlg = _CustomElectrodeNameDialog(kind_label, prefill=prefill,
                                             parent=self)
            dlg.exec()
            code = dlg.result_code
            if code == _CustomElectrodeNameDialog.RESULT_CONFIRM:
                name = dlg.name
                # Skip the add when the user typed an existing name —
                # just select that entry instead of duplicating.
                idx = combo.findData(name)
                if idx < 0:
                    combo.blockSignals(True)
                    try:
                        combo.addItem(name, userData=name)
                    finally:
                        combo.blockSignals(False)
                    idx = combo.findData(name)
                    if name not in tracker:
                        tracker.append(name)
                combo.blockSignals(True)
                try:
                    combo.setCurrentIndex(idx)
                finally:
                    combo.blockSignals(False)
                setattr(self, prev_attr, name)
                if on_confirm is not None:
                    on_confirm(name)
                return
            if code == _CustomElectrodeNameDialog.RESULT_TRY_AGAIN:
                # Re-open the dialog with whatever the user just
                # typed so they can edit (not retype from scratch).
                prefill = dlg.name
                continue
            # Cancel — revert the dropdown to whatever was selected
            # before the user picked Custom… . Block signals so the
            # revert doesn't re-trigger the changed-handler that
            # opened the dialog.
            if prev_data is not None:
                idx = combo.findData(prev_data)
                if idx >= 0:
                    combo.blockSignals(True)
                    try:
                        combo.setCurrentIndex(idx)
                    finally:
                        combo.blockSignals(False)
            return

    def _on_limits_user_edited(self, *_):
        """Fired when the user edits the limit / tolerance spinboxes.

        Sets the ``_limits_user_edited`` flag so a subsequent coating
        change leaves the user's hand-typed values alone. Also emits
        ``potentialLimitsChanged`` so the experiment tabs cache the
        new values for the next run.
        """
        self._limits_user_edited = True
        # A hand-edit of the displayed (shifted) limits changes the derived
        # original vs-Ag|AgCl values too — keep that annotation current.
        self._refresh_limits_original_label()
        self._emit_limits()

    def _on_experiment_changed(self, idx: int):
        code = self.experiment_combo.itemData(idx)
        if code in EXPERIMENTS:
            self.experiment_blurb.setText(EXPERIMENTS[code].blurb)
        # Dropdown selection is now the authoritative trigger for
        # swapping the experiment tab's contents — the user no longer
        # has to click a button to commit. The button (below) handles
        # navigation only. ``experimentRequested`` is wired in
        # ``MainWindow._on_experiment_requested`` → ``_swap_experiment``
        # which does the swap without changing the current tab, so a
        # dropdown change made while the user is still on the Setup
        # tab leaves them on Setup.
        if code:
            self.experimentRequested.emit(code)

    def _on_open_experiment(self):
        # Pure navigation. The dropdown's ``_on_experiment_changed``
        # already emitted ``experimentRequested`` if the user picked
        # a different option, so the Test parameters tab is already
        # showing the right content by the time this button fires.
        self.testParamsRequested.emit()

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

        # Bare test board (no electrodes): FORCE area to 0 and a neutral
        # coating label regardless of the (hidden) coating/area widgets, so
        # area-normalised metrics (current density, Q_inj density) are
        # disabled and the plot shows raw current (operator: "because the
        # test board has no electrodes, disable area for waveform metrics").
        _dev = DEVICES.get(device_name)
        if _dev is not None and not getattr(_dev, "has_electrodes", True):
            area_um2 = 0.0
            coating = "Test board"

        # Resolve geometry. The combo's userData is the short
        # geometry code (``circle`` / ``square`` / ``rectangle`` /
        # ``cone`` / ``ring`` / ``band``). Rounded is only
        # meaningful for square / rectangle but we still read it as
        # a bool — ``ElectrodePosition`` ignores the flag in other
        # geometry contexts.
        from ..config import ELECTRODE_GEOMETRY_CIRCLE
        geometry = (self.geometry_combo.currentData()
                    or ELECTRODE_GEOMETRY_CIRCLE)
        rounded = bool(self.geometry_rounded.isChecked())

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
            geometry=geometry,
            rounded=rounded,
            per_channel=per_channel,
            layout=self.device_view.current_layout(),
        )

    def current_aliases(self) -> dict:
        """Build the {logical_name: 'CHx'} dict the runner uses.

        Logic:
        * Each role assigned to a channel maps that role to the channel.
        * If the user picked a channel as ``Trigger``, use it.
        * If no channel is set as ``Trigger``, fall back to whichever
          channel carries the current monitor (I_mon).
        * Channels set to ``None`` are simply not represented.
        * Channels the connected scope DOESN'T HAVE (beyond
          ``self._n_visible_scope_channels``, e.g. CH3/CH4 on a 2-channel
          scope) are skipped — their stored role is preserved for
          persistence (see :meth:`_set_visible_scope_channels`) but must
          never map onto a channel that doesn't physically exist.
        """
        out: dict = {}
        explicit_trigger = None
        imon_ch = None
        n_vis = getattr(self, "_n_visible_scope_channels", 4)
        order = ("CH1", "CH2", "CH3", "CH4")
        for ch, combo in self._role_combos.items():
            if ch in order and order.index(ch) >= n_vis:
                continue  # channel not present on the connected scope
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

    # ------------------------------------------------------------- export
    def setup_snapshot(self) -> dict:
        """Curated dict of setup-tab values for the XLSX export.

        Different from :meth:`current_prefs` in two ways:

        1. **Filters out session-runtime / persistence-only fields**
           (``save_path``, ``auto_export_xlsx``, ``email_notifications``,
           the auto-save-plots toggles) — those describe what to do with
           the captured data, not what was tested.
        2. **Adds derived / display-friendly values** the exporter
           needs but ``current_prefs`` doesn't carry — the human-
           readable coating name, the resolved water-window limits, the
           per-channel overrides table, and the chosen channel-role
           mapping inverted to a ``role → channel`` dict.

        The runner stamps the result into ``session.test.extras
        ['setup_snapshot']`` on start, and
        :func:`stimtest.gamry_export._write_setup_sheet` writes a new
        "Setup" sheet from it.
        """
        # Resolve the human-friendly coating label (the dropdown stores
        # the canonical short name as userData; the visible text is the
        # spelled-out form like "AIROF (Activated Iridium Oxide Film)").
        coating_short = self.coating_combo.currentData() or ""
        coating_label = self.coating_combo.currentText()
        if coating_short == CUSTOM_COATING_LABEL:
            coating_short = self.coating_custom.text().strip() or "Custom"
            coating_label = coating_short
        # Resolve the return / reference electrode labels too.
        return_coating_short = self.return_coating.currentData() or ""
        return_coating_label = self.return_coating.currentText()
        ref_short = self.reference_combo.currentData() or ""
        ref_label = self.reference_combo.currentText()

        # Channel-role mapping inverted to ``role → channel``. The
        # ``current_prefs`` form is per-channel (``CH1: V_mon`` etc.)
        # which mirrors the GUI rows; for export we additionally write
        # the role-keyed view because that's the natural lookup
        # downstream (e.g. "which channel was V_mon?").
        role_to_channel: dict = {}
        for ch, combo in self._role_combos.items():
            role = combo.currentText()
            # Skip the "(none)" placeholder so the export only lists
            # roles the user actually assigned.
            if role and role != ROLE_NONE:
                role_to_channel.setdefault(role, []).append(ch)
        # Collapse single-element lists to the bare value for cleaner
        # rendering; multi-channel roles (rare) keep the list form.
        role_to_channel = {k: (v[0] if len(v) == 1 else v)
                           for k, v in role_to_channel.items()}

        cathodic_v, anodic_v, tol_v = self.current_potential_limits()

        snap = {
            "device": self.device_combo.currentText(),
            # Whether the selected device has real electrodes.  False only for
            # the Plexon Test Board (no reference / return electrode), which
            # makes the plot voltage axis stay plain "Voltage [V]" instead of
            # the reference-aware "Potential vs <ref>" / "Voltage vs <return>"
            # relabel (operator: "If the Test Board is connected, the unit for
            # the voltage channels can only be Voltage [V]").
            "has_electrodes": bool(getattr(
                DEVICES.get(self.device_combo.currentText()),
                "has_electrodes", True)),
            "grid_type": self.grid_type_combo.currentData() or "rect",
            "connector": self.connector_combo.currentText(),
            "custom_cable_map": {str(k): int(v)
                                 for k, v in self._custom_cable_map.items()},
            # Active device→Plexon cable translation (non-identity entries
            # only; empty ⇒ identity).  The runner installs this via
            # ``set_channel_map`` so pulsing a device channel commands the
            # mapped Plexon stim channel.  str keys for JSON round-trip.
            "channel_map": {str(k): int(v)
                            for k, v in self.current_channel_map().items()},
            "acq_mode": self.acq_mode_combo.currentText(),
            "acq_n_avg": self._current_n_avg(),
            "horiz_scaling": self.current_horizontal_scaling(),
            "ext_trigger": self.ext_trigger_check.isChecked(),
            # ``trig_slope`` is intentionally not saved — the
            # operator-facing edge selector was removed in favour of
            # auto-resolving the slope from the trigger-source rules
            # at run time (digital → RISE, I_mon → phase-1 polarity).
            "scope_has_ext": self._scope_has_ext,
            # Surface area
            "surface_area_mode": "Same for all electrodes" if self.area_mode.isChecked()
                                  else "Different per electrode",
            "surface_area_value": float(self.area_value.value()),
            "surface_area_unit": self.area_unit.currentText(),
            # Active-electrode coating
            "coating_mode": "Same for all electrodes" if self.coating_mode.isChecked()
                             else "Different per electrode",
            "coating_short": coating_short,
            "coating_label": coating_label,
            # Return / counter electrode
            "return_enable": bool(self.return_enable.isChecked()),
            "return_coating_short": return_coating_short,
            "return_coating_label": return_coating_label,
            # Reference electrode
            "reference_enable": bool(self.reference_enable.isChecked()),
            "reference_electrode_short": ref_short,
            "reference_electrode_label": ref_label,
            # Whether finished captures record E_ret into the learned-OCP
            # store (electrode_potential_history.record_capture gates on
            # this).  Default True for snapshots predating the toggle.
            "remember_return_potential": bool(
                self.remember_potential_chk.isChecked()),
            # Water window (live values, post any reference shift)
            "cathodic_limit_v": cathodic_v,
            "anodic_limit_v": anodic_v,
            "polarization_tolerance_v": tol_v,
            # Per-channel role assignment — both views are useful in
            # the export. Per-channel keeps insertion order (CH1, CH2,
            # ...); role-keyed is the inverted lookup.
            "channel_roles_per_channel": {
                ch: cb.currentText() for ch, cb in self._role_combos.items()
            },
            "channel_roles_by_role": role_to_channel,
            # Per-channel scope bandwidth / coupling overrides (Setup tab
            # dropdowns in line with the role).  Pushed to the scope at run
            # start by the experiment tab; "Auto" entries mean "use the
            # automatic policy".
            "channel_bandwidths": self.current_channel_bandwidths(),
            "channel_couplings": self.current_channel_couplings(),
            # Channel-mapping table — a 2-D list of ints (0 = empty).
            "channel_mapping": self.device_view.current_mapping().tolist(),
            # Per-channel area / coating overrides (only populated when
            # the respective "Different per electrode" mode is on).
            "per_channel_overrides": self.device_view.per_channel_overrides(),
            # Environment metadata. ``environment_short`` is the
            # canonical preset short_code (stable across releases);
            # ``environment_custom`` is the user's free-form text
            # for the ``custom`` preset (empty otherwise);
            # ``environment_display`` is the rendered label for
            # human readers (display name, or "Custom: <text>").
            # Used by ``stimtest.damage_warnings`` to drive the
            # warning posture on every Start / capture, and by the
            # contribute-data dialog for anonymized upload
            # categorisation.
            "environment_short": self.current_environment_short(),
            "environment_custom": self.current_environment_custom_text(),
            "environment_display": self.current_environment_display(),
            # Gas-sparging metadata. ``sparge_gas`` is the stable
            # short_code; ``sparge_gas_display`` is the rendered
            # label for human readers / exports. Stamped here so
            # the saved-session XLSX export, the
            # electrode-potential learning store, and the
            # contribute-data payload all carry the value as
            # provenance for cohort analysis.
            "sparge_gas": self.current_sparge_gas(),
            "sparge_gas_display": self.current_sparge_gas_display(),
        }
        return snap

    # ------------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        """Snapshot every user-editable control on this tab."""
        return {
            "notebook": self.notebook.text(),
            "notebook_enabled": self.notebook_check.isChecked(),
            "subject": self.subject.text(),
            "user_name": self.user_name.text(),
            "user_email": self.user_email.text(),
            "user_phone": self.user_phone.text(),
            "user_carrier": self.current_user_carrier(),
            "user_institution": self.user_institution.text(),
            "environment_short": self.current_environment_short(),
            "environment_custom": self.current_environment_custom_text(),
            "sparge_gas": self.current_sparge_gas(),
            "save_path": self.save_path.text(),
            "auto_export_xlsx": self.auto_export_xlsx.isChecked(),
            "email_notifications": self.email_notifications.isChecked(),
            "auto_save_plots": self.auto_save_plots.isChecked(),
            "auto_save_plots_fmt": self.current_auto_save_plots_format(),
            "auto_save_plots_dpi": self.current_auto_save_plots_dpi(),
            "acq_mode": self.acq_mode_combo.currentText(),
            "acq_n_avg": self._current_n_avg(),
            "horiz_scaling": self.current_horizontal_scaling(),
            "ext_trigger": self.ext_trigger_check.isChecked(),
            # ``trig_slope`` removed — see :meth:`current_prefs` for why.
            "scope_has_ext": self._scope_has_ext,
            "device": self.device_combo.currentText(),
            # Grid type for the *Other (custom grid)* device. Stored as
            # the canonical "rect" / "triangular" tag; ignored on
            # restore for built-in devices (those carry their own
            # ``DeviceDef.layout``).
            "grid_type": self.grid_type_combo.currentData() or "rect",
            "connector": self.connector_combo.currentText(),
            "custom_cable_map": {str(k): int(v)
                                 for k, v in self._custom_cable_map.items()},
            "area_mode": self.area_mode.isChecked(),
            "area_unit": self.area_unit.currentText(),
            "area_value": self.area_value.value(),
            "coating_mode": self.coating_mode.isChecked(),
            "coating": self.coating_combo.currentData(),
            "coating_custom": self.coating_custom.text(),
            # Electrode geometry. Pre-geometry prefs files miss
            # these keys; ``restore_prefs`` falls back to circle /
            # not-rounded (the dataclass defaults), matching the
            # implicit interpretation those older files would have
            # had anyway.
            "geometry": self.geometry_combo.currentData(),
            "geometry_rounded": bool(self.geometry_rounded.isChecked()),
            "return_enable": self.return_enable.isChecked(),
            "return_coating": self.return_coating.currentData(),
            # Custom-OCP values for the two electrodes — only relevant
            # when the matching dropdown is on ``Custom``, but
            # persisted unconditionally so the user's typed value
            # survives a coating swap-back.
            "return_custom_ocp_v": float(self.return_custom_ocp_v.value()),
            "reference_enable": self.reference_enable.isChecked(),
            "reference_electrode": self.reference_combo.currentData(),
            "reference_custom_ocp_v": float(self.reference_custom_ocp_v.value()),
            "remember_return_potential": bool(
                self.remember_potential_chk.isChecked()),
            # User-added custom-electrode names per combo, in
            # insertion order. Restored at launch so a previously-
            # added custom entry shows up in the dropdown again
            # without re-typing.
            "coating_custom_entries":   list(self._coating_custom_entries),
            "return_custom_entries":    list(self._return_custom_entries),
            "reference_custom_entries": list(self._reference_custom_entries),
            # User-named custom devices — names + per-device layout
            # and mapping. Saved alongside the simple electrode
            # trackers above so a saved profile carries the user's
            # full device library.
            "device_custom_entries": list(self._device_custom_entries),
            "device_custom_state": {
                # Snapshot the live state for the currently-selected
                # custom device alongside any previously-cached ones,
                # so the in-memory state for the active selection is
                # what actually gets saved (not a stale copy).
                **self._device_custom_state,
                **({self._prev_device_name: {
                        "layout": self.device_view.current_layout(),
                        "mapping": self.device_view.current_mapping().tolist(),
                    }}
                   if self._prev_device_name in self._device_custom_entries
                   else {}),
            },
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
            # Per-channel bandwidth / coupling override dropdowns (in line with
            # the role dropdown).  Stored for all four channels regardless of
            # visibility so a 2-ch scope session doesn't drop a hidden choice.
            "channel_bandwidths": {ch: cb.currentText()
                                   for ch, cb in self._bw_combos.items()},
            "channel_couplings": {ch: cb.currentText()
                                  for ch, cb in self._coupling_combos.items()},
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
        # Restore user-named custom-device entries to the dropdown
        # BEFORE the device selection is restored — a saved selection
        # may point at a custom device, and ``setCurrentText`` would
        # silently fall through if the entry doesn't exist yet.
        device_custom_entries = p.get("device_custom_entries")
        if isinstance(device_custom_entries, (list, tuple)):
            for name in device_custom_entries:
                name = str(name).strip()
                if not name:
                    continue
                if self.device_combo.findText(name) >= 0:
                    continue
                self.device_combo.blockSignals(True)
                try:
                    self.device_combo.addItem(name)
                finally:
                    self.device_combo.blockSignals(False)
                if name not in self._device_custom_entries:
                    self._device_custom_entries.append(name)
        # Per-device saved state (layout + mapping) — populated even
        # for entries that aren't currently selected so a switch to
        # them later picks up the right state.
        device_custom_state = p.get("device_custom_state")
        if isinstance(device_custom_state, dict):
            for name, state in device_custom_state.items():
                if isinstance(state, dict):
                    self._device_custom_state[name] = dict(state)
        if "device" in p:
            self.device_combo.blockSignals(True)
            self.device_combo.setCurrentText(p["device"])
            self.device_combo.blockSignals(False)
            # Manually populate the device view since we suppressed the signal
            from ..config import DEVICES
            if p["device"] in DEVICES:
                dev = DEVICES[p["device"]]
                self.device_view.set_device(dev)
                # The device-change SIGNAL was suppressed, so re-apply the
                # STRUCTURAL device-dependent UI constraints by hand — the
                # cable whitelist + electrode-row visibility + E_act/E_ret
                # scope-role filtering.  Without this a RESTORED test board
                # kept all 6 scope roles (+ the full cable list) at startup
                # (operator: "When the Test Board is selected only Vmon,
                # Imon, and Trigger are the only available oscilloscope
                # channels").  Runs BEFORE the connector / role restores
                # below so their setCurrentText picks from the constrained
                # lists (a saved E_act on a test board simply no-ops).
                self.connector_combo.blockSignals(True)
                try:
                    self._apply_device_ui_constraints(dev)
                finally:
                    self.connector_combo.blockSignals(False)
            elif p["device"] in self._device_custom_entries:
                # User-named custom device — apply its saved state.
                self._apply_custom_device(p["device"])
            self._prev_device_name = p["device"]
        # Grid type override — only applied for the *Other (custom grid)*
        # device since built-ins carry their own layout in DeviceDef.
        # Done after the device is set so set_device's "rect" default
        # doesn't clobber the saved choice.
        if (p.get("device") == "Other (custom grid)"
                and p.get("grid_type") in ("rect", "triangular")):
            saved_layout = p["grid_type"]
            self.device_view.set_layout(saved_layout)
            self.grid_type_combo.blockSignals(True)
            try:
                idx = self.grid_type_combo.findData(saved_layout)
                if idx >= 0:
                    self.grid_type_combo.setCurrentIndex(idx)
            finally:
                self.grid_type_combo.blockSignals(False)
        # Sync the chooser's visibility with the now-selected device
        # (restoring "Linear" or another built-in must hide it).
        self.grid_type_combo.setVisible(
            self.device_combo.currentText() == "Other (custom grid)")
        # Re-add user-added custom-electrode entries to each combo
        # BEFORE restoring selections — a saved selection may point
        # at a custom name, and ``findData`` would fail if the entry
        # doesn't exist yet.
        for key, combo, tracker in (
            ("coating_custom_entries",   self.coating_combo,
             self._coating_custom_entries),
            ("return_custom_entries",    self.return_coating,
             self._return_custom_entries),
            ("reference_custom_entries", self.reference_combo,
             self._reference_custom_entries),
        ):
            saved = p.get(key)
            if not isinstance(saved, (list, tuple)):
                continue
            for name in saved:
                name = str(name).strip()
                if not name:
                    continue
                if combo.findData(name) >= 0:
                    continue
                combo.blockSignals(True)
                try:
                    combo.addItem(name, userData=name)
                finally:
                    combo.blockSignals(False)
                if name not in tracker:
                    tracker.append(name)
        # Restore the custom pinout BEFORE selecting the connector so that
        # picking "Custom" repopulates the tree from the saved map (validated:
        # pins + channels must be 1–16 ints; anything malformed is dropped).
        raw_map = p.get("custom_cable_map")
        if isinstance(raw_map, dict):
            parsed: Dict[int, int] = {}
            for k, v in raw_map.items():
                try:
                    pk, cv = int(k), int(v)
                except (TypeError, ValueError):
                    continue
                if 1 <= pk <= 16 and 1 <= cv <= 16:
                    parsed[pk] = cv
            self._custom_cable_map = parsed
        if "connector" in p:
            self.connector_combo.setCurrentText(p["connector"])
            self._refresh_cable_map_tree()   # in case the value was unchanged
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
            # Reuse the spinbox-restore helper from pattern_panel —
            # same try/except shape, same intent.  Imported lazily
            # so this module doesn't pull pattern_panel at import
            # time.
            from .pattern_panel import _safe_set_spinbox_value
            _safe_set_spinbox_value(self.area_value, p["area_value"])
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
        # Electrode geometry. ``geometry`` key is a short code matching
        # one of ``ELECTRODE_GEOMETRIES``; older prefs files without
        # the key keep the panel default (circle, not-rounded).
        if "geometry" in p:
            idx = self.geometry_combo.findData(p["geometry"])
            if idx >= 0:
                self.geometry_combo.setCurrentIndex(idx)
        if "geometry_rounded" in p:
            try:
                self.geometry_rounded.setChecked(bool(p["geometry_rounded"]))
            except (TypeError, ValueError):
                pass
        # Refresh the Rounded-toggle visibility for the restored
        # geometry — done unconditionally so legacy prefs (which
        # don't carry geometry keys) still hide the toggle when the
        # default circle geometry applies.
        try:
            code = self.geometry_combo.currentData() or ""
            self.geometry_rounded.setVisible(
                code in self._SQUARE_OR_RECT_GEOMETRIES)
        except Exception:
            pass
        # Return / counter-electrode toggle + coating selector. Old prefs
        # files lack these keys; default to OFF + Pt in that case (set
        # in __init__) so a legacy round-trip is benign.
        if "return_enable" in p:
            try: self.return_enable.setChecked(bool(p["return_enable"]))
            except Exception: pass
        if "return_coating" in p:
            idx = self.return_coating.findData(p["return_coating"])
            if idx >= 0:
                self.return_coating.setCurrentIndex(idx)
        if "return_custom_ocp_v" in p:
            try:
                self.return_custom_ocp_v.setValue(
                    float(p["return_custom_ocp_v"]))
            except (TypeError, ValueError):
                pass
        # Sync the dropdown's enabled state with the toggle (the
        # toggled signal fires only on change, not on programmatic
        # setChecked of the same value, so call the handler explicitly).
        self.return_coating.setEnabled(self.return_enable.isChecked())
        # Reference electrode toggle + selector. Old prefs files lack
        # these keys; default to ON + Ag|AgCl (the catalog convention)
        # in that case so a legacy round-trip is benign.
        if "reference_enable" in p:
            try: self.reference_enable.setChecked(bool(p["reference_enable"]))
            except Exception: pass
        if "remember_return_potential" in p:
            try:
                self.remember_potential_chk.setChecked(
                    bool(p["remember_return_potential"]))
            except Exception:
                pass
        if "reference_electrode" in p:
            idx = self.reference_combo.findData(p["reference_electrode"])
            if idx >= 0:
                self.reference_combo.setCurrentIndex(idx)
        if "reference_custom_ocp_v" in p:
            try:
                self.reference_custom_ocp_v.setValue(
                    float(p["reference_custom_ocp_v"]))
            except (TypeError, ValueError):
                pass
        # Same idempotency caveat as the return-coating block: re-sync
        # the dropdown's enabled state. The actual limit shift was
        # already applied by the toggle handlers
        # (``_on_reference_enable_changed`` / ``_on_return_enable_changed``)
        # which call ``_apply_effective_reference_shift`` — but pre-
        # fallback prefs (where ``return_enable`` came in as ``False``
        # and the reference state didn't actually change) might leave
        # ``_current_ref_potential_v`` stale, so do one final
        # idempotent reconciliation pass here to cover that path.
        self.reference_combo.setEnabled(self.reference_enable.isChecked())
        self._apply_effective_reference_shift()
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
        if "user_phone" in p: self.user_phone.setText(str(p["user_phone"]))
        if "user_carrier" in p:
            _ci = self.user_carrier.findData(str(p["user_carrier"]))
            if _ci >= 0:
                self.user_carrier.setCurrentIndex(_ci)
        if "user_institution" in p:
            self.user_institution.setText(p["user_institution"])
        # Restore the environment combo + custom text. Block
        # signals while we set the combo so the per-change
        # ``_on_environment_changed`` handler doesn't fire mid-
        # restore (we emit once explicitly at the end).
        if "environment_short" in p:
            short = str(p["environment_short"] or "")
            idx = self.environment_combo.findData(short)
            if idx >= 0:
                self.environment_combo.blockSignals(True)
                try:
                    self.environment_combo.setCurrentIndex(idx)
                finally:
                    self.environment_combo.blockSignals(False)
                self.environment_custom.setVisible(short == "custom")
        if "environment_custom" in p:
            text = str(p["environment_custom"] or "")
            self.environment_custom.blockSignals(True)
            try:
                self.environment_custom.setText(text)
            finally:
                self.environment_custom.blockSignals(False)
        # Single emit so subscribers see one consistent event
        # rather than a flurry of intermediate states.
        self._emit_environment()
        # Restore the sparge-gas selection. Same block-signals /
        # single-emit pattern as the environment restore above
        # so the subscribed GUI state lands in one consistent
        # event rather than mid-restore intermediate values.
        if "sparge_gas" in p:
            short = str(p["sparge_gas"] or "none").strip()
            sg_idx = self.sparge_gas_combo.findData(short)
            if sg_idx >= 0:
                self.sparge_gas_combo.blockSignals(True)
                try:
                    self.sparge_gas_combo.setCurrentIndex(sg_idx)
                finally:
                    self.sparge_gas_combo.blockSignals(False)
        self.spargeGasChanged.emit(self.current_sparge_gas())
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
        if "auto_save_plots_dpi" in p:
            try:
                idx = self.auto_save_plots_dpi.findData(
                    int(p["auto_save_plots_dpi"]))
                if idx >= 0:
                    self.auto_save_plots_dpi.setCurrentIndex(idx)
            except Exception:
                pass
        if "auto_save_plots" in p:
            try:
                self.auto_save_plots.setChecked(bool(p["auto_save_plots"]))
                # Combo enable-state is wired to the checkbox toggle
                # signal — manually setting ``setChecked`` to the same
                # value as the default doesn't fire ``toggled``, so
                # mirror it here for the initial state (format combo +
                # the SVG-aware DPI gate).
                self.auto_save_plots_fmt.setEnabled(
                    self.auto_save_plots.isChecked())
                self._update_dpi_enabled()
                QtCore.QTimer.singleShot(0, self._emit_auto_save_plots)
            except Exception:
                pass
        if "acq_mode" in p:
            try: self.acq_mode_combo.setCurrentText(str(p["acq_mode"]))
            except Exception: pass
        if "horiz_scaling" in p:
            try:
                self.horiz_scaling_combo.setCurrentText(
                    "Tight" if str(p["horiz_scaling"]).lower().startswith("tight")
                    else "Wide")
            except Exception:
                pass
        # (Legacy prefs may carry "eret_coupling" from the removed global
        # electrode-coupling dropdown — it's silently ignored now; the
        # per-channel Coupling column carries the "DC + AC" choice instead.)
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
        # NOTE: ``scope_has_ext`` from prefs is *intentionally not*
        # restored here.  Doing so would show the EXT toggle before
        # any scope had a chance to confirm its model — e.g. an
        # operator who used a TBS2204B (has EXT) yesterday and a
        # TBS1052C (no EXT) today would briefly see the toggle at
        # app startup until they connected the new scope.
        # ``apply_scope_capabilities`` is the *only* place that flips
        # the toggle's visibility, and it runs on every scope-connect
        # signal, so we don't need (and shouldn't have) a pre-connect
        # cached value here.
        #
        # ``ext_trigger`` (the checked-state pref) is still restored
        # below — but only when the toggle is currently visible
        # (i.e. a live scope has confirmed EXT support), so stale
        # prefs from a different scope can't re-route the trigger
        # to a non-existent BNC.
        if "ext_trigger" in p:
            try:
                if self.ext_trigger_check.isVisible():
                    self.ext_trigger_check.setChecked(bool(p["ext_trigger"]))
            except Exception:
                pass
        # ``trig_slope`` from old prefs files is silently ignored — the
        # operator-facing edge selector was removed and the slope is
        # now auto-resolved at run time from the trigger-source rules.
        # Scope mapping
        # New per-channel role format
        if isinstance(p.get("channel_roles"), dict):
            for ch, role in p["channel_roles"].items():
                if ch in self._role_combos and role in SCOPE_ROLES:
                    self._role_combos[ch].setCurrentText(role)
            # Restored a real mapping → mark configured so a scope connect
            # won't re-apply the catalog default over it (e.g. a deliberate
            # None on CH3).  Gated on "at least one non-None role" so a
            # degenerate all-None prefs blob still lets the first-connect
            # default kick in.  (The setCurrentText calls above usually
            # flip the flag via _on_role_changed too; this is the explicit
            # backstop for a re-restore where values already match.)
            if any(r != ROLE_NONE for r in p["channel_roles"].values()
                   if r in SCOPE_ROLES):
                self._scope_roles_user_configured = True
        # Per-channel bandwidth / coupling override dropdowns.
        if isinstance(p.get("channel_bandwidths"), dict):
            for ch, mode in p["channel_bandwidths"].items():
                if ch in self._bw_combos and mode in SCOPE_BANDWIDTHS:
                    self._bw_combos[ch].setCurrentText(mode)
        if isinstance(p.get("channel_couplings"), dict):
            for ch, mode in p["channel_couplings"].items():
                if ch in self._coupling_combos and mode in SCOPE_COUPLINGS:
                    self._coupling_combos[ch].setCurrentText(mode)
        # Re-grey bandwidth/coupling for any None-role channel (a re-restore
        # where the role text already matched won't have fired _on_role_changed).
        self._refresh_scope_option_enabled()
        # Legacy channel-role format (below) applies ONLY when the modern
        # ``channel_roles`` dict is absent from the prefs blob.
        if not isinstance(p.get("channel_roles"), dict):
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
                    self._scope_roles_user_configured = True
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
        out: dict = {"device_view": self.device_view.view_state()}
        # Skip persisting if either pane has been collapsed to zero —
        # restoring the broken layout next session would hide the
        # device-view (or the forms) entirely.
        ms_sizes = list(self._main_split.sizes())
        if all(int(s) > 0 for s in ms_sizes):
            out["main_split"] = ms_sizes
        return out

    def restore_view_state(self, view: dict):
        if not view:
            return
        sizes = view.get("main_split")
        if isinstance(sizes, (list, tuple)) and len(sizes) >= 2:
            try:
                int_sizes = [int(s) for s in sizes]
            except (TypeError, ValueError):
                int_sizes = None
            if int_sizes is not None and all(s > 0 for s in int_sizes):
                self._main_split.setSizes(int_sizes)
        if isinstance(view.get("device_view"), dict):
            self.device_view.restore_view_state(view["device_view"])
