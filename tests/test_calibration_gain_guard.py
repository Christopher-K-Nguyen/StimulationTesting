"""A degenerate verification gain must never corrupt current data (CWRU).

Bench diagnosis: a Stimulator Verification run on BAD captures (railing V_mon
/ a mis-triggered I_mon on a 2-channel scope) fit a DEGENERATE
``np.polyfit`` slope of ``a ≈ 1e14``.  That garbage gain was saved to
calibration.json and then applied by ``ChannelCoeffs.apply_imon`` to every
subsequent capture — turning I_mon into ±1e14 µA (±1e8 A) nonsense
("the current is not being read correctly").

Three guards now make a bad calibration harmless:
  * ``ChannelCoeffs.is_plausible`` — the bound check.
  * ``apply_imon`` ignores an implausible coeff (identity fallback).
  * ``load_calibration`` DROPS an implausible saved coeff (protects an
    already-corrupted calibration.json, e.g. CWRU's current one).
The verification tab additionally saves identity + flags the channel when
the fit is implausible (tested at the unit level here via the shared bounds).
"""
from __future__ import annotations

import json

import numpy as np

from stimtest.readback_calibration import (
    ChannelCoeffs, ReadbackCalibration, load_calibration,
    _IMON_GAIN_MIN, _IMON_GAIN_MAX, _IMON_OFFSET_MAX_UA,
)


# ---------------------------------------------------------------------------
# is_plausible
# ---------------------------------------------------------------------------
def test_identity_is_plausible():
    assert ChannelCoeffs(1.0, 0.0).is_plausible()


def test_small_correction_is_plausible():
    assert ChannelCoeffs(1.02, -0.4).is_plausible()
    assert ChannelCoeffs(0.9, 12.0).is_plausible()


def test_cwru_garbage_gain_is_not_plausible():
    # The actual value seen at CWRU.
    assert not ChannelCoeffs(1.6e14, -1.6).is_plausible()
    assert not ChannelCoeffs(7.4e13, -79.6).is_plausible()


def test_nan_and_inf_not_plausible():
    assert not ChannelCoeffs(float("nan"), 0.0).is_plausible()
    assert not ChannelCoeffs(float("inf"), 0.0).is_plausible()
    assert not ChannelCoeffs(1.0, float("nan")).is_plausible()


def test_bounds_edges():
    assert ChannelCoeffs(_IMON_GAIN_MAX, 0.0).is_plausible()
    assert not ChannelCoeffs(_IMON_GAIN_MAX * 1.1, 0.0).is_plausible()
    assert ChannelCoeffs(_IMON_GAIN_MIN, 0.0).is_plausible()
    assert not ChannelCoeffs(_IMON_GAIN_MIN * 0.5, 0.0).is_plausible()
    assert not ChannelCoeffs(1.0, _IMON_OFFSET_MAX_UA * 2).is_plausible()


# ---------------------------------------------------------------------------
# apply_imon ignores garbage
# ---------------------------------------------------------------------------
def test_apply_imon_ignores_garbage_gain():
    cal = ReadbackCalibration(channels={1: ChannelCoeffs(a=1.6e14, b=-1.6)})
    raw = np.array([-3.0, 0.0, 3.2, -8.0])   # µA, physically sensible
    out = cal.apply_imon(raw, 1)
    # Garbage coeff ignored → input returned unchanged (NOT ×1e14).
    assert np.allclose(out, raw)
    assert float(np.max(np.abs(out))) < 100.0


def test_apply_imon_still_applies_a_real_correction():
    cal = ReadbackCalibration(channels={1: ChannelCoeffs(a=1.05, b=-0.5)})
    raw = np.array([0.0, 10.0, -10.0])
    out = cal.apply_imon(raw, 1)
    assert np.allclose(out, 1.05 * raw - 0.5)


def test_apply_imon_identity_channel_unchanged():
    cal = ReadbackCalibration(channels={})     # no entry → identity
    raw = np.array([1.0, -2.0, 3.0])
    assert np.allclose(cal.apply_imon(raw, 5), raw)


# ---------------------------------------------------------------------------
# load_calibration drops a corrupted saved coeff
# ---------------------------------------------------------------------------
def _write_cal(tmp_path, coeffs):
    p = tmp_path / "calibration.json"
    p.write_text(json.dumps({
        "schema_version": 2,
        "coefficients": coeffs,
        "stimulator": {"serial_number": "PLX00178"},
    }), encoding="utf-8")
    return p


def test_load_drops_garbage_channel_keeps_good(tmp_path):
    p = _write_cal(tmp_path, {
        "1": {"a": 1.6e14, "b": -1.6},   # garbage (CWRU)
        "2": {"a": 1.03, "b": -0.4},     # good
    })
    cal = load_calibration("PLX00178", path=p)
    assert cal is not None
    # CH1 garbage dropped → coeffs() returns identity; CH2 kept.
    assert cal.coeffs(1).a == 1.0 and cal.coeffs(1).b == 0.0
    assert cal.coeffs(2).a == 1.03

    # And applying the loaded cal to CH1 leaves I_mon untouched.
    raw = np.array([-3.0, 3.2])
    assert np.allclose(cal.apply_imon(raw, 1), raw)


def test_load_all_garbage_yields_identity_everywhere(tmp_path):
    p = _write_cal(tmp_path, {"1": {"a": 7.4e13, "b": -79.6}})
    cal = load_calibration("PLX00178", path=p)
    raw = np.array([-8.0, 8.0])
    # The exact ±10^14 corruption the CWRU npz showed can no longer happen.
    out = cal.apply_imon(raw, 1)
    assert float(np.max(np.abs(out))) < 100.0
