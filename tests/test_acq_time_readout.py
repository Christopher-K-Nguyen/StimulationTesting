"""Approximate per-capture acquisition-time readout beside the pulse rate.

Operator: "I want an output next to the pulse rate to indicate what is the
approximate acquisition time based on the average count and the pulse rate —
right of the pulse rate unit drop down."

The readout lives on ``PatternControlPanel`` as ``acq_time_label`` (added to
the rate row immediately after ``rate_unit_combo``).  Its estimate is
``sweeps / rate`` where ``sweeps`` = the average count in AVERAGE mode and 1
in SAMPLE mode — the same formula the runner uses to size its capture timeout.
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
def test_label_exists_and_is_right_of_unit_combo(_app):
    p = _panel()
    assert isinstance(p.acq_time_label, QtWidgets.QLabel)
    # Same container widget as the unit combo (they share the rate row).
    assert p.acq_time_label.parent() is p.rate_unit_combo.parent()
    # A non-empty default readout (default 16 avg @ default rate).
    assert p.acq_time_label.text().startswith("≈")
    assert "/ capture" in p.acq_time_label.text()
    p.deleteLater()


# ------------------------------------------------------- AVERAGE-mode formula
def test_average_mode_uses_navg_over_rate(_app):
    p = _panel()
    p.rate_pps.setValue(100.0)               # 100 pps (default unit = pps)
    p.set_acquisition_info("AVERAGE", 64)    # 64 / 100 = 0.64 s
    assert p.acq_time_label.text() == "≈ 0.64 s / capture"
    p.deleteLater()


def test_sample_mode_is_single_sweep(_app):
    p = _panel()
    p.rate_pps.setValue(100.0)
    p.set_acquisition_info("SAMPLE", 64)     # 1 / 100 = 0.01 s → 10 ms
    assert p.acq_time_label.text() == "≈ 10 ms / capture"
    p.deleteLater()


# ----------------------------------------------- live update on a rate change
def test_label_updates_when_rate_changes(_app):
    p = _panel()
    p.set_acquisition_info("AVERAGE", 64)
    p.rate_pps.setValue(100.0)               # 0.64 s
    assert p.acq_time_label.text() == "≈ 0.64 s / capture"
    p.rate_pps.setValue(10.0)                # 64 / 10 = 6.4 s
    assert p.acq_time_label.text() == "≈ 6.40 s / capture"
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
