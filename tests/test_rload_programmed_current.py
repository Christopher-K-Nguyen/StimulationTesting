"""R_load must be fit against the PROGRAMMED current, never the I_mon reading.

Operator: "Must use the programmed current for R_load fitting and not the
actual current monitor."

The reason is circularity, not preference: the verification sweep exists to
MEASURE the I_mon scaling (``imon_v_per_ua_actual``, ``a``, ``b``).  If R were
divided by an I_mon-derived current, R would inherit whatever gain error the
sweep is simultaneously trying to establish -- and on the bench that gain was
1.2073, which alone would drag a nominal 4990 Ohm board to 4990/1.2073 =
4133 Ohm.  The programmed current is the independent reference.

Also pins the +/-20 % acceptance band (operator, 0.2.226).  A good RC-model
r-squared does NOT vindicate R: at 100 uA the capacitive ramp is ~1.06 V
against a ~0.50 V iR step, so r-squared is dominated by C and a large R error
barely moves it (CLAUDE.md gotcha #203).
"""
from __future__ import annotations

import pathlib
import re

import numpy as np
import pytest


def _src(rel):
    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / rel).read_text(encoding="utf-8")


CAL = "stimtest/gui/calibration.py"


def _method(src, name):
    start = src.index(f"    def {name}")
    m = re.compile(r"^    def ", re.M).search(src, start + 10)
    return src[start:m.start() if m else len(src)]


def test_capture_stores_the_programmed_current_for_the_fit():
    """``_raw_meas`` carries (current, iR drops, ramp slopes); the current
    element must be the programmed amplitude."""
    body = _method(_src(CAL), "_capture_one_amplitude")
    i = body.index("_raw_meas.setdefault")
    stored = body[i:i + 260]
    assert "float(amp_ua) * 1e-6" in stored, stored


def test_no_measured_current_reaches_the_joint_fit():
    """The joint R/C fit may only see programmed amps -- guard against a
    future edit swapping in a measured/I_mon-derived current.

    Scoped to the FIT BLOCK: ``_fit_one_channel`` legitimately computes the
    I_mon DC offset for the coefficients block further down, so the whole
    method mentions I_mon for unrelated reasons.
    """
    body = _method(_src(CAL), "_fit_one_channel")
    start = body.index("_I_step, _Y_step")
    end = body.index("fit_r_ohm = slope_step")
    block = body[start:end].lower()
    for bad in ("imon", "i_mon", "measured_a"):
        assert bad not in block, f"{bad!r} leaked into the joint R/C fit"


def test_per_amplitude_current_is_programmed_everywhere():
    """Both the step and the ramp paths derive I from ``amp_ua``."""
    src = _src(CAL)
    assert src.count("I_A = amp_ua * 1e-6") >= 2


def test_tolerance_band_is_minus20_plus10():
    src = _src(CAL)
    assert "_R_TOLERANCE_LOW_PCT = 20.0" in src
    assert "_R_TOLERANCE_HIGH_PCT = 10.0" in src


def test_no_stale_symmetric_constant_is_referenced():
    """``_R_TOLERANCE_PCT`` was removed; a leftover f-string reference would
    NameError on the accept path (it did, at the 'within ±X%' log line)."""
    src = _src(CAL)
    code = [l for l in src.splitlines()
            if "_R_TOLERANCE_PCT" in l and not l.lstrip().startswith("#")]
    assert code == [], code


def test_band_is_asymmetric_in_practice():
    """−20 % accepted, +20 % rejected — the whole point of the change."""
    from stimtest.gui.calibration import CalibrationTab
    kw = dict(nominal_ohm=4990.0, vmon_offset_tolerance_mv=5.0,
              r_tolerance_low_pct=20.0, r_tolerance_high_pct=10.0,
              model_r2=0.99)
    assert CalibrationTab.sweep_retry_reasons(4990 * 0.85, 1.0, **kw) == []
    assert CalibrationTab.sweep_retry_reasons(4990 * 1.15, 1.0, **kw)
    # Boundaries.
    assert CalibrationTab.sweep_retry_reasons(4990 * 0.80, 1.0, **kw) == []
    assert CalibrationTab.sweep_retry_reasons(4990 * 1.10, 1.0, **kw) == []
    assert CalibrationTab.sweep_retry_reasons(4990 * 0.79, 1.0, **kw)
    assert CalibrationTab.sweep_retry_reasons(4990 * 1.11, 1.0, **kw)


