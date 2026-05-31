"""Voltage Transient (VT) characterization experiment.

This is the canonical "find the maximum charge-injection capacity" sweep that
produces all the metrics quoted in the IEEE NER 2025 paper.

Algorithm (mirrors ``runVoltageTransient.m`` in the original MATLAB suite,
but distilled into the much smaller form below):

    for each (active, return-electrodes) configuration:
        amp = ramp.starting_ua
        while amp <= ramp.max_ua and not aborted:
            1. Build a PulsePattern at this amplitude (scale the template
               pattern's *excitation* phase to ``amp``; other phases scale
               proportionally so the ratio stays intact).
            2. Program the stimulator: monitor channel = active, load the
               channel's pattern, set repetitions = infinite, start.
            3. Wait ``settle_pulses / rate_hz`` seconds for the pulse train
               to stabilize, then take *one* averaged scope capture.
            4. Stop the channel (safety) and compute metrics.
            5. Decide what to do next:
               - If *any* E_pol crossed the SIROF water window (E_lc = -0.6 V,
                 E_la = +0.8 V vs Ag|AgCl), mark this capture as having
                 reached_potential_limit and STOP the sweep.
               - If V_mon hit the stimulator's compliance rail (~±12 V),
                 mark voltage_compliance and STOP.
               - Otherwise pick the next step size: coarse step if we're far
                 from the limit, fine step if |E_pol| / |limit| > 0.7. This
                 keeps the sweep efficient but precise near the threshold.

The loop emits an ``ExperimentEvent`` after every capture so the GUI can
update the live plot, metrics table, and progress bar in real time without
polling.

Subscribers (the GUI worker, save/log code, etc.) attach via
:meth:`ExperimentRunner.subscribe`; see ``stimtest/experiments/base.py``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

from ..config import COATINGS, STIM_VOLTAGE_COMPLIANCE_V
from ..electrode import Configuration, ElectrodeArray
from ..hardware.base import Oscilloscope, Stimulator
from ..hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from ..metrics import compute_metrics
from ..readback_calibration import make_capture
from ..session import Capture, ChannelRun, Session
from ..waveforms import PulsePattern
from .base import ExperimentEvent, ExperimentResult, ExperimentRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _v_compliance_tripped(v_mon_v: "np.ndarray",
                          *,
                          threshold_v: float,
                          min_consecutive: int = 3) -> bool:
    """Return True iff ``|v_mon_v|`` exceeds ``threshold_v`` for at
    least ``min_consecutive`` consecutive samples.

    Audit finding #20 — the original compliance check was simply
    ``np.max(np.abs(v_mon_v)) > threshold_v``. A single noisy
    sample (mains pickup on an unshielded probe lead, an EMI
    transient near the bench) would trip it and abort an
    otherwise-good ramp step. Real compliance events come from
    the stimulator failing to drive the programmed current —
    they persist across the whole compliance window (tens of µs
    at minimum), so a ``min_consecutive`` of 3 is conservative:
    well below any meaningful event yet well above any
    single-sample transient.

    Implementation: build a boolean ``above`` mask and look for
    a run of ``min_consecutive`` consecutive True values using a
    cumulative-sum trick — pure-numpy, O(n), no scipy dep.
    Returns False on an empty trace (a defensive guard against
    callers that hand in a pre-acquisition placeholder).
    """
    if v_mon_v is None or len(v_mon_v) == 0:
        return False
    above = np.abs(np.asarray(v_mon_v, dtype=float)) > threshold_v
    if min_consecutive <= 1:
        return bool(above.any())
    # Count consecutive True runs by resetting on every False.
    # ``cs[i]`` is the length of the True-run ending at i.
    # Equivalent to ``itertools.groupby`` but vectorised.
    cs = np.zeros_like(above, dtype=int)
    cs[0] = int(above[0])
    for i in range(1, len(above)):
        cs[i] = cs[i - 1] + 1 if above[i] else 0
    return bool(cs.max() >= min_consecutive)


# ---------------------------------------------------------------------------
# Sweep policy
# ---------------------------------------------------------------------------
@dataclass
class RampPolicy:
    """Controls how aggressively the I_stim sweep grows at each step.

    Three strategies are supported:

    * ``"increment"`` — fixed coarse step until ratio > ``fine_threshold_ratio``,
      then fine step. The original behaviour, kept as the default.
    * ``"adaptive"`` — probe with coarse steps until enough data exists to
      fit a linear regression of ``|E_pol|/|limit|`` versus amplitude, then
      jump toward the predicted amplitude where the ratio = 1.0. Refits
      after each new capture and chases the moving estimate.
    * ``"predictive"`` — ask a trained ML predictor for the ceiling
      first. If no model is loaded or the prediction is too uncertain,
      fall back to ``adaptive`` automatically.

    The ``safety_factor`` only activates once the predicted ceiling
    starts oscillating (``oscillation_threshold`` direction reversals
    in successive predictions). A stable, monotonic prediction trail
    is left untouched.
    """
    starting_ua: float = 5.0          # initial amplitude per phase
    coarse_step_ua: float = 5.0       # added per coarse step
    fine_step_ua: float = 1.0         # added per fine step (near limit)
    fine_threshold_ratio: float = 0.7 # switch to fine when |E_pol| / |limit| > this
    max_ua: float = 1500.0            # hard ceiling
    settle_pulses: int = 3            # let stimulation settle before each capture
    # ----- adaptive / predictive knobs -----
    strategy: str = "increment"       # 'increment' | 'adaptive' | 'predictive'
    safety_factor: float = 0.85       # multiplied into predictions once oscillation kicks in
    oscillation_threshold: int = 3    # # of prediction direction-reversals before safety kicks in
    min_points_for_regression: int = 4  # below this, adaptive falls back to coarse stepping


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class VoltageTransientExperiment(ExperimentRunner):

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope, *,
                 configurations: Optional[List[Configuration]] = None,
                 ramp: Optional[RampPolicy] = None,
                 surface_area_um2: Optional[float] = None,
                 polarization_source: str = "auto",
                 predictor: Optional[object] = None,
                 cathodic_limit_v: Optional[float] = None,
                 anodic_limit_v: Optional[float] = None,
                 polarization_tolerance_v: float = 0.0):
        super().__init__(session, stimulator, oscilloscope)
        self.ramp = ramp or RampPolicy()
        self.polarization_source = polarization_source
        self.configurations = configurations or [session.test.configuration]
        # Optional :class:`stimtest.ml.QinjPredictor`. When the strategy
        # is ``"predictive"``, we ask this model for a one-shot estimate
        # of the maximum injectable amplitude up-front. ``None`` (the
        # default) means the runner falls back to the adaptive
        # regression path even in predictive mode — same behaviour the
        # MATLAB code had when its predictive heuristic ran out of
        # confidence.
        self.predictor = predictor
        # Per-configuration state for the adaptive prediction loop;
        # reset at the top of each ``_run_one_configuration``.
        self._prediction_history: List[float] = []
        self._oscillation_count: int = 0
        # NOTE: previously held ``self._r_estimate_ohm`` — a running
        # estimate of access resistance used to pre-compute V_mon
        # V/div before each capture.  Removed: it fought the
        # post-capture ``set_channel_scale_and_position_for_range``
        # observation-based scaler (V/div would jump formula-up then
        # observation-down on every step).  V_mon V/div now follows
        # the MATLAB two-stage flow exactly:
        #   1. COARSE once in ``apply_default_scope_view`` (1 V/div
        #      for ≥100 µs phases, 0.2 V/div otherwise — MATLAB
        #      ``setOscillocopeView.m`` defaults).
        #   2. FINE after each capture in ``_one_capture``'s
        #      post-capture block — port of MATLAB
        #      ``setFineScalePos2.m`` (range/(2·divs) for V/div,
        #      −mean/scale for POSition).

        # Surface area defaults to the active electrode's catalog value
        if surface_area_um2 is not None:
            self.surface_area_um2 = surface_area_um2
        else:
            try:
                self.surface_area_um2 = session.test.array[
                    self.configurations[0].active
                ].surface_area_um2
            except Exception:
                self.surface_area_um2 = 5000.0

        coat_name = session.test.array.sites[0].coating
        coat = COATINGS.get(coat_name, COATINGS["SIROF"])
        # User overrides win over the catalog. The Setup tab exposes
        # editable spinboxes for both limits so the user can dial in
        # values for a custom coating or a different reference
        # electrode without touching the catalog.
        self.cathodic_limit_v = (cathodic_limit_v if cathodic_limit_v is not None
                                 else coat.cathodic_limit_v)
        self.anodic_limit_v = (anodic_limit_v if anodic_limit_v is not None
                               else coat.anodic_limit_v)
        # Grace band on the stop check — same role as MATLAB's TOL
        # constant. A capture is treated as "limit hit" when a phase
        # crosses ``limit ± tolerance`` (more negative for cathodic,
        # more positive for anodic). 0 V → strict, no grace.
        self.polarization_tolerance_v = max(0.0, float(polarization_tolerance_v))

        # If both stim and scope are simulated, link them so the scope can
        # render whatever the stim is currently delivering.
        if isinstance(self.scope, SimulatedOscilloscope) \
                and isinstance(self.stim, SimulatedStimulator):
            self.scope.bind_stimulator(self.stim)

    # ------------------------------------------------------------------
    # Configuration kinds that share the same internal stim routing —
    # any pair of consecutive combos *both* in this set can skip the
    # PlexStim firmware: a channel acts as a passive return path only
    # when it has NO pattern loaded. There is no PS_UnloadChannel —
    # the only way to clear a previously-loaded pattern is a full
    # PS_InitAllStim (wrapped by stim.reinit()).
    #
    # Lab convention — TWO reinit triggers within a single Start-press:
    #
    # 1. ONCE at the top of run(): clean slate from any prior
    #    Start-press in the same connect session.
    # 2. BEFORE every multipolar config in the sweep loop. MP configs
    #    don't need the per-config reinit because MP's return is
    #    off-array (no on-array channel has to be in the unloaded
    #    state for the routing to work). Multipolar configs (BP, CG,
    #    PCG, PTP, PBP — anything with non-empty config.returns) DO
    #    need it: their return set spans on-array channels which must
    #    be in the unloaded state, and the previous config in the
    #    sweep may have left some of those channels loaded.
    #
    # The rule "reinit before every multipolar config" is independent
    # of which channels were previously loaded — we don't try to be
    # clever about overlap detection. PS_InitAllStim is cheap
    # (~200-500 ms), the routing-correctness cost of getting it wrong
    # is silent bad data, and the simple rule is easy to verify.

    def run(self) -> ExperimentResult:
        self.preflight()
        # NOTE: the historical ``self.stim.reinit()`` at the top of
        # run() was REMOVED.  The GUI lifecycle (see CLAUDE.md gotcha
        # #29b: "Stop tears down the stim; Start re-initializes it")
        # now guarantees a fresh device state at the top of every
        # Start press — the user's Stop press closes the stim and the
        # subsequent Start re-opens it before this run() is invoked.
        # On a normal Start with no prior Stop, the device state from
        # the previous run is harmless because each ``_one_capture``'s
        # ``stop_all`` + ``load_channel`` + ``load_zero_unused_channels``
        # cycle fully overwrites any stale patterns before stim starts.
        #
        # The reinit-at-top was also the SECOND ``ps_close_all_stim``
        # in a 4-call cascade (GUI close + GUI open's embedded close +
        # this reinit's close + this reinit's open's embedded close)
        # that crashed the vendor DLL with HEAP_CORRUPTION (Windows
        # 0xC0000374).  See plexon.py ``_is_open`` for the device-
        # level guard that complements this removal; both fixes are
        # needed to bring the per-Stop/Start cascade down from 4 to 1.
        #
        # Per-config reinit BEFORE multipolar configs (below) is KEPT
        # — multipolar configs require ``returns`` channels to be
        # unloaded, and reinit is the only way to enforce that
        # cleanly without per-channel iteration.
        all_captures: List[Capture] = []
        # ``current_configuration`` exposes the live config to the GUI's
        # ``_capture_key`` lookup so each (channel, combo) gets its own
        # plot page + metrics row in the Experiment tab.  Without this,
        # ``_capture_key`` falls back to ``session.test.configuration``
        # (a STATIC reference to the FIRST configuration set at runner
        # construction), and every capture from every subsequent
        # configuration overwrites the first config's page — the user
        # sees one plot that gets replaced rather than per-config rows
        # to toggle between, and metrics for configs 2+ never make it
        # into the side panel.  Set BEFORE the first config's
        # ``_run_one_configuration`` to override the stale fallback,
        # and updated per iteration below.
        self.current_configuration = None
        try:
            n_configs = len(self.configurations)
            for cfg_idx, config in enumerate(self.configurations):
                if self.aborted:
                    break
                # Update the live-config marker so any ``capture`` event
                # the runner emits below resolves to THIS config's
                # display name in the GUI.
                self.current_configuration = config
                # Between-channel pause: when the operator wires one channel
                # at a time, give them a chance to swap before the next
                # configuration starts.  Skips the very first config (nothing
                # to rewire from). Skips when ``pause_between_channels`` is
                # off — ``wait_for_continue`` short-circuits in that case.
                if cfg_idx > 0:
                    next_label = config.display_name()
                    if not self.wait_for_continue(
                            next_config_label=f"channel {config.active} "
                                              f"({next_label})"):
                        break
                # Per-config reinit before multipolar (anything with
                # non-empty returns). Cheap and unconditional —
                # accepts a redundant reinit on the very first config
                # of a multipolar-first sweep (start-of-run already
                # cleared the device) rather than tracking an extra
                # "just reinit'd" flag.
                if config.returns:
                    try:
                        self.stim.reinit()
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session,
                            message=(
                                f"Stimulator reinit before {config.id} "
                                f"{config.display_name()} (multipolar "
                                f"config — returns must be unloaded)."
                            )))
                    except Exception as e:
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session,
                            message=f"Stimulator reinit failed: {e}"))
                run = self._run_one_configuration(config)
                self.session.add_run(run)
                all_captures.extend(run.captures)
                self._emit(ExperimentEvent(kind="run_end", session=self.session, run=run))
        except Exception as e:
            self._emit(ExperimentEvent(
                kind="aborted", session=self.session,
                message=f"Experiment failed: {e}",
            ))
            return ExperimentResult(session=self.session, captures=all_captures,
                                    aborted=True, error=str(e))

        self._emit(ExperimentEvent(kind="session_end", session=self.session))
        return ExperimentResult(session=self.session, captures=all_captures,
                                aborted=self.aborted)

    # ------------------------------------------------------------------
    def _run_one_configuration(self, config: Configuration) -> ChannelRun:
        run = ChannelRun(configuration=config, surface_area_um2=self.surface_area_um2)
        self._emit(ExperimentEvent(
            kind="run_start", session=self.session, run=run,
            message=f"Sweep {config.display_name()}",
        ))

        base_pattern = self.session.test.pattern
        # Revert the scope to its default per-channel view (MATLAB
        # setDefaultScopeView3.m).  Resets adapt history and re-applies
        # horizontal + vertical defaults so the new monitor channel
        # starts fresh instead of inheriting the previous channel's
        # converged scales.
        #
        # Pass the configuration-shape flag + environment short-code so
        # the MATLAB V/div decision tree picks the right default.  MP
        # configs (no return list) take the tighter 1 V/div (≥100 µs)
        # / 0.2 V/div (<100 µs) defaults; multipolar uses 2 V/div / 0.5
        # V/div instead.  Animal environments bump short-phase patterns
        # to 2 V/div to ride out baseline drift.
        _is_multipolar = bool(getattr(config, "returns", ()))
        _env_short = None
        try:
            extras = (self.session.test.extras or {})
            snap = extras.get("setup_snapshot") or {}
            if isinstance(snap, dict):
                _env_short = snap.get("environment_short")
        except Exception:
            _env_short = None
        self.apply_default_scope_view(
            base_pattern, amp_ua=self.ramp.starting_ua,
            is_multipolar=_is_multipolar,
            environment_short=_env_short,
            reason=f"start of channel {config.active}")
        amp = self.ramp.starting_ua
        capture_idx = 0
        # Reset the adaptive bookkeeping so each new configuration
        # starts with a clean prediction trail.
        self._prediction_history = []
        self._oscillation_count = 0

        # ----- main amplitude ramp ---------------------------------------
        # We ramp the stimulus amplitude upward, capturing one waveform per
        # step. Steps shrink near the safety limits so we land *just* below
        # the SIROF water window without overshooting.
        # Guard against a zero-amplitude excitation phase (can happen
        # when the user enters a fully-zeroed asymmetric pattern, or
        # if a stale prefs restore lands an empty template). Falls back
        # to 1.0 µA so the ``scaled()`` call doesn't ZeroDivisionError
        # mid-sweep — preflight will already have flagged a no-phases
        # pattern, so we only need to defend against the zero case.
        excite_amp_abs = abs(base_pattern.excitation_phase.amplitude_ua) or 1.0
        while amp <= self.ramp.max_ua and not self.aborted:
            # Scale the template pattern so its *excitation* phase magnitude
            # equals ``amp``. Other phases scale by the same factor, which
            # preserves the biphasic / triphasic ratio specified by the user.
            pattern = base_pattern.scaled(amp / excite_amp_abs)
            cap = self._one_capture(config, pattern, capture_idx)
            run.captures.append(cap)
            capture_idx += 1
            self._emit(ExperimentEvent(
                kind="capture", session=self.session, run=run, capture=cap,
            ))

            if cap.status.aborted:
                break

            limit_hit = self._potential_limit_hit(cap)
            compliance = cap.status.voltage_compliance

            if limit_hit or compliance:
                cap.status.reached_potential_limit = limit_hit
                # Walk back to last good amplitude and stop
                break

            amp += self._next_step(cap, amp, run.captures)

        # Stop output for safety.  ``stop_all`` (= PS_StopStimAllChannels,
        # MATLAB ``stopStimulation``) so the unused zero-amplitude
        # channels that were brought up alongside the active in
        # ``_one_capture`` are also quieted on the way out of the
        # configuration.
        try:
            self.stim.stop_all()
        except Exception:
            pass

        # Compute & store final summary on the run
        from datetime import datetime
        run.finished_at = datetime.now()
        return run

    # ------------------------------------------------------------------
    def _one_capture(self, config: Configuration, pattern: PulsePattern,
                     index: int) -> Capture:
        # ----- 1. Program the stimulator ---------------------------------
        # set_monitor_channel routes V_mon and I_mon outputs to this channel
        # so the scope sees what the active electrode is doing. We use 0
        # repetitions (== "stimulate forever") and stop manually below.

        # Adaptive scope view: update I_mon vertical scale AND trigger
        # level before each step so the captured waveform tracks the
        # programmed current.  Without these, the trace at low amps is
        # invisible (scale set for max amp) and the trigger threshold
        # may fall below the noise floor.  Mirrors MATLAB
        # ``setOscilloscopeCurrentScale.m`` + ``setTriggerLevel.m``.
        try:
            # Use FIRST PHASE (``phases[0]``) — NOT ``excitation_phase``
            # — for the trigger-level update.  The I_mon scope trigger
            # fires on whichever phase the stimulator emits first, and
            # on some patterns ``phases[0]`` differs from
            # ``excitation_phase`` (anodic-first protocols, certain
            # triphasic shapes).  Phase-1 amplitude + width is what
            # ``imon_trigger_level`` needs to compute the correct
            # threshold for the leading edge the scope actually sees.
            _ph0 = pattern.phases[0] if pattern.phases else None
            _amp_signed = (float(_ph0.amplitude_ua)
                           if _ph0 is not None else 0.0)
            ph_us = (float(_ph0.width_us)
                     if _ph0 is not None else 200.0)
            # I_mon vertical scale + trigger level are amplitude-derived
            # via analytical formulas — they ARE the proactive setting
            # for I_mon (no observation needed; the I_mon peak is
            # exactly ``amp × imon_scaling``).  V_mon and the
            # potential channels are handled differently: a single
            # COARSE V/div at run start (``apply_default_scope_view``,
            # MATLAB rule), then FINE refinement after each capture
            # (``set_channel_scale_and_position_for_range``, MATLAB
            # ``setFineScalePos2``).  We no longer pre-set V_mon
            # V/div per amplitude — the previous formula-based
            # ``initial_channel_scales(load_r=R_estimate)`` call
            # competed with the post-capture observation-based
            # write, producing visible V/div jitter every step.
            self.update_imon_vertical_scale(abs(_amp_signed))
            self.update_imon_trigger_level(
                _amp_signed, phase_width_us=ph_us)
        except Exception:
            pass

        try:
            # ---- Defensive stop BEFORE load_channel ---------------------
            # Matches MATLAB ``stopStimulation()`` before ``setPattern``
            # in ``runProgressiveStress.m`` / ``runVoltageTransient.m``.
            # ``PS_LoadChannel`` for an arbitrary pattern uploads the
            # full .pat byte stream over USB and takes ~50-200 ms — we
            # want the device demonstrably quiescent during that window
            # rather than continuing the previous step's pattern.
            #
            # Idempotent on an already-stopped device, so the cost when
            # the previous iteration's finally already stopped is just
            # one extra SCPI round-trip.  Critical when the previous
            # finally was skipped (early-return on a stim-program error,
            # an aborted re-capture, etc.) — without this explicit stop,
            # ``load_channel`` would land on a still-running stim.
            #
            # Uses ``stop_all`` (= PS_StopStimAllChannels) rather than
            # ``stop_channel(active)`` because the previous iteration
            # also fired the unused zero-amplitude channels via
            # ``start_all``; ``stop_all`` quiets them too so the upcoming
            # ``load_zero_unused_channels`` reloads on a fully-stopped
            # device.
            try:
                self.stim.stop_all()
            except Exception:
                pass
            self.stim.set_monitor_channel(config.active)
            self.stim.load_channel(config.active, pattern)
            self.stim.set_repetitions(config.active, 0)  # infinite for sweep, then stop
            # Load a zero-amplitude, same-duration copy of the pattern
            # onto every "unused" channel (anything not active and not
            # in returns).  Port of MATLAB ``setPattern.m`` Zero Current
            # block — keeps unused channels TICKING in sync with the
            # active channel's pulse cycle rather than carrying a stale
            # pattern from a previous step or falling out of cadence.
            # Returns are intentionally excluded so they stay UNLOADED
            # (passive sink); CG configs are auto-skipped because their
            # returns span every other channel (unused set is empty).
            self.load_zero_unused_channels(pattern, config)
            # Use start_all (= PS_StartStimAllChannels) instead of
            # start_channel — single-channel start hits error code 4
            # ("WRONG TRIGGER MODE") on default-mode PlexStim devices.
            # Loaded channels (active + unused-with-zero) fire together;
            # the zero-amp channels deliver no current.
            self.stim.start_all()
        except Exception as e:
            cap = Capture(index=index, pattern=pattern)
            cap.status.aborted = True
            cap.status.notes = f"Stimulator program error: {e}"
            return cap

        # ----- Stim runs CONTINUOUSLY across every capture below --------
        # Critical correctness rule (per user spec):
        #
        #   "You can stop pulsing after the waveform is acquired, but
        #    you then need to stimulate again when trying to get a new
        #    waveform."
        #
        # The iterative fit-the-view loop (block 2b) fires additional
        # ``single_capture()`` calls to verify each rescale converged
        # on the in-view condition.  Each of those re-captures NEEDS
        # the stim still pulsing — otherwise the scope triggers on
        # noise (or doesn't trigger at all in NORMAL mode), the
        # observed (min, max) collapses to the noise floor, and the
        # next fine-scaler write picks a V/div sized for noise rather
        # than the pulse.  Previous revision stopped the channel in a
        # ``finally`` right after the first capture, so every iterative
        # re-capture in 2b ran without stim — visible in the GUI as
        # the V_mon trace clipping to ±25 mV (the noise envelope on
        # the V_mon BNC) regardless of the programmed amplitude.
        #
        # Solution: ONE outer try/finally that wraps EVERY capture in
        # this method.  ``stop_channel`` runs once on the way out, no
        # matter how many iterative re-captures happened.  An early
        # ``return`` from the inner first-capture exception branch
        # still triggers the outer finally, so the stim never leaks
        # past ``_one_capture``.
        try:
            # ----- 2. Let it settle, then grab one averaged capture ---------
            # The scope is in AVERAGE mode (set in run()); we wait long enough
            # for ``settle_pulses`` triggers so the average has converged before
            # we pull the curve. Skip entirely when both backends are simulated
            # — the sim scope returns a deterministic averaged frame
            # synchronously, so the wait is pure overhead. Real hardware needs
            # the wait so the trigger has time to fire and the average converges.
            #
            # Pulse-rate-aware timeout: ``N_avg / rate_hz + 5 s headroom``.
            # The driver default (``self._timeout_ms / 1000``, typically
            # 10 s) is too tight for low pulse rates — 64 averages at
            # 1 Hz needs 64 s.  The scope's soft-timeout path reads the
            # rolling average regardless, but that average is incomplete
            # if the budget expires mid-accumulation.  Headroom covers
            # trigger latency + USB-TMC round-trip jitter.
            try:
                # Mirror the LP / SP / PS pattern: pull the configured
                # ``ACQuire:NUMAVg`` count from the scope itself rather
                # than a runner attribute (only the scope owns the
                # truth after ``set_acquisition_mode``).
                _navg = int(getattr(self.scope,
                                    "_expected_acq_navg", None) or 0)
                _rate = float(getattr(pattern, "rate_hz", 0.0) or 0.0)
                if _navg > 0 and _rate > 0:
                    # Headroom = 6 s (5 s for trigger latency / USB-TMC
                    # round-trip jitter + 1 s extra requested by the
                    # operator after observing tight margins on a
                    # first-frame capture).
                    _capture_timeout_s = (_navg / _rate) + 6.0
                else:
                    _capture_timeout_s = None  # fall back to driver default
            except Exception:
                _capture_timeout_s = None
            try:
                acq = self.scope.single_capture(timeout_s=_capture_timeout_s)
            except Exception as e:
                # Emit a log event so the operator sees WHY this capture
                # came back empty.  Without this the GUI shows a blank
                # plot + NaN metrics row with no explanation (most common
                # cause: scope trigger timeout because the trigger source
                # / level was misconfigured for the current pulse polarity
                # or amplitude).
                self._emit(ExperimentEvent(
                    kind="log", session=self.session,
                    message=(f"⚠ Scope capture error at "
                             f"{_amp_signed:+.1f} µA "
                             f"(capture #{index + 1}): "
                             f"{type(e).__name__}: {e}")))
                cap = Capture(index=index, pattern=pattern)
                cap.status.aborted = True
                cap.status.notes = f"Scope capture error: {e}"
                return cap

            # Sanity check: largest I_mon edge should land at t ≈ 0 µs.
            # Emits a ⚠ log entry if the time axis is misaligned.
            try:
                self.check_trigger_alignment(acq)
            except Exception:
                pass

            # ----- 2b. Iterative fit-the-view loop ---------------------------
            # Port of MATLAB ``getWaveform2.m``: after each capture, check
            # whether every voltage trace fits inside the scope's visible
            # window (``±MAX_FACTOR`` divs around the channel's POSition).
            # If a trace falls outside its window, rescale + re-capture
            # until it fits, capped at MAX_RECAPTURE extra attempts (MATLAB
            # caps fine-scale iterations at 3 total via
            # ``fineScale_count > 2 → isInRange = true``).
            #
            # NOTE: every re-capture inside this loop happens with the
            # stim STILL RUNNING from the start_all() outside the outer
            # try.  The outer ``try / finally`` (this block's parent)
            # calls ``stop_channel`` ONCE on the way out — never between
            # captures — because stopping mid-loop would make the next
            # ``single_capture()`` acquire the noise floor (no trigger
            # in NORMAL mode), the fine-scaler would size V/div for
            # noise, and the V_mon trace would clip to ±25 mV regardless
            # of the programmed amplitude.  This was the previous bug.
            #
            # divs budget per channel:
            #   * V_mon → 3 divs (leaves ~5 divs of headroom for the next
            #     amplitude step's growing peak — VT ramps monotonically,
            #     so the NEXT capture is always larger; tighter V/div
            #     would clip).
            #   * E_act / E_ret → 4 divs (DC-biased traces with small AC
            #     swing — fit comfortably and leave room for polarization
            #     growth).
            #
            # I_mon is NOT included in this loop: it's a current monitor
            # whose peak is exactly ``amp × imon_scaling`` — known
            # analytically and already set by ``update_imon_vertical_scale``
            # before the first capture.  MATLAB skips it via
            # ``if isCurrentChannel: isInRange = true``.
            # MAX_FACTOR — "almost full ±N divs" margin, with 0.1 div
            # of leave-room.  MUST be derived from the SCOPE'S actual
            # vertical division count, NOT a hardcoded number:
            #   * TBS1104B / TDS / TPS (8 vert divs): half = 4.0 →
            #     MAX_FACTOR = 3.9.  This is what MATLAB
            #     ``getWaveform2.m`` baked in.
            #   * TBS2204B and the rest of the TBS2000* family (10 vert
            #     divs): half = 5.0 → MAX_FACTOR = 4.9.  Using the
            #     hardcoded 3.9 here would treat 25 % of the screen
            #     as off-limits and trip the "out of view" branch
            #     prematurely.
            # The scope's ``_half_vert_divs`` is set at connect time
            # from the per-series spec in tektronix_models.py (see
            # ``TektronixOscilloscope.open()`` step 5/6).  Simulators
            # inherit the conservative 4.0 default from base.
            _half_divs = float(getattr(self.scope, "_half_vert_divs", 4.0))
            MAX_FACTOR = max(0.5, _half_divs - 0.1)
            # Bumped from 2 → 4 to give the calibration-style
            # ``adapt_channel_scale`` enough headroom to traverse the
            # 1-2-5 grid on aggressive shrinks.  Worst case: a V_mon
            # at the apply_default_scope_view default of 500 mV/div
            # whose true signal is ±5 mV needs to shrink through
            # 200→100→50→…→5 mV/div on the 1-2-5 grid.  At one shrink
            # step per attempt that's up to 7 attempts; the cap is
            # set lower (5 total) because in practice the snap-UP
            # math jumps multiple grid cells per call (a single adapt
            # call on observed 32 mV picks 10 mV/div directly), and
            # ``adapt_channel_scale``'s internal lock detection bails
            # if it ever oscillates between two adjacent cells.
            # MATLAB's ``fineScale_count > 2`` was an under-estimate
            # for the snap-up grid behaviour.
            MAX_RECAPTURE = 4
            # I_mon IS included now (was excluded previously on the
            # assumption that the analytical ``update_imon_vertical_scale``
            # always sized it right).  Real-world: an under-sized I_mon
            # scale clips at the scope's ±4-div rail and the analytical
            # formula has no way to know.  Adding I_mon to the loop lets
            # the clip detector catch it and expand.  divs_budget for
            # I_mon is 3 (matches V_mon — both are zero-symmetric signals
            # so 3 divs each side of zero gives a 1-div headroom for the
            # next amplitude's growing peak).
            _voltage_roles = ("vmon", "imon", "eret", "eact")
            _divs_for = {"vmon": 3.0, "imon": 3.0,
                         "eret": 4.0, "eact": 4.0}
            # Coarse-step factor — when clip detection trips, multiply
            # the CURRENT V/div by this factor and re-capture.  MATLAB's
            # equivalent ``vertScale_idx + 5`` jumps ~100× (5 stops on
            # the 1-2-5 grid); we use a tamer 2.5× per step (~2 stops)
            # so two iterations cover a 6× range — enough to catch the
            # realistic "scale was 4× too tight" cases without over-
            # shooting to 100× and squishing the trace.
            CLIP_COARSE_FACTOR = 2.5
            try:
                import numpy as _np
                aliases_obs = getattr(self.scope, "channel_aliases", {}) or {}
                # Capability probe — pick the first available voltage role's
                # physical channel and see whether the scope can introspect
                # its current V/div + POSition.  If it can't (simulator,
                # SCPI silently dropping the query), fall back to a single
                # write per channel without iterating: no point in re-
                # capturing if we can't tell whether the new scale fit.
                _probe_ch = None
                for _r in _voltage_roles:
                    _probe_ch = aliases_obs.get(_r)
                    if _probe_ch:
                        break
                _introspectable = False
                if _probe_ch is not None:
                    _probe = self.scope.channel_in_view(
                        _probe_ch, 0.0, 1.0, margin_divs=MAX_FACTOR)
                    _introspectable = _probe is not None

                # Per-attempt diagnostic log lines so a session .txt log
                # shows EXACTLY what the in-view loop did each capture.
                # Without this, "you did not adjust the vertical scaling"
                # complaints have no traceable evidence — the only way
                # to verify the loop was running was to attach a debugger.
                _diag_lines = []
                _diag_lines.append(
                    f"  in-view loop start: introspectable={_introspectable}, "
                    f"available_roles="
                    f"{[r for r in _voltage_roles if aliases_obs.get(r)]}")
                for _attempt in range(MAX_RECAPTURE + 1):
                    _any_rescaled = False
                    _chan_data = getattr(acq, "channels", {}) or {}
                    _t_us_arr = _np.asarray(
                        getattr(acq, "time_us", None) or [],
                        dtype=float)
                    for _role in _voltage_roles:
                        _ch_name = aliases_obs.get(_role)
                        if not _ch_name:
                            continue
                        _arr = _chan_data.get(_ch_name)
                        if _arr is None:
                            continue
                        _a = _np.asarray(_arr, dtype=float)
                        if _a.size < 2:
                            continue
                        _lo = float(_a.min())
                        _hi = float(_a.max())
                        if not (_np.isfinite(_lo) and _np.isfinite(_hi)):
                            continue
                        _divs = _divs_for[_role]
                        # ---- Baseline-centered approach for E_ret / E_act
                        # Real VT data shows E_ret is mostly FLAT at the
                        # electrode rest potential with TRANSIENT spikes
                        # during the pulse — the data mean (pulled UP by
                        # spikes) is a poor position reference.  Instead:
                        #
                        #   1. Compute robust baseline from PRE-TRIGGER
                        #      samples (t < -1 µs) — the interpulse
                        #      window IS the rest potential by
                        #      construction; the pulse spikes are
                        #      excluded.
                        #   2. Compute ONE-SIDED swing relative to that
                        #      baseline: max(|max − baseline|,
                        #      |baseline − min|).  This is what we
                        #      actually need to fit on one side of
                        #      screen centre after positioning.
                        #   3. Synthesize a SYMMETRIC input
                        #      ``(baseline ± swing)`` and feed it to
                        #      ``compute_scale_position_targets``.  Its
                        #      mid-point = baseline (so the resulting
                        #      POSition centres the BASELINE, not the
                        #      pulse-pulled mean), and Vpp = 2 × swing
                        #      (so the V/div is sized for the max
                        #      excursion on either side).
                        #
                        # For V_mon / I_mon (zero-centred AC signals)
                        # the baseline path is skipped — the existing
                        # adapt-with-observed-range works fine because
                        # mean ≈ 0 makes (min+max)/2 ≈ 0 already.
                        # ``_target_lo`` / ``_target_hi`` carry the
                        # range we feed to adapt + coord-helper.  They
                        # start as the raw observed values; for
                        # E_ret / E_act we replace them with the
                        # baseline-symmetric synthetic range below.
                        # We keep the originals (``_lo`` / ``_hi``) for
                        # the diagnostic log so the operator sees the
                        # ACTUAL observed range.
                        _target_lo, _target_hi = _lo, _hi
                        _baseline = None  # for logging
                        _swing = None     # for logging
                        if _role in ("eret", "eact") and _t_us_arr.size == _a.size:
                            _pre_mask = _t_us_arr < -1.0
                            if _np.count_nonzero(_pre_mask) >= 8:
                                _pre = _a[_pre_mask]
                                # MAD-clipped mean: median ± 3 × MAD ×
                                # 1.4826 (the MAD→σ correction factor
                                # for a Gaussian).  Spikes / outliers
                                # that survive the time-window mask get
                                # rejected before averaging.
                                _med = float(_np.median(_pre))
                                _mad = float(_np.median(
                                    _np.abs(_pre - _med)))
                                if _mad > 0:
                                    _keep = _np.abs(_pre - _med) <= (
                                        3.0 * _mad * 1.4826)
                                    _baseline = (float(_pre[_keep].mean())
                                                 if _keep.any() else _med)
                                else:
                                    _baseline = _med
                                # One-sided swing — the larger of the
                                # two excursions above/below baseline.
                                _swing = max(abs(_hi - _baseline),
                                             abs(_baseline - _lo))
                                if _swing > 0:
                                    # Synthesize baseline-symmetric
                                    # input.  ``compute_scale_position_targets``
                                    # sees mid = baseline (so position
                                    # centres it) and Vpp = 2×swing (so
                                    # V/div sizes for max excursion).
                                    _target_lo = _baseline - _swing
                                    _target_hi = _baseline + _swing
                        # Two-stage decision:
                        #   1. CLIP CHECK — if the observed (min, max)
                        #      is sitting at the ±4-div ADC rail, the
                        #      TRUE peak is higher than the captured
                        #      data shows.  Sizing the new V/div from
                        #      the observed range would produce the
                        #      same V/div as before (since observed ==
                        #      rail) and never expand.  Coarse-step UP
                        #      instead: multiply current V/div by
                        #      CLIP_COARSE_FACTOR (~2.5×) and re-
                        #      capture.  Next iteration sees the
                        #      now-non-clipped data and fine-fits.
                        #   2. FINE FIT — when not clipped, use the
                        #      observed range to fit the trace into
                        #      ``_divs`` divisions via the standard
                        #      range/(2·divs) + mean-offset port of
                        #      ``setFineScalePos2``.
                        # ---- CALIBRATION-STYLE rescale --------------
                        # Per user feedback ("look at how the
                        # calibration is changing coarse vertical
                        # scales"), defer to the stateful
                        # :meth:`adapt_channel_scale` primitive — the
                        # same one calibration.py uses.  It is the
                        # robust, MATLAB-mirroring V/div manager:
                        #
                        #   * **Both directions**: clip → upscale,
                        #     small signal → downscale.  Eliminates
                        #     the "squished waveform" mode where the
                        #     trace fits with too much headroom and
                        #     8-bit ADC quantization (~16 mV/step at
                        #     500 mV/div) becomes visible as
                        #     stair-stepping.
                        #   * **Stateful per channel**: keeps a
                        #     history of every picked scale, detects
                        #     oscillation between adjacent grid
                        #     cells, hysteresis on shrink, hard cap
                        #     on retries — none of which the prior
                        #     homegrown loop had.
                        #   * **Calibration-proven**: this exact
                        #     primitive runs the per-amplitude
                        #     calibration sweep and converges
                        #     reliably across the full V_mon /
                        #     I_mon range.
                        #
                        # The ``_clipped`` extrapolation trick from
                        # calibration: when the trace sits at the
                        # ADC rail, the TRUE peak is unknown but at
                        # least 2× the observed value.  Doubling
                        # vlo/vhi forces ``adapt`` to size for a
                        # larger range on the next iteration.
                        def _clipped_arr(arr):
                            mn, mx = arr.min(), arr.max()
                            n = len(arr)
                            return (_np.sum(arr == mn) > 0.05 * n or
                                    _np.sum(arr == mx) > 0.05 * n)
                        _is_clipped = _clipped_arr(_a)
                        _clip_str = "CLIPPED" if _is_clipped else "not-clipped"
                        # ---- MATLAB-style in-view check ----------
                        # Verify the observed (v_min, v_max) actually
                        # fits within the visible window
                        # (``±MAX_FACTOR`` divs from the scope's
                        # current ``POSition``).  MAX_FACTOR is the
                        # MATLAB-faithful margin: 3.9 div on 8-vert-
                        # div scopes (TBS1000 / TDS / TPS), 4.9 div
                        # on 10-vert-div TBS2000-series.  Catches
                        # the subtle case where the trace exceeds
                        # the visible budget but DOESN'T saturate
                        # the ADC rail (e.g. an overshoot riding
                        # just above the 3.9-div line at 4.0-4.2
                        # div) — ``_clipped_arr`` misses this
                        # because the samples never settle at the
                        # rail.  ``channel_in_view`` returns None
                        # on simulator / SCPI failure (treat as
                        # "can't check" — don't extrapolate).
                        _in_view = None
                        try:
                            _in_view = self.scope.channel_in_view(
                                _ch_name, _lo, _hi,
                                margin_divs=MAX_FACTOR)
                        except Exception:
                            pass
                        # ---- Directional out-of-view detection -----
                        # ``channel_clip_sides`` returns a 5-tuple
                        # exposing WHICH SIDE the trace exceeds
                        # (above, below, or both) plus the
                        # position-only shift that would recentre it.
                        # When out-of-view is one-sided AND the
                        # opposite side has slack, we can fix it with
                        # a POSITION nudge alone (no V/div change,
                        # preserves ADC resolution) instead of
                        # symmetrically growing the V/div.  The
                        # symmetric-grow path stays as the fallback
                        # for both-sides-out + ADC-rail-clip.
                        _clip_sides = None
                        if _in_view is False:
                            try:
                                _clip_sides = self.scope.channel_clip_sides(
                                    _ch_name, _lo, _hi,
                                    margin_divs=MAX_FACTOR)
                            except Exception:
                                _clip_sides = None
                        # Build the in-view diagnostic string with
                        # directional info when available.
                        if _in_view is True:
                            _in_view_str = "in-view"
                        elif _in_view is False:
                            if _clip_sides is not None:
                                _below, _above, _shift, _hr_b, _hr_a = _clip_sides
                                if _below and _above:
                                    _in_view_str = (
                                        f"out-BOTH(±{MAX_FACTOR:.1f}div, "
                                        f"hr_b={_hr_b:+.2f}, "
                                        f"hr_a={_hr_a:+.2f})")
                                elif _below:
                                    _in_view_str = (
                                        f"out-BELOW(hr_b={_hr_b:+.2f}, "
                                        f"hr_a={_hr_a:+.2f}, "
                                        f"shift={_shift:+.2f}div)")
                                elif _above:
                                    _in_view_str = (
                                        f"out-ABOVE(hr_b={_hr_b:+.2f}, "
                                        f"hr_a={_hr_a:+.2f}, "
                                        f"shift={_shift:+.2f}div)")
                                else:
                                    # channel_in_view said False but
                                    # channel_clip_sides says no side
                                    # out — race condition or rounding.
                                    _in_view_str = (
                                        f"out-?(±{MAX_FACTOR:.1f}div)")
                            else:
                                _in_view_str = (
                                    f"out-of-view(±{MAX_FACTOR:.1f}div)")
                        else:
                            _in_view_str = "in-view?-unknown"
                        # ``_target_lo`` / ``_target_hi`` is what we
                        # pass to adapt + coord-helper — already either
                        # the raw observed range (V_mon / I_mon) or the
                        # baseline-symmetric synthetic range (E_ret /
                        # E_act, set above).  ``_adapt_lo`` /
                        # ``_adapt_hi`` further extends it when the
                        # observed data is at the rail (clipped) or
                        # exceeds the in-view budget — both signals
                        # that the TRUE peak is bigger than the
                        # captured range shows.
                        _adapt_lo, _adapt_hi = _target_lo, _target_hi
                        # ---- Conservative out-of-view handling -----
                        # User-spec insight (mirrors the MATLAB
                        # ``getWaveform2.m`` design): the 0.1 div gap
                        # between MAX_FACTOR (3.9 or 4.9) and the true
                        # rail (4.0 or 5.0) exists BECAUSE
                        # out-of-view almost always means the trace
                        # is at or near the ADC rail.  The captured
                        # ``(v_min, v_max)`` is then TRUNCATED — the
                        # true peak exceeds the observed value by an
                        # unknown amount.  Any decision based on the
                        # observed midpoint
                        # ``(v_min + v_max) / 2`` is BIASED toward
                        # the visible side; a position nudge derived
                        # from it can land the trace right back at
                        # the rail.
                        #
                        # Therefore: ALL out-of-view conditions (both
                        # the explicit ``_is_clipped`` rail-sample
                        # detection AND the more sensitive
                        # ``_in_view is False`` MAX_FACTOR exceedance)
                        # are treated as "probably saturated → grow
                        # V/div via symmetric extrapolation".  No
                        # position-only nudge path — the safer
                        # default is to widen the window, capture
                        # again with the trace fully visible, and
                        # let the next attempt use a FAITHFUL
                        # observed range to do any fine recentering.
                        #
                        # The directional ``channel_clip_sides`` info
                        # is still surfaced in the diagnostic log
                        # (``fit=out-ABOVE`` / ``fit=out-BELOW`` /
                        # ``fit=out-BOTH``) so post-mortem analysis
                        # shows which side went off — useful for
                        # tuning per-electrode V/div defaults — but
                        # the per-attempt ACTION is always
                        # "grow V/div".
                        if _is_clipped or (_in_view is False):
                            # Observed range is a lower bound on the
                            # true range.  Doubling forces ``adapt``
                            # to size for at least 2× the observed
                            # extent — escapes the rail in one step.
                            _adapt_lo = _target_lo * 2.0
                            _adapt_hi = _target_hi * 2.0
                        # Read current V/div BEFORE adapt so we can
                        # tell after the call whether adapt SHRUNK
                        # (signaling small magnitude → fine
                        # scale+position appropriate) or GREW
                        # (signaling large/clipped magnitude →
                        # coarse only; skip fine positioning per
                        # user-spec "fine scaling and positioning
                        # is for small magnitude waveforms.
                        # Otherwise, just coarse scaling.").
                        _pre_adapt_vpd = None
                        try:
                            _pre_adapt_vpd = float(
                                self.scope._q(f"{_ch_name}:SCAle?"))
                        except Exception:
                            pass
                        _result = None
                        try:
                            _new_scale = self.scope.adapt_channel_scale(
                                _ch_name,
                                v_min=_adapt_lo, v_max=_adapt_hi,
                                divs=_divs,
                                # ``shrink_stable_count=1`` matches
                                # calibration — single vote shrink
                                # since each VT amplitude is a
                                # fresh decision (no per-amp
                                # captures to noise-flicker on).
                                shrink_stable_count=1,
                            )
                            if _new_scale is not None:
                                _any_rescaled = True
                                _result = (f"adapt → "
                                           f"{_new_scale*1e3:.2f} mV/div")
                                # ---- bias_ratio-coordinated position ---
                                # For DC-biased roles (eret/eact at the
                                # electrode rest potential), the V/div
                                # adapt picked is sized for the SWING
                                # alone — it doesn't know about the
                                # mean.  Compute the coordinated targets
                                # explicitly via
                                # ``compute_scale_position_targets``,
                                # which uses ``bias_ratio = 2|mean|/Vpp``
                                # to decide whether V/div needs to be
                                # coarsened so the position offset fits
                                # within the ±5-div hardware limit.
                                # When bias_ratio > 1 (DC-dominated),
                                # adapt's swing-only V/div would leave
                                # POSition clamped and the trace would
                                # sit off-screen — override with the
                                # coordinated V/div.  Skipped for V_mon
                                # and I_mon (always AC-centered around
                                # zero; bias_ratio ≈ 0).
                                _bias_ratio = 0.0
                                _regime = "AC-centered"
                                # ---- "Fine = small-magnitude only" gate
                                # Per user-spec: fine scaling +
                                # positioning is ONLY for small-magnitude
                                # waveforms.  Otherwise, just coarse
                                # scaling (adapt's V/div alone, no
                                # position helper).
                                #
                                # Criterion: adapt SHRUNK V/div this
                                # iteration (``_new_scale < _pre_adapt_vpd``).
                                # A shrink means the observed signal is
                                # smaller than the previous V/div was
                                # accommodating — exactly the
                                # "small-magnitude" case the user
                                # described.  When adapt GREW V/div
                                # (large or clipped signal) or kept it
                                # the same (already converged), we skip
                                # the fine helper and let position stay
                                # wherever it was (default 0 from
                                # apply_default_scope_view, or whatever
                                # the previous fine-fit picked).
                                _adapt_shrunk = (
                                    _pre_adapt_vpd is not None
                                    and _new_scale is not None
                                    and _new_scale < _pre_adapt_vpd)
                                # Annotate the per-attempt result so a
                                # grep for "coarse-only" surfaces every
                                # capture where the fine-position path
                                # was deliberately skipped.
                                if (_role in ("eret", "eact")
                                        and not _adapt_shrunk):
                                    _result += " [coarse-only, large magnitude]"
                                if _role in ("eret", "eact") and _adapt_shrunk:
                                    # Use baseline-symmetric inputs so
                                    # mid-point = baseline (puts the
                                    # rest potential at screen centre)
                                    # rather than (min+max)/2 (which
                                    # gets pulled by pulse spikes).
                                    # ``_target_lo`` / ``_target_hi``
                                    # already carry the right values
                                    # — synthesized above for E_ret /
                                    # E_act when pre-trigger samples
                                    # were available, otherwise raw
                                    # observed.
                                    if _new_scale > 0:
                                        try:
                                            _targets = self.scope.compute_scale_position_targets(
                                                _target_lo, _target_hi,
                                                divs=_divs,
                                                grid=self.scope._TEK_VERTICAL_GRID_VPD,
                                                position_limit_divs=5.0,
                                            )
                                        except Exception:
                                            _targets = None
                                        if _targets is not None:
                                            (_coord_vpd, _coord_pos,
                                             _bias_ratio, _regime) = _targets
                                            # Override V/div ONLY when
                                            # the coordinated target is
                                            # coarser than adapt's pick
                                            # — i.e. position would
                                            # otherwise clamp.  This
                                            # preserves adapt's stateful
                                            # convergence in the common
                                            # AC-centered + moderate-bias
                                            # cases.
                                            if _coord_vpd > _new_scale:
                                                try:
                                                    self.scope.set_channel_scale(
                                                        _ch_name, _coord_vpd)
                                                    _new_scale = _coord_vpd
                                                    _result = (
                                                        f"adapt+coord → "
                                                        f"{_coord_vpd*1e3:.2f} mV/div "
                                                        f"(coarsened for position)")
                                                except Exception:
                                                    pass
                                            # Apply the coordinated
                                            # position offset whether or
                                            # not we changed V/div.
                                            try:
                                                self.scope.set_channel_position(
                                                    _ch_name, _coord_pos)
                                                _result += (
                                                    f", pos={_coord_pos:+.1f} div, "
                                                    f"R={_bias_ratio:.2f} "
                                                    f"({_regime})")
                                            except Exception:
                                                pass
                                        else:
                                            # Fall back to the simple
                                            # divide+clamp if the helper
                                            # is unavailable.  Use the
                                            # baseline (preferred) or
                                            # the synthetic mid-point as
                                            # the position reference.
                                            try:
                                                _ref = (_baseline
                                                        if _baseline is not None
                                                        else 0.5 * (_target_lo + _target_hi))
                                                _pos_divs_raw = -_ref / _new_scale
                                                _pos_divs = max(-5.0, min(
                                                    5.0, _pos_divs_raw))
                                                self.scope.set_channel_position(
                                                    _ch_name, _pos_divs)
                                                _result += (f", pos="
                                                            f"{_pos_divs:+.1f} div")
                                            except Exception:
                                                pass
                            else:
                                # adapt returned None — either:
                                #   * already-on-grid (no change needed),
                                #   * hysteresis blocked the shrink, or
                                #   * adapt is locked.
                                _result = "no change (already optimal)"
                        except Exception as _rescale_err:
                            _result = (f"FAILED "
                                       f"({type(_rescale_err).__name__}: "
                                       f"{_rescale_err})")
                        # Log per-attempt diagnostic — same format
                        # as before so existing post-mortem tooling
                        # (grep the .txt log) keeps working.  The
                        # ``in-view`` field is dropped because
                        # ``adapt`` doesn't expose it — the new
                        # primitive's decisions are visible via the
                        # scope's own log lines (``[scope] adapt
                        # CH1: ...``) which are already emitted by
                        # adapt_channel_scale itself.
                        # Baseline / swing line: only for E_ret / E_act
                        # and only when the pre-trigger window had enough
                        # samples to compute a robust baseline.  Surfaces
                        # the "we centred the BASELINE not the (min+max)/2"
                        # decision so post-mortem can verify it.
                        _baseline_str = ""
                        if _baseline is not None and _swing is not None:
                            _baseline_str = (
                                f", baseline={_baseline*1e3:+.2f}mV"
                                f", swing±{_swing*1e3:.2f}mV (one-sided)")
                        _diag_lines.append(
                            f"    attempt {_attempt + 1}/{MAX_RECAPTURE + 1} "
                            f"{_role:>4s} ({_ch_name}): "
                            f"observed [{_lo*1e3:+8.2f}, {_hi*1e3:+8.2f}] mV"
                            f"{_baseline_str}, "
                            f"clip={_clip_str}, fit={_in_view_str}, "
                            f"divs_budget={_divs:.0f} → "
                            f"result={_result}")
                    # Stop conditions:
                    #   * Nothing rescaled this pass → converged.
                    #   * Hit the attempt cap → accept what we have.
                    #
                    # The old ``not _introspectable → break`` gate was
                    # removed.  Rationale: with the calibration-style
                    # ``adapt_channel_scale`` primitive, simulator-style
                    # scopes have a base-class no-op that returns None,
                    # which naturally leaves ``_any_rescaled = False``
                    # and trips the first stop condition.  The
                    # ``_introspectable`` flag was a belt-and-suspenders
                    # check for the OLD homegrown loop where adapt
                    # could write a new scale even without working
                    # introspection — that scenario no longer applies.
                    # Worse, the gate occasionally short-circuited
                    # legitimate re-captures on Tek scopes where one
                    # SCPI query failed transiently, leaving the loop
                    # with a stale ``acq`` from before the rescale
                    # write.  Dropping the gate fixes that without
                    # introducing simulator-side loops.
                    if not _any_rescaled:
                        # Converged — no writes this iteration, so the
                        # current ``acq`` already reflects the final
                        # scope state.  No need to recapture.
                        break
                    # Re-capture with the new scale.  The stim is STILL
                    # RUNNING from the outer ``start_all()`` — the outer
                    # try/finally below does the single ``stop_channel``
                    # after the loop converges.  An exception here leaves
                    # ``acq`` pointing at the LAST good capture, so
                    # ``make_capture`` below still produces a Capture
                    # (just at the pre-rescale scale).  Better than
                    # aborting the step entirely.
                    #
                    # **Final iteration also recaptures**.  Even when
                    # ``_attempt >= MAX_RECAPTURE`` (we're about to
                    # break out of the loop), if the analyse-and-write
                    # block above made scope writes, we MUST recapture
                    # so the saved ``acq`` reflects the final scope
                    # state — not the pre-write state we just rejected.
                    # Without this final recapture, ``make_capture``
                    # below would use data captured at a V/div the
                    # rescale loop explicitly walked away from.
                    try:
                        # Re-use the pulse-rate-aware timeout computed
                        # for the first capture in this step.  Rescales
                        # don't change pulse rate, so the budget still
                        # applies; using the default would re-introduce
                        # the 10 s timeout at low rates.
                        acq = self.scope.single_capture(
                            timeout_s=_capture_timeout_s)
                    except Exception as e:
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session,
                            message=(f"⚠ Re-capture after rescale failed at "
                                     f"{_amp_signed:+.1f} µA "
                                     f"(capture #{index + 1}, attempt "
                                     f"{_attempt + 2} of "
                                     f"{MAX_RECAPTURE + 1}): "
                                     f"{type(e).__name__}: {e}")))
                        break
                    # Hard cap on attempts — break AFTER the recapture
                    # so the final acq reflects the loop's final writes.
                    if _attempt >= MAX_RECAPTURE:
                        break
            except Exception as _loop_err:
                # Defensive: a malformed scope state shouldn't kill the
                # whole sweep.  Just log via the print path and continue
                # with whatever ``acq`` currently holds.
                try:
                    _diag_lines.append(
                        f"  in-view loop ERROR: "
                        f"{type(_loop_err).__name__}: {_loop_err}")
                except Exception:
                    pass
            # ---- Final V/div summary per role -----------------------
            # After the rescale loop converges, read back the actual
            # V/div the scope landed on for each voltage role and add
            # it to the diagnostic.  This is the line the operator
            # should look at to verify the scaling is correct — if
            # V_mon ended at 500 mV/div with a ±5 mV signal, this
            # surfaces the problem immediately.  Without this, the
            # only way to verify "did the scaling actually adapt?"
            # was to count per-attempt log lines and guess at the
            # convergence state.
            try:
                _final_lines = []
                _final_chan_data = getattr(acq, "channels", {}) or {}
                _any_out_of_view = False
                for _role in _voltage_roles:
                    _ch_name = aliases_obs.get(_role)
                    if not _ch_name:
                        continue
                    # Per-role scope state.
                    try:
                        _final_vpd = float(self.scope._q(
                            f"{_ch_name}:SCAle?"))
                        _final_pos = float(self.scope._q(
                            f"{_ch_name}:POSition?"))
                    except Exception as _q_err:
                        _final_lines.append(
                            f"    {_role:>4s} ({_ch_name}): "
                            f"V/div read failed ({_q_err})")
                        continue
                    # ---- FINAL in-view verification ------------
                    # Re-check the SAVED acq one last time against
                    # MAX_FACTOR.  This is the contract guarantee
                    # the operator asked for: "every acquired
                    # waveform is verified to fit within 3.9 (or
                    # 4.9) divs".  If any role's observed range
                    # exceeds the budget on the FINAL capture,
                    # emit a ⚠ so the post-mortem shows we
                    # couldn't converge within MAX_RECAPTURE
                    # attempts.  The data is still saved (better
                    # than dropping the capture) but the warning
                    # surfaces the problem.
                    _final_arr = _final_chan_data.get(_ch_name)
                    _verify_str = ""
                    if _final_arr is not None and len(_final_arr) >= 2:
                        _a = _np.asarray(_final_arr, dtype=float)
                        _vlo = float(_a.min())
                        _vhi = float(_a.max())
                        if (_np.isfinite(_vlo)
                                and _np.isfinite(_vhi)
                                and _final_vpd > 0):
                            _vp_v = -_final_pos * _final_vpd
                            _win_lo = -MAX_FACTOR * _final_vpd + _vp_v
                            _win_hi = +MAX_FACTOR * _final_vpd + _vp_v
                            _fits = (_vlo > _win_lo
                                     and _vhi < _win_hi)
                            if _fits:
                                _verify_str = (
                                    f"  ✓ fits in ±{MAX_FACTOR:.1f}div")
                            else:
                                _any_out_of_view = True
                                _verify_str = (
                                    f"  ⚠ STILL OUT-OF-VIEW: "
                                    f"observed [{_vlo*1e3:+.1f}, "
                                    f"{_vhi*1e3:+.1f}] mV vs "
                                    f"window [{_win_lo*1e3:+.1f}, "
                                    f"{_win_hi*1e3:+.1f}] mV")
                    # Visible-range total uses the scope's actual
                    # vertical-div count (8 on TBS1000/TDS/TPS, 10 on
                    # TBS2000-series).  Hardcoding 8 here would
                    # under-report on the 2-series.
                    _total_divs = 2.0 * float(getattr(
                        self.scope, "_half_vert_divs", 4.0))
                    _final_lines.append(
                        f"    {_role:>4s} ({_ch_name}): "
                        f"V/div = {_final_vpd*1e3:.2f} mV, "
                        f"pos = {_final_pos:+.1f} div  "
                        f"(visible range "
                        f"{_final_vpd*_total_divs*1e3:.0f} mV total)"
                        f"{_verify_str}")
                if _final_lines:
                    _diag_lines.append("  final scope state:")
                    _diag_lines.extend(_final_lines)
                # Loud warning at the top of the diagnostic block
                # so a grep for "STILL OUT-OF-VIEW" in the session
                # log surfaces every unconverged capture without
                # the operator having to read the per-role lines.
                if _any_out_of_view:
                    _diag_lines.insert(
                        0, f"  ⚠ At least one role's FINAL capture "
                        f"is still out-of-view (±{MAX_FACTOR:.1f}div) "
                        f"after {MAX_RECAPTURE + 1} attempts. "
                        f"Data is saved but V/div didn't converge.")
            except Exception:
                pass
            # Emit the entire in-view diagnostic as ONE log event so it
            # stays grouped with the capture in the session .txt log.
            # Lets the operator (or a future agent investigating "the
            # vertical scaling wasn't adjusted") see exactly which
            # attempts ran, what each role's observed range was,
            # whether the scope said in-view / out-of-view / can't-
            # check, and what scale + position got written.
            try:
                if _diag_lines:
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session,
                        message=("In-view rescale loop at "
                                 f"{_amp_signed:+.1f} µA "
                                 f"(capture #{index + 1}):\n"
                                 + "\n".join(_diag_lines))))
            except Exception:
                pass
        finally:
            # SINGLE stop_all for the whole capture lifecycle.
            # Runs after the initial capture AND every iterative re-
            # capture in 2b — never between them.  Triggered by both
            # the normal end-of-block fall-through AND any early
            # ``return cap`` from the inner first-capture exception
            # branch (Python guarantees the finally fires on every
            # path leaving the try, including return-from-inner-block).
            # ``stop_all`` (= PS_StopStimAllChannels) is the MATLAB-
            # equivalent of ``stopStimulation``; quiets both the active
            # channel AND the unused channels we started via the
            # zero-amplitude pattern, so the next ``_one_capture`` lands
            # on a fully-stopped device when it does its own pre-load
            # ``stop_all`` (belt-and-suspenders).
            try:
                self.stim.stop_all()
            except Exception:
                pass

        # ----- 3+4. Demux channels and convert to physical units ---------
        # make_capture handles channel_aliases lookup, nominal scaling, and
        # applies readback calibration (gain/offset on I_mon, vmon_v_per_v
        # correction) when a calibration record exists for this stimulator.
        cap = make_capture(index, pattern, acq, self.scope, self.stim,
                           cal=self.cal, channel=config.active)
        # Voltage compliance check. PlexStim 2.0 V_mon saturates at
        # roughly ±STIM_VOLTAGE_COMPLIANCE_V; crossing it means the
        # device couldn't push the programmed current any further.
        # The constant lives in ``stimtest.config`` so a hardware-rev
        # change updates one place rather than every experiment runner.
        #
        # Audit finding #20 — the previous form ``np.max(np.abs(v))
        # > rail`` triggered on a single noisy sample, aborting
        # otherwise-good ramp steps when the scope picked up a spike
        # (e.g. mains coupling on a long unshielded probe lead). We
        # now require at least 3 consecutive samples above the rail
        # so glitches don't kill a sweep. 3 samples at the scope's
        # 2 GS/s rate is 1.5 ns — well below any meaningful
        # compliance event but well above any single-sample
        # transient. ``rolling_above`` is a tiny inline routine
        # (no scipy dep) that returns True iff the input has a run
        # of ``min_consecutive`` consecutive entries strictly above
        # the threshold.
        cap.status.voltage_compliance = bool(_v_compliance_tripped(
            cap.v_mon_v, threshold_v=STIM_VOLTAGE_COMPLIANCE_V,
            min_consecutive=3,
        ))
        compute_metrics(cap, surface_area_um2=self.surface_area_um2,
                        polarization_source=self.polarization_source)
        # Push the E_ret pre/post-pulse rest values into the
        # electrode-potential learning bin for the return coating.
        # No-ops when E_ret wasn't recorded (NaN values), when the
        # session lacks a setup snapshot, or when a non-Ag|AgCl
        # reference is wired (see record_capture for the full
        # skip rules). Wrapped in a try so a flaky disk on the
        # prefs dir never aborts a capture mid-sweep.
        try:
            from ..electrode_potential_history import record_capture
            record_capture(cap, self.session)
        except Exception:
            pass
        # Per-capture damage-warning synthesis. Reads the user's
        # Environment from the setup snapshot and emits an
        # ``ExperimentEvent(kind="log", ...)`` when Shannon /
        # NeurostimML / a Modified-Shannon cap fires above the
        # environment's threshold. ``info`` postures (PBS, mISF,
        # etc.) suppress per-capture spam to avoid 50-line log
        # floods on a long sweep; ``warn`` / ``alert`` emit one
        # line per flagged capture, prefixed with the title so
        # the user can grep / filter the log later.
        try:
            from ..damage_warnings import assess_finished_capture
            # ``ExperimentEvent`` is already imported at module level
            # (top of file).  Re-importing here would silently rebind
            # the name as a function-local — and Python's name-
            # resolution rules then treat EVERY ``ExperimentEvent``
            # reference earlier in the same function as the unbound
            # local, raising ``UnboundLocalError`` ("cannot access
            # local variable 'ExperimentEvent' where it is not
            # associated with a value") at the very first capture.
            extras = self.session.test.extras or {}
            snap = extras.get("setup_snapshot") or {}
            env_short = (
                snap.get("environment_short")
                if isinstance(snap, dict) else None
            ) or "pbs"
            warning = assess_finished_capture(cap, environment_short=env_short)
            if warning is not None:
                self._emit(ExperimentEvent(
                    kind="log",
                    session=self.session,
                    capture=cap,
                    message=f"{warning.title}\n{warning.body}",
                ))
        except Exception:
            pass
        return cap

    # ------------------------------------------------------------------
    def _potential_limit_hit(self, cap: Capture) -> bool:
        """Has E_pol of either active or return crossed the water window?

        The check uses the runner's ``cathodic_limit_v`` /
        ``anodic_limit_v`` (user-editable on the Setup tab; defaults
        come from the coating catalog) extended outward by
        ``polarization_tolerance_v``. Tolerance is a grace band — the
        runner only declares "limit hit" once a phase crosses
        ``limit ± tolerance``. Mirrors MATLAB's
        ``isActiveTooLow = active_lower < (lowerPotential - TOL)``.
        """
        tol = self.polarization_tolerance_v
        cath = self.cathodic_limit_v - tol
        anod = self.anodic_limit_v + tol
        for series in (cap.metrics.polarization_per_phase_v,
                       cap.metrics.return_polarization_per_phase_v):
            for v in series:
                if not np.isfinite(v):
                    continue
                if v <= cath or v >= anod:
                    return True
        return False

    def _polarization_ratio(self, cap: Capture) -> float:
        """Worst-case ``|E_pol| / |limit|`` across all phases of one capture.

        ≥ 1.0 means a phase has crossed the water window. Used both as
        the trigger for fine stepping (``increment`` strategy) and as
        the dependent variable in the adaptive regression.
        """
        worst = 0.0
        for series in (cap.metrics.polarization_per_phase_v,
                       cap.metrics.return_polarization_per_phase_v):
            for v in series:
                if not np.isfinite(v):
                    continue
                if v < 0:
                    worst = max(worst, abs(v) / abs(self.cathodic_limit_v))
                else:
                    worst = max(worst, v / self.anodic_limit_v)
        return worst

    # ------------------------------------------------------------------
    # Step-size dispatch
    # ------------------------------------------------------------------
    def _next_step(self, cap: Capture, current_amp_ua: float,
                   captures: List[Capture]) -> float:
        """Pick the next amplitude delta based on the configured strategy.

        ``increment`` keeps the original coarse-then-fine behaviour;
        ``adaptive`` and ``predictive`` use a regression of the
        accumulated polarization-ratio data to project the amplitude
        at which the water-window limit will be reached, and step
        toward that prediction.
        """
        strat = (self.ramp.strategy or "increment").lower()
        if strat == "increment":
            return self._next_step_increment(cap)

        # Predictive first — try the ML model. Fall back to adaptive on
        # any of: no model loaded, model raised, prediction not finite,
        # or the prediction is well below where we already are (no
        # signal in the model for this regime).
        target_ua: Optional[float] = None
        if strat == "predictive":
            target_ua = self._predict_target_ml()
            if target_ua is None or not np.isfinite(target_ua) or target_ua <= current_amp_ua:
                self._emit(ExperimentEvent(
                    kind="log", session=self.session,
                    message="Predictive: ML estimate unavailable / not "
                            "above current amp — falling back to "
                            "adaptive regression."))
                target_ua = None

        if target_ua is None:
            target_ua = self._predict_target_regression(captures)

        if target_ua is None:
            # Not enough captures yet to fit anything — keep probing
            # with the coarse step so we accumulate more data points.
            return self.ramp.coarse_step_ua

        # Track oscillation in successive predictions. The MATLAB code
        # used the same idea: when the regression is converging
        # monotonically the prediction trail is stable, but if it
        # bounces around the ceiling that's the cue to back off.
        self._record_prediction(target_ua)
        if self._oscillation_count >= self.ramp.oscillation_threshold:
            target_ua *= self.ramp.safety_factor
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=f"Adaptive: prediction oscillated {self._oscillation_count}× — "
                        f"applying safety factor "
                        f"{self.ramp.safety_factor:.2f} to target."))

        # Translate the predicted target into a delta. Clamp:
        #   * never below ``fine_step_ua`` (always make progress)
        #   * never above half the remaining headroom to ``max_ua``
        #     (avoid one giant jump that overshoots)
        delta = target_ua - current_amp_ua
        if delta <= 0:
            return self.ramp.fine_step_ua
        max_jump = max((self.ramp.max_ua - current_amp_ua) * 0.5,
                       self.ramp.coarse_step_ua * 4)
        return float(min(max(delta, self.ramp.fine_step_ua), max_jump))

    def _next_step_increment(self, cap: Capture) -> float:
        """Original coarse/fine step logic (kept verbatim)."""
        worst = self._polarization_ratio(cap)
        if worst >= self.ramp.fine_threshold_ratio:
            return self.ramp.fine_step_ua
        return self.ramp.coarse_step_ua

    # ----- regression engine -------------------------------------------
    # R² thresholds match the MATLAB ``changeCurrent_Fit.m`` heuristic:
    # a linear fit only needs to be "OK" (0.80) because the data is
    # close to linear in the small-window-window regime, while higher-
    # order fits demand stronger evidence (0.85) before we trust them.
    _R2_LINEAR_MIN = 0.80
    _R2_HIGH_ORDER_MIN = 0.85

    def _predict_target_regression(self,
                                   captures: List[Capture]) -> Optional[float]:
        """Fit polarization ratio vs. amplitude and predict the ceiling.

        Mirrors the MATLAB ``changeCurrent_Fit.m`` regression block:

        1. Build the (amplitude, max-|E_pol|/|limit|) sample list.
        2. Below ``min_points_for_regression`` → return ``None``
           (caller falls back to coarse stepping while we collect more).
        3. Try ``poly1``, ``poly2``, ``poly3`` fits in that order. Each
           fit must clear an R² threshold (0.80 for linear, 0.85 for
           higher-order). For every fit that passes, solve for the
           amplitude where the predicted ratio = 1.0 — i.e. the
           water-window crossover. ``poly2`` / ``poly3`` use closed-form
           polynomial roots, filtered to real positive values within
           the policy ceiling.
        4. Return the **minimum** valid prediction across fits. That's
           the most conservative ceiling: if any plausible fit thinks
           the limit is at 90 µA, prefer that over a slope-only fit
           saying 110 µA.
        5. **Early-fit dampening** — when we're just one sample past
           ``min_points_for_regression``, multiply the prediction by
           0.9. The early fit is volatile; the same trick lives in the
           MATLAB code for the same reason.
        """
        amps: List[float] = []
        ratios: List[float] = []
        for cap in captures:
            if cap.status.aborted: continue
            try:
                a = abs(cap.pattern.excitation_phase.amplitude_ua)
            except Exception:
                continue
            r = self._polarization_ratio(cap)
            if not np.isfinite(r) or a <= 0:
                continue
            amps.append(a); ratios.append(r)
        if len(amps) < self.ramp.min_points_for_regression:
            return None
        x = np.asarray(amps, dtype=float)
        y = np.asarray(ratios, dtype=float)

        candidates: List[float] = []
        for fit_type in ("poly1", "poly2", "poly3"):
            target = self._fit_and_solve(fit_type, x, y)
            if target is not None and np.isfinite(target):
                candidates.append(float(target))
        if not candidates:
            return None
        # Most-conservative pick — same as the MATLAB
        # ``min(currentStim_guess)`` line.
        target_ua = min(candidates)
        # Early-fit dampening (matches the MATLAB
        # ``currentStim_guess * 0.9`` step on the first usable fit).
        if len(amps) <= self.ramp.min_points_for_regression + 1:
            target_ua *= 0.9
        # Clamp to the data's current max (don't predict below where
        # we already are) and to the policy's hard ceiling.
        return float(min(max(target_ua, x.max()), self.ramp.max_ua))

    def _fit_and_solve(self, fit_type: str,
                       x: np.ndarray, y: np.ndarray) -> Optional[float]:
        """Fit ``y = f(x)`` of ``fit_type`` and solve for ``x | y = 1``.

        Returns ``None`` if the fit's R² fails the threshold or the
        crossover root isn't a usable positive real number. Caller
        treats ``None`` as "skip this fit type, try the next."
        """
        try:
            deg = {"poly1": 1, "poly2": 2, "poly3": 3}[fit_type]
        except KeyError:
            return None
        if len(x) <= deg:
            return None
        try:
            coeffs = np.polyfit(x, y, deg)
        except (np.linalg.LinAlgError, ValueError):
            return None
        if not np.all(np.isfinite(coeffs)):
            return None
        # Coefficient of determination — same formula as ``getLinReg.m``.
        y_fit = np.polyval(coeffs, x)
        ss_res = float(np.sum((y - y_fit) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        threshold = (self._R2_LINEAR_MIN if fit_type == "poly1"
                     else self._R2_HIGH_ORDER_MIN)
        if r2 < threshold:
            return None
        # Solve f(x) = 1 ⇔ shift the constant term down by 1 and root.
        shifted = coeffs.copy()
        shifted[-1] -= 1.0
        try:
            roots = np.roots(shifted)
        except (np.linalg.LinAlgError, ValueError):
            return None
        # Filter: real, positive, within the hardware-conservative cap.
        # The 1000 µA cap echoes the MATLAB ``max_tf = x_raw <= 1e3``.
        real_pos: List[float] = []
        for root in roots:
            if abs(root.imag) > 1e-9: continue
            v = float(root.real)
            if v <= 0 or v > 1000.0: continue
            real_pos.append(v)
        if not real_pos:
            return None
        return min(real_pos)

    def _predict_target_ml(self) -> Optional[float]:
        """Ask the ML model for a one-shot ceiling estimate.

        Returns ``None`` when no predictor is loaded or the model
        can't produce a usable estimate (no features for this
        electrode, exception during predict, etc.). The caller treats
        ``None`` as a signal to fall back to the regression path.

        The feature row is built directly from the live
        ``(pattern, configuration, surface_area, coating)`` tuple
        the runner already has on hand. An earlier revision called
        ``features_from_run(self.session, cfg)`` passing a
        ``Configuration`` where the helper expected a ``ChannelRun``;
        the resulting ``AttributeError`` was swallowed by the
        surrounding try/except, so the predictive strategy
        silently degraded to the regression path on every call.
        """
        if self.predictor is None:
            return None
        try:
            from ..ml.qinj_model import QinjFeatures
            cfg = self.session.test.configuration
            # Honour the live coating from the array (falls back
            # to SIROF only when the catalog lookup itself fails).
            # The predictor flags unknown coatings via the
            # ``is_extrapolation`` field rather than raising, so
            # an out-of-training-set coating still produces a
            # numeric prediction — just one the caller should
            # weight less.
            try:
                coating = (self.session.test.array.sites[0].coating
                           or "SIROF")
            except (AttributeError, IndexError):
                coating = "SIROF"
            feats = QinjFeatures.from_pattern(
                self.session.test.pattern,
                cfg,
                coating=coating,
                surface_area_um2=self.surface_area_um2,
            )
            result = self.predictor.predict(feats)
        except Exception:
            return None
        # The predictor returns a Q_inj density (mC/cm²); convert to
        # an excitation-phase amplitude using the active electrode's
        # area and the pattern's phase width. Mirrors the logic the
        # GUI's Fixed-Q_inj path uses.
        #
        # Note the dataclass attribute name: ``q_inj_predicted_…``
        # (the metric prefix), NOT ``predicted_q_inj_…``. An earlier
        # revision used the latter, which AttributeError'd inside
        # the surrounding try/except — silently turning every
        # predictive-strategy call into a regression fallback.
        try:
            q_target = float(result.q_inj_predicted_mc_per_cm2)
            area_cm2 = self.surface_area_um2 / 1e8
            phase_us = abs(self.session.test.pattern
                           .excitation_phase.width_us)
            if phase_us <= 0 or area_cm2 <= 0 or not np.isfinite(q_target):
                return None
            amp_ua = (q_target * 1e-3 * area_cm2 / (phase_us * 1e-6)) * 1e6
            return float(amp_ua) if np.isfinite(amp_ua) and amp_ua > 0 else None
        except Exception:
            return None

    def _record_prediction(self, target_ua: float) -> None:
        """Append the latest prediction; bump oscillation count on a flip."""
        hist = self._prediction_history
        if len(hist) >= 2:
            prev_dir = np.sign(hist[-1] - hist[-2])
            new_dir = np.sign(target_ua - hist[-1])
            if prev_dir != 0 and new_dir != 0 and prev_dir != new_dir:
                self._oscillation_count += 1
        hist.append(float(target_ua))
