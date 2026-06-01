"""Tests for Task #43 part 3: PS runner closed-loop wiring.

Mirrors test_sp_bias_wiring.py's source-level ordering checks for
ProgressiveStress.  PS has more structural complexity than SP (a
staircase amplitude ramp, with multiple captures per amplitude step),
so the natural call-site for the bias hooks differs:

* arm_bias_feedback() ONCE, after the initial apply_default_scope_view,
  BEFORE the ramp loop.  Scope gating window is stable across ramp
  steps so re-arming per step would be wasted work.
* bias_step_if_armed() inside the inner sampling loop, after every
  capture event.  Same cadence as the existing capture cadence.
* disarm_bias_feedback() in the OUTER finally (the one that fires
  even on early break / exception), BEFORE the safety-net stop_all.

The GUI plumbing in _BaseExperimentTab._attach_bias_controller_to_runner
is already tested by test_sp_bias_wiring.py — it applies to every
runner type (SP / PS / LP / VT) the same way once their runner-side
hooks are wired.  This file only covers the PS source-level
ordering checks.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _read_ps_source() -> str:
    return (Path(__file__).resolve().parent.parent
            / "stimtest/experiments/progressive_stress.py").read_text(
                encoding="utf-8")


def test_ps_arms_once_after_scope_view_before_ramp_loop():
    """arm_bias_feedback should land:
    * AFTER apply_default_scope_view (so the controller's
      MEASUrement-gating SCPI writes aren't clobbered by the
      scope-view defaults).
    * BEFORE the ramp's ``while`` loop (so it's not called per
      amplitude step — the scope state is stable across steps)."""
    src = _read_ps_source()
    assert "apply_default_scope_view" in src
    assert "arm_bias_feedback" in src
    asv_pos = src.index("apply_default_scope_view")
    arm_pos = src.index("arm_bias_feedback")
    # Find the ramp while loop start.
    # Pattern: ``while not self.aborted and amp <= self.policy.max_ua``
    ramp_while_pos = src.index("while not self.aborted and amp")
    assert asv_pos < arm_pos < ramp_while_pos


def test_ps_arms_exactly_once():
    """Re-arming per amplitude step would re-issue the gating-window
    SCPI writes (~5 ms each on TBS2000), wasting time + churning the
    scope state.  Source should have exactly one arm call.

    Counts ``self.arm_bias_feedback(`` to disambiguate from the
    superstring ``self.disarm_bias_feedback(`` (which also contains
    ``arm_bias_feedback`` as a substring)."""
    src = _read_ps_source()
    assert src.count("self.arm_bias_feedback(") == 1


def test_ps_calls_bias_step_after_capture_emit():
    """bias_step_if_armed should follow the inner-loop capture
    event so the GUI's status badge update lands in the log AFTER
    the just-emitted metrics row."""
    src = _read_ps_source()
    assert "bias_step_if_armed" in src
    capture_emit = src.index('kind="capture"')
    bias_step = src.index("bias_step_if_armed")
    assert capture_emit < bias_step


def test_ps_disarms_in_outer_finally_before_stop_all():
    """disarm_bias_feedback should land in the OUTER finally
    (the one with the ``Outer safety net`` comment) BEFORE the
    ``stop_all`` safety net — mirrors arm order (arm AFTER scope
    view, disarm BEFORE stim stop)."""
    src = _read_ps_source()
    # Outer finally has the distinctive "Outer safety net" comment.
    assert "Outer safety net" in src
    # Find the outer finally block.
    outer_finally_pos = src.index("Outer safety net")
    # Walk backward to the ``finally:`` keyword that owns it.
    finally_pos = src.rfind("finally:", 0, outer_finally_pos)
    block = src[finally_pos:]
    assert "disarm_bias_feedback" in block
    disarm_pos = block.index("disarm_bias_feedback")
    stop_all_pos = block.index("stop_all")
    assert disarm_pos < stop_all_pos
