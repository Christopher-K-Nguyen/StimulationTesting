"""POLARIS metric editing: response-class override (Good/Broken/Open) that
recomputes the appropriate metrics, hand-editable metric values, and save-back
to the source .npz.

Operator: "Allow the user to save changes to the metrics when adjusting the
values on POLARIS, include the option of setting the channel/combo as
Good/Broken/Open (the appropriate metrics are to be computed as well)."
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _mk_session_npz(path):
    from stimtest.session import (Session, TestParameters, Capture, ChannelRun,
                                  Configuration)
    from stimtest.electrode import ElectrodeArray
    from stimtest.waveforms import PulsePattern
    from stimtest.metrics import compute_metrics
    from stimtest.persistence import save_session_npz
    pat = PulsePattern.biphasic(amplitude_ua=50.0, polarity=-1)
    cfg = Configuration.monopolar(1)
    test = TestParameters(experiment="VT", pattern=pat, configuration=cfg,
                          array=ElectrodeArray.utah_4x4())
    sess = Session(notebook="t", subject="s", test=test)
    # A GOOD electrode: IR step (72 mV) + curved Faradaic ramp to 426 mV.
    t = np.linspace(-45.0, 595.0, 2000)
    v = np.zeros_like(t)
    i = np.zeros_like(t)
    m1 = (t >= 0.0) & (t <= 200.0)
    tau = t[m1] / 200.0
    v[m1] = -0.072 - 0.354 * tau ** 1.5
    m2 = (t > 200.0) & (t <= 400.0)
    tau2 = (t[m2] - 200.0) / 200.0
    v[m2] = -0.426 + 0.426 * tau2 ** 1.5
    i[m1] = -50.0
    i[m2] = 50.0
    cap = Capture(index=0, pattern=pat)
    cap.time_us = t
    cap.v_mon_v = v
    cap.i_mon_ua = i
    compute_metrics(cap, 5000.0)
    sess.add_run(ChannelRun(configuration=cfg, captures=[cap]))
    save_session_npz(sess, path)


def _select_run(panel):
    from stimtest.gui.viewer import KIND_RUN, ROLE_KIND
    tree = panel.tree

    def walk(it):
        for k in range(it.childCount()):
            yield it.child(k)
            yield from walk(it.child(k))

    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        if top.childCount() == 1 and top.child(0).text(0) == "…loading":
            panel._load_session_into_tree(top)
        for node in [top, *walk(top)]:
            if node.data(0, ROLE_KIND) == KIND_RUN:
                tree.setCurrentItem(node)
                return node
    return None


def test_parse_metric_value():
    from stimtest.gui.viewer import _parse_metric_value
    assert _parse_metric_value("35.65 nF") == 35.65
    assert _parse_metric_value("1.403 V") == 1.403
    assert abs(_parse_metric_value("−0.80") - (-0.80)) < 1e-9   # unicode minus
    assert _parse_metric_value("2.5e-3 s") == 2.5e-3
    assert _parse_metric_value("—") is None
    assert _parse_metric_value("Good") is None


def test_class_override_recompute_and_save(qapp, tmp_path, monkeypatch):
    from PyQt6 import QtWidgets
    from stimtest.gui.viewer import ViewerPanel
    from stimtest.persistence import load_session_npz
    p_npz = tmp_path / "sess.npz"
    _mk_session_npz(p_npz)
    panel = ViewerPanel()
    panel.load_session_file(Path(p_npz))
    node = _select_run(panel)
    assert node is not None
    # Edit bar is shown for an experiment run; combo starts at the auto class.
    assert not panel.metric_edit_bar.isHidden()
    assert panel.metric_edit_bar.class_combo.currentText() == "Good"

    # Force Open → class + C_eff recompute; access/E_pol cleared.
    panel._on_response_class_chosen("open")
    m = panel._edit_target()["run"].captures[0].metrics
    assert m.response_class == "open"
    assert np.isfinite(m.effective_capacitance_nf)
    assert len(m.access_voltage_per_phase_v) == 0
    assert bool(panel._dirty_paths)
    assert panel.metric_edit_bar.class_combo.currentText() == "Open"

    # Force back to Good → access/E_pol restored, C_eff cleared.
    panel._on_response_class_chosen("normal")
    m = panel._edit_target()["run"].captures[0].metrics
    assert m.response_class == "normal"
    assert not np.isfinite(m.effective_capacitance_nf)
    assert len(m.access_voltage_per_phase_v) >= 1

    # Save (auto-confirm) → reload → persisted.
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    panel._on_response_class_chosen("open")
    panel._on_save_metric_changes()
    s2 = load_session_npz(p_npz)
    assert s2.runs[0].captures[0].metrics.response_class == "open"


def test_hand_edit_value_round_trips(qapp, tmp_path, monkeypatch):
    from PyQt6 import QtWidgets, QtCore
    from stimtest.gui.viewer import ViewerPanel
    from stimtest.persistence import load_session_npz
    p_npz = tmp_path / "sess2.npz"
    _mk_session_npz(p_npz)
    panel = ViewerPanel()
    panel.load_session_file(Path(p_npz))
    _select_run(panel)
    panel._on_response_class_chosen("open")     # makes C_eff appear
    panel._on_metric_edit_toggled(True)         # unlock the Value column
    tbl = panel.metric_table
    edited = False
    for r in range(tbl.rowCount()):
        it = tbl.item(r, 1)
        if it and it.data(QtCore.Qt.ItemDataRole.UserRole) == "effective_capacitance_nf":
            it.setText("12.34 nF")
            edited = True
    assert edited, "no editable C_eff row found"
    monkeypatch.setattr(
        QtWidgets.QMessageBox, "question",
        staticmethod(lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes))
    panel._on_save_metric_changes()
    s2 = load_session_npz(p_npz)
    assert abs(s2.runs[0].captures[0].metrics.effective_capacitance_nf - 12.34) < 1e-6


def test_original_column_shows_pre_override_value(qapp, tmp_path):
    """When a capture is adjusted, the metric table gains a third 'Original'
    column showing the pre-adjustment value alongside the new one (operator:
    'keep the original values alongside the new one')."""
    from stimtest.gui.viewer import ViewerPanel
    p_npz = tmp_path / "sess_orig.npz"
    _mk_session_npz(p_npz)
    panel = ViewerPanel()
    panel.load_session_file(Path(p_npz))
    _select_run(panel)
    tbl = panel.metric_table
    # Before any adjustment: the historic 2-column table.
    assert tbl.columnCount() == 2
    # Override Good → Open: the table gains the Original column.
    panel._on_response_class_chosen("open")
    assert tbl.columnCount() == 3
    # The Response row now reads new="Open" with Original="Good".
    found = False
    for r in range(tbl.rowCount()):
        cell = tbl.item(r, 0)
        if cell is not None and cell.text() == "Response":
            assert tbl.item(r, 1).text() == "Open"
            assert tbl.item(r, 2).text() == "Good"
            found = True
    assert found, "Response row not found in metric table"
    # An UNCHANGED row (e.g. Q_ph) leaves its Original cell blank.
    for r in range(tbl.rowCount()):
        cell = tbl.item(r, 0)
        if cell is not None and cell.text().startswith("Q"):
            assert tbl.item(r, 2).text() == ""


def test_edit_bar_hidden_for_non_experiment_node(qapp, tmp_path):
    # A folder / session-root node isn't an editable capture/run → bar hidden.
    from stimtest.gui.viewer import ViewerPanel
    p_npz = tmp_path / "sess3.npz"
    _mk_session_npz(p_npz)
    panel = ViewerPanel()
    panel.load_session_file(Path(p_npz))
    # Select the session root (top-level) — not a capture/run.
    top = panel.tree.topLevelItem(0)
    panel.tree.setCurrentItem(top)
    assert panel._edit_target() is None
    assert panel.metric_edit_bar.isHidden()
