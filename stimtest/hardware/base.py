"""Abstract hardware interfaces.

This module defines what a "stimulator" and "oscilloscope" look like to the
rest of the project. Both real and simulated drivers implement these
abstract base classes; experiment runners and GUI code never import the
concrete drivers directly — they go through the factory functions in
``stimtest.hardware.__init__`` so swapping backends is trivial:

    >>> from stimtest.hardware import open_stimulator, open_oscilloscope
    >>> stim = open_stimulator(simulate=False)   # real Plexon, fall back to sim
    >>> scope = open_oscilloscope(simulate=False) # real Tek, fall back to sim

Each Stimulator carries a :class:`StimulatorInfo` snapshot (serial number,
firmware, scaling factors) so downstream code can tell if it's talking to
NIL hardware (different VMon/IMon scaling) without having to re-query.

Each Oscilloscope carries:
* :class:`ScopeInfo` (make, model, resource string, channel count)
* a ``channel_aliases`` dict mapping logical names ("vmon", "imon", "eret",
  "eact") to physical channels ("CH1", ..., "CH4"). The user picks this on
  the Setup tab; the experiment code reads it back without needing to know
  which is which.

To add a third backend (say, an Aim-TTI scope or a different stimulator),
subclass :class:`Stimulator` or :class:`Oscilloscope`, implement the abstract
methods, and add it to the factory. Nothing else changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple  # noqa: F401

import numpy as np

from ..waveforms import PulsePattern


# ---------------------------------------------------------------------------
# Stimulator
# ---------------------------------------------------------------------------
@dataclass
class StimulatorInfo:
    serial_number: str = ""
    firmware: str = ""
    description: str = ""
    n_channels: int = 0
    vmon_scaling_v_per_v: float = 0.25
    imon_scaling_v_per_ua: float = 2.5e-3
    is_simulated: bool = False


class Stimulator(ABC):
    """Abstract Plexon-style multi-channel current stimulator."""

    info: StimulatorInfo

    # ----- lifecycle -----
    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ----- channel programming -----
    @abstractmethod
    def load_channel(self, channel: int, pattern: PulsePattern) -> None:
        """Load a rectangular (or arbitrary) pattern onto one channel."""

    @abstractmethod
    def set_monitor_channel(self, channel: int) -> None:
        """Route this channel to the V_mon / I_mon outputs."""

    @abstractmethod
    def start_channel(self, channel: int) -> None:
        """Start a SINGLE channel that already has a pattern loaded.

        **Correct usage:** when only one channel needs to fire — e.g.
        a Voltage Transient sweep where exactly one electrode is
        active and the rest are unloaded. Single DLL/USB round-trip,
        no synchronisation concerns.

        **Wrong usage:** ``for ch in channels: stim.start_channel(ch)``
        when several channels are loaded. Each call is one round-trip
        of latency, so the channels start staggered by tens to
        hundreds of microseconds, and the device's digital sync
        output fires N times per pulse cycle (one edge per channel)
        instead of once. Use :meth:`start_all` for that case.
        """

    @abstractmethod
    def stop_channel(self, channel: int) -> None: ...

    @abstractmethod
    def stop_all(self) -> None: ...

    def start_all(self) -> None:
        """Start EVERY loaded channel on the same firmware clock tick.

        **Correct usage:** any workflow where multiple channels are
        loaded with patterns and need to fire synchronously — uniform
        whole-array stim, future multipolar configurations that load
        the active + return channels separately, ANY scenario where
        the digital sync output has to produce one clean edge per
        pulse cycle.

        On Plexon hardware this maps to ``PS_StartStimAllChannels``,
        a single SDK call. Don't simulate this with a per-channel
        :meth:`start_channel` loop — that defeats the point.

        Default implementation raises :class:`NotImplementedError`
        so a backend that lacks a synchronous-start primitive surfaces
        clearly. The simulator overrides this with a synchronous
        running-state flip across all loaded channels.
        """
        raise NotImplementedError

    def loaded_channels(self) -> "set[int]":
        """Channels that currently have a pattern loaded on the device.

        Plexon firmware uses the "loaded vs unloaded" state to decide
        routing for multipolar configurations: an unloaded channel
        outputs 0 A and can serve as a passive return path; a loaded
        channel cannot. There is no ``PS_UnloadChannel`` SDK
        function — the only way to clear a previously-loaded pattern
        is ``PS_InitAllStim`` (which :meth:`reinit` wraps).

        Experiment runners use this set to decide whether a
        configuration change requires a full reinit: if any of the
        new config's return channels appears in ``loaded_channels()``,
        the return wiring would be invalid, so reinit before
        proceeding.

        Default implementation returns an empty set; backends that
        actually track loaded state override this.
        """
        return set()

    # ----- advanced -----
    def reinit(self) -> None:
        """Close and re-open the hardware connection.

        Required between certain configuration changes — see
        :meth:`loaded_channels` for the routing-correctness rule that
        drives this. The default implementation is ``close(); open();``
        which works for any driver that implements those two cleanly.
        """
        try:
            self.close()
        except Exception:
            pass
        self.open()

    def set_repetitions(self, channel: int, n: int) -> None:
        """Number of pulses to deliver (0 = infinite). Optional override."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Oscilloscope
