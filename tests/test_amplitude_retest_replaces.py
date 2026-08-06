"""Re-testing an amplitude REPLACES its previous values.

Operator: "when retesting an amplitude, replace the previous amplitude
values."

A re-test exists to CORRECT a bad capture.  Appending it would leave the bad
value in the pool to be averaged in beside its own replacement -- and because
the fit now consumes every individual measurement (all four iR drops and both
phase slopes per capture), one stale entry contributes several bad points.

Three parallel per-channel stores must stay consistent: the results row, the
raw per-measurement values the fit expands, and the r2 history.
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


def _tab(_app):
    from stimtest.gui.calibration import CalibrationTab
    return CalibrationTab(stim=None, scope=None)


def _row(amp_ua, r_ohm):
    row = [0.0] * 12
    row[0], row[5] = amp_ua, r_ohm
    return tuple(row)


def _seed(tab, ch=1):
    tab._results[ch] = [_row(25.0, 1164.0), _row(50.0, 4990.0)]
    tab._raw_meas[ch] = [(25e-6, [0.1], [-5000.0]),
                         (50e-6, [0.25], [-10000.0])]
    tab._r2_meas[ch] = [(25.0, -196.0), (50.0, 0.998)]


def test_purge_removes_only_the_matching_amplitude(_app):
    tab = _tab(_app)
    try:
        _seed(tab)
        n = tab._purge_amplitude(1, 25.0)
        assert n == 1
        assert [r[0] for r in tab._results[1]] == [50.0]
        assert [round(e[0] * 1e6) for e in tab._raw_meas[1]] == [50]
        assert [e[0] for e in tab._r2_meas[1]] == [50.0]
    finally:
        tab.deleteLater()


def test_purge_keeps_all_three_stores_in_step(_app):
    """A stale raw entry would feed the fit even with the row replaced."""
    tab = _tab(_app)
    try:
        _seed(tab)
        tab._purge_amplitude(1, 25.0)
        amps_rows = {r[0] for r in tab._results[1]}
        amps_raw = {round(e[0] * 1e6, 6) for e in tab._raw_meas[1]}
        amps_r2 = {e[0] for e in tab._r2_meas[1]}
        assert amps_rows == amps_raw == amps_r2
    finally:
        tab.deleteLater()


def test_first_pass_at_an_amplitude_removes_nothing(_app):
    tab = _tab(_app)
    try:
        _seed(tab)
        assert tab._purge_amplitude(1, 100.0) == 0
        assert len(tab._results[1]) == 2
    finally:
        tab.deleteLater()


def test_purge_is_safe_on_an_untouched_channel(_app):
    tab = _tab(_app)
    try:
        assert tab._purge_amplitude(7, 25.0) == 0
    finally:
        tab.deleteLater()


def test_purge_tolerates_malformed_rows(_app):
    """A short/garbage row must not abort the sweep."""
    tab = _tab(_app)
    try:
        tab._results[1] = [(), ("x",), _row(25.0, 1164.0)]
        tab._raw_meas[1] = [(), (25e-6, [0.1], [-5000.0])]
        tab._r2_meas[1] = [(), (25.0, 0.5)]
        assert tab._purge_amplitude(1, 25.0) == 1
    finally:
        tab.deleteLater()


def test_retest_supersedes_rather_than_averages(_app):
    """The bench case: a bad 25 uA (1164 ohm) re-tested to a good one must
    leave ONLY the good value behind."""
    tab = _tab(_app)
    try:
        _seed(tab)
        tab._purge_amplitude(1, 25.0)
        tab._results[1].append(_row(25.0, 4990.0))
        vals = sorted(r[5] for r in tab._results[1])
        assert 1164.0 not in vals
        assert vals == [4990.0, 4990.0]
    finally:
        tab.deleteLater()


def test_sweep_purges_before_capturing():
    """The purge must run BEFORE the capture -- the raw / r2 stores are
    written DURING it, so clearing afterwards would drop the fresh data."""
    import pathlib
    import stimtest.gui.calibration as c
    src = pathlib.Path(c.__file__).read_text(encoding="utf-8")
    i_purge = src.find("self._purge_amplitude(ch, amp_ua)")
    i_cap = src.find("= self._capture_one_amplitude(")
    assert i_purge != -1 and i_cap != -1
    assert i_purge < i_cap
