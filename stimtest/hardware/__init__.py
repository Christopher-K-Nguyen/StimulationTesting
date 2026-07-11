"""Hardware abstraction: Plexon stimulator + Tektronix scope + STM32 bias module, plus simulator backends."""

from .base import (
    Stimulator, Oscilloscope, ScopeAcquisition, StimulatorInfo, ScopeInfo,
)
from .bias_module import (
    BIAS_LOG_DTYPE, BiasLogReadout, BiasModule, BiasModuleInfo,
)
from .bias_simulator import SimulatedBiasModule
from .simulator import SimulatedStimulator, SimulatedOscilloscope

__all__ = [
    "Stimulator", "Oscilloscope", "ScopeAcquisition",
    "StimulatorInfo", "ScopeInfo",
    "BiasModule", "BiasModuleInfo", "BiasLogReadout", "BIAS_LOG_DTYPE",
    "SimulatedStimulator", "SimulatedOscilloscope", "SimulatedBiasModule",
]


def open_stimulator(simulate: bool = False,
                    dll_path: str | None = None) -> Stimulator:
    """Factory: try real Plexon, fall back to simulator if missing or asked for.

    ``dll_path`` overrides the vendored DLL location so the GUI can
    point at a user-installed PlexStim SDK directory remembered across
    sessions. ``None`` uses the bundled copy.
    """
    if simulate:
        return SimulatedStimulator()
    try:
        from .plexon import PlexonStimulator
        return PlexonStimulator(dll_path=dll_path)
    except Exception as e:
        import warnings
        warnings.warn(f"Plexon stimulator unavailable ({e}); using simulator")
        return SimulatedStimulator()


def open_oscilloscope(simulate: bool = False, resource: str | None = None,
                      *, backend: str = "tektronix",
                      pico_series: str = "ps4000a",
                      pico_resolution_bits: int | None = None) -> Oscilloscope:
    """Factory: return a real scope (Tektronix or PicoScope) or the simulator.

    ``backend`` selects the hardware driver: ``"tektronix"`` (default — every
    existing caller/test is unchanged) or ``"pico"`` (the PicoScope block-mode
    backend, :mod:`stimtest.hardware.picoscope`).  ``pico_series`` picks the
    picosdk submodule (``"ps4000a"`` for the 4000-series); ``resource`` is a
    VISA address for Tektronix or a Pico SERIAL string for PicoScope (None =
    first unit).

    Does NOT silently fall back to the simulator when a real scope is requested
    — the caller (connection panel) catches any exception and shows the error
    message, leaving the indicator grey so the user can see what went wrong.
    """
    if simulate:
        return SimulatedOscilloscope()
    if backend == "pico":
        from .picoscope import PicoScopeOscilloscope
        return PicoScopeOscilloscope(series=pico_series, resource=resource,
                                     resolution_bits=pico_resolution_bits)
    from .tektronix import TektronixOscilloscope
    return TektronixOscilloscope(resource=resource)


def open_bias_module(simulate: bool = False,
                     port: str | None = None) -> BiasModule:
    """Factory: return a real STM32 bias module or (if simulate=True) the simulator.

    Matches the no-fallback contract of :func:`open_oscilloscope` —
    the caller (connection panel) catches any exception and surfaces
    the error message; this lets the operator see why the real device
    didn't open rather than silently dropping to simulator data.

    ``port`` is a serial-port name (``"COM5"`` on Windows,
    ``"/dev/ttyACM0"`` on Linux/Mac).  When None and ``simulate=False``,
    the driver tries :func:`stimtest.hardware.stm32_bias.auto_discover`
    and uses the first match.
    """
    if simulate:
        return SimulatedBiasModule()
    from .stm32_bias import STM32BiasModule, auto_discover
    if port is None:
        candidates = auto_discover()
        if not candidates:
            raise RuntimeError(
                "No serial ports detected for STM32 bias module.  "
                "Plug in the Nucleo-G474RE and try again, or pass "
                "an explicit port=...")
        port = candidates[0]
    return STM32BiasModule(port=port)
