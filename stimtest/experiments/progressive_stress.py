"""Progressive Stress / Stepped-Current Pulsing experiment.

Drives ONE SAMPLE — a channel (monopolar) OR a combo (bipolar / multipolar,
i.e. an active + return set) — through a staircase amplitude ramp, holding
each step for ``t_step_s`` seconds and grabbing several averaged scope frames
so metric drift within a step is captured.  (The active channel delivers the
current; for a combo its returns are passive sinks.)  Continues until any of:

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


from ..config import STIM_MAX_AMPLITUDE_UA, STIM_VOLTAGE_COMPLIANCE_V
from ..hardware.base import Oscilloscope, Stimulator
from ..hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from ..metrics import compute_metrics
from ..readback_calibration import make_capture
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
        # NOTE: the historical ``self.stim.reinit()`` at the top of
        # run() was REMOVED — see voltage_transient.run() for the
        # full rationale (CLAUDE.md gotcha #29b + #31).  Short
        # version: the GUI Stop / Start lifecycle now guarantees a
        # fresh device, and the reinit cascade was the second
        # ``ps_close_all_stim`` in a chain that HEAP_CORRUPT'd the
        # vendor DLL.  Stale pattern bytes from a previous run are
        # overwritten by ``load_channel`` before stim starts.
        config = self.session.test.configuration
        surface_area = self.session.test.array[config.active].surface_area_um2
        run = ChannelRun(configuration=config, surface_area_um2=surface_area)
        self.session.add_run(run)
        self._emit(ExperimentEvent(kind="run_start", session=self.session, run=run))

        base = self.session.test.pattern
        amp = self.policy.starting_ua
        # Revert to default scope view at the start (MATLAB
        # setDefaultScopeView3.m): clear adapt state, re-run layout,
        # re-size verticals — even single-channel runs benefit so
        # back-to-back Start presses don't inherit the previous run's
        # final scales.
        self.apply_default_scope_view(
            base, amp_ua=amp,
            reason=f"start of channel {config.active}")
        # Arm closed-loop bias feedback if the GUI pushed a controller
        # onto this runner.  Must happen AFTER apply_default_scope_view
        # so the controller's MEASUrement-gating SCPI writes aren't
        # clobbered.  Arms ONCE before the ramp loop — the gating
        # window stays in effect across amplitude steps (it's a scope
        # state, not an amplitude-dependent value).  No-op when no
        # controller is attached.
        self.arm_bias_feedback()
        idx = 0
        compliance_hit = False
        # ``next_pattern`` is the ramp-step's pattern *pre-built* during
        # the previous step's hold. Initially None — first iteration
        # falls through to the "build now" branch. Each iteration also
        # builds the *following* step's pattern in advance, so the
        # next iteration's load_channel skips the line-generation work.
        # The cost is the cheap ``scaled()`` call, which we move from
        # the critical path into the long settling window.
        next_pattern = None
        # Global monotonic run anchor for the precise-sampling time
        # columns.  ``time.monotonic()`` (NOT ``time.time()``) so the
        # cadence math can't jump on an NTP step / DST change — the
        # equivalent of MATLAB's ``tic``/``toc`` high-resolution
        # counter.  Each capture records the elapsed time vs this
        # anchor (actual) AND the fixed cadence-grid time it aimed for
        # (scheduled).  Operator: "make sure the timing is precise" +
        # "have the fixed time and elapsed time columns".
        run_t0 = time.monotonic()
        try:
            while not self.aborted and amp <= self.policy.max_ua:
                # User PAUSE checkpoint (between staircase steps — stim already
                # stopped here, next step reloads+starts on resume → restart=None).
                if not self.wait_if_paused():
                    break
                if next_pattern is not None:
                    pattern = next_pattern
                else:
                    # Identical to base.scaled(amp/|excite|) for a normal
                    # template; also grows a ZERO-amplitude template so a
                    # PS staircase "starting at 0 µA" actually increases the
                    # current (plain scaling can't grow a zero).  See base.py.
                    pattern = self._pattern_at_amplitude(base, amp)

                # Adaptive scope view per amplitude step: I_mon vertical
                # scale + trigger level both updated.  Vertical scale uses
                # the magnitude (``amp``); trigger level uses the SIGNED
                # amplitude so the level sign matches the phase-1 polarity.
                try:
                    ph_us = (pattern.excitation_phase.width_us
                             if pattern.excitation_phase else 200.0)
                    _amp_signed = (float(pattern.excitation_phase.amplitude_ua)
                                   if pattern.excitation_phase else float(amp))
                    self.update_imon_vertical_scale(float(amp))
                    self.update_imon_trigger_level(
                        _amp_signed, phase_width_us=ph_us)
                except Exception:
                    pass

                # Program & start
                try:
                    # Defensive stop BEFORE the load.  ``PS_LoadChannel``
                    # for an arbitrary pattern uploads the full .pat byte
                    # stream over USB (~50-200 ms) — we want the device
                    # quiescent during that window rather than continuing
                    # the previous amplitude's pattern.  Idempotent on a
                    # stopped device (the end-of-iteration stop_all
                    # below typically left us stopped); critical when the
                    # previous iteration's stop_all was skipped (early
                    # break on compliance hit, exception, etc.).
                    #
                    # Uses ``stop_all`` (= PS_StopStimAllChannels) rather
                    # than ``stop_channel(active)`` so the unused
                    # zero-amplitude channels from the previous iteration
                    # are quieted too before ``load_zero_unused_channels``
                    # reloads them.
                    try:
                        self.stim.stop_all()
                    except Exception:
                        pass
                    self.stim.set_monitor_channel(config.active)
                    self.stim.load_channel(config.active, pattern)
                    self.stim.set_repetitions(config.active, 0)
                    # Load zero-amplitude same-duration copy on every
                    # unused channel (everything not active and not in
                    # returns).  Port of MATLAB ``setPattern.m`` Zero
                    # Current block — keeps unused channels in cadence
                    # with the active pulse cycle.  Returns stay
                    # unloaded (passive sink); CG auto-skips because
                    # returns span every other channel.
                    self.load_zero_unused_channels(pattern, config)
                    # MONOPOLAR commit (config.returns empty) → ONE
                    # PS_LoadAllChannels; multipolar no-op (MATLAB
                    # loadPattern.m parity).
                    self.commit_loaded_channels(config)
                    # PS_StartStimAllChannels — single-channel start fails
                    # with WRONG-TRIGGER-MODE on default-mode PlexStim.
                    # Active + unused-zero channels fire together; zero
                    # channels deliver no current.
                    self.stim.start_all()
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
                    next_pattern = self._pattern_at_amplitude(base, next_amp)
                else:
                    next_pattern = None

                pulse_period_s = 1.0 / pattern.rate_hz
                navg = getattr(self.scope, "_expected_acq_navg", None) or 8
                step_start = time.monotonic()
                interval = max(self.policy.sampling_period_s, 1e-3)
                # FIXED cadence grid anchored to the step start: each grab
                # targets ``step_start + k·interval``, NOT ``now + interval``
                # — so a slow capture doesn't push every later grab late
                # (the drift MATLAB's ``rateControl`` failed to avoid).
                next_grab = step_start
                step_caps: List[Capture] = []
                _step_cap_idx = 0
                while not self.aborted and (time.monotonic() - step_start) < self.policy.t_step_s:
                    now = time.monotonic()
                    if now >= next_grab:
                        try:
                            acq = self.scope.capture_while_running(
                                wait_s=navg * pulse_period_s,
                                reset_before_run=(_step_cap_idx == 0))
                            # (No trigger/pulse-alignment check — operator:
                            # "Do not have warnings about the trigger
                            # warning"; the I_mon edge isn't a reliable
                            # time-axis signal.  See check_trigger_alignment.)
                            # SHARED fit-the-view rescale loop (same
                            # coarse/fine scaling + positioning as VT —
                            # operator request).  Stim keeps running
                            # (continuous within a step, gotcha #6);
                            # recapture = another continuous-run grab
                            # whose sleep IS the fresh-frame settle.  On
                            # a converged stationary signal the loop
                            # exits after ONE pass with no writes, so
                            # the snapshot cadence is unaffected.
                            acq = self.rescale_to_fit(
                                acq, pattern=pattern,
                                recapture=lambda _t:
                                    self.scope.capture_while_running(
                                        wait_s=navg * pulse_period_s),
                                timeout_s=navg * pulse_period_s + 6.0,
                                context=(f"at step {amp:.0f} µA "
                                         f"(snapshot "
                                         f"#{_step_cap_idx + 1})"))
                        except Exception:
                            continue
                        self._smooth_acquisition(acq)
                        cap = make_capture(idx, pattern, acq, self.scope, self.stim,
                                           cal=self.cal, channel=config.active)
                        compute_metrics(cap, surface_area)
                        # Feed E_ret pre/post-pulse rest values into
                        # the electrode-potential learning bin. No-ops
                        # when E_ret wasn't recorded, when the session
                        # lacks a setup snapshot, or for non-Ag|AgCl
                        # references — see record_capture's docstring.
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
                        cap.status.notes = f"step={amp:.0f}uA"
                        # Precise-sampling time columns: the fixed
                        # cadence-grid time this grab AIMED for
                        # (``next_grab``) and the actual monotonic
                        # elapsed when it fired — both vs the global
                        # run anchor so the whole staircase shares ONE
                        # timeline for failure-marker analysis over time.
                        cap.metrics.scheduled_time_s = next_grab - run_t0
                        cap.metrics.elapsed_time_s = now - run_t0
                        # Hardware-level stop condition: V_mon rail.
                        # Use the same 3-consecutive-samples glitch
                        # filter that voltage_transient uses, so a
                        # single noisy sample (EMI transient on the
                        # probe lead, etc.) doesn't trip the stop.
                        # Real compliance events persist across the
                        # whole pulse, so 3 samples is well below
                        # any meaningful event yet well above any
                        # single-sample artifact.
                        from .voltage_transient import _v_compliance_tripped
                        cap.status.voltage_compliance = bool(
                            cap.v_mon_v.size and _v_compliance_tripped(
                                cap.v_mon_v,
                                threshold_v=STIM_VOLTAGE_COMPLIANCE_V,
                                min_consecutive=3))
                        run.captures.append(cap)
                        step_caps.append(cap)
                        # Per-capture pulse count + cumulative delivered
                        # charge (building on this run's earlier captures) —
                        # PS is a discrete per-amplitude staircase like VT,
                        # so the cumulative is the true delivered dose.
                        self._record_capture_dose(run, cap)
                        # Nonparametric access-resistance drift monitor
                        # (Mann-Whitney vs the run's earlier R_a; warn-only).
                        self.check_access_resistance_drift(run)
                        self._emit(ExperimentEvent(kind="capture", session=self.session,
                                                   run=run, capture=cap))
                        # Closed-loop bias step at the same cadence as
                        # the capture loop.  Same placement as SP:
                        # AFTER the capture event so the GUI's status
                        # badge update lands alongside the just-emitted
                        # metrics row.  Cheap no-op when the controller
                        # isn't armed.
                        self.bias_step_if_armed()
                        idx += 1
                        _step_cap_idx += 1
                        # Advance the fixed grid by exactly one interval.
                        # If the capture overran a whole interval, skip
                        # the missed slot(s) rather than firing a
                        # back-to-back catch-up burst (snap to the next
                        # grid point strictly after ``now``).
                        next_grab += interval
                        if next_grab <= now:
                            missed = int((now - next_grab) // interval) + 1
                            next_grab += missed * interval
                    time.sleep(0.002)

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

                # ---- Stop BEFORE next amplitude's pattern load ----
                # Port of MATLAB ``runProgressiveStress.m`` line 371:
                # ``stopStimulation()`` fires after the capture for the
                # current amplitude and BEFORE the next iteration's
                # ``setPattern`` / ``loadPattern`` / ``startStimulation``
                # sequence.  Per user spec: "Progressive stress stays
                # pulsing until the amplitude/charge needs to be
                # stepped up" — within one step the stim is continuous
                # across every ``capture_while_running`` call (already
                # the case above), but between steps the channel is
                # explicitly stopped so the next ``load_channel`` lands
                # on a quiescent device.
                #
                # ``stop_all`` (= PS_StopStimAllChannels) rather than
                # ``stop_channel(active)`` so the unused zero-amplitude
                # channels brought up by ``start_all`` are also quieted —
                # otherwise they'd continue ticking through the
                # ~50-200 ms load window while we reload the active.
                # The next iteration also does a defensive ``stop_all``
                # right before its load (belt-and-suspenders), so a
                # silent failure here doesn't leak into the next step.
                try:
                    self.stim.stop_all()
                except Exception:
                    pass

                amp += self.policy.step_ua
        finally:
            # Disarm bias feedback FIRST so the controller's scope-
            # gating teardown happens before the stim quiets — keeps
            # the order consistent with how we armed it (arm AFTER
            # scope view, disarm BEFORE stim stop).  Finally-safe +
            # idempotent + swallows controller failures so a teardown
            # SCPI error doesn't mask the run's actual error.
            self.disarm_bias_feedback()
            # Outer safety net — fires on normal end-of-ramp, on a
            # compliance-triggered break, and on any exception escaping
            # the ramp loop.  ``stop_all`` matches MATLAB
            # ``stopStimulation`` exactly.
            try:
                self.stim.stop_all()
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


