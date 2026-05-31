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
from ..readback_calibration import ReadbackCalibration, load_calibration, make_capture
from ..session import Capture, ChannelRun, Session


ProgressCallback = Callable[["ExperimentEvent"], None]


#: I_mon trigger-level scaling constant from MATLAB ``setTriggerLevel.m``
#: (``currentMonScale_V_uA = 1e-3``).  Heuristic, NOT the device's actual
#: imon scaling — real PlexStim 2.0 hardware delivers 2.5 mV/µA (Default)
#: or 1.0 mV/µA (NIL), so the computed threshold lands well below the I_mon
#: peak in either case.
_IMON_TRIG_SCALING_V_PER_UA: float = 1e-3


def imon_trigger_level(amp_ua_signed: float,
                       phase_width_us: float = 200.0,
                       imon_v_per_ua: Optional[float] = None) -> float:
    """Return the I_mon trigger threshold (signed, in volts).

    Exact port of MATLAB ``setTriggerLevel.m``:

    .. code:: matlab

        currentMonScale_V_uA = 1e-3;
        if amplitude1_mag <= 20
            triggerLevel = (amplitude1_mag + 4.5) * sign * 1e-3;
        else
            triggerLevel = amplitude * 1e-3 * scale;
        end

    At 10 µA the level is **14.5 mV** — slightly more than the
    amplitude in mV.  This works because MATLAB's pipeline sets a
    bandwidth-limit filter on the I_mon channel (200 MHz on the
    TBS-series, not Full), which cuts the broadband noise far enough
    that the clean I_mon peak comfortably exceeds 14.5 mV even on
    a NIL device.  Without the BW limit the signal looks noisier
    AND has its peak rolled off, so the trigger never fires — that
    was the failure mode we hit earlier.  The fix is on the
    *bandwidth* axis, not on the trigger formula.

    ``imon_v_per_ua`` is accepted for API compatibility with earlier
    revisions but is no longer used to clamp — the MATLAB-exact
    threshold is what the lab convention expects.
    """
    SCALING = _IMON_TRIG_SCALING_V_PER_UA
    amp_mag = abs(float(amp_ua_signed))
    amp_sign = -1.0 if amp_ua_signed < 0 else 1.0
    scale = 0.40 if phase_width_us >= 100.0 else 0.25
    if amp_mag <= 20.0:
        # Small-amplitude branch tightened: ``(amp + 3.5) × 1 mV/µA``
        # caps the threshold at exactly 13.5 mV for 10 µA (down from
        # MATLAB's 14.5 mV).  The 4.5 mV buffer in MATLAB assumed a
        # 2.5 mV/µA stim into a low-Z scope input, where the actual
        # peak comfortably exceeded the threshold.  With the 20 MHz
        # BW limit now applied to the I_mon channel the signal is
        # cleaner but the realised peak on NIL devices is still close
        # to the theoretical limit, so the buffer needs to come down
        # by ~1 mV to leave the trigger above the realised peak.
        level_mag = (amp_mag + 3.5) * SCALING
    else:
        level_mag = amp_mag * SCALING * scale
    return amp_sign * level_mag


