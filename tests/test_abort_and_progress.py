"""Tests for abort responsiveness + progress event infrastructure.

Closes Task #56.  Verifies:

* ``ExperimentRunner.abort_sleep`` returns False promptly when
  abort is set partway through, True when allowed to complete.
* ``_emit_progress`` produces well-formed ProgressInfo events that
  subscribers receive.
* ``ProgressInfo`` dataclass round-trips its fields.
* ``RunnerWorker.progress`` Qt signal fires when the runner emits
  a progress event, with the ProgressInfo payload intact.
* The VT rescale loop short-circuits on abort (source-level check
  — the runtime branch is hard to unit-test without a full
  simulator setup).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# ProgressInfo dataclass shape
# ---------------------------------------------------------------------------
def test_progress_info_defaults():
    """Optional fields have sane defaults so callers can construct
    minimal ProgressInfo with just step + total."""
    from stimtest.experiments.base import ProgressInfo

    p = ProgressInfo(step=3, total=10)
    assert p.step == 3
    assert p.total == 10
    assert p.label == ""
    assert p.started_at == 0.0


def test_progress_info_fields_round_trip():
    from stimtest.experiments.base import ProgressInfo

    p = ProgressInfo(
        step=5, total=16, label="VT CH 3 (MP)", started_at=12345.678)
    assert p.step == 5
    assert p.total == 16
    assert p.label == "VT CH 3 (MP)"
    assert p.started_at == pytest.approx(12345.678)


# ---------------------------------------------------------------------------
# ExperimentEvent gains a progress field
# ---------------------------------------------------------------------------
def test_experiment_event_progress_field_optional():
    """Existing callers that don't pass progress= still work — the
    field is Optional[ProgressInfo] and defaults to None."""
    from stimtest.experiments.base import ExperimentEvent
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray

    sess = Session(
        notebook="", subject="", user_name="", user_email="",
        test=TestParameters(
            experiment="VT", duration_s=0.0,
            polarization_method="MP",
            counter_electrode_label="",
            reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1),
            pattern=PulsePattern.biphasic(amplitude_ua=100.0),
            array=ElectrodeArray.utah_4x4()),
        runs=[],
    )

    ev = ExperimentEvent(kind="capture", session=sess)
    assert ev.progress is None


# ---------------------------------------------------------------------------
# abort_sleep helper
# ---------------------------------------------------------------------------
def _make_minimal_runner():
    """Build a stripped-down ExperimentRunner instance for helper
    testing — concrete subclass with a no-op ``run`` so the ABC
    instantiation gate is satisfied, plus we bypass ``__init__``
    so we don't need real hardware / a populated session."""
    from stimtest.experiments.base import ExperimentRunner, ExperimentResult

    class _ConcreteRunner(ExperimentRunner):
        def run(self):
            return ExperimentResult(session=self.session, captures=[])

    r = _ConcreteRunner.__new__(_ConcreteRunner)
    r._subscribers = []
    r._abort_requested = False
    # Minimal session/test for _emit_progress.
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    r.session = Session(
        notebook="", subject="", user_name="", user_email="",
        test=TestParameters(
            experiment="VT", duration_s=0.0,
            polarization_method="MP",
            counter_electrode_label="",
            reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1),
            pattern=PulsePattern.biphasic(amplitude_ua=100.0),
            array=ElectrodeArray.utah_4x4()),
        runs=[],
    )
    return r


def test_abort_sleep_returns_true_on_full_sleep():
    """sleep(short) without abort returns True."""
    r = _make_minimal_runner()
    t0 = time.monotonic()
    ok = r.abort_sleep(0.10)  # 100 ms
    elapsed = time.monotonic() - t0
    assert ok is True
    # Should have actually slept ~100 ms (with some scheduling slop).
    assert 0.08 < elapsed < 0.30


def test_abort_sleep_returns_false_when_aborted_mid_sleep():
    """abort_sleep should return False quickly when abort fires
    during the sleep."""
    r = _make_minimal_runner()

    def _abort_after(delay):
        time.sleep(delay)
        r._abort_requested = True

    t0 = time.monotonic()
    abort_thread = threading.Thread(
        target=_abort_after, args=(0.05,), daemon=True)
    abort_thread.start()
    ok = r.abort_sleep(2.0, chunk_s=0.02)  # asks for 2s, should bail at ~50 ms
    elapsed = time.monotonic() - t0
    abort_thread.join(timeout=1.0)

    assert ok is False
    # Should have bailed within ~150 ms (50 ms wait + 1 chunk + slop).
    assert elapsed < 0.30, (
        f"abort_sleep took {elapsed:.3f} s — should have responded "
        f"within ~150 ms of abort firing")


def test_abort_sleep_zero_returns_immediately():
    """Zero / negative seconds is a no-op that just checks abort."""
    r = _make_minimal_runner()
    t0 = time.monotonic()
    ok = r.abort_sleep(0.0)
    elapsed = time.monotonic() - t0
    assert ok is True
    assert elapsed < 0.01  # truly immediate

    r._abort_requested = True
    ok = r.abort_sleep(0.0)
    assert ok is False


def test_abort_sleep_already_aborted_returns_false():
    """If abort is set BEFORE the call, return False without sleeping."""
    r = _make_minimal_runner()
    r._abort_requested = True
    t0 = time.monotonic()
    ok = r.abort_sleep(1.0)
    elapsed = time.monotonic() - t0
    assert ok is False
    assert elapsed < 0.10  # didn't actually sleep


