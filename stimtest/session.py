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
    driving_voltage_v: float = float("nan")             #: V_d (legacy scalar = max |V_mon|)
    #: Driving voltage per phase, ACTIVE electrode side (V_d_act). One
    #: entry per phase: |E_act_pre - E_act_drive|, where the pre value
    #: is the rest baseline (pre-pulse for phase 1, end-of-prior-
    #: interphase for later phases) and the drive value is sampled
    #: just before the phase ends. Falls back to V_mon when the
    #: instrumentation-amp E_act trace isn't available.
    active_driving_voltage_per_phase_v: List[float] = field(default_factory=list)
    #: Driving voltage per phase, RETURN electrode side (V_d_ret).
    #: Same calculation against E_ret. Empty when E_ret isn't recorded.
    return_driving_voltage_per_phase_v: List[float] = field(default_factory=list)
    access_voltage_per_phase_v: List[float] = field(default_factory=list)
    access_resistance_per_phase_kohm: List[float] = field(default_factory=list)
    #: Return-electrode access voltage per phase boundary. Same
    #: convention as ``access_voltage_per_phase_v`` (one entry per
    #: shape-aware boundary in :func:`access_index_labels`) but
    #: computed off the E_ret trace so the user sees the access drop
    #: on each side of the bipolar pair separately. Empty when the
    #: instrumentation-amp E_ret trace isn't recorded.
    return_access_voltage_per_phase_v: List[float] = field(default_factory=list)
    return_access_resistance_per_phase_kohm: List[float] = field(default_factory=list)
    polarization_per_phase_v: List[float] = field(default_factory=list)   # active electrode
    return_polarization_per_phase_v: List[float] = field(default_factory=list)
    charge_per_phase_nc: float = float("nan")
    charge_injection_mc_per_cm2: float = float("nan")   #: Q_inj
    effective_capacitance_nf: float = float("nan")
    driving_capacitance_mf_per_cm2: float = float("nan")  #: C_d
    #: Interpulse potential (V vs Ag|AgCl) — average of the E_ret
    #: trace OUTSIDE the active pulse, i.e. across the time-segments
    #: where ``time < 0`` (pre-pulse baseline) and
    #: ``time >= total_pulse_us`` (post-pulse, after discharge).
    #: Mirrors getVoltageMetrics.m's ``prePulsePotenial`` calc but
    #: averages over BOTH the pre- and post-pulse intervals so the
    #: result reflects the true settled inter-pulse rest potential.
    interpulse_potential_v: float = float("nan")
    #: Pre-pulse rest potential (V vs Ag|AgCl) — mean of the E_ret
    #: trace where ``time < 0``. Stored separately from the combined
    #: :attr:`interpulse_potential_v` so the runner can feed each
    #: half into :func:`stimtest.electrode_potential_history.record_sample`
    #: with a ``phase`` tag, and so a future drift-watcher can
    #: compare pre vs post on a per-capture basis. NaN if no
    #: pre-pulse samples were captured (e.g. the scope started
    #: acquiring at t = 0).
    return_pre_pulse_potential_v: float = float("nan")
    #: Post-pulse rest potential (V vs Ag|AgCl) — mean of the E_ret
    #: trace where ``time >= total_pulse_us``. Same role as
    #: :attr:`return_pre_pulse_potential_v` but for the discharge-
    #: tail window. NaN when the captured frame ends inside the
    #: active pulse (no rest samples after the last phase boundary).
    return_post_pulse_potential_v: float = float("nan")
    #: Shannon equation k-value for this capture
    #: (``log10(charge_density) + log10(charge_per_phase)`` with
    #: charge density in µC/cm²/ph and charge in µC/ph). Computed
    #: from :attr:`charge_per_phase_nc` and
    #: :attr:`charge_injection_mc_per_cm2`; NaN when either input
    #: is non-positive (e.g. an empty / pre-acquisition capture).
    #: See :mod:`stimtest.damage_models` for the full reference list.
    shannon_k_value: float = float("nan")
    #: Single-string tissue-damage classification combining Shannon
    #: + modified-Shannon caps; one of ``"likely_safe"``,
    #: ``"above_shannon"``, ``"above_macro_cap"``,
    #: ``"above_micro_cap"``, or ``"insufficient_data"``. Treat as
    #: GUIDANCE — per Li et al. 2024 the underlying Shannon model
    #: misclassifies ~36% of damaging stimulation as safe; see the
    #: NeurostimML web tool referenced in :mod:`stimtest.damage_models`
    #: for a higher-accuracy alternative.
    damage_classification: str = "insufficient_data"
    #: Per-criterion booleans behind :attr:`damage_classification`.
    #: Lets the UI render multiple badges ("above Shannon AND above
    #: macro cap") rather than collapsing to one verdict. Keys are
    #: ``"shannon"``, ``"macro_cap"``, ``"micro_cap"``; values are
    #: ``True`` when that criterion was crossed.
    damage_criteria: Dict[str, bool] = field(default_factory=dict)
    #: Electrode size band — ``"macro"`` (GSA > 0.03 cm²),
    #: ``"micro"`` (GSA < 2000 µm²), or ``"meso"`` (between).
    #: Provided so the UI can explain WHY a particular modified-
    #: Shannon cap did or didn't apply (the macro cap is gated on
    #: the macro band, the micro cap on the micro band, etc.).
    damage_band: str = ""
    #: Coarse 0-4 histological / functional damage level following
    #: the McCreery / Shepherd / Li et al. convention (0 = no damage,
    #: 4 = severe). The Shannon screen is binary, so this is a
    #: two-value mapping today: ``0`` for ``"likely_safe"``, ``2``
    #: for any ``"above_*"`` verdict (mild damage — the binarisation
    #: threshold). ``-1`` = "no verdict" (insufficient data); kept
    #: as ``-1`` rather than ``None`` so the field stays a plain
    #: int that round-trips through JSON / numpy without
    #: special-casing. See
    #: :data:`stimtest.damage_models.DAMAGE_LEVELS` for the
    #: full 0-4 scale and prose descriptions.
    damage_level: int = -1
    #: NeurostimML (Li et al. 2024 RF-Partial-19) verdict for this
    #: capture — ``"likely_safe"`` / ``"likely_damaging"`` /
    #: ``"model_not_installed"``. Populated only when the local
    #: model has been installed via Help → Install NeurostimML
    #: model… (see :mod:`stimtest.neurostimml`). Independent of the
    #: Shannon-based :attr:`damage_classification` so the UI can
    #: render both verdicts side-by-side.
    neurostimml_classification: str = "model_not_installed"
    #: Probability of damage from the NeurostimML model (0.0-1.0),
    #: when available. NaN when the model isn't installed or the
    #: feature vector couldn't be assembled (e.g. missing pulse
    #: rate or duty cycle). Distinct from
    #: :attr:`shannon_k_value` so a reader cross-comparing the two
    #: classifiers can see both numerical outputs.
    neurostimml_probability: float = float("nan")


@dataclass
class CaptureStatus:
    good: bool = True
    reached_potential_limit: bool = False
    voltage_compliance: bool = False    # stim hit ±12 V rail
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

    #: Optional tag distinguishing capture categories so the GUI can
    #: group them into sub-tabs (e.g. ``"pre_char"`` / ``"post_char"``
    #: for Short-Term Pulsing characterization, ``"snapshot"`` for
    #: mid-pulsing waveform grabs, ``"ramp_step"`` for VT amplitude
    #: ramps). Empty string means "default / unclassified" — the
    #: MultiChannelScope renders these in its flat capture history.
    kind: str = ""


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
    # Stop pytest from treating this as a test class because the name
    # begins with "Test". The dataclass is data, not a fixture.
    __test__ = False
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
