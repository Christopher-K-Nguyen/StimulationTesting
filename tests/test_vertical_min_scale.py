"""Model-aware minimum vertical scale (V/div).

Operator: "I had set 2 mV/div to be the minimum because that was what the
TBS1104B had, but I think the TBS2204B can go a bit smaller."  Verified LIVE
on the bench TBS2204B: the vertical floor is **1 mV/div** (0.5 mV/div requests
clamp up to 1 mV; 1/1.25/1.5 mV/div held exactly), one step finer than the
TBS1104B's 2 mV/div.  So the getWaveform3.m grid (2 mV floor) is extended DOWN
to the connected model's ``TekSeriesSpec.min_vdiv_v`` by
``TektronixOscilloscope._vertical_grid_vpd`` — 1 mV on a TBS2000B, unchanged
(2 mV) on the legacy 8-div families.
"""
from __future__ import annotations

import pytest


def test_base_grid_constant_unchanged_2mv_floor():
    """The raw getWaveform3.m class constant still floors at 2 mV (fidelity
    to the MATLAB port); the model-aware extension lives in the property."""
    from stimtest.hardware import tektronix as T
    g = T.TektronixOscilloscope._TEK_VERTICAL_GRID_VPD
    assert abs(g[0] - 0.002) < 1e-9
    assert list(g) == sorted(g)


def test_tbs2000b_spec_min_is_1mv():
    from stimtest.hardware.tektronix_models import get_series_spec
    spec = get_series_spec("TBS2204B")
    assert spec is not None
    assert spec.series_name == "TBS2000B"
    assert spec.min_vdiv_v == pytest.approx(1e-3)


def test_legacy_spec_min_is_2mv():
    from stimtest.hardware.tektronix_models import get_series_spec
    # TBS1104B is the legacy 8-div scope MATLAB targeted → 2 mV floor.
    for model in ("TBS1104", "TDS2014B", "TPS2014B"):
        spec = get_series_spec(model)
        if spec is not None:
            assert spec.min_vdiv_v == pytest.approx(2e-3), model


def _bare_scope(min_vdiv_v):
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._min_vdiv_v = min_vdiv_v
    return s


def test_vertical_grid_property_extends_to_1mv():
    s = _bare_scope(1e-3)
    grid = s._vertical_grid_vpd
    assert grid[0] == pytest.approx(1e-3)          # new floor
    assert 1.5e-3 in [pytest.approx(v) for v in grid] or any(
        abs(v - 1.5e-3) < 1e-9 for v in grid)      # the 1.5 mV step too
    assert any(abs(v - 2e-3) < 1e-9 for v in grid)  # base grid still present
    assert list(grid) == sorted(grid)              # strictly ordered
    assert grid[-1] == pytest.approx(5.0)          # top unchanged


def test_vertical_grid_property_default_is_base_grid():
    from stimtest.hardware import tektronix as T
    s = _bare_scope(2e-3)
    assert s._vertical_grid_vpd == T.TektronixOscilloscope._TEK_VERTICAL_GRID_VPD


def test_small_signal_fine_fits_to_1mv_on_tbs2000b():
    """A tiny ±0.6 mV signal (half-range 0.6 mV, divs=4 → ideal 0.15 mV/div)
    snaps (ceil) to 1 mV/div on a TBS2000B but 2 mV/div on a legacy scope."""
    fine = _bare_scope(1e-3)
    legacy = _bare_scope(2e-3)
    from stimtest.hardware.tektronix import TektronixOscilloscope as TO
    ideal = 0.6e-3 / 4.0
    assert TO._snap_to_grid(max(ideal, fine._vertical_grid_vpd[0]),
                            fine._vertical_grid_vpd,
                            direction="ceil") == pytest.approx(1e-3)
    assert TO._snap_to_grid(max(ideal, legacy._vertical_grid_vpd[0]),
                            legacy._vertical_grid_vpd,
                            direction="ceil") == pytest.approx(2e-3)
