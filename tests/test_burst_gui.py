"""Burst / pulse-train GUI + preview + runner cadence.

The backend (PulsePattern.pulses_per_burst / burst_period_us + properties +
build_burst_pat_pairs) is covered in test_burst_pattern.py.  This exercises the
GUI wiring: the pattern-panel burst group, its gating per experiment tab, the
pattern() burst stamp, prefs round-trip, the burst-period auto-clamp, the
preview burst drawing, and the runner burst-aware pulse-timing helpers.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")


@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


# --------------------------------------------------------------------------
# pattern-panel burst controls
# --------------------------------------------------------------------------
def _panel(_app, *, available=True):
    from stimtest.gui.pattern_panel import PatternControlPanel
    p = PatternControlPanel(title="Pulse pattern")
    p.set_burst_available(available)
    return p


def test_burst_group_hidden_until_available(_app):
    p = _panel(_app, available=False)
    assert p.burst_group.isVisible() is False
    # even if the enable box were ticked, an unavailable tab never bursts
    p.burst_enable_check.setChecked(True)
    assert p.pattern().is_burst is False


def test_burst_group_shown_when_available(_app):
    p = _panel(_app, available=True)
    # visibility only resolves once shown; assert not-hidden (explicit state)
    assert p.burst_group.isHidden() is False
    # default: enable box off → non-burst pattern
    assert p.burst_enable_check.isChecked() is False
    assert p.pattern().is_burst is False


def test_enabling_burst_emits_burst_pattern(_app):
    p = _panel(_app, available=True)
    # high intra-burst rate so the default 25 ms period easily fits the burst
    p.rate_pps.setValue(500.0)
    p.burst_enable_check.setChecked(True)
    p.pulses_per_burst_spin.setValue(5)
    p.burst_period_ms.setValue(25.0)
    pat = p.pattern()
    assert pat.is_burst is True
    assert pat.pulses_per_burst == 5
    assert pat.burst_period_us == pytest.approx(25000.0)


def test_disabling_burst_reverts_to_plain(_app):
    p = _panel(_app, available=True)
    p.rate_pps.setValue(500.0)
    p.burst_enable_check.setChecked(True)
    assert p.pattern().is_burst is True
    p.burst_enable_check.setChecked(False)
    assert p.pattern().is_burst is False
    assert p.pattern().pulses_per_burst == 1


def test_burst_period_auto_clamps_to_span(_app):
    # a LOW intra-burst rate makes the burst span large; the period floor
    # must rise so the burst is always valid (validate() would else raise).
    p = _panel(_app, available=True)
    p.rate_pps.setValue(50.0)          # 20 ms intra-period
    p.burst_enable_check.setChecked(True)
    p.pulses_per_burst_spin.setValue(5)
    pat = p.pattern()
    # span = 4*20ms + pulse ≈ 80 ms > the 25 ms default → period clamped up
    assert pat.burst_period_us >= pat.burst_span_us - 1.0
    pat.validate()   # must not raise


def test_burst_period_capped_at_device_ceiling(_app):
    # a huge pulse count × slow intra-rate makes the span exceed the
    # 125,000 ms PS_SetPeriod ceiling — the auto-clamp must NOT inflate the
    # period past the spinbox max, and the readout must warn.
    p = _panel(_app, available=True)
    p.rate_pps.setValue(1.0)               # 1000 ms intra-period
    p.burst_enable_check.setChecked(True)
    p.pulses_per_burst_spin.setValue(127)  # span ≈ 126 s > 125 s max
    assert p.burst_period_ms.value() <= p.burst_period_ms.maximum() + 1e-6
    assert "too long" in p.burst_readout.text().lower()


def test_preview_large_burst_caps_actual_and_desired(_app):
    # a SHAPED burst with N > N_PULSES_DRAWN: both the Desired curve AND the
    # Actual staircase truncate to draw_pulses, so they don't disagree on the
    # drawn pulse count (and the point budget stays bounded).
    from stimtest.gui.pattern_preview import PatternPreview
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=50.0, phase_width_us=100.0,
                                interphase_us=0.0, discharge_us=0.0,
                                rate_hz=1000.0, symmetric=True,
                                shape="sinusoidal")
    pat.pulses_per_burst = 30              # > N_PULSES_DRAWN (21)
    pat.burst_period_us = 60000.0
    prev = PatternPreview()
    prev.set_pattern(pat)                  # must not crash
    des = prev.curve.getData()[0]
    act = prev.curve_actual.getData()[0]
    assert des is not None and len(des) > 0
    assert act is not None and len(act) > 0
    # both truncated → end at comparable x (within one burst period), not the
    # Actual running 9 extra pulses past the Desired
    assert abs(float(act.max()) - float(des.max())) < pat.burst_period_us


def test_burst_prefs_round_trip(_app):
    p = _panel(_app, available=True)
    p.rate_pps.setValue(500.0)
    p.burst_enable_check.setChecked(True)
    p.pulses_per_burst_spin.setValue(7)
    p.burst_period_ms.setValue(40.0)
    prefs = p.current_prefs()
    assert prefs["burst_enabled"] is True
    assert prefs["pulses_per_burst"] == 7
    assert prefs["burst_period_ms"] == pytest.approx(40.0)

    p2 = _panel(_app, available=True)
    p2.restore_prefs(prefs)
    assert p2.burst_enable_check.isChecked() is True
    assert p2.pulses_per_burst_spin.value() == 7
    assert p2.burst_period_ms.value() == pytest.approx(40.0)
    assert p2.pattern().is_burst is True


def test_legacy_prefs_load_as_non_burst(_app):
    p = _panel(_app, available=True)
    p.restore_prefs({"phase_count": "Biphasic"})   # no burst keys
    assert p.burst_enable_check.isChecked() is False
    assert p.pattern().is_burst is False


# --------------------------------------------------------------------------
# per-tab gating
# --------------------------------------------------------------------------
def test_tab_supports_burst_flags():
    from stimtest.gui.experiment_tabs import (
        ShortPulsingTab, ContinuousPulsingTab, LongPulsingTab,
        VoltageTransientTab, ProgressiveStressTab,
    )
    assert ShortPulsingTab.SUPPORTS_BURST is True
    assert ContinuousPulsingTab.SUPPORTS_BURST is True    # inherits SP
    assert LongPulsingTab.SUPPORTS_BURST is True
    assert VoltageTransientTab.SUPPORTS_BURST is False
    assert ProgressiveStressTab.SUPPORTS_BURST is False


# --------------------------------------------------------------------------
# preview burst drawing
# --------------------------------------------------------------------------
def test_preview_draws_burst_without_crashing(_app):
    from stimtest.gui.pattern_preview import PatternPreview
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=50.0, phase_width_us=100.0,
                                interphase_us=0.0, discharge_us=0.0,
                                rate_hz=500.0)
    pat.pulses_per_burst = 5
    pat.burst_period_us = 25000.0
    prev = PatternPreview()
    prev.set_pattern(pat)   # must not raise
    xdata = prev.curve.getData()[0]
    assert xdata is not None and len(xdata) > 0
    # the drawn train spans more than one burst period (multiple bursts tiled)
    assert (xdata.max() - xdata.min()) > pat.burst_span_us
    # default view frames ~one burst (right edge near the burst span)
    lo, hi = prev._default_x_range
    assert hi >= pat.burst_span_us * 0.9


def test_preview_non_burst_unaffected(_app):
    from stimtest.gui.pattern_preview import PatternPreview
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=50.0, rate_hz=50.0)
    prev = PatternPreview()
    prev.set_pattern(pat)
    xdata = prev.curve.getData()[0]
    assert xdata is not None and len(xdata) > 0


# --------------------------------------------------------------------------
# runner burst-aware timing helpers
# --------------------------------------------------------------------------
def test_runner_pulses_per_second_and_fill():
    from stimtest.experiments.base import ExperimentRunner
    from stimtest.waveforms import PulsePattern

    single = PulsePattern.biphasic(amplitude_ua=50.0, rate_hz=100.0)
    burst = PulsePattern.biphasic(amplitude_ua=50.0, phase_width_us=100.0,
                                  interphase_us=0.0, discharge_us=0.0,
                                  rate_hz=500.0)
    burst.pulses_per_burst = 5
    burst.burst_period_us = 25000.0   # 5 / 25 ms = 200 pulses/sec

    assert ExperimentRunner._pulses_per_second(single) == pytest.approx(100.0)
    assert ExperimentRunner._pulses_per_second(burst) == pytest.approx(200.0)

    # _averager_fill_s = n_avg / overall_pps; use a tiny stub runner
    class _StubScope:
        _expected_acq_navg = 64

    class _R(ExperimentRunner):
        def __init__(self):
            self.scope = _StubScope()

        def run(self):  # abstract stub
            pass

    r = _R()
    # non-burst: 64 / 100 = 0.64 s
    assert r._averager_fill_s(single) == pytest.approx(0.64, rel=1e-3)
    # burst: 64 / 200 = 0.32 s (LONGER per pulse than 64/500=0.128 s — the
    # inter-burst gaps slow the effective trigger rate)
    assert r._averager_fill_s(burst) == pytest.approx(0.32, rel=1e-3)


def test_pulses_per_second_mock_pattern_degrades_gracefully():
    # a duck-typed / mock pattern with a non-numeric rate_hz must NOT raise
    # out of the runner timing path — the terminal fallback returns 0.0.
    from stimtest.experiments.base import ExperimentRunner

    class _BadPattern:
        rate_hz = object()          # truthy but non-float-convertible
    assert ExperimentRunner._pulses_per_second(_BadPattern()) == 0.0

    class _Empty:
        pass                        # no attrs at all
    assert ExperimentRunner._pulses_per_second(_Empty()) == 0.0
