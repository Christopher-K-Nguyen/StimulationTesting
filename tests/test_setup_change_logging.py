"""Every Setup input + selection is INDICATED in the log pane.

Operator: "I noticed that changing the return electrode, there was not new
text in the log pane.  Make sure that all input and selection are indicated
in the log pane."

The electrode-config sub-inputs (return / reference electrode, geometry,
connector, remember-potential toggle) used to fold into the generic
``arrayChanged`` metadata line — whose description shows only device / active
coating / area — so changing the RETURN electrode logged an ``array = …`` line
with UNCHANGED text (or, for the remember toggle, nothing at all).  They now
carry their own ``settingChanged`` string, and the generic ``array = …`` line
is de-duped so a return tweak doesn't ALSO spam an identical array line.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The prefs sandbox (app name "pulsar-pytest") has NO prefs file, which
# would trip MainWindow's MODAL first-launch admin-setup dialog and hang a
# headless run.  Skip it (test-only escape hatch).
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _mk():
    # PREFS SANDBOX (see test_param_change_logging.py): a pytest process is
    # named "python", so MainWindow would read/WRITE the operator's
    # console-dev prefs (AppData/Local/python/…).  A test-only app name
    # keeps test churn out of real prefs and real prefs out of the tests.
    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    QtWidgets.QApplication.processEvents()   # flush startup-queued signals
    lines: list[str] = []
    orig = w.log_pane.log
    w.log_pane.log = lambda m: (lines.append(m), orig(m))[1]
    w._log_setup_changes = True
    return w, lines


def _other_item(combo, forbid=("ustom",)):
    cur = combo.currentText()
    for i in range(combo.count()):
        t = combo.itemText(i)
        if t and t != cur and not any(f in t.lower() for f in forbid):
            return t
    return None


def test_return_coating_change_is_logged(_app):
    w, lines = _mk()
    st = w.setup_tab
    tgt = _other_item(st.return_coating)
    assert tgt is not None
    lines.clear()
    st.return_coating.setCurrentText(tgt)
    QtWidgets.QApplication.processEvents()
    joined = " || ".join(lines)
    assert "return electrode" in joined.lower(), lines
    assert tgt in joined, lines
    # no redundant generic "array = …" line for a return-only change
    assert not any(L.startswith("Setup: array =") for L in lines), lines
    w.close()


def test_return_enable_toggle_is_logged(_app):
    w, lines = _mk()
    st = w.setup_tab
    lines.clear()
    st.return_enable.setChecked(not st.return_enable.isChecked())
    QtWidgets.QApplication.processEvents()
    assert any("return electrode" in L.lower() for L in lines), lines
    w.close()


def test_remember_toggle_is_logged(_app):
    w, lines = _mk()
    st = w.setup_tab
    lines.clear()
    st.remember_potential_chk.setChecked(not st.remember_potential_chk.isChecked())
    QtWidgets.QApplication.processEvents()
    assert any("remember return potential" in L.lower() for L in lines), lines
    w.close()


def test_reference_enable_toggle_is_logged(_app):
    w, lines = _mk()
    st = w.setup_tab
    lines.clear()
    st.reference_enable.setChecked(not st.reference_enable.isChecked())
    QtWidgets.QApplication.processEvents()
    assert any("reference electrode" in L.lower() for L in lines), lines
    w.close()


def test_geometry_and_connector_changes_are_logged(_app):
    w, lines = _mk()
    st = w.setup_tab
    gt = _other_item(st.geometry_combo)
    if gt is not None:
        lines.clear()
        st.geometry_combo.setCurrentText(gt)
        QtWidgets.QApplication.processEvents()
        assert any("geometry" in L.lower() for L in lines), lines
    ct = _other_item(st.connector_combo)
    if ct is not None:
        lines.clear()
        st.connector_combo.setCurrentText(ct)
        QtWidgets.QApplication.processEvents()
        assert any("connector" in L.lower() for L in lines), lines
    w.close()


def test_device_change_still_logs_the_array_line(_app):
    """A change that DOES alter the device/coating/area description still
    logs the generic ``array = …`` line (the de-dup only suppresses an
    UNCHANGED description)."""
    w, lines = _mk()
    st = w.setup_tab
    dt = _other_item(st.device_combo, forbid=())
    assert dt is not None
    lines.clear()
    st.device_combo.setCurrentText(dt)
    QtWidgets.QApplication.processEvents()
    assert any(L.startswith("Setup: array =") for L in lines), lines
    w.close()
