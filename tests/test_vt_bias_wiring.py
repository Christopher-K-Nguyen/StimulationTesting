"""Tests for Task #43 part 5: VT runner closed-loop wiring.

Final runner in the bias track.  VT is the most involved because of
per-configuration iteration: each config in ``self.configurations``
gets its own ``_run_one_configuration`` call, and each invocation
runs its own ``apply_default_scope_view`` (which clears the gating
window).  So the controller is armed + disarmed PER configuration,
not once per run.

Pinned source-level invariants:

* arm_bias_feedback() inside _run_one_configuration, AFTER
  apply_default_scope_view and BEFORE the amplitude ``while``
  loop.
* bias_step_if_armed() AFTER each ``_one_capture`` capture event
  emit (inside the amplitude ramp loop).
* disarm_bias_feedback() in a finally that wraps the amplitude
  loop — so a mid-sweep exception / abort still disarms before
  the next config (or the outer stop_all) runs.
* disarm fires BEFORE the ``stop_all`` so the order mirrors the
  arm direction (arm AFTER scope view, disarm BEFORE stim stop).
"""
from __future__ import annotations

from pathlib import Path


def _read_vt_source() -> str:
    return (Path(__file__).resolve().parent.parent
            / "stimtest/experiments/voltage_transient.py").read_text(
                encoding="utf-8")


def test_vt_arms_in_run_one_configuration_after_scope_view():
    """arm_bias_feedback lands AFTER apply_default_scope_view inside
    _run_one_configuration (NOT in the outer run() iteration loop,
    where it would be wrong-side of the per-config scope reset)."""
    src = _read_vt_source()
    # Find the _run_one_configuration body.
    method_pos = src.index("def _run_one_configuration")
    body_end = src.find("\n    def ", method_pos + 1)
    body = src[method_pos:body_end if body_end != -1 else len(src)]
    assert "apply_default_scope_view" in body
    assert "self.arm_bias_feedback(" in body
    asv = body.index("apply_default_scope_view")
    arm = body.index("self.arm_bias_feedback(")
    assert asv < arm, (
        "arm_bias_feedback must come AFTER apply_default_scope_view "
        "inside _run_one_configuration; the per-config scope reset "
        "would otherwise clobber the gating window the controller "
        "depends on.")


def test_vt_arms_before_amplitude_while_loop():
    """Arm should land BEFORE the amplitude ramp's ``while`` loop —
    not inside the loop (where it would re-arm every amplitude step
    for no benefit) and not after the loop (too late)."""
    src = _read_vt_source()
    method_pos = src.index("def _run_one_configuration")
    body_end = src.find("\n    def ", method_pos + 1)
    body = src[method_pos:body_end if body_end != -1 else len(src)]
    arm = body.index("self.arm_bias_feedback(")
    # The amplitude ramp's while loop has a distinctive condition.
    ramp_while = body.index("while amp <= self.ramp.max_ua")
    assert arm < ramp_while


def test_vt_calls_bias_step_after_capture_emit():
    """bias_step_if_armed should land AFTER the per-amplitude capture
    event so the GUI status badge update lands alongside the just-emitted
    metrics row.

    The per-capture sequence (append, dose, limit flags, emit, bias step)
    was factored into the shared ``_capture_and_flag`` helper so the main
    ramp loop AND the bidirectional back-off search treat a capture
    identically — so the ordering now lives in that helper.
    """
    src = _read_vt_source()
    method_pos = src.index("def _capture_and_flag")
    body_end = src.find("\n    def ", method_pos + 1)
    body = src[method_pos:body_end if body_end != -1 else len(src)]
    assert "self.bias_step_if_armed()" in body
    capture_emit = body.index('kind="capture"')
    bias_step = body.index("self.bias_step_if_armed()")
    assert capture_emit < bias_step


def test_vt_disarms_in_finally_around_amplitude_loop():
    """disarm_bias_feedback must live in a finally that wraps the
    amplitude ``while`` loop — a mid-sweep exception or abort
    needs to leave the controller properly disarmed before the
    next config's arm or the outer-level stop_all runs."""
    src = _read_vt_source()
    method_pos = src.index("def _run_one_configuration")
    body_end = src.find("\n    def ", method_pos + 1)
    body = src[method_pos:body_end if body_end != -1 else len(src)]
    # Find the disarm.  There should be exactly one in this method.
    assert body.count("self.disarm_bias_feedback(") == 1
    disarm_pos = body.index("self.disarm_bias_feedback(")
    # And the closest preceding ``finally:`` should be the one that
    # wraps the amplitude loop (and the amplitude loop should land
    # BEFORE the disarm).
    ramp_while = body.index("while amp <= self.ramp.max_ua")
    finally_pos = body.rfind("finally:", 0, disarm_pos)
    assert finally_pos != -1
    assert ramp_while < finally_pos, (
        "the finally that owns disarm must come AFTER the amplitude "
        "while loop (i.e. wraps it)")


def test_vt_disarms_before_stop_all():
    """The disarm should run BEFORE the per-config stop_all — the
    bias controller's scope-gating teardown should land while the
    stim is still running (arm direction mirrors)."""
    src = _read_vt_source()
    method_pos = src.index("def _run_one_configuration")
    body_end = src.find("\n    def ", method_pos + 1)
    body = src[method_pos:body_end if body_end != -1 else len(src)]
    disarm_pos = body.index("self.disarm_bias_feedback(")
    # The per-config stop_all in _run_one_configuration is the one
    # we want — NOT any stop_all inside _one_capture (which is in a
    # different method anyway).  Find the FIRST stop_all after the
    # disarm.
    stop_all_pos = body.find("self.stim.stop_all()", disarm_pos)
    assert stop_all_pos != -1, (
        "_run_one_configuration should have a per-config stop_all "
        "after the disarm")
    assert disarm_pos < stop_all_pos


def test_vt_arms_exactly_once_per_run_one_configuration():
    """Catch accidental double-arms.  Each call to
    _run_one_configuration should fire arm exactly once — re-arming
    per amplitude step would re-issue the gating SCPI writes
    (~5 ms each) per iteration for no benefit."""
    src = _read_vt_source()
    method_pos = src.index("def _run_one_configuration")
    body_end = src.find("\n    def ", method_pos + 1)
    body = src[method_pos:body_end if body_end != -1 else len(src)]
    assert body.count("self.arm_bias_feedback(") == 1
