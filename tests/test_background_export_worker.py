"""Background export thread — TIF renders + XLSX writes in parallel with
the experiment (operator: "Is it possible to do parallel processing of
quickly saving TIF files and writing to XLSX during the experiment?").

The runner thread only ENQUEUES export work; `_ExportWorker` (a QThread)
renders matplotlib figures and rewrites the workbook off the runner's
critical path.  XLSX submissions COALESCE per path (a snapshot burst →
one rewrite of the freshest state) and write atomically via a ``.part``
side-file; successful plot writes are recorded so the end-of-run pass
only back-fills misses.
"""
from __future__ import annotations

import sys
import time

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _session_with_one_run():
    from stimtest.session import Capture, ChannelRun, Session, TestParameters
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.waveforms import PulsePattern
    pattern = PulsePattern.biphasic(amplitude_ua=50.0)
    array = ElectrodeArray.utah_4x4()
    config = Configuration.monopolar(1)
    test = TestParameters(experiment="CP", pattern=pattern,
                          configuration=config, array=array,
                          duration_s=0.0)
    session = Session(notebook="t", subject="exp", test=test)
    run = ChannelRun(configuration=config, surface_area_um2=5000.0)
    cap = Capture(index=0, pattern=pattern)
    t = np.linspace(-100.0, 500.0, 1200)
    cap.time_us = t
    cap.v_mon_v = np.where((t >= 0) & (t < 200), -0.2, 0.0)
    cap.i_mon_ua = np.where((t >= 0) & (t < 200), -50.0, 0.0)
    run.captures.append(cap)
    session.add_run(run)
    return session, run


def test_worker_renders_plot_and_records_success(qapp, tmp_path):
    from stimtest.gui.experiment_tabs import _ExportWorker
    session, run = _session_with_one_run()
    w = _ExportWorker()
    w.start()
    w.submit_plot(session, run, tmp_path, "png")
    w.finish()
    assert w.wait(60_000), "export worker must drain and exit"
    written = list(tmp_path.glob("*.png"))
    assert len(written) == 1, written
    assert id(run) in w.exported_run_ids


def test_worker_coalesces_xlsx_and_writes_atomically(qapp, tmp_path):
    from stimtest.gui.experiment_tabs import _ExportWorker
    session, run = _session_with_one_run()
    out = tmp_path / "t_exp.xlsx"
    w = _ExportWorker()
    # Enqueue a burst BEFORE starting the thread — the coalescer must
    # collapse them to (at least) one write of the freshest state.
    for _ in range(5):
        w.submit_xlsx(session, out)
    w.start()
    w.finish()
    assert w.wait(60_000)
    assert out.exists(), "coalesced xlsx must be written"
    assert not out.with_suffix(".xlsx.part").exists(), \
        "atomic .part side-file must be replaced away"


def test_worker_failure_is_contained(qapp, tmp_path):
    # A bad task must log-and-continue, never kill the thread.
    from stimtest.gui.experiment_tabs import _ExportWorker
    session, run = _session_with_one_run()
    w = _ExportWorker()
    msgs = []
    w.log_msg.connect(msgs.append)
    w.start()
    w.submit_plot(None, None, tmp_path, "png")      # broken task
    w.submit_plot(session, run, tmp_path, "png")    # good task after it
    w.finish()
    assert w.wait(60_000)
    assert id(run) in w.exported_run_ids, \
        "a failed task must not stop subsequent exports"


def _runner_worker(qapp, tmp_path, auto=True):
    """A RunnerWorker with a stub runner + a fake export thread that just
    COUNTS submit_xlsx calls (the real export QThread it starts in __init__
    is stopped and swapped out)."""
    from stimtest.gui.experiment_tabs import RunnerWorker

    class _StubRunner:
        def subscribe(self, cb):
            pass

    class _FakeExport:
        def __init__(self):
            self.calls = []

        def submit_xlsx(self, session, path):
            self.calls.append(path)

    w = RunnerWorker(_StubRunner(), save_path=tmp_path / "t.xlsx",
                     auto_export_xlsx=auto)
    w._export.finish()
    w._export.wait(10_000)          # stop the real export thread
    w._export = _FakeExport()
    return w


def test_runner_worker_throttles_in_run_xlsx_refresh(qapp, tmp_path):
    """Operator: "why is the workbook refreshed so many times?"  In-run .xlsx
    refreshes are rate-limited to one per _XLSX_MIN_INTERVAL_S (a fast VT
    sweep used to rewrite the whole workbook once per capture, ~70×).  The
    final end-of-run write uses force=True to bypass the throttle."""
    w = _runner_worker(qapp, tmp_path, auto=True)
    sess = object()

    # First in-run refresh writes; an immediate second is throttled away.
    w._maybe_refresh_xlsx(sess, w.save_path)
    w._maybe_refresh_xlsx(sess, w.save_path)
    w._maybe_refresh_xlsx(sess, w.save_path)
    assert len(w._export.calls) == 1, "burst must collapse to ONE in-run write"

    # Once the interval elapses, the next in-run refresh writes again.
    w._last_xlsx_at -= (w._XLSX_MIN_INTERVAL_S + 1.0)
    w._maybe_refresh_xlsx(sess, w.save_path)
    assert len(w._export.calls) == 2

    # force=True (the final end-of-run write) always fires, even inside the
    # throttle window.
    w._maybe_refresh_xlsx(sess, w.save_path, force=True)
    assert len(w._export.calls) == 3

    w._export = None  # drop the fake; RunnerWorker has no live thread now


def test_runner_worker_no_xlsx_when_autoexport_off(qapp, tmp_path):
    w = _runner_worker(qapp, tmp_path, auto=False)
    w._maybe_refresh_xlsx(object(), w.save_path)
    w._maybe_refresh_xlsx(object(), w.save_path, force=True)
    assert w._export.calls == [], "auto-export off ⇒ never refresh the workbook"
    w._export = None
