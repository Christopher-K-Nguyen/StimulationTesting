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

PHASE_SHAPES = (
    SHAPE_RECTANGULAR,
    SHAPE_LINEAR_INCREASING,
    SHAPE_LINEAR_DECREASING,
    SHAPE_SINUSOIDAL,
    SHAPE_SPEEDBUMPS,
    SHAPE_BOWTIE,
    SHAPE_HALFPIPE,
)

#: Sample count for curved shapes (sinusoidal / halfpipe / bowtie). The
#: PlexStim 2.0 ``PS_LoadArbPattern`` cap is 999 fixed points or 499 paired
#: values per channel — at 50 points per phase × 2 phases = 100 points, plus
#: leading/trailing zero markers, we're well within budget. 50 points samples
#: a 200 µs phase at 4 µs per breakpoint, fine enough that the linear
#: interpolation between breakpoints is visually indistinguishable from the
#: continuous curve.
_DEFAULT_CURVED_SAMPLES = 50


@dataclass
class Phase:
    """One phase of a stimulus pulse.

    ``shape`` selects the per-phase current waveform — rectangular by
    default for backwards compatibility. ``bump_count`` is only consulted
    when ``shape == "speedbumps"``; ignored otherwise.
    """
    amplitude_ua: float        # signed (µA); negative = cathodic
    width_us: float            # phase width
    delay_after_us: float = 0.0  # interphase or discharge delay following this phase
    shape: str = SHAPE_RECTANGULAR
    bump_count: int = 3        # speedbumps only

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

        The rest of the codebase uses ``charge_per_phase_nc`` from
        ``PulsePattern`` to label captures; that's still based on
        amplitude × width (peak·width) for traceability against the
        MATLAB-era convention. Per-shape "effective charge" is
        available from this property when needed.
        """
        return self.amplitude_ua * 1e-3 * self.width_us * _shape_duty(self.shape)

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
        )


def _shape_duty(shape: str) -> float:
    """Fractional area under the unit-amplitude waveform for each shape.

    Used by :meth:`Phase.charge_nc` to scale peak amplitude × width into
    the actual charge that flows through the electrode.
    """
    if shape == SHAPE_RECTANGULAR:
        return 1.0
    if shape == SHAPE_SINUSOIDAL:
        return 2.0 / np.pi          # ∫ sin(πt)dt over [0,1] = 2/π
    if shape in (SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING,
                 SHAPE_HALFPIPE, SHAPE_BOWTIE):
        return 0.5                  # triangle / (1−cos)/2 / V-shape
    if shape == SHAPE_SPEEDBUMPS:
        return 0.5                  # equal-width pulse/gap → 50% duty
    return 1.0


def shape_breakpoints(*, amplitude_ua: float, width_us: float,
                      shape: str = SHAPE_RECTANGULAR,
                      bump_count: int = 3,
                      n_samples: int = _DEFAULT_CURVED_SAMPLES,
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
    """
    if width_us <= 0:
        return [(0.0, 0.0)]
    s = shape.lower().strip()
    A = float(amplitude_ua)
    W = float(width_us)

    if s == SHAPE_RECTANGULAR:
        # Two breakpoints — start and end at peak. Existing behaviour.
        return [(0.0, A), (W, A)]

    if s == SHAPE_LINEAR_INCREASING:
        # Ramp from 0 to A across the phase.
        return [(0.0, 0.0), (W, A)]

    if s == SHAPE_LINEAR_DECREASING:
        # Ramp from A to 0 across the phase.
        return [(0.0, A), (W, 0.0)]

    if s == SHAPE_SINUSOIDAL:
        # Half-sine: amp(t) = A · sin(π · t / W). Zero at endpoints,
        # peak at midpoint.
        ts = np.linspace(0.0, W, n_samples)
        amps = A * np.sin(np.pi * ts / W)
        return list(zip(ts.tolist(), amps.tolist()))

    if s == SHAPE_HALFPIPE:
        # Smooth bowl: amp(t) = A · (1 − cos(2π · t / W)) / 2. Zero at
        # endpoints, peak at midpoint, smoother edges than sine.
        ts = np.linspace(0.0, W, n_samples)
        amps = A * (1 - np.cos(2 * np.pi * ts / W)) / 2
        return list(zip(ts.tolist(), amps.tolist()))

    if s == SHAPE_BOWTIE:
        # V-shape (or ^-shape): linear up to peak at midpoint, then
        # linear back to 0. Three breakpoints exactly capture it.
        return [(0.0, 0.0), (W / 2.0, A), (W, 0.0)]

    if s == SHAPE_SPEEDBUMPS:
        # bump_count sub-pulses of equal width at peak amplitude,
        # alternating with equal-width gaps at 0. For N bumps the
        # phase splits into 2N−1 equal segments: N pulses + (N−1)
        # gaps so the first and last segments are pulses (no leading
        # silence). Total active = N/(2N−1) · W; total gap =
        # (N−1)/(2N−1) · W.
        n = max(1, int(bump_count))
        if n == 1:
            return [(0.0, A), (W, A)]
        n_segments = 2 * n - 1
        seg_w = W / n_segments
        bps: List[Tuple[float, float]] = []
        for i in range(n_segments):
            t_start = i * seg_w
            t_end = (i + 1) * seg_w
            amp_here = A if (i % 2 == 0) else 0.0
            bps.append((t_start, amp_here))
            bps.append((t_end, amp_here))
        return bps

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
        # Symmetric mode is rectangular-only by lab convention. Reject
        # non-rectangular shape requests at construction time so the
        # user sees the error in the prefs / GUI form path rather
        # than getting a silently-rectangular pattern.
        if symmetric and shape != SHAPE_RECTANGULAR:
            raise ValueError(
                f"Symmetric biphasic only supports the rectangular shape; "
                f"got {shape!r}. Set symmetric=False to use a non-"
                f"rectangular phase shape.")
        if symmetric and shape2 is not None and shape2 != SHAPE_RECTANGULAR:
            raise ValueError(
                f"Symmetric biphasic ignores shape2 — pass symmetric="
                f"False to give the two phases different shapes "
                f"({shape2!r} requested).")
        a1 = polarity * abs(amplitude_ua)
        if symmetric or amplitude2_ua is None:
            a2 = -a1
            w2 = phase_width_us
            sh1 = SHAPE_RECTANGULAR     # forced by symmetric branch
            sh2 = SHAPE_RECTANGULAR
            bc1 = 1                     # bump_count irrelevant; clamp to 1
            bc2 = 1
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
        ratio=(2, -3, 1),
    ) -> "PulsePattern":
        """Triphasic with an asymmetric ratio. Default 2:-3:1 from IEEE NER paper.

        ``polarity = -1`` (cathodic-first) means the *excitation* phase
        (largest magnitude) is cathodic. We normalize so the excitation phase
        magnitude equals ``amp_excite_ua``.
        """
        if polarity not in (-1, +1):
            raise ValueError("polarity must be -1 or +1")
        ratio = np.asarray(ratio, dtype=float)
        excite_idx = int(np.argmax(np.abs(ratio)))
        # rescale so |ratio[excite_idx]| -> amp_excite_ua
        unit = abs(amp_excite_ua) / abs(ratio[excite_idx])
        amps = ratio * unit
        # apply polarity: ensure excitation phase has chosen sign
        if np.sign(amps[excite_idx]) != polarity:
            amps = -amps
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
        """Sign of the excitation phase (-1 cathodic-first, +1 anodic-first)."""
        return -1 if self.excitation_phase.amplitude_ua < 0 else +1

    @property
    def charge_per_phase_nc(self) -> float:
        """|Q_ph| of the *excitation* phase, nanocoulombs."""
        return abs(self.excitation_phase.charge_nc)

    # ------------------------------------------------------------------
    # Time-domain rendering (used by the simulator and for plotting)
    # ------------------------------------------------------------------
    def to_timeseries(self, t_pre_us: float = 100.0, t_post_us: float = 200.0,
                      sample_period_us: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
        """Return ``(time_us, current_ua)`` for one full pulse including padding.

        Shaped phases (sinusoidal / halfpipe / bowtie / speedbumps /
        ramps) are rendered by linearly interpolating
        :func:`shape_breakpoints` onto the sample grid — the same
        breakpoints the Plexon ``.pat`` writer uses, so the on-screen
        preview matches what's actually programmed onto the device.
        """
        total = t_pre_us + self.total_pulse_us + t_post_us
        n = int(round(total / sample_period_us)) + 1
        t = np.linspace(-t_pre_us, total - t_pre_us, n)
        i = np.zeros_like(t)
        cursor = 0.0
        for ph in self.phases:
            bps = shape_breakpoints(
                amplitude_ua=ph.amplitude_ua, width_us=ph.width_us,
                shape=ph.shape, bump_count=ph.bump_count,
            )
            # Interpolate the breakpoint sequence onto the sample grid
            # for this phase. ``np.interp`` does linear interpolation
            # which matches the PlexStim DLL's behaviour between
            # breakpoints — preview and device output stay consistent.
            mask_phase = (t >= cursor) & (t < cursor + ph.width_us)
            if mask_phase.any():
                bp_times = np.array([cursor + b[0] for b in bps])
                bp_amps  = np.array([b[1] for b in bps])
                i[mask_phase] = np.interp(t[mask_phase], bp_times, bp_amps)
            cursor += ph.width_us + ph.delay_after_us
        return t, i

    def scaled(self, factor: float) -> "PulsePattern":
        """Return a copy with all phase amplitudes scaled (used by the ramp loop)."""
        new = PulsePattern(
            phases=[p.scaled(factor) for p in self.phases],
            rate_hz=self.rate_hz,
            repetitions=self.repetitions,
        )
        return new

    # ------------------------------------------------------------------
    # Charge balance
    # ------------------------------------------------------------------
    @property
    def net_charge_nc(self) -> float:
        """Sum of per-phase charges in nanocoulombs (signed). 0 = perfectly balanced."""
        return float(sum(p.charge_nc for p in self.phases))

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
            STIM_CURRENT_RESOLUTION_UA,
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
            # 0 is fine (e.g. a discharge phase) but 0.05 µA on a 0.1-µA
            # device gets silently rounded and the user wouldn't know.
            if (ph.amplitude_ua != 0.0
                    and abs(ph.amplitude_ua) < STIM_CURRENT_RESOLUTION_UA - 1e-9):
                raise ValueError(
                    f"Phase {n}: amplitude {ph.amplitude_ua:+.3f} µA is "
                    f"below the {STIM_CURRENT_RESOLUTION_UA} µA hardware "
                    f"resolution.")
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
                f"one period ({period_us:.0f} µs at {self.rate_hz:g} Hz). "
                f"Either lower the rate or shorten the phases / delays.")
        if self.repetitions < 0:
            raise ValueError(
                f"repetitions must be ≥ 0 (0 = infinite), got "
                f"{self.repetitions}.")

    def auto_balance(self, adjust: str = "last_amp") -> "PulsePattern":
        """Return a copy in which the net charge is forced to zero.

        ``adjust`` selects which knob is rewritten to absorb the imbalance:

        * ``"last_amp"`` — recompute the *amplitude* of the final phase so
          ``sum(amp_i * width_i) == 0``. Useful when phase widths are
          fixed by the experiment and only amplitudes are tunable.
        * ``"last_width"`` — recompute the *width* of the final phase
          instead. Sign of the new width follows the original (we only
          ever produce positive widths).

        Phases other than the chosen one are passed through unchanged.
        Raises :class:`ValueError` if the adjustment can't reach balance
        (e.g. asking for ``"last_width"`` when the final amplitude is 0).
        """
        if not self.phases:
            return PulsePattern(phases=[], rate_hz=self.rate_hz,
                                repetitions=self.repetitions)
        head = self.phases[:-1]
        last = self.phases[-1]
        # Net charge from all phases except the last: amp_i * width_i, in nA·µs.
        # (We work in nA·µs because charge_nc is amp_µA × width_µs × 1e-3 = nC.)
        sum_head = sum(p.amplitude_ua * p.width_us for p in head)
        if adjust == "last_amp":
            if last.width_us <= 0:
                raise ValueError("Cannot balance via amplitude when the final "
                                 "phase has zero width.")
            new_amp = -sum_head / last.width_us
            new_last = Phase(new_amp, last.width_us, last.delay_after_us)
        elif adjust == "last_width":
            if last.amplitude_ua == 0:
                raise ValueError("Cannot balance via width when the final "
                                 "phase amplitude is zero.")
            new_width = -sum_head / last.amplitude_ua
            if new_width <= 0:
                raise ValueError("Width-based balance would require a negative "
                                 "duration; flip the polarity or use "
                                 "amplitude-based balance instead.")
            new_last = Phase(last.amplitude_ua, new_width, last.delay_after_us)
        else:
            raise ValueError(f"Unknown adjust mode {adjust!r}")
        return PulsePattern(phases=[*head, new_last],
                            rate_hz=self.rate_hz,
                            repetitions=self.repetitions)
