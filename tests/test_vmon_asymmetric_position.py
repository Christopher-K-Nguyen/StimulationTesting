"""Asymmetric / triphasic vertical positioning for V_mon (I_mon pinned).

Operator: "For asymmetric (and triphasic) waveforms, the vertical
positioning must be adjusted as well."

A SYMMETRIC biphasic V_mon swings evenly around 0, so the excursion
midpoint (min+max)/2 ≈ 0 and the scope position stays at 0.  An
ASYMMETRIC biphasic or TRIPHASIC pulse is lopsided — the midpoint is
offset from 0 — so the trace must be POSITION-shifted to sit centred on
screen instead of clipping the larger excursion.

The rescale loop runs V_mon through the SAME
``compute_scale_position_targets`` bias-ratio helper it uses for the
DC-biased E_ret / E_act roles, but on the RAW observed range (centre on
the excursion midpoint, not a baseline).  These tests pin the helper's
behaviour at the heart of that path: zero offset for a symmetric pulse,
a real offset for an asymmetric one.

**I_mon is deliberately NOT positioned** (operator: "Make sure that
Imon is always in vertical position 0 because that may be contributing
to the weird offset that I am seeing in anodal first") — POSition 0 is
an invariant for it; only its V/div is managed (analytic sizing +
clip-grow).  ``test_rescale_loop_never_positions_imon`` pins that.
"""
from __future__ import annotations

import inspect

import pytest


def _helper():
    from stimtest.hardware.tektronix import TektronixOscilloscope
    return (TektronixOscilloscope.compute_scale_position_targets,
            TektronixOscilloscope._TEK_VERTICAL_GRID_VPD)


def test_symmetric_vmon_keeps_position_zero():
    fn, grid = _helper()
    # Symmetric biphasic V_mon: ±250 mV around 0.
    out = fn(-0.250, +0.250, divs=3.0, grid=grid, position_limit_divs=5.0)
    assert out is not None
    vpd, pos_divs, bias_ratio, regime = out
    assert regime == "AC-centered"
    assert bias_ratio < 0.1
    assert abs(pos_divs) < 1e-6, \
        f"symmetric pulse must keep position 0, got {pos_divs}"


def test_asymmetric_vmon_gets_position_offset():
    fn, grid = _helper()
    # Asymmetric biphasic: cathodic -500 mV, anodic +100 mV → midpoint
    # -200 mV, Vpp 600 mV → bias_ratio = 2*200/600 ≈ 0.67 (moderate bias).
    out = fn(-0.500, +0.100, divs=3.0, grid=grid, position_limit_divs=5.0)
    assert out is not None
    vpd, pos_divs, bias_ratio, regime = out
    assert bias_ratio > 0.1, "lopsided excursion must register a bias"
    assert abs(pos_divs) > 0.05, \
        f"asymmetric pulse must shift position, got {pos_divs}"
    # The position offset stays within the ±5-div hardware limit.
    assert abs(pos_divs) <= 5.0


def test_triphasic_strong_asymmetry_offsets_within_limit():
    fn, grid = _helper()
    # Triphasic-ish lopsided envelope: -800 mV .. +50 mV.
    out = fn(-0.800, +0.050, divs=3.0, grid=grid, position_limit_divs=5.0)
    assert out is not None
    vpd, pos_divs, bias_ratio, regime = out
    assert abs(pos_divs) > 0.05
    assert abs(pos_divs) <= 5.0
    assert vpd > 0


def test_rescale_loop_never_positions_imon():
    """I_mon POSition 0 invariant (operator: "Make sure that Imon is
    always in vertical position 0 …").

    The shared rescale loop had TWO sites that could move a role's
    position — the asymmetric-centring branch (after a V/div change)
    and the recentre-at-kept-scale branch (adapt settled).  Both must
    exclude ``imon``.  Source-level pin (the loop's per-role branches
    aren't reachable behaviourally without a full scope+runner rig):
    the centring branch keys on ``_role == "vmon"`` alone, and the
    recentre condition carries the explicit ``_role != "imon"`` guard.
    """
    from stimtest.experiments.base import ExperimentRunner
    src = inspect.getsource(ExperimentRunner.rescale_to_fit)
    # 1. The asymmetric-centring branch is vmon-only (the old form was
    #    ``elif _role in ("vmon", "imon"):``).
    assert 'elif _role == "vmon":' in src, (
        "the asymmetric-centring branch must apply to vmon ONLY")
    assert '_role in ("vmon", "imon")' not in src, (
        "imon crept back into the position-centring branch")
    # 2. The recentre-at-kept-scale branch explicitly skips imon.
    assert 'not _overflowed and _role != "imon"' in src, (
        "the recentre branch must exclude imon (POSition 0 invariant)")
