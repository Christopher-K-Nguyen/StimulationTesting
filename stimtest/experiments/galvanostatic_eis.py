"""Galvanostatic EIS (GEIS) — a single-electrode impedance sweep.

Applies a small-signal SINUSOIDAL or SQUARE current at a series of log-spaced
frequencies and, at each, computes the complex electrode impedance
``Z(f) = V_mon_phasor / I_mon_phasor`` from a single-bin lock-in
(:func:`metrics.complex_impedance`).  The run's captures ARE the spectrum
(one capture per frequency), from which POLARIS / the tab draw a Bode
(|Z|, phase vs log f) and a Nyquist (−Z″ vs Z′) plot.

Inspired by Liu, Chen, Woeppel, Cui & Kubendran, "Online Charge Balancing
With Active Impedance Monitoring in Programmable Stimulator to Extend
Electrode Lifetime" (PMC13089616) — a current-perturbation ("galvanostatic")
impedance probe.  Unlike a pulsed VT/SP run there is NO water-window ramp:
the probe amplitude is fixed and small-signal.

Two design facts make this simple and robust:

* **Trigger-phase independence** — ``Z = V/I`` divides out any common time
  reference, so no precise scope trigger is needed.  A free-running SAMPLE
  frame spanning several whole cycles is enough; the multi-cycle lock-in IS
  the averaging (AVERAGE mode would smear a free sinusoid to zero).
* **Adaptive cycle count** — the "average count" for a lock-in is the number
  of whole cycles integrated per point.  It is time-bounded
  (:func:`eis_cycles_for`): many cycles at high f (cheap), few at low f
  (each cycle is slow), so per-point time stays ≈ ``target_capture_s`` in the
  mid-band, floored at ``min_cycles`` and capped at ``max_cycles``.

Frequency bounds come from the **1 µs stimulator time resolution**: each
half-cycle is one phase = an integer number of µs, so the max frequency is
shape-dependent (:func:`eis_frequency_limits`) — 500 kHz square / ~125 kHz
sine — and the achievable grid is sparse near the top (dedupe by rounded
width; lock in at the ACTUAL rendered frequency).
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional, Tuple

from ..config import STIM_MAX_AMPLITUDE_UA, STIM_TIME_RESOLUTION_US
from ..hardware.base import Oscilloscope, Stimulator
from ..hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from ..metrics import complex_impedance
from ..readback_calibration import make_capture
from ..session import ChannelRun, Session
from ..waveforms import Phase, PulsePattern, SHAPE_RECTANGULAR, SHAPE_SINUSOIDAL
from .base import ExperimentEvent, ExperimentResult, ExperimentRunner

#: Practical low-frequency floor (Hz).  The 1 µs resolution imposes no hard
#: lower bound (a large half-cycle is always renderable) — the real cost at
#: low f is TIME (1 Hz ≈ seconds/point), so this is a convenience floor, not
#: a hardware one.
EIS_MIN_FREQ_HZ = 0.1
#: Minimum half-cycle (µs) per probe shape → the 1 µs-resolution max frequency.
#: A square half-cycle is one flat level (1 µs suffices → 500 kHz); a sine
#: half-cycle needs several breakpoints to BE a sine (≥ 4 samples → ~125 kHz;
#: above ~50 kHz the sine is coarse and the square probe is cleaner).
EIS_SQUARE_MIN_HALF_US = 1.0
EIS_SINE_MIN_HALF_US = 4.0

#: Adaptive-cycle defaults (see :func:`eis_cycles_for`) — the NORMAL mode.
EIS_TARGET_CAPTURE_S = 0.5
EIS_MIN_CYCLES = 2
EIS_MAX_CYCLES = 32

#: Gamry-style **"Optimize for"** measurement modes.  Gamry's setting controls
#: the MINIMUM number of cycles completed before a data point is taken
#: (gamry.com potentiostatic-EIS docs): Fast (poor cell stability / low
#: well-defined Z), Normal (high-Z or noisy), Low Noise (highest min cycles +
#: enhanced precision, best data / slowest).  Gamry doesn't publish the exact
#: counts, so these are monotonic values honoring the semantics.  Each also
#: sets ``settle_cycles`` — the electrode-settle wait after a frequency change
#: (Gamry excludes the startup-transient cycle; the settle wait is our
#: equivalent, letting the interface reach steady state before the lock-in
#: frame is read).
EIS_MODES = {
    "fast":      dict(min_cycles=1, max_cycles=8,  target_capture_s=0.25, settle_cycles=1),
    "normal":    dict(min_cycles=2, max_cycles=32, target_capture_s=0.5,  settle_cycles=2),
    "low_noise": dict(min_cycles=4, max_cycles=64, target_capture_s=1.0,  settle_cycles=3),
}
EIS_MODE_LABELS = {"fast": "Fast", "normal": "Normal", "low_noise": "Low Noise"}


def eis_resolve_mode(mode: str) -> dict:
    """Resolve a Gamry-style mode name to its
    ``{min_cycles, max_cycles, target_capture_s, settle_cycles}`` params.
    Unknown names fall back to Normal."""
    return dict(EIS_MODES.get(str(mode).lower(), EIS_MODES["normal"]))


def eis_frequency_limits(shape: str) -> Tuple[float, float]:
    """(f_min_hz, f_max_hz) renderable on the 1 µs grid for ``shape``.

    f_max = ``1e6 / (2 · min_half_us)`` — square 500 kHz, sine ~125 kHz.
    f_min is the practical floor :data:`EIS_MIN_FREQ_HZ` (time-limited, not
    resolution-limited)."""
    min_half = (EIS_SQUARE_MIN_HALF_US if shape == SHAPE_RECTANGULAR
                else EIS_SINE_MIN_HALF_US)
    f_max = 1e6 / (2.0 * min_half)
    return EIS_MIN_FREQ_HZ, f_max


def _grid_half_width_us(freq_hz: float) -> float:
    """The half-cycle width (µs) rounded to the 1 µs grid, floored at 1 µs.
    The ACTUAL rendered frequency is ``1e6 / (2 · this)``."""
    half = (1e6 / freq_hz) / 2.0
    w = round(half / STIM_TIME_RESOLUTION_US) * STIM_TIME_RESOLUTION_US
    return max(w, STIM_TIME_RESOLUTION_US)


def eis_frequencies(freq_min_hz: float, freq_max_hz: float,
                    points_per_decade: int, shape: str) -> List[float]:
    """Log-spaced probe frequencies, CLAMPED to the shape's renderable range
    and DEDUPED by rounded half-cycle width (near f_max the 1 µs grid is
    sparse, so several requested points collapse to one).  Each returned
    value is the ACTUAL rendered frequency ``1e6/(2·w)`` for an integer-µs
    half-cycle ``w`` — use it for the lock-in bin, not the nominal request."""
    lo_hw, hi_hw = eis_frequency_limits(shape)
    fmin = max(float(freq_min_hz), lo_hw)
    fmax = min(float(freq_max_hz), hi_hw)
    if not (fmax > fmin):
        fmax = fmin
    decades = math.log10(fmax / fmin) if fmax > fmin else 0.0
    ppd = max(1, int(points_per_decade))
    n = max(2, int(round(decades * ppd)) + 1) if decades > 0 else 1
    nominal = ([fmin] if n <= 1
               else [fmin * (10 ** (i * decades / (n - 1))) for i in range(n)])
    # Snap each to its 1 µs-grid frequency, dedupe (low → high), keep order.
    out: List[float] = []
    seen = set()
    for f in nominal:
        w = _grid_half_width_us(f)
        if w in seen:
            continue
        seen.add(w)
        out.append(1e6 / (2.0 * w))
    return sorted(out)


def eis_spectrum(captures) -> dict:
    """Extract the impedance spectrum from a run's captures — the finite
    ``(eis_freq_hz, z_mag_ohm, z_phase_deg, z_real_ohm, z_imag_ohm)`` points,
    sorted by frequency.  Returns a dict of parallel float lists (empty when
    no capture carries a finite EIS point).  Used by the Bode/Nyquist plots
    and the export."""
    import math as _m
    rows = []
    for c in (captures or []):
        m = getattr(c, "metrics", None)
        if m is None:
            continue
        f = getattr(m, "eis_freq_hz", float("nan"))
        zm = getattr(m, "z_mag_ohm", float("nan"))
        if not (isinstance(f, float) or isinstance(f, int)):
            continue
        if not (_m.isfinite(f) and f > 0 and _m.isfinite(zm)):
            continue
        rows.append((float(f), float(zm),
                     float(getattr(m, "z_phase_deg", float("nan"))),
                     float(getattr(m, "z_real_ohm", float("nan"))),
                     float(getattr(m, "z_imag_ohm", float("nan")))))
    rows.sort(key=lambda r: r[0])
    return dict(
        freq_hz=[r[0] for r in rows],
        z_mag_ohm=[r[1] for r in rows],
        z_phase_deg=[r[2] for r in rows],
        z_real_ohm=[r[3] for r in rows],
        z_imag_ohm=[r[4] for r in rows],
    )


def eis_cycles_for(freq_hz: float, *, target_capture_s: float = EIS_TARGET_CAPTURE_S,
                   min_cycles: int = EIS_MIN_CYCLES,
                   max_cycles: int = EIS_MAX_CYCLES) -> int:
    """Whole cycles to integrate at ``freq_hz`` (the lock-in "average count").

    Time-bounded: ``clamp(round(f · target_capture_s), min_cycles,
    max_cycles)``.  Per-point time ≈ ``target_capture_s`` in the mid-band,
    floored at ``min_cycles`` cycles (low f — each cycle is slow, so ≥1 is
    irreducible) and capped at ``max_cycles`` (high f — don't integrate
    needless cycles).  So the cycle count REDUCES as frequency decreases."""
    lo = max(1, int(min_cycles))
    hi = max(lo, int(max_cycles))
    n = int(round(float(freq_hz) * float(target_capture_s)))
    return max(lo, min(hi, n))


@dataclass
class GalvanostaticEISPolicy:
    freq_min_hz: float = 1.0
    freq_max_hz: float = 100_000.0
    points_per_decade: int = 10
    amplitude_ua: float = 10.0
    probe_shape: str = SHAPE_SINUSOIDAL          # or SHAPE_RECTANGULAR
    #: Gamry-style "Optimize for" — "fast" | "normal" | "low_noise".  Sets the
    #: adaptive cycle bounds + settle wait (see :data:`EIS_MODES`).
    mode: str = "normal"
    #: Explicit overrides — leave None to derive from ``mode``.
    target_capture_s: Optional[float] = None
    min_cycles: Optional[int] = None
    max_cycles: Optional[int] = None
    settle_cycles: Optional[int] = None

    def resolved(self) -> dict:
        """The effective ``{min_cycles, max_cycles, target_capture_s,
        settle_cycles}`` — the ``mode`` preset with any explicit override
        applied."""
        m = eis_resolve_mode(self.mode)
        for k in ("min_cycles", "max_cycles", "target_capture_s", "settle_cycles"):
            v = getattr(self, k)
            if v is not None:
                m[k] = v
        return m


class GalvanostaticEISExperiment(ExperimentRunner):

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope, *,
                 policy: Optional[GalvanostaticEISPolicy] = None):
        super().__init__(session, stimulator, oscilloscope)
        self.policy = policy or GalvanostaticEISPolicy()
        if isinstance(self.scope, SimulatedOscilloscope) \
                and isinstance(self.stim, SimulatedStimulator):
            self.scope.bind_stimulator(self.stim)

    # ---------------------------------------------------------------- probe
    def _probe_pattern(self, freq_hz: float) -> Tuple[PulsePattern, float]:
        """Continuous symmetric-biphasic probe at ``freq_hz`` on the 1 µs grid.
        Returns ``(pattern, actual_freq_hz)`` — the half-cycle width is rounded
        to the grid, so the ACTUAL frequency (for the lock-in) may differ."""
        w = _grid_half_width_us(freq_hz)
        actual = 1e6 / (2.0 * w)
        amp = min(abs(float(self.policy.amplitude_ua)), STIM_MAX_AMPLITUDE_UA)
        shape = self.policy.probe_shape
        pat = PulsePattern(phases=[
            Phase(amplitude_ua=-amp, width_us=w, shape=shape, delay_after_us=0.0),
            Phase(amplitude_ua=+amp, width_us=w, shape=shape, delay_after_us=0.0),
        ], rate_hz=actual)
        return pat, actual

    def _layout_for_frequency(self, freq_hz: float, cycles: int) -> None:
        """Size the scope horizontal window to span ``cycles`` whole cycles."""
        try:
            span_s = cycles / float(freq_hz)
            divs = float(getattr(self.scope, "_n_horiz_divs", 10.0) or 10.0)
            self.scope.set_horizontal_scale(span_s / divs)
        except Exception:
            pass

    # ------------------------------------------------------------------ run
    def run(self) -> ExperimentResult:
        self.preflight()
        config = self.session.test.configuration
        run = ChannelRun(
            configuration=config,
            surface_area_um2=self.session.test.array[config.active].surface_area_um2)
        self.session.add_run(run)
        self._emit(ExperimentEvent(kind="run_start", session=self.session, run=run))

        p = self.policy
        mp = p.resolved()          # {min_cycles, max_cycles, target_capture_s, settle_cycles}
        freqs = eis_frequencies(p.freq_min_hz, p.freq_max_hz,
                                p.points_per_decade, p.probe_shape)
        lo_hw, hi_hw = eis_frequency_limits(p.probe_shape)
        if p.freq_max_hz > hi_hw + 1e-6:
            self._emit(ExperimentEvent(
                kind="log", session=self.session, run=run,
                message=(f"EIS: requested f_max {p.freq_max_hz:g} Hz clamped to "
                         f"{hi_hw:g} Hz — the 1 µs resolution can't render a "
                         f"{p.probe_shape} half-cycle faster than that.")))
        if not freqs:
            self._emit(ExperimentEvent(kind="aborted", session=self.session,
                                       run=run, message="EIS: no renderable frequencies."))
            return ExperimentResult(session=self.session, aborted=True,
                                    error="no renderable frequencies")
        self._emit(ExperimentEvent(
            kind="log", session=self.session, run=run,
            message=(f"Galvanostatic Electrochemical Impedance Spectroscopy: "
                     f"{len(freqs)} points, "
                     f"{freqs[0]:.3g}–{freqs[-1]:.3g} Hz, "
                     f"{p.amplitude_ua:g} µA {p.probe_shape} probe, "
                     f"{EIS_MODE_LABELS.get(p.mode, p.mode)} mode "
                     f"(adaptive {mp['min_cycles']}–{mp['max_cycles']} "
                     f"cycles/point).")))

        # EIS wants a FREE-RUNNING SAMPLE frame — Z=V/I is trigger-phase-
        # independent, and AVERAGE mode would smear a free sinusoid to zero.
        try:
            self.scope.set_acquisition_mode("SAMPLE")
        except Exception:
            pass
        try:
            imon_ch = self.scope.channel_aliases.get("imon", "CH2")
            self.scope.set_trigger(source=imon_ch, level_v=0.0,
                                   slope="RISE", mode="AUTO")
        except Exception:
            pass

        try:
            self.stim.set_monitor_channel(config.active)
            for idx, nominal_f in enumerate(freqs):
                if self.aborted:
                    break
                if not self.wait_if_paused(restart=None):
                    break
                pattern, actual_f = self._probe_pattern(nominal_f)
                cycles = eis_cycles_for(
                    actual_f, target_capture_s=mp["target_capture_s"],
                    min_cycles=mp["min_cycles"], max_cycles=mp["max_cycles"])
                try:
                    self.stim.stop_all()
                except Exception:
                    pass
                self.stim.load_channel(config.active, pattern)
                self.stim.set_repetitions(config.active, 0)
                self.load_zero_unused_channels(pattern, config)
                self.commit_loaded_channels(config)
                self._layout_for_frequency(actual_f, cycles)
                self.stim.start_all()

                # SETTLE (Gamry startup-transient exclusion): wait
                # ``settle_cycles`` for the interface to reach steady state
                # after the frequency change, THEN read a fresh frame that
                # fully spans ``cycles`` settled cycles.  ``capture_while_
                # running`` sleeps ``wait_s`` (abort-aware) before reading.
                settle_s = mp["settle_cycles"] / actual_f
                frame_s = cycles / actual_f
                wait_s = max(settle_s + frame_s, 0.03)
                acq = self.scope.capture_while_running(wait_s=wait_s)
                # Fit the vertical scale — |Z| (hence V_mon amplitude) varies
                # by orders of magnitude across the sweep, so the SAME shared
                # coarse/fine rescale the pulsed runners use is needed here too.
                try:
                    acq = self.rescale_to_fit(
                        acq, pattern=pattern,
                        recapture=lambda _t, _w=wait_s:
                            self.scope.capture_while_running(wait_s=_w),
                        timeout_s=wait_s + 6.0,
                        context=f"(EIS {actual_f:.3g} Hz)")
                except Exception:
                    pass
                cap = make_capture(idx, pattern, acq, self.scope, self.stim,
                                   cal=self.cal, channel=config.active)
                z = complex_impedance(cap.v_mon_v, cap.i_mon_ua,
                                      cap.time_us, actual_f)
                cap.metrics.eis_freq_hz = actual_f
                cap.metrics.z_mag_ohm = z["z_mag_ohm"]
                cap.metrics.z_phase_deg = z["z_phase_deg"]
                cap.metrics.z_real_ohm = z["z_real_ohm"]
                cap.metrics.z_imag_ohm = z["z_imag_ohm"]
                # Stamp the lock-in window for the record.
                cap.metrics.n_pulses = float(cycles)
                run.captures.append(cap)
                self._emit(ExperimentEvent(kind="capture", session=self.session,
                                           run=run, capture=cap))
                try:
                    self.stim.stop_all()
                except Exception:
                    pass
        except Exception as e:
            self._emit(ExperimentEvent(kind="aborted", session=self.session,
                                       run=run, message=f"EIS sweep failed: {e}"))
            try:
                self.stim.stop_all()
            except Exception:
                pass
            return ExperimentResult(session=self.session, captures=run.captures,
                                    aborted=True, error=str(e))
        finally:
            try:
                self.stim.stop_all()
            except Exception:
                pass

        run.finished_at = datetime.now()
        self._emit(ExperimentEvent(kind="run_end", session=self.session, run=run))
        self._emit(ExperimentEvent(kind="session_end", session=self.session))
        return ExperimentResult(session=self.session, captures=run.captures,
                                aborted=self.aborted)
