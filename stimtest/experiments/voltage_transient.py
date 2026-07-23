"""Voltage Transient (VT) characterization experiment.

This is the canonical "find the maximum charge-injection capacity" sweep that
produces all the metrics quoted in the IEEE NER 2025 paper.

Algorithm (mirrors ``runVoltageTransient.m`` in the original MATLAB suite,
but distilled into the much smaller form below):

    for each (active, return-electrodes) configuration:
        amp = |pattern excitation amplitude|   # the ramp starts at the pattern
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

from ..config import (COATINGS, STIM_VOLTAGE_COMPLIANCE_V,
                      STIM_MAX_AMPLITUDE_UA)
from ..electrode import Configuration, ElectrodeArray
from ..hardware.base import Oscilloscope, Stimulator
from ..hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from ..metrics import compute_metrics, is_continuous_sinusoidal
from ..readback_calibration import make_capture
from ..session import Capture, ChannelRun, Session
from ..waveforms import Phase, PulsePattern
from .base import ExperimentEvent, ExperimentResult, ExperimentRunner


# The rescale-loop tuning constants (_RESCALE_TRIM_PCT,
# _RESCALE_STALE_FACTOR) moved to experiments/base.py alongside the
# shared ExperimentRunner.rescale_to_fit loop; re-exported here for
# back-compat with existing imports / docs references.
from .base import _RESCALE_TRIM_PCT, _RESCALE_STALE_FACTOR  # noqa: F401


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
# Multi-parameter sweep
# ---------------------------------------------------------------------------
@dataclass
class SweepPoint:
    """One point in a multi-parameter VT sweep.

    A VT session normally ramps amplitude for a single pulse pattern on
    each electrode configuration. A sweep adds an outer axis: for every
    configuration, the runner walks a list of ``SweepPoint`` s, each of
    which transforms the session's *base* pattern before the amplitude
    ramp begins. Every point produces its own :class:`ChannelRun` so the
    results stay separable in the saved session and the Results tab.

    * ``rate_hz`` — override the pulse repetition rate (pps). ``None``
      keeps the base pattern's rate.
    * ``width_ratio`` — target ``W2:W1`` phase-width ratio for a
      *biphasic* pattern. The recharge phase's width becomes
      ``W1 * width_ratio`` and its amplitude is rebalanced so the pulse
      stays charge-balanced (``A1·W1 = -A2·W2``). ``None`` (or ``1.0``)
      leaves the pattern symmetric. Ignored for triphasic patterns.
    * ``label`` — short human tag (e.g. ``"200pps_asym2x"``) recorded on
      the resulting run.
    """
    rate_hz: Optional[float] = None
    width_ratio: Optional[float] = None
    label: str = ""


def pattern_for_sweep_point(base: PulsePattern,
                            point: SweepPoint) -> PulsePattern:
    """Return a copy of ``base`` transformed by a :class:`SweepPoint`.

    Overrides the rate and — for biphasic patterns with a non-trivial
    ``width_ratio`` — rewrites the recharge phase width and amplitude to
    keep the pulse charge-balanced. All other phase attributes (delays,
    shapes, bump counts) are preserved. Because the amplitude ramp later
    scales *every* phase by the same factor, a pattern that starts
    balanced here stays balanced at every step of the ramp.
    """
    phases = [Phase(p.amplitude_ua, p.width_us, p.delay_after_us,
                    p.shape, p.bump_count) for p in base.phases]
    rate = point.rate_hz if point.rate_hz is not None else base.rate_hz

    ratio = point.width_ratio
    if ratio is not None and ratio > 0 and len(phases) == 2:
        w1 = phases[0].width_us
        a1 = phases[0].amplitude_ua
        new_w2 = w1 * ratio
        # Charge balance: A1·W1 + A2·W2 = 0  ⇒  A2 = -A1·W1 / W2.
        new_a2 = (-a1 * w1 / new_w2) if new_w2 != 0 else phases[1].amplitude_ua
        phases[1] = Phase(new_a2, new_w2, phases[1].delay_after_us,
                          phases[1].shape, phases[1].bump_count)

    return PulsePattern(phases=phases, rate_hz=rate,
                        repetitions=base.repetitions)


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
    # NOTE: there is deliberately NO ``starting_ua`` — the VT ramp starts at
    # the configured stimulation pattern's own excitation amplitude (operator:
    # "the stimulation pattern should be starting Istim").  A caller that wants
    # to start lower (e.g. Long Pulsing's drift characterization at 20 %)
    # passes a pattern already scaled to that fraction as the runner's base.
    coarse_step_ua: float = 5.0       # added per coarse step
    fine_step_ua: float = 1.0         # added per fine step (near limit) — the
                                      # forward-CREEP floor.  Kept at 1 µA (NOT
                                      # the 0.1 µA testing grid) so a ramp that
                                      # fine-creeps toward the limit doesn't take
                                      # 10× the captures; the fine RESOLUTION of
                                      # the crossover comes from the back-off,
                                      # which narrows to ``test_current_resolution_ua``.
    # Current TESTING resolution (operator: "I want current TESTING (not current
    # pattern) to have 0.1 µA resolution") — the grid EVERY tested amplitude is
    # snapped to (``_snap_test_ua``), and the resolution the bidirectional
    # back-off narrows the water-window crossover to.  Distinct from the
    # pattern's shape-rendering grid (gotcha #64: 0.1 µA rectangular, 30 nA
    # shaped).  So a very-low-Q electrode whose crossover sits at a few µA
    # (bumps CH16) is resolved to 0.1 µA instead of the ramp giving up at a
    # coarse bracket floor.
    test_current_resolution_ua: float = 0.1
    fine_threshold_ratio: float = 0.7 # switch to fine when |E_pol| / |limit| > this
    # Hard ceiling — the PlexStim 2.0 HARDWARE RAIL (1000 µA, gotcha #56).  A
    # pattern programmed above this raises ValueError in
    # ``PulsePattern.validate``, so the ramp must never target past it.  The
    # default MUST equal the rail so every path that falls back to ``RampPolicy()``
    # — a direct construction, Long Pulsing's characterization sub-VT (which
    # omits max_ua) — is bounded; VoltageTransientExperiment ALSO clamps this to
    # ``STIM_MAX_AMPLITUDE_UA`` defensively.  Don't raise it above the rail.
    max_ua: float = STIM_MAX_AMPLITUDE_UA   # 1000 µA hard ceiling (hardware rail)
    settle_pulses: int = 3            # let stimulation settle before each capture
    # ----- adaptive / predictive knobs -----
    strategy: str = "increment"       # 'increment' | 'adaptive' | 'predictive'
    safety_factor: float = 0.85       # multiplied into predictions once oscillation kicks in
    oscillation_threshold: int = 3    # # of prediction direction-reversals before safety kicks in
    min_points_for_regression: int = 2  # below this, adaptive uses the proportional SEED jump
    seed_fraction: float = 0.7        # pre-regression seed: jump to this fraction of the
                                      # linearly-projected limit amplitude (safe undershoot)
    # ZERO-START DAMPENING SCHEDULE (operator: "Change the safe steps as
    # −75 %, −50 %, −40 %, −25 %, −10 %").  The first FIVE real predicted jumps
    # of a zero-start ramp are scaled DOWN by these factors in order — jump 1 →
    # ×0.25 (−75 %), jump 2 → ×0.50 (−50 %), jump 3 → ×0.60 (−40 %), jump 4 →
    # ×0.75 (−25 %), jump 5 → ×0.90 (−10 %); later jumps use the full prediction
    # (×1.0).  Start the most cautious (the projection out of the low-signal
    # 0→1 µA data is the least trustworthy of the ramp) and grow confidence as
    # real polarization data accumulates.  See ``_seed_dampen_factor``.
    seed_dampen_fractions: tuple = (0.25, 0.50, 0.60, 0.75, 0.90)
    # Local-secant fast-approach: once ≥2 climbing points exist, project the
    # amplitude that reaches ``aim_ratio`` × limit from the LAST two captures'
    # slope.  For the saturating (concave-down) E_pol vs I typical of SIROF
    # this UNDERSHOOTS the true crossover (safe) while taking a real step,
    # so the approach converges in 2-3 captures instead of the 6-10-capture
    # fine-creep the conservative global poly fit produced (operator: "taking
    # WAY too long to reach the potential limits").
    aim_ratio: float = 1.0            # secant projection target (× the water-window limit)
    # ----- bidirectional back-off (operator: "quitting too soon if the
    # electrode polarization is too large when there is enough current to
    # reduce", + "stopped at -0.644 V … did not try again to reach near
    # -0.6") -----
    # When a step OVERSHOOTS the acceptance band (E_pol past the far edge),
    # a one-way ramp would stop there with an overshoot.  With back-off ON
    # the runner instead brackets [last-safe amp, overshooting amp] and
    # RE-CAPTURES at interpolated amplitudes to land E_pol INSIDE the band
    # — the max-Q_inj crossover, precisely, without leaving an overshoot as
    # the reported result.  Port of MATLAB ``changeCurrent.m``'s
    # bidirectional inc/dec targeting.
    backoff_on_overshoot: bool = True
    # Max extra RE-CAPTURES spent narrowing onto the band per overshoot.
    # Bounded so a noisy / non-monotonic electrode can't loop forever; on
    # exhaustion the runner keeps the last SAFE (below-band) capture so it
    # never ends on an overshoot.  6 (was 4) so a wide overshoot bracket —
    # e.g. a low-Q electrode seeded past its crossover — can be regula-falsi'd
    # down to the 0.1 µA testing resolution instead of being accepted at the
    # coarse low side (bumps CH16 gave up at 1 µA when its crossover was a few
    # µA higher).
    backoff_max_captures: int = 6
    # SEED SAFETY CAP (operator canceled bumps CH16 after its 1 µA seed flung
    # to 51 µA → E_pol −4.8 V, 8× past the −0.6 V water window).  While the
    # polarization signal is still BURIED (baseline-corrected |E_pol| <
    # ``_SIGNAL_FLOOR_V``), the prediction is built from near-noise data and
    # can grossly overshoot a low-Q electrode; cap the jump to this multiple
    # of the current amplitude so a buried-signal step grows at most ~×10
    # (a bounded, undershoot-biased search).  Once signal emerges the operator's
    # full ``predicted × seed_fraction`` jump applies unclamped.
    seed_max_growth: float = 10.0

    # E_POL-PROXIMITY GROWTH CAP (pcc VT-max cathodal: CH01 flung 150→1000 µA,
    # E_pol past the water window; CH08 flung 1→51 µA; CH09 a 50 µA step →
    # +1126% overshoot — the continuous-sinusoid current was "too high and not
    # properly incremented safely", RUINING electrodes).  Every earlier guard
    # assumes the recent slope extrapolates safely — TRUE for a concave-DOWN
    # saturating SIROF, FALSE for a concave-UP low-Q / high-Z electrode: the
    # predictor over-projects, snap-to-ceiling mis-reads it as "hardware
    # limited" and flings to 1000 µA, and back-off only fires AFTER the
    # damaging overshoot.  This cap bounds the per-step GROWTH keyed on the
    # WORST MEASURED |E_pol|/|limit| ratio — big steps far from the window,
    # tiny steps close — so no single step can multiply E_pol into a
    # catastrophe.  Keyed on MEASURED E_pol (robust + valid from the FIRST
    # capture), NOT the predicted crossover (the thing that over-projects) and
    # NOT ``_signal_emerged`` (which needs 2 points and turns OFF for a
    # high-Z electrode).  ALWAYS-ON (buried ratio ≈ 0 → the loosest tier), so
    # it also supersedes the looser ``seed_max_growth`` cap for a concave-up
    # electrode near the emergence boundary (the buried 60→530 µA fling).  The
    # schedule is a tuple of ``(ratio_upper, max_growth)`` — the FIRST tier
    # whose ``ratio_upper`` the measured ratio is BELOW sets the cap.  Tuned so
    # a quadratic (concave-up) electrode's single-step overshoot stays ≲ 1.3×
    # the limit; back-off then refines it into the band.  A concave-DOWN SIROF
    # is inherently safe (under-projects) so the cap only mildly SLOWS it.
    epol_growth_tiers: tuple = (
        (0.20, 2.5),    # ratio < 0.20 (far / buried)
        (0.45, 1.7),    # 0.20 .. 0.45
        (0.70, 1.35),   # 0.45 .. 0.70
        (9.99, 1.15),   # ≥ 0.70 (final approach)
    )
    # CONCAVE-DOWN (saturating-SIROF) LOOSENING.  The tiered cap above is tuned
    # for the DANGEROUS concave-UP electrode (E_pol accelerates → the predictor
    # over-projects → a fling).  A healthy concave-DOWN SIROF (E_pol ∝ √amp)
    # DECELERATES, so the local secant UNDER-projects the crossover and the
    # per-capture band stop catches any overshoot — the tight tier is then pure
    # friction that turns a ~7-capture climb into a ~60-capture fine-creep.  When
    # the last three captures MEASURE deceleration (``_epol_concave_down``), the
    # cap is loosened to this looser bound (still fling-limited against a noise
    # glitch).  Concavity is MEASURED, not predicted, so a concave-up fling can
    # never masquerade as concave-down to unlock it.
    concave_down_max_growth: float = 3.0
    # NEAR-BAND SNAP GUARD (operator, exp_vt_max_check: the E_pol-saturation STOP
    # that halted CH05/07/09/11/12/14 below the band was removed so they climb to
    # the band/max — but that EXPOSED a latent overshoot: the CONCAVE-DOWN
    # snap-to-ceiling below bypasses the growth cap and FLINGS a decelerating
    # electrode to max_ua, which for a slow climber ALREADY near the band (CH11 at
    # 0.964× → snapped 439 → 1000 µA) over-polarizes it to ~1.18× the limit.  The
    # bypass is only SAFE far from the band (lots of headroom); once the measured
    # |E_pol|/|limit| reaches this, the growth cap must ALWAYS bind so the
    # electrode reaches the band via bounded steps, never a fling.  The
    # per-capture band stop then catches the crossing at a bounded amplitude.
    snap_ceiling_max_ratio: float = 0.75
    # BURIED-REGION √-BOUNDED LOOSENING (operator: "reach the maximum charge
    # injection capacity more efficiently").  The FLAT loosest tier (×2.5) is
    # calibrated for the worst PHYSICAL concave-up electrode (a QUADRATIC
    # E_pol ∝ amp²) at the tier boundary (ratio 0.20 × 2.5 = 0.50 next-ratio),
    # so it WASTES the risk budget when the measured ratio is tiny — the
    # high-Q "flying blind" crawl (CH01 spent ~half its captures at ×2.5 with
    # E_pol ≪ 20 % of the limit).  Replace the flat cap, IN the loosest tier
    # only, with a growth that holds a QUADRATIC electrode's linear-projected
    # next-ratio at ``blind_target_next_ratio``: next_ratio ≈ ratio·grow² ≤ T
    # ⇒ grow ≤ √(T/ratio).  SAFE BY CONSTRUCTION for any E_pol ∝ amp^n with
    # n ≤ 2 (SIROF is n ≤ 1 saturating; a concave-up defect is ≤ quadratic) —
    # NO single step can cross the water window — because an electrode with a
    # tiny MEASURED ratio has its crossover many × the current away (a near
    # crossover would already show a measurable ratio).  The per-capture band
    # stop + bidirectional back-off remain the backstop for n > 2 / noise.
    # Clamped ≥ the flat tier value (never slower) and ≤ ``blind_growth_ceiling``.
    blind_target_next_ratio: float = 0.65
    blind_growth_ceiling: float = 6.0
    # CONTINUOUS-STIMULATION EXTRA SAFETY (operator, after a continuous
    # sinusoidal / KHFAC run RUINED almost every electrode).  Continuous stim
    # is the LEAST forgiving mode — there is NO interpulse rest, so any
    # overshoot polarizes the electrode HARD and SUSTAINED — AND its E_pol is
    # measured by the Ghazavi AC decomposition, which is less robust than the
    # pulsed IR-step method and reads ~0 when a capture fails to trigger
    # (the bench saw NUMACq=0 washouts).  So a continuous ramp must be
    # STRUCTURALLY gentle, not just reactive: the per-step growth is HARD-capped
    # to ``continuous_max_growth`` REGARDLESS of what E_pol reads, so even a
    # blind ramp (E_pol unmeasurable) can only creep, never fling.
    continuous_max_growth: float = 1.5
    # BLIND-RAMP STOP: on a continuous pattern, if the current has climbed past
    # ``continuous_blind_stop_ua`` but E_pol is STILL unmeasurable (worst ratio
    # below ``continuous_blind_ratio``) across ``continuous_blind_captures``
    # consecutive captures, the ramp is flying blind (washed captures or a
    # broken electrode) — STOP rather than climb to the hardware ceiling
    # without ever verifying the water window.
    continuous_blind_stop_ua: float = 30.0
    continuous_blind_ratio: float = 0.05
    continuous_blind_captures: int = 3

    # MATLAB-STYLE OSCILLATION to the water-window limit (operator: "Look at how
    # my MATLAB code tried to oscillate to the limit" — ``changeCurrent.m``).
    # Once a capture has OVERSHOT the limit, the back-off converges by a strict
    # over/under BRACKET (``min`` over-amp, ``max`` under-amp — narrows
    # monotonically, can't thrash) + a DISTANCE-TO-LIMIT step table (big steps
    # far, ever-finer near) + oscillation (below → increment, above →
    # decrement), staying strictly INSIDE the bracket (shrink ×0.9 / grow ×1.1
    # / bisect).  Robust to the E_pol NOISE that made the old regula-falsi
    # back-off grind (new-run CH01: 6 captures pinning a ±15 %-noisy crossover).
    # Distance thresholds (V) → step (µA), MATLAB MAX_POTENTIAL_DIFF_THRESH /
    # CURRENT_INCREMENT (the |decrement| table is slightly larger so an
    # overshoot backs off decisively).  The step also scales with the current
    # amplitude so it stays proportionate at high currents.
    oscillate_dist_thresh_v: tuple = (
        0.600, 0.500, 0.400, 0.300, 0.200, 0.100, 0.080,
        0.060, 0.050, 0.020, 0.010, 0.005, 0.002, 0.001)
    oscillate_increment_ua: tuple = (
        48.0, 36.0, 28.0, 24.0, 16.0, 12.0, 10.0,
        8.0, 6.0, 4.0, 2.0, 1.0, 0.5, 0.1)
    # DAMAGED-electrode detection during back-off: E_pol that RISES as the
    # current is DECREASED (non-monotonic) means the overshoot degraded the
    # surface (pcc CH05: 60 µA @ 1.93 V → 30 µA @ 4.01 V).  Stop the back-off
    # search on the first such rise beyond this ratio jump and accept the
    # tightest SAFE side rather than thrashing a ruined electrode.
    damage_ratio_rise: float = 0.15

    # ---- Early open/broken FAILURE detection (operator-configurable) --------
    # The ramp can stop early on a capacitive/open/broken electrode (no Faradaic
    # water window to ramp toward — gotcha #58).  Operator (CH02/CH10 mis-flagged
    # "open" at 6 µA): make it OPTIONAL + tunable, and NEVER stop below a minimum
    # current where the classification is unreliable.
    stop_on_bad_response: bool = True        # master enable for the early stop
    bad_response_auto: bool = True           # True → built-in Z thresholds;
    #                                          False → use the *_z_kohm values below
    bad_response_min_current_ua: float = 50.0   # no early-stop below this current
    bad_response_open_z_kohm: float = 50.0      # |V|/|I| ≥ this ⇒ open (manual mode)
    bad_response_broken_z_kohm: float = 200.0   # |V|/|I| ≥ this ⇒ broken (manual)


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
                 polarization_tolerance_v: float = 0.0,
                 sweep_points: Optional[List[SweepPoint]] = None):
        super().__init__(session, stimulator, oscilloscope)
        self.ramp = ramp or RampPolicy()
        # SAFETY: never let the ramp target past the PlexStim hardware rail
        # (1000 µA).  A pattern above it raises ValueError in
        # PulsePattern.validate → the run aborts.  This clamps ANY caller-passed
        # ramp (e.g. a legacy RampPolicy(max_ua=1500), or Long Pulsing's default
        # ramp which omits max_ua and would inherit the dataclass default) down
        # to the rail, so no path can climb into the invalid region.  gotcha #56.
        try:
            self.ramp.max_ua = min(float(self.ramp.max_ua), STIM_MAX_AMPLITUDE_UA)
        except Exception:
            self.ramp.max_ua = STIM_MAX_AMPLITUDE_UA
        # STABLE copy of the policy excitation ceiling (post-rail-clamp).  Each
        # configuration derives its own RECHARGE-AWARE ceiling from THIS value
        # (not the possibly-already-lowered ``self.ramp.max_ua``) so a per-point
        # asymmetry in a sweep is handled and the clamp never compounds.
        self._policy_max_ua = float(self.ramp.max_ua)
        self.polarization_source = polarization_source
        self.configurations = configurations or [session.test.configuration]
        # Multi-parameter sweep axis. Each point transforms the session's
        # base pattern (rate and/or phase-width asymmetry) before its own
        # amplitude ramp. ``None`` → a single implicit point that leaves
        # the base pattern untouched, i.e. the original single-parameter
        # behaviour.
        self.sweep_points = sweep_points or [SweepPoint(label="")]
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

    def _resolve_dc_ac_roles(self, snap) -> tuple:
        """Roles that use the capture-in-DC-then-AC trick (gotcha #85): any role
        whose scope channel is set to "DC + AC" on the PER-CHANNEL coupling
        dropdown.  This is the sole control now — the old global "Electrode
        coupling" dropdown was removed (operator: "remove electrode coupling
        since the oscilloscope channels can set it").  Non-DC-biased roles
        (V_mon / I_mon) may be listed but the helper's bias-ratio gate makes the
        trick a no-op there, so it's safe.  A legacy ``eret_coupling`` key in an
        old snapshot is ignored."""
        couplings = {str(k).upper(): str(v).strip().lower()
                     for k, v in (snap.get("channel_couplings") or {}).items()}
        aliases = getattr(self.scope, "channel_aliases", None) or {}
        roles = set()
        for role in ("eret", "eact", "vmon", "imon"):
            ch = str(aliases.get(role, "")).upper()
            mode = couplings.get(ch, "auto") if ch else "auto"
            if mode in ("dc + ac", "dc+ac", "dc_ac"):
                roles.add(role)
        return tuple(roles)

    def run(self) -> ExperimentResult:
        self.preflight()
        # Fresh run → re-measure each channel's electrode DC offset before
        # AC-coupling (see ``measure_electrode_dc_offsets_and_switch_to_ac``).
        self._electrode_offset_config_key = None
        # Roles that use the capture-in-DC-then-AC trick (gotcha #85); resolved
        # below from the per-channel coupling dropdowns + the global mode.
        self._electrode_ac_roles: tuple = ()
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
            # Track wall-clock start so the GUI's progress widget can
            # compute elapsed + ETA.  Single timestamp at run begin;
            # downstream emits pass it through unchanged.
            import time as _time_run_start
            _run_started_at = _time_run_start.monotonic()
            _total_configs = max(len(self.configurations), 1)
            for cfg_idx, config in enumerate(self.configurations):
                if self.aborted:
                    break
                # Progress emit (Task #56): one event per
                # configuration so the operator sees "VT 3/16
                # channels" in the status bar.  Per-amplitude
                # progress would be noisier (~10 events / channel)
                # without adding much — channel-level granularity is
                # the right resolution for the long-sweep
                # frozen-looking experience this addresses.
                try:
                    self._emit_progress(
                        step=cfg_idx + 1,
                        total=_total_configs,
                        label=f"VT {config.display_name()}",
                        started_at=_run_started_at,
                    )
                except Exception:
                    pass
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
                # Inner axis: walk each sweep point for this config. The
                # common (non-sweep) case is a single point that leaves
                # the base pattern untouched.
                for point in self.sweep_points:
                    if self.aborted:
                        break
                    base = pattern_for_sweep_point(self.session.test.pattern,
                                                   point)
                    # A sweep point can produce an out-of-range pattern
                    # (e.g. a long asymmetric recharge phase that no
                    # longer fits inside the period at a high rate).
                    # Skip just that point with a clear log line rather
                    # than aborting the whole sweep.
                    try:
                        base.validate()
                    except ValueError as e:
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session,
                            message=(f"Skipping sweep point "
                                     f"{point.label or '(base)'}: {e}")))
                        continue
                    run = self._run_one_configuration(config, base, point.label)
                    self.session.add_run(run)
                    all_captures.extend(run.captures)
                    self._emit(ExperimentEvent(
                        kind="run_end", session=self.session, run=run))
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
    def _fit_scope_window(self) -> None:
        """Size the scope's horizontal window to the pulse before the run.

        Delegates to the driver's ``auto_layout_for_pulse`` so the
        record spans roughly the pulse plus a small margin instead of
        running far past it into a post-pulse tail. On scopes without an
        EXT-trigger input (e.g. the 2-channel TBS1072C) that tail is
        where an averaged edge-triggered record smears into "noise"; a
        tight window removes it rather than depending on the operator
        picking a narrow timebase by hand.

        Best-effort: no-op on backends that don't expose the helper
        (the simulator), and a failed SCPI write is logged, never fatal.
        The digital-delay pre-trigger is fixed at 1 µs to match the lab's
        Plexon sync wiring.
        """
        fit = getattr(self.scope, "auto_layout_for_pulse", None)
        if not callable(fit):
            return  # backend without horizontal auto-layout (e.g. sim)

        # Size to the WIDEST pulse across the sweep: asymmetric points
        # lengthen the recharge phase, so fitting to the longest keeps
        # every point inside the window.
        totals: List[float] = []
        for pt in self.sweep_points:
            try:
                totals.append(pattern_for_sweep_point(
                    self.session.test.pattern, pt).total_pulse_us)
            except Exception:
                continue
        total_us = (max(totals) if totals
                    else self.session.test.pattern.total_pulse_us)
        ext = bool(getattr(self.scope.info, "has_ext_trigger", True))
        try:
            scale_s, pos_pct = fit(
                phase1_us=total_us, interphase_us=0.0,
                phase2_us=0.0, discharge_us=0.0,
                digital_delay_us=1.0, ext_trigger=ext)
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=(
                    f"Scope window fit to pulse ({total_us:.0f} µs): "
                    f"{scale_s * 1e6:.1f} µs/div, trigger @ {pos_pct:.1f}% "
                    f"({'EXT sync' if ext else 'edge / no EXT input'}).")))
        except Exception as e:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=f"Scope window auto-fit skipped: {e}"))

    # ------------------------------------------------------------------
    def _recharge_aware_max_ua(self, base_pattern) -> float:
        """The highest EXCITATION (phase-1) amplitude the ramp may target such
        that EVERY phase of the amplitude-scaled pattern stays within the
        PlexStim rail (``STIM_MAX_AMPLITUDE_UA``, 1000 µA).

        The ramp scales the whole pattern by ``amp / |excitation|`` (see
        ``_pattern_at_amplitude``), so an ASYMMETRIC pattern — most commonly a
        cap-coupled **exp-decay recharge**, whose fast decay carries only ~20 %
        of the charge a rectangle of the same peak would, forcing its PEAK to
        several× the excitation to stay charge-balanced — drives the RECHARGE
        phase into the rail at an EXCITATION amplitude well BELOW ``max_ua``.
        Without capping there, the ramp steps past that point and the device
        REJECTS the pattern ("Stimulator program error: Phase 2 amplitude
        exceeds max" — the exp_vt_max_pcc failure: phase-2 = +1191 µA at
        excitation −260 µA, ratio 4.58, so the rail is hit at excitation
        ≈ 218 µA, not 1000 µA).

        Returns ``min(policy_max_ua, rail / max_phase_ratio)`` where
        ``max_phase_ratio`` = the largest ``|phase_i| / |excitation|`` over the
        base pattern.  A symmetric / excitation-dominant pattern (ratio ≤ 1) or
        a zero-amplitude template (``_pattern_at_amplitude`` rebuilds it with
        equal-magnitude phases) is unaffected → returns the policy ceiling."""
        base_ceiling = float(getattr(self, "_policy_max_ua", self.ramp.max_ua))
        try:
            phases = list(base_pattern.phases)
            exc = abs(float(base_pattern.excitation_phase.amplitude_ua))
            if exc <= 1e-12 or not phases:
                return base_ceiling
            ratio = max(abs(float(p.amplitude_ua)) / exc for p in phases)
            if ratio <= 1.0 + 1e-9:
                return base_ceiling
            return min(base_ceiling, float(STIM_MAX_AMPLITUDE_UA) / ratio)
        except Exception:
            return base_ceiling

    def _run_one_configuration(self, config: Configuration,
                               base_pattern: Optional[PulsePattern] = None,
                               label: str = "") -> ChannelRun:
        run = ChannelRun(configuration=config,
                         surface_area_um2=self.surface_area_um2,
                         label=label)
        suffix = f" [{label}]" if label else ""
        self._emit(ExperimentEvent(
            kind="run_start", session=self.session, run=run,
            message=f"Sweep {config.display_name()}{suffix}",
        ))

        # Multi-parameter sweep passes a per-config ``base_pattern`` (different
        # rate / phase-width asymmetry); fall back to the session pattern for a
        # plain single-config run.
        if base_pattern is None:
            base_pattern = self.session.test.pattern
        # RECHARGE-AWARE excitation ceiling: cap the ramp so the largest phase
        # (e.g. a cap-coupled exp-decay recharge, whose peak runs several× the
        # excitation) can't be driven past the 1000 µA rail — the ramp then
        # stops CLEANLY ("hardware-limited") at that excitation instead of
        # overshooting and getting a device rejection (exp_vt_max_pcc).
        # Derived from the STABLE _policy_max_ua so it's recomputed per config
        # (per-sweep-point asymmetry) and never compounds.
        _eff_max = self._recharge_aware_max_ua(base_pattern)
        self.ramp.max_ua = _eff_max
        if _eff_max < self._policy_max_ua - 1e-6:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=(f"Recharge-limited ceiling: excitation capped at "
                         f"{_eff_max:.0f} µA — the recharge phase reaches the "
                         f"{STIM_MAX_AMPLITUDE_UA:.0f} µA hardware rail there "
                         f"(asymmetric / cap-coupled pattern), so the ramp can't "
                         f"climb to {self._policy_max_ua:.0f} µA on this pulse.")))
        # CONTINUOUS-STIM extra safety flag (see RampPolicy.continuous_*): a
        # continuous sinusoid / KHFAC has no interpulse rest, so its ramp is
        # hard-capped gentler and stops if it's flying blind.
        try:
            self._is_continuous_pattern = bool(
                is_continuous_sinusoidal(base_pattern))
        except Exception:
            self._is_continuous_pattern = False
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
                # Roles that use the DC→AC fine-scale trick (gotcha #85):
                # resolved from the PER-CHANNEL coupling dropdown's "DC + AC"
                # items (``channel_couplings`` in the snapshot).  The global
                # "Electrode coupling" dropdown was removed; the per-channel
                # column is the sole control now.
                self._electrode_ac_roles = self._resolve_dc_ac_roles(snap)
        except Exception:
            _env_short = None
        # The ramp STARTS at the configured stimulation pattern's own
        # excitation amplitude (operator: "the stimulation pattern should be
        # starting Istim" — RampPolicy.starting_ua was removed).  The first
        # capture therefore uses the pattern AS DRAWN — **including a
        # ZERO-amplitude template ("I want to start at 0 µA during maximum
        # VT"): the FIRST capture IS taken at 0 µA** (a no-current baseline;
        # its response classification / limit checks are GATED OFF in the
        # loop below — a 0 µA pulse can't diagnose anything).  The SECOND
        # capture is strategy-dependent (operator: "the next capture should
        # be either the next step if the ramp is stepped; otherwise, it
        # should be 1 µA if adaptive/regression is chosen"):
        #   * fixed increment      → 0 + coarse step (the staircase's own
        #     next step, via ``_next_step_increment``);
        #   * adaptive / predictive → EXACTLY 1 µA (``_next_step``'s
        #     zero-amp branch) — a gentle probe that gives the regression /
        #     secant a real first measurement before the big seed jumps.
        excite_amp_abs = abs(base_pattern.excitation_phase.amplitude_ua)
        # Scope sizing needs a non-zero reference: size the initial view for
        # the FIRST REAL current (1 µA) when starting at 0 µA.
        self.apply_default_scope_view(
            base_pattern, amp_ua=(excite_amp_abs or 1.0),
            is_multipolar=_is_multipolar,
            environment_short=_env_short,
            reason=f"start of channel {config.active}")
        # Arm closed-loop bias feedback if the GUI pushed a controller
        # onto this runner.  Must happen AFTER apply_default_scope_view
        # so the controller's MEASUrement-gating SCPI writes aren't
        # clobbered.  Re-armed per configuration because each config's
        # apply_default_scope_view above clears the gating window.
        # Disarmed at the end of the configuration (before stop_all)
        # so the next config's apply + arm sees a clean state.  No-op
        # when no controller is attached.
        self.arm_bias_feedback()
        amp = excite_amp_abs          # ramp starts at the pattern's amplitude
                                      # (0.0 for a zero template → the first
                                      #  capture IS the 0 µA baseline)
        capture_idx = 0
        # Highest amplitude whose E_pol was BELOW the acceptance band (safe —
        # more current OK).  Serves as the lower bracket for the bidirectional
        # back-off when a later step OVERSHOOTS (task #101).
        last_safe_amp = None
        last_safe_cap = None
        # Reset the adaptive bookkeeping so each new configuration
        # starts with a clean prediction trail.
        self._prediction_history = []
        self._oscillation_count = 0
        self._safety_probe_armed = False
        # Zero-start seed-dampening schedule counter (×0.5, ×0.6, ×0.7 for
        # the first three jumps, then full) — reset per configuration.
        self._ramp_dampen_step = 0
        # Predictive V/div seed state — cleared per channel so the first
        # step of a new channel uses apply_default_scope_view's sizing
        # (no prior observed range to extrapolate from).
        self._seed_prev_amp_ua = None
        self._seed_prev_half_range = {}

        # ----- main amplitude ramp ---------------------------------------
        # We ramp the stimulus amplitude upward from the pattern amplitude
        # (``amp`` seeded above), capturing one waveform per step. Steps shrink
        # near the safety limits so we land *just* below the SIROF water window
        # without overshooting.  (``excite_amp_abs`` was computed above, before
        # apply_default_scope_view, so the scope view + the ramp start agree.)
        try:
            while amp <= self.ramp.max_ua and not self.aborted:
                # User PAUSE checkpoint (between amplitude steps — stim is
                # already stopped here by the previous step's finally, so the
                # halt is idempotent and the next _one_capture reloads+starts
                # on resume → restart=None).
                if not self.wait_if_paused():
                    break
                # Build the pattern so its *excitation* phase magnitude equals
                # ``amp``. Other phases scale by the same factor, preserving the
                # biphasic / triphasic ratio.  ``_pattern_at_amplitude`` is
                # identical to ``base_pattern.scaled(amp / excite_amp_abs)`` for
                # a normal template, but ALSO ramps a ZERO-amplitude template
                # (VT "starting at 0 µA") — plain scaling can't grow a zero, so
                # the old code stayed at 0 µA the whole ramp (gotcha: the
                # "did not increase the current" / "stopped at #7" report).
                pattern = self._pattern_at_amplitude(base_pattern, amp)
                cap, limit_hit = self._capture_and_flag(
                    config, pattern, run, capture_idx)
                capture_idx += 1

                if cap.status.aborted:
                    break

                # Stop ramping a NON-FARADAIC electrode early (operator:
                # "the very apparent bad channel/combo — clearly capacitive
                # response with no resistance — STILL continues to be
                # tested.  You can easily test this by just linear
                # regression of the first phase").  A capacitive / open /
                # broken response has NO water window to ramp toward: no
                # resistance ⇒ no IR drop ⇒ E_pol is cleared (so
                # _potential_limit_hit never trips), and a clean low-
                # amplitude capacitive ramp never reaches voltage
                # compliance — so the old loop ramped it all the way to
                # ``max_ua``, wasting captures + time on a dead/degenerate
                # electrode.  ``classify_response_and_ceff`` (run inside
                # compute_metrics, gotcha #58) flags these via the
                # first-phase linear-fit / IR-step test; the classification
                # is amplitude-INDEPENDENT (IR-step/peak = R/(R+W/C), the
                # current cancels), so a single capacitive/open/broken
                # capture is a reliable stop — a real SIROF keeps its IR
                # step and stays ``normal``.
                # **GATED on ``amp > 0``**: the 0 µA BASELINE capture of a
                # "start at 0 µA" VT-max run delivers NO current, so its
                # V_mon is pure noise / switching artifact and the classifier
                # cannot diagnose anything — without this gate the baseline
                # read "broken" and stopped the ramp at capture #1 before it
                # ever delivered current (operator: the first capture IS at
                # 0 µA; the ramp continues from it).
                # The classification already honours the min-current gate +
                # manual Z thresholds (threaded into compute_metrics above), so
                # a low-current or (in manual mode) sub-threshold capture reads
                # "normal" here and the ramp keeps climbing.  The master toggle
                # ``stop_on_bad_response`` (operator: "make the early check
                # optional") lets the operator disable the early stop entirely —
                # the ramp then runs to the potential limit / compliance / max
                # current regardless of the classification.
                _resp = getattr(cap.metrics, "response_class", "normal")
                if (getattr(self.ramp, "stop_on_bad_response", True)
                        and _resp in ("capacitive", "open", "broken")
                        and amp > 0.0):
                    cap.status.notes = (
                        (cap.status.notes + " | " if cap.status.notes else "")
                        + f"stopped early: {_resp} response "
                          f"(no Faradaic water window to ramp toward)")
                    _ceff = getattr(cap.metrics, "effective_capacitance_nf",
                                    float("nan"))
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session, run=run,
                        message=(
                            f"{config.display_name()}: {_resp} response "
                            f"detected at {amp:+.1f} µA"
                            + (f" (C_eff ≈ {_ceff:.1f} nF)"
                               if _ceff == _ceff else "")  # nan-safe
                            + " — no resistance / no E_pol limit, stopping "
                              "ramp early instead of running to max "
                              "current.")))
                    break

                # CONTINUOUS-STIM "flying blind" stop: a continuous sinusoid /
                # KHFAC whose current has climbed past
                # ``continuous_blind_stop_ua`` while E_pol stays UNMEASURABLE
                # (worst ratio below ``continuous_blind_ratio``) for
                # ``continuous_blind_captures`` consecutive captures is either
                # washing out (NUMACq=0 trigger failure) or driving a broken
                # electrode — either way we CANNOT verify the water window, and
                # continuous stim gives no rest, so STOP rather than climb blind
                # to the hardware ceiling (the failure that ruined the
                # electrodes).
                if (getattr(self, "_is_continuous_pattern", False)
                        and amp >= float(getattr(self.ramp,
                                                 "continuous_blind_stop_ua", 30.0))):
                    _nblind = int(getattr(self.ramp,
                                          "continuous_blind_captures", 3))
                    _bratio = float(getattr(self.ramp,
                                            "continuous_blind_ratio", 0.05))
                    _recent = [c for c in run.captures
                               if not c.status.aborted
                               and abs(c.pattern.excitation_phase.amplitude_ua)
                               > 0.0][-_nblind:]
                    _rr = [self._polarization_ratio(c) for c in _recent]
                    # Blind ⇔ E_pol is near-zero AND NOT GROWING with current.
                    # A washed / broken capture reads a flat ~0; a HEALTHY
                    # high-capacity electrode has a SMALL but RISING E_pol at
                    # low current, so its worst ratio grows step-to-step — the
                    # ``max ≤ 1.5·min`` (flat) test spares it while still
                    # catching a genuinely non-responding trace.
                    _flat = (len(_rr) >= _nblind
                             and max(_rr) < _bratio
                             and max(_rr) <= 1.5 * min(_rr))
                    if _flat:
                        note = (f"continuous-stim SAFETY STOP: E_pol "
                                f"unmeasurable (< {_bratio:.0%} of limit) for "
                                f"{_nblind} captures up to {amp:.1f} µA — cannot "
                                f"verify the water window, refusing to ramp "
                                f"blind (check the trigger / NUMACq).")
                        cap.status.notes = (
                            (cap.status.notes + " | " if cap.status.notes else "")
                            + note)
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session, run=run,
                            message=f"{config.display_name()}: {note}"))
                        break

                # ``limit_hit`` + the reached-limit flag were computed and
                # stored above (before the emit); reuse them for the stop
                # decision so the flag can't disagree with the break reason.
                compliance = cap.status.voltage_compliance

                if compliance:
                    break
                if limit_hit:
                    # An OVERSHOOT (E_pol past the far edge) → back off and
                    # RE-CAPTURE to land in-band instead of stopping on the
                    # overshoot (operator, RECURRING + emphatic: "the maximum
                    # VT testing is not continuing to DECREMENT the current if
                    # it exceeds the potential limits"; "quitting too soon …
                    # enough current to reduce").  Otherwise (a clean in-band
                    # stop — reached the near edge but did NOT overshoot the
                    # far edge) stop here as before.
                    #
                    # The bracket floor is the last below-band amplitude, or
                    # 0 µA (always safe — 0 current → 0 polarization) when the
                    # very first tested current already overshot (``last_safe_
                    # amp is None`` on a non-zero-start ramp).  The room-to-
                    # decrement test uses the 0.1 µA TESTING resolution, NOT
                    # the 1 µA forward ``fine_step_ua`` — the bug the operator
                    # kept hitting: a FINE-APPROACH overshoot lands ~1 µA above
                    # the last safe amp, so the old ``> fine_step_ua`` (1 µA)
                    # gate was FALSE and back-off was skipped, stopping the
                    # ramp on the overshoot without ever decrementing.  Back-
                    # off's own bracketing runs at 0.1 µA, so any ≥ 0.1 µA gap
                    # can and must be decremented into.
                    _lo_amp = last_safe_amp if last_safe_amp is not None else 0.0
                    _res = abs(getattr(self.ramp,
                               "test_current_resolution_ua", 0.1)) or 0.1
                    if (self.ramp.backoff_on_overshoot
                            and cap.status.exceeded_potential_limit
                            and (amp - _lo_amp) > _res):
                        capture_idx = self._backoff_to_band(
                            config, base_pattern, run,
                            lo_amp=_lo_amp, lo_cap=last_safe_cap,
                            hi_amp=amp, hi_cap=cap, capture_idx=capture_idx)
                    break

                # E_POL SATURATION near the water window — the electrode's
                # polarization has plateaued just below the band and is only
                # noise-limited from crossing, so stop instead of creeping ~30
                # captures for a measurement-noise spike (operator,
                # exp_vt_max_check: "reduce number of captures"; CH14 crept
                # 720→779 µA, CH04 424→461).  Reports the saturation amplitude
                # as the max charge injection — CONSERVATIVE (a few % below the
                # eventual noise crossing) and safe (E_pol will not climb
                # further, so no higher excursion is missed).  Continuous-stim
                # is EXEMPT (its own blind-stop above owns that case).
                # NOTE: the old E_pol-saturation STOP (that halted a channel
                # BELOW the band with a "not enough precision" lower bound) was
                # REMOVED here — operator (exp_vt_max_check): "CH05/CH07/CH09/CH11/
                # CH12/CH14 did not reach any potential limit or maximum current".
                # Those channels are SLOW CLIMBERS approaching the band (CH11
                # reached 0.964× — the band near-edge is 0.967×), NOT plateaus, so
                # the saturation detector's flat-window heuristic mistook their
                # slow climb for a plateau and stopped them below both the band and
                # max.  A channel must now COMPLETE by reaching the BAND (a few
                # more fine steps — no overshoot) or MAX CURRENT (the snap-to-
                # ceiling in `_next_step`, which fires ONLY when the crossover
                # projects ≥ max_ua so it can never overshoot the window).  A
                # snap-to-max on a *detected* plateau was rejected: near the band a
                # slow climber is statistically indistinguishable from a plateau
                # over a short noisy window, and snapping the climber to max
                # over-polarizes the electrode (gotcha #178).  `_epol_plateaued_
                # near_limit` is retained (a tested helper) but no longer stops the
                # ramp.

                # Below the band → this amplitude is safe; remember it as the
                # back-off floor before stepping up.
                last_safe_amp = amp
                last_safe_cap = cap
                _delta = self._maybe_safety_probe(
                    self._next_step(cap, amp, run.captures), run.captures)
                # SEED SAFETY CAP: while the polarization signal is still
                # buried (near-noise), a prediction can grossly overshoot a
                # low-Q electrode (bumps CH16: 1 µA → 51 µA → −4.8 V).  Cap the
                # step to ``seed_max_growth`` × the current amplitude so a
                # buried-signal jump grows at most ~×10.  Unclamped once signal
                # emerges (the operator's full predicted × seed_fraction jump).
                if amp > 0 and not self._signal_emerged(run.captures):
                    _cap_delta = amp * (self.ramp.seed_max_growth - 1.0)
                    if _cap_delta > 0:
                        _delta = min(_delta, _cap_delta)
                # E_POL-PROXIMITY GROWTH CAP — runs AFTER the seed cap and the
                # safety probe, BEFORE the snap.  Bounds the per-step growth by
                # how close the WORST MEASURED E_pol already is to its water-
                # window limit (tiered schedule).  ALWAYS-ON (valid from the
                # first capture; the buried ratio≈0 → the loosest tier), so it
                # supersedes the looser seed cap AND overrides a false
                # snap-to-ceiling on a concave-up low-Q electrode (the exact
                # pcc failure: an over-projected crossover mis-read as "hardware
                # limited" → flung to 1000 µA → electrode-ruining overshoot
                # before any band check).  A concave-DOWN SIROF is inherently
                # safe (under-projects) so this only mildly slows it.  SKIPPED
                # for the fixed-``increment`` strategy — those steps are the
                # operator's EXPLICIT choice (gotcha #56), not a predictor
                # projection, so they must not be capped.
                if amp > 0 and (self.ramp.strategy or "").lower() != "increment":
                    # DECISION signal = BASELINE-SUBTRACTED proximity, so the
                    # no-signal low-current region (rest-polarization-dominated)
                    # climbs fast (blind tier / √-loosening) instead of creeping
                    # in the moderate 1.7× tier — the exp_vt_max_check ~15-capture
                    # ceiling crawl.  ``_raw_ratio`` (absolute |E_pol|/|limit|) is
                    # kept only for the operator-facing log line.
                    _ratio = self._growth_ratio(run.captures)
                    _raw_ratio = self._worst_epol_ratio(run.captures)
                    _grow = None
                    for _thr, _g in getattr(self.ramp, "epol_growth_tiers",
                                            ((9.99, 1e9),)):
                        if _ratio < _thr:
                            _grow = _g
                            break
                    # BURIED-REGION √-BOUNDED LOOSENING: in the LOOSEST tier
                    # (measured ratio far from any limit), raise the flat cap to
                    # √(T/ratio) — the growth that keeps a QUADRATIC electrode's
                    # projected next-ratio ≤ T (see RampPolicy.blind_* — safe for
                    # any n ≤ 2 because a tiny measured ratio means the crossover
                    # is many × away).  Only speeds the high-Q crawl; the tighter
                    # final-approach tiers (ratio ≥ the first threshold) are
                    # untouched, and the clamp keeps it ≥ the flat value.
                    _tiers = getattr(self.ramp, "epol_growth_tiers",
                                     ((0.20, 2.5),))
                    if _grow is not None and _tiers and _ratio < _tiers[0][0]:
                        _T = float(getattr(self.ramp,
                                           "blind_target_next_ratio", 0.65))
                        _ceil = float(getattr(self.ramp,
                                             "blind_growth_ceiling", 6.0))
                        # Floor the ratio so √ is bounded by the ceiling — at the
                        # floor a quadratic electrode's next-ratio is still ≤ T.
                        _r = max(_ratio, _T / (_ceil * _ceil))
                        _grow = max(_grow, min(_ceil, (_T / _r) ** 0.5))
                    # NEAR-BAND GUARD: once the ABSOLUTE measured proximity
                    # ``_raw_ratio`` reaches ``snap_ceiling_max_ratio``, the
                    # electrode has too little headroom for ANY loosened / snapped
                    # step to be safe — a slow climber must reach the band via the
                    # TIGHT tier (bounded), never a fling.  Both the concave-down
                    # loosening AND the snap-to-ceiling below are disabled in this
                    # zone (the CH11/CH14 1.18× over-polarization the removed
                    # saturation-stop used to mask).  ``_raw_ratio`` (not the
                    # baseline-subtracted ``_ratio``) is the honest "how close to
                    # the limit am I" for a safety gate; NaN → treat as near-band
                    # (can't confirm we're far, so bind).
                    _snap_max = float(getattr(self.ramp,
                                              "snap_ceiling_max_ratio", 0.75))
                    _near_band = (not np.isfinite(_raw_ratio)) or \
                        (_raw_ratio >= _snap_max)
                    # SIGNAL-EMERGED GATE: the concave-down loosening / snap must
                    # NOT fire while E_pol is still BASELINE-DOMINATED (baseline-
                    # subtracted ``_ratio`` below the first tightening tier).  A
                    # high-rest-potential electrode (CH11: ~0.31× rest) reads a FLAT
                    # incremental E_pol at low current, which ``_epol_concave_down``
                    # mis-reads as "deceleration" → the snap flung it 10.8 → 1000 µA
                    # (a mid-climb crossover the fling blew past → 1.19× the limit).
                    # In this blind region the bounded √-loosening owns the climb
                    # (fast but never an unbounded fling).
                    _emerged = np.isfinite(_ratio) and _ratio >= _tiers[0][0]
                    _cd_ok = (self._epol_concave_down(run.captures)
                              and _emerged and not _near_band)
                    if _grow is not None and _cd_ok:
                        _cd = float(getattr(self.ramp,
                                            "concave_down_max_growth", 3.0))
                        _grow = max(_grow, _cd)
                    # CONCAVE-DOWN SNAP-TO-CEILING (operator: "optimise the
                    # mid-climb on decelerating electrodes"): a CONFIRMED
                    # decelerating electrode whose predicted step already REACHES
                    # the hardware ceiling (its crossover projects beyond max_ua,
                    # so ``_next_step`` returned the max_ua − current snap) is
                    # genuinely hardware-limited.  Let the snap through instead of
                    # the growth cap holding it to small mid-climb steps (the
                    # high-capacity ~5-capture 130 → 1000 µA creep on
                    # exp_vt_max_check).  SAFE: deceleration is MEASURED (a
                    # concave-up fling can't fake it → the pcc override still
                    # binds concave-up), the snap only fires when the step is
                    # ALREADY the ceiling snap (never an interior over-projection),
                    # and the per-capture band stop + back-off still catch any
                    # overshoot AT the ceiling.  Not for continuous-stim (its own
                    # hard cap must win) and DISABLED near the band (``_near_band``
                    # above) — a slow climber ALREADY near the band has too little
                    # headroom for a full snap to max, which over-polarizes it (the
                    # CH11/CH14 1.18× the removed saturation-stop used to mask).
                    if (_grow is not None and _cd_ok
                            and not getattr(self, "_is_continuous_pattern", False)
                            and amp + _delta >= self.ramp.max_ua - 1e-6):
                        _grow = None
                    # CONTINUOUS-STIM hard cap: a continuous sinusoid / KHFAC
                    # has no interpulse rest and its Ghazavi E_pol can read ~0
                    # on a washed capture, so bound the growth REGARDLESS of the
                    # measured ratio — even a blind ramp can only creep, never
                    # fling (this is the failure that ruined the electrodes).
                    if getattr(self, "_is_continuous_pattern", False):
                        _cm = float(getattr(self.ramp,
                                            "continuous_max_growth", 1.5))
                        _grow = _cm if _grow is None else min(_grow, _cm)
                    if _grow is not None:
                        _pcap = amp * (_grow - 1.0)
                        if 0.0 < _pcap < _delta:
                            self._emit(ExperimentEvent(
                                kind="log", session=self.session, run=run,
                                message=(
                                    f"{config.display_name()}: E_pol at "
                                    f"{_raw_ratio:.0%} of limit — capping step "
                                    f"{_delta:.1f} → {_pcap:.1f} µA "
                                    f"(growth guard).")))
                            _delta = _pcap
                # Snap the tested amplitude to the 0.1 µA testing grid
                # (operator: "current testing to have 0.1 µA resolution").
                amp = self._snap_test_ua(amp + _delta)
                # MATLAB no-re-test (changeCurrent.m:526 — operator: "how my
                # MATLAB accounted for already-tested amplitudes"): never spend a
                # capture re-measuring a current already tested on the grid.  If
                # the snapped target coincides with an already-tested amplitude,
                # NUDGE it UP one resolution step until fresh (bounded; the main
                # loop only steps up, so up-nudge preserves the forward ramp).
                _res_grid = abs(getattr(
                    self.ramp, "test_current_resolution_ua", 0.1)) or 0.1
                _tested = {round(abs(c.pattern.excitation_phase.amplitude_ua)
                                 / _res_grid)
                           for c in run.captures if not c.status.aborted}
                _nudge = 0
                while (round(amp / _res_grid) in _tested
                       and amp < self.ramp.max_ua and _nudge < 10_000):
                    amp = self._snap_test_ua(amp + _res_grid)
                    _nudge += 1
        finally:
            # Disarm bias feedback at the end of THIS configuration's
            # sweep so the NEXT configuration's apply_default_scope_view
            # + arm_bias_feedback above start from a clean scope state.
            # Finally-safe so a mid-sweep exception leaves the
            # controller properly disarmed.  Idempotent + swallows
            # controller failures.
            self.disarm_bias_feedback()

        # HARDWARE-LIMITED flag (operator safety hardening): if the ramp exited
        # by reaching max_ua WITHOUT any capture reaching the water-window
        # limit — and not on voltage compliance, a bad-response early stop, or
        # an abort — then the electrode's true ceiling is BEYOND the delivered
        # amplitude and the reported max(Q_inj) is only a LOWER BOUND.  Flag it
        # (note on the last capture + a log line) so downstream readers don't
        # treat it as the achieved maximum; do NOT extrapolate a crossover past
        # the PlexStim hardware max.
        try:
            _caps = [c for c in run.captures if not c.status.aborted]
            if not self.aborted and _caps:
                _last = _caps[-1]
                _reached = any(c.status.reached_potential_limit for c in _caps)
                _bad = getattr(_last.metrics, "response_class",
                               "normal") != "normal"
                if (not _reached and not _last.status.voltage_compliance
                        and not _bad and _last.pattern.excitation_phase):
                    _amp_last = abs(_last.pattern.excitation_phase.amplitude_ua)
                    _note = (f"hardware-limited: max(Q_inj) not reached "
                             f"(ramp maxed at {_amp_last:.0f} µA — reported "
                             f"value is a lower bound)")
                    _last.status.notes = (
                        (_last.status.notes + " | " if _last.status.notes
                         else "") + _note)
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session, run=run,
                        message=f"{config.display_name()}: {_note}"))
        except Exception:
            pass

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
    def _capture_and_flag(self, config: Configuration, pattern: PulsePattern,
                          run: ChannelRun, capture_idx: int):
        """Capture ONE waveform, record its dose, set the reached/exceeded
        potential-limit flags, emit it to the GUI, and run any armed
        closed-loop bias step.  Returns ``(cap, limit_hit)``.

        Shared by the main ramp loop AND the bidirectional back-off search
        so a capture is treated identically on both paths (dose accounting,
        limit flags, live emit, bias step).
        """
        cap = self._one_capture(config, pattern, capture_idx)
        run.captures.append(cap)
        # Per-capture pulse count + cumulative delivered charge, AFTER the
        # append so the cumulative sum includes this capture.
        self._record_capture_dose(run, cap)
        # Nonparametric access-resistance drift monitor (Mann-Whitney vs the
        # run's earlier R_a; warn-only, never affects the ramp).
        self.check_access_resistance_drift(run)
        # Set the reached / exceeded flags BEFORE the emit so the live
        # metrics table shows the true state immediately (not the default
        # False).  ``_potential_limit_hit`` reads only this capture's
        # already-computed E_pol; a bad-response capture has its E_pol
        # cleared → False.
        limit_hit = self._potential_limit_hit(cap)
        cap.status.reached_potential_limit = limit_hit
        cap.status.exceeded_potential_limit = (
            self._potential_limit_exceeded(cap))
        self._emit(ExperimentEvent(
            kind="capture", session=self.session, run=run, capture=cap))
        # Closed-loop bias step at amplitude-step cadence (cheap no-op when
        # the controller isn't armed).
        self.bias_step_if_armed()
        return cap, limit_hit

    def _backoff_to_band(self, config: Configuration,
                         base_pattern: PulsePattern, run: ChannelRun, *,
                         lo_amp: float, lo_cap: Capture,
                         hi_amp: float, hi_cap: Capture,
                         capture_idx: int) -> int:
        """Bidirectional back-off after an OVERSHOOT (operator: "quitting
        too soon if the electrode polarization is too large when there is
        enough current to reduce"; "stopped at -0.644 V … did not try again
        to reach near -0.6").

        Bracket ``[lo_amp, hi_amp]`` where ``lo_cap``'s E_pol is BELOW the
        acceptance band (safe — more current is OK) and ``hi_cap``'s E_pol
        is PAST the far edge (overshoot).  Interpolate the worst-case
        polarization ratio (``|E_pol|/|limit|``, target 1.0 = band centre)
        linearly across the bracket, RE-CAPTURE, and re-bracket on the
        result — landing E_pol INSIDE the band (the precise max-Q_inj
        crossover) instead of stopping on the overshoot.  Port of MATLAB
        ``changeCurrent.m``'s bidirectional inc/dec targeting.

        Bounded at ``ramp.backoff_max_captures`` re-captures and stopped
        early once the bracket narrows below ``fine_step_ua`` (the current
        resolution is 0.1 µA, so a sub-µA bracket is as tight as it gets).
        On exhaustion WITHOUT an in-band capture, the overshoot captures are
        already flagged ``exceeded`` (excluded from max(Q_inj)), so the
        reported maximum falls back to the safe below-band side — the run
        NEVER reports an overshoot as the achieved ceiling.

        Returns the updated ``capture_idx``.
        """
        # ``lo_cap`` may be None when the FIRST tested current already
        # overshot (non-zero-start ramp): the floor is 0 µA, which is safe by
        # construction (0 current → 0 polarization → ratio 0).
        r_lo = 0.0 if lo_cap is None else self._polarization_ratio(lo_cap)
        r_hi = self._polarization_ratio(hi_cap)   # > 1 (overshoot)
        # Narrow the crossover bracket to the 0.1 µA TESTING resolution
        # (operator) — not the 1 µA forward-creep ``fine_step_ua`` — so a low-Q
        # electrode's crossover is resolved finely instead of the ramp giving
        # up at a coarse bracket floor (bumps CH16 accepted 1.0 µA when its
        # crossover was a few µA higher).
        fine = abs(getattr(self.ramp, "test_current_resolution_ua", 0.1)) or 0.1
        # DAMAGED-electrode tracker: the OVERSHOOT is the first (amp, ratio)
        # reference; a later re-capture at a LOWER amplitude with a HIGHER
        # ratio is non-monotonic → the overshoot degraded the surface.
        _prev_bo_amp, _prev_bo_ratio = hi_amp, r_hi
        _prev_bo_valid = self._cap_has_finite_epol(hi_cap)
        self._emit(ExperimentEvent(
            kind="log", session=self.session, run=run,
            message=(f"{config.display_name()}: E_pol overshot the water "
                     f"window at {hi_amp:.1f} µA — backing off between "
                     f"{lo_amp:.1f} and {hi_amp:.1f} µA to land in-band "
                     f"(max-Q_inj crossover).")))
        for _ in range(max(0, int(self.ramp.backoff_max_captures))):
            if self.aborted or (hi_amp - lo_amp) <= fine:
                break
            # Linear interpolation toward ratio = 1.0 (band centre); fall
            # back to bisection when the bracket ratios are degenerate.
            if r_hi > r_lo:
                frac = (1.0 - r_lo) / (r_hi - r_lo)
            else:
                frac = 0.5
            frac = min(0.9, max(0.1, frac))
            target = lo_amp + (hi_amp - lo_amp) * frac
            target = round(target * 10.0) / 10.0        # 0.1 µA resolution
            if target <= lo_amp or target >= hi_amp:
                target = round(0.5 * (lo_amp + hi_amp) * 10.0) / 10.0
                if target <= lo_amp or target >= hi_amp:
                    break                                # can't split further
            pattern = self._pattern_at_amplitude(base_pattern, target)
            cap, limit_hit = self._capture_and_flag(
                config, pattern, run, capture_idx)
            capture_idx += 1
            if cap.status.aborted:
                break
            _r_new = self._polarization_ratio(cap)
            _new_valid = self._cap_has_finite_epol(cap)
            # DAMAGED-electrode detection: E_pol RISING as the current is
            # LOWERED (non-monotonic) means the overshoot degraded the
            # surface (pcc CH05: 60 µA @ 1.93 V → 30 µA @ 4.01 V).  The
            # back-off's monotonic assumption is broken, so regula-falsi would
            # thrash a ruined electrode — stop on the first such rise and
            # accept the tightest SAFE side.  BOTH the reference and the
            # current capture must have a REAL E_pol reading: a bad-class
            # re-capture reports ratio 0.0 (E_pol cleared), which would make a
            # normal 0.97 reading look like a huge non-monotonic rise → a
            # FALSE damage stop (exp_vt_max_anodal CH11: a lone 'open' capture
            # seeded _prev_bo_ratio = 0.00, then the next normal capture's
            # 0.97 tripped "E_pol rose 0.00→0.97").
            _dmg = abs(getattr(self.ramp, "damage_ratio_rise", 0.15))
            if (target < _prev_bo_amp and _prev_bo_valid and _new_valid
                    and _r_new > _prev_bo_ratio + _dmg):
                note = (f"DAMAGED electrode: E_pol rose "
                        f"({_prev_bo_ratio:.2f}→{_r_new:.2f}) as current fell "
                        f"({_prev_bo_amp:.1f}→{target:.1f} µA) — non-monotonic, "
                        f"stopping back-off.")
                cap.status.notes = (
                    (cap.status.notes + " | " if cap.status.notes else "")
                    + note)
                self._emit(ExperimentEvent(
                    kind="log", session=self.session, run=run,
                    message=f"{config.display_name()}: {note}"))
                if (lo_cap is not None
                        and not lo_cap.status.reached_potential_limit):
                    lo_cap.status.reached_potential_limit = True
                return capture_idx
            # Advance the damage reference ONLY from a valid-E_pol capture so a
            # transient bad-class re-capture can't poison the next comparison.
            if _new_valid:
                _prev_bo_amp, _prev_bo_ratio, _prev_bo_valid = (
                    target, _r_new, True)
            if cap.status.exceeded_potential_limit:
                hi_amp, hi_cap, r_hi = target, cap, _r_new
            elif limit_hit:
                # In-band (reached, NOT exceeded) → the crossover, done.
                self._emit(ExperimentEvent(
                    kind="log", session=self.session, run=run,
                    message=(f"{config.display_name()}: landed in-band at "
                             f"{target:.1f} µA — max-Q_inj crossover found.")))
                return capture_idx
            else:
                # Still below the band → raise the safe floor.
                lo_amp, lo_cap, r_lo = target, cap, _r_new
        # Converged WITHOUT a capture landing exactly in the band — the
        # E_pol-vs-current slope near the limit is steeper than the band is
        # wide, so the crossover sits between the last two brackets
        # (< fine_step apart).  Accept the SAFE (below-band) side as the
        # max-Q_inj amplitude — the highest current that stays within the
        # water window — and mark it "reached" so the run terminates on a
        # real condition (operator: the ramp must end on a real reason, and
        # this IS the crossover to current resolution) rather than looking
        # like it stopped short.  ``lo_cap`` is the tightest safe capture:
        # in the common path it's also the last re-capture (so it's the
        # terminal capture the GUI shows); if the bracket was already narrow
        # (0 re-captures) it's the main loop's last-safe capture.
        if lo_cap is not None and not lo_cap.status.reached_potential_limit:
            lo_cap.status.reached_potential_limit = True
            lo_cap.status.notes = (
                (lo_cap.status.notes + " | " if lo_cap.status.notes else "")
                + f"water-window crossover bracketed to "
                  f"[{lo_amp:.1f}, {hi_amp:.1f}] µA (< {fine:.1f} µA "
                  f"resolution); accepted {lo_amp:.1f} µA")
        self._emit(ExperimentEvent(
            kind="log", session=self.session, run=run,
            message=(f"{config.display_name()}: crossover bracketed to "
                     f"[{lo_amp:.1f}, {hi_amp:.1f}] µA — accepting "
                     f"{lo_amp:.1f} µA as the max-Q_inj amplitude.")))
        return capture_idx

    # ------------------------------------------------------------------
    # (`_pattern_at_amplitude` is the shared ExperimentRunner helper —
    #  identical to `scaled()` for a non-zero template, but grows a
    #  ZERO-amplitude template so "starting at 0 µA" ramps.  See base.py.)
    def _seed_scope_scales(self, amp_this_ua: float) -> None:
        """Predictive V/div seed (efficiency) — pre-grow each rescale-managed
        voltage channel's V/div BEFORE the first capture of an amplitude step,
        so the first read is already in-view and the rescale loop converges in
        ONE pass instead of a clip→upscale re-capture (each avoided re-capture
        saves a full averager settle + an N-channel CURVe?).

        Anchored to the PREVIOUS capture's OBSERVED signal half-range (NOT a
        formula — a formula-based per-amplitude pre-set caused V/div jitter,
        see the note in _one_capture), scaled by ``amp_this/amp_prev``, divided
        by the role's fit budget, snapped UP the grid, and applied ONLY when it
        GROWS the current scale.  Because it re-anchors to the real observed
        range each step it can't compound.  Skips the first step of a channel
        (no prior range) and any non-increasing amplitude.  An under-seed just
        clips → the existing clip detector grows it on the next rescale attempt
        (graceful — never worse than today).  I_mon is excluded (sized
        analytically by ``update_imon_vertical_scale``)."""
        scope = self.scope
        prev_amp = getattr(self, "_seed_prev_amp_ua", None)
        prev_hr = getattr(self, "_seed_prev_half_range", {}) or {}
        if (scope is None or not hasattr(scope, "set_channel_scale")
                or not prev_amp or prev_amp <= 0
                or amp_this_ua <= prev_amp):
            return
        ratio = float(amp_this_ua) / float(prev_amp)
        aliases = getattr(scope, "channel_aliases", {}) or {}
        grid = getattr(scope, "_vertical_grid_vpd", None)
        snap = getattr(scope, "_snap_to_grid", None)
        if grid is None or snap is None:
            return
        _DIVS = {"vmon": 3.0, "eact": 4.0, "eret": 4.0}
        for role, divs in _DIVS.items():
            ch = aliases.get(role)
            hr = prev_hr.get(role)
            if not ch or not hr or hr <= 0:
                continue
            try:
                seed = float(snap((hr * ratio) / divs, grid, direction="ceil"))
                cur = (scope._adapt_state.get(ch, {}).get("last_scale")
                       if hasattr(scope, "_adapt_state") else None)
                if cur is None or seed > cur * 1.001:      # only GROW
                    scope.set_channel_scale(ch, seed)
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session,
                        message=(f"  [seed] {role} V/div → {seed*1e3:.1f} mV "
                                 f"(was {(cur or 0)*1e3:.1f}, ×{ratio:.2f} for "
                                 f"{amp_this_ua:.0f} µA) — pre-grow to skip a "
                                 f"clip re-capture")))
            except Exception:
                pass

    def _stash_seed_range(self, cap, amp_signed_ua: float) -> None:
        """Record this capture's OBSERVED per-role half-range + amplitude for
        the next step's :meth:`_seed_scope_scales`.  Trimmed (same 1 %% the
        rescale uses) so a switching transient doesn't inflate the anchor."""
        import numpy as _np
        try:
            self._seed_prev_amp_ua = abs(float(amp_signed_ua))
            _arrs = {"vmon": getattr(cap, "v_mon_v", None),
                     "eact": getattr(cap, "e_act_v", None),
                     "eret": getattr(cap, "e_ret_v", None)}
            hr = getattr(self, "_seed_prev_half_range", None)
            if hr is None:
                hr = self._seed_prev_half_range = {}
            for _role, _a in _arrs.items():
                if _a is None:
                    continue
                _a = _np.asarray(_a, dtype=float)
                _a = _a[_np.isfinite(_a)]
                if _a.size < 4:
                    continue
                _lo = float(_np.percentile(_a, _RESCALE_TRIM_PCT))
                _hi = float(_np.percentile(_a, 100.0 - _RESCALE_TRIM_PCT))
                hr[_role] = abs(_hi - _lo) / 2.0
        except Exception:
            pass

    def _one_capture(self, config: Configuration, pattern: PulsePattern,
                     index: int) -> Capture:
        # Pulsing-window timer (operator: "the number of pulses must be
        # calculated from the elapsed time of starting and stopping the
        # pulsing").  ``_pulse_t0`` is stamped when start_all succeeds,
        # ``_pulse_t1`` when stop_all runs; n_pulses = round(elapsed × rate)
        # is applied to the capture after make_capture below.  Covers the
        # initial acquisition AND every rescale re-capture (all happen
        # while the stim pulses continuously between start_all/stop_all).
        _pulse_t0 = None
        _pulse_t1 = None
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
            # MONOPOLAR commit (config.returns empty) → ONE
            # PS_LoadAllChannels; multipolar is a no-op here (the
            # per-channel PS_LoadChannel loads stand).  MATLAB
            # loadPattern.m parity — without this the device arms
            # nothing and PS_StartStimAllChannels starts but delivers
            # no current (scope NUMACq stays 0).
            self.commit_loaded_channels(config)
            # Use start_all (= PS_StartStimAllChannels) instead of
            # start_channel — single-channel start hits error code 4
            # ("WRONG TRIGGER MODE") on default-mode PlexStim devices.
            # Loaded channels (active + unused-with-zero) fire together;
            # the zero-amp channels deliver no current.
            self.stim.start_all()
            _pulse_t0 = time.monotonic()   # pulsing has begun
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
                # Overall pulse rate (== rate_hz for a non-burst pattern; a
                # burst reaches VT only via LP's _characterize sub-VT, which
                # strips it — but stay burst-correct defensively).
                _rate = self._pulses_per_second(pattern)
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

            # ---- Electrode DC-offset capture → AC-couple (once/channel) ----
            # Operator: "Let's try AC coupled after capturing the offset from
            # DC coupled."  A DC-biased E_ret / E_act (e.g. +248 mV rest with
            # an ±8 mV swing) can't be fine-scaled while DC-coupled (gotcha
            # #13); measure its DC rest potential now (the stim is pulsing —
            # start_all above — so the scope triggers, and E_ret is still
            # DC-coupled from apply_default_scope_view), then AC-couple so the
            # rescale loop below fine-scales the swing.  The offset is threaded
            # into make_capture so the saved trace keeps the absolute level.
            # ONCE per channel — the rest potential is amplitude-independent,
            # so a ramp doesn't re-measure it every step.
            # Gated on the per-channel Coupling dropdowns via
            # ``_electrode_ac_roles`` (empty by default → the DC→AC trick is
            # skipped entirely; E_ret / E_act stay DC-coupled and the rescale
            # loop fits them by coordinated scale + position — faster + no AC
            # settling artifacts; operator: "leave it in DC and adjust the
            # scale and position").  A channel set to "DC + AC" opts its role
            # into the trick.
            _cfg_key = str(getattr(config, "active", config))
            _ac_roles = getattr(self, "_electrode_ac_roles", ())
            if (_ac_roles
                    and getattr(self, "_electrode_offset_config_key", None)
                    != _cfg_key):
                def _offset_recap(_t=_capture_timeout_s):
                    self.scope.settle_one_acquisition(timeout_s=_t)
                    return self.scope.single_capture(timeout_s=_t)
                self.measure_electrode_dc_offsets_and_switch_to_ac(
                    _offset_recap, roles=_ac_roles)
                self._electrode_offset_config_key = _cfg_key

            try:
                # Wait for n_avg FRESH averaged frames at the CURRENT
                # V/div before the FIRST read.  apply_default_scope_view
                # (per channel) and update_imon_vertical_scale (per step)
                # just wrote new vertical scales, but a ``CHx:SCAle`` write
                # does NOT reset the scope's free-running NUMACq — so
                # ``single_capture``'s plain ``NUMACq >= n_avg`` poll would
                # otherwise transfer a STALE frame still averaged at the
                # PREVIOUS channel/step scale (gotcha #33; the
                # entry-anchored ``settle_one_acquisition`` is what fixes
                # it).  No-op on the simulator + a quick trigger-wait in
                # non-AVERAGE modes; on the happy path the wait is the
                # averaging time the first read should have paid anyway.
                # Predictive V/div seed: pre-grow V_mon / E_act / E_ret from
                # the previous step's OBSERVED range × the amplitude ratio so
                # this first capture is already in-view (the rescale loop then
                # converges in one pass instead of a clip→upscale re-capture).
                self._seed_scope_scales(abs(_amp_signed))
                self.scope.settle_one_acquisition(timeout_s=_capture_timeout_s)
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

            # (No trigger/pulse-alignment check — the I_mon edge position is
            # not a reliable time-axis signal with the digital-sync trigger,
            # and at small phase widths there's a real V_mon/I_mon edge skew;
            # see check_trigger_alignment's note.  Operator: "Do not have
            # warnings about the trigger warning.")

            # ----- 2b. Iterative fit-the-view loop ---------------------------
            # MOVED to ExperimentRunner.rescale_to_fit (base.py) so every
            # runner (VT / PS / SP / LP) shares the SAME coarse/fine
            # scaling + positioning logic (operator request).  VT supplies
            # the settle-then-single_capture recapture (gotcha #40 — a
            # CHx:SCAle write does NOT reset NUMACq, so single_capture's
            # plain poll would read a STALE averaged frame without the
            # entry-anchored settle first).
            # Stim keeps RUNNING across the whole loop; the outer finally
            # below fires the single stop_all (gotcha #10).

            def _recapture(_t):
                self._emit(ExperimentEvent(
                    kind="log", session=self.session,
                    message=("  settling the averager at the "
                             "new V/div before re-capture…")))
                self.scope.settle_one_acquisition(timeout_s=_t)
                return self.scope.single_capture(timeout_s=_t)

            acq = self.rescale_to_fit(
                acq, pattern=pattern,
                recapture=_recapture,
                timeout_s=_capture_timeout_s,
                context=(f"at {_amp_signed:+.1f} µA "
                         f"(capture #{index + 1})"),
            )
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
            _pulse_t1 = time.monotonic()   # pulsing has ended

        # ----- 3+4. Demux channels and convert to physical units ---------
        # make_capture handles channel_aliases lookup, nominal scaling, and
        # applies readback calibration (gain/offset on I_mon, vmon_v_per_v
        # correction) when a calibration record exists for this stimulator.
        # Apply operator waveform smoothing (if enabled) to the acquired
        # arrays BEFORE building the Capture, so the plot, metrics, and
        # saved .npz all use the smoothed waveform.  (After the rescale
        # loop, so the scaling decision is unaffected.)
        self._smooth_acquisition(acq)
        cap = make_capture(index, pattern, acq, self.scope, self.stim,
                           cal=self.cal, channel=config.active,
                           electrode_dc_offsets=self._electrode_dc_offset)
        # Record this capture's observed per-role range for the NEXT step's
        # predictive V/div seed (efficiency; anchored to the real signal so
        # the seed can't compound).
        self._stash_seed_range(cap, _amp_signed)
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
        # Thread the operator-configurable failure-detection thresholds so the
        # STORED response_class (and the ramp's stop decision) honour the
        # minimum-current gate + manual Z thresholds (auto mode ⇒ None ⇒ the
        # built-in constants).
        _auto = getattr(self.ramp, "bad_response_auto", True)
        compute_metrics(
            cap, surface_area_um2=self.surface_area_um2,
            polarization_source=self.polarization_source,
            # The min-current gate improves CLASSIFICATION correctness (low
            # current → the open/broken heuristics are unreliable) regardless of
            # whether the early STOP is enabled — thread it UNCONDITIONALLY so
            # disabling `stop_on_bad_response` doesn't silently mislabel
            # functional low-current captures as broken in the saved metrics /
            # POLARIS (audit finding).  The stop decision itself is gated
            # separately, at the run-loop bad-response break.
            failure_min_current_ua=getattr(
                self.ramp, "bad_response_min_current_ua", 0.0),
            failure_open_z_mohm=(
                None if _auto
                else getattr(self.ramp, "bad_response_open_z_kohm", 50.0) / 1000.0),
            failure_broken_z_mohm=(
                None if _auto
                else getattr(self.ramp, "bad_response_broken_z_kohm", 200.0) / 1000.0),
            depol_us=self._epol_depol_us(),
        )
        # Number of pulses delivered for THIS capture = MEASURED pulsing
        # elapsed time (start_all → stop_all, covering the initial
        # acquisition + every rescale re-capture) × the pulse rate (operator:
        # "the number of pulses correlates with the DURATION of the pulsing,
        # not the trigger/average count").  MUST be set AFTER compute_metrics
        # — compute_metrics builds a FRESH CaptureMetrics and replaces
        # cap.metrics, so setting n_pulses before it silently WIPED the
        # measured value → _record_capture_dose then fell back to the
        # averaging count (the exact symptom).  Left NaN when the window
        # wasn't timed (early abort) or no real time elapsed (simulator);
        # _record_capture_dose then falls back to the averaging count.
        if _pulse_t0 is not None and _pulse_t1 is not None:
            _elapsed = _pulse_t1 - _pulse_t0
            # OVERALL pulse rate (a burst delivers pulses_per_burst pulses per
            # burst period, NOT rate_hz).  == rate_hz for a non-burst pattern.
            _rate = self._pulses_per_second(pattern)
            if _elapsed > 0 and _rate > 0:
                cap.metrics.n_pulses = float(round(_elapsed * _rate))
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
        """Has E_pol of either active or return REACHED the water window?

        Port of MATLAB ``getAcuteWaveformData2.m`` lines 289-301, where
        ``polarization_tolerance_v`` is the operator's **acceptable
        difference** (``2 × ACCEPTABLE_EMC_RANGE`` = 0.020 V) — the
        half-width of an acceptance BAND CENTERED on the limit, NOT an
        outward grace band::

            acceptLimitMin = limitPotential - tol     # -0.82 for a -0.80 limit
            acceptLimitMax = limitPotential + tol     # -0.78
            isPotentialLimitReached = acceptLimitMin <= E_mc <= acceptLimitMax

        The limit is "reached" when E_pol lands WITHIN ±tol of the
        limit. A cathodic ramp climbing from above (less negative)
        first enters the band at ``acceptLimitMax = limit + tol``
        (-0.78 V), so that is the trip threshold.

        The old code used ``cathodic_limit - tol`` (-0.82 V) — that is
        the band's FAR edge (``acceptLimitMin``), not where a climbing
        ramp enters it. The result (operator: "Emc reached ~-0.8 V by
        capture #4, but it kept increasing and stopped at -0.843 V by
        capture #10"): the secant aims the ramp AT the limit
        (``aim_ratio = 1.0``), reached -0.8 by cap #4, then had to creep
        the extra 0.02 V to -0.82 by tiny fine-steps — and because the
        polarization saturates (concave-down) near the limit, each
        ~10 µA step moves E_pol only ~0.01 V, so it burned ~6 captures
        and overshot the water window to -0.843 V (electrode damage).

        For a ONE-WAY ramp (the Python runner only increases current; it
        cannot back off like MATLAB's bidirectional inc/dec targeting),
        ``v <= acceptLimitMax`` also catches the case where a step
        overshoots PAST the band (``v < acceptLimitMin``) — so the ramp
        always stops the first capture at or beyond the near edge rather
        than running away past the limit.
        """
        tol = abs(self.polarization_tolerance_v)
        cath = self.cathodic_limit_v + tol   # acceptLimitMax: -0.80 + 0.02 = -0.78
        anod = self.anodic_limit_v - tol     # acceptLimitMin: +0.60 - 0.02 = +0.58
        finite = [v for series in (cap.metrics.polarization_per_phase_v,
                                   cap.metrics.return_polarization_per_phase_v)
                  for v in series if np.isfinite(v)]
        if finite:
            return any((v <= cath or v >= anod) for v in finite)
        # SAFETY BACKSTOP (operator: "why are CH02/CH03 still incremented despite
        # GREATLY exceeding the potential limits?").  A bad-class capture
        # (open/broken/capacitive) has its per-phase E_pol CLEARED by
        # compute_metrics, so the check above iterates an EMPTY list and is
        # BLIND — yet a BROKEN electrode still has REAL, large polarization that
        # can grossly exceed the water window (CH03: V_mon −1.31 V vs a −0.60 V
        # limit — 2.2×).  When no E_pol is available, fall back to the RAW V_mon
        # signed excursions vs the limits so the ramp STILL stops.  A broken
        # electrode has little/no ohmic access drop (V_mon ≈ E_pol), and the
        # backstop is applied ONLY when E_pol is unavailable, so it NEVER
        # false-trips a normal electrode's IR drop (those use the E_pol check).
        #
        # GATED ON ``stop_on_bad_response``: the raw-V_mon backstop IS the
        # bad-class water-window check, so it is coupled to the operator's
        # master bad-response toggle.  When bad-response detection is OFF the
        # operator has chosen to IGNORE bad-class captures and ramp to
        # voltage-compliance / max current (gotcha #178 fallback), so a
        # bad-class (empty-E_pol) capture must NOT trip a stop/back-off here
        # either — otherwise a LONE TRANSIENT misclassification derails the
        # ramp (exp_vt_max_anodal CH11: one 'open' capture at 917 µA amid
        # normal +0.777 V neighbours cleared E_pol → this backstop fired
        # ``exceeded`` → a spurious back-off → false "DAMAGED electrode" stop
        # at 916 µA, below both the band and the 1000 µA max).
        if not getattr(self.ramp, "stop_on_bad_response", True):
            return False
        return self._raw_vmon_exceeds_window(cap, cath, anod)

    def _raw_vmon_exceeds_window(self, cap: Capture,
                                 cath: float, anod: float) -> bool:
        """Raw-V_mon water-window backstop for a bad-class capture whose
        per-phase E_pol was cleared — the ONLY thing standing between a broken
        electrode and an unbounded over-ramp."""
        v = getattr(cap, "v_mon_v", None)
        if v is not None and getattr(v, "size", 0) >= 4:
            arr = np.asarray(v, dtype=float)
            arr = arr[np.isfinite(arr)]
            if arr.size >= 4:
                exc = arr - float(np.median(arr))   # V_mon idles ≈ 0 → median baseline
                if float(np.min(exc)) <= cath or float(np.max(exc)) >= anod:
                    return True
        # Trace trimmed (e.g. LP snapshot) → the unsigned driving voltage
        # (max|V_mon|) is a conservative last resort.
        dv = getattr(cap.metrics, "driving_voltage_v", float("nan"))
        return bool(np.isfinite(dv) and dv >= min(abs(cath), abs(anod)))

    def _potential_limit_exceeded(self, cap: Capture) -> bool:
        """Did E_pol OVERSHOOT the acceptance band — past its FAR edge?

        Distinct from :meth:`_potential_limit_hit` ("reached" = landed
        anywhere in / beyond the near edge).  "Exceeded" is specifically
        ``|E_pol| > |limit| + tol`` — the ramp stepped past the window
        instead of landing inside it (operator: "I saw a channel stop at
        -0.644 V … and said limit reached" — for a −0.60 limit that is
        0.024 V PAST the −0.62 far edge, i.e. exceeded, and is exactly the
        overshoot a bidirectional back-off should correct).  Surfaced as
        the separate "Limit exceeded?" metric so a clean stop (in-band)
        reads differently from an overshoot.
        """
        tol = abs(self.polarization_tolerance_v)
        cath_far = self.cathodic_limit_v - tol   # -0.62 for a -0.60 limit
        anod_far = self.anodic_limit_v + tol     # +0.62
        finite = [v for series in (cap.metrics.polarization_per_phase_v,
                                   cap.metrics.return_polarization_per_phase_v)
                  for v in series if np.isfinite(v)]
        if finite:
            return any((v < cath_far or v > anod_far) for v in finite)
        # Bad-class capture (E_pol cleared) that OVERSHOT the far edge — same
        # raw-V_mon backstop as _potential_limit_hit (so max_q_inj EXCLUDES a
        # broken over-ramp and the "Limit exceeded?" flag is honest).  Gated on
        # ``stop_on_bad_response`` for the same reason (see _potential_limit_hit)
        # — a lone transient bad-class capture must not force a back-off when
        # the operator has disabled bad-response detection.
        if not getattr(self.ramp, "stop_on_bad_response", True):
            return False
        return self._raw_vmon_exceeds_window(cap, cath_far, anod_far)

    def _polarization_ratio(self, cap: Capture) -> float:
        """Worst-case ``|E_pol| / |limit|`` across all phases of one capture.

        ≥ 1.0 means a phase has crossed the water window. Used both as
        the trigger for fine stepping (``increment`` strategy) and as
        the dependent variable in the adaptive regression.
        """
        worst = 0.0
        # Guard each divisor against a 0 V half-window (a nonsensical config,
        # but a ZeroDivisionError here would crash the ramp mid-run) — same
        # guard the inner loops of `_growth_ratio` already apply.
        cath = abs(self.cathodic_limit_v)
        anod = abs(self.anodic_limit_v)
        for series in (cap.metrics.polarization_per_phase_v,
                       cap.metrics.return_polarization_per_phase_v):
            for v in series:
                if not np.isfinite(v):
                    continue
                if v < 0:
                    if cath >= 1e-9:
                        worst = max(worst, abs(v) / cath)
                elif anod >= 1e-9:
                    worst = max(worst, v / anod)
        return worst

    @staticmethod
    def _cap_has_finite_epol(cap: Capture) -> bool:
        """True iff the capture has at least one finite per-phase E_pol
        (active OR return).  A bad-class capture clears its E_pol, so
        :meth:`_polarization_ratio` returns 0.0 for it — indistinguishable
        from a genuine 0 V polarization.  The back-off's damage detector
        must NOT compare against such a capture (a NaN→0.0 seed makes a real
        0.97 reading look like a huge "E_pol rose from 0.00" → false DAMAGED
        stop — exp_vt_max_anodal CH11)."""
        for series in (cap.metrics.polarization_per_phase_v,
                       cap.metrics.return_polarization_per_phase_v):
            for v in (series or []):
                if np.isfinite(v):
                    return True
        return False

    def _worst_epol_ratio(self, captures: List[Capture]) -> float:
        """Worst-case ``|E_pol| / |limit|`` of the LATEST capture, clamped to
        [0, 1] — the MEASURED proximity signal driving the growth cap (§2 of
        the pcc safety fix).  0.0 when there are no captures or neither limit
        is meaningfully non-zero (a 0 V window is nonsensical → don't divide
        by ~0)."""
        if not captures:
            return 0.0
        if (abs(self.cathodic_limit_v) < 1e-9
                and abs(self.anodic_limit_v) < 1e-9):
            return 0.0
        return float(min(self._polarization_ratio(captures[-1]), 1.0))

    def _growth_ratio(self, captures: List[Capture]) -> float:
        """BASELINE-SUBTRACTED worst-case E_pol proximity, in [0, 1] — the
        signal that drives the growth cap's tier + √-loosening.

        The raw ``_worst_epol_ratio`` includes the electrode's REST
        polarization (the |E_pol| at ~0 µA), a fixed offset that does NOT grow
        with current.  On a high-capacity electrode that offset can be a large
        fraction of the window (exp_vt_max_check: ~0.16 V ≈ 28 % of the −0.6 V
        limit BEFORE any current), so the raw ratio sits in the conservative
        1.7× tier for the ENTIRE low-current climb and the ramp creeps to the
        ceiling over ~15 captures even though the CURRENT-dependent polarization
        is still ~0.

        The water window trips when the ABSOLUTE |E_pol| reaches |limit|, i.e.
        when the current-dependent rise Δ = |E_pol| − rest reaches
        (|limit| − rest).  So Δ / (|limit| − rest) is the correct "how close is
        the current driving me to the limit" signal, and it is 0 while the
        polarization is still baseline-dominated (→ blind/√-loosening → fast
        climb) and 1 exactly at the trip (→ tight tier).  Near-limit behaviour
        is UNCHANGED (as |E_pol| → |limit|, this → 1, same as the raw ratio).

        The per-phase ``rest`` is the MIN |E_pol| seen over the run (the lowest-
        current, i.e. first, capture for a monotone ramp).  Using the min errs
        SAFE: a non-zero-start ramp or an upward-drifting rest over-subtracts
        (larger apparent Δ → higher ratio → TIGHTER cap), never the reverse.
        Same √-bound safety envelope as ``_worst_epol_ratio`` (safe for
        E_pol ∝ ampⁿ, n ≤ 2, backstopped by the per-capture band stop +
        back-off for a pathological n > 2 — exactly as the raw path already is).
        """
        if not captures:
            return 0.0
        if (abs(self.cathodic_limit_v) < 1e-9
                and abs(self.anodic_limit_v) < 1e-9):
            return 0.0
        latest = getattr(captures[-1].metrics,
                         "polarization_per_phase_v", None) or []
        if not latest:
            return float(min(self._worst_epol_ratio(captures), 1.0))
        n = len(latest)
        raw = float(min(self._worst_epol_ratio(captures), 1.0))
        # ``rest`` = per-phase |E_pol| at the ~0 µA BASELINE capture — the
        # GENUINE rest polarization.  REQUIRE a real 0 µA capture: on a
        # non-zero-start ramp the FIRST capture already carries current-dependent
        # polarization (a low-Q electrode reads ~0.15 V at 1 µA), so treating it
        # as "rest" would subtract REAL signal and unleash the exact concave-up
        # fling the growth cap prevents (a min-over-captures baseline made
        # test_growth_cap_prevents_high_impedance_fling fling 1 → 6 µA).  No
        # 0 µA baseline ⇒ raw ratio (behaviour byte-identical to before).
        base_cap = None
        for c in captures:
            try:
                a = abs(float(c.pattern.excitation_phase.amplitude_ua))
            except (AttributeError, TypeError):
                continue
            if a <= self._BASELINE_MAX_AMP_UA:
                base_cap = c
                break
        if base_cap is None:
            return raw
        base_pol = getattr(base_cap.metrics,
                           "polarization_per_phase_v", None) or []
        rest = [0.0] * n
        for i in range(min(n, len(base_pol))):
            v = base_pol[i]
            if v is not None and np.isfinite(v):
                rest[i] = abs(float(v))
        # Only DIVERGE from the raw ratio when the REST polarization is a
        # MEANINGFUL fraction of the window (the high-capacity electrode this
        # speedup targets — exp_vt_max_check rest ≈ 0.27 of the limit).  For a
        # low-baseline electrode the subtracted ratio ≈ raw anyway, and a hair
        # of difference can tip a step across the 0.20 tier boundary and change
        # the trajectory (→ measurable overshoot regression on quadratic
        # electrodes).  So below the threshold return the raw ratio EXACTLY,
        # keeping the √-loosening safety envelope + every ramp test unchanged.
        _significant = False
        for i in range(n):
            v = latest[i]
            if v is None or not np.isfinite(v):
                continue
            lim = (abs(self.cathodic_limit_v) if float(v) < 0
                   else abs(self.anodic_limit_v))
            b = rest[i] if np.isfinite(rest[i]) else 0.0
            if lim > 1e-9 and b / lim > self._BASELINE_MIN_FRAC:
                _significant = True
                break
        if not _significant:
            return raw
        worst = 0.0
        for i in range(n):
            v = latest[i]
            if v is None or not np.isfinite(v):
                continue
            lim = (abs(self.cathodic_limit_v) if float(v) < 0
                   else abs(self.anodic_limit_v))
            if lim < 1e-9:
                continue
            b = rest[i] if np.isfinite(rest[i]) else 0.0
            head = lim - b
            if head <= 1e-6:
                # rest already at/past the limit — the electrode is in trouble;
                # fall back to the raw (high) ratio so the cap stays tight.
                r = abs(float(v)) / lim
            else:
                r = (abs(float(v)) - b) / head
            worst = max(worst, r)
        return float(min(max(worst, 0.0), 1.0))

    def _epol_concave_down(self, captures: List[Capture]) -> bool:
        """Is the worst-case E_pol-vs-amplitude trajectory DECELERATING?

        Reads the last three captures' ``_polarization_ratio`` (worst
        |E_pol|/|limit|, a monotone proxy for |E_pol| at a fixed window)
        against their amplitudes and returns ``True`` when the later
        secant slope is meaningfully SHALLOWER than the earlier one — the
        saturating (concave-down) SIROF signature.  Such an electrode
        UNDER-projects its crossover (the local secant undershoots) and
        is inherently safe, so the caller loosens the E_pol growth cap.

        A concave-UP (accelerating) electrode — the pcc fling case —
        shows the OPPOSITE (later slope steeper) and returns ``False``,
        keeping the tight tiered cap.  Because the concavity is MEASURED
        from real captures, a dangerous fling can never disguise itself
        as decelerating to unlock the loosening.
        """
        pts = []
        for c in captures[-3:]:
            try:
                a = abs(c.pattern.excitation_phase.amplitude_ua)
            except (AttributeError, TypeError):
                continue
            r = self._polarization_ratio(c)
            if a > 0 and np.isfinite(r) and r > 0:
                pts.append((a, r))
        if len(pts) < 3:
            return False
        pts.sort()
        (a0, r0), (a1, r1), (a2, r2) = pts[-3:]
        if a1 <= a0 or a2 <= a1:
            return False
        s1 = (r1 - r0) / (a1 - a0)
        s2 = (r2 - r1) / (a2 - a1)
        # Decelerating when the later slope is at least 10 % shallower —
        # a margin above per-capture measurement jitter so a flat noisy
        # patch doesn't falsely read as saturation.
        return s2 < s1 * 0.9

    # E_pol SATURATION (plateau-near-limit) early stop.  See
    # ``_epol_plateaued_near_limit`` — bounds the near-crossover fine creep.
    _SAT_WINDOW_N = 5           # captures needed to confirm the plateau (trend)
    _SAT_FAST_N = 3             # FAST dead-flat tier: this many DEAD-flat
    #                             near-limit captures stop immediately
    _SAT_FLAT_BAND = 0.02       # fast tier: max-min of the window ratios < this
    #                             (unambiguous plateau; ≈ the settled noise)
    _SAT_MIN_RATIO = 0.90       # robust |E_pol|/|limit| must be ≥ this (near lim)
    _SAT_RISE_RATIO = 0.015     # robust ratio rose < this across window (flat)

    def _epol_plateaued_near_limit(
            self, captures: List[Capture]) -> Optional[Capture]:
        """Detect E_pol SATURATION near the water window and return the capture
        to report as the electrode's max charge injection — or ``None``.

        The electrode has reached its charge-injection capacity: worst-case
        ``|E_pol|`` is FLAT (not climbing with current) just BELOW the
        acceptance band, and the capture-to-capture NOISE (±10-15 mV on the
        bench) means the single-sample band stop only fires on a lucky spike.
        So the ramp CREEPS ~30 captures in ever-finer distance-table steps
        (exp_vt_max_check CH14: 720 → 779 µA; CH04: 424 → 461) until noise
        happens to cross the near edge — the gotcha #155 measurement-noise
        limit.  MATLAB ``changeCurrent.m`` avoids this by GROWING its step when
        under-limit + converging on an amplitude repeat; PULSAR's 0.1 µA
        distance-table steps are all distinct, so nothing stops the creep.

        Fires ONLY when, over the last ``_SAT_WINDOW_N`` normal, not-yet-reached
        captures with a NON-DECREASING amplitude:
          * the ROBUST (median) worst ``|E_pol|/|limit|`` is ≥ ``_SAT_MIN_RATIO``
            (NEAR the limit — never a mid-climb plateau), AND
          * that robust ratio ROSE by < ``_SAT_RISE_RATIO`` across the window
            (SATURATED — a still-climbing electrode shows a real rise and is
            spared, so a responsive electrode is never stopped by more than the
            tiny plateau threshold).
        The median rejects the noise spikes.  A SIROF that truly plateaus near
        the limit IS at its max charge injection, so this reports the
        CONSERVATIVE saturation amplitude (a few % below the eventual noise-spike
        crossing, and the electrode will not polarize further) instead of
        creeping for it.  Returns the window capture CLOSEST to the limit (max
        ratio) so its Q_inj is the reported max; the run loop marks it reached
        and stops.

        NOTE the flat-near-limit RATIO — not the amplitude climb — is the
        saturation signal.  An early version gated on a large amplitude climb
        (≥ 12 µA over the window), which BLOCKED the stop on a SATURATED
        electrode whose distance-table step had shrunk to the 0.3 µA floor
        (exp_vt_max_check CH11: amp crept 430 → 437 µA in 0.3 µA steps over 23
        captures while E_pol sat pinned flat at 0.578).  The amplitude only
        needs to be NON-DECREASING (a forward creep / stall — not a back-off
        decrement); a frozen or tiny-step creep near the limit IS convergence
        and SHOULD stop.
        """
        n = int(self._SAT_WINDOW_N)
        _res = abs(getattr(self.ramp, "test_current_resolution_ua", 0.1)) or 0.1
        caps = [c for c in captures
                if not c.status.aborted
                and getattr(c.metrics, "response_class", "normal") == "normal"
                and abs(c.pattern.excitation_phase.amplitude_ua) > 0.0
                and not c.status.reached_potential_limit
                and not c.status.exceeded_potential_limit]

        def _near_limit_ratios(win):
            """Return the window's ratios if it is a valid near-limit,
            non-decreasing-amplitude window, else None."""
            amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in win]
            if amps[-1] < amps[0] - _res:
                return None                      # amplitude decreased → not a
                #                                  forward saturation creep
            rr = [self._polarization_ratio(c) for c in win]
            if any((not np.isfinite(r)) or r <= 0 for r in rr):
                return None
            if float(np.median(rr)) < self._SAT_MIN_RATIO:
                return None                      # not near the limit yet
            return rr

        # FAST dead-flat tier (operator #3: faster near-limit approach) — a
        # short window of DEAD-FLAT near-limit captures (whole-window span <
        # _SAT_FLAT_BAND) is an unambiguous plateau, so stop WITHOUT waiting for
        # the full trend window (CH06's 0.936× flat: fires ~2 captures sooner).
        # Robust for the short window because it keys on the FULL SPAN (max-min),
        # not a 2-sample trend, so a single noise spike widens the span and a
        # noisy-CLIMBING electrode (CH07) falls through to the robust tier below.
        # SAFE re: overshoot — firing earlier lands at a LOWER amplitude.
        _fast = int(self._SAT_FAST_N)
        if _fast >= 2 and len(caps) >= _fast:
            r_fast = _near_limit_ratios(caps[-_fast:])
            # DEAD-flat: tight whole-window span AND a non-rising last-vs-first
            # trend (so a still-CLIMBING electrode — even one taking fine steps
            # near the top — is excluded and reaches the band normally; only a
            # genuine plateau fires early).
            if (r_fast is not None
                    and (max(r_fast) - min(r_fast)) < self._SAT_FLAT_BAND
                    and (r_fast[-1] - r_fast[0]) < self._SAT_RISE_RATIO):
                return max(caps[-_fast:], key=self._polarization_ratio)

        # ROBUST trend tier: the full _SAT_WINDOW_N window, median trend flat.
        if len(caps) < n:
            return None
        win = caps[-n:]
        ratios = _near_limit_ratios(win)
        if ratios is None:
            return None
        half = n // 2
        rise = float(np.median(ratios[-half:]) - np.median(ratios[:half]))
        if rise >= self._SAT_RISE_RATIO:
            return None                          # still climbing → let it climb
        return max(win, key=self._polarization_ratio)

    # ------------------------------------------------------------------
    # Per-excursion trajectories — consider ALL electrode-polarization
    # locations when predicting the max-charge ceiling.
    # ------------------------------------------------------------------
    # Excursions whose latest magnitude is below this are treated as
    # "near zero / not heading for a limit" and skipped (no informative
    # direction). 5 mV ≈ the per-capture baseline noise floor.
    _EXC_MIN_V = 0.005

    # R1 — the current-driven polarization SIGNAL is "emerged" once any
    # excursion's swing from its 0 µA rest anchor exceeds this.  50 mV is
    # comfortably above the ~5 mV averaged-capture noise + rest-potential
    # drift, and far below the ±600 mV water window — so a geometric ×factor
    # jump taken from a still-buried (< 50 mV, i.e. < ~8 % of the limit) state
    # stays well within the window even if the electrode responded linearly
    # (SIROF is sub-linear/saturating, so it stays safer still).
    _SIGNAL_FLOOR_V = 0.05

    # ``_growth_ratio`` baseline-subtraction engages only when the electrode's
    # REST polarization exceeds this fraction of its water-window limit.  A
    # high-capacity electrode (exp_vt_max_check rest ≈ 0.27) unlocks the faster
    # low-current climb; a low-baseline electrode stays on the exact raw ratio
    # so its √-loosening safety envelope + every ramp test are unchanged.
    _BASELINE_MIN_FRAC = 0.10

    # A capture at or below this amplitude is the genuine ~0 µA rest baseline
    # for ``_growth_ratio``.  Only a real 0 µA capture (VT-max-from-0) supplies
    # a trustworthy rest — a non-zero-start ramp's first capture already carries
    # current-dependent polarization, so it does NOT qualify.
    _BASELINE_MAX_AMP_UA = 0.5

    # R3 — once the worst excursion's |E_pol| is within this fraction of its
    # limit, switch from the coarse secant/regression to the decisive
    # near-edge APPROACH step (``_approach_step``) so the final approach lands
    # IN the acceptance band in one move instead of geometric fine-step creep.
    _APPROACH_RATIO = 0.85

    # Fractional safety probe: the informative fraction of a big predicted jump
    # to step FIRST (operator: "jump ~30 % of the way, observe, then commit").
    _PROBE_FRACTION = 0.30

    def _excursion_series(
        self, captures: List[Capture]
    ) -> "dict[tuple, list]":
        """Group every electrode-polarization location into its own
        amplitude→voltage trajectory.

        Port of the MATLAB ``changeCurrent_Fit.m`` triple loop
        (``excursion_idx`` × ``electrode_idx`` × ``limit_idx``): the
        max-charge ceiling is bounded by whichever polarization
        location — on EITHER electrode, at ANY phase — reaches its
        water-window limit FIRST, so the predictor must track each one
        independently rather than collapsing them into a single
        worst-case scalar (which loses the per-location trajectory and
        mispredicts when the dominant excursion switches as current
        rises).

        Returns ``{(electrode, phase_idx): [(amp_ua, E_pol_v), …]}`` —
        one climbing trajectory per excursion location across the
        accumulated captures. ``electrode`` is ``"active"`` or
        ``"return"``; ``phase_idx`` indexes the per-phase polarization
        list. Aborted captures and non-finite samples are dropped.
        """
        series: "dict[tuple, list]" = {}
        for c in captures:
            if c.status.aborted:
                continue
            try:
                a = abs(c.pattern.excitation_phase.amplitude_ua)
            except Exception:
                continue
            # R2 — INCLUDE the 0 µA baseline capture as each excursion's
            # (0, rest-potential) anchor.  A SIROF electrode idles at its rest
            # potential (~+0.2 V vs Pt), so at low current every phase reads the
            # rest (no polarization signal yet).  Anchoring each excursion's fit
            # at the measured (0 µA, rest) point (a) gives the regression the
            # true zero-current intercept for free — no explicit per-excursion
            # baseline subtraction needed (the linear crossover is invariant to
            # a constant offset, but the anchor stabilizes the fit and fixes the
            # DIRECTION detection below), and (b) removes the ~42 % low-amplitude
            # "anodic dead-zone" where a still-positive-but-descending cathodic
            # phase was mis-assigned to the anodic limit.  The anchor is flat
            # (rest ≈ rest) until real polarization emerges, so it never triggers
            # a premature crossover.  Was ``if a <= 0: continue`` (dropped the
            # baseline); ``a`` is ``abs(...)`` so ``a < 0`` never fires — the
            # guard just documents that a genuine negative would be dropped.
            if a < 0:
                continue
            for electrode, arr in (
                ("active", c.metrics.polarization_per_phase_v),
                ("return", c.metrics.return_polarization_per_phase_v),
            ):
                for k, v in enumerate(arr or []):
                    if v is None or not np.isfinite(v):
                        continue
                    series.setdefault((electrode, k), []).append((a, float(v)))
        # Keep each trajectory amplitude-sorted so "first" = lowest-amp
        # (the rest anchor) and "last" = highest-amp for the direction check.
        for k in series:
            series[k].sort(key=lambda p: p[0])
        return series

    def _excursion_limit_for(self, volt: float) -> Optional[float]:
        """The SIGNED water-window limit this excursion is approaching.

        A cathodic (negative) excursion heads for ``cathodic_limit_v``;
        an anodic (positive) one for ``anodic_limit_v``. Returns
        ``None`` for a near-zero excursion (no clear direction), so a
        flat phase never contributes a spurious crossover.

        Solving each excursion against ONLY its sign-matching limit
        (rather than MATLAB's both-then-filter) avoids a wrong-limit
        root landing at a small positive amplitude and dragging the
        ``min`` ceiling down to a needless fine-step crawl.
        """
        if volt <= -self._EXC_MIN_V:
            return self.cathodic_limit_v
        if volt >= self._EXC_MIN_V:
            return self.anodic_limit_v
        return None

    def _solve_excursion_crossing(
        self, x: np.ndarray, y: np.ndarray, limit: float
    ) -> Optional[float]:
        """Fit one excursion's E_pol-vs-amplitude and solve for the
        amplitude at which it reaches ``limit``.

        Tries ``poly1`` → ``poly2`` → ``poly3`` → ``exp1`` and returns the
        first that clears its R² gate with a usable crossover — preferring
        the lowest order, exactly as MATLAB ``changeCurrent_Fit.m``
        (``FIT_CELL = {'poly1','poly2','poly3','exp1'}``) breaks on the first
        good fit.  ``exp1`` is tried LAST (only when no polynomial fits) so it
        rescues a genuinely exponential E_pol-vs-current curve the polys
        missed, without ever overriding a good polynomial fit.
        """
        for fit_type in ("poly1", "poly2", "poly3", "exp1"):
            t = self._fit_and_solve(fit_type, x, y, target=limit)
            if t is not None and np.isfinite(t):
                return t
        return None

    # ------------------------------------------------------------------
    # R1 / R3 helpers — signal emergence, band near-edge, decisive approach
    # ------------------------------------------------------------------
    def _signal_emerged(self, captures: List[Capture]) -> bool:
        """Has the current-driven polarization risen above the rest-potential
        noise floor on ANY excursion?

        With the 0 µA rest anchor included in ``_excursion_series`` (R2), each
        excursion's swing from its lowest-amplitude (rest) point to its latest
        point measures the current-driven polarization directly.  Returns True
        once that swing exceeds ``_SIGNAL_FLOOR_V`` for any excursion — the cue
        for ``_next_step`` to stop the geometric search and start projecting.
        """
        for pts in self._excursion_series(captures).values():
            if len(pts) < 2:
                continue
            anchor = pts[0][1]        # lowest-amplitude point ≈ rest potential
            latest = pts[-1][1]
            if abs(latest - anchor) >= self._SIGNAL_FLOOR_V:
                return True
        return False

    def _band_near_edge(self, limit: float) -> float:
        """The acceptance band's NEAR edge for a ramp approaching ``limit``
        from the safe side (operator/gotcha #61: "reached" fires when E_pol
        lands within ``±tol`` of the limit, and a one-way ramp meets the band
        at its near edge FIRST).  For the cathodic (negative) limit the near
        edge is ``limit + tol`` (e.g. −0.58 for −0.60); for the anodic
        (positive) limit it is ``limit − tol``.  Aiming projections here
        (instead of the band CENTRE = the bare limit) is (a) more conservative
        — a smaller projected crossover, so the ramp lands at the near edge
        rather than stepping toward the centre — and (b) the key to killing the
        near-crossover fine-step creep."""
        tol = abs(self.polarization_tolerance_v)
        return limit + tol if limit < 0 else limit - tol

    def _approach_step(self, captures: List[Capture],
                       current_amp_ua: float) -> Optional[float]:
        """DECISIVE near-crossover step (R3) — kills the fine-step creep.

        Once an excursion is within ``_APPROACH_RATIO`` of its limit but not
        yet in-band, project — from the LOCAL slope (last two points, the
        freshest estimate) — the amplitude at which it reaches its band NEAR
        EDGE, and step there in ONE move.  Returns the MIN delta across the
        close excursions (the first to enter its band governs), or ``None`` if
        no excursion is close yet (caller uses the normal secant/regression).

        This replaces the old behaviour where, near the crossover, the
        secant/regression projected a target ≈ the current amplitude, so the
        delta floored to ``fine_step_ua`` (0.1-1 µA) and the ramp crept up one
        fine step at a time for 4-6 captures before finally landing in the band
        (operator: "stopped at −0.644 V … did not try again to reach near
        −0.6").  By projecting the NEAR EDGE from the local slope the step is
        sized to actually enter the band; on a genuinely asymptotic (very
        shallow) approach the projected step is large, so the per-capture band
        stop / back-off take over instead of a creep.  Undershoot-biased: the
        near edge is the closest in-band point, and the local slope of the
        concave-down SIROF response under-projects the true crossover.
        """
        best: Optional[float] = None
        best_cross: Optional[float] = None
        for pts in self._excursion_series(captures).values():
            if len(pts) < 2:
                continue
            (a0, v0), (a1, v1) = pts[-2], pts[-1]
            if a1 <= a0:
                continue
            slope = (v1 - v0) / (a1 - a0)
            if abs(slope) <= 1e-12:
                continue
            limit = self.cathodic_limit_v if slope < 0 else self.anodic_limit_v
            if abs(limit) <= 1e-9:
                continue
            # Only excursions CLOSE to their limit qualify for the decisive
            # step (far ones stay on the coarse secant/regression).
            if abs(v1) < self._APPROACH_RATIO * abs(limit):
                continue
            near = self._band_near_edge(limit)
            # Already in-band on this excursion → let the per-capture band stop
            # own it (don't step further).
            if (limit < 0 and v1 <= near) or (limit > 0 and v1 >= near):
                continue
            cross = a1 + (near - v1) / slope
            if not (np.isfinite(cross) and cross > a1):
                continue
            d = cross - a1
            if best is None or d < best:
                best, best_cross = d, cross
        if best is None:
            return None
        # HARDWARE-LIMITED intercept: if the governing (first-to-cross)
        # excursion's near-edge crossover is at/beyond ``max_ua``, this
        # electrode will NOT reach its band before the hardware ceiling — defer
        # to the downstream SNAP-TO-CEILING (return None) so it jumps to
        # ``max_ua`` in one move, instead of the approach step capping at half
        # the remaining headroom and creeping toward the top (the bench
        # hardware-limited channels finished in ~6 captures via snap; without
        # this the approach step re-introduced the creep it was meant to kill).
        if best_cross is not None and best_cross >= self.ramp.max_ua:
            return None
        # ``best`` is the LOCAL-SLOPE distance to the near edge — a meaningful
        # gap, not the ≈0 projection that caused the old creep — so it drives a
        # real step.  Floor at ``fine_step`` (only relevant if we're already a
        # sliver from the near edge → one small in-band step, never an
        # overshoot); cap at half the remaining headroom for safety (the
        # per-capture band stop catches a small overshoot, back-off a larger).
        max_jump = max((self.ramp.max_ua - current_amp_ua) * 0.5,
                       self.ramp.coarse_step_ua)
        return float(min(max(best, self.ramp.fine_step_ua), max_jump))

    def _min_over_amp(self, captures: List[Capture]) -> Optional[float]:
        """Smallest tested amplitude whose E_pol EXCEEDED the acceptance band's
        far edge (the MATLAB ``currentStim_over`` — the over-side of the
        oscillation bracket).  ``None`` if nothing has overshot yet."""
        overs = [abs(c.pattern.excitation_phase.amplitude_ua)
                 for c in captures
                 if not c.status.aborted and self._potential_limit_exceeded(c)]
        return min(overs) if overs else None

    def _oscillate_approach_step(self, captures: List[Capture],
                                 current_amp_ua: float) -> Optional[float]:
        """MATLAB ``changeCurrent.m`` DISTANCE-TO-LIMIT approach step — the
        robust replacement for the local-slope near-crossover projection
        (operator: "Look at how my MATLAB code tried to oscillate to the
        limit").

        Once the worst excursion is CLOSE to its band near-edge, pick the step
        straight from the distance table (big steps far, ever-finer near) so
        the ramp APPROACHES the limit with monotonically-shrinking steps and
        does NOT overshoot — instead of the slope projection, which blew past
        the limit when the local E_pol slope was under-estimated by
        capture-to-capture noise (new-run CH01 #7→#8: a shallow 0.9 mV/µA
        two-point slope projected +14 µA and overshot to −0.635 V, where the
        distance-table step from 0.013 V to the near edge is just +1 µA).  This
        avoids the overshoot → back-off grind AND the overshoot itself (safer
        for the electrode).  Returns the (positive) delta, or ``None`` when no
        excursion is close yet (the regression owns the fast far climb) or the
        bracket is exhausted (accept / back-off).
        """
        thr = self.ramp.oscillate_dist_thresh_v
        inc = self.ramp.oscillate_increment_ua
        if not thr or not inc:
            return None
        _res = abs(getattr(self.ramp, "test_current_resolution_ua", 0.1)) or 0.1
        best_dist: Optional[float] = None
        for pts in self._excursion_series(captures).values():
            if not pts:
                continue
            a1, v1 = pts[-1]
            # Only the LATEST capture's excursions (the current distance).
            if abs(a1 - current_amp_ua) > max(1.0, 0.02 * current_amp_ua):
                continue
            lim = (self.cathodic_limit_v if v1 < 0
                   else self.anodic_limit_v if v1 > 0 else None)
            if lim is None or abs(lim) < 1e-9:
                continue
            near = self._band_near_edge(lim)
            # Already in-band on this excursion → the per-capture band stop
            # owns it (the run loop breaks before calling _next_step); skip.
            if (lim < 0 and v1 <= near) or (lim > 0 and v1 >= near):
                continue
            dist = abs(near - v1)          # volts to the band near-edge
            if best_dist is None or dist < best_dist:
                best_dist = dist
        if best_dist is None:
            return None
        # Engage ONLY for the FINAL fine pin (within 0.05 V of the near edge).
        # Far from it the regression / secant climbs FAST — the table's coarse
        # rows are for the near approach, not the full climb from 1 µA, and
        # engaging early made a healthy SHALLOW (√-saturating) electrode take
        # too many small table steps (regression tests).  This narrow window is
        # exactly where the local-slope projection blows past the limit on
        # noisy E_pol (CH01 overshoot at 0.013 V), so the distance table's
        # shrinking step wins precisely where it matters.
        if best_dist > 0.050:
            return None
        step = inc[-1]
        for t, s in zip(thr, inc):
            if best_dist > t:
                step = s
                break
        # AMPLITUDE-PROPORTIONAL scale (a simple stand-in for MATLAB's
        # per-electrode surface-area ``scale``): the raw table (calibrated for
        # a reference electrode) is too slow at high current, so scale the step
        # with the current magnitude (capped 3×) — a 460 µA electrode converges
        # in a few steps, a 30 µA one keeps the fine table step.
        step *= min(3.0, max(1.0, current_amp_ua / 100.0))
        # STRICT over/under BRACKET (MATLAB): never step to/past a known
        # over-amp — stay strictly BELOW it, bisecting toward it.
        over = self._min_over_amp(captures)
        if over is not None:
            room = over - current_amp_ua
            if room <= _res:
                return None                # bracket exhausted → accept/back-off
            step = min(step, room * 0.5)   # strictly inside, halve toward over
        return float(max(step, _res))

    def _snap_test_ua(self, amp_ua: float) -> float:
        """Snap a tested amplitude to the 0.1 µA testing grid (operator: "I
        want current testing (not current pattern) to have 0.1 µA
        resolution").  Distinct from the pattern's shape-rendering grid
        (gotcha #64).  Floored at 0 but deliberately NOT clamped to ``max_ua`` —
        the ramp loop's ``while amp <= max_ua`` must be able to EXIT when a
        step pushes past the ceiling; clamping here pinned ``amp`` at max_ua
        and spun the loop forever."""
        res = float(getattr(self.ramp, "test_current_resolution_ua", 0.1) or 0.1)
        snapped = round(float(amp_ua) / res) * res
        return float(max(snapped, 0.0))

    def _is_zero_start_ramp(self, captures) -> bool:
        """True when this configuration's ramp started at 0 µA (the VT-max
        'start at 0 µA' case) — the first non-aborted capture is ≈ 0 µA."""
        for c in captures:
            if getattr(c.status, "aborted", False):
                continue
            try:
                return abs(c.pattern.excitation_phase.amplitude_ua) <= 1e-9
            except Exception:
                return False
        return False

    def _seed_dampen_factor(self, captures) -> float:
        """Dampening applied to the first N real predicted jumps of a
        zero-start adaptive ramp (operator: "Change the safety reduction of
        subsequent steps … to −50 %, −40 %, −30 %"): jump k →
        ``seed_dampen_fractions[k]`` (×0.5, ×0.6, ×0.7), later jumps → 1.0
        (full prediction).  The schedule starts most cautious and relaxes as
        real polarization data accumulates — the early jumps are projected
        from the low-signal 0→1 µA data, so the extra margin keeps a low-Q
        electrode from overshooting (bumps CH16)."""
        if not self._is_zero_start_ramp(captures):
            return 1.0
        idx = max(0, getattr(self, "_ramp_dampen_step", 0))
        fr = self.ramp.seed_dampen_fractions
        return float(fr[idx]) if idx < len(fr) else 1.0

    # ------------------------------------------------------------------
    # Step-size dispatch
    # ------------------------------------------------------------------
    #: Adaptive-safety probe: any predicted jump LARGER than this takes a
    #: FRACTIONAL probe capture first (operator: "for adaptive/regression, have
    #: an additional … increment if the next current amplitude change is >2 µA
    #: for safety" → later refined to a fractional, informative probe).
    _SAFETY_PROBE_MAX_DELTA_UA = 2.0

    def _maybe_safety_probe(self, delta: float,
                            captures: List[Capture]) -> float:
        """Insert a FRACTIONAL PROBE capture before a big adaptive jump.

        Operator safety rule for the adaptive / predictive (regression)
        policies: before committing to a big predicted jump, first step only
        PART of the way and capture — a checkpoint that (a) verifies the
        electrode's local behaviour where the ramp is heading, and (b) hands
        the regression/secant one more nearby point before the full jump.

        REFINED from the original +1 µA checkpoint to a FRACTIONAL probe
        (operator: "jump ~30 % of the way, observe, then commit").  A +1 µA
        step from, say, 2 µA toward a 500 µA jump revealed nothing about what
        500 µA would do — it just read the rest-potential floor and wasted a
        capture, and the subsequent full jump (still built from stale data)
        often overshot and triggered a back-off.  Stepping ``_PROBE_FRACTION``
        (30 %) of the way instead lands a REAL data point partway to the
        target, so the re-prediction on the next step is far better informed →
        the commit lands in-band and the back-off rarely fires.  The one-shot
        ``_safety_probe_armed`` flag lets the (re-predicted) jump through on
        the NEXT step so a chain of big jumps can't collapse into a probe
        crawl.

        GATED on signal emergence: while the polarization signal is still
        buried below ``_SIGNAL_FLOOR_V`` (e.g. the 1 µA probe of a zero-start
        ramp, or a low pattern-amplitude start), the ramp is far below the
        water window and the clamped step is inherently safe — probing there
        only adds cost (and would halve the operator-specified zero-start
        seed jump), so the probe is skipped until real polarization emerges.
        The fixed-increment strategy is untouched (its steps are the
        operator's explicit choice).  Applied at the run-loop call site so
        ``_next_step``'s raw predictions (and their unit tests) stay as-is.
        """
        strat = (self.ramp.strategy or "increment").lower()
        if strat == "increment":
            return delta
        # No probe during the buried-signal geometric search (far from the
        # limit → each bounded search step is safe on its own).
        if not self._signal_emerged(captures):
            return delta
        if delta > self._SAFETY_PROBE_MAX_DELTA_UA:
            if self._safety_probe_armed:
                # The previous step WAS the probe — take the real (re-predicted)
                # jump now.
                self._safety_probe_armed = False
                return delta
            self._safety_probe_armed = True
            # Step a fraction of the way; never below a coarse step (so the
            # probe is a meaningful advance) and never the full jump.
            probe = self._PROBE_FRACTION * delta
            probe = min(delta, max(probe, self.ramp.coarse_step_ua))
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=(f"Safety probe: predicted step {delta:+.1f} µA "
                         f"exceeds {self._SAFETY_PROBE_MAX_DELTA_UA:.0f} µA — "
                         f"stepping {probe:+.1f} µA ("
                         f"{self._PROBE_FRACTION*100:.0f} % of the way) first "
                         f"to observe before committing.")))
            return probe
        self._safety_probe_armed = False
        return delta

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
            # A stepped ramp starting at 0 µA takes ITS OWN next step
            # (0 + coarse step) — handled naturally by the increment path.
            return self._next_step_increment(cap)

        # "Start at 0 µA" (VT maximum): after the 0 µA BASELINE capture the
        # next capture is EXACTLY 1 µA for the adaptive / predictive
        # (regression) policies (operator: "the next capture should be …
        # 1 µA if adaptive/regression is chosen for ramp policy").  The
        # 0 µA capture carries no current, so the seed / secant have no
        # signal to project from — step gently to 1 µA and let the
        # regression climb from that first real measurement.
        if current_amp_ua <= 0.0:
            return 1.0

        # R3 — DECISIVE near-crossover step (highest priority once close).
        # When an excursion is within ``_APPROACH_RATIO`` of its limit,
        # project the band NEAR EDGE from the local slope and land in-band in
        # ONE move.  This replaces the old fine-step creep (operator: "stopped
        # at −0.644 V … did not try again to reach near −0.6"), where the
        # secant/regression projected a target ≈ the current amplitude near the
        # crossover so the step floored to ``fine_step`` and the ramp inched up
        # for 4-6 captures.  Returns ``None`` when no excursion is close yet.
        # MATLAB distance-table oscillation FIRST (near the limit): shrinking
        # steps that approach without overshooting (operator's changeCurrent.m).
        oscillate = self._oscillate_approach_step(captures, current_amp_ua)
        if oscillate is not None:
            return oscillate
        # Slope-projected decisive step (handles the hardware-limited snap the
        # oscillator defers on); regression owns the far climb.
        approach = self._approach_step(captures, current_amp_ua)
        if approach is not None:
            return approach

        # ZERO-START SEED DAMPENING (operator: "Change the safety reduction of
        # subsequent steps … to −50 %, −40 %, −30 %").  The first THREE real
        # predicted jumps of a 0 µA-start ramp are built from the low-signal
        # early data (the least trustworthy of the whole ramp), so whatever
        # crossover the prediction chain (regression → secant → proportional
        # seed) projects is scaled DOWN by the ``seed_dampen_fractions``
        # schedule: jump 1 → ×0.5, jump 2 → ×0.6, jump 3 → ×0.7, later jumps
        # unclamped.  ``_seed_dampen_factor`` reads the ``_ramp_dampen_step``
        # counter (reset per configuration in ``run``); this method increments
        # it after committing a dampened jump.  ``seed_factor == 1.0`` for a
        # non-zero-start ramp or after the three dampened jumps.
        seed_factor = self._seed_dampen_factor(captures)
        is_dampened = seed_factor < 1.0 - 1e-9
        if is_dampened:
            # Reaching here (past the approach-step early return) means this
            # step WILL commit a predicted jump — count it so the NEXT dampened
            # jump advances the schedule (×0.7 → ×0.5 → full).  The ceiling
            # snap is suppressed on dampened steps, so every such step is a
            # real dampened jump.
            self._ramp_dampen_step = getattr(self, "_ramp_dampen_step", 0) + 1

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
            # DIRECTION-OF-TRAVEL SECANT first (attacks the ~42 % low-amplitude
            # "anodic dead-zone").  As soon as ANY excursion has 2 points, its
            # local slope projects the crossover — even while the governing
            # cathodic excursion's VALUE is still positive (it is DESCENDING
            # toward the cathodic limit).  This fires from capture 2, instead
            # of the regression waiting for the value to cross zero at ~capture
            # 4.  The 2-point secant UNDERSHOOTS the concave-down SIROF
            # crossover (verified on bench data) — a safe big step that lands
            # in-band; the convexity guard + half-headroom clamp + per-capture
            # band stop bound any residual overshoot on a non-saturating
            # electrode.
            ds_target = self._local_secant_target(captures)
            if ds_target is not None and is_dampened:
                # −50 % / −40 % / −30 % margin on the first / second / third
                # dampened jump of a zero-start ramp (operator spec — see the
                # dampening note above).
                ds_target *= seed_factor
            if (ds_target is not None and np.isfinite(ds_target)
                    and ds_target > current_amp_ua):
                # SNAP TO CEILING (see the main-path note below): when the
                # regression returns None because the crossover exceeds max_ua,
                # the beyond-ceiling projection lands HERE.  If the UNDERSHOOTING
                # secant already projects ≥ max_ua the electrode won't reach the
                # water window before the hardware ceiling — jump straight to
                # max_ua instead of creeping by ~coarse_step near the top
                # (operator: the anodic CH01/CH02 crept 763→813→863→913→963).
                # Safe by the concave-down undershoot property (a sub-max
                # crosser's undershooting secant stays below max_ua).
                # No ceiling snap on the zero-start seed step — its projection
                # is built from the low-signal 0→1 µA data (rest-potential
                # noise), and a near-flat noise slope projects a huge
                # crossover; snapping the SECOND real capture to the 1000 µA
                # rail off noise would defeat the −30 % margin.  The clamps
                # below bound the step instead; a genuinely hardware-limited
                # channel snaps on the NEXT step, from real data.
                if (ds_target >= self.ramp.max_ua
                        and current_amp_ua < self.ramp.max_ua
                        and not is_dampened):
                    return float(self.ramp.max_ua - current_amp_ua)
                delta = ds_target - current_amp_ua
                max_jump = max((self.ramp.max_ua - current_amp_ua) * 0.5,
                               self.ramp.coarse_step_ua)
                return float(max(min(delta, max_jump),
                                 self.ramp.coarse_step_ua))
            # PROPORTIONAL single-point seed (capture 1 — no slope yet).
            # Assuming polarization ≈ linear through the origin, the amplitude
            # that reaches the limit (ratio = 1) is current / ratio; jump to
            # ``seed_fraction`` × that (seed_fraction < 1 → a safe undershoot).
            # On a ZERO-START ramp this IS jump 1, so use the MORE conservative
            # of the base margin and the dampening schedule's first factor
            # (×0.5) — otherwise the secant path (×0.5) and this fallback (×0.7)
            # would disagree on jump 1.  For a non-zero-start ramp seed_factor
            # is 1.0, so the base ``seed_fraction`` (0.7) margin applies.
            # SAFETY (operator: seed-jump safety cap): the ratio here is the
            # WORST-ABSOLUTE across phases — dominated by the flat anodic-return
            # baseline, so it is conservative by construction; the 60 %-headroom
            # clamp bounds it further, and with only ONE point there is no
            # slope to over-project from.  Do NOT re-key this on the cathodic
            # worst without re-adding a hard cap — a single low-amp cathodic
            # point over-projects the crossover (concave-up-through-origin).
            r = self._polarization_ratio(cap)
            if np.isfinite(r) and r > 1e-3:
                _seed_fr = min(self.ramp.seed_fraction, seed_factor)
                seed_target = current_amp_ua / r * _seed_fr
                delta = seed_target - current_amp_ua
                if delta > self.ramp.coarse_step_ua:
                    # Clamp to 60 % of the remaining headroom so a tiny
                    # first-capture ratio can't fling us at the ceiling.
                    max_jump = max((self.ramp.max_ua - current_amp_ua) * 0.6,
                                   self.ramp.coarse_step_ua)
                    return float(min(delta, max_jump))
            return self.ramp.coarse_step_ua

        # LOCAL-SECANT fast approach.  The global poly fit is conservative
        # and, for the saturating (concave-down) E_pol-vs-I of SIROF,
        # UNDER-predicts the crossover near the limit — so the step collapsed
        # to a ~fine-step creep and the climb burned 6-10 captures on the
        # last few %.  The secant of the LAST two captures projects further
        # (and still UNDERSHOOTS the true crossover for concave-down data, so
        # it's a safe bigger step).  Prefer whichever target is more
        # aggressive; the clamps below + the per-capture limit check keep it
        # from a runaway overshoot.
        secant_target = self._local_secant_target(captures)
        if (secant_target is not None and np.isfinite(secant_target)
                and secant_target > target_ua):
            target_ua = float(min(secant_target, self.ramp.max_ua))

        # Dampen the first three predicted jumps of a zero-start ramp
        # (×0.5, ×0.6, ×0.7) — the projection out of the low-signal early data
        # is the least-informed of the ramp (operator spec — see the note above).
        if is_dampened:
            target_ua *= seed_factor

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

        # SNAP TO CEILING (MATLAB changeCurrent_Fit.m:948) — the efficiency fix
        # for HARDWARE-LIMITED channels.  If even the UNDERSHOOTING local secant
        # projects the crossover at/beyond ``max_ua``, the electrode will NOT
        # reach the water window before the hardware ceiling, so jump straight
        # to ``max_ua`` in ONE step instead of creeping by ~coarse_step near the
        # top (operator: the anodic hardware-limited CH01/CH02 crept 763 → 813 →
        # 863 → 913 → 963).  SAFE by the concave-down undershoot property: a
        # channel that actually crosses BELOW max_ua has an undershooting secant
        # BELOW max_ua, so this NEVER fires early on a real crosser; and the
        # per-capture ``_potential_limit_hit`` still stops the instant the
        # capture at the ceiling crosses.  Requires ≥2 points (secant defined).
        if (secant_target is not None and np.isfinite(secant_target)
                and secant_target >= self.ramp.max_ua
                and current_amp_ua < self.ramp.max_ua
                and not is_dampened):
            # (No ceiling snap on a dampened seed step — noise-slope
            # projection; see the ds_target branch note.)
            return float(self.ramp.max_ua - current_amp_ua)

        # Translate the predicted target into a delta. Clamp:
        #   * never below ``fine_step_ua`` (always make progress) — except on
        #     the zero-start seed step, which floors at ``coarse_step_ua`` so
        #     a noise-driven tiny prediction can't reduce the second real
        #     capture to a 1 µA creep
        #   * never above half the remaining headroom to ``max_ua``
        #     (avoid one giant jump that overshoots)
        min_step = (self.ramp.coarse_step_ua if is_dampened
                    else self.ramp.fine_step_ua)
        delta = target_ua - current_amp_ua
        if delta <= 0:
            return min_step
        max_jump = max((self.ramp.max_ua - current_amp_ua) * 0.5,
                       self.ramp.coarse_step_ua * 4)
        return float(min(max(delta, min_step), max_jump))

    def _local_secant_target(self, captures: List[Capture]) -> Optional[float]:
        """Amplitude the LOCAL slope (last two captures) projects to reach
        ``aim_ratio`` × the water-window limit — the MIN over ALL excursions.

        Runs a 2-point secant on EACH electrode-polarization location (every
        phase of the active AND return electrode) and returns the smallest
        projected crossover — whichever excursion reaches its limit first
        governs the step.  Returns ``None`` when no excursion has two points
        heading toward a limit (caller keeps the regression / seed path).

        **DIRECTION OF TRAVEL, not value sign.**  The limit an excursion is
        heading for is set by its SLOPE, not its current value's sign.  A
        still-POSITIVE but DESCENDING cathodic excursion (anodic-baseline-
        dominated at low amplitude) is heading for the CATHODIC limit, so it
        is projected NOW rather than only after its value crosses zero
        (~capture 4).  This is what lets the seed skip the low-amplitude
        "anodic dead-zone" where the governing cathodic excursion is
        positive-but-descending — the operator's "ramp takes too long" in the
        low-amp regime (~42 % of captures were spent there).

        **Safety** — the real net is NOT a blanket "concave-down ⇒ always
        undershoots" (empirically FALSE for the still-steepening CH05/CH16
        near the top; the old docstring claimed it and was wrong).  The
        guarantees are: (1) the per-capture ``_potential_limit_hit`` band
        stop; (2) the caller's half-remaining-headroom delta clamp; and (3)
        the CONVEXITY GUARD below — if an excursion is ACCELERATING toward its
        limit (|slope| increasing over the last 3 points → concave-UP, where a
        linear secant would OVERSHOOT), its projection is halved so a rare
        non-saturating electrode can't be flung past the crossover.  Real
        SIROF is concave-down throughout its descent (verified on bench data),
        so the secant undershoots and the guard never fires there.
        """
        candidates: List[float] = []
        for pts in self._excursion_series(captures).values():
            if len(pts) < 2:
                continue
            (a0, v0), (a1, v1) = pts[-2], pts[-1]
            if a1 <= a0:                       # amplitude must be increasing
                continue
            slope = (v1 - v0) / (a1 - a0)      # dE_pol / dI
            if abs(slope) <= 1e-12:            # flat excursion → no target
                continue
            # Direction of travel picks the limit (slope sign), NOT v1's sign,
            # so a positive-but-descending cathodic excursion still targets the
            # cathodic limit.
            limit = self.cathodic_limit_v if slope < 0 else self.anodic_limit_v
            # Skip an excursion already at/past this limit — the per-capture
            # band stop owns that; projecting it yields a degenerate ~a1.
            if (limit < 0 and v1 <= limit) or (limit > 0 and v1 >= limit):
                continue
            target_v = self.ramp.aim_ratio * limit          # signed target
            cross = a1 + (target_v - v1) / slope
            if not (np.isfinite(cross) and cross > a1):
                continue
            # Convexity guard: if |slope| is INCREASING (accelerating toward
            # the limit → concave-up), the linear secant OVERSHOOTS the true
            # crossover; halve the projected step.  Strictly reduces it — can
            # never enlarge a step, so it cannot create an overshoot.
            if len(pts) >= 3:
                a_2, v_2 = pts[-3]
                if a0 > a_2:
                    slope_prev = (v0 - v_2) / (a0 - a_2)
                    if abs(slope) > abs(slope_prev) * 1.05:
                        cross = a1 + 0.5 * (cross - a1)
            candidates.append(float(cross))
        if not candidates:
            return None
        return min(candidates)

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
        """Predict the max-charge ceiling by fitting EVERY excursion.

        Faithful port of the MATLAB ``changeCurrent_Fit.m`` fitting block
        (lines 754-886): it loops over every potential-excursion location
        × electrode (Active / Return) × limit, fits each one's voltage vs.
        current, solves for the amplitude where it reaches its limit, and
        takes ``min(currentStim_guess)`` — the SMALLEST crossover, because
        the ceiling is whichever excursion reaches the water window first.

        The earlier Python version collapsed all excursions into ONE
        worst-case ``polarization_ratio`` scalar per capture and fit that
        single envelope. That loses each location's own trajectory and
        mispredicts when the dominant excursion SWITCHES as current rises
        (a steeply-climbing cathodic phase overtaking a flattening anodic
        one). Operator: "consider all potential excursions when predicting
        the maximum charge injection capacity."

        Steps:

        1. Build per-(electrode, phase) amplitude→E_pol trajectories
           (``_excursion_series``).
        2. Below ``min_points_for_regression`` points → return ``None``
           (caller falls back to the seed jump while collecting more).
        3. For each excursion, fit E_pol vs. amplitude (``poly1`` → ``poly2``
           → ``poly3``, first to clear its R² gate) and solve for the
           amplitude where it reaches ITS sign-matching limit.
        4. Return the **minimum** crossover across all excursions — the
           most conservative (first-to-cross) ceiling.
        5. **Early-fit dampening** — one sample past the minimum, ×0.9
           (the early fit is volatile; same trick as the MATLAB code).
        """
        series = self._excursion_series(captures)
        if not series:
            return None
        n_pts = max((len(p) for p in series.values()), default=0)
        if n_pts < self.ramp.min_points_for_regression:
            return None

        candidates: List[float] = []
        x_max = 0.0
        for pts in series.values():
            if len(pts) < self.ramp.min_points_for_regression:
                continue
            x = np.asarray([p[0] for p in pts], dtype=float)
            y = np.asarray([p[1] for p in pts], dtype=float)
            x_max = max(x_max, float(x.max()))
            # R2 — pick the limit by DIRECTION OF TRAVEL (net change from the
            # rest anchor to the highest-amplitude point), NOT the raw latest
            # value's sign.  With the (0, rest) anchor now included, a cathodic
            # excursion that is still POSITIVE at low current but DESCENDING
            # (rest +0.2 → less-positive → negative) has a negative net change,
            # so it is correctly assigned the cathodic limit from the start —
            # instead of the old ``sign(y[-1])`` which read the rest-dominated
            # positive value and wrongly targeted the anodic limit (the
            # low-amplitude dead-zone).  ``pts`` is amplitude-sorted, so
            # ``y[-1] - y[0]`` is the net polarization swing over the ramp.
            limit = self._excursion_limit_for(float(y[-1] - y[0]))
            if limit is None:
                continue
            target = self._solve_excursion_crossing(x, y, limit)
            if target is not None and np.isfinite(target):
                candidates.append(float(target))
        if not candidates:
            return None
        # Most-conservative pick — the MATLAB ``min(currentStim_guess)``.
        target_ua = min(candidates)
        # Early-fit dampening (MATLAB ``currentStim_guess * 0.9``).
        if n_pts <= self.ramp.min_points_for_regression + 1:
            target_ua *= 0.9
        # Clamp to the data's current max (don't predict below where we
        # already are) and to the policy's hard ceiling.
        return float(min(max(target_ua, x_max), self.ramp.max_ua))

    def _fit_and_solve(self, fit_type: str,
                       x: np.ndarray, y: np.ndarray,
                       target: float = 1.0) -> Optional[float]:
        """Fit ``y = f(x)`` of ``fit_type`` and solve for ``x | y = target``.

        ``target`` is the value ``y`` (an E_pol voltage, or a normalized
        ratio for legacy callers) must reach — the water-window limit for
        the per-excursion predictor, ``1.0`` for a ratio fit.

        Returns ``None`` if the fit's R² fails the threshold or the
        crossover root isn't a usable positive real number. Caller
        treats ``None`` as "skip this fit type, try the next."
        """
        if fit_type == "exp1":
            return self._fit_solve_exp1(x, y, target)
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
        # Solve f(x) = target ⇔ shift the constant term down by target and root.
        shifted = coeffs.copy()
        shifted[-1] -= target
        try:
            roots = np.roots(shifted)
        except (np.linalg.LinAlgError, ValueError):
            return None
        # Filter: real, positive, within the configured hardware ceiling.
        # (Was a hardcoded 1000 µA echoing the MATLAB ``max_tf = x_raw <=
        # 1e3``; now tracks ``ramp.max_ua`` so the predicted crossover is
        # never discarded for being above an arbitrary constant when the
        # operator set a different ceiling.  The PlexStim hardware max IS
        # 1000 µA, so the default ceiling should be set accordingly.)
        ceiling = float(self.ramp.max_ua)
        real_pos: List[float] = []
        for root in roots:
            if abs(root.imag) > 1e-9: continue
            v = float(root.real)
            if v <= 0 or v > ceiling: continue
            real_pos.append(v)
        if not real_pos:
            return None
        return min(real_pos)

    def _fit_solve_exp1(self, x: np.ndarray, y: np.ndarray,
                        target: float) -> Optional[float]:
        """MATLAB ``exp1`` fit: ``y = a·exp(b·x)`` solved for ``x | y = target``
        → ``x = ln(target/a) / b``.

        Fitted by LOG-LINEAR regression (``ln|y| = ln|a| + b·x``), which is
        robust with few noisy points and needs no initial guess.  ``a·exp(b·x)``
        is monotone and SAME-SIGN everywhere, so the fit only applies when every
        ``y`` shares one sign (mixed-sign E_pol trajectories can't be
        exponential — those keep the polynomial fits).  R² is scored on the
        ORIGINAL scale (not log space) against the SAME
        ``_R2_HIGH_ORDER_MIN`` gate the higher-order polys use.
        """
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        if x.size < 2 or y.size != x.size:
            return None
        s = np.sign(y)
        if s[0] == 0 or not np.all(s == s[0]):
            return None                       # mixed-sign / zero → not exp1
        ay = np.abs(y)
        if np.any(ay <= 0) or np.ptp(x) <= 0:
            return None
        try:
            b, ln_abs_a = np.polyfit(x, np.log(ay), 1)   # slope, intercept
        except (np.linalg.LinAlgError, ValueError):
            return None
        if not (np.isfinite(b) and np.isfinite(ln_abs_a)) or b == 0.0:
            return None
        a = float(s[0]) * float(np.exp(ln_abs_a))
        y_fit = a * np.exp(b * x)
        ss_res = float(np.sum((y - y_fit) ** 2))
        ss_tot = float(np.sum((y - y.mean()) ** 2))
        r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
        if r2 < self._R2_HIGH_ORDER_MIN:
            return None
        ratio = target / a
        if ratio <= 0.0:                       # ln of ≤ 0 → no real crossover
            return None
        x_cross = float(np.log(ratio) / b)
        ceiling = float(self.ramp.max_ua)
        if x_cross <= 0.0 or x_cross > ceiling:
            return None
        return x_cross

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
