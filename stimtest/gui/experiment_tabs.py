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

import os
import re
import math
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
from ..hardware.base import Oscilloscope, Stimulator, fmt_elapsed
from ..persistence import save_session_npz, save_session_npz_incremental
from ..session import Session, TestParameters
from ..waveforms import PulsePattern
from . import rich


#: Regex for parsing ``[bias-step]`` log lines emitted by
#: :meth:`ExperimentRunner.bias_step_if_armed`.  Captures measured /
#: error / bias voltages + the trailing flags portion (saturation, V_mon
#: insanity, deadband note).  Module-level so test stubs that delegate
#: into :meth:`_BaseExperimentTab._on_bias_step_log` via the unbound
#: method find the same constant the production tab does — referencing
#: through ``self`` would resolve against the stub's empty class, raise
#: AttributeError, get swallowed by the slot's defensive try/except,
#: and silently no-op.  Module-level dodges that asymmetry.
_BIAS_STEP_LINE_RE = re.compile(
    r"\[bias-step\]\s*"
    r"measured=(?P<measured>[-+0-9.eE]+)\s*V,\s*"
    r"error=(?P<error>[-+0-9.eE]+)\s*mV,\s*"
    r"bias=(?P<bias>[-+0-9.eE]+)\s*V"
    r"(?P<flags>.*)$"
)
from ..feature_flags import interstellar_enabled
from .channel_selector import ChannelSelector
from .combination_panel import CombinationPanel
from .multichannel_scope import MultiChannelScope
from .pattern_panel import PatternControlPanel
from .pattern_preview import PatternPreview
from .repeating_spinbox import RepeatingDoubleSpinBox, RepeatingSpinBox
from .staircase_plot import StaircasePlot
from .widgets import LogPane, MetricTable, _group_thousands


def _apply_channel_bandwidths(scope, aliases):
    """Set per-channel scope bandwidth: FULL on data channels, band-limited
    (20 MHz) on the TRIGGER channel for a clean comparator.

    Why the split (CWRU bench, CLAUDE.md gotchas #6 + #161): the
    ``imon_trigger_level`` formula was tuned for the 20 MHz-band-limited I_mon
    peak.  With I_mon at FULL bandwidth on a 2-channel scope — where I_mon IS
    the trigger — the broadband peak stops clearing the threshold and the
    scope barely triggers, producing garbage captures.  Limiting ONLY the
    trigger channel restores triggering while every data-only channel keeps
    full BW (operator preference).  On a 4-channel scope the trigger is a
    separate digital-sync channel, so I_mon stays full-BW there.

    ``aliases`` maps role → channel (e.g. ``{'imon':'CH1','vmon':'CH2',
    'trigger':'CH1'}``); a channel serving two roles (I_mon that is also the
    trigger) is configured ONCE, as the trigger.  Returns a list of
    ``(alias, channel, applied_bw_mhz, kind)`` for logging (``kind`` is
    ``"trigger"`` or ``"data"``)."""
    trig_ch = aliases.get("trigger")
    imon_ch = aliases.get("imon")
    # When there is NO separate Trigger channel, the trigger source falls back
    # to the I_mon channel (``current_aliases``: trigger = explicit or imon).
    # That channel MUST be band-limited to 20 MHz — a full-BW I_mon barely
    # clears ``imon_trigger_level`` (tuned for the band-limited peak) → the
    # scope hardly triggers → garbage captures.  So when I_mon IS the trigger,
    # 20 MHz WINS over a per-channel "Full" override (operator: "if there is no
    # Trigger channel, set Imon as trigger source and 20 MHz").
    imon_is_trigger = (trig_ch is not None and trig_ch == imon_ch)
    overrides = getattr(scope, "_channel_bandwidth_override", None) or {}
    results = []
    seen: set = set()
    for alias, ch in aliases.items():
        if ch in seen:
            continue
        seen.add(ch)
        # Per-channel operator OVERRIDE (Setup tab bandwidth dropdown) wins
        # over the automatic trigger/data split — EXCEPT that a "Full" override
        # can't defeat the 20 MHz requirement on the I_mon-as-trigger channel.
        # Only "full"/"20mhz" are stored; "Auto" channels fall through.
        ov = overrides.get(str(ch).upper()) or overrides.get(ch)
        if imon_is_trigger and ch == trig_ch and ov == "full":
            # A "Full" override can't defeat the 20 MHz requirement on the
            # I_mon-as-trigger channel — full-BW I_mon barely clears the
            # trigger level → garbage captures.  Force 20 MHz.
            bw = scope.set_channel_bandwidth_for_purpose(ch, "trigger")
            kind = "trigger-imon-forced20"
        elif ov == "20mhz":
            bw = scope.set_channel_bandwidth_for_purpose(ch, "manual")
            kind = "manual-20MHz"
        elif ov == "full":
            bw = scope.set_channel_bandwidth_full(ch)
            kind = "manual-full"
        elif trig_ch is not None and ch == trig_ch:
            bw = scope.set_channel_bandwidth_for_purpose(ch, "trigger")
            kind = "trigger"
        else:
            bw = scope.set_channel_bandwidth_full(ch)
            kind = "data"
        if bw is not None:
            results.append((alias, ch, bw, kind))
    return results


