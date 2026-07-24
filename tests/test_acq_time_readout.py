"""Approximate per-capture acquisition-time readout beside the pulse rate.

Operator: "I want an output next to the pulse rate to indicate what is the
approximate acquisition time based on the average count and the pulse rate —
right of the pulse rate unit drop down."

The readout lives on ``PatternControlPanel`` as ``acq_time_label`` on its own
"Average count" row just below the pulse-rate row, to the right of the inline
average-count editor (moved off the rate row so its verbose calculation stops
widening the panel).  Its estimate is ``sweeps / rate`` where ``sweeps`` = the
average count in AVERAGE mode and 1 in SAMPLE mode — the same formula the
runner uses to size its capture timeout.  In period (ms) mode the DISPLAYED
calculation flips to ``sweeps × period`` to match the selected unit.
The average count + mode come from the Setup tab, routed through
``_BaseExperimentTab.set_acquisition`` → ``pattern_panel.set_acquisition_info``.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# No prefs in a headless run → skip the MODAL first-launch admin dialog.
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _panel():
    from stimtest.gui.pattern_panel import PatternControlPanel
    return PatternControlPanel(title="Pulse pattern")


# ------------------------------------------------------------------- layout
def test_readout_sits_beside_the_rate_unit(_app):
    p = _panel()
    assert isinstance(p.acq_time_label, QtWidgets.QLabel)
    # The average COUNT is now set in Setup → Oscilloscope acquisition, so the
    # inline count editor + its "Average count" row were removed from Test
    # parameters — only the calculated-capture-time readout remains, back on
    # the pulse-rate row beside the rate-unit combo (operator: "only keep
    # '1 sweep ÷ 200 pps ≈ 5 ms / capture' by the pulse rate unit").
    assert p.acq_time_label.parent() is p.rate_unit_combo.parent()
    # A non-empty default readout (default 16 avg @ default rate),
    # showing the calculation (operator: "show the calculation").
    assert "≈" in p.acq_time_label.text()
    assert "÷" in p.acq_time_label.text()
    assert "/ capture" in p.acq_time_label.text()
    p.deleteLater()


def test_period_mode_calc_uses_multiplication(_app):
    """Operator: "If the pulse rate is set to pulse period, then have the
    calculation change accordingly."  In period (ms) mode the readout shows
    ``sweeps × period`` instead of ``sweeps ÷ rate`` — same result, mirrored
    to the unit the operator dialled in."""
    p = _panel()
    p.set_acquisition_info("AVERAGE", 64)
    p.rate_pps.setValue(100.0)                    # 100 pps
    p.rate_unit_combo.setCurrentText(p.UNIT_MS)   # → 10 ms period
    txt = p.acq_time_label.text()
    assert "×" in txt and "10 ms" in txt
    assert "÷" not in txt
    assert txt == "64 × 10 ms ≈ 0.64 s / capture"
    p.deleteLater()


# ------------------------------------------------------- AVERAGE-mode formula
def test_average_mode_shows_calc_navg_over_rate(_app):
    p = _panel()
    p.rate_pps.setValue(100.0)               # 100 pps (default unit = pps)
    p.set_acquisition_info("AVERAGE", 64)    # 64 / 100 = 0.64 s
    assert p.acq_time_label.text() == "64 ÷ 100 pps ≈ 0.64 s / capture"
    p.deleteLater()


def test_sample_mode_is_single_sweep(_app):
    p = _panel()
    p.rate_pps.setValue(100.0)
    p.set_acquisition_info("SAMPLE", 64)     # 1 / 100 = 0.01 s → 10 ms
    assert p.acq_time_label.text() == "1 sweep ÷ 100 pps ≈ 10 ms / capture"
    # SAMPLE mode ignores the average count → inline editor greyed out.
    assert p.acq_navg_inline.isEnabled() is False
    p.set_acquisition_info("AVERAGE", 64)
    assert p.acq_navg_inline.isEnabled() is True
    p.deleteLater()


# ----------------------------------------------- live update on a rate change
def test_label_updates_when_rate_changes(_app):
    p = _panel()
    p.set_acquisition_info("AVERAGE", 64)
    p.rate_pps.setValue(100.0)               # 0.64 s
    assert p.acq_time_label.text() == "64 ÷ 100 pps ≈ 0.64 s / capture"
    p.rate_pps.setValue(10.0)                # 64 / 10 = 6.4 s
    assert p.acq_time_label.text() == "64 ÷ 10 pps ≈ 6.40 s / capture"
    p.deleteLater()


# ------------------------------------------- inline average-count editor
def test_inline_navg_edit_updates_readout_and_emits(_app):
    """Editing the inline spin recomputes the estimate immediately and
    publishes the new count via ``acqNavgEdited`` (MainWindow forwards
    it into the Setup tab's spin)."""
    p = _panel()
    p.rate_pps.setValue(100.0)
    p.set_acquisition_info("AVERAGE", 64)
    got = []
    p.acqNavgEdited.connect(got.append)
    p.acq_navg_inline.setValue(128)          # type…
    p.acq_navg_inline.editingFinished.emit()  # …then commit (Enter/click-out)
    assert got == [128]
    assert p.acq_time_label.text() == "128 ÷ 100 pps ≈ 1.28 s / capture"
    p.deleteLater()


def test_set_acquisition_info_syncs_inline_spin_without_emitting(_app):
    """A Setup-tab push must sync the inline spin SILENTLY — re-emitting
    acqNavgEdited would bounce the value straight back at the Setup tab."""
    p = _panel()
    got = []
    p.acqNavgEdited.connect(got.append)
    p.set_acquisition_info("AVERAGE", 32)
    assert p.acq_navg_inline.value() == 32
    assert got == [], "sync from Setup must not re-emit acqNavgEdited"
    p.deleteLater()


# -------------------------------------------------- adaptive-unit formatting
@pytest.mark.parametrize("t_s, expected", [
    (2e-5, "20 µs"),
    (0.02, "20 ms"),
    (0.64, "0.64 s"),
    (6.4, "6.40 s"),
    (12.5, "12.5 s"),
    (64.0, "1 min 4 s"),
    (3600.0 + 47 * 60, "1 h 47 min"),
])
def test_fmt_acq_time(_app, t_s, expected):
    from stimtest.gui.pattern_panel import PatternControlPanel
    assert PatternControlPanel._fmt_acq_time(t_s) == expected


def test_fmt_acq_time_guards_bad_values(_app):
    from stimtest.gui.pattern_panel import PatternControlPanel
    assert PatternControlPanel._fmt_acq_time(0.0) == ""
    assert PatternControlPanel._fmt_acq_time(float("inf")) == ""
    assert PatternControlPanel._fmt_acq_time(float("nan")) == ""


def test_bad_navg_falls_back_to_default(_app):
    p = _panel()
    p.set_acquisition_info("AVERAGE", None)   # type: ignore[arg-type]
    assert p._acq_n_avg == 16
    p.set_acquisition_info("AVERAGE", 0)      # clamped up to >= 1
    assert p._acq_n_avg == 1
    p.deleteLater()


# ------------------------------------------- Setup → experiment-tab → panel
def test_set_acquisition_routes_into_pattern_panel(_app):
    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    QtWidgets.QApplication.processEvents()
    vt = w.vt_tab
    vt.set_acquisition("AVERAGE", 32)
    assert vt.pattern_panel._acq_n_avg == 32
    assert vt.pattern_panel._acq_mode == "AVERAGE"
    assert "/ capture" in vt.pattern_panel.acq_time_label.text()
    w.close()


def test_inline_navg_round_trips_through_setup_to_all_tabs(_app):
    """The full loop: editing VT's inline average-count spin pushes the
    value into the Setup tab's acq_navg_spin (source of truth), whose
    acquisitionChanged broadcast lands on EVERY tab's readout."""
    app = QtWidgets.QApplication.instance()
    if app is not None:
        app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    QtWidgets.QApplication.processEvents()
    # Pick a target guaranteed != the current value (delta-robust per
    # the prefs-sandbox rule — a fixed literal could no-op on re-runs).
    cur = int(w.setup_tab.acq_navg_spin.value())
    target = 128 if cur != 128 else 256
    w.vt_tab.pattern_panel.acq_navg_inline.setValue(target)
    w.vt_tab.pattern_panel.acq_navg_inline.editingFinished.emit()  # commit
    QtWidgets.QApplication.processEvents()
    assert int(w.setup_tab.acq_navg_spin.value()) == target
    # Broadcast reached a DIFFERENT tab's panel too.
    assert w.sp_tab.pattern_panel._acq_n_avg == target
    assert w.sp_tab.pattern_panel.acq_navg_inline.value() == target
    w.close()
