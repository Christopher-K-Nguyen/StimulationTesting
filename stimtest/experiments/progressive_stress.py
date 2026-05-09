"""Progressive Stress / Stepped-Current Pulsing experiment.

Drives a single channel through a staircase amplitude ramp, holding each
step for ``t_step_s`` seconds and grabbing several averaged scope frames so
metric drift within a step is captured. Continues until any of:

* the configured ``max_ua`` ceiling is reached (default = the PlexStim
  hardware limit, ~1 mA/channel);
* V_mon hits the stimulator's voltage compliance rail (~±12 V) — beyond
  this the device is no longer actually delivering the programmed current,
  so further steps are meaningless;
* the user aborts.

Why we don't auto-stop on a "failure" detector
----------------------------------------------
An earlier iteration borrowed the capacitive-to-faradaic transition test
from Nguyen et al., JNE 22 (2025) 066040 — but that paper detects
*encapsulation* breakdown (a-SiC dielectric over IDE traces under DC
stress), not stimulation-electrode failure under AC pulsing. The two have
different physics and the same detector misfires in pulsing mode. We
therefore let the ramp run to the device's hardware ceiling and leave
post-hoc analysis (Weibull fits, polarisation-trend inspection, etc.) to
the user. The legacy detector is still available in
:func:`stimtest.metrics.detect_capacitive_to_faradaic` for users who want
to apply it to dedicated insulation tests.

Each capture is tagged ``step=<amp>uA`` in ``status.notes`` so the viewer
and per-channel sheet can group them by step level.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import numpy as np

from ..config import STIM_MAX_AMPLITUDE_UA, STIM_VOLTAGE_COMPLIANCE_V
from ..hardware.base import Oscilloscope, Stimulator
from ..hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from ..metrics import compute_metrics
from ..session import Capture, ChannelRun, Session
from ..waveforms import PulsePattern
from .base import ExperimentEvent, ExperimentResult, ExperimentRunner


@dataclass
class StressPolicy:
    """One staircase configuration.

    Defaults ramp from 5 µA up to the PlexStim hardware limit in 5 µA steps,
    pausing 60 s on each step and grabbing one averaged scope frame every
    12 s. Adjust ``max_ua`` downward if you want to cap below the device
    limit, and ``sampling_period_s`` to tighten or loosen the per-step
    capture cadence.
    """
    starting_ua: float = 5.0
    step_ua: float = 5.0
    t_step_s: float = 60.0
    max_ua: float = STIM_MAX_AMPLITUDE_UA
    #: Wall-clock seconds between successive captures inside one step.
    #: ``floor(t_step_s / sampling_period_s)`` frames are grabbed before
    #: the ramp moves to the next current level.
    sampling_period_s: float = 12.0

    #: When True, stop the ramp as soon as V_mon hits the stimulator's
    #: voltage compliance rail (~±12 V) — beyond which the programmed current
    #: is no longer actually being delivered. Default True; set False if you
    #: explicitly want to push the device into compliance to characterize
    #: rail behavior.
    stop_on_voltage_compliance: bool = True


class ProgressiveStressExperiment(ExperimentRunner):

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope, *,
                 policy: Optional[StressPolicy] = None):
        super().__init__(session, stimulator, oscilloscope)
        self.policy = policy or StressPolicy()
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
        surface_area = self.session.test.array[config.active].surface_area_um2
        run = ChannelRun(configuration=config, surface_area_um2=surface_area)
        self.session.add_run(run)
        self._emit(ExperimentEvent(kind="run_start", session=self.session, run=run))

        base = self.session.test.pattern
        amp = self.policy.starting_ua
        idx = 0
        compliance_hit = False
        self.scope.set_record_length(2500)
        self.scope.set_acquisition_mode("AVERAGE", n_avg=8)

        # ``next_pattern`` is the ramp-step's pattern *pre-built* during
        # the previous step's hold. Initially None — first iteration
        # falls through to the "build now" branch. Each iteration also
        # builds the *following* step's pattern in advance, so the
        # next iteration's load_channel skips the line-generation work.
        # The cost is the cheap ``scaled()`` call, which we move from
        # the critical path into the long settling window.
        next_pattern = None
        excite_amp_abs = abs(base.excitation_phase.amplitude_ua) or 1.0
        try:
            while not self.aborted and amp <= self.policy.max_ua:
                if next_pattern is not None:
                    pattern = next_pattern
                else:
                    pattern = base.scaled(amp / excite_amp_abs)
                # Program & start
                try:
                    self.stim.set_monitor_channel(config.active)
                    self.stim.load_channel(config.active, pattern)
                    self.stim.set_repetitions(config.active, 0)
                    self.stim.start_channel(config.active)
                except Exception as e:
                    self._emit(ExperimentEvent(kind="aborted", session=self.session,
                                               message=f"Step program failed: {e}"))
                    break

                # ---- Pre-build the NEXT step's pattern ----
                # We're about to enter the inner sampling loop where
                # the host CPU is otherwise idle waiting for the scope.
                # Build the next ramp step's pattern object now so the
                # next outer iteration's ``load_channel`` skips this
                # work. ``None`` means we've reached max_ua and there
                # is no next step.
                next_amp = amp + self.policy.step_ua
                if next_amp <= self.policy.max_ua:
                    next_pattern = base.scaled(next_amp / excite_amp_abs)
                else:
                    next_pattern = None

                step_start = time.time()
                interval = max(self.policy.sampling_period_s, 1e-3)
                next_grab = step_start
                step_caps: List[Capture] = []
                while not self.aborted and (time.time() - step_start) < self.policy.t_step_s:
                    if time.time() >= next_grab:
                        try:
                            acq = self.scope.single_capture()
                        except Exception:
                            time.sleep(0.05); continue
                        cap = _make_capture(idx, pattern, acq, self.scope, self.stim)
                        compute_metrics(cap, surface_area)
                        cap.status.notes = f"step={amp:.0f}uA"
                        # Hardware-level stop condition: V_mon rail
                        cap.status.voltage_compliance = bool(
                            cap.v_mon_v.size and
                            np.max(np.abs(cap.v_mon_v)) > STIM_VOLTAGE_COMPLIANCE_V
                        )
                        run.captures.append(cap)
                        step_caps.append(cap)
                        self._emit(ExperimentEvent(kind="capture", session=self.session,
                                                   run=run, capture=cap))
                        idx += 1
                        next_grab += interval
                    time.sleep(0.02)

                # Stop if we hit voltage compliance — the device can't push
                # more current at this load even if the user asked for more.
                if self.policy.stop_on_voltage_compliance and any(
                        c.status.voltage_compliance for c in step_caps):
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session, run=run,
                        message=f"Voltage compliance hit at {amp:.1f} µA — stopping ramp.",
                    ))
                    compliance_hit = True
                    break

                amp += self.policy.step_ua
        finally:
            try:
                self.stim.stop_channel(config.active)
            except Exception:
                pass

        if not compliance_hit and not self.aborted and amp > self.policy.max_ua:
            self._emit(ExperimentEvent(
                kind="log", session=self.session, run=run,
                message=f"Reached max amplitude {self.policy.max_ua:.0f} µA "
                        f"({'PlexStim limit' if self.policy.max_ua >= STIM_MAX_AMPLITUDE_UA else 'configured cap'}).",
            ))

        run.finished_at = datetime.now()
        self._emit(ExperimentEvent(kind="run_end", session=self.session, run=run))
        self._emit(ExperimentEvent(kind="session_end", session=self.session))
        return ExperimentResult(session=self.session, captures=run.captures,
                                aborted=self.aborted)


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