# ---------------------------------------------------------------------------
# Background export worker: TIF renders + XLSX refreshes OFF the runner
# ---------------------------------------------------------------------------
class _ExportWorker(QtCore.QThread):
    """Dedicated export thread — TIF figure renders and XLSX workbook
    writes run IN PARALLEL with the experiment (operator: "Is it
    possible to do parallel processing of quickly saving TIF files and
    writing to XLSX during the experiment?").

    The runner thread only ENQUEUES work (microseconds) and moves on;
    this thread does the matplotlib Agg render / openpyxl write.  One
    serial consumer keeps matplotlib single-threaded (Agg figures must
    not be shared across threads) and prevents two writers racing the
    same .xlsx.

    * ``submit_plot``  — one task per ChannelRun figure.  Successful
      writes are recorded in :pyattr:`exported_run_ids` so the
      end-of-run back-fill only re-renders genuinely-missed runs.
    * ``submit_xlsx``  — COALESCED per path: a burst of capture events
      collapses to a single rewrite of the freshest session state
      (the queue can't grow unboundedly on a fast snapshot cadence).
      The workbook is written to a ``.part`` side-file then atomically
      ``os.replace``d so a crash mid-write never leaves a truncated
      .xlsx next to the good .npz.
    * ``finish()``     — sentinel; the thread drains the queue and
      exits.  Callers ``wait()`` afterwards so end-of-run log summaries
      reflect completed writes.
    """

    log_msg = QtCore.pyqtSignal(str)

    def __init__(self):
        super().__init__()
        import queue as _queue
        self._q: "_queue.Queue" = _queue.Queue()
        self._xlsx_pending: dict = {}
        self._lock = threading.Lock()
        #: ids of ChannelRuns whose figure was successfully written.
        self.exported_run_ids: set = set()

    # ---- producers (any thread) --------------------------------------
    def submit_plot(self, session, run, out_dir, fmt: str,
                    dpi: int = 600) -> None:
        self._q.put(("plot", session, run, Path(out_dir), str(fmt),
                     int(dpi)))

    def submit_xlsx(self, session, path) -> None:
        with self._lock:
            # Coalesce: only the FRESHEST session state per path matters.
            self._xlsx_pending[str(path)] = (session, Path(path))
        self._q.put(("xlsx",))

    def finish(self) -> None:
        self._q.put(("eof",))

    # ---- consumer (this thread) ---------------------------------------
    def run(self):  # noqa: C901 — small dispatch loop
        import os as _os
        while True:
            task = self._q.get()
            kind = task[0]
            if kind == "eof":
                break
            try:
                if kind == "plot":
                    _, _session, _run, _out_dir, _fmt, _dpi = task
                    from ..plotting import export_run_plot
                    p = export_run_plot(_session, _run, _out_dir,
                                        fmt=_fmt, dpi=_dpi)
                    if p is not None:
                        self.exported_run_ids.add(id(_run))
                        self.log_msg.emit(
                            f"[export] saved channel plot: {p.name}")
                elif kind == "xlsx":
                    with self._lock:
                        items = list(self._xlsx_pending.values())
                        self._xlsx_pending.clear()
                    for _session, _path in items:
                        from ..persistence import save_session_xlsx
                        _part = _path.with_suffix(_path.suffix + ".part")
                        save_session_xlsx(_session, _part)
                        _os.replace(_part, _path)
                        # NO per-refresh log line — the mirror is rewritten
                        # (throttled) several times per run and each emit
                        # flooded the log pane (operator: "why are there
                        # multiple refreshed workbook statements?").  The
                        # workbook is written SILENTLY; RunnerWorker logs ONE
                        # "Workbook saved" line at end-of-run instead.
            except Exception as e:
                self.log_msg.emit(
                    f"[export] background export failed "
                    f"({type(e).__name__}: {e}) — the end-of-run pass "
                    f"will retry.")


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
    # (capture, key) — ``key`` is the configuration DISPLAY NAME computed
    # on the WORKER at emission time (or the legacy int channel as a
    # fallback).  Computing the key at EMISSION rather than at GUI
    # processing time makes the page routing correct by construction:
    # the old path re-read ``runner.current_configuration`` when the
    # queued slot finally ran, so a delayed event could be routed to
    # whatever config the runner had moved on to (suspected cause of
    # the operator's blank-CH01 page after a 16-channel sweep).
    captured = QtCore.pyqtSignal(object, object)
    # Free-form log line for the GUI's log pane
    log_msg = QtCore.pyqtSignal(str)
    # Emitted on a session-save failure so the tab can pop a modal
    # warning. Carries (path, error_message). Without an explicit
    # signal a save failure would only land in the log pane, where
    # a busy user could miss it and assume their data is on disk.
    save_failed = QtCore.pyqtSignal(str, str)
    # Emitted once when the experiment ends (carries the ExperimentResult)
    finished = QtCore.pyqtSignal(object)
    # Emitted each time a single configuration's run COMPLETES (on the
    # runner's ``run_end`` event), carrying that config's list key
    # (``str(display_name())``).  Lets the GUI tick the channel/combo's
    # entry ✓ LIVE as each one finishes, instead of waiting for the whole
    # multi-config sweep to end (operator: "I do not see check marks by
    # the channel/combo … to indicate that they are complete" — they
    # only appeared at the very end of a long VT sweep).
    run_completed = QtCore.pyqtSignal(object)
    # Emitted when the runner pauses between channels for a physical rewire.
    # Carries the human-readable message describing the next channel.
    paused = QtCore.pyqtSignal(str)
    # Emitted on progress events from the runner.  Carries a
    # ProgressInfo dataclass (step, total, label, started_at).
    # _BaseExperimentTab routes these to the status bar for a live
    # "VT 7/16 channels | elapsed 1:47 | ETA 3:24"-style indicator.
    progress = QtCore.pyqtSignal(object)

    def __init__(self, runner: ExperimentRunner,
                 save_path: Optional[Path] = None,
                 auto_export_xlsx: bool = False,
                 auto_save_plots: bool = False,
                 auto_save_plots_fmt: str = "tif",
                 auto_save_plots_dpi: int = 600,
                 email_notifications: bool = False,
                 user_email: str = "",
                 user_name: str = "",
                 session_subject: str = "",
                 sms_phone: str = "",
                 sms_carrier: str = ""):
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
        try:
            self.auto_save_plots_dpi = int(auto_save_plots_dpi) or 600
        except (TypeError, ValueError):
            self.auto_save_plots_dpi = 600
        # Email notification settings — mirrored from the MATLAB
        # ``sendEmail.m`` / ``sendError.m`` workflow. The actual send
        # only fires when ``email_notifications`` is True AND
        # ``user_email`` is set AND SMTP credentials are configured
        # via env vars or ``~/.stimtest/email_config.json``.
        self.email_notifications = bool(email_notifications)
        self.user_email = str(user_email or "")
        self.user_name = str(user_name or "")
        self.session_subject = str(session_subject or "")
        # Optional SMS recipient (email-to-SMS gateway).  Gated by the same
        # ``email_notifications`` opt-in; fires only when BOTH phone +
        # carrier are set AND SMTP creds are configured.
        self.sms_phone = str(sms_phone or "").strip()
        self.sms_carrier = str(sms_carrier or "").strip()
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
        # Throttle the in-run .xlsx refreshes.  The workbook is a DERIVED
        # convenience mirror — per-capture freshness is unnecessary (the .npz
        # incremental save is the crash-recovery artifact), and each refresh
        # rewrites the ENTIRE growing session.  Without a throttle a 16-channel
        # VT fired ~70 full rewrites (one per capture), each keeping pace with
        # the ~14 s/capture cadence so the per-path coalescing never collapsed
        # them — flooding the log with "refreshed workbook" and wasting I/O
        # (operator: "why is the workbook refreshed so many times?").  Now the
        # in-run refreshes are rate-limited to one per _XLSX_MIN_INTERVAL_S; the
        # FINAL end-of-run refresh always fires (force=True) so the on-disk
        # workbook is complete.
        self._last_xlsx_at: float = 0.0
        # Background export thread — TIF renders + coalesced XLSX
        # refreshes run in PARALLEL with the experiment; the runner
        # thread only enqueues (see _ExportWorker).  Started here so
        # it's alive for the first capture; finished + drained at the
        # end of run() (finally block).
        self._export = _ExportWorker()
        self._export.log_msg.connect(self.log_msg)
        self._export.start()
        # Wire the plain-Python event stream into our Qt signals
        runner.subscribe(self._on_event)

    def _on_event(self, ev: ExperimentEvent):
        # Called from the worker thread (the runner's thread) — emitting Qt
        # signals from here is safe because Qt automatically marshals them
        # across to the GUI thread via the default queued connection.
        if ev.kind == "capture" and ev.capture is not None:
            # Key by THIS event's configuration (known here, on the
            # worker, at emission time) — see the ``captured`` signal
            # comment.  Falls back to the int channel sentinel when the
            # event carries no run.
            if ev.run is not None:
                try:
                    key = str(ev.run.configuration.display_name())
                except Exception:
                    key = int(ev.run.configuration.active)
            else:
                key = -1
            self.captured.emit(ev.capture, key)
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
                        # Keep the .xlsx current too — but THROTTLED (the
                        # workbook is a derived mirror; per-capture rewrites of
                        # the whole session flooded the log + wasted I/O).  The
                        # per-capture .npz above is the crash-recovery artifact;
                        # the .xlsx just needs to be roughly current, and the
                        # final end-of-run write completes it.
                        self._maybe_refresh_xlsx(
                            self.runner.session, self.save_path)
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
        if ev.kind == "run_end" and ev.run is not None:
            # Tick this config's entry ✓ LIVE — keyed by THIS run's config
            # (resolved here on the worker, at emission time), matching the
            # ``captured`` key so the right row gets the mark.  Without this
            # the ✓ only landed at end-of-run (_on_finished), so a long
            # multi-config VT sweep showed no completion marks until the
            # very end (operator complaint).
            try:
                _done_key = str(ev.run.configuration.display_name())
            except Exception:
                try:
                    _done_key = int(ev.run.configuration.active)
                except Exception:
                    _done_key = None
            if _done_key is not None:
                self.run_completed.emit(_done_key)
            # REAL-TIME per-channel figure save (operator: "the plot saves
            # occur after the experiment.  I want it in real time").
            # Rendered SYNCHRONOUSLY here on the runner thread — NOT
            # enqueued to the background export thread.  Why the change:
            # the background QThread was being starved of GIL time during
            # the sweep (the per-capture rescale loop is numpy/CPU-heavy),
            # so its renders — and even its log lines — only flushed at the
            # very END of the run (the operator saw "Back-filling N channel
            # plot(s)…" and every plot timestamped at run end).  A render
            # is only ~1.8 s at 600 DPI and this ``run_end`` fires in the
            # gap BETWEEN channels, where the stim is already stopped (the
            # VT/PS stop→load→start lifecycle), so rendering here does NOT
            # block pulsing — it just delays the next channel by ~2 s,
            # which is the right trade for a guaranteed on-disk-now plot.
            # Successful writes still land in ``exported_run_ids`` so the
            # end-of-run pass only back-fills a genuinely-failed render.
            if self.auto_save_plots and self.save_path is not None:
                try:
                    from ..plotting import export_run_plot
                    _p = export_run_plot(
                        self.runner.session, ev.run,
                        Path(self.save_path).parent,
                        fmt=self.auto_save_plots_fmt,
                        dpi=self.auto_save_plots_dpi)
                    if _p is not None:
                        self._export.exported_run_ids.add(id(ev.run))
                        self.log_msg.emit(
                            f"[export] saved channel plot (real-time): "
                            f"{_p.name}")
                except Exception as e:
                    self.log_msg.emit(
                        f"Real-time plot save failed "
                        f"({type(e).__name__}: {e}) — the end-of-run "
                        f"export will retry this channel.")
            # Keep the on-disk workbook current as channels complete —
            # THROTTLED (shared rate-limit with the per-capture refresh above),
            # so even a fast multi-channel sweep refreshes at most once per
            # _XLSX_MIN_INTERVAL_S mid-run.  The final end-of-run write
            # (in _on_finished) always fires, so the completed workbook is
            # never stale.
            self._maybe_refresh_xlsx(self.runner.session, self.save_path)
        if ev.kind == "paused":
            # Surface the between-channels pause as its own signal so the
            # tab can pop a modal "rewire to next channel, then continue"
            # dialog and call runner.request_continue() on dismiss.
            self.paused.emit(ev.message or "Continue to next channel?")
        if ev.kind == "progress" and ev.progress is not None:
            # Forward step/total/label/started_at to the tab's
            # status-bar slot.  Object signal — Qt auto-marshals to
            # the GUI thread via QueuedConnection.
            self.progress.emit(ev.progress)
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
            #
            # Hardware-disconnect detection (Task #55): inspect the
            # exception against known disconnect fingerprints (or
            # check if the runner already raised the structured
            # form).  When classified, the error string is prefixed
            # with the canonical ``[DISCONNECT:scope]`` /
            # ``[DISCONNECT:stim]`` marker so the GUI's
            # ``_on_finished`` handler can show a tailored
            # reconnect dialog instead of the generic "Run aborted"
            # path.  The partial-save mechanism (Task #54) already
            # wrote whatever captures completed before the
            # disconnect to the .npz, so prior data is preserved
            # regardless of what the operator picks in the dialog.
            from ..experiments.errors import (
                HardwareDisconnectError, looks_like_disconnect,
            )
            import traceback as _tb
            tb_text = _tb.format_exc()
            if isinstance(e, HardwareDisconnectError):
                _device = e.device
                _where = e.where or "(unknown)"
                self.log_msg.emit(
                    f"⚠ HARDWARE DISCONNECT — {_device.upper()} "
                    f"went away during {_where}.  Underlying error: "
                    f"{type(e.original).__name__}: {e.original}")
                _error_str = (
                    f"[DISCONNECT:{_device}] "
                    f"{type(e.original).__name__}: {e.original}")
            else:
                _classified = looks_like_disconnect(e)
                if _classified is not None:
                    self.log_msg.emit(
                        f"⚠ HARDWARE DISCONNECT — {_classified.upper()} "
                        f"appears to have disconnected.  Underlying error: "
                        f"{type(e).__name__}: {e}")
                    _error_str = (
                        f"[DISCONNECT:{_classified}] "
                        f"{type(e).__name__}: {e}")
                else:
                    self.log_msg.emit(
                        f"Run aborted ({type(e).__name__}): {e}\n{tb_text}")
                    _error_str = f"{type(e).__name__}: {e}"
            from ..experiments.base import ExperimentResult
            result = ExperimentResult(
                session=self.runner.session,
                aborted=True,
                error=_error_str,
            )
            # Mirror the MATLAB ``sendError.m`` notification — only
            # if the user opted in, set an email, and SMTP creds are
            # configured.
            self._maybe_send_failure_email(
                error_message=str(e),
                elapsed_seconds=_time.time() - run_start)
            self._drain_exports()
            self.finished.emit(result)
            return
        # Report the completed run's elapsed time BEFORE the data is saved
        # (operator: "indicate the elapsed time of the completed experiment
        # run before the saving of data") — so the log reads
        # "Run complete — elapsed …" then "Saved session to …".
        self.log_msg.emit(
            f"Run complete — elapsed {fmt_elapsed(_time.time() - run_start)}")
        # Persist the session immediately so partial runs aren't lost.
        # If the save fails (disk full, permission error, bad path) we
        # MUST surface it loudly — without the popup the user sees
        # "Run finished" in the log and assumes their data is on disk
        # when it isn't. Marking the result as ``aborted`` with an
        # error string also propagates into ``_on_finished`` so the
        # UI flow doesn't silently treat a save failure as success.
        if self.save_path is not None:
            try:
                # Mark a QUIT/aborted run ``incomplete`` in the .npz meta so
                # POLARIS shows the "incomplete run" badge and no loader treats
                # a partial run as complete (operator: "when the experiment is
                # quit, make sure that the files reflect that").  The filename
                # also gets an ``_ABORTED`` suffix after the export drain below.
                save_session_npz(result.session, self.save_path,
                                 incomplete=bool(getattr(result, "aborted", False)))
                self.log_msg.emit(f"Saved session to {self.save_path}")
                # Optional: also write the Gamry-style .xlsx workbook
                # next to the .npz. Doesn't fail the run on error —
                # the canonical .npz is already on disk and the user
                # can always re-export from the Results tab.
                # Final .xlsx through the SAME background export thread
                # (serialized with any in-flight refresh; atomic
                # .part→replace write).  The drain below guarantees it
                # is on disk before the worker reports completion.
                if self.auto_export_xlsx:
                    try:
                        _xlsx_path = Path(self.save_path).with_suffix(".xlsx")
                        self._export.submit_xlsx(result.session, _xlsx_path)
                        # ONE end-of-run confirmation (the drain below lands it
                        # on disk before ``finished``); the mid-run refreshes
                        # are silent, so this is the only workbook log line.
                        self.log_msg.emit(f"Workbook saved: {_xlsx_path.name}")
                    except Exception as e:
                        self.log_msg.emit(
                            f"Auto-export to .xlsx failed: "
                            f"{type(e).__name__}: {e}. "
                            f"The .npz is still saved at {self.save_path}; "
                            f"use the Results tab's Export .xlsx button to "
                            f"retry manually.")
                # BACK-FILL pass only: each ChannelRun's figure was
                # already enqueued in real time by the ``run_end`` hook;
                # here we enqueue just the runs whose background write
                # didn't succeed (abort mid-channel, transient failure)
                # so the end-of-session work is normally ZERO files.
                if self.auto_save_plots:
                    plot_dir = Path(self.save_path).parent
                    try:
                        _missing = [
                            _run for _run in result.session.runs
                            if id(_run) not in self._export.exported_run_ids]
                        for _run in _missing:
                            self._export.submit_plot(
                                result.session, _run, plot_dir,
                                self.auto_save_plots_fmt,
                                self.auto_save_plots_dpi)
                        if _missing:
                            self.log_msg.emit(
                                f"Back-filling {len(_missing)} channel "
                                f"plot(s) not yet saved in real time…")
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
        # Drain the background export thread BEFORE reporting finished:
        # every queued TIF render / XLSX write lands on disk first, so
        # the completion message (and the GUI teardown that follows the
        # ``finished`` signal) never races an in-flight export.  During
        # the run these exports were fully parallel; only this final
        # flush waits, and it's normally near-instant (the queue drained
        # as the experiment went).
        self._drain_exports()
        # QUIT/aborted run → add an ``_ABORTED`` suffix to the session-level
        # files so the file LIST itself shows a partial run (operator: "when
        # the experiment is quit, make sure that the files reflect that").
        # Done AFTER the drain so every export write has landed and nothing
        # re-creates the base name; best-effort (a rename failure never blocks
        # completion).  The .npz already carries ``incomplete=True`` from the
        # save above; renaming just makes it visible without opening the file.
        if getattr(result, "aborted", False) and self.save_path is not None:
            self._mark_files_aborted()
        self.finished.emit(result)

    def _mark_files_aborted(self) -> None:
        """Rename the session-level ``.npz`` + ``.xlsx`` to a ``…_ABORTED``
        stem so a quit run is distinguishable from a complete one in the file
        browser.  Per-channel plot images keep their names (a channel figure
        is valid data regardless).  Best-effort + never raises."""
        base = Path(self.save_path)
        for suffix in (".npz", ".xlsx"):
            src = base.with_suffix(suffix)
            try:
                if not src.exists():
                    continue
                dst = src.with_name(src.stem + "_ABORTED" + suffix)
                if dst.exists():
                    dst.unlink()
                src.replace(dst)
                self.log_msg.emit(
                    f"Run was quit — {src.name} saved as {dst.name} (partial).")
            except Exception as e:
                self.log_msg.emit(
                    f"Could not mark {src.name} as aborted "
                    f"({type(e).__name__}: {e}); the file is still on disk.")

    #: Minimum seconds between IN-RUN .xlsx refreshes (the final end-of-run
    #: write ignores this).  120 s keeps the mirror reasonably current for a
    #: long LP/SP run without flooding a fast VT sweep with per-capture
    #: rewrites of the whole workbook.
    _XLSX_MIN_INTERVAL_S = 120.0

    def _maybe_refresh_xlsx(self, session, path, *, force: bool = False) -> None:
        """Enqueue a background .xlsx workbook refresh, rate-limited to one per
        :pyattr:`_XLSX_MIN_INTERVAL_S` (unless ``force``, for the final
        end-of-run write).  No-op when auto-export is off or ``path`` is None;
        never raises (a lost mid-run refresh is harmless — the final forced
        write + the canonical .npz still land)."""
        if not self.auto_export_xlsx or path is None:
            return
        import time as _t
        now = _t.monotonic()
        if not force and (now - self._last_xlsx_at) < self._XLSX_MIN_INTERVAL_S:
            return
        self._last_xlsx_at = now
        try:
            self._export.submit_xlsx(session, Path(path).with_suffix(".xlsx"))
        except Exception:
            pass

    def _drain_exports(self, timeout_ms: int = 300_000) -> None:
        """Flush the background export thread (TIF + XLSX queue).

        Blocks until every queued write completed or ``timeout_ms``
        expires (5 min default — generous; the queue is normally empty
        by end-of-run because exports proceeded in parallel with the
        experiment).  Never raises.
        """
        try:
            self._export.finish()
            if not self._export.wait(timeout_ms):
                self.log_msg.emit(
                    "⚠ Background exports still running at the flush "
                    "deadline — finishing without them; check the save "
                    "directory for missing files.")
        except Exception:
            pass

    def _test_name(self) -> str:
        return getattr(self.runner.session.test, "experiment", "")

    def _maybe_send_sms(self, body: str) -> None:
        """Fire a text via the email-to-SMS gateway when a phone + carrier
        are set (operator: "Allow for a phone number option for text
        messages").  Gated by the same ``email_notifications`` opt-in;
        no-op when phone/carrier or SMTP creds are missing."""
        if not (self.email_notifications
                and self.sms_phone and self.sms_carrier):
            return
        try:
            from ..notifications import send_sms_via_gateway
            ok = send_sms_via_gateway(
                phone=self.sms_phone, carrier=self.sms_carrier,
                subject="PULSAR", body=body)
            self.log_msg.emit(
                "Notification text sent." if ok else
                "Notification text skipped (carrier/SMTP not configured).")
        except Exception as e:
            self.log_msg.emit(f"Text notification failed: {e}")

    def _maybe_send_completion_email(self, *, elapsed_seconds: float) -> None:
        """Fire the success email + text if the user opted in and SMTP
        works.  Gated on ``email_notifications``; the email needs a
        recipient address, the text needs a phone + carrier."""
        if not self.email_notifications:
            return
        from ..notifications import _format_elapsed
        if self.user_email:
            try:
                from ..notifications import send_completion_email
                ok = send_completion_email(
                    to_email=self.user_email,
                    recipient_name=self.user_name,
                    subject_name=self.session_subject,
                    test_name=self._test_name(),
                    elapsed_seconds=elapsed_seconds,
                    attachments=[self.save_path] if self.save_path else None,
                )
                self.log_msg.emit("Completion email sent." if ok
                                  else "Completion email skipped (SMTP not configured).")
            except Exception as e:
                self.log_msg.emit(f"Email notification failed: {e}")
        # Short text — gateways truncate ~160 chars, so keep it tight.
        self._maybe_send_sms(
            f"PULSAR: {self.session_subject} {self._test_name()} completed "
            f"in {_format_elapsed(elapsed_seconds)}.".strip())

    def _maybe_send_failure_email(self, *, error_message: str,
                                  elapsed_seconds: float) -> None:
        """Fire the failure email + text if the user opted in and SMTP
        works."""
        if not self.email_notifications:
            return
        from ..notifications import _format_elapsed
        if self.user_email:
            try:
                from ..notifications import send_error_email
                ok = send_error_email(
                    to_email=self.user_email,
                    recipient_name=self.user_name,
                    subject_name=self.session_subject,
                    test_name=self._test_name(),
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
        # Tight failure text — leave room for a short error tail.
        _err = (error_message or "").strip().replace("\n", " ")
        self._maybe_send_sms(
            (f"PULSAR FAILED: {self.session_subject} {self._test_name()} at "
             f"{_format_elapsed(elapsed_seconds)}. {_err}").strip()[:300])


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

    #: Force single-combo selection for the (partial) multipolar kinds
    #: (BP/TP/PBP/PTP) even when SINGLE_CONFIG is False.  LP sets this True so
    #: a multipolar LP run tests exactly one combo (Monopolar stays
    #: multi-select for simultaneous-channel pulsing).
    MULTIPOLAR_SINGLE = False

    #: Allow MULTIPLE (partial) multipolar combos but forbid a channel from
    #: repeating as active OR return across them — PS stresses several
    #: channel-disjoint multipolar combos sequentially.  Mutually exclusive
    #: with MULTIPOLAR_SINGLE.
    MULTIPOLAR_NO_REPEAT = False

    #: True for tabs that support BURST / pulse-train stimulation (group N
    #: pulses into a longer burst period).  Reveals the pattern-panel burst
    #: group.  SP / CP / LP set this True; VT (ramps a single pulse's
    #: amplitude) / PS / EIS leave it False (burst is meaningless there).
    SUPPORTS_BURST = False

    #: Short tag identifying the experiment in log-pane lines (subclasses
    #: override).  Multiple tabs carry same-named widgets (duration, max
    #: current, …), so every param log line is prefixed with this tag.
    LOG_TAG = "?"

    #: Emitted with True when a run starts and False when it ends.
    #: MainWindow uses this to lock controls outside the running tab
    #: (Setup tab, hardware connection) so the user can't reconfigure
    #: a session while it's mid-flight.
    runStateChanged = QtCore.pyqtSignal(bool)

    #: General "a Test-parameters input changed" signal carrying a
    #: ready-to-log "<field> = <value>" string (mirrors
    #: ``SetupTab.settingChanged``).  MainWindow connects it to
    #: ``_log_setup_change`` AFTER prefs restore, so the construction /
    #: restore burst never reaches the log — only live user edits do
    #: (operator: "have all inputs and selections in test parameters
    #: show up in the log pane … including de-selection").
    paramChanged = QtCore.pyqtSignal(str)

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
        # INTERSTELLAR (interpulse-bias) state.  The per-tab closed-loop
        # config panel is built lazily in _assemble_pages ONLY when the
        # feature is enabled, and shown only once INTERSTELLAR is
        # connected from the Setup tab.  These two attributes must exist
        # before _assemble_pages runs (it calls _refresh_bias_visibility).
        #   * _bias_feedback_panel — the config panel, or None when the
        #     feature is disabled (public build) / not yet built.
        #   * _bias_connected      — mirrors the shared driver's state,
        #     driven by the ConnectionPanel's biasConnected/Disconnected
        #     signals (wired in set_bias_host).
        self._bias_feedback_panel = None
        self._bias_connected: bool = False
        self._bias_host = None
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
        # Raster resolution for the saved figures (matplotlib -rNNN).
        # 600 == plotting.DEFAULT_DPI / MATLAB ``-r600``.
        self._auto_save_plots_dpi: int = 600
        # Email notifications — opt-in via the Setup tab. Cached here
        # and read at start time, so a toggle mid-session affects the
        # NEXT run, not the in-flight one. Recipient identity + session
        # subject string are also cached here; ``_start_runner`` hands
        # them to the worker which forwards to ``send_completion_email``
        # / ``send_error_email``.
        self._email_notifications: bool = False
        self._user_email: str = ""
        self._user_name: str = ""
        # Optional SMS recipient (phone + carrier) for run-end text
        # notifications via the email-to-SMS gateway.  Pushed from the
        # Setup tab; consumed by the RunnerWorker.
        self._sms_phone: str = ""
        self._sms_carrier: str = ""
        self._session_subject: str = ""
        # Setup-tab notebook + composed <Notebook>_<Session> stem —
        # session identity for the Session object and the on-disk
        # .npz / .xlsx names (see set_session_identity).
        self._notebook: str = ""
        self._session_stem: str = ""
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
        # When set (LP resume-from-.npz), the scope-setup block frames
        # the horizontal window from THIS loaded pattern instead of the
        # live pattern panel — the panel can't be cheaply reconstructed
        # from a saved PulsePattern, but the loaded pattern's phase
        # widths are exactly what the layout needs.  Consumed once in
        # ``_start_runner_body`` and cleared right after.
        self._resume_pattern_override: Optional[PulsePattern] = None

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
        # Keep the side metric table in sync with the capture SHOWN in the
        # plot: when the user navigates (per-capture dropdown / prev / next,
        # the entry list, or Latest) the scope emits captureChanged with the
        # now-displayed capture, and we re-point the table at it.  Without
        # this the table stayed on the last LIVE capture while the plot was
        # navigated, so the two showed DIFFERENT captures (operator: "the
        # plot values [are] not matching with the table values").
        try:
            self.multichan_scope.captureChanged.connect(
                self.metrics_side.show_capture)
        except Exception:
            pass
        self.log_pane = LogPane()
        self.pattern_panel = PatternControlPanel(title="Pulse pattern")
        # Reveal the burst / pulse-train group only on tabs that support it
        # (SP / CP / LP).  ``self.SUPPORTS_BURST`` resolves via MRO to the
        # subclass override even here in the base __init__ (same mechanism as
        # ``self.SINGLE_CONFIG`` below).  Guarded for test stubs.
        if hasattr(self.pattern_panel, "set_burst_available"):
            self.pattern_panel.set_burst_available(self.SUPPORTS_BURST)
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
        self.combo_panel = CombinationPanel(
            single_mode=self.SINGLE_CONFIG,
            multipolar_single=self.MULTIPOLAR_SINGLE,
            multipolar_no_repeat=self.MULTIPOLAR_NO_REPEAT)
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
        # Bias-feedback visibility tracks the configuration mix: the
        # STM32 bias module only makes sense for MONOPOLAR configs
        # (in BP / TP / CG the return current goes through another
        # array electrode, not the STM32-driven counter electrode).
        # Hide the whole bias panel when no MP configs are queued so
        # operators on bipolar-only sessions aren't tempted to flip
        # the closed-loop checkbox.  See
        # :meth:`_refresh_bias_visibility`.  Task #47.
        self.combo_panel.combinationsChanged.connect(
            lambda _configs: self._refresh_bias_visibility())
        # Log channel/combo SELECTION and DE-SELECTION (operator: "I do
        # include device configuration and channel/combo selection … I also
        # include de-selection").  ``combinationsChanged`` fires on grid
        # clicks (select + deselect), combo-list check/uncheck, kind and
        # global-return changes — the differ below reports exactly what
        # changed.  The old ``_on_selection_changed`` slot was DEAD code
        # (never connected), so selection changes previously never logged.
        self._last_combo_names: Optional[List[str]] = None
        self.combo_panel.combinationsChanged.connect(self._log_combo_selection)
        self.combo_panel.set_array(array)

        self.start_btn = QtWidgets.QPushButton("Start")
        self.start_btn.setEnabled(False)
        self.pause_btn = QtWidgets.QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.setEnabled(False)
        self.pause_btn.setToolTip(
            "Pause halts stimulation and holds the run; Resume restarts "
            "pulsing and continues where it left off (F6).")
        # Runner-side pause/resume is now implemented on every
        # ExperimentRunner (base.wait_if_paused + per-runner checkpoints),
        # so the control is visible.  It's disabled until a run is active
        # (enabled in _start_runner_body, reset in _on_finished).
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
        self.ensure_preview_rendered()

    def ensure_preview_rendered(self):
        """Render the initial pattern preview exactly once.

        Idempotent (guarded by ``_first_show_done``).  Called from TWO
        places because the preview lives in ``params_page``, which
        MainWindow re-parents into a SEPARATE top-level "Test
        parameters" tab:

        * :meth:`showEvent` — fires when the EXPERIMENT-VIEW tab is
          shown.
        * ``MainWindow._show_experiment`` — fires when this experiment's
          ``params_page`` (the preview's actual host) becomes the active
          Test-parameters content.

        Whichever happens first does the single render; the other is a
        no-op.  Without the second call a user who configures on the
        Test parameters tab and never opens the experiment view (the
        launch default focuses Setup) sees a permanently-stale
        "No pulse pattern set." preview even though every pulse field
        is populated — the bug this fixes.

        Deliberately LAZY: only the ACTIVE experiment's preview is
        built, and only once, so cold launch doesn't pay for a
        pyqtgraph rebuild per experiment tab (see CLAUDE.md §6).
        ``_first_show_done`` is left False if the render raises so a
        transient failure retries on the next show rather than wedging
        the banner forever.
        """
        if getattr(self, "_first_show_done", False):
            return
        try:
            self.pattern_preview.set_pattern(self.pattern_panel.pattern())
            self._first_show_done = True
        except Exception:
            pass

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
        # (The "Fast scaling mode" toggle was removed — the
        # MEASUrement:IMMed min/max path it enabled was both SLOWER than
        # a single CURVe? transfer on real hardware AND unable to detect
        # rail-clipping, so it under-reported electrode polarization. The
        # full-capture rescale is now the only path.)
        # Waveform smoothing — centered moving average on every recorded
        # capture (plot + metrics + saved .npz).  Default OFF.
        left.addWidget(self._build_smoothing_group())
        # Interpulse-bias (INTERSTELLAR) closed-loop feedback — sits
        # below the camera group on the Test Parameters page.  Built
        # ONLY when the experimental feature flag is enabled (the public
        # build has no bias UI at all — gotcha #103).  It shares the
        # single driver opened from the Setup tab, and stays hidden until
        # INTERSTELLAR is connected.  See :meth:`_build_bias_feedback_group`.
        if interstellar_enabled():
            left.addWidget(self._build_bias_feedback_group())
        # Initial visibility.  Safe whether or not the panel was built:
        # _refresh_bias_visibility no-ops when the panel is None (feature
        # disabled) and otherwise hides it until INTERSTELLAR connects.
        self._refresh_bias_visibility()
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
        _ch_legend = QtWidgets.QLabel(
            "<b>Channel selection</b> &nbsp;—&nbsp; "
            "click = toggle active &nbsp;·&nbsp; "
            "Ctrl + click = remove &nbsp;·&nbsp; "
            "External Return square = external counter"
        )
        # Reflow on narrow column widths — the splitter to the left
        # can be dragged to shrink the right column.
        _ch_legend.setWordWrap(True)
        right.addWidget(_ch_legend)
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
        # Held so the run-lock disables only the INPUT content (both scroll
        # columns' inner widgets) while leaving ``params_page`` + its scroll
        # areas enabled — so the operator can scroll/read the Test
        # parameters during a run (operator: "allow for scrolling through …
        # Setup and Test Parameters").  See ``_set_locked``.
        self._params_run_lock_content = (left_inner, right_inner)

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
        # Run-progress bar (elapsed time + pulse count) above the scope —
        # MATLAB-style pulsing waitbar (see _build_run_progress).
        ep.addWidget(self._build_run_progress())
        ep.addWidget(ep_split, stretch=1)

        # Final construction step: wire EVERY declared Test-parameters
        # input (base + subclass PARAM_LOG_WIDGETS + bias panel) to the
        # param log so each change shows in the log pane.  Here — not in
        # __init__ — because subclass widgets only exist by the time the
        # subclass calls _assemble_pages.
        self._wire_all_param_logs()

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
        # Camera parameters only make sense with a camera (operator: "if
        # no camera is connected, hide the camera parameters in test
        # parameters tab").  The group's toggles keep their state (and
        # keep round-tripping through prefs) — only the visibility flips.
        try:
            self._camera_capture_group.setVisible(True)
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
        try:
            self._camera_capture_group.setVisible(False)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Run-progress bar (MATLAB ``updateWaitbar.m`` port)
    # ------------------------------------------------------------------
    # MATLAB's pulsing waitbar shows two lines over a fractional bar:
    #   "12.3 / 60 s"   and   "(615 / 3,000 pulses)"
    # where pulses-so-far = elapsed × stim-rate and the bar fills to
    # elapsed/duration.  We render the same content as a thin bar + a
    # single-line label, updated once a second by a QTimer while a run
    # is live.  Bounded runs (SP / LP) get the fractional bar; an
    # UNBOUNDED Continuous-Pulsing run gets a busy bar + running counts
    # (no total).  Ramp experiments (VT / PS) leave it HIDDEN and rely
    # on the status-bar channel progress instead — they have no fixed
    # duration to fill a bar against.
    def _effective_pulse_rate_hz(self) -> float:
        """Overall pulses-per-second of the current pattern for duration↔pulses
        conversion + the run-progress count.  A BURST delivers
        ``pulses_per_burst`` pulses per ``burst_period`` — far below the
        intra-burst ``rate_hz`` — so the 'pulses' duration unit means DELIVERED
        pulses.  Identity ``== rate_hz`` for an ordinary (non-burst) pattern."""
        try:
            pat = self.pattern_panel.pattern()
            return max(float(getattr(pat, "effective_pulse_rate_hz", None)
                             or pat.rate_hz), 1e-6)
        except Exception:
            return 1e-6

    def _build_run_progress(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(2, 0, 2, 2)
        lay.setSpacing(8)
        self.run_progress_bar = QtWidgets.QProgressBar()
        self.run_progress_bar.setRange(0, 100)
        self.run_progress_bar.setTextVisible(False)
        self.run_progress_bar.setFixedHeight(14)
        self.run_progress_label = QtWidgets.QLabel("")
        self.run_progress_label.setMinimumWidth(300)
        # Tabular (fixed-width) digits so the elapsed-time / pulse-count
        # readouts don't jitter as they tick.  Qt's QSS engine does NOT
        # support the CSS ``font-variant-numeric`` property — setting it
        # via setStyleSheet was a no-op that logged "Unknown property
        # font-variant-numeric" once per affected widget, flooding the
        # launch console.  Use the QFont OpenType-feature API instead
        # (Qt 6.7+; silently skipped on older Qt — no warning either way).
        try:
            _pf = self.run_progress_label.font()
            _pf.setFeature(QtGui.QFont.Tag("tnum"), 1)
            self.run_progress_label.setFont(_pf)
        except Exception:
            pass
        lay.addWidget(self.run_progress_bar, stretch=1)
        lay.addWidget(self.run_progress_label, stretch=0)
        w.setVisible(False)            # only visible during a run
        self._run_progress_widget = w
        self._run_progress_timer = QtCore.QTimer(self)
        self._run_progress_timer.setInterval(1000)
        self._run_progress_timer.timeout.connect(self._tick_run_progress)
        self._run_progress_t0 = None
        self._run_progress_duration_s = None
        self._run_progress_rate_hz = None
        # Channel-step mode (VT): the bar tracks CHANNELS/combos completed
        # instead of elapsed time (the adaptive amplitude ramp has no fixed
        # duration to fill against).  Event-driven by _on_runner_progress;
        # the 1 s timer only ticks the elapsed-time portion of the label.
        self._run_progress_step_mode = False
        self._run_progress_total_steps = 0
        self._run_progress_step_label = ""
        return w

    def begin_run_progress(self, *, duration_s, rate_hz,
                           total_steps: int = 0) -> None:
        """Show + start the run progress bar.

        ``duration_s`` finite → fractional TIME bar (bounded SP/LP burst);
        ``float('inf')`` → busy bar + running counts (Continuous Pulsing);
        ``None`` + ``total_steps > 0`` → CHANNEL-STEP bar (VT: the amplitude
        ramp is adaptive, so the bar tracks channels/combos completed of
        ``total_steps``, advanced by ``_on_runner_progress`` on each
        per-channel progress event); ``None`` + no steps → HIDDEN (e.g. PS,
        which uses the status-bar channel progress only).
        """
        import time as _t
        # ---- Channel-step mode (VT) --------------------------------------
        if (duration_s is None or duration_s == 0) and int(total_steps) > 0:
            self._run_progress_step_mode = True
            self._run_progress_total_steps = int(total_steps)
            self._run_progress_step_label = f"Channel 0 / {int(total_steps)}"
            self._run_progress_t0 = _t.monotonic()
            self._run_progress_duration_s = None
            self._run_progress_rate_hz = float(rate_hz) if rate_hz else 0.0
            self.run_progress_bar.setRange(0, 100)
            self.run_progress_bar.setValue(0)
            # Show the "channels done / total" text ON the bar so it's
            # unmistakably a progress bar (operator: "where is the progress
            # bar" — the beside-label alone was easy to miss).
            self.run_progress_bar.setTextVisible(True)
            self.run_progress_bar.setFormat(f"0 / {int(total_steps)} channels")
            self._run_progress_widget.setVisible(True)
            self._tick_run_progress()
            self._run_progress_timer.start()   # ticks the elapsed readout
            return
        self._run_progress_step_mode = False
        if (duration_s is None or rate_hz is None
                or not (rate_hz > 0) or duration_s == 0):
            self._end_run_progress()
            return
        self._run_progress_t0 = _t.monotonic()
        self._run_progress_duration_s = float(duration_s)
        self._run_progress_rate_hz = float(rate_hz)
        bounded = self._run_progress_duration_s != float("inf")
        # range (0, 0) renders a Qt "busy"/marquee bar for unbounded runs.
        self.run_progress_bar.setRange(0, 100 if bounded else 0)
        if bounded:
            self.run_progress_bar.setValue(0)
        self._run_progress_widget.setVisible(True)
        self._tick_run_progress()
        self._run_progress_timer.start()

    def _tick_run_progress(self) -> None:
        import time as _t
        if self._run_progress_t0 is None:
            return
        elapsed = _t.monotonic() - self._run_progress_t0
        # Channel-step mode: the BAR value is set by _on_runner_progress (per
        # channel); the timer only refreshes the elapsed-time readout so a
        # long channel doesn't look frozen between per-channel events.
        if getattr(self, "_run_progress_step_mode", False):
            mins, secs = divmod(int(round(max(0.0, elapsed))), 60)
            hrs, mins = divmod(mins, 60)
            _el = (f"{hrs:d}:{mins:02d}:{secs:02d}" if hrs
                   else f"{mins:d}:{secs:02d}")
            self.run_progress_label.setText(
                f"{self._run_progress_step_label}   ·   elapsed {_el}")
            return
        rate = self._run_progress_rate_hz or 0.0
        dur = self._run_progress_duration_s
        pulses = elapsed * rate
        if dur is not None and dur != float("inf") and dur > 0:
            frac = max(0.0, min(elapsed / dur, 1.0))
            self.run_progress_bar.setValue(int(round(frac * 100)))
            total_pulses = dur * rate
            # SPACE thousands separators (operator: "separate thousands with a
            # space instead of a comma").
            self.run_progress_label.setText(
                f"{_group_thousands(elapsed, 1)} / {_group_thousands(dur)} s"
                f"   ·   ({_group_thousands(pulses)} / "
                f"{_group_thousands(total_pulses)} pulses)")
        else:
            # Unbounded (Continuous Pulsing) — no total to divide by.
            self.run_progress_label.setText(
                f"elapsed {_group_thousands(elapsed, 1)} s   ·   "
                f"{_group_thousands(pulses)} pulses")

    def _end_run_progress(self) -> None:
        try:
            self._run_progress_timer.stop()
        except Exception:
            pass
        self._run_progress_t0 = None
        self._run_progress_step_mode = False
        # Reset the on-bar text (channel mode turns it on) so a later
        # time-mode run (SP/LP) keeps its clean text-less bar.
        try:
            self.run_progress_bar.setTextVisible(False)
            self.run_progress_bar.setFormat("%p%")
        except Exception:
            pass
        try:
            self._run_progress_widget.setVisible(False)
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
        # Hidden until a camera is actually connected (operator: "if no
        # camera is connected, hide the camera parameters in test
        # parameters tab").  The connect/disconnect slots flip visibility;
        # prefs still round-trip through the (hidden) widgets.
        try:
            from .camera import camera_service
            grp.setVisible(bool(camera_service().is_connected()))
        except Exception:
            grp.setVisible(False)
        return grp

    def _build_smoothing_group(self) -> QtWidgets.QGroupBox:
        """'Waveform smoothing' toggle + window for the Test Parameters
        page.

        When ON, a centered moving average of ``window`` samples is
        applied to EVERY recorded capture's channel arrays — the plot,
        the computed metrics, AND the saved ``.npz`` all use the smoothed
        waveform.  Tames the broadband noise from running channels at
        full bandwidth.  Default OFF.  Round-trips via
        :meth:`current_prefs` / :meth:`restore_prefs` under the
        ``smoothing`` key.
        """
        grp = QtWidgets.QGroupBox("Waveform smoothing")
        gl = QtWidgets.QHBoxLayout(grp)
        gl.setContentsMargins(8, 6, 8, 6)
        gl.setSpacing(8)
        self.smoothing_chk = QtWidgets.QCheckBox(
            "Smooth recorded waveforms — moving average of")
        self.smoothing_chk.setChecked(False)   # default OFF
        self.smoothing_chk.setToolTip(
            "Apply a centered N-point moving average to every recorded "
            "capture's V_mon / I_mon / E_act / E_ret traces.  Affects the "
            "plot, the computed metrics, AND the saved .npz (the smoothed "
            "waveform is what gets recorded).  Tames broadband noise from "
            "running channels at full bandwidth; a small window (~5 "
            "samples ≈ 160 ns at 32 ns/sample) removes spikes without "
            "distorting the µs-scale pulse shape.  Default OFF.")
        self.smoothing_window_spin = QtWidgets.QSpinBox()
        self.smoothing_window_spin.setRange(2, 201)
        # Step by 1 (operator) — the moving average (uniform_filter1d) handles
        # any window, even or odd, so there's no need to step by 2 to stay odd.
        self.smoothing_window_spin.setSingleStep(1)
        self.smoothing_window_spin.setValue(5)
        self.smoothing_window_spin.setSuffix(" samples")
        self.smoothing_window_spin.setToolTip(
            "Centered moving-average window in samples.  Larger = "
            "smoother but more rounding of fast edges.  5 samples is a "
            "gentle de-spike.")
        # Disable the window spinbox when smoothing is off (clearer than
        # a silently-ignored value) — same pattern as the camera group.
        self.smoothing_window_spin.setEnabled(False)
        self.smoothing_chk.toggled.connect(
            self.smoothing_window_spin.setEnabled)
        gl.addWidget(self.smoothing_chk)
        gl.addWidget(self.smoothing_window_spin)
        gl.addStretch(1)
        self._smoothing_group = grp
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

    # ---- Interpulse-bias feedback (per-tab) ----------------------------
    def _build_bias_feedback_group(self) -> QtWidgets.QGroupBox:
        """Build the per-tab BiasFeedbackPanel and stash a reference.

        The widget is a QGroupBox-shaped composite (connector + config
        spinboxes + status badge) defined in
        :mod:`stimtest.gui.bias_feedback_panel`.  Until MainWindow
        calls :meth:`set_bias_host`, the connector's Connect button
        is disabled (no host to delegate to) — operator sees the
        widget at startup but can't open a parallel driver instance.

        Master Enable checkbox defaults OFF, so a run without bias
        intent is a pure no-op: :meth:`BiasFeedbackPanel.feedback_config`
        returns ``None`` and the runner skips the closed loop
        entirely.

        Per-tab prefs round-trip via ``current_prefs`` /
        ``restore_prefs`` under the ``bias_feedback`` key.
        """
        from .bias_feedback_panel import BiasFeedbackPanel
        # include_connector=False — INTERSTELLAR's Connect/Disconnect
        # button lives in the Setup tab's ConnectionPanel now, so this
        # per-tab panel is purely the closed-loop CONFIG + status.  (A
        # connector here would be unreachable anyway: the panel is hidden
        # until INTERSTELLAR is connected.)
        grp = BiasFeedbackPanel(self, include_connector=False)
        # Store on self for prefs round-trip + so the runner-bind
        # layer (#43) can call .feedback_config() at run start.
        self._bias_feedback_panel = grp
        return grp

    def set_bias_host(self, host: Optional[object]) -> None:
        """Plumb the shared ConnectionPanel host (the INTERSTELLAR
        driver owner) into this tab.

        Called by MainWindow once per tab right after construction
        (parallel to :meth:`set_hardware`).  Does three things:

        1. Hands the host to the per-tab config panel (a no-op when the
           panel has no embedded connector — the tabs' case).
        2. Stashes the host so :meth:`_attach_bias_controller_to_runner`
           can pull the shared driver out of it at run start.
        3. Subscribes to the host's ``biasConnected`` /
           ``biasDisconnected`` signals so this tab's config panel shows
           when INTERSTELLAR connects and hides when it disconnects
           (operator: "If INTERSTELLAR is not connected, then the
           interpulse bias option is hidden in the test parameters").

        Idempotent + safe with ``None`` to detach.
        """
        panel = getattr(self, "_bias_feedback_panel", None)
        if panel is not None:
            try:
                panel.set_bias_host(host)
            except Exception:
                pass
        prev_host = getattr(self, "_bias_host", None)
        self._bias_host = host
        # Re-wire the connect/disconnect signals only when the host
        # actually changed — re-connecting the SAME host with
        # UniqueConnection raises in PyQt6 (gotcha #31a).
        if host is not prev_host:
            if prev_host is not None:
                for signal_name, slot in (
                    ("biasConnected", self._on_bias_connected),
                    ("biasDisconnected", self._on_bias_disconnected),
                ):
                    sig = getattr(prev_host, signal_name, None)
                    try:
                        sig.disconnect(slot)
                    except (TypeError, RuntimeError, AttributeError):
                        pass
            if host is not None:
                try:
                    host.biasConnected.connect(
                        self._on_bias_connected,
                        QtCore.Qt.ConnectionType.UniqueConnection)
                    host.biasDisconnected.connect(
                        self._on_bias_disconnected,
                        QtCore.Qt.ConnectionType.UniqueConnection)
                except Exception:
                    # Host doesn't expose the expected signals — leave
                    # visibility driven by the sync below.
                    pass
        # Sync the current connection state (the host may already be
        # connected, e.g. the operator connected before this tab was
        # shown) and refresh visibility accordingly.
        self._bias_connected = bool(
            getattr(host, "bias", None) is not None) if host is not None \
            else False
        self._refresh_bias_visibility()

    def _on_bias_connected(self, _info: object = None) -> None:
        """INTERSTELLAR was connected (from the Setup tab) — reveal this
        tab's closed-loop config (subject to the config-mix gate)."""
        self._bias_connected = True
        self._refresh_bias_visibility()

    def _on_bias_disconnected(self) -> None:
        """INTERSTELLAR was disconnected — hide this tab's closed-loop
        config so the option isn't offered without hardware."""
        self._bias_connected = False
        self._refresh_bias_visibility()

    def _attach_bias_controller_to_runner(self, runner) -> None:
        """Build a :class:`BiasFeedbackController` from the panel's
        current settings + the shared bias driver, and push it onto
        ``runner.bias_controller`` so :meth:`arm_bias_feedback` finds
        a controller when the runner reaches that point.

        Three early-out cases (any of them → leave the controller
        unset; runner's arm/disarm helpers short-circuit silently):

        * BiasFeedbackPanel master Enable is OFF (``feedback_config()``
          returns ``None``).
        * No bias driver has been opened in the shared host (operator
          either didn't press Connect or the STM32 isn't on the bus).
        * The panel / host attribute is missing (tests, partial
          construction).

        Logs the decision exactly once per start so the operator's
        intent + the runner's resolution are visible in the session
        log alongside other run-start setup.
        """
        panel = getattr(self, "_bias_feedback_panel", None)
        host = getattr(self, "_bias_host", None)
        if panel is None:
            return
        try:
            cfg = panel.feedback_config()
        except Exception as exc:
            self.log_pane.log(
                f"[bias] feedback_config() raised "
                f"{type(exc).__name__}: {exc} — closed-loop skipped.")
            return
        if cfg is None:
            self.log_pane.log(
                "[bias] closed-loop feedback disabled "
                "(BiasFeedbackPanel master Enable is off).")
            return
        bias_driver = getattr(host, "bias", None) if host is not None else None
        if bias_driver is None:
            self.log_pane.log(
                "[bias] closed-loop feedback skipped: master Enable "
                "is on but no bias driver is open (press Connect on "
                "the BiasFeedbackPanel first).")
            return
        try:
            from ..experiments.bias_feedback import BiasFeedbackController
            controller = BiasFeedbackController(
                scope=self._scope, bias_module=bias_driver, config=cfg)
        except Exception as exc:
            self.log_pane.log(
                f"[bias] controller construction raised "
                f"{type(exc).__name__}: {exc} — closed-loop skipped.")
            return
        runner.bias_controller = controller
        # The panel's status badge subscribes to log lines via
        # _on_bias_step_log (wired in _start_runner_body alongside
        # the log_msg → log_pane connection).  No additional wiring
        # needed here.
        self.log_pane.log(
            f"[bias] closed-loop feedback enabled: setpoint="
            f"{cfg.setpoint_v:.3f} V, tolerance=±"
            f"{cfg.tolerance_v * 1e3:.1f} mV, k_i={cfg.k_i:.3f}.")

    def _on_bias_step_log(self, message: str) -> None:
        """Parse a ``[bias-step]`` log line and push the resulting
        :class:`BiasFeedbackStep` into the panel's status badge.

        Wired to ``RunnerWorker.log_msg`` in :meth:`_start_runner_body`
        so every log line passes through here; non-matching lines
        early-out cheaply.  See the docstring + comment on the
        module-level ``_BIAS_STEP_LINE_RE`` constant for the parsed
        format.

        Defensive: any parse failure or panel access error is
        silently swallowed — letting an exception here propagate
        would corrupt the worker's log stream.
        """
        try:
            panel = getattr(self, "_bias_feedback_panel", None)
            if panel is None:
                return
            if not isinstance(message, str):
                return
            m = _BIAS_STEP_LINE_RE.search(message)
            if m is None:
                return
            from ..experiments.bias_feedback import BiasFeedbackStep
            measured = float(m.group("measured"))
            error_mv = float(m.group("error"))
            bias = float(m.group("bias"))
            flags = m.group("flags") or ""
            saturated = "SATURATED" in flags
            vmon_sane = "V_mon insane" not in flags
            step = BiasFeedbackStep(
                measured_v=measured,
                error_v=error_mv * 1e-3,
                in_deadband=False,
                bias_v_before=bias,
                bias_v_after=bias,
                saturated=saturated,
                vmon_sane=vmon_sane,
                skipped=saturated or not vmon_sane,
                note="",
            )
            panel.set_status(step)
        except Exception:
            # Never let a parse error kill the log stream.
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

    def set_auto_save_plots(self, on: bool, fmt: str, dpi: int = 600):
        """Cache whether the next run should drop a plot per
        channel/combination next to the .npz, in what format
        (one of ``png``/``jpg``/``tif``/``svg``), and at what raster
        resolution (``dpi`` — ignored for the vector ``svg`` format)."""
        self._auto_save_plots = bool(on)
        f = (fmt or "").lower().lstrip(".")
        if f:
            self._auto_save_plots_fmt = f
        try:
            d = int(dpi)
            if d > 0:
                self._auto_save_plots_dpi = d
        except (TypeError, ValueError):
            pass

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

    def set_sms_recipient(self, phone: str, carrier: str):
        """Cache the recipient's phone + carrier for run-end TEXT
        notifications (email-to-SMS gateway).  Pushed from the Setup tab
        via :pyattr:`SetupTab.smsRecipientChanged`."""
        self._sms_phone = str(phone or "").strip()
        self._sms_carrier = str(carrier or "").strip()

    def set_session_subject(self, subject: str):
        """Cache the raw Session field for the email subject line."""
        self._session_subject = str(subject or "").strip()

    def set_session_identity(self, notebook: str, stem: str):
        """Cache the Setup tab's notebook field + the composed
        ``<Notebook>_<Session>`` filesystem stem.  The stem names the
        .npz / .xlsx (operator: "the file name should be
        '[notebook]_[session]'", not the first config's ``VT_CH01``);
        the notebook feeds ``Session.notebook`` so the per-channel plot
        exports (``{session.name}_{CHxx}``) carry the same identity.
        """
        self._notebook = str(notebook or "").strip()
        self._session_stem = str(stem or "").strip()

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
        # A bare test board has no electrode surface area (0) → the
        # charge-density damage screen is meaningless (Q / 0) and would
        # otherwise flag a spurious alert.  Skip it entirely.
        if not surface_area_um2 or float(surface_area_um2) <= 0:
            return True
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

        Also forwards the I_mon-trigger minimum-amplitude constraint to the
        pattern panel (operator: "When Imon is the trigger source, current
        cannot be 0"): a digital sync fires regardless of stim current (0 µA
        allowed, e.g. VT-max from 0), but the I_mon fallback can't trigger
        below ``IMON_TRIGGER_MIN_AMPLITUDE_UA`` — so the excitation-current
        input's magnitude is floored at 0.1 µA in that case.
        """
        self._trigger_is_digital = bool(is_digital)
        pp = getattr(self, "pattern_panel", None)
        if pp is not None and hasattr(pp, "set_min_amp_magnitude"):
            from ..experiments.base import IMON_TRIGGER_MIN_AMPLITUDE_UA
            pp.set_min_amp_magnitude(
                0.0 if self._trigger_is_digital
                else IMON_TRIGGER_MIN_AMPLITUDE_UA)

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
        # Feed the pattern panel so its per-capture acquisition-time readout
        # (beside the pulse rate) reflects the real average count + mode.
        pp = getattr(self, "pattern_panel", None)
        if pp is not None:
            try:
                pp.set_acquisition_info(self._acq_mode, self._acq_n_avg)
            except Exception:
                pass

    def set_save_dir(self, p: Path): self._save_dir = Path(p)

    def set_hardware(self, stim: Stimulator, scope: Oscilloscope):
        self._stim = stim; self._scope = scope
        self._refresh_start_enabled()

    def clear_hardware(self):
        self._stim = None; self._scope = None
        self._refresh_start_enabled()
        self.stop_btn.setEnabled(False)

    # ------------------------------------------------------------------
    def _resolve_trigger_settings(self):
        """Resolve the scope trigger from the Setup state + current pattern.

        Returns ``(source, slope, level_v, digital, note)`` — the single
        source of truth shared by the Start-time scope setup AND the
        Test-parameters entry pre-apply, so the two can't drift.

        Paths (see SetupTab.is_digital_trigger for the priority rules):
          1./2. digital (EXT BNC or a channel tagged Role=Trigger) →
             RISE @ 1.4 V (TTL midpoint; active-high sync, no polarity
             dependence).
          3. I_mon fallback → slope follows phase-1 polarity (FALL for
             cathodic-first), level from ``imon_trigger_level``.
        """
        TTL_LEVEL_V = 1.4   # TTL midpoint (sync lines are 3.3 V / 5 V)
        # CONTINUOUS waveform (no interpulse gap — a KHFAC sinusoid or any
        # back-to-back pattern that fills its period): the Plexon digital
        # sync stays HIGH the whole time it stimulates, so there's NO edge
        # for the scope to trigger on in NORMAL mode → NUMACq stays 0/64 and
        # the capture times out on a stale frame (confirmed on the bench:
        # "poll deadline reached, NUMACq = 0/64").  The I_mon LEVEL trigger
        # (sized near the pulse amplitude) ALSO misses, because at low
        # currents the amplitude-level sits above the tiny I_mon signal.
        # Trigger on the I_mon analog ZERO-CROSSING instead: a continuous
        # sinusoid (or bipolar square) crosses 0 cleanly every cycle
        # regardless of amplitude → a stable, PHASE-LOCKED NORMAL trigger so
        # AVERAGE-mode captures align (a random-phase AUTO trigger would
        # average the sinusoid to ~zero).  I_mon is AC-coupled (gotcha #83)
        # so its zero-crossing is genuinely at 0 V.  Scoped strictly to
        # ``not has_interpulse_gap()`` so ordinary pulsed patterns (which
        # DO have an idle interpulse edge) are unaffected.
        try:
            _cont_pat = self.pattern_panel.pattern()
        except Exception:
            _cont_pat = None
        self._continuous_pw_plan = None
        if (_cont_pat is not None and _cont_pat.phases
                and not _cont_pat.has_interpulse_gap()):
            from ..experiments.base import continuous_trigger_plan
            _imon_ch = self._aliases.get("imon", "CH2")
            _ph0 = _cont_pat.phases[0]
            _a0 = float(_ph0.amplitude_ua)
            _si = getattr(self._stim, "info", None)
            _vpu = float(getattr(_si, "imon_scaling_v_per_ua", 0.0) or 0.0)
            _plan = continuous_trigger_plan(_cont_pat,
                                            imon_v_per_ua=_vpu or None)
            _level = float(_plan["level_v"])
            _slope = str(_plan["slope"])
            if _plan["kind"] == "pulse_width":
                # Stash the plan — the caller UPGRADES the edge trigger to a
                # pulse-width-qualified one right after set_trigger (the
                # scope may decline → edge stays; strict upgrade).
                _plan = dict(_plan)
                _plan["source"] = _imon_ch
                self._continuous_pw_plan = _plan
            _lvl_txt = ("0 mV (zero-crossing)" if _level == 0.0
                        else f"{_level * 1000:+.2f} mV (½ peak)")
            _pw_txt = ("" if self._continuous_pw_plan is None else
                       f" + pulse-width qualification (> "
                       f"{_plan['width_s'] * 1e6:.1f} µs, {_plan['polarity']})")
            note = (f"path = I_mon ({_imon_ch}), level = {_lvl_txt}, slope = "
                    f"{_slope} (phase-1 {_a0:+.1f} µA, {_ph0.shape}){_pw_txt} "
                    "— CONTINUOUS waveform (no interpulse delay): the digital "
                    "sync has no edge and a level trigger misses the signal, "
                    "so trigger on the I_mon analog edge.")
            return (_imon_ch, _slope, _level, False, note)
        if self._trigger_is_digital:
            _path_note = ("EXT BNC" if self._trigger_source == "EXT"
                          else f"channel-Trigger ({self._trigger_source})")
            note = (f"path = {_path_note}, slope = RISE, "
                    f"level = {TTL_LEVEL_V*1000:.0f} mV (TTL sync line)"
                    + (" (EXT — scope firmware may auto-set level)"
                       if self._trigger_source == "EXT" else ""))
            return (self._trigger_source, "RISE", TTL_LEVEL_V, True, note)
        try:
            from ..experiments.base import (
                imon_trigger_level, imon_trigger_slope)
            _pat = self.pattern_panel.pattern()
            # FIRST PHASE — not excitation_phase — drives slope + level:
            # the trigger sees whichever phase fires first in time.
            _ph0 = (_pat.phases[0] if _pat and _pat.phases else None)
            _amp_raw = float(_ph0.amplitude_ua) if _ph0 is not None else 0.0
            # SLOPE follows the phase-1 POLARITY — FALL cathodal-first, RISE
            # anodal-first — via ``imon_trigger_slope`` (copysign-based, so a
            # 0 µA-start ramp's SIGNED ZERO still picks the right edge; the old
            # ``_amp_signed < 0`` with an ``or 10.0`` fallback flipped a
            # cathodal-first 0 µA start to RISE).
            slope = imon_trigger_slope(_amp_raw)
            # LEVEL magnitude fallback uses a SIGNED 10 µA so a signed-zero
            # start still yields a polarity-consistent level (FALL ⇒ negative).
            _amp_signed = _amp_raw or math.copysign(10.0, _amp_raw)
            _pw0 = (float(_ph0.width_us) if _ph0 is not None else 200.0)
            _si = getattr(self._stim, "info", None)
            _imon_vpu = float(
                getattr(_si, "imon_scaling_v_per_ua", 0.0) or 0.0)
            level = imon_trigger_level(
                _amp_signed, _pw0, imon_v_per_ua=_imon_vpu or None)
            note = (f"path = I_mon ({self._trigger_source}), slope = "
                    f"{slope} (from phase-1 polarity {_amp_raw:+.1f} µA), "
                    f"level = {level*1000:+.2f} mV (from imon_trigger_level)")
            return (self._trigger_source, slope, level, False, note)
        except Exception:
            note = (f"path = I_mon ({self._trigger_source}), fallback "
                    f"slope = {self._trigger_slope}, level = +50.00 mV "
                    f"(pattern unavailable)")
            return (self._trigger_source, self._trigger_slope,
                    0.05, False, note)

    def _maybe_upgrade_trigger_to_pulse_width(self) -> None:
        """UPGRADE the just-set edge trigger to a pulse-width-qualified one
        when the continuous-waveform plan calls for it (shaped, no-interpulse-
        delay pattern — see ``continuous_trigger_plan``).  Strict upgrade: a
        scope without pulse-width support (legacy family / simulator) declines
        and the I_mon edge trigger set by ``set_trigger`` stays in place.
        Call immediately AFTER ``set_trigger`` at every trigger-setup site."""
        plan = getattr(self, "_continuous_pw_plan", None)
        if not plan or self._scope is None:
            return
        try:
            applied = bool(self._scope.set_trigger_pulse_width(
                plan["source"], float(plan["level_v"]),
                str(plan["polarity"]), float(plan["width_s"]),
                when=str(plan.get("when", "MOREthan"))))
        except Exception:
            applied = False
        try:
            if applied:
                self.log_pane.log(
                    f"Scope trigger upgraded to PULSE-WIDTH qualification "
                    f"({plan['polarity']} pulse > "
                    f"{float(plan['width_s']) * 1e6:.1f} µs on "
                    f"{plan['source']}) — rejects narrow noise crossings on "
                    f"the continuous shaped waveform.")
            else:
                self.log_pane.log(
                    "Pulse-width trigger not supported on this scope — "
                    "keeping the I_mon edge trigger.")
        except Exception:
            pass

    def _imon_trigger_amplitude_error(self) -> "Optional[str]":
        """Return a message if the I_mon trigger can't fire on this pattern.

        Operator: "If Imon is the trigger source, require [the phase-1
        amplitude] >= |0.1| uA" — for ANY pulse shape, not just rectangular.
        With NO digital sync channel the scope triggers on the I_mon analog
        signal itself (edge or zero-crossing); below
        :data:`IMON_TRIGGER_MIN_AMPLITUDE_UA` that signal sits under the
        trigger/noise floor, so the scope never fires (NUMACq 0) and every
        capture is stale / misaligned.  A VT-max ramp that STARTS at 0 µA hits
        this on its first captures.  Digital-sync / EXT triggers fire on the
        TTL sync line regardless of stim current, so this guard applies ONLY
        to the I_mon trigger path.

        Returns ``None`` when the run may proceed (or when the trigger /
        pattern can't be resolved — fail open; the run itself will surface a
        genuine trigger problem).
        """
        try:
            _src, _slope, _level, _digital, _note = \
                self._resolve_trigger_settings()
        except Exception:
            return None
        if _digital:
            return None                       # sync line — fires regardless
        try:
            pat = self.pattern_panel.pattern()
        except Exception:
            return None
        if pat is None or not pat.phases:
            return None
        from ..experiments.base import IMON_TRIGGER_MIN_AMPLITUDE_UA
        # The MAGNITUDE (operator: "just as long as the magnitude is at least
        # 0.1 uA") — sign-agnostic, so a cathodal −5 µA (|−5| ≥ 0.1) passes and
        # only a sub-0.1 µA magnitude blocks.  Phase 1 is what the trigger fires
        # on (first in time); for a VT-max ramp this IS the starting amplitude
        # (gotcha #56), so a 0 µA start (signed-zero phase-1) is caught here.
        amp_mag = abs(float(pat.phases[0].amplitude_ua))
        if amp_mag >= IMON_TRIGGER_MIN_AMPLITUDE_UA:
            return None
        return (
            f"I_mon is the trigger source, but the phase-1 amplitude magnitude "
            f"is {amp_mag:.3f} µA — below the "
            f"{IMON_TRIGGER_MIN_AMPLITUDE_UA:.1f} µA minimum. The I_mon signal "
            f"is too small to trigger on below that, so the scope never fires "
            f"and the captures are stale / misaligned.\n\n"
            f"Set the amplitude (or a VT-max ramp's STARTING amplitude) to a "
            f"MAGNITUDE of at least {IMON_TRIGGER_MIN_AMPLITUDE_UA:.1f} µA "
            f"(either polarity), or add a digital Trigger channel (which fires "
            f"on the sync line regardless of stim current — required to test "
            f"from 0 µA).")

    def apply_scope_settings_on_entry(self) -> None:
        """Apply the pattern-independent oscilloscope settings when the
        operator enters the Test parameters page (operator: "set all
        necessary oscilloscope settings when entering the Test parameters
        tab, including record length").

        Settings applied: RECORD LENGTH, acquisition mode + NUMAVg, and
        the trigger (source / slope / level / digital flag).  Pre-paying
        the record-length write here moves the 10-30 s TBS2000 buffer
        reallocation (gotcha #26) to tab entry — where the operator is
        still dialing in parameters — instead of after Start, AND means
        the scope on the bench reflects the GUI state immediately.
        Pattern-DEPENDENT layout (horizontal window, vertical default
        scales) stays at Start: it follows the pattern still being
        edited.

        Cheap on re-entry: the driver setters are idempotent
        (confirmed-value caches), so unchanged settings skip their SCPI
        writes entirely.  No-op when no scope is connected or a run is
        starting / in flight.  Never raises — a scope hiccup here must
        not break tab navigation; Start re-asserts everything anyway.
        """
        if self._scope is None:
            return
        if getattr(self, "_start_in_progress", False):
            return
        if (self._worker_thread is not None
                and self._worker_thread.isRunning()):
            return

        def _tick():
            try:
                QtWidgets.QApplication.processEvents()
            except Exception:
                pass

        _log = self.log_pane.log
        try:
            from ..hardware.tektronix import DEFAULT_RECORD_LENGTH
            _log("Scope pre-setup (Test parameters entry): record length "
                 f"= {DEFAULT_RECORD_LENGTH} (idempotent — skipped if "
                 f"already set)")
            _tick()
            self._scope.set_record_length(DEFAULT_RECORD_LENGTH)
            _tick()
            _log(f"Scope pre-setup: acquisition mode = {self._acq_mode}, "
                 f"n_avg = {self._acq_n_avg}")
            self._scope.set_acquisition_mode(self._acq_mode,
                                             n_avg=self._acq_n_avg)
            _tick()
            (_src, _slope, _level, _digital,
             _note) = self._resolve_trigger_settings()
            _log(f"Scope pre-setup: trigger {_note}")
            self._scope.set_trigger(
                source=_src, level_v=_level, slope=_slope,
                mode="NORMAL", digital=_digital)
            # Continuous shaped waveform → upgrade to a pulse-width-
            # qualified trigger when the scope supports it (no-op / edge
            # fallback otherwise).
            self._maybe_upgrade_trigger_to_pulse_width()
            _tick()
        except Exception as e:
            try:
                _log(f"Scope pre-setup failed (will retry at Start): "
                     f"{type(e).__name__}: {e}")
            except Exception:
                pass

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

    def _refresh_bias_visibility(self) -> None:
        """Show / hide the per-tab INTERSTELLAR closed-loop config panel.

        Two gates, in order:

        1. **INTERSTELLAR connected?**  The panel is HIDDEN until the
           operator connects INTERSTELLAR from the Setup tab (operator:
           "If INTERSTELLAR is not connected, then the interpulse bias
           option is hidden in the test parameters").  ``_bias_connected``
           mirrors the shared driver's state via the connect/disconnect
           signals wired in :meth:`set_bias_host`.

        2. **Monopolar config queued?**  Even when connected, the panel
           only shows when at least one MONOPOLAR (``id == "MP"``) config
           is selected.  The bias module drives a dedicated counter
           electrode, so it has nothing to do for BP / TP / CG / PBP /
           PTP configs (which return current through array electrodes) —
           offering the closed-loop option there would be misleading.

        No-ops safely when the panel was never built (feature disabled in
        the public build, or the ``combinationsChanged`` signal fired
        during initial ``set_array(...)`` before the panel existed).
        """
        panel = getattr(self, "_bias_feedback_panel", None)
        if panel is None:
            return
        # Gate 1 — hidden entirely until INTERSTELLAR is connected.
        if not getattr(self, "_bias_connected", False):
            panel.setVisible(False)
            return
        # Gate 2 — connected, so show only for monopolar configs.
        try:
            configs = self.combo_panel.selected_configurations()
        except Exception:
            configs = []
        has_monopolar = any(getattr(c, "id", "") == "MP" for c in configs)
        panel.setVisible(has_monopolar)

    # ----- internal ----
    def _wire_param_log(self, widget, label: str) -> None:
        """Connect ``widget``'s change signal to a ``paramChanged`` log line.

        One call per Test-parameters input (operator: "have all inputs and
        selections in test parameters show up in the log pane").  Dispatch
        by widget type:

        * spin boxes → ``editingFinished`` — the log line fires ONLY when
          the user COMMITS the edit by pressing Enter or clicking out
          (operator: "For numerical inputs, do not automatically print
          out the number, wait for the user to press enter or click out
          of it").  The logged value is the widget's rendered ``text()``
          (includes the " s" / " µA" suffix + thousands grouping).  A
          per-widget LAST-LOGGED-VALUE baseline suppresses repeats whose
          VALUE didn't change — Qt re-fires ``editingFinished`` when the
          spinbox merely re-renders its text (e.g. regrouping "20000" as
          "20 000" on focus-out), and that cosmetic reformat must not
          produce a second line (operator: "Do not print again just to
          separate 000 with spaces").  Programmatic changes (prefs
          restore, run-time setValue) move the baseline WITHOUT logging —
          they aren't user inputs.
        * combo boxes → ``currentTextChanged``, immediate.
        * radio buttons → ``toggled`` but only on CHECK (the paired
          radio's un-check is implied; logging both would double every
          switch).
        * check boxes / other buttons → ``toggled`` → ON / OFF, immediate.

        Best-effort: an unknown widget type is silently skipped rather
        than raising during tab construction.
        """
        try:
            if isinstance(widget, (QtWidgets.QDoubleSpinBox,
                                   QtWidgets.QSpinBox)):
                # Baseline = the value most recently logged (or arrived at
                # programmatically).  Only a COMMIT that lands on a
                # DIFFERENT value logs.
                widget._last_param_logged_value = widget.value()

                def _sync_baseline(*_a, w=widget):
                    # A valueChanged while the spinbox is NOT focused is
                    # programmatic (prefs restore, entry-sync, runner) —
                    # follow it silently so the next user commit compares
                    # against the true pre-edit value.  While focused,
                    # the change is the user's in-progress edit: leave
                    # the baseline at the pre-edit value.
                    if not w.hasFocus():
                        w._last_param_logged_value = w.value()

                def _log_commit(w=widget, lab=label):
                    val = w.value()
                    if getattr(w, "_last_param_logged_value", None) == val:
                        return   # reformat-only editingFinished — no line
                    w._last_param_logged_value = val
                    self._emit_param(f"{lab} = {w.text()}")

                widget.valueChanged.connect(_sync_baseline)
                widget.editingFinished.connect(_log_commit)
            elif isinstance(widget, QtWidgets.QComboBox):
                widget.currentTextChanged.connect(
                    lambda txt, lab=label: self._emit_param(f"{lab} = {txt}"))
            elif isinstance(widget, QtWidgets.QRadioButton):
                widget.toggled.connect(
                    lambda on, w=widget, lab=label:
                        self._emit_param(f"{lab} = {w.text()}")
                        if on else None)
            elif isinstance(widget, QtWidgets.QAbstractButton):
                widget.toggled.connect(
                    lambda on, lab=label: self._emit_param(
                        f"{lab} = {'ON' if on else 'OFF'}"))
        except Exception:
            pass

    #: Base-class Test-parameters inputs wired to the param log at
    #: ``_assemble_pages`` time — shared by every experiment tab.
    _BASE_PARAM_LOG_WIDGETS = (
        ("cam_snapshot_chk", "camera periodic snapshot"),
        ("cam_snapshot_interval", "camera snapshot interval"),
        ("cam_record_chk", "camera record video"),
        ("smoothing_chk", "waveform smoothing"),
        ("smoothing_window_spin", "smoothing window"),
    )

    #: Bias-feedback panel inputs (attributes on ``_bias_feedback_panel``).
    _BIAS_PARAM_LOG_WIDGETS = (
        ("enable_check", "bias feedback"),
        ("setpoint_spin", "bias setpoint"),
        ("tolerance_spin", "bias tolerance"),
        ("ki_spin", "bias integral gain"),
        ("vmon_sanity_spin", "bias V_mon sanity"),
        ("gate_lo_spin", "bias gate start"),
        ("gate_hi_spin", "bias gate end"),
    )

    #: Subclass hook — extra ``(attribute, log label)`` pairs for the
    #: tab's OWN Test-parameters widgets.  Wired (with the base + bias
    #: lists) at the end of ``_assemble_pages``, so every experiment's
    #: inputs are covered by declaring them here instead of hand-wiring.
    #: Pattern-panel inputs are NOT listed — they already log via the
    #: debounced "Setup: pattern =" block.
    PARAM_LOG_WIDGETS: tuple = ()

    def _wire_all_param_logs(self) -> None:
        """Wire every declared Test-parameters input to the param log.

        Called once at the end of ``_assemble_pages`` (each tab's final
        construction step, when both base-class AND subclass widgets
        exist).  Missing attributes are skipped silently so the shared
        lists stay valid across tabs that hide/omit a control.
        """
        for attr, label in (self._BASE_PARAM_LOG_WIDGETS
                            + tuple(self.PARAM_LOG_WIDGETS)):
            w = getattr(self, attr, None)
            if w is not None:
                self._wire_param_log(w, label)
        panel = getattr(self, "_bias_feedback_panel", None)
        if panel is not None:
            for attr, label in self._BIAS_PARAM_LOG_WIDGETS:
                w = getattr(panel, attr, None)
                if w is not None:
                    self._wire_param_log(w, label)

    def _emit_param(self, msg: str) -> None:
        """Emit a ready-to-log Test-parameters change line.

        Prefixed with ``LOG_TAG`` because every experiment tab carries
        same-named widgets (duration, max current, …) and the operator
        must be able to tell WHICH experiment's parameter changed.
        MainWindow routes the signal through ``_log_setup_change`` (the
        prefs-restore gate + the "Setup: " prefix)."""
        try:
            self.paramChanged.emit(f"{self.LOG_TAG}: {msg}")
        except Exception:
            pass

    def _log_combo_selection(self, configs: list) -> None:
        """Report channel/combo SELECTION and DE-SELECTION to the log.

        Diffs the enabled-configuration display names against the last
        reported set so the line says exactly what the click did
        ("selected CH05" / "deselected CH05"), plus the resulting total.
        The FIRST fire (construction / ``set_array`` baseline) only
        records the baseline — MainWindow connects ``paramChanged``
        after prefs restore anyway, so startup stays quiet either way.
        A genuine array/device change that clears the grid logs as a
        deselection, which is accurate (the selection WAS cleared).
        """
        try:
            names = [str(c.display_name()) for c in (configs or [])]
        except Exception:
            names = []
        prev = self._last_combo_names
        self._last_combo_names = list(names)
        if prev is None:
            return                        # baseline, not a user change
        added = [n for n in names if n not in prev]
        removed = [n for n in prev if n not in names]
        if not added and not removed:
            return
        parts = []
        if added:
            parts.append("selected " + ", ".join(added))
        if removed:
            parts.append("deselected " + ", ".join(removed))
        if not names:
            total = "none selected"
        elif len(names) <= 12:
            total = "now: " + ", ".join(names)
        else:
            total = f"now {len(names)} selected"
        self._emit_param(f"channel/combo {'; '.join(parts)}  ({total})")

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
        # Waveform-smoothing toggle — own key, missing-tolerant (same
        # convention as camera_capture above), so a prefs file predating
        # the feature won't choke.
        try:
            out["smoothing"] = {
                "enabled": bool(self.smoothing_chk.isChecked()),
                "window": int(self.smoothing_window_spin.value())}
        except Exception:
            pass
        # Per-tab bias-feedback state — setpoint / tolerance / gain /
        # V_mon sanity / gating window + Enable checkbox.  Same
        # safety as camera_capture above: own key, missing-tolerant.
        panel = getattr(self, "_bias_feedback_panel", None)
        if panel is not None:
            try:
                out["bias_feedback"] = panel.prefs_dict()
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
                elif isinstance(w, QtWidgets.QLineEdit):
                    w.setText(str(p[name]))
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
        if "smoothing" in p:
            try:
                self.smoothing_chk.setChecked(
                    bool(p["smoothing"].get("enabled", False)))
                _w = int(p["smoothing"].get("window", 5))
                if _w >= 2:
                    self.smoothing_window_spin.setValue(_w)
            except Exception:
                pass
        # Per-tab bias-feedback prefs — partial-restore via the panel's
        # own tolerance for missing / malformed keys.  Safe even when
        # the panel wasn't built (test stubs / pre-bias-feature prefs).
        panel = getattr(self, "_bias_feedback_panel", None)
        if "bias_feedback" in p and panel is not None:
            try:
                panel.restore_prefs(p["bias_feedback"])
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

    @staticmethod
    def _start_prompts_suppressed() -> bool:
        """True when the Start-time save-location dialogs must NOT pop.

        Bypassed under ``PULSAR_SKIP_OVERWRITE_PROMPT=1`` (explicit) OR a
        headless ``QT_QPA_PLATFORM=offscreen`` context (the whole test
        suite) — a blocking modal has no operator to answer it there and
        would hang the run.  Production GUIs use a real Qt platform, so the
        dialogs show normally."""
        return bool(os.environ.get("PULSAR_SKIP_OVERWRITE_PROMPT")
                    or os.environ.get("QT_QPA_PLATFORM") == "offscreen")

    def _apply_appended_save_name(self, new_save_name: str) -> None:
        """Re-point this tab's session stem + log file for the Start-time
        **Append to filename** path.

        The .npz / .xlsx / plot images follow the returned save name
        directly; this makes the session STEM and the LOG (``<stem>_log.txt``)
        follow it too, so the WHOLE run uses the fresh name and touches NO
        existing file.  Best-effort; never raises."""
        try:
            new_stem = Path(new_save_name).stem
            self._session_stem = new_stem
            # A multi-config SP/CP sweep caches its shared save name — keep it
            # in step so later configs write to the same appended name.
            if getattr(self, "_sp_save_name", None):
                self._sp_save_name = new_save_name
            lp = getattr(self, "log_pane", None)
            if lp is not None and lp.log_file_path() is not None:
                # Fresh name → append-open on a non-existent file (no replace,
                # no overwrite of the prior session's log).
                lp.set_log_file(Path(self._save_dir) / f"{new_stem}_log.txt")
        except Exception:
            pass

    def _confirm_save_location_before_start(self, save_name: str) -> "Optional[str]":
        """Gate Start on the save location — return the save name to USE, or
        ``None`` to cancel.  Two ordered checks (either can cancel):

        1. the save FOLDER doesn't exist yet → Create / Cancel;
        2. the run would REPLACE existing files → Continue / Append / Cancel.

        On **Append**, returns a fresh ``<stem>_N.npz`` and re-points the
        session stem + log to it.  On **Continue** / no collision, returns
        ``save_name`` unchanged.  (A non-existent folder has no files to
        replace, so in practice at most one dialog shows.)"""
        if not self._confirm_directory_before_start():
            return None
        resolved = self._confirm_overwrite_before_start(save_name)
        if resolved is None:
            return None
        if resolved != save_name:
            # APPEND: the operator chose a fresh, non-colliding name.  Re-point
            # the session stem + log so the whole run uses it and NO existing
            # file (data OR the prior log) is touched — so DON'T replace_log.
            self._apply_appended_save_name(resolved)
            return resolved
        # SAME name (Continue / no collision): if a prior session's LOG
        # pre-existed it was listed as a victim in the dialog just accepted —
        # REPLACE it now (truncate → fresh log) so "Continue" replaces the log
        # too, matching the .npz / .xlsx (operator: "ask if the user wants to
        # replace it").
        try:
            lp = getattr(self, "log_pane", None)
            if lp is not None and lp.log_file_preexisted():
                lp.replace_log_file()
        except Exception:
            pass
        return save_name

    def _confirm_directory_before_start(self) -> bool:
        """Return True to proceed, False to cancel.  When the save folder
        does NOT exist, pops a Create / Cancel confirmation (operator: "if
        the directory does not exist, put a pop up that the directory does
        not exist and ask the user to confirm creating or cancel") — so an
        accidental / mistyped path (e.g. a stray nested folder) is caught
        BEFORE the runner silently ``mkdir``s it.  Env
        ``PULSAR_SKIP_OVERWRITE_PROMPT=1`` bypasses (headless runs)."""
        try:
            d = Path(self._save_dir)
        except Exception:
            return True
        if d.exists():
            return True
        if self._start_prompts_suppressed():
            return True
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Save folder does not exist")
        box.setText("The save folder does <b>not exist</b> yet.")
        box.setInformativeText(
            f"<b>Folder:</b> {d}<br><br>"
            "Click <b>Create</b> to create it and continue, or <b>Cancel</b> "
            "to go back and change the save location.")
        create = box.addButton("Create",
                              QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Cancel", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(box.buttons()[-1])   # safe default = Cancel
        box.exec()
        return box.clickedButton() is create

    def _files_that_would_be_replaced(self, save_name: str) -> "list[Path]":
        """Existing files in the save folder that STARTING would overwrite.

        The run writes ``<stem>.npz`` (always — crash-recovery artifact) +
        ``<stem>.xlsx`` (Gamry export) at the session level and per-channel
        plot images ``<stem>_CH*.<fmt>``.  Returns the subset that already
        exist so the caller can warn before clobbering a previous run's
        data.  Never raises — a check failure returns ``[]`` (proceed) so a
        transient filesystem hiccup can't block Start.
        """
        out: "list[Path]" = []
        try:
            base = Path(self._save_dir) / save_name          # <stem>.npz
            stem = base.stem
            d = base.parent
            for cand in (base, base.with_suffix(".xlsx")):
                if cand.exists():
                    out.append(cand)
            if d.is_dir():
                for pat in (f"{stem}_*.tif", f"{stem}_*.png",
                            f"{stem}_*.svg", f"{stem}_*.jpg"):
                    out.extend(sorted(d.glob(pat)))
        except Exception:
            return []
        # The session LOG (.txt) is included when it PRE-EXISTED (a prior
        # session's log with the same dir + filename) — operator: "even when
        # writing to the log file should ask if the user wants to replace it if
        # the directory and filename are the same".  The LogPane appends to it
        # continuously (so it always exists by run start); ``log_file_
        # preexisted()`` tells us it had PRIOR content, which is what makes it a
        # genuine overwrite victim (this session's own appends don't count).
        try:
            lp = getattr(self, "log_pane", None)
            logp = lp.log_file_path() if lp is not None else None
            if (logp is not None and lp.log_file_preexisted()
                    and logp.exists()
                    # Only a victim when it lives in THIS run's save folder —
                    # the run's log is ``<save_dir>/<stem>_log.txt``; a log
                    # pointed elsewhere isn't touched by this Start.
                    and Path(logp).parent == Path(self._save_dir)
                    and logp not in out):
                out.append(logp)
        except Exception:
            pass
        return out

    def _unique_save_name(self, save_name: str) -> str:
        """Return a ``<stem>_N.npz`` that collides with NO existing session
        file in the save folder.

        Checks the data ``.npz`` / ``.xlsx``, the per-channel plot images
        (``<stem>_CH*.<fmt>``), AND a same-named ``<stem>_log.txt`` — the same
        set a run writes — so the returned name is genuinely free.  ``N``
        starts at 2 and counts up to the first unused name.  Used by the
        Start-time overwrite prompt's **Append to filename** option so a
        re-run saves ALONGSIDE the existing data instead of clobbering it.
        Falls back to the original name (the caller then overwrites) in the
        astronomically unlikely event ``_2`` … ``_999`` are all taken; never
        raises."""
        try:
            d = Path(self._save_dir)
            base = Path(save_name)
            stem0 = base.stem
            suffix = base.suffix or ".npz"

            def _collides(stem: str) -> bool:
                if (d / f"{stem}{suffix}").exists():
                    return True
                if (d / f"{stem}.xlsx").exists():
                    return True
                if (d / f"{stem}_log.txt").exists():
                    return True
                for pat in (f"{stem}_*.tif", f"{stem}_*.png",
                            f"{stem}_*.svg", f"{stem}_*.jpg"):
                    if any(d.glob(pat)):
                        return True
                return False

            for n in range(2, 1000):
                cand = f"{stem0}_{n}"
                if not _collides(cand):
                    return f"{cand}{suffix}"
        except Exception:
            pass
        return save_name

    def _confirm_overwrite_before_start(self, save_name: str) -> "Optional[str]":
        """Resolve a save-name collision at Start — returns the save name to
        USE, or ``None`` to cancel.

        * No dialog (returns ``save_name`` unchanged) when nothing would be
          replaced — the common fresh-folder / new-filename case — or when
          prompts are suppressed (``PULSAR_SKIP_OVERWRITE_PROMPT=1``).
        * When existing session files WOULD be overwritten, pops a modal
          warning with three choices:
            - **Continue (overwrite)** → returns ``save_name`` (clobber);
            - **Append to filename** → returns a fresh, non-colliding
              ``<stem>_N.npz`` (operator: "give the option to append
              filenames if files of the same name already exist") so the run
              saves ALONGSIDE the existing files instead of replacing them;
            - **Cancel** → returns ``None`` (go back, change the inputs)."""
        victims = self._files_that_would_be_replaced(save_name)
        if not victims:
            return save_name
        if self._start_prompts_suppressed():
            return save_name
        folder = str(Path(self._save_dir))
        shown = victims[:8]
        lines = "<br>".join("• " + v.name for v in shown)
        if len(victims) > len(shown):
            lines += f"<br>• … and {len(victims) - len(shown)} more"
        suggested = self._unique_save_name(save_name)
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Replace existing files?")
        box.setText("Starting this run will <b>replace files that already "
                    "exist</b> in the save folder.")
        box.setInformativeText(
            f"<b>Folder:</b> {folder}<br><br>"
            f"<b>Will be replaced ({len(victims)}):</b><br>{lines}<br><br>"
            "Click <b>Continue</b> to overwrite them, "
            f"<b>Append to filename</b> to save as <b>{Path(suggested).name}"
            "</b> instead (keeping the existing files), or <b>Cancel</b> to "
            "go back and change the save location / filename.")
        cont = box.addButton("Continue (overwrite)",
                             QtWidgets.QMessageBox.ButtonRole.DestructiveRole)
        appnd = box.addButton("Append to filename",
                              QtWidgets.QMessageBox.ButtonRole.AcceptRole)
        canc = box.addButton("Cancel",
                             QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(canc)          # safe default = don't proceed
        box.exec()
        clicked = box.clickedButton()
        if clicked is cont:
            return save_name
        if clicked is appnd:
            return self._unique_save_name(save_name)
        return None                         # Cancel / dialog closed

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
        # ---- I_mon-trigger minimum-amplitude guard -----------------
        # (operator: "If Imon is the trigger source, require [the phase-1
        # amplitude] >= |0.1| uA" — any shape).  Below that the I_mon signal
        # is untriggerable → stale / misaligned captures.  Cheapest check,
        # BEFORE the overwrite prompt / any hardware setup: a hard BLOCK (the
        # operator must raise the amplitude or add a digital Trigger channel;
        # a VT-max from 0 µA needs a digital trigger).  Rolls back cleanly.
        _trig_amp_err = self._imon_trigger_amplitude_error()
        if _trig_amp_err is not None and not self._start_prompts_suppressed():
            try:
                self.log_pane.log("Start blocked — "
                                  + _trig_amp_err.split("\n")[0])
            except Exception:
                pass
            try:
                from PyQt6.QtWidgets import QMessageBox
                QMessageBox.warning(
                    self, "I_mon trigger — amplitude too low", _trig_amp_err)
            except Exception:
                pass
            self._start_in_progress = False
            try:
                self.start_btn.setEnabled(True)
            except Exception:
                pass
            return
        if _trig_amp_err is not None:
            # Suppressed (headless / test) — log + proceed rather than wedge a
            # non-interactive run; the pure guard method is tested directly.
            try:
                self.log_pane.log("⚠ " + _trig_amp_err.split("\n")[0]
                                  + "  (guard bypassed: headless)")
            except Exception:
                pass
        # ---- Overwrite warning -------------------------------------
        # If this run would REPLACE session files already on disk (same
        # save folder + filename as a previous run — the operator hit this
        # by re-running without changing the directory / filename), warn
        # FIRST with Continue / Cancel (operator: "pop-up warning if files
        # in a folder will be replaced … Continue if the user wants to
        # replace the files.  Cancel if the user wants to revert back to
        # the original inputs so that the user can change stuff").  Shown
        # AFTER ``_start_in_progress`` is set (the re-entry guard must
        # cover the modal) but BEFORE any hardware setup, so Cancel is a
        # clean no-op that leaves the operator's inputs untouched.
        _resolved_name = self._confirm_save_location_before_start(save_name)
        if _resolved_name is None:
            self._start_in_progress = False
            try:
                self.start_btn.setEnabled(True)
            except Exception:
                pass
            return
        # May have been renamed to a unique ``<stem>_N.npz`` (the operator
        # chose "Append to filename"); the whole run body uses it.
        save_name = _resolved_name
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

    def _reinit_stim_if_closed(self) -> bool:
        """Re-initialize the SHARED stimulator on Start when it reports
        closed OR when the previous run was STOPPED.

        Two triggers:

        1. **Device reports closed** (``is_open`` is False) — the SOURCE
           OF TRUTH, NOT the per-tab flag.  A Stop/abort in a DIFFERENT
           experiment tab closes the shared stimulator but only sets THAT
           tab's flag, so keying on ``is_open`` makes ANY tab's Start
           re-open a device any other tab closed.
        2. **Previous run was Stopped** (``_stim_needs_init`` or
           ``_stim_needs_close_after_run`` set) — even if the device
           still reports OPEN (operator: "Reinitialize the stimulator
           when pressing Start if the experiment has been Stopped").
           This covers the cases the is_open check alone missed: the
           Stop's deferred ``close()`` hasn't run yet (Start raced
           ``_on_finished``) or it failed silently, leaving a stale
           ``is_open == True``.  When the device is still open we
           ``close()`` it FIRST so the ``open()`` is a clean reinit; the
           driver's ``_is_open`` guard (gotcha #29c) means that's a
           single ``PS_CloseAllStim`` + ``PS_InitAllStim``, not the
           cascade.

        A NORMAL end-of-run sets NEITHER flag, so back-to-back normal
        runs skip re-init (the device stays open) — only a Stop forces it.

        Returns True if a re-init was performed.  Raises ``RuntimeError``
        on a genuine init failure (the caller's wrapper rolls back the
        UI and surfaces the error to the operator).
        """
        _stim_obj = getattr(self, "_stim", None)
        try:
            _stim_is_open = (bool(_stim_obj.is_open)
                             if _stim_obj is not None else None)
        except Exception:
            _stim_is_open = None
        _was_stopped = (bool(getattr(self, "_stim_needs_init", False))
                        or bool(getattr(self, "_stim_needs_close_after_run",
                                        False)))
        _needs_init = ((_stim_is_open is False) or _was_stopped
                       or (_stim_is_open is None
                           and getattr(self, "_stim_needs_init", False)))
        if not _needs_init:
            # Already open and the last run ended normally — clear any
            # stale per-tab flag so a later Start doesn't redundantly
            # re-open an open device (which would re-trip the close
            # cascade, gotcha #29c).
            self._stim_needs_init = False
            return False
        _reason = ("a prior Stop/abort"
                   if _was_stopped else
                   "device reports closed — possibly a Stop in another "
                   "experiment tab")
        self.log_pane.log_now(
            f"Re-initializing stimulator for new run ({_reason})…")
        try:
            if _stim_obj is None:
                # Operator never initialized it via ConnectionPanel.
                raise RuntimeError(
                    "No stimulator object available — open the Setup "
                    "tab → Connection panel → Initialize stimulator "
                    "before pressing Start.")
            # If the device still reports OPEN after a Stop, close it
            # first so open() lands on a quiet device (single
            # close+init, per the _is_open guard).
            if _stim_is_open:
                try:
                    _stim_obj.close()
                except Exception:
                    pass
            _stim_obj.open()
            _serial = ""
            try:
                _serial = str(getattr(_stim_obj.info,
                                      "serial_number", "") or "")
            except Exception:
                pass
            self.log_pane.log_now(
                f"Stimulator re-initialized (PS_InitAllStim, S/N "
                f"{_serial or 'unknown'}).")
            self._stim_needs_init = False
            self._stim_needs_close_after_run = False
        except Exception as _stim_init_err:
            raise RuntimeError(
                f"Stimulator re-init failed: "
                f"{type(_stim_init_err).__name__}: {_stim_init_err}.  "
                f"Power-cycle the stimulator + re-Initialize from the "
                f"Connection panel.") from _stim_init_err
        return True

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
        # Install the device→Plexon cable translation (operator: "when pulsing
        # CH01, it should be device CH01" — MATLAB Channels.Plexon).  Read the
        # non-identity map from the setup snapshot; empty / identity ⇒ the
        # runner never wraps its stim, so this is a no-op for the test board +
        # every standard cable.  A bad map must never block Start.
        try:
            _snap = (self._setup_snapshot_provider()
                     if self._setup_snapshot_provider is not None else None) or {}
            _cm = {int(k): int(v)
                   for k, v in (_snap.get("channel_map") or {}).items()}
            runner.set_channel_map(_cm)
            # Log the translation so the operator can VERIFY which Plexon stim
            # channel each selected DEVICE channel actually commands (operator:
            # "are you applying the correct Plexon channel to correspond with
            # the correct device channel?").  Arrow-free per the log-pane
            # convention; a failure is logged (not silently swallowed → the
            # runner would then use identity without the operator knowing).
            if _cm:
                _pairs = ", ".join(f"{d}:{p}" for d, p in sorted(_cm.items()))
                self.log_pane.log(
                    f"Cable translation ACTIVE — device:Plexon = {_pairs} "
                    f"(a pulse on device channel N commands Plexon channel M).")
            else:
                self.log_pane.log(
                    "Cable translation: identity — device channel = Plexon "
                    "channel (no remap).")
        except Exception as _cm_err:
            self.log_pane.log(
                f"⚠ Cable translation could NOT be applied: "
                f"{type(_cm_err).__name__}: {_cm_err} — falling back to "
                f"identity (device channel = Plexon channel).")
        # ---- User-spec: re-init the stim if a prior Stop closed it
        # Quote: "When you restart, initialize the stimulator."  We init
        # HERE — before the scope setup — so the runner takes a fully-
        # init'd stim and a failure aborts Start cleanly via the
        # wrapper's except clause.  No-op on the normal end-of-run →
        # next-Start path (device still open).  See
        # :meth:`_reinit_stim_if_closed` for why this keys on the shared
        # device's ``is_open`` rather than the per-tab flag.
        self._reinit_stim_if_closed()
        # ---- KHFAC discharge-as-short: (re-)enable the PlexStim auto-discharge
        # so the trailing discharge idle is a REAL passive short (charge drains
        # each cycle), not a floating 0-µA step.  Gated on the runner's pattern
        # carrying ``interpulse_discharge_us > 0`` (operator's toggle, which
        # itself requires Discharge Mode on).  No period change — build_pat_pairs
        # skipped the discharge 0-µA pair so the device idles + shorts it.
        # Best-effort — a failure logs but never blocks Start (the run still
        # delivers the sinusoid; only the short is lost).
        try:
            _pat = getattr(getattr(runner, "session", None), "test", None)
            _ipd = float(getattr(getattr(_pat, "pattern", None),
                                 "interpulse_discharge_us", 0.0) or 0.0)
            if _ipd > 0 and self._stim is not None \
                    and hasattr(self._stim, "set_auto_discharge"):
                self._stim.set_auto_discharge(True)
                self.log_pane.log(
                    f"KHFAC interpulse discharge: auto-discharge ENABLED — the "
                    f"{_ipd:g} µs interpulse gap passively shorts the electrode "
                    f"each cycle (charge recovery / DC mitigation).")
        except Exception as _ipd_err:
            self.log_pane.log(
                f"⚠ Could not enable auto-discharge for the interpulse "
                f"discharge: {type(_ipd_err).__name__}: {_ipd_err}.")
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
            # Push per-channel bandwidth / coupling OVERRIDES (the Setup-tab
            # dropdowns in line with the role) to the scope BEFORE channel
            # config, so ``_coupling_for`` (inside configure_channels) and
            # ``_apply_channel_bandwidths`` honour them.  Only concrete
            # (non-"Auto") choices override; "Auto" leaves the automatic
            # policy (full BW on data / 20 MHz on trigger; role-based coupling).
            try:
                _snap_scope = (self._setup_snapshot_provider()
                               if self._setup_snapshot_provider is not None
                               else None) or {}
                if hasattr(self._scope, "set_channel_coupling_overrides"):
                    self._scope.set_channel_coupling_overrides(
                        _snap_scope.get("channel_couplings") or {})
                if hasattr(self._scope, "set_channel_bandwidth_overrides"):
                    self._scope.set_channel_bandwidth_overrides(
                        _snap_scope.get("channel_bandwidths") or {})
            except Exception:
                pass
            # Turn OFF every channel first, then ON only the ones that are
            # actually mapped — same discipline as the calibration sweep.
            # Channel enable/disable is the scope driver's job —
            # ``configure_channels`` walks 1..n_channels and writes
            # ``SELect:CHx ON`` for mapped channels, ``OFF`` for
            # unused ones, then applies per-channel defaults.  The
            # GUI used to run its own OFF→ON pass here before
            # ``configure_channels`` did so itself, which produced
            # ~80 ms of redundant SCPI traffic on every connect
            # (LOG_ANALYSIS.md finding #10).  Removed in favor of
            # the single-source-of-truth call below.
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
            # Per-channel bandwidth: DATA channels run at FULL bandwidth
            # (operator preference), but the TRIGGER channel is limited to
            # 20 MHz for a clean comparator.  Why the split (CWRU bench):
            # the ``imon_trigger_level`` formula was tuned for the
            # 20 MHz-BAND-LIMITED I_mon peak (CLAUDE.md gotcha #6).  With
            # I_mon at FULL BW on a 2-channel scope — where I_mon IS the
            # trigger — the broadband peak no longer clears the threshold
            # (observed level -11.5 mV vs an actual -8 mV peak), so the scope
            # barely triggers → garbage captures → a degenerate verification
            # gain (gotcha #161).  Limiting ONLY the trigger channel restores
            # a reliable comparator while every data-only channel keeps full
            # BW.  On a 4-channel scope the trigger is a separate digital-sync
            # channel, so I_mon stays full-BW there.
            try:
                _bw_results = _apply_channel_bandwidths(
                    self._scope, self._aliases)
                if _bw_results:
                    _parts = ", ".join(
                        f"{_c} ({_a}) = "
                        + ("FULL" if _b == float("inf") else f"{_b:.0f} MHz")
                        + ("  [trigger]" if _k == "trigger" else "")
                        for _a, _c, _b, _k in _bw_results)
                    _log(f"Scope setup: bandwidth — {_parts} "
                         f"(full BW on data channels; trigger channel "
                         f"band-limited for a clean comparator).")
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
                # Trigger setup — resolution shared with the Test-
                # parameters entry pre-apply via
                # ``_resolve_trigger_settings`` (single source of truth
                # for the three trigger paths; see its docstring for the
                # EXT / channel-Trigger / I_mon priority rules).
                (_trig_src, slope_resolved, trig_level, _trig_digital,
                 _trig_note) = self._resolve_trigger_settings()
                _log(f"Scope setup: trigger {_trig_note}")
                _tick()
                self._scope.set_trigger(
                    source=_trig_src,
                    level_v=trig_level,
                    slope=slope_resolved,
                    mode="NORMAL",
                    # The 1.2 µs Plexon digital-sync offset applies for
                    # EXT BNC AND for any channel tagged Role=Trigger
                    # in the Setup tab (same TTL wire, different
                    # physical input).  Forward the Setup-tab flag so
                    # the driver shifts the time axis + horizontal
                    # layout for either path.
                    digital=_trig_digital,
                )
                # Continuous shaped waveform → upgrade to a pulse-width-
                # qualified trigger when the scope supports it (no-op /
                # edge fallback otherwise).
                self._maybe_upgrade_trigger_to_pulse_width()
                _tick()
                # On an LP resume the live pattern panel may not reflect
                # the saved run's pattern (it can't be cheaply rebuilt
                # from a stored PulsePattern), so frame the horizontal
                # window from the loaded pattern when one was injected.
                pat = (self._resume_pattern_override
                       if self._resume_pattern_override is not None
                       else self.pattern_panel.pattern())
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
            auto_save_plots_dpi=self._auto_save_plots_dpi,
            email_notifications=self._email_notifications,
            user_email=self._user_email,
            user_name=self._user_name,
            session_subject=self._session_subject,
            sms_phone=self._sms_phone,
            sms_carrier=self._sms_carrier)
        # Seed the background export worker with the run ids already
        # written by EARLIER configs in this shared-session sweep
        # (SP / CP accumulate every config into one growing Session —
        # see ShortPulsingTab._start_next_pending).  Without this, each
        # successive worker's end-of-run back-fill would see the prior
        # channels' runs as "not yet exported" and re-render every one
        # of them (and log a misleading "Back-filling N plot(s)").
        # Empty for the first config and for single-config experiments,
        # so it's a no-op everywhere except the 2nd+ config of a sweep.
        # Seeded BEFORE the worker thread starts, so the export thread
        # isn't yet touching the set.
        try:
            self._worker._export.exported_run_ids.update(
                getattr(self, "_export_carryover_ids", set()) or set())
        except Exception:
            pass
        self._worker_thread = QtCore.QThread(self)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.captured.connect(self._on_capture)
        self._worker.run_completed.connect(self._on_run_completed)
        self._worker.log_msg.connect(self.log_pane.log)
        # Closed-loop bias-feedback status badge — parse log lines
        # with the ``[bias-step]`` prefix into BiasFeedbackStep-like
        # state and push to the panel's status badge.  Keeps the
        # GUI wire format flexible while a dedicated event kind
        # gets designed (#43 follow-up).  No-op when no panel exists
        # or the line doesn't match the prefix.
        self._worker.log_msg.connect(self._on_bias_step_log)
        self._worker.save_failed.connect(self._on_save_failed)
        self._worker.finished.connect(self._on_finished)
        # Between-channels rewire prompt — the runner emits "paused" before
        # each non-first config when the tab's pause toggle is on.  The slot
        # pops a modal QMessageBox; clicking OK calls request_continue().
        self._worker.paused.connect(self._on_runner_paused)
        # Progress events drive the status-bar step / elapsed / ETA
        # indicator (Task #56).  No-op when the runner doesn't emit
        # progress; just keeps the slot disconnected from the
        # captured-event spam.
        self._worker.progress.connect(self._on_runner_progress)
        self._runner = runner
        # Closed-loop bias-feedback wiring (#43).  Construct the
        # controller from the BiasFeedbackPanel's current state +
        # the shared bias driver; push onto the runner BEFORE the
        # worker starts so arm_bias_feedback() (called in
        # short_pulsing.run() and friends) sees a controller to arm.
        # No-op when the panel's master Enable is off OR no bias
        # driver is open OR the panel isn't built (test stubs).  The
        # log_pane gets a one-line "[bias] feedback enabled / skipped"
        # message either way so the operator's intent is visible in
        # the session log.
        self._attach_bias_controller_to_runner(runner)
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
        # Push the reference-electrode short name so the scope's "Potential
        # axis" option can label the left axis "Potential vs <ref> [V]" when
        # only E_act / E_ret are shown (operator).  Sourced from the Setup
        # snapshot; falls back to Ag|AgCl (the catalog convention).
        try:
            _snap = (self._setup_snapshot_provider()
                     if self._setup_snapshot_provider is not None else None) or {}
            self.multichan_scope.set_reference_label(
                _snap.get("reference_electrode_short")
                or _snap.get("reference_electrode_label") or "Ag|AgCl")
            # Return / counter electrode for the "Voltage vs <return> [V]"
            # option — the Setup return-electrode coating (falls back to Pt).
            self.multichan_scope.set_return_label(
                _snap.get("return_coating_short")
                or _snap.get("return_coating_label") or "Pt")
            # A device with NO electrodes (the Plexon Test Board) has no
            # reference / return electrode, so the voltage axis stays plain
            # "Voltage [V]" — never "Potential vs <ref>" / "Voltage vs <return>"
            # (operator: "If the Test Board is connected, the unit for the
            # voltage channels can only be Voltage [V]").
            self.multichan_scope.set_reference_aware_labels(
                bool(_snap.get("has_electrodes", True)))
        except Exception:
            pass
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
        # Run-progress bar (MATLAB updateWaitbar.m): elapsed time +
        # pulse count.  SP / CP / LP carry a duration policy so the bar
        # shows (fractional for bounded SP/LP, busy for unbounded CP);
        # VT / PS have no fixed duration, so ``duration_s`` resolves to
        # None and the bar stays hidden (they use the status-bar channel
        # progress instead).  Pulse rate comes from the active pattern.
        try:
            _pat = runner.session.test.pattern
            # OVERALL pulse rate for the delivered-pulse count — a burst
            # delivers pulses_per_burst pulses per burst period, so the raw
            # intra-burst rate_hz over-counts.  == rate_hz for a non-burst
            # pattern (the ``effective_pulse_rate_hz`` property handles both).
            _rate = float(getattr(_pat, "effective_pulse_rate_hz",
                                  None) or _pat.rate_hz)
            _dur = getattr(getattr(runner, "policy", None),
                           "duration_s", None)
            # VT (adaptive amplitude ramp, no fixed duration) exposes a list
            # of configurations — drive a CHANNEL-STEP bar over them.  Other
            # ramp runners (PS) have no ``configurations`` → total 0 → the
            # step bar isn't shown (they keep the status-bar progress).
            _total_ch = len(getattr(runner, "configurations", None) or [])
            self.begin_run_progress(duration_s=_dur, rate_hz=_rate,
                                    total_steps=_total_ch)
        except Exception:
            self._end_run_progress()
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

    @QtCore.pyqtSlot(object, object)
    def _on_capture(self, capture, channel):
        # Route the capture into the per-entry list/stack. The list
        # key is the **configuration display name** (e.g. ``"CH05"``
        # for monopolar, ``"CH05 v 06"`` for bipolar) so two combos
        # that share an active channel produce distinct list rows
        # — the previous int-keyed path collapsed them into one. We
        # read the display name from the live runner; if no runner
        # is present (race / late capture) fall back to the legacy
        # int channel key so the capture still lands somewhere.
        # Worker-computed display-name key (see RunnerWorker.captured);
        # legacy int channels still resolve via _capture_key.
        key = (channel if isinstance(channel, str)
               else self._capture_key(channel))
        # Isolate the scope render: a scope-side failure must NOT starve the
        # Tracking feed below (it did for LP — a trimmed snapshot crashed the
        # scope render, and the swallowed exception meant the tracking plot
        # never got the sample).  Each consumer is independent.
        try:
            self.multichan_scope.add_capture(capture, key)
        except Exception:
            pass
        # NOTE: the metrics table is synced INSIDE add_capture via
        # ``captureChanged`` — but ONLY for the capture's page when it is
        # the one on screen.  We deliberately do NOT push the raw
        # ``capture`` here: in a multi-config sweep that overwrote the
        # table with a non-visible channel's live capture while the user
        # viewed a pinned, completed channel (operator: "plot values not
        # matching the table values").  captureChanged is the single
        # source of truth so the table can never show a different capture
        # than the plot.
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

    @QtCore.pyqtSlot(object)
    def _on_run_completed(self, key):
        """Tick a channel/combo's entry ✓ as soon as its config finishes.

        Fired by the worker's ``run_completed`` signal on each ``run_end``
        — so a multi-config sweep marks each row LIVE as it completes,
        rather than all-at-once at the end of the run (operator: "I do not
        see check marks by the channel/combo to indicate that they are
        complete").  The key matches the ``captured``/`_on_capture` key
        (``str(display_name())``, or the legacy int channel), so it lands
        on the correct already-existing row.  ``mark_completed`` is
        idempotent, so the end-of-run pass in ``_on_finished`` re-marking
        the same keys is harmless.
        """
        try:
            self.multichan_scope.mark_completed(key)
        except Exception:
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

    @QtCore.pyqtSlot(object)
    def _on_runner_progress(self, prog):
        """Update the main window's status bar with step / elapsed /
        ETA so long sweeps don't look frozen.

        ``prog`` is a :class:`stimtest.experiments.base.ProgressInfo`
        (passed through as ``object`` over the Qt signal).  Format:
        ``"VT 7/16 channels | elapsed 1:47 | ETA 3:24"``.  ETA is
        suppressed when ``started_at`` is 0 (runner opted out of
        time-tracking) or when only one step has completed (no
        rate estimate yet).
        """
        import time as _time

        def _fmt_dur(seconds: float) -> str:
            seconds = max(0.0, float(seconds))
            mins, secs = divmod(int(round(seconds)), 60)
            hrs, mins = divmod(mins, 60)
            return (f"{hrs:d}:{mins:02d}:{secs:02d}" if hrs
                    else f"{mins:d}:{secs:02d}")

        try:
            label = (prog.label or "").strip()
            step = max(int(prog.step), 0)
            total = max(int(prog.total), 1)
            started = float(prog.started_at)
        except Exception:
            return  # malformed progress event — ignore rather than crash

        parts = []
        if label:
            parts.append(label)
        parts.append(f"step {step}/{total}")
        if started > 0:
            elapsed = _time.monotonic() - started
            parts.append(f"elapsed {_fmt_dur(elapsed)}")
            # ETA only after we've seen at least one step complete —
            # otherwise the per-step time estimate is undefined.
            if step >= 1 and step <= total:
                avg = elapsed / step
                remaining = (total - step) * avg
                parts.append(f"ETA {_fmt_dur(remaining)}")
        msg = " | ".join(parts)
        # Show in the MainWindow's status bar with a long timeout so
        # the message persists between progress updates (a 0 timeout
        # would let other showMessage calls overwrite it; default Qt
        # behavior).  10-second timeout = the next progress event
        # arrives well before this expires under any normal cadence.
        try:
            self.window().statusBar().showMessage(msg, 10_000)
        except Exception:
            pass
        # Channel-step run-progress bar (VT): fill by channels/combos
        # COMPLETED — the current channel ``step`` is in progress, so
        # ``step-1`` are done.  The label carries the current channel; the
        # 1 s timer ticks the elapsed readout between per-channel events.
        if getattr(self, "_run_progress_step_mode", False):
            try:
                tot = max(1, int(getattr(self, "_run_progress_total_steps", 0)
                                 or total))
                done = max(0, min(step - 1, tot))
                self.run_progress_bar.setValue(int(round(done / tot * 100)))
                self.run_progress_bar.setFormat(f"{done} / {tot} channels")
                self._run_progress_step_label = (
                    f"{label}   ·   channel {step} / {tot}" if label
                    else f"channel {step} / {tot}")
                self._tick_run_progress()      # render label + elapsed now
            except Exception:
                pass

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
        # Stop + hide the run-progress bar.  For a multi-config SP/CP
        # sweep the NEXT config's ``begin_run_progress`` re-shows it;
        # on the final config (or any single-config run) it stays
        # hidden until the next Start.
        self._end_run_progress()
        # Disarm camera capture FIRST so a stray snapshot timer
        # can't fire after the run has logically ended.  Safe to
        # call when nothing was armed.
        try:
            self._disarm_camera_after_run()
        except Exception:
            pass
        # (No "Experiment finished. Captures: N." line — operator asked to
        # drop it; the "Experiment completed: X.YZ min" elapsed line below is
        # the end-of-run marker.)
        # MATLAB convention (PlexStimTek.m: "Experiment completed: X.YZ unit").
        # ``toc`` auto-scales the unit through ``LogPane.format_scaled``,
        # mirroring ``getEndTime.m``. Logs a no-op nan if the tic was
        # cleared by an intervening ``reset_clock`` (e.g. user re-Started).
        # Capture the elapsed seconds (toc returns it) for the completion
        # pop-up below (operator: "put the time elapsed in the completion
        # pop up").  NaN if the tic was cleared by an intervening restart.
        self._last_run_elapsed_s = self.log_pane.toc(
            "experiment", label="Experiment")
        # Wall-clock END banner in the .txt log (operator: "be sure that the
        # log file has the date and time of … ended").  Pairs with the
        # "PULSAR session log created …" header + the "Session started …"
        # banner.  Best-effort (a test stub may lack the method).
        try:
            self.log_pane.mark_session_ended()
        except Exception:
            pass
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
            # Hardware-disconnect detection (Task #55): the worker
            # marks disconnect-shaped errors with the canonical
            # ``[DISCONNECT:scope]`` / ``[DISCONNECT:stim]`` prefix.
            # Surface a tailored dialog instead of letting the
            # generic abort path silently roll forward — the
            # operator deserves to know WHICH device went away and
            # WHAT their next step is.  Prior captures are already
            # safely on disk via the per-capture incremental save
            # (Task #54); we explicitly tell the operator that here
            # so they don't panic.  We do NOT attempt automatic
            # reconnect-and-resume — stim/scope state restoration
            # mid-run is fragile (loaded patterns / repetition
            # counts / scope V/div + trigger settings vary by model
            # and reset differently on reconnect).  Manual
            # reconnect via the Connection Panel + restart is the
            # safe option.
            err_str = (getattr(result, "error", "") or "")
            if err_str.startswith("[DISCONNECT:"):
                _close_bracket = err_str.find("]")
                _device = err_str[len("[DISCONNECT:"):_close_bracket] \
                    if _close_bracket > 0 else "device"
                _device_label = (
                    "Scope (Tektronix)" if _device == "scope"
                    else "Stimulator (PlexStim)" if _device == "stim"
                    else _device)
                _save_status = (
                    f"\n\nPrior captures from this run have been "
                    f"saved incrementally and are available at:\n"
                    f"  {self.save_path}\n"
                    f"(marked 'incomplete' in metadata — POLARIS "
                    f"will surface a badge so you know it's a "
                    f"partial run.)"
                    if self.save_path is not None
                    else "")
                QtWidgets.QMessageBox.warning(
                    self,
                    f"{_device_label} disconnected mid-run",
                    f"PULSAR detected that the {_device_label} "
                    f"disconnected during the run.  The run has "
                    f"been aborted to prevent further errors."
                    f"{_save_status}"
                    f"\n\nWhat to do:\n"
                    f"  1. Re-plug the {_device_label} USB cable "
                    f"(or power-cycle the device).\n"
                    f"  2. Click Disconnect → Connect in the "
                    f"Setup tab's Connection Panel to re-open the "
                    f"connection.\n"
                    f"  3. Click Start again to begin a new run "
                    f"from where you left off."
                )
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
            # Carry the set of run ids this worker already exported into
            # the NEXT config's worker (shared-session SP/CP), so its
            # back-fill skips channels we've saved.  The export worker
            # was drained before ``finished`` fired, so the set is final.
            try:
                self._export_carryover_ids = (
                    getattr(self, "_export_carryover_ids", set())
                    | set(self._worker._export.exported_run_ids))
            except Exception:
                pass
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
            # Operator: "I want it to say experiment finished as a pop up."
            # Shown ONLY on genuine completion — NOT a user Stop / abort /
            # mid-run disconnect (those already surface their own dialogs)
            # and NOT mid-chain (this ``else`` is the true end, after the
            # last configuration).  NON-MODAL (``show()``, not the blocking
            # ``QMessageBox.information``) so it doesn't freeze the GUI at run
            # end AND can't hang a headless test that drives a run to
            # completion; a kept ref + WA_DeleteOnClose manages its lifetime.
            if not getattr(result, "aborted", False):
                try:
                    _el = getattr(self, "_last_run_elapsed_s", float("nan"))
                    _msg = ("Experiment finished in "
                            f"{fmt_elapsed(_el)}."
                            if isinstance(_el, (int, float))
                            and math.isfinite(_el)
                            else "Experiment finished.")
                    _mb = QtWidgets.QMessageBox(
                        QtWidgets.QMessageBox.Icon.Information,
                        "Experiment finished", _msg,
                        QtWidgets.QMessageBox.StandardButton.Ok, self)
                    _mb.setAttribute(
                        QtCore.Qt.WidgetAttribute.WA_DeleteOnClose, True)
                    self._finished_msgbox = _mb   # ref so it isn't GC'd
                    _mb.show()
                except Exception:
                    pass

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

        Disables the Parameters INPUT content (pattern panel,
        experiment-specific params, channel selector, combination
        panel) but leaves ``params_page`` AND its enclosing scroll areas
        ENABLED, so the operator can still scroll through and READ the
        parameters during a run (operator: "allow for scrolling through …
        Setup and Test Parameters").  The blunt
        ``params_page.setEnabled(False)`` propagated the disabled state to
        the scroll areas and froze scrolling; disabling only the two
        scroll-inner widgets keeps the scrollbars live.  Falls back to the
        whole-page disable if the inner refs are missing (defensive).
        The Stop button is left enabled by ``_start_runner`` /
        ``_on_finished``; the QTabWidget itself is untouched so the user
        can switch Parameters / Experiment.  Also emits
        :pyattr:`runStateChanged` so MainWindow can lock controls outside
        this tab (Setup, hardware connection).
        """
        inners = getattr(self, "_params_run_lock_content", None)
        if inners:
            for w in inners:
                if w is not None:
                    w.setEnabled(not locked)
        else:
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
        self.pause_btn.setText("Continue" if paused else "Pause")
        self.log_pane.log("Pause requested." if paused else "Continue requested.")

    # ---- MUST be implemented by subclass ----
    def start_clicked(self):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Voltage Transient tab
# ---------------------------------------------------------------------------
class VoltageTransientTab(_BaseExperimentTab):
    LOG_TAG = "VT"
    PARAM_LOG_WIDGETS = (
        ("mode_combo", "VT mode"),
        ("qph_lock_enable_chk", "Q_ph lock"),
        ("qph_lock_current", "Q_ph lock target"),
        ("qph_lock_width", "Q_ph lock target"),
        ("qph_lock_qph", "Q_ph lock target"),
        ("qinj_mc", "target charge density"),
        ("fixed_ramp_check", "ramp sweep"),
        ("pause_between_channels_check", "pause between channels"),
        ("strategy_combo", "ramp strategy"),
        ("start_ua", "ramp start current"),
        ("coarse_ua", "coarse step"),
        ("fine_ua", "fine step"),
        ("max_ua", "maximum current"),
        ("safety_factor", "safety factor"),
        ("failure_detect_check", "early failure stop"),
        ("failure_auto_check", "failure auto thresholds"),
        ("failure_min_ua", "failure min current"),
        ("failure_open_kohm", "open threshold"),
        ("failure_broken_kohm", "broken threshold"),
    )
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
        # Master enable for the 3-way parameter lock.  CHECKED (default)
        # preserves the historical behaviour: one of {current, width,
        # charge/phase} is auto-derived from the other two so charge per
        # phase stays consistent.  UNCHECKED frees current AND phase
        # width to be edited independently (charge/phase then just
        # DISPLAYS I×W and constrains nothing) — the operator asked for
        # this so they can change two parameters "without any affecting
        # the other".
        self.qph_lock_enable_chk = QtWidgets.QCheckBox(
            "Lock a parameter")
        self.qph_lock_enable_chk.setChecked(True)
        self.qph_lock_enable_chk.setToolTip(
            "When checked, ONE of the three parameters below is auto-"
            "derived from the other two (pick which with the radio "
            "buttons) so charge per phase stays consistent.\n\n"
            "Uncheck to edit current AND phase width independently — "
            "charge/phase then just shows I×W and doesn't force either "
            "value.")
        self.qph_lock_enable_chk.toggled.connect(
            self._on_qph_lock_enable_changed)
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
        self.qph_qph.setValue(self.qph_current.value() * self.qph_width.value()
                              * self._qph_excitation_duty() / 1000.0)
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
            "Adaptive/Predictive ramp only.  A back-off multiplier that is "
            "applied ONLY when the runner's successive max-charge predictions "
            "start OSCILLATING (bouncing up and down around the ceiling for "
            "≥3 steps) instead of converging.  When that happens the next "
            "target amplitude is multiplied by this factor (e.g. 0.85 = aim "
            "15% lower) to stop the ramp overshooting a moving prediction.\n\n"
            "It does NOTHING on a normal, well-behaved ramp — a stable "
            "monotonic prediction trail is used at full value (1.00×).  Lower "
            "it (more conservative) only if a channel keeps overshooting; "
            "0.85 is a good default.")

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
        # Master enable first, then one row per (radio-toggle + spinbox)
        # pair so the user reads them as a coupled triple. The radio in
        # each row marks that parameter as the auto-derived knob; the
        # spinbox shows its current value (read-only when its own radio
        # is checked). Unchecking the master enable disables the radios
        # and frees all three spinboxes.
        mode_form.addRow(self.qph_lock_enable_chk)
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

        # ----- Early open/broken failure detection (operator-configurable) ----
        # Operator: CH02/CH10 were mis-flagged "open" at 6 µA and the ramp
        # stopped early.  Make the early stop OPTIONAL + tunable, and never stop
        # below a minimum current where the classification is unreliable.
        self.failure_detect_check = QtWidgets.QCheckBox(
            "Stop ramp early on open / broken electrode")
        self.failure_detect_check.setChecked(True)
        self.failure_detect_check.setToolTip(
            "When on, the ramp stops early on a clearly open / broken / purely "
            "capacitive electrode (no Faradaic water window to ramp toward).\n"
            "Turn OFF to always ramp every channel to the potential limit / "
            "voltage compliance / maximum current.")
        self.failure_auto_check = QtWidgets.QCheckBox(
            "Auto thresholds (recommended)")
        self.failure_auto_check.setChecked(True)
        self.failure_auto_check.setToolTip(
            "Auto: use the built-in impedance thresholds.\n"
            "Off: use the manual open / broken kΩ thresholds below.")
        self.failure_min_ua = RepeatingDoubleSpinBox()
        self.failure_min_ua.setRange(0.0, 1000.0); self.failure_min_ua.setDecimals(0)
        self.failure_min_ua.setValue(50.0); self.failure_min_ua.setSuffix(" µA")
        self.failure_min_ua.setToolTip(
            "No early open/broken stop below this current — the classification "
            "is unreliable at low current (early polarization inflates the "
            "apparent impedance).  CH02/CH10 needed this.")
        self.failure_open_kohm = RepeatingDoubleSpinBox()
        self.failure_open_kohm.setRange(1.0, 100000.0)
        self.failure_open_kohm.setDecimals(0); self.failure_open_kohm.setValue(50.0)
        self.failure_open_kohm.setSuffix(" kΩ")
        self.failure_broken_kohm = RepeatingDoubleSpinBox()
        self.failure_broken_kohm.setRange(1.0, 100000.0)
        self.failure_broken_kohm.setDecimals(0); self.failure_broken_kohm.setValue(200.0)
        self.failure_broken_kohm.setSuffix(" kΩ")
        fail_form = rich.make_form()
        fail_form.addRow("", self.failure_auto_check)
        fail_form.addRow("Min current for detection:", self.failure_min_ua)
        fail_form.addRow("Open threshold (|V|/|I|):", self.failure_open_kohm)
        fail_form.addRow("Broken threshold (|V|/|I|):", self.failure_broken_kohm)
        failure_box = QtWidgets.QGroupBox("Early failure detection")
        _fv = QtWidgets.QVBoxLayout(failure_box)
        _fv.addWidget(self.failure_detect_check)
        _fv.addLayout(fail_form)
        self._failure_box = failure_box

        def _sync_failure_enabled(*_a):
            on = self.failure_detect_check.isChecked()
            manual = on and not self.failure_auto_check.isChecked()
            self.failure_auto_check.setEnabled(on)
            self.failure_min_ua.setEnabled(on)
            self.failure_open_kohm.setEnabled(manual)
            self.failure_broken_kohm.setEnabled(manual)
        self.failure_detect_check.toggled.connect(_sync_failure_enabled)
        self.failure_auto_check.toggled.connect(_sync_failure_enabled)
        _sync_failure_enabled()

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
        v.addWidget(failure_box)
        v.addWidget(sweep_box)
        v.addWidget(self.pattern_preview)
        self._ramp_box = ramp_box

        # Apply initial visibility
        self._on_mode_changed()
        self._on_strategy_changed()
        self._on_sweep_toggled()

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
        # Master enable is a single-widget row (no separate label), so
        # toggle its visibility directly rather than via
        # ``_set_form_row_visible`` (which needs a label-bearing field).
        self.qph_lock_enable_chk.setVisible(is_fixed_qph)
        self._set_form_row_visible(self.qph_current, is_fixed_qph)
        self._set_form_row_visible(self.qph_width,   is_fixed_qph)
        self._set_form_row_visible(self.qph_qph,     is_fixed_qph)
        self._set_form_row_visible(self.qinj_mc, is_fixed_qd)
        # Sync the 3-way coupled inputs from the current pattern when
        # entering MODE_FIXED_QPH — start from the live pulse so the
        # user doesn't see stale values from a previous session.
        if is_fixed_qph:
            self._sync_qph_inputs_from_pattern()
            # Re-apply the current lock/enable state (styling + which
            # spinboxes are editable) for the freshly-synced values.
            self._on_qph_lock_enable_changed(
                self.qph_lock_enable_chk.isChecked())
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

    def _on_qph_lock_enable_changed(self, checked):
        """Master parameter-lock enable toggled.

        Checked → the radio selection drives one auto-derived parameter
        (historical behaviour).  Unchecked → current AND phase width are
        both freely editable and charge/phase becomes a read-only I×W
        display that constrains nothing — so the operator can change two
        parameters without the third forcing one of them.
        """
        self.qph_lock_current.setEnabled(checked)
        self.qph_lock_width.setEnabled(checked)
        self.qph_lock_qph.setEnabled(checked)
        if checked:
            # Re-apply per-radio styling + recompute the locked value.
            self._on_qph_lock_changed()
        else:
            # Unlocked: current & width editable; charge/phase derived.
            self.pattern_panel._set_lock_state(self.qph_current, False)
            self.pattern_panel._set_lock_state(self.qph_width, False)
            self.pattern_panel._set_lock_state(self.qph_qph, True)
            self._recompute_locked_qph()  # target forced to QPH inside

    def _on_qph_lock_changed(self, *_):
        """A radio-toggle flipped — restyle the spinboxes so the locked
        one shows greyed-out + italic and is read-only, recompute the
        new locked value from the other two, and propagate the result
        into the pattern panel.
        """
        # No-op when the master lock is disabled — the radios are greyed
        # out and a stray programmatic toggle (e.g. during prefs
        # restore) must not re-impose lock styling on a free spinbox.
        if not self.qph_lock_enable_chk.isChecked():
            return
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
        # Master lock OFF → charge/phase is the derived (display-only)
        # value, so current and width stay independent regardless of
        # which radio happens to be checked.
        if not self.qph_lock_enable_chk.isChecked():
            target = self.QPH_LOCK_QPH
        else:
            target = self._qph_lock_target()
        I = float(self.qph_current.value())
        W = float(self.qph_width.value())
        Q = float(self.qph_qph.value())
        # Shape duty so Q_ph targets DELIVERED charge, not peak charge
        # (operator: "charge/phase calculated correctly for each pulse
        # shape").  Q_ph = I·W·d/1000; d = 1 for rectangular (unchanged).
        d = self._qph_excitation_duty()
        self._qph_in_progress = True
        try:
            if target == self.QPH_LOCK_CURRENT:
                # I = Q · 1000 / (W · d)
                if W > 0 and d > 0:
                    self.qph_current.setValue(max(0.0, Q * 1000.0 / (W * d)))
            elif target == self.QPH_LOCK_WIDTH:
                # W = Q · 1000 / (I · d)
                if I > 0 and d > 0:
                    self.qph_width.setValue(max(1.0, Q * 1000.0 / (I * d)))
            else:
                # Q = I · W · d / 1000
                self.qph_qph.setValue(I * W * d / 1000.0)
        finally:
            self._qph_in_progress = False
        self._push_qph_to_pattern()

    def _qph_excitation_duty(self) -> float:
        """Shape-duty (delivered / peak charge fraction) of the Fixed-Q_ph
        excitation phase, so the lock's Q_ph = I·W·duty/1000 reflects the
        DELIVERED charge rather than peak charge.

        Reads the SYMMETRIC excitation shape (the Fixed-Q_ph lock drives the
        symmetric ``amp_excite`` / ``width_shared`` controls).  Returns 1.0
        for rectangular (so behaviour is unchanged) and on any error.  The
        analytic ``_shape_duty`` is used for invertibility (I↔W↔Q closed
        form); it agrees with the device-exact ``charge_per_phase_nc`` to
        within the ~1 % staircase/quantization error, which is negligible for
        a target-setting tool (the delivered charge is then shown exactly by
        the device-exact pulse-preview / saved metric)."""
        try:
            from ..waveforms import _shape_duty, SHAPE_RECTANGULAR
            pp = self.pattern_panel
            shape = (pp.shape_combo.currentData() if hasattr(pp, "shape_combo")
                     else None) or SHAPE_RECTANGULAR
            bump = (int(pp.bump_count.value())
                    if hasattr(pp, "bump_count") else 2)
            d = float(_shape_duty(shape, bump_count=bump))
            return d if d > 1e-6 else 1.0
        except Exception:
            return 1.0

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
            # ``start_ua`` is never shown — the VT ramp starts at the pattern
            # amplitude (operator: "the stimulation pattern should be starting
            # Istim"); the field was removed from RampPolicy.
            base = {"coarse_ua", "fine_ua", "max_ua"}
            for key, (lab, w) in self._ramp_rows.items():
                visible = key in base
                lab.setVisible(visible); w.setVisible(visible)
            return
        strat = self.strategy_combo.currentText()
        # Visibility per strategy (start_ua removed — ramp starts at the pattern):
        #   Fixed increment  : coarse, fine, max  (no safety factor)
        #   Adaptive / Pred  : max, safety_factor  (no coarse/fine)
        show = {
            self.STRAT_INCR: {"coarse_ua", "fine_ua", "max_ua"},
            self.STRAT_REGR: {"max_ua", "safety_factor"},
            self.STRAT_PRED: {"max_ua", "safety_factor"},
        }.get(strat, {"coarse_ua", "fine_ua", "max_ua"})
        for key, (lab, w) in self._ramp_rows.items():
            visible = key in show
            lab.setVisible(visible); w.setVisible(visible)

    PREF_FIELDS = ("mode_combo", "strategy_combo", "fixed_ramp_check",
                   "pause_between_channels_check",
                   "qinj_mc",
                   "qph_current", "qph_width", "qph_qph",
                   "start_ua", "coarse_ua", "fine_ua", "max_ua", "safety_factor",
                   "failure_detect_check", "failure_auto_check",
                   "failure_min_ua", "failure_open_kohm", "failure_broken_kohm",
                   "sweep_check", "sweep_rates", "sweep_asym")

    def current_prefs(self) -> dict:
        # Add the radio-button lock target since the base PREF_FIELDS
        # walker only handles spinboxes / combos / checkboxes.
        out = super().current_prefs()
        out["qph_lock_target"] = self._qph_lock_target()
        out["qph_lock_enabled"] = bool(self.qph_lock_enable_chk.isChecked())
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
        # Master enable defaults ON (historical behaviour) when the key
        # is absent from older prefs.  Set it WITHOUT firing the toggled
        # slot mid-restore, then apply the combined enable+lock state in
        # one call so styling / editability is consistent.
        enabled = bool((p or {}).get("qph_lock_enabled", True))
        self.qph_lock_enable_chk.blockSignals(True)
        self.qph_lock_enable_chk.setChecked(enabled)
        self.qph_lock_enable_chk.blockSignals(False)
        self._on_qph_lock_enable_changed(enabled)

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
            # The ramp starts at the PATTERN's own amplitude (operator: "the
            # stimulation pattern should be starting Istim"); use the panel
            # pulse AS-IS and let the runner ramp up from there.  No start_ua
            # scaling — start_ua was removed.
            pattern = shape
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
        session = Session(
            # Setup-tab identity (operator: files + plots carry
            # [notebook]_[session], not the first config) — legacy
            # fallbacks when the Setup fields are blank.
            notebook=self._notebook or "vt_session",
            subject=self._session_subject or config.display_name(),
            test=test)
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
        sweep_points = self._build_sweep_points(pattern)
        runner = VoltageTransientExperiment(
            session, self._stim, self._scope,
            configurations=configs, ramp=ramp, predictor=predictor,
            cathodic_limit_v=self._cathodic_limit_v,
            anodic_limit_v=self._anodic_limit_v,
            polarization_tolerance_v=self._polarization_tolerance_v,
            sweep_points=sweep_points,
        )
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        runner.smoothing_enabled = bool(
            getattr(self, "smoothing_chk", None) is not None
            and self.smoothing_chk.isChecked())
        if getattr(self, "smoothing_window_spin", None) is not None:
            runner.smoothing_window = int(self.smoothing_window_spin.value())
        # Push the between-channels rewire-pause flag onto the runner.
        # Only meaningful when more than one configuration was selected;
        # the runner's wait_for_continue short-circuits if the flag is off.
        runner.pause_between_channels = (
            self.pause_between_channels_check.isChecked())
        save_name = (f"{self._session_stem}.npz" if self._session_stem else
                     f"VT_{config.display_name().replace(' ', '_')}.npz")
        self._start_runner(runner, save_name)

    def _failure_detection_kwargs(self) -> dict:
        """RampPolicy failure-detection fields from the GUI (operator #4:
        optional early open/broken stop + configurable thresholds)."""
        return dict(
            stop_on_bad_response=self.failure_detect_check.isChecked(),
            bad_response_auto=self.failure_auto_check.isChecked(),
            bad_response_min_current_ua=float(self.failure_min_ua.value()),
            bad_response_open_z_kohm=float(self.failure_open_kohm.value()),
            bad_response_broken_z_kohm=float(self.failure_broken_kohm.value()),
        )

    def _build_ramp_policy(self, panel_amp_ua: float) -> "RampPolicy":
        """Translate (mode, strategy) into a :class:`RampPolicy`.

        The VT ramp ALWAYS starts at the configured stimulation pattern's
        own amplitude (operator: "the stimulation pattern should be starting
        Istim" — ``RampPolicy.starting_ua`` was removed).  ``panel_amp_ua``
        here is the ACTUAL (post-scale) pattern excitation amplitude the
        runner will start from.

        * **Fixed mode (no ramp)** — single shot at the pattern amplitude.
          We set ``max_ua = |pattern_amp|`` so the ramp loop (which starts at
          the pattern amplitude) runs exactly once.
        * **Fixed mode + Ramp checkbox** — sweeps from the pattern amplitude
          to max_ua using the user's coarse/fine step sizes (same loop as
          Maximum mode + Fixed increment, but the ceiling is the user-typed
          max_ua rather than a water-window probe).
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
            # Single shot: the runner starts at the pattern amplitude and
            # max_ua == that amplitude, so the ramp loop runs exactly once.
            amp = abs(panel_amp_ua) if panel_amp_ua != 0 else 5.0
            return RampPolicy(coarse_step_ua=max(amp, 1.0),
                              fine_step_ua=max(amp, 1.0),
                              max_ua=amp, **self._failure_detection_kwargs())
        if is_fixed_any and ramp_on:
            # Fixed mode + Ramp on — same shape as the Maximum-mode
            # fixed-increment path, but capped at the user's max_ua
            # rather than the water-window detection logic.  Starts at the
            # pattern amplitude (no separate start knob).
            return RampPolicy(coarse_step_ua=self.coarse_ua.value(),
                              fine_step_ua=self.fine_ua.value(),
                              max_ua=self.max_ua.value(),
                              **self._failure_detection_kwargs())
        strat = self.strategy_combo.currentText()
        if strat == self.STRAT_INCR:
            return RampPolicy(coarse_step_ua=self.coarse_ua.value(),
                              fine_step_ua=self.fine_ua.value(),
                              max_ua=self.max_ua.value(),
                              strategy="increment",
                              **self._failure_detection_kwargs())
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
        return RampPolicy(coarse_step_ua=coarse_default,
                          fine_step_ua=fine_default,
                          max_ua=max_amp,
                          strategy=runner_strat,
                          safety_factor=safety,
                          **self._failure_detection_kwargs())


# ---------------------------------------------------------------------------
# Short-Term Pulsing tab
# ---------------------------------------------------------------------------
class ShortPulsingTab(_BaseExperimentTab):
    LOG_TAG = "SP"
    #: SP supports burst / pulse-train stimulation (ContinuousPulsingTab
    #: subclasses SP so it inherits this automatically).
    SUPPORTS_BURST = True
    PARAM_LOG_WIDGETS = (
        ("duration", "duration"),
        ("duration_unit", "duration unit"),
        ("do_char", "pre/post characterization"),
        ("char_mode", "characterization mode"),
        ("qinj_mc", "target charge density"),
        ("qph_nc", "target charge/phase"),
    )
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

        # (The on-demand "Acquire waveform" button was removed from the
        # Short-Term Pulsing parameters per operator request — captures
        # happen automatically at the runner's snapshot cadence, so a
        # manual single-shot button was redundant clutter.)
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

    # Short Pulsing runs the SELECTED channel/combo only (operator: "do not
    # capture all channels, just the selected channel/combo").  The
    # combination panel stays single-select; the multi-config save path
    # (gotcha #46) remains available but isn't driven from the UI.
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
        rate_hz = self._effective_pulse_rate_hz()
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
            rate = self._effective_pulse_rate_hz()
            return value / rate
        return float(value)

    # (``_acquire_one_capture`` + the ``set_hardware`` / ``clear_hardware``
    # overrides that enabled its button were removed along with the
    # "Acquire waveform" control — see the note in ``__init__``.  The
    # base ``_BaseExperimentTab.set_hardware`` / ``clear_hardware`` now
    # apply unchanged.)

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
        # Fresh shared session for this Start press: every queued
        # configuration accumulates into ONE Session so the saved
        # .npz / .xlsx contain ALL channels, not just the last one
        # written (operator: "running monopolar on all channels, but
        # it only captured CH01" — each config used to get its own
        # Session saved to the same filename, so they overwrote one
        # another and only the final channel survived).  See
        # _start_next_pending for the accumulation.
        self._sp_session = None
        self._sp_save_name = None
        self._export_carryover_ids = set()
        if len(self._pending_configs) > 1:
            self.log_pane.log(
                f"Short-Term Pulsing: queued {len(self._pending_configs)} "
                f"configuration(s) — running sequentially into one session.")
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
        # Accumulate every queued configuration into ONE growing Session
        # (matches VT, which already runs all configs through a single
        # session).  Each SP configuration still gets its own runner —
        # the between-channel rewiring pauses and the Stop/Start stim
        # lifecycle need that — but they SHARE one Session: the first
        # config creates it; later configs repoint
        # ``session.test.configuration`` (TestParameters is a mutable
        # dataclass, and the pattern / array / duration / extras are
        # identical across the sweep) and the runner appends its
        # ChannelRun via ``add_run``.  The worker re-saves the cumulative
        # session to the SAME path each time, so the .npz grows
        # CH01 → CH01+CH02 → … instead of being overwritten.  The save
        # filename is captured once so it stays constant even on the
        # legacy ``SP_<config>`` fallback (a per-config name would defeat
        # the single-file accumulation).
        if getattr(self, "_sp_session", None) is None:
            session = Session(
                # Setup-tab identity (operator: files + plots carry
                # [notebook]_[session], not the first config) — legacy
                # fallbacks when the Setup fields are blank.
                notebook=self._notebook or "sp_session",
                subject=self._session_subject or config.display_name(),
                test=test)
            self._stamp_extras(session)
            self._sp_session = session
            self._sp_save_name = (
                f"{self._session_stem}.npz" if self._session_stem else
                f"SP_{config.display_name().replace(' ', '_')}.npz")
        else:
            session = self._sp_session
            session.test.configuration = config
            # Re-stamp so the active-site area / setup snapshot reflect
            # the current config (cheap; extras dict is small).
            self._stamp_extras(session)
        runner = ShortPulsingExperiment(
            session, self._stim, self._scope, amplitude_ua=amplitude_ua,
            policy=ShortPulsingPolicy(
                capture_interval_s=max(duration_s * 2, 60.0),
                duration_s=duration_s),
        )
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        runner.smoothing_enabled = bool(
            getattr(self, "smoothing_chk", None) is not None
            and self.smoothing_chk.isChecked())
        if getattr(self, "smoothing_window_spin", None) is not None:
            runner.smoothing_window = int(self.smoothing_window_spin.value())
        # Constant filename across the whole sweep so every config saves
        # the cumulative session to the SAME .npz (see above).
        self._start_runner(runner, self._sp_save_name)


# ---------------------------------------------------------------------------
# Continuous Pulsing tab
# ---------------------------------------------------------------------------
class ContinuousPulsingTab(ShortPulsingTab):
    LOG_TAG = "CP"
    PARAM_LOG_WIDGETS = ShortPulsingTab.PARAM_LOG_WIDGETS + (
        ("snapshot_interval", "snapshot every"),
    )
    """Manual start/stop pulsing (operator: "the user just starts and
    stops the pulsing manually rather than a fixed number of pulses").

    Thin subclass of the Short-Term Pulsing tab: the Duration row is
    replaced by a "Snapshot every" cadence spinbox, and Start launches
    the unbounded :class:`ContinuousPulsingExperiment` — the GUI Stop
    button is the designed (normal) end of the run.
    """

    def __init__(self, array, parent=None):
        super().__init__(array, parent)
        # ---- swap the Duration row for a snapshot-cadence row -------
        # The duration widgets stay constructed (prefs round-trip +
        # inherited helpers read them without AttributeError) but are
        # hidden — a continuous run has no duration by definition.
        self.snapshot_interval = RepeatingDoubleSpinBox()
        self.snapshot_interval.setRange(0.5, 3600.0)
        self.snapshot_interval.setValue(5.0)
        self.snapshot_interval.setDecimals(1)
        self.snapshot_interval.setSuffix(" s")
        self.snapshot_interval.setToolTip(
            "Seconds between snapshot captures while pulsing. Each "
            "snapshot is a full averaged acquisition with the complete "
            "metric set; pulsing itself runs continuously until you "
            "press Stop.")
        _dur_w = self.duration.parentWidget()
        _form = None
        if _dur_w is not None:
            _host = _dur_w.parentWidget()
            if _host is not None:
                for _lay in _host.findChildren(QtWidgets.QFormLayout):
                    if _lay.indexOf(_dur_w) >= 0:
                        _form = _lay
                        break
        if _form is not None:
            _lbl = _form.labelForField(_dur_w)
            if _lbl is not None:
                _lbl.setVisible(False)
            _dur_w.setVisible(False)
            # Put the cadence row where Duration sat.
            _row = _form.getWidgetPosition(_dur_w)[0]
            _form.insertRow(max(_row, 0), "Snapshot every:",
                            self.snapshot_interval)
        # Group-box title follows the experiment name.
        for _gb in self.findChildren(QtWidgets.QGroupBox):
            if _gb.title() == "Short-Term Pulsing parameters":
                _gb.setTitle("Continuous Pulsing parameters")
                break

    PREF_FIELDS = ShortPulsingTab.PREF_FIELDS + ("snapshot_interval",)

    def experiment_type(self): return "CP"

    def _start_next_pending(self):
        if not self._pending_configs:
            return
        from ..experiments.continuous_pulsing import (
            ContinuousPulsingExperiment, ContinuousPulsingPolicy)
        config = self._pending_configs.pop(0)
        pattern = self.pattern_panel.pattern()
        amplitude_ua = abs(pattern.excitation_phase.amplitude_ua)
        # Pre-run damage screen — fixed amplitude, same as SP.  The
        # damage models are duty-cycle based, so an open-ended run is
        # screened at the configured amplitude like any other.
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
        test = TestParameters(experiment="CP", pattern=pattern,
                              configuration=config, array=self._array,
                              duration_s=0.0)
        # Accumulate multi-config CP runs into ONE shared, growing
        # session (same rationale + mechanism as
        # ShortPulsingTab._start_next_pending — CLAUDE.md gotcha #46).
        # CP is usually single-config (manual Stop per channel), but a
        # queued multi-channel CP sweep would otherwise overwrite the
        # .npz once per config and keep only the last channel.  Reuses
        # the SP shared-session slots (reset in the inherited
        # start_clicked).
        if getattr(self, "_sp_session", None) is None:
            session = Session(
                notebook=self._notebook or "cp_session",
                subject=self._session_subject or config.display_name(),
                test=test)
            self._stamp_extras(session)
            self._sp_session = session
            self._sp_save_name = (
                f"{self._session_stem}.npz" if self._session_stem else
                f"CP_{config.display_name().replace(' ', '_')}.npz")
        else:
            session = self._sp_session
            session.test.configuration = config
            self._stamp_extras(session)
        runner = ContinuousPulsingExperiment(
            session, self._stim, self._scope, amplitude_ua=amplitude_ua,
            policy=ContinuousPulsingPolicy(
                capture_interval_s=float(self.snapshot_interval.value())),
        )
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        runner.smoothing_enabled = bool(
            getattr(self, "smoothing_chk", None) is not None
            and self.smoothing_chk.isChecked())
        if getattr(self, "smoothing_window_spin", None) is not None:
            runner.smoothing_window = int(self.smoothing_window_spin.value())
        self.log_pane.log(
            "Continuous Pulsing: pulsing until Stop is pressed "
            f"(snapshot every {self.snapshot_interval.value():g} s).")
        # Constant filename across the sweep (shared session — see above).
        self._start_runner(runner, self._sp_save_name)


# ---------------------------------------------------------------------------
# Long-Term Pulsing tab
# ---------------------------------------------------------------------------
class LongPulsingTab(_BaseExperimentTab):
    LOG_TAG = "LP"
    #: LP supports burst / pulse-train stimulation.
    SUPPORTS_BURST = True
    PARAM_LOG_WIDGETS = (
        ("duration", "duration"),
        ("duration_unit", "duration unit"),
        ("snap_int", "snapshot interval"),
        ("snap_int_unit", "snapshot interval unit"),
        ("periodic_combo", "periodic feature"),
        ("char_int", "periodic-event interval"),
        ("char_int_unit", "periodic-event interval unit"),
        ("do_event_at_start", "periodic event at start"),
        ("pause_s", "pause duration"),
        ("pause_unit", "pause unit"),
    )
    # Total duration / re-characterization interval can each be authored
    # in seconds OR pulses (count); the unit-toggle dropdowns convert at
    # start-time using the panel's rate to come up with a seconds value
    # the runner expects.
    UNIT_S = "seconds"
    UNIT_P = "pulses"
    # Periodic-feature dropdown options (operator: a single dropdown instead of
    # two checkboxes — None / Maximum VT sweep / Pause / both).
    PERIODIC_NONE = "None"
    PERIODIC_MAXVT = "Maximum VT sweep"
    PERIODIC_PAUSE = "Pause"
    PERIODIC_BOTH = "Maximum VT sweep + Pause"

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

        # PERIODIC EVENT (max-VT and/or pause) — operator #81.  A separate
        # schedule from the snapshot: every ``char_int`` the runner can run
        # a max-Q_inj VT sweep and/or pause stimulation for an external
        # measurement.  The two toggles below are INDEPENDENT (either/both).
        self.char_int = RepeatingDoubleSpinBox()
        self.char_int.setRange(1, 1e9); self.char_int.setValue(600)
        self.char_int.setDecimals(1); self.char_int.setSuffix(" s")
        self.char_int.setToolTip(
            "How often to run the periodic event below (a max-Q_inj VT "
            "sweep and/or a potentiostat pause).  Distinct from the "
            "snapshot interval — the periodic event pauses pulsing while "
            "it runs; the snapshot doesn't.")
        self.char_int_unit = QtWidgets.QComboBox()
        self.char_int_unit.addItems([self.UNIT_S, self.UNIT_P])
        self.char_int_unit.currentTextChanged.connect(self._on_char_unit)
        self._char_prev_unit = self.UNIT_S
        # Fire the FIRST periodic event at the START of the run (t=0) —
        # a baseline max-VT + potentiostat window (operator: "at the start
        # of LP").  Default ON so the baseline is captured.
        self.do_event_at_start = QtWidgets.QCheckBox(
            "Also run the periodic event at the start (t=0)")
        self.do_event_at_start.setChecked(True)
        self.do_event_at_start.setToolTip(
            "Run the first max-VT / pause event at the very start of the "
            "run so you get a baseline max(Q_inj) + a potentiostat window "
            "at t=0, not only after the first full period.")
        # PERIODIC FEATURE — a single dropdown (operator: "Dropdown list for
        # periodic feature: none, maximum VT sweep, pause, maximum VT sweep +
        # pause.  When not none, show and enable an input for time or pulses").
        # The selection drives ``_periodic_run_max_vt`` / ``_periodic_do_pause``
        # (both share ONE event schedule); the Start logic keys the runner's
        # period on those (period → huge when None, so the runner skips the
        # event).  ``_on_periodic_feature_changed`` shows/enables the interval
        # (time or pulses) + the at-start toggle when not None, and the pause
        # duration only when the selection includes Pause.
        self.periodic_combo = QtWidgets.QComboBox()
        self.periodic_combo.addItems([
            self.PERIODIC_NONE, self.PERIODIC_MAXVT,
            self.PERIODIC_PAUSE, self.PERIODIC_BOTH])
        self.periodic_combo.setToolTip(
            "Periodic event during the run (a separate schedule from the "
            "snapshot; it pauses pulsing while it runs):\n"
            "  • None — no periodic event.\n"
            "  • Maximum VT sweep — every period, run a brief max-Q_inj ramp "
            "for a fresh safe-stim-ceiling data point over the run.\n"
            "  • Pause — every period, stop stimulation for the configured "
            "duration so an external instrument (potentiostat / EIS / cyclic "
            "voltammetry / microscopy) can measure, then resume.\n"
            "  • Maximum VT sweep + Pause — both (the sweep runs first, then "
            "the pause).")
        # Pause DURATION — shown/enabled only when the selection includes Pause.
        self.pause_s = RepeatingDoubleSpinBox()
        self.pause_s.setRange(1, 1e9); self.pause_s.setValue(60)
        self.pause_s.setDecimals(1); self.pause_s.setSuffix(" s")
        self.pause_s.setToolTip(
            "Pause duration each period. Stim is off for this whole window; "
            "use the time for external measurements. After the pause, stim "
            "resumes at the configured amplitude until the next event.")
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
        # Re-apply visibility/enable whenever the selection changes.  Connected
        # AFTER addItems (so it doesn't fire mid-construction); the handler also
        # guards on the form not being built yet.
        self.periodic_combo.currentTextChanged.connect(
            self._on_periodic_feature_changed)

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
        # Label per operator: 'Rename "Waveform snapshot every" to
        # "Periodic snapshot"' — the interval spinbox + unit combo to the
        # right still read naturally ("Periodic snapshot: [30] [s]").
        f.addRow("Periodic snapshot:", sn_w)
        # PERIODIC FEATURE dropdown + its dependent inputs (operator: single
        # dropdown; show the time/pulses input when not None).
        f.addRow("Periodic feature:", self.periodic_combo)
        # Shared event interval (time or pulses) — shown when not None.
        pe_row = QtWidgets.QHBoxLayout()
        pe_row.setContentsMargins(0, 0, 0, 0)
        pe_row.setSpacing(4)
        pe_row.addWidget(self.char_int, stretch=2)
        pe_row.addWidget(self.char_int_unit, stretch=1)
        pe_w = QtWidgets.QWidget(); pe_w.setLayout(pe_row)
        self._pe_field = pe_w
        f.addRow("Periodic event every:", pe_w)
        # Pause duration — shown only when the selection includes Pause.
        pa_row = QtWidgets.QHBoxLayout()
        pa_row.setContentsMargins(0, 0, 0, 0)
        pa_row.setSpacing(4)
        pa_row.addWidget(self.pause_s, stretch=2)
        pa_row.addWidget(self.pause_unit, stretch=1)
        pa_w = QtWidgets.QWidget(); pa_w.setLayout(pa_row)
        self._pa_field = pa_w
        f.addRow("Pause duration:", pa_w)
        f.addRow("", self.do_event_at_start)
        # Remember the form + apply the initial visibility for the default
        # (None) selection.
        self._lp_form = f
        self._on_periodic_feature_changed()

        # Crash-recovery: continue an interrupted LP run from its .npz.
        self.resume_btn = QtWidgets.QPushButton("Resume from .npz…")
        self.resume_btn.setToolTip(
            "Continue a Long-Term Pulsing run that was interrupted by a "
            "crash, power loss, or accidental close.  Pick the run's .npz "
            "(PULSAR writes a mid-run snapshot after every capture); "
            "pulsing resumes near where it stopped and appends new "
            "captures to the same file until the original total duration "
            "is reached.")
        self.resume_btn.clicked.connect(self.resume_from_npz_clicked)

        box = QtWidgets.QGroupBox("Long-Term Pulsing parameters")
        v = QtWidgets.QVBoxLayout(box)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(2)
        v.addWidget(self.pattern_panel)
        v.addWidget(self.pattern_preview)
        v.addLayout(f)
        v.addWidget(self.resume_btn)

        self._assemble_pages(box)

    def _extra_experiment_tabs(self):
        # Per the user spec: LP shows two sub-tabs on the Experiment
        # page — "Voltage Transient" (the existing multichannel scope,
        # added by the base class) and "Tracking" (this widget).
        return (("Tracking", self.tracking_plot),)

    def _refresh_preview(self, *_):
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    # MULTI-SELECT (operator: "I wanted simultaneous pulsing if multichannel
    # monopolar stimulation was selected").  LP must allow selecting SEVERAL
    # monopolar channels so ``start_clicked`` can pulse them ALL at once
    # (``_lp_pulse_channels`` → the runner's ``pulse_channels``, gotcha #105).
    # With ``SINGLE_CONFIG = True`` the combination panel was locked to
    # single-select, so ``len(configs) > 1`` never held and the whole
    # simultaneous-pulsing path was UNREACHABLE dead code — LP always pulsed
    # just one channel.  ``False`` unlocks the multi-selection that triggers it;
    # a multipolar (BP/TP) selection is constrained to ONE combo via
    # ``MULTIPOLAR_SINGLE`` below (operator: "only allow for one selection of
    # channel/combo under (partial) multipolar configuration"; the return
    # geometry is part of the test definition and conflicting return sets
    # can't be chronically co-pulsed).  **Don't set SINGLE_CONFIG back to
    # True.**
    SINGLE_CONFIG = False
    #: Multipolar (BP/TP/PBP/PTP) → single-combo for LP (Monopolar stays
    #: multi-select for simultaneous-channel pulsing).
    MULTIPOLAR_SINGLE = True
    PREF_FIELDS = ("duration", "duration_unit",
                   "snap_int", "snap_int_unit",
                   "periodic_combo",
                   "char_int", "char_int_unit", "do_event_at_start",
                   "pause_s", "pause_unit")

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
        rate_hz = self._effective_pulse_rate_hz()
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

    def _on_char_unit(self, *_):
        self._handle_unit_change(self.char_int,
                                 self.char_int_unit.currentText(),
                                 "_char_prev_unit")

    def _to_seconds(self, value: float, unit: str) -> float:
        if unit == self.UNIT_P:
            rate = self._effective_pulse_rate_hz()
            return value / rate
        return value

    def _periodic_run_max_vt(self) -> bool:
        """True when the periodic-feature dropdown includes the max-VT sweep."""
        return self.periodic_combo.currentText() in (
            self.PERIODIC_MAXVT, self.PERIODIC_BOTH)

    def _periodic_do_pause(self) -> bool:
        """True when the periodic-feature dropdown includes the pause."""
        return self.periodic_combo.currentText() in (
            self.PERIODIC_PAUSE, self.PERIODIC_BOTH)

    def _set_lp_row_visible(self, field_w, visible: bool):
        """Show/hide a QFormLayout row (label + field) on the LP param form.
        Uses ``QFormLayout.setRowVisible`` (Qt 6.4+) with a hide-both
        fallback for older Qt."""
        form = getattr(self, "_lp_form", None)
        if form is None:
            field_w.setVisible(visible)
            return
        try:
            form.setRowVisible(field_w, visible)
            return
        except (AttributeError, TypeError):
            pass
        field_w.setVisible(visible)
        try:
            lbl = form.labelForField(field_w)
        except (AttributeError, TypeError):
            lbl = None
        if lbl is not None:
            lbl.setVisible(visible)

    def _on_periodic_feature_changed(self, *_a):
        """Show + enable the interval (time/pulses) + at-start when the
        periodic feature is not None; show + enable the pause duration only
        when the selection includes Pause (operator dropdown spec).  No-op
        until the form is built (guards a stray currentTextChanged during
        construction)."""
        if getattr(self, "_lp_form", None) is None:
            return
        not_none = self.periodic_combo.currentText() != self.PERIODIC_NONE
        do_pause = self._periodic_do_pause()
        # Interval + at-start: only when a periodic feature is selected.
        self._set_lp_row_visible(self._pe_field, not_none)
        self._set_lp_row_visible(self.do_event_at_start, not_none)
        self.char_int.setEnabled(not_none)
        self.char_int_unit.setEnabled(not_none)
        self.do_event_at_start.setEnabled(not_none)
        # Pause duration: only when the selection includes Pause.
        self._set_lp_row_visible(self._pa_field, do_pause)
        self.pause_s.setEnabled(do_pause)
        self.pause_unit.setEnabled(do_pause)

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
            # Migrate LEGACY prefs (two checkboxes) → the periodic-feature
            # dropdown when the new key is absent.
            if "periodic_combo" not in p:
                _mv = bool(p.get("do_max_before_pause"))
                _pz = bool(p.get("do_pause"))
                _sel = (self.PERIODIC_BOTH if (_mv and _pz)
                        else self.PERIODIC_MAXVT if _mv
                        else self.PERIODIC_PAUSE if _pz
                        else self.PERIODIC_NONE)
                p = {**p, "periodic_combo": _sel}
        super().restore_prefs(p)
        # The dropdown signal already fired (no conversion thanks to
        # the prev_unit pre-sync above) — but if the saved unit was
        # never written, the suffix might still be the __init__
        # default. Re-apply the suffix to be safe.
        self._apply_unit_suffix(self.duration, self.duration_unit.currentText())
        self._apply_unit_suffix(self.snap_int, self.snap_int_unit.currentText())
        self._apply_unit_suffix(self.pause_s, self.pause_unit.currentText())
        # Re-apply the periodic-feature visibility/enable to the RESTORED
        # dropdown selection (in case the base field-walk set it with signals
        # blocked).
        self._on_periodic_feature_changed()

    @staticmethod
    def _resolve_pulse_channels(configs):
        """The channels to pulse SIMULTANEOUSLY for a multichannel-monopolar
        selection, or ``None`` for a single / multipolar selection (→ the
        sequential per-config chain).

        Multi-channel monopolar = ≥ 2 selected configs, ALL monopolar (no
        return channels).  Operator: simultaneous pulsing when multichannel
        monopolar is selected.  Extracted from ``start_clicked`` so the
        decision is unit-testable without spinning up hardware."""
        all_monopolar = bool(configs) and all(
            not getattr(c, "returns", ()) for c in configs)
        if all_monopolar and len(configs) > 1:
            return [int(c.active) for c in configs]
        return None

    def start_clicked(self):
        if not self._stim or not self._scope: return
        configs = self.combo_panel.selected_configurations()
        if not configs:
            QtWidgets.QMessageBox.warning(
                self, "No configuration selected",
                "Click an electrode and pick one combination from the "
                "Configurations to run list.")
            return
        # MULTI-CHANNEL MONOPOLAR: pulse ALL selected monopolar channels
        # SIMULTANEOUSLY in one run (chronic stimulation of the array) —
        # operator: "I wanted simultaneous pulsing if multichannel monopolar
        # stimulation was selected."  Previously LP queued each config and ran
        # them SEQUENTIALLY (pulse CH01 for the whole duration, then CH02, …),
        # which is wrong for chronic stim.  When every selected config is
        # monopolar (no return channels), collect their active channels and
        # run ONE LongPulsingExperiment that pulses them all at once,
        # monitoring the FIRST as the representative scope channel.  Any
        # non-monopolar config (BP/TP share return paths → can't co-run)
        # keeps the sequential per-config chain.  (Requires SINGLE_CONFIG =
        # False so the combo panel actually allows the multi-selection.)
        self._lp_pulse_channels = self._resolve_pulse_channels(configs)
        if self._lp_pulse_channels:
            self._pending_configs = [configs[0]]   # one run, all channels
            self.log_pane.log(
                f"Long-Term Pulsing: pulsing {len(self._lp_pulse_channels)} "
                f"monopolar channels simultaneously "
                f"({sorted(self._lp_pulse_channels)}); monitoring "
                f"CH{int(configs[0].active):02d}.")
        else:
            # Queue every selected config; the base class chains them via
            # ``_on_finished`` -> ``_start_next_pending``.
            self._pending_configs = list(configs)
            if len(self._pending_configs) > 1:
                self.log_pane.log(
                    f"Long-Term Pulsing: queued {len(self._pending_configs)} "
                    f"configuration(s) — running sequentially.")
        if self._periodic_do_pause() or self._periodic_run_max_vt():
            _period = self._to_seconds(self.char_int.value(),
                                       self.char_int_unit.currentText())
            parts = []
            if self._periodic_run_max_vt():
                parts.append("max(Q_inj) VT sweep")
            if self._periodic_do_pause():
                _p = self._to_seconds(self.pause_s.value(),
                                      self.pause_unit.currentText())
                parts.append(f"pause {_p:.0f} s")
            self.log_pane.log(
                f"Long-Term Pulsing periodic event every {_period:.0f} s: "
                + " + ".join(parts)
                + ("  ·  first event at t=0"
                   if self.do_event_at_start.isChecked() else ""))
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
        session = Session(
            # Setup-tab identity (operator: files + plots carry
            # [notebook]_[session], not the first config) — legacy
            # fallbacks when the Setup fields are blank.
            notebook=self._notebook or "lp_session",
            subject=self._session_subject or config.display_name(),
            test=test)
        self._stamp_extras(session)
        # Periodic-event (max-VT and/or potentiostat pause) config —
        # operator #81.  Enabled when EITHER toggle is on; otherwise the
        # period is set huge so the runner skips the event entirely and
        # only mid-pulsing snapshots fire.
        _run_max_vt = self._periodic_run_max_vt()
        _do_pause = self._periodic_do_pause()
        _event_enabled = _run_max_vt or _do_pause
        _period_s = (self._to_seconds(self.char_int.value(),
                                      self.char_int_unit.currentText())
                     if _event_enabled else max(duration_s, 1e9))
        _pause_s = (self._to_seconds(self.pause_s.value(),
                                     self.pause_unit.currentText())
                    if _do_pause else 0.0)
        runner = LongPulsingExperiment(
            session, self._stim, self._scope, amplitude_ua=amplitude_ua,
            # Multi-channel monopolar: pulse every selected channel at once
            # (set in start_clicked; None → classic single-channel LP).
            pulse_channels=getattr(self, "_lp_pulse_channels", None),
            policy=LongPulsingPolicy(
                duration_s=duration_s,
                characterize_every_s=_period_s,
                run_max_vt=_run_max_vt,
                pause_duration_s=_pause_s,
                fire_periodic_at_start=(
                    _event_enabled and self.do_event_at_start.isChecked()),
                capture_during_pulsing_every_s=snap_every_s),
        )
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        runner.smoothing_enabled = bool(
            getattr(self, "smoothing_chk", None) is not None
            and self.smoothing_chk.isChecked())
        if getattr(self, "smoothing_window_spin", None) is not None:
            runner.smoothing_window = int(self.smoothing_window_spin.value())
        self._start_runner(
            runner,
            (f"{self._session_stem}.npz" if self._session_stem else
             f"LP_{config.display_name().replace(' ', '_')}.npz"))

    # ------------------------------------------------------------------
    # Crash recovery — resume an interrupted run from its .npz
    # ------------------------------------------------------------------
    def _derive_snap_interval_s(self, captures) -> float:
        """Recover the snapshot cadence from a prior run by the MEDIAN
        spacing of its snapshot captures' scheduled (fixed-grid) times.
        Falls back to the current snapshot-interval spinbox when the saved
        captures predate the time columns."""
        sched = sorted(
            c.metrics.scheduled_time_s for c in captures
            if c.status.notes == "snapshot"
            and math.isfinite(getattr(c.metrics, "scheduled_time_s",
                                      float("nan"))))
        diffs = sorted(b - a for a, b in zip(sched, sched[1:]) if b > a)
        if diffs:
            return diffs[len(diffs) // 2]          # median spacing
        return self._to_seconds(self.snap_int.value(),
                                self.snap_int_unit.currentText())

    def resume_from_npz_clicked(self):
        from pathlib import Path
        from ..persistence import load_session_npz
        if not self._stim or not self._scope:
            QtWidgets.QMessageBox.warning(
                self, "Connect hardware first",
                "Initialize the stimulator and connect the oscilloscope "
                "before resuming a run.")
            return
        if getattr(self, "_start_in_progress", False):
            return
        start_dir = str(self._save_dir) if self._save_dir else ""
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Resume Long-Term Pulsing from .npz", start_dir,
            "PULSAR session (*.npz)")
        if not path:
            return
        path = Path(path)
        try:
            session = load_session_npz(path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self, "Could not load file",
                f"Failed to read {path.name}:\n{type(e).__name__}: {e}")
            return
        if (session.test.experiment or "").upper() != "LP":
            QtWidgets.QMessageBox.warning(
                self, "Not a Long-Term Pulsing run",
                f"{path.name} is a '{session.test.experiment}' session. "
                f"Resume is only supported for Long-Term Pulsing (LP).")
            return
        if not session.runs or not session.runs[-1].captures:
            QtWidgets.QMessageBox.warning(
                self, "Nothing to resume",
                f"{path.name} has no captures to continue from.")
            return
        run = session.runs[-1]
        pattern = session.test.pattern
        if pattern is None or not pattern.phases:
            QtWidgets.QMessageBox.warning(
                self, "No pattern in file",
                "The saved run has no pulse pattern; cannot resume.")
            return
        config = run.configuration
        duration_s = float(session.test.duration_s)
        _el = [c.metrics.elapsed_time_s for c in run.captures
               if math.isfinite(getattr(c.metrics, "elapsed_time_s",
                                        float("nan")))]
        resume_offset = max(_el) if _el else float("nan")
        n_prior = len(run.captures)
        amplitude_ua = (abs(pattern.excitation_phase.amplitude_ua)
                        if pattern.excitation_phase else 0.0)
        snap_every_s = self._derive_snap_interval_s(run.captures)
        off_txt = (f"{resume_offset:.0f}s" if math.isfinite(resume_offset)
                   else "an unknown time (older file without time columns)")
        remaining = (max(0.0, duration_s - resume_offset)
                     if math.isfinite(resume_offset) else duration_s)
        msg = (
            f"Resume Long-Term Pulsing from:\n  {path.name}\n\n"
            f"Prior captures: {n_prior}\n"
            f"Stopped near: {off_txt} of {duration_s:.0f}s total\n"
            f"Continue for ~{remaining:.0f}s more "
            f"(snapshot every {snap_every_s:.0f}s, "
            f"I_stim = {amplitude_ua:.0f} µA, {config.display_name()}).\n\n"
            f"New captures append to this same file. Proceed?")
        if QtWidgets.QMessageBox.question(
                self, "Resume run", msg
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        # The .npz doesn't persist extras; refresh from the current setup
        # so damage warnings / environment are populated for the
        # continuation (same as a fresh LP start).
        self._stamp_extras(session)
        runner = LongPulsingExperiment(
            session, self._stim, self._scope, amplitude_ua=amplitude_ua,
            policy=LongPulsingPolicy(
                duration_s=duration_s,
                characterize_every_s=max(duration_s, 1e9),
                capture_during_pulsing_every_s=snap_every_s),
            resume=True)
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        runner.smoothing_enabled = bool(
            getattr(self, "smoothing_chk", None) is not None
            and self.smoothing_chk.isChecked())
        if getattr(self, "smoothing_window_spin", None) is not None:
            runner.smoothing_window = int(self.smoothing_window_spin.value())
        # No queued configs — resume runs the single loaded run only.
        self._pending_configs = []
        # Frame the scope from the LOADED pattern (the live panel may not
        # match) and append in place to the same file.  Restore the tab's
        # save dir afterwards so a later normal run isn't redirected.
        prev_save_dir = self._save_dir
        self._resume_pattern_override = pattern
        self.set_save_dir(path.parent)
        # Wall-clock CONTINUE banner in the .txt log (operator: "when
        # continuing with LP, add another date and time for continuing").
        try:
            self.log_pane.mark_session_continued("Long-Term Pulsing resume")
        except Exception:
            pass
        self.log_pane.log(
            f"Resuming Long-Term Pulsing from {path.name}: {n_prior} prior "
            f"capture(s); continuing to {duration_s:.0f}s total.")
        try:
            self._start_runner(runner, path.name)
        finally:
            self._resume_pattern_override = None
            self.set_save_dir(prev_save_dir)


# ---------------------------------------------------------------------------
# Progressive Stress tab
# ---------------------------------------------------------------------------
class ProgressiveStressTab(_BaseExperimentTab):
    LOG_TAG = "PS"
    PARAM_LOG_WIDGETS = (
        ("start_ua", "starting current"),
        ("step_ua", "current step"),
        ("t_step", "time per step"),
        ("max_ua", "maximum current"),
        ("sampling_period", "sampling period"),
        ("stop_on_max_current", "stop on maximum current"),
        ("stop_on_compliance", "stop on voltage compliance"),
    )
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
        # ---- Stopping conditions (operator: "let the stopping conditions
        # be choices: maximum current and voltage compliance … and manual
        # stop") ----
        # 1. Maximum current — stop when the ramp reaches the "Maximum
        #    current" value set above.  Toggleable: when OFF the ramp runs
        #    to the PlexStim hardware ceiling (1000 µA) instead.
        self.stop_on_max_current = QtWidgets.QCheckBox(
            "Maximum current (the value set above)")
        self.stop_on_max_current.setChecked(True)
        self.stop_on_max_current.setToolTip(
            "Stop the staircase when it reaches the Maximum current set "
            "in the staircase parameters above. Turn OFF to let the ramp "
            "climb all the way to the PlexStim hardware ceiling "
            f"({int(_max)} µA) instead.")
        # 2. Voltage compliance — V_mon hits the ±12 V rail and the device
        #    can no longer deliver the programmed current.
        self.stop_on_compliance = QtWidgets.QCheckBox(
            "Voltage compliance (±12 V rail)")
        self.stop_on_compliance.setChecked(True)
        self.stop_on_compliance.setToolTip(
            "Halt the ramp when V_mon hits the PlexStim's ±12 V "
            "compliance rail. Past compliance, the device can't "
            "drive the programmed current anymore and ramping "
            "further just wastes time without producing usable "
            "data. Recommended ON for any new electrode.")
        # 3. Manual stop — always available (the Stop button).  Shown for
        #    completeness, checked + disabled so it can't be turned off.
        self.stop_manual = QtWidgets.QCheckBox("Manual stop (always available)")
        self.stop_manual.setChecked(True)
        self.stop_manual.setEnabled(False)
        self.stop_manual.setToolTip(
            "You can always end a run manually with the Stop button — this "
            "is never disabled.")

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
        # (Stopping-condition toggles moved to their own group below.)

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
        # Stopping conditions — the operator chooses which apply.
        stop_box = QtWidgets.QGroupBox("Stop the ramp when…")
        sv = QtWidgets.QVBoxLayout(stop_box)
        sv.setContentsMargins(8, 4, 8, 4); sv.setSpacing(2)
        sv.addWidget(self.stop_on_max_current)
        sv.addWidget(self.stop_on_compliance)
        sv.addWidget(self.stop_manual)
        rv.addWidget(stop_box)
        # Grey the Maximum-current spinbox when its stop choice is off (the
        # ramp then runs to the hardware ceiling), and re-render the
        # staircase so its y-extent reflects the effective ceiling.
        self.stop_on_max_current.toggled.connect(self.max_ua.setEnabled)
        self.stop_on_max_current.toggled.connect(
            lambda *_: self._refresh_staircase())

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

    def _effective_max_ua(self) -> float:
        """The ramp ceiling actually used: the user's Maximum-current value
        when that stop choice is enabled, else the PlexStim hardware limit
        (so the ramp still terminates even with the max-current stop off)."""
        from ..config import STIM_MAX_AMPLITUDE_UA
        if self.stop_on_max_current.isChecked():
            return float(self.max_ua.value())
        return float(STIM_MAX_AMPLITUDE_UA)

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
            max_ua=self._effective_max_ua(),
            rate_hz=rate_hz,
            phase_width_us=phase_w_us,
        )

    def _refresh_preview(self, *_):
        self.pattern_preview.set_pattern(self.pattern_panel.pattern())

    SINGLE_CONFIG = True
    #: PS stresses several channel-disjoint (partial) multipolar combos
    #: sequentially — multi-select but no channel reused as active/return
    #: (operator: "for multipolar, a channel cannot be repeated as active or
    #: return").  Monopolar / Common-Ground stay multi-select as before.
    MULTIPOLAR_NO_REPEAT = True
    PREF_FIELDS = ("start_ua", "step_ua", "t_step", "max_ua",
                   "sampling_period", "stop_on_compliance",
                   "stop_on_max_current")

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
            # warning. Reading the EFFECTIVE ceiling (the user's max when
            # the max-current stop is on, else the hardware limit) gives the
            # correct worst-case for the screen.
            max_ua = self._effective_max_ua()
        except Exception:
            max_ua = float(pattern.excitation_phase.amplitude_ua)
        if not self._pre_run_warning_check(
                pattern, site_area_um2, max_amplitude_ua=max_ua):
            self.log_pane.log("Run cancelled at pre-run damage screen.")
            return
        test = TestParameters(experiment="PS", pattern=pattern, configuration=config,
                              array=self._array)
        session = Session(
            # Setup-tab identity (operator: files + plots carry
            # [notebook]_[session], not the first config) — legacy
            # fallbacks when the Setup fields are blank.
            notebook=self._notebook or "ps_session",
            subject=self._session_subject or config.display_name(),
            test=test)
        self._stamp_extras(session)
        runner = ProgressiveStressExperiment(
            session, self._stim, self._scope,
            policy=StressPolicy(starting_ua=self.start_ua.value(),
                                step_ua=self.step_ua.value(),
                                t_step_s=self.t_step.value(),
                                # Effective ceiling: user's max when that stop
                                # choice is on, else the hardware limit so the
                                # ramp still terminates (operator: stopping
                                # conditions are choices).
                                max_ua=self._effective_max_ua(),
                                sampling_period_s=self.sampling_period.value(),
                                stop_on_voltage_compliance=self.stop_on_compliance.isChecked()),
        )
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        runner.smoothing_enabled = bool(
            getattr(self, "smoothing_chk", None) is not None
            and self.smoothing_chk.isChecked())
        if getattr(self, "smoothing_window_spin", None) is not None:
            runner.smoothing_window = int(self.smoothing_window_spin.value())
        self._start_runner(
            runner,
            (f"{self._session_stem}.npz" if self._session_stem else
             f"PS_{config.display_name().replace(' ', '_')}.npz"))

    # Queue chaining + completion-marking are handled by the base
    # ``_on_finished``; PS just needs ``_start_next_pending`` to build
    # a runner for the next configuration.


# ---------------------------------------------------------------------------
# Galvanostatic EIS tab
# ---------------------------------------------------------------------------
class GalvanostaticEISTab(_BaseExperimentTab):
    """Single-electrode impedance sweep.  A small-signal sine/square current
    is swept over frequency; each point's complex impedance
    ``Z = V_mon/I_mon`` (lock-in) builds a Bode + Nyquist spectrum.  Unlike
    the pulsed tabs there is NO pulse-pattern authoring — the probe is defined
    by amplitude + shape + the frequency sweep — and no water-window ramp."""

    LOG_TAG = "EIS"
    SINGLE_CONFIG = True
    PARAM_LOG_WIDGETS = (
        ("eis_fmin", "min frequency"),
        ("eis_fmax", "max frequency"),
        ("eis_ppd", "points per decade"),
        ("eis_amp", "probe amplitude"),
        ("eis_shape", "probe shape"),
        ("eis_mode", "optimize for"),
    )

    def __init__(self, array, parent=None):
        super().__init__(array, parent)
        from ..config import STIM_MAX_AMPLITUDE_UA
        from ..waveforms import SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR
        from ..experiments.galvanostatic_eis import (
            EIS_MODE_LABELS, eis_frequency_limits)
        from .eis_plots import BodePlot, NyquistPlot

        self._eis_caps: list = []            # accumulates for the spectrum plots

        # --- Frequency sweep ---
        self.eis_fmin = RepeatingDoubleSpinBox()
        self.eis_fmin.setRange(0.1, 1e6); self.eis_fmin.setDecimals(2)
        self.eis_fmin.setValue(1.0); self.eis_fmin.setSuffix(" Hz")
        self.eis_fmin.setToolTip(
            "Lowest probe frequency.  Low frequencies are slow (a 1 Hz "
            "point takes seconds); the true floor is time, not resolution.")
        self.eis_fmax = RepeatingDoubleSpinBox()
        self.eis_fmax.setRange(0.1, 1e6); self.eis_fmax.setDecimals(0)
        self.eis_fmax.setValue(100_000.0); self.eis_fmax.setSuffix(" Hz")
        self.eis_fmax.setToolTip(
            "Highest probe frequency.  CLAMPED to what the 1 µs stimulator "
            "resolution can render for the chosen shape — ~125 kHz sine, "
            "500 kHz square.")
        self.eis_ppd = RepeatingSpinBox()
        self.eis_ppd.setRange(1, 30); self.eis_ppd.setValue(10)
        self.eis_ppd.setSuffix(" /decade")
        self.eis_ppd.setToolTip("Log-spaced points per frequency decade.")

        # --- Probe ---
        self.eis_amp = RepeatingDoubleSpinBox()
        self.eis_amp.setRange(0.1, STIM_MAX_AMPLITUDE_UA)
        self.eis_amp.setDecimals(1); self.eis_amp.setValue(10.0)
        self.eis_amp.setSuffix(" µA")
        self.eis_amp.setToolTip(
            "Small-signal probe current amplitude.  Keep small so the "
            "electrode stays in its linear regime (this is not a "
            "water-window ramp).")
        self.eis_shape = QtWidgets.QComboBox()
        self.eis_shape.addItem("Sinusoidal", userData=SHAPE_SINUSOIDAL)
        self.eis_shape.addItem("Square", userData=SHAPE_RECTANGULAR)
        self.eis_shape.setToolTip(
            "Probe waveform.  Sinusoidal = clean single-bin impedance "
            "(≤ ~125 kHz on the 1 µs grid).  Square = the Cui-paper probe; "
            "renders cleanly to 500 kHz but carries odd harmonics.")
        self.eis_shape.currentIndexChanged.connect(self._on_eis_shape_changed)

        # --- Gamry-style "Optimize for" ---
        self.eis_mode = QtWidgets.QComboBox()
        for code in ("fast", "normal", "low_noise"):
            self.eis_mode.addItem(EIS_MODE_LABELS[code], userData=code)
        self.eis_mode.setCurrentIndex(1)     # Normal
        self.eis_mode.setToolTip(
            "Measurement quality (Gamry 'Optimize for').  Sets the minimum "
            "cycles integrated per point + settle: <b>Fast</b> (fewest "
            "cycles — poor cell stability / low well-defined Z), "
            "<b>Normal</b> (high-Z or noisy), <b>Low Noise</b> (most "
            "cycles, best data, slowest).")

        self._eis_limit_lbl = QtWidgets.QLabel()
        self._eis_limit_lbl.setStyleSheet("color: palette(windowText);")
        self._eis_limit_lbl.setWordWrap(True)

        params_box = QtWidgets.QGroupBox(
            "Galvanostatic Electrochemical Impedance Spectroscopy parameters")
        form = QtWidgets.QFormLayout(params_box)
        form.addRow("Min frequency:", self.eis_fmin)
        form.addRow("Max frequency:", self.eis_fmax)
        form.addRow("Density:", self.eis_ppd)
        form.addRow("Probe amplitude:", self.eis_amp)
        form.addRow("Probe shape:", self.eis_shape)
        form.addRow("Optimize for:", self.eis_mode)
        form.addRow(self._eis_limit_lbl)

        # Spectrum plots — built BEFORE _assemble_pages (which calls
        # _extra_experiment_tabs).
        self.bode_plot = BodePlot()
        self.nyquist_plot = NyquistPlot()

        self._assemble_pages(params_box)
        self._on_eis_shape_changed()

    def experiment_type(self) -> str:
        return "EIS"

    def _extra_experiment_tabs(self):
        return (("Bode", self.bode_plot), ("Nyquist", self.nyquist_plot))

    def _on_eis_shape_changed(self, *_):
        from ..experiments.galvanostatic_eis import eis_frequency_limits
        shape = self.eis_shape.currentData()
        _lo, hi = eis_frequency_limits(shape)
        self._eis_limit_lbl.setText(
            f"Renderable ≤ {hi/1000:g} kHz for this shape (1 µs grid). "
            f"Higher requests are clamped; near the top the grid is coarse.")

    def _on_capture(self, capture, channel):
        super()._on_capture(capture, channel)
        # Feed the Bode/Nyquist from the run's accumulated impedance points.
        try:
            from ..experiments.galvanostatic_eis import eis_spectrum
            self._eis_caps.append(capture)
            s = eis_spectrum(self._eis_caps)
            args = (s["freq_hz"], s["z_mag_ohm"], s["z_phase_deg"],
                    s["z_real_ohm"], s["z_imag_ohm"])
            self.bode_plot.set_spectrum(*args)
            self.nyquist_plot.set_spectrum(*args)
        except Exception:
            pass

    def _representative_probe(self, shape, amp_ua):
        """A nominal probe pattern (1 kHz) for the saved TestParameters
        metadata + the base class's pre-run / scope setup.  The runner builds
        its own per-frequency patterns."""
        from ..waveforms import Phase
        return PulsePattern(phases=[
            Phase(amplitude_ua=-abs(amp_ua), width_us=500.0, shape=shape,
                  delay_after_us=0.0),
            Phase(amplitude_ua=+abs(amp_ua), width_us=500.0, shape=shape,
                  delay_after_us=0.0),
        ], rate_hz=1000.0)

    def start_clicked(self):
        if not self._stim or not self._scope:
            return
        configs = self.combo_panel.selected_configurations()
        if not configs:
            QtWidgets.QMessageBox.warning(
                self, "No configuration selected",
                "Click an electrode to pick the channel to sweep.")
            return
        config = configs[0]          # SINGLE_CONFIG
        from ..experiments.galvanostatic_eis import (
            GalvanostaticEISExperiment, GalvanostaticEISPolicy)
        shape = self.eis_shape.currentData()
        amp = float(self.eis_amp.value())
        policy = GalvanostaticEISPolicy(
            freq_min_hz=float(self.eis_fmin.value()),
            freq_max_hz=float(self.eis_fmax.value()),
            points_per_decade=int(self.eis_ppd.value()),
            amplitude_ua=amp, probe_shape=shape,
            mode=self.eis_mode.currentData())
        probe = self._representative_probe(shape, amp)
        test = TestParameters(experiment="EIS", pattern=probe,
                              configuration=config, array=self._array,
                              duration_s=0.0)
        session = Session(notebook=self._notebook or "eis_session",
                          subject=self._session_subject or config.display_name(),
                          test=test)
        self._stamp_extras(session)
        save_name = (f"{self._session_stem}.npz" if self._session_stem else
                     f"EIS_{config.display_name().replace(' ', '_')}.npz")
        runner = GalvanostaticEISExperiment(session, self._stim, self._scope,
                                            policy=policy)
        runner.trigger_source = self._trigger_source
        runner.trigger_is_digital = self._trigger_is_digital
        # Fresh spectrum for this run.
        self._eis_caps = []
        try:
            self.bode_plot.clear(); self.nyquist_plot.clear()
        except Exception:
            pass
        self._start_runner(runner, save_name)

    def current_prefs(self) -> dict:
        out = super().current_prefs()
        out["eis_shape"] = self.eis_shape.currentData()
        out["eis_mode"] = self.eis_mode.currentData()
        return out

    def restore_prefs(self, p: dict):
        super().restore_prefs(p)
        p = p or {}
        shp = p.get("eis_shape")
        if shp is not None:
            i = self.eis_shape.findData(shp)
            if i >= 0:
                self.eis_shape.setCurrentIndex(i)
        md = p.get("eis_mode")
        if md is not None:
            i = self.eis_mode.findData(md)
            if i >= 0:
                self.eis_mode.setCurrentIndex(i)
        self._on_eis_shape_changed()
