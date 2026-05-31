"""Tests for the STM32 bias-module abstract base + simulator backend.

The STM32 hardware driver itself (:class:`STM32BiasModule`) needs
pyserial + a real Nucleo to exercise meaningfully; that goes into
``test_stm32_bias.py`` (separate file, skipped when pyserial /
hardware absent).  This file covers:

* :class:`BiasModule` API contract (every abstract method present,
  ``is_open``/context-manager semantics correct).
* :class:`SimulatedBiasModule` implements every abstract method and
  behaves sensibly.
* :class:`BiasLogReadout` dataclass round-trips through ``np.save`` /
  ``np.load`` — needed for session ``.npz`` persistence later.
* Protocol value-validation rules (polarity / source enums) raise
  ValueError, not silently accept garbage.
"""
from __future__ import annotations

import io

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Abstract base contract
# ---------------------------------------------------------------------------
def test_bias_module_abstract_methods_exist():
    """Every method the protocol promises has a corresponding abstract
    method on the ABC.  If a new command is added to the protocol,
    add the method here AND to the ABC; this test pins the contract.
    """
    from stimtest.hardware.bias_module import BiasModule

    expected_methods = {
        # Lifecycle
        "open", "close", "reset",
        # Bias config
        "set_bias_voltage", "get_bias_voltage",
        "set_bias_enabled", "get_bias_enabled",
        "set_bias_holdoff_us",
        "set_compliance_current",
        # Trigger
        "set_trigger_polarity", "set_trigger_source",
        "set_trigger_watchdog_ms",
        # Snapshot reads
        "read_bias_voltage", "read_bias_current",
        "read_electrode_potential", "read_bias_state",
        # Buffered log
        "set_log_rate_hz", "get_log_capacity",
        "clear_log", "start_log", "stop_log",
        "get_log_state", "get_log_points", "get_log_overflow",
        "read_log",
        # Error queue
        "pop_error",
    }
    actual = {m for m in dir(BiasModule) if not m.startswith("_")}
    missing = expected_methods - actual
    assert not missing, f"BiasModule missing abstract methods: {missing}"


def test_bias_module_cannot_instantiate_abstract():
    """Sanity: the ABC itself isn't instantiable.  Catches accidental
    removal of every ``@abstractmethod`` decorator."""
    from stimtest.hardware.bias_module import BiasModule

    with pytest.raises(TypeError, match="abstract"):
        BiasModule()


# ---------------------------------------------------------------------------
# SimulatedBiasModule end-to-end
# ---------------------------------------------------------------------------
def test_simulator_open_close_lifecycle():
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    assert bias.is_open is False
    bias.open()
    assert bias.is_open is True
    bias.close()
    assert bias.is_open is False


def test_simulator_context_manager():
    """``with bias:`` is the recommended usage pattern from the docstring
    — verify it actually works."""
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    with bias as b:
        assert b is bias
        assert bias.is_open is True
    assert bias.is_open is False


def test_simulator_info_populated():
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    info = bias.info
    assert info.manufacturer == "PULSAR-Simulator"
    assert info.model == "SimBias"
    assert info.hardware_id == "SIM-001"
    assert info.is_simulated is True
    assert info.log_capacity > 0  # must report a non-zero capacity


def test_simulator_bias_config_round_trip():
    """Set then get returns what we set — the simulator's most basic
    contract.  Real hardware won't always (DAC quantization etc.) but
    the simulator should be perfect."""
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()

    bias.set_bias_voltage(0.25)
    assert bias.get_bias_voltage() == pytest.approx(0.25)

    bias.set_bias_enabled(True)
    assert bias.get_bias_enabled() is True
    bias.set_bias_enabled(False)
    assert bias.get_bias_enabled() is False


def test_simulator_reset_returns_to_safe_state():
    """``*RST`` (`reset()`) must turn bias OFF, voltage 0.  Per the
    protocol §3.1 — same contract for simulator."""
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()
    bias.set_bias_voltage(0.5)
    bias.set_bias_enabled(True)

    bias.reset()
    assert bias.get_bias_voltage() == 0.0
    assert bias.get_bias_enabled() is False


def test_simulator_trigger_polarity_validation():
    """Wrong polarity raises ValueError rather than silently accepting."""
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()

    # Valid values accepted (case-insensitive).
    bias.set_trigger_polarity("LOW")
    bias.set_trigger_polarity("HIGH")
    bias.set_trigger_polarity("low")

    with pytest.raises(ValueError, match="LOW.*HIGH"):
        bias.set_trigger_polarity("MIDDLE")


