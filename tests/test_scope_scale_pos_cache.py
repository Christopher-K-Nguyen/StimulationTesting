"""Opt #3: the in-view / clip / screen-window checks read the confirmed-
value (V/div, POSition) caches instead of querying the scope every time.

The rescale loop calls ``channel_in_view`` / ``channel_is_clipped`` /
``_channel_screen_window_v`` ~45×/capture; on the operator's 16-channel run
that was 2693 ``CHx:SCAle?`` / ``CHx:POSition?`` round-trips (~42 s).  Since
every vertical scale/position WRITE goes through ``set_channel_scale`` /
``set_channel_position`` (which now record ``last_scale`` / ``last_pos``),
the checks can trust the cache and skip the queries — falling back to a live
query only when the cache is cold (first use, or a scope without the cache).
"""
from __future__ import annotations


def _bare_scope():
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    scope._adapt_state = {}
    scope._half_vert_divs = 5.0
    scope._invalidate_preamble_cache = lambda ch: None
    scope._queries = []

    def _q(cmd):
        scope._queries.append(cmd)
        if "SCAle?" in cmd:
            return "0.1"
        if "POSition?" in cmd:
            return "0"
        return "0"

    scope._q = _q
    scope._w = lambda cmd: None
    return scope


def _vert_queries(scope):
    return [q for q in scope._queries
            if "SCAle?" in q or "POSition?" in q]


def test_set_channel_position_records_last_pos():
    scope = _bare_scope()
    scope.set_channel_position("CH1", 1.5)
    assert scope._adapt_state["CH1"]["last_pos"] == 1.5


def test_in_view_uses_cache_after_writes():
    scope = _bare_scope()
    scope.set_channel_scale("CH1", 0.1)     # caches last_scale
    scope.set_channel_position("CH1", 1.0)  # caches last_pos
    scope._queries.clear()
    r = scope.channel_in_view("CH1", -0.2, 0.2, margin_divs=4.95)
    assert r is not None
    assert _vert_queries(scope) == [], (
        "in-view should be served entirely from the V/div + POSition cache")


def test_clip_uses_cache_after_writes():
    scope = _bare_scope()
    scope.set_channel_scale("CH1", 0.1)
    scope.set_channel_position("CH1", 0.0)
    scope._queries.clear()
    r = scope.channel_is_clipped("CH1", -0.2, 0.2)
    assert r is not None
    assert _vert_queries(scope) == []


def test_screen_window_uses_cache_after_writes():
    scope = _bare_scope()
    scope.set_channel_scale("CH1", 0.1)
    scope.set_channel_position("CH1", 0.0)
    scope._queries.clear()
    win = scope._channel_screen_window_v("CH1")
    assert win is not None
    assert _vert_queries(scope) == []


def test_falls_back_to_query_when_cache_cold():
    scope = _bare_scope()          # no writes → cache cold
    r = scope.channel_in_view("CH1", -0.2, 0.2, margin_divs=4.95)
    assert r is not None
    qs = _vert_queries(scope)
    assert any("SCAle?" in q for q in qs)
    assert any("POSition?" in q for q in qs)


def test_cached_scale_pos_returns_written_values():
    scope = _bare_scope()
    scope.set_channel_scale("CH2", 0.05)
    scope.set_channel_position("CH2", -2.0)
    scope._queries.clear()
    vpd, pos = scope._cached_scale_pos("CH2")
    assert vpd == 0.05
    assert pos == -2.0
    assert _vert_queries(scope) == []
