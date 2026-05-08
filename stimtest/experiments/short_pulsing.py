"""Short-Term Pulsing (SP) experiment.

Quick "is this electrode stable?" check: pulse continuously at a fixed
amplitude for a given duration, taking a snapshot capture every
``capture_interval_s`` seconds so we can plot V_mon and the derived metrics
over the test window.

This is the simplest of the four runners — no amplitude ramp, no failure
detection, no re-characterization. Think of it as the manual / sanity-check
mode. For longer drift tracking use Long-Term Pulsing; for stress testing
use Progressive Stress.

Each snapshot is a full averaged scope capture (so V_d, V_a, R_a, E_pol,
Q_inj, C_d are all computed for it). The saved .npz contains the entire
time-ordered list, suitable for plotting any metric vs wall-clock time.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..hardware.base import Oscilloscope, Stimulator
from ..hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from ..metrics import compute_metrics
from ..session import Capture, ChannelRun, Session
from ..waveforms import PulsePattern
from .base import ExperimentEvent, ExperimentResult, ExperimentRunner


@dataclass
class ShortPulsingPolicy:
    capture_interval_s: float = 1.0
    duration_s: float = 60.0


class ShortPulsingExperiment(ExperimentRunner):

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope, *,
                 amplitude_ua: float,
                 policy: Optional[ShortPulsingPolicy] = None):
        super().__init__(session, stimulator, oscilloscope)
        self.amplitude_ua = amplitude_ua
        self.policy = policy or ShortPulsingPolicy(duration_s=session.test.duration_s)

        if isinstance(self.scope, SimulatedOscilloscope) \
                and isinstance(self.stim, SimulatedStimulator):
            self.scope.bind_stimulator(self.stim)

    def run(self) -> ExperimentResult:
        self.preflight()
        from datetime import datetime
        config = self.session.test.configuration
        run = ChannelRun(configuration=config,
                         surface_area_um2=self.session.test.array[config.active].surface_area_um2)
        self.session.add_run(run)
        self._emit(ExperimentEvent(kind="run_start", session=self.session, run=run))

        base = self.session.test.pattern
        # Guard against a zero excitation amplitude (preflight already
        # rejects empty patterns, but a fully-zero asymmetric template
        # would still divide by zero here). 1.0 fallback keeps us out
        # of the ZeroDivisionError; the scaled pattern is then 0 µA
        # everywhere, which the runner programs without complaint.
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
        next_capture_at = t_start
        idx = 0
        try:
            while not self.aborted and (time.time() - t_start) < self.policy.duration_s:
                if time.time() >= next_capture_at:
                    acq = self.scope.single_capture()
                    cap = _make_capture(idx, pattern, acq, self.scope, self.stim)
                    compute_metrics(cap, run.surface_area_um2)
                    run.captures.append(cap)
                    self._emit(ExperimentEvent(kind="capture", session=self.session,
                                               run=run, capture=cap))
                    idx += 1
                    next_capture_at += self.policy.capture_interval_s
                time.sleep(0.01)
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


def _make_capture(index: int, pattern: PulsePattern, acq, scope, stim) -> Capture:
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
