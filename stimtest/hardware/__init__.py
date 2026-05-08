"""Hardware abstraction: Plexon stimulator + Tektronix scope, plus a simulator backend."""

from .base import (
    Stimulator, Oscilloscope, ScopeAcquisition, StimulatorInfo, ScopeInfo,
)
from .simulator import SimulatedStimulator, SimulatedOscilloscope

__all__ = [
    "Stimulator", "Oscilloscope", "ScopeAcquisition",
    "StimulatorInfo", "ScopeInfo",
    "SimulatedStimulator", "SimulatedOscilloscope",
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


def open_oscilloscope(simulate: bool = False, resource: str | None = None) -> Oscilloscope:
    """Factory: try real Tek scope (auto-detect), fall back to simulator."""
    if simulate:
        return SimulatedOscilloscope()
    try:
        from .tektronix import TektronixOscilloscope
        return TektronixOscilloscope(resource=resource)
    except Exception as e:
        import warnings
        warnings.warn(f"Tektronix scope unavailable ({e}); using simulator")
        return SimulatedOscilloscope()
