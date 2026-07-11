"""Electrode DC-offset capture → AC-couple, offset added back on save.

Operator: "Let's try AC coupled after capturing the offset from DC coupled."

A DC-biased electrode potential (E_ret at, e.g., +248 mV rest with only an
±8 mV pulse swing) can't be fine-scaled while DC-coupled — the ±5-div
POSition limit forces a coarse V/div (gotcha #13), so the swing renders
nearly flat.  The runner measures the DC rest potential ONCE (DC-coupled),
AC-couples the channel so the swing centres at 0 and the rescale loop can
fine-scale it, and threads the offset into ``make_capture`` so the SAVED
trace carries the absolute level back at the fine swing resolution.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.readback_calibration import make_capture
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner():
    pattern = PulsePattern.biphasic(amplitude_ua=50.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(session, stim, scope,
                                        ramp=RampPolicy(strategy="adaptive"))
    return runner, stim, scope


# ---- make_capture offset add-back ------------------------------------
def test_make_capture_adds_electrode_dc_offset():
    n = 200
    acq = SimpleNamespace(
        time_us=np.linspace(-50, 590, n),
        channels={"CH1": np.zeros(n), "CH2": np.zeros(n),
                  "CH3": np.full(n, 0.008)})   # AC E_ret centred ~+8 mV
    scope = SimpleNamespace(
        channel_aliases={"vmon": "CH1", "imon": "CH2", "eret": "CH3"})
    stim = SimpleNamespace(info=SimpleNamespace(
        vmon_scaling_v_per_v=1.0, imon_scaling_v_per_ua=1.0))
    pat = PulsePattern.biphasic(amplitude_ua=50.0, polarity=-1)

    # No offsets → raw AC reading (centred ~+8 mV) untouched (back-compat).
    cap0 = make_capture(0, pat, acq, scope, stim)
    assert abs(float(np.mean(cap0.e_ret_v)) - 0.008) < 1e-6

    # With a +248 mV DC offset → absolute level restored at fine resolution.
    cap1 = make_capture(0, pat, acq, scope, stim,
                        electrode_dc_offsets={"eret": 0.248})
    assert abs(float(np.mean(cap1.e_ret_v)) - 0.256) < 1e-6


def test_make_capture_offset_none_and_zero_are_noops():
    n = 64
    acq = SimpleNamespace(time_us=np.linspace(-10, 100, n),
                          channels={"CH3": np.full(n, 0.5)})
    scope = SimpleNamespace(channel_aliases={"eret": "CH3"})
    stim = SimpleNamespace(info=SimpleNamespace(
        vmon_scaling_v_per_v=1.0, imon_scaling_v_per_ua=1.0))
    pat = PulsePattern.biphasic(amplitude_ua=50.0, polarity=-1)
    for off in (None, {}, {"eret": 0.0}):
        cap = make_capture(0, pat, acq, scope, stim, electrode_dc_offsets=off)
        assert abs(float(np.mean(cap.e_ret_v)) - 0.5) < 1e-9


# ---- runner: measure DC offset, then AC-couple -----------------------
def test_measure_offset_then_ac_switches_coupling_and_records(monkeypatch):
    runner, stim, scope = _runner()
    try:
        scope.channel_aliases = {"vmon": "CH1", "imon": "CH2", "eret": "CH3"}
        writes = []
        monkeypatch.setattr(scope, "set_channel_coupling",
                            lambda ch, c: writes.append((ch, c)))
        n = 400
        eret = np.full(n, 0.248)      # +248 mV DC rest potential
        eret[200:260] += 0.010        # small swing during the pulse
        acq = SimpleNamespace(time_us=np.linspace(-50, 590, n),
                              channels={"CH3": eret})

        runner.measure_electrode_dc_offsets_and_switch_to_ac(lambda: acq)

        # Offset captured ≈ +248 mV (leading-edge baseline, robust to swing).
        assert abs(runner._electrode_dc_offset["eret"] - 0.248) < 5e-3
        # Coupling: DC first (to measure), THEN AC (for the fine-scale run).
        assert ("CH3", "DC") in writes and ("CH3", "AC") in writes
        assert writes.index(("CH3", "DC")) < writes.index(("CH3", "AC"))
    finally:
        stim.close(); scope.close()


def test_low_bias_ratio_keeps_dc_coupling(monkeypatch):
    """A large / near-zero-biased swing (low R) fine-scales fine while
    DC-coupled — it must NOT be switched to AC.  Operator: the DC→AC sum is
    only for "small magnitude waveforms like Eret in monopolar … that
    require fine scaling and positioning" (DC-dominated, high R)."""
    runner, stim, scope = _runner()
    try:
        scope.channel_aliases = {"eret": "CH3"}
        writes = []
        monkeypatch.setattr(scope, "set_channel_coupling",
                            lambda ch, c: writes.append((ch, c)))
        n = 400
        eret = np.full(n, 0.005)     # small +5 mV bias …
        eret[200:260] = 0.205        # … with a LARGE ±200 mV swing
        eret[260:320] = -0.195       #    R = 5/200 = 0.025  → NOT DC-dominated
        acq = SimpleNamespace(time_us=np.linspace(-50, 590, n),
                              channels={"CH3": eret})
        runner.measure_electrode_dc_offsets_and_switch_to_ac(lambda: acq)
        assert "eret" not in runner._electrode_dc_offset   # no offset recorded
        assert ("CH3", "AC") not in writes                 # stayed DC-coupled
        assert ("CH3", "DC") in writes                     # (DC only to measure)
    finally:
        stim.close(); scope.close()


