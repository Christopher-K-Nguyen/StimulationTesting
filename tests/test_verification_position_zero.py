"""Verification never moves a channel's vertical position off zero.

Operator: "Do not change the vertical position from 0."

It used to re-centre the cathodic-heavy V_mon with a non-zero
``CHx:POSition`` (bench: 2.592 div) on the premise that "POSition is
ADC-centering only, so the reconstructed volts are unaffected".  That premise
FAILED on this hardware: the TBS2000 answers ``WFMOutpre?`` with ``YOFf = 0``
even when the channel IS positioned, so the decode never removed the shift and
every sample came back offset by ``position x V/div`` -- the +951 mV "V_mon
offset" against a scope screen showing none.

It is also self-defeating here specifically: verification exists to MEASURE
the V_mon DC offset, so injecting a screen offset into that same channel
corrupts the measurement it is there to make.
"""
from __future__ import annotations

import pathlib
import re


def _cal_src() -> str:
    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / "stimtest" / "gui" / "calibration.py").read_text(
        encoding="utf-8")


def test_every_position_write_is_zero():
    """Any set_channel_position in the verification tab must write 0."""
    src = _cal_src()
    calls = re.findall(r"self\._scope\.set_channel_position\(\s*([^)]*)\)", src)
    assert calls, "expected the setup / default-view position writes"
    for args in calls:
        # Second argument is the position in divisions.
        pos = args.split(",")[-1].strip()
        assert pos in ("0.0", "0"), f"non-zero vertical position write: {args}"


def test_centring_block_is_gone():
    src = _cal_src()
    # The block's own machinery must not come back.
    assert "_pos_tgt" not in src
    assert "_bias_ratio" not in src
    assert "Centre the asymmetric V_mon" not in src


def test_intent_is_documented():
    """A future edit should hit the warning before re-adding the write."""
    src = _cal_src()
    assert "Do not change the vertical position from 0" in src
    assert "Do NOT reintroduce a ``set_channel_position`` call here" in src


def test_driver_crosscheck_still_guards_the_experiment_path():
    """Verification stops centring, but the EXPERIMENT still does -- so the
    YOFF/position cross-check must remain."""
    root = pathlib.Path(__file__).resolve().parent.parent
    drv = (root / "stimtest" / "hardware" / "tektronix.py").read_text(
        encoding="utf-8")
    assert "_yoff_used" in drv
    assert "(raw.astype(np.float64) - _yoff_used)" in drv