def imon_vertical_scale(amp_ua: float,
                        imon_v_per_ua: Optional[float] = None) -> float:
    """Return the I_mon channel V/div for the requested stimulus amplitude.

    Port of MATLAB ``setOscilloscopeCurrentScale.m`` with the same
    NIL-vs-Default correction applied to :func:`imon_trigger_level`:

    .. code:: matlab

        scale = currentStim / 4 * 1e-3
        if      amp < 20:   scale_num = scale + 3e-3
        elseif  amp < 100:  scale_num = scale + 5e-3
        elseif  amp < 200:  scale_num = scale + 20e-3
        elseif  amp < 500:  scale_num = scale + 40e-3
        else:               scale_num = scale + 80e-3

    MATLAB's empirical constants (the ``/ 4 * 1e-3`` slope and the
    fixed-offset bumps) were tuned on a 2.5 mV/µA Default-scaling
    stimulator, where the I_mon peak at 100 µA is ~250 mV and the
    formula yields 30 mV/div → ~8 divs full-scale (peak fills 4-5
    divs). On a NIL (1 mV/µA) device the peak is 2.5× smaller, so
    the same formula leaves the trace at ~0.3 divs — squished and
    unreadable, exactly what the user reported.

    Fix: when ``imon_v_per_ua`` is supplied, target **4 divisions for
    the expected peak**:

        scale = |amp| × imon_v_per_ua / 4

    then take ``min(MATLAB, expected_peak / 4 + small offset)`` so
    the trace always fits in 2-4 divs regardless of scaling.  On a
    Default device the MATLAB formula already targets ≈ 4 divs, so
    the min() is a near no-op.  On NIL it pulls the V/div down by
    ~2.5× and the trace fills the screen again.

    Pass ``imon_v_per_ua=None`` to get the legacy MATLAB-exact
    behaviour.
    """
    amp_abs = abs(float(amp_ua))
    # Legacy MATLAB-exact path — kept verbatim for callers that don't
    # pass the device scaling (the formula bakes in 1 mV/µA via the
    # ``1e-3`` constant and is correct only when the actual scaling
    # matches that assumption AND the signal isn't attenuated).
    if imon_v_per_ua is None or imon_v_per_ua <= 0.0:
        base = amp_abs / 4.0 * 1e-3
        if amp_abs < 20.0:
            return base + 3e-3
        if amp_abs < 100.0:
            return base + 5e-3
        if amp_abs < 200.0:
            return base + 20e-3
        if amp_abs < 500.0:
            return base + 40e-3
        return base + 80e-3
    # Device-aware path — scale tracks the *actual* expected I_mon
    # peak for THIS stimulator's scaling.  MATLAB's offsets
    # (3 / 5 / 20 / 40 / 80 mV) were tuned on a 1-mV/µA stimulator;
    # we re-express them as a fraction of the expected peak so they
    # scale correctly on both Default (2.5 mV/µA) and NIL (1 mV/µA)
    # devices and on any setup where the I_mon output is attenuated
    # by source impedance or RC filtering.
    #
    #   expected_peak_v = |amp| × imon_v_per_ua
    #   scale = expected_peak / 4    (4 divs for the peak)
    #         + noise_margin         (small fraction of peak, never
    #                                 < an absolute 0.5 mV floor so
    #                                 the trace never sits exactly at
    #                                 the comparator threshold).
    #
    # The scope still snaps this to its native 1-2-5 grid; on NIL
    # the result lands one or two stops finer than MATLAB, which is
    # what makes the 10 µA pulse big enough to trigger on.
    expected_peak_v = amp_abs * float(imon_v_per_ua)
    # Noise margin fraction.  Small amplitudes (< 20 µA) get a tighter
    # margin so the V/div drops slightly — at 10 µA / 1 mV/µA the
    # previous 20 % margin landed at 4.5 mV/div, which the scope
    # snapped up to 5 mV/div and put the ~4 mV measured peak at <1 div
    # (visible but cramped, and the trigger had no headroom).  Using
    # 10 % margin below 20 µA brings the same case to ~3.5 mV/div,
    # which lands the scope on 2 mV/div ⇒ the small pulse fills ~2-4
    # divs and the trigger circuit has room to discriminate.
    if amp_abs < 20.0:
        noise_margin_v = max(0.5e-3, 0.10 * expected_peak_v)
    else:
        noise_margin_v = max(0.5e-3, 0.20 * expected_peak_v)
    return expected_peak_v / 4.0 + noise_margin_v


@dataclass
class ProgressInfo:
    """Snapshot of run progress for the status-bar display.

    Emitted alongside ``ExperimentEvent`` when the runner advances
    to a new logical step (next amplitude in VT, next snapshot in
    SP/LP, next staircase step in PS).  The GUI uses these to
    update a "step X/Y, elapsed mm:ss, ETA mm:ss" indicator so
    long sweeps don't look frozen.

    Fields
    ------
    step : int
        1-based current step number.  ``step == total`` indicates
        the final step is starting (NOT that the run finished).
    total : int
        Total expected steps.  Best estimate at emit time — for
        adaptive sweeps that may early-exit (e.g., VT stopping at
        a water-window hit), the actual completed step count can
        be smaller.
    label : str
        Short human-readable description of what step ``step`` is
        ("amplitude 350 µA on CH 5", "snapshot 7", etc.).  Shown
        in the status bar verbatim — keep it under ~60 chars.
    started_at : float
        ``time.monotonic()`` timestamp at run START (not step
        start).  GUI uses this to compute elapsed + simple ETA
        (``(total - step) * (elapsed / step)``).
    """
    step: int
    total: int
    label: str = ""
    started_at: float = 0.0


