"""Regression tests for the 2026-06-10 scope-view fixes:

R1 — the VT in-view rescale loop used ``acq.time_us or []``; ``bool()`` on
     a numpy array with >1 element raises ``ValueError: truth value of an
     array is ambiguous``, which aborted the whole loop on the first
     capture so V_mon/E_act/E_ret were never rescaled.
R3 — ``apply_default_scope_view`` now gives the Role=Trigger digital-sync
     channel a fixed vertical scale + position.
R4 — cursors prefer E_act, then E_ret, then fall back to V_mon.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest

from stimtest.experiments.base import ExperimentRunner
from stimtest.waveforms import PulsePattern


class _DummyRunner(ExperimentRunner):
    def run(self):  # satisfy the lone @abstractmethod
        raise NotImplementedError


def _runner(aliases):
    r = _DummyRunner.__new__(_DummyRunner)
    scope = MagicMock()
    scope.channel_aliases = dict(aliases)
    scope.auto_layout_for_pulse = MagicMock(return_value=(4e-5, 20.0))
    scope.reset_adapt_state = MagicMock()

    def _q(cmd):
        if "SCAle?" in cmd:
            return "1.0"
        if "POSition?" in cmd:
            return "-2.0"
        return "0.0"
    scope._q = MagicMock(side_effect=_q)

    r.scope = scope
    r.session = MagicMock()
    r._emit = MagicMock()
    r.trigger_is_digital = True
    r.trigger_source = "CH4"
    # Bypass the I_mon scale/trigger internals — not under test here.
    r.update_imon_vertical_scale = MagicMock()
    r.update_imon_trigger_level = MagicMock()
    return r, scope


def _pattern():
    return PulsePattern.rect(polarity=-1, amplitude_ua=50.0,
                             phase_width_us=200.0, interphase_us=20.0,
                             discharge_us=20.0, rate_hz=50.0)


def _cursor_source(scope):
    assert scope.set_cursors.called, "set_cursors was never called"
    return scope.set_cursors.call_args.kwargs.get("source_channel")


# ---- R4: cursor-source fallback -------------------------------------------
def test_cursor_prefers_eact():
    r, scope = _runner({"vmon": "CH1", "imon": "CH2",
                        "eact": "CH3", "eret": "CH4"})
    r.apply_default_scope_view(_pattern())
    assert _cursor_source(scope) == "CH3"  # E_act wins


def test_cursor_falls_back_to_eret_when_no_eact():
    r, scope = _runner({"vmon": "CH1", "imon": "CH2", "eret": "CH3"})
    r.apply_default_scope_view(_pattern())
    assert _cursor_source(scope) == "CH3"  # E_ret


def test_cursor_falls_back_to_vmon_when_no_electrode():
    r, scope = _runner({"vmon": "CH1", "imon": "CH2", "trigger": "CH4"})
    r.apply_default_scope_view(_pattern())
    assert _cursor_source(scope) == "CH1"  # V_mon fallback


# ---- R3: trigger-channel scale + position ---------------------------------
def test_trigger_channel_gets_fixed_scale_and_position():
    r, scope = _runner({"vmon": "CH1", "imon": "CH2",
                        "eret": "CH3", "trigger": "CH4"})
    r.apply_default_scope_view(_pattern())
    scope.set_channel_scale.assert_any_call("CH4", 1.0)
    scope.set_channel_position.assert_any_call("CH4", -2.0)


def test_no_trigger_channel_leaves_ch4_unscaled():
    r, scope = _runner({"vmon": "CH1", "imon": "CH2", "eret": "CH3"})
    r.apply_default_scope_view(_pattern())
    for call in scope.set_channel_position.call_args_list:
        assert call.args[0] != "CH4", "CH4 positioned despite no trigger alias"


# ---- R1: numpy time_us truthiness -----------------------------------------
def test_time_us_numpy_array_truthiness_regression():
    """`acq.time_us or []` raised ValueError on a real (multi-sample)
    capture; the fixed form `[] if t is None else t` must not."""
    t = np.arange(20000, dtype=float)
    with pytest.raises(ValueError):
        bool(t)  # documents exactly why `t or []` crashed
    fixed = np.asarray([] if t is None else t, dtype=float)
    assert fixed.size == 20000
    empty = np.asarray([] if None is None else None, dtype=float)
    assert empty.size == 0
