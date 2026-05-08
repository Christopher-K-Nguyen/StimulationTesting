"""Combination panel — lists the (active, returns) test combos to run.

Inputs:
* actives                 — channel numbers selected in the grid
* global return           — bool, True when the off-array counter electrode
                            is in the circuit (toggled via the Global Return
                            square next to the channel grid). Required for
                            Monopolar and any "Partial …" kind.
* spacing                 — set of integer cell offsets the user toggled
                            (+1 / +2 / +3); each offset means "Nth-nearest
                            neighbour of the active in row/col distance".
                            Only meaningful for multipolar kinds.
* configuration kind      — Monopolar / Bipolar / Tripolar / Partial Bipolar
                            / Partial Tripolar / Partial Quadrupolar.

Output: a list of :class:`stimtest.electrode.Configuration` plus a
parallel checkbox for each that the user can untick to skip specific
combinations. ``selected_configurations()`` returns only the ticked
ones, in the order the experiment runner should iterate them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set, Tuple

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
# Modes that require the off-array counter electrode (Global Return) to
# be toggled on. Plain bipolar/tripolar use only on-array returns;
# Common Ground is on-array (all other channels) and works either way.
KIND_NEEDS_GLOBAL = {KIND_MONO, KIND_PBP, KIND_PTP, KIND_PCG}

# Mode dropdown contents per Global-Return state. Common Ground is in
# both because it doesn't depend on the off-array counter — the user's
# rule was "always show common ground". Partial Common Ground adds the
# off-array counter on top of CG's all-other-channels return set.
KINDS_GLOBAL_ON  = [KIND_MONO, KIND_PBP, KIND_PTP, KIND_PCG, KIND_CG]
KINDS_GLOBAL_OFF = [KIND_BP, KIND_TP, KIND_CG]


@dataclass
class _Combo:
    """One row in the combinations table."""
    config: Configuration
    enabled: bool = True


class CombinationPanel(QtWidgets.QGroupBox):
    combinationsChanged = QtCore.pyqtSignal(list)   # List[Configuration] (enabled only)
    # Emitted when the user hovers over a combo row in the list. Carries
    # the (active, returns) of that combo so the channel grid can paint
    # highlight rings. ``active = -1`` means "clear highlight" (mouse
    # left the list / the empty list).
    combinationHovered = QtCore.pyqtSignal(int, list)

    # Spacing values shown in the dropdown — number of electrodes that
    # lie *between* the active and the return. 0 = adjacent (no gap),
    # 1 = one electrode between, 2 = two between, etc. Internally this
    # is converted to a Chebyshev grid distance of ``spacing + 1`` for
    # the candidate-filter pipeline.
    SPACINGS = (0, 1, 2)

    def __init__(self, single_mode: bool = False, parent=None):
        super().__init__("Configurations to run", parent)
        # Static "single-mode" hint from the owning tab (True for the
        # pulsing experiments). The *effective* single mode is dynamic:
        # when the user picks **Monopolar** in single-mode, multiple
        # actives are allowed (each runs as its own monopolar config),
        # because the PlexStim is naturally a multi-channel current
        # source. For other kinds (BP/TP/Partial-*/CG) only one combo
        # is run per session.
        self._static_single_mode = bool(single_mode)
        self._single_mode = self._static_single_mode
        self._array: ElectrodeArray | None = None
        self._actives: List[int] = []
        # The Channel Selector defaults Global Return to *on*, so init
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

        # Spacing — single value chosen from a dropdown, gated by an
        # "Apply spacing" toggle. Off = no spacing filter (all
        # non-active channels are eligible, modulo the neighbours-only
        # filters); on = use the dropdown value as the Chebyshev
        # distance from the active.
        self.apply_spacing_check = QtWidgets.QCheckBox("Apply spacing")
        # Default off — most users don't want a spacing constraint until
        # they explicitly opt in.
        self.apply_spacing_check.setChecked(False)
        self.apply_spacing_check.toggled.connect(self._on_apply_spacing_toggled)
        self.spacing_combo = QtWidgets.QComboBox()
        for s in self.SPACINGS:
            label = f"{s} (adjacent)" if s == 0 else str(s)
            self.spacing_combo.addItem(label, userData=s)
        self.spacing_combo.setCurrentIndex(0)
        self.spacing_combo.setToolTip(
            "Number of electrodes that lie between the active and the "
            "return electrode. 0 = adjacent.")
        self.spacing_combo.currentTextChanged.connect(self._rebuild)

        # ----- candidate-filter checkboxes -----
        # "Neighbors only" restricts return candidates to topological
        # neighbours of the active electrode (orthogonal + optionally
        # diagonal). When off, the spacing distance alone gates which
        # channels are eligible.
        self.neighbors_only = QtWidgets.QCheckBox("Neighbors only")
        self.neighbors_only.setChecked(True)
        self.neighbors_only.toggled.connect(self._on_neighbors_only_toggled)
        # Active when "Neighbors only" is on — controls whether diagonal
        # neighbours (Chebyshev distance 1, but Manhattan 2) count as
        # neighbours. Off = orthogonal-only (4-connected).
        self.include_diagonal = QtWidgets.QCheckBox("Include diagonal")
        self.include_diagonal.setChecked(True)
        self.include_diagonal.toggled.connect(self._rebuild)
        # Tripolar / Partial Tripolar only — both returns must flank the
        # active electrode (active is the midpoint of the return pair).
        self.flanking_only = QtWidgets.QCheckBox("Flanking only")
        self.flanking_only.setChecked(True)
        self.flanking_only.toggled.connect(self._rebuild)

        self.select_all = QtWidgets.QCheckBox("All")
        self.select_all.setChecked(True)
        self.select_all.toggled.connect(self._on_select_all_toggled)
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

        # Spacing row — toggle + dropdown.
        spacing_row = QtWidgets.QHBoxLayout()
        spacing_row.addWidget(self.apply_spacing_check)
        spacing_row.addWidget(QtWidgets.QLabel("Distance:"))
        spacing_row.addWidget(self.spacing_combo)
        spacing_row.addStretch(1)
        self.spacing_widget = QtWidgets.QWidget()
        self.spacing_widget.setLayout(spacing_row)
        v.addWidget(self.spacing_widget)
        # Initial coupling: dropdown is enabled iff toggle is on.
        self.spacing_combo.setEnabled(self.apply_spacing_check.isChecked())

        # Filter row — Neighbors only / Include diagonal / Flanking only.
        # The whole row is hidden whenever the mode doesn't use spacing
        # neighbours (Monopolar, Common Ground); within it, individual
        # checkboxes are gated by mode and by each other.
        filter_row = QtWidgets.QHBoxLayout()
        filter_row.addWidget(self.neighbors_only)
        filter_row.addWidget(self.include_diagonal)
        filter_row.addWidget(self.flanking_only)
        filter_row.addStretch(1)
        self.filter_widget = QtWidgets.QWidget()
        self.filter_widget.setLayout(filter_row)
        v.addWidget(self.filter_widget)
        # Initial enabled-state coupling (Include diagonal only when
        # Neighbors only is on).
        self.include_diagonal.setEnabled(self.neighbors_only.isChecked())

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
        # When the user toggles Global Return, slide the current mode
        # between its with-/without-counter twins so the experiment
        # they were configuring keeps its meaning:
        #   Bipolar  ↔ Partial Bipolar
        #   Tripolar ↔ Partial Tripolar
        # (Monopolar / CG don't have twins so they stay put.)
        twin = {
            KIND_BP: KIND_PBP, KIND_PBP: KIND_BP,
            KIND_TP: KIND_PTP, KIND_PTP: KIND_TP,
        }
        prev_kind = self._kind()
        target_kind = twin.get(prev_kind)
        # The dropdown's available kinds depend on this state — refresh
        # before rebuilding combos so the user always sees a consistent
        # kind / spacing visibility for the current Global-Return.
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
                self._on_kind_changed(self.kind_combo.currentText())
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

    # ----------------------------------------------------------- internal
    def _spacings(self) -> Set[int]:
        """Set of Chebyshev distances to consider, or empty when the
        Apply-spacing toggle is off (which the candidate-builder treats
        as "no distance filter").

        The dropdown value is the number of electrodes *between* the
        active and the return; the candidate filter operates in
        Chebyshev grid distance, so we convert via ``distance = gap + 1``.
        Adjacent (gap=0) → distance 1, one-between → distance 2, etc.

        On a linear array with **Neighbors only** on, distance is
        always exactly 1 — "neighbours" is unambiguous on a 1-D strip
        (no second dimension to spread out into) — so spacing is
        forced to {1} regardless of what's typed in the dropdown.
        """
        if self._is_linear_array() and self.neighbors_only.isChecked():
            return {1}
        if not self.apply_spacing_check.isChecked():
            return set()
        gap = self.spacing_combo.currentData()
        try:
            gap = int(gap)
        except (TypeError, ValueError):
            return set()
        return {gap + 1}

    def _on_apply_spacing_toggled(self, checked: bool):
        # Distance dropdown is only meaningful when the toggle is on.
        self.spacing_combo.setEnabled(checked)
        # Apply-spacing fully owns candidate selection when on, so we
        # both disable AND uncheck the neighbour-based filters — their
        # current state shouldn't influence the candidate set while
        # spacing is in charge. We remember the prior checked state
        # so toggling spacing back off restores the user's choices
        # rather than leaving the boxes blank.
        if checked:
            self._neighbors_only_saved = self.neighbors_only.isChecked()
            self._flanking_only_saved = self.flanking_only.isChecked()
            for cb in (self.neighbors_only, self.flanking_only):
                cb.blockSignals(True)
                try:
                    cb.setChecked(False)
                finally:
                    cb.blockSignals(False)
            self.neighbors_only.setEnabled(False)
            self.flanking_only.setEnabled(False)
            # Include-diagonal stays enabled in spacing mode — it
            # determines whether diagonal cells at the picked distance
            # qualify as candidates, which is still meaningful here.
            self.include_diagonal.setEnabled(True)
        else:
            # Restore prior checked state, then re-enable the widgets.
            saved_n = getattr(self, "_neighbors_only_saved", True)
            saved_f = getattr(self, "_flanking_only_saved", True)
            for cb, val in ((self.neighbors_only, saved_n),
                            (self.flanking_only, saved_f)):
                cb.blockSignals(True)
                try:
                    cb.setChecked(bool(val))
                finally:
                    cb.blockSignals(False)
            self.neighbors_only.setEnabled(True)
            self.flanking_only.setEnabled(True)
            self.include_diagonal.setEnabled(self.neighbors_only.isChecked())
        self._rebuild()

    def _kind(self) -> str:
        return self.kind_combo.currentText()

    def _is_linear_array(self) -> bool:
        """True when the loaded array is 1-D (single row OR single column)."""
        return self._array is not None and (
            self._array.rows == 1 or self._array.cols == 1)

    def _on_kind_changed(self, kind: str):
        # Spacing + filter rows only apply when on-array neighbour returns
        # are needed. Common Ground and Monopolar use neither.
        needs_neighbours = KIND_NEAR_RETS.get(kind, 0) > 0
        self.spacing_widget.setVisible(needs_neighbours)
        self.filter_widget.setVisible(needs_neighbours)
        # Linear arrays don't have a second dimension, so "Include
        # diagonal" and "Flanking only" don't apply — hide both.
        # Spacing is also moot on a linear strip when Neighbors-only
        # is on (forced to 1), so hide the Apply-spacing widget too.
        is_linear = self._is_linear_array()
        self.include_diagonal.setVisible(not is_linear)
        if is_linear and self.neighbors_only.isChecked():
            self.spacing_widget.setVisible(False)
        # Flanking-only is meaningful for tripolar variants only AND
        # only when "Neighbors only" is on. Hidden on linear arrays
        # regardless (the user explicitly excluded it).
        is_tripolar = kind in (KIND_TP, KIND_PTP)
        self.flanking_only.setVisible(
            is_tripolar and self.neighbors_only.isChecked()
            and not is_linear)
        # Static single-mode (pulsing experiments + Progressive Stress)
        # is relaxed for the *single-active* kinds: Monopolar, Common
        # Ground, and Partial Common Ground. Each combination there
        # exercises a different active electrode, so the runner can
        # queue them sequentially. Multipolar kinds (BP / TP / PBP /
        # PTP) stay single-combo because the choice of return geometry
        # is part of the test definition — running several at once
        # would just bury the user.
        prev_single = self._single_mode
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

    def _on_neighbors_only_toggled(self, checked: bool):
        # Include-diagonal is meaningful when restricting to neighbours
        # OR when apply-spacing is on (it gates whether diagonal cells
        # at the picked distance qualify). Disable only when neither
        # condition holds.
        spacing_mode = self.apply_spacing_check.isChecked()
        self.include_diagonal.setEnabled(checked or spacing_mode)
        # Flanking-only also depends on neighbours-only — refresh its
        # visibility here in case the user just turned the parent off
        # while a tripolar mode is selected.
        kind = self._kind()
        is_tripolar = kind in (KIND_TP, KIND_PTP)
        self.flanking_only.setVisible(is_tripolar and checked)
        self._rebuild()

    def _rebuild(self):
        self._combos = self._compute_combos()
        # In single-combo mode only the first combo is enabled by default;
        # the user explicitly picks which single combination to run.
        if self._single_mode and self._combos:
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
        self._update_summary()
        self.combinationsChanged.emit(self.selected_configurations())

    def _update_summary(self):
        n_total = len(self._combos)
        n_on = sum(1 for c in self._combos if c.enabled)
        kind = self._kind()
        spacings = sorted(self._spacings())
        bits = [f"<b>{n_on}</b>/{n_total} combinations selected", kind]
        if KIND_NEAR_RETS.get(kind, 0) > 0:
            if spacings:
                bits.append("spacing: +" + ", +".join(str(s) for s in spacings))
            else:
                bits.append("spacing: <i>any distance</i>")
        if kind in KIND_NEEDS_GLOBAL:
            bits.append("Global Return: " +
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
            # Flanking-only is suppressed in spacing mode (matches the
            # disabled widget state). Otherwise it constrains tripolar
            # combos so the active sits at the midpoint of the return
            # pair.
            flank_only = (self.flanking_only.isChecked() and is_tripolar
                          and not self.apply_spacing_check.isChecked())
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
        """Channels at any of the selected spacing offsets from ``active``.

        Spacing N == Chebyshev distance N on the array's row/col grid.
        Triangular layouts use the same row/col coordinates the user
        authored in the device-mapping table, so spacing semantics stay
        consistent across array geometries.

        The "Neighbors only" / "Include diagonal" filters apply on top:

        * **Neighbors only off** — every channel at one of the picked
          Chebyshev distances is a candidate.
        * **Neighbors only on, Include diagonal on** — same as above,
          since 8-connected neighbours coincide with Chebyshev rings.
        * **Neighbors only on, Include diagonal off** — orthogonal-only:
          drop pure-diagonal cells (``dr > 0 and dc > 0``). For spacing
          1 that gives 4-connected neighbours; for spacing 2 it gives
          the orthogonal cells two steps away.
        """
        if self._array is None: return []
        try:
            ref = self._array[active]
        except KeyError:
            return []
        spacings = self._spacings()       # empty set ⇒ no distance filter
        # Apply-spacing owns candidate selection when on; the
        # Neighbors-only filter is bypassed (its widget is disabled
        # alongside). Include-diagonal still applies in either mode:
        # it gates whether pure-diagonal cells qualify, so the user
        # can switch between 4- and 8-connected candidate sets even
        # in spacing mode.
        spacing_mode = self.apply_spacing_check.isChecked()
        only = self.neighbors_only.isChecked() and not spacing_mode
        diag = self.include_diagonal.isChecked()
        # Exclude pure-diagonal cells when diagonals aren't allowed AND
        # the user has opted into a candidate-shape filter (either
        # neighbours-only in non-spacing mode, or apply-spacing).
        exclude_diag = (not diag) and (only or spacing_mode)
        out: List[int] = []
        for s in self._array.sites:
            if s.number == active: continue
            dr = abs(s.row - ref.row); dc = abs(s.col - ref.col)
            d = max(dr, dc)
            # Only filter by spacing when the toggle is on.
            if spacings and d not in spacings:
                continue
            if exclude_diag and dr > 0 and dc > 0:
                continue
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
            return f"Channel {active} (active) versus Off-array return"
        if kind_id == "CG":
            return (f"Channel {active} (active) versus "
                    f"all other on-array channels (return)")
        if kind_id == "PCG":
            return (f"Channel {active} (active) versus "
                    f"all other on-array channels (return) + Off-array return")
        if kind_id == "BP":
            return (f"Channel {active} (active) versus "
                    f"Channel {rets[0]} (return)")
        if kind_id == "TP":
            return (f"Channel {active} (active) versus "
                    f"Channels {rets[0]} and {rets[1]} (return)")
        if kind_id == "PBP":
            return (f"Channel {active} (active) versus "
                    f"Channel {rets[0]} (return) + Off-array return")
        if kind_id == "PTP":
            return (f"Channel {active} (active) versus "
                    f"Channels {rets[0]} and {rets[1]} (return) + Off-array return")
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
        self._update_summary()
        self.combinationsChanged.emit(self.selected_configurations())

    def _on_item_entered(self, item: QtWidgets.QListWidgetItem):
        """Mouse moved over a combo row — emit hover signal."""
        idx = self.combos_list.row(item)
        if 0 <= idx < len(self._combos):
            cfg = self._combos[idx].config
            self.combinationHovered.emit(int(cfg.active), list(cfg.returns))

    def eventFilter(self, obj, ev):
        """Clear highlight when the mouse leaves the combos list viewport."""
        if obj is self.combos_list.viewport() and ev.type() == QtCore.QEvent.Type.Leave:
            self.combinationHovered.emit(-1, [])
        return super().eventFilter(obj, ev)

    def _on_select_all_toggled(self, checked: bool):
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
