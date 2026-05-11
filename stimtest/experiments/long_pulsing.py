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
        # Lab convention (see voltage_transient.py): reinit at the
        # top of every Start-press so the device starts from a clean
        # slate. PlexStim has no PS_UnloadChannel, so any pattern
        # left loaded from a previous run would carry into this one.
        try:
            self.stim.reinit()
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message="Stimulator reinit at run start (clean slate)."))
        except Exception as e:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=f"Stimulator reinit at run start failed: {e}"))
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

        try:
            self.stim.set_monitor_channel(config.active)
            self.stim.load_channel(config.active, pattern)
            self.stim.set_repetitions(config.active, 0)
            self.stim.start_channel(config.active)
        except Exception as e:
            self._emit(ExperimentEvent(kind="aborted", session=self.session, run=run,
                                       message=f"Stim load failed: {e}"))
            return ExperimentResult(session=self.session, aborted=True, error=str(e))

        self.scope.set_record_length(2500)
        self.scope.set_acquisition_mode("AVERAGE", n_avg=8)

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
                    self._characterize(run, base_pattern=base, t_offset_s=now - t_start)
                    next_char_at = now + self.policy.characterize_every_s
                    # restart pulsing at the working amplitude
                    self.stim.load_channel(config.active, pattern)
                    self.stim.start_channel(config.active)

                # Lightweight snapshot capture
                if now >= next_snapshot_at:
                    acq = self.scope.single_capture()
                    cap = _make_capture(idx, pattern, acq, self.scope, self.stim)
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
                    run.captures.append(cap)
                    self._emit(ExperimentEvent(kind="capture", session=self.session,
                                               run=run, capture=cap))
                    idx += 1
                    next_snapshot_at = now + self.policy.capture_during_pulsing_every_s
                time.sleep(0.05)
        finally:
            try:
                self.stim.stop_channel(config.active)
            except Exception:
                pass

        run.finished_at = datetime.now()
        self._emit(ExperimentEvent(kind="run_end", session=self.session, run=run))
        self._emit(ExperimentEvent(kind="session_end", session=self.session))
        return ExperimentResult(session=self.session, captures=run.captures,
                                aborted=self.aborted)

    # --------------------------------------------------------------
    def _characterize(self, run: ChannelRun, base_pattern: PulsePattern,
                      t_offset_s: float) -> None:
        """Pause continuous pulsing and run a short VT sweep for drift tracking."""
        try:
            self.stim.stop_channel(run.configuration.active)
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


def _make_capture(index, pattern, acq, scope, stim) -> Capture:
    aliases = scope.channel_aliases
    v_mon = acq.channels.get(aliases.get("vmon", ""), np.zeros(0))
    i_mon = acq.channels.get(aliases.get("imon", ""), np.zeros(0))
    e_ret = acq.channels.get(aliases.get("eret", ""), None)
    e_act = acq.channels.get(aliases.get("eact", ""), None)
    info = stim.info
    v_mon_v = v_mon / info.vmon_scaling_v_per_v if info.vmon_scaling_v_per_v else v_mon
    i_mon_ua = i_mon / info.imon_scaling_v_per_ua if info.imon_scaling_v_per_ua else i_mon
    return Capture(
        index=index, pattern=pattern,
        time_us=np.asarray(acq.time_us),
        v_mon_v=np.asarray(v_mon_v),
        i_mon_ua=np.asarray(i_mon_ua),
        e_act_v=np.asarray(e_act) if e_act is not None and e_act.size else None,
        e_ret_v=np.asarray(e_ret) if e_ret is not None and e_ret.size else None,
    )
