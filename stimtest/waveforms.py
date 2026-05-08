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
from typing import List, Optional

import numpy as np


@dataclass
class Phase:
    """One phase of a stimulus pulse."""
    amplitude_ua: float        # signed (µA); negative = cathodic
    width_us: float            # phase width
    delay_after_us: float = 0.0  # interphase or discharge delay following this phase

    @property
    def charge_nc(self) -> float:
        """Charge per phase, nanocoulombs (signed)."""
        return self.amplitude_ua * 1e-3 * self.width_us  # µA·µs * 1e-3 -> nC

    def scaled(self, factor: float) -> "Phase":
        return Phase(self.amplitude_ua * factor, self.width_us, self.delay_after_us)


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
    ) -> "PulsePattern":
        """Symmetric biphasic by default, with optional asymmetric override."""
        if polarity not in (-1, +1):
            raise ValueError("polarity must be -1 (cathodic) or +1 (anodic)")
        a1 = polarity * abs(amplitude_ua)
        if symmetric or amplitude2_ua is None:
            a2 = -a1
            w2 = phase_width_us
        else:
            a2 = -np.sign(a1) * abs(amplitude2_ua)
            w2 = phase_width2_us if phase_width2_us is not None else phase_width_us
        return cls(
            phases=[
                Phase(a1, phase_width_us, interphase_us),
                Phase(a2, w2, discharge_us),
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
        """Return ``(time_us, current_ua)`` for one full pulse including padding."""
        total = t_pre_us + self.total_pulse_us + t_post_us
        n = int(round(total / sample_period_us)) + 1
        t = np.linspace(-t_pre_us, total - t_pre_us, n)
        i = np.zeros_like(t)
        cursor = 0.0
        for ph in self.phases:
            mask_phase = (t >= cursor) & (t < cursor + ph.width_us)
            i[mask_phase] = ph.amplitude_ua
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
