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
    SHAPE_EXP_DECAY,
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
    return (1.0, 1.0)   # unknown shapes default to rectangular-like


# ---------------------------------------------------------------------------
# Phase boundary helpers
# ---------------------------------------------------------------------------
@dataclass
class PhaseWindow:
    """Index range and edge times for one phase within a capture."""
    phase_index: int           # 0-based (1st phase = 0)
    start_us: float            # leading edge time (relative to t=0 = stim start)
    end_us: float              # trailing edge time
    start_idx: int             # nearest-sample index in time_us
    end_idx: int


def phase_windows(time_us: np.ndarray, pattern: PulsePattern) -> List[PhaseWindow]:
    """Return one PhaseWindow per phase in ``pattern``."""
    out: List[PhaseWindow] = []
    cursor = 0.0
    for k, ph in enumerate(pattern.phases):
        s = cursor
        e = cursor + ph.width_us
        s_idx = int(np.searchsorted(time_us, s, side="left"))
        e_idx = int(np.searchsorted(time_us, e, side="left"))
        out.append(PhaseWindow(k, s, e, s_idx, e_idx))
        cursor += ph.width_us + ph.delay_after_us
    return out


# ---------------------------------------------------------------------------
# Driving voltage (V_d)
# ---------------------------------------------------------------------------
def driving_voltage_from_vmon(v_mon: np.ndarray) -> float:
    """V_d = max |V_mon| over the whole pulse (per IEEE NER Fig. 3b)."""
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

    pre_mask = time_us < 0
    pre_voltage = (float(np.mean(trace_v[pre_mask])) if pre_mask.any()
                   else float(trace_v[0]))

    out: List[float] = []
    cursor = 0.0
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
                          pattern: PulsePattern) -> float:
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
    pre_mask  = time_us < 0
    post_mask = time_us >= float(pattern.total_pulse_us)
    rest_mask = pre_mask | post_mask
    if not rest_mask.any():
        return float("nan")
    return float(np.mean(trace_v[rest_mask]))


def _interpulse_potential_split(
    time_us: np.ndarray, trace_v: np.ndarray, pattern: PulsePattern,
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
    pre_mask = time_us < 0
    post_mask = time_us >= float(pattern.total_pulse_us)
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
                           max_seconds: float = 0.5) -> int:
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


