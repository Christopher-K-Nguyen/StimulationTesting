"""Pulse-shape control panel — biphasic / triphasic, symmetric / asymmetric.

Replaces the per-tab pattern QFormLayout in each experiment tab. Lets
the user pick:

* **Phase count**  — Biphasic (2 phases) or Triphasic (3 phases)
* **Symmetry**     — Symmetric (phase 2/3 are derived) or Asymmetric
                     (every phase amplitude/width is independently set)
* **Polarity**     — Cathodic-first or Anodic-first
* **Triphasic ratio** — three free-form floats, default 2 / -3 / 1, only
                     visible in Symmetric Triphasic mode
* **Per-phase amplitudes / widths** — visible in Asymmetric mode, one
                     spinbox pair per phase
* **Interphase delay**, **discharge delay**, **rate**
* **Charge-balance mode** — Off / Auto-adjust last phase amplitude /
                     Auto-adjust last phase width. When auto-adjust is
                     on, the chosen knob is recomputed live from the
                     other phases so the net charge per pulse is zero.

Public API
----------
* ``pattern() -> PulsePattern``   — current pattern (after charge balance)
* ``patternChanged(PulsePattern)`` — emitted on every control change
* ``current_prefs() / restore_prefs()`` — persistence

The panel does **not** know anything about which experiment is using it;
each tab can wrap it in its own QGroupBox with the right title.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6 import QtCore, QtWidgets

from ..config import (
    DEFAULT_DISCHARGE_DELAY_US, DEFAULT_INTERPHASE_DELAY_US,
    DEFAULT_PHASE_WIDTH_US, DEFAULT_RATE_PPS,
    STIM_CURRENT_RESOLUTION_UA, STIM_MAX_AMPLITUDE_UA,
    STIM_TIME_RESOLUTION_US,
)
from ..waveforms import Phase, PulsePattern
from . import rich
from .repeating_spinbox import RepeatingDoubleSpinBox, RepeatingSpinBox


# Phase-count and symmetry combo entries. String values are stable across
# releases — they're saved into the prefs JSON.
BIPHASIC = "Biphasic"
TRIPHASIC = "Triphasic"
ARBITRARY = "Arbitrary"
SYMMETRIC = "Symmetric"
ASYMMETRIC = "Asymmetric"

# Arbitrary sub-mode entries
ARB_FIXED = "Fixed"        # one shared period, N amplitude values
ARB_VARIABLE = "Variable"  # N (amplitude, duration) pairs

# Hardware row caps and per-row width range. PlexStim 2.0 limits
# arb-pattern rows to 999 (fixed-period) or 499 (variable-period) and
# constrains durations to 1–65535 µs. The amplitude bound is the
# usual ±1000 µA ceiling enforced elsewhere.
ARB_MAX_ROWS_FIXED = 999
ARB_MAX_ROWS_VAR = 499
ARB_MIN_DURATION_US = 1
ARB_MAX_DURATION_US = 65535
ARB_DEFAULT_ROWS = 4

CHARGE_BAL_OFF = "Off (manual)"
CHARGE_BAL_AMP = "Auto-adjust last phase amplitude"
CHARGE_BAL_WID = "Auto-adjust last phase width"


class PatternControlPanel(QtWidgets.QGroupBox):
    """Pulse-shape control panel + live PulsePattern output."""

    patternChanged = QtCore.pyqtSignal(object)   # PulsePattern
    # Emitted alongside patternChanged with a True/False indicating
    # whether the charge-balance warning should be visible at all
    # (only meaningful in biphasic + asymmetric + manual mode).
    balanceWarningVisibility = QtCore.pyqtSignal(bool)

    DEFAULT_TRI_RATIO = (2.0, -3.0, 1.0)
    # PlexStim 2.0 current-source rails at ±1000 μA per channel — clamp
    # the spinboxes there so the user can't ask for amplitudes the
    # hardware can't deliver.
    AMP_RANGE = (-STIM_MAX_AMPLITUDE_UA, STIM_MAX_AMPLITUDE_UA)
    WIDTH_RANGE = (1.0, 5000.0)

    # Two-way toggle between pulse rate (pps) and pulse period (ms).
    # Same rate under the hood — only the display flips. ``rate_hz`` is
    # always the canonical value passed to the runner.
    UNIT_PPS = "pps"
    UNIT_MS = "ms"
    RATE_UNIT_CHOICES = (UNIT_PPS, UNIT_MS)

    def __init__(self, title: str = "Pulse pattern", parent=None):
        super().__init__(title, parent)
        self._suspend_signals = False
        # Remembered rate (in Hz, the canonical unit) while "No
        # interpulse delay" is on. Toggling on captures the user's
        # typed rate; toggling off restores it. None means "nothing
        # saved yet". Stored in Hz so a unit change while pinned still
        # restores the same rate when the user un-pins.
        self._saved_rate_hz: Optional[float] = None
        # Current rate-spinbox unit. Default = pps to match prior
        # behaviour and what the runner / hardware see at the boundary.
        self._rate_unit: str = self.UNIT_PPS

        # ------ top row: phase count + symmetry + polarity ------
        self.phase_count = QtWidgets.QComboBox()
        self.phase_count.addItems([BIPHASIC, TRIPHASIC, ARBITRARY])
        self.symmetry = QtWidgets.QComboBox()
        self.symmetry.addItems([SYMMETRIC, ASYMMETRIC])
        self.polarity = QtWidgets.QComboBox()
        self.polarity.addItems(["Cathodic-first", "Anodic-first"])

        # ------ symmetric (single-shape) controls ------
        # Hardware resolution: 0.1 μA on amplitudes, 1 μs on widths.
        self.amp_excite = self._dspin(*self.AMP_RANGE, 50.0,
                                      step=STIM_CURRENT_RESOLUTION_UA, decimals=1,
                                      suffix=" " + rich.UA)
        self.width_shared = self._dspin(*self.WIDTH_RANGE, DEFAULT_PHASE_WIDTH_US,
                                        step=STIM_TIME_RESOLUTION_US, decimals=0,
                                        suffix=" " + rich.US)
        # Triphasic ratio: 3 small spinboxes
        self.ratio_spins: List[RepeatingDoubleSpinBox] = []
        for v in self.DEFAULT_TRI_RATIO:
            sp = RepeatingDoubleSpinBox()
            sp.setRange(-100.0, 100.0); sp.setDecimals(2); sp.setSingleStep(0.1)
            # Width sized to fit the widest legal value ("-100.00") plus
            # the up/down arrow buttons with comfortable margin even at
            # higher Windows DPI scaling.
            sp.setValue(v); sp.setFixedWidth(110)
            self.ratio_spins.append(sp)

        # ------ asymmetric per-phase controls ------
        # Same hardware resolution as the symmetric path.
        self.phase_amp: List[QtWidgets.QDoubleSpinBox] = []
        self.phase_width: List[QtWidgets.QDoubleSpinBox] = []
        for i in range(3):
            self.phase_amp.append(self._dspin(*self.AMP_RANGE,
                                              -50.0 if i == 0 else 50.0,
                                              step=STIM_CURRENT_RESOLUTION_UA,
                                              decimals=1, suffix=" " + rich.UA))
            self.phase_width.append(self._dspin(*self.WIDTH_RANGE,
                                                DEFAULT_PHASE_WIDTH_US,
                                                step=STIM_TIME_RESOLUTION_US,
                                                decimals=0, suffix=" " + rich.US))

        # ------ arbitrary-pattern controls ------
        # Sub-mode: Fixed (one period for all rows, 1-col table) or
        # Variable (per-row duration, 2-col table). Internal storage is
        # always (amp, duration) tuples — Fixed mode just substitutes
        # the shared period for every row's duration when pattern() is
        # built.
        self.arb_mode = QtWidgets.QComboBox()
        self.arb_mode.addItems([ARB_FIXED, ARB_VARIABLE])
        self.arb_mode.currentTextChanged.connect(self._on_arb_mode_changed)

        # Row count — auto-clamped to the per-mode hardware ceiling.
        self.arb_n_rows = RepeatingSpinBox()
        self.arb_n_rows.setRange(1, ARB_MAX_ROWS_FIXED)
        self.arb_n_rows.setValue(ARB_DEFAULT_ROWS)
        self.arb_n_rows.valueChanged.connect(self._on_arb_n_rows_changed)

        # Shared period for the Fixed sub-mode.
        self.arb_period_us = RepeatingDoubleSpinBox()
        self.arb_period_us.setRange(ARB_MIN_DURATION_US, ARB_MAX_DURATION_US)
        self.arb_period_us.setDecimals(0); self.arb_period_us.setSingleStep(1.0)
        self.arb_period_us.setValue(DEFAULT_PHASE_WIDTH_US)
        self.arb_period_us.setSuffix(" " + rich.US)
        self.arb_period_us.valueChanged.connect(self._emit)

        # Editable amplitude / duration table. The column count toggles
        # with the sub-mode. We pre-allocate ARB_MAX_ROWS_FIXED rows so
        # n_rows just changes which rows are *visible* — keeping any
        # off-screen values intact when the user later re-expands.
        self.arb_table = QtWidgets.QTableWidget(ARB_DEFAULT_ROWS, 2)
        self.arb_table.setHorizontalHeaderLabels([
            f"Amplitude [{rich.UA}]", f"Duration [{rich.US}]",
        ])
        hdr = self.arb_table.horizontalHeader()
        hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.arb_table.verticalHeader().setDefaultSectionSize(22)
        self.arb_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.DoubleClicked |
            QtWidgets.QAbstractItemView.EditTrigger.SelectedClicked |
            QtWidgets.QAbstractItemView.EditTrigger.EditKeyPressed)
        self.arb_table.itemChanged.connect(lambda _it: self._emit())

        # ------ delays + rate ------
        self.interphase_us = self._dspin(0.0, 5000.0, DEFAULT_INTERPHASE_DELAY_US,
                                         step=STIM_TIME_RESOLUTION_US, decimals=0,
                                         suffix=" " + rich.US)
        self.discharge_us = self._dspin(0.0, 5000.0, DEFAULT_DISCHARGE_DELAY_US,
                                        step=STIM_TIME_RESOLUTION_US, decimals=0,
                                        suffix=" " + rich.US)
        # PlexStim 2.0 supports 0.008–100,000 pps. The actual ceiling is
        # ALSO bounded by the pulse width: one pulse + a 5 µs guaranteed
        # interpulse gap must fit inside one period.
        # _update_rate_max() recomputes the maximum after any
        # phase-width / interphase / discharge change.
        self.rate_pps = self._dspin(0.008, 100000.0, DEFAULT_RATE_PPS,
                                    suffix=" " + rich.PPS)
        self.rate_pps.setDecimals(3)
        # Unit toggle — pps (rate) vs. s / ms / µs (period). Stored value
        # is always interpreted in the currently-selected unit; helpers
        # below convert to/from rate_hz at the pattern boundary.
        self.rate_unit_combo = QtWidgets.QComboBox()
        for u in self.RATE_UNIT_CHOICES:
            self.rate_unit_combo.addItem(u)
        self.rate_unit_combo.setCurrentText(self._rate_unit)
        self.rate_unit_combo.currentTextChanged.connect(self._on_rate_unit_changed)

        # Toggles for the three "skip / pin" delay shortcuts:
        # * No interphase delay   — default OFF; when on, T_iph forced to 0
        # * Discharge delay       — default ON;  when off, T_dd forced to 0
        # * No interpulse delay   — default OFF; when on, rate locked to
        #                           the maximum (1 e6 / (total + 5 µs))
        #                           and the spinbox shows the running value.
        self.no_interphase_check = QtWidgets.QCheckBox("No interphase delay")
        self.no_interphase_check.setChecked(False)
        self.no_interphase_check.toggled.connect(self._on_no_interphase_toggled)
        self.discharge_check = QtWidgets.QCheckBox("Discharge delay")
        self.discharge_check.setChecked(True)
        self.discharge_check.toggled.connect(self._on_discharge_toggled)
        self.no_interpulse_check = QtWidgets.QCheckBox("No interpulse delay")
        self.no_interpulse_check.setChecked(False)
        self.no_interpulse_check.toggled.connect(self._on_no_interpulse_toggled)

        # ------ charge-balance mode ------
        self.charge_mode = QtWidgets.QComboBox()
        self.charge_mode.addItems([CHARGE_BAL_OFF, CHARGE_BAL_AMP, CHARGE_BAL_WID])

        # ------ assemble ------
        outer = QtWidgets.QVBoxLayout(self)
        # Tight margins so the pattern panel doesn't add visible empty
        # space below its last form row before the next sibling widget.
        outer.setContentsMargins(8, 4, 8, 4)
        outer.setSpacing(4)
        # Top row: phase count + symmetry + polarity. Symmetry is only
        # meaningful for biphasic — triphasic uses the ratio knobs to
        # express asymmetry — so we hide the symmetry combo whenever
        # phase_count == Triphasic. Stored as ``self._sym_label_widget``
        # so both the combo and its row label can hide together.
        self.top_form = rich.make_form()
        top_phase = QtWidgets.QHBoxLayout()
        top_phase.addWidget(self.phase_count, stretch=1)
        self._sym_separator = QtWidgets.QLabel(" / ")
        top_phase.addWidget(self._sym_separator)
        top_phase.addWidget(self.symmetry, stretch=1)
        tp_w = QtWidgets.QWidget(); tp_w.setLayout(top_phase)
        self.top_form.addRow("Phases:", tp_w)
        self.top_form.addRow("Polarity:", self.polarity)
        outer.addLayout(self.top_form)

        # Symmetric / triphasic-ratio panel
        self.sym_box = QtWidgets.QWidget()
        self._sym_form = rich.make_form(self.sym_box)
        self._sym_form.setContentsMargins(0, 0, 0, 0)
        # Amplitude row label is dynamic — "Stimulation current (I) [μA]:"
        # for biphasic, "Amplitude factor (I) [μA]:" for triphasic.
        self._amp_row_label = QtWidgets.QLabel()
        self._amp_row_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._sym_form.addRow(self._amp_row_label, self.amp_excite)
        self._sym_form.addRow(
            rich.field_label("Phase width", rich.T_PH, rich.US),
            self.width_shared,
        )
        # Triphasic-only ratio row. To the right of the three ratio
        # spinboxes a small label calls out which phase the runner
        # treats as the *excitation* phase (the one ramping scales
        # against — the phase with the largest |Q_ph|). Equal phase
        # widths in the symmetric path means largest |Q_ph| = largest
        # |ratio entry|, so the label simply reads the spinbox values
        # and tags the winner.
        ratio_row = QtWidgets.QHBoxLayout()
        for sp in self.ratio_spins:
            ratio_row.addWidget(sp)
        self.ratio_excitation_label = rich.make_label("")
        self.ratio_excitation_label.setStyleSheet(
            "color: #1565c0; font-size: 9pt; padding-left: 8px;")
        ratio_row.addWidget(self.ratio_excitation_label)
        ratio_row.addStretch(1)
        self.ratio_w = QtWidgets.QWidget(); self.ratio_w.setLayout(ratio_row)
        self._sym_form.addRow("Triphasic ratio (a : b : c):", self.ratio_w)
        # Refresh the excitation tag whenever any ratio entry changes.
        for sp in self.ratio_spins:
            sp.valueChanged.connect(self._refresh_ratio_excitation_label)
        self._refresh_ratio_excitation_label()
        outer.addWidget(self.sym_box)

        # Asymmetric panel — one row per phase with [amp ___ width ___]
        self.asym_box = QtWidgets.QWidget()
        af = rich.make_form(self.asym_box)
        af.setContentsMargins(0, 0, 0, 0)
        self._phase_rows: List[QtWidgets.QWidget] = []
        for i in range(3):
            row = QtWidgets.QHBoxLayout()
            # Use the project's standard "Name [unit]:" form so the
            # asymmetric rows match the rest of the parameter forms.
            row.addWidget(QtWidgets.QLabel(f"Amplitude [{rich.UA}]:"))
            row.addWidget(self.phase_amp[i], stretch=1)
            row.addSpacing(8)
            row.addWidget(QtWidgets.QLabel(f"Width [{rich.US}]:"))
            row.addWidget(self.phase_width[i], stretch=1)
            w = QtWidgets.QWidget(); w.setLayout(row)
            self._phase_rows.append(w)
            af.addRow(f"Phase {i+1}:", w)
        outer.addWidget(self.asym_box)

        # Arbitrary-pattern body
        self.arb_box = QtWidgets.QWidget()
        arb_form = rich.make_form(self.arb_box)
        arb_form.setContentsMargins(0, 0, 0, 0)
        arb_form.addRow("Sampling:", self.arb_mode)
        # Row count + period sit on the same row so the user can see
        # both at a glance. The period label hides itself when the
        # sub-mode is Variable (it's not used there).
        rp_row = QtWidgets.QHBoxLayout()
        rp_row.addWidget(QtWidgets.QLabel("Rows:"))
        rp_row.addWidget(self.arb_n_rows)
        rp_row.addSpacing(12)
        self._arb_period_label = QtWidgets.QLabel("Pulse period:")
        rp_row.addWidget(self._arb_period_label)
        rp_row.addWidget(self.arb_period_us)
        rp_row.addStretch(1)
        rp_w = QtWidgets.QWidget(); rp_w.setLayout(rp_row)
        arb_form.addRow("", rp_w)
        arb_form.addRow(self.arb_table)
        outer.addWidget(self.arb_box)

        # Delays + rate. Each row is spinbox + a per-row checkbox that
        # short-circuits the spinbox value (no-interphase / discharge /
        # no-interpulse). Wrap each row in a QHBoxLayout so the
        # checkbox sits flush right of its spinbox.
        delays = rich.make_form()

        iph_row = QtWidgets.QHBoxLayout()
        iph_row.addWidget(self.interphase_us, stretch=1)
        iph_row.addWidget(self.no_interphase_check)
        iph_w = QtWidgets.QWidget(); iph_w.setLayout(iph_row)
        delays.addRow(rich.field_label("Interphase delay", rich.T_IPH, rich.US),
                      iph_w)

        dd_row = QtWidgets.QHBoxLayout()
        dd_row.addWidget(self.discharge_us, stretch=1)
        dd_row.addWidget(self.discharge_check)
        dd_w = QtWidgets.QWidget(); dd_w.setLayout(dd_row)
        delays.addRow(rich.field_label("Discharge delay", rich.T_DD, rich.US),
                      dd_w)

        rate_row = QtWidgets.QHBoxLayout()
        rate_row.addWidget(self.rate_pps, stretch=1)
        rate_row.addWidget(self.rate_unit_combo)
        rate_row.addWidget(self.no_interpulse_check)
        rate_w = QtWidgets.QWidget(); rate_w.setLayout(rate_row)
        # Row label flips between "Pulse rate" and "Pulse period" when
        # the user toggles the unit combo. We construct the QLabel by
        # hand (rather than letting QFormLayout build one from a string)
        # so we keep a handle and can rewrite its text later.
        self._rate_row_label = QtWidgets.QLabel(
            rich.field_label("Pulse rate", unit_str=rich.PPS))
        self._rate_row_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        delays.addRow(self._rate_row_label, rate_w)
        outer.addLayout(delays)

        # Apply initial enabled-state for the three checkboxes.
        self._on_no_interphase_toggled(self.no_interphase_check.isChecked())
        self._on_discharge_toggled(self.discharge_check.isChecked())
        self._on_no_interpulse_toggled(self.no_interpulse_check.isChecked())
        # Initial arbitrary-table layout (column count + period
        # visibility for the default Fixed sub-mode).
        self._on_arb_mode_changed(self.arb_mode.currentText())

        # Charge balance — only shown for biphasic + asymmetric (the only
        # mode where the user has independent control over both phase
        # amps/widths and might wind up with a net-charge imbalance).
        # Symmetric biphasic always balances by mirroring; triphasic
        # uses ratio knobs that the user can hand-tune to balance.
        self._charge_form = rich.make_form()
        self._charge_form.addRow("Charge balance:", self.charge_mode)
        # Wrap in a widget so the whole row can be hidden together.
        self._charge_w = QtWidgets.QWidget()
        self._charge_w.setLayout(self._charge_form)
        outer.addWidget(self._charge_w)

        # ----- wiring -----
        self.phase_count.currentTextChanged.connect(self._on_mode_changed)
        self.symmetry.currentTextChanged.connect(self._on_mode_changed)
        # ``_on_polarity_changed`` flips the asymmetric phase signs to
        # match the new polarity, then emits — chaining the emit
        # itself instead of subscribing twice. Track the previous
        # text so we can detect a real flip vs. an idempotent
        # ``setCurrentText`` (e.g. during prefs restore).
        self._prev_polarity = self.polarity.currentText()
        self.polarity.currentTextChanged.connect(self._on_polarity_changed)
        self.charge_mode.currentTextChanged.connect(self._on_balance_changed)
        for w in (self.amp_excite, self.width_shared,
                  self.interphase_us, self.discharge_us, self.rate_pps,
                  *self.ratio_spins, *self.phase_amp, *self.phase_width):
            w.valueChanged.connect(self._emit)

        # Initial visibility
        self._on_mode_changed()

    # ----------------------------------------------------------- helpers
    @staticmethod
    def _dspin(lo: float, hi: float, val: float,
               step: float = 1.0, decimals: int = 2,
               suffix: str = "") -> RepeatingDoubleSpinBox:
        sp = RepeatingDoubleSpinBox()
        sp.setRange(lo, hi); sp.setDecimals(decimals)
        sp.setSingleStep(step); sp.setValue(val); sp.setSuffix(suffix)
        return sp

    # ----------------------------------------------------------- mode changes
    def _on_mode_changed(self, *_):
        kind = self.phase_count.currentText()
        triphasic = (kind == TRIPHASIC)
        arbitrary = (kind == ARBITRARY)
        # Symmetry combo is only meaningful for biphasic.
        sym_combo_visible = not (triphasic or arbitrary)
        self.symmetry.setVisible(sym_combo_visible)
        self._sym_separator.setVisible(sym_combo_visible)
        if triphasic:
            asym = False
        elif arbitrary:
            asym = False
        else:
            asym = self.symmetry.currentText() == ASYMMETRIC

        # Sym / asym / arb bodies are mutually exclusive.
        self.sym_box.setVisible((not asym) and (not arbitrary))
        self.asym_box.setVisible(asym and (not arbitrary))
        self.arb_box.setVisible(arbitrary)
        self.ratio_w.setVisible(triphasic)
        # Polarity isn't meaningful for arbitrary patterns — the user
        # types signed amplitudes directly into the table. Hide its row.
        self.polarity.setVisible(not arbitrary)
        pol_label = self.top_form.labelForField(self.polarity)
        if pol_label is not None:
            pol_label.setVisible(not arbitrary)
        # Ratio row's label (sibling to ratio_w in the form layout)
        lab = self._sym_form.labelForField(self.ratio_w)
        if lab is not None:
            lab.setVisible(triphasic)

        # Amplitude row label depends on phase count
        if triphasic:
            self._amp_row_label.setText(
                rich.field_label("Amplitude factor", rich.var("I"), rich.UA))
        else:
            self._amp_row_label.setText(
                rich.field_label("Stimulation current", rich.I_STIM, rich.UA))

        # Hide the third asymmetric phase row when biphasic
        self._phase_rows[2].setVisible(triphasic)
        af = self.asym_box.layout()
        if isinstance(af, QtWidgets.QFormLayout):
            lab3 = af.labelForField(self._phase_rows[2])
            if lab3 is not None:
                lab3.setVisible(triphasic)

        # Charge balance row is meaningful ONLY for biphasic + asymmetric.
        # Arbitrary mode lets the user craft any charge profile they
        # want — the auto-balance shortcut wouldn't know which row to
        # rewrite — so it's hidden there too.
        cb_visible = (not triphasic) and (not arbitrary) and asym
        self._charge_w.setVisible(cb_visible)
        if not cb_visible:
            # Disable any auto-adjust lock that the dropdown might
            # have left on a spinbox in the previous mode.
            for sp in (*self.phase_amp, *self.phase_width):
                self._set_lock_state(sp, False)
        self._emit()

    def _on_polarity_changed(self, text: str):
        """Flip the asymmetric phase amplitudes when polarity flips.

        In symmetric mode, polarity is applied at pattern-build time
        from the ``self.polarity`` dropdown (see :meth:`pattern`), so
        the spinbox magnitudes are unaffected. In **asymmetric** mode
        the user types signed amplitudes directly — when they flip the
        polarity dropdown we invert each phase amp's sign so the
        per-phase signs stay consistent with the new polarity choice.
        Same convention as the symmetric path: polarity = -1
        (Cathodic-first) puts the excitation phase on the negative
        rail; polarity = +1 (Anodic-first) puts it on the positive
        rail. The user can still over-edit any individual sign.
        """
        if text != self._prev_polarity:
            for sp in self.phase_amp:
                sp.blockSignals(True)
                try:
                    sp.setValue(-sp.value())
                finally:
                    sp.blockSignals(False)
        self._prev_polarity = text
        self._emit()

    def _refresh_ratio_excitation_label(self, *_):
        """Re-render the excitation-phase tag next to the triphasic ratios.

        The runner ramps against the phase carrying the largest |Q_ph|
        (see :pyattr:`PulsePattern.excitation_phase`); for the
        symmetric path with shared phase widths that's the same as
        the spinbox row whose ``|value|`` is greatest. Tag the
        winning phase by index so the user sees, at a glance, which
        ratio entry is "the one the ramp uses".
        """
        try:
            vals = [float(sp.value()) for sp in self.ratio_spins]
        except Exception:
            self.ratio_excitation_label.setText("")
            return
        if not vals or all(v == 0 for v in vals):
            self.ratio_excitation_label.setText("")
            return
        # Find the index with the largest magnitude. ties → first.
        idx = max(range(len(vals)), key=lambda i: abs(vals[i]))
        # Phase letter follows the row label "(a : b : c)".
        letter = "abc"[idx] if idx < 3 else f"#{idx + 1}"
        self.ratio_excitation_label.setText(
            f"→ excitation phase: <b>{letter}</b> "
            f"(<i>{vals[idx]:+g}</i>)"
        )

    def set_amplitude_visible(self, visible: bool):
        """Hide/show the amplitude (Stimulation current) row.

        The VT tab calls this when the user picks Fixed charge
        density: the per-pulse current is then derived from
        Q_inj × area / phase_width per electrode, so a single
        editable amplitude field is misleading. The phase-shape
        controls (width, polarity, ratio, asymmetry) stay visible
        because they're still under the user's control.
        """
        self.amp_excite.setVisible(visible)
        self._amp_row_label.setVisible(visible)
        # Asymmetric per-phase amplitudes carry the same meaning —
        # also hide them in fixed-Q_inj mode so the user can't type a
        # current value the runner is going to overwrite anyway.
        for sp in self.phase_amp:
            sp.setVisible(visible)

    def _on_arb_mode_changed(self, mode: str):
        """Toggle column count + visibility of the period field."""
        is_fixed = (mode == ARB_FIXED)
        # Column count: 1 (amplitude) for Fixed; 2 (amp + duration) for Variable.
        self.arb_table.setColumnCount(1 if is_fixed else 2)
        if is_fixed:
            self.arb_table.setHorizontalHeaderLabels([f"Amplitude [{rich.UA}]"])
        else:
            self.arb_table.setHorizontalHeaderLabels([
                f"Amplitude [{rich.UA}]", f"Duration [{rich.US}]",
            ])
        # Row-count cap depends on the sub-mode (999 vs 499).
        cap = ARB_MAX_ROWS_FIXED if is_fixed else ARB_MAX_ROWS_VAR
        self.arb_n_rows.setMaximum(cap)
        if self.arb_n_rows.value() > cap:
            self.arb_n_rows.setValue(cap)
        # Period field is only used in Fixed mode.
        self.arb_period_us.setVisible(is_fixed)
        self._arb_period_label.setVisible(is_fixed)
        self._emit()

    def _on_arb_n_rows_changed(self, n: int):
        """Resize the table — keeps existing cell values intact."""
        n = max(1, int(n))
        self.arb_table.setRowCount(n)
        self._emit()

    def _on_balance_changed(self, *_):
        # Disable the auto-adjusted spinbox so the user can see it's
        # being driven by the panel rather than typed in.
        mode = self.charge_mode.currentText()
        # Find the "last phase" spinbox in the visible mode
        if self.symmetry.currentText() == SYMMETRIC:
            # In symmetric mode, the user only edits the excitation amp /
            # shared width; auto-balance is implied by the symmetry.
            # We still expose the dropdown so the user can opt out and
            # then switch to asymmetric to do something custom.
            self._set_lock_state(self.amp_excite, False)
            self._set_lock_state(self.width_shared, False)
        else:
            triphasic = self.phase_count.currentText() == TRIPHASIC
            last_idx = 2 if triphasic else 1
            self._set_lock_state(self.phase_amp[last_idx], mode == CHARGE_BAL_AMP)
            self._set_lock_state(self.phase_width[last_idx], mode == CHARGE_BAL_WID)
        self._emit()

    @staticmethod
    def _set_lock_state(w: QtWidgets.QDoubleSpinBox, locked: bool):
        w.setReadOnly(locked)
        w.setStyleSheet("background:#f0f0f0; font-style: italic;" if locked else "")

    # ----------------------------------------------------------- pattern build
    def pattern(self) -> PulsePattern:
        """Build a PulsePattern from the current control state."""
        polarity = -1 if self.polarity.currentText().startswith("Cathodic") else +1
        kind = self.phase_count.currentText()
        triphasic = (kind == TRIPHASIC)
        arbitrary = (kind == ARBITRARY)
        # Triphasic is always handled via the ratio path — no asymmetric
        # per-phase entry. Biphasic uses whatever symmetry the user picked.
        # Arbitrary skips both branches and reads phases from the table.
        asym = (not triphasic) and (not arbitrary) and \
               (self.symmetry.currentText() == ASYMMETRIC)
        rate = self._current_rate_hz()
        # Honour the no-interphase / discharge-disable shortcuts:
        # when set they override the spinbox value (which we keep
        # editable in the background so the user's typed value is
        # preserved across toggles).
        iph = (0.0 if self.no_interphase_check.isChecked()
               else float(self.interphase_us.value()))
        dd = (float(self.discharge_us.value())
              if self.discharge_check.isChecked() else 0.0)

        if arbitrary:
            # ---- Arbitrary path: read amp[+duration] from the table.
            n = self.arb_table.rowCount()
            is_fixed = self.arb_mode.currentText() == ARB_FIXED
            shared_us = float(self.arb_period_us.value())
            phases: List[Phase] = []
            for r in range(n):
                amp_item = self.arb_table.item(r, 0)
                amp_txt = amp_item.text().strip() if amp_item else ""
                if not amp_txt:
                    continue
                try:
                    amp = float(amp_txt)
                except ValueError:
                    continue
                if is_fixed:
                    width = shared_us
                else:
                    dur_item = self.arb_table.item(r, 1)
                    dur_txt = dur_item.text().strip() if dur_item else ""
                    try:
                        width = float(dur_txt)
                    except ValueError:
                        continue
                # Clamp duration to the hardware range to avoid silent
                # rejection later in the runner.
                width = max(ARB_MIN_DURATION_US,
                            min(ARB_MAX_DURATION_US, width))
                phases.append(Phase(amp, width, 0.0))
            # Discharge delay is appended after the last phase if the
            # discharge_check is on; same convention as the structured
            # bi-/triphasic paths.
            if phases and self.discharge_check.isChecked():
                last = phases[-1]
                phases[-1] = Phase(last.amplitude_ua, last.width_us, dd)
            pat = PulsePattern(phases=phases, rate_hz=rate)
        elif not asym:
            # ---- Symmetric path: derive every phase from one or two knobs.
            mag = abs(float(self.amp_excite.value()))
            w = float(self.width_shared.value())
            if not triphasic:
                pat = PulsePattern.biphasic(
                    amplitude_ua=mag, phase_width_us=w,
                    interphase_us=iph, discharge_us=dd,
                    polarity=polarity, rate_hz=rate,
                )
            else:
                ratio = tuple(sp.value() for sp in self.ratio_spins)
                pat = PulsePattern.triphasic(
                    amp_excite_ua=mag, phase_width_us=w,
                    interphase_us=iph, discharge_us=dd,
                    polarity=polarity, rate_hz=rate,
                    ratio=ratio,
                )
        else:
            # ---- Asymmetric path: per-phase amp/width as typed.
            phases = []
            n = 3 if triphasic else 2
            for i in range(n):
                amp = float(self.phase_amp[i].value())
                w = float(self.phase_width[i].value())
                # All phases except the last get the interphase delay;
                # the last gets the discharge delay (matches Plexon order).
                delay = iph if i < n - 1 else dd
                phases.append(Phase(amp, w, delay))
            pat = PulsePattern(phases=phases, rate_hz=rate)

        # ---- Charge balance ----
        # Only meaningful in biphasic + asymmetric (the only mode where
        # the user can independently set both phases and end up with a
        # net-charge imbalance the panel needs to absorb).
        mode = (self.charge_mode.currentText()
                if (asym and not triphasic) else CHARGE_BAL_OFF)
        if mode == CHARGE_BAL_AMP:
            try: pat = pat.auto_balance(adjust="last_amp")
            except ValueError: pass
        elif mode == CHARGE_BAL_WID:
            try: pat = pat.auto_balance(adjust="last_width")
            except ValueError: pass

        # ---- If a phase param was auto-computed, push it back into the
        # disabled spinbox so the user sees the value the panel is using.
        if asym and mode != CHARGE_BAL_OFF:
            self._suspend_signals = True
            try:
                last_idx = (3 if triphasic else 2) - 1
                last = pat.phases[last_idx]
                if mode == CHARGE_BAL_AMP:
                    self.phase_amp[last_idx].setValue(last.amplitude_ua)
                else:
                    self.phase_width[last_idx].setValue(last.width_us)
            finally:
                self._suspend_signals = False
        return pat

    # Minimum guaranteed dead-time between consecutive pulses (μs).
    # Hardware-conservative — the rate ceiling is computed from
    # 1 e6 / (total_pulse_µs + MIN_INTERPULSE_GAP_US).
    MIN_INTERPULSE_GAP_US = 5.0
    # Hardware envelope on rate: PlexStim 2.0 supports 0.008–100,000 pps.
    RATE_HZ_MIN = 0.008
    RATE_HZ_MAX = 100000.0

    # ----------------------------------------------------------- rate-unit helpers
    def _hz_to_unit(self, rate_hz: float, unit: Optional[str] = None) -> float:
        """Translate a rate in Hz into the spinbox's display value for
        the given unit (defaults to the currently-selected unit). Period
        mode inverts the rate; ``pps`` is identity.
        """
        u = unit or self._rate_unit
        if u == self.UNIT_PPS:
            return float(rate_hz)
        if rate_hz <= 0:
            return 0.0
        return 1e3 / rate_hz   # ms

    def _unit_to_hz(self, value: float, unit: Optional[str] = None) -> float:
        u = unit or self._rate_unit
        if u == self.UNIT_PPS:
            return float(value)
        if value <= 0:
            return self.RATE_HZ_MIN
        return 1e3 / float(value)   # ms -> Hz

    def _current_rate_hz(self) -> float:
        """Rate in Hz, derived from the spinbox and the selected unit."""
        return self._unit_to_hz(float(self.rate_pps.value()))

    def _set_rate_hz(self, rate_hz: float):
        """Display ``rate_hz`` in the spinbox using the current unit."""
        self.rate_pps.setValue(self._hz_to_unit(rate_hz))

    def _on_rate_unit_changed(self, new_unit: str):
        """User flipped the unit toggle — convert the spinbox display
        without changing the underlying rate, and rebuild the spinbox
        bounds so the legal range reflects the new unit.
        """
        if new_unit == self._rate_unit:
            return
        # Capture rate_hz under the OLD unit so the conversion preserves
        # the pulse rate the user had dialled in.
        old_rate_hz = self._current_rate_hz()
        self._rate_unit = new_unit
        # Suffix + decimals + bounds + label flip per unit.
        self._suspend_signals = True
        try:
            if new_unit == self.UNIT_PPS:
                self.rate_pps.setSuffix(" " + rich.PPS)
                self.rate_pps.setDecimals(3)
                self._rate_row_label.setText(
                    rich.field_label("Pulse rate", unit_str=rich.PPS))
            else:
                self.rate_pps.setSuffix(" " + self.UNIT_MS)
                self.rate_pps.setDecimals(3)
                self._rate_row_label.setText(
                    rich.field_label("Pulse period", unit_str=self.UNIT_MS))
            # Rebuild bounds from the canonical rate envelope.
            # _update_rate_max below tightens further based on pulse width.
            self._reapply_rate_bounds()
            # Re-display the previous rate in the new unit.
            self._set_rate_hz(old_rate_hz)
        finally:
            self._suspend_signals = False
        # Pulse width may now allow a different ceiling — recompute.
        self._update_rate_max()
        self._emit()

    def _reapply_rate_bounds(self):
        """Set the spinbox's hardware-envelope min/max in current units.

        When unit = pps, larger rate = larger displayed value, so
        min/max map directly. When unit is a period, the relationship
        inverts (high rate = small period), so the spinbox min/max
        derive from the rate max/min respectively.
        """
        if self._rate_unit == self.UNIT_PPS:
            lo = self.RATE_HZ_MIN
            hi = self.RATE_HZ_MAX
        else:
            # Period bounds: lower bound = period at MAX rate, upper at MIN rate.
            lo = self._hz_to_unit(self.RATE_HZ_MAX)
            hi = self._hz_to_unit(self.RATE_HZ_MIN)
        self.rate_pps.setRange(lo, hi)

    def _update_rate_max(self):
        """Cap the rate spinbox so one full pulse + a 5 µs interpulse
        gap fits inside one period.

        max_rate = 1e6 / (total_pulse_µs + 5), clamped to the PlexStim
        100,000-pps hardware ceiling. Called every time ``_emit`` fires
        so the limit always reflects the current pulse width. The bound
        is computed in Hz and translated into the spinbox's current
        display unit so the cap moves with the user's view.

        When ``no_interpulse_check`` is on, the spinbox is also pinned
        to the running max — so the rate display tracks whatever the
        pulse width allows.
        """
        try:
            total_us = max(self.pattern().total_pulse_us, 1e-6)
        except Exception:
            return
        # The 5 µs minimum gap is a safety margin — when the user
        # explicitly asks for "No interpulse delay" it's waived so the
        # next pulse can start immediately after the discharge phase.
        gap_us = (0.0 if self.no_interpulse_check.isChecked()
                  else self.MIN_INTERPULSE_GAP_US)
        max_rate_hz = min(1e6 / (total_us + gap_us), self.RATE_HZ_MAX)
        max_rate_hz = max(max_rate_hz, self.RATE_HZ_MIN)
        self._suspend_signals = True
        try:
            # Translate the Hz ceiling into the spinbox's current unit.
            # In pps that's the upper bound; in period units that's the
            # LOWER bound (smaller period <=> higher rate).
            if self._rate_unit == self.UNIT_PPS:
                self.rate_pps.setMaximum(max_rate_hz)
            else:
                self.rate_pps.setMinimum(self._hz_to_unit(max_rate_hz))
            # Clamp / pin behavior — same logic, expressed in Hz so it's
            # unit-agnostic.
            cur_hz = self._current_rate_hz()
            if self.no_interpulse_check.isChecked():
                self._set_rate_hz(max_rate_hz)
            elif cur_hz > max_rate_hz:
                self._set_rate_hz(max_rate_hz)
        finally:
            self._suspend_signals = False

    # ----------------------------------------------------------- delay toggles
    def _on_no_interphase_toggled(self, checked: bool):
        """Skip-interphase: disable the spinbox; ``pattern()`` uses 0."""
        self.interphase_us.setEnabled(not checked)
        self._emit()

    def _on_discharge_toggled(self, checked: bool):
        """Discharge-delay (default on): disable the spinbox when off;
        ``pattern()`` uses 0 in that case."""
        self.discharge_us.setEnabled(checked)
        self._emit()

    def _on_no_interpulse_toggled(self, checked: bool):
        """No-interpulse: lock rate to the running max — disable the
        spinbox so the user can't change it; the value tracks live via
        ``_update_rate_max``. Toggling back off restores the rate the
        user had typed before they enabled the lock.
        """
        if checked:
            # Capture the user-typed rate (in Hz) so toggling off
            # restores it even if the unit toggle moved meanwhile.
            self._saved_rate_hz = self._current_rate_hz()
        else:
            # Restore the prior rate, clamped to whatever the current
            # pulse-width-derived ceiling allows.
            if self._saved_rate_hz is not None:
                self._suspend_signals = True
                try:
                    self._set_rate_hz(self._saved_rate_hz)
                finally:
                    self._suspend_signals = False
            self._saved_rate_hz = None
        # Spinbox is locked while the no-interpulse checkbox is on
        # (its value is driven live by ``_update_rate_max``), but the
        # unit-toggle stays enabled so the user can still flip the
        # display between pps and ms — same rate underneath, just a
        # different way of reading it. ``_on_rate_unit_changed``
        # already preserves rate_hz across a unit swap.
        self.rate_pps.setEnabled(not checked)
        self._emit()

    def _emit(self, *_):
        if self._suspend_signals:
            return
        # Always re-clamp the rate ceiling first so the emitted pattern
        # already reflects the (possibly newly-tighter) max rate.
        self._update_rate_max()
        self.patternChanged.emit(self.pattern())
        # Charge-balance warning is only meaningful when the user has
        # manual control over both phases — biphasic + asymmetric +
        # the "Off (manual)" charge-balance mode. Hide otherwise.
        triphasic = self.phase_count.currentText() == TRIPHASIC
        asym = (not triphasic) and (self.symmetry.currentText() == ASYMMETRIC)
        manual = self.charge_mode.currentText() == CHARGE_BAL_OFF
        self.balanceWarningVisibility.emit(asym and manual)

    # ----------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        return {
            "phase_count": self.phase_count.currentText(),
            "symmetry": self.symmetry.currentText(),
            "polarity": self.polarity.currentText(),
            "amp_excite": self.amp_excite.value(),
            "width_shared": self.width_shared.value(),
            "ratio": [sp.value() for sp in self.ratio_spins],
            "phase_amp": [sp.value() for sp in self.phase_amp],
            "phase_width": [sp.value() for sp in self.phase_width],
            "interphase_us": self.interphase_us.value(),
            "discharge_us": self.discharge_us.value(),
            # Rate is persisted as Hz so the unit-toggle choice is
            # orthogonal to it; the unit just controls how the user
            # *sees* the value, not what gets saved.
            "rate_hz": self._current_rate_hz(),
            "rate_unit": self._rate_unit,
            "charge_mode": self.charge_mode.currentText(),
            # Per-row delay shortcuts (added later — old prefs without
            # these keys fall back to the code-defined defaults).
            "no_interphase": self.no_interphase_check.isChecked(),
            "discharge_on": self.discharge_check.isChecked(),
            "no_interpulse": self.no_interpulse_check.isChecked(),
            # Arbitrary-pattern state — survives restart so a hand-built
            # waveform doesn't get wiped between sessions.
            "arb_mode": self.arb_mode.currentText(),
            "arb_n_rows": int(self.arb_n_rows.value()),
            "arb_period_us": float(self.arb_period_us.value()),
            "arb_table": self._dump_arb_table(),
        }

    def _dump_arb_table(self) -> List[List[str]]:
        """Snapshot the arbitrary-pattern table as a list of (amp[, dur]) lists."""
        n = self.arb_table.rowCount()
        cols = self.arb_table.columnCount()
        rows: List[List[str]] = []
        for r in range(n):
            row: List[str] = []
            for c in range(cols):
                it = self.arb_table.item(r, c)
                row.append(it.text() if it is not None else "")
            rows.append(row)
        return rows

    def restore_prefs(self, p: dict):
        if not p: return
        if "phase_count" in p: self.phase_count.setCurrentText(p["phase_count"])
        if "symmetry" in p: self.symmetry.setCurrentText(p["symmetry"])
        if "polarity" in p: self.polarity.setCurrentText(p["polarity"])
        for key, sp in (("amp_excite", self.amp_excite),
                        ("width_shared", self.width_shared),
                        ("interphase_us", self.interphase_us),
                        ("discharge_us", self.discharge_us)):
            if key in p:
                try: sp.setValue(float(p[key]))
                except (TypeError, ValueError): pass
        # Rate / unit — accept both new (rate_hz + rate_unit) and the
        # legacy ``rate_pps`` key (which was always Hz under the hood).
        if "rate_unit" in p and p["rate_unit"] in self.RATE_UNIT_CHOICES:
            self.rate_unit_combo.setCurrentText(p["rate_unit"])
        rate_hz_val = None
        if "rate_hz" in p:
            try: rate_hz_val = float(p["rate_hz"])
            except (TypeError, ValueError): pass
        elif "rate_pps" in p:
            try: rate_hz_val = float(p["rate_pps"])
            except (TypeError, ValueError): pass
        if rate_hz_val is not None:
            try: self._set_rate_hz(rate_hz_val)
            except (TypeError, ValueError): pass
        if "ratio" in p and isinstance(p["ratio"], (list, tuple)):
            for sp, v in zip(self.ratio_spins, p["ratio"]):
                try: sp.setValue(float(v))
                except (TypeError, ValueError): pass
        if "phase_amp" in p and isinstance(p["phase_amp"], (list, tuple)):
            for sp, v in zip(self.phase_amp, p["phase_amp"]):
                try: sp.setValue(float(v))
                except (TypeError, ValueError): pass
        if "phase_width" in p and isinstance(p["phase_width"], (list, tuple)):
            for sp, v in zip(self.phase_width, p["phase_width"]):
                try: sp.setValue(float(v))
                except (TypeError, ValueError): pass
        if "charge_mode" in p: self.charge_mode.setCurrentText(p["charge_mode"])
        # Delay-shortcut checkboxes (older prefs may not have these).
        if "no_interphase" in p:
            self.no_interphase_check.setChecked(bool(p["no_interphase"]))
        if "discharge_on" in p:
            self.discharge_check.setChecked(bool(p["discharge_on"]))
        if "no_interpulse" in p:
            self.no_interpulse_check.setChecked(bool(p["no_interpulse"]))
        # Arbitrary-pattern state — apply mode first so the column count
        # is right when the table is repopulated.
        if "arb_mode" in p: self.arb_mode.setCurrentText(p["arb_mode"])
        if "arb_n_rows" in p:
            try: self.arb_n_rows.setValue(int(p["arb_n_rows"]))
            except (TypeError, ValueError): pass
        if "arb_period_us" in p:
            try: self.arb_period_us.setValue(float(p["arb_period_us"]))
            except (TypeError, ValueError): pass
        if isinstance(p.get("arb_table"), list):
            self._load_arb_table(p["arb_table"])
        self._on_mode_changed()
        self._on_balance_changed()

    def _load_arb_table(self, rows):
        """Re-populate the arbitrary table from a list-of-lists snapshot."""
        self.arb_table.blockSignals(True)
        try:
            n = min(len(rows), self.arb_n_rows.maximum())
            self.arb_table.setRowCount(max(n, self.arb_table.rowCount()))
            for r, row in enumerate(rows[:n]):
                for c, val in enumerate(row[: self.arb_table.columnCount()]):
                    self.arb_table.setItem(
                        r, c, QtWidgets.QTableWidgetItem(str(val) if val else ""))
        finally:
            self.arb_table.blockSignals(False)
