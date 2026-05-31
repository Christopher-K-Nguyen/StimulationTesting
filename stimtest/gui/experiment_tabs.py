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
from typing import Callable, List, Optional

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
from ..persistence import save_session_npz, save_session_npz_incremental
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
    # Emitted when the runner pauses between channels for a physical rewire.
    # Carries the human-readable message describing the next channel.
    paused = QtCore.pyqtSignal(str)

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
        # Optional pre-run callable executed by ``run`` BEFORE
        # ``self.runner.run()`` and on the worker thread.  Used to
        # move the scope-setup SCPI burst off the GUI thread — a
        # single hung USB-TMC round-trip in that burst used to
        # freeze the whole GUI for several seconds, and slow setup
        # commands (e.g. the read-back-validated ``set_acquisition_mode``
        # on stubborn firmware) gave the user no Stop-button escape.
        # With the callable assigned, the GUI stays responsive
        # during scope setup and any later hang is contained to
        # the worker thread.
        self.pre_run: Optional[Callable[[], None]] = None
        # Incremental-save throttle state.  See
        # ``persistence.save_session_npz_incremental`` — every capture
        # event triggers a throttled write so a mid-run crash leaves
        # the partial session on disk rather than only the in-memory
        # state.  Default throttle (2 s OR every capture) means short
        # runs (VT) save per capture; long runs (LP) save at most
        # twice a second.
        self._incr_last_save_at: Optional[float] = None
        self._incr_last_capture_count: int = 0
        # Wire the plain-Python event stream into our Qt signals
        runner.subscribe(self._on_event)

    def _on_event(self, ev: ExperimentEvent):
        # Called from the worker thread (the runner's thread) — emitting Qt
        # signals from here is safe because Qt automatically marshals them
        # across to the GUI thread via the default queued connection.
        if ev.kind == "capture" and ev.capture is not None:
            ch = ev.run.configuration.active if ev.run is not None else -1
            self.captured.emit(ev.capture, int(ch))
            # Incremental save trigger.  Runs on the WORKER thread (we
            # are in the runner's thread here), so the disk IO doesn't
            # block the GUI.  Throttled so long LP runs don't write per
            # capture — see ``save_session_npz_incremental`` for the
            # time / capture-count throttle.  ``incomplete=True`` marker
            # is set by the helper; the final ``save_session_npz`` at
            # end-of-run overwrites with ``incomplete=False`` so the
            # POLARIS load surfaces an "incomplete run" badge only for
            # genuinely-crashed sessions.
            if self.save_path is not None:
                try:
                    (
                        _,
                        self._incr_last_save_at,
                        self._incr_last_capture_count,
                        did_write,
                    ) = save_session_npz_incremental(
                        self.runner.session,
                        self.save_path,
                        last_save_at=self._incr_last_save_at,
                        last_capture_count=self._incr_last_capture_count,
                    )
                    if did_write:
                        # Quietly note the snapshot in the log so the
                        # operator (and post-hoc analysis) can verify
                        # that mid-run saves are happening.  Use
                        # log_msg signal so it crosses to the GUI
                        # thread alongside the runner's normal log
                        # output.
                        self.log_msg.emit(
                            f"[partial-save] mid-run snapshot written "
                            f"({self._incr_last_capture_count} caps)")
                except Exception as e:
                    # Disk full, permission denied, network share
                    # disconnect, etc.  Surface as a log line but
                    # NEVER raise — losing one partial save is
                    # annoying, aborting the run over it is much
                    # worse.  The final end-of-run save will retry.
                    self.log_msg.emit(
                        f"[partial-save] failed: "
                        f"{type(e).__name__}: {e} — run continues, "
                        f"final save will retry")
        if ev.kind == "paused":
            # Surface the between-channels pause as its own signal so the
            # tab can pop a modal "rewire to next channel, then continue"
            # dialog and call runner.request_continue() on dismiss.
            self.paused.emit(ev.message or "Continue to next channel?")
        if ev.message:
            self.log_msg.emit(ev.message)

    def request_continue(self):
        """Forward a continue request to the underlying runner.

        Called from the GUI thread after the user dismisses the rewire
        dialog. The runner's continue event is thread-safe so no extra
        marshalling is needed.
        """
        try:
            self.runner.request_continue()
        except Exception:
            pass

    @QtCore.pyqtSlot()
    def run(self):
        # This is what actually runs on the worker thread — invoked by the
        # ``thread.started`` signal we connect in ``_BaseExperimentTab``.
        # It blocks until the experiment is complete or aborted.
        import time as _time
        run_start = _time.time()
        # Pre-run callable — typically the scope-setup SCPI burst the
        # tab used to run synchronously on the GUI thread (which froze
        # the UI on slow USB-TMC round-trips).  Moving it here keeps
        # the GUI responsive: Stop button works, log pane updates as
        # each ``log_msg.emit`` arrives, and any hang in a single SCPI
        # call is contained to the worker.  Failures during pre-run
        # don't abort the experiment outright — log them and let the
        # runner attempt the run anyway (the scope may be in a usable
        # state from a previous run's setup, or the bug might be
        # downstream).
        if self.pre_run is not None:
            try:
                self.pre_run()
            except Exception as e:
                import traceback as _tb_pre
                self.log_msg.emit(
                    f"⚠ Scope setup error ({type(e).__name__}): {e}\n"
                    + _tb_pre.format_exc())
        try:
            result = self.runner.run()
        except Exception as e:
            # Catch everything — including code bugs (NameError,
            # AttributeError, etc.) — so the worker NEVER dies
            # silently leaving the GUI stuck on "Stop" with the
            # worker thread orphaned.  Pre-flight ValueError /
            # RuntimeError are the expected hits; the rest are
            # bugs that the user will see as "ran aborted with X"
            # in the log while the GUI stays responsive and lets
            # them start another experiment.
            import traceback as _tb
            tb_text = _tb.format_exc()
            self.log_msg.emit(
                f"Run aborted ({type(e).__name__}): {e}\n{tb_text}")
            from ..experiments.base import ExperimentResult
            result = ExperimentResult(
                session=self.runner.session,
                aborted=True,
                error=f"{type(e).__name__}: {e}",
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
        # Trigger source — "EXT" for digital-sync, the physical channel
        # name of a Trigger-role channel (e.g. "CH4"), or the I_mon
        # channel name (e.g. "CH2") when no Trigger role is assigned.
        # Pushed by the Setup tab via :meth:`set_trigger_source`.
        self._trigger_source: str = "EXT"
        # Trigger edge slope — "RISE" or "FALL".
        # Pushed by the Setup tab via :meth:`set_trigger_slope`.
        # Only used when the trigger source is I_mon; for EXT and
        # channel-Trigger paths the slope is forced to RISE because
        # they're both TTL sync lines.
        self._trigger_slope: str = "RISE"
        # True when the trigger source is a TTL sync line (EXT BNC or
        # a channel carrying Role=Trigger).  Determines whether the
        # trigger setup uses fixed TTL semantics (slope=RISE, 1.4 V) or
        # the polarity-derived I_mon semantics (slope follows phase-1
        # sign, level from imon_trigger_level).
        # Pushed by the Setup tab via :meth:`set_digital_trigger`.
        self._trigger_is_digital: bool = True
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
        # Environment metadata sourced from the Setup tab's combo.
        # Drives the warning posture in the pre-run damage screen
        # (see :mod:`stimtest.damage_warnings`). Updated via
        # :meth:`set_environment` whenever the Setup tab fires its
        # ``environmentChanged`` signal; defaults to PBS so a
        # never-touched session still gets a reasonable posture.
        self._environment_short: str = "pbs"
        self._environment_custom: str = ""
        # Snapshot provider — a callable returning a dict of all
        # Setup-tab field values (see :meth:`SetupTab.setup_snapshot`).
        # Stamped into ``session.test.extras['setup_snapshot']`` at
        # start-run time so the Gamry-XLSX exporter can write a "Setup"
        # sheet listing every parameter the user configured. ``None``
        # means no provider was wired (e.g. tests / standalone use);
        # the runner just skips the snapshot in that case.
        self._setup_snapshot_provider: Optional[Callable[[], dict]] = None
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
        # Coalesce rapid pattern-changed bursts (typing into amplitude /
        # width spinboxes fires patternChanged on every keystroke; the
        # debounced sink renders once per ~60 ms window instead of per
        # keystroke). Direct/programmatic updates still go through
        # ``set_pattern`` for synchronous semantics.
        self.pattern_panel.patternChanged.connect(
            self.pattern_preview.set_pattern_debounced)
        # Hide the charge-balance summary in modes where the user can't
        # control balance manually (symmetric, triphasic, or auto-adjust).
        self.pattern_panel.balanceWarningVisibility.connect(
            self.pattern_preview.set_balance_visible)
        # Defer the first preview render until this tab is actually
        # SHOWN (used to fire on the next event-loop tick via
        # singleShot(0), which made cold-launch redraw the preview
        # for every experiment tab even if the user never opened
        # them).  ``_first_show_done`` is checked in
        # :meth:`showEvent` below; subsequent shows are no-ops.
        self._first_show_done = False

        # Combinations panel — every experiment now has one, so the user
        # can pick a multipolar configuration uniformly. SINGLE_CONFIG
        # subclasses (pulsing) constrain it to one combo at a time.
        self.combo_panel = CombinationPanel(single_mode=self.SINGLE_CONFIG)
        self.channel_grid.activesChanged.connect(self.combo_panel.set_actives)
        self.channel_grid.globalReturnChanged.connect(self.combo_panel.set_global_return)
        self.combo_panel.combinationHovered.connect(
            lambda active, rets, sp: self.channel_grid.set_highlight(
                active if active > 0 else None, rets, sp))
        # Gate the Start button on having at least one selected
        # configuration — without this, an operator could press Start
        # with an empty combo list and trip the runner's "No
        # configurations selected" preflight error mid-flight.  We
        # re-evaluate every time the combo selection or hardware
        # connection state changes; ``_refresh_start_enabled`` is the
        # single decision point (hardware AND combinations).
        self.combo_panel.combinationsChanged.connect(
            lambda _configs: self._refresh_start_enabled())
        self.combo_panel.set_array(array)

        self.start_btn = QtWidgets.QPushButton("Start")
        self.start_btn.setEnabled(False)
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        # Audit finding #9 — runner-side ``pause()`` isn't implemented on
        # any ExperimentRunner subclass. Until that lands, surfacing
        # the button to users is a UX trap: clicking it changes the
        # label to "Resume" and logs a message but doesn't actually
        # halt the experiment. Hide the control entirely; the
        # in-tab toggle plumbing + the matching ``_act_pause`` menu
        # action + F6 shortcut all stay wired so re-exposing it is a
        # single ``setVisible(True)`` flip once the runner gets a
        # real pause/resume implementation.
        self.pause_btn.setVisible(False)
        self.stop_btn = QtWidgets.QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_clicked)
        self.pause_btn.toggled.connect(self.pause_toggled)
        self.stop_btn.clicked.connect(self.stop_clicked)

        # Inner sub-tab widget — Parameters / <experiment label>. The
        # second tab takes its title from ``EXPERIMENTS[code].tab_title``
        # The ``Parameters`` sub-tab was previously inside an
        # ``inner_tabs`` QTabWidget alongside the experiment view. Per
        # user spec it has been promoted to a top-level "Test
        # parameters" tab next to Setup; ``self.params_page`` is now a
        # standalone QWidget that lives in MainWindow's tab bar (see
        # ``MainWindow._test_params_*`` plumbing). ExperimentTab still
        # owns the params_page widget reference for prefs / signal
        # wiring, but its OWN layout below holds only the experiment
        # view + the Start / Pause / Stop button row.
        self.params_page = QtWidgets.QWidget()
        self.experiment_page = QtWidgets.QWidget()

        # Start / Pause / Stop are placed at the BOTTOM of the
        # experiment widget so they're always visible while the user
        # has the experiment tab focused. ``self._button_row_w`` is
        # exposed as a public-ish member so MainWindow can re-parent
        # it to the Test parameters tab on tab-change events — the
        # user wants the same buttons in the same bottom-of-tab
        # position whether they're on Test parameters or the
        # experiment view.
        self._button_row_w = QtWidgets.QWidget()
        button_row = QtWidgets.QHBoxLayout(self._button_row_w)
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        button_row.addWidget(self.start_btn)
        button_row.addWidget(self.pause_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addStretch(1)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(2, 2, 2, 2)
        outer.addWidget(self.experiment_page, stretch=1)
        outer.addWidget(self._button_row_w)
        # The log pane is no longer placed inside the experiment tab —
        # MainWindow owns a single shared log panel pinned to the bottom
        # of the GUI so it's visible from every tab. The ``self.log_pane``
        # reference is replaced by MainWindow with the shared instance
        # after construction; until then it points at a local placeholder.

    def showEvent(self, event):
        """First-show hook — render the initial pattern preview.

        Done lazily so cold launch doesn't pay for a pyqtgraph
        rebuild per experiment tab even when the user never opens
        them.  ``_first_show_done`` guards against re-rendering on
        subsequent tab switches.
        """
        super().showEvent(event)
        if not getattr(self, "_first_show_done", True):
            try:
                self.pattern_preview.set_pattern(
                    self.pattern_panel.pattern())
            except Exception:
                pass
            self._first_show_done = True

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
        # Camera capture controls (periodic snapshot + video recording)
        # — applies to whatever experiment this tab runs.  Triggered
        # at run start by the runner; both toggles default OFF so a
        # run without camera intent is a pure no-op.  See
        # :meth:`_build_camera_capture_group`.
        left.addWidget(self._build_camera_capture_group())
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
            "External Return square = external counter"
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
        # Pin a hard minimum so the user can't accidentally drag the
        # ``params_h_split`` divider far enough right to hide the
        # channel selector + configuration combo panel. The h_split
        # has ``setCollapsible(1, False)`` already, but that only
        # blocks drag-collapse — explicit min-width also clamps
        # ``setSizes(...)`` calls that come in from a stale prefs
        # entry.
        right_scroll.setMinimumWidth(280)

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
        # Subclasses can request additional sub-tabs alongside the
        # multichannel scope (LP / PS add a "Tracking" tab). The hook
        # returns an iterable of (label, widget) pairs that the base
        # class wraps in a QTabWidget when non-empty; the bare
        # multichan-scope behaviour falls out unchanged when the hook
        # returns nothing.
        extra_tabs = list(self._extra_experiment_tabs() or ())
        if extra_tabs:
            self._experiment_inner_tabs = QtWidgets.QTabWidget()
            self._experiment_inner_tabs.setDocumentMode(True)
            # Suppress wheel-scroll tab switching — same rationale as
            # the main top-level tabs (an accidental scroll over the
            # tab bar shouldn't jump between Voltage Transient /
            # Tracking views).
            from .widgets import disable_tabbar_wheel_scroll
            disable_tabbar_wheel_scroll(self._experiment_inner_tabs)
            # First tab is the multichan scope — explicitly labelled
            # "Voltage Transient" per the user spec ("the voltage
            # transient tab is like the Voltage Transient
            # experiment"). The label reads identically across all
            # experiments so the user gets the same vocabulary.
            self._experiment_inner_tabs.addTab(
                self.multichan_scope, "Voltage Transient")
            for label, widget in extra_tabs:
                self._experiment_inner_tabs.addTab(widget, label)
            _scope_widget = self._experiment_inner_tabs
        else:
            _scope_widget = self.multichan_scope
        # Wrap the scope view + camera preview in a vertical splitter
        # so the operator can drag the boundary to give the camera
        # more / less screen real estate.  The CameraStreamPane is
        # HIDDEN by default and auto-shows when ``camera_service()``
        # connects — when no camera is in use, the splitter collapses
        # to the scope view alone (zero visual cost).
        from .camera import CameraStreamPane, camera_service
        self.camera_stream_pane = CameraStreamPane(
            parent=self, allow_snapshot_button=True)
        # Pin a real minimum height (220 px ≈ 480p-ish preview area)
        # so when the service connects and the pane goes visible, the
        # splitter actually allocates room.  Without this floor, a
        # splitter that's been laying out a hidden zero-size child can
        # leave the newly-visible pane at literal height 0 and the
        # operator sees nothing.
        self.camera_stream_pane.setMinimumHeight(220)
        scope_cam_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        scope_cam_split.setChildrenCollapsible(False)
        scope_cam_split.addWidget(_scope_widget)
        scope_cam_split.addWidget(self.camera_stream_pane)
        # Scope takes 3× the height of the camera pane by default;
        # operator can drag the boundary at runtime.
        scope_cam_split.setStretchFactor(0, 3)
        scope_cam_split.setStretchFactor(1, 1)
        self._scope_cam_split = scope_cam_split
        ep_split.addWidget(scope_cam_split)
        # On camera connect/disconnect, re-trigger the splitter to
        # redistribute heights.  Without this nudge, QSplitter doesn't
        # automatically grow the camera pane just because its child
        # widget flipped from setVisible(False) to setVisible(True) —
        # it keeps whatever sizes it had with the pane at 0 height.
        try:
            svc = camera_service()
            svc.connected.connect(self._on_camera_service_connected)
            svc.disconnected.connect(self._on_camera_service_disconnected)
        except Exception:
            pass
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

    def _on_camera_service_connected(self, _desc: str) -> None:
        """Force the scope+camera splitter to give the pane real
        screen space when the camera connects.

        QSplitter doesn't automatically grow a child that flips from
        invisible to visible — it preserves the prior sizes (with the
        invisible child at 0).  We re-set sizes explicitly using a 3:1
        scope:camera ratio over the current splitter total height.
        Safe if the splitter has 0 total height (skip — happens when
        the experiment tab hasn't been shown yet; the resize fires
        again on the next splitter event).
        """
        try:
            split = self._scope_cam_split
            total = sum(split.sizes())
            if total <= 0:
                # Splitter not laid out yet (tab never shown).  Use
                # the camera pane's minimum so it has SOMETHING when
                # it becomes visible.
                total = max(self.camera_stream_pane.minimumHeight() * 4,
                            600)
            scope_h = max(int(total * 0.7), 200)
            cam_h = max(int(total * 0.3),
                        self.camera_stream_pane.minimumHeight())
            split.setSizes([scope_h, cam_h])
        except Exception:
            pass

    def _on_camera_service_disconnected(self) -> None:
        """Give the scope the full splitter height when the camera
        disconnects (the pane auto-hides itself via its own slot;
        we just redistribute the now-free space).
        """
        try:
            split = self._scope_cam_split
            total = sum(split.sizes())
            if total > 0:
                # All to the scope; the hidden camera pane retains a
                # nominal 0 so its setSizes restore later doesn't
                # leave a stuck divider.
                split.setSizes([total, 0])
        except Exception:
            pass

    def _build_camera_capture_group(self) -> QtWidgets.QGroupBox:
        """Camera capture controls for the Test Parameters page.

        Two independent toggles + one interval:

          * **Periodic snapshot** — when on, the runner takes a JPEG
            snapshot via :func:`camera_service` every ``snapshot
            interval`` seconds for the duration of the run.  Default
            interval is 30 s; floor 1 s so a runaway timer can't
            saturate the disk.
          * **Video recording** — when on, the runner starts an MP4
            recording at run start and stops it at run end.  One
            clip per run; filename is timestamped at start.

        Both default OFF so a run without explicit camera intent is
        a pure no-op (no extra files, no QtMultimedia activity).  The
        group is HIDDEN ENTIRELY when the user hasn't connected a
        camera in the ConnectionPanel — same auto-show semantics as
        the experiment-tab stream pane.

        Group state round-trips via :meth:`current_prefs` /
        :meth:`restore_prefs` under the ``camera_capture`` key.
        """
        # Lazy import — camera module pulls QtMultimedia indirectly
        # (via camera_service first call), but the import itself is
        # cheap (just the file).
        from .camera import camera_service
        svc = camera_service()

        grp = QtWidgets.QGroupBox("Camera capture during run")
        grp.setToolTip(
            "Optional camera-driven artefacts saved during a run.  "
            "Requires a camera connected in the Hardware panel — "
            "the group hides when no camera is set up.  Both toggles "
            "default OFF.")
        gl = QtWidgets.QGridLayout(grp)
        gl.setContentsMargins(8, 6, 8, 6)
        gl.setHorizontalSpacing(8)
        gl.setVerticalSpacing(4)

        self.cam_snapshot_chk = QtWidgets.QCheckBox(
            "Periodic snapshot every")
        self.cam_snapshot_chk.setChecked(False)
        self.cam_snapshot_chk.setToolTip(
            "Save a JPEG to <repo>\\test\\ every N seconds for the "
            "duration of the run.  Useful for tracking electrolyte "
            "level, bubble formation on the electrode, indicator "
            "lamps on the bench during a long run.")
        self.cam_snapshot_interval = QtWidgets.QDoubleSpinBox()
        self.cam_snapshot_interval.setRange(1.0, 3600.0)
        self.cam_snapshot_interval.setSingleStep(5.0)
        self.cam_snapshot_interval.setDecimals(1)
        self.cam_snapshot_interval.setValue(30.0)
        self.cam_snapshot_interval.setSuffix(" s")
        self.cam_snapshot_interval.setToolTip(
            "Snapshot interval in seconds.  Floor 1 s (anything "
            "faster would saturate the disk on a webcam-quality JPEG "
            "stream and serves no diagnostic purpose).  Ceiling "
            "3600 s (1 hr).")
        # Disable the interval spinbox when the checkbox is off — a
        # disabled visual cue is clearer than just-ignored value.
        self.cam_snapshot_interval.setEnabled(False)
        self.cam_snapshot_chk.toggled.connect(
            self.cam_snapshot_interval.setEnabled)

        self.cam_record_chk = QtWidgets.QCheckBox(
            "Record MP4 video for the entire run")
        self.cam_record_chk.setChecked(False)
        self.cam_record_chk.setToolTip(
            "Save one MP4 clip per run to <repo>\\test\\.  Recording "
            "starts at run start and stops at run end; filename is "
            "timestamped at the start moment.  Disk cost is ~10-30 "
            "MB/min at default webcam resolution.")

        gl.addWidget(self.cam_snapshot_chk,         0, 0)
        gl.addWidget(self.cam_snapshot_interval,    0, 1)
        gl.addWidget(self.cam_record_chk,           1, 0, 1, 2)

        # Auto-show/hide tied to camera-service connection state.
        # We don't disable the toggles when no camera is connected —
        # the user can configure intent BEFORE plugging the camera
        # in and the runner will pick it up.  But we DO hide the
        # group entirely when QtMultimedia isn't available at all
        # (e.g. a stripped headless install) since the toggles
        # would be dead-on-click.
        try:
            if not svc.enumerate_devices():
                # QtMultimedia OK but no cameras yet — show the
                # group anyway so the user can pre-configure.
                pass
        except Exception:
            grp.setVisible(False)

        # Store the group on self for prefs round-trip + so the
        # runner-bind layer (added in a later task) can read state.
        self._camera_capture_group = grp
        return grp

    def _camera_capture_prefs(self) -> dict:
        """Snapshot the camera-capture toggle state for prefs.json."""
        try:
            return {
                "periodic_snapshot": bool(self.cam_snapshot_chk.isChecked()),
                "snapshot_interval_s": float(self.cam_snapshot_interval.value()),
                "record_video": bool(self.cam_record_chk.isChecked()),
            }
        except Exception:
            return {}

    def _restore_camera_capture_prefs(self, p: dict) -> None:
        """Restore camera-capture toggle state from prefs.json."""
        if not isinstance(p, dict):
            return
        try:
            if "periodic_snapshot" in p:
                self.cam_snapshot_chk.setChecked(bool(p["periodic_snapshot"]))
            if "snapshot_interval_s" in p:
                self.cam_snapshot_interval.setValue(
                    float(p["snapshot_interval_s"]))
            if "record_video" in p:
                self.cam_record_chk.setChecked(bool(p["record_video"]))
        except Exception:
            pass

    def _extra_experiment_tabs(self):
        """Subclass hook — return an iterable of ``(label, widget)``
        pairs to inject as additional sub-tabs alongside the
        multichannel scope. Base implementation returns an empty
        tuple (no extra tabs); LP / PS override to add a Tracking
        sub-tab via the shared :class:`TrackingPlot` widget."""
        return ()

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
        # swap needed. Prefer the array's own ``layout`` attribute (which
        # the Setup tab fills from either the catalog DeviceDef or the
        # user's Square/Hexagonal chooser for custom grids); fall back
        # to the catalog lookup by name for older arrays without the
        # attribute.
        layout = getattr(array, "layout", None) or self._device_layout_for(array.name)
        self.channel_grid.set_array(array, layout=layout)
        # Push the array into the combination panel too (only set if the
        # subclass added one — VT does, the others don't yet).
        if hasattr(self, "combo_panel") and self.combo_panel is not None:
            self.combo_panel.set_array(array)
        # Forward the array to the Tracking plot if this tab has one
        # (LP / PS only). The tracking plot needs the active site's
        # surface area to convert I_stim µA → A/cm² when the user
        # toggles I_stim's display unit.
        tracking = getattr(self, "tracking_plot", None)
        if tracking is not None and hasattr(tracking, "set_array"):
            try:
                tracking.set_array(array)
            except Exception:
                # Tracking is informational only — never let an
                # array-forwarding hiccup interrupt the array swap.
                pass

    @staticmethod
    def _device_layout_for(name: str) -> str:
        from ..config import DEVICES
        d = DEVICES.get(name)
        return getattr(d, "layout", "rect") if d is not None else "rect"

    def set_aliases(self, aliases: dict):
        """Cache the scope-channel role mapping AND push it through to
        the multi-channel scope so the trace dropdowns hide rows the
        scope isn't actually capturing.

        ``aliases`` is keyed by lowercase role tag (``vmon`` / ``imon``
        / ``eret`` / ``eact`` / ``trigger``) and maps to a physical
        channel string (``"CH1"`` etc.) or ``None`` for unassigned.
        We translate to the multi-channel scope's title-case
        constants (``V_mon`` / …) for the trace-availability set.
        """
        self._aliases = dict(aliases)
        # Map alias keys → multi-channel-scope trace tags. Trigger is
        # excluded (it's a scope role but not a plotted waveform).
        from .multichannel_scope import (
            TRACE_VMON, TRACE_IMON, TRACE_EACT, TRACE_ERET,
        )
        alias_to_trace = {
            "vmon": TRACE_VMON, "imon": TRACE_IMON,
            "eact": TRACE_EACT, "eret": TRACE_ERET,
        }
        available = []
        for key, trace in alias_to_trace.items():
            phys = self._aliases.get(key)
            if phys:                            # assigned to a real CH
                available.append(trace)
        if (hasattr(self, "multichan_scope")
                and self.multichan_scope is not None):
            # MultiChannelScope's set_available_traces also handles
            # the E_ret → E_act inclusion rule, so we just hand it the
            # raw assigned set.
            self.multichan_scope.set_available_traces(available)

    def set_setup_snapshot_provider(self, provider: Optional[Callable[[], dict]]):
        """Cache a callable that returns a fresh Setup-tab snapshot
        dict (see :meth:`SetupTab.setup_snapshot`).

        Pulled at start-run time so the snapshot reflects the user's
        most recent edits — not whatever was current when the tab was
        first wired up. ``None`` clears the provider; the runner will
        just skip the ``setup_snapshot`` extras key in that case.
        """
        self._setup_snapshot_provider = provider

    def _stamp_extras(self, session: "Session") -> None:
        """Inject the latest Setup snapshot and the per-experiment
        parameters snapshot into ``session.test.extras`` so the Gamry-
        XLSX exporter can write them out.

        Called from each subclass's ``_start_run`` (or equivalent)
        right after constructing the :class:`Session`. The setup
        snapshot is fetched from the cached provider; the params
        snapshot comes from :meth:`params_snapshot` which subclasses
        override to record any test-parameters tab fields not already
        on ``session.test`` (ramp policy, mode, strategy, etc.).
        Either one is silently skipped if its source returns nothing.
        """
        try:
            if self._setup_snapshot_provider is not None:
                snap = self._setup_snapshot_provider()
                if snap:
                    session.test.extras["setup_snapshot"] = snap
        except Exception:
            # Snapshotting must never block a run — a stale GUI control
            # raising an exception shouldn't cost the user their data.
            pass
        try:
            params = self.params_snapshot()
            if params:
                session.test.extras["params_snapshot"] = params
        except Exception:
            pass

    def params_snapshot(self) -> dict:
        """Return per-experiment test-parameters fields not already
        captured on ``session.test``. Subclasses override.

        Default is empty — kinds with no extra fields just don't add
        anything. Used by :meth:`_stamp_extras` to thread the data
        through to ``session.test.extras['params_snapshot']``.
        """
        return {}

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

    def set_environment(self, short_code: str, custom_text: str = ""):
        """Cache the user-selected Environment (Setup tab combo).

        Wired into the Setup tab's ``environmentChanged`` signal via
        MainWindow. Values default to ``"pbs"`` / ``""`` so a never-
        touched session still has a sensible posture for the pre-
        run damage screen.

        Parameters
        ----------
        short_code : str
            Canonical environment short_code from
            :data:`stimtest.environments.ENVIRONMENT_PRESETS` (e.g.
            ``"pbs"``, ``"rat_cortex"``, ``"custom"``).
        custom_text : str, optional
            Free-form description used when ``short_code == "custom"``.
            Empty for non-custom presets.
        """
        self._environment_short = str(short_code or "pbs").strip()
        self._environment_custom = str(custom_text or "").strip()

    def _pre_run_warning_check(
        self, pattern, surface_area_um2: float,
        *, max_amplitude_ua: Optional[float] = None,
    ) -> bool:
        """Pre-run damage screen — call from each ``start_clicked``.

        Returns ``True`` to proceed, ``False`` to abort. The caller
        should bail (i.e. ``return`` from ``start_clicked``) on
        ``False`` without starting the runner.

        Behaviour by warning level:

        * No warning → silent True.
        * ``info`` posture → log a single line, return True (no
          modal so benchtop sweeps don't get a click-through every
          Start).
        * ``warn`` posture → modal with Cancel as default; True
          only if the user clicks "Proceed anyway".
        * ``alert`` posture → modal with Critical icon and the
          same Cancel-default logic; the synthesised body lists
          every flagged criterion and the literature caveat.

        Failures inside the warning module are caught and treated
        as "no warning" — the runner never aborts a Start due to
        a bug in the damage screen.
        """
        try:
            from ..damage_warnings import assess_planned_run
        except Exception:
            return True
        try:
            warning = assess_planned_run(
                pattern=pattern,
                surface_area_um2=float(surface_area_um2),
                environment_short=self._environment_short,
                max_amplitude_ua=max_amplitude_ua,
            )
        except Exception:
            return True
        if warning is None:
            return True
        # Info posture: log only, never block.
        if warning.level == "info":
            self.log_pane.log(
                f"[damage screen] {warning.title}\n{warning.body}")
            return True
        # warn / alert: render a modal.
        from PyQt6 import QtWidgets as _QtW
        icon = (_QtW.QMessageBox.Icon.Warning
                if warning.level == "warn"
                else _QtW.QMessageBox.Icon.Critical)
        box = _QtW.QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle(warning.title)
        box.setText("Pre-run damage screen flagged the planned parameters.")
        box.setInformativeText(warning.body)
        cancel_btn = box.addButton(_QtW.QMessageBox.StandardButton.Cancel)
        proceed_btn = box.addButton(
            "Proceed anyway",
            _QtW.QMessageBox.ButtonRole.AcceptRole)
        # Default to Cancel so an accidental Enter-press doesn't
        # blow past the warning. ``alert`` posture additionally
        # surfaces the warning in the log pane so a missed dialog
        # still leaves a record.
        box.setDefaultButton(cancel_btn)
        if warning.level == "alert":
            self.log_pane.log(
                f"[DAMAGE ALERT] {warning.title}\n{warning.body}")
        else:
            self.log_pane.log(
                f"[damage warning] {warning.title}\n{warning.body}")
        box.exec()
        return box.clickedButton() is proceed_btn

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

    def set_trigger_source(self, source: str):
        """Store the trigger source pushed from the Setup tab.

        ``source`` is ``"EXT"`` for the digital-sync BNC or the physical
        channel name (e.g. ``"CH2"``) when I_mon is used as the trigger.
        Applied to the scope in ``_start_runner`` before the first capture.
        """
        self._trigger_source = str(source) if source else "EXT"

    def set_trigger_slope(self, slope: str):
        """Store the trigger edge slope pushed from the Setup tab.

        ``slope`` is ``"RISE"`` or ``"FALL"``.
        Applied to the scope in ``_start_runner`` before the first capture.
        """
        self._trigger_slope = slope if slope in ("RISE", "FALL") else "RISE"

    def set_digital_trigger(self, is_digital: bool):
        """Store whether the trigger source is a TTL sync line.

        Pushed by the Setup tab via ``digitalTriggerChanged`` whenever
        the EXT checkbox toggles or a channel's role changes between
        ``Trigger`` and anything else.  ``True`` means the trigger
        setup uses fixed TTL semantics (slope=RISE, level=1.4 V);
        ``False`` means it falls back to I_mon's polarity-derived
        slope and amplitude-derived level.
        """
        self._trigger_is_digital = bool(is_digital)

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
        self._refresh_start_enabled()

    def clear_hardware(self):
        self._stim = None; self._scope = None
        self._refresh_start_enabled()
        self.stop_btn.setEnabled(False)

    def _refresh_start_enabled(self) -> None:
        """Single decision point for the Start button's enabled state.

        Requires BOTH:
          * Hardware initialised (``self._stim is not None`` — set by
            :meth:`set_hardware`)
          * At least one combination selected in the combo panel

        Wired to:
          * ``combo_panel.combinationsChanged`` — fires whenever the
            user adds, removes, or toggles a configuration row.
          * ``set_hardware`` / ``clear_hardware`` — fires when the
            Connection panel reports a stim init / close.

        Also sets a tooltip on the button explaining why it's disabled
        so the operator doesn't have to guess.
        """
        # Guard against partial construction: this slot is wired to
        # ``combo_panel.combinationsChanged`` in ``__init__`` BEFORE
        # ``self.start_btn`` is created, and ``set_array(...)`` later
        # in ``__init__`` fires the signal during initial combo
        # population.  Without this guard we'd raise AttributeError
        # several times per tab during app startup (visible as
        # tracebacks in the PULSAR.bat console).
        if not hasattr(self, "start_btn"):
            return
        has_hw = self._stim is not None
        has_cfg = False
        try:
            has_cfg = bool(self.combo_panel.selected_configurations())
        except Exception:
            has_cfg = False
        enabled = has_hw and has_cfg
        self.start_btn.setEnabled(enabled)
        if not enabled:
            if not has_hw and not has_cfg:
                tip = ("Initialize the stimulator and select at least "
                       "one configuration before starting.")
            elif not has_hw:
                tip = ("Initialize the stimulator on the Setup tab "
                       "before starting.")
            else:
                tip = ("Select at least one configuration in the "
                       "combinations list before starting.")
            self.start_btn.setToolTip(tip)
        else:
            self.start_btn.setToolTip("")

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
        # Pattern panel state — sub-dict so the keys can't collide with
        # the field-walked ones above.
        out["pattern"] = self.pattern_panel.current_prefs()
        # Channel-grid state — the External Return toggle gates which
        # configuration kinds the combo panel even offers, so it must
        # round-trip alongside the combo prefs to keep them coherent.
        # Actives are intentionally NOT persisted — they're per-session
        # picks, not configuration choices.
        if hasattr(self, "channel_grid") and self.channel_grid is not None:
            out["channel_grid"] = {
                "global_return": bool(self.channel_grid.global_return()),
            }
        # Combination-panel state (mode + spacing + filter toggles) so
        # the user's last picked configuration kind / spacing survives
        # a restart. Stored under its own key for the same reason.
        if hasattr(self, "combo_panel") and self.combo_panel is not None:
            out["combo"] = self.combo_panel.current_prefs()
        # Multi-channel scope toggles — visibility checkboxes, the
        # per-trace L/R axis assignment, and the inset state.
        if (hasattr(self, "multichan_scope")
                and self.multichan_scope is not None):
            out["multichan_scope"] = self.multichan_scope.current_prefs()
        # Tracking-plot state — metric-axis assignments + visible-key
        # toggles. Audit finding #13: the TrackingPlot widget exposed
        # ``current_prefs`` / ``restore_prefs`` but the base class
        # never walked it, so LP / PS users' picks didn't survive a
        # restart. Wire it through here so the same round-trip
        # pattern as multichan_scope applies. Only LP and PS tabs
        # construct a tracking_plot; VT and SP harmlessly skip this
        # branch because ``getattr`` returns None.
        tracking = getattr(self, "tracking_plot", None)
        if tracking is not None and hasattr(tracking, "current_prefs"):
            out["tracking_plot"] = tracking.current_prefs()
        # Camera-capture toggle state (periodic snapshot interval +
        # video recording on/off).  Round-tripped under its own key
        # so a tab built before the camera-capture group existed
        # doesn't see an unrecognised legacy key.
        try:
            out["camera_capture"] = self._camera_capture_prefs()
        except Exception:
            pass
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
            except (TypeError, ValueError):
                pass
        if "pattern" in p:
            self.pattern_panel.restore_prefs(p["pattern"])
        # Apply channel-grid prefs BEFORE the combo prefs — the
        # External Return toggle drives which kinds the combo panel
        # exposes, so a saved kind like "Bipolar" can only be restored
        # once the dropdown reflects the right External-Return state.
        cg = p.get("channel_grid")
        if (isinstance(cg, dict) and "global_return" in cg
                and hasattr(self, "channel_grid")
                and self.channel_grid is not None):
            self.channel_grid.set_global_return(bool(cg["global_return"]))
        if ("combo" in p and hasattr(self, "combo_panel")
                and self.combo_panel is not None):
            self.combo_panel.restore_prefs(p["combo"])
        if ("multichan_scope" in p and hasattr(self, "multichan_scope")
                and self.multichan_scope is not None):
            self.multichan_scope.restore_prefs(p["multichan_scope"])
        # Tracking-plot prefs — see ``current_prefs`` for the
        # corresponding write side (audit #13). LP / PS tabs have a
        # ``tracking_plot``; VT / SP don't.
        tracking = getattr(self, "tracking_plot", None)
        if ("tracking_plot" in p and tracking is not None
                and hasattr(tracking, "restore_prefs")):
            try:
                tracking.restore_prefs(p["tracking_plot"])
            except Exception:
                # Prefs restore should never abort the tab init;
                # silently skip a corrupt tracking-plot dict and
                # the widget reverts to its construction defaults.
                pass
        # Camera-capture toggles (periodic snapshot + record).  Safe
        # to call even when the camera-capture group hasn't been
        # built yet (older subclasses that override _assemble_pages
        # without inheriting the camera group); the inner method
        # silently no-ops on missing widgets.
        if "camera_capture" in p:
            try:
                self._restore_camera_capture_prefs(p["camera_capture"])
            except Exception:
                pass
        # Nudge preview to reflect the loaded values
        QtCore.QTimer.singleShot(0, self._refresh_preview)

    # -------------------------------------------------------------- view state
    def view_state(self) -> dict:
        """Snapshot the splitter positions + pattern-preview height
        for this tab.

        Three splitters live inside an experiment tab: the params-page
        horizontal split (params column vs. channel/combo column), the
        right-column vertical split between the channel grid and the
        combo panel, and the experiment-page horizontal split between
        the live scope view and the metrics side panel. Each is
        recorded by name so a future addition doesn't break old prefs.
        The pattern-preview's user-resized height (set via the
        widget's own ``↕+`` / ``↕−`` buttons) is persisted alongside
        the splitter sizes.

        Splitter sizes containing a zero (a fully-collapsed pane,
        whether reached by drag or by a stale restore) are NOT
        saved — restoring those next session would leave the user
        staring at a panel they can't see. Skipping the entry forces
        the splitter to fall back to its built-in stretch-factor
        default, which always gives every pane non-zero room.
        """
        out: dict = {"pattern_preview": self.pattern_preview.view_state()}
        for key, splitter in (
            ("params_h_split",     self._params_h_split),
            ("right_v_split",      self._right_v_split),
            ("experiment_h_split", self._experiment_h_split),
        ):
            sizes = list(splitter.sizes())
            if all(int(s) > 0 for s in sizes):
                out[key] = sizes
        return out

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
            # Reject saved splitter sizes where either pane has
            # collapsed to zero (or near-zero) — a stale entry
            # from a past layout (or a user who dragged a divider
            # all the way over) would otherwise leave the user
            # staring at a panel they can't see. Falling back to
            # the splitter's built-in stretch-factor defaults
            # gives the channel-selection / combination-panel
            # column the room it expects.
            try:
                int_sizes = [int(s) for s in sizes]
            except (TypeError, ValueError):
                continue
            if any(s < 50 for s in int_sizes):
                continue
            try:
                splitter.setSizes(int_sizes)
            except (TypeError, ValueError):
                pass
        pp_state = view.get("pattern_preview")
        if isinstance(pp_state, dict):
            self.pattern_preview.restore_view_state(pp_state)

    def _start_runner(self, runner: ExperimentRunner, save_name: str):
        # ---- Re-entry guard + IMMEDIATE Start-button disable -------
        # MUST come before any ``processEvents`` / ``_tick()`` /
        # modal-dialog call below.  Without these two lines a rapid
        # double-click on Start (or a queued click that lands during
        # the scope-setup ``_tick()`` calls a few hundred lines
        # below) spawns a SECOND ``_start_runner`` call.  Both
        # invocations then race through the PlexStim DLL
        # (``stim.reinit`` → ``ps_close_all_stim``) and PyVISA scope
        # (``single_capture`` → ``CURVe?`` / preamble query)
        # simultaneously.  PlexStim DLL is single-producer
        # (see CLAUDE.md "PlexStim DLL is not thread-safe"); two
        # concurrent calls corrupt the heap and the GUI dies with
        # ``STATUS_HEAP_CORRUPTION (0xC0000374)``.  faulthandler
        # caught the double-thread pattern explicitly:
        #   Thread d560: voltage_transient.run → reinit → ps_close_all_stim
        #   Thread 4568: voltage_transient.run → _one_capture → single_capture
        # — both inside ``run()`` of the SAME runner, two threads.
        #
        # ``_start_in_progress`` is set FIRST (synchronous, no event
        # loop pump) and cleared in the matching ``_on_finished`` so
        # a re-entry sees the flag and bails before touching any
        # hardware.  The Start-button disable is the user-facing UX
        # signal; the flag is the actual correctness guarantee.
        if getattr(self, "_start_in_progress", False):
            try:
                self.log_pane.log(
                    "Start re-entry blocked (a previous Start press "
                    "is still configuring scope / spawning worker). "
                    "Wait for the experiment view to appear, then "
                    "use Stop if you want to abort.")
            except Exception:
                pass
            return
        self._start_in_progress = True
        try:
            self.start_btn.setEnabled(False)
        except Exception:
            pass
        # ---- Exception-safety wrapper around the setup body --------
        # ``_start_runner_body`` does the scope-setup + worker-build
        # work (lines previously inlined here).  Splitting it out lets
        # this wrapper own the flag-clear / UI-restore logic without
        # forcing a 460-line indentation of the body inside a
        # try/finally.  Two ways out:
        #   • Normal: body returns having called ``worker_thread.start()``.
        #     The flag stays set and will be cleared by ``_on_finished``
        #     once the run actually completes.
        #   • Exception: any uncaught raise from ``configure_channels``,
        #     ``mkdir``, ``RunnerWorker(...)``, or a Qt signal-connect
        #     lands here.  We log it, clear the flag, and roll back the
        #     UI to "ready for next Start press" so the user isn't
        #     permanently locked out by a one-off setup failure.
        try:
            self._start_runner_body(runner, save_name)
        except Exception as _start_err:
            try:
                self.log_pane.log_now(
                    f"Start failed during setup — rolling back: "
                    f"{type(_start_err).__name__}: {_start_err}")
            except Exception:
                pass
            self._start_in_progress = False
            try:
                self.start_btn.setEnabled(True)
            except Exception:
                pass
            try:
                self.stop_btn.setEnabled(False)
            except Exception:
                pass
            try:
                self.pause_btn.setEnabled(False)
            except Exception:
                pass
            try:
                self._set_locked(False)
            except Exception:
                pass
            try:
                _win = self.window()
                if hasattr(_win, "statusBar"):
                    _win.statusBar().clearMessage()
            except Exception:
                pass
            # Surface the failure to the user via a non-modal status
            # bar message too — the log pane scrolls off-screen during
            # a long sweep, but a status-bar line stays put.
            try:
                _win = self.window()
                if hasattr(_win, "statusBar"):
                    _win.statusBar().showMessage(
                        f"Start failed: {type(_start_err).__name__} — "
                        f"check the log pane for details.", 10_000)
            except Exception:
                pass

    def _start_runner_body(self, runner: ExperimentRunner, save_name: str):
        """Scope-setup + worker construction for :meth:`_start_runner`.

        Split out so :meth:`_start_runner` can wrap the call in a
        try/except that rolls back the UI + clears
        ``_start_in_progress`` on any uncaught setup error, without
        indenting this entire ~450-line body inside a try block.

        Any exception raised here propagates to the wrapper which
        does the cleanup.  Returns having called
        ``self._worker_thread.start()`` on the normal path; the flag
        clear for that path happens in :meth:`_on_finished` when the
        worker thread emits ``finished``.
        """
        # Defensive cleanup: if a prior run died ungracefully (e.g. an
        # uncaught code bug that bypassed _on_finished), there may be
        # a leftover worker / thread holding GIL-side resources and
        # signal connections.  Reassigning ``self._worker_thread``
        # below without freeing the old one would leak a QThread and
        # — worse — leave dangling queued connections that fire on
        # the new worker's signals.  Quit + wait the old thread here
        # so the slate is clean before we build the next pair.
        if self._worker_thread is not None:
            try:
                self._worker_thread.quit()
                self._worker_thread.wait(2000)   # 2 s hard cap
            except Exception:
                pass
            self._worker_thread = None
        self._worker = None
        self._runner = None
        # ---- User-spec: re-init the stim if a prior Stop closed it
        # Quote: "When you restart, initialize the stimulator."
        # ``_stim_needs_init`` is set in :meth:`_on_finished` after
        # a Stop-triggered close.  We init HERE — before the scope
        # setup — so:
        #   * the runner about to be built can take a fully-init'd
        #     stim (no special "is it connected?" handling in the
        #     runner)
        #   * a failure to init aborts the Start cleanly via the
        #     wrapper's except clause, restoring UI state and
        #     surfacing the error to the operator
        # No-op when the stim wasn't torn down (normal end-of-run
        # → next Start path).
        if getattr(self, "_stim_needs_init", False):
            self.log_pane.log_now(
                "Re-initializing stimulator for new run "
                "(was closed by previous Stop)…")
            try:
                if getattr(self, "_stim", None) is not None:
                    self._stim.open()
                    _serial = ""
                    try:
                        _serial = str(
                            getattr(self._stim.info,
                                    "serial_number", "") or "")
                    except Exception:
                        pass
                    self.log_pane.log_now(
                        f"Stimulator re-initialized "
                        f"(PS_InitAllStim, S/N "
                        f"{_serial or 'unknown'}).")
                    self._stim_needs_init = False
                else:
                    # No stim object at all — operator never
                    # initialized it via ConnectionPanel.  Surface
                    # a friendly error instead of silently failing
                    # downstream when the runner tries to use it.
                    raise RuntimeError(
                        "No stimulator object available — open the "
                        "Setup tab → Connection panel → Initialize "
                        "stimulator before pressing Start.")
            except Exception as _stim_init_err:
                # Let the wrapper's except clause handle UI
                # rollback.  Re-raise so the rest of
                # _start_runner_body doesn't proceed without a
                # working stim.
                raise RuntimeError(
                    f"Stimulator re-init failed: "
                    f"{type(_stim_init_err).__name__}: "
                    f"{_stim_init_err}.  Power-cycle the "
                    f"stimulator + re-Initialize from the "
                    f"Connection panel."
                ) from _stim_init_err
        # Reset the log pane's elapsed-time clock so every line emitted
        # for this run counts from "Start clicked" — mirroring the
        # MATLAB convention where each script begins with ``tic``. The
        # named ``"experiment"`` timer is matched in :meth:`_on_finished`
        # to emit a ``getEndTime``-style "completed in X.YZ unit" line.
        self.log_pane.reset_clock()
        self.log_pane.tic("experiment")
        # Surface the snapshot contract explicitly: the runner has
        # already captured everything from the Setup tab (active
        # channel, returns, pattern, ramp, surface area, …) and
        # subsequent edits in Setup will NOT affect this run.  We
        # print a one-liner so the operator doesn't go fiddling
        # mid-sweep expecting the new value to land.
        try:
            self.log_pane.log_now(
                "Setup-tab parameters were snapshotted at Start — "
                "edits to Setup during this run are ignored "
                "(they take effect on the next Start press).")
        except Exception:
            pass
        # Snapshot Qt-widget state the scope-setup SCPI burst needs
        # — read it NOW on the GUI thread, while we have safe access
        # to ``self.pattern_panel``.  The burst itself runs later on
        # the worker thread (see ``RunnerWorker.pre_run`` wiring
        # below) so a slow USB-TMC round-trip in setup can no longer
        # freeze the GUI for 5-30 seconds.
        try:
            self._scope_setup_pattern = (self.pattern_panel.pattern()
                                         if hasattr(self, "pattern_panel")
                                         else None)
        except Exception:
            self._scope_setup_pattern = None
        if self._scope is not None:
            # All log() calls in this scope-setup block use ``_log`` =
            # :meth:`LogPane.log_now`, which forces a GUI repaint after
            # each line.  Without that, the ~15-30 synchronous SCPI
            # round-trips below freeze the event loop for several
            # seconds and every queued log line lands at once when
            # control finally returns.  The file mirror is unaffected
            # (line-buffered, writes synchronously); this is purely
            # for the on-screen "what is it doing now?" feedback.
            _log = self.log_pane.log_now
            # Turn OFF every channel first, then ON only the ones that are
            # actually mapped — same discipline as the calibration sweep.
            # Leaving unused channels enabled causes CURVe? errors on scopes
            # that don't allow reading a disabled channel, and adds noise.
            try:
                _scope_info = getattr(self._scope, "info", None)
                _n_ch = int(getattr(_scope_info, "n_channels", 4) or 4)
                _used_channels = set(self._aliases.values())
                _log(
                    f"Scope setup: turning OFF unused channels, "
                    f"keeping {sorted(_used_channels)} ON")
                for _ci in range(1, _n_ch + 1):
                    try:
                        self._scope._w(f"SELect:CH{_ci} OFF")
                    except Exception:
                        pass
                for _ch in _used_channels:
                    try:
                        self._scope._w(f"SELect:{_ch} ON")
                    except Exception:
                        pass
            except Exception:
                pass
            self._scope.configure_channels(self._aliases)
            # Surface "scope is being configured" in the status bar so
            # the operator can read at a glance why the experiment view
            # hasn't shown up yet.  Cleared when the runner starts.
            try:
                _win = self.window()
                if hasattr(_win, "statusBar"):
                    _win.statusBar().showMessage(
                        "Configuring scope (this can take 10-30 s on "
                        "TBS2000 with large records)…")
            except Exception:
                pass
            _log(
                f"Scope setup: configure_channels {self._aliases}")
            # Per-channel bandwidth (model-aware via TekSeriesSpec.
            # recommended_imon_bw):  full BW on V_mon to keep pulse
            # leading edges, 20 MHz limit on I_mon so the trigger
            # comparator gets a clean signal.
            try:
                vmon_ch = self._aliases.get("vmon", "CH1")
                imon_ch = self._aliases.get("imon", "CH2")
                bw_v = self._scope.set_channel_bandwidth_for_purpose(
                    vmon_ch, "vmon")
                bw_i = self._scope.set_channel_bandwidth_for_purpose(
                    imon_ch, "imon")
                if bw_v is not None or bw_i is not None:
                    _log(
                        f"Scope setup: bandwidth limits — "
                        f"{vmon_ch} (V_mon) ≤ {bw_v:.0f} MHz, "
                        f"{imon_ch} (I_mon) ≤ {bw_i:.0f} MHz "
                        f"(less I_mon noise → cleaner trigger).")
            except Exception:
                pass
            # ---- Event-loop pump for the scope-setup block ---------
            # Scope setup runs on the GUI thread (NOT the runner
            # worker — the runner is built AFTER setup completes).
            # Each slow scope SCPI write — especially
            # ``set_record_length(20000)`` on TBS2204B which triggers
            # an internal 10-30 s buffer reallocation — blocks the
            # event loop and freezes the GUI: the operator sees the
            # whole window stop responding for tens of seconds after
            # pressing Start, with the log pane only catching up in
            # a burst at the end (every log line during that window
            # WAS written to the on-disk file via the fsync path —
            # see widgets.LogPane — but the QPlainTextEdit can't
            # repaint while the GUI thread is blocked).
            #
            # ``_tick()`` calls ``QApplication.processEvents()`` to
            # drain pending paint events.  Sprinkle it after every
            # human-readable log line AND after every slow scope
            # call so the log pane catches up in real time and
            # button clicks / tab switches stay responsive.
            #
            # Proper fix would be to move scope setup to a worker
            # thread (and that's tracked separately), but pumping
            # events here is a one-liner that eliminates the
            # "Start button freezes the GUI" symptom without the
            # signal-wiring complexity of a true worker.
            def _tick():
                try:
                    QtWidgets.QApplication.processEvents()
                except Exception:
                    pass
            try:
                # Experiments use the 20 k record length (driver default).
                # 20 k @ 50 ns/pt over a 1 ms window gives ~4000 samples per
                # 200 µs phase — plenty for the Cisnal-derivative access edge
                # and E_pol-at-12-µs metrics, well below the scope's analog
                # bandwidth, and avoids the multi-second per-capture transfer
                # cost of 200 k / 2 M / 5 M.  See DEFAULT_RECORD_LENGTH in
                # ``tektronix.py`` for the full rationale.
                from ..hardware.tektronix import DEFAULT_RECORD_LENGTH
                _log(
                    f"Scope setup: record length = {DEFAULT_RECORD_LENGTH}")
                _tick()
                self._scope.set_record_length(DEFAULT_RECORD_LENGTH)
                _tick()
                _log(
                    f"Scope setup: acquisition mode = {self._acq_mode}, "
                    f"n_avg = {self._acq_n_avg}")
                _tick()
                self._scope.set_acquisition_mode(self._acq_mode,
                                                 n_avg=self._acq_n_avg)
                _tick()
                # Trigger setup — three mutually exclusive paths driven
                # by the Setup tab state.  See SetupTab.is_digital_trigger
                # for the priority rules.
                #
                #   1. EXT BNC checkbox checked → source="EXT".  The
                #      firmware owns the level on EXT; we still pass
                #      1.4 V as a sensible default but the driver
                #      no-ops the level write for EXT sources (see
                #      tektronix.set_trigger_level).
                #   2. A channel has Role=Trigger (typically CH3/CH4
                #      wired to the Plexon digital sync) → source=that
                #      channel, slope=RISE, level=1.4 V (TTL midpoint).
                #      Active-high sync, no polarity dependence.
                #   3. Fallback to the I_mon channel → source=that
                #      channel, slope follows phase-1 polarity (RISE
                #      for anodic-first, FALL for cathodic-first),
                #      level from imon_trigger_level(amp, pw).
                #
                # Logged so the operator can read back which path was
                # taken without having to grep source.
                TTL_LEVEL_V = 1.4   # TTL midpoint (sync lines are 3.3 V / 5 V)
                slope_resolved = self._trigger_slope
                trig_level = TTL_LEVEL_V
                if self._trigger_is_digital:
                    # Path 1 or 2 — TTL sync line, fixed setup.
                    slope_resolved = "RISE"
                    trig_level = TTL_LEVEL_V
                    _path_note = (
                        "EXT BNC" if self._trigger_source == "EXT"
                        else f"channel-Trigger ({self._trigger_source})")
                    _log(
                        f"Scope setup: trigger path = {_path_note}, "
                        f"slope = RISE, level = {TTL_LEVEL_V*1000:.0f} mV "
                        f"(TTL sync line — polarity and amplitude "
                        f"derivation skipped)"
                        + (" (EXT — scope firmware may auto-set level)"
                           if self._trigger_source == 'EXT' else ''))
                else:
                    # Path 3 — I_mon channel trigger.  Slope follows
                    # phase-1 polarity, level from MATLAB formula.
                    try:
                        from ..experiments.base import imon_trigger_level
                        _pat = self.pattern_panel.pattern()
                        # Use the FIRST PHASE of the pattern — NOT the
                        # "excitation phase" — for slope + level.  The
                        # trigger sees whichever phase fires FIRST, and
                        # for some patterns (anodic-first protocols,
                        # certain triphasic shapes) ``phases[0]`` and
                        # ``excitation_phase`` differ.  ``phases[0]`` is
                        # what's actually first at the I_mon edge, so
                        # its polarity is what determines the trigger
                        # slope: cathodic phase 1 (amp < 0) → FALL,
                        # anodic phase 1 (amp > 0) → RISE.
                        _ph0 = (_pat.phases[0]
                                if _pat and _pat.phases else None)
                        _amp_signed = (float(_ph0.amplitude_ua)
                                       if _ph0 is not None else 0.0) or 10.0
                        _pw0 = (float(_ph0.width_us)
                                if _ph0 is not None else 200.0)
                        # Use the actual stimulator scaling so the
                        # threshold gets clamped below the expected
                        # peak on NIL (1 mV/µA) devices.
                        _si = getattr(self._stim, "info", None)
                        _imon_vpu = float(
                            getattr(_si, "imon_scaling_v_per_ua", 0.0) or 0.0)
                        trig_level = imon_trigger_level(
                            _amp_signed, _pw0,
                            imon_v_per_ua=_imon_vpu or None)
                        # Cathodic-first (amp < 0) → I_mon dips negative
                        # at onset → trigger on FALL.  Anodic-first
                        # (amp > 0) → I_mon rises at onset → trigger on
                        # RISE.  The Setup-tab slope toggle is ignored
                        # in this path because polarity is unambiguous
                        # from the pattern.
                        slope_resolved = "FALL" if _amp_signed < 0 else "RISE"
                    except Exception:
                        trig_level = 0.05
                        slope_resolved = self._trigger_slope
                        _amp_signed = 0.0
                    _log(
                        f"Scope setup: trigger path = I_mon "
                        f"({self._trigger_source}), slope = "
                        f"{slope_resolved} (from phase-1 polarity "
                        f"{_amp_signed:+.1f} µA), level = "
                        f"{trig_level*1000:+.2f} mV "
                        f"(from imon_trigger_level)")
                _tick()
                self._scope.set_trigger(
                    source=self._trigger_source,
                    level_v=trig_level,
                    slope=slope_resolved,
                    mode="NORMAL",
                    # The 1.2 µs Plexon digital-sync offset applies for
                    # EXT BNC AND for any channel tagged Role=Trigger
                    # in the Setup tab (same TTL wire, different
                    # physical input).  Forward the Setup-tab flag so
                    # the driver shifts the time axis + horizontal
                    # layout for either path.
                    digital=self._trigger_is_digital,
                )
                _tick()
                pat = self.pattern_panel.pattern()
                if pat and pat.phases:
                    phase1_us = pat.phases[0].width_us
                    interphase_us = pat.phases[0].delay_after_us
                    _log(
                        f"Scope setup: horizontal layout for "
                        f"phase1={phase1_us:.0f} µs, "
                        f"interphase={interphase_us:.0f} µs, "
                        f"phase2={pat.phases[1].width_us if len(pat.phases) > 1 else 0.0:.0f} µs, "
                        f"discharge={pat.phases[1].delay_after_us if len(pat.phases) > 1 else 0.0:.0f} µs "
                        f"(ext_trigger={self._trigger_source == 'EXT'})")
                    _tick()
                    _scale_s, _pos_pct = self._scope.auto_layout_for_pulse(
                        phase1_us=phase1_us,
                        interphase_us=interphase_us,
                        phase2_us=pat.phases[1].width_us if len(pat.phases) > 1 else 0.0,
                        discharge_us=pat.phases[1].delay_after_us if len(pat.phases) > 1 else 0.0,
                        ext_trigger=(self._trigger_source == "EXT"),
                    )
                    _tick()
                    _n_divs = float(getattr(self._scope, "_n_horiz_divs", 10.0))
                    _log(
                        f"Scope setup: applied {_scale_s*1e6:.2f} µs/div, "
                        f"position = {_pos_pct:.2f}% "
                        f"(window = {_scale_s*1e6 * _n_divs:.1f} µs total)")
                    _tick()
                    # Initial vertical channel scales from amplitude + hardware.
                    # This sets a sensible starting scale so the first capture
                    # isn't clipped and the runner's per-capture adapt_channel_scale
                    # can fine-tune from there.
                    try:
                        from ..config import IMON_SCALING_DEFAULT, VMON_SCALING_DEFAULT
                        from ..experiments.base import imon_vertical_scale
                        _stim_info = getattr(self._stim, "info", None)
                        _imon_vpu = float(
                            getattr(_stim_info, "imon_scaling_v_per_ua",
                                    IMON_SCALING_DEFAULT) or IMON_SCALING_DEFAULT)
                        _vmon_vpv = float(
                            getattr(_stim_info, "vmon_scaling_v_per_v",
                                    VMON_SCALING_DEFAULT) or VMON_SCALING_DEFAULT)
                        _amp0 = (abs(pat.excitation_phase.amplitude_ua)
                                 if pat.excitation_phase else 0.0) or 10.0
                        # V_mon: load-agnostic estimate from initial_channel_scales
                        # (load_r/c default to 0 → snaps to a generous 1 V/div).
                        # The runner's adapt_channel_scale converges from here.
                        scales = self._scope.initial_channel_scales(
                            amp_ua=_amp0,
                            imon_v_per_ua=_imon_vpu,
                            vmon_v_per_v=_vmon_vpv,
                            phase_us=phase1_us,
                        )
                        vmon_ch = self._aliases.get("vmon", "CH1")
                        imon_ch = self._aliases.get("imon", "CH2")
                        _vmon_vpd = scales.get("CH1", 1.0)
                        _imon_vpd = imon_vertical_scale(
                            _amp0, imon_v_per_ua=_imon_vpu)
                        _log(
                            f"Scope setup: vertical scale "
                            f"{vmon_ch} (V_mon) = {_vmon_vpd*1000:.2f} mV/div, "
                            f"{imon_ch} (I_mon) = {_imon_vpd*1000:.2f} mV/div "
                            f"(sized for I_stim = {_amp0:.0f} µA)")
                        self._scope.set_channel_scale(vmon_ch, _vmon_vpd)
                        self._scope.set_channel_scale(imon_ch, _imon_vpd)
                        _log(
                            f"Scope setup: vertical position {vmon_ch} = 0 div, "
                            f"{imon_ch} = 0 div")
                        self._scope.set_channel_position(vmon_ch, 0.0)
                        self._scope.set_channel_position(imon_ch, 0.0)
                    except Exception as _vsc_err:
                        _log(f"Vertical scale init: {_vsc_err}")
                    vmon_ch = self._aliases.get("vmon", "CH1")
                    self._scope.set_cursors(phase1_us, interphase_us,
                                           source_channel=vmon_ch)
                else:
                    self._scope.set_horizontal_position(30.0)
            except Exception as e:
                _log(f"Scope setup failed: {e}")
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
        # Between-channels rewire prompt — the runner emits "paused" before
        # each non-first config when the tab's pause toggle is on.  The slot
        # pops a modal QMessageBox; clicking OK calls request_continue().
        self._worker.paused.connect(self._on_runner_paused)
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
        # Push the electrode surface area down to the scope view so
        # the right-axis I_mon trace flips to current density (A/cm²)
        # when an area is configured.  Pull from the runner first
        # (VT exposes ``surface_area_um2`` directly); fall back to
        # the first array site (PS/SP/LP read it lazily per channel
        # in their runner loops, so the runner attribute doesn't
        # exist).  ``None`` keeps the default µA presentation.
        _area_um2 = 0.0
        try:
            _area_um2 = float(getattr(runner, "surface_area_um2", 0.0) or 0.0)
        except (TypeError, ValueError):
            _area_um2 = 0.0
        if _area_um2 <= 0:
            try:
                _sites = getattr(self._array, "sites", None) or []
                if _sites:
                    _area_um2 = float(
                        getattr(_sites[0], "surface_area_um2", 0.0) or 0.0)
            except Exception:
                _area_um2 = 0.0
        self.multichan_scope.set_surface_area_um2(
            _area_um2 if _area_um2 > 0 else None)
        # Pre-emptively register the configuration(s) the runner is
        # about to attempt — the entry list shows up immediately, not
        # only after the first capture lands. Pull the configs from
        # the runner so multi-config sweeps (VT) get every queued
        # combination listed up front; falls back to the single
        # ``session.test.configuration`` for sweep-less runners
        # (SP / LP / PS), which only stage one config per runner
        # construction. Best-effort — a runner that doesn't expose
        # either attribute just relies on the lazy first-capture path.
        try:
            configs_attempting = (
                getattr(runner, "configurations", None)
                or [runner.session.test.configuration]
            )
            for cfg in configs_attempting:
                self.multichan_scope.add_pending(str(cfg.display_name()))
        except Exception:
            pass
        # (The previous "auto-switch to the Experiment sub-tab" call
        # is gone now that Parameters is a top-level tab in
        # MainWindow's tab bar; the experiment widget is the
        # experiment view itself, no inner sub-tabs to flip.)
        # ---- Camera capture start hooks ---------------------------
        # If the Test Parameters tab's camera-capture toggles are on,
        # arm the shared ``camera_service()`` for this run.  All
        # state lives in self._camera_run_state so _on_finished can
        # tear it down cleanly even if the run aborts.  Best-effort:
        # any failure here logs but doesn't block the experiment.
        try:
            self._arm_camera_for_run()
        except Exception as _cam_err:
            self.log_pane.log(
                f"[camera] run-arm failed (continuing without capture): "
                f"{type(_cam_err).__name__}: {_cam_err}")
        # Clear the "Configuring scope..." status-bar message —
        # everything that runs from this point lives on the worker
        # thread so the GUI is responsive again.
        try:
            _win = self.window()
            if hasattr(_win, "statusBar"):
                _win.statusBar().clearMessage()
        except Exception:
            pass
        self._worker_thread.start()
        # Re-entry guard stays SET throughout the worker's run so a
        # re-press of Start (if the button somehow got re-enabled
        # programmatically) can't queue a second runner.  The flag
        # is cleared in :meth:`_on_finished` once the worker emits
        # ``finished``.  The wrapper :meth:`_start_runner` clears it
        # on the exception path (setup failed mid-stride).

    # ---------------- Camera capture during run ------------------
    def _arm_camera_for_run(self) -> None:
        """If the camera-capture toggles are on, start a periodic
        snapshot timer and/or video recording for the duration of
        this run.

        State lives in ``self._camera_run_state`` so :meth:`_on_finished`
        can tear it down on ALL exit paths (normal end, abort, error).
        No-op when the user hasn't toggled either option on, OR when
        no camera is connected (the toggles can be pre-configured
        before plugging in the camera; the runner just skips them
        if the camera isn't ready).
        """
        from .camera import camera_service
        svc = camera_service()
        state: dict = {"timer": None, "was_recording_start": False}
        self._camera_run_state = state
        if not svc.is_connected():
            # User pre-configured the toggles but no camera is up —
            # silently skip.  Log so the operator sees why no clips
            # appear.
            try:
                snap_on = bool(self.cam_snapshot_chk.isChecked())
                rec_on = bool(self.cam_record_chk.isChecked())
            except Exception:
                snap_on = rec_on = False
            if snap_on or rec_on:
                self.log_pane.log(
                    "[camera] run-arm: camera-capture toggles are on "
                    "but no camera is connected — connect one in the "
                    "Hardware panel to enable capture.")
            return
        # Recording: starts once, lasts the whole run.
        try:
            if bool(self.cam_record_chk.isChecked()):
                out = svc.start_recording()
                if out is not None:
                    state["was_recording_start"] = True
                    self.log_pane.log(
                        f"[camera] recording started for this run → {out}")
        except Exception:
            pass
        # Periodic snapshot: QTimer fires on the GUI thread (cheap;
        # the actual file write is async via QImageCapture).
        try:
            if bool(self.cam_snapshot_chk.isChecked()):
                interval_ms = max(
                    1000, int(self.cam_snapshot_interval.value() * 1000))
                timer = QtCore.QTimer(self)
                timer.setInterval(interval_ms)
                timer.timeout.connect(svc.take_snapshot)
                timer.start()
                state["timer"] = timer
                self.log_pane.log(
                    f"[camera] periodic snapshot armed: "
                    f"every {self.cam_snapshot_interval.value():.1f} s "
                    f"for the duration of the run.")
        except Exception:
            pass

    def _disarm_camera_after_run(self) -> None:
        """Stop the per-run camera capture (timer + recording).

        Called from :meth:`_on_finished` on EVERY exit path.  Safe
        when nothing was armed (the arm step is no-op when toggles
        are off / no camera connected, and this method checks the
        state dict before touching anything).
        """
        state = getattr(self, "_camera_run_state", None)
        if not state:
            return
        # Stop the snapshot timer first so a stray fire mid-teardown
        # doesn't queue a snapshot after recording has stopped.
        timer = state.get("timer")
        if timer is not None:
            try:
                timer.stop()
                timer.deleteLater()
            except Exception:
                pass
        # Stop the recording (the file gets finalised in the
        # recorder's stoppedState handler — see camera.py).
        if state.get("was_recording_start"):
            try:
                from .camera import camera_service
                camera_service().stop_recording()
            except Exception:
                pass
        self._camera_run_state = None

    @QtCore.pyqtSlot(object, int)
    def _on_capture(self, capture, channel: int):
        # Route the capture into the per-entry list/stack. The list
        # key is the **configuration display name** (e.g. ``"CH05"``
        # for monopolar, ``"CH05 v 06"`` for bipolar) so two combos
        # that share an active channel produce distinct list rows
        # — the previous int-keyed path collapsed them into one. We
        # read the display name from the live runner; if no runner
        # is present (race / late capture) fall back to the legacy
        # int channel key so the capture still lands somewhere.
        key = self._capture_key(channel)
        self.multichan_scope.add_capture(capture, key)
        # Mirror the latest capture's metrics into the side panel so
        # the user can read live numbers without switching list rows.
        self.metrics_side.show_capture(capture)
        # Forward to the optional Tracking sub-tab (LP / PS only).
        # The widget plots metric evolution over time; each capture
        # contributes one sample per (key, metric) pair.
        tracking = getattr(self, "tracking_plot", None)
        if tracking is not None:
            try:
                tracking.add_capture(capture, key)
            except Exception:
                # Tracking is purely informational — never let a plot
                # failure interrupt the experiment runner. Logs go
                # through the standard log pane via the runner.
                pass

    def _capture_key(self, channel: int):
        """Stable list key for the live capture event.

        Picks the configuration's ``display_name()`` when a runner is
        active so multipolar combinations sharing an active channel
        get distinct list rows. Falls back to the bare ``int`` channel
        when no runner is reachable (race during shutdown, capture
        replayed without a runner) so the capture still lands somewhere
        sensible. ``channel <= 0`` collapses to ``-1`` for the unknown-
        channel sentinel — same convention as before.
        """
        runner = getattr(self, "_runner", None)
        cfg = None
        if runner is not None:
            # Runners track the live config under various names —
            # ``current_configuration`` for sweep-style runners,
            # ``configuration`` for single-config ones, and the
            # ``session.test.configuration`` fallback for everything
            # else. Use the first that resolves.
            for attr in ("current_configuration", "configuration"):
                cfg = getattr(runner, attr, None)
                if cfg is not None:
                    break
            if cfg is None:
                sess = getattr(runner, "session", None)
                test = getattr(sess, "test", None) if sess is not None else None
                cfg = getattr(test, "configuration", None)
        if cfg is not None:
            try:
                return str(cfg.display_name())
            except Exception:
                pass
        return channel if channel > 0 else -1

    @QtCore.pyqtSlot(str, str)
    @QtCore.pyqtSlot(str)
    def _on_runner_paused(self, message: str):
        """Pop a modal "rewire to next channel" prompt and release the
        runner when the user clicks Continue.

        The runner thread is parked inside ``wait_for_continue`` until
        :meth:`RunnerWorker.request_continue` fires.  Clicking Stop on
        the dialog requests an abort instead, which also unblocks the
        runner via its ``_continue_event``.
        """
        if self._worker is None:
            return
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Re-wire next channel")
        box.setIcon(QtWidgets.QMessageBox.Icon.Information)
        box.setText(message or "Re-wire to the next channel, then click "
                                "Continue.")
        box.setInformativeText(
            "Stimulation has been stopped while you swap the cable. "
            "Click <b>Continue</b> once the next channel is connected.")
        cont_btn = box.addButton("Continue",
                                 QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        stop_btn = box.addButton("Stop run",
                                 QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(cont_btn)
        box.exec()
        if box.clickedButton() is stop_btn:
            # Abort the run — the runner's wait_for_continue sees the abort
            # flag and exits the loop, then the runner unwinds normally.
            try:
                self._worker.runner.abort()
            except Exception:
                pass
        else:
            try:
                self._worker.request_continue()
            except Exception:
                pass

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
        # Clear the re-entry guard FIRST.  ``_start_runner_body``
        # leaves ``_start_in_progress = True`` for the duration of
        # the worker run; this is the normal-path clear that
        # re-arms Start for the next press.  The exception-path
        # clear lives in :meth:`_start_runner`'s except clause.
        # Safe to call when the flag was never set (legacy / test
        # paths that bypass ``_start_runner``).
        self._start_in_progress = False
        # Disarm camera capture FIRST so a stray snapshot timer
        # can't fire after the run has logically ended.  Safe to
        # call when nothing was armed.
        try:
            self._disarm_camera_after_run()
        except Exception:
            pass
        self.log_pane.log(f"Experiment finished. Captures: {len(result.captures)}.")
        # MATLAB convention (PlexStimTek.m: "Experiment completed: X.YZ unit").
        # ``toc`` auto-scales the unit through ``LogPane.format_scaled``,
        # mirroring ``getEndTime.m``. Logs a no-op nan if the tic was
        # cleared by an intervening ``reset_clock`` (e.g. user re-Started).
        self.log_pane.toc("experiment", label="Experiment")
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.pause_btn.blockSignals(True)
        self.pause_btn.setChecked(False)
        self.pause_btn.setText("Pause")
        self.pause_btn.blockSignals(False)
        # Use the unified gate so the post-run Start state also
        # respects the "at least one combination selected" rule.
        self._refresh_start_enabled()
        if self._worker_thread is not None:
            self._worker_thread.quit()
            self._worker_thread.wait()
            self._worker_thread = None
        self._worker = None
        self._runner = None
        # ---- User-spec: close the stim if Stop was pressed -----
        # ``_stim_needs_close_after_run`` is set in
        # :meth:`stop_clicked`.  We do the close HERE — not in
        # stop_clicked — so it happens AFTER ``wait()`` above
        # has confirmed the runner thread is fully dead.
        # Closing earlier would race the runner's last DLL call
        # (it might still be in its finally-block ``stop_all``
        # at the moment Stop was clicked, especially if the
        # runner was deep in a slow USB-TMC scope read).  The
        # ``_dll_lock`` would prevent a heap race, but the
        # runner would still see its next call hit a closed
        # device and raise — better to defer until it's gone.
        # Marks ``_stim_needs_init`` so the next Start press
        # re-initializes (see :meth:`_start_runner_body`).
        if getattr(self, "_stim_needs_close_after_run", False):
            try:
                if getattr(self, "_stim", None) is not None:
                    self._stim.close()
                    self.log_pane.log(
                        "Stimulator closed (PS_CloseAllStim) — "
                        "next Start will re-initialize.")
            except Exception as _close_err:
                self.log_pane.log(
                    f"Stimulator close failed (continuing — "
                    f"next Start will still attempt re-init): "
                    f"{type(_close_err).__name__}: {_close_err}")
            self._stim_needs_close_after_run = False
            self._stim_needs_init = True
        # If a sequential queue has more configs lined up, mark the
        # active channel as completed and chain to the next entry —
        # keeping the params locked across the gap so the user never
        # sees inputs become editable mid-queue. If a run aborted
        # (preflight failure or user Stop), drain the queue so we
        # don't keep firing failed runs.
        if getattr(result, "aborted", False):
            self._pending_configs = []
        # Mark EVERY combination that actually ran as ✓ in the entry
        # list.  Iterates ``result.session.runs`` — each ChannelRun
        # exposes the config it was for via ``.configuration`` —
        # rather than the OLD ``session.test.configuration`` lookup
        # which only returns the STATIC first-config reference and
        # leaves configs 2+ permanently unmarked.  Falls back to the
        # static config when ``runs`` is empty (single-config
        # runners or runs that aborted before any config completed).
        try:
            runs = getattr(result.session, "runs", None) or []
            marked_any = False
            for _run in runs:
                _cfg = getattr(_run, "configuration", None)
                if _cfg is None:
                    continue
                try:
                    self.multichan_scope.mark_completed(
                        str(_cfg.display_name()))
                    marked_any = True
                except Exception:
                    pass
            if not marked_any:
                # Empty runs list (preflight failure / runner that
                # aborted before any config produced output) — fall
                # back to the static config so SOMETHING gets marked.
                cfg_done = result.session.test.configuration
                self.multichan_scope.mark_completed(
                    str(cfg_done.display_name()))
        except Exception:
            pass
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
            # ---- User-spec: tear down the stim on Stop ----------
            # Quote: "When you stop, abort stim and close the
            # stimulator.  When you restart, initialize the
            # stimulator."  Rationale: leaves the device in a
            # FULLY CLEAN state between runs.  The next Start
            # press re-initializes (see _start_runner_body).
            # This sidesteps the close-while-pulsing
            # HEAP_CORRUPTION (CLAUDE.md gotcha #31) at the
            # source — by the time the runner calls reinit at
            # the top of run(), the device is already aborted
            # and closed, so reinit's open() lands on a fully
            # quiet device.
            #
            # ``abort_all`` (= PS_AbortAll) is DLL-safe in ANY
            # trigger mode and aborts a pulse already in flight,
            # unlike ``stop_all`` which returns error 4 outside
            # SOFT trigger mode.  The ``_dll_lock`` re-entrant
            # mutex serializes us against any in-flight runner
            # DLL call so we wait for the runner to release the
            # lock before we touch the device.
            #
            # The actual ``close()`` happens later in
            # :meth:`_on_finished` — AFTER the runner thread has
            # fully exited (via ``_worker_thread.wait()``).
            # Closing here would race the runner's next DLL call
            # (it might still be inside ``_one_capture`` waiting
            # on a slow USB-TMC read; on return it'll try to
            # call ``stop_all`` in its finally, and a closed
            # device would raise from inside the runner).  The
            # flag ``_stim_needs_close_after_run`` carries the
            # intent across the GUI / worker boundary.
            try:
                if getattr(self, "_stim", None) is not None:
                    self._stim.abort_all()
                    self.log_pane.log(
                        "Stimulator aborted (PS_AbortAll).")
            except Exception as _e:
                self.log_pane.log(
                    f"Stimulator abort_all failed "
                    f"(continuing — close will still fire after "
                    f"the runner exits): "
                    f"{type(_e).__name__}: {_e}")
            self._stim_needs_close_after_run = True

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
    # VT mode constants — saved in prefs JSON.
    # The "Fixed current" mode was folded into "Fixed charge/phase":
    # that mode now exposes a 3-way lock (current / width / Q_ph) so
    # the user picks which of the three is the auto-derived knob,
    # making Fixed-current a degenerate case (lock = current with the
    # other two pinned). Old prefs containing the legacy string are
    # silently ignored on restore (the combo stays at the default).
    MODE_FIXED_QPH = "Fixed charge/phase"
    MODE_FIXED_QD = "Fixed charge density"
    MODE_MAX = "Maximum"
    STRAT_INCR = "Fixed increment"
    STRAT_REGR = "Adaptive (regression)"
    STRAT_PRED = "Predictive (ML)"

    #: Lock-target options for the 3-way Fixed-Q_ph lock UI. Whichever
    #: parameter is locked is the one the runner / handlers AUTO-DERIVE
    #: from the other two via ``Q_ph = I_stim · W / 1000`` (nC).
    QPH_LOCK_CURRENT = "current"
    QPH_LOCK_WIDTH = "width"
    QPH_LOCK_QPH = "qph"

    def __init__(self, array, parent=None):
        super().__init__(array, parent)

        # ----- mode + strategy -----
        # Mode picks whether to ramp at all. Fixed-charge/phase = run
        # a single pulse train at the user-set (current, width, Q_ph)
        # triple with one of the three auto-derived; Maximum = ramp
        # to find the water-window threshold and report max Q_inj.
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems([self.MODE_FIXED_QPH,
                                  self.MODE_FIXED_QD, self.MODE_MAX])
        self.mode_combo.setCurrentText(self.MODE_MAX)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        self.mode_combo.setToolTip(
            "Voltage-Transient run mode.<br><br>"
            "<b>Fixed charge per phase</b> — single train at a "
            "user-set (current, width, Q_ph) triple; two of the "
            "three are typed, the locked one auto-derives.<br>"
            "<b>Fixed charge density</b> — single train at the "
            "target Q_inj you enter (current adjusted for the "
            "active site's surface area).<br>"
            "<b>Maximum</b> — ramp upward to the safe-stimulation "
            "ceiling; the runner reports the maximum Q_inj the "
            "electrode tolerates without polarising past the water "
            "window.")

        # ----- Fixed-charge/phase 3-way lock UI -----
        # Three coupled spinboxes (current, width, Q_ph) plus a
        # button group of three radio toggles. Whichever toggle is
        # checked marks the auto-derived knob; the other two are
        # user-editable. Default lock = Q_ph so the panel behaves like
        # the legacy "Fixed current" mode at first glance (the user
        # sets current and width, Q_ph follows). The toggle group is
        # exclusive — only one can be active.
        self.qph_lock_current = QtWidgets.QRadioButton("Lock current")
        self.qph_lock_width   = QtWidgets.QRadioButton("Lock phase width")
        self.qph_lock_qph     = QtWidgets.QRadioButton("Lock charge/phase")
        self._qph_lock_group = QtWidgets.QButtonGroup(self)
        self._qph_lock_group.setExclusive(True)
        self._qph_lock_group.addButton(self.qph_lock_current, 0)
        self._qph_lock_group.addButton(self.qph_lock_width,   1)
        self._qph_lock_group.addButton(self.qph_lock_qph,     2)
        self.qph_lock_qph.setChecked(True)   # default: Q_ph derives
        self._qph_lock_group.buttonToggled.connect(self._on_qph_lock_changed)

        from ..config import (STIM_CURRENT_RESOLUTION_UA,
                               STIM_CURRENT_UI_STEP_UA,
                               STIM_TIME_RESOLUTION_US,
                               STIM_MAX_AMPLITUDE_UA)
        # Three coupled inputs. ``current`` and ``width`` mirror the
        # pattern panel's amp_excite / width_shared widgets (we sync
        # them bidirectionally so editing here updates the pulse and
        # vice versa); ``qph`` is owned by this tab.
        self.qph_current = RepeatingDoubleSpinBox()
        self.qph_current.setRange(STIM_CURRENT_RESOLUTION_UA, STIM_MAX_AMPLITUDE_UA)
        self.qph_current.setDecimals(1); self.qph_current.setSingleStep(STIM_CURRENT_UI_STEP_UA)
        self.qph_current.setSuffix(" µA")
        self.qph_current.setToolTip(
            "Excitation-phase amplitude. One of the three locked "
            "triplet inputs (current, width, Q_ph). Edits here "
            "propagate to the pulse pattern's excitation phase and "
            "re-derive whichever value is locked.")
        self.qph_width = RepeatingDoubleSpinBox()
        self.qph_width.setRange(STIM_TIME_RESOLUTION_US, 50000.0)
        self.qph_width.setDecimals(0); self.qph_width.setSingleStep(STIM_TIME_RESOLUTION_US)
        self.qph_width.setSuffix(" µs")
        self.qph_width.setToolTip(
            "Excitation-phase width. One of the three locked "
            "triplet inputs (current, width, Q_ph). Edits propagate "
            "to the pattern panel and re-derive whichever knob is "
            "locked. PlexStim 2.0 timing grid is 1 µs.")
        self.qph_qph = RepeatingDoubleSpinBox()
        self.qph_qph.setRange(0.001, 1e6); self.qph_qph.setDecimals(3)
        self.qph_qph.setSingleStep(0.5); self.qph_qph.setSuffix(" nC")
        self.qph_qph.setToolTip(
            "Charge per excitation phase, in nC. Q_ph = current × "
            "width × shape duty. Editing this with current or width "
            "locked re-derives the unlocked knob to match.")
        # Seed initial values from the pattern panel so we start
        # consistent with whatever pulse is already shaped.
        self.qph_current.setValue(abs(float(self.pattern_panel.amp_excite.value())))
        self.qph_width.setValue(float(self.pattern_panel.width_shared.value()))
        self.qph_qph.setValue(self.qph_current.value() * self.qph_width.value() / 1000.0)
        # Reentrancy guard for the 3-way coupling so a programmatic
        # setValue() doesn't ring back through the change handlers.
        self._qph_in_progress = False
        self.qph_current.valueChanged.connect(self._on_qph_input_changed)
        self.qph_width.valueChanged.connect(self._on_qph_input_changed)
        self.qph_qph.valueChanged.connect(self._on_qph_input_changed)
        # Mirror back from the pattern panel: if the user types into
        # amp_excite or width_shared directly, propagate the change
        # into our coupled triple here.
        self.pattern_panel.amp_excite.valueChanged.connect(
            self._on_pattern_amp_or_width_changed)
        self.pattern_panel.width_shared.valueChanged.connect(
            self._on_pattern_amp_or_width_changed)

        # The legacy "Charge per phase" readout label that used to sit
        # below the mode dropdown was removed — it was redundant with
        # the editable Q_ph spinbox in the 3-way lock UI, and its
        # value calculation didn't always reflect the actual pattern
        # (peak·width vs. shape-aware integral mismatch).

        # Pause-between-channels toggle.  When checked the runner stops
        # stim and waits for the operator to click Continue before moving
        # to the next configuration — useful when only one channel can be
        # wired up to the test/electrode rig at a time, so the operator
        # physically swaps wires between channels.  Not connected to a
        # handler here — the value is read at Start time and pushed onto
        # the runner via ``runner.pause_between_channels``.
        self.pause_between_channels_check = QtWidgets.QCheckBox(
            "Pause after each channel (rewire prompt)")
        self.pause_between_channels_check.setChecked(False)
        self.pause_between_channels_check.setToolTip(
            "When on, the runner stops stimulation after finishing one "
            "channel (configuration) and shows a 'Continue to next channel' "
            "dialog so you can physically re-wire the next channel before "
            "the sweep proceeds. Off = run all channels back-to-back.")

        # Ramp toggle for the Fixed modes — when checked the runner
        # walks from start_ua to max_ua (or start_qinj→max_qinj for the
        # charge-density mode) using the configured step.
        self.fixed_ramp_check = QtWidgets.QCheckBox("Ramp (sweep through values)")
        self.fixed_ramp_check.setChecked(False)
        self.fixed_ramp_check.toggled.connect(self._on_strategy_changed)
        self.fixed_ramp_check.setToolTip(
            "When checked, the Fixed-Q_ph and Fixed-Q_inj modes "
            "sweep from start_ua to max_ua (or start_qinj to "
            "max_qinj) instead of holding at the single target. "
            "Ignored in Maximum mode (which always ramps).")
        # Target charge density for the Fixed-Q_inj mode. Range covers
        # everything from sub-clinical (0.001 mC/cm²) to past the SIROF
        # safety limit (~5 mC/cm²) so the user can scan beyond the
        # water window if they're characterising a new coating.
        self.qinj_mc = RepeatingDoubleSpinBox()
        self.qinj_mc.setRange(0.001, 100.0); self.qinj_mc.setDecimals(3)
        self.qinj_mc.setSingleStep(0.05); self.qinj_mc.setValue(0.5)
        self.qinj_mc.setSuffix(" mC/cm²")
        self.qinj_mc.setToolTip(
            "Target charge-injection density for Fixed-Q_inj mode. "
            "The runner picks the current that delivers this Q_inj "
            "given the active site's surface area and the "
            "configured phase width. Typical SIROF safe limit is "
            "~3 mC/cm²; the range allows past that for "
            "characterising new coatings.")
        # Strategy is only meaningful in Maximum mode.
        #
        # Predictive (ML) is intentionally NOT added to the combo
        # in this build. The underlying data path (load the bundled
        # pickle / fall back to CSV, frozen-build-aware) is wired
        # up in ``QinjPredictor.load_default``, but the broader
        # predictive-mode UX (visible safety-factor effects on the
        # ramp, prediction-trail visualisation, the user-facing
        # explanation of when ML is preferable to plain regression)
        # isn't ready yet. To re-expose: append
        # ``self.STRAT_PRED`` to the ``addItems`` list and restore
        # the third tooltip bullet below.
        self.strategy_combo = QtWidgets.QComboBox()
        self.strategy_combo.addItems([self.STRAT_INCR, self.STRAT_REGR])
        self.strategy_combo.currentTextChanged.connect(self._on_strategy_changed)
        self.strategy_combo.setToolTip(
            "Maximum-mode ramp algorithm.<br><br>"
            "<b>Incremental</b> — fixed-step climb using "
            "coarse / fine step sizes.<br>"
            "<b>Regression</b> — fit V_d(I_stim) and project the "
            "next step from the fit.")

        # ----- ramp policy controls (Maximum mode) -----
        # Pattern shape comes from self.pattern_panel; start_ua is the
        # *initial* amplitude the ramp policy walks up from.
        # 0.1 µA resolution everywhere — matches the Plexon stim hardware.
        # 1000 µA ceiling matches the PlexStim 2.0 current-source rails.
        from ..config import (STIM_CURRENT_RESOLUTION_UA,
                               STIM_CURRENT_UI_STEP_UA, STIM_MAX_AMPLITUDE_UA)
        _step = STIM_CURRENT_RESOLUTION_UA   # hardware grid (still the range floor)
        _ui_step = STIM_CURRENT_UI_STEP_UA   # ergonomic 1 µA arrow / wheel step
        _max = STIM_MAX_AMPLITUDE_UA
        self.start_ua = RepeatingDoubleSpinBox()
        self.start_ua.setRange(_step, _max); self.start_ua.setSingleStep(_ui_step)
        self.start_ua.setDecimals(1); self.start_ua.setValue(5.0); self.start_ua.setSuffix(" µA")
        self.start_ua.setToolTip(
            "Initial excitation amplitude the ramp walks up from. "
            "Start small (~5 µA) so the first few captures sit "
            "well inside the safe-stimulation envelope before the "
            "ramp climbs into water-window territory.")
        self.coarse_ua = RepeatingDoubleSpinBox()
        self.coarse_ua.setRange(_step, 100); self.coarse_ua.setSingleStep(_ui_step)
        self.coarse_ua.setDecimals(1); self.coarse_ua.setValue(5.0); self.coarse_ua.setSuffix(" µA")
        self.coarse_ua.setToolTip(
            "Coarse step size used while the ramp is far from the "
            "predicted ceiling. Larger = faster ramp, less detail "
            "in the linear regime.")
        self.fine_ua = RepeatingDoubleSpinBox()
        self.fine_ua.setRange(_step, 100); self.fine_ua.setSingleStep(_ui_step)
        self.fine_ua.setDecimals(1); self.fine_ua.setValue(1.0); self.fine_ua.setSuffix(" µA")
        self.fine_ua.setToolTip(
            "Fine step size used once the ramp approaches the "
            "predicted ceiling. Smaller = more captures near the "
            "knee of the V_d(I) curve where the maximum lives.")
        self.max_ua = RepeatingDoubleSpinBox()
        self.max_ua.setRange(1, _max); self.max_ua.setSingleStep(_ui_step)
        self.max_ua.setDecimals(1); self.max_ua.setValue(_max); self.max_ua.setSuffix(" µA")
        self.max_ua.setToolTip(
            "Hard ceiling on excitation amplitude. The ramp stops "
            "here even if no V_d ceiling has been hit. Default "
            "matches the PlexStim 2.0 hardware rail (1000 µA).")
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
        # ----- Fixed-charge/phase 3-way lock rows -----
        # One row per (radio-toggle + spinbox) pair so the user reads
        # them as a coupled triple. The radio in each row marks that
        # parameter as the auto-derived knob; the spinbox shows its
        # current value (read-only when its own radio is checked).
        mode_form.addRow(self.qph_lock_current, self.qph_current)
        mode_form.addRow(self.qph_lock_width,   self.qph_width)
        mode_form.addRow(self.qph_lock_qph,     self.qph_qph)
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
        strategy_form.addRow("", self.pause_between_channels_check)
        strategy_form.addRow("Ramp strategy:", self.strategy_combo)
        self._strategy_form = strategy_form
        # Form rows for ramp parameters. Stored as label widgets so we
        # can hide entire rows when the strategy doesn't need them.
        self._ramp_rows: dict = {}
        ramp_form = rich.make_form()
        for key, label, widget in (
            ("start_ua",
             # Sweep starting amplitude — distinct from the
             # general I_stim (which describes the live pulse
             # amplitude). Using ``I_start`` here makes the
             # ramp-floor knob immediately distinguishable from
             # the per-pulse readouts elsewhere in the panel.
             rich.field_label("Starting current", rich.I_START, rich.UA),
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

        # Combine into one parameters group. Vertical order:
        #
        #   1. VT-mode form  (pick mode + see/set the charge target)
        #   2. Pulse pattern editor
        #   3. Ramp toggle + Ramp strategy
        #   4. Ramp policy box (start/coarse/fine/max/safety)
        #   5. Pulse pattern preview
        #
        # The ramp controls now sit ABOVE the live preview — the user
        # asked for the ramp toggle / policy box to be visible without
        # scrolling past the plot, since picking a ramp strategy is
        # part of "what's about to run", not feedback on the pattern.
        # The preview stays at the bottom so it remains the visual
        # endpoint of the parameter pipeline.
        params_box = QtWidgets.QGroupBox("Voltage Transient parameters")
        v = QtWidgets.QVBoxLayout(params_box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addLayout(mode_form)
        v.addWidget(self.pattern_panel)
        v.addLayout(strategy_form)
        v.addWidget(ramp_box)
        v.addWidget(self.pattern_preview)
        self._ramp_box = ramp_box

        # Apply initial visibility
        self._on_mode_changed()
        self._on_strategy_changed()

        # Hand off to the base class for the Parameters / Experiment sub-tabs
        self._assemble_pages(params_box)

    def _refresh_preview(self, *_):
        # Pattern panel emits patternChanged → preview is already wired in
        # the base class. Nothing extra to do here; ramp params don't
        # affect the displayed pulse shape.
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    def set_array(self, array):
        # Forward to the base class — channel grid + combo panel
        # update is all that's needed here; there's no longer a Q_ph
        # readout label that needed re-rendering on array change.
        super().set_array(array)

    def set_same_area(self, same: bool):
        """When the user has "Same for all electrodes" ticked, hide
        the Fixed-charge-density mode entry — uniform-area arrays
        make it equivalent to Fixed current (just a unit conversion
        the user shouldn't have to worry about)."""
        prev = self.mode_combo.currentText()
        self.mode_combo.blockSignals(True)
        try:
            self.mode_combo.clear()
            modes = [self.MODE_FIXED_QPH]
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
        is_fixed_qph = mode == self.MODE_FIXED_QPH
        is_fixed_qd = mode == self.MODE_FIXED_QD
        is_fixed_any = mode in (self.MODE_FIXED_QPH, self.MODE_FIXED_QD)
        # 3-way Fixed-Q_ph rows: visible only in MODE_FIXED_QPH. The
        # ``_set_form_row_visible`` helper looks up the row LABEL via
        # labelForField, so the field widget (spinbox) is the right
        # arg — passing the radio button (which IS the label) leaves
        # the spinbox stuck visible.
        self._set_form_row_visible(self.qph_current, is_fixed_qph)
        self._set_form_row_visible(self.qph_width,   is_fixed_qph)
        self._set_form_row_visible(self.qph_qph,     is_fixed_qph)
        self._set_form_row_visible(self.qinj_mc, is_fixed_qd)
        # Sync the 3-way coupled inputs from the current pattern when
        # entering MODE_FIXED_QPH — start from the live pulse so the
        # user doesn't see stale values from a previous session.
        if is_fixed_qph:
            self._sync_qph_inputs_from_pattern()
            self._on_qph_lock_changed()
        # In Fixed-charge-density mode the per-pulse current is derived
        # from Q_inj × area / phase_width per electrode, so the pattern
        # panel's Stimulation current input is misleading — hide it
        # (and the asymmetric per-phase amp inputs). Other shape
        # controls (widths, polarity, ratio) stay visible because
        # they're still under the user's control.
        self.pattern_panel.set_amplitude_visible(not is_fixed_qd)
        # Ramp toggle — exposed for BOTH Fixed-charge-density AND
        # Fixed-charge/phase (per user request). Ramping Q_ph runs a
        # sweep where the locked-knob is held while one of the other
        # two ramps; the runner reads the lock target via
        # ``self._qph_lock_group.checkedId()`` to know which axis to
        # sweep. Maximum mode is always a ramp so the toggle is
        # hidden there.
        ramp_toggle_visible = is_fixed_any
        self.fixed_ramp_check.setVisible(ramp_toggle_visible)
        self._set_form_row_visible(self.strategy_combo, is_max)
        ramp_visible = is_max or (ramp_toggle_visible
                                  and self.fixed_ramp_check.isChecked())
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

    # --- 3-way Q_ph lock (current / width / Q_ph) -------------------------
    # Coupling rule:   Q_ph [nC] = I_stim [µA] · W [µs] / 1000
    # Whichever radio is checked is the AUTO-DERIVED knob; the other
    # two are user-editable. Editing an unlocked spinbox triggers a
    # recomputation of the locked one. The currently-locked spinbox
    # is rendered read-only / italic via _set_lock_state so the user
    # sees at a glance which value is auto-driven.
    def _qph_lock_target(self) -> str:
        """Return which of {current, width, qph} is the locked
        (auto-derived) parameter. Defaults to ``qph`` if no radio is
        checked yet (during construction)."""
        if self.qph_lock_current.isChecked():
            return self.QPH_LOCK_CURRENT
        if self.qph_lock_width.isChecked():
            return self.QPH_LOCK_WIDTH
        return self.QPH_LOCK_QPH

    def _on_qph_lock_changed(self, *_):
        """A radio-toggle flipped — restyle the spinboxes so the locked
        one shows greyed-out + italic and is read-only, recompute the
        new locked value from the other two, and propagate the result
        into the pattern panel.
        """
        target = self._qph_lock_target()
        # Apply read-only styling: only the locked spinbox is
        # auto-computed; the other two remain editable.
        self.pattern_panel._set_lock_state(self.qph_current,
                                           target == self.QPH_LOCK_CURRENT)
        self.pattern_panel._set_lock_state(self.qph_width,
                                           target == self.QPH_LOCK_WIDTH)
        self.pattern_panel._set_lock_state(self.qph_qph,
                                           target == self.QPH_LOCK_QPH)
        # Force a recompute so the locked value is consistent with
        # the (now-fixed) other two.
        self._recompute_locked_qph()

    def _recompute_locked_qph(self):
        """Compute the auto-derived parameter from the other two and
        apply it to its spinbox. Reentrancy-guarded so the resulting
        valueChanged signal doesn't ring back into the input handlers."""
        target = self._qph_lock_target()
        I = float(self.qph_current.value())
        W = float(self.qph_width.value())
        Q = float(self.qph_qph.value())
        self._qph_in_progress = True
        try:
            if target == self.QPH_LOCK_CURRENT:
                # I = Q · 1000 / W
                if W > 0:
                    self.qph_current.setValue(max(0.0, Q * 1000.0 / W))
            elif target == self.QPH_LOCK_WIDTH:
                # W = Q · 1000 / I
                if I > 0:
                    self.qph_width.setValue(max(1.0, Q * 1000.0 / I))
            else:
                # Q = I · W / 1000
                self.qph_qph.setValue(I * W / 1000.0)
        finally:
            self._qph_in_progress = False
        self._push_qph_to_pattern()

    def _on_qph_input_changed(self, *_):
        """One of the 3-way spinboxes changed (user-typed, since the
        locked one is read-only). Recompute the locked parameter from
        the new triple and propagate to the pulse pattern."""
        if self._qph_in_progress:
            return
        self._recompute_locked_qph()

    def _push_qph_to_pattern(self):
        """Mirror the (current, width) pair into the pattern panel's
        amp_excite + width_shared inputs so the rendered pulse
        reflects the lock-UI's current values. Sign of amp_excite is
        preserved (the panel encodes polarity via sign).

        Gated on Fixed-Q_ph mode: outside that mode the lock UI is
        hidden, its values are stale, and overwriting the pattern
        panel would destroy the user's direct edits — most visibly
        prefs restore in Max mode used to instantly revert
        width_shared / amp_excite to whatever the Fixed-Q_ph lock
        radio was last computing.
        """
        try:
            if self.mode_combo.currentText() != self.MODE_FIXED_QPH:
                return
        except Exception:
            return
        I = float(self.qph_current.value())
        W = float(self.qph_width.value())
        # Preserve the panel's polarity sign — the lock UI only
        # exposes magnitude, so we flip back to negative if the
        # pattern panel was already in a cathodic-first configuration.
        prev = float(self.pattern_panel.amp_excite.value())
        signed_I = -abs(I) if prev < 0 else abs(I)
        # Signal-block the panel's spinboxes so our writes don't
        # bounce back through ``_on_pattern_amp_or_width_changed``.
        self._qph_in_progress = True
        try:
            self.pattern_panel.amp_excite.setValue(signed_I)
            self.pattern_panel.width_shared.setValue(W)
        finally:
            self._qph_in_progress = False

    def _on_pattern_amp_or_width_changed(self, *_):
        """Pattern panel's amp / width was edited directly (e.g. user
        typed into the panel rather than into our 3-way lock UI).
        Mirror the new value back, then recompute whatever's locked.

        Only runs while the user is in **Fixed-Q_ph** strategy mode
        — the lock UI is hidden in every other mode, so propagating
        constraints back into the pattern panel would otherwise
        overwrite a fresh user edit (e.g. typing into width_shared
        in MAX mode would instantly be reverted by
        ``W = Q × 1000 / I`` if the lock radio happened to be on
        "Lock phase width" from a prior session).  Bug report:
        "I am unable to change the phase width in the test
        parameters for Voltage Transient, symmetric."
        """
        if self._qph_in_progress:
            return
        # Gate on the strategy mode — outside Fixed-Q_ph the lock UI
        # is irrelevant and the pattern panel is the source of truth.
        try:
            if self.mode_combo.currentText() != self.MODE_FIXED_QPH:
                return
        except Exception:
            # If mode_combo isn't accessible for some reason, fall
            # back to the conservative "do nothing" behaviour rather
            # than risk overwriting user input.
            return
        self._qph_in_progress = True
        try:
            self.qph_current.setValue(
                abs(float(self.pattern_panel.amp_excite.value())))
            self.qph_width.setValue(
                float(self.pattern_panel.width_shared.value()))
        finally:
            self._qph_in_progress = False
        # Now recompute whichever is locked (current/width changes
        # most naturally drive the Q_ph readout, but the user might
        # have lock=current — in which case typing width here would
        # drive current via the constraint).
        self._recompute_locked_qph()

    def _sync_qph_inputs_from_pattern(self):
        """Pull the current pulse's amp / width into the 3-way UI;
        Q_ph is computed from those. Used when the user enters
        MODE_FIXED_QPH so the lock UI starts from the live pulse."""
        self._qph_in_progress = True
        try:
            self.qph_current.setValue(
                abs(float(self.pattern_panel.amp_excite.value())))
            self.qph_width.setValue(
                float(self.pattern_panel.width_shared.value()))
            self.qph_qph.setValue(
                self.qph_current.value() * self.qph_width.value() / 1000.0)
        finally:
            self._qph_in_progress = False

    def _on_strategy_changed(self, *_):
        mode = self.mode_combo.currentText()
        # Ramp visibility re-evaluation lives here too so the Ramp
        # checkbox can show/hide the same ramp_box.
        is_max = mode == self.MODE_MAX
        is_fixed_qph = mode == self.MODE_FIXED_QPH
        is_fixed_qd = mode == self.MODE_FIXED_QD
        is_fixed_any = mode in (self.MODE_FIXED_QPH, self.MODE_FIXED_QD)
        # Fixed-Q_ph mode is single-shot by construction — disable the
        # ramp box even if the checkbox somehow lingered checked.
        ramp_on = (is_fixed_any and not is_fixed_qph
                   and self.fixed_ramp_check.isChecked())
        self._ramp_box.setVisible(is_max or ramp_on)
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
                   "pause_between_channels_check",
                   "qinj_mc",
                   "qph_current", "qph_width", "qph_qph",
                   "start_ua", "coarse_ua", "fine_ua", "max_ua", "safety_factor")

    def current_prefs(self) -> dict:
        # Add the radio-button lock target since the base PREF_FIELDS
        # walker only handles spinboxes / combos / checkboxes.
        out = super().current_prefs()
        out["qph_lock_target"] = self._qph_lock_target()
        return out

    def restore_prefs(self, p: dict):
        super().restore_prefs(p)
        target = (p or {}).get("qph_lock_target", self.QPH_LOCK_QPH)
        if target == self.QPH_LOCK_CURRENT:
            self.qph_lock_current.setChecked(True)
        elif target == self.QPH_LOCK_WIDTH:
            self.qph_lock_width.setChecked(True)
        else:
            self.qph_lock_qph.setChecked(True)
        self._on_qph_lock_changed()

    def experiment_type(self) -> str: return "VT"

    def start_clicked(self):
        if self._stim is None or self._scope is None:
            return
        shape = self.pattern_panel.pattern()
        excite = shape.excitation_phase.amplitude_ua
        mode = self.mode_combo.currentText()
        is_max = mode == self.MODE_MAX
        is_fixed_qd = mode == self.MODE_FIXED_QD
        is_fixed_qph = mode == self.MODE_FIXED_QPH
        # Maximum / ramped modes scale the panel pulse to start_ua.
        # Fixed-Q_ph uses the panel pulse as-is (the 3-way lock UI
        # has already pushed I, W, and the derived knob into the
        # pulse). Fixed-Q_inj derives I_stim from Q_inj × area /
        # phase width per electrode and pre-scales the pulse before
        # the runner sees it.
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
        elif is_fixed_qph:
            # The 3-way lock UI has already pushed (current, width)
            # into the pattern panel; the configured pulse already
            # carries the user-selected Q_ph target. Run as-is. The
            # log line records the auto-derived knob so the npz
            # inspection later shows which parameter was the
            # dependent variable.
            pattern = shape
            target = self._qph_lock_target()
            self.log_pane.log(
                f"Fixed Q_ph (lock={target}): "
                f"I={self.qph_current.value():.2f} µA, "
                f"W={self.qph_width.value():.1f} µs, "
                f"Q_ph={self.qph_qph.value():.3f} nC/ph.")
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
        # Pre-run damage screen — runs Shannon + NeurostimML + the
        # selected Environment posture against the planned worst-
        # case ramp ceiling. Returns False if the user clicks
        # Cancel on a ``warn``/``alert`` modal; we bail before
        # starting the runner so the stimulator never gets loaded
        # with parameters the user reconsidered.
        try:
            site = self._array.sites[0] if self._array.sites else None
            site_area_um2 = (site.surface_area_um2 if site else 5000.0)
        except Exception:
            site_area_um2 = 5000.0
        # The VT ramp ceiling is the worst-case amplitude; pull it
        # from the ramp policy (built next) by reading the same
        # field the runner does. Building the policy here is cheap
        # and the policy is recomputed identically below for the
        # actual runner call.
        _peek_ramp = self._build_ramp_policy(pattern.excitation_phase.amplitude_ua)
        _max_ua = getattr(_peek_ramp, "max_ua", None)
        if not self._pre_run_warning_check(
                pattern, site_area_um2, max_amplitude_ua=_max_ua):
            self.log_pane.log("Run cancelled at pre-run damage screen.")
            return
        test = TestParameters(experiment="TV" if pattern.is_triphasic else "VT",
                              pattern=pattern, configuration=config,
                              array=self._array)
        session = Session(notebook="vt_session", subject=config.display_name(), test=test)
        self._stamp_extras(session)

        ramp = self._build_ramp_policy(pattern.excitation_phase.amplitude_ua)
        # Predictive strategy → try to load a trained ML model. The new
        # ``load_default()`` classmethod prefers the bundled pre-fit
        # pickle (cheap), falls back to fitting from the bundled CSV
        # (slower but works for dev checkouts), and returns ``None``
        # when neither source is available. ``None`` propagates to the
        # runner where it triggers the adaptive-regression fallback —
        # so a missing model never blocks a Start.
        #
        # The previous ``QinjPredictor().fit_from_csv()`` call used a
        # process-CWD-relative path and silently failed in frozen
        # builds; ``load_default()`` is frozen-build-aware (resolves
        # ``sys._MEIPASS / data`` for installed builds and the
        # in-repo ``data/`` folder for dev runs).
        predictor = None
        if (self.mode_combo.currentText() == self.MODE_MAX
                and self.strategy_combo.currentText() == self.STRAT_PRED):
            try:
                from ..ml import QinjPredictor
                predictor = QinjPredictor.load_default()
                if predictor is None:
                    self.log_pane.log(
                        "Predictive: no trained model or dataset "
                        "available; the runner will fall back to "
                        "adaptive regression.")
            except Exception as e:
                self.log_pane.log(
                    f"Predictive: model load failed ({e}); the "
                    f"runner will fall back to adaptive regression.")
                predictor = None
        runner = VoltageTransientExperiment(
            session, self._stim, self._scope,
            configurations=configs, ramp=ramp, predictor=predictor,
            cathodic_limit_v=self._cathodic_limit_v,
            anodic_limit_v=self._anodic_limit_v,
            polarization_tolerance_v=self._polarization_tolerance_v,
        )
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        # Push the between-channels rewire-pause flag onto the runner.
        # Only meaningful when more than one configuration was selected;
        # the runner's wait_for_continue short-circuits if the flag is off.
        runner.pause_between_channels = (
            self.pause_between_channels_check.isChecked())
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
        is_fixed_qph = mode == self.MODE_FIXED_QPH
        is_fixed_any = mode in (self.MODE_FIXED_QPH, self.MODE_FIXED_QD)
        # MODE_FIXED_QPH is single-shot by definition — a ramp would
        # vary Q_ph or amp away from the user's fixed target, which is
        # nonsense in this mode. Force the no-ramp branch even if the
        # ramp checkbox happens to be ticked from a previous mode.
        ramp_on = (self.fixed_ramp_check.isChecked() and not is_fixed_qph)
        if is_fixed_any and not ramp_on:
            amp = abs(panel_amp_ua) if panel_amp_ua != 0 else 5.0
            return RampPolicy(starting_ua=amp,
                              coarse_step_ua=max(amp, 1.0),
                              fine_step_ua=max(amp, 1.0),
                              max_ua=amp)
        if is_fixed_any and ramp_on:
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
    # The historical "None" sentinel was removed and replaced by an
    # explicit checkbox (``self.do_char``) per the user spec: gating
    # pre/post characterization should be one toggle, with the
    # remaining options matching the VT mode dropdown (Fixed current
    # / Fixed charge density / Maximum charge-injection). Kept as a
    # class attribute for back-compat with prefs files that stored
    # "None" — the loader maps it to ``do_char=False``.
    CHAR_NONE = "None"
    CHAR_FIXED_AMP = "Fixed current"
    CHAR_FIXED_QPH = "Fixed charge per phase"
    CHAR_FIXED_QD = "Fixed charge density"
    CHAR_MAX = "Maximum charge-injection"
    # ``Fixed charge per phase`` is the absolute Q_ph (nC) target —
    # the runner picks the amplitude that delivers that charge given
    # the current phase width (I = Q_ph_nC × 1000 / W_µs). Distinct
    # from ``Fixed charge density`` (Q_inj in mC/cm²) which divides
    # by electrode area; offering both lets the operator pick the
    # framing that matches the experimental protocol they're
    # replicating from prior work.
    CHAR_MODES = (CHAR_FIXED_AMP, CHAR_FIXED_QPH, CHAR_FIXED_QD, CHAR_MAX)

    def __init__(self, array, parent=None):
        super().__init__(array, parent)
        # Duration + unit toggle (seconds / pulses, integer in pulses).
        # ``_duration_prev_unit`` caches the previous selection so
        # ``_on_duration_unit`` can convert the value via the live
        # pulse rate (e.g. "60 s" at 50 Hz → "3000 pulses" when the
        # user flips the dropdown to pulses).
        self.duration = RepeatingDoubleSpinBox()
        self.duration.setRange(1, 1e9); self.duration.setValue(60)
        self.duration.setDecimals(1); self.duration.setSuffix(" s")
        self.duration.setToolTip(
            "Total pulsing duration for the burst. Unit picker on "
            "the right converts between seconds (wall-clock) and "
            "pulses (count) using the live pulse rate. Captures "
            "happen at the capture-interval cadence the runner "
            "computes from the configured duration.")
        self.duration_unit = QtWidgets.QComboBox()
        self.duration_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.duration_unit.currentTextChanged.connect(self._on_duration_unit)
        self.duration_unit.setToolTip(
            "Unit for the duration spinbox. Switching converts the "
            "current value through the live pulse rate (e.g. 60 s "
            "at 50 pps ↔ 3000 pulses).")
        self._duration_prev_unit = self.UNIT_S

        # Pre/post characterization toggle + mode dropdown.
        # The toggle (``do_char``) gates the whole feature; when off,
        # the mode dropdown and target Q_inj are disabled. When on,
        # the dropdown picks among the three VT-style mode entries
        # (matching the Voltage Transient experiment's "Mode" combo
        # so the user reads the same options across both tabs).
        self.do_char = QtWidgets.QCheckBox("Pre/post characterization")
        self.do_char.setToolTip(
            "Run a short Voltage Transient–style characterization "
            "BEFORE the pulsing burst (pre-char) and AFTER it (post-"
            "char). Use the dropdown to pick which characterization "
            "the runner performs. With this OFF, only the pulsing "
            "burst runs.")
        self.do_char.setChecked(False)
        self.char_mode = QtWidgets.QComboBox()
        self.char_mode.addItems(list(self.CHAR_MODES))
        self.char_mode.setCurrentText(self.CHAR_FIXED_AMP)
        self.char_mode.setEnabled(False)
        self.do_char.toggled.connect(self.char_mode.setEnabled)
        self.do_char.toggled.connect(self._on_char_mode_changed)
        self.char_mode.currentTextChanged.connect(self._on_char_mode_changed)
        self.char_mode.setToolTip(
            "Which Voltage-Transient-style characterization the "
            "pre/post runs use.<br><br>"
            "<b>Fixed current</b> — single train at the pulse "
            "panel's current amplitude.<br>"
            "<b>Fixed charge per phase</b> — pick current to hit "
            "the Q_ph target (nC) at the configured phase "
            "width.<br>"
            "<b>Fixed charge density</b> — pick current to hit "
            "the Q_inj target (mC/cm²) for the active site's "
            "area.<br>"
            "<b>Maximum charge-injection</b> — ramp upward to "
            "find the safe-stim ceiling before / after the burst.")
        self.qinj_mc = RepeatingDoubleSpinBox()
        self.qinj_mc.setRange(0.001, 100.0); self.qinj_mc.setDecimals(3)
        self.qinj_mc.setSingleStep(0.05); self.qinj_mc.setValue(0.5)
        self.qinj_mc.setSuffix(" mC/cm²")
        self.qinj_mc.setToolTip(
            "Target charge density for the Fixed-Q_inj "
            "characterization mode. Active electrode site area "
            "(set on the Setup tab) determines the corresponding "
            "current.")
        # Q_ph target spinbox — the value the runner aims for when
        # the user picks ``CHAR_FIXED_QPH``. nC is the natural unit
        # for per-phase charge (Q_ph = amplitude × phase width); a
        # 100 µA × 200 µs biphasic delivers 20 nC per phase, which
        # sits in the middle of this 0.001–100 range. The range cap
        # of 100 nC corresponds to the PlexStim's 1 mA × 100 µs
        # ceiling — enough headroom for long-pulse protocols without
        # letting the user type something physically impossible to
        # deliver.
        self.qph_nc = RepeatingDoubleSpinBox()
        self.qph_nc.setRange(0.001, 100.0); self.qph_nc.setDecimals(3)
        self.qph_nc.setSingleStep(0.5); self.qph_nc.setValue(20.0)
        self.qph_nc.setSuffix(" nC")
        self.qph_nc.setToolTip(
            "Target charge per phase (nC) for the Fixed-Q_ph "
            "characterization mode. The runner picks the current "
            "that delivers this Q_ph at the configured phase "
            "width (I_µA = Q_ph_nC × 1000 / W_µs). Range top is "
            "100 nC — the PlexStim 2.0's 1 mA × 100 µs ceiling.")

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
        dur_row.setContentsMargins(0, 0, 0, 0)
        dur_row.setSpacing(4)
        dur_row.addWidget(self.duration, stretch=2)
        dur_row.addWidget(self.duration_unit, stretch=1)
        dur_w = QtWidgets.QWidget(); dur_w.setLayout(dur_row)
        ext.addRow("Duration:", dur_w)
        # Audit finding #10 — the SP runner doesn't yet wire up pre/post
        # characterization. The widgets (``do_char``, ``char_mode``,
        # ``qinj_mc``, ``qph_nc``) stay constructed so their values
        # round-trip through prefs and ``start_clicked`` can read them
        # without AttributeError, but we don't add their rows to the
        # form — the user can't toggle a feature the runner ignores.
        # To re-expose once the runner-side support lands: add three
        # lines back here (do_char row + qinj_mc row + qph_nc row),
        # and re-include the ``Pre/post characterization: …`` log
        # line in ``start_clicked``.
        # Original rows preserved as comments for the diff:
        #   ext.addRow(self.do_char, self.char_mode)
        #   ext.addRow(rich.field_label("Target charge density",
        #                               rich.Q_INJ, "mC/cm²"),
        #              self.qinj_mc)
        #   ext.addRow(rich.field_label("Target charge per phase",
        #                               "Q_ph", "nC"),
        #              self.qph_nc)
        ext.addRow("", self.acquire_btn)

        params_box = QtWidgets.QGroupBox("Short-Term Pulsing parameters")
        v = QtWidgets.QVBoxLayout(params_box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addWidget(self.pattern_panel)
        v.addWidget(self.pattern_preview)
        v.addLayout(ext)

        self._assemble_pages(params_box)
        # (Previously the experiment-page inner sub-tab was hidden
        # here until the user had captured something. With the
        # Parameters → top-level tab refactor the experiment widget
        # IS the experiment page; we no longer hide it pre-acquire.)

    def _refresh_preview(self, *_):
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    SINGLE_CONFIG = True
    PREF_FIELDS = ("duration", "duration_unit",
                   "do_char", "char_mode", "qinj_mc", "qph_nc")

    def experiment_type(self): return "SP"

    def restore_prefs(self, p: dict):
        # Auto-migrate from the old single-combo schema where
        # ``char_mode == "None"`` encoded "no characterization". The
        # new schema splits that into a ``do_char`` checkbox + the
        # three mode entries. When loading an old prefs payload:
        #   * char_mode == "None"  → do_char=False, leave mode at
        #     its default (Fixed current — first entry).
        #   * char_mode in CHAR_MODES → do_char=True, mode preserved.
        # Old prefs files without ``do_char`` infer it from the saved
        # ``char_mode`` value to keep the round-trip stable.
        if isinstance(p, dict) and "do_char" not in p:
            old_cm = p.get("char_mode")
            if old_cm == self.CHAR_NONE:
                p = {**p, "do_char": False, "char_mode": self.CHAR_FIXED_AMP}
            elif old_cm in self.CHAR_MODES:
                p = {**p, "do_char": True}
        # Pre-sync ``_duration_prev_unit`` so the duration_unit
        # dropdown's setCurrentText (fired by the base-class field
        # walk) doesn't trigger an unwanted seconds↔pulses value
        # conversion — the saved value is already expressed in the
        # saved unit. See ``LongPulsingTab.restore_prefs`` for the
        # same trick.
        if isinstance(p, dict):
            saved_unit = p.get("duration_unit")
            if saved_unit in (self.UNIT_S, self.UNIT_P):
                self._duration_prev_unit = saved_unit
        super().restore_prefs(p)
        # Re-apply the suffix after restore so old prefs files that
        # never wrote a duration_unit still display the right unit.
        self._apply_unit_suffix(self.duration,
                                self.duration_unit.currentText())

    def _apply_unit_suffix(self, spin: QtWidgets.QDoubleSpinBox, unit: str):
        """Update suffix + decimal precision for ``unit``. Mirror of
        the LP helper — extracted so the slot below stays a thin
        glue between dropdown event and conversion+suffix work."""
        if unit == self.UNIT_P:
            spin.setSuffix(" pulses")
            spin.setDecimals(0); spin.setSingleStep(1)
        else:
            spin.setSuffix(" s")
            spin.setDecimals(1); spin.setSingleStep(1)

    def _convert_spinbox_value(self, spin: QtWidgets.QDoubleSpinBox,
                               from_unit: str, to_unit: str):
        """Convert ``spin``'s value using the live pulse rate. Same
        contract as :meth:`LongPulsingTab._convert_spinbox_value`."""
        if from_unit == to_unit:
            return
        rate_hz = max(self.pattern_panel.pattern().rate_hz, 1e-6)
        cur = float(spin.value())
        if from_unit == self.UNIT_S and to_unit == self.UNIT_P:
            new_val = round(cur * rate_hz)
        elif from_unit == self.UNIT_P and to_unit == self.UNIT_S:
            new_val = cur / rate_hz
        else:
            return
        new_val = max(spin.minimum(), min(spin.maximum(), new_val))
        spin.blockSignals(True)
        try:
            spin.setValue(new_val)
        finally:
            spin.blockSignals(False)

    def _on_duration_unit(self, *_):
        new_unit = self.duration_unit.currentText()
        old_unit = getattr(self, "_duration_prev_unit", self.UNIT_S)
        if old_unit != new_unit:
            self._convert_spinbox_value(self.duration, old_unit, new_unit)
        self._apply_unit_suffix(self.duration, new_unit)
        self._duration_prev_unit = new_unit

    def _on_char_mode_changed(self, *_):
        # Each "target" row is only meaningful for the mode it
        # parameterises, AND only when the pre/post-characterization
        # toggle is on. The Q_inj row shows for CHAR_FIXED_QD; the
        # Q_ph row shows for CHAR_FIXED_QPH; CHAR_FIXED_AMP and
        # CHAR_MAX have no extra target field.
        is_on = self.do_char.isChecked()
        cur = self.char_mode.currentText()
        is_qd = is_on and (cur == self.CHAR_FIXED_QD)
        is_qph = is_on and (cur == self.CHAR_FIXED_QPH)
        for spin, visible in ((self.qinj_mc, is_qd),
                              (self.qph_nc, is_qph)):
            spin.setVisible(visible)
            layout = (spin.parentWidget().layout()
                      if spin.parentWidget() else None)
            if isinstance(layout, QtWidgets.QFormLayout):
                lab = layout.labelForField(spin)
                if lab is not None:
                    lab.setVisible(visible)

    def characterization_mode(self) -> str:
        """Effective characterization mode reflecting the toggle state.
        ``CHAR_NONE`` when the user has disabled pre/post char, else
        the dropdown's current text. Lets the runner / start path
        treat the toggle + mode as one logical "what to run" value
        without having to know the GUI layout."""
        if not self.do_char.isChecked():
            return self.CHAR_NONE
        return self.char_mode.currentText()

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
            pat = self.pattern_panel.pattern()
            if pat and pat.phases:
                phase1_us = pat.phases[0].width_us
                interphase_us = pat.phases[0].delay_after_us
                self._scope.auto_layout_for_pulse(
                    phase1_us=phase1_us,
                    interphase_us=interphase_us,
                    phase2_us=pat.phases[1].width_us if len(pat.phases) > 1 else 0.0,
                    discharge_us=pat.phases[1].delay_after_us if len(pat.phases) > 1 else 0.0,
                    ext_trigger=(self._trigger_source == "EXT"),
                )
                vmon_ch = self._aliases.get("vmon", "CH1")
                self._scope.set_cursors(phase1_us, interphase_us,
                                       source_channel=vmon_ch)
            else:
                self._scope.set_horizontal_position(30.0)
            from ..session import Capture
            from ..waveforms import PulsePattern
            acq = self._scope.single_capture()
            cap = Capture(index=0, pattern=self.pattern_panel.pattern())
            cap.time_us = acq.time_us
            cap.v_mon_v = getattr(acq, "v_mon_v", cap.v_mon_v)
            # Audit finding #24: previously dropped, so the
            # multichan scope's _refresh_traces silently skipped
            # the I_mon trace on single-capture acquisitions.
            # Mirror the V_mon / E_act / E_ret copy pattern.
            cap.i_mon_ua = getattr(acq, "i_mon_ua", cap.i_mon_ua)
            cap.e_act_v = getattr(acq, "e_act_v", None)
            cap.e_ret_v = getattr(acq, "e_ret_v", None)
            ch = -1
            actives = self.channel_grid.actives()
            if actives: ch = actives[0]
            self.multichan_scope.add_capture(cap, ch if ch > 0 else -1)
            self.metrics_side.show_capture(cap)
            self.log_pane.log("Single capture acquired.")
            # (No inner sub-tabs to flip any more — see the refactor
            # note next to ``self.experiment_page`` in
            # ``ExperimentTab.__init__``.)
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
        # (The "reveal the live waveform sub-tab" step is gone with
        # the inner-tabs refactor.) Queue every selected config; the
        # base class chains them via
        # ``_on_finished`` -> ``_start_next_pending``. The combination
        # panel restricts multi-config selection to single-active modes
        # (MP / CG / PCG); multipolar modes return a single combo.
        self._pending_configs = list(configs)
        if len(self._pending_configs) > 1:
            self.log_pane.log(
                f"Short-Term Pulsing: queued {len(self._pending_configs)} "
                f"configuration(s) — running sequentially.")
        cm = self.characterization_mode()
        if cm != self.CHAR_NONE:
            # Suffix shows the target value for the modes that
            # take one — Q_inj for fixed charge density, Q_ph for
            # fixed charge per phase. CHAR_FIXED_AMP and CHAR_MAX
            # don't carry an extra parameter.
            if cm == self.CHAR_FIXED_QD:
                target = (f" (target Q_inj = "
                          f"{self.qinj_mc.value():.3f} mC/cm²)")
            elif cm == self.CHAR_FIXED_QPH:
                target = (f" (target Q_ph = "
                          f"{self.qph_nc.value():.3f} nC)")
            else:
                target = ""
            self.log_pane.log(
                f"Pre/post characterization: {cm}{target}"
                f" — runner-side support is a follow-up.")
        self._start_next_pending()

    def _start_next_pending(self):
        if not self._pending_configs:
            return
        config = self._pending_configs.pop(0)
        pattern = self.pattern_panel.pattern()
        amplitude_ua = abs(pattern.excitation_phase.amplitude_ua)
        duration_s = self._to_seconds(self.duration.value(),
                                      self.duration_unit.currentText())
        # Pre-run damage screen — Short-Term Pulsing has no ramp,
        # so the worst-case amplitude IS the configured amplitude.
        try:
            site = self._array.sites[0] if self._array.sites else None
            site_area_um2 = (site.surface_area_um2 if site else 5000.0)
        except Exception:
            site_area_um2 = 5000.0
        if not self._pre_run_warning_check(
                pattern, site_area_um2,
                max_amplitude_ua=amplitude_ua):
            self.log_pane.log("Run cancelled at pre-run damage screen.")
            return
        test = TestParameters(experiment="SP", pattern=pattern, configuration=config,
                              array=self._array, duration_s=duration_s)
        session = Session(notebook="sp_session", subject=config.display_name(), test=test)
        self._stamp_extras(session)
        runner = ShortPulsingExperiment(
            session, self._stim, self._scope, amplitude_ua=amplitude_ua,
            policy=ShortPulsingPolicy(
                capture_interval_s=max(duration_s * 2, 60.0),
                duration_s=duration_s),
        )
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
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
        # Tracking sub-tab — instantiated BEFORE _assemble_pages so the
        # _extra_experiment_tabs hook below can hand it to the base
        # layout code. The base class wraps the multichan scope and
        # this tracking widget in a QTabWidget labelled "Voltage
        # Transient" / "Tracking" per the user spec.
        from .tracking_plot import TrackingPlot
        self.tracking_plot = TrackingPlot()

        # Duration spinbox + unit selector. Pulses-mode forces integer
        # values via the unit-toggle slot; seconds-mode keeps 1 decimal.
        # ``setSuffix(" s")`` displays the unit upfront — without it the
        # spinbox showed a bare "3600" on launch and the user had to
        # touch the unit dropdown to see what units it meant.
        self.duration = RepeatingDoubleSpinBox()
        self.duration.setRange(1, 1e9); self.duration.setValue(3600)
        self.duration.setDecimals(1); self.duration.setSuffix(" s")
        self.duration.setToolTip(
            "Total pulsing duration. Long-Term Pulsing typically "
            "runs for hours; defaults to 3600 s. Captures happen "
            "at the snapshot interval below; pauses happen at the "
            "configured pause interval if enabled.")
        self.duration_unit = QtWidgets.QComboBox()
        self.duration_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.duration_unit.currentTextChanged.connect(self._on_duration_unit)
        self.duration_unit.setToolTip(
            "Unit for the total duration. Switching converts the "
            "current value through the live pulse rate (e.g. "
            "1 hour at 50 pps ↔ 180000 pulses).")
        self._duration_prev_unit = self.UNIT_S

        # Periodic *snapshot* — one passive scope grab every N
        # seconds/pulses during the burst. Distinct from a full VT,
        # which is the optional max-charge-injection step before pause.
        self.snap_int = RepeatingDoubleSpinBox()
        self.snap_int.setRange(1, 1e9); self.snap_int.setValue(60)
        self.snap_int.setDecimals(1); self.snap_int.setSuffix(" s")
        self.snap_int.setToolTip(
            "Snapshot interval — how often the runner pauses the "
            "burst long enough to take one averaged scope capture "
            "for drift-tracking. The trace lands in Tracking and "
            "the Voltage-Transient sub-tab; the pulse train resumes "
            "immediately after the single-shot capture.")
        self.snap_int_unit = QtWidgets.QComboBox()
        self.snap_int_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.snap_int_unit.currentTextChanged.connect(self._on_snap_unit)
        self.snap_int_unit.setToolTip(
            "Unit for the snapshot interval. Same convention as "
            "the total-duration unit picker.")
        self._snap_prev_unit = self.UNIT_S

        # External-measurement pause AFTER each snapshot. Optionally,
        # the runner can also do a max-charge-injection sweep right
        # before pausing — so the user gets a fresh max-Q_inj data
        # point at every pause point.
        self.do_pause = QtWidgets.QCheckBox("Pause after characterization")
        self.do_pause.setToolTip(
            "When on, the runner pauses stim for the configured "
            "pause duration after each snapshot. Useful for "
            "external probes (EIS / cyclic voltammetry / "
            "microscopy) that need stim off to take their own "
            "measurement.")
        self.do_max_before_pause = QtWidgets.QCheckBox(
            "Run maximum charge-injection before pausing")
        self.do_max_before_pause.setEnabled(False)
        self.do_max_before_pause.setToolTip(
            "When on (requires pause-after-characterization), the "
            "runner does a brief max-Q_inj ramp before each pause. "
            "Gives a fresh max-charge-injection data point at "
            "every pause point so you can plot drift in the safe-"
            "stim ceiling alongside drift in V_d / R_a / E_pol.")
        self.do_pause.toggled.connect(self.do_max_before_pause.setEnabled)
        self.pause_s = RepeatingDoubleSpinBox()
        self.pause_s.setRange(1, 1e9); self.pause_s.setValue(60)
        self.pause_s.setDecimals(1); self.pause_s.setSuffix(" s")
        self.pause_s.setToolTip(
            "Pause duration after each snapshot. Stim is off for "
            "this whole period; you can use the time for external "
            "measurements. After the pause, stim resumes at the "
            "configured amplitude and continues until the next "
            "snapshot interval.")
        self.pause_unit = QtWidgets.QComboBox()
        self.pause_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.pause_unit.currentTextChanged.connect(self._on_pause_unit)
        self.pause_unit.setToolTip(
            "Unit for the pause duration. "
            "(Pulses option converts via the rate even though no "
            "stim is delivered during the pause — useful for "
            "matching pause length to a fixed number of "
            "would-have-been pulses.)")
        self._pause_prev_unit = self.UNIT_S
        self.do_pause.toggled.connect(self.pause_s.setEnabled)
        self.do_pause.toggled.connect(self.pause_unit.setEnabled)
        self.pause_s.setEnabled(self.do_pause.isChecked())
        self.pause_unit.setEnabled(self.do_pause.isChecked())

        # Build the parameter form
        f = rich.make_form()
        # Total duration row
        dur_row = QtWidgets.QHBoxLayout()
        dur_row.setContentsMargins(0, 0, 0, 0)
        dur_row.setSpacing(4)
        dur_row.addWidget(self.duration, stretch=2)
        dur_row.addWidget(self.duration_unit, stretch=1)
        dur_w = QtWidgets.QWidget(); dur_w.setLayout(dur_row)
        f.addRow("Total duration:", dur_w)
        # Snapshot-every row
        sn_row = QtWidgets.QHBoxLayout()
        sn_row.setContentsMargins(0, 0, 0, 0)
        sn_row.setSpacing(4)
        sn_row.addWidget(self.snap_int, stretch=2)
        sn_row.addWidget(self.snap_int_unit, stretch=1)
        sn_w = QtWidgets.QWidget(); sn_w.setLayout(sn_row)
        f.addRow("Waveform snapshot every:", sn_w)
        # Audit finding #11 — the LP runner doesn't yet honour the
        # "pause after characterization" or "max-Q_inj before pause"
        # options. Widgets stay constructed so prefs round-trip and
        # ``start_clicked``'s ``self.do_pause.isChecked()`` guard
        # gracefully stays False, but we omit the form rows so the
        # user can't tick a control the runner ignores. To re-expose
        # once the runner-side support lands, restore the
        # ``pa_row`` block and the ``do_max_before_pause`` row:
        #
        #   pa_row = QtWidgets.QHBoxLayout()
        #   pa_row.setContentsMargins(0, 0, 0, 0)
        #   pa_row.setSpacing(4)
        #   pa_row.addWidget(self.do_pause, stretch=3)
        #   pa_row.addWidget(QtWidgets.QLabel("for"))
        #   pa_row.addWidget(self.pause_s, stretch=1)
        #   pa_row.addWidget(self.pause_unit)
        #   pa_w = QtWidgets.QWidget(); pa_w.setLayout(pa_row)
        #   f.addRow("", pa_w)
        #   f.addRow("", self.do_max_before_pause)

        box = QtWidgets.QGroupBox("Long-Term Pulsing parameters")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addWidget(self.pattern_panel)
        v.addWidget(self.pattern_preview)
        v.addLayout(f)

        self._assemble_pages(box)

    def _extra_experiment_tabs(self):
        # Per the user spec: LP shows two sub-tabs on the Experiment
        # page — "Voltage Transient" (the existing multichannel scope,
        # added by the base class) and "Tracking" (this widget).
        return (("Tracking", self.tracking_plot),)

    def _refresh_preview(self, *_):
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    SINGLE_CONFIG = True
    PREF_FIELDS = ("duration", "duration_unit",
                   "snap_int", "snap_int_unit",
                   "do_pause", "pause_s", "pause_unit",
                   "do_max_before_pause")

    def experiment_type(self): return "LP"

    # ----- unit conversion helpers -----
    def _apply_unit_suffix(self, spin: QtWidgets.QDoubleSpinBox, unit: str):
        """Update the spinbox's suffix and integer/decimal precision
        for ``unit``. Does NOT mutate the spinbox's value — value
        conversion goes through :meth:`_convert_spinbox_value` so the
        two concerns stay independent (the suffix update is reversible;
        the conversion is one-way and rate-dependent)."""
        if unit == self.UNIT_P:
            spin.setSuffix(" pulses")
            spin.setDecimals(0); spin.setSingleStep(1)
        else:
            spin.setSuffix(" s")
            spin.setDecimals(1); spin.setSingleStep(1)

    def _convert_spinbox_value(self, spin: QtWidgets.QDoubleSpinBox,
                               from_unit: str, to_unit: str):
        """Convert ``spin``'s current value from ``from_unit`` to
        ``to_unit`` using the live pulse rate. ``setValue`` is signal-
        suppressed so the conversion doesn't trigger downstream slots
        that might re-trip the unit machinery."""
        if from_unit == to_unit:
            return
        rate_hz = max(self.pattern_panel.pattern().rate_hz, 1e-6)
        cur = float(spin.value())
        if from_unit == self.UNIT_S and to_unit == self.UNIT_P:
            new_val = round(cur * rate_hz)
        elif from_unit == self.UNIT_P and to_unit == self.UNIT_S:
            new_val = cur / rate_hz
        else:
            return
        # Clamp to the spin's range so an extreme conversion doesn't
        # silently overflow ``setValue``'s built-in clamp.
        new_val = max(spin.minimum(), min(spin.maximum(), new_val))
        spin.blockSignals(True)
        try:
            spin.setValue(new_val)
        finally:
            spin.blockSignals(False)

    def _handle_unit_change(self, spin: QtWidgets.QDoubleSpinBox,
                            new_unit: str, prev_attr: str):
        """Apply a unit-dropdown change: convert the value to the new
        unit (using the live rate) AND update the suffix / decimals.
        Tracks the previous unit on ``self`` under ``prev_attr`` so
        the next call knows the direction to convert."""
        old_unit = getattr(self, prev_attr, self.UNIT_S)
        if old_unit != new_unit:
            self._convert_spinbox_value(spin, old_unit, new_unit)
        self._apply_unit_suffix(spin, new_unit)
        setattr(self, prev_attr, new_unit)

    def _on_duration_unit(self, *_):
        self._handle_unit_change(self.duration,
                                 self.duration_unit.currentText(),
                                 "_duration_prev_unit")

    def _on_snap_unit(self, *_):
        self._handle_unit_change(self.snap_int,
                                 self.snap_int_unit.currentText(),
                                 "_snap_prev_unit")

    def _on_pause_unit(self, *_):
        self._handle_unit_change(self.pause_s,
                                 self.pause_unit.currentText(),
                                 "_pause_prev_unit")

    def _to_seconds(self, value: float, unit: str) -> float:
        if unit == self.UNIT_P:
            rate = max(self.pattern_panel.pattern().rate_hz, 1e-6)
            return value / rate
        return value

    def restore_prefs(self, p: dict):
        """Restore saved values + units without triggering the unit-
        change conversion. Pre-sync ``_*_prev_unit`` to match the
        saved unit so when the base class's field walk sets the
        dropdown's text, the slot sees ``old_unit == new_unit`` and
        skips the implicit value conversion (the saved value is
        already in the saved unit)."""
        if isinstance(p, dict):
            if p.get("duration_unit") in (self.UNIT_S, self.UNIT_P):
                self._duration_prev_unit = p["duration_unit"]
            if p.get("snap_int_unit") in (self.UNIT_S, self.UNIT_P):
                self._snap_prev_unit = p["snap_int_unit"]
            if p.get("pause_unit") in (self.UNIT_S, self.UNIT_P):
                self._pause_prev_unit = p["pause_unit"]
        super().restore_prefs(p)
        # The dropdown signal already fired (no conversion thanks to
        # the prev_unit pre-sync above) — but if the saved unit was
        # never written, the suffix might still be the __init__
        # default. Re-apply the suffix to be safe.
        self._apply_unit_suffix(self.duration, self.duration_unit.currentText())
        self._apply_unit_suffix(self.snap_int, self.snap_int_unit.currentText())
        self._apply_unit_suffix(self.pause_s, self.pause_unit.currentText())

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
        # Pre-run damage screen — same posture-aware modal as VT/SP.
        # Long-Term Pulsing runs at the configured amplitude for
        # the whole sweep (no ramp), so the worst case IS that
        # amplitude.
        try:
            site = self._array.sites[0] if self._array.sites else None
            site_area_um2 = (site.surface_area_um2 if site else 5000.0)
        except Exception:
            site_area_um2 = 5000.0
        if not self._pre_run_warning_check(
                pattern, site_area_um2,
                max_amplitude_ua=amplitude_ua):
            self.log_pane.log("Run cancelled at pre-run damage screen.")
            return
        test = TestParameters(experiment="LP", pattern=pattern, configuration=config,
                              array=self._array, duration_s=duration_s)
        session = Session(notebook="lp_session", subject=config.display_name(), test=test)
        self._stamp_extras(session)
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
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        self._start_runner(runner, f"LP_{config.display_name().replace(' ','_')}.npz")


# ---------------------------------------------------------------------------
# Progressive Stress tab
# ---------------------------------------------------------------------------
class ProgressiveStressTab(_BaseExperimentTab):
    def __init__(self, array, parent=None):
        super().__init__(array, parent)
        # Tracking sub-tab — same role as LP's. See LongPulsingTab
        # for the rationale (Experiment page hosts a QTabWidget with
        # "Voltage Transient" + "Tracking" sub-tabs).
        from .tracking_plot import TrackingPlot
        self.tracking_plot = TrackingPlot()
        from ..config import (STIM_CURRENT_RESOLUTION_UA,
                               STIM_CURRENT_UI_STEP_UA, STIM_MAX_AMPLITUDE_UA)
        _step = STIM_CURRENT_RESOLUTION_UA   # hardware grid (range floor)
        _ui_step = STIM_CURRENT_UI_STEP_UA   # ergonomic 1 µA arrow / wheel step
        _max = STIM_MAX_AMPLITUDE_UA
        self.start_ua = RepeatingDoubleSpinBox()
        # Allow 0 µA — useful as a "lazy ramp from rest" starting point.
        self.start_ua.setRange(0.0, _max); self.start_ua.setSingleStep(_ui_step)
        self.start_ua.setDecimals(1); self.start_ua.setValue(0.0); self.start_ua.setSuffix(" µA")
        self.start_ua.setToolTip(
            "Initial step of the staircase. 0 µA gives a "
            "no-stim baseline period at the start; non-zero "
            "jumps straight into the first stim level.")
        self.step_ua = RepeatingDoubleSpinBox()
        self.step_ua.setRange(_step, _max); self.step_ua.setSingleStep(_ui_step)
        self.step_ua.setDecimals(1); self.step_ua.setValue(5.0); self.step_ua.setSuffix(" µA")
        self.step_ua.setToolTip(
            "Amplitude increment between consecutive staircase "
            "steps. Smaller = finer charge-density resolution at "
            "the cost of more steps; larger = faster ramp through "
            "the safe-stim envelope.")
        self.t_step = RepeatingDoubleSpinBox(); self.t_step.setRange(1, 3600); self.t_step.setValue(60); self.t_step.setSuffix(" s")
        self.t_step.setToolTip(
            "Wall-clock seconds the ramp dwells at each staircase "
            "level before stepping up. The runner takes one "
            "scope capture every <i>sampling period</i> seconds "
            "during the step, so total captures per step = "
            "floor(t_step / sampling_period).")
        # Default ceiling = PlexStim hardware limit (1 mA/channel). Users can
        # cap below that if they want to stop the ramp earlier.
        self.max_ua = RepeatingDoubleSpinBox(); self.max_ua.setRange(1, _max)
        self.max_ua.setSingleStep(_ui_step); self.max_ua.setDecimals(1)
        self.max_ua.setValue(_max); self.max_ua.setSuffix(" µA")
        self.max_ua.setToolTip(
            "Hard ceiling for the staircase. Default is the "
            "PlexStim 2.0 rail (1000 µA); cap below that to "
            "stop the ramp before it climbs into compliance / "
            "water-window territory.")
        # Wall-clock seconds between successive scope captures inside a
        # single staircase step. The runner snaps to ``floor(t_step /
        # sampling_period)`` frames per step.
        self.sampling_period = RepeatingDoubleSpinBox()
        self.sampling_period.setRange(0.1, 3600.0)
        self.sampling_period.setSingleStep(1.0)
        self.sampling_period.setDecimals(1)
        self.sampling_period.setValue(10.0)
        self.sampling_period.setSuffix(" s")
        self.sampling_period.setToolTip(
            "How often the runner grabs a scope capture during "
            "each staircase step. 10 s on a 60 s step gives "
            "6 captures per step — enough to see V_d drift "
            "during the dwell. Shorter = denser tracking; "
            "longer = fewer captures per step.")
        # Hardware-level stop: V_mon hits the ±12 V compliance rail and the
        # device stops actually delivering the programmed current. There's no
        # point ramping further past that point.
        self.stop_on_compliance = QtWidgets.QCheckBox(
            "Stop when V_mon hits voltage compliance (±12 V rail)")
        self.stop_on_compliance.setChecked(True)
        self.stop_on_compliance.setToolTip(
            "Halt the ramp when V_mon hits the PlexStim's ±12 V "
            "compliance rail. Past compliance, the device can't "
            "drive the programmed current anymore and ramping "
            "further just wastes time without producing usable "
            "data. Recommended ON for any new electrode.")

        # Stress-specific ramp parameters live below the pattern panel
        f = rich.make_form()
        # Same ``I_start`` abbreviation as the VT panel (see
        # rich.I_START) so a reader switching between tabs sees
        # the same variable symbol for the ramp-floor knob.
        f.addRow(rich.field_label("Starting current", rich.I_START, rich.UA),
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
        # Also re-render the staircase whenever the pulse pattern
        # changes — the top axis (pulse-count ticks) depends on
        # ``pattern.rate_hz`` and the right axis (Q/phase ticks)
        # depends on ``pattern.phases[0].width_us``. Without this
        # connection the auxiliary axes would freeze with stale
        # values whenever the user edits the pulse pattern.
        self.pattern_panel.patternChanged.connect(
            lambda *_: self._refresh_staircase())
        self._fig_tabs = QtWidgets.QTabWidget()
        self._fig_tabs.setDocumentMode(True)
        self._fig_tabs.addTab(self.pattern_preview, "Pulse pattern")
        self._fig_tabs.addTab(self.staircase, "Staircase")
        # Suppress wheel-scroll tab switching on the Pulse pattern /
        # Staircase figure switcher.
        from .widgets import disable_tabbar_wheel_scroll
        disable_tabbar_wheel_scroll(self._fig_tabs)

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

    def _extra_experiment_tabs(self):
        # Per the user spec: PS shows two sub-tabs on the Experiment
        # page — "Voltage Transient" (the existing multichannel scope)
        # and "Tracking" (this widget). Same pattern as LP.
        return (("Tracking", self.tracking_plot),)

    def _refresh_staircase(self, *_):
        # Pull the live pulse-pattern context so the staircase's
        # auxiliary axes (top = pulse count, right = Q/phase) can
        # render their labels from real values rather than guesses.
        try:
            pat = self.pattern_panel.pattern()
            rate_hz = float(pat.rate_hz)
            # Phase 1 width — the canonical "phase width" the
            # Charge/Phase axis is computed against. For triphasic
            # patterns where each phase has a different width, the
            # right axis ends up calibrated against phase 1 — the
            # excitation phase, the one whose magnitude maps onto
            # the staircase y-axis. That's the right reference for
            # "how much charge per pulse phase".
            phase_w_us = (float(pat.phases[0].width_us)
                          if pat.phases else 0.0)
        except Exception:
            rate_hz = 0.0
            phase_w_us = 0.0
        self.staircase.set_policy(
            start_ua=float(self.start_ua.value()),
            step_ua=float(self.step_ua.value()),
            t_step_s=float(self.t_step.value()),
            max_ua=float(self.max_ua.value()),
            rate_hz=rate_hz,
            phase_width_us=phase_w_us,
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
        # Pre-run damage screen — Progressive Stress is a ramping
        # protocol like VT, so the worst-case amplitude is the
        # configured stop_ua (the highest planned step). Pull it
        # off the form spinbox so the pre-run check evaluates the
        # ceiling rather than the starting step.
        try:
            site = self._array.sites[0] if self._array.sites else None
            site_area_um2 = (site.surface_area_um2 if site else 5000.0)
        except Exception:
            site_area_um2 = 5000.0
        try:
            # PS panel exposes the ramp ceiling as ``self.max_ua``
            # (mirrors VT). An earlier draft of this hook
            # referenced a non-existent ``self.stop_ua`` and was
            # papered over by the bare-Exception fallback below;
            # that left the pre-run damage screen evaluating the
            # starting amplitude instead of the ceiling, so a
            # ramp that climbs toward 1000 µA never tripped a
            # warning. Reading ``max_ua`` directly gives the
            # correct worst-case for the screen.
            max_ua = float(self.max_ua.value())
        except Exception:
            max_ua = float(pattern.excitation_phase.amplitude_ua)
        if not self._pre_run_warning_check(
                pattern, site_area_um2, max_amplitude_ua=max_ua):
            self.log_pane.log("Run cancelled at pre-run damage screen.")
            return
        test = TestParameters(experiment="PS", pattern=pattern, configuration=config,
                              array=self._array)
        session = Session(notebook="ps_session", subject=config.display_name(), test=test)
        self._stamp_extras(session)
        runner = ProgressiveStressExperiment(
            session, self._stim, self._scope,
            policy=StressPolicy(starting_ua=self.start_ua.value(),
                                step_ua=self.step_ua.value(),
                                t_step_s=self.t_step.value(),
                                max_ua=self.max_ua.value(),
                                sampling_period_s=self.sampling_period.value(),
                                stop_on_voltage_compliance=self.stop_on_compliance.isChecked()),
        )
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        self._start_runner(runner, f"PS_{config.display_name().replace(' ','_')}.npz")

    # Queue chaining + completion-marking are handled by the base
    # ``_on_finished``; PS just needs ``_start_next_pending`` to build
    # a runner for the next configuration.
