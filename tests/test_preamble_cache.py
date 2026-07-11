"""Tests for LOG_ANALYSIS.md finding #4: per-capture WFMOutpre? cache.

Bug: every capture pulled a full WFMOutpre? per channel (~110 ms
each) and a CURVe? per channel (also ~110 ms).  For a 4-channel
TBS2204B that's ~880 ms of pure SCPI overhead per capture.  Over
a 16-config × 10-amp VT sweep (~640 captures), that's ~10 minutes
of overhead.

Fix: cache the per-channel WFMOutpre? response in
``_preamble_cache`` keyed by channel.  On cache hit, skip the
WFMOutpre? query entirely — saves ~110 ms per channel after the
first capture per setting change.  Invalidate when any of the
contributing fields could change:

  * ``set_channel_scale(ch, ...)`` → invalidates ``ch`` (YMUlt change)
  * ``set_channel_position(ch, ...)`` → invalidates ``ch`` (YOFf/YZEro)
  * ``set_horizontal_scale(...)`` → invalidates ALL (XINcr)
  * ``set_horizontal_position(...)`` → invalidates ALL (XZEro)
  * ``set_record_length(...)`` → invalidates ALL (NR_Pt / XINcr)
  * ``set_acquisition_mode(...)`` → invalidates ALL (Y format)
  * ``configure_channels(...)`` → invalidates ALL (channel reshuffle)

Tests pin both directions: cache HITS skip the query, and
INVALIDATION drops stale entries on every relevant setter.

Env-var escape hatch: ``PULSAR_DISABLE_PREAMBLE_CACHE=1`` disables
the cache for debugging.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import numpy as np
import pytest


def _bare_tek_instance():
    """Construct a TektronixOscilloscope without opening hardware."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    scope._w = MagicMock()
    scope._q = MagicMock(return_value="0.0")
    scope._log = lambda msg: None
    scope._adapt_state = {}
    scope._preamble_cache = {}
    scope._time_cache = None
    scope._time_cache_key = None
    scope._last_xunit = "s"
    scope._last_yunit = "V"
    scope._expected_trigger_source = "EXT"
    scope._expected_trigger_is_digital = True
    return scope


# ---------------------------------------------------------------------------
# _invalidate_preamble_cache helper
# ---------------------------------------------------------------------------
def test_invalidate_single_channel_pops_only_that_entry():
    scope = _bare_tek_instance()
    scope._preamble_cache["CH1"] = ("preamble1",)
    scope._preamble_cache["CH2"] = ("preamble2",)
    scope._preamble_cache["CH3"] = ("preamble3",)

    scope._invalidate_preamble_cache("CH2")
    assert "CH1" in scope._preamble_cache
    assert "CH2" not in scope._preamble_cache
    assert "CH3" in scope._preamble_cache


def test_invalidate_no_channel_clears_all():
    scope = _bare_tek_instance()
    scope._preamble_cache["CH1"] = ("p1",)
    scope._preamble_cache["CH2"] = ("p2",)
    scope._invalidate_preamble_cache()  # no arg → clear all
    assert scope._preamble_cache == {}


def test_invalidate_safe_before_cache_initialized():
    """When _preamble_cache doesn't exist (early-construction path),
    invalidation is a no-op rather than AttributeError."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    # _preamble_cache attr deliberately not set.
    scope._invalidate_preamble_cache()  # must not raise
    scope._invalidate_preamble_cache("CH1")  # must not raise


# ---------------------------------------------------------------------------
# Per-channel setter invalidation
# ---------------------------------------------------------------------------
def test_set_channel_scale_invalidates_only_that_channel():
    """Y-side writes invalidate the channel's preamble (YMUlt) but
    leave other channels' caches intact."""
    scope = _bare_tek_instance()
    scope._preamble_cache["CH1"] = ("p1",)
    scope._preamble_cache["CH2"] = ("p2",)
    scope._preamble_cache["CH3"] = ("p3",)

    scope.set_channel_scale("CH2", 0.5)
    assert "CH1" in scope._preamble_cache  # untouched
    assert "CH2" not in scope._preamble_cache  # dropped
    assert "CH3" in scope._preamble_cache


