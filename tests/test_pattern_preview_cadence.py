"""Pulse-pattern preview "Cadence" view — one-click framing of several
consecutive pulses so the pulse train / interpulse spacing is visible, and
the back-to-back continuous pulsing shows when there's no interpulse delay.

Operator: "continuous plotting … move along the x axis to find the next
pulses … expand the x axis scale to see how close pulses are … see the
pulsing with no interpulse delay".  The preview already tiles N_PULSES_DRAWN
periods; the Cadence button zooms the x-view out to a few of them.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import QtWidgets  # noqa: E402
_APP = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
_APP.setApplicationName("pulsar-pytest")

from stimtest.gui.pattern_preview import PatternPreview  # noqa: E402
from stimtest.waveforms import (Phase, PulsePattern,  # noqa: E402
                                SHAPE_RECTANGULAR)


def _no_gap_pattern():
    # period == total pulse → interpulse gap 0 (continuous pulsing).
    return PulsePattern(phases=[
        Phase(amplitude_ua=-50, width_us=100, shape=SHAPE_RECTANGULAR,
              delay_after_us=0.0),
        Phase(amplitude_ua=50, width_us=100, shape=SHAPE_RECTANGULAR,
              delay_after_us=0.0),
    ], rate_hz=5000.0)


def _x_view(pp):
    r0, r1 = pp.plot.getViewBox().viewRange()[0]
    return r0, r1


def test_cadence_button_exists():
    pp = PatternPreview()
    assert hasattr(pp, "cadence_btn")
    assert pp.cadence_btn.text() == "Cadence"
    pp.deleteLater()


def test_train_is_continuous_over_many_periods():
    # The preview draws a continuous train, not a single pulse.
    pp = PatternPreview()
    pat = _no_gap_pattern()
    pp.set_pattern(pat)
    per = 1e6 / pat.rate_hz
    d0, d1 = pp._data_x_range
    assert (d1 - d0) / per > 10          # ~21 tiled periods
    pp.deleteLater()


def test_cadence_view_frames_several_pulses():
    pp = PatternPreview()
    pat = PulsePattern.biphasic(amplitude_ua=50.0, phase_width_us=100.0,
                                rate_hz=100.0)
    pp.set_pattern(pat)
    per = 1e6 / pat.rate_hz
    # Default frames ≈ one pulse …
    dv = pp._default_x_range
    assert (dv[1] - dv[0]) / per < 1.0
    # … the cadence view expands to ~3 periods.
    pp.show_cadence_view(3.0)
    r0, r1 = _x_view(pp)
    assert 2.5 <= (r1 - r0) / per <= 3.5
    pp.deleteLater()


def test_cadence_view_shows_back_to_back_no_interpulse():
    pp = PatternPreview()
    pat = _no_gap_pattern()
    pp.set_pattern(pat)
    per = 1e6 / pat.rate_hz
    pp.show_cadence_view(3.0)
    r0, r1 = _x_view(pp)
    # Spans 3 whole periods → 3 back-to-back pulses visible.
    assert 2.5 <= (r1 - r0) / per <= 3.5
    # The rendered trace actually has samples across that window (continuous).
    t, _ = pp.curve.getData()
    inwin = ((t >= r0) & (t <= r1)).sum()
    assert inwin > 100
    pp.deleteLater()


def test_cadence_view_noop_before_pattern():
    pp = PatternPreview()
    # No pattern set yet → no cached period → no-op, no crash.
    pp.show_cadence_view(3.0)
    pp.deleteLater()
