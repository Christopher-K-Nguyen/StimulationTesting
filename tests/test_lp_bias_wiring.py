"""Tests for Task #43 part 4: LP runner closed-loop wiring.

Mirrors test_sp_bias_wiring.py / test_ps_bias_wiring.py source-level
ordering checks, with one extra wrinkle: LP runs a sub-VT inside
``_characterize`` for periodic drift tracking, and that sub-runner
clobbers scope state.  The bias controller must therefore disarm
BEFORE the sub-VT and re-arm AFTER the post-recharacterization
scope-view restore.

Pinned source-level invariants:

* arm_bias_feedback() lands AFTER apply_default_scope_view at run
  start, BEFORE the main pulsing loop.
* disarm_bias_feedback() lands BEFORE the ``_characterize`` call
  in the periodic-recharacterization branch.
* arm_bias_feedback() lands AFTER the ``post-recharacterization
  restore`` apply_default_scope_view call (so the gating SCPI writes
  override the post-restore state).
* bias_step_if_armed() lands AFTER the snapshot capture event emit.
* disarm_bias_feedback() lands in the OUTER finally BEFORE the
  safety-net stop_all.
"""
from __future__ import annotations

from pathlib import Path


def _read_lp_source() -> str:
    return (Path(__file__).resolve().parent.parent
            / "stimtest/experiments/long_pulsing.py").read_text(
                encoding="utf-8")


def test_lp_arms_after_scope_view_before_main_loop():
    """arm at run start lands between apply_default_scope_view and
    the main pulsing ``while`` loop."""
    src = _read_lp_source()
    assert "apply_default_scope_view" in src
    assert "arm_bias_feedback" in src
    asv_pos = src.index("apply_default_scope_view")
    arm_pos = src.index("self.arm_bias_feedback(")
    # Main pulsing loop header (precise-sampling refactor: the elapsed
    # check moved inside the loop, so the header is now the bare
    # ``while not self.aborted:``).
    main_while_pos = src.index("while not self.aborted:")
    assert asv_pos < arm_pos < main_while_pos


def test_lp_disarms_before_characterize_call():
    """The bias controller must be disarmed BEFORE
    ``self._characterize`` runs — the sub-VT inside _characterize
    calls apply_default_scope_view which would clobber the gating
    window otherwise."""
    src = _read_lp_source()
    # Find the per-iteration ``_characterize`` invocation (the actual
    # method def itself starts with ``def _characterize``; the call
    # site uses ``self._characterize(``).
    char_call_pos = src.index("self._characterize(")
    # Find the disarm before it (the WINDOW from start to char_call).
    # We want the LAST disarm BEFORE the call.
    disarm_before = src.rfind("self.disarm_bias_feedback(", 0, char_call_pos)
    assert disarm_before != -1, (
        "expected a disarm_bias_feedback() call somewhere before "
        "self._characterize(")
    # And the disarm should be reasonably close to the char call
    # (not way at the top of run()) — the same iteration's branch.
    # Allow ~1500 chars of intervening comment / handler code.
    assert (char_call_pos - disarm_before) < 1500


def test_lp_rearms_after_post_recharacterization_restore():
    """After the post-recharacterization ``apply_default_scope_view``
    restore, the controller must re-arm so the gating SCPI writes
    take precedence over the post-restore state."""
    src = _read_lp_source()
    # Find the post-recharacterization restore marker.
    restore_pos = src.index("post-recharacterization restore")
    # And the first arm AFTER that marker.
    arm_after_restore = src.find("self.arm_bias_feedback(", restore_pos)
    assert arm_after_restore != -1, (
        "expected a re-arm after the post-recharacterization restore")
    # Also: there must be EXACTLY 2 self.arm_bias_feedback( calls in
    # the entire LP source — one at run start and one after the
    # restore.  Catches accidental triple-arms / missed re-arms.
    assert src.count("self.arm_bias_feedback(") == 2


def test_lp_calls_bias_step_after_snapshot_capture():
    """bias_step_if_armed should follow the snapshot capture event so the
    GUI's status badge update lands AFTER the just-emitted metrics row.

    The per-channel snapshot capture+emit now lives in the
    ``_capture_and_emit_snapshot`` helper (called once per monitored channel —
    multichannel monopolar plots each channel, CLAUDE.md gotcha #105); the
    bias step fires ONCE per snapshot round AFTER that channel loop, so the
    runtime order (capture emit → bias step) is preserved even though the
    ``kind="capture"`` string now sits in the helper defined below run()."""
    src = _read_lp_source()
    assert "bias_step_if_armed" in src
    # In run()'s snapshot round, the capture-emitting helper is CALLED before
    # the bias step (the call precedes the helper's own def in the source).
    snap_call = src.index("self._capture_and_emit_snapshot(")
    bias_step = src.index("self.bias_step_if_armed(")
    assert snap_call < bias_step
    # …and the helper actually emits the capture event.
    helper_def = src.index("def _capture_and_emit_snapshot")
    assert src.index('kind="capture"', helper_def) > helper_def


def test_lp_disarms_in_outer_finally_before_stop_all():
    """disarm_bias_feedback should land in the outer finally
    (the one with the ``= PS_StopStimAllChannels`` safety-net
    comment) BEFORE the stop_all — mirrors arm order."""
    src = _read_lp_source()
    # Find the outer finally (the one with the matching MATLAB
    # comment).  There are multiple ``finally:`` lines (e.g. inside
    # the try around stim.load), so we anchor on the safety-net text.
    sentinel = "PS_StopStimAllChannels"
    sentinel_pos = src.index(sentinel)
    finally_pos = src.rfind("finally:", 0, sentinel_pos)
    block = src[finally_pos:]
    assert "disarm_bias_feedback" in block
    disarm_pos = block.index("disarm_bias_feedback")
    stop_all_pos = block.index("stop_all")
    assert disarm_pos < stop_all_pos


def test_lp_disarms_at_least_twice_total():
    """There should be at least 2 disarm calls in LP: one before
    each ``_characterize`` call, plus one in the outer finally.
    (More is fine — defensive disarms in error branches are
    welcome.)"""
    src = _read_lp_source()
    assert src.count("self.disarm_bias_feedback(") >= 2
