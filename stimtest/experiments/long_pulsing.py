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

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np

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

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope, *,
                 amplitude_ua: float,
                 policy: Optional[LongPulsingPolicy] = None,
                 ramp: Optional[RampPolicy] = None):
        super().__init__(session, stimulator, oscilloscope)
        self.amplitude_ua = amplitude_ua
        self.policy = policy or LongPulsingPolicy(duration_s=session.test.duration_s)
        self.ramp = ramp or RampPolicy(starting_ua=amplitude_ua * 0.2,
                                       coarse_step_ua=amplitude_ua * 0.2,
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
        run = ChannelRun(configuration=config,
                         surface_area_um2=self.session.test.array[config.active].surface_area_um2)
        self.session.add_run(run)
        self._emit(ExperimentEvent(kind="run_start", session=self.session, run=run))

        base = self.session.test.pattern
        # Guard against a zero excitation amplitude (see VT/SP for the
        # same defensive pattern — preflight rejects empty templates,
        # but an all-zero asymmetric one would still divide by zero).
        excite_amp_abs = abs(base.excitation_phase.amplitude_ua) or 1.0
        pattern = base.scaled(self.amplitude_ua / excite_amp_abs)
        # MATLAB setDefaultScopeView3.m: revert to default per-channel
        # scope view at the start of every channel (single-channel run
        # here, so this fires once).
        self.apply_default_scope_view(
            pattern, amp_ua=self.amplitude_ua,
            reason=f"start of channel {config.active}")

        try:
            self.stim.set_monitor_channel(config.active)
            self.stim.load_channel(config.active, pattern)
            self.stim.set_repetitions(config.active, 0)
            # Load zero-amplitude same-duration copy on every unused
            # channel.  Port of MATLAB ``setPattern.m`` Zero Current
            # block.  LP can run for hours; unused channels carrying
            # a stale pattern from a prior session would deliver
            # current for the full duration without this — keep them
            # TICKING in cadence with the active channel at zero amp
            # instead.  Returns intentionally stay unloaded (passive
            # sink); CG auto-skips because returns span every other
            # channel.
            self.load_zero_unused_channels(pattern, config)
            # PS_StartStimAllChannels — single-channel start fails with
            # WRONG-TRIGGER-MODE on default-mode PlexStim devices.
            # Active + unused-zero channels fire together.
            self.stim.start_all()
        except Exception as e:
            self._emit(ExperimentEvent(kind="aborted", session=self.session, run=run,
                                       message=f"Stim load failed: {e}"))
            return ExperimentResult(session=self.session, aborted=True, error=str(e))

        pulse_period_s = 1.0 / pattern.rate_hz
        navg = getattr(self.scope, "_expected_acq_navg", None) or 8
        t_start = time.time()
        next_snapshot_at = t_start
        next_char_at = t_start + self.policy.characterize_every_s
        idx = 0
        try:
            while not self.aborted and (time.time() - t_start) < self.policy.duration_s:
                now = time.time()

                # Periodic re-characterization
                if now >= next_char_at:
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session, run=run,
                        message=f"Re-characterizing at t = {now - t_start:.0f}s",
                    ))
                    _char_aborted = self._characterize(
                        run, base_pattern=base, t_offset_s=now - t_start)
                    # If the user aborted DURING the inline VT sub-run,
                    # propagate the abort to this outer pulsing loop —
                    # otherwise pulsing would silently resume after a
                    # cancelled re-characterization.
                    if _char_aborted or self.aborted:
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session, run=run,
                            message=("Re-characterization aborted — "
                                     "ending long-pulsing run.")))
                        break
                    next_char_at = now + self.policy.characterize_every_s
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
                    self.stim.load_channel(config.active, pattern)
                    # Re-load zero-amplitude same-duration pattern on
                    # unused channels so they tick in cadence with the
                    # active again (port of MATLAB setPattern.m Zero
                    # Current).
                    self.load_zero_unused_channels(pattern, config)
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

                # Lightweight snapshot capture
                if now >= next_snapshot_at:
                    acq = self.scope.capture_while_running(
                        wait_s=navg * pulse_period_s,
                        reset_before_run=(idx == 0))
                    _acq_ch = getattr(acq, "channels", {}) or {}
                    for _ch, _arr in _acq_ch.items():
                        _a = np.asarray(_arr, dtype=float)
                        if _a.size >= 2:
                            self.scope.adapt_channel_scale(
                                _ch, v_min=float(_a.min()), v_max=float(_a.max()))
                    cap = make_capture(idx, pattern, acq, self.scope, self.stim,
                                       cal=self.cal, channel=config.active)
                    compute_metrics(cap, run.surface_area_um2)
                    # Feed E_ret pre/post-pulse rest values into the
                    # electrode-potential learning bin. No-ops when
                    # E_ret wasn't recorded or the snapshot is
                    # missing. See record_capture for full skip rules.
                    try:
                        from ..electrode_potential_history import record_capture
                        record_capture(cap, self.session)
                    except Exception:
                        pass
                    # Per-capture damage warning, posture-aware.
                    try:
                        from ..damage_warnings import assess_finished_capture
                        snap = (self.session.test.extras or {}).get(
                            "setup_snapshot") or {}
                        env_short = (snap.get("environment_short")
                                     if isinstance(snap, dict) else None
                                     ) or "pbs"
                        warn = assess_finished_capture(
                            cap, environment_short=env_short)
                        if warn is not None:
                            self._emit(ExperimentEvent(
                                kind="log", session=self.session,
                                capture=cap,
                                message=f"{warn.title}\n{warn.body}"))
                    except Exception:
                        pass
                    cap.status.notes = "snapshot"
                    # Memory checkpoint — drop the raw sample arrays
                    # for snapshot captures (metrics already computed
                    # and stored on cap.metrics).  Keeps the per-
                    # capture memory footprint at ~kilobytes instead
                    # of megabytes over a multi-hour run.
                    if getattr(self.policy, "trim_snapshot_arrays", True):
                        try:
                            cap.v_mon_v = None
                            cap.i_mon_ua = None
                            cap.e_act_v = None
                            cap.e_ret_v = None
                            cap.time_us = None
                        except Exception:
                            pass
                    run.captures.append(cap)
                    self._emit(ExperimentEvent(kind="capture", session=self.session,
                                               run=run, capture=cap))
                    idx += 1
                    next_snapshot_at = now + self.policy.capture_during_pulsing_every_s
                time.sleep(0.005)
        finally:
            # ``stop_all`` (= PS_StopStimAllChannels) — matches MATLAB
            # ``stopStimulation`` and quiets both the active channel
            # AND the unused zero-amplitude channels that were brought
            # up alongside it via ``start_all``.
            try:
                self.stim.stop_all()
            except Exception:
                pass

        run.finished_at = datetime.now()
        self._emit(ExperimentEvent(kind="run_end", session=self.session, run=run))
        self._emit(ExperimentEvent(kind="session_end", session=self.session))
        return ExperimentResult(session=self.session, captures=run.captures,
                                aborted=self.aborted)

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
        # Run a small inline VT with a few amplitude steps
        char_session = Session(notebook=self.session.notebook,
                               subject=self.session.subject + f"_t{int(t_offset_s)}",
                               test=self.session.test)
        sub = VoltageTransientExperiment(char_session, self.stim, self.scope,
                                         configurations=[run.configuration],
                                         ramp=self.ramp,
                                         surface_area_um2=run.surface_area_um2)
        sub_result = sub.run()
        for c in sub_result.captures:
            c.status.notes = f"char@{int(t_offset_s)}s"
            run.captures.append(c)
            self._emit(ExperimentEvent(kind="capture", session=self.session,
                                       run=run, capture=c))
        return bool(getattr(sub_result, "aborted", False))
