"""Continuous Pulsing (CP) experiment.

Operator-controlled pulsing: load the pattern once, start the stim, and
pulse **until the operator presses Stop** — no fixed duration and no
fixed number of pulses (operator request: "the user just starts and
stops the pulsing manually rather than a fixed number of pulses").

Implementation: a thin subclass of Short-Term Pulsing with an UNBOUNDED
duration (``duration_s = inf`` makes SP's wall-clock end condition
vacuous, so only the abort flag — the GUI Stop button — exits the
loop).  Everything else is inherited verbatim: snapshot captures every
``capture_interval_s`` with full metrics, the shared fit-the-view
rescale loop, the zero-pattern unused channels, bias feedback, damage
warnings, and the single ``stop_all`` teardown.

The ONE semantic difference: pressing Stop is the **designed** way to
end this experiment, so the result is reported as a normal completion
(``aborted=False``) rather than an aborted run — unless a real error
occurred (stim load failure etc.), which stays an abort.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..hardware.base import Oscilloscope, Stimulator
from ..session import Session
from .base import ExperimentEvent, ExperimentResult
from .short_pulsing import ShortPulsingExperiment, ShortPulsingPolicy


@dataclass
class ContinuousPulsingPolicy:
    #: Seconds between snapshot captures while pulsing.
    capture_interval_s: float = 5.0


class ContinuousPulsingExperiment(ShortPulsingExperiment):

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope, *,
                 amplitude_ua: float,
                 policy: Optional[ContinuousPulsingPolicy] = None):
        cp = policy or ContinuousPulsingPolicy()
        super().__init__(
            session, stimulator, oscilloscope,
            amplitude_ua=amplitude_ua,
            # inf duration → SP's wall-clock end condition never fires;
            # the abort flag (GUI Stop) is the only exit.
            policy=ShortPulsingPolicy(
                capture_interval_s=float(cp.capture_interval_s),
                duration_s=float("inf")),
        )

    def run(self) -> ExperimentResult:
        self._emit(ExperimentEvent(
            kind="log", session=self.session,
            message=("Continuous pulsing — the stim pulses until you "
                     "press Stop (snapshot every "
                     f"{self.policy.capture_interval_s:g} s).")))
        result = super().run()
        if result.error is None:
            # Operator Stop IS the normal end of a continuous run —
            # report a clean completion, not an abort.  Real failures
            # (stim load error, hardware disconnect) keep aborted=True
            # via the error field.
            result.aborted = False
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=(f"Continuous pulsing stopped by operator after "
                         f"{len(result.captures)} snapshot(s).")))
        return result
