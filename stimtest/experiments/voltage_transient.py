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
from ..session import Capture, ChannelRun, Session
from ..waveforms import PulsePattern
from .base import ExperimentEvent, ExperimentResult, ExperimentRunner


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
    # PS_InitAllStim (wrapped by stim.reinit()). So before starting
    # any new configuration, we must check whether any of its
    # designated returns is still in stim.loaded_channels() and
    # reinit if so. The precise overlap check is the correctness
    # criterion; the older _NO_REINIT_KINDS hardcoded "MP and CG
    # don't need reinit" shortcut got the MP→CG and CG→CG cases
    # wrong (both leave the previous active channel loaded, and CG's
    # return set spans all-other-on-array — which includes the
    # previous active).

    def run(self) -> ExperimentResult:
        self.preflight()
        all_captures: List[Capture] = []
        try:
            self.scope.set_record_length(2500)
            # Acquisition mode + count are configured by the GUI's
            # Setup tab via _start_runner before this thread fires —
            # we don't override here so the user's choice (Sample vs
            # Average, and the picked n_avg) sticks for the whole run.
            for config in self.configurations:
                if self.aborted:
                    break
                # Routing-correctness check (see class-level comment):
                # if any of this config's returns is still loaded
                # from a previous config, the firmware can't honour
                # them as returns — they'd carry stale stim instead
                # of being passive ground paths. Force a reinit to
                # clear all loaded patterns. MP configs have an empty
                # returns tuple (return is off-array global) so the
                # intersection is empty and no reinit is triggered.
                already_loaded = self.stim.loaded_channels()
                clashing = set(config.returns) & already_loaded
                if clashing:
                    try:
                        self.stim.reinit()
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session,
                            message=(
                                f"Stimulator reinit before {config.id} "
                                f"(prev-loaded ch{sorted(clashing)} would "
                                f"clash with this config's return set)."
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
        amp = self.ramp.starting_ua
        capture_idx = 0
        last_good_amp = amp
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

            last_good_amp = amp
            amp += self._next_step(cap, amp, run.captures)

        # Stop output for safety
        try:
            self.stim.stop_channel(config.active)
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
        try:
            self.stim.set_monitor_channel(config.active)
            self.stim.load_channel(config.active, pattern)
            self.stim.set_repetitions(config.active, 0)  # infinite for sweep, then stop
            self.stim.start_channel(config.active)
        except Exception as e:
            cap = Capture(index=index, pattern=pattern)
            cap.status.aborted = True
            cap.status.notes = f"Stimulator program error: {e}"
            return cap

        # ----- 2. Let it settle, then grab one averaged capture ---------
        # The scope is in AVERAGE mode (set in run()); we wait long enough
        # for ``settle_pulses`` triggers so the average has converged before
        # we pull the curve. Skip entirely when both backends are simulated
        # — the sim scope returns a deterministic averaged frame
        # synchronously, so the wait is pure overhead. Real hardware needs
        # the wait so the trigger has time to fire and the average converges.
        if not (self.stim.info.is_simulated and self.scope.info.is_simulated):
            time.sleep(max(0.05, self.ramp.settle_pulses / pattern.rate_hz))
        try:
            acq = self.scope.single_capture()
        except Exception as e:
            cap = Capture(index=index, pattern=pattern)
            cap.status.aborted = True
            cap.status.notes = f"Scope capture error: {e}"
            return cap
        finally:
            try:
                self.stim.stop_channel(config.active)
            except Exception:
                pass

        # ----- 3. Demux scope channels into logical signals --------------
        # The user picked, on the Setup tab, which physical scope channel
        # carries which signal (V_mon, I_mon, E_ret, E_act). The driver
        # remembered that mapping in ``channel_aliases``; here we read the
        # right key out of the captured frame.
        aliases = self.scope.channel_aliases
        v_mon = acq.channels.get(aliases.get("vmon", ""), np.zeros(0))
        i_mon = acq.channels.get(aliases.get("imon", ""), np.zeros(0))
        e_ret = acq.channels.get(aliases.get("eret", ""), None)
        e_act = acq.channels.get(aliases.get("eact", ""), None)

        # ----- 4. Convert raw scope volts to physical units --------------
        # V_mon line carries (vmon_scaling) volts per volt at the electrode,
        # and I_mon line carries (imon_scaling) volts per microamp. Divide
        # to recover real V and µA. NIL devices use different scaling than
        # standard PlexStim 2.0; the driver knows which it is.
        info = self.stim.info
        v_mon_v = v_mon / info.vmon_scaling_v_per_v if info.vmon_scaling_v_per_v else v_mon
        i_mon_ua = i_mon / info.imon_scaling_v_per_ua if info.imon_scaling_v_per_ua else i_mon

        cap = Capture(
            index=index, pattern=pattern,
            time_us=np.asarray(acq.time_us),
            v_mon_v=np.asarray(v_mon_v),
            i_mon_ua=np.asarray(i_mon_ua),
            e_act_v=np.asarray(e_act) if e_act is not None and e_act.size else None,
            e_ret_v=np.asarray(e_ret) if e_ret is not None and e_ret.size else None,
        )
        # Voltage compliance check. PlexStim 2.0 V_mon saturates at
        # roughly ±STIM_VOLTAGE_COMPLIANCE_V; crossing it means the
        # device couldn't push the programmed current any further.
        # The constant lives in ``stimtest.config`` so a hardware-rev
        # change updates one place rather than every experiment runner.
        cap.status.voltage_compliance = bool(
            np.max(np.abs(v_mon_v)) > STIM_VOLTAGE_COMPLIANCE_V
        )
        compute_metrics(cap, surface_area_um2=self.surface_area_um2,
                        polarization_source=self.polarization_source)
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
        """
        if self.predictor is None:
            return None
        try:
            from ..ml import features_from_run
            cfg = self.session.test.configuration
            feats = features_from_run(self.session, cfg)
            result = self.predictor.predict(feats)
        except Exception:
            return None
        # The predictor returns a Q_inj density (mC/cm²); convert to
        # an excitation-phase amplitude using the active electrode's
        # area and the pattern's phase width. Mirrors the logic the
        # GUI's Fixed-Q_inj path uses.
        try:
            q_target = float(result.predicted_q_inj_mc_per_cm2)
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