def test_abort_sleep_subchunk_duration_still_checks_abort():
    """A sub-chunk sleep (seconds < chunk_s) still returns the
    correct abort state."""
    r = _make_minimal_runner()
    # 5 ms is less than the default 50 ms chunk; full sleep.
    ok = r.abort_sleep(0.005)
    assert ok is True

    r._abort_requested = True
    ok = r.abort_sleep(0.005)
    assert ok is False


# ---------------------------------------------------------------------------
# _emit_progress
# ---------------------------------------------------------------------------
def test_emit_progress_dispatches_to_subscribers():
    """_emit_progress produces an event with kind='progress' and a
    populated ProgressInfo payload that subscribers receive."""
    from stimtest.experiments.base import ProgressInfo

    r = _make_minimal_runner()
    received: list = []
    r.subscribe(received.append)

    r._emit_progress(step=3, total=16,
                     label="VT CH 5 (MP)", started_at=1000.0)

    assert len(received) == 1
    ev = received[0]
    assert ev.kind == "progress"
    assert isinstance(ev.progress, ProgressInfo)
    assert ev.progress.step == 3
    assert ev.progress.total == 16
    assert ev.progress.label == "VT CH 5 (MP)"
    assert ev.progress.started_at == pytest.approx(1000.0)


def test_emit_progress_coerces_types():
    """_emit_progress should coerce step/total to int and started_at
    to float so float-typed loop indices etc. don't break."""
    r = _make_minimal_runner()
    received: list = []
    r.subscribe(received.append)

    r._emit_progress(step=3.7, total=16.0, label="x", started_at=10)

    assert received[0].progress.step == 3
    assert received[0].progress.total == 16
    assert isinstance(received[0].progress.started_at, float)


# ---------------------------------------------------------------------------
# RunnerWorker.progress signal
# ---------------------------------------------------------------------------
def test_runner_worker_emits_progress_signal_on_progress_event():
    """When the runner emits a progress event, RunnerWorker should
    re-emit on its progress Qt signal with the ProgressInfo payload."""
    pytest.importorskip("PyQt6.QtCore")
    from PyQt6 import QtCore
    from stimtest.gui.experiment_tabs import RunnerWorker
    from stimtest.experiments.base import (
        ExperimentEvent, ProgressInfo,
    )

    app = QtCore.QCoreApplication.instance()
    if app is None:
        app = QtCore.QCoreApplication([])

    class _StubRunner:
        def __init__(self):
            from stimtest.session import Session, TestParameters
            from stimtest.waveforms import PulsePattern
            from stimtest.electrode import Configuration, ElectrodeArray
            self.session = Session(
                notebook="", subject="", user_name="", user_email="",
                test=TestParameters(
                    experiment="VT", duration_s=0.0,
                    polarization_method="MP",
                    counter_electrode_label="",
                    reference_electrode_label="",
                    target_charge_phase_nc=0.0,
                    configuration=Configuration.monopolar(1),
                    pattern=PulsePattern.biphasic(amplitude_ua=100.0),
                    array=ElectrodeArray.utah_4x4()),
                runs=[],
            )
            self._cb = None

        def subscribe(self, cb):
            self._cb = cb

        def request_continue(self):
            pass

        def run(self):
            # Emit one progress event, then succeed.
            self._cb(ExperimentEvent(
                kind="progress",
                session=self.session,
                progress=ProgressInfo(
                    step=7, total=16, label="VT CH 3",
                    started_at=time.monotonic()),
            ))
            from stimtest.experiments.base import ExperimentResult
            return ExperimentResult(session=self.session, captures=[])

    runner = _StubRunner()
    worker = RunnerWorker(runner, save_path=None)
    received: list = []
    worker.progress.connect(received.append)
    worker.run()
    app.processEvents()

    assert len(received) == 1
    prog = received[0]
    assert prog.step == 7
    assert prog.total == 16
    assert prog.label == "VT CH 3"


# ---------------------------------------------------------------------------
# VT rescale loop abort short-circuit (source-level check)
# ---------------------------------------------------------------------------
def test_vt_rescale_loop_checks_abort():
    """The MAX_RECAPTURE rescale loop in voltage_transient.py should
    check _abort_requested at the top of each iteration so Stop
    breaks out promptly even when deep in scope rescaling."""
    src_path = Path(__file__).resolve().parent.parent / (
        "stimtest/experiments/voltage_transient.py")
    src = src_path.read_text(encoding="utf-8")
    # Find the rescale loop and verify the abort check is present
    # near the top.  Brittle string-match but adequate — the alternative
    # is a full simulator run which is hours of test setup.
    rescale_loop_start = src.find("for _attempt in range(MAX_RECAPTURE + 1):")
    assert rescale_loop_start > 0, (
        "rescale loop signature 'for _attempt in range(MAX_RECAPTURE + 1):' "
        "not found — has the loop been renamed?")
    # Look within 1500 chars of the loop start for the abort check.
    # Wide enough to include our documenting comment block AND the
    # actual code that follows it.
    window = src[rescale_loop_start:rescale_loop_start + 1500]
    assert "self._abort_requested" in window, (
        "rescale loop body should check self._abort_requested near "
        "the top so Stop breaks out within one iteration — Task #56.")
