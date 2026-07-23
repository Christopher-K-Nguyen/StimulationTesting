"""E_pol SATURATION (plateau-near-limit) stop — kill the near-crossover fine
creep on a saturated, noisy electrode.

Operator (exp_vt_max_check): "improve the approach for reaching maximum charge
injection capacity — reduce number of captures."

The dominant remaining waste in the latest run was NOT the low-current climb
(the baseline-subtraction fixed that) but the FINAL approach on the channels
whose E_pol SATURATES just below the acceptance band: worst-case |E_pol| goes
flat at ~96 % of the limit and is noisy (±10-15 mV), so the single-sample band
stop only fires on a lucky spike and the distance-table (gotcha #155) creeps ~30
captures (CH14: 720 → 779 µA) waiting for it.

``_epol_plateaued_near_limit`` detects the saturation — robust (median) worst
ratio NEAR the limit AND FLAT across the window — and reports the saturation
amplitude as the electrode's max charge injection (conservative: a few % below
the eventual noise crossing, and the electrode will not polarize further).

SAFETY (verified here): it never fires on a still-climbing electrode (which
would under-report), on a mid-climb plateau far from the limit, or on a stuck
amplitude.
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
    return VoltageTransientExperiment(
        session, stim, scope,
        # coarse_step 50 µA matches the GUI's adaptive ramp (max_ua × 0.05) —
        # the plateau coarse-step-toward-max uses it.
        ramp=RampPolicy(strategy="adaptive", max_ua=1000.0, coarse_step_ua=50.0),
        cathodic_limit_v=CATH, anodic_limit_v=0.6,
        polarization_tolerance_v=0.02)


def _cap(amp_ua, epol_v):
    """One cathodic capture at ``amp_ua`` with worst |E_pol| = ``epol_v``."""
    c = Capture(index=0,
                pattern=PulsePattern.biphasic(amplitude_ua=amp_ua, polarity=-1))
    c.metrics.polarization_per_phase_v = [-abs(epol_v), 0.0]
    c.metrics.response_class = "normal"
    return c


# ---- direct unit tests on the detector -----------------------------------

def test_plateau_fires_on_flat_near_limit_window():
    """A window of captures whose amplitude climbs but E_pol stays FLAT at
    ~96 % of the −0.6 V limit (± noise) → fires, returning the capture closest
    to the limit."""
    r = _runner()
    # CH14-like: amps climb 720→744, |E_pol| hovers ~0.575 with ±noise
    win = [_cap(720, 0.573), _cap(726, 0.561), _cap(732, 0.565),
           _cap(738, 0.561), _cap(744, 0.576)]
    hit = r._epol_plateaued_near_limit(win)
    assert hit is not None
    # closest to the limit = the 0.576 capture (744 µA)
    assert abs(hit.pattern.excitation_phase.amplitude_ua) == pytest.approx(744)


def test_plateau_does_not_fire_when_still_climbing():
    """A near-limit window whose E_pol is still RISING must NOT fire (it would
    under-report an electrode that can still reach the band)."""
    r = _runner()
    win = [_cap(600, 0.50), _cap(650, 0.53), _cap(700, 0.55),
           _cap(750, 0.57), _cap(800, 0.59)]        # rising ~0.09 V/window
    assert r._epol_plateaued_near_limit(win) is None


def test_plateau_does_not_fire_far_from_limit():
    """A FLAT mid-climb plateau far below the limit (|E_pol| ~0.30, 50 %) is not
    saturation near the water window — must NOT fire."""
    r = _runner()
    win = [_cap(100, 0.30), _cap(112, 0.29), _cap(124, 0.31),
           _cap(136, 0.30), _cap(148, 0.30)]
    assert r._epol_plateaued_near_limit(win) is None


def test_plateau_fires_on_a_stuck_tiny_step_creep():
    """A SATURATED electrode whose distance-table step has shrunk to the µA
    floor creeps in TINY steps near the limit — the amplitude barely moves, yet
    E_pol is flat at ~95 % of the limit, so it IS at max charge injection and
    must be stopped (exp_vt_max_check CH11: crept 430 → 437 µA in 0.3 µA steps
    over 23 captures while E_pol sat pinned at 0.578).  The amplitude-climb
    amount is NOT the saturation signal — the flat-near-limit ratio is."""
    r = _runner()
    win = [_cap(740.0, 0.573), _cap(740.3, 0.561), _cap(740.6, 0.565),
           _cap(740.9, 0.561), _cap(741.2, 0.576)]   # 0.3 µA steps (CH11-like)
    assert r._epol_plateaued_near_limit(win) is not None


def test_plateau_skips_a_decreasing_window():
    """A window whose amplitude DECREASED (e.g. a back-off decrement) is not a
    forward saturation creep — must NOT fire."""
    r = _runner()
    win = [_cap(760, 0.576), _cap(755, 0.573), _cap(750, 0.575),
           _cap(745, 0.574), _cap(740, 0.576)]       # amplitude going DOWN
    assert r._epol_plateaued_near_limit(win) is None


def test_fast_tier_fires_on_dead_flat_trio():
    """FAST dead-flat tier (operator #3: faster near-limit approach) — 3 DEAD-
    flat near-limit captures fire immediately, without waiting for the full
    5-window (exp_vt_max_check CH06's 0.936× plateau: fires ~2 captures sooner)."""
    r = _runner()
    win = [_cap(957, 0.5616), _cap(963, 0.5621), _cap(969, 0.5615)]  # ~0.936× flat
    assert r._epol_plateaued_near_limit(win) is not None


def test_fast_tier_spares_a_climbing_trio():
    """A still-CLIMBING near-limit trio (wide span / rising trend) does NOT fire
    the fast tier — it reaches the band normally rather than being preempted."""
    r = _runner()
    win = [_cap(900, 0.540), _cap(910, 0.555), _cap(920, 0.570)]     # 0.90→0.95× rising
    assert r._epol_plateaued_near_limit(win) is None


def test_plateau_ignores_reached_and_bad_captures():
    """Already-reached / exceeded / non-normal captures are excluded from the
    window (the run loop's limit stop owns a genuine reach)."""
    r = _runner()
    win = [_cap(720, 0.573), _cap(726, 0.561), _cap(732, 0.565)]
    win[-1].status.reached_potential_limit = True    # latest already reached
    # only 2 usable captures now → below the fast-tier window (3) → no fire
    assert r._epol_plateaued_near_limit(win) is None


# ---- end-to-end: fewer captures, no worse overshoot ----------------------

def _drive(r, epol_of_amp, seed=0):
    rng = np.random.default_rng(seed)

    def _fake(config, pattern, idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=idx, pattern=pattern)
        noise = rng.normal(0.0, 0.013) if amp > 0 else 0.0
        c.metrics.polarization_per_phase_v = [-(epol_of_amp(amp) + noise), 0.0]
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


def _sweep(sat_on, epol, seed=0):
    r = _runner()
    if not sat_on:
        r._epol_plateaued_near_limit = lambda caps: None
    _drive(r, epol, seed=seed)
    run = r._run_one_configuration(Configuration.monopolar(1))
    caps = [c for c in run.captures if not c.status.aborted]
    amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
    worst = max((abs(epol(a)) / abs(CATH)) for a in amps) if amps else 0.0
    return len(caps), worst


# saturates at 0.577 V (96 % of the 0.6 V limit) near ~700 µA — never a clean
# cross; the E_pol goes flat just below the near edge (the CH14 scenario).
def _saturating(amp):
    return 0.577 * (1.0 - np.exp(-abs(amp) / 220.0))


def test_saturating_electrode_completes_at_band_or_max():
    """Operator: "CH07/CH09/CH11 did not reach any potential limit or maximum
    current."  A saturating electrode whose E_pol plateaus just below the band
    must COMPLETE — either its noise crosses into the band (REACHED) or it ramps
    to MAX CURRENT (hardware-limited) — but it must NEVER stop below BOTH with a
    'not enough precision' lower bound (the old behaviour that was removed)."""
    r = _runner()
    _drive(r, _saturating, seed=1)
    run = r._run_one_configuration(Configuration.monopolar(1))
    caps = [c for c in run.captures if not c.status.aborted]
    amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
    reached = any(c.status.reached_potential_limit for c in caps)
    near_max = max(amps) >= r.ramp.max_ua - r.ramp.coarse_step_ua
    assert reached or near_max, (reached, max(amps))   # band OR max — completed
    # NOT the old "not enough precision" lower-bound stop below both
    assert not any("not enough precision" in (c.status.notes or "").lower()
                   for c in caps)
    assert np.isfinite(run.max_q_inj)


def test_saturation_stop_does_not_worsen_a_clean_crosser():
    """A genuinely climbing electrode still REACHES the band with the plateau
    coarse-step ON.  It may cost a capture or two more than OFF (a coarse-step
    PROBE near the shallow top before it crosses), but crucially it NEVER
    worsens the overshoot — the SAFETY invariant (operator: electrode damage)."""
    def _crosser(amp):
        return 0.75 * (min(abs(amp) / 500.0, 3.0)) ** 0.75
    on_n, on_w = _sweep(True, _crosser, seed=2)
    off_n, off_w = _sweep(False, _crosser, seed=2)
    assert on_n <= off_n + 3, (on_n, off_n)      # ~same (coarse-step probe cost)
    assert on_w <= off_w + 1e-9, (on_w, off_w)   # no worse overshoot (SAFETY)


def _highbaseline_slow_climber(amp):
    """CH11-like: a HIGH rest polarization (0.185 V ≈ 0.31× at 0 µA) plus a slow
    current-driven climb that crosses the band near ~450 µA.  The flat low-current
    region (baseline-dominated) is what fooled the concave-down snap into flinging
    the ramp to max (→ 1.19× the limit) before the near-band + signal-emerged
    guards were added."""
    a = abs(amp)
    return 0.185 + 0.55 * (a / 450.0) ** 1.3          # 0.185 → ~0.74 at 450 µA


def test_highbaseline_slow_climber_does_not_fling_to_max():
    """Regression (exp_vt_max_check CH11/CH14): a high-rest-potential SLOW CLIMBER
    that crosses the band around ~450 µA must reach the band via BOUNDED steps —
    it must NOT be flung to the 1000 µA ceiling (which over-polarized it to ~1.19×
    the limit).  Starting from a genuine 0 µA baseline so the baseline-subtracted
    growth ratio is honest (the guards key on it)."""
    pattern = PulsePattern.biphasic(amplitude_ua=0.0, polarity=-1)   # start at 0 µA
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    r = VoltageTransientExperiment(
        Session(notebook="t", subject="s", test=test), stim, scope,
        ramp=RampPolicy(strategy="adaptive", max_ua=1000.0, coarse_step_ua=50.0),
        cathodic_limit_v=CATH, anodic_limit_v=0.8, polarization_tolerance_v=0.02)
    _drive(r, _highbaseline_slow_climber, seed=4)
    run = r._run_one_configuration(Configuration.monopolar(1))
    caps = [c for c in run.captures if not c.status.aborted]
    amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in caps]
    worst = max((_highbaseline_slow_climber(a) / abs(CATH)) for a in amps)
    reached = any(c.status.reached_potential_limit for c in caps)
    # It completes (reaches the band) …
    assert reached, amps
    # … WITHOUT a fling: no capture ever over-polarized the electrode beyond a
    # small back-off-recoverable overshoot (the pre-fix bug hit 1.19×).
    assert worst < 1.10, (worst, [round(a) for a in amps])
    # and the crossing happened at a BOUNDED amplitude, not the 1000 µA ceiling.
    assert max(amps) < 700.0, max(amps)


def test_ramp_never_retests_an_amplitude():
    """MATLAB no-re-test (changeCurrent.m:526): the ramp must never spend a
    capture re-measuring a current already tested on the 0.1 µA grid — the tested
    amplitudes are all distinct."""
    r = _runner()
    _drive(r, _saturating, seed=3)
    run = r._run_one_configuration(Configuration.monopolar(1))
    amps = [round(abs(c.pattern.excitation_phase.amplitude_ua) / 0.1)
            for c in run.captures if not c.status.aborted
            and abs(c.pattern.excitation_phase.amplitude_ua) > 0.0]
    assert len(amps) == len(set(amps)), "an amplitude was tested twice"


# ---- ESCALATION: reach max on a genuine plateau, spare a climber ----------

def test_confirmed_plateau_fires_on_flat_six_window():
    """The ESCALATION detector fires on a genuine 6-capture flat, near-limit,
    increasing-amplitude window (a saturated electrode)."""
    r = _runner()
    caps = [_cap(700 + 6 * i, 0.575 + (0.002 if i % 2 else -0.002))
            for i in range(6)]
    assert r._epol_confirmed_plateau(caps) is True


def test_confirmed_plateau_spares_a_rising_six_window():
    """A RISING near-limit 6-window must NOT fire — the escalation ACTS on
    detection, so it must not over-polarize a slow climber (the fast tier's
    3-capture window could false-fire on noise; the 6-capture linear fit
    cannot)."""
    r = _runner()
    caps = [_cap(700 + 10 * i, 0.545 + 0.006 * i) for i in range(6)]  # 0.545→0.575
    assert r._epol_confirmed_plateau(caps) is False


def test_plateau_below_band_escalates_toward_max():
    """A saturating electrode whose E_pol plateaus BELOW the band (ratio ~0.92,
    never genuinely crosses) reaches near MAX current — the operator's 'reach
    max current, hardware-limited' — instead of noise-creeping to a random low
    amplitude and reporting it as max Q_inj."""
    def _plateau(amp):
        return 0.55 * (1.0 - np.exp(-abs(amp) / 150.0))   # saturates at 0.55
    r = _runner()
    _drive(r, _plateau, seed=7)
    run = r._run_one_configuration(Configuration.monopolar(1))
    amps = [abs(c.pattern.excitation_phase.amplitude_ua)
            for c in run.captures if not c.status.aborted]
    assert max(amps) >= 900.0, max(amps)     # escalated toward the 1000 µA max
    assert np.isfinite(run.max_q_inj)


def test_single_noise_crossing_does_not_stop_the_ramp():
    """2-CONSECUTIVE reached: a lone in-band capture (E_pol below the near edge
    but noise-spiked over it ONCE) must NOT stop the ramp — the electrode is
    still below the band, so it keeps climbing (exp_vt_max_anodal CH08 crept 34
    captures because a single noise crossing at 774 µA falsely stopped it)."""
    below = -0.55            # cathodic, near-edge is -0.58 (limit -0.6, tol 0.02)
    seq = iter([below, below, -0.59, below, below, below])   # ONE spike over -0.58
    r = _runner()

    def _fake(config, pattern, idx):
        amp = abs(float(pattern.excitation_phase.amplitude_ua))
        c = Capture(index=idx, pattern=pattern)
        try:
            e = next(seq) if amp > 0 else 0.0
        except StopIteration:
            e = below
        c.metrics.polarization_per_phase_v = [e, 0.0]
        c.metrics.response_class = "normal"
        c.metrics.charge_injection_mc_per_cm2 = amp / 100.0
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
    run = r._run_one_configuration(Configuration.monopolar(1))
    # The single -0.59 spike (3rd real capture) must NOT have stopped the ramp:
    # it kept going past that capture.
    reached_caps = [c for c in run.captures
                    if c.status.reached_potential_limit]
    # The lone spike alone never confirms (needs 2 consecutive) → no stop on it.
    assert len(run.captures) > 4, [len(run.captures)]
