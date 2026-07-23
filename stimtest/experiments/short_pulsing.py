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


from ..hardware.base import Oscilloscope, Stimulator
from ..hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from ..metrics import compute_metrics
from ..readback_calibration import make_capture
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
        # NOTE: the historical ``self.stim.reinit()`` at the top of
        # run() was REMOVED — see voltage_transient.run() for the
        # full rationale (CLAUDE.md gotcha #29b + #31).  Short
        # version: the GUI Stop / Start lifecycle now guarantees a
        # fresh device, and the reinit cascade was the second
        # ``ps_close_all_stim`` in a chain that HEAP_CORRUPT'd the
        # vendor DLL.  Stale pattern bytes from a previous run are
        # overwritten by ``load_channel`` before stim starts.
        from datetime import datetime
        config = self.session.test.configuration
        run = ChannelRun(configuration=config,
                         surface_area_um2=self.session.test.array[config.active].surface_area_um2)
        self.session.add_run(run)
        self._emit(ExperimentEvent(kind="run_start", session=self.session, run=run))

        base = self.session.test.pattern
        # Build the fixed-amplitude pulsing pattern.  ``_pattern_at_amplitude``
        # is identical to ``base.scaled(self.amplitude_ua / |excitation|)`` for
        # a normal template, but also GROWS a zero-amplitude template (plain
        # scaling can't grow a zero → it would pulse at 0 µA regardless of the
        # requested amplitude).  See base.py.
        pattern = self._pattern_at_amplitude(base, self.amplitude_ua)
        # MATLAB setDefaultScopeView3.m: revert to default per-channel
        # scope view at the start of every channel (here: at run start,
        # since short-pulsing is single-channel).
        self.apply_default_scope_view(
            pattern, amp_ua=self.amplitude_ua,
            reason=f"start of channel {config.active}")

        # Arm closed-loop bias feedback if the GUI pushed a controller
        # onto this runner.  Must happen AFTER apply_default_scope_view
        # so the controller's MEASUrement-gating SCPI writes aren't
        # clobbered by the scope-view defaults.  No-op when no
        # controller is attached (the common case until the operator
        # flips the master Enable checkbox on the BiasFeedbackPanel).
        self.arm_bias_feedback()

        try:
            self.stim.set_monitor_channel(config.active)
            self.stim.load_channel(config.active, pattern)
            self.stim.set_repetitions(config.active, 0)
            # Load a zero-amplitude, same-duration copy of the pattern
            # on every unused channel (anything not active and not in
            # returns).  Port of MATLAB ``setPattern.m`` Zero Current
            # block.  SP runs continuously for many minutes, so making
            # sure unused channels stay TICKING in sync with the active
            # channel's pulse cycle is especially important here —
            # without it, an unused channel carrying a stale pattern
            # from a previous run would deliver current for the whole
            # SP duration.
            self.load_zero_unused_channels(pattern, config)
            # MONOPOLAR commit (config.returns empty) → ONE
            # PS_LoadAllChannels; multipolar no-op (MATLAB loadPattern.m
            # parity — see ExperimentRunner.commit_loaded_channels).
            self.commit_loaded_channels(config)
            # PS_StartStimAllChannels — single-channel start fails with
            # WRONG-TRIGGER-MODE on default-mode PlexStim devices.
            # Loaded channels (active + unused-zero) fire together;
            # zero channels deliver no current.
            self.stim.start_all()
        except Exception as e:
            self._emit(ExperimentEvent(kind="aborted", session=self.session, run=run,
                                       message=f"Stim load failed: {e}"))
            return ExperimentResult(session=self.session, aborted=True, error=str(e))

        # Burst-aware per-triggered-pulse spacing: for a burst the averager
        # fills at the OVERALL pulse rate (slower than rate_hz because of the
        # inter-burst gaps), so ``navg × pulse_period_s`` waits long enough for
        # a full average.  Identity ``== 1/rate_hz`` for a non-burst pattern.
        pulse_period_s = 1.0 / max(self._pulses_per_second(pattern), 1e-9)
        navg = getattr(self.scope, "_expected_acq_navg", None) or 8
        v_mon_phys = self.scope.channel_aliases.get("vmon", "CH1")
        i_mon_phys = self.scope.channel_aliases.get("imon", "CH2")

        def _snapshot(snap_idx: int, *, reset_before_run: bool,
                      context: str) -> Capture:
            """Take one averaged snapshot, compute metrics, record + emit it.

            Shared by the cadence loop AND the end-of-run final capture so
            the two never drift.  ``context`` is the rescale-loop log tag.
            """
            acq = self.scope.capture_while_running(
                wait_s=navg * pulse_period_s,
                reset_before_run=reset_before_run)
            # SHARED fit-the-view rescale loop (same coarse/fine scaling +
            # positioning as VT — operator request).  Stim runs for the
            # whole SP session (gotcha #8); recapture = another
            # continuous-run grab whose sleep IS the fresh-frame settle.
            # Converged stationary signal → one pass, zero writes, zero
            # re-captures — the snapshot cadence is unaffected.
            acq = self.rescale_to_fit(
                acq, pattern=pattern,
                recapture=lambda _t:
                    self.scope.capture_while_running(
                        wait_s=navg * pulse_period_s),
                timeout_s=navg * pulse_period_s + 6.0,
                context=context)
            self._smooth_acquisition(acq)
            cap = make_capture(snap_idx, pattern, acq, self.scope, self.stim,
                               cal=self.cal, channel=config.active)
            compute_metrics(cap, run.surface_area_um2,
                            depol_us=self._epol_depol_us())
            # Feed the E_ret pre/post-pulse rest values into the electrode-
            # potential learning bin keyed by the return coating.  Silently
            # no-ops when the capture has no E_ret trace (NaN rest values)
            # or when the session lacks a setup snapshot.
            try:
                from ..electrode_potential_history import record_capture
                record_capture(cap, self.session)
            except Exception:
                pass
            # Per-capture damage warning — environment-posture aware.
            # ``info`` (PBS / mISF / etc.) suppresses per-capture log spam;
            # ``warn`` / ``alert`` emit one log line per flagged capture.
            try:
                from ..damage_warnings import assess_finished_capture
                snap = (self.session.test.extras or {}).get(
                    "setup_snapshot") or {}
                env_short = (snap.get("environment_short")
                             if isinstance(snap, dict) else None) or "pbs"
                warn = assess_finished_capture(
                    cap, environment_short=env_short)
                if warn is not None:
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session, capture=cap,
                        message=f"{warn.title}\n{warn.body}"))
            except Exception:
                pass
            run.captures.append(cap)
            self._emit(ExperimentEvent(kind="capture", session=self.session,
                                       run=run, capture=cap))
            return cap

        t_start = time.time()
        next_capture_at = t_start
        idx = 0
        try:
            while not self.aborted and (time.time() - t_start) < self.policy.duration_s:
                # User PAUSE checkpoint — SP pulses CONTINUOUSLY, so halt +
                # restart pulsing (start_all resumes the RETAINED pattern).
                # Exclude the paused wall-clock from the duration + cadence so
                # a pause doesn't eat into the run ("continue where it left off").
                if not self.wait_if_paused(restart=lambda: self.stim.start_all()):
                    break
                if self._last_pause_duration_s > 0:
                    t_start += self._last_pause_duration_s
                    next_capture_at += self._last_pause_duration_s
                if time.time() >= next_capture_at:
                    _snapshot(idx, reset_before_run=(idx == 0),
                              context=f"(capture #{idx + 1})")
                    idx += 1
                    next_capture_at += self.policy.capture_interval_s
                    # Closed-loop bias step happens at the same cadence
                    # as the capture loop — measured E_ret from the
                    # gated MEASUrement window is the input, programmed
                    # bias DAC voltage is the output.  Cheap no-op when
                    # the controller isn't armed.  Placed AFTER the
                    # capture event so the GUI's BiasFeedbackPanel
                    # status badge can sit alongside the just-emitted
                    # metrics row.
                    self.bias_step_if_armed()
                time.sleep(0.001)
            # Final capture at the END of the run (operator: "When SP ends,
            # add another capture") — captures the electrode state right
            # after the full pulsing window so a start-vs-end comparison is
            # always available (the default cadence takes only the opening
            # snapshot for a short run).  Stim is still live here — the
            # ``finally`` below stops it.  Skipped on an abort (the operator
            # pressed Stop; Continuous Pulsing also exits only via abort, so
            # it never takes this extra capture).
            if not self.aborted:
                _snapshot(idx, reset_before_run=(idx == 0),
                          context="(final capture)")
                idx += 1
        finally:
            # Disarm bias feedback FIRST so the controller's scope-
            # gating teardown happens before the stim quiets — keeps
            # the order consistent with how we armed it (arm AFTER
            # scope view, disarm BEFORE stim stop).  Finally-safe +
            # idempotent + swallows controller failures so a teardown
            # SCPI error doesn't mask the run's actual error.
            self.disarm_bias_feedback()
            # ``stop_all`` (= PS_StopStimAllChannels, MATLAB
            # ``stopStimulation``) — quiets both the active channel AND
            # the unused zero-amplitude channels that were brought up
            # alongside it via ``start_all`` at run start.
            try:
                self.stim.stop_all()
            except Exception:
                pass

        run.finished_at = datetime.now()
        self._emit(ExperimentEvent(kind="run_end", session=self.session, run=run))
        self._emit(ExperimentEvent(kind="session_end", session=self.session))
        return ExperimentResult(session=self.session, captures=run.captures,
                                aborted=self.aborted)


