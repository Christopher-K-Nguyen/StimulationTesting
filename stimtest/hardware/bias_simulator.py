"""Simulated STM32 bias module for offline development.

Mirrors :class:`stimtest.hardware.stm32_bias.STM32BiasModule`'s
public API without touching a serial port.  Useful for:

* GUI development on machines without a Nucleo connected.
* Unit tests of experiment runners that arm/drain the bias log
  per capture.
* The ``--simulate`` launcher path.

What's modelled
---------------
* Programmed bias voltage / enable / holdoff / compliance kept as
  ordinary attributes.
* When ``bias_enabled`` is True the simulator pretends the bias is
  engaged (no trigger logic — there's no TTL to monitor).
* :meth:`read_log` synthesizes a flat-line waveform at the
  programmed bias voltage with a small Gaussian noise floor (±50 µV
  on V, ±10 nA on I).  Just enough realism that downstream metric
  code doesn't see all zeros.
* ``MEASure:ELECtrode?`` returns ``NaN`` (no electrode sense path
  in the simulator).
* Errors are not modelled — the queue always returns ``(0, "No error")``.

Anything you can do on the real STM32 you can do here for
development purposes; anything you can't do here (e.g. assert on
firmware-fault recovery) is out of scope for the simulator.
"""
from __future__ import annotations

import time
from typing import Tuple

import numpy as np

from .bias_module import (
    BIAS_LOG_DTYPE, BiasLogReadout, BiasModule, BiasModuleInfo,
)


