"""Simulated Plexon stimulator + Tektronix oscilloscope.

The two classes below stand in for the real hardware so the entire codebase
(GUI, experiment runners, persistence, tests) can be exercised offline. The
simulator is realistic enough that the algorithms in
``stimtest.experiments.voltage_transient`` will actually find a sensible
"max Q_inj" value and demonstrate the same waveform features the real
electrodes produce.

What's modeled
--------------
* **Electrode equivalent circuit** (``_ElectrodeModel``):
  a Randles-style two-terminal cell with an access resistance ``R_a`` (the
  ohmic "i-R drop" you see at the leading edge of every phase), a parallel
  polarization resistance ``R_p`` representing the Faradaic leak, and a
  double-layer capacitance ``C_dl`` that integrates the stim current. The
  potential is soft-clamped to the SIROF water window (E_lc = -0.6 V,
  E_la = +0.8 V) so we get the right "limit reached" behavior at high
  amplitudes.

* **Two-electrode pair** (active + return):
  a real bipolar pair has an active electrode (``self._active``) seeing
  +i(t) and a return electrode (``self._return``) seeing -i(t). The
  scope's V_mon trace is (E_act - E_ret) plus a small i*R_a contribution.
  E_act and E_ret are also returned separately so the metrics module can
  compute polarization for each electrode.

* **Plexon scaling factors**:
  the simulated stimulator advertises the same V/V (V_mon) and V/µA (I_mon)
  scaling factors as a real PlexStim 2.0, so any code that converts raw
  scope volts back to electrode volts (e.g. compute_metrics) sees realistic
  numbers.

What's NOT modeled
------------------
* Real electrochemistry (no Faradaic side-reactions, no electrode aging,
  no surface roughness effects). For long-term / progressive-stress
  experiments the simulator will produce stable, drift-free traces — fine
  for testing the GUI and runners but not for studying real degradation.
* Trigger jitter, scope noise floor variability, USB latency, etc.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np

from ..config import (
    DEFAULT_DISCHARGE_DELAY_US, DEFAULT_INTERPHASE_DELAY_US,
    DEFAULT_PHASE_WIDTH_US, IMON_SCALING_DEFAULT, VMON_SCALING_DEFAULT,
)
from ..waveforms import PulsePattern
from .base import Oscilloscope, ScopeAcquisition, ScopeInfo, Stimulator, StimulatorInfo


# ---------------------------------------------------------------------------
# A tiny shared simulation core
# ---------------------------------------------------------------------------
class _ElectrodeModel:
    """Two-terminal Randles-style electrode for the simulator.

    Returns the simulated active and return potentials (vs Ag|AgCl) for an
    arbitrary stim current i(t).
    """

    def __init__(self, r_access_kohm: float = 2.0, c_dl_nf: float = 60.0,
                 r_pol_mohm: float = 5.0, e_eq_v: float = 0.0,
                 cathodic_limit_v: float = -0.6,
                 anodic_limit_v: float = +0.8):
        self.r_access = r_access_kohm * 1e3      # Ω
        self.c_dl = c_dl_nf * 1e-9               # F
        self.r_pol = r_pol_mohm * 1e6            # Ω
        self.e_eq = e_eq_v
        self.e_lc = cathodic_limit_v
        self.e_la = anodic_limit_v

    def simulate(self, t_s: np.ndarray, i_a: np.ndarray) -> np.ndarray:
        """Return electrode potential vs Ag|AgCl, given a current waveform.

        The double-layer voltage E_dl is solved as a first-order ODE:

            dE_dl/dt = (i(t) - E_dl/R_pol) / C_dl

        We use forward Euler because the time steps are small (sub-µs) and
        we don't need stiff integration here. The *terminal* voltage between
        active and reference is E_dl + i*R_access, but the *electrode-vs-
        reference* potential the instrumentation amp would record is just
        E_eq + E_dl (the access drop happens in the electrolyte, not at the
        electrode interface).

        The soft clamp at the water window means once E_dl reaches the SIROF
        cathodic / anodic limit it stops integrating further — the algorithm
        in voltage_transient.py will see this as ``reached_potential_limit``
        and halt the ramp.
        """
        n = t_s.size
        e_dl = np.zeros(n)
        for k in range(1, n):
            dt = t_s[k] - t_s[k - 1]
            # Charge-balance: current into the cap minus leakage through R_pol
            de = (i_a[k - 1] - e_dl[k - 1] / self.r_pol) / self.c_dl
            e_dl[k] = e_dl[k - 1] + de * dt
            # Clamp to the water window so the simulated cell can't run away
            if e_dl[k] < self.e_lc - self.e_eq:
                e_dl[k] = self.e_lc - self.e_eq
            elif e_dl[k] > self.e_la - self.e_eq:
                e_dl[k] = self.e_la - self.e_eq
        return self.e_eq + e_dl


# ---------------------------------------------------------------------------
# Simulated stimulator
# ---------------------------------------------------------------------------
class SimulatedStimulator(Stimulator):
    """In-memory PlexStim mock."""

    def __init__(self, n_channels: int = 16):
        self.info = StimulatorInfo(
            serial_number="SIM-PLX-0001",
            firmware="sim 0.1",
            description="Simulated PlexStim 2.0 (no hardware)",
            n_channels=n_channels,
            vmon_scaling_v_per_v=VMON_SCALING_DEFAULT,
            imon_scaling_v_per_ua=IMON_SCALING_DEFAULT,
            is_simulated=True,
        )
        self._patterns: Dict[int, PulsePattern] = {}
        self._monitor_channel: int = 1
        self._running: Dict[int, bool] = {}
        self._is_open = False

    @property
    def is_open(self) -> bool:
        # Track the real open/close state (the base property infers it
        # from ``info.n_channels``, which the simulator keeps populated
        # across close()).  The GUI's Start path keys re-init on this,
        # so it must flip to False after close() — see CLAUDE.md gotcha
        # on the cross-tab "stim stays closed" lifecycle.
        return self._is_open

    # -- lifecycle --
    def open(self) -> None:
        self._is_open = True
        # Mirrors PlexonStimulator.open: a fresh open clears any
        # previously-loaded patterns.
        self._patterns.clear()
        self._running.clear()

    def close(self) -> None:
        self._is_open = False
        self._patterns.clear()
        self._running.clear()

    def loaded_channels(self) -> set:
        """Return the set of channels that currently have a pattern.

        Mirrors PlexonStimulator.loaded_channels so experiment
        runners can query the same set against either backend.
        """
        return set(self._patterns.keys())

    # -- programming --
    def load_channel(self, channel: int, pattern: PulsePattern) -> None:
        self._patterns[channel] = pattern

    def set_monitor_channel(self, channel: int) -> None:
        self._monitor_channel = channel

    def start_channel(self, channel: int) -> None:
        self._running[channel] = True

    def stop_channel(self, channel: int) -> None:
        self._running[channel] = False

    def stop_all(self) -> None:
        for ch in list(self._running):
            self._running[ch] = False

    def start_all(self) -> None:
        """Synchronous-start mirror — flag every loaded channel running.

        The simulator doesn't model trigger jitter, so the start is
        instantaneous. Mirrors PlexonStimulator.start_all so callers
        can use the same code path against either backend.
        """
        for ch in list(self._patterns):
            self._running[ch] = True

    def set_repetitions(self, channel: int, n: int) -> None:
        if channel in self._patterns:
            self._patterns[channel].repetitions = n

    # -- exposed for the simulated scope --
    @property
    def monitor_channel(self) -> int:
        return self._monitor_channel

    def monitored_pattern(self) -> Optional[PulsePattern]:
        return self._patterns.get(self._monitor_channel)


# ---------------------------------------------------------------------------
# Simulated oscilloscope
# ---------------------------------------------------------------------------
class SimulatedOscilloscope(Oscilloscope):
    """Mock scope that fabricates plausible waveforms from the linked stimulator.

    The simulator is hooked to a stimulator instance via :meth:`bind_stimulator`
    so it can replay whatever pattern is currently loaded on the monitor channel.
    The default channel mapping mirrors the IEEE NER paper setup.
    """

    def __init__(self, record_length: int = 2500, sample_period_us: float = 0.4,
                 *, has_ext_trigger: bool = False):
        # ``has_ext_trigger`` defaults to **False** so the simulator
        # matches the most common bench scope class (TBS1000C / B
        # without an EXT BNC).  Users testing the EXT-trigger path
        # in ``--simulate`` mode can pass ``has_ext_trigger=True``
        # explicitly (e.g. when wiring a unit test for the
        # digital-sync trigger flow).
        #
        # Background: the previous default was ``True``, which made
        # the Setup tab show the "Use digital sync trigger (EXT)"
        # checkbox in every simulator session — confusing because
        # the user's actual bench scope was a TBS1000C with no EXT
        # input.  Hiding the toggle by default in sim makes the
        # ``--simulate`` view representative of the bench reality
        # for the typical operator.
        self.info = ScopeInfo(
            make="SIM",
            model="SimScope-4",
            serial="SIM-SCOPE-0001",
            firmware="sim 0.1",
            resource="SIM://localhost",
            n_channels=4,
            has_ext_trigger=has_ext_trigger,
            is_simulated=True,
        )
        self.channel_aliases = {
            "vmon": "CH1", "imon": "CH2", "eret": "CH3", "eact": "CH4",
        }
        self.record_length = record_length
        self.sample_period_us = sample_period_us
        self.t_pre_us = 50.0  # show 50 µs before the trigger
        self._stim: Optional[SimulatedStimulator] = None
        self._is_open = False

        # Two-electrode model (active and return) for added realism
        self._active = _ElectrodeModel(r_access_kohm=2.1, c_dl_nf=60.0,
                                       r_pol_mohm=5.0, e_eq_v=0.0)
        self._return = _ElectrodeModel(r_access_kohm=1.9, c_dl_nf=80.0,
                                       r_pol_mohm=6.0, e_eq_v=0.05)

        # Gated-measurement window state.  None = no gating; tuple is
        # (t_us_start, t_us_end).  Set by :meth:`gate_measurement_window`,
        # consumed by :meth:`measure_mean` to bound its computation.
        self._gated_window_us: Optional[Tuple[float, float]] = None

    # -- lifecycle --
    def open(self, resource: Optional[str] = None) -> None:
        self._is_open = True
        if resource:
            self.info.resource = resource

    def close(self) -> None:
        self._is_open = False

    # -- config --
    def set_channel_scale(self, channel: str, volts_per_div: float) -> None: pass
    def set_horizontal_scale(self, seconds_per_div: float) -> None: pass
    def set_record_length(self, n: int) -> None: self.record_length = n
    def set_acquisition_mode(self, mode: str = "AVERAGE", n_avg: int = 16) -> None: pass
    def set_trigger(self, source: str = "EXT", level_v: float = 1.0,
                    slope: str = "RISE", mode: str = "NORMAL") -> None: pass

    def set_trigger_level(self, level_v: float) -> None: pass

    # -- gated measurement (closed-loop bias feedback) ----------------
    # Mirrors the Tek primitives.  The simulator implementation does
    # a synchronous ``single_capture`` then computes the channel mean
    # over the requested time window — exact match for what the real
    # scope's MEASUrement subsystem reports, just done host-side here.
    # Stored window state lets ``measure_mean`` re-use the cached
    # bounds without the caller re-supplying them.
    def gate_measurement_window(self, t_us_start: float,
                                t_us_end: float) -> None:
        if t_us_end < t_us_start:
            raise ValueError(
                f"gate window end ({t_us_end} µs) must be ≥ start "
                f"({t_us_start} µs)")
        self._gated_window_us = (float(t_us_start), float(t_us_end))

    def clear_measurement_gating(self) -> None:
        self._gated_window_us = None

    def measure_mean(self, channel: str) -> float:
        """Sample the simulator's current synthesized waveform on the
        requested channel and return the mean over the gated window
        (or NaN if no window is set or no samples land inside)."""
        window = getattr(self, "_gated_window_us", None)
        if window is None:
            return float("nan")
        try:
            acq = self.single_capture()
        except Exception:
            return float("nan")
        # Resolve logical-name channels via the alias map (matches the
        # real driver: callers pass "CH3" but might also pass "eret").
        ch = channel
        if ch in self.channel_aliases:
            ch = self.channel_aliases[ch]
        data = acq.channels.get(ch)
        if data is None or data.size == 0:
            return float("nan")
        t_us = acq.time_us
        t_lo, t_hi = window
        mask = (t_us >= t_lo) & (t_us <= t_hi)
        if not np.any(mask):
            return float("nan")
        return float(np.mean(data[mask]))

    def settle_one_acquisition(self, *, timeout_s=None):
        """No-op for the simulator (no real averager or USB transfer)."""
        return None

    # -- binding --
    def bind_stimulator(self, stim: SimulatedStimulator) -> None:
        """Tell the simulated scope where to read its 'real' stimulus from."""
        self._stim = stim

    # -- acquisition --
    def single_capture(self, *, timeout_s=None) -> ScopeAcquisition:
        # ``timeout_s`` is accepted for API parity with the real driver
        # (callers pass a pulse-rate-aware budget) but the simulator
        # returns a deterministic frame synchronously, so the budget
        # is ignored.
        del timeout_s
        pat = self._stim.monitored_pattern() if self._stim is not None else None
        if pat is None:
            return self._noise_capture()
        # Build a time vector covering pre + pulse + tail, then crop to record_length
        total_us = self.t_pre_us + pat.total_pulse_us + 200.0
        n_natural = int(round(total_us / self.sample_period_us))
        n = max(n_natural, self.record_length)
        t_us = np.linspace(-self.t_pre_us, total_us - self.t_pre_us, n)
        _, i_ua = pat.to_timeseries(t_pre_us=self.t_pre_us, t_post_us=200.0,
                                    sample_period_us=self.sample_period_us)
        # Resample to the same length as t_us
        if i_ua.size != n:
            i_ua = np.interp(t_us, np.linspace(t_us[0], t_us[-1], i_ua.size), i_ua)
        i_a = i_ua * 1e-6

        t_s = t_us * 1e-6
        e_act = self._active.simulate(t_s, i_a)
        e_ret = self._return.simulate(t_s, -i_a)  # return sees opposite polarity

        # The electrode-side differential voltage (E_act − E_ret) plus
        # the IR drop across both access resistances.  This is what
        # a probe at the active electrode would see relative to the
        # return — i.e. the actual ELECTRODE voltage.
        v_electrode = ((e_act - e_ret)
                       + i_a * (self._active.r_access
                                + self._return.r_access))
        # The stim's V_mon OUTPUT pin scales the electrode voltage by
        # ``vmon_v_per_v`` (0.25 V/V on Default, 1.0 V/V on NIL).
        # The scope sees the V_mon-OUTPUT-pin voltage, NOT the raw
        # electrode voltage, so emit the SCALED value here.
        # ``make_capture`` later divides by the same factor to recover
        # electrode volts in ``cap.v_mon_v``.
        #
        # Previously this stage emitted the raw electrode voltage
        # directly, and ``make_capture``'s divide produced a 4×-too-
        # large ``cap.v_mon_v`` on Default scaling (1/0.25 = 4).
        # That broke the E_act = V_mon + E_ret derivation in the
        # multichannel plot — V_mon was in 4×-electrode-volts while
        # E_ret was in electrode-volts, so the sum had wildly wrong
        # units.  Scaling here restores unit consistency with the
        # real-hardware path.
        try:
            vmon_v_per_v = float(self._stim.info.vmon_scaling_v_per_v
                                 or 1.0)
        except Exception:
            vmon_v_per_v = 1.0
        v_mon = v_electrode * vmon_v_per_v
        # Add a touch of measurement noise.  Keep it SMALL (0.5 mV on the
        # V_mon-output pin) — the real capture path averages 64× so its
        # residual noise is sub-mV, and on the Default preset make_capture
        # DIVIDES V_mon by 0.25 (×4), so a larger σ here would blow up to
        # ~20 mV of electrode-referred noise and SWAMP the I·R access step at
        # low amplitude (5 µA → ~20 mV IR), corrupting the single-sample
        # ``v_ir`` the response classifier reads and mislabelling the healthy
        # synthetic electrode "broken".
        rng = np.random.default_rng(0)
        v_mon = v_mon + rng.normal(0.0, 5e-4, size=v_mon.size)
        i_meas = i_ua + rng.normal(0.0, 0.05, size=i_ua.size)

        # Crop to record_length samples around the pulse
        if t_us.size > self.record_length:
            t_us = t_us[: self.record_length]
            v_mon = v_mon[: self.record_length]
            i_meas = i_meas[: self.record_length]
            e_act = e_act[: self.record_length]
            e_ret = e_ret[: self.record_length]

        # Build the channel map from whatever roles the operator
        # actually wired.  V_mon / I_mon are mandatory; E_ret / E_act
        # are OPTIONAL reference-electrode channels that many bench
        # setups don't map (the Setup tab's alias config commonly
        # carries only vmon/imon/trigger).  Hard-indexing
        # ``channel_aliases["eret"]`` KeyError'd the whole capture in
        # that case, which aborted the run before the first snapshot
        # was saved — the "Continuous Pulsing saves nothing" bug (the
        # real Tektronix path already tolerates this because it
        # iterates the live scope channels rather than a fixed dict).
        channels = {
            self.channel_aliases["vmon"]: v_mon,
            self.channel_aliases["imon"]: i_meas * 1e-6 / self._stim.info.imon_scaling_v_per_ua * 1e-3,  # arbitrary scaling
        }
        if "eret" in self.channel_aliases:
            channels[self.channel_aliases["eret"]] = e_ret
        if "eact" in self.channel_aliases:
            channels[self.channel_aliases["eact"]] = e_act
        return ScopeAcquisition(
            time_us=t_us,
            channels=channels,
            sample_period_us=self.sample_period_us,
            record_length=t_us.size,
            trigger_position_us=0.0,
        )

    def _noise_capture(self) -> ScopeAcquisition:
        n = self.record_length
        t_us = np.arange(n) * self.sample_period_us - self.t_pre_us
        rng = np.random.default_rng(0)
        return ScopeAcquisition(
            time_us=t_us,
            channels={ch: rng.normal(0.0, 5e-3, size=n) for ch in self.channel_aliases.values()},
            sample_period_us=self.sample_period_us,
            record_length=n,
        )