def test_simulator_trigger_source_validation():
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()

    for ok in ("GPIO", "MAN", "CONT", "gpio"):
        bias.set_trigger_source(ok)

    with pytest.raises(ValueError, match="GPIO"):
        bias.set_trigger_source("UART")


def test_simulator_read_log_empty_when_not_started():
    """Reading the log immediately after open (before start_log) should
    return an empty structured array, not raise."""
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()
    readout = bias.read_log()
    assert readout.n_points == 0
    assert len(readout.samples) == 0
    assert readout.overflowed is False
    assert readout.sample_rate_hz > 0


def test_simulator_read_log_after_run_returns_samples():
    """After ``start_log`` + a brief wait + ``stop_log``, the log should
    contain samples at roughly the configured rate."""
    import time
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()
    bias.set_log_rate_hz(1000.0)   # 1 kHz for a cheap-to-verify rate
    bias.set_bias_voltage(0.3)
    bias.set_bias_enabled(True)
    bias.clear_log()
    bias.start_log()
    time.sleep(0.05)  # 50 ms → expect ~50 samples
    bias.stop_log()
    readout = bias.read_log()

    assert readout.n_points > 0
    assert readout.sample_rate_hz == 1000.0
    # Voltage should sit near the programmed value (with noise).
    assert abs(np.mean(readout.samples["bias_v"]) - 0.3) < 0.01
    # Electrode V is NaN for the simulator (no sense path).
    assert np.all(np.isnan(readout.samples["elec_v"]))


def test_simulator_no_errors_in_queue():
    """Simulator never generates errors; queue must always read empty."""
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()
    assert bias.pop_error() == (0, "No error")
    assert bias.drain_errors() == []


def test_simulator_electrode_potential_is_nan():
    """Per the simulator's docstring it has no electrode-V sense path."""
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()
    assert np.isnan(bias.read_electrode_potential())


def test_simulator_compliance_then_disabled_reports_zero_current():
    """When bias is disengaged, both the read voltage and current
    should report zero — the DAC is high-impedance."""
    from stimtest.hardware import SimulatedBiasModule

    bias = SimulatedBiasModule()
    bias.open()
    bias.set_bias_voltage(1.0)
    bias.set_bias_enabled(False)
    assert bias.read_bias_voltage() == 0.0
    assert bias.read_bias_current() == 0.0
    assert bias.read_bias_state() is False


# ---------------------------------------------------------------------------
# BiasLogReadout persistence
# ---------------------------------------------------------------------------
def test_bias_log_readout_round_trips_through_numpy_save():
    """Session ``.npz`` persistence (follow-up turn) will save the
    structured samples array directly.  Verify the dtype round-trips
    cleanly so we don't lose precision or field names."""
    from stimtest.hardware import BIAS_LOG_DTYPE

    n = 100
    a = np.empty((n,), dtype=BIAS_LOG_DTYPE)
    a["t_us"] = np.arange(n, dtype=np.float64) * 100.0
    a["bias_v"] = 0.25
    a["bias_a"] = 1e-6
    a["elec_v"] = np.nan

    buf = io.BytesIO()
    np.save(buf, a)
    buf.seek(0)
    b = np.load(buf, allow_pickle=False)

    assert b.dtype == BIAS_LOG_DTYPE
    assert len(b) == n
    np.testing.assert_array_equal(b["t_us"], a["t_us"])
    np.testing.assert_array_equal(b["bias_v"], a["bias_v"])
    assert np.all(np.isnan(b["elec_v"]))


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def test_open_bias_module_simulate_returns_simulator():
    from stimtest.hardware import open_bias_module, SimulatedBiasModule

    bias = open_bias_module(simulate=True)
    try:
        assert isinstance(bias, SimulatedBiasModule)
        assert bias.is_open is False  # factory doesn't open() for you
    finally:
        bias.close()


def test_open_bias_module_real_no_ports_raises():
    """When no serial port is given AND auto_discover finds none, the
    factory raises a helpful error rather than silently dropping to
    the simulator (matches the no-fallback contract of
    ``open_oscilloscope``)."""
    from unittest.mock import patch
    from stimtest.hardware import open_bias_module

    # Patch auto_discover to return empty (simulates "no serial ports").
    with patch("stimtest.hardware.stm32_bias.auto_discover", return_value=[]):
        with pytest.raises(RuntimeError, match="No serial ports"):
            open_bias_module(simulate=False, port=None)
