"""Count vs capture-time input mode in Setup → Oscilloscope acquisition.

Operator: "a dropdown list of average count and capture time on the right of
the acquisition mode dropdown list.  On the right of that is the input for
average count or capture time" + "hide the new dropdown list and spinners if
average acquisition is not selected" + "Round average count to the ceiling
when capture time is selected" + "average acquisition average count must be
at least 2".  In Test Parameters the pattern panel keeps its calculated-
capture-time readout (unchanged).

The conversion lives on the Setup tab; capture_time = count / rate needs the
pulse rate, which MainWindow feeds from the active experiment's pattern via
``set_pulse_rate_hz`` (Setup has no rate of its own).
"""
from __future__ import annotations

import math
import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")
pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _tab():
    from stimtest.gui.setup_tab import SetupTab
    return SetupTab()


def _to_time(tab):
    tab.acq_input_mode_combo.setCurrentIndex(
        tab.acq_input_mode_combo.findData("time"))


# ---------------------------------------------------------------- layout
def test_defaults_to_count_mode(_app):
    tab = _tab()
    assert tab.acq_input_mode_combo.count() == 2
    assert tab.acq_input_mode_combo.currentData() == "count"
    # AVERAGE (default) → dropdown + count shown, time spin hidden.
    assert not tab.acq_input_mode_combo.isHidden()
    assert not tab._acq_navg_field.isHidden()
    assert tab.acq_time_spin.isHidden()
    assert abs(tab.acq_time_spin.singleStep() - 0.1) < 1e-9
    assert tab.acq_time_spin.decimals() == 1
    assert tab.acq_time_spin.suffix().strip() == "s"


def test_hidden_when_not_average(_app):
    """Operator: 'hide the new dropdown list and spinners if average
    acquisition is not selected.'"""
    tab = _tab()
    tab.set_acq_mode("SAMPLE")
    assert tab.acq_input_mode_combo.isHidden()
    assert tab.acq_time_spin.isHidden()
    assert tab._acq_navg_field.isHidden()
    tab.set_acq_mode("AVERAGE")
    assert not tab.acq_input_mode_combo.isHidden()
    assert not tab._acq_navg_field.isHidden()


def test_time_spin_shown_in_time_mode(_app):
    tab = _tab()
    _to_time(tab)
    assert tab._acq_navg_field.isHidden()
    assert not tab.acq_time_spin.isHidden()


# ------------------------------------------------ mode display rename
def test_mode_labels_are_friendly_but_data_is_scpi(_app):
    """Operator: 'SAMPLE to Sample, AVERAGE to Average, HIRES to Hi-Res.'
    The combo shows friendly text; item DATA + ``_current_acq_mode`` stay SCPI
    so the driver / runner are unaffected."""
    tab = _tab()
    texts = [tab.acq_mode_combo.itemText(i)
             for i in range(tab.acq_mode_combo.count())]
    datas = [tab.acq_mode_combo.itemData(i)
             for i in range(tab.acq_mode_combo.count())]
    assert texts == ["Sample", "Average"]
    assert datas == ["SAMPLE", "AVERAGE"]
    assert tab._current_acq_mode() == "AVERAGE"      # default
    assert tab.current_acquisition()[0] == "AVERAGE"  # SCPI out
    tab.set_acq_mode("SAMPLE")
    assert tab.acq_mode_combo.currentText() == "Sample"
    assert tab._current_acq_mode() == "SAMPLE"


def test_hires_renders_as_friendly_label(_app):
    """A modern scope reporting HIRES shows 'Hi-Res' but keeps SCPI data."""
    class _Info:
        n_channels = 4
        has_ext_trigger = True

    class _Scope:
        info = _Info()
        def acquisition_modes(self):
            return ["SAMPLE", "AVERAGE", "HIRES"]
        def average_count_choices(self):
            return [2, 4, 8, 16, 32, 64, 128, 256, 512]
        def max_average_count(self):
            return 512

    tab = _tab()
    tab.apply_scope_capabilities(_Scope())
    texts = [tab.acq_mode_combo.itemText(i)
             for i in range(tab.acq_mode_combo.count())]
    assert texts == ["Sample", "Average", "Hi-Res"]
    assert tab.set_acq_mode("HIRES")
    assert tab.acq_mode_combo.currentText() == "Hi-Res"
    assert tab._current_acq_mode() == "HIRES"
    # Hi-Res is single-acquisition → the count/capture-time controls hide.
    assert tab.acq_input_mode_combo.isHidden()
    assert tab.acq_time_spin.isHidden()


