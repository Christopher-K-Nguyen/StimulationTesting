"""Regression test for Task #58: KeyError 'history' in VT rescale loop.

Bug discovered in LOG_ANALYSIS.md baseline (2026-05-31): every VT
rescale-loop attempt was failing with ``KeyError: 'history'`` because
``set_channel_scale`` seeded ``_adapt_state[ch]`` with a PARTIAL
schema (only ``shrink_count`` + ``last_scale``).  When
``adapt_channel_scale`` ran later, its ``setdefault`` saw the key
already present and was a no-op; the next line
``history = st["history"]`` raised KeyError.

Pattern at fault:

    # set_channel_scale (pre-fix)
    self._adapt_state.setdefault(
        channel, {"shrink_count": 0, "last_scale": None}
    )["last_scale"] = ...

    # adapt_channel_scale (later in the call sequence)
    st = self._adapt_state.setdefault(
        channel, {"shrink_count": 0, "last_scale": None,
                  "history": [], "settled_count": 0, "locked": False})
    history = st["history"]   # KeyError — partial dict, no 'history'

This test reproduces the sequence + asserts the KeyError no longer
fires.  Also tests:

* Both methods produce the same dict shape (the canonical
  ``_new_adapt_state`` schema)
* A defensive back-fill in ``adapt_channel_scale`` handles
  pre-existing partial dicts from any future divergent code path

Closes Task #58.
"""
from __future__ import annotations

import pytest


def _bare_tek_instance():
    """Construct a TektronixOscilloscope without opening hardware.

    We bypass ``__init__`` so we don't need pyvisa or a real scope —
    just the _adapt_state dict + the relevant methods.  Stub out
    ``_w`` (SCPI write) and ``_q`` (SCPI query) so set_channel_scale
    and adapt_channel_scale don't try to hit a wire.
    """
    from stimtest.hardware.tektronix import TektronixOscilloscope

    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    # Stub I/O.
    scope._w = lambda cmd: None
    scope._q = lambda cmd: "0.2"  # fake "current scale = 200 mV/div"
    scope._log = lambda msg: None
    # Stub state — ABC subclasses normally get these in __init__.
    scope._adapt_state = {}
    # _ADAPT_* constants don't carry past the staticmethod helper;
    # they live on the class so we don't need to set them.
    return scope


# ---------------------------------------------------------------------------
# The canonical default schema is a single source of truth
# ---------------------------------------------------------------------------
def test_new_adapt_state_has_all_required_keys():
    """The default schema must contain every key both
    ``set_channel_scale`` and ``adapt_channel_scale`` read."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    default = TektronixOscilloscope._new_adapt_state()
    required = {"shrink_count", "last_scale", "history",
                "settled_count", "locked"}
    missing = required - set(default.keys())
    assert not missing, (
        f"_new_adapt_state missing required keys: {missing}.  "
        f"Both set_channel_scale and adapt_channel_scale read these; "
        f"a missing key triggers KeyError mid-rescale — see audit "
        f"Task #58.")
    # And the default values are sane starting points.
    assert default["shrink_count"] == 0
    assert default["last_scale"] is None
    assert default["history"] == []
    assert default["settled_count"] == 0
    assert default["locked"] is False


# ---------------------------------------------------------------------------
# The bug: set_channel_scale-then-adapt sequence no longer KeyErrors
# ---------------------------------------------------------------------------
def test_set_then_adapt_does_not_raise_keyerror_history():
    """The exact sequence that produced the bug: call
    ``set_channel_scale`` first (which seeds the cache), then call
    ``adapt_channel_scale`` (which used to fail because the seeded
    dict was missing ``history``)."""
    scope = _bare_tek_instance()

    # Step 1: set_channel_scale seeds _adapt_state[CH1].
    scope.set_channel_scale("CH1", 0.5)
    assert "CH1" in scope._adapt_state
    # Pre-fix this would have only had {shrink_count, last_scale}.
    # Post-fix the seeded dict has the full schema.
    seeded = scope._adapt_state["CH1"]
    for key in ("shrink_count", "last_scale", "history",
                "settled_count", "locked"):
        assert key in seeded, (
            f"set_channel_scale must seed _adapt_state[ch] with "
            f"the FULL schema (missing {key!r}).  Audit Task #58.")
    assert seeded["last_scale"] == pytest.approx(0.5)

    # Step 2: adapt_channel_scale used to KeyError here.  Should now
    # complete without raising.
    try:
        scope.adapt_channel_scale(
            "CH1", v_min=-0.44, v_max=0.28, divs=3.0)
    except KeyError as e:
        pytest.fail(
            f"adapt_channel_scale raised KeyError {e!r} after "
            f"set_channel_scale seeded the state — the pre-fix bug "
            f"is back!  Check that set_channel_scale and "
            f"adapt_channel_scale use the same _new_adapt_state() "
            f"default.")


# ---------------------------------------------------------------------------
# Defensive back-fill in adapt_channel_scale handles legacy partial dicts
# ---------------------------------------------------------------------------
def test_adapt_backfills_partial_state_from_any_source():
    """Even if some future code path leaves a partial dict in
    _adapt_state, ``adapt_channel_scale`` should back-fill the
    missing keys rather than KeyError.  Belt-and-braces guard
    against divergent setdefaults reappearing."""
    scope = _bare_tek_instance()

    # Simulate a legacy / divergent code path that left a partial dict.
    scope._adapt_state["CH2"] = {"shrink_count": 0, "last_scale": 0.1}

    # Should not raise.
    try:
        scope.adapt_channel_scale(
            "CH2", v_min=-0.10, v_max=0.10, divs=3.0)
    except KeyError as e:
        pytest.fail(
            f"adapt_channel_scale should defensively back-fill "
            f"missing keys on a partial _adapt_state[ch] dict, "
            f"not raise KeyError ({e!r}).  See audit Task #58.")

    # After the call, the dict should have the full schema.
    st = scope._adapt_state["CH2"]
    for key in ("shrink_count", "last_scale", "history",
                "settled_count", "locked"):
        assert key in st, (
            f"after adapt_channel_scale, _adapt_state[ch] must "
            f"have the full schema (missing {key!r})")


# ---------------------------------------------------------------------------
# Adapt-first sequence still works (the non-buggy path)
# ---------------------------------------------------------------------------
def test_adapt_first_then_set_still_works():
    """Reverse order — adapt first, then set_channel_scale — was
    the non-buggy path pre-fix.  Verify it still works post-fix."""
    scope = _bare_tek_instance()

    scope.adapt_channel_scale(
        "CH3", v_min=-0.04, v_max=0.04, divs=4.0)
    assert "CH3" in scope._adapt_state
    assert "history" in scope._adapt_state["CH3"]

    scope.set_channel_scale("CH3", 0.05)
    # set_channel_scale should update last_scale without dropping
    # the history.
    assert scope._adapt_state["CH3"]["last_scale"] == pytest.approx(0.05)
    assert "history" in scope._adapt_state["CH3"]
