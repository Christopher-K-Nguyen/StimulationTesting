"""Elapsed time to complete a channel/combo, shown on the metric measurements.

Operator: "add time elapsed for complete channel/combo on the metric
measurements."  ``ChannelRun`` already records ``started_at`` (at
construction) + ``finished_at`` (stamped by the runner on completion, and
round-tripped in persistence), so this is a display-only add:
``ChannelRun.duration_s`` → the POLARIS channel/combo metric table
("Time to complete") + the Gamry electrode-sheet ELECTRODE preamble
("DURATION").
"""
from __future__ import annotations

from datetime import datetime, timedelta

import math
import pytest

from stimtest.session import ChannelRun
from stimtest.electrode import Configuration


def _cfg():
    # A simple monopolar config on CH01.
    return Configuration.monopolar(1)


def test_duration_s_nan_until_finished():
    run = ChannelRun(configuration=_cfg())
    assert math.isnan(run.duration_s)          # finished_at is None


def test_duration_s_is_wall_clock_span():
    run = ChannelRun(configuration=_cfg())
    run.started_at = datetime(2026, 7, 1, 12, 0, 0)
    run.finished_at = datetime(2026, 7, 1, 12, 1, 23)   # 83 s later
    assert run.duration_s == pytest.approx(83.0)


def test_duration_s_handles_timedelta_minutes():
    run = ChannelRun(configuration=_cfg())
    run.started_at = datetime(2026, 7, 1, 12, 0, 0)
    run.finished_at = run.started_at + timedelta(hours=1, minutes=5, seconds=3)
    assert run.duration_s == pytest.approx(3903.0)


@pytest.mark.parametrize(
    "secs, expected",
    [
        (float("nan"), "—"),
        (-5.0, "—"),
        (0.0, "0s"),
        (45.0, "45s"),
        (83.0, "1m 23s"),
        (600.0, "10m 00s"),
        (3903.0, "1h 05m 03s"),
    ],
)
def test_fmt_duration_s(secs, expected):
    pytest.importorskip("pyqtgraph")
    from stimtest.gui.viewer import _fmt_duration_s
    assert _fmt_duration_s(secs) == expected


def test_gamry_electrode_sheet_has_duration_row(tmp_path):
    """The Gamry per-electrode xlsx sheet carries a DURATION metadata row."""
    pytest.importorskip("openpyxl")
    import numpy as np
    from stimtest.session import Session, TestParameters, Capture
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import ElectrodeArray
    from stimtest.metrics import compute_metrics
    from stimtest import gamry_export

    p = PulsePattern.biphasic(amplitude_ua=80.0)
    test = TestParameters(experiment="SP", pattern=p,
                          configuration=_cfg(),
                          array=ElectrodeArray.utah_4x4(), duration_s=1.0)
    sess = Session(notebook="nb", subject="s1", test=test)
    run = ChannelRun(configuration=_cfg(), surface_area_um2=5000.0)
    run.started_at = datetime(2026, 7, 1, 12, 0, 0)
    run.finished_at = datetime(2026, 7, 1, 12, 2, 0)     # 120 s
    t = np.linspace(-100.0, 600.0, 400)
    c = Capture(index=0, pattern=p)
    c.time_us = t
    c.i_mon_ua = np.where((t >= 0) & (t < 200), -80.0,
                          np.where((t >= 220) & (t < 420), 80.0, 0.0))
    c.v_mon_v = np.where((t >= 0) & (t < 200), -0.4,
                         np.where((t >= 220) & (t < 420), 0.27, 0.0))
    compute_metrics(c, surface_area_um2=5000.0)
    run.captures.append(c)
    sess.runs.append(run)

    out = tmp_path / "sess.xlsx"
    gamry_export.save_session_xlsx(sess, out)
    from openpyxl import load_workbook
    wb = load_workbook(out)
    # Find the electrode sheet + scan for a DURATION tag with the 120 s value.
    found = False
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            if row and str(row[0]) == "ELAPSED":
                assert abs(float(row[2]) - 120.0) < 1.0   # VALUE ≈ 120 s
                assert "complete" in str(row[3]).lower()
                found = True
    assert found, "no ELAPSED row in any electrode sheet"
