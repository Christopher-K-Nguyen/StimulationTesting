"""Tabbed scope view — one sub-tab per channel + global waveform toggles.

Replaces the single :class:`stimtest.gui.widgets.ScopePlot` in each
experiment tab. Layout per sub-tab::

    ┌────────────────────────────────────────────────┐
    │ ☑V_mon  ☑I_mon  ☑E_act  ☑E_ret  [colour key]   │   global toggles
    ├────────────────────────────────────────────────┤
    │                                                │
    │           ScopePlot (pyqtgraph)                │   per-channel
    │                                                │
    ├────────────────────────────────────────────────┤
    │   per-channel metrics table                    │
    └────────────────────────────────────────────────┘

The wrapping ``QTabWidget`` shows a tab for every channel that has
either been selected as active, has captures recorded, or has been
explicitly registered via :meth:`ensure_tab`. Tabs that finish a run
get a small green ✓ in their title so the user can see at a glance
which channels are done.

Visibility checkboxes are global to the pane — toggling V_mon hides
the V_mon trace in every sub-tab. State is exposed via :meth:`prefs`
/ :meth:`restore_prefs` so the experiment tabs can persist it.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from ..session import Capture
from . import rich
from .widgets import AXIS_LEFT, AXIS_NA, AXIS_RIGHT, MetricTable, ScopePlot


# Trace identifiers — keep the strings stable; they're used as plot keys
# AND as prefs JSON keys.
TRACE_VMON = "V_mon"
TRACE_IMON = "I_mon"
TRACE_EACT = "E_act"
TRACE_ERET = "E_ret"
ALL_TRACES = (TRACE_VMON, TRACE_IMON, TRACE_EACT, TRACE_ERET)
TRACE_COLOURS = {
    TRACE_VMON: "#444444",
    TRACE_IMON: "#1976d2",
    TRACE_EACT: "#e57373",
    TRACE_ERET: "#26a69a",
}
# Default per-trace Y-axis assignment. I_mon goes to the right axis so
# its µA range doesn't compress the V/E traces on the left axis;
# everything else is voltage and shares the left axis.
DEFAULT_TRACE_AXIS = {
    TRACE_VMON: AXIS_LEFT,
    TRACE_IMON: AXIS_RIGHT,
    TRACE_EACT: AXIS_LEFT,
    TRACE_ERET: AXIS_LEFT,
}


class _ChannelPage(QtWidgets.QWidget):
    """One sub-tab: scope plot + metric table for a single
    channel-or-combination key.

    The ``key`` argument is the stable identifier used by
    :class:`MultiChannelScope` to route captures: ``int`` for legacy
    monopolar callers (rendered as ``"CH05"`` in the tab title) and
    ``str`` for combination labels (e.g. ``"CH05 v 06"`` for a
    bipolar pair). Two combinations sharing an active channel get
    distinct sub-tabs, which fixes the previous behaviour where they
    collided on a single CHnn tab.
    """

    def __init__(self, key, parent=None):
        super().__init__(parent)
        self.key = key
        # Back-compat alias — callers who reach in for ``page.channel``
        # still get the int when the key is one, otherwise the
        # full combination label.
        self.channel = key
        self.scope = ScopePlot()
        self.metrics = MetricTable()
        # Full per-key capture history. Previously this stored only
        # ``_latest`` and every new capture overwrote the prior one,
        # leaving the user no way to inspect mid-run captures after
        # the latest landed. Storing a list lets the navigation row
        # below page through every snapshot taken on this channel /
        # combo. ``_latest`` stays as a back-compat alias pointing at
        # the most-recent (== last in list) entry.
        self._captures: List[Capture] = []
        self._current_idx: int = -1

        # ----- capture-history nav row -----
        # Prev / next buttons + "X of Y" label. Hidden when only one
        # capture has landed (no point showing nav for nothing to
        # navigate). Updated by ``_refresh_nav_row``.
        self._nav_prev = QtWidgets.QToolButton()
        self._nav_prev.setText("◀")
        self._nav_prev.setToolTip("Previous capture on this channel / combo")
        self._nav_prev.clicked.connect(self._nav_step_back)
        self._nav_next = QtWidgets.QToolButton()
        self._nav_next.setText("▶")
        self._nav_next.setToolTip("Next capture on this channel / combo")
        self._nav_next.clicked.connect(self._nav_step_forward)
        self._nav_latest = QtWidgets.QToolButton()
        self._nav_latest.setText("Latest")
        self._nav_latest.setToolTip(
            "Jump to the most-recent capture and re-arm auto-follow so "
            "the page snaps to new captures as they arrive.")
        self._nav_latest.clicked.connect(self._nav_jump_latest)
        self._nav_label = QtWidgets.QLabel("—")
        self._nav_label.setStyleSheet("color: #555;")
        self._nav_kind = QtWidgets.QLabel("")
        self._nav_kind.setStyleSheet("color: #888; font-style: italic;")
        # Auto-follow flag — when True (default), incoming captures
        # snap the page to the newest one. The user breaking out of
        # latest via prev/next disables follow until they hit Latest.
        self._auto_follow: bool = True

        nav_row = QtWidgets.QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.setSpacing(4)
        nav_row.addWidget(self._nav_prev)
        nav_row.addWidget(self._nav_next)
        nav_row.addWidget(self._nav_label)
        nav_row.addWidget(self._nav_kind, stretch=1)
        nav_row.addWidget(self._nav_latest)
        self._nav_row_w = QtWidgets.QWidget()
        self._nav_row_w.setLayout(nav_row)
        self._nav_row_w.setVisible(False)

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.addWidget(self._nav_row_w)
        v.addWidget(self.scope, stretch=3)
        v.addWidget(self.metrics, stretch=2)

    # ---- back-compat shim: callers and tests reach for ``_latest`` ----
    @property
    def _latest(self) -> Optional[Capture]:
        """The most-recent capture in the history (last appended)."""
        if not self._captures:
            return None
        return self._captures[-1]

    def capture_count(self) -> int:
        """Number of captures currently held for this channel / combo."""
        return len(self._captures)

    def current_capture(self) -> Optional[Capture]:
        """The capture currently rendered into the scope + metrics
        table — may be older than the latest if the user navigated
        back via the prev / next buttons."""
        if 0 <= self._current_idx < len(self._captures):
            return self._captures[self._current_idx]
        return None

    def set_capture(self, capture: Capture, visible: Dict[str, bool],
                    axis_map: Optional[Dict[str, str]] = None,
                    inset_visible: bool = False,
                    inset_traces: Optional[set] = None):
        """Append ``capture`` to the history and (when auto-following)
        render it. Use ``set_index`` to navigate to a different
        capture without appending."""
        self._captures.append(capture)
        if self._auto_follow or self._current_idx < 0:
            self._current_idx = len(self._captures) - 1
        self._refresh_traces(visible, axis_map, inset_visible, inset_traces)
        cap = self.current_capture()
        if cap is not None:
            self.metrics.show_capture(cap)
        self._refresh_nav_row()

    def set_index(self, idx: int,
                  visible: Optional[Dict[str, bool]] = None,
                  axis_map: Optional[Dict[str, str]] = None,
                  inset_visible: bool = False,
                  inset_traces: Optional[set] = None) -> bool:
        """Render the capture at ``idx`` (0-based). Returns True on
        success, False if the index is out of range. Disables
        auto-follow so subsequent ``set_capture`` calls don't snap
        the view back to latest — the user explicitly chose this
        capture and probably wants to stay on it."""
        if not (0 <= idx < len(self._captures)):
            return False
        self._current_idx = idx
        self._auto_follow = (idx == len(self._captures) - 1)
        if visible is not None:
            self._refresh_traces(visible, axis_map, inset_visible, inset_traces)
        cap = self.current_capture()
        if cap is not None:
            self.metrics.show_capture(cap)
        self._refresh_nav_row()
        return True

    def refresh_visibility(self, visible: Dict[str, bool],
                           axis_map: Optional[Dict[str, str]] = None,
                           inset_visible: bool = False,
                           inset_traces: Optional[set] = None):
        if self.current_capture() is not None:
            self._refresh_traces(visible, axis_map, inset_visible, inset_traces)

    # ----- nav-row helpers -----
    def _nav_step_back(self):
        if self._current_idx > 0:
            self.set_index(self._current_idx - 1)

    def _nav_step_forward(self):
        if self._current_idx < len(self._captures) - 1:
            self.set_index(self._current_idx + 1)

    def _nav_jump_latest(self):
        if self._captures:
            self.set_index(len(self._captures) - 1)
        # Re-arm auto-follow even if we were already at latest, so
        # future captures keep snapping into view.
        self._auto_follow = True

    def _refresh_nav_row(self):
        """Update the prev/next button enable-state, the index label,
        and the optional kind tag. Hide the whole row when ≤ 1
        captures (nothing to navigate)."""
        n = len(self._captures)
        if n <= 1:
            self._nav_row_w.setVisible(False)
            return
        self._nav_row_w.setVisible(True)
        idx = self._current_idx
        self._nav_prev.setEnabled(idx > 0)
        self._nav_next.setEnabled(idx < n - 1)
        self._nav_label.setText(
            f"Capture {idx + 1} of {n}"
            + (" (latest)" if idx == n - 1 else ""))
        cap = self.current_capture()
        # Optional capture-kind tag — set by the experiment runner
        # via ``Capture.kind`` (e.g. 'pre_char', 'post_char',
        # 'snapshot'). Renders as italic grey when present so the
        # user can tell at a glance what category they're viewing.
        kind = getattr(cap, "kind", "") if cap is not None else ""
        self._nav_kind.setText(f"— {kind}" if kind else "")

    def _trace_label(self, trace: str) -> str:
        """Suffix every trace name with its display unit so the
        legend reads cleanly. Used as the curve key in ScopePlot —
        stable across calls so re-plotting reuses the existing
        PlotDataItem rather than churning the legend."""
        return f"{trace} ({'µA' if trace == TRACE_IMON else 'V'})"

    def _refresh_traces(self, visible: Dict[str, bool],
                        axis_map: Optional[Dict[str, str]] = None,
                        inset_visible: bool = False,
                        inset_traces: Optional[set] = None):
        cap = self.current_capture()
        if cap is None: return
        axis_map = axis_map or DEFAULT_TRACE_AXIS
        traces: Dict[str, np.ndarray] = {}
        axis: Dict[str, str] = {}
        if visible.get(TRACE_VMON, True) and cap.v_mon_v.size:
            k = self._trace_label(TRACE_VMON)
            traces[k] = cap.v_mon_v
            axis[k] = axis_map.get(TRACE_VMON, AXIS_LEFT)
        if visible.get(TRACE_IMON, True) and cap.i_mon_ua is not None and cap.i_mon_ua.size:
            k = self._trace_label(TRACE_IMON)
            # ``i_mon_ua`` is already in microamps — no conversion.
            traces[k] = cap.i_mon_ua
            axis[k] = axis_map.get(TRACE_IMON, AXIS_RIGHT)
        if visible.get(TRACE_EACT, True) and cap.e_act_v is not None and cap.e_act_v.size:
            k = self._trace_label(TRACE_EACT)
            traces[k] = cap.e_act_v
            axis[k] = axis_map.get(TRACE_EACT, AXIS_LEFT)
        if visible.get(TRACE_ERET, True) and cap.e_ret_v is not None and cap.e_ret_v.size:
            k = self._trace_label(TRACE_ERET)
            traces[k] = cap.e_ret_v
            axis[k] = axis_map.get(TRACE_ERET, AXIS_LEFT)
        colours = {self._trace_label(t): TRACE_COLOURS[t] for t in ALL_TRACES}
        # Clear and replot — ScopePlot.set_traces only appends/updates
        # so toggling visibility off requires a clear() first.
        self.scope.clear()
        self.scope.set_traces(cap.time_us, traces, colors=colours, axis=axis)
        # Inset state: pass through to the plot. The inset trace
        # selection comes in as raw role tags (V_mon / I_mon / ...);
        # translate to the unit-suffixed names that ScopePlot uses
        # internally.
        if inset_traces:
            inset_keys = {self._trace_label(t) for t in inset_traces
                          if visible.get(t, True)}
        else:
            inset_keys = set()
        self.scope.set_inset_traces(inset_keys)
        self.scope.set_inset_visible(bool(inset_visible))


class MultiChannelScope(QtWidgets.QWidget):
    """A tab-per-channel scope view with global waveform toggles.

    Two control groups along the top bar:

    * **Per-trace axis dropdowns** — one combo per trace (V_mon /
      I_mon / E_act / E_ret) with three options: ``N/A`` (hides the
      trace), ``Left y-axis``, ``Right y-axis``. Default routing:
      I_mon → right (so its µA range doesn't compress the V-scale
      traces); V_mon / E_act / E_ret → left. Hidden in the toolbar
      entirely when the scope doesn't capture that trace
      (see :meth:`set_available_traces`).
    * **Inset** — checkbox plus a multi-select dropdown (a
      QToolButton with a popup of checkable trace names) that
      controls a small below-plot inset. Useful for focusing on a
      subset of waveforms while keeping the full set visible above.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # ----- top bar: per-trace axis dropdowns -----
        self.axis_combos: Dict[str, QtWidgets.QComboBox] = {}
        # Trace-name labels next to each combo — kept around so we can
        # show/hide them as a unit when ``set_available_traces`` runs.
        self.axis_labels: Dict[str, QtWidgets.QLabel] = {}
        # Per-trace axis assignment (live state; mirrored to prefs).
        # Visibility is encoded as AXIS_NA — no separate flag.
        self._axis_map: Dict[str, str] = dict(DEFAULT_TRACE_AXIS)
        # Which traces the scope is actually capturing. Default to all
        # four; ``set_available_traces`` narrows the list once the
        # experiment tab pushes its alias map.
        self._available_traces: set = set(ALL_TRACES)

        bar = QtWidgets.QHBoxLayout()
        for trace in ALL_TRACES:
            # Coloured trace name — same hue family as the curve in the
            # plot so the user reads the row at a glance.
            lbl = QtWidgets.QLabel(self._trace_label(trace))
            lbl.setStyleSheet(
                f"color: {TRACE_COLOURS[trace]}; font-weight: bold;")
            self.axis_labels[trace] = lbl
            bar.addWidget(lbl)
            # Single dropdown collapses the old (visibility checkbox +
            # L/R combo) pair: ``N/A`` hides the trace, the two axis
            # entries place it on left or right. Items spelled out per
            # the user spec so ``L`` / ``R`` aren't ambiguous on first
            # encounter.
            axis_combo = QtWidgets.QComboBox()
            axis_combo.addItem("N/A",          userData=AXIS_NA)
            axis_combo.addItem("Left y-axis",  userData=AXIS_LEFT)
            axis_combo.addItem("Right y-axis", userData=AXIS_RIGHT)
            default_axis = DEFAULT_TRACE_AXIS.get(trace, AXIS_LEFT)
            axis_combo.setCurrentIndex(
                {AXIS_NA: 0, AXIS_LEFT: 1, AXIS_RIGHT: 2}[default_axis])
            axis_combo.setToolTip(
                f"{trace} placement on the scope: pick "
                f"<b>N/A</b> to hide it, <b>Left y-axis</b> for the "
                f"main voltage scale, <b>Right y-axis</b> for the "
                f"second (current / alternate) scale. Default for "
                f"{trace} is "
                f"<b>{'Right y-axis' if DEFAULT_TRACE_AXIS[trace] == AXIS_RIGHT else 'Left y-axis'}</b>.")
            axis_combo.currentIndexChanged.connect(
                lambda _idx, t=trace: self._on_axis_changed(t))
            self.axis_combos[trace] = axis_combo
            bar.addWidget(axis_combo)
        bar.addSpacing(12)

        # ----- inset controls -----
        # Toggle button: when on, the small inset plot below the main
        # view is visible.
        self.inset_check = QtWidgets.QCheckBox("Inset")
        self.inset_check.setChecked(False)
        self.inset_check.setToolTip(
            "Show a compact inset plot below the main scope, mirroring "
            "the trace(s) you pick from the dropdown next to this "
            "toggle. Useful for keeping a focused single-trace view "
            "alongside the full multi-trace plot.")
        self.inset_check.toggled.connect(self._on_inset_toggled)
        bar.addWidget(self.inset_check)
        # Multi-select dropdown for inset traces — a QToolButton
        # whose popup menu carries one checkable QAction per trace.
        # Mirrors the spreadsheet "Filter" UX (and combination_panel's
        # checkable list-widget pattern, just inside a popup).
        self.inset_btn = QtWidgets.QToolButton()
        self.inset_btn.setText("(pick traces)")
        self.inset_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.inset_btn.setToolTip(
            "Pick which traces appear in the inset. Multi-select; "
            "default starts empty so the inset is blank until you "
            "pick at least one.")
        self._inset_menu = QtWidgets.QMenu(self.inset_btn)
        self.inset_actions: Dict[str, "QtGui.QAction"] = {}
        for trace in ALL_TRACES:
            act = self._inset_menu.addAction(trace)
            act.setCheckable(True)
            act.setChecked(False)
            act.triggered.connect(self._on_inset_traces_changed)
            self.inset_actions[trace] = act
        self.inset_btn.setMenu(self._inset_menu)
        # Keep the button label in sync with the picked subset.
        self._refresh_inset_button_label()
        # Disabled until the inset is toggled on — clearer affordance
        # that the dropdown is inert without the inset.
        self.inset_btn.setEnabled(False)
        bar.addWidget(self.inset_btn)
        bar.addStretch(1)

        # ----- entry list + content stack -----
        # Replaces the previous QTabWidget — a left-side ``QListWidget``
        # carries one row per channel/combination, and a
        # ``QStackedWidget`` on the right shows the matching
        # ``_ChannelPage`` for the currently-selected list row. New
        # captures append rows to the list AND push their pages onto
        # the stack (rather than spawning sub-tabs across the top).
        # Pages are keyed by a stable identifier — either an ``int``
        # channel (legacy monopolar path) or a ``str`` combination
        # label (e.g. ``"CH05 v 06"`` for bipolar). Two configurations
        # that share an active channel get distinct rows because
        # their ``str`` keys differ.
        self._pages: Dict[object, _ChannelPage] = {}
        # ``_keys_in_order`` maintains the insertion order so a list
        # row at index ``i`` corresponds to the i-th page on the stack
        # — Qt doesn't expose a direct row→key lookup so we keep one.
        self._keys_in_order: List[object] = []
        self._completed: set = set()

        self.entry_list = QtWidgets.QListWidget()
        self.entry_list.setToolTip(
            "Channels and combinations under test. New entries appear "
            "as captures stream in; click an entry to view its scope "
            "trace and metric table on the right.")
        self.entry_list.setMaximumWidth(220)
        self.entry_list.currentRowChanged.connect(self._on_entry_changed)
        self.content_stack = QtWidgets.QStackedWidget()
        # When the stack is empty we show a placeholder so the right
        # pane doesn't render as an empty grey rectangle pre-run.
        self._placeholder = QtWidgets.QLabel(
            "Captures from the experiment runner will appear here, "
            "one entry per channel / combination. Pick an entry on "
            "the left to view its trace.")
        self._placeholder.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._placeholder.setStyleSheet("color: #888; padding: 32px;")
        self.content_stack.addWidget(self._placeholder)

        # Horizontal splitter so the user can tune how much room the
        # entry list gets — e.g. 4-cell list vs a long combination
        # name set.
        split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        split.addWidget(self.entry_list)
        split.addWidget(self.content_stack)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([180, 520])
        self._main_split = split

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(bar)
        v.addWidget(split, stretch=1)

    # ---------------------------------------------------------- public API
    def visibility(self) -> Dict[str, bool]:
        """Per-trace visibility derived from the axis map.

        A trace is visible iff its axis is left / right (i.e. NOT
        ``AXIS_NA``). Traces the scope doesn't capture are
        unconditionally hidden — :meth:`set_available_traces` clears
        them via the same axis-map path.
        """
        return {t: (self._axis_map.get(t) != AXIS_NA
                    and t in self._available_traces)
                for t in ALL_TRACES}

    def set_visibility(self, vis: Dict[str, bool]):
        """Compatibility shim for legacy prefs.

        New prefs encode visibility inside ``axis_map`` (``AXIS_NA`` =
        hidden) so this is only invoked when restoring an old profile
        with a separate ``visibility`` dict. Maps each ``False`` entry
        to ``AXIS_NA`` and each ``True`` entry back to the trace's
        default axis (so the user gets a sensible placement when the
        old prefs didn't carry an ``axis_map``).
        """
        for t, on in vis.items():
            if t not in self.axis_combos:
                continue
            if not on:
                self._set_axis(t, AXIS_NA)
            else:
                # Restore to the trace default if the current axis is
                # AXIS_NA; otherwise leave whatever the user picked.
                if self._axis_map.get(t) == AXIS_NA:
                    self._set_axis(t, DEFAULT_TRACE_AXIS.get(t, AXIS_LEFT))
        self._on_visibility_toggled()

    def axis_map(self) -> Dict[str, str]:
        """Per-trace ``axis`` snapshot — values are ``"left"``,
        ``"right"`` or ``"na"`` (hidden)."""
        return dict(self._axis_map)

    def set_axis_map(self, axes: Dict[str, str]) -> None:
        """Apply a saved per-trace axis assignment."""
        for trace, axis in axes.items():
            if trace not in self.axis_combos:
                continue
            if axis not in (AXIS_LEFT, AXIS_RIGHT, AXIS_NA):
                continue
            self._set_axis(trace, axis)
        # Push the new assignment into every existing page.
        self._on_visibility_toggled()

    def _set_axis(self, trace: str, axis: str) -> None:
        """Block-signals helper — sets the combo's index AND the
        cached ``_axis_map`` entry without firing the change signal.
        Used by :meth:`set_axis_map` / :meth:`set_visibility` so a
        bulk restore doesn't trigger N redundant repaints.
        """
        combo = self.axis_combos.get(trace)
        if combo is None:
            return
        idx = {AXIS_NA: 0, AXIS_LEFT: 1, AXIS_RIGHT: 2}.get(axis, 1)
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(idx)
        finally:
            combo.blockSignals(False)
        self._axis_map[trace] = axis

    def set_available_traces(self, available) -> None:
        """Tell the scope which traces the device is actually capturing.

        Hides the row entirely for traces that aren't in
        ``available`` — saves a row of dead controls when the scope
        is only mapped to V_mon / I_mon, for instance.

        **Per-spec rule:** if ``E_ret`` is captured, ``E_act`` is
        added too (the convention is that E_act is always plotted
        alongside E_ret when either is recorded — the user's note:
        "include E_act if E_ret is a channel").

        Pass an empty iterable or ``None`` to make every trace
        available (the default; useful for tests / legacy callers).
        """
        if not available:
            available_set = set(ALL_TRACES)
        else:
            available_set = {str(t) for t in available if t in ALL_TRACES}
        # E_ret implies E_act per the user spec.
        if TRACE_ERET in available_set:
            available_set.add(TRACE_EACT)
        self._available_traces = available_set
        # Show / hide the per-trace controls (label + axis combo) as
        # a unit. Unavailable traces get their axis forced to AXIS_NA
        # so ``visibility()`` reads ``False`` and the plot skips them.
        for trace in ALL_TRACES:
            visible = trace in available_set
            self.axis_labels[trace].setVisible(visible)
            self.axis_combos[trace].setVisible(visible)
            if not visible:
                # Don't clobber the user's saved axis choice for the
                # eventual return of this trace — only the runtime
                # ``_available_traces`` check gates rendering. The
                # axis combo state stays put.
                pass
        # Also prune the inset selection: a trace that's no longer
        # available shouldn't keep its inset checkbox set, otherwise
        # toggling availability back on later would re-introduce a
        # stale selection silently.
        for trace, act in self.inset_actions.items():
            act.setVisible(trace in available_set)
            if trace not in available_set and act.isChecked():
                act.blockSignals(True)
                try:
                    act.setChecked(False)
                finally:
                    act.blockSignals(False)
        self._refresh_inset_button_label()
        # Repaint every existing page with the new availability.
        self._on_visibility_toggled()

    def available_traces(self) -> set:
        """Snapshot of the currently-captured traces (after the
        E_ret → E_act expansion)."""
        return set(self._available_traces)

    def inset_enabled(self) -> bool:
        return bool(self.inset_check.isChecked())

    def set_inset_enabled(self, on: bool) -> None:
        self.inset_check.setChecked(bool(on))   # triggers _on_inset_toggled

    def inset_traces(self) -> List[str]:
        return [t for t, act in self.inset_actions.items() if act.isChecked()]

    def set_inset_traces(self, traces) -> None:
        names = set(traces or [])
        for t, act in self.inset_actions.items():
            act.blockSignals(True)
            try:
                act.setChecked(t in names)
            finally:
                act.blockSignals(False)
        self._refresh_inset_button_label()
        self._on_visibility_toggled()

    def ensure_tab(self, key) -> _ChannelPage:
        """Backward-compatible alias for :meth:`ensure_page`."""
        return self.ensure_page(key)

    def ensure_page(self, key) -> _ChannelPage:
        """Get/create the entry-list row + stacked-widget page for ``key``.

        ``key`` is either an ``int`` channel number (legacy monopolar
        path — rendered as ``"CH05"`` in the row label) or a ``str``
        combination label (multipolar path — used as the row label
        verbatim, e.g. ``"CH05 v 06"`` for a bipolar pair). Two
        configurations that share an active channel produce distinct
        rows because their ``str`` keys differ — the previous
        ``int``-only key collapsed them into one.

        **Timing**: this is the only entry-creation path. Called by
        :meth:`add_capture` (first capture for a key — including
        failed / aborted captures) AND by :meth:`add_pending` (an
        attempt starts but no capture has landed yet). Either way,
        the row appears as soon as ANY attempt is made, never
        deferred to completion. The ✓ marker (via
        :meth:`mark_completed`) is purely a status tag added on top
        of an already-existing row; it never creates rows.
        """
        if key in self._pages:
            return self._pages[key]
        page = _ChannelPage(key)
        self._pages[key] = page
        self._keys_in_order.append(key)
        self.content_stack.addWidget(page)
        item = QtWidgets.QListWidgetItem(self._title(key))
        item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
        self.entry_list.addItem(item)
        # Auto-select the very first entry so the user never sees the
        # placeholder once captures start streaming. Subsequent
        # entries don't steal focus — that's
        # :meth:`add_capture`'s job.
        if self.entry_list.count() == 1:
            self.entry_list.setCurrentRow(0)
        return page

    def add_pending(self, key) -> _ChannelPage:
        """Pre-emptively create an entry for ``key`` before any
        capture has arrived.

        Useful at "attempt start" — the experiment runner has just
        begun a configuration and we want the row to appear in the
        list immediately, not wait for the first acquired waveform.
        The right-side page is empty (its scope shows no curves and
        the metric table is blank) until the first :meth:`add_capture`
        fills it in.

        Idempotent: calling on a key that already exists is a no-op.
        Returns the page either way so callers can attach further
        state if they wish.
        """
        return self.ensure_page(key)

    def add_capture(self, capture: Capture, key):
        """Drop a capture into ``key``'s page and focus it in the list.

        **Adds the entry on every capture, not on completion.** The
        row appears as soon as the FIRST capture (good or bad) lands
        for ``key``, so the user watches the entry list grow as
        attempts happen — never deferred until a configuration
        finishes. ``capture.status.good`` is *not* gated here; a
        failed acquisition still spawns the row, with whatever
        partial data it carries rendered into the page.
        """
        page = self.ensure_page(key)
        page.set_capture(capture, self.visibility(),
                         axis_map=self._axis_map,
                         inset_visible=self.inset_enabled(),
                         inset_traces=set(self.inset_traces()))
        # Focus the matching list row so the right-side stack auto-
        # swaps. ``ensure_page`` already auto-focused the very first
        # entry; subsequent captures pull the active selection along
        # with them so the user doesn't have to manually click.
        idx = self._keys_in_order.index(key)
        self.entry_list.setCurrentRow(idx)

    def mark_completed(self, key):
        """Tag ``key``'s entry as finished — small ✓ at the end of
        the row label — so the user sees at a glance which channel /
        combination is done.
        """
        self._completed.add(key)
        if key in self._pages:
            try:
                idx = self._keys_in_order.index(key)
            except ValueError:
                return
            item = self.entry_list.item(idx)
            if item is not None:
                item.setText(self._title(key))

    def clear(self):
        """Drop every entry and reset to the placeholder view."""
        self._pages.clear()
        self._completed.clear()
        self._keys_in_order.clear()
        self.entry_list.clear()
        # Tear down every page widget except the placeholder at index
        # 0; the placeholder stays so a subsequent run can reuse it.
        while self.content_stack.count() > 1:
            w = self.content_stack.widget(self.content_stack.count() - 1)
            self.content_stack.removeWidget(w)
            w.deleteLater()
        self.content_stack.setCurrentIndex(0)

    # ---------------------------------------------------------- helpers
    def _trace_label(self, trace: str) -> str:
        # QCheckBox text is plain (no HTML rendering). Use underscore-
        # subscript notation that reads cleanly without markup. The rich
        # variants are still used for the plot axis labels and headers.
        return trace

    def _title(self, key) -> str:
        """Tab title — accepts ``int`` (legacy CHnn) or ``str``
        (verbatim combination label). Appends a green ✓ when the
        run for that key has finished."""
        check = " ✓" if key in self._completed else ""
        if isinstance(key, int):
            return f"CH{key:02d}{check}"
        return f"{key}{check}"

    def _on_visibility_toggled(self, *_):
        vis = self.visibility()
        for page in self._pages.values():
            page.refresh_visibility(
                vis, axis_map=self._axis_map,
                inset_visible=self.inset_enabled(),
                inset_traces=set(self.inset_traces()))

    def _on_entry_changed(self, row: int) -> None:
        """User picked a different list row — swap the stacked content.

        Stack widget index 0 is the placeholder; the page for list
        row ``i`` lives at stack index ``i + 1``. ``row == -1`` (no
        selection) falls back to the placeholder so the right pane
        reads as "no entry selected" instead of staying on whichever
        page was last visible.
        """
        if row < 0 or row >= len(self._keys_in_order):
            self.content_stack.setCurrentIndex(0)
            return
        # +1 because the placeholder occupies stack index 0.
        self.content_stack.setCurrentIndex(row + 1)

    def _on_axis_changed(self, trace: str) -> None:
        """User picked a new option for the trace's dropdown.

        ``userData`` is one of ``AXIS_NA`` / ``AXIS_LEFT`` /
        ``AXIS_RIGHT``; we cache it and trigger a full repaint of
        every channel page (the visibility derives from
        ``axis != AXIS_NA``, so toggling NA on/off effectively shows
        and hides the trace).
        """
        combo = self.axis_combos.get(trace)
        if combo is None:
            return
        new_axis = combo.currentData() or AXIS_LEFT
        if new_axis not in (AXIS_NA, AXIS_LEFT, AXIS_RIGHT):
            new_axis = AXIS_LEFT
        self._axis_map[trace] = new_axis
        self._on_visibility_toggled()

    def _on_inset_toggled(self, on: bool) -> None:
        """Inset checkbox toggled — show/hide the inset plot AND
        enable/disable the multi-select dropdown next to it."""
        self.inset_btn.setEnabled(bool(on))
        self._on_visibility_toggled()

    def _on_inset_traces_changed(self, *_) -> None:
        """A trace was checked / unchecked in the inset menu."""
        self._refresh_inset_button_label()
        self._on_visibility_toggled()

    def _refresh_inset_button_label(self) -> None:
        """Show the user the active inset selection in the button text.

        Empty selection → ``"(pick traces)"`` so the button still
        reads as actionable. Non-empty → comma-joined trace tags.
        """
        picked = self.inset_traces()
        if not picked:
            self.inset_btn.setText("(pick traces)")
        else:
            self.inset_btn.setText(", ".join(picked))

    # --------------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        """Snapshot the global toggles for save/restore across sessions.

        Visibility is encoded inside ``axis_map`` (``AXIS_NA`` = hidden)
        rather than as a separate key — collapses what used to be two
        round-trips into one.
        """
        return {
            "axis_map": self.axis_map(),
            "inset_enabled": self.inset_enabled(),
            "inset_traces": self.inset_traces(),
        }

    def restore_prefs(self, p: dict) -> None:
        """Apply saved toggles. Defensive on every key — a missing or
        mistyped value is ignored so a stale prefs file from a
        previous version never breaks the launch.

        **Backward compat**: pre-AXIS_NA prefs carried a separate
        ``visibility`` dict (bool per trace). When present, we merge
        it into the axis map by forcing hidden traces to ``AXIS_NA``
        so the new format absorbs the legacy state cleanly.
        """
        if not isinstance(p, dict) or not p:
            return
        axes = dict(p.get("axis_map") or {})
        vis = p.get("visibility")
        if isinstance(vis, dict):
            for trace, on in vis.items():
                if trace in ALL_TRACES and not on:
                    axes[trace] = AXIS_NA
        if axes:
            self.set_axis_map(axes)
        if "inset_enabled" in p:
            try:
                self.set_inset_enabled(bool(p["inset_enabled"]))
            except (TypeError, ValueError):
                pass
        traces = p.get("inset_traces")
        if isinstance(traces, (list, tuple, set)):
            self.set_inset_traces(traces)
