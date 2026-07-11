"""Long-Term Pulsing (LP) with customizable periodic characterization.

Use case: pulse an electrode at a fixed safe amplitude for hours-to-days
while periodically re-measuring the full set of voltage-transient metrics so
we can track drift in ``Q_inj`` / ``V_d`` / ``R_a`` / ``E_pol`` / ``C_d``
over the lifetime of the experiment.

Algorithm
---------
The runner has two interleaved schedules:

1. **Snapshots** (``capture_during_pulsing_every_s``, default 30 s):
   between characterizations we grab a single averaged scope frame so the
   GUI can show the current waveform shape. These captures are tagged
   ``snapshot`` in their ``status.notes``.
2. **Re-characterization** (``characterize_every_s``, default 600 s):
   pulsing pauses, we hand control to a transient
   :class:`VoltageTransientExperiment` running a short ramp on the same
   channel, then resume the working amplitude. These captures are tagged
   ``char@<seconds>s`` so the GUI can plot drift trends from them.

Both schedules write into the same :class:`ChannelRun`, so the saved .npz
contains a single time-ordered list of captures with mixed snapshot/char
entries — the analysis layer separates them by the ``notes`` tag.

Wrapping VT for the periodic measurement keeps the metrics computation
identical between modes: a "Q_inj at t=0" and a "Q_inj at t=12h" come from
exactly the same code path.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


from ..hardware.base import Oscilloscope, Stimulator
from ..hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from ..metrics import compute_metrics
from ..readback_calibration import make_capture
from ..session import Capture, ChannelRun, Session
from ..waveforms import PulsePattern
from .base import ExperimentEvent, ExperimentResult, ExperimentRunner
from .voltage_transient import RampPolicy, VoltageTransientExperiment


@dataclass
class LongPulsingPolicy:
    duration_s: float = 3600.0          # total pulsing time
    characterize_every_s: float = 600.0  # how often to re-characterize
    characterize_steps: int = 6         # number of capture points within each char window
    capture_during_pulsing_every_s: float = 30.0  # quick V_mon snapshots between chars
    # ---- Periodic max-VT / pause (potentiostat window) — operator #81 ----
    # "There should have been an option in LP to periodically capture
    # maximum charge injection capacity and/or pause (separate from the
    # snapshot), allowing for other measurements such as potentiostat …
    # The snapshot and periodic maximum VT/pause should be at the start of
    # LP."  The PERIOD is ``characterize_every_s`` (a finite value enables
    # the periodic event; the tab leaves it huge to disable).  At each
    # period the runner optionally (a) runs a max-Q_inj VT sweep
    # (``run_max_vt``) and/or (b) STOPS stimulation for ``pause_duration_s``
    # so the operator can run an external instrument, then resumes pulsing.
    run_max_vt: bool = True          # run the char (max-VT) sweep each period
    pause_duration_s: float = 0.0    # >0 → pause stimulation this long each period
    # Fire the FIRST periodic event at t=0 (a baseline max-VT + potentiostat
    # window at the start) instead of only after the first full period.
    fire_periodic_at_start: bool = False
    # Memory checkpoint: drop the raw V_mon / I_mon / E_act / E_ret
    # sample arrays from snapshot captures after metrics are extracted.
    # Metrics are tiny (a few floats); the arrays are 2k-20k samples
    # × float64 × 5 channels ≈ 600 KB-2 MB per snapshot, and a 30 min
    # run produces ~60 snapshots ⇒ ~120 MB of stale waveforms.
    # Setting this to True (default) keeps the captures' metric output
    # but drops the underlying samples once they're no longer needed.
    # Characterization captures are NEVER trimmed (they're the
    # reference waveforms for drift analysis).
    trim_snapshot_arrays: bool = True


class LongPulsingExperiment(ExperimentRunner):

    # Drift-characterization sub-VT starts at this fraction of the pulsing
    # amplitude (the direct replacement for the removed RampPolicy.starting_ua
    # = amplitude_ua * 0.2) — see _characterize, which scales the char base
    # pattern by this before handing it to the VT sub-runner.
    _CHAR_START_FRAC = 0.2

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope, *,
                 amplitude_ua: float,
                 policy: Optional[LongPulsingPolicy] = None,
                 ramp: Optional[RampPolicy] = None,
                 pulse_channels: Optional[List[int]] = None,
                 resume: bool = False):
        super().__init__(session, stimulator, oscilloscope)
        self.amplitude_ua = amplitude_ua
        # Channels that pulse the REAL pattern SIMULTANEOUSLY (chronic
        # multi-channel stimulation — operator: "LP is not pulsing all of
        # the channels in monopolar like I selected").  Defaults to just
        # the config's active channel (the classic single-channel LP).
        # The scope monitors ``config.active`` (the primary), so snapshots
        # + metrics track that one representative channel while EVERY
        # channel in this set delivers current.  Only meaningful for
        # MONOPOLAR (each channel returns through the shared counter
        # electrode); the tab passes it only for an all-monopolar
        # selection.
        self.pulse_channels: Optional[List[int]] = (
            [int(c) for c in pulse_channels] if pulse_channels else None)
        # Crash-recovery: when True, CONTINUE the last ChannelRun already
        # present in ``session`` (loaded from a partial .npz) instead of
        # starting a fresh run — appending new captures after the prior
        # ones, with the snapshot/char cadence + duration budget resumed
        # at the last capture's elapsed (pulsing) time.  Operator: "I
        # want to be able to continue or near where a NPZ file stopped,
        # in case the program or computer crashes."  See run() for the
        # offset derivation (uses CaptureMetrics.elapsed_time_s, with a
        # snapshot-count fallback for legacy archives that lack it).
        self.resume = bool(resume)
        self.policy = policy or LongPulsingPolicy(duration_s=session.test.duration_s)
        # RampPolicy no longer carries a starting amplitude — the VT ramp
        # starts at its base pattern's amplitude.  Long Pulsing's drift
        # characterization wants to start LOW (historically 20 % of the
        # pulsing amplitude) and ramp up, so ``_characterize`` hands the sub-VT
        # a base pattern pre-scaled to ``_CHAR_START_FRAC`` (below); the
        # coarse/fine step sizes stay proportional to the pulsing amplitude.
        self.ramp = ramp or RampPolicy(coarse_step_ua=amplitude_ua * 0.2,
                                       fine_step_ua=amplitude_ua * 0.05)

        if isinstance(self.scope, SimulatedOscilloscope) \
                and isinstance(self.stim, SimulatedStimulator):
            self.scope.bind_stimulator(self.stim)

    # --------------------------------------------------------------
    def run(self) -> ExperimentResult:
        self.preflight()
        # NOTE: the historical ``self.stim.reinit()`` at the top of
        # run() was REMOVED — see voltage_transient.run() for the
        # full rationale (CLAUDE.md gotcha #29b + #31).  Short
        # version: the GUI Stop / Start lifecycle now guarantees a
        # fresh device, and the reinit cascade was the second
        # ``ps_close_all_stim`` in a chain that HEAP_CORRUPT'd the
        # vendor DLL.  Stale pattern bytes from a previous run are
        # overwritten by ``load_channel`` before stim starts.
        config = self.session.test.configuration
        # MULTICHANNEL MONOPOLAR MONITORING (operator: "I tried pulsing all of
        # the channels in monopolar … but only CH01 was plotted").  The
        # PlexStim has a SINGLE monitor pickoff, so LP historically monitored
        # only ``config.active`` while every pulse channel delivered current
        # (gotcha #105).  To PLOT each channel, we now cycle
        # ``set_monitor_channel`` across the pulse set once per snapshot and
        # give each channel its OWN ChannelRun (a monopolar Configuration), so
        # every capture routes to its own plot page — keyed by
        # ``run.configuration.display_name()`` in the worker's _on_event,
        # exactly like VT's per-channel pages.  Single-channel LP (and resume)
        # keep the unchanged one-run path.
        pulse = [int(c) for c in self._pulse_set(config)]
        monitor_all = len(pulse) > 1 and not self.resume
        if len(pulse) > 1 and self.resume:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=(f"Multichannel LP resume monitors the primary "
                         f"CH{int(config.active):02d} only (per-channel resume "
                         "of a multichannel run is unsupported); all selected "
                         "channels still pulse.")))
        if self.resume and self.session.runs:
            # CONTINUE the run loaded from the partial .npz — append new
            # captures to its existing list so the final save holds the
            # full history (pre- and post-crash).  Re-open it (clear
            # finished_at) so the end-of-run stamp reflects this session.
            run = self.session.runs[-1]
            run.finished_at = None
            ch_runs = {int(config.active): run}
            self._emit(ExperimentEvent(
                kind="log", session=self.session, run=run,
                message=f"Resuming Long-Term Pulsing — {len(run.captures)} "
                        f"prior capture(s) loaded."))
        elif monitor_all:
            from ..electrode import Configuration
            ch_runs = {}
            for ch in pulse:
                cfg_ch = (config if ch == int(config.active)
                          else Configuration.monopolar(ch))
                r_ch = ChannelRun(
                    configuration=cfg_ch,
                    surface_area_um2=self.session.test.array[ch].surface_area_um2)
                self.session.add_run(r_ch)
                ch_runs[ch] = r_ch
            run = ch_runs[int(config.active)]   # primary — char / dose / result
        else:
            run = ChannelRun(
                configuration=config,
                surface_area_um2=self.session.test.array[config.active].surface_area_um2)
            self.session.add_run(run)
            ch_runs = {int(config.active): run}
        # Channels to CAPTURE each snapshot round (cycle the monitor across
        # them).  Multichannel = the full pulse set; else just the primary.
        monitor_channels = pulse if monitor_all else [int(config.active)]
        for r_ch in ch_runs.values():
            self._emit(ExperimentEvent(kind="run_start", session=self.session, run=r_ch))

        base = self.session.test.pattern
        # Build the fixed-amplitude pulsing pattern.  ``_pattern_at_amplitude``
        # is identical to ``base.scaled(self.amplitude_ua / |excitation|)`` for
        # a normal template, but also GROWS a zero-amplitude template (plain
        # scaling can't grow a zero — it would pulse at 0 µA regardless of the
        # requested amplitude).  See base.py.
        pattern = self._pattern_at_amplitude(base, self.amplitude_ua)
        # MATLAB setDefaultScopeView3.m: revert to default per-channel
        # scope view at the start of every channel (single-channel run
        # here, so this fires once).
        self.apply_default_scope_view(
            pattern, amp_ua=self.amplitude_ua,
            reason=f"start of channel {config.active}")
        # Arm closed-loop bias feedback if the GUI pushed a controller
        # onto this runner.  Must happen AFTER apply_default_scope_view
        # so the controller's MEASUrement-gating SCPI writes aren't
        # clobbered.  We disarm + re-arm around each ``_characterize``
        # sub-VT below — the sub-runner's own apply_default_scope_view
        # would otherwise tear down the gating window.  No-op when no
        # controller is attached.
        self.arm_bias_feedback()

        try:
            # Monitor the PRIMARY (config.active) channel — the scope has
            # one monitor pickoff, so snapshots + metrics track this one
            # representative channel while every channel in
            # ``_pulse_set()`` delivers current.
            self.stim.set_monitor_channel(config.active)
            self._load_pulse_and_zero(pattern, config)
            # MONOPOLAR commit (config.returns empty) → ONE
            # PS_LoadAllChannels; multipolar no-op (MATLAB loadPattern.m
            # parity — see ExperimentRunner.commit_loaded_channels).
            self.commit_loaded_channels(config)
            # PS_StartStimAllChannels — single-channel start fails with
            # WRONG-TRIGGER-MODE on default-mode PlexStim devices.
            # Every pulse channel + the unused-zero channels fire together.
            self.stim.start_all()
        except Exception as e:
            self._emit(ExperimentEvent(kind="aborted", session=self.session, run=run,
                                       message=f"Stim load failed: {e}"))
            return ExperimentResult(session=self.session, aborted=True, error=str(e))

        pulse_period_s = 1.0 / pattern.rate_hz
        navg = getattr(self.scope, "_expected_acq_navg", None) or 8
        snap_interval = max(self.policy.capture_during_pulsing_every_s, 1e-3)
        char_interval = self.policy.characterize_every_s
        # MONOTONIC pulsing-time anchor.  ``time.monotonic()`` (NOT
        # ``time.time()``) so the cadence can't jump on an NTP step /
        # DST change — the equivalent of MATLAB's ``tic``/``toc``.  The
        # anchor is shifted forward by each re-characterization window's
        # duration below (port of runLongPulsing.m
        # ``startTime = startTime + endPause``), so both the snapshot
        # cadence AND the ``duration_s`` budget measure PULSING time,
        # excluding the paused char windows.  (``t_start`` itself is set
        # just below, offset by any resume time.)
        # FIXED snapshot grid: the next snapshot AIMS for
        # ``snap_idx · snap_interval`` pulsing-seconds (NOT
        # ``now + interval``), so a slow capture or a char window can't
        # make the cadence drift — this is the "keep the right sampling
        # despite not using rateControl" behaviour (rateControl did NOT
        # keep precise time).  Port of runLongPulsing.m
        # ``timestamp_arr = 0:periodicTime:pulsingTime`` checked against
        # ``toc(startTime)``.
        # Resume offset: the pulsing time already delivered before the
        # crash, taken from the last loaded capture's elapsed_time_s
        # (the precise pulsing clock).  The grid + duration budget pick
        # up from there, so a 1 h run that died at 22 min continues for
        # the remaining ~38 min on the same cadence.  Legacy archives
        # (no elapsed_time_s) fall back to snapshot-count × interval.
        resume_offset = 0.0
        idx = 0
        if self.resume and run.captures:
            idx = len(run.captures)
            _el = [c.metrics.elapsed_time_s for c in run.captures
                   if math.isfinite(getattr(c.metrics, "elapsed_time_s",
                                            float("nan")))]
            if _el:
                resume_offset = max(_el)
            else:
                _n_snap = sum(1 for c in run.captures
                              if c.status.notes == "snapshot")
                resume_offset = _n_snap * snap_interval
            self._emit(ExperimentEvent(
                kind="log", session=self.session, run=run,
                message=f"Resuming cadence at t = {resume_offset:.0f}s "
                        f"(pulsing); {self.policy.duration_s - resume_offset:.0f}s "
                        f"remaining of {self.policy.duration_s:.0f}s."))
        # Anchor the monotonic clock so ``monotonic() - t_start`` already
        # reads ``resume_offset`` — the existing loop logic then needs no
        # resume special-casing.
        t_start = time.monotonic() - resume_offset
        # First snapshot after resume lands on the NEXT grid point strictly
        # after the resume offset (skip already-covered slots).
        snap_idx = (int(resume_offset / snap_interval) + 1
                    if resume_offset > 0 else 0)
        next_snapshot_at = snap_idx * snap_interval
        if char_interval and char_interval > 0 and math.isfinite(char_interval):
            if resume_offset > 0:
                char_count = int(resume_offset / char_interval)
                next_char_at = (char_count + 1) * char_interval
            elif getattr(self.policy, "fire_periodic_at_start", False):
                # Baseline periodic event (max-VT + potentiostat pause) at
                # t=0 (operator: "at the start of LP").
                char_count = 0
                next_char_at = 0.0
            else:
                char_count = 0
                next_char_at = char_interval
        else:
            char_count = 0
            next_char_at = float("inf")
        # Force a scope-averager reset on the first capture of THIS run
        # segment (decoupled from idx, which is non-zero after a resume).
        first_snap = True
        # Most-recent untrimmed snapshot PER MONITORED CHANNEL — kept so its
        # waveform renders on the live plot; the one before it (same channel)
        # gets trimmed (see _capture_and_emit_snapshot).
        self._prev_snapshot_caps = {int(ch): None for ch in monitor_channels}
        # Per-channel capture index → make_capture index / the page's
        # per-capture dropdown.  Resume seeds the primary from its already-
        # loaded captures; every other channel starts at 0.
        ch_idx = {int(ch): len(ch_runs[int(ch)].captures)
                  for ch in monitor_channels}
        try:
            while not self.aborted:
                # User PAUSE checkpoint — LP pulses CONTINUOUSLY, so halt +
                # restart pulsing (start_all resumes the RETAINED patterns on
                # every pulsed channel).  Exclude the paused wall-clock from the
                # pulsing-time anchor so the pause doesn't count against
                # ``duration_s`` (same compensation as the char window below).
                if not self.wait_if_paused(restart=lambda: self.stim.start_all()):
                    break
                if self._last_pause_duration_s > 0:
                    t_start += self._last_pause_duration_s
                elapsed = time.monotonic() - t_start
                if elapsed >= self.policy.duration_s:
                    break

                # Periodic event: max-VT characterization (optional) +
                # potentiostat pause (optional).  Operator #81 — "This
                # should come after the snapshot": the snapshot grid usually
                # coincides, and a t=0 event follows the t=0 snapshot below
                # in the same tick after this returns.
                if elapsed >= next_char_at:
                    # Mark the wall-clock start of the (pulsing-paused)
                    # event window (char + pause) so we can subtract its
                    # whole duration from the pulsing-time anchor below.
                    char_t0 = time.monotonic()
                    # Disarm bias feedback BEFORE the sub-VT runs —
                    # the sub-runner's own apply_default_scope_view +
                    # per-amplitude scope writes would otherwise clobber
                    # the gating-window state the controller depends on.
                    # We re-arm below AFTER the post-event restore.
                    self.disarm_bias_feedback()
                    _char_aborted = False
                    # (a) Periodic max-Q_inj VT sweep (optional).
                    if getattr(self.policy, "run_max_vt", True):
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session, run=run,
                            message=f"Periodic max-VT at t = {elapsed:.0f}s "
                                    f"(pulsing time)"))
                        _char_aborted = self._characterize(
                            run, base_pattern=base, t_offset_s=elapsed)
                    # (b) Potentiostat pause window (optional) — stop
                    # stimulation so the operator can run an external
                    # instrument, then resume.  Skipped if the max-VT above
                    # aborted / the user aborted.
                    _pause_s = float(getattr(self.policy, "pause_duration_s", 0.0))
                    if (not _char_aborted and not self.aborted
                            and _pause_s > 0.0):
                        self._pause_window(run, _pause_s, elapsed)
                    # If the user aborted DURING the event, propagate the
                    # abort to the outer pulsing loop.
                    if _char_aborted or self.aborted:
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session, run=run,
                            message=("Periodic max-VT / pause aborted — "
                                     "ending long-pulsing run.")))
                        break
                    # Pause compensation: shift the pulsing-time anchor
                    # forward by the char window's wall-clock duration so
                    # the char window is EXCLUDED from pulsing time
                    # (MATLAB ``startTime = startTime + endPause``).  The
                    # snapshot grid + duration budget then ignore it.
                    t_start += time.monotonic() - char_t0
                    char_count += 1
                    # Next char on the fixed pulsing-time grid.
                    next_char_at = (char_count + 1) * char_interval
                    # Restart pulsing at the working amplitude.  The
                    # inline VT sub-run (and its per-step reinit /
                    # load_channel calls) may have left the unused
                    # channels in an unknown state.
                    #
                    # Explicit ``stop_all`` BEFORE the load: the sub-VT's
                    # outer finally already stopped, but its finally may
                    # not have run if the sub-VT aborted mid-stream.
                    # ``PS_LoadChannel`` is ~50-200 ms for an arb
                    # pattern; we want a quiescent device during that
                    # window rather than running whatever the sub-VT
                    # left on the channels.  Matches MATLAB
                    # ``stopStimulation`` before ``setPattern``.
                    try:
                        self.stim.stop_all()
                    except Exception:
                        pass
                    # Re-load the real pattern on EVERY pulse channel + zero
                    # on the unused ones so they tick in cadence again
                    # (port of MATLAB setPattern.m Zero Current).
                    self._load_pulse_and_zero(pattern, config)
                    # MONOPOLAR commit (config.returns empty) → ONE
                    # PS_LoadAllChannels; multipolar no-op.
                    self.commit_loaded_channels(config)
                    self.stim.start_all()  # PS_StartStimAllChannels
                    # Restore default scope view — the inline VT
                    # called ``apply_default_scope_view`` which wiped
                    # the adapt history and may have changed scales.
                    # Without this the first post-char snapshot can
                    # look discontinuous from pre-char data.
                    try:
                        self.apply_default_scope_view(
                            pattern, amp_ua=self.amplitude_ua,
                            reason="post-recharacterization restore")
                    except Exception:
                        pass
                    # Re-arm closed-loop feedback AFTER the scope-view
                    # restore so the controller's gating SCPI writes
                    # take precedence over the post-restore state.
                    # Mirror image of the disarm before _characterize.
                    self.arm_bias_feedback()
                    # The char window (now excluded from pulsing time)
                    # advanced the wall clock; recompute pulsing-elapsed
                    # and skip the snapshot grid PAST any slots that the
                    # window straddled, so we resume on the next clean
                    # grid point instead of firing a catch-up burst.
                    elapsed = time.monotonic() - t_start
                    snap_idx = int(elapsed // snap_interval) + 1
                    next_snapshot_at = snap_idx * snap_interval

                # Lightweight snapshot capture — cycle the monitor pickoff
                # across EVERY pulse channel so each gets its OWN plot page
                # (operator: "pulsing all of the channels … but only CH01 was
                # plotted").  Single-channel = one pass, unchanged.
                if elapsed >= next_snapshot_at:
                    scheduled_s = next_snapshot_at   # cadence-grid target
                    for ch in monitor_channels:
                        # A monitor switch leaves the averager holding the
                        # PREVIOUS channel's frames, so every multichannel grab
                        # must flush; single-channel only flushes on the very
                        # first snapshot (unchanged behaviour).
                        reset = True if len(monitor_channels) > 1 else first_snap
                        self._capture_and_emit_snapshot(
                            ch, ch_runs[ch], ch_idx[ch], pattern=pattern,
                            navg=navg, pulse_period_s=pulse_period_s,
                            scheduled_s=scheduled_s, t_start=t_start,
                            reset_before_run=reset)
                        ch_idx[ch] += 1
                        if self.aborted:
                            break
                    first_snap = False
                    # Closed-loop bias step at snapshot cadence (once per
                    # round; operates on the primary monitored channel).
                    self.bias_step_if_armed()
                    idx += 1
                    # Advance the fixed grid by one interval.  A multichannel
                    # round takes ~N× a single capture, so it usually overruns
                    # ≥1 slot — snap to the next grid point strictly after the
                    # current pulsing-elapsed instead of firing a catch-up
                    # burst.
                    snap_idx += 1
                    next_snapshot_at = snap_idx * snap_interval
                    elapsed_after = time.monotonic() - t_start
                    if next_snapshot_at <= elapsed_after:
                        snap_idx = int(elapsed_after // snap_interval) + 1
                        next_snapshot_at = snap_idx * snap_interval
                time.sleep(0.005)
        finally:
            # Disarm bias feedback FIRST so the controller's scope-
            # gating teardown happens before the stim quiets — keeps
            # the order consistent with how we armed it (arm AFTER
            # scope view, disarm BEFORE stim stop).  Finally-safe +
            # idempotent + swallows controller failures so a teardown
            # SCPI error doesn't mask the run's actual error.  Also
            # covers the case where the outer ``break`` fired (e.g.
            # aborted during ``_characterize``) and we never re-armed.
            self.disarm_bias_feedback()
            # ``stop_all`` (= PS_StopStimAllChannels) — matches MATLAB
            # ``stopStimulation`` and quiets both the active channel
            # AND the unused zero-amplitude channels that were brought
            # up alongside it via ``start_all``.
            try:
                self.stim.stop_all()
            except Exception:
                pass

        _end = datetime.now()
        # Close EVERY per-channel run (multichannel) so each channel/combo
        # ticks ✓ in the entry list; single-channel closes the sole run.
        for r_ch in ch_runs.values():
            r_ch.finished_at = _end
            self._emit(ExperimentEvent(kind="run_end", session=self.session, run=r_ch))
        self._emit(ExperimentEvent(kind="session_end", session=self.session))
        return ExperimentResult(session=self.session, captures=run.captures,
                                aborted=self.aborted)

    # --------------------------------------------------------------
    def _capture_and_emit_snapshot(self, ch, ch_run, cap_idx, *, pattern,
                                   navg, pulse_period_s, scheduled_s,
                                   t_start, reset_before_run):
        """Capture ONE monitored channel and emit it against ``ch_run``.

        Switches the stim monitor pickoff to ``ch`` (the PlexStim has a single
        pickoff), grabs + fit-the-view-rescales a snapshot, computes metrics,
        appends to ``ch_run`` and emits a ``capture`` event keyed to that
        channel's configuration so it lands on its OWN plot page.  Shared by
        the single-channel and the multichannel-monopolar snapshot paths
        (single-channel = one call per snapshot round).  Trims the PREVIOUS
        snapshot of the SAME channel (per-channel memory bound)."""
        self.stim.set_monitor_channel(int(ch))
        acq = self.scope.capture_while_running(
            wait_s=navg * pulse_period_s, reset_before_run=reset_before_run)
        # SHARED fit-the-view rescale loop (same coarse/fine scaling +
        # positioning as VT).  Recapture = another continuous-run grab whose
        # sleep IS the fresh-frame settle; converged stationary signal → one
        # pass, zero writes — snapshot cadence unaffected.
        acq = self.rescale_to_fit(
            acq, pattern=pattern,
            recapture=lambda _t: self.scope.capture_while_running(
                wait_s=navg * pulse_period_s),
            timeout_s=navg * pulse_period_s + 6.0,
            context=f"(snapshot #{cap_idx + 1} CH{int(ch):02d})")
        self._smooth_acquisition(acq)
        cap = make_capture(cap_idx, pattern, acq, self.scope, self.stim,
                           cal=self.cal, channel=int(ch))
        compute_metrics(cap, ch_run.surface_area_um2)
        # Precise-sampling columns: the cadence-grid target + the actual
        # pulsing-elapsed when THIS channel's grab landed.
        cap.metrics.scheduled_time_s = scheduled_s
        cap.metrics.elapsed_time_s = time.monotonic() - t_start
        # Feed E_ret rest values into the electrode-potential learning bin.
        try:
            from ..electrode_potential_history import record_capture
            record_capture(cap, self.session)
        except Exception:
            pass
        # Per-capture damage warning, posture-aware.
        try:
            from ..damage_warnings import assess_finished_capture
            snap = (self.session.test.extras or {}).get("setup_snapshot") or {}
            env_short = (snap.get("environment_short")
                         if isinstance(snap, dict) else None) or "pbs"
            warn = assess_finished_capture(cap, environment_short=env_short)
            if warn is not None:
                self._emit(ExperimentEvent(
                    kind="log", session=self.session, capture=cap,
                    message=f"{warn.title}\n{warn.body}"))
        except Exception:
            pass
        cap.status.notes = "snapshot"
        ch_run.captures.append(cap)
        # Nonparametric access-resistance drift monitor over THIS channel's
        # own run (Mann-Whitney vs its earlier R_a; warn-only).  Reads the R_a
        # METRIC, so it survives the array-trim below.
        self.check_access_resistance_drift(ch_run)
        self._emit(ExperimentEvent(kind="capture", session=self.session,
                                   run=ch_run, capture=cap))
        # Memory checkpoint — keep THIS channel's LATEST snapshot untrimmed
        # (so its waveform renders live) and trim its previous one.  Bounds
        # memory to one untrimmed frame PER monitored channel.
        if getattr(self.policy, "trim_snapshot_arrays", True):
            prev = self._prev_snapshot_caps.get(int(ch))
            if prev is not None:
                try:
                    prev.v_mon_v = None
                    prev.i_mon_ua = None
                    prev.e_act_v = None
                    prev.e_ret_v = None
                    prev.time_us = None
                except Exception:
                    pass
            self._prev_snapshot_caps[int(ch)] = cap
        return cap

    # --------------------------------------------------------------
    def _pulse_set(self, config) -> List[int]:
        """The channels that pulse the REAL pattern this run.

        ``pulse_channels`` when the tab passed a multi-channel monopolar
        selection, otherwise just ``config.active`` (classic single-channel
        LP).  Always includes ``config.active`` (the monitored primary).
        """
        primary = int(getattr(config, "active", 0))
        if self.pulse_channels:
            chs = list(dict.fromkeys(self.pulse_channels))   # de-dup, ordered
            if primary and primary not in chs:
                chs.insert(0, primary)
            return chs
        return [primary]

    def _load_pulse_and_zero(self, pattern: PulsePattern, config) -> None:
        """Load the real ``pattern`` on every pulse channel (reps=0 =
        continuous) and a zero-amplitude copy on the remaining unused
        channels.  Returns stay unloaded (passive sink).  Shared by the
        initial start and the post-characterization restart."""
        pulse = self._pulse_set(config)
        for ch in pulse:
            self.stim.load_channel(ch, pattern)
            try:
                self.stim.set_repetitions(ch, 0)   # infinite until stopped
            except Exception:
                pass
        # Zero every channel that is NOT pulsing + NOT a multipolar return.
        self.load_zero_unused_channels(pattern, config,
                                       active_channels=set(pulse))
        if len(pulse) > 1:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=(f"Long-Term Pulsing: pulsing {len(pulse)} channels "
                         f"simultaneously — {sorted(pulse)} "
                         f"(monitoring CH{int(getattr(config,'active',0)):02d}).")))

    def _pause_window(self, run: ChannelRun, pause_s: float,
                      elapsed_s: float) -> None:
        """Potentiostat pause — STOP stimulation for ``pause_s`` seconds so
        the operator can run an external measurement, then let the caller's
        post-event block resume pulsing (operator #81: "pause … allowing for
        other measurements such as potentiostat").

        Abort-aware (a Stop press ends the wait immediately).  The pause
        duration is EXCLUDED from pulsing time by the caller's clock
        compensation (``t_start += monotonic - char_t0``), so it doesn't
        count against ``duration_s``.
        """
        self._emit(ExperimentEvent(
            kind="log", session=self.session, run=run,
            message=(f"Pausing stimulation for {pause_s:.0f}s at "
                     f"t = {elapsed_s:.0f}s (pulsing time) for an external "
                     f"measurement (e.g. potentiostat).")))
        try:
            self.stim.stop_all()
        except Exception:
            pass
        deadline = time.monotonic() + pause_s
        while not self.aborted and time.monotonic() < deadline:
            time.sleep(0.05)
        if not self.aborted:
            self._emit(ExperimentEvent(
                kind="log", session=self.session, run=run,
                message="Pause complete — resuming pulsing."))

    # --------------------------------------------------------------
    def _characterize(self, run: ChannelRun, base_pattern: PulsePattern,
                      t_offset_s: float) -> bool:
        """Pause continuous pulsing and run a short VT sweep for drift
        tracking.  Returns ``True`` if the sub-run aborted (so the
        outer pulsing loop can also bail out instead of silently
        resuming).
        """
        # ``stop_all`` (= PS_StopStimAllChannels) — quiets both the
        # active channel AND the unused zero-amplitude channels brought
        # up by ``start_all`` in the outer ``run()``.  Matches MATLAB
        # ``stopStimulation`` before pausing for re-characterization.
        try:
            self.stim.stop_all()
        except Exception:
            pass
        # Run a small inline VT with a few amplitude steps.  The VT ramp starts
        # at its base pattern's amplitude, so to begin the characterization LOW
        # (drift tracking wants a ramp, not a single high-amplitude shot) we
        # give the sub-VT a base pattern PRE-SCALED to _CHAR_START_FRAC of the
        # pulsing amplitude — the direct replacement for the removed
        # ``RampPolicy.starting_ua = amplitude_ua * 0.2``.
        import dataclasses as _dc
        _char_pattern = self.session.test.pattern.scaled(self._CHAR_START_FRAC)
        _char_test = _dc.replace(self.session.test, pattern=_char_pattern)
        char_session = Session(notebook=self.session.notebook,
                               subject=self.session.subject + f"_t{int(t_offset_s)}",
                               test=_char_test)
        sub = VoltageTransientExperiment(char_session, self.stim, self.scope,
                                         configurations=[run.configuration],
                                         ramp=self.ramp,
                                         surface_area_um2=run.surface_area_um2)
        sub_result = sub.run()
        for c in sub_result.captures:
            c.status.notes = f"char@{int(t_offset_s)}s"
            # Place the char sub-captures on the run timeline at the
            # window-start pulsing-elapsed.  Scheduled stays NaN — a
            # char window is a burst of ramp captures, not a single
            # cadence-grid point.
            c.metrics.elapsed_time_s = float(t_offset_s)
            run.captures.append(c)
            self._emit(ExperimentEvent(kind="capture", session=self.session,
                                       run=run, capture=c))
        return bool(getattr(sub_result, "aborted", False))