def access_voltage_and_resistance(
    time_us: np.ndarray, v_trace: np.ndarray, pattern: PulsePattern,
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
    """
    if v_trace.size < 20:
        return [], [], []

    # --- 1. Shape-aware boundary list --------------------------------------
    # Build the expected (phase, role) labels using per-shape leading /
    # trailing edge factors; this gives one entry per boundary that has
    # a clean current-step (rect / linear / bowtie / halfpipe / exp-
    # decay leading) and OMITS boundaries where the current ramps from
    # or to zero (sinusoidal both edges, linear-increasing leading,
    # linear-decreasing trailing, exp-decay trailing). The output
    # arrays have the same length as this label list, so the caller
    # can ``zip`` to dispatch by role.
    labels = access_index_labels(pattern)
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

    # Pre-pulse baseline (samples where time < 0)
    pre_mask = time_us < 0
    pre_voltage = float(np.mean(v_trace[pre_mask])) if pre_mask.any() else float(v_trace[0])

    # --- 2. Smoothed |dV/dt| ------------------------------------------------
    # Compute the smoothed V_mon trace ONCE up front and pass it to
    # both `_abs_derivative` (which would otherwise smooth again
    # internally) AND step 5's driving-extrema lookup.  Saves one
    # `uniform_filter1d` call per capture; ~0.5-1 ms on a 20 k
    # sample trace.
    v_arr_f = np.asarray(v_trace, dtype=float)
    v_smooth = _smooth(v_arr_f)
    deriv_abs, deriv_signed = _abs_derivative(
        time_us, v_arr_f, v_smooth_precomputed=v_smooth)
    peak_max = float(np.max(deriv_abs)) if deriv_abs.size else 0.0
    if peak_max <= 0:
        return [], [], []

    # --- 3. Expected peak count -------------------------------------------
    # One |dV/dt| peak per boundary in the label list. The previous
    # logic counted purely from delays / phase count; the new label
    # list already encodes shape-aware filtering.
    n_peaks_expected = n_access_expected

    peaks = _find_n_peaks(deriv_abs, n_peaks_expected, peak_max * 0.9)
    if peaks.size == 0:
        return ([float("nan")] * n_access_expected,
                [float("nan")] * n_access_expected,
                [-1] * n_access_expected)

    # --- 4. Localize each access index by linear-fit deviation -------------
    access_idx: List[int] = []
    for i in range(min(n_access_expected, peaks.size)):
        access_idx.append(_localize_access_point(deriv_abs, int(peaks[i])))

    # Pad if we're short (best-effort fallback)
    while len(access_idx) < n_access_expected:
        access_idx.append(-1)

    # --- 5. Driving voltage extrema (used as references for trailing V_a) --
    # Re-use the smoothed trace computed in step 2 — same window=10
    # so the result is identical, just without paying for the smooth
    # twice.
    v_filt = v_smooth
    if polarity == -1:
        driving1_idx = int(np.argmin(v_filt))
        driving2_idx = int(np.argmax(v_filt))
    else:
        driving1_idx = int(np.argmax(v_filt))
        driving2_idx = int(np.argmin(v_filt))

    # --- 6. Compute V_a per access point -----------------------------------
    va: List[float] = []
    amp_for: List[float] = []  # corresponding |I_amp| for R_a calc
    a1 = abs(pattern.phases[0].amplitude_ua)
    a2 = abs(pattern.phases[1].amplitude_ua) if pattern.num_phases >= 2 else a1

    # 6a) Leading phase 1
    i1 = access_idx[0]
    v_a1 = (v_trace[i1] - pre_voltage) if 0 <= i1 < v_trace.size else float("nan")
    va.append(float(v_a1))
    amp_for.append(a1)

    # 6b) Trailing phase 1 + leading phase 2 (only if interphase delay)
    if has_iph and len(access_idx) >= 3:
        i2 = access_idx[1]
        v_a2 = (v_trace[i2] - v_filt[driving1_idx]) if 0 <= i2 < v_trace.size else float("nan")
        va.append(float(v_a2)); amp_for.append(a1)

        i3 = access_idx[2]
        # End-of-interphase = min |dV/dt| sample inside the interphase window
        if 0 <= i2 < i3 < deriv_abs.size:
            seg = deriv_abs[i2:i3]
            end_iph_local = int(np.argmin(seg))
            end_iph_idx = i2 + end_iph_local
            v_end_iph = float(v_trace[min(end_iph_idx + 1, v_trace.size - 1)])
        else:
            v_end_iph = float("nan")
        v_a3 = (v_end_iph - v_trace[i3]) if 0 <= i3 < v_trace.size else float("nan")
        va.append(float(v_a3)); amp_for.append(a2)

    # 6c) Trailing phase 2 (only if discharge delay)
    if has_dd and len(access_idx) >= n_access_expected:
        i4 = access_idx[-1]
        v_a4 = (v_filt[driving2_idx] - v_trace[i4]) if 0 <= i4 < v_trace.size else float("nan")
        va.append(float(v_a4)); amp_for.append(a2)

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
def access_index_labels(pattern: PulsePattern) -> List[Tuple[int, str]]:
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
    """
    n = pattern.num_phases
    if n == 0:
        return []
    peak_amp = max((abs(p.amplitude_ua) for p in pattern.phases), default=1.0)
    if peak_amp <= 0:
        peak_amp = 1.0
    cutoff = _STEP_FACTOR_THRESHOLD * peak_amp

    labels: List[Tuple[int, str]] = []
    # Phase 1 leading edge — pre-pulse (0) → phase1 leading-factor × peak.
    f_lead0, _ = _phase_step_factors(pattern.phases[0].shape)
    if abs(pattern.phases[0].amplitude_ua) * f_lead0 >= cutoff:
        labels.append((0, "lead"))

    for k in range(n):
        ph = pattern.phases[k]
        _, f_trail_k = _phase_step_factors(ph.shape)
        is_last = (k == n - 1)
        delay = ph.delay_after_us

        if delay > 0:
            # Trailing of phase k: amp drops to 0 over the delay.
            if abs(ph.amplitude_ua) * f_trail_k >= cutoff:
                labels.append((k, "trail"))
            # Leading of phase k+1 (if any): amp rises from 0.
            if not is_last:
                next_ph = pattern.phases[k + 1]
                f_lead_next, _ = _phase_step_factors(next_ph.shape)
                if abs(next_ph.amplitude_ua) * f_lead_next >= cutoff:
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
            if net_step >= cutoff:
                labels.append((k + 1, "lead"))
    return labels


def polarization_per_phase(
    time_us: np.ndarray, e_trace: np.ndarray, pattern: PulsePattern,
    *, depol_us: float = DEPOLARIZATION_TIME_US,
    method: str = "auto",
    access_idx: Optional[List[int]] = None,
) -> List[float]:
    """E_pol per phase. Three methods:

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
    if method not in ("time", "derivative", "auto"):
        raise ValueError(f"Unknown method {method!r}")

    # ----- time-based fallback (always computable) ------------------------
    def _time_sample(phase_idx: int) -> float:
        """Sample ``e_trace`` at the end of phase ``phase_idx`` plus depol_us."""
        cursor = 0.0
        for k, ph in enumerate(pattern.phases):
            cursor += ph.width_us
            if k == phase_idx:
                t_sample = cursor + depol_us
                idx = int(np.searchsorted(time_us, t_sample, side="left"))
                idx = int(np.clip(idx, 0, e_trace.size - 1))
                return float(e_trace[idx])
            cursor += ph.delay_after_us
        return float("nan")

    if method == "time":
        return [_time_sample(k) for k in range(n)]

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
# Effective per-phase capacitance from linear dV/dt
# ---------------------------------------------------------------------------
def effective_capacitance_nf(
    time_us: np.ndarray, v_trace: np.ndarray, pattern: PulsePattern,
    phase_index: int = 0, plateau_fraction: float = 0.7,
) -> float:
    """C = I / |dV/dt| over the central plateau of the chosen phase, in nF."""
    if not (0 <= phase_index < pattern.num_phases):
        return float("nan")
    windows = phase_windows(time_us, pattern)
    w = windows[phase_index]
    n = w.end_idx - w.start_idx
    if n < 6:
        return float("nan")
    margin = int(((1 - plateau_fraction) / 2) * n)
    s = w.start_idx + margin
    e = w.end_idx - margin
    if e - s < 4:
        return float("nan")
    t = time_us[s:e] * 1e-6                              # s
    v = v_trace[s:e]
    slope, _ = np.polyfit(t, v, 1)
    if slope == 0:
        return float("nan")
    i_a = abs(pattern.phases[phase_index].amplitude_ua) * 1e-6
    cap_f = i_a / abs(slope)
    return cap_f * 1e9                                   # F -> nF


# ---------------------------------------------------------------------------
# Top-level: compute everything for a Capture
# ---------------------------------------------------------------------------
def compute_metrics(capture: Capture, surface_area_um2: float,
                    *, polarization_source: str = "auto") -> CaptureMetrics:
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

    # ----- 2. Driving voltage V_d -----------------------------------------
    # V_mon = E_act - E_ret straight off the stimulator's monitor output.
    # Always available; we use its abs-max as the baseline V_d estimate.
    v_d_vmon = driving_voltage_from_vmon(capture.v_mon_v)

    # If the instrumentation amplifier is wired up we get the active and
    # return potentials separately, which gives a slightly cleaner V_d
    # measurement (no scope-input attenuation effects). Take whichever is
    # larger to be conservative — V_d quoted in the paper is the *actual*
    # max of |E_act - E_ret|.
    e_act = capture.e_act_v
    e_ret = capture.e_ret_v
    has_potentials = (e_act is not None and e_ret is not None
                      and e_act.size == e_ret.size == capture.time_us.size)
    if has_potentials and polarization_source != "vmon":
        v_d = driving_voltage_from_potentials(e_act, e_ret)
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
    va_list, ra_list, access_idx = access_voltage_and_resistance(
        capture.time_us, capture.v_mon_v, pat,
    )
    m.access_voltage_per_phase_v = list(va_list)
    m.access_resistance_per_phase_kohm = list(ra_list)

    # Return-side access V_a / R_a — same algorithm but reads off the
    # E_ret trace so the user gets a separate measurement for the
    # counter / return path. Skipped if E_ret isn't recorded; if it is,
    # the return access list is parallel (same length, same access_idx
    # ordering) to the active-side ``access_voltage_per_phase_v``.
    if e_ret is not None and e_ret.size == capture.time_us.size:
        va_ret, ra_ret, _ = access_voltage_and_resistance(
            capture.time_us, e_ret, pat,
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
    if has_potentials:
        m.active_driving_voltage_per_phase_v = _driving_voltage_per_phase(
            capture.time_us, e_act, pat,
        )
        m.return_driving_voltage_per_phase_v = _driving_voltage_per_phase(
            capture.time_us, e_ret, pat,
        )
    else:
        # Fallback to V_mon for the active side; return side stays
        # empty since we don't have a separate E_ret trace.
        m.active_driving_voltage_per_phase_v = _driving_voltage_per_phase(
            capture.time_us, capture.v_mon_v, pat,
        )
        m.return_driving_voltage_per_phase_v = []

    # ----- 4. Electrode polarization (E_pol) ----------------------------
    # E_pol per phase = ``driving_potential − trailing_access_voltage``,
    # which equals the value of the trace at the trailing-access plateau
    # index found by access_voltage_and_resistance. The 12 µs after-phase
    # sample is used only as a fallback for phases that don't have a
    # trailing access point (no interphase or discharge delay) or when
    # the access detector failed for that phase.
    #
    # With the instrumentation amplifier we get both active and return
    # E_pol from the corresponding traces; without it we fall back to
    # V_mon as a proxy and skip the return entries.
    if has_potentials:
        m.polarization_per_phase_v = polarization_per_phase(
            capture.time_us, e_act, pat,
            method="auto", access_idx=access_idx,
        )
        m.return_polarization_per_phase_v = polarization_per_phase(
            capture.time_us, e_ret, pat,
            method="auto", access_idx=access_idx,
        )
    else:
        # Fall back to using V_mon as a proxy (less accurate)
        m.polarization_per_phase_v = polarization_per_phase(
            capture.time_us, capture.v_mon_v, pat,
            method="auto", access_idx=access_idx,
        )
        m.return_polarization_per_phase_v = []

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
        capture.time_us, _eip_trace, pat,
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
            capture.time_us, _eip_trace, pat,
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
    # Effective per-phase capacitance: linear fit of V over the *plateau*
    # of the first phase (skipping the leading/trailing 15% to avoid
    # the access drops), then C = I / |dV/dt|. Tells us how much
    # double-layer capacitance the active electrode is actually using.
    m.effective_capacitance_nf = effective_capacitance_nf(
        capture.time_us, capture.v_mon_v, pat, phase_index=0,
    )
    # Driving capacitance C_d = max(Q_inj) / V_d (mF/cm²). This is the
    # whole-system efficiency metric from IEEE NER paper: how much charge
    # we got per volt across the entire return-active circuit.
    m.driving_capacitance_mf_per_cm2 = driving_capacitance(q_inj, m.driving_voltage_v)

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
