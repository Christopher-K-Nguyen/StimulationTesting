"""Tests for ExperimentRunner._smooth_acquisition — the operator
waveform-smoothing applied to every recorded capture (plot + metrics +
saved .npz) via a centered moving average.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from stimtest.experiments.base import ExperimentRunner


class _DummyRunner(ExperimentRunner):
    def run(self):  # satisfy the lone @abstractmethod
        raise NotImplementedError


def _runner(enabled, window):
    r = _DummyRunner.__new__(_DummyRunner)
    r.smoothing_enabled = enabled
    r.smoothing_window = window
    return r


def test_smoothing_off_leaves_arrays_unchanged():
    r = _runner(False, 5)
    arr = np.linspace(0.0, 1.0, 200)
    acq = SimpleNamespace(channels={"CH1": arr.copy()})
    r._smooth_acquisition(acq)
    assert np.array_equal(acq.channels["CH1"], arr)


def test_smoothing_window_below_2_is_noop():
    r = _runner(True, 1)
    arr = np.linspace(0.0, 1.0, 200)
    acq = SimpleNamespace(channels={"CH1": arr.copy()})
    r._smooth_acquisition(acq)
    assert np.array_equal(acq.channels["CH1"], arr)


def test_smoothing_attenuates_a_spike_and_preserves_mass():
    r = _runner(True, 5)
    arr = np.zeros(100)
    arr[50] = 1.0  # unit spike on a flat baseline
    acq = SimpleNamespace(channels={"CH1": arr.copy(), "CH2": arr.copy()})
    r._smooth_acquisition(acq)
    # 5-point centered MA spreads a unit spike over 5 samples → peak ≈ 0.2.
    assert acq.channels["CH1"][50] < 0.5
    assert abs(acq.channels["CH1"][50] - 0.2) < 0.05
    # Local mass (area) is preserved by a moving average.
    assert acq.channels["CH1"][48:53].sum() > 0.9
    # Every channel is smoothed, not just the first.
    assert acq.channels["CH2"][50] < 0.5


def test_smoothing_tolerates_missing_or_empty_channels():
    r = _runner(True, 5)
    # No channels / empty dict / tiny array → must not raise.
    r._smooth_acquisition(SimpleNamespace(channels={}))
    r._smooth_acquisition(SimpleNamespace(channels=None))
    acq = SimpleNamespace(channels={"CH1": np.array([1.0, 2.0])})
    r._smooth_acquisition(acq)  # size <= window → returned as-is, no raise