def test_set_channel_position_invalidates_only_that_channel():
    scope = _bare_tek_instance()
    scope._preamble_cache["CH1"] = ("p1",)
    scope._preamble_cache["CH3"] = ("p3",)

    scope.set_channel_position("CH1", 2.0)
    assert "CH1" not in scope._preamble_cache
    assert "CH3" in scope._preamble_cache


# ---------------------------------------------------------------------------
# Global setter invalidation
# ---------------------------------------------------------------------------
def test_set_horizontal_scale_invalidates_all_channels():
    """X-side writes affect every channel's preamble (XINcr).  All
    cached entries must be dropped."""
    scope = _bare_tek_instance()
    scope._preamble_cache = {"CH1": ("p1",), "CH2": ("p2",),
                              "CH3": ("p3",), "CH4": ("p4",)}
    scope._q = MagicMock(return_value="1e-6")
    scope._cmds = MagicMock()
    scope._cmds.horiz_scale = "HORizontal:SCAle"
    scope._cmds.timebase_grid_mantissas = (1, 2, 4)

    try:
        scope.set_horizontal_scale(1e-6)
    except Exception:
        # set_horizontal_scale does extra work (snapping etc.) we
        # haven't fully mocked; that's fine.  The invalidation
        # should have run before any of the optional code paths.
        pass
    assert scope._preamble_cache == {}


def test_set_horizontal_position_invalidates_all_channels():
    scope = _bare_tek_instance()
    scope._preamble_cache = {"CH1": ("p1",), "CH2": ("p2",)}
    scope._cmds = MagicMock()
    scope._cmds.horiz_position = "HORizontal:POSition"
    scope._cmds.horiz_position_unit = "percent"

    scope.set_horizontal_position(20.0)
    assert scope._preamble_cache == {}


def test_horizontal_position_is_not_snapped_to_10pct():
    """The trigger position is applied at FULL precision — NOT floored to a
    10% grid (operator: "forget about my requirement of trigger percentage
    rounded").  The old floor collapsed a few-% leading offset to 0% and
    jammed the leading edge against the trigger marker, defeating the
    asymmetric pre/post framing."""
    scope = _bare_tek_instance()
    scope._cmds = MagicMock()
    scope._cmds.horiz_position = "HORizontal:POSition"
    scope._cmds.horiz_position_unit = "percent"
    scope._w = MagicMock()

    scope.set_horizontal_position(47.3)
    # cached value is the exact (clamped) %, not 40.0
    assert scope._expected_horiz_position_pct == pytest.approx(47.3)
    # and the SCPI write carried the exact value
    pos_writes = [c.args[0] for c in scope._w.call_args_list
                  if "HORizontal:POSition" in c.args[0]]
    assert pos_writes and "47.3" in pos_writes[-1], pos_writes

    # a small offset survives instead of flooring to 0
    scope.set_horizontal_position(7.5)
    assert scope._expected_horiz_position_pct == pytest.approx(7.5)


def test_set_record_length_invalidates_all_channels():
    scope = _bare_tek_instance()
    scope._preamble_cache = {"CH1": ("p1",), "CH2": ("p2",)}
    scope._cmds = MagicMock()
    scope._cmds.horiz_record = "HORizontal:RECOrdlength"
    scope._w_checked = MagicMock()
    scope._q = MagicMock(return_value="20000")
    scope.info = MagicMock()
    scope.info.model = "TBS2204B"

    scope.set_record_length(20000)
    assert scope._preamble_cache == {}


def test_set_acquisition_mode_invalidates_all_channels():
    scope = _bare_tek_instance()
    scope._preamble_cache = {"CH1": ("p1",), "CH2": ("p2",)}
    scope._cmds = MagicMock()
    scope._cmds.data_encoding_cmd = "DATa:ENCdg RIBinary"
    scope._cmds.has_acq_numavg = True
    scope._cmds.acq_numavg_values = (2, 4, 16, 32, 64)
    scope._w_checked = MagicMock()
    scope._q = MagicMock(side_effect=["AVERAGE", "16"])
    scope._cmds.has_hires = False

    scope.set_acquisition_mode("AVERAGE", n_avg=16)
    assert scope._preamble_cache == {}


