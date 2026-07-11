"""Baseline-subtracted growth ratio — reduce captures on high-capacity
(high rest-polarization) electrodes without worsening overshoot.

Operator (exp_vt_max_check): "improve incrementing to the maximum charge
injection capacity — reduce the number of captures."

A high-capacity electrode carries a large FIXED rest polarization (~0.16 V ≈
28 % of the −0.6 V limit) BEFORE any current flows.  ``_worst_epol_ratio``
(the growth-cap signal) is raw ``|E_pol|/|limit|``, so that baseline pins the
ramp in the conservative 1.7× tier for the entire low-current climb and it
creeps to the ceiling over ~15 captures.  ``_growth_ratio`` subtracts the 0 µA
rest — ``(|E_pol| − rest)/(|limit| − rest)`` — so the no-signal region climbs
fast while near-limit safety is unchanged.

CRITICAL SAFETY GATES (both verified here):
  * only a GENUINE 0 µA baseline capture supplies ``rest`` — else the first
    capture of a non-zero-start ramp (already carrying real polarization) would
    be mistaken for rest and unleash a concave-up fling.
  * only engages when ``rest`` exceeds ``_BASELINE_MIN_FRAC`` of the limit — a
    low-baseline electrode stays byte-identical on the raw ratio.
"""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.session import Capture, Session, TestParameters
from stimtest.waveforms import PulsePattern

CATH = -0.6


def _runner():
    pattern = PulsePattern.biphasic(amplitude_ua=0.1, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    r = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(strategy="adaptive", max_ua=1000.0),
        cathodic_limit_v=CATH, anodic_limit_v=0.6,
        polarization_tolerance_v=0.02)
    return r


def _drive(r, epol_of_amp):
    def _fake(config, pattern, idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=idx, pattern=pattern)
        c.metrics.polarization_per_phase_v = [epol_of_amp(amp), 0.0]
        c.metrics.charge_injection_mc_per_cm2 = amp / 100.0
        c.metrics.response_class = "normal"
        c.time_us = np.zeros(4); c.v_mon_v = np.zeros(4); c.i_mon_ua = np.zeros(4)
        return c
    r._one_capture = _fake
    r._record_capture_dose = lambda run, cap: None
    r.bias_step_if_armed = lambda: None
    r.apply_default_scope_view = lambda *a, **k: None
    r.arm_bias_feedback = lambda: None
    r.disarm_bias_feedback = lambda: None
    r._seed_scope_scales = lambda *a, **k: None
    r._emit = lambda ev: None
    return r


def _sweep(baseline_min_frac, epol):
    r = _runner()
    r._BASELINE_MIN_FRAC = baseline_min_frac      # 10.0 → fix OFF (raw ratio)
    _drive(r, epol)
    run = r._run_one_configuration(Configuration.monopolar(1))
    caps = [c for c in run.captures if not c.status.aborted]
    amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
    worst = max((abs(epol(a)) / abs(CATH)) for a in amps) if amps else 0.0
    return len(caps), worst


# a high-capacity electrode: 0.16 V rest + SUB-LINEAR (saturating) rise that
# crosses −0.6 at ~400 µA — the exp_vt_max_check shape.
def _hi_baseline(amp):
    return -(0.16 + 0.44 * (min(abs(amp) / 400.0, 1.0)) ** 0.7)


# a HARDWARE-LIMITED high-capacity electrode: 0.16 V rest + sub-linear rise
# MAXING at 0.45 V (75 % of the limit) — never crosses, so the ramp climbs to
# the 1000 µA ceiling.  This is where the speedup is unambiguous (no crossover
# to overshoot); the exp_vt_max_check hardware-limited channels (CH01/02/11/12).
def _hi_baseline_hwlim(amp):
    return -(0.16 + 0.29 * (min(abs(amp) / 1000.0, 1.0)) ** 0.8)


# a low-baseline electrode (rest ≈ 0): same sub-linear shape, no offset.
def _lo_baseline(amp):
    return -abs(CATH) * (min(abs(amp) / 400.0, 3.0)) ** 0.7


def test_baseline_subtraction_speeds_hardware_limited_climb():
    """The win: a high-rest HARDWARE-LIMITED electrode reaches the 1000 µA
    ceiling in MEANINGFULLY fewer captures than the raw-ratio behaviour (the
    baseline pinned the raw ramp in the conservative tier for the whole climb).
    """
    n_fix, worst_fix = _sweep(0.10, _hi_baseline_hwlim)   # fix ON
    n_raw, worst_raw = _sweep(10.0, _hi_baseline_hwlim)   # fix OFF (raw)
    assert n_fix < n_raw - 2, (n_fix, n_raw)
    # a hardware-limited electrode never reaches the window either way
    assert worst_fix < 1.0 and worst_raw < 1.0, (worst_fix, worst_raw)


