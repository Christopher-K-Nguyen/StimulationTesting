"""Waveform-level metrics for one capture.

Translation of the canonical MATLAB algorithms (``getVoltageMetrics.m``,
``getAccess2.m``, ``getDriving2.m``, ``getCapacitance.m``,
``checkPotentialExcursion.m``, ``FindPeaks.m``).

All time inputs are microseconds, voltages volts, currents microamps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

try:
    from scipy.signal import find_peaks as _scipy_find_peaks
    from scipy.signal import savgol_filter as _scipy_savgol
    _HAS_SCIPY = True
except Exception:
    _HAS_SCIPY = False

    def _scipy_find_peaks(x, height=None):
        """Minimal find_peaks fallback: local maxima above ``height``."""
        x = np.asarray(x)
        peaks = []
        for i in range(1, x.size - 1):
            if x[i] > x[i - 1] and x[i] >= x[i + 1]:
                if height is None or x[i] >= height:
                    peaks.append(i)
        return np.asarray(peaks, dtype=int), {}

    def _scipy_savgol(y, window_length, polyorder, deriv=0, delta=1.0):
        """Cheap fallback: ``deriv=0`` returns y unchanged; ``deriv=1`` does
        np.gradient; higher derivs unsupported."""
        if deriv == 0:
            return np.asarray(y)
        if deriv == 1:
            return np.gradient(np.asarray(y), delta)
        raise NotImplementedError("scipy not installed; cannot compute deriv > 1")

find_peaks = _scipy_find_peaks
savgol_filter = _scipy_savgol

from .config import DEPOLARIZATION_TIME_US
from .session import Capture, CaptureMetrics
from .waveforms import (
    PulsePattern,
    SHAPE_RECTANGULAR, SHAPE_LINEAR_INCREASING, SHAPE_LINEAR_DECREASING,
    SHAPE_SINUSOIDAL, SHAPE_SPEEDBUMPS, SHAPE_BOWTIE, SHAPE_HALFPIPE,
    SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING, SHAPE_GAUSSIAN,
)


# ---------------------------------------------------------------------------
# Pulse-shape edge factors
# ---------------------------------------------------------------------------
#: Threshold (fraction of phase peak amplitude) above which a phase
#: boundary is treated as a "clean step" — i.e. the current jumps fast
#: enough to produce an unambiguous ohmic V step we can read V_a / R_a
#: off. 0.5 picks up rectangular / linear / bowtie / halfpipe / exp-
#: decay cleanly while excluding speedbumps' bi-level half-amplitude
#: edges and the zero-amplitude endpoints of sinusoidal / linear-
#: increasing leading edges.
_STEP_FACTOR_THRESHOLD = 0.5

# Data-driven vertical-IR-step detection (operator: "Looking at the sinusoidal
# and gaussian shapes, there are some instances of vertical i-R drops.  The
# access voltage/resistance should be allowed to be determined").  A genuine
# access (ohmic) step is a near-INSTANTANEOUS jump in V_mon at a phase
# boundary: a large single-sample change that towers over the smooth-shape
# per-sample change.  A shape whose NOMINAL edge factor suppresses the access
# label (sinusoidal / gaussian) is still measured when the data shows such a
# step at that boundary.
_IR_STEP_WIN_US = 3.0        # search half-window (µs) around the boundary time
_IR_STEP_MIN_FRAC = 0.08     # sustained step ≥ this fraction of the trace p2p
_IR_STEP_SPIKE_RATIO = 4.0   # max single-sample |Δv| ≥ this × the median |Δv|
_IR_STEP_MIN_ABS_V = 0.003   # absolute floor (V) — below this it's noise, not R
_IR_STEP_VERTICAL_FRAC = 0.3 # ≥ this fraction of the SUSTAINED step lands in one
                             # sample → vertical (a smooth ramp spreads it thin)


def _boundary_has_ir_step(time_us: np.ndarray, v_trace: np.ndarray,
                          boundary_us: float, *,
                          win_us: float = _IR_STEP_WIN_US,
                          min_frac: float = _IR_STEP_MIN_FRAC,
                          spike_ratio: float = _IR_STEP_SPIKE_RATIO,
                          min_abs_v: float = _IR_STEP_MIN_ABS_V,
                          vertical_frac: float = _IR_STEP_VERTICAL_FRAC) -> bool:
    """True if ``v_trace`` shows a vertical (near-instantaneous) IR step within
    ``win_us`` of ``boundary_us``.

    A genuine ohmic step is a LARGE, SUSTAINED, CONCENTRATED jump — it must
    clear ALL of:

      1. **SUSTAINED** — the robust (median) level of the samples just AFTER
         the boundary differs from just BEFORE by ``step``.  A transient noise
         SPIKE returns to baseline (before ≈ after → ``step`` ≈ 0) and is
         rejected here; only a persistent level shift survives.
      2. **ABSOLUTE floor** — ``step ≥ min_abs_v`` (3 mV).  On a SMALL-SIGNAL
         shaped capture (e.g. a ±10 mV sinusoid) the post-average residual
         noise floor is ~1-2 mV; without this floor the noise itself could
         satisfy the fractional test and invent a phantom access point (there
         is no meaningful access R to report below a few mV anyway).
      3. **MEANINGFUL** — ``step / p2p ≥ min_frac`` (8 % of the trace swing).
      4. **CONCENTRATED / VERTICAL** — the largest single-sample |Δv| carries
         ``≥ vertical_frac`` of the whole sustained ``step`` (a vertical drop
         happens in ~1 sample; a smooth sine / gaussian ramp spreads the change
         over the whole window so ``max|Δv|`` is a tiny fraction of ``step``)
         AND ``max|Δv| ≥ spike_ratio × median|Δv|``.

    So a smooth ramp fails (4), a noise spike fails (1), and a small-signal
    noisy capture fails (2) — none invent an access point where there is no
    real ohmic drop.
    """
    t = np.asarray(time_us, dtype=float)
    v = np.asarray(v_trace, dtype=float)
    if t.size < 8 or v.size != t.size:
        return False
    p2p = float(np.percentile(v, 99.0) - np.percentile(v, 1.0))
    if not np.isfinite(p2p) or p2p <= 0.0:
        return False
    b = float(boundary_us)
    win = float(win_us)
    in_win = np.abs(t - b) <= win
    before = v[in_win & (t < b)]
    after = v[in_win & (t >= b)]
    if before.size < 2 or after.size < 2:
        return False
    step = abs(float(np.median(after)) - float(np.median(before)))
    if step < float(min_abs_v):                       # (2) absolute floor
        return False
    if step / p2p < float(min_frac):                  # (3) meaningful vs trace
        return False
    idx = np.flatnonzero(in_win)
    seg = v[idx[0]:idx[-1] + 1]
    dv = np.abs(np.diff(seg))
    if dv.size < 3:
        return False
    max_dv = float(np.max(dv))
    typ_dv = float(np.median(dv))
    if max_dv < float(vertical_frac) * step:          # (4a) concentrated jump
        return False
    if typ_dv > 0.0 and max_dv < float(spike_ratio) * typ_dv:  # (4b) vs median
        return False
    return True


def _phase_step_factors(shape: str) -> Tuple[float, float]:
    """Return ``(leading_factor, trailing_factor)`` for a phase shape:
    the fraction of the phase's peak amplitude *at* the leading and
    trailing edges. Used by the access-voltage / driving-voltage
    extractors to decide whether a given phase boundary produces a
    measurable step.

    Reference table (factor = current at edge / peak amplitude):

      ============  ============   =============
      Shape         Leading edge   Trailing edge
      ============  ============   =============
      Rectangular   1.0            1.0
      Lin-incr      0.0            1.0
      Lin-decr      1.0            0.0
      Sinusoidal    0.0            0.0
      Speedbumps    0.0            0.0   (internal segment edges
                                          confound peak detection
                                          even though the boundary
                                          factor is nominally 0.5)
      Bowtie        1.0            1.0
      Halfpipe      1.0            1.0
      Exp-decay     1.0            ~0.007 (= exp(-N) with N=5)
      Exp-incr      ~0.007         1.0   (time-reversed exp-decay)
      Gaussian      ~0.04          ~0.04 (truncated at ±2.5σ; the
                                          edge current is exp(-3.125)
                                          ≈ 4 % of peak — no ohmic
                                          step, like sinusoidal)
      ============  ============   =============
    """
    if shape == SHAPE_RECTANGULAR:        return (1.0, 1.0)
    if shape == SHAPE_LINEAR_INCREASING:  return (0.0, 1.0)
    if shape == SHAPE_LINEAR_DECREASING:  return (1.0, 0.0)
    if shape == SHAPE_SINUSOIDAL:         return (0.0, 0.0)
    if shape == SHAPE_SPEEDBUMPS:         return (0.0, 0.0)
    if shape == SHAPE_BOWTIE:             return (1.0, 1.0)
    if shape == SHAPE_HALFPIPE:           return (1.0, 1.0)
    if shape == SHAPE_EXP_DECAY:          return (1.0, 0.0067)
    # Time-reversed exp-decay: starts at ~0.7 % of peak, ends at peak.
    if shape == SHAPE_EXP_INCREASING:     return (0.0067, 1.0)
    # Gaussian ramps smoothly from ~4 % of peak — no current STEP at
    # either boundary, so no IR jump to read V_a / R_a off (operator:
    # "Gaussian shape should not have access points, like sinusoidal").
    # Both factors sit well under _STEP_FACTOR_THRESHOLD (0.5), so the
    # access extractor skips every gaussian phase boundary.
    if shape == SHAPE_GAUSSIAN:           return (0.04, 0.04)
    return (1.0, 1.0)   # unknown shapes default to rectangular-like


# ---------------------------------------------------------------------------
# Phase boundary helpers
# ---------------------------------------------------------------------------
def pulse_onset_us(time_us: np.ndarray, *traces) -> float:
    """Detect the phase-1 ONSET time (µs) from the captured data.

    The time axis is TRIGGER-relative, taken as-is from the acquisition
    (CLAUDE.md gotcha #37): with the tagged digital sync trigger t=0
    already IS the stim onset (this returns ≈ 0 and nothing changes),
    but with the **I_mon-trigger fallback the scope fires on the ANODIC
    edge** — for a cathodic-first pulse the true phase-1 onset sits
    EARLIER (negative time).  Every phase-TIME chain (phase windows,
    per-phase driving voltage, the time-method E_pol sample, the plot's
    Epol cursors) must anchor at the DETECTED onset, not at t=0 —
    otherwise Epol1 lands where Epol2 belongs and Epol2 falls off the
    end of the record (operator-reported symptom).

    Detection: first trace (priority order — pass I_mon first, it has
    the cleanest edges) whose robust 1/99-percentile p2p is non-zero;
    find the first sample deviating from the median by more than 10 % of
    that p2p (a point clearly INSIDE the pulse), then **walk BACK to where
    the signal returns to baseline** so a SMOOTH-RAMP shape (sine / gaussian)
    reports its TRUE start, not the 10 %-crossing point 10-15 µs into the
    ramp (operator: the Emc/Ema markers landed ~13 µs late on a sinusoid
    because the onset was detected at the 10 % crossing, shifting the whole
    phase-time chain).  A rectangular edge is unchanged (the walk-back stops
    on the first pre-edge baseline sample = the edge itself).  Same detector
    family as the GUI's pulse-span framing.  Returns 0.0 when no trace shows
    a pulse (all-idle frame).
    """
    t = np.asarray(time_us, dtype=float)
    if t.size < 8:
        return 0.0
    for arr in traces:
        if arr is None:
            continue
        a = np.asarray(arr, dtype=float)
        if a.size != t.size or a.size < 8:
            continue
        p2p = float(np.percentile(a, 99.0) - np.percentile(a, 1.0))
        if not np.isfinite(p2p) or p2p <= 0.0:
            continue
        med = float(np.median(a))
        active = np.abs(a - med) > (0.10 * p2p)
        idx = np.flatnonzero(active)
        if idx.size:
            i0 = int(idx[0])
            # Walk back from the 10 % crossing to the pulse's true departure
            # from baseline.  Band = max(1 % of p2p, 3× the ROBUST baseline
            # noise SD) — well above noise (so a noise wiggle can't stop the
            # walk early) yet far below the 10 % crossing (so a smooth ramp
            # reports ~t0, not the mid-ramp crossing).  Noise is estimated by
            # the MAD of the whole trace (robust — most samples are baseline,
            # so the pulse + its ramp are OUTLIERS the median ignores; a
            # windowed SD near i0 would be inflated by the ramp itself).
            mad = float(np.median(np.abs(a - med)))
            sd = 1.4826 * mad if np.isfinite(mad) else 0.0
            # 0.3 % of p2p is below even a gaussian's truncated ~4 % edge foot
            # (so its true t0 is found), while the 3σ floor keeps the band
            # above baseline noise on real captures.
            band = max(0.003 * p2p, 3.0 * sd)
            j = i0
            while j > 0 and abs(a[j - 1] - med) > band:
                j -= 1
            return float(t[j])
    return 0.0


@dataclass
class PhaseWindow:
    """Index range and edge times for one phase within a capture."""
    phase_index: int           # 0-based (1st phase = 0)
    start_us: float            # leading edge time (relative to the pulse onset)
    end_us: float              # trailing edge time
    start_idx: int             # nearest-sample index in time_us
    end_idx: int


def phase_windows(time_us: np.ndarray, pattern: PulsePattern,
                  *, onset_us: float = 0.0) -> List[PhaseWindow]:
    """Return one PhaseWindow per phase in ``pattern``.

    ``onset_us`` anchors the chain at the DETECTED phase-1 onset (see
    :func:`pulse_onset_us`) — 0.0 keeps the legacy digital-trigger
    assumption that the pulse starts at t=0.
    """
    out: List[PhaseWindow] = []
    cursor = float(onset_us)
    for k, ph in enumerate(pattern.phases):
        s = cursor
        e = cursor + ph.width_us
        s_idx = int(np.searchsorted(time_us, s, side="left"))
        e_idx = int(np.searchsorted(time_us, e, side="left"))
        out.append(PhaseWindow(k, s, e, s_idx, e_idx))
        cursor += ph.width_us + ph.delay_after_us
    return out


def ideal_current_ua(time_us: np.ndarray, pattern: PulsePattern,
                     *, onset_us: float = 0.0,
                     current_step_nA: Optional[int] = None) -> np.ndarray:
    """Reconstruct the PROGRAMMED (ideal) current waveform (µA) on ``time_us``.

    Anchored at ``onset_us``: each phase's shape breakpoints
    (:func:`waveforms.shape_breakpoints`, phase-relative) are shifted to the
    phase's absolute time window and linearly interpolated onto ``time_us``;
    the interphase / discharge / interpulse gaps carry exactly ZERO current.

    This is the CLEAN alternative to the measured I_mon for anything that
    needs the true delivered current shape (operator: "the spikes/swings in
    current is not always representative of the true pattern — there [are]
    even cases of asynchronous starts at small pulse widths").  I_mon carries
    switching transients + a variable turn-on skew at small pulse widths; the
    ideal current is exactly what the current-source stimulator is programmed
    to deliver, quantised to the device's per-shape current grid so it matches
    the charge the electrode actually receives.

    ``current_step_nA`` defaults to ``pattern.device_current_step_nA()`` (0.1
    µA for an all-rectangular pattern, 30 nA if any phase is shaped).
    """
    from .waveforms import shape_breakpoints
    t = np.asarray(time_us, dtype=float)
    out = np.zeros(t.size, dtype=float)
    if t.size == 0 or not pattern.phases:
        return out
    if current_step_nA is None:
        try:
            current_step_nA = pattern.device_current_step_nA()
        except Exception:
            current_step_nA = 0
    step_ua = float(current_step_nA) * 1e-3 if current_step_nA else 0.0
    cursor = float(onset_us)
    for ph in pattern.phases:
        p0 = cursor
        p1 = cursor + ph.width_us
        try:
            bps = shape_breakpoints(
                amplitude_ua=ph.amplitude_ua, width_us=ph.width_us,
                shape=ph.shape, bump_count=getattr(ph, "bump_count", 3),
                tau_us=getattr(ph, "tau_us", 0.0),
                tail_zero_us=getattr(ph, "tail_zero_us", 0.0),
                offset_ua=getattr(ph, "offset_ua", 0.0),
            )
        except Exception:
            bps = []
        if len(bps) >= 2:
            bt = np.asarray([b[0] for b in bps], dtype=float) + p0
            ba = np.asarray([b[1] for b in bps], dtype=float)
            if step_ua > 0:
                ba = np.round(ba / step_ua) * step_ua
            mask = (t >= p0) & (t <= p1)
            if mask.any():
                out[mask] = np.interp(t[mask], bt, ba)
        cursor = p1 + ph.delay_after_us
    return out


# ---------------------------------------------------------------------------
# Driving voltage (V_d)
# ---------------------------------------------------------------------------
def pulse_region_mask(time_us: np.ndarray, pattern: PulsePattern,
                      recovery_us: float = 0.0) -> np.ndarray:
    """Boolean mask selecting the pulse: ``t <= total_pulse_us + recovery``.

    Metrics like V_d are defined *during the pulse* (IEEE NER Fig. 3b),
    but the scope record routinely runs far past it —
    ``DEFAULT_RECORD_LENGTH`` is 20 000 points, so the pulse fills only
    a fraction of the window and the rest is post-pulse tail. That tail
    can carry a real transient (auto-discharge shorting at the end of
    the period, the leading edge of the next pulse if the timebase is
    "wide", or open-input noise) whose magnitude has nothing to do with
    the driving voltage. Restricting the abs-max scan to the pulse
    region keeps such a tail transient from masquerading as V_d.

    The pre-pulse baseline (``t < 0``) is kept — it sits near 0 V and is
    harmless — so only the tail is excluded. Matches the ``t = 0`` pulse
    start convention already used by :func:`phase_windows`.
    """
    t = np.asarray(time_us, dtype=float)
    upper = float(pattern.total_pulse_us) + float(recovery_us)
    return t <= upper


def driving_voltage_from_vmon(v_mon: np.ndarray) -> float:
    """V_d = max |V_mon| (per IEEE NER Fig. 3b).

    The caller is responsible for restricting ``v_mon`` to the pulse
    region (see :func:`pulse_region_mask`) — this helper just takes the
    abs-max of whatever it's given.
    """
    if v_mon.size == 0:
        return float("nan")
    return float(np.max(np.abs(v_mon)))


def driving_voltage_from_potentials(e_act: np.ndarray, e_ret: np.ndarray) -> float:
    """V_d = max |E_act - E_ret| (per IEEE NER Fig. 3c)."""
    if e_act.size == 0 or e_ret.size == 0:
        return float("nan")
    return float(np.max(np.abs(e_act - e_ret)))


def _driving_voltage_per_phase(time_us: np.ndarray, trace_v: np.ndarray,
                                pattern: PulsePattern,
                                *, sample_offset_us: float = 1.0,
                                onset_us: float = 0.0,
                                ) -> List[float]:
    """Per-phase driving voltage: |reference - value at driving point|.

    Faithful port of MATLAB ``getVoltageMetrics.m`` lines 554-590.

    For each phase ``k``:

      * The DRIVING point is sampled ``sample_offset_us`` before the
        phase ends (default 1 µs), so the trace is at its peak
        excursion but we're not on the trailing-edge access drop.
      * The REFERENCE is the rest baseline that immediately precedes
        the phase: pre-pulse mean for phase 1; the END of the prior
        interphase delay for phases 2+ (when the trace has settled
        back toward zero); falls back to the pre-pulse mean if the
        prior interphase delay is zero (no recovery time).

    Returns a list of length ``pattern.num_phases`` with NaN entries
    where the phase is too short / the trace is empty.
    """
    n_samples = time_us.size
    phases = pattern.phases
    n_phases = len(phases)
    if n_samples == 0 or trace_v.size != n_samples or n_phases == 0:
        return [float("nan")] * n_phases

    # Pre-pulse baseline = everything BEFORE the (detected) onset — with
    # the I_mon trigger the onset is negative, so ``time < 0`` would have
    # averaged phase-1 samples into the "rest" reference.
    pre_mask = time_us < float(onset_us)
    pre_voltage = (float(np.mean(trace_v[pre_mask])) if pre_mask.any()
                   else float(trace_v[0]))

    out: List[float] = []
    cursor = float(onset_us)
    prior_delay_us = 0.0     # delay BEFORE the current phase
    for k, ph in enumerate(phases):
        end_t = cursor + ph.width_us
        # Sample the driving point a hair before the phase ends.
        drive_t = end_t - max(0.0, sample_offset_us)
        drive_idx = int(np.searchsorted(time_us, drive_t, side="right") - 1)
        drive_idx = max(0, min(n_samples - 1, drive_idx))
        drive_val = float(trace_v[drive_idx])

        if k == 0 or prior_delay_us <= 0:
            # Phase 1, OR a phase with no recovery time before it —
            # fall back to the pre-pulse baseline.
            ref = pre_voltage
        else:
            # Sample the END of the prior interphase delay (~0 µs
            # before the current phase starts): the trace has
            # discharged back toward rest by then.
            prior_end_t = cursor - max(0.0, sample_offset_us)
            ref_idx = int(np.searchsorted(time_us, prior_end_t,
                                          side="right") - 1)
            ref_idx = max(0, min(n_samples - 1, ref_idx))
            ref = float(trace_v[ref_idx])

        out.append(abs(ref - drive_val))
        cursor = end_t + ph.delay_after_us
        prior_delay_us = ph.delay_after_us
    return out


def _interpulse_potential(time_us: np.ndarray, trace_v: np.ndarray,
                          pattern: PulsePattern,
                          *, onset_us: float = 0.0) -> float:
    """Average ``trace_v`` over the rest periods around the active pulse.

    Two rest windows are sampled:

      * ``time < 0`` — the pre-pulse baseline (matches MATLAB's
        ``prePulse_tf = time < 0; prePulsePotenial = mean(...)``).
      * ``time >= total_pulse_us`` — the post-discharge tail, where
        the electrode has settled back to its open-circuit / inter-
        pulse rest potential.

    Both windows are averaged together (not separately) so a single
    scalar reflects the true settled rest potential. If only one
    window has samples, that one is used. Returns NaN when neither
    rest window is populated (i.e., the captured frame is entirely
    inside the active pulse).
    """
    if time_us.size == 0 or trace_v.size != time_us.size:
        return float("nan")
    # No idle interpulse window → no rest potential to report.  With the
    # period fully occupied by the pulse, the "pre-pulse" samples are the
    # PRIOR pulse's tail and the post window (starting at onset+total_pulse)
    # is the NEXT pulse's onset — both are active-pulse data, not OCP
    # (operator: "no interpulse delay → E_ret/E_act are never near zero
    # during interpulse because there is no interpulse").
    if not pattern.has_interpulse_gap():
        return float("nan")
    pre_mask  = time_us < float(onset_us)
    post_mask = time_us >= float(onset_us) + float(pattern.total_pulse_us)
    rest_mask = pre_mask | post_mask
    if not rest_mask.any():
        return float("nan")
    return float(np.mean(trace_v[rest_mask]))


def _interpulse_potential_split(
    time_us: np.ndarray, trace_v: np.ndarray, pattern: PulsePattern,
    *, onset_us: float = 0.0,
) -> Tuple[float, float]:
    """Return ``(pre_pulse_mean, post_pulse_mean)`` from ``trace_v``.

    Same windowing as :func:`_interpulse_potential` but reports the
    two rest periods separately, so the runner can feed them into
    :mod:`stimtest.electrode_potential_history` with the right
    ``phase`` tag and so a downstream drift-detector can watch for
    pre→post offset growing across captures (a tell-tale sign of
    accumulating DC bias / failed auto-discharge).

    Each half returns ``float("nan")`` when its window is empty —
    the caller is expected to guard against NaN before recording.
    """
    if time_us.size == 0 or trace_v.size != time_us.size:
        return float("nan"), float("nan")
    # No idle interpulse window → decline (see _interpulse_potential).  This
    # also keeps the learned-OCP store from being fed pulse data as if it
    # were the electrode rest potential.
    if not pattern.has_interpulse_gap():
        return float("nan"), float("nan")
    pre_mask = time_us < float(onset_us)
    post_mask = time_us >= float(onset_us) + float(pattern.total_pulse_us)
    pre_v = float(np.mean(trace_v[pre_mask])) if pre_mask.any() else float("nan")
    post_v = float(np.mean(trace_v[post_mask])) if post_mask.any() else float("nan")
    return pre_v, post_v


# ---------------------------------------------------------------------------
# Access voltage / resistance
# ---------------------------------------------------------------------------
def _smooth(y: np.ndarray, window: int = 10) -> np.ndarray:
    """Centered moving-average smoothing (matches MATLAB ``smooth(y,N)``).

    Uses :func:`scipy.ndimage.uniform_filter1d` with ``mode="nearest"`` so
    the boundaries don't get pulled toward zero the way ``np.convolve``
    does. ~3× faster than the manual ``convolve + edge patch`` we used to
    have, and a single function call instead of a slice-assignment fix-up.
    """
    arr = np.asarray(y, dtype=float)
    if arr.size <= window:
        return arr
    if _HAS_SCIPY:
        from scipy.ndimage import uniform_filter1d
        return uniform_filter1d(arr, size=window, mode="nearest")
    # scipy not available — fall back to the original convolution+patch.
    kernel = np.ones(window) / window
    out = np.convolve(arr, kernel, mode="same")
    half = window // 2
    out[:half] = arr[:half]
    out[-half:] = arr[-half:]
    return out


def _despike(v: np.ndarray, time_us: Optional[np.ndarray] = None,
             win_us: float = 4.0) -> np.ndarray:
    """MEDIAN-filter the trace over ~``win_us`` to reject brief switching
    SPIKES and ringing before reading SETTLED metric values (driving
    voltage, access plateau).

    At small currents the phase-boundary switching transients (a sharp
    overshoot + a few µs of ringing) are LARGE relative to the signal, so a
    plain max/argmin lands on a spike and mis-reports V_d / V_a (operator:
    "spikes and ringing … We need to ensure that the spike does not mislead
    … the driving voltage … this is also important for access voltage").  A
    MEDIAN (not the mean ``_smooth``) rejects a spike narrower than half the
    window WITHOUT smearing the trace toward it, and a window spanning ≳ one
    ringing period averages the oscillation to its centre (the settled
    level).  The IR-step EDGE is blurred, but access values are read on the
    post-step PLATEAU (away from the edge), so the step magnitude
    (plateau − reference) is preserved.  Used ONLY for value reads; the
    |dV/dt| edge LOCALIZATION still runs on the lightly-smoothed trace."""
    v = np.asarray(v, dtype=float)
    if v.size < 5:
        return v
    dt = 1.0
    if time_us is not None:
        d = np.diff(np.asarray(time_us, dtype=float))
        if d.size:
            md = float(np.median(d))
            if np.isfinite(md) and md > 0:
                dt = md
    n = max(3, int(round(win_us / dt)))
    if n % 2 == 0:
        n += 1
    if n >= v.size:
        return v
    if _HAS_SCIPY:
        from scipy.ndimage import median_filter
        return median_filter(v, size=n, mode="nearest")
    # No scipy — rolling median (n is small, ≤ a few dozen samples).
    half = n // 2
    out = v.copy()
    for i in range(v.size):
        out[i] = np.median(v[max(0, i - half):min(v.size, i + half + 1)])
    return out


def _abs_derivative(time_us: np.ndarray, v: np.ndarray,
                    smooth_window: int = 10,
                    edge_zero: int = 30,
                    v_smooth_precomputed: Optional[np.ndarray] = None
                    ) -> np.ndarray:
    """|dV/dt| with the canonical pre-/post-smoothing and edge clamping
    used by ``getAccess.m``.

    Pass ``v_smooth_precomputed`` to skip the initial smoothing pass
    when the caller has already computed it (saves the redundant
    ``uniform_filter1d`` call when this is invoked alongside another
    site that smooths the same trace — e.g.
    ``access_voltage_and_resistance`` needs both the derivative AND a
    smoothed V_mon for driving-extrema lookup).
    """
    if v_smooth_precomputed is not None:
        v_smooth = v_smooth_precomputed
    else:
        v_smooth = _smooth(np.asarray(v, dtype=float), smooth_window)
    dv = np.diff(v_smooth)
    dt = np.diff(time_us)
    # protect against length mismatch (rare scope quirk)
    n = min(dv.size, dt.size)
    deriv = np.zeros(n)
    np.divide(dv[:n], dt[:n], out=deriv, where=dt[:n] != 0)
    deriv = _smooth(deriv, smooth_window)
    if deriv.size > 2 * edge_zero:
        deriv[:edge_zero] = 0.0
        deriv[-edge_zero:] = 0.0
    return np.abs(deriv), deriv  # (|dV/dt|, signed dV/dt)


def _find_n_peaks(deriv_abs: np.ndarray, n_target: int,
                  start_thresh: float, max_seconds: float = 2.0,
                  max_iterations: int = 50) -> np.ndarray:
    """Adaptive peak finder that returns exactly ``n_target`` peaks.

    Mirrors the while-loop in ``getAccess.m``: start at 0.9 × peak_max, decrease
    threshold by 10%% if too few peaks, re-smooth and restart if too many.

    Bounded by BOTH ``max_seconds`` and ``max_iterations`` — on a
    pathological waveform the original time-only bound could chew
    through hundreds of iterations of `_smooth + find_peaks` each
    hovering near the deadline.  The iteration cap fails fast and
    falls through to the best-effort lookup.
    """
    import time as _time
    deadline = _time.time() + max_seconds
    thresh = start_thresh
    deriv = deriv_abs.copy()
    iteration = 0
    # Adaptive smoothing-window escalation: each time we get "too
    # many peaks" we widen the smooth window.  Starting at 5 and
    # doubling on each over-shoot collapses the typical
    # noise-cluster-of-many-tiny-peaks scenario in 2-3 iterations
    # instead of 8-10 of small constant widenings — same number of
    # peaks at the end, far fewer `_smooth + find_peaks` round-trips.
    smooth_window = 5
    while _time.time() < deadline and iteration < max_iterations:
        iteration += 1
        peaks, _info = find_peaks(deriv, height=thresh)
        if peaks.size == n_target:
            return peaks
        if peaks.size > n_target:
            # too many: smooth more (geometric escalation) and reset
            deriv = _smooth(deriv, smooth_window)
            smooth_window = min(smooth_window * 2, 64)
            thresh = float(np.max(deriv)) * 0.9
        else:
            thresh *= 0.9
            if thresh < 1e-9:
                break
    # Best effort: return whatever we have, padded/truncated
    peaks, _ = find_peaks(deriv_abs, height=max(start_thresh * 0.05, 1e-9))
    return peaks[:n_target] if peaks.size >= n_target else peaks


def _localize_access_point(deriv_abs: np.ndarray, peak_idx: int,
                           fit_len: int = 50, walk_len: int = 30,
                           dev_thresh: float = 1.0,
                           max_seconds: float = 0.5,
                           max_walk_len: Optional[int] = None) -> int:
    """Given a derivative peak, find where the curve first departs from a
    linear fit by ``dev_thresh`` (relative). Vectorised port of the
    ``getAccess.m`` linear-fit deviation search (lines 230–280).

    The original MATLAB code iterates ``idx_test`` forward sample by sample,
    checking a ``walk_len``-wide window each time. Because the linear fit is
    computed once and held constant, the deviation at any given absolute
    index is identical regardless of which ``idx_test`` evaluates it. So we
    can compute the deviation across the whole search range in one shot and
    return the first hit. ``walk_len`` and ``max_seconds`` survive only as
    legacy knobs (ignored here).
    """
    n = deriv_abs.size
    end_fit = min(peak_idx + fit_len, n)
    if end_fit - peak_idx < 4:
        return min(peak_idx + 1, n - 1)
    xs = np.arange(peak_idx, end_fit, dtype=float)
    ys = deriv_abs[peak_idx:end_fit]
    slope, intercept = np.polyfit(xs, ys, 1)

    upper = n - 50
    # Keep the settling/marker sample AT the current transition — never walk
    # deep into the phase where the electrode polarization lives (operator).
    if max_walk_len is not None and max_walk_len > 0:
        upper = min(upper, peak_idx + int(max_walk_len))
    start = peak_idx + 1
    if upper <= start:
        return min(start, n - 1)
    xs_full = np.arange(start, upper, dtype=float)
    fit_vals = slope * xs_full + intercept
    data = deriv_abs[start:upper]
    with np.errstate(divide="ignore", invalid="ignore"):
        denom = np.where(np.abs(fit_vals) > 1e-12, fit_vals, 1e-12)
        dev = np.abs((fit_vals - data) / denom)
    hit = np.where(dev > dev_thresh)[0]
    if hit.size:
        return int(start + hit[0]) + 1
    return min(upper, n - 1)


#: Post-edge iR-extraction window (µs).  The linear fit sits CLOSE to the edge
#: — starting ``_ACCESS_AFTER_START_US`` after the ``|dV/dt|`` peak (past the
#: ~1 µs current-rise / switching transient) and spanning ``_ACCESS_AFTER_WIN_US``.
_ACCESS_AFTER_START_US = 1.0
_ACCESS_AFTER_WIN_US = 2.0

#: The access step is the FAST ohmic jump AT the current transition, so an
#: access point is localized in a TIGHT window around the pattern-derived edge
#: time — never a distant mid-phase / post-polarization |dV/dt| feature, and
#: the settling/marker sample never walks deep into the phase where the
#: electrode polarization (E_mc/E_ma) lives (operator: "Va/Ra cannot be after
#: the electrode polarization … the access voltage is localized around
#: vertical rises/falls from the current pattern").
#:  * ``_ACCESS_EDGE_SEARCH_US`` — half-window (µs) around each expected
#:    current-edge time for the |dV/dt| peak search.  Comfortably covers the
#:    onset-detection skew (~1-2 µs) while excluding far peaks.
#:  * ``_ACCESS_SETTLE_MAX_US`` — max forward walk (µs) from the edge to the
#:    settling/marker sample, so the marker stays AT the vertical transition.
_ACCESS_EDGE_SEARCH_US = 12.0
_ACCESS_SETTLE_MAX_US = 15.0


def access_step_by_extrapolation(time_us, v, peak_idx: int, acc_idx: int,
                                 *, before_len: int = 40, before_gap: int = 10,
                                 after_len: int = 30,
                                 after_start_us: float = _ACCESS_AFTER_START_US,
                                 after_win_us: float = _ACCESS_AFTER_WIN_US
                                 ) -> float:
    """The IR STEP at a current edge, via linear extrapolation of V_mon on both
    sides of the edge back to the edge moment (the ``|dV/dt|`` peak).

    Fits the PRE-edge plateau (``[peak-before_gap-before_len : peak-before_gap]``)
    and a SHORT POST-edge window placed CLOSE to the edge, extrapolates BOTH to
    ``t[peak]``, and returns ``|v_after - v_before|`` — the pure IR jump with the
    linear cap ramp subtracted out (independent of the current source's finite
    rise time).

    **The post-edge window is NEAR-EDGE + short** (``[peak + after_start_us :
    + after_win_us]``, TIME-based so it's sample-rate-independent), NOT the
    localizer's late settling point.  For a healthy electrode V after the edge
    is ``iR + (I/C)·t`` — a LINEAR ramp — so a close or a late window
    extrapolate to the same iR (unchanged, ~1 kΩ).  But a CAPACITIVE / high-Z
    (leaky-capacitor) electrode has an EXPONENTIAL post-edge ramp that FLATTENS;
    a LATE window fits that shallow tail and OVER-projects the extrapolation
    back to the edge, inflating R_a (exp_vt_max_pcc: CH07 read ~20 kΩ at 6 µA
    with no visible iR step).  Fitting the NEAR-EDGE constant-current slope
    extracts the true small ohmic step instead (operator: "the access
    resistance is being determined later than the actual iR drop … extract the
    small ohmic step").  The despiked trace the caller passes rejects the
    switching spike, so a close window is safe.  ``after_len`` / ``acc_idx``
    are the legacy fallback when the time axis is unusable.

    SHARED by BOTH the experiment metrics (:func:`access_voltage_and_resistance`)
    and the calibration R-extraction (operator: "calibration should use the same
    method for access points"); the test-board R+C is a pure LINEAR ramp so the
    close window leaves calibration unchanged.  Returns NaN when a window is too
    short."""
    v = np.asarray(v, dtype=float)
    t = np.asarray(time_us, dtype=float)
    n = v.size
    peak_idx = int(peak_idx)
    acc_idx = int(acc_idx)
    if n < 8 or t.size != n or not (0 <= peak_idx < acc_idx < n):
        return float("nan")
    dt = float(np.median(np.diff(t))) if n > 1 else 0.0
    if dt > 0 and np.isfinite(dt):
        acc_start = peak_idx + max(1, int(round(after_start_us / dt)))
        hi_end = min(acc_start + max(4, int(round(after_win_us / dt))), n)
    else:                                    # unusable time axis → legacy window
        acc_start = acc_idx
        hi_end = min(acc_idx + after_len, n)
    lo_end = max(peak_idx - before_gap, 0)
    lo_start = max(lo_end - before_len, 0)
    if acc_start >= n or hi_end - acc_start < 4 or lo_end - lo_start < 4:
        return float("nan")
    try:
        sh, ih = np.polyfit(t[acc_start:hi_end], v[acc_start:hi_end], 1)  # post-edge
        sl, il = np.polyfit(t[lo_start:lo_end], v[lo_start:lo_end], 1)    # pre-edge
        tp = float(t[peak_idx])
        return abs((sh * tp + ih) - (sl * tp + il))
    except Exception:
        return float("nan")


def access_voltage_and_resistance(
    time_us: np.ndarray, v_trace: np.ndarray, pattern: PulsePattern,
    *, onset_us: Optional[float] = None,
    labels: Optional[List[Tuple[int, str]]] = None,
) -> Tuple[List[float], List[float], List[int]]:
    """Compute access voltages and resistances per phase boundary.

    Faithful Python port of ``getAccess.m``. Returns ``(V_a_list, R_a_list,
    access_idx_list)`` whose length is the number of access points the pulse
    has:

    * Leading phase 1 (always present)
    * Trailing phase 1, Leading phase 2 (if there's an interphase delay)
    * Trailing phase 2 (if there's a discharge delay)

    For triphasic patterns we treat phase 2 like the second of three phases
    and add a third leading/trailing pair if there's a second interphase
    delay; in practice the IEEE NER triphasic uses interphase + interphase +
    discharge so we get up to 6 access points.

    ``onset_us`` — the detected pulse-onset time (:func:`pulse_onset_us`).
    When provided, each labelled access boundary is localized by a
    TIME-ANCHORED, region-partitioned peak search: the expected boundary
    time is computed from ``onset_us`` + the cumulative phase timing, and
    each boundary takes the largest ``|dV/dt|`` peak in the trace region it
    OWNS (between its midpoints with the neighbouring boundaries).  This maps
    peak *i* to label *i* by construction, fixing the failure mode where a
    leading-edge compliance double-bump produced two clustered global peaks
    and the time-ordered assignment shoved the trailing-phase-1 access point
    back to the pulse start (operator: "the second access voltage/resistance
    is floating near the beginning of the pulse").  ``None`` (default) keeps
    the legacy global-peak-in-time-order path — used by the calibration
    R-extraction, which has its own onset convention and verified window
    fit.

    ``labels`` — pass a precomputed :func:`access_index_labels` list so the
    active-electrode, return-electrode, and marker call sites all use the
    SAME (possibly data-driven-augmented) boundary set, keeping the returned
    arrays parallel.  When ``None`` the labels are computed here from the
    pattern + this trace (data-driven when ``onset_us`` is given).
    """
    if v_trace.size < 20:
        return [], [], []

    # --- 1. Shape-aware boundary list --------------------------------------
    # Build the expected (phase, role) labels using per-shape leading /
    # trailing edge factors; this gives one entry per boundary that has
    # a clean current-step (rect / linear / bowtie / halfpipe / exp-
    # decay leading) and OMITS boundaries where the current ramps from
    # or to zero (sinusoidal both edges, linear-increasing leading,
    # linear-decreasing trailing, exp-decay trailing) UNLESS the trace
    # shows a real vertical IR step there (data-driven augmentation). The
    # output arrays have the same length as this label list, so the caller
    # can ``zip`` to dispatch by role.
    if labels is None:
        labels = access_index_labels(pattern, time_us=time_us,
                                     v_trace=v_trace, onset_us=onset_us)
    n_access_expected = len(labels)
    if n_access_expected == 0:
        return [], [], []

    polarity = pattern.polarity

    # Whether the pattern includes an interphase delay (a quiet gap
    # between phases 1 and 2 where the current returns to zero) and a
    # discharge delay (a quiet gap AFTER the last phase, where the
    # electrode passively recovers). These flags gate steps 6b and 6c
    # below, which look for the trailing-edge / leading-edge access
    # points that only exist when the current actually steps to / from
    # zero at those boundaries.
    #
    #   * has_iph — phase 1 has a non-zero ``delay_after_us``, AND there
    #     is at least one subsequent phase to lead. Monophasic patterns
    #     have no interphase delay by definition (only one phase). For
    #     biphasic and triphasic this collapses to "is there a gap
    #     between phase 1 and phase 2?" — the only interphase boundary
    #     the current code path measures (the second interphase delay
    #     in a true triphasic is captured by the shape-aware label
    #     list in step 1, which already drops boundaries that don't
    #     produce a clean step).
    #   * has_dd — the LAST phase has a non-zero ``delay_after_us``.
    #     This is the recovery / passive-discharge window every
    #     experiment-grade pattern includes; we use the gap to compute
    #     the trailing-phase access point (the V_a4 entry below).
    #
    # Both flags default to ``False`` for monophasic-no-recovery
    # patterns; the caller still gets the leading-edge V_a out of
    # step 6a so a bare monophasic pulse isn't a special case.
    has_iph = (pattern.num_phases >= 2
               and pattern.phases[0].delay_after_us > 0)
    has_dd = pattern.phases[-1].delay_after_us > 0

    # Pre-pulse baseline — samples before the pulse onset.  With a digital
    # trigger (or no onset given) the pulse begins at t=0, so ``time < 0`` is
    # the pre-pulse window (legacy behaviour).  Under the I_mon trigger the
    # scope fires mid-pulse and the onset is elsewhere, so anchor the window
    # to ``onset_us`` (matching ``compute_metric_markers``' own baseline).
    pre_cut = 0.0 if onset_us is None else (float(onset_us) - 1.0)
    pre_mask = time_us < pre_cut
    pre_voltage = float(np.mean(v_trace[pre_mask])) if pre_mask.any() else float(v_trace[0])

    # --- 2. Smoothed |dV/dt| ------------------------------------------------
    # Compute the smoothed V_mon trace ONCE up front and pass it to
    # both `_abs_derivative` (which would otherwise smooth again
    # internally) AND step 5's driving-extrema lookup.  Saves one
    # `uniform_filter1d` call per capture; ~0.5-1 ms on a 20 k
    # sample trace.
    v_arr_f = np.asarray(v_trace, dtype=float)
    v_smooth = _smooth(v_arr_f)
    # DESPIKED trace for SETTLED-value reads (driving extrema + access
    # plateaus) — rejects the switching spikes/ringing that otherwise
    # mis-report V_a / V_d at small currents.  The |dV/dt| edge LOCALIZATION
    # below stays on ``v_smooth`` (despiking blurs the IR edge it needs).
    v_ds = _despike(v_arr_f, time_us)
    deriv_abs, deriv_signed = _abs_derivative(
        time_us, v_arr_f, v_smooth_precomputed=v_smooth)
    peak_max = float(np.max(deriv_abs)) if deriv_abs.size else 0.0
    if peak_max <= 0:
        return [], [], []

    # --- 3 + 4. Localize each labelled access index -----------------------
    access_idx: List[int] = []
    peak_idx: List[int] = []   # |dV/dt| peak (edge moment) parallel to access_idx
    if onset_us is None:
        # LEGACY PATH: find exactly N global |dV/dt| peaks and map them to
        # the labels in time order.  Fragile when a single boundary throws
        # two close peaks (compliance double-bump) — kept only for the
        # calibration caller, which feeds clean test-board signals.
        n_peaks_expected = n_access_expected
        peaks = _find_n_peaks(deriv_abs, n_peaks_expected, peak_max * 0.9)
        if peaks.size == 0:
            return ([float("nan")] * n_access_expected,
                    [float("nan")] * n_access_expected,
                    [-1] * n_access_expected)
        for i in range(min(n_access_expected, peaks.size)):
            access_idx.append(_localize_access_point(deriv_abs, int(peaks[i])))
            peak_idx.append(int(peaks[i]))
    else:
        # TIME-ANCHORED PATH: derive each label's expected boundary time
        # from the onset + cumulative phase timing, then let each boundary
        # take the largest |dV/dt| peak in the trace region it OWNS (between
        # its midpoints with the adjacent boundaries).  Peak i ↔ label i by
        # construction, so a leading-edge double-bump can't steal the
        # trailing-phase slot.
        phase_start: List[float] = []
        _acc = float(onset_us)
        for ph in pattern.phases:
            phase_start.append(_acc)
            _acc += ph.width_us + ph.delay_after_us
        exp_t: List[float] = []
        for (ph_idx, role) in labels:
            if 0 <= ph_idx < len(phase_start):
                bt = phase_start[ph_idx]
                if role == "trail":
                    bt = bt + pattern.phases[ph_idx].width_us
            else:
                bt = float(onset_us)
            exp_t.append(bt)
        sz = deriv_abs.size
        exp_idx = [int(np.clip(np.searchsorted(time_us, et), 0, sz - 1))
                   for et in exp_t]
        N = len(exp_idx)
        # Sample interval → the tight edge-search half-window + the settling
        # walk cap (in samples), so the access point stays AT the vertical
        # current transition (operator #10).
        _dt = float(np.median(np.diff(time_us))) if time_us.size > 1 else 0.0
        edge_w = (max(1, int(round(_ACCESS_EDGE_SEARCH_US / _dt)))
                  if _dt > 0 else sz)
        settle_len = (max(4, int(round(_ACCESS_SETTLE_MAX_US / _dt)))
                      if _dt > 0 else None)
        for i, b in enumerate(exp_idx):
            if i > 0:
                lo = (exp_idx[i - 1] + b) // 2
            else:
                gap = (exp_idx[1] - b) if N > 1 else b
                lo = b - max(gap // 2, 1)
            if i < N - 1:
                hi = (exp_idx[i + 1] + b) // 2
            else:
                gap = (b - exp_idx[i - 1]) if N > 1 else (sz - b)
                hi = b + max(gap // 2, 1)
            lo = int(max(0, min(lo, sz - 2)))
            hi = int(max(lo + 1, min(hi, sz)))
            # NARROW the peak search to a tight window around the pattern's
            # expected current-edge time — the access step is the fast ohmic
            # jump AT the transition, not a distant mid-phase / post-E_pol
            # |dV/dt| feature (operator: "the access voltage is localized
            # around vertical rises/falls from the current pattern").  Falls
            # back to the wider midpoint region if the narrow window degenerates.
            nlo = int(max(lo, b - edge_w))
            nhi = int(min(hi, b + edge_w + 1))
            if nhi - nlo >= 1:
                lo, hi = nlo, nhi
            local = deriv_abs[lo:hi]
            if local.size == 0:
                _b = int(min(max(b, 0), sz - 1))
                access_idx.append(_b)
                peak_idx.append(_b)
                continue
            peak_local = lo + int(np.argmax(local))
            access_idx.append(_localize_access_point(
                deriv_abs, peak_local, max_walk_len=settle_len))
            peak_idx.append(peak_local)

    # Pad if we're short (best-effort fallback)
    while len(access_idx) < n_access_expected:
        access_idx.append(-1)
    while len(peak_idx) < n_access_expected:
        peak_idx.append(-1)

    # --- 5. Driving voltage extrema (used as references for trailing V_a) --
    # Use the DESPIKED trace so the driving extremum is the SETTLED peak,
    # not a switching spike (which at small currents is the global min/max).
    v_filt = v_ds
    if polarity == -1:
        driving1_idx = int(np.argmin(v_filt))
        driving2_idx = int(np.argmax(v_filt))
    else:
        driving1_idx = int(np.argmax(v_filt))
        driving2_idx = int(np.argmin(v_filt))

    # --- 6. Compute V_a per access point (LABEL-DRIVEN) --------------------
    # One entry per ``labels`` entry, in label order, so the returned
    # ``va`` / ``ra`` arrays stay PARALLEL to ``access_idx`` / ``labels``
    # regardless of which delays exist.  The previous form hard-coded
    # 6a/6b/6c gated on ``has_iph`` / ``has_dd``; when there was no
    # interphase delay (a delay-less ph1→ph2 boundary, which
    # ``access_index_labels`` still reports as a fused ``(k+1,'lead')``)
    # step 6b was skipped, leaving ``va`` SHORTER than ``access_idx`` and
    # MISALIGNED — the trailing-phase value landed under the leading
    # label and the real trailing point was dropped.  This loop computes
    # every label, byte-identical to the old code for the common
    # interphase+discharge biphasic (verified), and additionally handles
    # the delay-less and monophasic-trailing cases.
    amp_of_phase = [abs(p.amplitude_ua) for p in pattern.phases]

    def _driving_idx_for(phase_idx: int) -> int:
        # Phase parity → driving extremum: even (cathodic for polarity -1)
        # uses driving1, odd (anodic) uses driving2.  Matches the biphasic
        # original; best available for triphasic (only two global extrema
        # are localized, as before).
        return driving1_idx if (phase_idx % 2 == 0) else driving2_idx

    va: List[float] = []
    amp_for: List[float] = []  # corresponding |I_amp| for R_a calc
    for i, (ph_idx, role) in enumerate(labels):
        acc = access_idx[i] if i < len(access_idx) else -1
        pk = peak_idx[i] if i < len(peak_idx) else -1
        amp = amp_of_phase[ph_idx] if 0 <= ph_idx < len(amp_of_phase) else 0.0
        # A DELAY-LESS ph(k-1)→phk boundary steps directly between two LIVE
        # phases, so the current change (and thus the IR step) is the SUM of
        # both phase amplitudes (the polarity flips); every other boundary
        # steps to / from ZERO, so it's a single phase's amplitude.
        if (role == "lead" and ph_idx > 0
                and pattern.phases[ph_idx - 1].delay_after_us <= 0):
            amp = amp + (amp_of_phase[ph_idx - 1]
                         if ph_idx - 1 < len(amp_of_phase) else 0.0)
        # V_a = the PURE IR STEP via before/after linear extrapolation of the
        # DESPIKED V_mon back to the |dV/dt| edge moment — the SHARED method the
        # calibration R-extraction uses too (operator: "calibration should use
        # the same method for access points as the experiment").  The cap ramp
        # + the current-source rise time are subtracted out by the two-sided
        # extrapolation, so this reads a bit LOWER + cleaner than the old
        # settling-plateau read (which sat ~20-30 samples past the edge and
        # carried the cap charge accumulated over that window).
        v_a = access_step_by_extrapolation(time_us, v_ds, pk, acc)
        va.append(float(v_a))
        amp_for.append(float(amp))

    # --- 7. R_a (kOhm) ------------------------------------------------------
    ra: List[float] = []
    for v, a_ua in zip(va, amp_for):
        if a_ua <= 0 or not np.isfinite(v):
            ra.append(float("nan"))
        else:
            ra.append(abs(v) / (a_ua * 1e-6) / 1e3)

    return [abs(x) for x in va], ra, access_idx


# ---------------------------------------------------------------------------
# Polarization (E_pol)
# ---------------------------------------------------------------------------
def access_index_labels(pattern: PulsePattern, *,
                        time_us: Optional[np.ndarray] = None,
                        v_trace: Optional[np.ndarray] = None,
                        onset_us: Optional[float] = None,
                        ) -> List[Tuple[int, str]]:
    """Per-entry ``(phase_idx, role)`` labels for the ``access_idx`` array
    returned by :func:`access_voltage_and_resistance`.

    The list is built shape-aware: every potential phase boundary is
    considered, and an entry is emitted only when the current step at
    that boundary is large enough to produce an unambiguous V_a /
    R_a measurement. Concretely:

      * Pre-pulse → phase 1 leading edge: included if phase 1's
        leading factor (current at t=0 / peak) ≥ threshold.
      * For each phase k with ``delay_after_us > 0`` we emit
        ``(k, 'trail')`` if phase k's trailing factor ≥ threshold,
        and ``(k+1, 'lead')`` if phase k+1's leading factor ≥
        threshold.
      * For a delay-less boundary between phase k and k+1, the trail
        of k and the lead of k+1 fuse into one combined event; we
        emit a single ``(k+1, 'lead')`` if the combined |Δ I| / peak
        ≥ threshold.

    Threshold is :data:`_STEP_FACTOR_THRESHOLD` (0.5 of peak), so
    e.g. ``linear_decreasing`` contributes a leading entry but no
    trailing one, ``linear_increasing`` contributes a trailing entry
    but no leading one, and sinusoidal contributes none at all.

    **Data-driven augmentation** — when ``time_us`` / ``v_trace`` /
    ``onset_us`` are all provided, a boundary the NOMINAL shape factor
    SUPPRESSES is STILL emitted if the trace shows a genuine vertical IR step
    there (:func:`_boundary_has_ir_step`).  This lets a sinusoidal / gaussian
    pulse contribute access points where the real data shows an ohmic drop
    (operator: "sinusoidal and gaussian … vertical i-R drops … access
    voltage/resistance should be allowed to be determined") without ever
    inventing an access point on a smooth ramp.  Nominal-only (no trace) keeps
    the exact legacy behaviour — used by callers that only need the pattern
    shape (e.g. the calibration R-extraction).
    """
    n = pattern.num_phases
    if n == 0:
        return []
    peak_amp = max((abs(p.amplitude_ua) for p in pattern.phases), default=1.0)
    if peak_amp <= 0:
        peak_amp = 1.0
    cutoff = _STEP_FACTOR_THRESHOLD * peak_amp

    # Data-driven vertical-IR-step check needs the absolute boundary times.
    _have_trace = (time_us is not None and v_trace is not None
                   and onset_us is not None
                   and np.asarray(v_trace).size == np.asarray(time_us).size
                   and np.asarray(time_us).size >= 8)
    if _have_trace:
        _pstart: List[float] = []
        _acc = float(onset_us)
        for ph in pattern.phases:
            _pstart.append(_acc)
            _acc += ph.width_us + ph.delay_after_us

    def _emit(nominal_ok: bool, boundary_us: float) -> bool:
        if nominal_ok:
            return True
        return bool(_have_trace
                    and _boundary_has_ir_step(time_us, v_trace, boundary_us))

    labels: List[Tuple[int, str]] = []
    # Phase 1 leading edge — pre-pulse (0) → phase1 leading-factor × peak.
    f_lead0, _ = _phase_step_factors(pattern.phases[0].shape)
    _b0 = _pstart[0] if _have_trace else 0.0
    if _emit(abs(pattern.phases[0].amplitude_ua) * f_lead0 >= cutoff, _b0):
        labels.append((0, "lead"))

    for k in range(n):
        ph = pattern.phases[k]
        _, f_trail_k = _phase_step_factors(ph.shape)
        is_last = (k == n - 1)
        delay = ph.delay_after_us
        _pk_end = (_pstart[k] + ph.width_us) if _have_trace else 0.0

        if delay > 0:
            # Trailing of phase k: amp drops to 0 over the delay.
            if _emit(abs(ph.amplitude_ua) * f_trail_k >= cutoff, _pk_end):
                labels.append((k, "trail"))
            # Leading of phase k+1 (if any): amp rises from 0.
            if not is_last:
                next_ph = pattern.phases[k + 1]
                f_lead_next, _ = _phase_step_factors(next_ph.shape)
                _bnext = _pstart[k + 1] if _have_trace else 0.0
                if _emit(abs(next_ph.amplitude_ua) * f_lead_next >= cutoff,
                         _bnext):
                    labels.append((k + 1, "lead"))
        elif not is_last:
            # No delay: the current jumps directly from phase k's
            # trailing value to phase k+1's leading value. Net step
            # magnitude is the absolute difference of the signed
            # values (phases usually flip polarity, so the magnitudes
            # add). Reported as (k+1, 'lead') by convention.
            next_ph = pattern.phases[k + 1]
            f_lead_next, _ = _phase_step_factors(next_ph.shape)
            net_step = abs(ph.amplitude_ua * f_trail_k
                           - next_ph.amplitude_ua * f_lead_next)
            _bnext = _pstart[k + 1] if _have_trace else 0.0
            if _emit(net_step >= cutoff, _bnext):
                labels.append((k + 1, "lead"))
    return labels


def _phase_current_ends_near_zero(ph, frac: float = 0.1) -> bool:
    """True if the phase's current tapers to ~0 at its END, leaving no
    trailing IR (access) step to read.

    Exp-decay and linear-decreasing phases START at their peak (a clean
    LEADING access) but ramp to ~0, so their trailing edge carries no
    current step (sine / gaussian taper at both ends).  For such a phase,
    with NO interphase / discharge delay after it there is also no trailing
    IR step to extrapolate.  The caller (operator method) then reads E_pol
    at the FLATTEST point (min |dV/dt|) near the phase end instead — where
    the current has decayed so V is the settled interface polarization with
    no ohmic drop (operator: "for exp-decay or linear-decreasing phases
    there is no trailing access voltage … let the end of the phase be the
    electrode polarization … use the absolute minimum of the derivative in
    that phase to pinpoint the electrode polarization").

    A non-zero ``offset_ua`` floor makes the current end at the floor (a
    real current-off step at the phase boundary), so this returns False and
    E_pol stays computable.  Data-driven off the shape's rendered
    breakpoints so it tracks the actual contour rather than a hard-coded
    shape list."""
    from .waveforms import shape_breakpoints
    try:
        bps = shape_breakpoints(
            amplitude_ua=float(ph.amplitude_ua), width_us=float(ph.width_us),
            shape=ph.shape, bump_count=int(getattr(ph, "bump_count", 3)),
            tau_us=float(getattr(ph, "tau_us", 0.0)),
            offset_ua=float(getattr(ph, "offset_ua", 0.0)))
    except Exception:
        return False
    if not bps:
        return False
    peak = max((abs(a) for (_, a) in bps), default=0.0)
    if peak <= 0:
        return False
    return abs(bps[-1][1]) < frac * peak


def polarization_per_phase(
    time_us: np.ndarray, e_trace: np.ndarray, pattern: PulsePattern,
    *, depol_us: float = DEPOLARIZATION_TIME_US,
    method: str = "auto",
    access_idx: Optional[List[int]] = None,
    onset_us: float = 0.0,
    driving_per_phase: Optional[List[float]] = None,
    leading_access_per_phase: Optional[List[float]] = None,
    trailing_epol_per_phase: Optional[List[float]] = None,
) -> List[float]:
    """E_pol per phase. Four methods:

    ``"time"``
        Sample ``e_trace`` at ``phase_end + depol_us`` (default 12 µs after
        each phase). Simple and stable but a fixed time offset is a
        compromise: it's too short for some coatings that recover slowly,
        too long for short interphase delays.
    ``"derivative"`` (recommended)
        Use the trailing-access plateau index from
        :func:`access_voltage_and_resistance` and sample ``e_trace`` there.
        This is mathematically equivalent to
        ``driving_potential − trailing_access_voltage`` because the trailing
        access voltage is already defined as
        ``e_trace[trail_idx] − e_trace[driving_idx]``. Adapts to the
        actual ohmic-recovery time of the electrode rather than guessing.
    ``"auto"`` (default)
        Use ``"derivative"`` for any phase that has a usable trailing
        access plateau (i.e. the phase has an interphase or discharge
        delay after it and the access detector found one); fall back to
        ``"time"`` for any phase that doesn't.
    ``"operator"`` (the lab's canonical definition)
        Use the ``"time"`` sample when the phase has a trailing recovery
        delay (interphase or discharge — the current has settled to zero,
        so ``phase_end + depol`` is pure polarization, matching the IEEE
        NER ``E_mc`` convention); otherwise (a delay-less boundary, no
        quiet window to sample) use ``driving_potential − leading_access``
        for that phase, re-signed by the phase polarity.  Requires
        ``driving_per_phase`` + ``leading_access_per_phase`` (magnitudes)
        for the no-delay branch; falls back to the time sample if either
        is missing.

    Parameters
    ----------
    time_us, e_trace
        Time vector (µs) and the active-electrode trace (V).
    pattern : PulsePattern
        Pulse anatomy — determines how many polarization values come back
        and which phases have a trailing access point.
    depol_us : float
        Used by the ``"time"`` method (and the ``"auto"`` fallback).
        Defaults to 12 µs (matches the MATLAB code and IEEE NER paper).
    method : {'time','derivative','auto'}
        See above.
    access_idx : list[int], optional
        Required when ``method`` is ``'derivative'`` or ``'auto'``. Pass the
        indices returned by :func:`access_voltage_and_resistance`.

    Returns
    -------
    list[float]
        One value per phase. NaN when the requested method couldn't produce
        a value for that phase.
    """
    n = pattern.num_phases
    if e_trace.size == 0:
        return [float("nan")] * n

    method = method.lower()
    if method not in ("time", "derivative", "auto", "operator"):
        raise ValueError(f"Unknown method {method!r}")

    # ----- time-based fallback (always computable) ------------------------
    def _time_sample(phase_idx: int) -> float:
        """Sample ``e_trace`` at the end of phase ``phase_idx`` plus depol_us —
        the LAST sample at or before ``phase_end + depol`` (MATLAB
        ``find(time <= phaseWidth + depolTime, 1, 'last')``: the sample rarely
        lands exactly at +12 µs, so take the last one that's ≤ it rather than
        the first one ≥ it)."""
        # Anchored at the DETECTED pulse onset — with the I_mon trigger
        # the chain from t=0 sampled phase 2's tail as "Epol1".
        cursor = float(onset_us)
        for k, ph in enumerate(pattern.phases):
            cursor += ph.width_us
            if k == phase_idx:
                t_sample = cursor + depol_us
                idx = int(np.searchsorted(time_us, t_sample, side="right")) - 1
                idx = int(np.clip(idx, 0, e_trace.size - 1))
                return float(e_trace[idx])
            cursor += ph.delay_after_us
        return float("nan")

    if method == "time":
        return [_time_sample(k) for k in range(n)]

    if method == "operator":
        # Full E_pol decision (MATLAB getVoltageMetrics.m + Cogan 2008), per
        # phase k with trailing delay d_k (interphase for a middle phase,
        # discharge for the last).  Operator-specified:
        #   1. d_k >= depol         → TIME method (a quiet recovery window fits
        #      the +12 µs sample, so phase_end+12 is IR-free polarization).
        #   2. 0 < d_k < depol      → phase-k TRAILING access point: the delay
        #      is too SHORT to fit 12 µs, but the current DOES step to zero, so
        #      the post-IR-drop plateau is the IR-free polarization.
        #   3. d_k == 0, phase has a CLEAN leading access (phase 1, or a phase
        #      preceded by a delay) → driving(k) − leading_access(k).
        #   4. d_k == 0, no clean lead (e.g. phase 2 with NO interphase delay
        #      before it AND no discharge delay after it — "no second phase
        #      leading, and no discharge delay") → NaN (can't be determined).
        depol = float(depol_us)
        dpp = driving_per_phase or []
        lap = leading_access_per_phase or []
        tap = trailing_epol_per_phase or []
        # Despiked trace (median filter) for the settled-value read below.
        _ds_full = _despike(e_trace, time_us)

        def _epol_min_derivative(phase_idx: int) -> float:
            """E_pol for a phase whose current tapers to ~0 at its end: the
            settled voltage at the FLATTEST point (min |dV/dt|) in the LATTER
            half of the phase, where the current has decayed so V is the
            interface polarization with no ohmic drop (operator: "let the end
            of the phase be the electrode polarization … use the absolute
            minimum of the derivative in that phase to pinpoint it" — the same
            flat-point read used for the ending-interphase potential).  The
            latter-half restriction keeps a mid-phase inflection (IR falling
            while polarization rises) from masquerading as the settled point."""
            cursor = float(onset_us)
            for kk, phk in enumerate(pattern.phases):
                if kk == phase_idx:
                    t_start = cursor
                    t_end = cursor + phk.width_us
                    t_lo = t_start + 0.5 * phk.width_us
                    idxs = np.nonzero(
                        (time_us >= t_lo) & (time_us <= t_end))[0]
                    if idxs.size < 4:
                        return _time_sample(phase_idx)
                    seg = _ds_full[idxs]
                    dv = np.abs(np.gradient(seg))
                    return float(seg[int(np.argmin(dv))])
                cursor += phk.width_us + phk.delay_after_us
            return float("nan")

        res: List[float] = []
        for k, ph in enumerate(pattern.phases):
            d_k = float(ph.delay_after_us)
            if d_k >= depol:                                   # (1) time
                res.append(_time_sample(k))
                continue
            if d_k > 0:                                        # (2) trailing access
                tv = tap[k] if k < len(tap) else float("nan")
                res.append(float(tv) if np.isfinite(tv) else _time_sample(k))
                continue
            # (3a) a phase whose current TAPERS TO ~0 at its end (exp-decay,
            # linear-decreasing without a current offset) has no trailing IR
            # step — but its END *is* the polarization (current ~0 → no ohmic
            # drop): read the settled voltage at the flattest point (min
            # |dV/dt|) in the phase, regardless of the leading access
            # (operator: "let the end of the phase be the electrode
            # polarization … use the absolute minimum of the derivative").
            if _phase_current_ends_near_zero(ph):
                res.append(_epol_min_derivative(k))
                continue
            # (3)/(4) delay-less flat/constant-ending phase: needs a clean
            # leading access to subtract the (constant) ohmic drop.
            has_lead = (k == 0) or (pattern.phases[k - 1].delay_after_us > 0)
            d = dpp[k] if k < len(dpp) else float("nan")
            la = lap[k] if k < len(lap) else float("nan")
            if has_lead and np.isfinite(d) and np.isfinite(la):
                sgn = -1.0 if float(ph.amplitude_ua) < 0 else 1.0
                res.append(sgn * (abs(d) - abs(la)))
            else:
                res.append(float("nan"))                       # (4) undetermined
        return res

    # ----- derivative / auto: sample at the trailing access plateau -------
    out: List[float] = [float("nan")] * n
    if access_idx is not None and len(access_idx) > 0:
        labels = access_index_labels(pattern)
        for idx, (phase_idx, role) in zip(access_idx, labels):
            if role != "trail":
                continue
            if idx is None or idx < 0 or idx >= e_trace.size:
                continue
            out[phase_idx] = float(e_trace[idx])

    if method == "derivative":
        return out

    # auto: any phase whose derivative-method value is NaN gets the time fallback
    for k in range(n):
        if not np.isfinite(out[k]):
            out[k] = _time_sample(k)
    return out


# ---------------------------------------------------------------------------
# Q_inj and C_d
# ---------------------------------------------------------------------------
def charge_injection_mc_per_cm2(
    pattern: PulsePattern, surface_area_um2: float,
) -> Tuple[float, float]:
    """Return (Q_ph in nC, Q_inj in mC/cm²) for the *excitation* phase."""
    q_ph_nc = pattern.charge_per_phase_nc                # nC
    area_cm2 = surface_area_um2 * 1e-8                   # µm² -> cm²
    if area_cm2 <= 0:
        return q_ph_nc, float("nan")
    # nC / cm² -> mC/cm² requires *1e-6
    q_inj_mc_cm2 = (q_ph_nc * 1e-6) / area_cm2
    return q_ph_nc, q_inj_mc_cm2


def driving_capacitance(q_inj_max_mc_cm2: float, v_d_v: float) -> float:
    """C_d = max(Q_inj) / V_d, mF/cm²."""
    if v_d_v <= 0 or not np.isfinite(v_d_v):
        return float("nan")
    return q_inj_max_mc_cm2 / v_d_v   # mC/cm² / V = mF/cm²


# ---------------------------------------------------------------------------
# NOTE: a pulse-derived "effective capacitance" (C_eff = I / |dV/dt| over the
# central plateau) was REMOVED.  Per the literature — Harris 2024 (J. Neural
# Eng. 21:013003, "Limitations in the electrochemical analysis of voltage
# transients"), Harris et al. 2019 (Front. Neurosci. 13:380), and the lab's
# own Nguyen et al. VT paper — a galvanostatic voltage transient cannot
# separate the ohmic, capacitive, and Faradaic contributions: the total
# current is i = i_c + i_f, so the ramp slope reflects BOTH and I/(dV/dt) is
# not a meaningful double-layer capacitance.  The defensible pulse metrics are
# the VOLTAGES (access, polarization, total) and the derived max(Q_inj); a
# real capacitance/charge-storage number requires CV (CSC) or EIS (CPE),
# which this instrument does not run.  Don't reintroduce a pulse C_eff.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Response classification → effective capacitance (C_eff)
# ---------------------------------------------------------------------------
# Operator: "show capacitance since it is so linear … but only when the
# response is entirely capacitive, open circuit, or broken.  Do not include
# access voltage or resistance [or electrode polarization]."
#
# A NORMAL electrode response = an instantaneous IR step (access resistance)
# at the current edge + a charging ramp + Faradaic polarization.  For that
# mixed response a galvanostatic VT cannot separate capacitive from Faradaic
# current (Harris 2024), so C_eff = I/(dV/dt) is NOT a valid capacitance —
# we report access V/R + E_pol instead.
#
# Two cases DO have a well-defined C_eff (no separable resistive+Faradaic
# part); for them we report C_eff and SUPPRESS access V/R + E_pol:
#   * "capacitive" — a clean LINEAR V_mon ramp with a NEGLIGIBLE leading IR
#     step (a near-ideal capacitor under constant current: V = I·t/C).
#   * "open" — V_mon rails toward the compliance voltage at an effective
#     impedance far above any functional electrode (open / broken /
#     disconnected; the current source can't drive it).
_CEFF_OPEN_RAIL_FRAC = 0.85       # |V_mon|max ≥ this × compliance → railed
_CEFF_OPEN_Z_MOHM = 0.05          # …AND effective |V|/|I| ≥ this (50 kΩ) → open
_CEFF_BROKEN_Z_MOHM = 0.2         # effective |V|/|I| ≥ this (200 kΩ), not railed → broken
# SHAPE-AGNOSTIC broken/open detection — bumps CH04/07/14/15/16 (SPEEDBUMPS)
# flung to E_pol ±5-12 V at 51 µA yet read "normal" because the rectangular
# gate below declines to classify any shaped pulse.  A broken/open electrode
# drives an EXTREME voltage EXCURSION per unit current REGARDLESS of pulse
# shape (curvature doesn't matter — magnitude does).  ``exc_z = |V_mon
# excursion| / I_stim`` (MΩ) is the normalized impedance (dividing by current
# folds out the amplitude, so the same threshold works at any ramp step).
#
# THRESHOLD = 0.30 MΩ (was 0.05).  Ground truth = the exp_vt_max archive
# (rectangular, 16 SIROF channels, first capture at 5 µA):
#   * GOOD channels: exc_z = 0.002-0.009 MΩ (2-9 kΩ), V_mon peak 13-43 mV.
#   * BAD channels (CH04/07/14/15): exc_z = 0.45-1.00 MΩ, V_mon peak 2.3-5.0 V.
# A ~50× gap.  0.05 MΩ "cleanly separated" those LARGE low-impedance SIROF —
# but it FALSE-POSITIVES a functional small MICROELECTRODE (operator: a
# gaussian that "is still quitting after designated 'broken' despite there
# being enough current to be reduced to sit within the potential limits").
# A small electrode legitimately has 50-150 kΩ of access resistance, and near
# its water window at LOW absolute current the polarization inflates the
# apparent exc_z further (a functional electrode reaching E_pol ≈ 0.8 V at
# 8 µA reads ~0.1-0.2 MΩ) — WELL above the old 0.05 gate, so it was wrongly
# flagged broken + the ramp quit (compute_metrics clears E_pol for a bad
# class → limit_hit never trips).  0.30 MΩ sits 1.5× below the smallest bench
# BAD (0.45 MΩ) and above any functional electrode's apparent impedance, so
# the truly-open/broken (0.45-1.7 MΩ, incl. the railed test case) are still
# caught while a functional high-Z gaussian stays normal and rides its
# water-window / back-off logic instead of quitting.
#
# GATE lowered 5 → 1 µA so the FIRST low-current capture is the early
# bad-channel indicator (operator: "the shapes at 0 or 1 µA can be an early
# indicator of a bad channel/combo").  At 1 µA the polarization is negligible,
# so exc_z ≈ the pure access resistance — the cleanest possible read: a
# functional electrode (≤ ~150 kΩ → ≤ 0.15 V at 1 µA) stays under the 0.30 MΩ
# gate, while an OPEN/broken one (0.45-1 MΩ → 0.45-1 V at 1 µA) clears both the
# 30 mV signal floor AND the impedance gate and stops the ramp at capture #2
# instead of over-ramping.  A 0 µA baseline delivers no current → V_mon is
# noise → the 30 mV floor (`_vpk >= _CLASSIFY_MIN_SIGNAL_V`) declines it.
_SHAPED_BROKEN_Z_MOHM = 0.30
_SHAPED_BROKEN_MIN_UA = 1.0
_CEFF_CAP_LINEAR_R2 = 0.95        # charging ramp must be linear (operator: "regression very high"; 0.99 → 0.95: bench PBP CH04 — a visibly straight capacitive ramp the operator called OPEN — read R² 0.977 and was wrongly bumped to broken)
_CEFF_CAP_STEP_FRAC = 0.08        # |exc at first 2%-departure| / |peak| ≤ this → no IR step
_CEFF_CAP_RESID_FRAC = 0.05       # normalized RMS fit-residual ≤ this → "no fluctuation in the line" (0.04 → 0.05, same PBP CH04 evidence as the R² relax)
_CEFF_CAP_MIRROR_RATIO = 2.5      # |slope_ph2 / slope_ph1| within [1/x, x] + opposite sign → "near mirrored lines"
# Open (pure-capacitance) vs BROKEN (finite-R R‖C) discriminator — PHASE-2/
# PHASE-1 SLOPE MIRROR (operator: "For checking open, look at the second
# phase.  The voltage should be symmetric to the first phase").  A
# charge-balanced biphasic pulse into a PURE CAPACITANCE (a true open, or a
# capacitive electrode) makes phase 2 the MIRROR of phase 1: equal-and-
# opposite slope.  **The former RETURN-TO-BASELINE requirement was RETIRED**
# (the operator's bench PBP run proved it UNSATISFIABLE for a discharge-delay
# biphasic: the DISCHARGE phase does the reset, so V at phase-2 end sits
# 0.18–0.64 of peak off baseline for EVERY channel — healthy CH01/CH03/CH16
# at 0.25–0.35 included — making the straight-ramp→open path dead code; the
# operator's own later exp_vt_max logs classify the straight-ramp channels
# CH04/CH14/CH15 as OPEN).  Linearity + slope mirror IS the operator's open
# criterion ("look at the linear regression on how straight the edges are");
# a straight ramp whose phase-2 slope is NOT a mirror (or same-signed) stays
# broken.
_CEFF_OPEN_SLOPE_RATIO = 2.0   # |slope_ph2 / slope_ph1| within [1/x, x] → mirror slopes (1.6 → 2.0: PBP CH04's straight open ramp reads 1.61)
# Access-voltage (IR step) gate — the PRIMARY normal-vs-bad discriminator
# Access-voltage (IR step) gate — the PRIMARY normal-vs-bad discriminator
# (operator: "CH03 like CH04 exhibits no access voltage").  A FUNCTIONAL
# electrode's ohmic access resistance makes V_mon JUMP to I·R within the
# first ~0.5 µs of the current edge, THEN ramp (polarisation).  A
# disconnected / pure-capacitor / broken response has NO ohmic step — it
# starts from ~0 and ramps.
_ACCESS_T_US = 0.5                # sample the fast IR step this long after onset
_ACCESS_FRAC_MIN = 0.20          # |V(IR)| / |peak| ≥ this ⇒ a clear access-R step
_ACCESS_FRAC_LOW = 0.10          # a MODEST but real IR step — enough (with a LOW
                                 # impedance) to call a highly-polarisable
                                 # electrode normal; a pure capacitor / broken
                                 # ramp starts from ~0 (access_frac ≈ 0) and
                                 # stays below this (operator: CH16 = 0.17, GOOD)
_ACCESS_RAMP_FACTOR = 1.2        # |peak| > this × |V(IR)| ⇒ a Faradaic ramp follows the step
#: Minimum V_mon peak (volts) below which the response is UNCLASSIFIABLE — the
#: pulse is comparable to the averaged noise floor (~1-2 mV), so the
#: access-voltage / edge-straightness discriminators can't tell a functional
#: electrode from a broken one (a 3 mV V_mon has no resolvable IR step
#: regardless of the electrode).  Below this, ``classify_response_and_ceff``
#: returns "normal" (declines to classify) so a VT ramp STARTING near 0 µA
#: isn't wrongly flagged broken + stopped on its tiny first capture (operator:
#: "testing maximum VT starting at 0 µA, but it mistakenly thought the channel
#: was broken" — V_mon was 2.7-3.5 mV at the 1 µA floor).  A genuinely OPEN
#: electrode rails HIGH (large V_mon) so it's still caught; a real defect is
#: classified once the ramp reaches an amplitude with enough signal.  30 mV ⇒ a
#: normal electrode's ≥20 %-of-peak IR step is ≥6 mV — several × the noise.
_CLASSIFY_MIN_SIGNAL_V = 0.030

#: OPEN-vs-BROKEN flatness threshold (operator + Harris 2019 chronopotentiometry,
#: Front. Neurosci. 13:380).  Under constant current a CAPACITIVE charge keeps
#: the potential RAMPING (dE/dt ≠ 0) while a disconnected / resistive (OPEN)
#: electrode PLATEAUS (dE/dt → 0 — the potential settles to I·R and stops
#: changing).  Measured as the fractional potential change across the LATE half
#: of the phase, ``|V(0.85·W) − V(0.5·W)| / |V_peak|``: below this ⇒ flat
#: (plateaued) ⇒ OPEN, above ⇒ still ramping ⇒ BROKEN.  Ground truth = the
#: exp_vt_max_check 16-electrode array: the OPEN family (CH04/06/08/11/15) sits
#: at 0.056–0.075, the BROKEN family (CH01/02/03/05/09/10/12-14/16) at
#: 0.126–0.327 — a clean gap, threshold in the middle.
_FLAT_TAIL_FRAC = 0.10


def classify_response_and_ceff(time_us, v_mon, pat, *, onset_us, driving_v,
                               compliance_v, open_z_mohm=None,
                               broken_z_mohm=None, min_current_ua=0.0):
    """Classify a capture's response and, for the capacitive / open / broken
    cases, the effective capacitance ``C_eff = |I| / |dV/dt|`` in nF.

    Returns ``(response_class, c_eff_nf)`` — class is ``"normal"`` /
    ``"capacitive"`` / ``"open"`` / ``"broken"``; ``c_eff_nf`` is NaN for
    normal and finite for the other three (where it doubles as a flag — a
    broken/open electrode reads sub-nF vs the tens of nF of a real
    capacitive electrode).

    Discriminators (onset-tolerant, measured off the cathodic excursion of
    ``v_mon``).  The PRIMARY split is the ACCESS VOLTAGE — the ohmic IR step
    a functional electrode's series access resistance makes at the current
    edge (operator: "CH03 like CH04 exhibits no access voltage"):
      * **normal** — a real IR step: V_mon jumps to ≥ ``_ACCESS_FRAC_MIN`` of
        the peak within ``_ACCESS_T_US`` of the edge and a polarisation ramp
        then exceeds it (OR a flat LOW-impedance resistive drop near
        compliance).  This is the ONLY functional class — access V/R + E_pol
        are meaningful; ``C_eff`` = NaN.
      * The no-access cases (V_mon starts from ~0 and ramps — no IR step)
        split by how STRAIGHT the ramp is (operator: "look at the linear
        regression on how straight the edges are"):
          * **open** — a dead-straight linear ramp climbing to a high voltage
            (high effective impedance / reaches compliance), OR a flat jump
            at a high impedance (source railing an open circuit).
          * **capacitive** — a straight ramp that MIRRORS in phase 2 at a LOW
            electrode impedance (a functional pure capacitor).
          * **broken** — a CURVED / transient ramp (not straight) with no
            access step: a degraded / partially-connected electrode.
        ``C_eff = |I|/|dV/dt|`` (nF) is reported ONLY for the STRAIGHT-ramp
        classes (open, capacitive), where ``dV/dt`` is constant so the number
        is a real capacitance; it is **NaN for broken** — a curved ramp has no
        single meaningful slope, so C_eff there would be a meaningless average.
    """
    if v_mon is None or getattr(v_mon, "size", 0) < 4 or not pat.phases:
        return "normal", float("nan")
    # CONTINUOUS SINUSOID (KHFAC): the Ghazavi phase-decomposition is the
    # appropriate analysis (``compute_metrics`` uses it for this regime); the
    # PULSED broken/open heuristics below don't apply — and mis-fired "broken"
    # on a low-current (−1 µA) continuous-sinusoid capture (operator).  Decline
    # → "normal" (electrode health is read from the Ghazavi R_access instead).
    if is_continuous_sinusoidal(pat):
        return "normal", float("nan")
    # The capacitive / open / broken heuristics ALL assume a RECTANGULAR
    # current: a sharp current edge is what produces the IR step, a flat
    # current top is what produces the linear capacitor ramp, and peak
    # amplitude is what sets the impedance estimate.  A SHAPED current
    # (gaussian / sinusoidal / ramp / bowtie / …) makes V_mon curved even
    # into a perfectly NORMAL electrode — its smooth onset has no IR step
    # and its body isn't a straight line — so these tests misfire and a
    # healthy electrode reads as "open"/"capacitive" (operator: "testing
    # the gaussian or sinusoidal symmetric waveforms, PULSAR can incorrectly
    # determine them as open").  The linear-ramp model only holds for a
    # rectangular pulse, so for ANY non-rectangular phase we decline to
    # classify and return "normal".  (A genuinely railed shaped pulse is
    # still surfaced by the separate voltage-compliance check.)
    if not all(getattr(ph, "shape", SHAPE_RECTANGULAR) == SHAPE_RECTANGULAR
               for ph in pat.phases):
        # SHAPE-AGNOSTIC broken/open detection for shaped pulses (bumps
        # CH04/07/14/15/16 flung to E_pol ±5-12 V yet read "normal").  We can't
        # run the rectangular curvature heuristics, but the EXCURSION impedance
        # ``|V_mon excursion| / I_stim`` flags a broken/open electrode
        # regardless of shape.  Gated on ``amp ≥ _SHAPED_BROKEN_MIN_UA`` so the
        # low-current rest-potential noise can't false-positive; below that we
        # decline (return "normal") exactly as before.
        _vs = v_mon.astype(float)
        _ts = time_us.astype(float)
        _amp = abs(float(pat.phases[0].amplitude_ua))
        if _amp >= _SHAPED_BROKEN_MIN_UA:
            _pre = _vs[_ts < onset_us]
            _bl = (float(np.median(_pre)) if _pre.size >= 5
                   else float(np.median(_vs[:max(5, _vs.size // 20)])))
            _vpk = float(np.max(np.abs(_vs - _bl)))
            if (_vpk >= _CLASSIFY_MIN_SIGNAL_V
                    and (_vpk / _amp) >= _SHAPED_BROKEN_Z_MOHM):
                # Curved ramp → the parallel-R‖C fit in compute_metrics reports
                # R / C / τ; C_eff here stays NaN (a shaped curve has no single
                # meaningful I/(dV/dt) slope).
                return "broken", float("nan")
        return "normal", float("nan")
    v = v_mon.astype(float)
    t = time_us.astype(float)
    vmax = float(np.max(np.abs(v)))
    # Low-signal guard: a V_mon peak near the averaged noise floor has no
    # resolvable IR step, so the discriminators below would misfire (they read
    # "no access resistance" on a functional electrode and call it broken).
    # Decline to classify — return "normal" — so a ramp starting near 0 µA
    # isn't killed on its tiny first capture.  (An open electrode rails HIGH,
    # so vmax >> this and it's still classified.)
    if vmax < _CLASSIFY_MIN_SIGNAL_V:
        return "normal", float("nan")
    ph0 = pat.phases[0]
    amp_ua = abs(float(ph0.amplitude_ua))
    # MINIMUM-CURRENT gate (operator: CH02/CH08/CH10 mis-flagged "open" at 6 µA).
    # At very low current the early polarisation dwarfs the tiny stimulus, so the
    # effective impedance is INFLATED and a functional electrode reads high-Z /
    # no-access → a false open/broken.  Below ``min_current_ua`` the open/broken
    # classification is unreliable, so DECLINE (return "normal") — the ramp keeps
    # increasing the current until the classification is trustworthy (or the
    # potential limit / compliance stops it).  Default 0 ⇒ unchanged for every
    # caller that doesn't pass a threshold (POLARIS, exports); the VT runner
    # passes the RampPolicy value.
    if 0.0 < amp_ua < float(min_current_ua):
        return "normal", float("nan")
    _open_z = float(open_z_mohm) if open_z_mohm else _CEFF_OPEN_Z_MOHM
    _broken_z = float(broken_z_mohm) if broken_z_mohm else _CEFF_BROKEN_Z_MOHM
    eff_z_mohm = (vmax / amp_ua) if amp_ua > 0 else float("inf")   # V/µA = MΩ
    # Pre-pulse baseline, then the cathodic excursion off it.
    pre = v[t < onset_us]
    baseline = (float(np.median(pre)) if pre.size >= 5
                else float(np.median(v[:max(5, v.size // 20)])))
    exc = v - baseline
    ipeak = int(np.argmax(np.abs(exc)))
    vpeak = float(exc[ipeak])
    if abs(vpeak) < 1e-4:
        return "normal", float("nan")   # no excursion → nothing to classify
    # ---- PRIMARY discriminator: ACCESS VOLTAGE (the ohmic IR step) --------
    # Operator: "CH03 like CH04 exhibits no access voltage."  A FUNCTIONAL
    # electrode has an ohmic access resistance, so V_mon JUMPS to I·R_access
    # within the first ~0.5 µs of the current edge, THEN ramps (Faradaic /
    # capacitive polarisation).  A disconnected / pure-capacitor / broken
    # response has NO ohmic step — it starts from ~0 and ramps.  Measure the
    # fast jump at ``onset + _ACCESS_T_US`` as a fraction of the peak: a big
    # instant jump (≥ _ACCESS_FRAC_MIN of the peak) that a later ramp then
    # EXCEEDS (peak > _ACCESS_RAMP_FACTOR × the step) = ohmic step +
    # polarisation = a real electrode.  On the operator's run CH01/CH02
    # jump to ~0.5 of the peak (real access R); CH03 0.04 / CH04 0.07 start
    # from ~0 and ramp (no access → bad).
    _iir = int(np.argmin(np.abs(t - (onset_us + _ACCESS_T_US))))
    v_ir = abs(float(exc[_iir]))
    access_frac = v_ir / abs(vpeak)
    has_ramp = abs(vpeak) > _ACCESS_RAMP_FACTOR * v_ir   # polarisation past the step
    # EXTREME voltage per unit current = a very high effective impedance — a
    # bad (disconnected / degrading) electrode in its own right (operator:
    # "CH03 is also bad for its extreme voltage" — 11.9 V / 50 µA ≈ 236 kΩ).
    # Flags BAD even when an ohmic step IS present, so it's checked ALONGSIDE
    # the access-voltage gate.  (A functional electrode at high CURRENT can
    # reach a high voltage at LOW Z — e.g. 7.8 V / 1000 µA = 7.8 kΩ — and is
    # NOT extreme, so it stays normal.)
    extreme_z = eff_z_mohm >= _broken_z
    healthy_z = eff_z_mohm < _open_z             # < open threshold — real load
    # NORMAL — a functional electrode.  Either tell suffices (both require a
    # non-extreme impedance + a polarisation ramp):
    #   (a) a clear ohmic IR STEP (access_frac ≥ _ACCESS_FRAC_MIN), OR
    #   (b) a LOW-impedance load (healthy_z).
    # (b) is the fix for a HIGHLY-POLARISABLE electrode: most of its V_mon is
    # Faradaic polarisation (the ramp), not I·R, so the IR fraction can dip
    # BELOW the step threshold on a perfectly good electrode — but the low
    # impedance proves it's still delivering current.  Operator: CH16 is GOOD
    # — 8.6 kΩ with a 72 mV IR step + a clean ramp to 426 mV (access_frac
    # 0.17, just under 0.20).  The truly-bad CH03/04/06/09 are all ≥ 268 kΩ =
    # extreme_z, so this NEVER rescues them (the impedance gap 10 kΩ vs 268 kΩ
    # is unambiguous).  **Don't raise this to gate ONLY on access_frac** — a
    # highly-polarisable good SIROF is then mislabelled broken.
    if has_ramp and not extreme_z and (
            access_frac >= _ACCESS_FRAC_MIN
            or (healthy_z and access_frac >= _ACCESS_FRAC_LOW)):
        return "normal", float("nan")
    if access_frac >= _ACCESS_FRAC_MIN and not extreme_z:
        # A clear ohmic step but NO ramp: a FLAT low-impedance resistive drop
        # (a functional electrode near compliance at high current) is NORMAL;
        # a FLAT higher-impedance jump is the source railing a partially-open
        # circuit → open (the "step" sits near the compliance rail).
        if healthy_z:
            return "normal", float("nan")
        return "open", float("nan")
    # ---- No access voltage → BAD.  FLATNESS discriminator (operator +
    # Harris 2019 chronopotentiometry, Front. Neurosci. 13:380 — the
    # capacitive/Faradaic potential analysis): under constant current a
    # CAPACITIVE charge keeps the potential RAMPING (dE/dt ≠ 0), while a
    # disconnected / resistive (OPEN) electrode PLATEAUS (dE/dt → 0 — the
    # potential settles to I·R and stops changing).  So the open-vs-broken
    # split is FLATNESS, not straightness (the former "straight ramp = open"
    # was BACKWARDS — operator's electrode-array check: the big STRAIGHT
    # high-Z ramp CH01 is BROKEN, the low-V flat-plateau CH04 is OPEN):
    #   * OPEN   = the late-phase potential is FLAT (plateaued) — CH04 family.
    #   * BROKEN = the potential keeps RAMPING through the phase, no clean
    #     access step, degraded / high-Z — CH01 family.
    #   * CAPACITIVE = a functional pure capacitor: a low-Z, clean-linear,
    #     phase-2-slope-mirrored ramp.
    # tail_change = |V(0.85·W) − V(0.5·W)| / |V_peak| (±5%-window averages tame
    # 1 µA noise); < _FLAT_TAIL_FRAC ⇒ plateaued.  A railed (not has_ramp)
    # trace is flat by definition → open.
    W_us = float(getattr(ph0, "width_us", 0.0) or 0.0)
    tail_change = float("inf")
    if W_us > 0 and abs(vpeak) > 1e-12:
        def _tail_v(frac, half=0.05):
            mm = ((t >= onset_us + (frac - half) * W_us)
                  & (t <= onset_us + (frac + half) * W_us))
            return float(np.mean(exc[mm])) if np.any(mm) else float("nan")
        _vm, _vl = _tail_v(0.5), _tail_v(0.85)
        if np.isfinite(_vm) and np.isfinite(_vl):
            tail_change = abs(_vl - _vm) / abs(vpeak)
    is_flat = (not has_ramp) or (np.isfinite(tail_change)
                                 and tail_change < _FLAT_TAIL_FRAC)
    # Straightness + phase-2 slope-mirror — now used ONLY to pick out a
    # functional pure CAPACITOR among the RAMPING (non-flat) responses.
    slope_v_per_us = float("nan"); r2 = 0.0; resid_norm = float("inf")
    rises = np.nonzero(np.abs(exc) >= 0.02 * abs(vpeak))[0]
    if rises.size and rises[0] < ipeak and (ipeak - rises[0]) >= 4:
        i0 = int(rises[0]); tt = t[i0:ipeak + 1]; vv = v[i0:ipeak + 1]
        try:
            coef = np.polyfit(tt, vv, 1)
            slope_v_per_us = float(coef[0])
            fit = np.polyval(coef, tt)
            ss_res = float(np.sum((vv - fit) ** 2))
            ss_tot = float(np.sum((vv - vv.mean()) ** 2))
            r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
            resid_norm = (float(np.sqrt(ss_res / len(vv))) / abs(vpeak)
                          if len(vv) and abs(vpeak) > 1e-12 else float("inf"))
        except Exception:
            pass
    is_straight = (has_ramp and np.isfinite(r2) and r2 >= _CEFF_CAP_LINEAR_R2
                   and resid_norm <= _CEFF_CAP_RESID_FRAC
                   and np.isfinite(slope_v_per_us) and abs(slope_v_per_us) > 1e-12)
    mirrored = False
    if is_straight and not is_flat and len(pat.phases) >= 2:
        try:
            _pw = phase_windows(t, pat, onset_us=onset_us)
            if len(_pw) >= 2 and (_pw[1].end_idx - _pw[1].start_idx) >= 4:
                s2 = float(np.polyfit(t[_pw[1].start_idx:_pw[1].end_idx],
                                      v[_pw[1].start_idx:_pw[1].end_idx], 1)[0])
                if s2 * slope_v_per_us < 0:      # opposite sign (mirror slopes)
                    ratio = abs(s2) / abs(slope_v_per_us)
                    mirrored = (1.0 / _CEFF_CAP_MIRROR_RATIO <= ratio
                                <= _CEFF_CAP_MIRROR_RATIO)
        except Exception:
            pass
    if is_flat:
        cls = "open"          # plateaued → not charging → disconnected/resistive
    elif is_straight and mirrored and eff_z_mohm < _open_z:
        cls = "capacitive"    # functional low-Z pure capacitor
    else:
        cls = "broken"        # keeps ramping, degraded / high-Z
    c_eff_nf = float("nan")
    # C = I/(dV/dt) is meaningful ONLY for the CAPACITIVE straight ramp (constant
    # dV/dt).  A flat OPEN plateau has dV/dt→0 (not a capacitance; compute_metrics
    # fits its R‖C instead) and a BROKEN ramp has no single slope → NaN for both.
    if cls == "capacitive" and abs(slope_v_per_us) > 1e-12 and amp_ua > 0:
        c_eff_nf = (amp_ua / abs(slope_v_per_us)) / 1000.0
    return cls, c_eff_nf


def fit_parallel_rc(time_us, v_mon, pat, *, onset_us, amp_ua,
                    phase_idx: int = 0):
    """Fit a BROKEN (exponential) charging response to a parallel R‖C model.

    A disconnected / degrading electrode whose V_mon charges EXPONENTIALLY
    (not a straight ramp, no IR step) is a parallel R‖C driven by the
    constant stimulus current I:  ``V(t) = V∞·(1 − e^(−t/τ))`` with
    ``V∞ = I·R`` and ``τ = R·C``.  So from the fit:  R = V∞/I,  C = τ/R.
    (Operator: "would it be possible to fit the exponential response with
    RC?" — CH03 fits this with R²≈0.998: R≈258 kΩ, C≈0.13 nF, τ≈33 µs; the
    curved ramp that made it "broken" IS a clean exponential.)

    ``phase_idx`` picks WHICH phase's window to fit (operator: "for broken
    and open channels, compute the same metrics for other phases") — the
    onset-anchored cumulative timing chain gives each phase's bounds; the
    baseline for phase k > 0 is the trace level JUST BEFORE that phase
    starts (its charging is relative to where the previous phase left it),
    while phase 0 keeps the pre-pulse baseline.  ``amp_ua`` should be that
    phase's own amplitude.

    Returns ``(r_kohm, c_nf, tau_us, r2)``; all NaN (r2=0) when scipy is
    missing, the window is too short, or the fit fails / degenerates.
    """
    try:
        from scipy.optimize import curve_fit
    except Exception:
        return float("nan"), float("nan"), float("nan"), 0.0
    if (v_mon is None or getattr(v_mon, "size", 0) < 8 or not pat.phases
            or not (0 <= int(phase_idx) < len(pat.phases))):
        return float("nan"), float("nan"), float("nan"), 0.0
    t = np.asarray(time_us, float)
    v = np.asarray(v_mon, float)
    amp_a = abs(float(amp_ua)) * 1e-6
    if amp_a <= 0:
        return float("nan"), float("nan"), float("nan"), 0.0
    _wins = phase_windows(t, pat, onset_us=onset_us)
    _w = _wins[int(phase_idx)]
    ph_start, ph_end = float(_w.start_us), float(_w.end_us)
    mask = (t >= ph_start) & (t <= ph_end)
    if int(np.count_nonzero(mask)) < 8:
        return float("nan"), float("nan"), float("nan"), 0.0
    if int(phase_idx) == 0:
        pre = v[t < onset_us]
        base = float(np.median(pre)) if pre.size >= 5 else 0.0
    else:
        # Level just BEFORE this phase begins (last few samples of the
        # preceding delay / phase) — the phase charges relative to it.
        pre = v[(t < ph_start)]
        base = (float(np.median(pre[-5:])) if pre.size >= 5
                else (float(pre[-1]) if pre.size else 0.0))
    tt = t[mask] - ph_start
    vv = np.abs(v[mask] - base)          # |excursion| off the phase baseline
    v_end = float(vv[-1]) if vv.size else 0.0
    if v_end < 1e-4:
        return float("nan"), float("nan"), float("nan"), 0.0

    def _rc(t_, vinf, tau):
        return vinf * (1.0 - np.exp(-t_ / np.maximum(tau, 1e-6)))

    try:
        popt, _ = curve_fit(_rc, tt, vv, p0=[v_end, 60.0],
                            bounds=([0.0, 1e-3], [np.inf, np.inf]),
                            maxfev=20000)
    except Exception:
        return float("nan"), float("nan"), float("nan"), 0.0
    vinf, tau_us = float(popt[0]), float(popt[1])
    fit = _rc(tt, vinf, tau_us)
    ss_tot = float(np.sum((vv - vv.mean()) ** 2))
    r2 = (1.0 - float(np.sum((vv - fit) ** 2)) / ss_tot) if ss_tot > 0 else 0.0
    if not (np.isfinite(vinf) and np.isfinite(tau_us)
            and vinf > 0 and tau_us > 0):
        return float("nan"), float("nan"), float("nan"), 0.0
    r_ohm = vinf / amp_a                  # R = V∞ / I
    c_f = (tau_us * 1e-6) / r_ohm         # C = τ / R  (τ in seconds)
    return r_ohm / 1e3, c_f * 1e9, tau_us, r2


#: A broken RC fit is accepted only when the exponential model genuinely
#: fits (else the R/C/τ would be as meaningless as the linear slope was).
_RC_FIT_MIN_R2 = 0.95


# ---------------------------------------------------------------------------
# Top-level: compute everything for a Capture
# ---------------------------------------------------------------------------
RESPONSE_CLASSES = ("normal", "broken", "open", "capacitive")


def effective_capacitance_from_ramp(time_us, v_mon, pat, *, onset_us, amp_ua,
                                    phase_idx: int = 0):
    """Linear ``C_eff = |I| / |dV/dt|`` (nF) from a phase's charging-ramp
    slope of V_mon — the open / capacitive capacitance.

    Used when the operator FORCES an open / capacitive class in POLARIS (the
    auto-classifier only returns a C_eff for a straight ramp, so a forced
    override needs its own slope fit), and per phase for the bad-response
    per-phase metric lists (operator: "for broken and open channels, compute
    the same metrics for other phases").  ``phase_idx = 0`` keeps the
    original global-peak-anchored phase-1 behaviour; ``phase_idx > 0`` fits
    the ramp slope over the INNER portion of that phase's onset-anchored
    window (the leading 15 % is skipped to clear the switching transient),
    with ``amp_ua`` = that phase's own amplitude.  Returns NaN if the ramp
    is too short / flat or the amplitude is non-positive."""
    t = np.asarray(time_us, dtype=float)
    v = np.asarray(v_mon, dtype=float)
    if t.size < 8 or v.size != t.size or amp_ua <= 0:
        return float("nan")
    if int(phase_idx) > 0:
        try:
            _w = phase_windows(t, pat, onset_us=onset_us)[int(phase_idx)]
        except Exception:
            return float("nan")
        span = float(_w.end_us) - float(_w.start_us)
        lo = float(_w.start_us) + 0.15 * span     # skip switching transient
        hi = float(_w.end_us) - 0.05 * span
        m = (t >= lo) & (t <= hi)
        if int(np.count_nonzero(m)) < 6:
            return float("nan")
        try:
            slope = float(np.polyfit(t[m], v[m], 1)[0])   # V/µs
        except Exception:
            return float("nan")
        if abs(slope) < 1e-12:
            return float("nan")
        return (abs(float(amp_ua)) / abs(slope)) / 1000.0
    pre = v[t < onset_us]
    base = (float(np.median(pre)) if pre.size >= 5
            else float(np.median(v[:max(5, v.size // 20)])))
    exc = v - base
    ipk = int(np.argmax(np.abs(exc)))
    vpk = float(exc[ipk])
    if abs(vpk) < 1e-4:
        return float("nan")
    rises = np.nonzero(np.abs(exc) >= 0.02 * abs(vpk))[0]
    if not (rises.size and rises[0] < ipk and (ipk - rises[0]) >= 4):
        return float("nan")
    i0 = int(rises[0])
    try:
        slope = float(np.polyfit(t[i0:ipk + 1], v[i0:ipk + 1], 1)[0])  # V/µs
    except Exception:
        return float("nan")
    if abs(slope) < 1e-12:
        return float("nan")
    return (amp_ua / abs(slope)) / 1000.0


# ---------------------------------------------------------------------------
# Harris 2019 chronopotentiometry — capacitive / Faradaic charge-transfer
# decomposition (Front. Neurosci. 13:380, "Using Chronopotentiometry to Better
# Characterize the Charge Injection Mechanisms of Platinum Electrodes").
# ---------------------------------------------------------------------------
# GALVANOSTATIC (constant current) → the potential-time SLOPE tells the split:
#   capacitive current  i_c = A·C_dl·(dE/dt)   (Eq 1) → a CONSTANT dE/dt while
#                                                        the double layer charges
#   Faradaic current    i_f                     → holds the potential (dE/dt DIPS
#                                                  toward 0; a PEAK in 1/(dE/dt))
# PULSAR samples at µs (32 ns) so it resolves the access-R IR step + the early
# capacitive ramp that Harris's ms-resolution commercial potentiostat could
# NOT — a genuine advantage.  HONEST BOUNDARY (operator's own conclusion +
# Harris 2024 "Limitations in the electrochemical analysis of voltage
# transients"): a galvanostatic pulse CANNOT cleanly separate iRu / capacitive
# / Faradaic at every instant, and the reciprocal derivative "was unable to
# distinguish specific Faradaic reactions".  So this is a FIRST-ORDER
# decomposition — C_dl from the pure-capacitive window is defensible; the
# capacitive/Faradaic CHARGE split is explicitly APPROXIMATE.  This is NOT the
# removed full-pulse C_eff (gotcha #58): C_dl uses ONLY the constant-dE/dt
# window AFTER the IR step, not the whole (mixed) ramp.
_CT_IR_SKIP_US = 2.0            #: skip this long after a phase edge (the IR step)
_CT_FARADAIC_DIP_FRAC = 0.5     #: dE/dt < this × the capacitive baseline ⇒ Faradaic
_CT_CAP_WINDOW_FRAC = 0.25      #: capacitive-baseline dE/dt = median over the first
                               #: this fraction of the post-IR-step window


@dataclass
class ChargeTransferAnalysis:
    """Harris 2019 capacitive/Faradaic decomposition of ONE pulse phase.

    ``c_dl_mf_per_cm2`` (double-layer capacitance C_dl) and
    ``dedt_capacitive_v_per_s`` are defensible; the charge split
    (``q_capacitive_nc`` / ``q_faradaic_nc`` / ``faradaic_fraction``) is
    FIRST-ORDER / APPROXIMATE (see the module note).  ``faradaic_onset_us`` /
    ``_v`` is where dE/dt first dips below the capacitive baseline (NaN = no
    resolvable Faradaic onset — a ~purely capacitive phase)."""
    phase_idx: int = 0
    c_dl_mf_per_cm2: float = float("nan")
    dedt_capacitive_v_per_s: float = float("nan")
    faradaic_onset_us: float = float("nan")
    faradaic_onset_v: float = float("nan")
    q_total_nc: float = float("nan")
    q_capacitive_nc: float = float("nan")
    q_faradaic_nc: float = float("nan")
    faradaic_fraction: float = float("nan")


def active_potential_trace(capture):
    """Return ``(E_array, kind)`` — the active-electrode POTENTIAL for
    chronopotentiometry / RDC analysis.

    Same active-trace selection the metrics + plot markers use (gotcha #87):
      * ``e_act`` — E_act recorded directly (potential vs the reference);
      * ``e_act_derived`` — E_act = V_mon + E_ret (the differential identity)
        when only E_ret was digitised;
      * ``v_mon`` — V_mon as a proxy (active vs return) when no electrode
        potential is available — the common V_mon/I_mon-only case.
    The ``kind`` drives the plot's x-axis label (vs reference vs monitor)."""
    t = getattr(capture, "time_us", None)
    n = int(getattr(t, "size", 0)) if t is not None else 0
    e_act = getattr(capture, "e_act_v", None)
    e_ret = getattr(capture, "e_ret_v", None)
    v_mon = getattr(capture, "v_mon_v", None)
    if e_act is not None and int(getattr(e_act, "size", 0)) == n and n:
        return np.asarray(e_act, dtype=float), "e_act"
    if (e_ret is not None and int(getattr(e_ret, "size", 0)) == n and n
            and v_mon is not None and int(getattr(v_mon, "size", 0)) == n):
        return (np.asarray(v_mon, dtype=float)
                + np.asarray(e_ret, dtype=float)), "e_act_derived"
    if v_mon is not None and int(getattr(v_mon, "size", 0)) == n and n:
        return np.asarray(v_mon, dtype=float), "v_mon"
    return np.zeros(0, dtype=float), "v_mon"


@dataclass
class RDCSegment:
    """One phase's Reciprocal Derivative Chronopotentiometry curve (Musa et
    al. 2010, IEEE EMBS): ``dt/dE`` plotted vs the electrode potential ``E``.

    * ``potential_v`` — E (V), the x-axis (time-ordered over the phase body).
    * ``dtde_ms_per_v`` — dt/dE (ms/V), the y-axis (= 1/(dE/dt); SIGNED, so a
      cathodic phase sits below zero and an anodic phase above, matching the
      paper's stacked anodic/cathodic curves).
    A Faradaic reaction holds the potential (dE/dt → min) → a PEAK in |dt/dE|;
    capacitive charging (steep dE/dt) → a FLAT low-|dt/dE| region.  The
    leading iR-step + trailing current-reversal transients are excluded."""
    phase_idx: int
    polarity: str                      # 'cathodic' | 'anodic'
    potential_v: np.ndarray
    dtde_ms_per_v: np.ndarray


def reciprocal_derivative_curve(capture, *, onset_us: Optional[float] = None,
                                smooth_us: float = 4.0):
    """Reciprocal Derivative Chronopotentiometry (Musa et al. 2010).

    Returns ``(segments, trace_kind)`` — one :class:`RDCSegment` per
    non-zero-amplitude phase and the potential-trace kind (for the axis
    label, from :func:`active_potential_trace`).  ``dt/dE`` (ms/V) is computed
    from the smoothed potential derivative (:func:`charge_transfer_dedt`),
    restricted to each phase BODY (the leading iR step + trailing reversal
    transient excluded — the paper drops the iR data for clarity), and plotted
    against E.  Near-zero |dE/dt| (a genuine turning point) is dropped to NaN
    so the reciprocal doesn't blow up to ±∞; real Faradaic peaks (small but
    finite dE/dt) survive.  Empty list when the capture is unusable."""
    segs: list = []
    t = np.asarray(getattr(capture, "time_us", np.zeros(0)), dtype=float)
    v, kind = active_potential_trace(capture)
    pat = getattr(capture, "pattern", None)
    if t.size < 8 or v.size != t.size or pat is None or not pat.phases:
        return segs, kind
    if onset_us is None:
        try:
            onset_us = pulse_onset_us(t, capture.i_mon_ua, v)
        except Exception:
            onset_us = 0.0
    _, dedt = charge_transfer_dedt(t, v, pat, onset_us=onset_us)
    try:
        windows = phase_windows(t, pat, onset_us=onset_us)
    except Exception:
        return segs, kind
    for i, ph in enumerate(pat.phases):
        amp = float(ph.amplitude_ua)
        if abs(amp) <= 0.0 or i >= len(windows):
            continue
        t0, t1 = float(windows[i].start_us), float(windows[i].end_us)
        span = t1 - t0
        if span <= 4.0:
            continue
        ir = max(_CT_IR_SKIP_US, 0.02 * span)          # skip the IR step
        body = (t >= t0 + ir) & (t <= t1 - 0.02 * span)
        if int(np.count_nonzero(body)) < 6:
            continue
        e_b = v[body]
        d_b = dedt[body]                               # V/µs
        # dt/dE in ms/V = 1/(dE/dt[V/µs]) × 1e-3.  NaN where dE/dt ≈ 0 (a
        # turning point → reciprocal → ±∞); finite Faradaic peaks survive.
        with np.errstate(divide="ignore", invalid="ignore"):
            dtde = np.where(np.abs(d_b) < 1e-9, np.nan, 1e-3 / d_b)
        polarity = "cathodic" if amp < 0 else "anodic"
        segs.append(RDCSegment(phase_idx=i, polarity=polarity,
                               potential_v=e_b, dtde_ms_per_v=dtde))
    return segs, kind


def charge_transfer_dedt(time_us, v_active, pattern, *, onset_us,
                         smooth_us: float = 4.0):
    """Return ``(t_us, dedt_v_per_us)`` — the smoothed potential derivative over
    the whole capture, for the dE/dt / reciprocal-derivative plot.  Light
    moving-average smoothing (``smooth_us``) before differentiating tames the
    per-sample noise that ``np.gradient`` would otherwise amplify."""
    t = np.asarray(time_us, dtype=float)
    v = np.asarray(v_active, dtype=float)
    if t.size < 8 or v.size != t.size:
        return t, np.zeros_like(t)
    dt = float(np.median(np.diff(t))) if t.size > 1 else 1.0
    k = max(3, int(round(smooth_us / dt)) | 1)          # odd window
    vs = np.convolve(v, np.ones(k) / k, mode="same")
    return t, np.gradient(vs, t)


def chronopotentiometry_charge_transfer(time_us, v_active, pattern, *, onset_us,
                                        area_um2, phase_idx: int = 0
                                        ) -> ChargeTransferAnalysis:
    """Capacitive/Faradaic decomposition of phase ``phase_idx`` (Harris 2019).

    ``v_active`` is the active-electrode potential (E_act when recorded, else
    V_mon — same convention as the other bad-response fits).  Constant phase
    current ``I`` from the pattern; ``area_um2`` for the areal C_dl.  Returns a
    :class:`ChargeTransferAnalysis` (all-NaN when the phase is unusable:
    zero amplitude, no area, or too few samples)."""
    out = ChargeTransferAnalysis(phase_idx=int(phase_idx))
    t = np.asarray(time_us, dtype=float)
    v = np.asarray(v_active, dtype=float)
    if (t.size < 8 or v.size != t.size or not pattern.phases
            or int(phase_idx) >= len(pattern.phases)):
        return out
    ph = pattern.phases[int(phase_idx)]
    I_ua = abs(float(ph.amplitude_ua))
    if I_ua <= 0 or not (area_um2 and area_um2 > 0):
        return out
    try:
        w = phase_windows(t, pattern, onset_us=onset_us)[int(phase_idx)]
    except Exception:
        return out
    t0, t1 = float(w.start_us), float(w.end_us)
    span = t1 - t0
    if span <= 4.0:
        return out
    # Smoothed dE/dt (V/µs) over the whole trace, then restrict to the phase.
    _, dedt = charge_transfer_dedt(t, v, pattern, onset_us=onset_us)
    ir_end = t0 + max(_CT_IR_SKIP_US, 0.02 * span)      # skip the IR-step transient
    body = (t >= ir_end) & (t <= t1 - 0.02 * span)
    if int(np.count_nonzero(body)) < 8:
        return out
    tb, db, vb = t[body], dedt[body], v[body]
    # Capacitive baseline dE/dt = median |dE/dt| over the FIRST part of the
    # post-IR window (purely capacitive before Faradaic reactions divert current).
    early = tb <= (ir_end + _CT_CAP_WINDOW_FRAC * (t1 - ir_end))
    dedt_cap_us = float(np.median(np.abs(db[early]))) if np.any(early) else float("nan")
    if not np.isfinite(dedt_cap_us) or dedt_cap_us < 1e-9:
        return out
    out.dedt_capacitive_v_per_s = dedt_cap_us * 1e6
    # C_dl = I / (A · dE/dt) → F/cm², reported as mF/cm².
    I_a = I_ua * 1e-6
    A_cm2 = float(area_um2) * 1e-8
    c_dl_f_per_cm2 = I_a / (A_cm2 * out.dedt_capacitive_v_per_s)
    out.c_dl_mf_per_cm2 = c_dl_f_per_cm2 * 1e3
    # Faradaic onset: first SUSTAINED (≥ 3 µs) dip below the capacitive baseline.
    dt = float(np.median(np.diff(tb))) if tb.size > 1 else 1.0
    need = max(1, int(round(3.0 / dt)))
    dip = np.abs(db) < (_CT_FARADAIC_DIP_FRAC * dedt_cap_us)
    onset_i = -1
    run = 0
    for i, d in enumerate(dip):
        run = run + 1 if d else 0
        if run >= need:
            onset_i = i - need + 1
            break
    if onset_i >= 0:
        out.faradaic_onset_us = float(tb[onset_i])
        out.faradaic_onset_v = float(vb[onset_i])
    # Charge split (APPROXIMATE): Q_total = |I|·W; Q_cap = A·C_dl·ΔE_pol, where
    # ΔE_pol is the polarization swing across the phase body (after the IR step).
    out.q_total_nc = I_ua * span / 1000.0               # µA·µs = pC → nC
    v_ir = float(np.median(v[(t >= ir_end) & (t <= ir_end + max(1.0, 0.02 * span))]))
    v_end = float(np.median(v[(t >= t1 - max(1.0, 0.02 * span)) & (t <= t1)]))
    dE_pol = abs(v_end - v_ir)
    q_cap_c = A_cm2 * c_dl_f_per_cm2 * dE_pol            # F·V = C
    out.q_capacitive_nc = min(q_cap_c * 1e9, out.q_total_nc)
    out.q_faradaic_nc = max(0.0, out.q_total_nc - out.q_capacitive_nc)
    out.faradaic_fraction = (out.q_faradaic_nc / out.q_total_nc
                             if out.q_total_nc > 0 else float("nan"))
    return out


# ---------------------------------------------------------------------------
# Ghazavi & Cogan 2018 — electrode polarization under continuous sinusoidal
# (KHFAC) stimulation
# ---------------------------------------------------------------------------
def is_continuous_sinusoidal(pattern) -> bool:
    """True for a CONTINUOUS symmetric-biphasic SINUSOIDAL waveform — the
    KHFAC regime the Ghazavi & Cogan (2018, *J. Neural Eng.* 15 036023) method
    applies to.

    Requires (operator: "biphasic symmetric sinusoidal … with no interphase,
    discharge, and interpulse delays"):
      * exactly two phases, BOTH sinusoidal;
      * symmetric — equal |amplitude| and equal width, opposite polarity;
      * no interphase delay (phase-1 ``delay_after_us`` ≈ 0), no discharge
        delay (phase-2 ``delay_after_us`` ≈ 0);
      * no idle interpulse gap (``not has_interpulse_gap()`` — the period is
        fully occupied by the pulse, so the waveform is a continuous sinusoid).

    In this regime there is no current-step edge for the pulsed access-voltage
    extrapolation, so polarization is obtained from the phase decomposition
    instead (:func:`ghazavi_polarization`).
    """
    try:
        phs = pattern.phases
        if len(phs) != 2:
            return False
        if any(p.shape != SHAPE_SINUSOIDAL for p in phs):
            return False
        a0, a1 = phs[0].amplitude_ua, phs[1].amplitude_ua
        if not (abs(abs(a0) - abs(a1)) <= 1e-6 + 1e-3 * max(abs(a0), abs(a1))):
            return False                      # unequal magnitude → asymmetric
        # OPPOSITE polarity — via the SIGNED sign (``copysign``), NOT ``a0*a1``.
        # A 0 µA-start KHFAC template (VT-max starting at 0 µA) carries SIGNED
        # ZEROS ``-0.0`` / ``+0.0`` (the GUI emits ``polarity * abs(0)``, gotcha
        # #56); ``a0*a1 = 0 >= 0`` wrongly rejected it as "same sign", so the
        # BASE pattern read as non-sinusoidal and the KHFAC path (Ghazavi
        # metrics, corrected E′act, the classifier gate) was skipped for the
        # 0 µA baseline.  ``copysign`` recovers ``-0.0`` → −1 / ``+0.0`` → +1,
        # so opposite signed zeros count as biphasic (same convention
        # ``_pattern_at_amplitude`` uses to grow the ramp).
        import math
        if math.copysign(1.0, a0) == math.copysign(1.0, a1):
            return False                      # same polarity → not biphasic
        if abs(phs[0].width_us - phs[1].width_us) > 1e-6:
            return False                      # unequal widths → asymmetric
        if abs(phs[0].delay_after_us) > 1e-6:
            return False                      # interphase delay → not continuous
        # The TRAILING (phase-2) discharge delay is allowed ONLY when it's
        # rendered as an auto-discharge SHORT (``interpulse_discharge_us`` > 0):
        # the sinusoid is still continuous, the brief inter-cycle short is
        # windowed out of the Ghazavi E_off, so the KHFAC / DC analysis keeps
        # applying (operator: "replace a 0-µA step … keep the analysis").  A
        # plain floating discharge step still disqualifies (not continuous).
        _ipd = float(getattr(pattern, "interpulse_discharge_us", 0.0) or 0.0)
        if abs(phs[1].delay_after_us) > 1e-6 and _ipd <= 0:
            return False                      # floating discharge → not continuous
        if pattern.has_interpulse_gap():
            return False                      # idle interpulse → not continuous
    except Exception:
        return False
    return True


def ghazavi_polarization(v_m: np.ndarray, i_mon_ua: np.ndarray,
                         time_us: Optional[np.ndarray] = None,
                         *, rate_hz: Optional[float] = None,
                         robust: bool = False):
    """Electrode polarization from a continuous sinusoidal voltage transient —
    port of Ghazavi & Cogan (2018) §2.3.

    Their model (their eqs 1-8): the cell is an access resistance ``R_access``
    in series with the double-layer capacitance, so the measured electrode
    voltage decomposes into a RESISTIVE part in phase with the current,
    ``V_r(t) = R_access·I(t)``, and the INTERFACE potential ``E_i(t)``.  Under
    the ideal-capacitor assumption (their δ = −π/2, valid where double-layer
    charging dominates) the decomposition is EXACTLY the least-squares
    projection of ``V_m`` onto ``I``:

        R_access = Σ(V_ac·I_ac) / Σ(I_ac²)          (their V_ro/I₀)
        E_i(t)   = V_m(t) − R_access·I(t)           (interface, DC kept)
        E_off    = mean(E_i) = mean(V_m)            (their offset potential)
        E_mc     = min(E_i) = E_off − E_io          (their eq: most cathodic)
        E_ma     = max(E_i) = E_off + E_io          (most anodic)

    (Proof of the equivalence: for ``V_m = V_ro·sin(ωt) − E_io·cos(ωt)`` and
    ``I = I₀·sin(ωt)``, ``Σ(V_ac·I_ac)/Σ(I_ac²) = V_ro/I₀`` because the
    quadrature ``cos`` term averages out, and ``V_m − (V_ro/I₀)·I =
    −E_io·cos(ωt)`` whose extrema are ``±E_io`` — i.e. this is their eqs 6-8
    with δ = −π/2, computed per-sample without an FFT/phase fit.)

    Then ``E_mc`` / ``E_ma`` are compared to the water-window limits, exactly
    as for a pulsed waveform — the KHFAC "max charge injection capacity"
    question.

    Parameters
    ----------
    v_m : the measured electrode voltage trace (volts) — E_act vs reference
        when recorded, else V_mon as the proxy (same convention as the pulsed
        metrics).
    i_mon_ua : the applied current trace (µA).
    time_us : optional time axis (µs) — used only to estimate the fundamental
        frequency for the reported ``freq_khz``.
    rate_hz : optional pattern rate (Hz) — the fallback frequency when it can't
        be measured from the trace.

    Returns
    -------
    dict with ``e_mc_v, e_ma_v, e_io_v, e_off_v, r_access_kohm, freq_khz`` —
    all NaN when the traces are too short / carry no current.
    """
    nan = float("nan")
    out = dict(e_mc_v=nan, e_ma_v=nan, e_io_v=nan, e_off_v=nan,
               r_access_kohm=nan, v_access_v=nan, freq_khz=nan)
    try:
        v = np.asarray(v_m, dtype=float)
        i = np.asarray(i_mon_ua, dtype=float)
    except Exception:
        return out
    n = min(v.size, i.size)
    if n < 16:
        return out
    v = v[:n]
    i = i[:n]
    good = np.isfinite(v) & np.isfinite(i)
    if np.count_nonzero(good) < 16:
        return out
    v = v[good]
    i = i[good]
    i_ac = i - float(np.mean(i))
    v_ac = v - float(np.mean(v))
    denom = float(np.sum(i_ac * i_ac))
    if denom <= 1e-9:                         # no current → nothing to project
        return out
    # Resistive (in-phase) projection.  I in µA, V in volts → R in V/µA;
    # ×1e3 → kΩ  (V/µA = 1e6 Ω = 1e3 kΩ).
    r_v_per_ua = float(np.sum(v_ac * i_ac) / denom)
    e_i = v - r_v_per_ua * i_ac               # interface potential, DC retained
    # WINDOW OUT the 1 µs interpulse-discharge transient (operator: "keep KHFAC
    # analysis, window it out").  A real auto-discharge short each cycle drives
    # E_i briefly toward equilibrium + can throw a switching spike — a rare
    # (~0.5 % of samples) OUTLIER vs the sinusoid, which is dense near its
    # peaks.  MAD-clip rejects it, so E_off (mean) isn't biased and E_mc/E_ma
    # (min/max) aren't set by a switching spike — the sinusoid peaks (many
    # samples) survive the clip untouched.  Gated: only when ``robust`` (a
    # discharge is present), so a plain continuous sinusoid is byte-unchanged.
    e_stat = e_i
    if robust and e_i.size >= 16:
        med = float(np.median(e_i))
        mad = float(np.median(np.abs(e_i - med)))
        if mad > 0:
            keep = np.abs(e_i - med) <= 6.0 * 1.4826 * mad
            if np.count_nonzero(keep) >= 16:
                e_stat = e_i[keep]
    out["e_mc_v"] = float(np.min(e_stat))
    out["e_ma_v"] = float(np.max(e_stat))
    out["e_off_v"] = float(np.mean(e_stat))
    out["e_io_v"] = 0.5 * (out["e_ma_v"] - out["e_mc_v"])
    out["r_access_kohm"] = abs(r_v_per_ua) * 1e3
    # ACCESS VOLTAGE amplitude V_ro = R_access · I_o (Ghazavi eq 4, = their
    # in-phase V_mo·cos φ).  I_o = the AC current amplitude = √2 · RMS(i_ac)
    # for a sinusoid (robust vs a peak read).  ``r_v_per_ua`` is V/µA and
    # i_ac is µA, so the product is volts.
    i_amp_ua = float(np.sqrt(2.0) * np.std(i_ac))
    out["v_access_v"] = abs(r_v_per_ua) * i_amp_ua
    # Fundamental frequency — measured from the current trace (zero-crossing
    # rate) when a time axis is available, else the pattern rate.
    f_hz = nan
    if time_us is not None:
        try:
            t = np.asarray(time_us, dtype=float)[good]
            span_s = (float(t[-1]) - float(t[0])) * 1e-6
            if span_s > 0:
                # Count rising zero-crossings of the AC current.
                sgn = np.sign(i_ac)
                sgn[sgn == 0] = 1
                rising = np.count_nonzero((sgn[:-1] < 0) & (sgn[1:] >= 0))
                if rising >= 1:
                    f_hz = rising / span_s
        except Exception:
            f_hz = nan
    if not np.isfinite(f_hz) and rate_hz:
        f_hz = float(rate_hz)
    out["freq_khz"] = f_hz * 1e-3 if np.isfinite(f_hz) else nan
    return out


def ghazavi_corrected_waveforms(v_m: np.ndarray, i_mon_ua: np.ndarray):
    """Ghazavi & Cogan 2018 §2.3 ACCESS-RESISTANCE CORRECTION of a measured
    electrode voltage — the "corrected waveform" the operator wants plotted
    with a prime (``E′act`` from the active trace, ``E′ret`` from ``E_ret``).

    Split the measured voltage ``V_m(t)`` into the RESISTIVE (in-phase-with-I)
    part and the INTERFACE potential (their Fig 2):

        R_access = Σ(V_ac·I_ac) / Σ(I_ac²)        (least-squares projection,
                                                   identical to the R in
                                                   :func:`ghazavi_polarization`)
        V_r(t)   = R_access · I_ac(t)             (resistive drop)
        E′(t)    = V_m(t) − V_r(t)                (interface — CORRECTED,
                                                   access resistance removed;
                                                   DC/offset retained)

    where ``I_ac = I − mean(I)``.  ``E′`` is the double-layer/Faradaic interface
    potential the water-window limit really cares about — the iR drop across the
    access resistance has been subtracted out.

    Returns ``(e_i, v_r, r_v_per_ua)``:
      * ``e_i`` / ``v_r`` — float arrays the SAME length as ``v_m`` (aligned to
        the capture time axis; non-finite input samples propagate as NaN so a
        plot shows gaps rather than garbage);
      * ``r_v_per_ua`` — the access resistance in V/µA.
    Returns ``(None, None, nan)`` when the traces are too short (< 16 finite
    paired samples) or carry no AC current.  Used ONLY for continuous-
    sinusoidal (KHFAC) captures; see :func:`is_continuous_sinusoidal`.
    """
    nan = float("nan")
    try:
        v = np.asarray(v_m, dtype=float)
        i = np.asarray(i_mon_ua, dtype=float)
    except Exception:
        return None, None, nan
    n = min(v.size, i.size)
    if n < 16:
        return None, None, nan
    v = v[:n]
    i = i[:n]
    good = np.isfinite(v) & np.isfinite(i)
    if np.count_nonzero(good) < 16:
        return None, None, nan
    i_mean = float(np.mean(i[good]))
    v_mean = float(np.mean(v[good]))
    i_ac_good = i[good] - i_mean
    denom = float(np.sum(i_ac_good * i_ac_good))
    if denom <= 1e-9:                         # no AC current → nothing to remove
        return None, None, nan
    r_v_per_ua = float(np.sum((v[good] - v_mean) * i_ac_good) / denom)
    # Full-length (aligned to the capture time axis) — I_ac uses the
    # good-sample mean but is evaluated over every sample.
    i_ac = i - i_mean
    v_r = r_v_per_ua * i_ac                    # resistive drop (V)
    e_i = v - v_r                              # interface potential (V), DC kept
    return e_i, v_r, r_v_per_ua


def shaped_peak_access(pattern, time_us: np.ndarray, v_active: np.ndarray,
                       i_mon_ua: np.ndarray, *,
                       onset_us: Optional[float] = None) -> List[dict]:
    """Access voltage / resistance at PEAK CURRENT for SMOOTH shaped phases
    (gaussian / sinusoidal) that have NO current-step edge.

    Operator: "change the discontinuous gaussian and sinusoidal to peak current
    because there is no edge in the waveform.  However, having current offset
    would cause an edge, so I expect an iR drop there."

    A smoothly-ramping shaped pulse has no abrupt current step, so the classical
    IR-step-at-the-edge access measurement (``getAccess.m``) doesn't apply —
    there's nothing to extrapolate.  But the ohmic drop ``V_r = R_a·I`` is in
    phase with the current, so it's MAXIMAL at PEAK CURRENT.  Here:

        R_a      = least-squares slope of V_active on I over the phase window
                   ( = Σ(V_ac·I_ac)/Σ(I_ac²), the resistive projection )
        V_access = |R_a|·|I_peak|   at argmax|I| within the phase

    A phase that DOES produce a current step is SKIPPED (it's already in
    :func:`access_index_labels` — a rectangular phase, or a shaped phase riding
    a current OFFSET/pedestal, whose 0→offset transition is a real edge with an
    iR jump caught by the classical edge method + the data-driven
    :func:`_boundary_has_ir_step`).  The continuous-sinusoidal (KHFAC) case is
    handled by :func:`ghazavi_polarization` and skipped here.

    NOTE this is a SEPARATE measurement from the edge-based
    ``access_voltage_per_phase_v`` — it deliberately does NOT feed the E_pol
    operator method (which uses the leading edge access), so the water-window
    limit is unchanged.

    Returns a list of ``{phase_idx, v_access_v, r_access_kohm, peak_idx}``
    dicts (one per qualifying phase; empty for rectangular / edge / offset /
    KHFAC patterns or when the traces are too short).
    """
    out: List[dict] = []
    if pattern is None or onset_us is None:
        return out
    try:
        if is_continuous_sinusoidal(pattern):     # KHFAC → ghazavi_* handles it
            return out
    except Exception:
        pass
    try:
        t = np.asarray(time_us, dtype=float)
        v = np.asarray(v_active, dtype=float)
        i = np.asarray(i_mon_ua, dtype=float)
    except Exception:
        return out
    n = min(t.size, v.size, i.size)
    if n < 16:
        return out
    t, v, i = t[:n], v[:n], i[:n]
    # Phases that already produce a current-step edge (nominal step factor OR a
    # data-driven vertical IR step — e.g. an OFFSET pedestal) — skip; the edge
    # method covers them.
    try:
        _labels = access_index_labels(pattern, time_us=t, v_trace=v,
                                      onset_us=onset_us)
        _edged = {int(ph_idx) for ph_idx, _role in _labels}
    except Exception:
        _edged = set()
    cursor = float(onset_us)
    for k, ph in enumerate(pattern.phases):
        p0 = cursor
        p1 = cursor + ph.width_us
        cursor = p1 + ph.delay_after_us
        # Only the SMOOTH shapes the operator named (gaussian / sinusoidal);
        # rectangular / linear / exp have genuine edges handled elsewhere.
        if ph.shape not in (SHAPE_GAUSSIAN, SHAPE_SINUSOIDAL):
            continue
        # A current OFFSET pedestal makes a real 0→offset edge, so the ohmic
        # drop is an IR STEP there — the operator expects it at the edge, not at
        # peak current.  Exclude on the KNOWN ``offset_ua`` parameter DIRECTLY:
        # a SMALL offset (e.g. 0.5 µA × 2 kΩ = 1 mV) makes a step BELOW the
        # data-driven ``_boundary_has_ir_step`` 3 mV floor, so the trace check
        # alone would miss it and wrongly emit a peak-current access
        # (adversarial finding).  The parameter check is threshold-free.
        if abs(float(getattr(ph, "offset_ua", 0.0))) > 1e-9:
            continue
        if k in _edged:                            # a real step from any cause
            continue
        mask = (t >= p0) & (t < p1)
        if np.count_nonzero(mask) < 8:
            continue
        vv = v[mask]
        ii = i[mask]
        good = np.isfinite(vv) & np.isfinite(ii)
        if np.count_nonzero(good) < 8:
            continue
        vv, ii = vv[good], ii[good]
        i_ac = ii - float(np.mean(ii))
        v_ac = vv - float(np.mean(vv))
        denom = float(np.sum(i_ac * i_ac))
        if denom <= 1e-9:                          # no AC current → no drop
            continue
        r_v_per_ua = float(np.sum(v_ac * i_ac) / denom)
        # Peak-current sample (global index into the full trace).
        _win_idx = np.flatnonzero(mask)
        _loc = int(np.argmax(np.abs(i[mask])))
        peak_idx = int(_win_idx[_loc])
        i_peak = abs(float(i[peak_idx]))
        out.append(dict(phase_idx=k,
                        v_access_v=abs(r_v_per_ua) * i_peak,
                        r_access_kohm=abs(r_v_per_ua) * 1e3,
                        peak_idx=peak_idx))
    return out


def phase_angle_deg(signal: np.ndarray, current_ua: np.ndarray,
                    time_us: np.ndarray, freq_hz: float) -> float:
    """Impedance (EIS) phase angle, in degrees, of a VOLTAGE ``signal``
    relative to the ``current_ua`` at the drive frequency ``freq_hz``.

    Single-bin lock-in (a DFT evaluated at exactly the drive frequency):
    each trace's fundamental phasor is
    ``S = Σ (x − mean(x)) · exp(−j·2π·f·t)`` and the phase angle is
    ``angle(V_phasor · conj(I_phasor)) = angle(V_phasor) − angle(I_phasor)``.

    Sign convention is the electrochemistry/EIS impedance phase
    ``φ = ∠(V/I)``:

      * ``≈ 0°``   — purely RESISTIVE (voltage in phase with current);
      * ``≈ −90°`` — purely CAPACITIVE (voltage LAGS current — a double-
        layer charging, ``I = C·dV/dt``);
      * ``> 0``    — inductive / phase-lead (rare here).

    Integrating over every cycle makes it far more noise-robust than a
    zero-crossing / peak-lag estimate, and it needs no edge detection —
    the continuous sinusoid's fundamental IS the drive frequency.

    Returns NaN when ``freq_hz`` is non-finite/≤ 0, the traces are too
    short (< 16 finite samples), or either trace carries no AC component.
    Used ONLY for continuous-sinusoidal (KHFAC) captures; see
    :func:`is_continuous_sinusoidal`.
    """
    nan = float("nan")
    try:
        f = float(freq_hz)
    except (TypeError, ValueError):
        return nan
    if not np.isfinite(f) or f <= 0.0:
        return nan
    try:
        x = np.asarray(signal, dtype=float)
        i = np.asarray(current_ua, dtype=float)
        t = np.asarray(time_us, dtype=float)
    except Exception:
        return nan
    n = min(x.size, i.size, t.size)
    if n < 16:
        return nan
    x = x[:n]; i = i[:n]; t = t[:n]
    good = np.isfinite(x) & np.isfinite(i) & np.isfinite(t)
    if np.count_nonzero(good) < 16:
        return nan
    x = x[good]; i = i[good]; t = t[good]
    w = 2.0 * np.pi * f
    ph = np.exp(-1j * w * (t * 1e-6))            # time µs → s
    v_ph = np.sum((x - float(np.mean(x))) * ph)
    i_ph = np.sum((i - float(np.mean(i))) * ph)
    if abs(v_ph) < 1e-12 or abs(i_ph) < 1e-12:   # no fundamental content
        return nan
    return float(np.degrees(np.angle(v_ph * np.conj(i_ph))))


def complex_impedance(v_mon: np.ndarray, i_mon_ua: np.ndarray,
                      time_us: np.ndarray, freq_hz: float) -> dict:
    """Complex electrode impedance ``Z(f) = V/I`` at the drive frequency, from
    a GALVANOSTATIC sinusoidal (or square-wave) current — one point of a
    galvanostatic-EIS sweep.  Single-bin lock-in (a DFT at exactly the drive
    frequency): ``V_ph = Σ(V−mean)·e^(−j2πft)``, ``I_ph = Σ(I−mean)·e^(−j2πft)``,
    ``Z = V_ph / I_ph``.

    Units: ``V_mon`` in volts, ``i_mon_ua`` in µA → ``V/µA = 1e6 Ω``, so
    ``|Z|_Ω = |V_ph|/|I_ph| × 1e6``.  Sign convention is the EIS impedance
    phase ``∠Z`` (≈ 0° resistive, → −90° capacitive — V lags I).  The Nyquist
    coordinates are ``Z_real = Re(Z)`` (resistive) and ``Z_imag = Im(Z)``
    (reactive, negative for a capacitor; Nyquist plots ``−Z_imag``).

    **Trigger-phase-independent**: ``Z = V_ph/I_ph`` divides out any common
    time reference, so the capture needs no precise trigger — a free-running
    SAMPLE frame covering several whole cycles is enough (the multi-cycle
    lock-in IS the averaging).

    Returns a dict ``{z_mag_ohm, z_phase_deg, z_real_ohm, z_imag_ohm}`` — all
    NaN when the frequency is non-finite/≤0, the traces are too short
    (< 16 finite samples), or either trace carries no AC component.
    """
    nan = float("nan")
    out = dict(z_mag_ohm=nan, z_phase_deg=nan, z_real_ohm=nan, z_imag_ohm=nan)
    try:
        f = float(freq_hz)
    except (TypeError, ValueError):
        return out
    if not np.isfinite(f) or f <= 0.0:
        return out
    try:
        v = np.asarray(v_mon, dtype=float)
        i = np.asarray(i_mon_ua, dtype=float)
        t = np.asarray(time_us, dtype=float)
    except Exception:
        return out
    n = min(v.size, i.size, t.size)
    if n < 16:
        return out
    v = v[:n]; i = i[:n]; t = t[:n]
    good = np.isfinite(v) & np.isfinite(i) & np.isfinite(t)
    if np.count_nonzero(good) < 16:
        return out
    v = v[good]; i = i[good]; t = t[good]
    w = 2.0 * np.pi * f
    ph = np.exp(-1j * w * (t * 1e-6))            # time µs → s
    v_ph = np.sum((v - float(np.mean(v))) * ph)
    i_ph = np.sum((i - float(np.mean(i))) * ph)
    if abs(v_ph) < 1e-12 or abs(i_ph) < 1e-12:   # no fundamental content
        return out
    z = (v_ph / i_ph) * 1e6                        # V/µA → Ω
    out["z_mag_ohm"] = float(abs(z))
    out["z_phase_deg"] = float(np.degrees(np.angle(z)))
    out["z_real_ohm"] = float(z.real)
    out["z_imag_ohm"] = float(z.imag)
    return out


def recompute_capture_metrics(capture: Capture, surface_area_um2: float,
                              *, class_override=None,
                              polarization_source: str = "auto",
                              depol_us: Optional[float] = None) -> CaptureMetrics:
    """Re-run :func:`compute_metrics` for a capture, optionally FORCING the
    response class (POLARIS "Good / Broken / Open" override — operator: "setting
    the channel/combo as Good/Broken/Open (the appropriate metrics are to be
    computed as well)").

    ``class_override`` is one of ``RESPONSE_CLASSES`` (``"normal"`` = Good) or
    ``None`` to use the automatic classifier.  Metrics are (re)computed for the
    chosen class — Good keeps access V/R + E_pol (C_eff cleared), Broken fits
    the parallel R‖C (R, C, τ) and suppresses access/E_pol, Open/Capacitive
    report the linear C_eff and suppress access/E_pol.  Mutates + returns
    ``capture.metrics``.

    ``depol_us`` defaults to the value already stored on ``capture.metrics``
    (so a POLARIS re-classify keeps the run's E_pol time delay), falling back
    to the canonical 12 µs for legacy captures."""
    if depol_us is None:
        prev = getattr(capture, "metrics", None)
        depol_us = float(getattr(prev, "depolarization_us", DEPOLARIZATION_TIME_US)
                         or DEPOLARIZATION_TIME_US)
    return compute_metrics(capture, surface_area_um2,
                           polarization_source=polarization_source,
                           force_class=class_override, depol_us=depol_us)


def compute_metrics(capture: Capture, surface_area_um2: float,
                    *, polarization_source: str = "auto",
                    force_class: Optional[str] = None,
                    failure_open_z_mohm: Optional[float] = None,
                    failure_broken_z_mohm: Optional[float] = None,
                    failure_min_current_ua: float = 0.0,
                    depol_us: float = DEPOLARIZATION_TIME_US) -> CaptureMetrics:
    """Populate ``capture.metrics`` from its raw traces.

    Orchestrates every per-capture analysis: charge injection, driving
    voltage, access voltages and resistances, polarization, and capacitance.
    All the heavy lifting lives in the helpers above; this function just
    decides which trace to feed to each one and packs the results into the
    capture's ``CaptureMetrics`` dataclass.

    Parameters
    ----------
    capture : Capture
        Must contain ``time_us``, ``v_mon_v``, ``i_mon_ua``. ``e_act_v`` and
        ``e_ret_v`` are optional (set when an instrumentation amplifier is
        connected) and produce more accurate ``V_d`` and per-electrode
        ``E_pol`` numbers when present.
    surface_area_um2 : float
        Geometric surface area of the active electrode site (µm²). Needed
        to convert charge-per-phase into ``Q_inj`` (mC/cm²).
    polarization_source : {'auto', 'vmon', 'potentials'}
        - ``auto``  : use E_act/E_ret if both are available, else fall back
                      to V_mon as a proxy for polarization.
        - ``vmon``  : always use V_mon (simpler / fewer wires).
        - ``potentials`` : require E_act and E_ret (raises if missing).
    """
    pat = capture.pattern
    m = CaptureMetrics()
    # Record the E_pol time delay used, so the plot markers + POLARIS read the
    # SAME value the polarization was computed with (operator-configurable).
    try:
        m.depolarization_us = float(depol_us)
    except (TypeError, ValueError):
        m.depolarization_us = DEPOLARIZATION_TIME_US

    # ----- 1. Charge math -------------------------------------------------
    # Q_ph and Q_inj depend only on the *pattern* and surface area, never
    # on the actual scope traces — so we always have these even if the scope
    # missed the trigger (we'll fail later metrics gracefully if so).
    q_ph, q_inj = charge_injection_mc_per_cm2(pat, surface_area_um2)
    m.charge_per_phase_nc = q_ph
    m.charge_injection_mc_per_cm2 = q_inj

    # ----- 1b. Tissue-damage screen (Shannon + modified Shannon) ---------
    # Run the Shannon equation + the macro/micro caps from
    # ``damage_models`` against the just-computed Q_ph / Q_inj /
    # surface area. The result populates four new CaptureMetrics
    # fields (``shannon_k_value``, ``damage_classification``,
    # ``damage_criteria``, ``damage_band``) so the Viewer can render
    # the verdict alongside Q_inj. Per Li et al. 2024 the Shannon
    # model alone has ~64% accuracy and misclassifies ~36% of
    # damaging stimulation as safe — the GUI surfaces the result
    # as guidance, never as a hard interlock. See
    # ``stimtest/damage_models.py`` for the full reference list.
    try:
        from .damage_models import (
            assess_from_capture_metrics,
            damage_level_from_classification,
        )
        assessment = assess_from_capture_metrics(
            charge_per_phase_nc=q_ph,
            charge_injection_mc_per_cm2=q_inj,
            surface_area_um2=surface_area_um2,
        )
        m.shannon_k_value = float(assessment.k_value)
        m.damage_classification = assessment.classification
        m.damage_criteria = {
            "shannon": bool(assessment.above_shannon),
            "macro_cap": bool(assessment.above_macro_cap),
            "micro_cap": bool(assessment.above_micro_cap),
        }
        m.damage_band = assessment.band
        # Coarse 0-4 damage level from the binary Shannon verdict.
        # Returns ``None`` for ``insufficient_data`` so a downstream
        # UI can render "—"; we store -1 in the dataclass to keep
        # the field a plain int that round-trips through JSON /
        # numpy without special-casing.
        level = damage_level_from_classification(assessment.classification)
        m.damage_level = -1 if level is None else int(level)
    except Exception:
        # Damage screen failure must never block the rest of the
        # metric pipeline — leave the dataclass defaults in place
        # (``shannon_k_value = NaN``, classification =
        # ``"insufficient_data"``, ``damage_level = -1``).
        pass

    # ----- 1c. NeurostimML local-inference screen (optional) ------------
    # If the user has installed the local RF-Partial-19 model via
    # Help → Install NeurostimML model…, run the higher-accuracy
    # ML prediction alongside Shannon. This is opportunistic —
    # silently no-ops when the model file isn't on disk, the
    # feature vector can't be assembled (e.g. no pulse rate /
    # duty cycle reachable from the bare Capture), or scikit-learn
    # isn't installed. The Shannon screen above always runs and
    # remains the reliable baseline; the ML prediction is a
    # second opinion the GUI can render side-by-side.
    try:
        from .neurostimml import predict_from_capture
        result = predict_from_capture(capture, surface_area_um2)
        if result is not None:
            m.neurostimml_classification = result.classification
            m.neurostimml_probability = float(result.probability)
    except Exception:
        # Same defensive posture as Shannon — never let the
        # optional ML hop break per-capture metric population.
        pass

    if capture.time_us.size == 0 or capture.v_mon_v.size == 0:
        # Empty capture (scope timeout / acquisition error) — return what we
        # already have and let the caller decide what to do.
        capture.metrics = m
        return m

    # ----- 1d. Pulse-onset anchor for every phase-TIME chain ------------
    # t=0 is TRIGGER-relative: with the digital sync it IS the onset
    # (detector returns ≈0, nothing changes), but with the I_mon-trigger
    # fallback the scope fires mid-pulse and the chains below would
    # sample the WRONG phase (operator: Epol1 landed where Epol2
    # belongs; Epol2 fell off the record).  Detect once, thread through.
    onset_us = pulse_onset_us(capture.time_us, capture.i_mon_ua,
                              capture.v_mon_v)

    # ----- 2. Driving voltage V_d -----------------------------------------
    # V_mon = E_act - E_ret straight off the stimulator's monitor output.
    # Always available; we use its abs-max as the baseline V_d estimate.
    # DESPIKE so a switching spike at small currents isn't reported as V_d
    # (operator: "the spike does not mislead … the driving voltage"), AND
    # restrict the abs-max scan to the pulse region so a post-pulse tail
    # transient (discharge / next-period / open-input, in the ~20 000-pt record
    # tail) isn't reported as V_d either (which would corrupt C_d = Q_inj / V_d).
    # Two complementary fixes combined (despike full trace FIRST — the median
    # filter needs the contiguous time axis — then window to the pulse region).
    roi = pulse_region_mask(capture.time_us, pat)
    _v_despiked = _despike(capture.v_mon_v, capture.time_us)
    if _v_despiked.size == capture.time_us.size:
        v_mon_roi = _v_despiked[roi]
    else:
        v_mon_roi = _v_despiked
    v_d_vmon = driving_voltage_from_vmon(v_mon_roi)

    # If the instrumentation amplifier is wired up we get the active and
    # return potentials separately, which gives a slightly cleaner V_d
    # measurement (no scope-input attenuation effects). Take whichever is
    # larger to be conservative — V_d quoted in the paper is the *actual*
    # max of |E_act - E_ret|.
    e_act = capture.e_act_v
    e_ret = capture.e_ret_v
    _n = capture.time_us.size
    _has_eret = e_ret is not None and e_ret.size == _n
    # DERIVE E_act = V_mon + E_ret when the active electrode wasn't digitised
    # directly but E_ret WAS — the differential identity E_act = V_mon + E_ret
    # (RAW).  This matches the DERIVED E_act trace the live plot draws
    # (``multichannel_scope._refresh_traces``), so the active metrics AND the
    # plot markers ride the SAME active-electrode trace the operator sees
    # (operator: "put the experiment plot markers on the Eact waveform when
    # appropriate").  The common V_mon/I_mon-only case (no E_ret) is
    # unaffected → E_act stays absent and ``active_trace`` stays V_mon.
    _eact_recorded = e_act is not None and e_act.size == _n
    if (not _eact_recorded and _has_eret
            and capture.v_mon_v is not None and capture.v_mon_v.size == _n):
        e_act = (np.asarray(capture.v_mon_v, dtype=float)
                 + np.asarray(e_ret, dtype=float))
        _eact_recorded = True                        # now available (derived)
    has_potentials = (e_act is not None and e_ret is not None
                      and e_act.size == e_ret.size == _n)
    # ACTIVE-electrode metrics — access V/R, per-phase driving, and E_pol —
    # are measured on E_act (active vs reference) WHEN IT'S RECORDED OR
    # DERIVABLE, else V_mon (active vs return) as the proxy (operator: "active
    # metrics and electrode polarization should be based on Eact when
    # available, otherwise Vmon").  So the metric VALUE lands on the SAME trace
    # the plot markers sit on (gotcha #87), never V_mon-value-on-E_act-marker.
    # ``polarization_source == 'vmon'`` forces the V_mon proxy.
    _has_eact = _eact_recorded and polarization_source != "vmon"
    active_trace = e_act if _has_eact else capture.v_mon_v
    if has_potentials and polarization_source != "vmon":
        # Despike AND restrict to the pulse region (same as V_mon) so neither a
        # switching spike nor a post-pulse tail transient on the
        # instrumentation-amp traces can inflate V_d.  has_potentials guarantees
        # e_act/e_ret are length ``_n`` == time_us == roi, so [roi] is safe.
        v_d = driving_voltage_from_potentials(
            _despike(e_act, capture.time_us)[roi],
            _despike(e_ret, capture.time_us)[roi])
        m.driving_voltage_v = max(v_d_vmon, v_d)
    else:
        m.driving_voltage_v = v_d_vmon

    # ----- 3. Access voltage and resistance per phase -------------------
    # Canonical port of getAccess.m. Returns one entry per access point in
    # the order [lead-phase1, trail-phase1, lead-phase2, trail-phase2]
    # (entries 2-3 only included if there's an interphase delay; entry 4
    # only if there's a discharge delay). The algorithm:
    #   a. compute |dV/dt| with double-smoothing
    #   b. find exactly N peaks in |dV/dt| using an adaptive threshold
    #   c. for each peak, walk forward and find where the trace deviates
    #      from a linear fit (this is the *end* of the access drop, where
    #      the polarization phase begins). That point's voltage is sampled
    #      and differenced against the appropriate baseline (pre-pulse,
    #      driving voltage, or end-of-interphase, depending on which
    #      access point we're computing).
    # See stimtest/metrics.py:access_voltage_and_resistance for full detail.
    #
    # Compute the (data-driven-augmented) access LABELS ONCE from the ACTIVE
    # trace and reuse them for the active read, the return read, AND the E_pol
    # leading-access mapping below — so all three stay PARALLEL (same count,
    # same order) even when a sinusoidal / gaussian boundary contributes a
    # data-driven vertical-IR-step access point (gotcha #122).
    _acc_labels = access_index_labels(pat, time_us=capture.time_us,
                                      v_trace=active_trace, onset_us=onset_us)
    va_list, ra_list, access_idx = access_voltage_and_resistance(
        capture.time_us, active_trace, pat, onset_us=onset_us,
        labels=_acc_labels,
    )
    m.access_voltage_per_phase_v = list(va_list)
    m.access_resistance_per_phase_kohm = list(ra_list)

    # Return-side access V_a / R_a — same algorithm + the SAME labels but
    # reads off the E_ret trace so the user gets a separate measurement for
    # the counter / return path. Skipped if E_ret isn't recorded; if it is,
    # the return access list is parallel (same length, same access_idx
    # ordering) to the active-side ``access_voltage_per_phase_v``.
    ret_access_idx: List[int] = []
    if e_ret is not None and e_ret.size == capture.time_us.size:
        va_ret, ra_ret, ret_access_idx = access_voltage_and_resistance(
            capture.time_us, e_ret, pat, onset_us=onset_us,
            labels=_acc_labels,
        )
        m.return_access_voltage_per_phase_v = list(va_ret)
        m.return_access_resistance_per_phase_kohm = list(ra_ret)
    else:
        m.return_access_voltage_per_phase_v = []
        m.return_access_resistance_per_phase_kohm = []

    # Driving voltage per phase — for both the ACTIVE and RETURN
    # electrodes when the instrumentation amp is wired up. Mirrors
    # getVoltageMetrics.m's ``drivingVoltage_arr`` calc: for each
    # phase k, take |reference_voltage − value at the driving point|,
    # where the reference is the pre-pulse rest level for phase 1 and
    # the end of the prior interphase delay for later phases.
    # ACTIVE side: E_act when recorded, else V_mon (``active_trace``).
    m.active_driving_voltage_per_phase_v = _driving_voltage_per_phase(
        capture.time_us, active_trace, pat, onset_us=onset_us,
    )
    # RETURN side: only when a separate E_ret trace is recorded.
    if _has_eret:
        m.return_driving_voltage_per_phase_v = _driving_voltage_per_phase(
            capture.time_us, e_ret, pat, onset_us=onset_us,
        )
    else:
        m.return_driving_voltage_per_phase_v = []

    # ----- 4. Electrode polarization (E_pol) ----------------------------
    # OPERATOR-SPEC E_pol: the TIME method (phase_end + depol) for phases
    # with a trailing recovery delay (interphase/discharge — the IEEE NER
    # E_mc convention), and driving_potential − leading_access for delay-
    # less phases.  (This REPLACED the derivative/auto method, which sampled
    # the trailing-access plateau at phase end — a different, larger value
    # that disagreed with the on-plot Emc/Ema marker and the operator's
    # published definition.)  This same value drives the table, the plot
    # markers, and the water-window limit decision.
    #
    # The no-delay branch needs leading-access MAGNITUDES per phase, which
    # we extract from the access labels + va_list computed in step 3.  REUSE
    # the data-driven ``_acc_labels`` from step 3 (don't recompute nominal —
    # it would drop any data-driven vertical-IR-step boundary and misalign
    # the mapping against ``va_list``).

    def _lead_mags(va):
        out_ = [float("nan")] * pat.num_phases
        for _i, (_pidx, _role) in enumerate(_acc_labels):
            if _role == "lead" and 0 <= _pidx < pat.num_phases and _i < len(va):
                out_[_pidx] = abs(va[_i])
        return out_

    def _trail_epol(trace, acc_idx):
        """Per-phase E_pol from the TRAILING access point = the raw trace value
        at the post-IR-drop plateau (used for a SHORT trailing delay, Cogan
        2008).  NaN where the phase has no trailing access."""
        out_ = [float("nan")] * pat.num_phases
        _tr = np.asarray(trace, dtype=float)
        for _i, (_pidx, _role) in enumerate(_acc_labels):
            if _role == "trail" and 0 <= _pidx < pat.num_phases and _i < len(acc_idx):
                _ix = acc_idx[_i]
                if _ix is not None and 0 <= int(_ix) < _tr.size:
                    out_[_pidx] = float(_tr[int(_ix)])
        return out_

    # ACTIVE E_pol on ``active_trace`` (E_act when recorded, else V_mon) — the
    # driving + leading-access inputs above are on that SAME trace, so the
    # E_pol value, the table, the plot marker, and the water-window limit all
    # agree.
    m.polarization_per_phase_v = polarization_per_phase(
        capture.time_us, active_trace, pat, method="operator",
        depol_us=depol_us, onset_us=onset_us,
        driving_per_phase=m.active_driving_voltage_per_phase_v,
        leading_access_per_phase=_lead_mags(m.access_voltage_per_phase_v),
        trailing_epol_per_phase=_trail_epol(active_trace, access_idx),
    )
    # RETURN E_pol only when a separate E_ret trace is recorded.
    if _has_eret:
        m.return_polarization_per_phase_v = polarization_per_phase(
            capture.time_us, e_ret, pat, method="operator",
            depol_us=depol_us, onset_us=onset_us,
            driving_per_phase=m.return_driving_voltage_per_phase_v,
            leading_access_per_phase=_lead_mags(
                m.return_access_voltage_per_phase_v),
            trailing_epol_per_phase=_trail_epol(e_ret, ret_access_idx),
        )
    else:
        m.return_polarization_per_phase_v = []

    # ----- 4a. CONTINUOUS SINUSOIDAL (KHFAC) — Ghazavi & Cogan 2018 -------
    # For a continuous symmetric-biphasic SINUSOID with no interphase /
    # discharge / interpulse delays there is NO current-step edge, so the
    # pulsed access-voltage extrapolation (steps 3-4 above) is meaningless.
    # Replace it with the phase-decomposition polarization: the resistive
    # (in-phase-with-I) part is projected out and the interface excursions
    # E_mc / E_ma feed the SAME water-window limit + VT-max ramp so the sweep
    # finds max charge injection for KHFAC (operator: "implement this in the
    # biphasic symmetric sinusoidal … with no interphase, discharge, and
    # interpulse delays").
    if is_continuous_sinusoidal(pat):
        # Window out the 1 µs interpulse-discharge transient (robust E_off /
        # extrema) only when the pattern actually carries one — a plain
        # continuous sinusoid stays byte-identical.
        _robust_khfac = float(getattr(pat, "interpulse_discharge_us", 0.0)) > 0
        g = ghazavi_polarization(
            active_trace, capture.i_mon_ua, capture.time_us,
            rate_hz=getattr(pat, "rate_hz", None), robust=_robust_khfac)
        m.polarization_method = "sinusoidal"
        m.ghazavi_e_mc_v = g["e_mc_v"]
        m.ghazavi_e_ma_v = g["e_ma_v"]
        m.ghazavi_e_io_v = g["e_io_v"]
        m.ghazavi_e_off_v = g["e_off_v"]
        m.ghazavi_r_access_kohm = g["r_access_kohm"]
        m.ghazavi_v_access_v = g["v_access_v"]
        m.ghazavi_freq_khz = g["freq_khz"]
        # RETURN-electrode decomposition (E_ret vs I_mon) — the same Ghazavi
        # quantities for the return/counter electrode, reported alongside the
        # active set (operator: "report the metrics for the active and return"
        # like the pulsed tests).  Only when E_ret is recorded.
        if _has_eret:
            g_ret = ghazavi_polarization(
                e_ret, capture.i_mon_ua, capture.time_us,
                rate_hz=getattr(pat, "rate_hz", None), robust=_robust_khfac)
            m.ghazavi_return_e_mc_v = g_ret["e_mc_v"]
            m.ghazavi_return_e_ma_v = g_ret["e_ma_v"]
            m.ghazavi_return_e_io_v = g_ret["e_io_v"]
            m.ghazavi_return_e_off_v = g_ret["e_off_v"]
            m.ghazavi_return_r_access_kohm = g_ret["r_access_kohm"]
            m.ghazavi_return_v_access_v = g_ret["v_access_v"]
        # PHASE ANGLE of each VOLTAGE waveform vs I_mon at the drive
        # frequency (operator: "phase angle difference measured in the
        # voltage waveforms (Vmon, Eret, Eact) versus Imon for continuous
        # sinusoidal").  Impedance/EIS phase — ~0° resistive, →−90°
        # capacitive.  V_mon always; E_ret / E_act only when recorded (E_act
        # includes the V_mon+E_ret derived case set up above — same convention
        # as the other active metrics).
        #
        # LOCK-IN FREQUENCY = the EXACT drive fundamental ``rate_hz`` — a
        # continuous symmetric-biphasic sinusoid is one full sine cycle per
        # period, so the fundamental is exactly ``1/period = rate_hz``.  Do
        # NOT use the zero-crossing-measured ``ghazavi_freq_khz`` as the
        # primary bin (adversarial finding): that estimate is systematically
        # off (a boundary off-by-one + noise chatter), and projecting the
        # lock-in onto the wrong DFT bin returns a plausible-but-GARBAGE angle
        # (a purely capacitive electrode read as −20° instead of −90°) that
        # does NOT trip the |v_ph|>1e-12 guard.  The phasor phase-DIFFERENCE
        # is leakage-robust (V and I leak identically and cancel), so the exact
        # rate is strictly better.  The measured estimate is the fallback only
        # when the pattern carries no rate.  Also OVERWRITE the reported
        # frequency with the exact value so the displayed "Pulse frequency"
        # and the phase agree.
        _f_hz = float(getattr(pat, "rate_hz", 0.0) or 0.0)
        if not (_f_hz > 0.0):
            _f_hz = (m.ghazavi_freq_khz * 1e3
                     if np.isfinite(m.ghazavi_freq_khz) else 0.0)
        if _f_hz > 0.0:
            m.ghazavi_freq_khz = _f_hz * 1e-3
        _i_mon = capture.i_mon_ua
        if capture.v_mon_v is not None:
            m.phase_angle_vmon_deg = phase_angle_deg(
                capture.v_mon_v, _i_mon, capture.time_us, _f_hz)
        if _has_eret:
            m.phase_angle_eret_deg = phase_angle_deg(
                e_ret, _i_mon, capture.time_us, _f_hz)
        if _eact_recorded:                       # recorded OR derived above
            m.phase_angle_eact_deg = phase_angle_deg(
                e_act, _i_mon, capture.time_us, _f_hz)
        # DRIVING VOLTAGE for KHFAC = the AC AMPLITUDE (peak) of V_mon
        # (operator: "amplitude/peak"), V_mo = √2·RMS(V_mon_ac) = |Z|·I_o —
        # the peak voltage swing the stimulator drives, the compliance figure
        # analogous to the pulsed peak V_d.  NOT max|V_mon| (which folds in any
        # DC offset / E_off drift); √2·std is DC-immune (std removes the mean).
        try:
            _vm = np.asarray(capture.v_mon_v, dtype=float)
            _vm = _vm[np.isfinite(_vm)]
            if _vm.size >= 16:
                m.driving_voltage_v = float(np.sqrt(2.0) * np.std(_vm))
        except Exception:
            pass
        # Feed the interface excursions into the ramp's water-window check
        # (E_mc negative → cathodic limit, E_ma positive → anodic).  Drop the
        # spurious pulsed access / driving-per-phase values (no edges in a
        # continuous sinusoid).  ORDER the list BY PHASE POLARITY (anodic phase
        # → E_ma, cathodic phase → E_mc) so `compute_metric_markers` pairs each
        # phase's Emc/Ema TAG with the matching value — for an ANODIC-FIRST
        # sinusoid a fixed [E_mc, E_ma] order swapped the two on the plot (audit
        # finding; the limit check + table are order-agnostic, so only the plot
        # label→value pairing was wrong).
        _phase_pol = []
        for _ph in pat.phases:
            _v = g["e_ma_v"] if float(_ph.amplitude_ua) > 0 else g["e_mc_v"]
            if _v == _v:                     # NaN-safe
                _phase_pol.append(float(_v))
        m.polarization_per_phase_v = _phase_pol
        m.return_polarization_per_phase_v = []
        m.access_voltage_per_phase_v = []
        m.access_resistance_per_phase_kohm = []
        m.return_access_voltage_per_phase_v = []
        m.return_access_resistance_per_phase_kohm = []
        m.active_driving_voltage_per_phase_v = []
        m.return_driving_voltage_per_phase_v = []

    # ----- 4b. Response classification → conditional C_eff --------------
    # Operator: "show capacitance since it is so linear … but only when the
    # response is entirely capacitive, open circuit, or broken.  Do not
    # include access voltage or resistance [or electrode polarization]."
    # For those cases there's no separable resistive + Faradaic component,
    # so C_eff = I/(dV/dt) IS meaningful and access V/R + E_pol are NOT — we
    # report the former and SUPPRESS the latter.  A NORMAL mixed electrode
    # keeps access V/R + E_pol and leaves C_eff NaN.
    from .config import STIM_VOLTAGE_COMPLIANCE_V
    _auto_class, _c_eff_nf = classify_response_and_ceff(
        capture.time_us, capture.v_mon_v, pat, onset_us=onset_us,
        driving_v=m.driving_voltage_v,
        compliance_v=STIM_VOLTAGE_COMPLIANCE_V,
        open_z_mohm=failure_open_z_mohm, broken_z_mohm=failure_broken_z_mohm,
        min_current_ua=failure_min_current_ua,
    )
    # ``force_class`` (POLARIS Good/Broken/Open override) wins over the
    # automatic classification when it's a valid class; otherwise use the
    # classifier's verdict (operator: "setting the channel/combo as
    # Good/Broken/Open — the appropriate metrics are to be computed as well").
    m.response_class = (force_class if force_class in RESPONSE_CLASSES
                        else _auto_class)
    _amp0 = abs(float(pat.phases[0].amplitude_ua)) if pat.phases else 0.0
    if m.response_class != "normal":
        # Linear C_eff = |I|/|dV/dt|.  The classifier only returns it for a
        # straight ramp, so a FORCED open/capacitive on a non-straight capture
        # needs its own slope fit.
        c_eff = _c_eff_nf
        if not (isinstance(c_eff, float) and np.isfinite(c_eff)):
            c_eff = effective_capacitance_from_ramp(
                capture.time_us, capture.v_mon_v, pat,
                onset_us=onset_us, amp_ua=_amp0)
        m.effective_capacitance_nf = c_eff
        # BROKEN / OPEN → fit the parallel R‖C model to recover R, C, τ
        # (operator: "fit the exponential response with RC … report all
        # three").  Under the FLATNESS definition an OPEN electrode PLATEAUS at
        # I·R — an R‖C is exactly that (V=V∞(1−e^{−t/τ}), R=V∞/I, C=τ/R), so it
        # gets the same fit; a BROKEN electrode's charge-then-degrade also fits
        # R‖C.  CAPACITIVE stays a pure linear-ramp capacitance (the
        # ``I/(dV/dt)`` C above; τ→∞, no R).  The fit runs on ``active_trace``
        # — E_act when recorded (directly or derived V_mon+E_ret), else V_mon
        # (operator: "broken channels should be based on Eact, if possible").
        if m.response_class in ("broken", "open"):
            _r_k, _c_n, _tau, _rc_r2 = fit_parallel_rc(
                capture.time_us, active_trace, pat, onset_us=onset_us,
                amp_ua=_amp0)
            if _rc_r2 >= _RC_FIT_MIN_R2:
                m.effective_capacitance_nf = _c_n   # RC-fit C (linear C was NaN)
                m.rc_fit_resistance_kohm = _r_k
                m.rc_fit_tau_us = _tau
            elif m.response_class == "open":
                # Flat plateau but the R‖C didn't converge → the linear slope
                # C above is meaningless (dV/dt≈0 → huge); report no capacitance.
                m.effective_capacitance_nf = float("nan")
        else:
            # CAPACITIVE → pure capacitance, no R‖C — clear any stale RC fit
            # (matters when a FORCED override flips broken → capacitive).
            m.rc_fit_resistance_kohm = float("nan")
            m.rc_fit_tau_us = float("nan")
        # PER-PHASE bad-response metrics (operator: "for broken and open
        # channels, compute the same metrics for other phases") — the same
        # fit per pattern phase, each on its own onset-anchored window with
        # its own amplitude.  Broken: per-phase R‖C on ``active_trace``
        # (E_act-based when available, matching the scalar); open /
        # capacitive: per-phase linear C_eff on V_mon (same trace as the
        # scalar).  Entry 0 mirrors the scalar values; a failed fit is NaN.
        _n_ph = pat.num_phases
        m.effective_capacitance_per_phase_nf = [float("nan")] * _n_ph
        m.rc_fit_resistance_per_phase_kohm = [float("nan")] * _n_ph
        m.rc_fit_tau_per_phase_us = [float("nan")] * _n_ph
        for _k in range(_n_ph):
            _amp_k = abs(float(pat.phases[_k].amplitude_ua))
            if _amp_k <= 0:
                continue
            if m.response_class in ("broken", "open"):
                if _k == 0:
                    # Phase 1 = the scalar fit above (don't fit twice).
                    m.effective_capacitance_per_phase_nf[0] = (
                        m.effective_capacitance_nf)
                    m.rc_fit_resistance_per_phase_kohm[0] = (
                        m.rc_fit_resistance_kohm)
                    m.rc_fit_tau_per_phase_us[0] = m.rc_fit_tau_us
                    continue
                _rk, _cn, _tu, _r2 = fit_parallel_rc(
                    capture.time_us, active_trace, pat, onset_us=onset_us,
                    amp_ua=_amp_k, phase_idx=_k)
                if _r2 >= _RC_FIT_MIN_R2:
                    m.effective_capacitance_per_phase_nf[_k] = _cn
                    m.rc_fit_resistance_per_phase_kohm[_k] = _rk
                    m.rc_fit_tau_per_phase_us[_k] = _tu
            else:
                if _k == 0:
                    m.effective_capacitance_per_phase_nf[0] = (
                        m.effective_capacitance_nf)
                    continue
                m.effective_capacitance_per_phase_nf[_k] = (
                    effective_capacitance_from_ramp(
                        capture.time_us, capture.v_mon_v, pat,
                        onset_us=onset_us, amp_ua=_amp_k, phase_idx=_k))
        # Entirely-capacitive / open / broken → no resistive access step and
        # no separable Faradaic polarization; suppress those metrics so the
        # table/plot/limit-check don't report meaningless V_a / R_a / E_pol
        # (an open electrode then stops on voltage compliance rather than a
        # spurious water-window trip from garbage E_pol).
        m.access_voltage_per_phase_v = []
        m.access_resistance_per_phase_kohm = []
        m.return_access_voltage_per_phase_v = []
        m.return_access_resistance_per_phase_kohm = []
        m.polarization_per_phase_v = []
        m.return_polarization_per_phase_v = []
        m.shaped_access_v_per_phase = []
        m.shaped_access_r_kohm_per_phase = []
        m.return_shaped_access_v_per_phase = []
        m.return_shaped_access_r_kohm_per_phase = []
    else:
        # NORMAL / Good → access V/R + E_pol (computed above) stay; no C_eff /
        # R‖C (clear them in case a FORCED override flips broken → Good).
        m.effective_capacitance_nf = float("nan")
        m.rc_fit_resistance_kohm = float("nan")
        m.rc_fit_tau_us = float("nan")
        m.effective_capacitance_per_phase_nf = []
        m.rc_fit_resistance_per_phase_kohm = []
        m.rc_fit_tau_per_phase_us = []
        # PEAK-CURRENT access for SMOOTH shaped phases (gaussian / sinusoidal
        # with no current-step edge) — operator: "change the discontinuous
        # gaussian and sinusoidal to peak current because there is no edge …
        # having current offset would cause an edge, so I expect an iR drop
        # there."  A SEPARATE measurement from the edge-based access above
        # (does NOT feed E_pol — see shaped_peak_access); the KHFAC continuous
        # sinusoid + offset/edge phases self-exclude in the helper.
        _npha = len(pat.phases)

        def _fill_shaped(_trace):
            _v = [float("nan")] * _npha
            _r = [float("nan")] * _npha
            for _e in shaped_peak_access(pat, capture.time_us, _trace,
                                         capture.i_mon_ua, onset_us=onset_us):
                _k = int(_e["phase_idx"])
                if 0 <= _k < _npha:
                    _v[_k] = float(_e["v_access_v"])
                    _r[_k] = float(_e["r_access_kohm"])
            return _v, _r

        _sv, _sr = _fill_shaped(active_trace)
        m.shaped_access_v_per_phase = _sv
        m.shaped_access_r_kohm_per_phase = _sr
        if e_ret is not None and e_ret.size == capture.time_us.size:
            _rv, _rr = _fill_shaped(e_ret)
            m.return_shaped_access_v_per_phase = _rv
            m.return_shaped_access_r_kohm_per_phase = _rr
        else:
            m.return_shaped_access_v_per_phase = []
            m.return_shaped_access_r_kohm_per_phase = []

    # ----- 5. Interpulse potential (V vs Ag|AgCl) -----------------------
    # Mean of E_ret across the time-segments OUTSIDE the active pulse
    # — i.e. ``time < 0`` (pre-pulse baseline) plus ``time >=
    # total_pulse_us`` (post-discharge tail). Mirrors
    # ``getVoltageMetrics.m``'s ``prePulsePotenial`` calc but averages
    # both rest segments so the result reflects the genuinely-settled
    # interpulse rest potential rather than just the leading edge.
    #
    # Falls back to V_mon if E_ret isn't available — the absolute
    # value is then biased by the stimulator's offset, but the metric
    # still tracks drift across captures, which is what the user
    # cares about.
    #
    # We also record the pre- and post-pulse means SEPARATELY so the
    # runner can feed them into the electrode-potential learning
    # store (one as ``phase="pre"``, the other as ``phase="post"``)
    # without having to recompute the windows. Only the E_ret-derived
    # half is published in those split fields — the V_mon fallback is
    # NOT a measurement of any single electrode's OCP, so feeding
    # those numbers into the learning bin would corrupt it.
    # Preference order for the interpulse-potential trace:
    #   1. E_ret  — the return electrode's potential is the canonical
    #               reference (matches MATLAB getVoltageMetrics.m's
    #               ``prePulsePotenial`` and the IEEE NER paper's
    #               definition).
    #   2. E_act  — when only the active line is wired up, its idle
    #               level still measures the electrode-electrolyte rest
    #               potential, just polarity-flipped.  Better than V_mon
    #               because V_mon carries the stimulator's DC offset.
    #   3. V_mon  — last-resort fallback (no instrumentation amp wired).
    #               Absolute value is biased by the stim offset but the
    #               metric still tracks drift across captures.
    e_ret_for_potentials = (e_ret
                            if e_ret is not None and e_ret.size == capture.time_us.size
                            else None)
    e_act_for_potentials = (e_act
                            if e_act is not None and e_act.size == capture.time_us.size
                            else None)
    if e_ret_for_potentials is not None:
        _eip_trace = e_ret_for_potentials
        _eip_source = "e_ret"
    elif e_act_for_potentials is not None:
        _eip_trace = e_act_for_potentials
        _eip_source = "e_act"
    else:
        _eip_trace = capture.v_mon_v
        _eip_source = "v_mon"
    m.interpulse_potential_v = _interpulse_potential(
        capture.time_us, _eip_trace, pat, onset_us=onset_us,
    )
    # Split pre / post pulse means for the electrode-potential learning
    # store — only populated when we have a real electrode trace
    # (E_ret OR E_act), since the runner uses these to track each
    # electrode's rest OCP across captures.  V_mon-derived numbers
    # would corrupt the learning bin (they conflate stim offset with
    # electrode potential), so leave them at NaN in the V_mon
    # fallback case.
    if _eip_source in ("e_ret", "e_act"):
        pre_v, post_v = _interpulse_potential_split(
            capture.time_us, _eip_trace, pat, onset_us=onset_us,
        )
        # Always populate the return_* fields for back-compat — they
        # were originally E_ret-only, now they carry whichever
        # electrode trace we ended up using (E_ret preferred).  The
        # learning store keys by coating + electrode role, so the
        # source-of-truth annotation isn't lost.
        m.return_pre_pulse_potential_v = pre_v
        m.return_post_pulse_potential_v = post_v
    # Else leave both at the dataclass default (NaN); the runner
    # interprets NaN as "no usable measurement, don't record".

    # ----- 6. Capacitance metrics ---------------------------------------
    # (Pulse-derived C_eff = I/|dV/dt| was REMOVED — see the note above the
    # compute_metrics block: a VT can't separate i_c from i_f, so it isn't a
    # valid capacitance.)
    # Driving capacitance C_d = max(Q_inj) / V_d (mF/cm²). This is the
    # whole-system efficiency metric from IEEE NER paper: how much charge
    # we got per volt across the entire return-active circuit.
    m.driving_capacitance_mf_per_cm2 = driving_capacitance(q_inj, m.driving_voltage_v)

    # ----- 7. Driving impedance Z_d + driving energy --------------------
    # Operator request.  Z_d = |V_d| / |I_stim| (kΩ) — the total impedance
    # the stimulator drives into (access R + polarization), driving voltage
    # ÷ programmed stimulus current.  Driving energy = ∫ V_mon·I_IDEAL dt over
    # the WHOLE pulse (every phase window — the passive interphase/discharge
    # gaps carry ~0 current so add ~0, and the trailing interpulse region —
    # where the end-of-record artifact lives — is EXCLUDED by construction).
    #
    # The current is the IDEAL PROGRAMMED pattern current (``ideal_current_ua``),
    # NOT the measured I_mon (operator: "driving energy should be calculated
    # with the ideal current pattern instead of the current monitor because the
    # spikes/swings in current is not always representative of the true pattern
    # — there [are] even cases of asynchronous starts at small pulse widths").
    # I_mon carries switching spikes + a variable turn-on skew at small pulse
    # widths that misrepresent the delivered current; the current-source
    # stimulator actually delivers the programmed waveform, so the ideal
    # current is the faithful factor for the power integral.
    try:
        _i_stim_ua = (abs(float(pat.excitation_phase.amplitude_ua))
                      if pat.excitation_phase is not None else 0.0)
        if _i_stim_ua > 1e-9 and np.isfinite(m.driving_voltage_v):
            # V / (µA·1e-6) = Ω; /1e3 = kΩ.
            m.driving_impedance_kohm = (
                abs(m.driving_voltage_v) / (_i_stim_ua * 1e-6) / 1e3)
    except Exception:
        m.driving_impedance_kohm = float("nan")

    try:
        if capture.v_mon_v is not None and capture.v_mon_v.size == capture.time_us.size:
            _pw = phase_windows(capture.time_us, pat, onset_us=onset_us)
            if _pw:
                _s = max(0, int(_pw[0].start_idx))
                _e = min(int(capture.time_us.size), int(_pw[-1].end_idx))
                if _e - _s >= 2:
                    # IDEAL programmed current on the capture's time axis
                    # (anchored at the detected onset) — zero in the passive
                    # gaps, no I_mon spikes / turn-on skew.
                    _i_ideal = ideal_current_ua(
                        capture.time_us, pat, onset_us=onset_us)
                    _t_s = capture.time_us[_s:_e] * 1e-6          # µs → s
                    _v = capture.v_mon_v[_s:_e]                   # V
                    _i_a = _i_ideal[_s:_e] * 1e-6                 # µA → A
                    # np.trapz was REMOVED in numpy 2.x (→ np.trapezoid);
                    # pick whichever exists so this works on the test env
                    # (numpy 2.4) AND the bundled runtime.
                    _trapz = getattr(np, "trapezoid", None) or np.trapz
                    _energy_j = float(_trapz(_v * _i_a, _t_s))    # ∫ P dt = J
                    m.driving_energy_uj = _energy_j * 1e6         # J → µJ
    except Exception:
        m.driving_energy_uj = float("nan")

    # ----- 8. Chronopotentiometry capacitive/Faradaic decomposition (Harris
    # 2019, Front. Neurosci. 13:380) — C_dl (double-layer capacitance from the
    # constant-dE/dt window), Faradaic onset, and the (APPROXIMATE) capacitive/
    # Faradaic charge split of the EXCITATION phase.  ONLY for a FUNCTIONAL
    # (normal) electrode driven by a RECTANGULAR (constant-current) pulse with a
    # known area — the ``i_c = A·C_dl·dE/dt`` model needs constant current, and a
    # bad electrode is already characterized by its open/broken + R‖C.  Runs on
    # ``active_trace`` (E_act when recorded, else V_mon), same as the other fits.
    try:
        _all_rect = all(getattr(ph, "shape", SHAPE_RECTANGULAR) == SHAPE_RECTANGULAR
                        for ph in pat.phases)
        if (m.response_class == "normal" and _all_rect and pat.phases
                and surface_area_um2 and surface_area_um2 > 0):
            _exc_idx = max(range(len(pat.phases)),
                           key=lambda i: abs(float(pat.phases[i].amplitude_ua)))
            _ct = chronopotentiometry_charge_transfer(
                capture.time_us, active_trace, pat, onset_us=onset_us,
                area_um2=surface_area_um2, phase_idx=_exc_idx)
            m.c_dl_mf_per_cm2 = _ct.c_dl_mf_per_cm2
            m.faradaic_onset_us = _ct.faradaic_onset_us
            m.faradaic_onset_v = _ct.faradaic_onset_v
            m.capacitive_charge_nc = _ct.q_capacitive_nc
            m.faradaic_charge_nc = _ct.q_faradaic_nc
            m.faradaic_fraction = _ct.faradaic_fraction
    except Exception:
        pass

    capture.metrics = m
    return m


# ---------------------------------------------------------------------------
# Capacitive-to-faradaic transition (used by progressive-stress experiment)
# ---------------------------------------------------------------------------
def detect_capacitive_to_faradaic(
    time_s: np.ndarray, current_a: np.ndarray,
) -> bool:
    """True if the current trace shows the capacitive-to-faradaic transition
    described in Nguyen et al., JNE 22 (2025) 066040.

    .. note::
       This detector is for *encapsulation* (a-SiC dielectric over IDE
       traces) progressive-voltage breakdown, **not** for stimulation
       electrode failure. It is intentionally not used by
       :class:`ProgressiveStressExperiment`. Kept here for users who want to
       run the JNE-style insulation-aging analysis on dedicated test
       structures.

    Detection criteria (both must hold):
      * tail current magnitude > head current magnitude (rising trend); and
      * second-half derivative > first-half derivative (accelerating).
    """
    if current_a.size < 5:
        return False
    n = current_a.size
    half = n // 2
    final = float(np.mean(current_a[-max(5, n // 10):]))
    initial = float(np.mean(current_a[:max(5, n // 10)]))
    rising = abs(final) > abs(initial)
    derivative = np.gradient(current_a)
    accelerating = float(np.mean(derivative[half:])) > float(np.mean(derivative[:half]))
    return rising and accelerating


# ---------------------------------------------------------------------------
# Access-resistance DRIFT test (operator: flag a broken / degrading electrode
# when the PRESENT access resistance differs significantly from the PREVIOUS
# ones in the run).  Nonparametric Mann-Whitney U — chosen by the operator
# ("Mann-Whitney").  R_access is the ohmic electrolyte / spreading resistance,
# so for a HEALTHY sample it stays ~constant across amplitude steps / over
# time; a significant, sustained change is a real physical event (delamination
# raising series R, corrosion, or the access step vanishing = open).  Compare
# the RESISTANCE (R_a), NOT the access VOLTAGE (V_a = I·R_a scales with the
# programmed current, so it can't be compared across a ramp).
# ---------------------------------------------------------------------------
def representative_access_resistance_kohm(capture) -> float:
    """The ONE access-resistance scalar per capture used for drift testing:
    the LEADING (phase-1) access resistance R_a1 — the cleanest ohmic step.

    Returns NaN when no access resistance was measured (a 0 µA capture, a
    smooth shaped phase with no current-step edge, or an open/broken capture
    whose ``access_*`` were cleared by the classifier)."""
    m = getattr(capture, "metrics", None)
    if m is None:
        return float("nan")
    for r in (getattr(m, "access_resistance_per_phase_kohm", None) or ()):
        if r is not None and np.isfinite(r):
            return float(r)
    return float("nan")


@dataclass
class DriftTestResult:
    """Outcome of :func:`access_resistance_drift_mannwhitney`."""
    flagged: bool
    p_value: float
    median_present_kohm: float
    median_reference_kohm: float
    n_present: int
    n_reference: int


def access_resistance_drift_mannwhitney(
        reference_kohm, present_kohm, *,
        alpha: float = 0.01, min_reference: int = 6, min_present: int = 3,
        magnitude_frac: float = 0.25) -> "DriftTestResult":
    """Two-sided Mann-Whitney U test: does the PRESENT access-resistance sample
    differ from the REFERENCE history?

    Nonparametric + median-based (robust to the noisy IR-step R_a and to a few
    contaminated captures — no normality assumption).  Non-finite values are
    dropped; the CALLER must already have excluded 0 µA captures (R_a is
    undefined at zero current).

    ``flagged`` is True only when BOTH:
      * ``p < alpha`` (default 0.01), AND
      * the present median shifts from the reference median by more than
        ``magnitude_frac`` (default 25 %).
    The magnitude gate matters because with enough averaging a physically-
    trivial R_a wiggle becomes statistically 'significant' — we want to flag
    delamination / corrosion, not thermal drift.  Returns a NON-flagged result
    (``p_value = NaN``) when either sample is below its minimum size, or when
    SciPy is unavailable (graceful degradation — never raise into a run)."""
    ref = np.asarray([r for r in reference_kohm
                      if r is not None and np.isfinite(r)], dtype=float)
    pres = np.asarray([r for r in present_kohm
                       if r is not None and np.isfinite(r)], dtype=float)
    med_r = float(np.median(ref)) if ref.size else float("nan")
    med_p = float(np.median(pres)) if pres.size else float("nan")
    if ref.size < int(min_reference) or pres.size < int(min_present):
        return DriftTestResult(False, float("nan"), med_p, med_r,
                               int(pres.size), int(ref.size))
    try:
        from scipy.stats import mannwhitneyu
        _, p = mannwhitneyu(pres, ref, alternative="two-sided")
        p = float(p)
    except Exception:
        return DriftTestResult(False, float("nan"), med_p, med_r,
                               int(pres.size), int(ref.size))
    mag = abs(med_p - med_r) / med_r if med_r > 0 else float("inf")
    flagged = bool(p < float(alpha) and mag > float(magnitude_frac))
    return DriftTestResult(flagged, p, med_p, med_r,
                           int(pres.size), int(ref.size))
