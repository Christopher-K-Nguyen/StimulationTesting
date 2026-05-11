"""Tests for the small helpers inside :mod:`stimtest.experiments.voltage_transient`.

Covers audit finding #20 (the debounced compliance check) and pins
its semantics so a future "let's go back to a single-sample check"
refactor won't silently reintroduce the spike-sensitive behaviour.
"""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.experiments.voltage_transient import _v_compliance_tripped


class TestVComplianceTripped:
    """Regression coverage for audit finding #20 — the original
    ``np.max(np.abs(v)) > rail`` form tripped on a single noisy
    sample, aborting otherwise-good ramps. The replacement requires
    ``min_consecutive`` consecutive samples above the rail."""

    RAIL = 12.0

    def test_clean_trace_does_not_trip(self):
        """Voltage well within the rail — no trip regardless of
        debounce window."""
        v = np.array([0.0, 5.0, -8.0, 11.5, -11.5, 0.0])
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=3) is False

    def test_single_spike_does_not_trip(self):
        """One isolated sample above the rail (mains pickup, ESD,
        EMI transient) MUST be rejected. This is the audit's
        primary regression."""
        v = np.array([0.0, 0.0, 99.0, 0.0, 0.0])
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=3) is False

    def test_two_consecutive_below_threshold(self):
        """Exactly 2 consecutive samples above the rail — still
        below the 3-sample default. Should NOT trip; a real
        compliance event spans tens of µs (hundreds of samples
        at 2 GS/s)."""
        v = np.array([0.0, 13.0, 13.5, 0.0, 0.0])
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=3) is False

    def test_three_consecutive_trips(self):
        """Exactly 3 consecutive at the debounce threshold — must
        trip. This nails the boundary case."""
        v = np.array([0.0, 13.0, 13.5, 13.2, 0.0])
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=3) is True

    def test_sustained_breach_trips(self):
        """A real compliance event — long run of samples above the
        rail — trips. The original behaviour we want to preserve."""
        v = np.array([0.0, 13.0, 13.5, 13.2, 13.4, 13.0, 0.0])
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=3) is True

    def test_negative_rail_excursion(self):
        """The check uses ``|v|``, so negative excursions trip the
        same way positive ones do."""
        v = np.array([0.0, -13.0, -13.5, -13.2, 0.0])
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=3) is True

    def test_mixed_polarity_breaches_dont_chain(self):
        """A negative breach then a positive breach with a sample
        between must NOT chain into a 4-sample run — the
        consecutive-counter resets on each ≤-rail sample."""
        v = np.array([13.5, 13.5, 0.0, -13.5, -13.5])
        # First run: 2, then reset, then 2 more. Max consecutive = 2.
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=3) is False

    def test_empty_trace_is_false(self):
        """Defensive guard for callers that pass a pre-acquisition
        placeholder."""
        assert _v_compliance_tripped(np.array([]),
                                      threshold_v=self.RAIL,
                                      min_consecutive=3) is False

    def test_none_trace_is_false(self):
        """Same guard for None — better to swallow than raise from
        inside the capture loop."""
        assert _v_compliance_tripped(None,
                                      threshold_v=self.RAIL,
                                      min_consecutive=3) is False

    def test_min_consecutive_1_matches_old_behaviour(self):
        """Setting min_consecutive=1 recovers the original
        any-sample-above-rail behaviour — useful for callers that
        opt out of the debounce."""
        v_spike = np.array([0.0, 0.0, 99.0, 0.0])
        assert _v_compliance_tripped(v_spike, threshold_v=self.RAIL,
                                      min_consecutive=1) is True

    def test_min_consecutive_larger_window(self):
        """Larger debounce windows require longer runs. A 3-sample
        breach should NOT trip a 5-sample-min check."""
        v = np.array([13.0, 13.0, 13.0, 0.0, 0.0])
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=5) is False

    def test_rail_exact_equality_does_not_trip(self):
        """Spec says ``> rail`` (strictly greater). A sample
        exactly at the rail is technically still on the compliant
        side."""
        v = np.array([self.RAIL, self.RAIL, self.RAIL, self.RAIL])
        assert _v_compliance_tripped(v, threshold_v=self.RAIL,
                                      min_consecutive=3) is False
