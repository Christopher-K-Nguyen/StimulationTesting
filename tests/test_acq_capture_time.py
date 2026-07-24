"""Acquisition input-mode dropdown — "Average count" vs "Capture time".

Operator: "Add a dropdown list if average acquisition is selected, where there
is average count or capture time.  If average count is selected, then the input
next to it is number of waveforms.  If capture time is selected, then it sets
the capture time (average count / pulse rate), where average count depends on
capture time and pulse rate, and the increment/decrement is 0.1 s" +
"Round average count to the ceiling when capture time is selected" +
"average acquisition average count must be at least 2".

Lives on ``PatternControlPanel`` (Test Parameters) because the pulse RATE is
live there — capture_time = count / rate, so in time mode the count is DERIVED
(count = ceil(time × rate), clamped to [2, scope-max]).
"""
from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _panel():
    from stimtest.gui.pattern_panel import PatternControlPanel
    return PatternControlPanel(title="Pulse pattern")


def _to_time_mode(p):
    p.acq_input_mode_combo.setCurrentIndex(
        p.acq_input_mode_combo.findData("time"))


# ------------------------------------------------------------------ default
def test_dropdown_present_and_defaults_to_count(_app):
    p = _panel()
    try:
        assert p.acq_input_mode_combo.count() == 2
        assert p.acq_input_mode_combo.currentData() == "count"
        # Count spin shown, time spin explicitly hidden (isHidden is the
        # reliable flag on a never-shown widget — gotcha #70c).
        assert not p.acq_navg_inline.isHidden()
        assert p.acq_time_spin.isHidden()
        # Step is 0.1 s, suffix in seconds.
        assert abs(p.acq_time_spin.singleStep() - 0.1) < 1e-9
        assert p.acq_time_spin.suffix().strip() == "s"
        assert p.acq_time_spin.decimals() == 1
    finally:
        p.deleteLater()


def test_switch_to_time_mode_swaps_the_input(_app):
    p = _panel()
    try:
        _to_time_mode(p)
        assert p.acq_input_mode_combo.currentData() == "time"
        assert p.acq_navg_inline.isHidden()
        assert not p.acq_time_spin.isHidden()
    finally:
        p.deleteLater()


# ----------------------------------------------------------------- ceiling
def test_time_mode_ceils_the_count(_app):
    p = _panel()
    try:
        p.set_acquisition_info("AVERAGE", 16)
        p.rate_pps.setValue(25.0)
        assert abs(p._current_rate_hz() - 25.0) < 1e-6      # rate not clamped
        _to_time_mode(p)
        p.acq_time_spin.setValue(0.1)                        # 0.1 × 25 = 2.5
        p._on_acq_time_changed()
        assert p._acq_n_avg == 3                             # ceil, NOT round→2
        p.acq_time_spin.setValue(0.3)                        # 0.3 × 25 = 7.5
        p._on_acq_time_changed()
        assert p._acq_n_avg == 8                             # ceil(7.5)
        p.acq_time_spin.setValue(0.4)                        # 0.4 × 25 = 10.0
        p._on_acq_time_changed()
        assert p._acq_n_avg == 10           # exact integer, no float ceil-up
    finally:
        p.deleteLater()


def test_derived_count_floored_at_two(_app):
    """AVERAGE count must be >= 2 (count 1 = a single SAMPLE)."""
    p = _panel()
    try:
        p.set_acquisition_info("AVERAGE", 16)
        p.rate_pps.setValue(5.0)
        assert abs(p._current_rate_hz() - 5.0) < 1e-6
        _to_time_mode(p)
        p.acq_time_spin.setValue(0.1)                        # 0.1 × 5 = 0.5
        p._on_acq_time_changed()
        assert p._acq_n_avg == 2            # ceil(0.5)=1 → floored to 2
    finally:
        p.deleteLater()


# ------------------------------------------------------------------- emit
def test_time_mode_publishes_derived_count(_app):
    p = _panel()
    try:
        p.set_acquisition_info("AVERAGE", 16)
        p.rate_pps.setValue(50.0)
        got = []
        p.acqNavgEdited.connect(lambda n: got.append(int(n)))
        _to_time_mode(p)
        p.acq_time_spin.setValue(2.0)                        # 2.0 × 50 = 100
        p._on_acq_time_changed()
        assert p._acq_n_avg == 100
        assert got and got[-1] == 100
        # The hidden count spin mirrors the derived value.
        assert int(p.acq_navg_inline.value()) == 100
    finally:
        p.deleteLater()


def test_rate_change_recomputes_count_in_time_mode(_app):
    """Operator: 'average count depends on capture time and pulse rate.'"""
    p = _panel()
    try:
        p.set_acquisition_info("AVERAGE", 16)
        p.rate_pps.setValue(100.0)
        _to_time_mode(p)
        p.acq_time_spin.setValue(1.0)                        # 1.0 × 100 = 100
        p._on_acq_time_changed()
        assert p._acq_n_avg == 100
        p.rate_pps.setValue(200.0)             # fires _emit → recompute
        assert abs(p._current_rate_hz() - 200.0) < 1e-6
        assert p._acq_n_avg == 200             # 1.0 s × 200 pps
    finally:
        p.deleteLater()


def test_count_clamped_to_scope_max(_app):
    p = _panel()
    try:
        p.set_acquisition_info("AVERAGE", 16)
        p.rate_pps.setValue(1000.0)
        _to_time_mode(p)
        p.acq_time_spin.setValue(100.0)        # 100 × 1000 = 100000 → clamp
        p._on_acq_time_changed()
        assert p._acq_n_avg == int(p.acq_navg_inline.maximum())
    finally:
        p.deleteLater()


# ------------------------------------------------------------------- SAMPLE
def test_sample_mode_disables_the_input_mode_controls(_app):
    p = _panel()
    try:
        p.set_acquisition_info("SAMPLE", 16)
        assert not p.acq_input_mode_combo.isEnabled()
        assert not p.acq_time_spin.isEnabled()
        assert not p.acq_navg_inline.isEnabled()
        p.set_acquisition_info("AVERAGE", 16)
        assert p.acq_input_mode_combo.isEnabled()
    finally:
        p.deleteLater()


# ------------------------------------------------------------------- prefs
def test_prefs_round_trip_time_mode(_app):
    p = _panel()
    try:
        p.set_acquisition_info("AVERAGE", 16)
        p.rate_pps.setValue(100.0)
        _to_time_mode(p)
        p.acq_time_spin.setValue(1.5)
        p._on_acq_time_changed()
        prefs = p.current_prefs()
        assert prefs["acq_input_mode"] == "time"
        assert abs(prefs["acq_capture_time_s"] - 1.5) < 1e-6

        q = _panel()
        try:
            q.rate_pps.setValue(100.0)
            q.restore_prefs(prefs)
            assert q.acq_input_mode_combo.currentData() == "time"
            assert abs(q.acq_time_spin.value() - 1.5) < 1e-6
            assert not q.acq_time_spin.isHidden()
            assert q.acq_navg_inline.isHidden()
            # Count derived from the restored time × rate (no float ceil-up).
            assert q._acq_n_avg == 150
        finally:
            q.deleteLater()
    finally:
        p.deleteLater()


def test_legacy_prefs_without_keys_load_as_count_mode(_app):
    p = _panel()
    try:
        p.restore_prefs({"amp_excite": 50.0})   # no acq_* keys
        assert p.acq_input_mode_combo.currentData() == "count"
        assert p.acq_time_spin.isHidden()
    finally:
        p.deleteLater()
