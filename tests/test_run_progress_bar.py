"""Experiment-tab run-progress bar — MATLAB ``updateWaitbar.m`` port.

Shows elapsed time + pulse count (pulses = elapsed × stim-rate) over a
fractional bar while a run is live (operator: "I want a progress bar in
the experiment tab that periodically update and display time elapsed and
number of pulses, like my MATLAB code").

* Bounded pulsing (SP / LP) → fractional bar + "e / D s · (p / P pulses)".
* Unbounded (Continuous Pulsing) → busy bar (range 0,0) + running counts.
* Voltage Transient (``duration_s=None`` + ``total_steps`` channels) →
  CHANNEL-STEP bar: fills by channels/combos completed (the adaptive
  amplitude ramp has no fixed duration), label shows the current channel +
  ticking elapsed (operator: "progress bar … correlates with the channel
  … testing"; per-amplitude granularity is a Verification-process concern).
* PS (``duration_s=None`` + no channel list) → bar stays hidden.
"""
from __future__ import annotations

import sys
import time

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _sp_tab(qapp):
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    return w, w._exp_tab_by_code["SP"][0]


def test_bounded_run_shows_fractional_bar_and_counts(qapp):
    w, tab = _sp_tab(qapp)
    tab.begin_run_progress(duration_s=60.0, rate_hz=50.0)
    # ``isHidden`` reflects the explicit setVisible state independent of
    # whether the (never-shown) top window is on screen.
    assert not tab._run_progress_widget.isHidden()
    assert tab._run_progress_timer.isActive()
    assert (tab.run_progress_bar.minimum(),
            tab.run_progress_bar.maximum()) == (0, 100)

    # Pin elapsed to 12.3 s and re-tick deterministically.
    tab._run_progress_t0 = time.monotonic() - 12.3
    tab._tick_run_progress()
    assert tab.run_progress_bar.value() == 21          # 12.3/60 ≈ 20.5 %
    txt = tab.run_progress_label.text()
    assert "12.3 / 60 s" in txt
    assert "615" in txt and "3,000" in txt             # pulses, comma-grouped
    tab._end_run_progress()


def test_unbounded_run_uses_busy_bar_without_total(qapp):
    w, tab = _sp_tab(qapp)
    tab.begin_run_progress(duration_s=float("inf"), rate_hz=100.0)
    # Range (0, 0) is Qt's busy/marquee bar.
    assert (tab.run_progress_bar.minimum(),
            tab.run_progress_bar.maximum()) == (0, 0)
    tab._run_progress_t0 = time.monotonic() - 7.0
    tab._tick_run_progress()
    txt = tab.run_progress_label.text()
    assert "elapsed 7.0 s" in txt
    assert "700 pulses" in txt
    assert "/" not in txt                              # no total to divide by
    tab._end_run_progress()


def test_no_duration_keeps_bar_hidden(qapp):
    # VT / PS: no duration policy → bar must NOT show.
    w, tab = _sp_tab(qapp)
    tab.begin_run_progress(duration_s=None, rate_hz=50.0)
    assert tab._run_progress_widget.isHidden()
    assert not tab._run_progress_timer.isActive()


def test_zero_or_bad_rate_keeps_bar_hidden(qapp):
    w, tab = _sp_tab(qapp)
    tab.begin_run_progress(duration_s=60.0, rate_hz=0.0)
    assert tab._run_progress_widget.isHidden()
    tab.begin_run_progress(duration_s=60.0, rate_hz=None)
    assert tab._run_progress_widget.isHidden()


def test_end_stops_timer_and_hides(qapp):
    w, tab = _sp_tab(qapp)
    tab.begin_run_progress(duration_s=60.0, rate_hz=50.0)
    assert tab._run_progress_timer.isActive()
    tab._end_run_progress()
    assert not tab._run_progress_timer.isActive()
    assert tab._run_progress_widget.isHidden()
    assert tab._run_progress_t0 is None


# ---------------------------------------------------------------------------
# VT channel-step bar
# ---------------------------------------------------------------------------
def _vt_tab(qapp):
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    return w, w._exp_tab_by_code["VT"][0]


def test_vt_channel_step_bar_tracks_channels(qapp):
    """VT: bar shows over channels/combos, filling by channels COMPLETED
    (the current channel is in progress)."""
    from stimtest.experiments.base import ProgressInfo
    w, tab = _vt_tab(qapp)
    tab.begin_run_progress(duration_s=None, rate_hz=100.0, total_steps=16)
    assert not tab._run_progress_widget.isHidden()
    assert tab._run_progress_step_mode is True
    assert (tab.run_progress_bar.minimum(),
            tab.run_progress_bar.maximum()) == (0, 100)
    assert tab.run_progress_bar.value() == 0            # no channel done yet

    # Channel 7 of 16 starts → 6 completed → 6/16 = 37.5 % → 38.
    tab._on_runner_progress(
        ProgressInfo(step=7, total=16, label="VT CH07", started_at=0.0))
    assert tab.run_progress_bar.value() == 38
    lbl = tab.run_progress_label.text()
    assert "channel 7 / 16" in lbl and "VT CH07" in lbl
    assert "elapsed" in lbl                             # timer readout present

    # Last channel → 15/16 = 93.75 % → 94.
    tab._on_runner_progress(
        ProgressInfo(step=16, total=16, label="VT CH16", started_at=0.0))
    assert tab.run_progress_bar.value() == 94

    tab._end_run_progress()
    assert tab._run_progress_step_mode is False
    assert tab._run_progress_widget.isHidden()


def test_vt_zero_channels_keeps_bar_hidden(qapp):
    # No channel list (e.g. PS) → total_steps 0 → no step bar.
    w, tab = _vt_tab(qapp)
    tab.begin_run_progress(duration_s=None, rate_hz=100.0, total_steps=0)
    assert tab._run_progress_widget.isHidden()
    assert tab._run_progress_step_mode is False
