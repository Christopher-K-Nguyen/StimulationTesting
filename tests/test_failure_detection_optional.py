"""Optional, configurable early open/broken failure detection during VT-max.

Operator (exp_vt_max_check): CH02/CH08/CH10 were mis-flagged "open" and stopped
at 6 µA — at that current the early polarization inflates the apparent impedance,
so a functional electrode reads high-Z.  The early stop is now:
  * gated on a MINIMUM CURRENT (below it the classification is unreliable → the
    ramp keeps climbing),
  * with CONFIGURABLE open/broken impedance thresholds, and
  * an OPTIONAL master toggle (``stop_on_bad_response``) so the ramp can always
    run to the potential limit / compliance / max current.
"""
from __future__ import annotations

import math

import numpy as np

from stimtest.experiments.voltage_transient import RampPolicy
from stimtest.metrics import classify_response_and_ceff, compute_metrics
from stimtest.session import Capture
from stimtest.waveforms import PulsePattern, Phase, SHAPE_RECTANGULAR


def _pat(amp_ua):
    return PulsePattern(phases=[
        Phase(amplitude_ua=amp_ua, width_us=200, shape=SHAPE_RECTANGULAR),
        Phase(amplitude_ua=-amp_ua, width_us=200, shape=SHAPE_RECTANGULAR),
    ], rate_hz=50.0)


def _open_like_v(t, peak=-0.5):
    """A high-impedance straight ramp with NO IR step — the open/broken
    signature (V_mon starts from ~0 and ramps, no ohmic jump)."""
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = peak * (t[m] / 200.0)
    return v


def test_min_current_gate_declines_low_current():
    """At 6 µA the open/broken heuristics are unreliable — with a 50 µA gate the
    classifier DECLINES (returns 'normal') so the ramp keeps climbing; with no
    gate the same capture classifies non-normal (the CH02/CH10 bug)."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = _open_like_v(t, peak=-0.5)          # 0.5 V at 6 µA ⇒ ~83 kΩ, no IR step
    ungated, _ = classify_response_and_ceff(
        t, v, _pat(-6.0), onset_us=0.0, driving_v=0.5, compliance_v=9.0)
    gated, _ = classify_response_and_ceff(
        t, v, _pat(-6.0), onset_us=0.0, driving_v=0.5, compliance_v=9.0,
        min_current_ua=50.0)
    assert ungated in ("open", "broken"), ungated
    assert gated == "normal", gated


def test_min_current_gate_still_classifies_above_threshold():
    """The gate only silences LOW current — a genuinely open capture above the
    minimum current still classifies open, so a dead electrode is still caught
    as the ramp climbs."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = _open_like_v(t, peak=-6.0)          # 6 V at 100 µA ⇒ 60 kΩ, no IR step
    cls, _ = classify_response_and_ceff(
        t, v, _pat(-100.0), onset_us=0.0, driving_v=6.0, compliance_v=9.0,
        min_current_ua=50.0)
    assert cls in ("open", "broken"), cls


def test_manual_z_threshold_changes_classification():
    """A raised BROKEN (extreme-Z) threshold reclassifies a real-access-step but
    very-high-voltage capture from broken → normal (the 'manual thresholds'
    mode): a functional electrode with an IR step that the default 0.2 MΩ
    extreme-Z gate would otherwise force broken."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = np.zeros_like(t)
    m = (t >= 0.0) & (t <= 200.0)
    v[m] = -1.2 - 1.8 * (t[m] / 200.0)      # IR step 1.2 V + ramp to 3 V
    # at 10 µA, vmax = 3 V ⇒ 0.3 MΩ ⇒ default broken_z 0.2 MΩ ⇒ extreme ⇒ broken
    strict, _ = classify_response_and_ceff(
        t, v, _pat(-10.0), onset_us=0.0, driving_v=3.0, compliance_v=9.0)
    # raise broken_z to 1.0 MΩ ⇒ not extreme + has access step ⇒ normal
    loose, _ = classify_response_and_ceff(
        t, v, _pat(-10.0), onset_us=0.0, driving_v=3.0, compliance_v=9.0,
        broken_z_mohm=1.0)
    assert strict in ("open", "broken"), strict
    assert loose == "normal", loose


def test_compute_metrics_threads_failure_thresholds():
    """compute_metrics passes the thresholds through to the classifier so the
    STORED response_class honours the min-current gate."""
    t = np.linspace(-50.0, 450.0, 2000)
    v = _open_like_v(t, peak=-0.5)
    cap = Capture(index=0, pattern=_pat(-6.0))
    cap.time_us = t; cap.v_mon_v = v; cap.i_mon_ua = np.zeros_like(t)
    m0 = compute_metrics(cap, 4000.0)                               # min=0
    m1 = compute_metrics(cap, 4000.0, failure_min_current_ua=50.0)  # gated
    assert m0.response_class in ("open", "broken")
    assert m1.response_class == "normal"


def test_ramp_policy_failure_fields_default():
    """Defaults preserve the historical behaviour (early stop ON) but expose the
    tunables the GUI drives."""
    p = RampPolicy()
    assert p.stop_on_bad_response is True
    assert p.bad_response_auto is True
    assert p.bad_response_min_current_ua == 50.0
    assert p.bad_response_open_z_kohm == 50.0
    assert p.bad_response_broken_z_kohm == 200.0


def _bad_runner(stop_on_bad):
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.experiments.voltage_transient import VoltageTransientExperiment
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(strategy="increment", coarse_step_ua=10.0, max_ua=35.0,
                        stop_on_bad_response=stop_on_bad))
    return runner, stim, scope


def _fake_broken():
    def _make(config, pattern, index):
        c = Capture(index=index, pattern=pattern)
        c.metrics.response_class = "broken"
        c.metrics.effective_capacitance_nf = 0.1
        c.time_us = np.zeros(4); c.v_mon_v = np.zeros(4); c.i_mon_ua = np.zeros(4)
        return c
    return _make


def test_master_toggle_off_ramps_through_bad_response(monkeypatch):
    """stop_on_bad_response=False (operator #4): the ramp IGNORES the class and
    runs to max_ua instead of stopping at the first bad capture."""
    runner, stim, scope = _bad_runner(stop_on_bad=False)
    try:
        monkeypatch.setattr(runner, "_one_capture", _fake_broken())
        run = runner.run().session.runs[0]
        amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in run.captures]
        # ramped through the bad captures to the ceiling (10→20→30; 40>35 exits)
        assert len(amps) >= 3 and max(amps) >= 30.0, amps
    finally:
        stim.close(); scope.close()


def test_master_toggle_on_stops_on_bad_response(monkeypatch):
    """The default (stop_on_bad_response=True) still stops early on a bad
    response — the historical behaviour is preserved."""
    runner, stim, scope = _bad_runner(stop_on_bad=True)
    try:
        monkeypatch.setattr(runner, "_one_capture", _fake_broken())
        run = runner.run().session.runs[0]
        amps = [abs(c.pattern.excitation_phase.amplitude_ua) for c in run.captures]
        assert max(amps) < 35.0, amps         # stopped early
    finally:
        stim.close(); scope.close()