class SimulatedBiasModule(BiasModule):
    """In-process stand-in for the STM32 bias module driver."""

    def __init__(self):
        self.info = BiasModuleInfo(
            manufacturer="PULSAR-Simulator",
            model="SimBias",
            hardware_id="SIM-001",
            firmware="0.1.0-sim",
            port="<simulated>",
            log_capacity=4096,
            is_simulated=True,
        )
        self._open = False

        # State (matches the real driver's wire-level state shape).
        self._bias_voltage = 0.0
        self._bias_enabled = False
        self._holdoff_us = 0.0
        self._compliance_a = 100e-6
        self._trig_polarity = "LOW"
        self._trig_source = "GPIO"
        self._trig_watchdog_ms = 5000.0

        # Log state.  Tracks accumulated RUN time across multiple
        # start/stop cycles since the last clear, so the buffer
        # contents persist across a stop (matching real-firmware
        # semantics from the protocol §3.5 "Stop sampling. Buffer
        # contents preserved").
        self._log_rate_hz = 10_000.0
        self._log_state = "STOP"
        self._log_accumulated_s = 0.0
        self._log_state_change_t = time.monotonic()

        # No-op cmd_logger hook for API parity.
        self.cmd_logger = None

    # ============================================================ lifecycle

    @property
    def is_open(self) -> bool:
        return self._open

    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    def reset(self) -> None:
        self._bias_voltage = 0.0
        self._bias_enabled = False
        self._holdoff_us = 0.0
        self._trig_polarity = "LOW"
        self._log_state = "STOP"
        self._log_accumulated_s = 0.0
        self._log_state_change_t = time.monotonic()

    # ============================================================ bias config

    def set_bias_voltage(self, v: float) -> None:
        self._bias_voltage = float(v)

    def get_bias_voltage(self) -> float:
        return self._bias_voltage

    def set_bias_enabled(self, enabled: bool) -> None:
        self._bias_enabled = bool(enabled)

    def get_bias_enabled(self) -> bool:
        return self._bias_enabled

    def set_bias_holdoff_us(self, us: float) -> None:
        self._holdoff_us = float(us)

    def set_compliance_current(self, amps: float) -> None:
        self._compliance_a = float(amps)

    # ============================================================ trigger

    def set_trigger_polarity(self, polarity: str) -> None:
        p = polarity.strip().upper()
        if p not in ("LOW", "HIGH"):
            raise ValueError(
                f"polarity must be 'LOW' or 'HIGH', got {polarity!r}")
        self._trig_polarity = p

    def set_trigger_source(self, source: str) -> None:
        s = source.strip().upper()
        if s not in ("GPIO", "MAN", "CONT"):
            raise ValueError(
                f"source must be 'GPIO', 'MAN', or 'CONT', got {source!r}")
        self._trig_source = s

    def set_trigger_watchdog_ms(self, ms: float) -> None:
        self._trig_watchdog_ms = float(ms)

    # ============================================================ measure (snapshot)

    def read_bias_voltage(self) -> float:
        # Simulator: actual output == programmed when enabled, else 0.
        return self._bias_voltage if self._bias_enabled else 0.0

    def read_bias_current(self) -> float:
        # Pretend a ~1 µA leak at the programmed voltage (resistive load
        # at ~250 kΩ), zero when disengaged.
        if not self._bias_enabled or self._bias_voltage == 0:
            return 0.0
        return self._bias_voltage / 250_000.0

    def read_electrode_potential(self) -> float:
        # No electrode sense path in the simulator.
        return float("nan")

    def read_bias_state(self) -> bool:
        return self._bias_enabled

    # ============================================================ buffered log

    def set_log_rate_hz(self, hz: float) -> None:
        self._log_rate_hz = float(hz)

    def get_log_capacity(self) -> int:
        return self.info.log_capacity

    def clear_log(self) -> None:
        """Reset the accumulated-run-time counter to 0.  If currently
        RUN, the next ``get_log_points`` will count from this instant."""
        self._log_accumulated_s = 0.0
        self._log_state_change_t = time.monotonic()

    def start_log(self) -> None:
        if self._log_state != "RUN":
            self._log_state = "RUN"
            self._log_state_change_t = time.monotonic()

    def stop_log(self) -> None:
        """Freeze the accumulated-run-time counter.  Buffer contents
        survive — real-firmware semantics from protocol §3.5."""
        if self._log_state == "RUN":
            self._log_accumulated_s += (
                time.monotonic() - self._log_state_change_t)
            self._log_state = "STOP"
            self._log_state_change_t = time.monotonic()

    def get_log_state(self) -> str:
        return self._log_state

    def get_log_points(self) -> int:
        elapsed = self._elapsed_log_s()
        n = int(elapsed * self._log_rate_hz)
        return min(n, self.info.log_capacity)

    def get_log_overflow(self) -> bool:
        elapsed = self._elapsed_log_s()
        return (elapsed * self._log_rate_hz) > self.info.log_capacity

    def read_log(self) -> BiasLogReadout:
        n = self.get_log_points()
        if n <= 0:
            return BiasLogReadout(
                samples=np.empty((0,), dtype=BIAS_LOG_DTYPE),
                n_points=0,
                overflowed=False,
                sample_rate_hz=self._log_rate_hz)

        # Time axis: uniform spacing 1/rate, starting from 0.
        dt_us = 1e6 / self._log_rate_hz
        t = np.arange(n, dtype=np.float64) * dt_us

        # Programmed voltage with a small noise floor.  Real STM32
        # ADCs at 12-bit + amplifier noise sit around ±50 µV; emulate.
        # Seed off the state-change timestamp so successive reads
        # within one RUN burst get a deterministic-per-burst trace.
        rng = np.random.default_rng(
            seed=int(self._log_state_change_t * 1e3) & 0xFFFF)
        v_noise = rng.normal(0.0, 5e-5, size=n)
        i_noise = rng.normal(0.0, 1e-8, size=n)

        bias_v = self._bias_voltage + v_noise if self._bias_enabled else v_noise
        bias_a = self.read_bias_current() + i_noise

        samples = np.empty((n,), dtype=BIAS_LOG_DTYPE)
        samples["t_us"] = t
        samples["bias_v"] = bias_v
        samples["bias_a"] = bias_a
        samples["elec_v"] = np.nan

        return BiasLogReadout(
            samples=samples,
            n_points=n,
            overflowed=self.get_log_overflow(),
            sample_rate_hz=self._log_rate_hz)

    # ============================================================ error queue

    def pop_error(self) -> Tuple[int, str]:
        # Simulator never generates errors.
        return (0, "No error")

    # ------------------------------------------------------------- helpers
    def _elapsed_log_s(self) -> float:
        """Total RUN time since last :meth:`clear_log`, frozen during STOP.

        Mirrors firmware semantics: the buffer keeps growing only when
        the log is actively running, but its contents persist across
        stop/start cycles.
        """
        if self._log_state == "RUN":
            return self._log_accumulated_s + (
                time.monotonic() - self._log_state_change_t)
        return self._log_accumulated_s