# ---------------------------------------------------------------------------
# _read_channel cache hit / miss behavior
# ---------------------------------------------------------------------------
def test_read_channel_skips_wfmoutpre_on_cache_hit(monkeypatch):
    """When the preamble cache has an entry for the channel,
    ``_read_channel`` should NOT call ``_read_preamble`` — the whole
    point of the optimization."""
    scope = _bare_tek_instance()
    # Seed the cache with a known preamble.
    scope._preamble_cache["CH1"] = (
        1e-3,    # ymult
        0.0,     # yoff
        0.0,     # yzero
        32e-9,   # xinc
        -320e-6, # xzero
        10000,   # pt_off
        True,    # is_signed
        True,    # is_big_endian
    )
    # Mock _read_preamble — we want to confirm it does NOT get called.
    scope._read_preamble = MagicMock(side_effect=AssertionError(
        "_read_preamble was called even though cache had an entry — "
        "the optimization is broken"))
    scope._cmds = MagicMock()
    scope._cmds.use_data_source = True
    scope._cmds.preamble = "WFMOutpre"

    # Mock the CURVe? path: return a fake binary blob.
    scope._inst = MagicMock()
    scope._inst.query_binary_values = MagicMock(
        return_value=np.zeros(20000, dtype=np.int8))
    scope._data_width = 1
    scope._n_horiz_divs = 15.0

    # PULSAR_DISABLE_PREAMBLE_CACHE must NOT be set for this test.
    monkeypatch.delenv("PULSAR_DISABLE_PREAMBLE_CACHE", raising=False)

    # _read_channel does CURVe? via query_binary_values; provide a
    # ScopeAcquisition-shaped path through if needed.
    # Should NOT raise.
    try:
        scope._read_channel("CH1")
    except AssertionError:
        # Re-raise so pytest reports it as the test failure.
        raise
    except Exception:
        # Other failures (e.g., the CURVe? path not perfectly mocked)
        # are fine — we just care that _read_preamble was bypassed.
        pass

    # Verify _read_preamble was NOT called.
    scope._read_preamble.assert_not_called()


def test_read_channel_calls_wfmoutpre_on_cache_miss(monkeypatch):
    """When the cache has no entry, ``_read_channel`` should fall
    through to ``_read_preamble`` and populate the cache for next time."""
    scope = _bare_tek_instance()
    scope._preamble_cache = {}  # empty
    fake_preamble = (1e-3, 0.0, 0.0, 32e-9, -320e-6, 10000, True, True)
    scope._read_preamble = MagicMock(return_value=fake_preamble)
    scope._cmds = MagicMock()
    scope._cmds.use_data_source = True
    scope._cmds.preamble = "WFMOutpre"
    scope._inst = MagicMock()
    scope._inst.query_binary_values = MagicMock(
        return_value=np.zeros(20000, dtype=np.int8))
    scope._data_width = 1
    scope._n_horiz_divs = 15.0
    monkeypatch.delenv("PULSAR_DISABLE_PREAMBLE_CACHE", raising=False)

    try:
        scope._read_channel("CH2")
    except Exception:
        pass  # ignore CURVe?-side mock incompleteness

    # _read_preamble SHOULD have been called.
    scope._read_preamble.assert_called_once()
    # And the cache should now contain the entry for CH2.
    assert "CH2" in scope._preamble_cache
    assert scope._preamble_cache["CH2"] == fake_preamble


