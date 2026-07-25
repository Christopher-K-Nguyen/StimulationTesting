"""Verification tab: Done closes the tab; 500 µA point removed.

Operator: "When pressing Done in the verification, close the verification tab"
+ "remove the 500 µA test" + (they lost an unsaved verification) so Done
prompts to save when a completed sweep hasn't been written to disk.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

import pytest

pytest.importorskip("PyQt6")
from PyQt6 import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _mw(_app):
    from stimtest.gui.main_window import MainWindow
    return MainWindow(simulate_default=True)


# ------------------------------------------------------------- 500 µA
def test_amplitude_grid_has_no_500():
    from stimtest.gui.calibration import CalibrationTab
    assert 500.0 not in CalibrationTab.DEFAULT_AMPLITUDE_GRID_UA
    assert CalibrationTab.DEFAULT_AMPLITUDE_GRID_UA[-1] == 200.0


def test_amplitude_grid_starts_above_the_trigger_branch():
    """Operator: "exclude 10 uA and do 25 uA instead of 20 uA".

    ``imon_trigger_level`` switches formula at 20 uA: at or BELOW it asks for
    ``(amp + 3.5) mV/uA``, which on a NIL-preset stimulator (1 mV/uA) is
    ABOVE the I_mon peak it is meant to trigger on -- 13.5 mV against a
    10 mV signal -- so the scope can never fire and the capture is a
    free-running frame (the "NUMACq = 0/1" poll timeouts).  Above 20 uA it
    uses ``amp x 0.25``, comfortably under the peak.
    """
    from stimtest.experiments.base import imon_trigger_level
    from stimtest.gui.calibration import CalibrationTab
    grid = CalibrationTab.DEFAULT_AMPLITUDE_GRID_UA
    assert grid == (25.0, 50.0, 100.0, 200.0)
    for amp in grid:
        level = abs(imon_trigger_level(
            amp_ua_signed=-amp,
            phase_width_us=CalibrationTab.PHASE_WIDTH_US,
            imon_v_per_ua=1e-3))
        peak = amp * 1e-3            # NIL preset: 1 mV per uA
        assert level < peak, (
            f"{amp} uA: trigger level {level*1e3:.2f} mV exceeds the "
            f"{peak*1e3:.2f} mV I_mon peak -- the scope cannot fire")


# ----------------------------- AVERAGE mode + DC coupling + wide window
def test_verification_uses_average_mode_64():
    """Operator: "instead of sample mode acquisition and taking the third
    sample, do average mode with average count of 64" -- supersedes the
    earlier 'do only sample mode acquisition for verification'."""
    from stimtest.gui.calibration import CalibrationTab
    assert CalibrationTab.CAL_ACQ_MODE.upper() == "AVERAGE"
    assert CalibrationTab.CAL_N_AVERAGES == 64


def _cal_src() -> str:
    import pathlib
    import stimtest.gui.calibration as c
    return pathlib.Path(c.__file__).read_text(encoding="utf-8")


def test_acquisition_count_is_mode_derived():
    """``_cal_n_acq`` derives the poll target from the MODE -- the full
    NUMAVg stack in AVERAGE, a single sweep in SAMPLE."""
    src = _cal_src()
    assert "def _cal_n_acq" in src
    assert "n_acq=n_acq," in src           # capture calls use the derived count
    assert "_cal_navg()" in src            # AVERAGE path still uses NUMAVg


def test_verification_forces_dc_coupling():
    """Operator: 'Vmon and Imon are strictly in DC-coupled mode.'"""
    src = _cal_src()
    assert 'set_channel_coupling(_cch, "DC")' in src


def test_verification_uses_wide_horizontal_window():
    """Operator: 'Instead of tight, do wide horizontal window' — supersedes
    the earlier 'make the horizontal window of verification tight'.

    WIDE is one grid increment above the tightest non-clipping step, so the
    125 us pulse frames in 300 us instead of 150 us: whole pulse plus real
    post-pulse recovery, and it is no longer pressed against the edge of the
    record."""
    src = _cal_src()
    assert 'set_horizontal_fit_mode("wide")' in src
    assert 'set_horizontal_fit_mode("tight")' not in src


# ------------------------------------------------- has_unsaved_results
def test_unsaved_results_flag(_app):
    from stimtest.gui.calibration import CalibrationTab
    cal = CalibrationTab(stim=None, scope=None)
    try:
        assert cal.has_unsaved_results() is False          # no sweep yet
        cal._fit = {1: object()}                            # pretend a sweep ran
        cal._saved_since_sweep = False
        assert cal.has_unsaved_results() is True
        cal._saved_since_sweep = True                       # after a save
        assert cal.has_unsaved_results() is False
    finally:
        cal.deleteLater()


# ------------------------------------------------- Done closes the tab
def test_done_closes_and_destroys_the_tab(_app):
    w = _mw(_app)
    try:
        cal = w._open_calibration_tab()
        assert w.tabs.indexOf(cal) >= 0                     # in the tab bar
        assert w.cal_tab is cal
        # No unsaved results → Done closes without a prompt.
        w._on_calibration_done()
        assert w.cal_tab is None                            # destroyed
        assert w.tabs.indexOf(cal) < 0                      # removed from bar
        # Re-opening creates a FRESH tab that's back in the bar.
        cal2 = w._open_calibration_tab()
        assert cal2 is not cal
        assert w.tabs.indexOf(cal2) >= 0
    finally:
        w.close()


def test_done_prompts_when_unsaved_then_discard_closes(_app, monkeypatch):
    w = _mw(_app)
    try:
        cal = w._open_calibration_tab()
        cal._fit = {1: object()}
        cal._saved_since_sweep = False
        assert cal.has_unsaved_results() is True
        # Discard → close proceeds without saving.
        monkeypatch.setattr(
            QtWidgets.QMessageBox, "question",
            staticmethod(lambda *a, **k:
                         QtWidgets.QMessageBox.StandardButton.Discard))
        w._on_calibration_done()
        assert w.cal_tab is None                            # closed
    finally:
        w.close()


def test_done_cancel_keeps_the_tab_open(_app, monkeypatch):
    w = _mw(_app)
    try:
        cal = w._open_calibration_tab()
        cal._fit = {1: object()}
        cal._saved_since_sweep = False
        monkeypatch.setattr(
            QtWidgets.QMessageBox, "question",
            staticmethod(lambda *a, **k:
                         QtWidgets.QMessageBox.StandardButton.Cancel))
        w._on_calibration_done()
        assert w.cal_tab is cal                             # still open
        assert w.tabs.indexOf(cal) >= 0
    finally:
        w.close()