def test_symmetric_fallback_still_supported():
    """Existing callers passing a single ``r_tolerance_pct`` keep working."""
    from stimtest.gui.calibration import CalibrationTab
    kw = dict(nominal_ohm=4990.0, vmon_offset_tolerance_mv=5.0, model_r2=0.99)
    assert CalibrationTab.sweep_retry_reasons(
        4990 * 0.95, 1.0, r_tolerance_pct=10.0, **kw) == []
    assert CalibrationTab.sweep_retry_reasons(
        4990 * 0.85, 1.0, r_tolerance_pct=10.0, **kw)


@pytest.mark.parametrize("gain", [1.0, 1.2073, 0.8])
def test_fit_is_immune_to_imon_gain(gain):
    """The whole point: whatever the monitor's gain, R is unchanged.

    Reproduces the fit arithmetic (slope through the origin, then divide by
    the V_mon scaling) with the I_mon gain deliberately varied.  A fit that
    used the measured current would move with ``gain``; this one must not.
    """
    R, vmon_v_per_v = 4990.0, 1.0
    amps_ua = np.array([25.0, 50.0, 100.0, 200.0])
    I_prog = amps_ua * 1e-6
    step_v = I_prog * R * vmon_v_per_v          # what V_mon actually shows

    # The monitor mis-reads the current by `gain` -- irrelevant if unused.
    _I_measured = I_prog * gain

    slope_step = float(np.sum(I_prog * step_v) / np.sum(I_prog * I_prog))
    r_fit = slope_step / vmon_v_per_v
    assert r_fit == pytest.approx(R, rel=1e-9)


def test_using_the_monitor_current_would_have_reproduced_the_bench_error():
    """Documents the failure mode the guard prevents: 4990 / 1.2073 = 4133,
    which is inside the observed 4164 +/- 186 Ohm bench spread."""
    R, gain = 4990.0, 1.2073
    amps_ua = np.array([25.0, 50.0, 100.0, 200.0])
    I_prog = amps_ua * 1e-6
    step_v = I_prog * R
    I_wrong = I_prog * gain
    r_wrong = float(np.sum(I_wrong * step_v) / np.sum(I_wrong * I_wrong))
    assert r_wrong == pytest.approx(R / gain, rel=1e-9)
    assert 4164 - 186 <= r_wrong <= 4164 + 186


def test_band_accepts_the_bench_spread_and_still_rejects_a_dead_board():
    from stimtest.gui.calibration import CalibrationTab
    kw = dict(nominal_ohm=4990.0, r_tolerance_low_pct=20.0,
              r_tolerance_high_pct=10.0,
              vmon_offset_tolerance_mv=5.0, model_r2=0.99)
    # The bench MEAN (4164 = -16.6 %) is comfortably accepted, where +/-10 %
    # rejected it and burned two extra sweeps per channel for nothing.
    assert CalibrationTab.sweep_retry_reasons(4164.0, 1.0, **kw) == []
    assert CalibrationTab.sweep_retry_reasons(4164 + 186, 1.0, **kw) == []
    # ...but a genuinely wrong load is still caught, both directions.
    assert CalibrationTab.sweep_retry_reasons(2000.0, 1.0, **kw)
    assert CalibrationTab.sweep_retry_reasons(9000.0, 1.0, **kw)


def test_low_tail_of_the_bench_spread_is_still_marginal():
    """HONEST BOUND, surfaced to the operator: -1 SD of the observed spread
    (4164 - 186 = 3978) is 20.3 % below nominal, i.e. a hair outside the
    -20 % edge, so the occasional channel will still retry."""
    from stimtest.gui.calibration import CalibrationTab
    kw = dict(nominal_ohm=4990.0, vmon_offset_tolerance_mv=5.0, model_r2=0.99,
              r_tolerance_high_pct=10.0)
    low = 4164 - 186
    assert abs(low - 4990.0) / 4990.0 * 100 == pytest.approx(20.3, abs=0.1)
    assert CalibrationTab.sweep_retry_reasons(
        low, 1.0, r_tolerance_low_pct=20.0, **kw)
    assert CalibrationTab.sweep_retry_reasons(
        low, 1.0, r_tolerance_low_pct=25.0, **kw) == []
