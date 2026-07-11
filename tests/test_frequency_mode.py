"""Continuous-mode pulse FREQUENCY knob (interpulse delay unchecked).

Operator: "When interpulse delay is unselected, allow the pulse rate to be
called pulse frequency (Hz), and the value can be changed" + (revised)
"don't disable width — it gives the user [the choice] to change width or
frequency/period".

So with the interpulse box UNCHECKED (continuous waveform):
  * the rate row is labelled "Pulse frequency" with a Hz suffix;
  * the knob stays EDITABLE (it used to be disabled + pinned);
  * editing the FREQUENCY rescales the phase WIDTHS to fill one period;
  * editing a WIDTH updates the displayed frequency (1e6 / total);
  * re-checking restores the "Pulse rate [pps]" label + the saved rate.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")

from PyQt6 import QtWidgets  # noqa: E402
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
_APP.setApplicationName("pulsar-pytest")


def _panel():
    from stimtest.gui.pattern_panel import PatternControlPanel
    return PatternControlPanel(title="Pulse pattern")


def test_uncheck_keeps_knob_editable_and_relabels_to_frequency():
    p = _panel()
    p.interpulse_check.setChecked(False)
    assert p.rate_pps.isEnabled() is True          # was disabled before
    assert "Hz" in p.rate_pps.suffix()
    assert "frequency" in p._rate_row_label.text().lower()
    p.interpulse_check.setChecked(True)
    assert "pps" in p.rate_pps.suffix()
    assert "rate" in p._rate_row_label.text().lower()
    p.deleteLater()


def test_frequency_edit_rescales_widths():
    p = _panel()
    # Symmetric biphasic, no interphase/discharge delays, width 250 µs each.
    p.interphase_check.setChecked(False)
    p.discharge_check.setChecked(False)
    p.width_shared.setValue(250.0)
    p.interpulse_check.setChecked(False)           # continuous
    pat = p.pattern()
    assert abs(pat.rate_hz - 2000.0) < 1.0         # pinned to 1e6/500
    # User types a HIGHER frequency → widths shrink to fill one period.
    p.rate_pps.setValue(4000.0)
    assert abs(float(p.width_shared.value()) - 125.0) < 0.5
    pat = p.pattern()
    assert abs(pat.rate_hz - 4000.0) < 1.0
    assert abs(sum(ph.width_us for ph in pat.phases) - 250.0) < 1.0
    p.deleteLater()


def test_width_edit_updates_frequency_display():
    p = _panel()
    p.interphase_check.setChecked(False)
    p.discharge_check.setChecked(False)
    p.width_shared.setValue(125.0)
    p.interpulse_check.setChecked(False)
    assert abs(p._current_rate_hz() - 4000.0) < 1.0
    p.width_shared.setValue(250.0)                 # widen → lower frequency
    assert abs(p._current_rate_hz() - 2000.0) < 1.0
    p.deleteLater()


def test_frequency_snaps_widths_to_1us_grid():
    """The pattern time resolution is 1 µs (operator) — a typed frequency
    whose ideal width is fractional snaps the width to the grid and the
    DISPLAYED frequency converges to the achievable value."""
    p = _panel()
    p.interphase_check.setChecked(False)
    p.discharge_check.setChecked(False)
    p.width_shared.setValue(250.0)
    p.interpulse_check.setChecked(False)
    p.rate_pps.setValue(3000.0)            # ideal width 166.67 µs (off-grid)
    w = float(p.width_shared.value())
    assert w == round(w)                    # landed on the 1 µs grid (167)
    assert abs(w - 167.0) < 0.5
    # Display shows the ACHIEVED frequency (1e6 / 334), not the typed one.
    assert abs(p._current_rate_hz() - 1e6 / 334.0) < 0.5


def test_frequency_snap_picks_nearest_frequency_not_nearest_width():
    """f = 1e6/total is NONLINEAR in the width, so near the midpoint the
    closer WIDTH is the farther FREQUENCY.  Typed 47664 Hz → ideal width
    10.490 µs: plain width-rounding gives 10 µs (50000 Hz, err 2336 Hz) but
    the NEAREST FREQUENCY is 11 µs (45454.5 Hz, err 2210 Hz) — the snap
    must pick 11 (operator: "snap to the nearest to satisfy the time
    resolution")."""
    p = _panel()
    p.interphase_check.setChecked(False)
    p.discharge_check.setChecked(False)
    p.width_shared.setValue(10.0)
    p.interpulse_check.setChecked(False)
    p.rate_pps.setValue(47664.0)
    assert abs(float(p.width_shared.value()) - 11.0) < 0.5
    assert abs(p._current_rate_hz() - 1e6 / 22.0) < 1.0


def test_recheck_restores_saved_rate():
    p = _panel()
    p.rate_pps.setValue(100.0)                     # user's pulsed rate
    p.interpulse_check.setChecked(False)           # → pinned to width max
    assert p._current_rate_hz() != 100.0
    p.interpulse_check.setChecked(True)
    assert abs(p._current_rate_hz() - 100.0) < 1e-6
    p.deleteLater()
