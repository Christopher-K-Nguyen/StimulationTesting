"""Access-resistance DRIFT test — nonparametric Mann-Whitney U of the present
R_a vs the run's earlier R_a (operator: "Mann-Whitney"; "the access resistance
at 0 uA is excluded"; "I would rather do a specific statistical test").

Two layers:
  * the pure statistic ``metrics.access_resistance_drift_mannwhitney`` +
    ``representative_access_resistance_kohm``;
  * the runner hook ``ExperimentRunner.check_access_resistance_drift`` (excludes
    0 µA captures, stores p + flag on the latest capture, warn-only).
"""
from __future__ import annotations

import math

import numpy as np

from stimtest.metrics import (access_resistance_drift_mannwhitney as mw,
                             representative_access_resistance_kohm as rep)
from stimtest.session import Capture, CaptureMetrics, CaptureStatus
from stimtest.waveforms import PulsePattern


# ------------------------------------------------------------- pure statistic
def test_stable_ra_is_not_flagged():
    ref = [5.0, 5.2, 4.8, 5.1, 4.9, 5.05, 4.95, 5.15]
    pres = [5.1, 4.9, 5.0, 5.2]
    res = mw(ref, pres)
    assert res.flagged is False
    assert res.p_value > 0.2                     # clearly not significant


def test_recent_jump_is_flagged():
    ref = [5.0, 5.2, 4.8, 5.1, 4.9, 5.05, 4.95, 5.15]   # ~5 kΩ baseline
    pres = [20.0, 21.0, 19.5, 20.5]                     # jumped to ~20 kΩ
    res = mw(ref, pres)
    assert res.flagged is True
    assert res.p_value < 0.01
    assert res.median_present_kohm > 3 * res.median_reference_kohm


def test_magnitude_gate_blocks_significant_but_tiny_shift():
    # Complete separation → p is tiny, but the median only moves ~2 % — below
    # the 25 % magnitude gate, so it must NOT flag (thermal-drift immunity).
    ref = [5.00] * 10
    pres = [5.10] * 10
    res = mw(ref, pres)
    assert res.p_value < 0.01                    # statistically separable
    assert res.flagged is False                  # …but physically trivial


def test_below_minimum_history_is_not_tested():
    res = mw([5.0, 5.1, 5.0], [20.0, 20.0, 20.0])   # ref n=3 < min_reference
    assert res.flagged is False
    assert math.isnan(res.p_value)


def test_non_finite_values_dropped():
    ref = [5.0, float("nan"), 5.1, 4.9, float("inf"), 5.0, 5.2, 4.8, 5.1]
    pres = [20.0, 20.0, float("nan"), 20.0, 20.0]
    res = mw(ref, pres)
    assert res.flagged is True                   # NaN/inf ignored, jump remains
    assert res.n_reference == 7 and res.n_present == 4


# ------------------------------------------------------- representative R_a
def _cap(amp_ua, ra_kohm):
    pat = PulsePattern.biphasic(amplitude_ua=max(abs(amp_ua), 1e-9),
                                polarity=(-1 if amp_ua < 0 else 1))
    m = CaptureMetrics()
    if ra_kohm is not None:
        m.access_resistance_per_phase_kohm = [ra_kohm]
    return Capture(index=0, pattern=pat, metrics=m, status=CaptureStatus())


def test_representative_takes_leading_access_r():
    assert rep(_cap(-50.0, 8.8)) == 8.8
    assert math.isnan(rep(_cap(-50.0, None)))    # no access recorded → NaN


# --------------------------------------------------------------- runner hook
def _runner():
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.experiments.progressive_stress import ProgressiveStressExperiment
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    test = TestParameters(experiment="PS", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    return ProgressiveStressExperiment(sess, stim, scope), stim, scope


def test_runner_hook_excludes_zero_ua_and_flags_drift():
    from stimtest.session import ChannelRun
    from stimtest.electrode import Configuration
    r, stim, scope = _runner()
    try:
        run = ChannelRun(configuration=Configuration.monopolar(1))
        # A 0 µA capture with GARBAGE R_a must be excluded from the baseline.
        run.captures.append(_cap(0.0, 999.0))
        # 8 healthy steps at ~5 kΩ …
        for ra in (5.0, 5.2, 4.8, 5.1, 4.9, 5.05, 4.95, 5.15):
            run.captures.append(_cap(-50.0, ra))
        # … then 4 recent captures that jumped to ~20 kΩ (the present window).
        for ra in (20.0, 21.0, 19.5, 20.5):
            run.captures.append(_cap(-50.0, ra))
        r.check_access_resistance_drift(run)
        last = run.captures[-1].metrics
        assert last.access_resistance_drift_flag is True
        assert last.access_resistance_drift_p < 0.01
        # The 999 kΩ 0-µA capture did NOT poison the baseline (median ~5, not
        # inflated) — flag is driven purely by the real jump.
    finally:
        stim.close(); scope.close()


def test_runner_hook_no_flag_when_stable():
    from stimtest.session import ChannelRun
    from stimtest.electrode import Configuration
    r, stim, scope = _runner()
    try:
        run = ChannelRun(configuration=Configuration.monopolar(1))
        for ra in (5.0, 5.2, 4.8, 5.1, 4.9, 5.05, 4.95, 5.15, 5.0, 4.9, 5.1, 5.0):
            run.captures.append(_cap(-50.0, ra))
        r.check_access_resistance_drift(run)
        assert run.captures[-1].metrics.access_resistance_drift_flag is False
    finally:
        stim.close(); scope.close()