def test_measure_offset_resets_stale_offset(monkeypatch):
    runner, stim, scope = _runner()
    try:
        scope.channel_aliases = {"eret": "CH3"}
        monkeypatch.setattr(scope, "set_channel_coupling", lambda ch, c: None)
        runner._electrode_dc_offset = {"eret": 9.9}   # stale from a prior channel
        n = 100
        eret = np.full(n, 0.05)      # +50 mV bias …
        eret[40:60] += 0.002         # … small swing → DC-dominated (R=25)
        acq = SimpleNamespace(time_us=np.linspace(-10, 100, n),
                              channels={"CH3": eret})
        runner.measure_electrode_dc_offsets_and_switch_to_ac(lambda: acq)
        assert abs(runner._electrode_dc_offset["eret"] - 0.05) < 5e-3
    finally:
        stim.close(); scope.close()


# ---- AC acceptance: interpulse SD < 5 mV before AND after ------------
def _cap_with_interpulse(before_drift_mv, after_drift_mv, n=600):
    """A capture with a clear V_mon pulse in ``t ∈ [0, 200] µs`` and an
    E_ret (CH3) DC rest ~+240 mV whose LEADING (t<0) and TRAILING (t>200)
    interpulse baselines carry a linear DRIFT of the given magnitude.  A
    linear drift of ``D`` mV has SD ≈ ``D / √12`` — so D=40 → SD≈11.5 mV
    (settling), D=4 → SD≈1.2 mV (settled)."""
    t = np.linspace(-100.0, 500.0, n)
    vm = np.zeros(n)
    vm[(t >= 0.0) & (t <= 200.0)] = 1.0             # V_mon pulse
    er = np.full(n, 0.240)                          # +240 mV DC rest
    b = t < 0.0
    er[b] += np.linspace(0.0, before_drift_mv * 1e-3, int(b.sum()))
    a = t > 200.0
    er[a] += np.linspace(0.0, after_drift_mv * 1e-3, int(a.sum()))
    return SimpleNamespace(time_us=t, channels={"CH1": vm, "CH3": er})


