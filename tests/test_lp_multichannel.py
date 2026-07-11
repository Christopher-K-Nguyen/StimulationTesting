"""Long-Term Pulsing: pulse ALL selected monopolar channels + render the
first snapshot's waveform.

Operator:
  * "LP is not pulsing all of the channels in monopolar like I selected."
    → ``pulse_channels`` makes LP load the REAL pattern on every selected
    channel (chronic multi-channel stim), zeroing the rest; the scope
    monitors the first (primary) channel.
  * "I am not seeing the snapshot at the beginning of the pulsing; this
    should include plotting and metrics." → each snapshot is emitted WITH
    its raw arrays (so the live waveform plot renders it); only the PREVIOUS
    snapshot is trimmed afterwards, so memory stays bounded to one untrimmed
    snapshot while every snapshot (incl. the first) renders live.
"""
from __future__ import annotations

import numpy as np

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.long_pulsing import (LongPulsingExperiment,
                                               LongPulsingPolicy)
from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                         SimulatedStimulator)
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


def _runner(pulse_channels=None, *, snap_s=0.05, dur=0.5):
    pattern = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    test = TestParameters(experiment="LP", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4(), duration_s=dur)
    sess = Session(notebook="n", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    r = LongPulsingExperiment(
        sess, stim, scope, amplitude_ua=50.0, pulse_channels=pulse_channels,
        policy=LongPulsingPolicy(duration_s=dur, characterize_every_s=1e9,
                                 capture_during_pulsing_every_s=snap_s))
    return r, stim, scope


def _load_spy(stim):
    """Record every load_channel(ch, pattern) as ch -> list of |amplitude|."""
    loads: dict[int, list[float]] = {}
    orig = stim.load_channel

    def spy(ch, pat, *a, **k):
        loads.setdefault(int(ch), []).append(
            abs(pat.excitation_phase.amplitude_ua))
        return orig(ch, pat, *a, **k)

    stim.load_channel = spy
    return loads


# --------------------------------------------------------------- #82
def test_lp_pulses_all_selected_monopolar_channels():
    r, stim, scope = _runner(pulse_channels=[1, 3, 5, 7])
    loads = _load_spy(stim)
    try:
        r.run()
        real = sorted(ch for ch, amps in loads.items() if any(a > 0 for a in amps))
        assert real == [1, 3, 5, 7], real
        # Every OTHER loaded channel got only the zero pattern.
        for ch, amps in loads.items():
            if ch not in (1, 3, 5, 7):
                assert all(a == 0 for a in amps), (ch, amps)
    finally:
        stim.close(); scope.close()


def test_lp_single_channel_default_unchanged():
    """Without pulse_channels, LP pulses ONLY config.active (classic
    single-channel behaviour)."""
    r, stim, scope = _runner(pulse_channels=None)
    loads = _load_spy(stim)
    try:
        r.run()
        real = sorted(ch for ch, amps in loads.items() if any(a > 0 for a in amps))
        assert real == [1], real
    finally:
        stim.close(); scope.close()


def test_pulse_set_always_includes_primary():
    """``_pulse_set`` de-dups + guarantees the monitored primary is in the
    pulse set even if the caller omitted it."""
    r, stim, scope = _runner(pulse_channels=[3, 5, 3])
    try:
        # config.active == 1 (the primary) is prepended; duplicates dropped.
        assert r._pulse_set(Configuration.monopolar(1)) == [1, 3, 5]
    finally:
        stim.close(); scope.close()


def test_lp_multichannel_monitors_and_plots_each_channel():
    """Multichannel monopolar LP cycles the single monitor pickoff across
    EVERY pulse channel and gives each its OWN ChannelRun, so each routes to
    its own plot page (operator: "pulsing all of the channels in monopolar …
    but only CH01 was plotted").  The single monitor pickoff means only one
    channel can be captured at an instant, so the runner rotates it per
    snapshot round."""
    r, stim, scope = _runner(pulse_channels=[1, 2, 4], snap_s=0.05, dur=0.5)
    monitored: list[int] = []
    orig_mon = stim.set_monitor_channel

    def mon_spy(ch, *a, **k):
        monitored.append(int(ch))
        return orig_mon(ch, *a, **k)

    stim.set_monitor_channel = mon_spy
    try:
        res = r.run()
        # One ChannelRun per pulse channel, keyed by its monopolar config.
        actives = sorted(int(run.configuration.active)
                         for run in res.session.runs)
        assert actives == [1, 2, 4], actives
        # Every channel got snapshot captures on its OWN run — not just CH01.
        for run in res.session.runs:
            snaps = [c for c in run.captures if c.status.notes == "snapshot"]
            assert snaps, f"CH{int(run.configuration.active):02d} had no snapshots"
        # The monitor pickoff was cycled to every channel (the fix): pre-fix
        # only CH01 was ever monitored/plotted.
        assert set(monitored) >= {1, 2, 4}, monitored
    finally:
        stim.close(); scope.close()


def test_lp_single_channel_still_one_run():
    """Single-channel LP (no pulse_channels) keeps the classic one-run path —
    the multichannel per-channel-run branch must not fire."""
    r, stim, scope = _runner(pulse_channels=None, snap_s=0.05, dur=0.4)
    try:
        res = r.run()
        assert len(res.session.runs) == 1
        assert int(res.session.runs[0].configuration.active) == 1
    finally:
        stim.close(); scope.close()


# --------------------------------------------------------------- #83
def test_first_snapshot_is_emitted_with_arrays_for_rendering():
    """Every snapshot (including the FIRST) reaches the GUI WITH its raw
    arrays so the live waveform plot can render it; the trim happens only
    afterwards (on the next snapshot)."""
    r, stim, scope = _runner(snap_s=0.05, dur=0.4)
    emitted_has_arrays: list[bool] = []
    orig_emit = r._emit

    def emit_spy(ev):
        # Snapshot the array state AT EMIT TIME (the same object is trimmed
        # later, so we must check it now).
        cap = getattr(ev, "capture", None)
        if (cap is not None and getattr(ev, "kind", "") == "capture"
                and getattr(cap.status, "notes", "") == "snapshot"):
            emitted_has_arrays.append(cap.v_mon_v is not None
                                      and cap.v_mon_v.size > 0)
        return orig_emit(ev)

    r._emit = emit_spy
    try:
        r.run()
        assert emitted_has_arrays, "no snapshot was emitted"
        # The FIRST snapshot must carry arrays when it's emitted.
        assert emitted_has_arrays[0] is True
        # In fact EVERY snapshot is emitted with arrays (the trim is deferred).
        assert all(emitted_has_arrays), emitted_has_arrays
    finally:
        stim.close(); scope.close()


def test_only_the_latest_snapshot_stays_untrimmed():
    """Memory bound: after the run, at most the last snapshot keeps arrays;
    the earlier ones are trimmed."""
    r, stim, scope = _runner(snap_s=0.05, dur=0.5)
    try:
        res = r.run()
        snaps = [c for c in res.captures if c.status.notes == "snapshot"]
        assert len(snaps) >= 2
        untrimmed = [c for c in snaps if c.v_mon_v is not None]
        assert len(untrimmed) <= 1, (
            f"{len(untrimmed)} snapshots kept arrays; expected ≤ 1")
    finally:
        stim.close(); scope.close()


# --------------------------------------------------------------- #81
def _pol(**kw):
    from stimtest.experiments.long_pulsing import LongPulsingPolicy
    base = dict(duration_s=0.6, characterize_every_s=0.25,
                capture_during_pulsing_every_s=0.05)
    base.update(kw)
    return LongPulsingPolicy(**base)


def _run_logs(policy):
    r, stim, scope = _runner()
    r.policy = policy
    logs: list[str] = []
    orig = r._emit

    def spy(ev):
        if getattr(ev, "kind", "") == "log":
            logs.append(getattr(ev, "message", ""))
        return orig(ev)

    r._emit = spy
    try:
        res = r.run()
        return res, logs
    finally:
        stim.close(); scope.close()


def test_periodic_pause_only_no_char_captures():
    """Pause enabled, max-VT disabled → the periodic event pauses stim but
    runs NO characterization sweep."""
    res, logs = _run_logs(_pol(run_max_vt=False, pause_duration_s=0.05,
                               fire_periodic_at_start=True))
    chars = [c for c in res.captures if "char@" in (c.status.notes or "")]
    assert chars == []
    assert any("Pausing stimulation" in m for m in logs)
    assert any("Pause complete" in m for m in logs)


def test_periodic_pause_fires_at_start():
    """fire_periodic_at_start → the first pause happens at t=0."""
    res, logs = _run_logs(_pol(run_max_vt=False, pause_duration_s=0.05,
                               fire_periodic_at_start=True))
    assert any("t = 0s" in m and "Pausing" in m for m in logs)


def test_periodic_max_vt_runs_characterization():
    """max-VT enabled → the periodic event appends char@ captures."""
    res, logs = _run_logs(_pol(run_max_vt=True, pause_duration_s=0.0,
                               characterize_every_s=0.3))
    chars = [c for c in res.captures if "char@" in (c.status.notes or "")]
    assert chars, "expected characterization captures from the periodic max-VT"
    assert any("Periodic max-VT" in m for m in logs)


def test_no_periodic_event_when_disabled():
    """Huge period (the tab's disabled default) → no char, no pause."""
    res, logs = _run_logs(_pol(characterize_every_s=1e9, run_max_vt=True,
                               pause_duration_s=30.0))
    chars = [c for c in res.captures if "char@" in (c.status.notes or "")]
    assert chars == []
    assert not any("Pausing stimulation" in m for m in logs)