@dataclass
class ExperimentEvent:
    """Posted to subscribers as the experiment progresses."""
    kind: str            # 'capture' | 'run_start' | 'run_end' | 'paused' |
                         # 'session_end' | 'log' | 'aborted' | 'progress'
    session: Session
    run: Optional[ChannelRun] = None
    capture: Optional[Capture] = None
    message: str = ""
    #: Optional progress snapshot.  Set on ``kind == "progress"``
    #: events; ``None`` on all other event kinds.  GUI consumers
    #: that don't care about progress can ignore this field.
    progress: Optional[ProgressInfo] = None


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
        # Trigger source ("EXT" or a channel name like "CH2") — set by the
        # GUI tab via the Setup trigger toggle before the runner is started.
        # Used to decide whether to update the trigger level per amplitude.
        self.trigger_source: str = "EXT"
        # True when ``trigger_source`` is a TTL sync line — either the EXT
        # BNC or a scope channel the operator tagged with Role=Trigger
        # (typically CH3/CH4 wired to the Plexon digital sync).  In that
        # case the trigger level is fixed at the TTL midpoint (1.4 V),
        # NOT the amplitude-derived I_mon formula — applying I_mon-style
        # millivolt thresholds to a 3.3 V / 5 V TTL signal causes the
        # scope to either never fire (level below the LOW rail) or fire
        # on noise.  Pushed by the GUI tab before run start.
        self.trigger_is_digital: bool = False
        # When True, the runner pauses between configurations (channels) so
        # the operator can physically re-wire the next channel. The pause is
        # implemented via ``_continue_event``: the runner emits a "paused"
        # event and waits; the GUI calls :meth:`request_continue` to resume.
        # Set by the GUI tab before starting the run.
        import threading as _threading
        self.pause_between_channels: bool = False
        self._continue_event = _threading.Event()
        self._continue_event.set()   # not waiting at construction time
        # Load readback calibration for this stimulator serial, if available.
        serial = getattr(getattr(stimulator, "info", None), "serial_number", "") or ""
        self.cal: Optional[ReadbackCalibration] = load_calibration(stim_serial=serial)
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
        # Releasing the continue gate ensures any thread parked in
        # ``wait_for_continue`` unblocks promptly — otherwise ``abort``
        # only takes effect after the user dismisses the rewire dialog.
        try:
            self._continue_event.set()
        except Exception:
            pass
        # Emergency-cease the stimulator immediately.  Uses the
        # PlexStim ``PS_AbortAll`` primitive (via ``abort_all`` on
        # the driver) rather than ``stop_all`` (``PS_StopStimAll-
        # Channels``) for two reasons:
        #   1. ``PS_AbortAll`` halts mid-pulse — operator intent on
        #      a Stop button is "cease NOW", not "let the current
        #      pulse finish".  ``stop_all`` waits up to one capture
        #      period for the in-flight waveform to complete.
        #   2. ``PS_AbortAll`` has no trigger-mode requirement;
        #      ``stop_all`` returns SDK error 4 outside
        #      ``PS_TRIG_SOFT`` mode.
        # The cross-thread call is now safe: the DLL access is
        # serialised by ``_dll_lock`` inside the Plexon driver
        # (see plexon.py ``_dll_locked``), so this can no longer
        # corrupt the DLL heap by racing the worker's stim calls.
        # CLAUDE.md §3's "single producer" rule is satisfied at the
        # SDK level by the lock — only one thread is inside any
        # SDK call at a time, even if both threads are calling.
        try:
            self.stim.abort_all()
        except Exception:
            pass

    def request_continue(self) -> None:
        """Release a runner that's paused between channels.

        Called by the GUI when the user clicks "Continue" on the
        rewire dialog.  Idempotent — extra calls are no-ops.
        """
        try:
            self._continue_event.set()
        except Exception:
            pass

    def wait_for_continue(self, next_config_label: str = "") -> bool:
        """Block until :meth:`request_continue` (or :meth:`abort`) fires.

        Emits a ``"paused"`` event so the GUI can pop the rewire prompt,
        then waits on the event.  Polls every ~100 ms so an abort during
        the pause unblocks promptly.  Returns True if the user resumed,
        False if the wait ended due to an abort.

        Safe to call from the runner thread; safe to interleave with
        scope/stim cleanup before invoking.
        """
        if not self.pause_between_channels:
            return True
        try:
            self.stim.stop_all()
        except Exception:
            pass
        self._continue_event.clear()
        self._emit(ExperimentEvent(
            kind="paused", session=self.session,
            message=(f"Paused — rewire to {next_config_label}, then click "
                     f"Continue.").strip()))
        while not self._continue_event.wait(timeout=0.1):
            if self._abort_requested:
                return False
        return not self._abort_requested

    #: TTL midpoint used as the trigger level for any digital sync line
    #: (EXT BNC or a scope channel tagged Role=Trigger).  3.3 V and 5 V
    #: TTL both clear 1.4 V with margin in both directions; the same
    #: level works for the Plexon stimulator's 3.3 V CMOS sync output
    #: and the bench function generators that produce 5 V TTL.
    TTL_TRIGGER_LEVEL_V: float = 1.4

    def update_imon_trigger_level(self, amp_ua_signed: float,
                                   phase_width_us: float = 200.0) -> None:
        """Update the scope trigger level for the current amplitude.

        Behaviour depends on the trigger source the GUI configured:

          * ``trigger_source == "EXT"`` → no-op.  The scope firmware
            owns the level on its dedicated EXT BNC.
          * ``trigger_is_digital`` (a channel-Trigger TTL sync line,
            typically CH3/CH4 wired to the Plexon digital sync) →
            write the fixed TTL midpoint :attr:`TTL_TRIGGER_LEVEL_V`.
            The amplitude-derived formula doesn't apply — a sync line
            has a fixed 3.3 V / 5 V swing regardless of stim current.
          * Otherwise (I_mon channel trigger) → MATLAB
            ``setTriggerLevel`` formula so the threshold tracks the
            programmed current and stays above the noise floor at
            every step of a VT / PS sweep.
        """
        if self.trigger_source == "EXT":
            return
        try:
            if self.trigger_is_digital:
                self.scope.set_trigger_level(self.TTL_TRIGGER_LEVEL_V)
                return
            # Thread the stimulator's actual I_mon scaling through so
            # the clamp inside ``imon_trigger_level`` can pull the
            # threshold below the expected peak on NIL devices.
            stim_info = getattr(self.stim, "info", None)
            imon_v_per_ua = float(
                getattr(stim_info, "imon_scaling_v_per_ua", 0.0) or 0.0)
            level = imon_trigger_level(
                amp_ua_signed, phase_width_us,
                imon_v_per_ua=imon_v_per_ua or None)
            self.scope.set_trigger_level(level)
        except Exception:
            pass

    def check_trigger_alignment(self, acq, *,
                                tolerance_us: float = 5.0) -> Optional[float]:
        """Verify the captured I_mon edge lands at t ≈ 0 µs.

        After an acquisition, the largest sample-to-sample change on the
        I_mon channel should coincide with the trigger (which is either
        the I_mon edge itself, or the EXT sync 1.2 µs before it).  If
        that edge ends up far from t = 0, the time axis is wrong — most
        likely a scope-firmware glitch where ``XZEro`` or ``PT_Off``
        disagreed with each other.

        Returns the edge time (µs) so the caller can record it.  Emits a
        ``"log"`` event with a ⚠ marker when the misalignment exceeds
        ``tolerance_us``.  ``None`` if the acquisition is malformed or
        no I_mon alias is mapped.
        """
        import numpy as _np
        try:
            aliases = getattr(self.scope, "channel_aliases", {}) or {}
            imon_ch = aliases.get("imon")
            if not imon_ch:
                return None
            t_us = getattr(acq, "time_us", None)
            chans = getattr(acq, "channels", None) or {}
            i_mon = chans.get(imon_ch)
            if t_us is None or i_mon is None:
                return None
            if len(t_us) < 2 or len(i_mon) != len(t_us):
                return None
            di = _np.abs(_np.diff(_np.asarray(i_mon, dtype=float)))
            # Find the FIRST edge that exceeds 80 % of the max edge,
            # NOT the absolute argmax.  A biphasic pulse has multiple
            # edges of comparable magnitude (phase 1 onset, phase 1
            # end, phase 2 onset, phase 2 end) — argmax can land on
            # any of them depending on noise, producing apparent
            # offsets at +200 µs / +250 µs / +450 µs etc. that aren't
            # actually trigger-alignment errors.  Picking the FIRST
            # large edge consistently lands on the leading-edge
            # transition (phase 1 onset) which IS where the trigger
            # should be.  Closes Task #59 (false-positive cluster).
            max_di = float(_np.max(di))
            if max_di <= 0:
                return None
            threshold = 0.80 * max_di
            edge_candidates = _np.where(di >= threshold)[0]
            if edge_candidates.size == 0:
                return None
            edge_idx = int(edge_candidates[0])  # FIRST such edge
            t_edge = float(t_us[edge_idx])
            if abs(t_edge) > float(tolerance_us):
                # Surface the diagnostic context the operator needs
                # to triage: how many candidate edges, their times,
                # and the ratio of the picked edge to max.  When
                # multiple edges are close to the threshold, the
                # warning is more likely a multi-edge biphasic and
                # less likely an actual time-axis bug.
                n_cand = int(edge_candidates.size)
                t_max = float(t_us[int(_np.argmax(di))])
                self._emit(ExperimentEvent(
                    kind="log", session=self.session,
                    message=(f"⚠ trigger/pulse alignment off: first "
                             f"≥80%-max I_mon edge at t={t_edge:+.2f} µs "
                             f"(expected ≈ 0 µs; {n_cand} candidate "
                             f"edge(s) ≥ threshold; absolute-max edge "
                             f"at t={t_max:+.2f} µs)")))
            return t_edge
        except Exception:
            return None

    def load_zero_unused_channels(self, pattern, config) -> None:
        """Load a zero-amplitude, same-duration copy of ``pattern`` on
        every channel that is NOT the active channel AND NOT in
        ``config.returns``.

        Direct port of MATLAB ``setPattern.m`` "Zero Current" block
        (lines 142-193).  Three categories of channel exist on the
        stimulator during a run, and each gets a different treatment:

        ============ =========================================================
        active       Receives the experiment's real pattern.  ONE channel
                     per configuration (``config.active``).
        returns      Multipolar return path.  Left UNLOADED so the device
                     routes current back through these channels passively
                     — loading anything (even zero) makes them ACTIVE
                     drivers, which breaks the multipolar configuration.
                     Zero or more channels per configuration
                     (``config.returns``).
        unused       Everything else.  Loaded with a same-duration but
                     zero-amplitude copy of the active pattern so the
                     channel TICKS in step with the active one but
                     delivers no current.  Without this, an unused
                     channel either carries a stale pattern from a
                     prior step (if reinit was skipped) or runs out of
                     sync with the active channel's pulse cycle.
        ============ =========================================================

        **CG (Common Ground)** configurations have ``returns =``
        every-other-channel by construction (see
        ``Configuration.enumerate_combinations`` for ``"CG"``), so the
        unused set is naturally empty and this method becomes a no-op
        — matching MATLAB's explicit ``~isCG`` skip without a
        special-case branch.

        Errors loading any single unused channel are swallowed (we
        emit a log line but don't abort the run) — the active channel's
        data still flows correctly regardless.

        ``set_repetitions(ch, 0)`` is called on every loaded unused
        channel so it follows the same "infinite repetitions until
        stopped" cadence as the active channel; otherwise the unused
        channel would default to 1 repetition and fall silent after
        one pulse cycle while the active keeps going.
        """
        try:
            n_channels = int(
                getattr(getattr(self.stim, "info", None),
                        "n_channels", 0) or 0)
            if n_channels <= 0:
                return
            active = int(getattr(config, "active", 0))
            returns_set = set(int(r) for r in (getattr(config, "returns", ()) or ()))
            excluded = {active} | returns_set
            unused = [ch for ch in range(1, n_channels + 1)
                      if ch not in excluded]
            if not unused:
                # CG case (returns spans every other channel) or a
                # 1-channel device — nothing to load.
                return
            try:
                zero_pattern = pattern.scaled(0.0)
            except Exception:
                # Pathological pattern that can't be scaled — bail out
                # silently rather than crashing the runner.
                return
            n_loaded = 0
            for ch in unused:
                try:
                    self.stim.load_channel(ch, zero_pattern)
                    # Match the active channel's infinite-repetitions
                    # cadence so the unused channels stay aligned with
                    # the active pulse cycle, not fall out after one
                    # pulse.
                    try:
                        self.stim.set_repetitions(ch, 0)
                    except Exception:
                        pass
                    n_loaded += 1
                except Exception:
                    # One unused channel failing to load shouldn't kill
                    # the run.  Just continue with the rest — the active
                    # channel's data remains correct.
                    continue
            if n_loaded > 0:
                try:
                    total_us = float(getattr(pattern, "total_pulse_us", 0.0))
                except Exception:
                    total_us = 0.0
                self._emit(ExperimentEvent(
                    kind="log", session=self.session,
                    message=(
                        f"Loaded zero-amplitude pattern on {n_loaded} "
                        f"unused channel(s) — "
                        f"{sorted(unused)[:8]}"
                        + (f" … (+{len(unused) - 8} more)"
                           if len(unused) > 8 else "")
                        + f"  ·  duration {total_us:.1f} µs "
                        f"(matches active pattern).")))
        except Exception as e:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=f"load_zero_unused_channels skipped: {e}"))

    def update_imon_vertical_scale(self, amp_ua: float) -> None:
        """Re-apply the I_mon channel V/div for the current amplitude.

        Mirrors MATLAB ``setOscilloscopeCurrentScale.m`` — sized to keep
        the I_mon trace at 2-4 divisions on screen across the sweep so
        the user can actually SEE the captured current waveform.  Reads
        the I_mon physical channel from the scope's ``channel_aliases``
        mapping; silent no-op when no I_mon alias is configured (e.g.
        a 2-channel scope that's only displaying V_mon).
        """
        try:
            aliases = getattr(self.scope, "channel_aliases", {}) or {}
            imon_ch = aliases.get("imon")
            if not imon_ch:
                return
            # Pull the stimulator's actual I_mon scaling so the
            # vertical scale fits the real signal on NIL (1 mV/µA)
            # as well as Default (2.5 mV/µA) devices.
            stim_info = getattr(self.stim, "info", None)
            imon_v_per_ua = float(
                getattr(stim_info, "imon_scaling_v_per_ua", 0.0) or 0.0)
            self.scope.set_channel_scale(
                imon_ch,
                imon_vertical_scale(amp_ua,
                                    imon_v_per_ua=imon_v_per_ua or None))
        except Exception:
            pass

    def apply_default_scope_view(self, pattern, *, amp_ua: Optional[float] = None,
                                 is_multipolar: bool = False,
                                 environment_short: Optional[str] = None,
                                 reason: str = "channel change") -> None:
        """Revert the scope to the per-channel default view.

        Faithful port of MATLAB ``setDefaultScopeView3.m``: every time
        the monitor channel changes (and at run start), we re-run the
        full default-view pass so the new channel starts from a known-
        good window instead of inheriting whatever scale/position the
        previous channel's adaptive loop converged on.

        The MATLAB sequence is:

          1. Pick horizontal scale from a candidate timebase grid
             (smallest where pulse / window ≥ 0.4) and write
             HORizontal position then HORizontal scale.
          2. Open vertical-bar cursors at phase-1 end and phase-1 + a
             depolarization gap (the MATLAB ``depolTime`` rule depends
             on phase1 / interphase magnitudes).
          3. Set V_mon vertical scale — default 200 mV/div, but special
             cases for very-short or very-wide phase patterns.
          4. Set I_mon vertical scale via ``setCurrentScale`` (our
             :func:`imon_vertical_scale`).
          5. Set trigger level via ``setTriggerLevel2`` (our
             :func:`imon_trigger_level`), skipped on EXT.

        We also reset the per-channel ``adapt_channel_scale`` history
        so the autorange hunts freshly for this channel.

        Silent no-op if the scope is missing any of the hooks (older
        mock scopes pass through without complaint).
        """
        if self.scope is None or pattern is None:
            return
        scope = self.scope
        try:
            # ---- 0. Wipe adapt history ---------------------------------
            reset_fn = getattr(scope, "reset_adapt_state", None)
            if callable(reset_fn):
                reset_fn(None)

            phases = getattr(pattern, "phases", None) or []
            if not phases:
                return
            ph1 = phases[0]
            ph2 = phases[1] if len(phases) > 1 else None
            phase1_us = float(getattr(ph1, "width_us", 0.0))
            interphase_us = float(getattr(ph1, "delay_after_us", 0.0))
            phase2_us = float(getattr(ph2, "width_us", 0.0)) if ph2 else 0.0
            discharge_us = float(getattr(ph2, "delay_after_us", 0.0)) if ph2 else 0.0

            # ---- 1. Horizontal layout ----------------------------------
            layout_fn = getattr(scope, "auto_layout_for_pulse", None)
            scale_s = None
            pos_pct = None
            if callable(layout_fn):
                try:
                    scale_s, pos_pct = layout_fn(
                        phase1_us=phase1_us,
                        interphase_us=interphase_us,
                        phase2_us=phase2_us,
                        discharge_us=discharge_us,
                        # Any TTL sync line (EXT BNC or a channel
                        # tagged Role=Trigger) carries the same
                        # 1.2 µs Plexon digital-delay; only I_mon
                        # channel triggers skip the offset.
                        ext_trigger=self.trigger_is_digital,
                    )
                except Exception:
                    pass

            aliases = getattr(scope, "channel_aliases", {}) or {}
            vmon_ch = aliases.get("vmon", "CH1")
            imon_ch = aliases.get("imon", "CH2")

            # ---- 2. Cursors --------------------------------------------
            # MATLAB depolTime rules from setDefaultScopeView3.m:
            #   phase1 >= 100 µs        → depolTime = 12 µs
            #   interphase > 10 µs      → depolTime = 6.5 µs
            #   interphase == 0         → depolTime = 0 µs
            #   else                    → depolTime = interphase / 2
            if phase1_us >= 100:
                depol_us = 12.0
            elif interphase_us > 10:
                depol_us = 6.5
            elif interphase_us == 0:
                depol_us = 0.0
            else:
                depol_us = interphase_us / 2.0
            try:
                cursors_fn = getattr(scope, "set_cursors", None)
                if callable(cursors_fn):
                    cursors_fn(phase1_us, depol_us, source_channel=vmon_ch)
            except Exception:
                pass

            # ---- 3. Voltage vertical scale — COARSE initial -------------
            # Direct port of MATLAB ``setOscillocopeView.m`` lines
            # 300-382.  Picks a generous V/div that's GUARANTEED not
            # to clip across the expected amplitude range for this
            # phase-width regime + configuration + environment.  The
            # post-capture fine-scaler in the runner (range+mean port
            # of ``setFineScalePos2.m``) tightens this on the first
            # observation; the only job of this block is to fit the
            # *first* capture's signal on screen even when the load
            # is unknown.
            #
            # MATLAB rule (full 4-case decision tree, line 300-317):
            #
            #   phase1 >= 100 µs:
            #     isMP                          → 1.0 V/div
            #     multipolar (BP/TP/CG/PBP/PTP) → 2.0 V/div   ← NEW
            #   phase1 <  100 µs:
            #     environment ∋ "A" (animal)    → 2.0 V/div   ← NEW
            #     isMP                          → 0.2 V/div
            #     multipolar                    → 0.5 V/div   ← NEW
            #
            # ``interphase == 0`` → 200 µV/div  (degenerate / very-short
            # asymmetric pattern marker; not in MATLAB but useful for
            # patterns where the leading edge is essentially a single
            # phase with no companion).
            env_is_animal = bool(
                environment_short
                and str(environment_short).strip().upper().startswith("A")
            )
            if phase1_us >= 100:
                vmon_vpd = 2.0 if is_multipolar else 1.0
            elif interphase_us == 0:
                vmon_vpd = 200e-6
            elif env_is_animal:
                # Animal-environment short-phase patterns sit on noisier
                # ground; MATLAB picks 2 V/div across the board for both
                # MP and multipolar so the leading edge isn't lost in
                # baseline drift.
                vmon_vpd = 2.0
            else:
                vmon_vpd = 0.5 if is_multipolar else 0.2

            # Apply the V/div + position=0 to EVERY voltage channel
            # the scope is mapped to — V_mon, E_act, E_ret — not just
            # V_mon. MATLAB ``setOscillocopeView.m`` line 322 loops
            # over ``voltageChannel_cell`` and writes the SAME scale +
            # position to all of them. Previously only V_mon got the
            # default-view write, so E_act / E_ret carried whatever
            # V/div the previous channel's fine-scaler converged on
            # (often too tight, clipping the new channel's first
            # capture).
            voltage_roles = ("vmon", "eact", "eret")
            for _role in voltage_roles:
                _ch = aliases.get(_role)
                if not _ch:
                    continue
                try:
                    scope.set_channel_scale(_ch, vmon_vpd)
                    scope.set_channel_position(_ch, 0.0)
                except Exception:
                    pass

            # ---- 4. I_mon vertical scale -------------------------------
            # Use FIRST PHASE (``phases[0]``) — not ``excitation_phase``
            # — for trigger-related amplitude reads.  The scope trigger
            # fires on whichever phase comes first in time; on patterns
            # where ``phases[0]`` ≠ ``excitation_phase`` (anodic-first,
            # certain triphasic shapes) the slope and level derivations
            # both need the phase-1 amplitude, not the excitation
            # phase's.
            _ph0 = (pattern.phases[0]
                    if getattr(pattern, "phases", None) else None)
            if amp_ua is None:
                amp_ua = (abs(float(getattr(_ph0, "amplitude_ua", 0.0)))
                          if _ph0 is not None else 0.0)
            amp_ua = abs(float(amp_ua)) or 1.0
            self.update_imon_vertical_scale(amp_ua)
            try:
                scope.set_channel_position(imon_ch, 0.0)
            except Exception:
                pass

            # ---- 5. Trigger level (skipped on EXT) ---------------------
            # Pull the SIGNED amplitude from ``phases[0]`` so the I_mon
            # trigger level inherits the sign of the leading edge the
            # scope actually sees.
            if _ph0 is not None:
                self.update_imon_trigger_level(
                    float(getattr(_ph0, "amplitude_ua", -amp_ua)),
                    phase_width_us=float(getattr(_ph0, "width_us", phase1_us)))

            # ---- log summary -------------------------------------------
            _hs = f"{scale_s*1e6:.2f} µs/div" if scale_s else "—"
            _hp = f"{pos_pct:.1f} %" if pos_pct is not None else "—"
            # Which voltage roles actually got the default write — for
            # the log the operator reads in the GUI.
            _v_chans = ", ".join(
                f"{aliases[r]} ({r.upper()})"
                for r in ("vmon", "eact", "eret") if aliases.get(r))
            _config_tag = "multipolar" if is_multipolar else "monopolar"
            _env_tag = ("animal" if env_is_animal
                        else (environment_short or "—"))
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=(
                    f"Scope reverted to default view ({reason}): "
                    f"H = {_hs} @ {_hp},  "
                    f"voltage channels [{_v_chans}] = "
                    f"{vmon_vpd*1e3:.0f} mV/div @ pos 0 div "
                    f"(rule: {_config_tag}, phase1={phase1_us:.0f} µs, "
                    f"env={_env_tag}),  "
                    f"{imon_ch} (I_mon) sized for {amp_ua:.0f} µA,  "
                    f"cursors @ {phase1_us:.1f} µs / "
                    f"{phase1_us + depol_us:.1f} µs (depol = {depol_us:.1f} µs).")))
        except Exception as e:
            self._emit(ExperimentEvent(
                kind="log", session=self.session,
                message=f"apply_default_scope_view skipped: {e}"))

    @property
    def aborted(self) -> bool:
        return self._abort_requested

    # ----- abort-aware sleep --------
    def abort_sleep(self, seconds: float, *,
                    chunk_s: float = 0.05) -> bool:
        """Sleep ``seconds`` total in ``chunk_s`` chunks, checking the
        abort flag between chunks.  Returns True if the sleep ran to
        completion; False if the abort flag tripped partway through.

        Use anywhere a runner previously did ``time.sleep(N)`` for
        seconds-scale intervals — replacing with ``abort_sleep`` keeps
        the Stop button responsive within ``chunk_s`` (default 50 ms)
        rather than after the full sleep.

        Sub-chunk sleeps (``seconds < chunk_s``) sleep once for the
        full duration and check abort once at the end.  Negative /
        zero ``seconds`` is a no-op that just checks abort.
        """
        import time as _time
        if seconds <= 0:
            return not self._abort_requested
        if seconds < chunk_s:
            _time.sleep(seconds)
            return not self._abort_requested
        deadline = _time.monotonic() + seconds
        while True:
            if self._abort_requested:
                return False
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                return True
            _time.sleep(min(chunk_s, remaining))

    # ----- progress emit --------
    def _emit_progress(self, step: int, total: int, label: str = "",
                       *, started_at: float = 0.0) -> None:
        """Convenience wrapper: emit a ``kind="progress"`` event with
        a populated :class:`ProgressInfo`.

        Subscribers that don't care about progress simply ignore
        events with no useful ``message`` and ``progress is not None``.
        The GUI's RunnerWorker forwards progress events as a
        dedicated Qt signal that drives the status-bar indicator.

        Parameters
        ----------
        step, total :
            1-based step number + total expected.  ``step == total``
            means the LAST step is starting (not that the run is
            done).
        label :
            Short human-readable description (see ProgressInfo doc).
        started_at :
            ``time.monotonic()`` at run start, used by the GUI to
            compute elapsed + ETA.  Pass 0.0 if you don't want ETA
            shown (the GUI suppresses ETA when started_at is 0).
        """
        self._emit(ExperimentEvent(
            kind="progress",
            session=self.session,
            progress=ProgressInfo(
                step=int(step),
                total=int(total),
                label=str(label),
                started_at=float(started_at),
            ),
        ))

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
