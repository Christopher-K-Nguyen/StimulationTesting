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
from .widgets import MetricTable, ScopePlot


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


class _ChannelPage(QtWidgets.QWidget):
    """One sub-tab: scope plot + metric table for a single channel."""

    def __init__(self, channel: int, parent=None):
        super().__init__(parent)
        self.channel = channel
        self.scope = ScopePlot()
        self.metrics = MetricTable()
        self._latest: Optional[Capture] = None
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.addWidget(self.scope, stretch=3)
        v.addWidget(self.metrics, stretch=2)

    def set_capture(self, capture: Capture, visible: Dict[str, bool]):
        """Render a capture into the scope and the metrics table."""
        self._latest = capture
        self._refresh_traces(visible)
        self.metrics.show_capture(capture)

    def refresh_visibility(self, visible: Dict[str, bool]):
        if self._latest is not None:
            self._refresh_traces(visible)

    def _refresh_traces(self, visible: Dict[str, bool]):
        cap = self._latest
        if cap is None: return
        traces: Dict[str, np.ndarray] = {}
        if visible.get(TRACE_VMON, True) and cap.v_mon_v.size:
            traces[f"{TRACE_VMON} (V)"] = cap.v_mon_v
        if visible.get(TRACE_IMON, True) and cap.i_mon_a is not None and cap.i_mon_a.size:
            # Render µA on the same axis as V_mon so the user sees both,
            # at the cost of mixed units. The y-axis label remains
            # "Voltage (V)" for compatibility with the existing
            # ScopePlot widget — refining that is a follow-up.
            traces[f"{TRACE_IMON} (µA)"] = cap.i_mon_a * 1e6
        if visible.get(TRACE_EACT, True) and cap.e_act_v is not None and cap.e_act_v.size:
            traces[f"{TRACE_EACT} (V)"] = cap.e_act_v
        if visible.get(TRACE_ERET, True) and cap.e_ret_v is not None and cap.e_ret_v.size:
            traces[f"{TRACE_ERET} (V)"] = cap.e_ret_v
        # Colour map keyed by the same suffixed names we set above
        colours = {f"{k} (V)": v for k, v in TRACE_COLOURS.items()}
        colours[f"{TRACE_IMON} (µA)"] = TRACE_COLOURS[TRACE_IMON]
        # Clear and replot — ScopePlot.set_traces only appends/updates
        # so toggling visibility off requires a clear() first.
        self.scope.clear()
        self.scope.set_traces(cap.time_us, traces, colors=colours)


class MultiChannelScope(QtWidgets.QWidget):
    """A tab-per-channel scope view with global waveform-visibility toggles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # ----- top bar: visibility checkboxes -----
        self.checks: Dict[str, QtWidgets.QCheckBox] = {}
        bar = QtWidgets.QHBoxLayout()
        bar.addWidget(QtWidgets.QLabel("Show traces:"))
        for trace in ALL_TRACES:
            cb = QtWidgets.QCheckBox(self._trace_label(trace))
            cb.setChecked(True)
            cb.setStyleSheet(f"color: {TRACE_COLOURS[trace]}; font-weight: bold;")
            cb.toggled.connect(self._on_visibility_toggled)
            self.checks[trace] = cb
            bar.addWidget(cb)
        bar.addStretch(1)

        # ----- channel tab widget -----
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setMovable(False)
        self.tabs.setDocumentMode(True)
        self._pages: Dict[int, _ChannelPage] = {}
        self._completed: set = set()

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(bar)
        v.addWidget(self.tabs, stretch=1)

    # ---------------------------------------------------------- public API
    def visibility(self) -> Dict[str, bool]:
        return {t: cb.isChecked() for t, cb in self.checks.items()}

    def set_visibility(self, vis: Dict[str, bool]):
        for t, cb in self.checks.items():
            if t in vis:
                cb.setChecked(bool(vis[t]))

    def ensure_tab(self, channel: int) -> _ChannelPage:
        """Get/create the sub-tab for ``channel``."""
        if channel in self._pages:
            return self._pages[channel]
        page = _ChannelPage(channel)
        self._pages[channel] = page
        self.tabs.addTab(page, self._title(channel))
        return page

    def add_capture(self, capture: Capture, channel: int):
        """Drop a capture into ``channel``'s sub-tab and focus it."""
        page = self.ensure_tab(channel)
        page.set_capture(capture, self.visibility())
        self.tabs.setCurrentWidget(page)

    def mark_completed(self, channel: int):
        self._completed.add(channel)
        if channel in self._pages:
            idx = self.tabs.indexOf(self._pages[channel])
            if idx >= 0:
                self.tabs.setTabText(idx, self._title(channel))

    def clear(self):
        self._pages.clear()
        self._completed.clear()
        while self.tabs.count():
            self.tabs.removeTab(0)

    # ---------------------------------------------------------- helpers
    def _trace_label(self, trace: str) -> str:
        # QCheckBox text is plain (no HTML rendering). Use underscore-
        # subscript notation that reads cleanly without markup. The rich
        # variants are still used for the plot axis labels and headers.
        return trace

    def _title(self, channel: int) -> str:
        check = " ✓" if channel in self._completed else ""
        return f"CH{channel:02d}{check}"

    def _on_visibility_toggled(self, *_):
        vis = self.visibility()
        for page in self._pages.values():
            page.refresh_visibility(vis)