# ---------------------------------------------------------------------------
@dataclass
class ScopeInfo:
    make: str = ""
    model: str = ""
    serial: str = ""
    firmware: str = ""
    resource: str = ""
    n_channels: int = 4
    is_simulated: bool = False
    #: True if the scope has an external-trigger BNC input
    #: (``TRIGger:A:EDGE:SOUrce EXT`` is accepted). TBS2000B/MSO/MDO/DPO
    #: have it; TBS1000C and TBS1000B-EDU don't (trigger sources are
    #: CH1, CH2, AC LINE only on those models). Probed at open time and
    #: cached here so the GUI / runners can fall back to an internal
    #: channel trigger when EXT is unavailable.
    has_ext_trigger: bool = True


@dataclass
class ScopeAcquisition:
    """One captured frame from the scope."""
    time_us: np.ndarray
    channels: Dict[str, np.ndarray] = field(default_factory=dict)  # name -> volts
    sample_period_us: float = 0.0
    record_length: int = 0
    trigger_position_us: float = 0.0


class Oscilloscope(ABC):
    """Abstract multi-channel scope."""

    info: ScopeInfo
    channel_aliases: Dict[str, str]  # logical name -> physical channel ('CH1' ...)

    # ----- lifecycle -----
    @abstractmethod
    def open(self, resource: Optional[str] = None) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    # ----- configuration -----
    @abstractmethod
    def set_channel_scale(self, channel: str, volts_per_div: float) -> None: ...

    @abstractmethod
    def set_horizontal_scale(self, seconds_per_div: float) -> None: ...

    def set_trigger(self, source: str = "EXT", level_v: float = 1.0,
                    slope: str = "RISE", mode: str = "NORMAL") -> None:
        """Default no-op; overridden by real driver."""

    def set_acquisition_mode(self, mode: str = "AVERAGE", n_avg: int = 16) -> None:
        """SAMPLE | AVERAGE | PEAK; n_avg only used for AVERAGE."""

    # ----- acquisition-capability discovery -----
    def acquisition_modes(self) -> "List[str]":
        """List of acquisition-mode strings the scope supports.

        The GUI uses this to populate the mode dropdown in the Setup
        tab. Subclasses can override to return a hardware-specific
        list; the default is the ubiquitous Sample / Average pair.
        """
        return ["SAMPLE", "AVERAGE"]

    def average_count_choices(self) -> "Optional[List[int]]":
        """Discrete averaging counts the scope offers, or None for arbitrary.

        Some scopes accept any integer up to a max (Keysight, R&S);
        others are restricted to a fixed list (Tektronix TBS-series:
        powers of two from 2 to 512). The GUI builds the right widget
        based on this — combobox for fixed choices, spinbox otherwise.
        """
        return None  # arbitrary by default

    def max_average_count(self) -> int:
        """Upper bound on n_avg for scopes that accept arbitrary counts.

        Ignored when :meth:`average_count_choices` returns a list.
        """
        return 512

    def set_record_length(self, n: int) -> None:
        """Set the number of samples per acquisition (e.g. 2500 for TBS scopes)."""

    def configure_channels(self, alias_to_phys: Dict[str, str]) -> None:
        """Tell the scope which logical signal lives on which physical channel."""
        self.channel_aliases = dict(alias_to_phys)

    # ----- acquisition -----
    @abstractmethod
    def single_capture(self) -> ScopeAcquisition:
        """Trigger once, wait for completion, return all configured channels."""

    def auto_scale(self) -> None:
        """Best-effort autoscale; default no-op (simulator)."""