def test_baseline_subtraction_no_worse_overshoot_on_a_crosser():
    """SAFETY on a CROSSING high-rest electrode: the faster low-current climb
    must not overshoot the water window more than the raw ramp (a crosser may
    take MORE captures via back-off — that is acceptable; a bigger transient
    overshoot is NOT)."""
    n_fix, worst_fix = _sweep(0.10, _hi_baseline)     # fix ON
    n_raw, worst_raw = _sweep(10.0, _hi_baseline)     # fix OFF (raw)
    assert worst_fix <= worst_raw + 0.05, (worst_fix, worst_raw)


def test_baseline_subtraction_is_noop_for_low_baseline():
    """A low-baseline electrode is byte-identical ON vs OFF — the gate keeps the
    √-loosening safety envelope + every other ramp test unchanged."""
    n_fix, worst_fix = _sweep(0.10, _lo_baseline)
    n_raw, worst_raw = _sweep(10.0, _lo_baseline)
    assert n_fix == n_raw, (n_fix, n_raw)
    assert abs(worst_fix - worst_raw) < 1e-9


def test_growth_ratio_requires_a_zero_uA_baseline():
    """``_growth_ratio`` falls back to the raw ratio when there is NO ~0 µA
    baseline capture — the guard against mistaking a non-zero-start ramp's
    first (already-polarized) capture for rest."""
    r = _runner()
    # one capture at 5 µA (NOT a 0 µA baseline) carrying real polarization
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=5.0, polarity=-1))
    c.metrics.polarization_per_phase_v = [-0.20, 0.0]   # 0.20 V already at 5 µA
    gr = r._growth_ratio([c])
    raw = r._worst_epol_ratio([c])
    assert abs(gr - raw) < 1e-9, (gr, raw)   # no baseline → raw ratio exactly


def _decel_hwlim(amp):
    # strongly saturating (concave-DOWN) electrode that maxes at 0.45 V — never
    # crosses the −0.6 V window, so it is hardware-limited AND confirmed
    # decelerating (the mid-climb snap-to-ceiling target).
    return -0.45 * (1.0 - np.exp(-abs(amp) / 80.0))


def _accel_hwlim(amp):
    # concave-UP (accelerating) electrode that also maxes below the window
    # (0.45 V) — must NOT snap (a concave-up trajectory can be a flinger; the
    # growth cap must keep binding it).
    return -0.45 * (min(abs(amp) / 1000.0, 1.0)) ** 1.6


def test_snap_to_ceiling_for_confirmed_decelerating_electrode():
    """A CONFIRMED decelerating (concave-down) hardware-limited electrode snaps
    toward the 1000 µA ceiling once deceleration is measured, instead of the
    growth cap holding it to small mid-climb steps (a capped climb is ~15+)."""
    n, worst = _sweep(0.10, _decel_hwlim)
    assert n <= 11, n                 # snap fired
    assert worst < 1.0, worst          # hardware-limited — never crosses


def test_no_snap_for_accelerating_electrode():
    """A concave-UP (accelerating) electrode must NOT snap — a fling can't be
    allowed to disguise itself as decelerating; the growth cap keeps binding it
    so it never overshoots the window (the snap only fires on MEASURED
    deceleration)."""
    n, worst = _sweep(0.10, _accel_hwlim)
    assert worst <= 1.05, worst        # cap held; no fling past the window
    assert n >= 8, n                   # climbed cautiously (did NOT collapse to
    #                                    a single 130 → 1000 snap step)


def test_growth_ratio_subtracts_a_real_baseline():
    """With a genuine 0 µA baseline present, the growth ratio is the
    baseline-subtracted value (well below the raw ratio)."""
    r = _runner()
    base = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=0.0, polarity=-1))
    base.metrics.polarization_per_phase_v = [-0.16, 0.0]      # rest 0.16 V
    latest = Capture(index=1, pattern=PulsePattern.biphasic(amplitude_ua=100.0, polarity=-1))
    latest.metrics.polarization_per_phase_v = [-0.19, 0.0]    # +0.03 V of signal
    gr = r._growth_ratio([base, latest])
    raw = r._worst_epol_ratio([base, latest])
    # raw = 0.19/0.6 = 0.317; baseline-subtracted = (0.19-0.16)/(0.6-0.16) = 0.068
    assert raw == pytest.approx(0.19 / 0.6, abs=1e-3)
    assert gr == pytest.approx((0.19 - 0.16) / (0.6 - 0.16), abs=1e-3)
    assert gr < raw
