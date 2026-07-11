"""LP resume from a crashed .npz.

Operator: "For LP experiment, I want to be able to continue or near where
a NPZ file stopped, in case the program or computer crashes."

``LongPulsingExperiment(..., resume=True)`` continues the last ChannelRun
already present in the session (loaded from a partial .npz): it appends
new captures after the prior ones, with the snapshot cadence + duration
budget resumed at the last capture's ``elapsed_time_s`` (pulsing) time.

Also covers the latent persistence bug this surfaced: LP trims snapshot
arrays to ``None``; ``save_session_npz`` must coerce those to empty
arrays so the partial .npz is actually LOADABLE (numpy refuses object
arrays under ``allow_pickle=False``) — otherwise crash recovery is moot.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.long_pulsing import (
    LongPulsingExperiment, LongPulsingPolicy)
from stimtest.hardware.simulator import (
    SimulatedOscilloscope, SimulatedStimulator)
from stimtest.persistence import (
    load_session_npz, save_session_npz)
from stimtest.session import (
    Capture, ChannelRun, Session, TestParameters)
from stimtest.waveforms import Phase, PulsePattern


def _session(dur: float) -> Session:
    arr = ElectrodeArray.utah_4x4()
    cfg = Configuration.monopolar(1)
    pat = PulsePattern(
        phases=[Phase(amplitude_ua=-20.0, width_us=100.0, delay_after_us=100.0),
                Phase(amplitude_ua=20.0, width_us=100.0, delay_after_us=0.0)],
        rate_hz=400.0, repetitions=0)
    test = TestParameters(experiment="LP", pattern=pat, configuration=cfg,
                          array=arr, duration_s=dur)
    return Session(notebook="nb", subject="el", test=test)


def _hw(navg=2):
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    scope._expected_acq_navg = navg
    return stim, scope


def _run_lp(session, dur, snap, resume=False):
    stim, scope = _hw()
    try:
        LongPulsingExperiment(
            session, stim, scope, amplitude_ua=20.0,
            policy=LongPulsingPolicy(duration_s=dur, characterize_every_s=1e9,
                                     capture_during_pulsing_every_s=snap),
            resume=resume).run()
    finally:
        stim.close(); scope.close()


# ----------------------------------------------------------------------
# Persistence: trimmed (None) snapshot arrays must round-trip
# ----------------------------------------------------------------------
def test_trimmed_lp_session_is_loadable(tmp_path: Path):
    """An LP session with trimmed snapshot arrays (None) must save + load
    — the partial crash-recovery .npz is exactly this shape."""
    s = _session(0.3)
    _run_lp(s, 0.3, 0.06)
    snaps = [c for c in s.runs[0].captures if c.status.notes == "snapshot"]
    assert snaps, "expected snapshot captures"
    # LP trims arrays to None by default.
    assert snaps[0].v_mon_v is None
    p = tmp_path / "trimmed.npz"
    save_session_npz(s, p, incomplete=True)
    loaded = load_session_npz(p)            # must not raise
    lc = loaded.runs[0].captures[0]
    # Trimmed arrays come back as empty (not None / object).
    assert lc.v_mon_v is not None and lc.v_mon_v.size == 0
    # Metrics (incl. the time columns) survived.
    assert math.isfinite(lc.metrics.elapsed_time_s)


# ----------------------------------------------------------------------
# Runner resume
# ----------------------------------------------------------------------
def test_resume_continues_same_run(tmp_path: Path):
    # Phase 1: a "crashed" partial run (ran to 0.2 s).
    s1 = _session(0.2)
    _run_lp(s1, 0.2, 0.06)
    n_prior = len(s1.runs[0].captures)
    assert n_prior >= 2
    p = tmp_path / "partial.npz"
    save_session_npz(s1, p, incomplete=True)

    # Recover: load, and the ORIGINAL run was meant to be 0.5 s.
    s2 = load_session_npz(p)
    s2.test.duration_s = 0.5
    prior_last = max(c.metrics.elapsed_time_s for c in s2.runs[0].captures
                     if math.isfinite(c.metrics.elapsed_time_s))

    # Phase 2: RESUME.
    _run_lp(s2, 0.5, 0.06, resume=True)

    assert len(s2.runs) == 1, "resume must continue the run, not duplicate it"
    caps = s2.runs[0].captures
    assert len(caps) > n_prior, "resume should add captures"
    snaps = [c for c in caps if c.status.notes == "snapshot"]
    new = snaps[n_prior:]
    # New captures continue strictly past where it stopped.
    assert all(c.metrics.elapsed_time_s > prior_last - 1e-6 for c in new)
    # Still on the same fixed 0.06 grid (no drift across the resume seam).
    assert all(abs(round(c.metrics.scheduled_time_s / 0.06) * 0.06
                   - c.metrics.scheduled_time_s) < 1e-6 for c in new)
    # Stopped near the full duration.
    assert caps[-1].metrics.elapsed_time_s <= 0.5 + 0.06


def test_resume_of_complete_run_is_noop():
    s1 = _session(0.2)
    _run_lp(s1, 0.2, 0.06)
    n_prior = len(s1.runs[0].captures)
    # Resume with duration already covered → no new captures, no crash.
    _run_lp(s1, 0.2, 0.06, resume=True)
    assert len(s1.runs) == 1
    assert len(s1.runs[0].captures) == n_prior


def test_resume_legacy_without_time_columns(tmp_path: Path):
    """A pre-time-column archive (no elapsed_time_s) still resumes, using
    the snapshot-count × interval fallback for the offset."""
    s = _session(0.5)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    # 3 prior "snapshots" with NO elapsed_time_s (legacy).
    for i in range(3):
        c = Capture(index=i, pattern=s.test.pattern)
        c.status.notes = "snapshot"
        # leave scheduled/elapsed NaN
        run.captures.append(c)
    s.add_run(run)
    # Resume: offset should fall back to 3 × 0.06 = 0.18 s.
    _run_lp(s, 0.5, 0.06, resume=True)
    caps = s.runs[0].captures
    assert len(caps) > 3
    new = [c for c in caps if c.status.notes == "snapshot"][3:]
    # First new snapshot lands strictly after the estimated 0.18 s offset.
    assert new and new[0].metrics.scheduled_time_s >= 0.18 - 1e-6


# ----------------------------------------------------------------------
# GUI wiring
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def test_lp_tab_has_resume_button(qapp):
    from stimtest.gui.experiment_tabs import LongPulsingTab
    tab = LongPulsingTab(ElectrodeArray.utah_4x4())
    assert hasattr(tab, "resume_btn")
    assert "Resume" in tab.resume_btn.text()
    assert hasattr(tab, "resume_from_npz_clicked")


def test_derive_snap_interval_median(qapp):
    from stimtest.gui.experiment_tabs import LongPulsingTab
    tab = LongPulsingTab(ElectrodeArray.utah_4x4())
    caps = []
    for i, t in enumerate((0.0, 30.0, 60.0, 90.0)):
        c = Capture(index=i, pattern=PulsePattern.biphasic(amplitude_ua=10.0))
        c.status.notes = "snapshot"
        c.metrics.scheduled_time_s = t
        caps.append(c)
    assert tab._derive_snap_interval_s(caps) == pytest.approx(30.0)
