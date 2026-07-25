"""The channel fit consumes EVERY individual measurement.

Operator: "I want the fitting to use all values."

Each capture yields SEVERAL independent measurements of the same load -- one
iR drop per current edge (four on a biphasic with an interphase gap) and one
ramp slope per phase.  The fit used to collapse those to a single average per
amplitude before fitting, which threw away most of the data and made the
residual a measure of amplitude-to-amplitude scatter rather than of
measurement scatter.

The raw values live in a side map (``_raw_meas``) rather than a wider results
tuple, so none of the existing unpack plumbing had to move.
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

R_TRUE, C_TRUE = 4990.0, 4700e-12
# CalibrationTab(stim=None) falls back to VMON_SCALING_DEFAULT.
from stimtest.config import VMON_SCALING_DEFAULT as K  # noqa: E402

AMPS = [10.0, 20.0, 50.0, 100.0, 200.0]


@pytest.fixture(scope="module")
def _app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _build(drop_scale=(1.0, 1.0, 1.0, 1.0)):
    """Ideal series-RC measurements: rows (per-amplitude averages) + raw."""
    rows, raw = [], []
    for a in AMPS:
        I = a * 1e-6
        step = I * R_TRUE * K
        slope = -I * K / C_TRUE
        row = [0.0] * 12
        row[0], row[4], row[5] = a, C_TRUE * 1e12, R_TRUE
        row[6], row[7], row[8], row[9] = step, slope, -1.0, 1e-8
        rows.append(tuple(row))
        # 4 iR drops (one per edge) + 2 phase slopes, phase 2 opposite sign.
        raw.append((I, [step * f for f in drop_scale], [slope, -slope]))
    return rows, raw


def _fit(_app, rows, raw):
    from stimtest.gui.calibration import CalibrationTab
    tab = CalibrationTab(stim=None, scope=None)
    try:
        tab._results[1] = rows
        if raw is None:
            tab._raw_meas.pop(1, None)
        else:
            tab._raw_meas[1] = raw
        return tab._fit_one_channel(1)
    finally:
        tab.deleteLater()


def test_recovers_truth_from_all_values(_app):
    rows, raw = _build()
    fit = _fit(_app, rows, raw)
    assert abs(fit["fit_r_ohm"] - R_TRUE) / R_TRUE < 0.01
    assert abs(fit["fit_c_pf"] - C_TRUE * 1e12) / (C_TRUE * 1e12) < 0.01


def test_matches_the_averaged_path_on_clean_data(_app):
    """Using all values must not CHANGE the estimator -- only feed it more
    points.  On scatter-free input both paths must agree."""
    rows, raw = _build()
    with_raw = _fit(_app, rows, raw)
    without = _fit(_app, rows, None)          # fallback: per-amplitude means
    assert abs(with_raw["fit_r_ohm"] - without["fit_r_ohm"]) < 1e-6
    assert abs(with_raw["fit_c_pf"] - without["fit_c_pf"]) < 1e-6


def test_scatter_is_averaged_down_not_inherited(_app):
    """+/-2% scatter across the four drops must not move R by ~2%."""
    rows, raw = _build(drop_scale=(1.00, 0.98, 1.01, 0.99))
    fit = _fit(_app, rows, raw)
    assert abs(fit["fit_r_ohm"] - R_TRUE) / R_TRUE < 0.01


def test_phase2_slope_sign_is_normalised(_app):
    """Phase 2 ramps the OPPOSITE way; pooling raw signed slopes would
    partially cancel and inflate C."""
    rows, raw = _build()
    fit = _fit(_app, rows, raw)
    # Both slopes present and opposite in the fixture...
    assert raw[0][2][0] * raw[0][2][1] < 0
    # ...yet C still recovers, so they were not cancelled.
    assert abs(fit["fit_c_pf"] - C_TRUE * 1e12) / (C_TRUE * 1e12) < 0.01


def test_raw_map_is_cleared_with_results():
    """A retry must not pool a discarded attempt with the kept one."""
    import pathlib
    import stimtest.gui.calibration as c
    src = pathlib.Path(c.__file__).read_text(encoding="utf-8")
    # Every _results reset is paired with a _raw_meas reset.
    assert src.count("self._raw_meas.clear()") >= 2
    assert "self._raw_meas[ch] = []" in src


def test_fallback_when_raw_unavailable(_app):
    """A re-loaded session has no raw values -- the fit must still work."""
    rows, _ = _build()
    fit = _fit(_app, rows, None)
    assert abs(fit["fit_r_ohm"] - R_TRUE) / R_TRUE < 0.01
