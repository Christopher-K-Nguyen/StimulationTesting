"""``make_capture(apply_current_offset=...)`` — the calibration I_mon
(current-monitor) DC-offset subtraction.

The GUI "Current offset" TOGGLE was REMOVED (operator: "Remove that I_mon
offset toggle at the end of the test parameters") — but ``make_capture``
keeps the ``apply_current_offset`` parameter as a dormant, default-OFF
capability (never set True by any runner now).  Default OFF = no subtraction
(gotcha #78, byte-identical to the raw int8→double conversion); ON re-subtracts
``cal.imon_offset_v``.  Only the I_mon offset is affected; the V_mon offset
stays off.
"""
from __future__ import annotations

import numpy as np

from stimtest.readback_calibration import ReadbackCalibration, make_capture


class _Info:
    vmon_scaling_v_per_v = 0.25
    imon_scaling_v_per_ua = 1.0e-3


class _Stim:
    info = _Info()


class _Acq:
    def __init__(self, imon):
        self.channels = {"CH1": np.zeros(4), "CH2": np.asarray(imon, float)}
        self.time_us = np.array([-2.0, -1.0, 0.0, 1.0])


class _Scope:
    channel_aliases = {"vmon": "CH1", "imon": "CH2"}


def _cal(imon_offset_v):
    c = ReadbackCalibration()
    c.imon_offset_v = imon_offset_v      # raw scope volts
    c.imon_v_per_ua_actual = 1.0e-3      # 1 mV/µA
    c.vmon_v_per_v_actual = 0.25
    return c


def _capture(apply_offset):
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    # Raw I_mon = 0.010 V everywhere; a +2 mV offset means the "true" current
    # is (0.010 − 0.002)/1e-3 = 8 µA when the offset is applied, 10 µA when not.
    acq = _Acq([0.010, 0.010, 0.010, 0.010])
    return make_capture(0, pat, acq, _Scope(), _Stim(),
                        cal=_cal(0.002), channel=0,
                        apply_current_offset=apply_offset)


def test_offset_off_is_no_subtraction():
    cap = _capture(apply_offset=False)
    # 0.010 V / 1 mV/µA = 10 µA (no offset removed).
    assert np.allclose(cap.i_mon_ua, 10.0), cap.i_mon_ua


def test_offset_on_is_now_a_noop():
    """Operator (0.2.226): "Do not calibrate the system based on the
    verification test — only the scaling whether it is default or NIL."

    ``apply_current_offset=True`` used to subtract ``cal.imon_offset_v``
    (10 µA → 8 µA here).  No verification-FITTED quantity may touch a capture
    any more, so the flag is retained for call compatibility but does nothing.
    """
    cap = _capture(apply_offset=True)
    assert np.allclose(cap.i_mon_ua, 10.0), cap.i_mon_ua


def test_fitted_scalings_are_ignored_in_favour_of_the_preset():
    """The core of the instruction: a verification's fitted scalings must NOT
    override the stimulator PRESET.  Here the fit claims 2 mV/µA and 0.5 V/V
    while the preset says 1 mV/µA and 0.25 V/V — the preset must win, or a bad
    sweep silently rescales every later measurement (gotcha #161)."""
    from stimtest.waveforms import PulsePattern
    c = ReadbackCalibration()
    c.imon_v_per_ua_actual = 2.0e-3          # 2x the preset
    c.vmon_v_per_v_actual = 0.5              # 2x the preset
    c.imon_offset_v = 0.002
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    acq = _Acq([0.010, 0.010, 0.010, 0.010])
    cap = make_capture(0, pat, acq, _Scope(), _Stim(), cal=c, channel=0)
    # PRESET 1 mV/µA -> 10 µA (not 5 µA, which the fitted 2 mV/µA would give).
    assert np.allclose(cap.i_mon_ua, 10.0), cap.i_mon_ua


def test_per_channel_gain_is_not_applied():
    """``apply_imon`` (per-channel gain ``a`` / offset ``b``) is dormant: the
    degenerate 1e14 slope of gotcha #161 must be structurally unable to reach
    a capture, not merely caught by a plausibility guard."""
    from stimtest.waveforms import PulsePattern
    from stimtest.readback_calibration import ChannelCoeffs
    c = ReadbackCalibration()
    c.channels = {0: ChannelCoeffs(a=3.0, b=100.0)}
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    acq = _Acq([0.010, 0.010, 0.010, 0.010])
    cap = make_capture(0, pat, acq, _Scope(), _Stim(), cal=c, channel=0)
    assert np.allclose(cap.i_mon_ua, 10.0), cap.i_mon_ua


def test_default_is_off():
    """Omitting the kwarg keeps the gotcha-#78 no-subtract behavior."""
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    acq = _Acq([0.010, 0.010, 0.010, 0.010])
    cap = make_capture(0, pat, acq, _Scope(), _Stim(), cal=_cal(0.002),
                       channel=0)   # no apply_current_offset
    assert np.allclose(cap.i_mon_ua, 10.0), cap.i_mon_ua
