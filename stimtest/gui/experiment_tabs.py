"""All experiment tabs (VT, SP, LP, PS) share most of their UI, so they live
in a single module to keep things tight.

Architecture
------------
* :class:`_BaseExperimentTab` provides the shared scaffolding: channel grid,
  scope plot, metrics table, log pane, start/stop buttons, hardware handles.
* Four concrete subclasses (:class:`VoltageTransientTab`,
  :class:`ShortPulsingTab`, :class:`LongPulsingTab`,
  :class:`ProgressiveStressTab`) add their experiment-specific parameter
  forms and implement :meth:`start_clicked`, which:

      1. Builds a :class:`PulsePattern` from the form values
      2. Builds a :class:`Configuration` from the channel grid selection
      3. Instantiates the matching :class:`ExperimentRunner`
      4. Hands it to :meth:`_start_runner` which spins up a worker thread

Worker thread pattern
---------------------
The experiment runners block (they wait for hardware, sleep between
captures, etc.). To keep the GUI responsive we run them on a Qt
:class:`QThread`:

      RunnerWorker(runner)             # owns the runner; lives in the worker thread
        ├─ subscribed to runner events
        ├─ emits Qt signals (captured / log_msg / finished)
        └─ run() called via thread.started

The tab is the "main thread" side: it connects to those signals to update
the plot, log, and metrics table. Stop simply calls runner.abort() — the
runner checks ``self.aborted`` between captures and unwinds cleanly.

This means a long-running LP experiment (hours/days) doesn't freeze the GUI
and the user can always abort. It also means experiment progress is
naturally event-driven: the GUI never has to poll.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import (
    DEFAULT_DISCHARGE_DELAY_US, DEFAULT_INTERPHASE_DELAY_US,
    DEFAULT_PHASE_WIDTH_US, DEFAULT_RATE_PPS, DEFAULT_SAVE_DIR,
)
from ..electrode import Configuration, ElectrodeArray, enumerate_combinations
from ..experiments import (
    LongPulsingExperiment, ProgressiveStressExperiment,
    ShortPulsingExperiment, VoltageTransientExperiment,
)
from ..experiments.base import ExperimentEvent, ExperimentRunner
from ..experiments.long_pulsing import LongPulsingPolicy
from ..experiments.progressive_stress import StressPolicy
from ..experiments.short_pulsing import ShortPulsingPolicy
from ..experiments.voltage_transient import RampPolicy
from ..hardware.base import Oscilloscope, Stimulator
from ..persistence import save_session_npz
from ..session import Session, TestParameters
from ..waveforms import PulsePattern
from . import rich
from .channel_selector import ChannelSelector
from .combination_panel import CombinationPanel
from .multichannel_scope import MultiChannelScope
from .pattern_panel import PatternControlPanel
from .pattern_preview import PatternPreview
from .repeating_spinbox import RepeatingDoubleSpinBox, RepeatingSpinBox
from .staircase_plot import StaircasePlot
from .widgets import LogPane, MetricTable


# ---------------------------------------------------------------------------
# Background worker: bridges QThread <-> ExperimentRunner
# ---------------------------------------------------------------------------
class RunnerWorker(QtCore.QObject):
    """QObject wrapper that runs an ExperimentRunner on a background thread.

    Why a wrapper? PyQt threading needs Qt signals to talk back to the GUI
    thread safely. The runner itself is plain Python (so it stays testable
    and CLI-friendly); this class adapts it to Qt by:

      1. Subscribing to the runner's plain-Python event stream
         (``ExperimentEvent`` objects).
      2. Re-emitting selected events as Qt signals (``captured``,
         ``log_msg``, ``finished``) which automatically marshal across
         threads using Qt's queued connection.

    Lifecycle:
      * Tab creates ``RunnerWorker(runner, save_path)``
      * Tab moves it to a fresh ``QThread`` and connects ``thread.started``
        to ``worker.run`` (so ``run`` executes on the worker thread, not
        the GUI thread).
      * Worker emits signals as captures arrive; tab's slots update the
        plot, table, and log.
      * When ``run()`` returns the worker emits ``finished`` and the tab
        cleans up the thread.
    """
    # Emitted once per completed capture. Carries both the Capture and
    # the active channel of the run that produced it, so the tab can
    # route captures into the correct per-channel sub-tab.
    captured = QtCore.pyqtSignal(object, int)
    # Free-form log line for the GUI's log pane
    log_msg = QtCore.pyqtSignal(str)
    # Emitted on a session-save failure so the tab can pop a modal
    # warning. Carries (path, error_message). Without an explicit
    # signal a save failure would only land in the log pane, where
    # a busy user could miss it and assume their data is on disk.
    save_failed = QtCore.pyqtSignal(str, str)
    # Emitted once when the experiment ends (carries the ExperimentResult)
    finished = QtCore.pyqtSignal(object)

    def __init__(self, runner: ExperimentRunner,
                 save_path: Optional[Path] = None,
                 auto_export_xlsx: bool = False,
                 auto_save_plots: bool = False,
                 auto_save_plots_fmt: str = "tif",
                 email_notifications: bool = False,
                 user_email: str = "",
                 user_name: str = "",
                 session_subject: str = ""):
        super().__init__()
        self.runner = runner
        self.save_path = save_path
        # When True, write a Gamry-DTA-style .xlsx workbook alongside
        # the .npz on every successful run. Failures are logged but
        # don't fail the run (the .npz is still on disk; the user can
        # always re-export from the Results tab).
        self.auto_export_xlsx = bool(auto_export_xlsx)
        # Auto-save channel/combination plots after a successful run.
        # Failures are logged and don't fail the run — the .npz is the
        # canonical output and the user can always re-export from the
        # Results tab.
        self.auto_save_plots = bool(auto_save_plots)
        self.auto_save_plots_fmt = str(auto_save_plots_fmt or "tif")\
            .lower().lstrip(".")
        # Email notification settings — mirrored from the MATLAB
        # ``sendEmail.m`` / ``sendError.m`` workflow. The actual send
        # only fires when ``email_notifications`` is True AND
        # ``user_email`` is set AND SMTP credentials are configured
        # via env vars or ``~/.stimtest/email_config.json``.
        self.email_notifications = bool(email_notifications)
        self.user_email = str(user_email or "")
        self.user_name = str(user_name or "")
        self.session_subject = str(session_subject or "")
        # Wire the plain-Python event stream into our Qt signals
        runner.subscribe(self._on_event)

    def _on_event(self, ev: ExperimentEvent):
        # Called from the worker thread (the runner's thread) — emitting Qt
        # signals from here is safe because Qt automatically marshals them
        # across to the GUI thread via the default queued connection.
        if ev.kind == "capture" and ev.capture is not None:
            ch = ev.run.configuration.active if ev.run is not None else -1
            self.captured.emit(ev.capture, int(ch))
        if ev.message:
            self.log_msg.emit(ev.message)

    @QtCore.pyqtSlot()
    def run(self):
        # This is what actually runs on the worker thread — invoked by the
        # ``thread.started`` signal we connect in ``_BaseExperimentTab``.
        # It blocks until the experiment is complete or aborted.
        import time as _time
        run_start = _time.time()
        try:
            result = self.runner.run()
        except (ValueError, RuntimeError) as e:
            # Pre-flight or hardware-side validation failure. Surface the
            # message in the log pane and synthesise an empty/aborted
            # result so the GUI's ``_on_finished`` slot still fires and
            # restores the Start button. Without this, the worker thread
            # would die silently and leave the UI stuck on "Stop".
            self.log_msg.emit(f"Run aborted: {e}")
            from ..experiments.base import ExperimentResult
            result = ExperimentResult(
                session=self.runner.session,
                aborted=True,
                error=str(e),
            )
            # Mirror the MATLAB ``sendError.m`` notification — only
            # if the user opted in, set an email, and SMTP creds are
            # configured.
            self._maybe_send_failure_email(
                error_message=str(e),
                elapsed_seconds=_time.time() - run_start)
            self.finished.emit(result)
            return
        # Persist the session immediately so partial runs aren't lost.
        # If the save fails (disk full, permission error, bad path) we
        # MUST surface it loudly — without the popup the user sees
        # "Run finished" in the log and assumes their data is on disk
        # when it isn't. Marking the result as ``aborted`` with an
        # error string also propagates into ``_on_finished`` so the
        # UI flow doesn't silently treat a save failure as success.
        if self.save_path is not None:
            try:
                save_session_npz(result.session, self.save_path)
                self.log_msg.emit(f"Saved session to {self.save_path}")
                # Optional: also write the Gamry-style .xlsx workbook
                # next to the .npz. Doesn't fail the run on error —
                # the canonical .npz is already on disk and the user
                # can always re-export from the Results tab.
                if self.auto_export_xlsx:
                    xlsx_path = Path(self.save_path).with_suffix(".xlsx")
                    try:
                        from ..persistence import save_session_xlsx
                        save_session_xlsx(result.session, xlsx_path)
                        self.log_msg.emit(f"Auto-exported .xlsx to {xlsx_path}")
                    except Exception as e:
                        self.log_msg.emit(
                            f"Auto-export to .xlsx failed: "
                            f"{type(e).__name__}: {e}. "
                            f"The .npz is still saved at {self.save_path}; "
                            f"use the Results tab's Export .xlsx button to "
                            f"retry manually.")
                # Optional: also drop one plot per channel/combination
                # next to the .npz. Same format menu as the Results
                # tab's Export plot button (PNG / JPEG / TIFF / SVG).
                if self.auto_save_plots:
                    plot_dir = Path(self.save_path).parent
                    try:
                        from ..plotting import export_session_plots
                        written = export_session_plots(
                            result.session, plot_dir,
                            fmt=self.auto_save_plots_fmt)
                        self.log_msg.emit(
                            f"Auto-saved {len(written)} plot(s) to "
                            f"{plot_dir} (.{self.auto_save_plots_fmt}).")
                    except Exception as e:
                        self.log_msg.emit(
                            f"Auto-save plots failed: "
                            f"{type(e).__name__}: {e}. "
                            f"The .npz is still saved at {self.save_path}; "
                            f"use the Results tab's Export plot button to "
                            f"retry manually.")
            except Exception as e:
                self.log_msg.emit(
                    f"⚠ SAVE FAILED — captured data NOT written to disk. "
                    f"({type(e).__name__}: {e}) "
                    f"Path: {self.save_path}")
                # Tag the result so the GUI's _on_finished knows the
                # run "completed but data is at risk" — don't overwrite
                # a runner-level abort if one is already set.
                if not result.aborted:
                    result.aborted = True
                    result.error = f"Save failed: {e}"
                # Emit the dedicated signal so the tab can pop a modal
                # on the GUI thread (Qt's queued-connection delivery
                # marshals it across automatically — no manual
                # invokeMethod needed).
                self.save_failed.emit(str(self.save_path),
                                      f"{type(e).__name__}: {e}")
        # Notification email — same opt-in gate as the MATLAB version.
        # Successful run -> "completed" message; aborted/save-failed
        # -> failure message. The runner-level abort (caught above)
        # already sent its own failure mail, so don't duplicate.
        elapsed_s = _time.time() - run_start
        if not result.aborted:
            self._maybe_send_completion_email(elapsed_seconds=elapsed_s)
        elif result.error and not result.error.startswith("Save failed:"):
            # Save-failed branch already logged loudly + popped a
            # modal; we only mail when the abort came from somewhere
            # other than the save (the MATLAB sendError.m equivalent).
            self._maybe_send_failure_email(
                error_message=result.error or "experiment aborted",
                elapsed_seconds=elapsed_s)
        self.finished.emit(result)

    def _maybe_send_completion_email(self, *, elapsed_seconds: float) -> None:
        """Fire the success email if the user opted in and SMTP works."""
        if not (self.email_notifications and self.user_email):
            return
        try:
            from ..notifications import send_completion_email
            ok = send_completion_email(
                to_email=self.user_email,
                recipient_name=self.user_name,
                subject_name=self.session_subject,
                test_name=getattr(self.runner.session.test, "experiment", ""),
                elapsed_seconds=elapsed_seconds,
                attachments=[self.save_path] if self.save_path else None,
            )
            self.log_msg.emit("Completion email sent." if ok
                              else "Completion email skipped (SMTP not configured).")
        except Exception as e:
            self.log_msg.emit(f"Email notification failed: {e}")

    def _maybe_send_failure_email(self, *, error_message: str,
                                  elapsed_seconds: float) -> None:
        """Fire the failure email if the user opted in and SMTP works."""
        if not (self.email_notifications and self.user_email):
            return
        try:
            from ..notifications import send_error_email
            ok = send_error_email(
                to_email=self.user_email,
                recipient_name=self.user_name,
                subject_name=self.session_subject,
                test_name=getattr(self.runner.session.test, "experiment", ""),
                error_message=error_message,
                elapsed_seconds=elapsed_seconds,
                attachments=[self.save_path] if (self.save_path
                                                 and self.save_path.exists())
                            else None,
            )
            self.log_msg.emit("Failure email sent." if ok
                              else "Failure email skipped (SMTP not configured).")
        except Exception as e:
            self.log_msg.emit(f"Email notification failed: {e}")


# ---------------------------------------------------------------------------
# Shared base tab
# ---------------------------------------------------------------------------
class _BaseExperimentTab(QtWidgets.QWidget):
    """Common scaffolding: HW handles, channel picker, plot, metrics, log, controls.

    The experiment tab is itself a two-page sub-tab widget:

      * **Parameters** — pulse pattern, experiment-specific parameters,
        Channel selection (selector + Configurations to run), Start/Stop.
      * **Experiment** — the multi-channel live scope view with one tab
        per electrode/combination.

    The log pane sits below the inner tabs so it's always visible
    regardless of which sub-tab is up. Subclasses build their
    experiment-specific parameter widget(s) and then call
    :meth:`_assemble_pages(params_box)` to finalize the layout.
    """

    #: True for pulsing experiments — only one combo can run per session.
    SINGLE_CONFIG = False

    #: Emitted with True when a run starts and False when it ends.
    #: MainWindow uses this to lock controls outside the running tab
    #: (Setup tab, hardware connection) so the user can't reconfigure
    #: a session while it's mid-flight.
    runStateChanged = QtCore.pyqtSignal(bool)

    def __init__(self, array: ElectrodeArray, parent=None):
        super().__init__(parent)
        self._array = array
        self._stim: Optional[Stimulator] = None
        self._scope: Optional[Oscilloscope] = None
        self._aliases = {"vmon": "CH1", "imon": "CH2", "eret": "CH3", "eact": "CH4"}
        self._save_dir = Path(DEFAULT_SAVE_DIR)
        # Acquisition mode + averaging count — pushed by the Setup tab
        # via :meth:`set_acquisition` and applied to the scope just
        # before each run starts.
        self._acq_mode: str = "AVERAGE"
        self._acq_n_avg: int = 16
        # User-overridable water-window limits + grace tolerance.
        # Filled in by the Setup tab on first emission; the VT runner
        # reads them at start time. Defaults match the SIROF catalog
        # row so a tab that never receives a Setup update still does
        # something sensible.
        self._cathodic_limit_v: float = -0.6
        self._anodic_limit_v: float = +0.8
        self._polarization_tolerance_v: float = 0.020
        # Auto-export the saved session as a Gamry-style .xlsx alongside
        # the .npz on every successful run. Default off; flipped via
        # the Setup tab's checkbox (see :pyattr:`SetupTab.autoExportXlsxChanged`).
        self._auto_export_xlsx: bool = False
        # Auto-save plots toggle + format (e.g. "tif"/"png"/"svg"/"jpg").
        # Pushed by ``MainWindow`` from ``SetupTab.autoSavePlotsChanged``;
        # consumed by ``_start_runner`` when constructing the
        # ``RunnerWorker``.
        self._auto_save_plots: bool = False
        self._auto_save_plots_fmt: str = "tif"
        # Email notifications — opt-in via the Setup tab. Cached here
        # and read at start time, so a toggle mid-session affects the
        # NEXT run, not the in-flight one. Recipient identity + session
        # subject string are also cached here; ``_start_runner`` hands
        # them to the worker which forwards to ``send_completion_email``
        # / ``send_error_email``.
        self._email_notifications: bool = False
        self._user_email: str = ""
        self._user_name: str = ""
        self._session_subject: str = ""
        self._worker_thread: Optional[QtCore.QThread] = None
        self._worker: Optional[RunnerWorker] = None
        self._runner: Optional[ExperimentRunner] = None
        # Multi-config queue. Populated by ``start_clicked`` when the
        # user has selected several single-active configurations
        # (Monopolar / Common Ground / Partial Common Ground); each
        # one runs sequentially via ``_on_finished`` chaining into
        # ``_start_next_pending``. Empty between runs and for the
        # multipolar single-combo case.
        self._pending_configs: list = []

        self.channel_grid = ChannelSelector(array)
        self.multichan_scope = MultiChannelScope()
        # Side-panel metric measurements — shows the latest capture's
        # full metric set (Q_inj, V_d, R_a, E_pol, …) on the right of
        # the Experiment sub-tab. Distinct from the per-channel
        # metric tables inside ``multichan_scope``: this side panel
        # always reflects the most-recent capture across all channels,
        # so the user can read off the live numbers without flipping
        # between channel sub-tabs.
        self.metrics_side = MetricTable()
        self.log_pane = LogPane()
        self.pattern_panel = PatternControlPanel(title="Pulse pattern")
        self.pattern_preview = PatternPreview()
        self.pattern_panel.patternChanged.connect(self.pattern_preview.set_pattern)
        # Hide the charge-balance summary in modes where the user can't
        # control balance manually (symmetric, triphasic, or auto-adjust).
        self.pattern_panel.balanceWarningVisibility.connect(
            self.pattern_preview.set_balance_visible)
        QtCore.QTimer.singleShot(0, lambda: self.pattern_preview.set_pattern(
            self.pattern_panel.pattern()))

        # Combinations panel — every experiment now has one, so the user
        # can pick a multipolar configuration uniformly. SINGLE_CONFIG
        # subclasses (pulsing) constrain it to one combo at a time.
        self.combo_panel = CombinationPanel(single_mode=self.SINGLE_CONFIG)
        self.channel_grid.activesChanged.connect(self.combo_panel.set_actives)
        self.channel_grid.globalReturnChanged.connect(self.combo_panel.set_global_return)
        self.combo_panel.combinationHovered.connect(
            lambda active, rets: self.channel_grid.set_highlight(
                active if active > 0 else None, rets))
        self.combo_panel.set_array(array)

        self.start_btn = QtWidgets.QPushButton("Start")
        self.start_btn.setEnabled(False)
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_clicked)
        self.pause_btn.toggled.connect(self.pause_toggled)
        self.stop_btn.clicked.connect(self.stop_clicked)

        # Inner sub-tab widget — Parameters / <experiment label>. The
        # second tab takes its title from ``EXPERIMENTS[code].tab_title``
        # so the user reads "Voltage Transient" / "Short-Term Pulsing"
        # / etc. instead of a generic "Experiment" label. The subclass's
        # ``experiment_type()`` is dispatched here even though we're
        # inside the base __init__ — Python has finished building the
        # subclass class by the time the base __init__ runs, so the
        # override resolves correctly.
        self.inner_tabs = QtWidgets.QTabWidget()
        self.params_page = QtWidgets.QWidget()
        self.experiment_page = QtWidgets.QWidget()
        from ..config import EXPERIMENTS
        try:
            exp_title = EXPERIMENTS[self.experiment_type()].tab_title
        except (KeyError, NotImplementedError):
            exp_title = "Experiment"
        self.inner_tabs.addTab(self.params_page, "Parameters")
        self.inner_tabs.addTab(self.experiment_page, exp_title)

        # Start / Pause / Stop are placed at the BOTTOM of the experiment
        # widget, outside the inner sub-tabs, so they're always visible
        # regardless of whether the user is on the Parameters or
        # Experiment sub-tab.
        button_row_w = QtWidgets.QWidget()
        button_row = QtWidgets.QHBoxLayout(button_row_w)
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.pause_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addStretch(1)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.addWidget(self.inner_tabs, stretch=1)
        outer.addWidget(button_row_w)
        # The log pane is no longer placed inside the experiment tab —
        # MainWindow owns a single shared log panel pinned to the bottom
        # of the GUI so it's visible from every tab. The ``self.log_pane``
        # reference is replaced by MainWindow with the shared instance
        # after construction; until then it points at a local placeholder.

    # ----- layout assembly --------------------------------------------------
    def _assemble_pages(self, params_box: QtWidgets.QWidget):
        """Lay out the Parameters and Experiment sub-tabs.

        Three structural choices that show up here for a reason:

        * **QScrollArea** wraps both columns of the Parameters page so
          tall forms (pattern panel + experiment-specific params) stay
          accessible on smaller screens — no more "stretch the window
          to see the inputs".
        * **QSplitter** between the left (params) and right (channel +
          combos) columns means the user can drag the divider to give
          either side more horizontal room. Same for the
          channel-grid / combos split inside the right column.
        * **Pause / Stop** sit on the same row as Start; Pause is a
          checkable toggle (Pause → Resume) and forwards to a runner
          ``pause(bool)`` method when the experiment runner supports it.
        """
        # ----- Parameters page: scrollable splitter -----
        pp = QtWidgets.QVBoxLayout(self.params_page)
        pp.setContentsMargins(4, 4, 4, 4)

        # Left column: just the parameters group, in a scroll area.
        # Start / Pause / Stop now live at the bottom of the experiment
        # widget (set up in __init__) so they're visible on both
        # Parameters and Experiment sub-tabs.
        left_inner = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_inner)
        left.addWidget(params_box)
        left.addStretch(1)
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidget(left_inner)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # The params box has wide rows (label + spinbox + suffix) that
        # clip if the splitter shrinks the column too much. Pin a hard
        # floor so the user can't drag the divider into a useless state.
        left_scroll.setMinimumWidth(420)

        # Right column: channel-selection hint, then a vertical splitter
        # between the grid and the combo panel so either can grow.
        right_inner = QtWidgets.QWidget()
        right = QtWidgets.QVBoxLayout(right_inner)
        right.addWidget(QtWidgets.QLabel(
            "<b>Channel selection</b> &nbsp;—&nbsp; "
            "click = toggle active &nbsp;·&nbsp; "
            "Ctrl + click = remove &nbsp;·&nbsp; "
            "Global Return square = off-array counter"
        ))
        v_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        v_split.addWidget(self.channel_grid)
        v_split.addWidget(self.combo_panel)
        v_split.setStretchFactor(0, 2)
        v_split.setStretchFactor(1, 5)
        right.addWidget(v_split, stretch=1)
        self._right_v_split = v_split
        right_scroll = QtWidgets.QScrollArea()
        right_scroll.setWidget(right_inner)
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        h_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        h_split.addWidget(left_scroll)
        h_split.addWidget(right_scroll)
        h_split.setStretchFactor(0, 1)
        h_split.setStretchFactor(1, 2)
        # Don't let the user drag the divider so far that the params
        # column collapses to zero — it always honours the min-width.
        h_split.setCollapsible(0, False)
        h_split.setCollapsible(1, False)
        pp.addWidget(h_split, stretch=1)
        self._params_h_split = h_split

        # ----- Experiment page: live scope view + metrics side panel -----
        ep_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self._experiment_h_split = ep_split
        ep_split.addWidget(self.multichan_scope)
        # Wrap the metrics table in a group box so its purpose is clear,
        # and pin a min-width so the splitter can't collapse it.
        metrics_box = QtWidgets.QGroupBox("Metric measurements")
        mv = QtWidgets.QVBoxLayout(metrics_box)
        mv.setContentsMargins(4, 6, 4, 4)
        mv.addWidget(self.metrics_side)
        metrics_box.setMinimumWidth(280)
        ep_split.addWidget(metrics_box)
        ep_split.setStretchFactor(0, 3)
        ep_split.setStretchFactor(1, 1)
        ep_split.setCollapsible(0, False)
        ep_split.setCollapsible(1, False)

        ep = QtWidgets.QVBoxLayout(self.experiment_page)
        ep.setContentsMargins(4, 4, 4, 4)
        ep.addWidget(ep_split, stretch=1)

    # ----- subclasses override ----
    def build_params_form(self) -> QtWidgets.QWidget:
        raise NotImplementedError

    def build_runner(self, session: Session) -> ExperimentRunner:
        raise NotImplementedError

    def experiment_type(self) -> str:
        raise NotImplementedError

    # ----- API used by MainWindow ----
    def set_array(self, array: ElectrodeArray):
        self._array = array
        # ChannelSelector knows how to repaint with a new array, no widget
        # swap needed. Look up the device's layout if available.
        layout = self._device_layout_for(array.name)
        self.channel_grid.set_array(array, layout=layout)
        # Push the array into the combination panel too (only set if the
        # subclass added one — VT does, the others don't yet).
        if hasattr(self, "combo_panel") and self.combo_panel is not None:
            self.combo_panel.set_array(array)

    @staticmethod
    def _device_layout_for(name: str) -> str:
        from ..config import DEVICES
        d = DEVICES.get(name)
        return getattr(d, "layout", "rect") if d is not None else "rect"

    def set_aliases(self, aliases: dict): self._aliases = dict(aliases)

    def set_auto_export_xlsx(self, on: bool):
        """Cache whether the next run should also write an .xlsx after
        the .npz. ``RunnerWorker`` reads this through the constructor
        kwarg passed by ``_start_runner``.
        """
        self._auto_export_xlsx = bool(on)

    def set_auto_save_plots(self, on: bool, fmt: str):
        """Cache whether the next run should drop a plot per
        channel/combination next to the .npz, and in what format
        (one of ``png``/``jpg``/``tif``/``svg``)."""
        self._auto_save_plots = bool(on)
        f = (fmt or "").lower().lstrip(".")
        if f:
            self._auto_save_plots_fmt = f

    def set_email_notifications(self, on: bool):
        """Cache whether the next run should email the user on
        success/failure (mirrors MATLAB ``sendEmail.m`` /
        ``sendError.m``)."""
        self._email_notifications = bool(on)

    def set_user_identity(self, name: str, email: str):
        """Cache the recipient's name + email for completion / failure
        notifications. Pushed from the Setup tab via
        :pyattr:`SetupTab.userIdentityChanged`."""
        self._user_name = str(name or "").strip()
        self._user_email = str(email or "").strip()

    def set_session_subject(self, subject: str):
        """Cache the raw Session field for the email subject line."""
        self._session_subject = str(subject or "").strip()

    def set_potential_limits(self, cathodic_v: float, anodic_v: float,
                             tolerance_v: float):
        """Cache the user-edited water-window limits + tolerance.

        The Setup tab's spinboxes drive these via
        :pyattr:`SetupTab.potentialLimitsChanged`; the VT runner reads
        them at start time so a fresh-typed value takes effect on the
        next run without the user having to reload.
        """
        self._cathodic_limit_v = float(cathodic_v)
        self._anodic_limit_v = float(anodic_v)
        self._polarization_tolerance_v = max(0.0, float(tolerance_v))

    def set_acquisition(self, mode: str, n_avg: int):
        """Stash the acquisition mode + averaging count for the next run.

        ``_start_runner`` applies them to the scope just before
        spinning up the worker thread; the runner inherits whatever
        we set here and won't reach for hard-coded defaults.
        """
        self._acq_mode = str(mode or "AVERAGE")
        try:
            self._acq_n_avg = int(n_avg)
        except (TypeError, ValueError):
            self._acq_n_avg = 16

    def set_save_dir(self, p: Path): self._save_dir = Path(p)

    def set_hardware(self, stim: Stimulator, scope: Oscilloscope):
        self._stim = stim; self._scope = scope
        self.start_btn.setEnabled(True)

    def clear_hardware(self):
        self._stim = None; self._scope = None
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)

    # ----- internal ----
    def _on_selection_changed(self, active: int, returns: list):
        # Kept for backward compat with code that connected to it
        self.log_pane.log(f"Selected: active={active}, returns={returns}")

    def _build_pattern(self, polarity: int, triphasic: bool,
                       starting_amp_ua: float, phase_us: float,
                       interphase_us: float, discharge_us: float,
                       rate_pps: float) -> PulsePattern:
        return PulsePattern.rect(
            polarity=polarity, triphasic=triphasic,
            amplitude_ua=starting_amp_ua, phase_width_us=phase_us,
            interphase_us=interphase_us, discharge_us=discharge_us,
            rate_hz=rate_pps,
        )

    def _connect_preview(self, *controls):
        """Wire any number of param controls to refresh the live pattern preview.

        Each control should expose a ``valueChanged`` (spinbox) or
        ``currentTextChanged`` (combo box) signal. We pick whichever
        is available so callers can pass a heterogeneous mix.
        """
        for c in controls:
            sig = (getattr(c, "valueChanged", None)
                   or getattr(c, "currentTextChanged", None)
                   or getattr(c, "currentIndexChanged", None))
            if sig is not None:
                sig.connect(self._refresh_preview)
        # Initial render
        QtCore.QTimer.singleShot(0, self._refresh_preview)

    def _refresh_preview(self, *_):
        """Concrete tabs override this to push their current params into the preview."""
        pass

    # -------------------------------------------------------------- prefs
    #: Concrete tabs list the names of their parameter widgets here.
    #: ``current_prefs`` and ``restore_prefs`` walk this list and treat
    #: each attribute as a QSpinBox / QDoubleSpinBox / QComboBox /
    #: QCheckBox automatically — no per-tab boilerplate needed.
    PREF_FIELDS: tuple = ()

    def current_prefs(self) -> dict:
        out = {}
        for name in self.PREF_FIELDS:
            w = getattr(self, name, None)
            if w is None: continue
            if isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                out[name] = w.value()
            elif isinstance(w, QtWidgets.QComboBox):
                out[name] = w.currentText()
            elif isinstance(w, QtWidgets.QCheckBox):
                out[name] = w.isChecked()
            elif isinstance(w, QtWidgets.QLineEdit):
                out[name] = w.text()
        # Pattern panel state — sub-dict so the keys can't collide with
        # the field-walked ones above.
        out["pattern"] = self.pattern_panel.current_prefs()
        return out

    def restore_prefs(self, p: dict):
        if not p: return
        for name in self.PREF_FIELDS:
            if name not in p: continue
            w = getattr(self, name, None)
            if w is None: continue
            try:
                if isinstance(w, (QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox)):
                    w.setValue(float(p[name]) if isinstance(w, QtWidgets.QDoubleSpinBox)
                               else int(p[name]))
                elif isinstance(w, QtWidgets.QComboBox):
                    w.setCurrentText(str(p[name]))
                elif isinstance(w, QtWidgets.QCheckBox):
                    w.setChecked(bool(p[name]))
                elif isinstance(w, QtWidgets.QLineEdit):
                    w.setText(str(p[name]))
            except (TypeError, ValueError):
                pass
        if "pattern" in p:
            self.pattern_panel.restore_prefs(p["pattern"])
        # Nudge preview to reflect the loaded values
        QtCore.QTimer.singleShot(0, self._refresh_preview)

    # -------------------------------------------------------------- view state
    def view_state(self) -> dict:
        """Snapshot the splitter positions for this tab.

        Three splitters live inside an experiment tab: the params-page
        horizontal split (params column vs. channel/combo column), the
        right-column vertical split between the channel grid and the
        combo panel, and the experiment-page horizontal split between
        the live scope view and the metrics side panel. Each is
        recorded by name so a future addition doesn't break old prefs.
        """
        return {
            "params_h_split": list(self._params_h_split.sizes()),
            "right_v_split":  list(self._right_v_split.sizes()),
            "experiment_h_split": list(self._experiment_h_split.sizes()),
        }

    def restore_view_state(self, view: dict):
        if not view:
            return
        for key, splitter in (
            ("params_h_split",     self._params_h_split),
            ("right_v_split",      self._right_v_split),
            ("experiment_h_split", self._experiment_h_split),
        ):
            sizes = view.get(key)
            if not isinstance(sizes, (list, tuple)):
                continue
            try:
                splitter.setSizes([int(s) for s in sizes])
            except (TypeError, ValueError):
                pass

    def _start_runner(self, runner: ExperimentRunner, save_name: str):
        if self._scope is not None:
            self._scope.configure_channels(self._aliases)
            # Apply the user's chosen acquisition mode + count BEFORE
            # the runner starts so the runner inherits the right state.
            try:
                self._scope.set_acquisition_mode(self._acq_mode,
                                                 n_avg=self._acq_n_avg)
            except Exception as e:
                self.log_pane.log(f"set_acquisition_mode failed: {e}")
        self._save_dir.mkdir(parents=True, exist_ok=True)
        save_path = self._save_dir / save_name
        self._worker = RunnerWorker(
            runner, save_path=save_path,
            auto_export_xlsx=self._auto_export_xlsx,
            auto_save_plots=self._auto_save_plots,
            auto_save_plots_fmt=self._auto_save_plots_fmt,
            email_notifications=self._email_notifications,
            user_email=self._user_email,
            user_name=self._user_name,
            session_subject=self._session_subject)
        self._worker_thread = QtCore.QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.captured.connect(self._on_capture)
        self._worker.log_msg.connect(self.log_pane.log)
        self._worker.save_failed.connect(self._on_save_failed)
        self._worker.finished.connect(self._on_finished)
        self._runner = runner
        # Lock all parameter inputs (this tab + Setup tab + hardware)
        # for the duration of the run. Tab-bar selection stays enabled
        # so the user can flip between Parameters / Experiment / Results
        # views while the worker is running.
        self._set_locked(True)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        # Pause is only meaningful while a run is in flight. Reset to
        # the un-paused state for a fresh run.
        self.pause_btn.setEnabled(True)
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText("Pause")
        self.pause_btn.blockSignals(False)
        self.multichan_scope.clear()
        # Auto-switch to the Experiment sub-tab so the user sees the
        # live plots come in without having to click over.
        self.inner_tabs.setCurrentWidget(self.experiment_page)
        self._worker_thread.start()

    @QtCore.pyqtSlot(object, int)
    def _on_capture(self, capture, channel: int):
        # Route the capture into the per-channel sub-tab. Channel <= 0
        # means we couldn't recover the active channel from the event,
        # so pick a sentinel that's still distinct (-1) and label it
        # generically. Always update; per-trace visibility is enforced
        # by MultiChannelScope based on the global toggle bar.
        self.multichan_scope.add_capture(capture, channel if channel > 0 else -1)
        # Mirror the latest capture's metrics into the side panel so
        # the user can read live numbers without switching channel tabs.
        self.metrics_side.show_capture(capture)

    @QtCore.pyqtSlot(str, str)
    def _on_save_failed(self, path: str, err: str):
        """Pop a modal when the worker fails to write the .npz file.

        The save error is also already in the log pane (with a ⚠
        marker) and the result.error field is set to "Save failed:
        …", but a busy user can miss the log line; the modal makes
        sure the failure is acknowledged before the next run.
        """
        QtWidgets.QMessageBox.critical(
            self, "Save failed",
            f"Could not write the captured session to:\n{path}\n\n{err}\n\n"
            f"The data is still in memory but will be lost when you start "
            f"the next run. Pick a different save path on the Setup tab "
            f"and re-save manually if you need to keep this trace.")

    @QtCore.pyqtSlot(object)
    def _on_finished(self, result):
        self.log_pane.log(f"Experiment finished. Captures: {len(result.captures)}.")
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText("Pause")
        self.pause_btn.blockSignals(False)
        self.start_btn.setEnabled(self._stim is not None)
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
        self._worker = None
        self._runner = None
        # If a sequential queue has more configs lined up, mark the
        # active channel as completed and chain to the next entry —
        # keeping the params locked across the gap so the user never
        # sees inputs become editable mid-queue. If a run aborted
        # (preflight failure or user Stop), drain the queue so we
        # don't keep firing failed runs.
        if getattr(result, "aborted", False):
            self._pending_configs = []
        if self._pending_configs:
            try:
                cfg_done = result.session.test.configuration
                self.channel_grid.set_completed(
                    list(self.channel_grid._completed) + [cfg_done.active])
            except Exception:
                pass
            self._set_locked(True)
            n_left = len(self._pending_configs)
            self.log_pane.log(
                f"{n_left} configuration(s) remaining — starting next.")
            QtCore.QTimer.singleShot(500, self._start_next_pending)
        else:
            # All configs done — re-enable inputs.
            self._set_locked(False)

    def _start_next_pending(self):
        """Pop the next pending Configuration and start it.

        Subclasses that support multi-config sequential runs override
        this. The base implementation is a no-op so a subclass that
        never queues (current implementation: VT) doesn't need to do
        anything special.
        """
        return

    # ---- run-lock plumbing -------------------------------------------
    def _set_locked(self, locked: bool):
        """Lock or unlock parameter inputs for the duration of a run.

        Disables the entire Parameters sub-tab content (pattern panel,
        experiment-specific params, channel selector, combination
        panel) plus the Stop button is left enabled by ``_start_runner``
        / ``_on_finished``. The QTabWidget itself is not touched, so
        the user can still switch between Parameters / Experiment.
        Also emits :pyattr:`runStateChanged` so MainWindow can lock
        controls outside this tab (Setup, hardware connection).
        """
        self.params_page.setEnabled(not locked)
        self.runStateChanged.emit(locked)

    def stop_clicked(self):
        if self._runner is not None:
            self._runner.abort()
            self.log_pane.log("Stop requested.")

    def pause_toggled(self, paused: bool):
        """Toggle pulsing on/off without ending the run.

        Forwards to ``runner.pause(bool)`` if the runner supports it,
        otherwise just logs the request — actual pause support inside
        each experiment runner is a follow-up.
        """
        runner = self._runner
        if runner is None:
            return
        pause_fn = getattr(runner, "pause", None)
        if callable(pause_fn):
            try:
                pause_fn(paused)
            except Exception as e:
                self.log_pane.log(f"Pause failed: {e}")
        self.pause_btn.setText("Resume" if paused else "Pause")
        self.log_pane.log("Pause requested." if paused else "Resume requested.")

    # ---- MUST be implemented by subclass ----
    def start_clicked(self):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Voltage Transient tab
# ---------------------------------------------------------------------------
class VoltageTransientTab(_BaseExperimentTab):
    # VT mode constants — saved in prefs JSON
    # Renamed from "Fixed current" to "Fixed current" — clearer
    # against "Fixed charge density" since both refer to a setpoint
    # type rather than the abstract pulse-shape parameter. Old prefs
    # containing the legacy string are silently ignored on restore
    # (the combo stays at its default).
    MODE_FIXED = "Fixed current"
    MODE_FIXED_QD = "Fixed charge density"
    # Renamed from "Maximum charge-injection" to plain "Maximum" —
    # shorter dropdown row, easier to scan against the two Fixed
    # variants. Old prefs containing the long string are silently
    # ignored on restore (combo stays at the default).
    MODE_MAX = "Maximum"
    STRAT_INCR = "Fixed increment"
    STRAT_REGR = "Adaptive (regression)"
    STRAT_PRED = "Predictive (ML)"

    def __init__(self, array, parent=None):
        super().__init__(array, parent)

        # ----- mode + strategy -----
        # Mode picks whether to ramp at all. Fixed = run a single pulse
        # train at the panel's amplitude (used for quick sanity checks and
        # pre/post-pulsing characterization); Maximum = ramp to find the
        # water-window threshold and report max Q_inj.
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems([self.MODE_FIXED, self.MODE_FIXED_QD, self.MODE_MAX])
        self.mode_combo.setCurrentText(self.MODE_MAX)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        # Live Q_ph readout for Fixed-amplitude mode — pattern panel
        # drives it via patternChanged, so the label tracks every
        # amplitude / phase-width tweak in real time.
        self.fixed_qph_label = QtWidgets.QLabel()
        self.fixed_qph_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.fixed_qph_label.setStyleSheet(
            "color: #1565c0; font-weight: bold; padding: 2px 6px;"
        )
        self.pattern_panel.patternChanged.connect(self._refresh_fixed_qph)
        # Ramp toggle for the Fixed modes — when checked the runner
        # walks from start_ua to max_ua (or start_qinj→max_qinj for the
        # charge-density mode) using the configured step.
        self.fixed_ramp_check = QtWidgets.QCheckBox("Ramp (sweep through values)")
        self.fixed_ramp_check.setChecked(False)
        self.fixed_ramp_check.toggled.connect(self._on_strategy_changed)
        # Target charge density for the Fixed-Q_inj mode. Range covers
        # everything from sub-clinical (0.001 mC/cm²) to past the SIROF
        # safety limit (~5 mC/cm²) so the user can scan beyond the
        # water window if they're characterising a new coating.
        self.qinj_mc = RepeatingDoubleSpinBox()
        self.qinj_mc.setRange(0.001, 100.0); self.qinj_mc.setDecimals(3)
        self.qinj_mc.setSingleStep(0.05); self.qinj_mc.setValue(0.5)
        self.qinj_mc.setSuffix(" mC/cm²")
        # Strategy is only meaningful in Maximum mode.
        self.strategy_combo = QtWidgets.QComboBox()
        self.strategy_combo.addItems([self.STRAT_INCR, self.STRAT_REGR, self.STRAT_PRED])
        self.strategy_combo.currentTextChanged.connect(self._on_strategy_changed)

        # ----- ramp policy controls (Maximum mode) -----
        # Pattern shape comes from self.pattern_panel; start_ua is the
        # *initial* amplitude the ramp policy walks up from.
        # 0.1 µA resolution everywhere — matches the Plexon stim hardware.
        # 1000 µA ceiling matches the PlexStim 2.0 current-source rails.
        from ..config import STIM_CURRENT_RESOLUTION_UA, STIM_MAX_AMPLITUDE_UA
        _step = STIM_CURRENT_RESOLUTION_UA
        _max = STIM_MAX_AMPLITUDE_UA
        self.start_ua = RepeatingDoubleSpinBox()
        self.start_ua.setRange(_step, _max); self.start_ua.setSingleStep(_step)
        self.start_ua.setDecimals(1); self.start_ua.setValue(5.0); self.start_ua.setSuffix(" µA")
        self.coarse_ua = RepeatingDoubleSpinBox()
        self.coarse_ua.setRange(_step, 100); self.coarse_ua.setSingleStep(_step)
        self.coarse_ua.setDecimals(1); self.coarse_ua.setValue(5.0); self.coarse_ua.setSuffix(" µA")
        self.fine_ua = RepeatingDoubleSpinBox()
        self.fine_ua.setRange(_step, 100); self.fine_ua.setSingleStep(_step)
        self.fine_ua.setDecimals(1); self.fine_ua.setValue(1.0); self.fine_ua.setSuffix(" µA")
        self.max_ua = RepeatingDoubleSpinBox()
        self.max_ua.setRange(1, _max); self.max_ua.setSingleStep(_step)
        self.max_ua.setDecimals(1); self.max_ua.setValue(_max); self.max_ua.setSuffix(" µA")
        # Adaptive / predictive headroom — multiplier applied to the
        # regression's predicted ceiling ONLY when the prediction trail
        # starts oscillating (the runner counts direction reversals in
        # successive predictions). With a stable, monotonic prediction
        # the value of this knob is irrelevant — the runner jumps to
        # the full prediction. 0.85 means "if oscillation kicks in,
        # back off to 85 % of the predicted ceiling."
        self.safety_factor = RepeatingDoubleSpinBox()
        self.safety_factor.setRange(0.50, 0.99); self.safety_factor.setValue(0.85)
        self.safety_factor.setSingleStep(0.05); self.safety_factor.setDecimals(2)
        self.safety_factor.setToolTip(
            "Applied to the regression's predicted ceiling only when "
            "successive predictions oscillate around the maximum. A "
            "stable monotonic trail trusts the prediction at 1.00×.")

        # combo_panel is owned by the base class — wired up there too.

        # ----- mode form (sits ABOVE the pattern panel) -----
        # The VT-mode dropdown plus its dependent Q_ph / Q_inj setpoint
        # rows live above the pulse-pattern editor so the user picks
        # what they're targeting before they shape the pulse. The
        # ramp toggle + ramp strategy live in a SEPARATE form below
        # the preview because they describe how the runner sweeps
        # rather than what's being injected per pulse.
        mode_form = rich.make_form()
        mode_form.addRow("VT mode:", self.mode_combo)
        mode_form.addRow(rich.field_label("Charge per phase",
                                          rich.Q_PH, "nC/ph"),
                         self.fixed_qph_label)
        mode_form.addRow(rich.field_label("Charge density",
                                          rich.Q_INJ, "mC/cm²"),
                         self.qinj_mc)

        # ----- ramp-strategy form (sits BELOW the preview) -----
        # Ramp-on toggle + strategy picker. ``self._strategy_form``
        # is stashed on the instance so ``_set_form_row_visible``
        # can find the strategy_combo's row even though the form is
        # nested inside the params_box's outer VBox.
        strategy_form = rich.make_form()
        strategy_form.addRow("", self.fixed_ramp_check)
        strategy_form.addRow("Ramp strategy:", self.strategy_combo)
        self._strategy_form = strategy_form
        # Form rows for ramp parameters. Stored as label widgets so we
        # can hide entire rows when the strategy doesn't need them.
        self._ramp_rows: dict = {}
        ramp_form = rich.make_form()
        for key, label, widget in (
            ("start_ua",
             rich.field_label("Starting current", rich.I_STIM, rich.UA),
             self.start_ua),
            ("coarse_ua",
             rich.field_label("Coarse step", unit_str=rich.UA),
             self.coarse_ua),
            ("fine_ua",
             rich.field_label("Fine step", unit_str=rich.UA),
             self.fine_ua),
            ("max_ua",
             rich.field_label("Maximum current", rich.var("I", "max"), rich.UA),
             self.max_ua),
            ("safety_factor",
             "Safety factor (× ceiling, on oscillation):",
             self.safety_factor),
        ):
            lab = QtWidgets.QLabel(label); lab.setTextFormat(QtCore.Qt.TextFormat.RichText)
            ramp_form.addRow(lab, widget)
            self._ramp_rows[key] = (lab, widget)
        ramp_box = QtWidgets.QGroupBox("Ramp policy")
        QtWidgets.QVBoxLayout(ramp_box).addLayout(ramp_form)

        # ----- multi-parameter sweep -----
        # Optional outer axis on top of the amplitude ramp: repeat the
        # whole ramp at several stimulation rates and/or phase-width
        # asymmetry ratios. Each (rate × ratio) combination becomes its
        # own labelled ChannelRun so results stay separable on disk and
        # in the Results tab. Left blank / unchecked → single run, exactly
        # as before.
        self.sweep_check = QtWidgets.QCheckBox(
            "Sweep multiple rates / asymmetries")
        self.sweep_check.setChecked(False)
        self.sweep_check.toggled.connect(self._on_sweep_toggled)
        self.sweep_rates = QtWidgets.QLineEdit()
        self.sweep_rates.setPlaceholderText(
            "e.g. 50, 100, 200   (blank = pattern rate)")
        self.sweep_rates.setToolTip(
            "Comma- or space-separated pulse rates in pps. The full "
            "amplitude ramp is repeated at each rate. Blank keeps the "
            "pulse pattern's own rate.")
        self.sweep_asym = QtWidgets.QLineEdit()
        self.sweep_asym.setPlaceholderText(
            "e.g. 1, 2, 4   (W2:W1 ratio; blank = 1)")
        self.sweep_asym.setToolTip(
            "Comma- or space-separated recharge:excitation phase-width "
            "ratios (W2:W1) for a biphasic pattern. The recharge phase "
            "amplitude is rebalanced so each pulse stays charge-balanced. "
            "Ignored for triphasic patterns. Blank / 1 = symmetric.")
        sweep_form = rich.make_form()
        sweep_form.addRow("", self.sweep_check)
        sweep_form.addRow("Rates (pps):", self.sweep_rates)
        sweep_form.addRow("Asymmetry (W2:W1):", self.sweep_asym)
        self._sweep_rows = {
            "rates": (sweep_form.labelForField(self.sweep_rates), self.sweep_rates),
            "asym": (sweep_form.labelForField(self.sweep_asym), self.sweep_asym),
        }
        sweep_box = QtWidgets.QGroupBox("Multi-parameter sweep")
        QtWidgets.QVBoxLayout(sweep_box).addLayout(sweep_form)
        self._sweep_box = sweep_box

        # Combine into one parameters group. Vertical order:
        #
        #   1. VT-mode form  (pick mode + see/set the charge target)
        #   2. Pulse pattern editor
        #   3. Pulse pattern preview
        #   4. Ramp toggle + Ramp strategy
        #   5. Ramp policy box (start/coarse/fine/max/safety)
        #
        # The mode-related controls front-load the form because the
        # rest of the inputs key off whichever mode the user picks.
        params_box = QtWidgets.QGroupBox("Voltage Transient parameters")
        v = QtWidgets.QVBoxLayout(params_box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addLayout(mode_form)
        v.addWidget(self.pattern_panel)
        v.addWidget(self.pattern_preview)
        v.addLayout(strategy_form)
        v.addWidget(ramp_box)
        v.addWidget(sweep_box)
        self._ramp_box = ramp_box

        # Apply initial visibility
        self._on_mode_changed()
        self._on_strategy_changed()
        self._on_sweep_toggled()
        # Initial Q_ph readout (deferred so the pattern panel has had a
        # chance to render its first patternChanged tick).
        QtCore.QTimer.singleShot(0, self._refresh_fixed_qph)

        # Hand off to the base class for the Parameters / Experiment sub-tabs
        self._assemble_pages(params_box)

    def _refresh_preview(self, *_):
        # Pattern panel emits patternChanged → preview is already wired in
        # the base class. Nothing extra to do here; ramp params don't
        # affect the displayed pulse shape.
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    def set_array(self, array):
        # Forward to the base class first (channel grid + combo panel
        # update), then refresh the Q_ph readout because the per-site
        # surface area is what turns Q_ph into Q_inj for the side label.
        super().set_array(array)
        self._refresh_fixed_qph()

    def set_same_area(self, same: bool):
        """When the user has "Same for all electrodes" ticked, hide
        the Fixed-charge-density mode entry — uniform-area arrays
        make it equivalent to Fixed current (just a unit conversion
        the user shouldn't have to worry about)."""
        prev = self.mode_combo.currentText()
        self.mode_combo.blockSignals(True)
        try:
            self.mode_combo.clear()
            modes = [self.MODE_FIXED]
            if not same:
                modes.append(self.MODE_FIXED_QD)
            modes.append(self.MODE_MAX)
            for m in modes:
                self.mode_combo.addItem(m)
            if prev in modes:
                self.mode_combo.setCurrentText(prev)
            else:
                self.mode_combo.setCurrentText(self.MODE_MAX)
        finally:
            self.mode_combo.blockSignals(False)
        self._on_mode_changed()

    # --- mode / strategy visibility logic ---------------------------------
    def _on_mode_changed(self, *_):
        mode = self.mode_combo.currentText()
        is_max = mode == self.MODE_MAX
        is_fixed = mode == self.MODE_FIXED
        is_fixed_qd = mode == self.MODE_FIXED_QD
        is_fixed_any = mode in (self.MODE_FIXED, self.MODE_FIXED_QD)
        # Q_ph / Q_inj rows: each Fixed mode shows ONLY the row that
        # corresponds to its setpoint. Maximum mode hides both. The
        # form layout reflows the gap automatically when both rows
        # collapse, so there's no empty space left between VT mode
        # and Ramp strategy.
        #
        #   Mode                    | Q_ph row | Q_inj row
        #   Fixed current         |   show   |   hide
        #   Fixed charge density    |   hide   |   show
        #   Maximum                 |   hide   |   hide
        #
        # ``_set_form_row_visible`` toggles the label alongside its
        # field so neither leaves an orphan widget behind.
        self._set_form_row_visible(self.fixed_qph_label, is_fixed)
        self._set_form_row_visible(self.qinj_mc, is_fixed_qd)
        # In Fixed-charge-density mode the per-pulse current is derived
        # from Q_inj × area / phase_width per electrode, so the pattern
        # panel's Stimulation current input is misleading — hide it
        # (and the asymmetric per-phase amp inputs). Other shape
        # controls (widths, polarity, ratio) stay visible because
        # they're still under the user's control.
        self.pattern_panel.set_amplitude_visible(not is_fixed_qd)
        # Ramp toggle is only meaningful for Fixed modes — Maximum is
        # always a ramp.
        self.fixed_ramp_check.setVisible(is_fixed_any)
        # Ramp strategy is meaningful ONLY in Maximum mode (Fixed
        # modes use a single user-typed start/coarse/fine/max), so
        # hide its row entirely outside of Maximum — the ramp toggle
        # itself stays visible because it controls whether the
        # ramp_box shows up in Fixed modes.
        self._set_form_row_visible(self.strategy_combo, is_max)
        # Ramp box (start/max etc.) appears in Maximum mode, OR in
        # Fixed mode when the user has ticked "Ramp".
        ramp_visible = is_max or (is_fixed_any and self.fixed_ramp_check.isChecked())
        self._ramp_box.setVisible(ramp_visible)
        self._on_strategy_changed()

    @staticmethod
    def _set_form_row_visible(field_widget, visible: bool):
        """Hide/show a QFormLayout row by toggling both label and field.

        Walks the parent widget's layout tree (not just the immediate
        ``parent.layout()``) so this still finds the right
        ``QFormLayout`` when the form has been added as a SUB-layout
        of an outer VBox/HBox — the common case here. Without the
        recursive search the label widget Qt creates for string-based
        ``addRow("Charge per phase ...", widget)`` calls never gets
        hidden, leaving an orphan label visible after the field
        disappears.
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

    def _refresh_fixed_qph(self, *_):
        """Recompute and display Q_ph from the current pattern's
        excitation phase. Called by ``patternChanged`` so the
        Fixed-amplitude readout tracks every tweak."""
        try:
            shape = self.pattern_panel.pattern()
            ex = shape.excitation_phase
            q_ph_nc = abs(ex.amplitude_ua) * ex.width_us * 1e-3   # nC
        except Exception:
            self.fixed_qph_label.setText("—")
            return
        # Quick area-aware Q_inj readout when an array is loaded — just
        # the first electrode's area. Useful sanity-check that pairs
        # with Q_ph for users picking Fixed-amplitude.
        site = self._array.sites[0] if self._array.sites else None
        if site is not None and site.surface_area_um2 > 0:
            area_cm2 = site.surface_area_um2 / 1e8
            q_inj_mc_per_cm2 = (q_ph_nc * 1e-9) / area_cm2 * 1e3   # mC/cm²
            self.fixed_qph_label.setText(
                f"{q_ph_nc:.2f} nC &nbsp;·&nbsp; "
                f"{q_inj_mc_per_cm2:.3f} mC/cm² "
                f"<span style='color:#666; font-weight:normal;'>"
                f"(at {site.surface_area_um2:.0f} μm²)</span>"
            )
        else:
            self.fixed_qph_label.setText(f"{q_ph_nc:.2f} nC")

    def _on_strategy_changed(self, *_):
        mode = self.mode_combo.currentText()
        # Ramp visibility re-evaluation lives here too so the Ramp
        # checkbox can show/hide the same ramp_box.
        is_max = mode == self.MODE_MAX
        is_fixed_qd = mode == self.MODE_FIXED_QD
        is_fixed_any = mode in (self.MODE_FIXED, self.MODE_FIXED_QD)
        self._ramp_box.setVisible(is_max or
                                  (is_fixed_any and self.fixed_ramp_check.isChecked()))
        if mode != self.MODE_MAX:
            # Fixed-mode ramps always show start/coarse/fine/max — but
            # Fixed-charge-density additionally hides ``start_ua``
            # because the ramp is parameterised in charge density,
            # not current (the per-electrode area means the equivalent
            # current varies channel to channel anyway).
            base = {"start_ua", "coarse_ua", "fine_ua", "max_ua"}
            if is_fixed_qd:
                base.discard("start_ua")
            for key, (lab, w) in self._ramp_rows.items():
                visible = key in base
                lab.setVisible(visible); w.setVisible(visible)
            return
        strat = self.strategy_combo.currentText()
        # Visibility per strategy:
        #   Fixed increment  : start, coarse, fine, max  (no safety factor)
        #   Adaptive / Pred  : start, max, safety_factor  (no coarse/fine)
        show = {
            self.STRAT_INCR: {"start_ua", "coarse_ua", "fine_ua", "max_ua"},
            self.STRAT_REGR: {"start_ua", "max_ua", "safety_factor"},
            self.STRAT_PRED: {"start_ua", "max_ua", "safety_factor"},
        }.get(strat, {"start_ua", "coarse_ua", "fine_ua", "max_ua"})
        for key, (lab, w) in self._ramp_rows.items():
            visible = key in show
            lab.setVisible(visible); w.setVisible(visible)

    PREF_FIELDS = ("mode_combo", "strategy_combo", "fixed_ramp_check",
                   "qinj_mc",
                   "start_ua", "coarse_ua", "fine_ua", "max_ua", "safety_factor",
                   "sweep_check", "sweep_rates", "sweep_asym")

    def experiment_type(self) -> str: return "VT"

    def _on_sweep_toggled(self, *_):
        """Show the rate / asymmetry inputs only when sweeping is on."""
        on = self.sweep_check.isChecked()
        for _key, (lab, w) in self._sweep_rows.items():
            w.setVisible(on)
            if lab is not None:
                lab.setVisible(on)

    @staticmethod
    def _parse_float_list(text: str) -> list:
        """Parse a comma/space-separated list of positive floats.

        Silently drops blanks and non-numeric tokens, and de-duplicates
        while preserving first-seen order. Returns ``[]`` for empty input.
        """
        out: list = []
        seen: set = set()
        for tok in text.replace(",", " ").split():
            try:
                val = float(tok)
            except ValueError:
                continue
            if val <= 0:
                continue
            if val not in seen:
                seen.add(val)
                out.append(val)
        return out

    def _build_sweep_points(self, pattern: PulsePattern):
        """Turn the sweep line-edits into a list of ``SweepPoint`` s.

        Returns ``None`` when sweeping is off or the inputs reduce to a
        single untouched point — the runner then behaves exactly as the
        original single-parameter path. Asymmetry ratios are ignored for
        triphasic patterns (a phase-width ratio has no single meaning
        across three phases); a log line explains the skip.
        """
        from ..experiments.voltage_transient import SweepPoint
        if not self.sweep_check.isChecked():
            return None
        rates = self._parse_float_list(self.sweep_rates.text())
        ratios = self._parse_float_list(self.sweep_asym.text())
        if ratios and pattern.is_triphasic:
            self.log_pane.log(
                "Sweep: asymmetry ratios ignored for a triphasic pattern; "
                "sweeping rate only.")
            ratios = []
        rate_axis = rates or [None]
        ratio_axis = ratios or [None]
        if rate_axis == [None] and ratio_axis == [None]:
            return None
        points = []
        for r in rate_axis:
            for a in ratio_axis:
                parts = []
                if r is not None:
                    parts.append(f"{r:g}pps")
                if a is not None and a != 1:
                    parts.append(f"asym{a:g}x")
                label = "_".join(parts) or "base"
                points.append(SweepPoint(rate_hz=r, width_ratio=a, label=label))
        self.log_pane.log(
            f"Multi-parameter sweep: {len(rate_axis)} rate(s) × "
            f"{len(ratio_axis)} ratio(s) = {len(points)} run(s) per "
            f"configuration.")
        return points

    def start_clicked(self):
        if self._stim is None or self._scope is None:
            return
        shape = self.pattern_panel.pattern()
        excite = shape.excitation_phase.amplitude_ua
        mode = self.mode_combo.currentText()
        is_max = mode == self.MODE_MAX
        is_fixed_qd = mode == self.MODE_FIXED_QD
        # Maximum / ramped modes scale the panel pulse to start_ua.
        # Fixed current uses the panel's amp as-is.
        # Fixed charge density derives I_stim from Q_inj × area / phase
        # width per electrode — runner-side compute happens in
        # VoltageTransientExperiment when this mode is in play; here
        # we just set a sentinel start_ua so the runner has something
        # to work with if it doesn't yet honour the QD path.
        if is_fixed_qd:
            try:
                # Q_inj (mC/cm²) × A (cm²) ÷ T_ph (s) = I (A); convert to µA.
                site = self._array.sites[0] if self._array.sites else None
                area_cm2 = (site.surface_area_um2 / 1e8) if site else 1e-5
                phase_us = max(abs(shape.excitation_phase.width_us), 1.0)
                amp_target_ua = (
                    self.qinj_mc.value() * 1e-3 * area_cm2 / (phase_us * 1e-6) * 1e6
                )
            except Exception:
                amp_target_ua = abs(excite)
            scale = (amp_target_ua / abs(excite)) if excite != 0 else 1.0
            pattern = shape.scaled(scale)
            self.log_pane.log(
                f"Fixed Q_inj target: {self.qinj_mc.value():.3f} mC/cm² "
                f"⇒ I_stim ≈ {amp_target_ua:.2f} µA "
                f"(site area {area_cm2*1e8:.0f} µm², T_ph {phase_us:.0f} µs).")
        elif is_max:
            scale = (self.start_ua.value() / abs(excite)) if excite != 0 else 1.0
            pattern = shape.scaled(scale)
        else:
            pattern = shape

        configs = self.combo_panel.selected_configurations()
        if not configs:
            QtWidgets.QMessageBox.warning(
                self, "No configurations selected",
                "Click electrodes in the grid to mark actives, then check at "
                "least one combination in the configurations list."
            )
            return
        self.log_pane.log(f"Running {len(configs)} configuration(s)")

        config = configs[0]
        test = TestParameters(experiment="TV" if pattern.is_triphasic else "VT",
                              pattern=pattern, configuration=config,
                              array=self._array)
        session = Session(notebook="vt_session", subject=config.display_name(), test=test)

        ramp = self._build_ramp_policy(pattern.excitation_phase.amplitude_ua)
        # Predictive strategy → try to load a trained ML model. If the
        # dataset isn't available the predictor stays ``None`` and the
        # runner falls back to the adaptive regression path on its own.
        predictor = None
        if (self.mode_combo.currentText() == self.MODE_MAX
                and self.strategy_combo.currentText() == self.STRAT_PRED):
            try:
                from ..ml import QinjPredictor
                predictor = QinjPredictor().fit_from_csv()
            except Exception as e:
                self.log_pane.log(
                    f"Predictive: no trained model usable ({e}); the "
                    f"runner will fall back to adaptive regression.")
                predictor = None
        sweep_points = self._build_sweep_points(pattern)
        runner = VoltageTransientExperiment(
            session, self._stim, self._scope,
            configurations=configs, ramp=ramp, predictor=predictor,
            cathodic_limit_v=self._cathodic_limit_v,
            anodic_limit_v=self._anodic_limit_v,
            polarization_tolerance_v=self._polarization_tolerance_v,
            sweep_points=sweep_points,
        )
        save_name = f"VT_{config.display_name().replace(' ', '_')}.npz"
        self._start_runner(runner, save_name)

    def _build_ramp_policy(self, panel_amp_ua: float) -> "RampPolicy":
        """Translate (mode, strategy) into a :class:`RampPolicy`.

        * **Fixed mode (no ramp)** — single shot at the panel's
          amplitude. We pin ``starting_ua = max_ua = |panel_amp|`` so
          the ramp loop immediately terminates.
        * **Fixed mode + Ramp checkbox** — sweeps from start_ua to
          max_ua using the user's coarse/fine step sizes (same loop
          as Maximum mode + Fixed increment, but the ceiling is the
          user-typed max_ua rather than a water-window probe).
        * **Maximum + Fixed increment** — the user-typed step sizes.
        * **Maximum + Adaptive / Predictive** — derives step sizes from
          the safety_factor spinbox.
        """
        mode = self.mode_combo.currentText()
        is_max = mode == self.MODE_MAX
        is_fixed_any = mode in (self.MODE_FIXED, self.MODE_FIXED_QD)
        if is_fixed_any and not self.fixed_ramp_check.isChecked():
            amp = abs(panel_amp_ua) if panel_amp_ua != 0 else 5.0
            return RampPolicy(starting_ua=amp,
                              coarse_step_ua=max(amp, 1.0),
                              fine_step_ua=max(amp, 1.0),
                              max_ua=amp)
        if is_fixed_any and self.fixed_ramp_check.isChecked():
            # Fixed mode + Ramp on — same shape as the Maximum-mode
            # fixed-increment path, but capped at the user's max_ua
            # rather than the water-window detection logic.
            return RampPolicy(starting_ua=self.start_ua.value(),
                              coarse_step_ua=self.coarse_ua.value(),
                              fine_step_ua=self.fine_ua.value(),
                              max_ua=self.max_ua.value())
        strat = self.strategy_combo.currentText()
        if strat == self.STRAT_INCR:
            return RampPolicy(starting_ua=self.start_ua.value(),
                              coarse_step_ua=self.coarse_ua.value(),
                              fine_step_ua=self.fine_ua.value(),
                              max_ua=self.max_ua.value(),
                              strategy="increment")
        # Adaptive / Predictive — the runner does the regression and
        # picks where to jump next; we just hand it the start/max
        # bounds, default coarse/fine steps for the probing phase
        # before the regression has enough data, and the safety
        # factor (which the runner only applies once it detects
        # oscillation in successive predictions).
        safety = float(self.safety_factor.value())
        max_amp = float(self.max_ua.value())
        coarse_default = max(max_amp * 0.05, 1.0)
        fine_default = max(coarse_default / 5.0, 0.5)
        runner_strat = "predictive" if strat == self.STRAT_PRED else "adaptive"
        self.log_pane.log(
            f"{runner_strat.capitalize()} ramp: probe step ≈ "
            f"{coarse_default:.2f} µA, safety factor "
            f"{safety:.2f} (only applies if predictions oscillate).")
        return RampPolicy(starting_ua=self.start_ua.value(),
                          coarse_step_ua=coarse_default,
                          fine_step_ua=fine_default,
                          max_ua=max_amp,
                          strategy=runner_strat,
                          safety_factor=safety)


# ---------------------------------------------------------------------------
# Short-Term Pulsing tab
# ---------------------------------------------------------------------------
class ShortPulsingTab(_BaseExperimentTab):
    UNIT_S = "seconds"
    UNIT_P = "pulses"
    CHAR_NONE = "None"
    CHAR_FIXED_AMP = "Fixed current"
    CHAR_FIXED_QD = "Fixed charge density"
    CHAR_MAX = "Maximum charge-injection"

    def __init__(self, array, parent=None):
        super().__init__(array, parent)
        # Duration + unit toggle (seconds / pulses, integer in pulses).
        self.duration = RepeatingDoubleSpinBox()
        self.duration.setRange(1, 1e9); self.duration.setValue(60)
        self.duration.setDecimals(1); self.duration.setSuffix(" s")
        self.duration_unit = QtWidgets.QComboBox()
        self.duration_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.duration_unit.currentTextChanged.connect(self._on_duration_unit)

        # Pre/post characterization mode + (optional) target Q_inj.
        self.char_mode = QtWidgets.QComboBox()
        self.char_mode.addItems([self.CHAR_NONE, self.CHAR_FIXED_AMP,
                                 self.CHAR_FIXED_QD, self.CHAR_MAX])
        self.char_mode.setCurrentText(self.CHAR_NONE)
        self.char_mode.currentTextChanged.connect(self._on_char_mode_changed)
        self.qinj_mc = RepeatingDoubleSpinBox()
        self.qinj_mc.setRange(0.001, 100.0); self.qinj_mc.setDecimals(3)
        self.qinj_mc.setSingleStep(0.05); self.qinj_mc.setValue(0.5)
        self.qinj_mc.setSuffix(" mC/cm²")

        # On-demand "Acquire waveform" — captures one pulse trace at
        # the current pulsing amplitude into the multichan-scope view
        # without disturbing the running stim. Routed through the
        # scope's single_capture path.
        self.acquire_btn = QtWidgets.QPushButton("Acquire waveform")
        self.acquire_btn.clicked.connect(self._acquire_one_capture)
        self.acquire_btn.setEnabled(False)

        ext = rich.make_form()
        # Duration row: spinbox + unit dropdown
        dur_row = QtWidgets.QHBoxLayout()
        dur_row.addWidget(self.duration, stretch=2)
        dur_row.addWidget(self.duration_unit, stretch=1)
        dur_w = QtWidgets.QWidget(); dur_w.setLayout(dur_row)
        ext.addRow("Duration:", dur_w)
        ext.addRow("Pre/post characterization:", self.char_mode)
        ext.addRow(rich.field_label("Target charge density",
                                    rich.Q_INJ, "mC/cm²"),
                   self.qinj_mc)
        ext.addRow("", self.acquire_btn)
        # Initial visibility (Q_inj field hidden when char_mode = None)
        QtCore.QTimer.singleShot(0, self._on_char_mode_changed)

        params_box = QtWidgets.QGroupBox("Short-Term Pulsing parameters")
        v = QtWidgets.QVBoxLayout(params_box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addWidget(self.pattern_panel)
        v.addWidget(self.pattern_preview)
        v.addLayout(ext)

        self._assemble_pages(params_box)
        # Hide the live "Short-Term Pulsing" sub-tab until the user
        # has actually captured something. Until Acquire / Start runs
        # the experiment view is empty, and a clickable empty tab
        # adds noise without value. Index 1 is the second inner tab
        # (the experiment page; index 0 is Parameters).
        self.inner_tabs.setTabVisible(1, False)

    def _refresh_preview(self, *_):
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    SINGLE_CONFIG = True
    PREF_FIELDS = ("duration", "duration_unit", "char_mode", "qinj_mc")

    def experiment_type(self): return "SP"

    def _on_duration_unit(self, *_):
        if self.duration_unit.currentText() == self.UNIT_P:
            self.duration.setSuffix(" pulses")
            self.duration.setDecimals(0); self.duration.setSingleStep(1)
            self.duration.setValue(round(self.duration.value()))
        else:
            self.duration.setSuffix(" s")
            self.duration.setDecimals(1); self.duration.setSingleStep(1)

    def _on_char_mode_changed(self, *_):
        # Q_inj target only meaningful for the charge-density variant.
        is_qd = self.char_mode.currentText() == self.CHAR_FIXED_QD
        self.qinj_mc.setVisible(is_qd)
        layout = self.qinj_mc.parentWidget().layout() \
            if self.qinj_mc.parentWidget() else None
        if isinstance(layout, QtWidgets.QFormLayout):
            lab = layout.labelForField(self.qinj_mc)
            if lab is not None: lab.setVisible(is_qd)

    def _to_seconds(self, value: float, unit: str) -> float:
        if unit == self.UNIT_P:
            rate = max(self.pattern_panel.pattern().rate_hz, 1e-6)
            return value / rate
        return float(value)

    def _acquire_one_capture(self):
        """Trigger a single scope capture and route it into the
        multi-channel scope view. Useful for grabbing a one-off trace
        without starting / stopping a full pulsing run.
        """
        if self._scope is None:
            QtWidgets.QMessageBox.warning(
                self, "Scope not connected",
                "Connect the oscilloscope before acquiring.")
            return
        try:
            self._scope.configure_channels(self._aliases)
            self._scope.set_acquisition_mode(self._acq_mode,
                                             n_avg=self._acq_n_avg)
            from ..session import Capture
            from ..waveforms import PulsePattern
            acq = self._scope.single_capture()
            cap = Capture(index=0, pattern=self.pattern_panel.pattern())
            cap.time_us = acq.time_us
            cap.v_mon_v = getattr(acq, "v_mon_v", cap.v_mon_v)
            cap.e_act_v = getattr(acq, "e_act_v", None)
            cap.e_ret_v = getattr(acq, "e_ret_v", None)
            ch = -1
            actives = self.channel_grid.actives()
            if actives: ch = actives[0]
            self.multichan_scope.add_capture(cap, ch if ch > 0 else -1)
            self.metrics_side.show_capture(cap)
            self.log_pane.log("Single capture acquired.")
            # Reveal + jump to the live waveform sub-tab now that
            # there's something to look at. The tab stays visible
            # for the rest of the session.
            self.inner_tabs.setTabVisible(1, True)
            self.inner_tabs.setCurrentWidget(self.experiment_page)
        except Exception as e:
            self.log_pane.log(f"Acquire failed: {e}")

    def set_hardware(self, stim, scope):
        # Acquire button only useful when scope is up.
        super().set_hardware(stim, scope)
        self.acquire_btn.setEnabled(True)

    def clear_hardware(self):
        super().clear_hardware()
        self.acquire_btn.setEnabled(False)

    def start_clicked(self):
        if not self._stim or not self._scope: return
        configs = self.combo_panel.selected_configurations()
        if not configs:
            QtWidgets.QMessageBox.warning(
                self, "No configuration selected",
                "Click an electrode and pick one combination from the "
                "Configurations to run list.")
            return
        # Reveal the live waveform sub-tab — base ``_start_runner``
        # auto-switches focus to it, but the tab is hidden until first
        # use so we have to un-hide before that switch.
        self.inner_tabs.setTabVisible(1, True)
        # Queue every selected config; the base class chains them via
        # ``_on_finished`` -> ``_start_next_pending``. The combination
        # panel restricts multi-config selection to single-active modes
        # (MP / CG / PCG); multipolar modes return a single combo.
        self._pending_configs = list(configs)
        if len(self._pending_configs) > 1:
            self.log_pane.log(
                f"Short-Term Pulsing: queued {len(self._pending_configs)} "
                f"configuration(s) — running sequentially.")
        cm = self.char_mode.currentText()
        if cm != self.CHAR_NONE:
            self.log_pane.log(
                f"Pre/post characterization: {cm}"
                + (f" (target Q_inj = {self.qinj_mc.value():.3f} mC/cm²)"
                   if cm == self.CHAR_FIXED_QD else "")
                + " — runner-side support is a follow-up.")
        self._start_next_pending()

    def _start_next_pending(self):
        if not self._pending_configs:
            return
        config = self._pending_configs.pop(0)
        pattern = self.pattern_panel.pattern()
        amplitude_ua = abs(pattern.excitation_phase.amplitude_ua)
        duration_s = self._to_seconds(self.duration.value(),
                                      self.duration_unit.currentText())
        test = TestParameters(experiment="SP", pattern=pattern, configuration=config,
                              array=self._array, duration_s=duration_s)
        session = Session(notebook="sp_session", subject=config.display_name(), test=test)
        runner = ShortPulsingExperiment(
            session, self._stim, self._scope, amplitude_ua=amplitude_ua,
            policy=ShortPulsingPolicy(
                capture_interval_s=max(duration_s * 2, 60.0),
                duration_s=duration_s),
        )
        self._start_runner(runner, f"SP_{config.display_name().replace(' ','_')}.npz")


# ---------------------------------------------------------------------------
# Long-Term Pulsing tab
# ---------------------------------------------------------------------------
class LongPulsingTab(_BaseExperimentTab):
    # Total duration / re-characterization interval can each be authored
    # in seconds OR pulses (count); the unit-toggle dropdowns convert at
    # start-time using the panel's rate to come up with a seconds value
    # the runner expects.
    UNIT_S = "seconds"
    UNIT_P = "pulses"

    def __init__(self, array, parent=None):
        super().__init__(array, parent)

        # Duration spinbox + unit selector. Pulses-mode forces integer
        # values via the unit-toggle slot; seconds-mode keeps 1 decimal.
        self.duration = RepeatingDoubleSpinBox()
        self.duration.setRange(1, 1e9); self.duration.setValue(3600)
        self.duration.setDecimals(1)
        self.duration_unit = QtWidgets.QComboBox()
        self.duration_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.duration_unit.currentTextChanged.connect(self._on_duration_unit)

        # Periodic *snapshot* — one passive scope grab every N
        # seconds/pulses during the burst. Distinct from a full VT,
        # which is the optional max-charge-injection step before pause.
        self.snap_int = RepeatingDoubleSpinBox()
        self.snap_int.setRange(1, 1e9); self.snap_int.setValue(60)
        self.snap_int.setDecimals(1)
        self.snap_int_unit = QtWidgets.QComboBox()
        self.snap_int_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.snap_int_unit.currentTextChanged.connect(self._on_snap_unit)

        # External-measurement pause AFTER each snapshot. Optionally,
        # the runner can also do a max-charge-injection sweep right
        # before pausing — so the user gets a fresh max-Q_inj data
        # point at every pause point.
        self.do_pause = QtWidgets.QCheckBox("Pause after characterization")
        self.do_max_before_pause = QtWidgets.QCheckBox(
            "Run maximum charge-injection before pausing")
        self.do_max_before_pause.setEnabled(False)
        self.do_pause.toggled.connect(self.do_max_before_pause.setEnabled)
        self.pause_s = RepeatingDoubleSpinBox()
        self.pause_s.setRange(1, 1e9); self.pause_s.setValue(60)
        self.pause_s.setDecimals(1)
        self.pause_unit = QtWidgets.QComboBox()
        self.pause_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.pause_unit.currentTextChanged.connect(self._on_pause_unit)
        self.do_pause.toggled.connect(self.pause_s.setEnabled)
        self.do_pause.toggled.connect(self.pause_unit.setEnabled)
        self.pause_s.setEnabled(self.do_pause.isChecked())
        self.pause_unit.setEnabled(self.do_pause.isChecked())

        # Build the parameter form
        f = rich.make_form()
        # Total duration row
        dur_row = QtWidgets.QHBoxLayout()
        dur_row.addWidget(self.duration, stretch=2)
        dur_row.addWidget(self.duration_unit, stretch=1)
        dur_w = QtWidgets.QWidget(); dur_w.setLayout(dur_row)
        f.addRow("Total duration:", dur_w)
        # Snapshot-every row
        sn_row = QtWidgets.QHBoxLayout()
        sn_row.addWidget(self.snap_int, stretch=2)
        sn_row.addWidget(self.snap_int_unit, stretch=1)
        sn_w = QtWidgets.QWidget(); sn_w.setLayout(sn_row)
        f.addRow("Waveform snapshot every:", sn_w)
        # Pause-after-snapshot block.
        pa_row = QtWidgets.QHBoxLayout()
        pa_row.addWidget(self.do_pause, stretch=3)
        pa_row.addWidget(QtWidgets.QLabel("for"))
        pa_row.addWidget(self.pause_s, stretch=1)
        pa_row.addWidget(self.pause_unit)
        pa_w = QtWidgets.QWidget(); pa_w.setLayout(pa_row)
        f.addRow("", pa_w)
        f.addRow("", self.do_max_before_pause)

        box = QtWidgets.QGroupBox("Long-Term Pulsing parameters")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addWidget(self.pattern_panel)
        v.addWidget(self.pattern_preview)
        v.addLayout(f)

        self._assemble_pages(box)

    def _refresh_preview(self, *_):
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    SINGLE_CONFIG = True
    PREF_FIELDS = ("duration", "duration_unit",
                   "snap_int", "snap_int_unit",
                   "do_pause", "pause_s", "pause_unit",
                   "do_max_before_pause")

    def experiment_type(self): return "LP"

    # ----- unit conversion helpers -----
    def _apply_unit(self, spin: QtWidgets.QDoubleSpinBox, unit: str):
        if unit == self.UNIT_P:
            spin.setSuffix(" pulses")
            spin.setDecimals(0); spin.setSingleStep(1)
            spin.setValue(round(spin.value()))
        else:
            spin.setSuffix(" s")
            spin.setDecimals(1); spin.setSingleStep(1)

    def _on_duration_unit(self, *_):
        self._apply_unit(self.duration, self.duration_unit.currentText())

    def _on_snap_unit(self, *_):
        self._apply_unit(self.snap_int, self.snap_int_unit.currentText())

    def _on_pause_unit(self, *_):
        self._apply_unit(self.pause_s, self.pause_unit.currentText())

    def _to_seconds(self, value: float, unit: str) -> float:
        if unit == self.UNIT_P:
            rate = max(self.pattern_panel.pattern().rate_hz, 1e-6)
            return value / rate
        return value

    def start_clicked(self):
        if not self._stim or not self._scope: return
        configs = self.combo_panel.selected_configurations()
        if not configs:
            QtWidgets.QMessageBox.warning(
                self, "No configuration selected",
                "Click an electrode and pick one combination from the "
                "Configurations to run list.")
            return
        # Queue every selected config; the base class chains them via
        # ``_on_finished`` -> ``_start_next_pending``.
        self._pending_configs = list(configs)
        if len(self._pending_configs) > 1:
            self.log_pane.log(
                f"Long-Term Pulsing: queued {len(self._pending_configs)} "
                f"configuration(s) — running sequentially.")
        if self.do_pause.isChecked():
            pause_s = self._to_seconds(self.pause_s.value(),
                                       self.pause_unit.currentText())
            self.log_pane.log(
                f"Pause-after-snapshot enabled: {pause_s:.0f} s. "
                + ("Max-Q_inj sweep before each pause: ON. "
                   if self.do_max_before_pause.isChecked() else "")
                + "Runner-side support is a follow-up.")
        self._start_next_pending()

    def _start_next_pending(self):
        if not self._pending_configs:
            return
        config = self._pending_configs.pop(0)
        pattern = self.pattern_panel.pattern()
        amplitude_ua = abs(pattern.excitation_phase.amplitude_ua)
        duration_s = self._to_seconds(self.duration.value(),
                                      self.duration_unit.currentText())
        snap_every_s = self._to_seconds(self.snap_int.value(),
                                        self.snap_int_unit.currentText())
        test = TestParameters(experiment="LP", pattern=pattern, configuration=config,
                              array=self._array, duration_s=duration_s)
        session = Session(notebook="lp_session", subject=config.display_name(), test=test)
        runner = LongPulsingExperiment(
            session, self._stim, self._scope, amplitude_ua=amplitude_ua,
            policy=LongPulsingPolicy(
                duration_s=duration_s,
                # No periodic full-VT — characterize_every_s set huge
                # so the runner skips it; mid-pulsing snapshots fire
                # at snap_every_s instead.
                characterize_every_s=max(duration_s, 1e9),
                capture_during_pulsing_every_s=snap_every_s),
        )
        self._start_runner(runner, f"LP_{config.display_name().replace(' ','_')}.npz")


# ---------------------------------------------------------------------------
# Progressive Stress tab
# ---------------------------------------------------------------------------
class ProgressiveStressTab(_BaseExperimentTab):
    def __init__(self, array, parent=None):
        super().__init__(array, parent)
        from ..config import STIM_CURRENT_RESOLUTION_UA, STIM_MAX_AMPLITUDE_UA
        _step = STIM_CURRENT_RESOLUTION_UA
        _max = STIM_MAX_AMPLITUDE_UA
        self.start_ua = RepeatingDoubleSpinBox()
        # Allow 0 µA — useful as a "lazy ramp from rest" starting point.
        self.start_ua.setRange(0.0, _max); self.start_ua.setSingleStep(_step)
        self.start_ua.setDecimals(1); self.start_ua.setValue(0.0); self.start_ua.setSuffix(" µA")
        self.step_ua = RepeatingDoubleSpinBox()
        self.step_ua.setRange(_step, _max); self.step_ua.setSingleStep(_step)
        self.step_ua.setDecimals(1); self.step_ua.setValue(5.0); self.step_ua.setSuffix(" µA")
        self.t_step = RepeatingDoubleSpinBox(); self.t_step.setRange(1, 3600); self.t_step.setValue(60); self.t_step.setSuffix(" s")
        # Default ceiling = PlexStim hardware limit (1 mA/channel). Users can
        # cap below that if they want to stop the ramp earlier.
        self.max_ua = RepeatingDoubleSpinBox(); self.max_ua.setRange(1, _max)
        self.max_ua.setSingleStep(_step); self.max_ua.setDecimals(1)
        self.max_ua.setValue(_max); self.max_ua.setSuffix(" µA")
        # Wall-clock seconds between successive scope captures inside a
        # single staircase step. The runner snaps to ``floor(t_step /
        # sampling_period)`` frames per step.
        self.sampling_period = RepeatingDoubleSpinBox()
        self.sampling_period.setRange(0.1, 3600.0)
        self.sampling_period.setSingleStep(1.0)
        self.sampling_period.setDecimals(1)
        self.sampling_period.setValue(10.0)
        self.sampling_period.setSuffix(" s")
        # Hardware-level stop: V_mon hits the ±12 V compliance rail and the
        # device stops actually delivering the programmed current. There's no
        # point ramping further past that point.
        self.stop_on_compliance = QtWidgets.QCheckBox(
            "Stop when V_mon hits voltage compliance (±12 V rail)")
        self.stop_on_compliance.setChecked(True)

        # Stress-specific ramp parameters live below the pattern panel
        f = rich.make_form()
        f.addRow(rich.field_label("Starting current", rich.I_STIM, rich.UA),
                 self.start_ua)
        f.addRow(rich.field_label("Step", unit_str=rich.UA), self.step_ua)
        f.addRow(rich.field_label("Time per step", unit_str="s"), self.t_step)
        f.addRow(rich.field_label("Maximum current", rich.var("I", "max"), rich.UA),
                 self.max_ua)
        f.addRow("Sampling period:", self.sampling_period)
        f.addRow("", self.stop_on_compliance)

        # Two-tab figure: the pulse pattern (live preview) and the
        # staircase plot of stepped current vs. time.
        self.staircase = StaircasePlot()
        for w in (self.start_ua, self.step_ua, self.t_step, self.max_ua):
            w.valueChanged.connect(self._refresh_staircase)
        self._fig_tabs = QtWidgets.QTabWidget()
        self._fig_tabs.setDocumentMode(True)
        self._fig_tabs.addTab(self.pattern_preview, "Pulse pattern")
        self._fig_tabs.addTab(self.staircase, "Staircase")

        # Staircase parameters: dedicated groupbox at the TOP of the
        # Parameters sub-tab, outside the pulse-pattern / figure
        # groupbox entirely. Always visible — no scroll, no nesting.
        ramp_box = QtWidgets.QGroupBox("Staircase parameters")
        rv = QtWidgets.QVBoxLayout(ramp_box)
        rv.setContentsMargins(8, 6, 8, 6); rv.setSpacing(2)
        rv.addLayout(f)

        # Pulse pattern groupbox: pattern shape controls + figure tabs.
        # Sits *below* the staircase parameters; the staircase ramp
        # values are no longer attached to or hidden by the figure.
        pulse_box = QtWidgets.QGroupBox("Pulse pattern")
        pv = QtWidgets.QVBoxLayout(pulse_box)
        pv.setContentsMargins(8, 6, 8, 6); pv.setSpacing(2)
        pv.addWidget(self.pattern_panel)
        pv.addWidget(self._fig_tabs)

        # Wrap into one widget so _assemble_pages can treat them as
        # one "params_box". Ramp params at the top, pulse pattern below.
        params_root = QtWidgets.QWidget()
        rl = QtWidgets.QVBoxLayout(params_root)
        rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(6)
        rl.addWidget(ramp_box)
        rl.addWidget(pulse_box)

        # Initial staircase render
        QtCore.QTimer.singleShot(0, self._refresh_staircase)

        self._assemble_pages(params_root)

    def _refresh_staircase(self, *_):
        self.staircase.set_policy(
            start_ua=float(self.start_ua.value()),
            step_ua=float(self.step_ua.value()),
            t_step_s=float(self.t_step.value()),
            max_ua=float(self.max_ua.value()),
        )

    def _refresh_preview(self, *_):
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    SINGLE_CONFIG = True
    PREF_FIELDS = ("start_ua", "step_ua", "t_step", "max_ua",
                   "sampling_period", "stop_on_compliance")

    def experiment_type(self): return "PS"

    def start_clicked(self):
        if not self._stim or not self._scope: return
        configs = self.combo_panel.selected_configurations()
        if not configs:
            QtWidgets.QMessageBox.warning(
                self, "No configuration selected",
                "Click an electrode and pick one combination from the "
                "Configurations to run list.")
            return
        # Queue every selected combo; the base class chains them via
        # ``_on_finished`` -> ``_start_next_pending``. The combination
        # panel already restricts multi-config selection to the
        # single-active modes (MP / CG / PCG); multipolar modes return
        # at most one combo.
        self._pending_configs = list(configs)
        if len(self._pending_configs) > 1:
            self.log_pane.log(
                f"Progressive Stress: queued {len(self._pending_configs)} "
                f"configuration(s) — running sequentially.")
        self._start_next_pending()

    def _start_next_pending(self):
        if not self._pending_configs:
            return
        config = self._pending_configs.pop(0)
        # Pattern shape from panel; rescale to the stress sweep's starting amp.
        shape = self.pattern_panel.pattern()
        excite = shape.excitation_phase.amplitude_ua
        scale = (self.start_ua.value() / abs(excite)) if excite != 0 else 1.0
        pattern = shape.scaled(scale)
        test = TestParameters(experiment="PS", pattern=pattern, configuration=config,
                              array=self._array)
        session = Session(notebook="ps_session", subject=config.display_name(), test=test)
        runner = ProgressiveStressExperiment(
            session, self._stim, self._scope,
            policy=StressPolicy(starting_ua=self.start_ua.value(),
                                step_ua=self.step_ua.value(),
                                t_step_s=self.t_step.value(),
                                max_ua=self.max_ua.value(),
                                sampling_period_s=self.sampling_period.value(),
                                stop_on_voltage_compliance=self.stop_on_compliance.isChecked()),
        )
        self._start_runner(runner, f"PS_{config.display_name().replace(' ','_')}.npz")

    # Queue chaining + completion-marking are handled by the base
    # ``_on_finished``; PS just needs ``_start_next_pending`` to build
    # a runner for the next configuration.
