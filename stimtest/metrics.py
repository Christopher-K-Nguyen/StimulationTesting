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
from .waveforms import Phase, PulsePattern


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
                    edge_zero: int = 30) -> np.ndarray:
    """|dV/dt| with the canonical pre-/post-smoothing and edge clamping
    used by ``getAccess.m``."""
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
                  start_thresh: float, max_seconds: float = 2.0) -> np.ndarray:
    """Adaptive peak finder that returns exactly ``n_target`` peaks.

    Mirrors the while-loop in ``getAccess.m``: start at 0.9 × peak_max, decrease
    threshold by 10%% if too few peaks, re-smooth and restart if too many.
    """
    import time as _time
    deadline = _time.time() + max_seconds
    thresh = start_thresh
    deriv = deriv_abs.copy()
    while _time.time() < deadline:
        peaks, _info = find_peaks(deriv, height=thresh)
        if peaks.size == n_target:
            return peaks
        if peaks.size > n_target:
            # too many: smooth more and reset
            deriv = _smooth(deriv, 5)
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

    # --- 1. Pulse anatomy ---------------------------------------------------
    has_iph = any(p.delay_after_us > 0 for p in pattern.phases[:-1])
    has_dd = pattern.phases[-1].delay_after_us > 0
    polarity = pattern.polarity

    # Pre-pulse baseline (samples where time < 0)
    pre_mask = time_us < 0
    pre_voltage = float(np.mean(v_trace[pre_mask])) if pre_mask.any() else float(v_trace[0])

    # --- 2. Smoothed |dV/dt| ------------------------------------------------
    deriv_abs, deriv_signed = _abs_derivative(time_us, v_trace)
    peak_max = float(np.max(deriv_abs)) if deriv_abs.size else 0.0
    if peak_max <= 0:
        return [], [], []

    # --- 3. Expected peak count (matches getAccess.m logic) ----------------
    # 3 base peaks for a biphasic pulse + 1 per delay
    n_phases = pattern.num_phases
    n_peaks_expected = (n_phases + 1) + (1 if has_iph else 0) + (1 if has_dd else 0)
    n_access_expected = 1
    if has_iph:
        n_access_expected += 2 * (n_phases - 1)
    if has_dd:
        n_access_expected += 1

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
    v_filt = _smooth(np.asarray(v_trace, dtype=float))
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


def access_indices_for_phase(*args, **kwargs):
    """Backward-compat shim for older callers. The canonical algorithm now
    operates on the whole pulse via :func:`access_voltage_and_resistance`."""
    raise NotImplementedError(
        "Use access_voltage_and_resistance() — the canonical getAccess.m port "
        "operates on the entire pulse, not one phase at a time."
    )


# ---------------------------------------------------------------------------
# Polarization (E_pol)
# ---------------------------------------------------------------------------
def access_index_labels(pattern: PulsePattern) -> List[Tuple[int, str]]:
    """Per-entry ``(phase_idx, role)`` labels for the ``access_idx`` array
    returned by :func:`access_voltage_and_resistance`.

    The ordering is fixed by the analysis algorithm:

      * entry 0 is always the leading edge of phase 1;
      * for each interphase delay between phase k and k+1 we get
        ``(k, 'trail')`` immediately followed by ``(k+1, 'lead')``;
      * for a discharge delay after the last phase we get ``(last, 'trail')``.

    The result is a parallel list of the same length as ``access_idx`` so the
    caller can ``zip(access_idx, labels)`` and dispatch by role.
    """
    n = pattern.num_phases
    has_iph_after = [ph.delay_after_us > 0 for ph in pattern.phases[:-1]]
    has_dd = pattern.phases[-1].delay_after_us > 0

    labels: List[Tuple[int, str]] = [(0, "lead")]
    if n == 1:
        if has_dd:
            labels.append((0, "trail"))
        return labels
    for k in range(n - 1):
        if has_iph_after[k]:
            labels.append((k, "trail"))
            labels.append((k + 1, "lead"))
    if has_dd:
        labels.append((n - 1, "trail"))
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

    # ----- 5. Capacitance metrics ---------------------------------------
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
