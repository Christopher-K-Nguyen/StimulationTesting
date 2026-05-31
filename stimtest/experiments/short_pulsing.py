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
        # Guard against a zero excitation amplitude (preflight already
        # rejects empty patterns, but a fully-zero asymmetric template
        # would still divide by zero here). 1.0 fallback keeps us out
        # of the ZeroDivisionError; the scaled pattern is then 0 µA
        # everywhere, which the runner programs without complaint.
        excite_amp_abs = abs(base.excitation_phase.amplitude_ua) or 1.0
        pattern = base.scaled(self.amplitude_ua / excite_amp_abs)
        # MATLAB setDefaultScopeView3.m: revert to default per-channel
        # scope view at the start of every channel (here: at run start,
        # since short-pulsing is single-channel).
        self.apply_default_scope_view(
            pattern, amp_ua=self.amplitude_ua,
            reason=f"start of channel {config.active}")

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
            # PS_StartStimAllChannels — single-channel start fails with
            # WRONG-TRIGGER-MODE on default-mode PlexStim devices.
            # Loaded channels (active + unused-zero) fire together;
            # zero channels deliver no current.
            self.stim.start_all()
        except Exception as e:
            self._emit(ExperimentEvent(kind="aborted", session=self.session, run=run,
                                       message=f"Stim load failed: {e}"))
            return ExperimentResult(session=self.session, aborted=True, error=str(e))

        pulse_period_s = 1.0 / pattern.rate_hz
        navg = getattr(self.scope, "_expected_acq_navg", None) or 8
        v_mon_phys = self.scope.channel_aliases.get("vmon", "CH1")
        i_mon_phys = self.scope.channel_aliases.get("imon", "CH2")
        t_start = time.time()
        next_capture_at = t_start
        idx = 0
        try:
            while not self.aborted and (time.time() - t_start) < self.policy.duration_s:
                if time.time() >= next_capture_at:
                    acq = self.scope.capture_while_running(
                        wait_s=navg * pulse_period_s,
                        reset_before_run=(idx == 0))
                    chan_data = getattr(acq, "channels", {}) or {}
                    for _ch, _arr in chan_data.items():
                        _a = np.asarray(_arr, dtype=float)
                        if _a.size >= 2:
                            self.scope.adapt_channel_scale(
                                _ch, v_min=float(_a.min()),
                                v_max=float(_a.max()))
                    cap = make_capture(idx, pattern, acq, self.scope, self.stim,
                                       cal=self.cal, channel=config.active)
                    compute_metrics(cap, run.surface_area_um2)
                    # Feed the E_ret pre/post-pulse rest values into
                    # the electrode-potential learning bin keyed by
                    # the return coating. Silently no-ops when the
                    # capture has no E_ret trace (NaN rest values)
                    # or when the session lacks a setup snapshot.
                    try:
                        from ..electrode_potential_history import record_capture
                        record_capture(cap, self.session)
                    except Exception:
                        pass
                    # Per-capture damage warning — environment-posture
                    # aware. ``info`` (PBS / mISF / etc.) suppresses
                    # per-capture log spam; ``warn`` / ``alert`` emit
                    # one log line per flagged capture.
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
                    run.captures.append(cap)
                    self._emit(ExperimentEvent(kind="capture", session=self.session,
                                               run=run, capture=cap))
                    idx += 1
                    next_capture_at += self.policy.capture_interval_s
                time.sleep(0.001)
        finally:
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


