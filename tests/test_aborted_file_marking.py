"""Quit/aborted runs are reflected in the saved files.

Operator: "When the experiment is quit, make sure that the files reflect
that."  Two mechanisms:
  * the .npz is saved with ``incomplete=True`` (POLARIS shows the
    "incomplete run" badge), and
  * the session-level ``.npz`` / ``.xlsx`` get an ``_ABORTED`` filename
    suffix (``RunnerWorker._mark_files_aborted``) so the file browser shows a
    partial run at a glance.
"""
from __future__ import annotations

import sys
import types

import numpy as np
import pytest


@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _session():
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.session import Capture, ChannelRun, Session, TestParameters
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    s = Session(notebook="nb", subject="subj", test=test)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    c = Capture(index=0, pattern=pat)
    c.time_us = np.linspace(-10, 100, 40)
    c.v_mon_v = np.zeros(40); c.i_mon_ua = np.zeros(40)
    run.captures.append(c); s.add_run(run)
    return s


def test_incomplete_flag_round_trips(tmp_path):
    from stimtest.persistence import save_session_npz, load_session_meta
    p = tmp_path / "sess.npz"
    save_session_npz(_session(), p, incomplete=True)
    assert load_session_meta(p).get("incomplete") is True
    # A completed run stays incomplete=False.
    p2 = tmp_path / "done.npz"
    save_session_npz(_session(), p2, incomplete=False)
    assert load_session_meta(p2).get("incomplete") is False


def test_mark_files_aborted_renames_npz_and_xlsx(_app, tmp_path):
    """``_mark_files_aborted`` moves ``<stem>.npz`` + ``<stem>.xlsx`` to
    ``<stem>_ABORTED.*`` and removes the base names."""
    from stimtest.gui.experiment_tabs import RunnerWorker
    npz = tmp_path / "run.npz"
    xlsx = tmp_path / "run.xlsx"
    npz.write_bytes(b"npz"); xlsx.write_bytes(b"xlsx")
    logged: list = []
    stub = types.SimpleNamespace(
        save_path=npz,
        log_msg=types.SimpleNamespace(emit=logged.append))
    RunnerWorker._mark_files_aborted(stub)
    assert (tmp_path / "run_ABORTED.npz").exists()
    assert (tmp_path / "run_ABORTED.xlsx").exists()
    assert not npz.exists()
    assert not xlsx.exists()
    assert any("ABORTED" in m for m in logged)


def test_mark_files_aborted_is_noop_without_xlsx(_app, tmp_path):
    """Only the files that exist are renamed — no .xlsx (auto-export off) is
    fine, and a missing base file never raises."""
    from stimtest.gui.experiment_tabs import RunnerWorker
    npz = tmp_path / "run.npz"
    npz.write_bytes(b"npz")
    stub = types.SimpleNamespace(
        save_path=npz,
        log_msg=types.SimpleNamespace(emit=lambda *_a: None))
    RunnerWorker._mark_files_aborted(stub)   # must not raise
    assert (tmp_path / "run_ABORTED.npz").exists()
    assert not (tmp_path / "run_ABORTED.xlsx").exists()
