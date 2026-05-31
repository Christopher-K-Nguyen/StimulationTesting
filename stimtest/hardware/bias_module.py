"""Abstract interpulse-bias-module interface + dataclasses.

Mirrors the ``Stimulator`` / ``Oscilloscope`` ABC pattern from
:mod:`stimtest.hardware.base`, kept in its own module to avoid
sprawling ``base.py`` now that there are three device classes.

The canonical implementation is :class:`stimtest.hardware.stm32_bias.
STM32BiasModule` which speaks the protocol documented in
``stm32_bias_protocol.md`` (sibling file).  A simulator backend
lives in :mod:`stimtest.hardware.bias_simulator` for offline dev.

Lifecycle (mirrors :class:`Stimulator` / :class:`Oscilloscope`)::

    bias = open_bias_module(simulate=False, port="COM5")
    try:
        bias.reset()
        bias.set_bias_voltage(0.25)
        bias.set_compliance_current(100e-6)
        bias.set_bias_enabled(True)
        bias.start_log()
        # ... experiment runs; STM32 autonomously engages bias on the
        # TTL trigger between stim pulses ...
        bias.stop_log()
        readout = bias.read_log()
    finally:
        bias.close()

The experiment runner integration (TODO, follow-up turn) will arm
the log before each capture and drain it after, stashing the
returned :class:`BiasLogReadout` alongside the scope acquisition
in the session ``.npz`` file.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class BiasModuleInfo:
    """Static info snapshot taken at :meth:`BiasModule.open`."""
    manufacturer: str = ""       # "STMicroelectronics"
    model: str = ""              # "Nucleo-G474RE"
    hardware_id: str = ""        # "IPB-001" (board revision)
    firmware: str = ""           # "0.1.0"
    port: str = ""               # "COM5" / "/dev/ttyACM0"
    log_capacity: int = 0        # samples; queried via LOG:CAPacity?
    is_simulated: bool = False


# Structured-array dtype for the log readout.  Using a structured
# ndarray (rather than four separate 1-D arrays) keeps the four
# fields contiguous per-sample, simplifies .npz round-trip, and
# matches the column order of the CSV that ``LOG:DATA?`` returns.
BIAS_LOG_DTYPE = np.dtype([
    ("t_us",    np.float64),   # microseconds from LOG:CLEar (monotonic)
    ("bias_v",  np.float64),   # actual DAC output (V)
    ("bias_a",  np.float64),   # bias current (A) through output path
    ("elec_v",  np.float64),   # electrode potential as seen by STM32 ADC (V); NaN if no sense path
])


@dataclass
class BiasLogReadout:
    """Result of draining the firmware's circular log buffer."""
    samples: np.ndarray          # structured array with BIAS_LOG_DTYPE
    n_points: int                # equals len(samples) when buffer didn't wrap
    overflowed: bool             # True if buffer wrapped since last clear (oldest samples lost)
    sample_rate_hz: float        # active LOG:RATE at readout time


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BiasModule(ABC):
    """Abstract interpulse-potential-bias module.

    See ``stm32_bias_protocol.md`` for the wire protocol of the
    canonical STM32 implementation.  Methods correspond 1:1 to
    protocol commands (with friendlier Python names).
    """

    info: BiasModuleInfo

    # ------------------------------------------------------------- lifecycle
    @property
    def is_open(self) -> bool:
        """True after a successful :meth:`open`."""
        return (getattr(self, "info", None) is not None
                and bool(self.info.port))

    @abstractmethod
    def open(self) -> None:
        """Open the serial port, query ``*IDN?``, populate :attr:`info`.

        Must tolerate the USB-CDC DTR-toggle soft-reset that happens
        when the port opens — waits up to 1000 ms for the first
        ``*IDN?`` reply before raising.
        """

    @abstractmethod
    def close(self) -> None:
        """Send ``*RST`` and close the serial port.  Idempotent."""

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    @abstractmethod
    def reset(self) -> None:
        """Send ``*RST`` — bias OFF, V=0, holdoff=0, polarity=LOW, log cleared."""

    # ------------------------------------------------------------- bias config
    @abstractmethod
    def set_bias_voltage(self, v: float) -> None:
        """Set programmed bias voltage (V).  Out-of-range raises."""

    @abstractmethod
    def get_bias_voltage(self) -> float:
        """Return the programmed bias voltage (NOT the actual DAC output).

        Use :meth:`read_bias_voltage` for the actual output."""

    @abstractmethod
    def set_bias_enabled(self, enabled: bool) -> None:
        """Master enable.  OFF disconnects DAC regardless of trigger state."""

    @abstractmethod
    def get_bias_enabled(self) -> bool: ...

    @abstractmethod
    def set_bias_holdoff_us(self, us: float) -> None:
        """Delay after TTL falling edge before bias DAC engages (µs).

        Used to let post-pulse discharge settle.  Range 0–10000 typical.
        """

    @abstractmethod
    def set_compliance_current(self, amps: float) -> None:
        """Set compliance current limit (A).

        On overcurrent the firmware folds DAC to 0 V, sets
        ``bias_enabled = False``, and pushes error -310 onto the
        error queue.  Default 100 µA.
        """

    # ------------------------------------------------------------- trigger
    @abstractmethod
    def set_trigger_polarity(self, polarity: str) -> None:
        """``"LOW"`` (default — bias during interpulse interval) or
        ``"HIGH"``.  PlexStim EXT-trigger goes HIGH during pulse,
        LOW between — so LOW is what you want for interpulse bias."""

    @abstractmethod
    def set_trigger_source(self, source: str) -> None:
        """``"GPIO"`` (default — react to TTL on the trigger input pin),
        ``"MAN"`` (ignore trigger; bias engages only on :meth:`set_bias_enabled`
        — test mode), or ``"CONT"`` (continuous — calibration)."""

    @abstractmethod
    def set_trigger_watchdog_ms(self, ms: float) -> None:
        """Auto-disable bias if trigger is stuck in the engaging state
        longer than this duration.  Default 5000 ms.  Pass 0 to disable."""

    # ---------------------------------------------------- snapshot reads
    @abstractmethod
    def read_bias_voltage(self) -> float:
        """Actual DAC output voltage (V).  May differ from
        :meth:`get_bias_voltage` if compliance fold-back fired or the
        bias is currently disengaged."""

    @abstractmethod
    def read_bias_current(self) -> float:
        """Bias current through the output path (A).  Zero when disengaged."""

    @abstractmethod
    def read_electrode_potential(self) -> float:
        """Electrode potential as seen by STM32's ADC (V).  Returns
        ``float('nan')`` if hardware lacks an electrode-V sense path."""

    @abstractmethod
    def read_bias_state(self) -> bool:
        """True if DAC currently driving (bias engaged)."""

    # ---------------------------------------------------- buffered log
    @abstractmethod
    def set_log_rate_hz(self, hz: float) -> None:
        """Set the buffered-log sample rate (Hz).  Default 10000."""

    @abstractmethod
    def get_log_capacity(self) -> int:
        """Buffer capacity in samples (firmware-defined).  At 10 kSPS
        and a 4096-sample buffer that's ~410 ms of coverage."""

    @abstractmethod
    def clear_log(self) -> None:
        """Empty the buffer and reset the time origin."""

    @abstractmethod
    def start_log(self) -> None:
        """Begin sampling.  Idempotent if already running."""

    @abstractmethod
    def stop_log(self) -> None:
        """Stop sampling.  Buffer contents preserved.  Idempotent."""

    @abstractmethod
    def get_log_state(self) -> str:
        """``"RUN"`` or ``"STOP"``."""

    @abstractmethod
    def get_log_points(self) -> int:
        """Number of samples currently in buffer (≤ capacity)."""

    @abstractmethod
    def get_log_overflow(self) -> bool:
        """True if buffer wrapped since last :meth:`clear_log`."""

    @abstractmethod
    def read_log(self) -> BiasLogReadout:
        """Drain the buffer into a :class:`BiasLogReadout`.

        Implementations send ``LOG:DATA?`` and parse the CSV block
        through the ``END\\n`` terminator.  After return, the buffer
        is unchanged on the firmware side — call :meth:`clear_log`
        explicitly if you want to arm for the next capture.
        """

    # ---------------------------------------------------- error queue
    @abstractmethod
    def pop_error(self) -> Tuple[int, str]:
        """Pop one error from the queue.  Returns ``(0, "No error")``
        when the queue is empty."""

    def drain_errors(self, max_pop: int = 32) -> List[Tuple[int, str]]:
        """Pop errors until queue empty or ``max_pop`` reached.

        Returns the list of popped errors (each ``(code, msg)``).
        Empty list when the queue was already empty.  ``max_pop``
        is a runaway-loop backstop — should never matter in practice
        since the queue is 16-deep per the protocol spec.
        """
        out: List[Tuple[int, str]] = []
        for _ in range(max_pop):
            code, msg = self.pop_error()
            if code == 0:
                break
            out.append((code, msg))
        return out
