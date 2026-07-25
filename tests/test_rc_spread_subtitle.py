"""Plot subtitle carries mean +/- SD of R_load and C_load.

Operator: "In the plot subtitle, include the mean +/- SD resistance and
capacitance."

These are the PER-CAPTURE estimates accumulated across the amplitude sweep.
The spread is the useful part: a tight SD says the load is behaving linearly
and the fit is trustworthy; a wide SD flags amplitude-dependent behaviour
(clipping at the big steps, noise domination at the small ones) that a single
fitted number hides.
"""
from __future__ import annotations

import math
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


def _fmt(vals, unit="Ω", decimals=0):
    from stimtest.gui.calibration import CalibrationTab
    return CalibrationTab.format_mean_sd(vals, unit, decimals=decimals)


# --------------------------------------------------------- format_mean_sd
def test_mean_and_sample_sd():
    # mean 4990; sample SD (ddof=1) of [4890, 4990, 5090] is 100.
    out = _fmt([4890.0, 4990.0, 5090.0])
    assert "4990" in out and "100" in out and "Ω" in out and "n=3" in out
    assert "±" in out


def test_single_sample_omits_sd():
    """An SD from one point is meaningless -- not zero."""
    out = _fmt([4990.0])
    assert "n=1" in out and "±" not in out


def test_non_finite_entries_are_dropped():
    out = _fmt([4990.0, float("nan"), 4990.0, float("inf")])
    assert "n=2" in out


def test_all_non_finite_returns_empty():
    assert _fmt([float("nan"), float("inf")]) == ""
    assert _fmt([]) == ""
    assert _fmt(None) == ""


def test_garbage_entries_are_skipped():
    out = _fmt([4990.0, "x", None, 4990.0])
    assert "n=2" in out


def test_decimals_respected():
    assert "4.99" in _fmt([4.99], unit="kΩ", decimals=2)


# ------------------------------------------------------- subtitle wiring
def _tab(_app):
    from stimtest.gui.calibration import CalibrationTab
    return CalibrationTab(stim=None, scope=None)


def _row(cap_pf, r_ohm):
    """A results row with cap/R at the documented indices."""
    from stimtest.gui.calibration import CalibrationTab
    row = [0.0] * 12
    row[CalibrationTab._RESULT_IDX_CAP_PF] = cap_pf
    row[CalibrationTab._RESULT_IDX_R_OHM] = r_ohm
    return tuple(row)


def test_subtitle_reports_both_quantities(_app):
    tab = _tab(_app)
    try:
        tab._results[1] = [_row(4700.0, 4890.0), _row(4800.0, 5090.0)]
        sub = tab._rc_spread_subtitle(1)
        assert "<i>R</i><sub>load</sub>" in sub
        assert "<i>C</i><sub>load</sub>" in sub
        assert "Ω" in sub and "pF" in sub
        assert "4990" in sub and "4750" in sub      # the two means
        assert "n=2" in sub
    finally:
        tab.deleteLater()


def test_subtitle_empty_without_captures(_app):
    tab = _tab(_app)
    try:
        assert tab._rc_spread_subtitle(1) == ""     # no rows yet
        assert tab._rc_spread_subtitle(None) == ""
    finally:
        tab.deleteLater()


def test_subtitle_survives_short_rows(_app):
    """A malformed/short row must not raise into the render path."""
    tab = _tab(_app)
    try:
        tab._results[1] = [(0.0, 1.0), _row(4700.0, 4990.0)]
        sub = tab._rc_spread_subtitle(1)
        assert "n=1" in sub                          # only the good row
    finally:
        tab.deleteLater()


def test_indices_match_the_append_layout():
    """Guard the tuple layout the subtitle depends on."""
    import pathlib
    import stimtest.gui.calibration as c
    from stimtest.gui.calibration import CalibrationTab
    src = pathlib.Path(c.__file__).read_text(encoding="utf-8")
    i = src.find("self._results[ch].append(")
    block = src[i:i + 420]
    # est_cap_pf then est_r_ohm, in that order, at indices 4 and 5.
    assert block.find("est_cap_pf") < block.find("est_r_ohm")
    assert CalibrationTab._RESULT_IDX_CAP_PF == 4
    assert CalibrationTab._RESULT_IDX_R_OHM == 5
