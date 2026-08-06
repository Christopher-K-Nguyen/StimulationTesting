"""Verification sweep: retry a channel when the V_mon DC offset is large.

Operator: "If the V_mon offset is more than +/-5 mV, then try again."

A large per-channel V_mon baseline means the kept frame was a stale /
still-settling acquisition (bench: run 1 = +928.573 mV vs run 2 = +1.859 mV
on the SAME board).  That same frame supplied the step / ramp slopes the
joint R/C fit consumes, so the offset is a frame-QUALITY proxy that catches
a bad sweep the R_load check alone can miss.  Both gates share ONE attempt
budget (``_R_RETRY_MAX``).
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


NOMINAL = 4990.0
KW = dict(nominal_ohm=NOMINAL, r_tolerance_pct=10.0,
          vmon_offset_tolerance_mv=5.0)


def _reasons(r_ohm, v_mv):
    from stimtest.gui.calibration import CalibrationTab
    return CalibrationTab.sweep_retry_reasons(r_ohm, v_mv, **KW)


# --------------------------------------------------------------- accept
def test_good_r_and_small_offset_accepts():
    """Both gates pass -> no reasons -> accept the sweep."""
    assert _reasons(NOMINAL, +1.859) == []
    assert _reasons(NOMINAL, -4.9) == []
    assert _reasons(5200.0, 0.0) == []          # +4.2%, inside 10%


# ------------------------------------------------- V_mon offset gate
def test_large_positive_offset_triggers_retry():
    """The bench case: +928.573 mV on an otherwise-nominal R."""
    out = _reasons(NOMINAL, +928.573)
    assert len(out) == 1
    assert "V_mon offset" in out[0]
    assert "+928.57" in out[0]


def test_large_negative_offset_triggers_retry():
    """The gate is on MAGNITUDE — negative offsets count too."""
    out = _reasons(NOMINAL, -50.0)
    assert len(out) == 1 and "V_mon offset" in out[0]


def test_offset_boundary_is_exclusive():
    """Exactly +/-5 mV is still acceptable; just past it is not."""
    assert _reasons(NOMINAL, 5.0) == []
    assert _reasons(NOMINAL, -5.0) == []
    assert _reasons(NOMINAL, 5.01) != []
    assert _reasons(NOMINAL, -5.01) != []


def test_nan_offset_is_not_a_failure():
    """No baseline recorded -> can't be judged -> must NOT burn a retry."""
    assert _reasons(NOMINAL, float("nan")) == []


# ------------------------------------------------------ R_load gate
def test_r_deviation_still_triggers_retry():
    """The pre-existing R gate is unchanged (bench: 3412 ohm = -31.6%)."""
    out = _reasons(3412.0, 0.0)
    assert len(out) == 1 and "R_load" in out[0]


def test_nan_r_is_a_failure():
    """A NaN *R* is the opposite of a NaN offset — the fit genuinely failed."""
    assert _reasons(float("nan"), 0.0) != []
    assert _reasons(0.0, 0.0) != []             # non-positive is unusable


# ------------------------------------------------ both gates compose
def test_both_gates_report_both_reasons():
    """One attempt budget, but the banner names every cause."""
    out = _reasons(3412.0, +928.573)
    assert len(out) == 2
    assert any("R_load" in r for r in out)
    assert any("V_mon offset" in r for r in out)


# ------------------------------------------------------- wiring
def test_tolerance_constant_is_five_mv():
    """The operator's +/-5 mV threshold is wired into the sweep."""
    import pathlib
    import stimtest.gui.calibration as c
    src = pathlib.Path(c.__file__).read_text(encoding="utf-8")
    assert "_VMON_OFFSET_TOLERANCE_MV = 5.0" in src
    # ...and the sweep routes its decision through the pure helper.
    assert "self.sweep_retry_reasons(" in src


def test_helper_is_callable_without_hardware(_app):
    """Pure + static: no scope, no stim, no widget state."""
    from stimtest.gui.calibration import CalibrationTab
    assert CalibrationTab.sweep_retry_reasons(NOMINAL, 0.0, **KW) == []


# ------------------------------------------------- r2 gate (3rd reason)
def _reasons_r2(r2):
    from stimtest.gui.calibration import CalibrationTab
    return CalibrationTab.sweep_retry_reasons(
        NOMINAL, 0.0, model_r2=r2, **KW)


def test_negative_r2_triggers_retry():
    """Operator: "redo the channel if the ending r2 is negative".

    The RC model is NOT fitted to the data, so r2 is unbounded below;
    negative means it describes the captures worse than a flat line.
    Bench: CH04 read -1.99 (alongside a -66 mV V_mon offset) while healthy
    channels read ~+0.99."""
    out = _reasons_r2(-1.9917)
    assert len(out) == 1 and "r²" in out[0]


def test_positive_r2_accepts():
    assert _reasons_r2(0.9985) == []
    assert _reasons_r2(0.0) == []          # zero is not negative


def test_nan_r2_is_not_a_failure():
    """Unjudgeable -- must not burn a retry (same rule as a NaN offset)."""
    assert _reasons_r2(float("nan")) == []


def test_r2_gate_composes_with_the_others():
    from stimtest.gui.calibration import CalibrationTab
    out = CalibrationTab.sweep_retry_reasons(
        3412.0, +928.573, model_r2=-1.99, **KW)
    assert len(out) == 3
    assert any("R_load" in r for r in out)
    assert any("V_mon offset" in r for r in out)
    assert any("r²" in r for r in out)


def test_r2_defaults_to_not_supplied():
    """Callers that pass no r2 keep the previous two-gate behaviour."""
    from stimtest.gui.calibration import CalibrationTab
    assert CalibrationTab.sweep_retry_reasons(NOMINAL, 0.0, **KW) == []