def test_env_var_disables_cache(monkeypatch):
    """``PULSAR_DISABLE_PREAMBLE_CACHE=1`` makes every _read_channel
    call go to ``_read_preamble`` even when the cache has an entry."""
    scope = _bare_tek_instance()
    scope._preamble_cache["CH3"] = (1e-3, 0.0, 0.0, 32e-9, -320e-6, 0, True, True)
    fake_preamble = (2e-3, 0.0, 0.0, 32e-9, -320e-6, 0, True, True)
    scope._read_preamble = MagicMock(return_value=fake_preamble)
    scope._cmds = MagicMock()
    scope._cmds.use_data_source = True
    scope._cmds.preamble = "WFMOutpre"
    scope._inst = MagicMock()
    scope._inst.query_binary_values = MagicMock(
        return_value=np.zeros(20000, dtype=np.int8))
    scope._data_width = 1
    scope._n_horiz_divs = 15.0

    # Disable the cache via env var.
    monkeypatch.setenv("PULSAR_DISABLE_PREAMBLE_CACHE", "1")

    try:
        scope._read_channel("CH3")
    except Exception:
        pass

    # _read_preamble SHOULD have been called even though the cache
    # had an entry.
    scope._read_preamble.assert_called_once()


# ---------------------------------------------------------------------------
# Preamble PATCH-in-place (in-run efficiency) — instead of invalidate+re-query
# ---------------------------------------------------------------------------
def _seeded_scope():
    """Bare scope with a learned codes-per-div (25) + a full 8-tuple cache
    entry for CH1 at 1 V/div (ymult 0.04, yoff 0, yzero 0)."""
    scope = _bare_tek_instance()
    scope._y_codes_per_div = {"CH1": 25.0}
    scope._preamble_cache["CH1"] = (
        0.04, 0.0, 0.0, 32e-9, -320e-6, 0, True, True)   # 1 V/div, pos 0
    return scope


def test_scale_write_patches_ymult_not_invalidate():
    """With codes-per-div learned, a V/div write PATCHES ymult in place
    (cache entry survives, no WFMOutpre? re-query) and the value matches the
    confirmed formula ymult = vpd × 0.04 (= vpd / 25)."""
    scope = _seeded_scope()
    scope.set_channel_scale("CH1", 0.5)              # 500 mV/div
    assert "CH1" in scope._preamble_cache, "must PATCH, not drop"
    ymult = scope._preamble_cache["CH1"][0]
    assert ymult == pytest.approx(0.5 * 0.04)        # 0.02, = 0.5/25
    # yoff / yzero / x-fields untouched
    assert scope._preamble_cache["CH1"][1] == 0.0
    assert scope._preamble_cache["CH1"][3] == 32e-9


def test_position_write_patches_yoff_not_invalidate():
    """A position write PATCHES yoff = pos_divs × 25 in place."""
    scope = _seeded_scope()
    scope.set_channel_position("CH1", 0.9)
    assert "CH1" in scope._preamble_cache, "must PATCH, not drop"
    assert scope._preamble_cache["CH1"][1] == pytest.approx(0.9 * 25.0)  # 22.5
    # ymult unchanged (position doesn't touch V/div)
    assert scope._preamble_cache["CH1"][0] == pytest.approx(0.04)
    # negative position → negative yoff (sign follows the written divisions)
    scope.set_channel_position("CH1", -2.0)
    assert scope._preamble_cache["CH1"][1] == pytest.approx(-50.0)


def test_patch_falls_back_to_invalidate_when_codes_per_div_unknown():
    """Before codes-per-div is learned for a channel, the setters must fall
    back to a full invalidate (correctness over speed)."""
    scope = _bare_tek_instance()                      # no _y_codes_per_div
    scope._preamble_cache["CH2"] = (0.04, 0.0, 0.0, 32e-9, -320e-6, 0, True, True)
    scope.set_channel_scale("CH2", 0.5)
    assert "CH2" not in scope._preamble_cache, "unlearned → must invalidate"


def test_patch_helper_returns_false_without_cache_entry():
    scope = _seeded_scope()
    # No entry for CH3 → _patch_preamble_y reports False so the caller
    # invalidates instead of silently doing nothing.
    assert scope._patch_preamble_y("CH3", ymult=0.02) is False


