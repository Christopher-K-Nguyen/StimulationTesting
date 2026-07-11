"""Every Test-parameters input + channel/combo (de)selection logs to the pane.

Operator: "Have all inputs and selections in test parameters show up in the
log pane.  For setup and test parameters tab, I do include device
configuration and channel/combo selection … I also include de-selection."

Mechanism (mirrors SetupTab.settingChanged):

* ``_BaseExperimentTab.paramChanged(str)`` carries a ready-to-log
  ``"<TAG>: <field> = <value>"`` line; MainWindow connects it to
  ``_log_setup_change`` AFTER prefs restore, so construction / restore
  bursts never log.
* ``_wire_param_log`` dispatches per widget type — spin boxes log on
  COMMIT ONLY (``editingFinished`` = Enter or click-out; operator: "do
  not automatically print out the number, wait for the user to press
  enter or click out"), with a per-widget last-logged-value baseline so
  a reformat-only ``editingFinished`` (thousands regrouping) never logs
  a second line; combos / checkboxes log immediately; radios log on
  check only.
* Widgets are declared in ``PARAM_LOG_WIDGETS`` per tab (+ the shared
  ``_BASE_PARAM_LOG_WIDGETS`` camera/smoothing and the bias panel).
* Channel/combo SELECTION and DE-SELECTION log via a differ on
  ``combinationsChanged`` (``_log_combo_selection``) — the old
  ``_on_selection_changed`` slot was dead code (never connected).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# The prefs sandbox below (app name "pulsar-pytest") starts with NO prefs
# file, which would trip MainWindow's MODAL first-launch admin-setup dialog
# and HANG the headless run.  Skip it (test-only escape hatch).
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture(scope="module")
def _win(_app):
    # PREFS SANDBOX: MainWindow saves prefs on close via Qt's
    # AppConfigLocation, which keys on the APPLICATION NAME — a pytest
    # process is "python", so tests would READ+WRITE the operator's
    # console-dev prefs file (AppData/Local/python/...), making a
    # setValue(x) a NO-OP when a prior test run persisted x (the
    # valueChanged never fires → the log test flakes) AND polluting a
    # real prefs file.  A test-only app name isolates both directions.
    _app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    lines: list[str] = []
    orig = w.log_pane.log
    w.log_pane.log = lambda m: (lines.append(m), orig(m))[1]
    w._log_setup_changes = True          # launch state: gate open
    QTest.qWait(400)                      # flush startup singleShots
    lines.clear()
    yield w, lines
    w.close()


def _grab(lines, fn, wait=750, want=None, timeout_ms=6000):
    """Trigger ``fn`` and collect log lines.

    With ``want`` (a substring), POLLS until a matching line arrives or
    ``timeout_ms`` elapses — required for the DEBOUNCED spinbox logs
    (600 ms QTimer): a fixed wait is flaky under full-suite load, where
    the event loop can delay the timer past any fixed margin.
    Without ``want``, waits a fixed ``wait`` ms (immediate signals).
    """
    lines.clear()
    fn()
    if want is None:
        QTest.qWait(wait)
        return list(lines)
    waited = 0
    while waited < timeout_ms:
        QTest.qWait(120)
        waited += 120
        if any(want in L for L in lines):
            break
    return list(lines)


# ------------------------------------------------------ startup silence
def test_startup_produces_no_param_lines(_win):
    w, lines = _win
    QTest.qWait(300)
    assert not [L for L in lines if ": channel/combo" in L or "bias " in L], lines


# ------------------------------------------------------ per-widget types
def _toggle_value(spin, a, b):
    """A target that ALWAYS differs from the current value (so
    ``valueChanged`` always fires, even against persisted prefs)."""
    return b if abs(spin.value() - a) < 1e-9 else a


def _user_commit(spin, value):
    """Simulate a USER edit + commit on a param-logged spinbox.

    ``setValue`` alone models a PROGRAMMATIC change: the wire-time
    ``_sync_baseline`` hook sees the widget unfocused and moves the
    last-logged baseline WITH the value (so programmatic changes never
    log — by design).  A real user edit happens while the spinbox HAS
    focus (baseline pinned at the pre-edit value), then Enter /
    click-out fires ``editingFinished``.  Headless, focus on a widget
    buried in an unshown MainWindow page doesn't stick, so we model the
    focused-edit by restoring the pre-edit baseline before emitting the
    commit signal — the exact state a focused edit leaves behind.
    """
    before = getattr(spin, "_last_param_logged_value", None)
    spin.setValue(value)                       # baseline follows (unfocused)
    spin._last_param_logged_value = before     # …as if edited while focused
    spin.editingFinished.emit()                # the user commit


def test_spinbox_logs_on_commit_not_on_setvalue(_win):
    """Operator: numeric inputs log when the user presses Enter or
    clicks out — NOT on every value change."""
    w, lines = _win
    spin = w.vt_tab.max_ua
    tgt = _toggle_value(spin, 432.0, 431.0)
    # 1. A bare value change (no commit) logs NOTHING.
    got = _grab(lines, lambda: spin.setValue(tgt), wait=300)
    assert not any("VT: maximum current" in L for L in got), got
    # 2. The commit (Enter / click-out) logs exactly one line.
    tgt2 = _toggle_value(spin, 432.0, 431.0)
    got = _grab(lines, lambda: _user_commit(spin, tgt2),
                want="VT: maximum current")
    assert sum(1 for L in got if "VT: maximum current" in L) == 1, got
    assert any(f"{tgt2:.1f}" in L for L in got if "maximum current" in L), got


def test_reformat_editingfinished_does_not_relog(_win):
    """Qt re-fires ``editingFinished`` when the spinbox merely re-renders
    its text (thousands regrouping on focus-out).  The value is unchanged
    → no second line (operator: "Do not print again just to separate 000
    with spaces")."""
    w, lines = _win
    spin = w.vt_tab.max_ua
    tgt = _toggle_value(spin, 432.0, 431.0)
    _grab(lines, lambda: _user_commit(spin, tgt), want="VT: maximum current")
    # The reformat-only repeat: editingFinished with the SAME value.
    got = _grab(lines, lambda: spin.editingFinished.emit(), wait=300)
    assert not any("VT: maximum current" in L for L in got), got


def test_combo_change_logs_immediately(_win):
    w, lines = _win
    tgt = (w.vt_tab.strategy_combo.currentIndex() + 1) % w.vt_tab.strategy_combo.count()
    got = _grab(lines, lambda: w.vt_tab.strategy_combo.setCurrentIndex(tgt), wait=100)
    assert any("VT: ramp strategy = " in L for L in got), got


def test_checkbox_toggle_logs_on_off(_win):
    w, lines = _win
    chk = w.ps_tab.stop_on_compliance
    got = _grab(lines, lambda: chk.setChecked(not chk.isChecked()), wait=100)
    assert any("PS: stop on voltage compliance = " in L for L in got), got


def test_camera_and_bias_inputs_log(_win):
    w, lines = _win
    chk = w.sp_tab.cam_record_chk
    want_state = "OFF" if chk.isChecked() else "ON"     # a real toggle
    got = _grab(lines, lambda: chk.setChecked(not chk.isChecked()), wait=100)
    assert any(f"SP: camera record video = {want_state}" in L
               for L in got), got
    spin = w.vt_tab._bias_feedback_panel.setpoint_spin
    tgt = _toggle_value(spin, 0.31, 0.32)
    got = _grab(lines, lambda: _user_commit(spin, tgt),
                want="VT: bias setpoint")
    assert any("VT: bias setpoint" in L for L in got), got


def test_each_tab_logs_with_its_own_tag(_win):
    w, lines = _win
    got = _grab(lines,
                lambda: _user_commit(w.sp_tab.duration,
                                     _toggle_value(w.sp_tab.duration,
                                                   77.0, 78.0)),
                want="SP: duration")
    assert any(L.startswith("Setup: SP: duration = ") for L in got), got
    got = _grab(lines,
                lambda: _user_commit(w.lp_tab.snap_int,
                                     _toggle_value(w.lp_tab.snap_int,
                                                   33.0, 34.0)),
                want="LP: snapshot interval")
    assert any(L.startswith("Setup: LP: snapshot interval = ") for L in got), got
    got = _grab(lines,
                lambda: _user_commit(w.ps_tab.step_ua,
                                     _toggle_value(w.ps_tab.step_ua,
                                                   11.0, 12.0)),
                want="PS: current step")
    assert any(L.startswith("Setup: PS: current step = ") for L in got), got


# ----------------------------------------- channel/combo select + DE-select
def test_channel_selection_and_deselection_log(_win):
    w, lines = _win
    tab = w.vt_tab
    # Start from a KNOWN-empty selection (restored prefs may have selected
    # channels — set_actives([5]) would then be a no-op diff).
    tab.combo_panel.set_actives([])
    QTest.qWait(100)
    got = _grab(lines, lambda: tab.combo_panel.set_actives([5]), wait=100)
    assert any("channel/combo selected CH05" in L for L in got), got
    got = _grab(lines, lambda: tab.combo_panel.set_actives([5, 6]), wait=100)
    assert any("selected CH06" in L for L in got), got
    got = _grab(lines, lambda: tab.combo_panel.set_actives([6]), wait=100)
    assert any("deselected CH05" in L for L in got), got
    got = _grab(lines, lambda: tab.combo_panel.set_actives([]), wait=100)
    assert any("deselected CH06" in L and "none selected" in L for L in got), got


def test_dead_slot_replaced_by_live_differ(_win):
    """The old _on_selection_changed was never connected; the differ IS."""
    w, lines = _win
    tab = w.vt_tab
    # combinationsChanged must reach _log_combo_selection (live wire).
    receivers_ok = True
    try:
        tab.combo_panel.combinationsChanged.disconnect(tab._log_combo_selection)
        tab.combo_panel.combinationsChanged.connect(tab._log_combo_selection)
    except TypeError:
        receivers_ok = False
    assert receivers_ok, "_log_combo_selection is not connected to combinationsChanged"