# ---------------------------------------------------------------- ceiling
def test_time_mode_ceils_the_count(_app):
    tab = _tab()
    tab.set_pulse_rate_hz(25.0)
    _to_time(tab)
    tab.acq_time_spin.setValue(0.1)                  # 0.1 × 25 = 2.5
    tab._on_capture_time_changed()
    assert tab._current_n_avg() == 3                 # ceil, NOT round→2
    tab.acq_time_spin.setValue(0.4)                  # 0.4 × 25 = 10.0
    tab._on_capture_time_changed()
    assert tab._current_n_avg() == 10                # exact, no float ceil-up


def test_floored_at_two(_app):
    """AVERAGE count must be >= 2 (count 1 = a single SAMPLE)."""
    tab = _tab()
    tab.set_pulse_rate_hz(5.0)
    _to_time(tab)
    tab.acq_time_spin.setValue(0.1)                  # 0.1 × 5 = 0.5
    tab._on_capture_time_changed()
    assert tab._current_n_avg() == 2                 # ceil(0.5)=1 → floor 2


def test_time_mode_emits_derived_count(_app):
    tab = _tab()
    tab.set_pulse_rate_hz(50.0)
    got = []
    tab.acquisitionChanged.connect(lambda m, n: got.append((m, int(n))))
    _to_time(tab)
    tab.acq_time_spin.setValue(2.0)                  # 2.0 × 50 = 100
    tab._on_capture_time_changed()
    assert tab._current_n_avg() == 100
    assert got and got[-1] == ("AVERAGE", 100)


def test_rate_change_recomputes(_app):
    tab = _tab()
    tab.set_pulse_rate_hz(100.0)
    _to_time(tab)
    tab.acq_time_spin.setValue(1.0)                  # 1.0 × 100 = 100
    tab._on_capture_time_changed()
    assert tab._current_n_avg() == 100
    tab.set_pulse_rate_hz(200.0)                     # count follows rate
    assert tab._current_n_avg() == 200


def test_current_acquisition_reports_derived(_app):
    tab = _tab()
    tab.set_pulse_rate_hz(100.0)
    _to_time(tab)
    tab.acq_time_spin.setValue(1.5)
    tab._on_capture_time_changed()
    mode, n = tab.current_acquisition()
    assert mode == "AVERAGE" and n == 150


def test_no_rate_no_derivation(_app):
    """Without a fed rate the conversion is a safe no-op (keeps the count)."""
    tab = _tab()
    _to_time(tab)                                    # no set_pulse_rate_hz
    before = tab._current_n_avg()
    tab.acq_time_spin.setValue(2.0)
    tab._on_capture_time_changed()
    assert tab._current_n_avg() == before


# ---------------------------------------------------------------- prefs
def test_prefs_round_trip(_app):
    tab = _tab()
    tab.set_pulse_rate_hz(100.0)
    _to_time(tab)
    tab.acq_time_spin.setValue(1.5)
    tab._on_capture_time_changed()
    prefs = tab.current_prefs()
    assert prefs["acq_input_mode"] == "time"
    assert abs(prefs["acq_capture_time_s"] - 1.5) < 1e-6

    tab2 = _tab()
    tab2.restore_prefs(prefs)
    assert tab2.acq_input_mode_combo.currentData() == "time"
    assert abs(tab2.acq_time_spin.value() - 1.5) < 1e-6
    assert not tab2.acq_time_spin.isHidden()
    # Count derives from the restored time on the first rate feed.
    tab2.set_pulse_rate_hz(100.0)
    assert tab2._current_n_avg() == 150


def test_legacy_prefs_load_as_count(_app):
    tab = _tab()
    tab.restore_prefs({"acq_mode": "AVERAGE", "acq_n_avg": 32})
    assert tab.acq_input_mode_combo.currentData() == "count"
    assert tab.acq_time_spin.isHidden()


# ------------------------------------------------ MainWindow rate feed
def test_mainwindow_feeds_active_pattern_rate_to_setup(_app):
    _app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    try:
        st = w.setup_tab
        code = w._current_exp_code
        pp = w._exp_tab_by_code[code][0].pattern_panel
        pp.rate_pps.setValue(100.0)          # fires patternChanged → feed
        assert abs(st._pulse_rate_hz - pp.effective_rate_hz()) < 1e-6
        # And the Setup capture-time conversion uses that fed rate.
        st.acq_input_mode_combo.setCurrentIndex(
            st.acq_input_mode_combo.findData("time"))
        st.acq_time_spin.setValue(1.0)
        st._on_capture_time_changed()
        assert st._current_n_avg() == int(math.ceil(pp.effective_rate_hz()))
    finally:
        w.close()