def test_ac_accepts_when_both_interpulse_sds_are_low(monkeypatch):
    """After DC→AC, the helper re-captures until BOTH the leading and
    trailing interpulse baselines are FLAT — SD ≤ 5 mV each (operator:
    "recapture until the interpulse before and after the pulse both have
    SD < 5 mV").  A drift on EITHER side is rejected."""
    runner, stim, scope = _runner()
    try:
        scope.channel_aliases = {"vmon": "CH1", "eret": "CH3"}
        monkeypatch.setattr(scope, "set_channel_coupling", lambda ch, c: None)
        monkeypatch.setattr(runner, "abort_sleep", lambda *a, **k: True)
        # Call 1 = DC measurement (flat ~+240 mV, DC-dominated).  Calls 2+ =
        # AC settle checks: SD decays, and the AFTER side lagging the BEFORE
        # side proves BOTH must pass.
        seq = [
            _cap_with_interpulse(2, 2),      # DC measure (small swing → R big)
            _cap_with_interpulse(40, 40),    # both SD ~11.5 mV → reject
            _cap_with_interpulse(3, 20),     # before OK (~0.9), after ~5.8 → reject
            _cap_with_interpulse(4, 4),      # both ~1.2 mV → ACCEPT
        ]
        calls = {"n": 0}

        def _recap():
            c = seq[min(calls["n"], len(seq) - 1)]
            calls["n"] += 1
            return c

        runner.measure_electrode_dc_offsets_and_switch_to_ac(_recap)
        # 1 DC measure + 3 settle checks (accept on the flat one) = 4.
        assert calls["n"] == 4
        assert abs(runner._electrode_dc_offset["eret"] - 0.240) < 5e-3
    finally:
        stim.close(); scope.close()


def test_ac_rejects_when_only_one_side_is_flat(monkeypatch):
    """BOTH interpulse regions must settle — a flat leading side with a
    still-drifting trailing side is NOT accepted (it exhausts the checks)."""
    runner, stim, scope = _runner()
    try:
        scope.channel_aliases = {"vmon": "CH1", "eret": "CH3"}
        monkeypatch.setattr(scope, "set_channel_coupling", lambda ch, c: None)
        monkeypatch.setattr(runner, "abort_sleep", lambda *a, **k: True)
        measure = _cap_with_interpulse(2, 2)
        one_sided = _cap_with_interpulse(2, 40)      # before flat, after SD ~11.5
        calls = {"n": 0}

        def _recap():
            calls["n"] += 1
            return measure if calls["n"] == 1 else one_sided

        runner.measure_electrode_dc_offsets_and_switch_to_ac(_recap)
        from stimtest.experiments.base import _AC_SETTLE_MAX_CHECKS
        assert calls["n"] == 1 + _AC_SETTLE_MAX_CHECKS   # never accepts early
    finally:
        stim.close(); scope.close()


def test_ac_accepts_anyway_after_max_sd_checks(monkeypatch):
    """If the interpulse never flattens (e.g. a very slow corner), the loop
    is bounded — it accepts the last capture after the max checks rather
    than stalling the run."""
    runner, stim, scope = _runner()
    try:
        scope.channel_aliases = {"vmon": "CH1", "eret": "CH3"}
        monkeypatch.setattr(scope, "set_channel_coupling", lambda ch, c: None)
        monkeypatch.setattr(runner, "abort_sleep", lambda *a, **k: True)
        measure = _cap_with_interpulse(2, 2)
        stuck = _cap_with_interpulse(40, 40)         # both SD ~11.5 mV forever
        calls = {"n": 0}

        def _recap():
            calls["n"] += 1
            return measure if calls["n"] == 1 else stuck

        runner.measure_electrode_dc_offsets_and_switch_to_ac(_recap)
        from stimtest.experiments.base import _AC_SETTLE_MAX_CHECKS
        assert calls["n"] == 1 + _AC_SETTLE_MAX_CHECKS
    finally:
        stim.close(); scope.close()


# ---- VT wires the helper + threads the offset ------------------------
def test_vt_wires_offset_capture_and_addback():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "stimtest"
           / "experiments" / "voltage_transient.py").read_text(encoding="utf-8")
    assert "measure_electrode_dc_offsets_and_switch_to_ac(" in src
    assert "electrode_dc_offsets=self._electrode_dc_offset" in src
