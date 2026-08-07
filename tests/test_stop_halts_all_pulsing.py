"""Stop AND Pause must halt all pulsing — and nothing may restart it.

Operator: "when stopping an experiment, all pulsing must stop … that includes
pausing."

Two independent holes existed:

1. ``pause_toggled`` only set a FLAG on the runner.  The device kept pulsing
   until the worker reached its next ``wait_if_paused`` checkpoint, which sits
   between captures — with a slow averager settle that is SECONDS of
   stimulation into an electrode the operator believes is quiet.  Stop already
   called ``abort_all()`` immediately; Pause now does too.

2. Even with the device halted, the worker could bring pulsing back up: 7 of
   the 9 ``start_all()`` sites had no abort/pause guard, so the next amplitude
   step would simply start stimulating again.  Every site now goes through
   ``ExperimentRunner.start_pulsing()``, which refuses while aborted or paused.
"""
from __future__ import annotations

import pathlib

import pytest


class _Stim:
    def __init__(self):
        self.started = 0

    def start_all(self):
        self.started += 1


class _Runner:
    """Minimal stand-in exercising the real gate."""

    def __init__(self):
        from stimtest.experiments.base import ExperimentRunner
        self.stim = _Stim()
        self._abort_requested = False
        self._pause_requested = False
        self.logs = []
        self._log = self.logs.append
        self.start_pulsing = ExperimentRunner.start_pulsing.__get__(self)


def test_starts_when_running():
    r = _Runner()
    assert r.start_pulsing() is True
    assert r.stim.started == 1


def test_refuses_while_aborted():
    r = _Runner()
    r._abort_requested = True
    assert r.start_pulsing() is False
    assert r.stim.started == 0, "pulsing restarted after Stop"


def test_refuses_while_paused():
    r = _Runner()
    r._pause_requested = True
    assert r.start_pulsing() is False
    assert r.stim.started == 0, "pulsing restarted while paused"


def test_resume_can_start_again():
    """The gate must not wedge the run: clearing the pause re-enables it,
    which is what ``wait_if_paused``'s restart callback relies on."""
    r = _Runner()
    r._pause_requested = True
    assert r.start_pulsing() is False
    r._pause_requested = False
    assert r.start_pulsing() is True
    assert r.stim.started == 1


def test_suppression_is_logged():
    r = _Runner()
    r._abort_requested = True
    r.start_pulsing()
    assert any("SUPPRESSED" in m for m in r.logs)


def _src(rel):
    root = pathlib.Path(__file__).resolve().parent.parent
    return (root / rel).read_text(encoding="utf-8")


RUNNERS = ("voltage_transient", "short_pulsing", "progressive_stress",
           "long_pulsing", "galvanostatic_eis")


@pytest.mark.parametrize("mod", RUNNERS)
def test_no_runner_bypasses_the_gate(mod):
    """A direct ``self.stim.start_all()`` in a runner would reopen the hole."""
    src = _src(f"stimtest/experiments/{mod}.py")
    code = "\n".join(l for l in src.splitlines()
                     if not l.strip().startswith("#"))
    assert "self.stim.start_all()" not in code, (
        f"{mod} starts pulsing without the abort/pause gate")


def test_every_runner_actually_uses_the_gate():
    total = sum(_src(f"stimtest/experiments/{m}.py").count("self.start_pulsing()")
                for m in RUNNERS)
    assert total >= 8, total


def test_pause_stops_the_device_immediately():
    """Not just the flag — the GUI must halt the device on the spot."""
    src = _src("stimtest/gui/experiment_tabs.py")
    i = src.index("def pause_toggled")
    body = src[i:i + 3000]
    assert "stop_all_forced" in body, (
        "pause_toggled leaves the stimulator pulsing until the worker's next "
        "checkpoint")


def test_pause_stops_rather_than_aborts():
    """Operator: "pause should not abort, it should stop the pulsing so that
    resuming will start the pulsing."

    ``stop_all`` is the halt ``start_all`` cleanly reverses — the PlexStim
    retains each channel's pattern/period/repetitions across it, so resume
    needs no reload.  ``abort_all`` is for ENDING a run; using it for Pause
    would make a suspend into a tear-down.
    """
    src = _src("stimtest/gui/experiment_tabs.py")
    i = src.index("def pause_toggled")
    body = src[i:i + 3000]
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    assert "abort_all()" not in code, "Pause aborts — it must stop instead"


def test_the_runner_pause_checkpoint_also_stops_not_aborts():
    """The GUI and the worker must agree on what a pause does."""
    src = _src("stimtest/experiments/base.py")
    i = src.index("def wait_if_paused")
    body = src[i:i + 3000]
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    assert "stop_all_forced" in code
    assert "abort_all()" not in code


def test_stop_halts_the_program_not_just_the_pulse():
    """Operator: "stopping/aborting did not stop the pulsing."

    ``abort_all`` (PS_AbortAll) kills only the pulse IN FLIGHT.  Runners arm
    channels with ``set_repetitions(ch, 0)`` = INFINITE repetitions, so the
    channel program keeps free-running and the train continues with the next
    pulse.  ``stop_all`` (PS_StopStimAllChannels) is what halts the program.
    Stop must do BOTH, program first.
    """
    src = _src("stimtest/gui/experiment_tabs.py")
    i = src.index("def stop_clicked")
    body = src[i:i + 5200]
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    assert "stop_all_forced" in code, "Stop never halts the channel program"
    assert "abort_all()" in code, "Stop never aborts the in-flight pulse"
    assert code.index("stop_all_forced") < code.index("abort_all()"), \
        "stop_all must come FIRST so no further pulses are scheduled"


def test_stop_closes_the_stimulator_and_start_reinitializes():
    """Operator lifecycle: "Pressing Stop should: PS_StopStimAllChannels,
    PS_AbortAll, PS_CloseAllStim" and "Pressing Start should do PS_InitAllStim".

    The CLOSE is deferred to ``_on_finished`` — it runs after
    ``_worker_thread.wait()``, so the runner thread is provably dead and its
    last DLL call cannot race PS_CloseAllStim (the cascade that heap-corrupts
    the vendor DLL, gotcha #29c)."""
    src = _src("stimtest/gui/experiment_tabs.py")
    stop = src[src.index("def stop_clicked"):][:4000]
    assert "_stim_needs_close_after_run = True" in stop, (
        "Stop does not request the PS_CloseAllStim step")
    fin = src[src.index("def _on_finished"):][:6000]
    assert "_worker_thread.wait()" in fin
    assert "_stim.close()" in fin, "_on_finished never issues PS_CloseAllStim"
    assert fin.index("_worker_thread.wait()") < fin.index("_stim.close()"), (
        "the close must come AFTER the worker thread is joined")


def test_force_stop_bypasses_the_idempotence_guard():
    """``stop_all``'s ``_is_running`` short-circuit is a sweep optimisation.
    For an operator Stop / Pause a redundant DLL call costs nothing, while
    skipping a real stop because the flag went stale leaves the electrode
    pulsing after the user pressed Stop."""
    from stimtest.hardware.plexon import PlexonStimulator
    import inspect
    sig = inspect.signature(PlexonStimulator.stop_all)
    assert "force" in sig.parameters, "stop_all has no force override"
    body = inspect.getsource(PlexonStimulator.stop_all)
    assert "not self._is_running and not force" in body
    # A refused stop must ESCALATE to PS_AbortAll, not vanish into a caller's
    # try/except (PS_StopStimAllChannels returns 4 outside PS_TRIG_SOFT).
    assert "ps_abort_all" in body, "a failed stop does not escalate to abort"
