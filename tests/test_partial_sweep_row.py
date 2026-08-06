"""A channel still being swept must not display a one-point "fit".

Bench: with the results row refreshed after EVERY amplitude, CH02 showed
``R_load = 1164 ohm (-76.7%)`` in red off its FIRST 25 uA capture -- while its
r2 was 0.998, i.e. the nominal-4990-ohm model matched the data almost
perfectly and the load was fine all along.

The joint R/C fit needs >= 2 distinct amplitudes.  Below that it cannot run,
and the per-capture median fallback (meant for a fit that legitimately FAILED
on a complete sweep) was presenting a single capture as a result.
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

from stimtest.config import VMON_SCALING_DEFAULT as K  # noqa: E402

R_TRUE, C_TRUE = 4990.0, 4700e-12


@pytest.fixture(scope="module")
def _app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _row(amp_ua, r_ohm=R_TRUE, c_pf=C_TRUE * 1e12):
    """One results row with a DELIBERATELY off per-capture R, to prove the
    in-progress path does not surface it."""
    I = amp_ua * 1e-6
    row = [0.0] * 12
    row[0] = amp_ua
    row[4] = c_pf
    row[5] = r_ohm
    row[6] = I * R_TRUE * K              # step_v
    row[7] = -I * K / C_TRUE             # ramp slope
    row[8], row[9] = -1.0, 1e-8
    return tuple(row)


def _fit(_app, rows):
    from stimtest.gui.calibration import CalibrationTab
    tab = CalibrationTab(stim=None, scope=None)
    try:
        tab._results[1] = rows
        return tab._fit_one_channel(1)
    finally:
        tab.deleteLater()


def test_single_amplitude_reports_no_fit(_app):
    """One capture -> the fit cannot run -> R and C must be blank (NaN),
    NOT the single capture's own estimate."""
    fit = _fit(_app, [_row(25.0, r_ohm=1164.0)])   # the bench's bad value
    assert fit is None or not math.isfinite(fit.get("fit_r_ohm", float("nan")))
    if fit is not None:
        # ...and the DISPLAYED value must not be the 1164 one-point estimate.
        shown = fit.get("median_r_ohm", float("nan"))
        assert not math.isfinite(shown) or abs(shown - 1164.0) > 1.0


def test_two_amplitudes_produce_a_real_fit(_app):
    """As soon as a second amplitude lands, the joint fit runs."""
    fit = _fit(_app, [_row(25.0), _row(50.0)])
    assert fit is not None
    assert math.isfinite(fit["fit_r_ohm"])
    assert abs(fit["fit_r_ohm"] - R_TRUE) / R_TRUE < 0.05


def test_complete_sweep_recovers_truth(_app):
    fit = _fit(_app, [_row(a) for a in (25.0, 50.0, 100.0, 200.0)])
    assert abs(fit["fit_r_ohm"] - R_TRUE) / R_TRUE < 0.02
    assert abs(fit["fit_c_pf"] - C_TRUE * 1e12) / (C_TRUE * 1e12) < 0.02


def test_repeated_amplitude_is_not_a_fit(_app):
    """Two captures at the SAME current give no slope information."""
    fit = _fit(_app, [_row(25.0), _row(25.0)])
    assert fit is None or not math.isfinite(fit.get("fit_r_ohm", float("nan")))


def test_fit_ran_flag_gates_the_median_fallback():
    """The fallback must be reachable only once a fit was attempted."""
    import pathlib
    import stimtest.gui.calibration as c
    src = pathlib.Path(c.__file__).read_text(encoding="utf-8")
    assert "_fit_ran = bool(" in src
    i = src.find("median_r_ohm = fit_r_ohm")
    assert i != -1
    assert "if not _fit_ran" in src[i:i + 400]
