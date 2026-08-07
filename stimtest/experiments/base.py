"""Common runner infrastructure for experiments.

Every experiment is a subclass of :class:`ExperimentRunner` that implements a
single :meth:`run` method. The runner is purely synchronous (it can sleep,
poll the scope, etc.) and emits :class:`ExperimentEvent` objects to any
subscriber as it goes. This decouples experiment logic from how it's
*driven*:

* The GUI (:mod:`stimtest.gui.experiment_tabs`) wraps a runner in a
  :class:`RunnerWorker` running on a QThread, and converts events into Qt
  signals so the live plot / metrics table update in real time.
* The CLI (:mod:`run_cli`) just calls ``runner.run()`` directly and ignores
  events.
* Unit tests can subscribe a list-collector and assert on what got emitted.

Event kinds (string in ``ExperimentEvent.kind``):
  ``run_start``    - starting a new ChannelRun
  ``capture``      - one Capture finished (always carries .capture)
  ``run_end``      - ChannelRun complete
  ``session_end``  - whole experiment complete
  ``log``          - free-form log message in .message
  ``aborted``      - error / user abort

Aborting
--------
GUI calls :meth:`abort` from the main thread. The runner checks
``self.aborted`` at every safe point (between captures, between
configurations) and unwinds cleanly, stopping the stimulator first for
safety.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from typing import Callable, List, Optional

from ..hardware.base import Oscilloscope, Stimulator
from ..readback_calibration import (
    ReadbackCalibration, load_calibration, make_capture, per_capture_baseline)
from ..session import Capture, ChannelRun, Session


ProgressCallback = Callable[["ExperimentEvent"], None]


#: I_mon trigger-level scaling constant from MATLAB ``setTriggerLevel.m``
#: (``currentMonScale_V_uA = 1e-3``).  Heuristic, NOT the device's actual
#: imon scaling — real PlexStim 2.0 hardware delivers 2.5 mV/µA (Default)
#: or 1.0 mV/µA (NIL), so the computed threshold lands well below the I_mon
#: peak in either case.
_IMON_TRIG_SCALING_V_PER_UA: float = 1e-3

#: Percentile trimmed off EACH extreme when sizing the vertical scale
#: from a captured array (slow mode) in :meth:`ExperimentRunner.
#: rescale_to_fit`.  Drops the briefest ~1 % of samples at each end so
#: the V/div sizes for the SETTLED signal and the sub-µs compliance-
#: switching transients (the ±1.5 V spikes at the phase boundaries) clip
#: slightly instead of blowing up the scale — operator-spec "size 1 µs
#: before the max/min, not the switching spike".  1.0 % ≈ 200 samples /
#: 6.4 µs of a 20k-point / 640 µs record — comfortably more than the few
#: brief (1-2 µs) switching transients but far below the settled-plateau
#: population.  The stale-frame reject below is the backstop for
#: anything that still slips through.
_RESCALE_TRIM_PCT = 1.0

#: A trimmed sizing range whose magnitude still exceeds this factor ×
#: the channel's on-screen half-window is physically impossible at the
#: current V/div — a stale frame from a coarser scale read before the
#: rescale settled.  1.6× clears real ADC over-range (~1.28×) but
#: catches a one-grid-step-stale frame (≥ 2× the new rail).
_RESCALE_STALE_FACTOR = 1.6

#: Fill-only re-capture skip threshold.  A fill-only V/div change (in-view,
#: not-clipped) is applied WITHOUT a verify re-capture ONLY when it's MINOR —
#: the new scale is within [ratio, 1/ratio] of the scale the current capture was
#: taken at.  A SIGNIFICANT rescale (the seed/grow left the trace badly under-
#: or over-filled and adapt fine-fits it, e.g. a small signal parked at
#: 14 mV/div → 3 mV/div = 0.21×) MUST re-capture, otherwise the SAVED capture
#: stays at the coarse pre-write scale and V_mon renders TINY / "not scaled"
#: (operator regression).  0.7 ⇒ skip a change within ±~30-43 % (a couple of
#: fine-grid steps — "slightly coarser", which the operator accepted for the
#: speed win); re-capture anything larger.
_RESCALE_FILL_SKIP_RATIO = 0.7


#: Log-display names for the internal role KEYS (operator: "for the log pane
#: text, use Vmon, Imon, Eret, Eact instead of all lowercase").  The keys stay
#: lowercase everywhere in CODE (dict keys, comparisons, aliases); this maps
#: them to the operator-facing casing for LOG lines only.
_ROLE_DISPLAY = {"vmon": "Vmon", "imon": "Imon", "eret": "Eret", "eact": "Eact"}


def _role_disp(role) -> str:
    """The log-display name (``Vmon`` …) for an internal role key (``vmon`` …).
    Unknown keys pass through unchanged.  All four names are 4 chars, so a
    ``:>4s`` field aligns identically to the old lowercase keys."""
    return _ROLE_DISPLAY.get(str(role).lower(), str(role))

#: Bias-ratio threshold (|rest potential| / swing) above which an
#: electrode-potential channel (E_ret / E_act) is DC-DOMINATED and gets
#: the DC→AC-couple-with-offset-sum treatment.  Operator: "the summation
#: of data from DC coupled and AC coupled should be done for small
#: magnitude waveforms like Eret in monopolar … waveforms that require
#: fine scaling and positioning."  A small swing sitting on a large DC
#: bias (monopolar E_ret: +248 mV rest, ±8 mV swing → R≈31) can't be
#: fine-scaled while DC-coupled (the ±5-div POSition limit forces a coarse
#: V/div, gotcha #13), so we measure the rest potential, AC-couple, and
#: sum the offset back.  A large / near-zero-biased swing (R ≤ 1 — the
#: AC-centred / moderate-bias regimes of gotcha #13: a polarised E_act, a
#: multipolar return) fine-scales fine while DC-coupled and keeps its true
#: absolute level, so it stays DC.  1.0 = the gotcha-#13 DC-dominated edge.
_DC_TO_AC_BIAS_RATIO = 1.0

#: Settle time (seconds) to wait AFTER a DC→AC coupling switch on a
#: DC-biased electrode channel (E_ret / E_act) before the next averaged
#: capture.  Switching DC→AC leaves the input coupling capacitor charged to
#: the OLD DC rest level; it bleeds off through the AC high-pass (~10 Hz
#: corner → RC ≈ 16 ms) over several time constants.  Capturing during that
#: transient blends a decaying baseline into the free-running average → a
#: shifted / noisy E_ret (operator: "a lot of noise or weird shifts from
#: Eret … changing from DC to AC coupled").  0.5 s ≈ 30 RC at a 10 Hz corner
#: (or ~3 RC even at a conservative 1 Hz corner), paid ONCE per channel.
#: Bump this if a lower-corner scope still shows a first-capture shift.
#: Now used as an initial head-start before the interpulse-verify loop
#: below (the loop is the real acceptance gate).
_AC_COUPLING_SETTLE_S = 0.5

#: DC→AC ACCEPTANCE gate (operator: "for AC+DC, we need to recapture until the
#: interpulse before and after the pulse both have SD < 5 mV").  Once
#: AC-coupled, the true steady state is the DC offset FULLY removed → the
#: interpulse (flat baseline BETWEEN pulses, both LEADING and TRAILING the
#: pulse) is FLAT.  While the coupling cap is still discharging, that baseline
#: DRIFTS (a ramp decaying from the old rest level), which shows up as a HIGH
#: standard deviation over the interpulse region.  So instead of trusting a
#: fixed settle time, re-capture and ACCEPT only once the SD of BOTH interpulse
#: regions ≤ this threshold — a self-calibrating check that adapts to any RC /
#: DC level and confirms the averager has flushed the settling frames.  (DC-ONLY
#: mode SKIPS this entirely — the helper isn't called; E_ret is stable
#: DC-coupled from the first frame.)
_AC_INTERPULSE_SD_V = 0.005           # 5 mV — SD of a settled (flat) interpulse
#: LENIENT interpulse-SD threshold used when the pulse amplitude is ~0 µA
#: (operator #5: "be more lenient on AC+DC waveforms at 0 µA").  At 0 µA there
#: is no pulse — the whole trace is the noise floor, so the strict 5 mV
#: flatness accept can't reliably pass on a noisy baseline and there is no
#: signal to preserve; a 3× looser accept lets the DC→AC offset measurement
#: proceed instead of exhausting the retries + force-accepting.
_AC_INTERPULSE_SD_LENIENT_V = 0.015   # 15 mV
#: Bounded re-captures while waiting for the AC interpulse SD to settle.
#: Each is a full averaged capture; typically 1-2 suffice.  On timeout the
#: last capture is accepted anyway (with a ⚠ log) so a run never stalls.
_AC_SETTLE_MAX_CHECKS = 4


#: Amplitude (µA) at or below which the SMALL-AMPLITUDE trigger formula
#: applies (operator: "change the threshold for low amplitude trigger
#: adjustment to 25 µA and below, not 20 µA").  Above it the threshold is a
#: FRACTION of amplitude, which shrinks with the signal and eventually sinks
#: into the constant I_mon noise floor — at 25 µA the old ``× 0.25`` rule gave
#: 6.25 mV, indistinguishable from noise, and the scope stopped triggering.
_IMON_TRIG_LOW_AMP_UA: float = 25.0

#: Hard ceiling on the trigger threshold as a FRACTION of the expected I_mon
#: peak.  A threshold above the peak is uncrossable, so the scope never fires.
_IMON_TRIG_PEAK_FRAC_MAX: float = 0.60


def imon_trigger_level(amp_ua_signed: float,
                       phase_width_us: float = 200.0,
                       imon_v_per_ua: Optional[float] = None) -> float:
    """Return the I_mon trigger threshold (signed, in volts).

    Exact port of MATLAB ``setTriggerLevel.m``:

    .. code:: matlab

        currentMonScale_V_uA = 1e-3;
        if amplitude1_mag <= 20
            triggerLevel = (amplitude1_mag + 4.5) * sign * 1e-3;
        else
            triggerLevel = amplitude * 1e-3 * scale;
        end

    At 10 µA the level is **14.5 mV** — slightly more than the
    amplitude in mV.  This works because MATLAB's pipeline sets a
    bandwidth-limit filter on the I_mon channel (200 MHz on the
    TBS-series, not Full), which cuts the broadband noise far enough
    that the clean I_mon peak comfortably exceeds 14.5 mV even on
    a NIL device.  Without the BW limit the signal looks noisier
    AND has its peak rolled off, so the trigger never fires — that
    was the failure mode we hit earlier.  The fix is on the
    *bandwidth* axis, not on the trigger formula.

    ``imon_v_per_ua`` IS used: when the caller supplies the actual
    scaling the threshold is capped at ``_IMON_TRIG_PEAK_FRAC_MAX`` of the
    expected peak, so it can never exceed the signal it must cross.  The
    MATLAB-exact value is preserved wherever it already fits (a Default-preset
    device); the cap only rescues the NIL case, where the unmodified formula
    lands ABOVE the peak and the scope never triggers.
    """
    SCALING = _IMON_TRIG_SCALING_V_PER_UA
    amp_mag = abs(float(amp_ua_signed))
    # ``copysign`` (not ``amp < 0``) so a SIGNED ZERO keeps its polarity:
    # a 0 µA-start cathodal-first ramp's phase-1 amplitude is ``-0.0``
    # (gotcha #56), and ``-0.0 < 0`` is False → a positive level, inconsistent
    # with the cathodal-first FALL trigger slope.  Identical to the old test
    # for every non-zero amplitude; only the ``±0.0`` baseline changes.
    amp_sign = math.copysign(1.0, float(amp_ua_signed))
    scale = 0.40 if phase_width_us >= 100.0 else 0.25
    if amp_mag <= _IMON_TRIG_LOW_AMP_UA:
        # Small-amplitude branch tightened: ``(amp + 3.5) × 1 mV/µA``
        # caps the threshold at exactly 13.5 mV for 10 µA (down from
        # MATLAB's 14.5 mV).  The 4.5 mV buffer in MATLAB assumed a
        # 2.5 mV/µA stim into a low-Z scope input, where the actual
        # peak comfortably exceeded the threshold.  With the 20 MHz
        # BW limit now applied to the I_mon channel the signal is
        # cleaner but the realised peak on NIL devices is still close
        # to the theoretical limit, so the buffer needs to come down
        # by ~1 mV to leave the trigger above the realised peak.
        level_mag = (amp_mag + 3.5) * SCALING
    else:
        level_mag = amp_mag * SCALING * scale
    # ---- Keep the threshold INSIDE the realised I_mon peak ------------
    # ``(amp + 3.5) mV/µA`` assumes a DEFAULT-preset stimulator
    # (2.5 mV/µA), where at 20 µA it is 23.5 mV against a 50 mV peak — 47 %,
    # comfortably reachable.  On a NIL device (1 mV/µA) the SAME threshold
    # sits ABOVE the 20 mV peak, so the scope can never fire: NUMACq stays 0,
    # every capture is a free-running frame, and at 200 pps (2.5 % duty) it
    # almost always lands in the interpulse — flat V_mon, absurd R/C, hugely
    # negative r².
    #
    # When the caller tells us the ACTUAL scaling, cap the threshold at a
    # fraction of the expected peak so it is always crossable.  This can only
    # ever LOWER an otherwise-unreachable threshold, so a device where the
    # MATLAB-exact value already fits is untouched.
    if imon_v_per_ua:
        _peak = amp_mag * float(imon_v_per_ua)
        if _peak > 0:
            level_mag = min(level_mag, _IMON_TRIG_PEAK_FRAC_MAX * _peak)
    return amp_sign * level_mag


#: Minimum rectangular-biphasic amplitude (µA) that produces an I_mon edge the
#: scope can trigger on when I_mon is the trigger source (no digital sync).
#: Below this the I_mon edge sits under the trigger/noise floor → the scope
#: never fires (NUMACq 0) → stale / misaligned captures.  Equals one
#: rectangular current-quantization step (``STIM_CURRENT_STEP_RECT_NA`` = 100
#: nA, gotcha #64): you cannot trigger on less than one deliverable step.
IMON_TRIGGER_MIN_AMPLITUDE_UA = 0.1


def imon_trigger_slope(amp_ua_signed: float) -> str:
    """Scope EDGE trigger slope for an **I_mon-triggered** pulsed capture.

    ONLY meaningful when I_mon is the trigger source (no digital sync / EXT —
    those fire on the TTL sync line regardless of stim polarity).  Operator's
    rule: FALL for cathodal-first, RISE for anodal-first — the LEADING I_mon
    edge (where the trigger must sit so t=0 is the pulse onset) is a FALLING
    edge for a cathodal-first pulse (I_mon steps 0 → −I) and a RISING edge for
    an anodal-first one (0 → +I).

    Polarity is recovered with ``math.copysign`` (NOT ``amp < 0``) so a
    0 µA-START ramp still picks the right edge: its phase-1 amplitude is a
    SIGNED ZERO (gotcha #56 — ``-0.0`` cathodal-first, ``+0.0`` anodal-first),
    and both ``-0.0 < 0`` (False) and ``-0.0 or 10.0`` (→ +10.0) silently flip
    a cathodal-first 0 µA start to RISE — the misalignment the operator saw on
    a cathodal-first VT-max starting at 0 µA (the leading edge landed ~one
    phase-width off t=0 because the RISE trigger fired on the anodic recovery
    edge instead of the cathodic onset).
    """
    return "FALL" if math.copysign(1.0, float(amp_ua_signed)) < 0 else "RISE"


#: I_mon scaling fallback (Default preset, 2.5 mV/µA) for the continuous
#: trigger when the connected stimulator's scaling isn't available.
_IMON_DEFAULT_V_PER_UA = 2.5e-3


def continuous_trigger_level_slope(amp_ua_signed: float, shape: str,
                                   imon_v_per_ua: "Optional[float]" = None):
    """Best I_mon trigger ``(level_v, slope)`` for a CONTINUOUS (no-
    interpulse-delay) pattern.

    Operator: for a pattern with NO interpulse delay the Plexon digital
    sync has no edge (it stays HIGH the whole time it stimulates) and the
    amplitude-level I_mon trigger sits above the tiny low-current signal —
    so neither fires and NUMACq stays 0.  Trigger on the I_mon waveform
    itself:

      * **slope** — RISE for anodal-first (phase-1 amplitude > 0), FALL for
        cathodal-first (operator's rule; this puts the trigger at the
        phase-1 onset for either polarity).
      * **level** — depends on the SHAPE (operator: "consider the other
        shapes for the best trigger"):
          * SINUSOIDAL / RECTANGULAR swing symmetrically through 0 with a
            STEEP slope at the phase boundary → **level 0** (operator's
            explicit spec for the sinusoid; the square's zero-crossing is
            equally clean).
          * every other shape (gaussian / linear ramp / exp / bowtie /
            halfpipe / speedbumps) either DWELLS near 0 at the phase edge
            (gentle, jittery zero-crossing) or doesn't cross 0 cleanly, so
            trigger at **HALF the phase-1 I_mon peak** (signed), where the
            edge is steep and crossed exactly once per cycle on the phase-1
            slope.  Amplitude-dependent, so the VT/PS per-step
            ``update_imon_trigger_level`` recomputes it each step.

    ``imon_v_per_ua`` is the connected stimulator's I_mon scaling (V/µA);
    falls back to the Default preset (2.5 mV/µA)."""
    from ..waveforms import SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR
    # Via ``imon_trigger_slope`` (copysign) so a 0 µA-START continuous ramp's
    # SIGNED ZERO keeps its polarity (gotcha #56/#168): ``+0.0`` anodal-first →
    # RISE, ``-0.0`` cathodal-first → FALL.  A plain ``> 0`` test flips an
    # anodal-first 0 µA start to FALL.  Identical to ``> 0`` for every non-zero
    # amplitude.
    slope = imon_trigger_slope(amp_ua_signed)
    if shape in (SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR):
        return 0.0, slope
    vpu = float(imon_v_per_ua or 0.0) or _IMON_DEFAULT_V_PER_UA
    return 0.5 * float(amp_ua_signed) * vpu, slope


def _phase1_halfpeak_dwell_us(phase) -> float:
    """Longest contiguous time (µs) phase 1's ideal current stays BEYOND
    half its peak — the pulse the half-peak trigger comparator sees.

    Sampled from :func:`waveforms.shape_breakpoints` on a dense grid.
    Amplitude-INVARIANT (signal and the half-peak level scale together),
    so the width qualification never needs a per-step update.  Returns
    0.0 when it can't be computed (caller falls back to a width-fraction
    default)."""
    try:
        from ..waveforms import shape_breakpoints
        bps = shape_breakpoints(
            amplitude_ua=float(phase.amplitude_ua) or 1.0,
            width_us=float(phase.width_us),
            shape=phase.shape,
            bump_count=int(getattr(phase, "bump_count", 3) or 3),
            tau_us=float(getattr(phase, "tau_us", 0.0) or 0.0),
            tail_zero_us=float(getattr(phase, "tail_zero_us", 0.0) or 0.0),
            offset_ua=float(getattr(phase, "offset_ua", 0.0) or 0.0),
        )
        if len(bps) < 2:
            return 0.0
        import numpy as _np
        t_b = _np.array([b[0] for b in bps], dtype=float)
        i_b = _np.array([b[1] for b in bps], dtype=float)
        t = _np.linspace(0.0, float(phase.width_us), 2000)
        i = _np.interp(t, t_b, i_b)
        peak = float(_np.max(_np.abs(i)))
        if peak <= 0.0:
            return 0.0
        beyond = _np.abs(i) >= 0.5 * peak
        # longest contiguous run of True
        best = run = 0
        for flag in beyond:
            run = run + 1 if flag else 0
            best = max(best, run)
        dt = t[1] - t[0]
        return best * dt
    except Exception:
        return 0.0


def continuous_trigger_plan(pattern, imon_v_per_ua=None) -> dict:
    """Full trigger plan for a CONTINUOUS (no-interpulse-delay) pattern.

    Returns a dict:

      * ``{"kind": "edge", "level_v", "slope"}`` — SINUSOIDAL /
        RECTANGULAR: a steep, once-per-cycle zero-crossing; the plain edge
        trigger is ideal.
      * ``{"kind": "pulse_width", "level_v", "slope", "polarity",
        "width_s", "when"}`` — every other shape: the half-peak edge can
        fire on NARROW noise crossings at low currents, so QUALIFY the
        crossing — trigger only when the signal stays beyond the half-peak
        level for MORE than half the phase-1 dwell (noise blips are µs-
        narrow; the real lobe is tens-of-µs wide).  Amplitude-invariant
        width (signal + level scale together); only the LEVEL needs the
        per-step update.  The scope may decline (legacy family /
        simulator → ``set_trigger_pulse_width`` returns False) — the
        caller keeps the edge trigger from ``continuous_trigger_level_
        slope`` in that case, so this is a strict upgrade, never a
        requirement.
    """
    from ..waveforms import SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR
    ph0 = pattern.phases[0]
    amp = float(ph0.amplitude_ua)
    level, slope = continuous_trigger_level_slope(
        amp, ph0.shape, imon_v_per_ua=imon_v_per_ua)
    if ph0.shape in (SHAPE_SINUSOIDAL, SHAPE_RECTANGULAR):
        return {"kind": "edge", "level_v": level, "slope": slope}
    dwell_us = _phase1_halfpeak_dwell_us(ph0)
    if dwell_us <= 0.0:
        dwell_us = 0.5 * float(ph0.width_us)     # conservative fallback
    return {
        "kind": "pulse_width",
        "level_v": level,
        "slope": slope,
        "polarity": "NEGative" if amp < 0 else "POSitive",
        # Qualify at half the dwell — well above noise-blip widths, well
        # below the real lobe's dwell, floored at 2 µs for sanity.
        "width_s": max(0.5 * dwell_us, 2.0) * 1e-6,
        "when": "MOREthan",
    }


def imon_vertical_scale(amp_ua: float,
                        imon_v_per_ua: Optional[float] = None) -> float:
    """Return the I_mon channel V/div for the requested stimulus amplitude.

    Port of MATLAB ``setOscilloscopeCurrentScale.m`` with the same
    NIL-vs-Default correction applied to :func:`imon_trigger_level`:

    .. code:: matlab

        scale = currentStim / 4 * 1e-3
        if      amp < 20:   scale_num = scale + 3e-3
        elseif  amp < 100:  scale_num = scale + 5e-3
        elseif  amp < 200:  scale_num = scale + 20e-3
        elseif  amp < 500:  scale_num = scale + 40e-3
        else:               scale_num = scale + 80e-3

    MATLAB's empirical constants (the ``/ 4 * 1e-3`` slope and the
    fixed-offset bumps) were tuned on a 2.5 mV/µA Default-scaling
    stimulator, where the I_mon peak at 100 µA is ~250 mV and the
    formula yields 30 mV/div → ~8 divs full-scale (peak fills 4-5
    divs). On a NIL (1 mV/µA) device the peak is 2.5× smaller, so
    the same formula leaves the trace at ~0.3 divs — squished and
    unreadable, exactly what the user reported.

    Fix: when ``imon_v_per_ua`` is supplied, target **4 divisions for
    the expected peak**:

        scale = |amp| × imon_v_per_ua / 4

    then take ``min(MATLAB, expected_peak / 4 + small offset)`` so
    the trace always fits in 2-4 divs regardless of scaling.  On a
    Default device the MATLAB formula already targets ≈ 4 divs, so
    the min() is a near no-op.  On NIL it pulls the V/div down by
    ~2.5× and the trace fills the screen again.

    Pass ``imon_v_per_ua=None`` to get the legacy MATLAB-exact
    behaviour.
    """
    amp_abs = abs(float(amp_ua))
    # Legacy MATLAB-exact path — kept verbatim for callers that don't
    # pass the device scaling (the formula bakes in 1 mV/µA via the
    # ``1e-3`` constant and is correct only when the actual scaling
    # matches that assumption AND the signal isn't attenuated).
    if imon_v_per_ua is None or imon_v_per_ua <= 0.0:
        base = amp_abs / 4.0 * 1e-3
        if amp_abs < 20.0:
            return base + 3e-3
        if amp_abs < 100.0:
            return base + 5e-3
        if amp_abs < 200.0:
            return base + 20e-3
        if amp_abs < 500.0:
            return base + 40e-3
        return base + 80e-3
    # Device-aware path — scale tracks the *actual* expected I_mon
    # peak for THIS stimulator's scaling.  MATLAB's offsets
    # (3 / 5 / 20 / 40 / 80 mV) were tuned on a 1-mV/µA stimulator;
    # we re-express them as a fraction of the expected peak so they
    # scale correctly on both Default (2.5 mV/µA) and NIL (1 mV/µA)
    # devices and on any setup where the I_mon output is attenuated
    # by source impedance or RC filtering.
    #
    #   expected_peak_v = |amp| × imon_v_per_ua
    #   scale = expected_peak / 4    (4 divs for the peak)
    #         + noise_margin         (small fraction of peak, never
    #                                 < an absolute 0.5 mV floor so
    #                                 the trace never sits exactly at
    #                                 the comparator threshold).
    #
    # The scope still snaps this to its native 1-2-5 grid; on NIL
    # the result lands one or two stops finer than MATLAB, which is
    # what makes the 10 µA pulse big enough to trigger on.
    expected_peak_v = amp_abs * float(imon_v_per_ua)
    # Noise margin fraction.  Small amplitudes (< 20 µA) get a tighter
    # margin so the V/div drops slightly — at 10 µA / 1 mV/µA the
    # previous 20 % margin landed at 4.5 mV/div, which the scope
    # snapped up to 5 mV/div and put the ~4 mV measured peak at <1 div
    # (visible but cramped, and the trigger had no headroom).  Using
    # 10 % margin below 20 µA brings the same case to ~3.5 mV/div,
    # which lands the scope on 2 mV/div ⇒ the small pulse fills ~2-4
    # divs and the trigger circuit has room to discriminate.
    if amp_abs < 20.0:
        noise_margin_v = max(0.5e-3, 0.10 * expected_peak_v)
    else:
        noise_margin_v = max(0.5e-3, 0.20 * expected_peak_v)
    return expected_peak_v / 4.0 + noise_margin_v


@dataclass
class ProgressInfo:
    """Snapshot of run progress for the status-bar display.

    Emitted alongside ``ExperimentEvent`` when the runner advances
    to a new logical step (next amplitude in VT, next snapshot in
    SP/LP, next staircase step in PS).  The GUI uses these to
    update a "step X/Y, elapsed mm:ss, ETA mm:ss" indicator so
    long sweeps don't look frozen.

    Fields
    ------
    step : int
        1-based current step number.  ``step == total`` indicates
        the final step is starting (NOT that the run finished).
    total : int
        Total expected steps.  Best estimate at emit time — for
        adaptive sweeps that may early-exit (e.g., VT stopping at
        a water-window hit), the actual completed step count can
        be smaller.
    label : str
        Short human-readable description of what step ``step`` is
        ("amplitude 350 µA on CH 5", "snapshot 7", etc.).  Shown
        in the status bar verbatim — keep it under ~60 chars.
    started_at : float
        ``time.monotonic()`` timestamp at run START (not step
        start).  GUI uses this to compute elapsed + simple ETA
        (``(total - step) * (elapsed / step)``).
    """
    step: int
    total: int
    label: str = ""
    started_at: float = 0.0


@dataclass
class ExperimentEvent:
    """Posted to subscribers as the experiment progresses."""
    kind: str            # 'capture' | 'run_start' | 'run_end' | 'paused' |
                         # 'session_end' | 'log' | 'aborted' | 'progress'
    session: Session
    run: Optional[ChannelRun] = None
    capture: Optional[Capture] = None
    message: str = ""
    #: Optional progress snapshot.  Set on ``kind == "progress"``
    #: events; ``None`` on all other event kinds.  GUI consumers
    #: that don't care about progress can ignore this field.
    progress: Optional[ProgressInfo] = None


@dataclass
class ExperimentResult:
    """Returned by :meth:`ExperimentRunner.run`."""
    session: Session
    captures: List[Capture] = field(default_factory=list)
    aborted: bool = False
    error: Optional[str] = None

    @property
    def max_q_inj(self) -> float:
        if not self.captures:
            return float("nan")
        return max((c.metrics.charge_injection_mc_per_cm2
                    for c in self.captures
                    if c.status.good), default=float("nan"))


class _ChannelMappedStimulator:
    """Thin translating proxy — a SIMPLE device→Plexon channel-number
    conversion applied at the runner→stimulator boundary.

    Port of the MATLAB ``channelStim = File.Parameters.Channels.Plexon(
    channelNum)`` rule (``runVoltageTransient.m`` / ``loadPattern.m`` /
    ``setMonitorChannel.m``): the runner keeps selecting, capturing and
    LABELLING everything by the DEVICE channel the operator picked, but every
    per-channel COMMAND targets the PLEXON stim channel the cable wires that
    device channel to.  This is deliberately NOT done inside the driver / DLL
    methods (operator: "this should not require using the DLL, it should be
    simple converting the channel number") — it's a dict lookup in front of
    the unmodified stimulator.  Every other attribute / method (``info``,
    ``is_open``, ``start_all``, ``stop_all``, ``abort_all``, ``open``,
    ``close``, ``reinit``, ``load_all_channels`` …) delegates unchanged, so
    the all-channel operations and device state are untouched.  Installed only
    for a genuinely non-identity map (see ``ExperimentRunner.set_channel_map``);
    an identity / test-board cable never wraps, so there is zero behaviour
    change for the common case."""

    # Per-channel commands whose FIRST positional arg is the channel number.
    _TRANSLATED = ("load_channel", "set_monitor_channel", "set_repetitions",
                   "start_channel", "stop_channel", "abort_channel")

    def __init__(self, stim, device_to_plexon: dict):
        object.__setattr__(self, "_stim", stim)
        object.__setattr__(self, "_map",
                           {int(d): int(p) for d, p in device_to_plexon.items()})

    def _p(self, channel: int) -> int:
        """Device channel → Plexon stim channel (identity if unmapped)."""
        return self._map.get(int(channel), int(channel))

    def load_channel(self, channel, pattern):
        return self._stim.load_channel(self._p(channel), pattern)

    def set_monitor_channel(self, channel):
        return self._stim.set_monitor_channel(self._p(channel))

    def set_repetitions(self, channel, n):
        return self._stim.set_repetitions(self._p(channel), n)

    def start_channel(self, channel):
        return self._stim.start_channel(self._p(channel))

    def stop_channel(self, channel):
        return self._stim.stop_channel(self._p(channel))

    def abort_channel(self, channel):
        return self._stim.abort_channel(self._p(channel))

    def __getattr__(self, name):
        # Only reached for attributes NOT defined on the proxy — delegate to
        # the wrapped stimulator (use object.__getattribute__ to avoid
        # recursing through this method for the ``_stim`` lookup itself).
        return getattr(object.__getattribute__(self, "_stim"), name)


def stop_all_forced(stim) -> None:
    """``stim.stop_all(force=True)`` with a graceful fallback.

    ``force`` bypasses the driver's ``_is_running`` idempotence guard, which is
    a sweep optimisation we must NOT let skip an operator Stop / Pause.  But
    the kwarg only exists on the real PlexStim driver — the simulator, test
    doubles and any third-party stim may still have the plain 2-arg signature,
    and letting a TypeError escape here wedges a paused worker thread.
    """
    try:
        stim.stop_all(force=True)
    except TypeError:
        stim.stop_all()


class ExperimentRunner(ABC):
    """Base class. Subclasses implement :meth:`run`."""

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope):
        self.session = session
        self.stim = stimulator
        self.scope = oscilloscope
        self._subscribers: List[ProgressCallback] = []
        self._abort_requested = False
        # Let the scope's blocking poll / settle / wait loops see our
        # abort flag so STOP halts a capture promptly instead of waiting
        # out a full averaging window (operator: "When pressing STOP, the
        # program should immediately stop when it is safely possible").
        # The active runner overwrites this on construction; only one
        # runs at a time, so the hook always reflects the live run.
        try:
            self.scope.set_abort_check(lambda: self.aborted)
        except Exception:
            pass
        # Trigger source ("EXT" or a channel name like "CH2") — set by the
        # GUI tab via the Setup trigger toggle before the runner is started.
        # Used to decide whether to update the trigger level per amplitude.
        self.trigger_source: str = "EXT"
        # True when ``trigger_source`` is a TTL sync line — either the EXT
        # BNC or a scope channel the operator tagged with Role=Trigger
        # (typically CH3/CH4 wired to the Plexon digital sync).  In that
        # case the trigger level is fixed at the TTL midpoint (1.4 V),
        # NOT the amplitude-derived I_mon formula — applying I_mon-style
        # millivolt thresholds to a 3.3 V / 5 V TTL signal causes the
        # scope to either never fire (level below the LOW rail) or fire
        # on noise.  Pushed by the GUI tab before run start.
        self.trigger_is_digital: bool = False
        # Waveform smoothing applied to EVERY recorded capture (plot +
        # metrics + saved .npz) — a centered moving average of
        # ``smoothing_window`` samples.  Tames the broadband noise from
        # running channels at full bandwidth.  OFF by default; window
        # only matters when enabled (>= 2).  Pushed by the GUI tab at
        # Start, mirroring ``trigger_source``.
        self.smoothing_enabled: bool = False
        self.smoothing_window: int = 5
        # ---- electrode DC-offset / AC-couple state ----------------------
        # ``{role: volts}`` capturing the DC rest potential of E_ret / E_act
        # measured (DC-coupled) at the start of a channel / run, right before
        # the channel is AC-coupled so its small pulse swing can be fine-
        # scaled (operator: "Let's try AC coupled after capturing the offset
        # from DC coupled").  Passed to ``make_capture`` so the AC-coupled
        # captures carry the absolute level back.  Empty ⇒ pure-DC behaviour
        # (no add-back), so this is inert for runs that don't call
        # :meth:`measure_electrode_dc_offsets_and_switch_to_ac`.
        self._electrode_dc_offset: dict = {}
        # Device→Plexon channel map (cable translation).  Empty ⇒ identity
        # (device channel = Plexon stim channel), the common case.  Installed
        # via :meth:`set_channel_map` before the run; see gotcha on the
        # ``_ChannelMappedStimulator`` proxy.
        self._channel_map: dict = {}
        # When True, the runner pauses between configurations (channels) so
        # the operator can physically re-wire the next channel. The pause is
        # implemented via ``_continue_event``: the runner emits a "paused"
        # event and waits; the GUI calls :meth:`request_continue` to resume.
        # Set by the GUI tab before starting the run.
        import threading as _threading
        self.pause_between_channels: bool = False
        self._continue_event = _threading.Event()
        self._continue_event.set()   # not waiting at construction time
        # ---- user PAUSE / RESUME (distinct from the between-channel rewire
        # gate above).  ``pause(True)`` from the GUI sets ``_pause_requested``;
        # the worker halts stimulation + holds at its next
        # :meth:`wait_if_paused` checkpoint and resumes when ``pause(False)``
        # sets ``_resume_event`` (operator: "a pause button in between start
        # and stop … stop pulsing, hold, then resume").
        self._pause_requested: bool = False
        self._resume_event = _threading.Event()
        self._last_pause_duration_s: float = 0.0
        # Load readback calibration for this stimulator serial, if available.
        serial = getattr(getattr(stimulator, "info", None), "serial_number", "") or ""
        self.cal: Optional[ReadbackCalibration] = load_calibration(stim_serial=serial)
        # ---- closed-loop bias-feedback wiring ---------------------------
        #
        # Optional :class:`BiasFeedbackController` instance — None when the
        # operator hasn't enabled feedback for this run (the GUI's
        # :class:`BiasFeedbackPanel.feedback_config()` returns None when its
        # master checkbox is off, and the worker constructs the controller
        # only when a config comes back).  Runners call
        # :meth:`bias_step_if_armed` at safe points in their loop (between
        # captures, between amplitudes); the helper short-circuits when
        # ``bias_controller is None`` so the no-op path costs ~0.
        #
        # Set by the GUI worker AFTER runner construction and BEFORE
        # ``run()``; mirrors how ``trigger_source`` is pushed.  Tests can
        # also set it directly.
        from .bias_feedback import BiasFeedbackController as _BFC
        self.bias_controller: Optional[_BFC] = None
        # When True, every ``bias_step_if_armed`` call counts as a step
        # for the per-iteration cadence; when False the runner has to call
        # the helper manually.  Most runners flip this on at ``arm`` time
        # and off at ``disarm`` time.  Kept as a per-runner attribute so
        # the GUI can interrogate state from a separate thread.
        self.bias_armed: bool = False
        # Snapshot the hardware identity into session.extras so the Gamry-DTA
        # exporter can write it out without holding a live reference to the
        # drivers. We do this here (in __init__) so subclasses don't have to
        # remember to call it; the snapshot reflects the connection state at
        # the moment the runner was constructed.
        self._snapshot_instrumentation()

    def set_channel_map(self, device_to_plexon: Optional[dict]) -> None:
        """Install a SIMPLE device→Plexon channel-number conversion (cable
        translation) in front of the stimulator.

        ``device_to_plexon`` maps the DEVICE channel the operator selects
        (what all captures / plots / labels use) to the PLEXON stim channel the
        cable physically wires it to — the MATLAB ``Channels.Plexon`` array.
        Only the non-identity entries need be present.  Wraps ``self.stim`` in
        :class:`_ChannelMappedStimulator` so every per-channel COMMAND targets
        the mapped Plexon port while the runner keeps using device channels.

        A NO-OP for an empty / identity map (the test board and every standard
        cable), so the common path never wraps and has zero behaviour change.
        Idempotent: re-installing unwraps any prior proxy first, so calling it
        twice (or with a changed map) can't double-translate.  Called by the
        GUI worker after construction and before ``run()`` (mirrors how
        ``trigger_source`` is pushed)."""
        # Unwrap any previously-installed proxy so this is idempotent.
        if isinstance(self.stim, _ChannelMappedStimulator):
            self.stim = object.__getattribute__(self.stim, "_stim")
        nonident = {int(d): int(p)
                    for d, p in (device_to_plexon or {}).items()
                    if int(d) != int(p)}
        self._channel_map = nonident
        if nonident:
            self.stim = _ChannelMappedStimulator(self.stim, nonident)

    def _record_capture_dose(self, run: "ChannelRun", cap: Capture) -> None:
        """Set ``cap.metrics.n_pulses`` and ``cap.metrics.cumulative_charge_nc``.

        Call this RIGHT AFTER appending ``cap`` to ``run.captures`` (and
        after :func:`compute_metrics` has set ``charge_per_phase_nc``).

        * **n_pulses** — pulses delivered to acquire THIS capture =
          (pulsing duration) × (pulse rate).  Operator: "The number of
          pulses must be calculated from the elapsed time of starting and
          stopping the pulsing."  The runner MEASURES that elapsed time
          (``time.monotonic()`` from ``start_all`` to ``stop_all``) and
          PRE-SETS ``cap.metrics.n_pulses = round(duration_s × rate_hz)``
          before calling this helper; we honour any already-set positive
          value.  Only when the runner didn't measure it (NaN / 0 — e.g.
          the simulator, where capture returns instantly so no real
          pulsing time elapsed) do we fall back to the scope averaging
          count (``scope._expected_acq_navg`` pulse-triggered frames), or
          1 as a last resort.
        * **cumulative_charge_nc** — running total of cathodic charge
          delivered up to + including this capture, building on the run's
          earlier captures: ``Σ |Q_ph_i| × n_pulses_i``.  Recomputed by
          summing ``run.captures`` (small N) so it needs no separate
          accumulator and naturally resets per ChannelRun.

        For VT / PS each capture is a discrete pulsing burst at one
        amplitude, so the cumulative is the true delivered dose.  (SP / CP /
        LP pulse continuously between snapshots; their runners don't call
        this, so the fields stay NaN and the table omits the rows rather
        than under-reporting the continuous dose.)
        """
        import math
        n_pre = cap.metrics.n_pulses
        if not (isinstance(n_pre, (int, float))
                and math.isfinite(n_pre) and n_pre > 0):
            # Runner didn't measure real pulsing time → averaging-count
            # fallback (pulses that built the averaged frame).
            try:
                navg = int(getattr(self.scope, "_expected_acq_navg", 0) or 0)
            except Exception:
                navg = 0
            cap.metrics.n_pulses = float(navg) if navg > 0 else 1.0
        total_nc = 0.0
        total_pulses = 0.0
        for c in run.captures:
            q = c.metrics.charge_per_phase_nc
            n = c.metrics.n_pulses
            if math.isfinite(n):
                total_pulses += float(n)
                if math.isfinite(q):
                    total_nc += abs(float(q)) * float(n)
        cap.metrics.cumulative_charge_nc = total_nc
        cap.metrics.cumulative_n_pulses = total_pulses

    # Access-resistance drift monitor (operator: Mann-Whitney test of the
    # present access resistance vs the previous R_a in the run).  Knobs:
    _RA_DRIFT_WINDOW = 4        # "present" sample = the last N non-zero-µA R_a
    _RA_DRIFT_MIN_UA = 0.5      # exclude ~0 µA captures (R_a = V/I undefined)

    def check_access_resistance_drift(self, run: "ChannelRun") -> None:
        """Nonparametric drift test of the access resistance over the run.

        Two-sided Mann-Whitney U of the RECENT R_a window vs the run's EARLIER
        R_a — 0 µA captures excluded (R_a is undefined at zero current)
        (operator: "the access resistance at 0 uA is excluded … I would rather
        do a specific statistical test").  Stores the p-value + a boolean flag
        on the LATEST capture (``access_resistance_drift_p`` / ``_flag``) and
        emits ONE ⚠ log line at the ONSET of a sustained drift (edge-triggered,
        so it doesn't spam).  WARN-ONLY — never stops the run.  Fully guarded:
        a failure here can NEVER affect stimulation.  Call RIGHT AFTER
        appending the capture + computing its metrics."""
        try:
            from ..metrics import (
                representative_access_resistance_kohm as _rep,
                access_resistance_drift_mannwhitney as _mw)
            ras = []
            for c in run.captures:
                try:
                    amp = abs(float(c.pattern.excitation_phase.amplitude_ua))
                except Exception:
                    amp = 0.0
                if amp <= self._RA_DRIFT_MIN_UA:     # exclude 0 µA
                    continue
                r = _rep(c)
                if r == r:                            # not NaN
                    ras.append(float(r))
            w = int(self._RA_DRIFT_WINDOW)
            if len(ras) < w + 1:
                return
            res = _mw(ras[:-w], ras[-w:])
            cap = run.captures[-1]
            cap.metrics.access_resistance_drift_p = res.p_value
            cap.metrics.access_resistance_drift_flag = bool(res.flagged)
            # Edge-triggered: warn only when THIS capture flags and the PREVIOUS
            # one didn't, so a sustained drift logs ONCE at its onset.
            prev_flagged = (len(run.captures) >= 2 and bool(getattr(
                run.captures[-2].metrics,
                "access_resistance_drift_flag", False)))
            if res.flagged and not prev_flagged:
                self._emit(ExperimentEvent(
                    kind="log", session=self.session, run=run, capture=cap,
                    message=(
                        f"⚠ Access-resistance DRIFT: recent R_a median "
                        f"{res.median_present_kohm:.1f} kΩ differs from the run "
                        f"baseline {res.median_reference_kohm:.1f} kΩ "
                        f"(Mann-Whitney p={res.p_value:.2g}) — possible "
                        f"electrode degradation.")))
        except Exception:
            pass

    def _snapshot_instrumentation(self) -> None:
        from dataclasses import asdict
        try:
            stim_info = asdict(self.stim.info) if getattr(self.stim, "info", None) else {}
        except Exception:
            stim_info = {}
        try:
            scope_info = asdict(self.scope.info) if getattr(self.scope, "info", None) else {}
        except Exception:
            scope_info = {}
        aliases = dict(getattr(self.scope, "channel_aliases", {}) or {})
        # Look up coating-derived water-window limits if available
        coating_props = None
        try:
            from ..config import COATINGS, DEPOLARIZATION_TIME_US
            if self.session.test.array.sites:
                first = self.session.test.array.sites[0]
                c = COATINGS.get(first.coating)
                if c is not None:
                    coating_props = {
                        "name": c.name,
                        "cathodic_limit_v": c.cathodic_limit_v,
                        "anodic_limit_v": c.anodic_limit_v,
                        "typical_csc_mc_per_cm2": c.typical_csc_mc_per_cm2,
                    }
        except Exception:
            DEPOLARIZATION_TIME_US = 12.0  # fall back to the canonical default
        else:
            from ..config import DEPOLARIZATION_TIME_US
        # E_pol time delay: honour the GUI's custom value (Setup-tab "custom
        # E_pol time delay" toggle) if the setup snapshot carries one, else the
        # canonical 12 µs.  This is the delay compute_metrics uses for the
        # operator TIME method (phase_end + depol).
        import math as _math
        _depol = DEPOLARIZATION_TIME_US
        try:
            _snap = self.session.test.extras.get("setup_snapshot", {}) or {}
            _cand = _snap.get("depolarization_us", None)
            if _cand is not None:
                _cand = float(_cand)
                if _math.isfinite(_cand) and _cand >= 0:
                    _depol = _cand
        except Exception:
            _depol = DEPOLARIZATION_TIME_US
        self.session.test.extras.update({
            "stimulator_info": stim_info,
            "oscilloscope_info": scope_info,
            "channel_aliases": aliases,
            "coating_props": coating_props,
            "depolarization_us": _depol,
        })
        # Task #57: capture reproducibility metadata once at runner
        # construction (PULSAR git hash + version, Python + package
        # versions, OS, hardware identity, setup-snapshot hash).
        # Stored on the Session itself (NOT on test.extras) so it
        # round-trips through the .npz under ``meta.system_metadata``
        # — see persistence.save_session_npz.  Best-effort: any
        # individual probe failure becomes an empty string in the
        # dict rather than raising.
        try:
            from ..session_metadata import capture_system_metadata
            self.session.system_metadata = capture_system_metadata(
                self.session,
                stimulator=self.stim,
                oscilloscope=self.scope,
            )
        except Exception:
            # Defensive — system_metadata is informational, never
            # block runner construction over it.
            pass

    # ----- pub/sub --------
    def subscribe(self, cb: ProgressCallback) -> None:
        self._subscribers.append(cb)

    def _emit(self, event: ExperimentEvent) -> None:
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception as e:
                # Never let a UI bug kill the experiment
                print(f"[stimtest] subscriber error: {e}")

    def abort(self) -> None:
        self._abort_requested = True
        # Releasing the continue gate ensures any thread parked in
        # ``wait_for_continue`` unblocks promptly — otherwise ``abort``
        # only takes effect after the user dismisses the rewire dialog.
        try:
            self._continue_event.set()
        except Exception:
            pass
        # Likewise release a worker parked in ``wait_if_paused`` so a Stop
        # pressed while PAUSED aborts immediately instead of after the next
        # 0.1 s poll tick.
        try:
            self._resume_event.set()
        except Exception:
            pass
        # Emergency-cease the stimulator immediately.  Uses the
        # PlexStim ``PS_AbortAll`` primitive (via ``abort_all`` on
        # the driver) rather than ``stop_all`` (``PS_StopStimAll-
        # Channels``) for two reasons:
        #   1. ``PS_AbortAll`` halts mid-pulse — operator intent on
        #      a Stop button is "cease NOW", not "let the current
        #      pulse finish".  ``stop_all`` waits up to one capture
        #      period for the in-flight waveform to complete.
        #   2. ``PS_AbortAll`` has no trigger-mode requirement;
        #      ``stop_all`` returns SDK error 4 outside
        #      ``PS_TRIG_SOFT`` mode.
        # The cross-thread call is now safe: the DLL access is
        # serialised by ``_dll_lock`` inside the Plexon driver
        # (see plexon.py ``_dll_locked``), so this can no longer
        # corrupt the DLL heap by racing the worker's stim calls.
        # CLAUDE.md §3's "single producer" rule is satisfied at the
        # SDK level by the lock — only one thread is inside any
        # SDK call at a time, even if both threads are calling.
        try:
            self.stim.abort_all()
        except Exception:
            pass

    def request_continue(self) -> None:
        """Release a runner that's paused between channels.

        Called by the GUI when the user clicks "Continue" on the
        rewire dialog.  Idempotent — extra calls are no-ops.
        """
        try:
            self._continue_event.set()
        except Exception:
            pass

    # ----- closed-loop bias feedback ----------------------------------
    def arm_bias_feedback(self) -> bool:
        """Activate the optional :class:`BiasFeedbackController`.

        Called by the runner right before its first capture iteration
        (typically after ``apply_default_scope_view`` so the scope's
        gating window writes don't get clobbered).  No-op when no
        controller is attached.  Returns True when the controller
        actually armed, False otherwise — runners can use this to
        skip status emission entirely for an unarmed loop.

        On any failure the controller is treated as unarmed; the
        operator sees a log message but the experiment continues.
        Feedback is a *best-effort overlay*: a broken bias module
        must not break the underlying data collection.
        """
        if self.bias_controller is None:
            self.bias_armed = False
            return False
        try:
            self.bias_controller.arm()
        except Exception as exc:
            self._emit(ExperimentEvent(
                kind="log",
                session=self.session,
                message=(
                    f"[bias] arm() raised "
                    f"{type(exc).__name__}: {exc}; "
                    f"closed-loop feedback disabled for this run."),
            ))
            self.bias_controller = None
            self.bias_armed = False
            return False
        self.bias_armed = True
        self._emit(ExperimentEvent(
            kind="log",
            session=self.session,
            message=(
                f"[bias] armed: setpoint="
                f"{self.bias_controller.config.setpoint_v:.3f} V, "
                f"tolerance=±"
                f"{self.bias_controller.config.tolerance_v * 1e3:.1f} mV, "
                f"k_i={self.bias_controller.config.k_i:.3f}, "
                f"gating="
                f"{self.bias_controller.config.gating_window_us[0]:.0f}-"
                f"{self.bias_controller.config.gating_window_us[1]:.0f} µs."),
        ))
        return True

    def disarm_bias_feedback(self) -> None:
        """Disarm the controller + restore scope MEASUrement defaults.

        Idempotent + safe to call from a ``finally`` block — wraps the
        controller's ``disarm()`` in a try/except so a scope SCPI
        failure during teardown doesn't mask the run's actual error.
        Does NOT change the bias-module's master enable; the runner
        decides whether to leave the DAC armed for the next run.
        """
        self.bias_armed = False
        ctrl = self.bias_controller
        if ctrl is None:
            return
        try:
            ctrl.disarm()
        except Exception as exc:
            # Log + swallow.  Letting a teardown failure escape would
            # mask the run's actual error in the GUI dialog.
            try:
                self._emit(ExperimentEvent(
                    kind="log",
                    session=self.session,
                    message=(
                        f"[bias] disarm() raised "
                        f"{type(exc).__name__}: {exc}; ignored."),
                ))
            except Exception:
                pass

    def bias_step_if_armed(self) -> None:
        """Run one feedback iteration when the controller is armed.

        Called at safe per-iteration points in each runner's loop
        (typically right after a capture lands or between amplitude
        steps).  No-op when ``bias_armed`` is False — runners can
        call this unconditionally without checking; the helper does
        the cheap early-out.

        Status surfaces as a ``log`` event with the
        ``[bias-step]`` prefix; the GUI parses these to update the
        BiasFeedbackPanel status badge.  Future-proofing: when the
        progress-channel needs become richer we'll add a dedicated
        ``bias_status`` event kind, but log lines keep the wire
        format flexible while #43's first-runner-wiring lands.
        """
        if not self.bias_armed or self.bias_controller is None:
            return
        try:
            step = self.bias_controller.step()
        except Exception as exc:
            # A failed step doesn't disarm the loop — transient scope
            # / SDK failures are exactly what the controller's own
            # try/except envelopes handle.  If the failure is at this
            # OUTER level, surface and continue.
            self._emit(ExperimentEvent(
                kind="log",
                session=self.session,
                message=(
                    f"[bias-step] outer-level "
                    f"{type(exc).__name__}: {exc}"),
            ))
            return
        # Emit a tagged log line so the GUI can route it (a dedicated
        # event kind comes later; for now a structured prefix is
        # cheap and lets bench operators grep the session log).
        msg = (f"[bias-step] measured={step.measured_v:+.4f} V, "
               f"error={step.error_v * 1e3:+.2f} mV, "
               f"bias={step.bias_v_after:+.4f} V")
        if step.saturated:
            msg += " ⚠ SATURATED"
        if not step.vmon_sane:
            msg += " ⚠ V_mon insane"
        if step.note:
            msg += f"  ({step.note})"
        self._emit(ExperimentEvent(
            kind="log",
            session=self.session,
            message=msg,
        ))

    def wait_for_continue(self, next_config_label: str = "") -> bool:
        """Block until :meth:`request_continue` (or :meth:`abort`) fires.

        Emits a ``"paused"`` event so the GUI can pop the rewire prompt,
        then waits on the event.  Polls every ~100 ms so an abort during
        the pause unblocks promptly.  Returns True if the user resumed,
        False if the wait ended due to an abort.

        Safe to call from the runner thread; safe to interleave with
        scope/stim cleanup before invoking.
        """
        if not self.pause_between_channels:
            return True
        try:
            self.stim.stop_all()
        except Exception:
            pass
        self._continue_event.clear()
        self._emit(ExperimentEvent(
            kind="paused", session=self.session,
            message=(f"Paused — rewire to {next_config_label}, then click "
                     f"Continue.").strip()))
        while not self._continue_event.wait(timeout=0.1):
            if self._abort_requested:
                return False
        return not self._abort_requested

    #: TTL midpoint used as the trigger level for any digital sync line
    #: (EXT BNC or a scope channel tagged Role=Trigger).  3.3 V and 5 V
    #: TTL both clear 1.4 V with margin in both directions; the same
    #: level works for the Plexon stimulator's 3.3 V CMOS sync output
    #: and the bench function generators that produce 5 V TTL.
    TTL_TRIGGER_LEVEL_V: float = 1.4

    def update_imon_trigger_level(self, amp_ua_signed: float,
                                   phase_width_us: float = 200.0) -> None:
        """Update the scope trigger level for the current amplitude.

        Behaviour depends on the trigger source the GUI configured:

          * ``trigger_source == "EXT"`` → no-op.  The scope firmware
            owns the level on its dedicated EXT BNC.
          * ``trigger_is_digital`` (a channel-Trigger TTL sync line,
            typically CH3/CH4 wired to the Plexon digital sync) →
            write the fixed TTL midpoint :attr:`TTL_TRIGGER_LEVEL_V`.
            The amplitude-derived formula doesn't apply — a sync line
            has a fixed 3.3 V / 5 V swing regardless of stim current.
          * Otherwise (I_mon channel trigger) → MATLAB
            ``setTriggerLevel`` formula so the threshold tracks the
            programmed current and stays above the noise floor at
            every step of a VT / PS sweep.
        """
        try:
            # CONTINUOUS (no-interpulse-delay) pattern: the GUI configured an
            # I_mon zero-crossing / half-peak trigger (the digital sync has no
            # edge; the amplitude-level trigger sits above the tiny signal —
            # the bench NUMACq 0/64 failure).  Re-derive the SAME shape-aware
            # level here so the per-step update keeps it consistent instead of
            # falling through to the TTL / amplitude-level formulas.  Checked
            # BEFORE the EXT / digital branches because for a continuous
            # pattern the I_mon analog trigger REPLACES both.
            _test = getattr(self.session, "test", None)
            _pat = getattr(_test, "pattern", None)
            if (_pat is not None and getattr(_pat, "phases", None)
                    and not _pat.has_interpulse_gap()):
                stim_info = getattr(self.stim, "info", None)
                _vpu = float(
                    getattr(stim_info, "imon_scaling_v_per_ua", 0.0) or 0.0)
                _level, _ = continuous_trigger_level_slope(
                    amp_ua_signed, _pat.phases[0].shape,
                    imon_v_per_ua=_vpu or None)
                self.scope.set_trigger_level(_level)
                return
        except Exception:
            pass
        if self.trigger_source == "EXT":
            return
        try:
            if self.trigger_is_digital:
                self.scope.set_trigger_level(self.TTL_TRIGGER_LEVEL_V)
                return
            # Thread the stimulator's actual I_mon scaling through so
            # the clamp inside ``imon_trigger_level`` can pull the
            # threshold below the expected peak on NIL devices.
            stim_info = getattr(self.stim, "info", None)
            imon_v_per_ua = float(
                getattr(stim_info, "imon_scaling_v_per_ua", 0.0) or 0.0)
            level = imon_trigger_level(
                amp_ua_signed, phase_width_us,
                imon_v_per_ua=imon_v_per_ua or None)
            self.scope.set_trigger_level(level)
        except Exception:
            pass

    def check_trigger_alignment(self, acq, *,
                                tolerance_us: float = 5.0) -> Optional[float]:
        """Return the captured I_mon leading-edge time (µs), or ``None``.

        Finds the FIRST sample-to-sample I_mon edge exceeding 80 % of the
        max edge (the phase-1 onset on a multi-edge biphasic).  Returns
        that edge's time so a caller can record it; ``None`` if the
        acquisition is malformed or no I_mon alias is mapped.

        **Does NOT warn** on a non-zero edge time — see the note at the
        return statement: with the digital-sync trigger the I_mon edge
        position is governed by the (imperfect) current timing and the
        V_mon/I_mon edge skew at small phase widths, so it is not a
        reliable time-axis signal.  ``tolerance_us`` is unused, kept for
        API/back-compat.
        """
        import numpy as _np
        try:
            aliases = getattr(self.scope, "channel_aliases", {}) or {}
            imon_ch = aliases.get("imon")
            if not imon_ch:
                return None
            t_us = getattr(acq, "time_us", None)
            chans = getattr(acq, "channels", None) or {}
            i_mon = chans.get(imon_ch)
            if t_us is None or i_mon is None:
                return None
            if len(t_us) < 2 or len(i_mon) != len(t_us):
                return None
            di = _np.abs(_np.diff(_np.asarray(i_mon, dtype=float)))
            # Find the FIRST edge that exceeds 80 % of the max edge,
            # NOT the absolute argmax.  A biphasic pulse has multiple
            # edges of comparable magnitude (phase 1 onset, phase 1
            # end, phase 2 onset, phase 2 end) — argmax can land on
            # any of them depending on noise, producing apparent
            # offsets at +200 µs / +250 µs / +450 µs etc. that aren't
            # actually trigger-alignment errors.  Picking the FIRST
            # large edge consistently lands on the leading-edge
            # transition (phase 1 onset) which IS where the trigger
            # should be.  Closes Task #59 (false-positive cluster).
            max_di = float(_np.max(di))
            if max_di <= 0:
                return None
            threshold = 0.80 * max_di
            edge_candidates = _np.where(di >= threshold)[0]
            if edge_candidates.size == 0:
                return None
            edge_idx = int(edge_candidates[0])  # FIRST such edge
            t_edge = float(t_us[edge_idx])
            # NOTE: we deliberately DON'T warn on a non-zero edge time
            # (operator: "Do not have warnings about the trigger warning.
            # The current pulse is not perfect, which is why I use the
            # digital signal as trigger than current.  Smaller pulse widths
            # do notable delays between the voltage drop and the current
            # drop").  With the digital-sync trigger the I_mon edge position
            # relative to t=0 is set by the current's own (imperfect) timing
            # — at small phase widths there's a REAL, expected skew between
            # the V_mon and I_mon edges — so it is NOT a reliable
            # time-axis-correctness signal.  Time-axis correctness is handled
            # by the acquisition time axis itself (gotchas #37 / #53).  The
            # edge time is still RETURNED for any caller that wants to record
            # it; ``tolerance_us`` is retained for API/back-compat.
            return t_edge
        except Exception:
            return None

    def commit_loaded_channels(self, config) -> None:
        """Commit staged channel parameters to the stimulator hardware
        immediately before starting — config-dependent, mirroring the
        MATLAB ``loadPattern.m`` commit branch.

        * **Monopolar** (no return channels, ``config.returns`` empty):
          issue ONE ``PS_LoadAllChannels``.  This is REQUIRED on real
          PlexStim hardware — the device arms an all-channels start from
          this single commit.  Per-channel ``PS_LoadChannel`` alone
          (which :meth:`load_channel` / :meth:`load_zero_unused_channels`
          already issued) leaves ``PS_StartStimAllChannels`` returning OK
          with nothing actually armed → no output, no digital-sync edge
          (scope ``NUMACq`` stays 0).  Matches MATLAB ``loadPattern.m``
          ``isempty(channelReturn_arr) -> PS_LoadAllChannels``.
        * **Multipolar** (``config.returns`` non-empty): NO-OP here.  The
          per-channel ``PS_LoadChannel`` commits are correct, and
          ``PS_LoadAllChannels`` MUST NOT be used — it would commit the
          return channels, which must stay UNLOADED as passive sinks.
          Matches MATLAB's per-channel ``PS_LoadChannel`` loop.

        Call AFTER ``load_channel(active)`` + ``load_zero_unused_channels``
        and immediately BEFORE ``start_all``.
        """
        if getattr(config, "returns", None):
            # Multipolar — per-channel commit already done; do NOT
            # PS_LoadAllChannels (would arm the unloaded return path).
            return
        try:
            self.stim.load_all_channels()
        except Exception as e:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=f"load_all_channels (monopolar commit) failed: {e}"))

    def _smooth_acquisition(self, acq):
        """Apply the operator's moving-average smoothing (when enabled)
        to every channel array of ``acq`` IN PLACE, so the resulting
        :class:`Capture` — plot, metrics, AND saved ``.npz`` — all use
        the smoothed waveform.

        Centered moving average of ``self.smoothing_window`` samples via
        :func:`metrics._smooth` (``uniform_filter1d``, edge-safe).  No-op
        when smoothing is off or the window < 2.  Call right before
        :func:`make_capture` (after the rescale loop, so the scaling
        decision is unaffected).  Returns ``acq`` for chaining.
        """
        try:
            if not getattr(self, "smoothing_enabled", False):
                return acq
            win = int(getattr(self, "smoothing_window", 5) or 0)
            if win < 2:
                return acq
            from ..metrics import _smooth
            chans = getattr(acq, "channels", None)
            if not chans:
                return acq
            for _name, _arr in list(chans.items()):
                try:
                    chans[_name] = _smooth(_arr, window=win)
                except Exception:
                    pass  # leave that channel raw if smoothing fails
        except Exception:
            pass
        return acq

    def _pattern_at_amplitude(self, base, amp_ua: float):
        """Build the pattern whose EXCITATION phase magnitude == ``amp_ua``.

        Shared by every runner that steps / sets a stimulus amplitude from a
        template (VT ramp, PS staircase, SP / LP fixed pulsing).

        For a normal (non-zero) template this is IDENTICAL to
        ``base.scaled(amp_ua / |excitation amplitude|)`` — the multiplicative
        scale that preserves each phase's biphasic / triphasic ratio.

        **Zero-amplitude template (e.g. VT / PS "starting at 0 µA").**
        Multiplicative scaling CANNOT grow a zero — ``0 × factor == 0`` — so
        ``scaled(amp/excite)`` would leave EVERY step at 0 µA while the ramp
        variable climbs meaninglessly (delivering no current; the operator's
        "did not try increasing the stimulation current" / "stopped at #7").
        A zero template still carries the pulse GEOMETRY (phase widths, shapes,
        delays, rate) and the leading phase's POLARITY — recoverable from its
        SIGNED ZERO via ``copysign`` (``-0.0`` ⇒ cathodic-first, ``+0.0`` ⇒
        anodic-first; note ``-0.0 < 0`` is False, so ``PulsePattern.polarity``
        reads +1 — use ``copysign``, NOT a ``< 0`` test).  So we rebuild it as a
        charge-balanced pattern at ``±amp_ua`` with STRICT sign alternation from
        phase 1 (the convention ``PulsePattern.biphasic`` / ``.triphasic`` use),
        equal per-phase magnitude.  A zero template has no per-phase ratio to
        preserve, so equal magnitude is the only well-defined choice — exactly
        the symmetric-biphasic case; an asymmetric / triphasic RATIO must be
        expressed with a non-zero amplitude.
        """
        exc_mag = abs(base.excitation_phase.amplitude_ua)
        if exc_mag > 0.0:
            return base.scaled(amp_ua / exc_mag)
        if not base.phases:
            return base
        pol = int(math.copysign(1.0, base.phases[0].amplitude_ua))  # ±1
        new_phases = [
            replace(p, amplitude_ua=(pol if i % 2 == 0 else -pol) * float(amp_ua))
            for i, p in enumerate(base.phases)
        ]
        return replace(base, phases=new_phases)

    def _epol_depol_us(self) -> float:
        """The E_pol time delay (µs) to pass to ``compute_metrics`` for this
        run — resolved once into ``test.extras['depolarization_us']`` by the
        hardware-snapshot step (honouring the GUI's custom-delay toggle, else
        the canonical 12 µs).  Every runner routes its ``compute_metrics``
        call through this so the operator's chosen delay is applied
        uniformly."""
        from ..config import DEPOLARIZATION_TIME_US
        try:
            v = float(self.session.test.extras.get(
                "depolarization_us", DEPOLARIZATION_TIME_US))
            if v == v and v >= 0:      # finite (NaN != NaN) and non-negative
                return v
        except Exception:
            pass
        return float(DEPOLARIZATION_TIME_US)

    # ---- burst-aware pulse-timing helpers (single source of truth) --------
    @staticmethod
    def _pulses_per_second(pattern) -> float:
        """OVERALL pulses delivered per second for pulse-COUNT accounting —
        ``rate_hz`` for an ordinary train, ``pulses_per_burst × burst_rate``
        for a burst (N pulses every ``burst_period``, so the average is FAR
        below the intra-burst ``rate_hz``).  Identity ``== rate_hz`` for every
        non-burst pattern, so routing existing call sites through it is safe.
        Prefer the ``effective_pulse_rate_hz`` property; fall back to a manual
        derivation for a mock/legacy pattern that lacks it."""
        eff = getattr(pattern, "effective_pulse_rate_hz", None)
        if isinstance(eff, (int, float)) and eff > 0:
            return float(eff)
        try:
            dev = float(getattr(pattern, "device_period_us", 0.0) or 0.0)
            ppp = int(getattr(pattern, "pulses_per_period", 1) or 1)
            if dev > 0:
                return ppp * 1e6 / dev
        except Exception:
            pass
        # Terminal fallback for a mock / duck-typed pattern — guarded too, so a
        # non-numeric ``rate_hz`` degrades to 0 rather than raising into the
        # runner's timing path (the docstring promises this graceful fallback).
        try:
            return float(getattr(pattern, "rate_hz", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _averager_fill_s(self, pattern, *, headroom_s: float = 0.0) -> float:
        """Seconds for the scope averager to accumulate its ``n_avg`` pulse-
        triggered frames = ``n_avg / overall_pulse_rate`` (+ optional
        headroom).  For a burst this is LONGER than ``n_avg / rate_hz`` — the
        trigger rate averaged over the inter-burst gaps is the OVERALL rate, so
        keeping ``rate_hz`` under-waits and reads an incomplete average
        (gotcha #32).  Identity to the old ``n_avg / rate_hz`` for a non-burst
        pattern.  A missing / zero rate yields ``headroom_s`` (never 0-wait)."""
        try:
            navg = int(getattr(self.scope, "_expected_acq_navg", 0) or 0)
        except Exception:
            navg = 0
        if navg <= 0:
            navg = 8      # matches the runners' ``_expected_acq_navg or 8``
        pps = self._pulses_per_second(pattern)
        if pps <= 0:
            return float(headroom_s)
        return navg / pps + float(headroom_s)

    def load_zero_unused_channels(self, pattern, config,
                                  active_channels=None) -> None:
        """Load a zero-amplitude, same-duration copy of ``pattern`` on
        every channel that is NOT an active channel AND NOT in
        ``config.returns``.

        ``active_channels`` (optional) is the SET of channels carrying the
        REAL pattern.  Defaults to ``{config.active}`` (the single-active
        case every VT/PS/SP sweep uses).  Long-Term Pulsing passes the full
        set of selected MONOPOLAR channels so it can pulse them ALL
        simultaneously (operator: "LP is not pulsing all of the channels in
        monopolar like I selected") — those channels then get the real
        pattern (loaded by the caller) and are EXCLUDED from the zero set
        here.

        Direct port of MATLAB ``setPattern.m`` "Zero Current" block
        (lines 142-193).  Three categories of channel exist on the
        stimulator during a run, and each gets a different treatment:

        ============ =========================================================
        active       Receives the experiment's real pattern.  ONE channel
                     per configuration (``config.active``).
        returns      Multipolar return path.  Left UNLOADED so the device
                     routes current back through these channels passively
                     — loading anything (even zero) makes them ACTIVE
                     drivers, which breaks the multipolar configuration.
                     Zero or more channels per configuration
                     (``config.returns``).
        unused       Everything else.  Loaded with a same-duration but
                     zero-amplitude copy of the active pattern so the
                     channel TICKS in step with the active one but
                     delivers no current.  Without this, an unused
                     channel either carries a stale pattern from a
                     prior step (if reinit was skipped) or runs out of
                     sync with the active channel's pulse cycle.
        ============ =========================================================

        **CG (Common Ground)** configurations have ``returns =``
        every-other-channel by construction (see
        ``Configuration.enumerate_combinations`` for ``"CG"``), so the
        unused set is naturally empty and this method becomes a no-op
        — matching MATLAB's explicit ``~isCG`` skip without a
        special-case branch.

        Errors loading any single unused channel are swallowed (we
        emit a log line but don't abort the run) — the active channel's
        data still flows correctly regardless.

        ``set_repetitions(ch, 0)`` is called on every loaded unused
        channel so it follows the same "infinite repetitions until
        stopped" cadence as the active channel; otherwise the unused
        channel would default to 1 repetition and fall silent after
        one pulse cycle while the active keeps going.
        """
        try:
            n_channels = int(
                getattr(getattr(self.stim, "info", None),
                        "n_channels", 0) or 0)
            if n_channels <= 0:
                return
            if active_channels is not None:
                active_set = {int(a) for a in active_channels}
            else:
                active_set = {int(getattr(config, "active", 0))}
            returns_set = set(int(r) for r in (getattr(config, "returns", ()) or ()))
            excluded = active_set | returns_set
            unused = [ch for ch in range(1, n_channels + 1)
                      if ch not in excluded]
            if not unused:
                # CG case (returns spans every other channel) or a
                # 1-channel device — nothing to load.
                return
            try:
                zero_pattern = pattern.scaled(0.0)
            except Exception:
                # Pathological pattern that can't be scaled — bail out
                # silently rather than crashing the runner.
                return
            # DEVICE-SYNCED early skip: when ONLY the active channel's
            # amplitude changes (VT/PS/LP sweep), the unused channels' zero
            # pattern is unchanged and the device still holds it — so there's
            # nothing to (re)load (operator: "the zero/unused channels do not
            # need to be loaded again").  Check the stim's per-channel content
            # cache (``_channel_content_sig`` — the authoritative record of
            # what each channel holds on the DEVICE); it is WIPED by
            # open/close/reinit, so after a per-config multipolar reinit the
            # check fails and the channels reload.  Skips the whole call — no
            # per-channel ``load_channel`` (which would recompute this same
            # signature N times) and no ``set_repetitions``.  Falls through on
            # a stim without the cache (the simulator), preserving behaviour.
            _csig_fn = getattr(type(self.stim), "_content_signature", None)
            _dev_cache = getattr(self.stim, "_channel_content_sig", None)
            if callable(_csig_fn) and isinstance(_dev_cache, dict):
                try:
                    _zsig = _csig_fn(zero_pattern)
                    if all(_dev_cache.get(ch) == _zsig for ch in unused):
                        return
                except Exception:
                    pass
            loaded_chs = []
            for ch in unused:
                try:
                    _did = self.stim.load_channel(ch, zero_pattern)
                    # Match the active channel's infinite-repetitions
                    # cadence so the unused channels stay aligned with
                    # the active pulse cycle, not fall out after one
                    # pulse.
                    try:
                        self.stim.set_repetitions(ch, 0)
                    except Exception:
                        pass
                    # Count only channels ACTUALLY (re)uploaded.  ``load_channel``
                    # returns False on a content-cache hit — the device already
                    # holds this zero pattern, so it was NOT re-applied this step.
                    # The zero pattern doesn't change across an amplitude sweep
                    # (VT/PS scale amplitude only → constant zero timing), so
                    # after the first step every channel is a cache hit and this
                    # stays empty — no redundant "Loaded zero-amplitude pattern"
                    # log line every step (operator: "the zero channels do not
                    # change … not applied every step").
                    if _did is not False:
                        loaded_chs.append(ch)
                except Exception:
                    # One unused channel failing to load shouldn't kill
                    # the run.  Just continue with the rest — the active
                    # channel's data remains correct.
                    continue
            n_loaded = len(loaded_chs)
            if n_loaded > 0:
                try:
                    total_us = float(getattr(pattern, "total_pulse_us", 0.0))
                except Exception:
                    total_us = 0.0
                self._emit(ExperimentEvent(
                    kind="log", session=self.session,
                    message=(
                        f"Loaded zero-amplitude pattern on {n_loaded} "
                        f"unused channel(s) — "
                        f"{sorted(loaded_chs)[:8]}"
                        + (f" … (+{len(loaded_chs) - 8} more)"
                           if len(loaded_chs) > 8 else "")
                        + f"  ·  duration {total_us:.1f} µs "
                        f"(matches active pattern).")))
        except Exception as e:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=f"load_zero_unused_channels skipped: {e}"))

    def update_imon_vertical_scale(self, amp_ua: float) -> None:
        """Re-apply the I_mon channel V/div for the current amplitude.

        Mirrors MATLAB ``setOscilloscopeCurrentScale.m`` — sized to keep
        the I_mon trace at 2-4 divisions on screen across the sweep so
        the user can actually SEE the captured current waveform.  Reads
        the I_mon physical channel from the scope's ``channel_aliases``
        mapping; silent no-op when no I_mon alias is configured (e.g.
        a 2-channel scope that's only displaying V_mon).
        """
        try:
            aliases = getattr(self.scope, "channel_aliases", {}) or {}
            imon_ch = aliases.get("imon")
            if not imon_ch:
                return
            # Pull the stimulator's actual I_mon scaling so the
            # vertical scale fits the real signal on NIL (1 mV/µA)
            # as well as Default (2.5 mV/µA) devices.
            stim_info = getattr(self.stim, "info", None)
            imon_v_per_ua = float(
                getattr(stim_info, "imon_scaling_v_per_ua", 0.0) or 0.0)
            self.scope.set_channel_scale(
                imon_ch,
                imon_vertical_scale(amp_ua,
                                    imon_v_per_ua=imon_v_per_ua or None))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def rescale_to_fit(self, acq, *, pattern, recapture,
                       timeout_s=None, context=""):
        """Iterative fit-the-view rescale loop — SHARED by every runner.

        Port of MATLAB ``getWaveform2.m`` / ``getWaveform3.m``: after a
        capture, check each mapped voltage role (V_mon / I_mon / E_ret /
        E_act) against the scope's visible window, coarse-/fine-rescale
        + position via ``adapt_channel_scale`` and
        ``compute_scale_position_targets``, re-capture, and repeat until
        every trace fits (bounded attempts).  Includes the percentile
        trim + stale-frame reject (gotchas #35/#40), baseline-centred
        E_ret/E_act positioning, asymmetric V_mon/I_mon positioning
        (#41), the fits-now no-coarsen gate, and the final in-view
        verification + per-attempt diagnostics.

        Extracted from VoltageTransient._one_capture so PS / SP / LP get
        the SAME scaling + positioning (operator request: "I want this
        same coarse/fine scaling (and positioning) for the other
        experiments").  On a converged, stationary signal the loop exits
        after ONE pass with zero scope writes and zero re-captures — so
        calling it per snapshot (SP/LP cadence) costs nothing extra.

        Parameters
        ----------
        acq:
            The just-taken acquisition; returned (possibly replaced by a
            re-capture at the final scope state).
        recapture:
            ``recapture(timeout_s) -> acq`` — how THIS experiment takes
            another averaged capture with stim still running.  VT passes
            settle+single_capture (gotcha #40); PS/SP/LP pass
            ``capture_while_running`` with their snapshot wait (its
            sleep IS the fresh-frame settle for that path).
        timeout_s:
            Pulse-rate-aware capture budget (``n_avg/rate + headroom``);
            forwarded to ``recapture``.
        context:
            Human-readable label for the diagnostic log lines, e.g.
            ``"at +50.0 µA (capture #1)"``.

        The stim must be RUNNING for the duration (the caller owns
        start/stop — see gotcha #10); this method touches ONLY the scope.
        """
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
        # MAX_FACTOR — "almost full ±N divs" margin, with 0.05 div
        # of leave-room (operator request: let the waveform use more
        # of the screen before flagging out-of-view).  MUST be
        # derived from the SCOPE'S actual vertical division count,
        # NOT a hardcoded number:
        #   * TBS1104B / TDS / TPS (8 vert divs): half = 4.0 →
        #     MAX_FACTOR = 3.95.
        #   * TBS2204B and the rest of the TBS2000* family (10 vert
        #     divs): half = 5.0 → MAX_FACTOR = 4.95.  Using the
        #     hardcoded 3.95 here would treat 21 % of the screen
        #     as off-limits and trip the "out of view" branch
        #     prematurely.
        # The scope's ``_half_vert_divs`` is set at connect time
        # from the per-series spec in tektronix_models.py (see
        # ``TektronixOscilloscope.open()`` step 5/6).  Simulators
        # inherit the conservative 4.0 default from base.
        _half_divs = float(getattr(self.scope, "_half_vert_divs", 4.0))
        MAX_FACTOR = max(0.5, _half_divs - 0.05)
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
        # the clip detector catch it and expand.
        #
        # I_mon divs_budget is 4 (was 3 — operator: "I_mon is too
        # small").  Unlike V_mon, I_mon (a controlled-current
        # monitor) has NO big compliance-switching transient, so it
        # doesn't need V_mon's extra headroom and CAN fill more of
        # the screen.  The grid-snap interaction is the real reason
        # 3 looked small: ``adapt`` ceils V/div onto the 1-2-5 grid,
        # and for a small NIL-preset I_mon (~±60 mV) the ideal at
        # divs=3 (≈21 mV) snapped UP to 50 mV → only ~2.5 div fill;
        # at divs=4 the ideal (≈16 mV) snaps to 20 mV → ~6 div fill.
        _voltage_roles = ("vmon", "imon", "eret", "eact")
        _divs_for = {"vmon": 3.0, "imon": 4.0,
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
                f"{[_role_disp(r) for r in _voltage_roles if aliases_obs.get(r)]}")
            # Pre-loop defaults so the post-loop in-view verify can reference
            # them even if the loop breaks at entry (abort requested).
            _needs_recapture = False
            _fill_write_roles = set()
            for _attempt in range(MAX_RECAPTURE + 1):
                # Abort-responsiveness check (Task #56): each
                # rescale-loop iteration involves a scope CURVe?
                # transfer that can take 3-5 s.  Checking abort
                # between iterations cuts worst-case Stop latency
                # from MAX_RECAPTURE+1 iterations (~15 s) down to
                # one iteration (~5 s).  We can't interrupt the
                # scope SCPI in flight, but we can decline to
                # start the next one.
                if self._abort_requested:
                    break
                _any_rescaled = False
                # ``_needs_recapture`` gates the costly verify re-capture
                # (a full averager settle — the dominant VT-max time sink at
                # high n_avg / low pulse rate).  It is set True ONLY when a
                # role OVERFLOWED (clip / out-of-view) and adapt GREW the
                # V/div: that grow is an extrapolation whose result MUST be
                # re-verified against a fresh capture.  A FILL-ONLY change (a
                # downscale to better-fill the screen, or a cosmetic recentre
                # on an already in-view + not-clipped trace) is applied to the
                # scope but does NOT require a re-capture: the saved ``acq`` is
                # still valid in-view data, and the finer scale only benefits
                # the NEXT capture (which reseeds anyway).  Operator profiling
                # of a 16-ch max run: 132 of 162 scale changes were fill-only,
                # each firing a needless multi-second settle.  ``_fill_write_
                # roles`` remembers which roles got such an apply-without-
                # recapture write this pass so the post-loop in-view verify
                # trusts their (known in-view) pre-write state instead of
                # re-checking the pre-write ``acq`` against the finer live
                # scale (which would false-warn "STILL OUT-OF-VIEW").
                _needs_recapture = False
                _fill_write_roles = set()
                _chan_data = getattr(acq, "channels", {}) or {}
                # NOTE: ``acq.time_us`` is a numpy array — DON'T write
                # ``... or []``: ``bool(np.ndarray)`` with >1 element
                # raises ``ValueError: truth value of an array is
                # ambiguous``, which (being OUTSIDE the per-role inner
                # try) aborted the ENTIRE rescale loop on the very
                # first capture, so V_mon/E_act/E_ret were never
                # rescaled (left at the 1000 mV/div default).  Use an
                # explicit None test.
                _t_raw = getattr(acq, "time_us", None)
                _t_us_arr = _np.asarray(
                    [] if _t_raw is None else _t_raw,
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
                    # Robust SIZING range.  The absolute array min/max
                    # sized V/div for the brief V_mon compliance-
                    # switching transients (±1.5 V spikes at the phase
                    # boundaries vs the ±150 mV settled plateau), so the
                    # loop OSCILLATED 50↔1000 mV/div and never converged
                    # ("STILL OUT-OF-VIEW").  Two guards:
                    #   1) PERCENTILE — drop the briefest ~0.5 % of
                    #      samples at each extreme so the scale sizes for
                    #      the settled signal; the sub-µs transients clip
                    #      slightly.
                    #   2) STALE-FRAME REJECT — if the trimmed result is
                    #      STILL physically impossible at the current
                    #      V/div (a frame from a coarser scale read
                    #      before the rescale settled), skip this role
                    #      and keep the current scale.
                    # Clip detection (_clipped_arr) and the pre-trigger
                    # baseline still use the full array ``_a``.
                    _lo = float(_np.percentile(_a, _RESCALE_TRIM_PCT))
                    _hi = float(_np.percentile(
                        _a, 100.0 - _RESCALE_TRIM_PCT))
                    _win = (
                        self.scope._channel_screen_window_v(_ch_name)
                        if hasattr(self.scope,
                                   "_channel_screen_window_v")
                        else None)
                    if _win is not None:
                        _wc, _whalf = _win
                        _wlimit = _RESCALE_STALE_FACTOR * _whalf
                        if (abs(_lo - _wc) > _wlimit
                                or abs(_hi - _wc) > _wlimit):
                            _diag_lines.append(
                                f"    {_role_disp(_role)} ({_ch_name}): rejected "
                                f"impossible read [{_lo*1e3:+.0f}, "
                                f"{_hi*1e3:+.0f}] mV at current V/div "
                                f"(stale frame from a coarser scale) "
                                f"— keeping current scale")
                            continue
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
                    # Baseline-centering needs the full pre-trigger
                    # array + time axis to robustly locate the rest
                    # potential under the pulse spikes.
                    #
                    # GUARDED on an idle interpulse existing: the pre-trigger
                    # (t < -1 µs) window is the electrode REST potential ONLY
                    # when the period has an idle gap.  With no interpulse
                    # (period fully occupied by the pulse) those samples are
                    # the PRIOR pulse's tail, not OCP — centering on them puts
                    # the trace off-screen (operator: "no interpulse delay →
                    # E_ret/E_act are never near zero during interpulse").  Fall
                    # back to the raw observed-range midpoint (adapt / position
                    # centering) in that case.
                    if (_role in ("eret", "eact")
                            and _a is not None
                            and _t_us_arr.size == _a.size
                            and getattr(pattern, "has_interpulse_gap",
                                        lambda: True)()):
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
                                    f"out-BOTH(±{MAX_FACTOR:.2f}div, "
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
                                    f"out-?(±{MAX_FACTOR:.2f}div)")
                        else:
                            _in_view_str = (
                                f"out-of-view(±{MAX_FACTOR:.2f}div)")
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
                    _overflowed = bool(_is_clipped) or (_in_view is False)
                    if _overflowed:
                        # Observed range is a lower bound on the
                        # true range.  Doubling biases ``adapt``
                        # toward a larger size — and the
                        # ``force_grow`` flag below GUARANTEES the
                        # grow fires (the fits-now veto is bypassed,
                        # because this doubled range is an
                        # extrapolation, not a faithful observation:
                        # an offset-driven clip can rail one side
                        # while the offset-blind half-range still
                        # "fits" — adversarial finding on E_act at
                        # a ~800 mV rest potential).
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
                    # Scale BEFORE adapt runs.  Prefer the confirmed-value
                    # cache (``_cached_scale_pos`` — what we last WROTE, the
                    # current scale until adapt changes it) so a converged
                    # pass costs no CHx:SCAle? round-trip (Opt #3).  Falls
                    # back to a live query on scopes without the cache.
                    _pre_adapt_vpd = None
                    try:
                        _csp = getattr(self.scope, "_cached_scale_pos", None)
                        if _csp is not None:
                            _pre_adapt_vpd = _csp(_ch_name)[0]
                        if _pre_adapt_vpd is None:
                            _pre_adapt_vpd = float(
                                self.scope._q(f"{_ch_name}:SCAle?"))
                    except Exception:
                        pass
                    _result = None
                    # A NON-overflow (fill) rescale DEFERS its
                    # re-capture-vs-fill-only decision until AFTER the
                    # coordinated-position path below has run — because that
                    # path can COARSEN ``_new_scale`` back UP (a DC-dominated
                    # role coarsens the swing-only V/div so the position
                    # offset fits ±5 div).  Deciding on adapt's raw swing-only
                    # pick (line ~2011) mis-reads a coarsen-back as a
                    # "significant shrink" → a needless verify re-capture on
                    # EVERY capture (the continuous-sinusoid DC-dominated
                    # thrash — 128/134 captures burned all 5 rescale iters in
                    # the exp_vt_max_cathodic_sin_cont run).  See the deferred
                    # re-evaluation after the coord path.
                    _fill_defer = False
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
                            # Clip / out-of-view → the doubled range
                            # is an extrapolation; the fits-now
                            # no-coarsen veto must not block the
                            # escape grow (see _overflowed above).
                            force_grow=_overflowed,
                        )
                        if _new_scale is not None:
                            _any_rescaled = True
                            if _overflowed:
                                # A GROW to escape a clip / out-of-view: the
                                # ×2-extrapolated size must be verified against
                                # a fresh capture → re-capture required.
                                _needs_recapture = True
                            else:
                                # A FILL-ONLY change on an already in-view +
                                # not-clipped trace.  DEFER the re-capture
                                # decision — the coord path below may coarsen
                                # ``_new_scale`` back up, so we must judge
                                # "minor vs significant" on the FINAL applied
                                # scale, not adapt's swing-only pick.  See the
                                # re-evaluation block after the coord path.
                                _fill_defer = True
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
                            # ---- shrink/grow detection (informational)
                            # ``_adapt_shrunk`` records whether adapt
                            # TIGHTENED (small signal) or grew / kept
                            # the V/div this iteration.  It is NO
                            # LONGER a gate on positioning — see below.
                            _adapt_shrunk = (
                                _pre_adapt_vpd is not None
                                and _new_scale is not None
                                and _new_scale < _pre_adapt_vpd)
                            # ---- E_ret / E_act position at ANY magnitude
                            # E_ret / E_act carry a DC REST POTENTIAL,
                            # so they need POSITION centring REGARDLESS
                            # of magnitude — a large swing on a
                            # non-zero rest potential still has to be
                            # centred or its baseline rides off-screen.
                            # Unlike the zero-centred V_mon / I_mon
                            # (where position 0 IS centred at any
                            # size), the bias-ratio helper therefore
                            # runs whether adapt shrank OR grew the
                            # V/div.  The ``bias_ratio`` inside the
                            # helper still decides offset-vs-zero and
                            # coarsens the V/div only when DC-dominated
                            # (R > 1) so the offset fits the ±5-div
                            # hardware limit.  (Revises the earlier
                            # "fine = small-magnitude only" skip, which
                            # wrongly applied the zero-centred rule to
                            # DC-biased roles and left a large-swing
                            # E_ret / E_act uncentred at the rest
                            # potential.)
                            if (_role in ("eret", "eact")
                                    and not _adapt_shrunk):
                                _result += " [large magnitude]"
                            if _role in ("eret", "eact"):
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
                                            grid=self.scope._vertical_grid_vpd,
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
                            elif _role == "vmon":
                                # V_mon is an AC signal, but ASYMMETRIC
                                # biphasic and TRIPHASIC pulses are NOT
                                # zero-centred — the excursions are
                                # lopsided, so the trace needs a
                                # POSITION offset to sit centred on
                                # screen (operator: "For asymmetric
                                # (and triphasic) waveforms, the
                                # vertical positioning must be
                                # adjusted as well").  Run the SAME
                                # bias_ratio-coordinated helper on the
                                # RAW observed range so the EXCURSION
                                # midpoint (min+max)/2 lands at screen
                                # centre.  For a SYMMETRIC biphasic the
                                # midpoint ≈ 0 → bias_ratio < 0.1 →
                                # AC-centered regime → pos = 0, so this
                                # is a NO-OP for the common case (and
                                # we skip the POSition write + its
                                # preamble-cache invalidation when
                                # |pos| is negligible).  Like the
                                # E_ret/E_act path, NOT gated on
                                # _adapt_shrunk — centring (when the
                                # signal is off-zero) is needed at any
                                # magnitude, not just on a shrink.
                                #
                                # I_MON IS DELIBERATELY EXCLUDED — it
                                # stays at POSition 0 ALWAYS (operator:
                                # "Make sure that Imon is always in
                                # vertical position 0 because that may
                                # be contributing to the weird offset
                                # that I am seeing in anodal first").
                                # ``update_imon_vertical_scale`` pins
                                # its position to 0 at every sizing, so
                                # with the rescale loop hands-off,
                                # POSition 0 is an INVARIANT for I_mon.
                                # If an asymmetric I_mon ever clips at
                                # 0, the clip detector's grow path
                                # still expands the V/div (the safety
                                # contract is untouched — only the
                                # cosmetic centring is skipped).
                                if _new_scale and _new_scale > 0:
                                    try:
                                        _targets = self.scope.compute_scale_position_targets(
                                            _target_lo, _target_hi,
                                            divs=_divs,
                                            grid=self.scope._vertical_grid_vpd,
                                            position_limit_divs=5.0,
                                        )
                                    except Exception:
                                        _targets = None
                                    if _targets is not None:
                                        (_coord_vpd, _coord_pos,
                                         _bias_ratio, _regime) = _targets
                                        # Coarsen V/div only if the
                                        # position would otherwise
                                        # clamp (strongly-asymmetric /
                                        # DC-dominated — rare for a
                                        # monitor channel).  Symmetric
                                        # pulses leave _coord_vpd ==
                                        # _new_scale.
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
                                        # Apply the offset ONLY when
                                        # it's non-negligible — a
                                        # symmetric pulse gives pos ≈ 0
                                        # and writing it would
                                        # needlessly invalidate the
                                        # preamble cache every capture.
                                        if abs(_coord_pos) > 0.05:
                                            try:
                                                self.scope.set_channel_position(
                                                    _ch_name, _coord_pos)
                                                _result += (
                                                    f", pos={_coord_pos:+.1f} div, "
                                                    f"R={_bias_ratio:.2f} "
                                                    f"({_regime}, asym)")
                                            except Exception:
                                                pass
                            # ---- deferred fill re-capture decision ----
                            # The coord path above may have COARSENED
                            # ``_new_scale`` back up (a DC-dominated role
                            # coarsens the swing-only V/div so the DC position
                            # fits ±5 div).  Judge minor-vs-significant on the
                            # FINAL applied scale, NOT adapt's swing-only pick:
                            #   * MINOR (final scale ≈ the pre-write scale — the
                            #     coarsen-back case, or a small tweak the
                            #     operator accepted) → FILL-ONLY, no verify
                            #     re-capture (the saved acq at the pre-write
                            #     scale is valid in-view data; the finer/coarser
                            #     scale only benefits the NEXT capture).
                            #   * SIGNIFICANT (adapt genuinely fine-fit a small
                            #     signal parked under a coarse seed, and coord
                            #     did NOT coarsen it back) → re-capture so the
                            #     saved acq isn't quantized at the coarse scale.
                            # This kills the continuous-sinusoid DC-dominated
                            # thrash where the coord path coarsened adapt's fine
                            # pick back to ≈ the pre-write scale yet the loop
                            # re-captured all MAX_RECAPTURE+1 iterations every
                            # capture (128/134 in exp_vt_max_cathodic_sin_cont).
                            if _fill_defer and not _overflowed:
                                _minor = False
                                if (_pre_adapt_vpd and _pre_adapt_vpd > 0
                                        and _new_scale and _new_scale > 0):
                                    _r = _new_scale / _pre_adapt_vpd
                                    _minor = (_RESCALE_FILL_SKIP_RATIO <= _r
                                              <= 1.0 / _RESCALE_FILL_SKIP_RATIO)
                                if _minor:
                                    _fill_write_roles.add(_role)
                                else:
                                    _needs_recapture = True
                        else:
                            # adapt returned None — either:
                            #   * already-on-grid (no change needed),
                            #   * hysteresis blocked the shrink,
                            #   * the fits-now gate kept a finer
                            #     fitting scale, or
                            #   * adapt is locked.
                            _result = "no change (already optimal)"
                            # ---- centring must NOT depend on a V/div
                            # change (adversarial finding on gotcha
                            # #41).  With the fits-now gate, adapt
                            # settles more often — but an in-view,
                            # unclipped trace can still sit materially
                            # off-centre (asymmetric pulse, DC-biased
                            # role at a stale position).  Apply the
                            # coordinated POSITION at the KEPT scale
                            # when the bias is real and the current
                            # position differs by > 0.25 div.  Faithful
                            # data only (never on clip / out-of-view —
                            # those take the force_grow path), and
                            # position-only: the V/div stays the
                            # fits-now winner.
                            #
                            # I_mon is EXCLUDED here too — POSition 0
                            # is an invariant for it (operator: "Make
                            # sure that Imon is always in vertical
                            # position 0"; see the vmon branch above
                            # for the full rationale).
                            if not _overflowed and _role != "imon":
                                try:
                                    _kept_vpd = _pre_adapt_vpd
                                    _targets = (
                                        self.scope.compute_scale_position_targets(
                                            _target_lo, _target_hi,
                                            divs=_divs,
                                            grid=self.scope._vertical_grid_vpd,
                                            position_limit_divs=5.0,
                                        )) if _kept_vpd else None
                                    if _targets is not None:
                                        (_c_vpd, _c_pos,
                                         _bias_ratio, _regime) = _targets
                                        # Re-express the target offset in
                                        # divisions of the KEPT scale
                                        # (the helper sized for its own
                                        # vpd pick, which we're not
                                        # applying).
                                        _mid = 0.5 * (_target_lo + _target_hi)
                                        _pos_kept = max(-5.0, min(
                                            5.0, -_mid / _kept_vpd))
                                        # Current position — prefer the
                                        # confirmed-value cache so the
                                        # converged "recentre?" check on a
                                        # stationary signal costs no
                                        # CHx:POSition? round-trip (Opt #3).
                                        _cur_pos = None
                                        try:
                                            _csp = getattr(
                                                self.scope,
                                                "_cached_scale_pos", None)
                                            if _csp is not None:
                                                _cur_pos = _csp(_ch_name)[1]
                                            if _cur_pos is None:
                                                _cur_pos = float(self.scope._q(
                                                    f"{_ch_name}:POSition?"))
                                        except Exception:
                                            pass
                                        if (_bias_ratio >= 0.1
                                                and _cur_pos is not None
                                                and abs(_pos_kept - _cur_pos)
                                                > 0.25):
                                            self.scope.set_channel_position(
                                                _ch_name, _pos_kept)
                                            _any_rescaled = True
                                            # Cosmetic recentre on an in-view
                                            # (not-overflowed) trace — position
                                            # is ADC-centering only (gotcha
                                            # #41: reconstructed values are
                                            # unaffected), so no verify
                                            # re-capture is needed.  Treat as a
                                            # fill-only write.
                                            _fill_write_roles.add(_role)
                                            _result = (
                                                f"recentre pos="
                                                f"{_pos_kept:+.1f} div "
                                                f"(scale kept, R="
                                                f"{_bias_ratio:.2f} "
                                                f"{_regime})")
                                except Exception:
                                    pass
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
                        f"{_role_disp(_role):>4s} ({_ch_name}): "
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
                if not _needs_recapture:
                    # Converged for re-capture purposes.  Either NO writes
                    # happened (``acq`` already reflects the scope), OR the
                    # only writes were FILL-ONLY (a downscale / cosmetic
                    # recentre on an already in-view + not-clipped trace —
                    # ``_fill_write_roles``).  In the fill-only case the saved
                    # ``acq`` is valid in-view data at the pre-write scale; the
                    # finer scale/position is set for the next capture but does
                    # NOT need a verify re-capture (that re-capture is a full
                    # averager settle — the dominant VT-max cost the operator
                    # asked to cut).  The grow-for-clip / out-of-view escape
                    # ALWAYS sets ``_needs_recapture`` above, so overflow
                    # captures are still re-verified.
                    if _fill_write_roles:
                        _diag_lines.append(
                            "  converged (fill-only writes applied to "
                            f"{sorted(_role_disp(r) for r in _fill_write_roles)}"
                            " — no verify re-capture; saved acq is valid "
                            "in-view data at the pre-write scale)")
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
                    # A ``CHx:SCAle`` write does NOT reset the scope's
                    # free-running NUMACq, so ``single_capture``'s plain
                    # ``NUMACq >= n_avg`` poll would exit IMMEDIATELY on
                    # a STALE averaged frame still accumulated at the OLD
                    # V/div — whose reconstructed magnitude is inflated
                    # by exactly (old/new V/div).  That stale read makes
                    # the rescale loop oscillate and freeze at a too-fine
                    # scale ("vertical not adjusted").  The runner's
                    # ``recapture`` waits for n_avg FRESH frames at the
                    # NEW scale FIRST (``settle_one_acquisition`` is
                    # entry-anchored per gotcha #33) before transferring
                    # the now-fresh ``CURVe?`` (gotcha #40).
                    acq = recapture(timeout_s)
                except Exception as e:
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session,
                        message=(f"⚠ Re-capture after rescale failed "
                                 f"{context} (attempt "
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
        # (The rescale loop re-captures the full waveform on every
        # iteration including the last, so ``acq`` already holds the
        # final, correctly-scaled arrays here — no extra transfer
        # needed before make_capture.)
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
                        f"    {_role_disp(_role):>4s} ({_ch_name}): "
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
                    # Verify against the SETTLED signal, not the
                    # absolute min/max — same trim the sizing loop
                    # uses.  The brief compliance-switching transient
                    # (±1.5 V spikes at the phase boundaries) is
                    # EXPECTED to clip slightly and must NOT trip a
                    # "STILL OUT-OF-VIEW" warning when the real signal
                    # fits (operator: "why did it stop / say
                    # out-of-view when it's scaled fine").
                    _vlo = float(_np.percentile(_a, _RESCALE_TRIM_PCT))
                    _vhi = float(
                        _np.percentile(_a, 100.0 - _RESCALE_TRIM_PCT))
                    # A role whose LAST write was fill-only (applied without a
                    # verify re-capture) has a saved ``acq`` at the PRE-write
                    # scale — which was in-view by definition (that's why the
                    # change was fill-only, not an overflow escape).  The live
                    # ``_final_vpd`` read back here is the finer post-write
                    # scale, so checking the pre-write ``acq`` against it would
                    # false-warn "STILL OUT-OF-VIEW".  Trust the known in-view
                    # state instead.  (``not _needs_recapture`` confirms we
                    # exited via the fill-only break, not a re-capture path
                    # where ``acq`` IS fresh at ``_final_vpd``.)
                    if (not _needs_recapture) and (_role in _fill_write_roles):
                        _verify_str = (
                            f"  ✓ fits (in-view; fill-only write, "
                            f"not re-captured)")
                    elif (_np.isfinite(_vlo)
                            and _np.isfinite(_vhi)
                            and _final_vpd > 0):
                        _vp_v = -_final_pos * _final_vpd
                        _win_lo = -MAX_FACTOR * _final_vpd + _vp_v
                        _win_hi = +MAX_FACTOR * _final_vpd + _vp_v
                        _fits = (_vlo > _win_lo
                                 and _vhi < _win_hi)
                        if _fits:
                            _verify_str = (
                                f"  ✓ fits in ±{MAX_FACTOR:.2f}div")
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
                    f"    {_role_disp(_role):>4s} ({_ch_name}): "
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
                    f"is still out-of-view (±{MAX_FACTOR:.2f}div) "
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
                    message=(f"In-view rescale loop {context}:\n"
                             + "\n".join(_diag_lines))))
        except Exception:
            pass
        return acq

    def measure_electrode_dc_offsets_and_switch_to_ac(
            self, recapture, *, roles=("eret", "eact"),
            settle_sd_v=None) -> None:
        """Capture the DC rest potential of E_ret / E_act, then AC-couple them.

        Operator: "Let's try AC coupled after capturing the offset from DC
        coupled."  A DC-biased electrode potential — e.g. E_ret at +248 mV
        rest with only an ±8 mV pulse swing — can't be fine-scaled while
        DC-coupled: to keep the +248 mV bias on-screen the vertical POSition
        (±5-div hardware limit) forces a coarse V/div (gotcha #13), so the
        swing renders nearly flat.  Called at the start of each channel (VT)
        / run (PS·SP·LP), this helper:

          1. DC-couples each mapped E_ret / E_act channel and takes ONE
             capture (``recapture()`` returns a ``ScopeAcquisition``).
          2. Extracts the DC rest potential (``per_capture_baseline`` —
             leading-edge / pre-trigger mean, robust to the pulse spikes)
             and the swing about it.
          3. **DC-dominated gate** (operator: "the summation of data from DC
             coupled and AC coupled should be done for small magnitude
             waveforms like Eret in monopolar … waveforms that require fine
             scaling and positioning").  Only when the bias/swing ratio
             ``R = |rest potential| / swing`` exceeds
             ``_DC_TO_AC_BIAS_RATIO`` (a small swing on a large DC bias, so
             the ±5-div POSition limit blocks fine-scaling) does it proceed;
             a large / near-zero-biased swing (R ≤ threshold) stays
             DC-coupled with its true absolute level.
          4. For a DC-dominated channel: records the rest potential in
             ``self._electrode_dc_offset[role]`` (the runner threads that
             dict into ``make_capture`` so the AC captures carry the absolute
             level BACK at the fine swing resolution — best of both), then
             AC-couples so the swing centres at 0 and the rescale loop can
             fine-scale it, and resets that channel's adapt state so the AC
             scale is hunted fresh (DC left it coarse).

        ``recapture`` is the runner's own fresh-frame capture callable (the
        SAME one it hands ``rescale_to_fit`` — VT: settle + single_capture;
        PS/SP/LP: capture_while_running), so the measured frame is settled.
        No-op without a scope or a coupling setter (simulator).  Never raises.
        """
        import numpy as _np
        scope = self.scope
        if scope is None or not hasattr(scope, "set_channel_coupling"):
            return
        # NO-INTERPULSE guard: the whole DC→AC trick hinges on a settled idle
        # interpulse window — to MEASURE the rest potential (leading-edge
        # baseline) and to VERIFY the AC coupling settled (both interpulse
        # regions flat, SD ≤ 5 mV).  With the period fully occupied by the
        # pulse there is no idle window: the "rest" reading is prior-pulse
        # tail and the SD-flatness accept can never pass (it would exhaust the
        # retries then force-accept a wrong add-back).  Skip the trick and
        # leave E_ret/E_act DC-coupled (operator: "no interpulse delay →
        # E_ret/E_act are never near zero during interpulse").
        _pat = getattr(getattr(self.session, "test", None), "pattern", None)
        if _pat is not None and hasattr(_pat, "has_interpulse_gap") \
                and not _pat.has_interpulse_gap():
            self._electrode_dc_offset = {}
            return
        aliases = getattr(scope, "channel_aliases", {}) or {}
        # Reset so a re-run / next channel re-measures from scratch — a stale
        # offset from the previous electrode would corrupt the add-back.
        self._electrode_dc_offset = {}
        for role in roles:
            ch = aliases.get(role)
            if not ch:
                continue
            try:
                # ---- 1. DC-couple + capture -----------------------------
                # DC-couple so we can BOTH measure the rest potential AND
                # decide whether this waveform even NEEDS the DC→AC trick.
                scope.set_channel_coupling(ch, "DC")
                acq = recapture()
                if acq is None:
                    continue
                arr = getattr(acq, "channels", {}).get(ch)
                if arr is None:
                    continue
                t_us = getattr(acq, "time_us", None)
                a = _np.asarray(arr, dtype=float)
                a = a[_np.isfinite(a)]
                if a.size < 8:
                    continue
                # ---- 2. rest potential + swing --------------------------
                baseline = per_capture_baseline(
                    _np.asarray(arr, dtype=float),
                    _np.asarray(t_us) if t_us is not None else None)
                # Swing about the baseline, percentile-trimmed to reject the
                # brief switching transients (same trim the rescale uses).
                _lo = float(_np.percentile(a, _RESCALE_TRIM_PCT))
                _hi = float(_np.percentile(a, 100.0 - _RESCALE_TRIM_PCT))
                swing = max(abs(_hi - baseline), abs(baseline - _lo))
                # ---- 3. DC-dominated gate -------------------------------
                # R = |rest potential| / swing.  ONLY a small swing on a
                # large DC bias NEEDS DC→AC (operator: "small magnitude
                # waveforms like Eret in monopolar … that require fine
                # scaling and positioning").  A large / near-zero-biased
                # swing (R ≤ threshold) fine-scales fine while DC-coupled and
                # keeps its true absolute level — leave it DC.
                R = (abs(baseline) / swing) if swing > 1e-12 else float("inf")
                if not (_np.isfinite(R) and R > _DC_TO_AC_BIAS_RATIO):
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session,
                        message=(
                            f"[scope] {_role_disp(role)} ({ch}) bias/swing "
                            f"R={R:.2f} ≤ {_DC_TO_AC_BIAS_RATIO:g} — keeping "
                            f"DC coupling (swing fine-scales without AC).")))
                    continue
                # ---- 4. DC-dominated → record offset + AC-couple --------
                if _np.isfinite(baseline):
                    self._electrode_dc_offset[role] = float(baseline)
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session,
                        message=(
                            f"[scope] {_role_disp(role)} ({ch}) DC rest potential "
                            f"= {baseline*1e3:+.1f} mV, swing ±{swing*1e3:.1f} "
                            f"mV (R={R:.0f}, DC-dominated) — AC-coupling so the "
                            f"swing fine-scales; offset summed back on save.")))
                scope.set_channel_coupling(ch, "AC")
                reset_fn = getattr(scope, "reset_adapt_state", None)
                if callable(reset_fn):
                    reset_fn(ch)
                # ---- ACCEPT when BOTH interpulse regions are FLAT ---------
                # DC→AC leaves the coupling cap charged to the OLD DC rest
                # level; it bleeds off through the AC high-pass over several
                # RC time constants.  The steady state is the DC FULLY removed
                # → the interpulse baseline (flat, BETWEEN pulses) both BEFORE
                # and AFTER the pulse is FLAT.  While the cap is still
                # discharging, that baseline DRIFTS = a high standard
                # deviation.  Re-capture and ACCEPT only once the SD of BOTH
                # the leading AND trailing interpulse ≤ _AC_INTERPULSE_SD_V
                # (operator: "recapture until the interpulse before and after
                # the pulse both have SD < 5 mV").  Self-calibrates to any RC /
                # DC level AND confirms the averager has flushed the settling
                # frames.  A short head-start sleep lets the RC begin
                # discharging before the first averaged window.
                # Acceptance SD threshold — the caller passes a LENIENT value at
                # ~0 µA (operator #5), where the trace is the pure noise floor
                # and the strict 5 mV flatness accept can't reliably pass.
                _sd_thresh = (float(settle_sd_v) if settle_sd_v
                              else _AC_INTERPULSE_SD_V)
                self.abort_sleep(_AC_COUPLING_SETTLE_S)
                _sdb = _sda = float("nan")
                for _chk in range(_AC_SETTLE_MAX_CHECKS):
                    if self.aborted:
                        break
                    _acq2 = recapture()
                    if _acq2 is None:
                        continue
                    _sdb, _sda = self._interpulse_sds(_acq2, ch)
                    _ok = (_np.isfinite(_sdb) and _np.isfinite(_sda)
                           and _sdb <= _sd_thresh
                           and _sda <= _sd_thresh)
                    if _ok:
                        self._emit(ExperimentEvent(
                            kind="log", session=self.session,
                            message=(
                                f"[scope] {_role_disp(role)} ({ch}) AC interpulse "
                                f"settled: SD before={_sdb*1e3:.2f} mV, "
                                f"after={_sda*1e3:.2f} mV "
                                f"(≤ {_sd_thresh*1e3:.0f} mV) after "
                                f"{_chk + 1} check(s) — accepted.")))
                        break
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session,
                        message=(
                            f"[scope] {_role_disp(role)} ({ch}) AC interpulse SD "
                            f"before={_sdb*1e3:.2f} mV, after={_sda*1e3:.2f} mV, "
                            f"still settling (check "
                            f"{_chk + 1}/{_AC_SETTLE_MAX_CHECKS}).")))
                else:
                    self._emit(ExperimentEvent(
                        kind="log", session=self.session,
                        message=(
                            f"[scope] ⚠ {_role_disp(role)} ({ch}) AC interpulse SD "
                            f"still before={_sdb*1e3:.2f} mV / after={_sda*1e3:.2f} "
                            f"mV after {_AC_SETTLE_MAX_CHECKS} checks — "
                            f"accepting anyway.")))
            except Exception:
                pass

    def _interpulse_sds(self, acq, ch):
        """``(sd_before, sd_after)`` volts — the standard deviation of the
        interpulse (flat baseline) regions BEFORE and AFTER the pulse on
        channel ``ch``.

        Used by the DC→AC accept loop (operator: "recapture until the
        interpulse before and after the pulse both have SD < 5 mV").  Once
        AC coupling has settled, both interpulse baselines are FLAT (low SD);
        while the coupling cap is still discharging they DRIFT (high SD).

        The PULSE SPAN is located from V_mon (its large excursion), so the
        split works for any trigger position (digital-sync onset≈0 OR the
        I_mon mid-record fallback).  Falls back to the pre-trigger region
        (``t < 0``, before) + the record TAIL (after) when V_mon isn't in the
        acquisition.  Returns ``(nan, nan)`` on missing / too-short data (the
        caller then keeps re-capturing, accepting anyway after the max checks).
        """
        import numpy as _np
        chans = getattr(acq, "channels", {}) or {}
        er = chans.get(ch)
        if er is None:
            return float("nan"), float("nan")
        er = _np.asarray(er, dtype=float)
        n = er.size
        if n < 40:
            return float("nan"), float("nan")
        # Locate the pulse span from V_mon (the largest excursion).
        aliases = getattr(self.scope, "channel_aliases", {}) or {}
        vm = chans.get(aliases.get("vmon"))
        lo = hi = None
        if vm is not None:
            vm = _np.asarray(vm, dtype=float)
            if vm.size == n and _np.isfinite(vm).any():
                med = _np.nanmedian(vm)
                p2p = (_np.nanpercentile(vm, 99)
                       - _np.nanpercentile(vm, 1))
                if p2p > 1e-9:
                    idx = _np.flatnonzero(_np.abs(vm - med) > 0.1 * p2p)
                    if idx.size:
                        lo, hi = int(idx[0]), int(idx[-1])
        if lo is None:
            # Fallback: pre-trigger before, record tail after.
            t = getattr(acq, "time_us", None)
            if t is not None and _np.size(t) == n:
                t = _np.asarray(t, dtype=float)
                _span = t.max() - t.min()
                before = er[t < 0.0]
                after = er[t > (t.max() - 0.25 * _span)]
            else:
                before = er[:n // 8]
                after = er[-(n // 4):]
        else:
            m = max(2, n // 100)          # small guard margin around the pulse
            before = er[:max(0, lo - m)]
            after = er[min(n, hi + m):]

        def _sd(a):
            a = a[_np.isfinite(a)]
            return float(_np.std(a)) if a.size >= 8 else float("nan")
        return _sd(before), _sd(after)

    def apply_default_scope_view(self, pattern, *, amp_ua: Optional[float] = None,
                                 is_multipolar: bool = False,
                                 environment_short: Optional[str] = None,
                                 reason: str = "channel change") -> None:
        """Revert the scope to the per-channel default view.

        Faithful port of MATLAB ``setDefaultScopeView3.m``: every time
        the monitor channel changes (and at run start), we re-run the
        full default-view pass so the new channel starts from a known-
        good window instead of inheriting whatever scale/position the
        previous channel's adaptive loop converged on.

        The MATLAB sequence is:

          1. Pick horizontal scale from a candidate timebase grid
             (smallest where pulse / window ≥ 0.4) and write
             HORizontal position then HORizontal scale.
          2. Open vertical-bar cursors at phase-1 end and phase-1 + a
             depolarization gap (the MATLAB ``depolTime`` rule depends
             on phase1 / interphase magnitudes).
          3. Set V_mon vertical scale — default 200 mV/div, but special
             cases for very-short or very-wide phase patterns.
          4. Set I_mon vertical scale via ``setCurrentScale`` (our
             :func:`imon_vertical_scale`).
          5. Set trigger level via ``setTriggerLevel2`` (our
             :func:`imon_trigger_level`), skipped on EXT.

        We also reset the per-channel ``adapt_channel_scale`` history
        so the autorange hunts freshly for this channel.

        Silent no-op if the scope is missing any of the hooks (older
        mock scopes pass through without complaint).
        """
        if self.scope is None or pattern is None:
            return
        scope = self.scope
        try:
            # ---- 0. Wipe adapt history ---------------------------------
            reset_fn = getattr(scope, "reset_adapt_state", None)
            if callable(reset_fn):
                reset_fn(None)

            phases = getattr(pattern, "phases", None) or []
            if not phases:
                return
            ph1 = phases[0]
            ph2 = phases[1] if len(phases) > 1 else None
            phase1_us = float(getattr(ph1, "width_us", 0.0))
            interphase_us = float(getattr(ph1, "delay_after_us", 0.0))
            phase2_us = float(getattr(ph2, "width_us", 0.0)) if ph2 else 0.0
            discharge_us = float(getattr(ph2, "delay_after_us", 0.0)) if ph2 else 0.0

            # ---- 1. Horizontal layout ----------------------------------
            layout_fn = getattr(scope, "auto_layout_for_pulse", None)
            scale_s = None
            pos_pct = None
            if callable(layout_fn):
                try:
                    scale_s, pos_pct = layout_fn(
                        phase1_us=phase1_us,
                        interphase_us=interphase_us,
                        phase2_us=phase2_us,
                        discharge_us=discharge_us,
                        # Any TTL sync line (EXT BNC or a channel
                        # tagged Role=Trigger) carries the same
                        # 1.2 µs Plexon digital-delay; only I_mon
                        # channel triggers skip the offset.
                        ext_trigger=self.trigger_is_digital,
                    )
                except Exception:
                    pass

            aliases = getattr(scope, "channel_aliases", {}) or {}
            vmon_ch = aliases.get("vmon", "CH1")
            imon_ch = aliases.get("imon", "CH2")

            # ---- 2. Cursors --------------------------------------------
            # MATLAB depolTime rules from setDefaultScopeView3.m:
            #   phase1 >= 100 µs        → depolTime = 12 µs
            #   interphase > 10 µs      → depolTime = 6.5 µs
            #   interphase == 0         → depolTime = 0 µs
            #   else                    → depolTime = interphase / 2
            if phase1_us >= 100:
                depol_us = 12.0
            elif interphase_us > 10:
                depol_us = 6.5
            elif interphase_us == 0:
                depol_us = 0.0
            else:
                depol_us = interphase_us / 2.0
            # Cursor source: prefer the electrode-potential channel
            # (E_act, then E_ret); fall back to V_mon when neither is
            # mapped — operator spec: "set the cursors to V_mon if E_act
            # or E_ret are not available."
            _cursor_ch = (aliases.get("eact")
                          or aliases.get("eret")
                          or vmon_ch)
            try:
                cursors_fn = getattr(scope, "set_cursors", None)
                if callable(cursors_fn):
                    cursors_fn(phase1_us, depol_us, source_channel=_cursor_ch)
            except Exception:
                pass

            # ---- 3. Voltage vertical scale — COARSE initial -------------
            # Direct port of MATLAB ``setOscillocopeView.m`` lines
            # 300-382.  Picks a generous V/div that's GUARANTEED not
            # to clip across the expected amplitude range for this
            # phase-width regime + configuration + environment.  The
            # post-capture fine-scaler in the runner (range+mean port
            # of ``setFineScalePos2.m``) tightens this on the first
            # observation; the only job of this block is to fit the
            # *first* capture's signal on screen even when the load
            # is unknown.
            #
            # MATLAB rule (full 4-case decision tree, line 300-317):
            #
            #   phase1 >= 100 µs:
            #     isMP                          → 1.0 V/div
            #     multipolar (BP/TP/CG/PBP/PTP) → 2.0 V/div   ← NEW
            #   phase1 <  100 µs:
            #     environment ∋ "A" (animal)    → 2.0 V/div   ← NEW
            #     isMP                          → 0.2 V/div
            #     multipolar                    → 0.5 V/div   ← NEW
            #
            # ``interphase == 0`` → 200 µV/div  (degenerate / very-short
            # asymmetric pattern marker; not in MATLAB but useful for
            # patterns where the leading edge is essentially a single
            # phase with no companion).
            env_is_animal = bool(
                environment_short
                and str(environment_short).strip().upper().startswith("A")
            )
            if phase1_us >= 100:
                vmon_vpd = 2.0 if is_multipolar else 1.0
            elif interphase_us == 0:
                vmon_vpd = 200e-6
            elif env_is_animal:
                # Animal-environment short-phase patterns sit on noisier
                # ground; MATLAB picks 2 V/div across the board for both
                # MP and multipolar so the leading edge isn't lost in
                # baseline drift.
                vmon_vpd = 2.0
            else:
                vmon_vpd = 0.5 if is_multipolar else 0.2

            # Apply the V/div + position=0 to EVERY voltage channel
            # the scope is mapped to — V_mon, E_act, E_ret — not just
            # V_mon. MATLAB ``setOscillocopeView.m`` line 322 loops
            # over ``voltageChannel_cell`` and writes the SAME scale +
            # position to all of them. Previously only V_mon got the
            # default-view write, so E_act / E_ret carried whatever
            # V/div the previous channel's fine-scaler converged on
            # (often too tight, clipping the new channel's first
            # capture).
            voltage_roles = ("vmon", "eact", "eret")
            for _role in voltage_roles:
                _ch = aliases.get(_role)
                if not _ch:
                    continue
                try:
                    scope.set_channel_scale(_ch, vmon_vpd)
                    scope.set_channel_position(_ch, 0.0)
                except Exception:
                    pass

            # ---- 4. I_mon vertical scale -------------------------------
            # Use FIRST PHASE (``phases[0]``) — not ``excitation_phase``
            # — for trigger-related amplitude reads.  The scope trigger
            # fires on whichever phase comes first in time; on patterns
            # where ``phases[0]`` ≠ ``excitation_phase`` (anodic-first,
            # certain triphasic shapes) the slope and level derivations
            # both need the phase-1 amplitude, not the excitation
            # phase's.
            _ph0 = (pattern.phases[0]
                    if getattr(pattern, "phases", None) else None)
            if amp_ua is None:
                amp_ua = (abs(float(getattr(_ph0, "amplitude_ua", 0.0)))
                          if _ph0 is not None else 0.0)
            amp_ua = abs(float(amp_ua)) or 1.0
            self.update_imon_vertical_scale(amp_ua)
            try:
                scope.set_channel_position(imon_ch, 0.0)
            except Exception:
                pass

            # ---- 4b. Trigger channel vertical scale + position ---------
            # The Role=Trigger digital-sync channel (e.g. CH4) carries a
            # TTL (0 → ~3.3/5 V), NOT a measured analog signal — so it
            # does NOT go through the adaptive in-view rescale loop.  Give
            # it a FIXED scale + position so the 0→high edge sits clearly
            # on-screen (operator spec: "set the correct vertical scaling
            # and position for the trigger channel").  Read back to verify
            # the writes actually took (getter-after-setter check).
            _trig_ch = aliases.get("trigger")
            if _trig_ch and str(_trig_ch).upper().startswith("CH"):
                _TRIG_VPD = 1.0    # 1 V/div: a 3.3-5 V TTL spans 3-5 div
                _TRIG_POS = -2.0   # 0 V at -2 div → rising edge sweeps up
                try:
                    scope.set_channel_scale(_trig_ch, _TRIG_VPD)
                    scope.set_channel_position(_trig_ch, _TRIG_POS)
                    _q = getattr(scope, "_q", None)
                    if callable(_q):
                        _rb_s = float(scope._q(f"{_trig_ch}:SCAle?"))
                        _rb_p = float(scope._q(f"{_trig_ch}:POSition?"))
                        if (abs(_rb_s - _TRIG_VPD) > 1e-3
                                or abs(_rb_p - _TRIG_POS) > 0.05):
                            self._emit(ExperimentEvent(
                                kind="log", session=self.session,
                                message=(
                                    f"⚠ trigger channel {_trig_ch} "
                                    f"scale/position read-back mismatch: got "
                                    f"{_rb_s:.3f} V/div @ {_rb_p:+.2f} div, "
                                    f"wanted {_TRIG_VPD:.3f} V/div @ "
                                    f"{_TRIG_POS:+.2f} div")))
                except Exception:
                    pass

            # ---- 5. Trigger level (skipped on EXT) ---------------------
            # Pull the SIGNED amplitude from ``phases[0]`` so the I_mon
            # trigger level inherits the sign of the leading edge the
            # scope actually sees.
            if _ph0 is not None:
                self.update_imon_trigger_level(
                    float(getattr(_ph0, "amplitude_ua", -amp_ua)),
                    phase_width_us=float(getattr(_ph0, "width_us", phase1_us)))

            # ---- log summary -------------------------------------------
            _hs = f"{scale_s*1e6:.2f} µs/div" if scale_s else "—"
            _hp = f"{pos_pct:.1f} %" if pos_pct is not None else "—"
            # Which voltage roles actually got the default write — for
            # the log the operator reads in the GUI.
            _v_chans = ", ".join(
                f"{aliases[r]} ({r.upper()})"
                for r in ("vmon", "eact", "eret") if aliases.get(r))
            _config_tag = "multipolar" if is_multipolar else "monopolar"
            _env_tag = ("animal" if env_is_animal
                        else (environment_short or "—"))
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=(
                    f"Scope reverted to default view ({reason}): "
                    f"H = {_hs} @ {_hp},  "
                    f"voltage channels [{_v_chans}] = "
                    f"{vmon_vpd*1e3:.0f} mV/div @ pos 0 div "
                    f"(rule: {_config_tag}, phase1={phase1_us:.0f} µs, "
                    f"env={_env_tag}),  "
                    f"{imon_ch} (I_mon) sized for {amp_ua:.0f} µA,  "
                    f"cursors @ {phase1_us:.1f} µs / "
                    f"{phase1_us + depol_us:.1f} µs (depol = {depol_us:.1f} µs).")))
        except Exception as e:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=f"apply_default_scope_view skipped: {e}"))

    @property
    def aborted(self) -> bool:
        return self._abort_requested

    # ----- abort-aware sleep --------
    def abort_sleep(self, seconds: float, *,
                    chunk_s: float = 0.05) -> bool:
        """Sleep ``seconds`` total in ``chunk_s`` chunks, checking the
        abort flag between chunks.  Returns True if the sleep ran to
        completion; False if the abort flag tripped partway through.

        Use anywhere a runner previously did ``time.sleep(N)`` for
        seconds-scale intervals — replacing with ``abort_sleep`` keeps
        the Stop button responsive within ``chunk_s`` (default 50 ms)
        rather than after the full sleep.

        Sub-chunk sleeps (``seconds < chunk_s``) sleep once for the
        full duration and check abort once at the end.  Negative /
        zero ``seconds`` is a no-op that just checks abort.
        """
        import time as _time
        if seconds <= 0:
            return not self._abort_requested
        if seconds < chunk_s:
            _time.sleep(seconds)
            return not self._abort_requested
        deadline = _time.monotonic() + seconds
        while True:
            if self._abort_requested:
                return False
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return True
            _time.sleep(min(chunk_s, remaining))

    # ----- user pause / resume --------
    def pause(self, paused: bool) -> None:
        """Request PAUSE (``True``) or RESUME (``False``) — called from the GUI
        thread.  Pausing sets a flag the WORKER thread honours at its next
        :meth:`wait_if_paused` checkpoint (where it halts stimulation and
        holds); resuming sets ``_resume_event`` to release that checkpoint.

        Only a bool + a threading.Event cross the thread boundary here — every
        actual stim call stays on the worker thread (single-producer, no DLL
        race), so this can't corrupt the PlexStim heap the way a cross-thread
        stop would.
        """
        self._pause_requested = bool(paused)
        if not paused:
            self._resume_event.set()

    @property
    def paused(self) -> bool:
        return self._pause_requested

    def start_pulsing(self) -> bool:
        """Bring stimulation up — UNLESS the run is aborted or paused.

        **The single gate every runner must go through to start pulsing.**

        Operator: "when stopping an experiment, all pulsing must stop … that
        includes pausing."  Stop and Pause halt the DEVICE straight away from
        the GUI thread (``PS_AbortAll``), but that only kills the pulse in
        flight — the worker thread is off doing a capture or an averager
        settle and, when it comes back, its next step would happily call
        ``start_all()`` and resume stimulating an electrode the operator
        believes is quiet.  7 of the 9 ``start_all()`` sites had no guard.

        Routing every start through here closes that window: once aborted or
        paused, nothing can bring pulsing back up.  Resume is unaffected —
        ``wait_if_paused`` clears the flag before it calls its ``restart``
        callback.

        Returns True if stimulation was started, False if it was suppressed.
        """
        if self._abort_requested or self._pause_requested:
            self._log("Start of pulsing SUPPRESSED — run is "
                      + ("aborted" if self._abort_requested else "paused")
                      + ".")
            return False
        self.stim.start_all()
        return True

    def wait_if_paused(self, restart: Optional[Callable[[], None]] = None) -> bool:
        """Worker-thread checkpoint: if a pause is pending, HALT stimulation,
        emit a log line, and BLOCK until resume or abort; on resume optionally
        ``restart()`` pulsing.  Call at the same safe points as the
        ``self.aborted`` checks (between captures / steps).

        Returns ``True`` to continue, ``False`` if the run was ABORTED while
        paused (caller should ``break`` its loop).  Stores the paused wall-clock
        duration in ``self._last_pause_duration_s`` so a time-bounded runner can
        add it back to its clock reference and NOT count the pause against the
        run duration (operator: "Resume … continues where it left off").

        ``restart`` is called AFTER resume to bring pulsing back up.  The
        PlexStim RETAINS every channel's loaded pattern across a stop/start
        cycle (gotcha #38/#65), so the canonical restart is just
        ``self.stim.start_all()`` — no reload needed.  Stepped runners (VT/PS)
        that reload on their next iteration pass ``restart=None`` (the halt is
        idempotent — stim is already stopped between steps).
        """
        import time as _time
        self._last_pause_duration_s = 0.0
        if not self._pause_requested:
            return not self._abort_requested
        t0 = _time.monotonic()
        # Halt pulsing (idempotent — stop_all early-returns if already stopped).
        try:
            stop_all_forced(self.stim)
        except Exception:
            pass
        self._emit(ExperimentEvent(
            kind="log", session=self.session,
            message="⏸ Paused — stimulation halted; press Resume to continue."))
        # Block until resume (``_resume_event``) or abort.  The 0.1 s poll
        # timeout keeps abort responsive even if the event is missed.
        while self._pause_requested and not self._abort_requested:
            self._resume_event.wait(0.1)
            self._resume_event.clear()
        self._last_pause_duration_s = _time.monotonic() - t0
        if self._abort_requested:
            return False
        if restart is not None:
            try:
                restart()
            except Exception as e:
                self._emit(ExperimentEvent(
                    kind="log", session=self.session,
                    message=f"Resume restart failed: {e}"))
        self._emit(ExperimentEvent(
            kind="log", session=self.session,
            message="▶ Resumed — stimulation restarted."))
        return not self._abort_requested

    # ----- progress emit --------
    def _emit_progress(self, step: int, total: int, label: str = "",
                       *, started_at: float = 0.0) -> None:
        """Convenience wrapper: emit a ``kind="progress"`` event with
        a populated :class:`ProgressInfo`.

        Subscribers that don't care about progress simply ignore
        events with no useful ``message`` and ``progress is not None``.
        The GUI's RunnerWorker forwards progress events as a
        dedicated Qt signal that drives the status-bar indicator.

        Parameters
        ----------
        step, total :
            1-based step number + total expected.  ``step == total``
            means the LAST step is starting (not that the run is
            done).
        label :
            Short human-readable description (see ProgressInfo doc).
        started_at :
            ``time.monotonic()`` at run start, used by the GUI to
            compute elapsed + ETA.  Pass 0.0 if you don't want ETA
            shown (the GUI suppresses ETA when started_at is 0).
        """
        self._emit(ExperimentEvent(
            kind="progress",
            session=self.session,
            progress=ProgressInfo(
                step=int(step),
                total=int(total),
                label=str(label),
                started_at=float(started_at),
            ),
        ))

    # ----- preflight -----
    def preflight(self) -> None:
        """Run-time sanity checks before any hardware command is issued.

        This is the runner-side mirror of the MATLAB
        ``checkExperimentInputs`` helper: every assumption the run-loop
        makes (hardware open, pattern within bounds, channels present
        on the array, save path writable) gets verified once up-front
        so a faulty input dies with a clear message instead of crashing
        the DLL or producing garbage captures halfway through.

        Subclasses may override to add experiment-specific checks but
        SHOULD call ``super().preflight()`` first so the base checks
        always run.
        """
        # Hardware handles must be open. Catching closed handles up
        # front avoids the cryptic ``ps_get_*`` and ``VI_ERROR_*``
        # failures that come out of the drivers when called on a
        # half-torn-down session.
        if self.stim is None:
            raise RuntimeError("preflight: stimulator is not connected.")
        # The GUI reinitializes the stim on Start (gotcha #29b, revised:
        # the hardware stays CONNECTED between runs and each Start does a
        # fresh PS_InitAllStim via ``_reinit_stim_for_new_run``), but if
        # THIS run reached the runner with the device still closed — e.g.
        # a code path that bypassed that GUI step — proceeding would fire
        # a flood of
        # "Communication to Stimulator failed" DLL errors (set_monitor_
        # channel, load_channel, …) on a dead handle (operator: "After I
        # aborted the experiment, I still get errors about the stimulator,
        # likely because it is still closed and not initialized").  Self-
        # heal: re-open it here.  ``is_open`` is the device's own state
        # (PlexonStimulator); a driver without it (returns True via the
        # base property) is left alone.  A fresh ``open()`` on a closed
        # device is a clean single PS_InitAllStim (no close cascade — see
        # plexon.py ``_is_open``).
        try:
            _stim_open = bool(getattr(self.stim, "is_open", True))
        except Exception:
            _stim_open = True
        if not _stim_open:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=("Stimulator was closed (prior Stop/abort) — "
                         "re-initializing before this run…")))
            try:
                self.stim.open()
            except Exception as _open_err:
                raise RuntimeError(
                    "preflight: the stimulator is closed and "
                    f"re-initialization failed ({type(_open_err).__name__}: "
                    f"{_open_err}). Re-Initialize it from the Setup tab → "
                    "Connection panel and try again.")
        if self.scope is None:
            raise RuntimeError("preflight: oscilloscope is not connected.")

        test = self.session.test
        # Pattern bounds vs. the device limits. ``PulsePattern.validate``
        # raises ValueError with a phase-by-phase reason when something
        # is out of range.
        if test.pattern is None:
            raise RuntimeError("preflight: no pulse pattern defined.")
        test.pattern.validate()

        # Configuration: active channel must exist on the array; every
        # listed return channel must also be present (or be 0 for the
        # off-array global return).
        cfg = test.configuration
        array = test.array
        if cfg is None:
            raise RuntimeError("preflight: no electrode configuration set.")
        valid_channels = {site.number for site in array.sites}
        if cfg.active not in valid_channels:
            raise RuntimeError(
                f"preflight: active channel {cfg.active} is not present on "
                f"array {array.name!r} (channels: "
                f"{sorted(valid_channels)}).")
        for ret in (cfg.returns or ()):
            if ret == 0:
                continue  # off-array global return — handled by hardware
            if ret not in valid_channels:
                raise RuntimeError(
                    f"preflight: return channel {ret} is not present on "
                    f"array {array.name!r}.")
        if cfg.active in (cfg.returns or ()):
            raise RuntimeError(
                f"preflight: active channel {cfg.active} is also listed "
                f"as a return channel — that's a short.")

    # ----- to be implemented -----
    @abstractmethod
    def run(self) -> ExperimentResult: ...
