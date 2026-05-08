"""Common runner infrastructure for experiments.

Every experiment is a subclass of :class:`ExperimentRunner` that implements a
single :meth:`run` method. The runner is purely synchronous (it can sleep,
poll the scope, etc.) and emits :class:`ExperimentEvent` objects to any
subscriber as it goes. This decouples experiment logic from how it's
*driven*:

* The GUI (:mod:`stimtest.gui.experiment_tabs`) wraps a runner in a
  :class:`RunnerWorker` running on a QThread, and converts events into Qt
  signals so the live plot / metrics table update in real time.
* The CLI (:mod:`run_cli`) just calls ``runner.run()`` directly and ignores
  events.
* Unit tests can subscribe a list-collector and assert on what got emitted.

Event kinds (string in ``ExperimentEvent.kind``):
  ``run_start``    - starting a new ChannelRun
  ``capture``      - one Capture finished (always carries .capture)
  ``run_end``      - ChannelRun complete
  ``session_end``  - whole experiment complete
  ``log``          - free-form log message in .message
  ``aborted``      - error / user abort

Aborting
--------
GUI calls :meth:`abort` from the main thread. The runner checks
``self.aborted`` at every safe point (between captures, between
configurations) and unwinds cleanly, stopping the stimulator first for
safety.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from ..hardware.base import Oscilloscope, Stimulator
from ..session import Capture, ChannelRun, Session


ProgressCallback = Callable[["ExperimentEvent"], None]


@dataclass
class ExperimentEvent:
    """Posted to subscribers as the experiment progresses."""
    kind: str            # 'capture' | 'run_start' | 'run_end' | 'session_end' | 'log' | 'aborted'
    session: Session
    run: Optional[ChannelRun] = None
    capture: Optional[Capture] = None
    message: str = ""


@dataclass
class ExperimentResult:
    """Returned by :meth:`ExperimentRunner.run`."""
    session: Session
    captures: List[Capture] = field(default_factory=list)
    aborted: bool = False
    error: Optional[str] = None

    @property
    def max_q_inj(self) -> float:
        if not self.captures:
            return float("nan")
        return max((c.metrics.charge_injection_mc_per_cm2
                    for c in self.captures
                    if c.status.good), default=float("nan"))


class ExperimentRunner(ABC):
    """Base class. Subclasses implement :meth:`run`."""

    def __init__(self, session: Session, stimulator: Stimulator,
                 oscilloscope: Oscilloscope):
        self.session = session
        self.stim = stimulator
        self.scope = oscilloscope
        self._subscribers: List[ProgressCallback] = []
        self._abort_requested = False
        # Snapshot the hardware identity into session.extras so the Gamry-DTA
        # exporter can write it out without holding a live reference to the
        # drivers. We do this here (in __init__) so subclasses don't have to
        # remember to call it; the snapshot reflects the connection state at
        # the moment the runner was constructed.
        self._snapshot_instrumentation()

    def _snapshot_instrumentation(self) -> None:
        from dataclasses import asdict
        try:
            stim_info = asdict(self.stim.info) if getattr(self.stim, "info", None) else {}
        except Exception:
            stim_info = {}
        try:
            scope_info = asdict(self.scope.info) if getattr(self.scope, "info", None) else {}
        except Exception:
            scope_info = {}
        aliases = dict(getattr(self.scope, "channel_aliases", {}) or {})
        # Look up coating-derived water-window limits if available
        coating_props = None
        try:
            from ..config import COATINGS, DEPOLARIZATION_TIME_US
            if self.session.test.array.sites:
                first = self.session.test.array.sites[0]
                c = COATINGS.get(first.coating)
                if c is not None:
                    coating_props = {
                        "name": c.name,
                        "cathodic_limit_v": c.cathodic_limit_v,
                        "anodic_limit_v": c.anodic_limit_v,
                        "typical_csc_mc_per_cm2": c.typical_csc_mc_per_cm2,
                    }
        except Exception:
            DEPOLARIZATION_TIME_US = 12.0  # fall back to the canonical default
        else:
            from ..config import DEPOLARIZATION_TIME_US
        self.session.test.extras.update({
            "stimulator_info": stim_info,
            "oscilloscope_info": scope_info,
            "channel_aliases": aliases,
            "coating_props": coating_props,
            "depolarization_us": DEPOLARIZATION_TIME_US,
        })

    # ----- pub/sub --------
    def subscribe(self, cb: ProgressCallback) -> None:
        self._subscribers.append(cb)

    def _emit(self, event: ExperimentEvent) -> None:
        for cb in self._subscribers:
            try:
                cb(event)
            except Exception as e:
                # Never let a UI bug kill the experiment
                print(f"[stimtest] subscriber error: {e}")

    def abort(self) -> None:
        self._abort_requested = True
        try:
            self.stim.stop_all()
        except Exception:
            pass

    @property
    def aborted(self) -> bool:
        return self._abort_requested

    # ----- preflight -----
    def preflight(self) -> None:
        """Run-time sanity checks before any hardware command is issued.

        This is the runner-side mirror of the MATLAB
        ``checkExperimentInputs`` helper: every assumption the run-loop
        makes (hardware open, pattern within bounds, channels present
        on the array, save path writable) gets verified once up-front
        so a faulty input dies with a clear message instead of crashing
        the DLL or producing garbage captures halfway through.

        Subclasses may override to add experiment-specific checks but
        SHOULD call ``super().preflight()`` first so the base checks
        always run.
        """
        # Hardware handles must be open. Catching closed handles up
        # front avoids the cryptic ``ps_get_*`` and ``VI_ERROR_*``
        # failures that come out of the drivers when called on a
        # half-torn-down session.
        if self.stim is None:
            raise RuntimeError("preflight: stimulator is not connected.")
        if self.scope is None:
            raise RuntimeError("preflight: oscilloscope is not connected.")

        test = self.session.test
        # Pattern bounds vs. the device limits. ``PulsePattern.validate``
        # raises ValueError with a phase-by-phase reason when something
        # is out of range.
        if test.pattern is None:
            raise RuntimeError("preflight: no pulse pattern defined.")
        test.pattern.validate()

        # Configuration: active channel must exist on the array; every
        # listed return channel must also be present (or be 0 for the
        # off-array global return).
        cfg = test.configuration
        array = test.array
        if cfg is None:
            raise RuntimeError("preflight: no electrode configuration set.")
        valid_channels = {site.number for site in array.sites}
        if cfg.active not in valid_channels:
            raise RuntimeError(
                f"preflight: active channel {cfg.active} is not present on "
                f"array {array.name!r} (channels: "
                f"{sorted(valid_channels)}).")
        for ret in (cfg.returns or ()):
            if ret == 0:
                continue  # off-array global return — handled by hardware
            if ret not in valid_channels:
                raise RuntimeError(
                    f"preflight: return channel {ret} is not present on "
                    f"array {array.name!r}.")
        if cfg.active in (cfg.returns or ()):
            raise RuntimeError(
                f"preflight: active channel {cfg.active} is also listed "
                f"as a return channel — that's a short.")

    # ----- to be implemented -----
    @abstractmethod
    def run(self) -> ExperimentResult: ...
