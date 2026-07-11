"""Stimulus waveform generation.

Models the rectangular biphasic / triphasic / arbitrary patterns supported by
the Plexon PlexStim. The MATLAB code calls ``PS_SetRectParam2`` for each
channel; here we wrap the same parameter set in a dataclass so the rest of
the codebase can pass waveforms around as plain Python objects.

Conventions
-----------
* Currents are expressed in microamps (µA), times in microseconds (µs) — the
  same units used by Plexon's API and throughout the IEEE NER paper.
* ``polarity = -1`` is *cathodic-first* (first phase has negative current),
  ``polarity = +1`` is *anodic-first*.
* For triphasic patterns the IEEE NER paper uses a 2:-3:1 amplitude ratio,
  where the largest-magnitude phase is the *excitation* phase. We follow that
  convention here: the user supplies the magnitude of the excitation phase
  (``amp_excite``) and the helper computes the other two phases.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Phase shapes
# ---------------------------------------------------------------------------
# String constants instead of an Enum so prefs / .npz round-trip through JSON
# without a custom encoder. Every constant must be matched in
# ``shape_breakpoints`` below; adding a new shape there + here is the whole
# extension point.
SHAPE_RECTANGULAR       = "rectangular"
SHAPE_LINEAR_INCREASING = "linear_increasing"
SHAPE_LINEAR_DECREASING = "linear_decreasing"
SHAPE_SINUSOIDAL        = "sinusoidal"
SHAPE_SPEEDBUMPS        = "speedbumps"
SHAPE_BOWTIE            = "bowtie"
SHAPE_HALFPIPE          = "halfpipe"
#: Capacitively-coupled exp-decay anodic phase. ``Phase.tau_us`` carries
#: the time constant. Used in asymmetric biphasic mode for the recharge
#: phase, paired with a rectangular cathodic phase per Cogan 2008 Fig 1c
#: and Liu 2026 Fig 1b.
SHAPE_EXP_DECAY         = "exp_decay"
#: Time-reversed exp-decay: I(t) = A · exp(−(W − t)/τ). Starts at
#: A·exp(−W/τ) (≈ 0.7 % of A when τ = W/5) and rises to peak A at
#: end of phase. The mirror image of SHAPE_EXP_DECAY across the
#: middle of the phase. Used by Yip et al. (2017)'s in-vivo tested
#: biphasic-exponential waveform: decaying-exponential cathodic
#: paired with growing-exponential anodic to maintain charge
#: balance under the same τ.
SHAPE_EXP_INCREASING    = "exp_increasing"
#: Truncated Gaussian peaked at the centre of the phase. Standard
#: deviation σ = W / 5 so the truncation at the phase boundaries
#: catches ~99 % of the Gaussian mass; the discarded tails are below
#: the 30-nA quantum at typical peak amplitudes. Sahin & Tie (2007)
#: identified the Gaussian as one of three most-efficient waveforms
#: when accounting for both the strength–duration curve AND the
#: charge-injection capacity of practical electrode materials.
SHAPE_GAUSSIAN          = "gaussian"

PHASE_SHAPES = (
    SHAPE_RECTANGULAR,
    SHAPE_LINEAR_INCREASING,
    SHAPE_LINEAR_DECREASING,
    SHAPE_SINUSOIDAL,
    SHAPE_SPEEDBUMPS,
    SHAPE_BOWTIE,
    SHAPE_HALFPIPE,
    SHAPE_EXP_DECAY,
    SHAPE_EXP_INCREASING,
    SHAPE_GAUSSIAN,
)

#: Decay-completion ratio: t_a = N · τ. With N=5, exp(-N) = 0.0067 so
#: the exp-decay reaches ~0.7 % of peak by the end of the anodic phase
#: — visually indistinguishable from a clean cap discharge. Used by
#: :func:`solve_capacitive_balance` to derive τ from t_a (or vice versa).
EXP_DECAY_TAU_RATIO = 5.0

#: Default sample count for curved shapes (sinusoidal / halfpipe / bowtie /
#: exp-decay / linear ramps) when a caller doesn't pass an explicit budget.
#: The PlexStim 2.0 ``PS_LoadArbPattern`` cap is 999 fixed points or 499
#: paired values per channel; with 240 points per phase × 2 phases plus a
#: handful of zero markers, a symmetric biphasic with two curved phases
#: still fits well under 499 pairs. Real callers (the device-side
#: ``_load_arbitrary``, the preview, and :func:`solve_capacitive_balance`)
#: ask :meth:`PulsePattern.curved_sample_budget` for a pattern-aware
#: budget that pushes the resolution up to the actual ceiling — for
#: asymmetric pulses (one rectangular cathodic + one curved anodic) that
#: ceiling is ~497 breakpoints on the curved phase, near-imperceptible
#: staircase versus the smooth math curve.
_DEFAULT_CURVED_SAMPLES = 240

#: PlexStim ``.pat`` (Variable) format hard cap: 499 (amp_nA, duration_µs)
#: pairs per channel. Used by :meth:`PulsePattern.curved_sample_budget`
#: to allocate breakpoints across the phases of one pulse.
_PAT_MAX_PAIRS = 499

#: PlexStim ``.pat`` (Fixed) format hard cap: 999 amplitude samples per
#: channel, played at a fixed sample period. Pair format and Fixed
#: format are mutually exclusive — every channel chooses one. The
#: :func:`build_pat_samples_fixed` / :func:`validate_pat_samples_fixed`
#: helpers cover the Fixed path; the Variable path stays the default
#: because it carries non-uniform segments more compactly (a 200-µs
#: rectangular phase is one pair vs 200 samples).
_PAT_MAX_FIXED_POINTS = 999

#: Minimum sample period for the Fixed format. The PlexStim 2.0 SDK
#: rejects anything below 1 µs (the device's hardware time resolution
#: matches :data:`stimtest.config.STIM_TIME_RESOLUTION_US`).
_PAT_MIN_SAMPLE_PERIOD_US = 1


@dataclass
class Phase:
    """One phase of a stimulus pulse.

    ``shape`` selects the per-phase current waveform — rectangular by
    default for backwards compatibility.

    Shape-specific extras (ignored for shapes that don't use them):
      * ``bump_count`` — number of half-sine sub-pulses for SHAPE_SPEEDBUMPS.
      * ``tau_us`` — exponential time constant for SHAPE_EXP_DECAY.
        ``0.0`` means "use the canonical t_a / EXP_DECAY_TAU_RATIO
        derivation" (i.e. derive τ from the phase width); set non-zero
        only by :func:`solve_capacitive_balance`.
      * ``tail_zero_us`` — for SHAPE_EXP_DECAY, the duration at the
        *end* of the phase whose breakpoint amplitudes are forced to
        zero (instead of carrying their natural ``A·exp(−t/τ)``
        value). Used by the cap-coupled solver as a discrete-charge-
        balance trim: the trailing portion's natural-amp contribution
        gets removed, the rest of the phase compensates via the
        flat-top / Ia refinement. Default 0 (no zero tail). The
        device's 1 µs grid is the resolution; values < 1 µs round
        up to 1 µs at .pat-build time.
      * ``offset_ua`` — magnitude (always non-negative; the sign
        of the active phase amplitude is auto-applied) of a baseline
        FLOOR amplitude that the shape never drops below. Turns a
        linear-increasing ramp into a TRAPEZOIDAL waveform that
        ramps from ``offset`` to peak rather than from 0 to peak.
        Applies generically to any non-rectangular shape (exp /
        Gaussian / sin / halfpipe / bowtie / linear) — the
        normalised shape is rescaled into [offset, peak]. Ignored
        for rectangular (the rectangle is already at peak the
        whole time). Default 0 (no offset).
    """
    amplitude_ua: float        # signed (µA); negative = cathodic
    width_us: float            # phase width
    delay_after_us: float = 0.0  # interphase or discharge delay following this phase
    shape: str = SHAPE_RECTANGULAR
    bump_count: int = 3        # speedbumps only
    tau_us: float = 0.0        # exp_decay only; 0 = derive from width
    tail_zero_us: float = 0.0  # exp_decay only; trailing duration with amp forced to 0
    offset_ua: float = 0.0     # baseline-floor magnitude for non-rect shapes

    @property
    def charge_nc(self) -> float:
        """Charge per phase, nanocoulombs (signed).

        For non-rectangular shapes this is the *integral* of the
        current over the phase width — equal to ``amplitude * width``
        only when the shape spends 100 % of its width at the peak
        (rectangular). Non-rectangular shapes carry less charge for
        the same peak amplitude:

          * Linear (increasing/decreasing): 0.5
          * Sinusoidal half-sine: 2/π ≈ 0.637
          * Halfpipe (1−cos)/2: 0.5
          * Bowtie (V-shape): 0.5
          * Speedbumps (N pulses at ``bump_count`` ratio): 0.5 of a
            fully-active phase (N pulses + N gaps, each duty-cycle).

        This is the ANALYTIC continuous-shape estimate (``peak × width ×
        duty``) and is used only for SELECTING the excitation phase (argmax
        |charge|).  The charge the codebase REPORTS
        (``PulsePattern.charge_per_phase_nc`` / ``net_charge_nc``) comes from
        the IDEAL continuous integral (:func:`ideal_charge_nc` — trapezoidal,
        unquantized), which equals this analytic value for a canonical shape
        but ALSO correctly handles a non-canonical ``tau_us`` /
        ``tail_zero_us`` / ``offset_ua`` that ``_shape_duty`` hardcodes.  The
        DEVICE-EXACT staircase (``actual_phase_charges_nc``, 30/100 nA grid) is
        used only to show the quantization ERROR in the test-parameters panel
        (operator: "charge metrics use the ideal pattern; realistic only for
        the error").
        """
        return (self.amplitude_ua * 1e-3 * self.width_us
                * _shape_duty(self.shape, bump_count=self.bump_count))

    @property
    def peak_charge_nc(self) -> float:
        """Charge as if the phase ran at peak amplitude for the full width.

        Mirrors the historical pre-shape definition (``amplitude × width``)
        so the rest of the codebase, which keys captures off "Q_ph",
        keeps reporting a value comparable to MATLAB-era data.
        """
        return self.amplitude_ua * 1e-3 * self.width_us

    def scaled(self, factor: float) -> "Phase":
        return Phase(
            amplitude_ua=self.amplitude_ua * factor,
            width_us=self.width_us,
            delay_after_us=self.delay_after_us,
            shape=self.shape,
            bump_count=self.bump_count,
            tau_us=self.tau_us,
            tail_zero_us=self.tail_zero_us,
            offset_ua=self.offset_ua * abs(factor),
        )


def _shape_duty(shape: str, *, bump_count: int = 3) -> float:
    """Fractional area under the unit-amplitude waveform for each shape.

    Used by :meth:`Phase.charge_nc` to scale peak amplitude × width into
    the actual charge that flows through the electrode. Duty for the
    rectangular-with-notch shapes (bowtie, halfpipe) is the AREA THAT
    REMAINS after the notch is removed — not the notch's area.

    For SHAPE_EXP_DECAY this returns the asymptotic-completion value
    (1 - exp(-EXP_DECAY_TAU_RATIO))/EXP_DECAY_TAU_RATIO ≈ 0.1987;
    callers that need the exact charge for a non-canonical τ should
    integrate the breakpoints directly via :func:`shape_breakpoints`.
    """
    if shape == SHAPE_RECTANGULAR:
        return 1.0
    if shape == SHAPE_SINUSOIDAL:
        return 2.0 / np.pi          # ∫ sin(πt)dt over [0,1] = 2/π
    if shape in (SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING,
                 SHAPE_BOWTIE):
        return 0.5                  # triangle / V-shape: half of rectangle
    if shape == SHAPE_HALFPIPE:
        # Rectangle minus a half-sine notch: 1 − 2/π ≈ 0.363
        return 1.0 - 2.0 / np.pi
    if shape == SHAPE_SPEEDBUMPS:
        # Audit finding #18 — ``bump_count`` was previously ignored
        # by the duty calculation (hardcoded 0.7 = the N=2 case),
        # so the analytic charge prediction silently mismatched the
        # delivered charge whenever a user picked any N other than 2.
        # Generalised formula:
        #     N bumps → 2N+1 equal segments
        #     N peak segments at A, (N+1) intermediate at A/2
        #     Total area / (A·W) = (N + 0.5·(N+1)) / (2N+1)
        #                        = (1.5·N + 0.5) / (2N+1)
        # ``bump_count`` defaults to 2 — keeps the legacy duty value
        # of 0.7 for any caller that didn't pass it.
        n_b = max(1, int(bump_count) if bump_count else 2)
        return (1.5 * n_b + 0.5) / (2 * n_b + 1)
    if shape == SHAPE_EXP_DECAY:
        # Canonical τ = W / EXP_DECAY_TAU_RATIO → integral of
        # exp(-t/τ) over [0, W] = τ · (1 - exp(-N)) where N = ratio.
        # As fraction of A · W: (1 - exp(-N)) / N.
        N = EXP_DECAY_TAU_RATIO
        return (1.0 - float(np.exp(-N))) / N
    if shape == SHAPE_EXP_INCREASING:
        # Time-reversed exp-decay; same area under the curve as
        # SHAPE_EXP_DECAY at canonical τ. Both phases of Yip's
        # mirror-symmetric biphasic-exponential carry equal charge.
        N = EXP_DECAY_TAU_RATIO
        return (1.0 - float(np.exp(-N))) / N
    if shape == SHAPE_GAUSSIAN:
        # Zero-tapered Gaussian centred at W/2 with σ = W/5. The
        # raw Gaussian ``exp(−(t−W/2)²/(2σ²))`` evaluated on [0, W]
        # would leave a ~4 % residual at the endpoints (exp(−3.125)
        # ≈ 0.044) which reads as a baseline offset on the preview
        # — visually wrong. We subtract the endpoint value and
        # rescale so the curve hits ZERO at both ends while the
        # peak still touches the user's amplitude ``A`` at t = W/2.
        #
        # Normalised form:
        #   g(t) = (raw(t) − e_v) / (1 − e_v),  e_v = exp(−3.125).
        #
        # Duty factor (fractional area under the unit-amplitude
        # zero-tapered curve):
        #   ∫₀^W g(t) dt = (∫raw dt − e_v·W) / (1 − e_v)
        #              = ((W/5)·√(2π)·erf(2.5/√2) − e_v·W) / (1−e_v)
        # In ratio of A·W:
        #   duty = ((1/5)·√(2π)·erf(2.5/√2) − e_v) / (1 − e_v)
        #        ≈ (0.4953 − 0.0440) / (1 − 0.0440)  ≈ 0.4721
        from math import erf, exp, pi, sqrt
        raw_duty = (1.0 / 5.0) * sqrt(2.0 * pi) * erf(2.5 / sqrt(2.0))
        endpoint = exp(-(2.5 * 2.5) / 2.0)
        return (raw_duty - endpoint) / (1.0 - endpoint)
    return 1.0


# ---------------------------------------------------------------------------
# Capacitively-coupled charge-balance solver
# ---------------------------------------------------------------------------
LOCK_WIDTH = "width"
LOCK_AMPLITUDE = "amplitude"


@dataclass
class CapacitiveBalance:
    """Result of solving for charge-balance in a capacitively-coupled
    biphasic pattern. All values are positive magnitudes; the caller
    re-applies sign per the polarity convention.

    Two anodic-shape regimes coexist behind the same dataclass:

    * **Pure exp-decay** (default; ``saturated=False``,
      ``flat_width_us=0``). The anodic phase is a single
      exponential decay from ``anodic_amplitude_ua`` with time
      constant ``tau_us`` over ``anodic_width_us``. This is the
      historical behaviour for ideal-current-source modelling and
      is what the device plays whenever the unconstrained
      solution sits at or below the hardware ceiling.
    * **Saturated flat-top + decay** (``saturated=True``,
      ``flat_width_us > 0``). The PlexStim 2.0 caps source
      current at ``STIM_MAX_AMPLITUDE_UA`` (1000 µA per channel),
      and a capacitively-coupled ideal solution at very short
      anodic widths or very large cathodic charges would demand
      a peak above that ceiling. In that regime the solver
      clamps ``anodic_amplitude_ua`` at the ceiling, holds it
      flat for ``flat_width_us``, then exponentially decays from
      the same peak over ``anodic_width_us - flat_width_us``
      with time constant ``tau_us``. Total charge across the
      flat + decay equals the cathodic charge to within
      30-nA quantisation.

    See :func:`solve_capacitive_balance` for the math; the
    pattern panel builds either a 2-phase or 3-phase ``Phase``
    list depending on which regime the result describes.
    """
    anodic_amplitude_ua: float
    anodic_width_us: float
    tau_us: float
    cathodic_charge_nc: float
    #: Actual anodic charge after 30-nA quantisation + left-Riemann
    #: integration of the staircase; ideally equal to
    #: ``cathodic_charge_nc`` after the iterative refinement.
    actual_anodic_charge_nc: float = 0.0
    #: ``True`` when the unconstrained solution would have required
    #: ``anodic_amplitude_ua > STIM_MAX_AMPLITUDE_UA``. The shape
    #: switches to flat-top-then-exp-decay (see class docstring).
    saturated: bool = False
    #: Duration of the saturated flat-top portion (µs). Zero
    #: outside saturated mode. The decay portion runs for
    #: ``anodic_width_us - flat_width_us`` immediately after.
    flat_width_us: float = 0.0
    #: ``True`` when no cap-coupled solution exists for the
    #: requested constraints — typically the user pinned a width
    #: too short for any 1000-µA-capped anodic phase to deliver
    #: the required charge (i.e. the cathodic charge exceeds what
    #: even a 1000-µA rectangle of duration ``anodic_width_us``
    #: could provide). When ``True``, the other fields hold the
    #: best-effort approximation but the runner / pattern panel
    #: should refuse to load the pattern and surface a warning.
    infeasible: bool = False


def actual_charge_nc(phase: "Phase",
                     *, current_step_nA: int = 30,
                     n_samples: int = _DEFAULT_CURVED_SAMPLES,
                     ) -> float:
    """Charge in nC for a single phase as the device actually plays it.

    Each shape contributes its discrete-breakpoint integral, computed
    with the rule that's correct for that shape's interpretation:

      * Linearly-changing breakpoints (ramps, bowtie, sin / halfpipe /
        exp-decay sampled finely) → **trapezoidal** rule on the
        rounded amps. Matches the linear interpretation of the
        sample-to-sample transition.
      * Constant rectangular sub-segments (rectangular, speedbumps)
        → trivially `Σ aₖ · Δtₖ` over the held segments — and this
        also happens to be what trapezoidal gives when consecutive
        breakpoints carry the same amplitude.

    Amplitudes are quantised to the device's native 30 nA grid before
    integration so the returned value reflects what's actually
    delivered by the electrode. Use this — not :attr:`Phase.charge_nc`
    — whenever charge balance needs to be checked.
    """
    bps = shape_breakpoints(
        amplitude_ua=phase.amplitude_ua, width_us=phase.width_us,
        shape=phase.shape, bump_count=phase.bump_count,
        tau_us=phase.tau_us, n_samples=n_samples,
        tail_zero_us=getattr(phase, "tail_zero_us", 0.0),
        offset_ua=getattr(phase, "offset_ua", 0.0),
    )
    if len(bps) < 2:
        return 0.0
    times = np.asarray([t for t, _ in bps], dtype=float)
    amps = np.asarray([a for _, a in bps], dtype=float)
    step_ua = float(current_step_nA) * 1e-3 if current_step_nA else 0.0
    if step_ua > 0:
        amps = np.round(amps / step_ua) * step_ua
    # Sample-and-hold integration: aₖ is held over (tₖ, tₖ₊₁), so
    # the integral is Σ aₖ · (tₖ₊₁ − tₖ) — left-Riemann sum on the
    # staircase. Matches what the PlexStim variable-pattern player
    # delivers to the electrode.
    dts = np.diff(times)
    Q_uA_us = float(np.sum(amps[:-1] * dts))
    return Q_uA_us * 1e-3   # µA·µs → nC


def ideal_charge_nc(phase: "Phase",
                    *, n_samples: int = _DEFAULT_CURVED_SAMPLES,
                    ) -> float:
    """Charge in nC for a single phase of the IDEAL (as-designed) waveform.

    This is the true integral of the CONTINUOUS designed shape — NO device
    30/100 nA current quantization AND the **trapezoidal** rule (which exactly
    integrates the piecewise-linear ideal ramp), so a linear-increasing
    1000 µA / 200 µs reads a clean **100.0 nC** (not the device staircase's
    99.6).  It handles a non-canonical ``tau_us`` / ``tail_zero_us`` /
    ``offset_ua`` via the breakpoints (unlike the analytic
    ``peak × width × _shape_duty``, which hardcodes the canonical duty).

    Operator: "For all charge metrics, use the ideal pattern.  Only use the
    realistic when comparing the error in the test parameters."  So every
    REPORTED charge metric (Q_ph, Q_inj, Q_net, cumulative charge) routes
    through this; :func:`actual_charge_nc` (the device sample-and-hold
    staircase on the quantized grid) is used ONLY to show the quantization
    ERROR in the pattern-preview / test-parameters panel.
    """
    bps = shape_breakpoints(
        amplitude_ua=phase.amplitude_ua, width_us=phase.width_us,
        shape=phase.shape, bump_count=phase.bump_count,
        tau_us=phase.tau_us, n_samples=n_samples,
        tail_zero_us=getattr(phase, "tail_zero_us", 0.0),
        offset_ua=getattr(phase, "offset_ua", 0.0),
    )
    if len(bps) < 2:
        return 0.0
    times = np.asarray([t for t, _ in bps], dtype=float)
    amps = np.asarray([a for _, a in bps], dtype=float)
    # Trapezoidal = the true integral of the continuous (piecewise-linear)
    # ideal waveform.  NO current-step rounding — this is the IDEAL charge.
    _trapz = getattr(np, "trapezoid", None) or np.trapz  # numpy 2.x renamed it
    return float(_trapz(amps, times)) * 1e-3   # µA·µs → nC


def solve_capacitive_balance(*,
                             cathodic_amplitude_ua: float,
                             cathodic_width_us: float,
                             lock: str,
                             locked_value: float,
                             current_step_nA: int = 30,
                             n_samples: Optional[int] = None,
                             max_pairs: int = _PAT_MAX_PAIRS,
                             refine_iterations: int = 6,
                             max_amplitude_ua: Optional[float] = None,
                             tau_override_us: Optional[float] = None,
                             ) -> CapacitiveBalance:
    """Charge-balance solver for the cap-coupled anodic phase.

    Given a rectangular cathodic phase ``(I_c, t_c)``, solve for the
    anodic phase parameters such that the anodic charge magnitude
    matches the cathodic — exact charge balance by construction.

    Two lock modes drive the geometry:

    * ``lock="width"`` — user fixes ``t_a``; we derive
      ``τ = t_a / EXP_DECAY_TAU_RATIO`` and solve for ``I_a_peak``.
    * ``lock="amplitude"`` — user fixes ``I_a_peak``; we keep the
      same ``t_a = N·τ`` ratio and solve for τ (and t_a):
        τ = (I_c·t_c) / (I_a_peak · (1 − exp(−N)))
        t_a = N · τ.

    τ override
    ----------
    When ``tau_override_us`` is provided (non-None and > 0), the
    auto-derived τ is replaced by the user's value and the
    *other* free parameter re-solves with that τ in place:

    * ``lock="width"`` (t_a fixed, τ user-provided):
        Pure decay: ``I_a = Q / (τ · (1 − exp(−t_a/τ)))``.
        If the resulting ``I_a > I_max`` we still drop into the
        saturated branch, but with the user's τ pinned: solve
        for ``t_flat`` from ``I_max·t_flat + I_max·τ·(1−exp(−(t_a−t_flat)/τ)) = Q``.
        That equation has no closed-form solution in general
        (the unknown appears both linearly and inside the exp);
        we use a 25-iteration bisection on ``t_flat ∈ [0, t_a]``
        which converges to sub-µs precision.
    * ``lock="amplitude"`` (I_a fixed, τ user-provided):
        ``t_a = −τ · ln(1 − Q/(I_a·τ))``.
        Requires ``I_a · τ > Q`` (else the log is undefined —
        the available current never integrates up to ``Q`` even
        as ``t_a → ∞``). When that constraint fails we mark the
        result ``infeasible`` and clip ``t_a`` to a large
        sentinel so the panel can render a warning rather than
        explode.

    With ``tau_override_us=None`` (default), behaviour is identical
    to the historical solver — the caller path is opt-in.

    Saturation
    ----------
    The PlexStim 2.0 hardware caps source current at
    ``STIM_MAX_AMPLITUDE_UA`` (1000 µA / channel) regardless of
    compliance voltage. When the unconstrained solution at the
    user's locked width would demand a peak above that ceiling
    (short ``t_a`` × large ``Q_cathodic``), the result switches to
    a **flat-top + exp-decay** anodic shape:

    * Hold at ``I_max = STIM_MAX_AMPLITUDE_UA`` for ``t_flat``,
    * then exp-decay from ``I_max`` over ``t_a − t_flat = N·τ``,
    * choosing ``τ`` so the total charge matches the cathodic.

    Charge balance: ``I_max·t_flat + I_max·τ·(1−exp(−N)) = Q``
    Width budget:   ``t_flat + N·τ = t_a``
    → ``τ = (I_max·t_a − Q) / (I_max·(N − 1 + exp(−N)))``
    → ``t_flat = t_a − N·τ``.

    Continuity at the saturation boundary (``Ia_unsat = I_max``)
    yields ``t_flat = 0`` and ``τ = t_a / N`` — i.e. the
    saturated formula collapses smoothly into the unsaturated
    one. Below the ceiling, the result is identical to the
    historical behaviour (``saturated=False``, ``flat_width_us=0``).

    If the user has set ``t_a`` so short that even a full 1000-µA
    rectangle of width ``t_a`` doesn't deliver enough charge
    (``Q > I_max·t_a``), the configuration is **infeasible** —
    no cap-coupled balance exists at any ``τ``. The result's
    ``infeasible`` flag is set; the pattern panel surfaces a
    warning and refuses to load the pulse.

    Parameters
    ----------
    cathodic_amplitude_ua : float
        Magnitude of the rectangular cathodic phase amplitude.
    cathodic_width_us : float
        Cathodic phase width.
    lock : {"width", "amplitude"}
        Which anodic parameter the user pinned.
    locked_value : float
        The pinned value (interpretation depends on ``lock``).
    max_amplitude_ua : float, optional
        Hardware peak-current ceiling. Defaults to
        :data:`stimtest.config.STIM_MAX_AMPLITUDE_UA`. Pass a
        smaller value (e.g. for a partial-channel-share scheme)
        to test the saturation logic; tests use this to drive
        sub-1000-µA scenarios without changing global config.
    tau_override_us : float, optional
        User-pinned τ. When None or ≤ 0, τ is auto-derived from
        the locked geometry as before. When > 0, τ is fixed to
        this value and the derivation re-solves for the
        non-locked parameter (see "τ override" section above).
    current_step_nA, n_samples, max_pairs, refine_iterations
        See module-level docstring; tweaks for the discrete-charge
        refinement loop. Unchanged from the historical signature.
    """
    # Hardware ceiling. Imported lazily so a caller using a non-
    # default value (tests, partial-channel-share schemes) can
    # override without touching global config.
    if max_amplitude_ua is None:
        from .config import STIM_MAX_AMPLITUDE_UA as _imax_default
        max_amp = float(_imax_default)
    else:
        max_amp = float(max_amplitude_ua)

    Ic = abs(float(cathodic_amplitude_ua))
    tc = abs(float(cathodic_width_us))
    Q = Ic * tc          # cathodic charge magnitude (µA · µs == nC)
    N = EXP_DECAY_TAU_RATIO
    decay_factor = 1.0 - float(np.exp(-N))   # (1 − exp(−N)) ≈ 0.9933

    # Compose a budget for the iterative refinement so the solver's
    # actual_charge_nc() integrates over the SAME breakpoint grid
    # that ``_load_arbitrary`` will eventually write to the .pat.
    #
    # Match :meth:`PulsePattern.curved_sample_budget` for the
    # worst-case (saturated) cap-coupled phase composition:
    #   • 1 rect cathodic + (1 pair if interphase delay > 0)
    #   • 1 rect flat-top  (only in saturated mode)
    #   • 1 exp-decay curved + (1 pair if discharge delay > 0)
    #   ⇒ up to 4 fixed pairs around 1 curved phase.
    # ``curved_sample_budget`` returns ``(max_pairs − fixed) // 1
    # + 1`` breakpoints per curved phase, so for 4 fixed pairs that
    # is ``max_pairs − 3`` breakpoints → ``max_pairs − 4`` pairs
    # from the curved (n breakpoints generate n − 1 pairs). Total:
    # 4 + (max_pairs − 4) = max_pairs. ✓
    #
    # We mirror ``max_pairs − 3`` here so the solver's discrete
    # integration sees the SAME number of breakpoints the device
    # will actually play. This makes
    # ``_adjust_flat_for_discrete_balance`` exact (the solver's
    # Q_a equals the device's Q_a to within machine precision),
    # collapsing the historical sub-pC budget mismatch that
    # otherwise leaked through as a few hundred pC of net charge.
    # Slightly over-samples in the unsaturated case (3 fixed pairs
    # → device uses ``max_pairs − 2`` breakpoints; solver uses
    # ``max_pairs − 3``); the difference is one breakpoint, well
    # below the 30 nA quantum's contribution.
    if n_samples is None:
        n_samples = max(8, int(max_pairs) - 3)

    saturated = False
    flat_width_us = 0.0
    infeasible = False

    # τ override path — opt-in when the caller passes a positive
    # ``tau_override_us``. Lets the user fix τ explicitly (e.g. to
    # match a measured RC time constant of a real electrode).
    # ``None`` / 0 / negative means "auto-derive", preserving the
    # legacy behaviour for every existing caller.
    tau_user: Optional[float] = None
    if tau_override_us is not None and float(tau_override_us) > 0.0:
        tau_user = float(tau_override_us)

    if lock == LOCK_WIDTH:
        ta = max(1e-6, abs(float(locked_value)))
        if tau_user is not None:
            # User pinned τ; re-solve for I_a from the pure-decay
            # charge integral:
            #     ∫₀^t_a I_a · exp(−t/τ) dt = I_a · τ · (1 − exp(−t_a/τ))
            # Setting that equal to Q → I_a = Q / (τ · (1 − exp(−t_a/τ))).
            tau = tau_user
            decay_user = 1.0 - float(np.exp(-ta / tau)) if tau > 0 else 0.0
            Ia = Q / (tau * decay_user) if tau * decay_user > 0 else 0.0
        else:
            tau = ta / N
            # Unconstrained peak amplitude (ideal current source).
            Ia = Q / (tau * decay_factor) if tau * decay_factor > 0 else 0.0
        # Saturation check. If the ideal solver wants more than the
        # hardware can deliver, switch to the flat-top-then-exp-decay
        # shape with the peak clamped at ``max_amp``. See the
        # docstring for the derivation.
        if Ia > max_amp:
            saturated = True
            Ia = max_amp
            if tau_user is not None:
                # User-pinned τ + saturation → solve for t_flat from
                #   I_max·t_flat + I_max·τ·(1−exp(−(t_a−t_flat)/τ)) = Q
                # No closed form (t_flat appears both linearly and
                # inside the exp); bisect on t_flat ∈ [0, t_a]. The
                # equation is monotone in t_flat (charge increases
                # as the flat-top grows), so bisection converges
                # cleanly. 25 iterations gives ~30 ns precision on
                # a 10 ms upper bound — well under the 1 µs hardware
                # quantum. If even the maximum (t_flat = t_a) can't
                # deliver Q, the config is infeasible.
                tau = tau_user
                if max_amp * ta < Q - 1e-9:
                    infeasible = True
                    flat_width_us = ta
                else:
                    lo, hi = 0.0, ta
                    for _ in range(25):
                        mid = 0.5 * (lo + hi)
                        decay_w = ta - mid
                        decay_q = (max_amp * tau
                                   * (1.0 - float(np.exp(-decay_w / tau)))
                                   if tau > 0 else 0.0)
                        q_total = max_amp * mid + decay_q
                        if q_total < Q:
                            lo = mid
                        else:
                            hi = mid
                    flat_width_us = 0.5 * (lo + hi)
            else:
                denom = max_amp * (N - 1.0 + float(np.exp(-N)))
                # ``denom`` is positive for N >= 1 (with N = 5 it's
                # ~4.0067 × max_amp); guard anyway against degenerate
                # configs that might pass N <= 1 in a future tweak.
                if denom > 0:
                    tau = (max_amp * ta - Q) / denom
                else:
                    tau = 0.0
                flat_width_us = ta - N * tau
                # Infeasible when even a full 1000-µA rectangle of
                # width ``ta`` can't deliver Q. ``flat_width_us`` < 0
                # means the cathodic charge is too large for the
                # locked anodic width; ``tau`` < 0 means the user has
                # set ``ta`` shorter than the saturation requires.
                # Either way no cap-coupled balance exists at this
                # ``ta``; we return the closest-fit values and flag
                # the result for the caller to render a warning.
                if tau < 0.0 or flat_width_us < 0.0:
                    infeasible = True
                    tau = max(0.0, tau)
                    flat_width_us = max(0.0, flat_width_us)
    elif lock == LOCK_AMPLITUDE:
        Ia = max(1e-9, abs(float(locked_value)))
        # Defensive clamp — the spinbox shouldn't allow values
        # above ``max_amp``, but if a programmatic caller passes
        # one in we still cap rather than asking the device for
        # current it can't deliver. No flat-top in this branch:
        # at the locked amplitude (now ≤ max_amp), the pure exp-
        # decay shape carries the full charge naturally.
        if Ia > max_amp:
            saturated = True
            Ia = max_amp
        if tau_user is not None:
            # User pinned τ; solve for t_a from the pure-decay
            # integral:
            #     I_a · τ · (1 − exp(−t_a/τ)) = Q
            #     → t_a = −τ · ln(1 − Q/(I_a · τ))
            # Requires I_a · τ > Q — else the integral can never
            # reach Q (asymptotic ceiling = I_a · τ). Flag
            # infeasible and clip t_a so the panel can warn
            # rather than blow up on the log.
            tau = tau_user
            ratio = Q / (Ia * tau) if Ia * tau > 0 else float("inf")
            if ratio >= 1.0 - 1e-12:
                infeasible = True
                # Sentinel large t_a — visually conveys "the decay
                # would need to run forever". 100 ms is plenty
                # past the typical < 5 ms anodic windows.
                ta = 100_000.0
            else:
                ta = -tau * float(np.log(1.0 - ratio))
        else:
            tau = Q / (Ia * decay_factor) if Ia * decay_factor > 0 else 0.0
            ta = N * tau
    else:
        raise ValueError(
            f"lock must be {LOCK_WIDTH!r} or {LOCK_AMPLITUDE!r}, "
            f"got {lock!r}.")

    # ---- Refine for ACTUAL charge balance ----
    # The continuous solution above gives ideal balance. After 30 nA
    # quantisation + left-Riemann staircase integration, the actual
    # anodic Q usually misses Q_cath by < 1 % due to rounding in the
    # exp-decay tail. Iteratively scale the *non-locked* parameter so
    # the staircase Q lands exactly on Q_cath. The cathodic phase is
    # also rounded so its actual charge is what we balance against.
    cath_phase = Phase(amplitude_ua=Ic, width_us=tc,
                       shape=SHAPE_RECTANGULAR)
    Q_cath_actual = actual_charge_nc(
        cath_phase, current_step_nA=current_step_nA, n_samples=n_samples)

    def _q_anod_pure_decay(Ia_val: float, ta_val: float,
                           tau_val: float) -> float:
        """Discrete charge of a pure exp-decay phase."""
        anod = Phase(amplitude_ua=Ia_val, width_us=ta_val,
                     shape=SHAPE_EXP_DECAY, tau_us=tau_val)
        return actual_charge_nc(
            anod, current_step_nA=current_step_nA, n_samples=n_samples)

    def _q_anod_saturated(Ia_val: float, t_flat: float,
                          tau_val: float, ta_val: float) -> float:
        """Discrete charge of the flat + exp-decay composite. Sums
        the two phases the device will actually play (a rectangular
        flat-top of width ``t_flat`` followed by an exp-decay of
        width ``ta_val − t_flat`` with the same peak)."""
        flat_charge = 0.0
        if t_flat > 0:
            flat = Phase(amplitude_ua=Ia_val, width_us=t_flat,
                         shape=SHAPE_RECTANGULAR)
            flat_charge = actual_charge_nc(
                flat, current_step_nA=current_step_nA,
                n_samples=n_samples)
        decay_w = max(0.0, ta_val - t_flat)
        decay_charge = 0.0
        if decay_w > 0 and tau_val > 0:
            decay = Phase(amplitude_ua=Ia_val, width_us=decay_w,
                          shape=SHAPE_EXP_DECAY, tau_us=tau_val)
            decay_charge = actual_charge_nc(
                decay, current_step_nA=current_step_nA,
                n_samples=n_samples)
        return flat_charge + decay_charge

    def _adjust_flat_for_discrete_balance(
            Ia_val: float, ta_val: float, tau_val: float,
            flat_val: float, target_Q_nc_val: float,
            max_amp_val: float) -> float:
        """Discrete-aware flat-width nudge for charge balance.

        The earlier saturated refinement re-solved τ from the
        ANALYTICAL closed-form ``τ = (max·t_a − Q) / (max·(N − 1
        + e^(−N)))``. Problem: after 30-nA quantisation + the
        left-Riemann staircase the device actually plays, the
        DISCRETE anodic charge differs from the analytical
        integral by a few hundred pC over a ~500 nC pulse —
        small but visible (the user reported a +0.94 nC residual
        on a -1000 µA × 500 µs cathodic / 1250 µs anodic case).
        Each refinement iteration re-solved the *same*
        analytical τ, so the loop never absorbed the discrete
        sampling error.

        This helper closes the loop on the actual discrete
        charge: integrate Q_a over the device's breakpoint
        grid, compare with the cathodic discrete charge, and
        shave/grow the flat-top by ``ΔQ / max_amp`` µs to
        cancel the residual. Each µs of flat-top contributes
        exactly ``max_amp`` µA·µs of charge (a clean rectangle —
        no rounding except on a single 30 nA quantum at the
        amplitude); the decay portion's discrete charge is
        almost unchanged for sub-µs flat-top adjustments
        (``decay_w = ta − flat`` shifts by the same amount,
        but the integral's sensitivity to ``decay_w`` at the
        N=5 tail is negligible — the curve has long since
        decayed past 30 nA).

        Two or three iterations of this in the outer refinement
        loop drive the residual to sub-pC. Returns the
        adjusted ``flat_width`` clamped to [0, t_a] so a
        wildly-overshooting first iteration can't push the
        flat-top into nonsensical territory.
        """
        Q_a = _q_anod_saturated(Ia_val, flat_val, tau_val, ta_val)
        # ``actual_charge_nc`` returns nC; the formula's
        # ``max_amp`` is µA, ``flat`` is µs. nC · 1000 = µA·µs.
        err_uAus = (abs(Q_a) - abs(target_Q_nc_val)) * 1000.0
        if max_amp_val > 0:
            flat_new = flat_val - err_uAus / max_amp_val
        else:
            flat_new = flat_val
        return max(0.0, min(ta_val, flat_new))

    for _ in range(refine_iterations):
        if saturated and flat_width_us > 0:
            Q_a = _q_anod_saturated(Ia, flat_width_us, tau, ta)
        else:
            Q_a = _q_anod_pure_decay(Ia, ta, tau)
        if Q_a == 0:
            break
        scale = abs(Q_cath_actual) / abs(Q_a)
        if abs(scale - 1.0) < 1e-9:
            break
        if saturated and flat_width_us > 0:
            # In saturated mode the peak is pinned at ``max_amp``;
            # we can't scale that. The width budget is also fixed
            # (user locked ``ta``), so the only knob we have left
            # is the flat-top width. Adjust it directly based on
            # the DISCRETE charge gap rather than re-solving the
            # analytical equation (which can't see the 30-nA
            # quantisation + left-Riemann staircase error). See
            # ``_adjust_flat_for_discrete_balance`` for the
            # rationale.
            target_Q_uAus = Q_cath_actual * 1000.0
            if max_amp * ta < target_Q_uAus - 1e-9:
                # Even a full-rect at max_amp can't deliver Q.
                # No feasible saturated balance exists.
                infeasible = True
                flat_width_us = ta
            else:
                flat_width_us = _adjust_flat_for_discrete_balance(
                    Ia, ta, tau, flat_width_us,
                    Q_cath_actual, max_amp)
        elif lock == LOCK_WIDTH:
            # Width is fixed; scale amplitude by the ratio. τ stays
            # at its current value (auto-derived τ = t_a / N is
            # tied to width and unaffected; user-pinned τ is also
            # untouched). If scaling pushes above max_amp the
            # unsaturated solution can no longer hold; switch the
            # loop to the saturated path on the next iteration.
            Ia = Ia * scale
            if Ia > max_amp:
                saturated = True
                Ia = max_amp
                target_Q_uAus = Q_cath_actual * 1000.0
                if max_amp * ta < target_Q_uAus - 1e-9:
                    infeasible = True
                    flat_width_us = ta
                else:
                    # Seed the saturated state with an analytical
                    # estimate of (τ, flat) so the first
                    # discrete-aware adjustment starts close to
                    # the right answer. For user-pinned τ keep
                    # the user's value; for auto-derive use the
                    # closed-form τ that satisfies analytical
                    # balance under the N·τ + flat = ta
                    # constraint.
                    if tau_user is None:
                        denom = max_amp * (N - 1.0 + float(np.exp(-N)))
                        if denom > 0:
                            tau = max(0.0, (max_amp * ta - target_Q_uAus)
                                      / denom)
                            flat_width_us = max(0.0, ta - N * tau)
                    else:
                        # Manual τ: leave τ at the user's value,
                        # initialise flat from the analytical
                        # rectangle estimate (Q_remaining /
                        # max_amp) so the next iteration's
                        # discrete adjustment has a sensible
                        # starting point.
                        decay_q_anal = (max_amp * tau
                                        * (1.0 - float(np.exp(-ta / tau)))
                                        if tau > 0 else 0.0)
                        flat_width_us = max(
                            0.0, min(ta, (target_Q_uAus - decay_q_anal)
                                     / max_amp))
                    # One discrete-aware refinement step on the
                    # initial guess so the next outer-loop
                    # iteration sees a near-balanced state.
                    flat_width_us = _adjust_flat_for_discrete_balance(
                        Ia, ta, tau, flat_width_us,
                        Q_cath_actual, max_amp)
        else:
            # Amplitude is fixed; scale width by the ratio. τ
            # scales too in auto-derive mode (τ = t_a / N), but
            # stays pinned when the user has supplied an override.
            ta = ta * scale
            if tau_user is None:
                tau = ta / N

    # Sync n_samples to the panel's ACTUAL breakpoint count for the
    # determined saturation state. The panel computes per-curved
    # samples via :meth:`PulsePattern.curved_sample_budget`, which
    # returns ``(max_pairs − fixed_pairs) + 1`` breakpoints for the
    # 1-curved cap-coupled composition. fixed_pairs depends on
    # whether saturation introduces the flat-top phase:
    #
    #   * Saturated: cath rect + flat rect + curved + 2 delays
    #     → 4 fixed pairs → max_pairs − 4 + 1 = 496 breakpoints.
    #   * Unsaturated: cath rect + curved + 2 delays
    #     → 3 fixed pairs → max_pairs − 3 + 1 = 497 breakpoints.
    #
    # Without this sync the solver's discrete charge differs from
    # the panel's by ±1 sample's worth (~13 pC at the typical tail
    # amplitude), leaking through as a panel-level net charge
    # residual that the refinement above couldn't see. With this
    # sync, the solver's discrete model matches the panel's exactly
    # and the existing refinement converges to within sub-pC.
    n_samples_panel = max(8, int(max_pairs)
                          - (4 if saturated else 3) + 1)
    if n_samples_panel != n_samples:
        # Re-establish the discrete reference (Q_cath_actual) and
        # re-run a few refinement passes at the corrected sample
        # count so the returned Ia / flat_width / τ correspond to
        # the breakpoint grid the panel will actually integrate.
        n_samples = n_samples_panel
        Q_cath_actual = actual_charge_nc(
            cath_phase, current_step_nA=current_step_nA,
            n_samples=n_samples)
        for _ in range(refine_iterations):
            if saturated and flat_width_us > 0:
                Q_a = _q_anod_saturated(Ia, flat_width_us, tau, ta)
            else:
                Q_a = _q_anod_pure_decay(Ia, ta, tau)
            if Q_a == 0:
                break
            scale = abs(Q_cath_actual) / abs(Q_a)
            if abs(scale - 1.0) < 1e-9:
                break
            if saturated and flat_width_us > 0:
                target_Q_uAus = Q_cath_actual * 1000.0
                if max_amp * ta < target_Q_uAus - 1e-9:
                    infeasible = True
                    flat_width_us = ta
                    break
                flat_width_us = _adjust_flat_for_discrete_balance(
                    Ia, ta, tau, flat_width_us,
                    Q_cath_actual, max_amp)
            elif lock == LOCK_WIDTH:
                Ia = Ia * scale
            else:
                ta = ta * scale
                if tau_user is None:
                    tau = ta / N

    if saturated and flat_width_us > 0:
        Q_a_final = _q_anod_saturated(Ia, flat_width_us, tau, ta)
    else:
        Q_a_final = _q_anod_pure_decay(Ia, ta, tau)

    return CapacitiveBalance(
        anodic_amplitude_ua=Ia,
        anodic_width_us=ta,
        tau_us=tau,
        cathodic_charge_nc=Q_cath_actual,
        actual_anodic_charge_nc=Q_a_final,
        saturated=saturated,
        flat_width_us=flat_width_us,
        infeasible=infeasible,
    )


def shape_breakpoints(*, amplitude_ua: float, width_us: float,
                      shape: str = SHAPE_RECTANGULAR,
                      bump_count: int = 3,
                      tau_us: float = 0.0,
                      n_samples: int = _DEFAULT_CURVED_SAMPLES,
                      tail_zero_us: float = 0.0,
                      offset_ua: float = 0.0,
                      ) -> List[Tuple[float, float]]:
    """Generate (offset_us, amp_ua) breakpoints for a single phase.

    The returned list is **relative to the start of the phase** — the
    first breakpoint is at ``offset_us = 0`` and the last at
    ``offset_us = width_us``. The Plexon ``.pat`` writer composes
    multiple phases by adding the running cursor to each offset.

    All shapes are rendered into linearly-interpolated breakpoint
    sequences so a single waveform-loader code path handles every
    case. The PlexStim DLL linearly interpolates between breakpoints,
    so curved shapes are sampled at ``n_samples`` evenly-spaced points
    across the phase width — visually indistinguishable from the
    continuous curve at 50 samples per 200 µs phase.

    The scaled amplitude at each breakpoint is signed: positive for
    anodic, negative for cathodic. ``amplitude_ua`` is the **peak**
    magnitude (signed) — shape factors of [-1, +1] multiply it.

    ``tail_zero_us`` (SHAPE_EXP_DECAY only): the trailing duration
    whose breakpoint amplitudes are forced to zero rather than
    carrying their natural ``A·exp(−t/τ)`` value. Used by the
    cap-coupled solver as a discrete-charge-balance trim — zeroing
    the small-amplitude tail removes a known charge contribution
    that the solver compensates for elsewhere (flat-top width or
    Ia scale). The total phase duration ``width_us`` is preserved;
    only the trailing portion's *amplitudes* are clamped.
    """
    if width_us <= 0:
        return [(0.0, 0.0)]
    s = shape.lower().strip()
    A = float(amplitude_ua)
    W = float(width_us)
    # Local helper that applies the optional baseline-floor offset
    # to a computed amps array and returns the (t, amp) breakpoint
    # list. Skipped (no-op) for rectangular and for missing /
    # zero-magnitude offsets. Caps the offset's magnitude at |A| so
    # the user can't accidentally invert the shape by typing an
    # offset larger than the peak.
    #
    # The transform is a linear blend: each natural amp ``a`` (which
    # by construction lies between 0 and A in the same sign) is
    # remapped to ``signed_offset + (A − signed_offset) · (a / A)``,
    # so the natural-min-of-zero point lands at ``signed_offset`` and
    # the natural-peak-of-A point lands at ``A``. For shapes whose
    # natural minimum isn't exactly 0 (e.g. exp_decay floors at
    # ``A·e^(−W/τ)``) the floor lands at approximately
    # ``signed_offset`` plus a small natural-floor remainder; the
    # qualitative trapezoidal effect is the same.
    def _bps_with_offset(ts_arr, amps_arr):
        if abs(offset_ua) > 1e-12 and abs(A) > 1e-12 and s != SHAPE_RECTANGULAR:
            sign = 1.0 if A >= 0 else -1.0
            so = sign * min(abs(float(offset_ua)), abs(A))
            amps_arr = so + (A - so) * (amps_arr / A)
        return list(zip(ts_arr.tolist(), amps_arr.tolist()))

    if s == SHAPE_RECTANGULAR:
        # Two breakpoints — start and end at peak. Existing behaviour.
        return [(0.0, A), (W, A)]

    if s == SHAPE_LINEAR_INCREASING:
        # The device sample-and-holds, so 2 breakpoints would render
        # as a flat zero. Sample at n_samples + 1 evenly-spaced times
        # so the device steps through the ramp and the Actual plot
        # shows the discrete approximation.
        ts = np.linspace(0.0, W, n_samples + 1)
        amps = A * ts / W
        return _bps_with_offset(ts, amps)

    if s == SHAPE_LINEAR_DECREASING:
        ts = np.linspace(0.0, W, n_samples + 1)
        amps = A * (1.0 - ts / W)
        return _bps_with_offset(ts, amps)

    if s == SHAPE_SINUSOIDAL:
        # Half-sine: amp(t) = A · sin(π · t / W). Zero at endpoints,
        # peak at midpoint. ``n_samples`` is honored verbatim — the
        # caller is expected to size it via
        # :meth:`PulsePattern.curved_sample_budget` so the staircase
        # tracks the smooth curve at near-imperceptible resolution.
        n_curve = max(2, int(n_samples))
        ts = np.linspace(0.0, W, n_curve)
        amps = A * np.sin(np.pi * ts / W)
        return _bps_with_offset(ts, amps)

    if s == SHAPE_BOWTIE:
        # "Rectangular pulse with an isosceles triangle cut out of it" —
        # peak at endpoints, 0 at midpoint, linear in between. The
        # device sample-and-holds, so 3 breakpoints would render as
        # a rectangular pulse + zero (wrong); sample densely so the
        # discrete staircase actually approximates the V-cut shape.
        ts = np.linspace(0.0, W, n_samples + 1)
        amps = A * np.abs(2.0 * ts / W - 1.0)
        return _bps_with_offset(ts, amps)

    if s == SHAPE_HALFPIPE:
        # "Rectangular pulse with a sinusoid cut out of it" —
        # I(t) = A · (1 − sin(π · t / W)). Peak at endpoints, smooth
        # dip to 0 at midpoint. Honors ``n_samples`` directly so
        # ``curved_sample_budget`` can push it up to the .pat file's
        # remaining capacity.
        n_curve = max(2, int(n_samples))
        ts = np.linspace(0.0, W, n_curve)
        amps = A * (1 - np.sin(np.pi * ts / W))
        return _bps_with_offset(ts, amps)

    if s == SHAPE_SPEEDBUMPS:
        # Bi-level rectangular pattern per the user's ASCII spec.
        # ``bump_count`` is the number of peak bumps; the pattern
        # has ``2N+1`` equal segments alternating intermediate
        # (= A/2) → peak (= A) → intermediate → … → intermediate.
        # The intermediate level is fixed at 50 % of the peak.
        #
        # Audit finding #18 — ``bump_count`` was previously
        # ignored (hardcoded N=2). The duty-factor calculation in
        # ``_shape_duty`` was correspondingly hardcoded to 0.7,
        # the analytic value for N=2. Both are now generalised:
        # ``_shape_duty`` reads ``bump_count`` and computes the
        # exact duty ``(1.5·N + 0.5) / (2N+1)``; this renderer
        # uses the same N so analytic and discrete charge agree
        # for any user-chosen N ≥ 1.
        #
        # ASCII spec for N=2 (one column = one time unit):
        #     _                _      <- baseline (outside the phase)
        #       |_    _   _|          <- intermediate (= A/2)
        #          |_| |_|            <- peak (= A)
        #
        # Each segment gets two breakpoints at the same amplitude
        # (start + end) so linear interpolation between consecutive
        # breakpoints renders flat segments; segment boundaries
        # share a time and force instantaneous steps under interp.
        n_bumps = max(1, int(bump_count) if bump_count else 2)
        n_segs = 2 * n_bumps + 1
        seg_w = W / n_segs
        int_amp = A / 2.0   # 50 % of peak
        bps: List[Tuple[float, float]] = []
        for k in range(n_segs):
            t_start = k * seg_w
            t_end = (k + 1) * seg_w
            amp = A if (k % 2 == 1) else int_amp
            bps.append((t_start, amp))
            bps.append((t_end, amp))
        return bps

    if s == SHAPE_EXP_DECAY:
        # Capacitively-coupled anodic phase. ``tau_us`` is the
        # exponential time constant; if 0 (or negative), fall back
        # to the canonical t_a = EXP_DECAY_TAU_RATIO · τ ratio so
        # the shape stays well-defined when this is called outside
        # the cap-coupled solver. I(t) = A · exp(-t / τ); peak at
        # t=0, decays to A · exp(-W/τ) by end of phase (≈ 0.7 % of
        # peak when τ = W/5). The caller is responsible for picking
        # A consistent with the cathodic charge they want to balance
        # — see :func:`solve_capacitive_balance`.
        #
        # ``n_samples`` is sized by ``curved_sample_budget`` for the
        # caller's pattern. For asymmetric cap-coupled (the only mode
        # where exp-decay appears), the cathodic side is a 1-pair
        # rectangular phase so the anodic phase gets nearly the full
        # 499-pair budget — fine enough that the staircase tracks the
        # exponential's fast t=0 drop without visible "blocks".
        tau = float(tau_us) if tau_us and tau_us > 0 else W / EXP_DECAY_TAU_RATIO
        n_exp = max(8, int(n_samples))
        ts = np.linspace(0.0, W, n_exp)
        amps = A * np.exp(-ts / tau)
        # ``tail_zero_us`` clamp — zero out the last K *played*
        # amplitudes such that K · dt ≥ tail_zero_us, where dt is
        # the uniform breakpoint spacing. The played pairs (per
        # ``build_pat_pairs``) walk amps[0..n-2]; amps[n-1] is the
        # boundary marker and isn't played. Zeroing only the
        # boundary would be a no-op for the device — we have to
        # also zero played amps[n-1-K..n-2] so the device actually
        # delivers 0 µA for the trailing K samples.
        #
        # K is the round-to-nearest-integer of tail_zero_us / dt,
        # so the zeroed duration is the closest integer-sample
        # match to the requested ``tail_zero_us``. Sub-dt
        # tail_zero_us values either round to 0 (no trim) or to
        # 1 (zero exactly one played sample). The caller is
        # responsible for picking ``tail_zero_us`` ≥ ~0.5·dt if
        # they want any trim to happen — the cap-coupled panel
        # post-process does this with a half-quantum guard.
        tz = float(tail_zero_us)
        if tz > 0.0 and n_exp > 1:
            dt_bp = W / (n_exp - 1)
            K = max(0, int(round(tz / dt_bp))) if dt_bp > 0 else 0
            # Cap K at n_exp - 1 played samples (everything except
            # the very first breakpoint, which carries the peak).
            K = min(K, n_exp - 1)
            if K > 0:
                # Zero the last K played amps + the boundary marker.
                # Indices n-1-K .. n-1 inclusive.
                amps[n_exp - 1 - K:] = 0.0
        return _bps_with_offset(ts, amps)

    if s == SHAPE_EXP_INCREASING:
        # Time-reversed exp-decay: I(t) = A · exp(−(W − t) / τ).
        # Used by Yip et al. (2017) as the anodic phase of a
        # mirror-symmetric biphasic-exponential waveform whose
        # cathodic phase is SHAPE_EXP_DECAY with the same τ.
        # Charge balance holds because both phases integrate to
        # ``A · τ · (1 − e^(−W/τ))`` in magnitude.
        tau = float(tau_us) if tau_us and tau_us > 0 else W / EXP_DECAY_TAU_RATIO
        n_exp = max(8, int(n_samples))
        ts = np.linspace(0.0, W, n_exp)
        amps = A * np.exp(-(W - ts) / tau)
        return _bps_with_offset(ts, amps)

    if s == SHAPE_GAUSSIAN:
        # ZERO-TAPERED Gaussian peaked at the phase's midpoint with
        # σ = W / 5. The raw Gaussian
        # ``A·exp(−(t−W/2)²/(2σ²))`` on [0, W] leaves a ~4 %
        # endpoint residual (≈ A·exp(−3.125) ≈ 0.044·A) which reads
        # as a baseline offset on the preview — the curve looks
        # like it's lifted off zero rather than tapered to zero.
        # We subtract the endpoint value and rescale so the curve
        # hits exactly zero at both phase boundaries while the
        # peak still touches the user-specified ``A`` at t = W/2.
        #
        # The user's signed ``amplitude_ua`` IS the peak amplitude
        # so the spinbox reads naturally; ``charge_nc`` accounts
        # for the zero-tapered duty factor (≈ 0.472, see
        # :func:`_shape_duty`). See Sahin & Tie (2007) Figure 1
        # for the centred-Gaussian convention.
        n_g = max(8, int(n_samples))
        ts = np.linspace(0.0, W, n_g)
        sigma = W / 5.0 if W > 0 else 1.0
        center = W * 0.5
        raw = np.exp(-((ts - center) / sigma) ** 2 / 2.0)
        endpoint = float(np.exp(-((2.5) ** 2) / 2.0))   # raw(0) = raw(W)
        # Zero-tapered shape: floor-subtract + rescale to keep peak at A.
        amps = A * (raw - endpoint) / (1.0 - endpoint)
        return _bps_with_offset(ts, amps)

    # Unknown shape — fall back to rectangular so an old-prefs string
    # we don't recognise doesn't crash the runner.
    return [(0.0, A), (W, A)]


@dataclass
class PulsePattern:
    """A complete stimulus pulse pattern (one pulse, repeated at ``rate_hz``)."""
    phases: List[Phase] = field(default_factory=list)
    rate_hz: float = 50.0
    repetitions: int = 0   # 0 = infinite (Plexon convention)

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------
    @classmethod
    def biphasic(
        cls,
        amplitude_ua: float,
        phase_width_us: float = 200.0,
        interphase_us: float = 20.0,
        discharge_us: float = 20.0,
        polarity: int = -1,
        rate_hz: float = 50.0,
        symmetric: bool = True,
        amplitude2_ua: Optional[float] = None,
        phase_width2_us: Optional[float] = None,
        shape: str = SHAPE_RECTANGULAR,
        shape2: Optional[str] = None,
        bump_count: int = 3,
        bump_count2: Optional[int] = None,
    ) -> "PulsePattern":
        """Symmetric biphasic by default, with optional asymmetric override.

        Phase shapes (``shape`` / ``shape2``) are **only available in
        asymmetric mode** (``symmetric=False``). Symmetric patterns
        are always rectangular — passing a non-rectangular ``shape``
        with ``symmetric=True`` raises ``ValueError`` so the
        misconfiguration is caught at construction rather than
        silently coerced. The lab convention is that fancy phase
        shapes (sinusoidal / halfpipe / bowtie / speedbumps / ramps)
        are research workflows that always involve asymmetric
        cathodic / anodic widths or amplitudes anyway, so coupling
        them to asymmetric mode keeps the GUI surface area small.

        In asymmetric mode:
          * ``shape`` sets the cathodic (first) phase's waveform.
          * ``shape2`` sets the anodic (recharge) phase's waveform;
            falls back to ``shape`` when omitted so a single dropdown
            can drive both phases if the user wants matching shapes
            with mismatched amplitudes / widths.
          * ``bump_count`` / ``bump_count2`` set the per-phase
            speedbump count when the corresponding shape is
            ``SHAPE_SPEEDBUMPS``.
        """
        if polarity not in (-1, +1):
            raise ValueError("polarity must be -1 (cathodic) or +1 (anodic)")
        if shape not in PHASE_SHAPES:
            raise ValueError(
                f"shape must be one of {PHASE_SHAPES}, got {shape!r}")
        # Both phases share the chosen shape in symmetric mode (since
        # symmetric means "phase 2 mirrors phase 1"). In asymmetric mode
        # the user can override the second phase's shape via shape2.
        a1 = polarity * abs(amplitude_ua)
        if symmetric or amplitude2_ua is None:
            a2 = -a1
            w2 = phase_width_us
            sh1 = shape
            sh2 = shape         # symmetric: both phases use the same shape
            bc1 = int(bump_count)
            bc2 = int(bump_count)
        else:
            a2 = -np.sign(a1) * abs(amplitude2_ua)
            w2 = phase_width2_us if phase_width2_us is not None else phase_width_us
            sh1 = shape
            sh2 = shape2 if shape2 is not None else shape
            if sh2 not in PHASE_SHAPES:
                raise ValueError(
                    f"shape2 must be one of {PHASE_SHAPES}, got {sh2!r}")
            bc1 = int(bump_count)
            bc2 = int(bump_count2 if bump_count2 is not None else bump_count)
        return cls(
            phases=[
                Phase(a1, phase_width_us, interphase_us,
                      shape=sh1, bump_count=bc1),
                Phase(a2, w2, discharge_us,
                      shape=sh2, bump_count=bc2),
            ],
            rate_hz=rate_hz,
        )

    @classmethod
    def triphasic(
        cls,
        amp_excite_ua: float,
        phase_width_us: float = 200.0,
        interphase_us: float = 20.0,
        discharge_us: float = 20.0,
        polarity: int = -1,
        rate_hz: float = 50.0,
        ratio=(2, 3, 1),
    ) -> "PulsePattern":
        """Triphasic with a per-phase MAGNITUDE ratio. Default 2:3:1
        from IEEE NER paper (the middle / excitation phase is 3× the
        outers).

        Sign convention — STRICT alternation, driven by ``polarity``:

          * Phase 1 takes ``polarity``.
          * Phase 2 takes ``-polarity``  (OPPOSITE of phases 1 and 3).
          * Phase 3 takes ``polarity``.

        So:

          * ``polarity = -1`` (cathodic-first) → cathodic / anodic /
            cathodic.
          * ``polarity = +1`` (anodic-first)   → anodic / cathodic /
            anodic.

        ``ratio`` carries MAGNITUDES ONLY — every entry must be > 0
        and is taken as ``abs(entry)`` if a signed value is supplied.
        A zero ratio entry would collapse that phase to no current
        (degenerating triphasic into "biphasic with a pause") and is
        rejected so the alternating-sign invariant is preserved.

        The output is rescaled so the largest-magnitude phase (the
        excitation phase) has amplitude ``|amp_excite_ua|``.
        """
        if polarity not in (-1, +1):
            raise ValueError("polarity must be -1 or +1")
        ratio = np.abs(np.asarray(ratio, dtype=float))
        if ratio.size != 3:
            raise ValueError("ratio must have exactly 3 entries")
        # Every entry must be strictly positive — a zero entry would
        # collapse that phase to no current and break the alternating-
        # sign invariant the GUI annotations rely on. Surface the
        # offending values so the user sees what's wrong.
        if np.any(ratio <= 0):
            raise ValueError(
                "all triphasic ratio entries must be > 0; got "
                f"({ratio[0]:g}, {ratio[1]:g}, {ratio[2]:g})"
            )
        excite_idx = int(np.argmax(ratio))
        unit = abs(amp_excite_ua) / ratio[excite_idx]
        magnitudes = ratio * unit
        # STRICT alternation: phase 1 = polarity, phase 2 = -polarity,
        # phase 3 = polarity. Magnitudes are the ABS-only ratio entries
        # scaled to the excitation amplitude; signs come ONLY from
        # this pattern, never from the ratio. This guarantees phase 2
        # is always the opposite polarity of phases 1 and 3.
        sign_pattern = (polarity, -polarity, polarity)
        amps = [s * m for s, m in zip(sign_pattern, magnitudes)]
        phases = [
            Phase(amps[0], phase_width_us, interphase_us),
            Phase(amps[1], phase_width_us, interphase_us),
            Phase(amps[2], phase_width_us, discharge_us),
        ]
        return cls(phases=phases, rate_hz=rate_hz)

    @classmethod
    def rect(
        cls,
        polarity: int = -1,
        triphasic: bool = False,
        amplitude_ua: float = 10.0,
        phase_width_us: float = 200.0,
        interphase_us: float = 20.0,
        discharge_us: float = 20.0,
        rate_hz: float = 50.0,
    ) -> "PulsePattern":
        """Convenience constructor used by experiments to start a sweep."""
        if triphasic:
            return cls.triphasic(
                amplitude_ua, phase_width_us, interphase_us, discharge_us,
                polarity=polarity, rate_hz=rate_hz,
            )
        return cls.biphasic(
            amplitude_ua, phase_width_us, interphase_us, discharge_us,
            polarity=polarity, rate_hz=rate_hz,
        )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def num_phases(self) -> int:
        return len(self.phases)

    @property
    def is_triphasic(self) -> bool:
        return self.num_phases == 3

    @property
    def total_pulse_us(self) -> float:
        return sum(p.width_us + p.delay_after_us for p in self.phases)

    @property
    def interpulse_gap_us(self) -> float:
        """Idle interpulse gap = repetition period − total pulse duration.

        The pulse repeats every ``period = 1e6 / rate_hz`` µs; the pulse
        itself occupies ``total_pulse_us`` (every phase width PLUS every
        interphase delay AND the trailing discharge delay).  Whatever is left
        over is the idle INTERPULSE window — the only stretch where the
        electrode sits at its rest potential (OCP).

        NOTE: this is distinct from the interphase / discharge delays.  A
        pattern can have a non-zero discharge delay and STILL have zero
        interpulse gap (the discharge delay is counted inside
        ``total_pulse_us``).  ``validate()`` guarantees ``total_pulse_us ≤
        period`` so the gap is never negative; a value at/near zero means
        "no idle interpulse exists" (see :meth:`has_interpulse_gap`).
        """
        if self.rate_hz <= 0:
            return 0.0
        return 1e6 / self.rate_hz - self.total_pulse_us

    def has_interpulse_gap(self, min_gap_us: float = 10.0) -> bool:
        """True iff a TRUSTWORTHY idle interpulse window exists.

        Used to guard every site that samples "the rest potential during the
        interpulse" (E_ret / E_act OCP, pre-trigger baselines, AC-settle
        flatness checks, learned-OCP recording).  When the repetition period
        is fully occupied by the pulse (gap ≤ ``min_gap_us``), there is no idle
        window — the "pre-pulse" / "post-discharge" samples are actually the
        neighbouring pulse's active data, so any code that treats them as the
        rest potential must instead decline (operator: "When there is no
        interpulse delay, then E_ret or E_act are never expected to be near
        zero during interpulse because there is no interpulse").

        The 10 µs floor (≈ one settle time / one scope leading division) is a
        practical minimum — ``auto_layout_for_pulse`` reserves ~1 division of
        leading baseline that is only genuinely idle if the gap is at least
        that wide.
        """
        return self.interpulse_gap_us > float(min_gap_us)

    @property
    def _excitation_index(self) -> int:
        """Index of the phase carrying the largest |Q_ph|.

        Selection uses the analytic per-phase ``charge_nc`` (peak × width ×
        shape-duty) — unchanged from the historical behaviour, so every
        existing caller of ``excitation_phase`` picks the same phase.  The
        REPORTED charge of that phase (``charge_per_phase_nc``) is the
        device-exact integral; only the SELECTION is analytic.
        """
        if not self.phases:
            return -1
        return max(range(len(self.phases)),
                   key=lambda i: abs(self.phases[i].charge_nc))

    @property
    def excitation_phase(self) -> Phase:
        """Phase carrying the largest |Q_ph| — the one that does the work.

        Triphasic patterns can have different per-phase widths so the
        phase with the largest *amplitude* magnitude isn't always the
        one with the largest *charge* magnitude. Charge per phase
        (``|amp × width|``) is the quantity that loads the electrode
        and the right thing to scale by during a ramp. For symmetric
        biphasic and the standard 2:-3:1 triphasic with equal phase
        widths the two definitions agree, so existing call sites
        keep their previous behaviour.
        """
        return max(self.phases, key=lambda p: abs(p.charge_nc))

    @property
    def polarity(self) -> int:
        """Sign of phase 1's amplitude. ``-1`` = cathodic-first
        (phase 1 cathodic), ``+1`` = anodic-first (phase 1 anodic).

        Standard electrochemistry convention: "cathodic-first" /
        "anodic-first" naming refers to the LEADING phase, not the
        excitation phase. For symmetric biphasic the two coincide;
        for triphasic 2:3:1 they differ — a cathodic-first triphasic
        runs (cathodic, anodic, cathodic) with the anodic middle
        phase being the dominant excitation, but it's still "cathodic-
        first" because phase 1 is cathodic.
        """
        if not self.phases:
            return -1
        return -1 if self.phases[0].amplitude_ua < 0 else +1

    @property
    def charge_per_phase_nc(self) -> float:
        """|Q_ph| of the *excitation* phase, nanocoulombs — IDEAL pattern.

        The reported per-phase charge is the true integral of the CONTINUOUS
        as-designed waveform (:func:`ideal_charge_nc` — trapezoidal, NO device
        30/100 nA quantization), so a linear-increasing 1000 µA / 200 µs reads
        a clean **100.0 nC** rather than the device staircase's 99.6 (operator:
        "For all charge metrics, use the ideal pattern.  Only use the realistic
        when comparing the error in the test parameters").  It handles a
        non-canonical ``tau_us`` / ``tail_zero_us`` / ``offset_ua`` via the
        breakpoints (unlike the analytic ``peak × width × _shape_duty``).  Every
        reported charge metric (Q_ph, Q_inj, Q_net, cumulative charge) routes
        through the ideal integral; the DEVICE staircase
        (``actual_phase_charges_nc``) is used ONLY to show the quantization
        ERROR in the pattern-preview / test-parameters panel.  The excitation
        phase is selected analytically (``_excitation_index``).
        """
        idx = self._excitation_index
        if idx < 0:
            return 0.0
        q = self.ideal_phase_charges_nc()
        return abs(q[idx]) if idx < len(q) else 0.0

    def ideal_phase_charges_nc(self) -> List[float]:
        """Per-phase charge (signed, nC) of the IDEAL continuous waveform — no
        device current quantization, trapezoidal integral (see
        :func:`ideal_charge_nc`).  The basis for every REPORTED charge metric.
        """
        n_samples = self.curved_sample_budget()
        return [ideal_charge_nc(ph, n_samples=n_samples) for ph in self.phases]

    # ------------------------------------------------------------------
    # Time-domain rendering (used by the simulator and for plotting)
    # ------------------------------------------------------------------
    def to_timeseries(self, t_pre_us: float = 100.0, t_post_us: float = 200.0,
                      sample_period_us: float = 0.5,
                      *, current_step_nA: int = 30,
                      ) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(time_us, current_ua)`` for the **Actual** waveform —
        sample-and-hold staircase exactly as the PlexStim device plays it.

        For each phase, the shape generator gives breakpoints
        ``(tₖ, aₖ)`` in time order. The device holds amplitude
        ``aₖ`` from ``tₖ`` until the next breakpoint at ``tₖ₊₁`` —
        i.e., piecewise-constant, NOT linearly interpolated. We
        also quantise each amplitude to the device's native 30 nA
        grid before plotting so the Actual trace shows what the
        electrode physically receives.

        For curved shapes (sin / halfpipe / exp-decay / ramps / bowtie)
        this rendering visibly differs from
        :meth:`to_timeseries_desired` — the staircase steps are the
        discrete approximation. For flat-segmented shapes
        (rectangular, speedbumps) the two coincide.
        """
        total = t_pre_us + self.total_pulse_us + t_post_us
        n = int(round(total / sample_period_us)) + 1
        t = np.linspace(-t_pre_us, total - t_pre_us, n)
        i = np.zeros_like(t)
        step_ua = float(current_step_nA) * 1e-3 if current_step_nA else 0.0
        # Match the per-phase breakpoint count the device will see when
        # ``_load_arbitrary`` writes this pattern, so the preview's
        # staircase IS what's actually delivered.
        n_samples = self.curved_sample_budget()
        cursor = 0.0
        for ph in self.phases:
            bps = shape_breakpoints(
                amplitude_ua=ph.amplitude_ua, width_us=ph.width_us,
                shape=ph.shape, bump_count=ph.bump_count,
                tau_us=ph.tau_us, n_samples=n_samples,
                tail_zero_us=getattr(ph, "tail_zero_us", 0.0),
            offset_ua=getattr(ph, "offset_ua", 0.0),
            )
            mask_phase = (t >= cursor) & (t < cursor + ph.width_us)
            if mask_phase.any():
                bp_times = np.array([cursor + b[0] for b in bps])
                bp_amps  = np.array([b[1] for b in bps])
                if step_ua > 0:
                    bp_amps = np.round(bp_amps / step_ua) * step_ua
                # Sample-and-hold: for each sample at time t, locate
                # the breakpoint whose time is at-or-before t and use
                # that breakpoint's amp. ``np.searchsorted`` with
                # ``side='right'`` then ``-1`` gives the right index.
                idx = np.searchsorted(bp_times, t[mask_phase], side="right") - 1
                idx = np.clip(idx, 0, len(bp_amps) - 1)
                i[mask_phase] = bp_amps[idx]
            cursor += ph.width_us + ph.delay_after_us
        return t, i

    def to_timeseries_desired(self, t_pre_us: float = 100.0,
                              t_post_us: float = 200.0,
                              sample_period_us: float = 0.5,
                              ) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(time_us, current_ua)`` for the *smooth mathematical
        ideal* — what we'd play if the device had infinite resolution
        and continuous-time output.

        Same time grid as :meth:`to_timeseries`; what differs is the
        per-phase rendering. Curved shapes (sin / halfpipe / exp-decay /
        speedbumps / bowtie) are evaluated point-wise on the smooth
        formula, not sample-and-held from breakpoints. The pattern
        preview overlays this on top of the staircase so the user sees
        Desired vs Actual at a glance.

        **Boundary doubling**: at every zero-delay phase transition the
        function emits a duplicate-time sample carrying the previous
        phase's terminal amplitude before the next phase's first
        sample. Pyqtgraph's default linear connectivity then draws a
        vertical jump at the boundary instead of a diagonal — fixing
        the "line dips through zero between phases" artefact for
        triphasic / no-delay rectangular patterns. The resulting
        ``t`` array is no longer strictly monotonic (the duplicate-
        time pair stays equal-time) but is monotonic non-decreasing
        — pyqtgraph handles that correctly.
        """
        total = t_pre_us + self.total_pulse_us + t_post_us
        n = int(round(total / sample_period_us)) + 1
        t = np.linspace(-t_pre_us, total - t_pre_us, n)
        i = np.zeros_like(t)
        # Track each phase's TERMINAL amplitude (the value that would
        # be sampled at exactly ``cursor + width_us`` if the mask
        # were inclusive on the right). Used by the boundary-doubling
        # post-process below to emit a vertical jump at zero-delay
        # transitions where the value was about to change abruptly.
        cursor = 0.0
        terminal_amps: list[tuple[float, float]] = []  # (boundary_time, end_amp)
        for ph in self.phases:
            mask = (t >= cursor) & (t < cursor + ph.width_us)
            if not mask.any():
                cursor += ph.width_us + ph.delay_after_us
                continue
            tau = ph.tau_us if ph.tau_us > 0 else ph.width_us / EXP_DECAY_TAU_RATIO
            tt = t[mask] - cursor          # 0..width within the phase
            W = float(ph.width_us)
            A = float(ph.amplitude_ua)
            s = ph.shape
            if s == SHAPE_RECTANGULAR:
                vals = np.full_like(tt, A)
                end_amp = A
            elif s == SHAPE_LINEAR_INCREASING:
                vals = A * tt / W
                end_amp = A
            elif s == SHAPE_LINEAR_DECREASING:
                vals = A * (1.0 - tt / W)
                end_amp = 0.0
            elif s == SHAPE_SINUSOIDAL:
                vals = A * np.sin(np.pi * tt / W)
                end_amp = 0.0
            elif s == SHAPE_BOWTIE:
                vals = A * np.abs(2.0 * tt / W - 1.0)
                end_amp = A
            elif s == SHAPE_HALFPIPE:
                vals = A * (1.0 - np.sin(np.pi * tt / W))
                end_amp = A
            elif s == SHAPE_SPEEDBUMPS:
                # Bi-level rectangular pattern: (2N+1) equal segments,
                # alternating intermediate (A/2) and peak (A).
                n_b = max(1, int(ph.bump_count))
                seg_w = W / (2 * n_b + 1)
                vals = np.full_like(tt, A / 2.0)
                for k in range(n_b):
                    # Peak segments are at odd indices 1, 3, 5, …
                    t0 = (2 * k + 1) * seg_w
                    t1 = t0 + seg_w
                    in_peak = (tt >= t0) & (tt < t1)
                    vals[in_peak] = A
                # Last segment is intermediate (A/2): index 2N.
                end_amp = A / 2.0
            elif s == SHAPE_EXP_DECAY:
                vals = A * np.exp(-tt / tau)
                end_amp = float(A * np.exp(-W / tau))
                # Honour ``tail_zero_us`` on the smooth-ideal path so
                # the Desired trace matches what the .pat actually
                # plays. Without this the preview would show the
                # natural exp-decay tail while the device plays a
                # zeroed tail — confusing.
                tz = float(getattr(ph, "tail_zero_us", 0.0))
                if tz > 0.0:
                    zero_start = max(0.0, W - tz)
                    vals = np.where(tt >= zero_start, 0.0, vals)
                    if W - tz <= 0:
                        end_amp = 0.0
            elif s == SHAPE_EXP_INCREASING:
                # Mirror image of exp-decay: I(t) = A · exp(−(W−t)/τ).
                # Starts at A·exp(−W/τ) (≈ 0.7 % of A at canonical
                # τ = W/5) and rises to peak A at end of phase.
                # See Yip et al. 2017 Fig 4.
                vals = A * np.exp(-(W - tt) / tau)
                end_amp = float(A)
            elif s == SHAPE_GAUSSIAN:
                # Zero-tapered Gaussian, σ = W/5 → peak at t = W/2
                # reaches the user's signed amplitude A; both
                # endpoints sit exactly at 0 (raw-Gaussian floor of
                # exp(−3.125) ≈ 0.044·A is subtracted out, then
                # rescaled so the peak still equals A). See
                # :func:`shape_breakpoints` for the matching
                # discrete renderer. Sahin & Tie 2007 Fig 1.
                sigma = W / 5.0 if W > 0 else 1.0
                raw = np.exp(-((tt - W * 0.5) / sigma) ** 2 / 2.0)
                endpoint = float(np.exp(-3.125))
                vals = A * (raw - endpoint) / (1.0 - endpoint)
                # Phase boundary now sits at exactly zero (raw
                # endpoint minus itself, scaled — algebraically 0).
                end_amp = 0.0
            else:
                vals = np.full_like(tt, A)
                end_amp = A
            # Apply the optional baseline-floor offset — mirrors the
            # ``_bps_with_offset`` transform inside ``shape_breakpoints``
            # so the smooth Desired curve agrees with what the device
            # plays (and with the Actual staircase) when an offset is
            # set. Without this, setting an offset left both Desired
            # AND Actual unchanged on the preview because the smooth-
            # ideal path computed ``vals`` from the analytic formula
            # and never applied the transform.
            offset = float(getattr(ph, "offset_ua", 0.0))
            if (abs(offset) > 1e-12 and abs(A) > 1e-12
                    and s != SHAPE_RECTANGULAR):
                sign = 1.0 if A >= 0 else -1.0
                so = sign * min(abs(offset), abs(A))
                # Linear blend: natural-min-of-zero lands at ``so``,
                # natural-peak-of-A stays at A. ``end_amp`` is the
                # phase's terminal natural amplitude — apply the same
                # transform so boundary-doubling carries the offset-
                # adjusted endpoint.
                vals = so + (A - so) * (vals / A)
                end_amp = so + (A - so) * (end_amp / A)
            i[mask] = vals
            terminal_amps.append((cursor + ph.width_us, end_amp))
            cursor += ph.width_us + ph.delay_after_us
        # Boundary doubling — only at zero-delay transitions. For
        # delayed transitions the gap is filled with the zero-amp
        # baseline (mask doesn't cover it, ``i`` stays at 0), which
        # already breaks any artificial diagonal.
        extra_t: list[float] = []
        extra_i: list[float] = []
        cursor = 0.0
        for ph_idx, ph in enumerate(self.phases):
            boundary = cursor + ph.width_us
            cursor = boundary + ph.delay_after_us
            if ph.delay_after_us > 0:
                continue   # zero-baseline gap handles the visual
            if ph_idx + 1 >= len(self.phases):
                continue   # last phase — no next phase to jump to
            if ph_idx >= len(terminal_amps):
                continue
            _, end_amp = terminal_amps[ph_idx]
            extra_t.append(boundary)
            extra_i.append(end_amp)
        if extra_t:
            t_full = np.concatenate([t, np.array(extra_t)])
            i_full = np.concatenate([i, np.array(extra_i)])
            # Stable sort by time keeps the synthetic boundary
            # samples just BEFORE the next phase's first sample
            # (which is at the same time), giving the desired
            # horizontal-then-vertical render.
            order = np.argsort(t_full, kind="stable")
            # Tweak: for ties we want the synthetic end-amp sample
            # FIRST. argsort is stable; the synthetic ones are
            # appended last, so they end up SECOND for ties. Swap
            # by walking ties and putting the appended index ahead
            # of the original.
            n_orig = len(t)
            order_list = order.tolist()
            # Build a (time, idx-from-orig?) sort key: tie-break
            # synthetic-first.
            order_list.sort(key=lambda k: (t_full[k], 0 if k >= n_orig else 1))
            order = np.array(order_list, dtype=int)
            t = t_full[order]
            i = i_full[order]
        return t, i

    def actual_vs_desired_accuracy_pct(self, *,
                                       sample_period_us: float = 0.05,
                                       current_step_nA: int = 30,
                                       ) -> float:
        """Fidelity of the Actual (sample-and-hold + hardware-quantised)
        waveform vs the Desired (continuous mathematical ideal),
        expressed as a percentage 0.0–100.0.

        Definition (normalised RMS):

        .. math::

            \\text{accuracy} = 100 \\cdot \\max\\!\\left(0, \\;
            1 - \\frac{\\sqrt{\\langle (I_\\text{actual} - I_\\text{desired})^2 \\rangle}}
                      {\\sqrt{\\langle I_\\text{desired}^2 \\rangle}}\\right)

        Both samplings cover the same active region (no pre/post pad)
        at ``sample_period_us`` granularity (0.05 µs default — fine
        enough to resolve the sub-µs sub-segments curved shapes break
        into). The actual is computed with hardware-quantised
        amplitudes on the 30 nA grid; the desired is the smooth math.

        Returns 100.0 for all-rectangular patterns (Actual ≡ Desired)
        and degenerate all-zero patterns. Lower values quantify how
        much the discrete approximation diverges — typically 99.0–
        99.95 % for curved shapes at our sample budgets, dropping
        toward 95–97 % only for very long phases on the steepest
        shapes (exp-decay with short τ, or many speedbumps).

        Used by the pattern preview's legend to surface the
        Actual-vs-Desired closeness as a one-number summary the
        operator can glance at, and exposed as a method so unit
        tests can pin it for regression coverage of the quantisation
        + sample-and-hold pipeline.
        """
        # Sample BOTH at the same time grid over the active region
        # only (t_pre = t_post = 0). The trailing interpulse gap and
        # any pre-pulse lead are not part of the pattern's signal —
        # including them would just dilute the metric with zeros.
        t_a, i_a = self.to_timeseries(
            t_pre_us=0.0, t_post_us=0.0,
            sample_period_us=sample_period_us,
            current_step_nA=current_step_nA,
        )
        t_d, i_d = self.to_timeseries_desired(
            t_pre_us=0.0, t_post_us=0.0,
            sample_period_us=sample_period_us,
        )
        # ``to_timeseries_desired`` post-processes its samples with
        # boundary-doubling (duplicate t at zero-delay phase
        # transitions, for the visual vertical jump). That makes
        # ``t_d`` non-uniform; resample onto the actual's uniform
        # grid via interp. np.interp's behaviour at duplicate t
        # values is well-defined (picks the right-most sample),
        # and the duplicate-t pairs are a tiny fraction of the
        # total samples so the impact on the RMS is negligible.
        if t_d.size != t_a.size or not np.allclose(t_d, t_a):
            i_d = np.interp(t_a, t_d, i_d)
        rms_des = float(np.sqrt(np.mean(i_d * i_d)))
        if rms_des < 1e-9:
            # Degenerate (all-zero) pattern — call it 100 % rather
            # than dividing by zero.
            return 100.0
        rms_err = float(np.sqrt(np.mean((i_a - i_d) ** 2)))
        return float(min(100.0, max(0.0, 100.0 * (1.0 - rms_err / rms_des))))

    def scaled(self, factor: float) -> "PulsePattern":
        """Return a copy with all phase amplitudes scaled (used by the ramp loop)."""
        new = PulsePattern(
            phases=[p.scaled(factor) for p in self.phases],
            rate_hz=self.rate_hz,
            repetitions=self.repetitions,
        )
        return new

    # ------------------------------------------------------------------
    # Arbitrary-pattern budget
    # ------------------------------------------------------------------
    def curved_sample_budget(self, *, max_pairs: int = _PAT_MAX_PAIRS) -> int:
        """Per-curved-phase breakpoint count, sized to the .pat 499-pair cap.

        The Plexon ``.pat`` Variable format takes up to ``max_pairs``
        ``(amp_nA, duration_µs)`` records per channel. We allocate that
        budget across the phases of one pulse:

          * **Rectangular** phases use exactly 1 pair each (2 breakpoints
            → 1 hold record).
          * **Speedbumps** uses 9 pairs (10 breakpoints, including
            duplicate-time segment boundaries that become 1 µs glitches
            in the .pat).
          * **Curved** phases (linear ramp / sinusoidal / bowtie /
            halfpipe / exp-decay) get the *remaining* budget split
            evenly. ``n`` breakpoints generate ``n − 1`` pairs, so
            ``n_curved · (n − 1) ≤ remaining`` ⇒ ``n ≤ remaining /
            n_curved + 1``.
          * Each ``delay_after_us > 0`` adds one zero-amplitude pair
            for the gap.

        Returns ``_DEFAULT_CURVED_SAMPLES`` when there are no curved
        phases (the value is unused in that case anyway). Floor of 2
        keeps every curved phase at least playable when the budget is
        unusually tight.
        """
        CURVED = {SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING,
                  SHAPE_SINUSOIDAL, SHAPE_BOWTIE, SHAPE_HALFPIPE,
                  SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING, SHAPE_GAUSSIAN}
        fixed_pairs = 0
        n_curved = 0
        for ph in self.phases:
            s = ph.shape
            if s == SHAPE_RECTANGULAR:
                fixed_pairs += 1
            elif s == SHAPE_SPEEDBUMPS:
                fixed_pairs += 9
            elif s in CURVED:
                n_curved += 1
            else:
                fixed_pairs += 1   # unknown shapes fall back to rect-like
            if ph.delay_after_us > 0:
                fixed_pairs += 1
        if n_curved == 0:
            return _DEFAULT_CURVED_SAMPLES
        remaining = max(1, int(max_pairs) - fixed_pairs)
        # Pair-count budget per curved phase. Linear shapes
        # (SHAPE_LINEAR_INCREASING / SHAPE_LINEAR_DECREASING) emit
        # ``n_samples + 1`` breakpoints → ``n_samples`` pairs; sine /
        # bowtie / exp / gaussian emit ``n_samples`` breakpoints →
        # ``n_samples - 1`` pairs. To stay safe under the worst case
        # (linear), allocate ``per_curved`` so that ``per_curved``
        # pairs per phase fit: ``per_curved = remaining // n_curved``
        # (no +1). Previously this formula added +1 — fine for the
        # sin-family budget (where the +1 cancels the −1 pair drop)
        # but pushed LINEAR shapes over by 1 pair, tripping the
        # 499-pair validator on otherwise valid patterns (e.g.
        # mix-and-match with a single linear-increasing phase).
        per_curved = remaining // n_curved
        return max(2, per_curved)

    # ------------------------------------------------------------------
    # Charge balance
    # ------------------------------------------------------------------
    @property
    def net_charge_nc(self) -> float:
        """Signed sum of per-phase charges, nanocoulombs — IDEAL pattern.

        0 = perfectly charge-balanced on the as-designed waveform.  Uses the
        IDEAL continuous integral (:meth:`ideal_phase_charges_nc`) per the
        operator's "charge metrics use the ideal pattern" rule, so a symmetric
        biphasic reads a clean 0.  The DEVICE-realistic residual (from the
        30/100 nA quantization) is a separate quantity shown as the error in
        the pattern-preview panel; ``auto_balance`` still minimizes the DEVICE
        integral internally (``actual_phase_charges_nc``) so the delivered
        charge is balanced — this reported metric is the ideal-waveform net.
        """
        return float(sum(self.ideal_phase_charges_nc()))

    def actual_phase_charges_nc(self, *, current_step_nA: int = 30,
                                max_pairs: int = _PAT_MAX_PAIRS,
                                ) -> List[float]:
        """Per-phase charge as the device actually plays it — discrete
        staircase integral, with the same breakpoint budget that
        ``_load_arbitrary`` will use for this pattern.
        """
        n_samples = self.curved_sample_budget(max_pairs=max_pairs)
        return [actual_charge_nc(ph, current_step_nA=current_step_nA,
                                  n_samples=n_samples)
                for ph in self.phases]

    # ------------------------------------------------------------------
    # Current quantization grid
    # ------------------------------------------------------------------
    def device_current_step_nA(self) -> int:
        """Current-quantization grid (nA) this pattern is rendered on.

        Operator: "keep the current resolution at 0.1 µA for rectangular
        shapes.  I do not trust the 30 nA resolution of the stimulator but
        will use it for non-rectangular shapes."  A pattern whose phases
        are ALL rectangular is rounded to the trusted 0.1 µA (100 nA)
        grid; any non-rectangular (ramp / sine / bowtie / halfpipe /
        speedbumps / exp / gaussian) phase drops to the device's native
        30 nA resolution so the curve renders smoothly.  An empty pattern
        defaults to the rectangular grid.

        Used as the amplitude grid in :func:`build_pat_pairs` (the device
        ``.pat``), the validation floor in :meth:`validate`, and the
        charge-balance grid in :meth:`auto_balance`, so all three agree on
        the resolution the device will actually receive.
        """
        from .config import STIM_CURRENT_STEP_RECT_NA, STIM_CURRENT_STEP_FINE_NA
        all_rect = all(ph.shape == SHAPE_RECTANGULAR for ph in self.phases)
        return (STIM_CURRENT_STEP_RECT_NA if all_rect
                else STIM_CURRENT_STEP_FINE_NA)

    # ------------------------------------------------------------------
    # Hardware-side validation
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """Sanity-check this pattern against PlexStim 2.0 hardware limits.

        Raises ``ValueError`` with a descriptive message if anything is
        out of range. Called by the runner just before
        :meth:`Stimulator.load_channel` so a bad pattern never reaches
        the DLL — the MATLAB code's defence against the family of
        firmware crashes that happen when an out-of-range parameter
        gets through.

        Checks:
        * at least one phase
        * each phase has a positive width (≥ 1 µs hardware resolution)
        * each amplitude within ±1000 µA (PlexStim 2.0 hardware limit)
        * delay_after_us ≥ 0
        * rate_hz > 0 and the period (1e6 / rate) is at least the total
          pulse length (otherwise the next pulse would start before this
          one finishes)
        * repetitions ≥ 0 (Plexon convention: 0 = infinite)
        """
        from .config import (
            STIM_MAX_AMPLITUDE_UA, STIM_TIME_RESOLUTION_US,
            STIM_CURRENT_STEP_RECT_NA, STIM_CURRENT_STEP_FINE_NA,
        )
        if not self.phases:
            raise ValueError("Pattern has no phases — nothing to send to the device.")
        for n, ph in enumerate(self.phases, 1):
            if ph.width_us < STIM_TIME_RESOLUTION_US:
                raise ValueError(
                    f"Phase {n}: width {ph.width_us} µs is below the "
                    f"{STIM_TIME_RESOLUTION_US} µs hardware resolution.")
            if abs(ph.amplitude_ua) > STIM_MAX_AMPLITUDE_UA + 1e-6:
                raise ValueError(
                    f"Phase {n}: amplitude {ph.amplitude_ua:+.2f} µA exceeds "
                    f"the {STIM_MAX_AMPLITUDE_UA:g} µA PlexStim limit.")
            # Reject sub-resolution amplitudes that aren't exactly zero —
            # 0 is fine (e.g. a discharge phase) but a value below this
            # phase's current grid gets silently rounded and the user
            # wouldn't know.  Floor is PER-SHAPE: 0.1 µA for a rectangular
            # phase, 30 nA for a shaped one (operator: trust 0.1 µA for
            # rectangular, the device's 30 nA only for non-rectangular).
            _res_ua = ((STIM_CURRENT_STEP_RECT_NA
                        if ph.shape == SHAPE_RECTANGULAR
                        else STIM_CURRENT_STEP_FINE_NA) / 1000.0)
            if (ph.amplitude_ua != 0.0
                    and abs(ph.amplitude_ua) < _res_ua - 1e-9):
                raise ValueError(
                    f"Phase {n}: amplitude {ph.amplitude_ua:+.3f} µA is "
                    f"below the {_res_ua:g} µA current resolution for a "
                    f"{ph.shape} phase.")
            if ph.delay_after_us < 0:
                raise ValueError(
                    f"Phase {n}: delay_after_us {ph.delay_after_us} µs "
                    f"is negative.")
            if ph.shape not in PHASE_SHAPES:
                raise ValueError(
                    f"Phase {n}: shape {ph.shape!r} is not a recognised "
                    f"phase shape. Expected one of {PHASE_SHAPES}.")
            if ph.shape == SHAPE_SPEEDBUMPS and ph.bump_count < 1:
                raise ValueError(
                    f"Phase {n}: speedbumps shape requires bump_count ≥ 1, "
                    f"got {ph.bump_count}.")
        if self.rate_hz <= 0:
            raise ValueError(f"rate_hz must be positive, got {self.rate_hz}.")
        period_us = 1e6 / self.rate_hz
        if self.total_pulse_us > period_us + 1e-6:
            raise ValueError(
                f"Pulse total ({self.total_pulse_us:.0f} µs) is longer than "
                f"one period ({period_us:.0f} µs at {self.rate_hz:g} pps). "
                f"Either lower the rate or shorten the phases / delays.")
        if self.repetitions < 0:
            raise ValueError(
                f"repetitions must be ≥ 0 (0 = infinite), got "
                f"{self.repetitions}.")

    def auto_balance(self, adjust: str = "last_amp",
                     *, current_step_nA: Optional[int] = None) -> "PulsePattern":
        """Return a copy in which the net charge is forced to EXACTLY
        zero against the device's quantization grids.

        ``current_step_nA`` is the current grid the balance is computed
        on; ``None`` (the default) derives it from the pattern's shape via
        :meth:`device_current_step_nA` — 100 nA (0.1 µA) for an
        all-rectangular pattern, 30 nA when any phase is shaped — so the
        balance is computed on the SAME grid :func:`build_pat_pairs` will
        render the device ``.pat`` on.  Pass an explicit value to override
        (tests / special cases).

        ``adjust`` selects which knob is rewritten to absorb the imbalance:

        * ``"last_amp"`` — recompute the *amplitude* of the final phase
          so the sum of QUANTIZED phase charges is zero. The head
          phases' amps are quantized to the ``current_step_nA`` grid
          before the balance is computed; the resulting last-phase amp is
          then itself quantized to the same grid, with a ±1-step search to
          absorb any rounding residual. Result: the QUANTIZED sum is
          exactly zero whenever the grid permits, and within ½ step
          otherwise.
        * ``"last_width"`` — recompute the *width* of the final phase
          on the 1 µs hardware grid using the same balance-against-
          quantized-head-then-search-for-residual pattern. Sign of the
          new width follows the original (we only ever produce
          positive widths).

        Phases other than the chosen one are passed through unchanged.
        Raises :class:`ValueError` if the adjustment can't reach balance
        (e.g. asking for ``"last_width"`` when the final amplitude is 0).
        """
        if current_step_nA is None:
            current_step_nA = self.device_current_step_nA()
        if not self.phases:
            return PulsePattern(phases=[], rate_hz=self.rate_hz,
                                repetitions=self.repetitions)
        head = self.phases[:-1]
        last = self.phases[-1]
        # Quantize the HEAD phase amplitudes to the device current
        # grid so the auto-balance is computed against what the device
        # will actually deliver (not the user-typed continuous values).
        # ``sum_head_q`` is in nA·µs so charge_nc = sum_head_q * 1e-3.
        step_ua = float(current_step_nA) * 1e-3 if current_step_nA else 0.0
        def _q_amp(a: float) -> float:
            return round(a / step_ua) * step_ua if step_ua > 0 else a

        def _signed_offset(a: float, shape: str, offset_ua: float) -> float:
            """Signed offset clamped to |a|. Zero for rectangular shapes
            (offset has no effect since the natural amplitude is
            constant at A). Sign tracks ``a``'s sign so the offset
            lifts the natural-floor toward A — same convention as
            ``_bps_with_offset`` inside :func:`shape_breakpoints`."""
            if shape == SHAPE_RECTANGULAR or a == 0:
                return 0.0
            return (1.0 if a >= 0 else -1.0) * min(abs(offset_ua), abs(a))

        # Shape-aware, offset-aware balance: each phase contributes
        # ``W · (A · duty + so · (1 − duty))`` to the integrated charge,
        # where ``so`` is the per-phase signed offset (zero for
        # rectangular shapes since offset has no effect there). The
        # offset term is the contribution from the baseline-floor
        # region of trapezoidal-shape pulses; without accounting for
        # it, an offset on the head phases would leak into the net
        # charge and ``auto_balance`` would compute a stale "balance"
        # ignoring the offset. For shapes with duty == 1 (rect), the
        # ``(1 − duty)`` factor cancels the offset term automatically.
        last_duty = _shape_duty(last.shape, bump_count=last.bump_count)
        sum_head_q = sum(
            p.width_us * (
                _q_amp(p.amplitude_ua)
                * _shape_duty(p.shape, bump_count=p.bump_count)
                + _signed_offset(_q_amp(p.amplitude_ua), p.shape,
                                 getattr(p, "offset_ua", 0.0))
                * (1.0 - _shape_duty(p.shape, bump_count=p.bump_count))
            )
            for p in head
        )
        if adjust == "last_amp":
            if last.width_us <= 0:
                raise ValueError("Cannot balance via amplitude when the final "
                                 "phase has zero width.")
            # Continuous-math balance against quantized head.
            # Shape-aware + offset-aware: the last phase's actual
            # integrated charge is
            # ``W · (A · last_duty + so_last · (1 − last_duty))``.
            # ``so_last`` depends on sign(A_last) — which we don't
            # know yet — so we estimate it as opposite to the head's
            # sign (the natural direction needed to balance) and
            # treat its magnitude as the user-typed
            # ``last.offset_ua`` (correct whenever |A_last| ends up
            # ≥ offset, which is the common case). Substituting,
            # the balance equation is linear in A_last:
            #   sum_head_q + W · sign_last · offset · (1 − duty)
            #     + W · A · duty = 0
            # ⇒ A = −[sum_head_q + W · so_last · (1 − duty)] /
            #        (W · duty).
            last_offset_q = 0.0
            if (last.shape != SHAPE_RECTANGULAR
                    and float(getattr(last, "offset_ua", 0.0)) > 0):
                expected_sign = -1.0 if sum_head_q > 0 else 1.0
                last_offset_q = (last.width_us * expected_sign
                                 * float(last.offset_ua)
                                 * (1.0 - last_duty))
            denom = last.width_us * last_duty if last_duty > 0 else last.width_us
            new_amp_continuous = (
                -(sum_head_q + last_offset_q) / denom if denom > 0 else 0.0
            )
            # Quantize to the device grid, then ±1-step search to find
            # the candidate that minimises the ACTUAL DISCRETE net
            # charge that the .pat staircase will deliver — NOT an
            # analytic-duty estimate. The two diverge for curved
            # shapes (linear ramp / sin / Gaussian / exp-decay) by
            # the left-Riemann discretisation error, which for a
            # 200-sample 200-µs phase at 200 µA is roughly
            # |A|·W / (2·n_samples) ≈ 100 pC per phase. Driving the
            # search by ``actual_phase_charges_nc`` (the same
            # function the runner / device delivers) closes that gap
            # — typical post-refinement residual is below the 30 pC
            # device quantum.
            #
            # Computational cost: 3 candidate evaluations (centre,
            # −1 step, +1 step), each calling ``actual_charge_nc``
            # once per phase. Cheap (~tens of µs on a typical pattern).
            if step_ua > 0:
                center = round(new_amp_continuous / step_ua) * step_ua

                def _net_for_amp(cand_amp: float) -> float:
                    """Net discrete charge if last_phase.amplitude_ua = cand_amp,
                    in nC."""
                    cand_last = Phase(
                        cand_amp, last.width_us, last.delay_after_us,
                        shape=last.shape, bump_count=last.bump_count,
                        tau_us=last.tau_us,
                        offset_ua=getattr(last, "offset_ua", 0.0),
                        tail_zero_us=getattr(last, "tail_zero_us", 0.0))
                    cand_pat = PulsePattern(
                        phases=[*head, cand_last],
                        rate_hz=self.rate_hz,
                        repetitions=self.repetitions)
                    return sum(cand_pat.actual_phase_charges_nc(
                        current_step_nA=current_step_nA))

                # Linear-correction pre-refinement. The continuous-
                # analytic solve gives an initial ``center`` that's
                # off by up to ~150 pC for curved shapes (left-
                # Riemann discretisation error). Since one quantum
                # step adjusts Q by only ~1.5 pC per step (for
                # W=100µs/duty=0.5/step_ua=0.03µA), the ±1-step
                # search alone can't span larger errors. Use the
                # gradient ``∂Q/∂A = W · duty`` to nudge ``center``
                # toward the discrete optimum in ONE big step,
                # then let the ±1-step search absorb the rounding
                # residual. This converges to sub-quantum residual
                # for shapes whose discrete-duty deviation from
                # ``_shape_duty`` is well-behaved (essentially all
                # the supported shapes — the discretisation just
                # systematically over- or under-counts by a small
                # fraction).
                if abs(denom) > 1e-9:
                    center_resid_nc = _net_for_amp(center)
                    # Δamp (µA) such that (Δamp · W · duty) · 1e-3
                    # cancels the residual (which is in nC =
                    # µA·µs·1e-3). So Δamp = −resid_nc · 1000 /
                    # denom.
                    correction_ua = -center_resid_nc * 1000.0 / denom
                    new_center = center + correction_ua
                    # Re-quantize to the device grid.
                    center = round(new_center / step_ua) * step_ua

                best_amp = center
                best_resid = abs(_net_for_amp(center))
                for delta in (-step_ua, +step_ua):
                    cand = center + delta
                    resid = abs(_net_for_amp(cand))
                    if resid < best_resid:
                        best_amp = cand
                        best_resid = resid
                new_amp = best_amp
            else:
                new_amp = new_amp_continuous
            new_last = Phase(new_amp, last.width_us, last.delay_after_us,
                             shape=last.shape, bump_count=last.bump_count,
                             tau_us=last.tau_us,
                             offset_ua=getattr(last, "offset_ua", 0.0),
                             tail_zero_us=getattr(last, "tail_zero_us", 0.0))
        elif adjust == "last_width":
            if last.amplitude_ua == 0:
                raise ValueError("Cannot balance via width when the final "
                                 "phase amplitude is zero.")
            # Quantize the LAST phase amp the same way the device will,
            # then balance widths against the quantized head charge so
            # the resulting net charge is exactly zero.
            last_amp_q = _q_amp(last.amplitude_ua)
            if last_amp_q == 0:
                # Quantization collapsed last amp to zero — fall back
                # to the continuous formula and let the caller see the
                # same error path it always did when amp == 0.
                raise ValueError("Cannot balance via width when the final "
                                 "phase amplitude quantizes to zero.")
            # Shape-aware + offset-aware: with A_last known (and
            # already-quantized), ``so_last`` is fully determined.
            # ``W · (A_q · duty + so_last · (1 − duty)) = −sum_head_q``
            # ⇒ ``W = −sum_head_q /
            #         (A_q · duty + so_last · (1 − duty))``.
            so_last = _signed_offset(
                last_amp_q, last.shape,
                getattr(last, "offset_ua", 0.0))
            denom = (last_amp_q * last_duty
                     + so_last * (1.0 - last_duty))
            new_width_continuous = -sum_head_q / denom if denom != 0 else 0.0
            if new_width_continuous <= 0:
                raise ValueError("Width-based balance would require a negative "
                                 "duration; flip the polarity or use "
                                 "amplitude-based balance instead.")
            # Width grid is 1 µs (hardware resolution). Same
            # discrete-aware ±1-step search as the last_amp branch —
            # minimise the ACTUAL net charge that
            # ``actual_phase_charges_nc`` reports, not the analytic
            # ``sum_head_q + denom · W`` proxy. Closes the same
            # left-Riemann residual gap for width-locked balance.
            width_step = 1.0
            center = round(new_width_continuous / width_step) * width_step

            def _net_for_w(cand_w: float) -> float:
                """Net discrete charge if last_phase.width_us = cand_w, in nC."""
                cand_last = Phase(
                    last.amplitude_ua, cand_w, last.delay_after_us,
                    shape=last.shape, bump_count=last.bump_count,
                    tau_us=last.tau_us,
                    offset_ua=getattr(last, "offset_ua", 0.0),
                    tail_zero_us=getattr(last, "tail_zero_us", 0.0))
                cand_pat = PulsePattern(
                    phases=[*head, cand_last],
                    rate_hz=self.rate_hz,
                    repetitions=self.repetitions)
                return sum(cand_pat.actual_phase_charges_nc(
                    current_step_nA=current_step_nA))

            # Linear-correction pre-refinement — same idea as the
            # last_amp branch but the gradient is ``∂Q/∂W = A · duty +
            # so · (1 − duty)`` (i.e. the existing ``denom`` term in
            # the analytic formula). Bridge the analytic-vs-discrete
            # gap in one big step before the ±1-µs fine-tune so
            # curved-shape residuals collapse to sub-quantum.
            if abs(denom) > 1e-9:
                center_resid_nc = _net_for_w(center)
                correction_us = -center_resid_nc * 1000.0 / denom
                new_center = center + correction_us
                # Re-quantize to the 1 µs hardware grid.
                center = round(new_center / width_step) * width_step
                if center <= 0:
                    center = width_step   # never produce a non-positive width

            best_w = center
            best_resid = abs(_net_for_w(center))
            for delta in (-width_step, +width_step):
                cand = center + delta
                if cand <= 0:
                    continue
                resid = abs(_net_for_w(cand))
                if resid < best_resid:
                    best_w = cand
                    best_resid = resid
            new_last = Phase(last.amplitude_ua, best_w, last.delay_after_us,
                             shape=last.shape, bump_count=last.bump_count,
                             tau_us=last.tau_us,
                             offset_ua=getattr(last, "offset_ua", 0.0),
                             tail_zero_us=getattr(last, "tail_zero_us", 0.0))
        else:
            raise ValueError(f"Unknown adjust mode {adjust!r}")
        return PulsePattern(phases=[*head, new_last],
                            rate_hz=self.rate_hz,
                            repetitions=self.repetitions)


# ---------------------------------------------------------------------------
# PlexStim arbitrary-waveform (.pat) builder + validator
# ---------------------------------------------------------------------------
def build_pat_pairs(pattern: "PulsePattern", *,
                    max_pairs: int = _PAT_MAX_PAIRS,
                    ) -> List[Tuple[int, int]]:
    """Build the ``.pat`` (amp_nA, duration_µs) pair list for ``pattern``.

    This is the pure / side-effect-free core of
    :meth:`stimtest.hardware.plexon.PlexStim._load_arbitrary` — pulled
    out so the constraint logic can be exercised without a running
    DLL. The :class:`PlexStim` class composes the same per-phase
    ``shape_breakpoints`` rendering, then adds zero-amplitude pairs
    for any non-zero ``delay_after_us``. The output is structured
    exactly as the device expects:

    * Each pair is held for ``duration_µs`` at ``amp_nA`` (sample-and-
      hold staircase). ``amp_nA`` is a signed integer; negative =
      cathodic.
    * Maximum :data:`_PAT_MAX_PAIRS` pairs total — the PlexStim 2.0
      SDK rejects longer patterns.
    * Every duration is at least 1 µs (the firmware refuses
      ``"0 nA for 0 µs"`` records on some revisions, and 1 µs is the
      hardware time resolution).

    Pair counts are bounded by :meth:`PulsePattern.curved_sample_budget`
    which already partitions ``max_pairs`` across the phases. The
    output is also passed through :func:`validate_pat_pairs` before
    return so any miscalculation surfaces here rather than as a
    silent-truncation on the device.
    """
    from .config import STIM_CURRENT_STEP_RECT_NA, STIM_CURRENT_STEP_FINE_NA
    n_samples = pattern.curved_sample_budget(max_pairs=max_pairs)
    pairs: List[Tuple[int, int]] = []
    for ph in pattern.phases:
        # Current-quantization grid this PHASE's amplitude is rounded to,
        # keyed on its SHAPE (operator: "keep the current resolution at
        # 0.1 µA for rectangular shapes … will use [30 nA] for
        # non-rectangular shapes"): a rectangular phase lands on the
        # trusted 0.1 µA (100 nA) grid; a shaped phase uses the device's
        # native 30 nA so the curve renders smoothly.  Per-phase (not
        # per-pattern) so a rectangular phase inside a mixed pulse — e.g.
        # the rect cathodic of a rect+exp-decay cap-coupled pair — still
        # gets the 0.1 µA grid.
        _step_nA = (STIM_CURRENT_STEP_RECT_NA
                    if ph.shape == SHAPE_RECTANGULAR
                    else STIM_CURRENT_STEP_FINE_NA)
        # Skip phases whose duration is zero — nothing to play and
        # the firmware rejects "0 nA for 0 µs" pairs.
        if ph.width_us <= 0 and ph.delay_after_us <= 0:
            continue
        # Per-phase sample-count clamp. The PlexStim hardware refuses
        # sub-µs durations, so ``build_pat_pairs`` already rounds each
        # segment up with ``max(1, int(round(...)))``. Without a per-
        # phase cap on ``n_samples``, that rounding can inflate the
        # phase's playback duration arbitrarily: a 200-µs phase with
        # n_samples=498 produces 498 segments of 0.4 µs each, every
        # one clamped to 1 µs, for a total of 498 µs (2.5× the
        # intended width). Capping at ``int(W)`` keeps each natural
        # segment ≥ 1 µs so the rounded total stays close to the
        # intended phase width. The floor at 2 keeps every curved
        # phase at least playable for very narrow widths.
        if ph.width_us > 0:
            ph_n_samples = max(2, min(n_samples, int(round(float(ph.width_us)))))
            bps = shape_breakpoints(
                amplitude_ua=ph.amplitude_ua,
                width_us=ph.width_us,
                shape=ph.shape,
                bump_count=ph.bump_count,
                tau_us=ph.tau_us,
                n_samples=ph_n_samples,
                tail_zero_us=getattr(ph, "tail_zero_us", 0.0),
                offset_ua=getattr(ph, "offset_ua", 0.0),
            )
            # Convert (time, amp) breakpoints into (amp_nA, dur_us) pairs
            # by walking consecutive points. The last breakpoint is the
            # phase boundary marker — its amplitude doesn't get held
            # within the phase, so we stop one short.
            #
            # Use CUMULATIVE rounding to integer microseconds: each pair's
            # end-time is the rounded running cumulative time, so the
            # phase's total duration always tracks ``int(round(W))``
            # exactly. Previously each segment was rounded independently
            # with ``max(1, int(round(t_next - t_k)))`` which dropped the
            # fractional remainder on every segment — for a 500 µs phase
            # split into 496 segments of natural width ~1.008 µs, every
            # segment was clamped to 1 µs and 4 µs of duration vanished.
            # Cumulative rounding distributes the rounding error across
            # the segments (some 1 µs, some 2 µs) so the total is exact.
            # Each segment still respects the ≥ 1 µs hardware minimum.
            cursor_int = 0   # cumulative integer-µs time within this phase
            phase_target_us = int(round(float(ph.width_us)))
            for k in range(len(bps) - 1):
                t_k, a_k = bps[k]
                t_next, _ = bps[k + 1]
                # Snap the segment's end-time to the integer µs grid using
                # the cumulative running float-time. Floor below by
                # cursor_int + 1 so each pair takes at least 1 µs (the
                # hardware minimum).
                next_int = int(round(float(t_next)))
                # On the very last pair of this phase, force the end-time
                # to exactly the phase target so the total duration
                # equals ``W`` regardless of small rounding drifts.
                if k == len(bps) - 2:
                    next_int = phase_target_us
                next_int = max(cursor_int + 1, next_int)
                duration_us = next_int - cursor_int
                # Quantize to the pattern's current grid (0.1 µA for
                # rectangular, 30 nA for shaped) rather than the raw 1 nA
                # the .pat format allows — so a rectangular amplitude lands
                # on the trusted 0.1 µA grid instead of an arbitrary value
                # the operator doesn't trust the device to deliver.
                amp_nA = (int(round(float(a_k) * 1000.0 / _step_nA))
                          * _step_nA)
                pairs.append((amp_nA, duration_us))
                cursor_int = next_int
        # Inter-phase / discharge / post-phase delay — held at 0 nA.
        # Skip when delay_after_us is 0; some firmware revs reject
        # "0 nA for 0 µs" pairs. Round up to the 1 µs hardware grid.
        if ph.delay_after_us > 0:
            pairs.append((0, max(1, int(round(ph.delay_after_us)))))
    validate_pat_pairs(pairs, max_pairs=max_pairs)
    return pairs


def validate_pat_pairs(pairs: List[Tuple[int, int]], *,
                       max_pairs: int = _PAT_MAX_PAIRS) -> None:
    """Sanity-check a ``.pat`` pair list against the PlexStim 2.0 SDK.

    Raises ``ValueError`` with a human-readable message if anything is
    out of range. Called automatically by :func:`build_pat_pairs` so
    callers don't normally need to invoke it directly — exposed
    publicly for tests and for any code that synthesises pairs
    outside the standard pattern path.

    Constraints enforced:

    * **Pair count** — at least 1, at most :data:`_PAT_MAX_PAIRS`
      (499). Longer lists silently truncate on the device.
    * **Duration** — every duration is a positive integer (≥ 1 µs).
      The firmware refuses ``"0 nA for 0 µs"`` records on some
      revisions; 0-duration pairs would otherwise propagate from a
      misuse of ``build_pat_pairs``.
    * **Amplitude** — every amplitude fits in the int32 nA range that
      the SDK ABI accepts (the runtime check uses the project-wide
      ``STIM_MAX_AMPLITUDE_UA`` ceiling × 1000 nA/µA, with a small
      margin for rounding).
    * **Numeric type** — both fields are plain ``int`` so the JSON-
      style serialisation in :meth:`PlexStim._load_arbitrary` doesn't
      emit ``5.0`` where the parser wants ``5``.
    """
    from .config import STIM_MAX_AMPLITUDE_UA
    if not pairs:
        raise ValueError("build_pat_pairs produced 0 pairs — "
                         "PlexStim won't accept an empty pattern.")
    if len(pairs) > max_pairs:
        raise ValueError(
            f"build_pat_pairs produced {len(pairs)} pairs but the "
            f"PlexStim .pat format caps at {max_pairs}. Reduce the "
            f"per-curved-phase sample count or split the pattern.")
    amp_limit_nA = int(round(STIM_MAX_AMPLITUDE_UA * 1000.0)) + 1  # +1 nA margin
    for k, pair in enumerate(pairs):
        if not (isinstance(pair, tuple) and len(pair) == 2):
            raise ValueError(
                f"Pair {k} is {pair!r} — expected a (amp_nA, dur_us) tuple.")
        amp_nA, dur_us = pair
        if not isinstance(amp_nA, int) or not isinstance(dur_us, int):
            raise ValueError(
                f"Pair {k} = ({amp_nA!r}, {dur_us!r}) — both fields "
                f"must be plain ints (no floats / numpy scalars).")
        if dur_us < 1:
            raise ValueError(
                f"Pair {k}: duration {dur_us} µs is below 1 µs — the "
                f"PlexStim firmware refuses zero-length records.")
        if abs(amp_nA) > amp_limit_nA:
            raise ValueError(
                f"Pair {k}: amplitude {amp_nA} nA exceeds the "
                f"±{amp_limit_nA} nA PlexStim hardware limit "
                f"({STIM_MAX_AMPLITUDE_UA:g} µA).")


def format_pat_lines(pairs: List[Tuple[int, int]]) -> List[str]:
    """Render ``.pat`` pairs as the line-oriented Variable format.

    The PlexStim 2.0 SDK reads a text file whose first line is
    literally ``Variable`` and whose subsequent lines alternate amp
    (nA) and duration (µs) values, one per line, terminated by a
    final newline. Returns the list of lines (caller is responsible
    for joining and newline-terminating).
    """
    lines = ["Variable"]
    for amp_nA, dur_us in pairs:
        lines.append(str(int(amp_nA)))
        lines.append(str(int(dur_us)))
    return lines


# ---------------------------------------------------------------------------
# PlexStim Fixed-format (.pat) builder + validator
# ---------------------------------------------------------------------------
def build_pat_samples_fixed(pattern: "PulsePattern", *,
                            sample_period_us: int = 1,
                            max_points: int = _PAT_MAX_FIXED_POINTS,
                            ) -> List[int]:
    """Render ``pattern`` to the PlexStim **Fixed** format — a list
    of up to 999 amplitude samples (in nA) played at a fixed sample
    period of ``sample_period_us`` µs (≥ 1).

    Use this when:

    * The total pulse length divided by the desired resolution fits
      in 999 points (e.g. a 500-µs pulse at 1 µs/sample = 500 points,
      or a 2 ms pulse at 5 µs/sample = 400 points).
    * The pattern is dominated by curved shapes that benefit from
      uniform sampling more than from non-uniform breakpoints.

    Otherwise prefer :func:`build_pat_pairs` (Variable format) — it
    represents a long rectangular phase as a single pair, while the
    Fixed format would burn 200 samples on a 200-µs flat phase.

    The output is also passed through
    :func:`validate_pat_samples_fixed` before return so any
    miscalculation surfaces here rather than as silent truncation on
    the device.
    """
    period = max(_PAT_MIN_SAMPLE_PERIOD_US, int(round(sample_period_us)))
    total_us = float(pattern.total_pulse_us)
    if total_us <= 0:
        raise ValueError("Pattern has zero total duration — nothing to sample.")
    n_samples_estimate = int(round(total_us / period))
    if n_samples_estimate < 1:
        raise ValueError(
            f"Pattern total {total_us:g} µs / sample period {period} µs "
            f"rounds to 0 samples. Pick a finer period or a longer pattern.")
    # Reject upfront when the estimated sample count overflows the cap.
    # Catching it here is clearer than silently truncating mid-loop and
    # then having validate_pat_samples_fixed flag the cap violation
    # AFTER we've already lost the trailing data.
    if n_samples_estimate > max_points:
        raise ValueError(
            f"Pattern total {total_us:g} µs / sample period {period} µs "
            f"= {n_samples_estimate} samples but the PlexStim Fixed "
            f"format caps at {max_points}. Either raise the sample "
            f"period or shorten the pattern.")
    # Render via the same per-phase shape engine the Variable path uses,
    # then resample onto the fixed grid. ``shape_breakpoints`` returns
    # (offset_us, amp_ua) pairs relative to the phase start; we walk
    # the cumulative timeline and lookup each fixed-period sample.
    samples_ua: List[float] = []
    cursor_us = 0.0
    target_us = 0.0
    for ph in pattern.phases:
        if ph.width_us <= 0:
            continue
        bps = shape_breakpoints(
            amplitude_ua=ph.amplitude_ua,
            width_us=ph.width_us,
            shape=ph.shape,
            bump_count=ph.bump_count,
            tau_us=ph.tau_us,
            n_samples=max(2, int(round(ph.width_us / period)) + 1),
            tail_zero_us=getattr(ph, "tail_zero_us", 0.0),
            offset_ua=getattr(ph, "offset_ua", 0.0),
        )
        # Step through the fixed grid for as long as we're inside this
        # phase. ``target_us`` is the cumulative time for the next
        # output sample.
        phase_end_us = cursor_us + ph.width_us
        while target_us + 1e-9 < phase_end_us:
            local = target_us - cursor_us
            # Hold-and-step: find the breakpoint whose offset is the
            # largest still ≤ local, take its amplitude. Mirrors the
            # device's sample-and-hold staircase.
            amp = bps[0][1]
            for off, a in bps:
                if off <= local + 1e-9:
                    amp = a
                else:
                    break
            samples_ua.append(float(amp))
            target_us += period
        cursor_us = phase_end_us
        # Inter-phase / discharge / post-phase delay — held at 0 µA.
        if ph.delay_after_us > 0:
            delay_end_us = cursor_us + ph.delay_after_us
            while target_us + 1e-9 < delay_end_us:
                samples_ua.append(0.0)
                target_us += period
            cursor_us = delay_end_us
    samples_nA = [int(round(a * 1000.0)) for a in samples_ua]
    validate_pat_samples_fixed(samples_nA,
                               sample_period_us=period,
                               max_points=max_points)
    return samples_nA


def validate_pat_samples_fixed(samples: List[int], *,
                               sample_period_us: int,
                               max_points: int = _PAT_MAX_FIXED_POINTS,
                               ) -> None:
    """Sanity-check Fixed-format samples against the PlexStim 2.0 SDK.

    Constraints enforced:

    * **Sample period** ≥ :data:`_PAT_MIN_SAMPLE_PERIOD_US` (1 µs) —
      the firmware refuses sub-1-µs sample rates.
    * **Sample count** in ``[1, max_points]`` — the documented limit
      is 999 points; longer arrays silently truncate on the device.
    * **Amplitude** within the ``±STIM_MAX_AMPLITUDE_UA × 1000`` nA
      range. Each sample is a plain ``int`` (no float / numpy scalar
      leakage that would corrupt the line-oriented file format).

    Raises ``ValueError`` with a human-readable message for every
    violation. Called automatically by :func:`build_pat_samples_fixed`
    so callers don't normally need to invoke it directly.
    """
    from .config import STIM_MAX_AMPLITUDE_UA
    if not isinstance(sample_period_us, int) or sample_period_us < _PAT_MIN_SAMPLE_PERIOD_US:
        raise ValueError(
            f"sample_period_us={sample_period_us!r} — must be an integer "
            f"≥ {_PAT_MIN_SAMPLE_PERIOD_US} µs (PlexStim hardware "
            f"time resolution).")
    if not samples:
        raise ValueError("build_pat_samples_fixed produced 0 samples — "
                         "PlexStim won't accept an empty pattern.")
    if len(samples) > max_points:
        raise ValueError(
            f"build_pat_samples_fixed produced {len(samples)} samples but the "
            f"PlexStim Fixed format caps at {max_points}. Either raise the "
            f"sample period or split the pattern.")
    amp_limit_nA = int(round(STIM_MAX_AMPLITUDE_UA * 1000.0)) + 1
    for k, amp in enumerate(samples):
        if not isinstance(amp, int):
            raise ValueError(
                f"Sample {k} = {amp!r} — must be a plain int (no floats "
                f"/ numpy scalars).")
        if abs(amp) > amp_limit_nA:
            raise ValueError(
                f"Sample {k}: amplitude {amp} nA exceeds the "
                f"±{amp_limit_nA} nA PlexStim hardware limit "
                f"({STIM_MAX_AMPLITUDE_UA:g} µA).")


def format_pat_fixed_lines(samples: List[int],
                           sample_period_us: int) -> List[str]:
    """Render Fixed-format samples as the PlexStim ``.pat`` text form.

    Output structure:

    * Line 1: literal ``Fixed``.
    * Line 2: sample period in µs (integer).
    * Lines 3..N+2: one amplitude (nA, signed integer) per line.

    Caller joins with ``"\\n"`` and appends a trailing newline; mirrors
    the convention used by :func:`format_pat_lines` for the Variable
    format.
    """
    lines = ["Fixed", str(int(sample_period_us))]
    for amp_nA in samples:
        lines.append(str(int(amp_nA)))
    return lines
