"""Calibration V_mon clipped-equilibrium fix.

Adversarial review surfaced a PRE-EXISTING defect: the test-board V_mon
waveform is strongly asymmetric (cathodic |v_min| ≈ 3.1× v_max at 50 µs
phases), but calibration pinned V_mon POSition to 0 and sized via the
offset-blind centered half-range.  The scope rail truncated the observed
range until adapt settled (ideal == current) with the cathodic peak
still railed — and the railed dwell (< 5 % of the record) never tripped
the ``_clipped`` 5 %-of-samples doubling.  Result: the I·R edge-step
measurand biased −13…−18 %, tripping the >10 % R-retry.

Fix in ``calibration._capture_one_amplitude``:
* ONE-SIDED rail detection via the position-aware ``channel_is_clipped``
  → ×2 doubling + ``force_grow=True`` (rail reads are extrapolations —
  the fits-now veto must not block the escape);
* coordinated POSITION of the asymmetric midpoint on faithful captures
  (both peaks then fit at a finer V/div);
* I_mon's ``force_grow`` keys on the RAIL check only — NOT ``_clipped``,
  which false-positives on square plateaus (the verified I_mon
  improvement from the fits-now gate must survive).
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _scope(current_scale: float, half_vert_divs: float = 5.0):
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._half_vert_divs = half_vert_divs
    s._log = lambda *a, **k: None
    s._inst = object()
    writes = []
    s.set_channel_scale = lambda ch, vpd: writes.append((ch, vpd))
    s._adapt_state = {"CH1": s._new_adapt_state()}
    s._adapt_state["CH1"]["last_scale"] = current_scale
    return s, writes


def test_rail_truncated_equilibrium_escapes_with_double_plus_force_grow():
    """The exact equilibrium: at 180 mV/div (pos 0, rail ±0.9 V) the
    rail-truncated read [-0.9, +0.42] gives ideal == current → the old
    code settled forever with the cathodic peak railed.  The fix doubles
    the range AND passes force_grow → adapt must GROW."""
    s, writes = _scope(current_scale=0.18)
    # Old behavior (no doubling, no force_grow): settles.
    out = s.adapt_channel_scale("CH1", v_min=-0.9, v_max=0.42,
                                divs=4.0, shrink_stable_count=1)
    assert out is None and writes == []
    # Fixed behavior: rail detected → caller doubles + force_grow.
    out = s.adapt_channel_scale("CH1", v_min=-1.8, v_max=0.84,
                                divs=4.0, shrink_stable_count=1,
                                force_grow=True)
    assert out is not None and out > 0.18, \
        f"must escape the clipped equilibrium, got {out}"


def test_calibration_wires_rail_check_and_positioning():
    src = (_ROOT / "stimtest" / "gui" / "calibration.py").read_text(
        encoding="utf-8")
    # One-sided rail detection feeds force_grow for V_mon...
    assert "channel_is_clipped" in src
    assert "force_grow=_v_railed" in src
    # ...and I_mon's force_grow keys on the rail check ONLY (the
    # plateau-false-positive _clipped must NOT force a grow).
    assert "force_grow=_i_railed" in src
    assert "force_grow=_clipped" not in src
    # Faithful captures get the asymmetric-midpoint position write.
    assert src.count("set_channel_position") >= 3, \
        "V_mon centring write missing from _capture_one_amplitude"


def test_position_write_triggers_recapture():
    # The centring write must set scale_changed so the stored waveform
    # is re-captured at the centred state (the loop's accept contract:
    # break only when nothing changed).
    src = (_ROOT / "stimtest" / "gui" / "calibration.py").read_text(
        encoding="utf-8")
    block = src[src.find("def _capture_one_amplitude"):]
    assert "scale_changed = True  # re-capture centred" in block
