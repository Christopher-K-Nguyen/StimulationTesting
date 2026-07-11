"""Combination panel — lists the (active, returns) test combos to run.

Inputs:
* actives                 — channel numbers selected in the grid
* global return           — bool, True when the off-array counter electrode
                            is in the circuit (toggled via the External Return
                            square next to the channel grid). Required for
                            Monopolar and any "Partial …" kind.
* spacing                 — single integer picked from the always-live
                            **Spacing** dropdown: the number of electrodes
                            that lie *between* the active and the return
                            (0 = adjacent / immediate neighbour). Only
                            meaningful for multipolar kinds.
* include diagonal        — bool, when on diagonals at the same step
                            (Euclidean ``(spacing + 1) · √2``) qualify
                            as candidates alongside orthogonal cells.
* configuration kind      — Monopolar / Bipolar / Tripolar / Partial Bipolar
                            / Partial Tripolar / Partial Quadrupolar.

Output: a list of :class:`stimtest.electrode.Configuration` plus a
parallel checkbox for each that the user can untick to skip specific
combinations. ``selected_configurations()`` returns only the ticked
ones, in the order the experiment runner should iterate them.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

from PyQt6 import QtCore, QtWidgets

from ..electrode import Configuration, ElectrodeArray
from . import rich


# Configuration-mode labels (combo dropdown entries). String values are
# stable — they're saved to the prefs JSON.
KIND_MONO  = "Monopolar"
KIND_CG    = "Common Ground"
KIND_PCG   = "Partial Common Ground"
KIND_BP    = "Bipolar"
KIND_TP    = "Tripolar"
KIND_PBP   = "Partial Bipolar"
KIND_PTP   = "Partial Tripolar"

# How many *on-array* return electrodes each mode needs from the
# spacing-driven neighbour set. Common Ground takes ALL other
# channels and doesn't use spacing — it's handled as a special case
# in :meth:`_compute_combos`.
KIND_NEAR_RETS = {
    KIND_MONO: 0,
    KIND_BP: 1, KIND_PBP: 1,
    KIND_TP: 2, KIND_PTP: 2,
}
# Modes that require the off-array counter electrode (External Return) to
# be toggled on. Plain bipolar/tripolar use only on-array returns;
# Common Ground is on-array (all other channels) and works either way.
KIND_NEEDS_GLOBAL = {KIND_MONO, KIND_PBP, KIND_PTP, KIND_PCG}

# Mode dropdown contents per External-Return state. Common Ground is
# offered ONLY when the external return is OFF — its return path is
# every other on-array channel; mixing that with an external return
# is conceptually muddled (which one is the actual return?). The user
# spec is "exclude Common Ground when the external return is selected".
# Partial Common Ground stays in the External-on list because it
# explicitly combines the all-other-channels return with the external
# counter (that's its whole point).
KINDS_GLOBAL_ON  = [KIND_MONO, KIND_PBP, KIND_PTP, KIND_PCG]
KINDS_GLOBAL_OFF = [KIND_BP, KIND_TP, KIND_CG]


@dataclass
class _Combo:
    """One row in the combinations table."""
    config: Configuration
    enabled: bool = True


class CombinationPanel(QtWidgets.QGroupBox):
    combinationsChanged = QtCore.pyqtSignal(list)   # List[Configuration] (enabled only)
    # Emitted when the user hovers over a combo row in the list. Carries
    # ``(active, returns, spacing_label)`` so the channel grid can
    # paint highlight rings AND a double-headed spacing arrow stretched
    # between the active and each return, with ``spacing_label``
    # rendered in the middle of the line. The label is the integer
    # gap value the user picked in the always-live **Spacing** dropdown
    # (e.g. ``"0"`` for adjacent / immediate neighbours), and an empty
    # string for kinds with no spacing concept (Monopolar / CG / PCG).
    # ``active = -1`` means "clear highlight" (mouse left the list /
    # the empty list); pass an empty list / empty string in that case.
    combinationHovered = QtCore.pyqtSignal(int, list, str)

    # Spacing values shown in the dropdown — number of electrodes that
    # lie *between* the active and the return. 0 = adjacent (immediate
    # neighbour), 1 = one electrode between, 2 = two between. The
    # dropdown is always live; there is no separate enable toggle.
    # Internally the chosen value becomes a Euclidean grid distance of
    # ``spacing + 1`` for orthogonal returns; with "Include diagonal"
    # on, diagonal returns at the same integer step (Euclidean
    # ``(spacing + 1) * √2``) also qualify, per the MATLAB
    # ``getNeighbor.m`` convention.
    #
    # The trailing ``"All"`` entry (userData ``None``) disables the
    # distance filter entirely — every non-active on-array channel
    # becomes a candidate return, equivalent to the MATLAB
    # ``getNeighbor.m`` 'all' tag. Useful for sweeps where the user
    # wants every possible (active, return) pair without a
    # spacing-shape constraint.
    SPACINGS = (0, 1, 2)

    def __init__(self, single_mode: bool = False, parent=None,
                 multipolar_single: bool = False,
                 multipolar_no_repeat: bool = False):
        super().__init__("Configurations to run", parent)
        # Static "single-mode" hint from the owning tab (True for the
        # pulsing experiments). The *effective* single mode is dynamic:
        # when the user picks **Monopolar** in single-mode, multiple
        # actives are allowed (each runs as its own monopolar config),
        # because the PlexStim is naturally a multi-channel current
        # source. For other kinds (BP/TP/Partial-*/CG) only one combo
        # is run per session.
        self._static_single_mode = bool(single_mode)
        # ``multipolar_single`` forces single-combo selection for the
        # (partial) multipolar kinds (BP / TP / PBP / PTP) EVEN when the tab
        # is not statically single-mode — Long-Term Pulsing wants exactly one
        # multipolar combo per run (the return geometry is part of the test
        # definition; you can't chronically pulse conflicting return sets),
        # while keeping Monopolar multi-select for its simultaneous-channel
        # pulsing.  Operator (0.2.152): "only allow for one selection of
        # channel/combo under (partial) multipolar configuration."
        self._multipolar_single = bool(multipolar_single)
        # ``multipolar_no_repeat`` lets the (partial) multipolar kinds
        # (BP/TP/PBP/PTP) be MULTI-select but enforces that no channel is
        # reused as active OR return across the selected combos — Progressive
        # Stress wants to stress several non-overlapping multipolar combos
        # sequentially, without stressing any channel twice (operator, 0.2.152:
        # "PS … tests the selected channel/combo sequentially.  For multipolar,
        # a channel cannot be repeated as active or return").  Combos that
        # would reuse an already-selected channel are greyed out.  Mutually
        # exclusive with ``multipolar_single`` (no tab sets both).
        self._multipolar_no_repeat = bool(multipolar_no_repeat)
        self._single_mode = self._static_single_mode
        self._array: ElectrodeArray | None = None
        self._actives: List[int] = []
        # The Channel Selector defaults External Return to *on*, so init
        # this side as on too — that way the first dropdown populate
        # uses the ON list (which starts with Monopolar) instead of the
        # OFF list and being twin-swapped to PBP later.
        self._global_return: bool = True
        self._combos: List[_Combo] = []

        # ----- controls -----
        # Items populated by :meth:`_refresh_kind_dropdown` based on the
        # current Global-Return state — kinds that *need* the global
        # return only appear when it's toggled on, kinds that don't only
        # appear when it's off, and Common Ground appears in both lists.
        # The actual population happens at the end of __init__ once the
        # spacing widgets exist (the on-kind-changed callback needs them).
        self.kind_combo = QtWidgets.QComboBox()
        self.kind_combo.currentTextChanged.connect(self._on_kind_changed)
        self.kind_combo.setToolTip(
            "Electrode configuration kind.<br><br>"
            "<b>Monopolar</b> — single active channel, current "
            "returns through the system ground.<br>"
            "<b>Bipolar</b> — active + one return electrode "
            "(pairs).<br>"
            "<b>Tripolar</b> — active + two returns (the second-"
            "return pair flanks the active when 'Flanking only' "
            "is on).<br>"
            "<b>Partial Tripolar</b> — fractional current "
            "splitting between two returns.<br>"
            "<b>Common Ground</b> — every other channel ties to "
            "ground; the active drives against all of them.")

        # Spacing — always-live dropdown (no enable toggle). 0 means
        # adjacent (immediate neighbours, Euclidean distance 1); 1 means
        # one electrode between (distance 2); etc. With "Include
        # diagonal" on, diagonal returns at the same integer step
        # (Euclidean (spacing + 1) · √2) also qualify, per the MATLAB
        # ``getNeighbor.m`` convention.
        self.spacing_combo = QtWidgets.QComboBox()
        for s in self.SPACINGS:
            label = f"{s} (adjacent)" if s == 0 else str(s)
            self.spacing_combo.addItem(label, userData=s)
        # "All" sentinel — userData None disables the distance filter
        # so every non-active channel is a candidate. Pairs with the
        # MATLAB 'all' tag in getNeighbor.m. Listed last so the
        # numeric spacings are the primary visual entries.
        self.spacing_combo.addItem("All (every channel)", userData=None)
        self.spacing_combo.setCurrentIndex(0)
        self.spacing_combo.setToolTip(
            "Number of electrodes that lie between the active and the "
            "return electrode. 0 = adjacent (immediate neighbours). "
            "“All” disables the distance filter — every non-active "
            "channel is a candidate return.")
        self.spacing_combo.currentTextChanged.connect(self._rebuild)

        # ----- candidate-filter checkboxes -----
        # "Include diagonal" — when on, diagonal cells at the picked
        # spacing N (Euclidean distance N·√2) qualify alongside
        # orthogonal cells (distance N). Off = orthogonal-only.
        # Hidden on linear arrays (no second dimension) and on
        # hexagonal layouts (every immediate neighbour is equidistant
        # so the distinction is meaningless).
        self.include_diagonal = QtWidgets.QCheckBox("Include diagonal")
        self.include_diagonal.setChecked(True)
        self.include_diagonal.setToolTip(
            "Treat diagonal returns as one step away. With this on, "
            "spacing N matches both orthogonal cells (distance N) and "
            "diagonal cells (distance N·√2).")
        self.include_diagonal.toggled.connect(self._rebuild)
        # Tripolar / Partial Tripolar only — both returns must flank the
        # active electrode (active is the midpoint of the return pair).
        self.flanking_only = QtWidgets.QCheckBox("Flanking only")
        self.flanking_only.setChecked(True)
        self.flanking_only.toggled.connect(self._rebuild)
        self.flanking_only.setToolTip(
            "Tripolar / Partial-Tripolar only. When on, the two "
            "returns must flank the active (active is the midpoint "
            "of the return pair). When off, any pair of returns at "
            "the configured spacing qualifies — useful for asymmetric "
            "configurations where one return is closer than the other.")

        self.select_all = QtWidgets.QCheckBox("All")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._on_select_all_toggled)
        self.select_all.setToolTip(
            "Tick / untick every combination in the candidate list "
            "below. Only meaningful when multiple configurations "
            "can be queued for a single Start (Long-Term Pulsing, "
            "Progressive Stress — VT runs one combo at a time).")
        # The "All" affordance is meaningless in single-combo mode.
        self.select_all.setVisible(not self._single_mode)

        self.combos_list = QtWidgets.QListWidget()
        self.combos_list.itemChanged.connect(self._on_item_changed)
        # Hover-highlight: itemEntered fires only when mouse-tracking is
        # on at the viewport level. We also want to clear the highlight
        # when the mouse leaves the list — install an event filter for
        # the QEvent.Leave we won't get from the list itself.
        self.combos_list.setMouseTracking(True)
        self.combos_list.viewport().setMouseTracking(True)
        self.combos_list.itemEntered.connect(self._on_item_entered)
        self.combos_list.viewport().installEventFilter(self)

        self.summary = QtWidgets.QLabel()
        self.summary.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.summary.setStyleSheet("color: #555; padding: 2px;")

        # ----- layout -----
        v = QtWidgets.QVBoxLayout(self)
        kind_row = QtWidgets.QHBoxLayout()
        kind_row.addWidget(QtWidgets.QLabel("Mode:"))
        kind_row.addWidget(self.kind_combo, stretch=1)
        v.addLayout(kind_row)

        # Spacing row — always-live dropdown labelled "Spacing:".
        spacing_row = QtWidgets.QHBoxLayout()
        spacing_row.addWidget(QtWidgets.QLabel("Spacing:"))
        spacing_row.addWidget(self.spacing_combo)
        spacing_row.addStretch(1)
        self.spacing_widget = QtWidgets.QWidget()
        self.spacing_widget.setLayout(spacing_row)
        v.addWidget(self.spacing_widget)

        # Filter row — Include diagonal + Flanking only. Hidden as a
        # whole whenever the mode doesn't use neighbour returns
        # (Monopolar, Common Ground); within it, each checkbox is
        # gated by array geometry / kind (see
        # :meth:`_refresh_widget_visibility`).
        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(self.include_diagonal)
        filter_row.addWidget(self.flanking_only)
        filter_row.addStretch(1)
        self.filter_widget = QtWidgets.QWidget()
        self.filter_widget.setLayout(filter_row)
        v.addWidget(self.filter_widget)

        v.addWidget(self.summary)
        list_row = QtWidgets.QHBoxLayout()
        list_row.addWidget(self.combos_list, stretch=1)
        side = QtWidgets.QVBoxLayout()
        side.addWidget(self.select_all)
        side.addStretch(1)
        list_row.addLayout(side)
        v.addLayout(list_row, stretch=1)

        # Initial state — populates the kind dropdown based on the
        # default Global-Return = off and triggers _on_kind_changed
        # so the spacing visibility is correct on first paint.
        self._refresh_kind_dropdown()

    # ----------------------------------------------------------- public API
    def set_array(self, array: ElectrodeArray):
        self._array = array
        # Re-evaluate diagonal/flanking visibility — a 4×4 → linear
        # array swap should immediately hide those rows.
        self._on_kind_changed(self._kind())
        self._rebuild()

    def set_actives(self, actives: List[int]):
        self._actives = list(actives)
        self._rebuild()

    def set_global_return(self, on: bool):
        prev_on = self._global_return
        self._global_return = bool(on)
        # When the user toggles External Return, slide the current
        # mode between its with-/without-counter twins so the
        # experiment they were configuring keeps its meaning:
        #   Bipolar       ↔ Partial Bipolar
        #   Tripolar      ↔ Partial Tripolar
        #   Common Ground ↔ Partial Common Ground
        # (Monopolar has no twin — it's already global-only, so
        # toggling External off falls back to the dropdown's first
        # external-off entry, Bipolar.)
        twin = {
            KIND_BP:  KIND_PBP, KIND_PBP: KIND_BP,
            KIND_TP:  KIND_PTP, KIND_PTP: KIND_TP,
            KIND_CG:  KIND_PCG, KIND_PCG: KIND_CG,
        }
        prev_kind = self._kind()
        target_kind = twin.get(prev_kind)
        # The dropdown's available kinds depend on this state — refresh
        # before rebuilding combos so the user always sees a consistent
        # kind / spacing visibility for the current External-Return
        # state. May implicitly change the selected kind if the prior
        # one isn't in the new list (e.g. CG → MP when External on).
        self._refresh_kind_dropdown()
        # Apply the twin-swap if one exists and is in the new kind list.
        if target_kind is not None:
            idx = self.kind_combo.findText(target_kind)
            if idx >= 0:
                self.kind_combo.blockSignals(True)
                try:
                    self.kind_combo.setCurrentIndex(idx)
                finally:
                    self.kind_combo.blockSignals(False)
        # If the kind has changed (twin swap OR dropdown fallback),
        # rerun the kind-changed handler so spacing / filter widget
        # visibility is consistent with the new kind. Idempotent when
        # the kind didn't change.
        new_kind = self._kind()
        if new_kind != prev_kind:
            self._on_kind_changed(new_kind)
        self._rebuild()

    def _refresh_kind_dropdown(self):
        """Populate the kind combobox with the kinds valid for the
        current Global-Return state, preserving the user's selection
        when it's still in the new list."""
        available = (KINDS_GLOBAL_ON if self._global_return else KINDS_GLOBAL_OFF)
        prev = self.kind_combo.currentText()
        self.kind_combo.blockSignals(True)
        try:
            self.kind_combo.clear()
            for k in available:
                self.kind_combo.addItem(k)
            if prev in available:
                self.kind_combo.setCurrentText(prev)
            else:
                self.kind_combo.setCurrentIndex(0)
        finally:
            self.kind_combo.blockSignals(False)
        self._on_kind_changed(self.kind_combo.currentText())

    def selected_configurations(self) -> List[Configuration]:
        return [c.config for c in self._combos if c.enabled]

    # --------------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        """Snapshot the user-editable controls so they survive a GUI
        restart.

        Captures the *mode* (kind dropdown), the spacing dropdown
        value (integer gap, or ``None`` for the "All" sentinel), and
        the two filter toggles. Channel selection / actives /
        external-return state live on the channel grid and are
        persisted there — those depend on the current array geometry
        and are restored separately.
        """
        return {
            "kind": self.kind_combo.currentText(),
            # ``currentData`` returns the userData (int gap or None
            # for "All"). Stored directly so the round-trip preserves
            # the All sentinel without ambiguous string parsing.
            "spacing_gap": self.spacing_combo.currentData(),
            "include_diagonal": bool(self.include_diagonal.isChecked()),
            "flanking_only": bool(self.flanking_only.isChecked()),
        }

    def restore_prefs(self, p: dict) -> None:
        """Apply a saved prefs dict back into the controls.

        Defensive on every entry: a saved kind that's no longer in
        the current dropdown (e.g. user toggled External Return so
        the available kinds changed) is silently ignored — the
        dropdown stays on whatever default :meth:`_refresh_kind_dropdown`
        picked. Same for an out-of-range spacing gap.
        """
        if not isinstance(p, dict) or not p:
            return
        # Mode / kind — only apply if it's still a valid choice.
        kind = p.get("kind")
        if isinstance(kind, str) and kind:
            idx = self.kind_combo.findText(kind)
            if idx >= 0:
                self.kind_combo.blockSignals(True)
                try:
                    self.kind_combo.setCurrentIndex(idx)
                finally:
                    self.kind_combo.blockSignals(False)
                self._on_kind_changed(kind)
        # Spacing — match by userData so the All sentinel (None)
        # round-trips without string-parsing.
        if "spacing_gap" in p:
            gap = p["spacing_gap"]
            idx = self.spacing_combo.findData(gap)
            if idx >= 0:
                self.spacing_combo.blockSignals(True)
                try:
                    self.spacing_combo.setCurrentIndex(idx)
                finally:
                    self.spacing_combo.blockSignals(False)
        # Filter toggles.
        if "include_diagonal" in p:
            try:
                self.include_diagonal.setChecked(bool(p["include_diagonal"]))
            except (TypeError, ValueError):
                pass
        if "flanking_only" in p:
            try:
                self.flanking_only.setChecked(bool(p["flanking_only"]))
            except (TypeError, ValueError):
                pass
        self._rebuild()

    # ----------------------------------------------------------- internal
    def _spacings(self) -> Set[int]:
        """Set with the single integer Euclidean grid distance (N) the
        candidate filter is currently looking for.

        With the always-live spacing dropdown, this is just ``{gap + 1}``
        where *gap* is the user-picked number-of-electrodes-between
        value: ``0 (adjacent) → {1}``, ``1 → {2}``, ``2 → {3}``.
        Returns the empty set when the dropdown is on **All** (userData
        ``None``) — the candidate filter then accepts every non-active
        channel, equivalent to MATLAB ``getNeighbor.m`` 'all' tag.
        """
        gap = self.spacing_combo.currentData()
        if gap is None:
            return set()
        try:
            return {int(gap) + 1}
        except (TypeError, ValueError):
            return set()

    def _valid_distances(self) -> Optional[List[float]]:
        """Euclidean grid distances that qualify as candidate neighbours.

        Mirrors the MATLAB ``getNeighbor.m`` convention:

        * 'side' tag → matches ``N`` only (orthogonal)
        * 'diag' tag → matches ``N · √2`` only (diagonal corners)
        * 'adj'  tag → matches both ``{N, N · √2}``

        The GUI's "Include diagonal" toggle picks between *side* (off)
        and *adj* (on), with N derived directly from the spacing
        dropdown (``gap + 1``).

        On hexagonal/triangular layouts the six immediate neighbours
        are equidistant — there's no orthogonal-vs-diagonal distinction
        to express — so the toggle is hidden and treated as
        effectively on (always 'adj'). On rectangular layouts the
        widget state is honoured.
        """
        spacings = self._spacings()
        if not spacings:
            return None
        N = float(next(iter(spacings)))
        diag = self.include_diagonal.isChecked() or self._is_hex_array()
        if diag:
            return [N, N * math.sqrt(2)]
        return [N]

    def _kind(self) -> str:
        return self.kind_combo.currentText()

    def _is_linear_array(self) -> bool:
        """True when the loaded array is 1-D (single row OR single column)."""
        return self._array is not None and (
            self._array.rows == 1 or self._array.cols == 1)

    def _is_hex_array(self) -> bool:
        """True when the loaded array is laid out on a hexagonal/triangular
        lattice (e.g. MicroProbes FMA, or a custom device the user marked
        as hexagonal). The orthogonal-vs-diagonal distinction is
        meaningless here — every immediate neighbour is equidistant —
        so the "Include diagonal" toggle is hidden and treated as
        effectively on for the candidate filter.
        """
        return (self._array is not None
                and getattr(self._array, "layout", "rect") == "triangular")

    def _refresh_widget_visibility(self):
        """Refresh visibility of spacing/filter widgets based on the
        current kind and the array geometry.

        Centralised so the kind-change handler can keep the row
        visibility consistent across Mode flips. With the always-live
        spacing dropdown there are no toggle states to track here.
        """
        kind = self._kind()
        # Spacing + filter rows only apply when on-array neighbour
        # returns are needed. Common Ground and Monopolar use neither.
        needs_neighbours = KIND_NEAR_RETS.get(kind, 0) > 0
        self.spacing_widget.setVisible(needs_neighbours)
        self.filter_widget.setVisible(needs_neighbours)
        # Linear arrays don't have a second dimension, so "Include
        # diagonal" doesn't apply — hide it. Hexagonal layouts have six
        # equidistant immediate neighbours, so the orthogonal-vs-
        # diagonal distinction is meaningless there too — hide the
        # toggle and let :meth:`_valid_distances` treat the layout as
        # if diagonals were always on.
        is_linear = self._is_linear_array()
        is_hex = self._is_hex_array()
        self.include_diagonal.setVisible(not is_linear and not is_hex)
        # Flanking-only is meaningful for tripolar variants only.
        # Hidden on linear arrays since "flanking" through a 1-D strip
        # collapses to "the two cells on either side" — there's no
        # geometric choice to make.
        is_tripolar = kind in (KIND_TP, KIND_PTP)
        self.flanking_only.setVisible(is_tripolar and not is_linear)

    def _on_kind_changed(self, kind: str):
        self._refresh_widget_visibility()
        # Static single-mode (pulsing experiments + Progressive Stress)
        # is relaxed for the *single-active* kinds: Monopolar, Common
        # Ground, and Partial Common Ground. Each combination there
        # exercises a different active electrode, so the runner can
        # queue them sequentially. Multipolar kinds (BP / TP / PBP /
        # PTP) stay single-combo because the choice of return geometry
        # is part of the test definition — running several at once
        # would just bury the user.
        prev_single = self._single_mode
        _is_multipolar = kind in (KIND_BP, KIND_TP, KIND_PBP, KIND_PTP)
        if _is_multipolar and self._multipolar_no_repeat:
            # PS: multiple NON-OVERLAPPING multipolar combos (multi-select; the
            # no-repeat constraint greys out any channel-sharing combo).
            self._single_mode = False
        elif _is_multipolar and self._multipolar_single:
            # LP: exactly one multipolar combo (return geometry is the test).
            self._single_mode = True
        else:
            # Static single-mode (SP/CP) → single for multipolar; the
            # single-active kinds (Monopolar / Common Ground / Partial CG) are
            # always multi (each combo exercises a different active).
            self._single_mode = (
                self._static_single_mode
                and kind not in (KIND_MONO, KIND_CG, KIND_PCG)
            )
        self.select_all.setVisible(not self._single_mode)
        # If the relaxation just toggled, force a rebuild to reset
        # the default check-state of the combo rows.
        if prev_single != self._single_mode:
            pass    # _rebuild below will pick the new defaults
        self._rebuild()

    def _rebuild(self):
        self._combos = self._compute_combos()
        # In single-combo mode only the first combo is enabled by default;
        # the user explicitly picks which single combination to run.  The
        # no-repeat multipolar mode (PS) ALSO defaults to just the first combo
        # — the rest would mostly conflict, and the user adds channel-disjoint
        # combos one at a time (the greying guides them).
        if (self._single_mode or self._no_repeat_active()) and self._combos:
            for i, c in enumerate(self._combos):
                c.enabled = (i == 0)
        self.combos_list.blockSignals(True)
        try:
            self.combos_list.clear()
            for combo in self._combos:
                item = QtWidgets.QListWidgetItem(self._format_combo_label(combo.config))
                item.setFlags(item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(QtCore.Qt.CheckState.Checked
                                   if combo.enabled
                                   else QtCore.Qt.CheckState.Unchecked)
                self.combos_list.addItem(item)
        finally:
            self.combos_list.blockSignals(False)
        # Grey out channel-sharing combos for the PS no-repeat mode.
        self._refresh_no_repeat_availability()
        self._update_summary()
        self.combinationsChanged.emit(self.selected_configurations())

    def _no_repeat_active(self) -> bool:
        """True when the PS no-repeat constraint applies — multipolar_no_repeat
        set AND the current kind is a (partial) multipolar kind."""
        return (self._multipolar_no_repeat
                and self._kind() in (KIND_BP, KIND_TP, KIND_PBP, KIND_PTP))

    @staticmethod
    def _combo_channels(cfg) -> Set[int]:
        """Every channel a combo touches — the active plus all returns."""
        return {int(cfg.active)} | {int(r) for r in cfg.returns}

    def _refresh_no_repeat_availability(self):
        """PS no-repeat: grey out any UNSELECTED combo that shares a channel
        (active OR return) with a SELECTED combo, so no channel is stressed
        twice.  No-op unless ``_no_repeat_active()``.  Selected combos stay
        enabled; a combo that no longer conflicts (because the user deselected
        the combo it clashed with) is re-enabled."""
        if not self._no_repeat_active():
            return
        used: Set[int] = set()
        for c in self._combos:
            if c.enabled:
                used |= self._combo_channels(c.config)
        self.combos_list.blockSignals(True)
        try:
            for i, c in enumerate(self._combos):
                item = self.combos_list.item(i)
                if item is None:
                    continue
                if c.enabled:
                    item.setFlags(item.flags()
                                  | QtCore.Qt.ItemFlag.ItemIsEnabled
                                  | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                    continue
                conflict = bool(self._combo_channels(c.config) & used)
                if conflict:
                    if item.checkState() != QtCore.Qt.CheckState.Unchecked:
                        item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                    item.setFlags(item.flags()
                                  & ~QtCore.Qt.ItemFlag.ItemIsEnabled)
                else:
                    item.setFlags(item.flags()
                                  | QtCore.Qt.ItemFlag.ItemIsEnabled
                                  | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        finally:
            self.combos_list.blockSignals(False)

    def _update_summary(self):
        n_total = len(self._combos)
        n_on = sum(1 for c in self._combos if c.enabled)
        kind = self._kind()
        bits = [f"<b>{n_on}</b>/{n_total} combinations selected", kind]
        if KIND_NEAR_RETS.get(kind, 0) > 0:
            # Show the user-facing gap value (matches the dropdown)
            # rather than the internal Euclidean distance — "0" reads
            # as "adjacent" to the user even though the candidate
            # filter looks for distance 1.
            gap = self.spacing_combo.currentData()
            if gap is None:
                bits.append("spacing: <i>all</i>")
            else:
                try:
                    gap_str = str(int(gap))
                except (TypeError, ValueError):
                    gap_str = "?"
                label = f"{gap_str} (adjacent)" if gap_str == "0" else gap_str
                bits.append(f"spacing: {label}")
        if kind in KIND_NEEDS_GLOBAL:
            bits.append("External Return: " +
                        ("<b>on</b>" if self._global_return
                         else "<span style='color:#c62828;'><b>missing</b></span>"))
        self.summary.setText(" &nbsp;·&nbsp; ".join(bits))

    def _compute_combos(self) -> List[_Combo]:
        if self._array is None or not self._actives:
            return []
        kind = self._kind()
        n_returns = KIND_NEAR_RETS.get(kind, 0)
        # Validation gate: if the kind needs the global return and the
        # user hasn't toggled it on, skip combo generation. Summary will
        # surface the reason in red.
        if kind in KIND_NEEDS_GLOBAL and not self._global_return:
            return []
        out: List[_Combo] = []
        seen: Set[Tuple[int, Tuple[int, ...]]] = set()
        for active in self._actives:
            if kind == KIND_MONO:
                # Monopolar: active alone (Configuration.monopolar implies
                # the off-array counter is the implicit return).
                cfg = Configuration.monopolar(active)
                key = (active, ())
                if key in seen: continue
                seen.add(key)
                out.append(_Combo(cfg))
                continue
            if kind in (KIND_CG, KIND_PCG):
                # Common ground: every other on-array channel acts as
                # a return regardless of spacing. Partial CG also
                # implicitly adds the off-array counter (encoded by
                # the kind id; the runner reads it).
                others = tuple(sorted(n for n in self._array.channel_numbers
                                       if n != active))
                key = (kind, active, others)
                if key in seen: continue
                seen.add(key)
                cfg_id = "PCG" if kind == KIND_PCG else "CG"
                cfg = Configuration(id=cfg_id, active=active, returns=others)
                out.append(_Combo(cfg))
                continue
            # Otherwise we need on-array neighbour returns picked by spacing.
            from itertools import combinations
            candidates = self._neighbours_at_spacing(active)
            is_tripolar = kind in (KIND_TP, KIND_PTP)
            # Flanking-only constrains tripolar combos so the active
            # sits at the midpoint of the return pair (sum of offsets
            # is zero in both axes).
            flank_only = self.flanking_only.isChecked() and is_tripolar
            ref = self._array[active]
            for ret_set in combinations(candidates, n_returns):
                if flank_only:
                    # Tripolar flanking: the two returns must lie on
                    # opposite sides of the active so the active is the
                    # midpoint of the pair (sum of offsets == 0).
                    s1 = self._array[ret_set[0]]
                    s2 = self._array[ret_set[1]]
                    if (s1.row + s2.row) != 2 * ref.row \
                       or (s1.col + s2.col) != 2 * ref.col:
                        continue
                key = (active, tuple(sorted(ret_set)))
                if key in seen: continue
                seen.add(key)
                cfg = Configuration.from_active_returns(active, ret_set)
                # Re-tag the kind id for clarity in the display name —
                # the from_active_returns helper picks BP/TP based on
                # |returns|, which doesn't distinguish full vs partial.
                cfg = Configuration(
                    id={KIND_BP: "BP", KIND_TP: "TP",
                        KIND_PBP: "PBP", KIND_PTP: "PTP"}.get(kind, cfg.id),
                    active=active, returns=cfg.returns,
                )
                out.append(_Combo(cfg))
        return out

    def _neighbours_at_spacing(self, active: int) -> List[int]:
        """Channels matching the active candidate-filter rules.

        Distances are Euclidean on the row/col grid (matching the
        MATLAB ``getNeighbor.m`` convention: ``norm(stimXY-checkXY)``).
        The candidate set is the union of cells at the picked Euclidean
        distance(s):

        * **Neither toggle on** — no distance filter; every non-active
          channel is a candidate (equivalent to the MATLAB 'all' tag).
        * **Neighbors only, no diagonal** — distance ``1`` (orthogonal
          immediate neighbours; MATLAB 'side' at N=1).
        * **Neighbors only, with diagonal** — distance ∈ ``{1, √2}``;
          the four diagonal neighbours are treated as one step away
          (MATLAB 'adj' at N=1).
        * **Apply spacing N, no diagonal** — distance ``N`` (orthogonal;
          MATLAB 'side' at N).
        * **Apply spacing N, with diagonal** — distance ∈ ``{N, N·√2}``
          (MATLAB 'adj' at N).

        Triangular and other non-rectangular layouts use the same
        row/col coordinates the user authored in the device-mapping
        table, so spacing semantics stay consistent across array
        geometries — nothing here is specific to a 4×4 or any other
        particular arrangement.
        """
        if self._array is None: return []
        try:
            ref = self._array[active]
        except KeyError:
            return []
        valid = self._valid_distances()       # None ⇒ no distance filter
        out: List[int] = []
        tol = 1e-6
        for s in self._array.sites:
            if s.number == active: continue
            if valid is None:
                out.append(s.number)
                continue
            dr = float(s.row - ref.row)
            dc = float(s.col - ref.col)
            d = math.sqrt(dr * dr + dc * dc)
            if any(abs(d - vd) <= tol for vd in valid):
                out.append(s.number)
        return out

    # --------- list-widget interactions ---------
    def _format_combo_label(self, cfg: Configuration) -> str:
        """Spelled-out label for the combinations list.

        ``Configuration.display_name()`` produces a compact form like
        ``"CH05 v 06,09"`` that's good for plot titles but cryptic in a
        dense list. This formatter expands each combo into a sentence
        that names the active channel and its returns, joined by
        "versus". The mode prefix is omitted because it's already
        shown in the *Mode:* dropdown above the list.
        """
        active = cfg.active
        rets = list(cfg.returns)
        kind_id = cfg.id

        if kind_id == "MP":
            return f"Channel {active} (active) versus External return"
        if kind_id == "CG":
            return (f"Channel {active} (active) versus "
                    f"all other on-array channels (return)")
        if kind_id == "PCG":
            return (f"Channel {active} (active) versus "
                    f"all other on-array channels (return) + External return")
        if kind_id == "BP":
            return (f"Channel {active} (active) versus "
                    f"Channel {rets[0]} (return)")
        if kind_id == "TP":
            return (f"Channel {active} (active) versus "
                    f"Channels {rets[0]} and {rets[1]} (return)")
        if kind_id == "PBP":
            return (f"Channel {active} (active) versus "
                    f"Channel {rets[0]} (return) + External return")
        if kind_id == "PTP":
            return (f"Channel {active} (active) versus "
                    f"Channels {rets[0]} and {rets[1]} (return) + External return")
        # Fallback for any future kind
        if not rets:
            return f"Channel {active} (active)"
        rets_str = " and ".join(str(r) for r in rets)
        return f"Channel {active} (active) versus Channels {rets_str} (return)"

    def _on_item_changed(self, item: QtWidgets.QListWidgetItem):
        idx = self.combos_list.row(item)
        if not (0 <= idx < len(self._combos)):
            return
        new_state = (item.checkState() == QtCore.Qt.CheckState.Checked)
        self._combos[idx].enabled = new_state
        # Single-combo mode: checking one row unchecks every other row,
        # so only one combination is ever selected at a time.
        if self._single_mode and new_state:
            self.combos_list.blockSignals(True)
            try:
                for i in range(self.combos_list.count()):
                    if i == idx: continue
                    other = self.combos_list.item(i)
                    if other.checkState() != QtCore.Qt.CheckState.Unchecked:
                        other.setCheckState(QtCore.Qt.CheckState.Unchecked)
                    if i < len(self._combos):
                        self._combos[i].enabled = False
            finally:
                self.combos_list.blockSignals(False)
        # PS no-repeat: re-grey / re-enable combos after this change so the
        # channel-disjoint invariant holds (checking one locks its channels;
        # unchecking frees them).
        self._refresh_no_repeat_availability()
        self._update_summary()
        self.combinationsChanged.emit(self.selected_configurations())

    def _on_item_entered(self, item: QtWidgets.QListWidgetItem):
        """Mouse moved over a combo row — emit hover signal.

        Bundles a spacing label alongside the specific (active,
        returns) so the channel grid can stretch a double-headed
        arrow between active and each return, with the value drawn
        in the middle of the line. The label is the integer gap
        value the user picked in the **Spacing** dropdown ("0" =
        adjacent / immediate neighbour), or empty for kinds with no
        spacing concept (Monopolar / Common Ground / Partial CG).
        """
        idx = self.combos_list.row(item)
        if not (0 <= idx < len(self._combos)):
            return
        cfg = self._combos[idx].config
        active = int(cfg.active)
        returns = list(cfg.returns)
        spacing_label = ""
        # Show the arrow whenever the kind actually uses on-array
        # neighbour returns (BP / TP / PBP / PTP). Monopolar / CG / PCG
        # have no spacing concept so the label stays empty there.
        if KIND_NEAR_RETS.get(self._kind(), 0) > 0:
            gap = self.spacing_combo.currentData()
            try:
                spacing_label = str(int(gap))
            except (TypeError, ValueError):
                spacing_label = ""
        self.combinationHovered.emit(active, returns, spacing_label)

    def eventFilter(self, obj, ev):
        """Clear highlight when the mouse leaves the combos list viewport."""
        if obj is self.combos_list.viewport() and ev.type() == QtCore.QEvent.Type.Leave:
            self.combinationHovered.emit(-1, [], "")
        return super().eventFilter(obj, ev)

    def _on_select_all_toggled(self, checked: bool):
        if self._no_repeat_active():
            # "Select all" can't mean literally every combo here (they'd share
            # channels).  Checked → GREEDY maximal channel-disjoint set (take
            # combos in list order, skipping any that reuse a channel already
            # taken); unchecked → clear.
            used: Set[int] = set()
            self.combos_list.blockSignals(True)
            try:
                for i, c in enumerate(self._combos):
                    item = self.combos_list.item(i)
                    chans = self._combo_channels(c.config)
                    take = checked and not (chans & used)
                    c.enabled = take
                    if item is not None:
                        item.setCheckState(QtCore.Qt.CheckState.Checked if take
                                           else QtCore.Qt.CheckState.Unchecked)
                    if take:
                        used |= chans
            finally:
                self.combos_list.blockSignals(False)
            self._refresh_no_repeat_availability()
            self._update_summary()
            self.combinationsChanged.emit(self.selected_configurations())
            return
        self.combos_list.blockSignals(True)
        try:
            state = (QtCore.Qt.CheckState.Checked if checked
                     else QtCore.Qt.CheckState.Unchecked)
            for i in range(self.combos_list.count()):
                self.combos_list.item(i).setCheckState(state)
            for c in self._combos:
                c.enabled = checked
        finally:
            self.combos_list.blockSignals(False)
        self._update_summary()
        self.combinationsChanged.emit(self.selected_configurations())
