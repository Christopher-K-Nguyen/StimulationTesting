"""Session and capture data containers.

These dataclasses replace MATLAB's ``File`` / ``Capture`` / ``Data`` structs.
A *Session* corresponds to one full experiment run (potentially across many
channels). Each test on a single (active, returns) group produces a *ChannelRun*
which contains a list of *Capture* objects (one per stimulus amplitude tried
during the ramp).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import numpy as np

from .electrode import Configuration, ElectrodeArray
from .waveforms import PulsePattern


# ---------------------------------------------------------------------------
# Per-capture data
# ---------------------------------------------------------------------------
@dataclass
class CaptureMetrics:
    """Metrics computed from one captured waveform."""
    driving_voltage_v: float = float("nan")             #: V_d
    access_voltage_per_phase_v: List[float] = field(default_factory=list)
    access_resistance_per_phase_kohm: List[float] = field(default_factory=list)
    polarization_per_phase_v: List[float] = field(default_factory=list)   # active electrode
    return_polarization_per_phase_v: List[float] = field(default_factory=list)
    charge_per_phase_nc: float = float("nan")
    charge_injection_mc_per_cm2: float = float("nan")   #: Q_inj
    effective_capacitance_nf: float = float("nan")
    driving_capacitance_mf_per_cm2: float = float("nan")  #: C_d


@dataclass
class CaptureStatus:
    good: bool = True
    reached_potential_limit: bool = False
    voltage_compliance: bool = False    # stim hit ±9 V rail
    aborted: bool = False
    notes: str = ""


@dataclass
class Capture:
    """One acquisition: stimulus parameters, raw scope traces, derived metrics."""
    index: int
    pattern: PulsePattern
    timestamp: datetime = field(default_factory=datetime.now)

    #: Time vector (microseconds) for all the trace arrays
    time_us: np.ndarray = field(default_factory=lambda: np.zeros(0))
    #: V_mon trace (volts), i.e. (E_act - E_ret) measured by the stimulator
    v_mon_v: np.ndarray = field(default_factory=lambda: np.zeros(0))
    #: I_mon trace (microamps), the stimulus current as recorded by the scope
    i_mon_ua: np.ndarray = field(default_factory=lambda: np.zeros(0))
    #: Return potential vs Ag|AgCl (volts), from instrumentation amp (optional)
    e_ret_v: Optional[np.ndarray] = None
    #: Active potential vs Ag|AgCl (volts), if measured directly (optional)
    e_act_v: Optional[np.ndarray] = None

    metrics: CaptureMetrics = field(default_factory=CaptureMetrics)
    status: CaptureStatus = field(default_factory=CaptureStatus)


# ---------------------------------------------------------------------------
# Per-channel run
# ---------------------------------------------------------------------------
@dataclass
class ChannelRun:
    """All captures for a single (active + returns) group within a session."""
    configuration: Configuration
    surface_area_um2: float = 5000.0
    captures: List[Capture] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None

    @property
    def name(self) -> str:
        return self.configuration.display_name()

    @property
    def max_q_inj(self) -> float:
        if not self.captures:
            return float("nan")
        good = [c.metrics.charge_injection_mc_per_cm2
                for c in self.captures if c.status.good]
        return max(good) if good else float("nan")

    @property
    def max_q_inj_capture(self) -> Optional[Capture]:
        good = [c for c in self.captures if c.status.good]
        if not good:
            return None
        return max(good, key=lambda c: c.metrics.charge_injection_mc_per_cm2)


# ---------------------------------------------------------------------------
# Test parameters and session
# ---------------------------------------------------------------------------
@dataclass
class TestParameters:
    """Top-level experiment parameters (mirrors MATLAB ``File.Test``/``File.Parameters``)."""
    experiment: str                         # 'VT' | 'TV' | 'SP' | 'LP' | 'PS'
    pattern: PulsePattern                   # initial / template pulse pattern
    configuration: Configuration            # for single-channel runs
    array: ElectrodeArray
    duration_s: float = 60.0                # for SP/LP
    number_of_pulses: float = float("inf")
    polarization_method: str = "time"       # 'time' (12 µs after phase) or 'derivative'
    counter_electrode_label: str = "Pt counter"
    reference_electrode_label: str = "Ag|AgCl"
    target_charge_phase_nc: float = float("inf")  # None / inf = ramp until E_pol limit
    interpulse_short: bool = True
    extras: Dict[str, object] = field(default_factory=dict)


@dataclass
class Session:
    """An entire experiment session."""
    notebook: str
    subject: str
    test: TestParameters
    runs: List[ChannelRun] = field(default_factory=list)
    user_name: str = ""
    user_email: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    save_dir: str = ""

    @property
    def name(self) -> str:
        if self.subject:
            return f"{self.notebook}_{self.subject}"
        return self.notebook

    def add_run(self, run: ChannelRun) -> None:
        self.runs.append(run)

    @property
    def total_captures(self) -> int:
        return sum(len(r.captures) for r in self.runs)
