"""Abstract hardware interfaces.

This module defines what a "stimulator" and "oscilloscope" look like to the
rest of the project. Both real and simulated drivers implement these
abstract base classes; experiment runners and GUI code never import the
concrete drivers directly — they go through the factory functions in
``stimtest.hardware.__init__`` so swapping backends is trivial.

Also exposes :func:`fmt_elapsed` — a shared MATLAB-``getEndTime.m`` port
that auto-picks µs / ms / s / min / h based on magnitude.  Used by every
hardware driver's per-command timing log; lives here so the
implementations don't fork copies.

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


def fmt_elapsed(t_s: float) -> str:
    """Format an elapsed-seconds value with an auto-selected unit.

    Direct port of MATLAB ``getEndTime.m``:

      * < 0.1 ms → microseconds
      * < 0.1 s  → milliseconds
      * < 60 s   → seconds
      * < 60 min → minutes
      * else     → hours

    Single source of truth — both :mod:`stimtest.hardware.tektronix`
    and :mod:`stimtest.hardware.plexon` re-export this as ``_fmt_elapsed``
    so legacy intra-module callers keep working unchanged.
    """
    ms = t_s * 1e3
    if ms < 0.1:
        return f"{t_s * 1e6:.2f} us"
    if t_s < 0.1:
        return f"{ms:.2f} ms"
    if t_s < 60:
        return f"{t_s:.2f} s"
    mn = t_s / 60.0
    if mn < 60:
        return f"{mn:.2f} min"
    return f"{mn / 60.0:.2f} h"

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

    @property
    def is_open(self) -> bool:
        """True after a successful ``open()``; subclasses may override."""
        return getattr(self, "info", None) is not None and self.info.n_channels > 0

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

    def abort_all(self) -> None:
        """Cease ALL stimulation **immediately**, even mid-pulse.

        Wraps the PlexStim SDK's ``PS_AbortAll`` — the *emergency*
        cease-all-stim primitive.  Distinct from :meth:`stop_all`
        (``PS_StopStimAllChannels``) in two ways:

        * **Mid-pulse halt**: takes effect even if a pulse or
          arbitrary waveform is currently being emitted.
          ``stop_all`` lets the current waveform finish.
        * **No trigger-mode requirement**: ``stop_all`` returns SDK
          error 4 ("wrong trigger mode") when the device isn't in
          ``PS_TRIG_SOFT``; ``abort_all`` has no such restriction.

        Intended for the GUI's Stop button so the operator can halt
        a sweep on demand without waiting up to one capture period
        for the current pulse to complete.

        Default implementation falls back to :meth:`stop_all` so
        backends without a true abort primitive (e.g. the simulator)
        still behave sensibly — the API distinction is a no-op there
        because they have no in-flight hardware pulses to interrupt.
        """
        self.stop_all()

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

    def load_all_channels(self) -> None:
        """Commit the staged parameters of EVERY channel to the device
        in one call.

        On Plexon hardware this maps to ``PS_LoadAllChannels`` — the
        commit step the working MATLAB used for MONOPOLAR configs (no
        return channels): stage every channel's pattern, then ONE
        ``PS_LoadAllChannels``, then ``PS_StartStimAllChannels``.
        Multipolar configs instead committed each NON-return channel
        individually (returns stay unloaded as passive sinks), so
        callers gate this on an empty return set — see
        :meth:`ExperimentRunner.commit_loaded_channels`.

        Default implementation is a NO-OP: backends that already commit
        per-channel at :meth:`load_channel` time (e.g. the simulator)
        have nothing extra to do here.
        """
        return

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

    def set_auto_discharge(self, enabled: bool) -> None:
        """Enable / disable the device's automatic discharge mode.

        With auto-discharge ON (the default and the strongly-recommended
        setting), the stimulator actively shorts the electrode to a
        recovery rail during the post-pulse discharge interval — the
        residual charge from any per-pulse imbalance gets safely drained
        before the next pulse fires. With it OFF the electrode floats
        during the gap, and any residual charge accumulates pulse-to-
        pulse. That can drift the electrode-tissue interface DC offset
        out of the water-window and cause irreversible faradaic
        reactions, so the GUI surfaces a warning before letting the
        user disable this.

        Maps to the PlexStim 2.0 SDK's ``PS_SetAutoDischarge`` call.
        Default implementation is a no-op so backends without this
        primitive (the simulator) can ignore it without raising.
        """
        return None

    def get_auto_discharge(self) -> Optional[bool]:
        """Return the current auto-discharge state as a tri-bool:

        * ``True`` — enabled (the safe, recommended state).
        * ``False`` — explicitly disabled (the user has acknowledged
          the risk; pulses run with a floating electrode during the
          discharge interval).
        * ``None`` — backend doesn't expose this setting (simulator,
          legacy device).
        """
        return None


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
    #:
    #: Default is **False** — EXT must be affirmatively confirmed by
    #: either a matching model-spec entry or a successful live probe.
    #: A True default would silently show the EXT toggle in the Setup
    #: tab even on scopes that have no rear-BNC input (TBS1000C etc.),
    #: leaving the operator to pick a trigger source the scope can't
    #: actually use.
    has_ext_trigger: bool = False


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

    # ----- display geometry -----
    # Default values are the conservative "classic Tek" layout
    # (10 horizontal × 8 vertical divs).  Real drivers should
    # override at connect time:
    #   * Tek: queries ``HORizontal:DIVisions?`` for horiz, looks up
    #     ``n_vert_divs`` in the per-series spec for vert (no SCPI
    #     query exists for vertical divs — it's a fixed family
    #     property).
    #   * Simulator: inherits the defaults; tests don't care.
    # Used by the in-view rescale loop and the clip detector — see
    # ``hardware/tektronix.py:channel_is_clipped`` and
    # ``experiments/voltage_transient.py:_one_capture`` block 2b.
    _n_horiz_divs: float = 10.0
    _n_vert_divs: float = 8.0
    _half_vert_divs: float = 4.0

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

    def set_horizontal_position(self, percent: float) -> None:
        """Set the trigger position as a percentage from the left edge
        (0 = far left, 100 = far right). Default no-op; overridden by
        drivers that support trigger-marker repositioning."""

    def capture_single_sequence(self, *, n_acq: int = 16,
                                timeout_s: float = 30.0,
                                tick_fn=None) -> "ScopeAcquisition":
        """Acquire one complete N-average sequence (SEQuence mode).
        Default falls back to single_capture; overridden by real drivers."""
        return self.single_capture()

    def set_trigger(self, source: str = "EXT", level_v: float = 1.0,
                    slope: str = "RISE", mode: str = "NORMAL") -> None:
        """Default no-op; overridden by real driver."""

    def set_trigger_level(self, level_v: float) -> None:
        """Update only the trigger threshold (V) without changing source/slope.
        Default no-op; overridden by real driver and simulator."""

    def set_cursors(self, phase1_us: float, interphase_us: float = 0.0,
                    source_channel: str = "CH1") -> None:
        """Place vertical-bar cursors at end-of-phase-1 and mid-interphase.
        Default no-op; overridden by real driver."""

    # ----- gated measurement primitives (closed-loop feedback path) -----
    # These provide a *fast* read of a per-channel statistic (mean) over a
    # user-selected time window WITHOUT pulling the full waveform off the
    # wire.  Used by the closed-loop interpulse-bias feedback controller
    # (~10-20 Hz update rate is the design target).
    #
    # Implementation pattern on real Tek scopes:
    #   1. Place vertical (time-axis) cursors at ``t_us_start`` and
    #      ``t_us_end`` relative to the trigger via
    #      ``CURSor:VBArs:POSITION1/2``.
    #   2. Configure the MEASUrement subsystem to gate on cursors
    #      (``MEASU:IMMed:GATing CURSor``, ``TYPe MEAN``, ``SOURce CHx``).
    #   3. Query ``MEASU:IMMed:VALue?`` — the scope computes the statistic
    #      over every sample in the gated window and returns one float.
    #
    # Faster than ``CURVe?`` because no waveform transfer; more accurate
    # than a point-cursor read because the scope averages internally,
    # beating 8-bit ADC quantization the same way the AVERAGE acquisition
    # mode does.  See ``experiments/bias_feedback.py`` for the consumer.
    def gate_measurement_window(self, t_us_start: float,
                                t_us_end: float) -> None:
        """Place cursors at ``[t_us_start, t_us_end]`` (µs from trigger)
        and configure the scope's MEASUrement subsystem to gate on those
        cursors.  Subsequent :meth:`measure_mean` / similar queries return
        statistics computed over just this window.

        Default no-op; real drivers override.  Idempotent — repeated calls
        with the same window are cheap; calls with a new window update
        cursor positions but don't re-arm anything else.
        """

    def clear_measurement_gating(self) -> None:
        """Turn off cursor gating so subsequent MEASUrement queries
        operate on the full acquisition window.  Default no-op."""

    def measure_mean(self, channel: str) -> float:
        """Return the MEAN of ``channel`` over the currently-gated window
        (set by :meth:`gate_measurement_window`).  Volts.

        Returns ``float('nan')`` if the scope can't compute a value
        (channel off, no acquisition, gating cleared without re-setting).
        Default no-op returns NaN; real drivers override.
        """
        return float("nan")

    def settle_one_acquisition(self, *,
                               timeout_s: "Optional[float]" = None) -> None:
        """Wait for one fresh averaged acquisition WITHOUT transferring
        the waveform.  Used by the rescale loop's recapture before a
        ``CURVe?`` so the transferred frame reflects the post-rescale
        V/div, not a stale frame from the previous scale (gotcha #40).
        Default no-op; real drivers override."""
        return None

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

    def set_trigger_pulse_width(self, source: str, level_v: float,
                                polarity: str, width_s: float,
                                when: str = "MOREthan") -> bool:
        """Switch to a pulse-width-qualified trigger; return True if applied.

        Base default returns False — "not supported" — so callers keep the
        edge trigger.  The Tektronix MODERN dialect (TBS2000/B, TBS1000C)
        overrides this; the simulator + legacy families inherit the
        decline.  See the Tektronix override for the semantics.
        """
        return False

    def set_average_count(self, n_avg: int) -> int:
        """Apply the AVERAGE-mode averaging count and RETURN the value the
        scope actually applied, confirmed by reading it back.

        The GUI calls this when the operator changes the average count so
        it can reflect the REAL applied value (a fixed-grid scope snaps an
        off-grid request to its nearest supported count).  This base
        default just echoes the request — arbitrary-count scopes and the
        simulator impose no grid, so there's nothing to snap or confirm.
        The Tektronix override writes ``ACQuire:NUMAVg`` and re-queries the
        device.  Touches only the averaging count, never the acquisition
        mode, so it's cheap enough to run interactively.
        """
        return int(n_avg)

    def set_record_length(self, n: int) -> None:
        """Set the number of samples per acquisition (e.g. 2500 for TBS scopes)."""

    def configure_channels(self, alias_to_phys: Dict[str, str]) -> None:
        """Tell the scope which logical signal lives on which physical channel."""
        self.channel_aliases = dict(alias_to_phys)

    # ----- acquisition -----
    @abstractmethod
    def single_capture(self, *,
                       timeout_s: "Optional[float]" = None) -> ScopeAcquisition:
        """Trigger once, wait for completion, return all configured channels.

        Parameters
        ----------
        timeout_s:
            Override for the driver's default per-capture timeout.
            Pass when the caller can predict the required wait from
            ``N_avg / rate_hz``.  ``None`` falls back to the driver's
            default (typically 10 s via the VISA session timeout).
        """

    # ----- abort responsiveness -----------------------------------------
    def set_abort_check(self, fn) -> None:
        """Install a no-arg callable that returns True when the current
        run is aborting.  The driver's blocking poll / sleep loops
        (``single_capture``'s NUMACq poll, ``settle_one_acquisition``,
        ``capture_while_running``'s wait) consult it so STOP halts the
        run promptly instead of waiting out a full averaging window
        (operator: "When pressing STOP, the program should immediately
        stop when it is safely possible").  Pass ``None`` to clear it
        (runners do so in their ``finally``).
        """
        self._abort_check = fn

    def _should_abort(self) -> bool:
        """True when the installed abort-check fires.  Never raises."""
        fn = getattr(self, "_abort_check", None)
        if fn is None:
            return False
        try:
            return bool(fn())
        except Exception:
            return False

    def capture_while_running(self, wait_s: float = 0.0, *,
                              reset_before_run: bool = False,
                              tick_fn=None) -> ScopeAcquisition:
        """Acquire while stim is running (MATLAB-style continuous mode).

        Default implementation falls back to ``single_capture`` so
        simulators and legacy drivers work without change.
        """
        import time
        remaining = max(float(wait_s), 0.0)
        while remaining > 0.0:
            if self._should_abort():
                break
            chunk = min(0.05, remaining)
            time.sleep(chunk)
            remaining -= chunk
            if tick_fn is not None:
                tick_fn()
        return self.single_capture()

    def adapt_channel_scale(self, channel: str, *,
                            v_min: float, v_max: float,
                            divs: float = 4.0,
                            shrink_threshold: float = 0.30,
                            shrink_stable_count: int = 2,
                            force_grow: bool = False):
        """Post-capture autorange — default no-op; returns None."""
        return None

    def channel_is_clipped(self, channel: str,
                           v_min: float, v_max: float,
                           *, margin_pct: float = 0.05) -> Optional[bool]:
        """Does the observed ``[v_min, v_max]`` indicate the trace is
        saturating the scope's vertical rails?

        Different from :meth:`channel_in_view` (which checks "does
        the data fit comfortably inside the visible window") because
        clipping is the failure mode where the data appears to fit
        EXACTLY at the rail — the scope's ADC saturated and the true
        peak is HIGHER than what was captured.  When this happens,
        sizing the new V/div from the observed range produces the
        SAME V/div as before (since the observed range == the visible
        window) and the rescale fails to expand.  Coarse-step UP is
        required instead.

        Default implementation returns ``None`` (can't detect);
        overridden by drivers that can query the channel's actual
        V/div + POSition and compute the rail position.

        ``margin_pct`` (default 5 %) — how close to the rail counts
        as clipped.  At 5 %, an observation within `0.95 × half_window`
        of either rail is flagged as clipped.  Too tight and noisy
        traces trip false positives; too loose and genuine clips are
        missed.

        Returns:
          * ``True``  — clipped at the top, bottom, or both rails.
          * ``False`` — trace has headroom on both sides.
          * ``None``  — driver can't introspect (simulator, SCPI
            error).  Callers treat None like "leave alone" — don't
            assume one way or the other.
        """
        return None

    def channel_in_view(self, channel: str,
                        v_min: float, v_max: float,
                        *, margin_divs: float = 3.95) -> Optional[bool]:
        """Does ``[v_min, v_max]`` fit inside the channel's visible window?

        Port of the in-view check from MATLAB ``getWaveform2.m``::

            vertPos        = -pos_divs * vpd               # volts
            range_min      = -margin_divs * vpd + vertPos
            range_max      = +margin_divs * vpd + vertPos
            in_view        = (v_min > range_min) and (v_max < range_max)

        ``margin_divs`` defaults to **3.9** (MATLAB ``MAX_FACTOR``), one
        tenth of a division shy of the scope's hard ±4-div edge so
        traces that just kiss the grid still register as out-of-view —
        the user typically wants headroom on every rescale.

        Returns:
          * ``True``  — trace fits comfortably; no rescale needed.
          * ``False`` — trace would clip; rescale + recapture.
          * ``None``  — scope can't introspect its own scale / position
            (no SCPI access, e.g. the simulator). Callers should fall
            back to a single-pass fit-the-range write without iterating.

        Default implementation returns ``None``; tektronix.py overrides
        it with an actual SCPI ``SCAle? / POSition?`` query pair.
        """
        return None

    def channel_clip_sides(self, channel: str,
                           v_min: float, v_max: float,
                           *, margin_divs: float = 3.95):
        """Directional companion to :meth:`channel_in_view` — returns
        which side(s) of the screen the trace exceeds.

        See ``TektronixOscilloscope.channel_clip_sides`` for the full
        contract (5-tuple ``(below_out, above_out, pos_shift_divs,
        headroom_below_div, headroom_above_div)``).  Default returns
        ``None`` so simulator-style drivers without introspection
        don't have to implement it; the caller falls back to the
        existing symmetric-extrapolate path.
        """
        return None

    def set_channel_position(self, channel: str, divisions: float) -> None:
        """Set the per-channel vertical position in divisions from screen
        centre.  Default no-op so simulator-style drivers don't have
        to override; the Tek driver overrides with a SCPI write."""

    def set_channel_coupling(self, channel: str, coupling: str) -> None:
        """Set AC / DC input coupling on one channel.  Default no-op so
        simulator-style drivers don't have to override; the Tek driver
        writes ``CHx:COUPling AC|DC``.  Used by the electrode-offset
        capture (measure the DC rest potential, then AC-couple so the
        small pulse swing can be fine-scaled)."""

    def zero_all_channel_positions(self) -> None:
        """Centre EVERY physical channel (vertical position 0 div).

        Operator spec: "all channels default to vertical position 0
        before running the experiment."  Called at run start (and at
        each VT channel change via ``apply_default_scope_view``) so a
        run always begins from a known centred baseline regardless of
        whatever position a previous run / the adaptive rescale loop
        left a channel at.  Default no-op for simulator-style drivers;
        the Tek driver overrides to walk ``CH1…CHn``."""

    def set_channel_scale_and_position_for_range(
            self, channel: str, *,
            v_min: float, v_max: float,
            divs: float = 4.0):
        """Fine-scale + position for a channel whose observed trace
        spans ``[v_min, v_max]``.  Default no-op — only overridden by
        the Tektronix driver where the SCPI writes have real effect.
        Returns ``None`` so callers don't have to special-case
        non-introspectable backends (simulator, headless tests).
        """
        return None

    def auto_scale(self) -> None:
        """Best-effort autoscale; default no-op (simulator)."""
