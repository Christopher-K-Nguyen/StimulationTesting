"""Driving vs E_pol marker colours are distinct; no markers at 0 µA.

Operator:
  * "The color for marker and label of driving voltage and electrode
    polarization are too similar."  Both use a "+" glyph, so their colours
    must be far apart.
  * "The experiment plot at 0 µA can have markers, specifically electrode
    polarization, are at incorrect locations."  A 0 µA capture must draw no
    metric markers.
"""
from __future__ import annotations

import numpy as np

from stimtest.plotting import MARKER_COLOURS, compute_metric_markers
from stimtest.session import Capture, CaptureMetrics, CaptureStatus
from stimtest.waveforms import PulsePattern


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def test_driving_and_polar_colors_are_distinct():
    d = _hex_to_rgb(MARKER_COLOURS["driving"])
    p = _hex_to_rgb(MARKER_COLOURS["polar"])
    # Both are "+" glyphs → require a large RGB separation (the old
    # reddish-purple/purple pair differed by only ~25 total).
    dist = sum(abs(a - b) for a, b in zip(d, p))
    assert dist > 150, (MARKER_COLOURS["driving"], MARKER_COLOURS["polar"], dist)
    assert MARKER_COLOURS["driving"] != MARKER_COLOURS["polar"]


def _cap(amp_ua):
    pat = PulsePattern.biphasic(amplitude_ua=amp_ua, polarity=-1)
    n = 500
    t = np.linspace(-50.0, 250.0, n)
    # A plausible biphasic-ish V_mon so a non-zero capture DOES yield markers.
    v = np.zeros(n)
    v[(t >= 0) & (t < 100)] = -0.3
    v[(t >= 100) & (t < 200)] = 0.3
    i = np.zeros(n)
    i[(t >= 0) & (t < 100)] = amp_ua
    i[(t >= 100) & (t < 200)] = -amp_ua
    m = CaptureMetrics()
    return Capture(index=0, pattern=pat, time_us=t, v_mon_v=v, i_mon_ua=i,
                   metrics=m, status=CaptureStatus())


def test_no_markers_at_zero_ua():
    assert compute_metric_markers(_cap(0.0)) == []


def test_markers_present_at_real_current():
    # A real (non-zero) capture still produces markers.
    assert len(compute_metric_markers(_cap(-100.0))) > 0


def _shaped_cap(shape, amp_ua, iph=20.0):
    from stimtest.waveforms import Phase, PulsePattern as _PP
    pat = _PP(phases=[
        Phase(amplitude_ua=-amp_ua, width_us=200, shape=shape,
              delay_after_us=iph),
        Phase(amplitude_ua=amp_ua, width_us=200, shape=shape,
              delay_after_us=iph)], rate_hz=200.0)
    n = 500
    t = np.linspace(-64.0, 900.0, n)
    rng = np.random.RandomState(2)
    v = 0.002 * rng.randn(n)                 # 0 µA → noise floor
    return Capture(index=0, pattern=pat, time_us=t, v_mon_v=v,
                   i_mon_ua=np.zeros(n), metrics=CaptureMetrics(),
                   status=CaptureStatus())


def test_no_epol_guides_at_zero_ua_shaped():
    """Operator: "sinusoidal and gaussian … at 0 µA, the electrode
    polarization are misplaced".  The E_pol GUIDE lines (expected-location
    vertical guides) must also be suppressed at 0 µA — otherwise the onset
    detector reads noise and draws them at meaningless positions."""
    from stimtest.plotting import expected_epol_times_us
    from stimtest.waveforms import SHAPE_SINUSOIDAL, SHAPE_GAUSSIAN
    for shape in (SHAPE_SINUSOIDAL, SHAPE_GAUSSIAN):
        assert expected_epol_times_us(_shaped_cap(shape, 0.0)) == []
        # A real current still yields guides (interphase-delay phases).
        assert len(expected_epol_times_us(_shaped_cap(shape, 50.0))) > 0


def test_no_epol_guides_for_bad_response():
    """Even at a real current, a BROKEN / OPEN / CAPACITIVE electrode has no
    meaningful electrode polarization, so the E_pol GUIDE lines must be
    suppressed — matching the markers (gotcha #58/#86).  Operator: "even for
    broken, why are the locations for electrode polarization incorrect?? Why
    is the marker located in the interpulse".  (At low current a bad capture's
    onset detector reads noise and drops the guide into the interpulse.)"""
    from stimtest.plotting import expected_epol_times_us
    from stimtest.waveforms import SHAPE_SINUSOIDAL
    # A real-current NORMAL capture DOES yield guides.
    assert len(expected_epol_times_us(_shaped_cap(SHAPE_SINUSOIDAL, 50.0))) > 0
    # Same pattern + current, but a bad response class → NO guides.
    for rc in ("broken", "open", "capacitive"):
        cap = _shaped_cap(SHAPE_SINUSOIDAL, 50.0)
        cap.metrics.response_class = rc
        assert expected_epol_times_us(cap) == [], rc
