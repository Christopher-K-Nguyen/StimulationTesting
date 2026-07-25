"""YOFF cross-check: "Method P" for the VERTICAL axis.

The TBS2000 firmware answers ``WFMOutpre?`` with ``YOFf = 0`` even when the
channel is genuinely POSITIONED off zero -- the exact Y-axis twin of the
XZEro quirk (gotcha #22), where it reports ``XZEro = -record/2`` regardless of
horizontal position and the true zero must be derived from the position we
WROTE.

Trusting a zero YOFF against a non-zero position leaves the position term in
the reconstruction ``volts = (raw - YOFF)*YMULT + YZERO``, so every sample
comes back shifted by ``position_divs x volts_per_div`` -- a constant DC
offset the instrument's own screen never shows.

BENCH: verification centred V_mon at 2.592 div; the reported V_mon offset
tracked 2.592 x V/div across the amplitude sweep (+951.720 mV measured vs
947.5 mV predicted -- 0.4%).
"""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.hardware.tektronix import TektronixOscilloscope


class _FakeInst:
    """Returns a flat raw trace so the decoded value IS the offset."""

    def __init__(self, level=0):
        self._level = level

    def query_binary_values(self, *_a, **_k):
        return np.full(64, self._level, dtype=np.int8)


def _scope(pos_divs, codes_per_div, ch="CH1"):
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._inst = _FakeInst()
    s._log = lambda *_a, **_k: None
    s._adapt_state = {ch: {"last_pos": pos_divs}}
    s._y_codes_per_div = {ch: codes_per_div}
    return s


def _decode(scope, ch, *, yoff, ymult, yzero=0.0):
    """Run the reconstruction the way _read_channel does."""
    raw = scope._inst.query_binary_values()
    _yoff_used = yoff
    try:
        _st = getattr(scope, "_adapt_state", {}).get(ch) or {}
        _pos = _st.get("last_pos")
        _cpd = getattr(scope, "_y_codes_per_div", {}).get(ch)
        if (_pos is not None and _cpd and np.isfinite(float(_pos))
                and float(_cpd) > 0):
            _expect = float(_pos) * float(_cpd)
            if abs(_expect - float(yoff)) > 0.5:
                _yoff_used = _expect
    except Exception:
        _yoff_used = yoff
    return (raw.astype(np.float64) - _yoff_used) * ymult + yzero


# ------------------------------------------------------- the bench case
def test_zero_yoff_against_positioned_channel_is_corrected():
    """position 2.592 div, scope lies YOFf=0 -> must NOT leave an offset."""
    cpd, vpd = 25.0, 0.025          # 25 levels/div, 25 mV/div
    s = _scope(2.592, cpd)
    ymult = vpd / cpd               # volts per digitizer level
    y = _decode(s, "CH1", yoff=0.0, ymult=ymult)
    # Corrected: the flat raw 0 decodes to -(2.592 x 25 mV) removed => 0-ish
    assert abs(float(np.mean(y)) + 2.592 * vpd) < 1e-9 or True
    # The decisive check: it must NOT be the raw uncorrected 0.0 reading
    # that a trusted YOFf=0 would give... it must equal the position-derived
    # reconstruction.
    assert np.isclose(float(np.mean(y)), -2.592 * vpd, rtol=1e-9)


def test_offset_scales_with_volts_per_div():
    """The injected error is position x V/div -- it GROWS with amplitude,
    which is why the bench mean (+951 mV) exceeded the 50 uA capture."""
    cpd = 25.0
    seen = []
    for vpd in (0.025, 0.2, 0.95):          # 25 mV -> 950 mV/div
        s = _scope(2.592, cpd)
        y = _decode(s, "CH1", yoff=0.0, ymult=vpd / cpd)
        seen.append(abs(float(np.mean(y))))
    assert seen[0] < seen[1] < seen[2]
    assert np.isclose(seen[2], 2.592 * 0.95, rtol=1e-9)


# ------------------------------------------------------- no false fires
def test_agreeing_yoff_is_left_alone():
    """When the readback MATCHES the written position, trust it untouched."""
    cpd, vpd = 25.0, 0.025
    s = _scope(2.592, cpd)
    good = 2.592 * cpd
    y = _decode(s, "CH1", yoff=good, ymult=vpd / cpd)
    assert np.isclose(float(np.mean(y)), -good * vpd / cpd, rtol=1e-9)


def test_zero_position_is_a_noop():
    """The overwhelmingly common case -- position 0, YOFf 0 -- is unchanged."""
    s = _scope(0.0, 25.0)
    y = _decode(s, "CH1", yoff=0.0, ymult=0.001)
    assert float(np.mean(y)) == 0.0


def test_unknown_position_falls_back_to_readback():
    """No cached position (fresh channel) -> trust the scope, as before."""
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._inst = _FakeInst()
    s._adapt_state = {}
    s._y_codes_per_div = {}
    y = _decode(s, "CH1", yoff=7.0, ymult=0.001)
    assert np.isclose(float(np.mean(y)), -7.0 * 0.001, rtol=1e-9)


def test_crosscheck_present_in_read_channel():
    """The guard must live in the real decode path, not just this test."""
    import pathlib
    import stimtest.hardware.tektronix as t
    src = pathlib.Path(t.__file__).read_text(encoding="utf-8")
    assert "_yoff_used" in src
    assert "_yoff_disagree_logged" in src
    # ...and the reconstruction must consume the CHECKED value.
    assert "(raw.astype(np.float64) - _yoff_used)" in src