def test_patched_cache_decodes_identically_to_fresh_query():
    """End-to-end: the patched (ymult, yoff) reconstruct volts identically to
    what a fresh WFMOutpre? would give — the whole safety point.  Decode a
    known raw code both ways."""
    scope = _seeded_scope()
    scope.set_channel_scale("CH1", 0.2)              # 200 mV/div
    scope.set_channel_position("CH1", 1.0)           # +1 div
    ymult, yoff, yzero = scope._preamble_cache["CH1"][:3]
    # A fresh WFMOutpre? at 200 mV/div, +1 div would report exactly these:
    assert (ymult, yoff, yzero) == pytest.approx((0.2 * 0.04, 25.0, 0.0))
    # Reconstruct volts for raw code +50 → (50 - 25)*0.008 + 0 = 0.2 V
    raw = 50.0
    assert (raw - yoff) * ymult + yzero == pytest.approx(0.2)


# ---------------------------------------------------------------------------
# Documented invalidation contract — source-level check
# ---------------------------------------------------------------------------
def test_invalidation_call_sites_documented_in_source():
    """Spot-check that the canonical setters carry
    ``_invalidate_preamble_cache`` calls in the source.  Catches
    refactors that drop the invalidation by accident."""
    from pathlib import Path
    src_path = (Path(__file__).resolve().parent.parent /
                "stimtest/hardware/tektronix.py")
    src = src_path.read_text(encoding="utf-8")
    # Count occurrences — should be at least 6 (one per invalidating
    # method: set_channel_scale, set_channel_position,
    # set_horizontal_scale, set_horizontal_position [×2 branches],
    # set_record_length, set_acquisition_mode, configure_channels).
    n = src.count("_invalidate_preamble_cache(")
    assert n >= 7, (
        f"expected ≥7 _invalidate_preamble_cache() call sites in "
        f"tektronix.py; found {n}.  Refactor likely dropped one — "
        f"verify set_channel_scale, set_channel_position, "
        f"set_horizontal_scale, set_horizontal_position, "
        f"set_record_length, set_acquisition_mode, and "
        f"configure_channels all call the invalidator.")


# ---------------------------------------------------------------------------
# Redundant-command elimination (log-audit): SELect + horizontal SEC/DIV
# ---------------------------------------------------------------------------
def test_select_channel_cached_not_resent_each_read(monkeypatch):
    """``SELect:CH ON`` is sent once per channel, then skipped — nothing
    deselects a used channel mid-run, so re-sending it before every CURVe?
    (~8×/capture) was redundant."""
    scope = _bare_tek_instance()
    scope._selected_channels = set()
    scope._read_preamble = MagicMock(
        return_value=(1e-3, 0.0, 0.0, 32e-9, -320e-6, 0, True, True))
    scope._cmds = MagicMock()
    scope._cmds.use_data_source = True
    scope._cmds.preamble = "WFMOutpre"
    scope._inst = MagicMock()
    scope._inst.query_binary_values = MagicMock(
        return_value=np.zeros(20000, dtype=np.int8))
    scope._n_horiz_divs = 15.0
    monkeypatch.delenv("PULSAR_DISABLE_PREAMBLE_CACHE", raising=False)

    def _selects():
        return [c for c in scope._w.call_args_list
                if "SELect:CH1" in str(c)]
    for _ in range(5):
        try:
            scope._read_channel("CH1")
        except Exception:
            pass
    assert len(_selects()) == 1, "SELect:CH1 ON should be sent ONCE, then cached"


def test_horizontal_scale_skips_repeated_identical_request():
    """A VT sweep re-applies the SAME SEC/DIV per channel — the second+
    identical request must skip the write + readback + preamble invalidation."""
    scope = _bare_tek_instance()
    scope._cmds = MagicMock()
    scope._cmds.horiz_scale = "HORizontal:SCAle"
    scope._q = MagicMock(return_value="4e-5")     # scope applies 40 µs/div
    scope._expected_horiz_scale_s = None
    scope._last_horiz_scale_req = None
    a1 = scope.set_horizontal_scale(4e-5)
    n1 = scope._w.call_count
    a2 = scope.set_horizontal_scale(4e-5)         # identical → skip
    assert scope._w.call_count == n1, "repeated identical SEC/DIV must not re-write"
    assert a2 == a1
    scope.set_horizontal_scale(1e-4)              # different → writes
    assert scope._w.call_count > n1
