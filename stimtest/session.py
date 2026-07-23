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
    #: Access-resistance DRIFT test (operator: flag a broken / degrading
    #: electrode when the present R_a differs significantly from the previous
    #: ones in the run).  Two-sided Mann-Whitney U p-value of the recent R_a
    #: window vs the run's earlier R_a (0 µA captures excluded); ``flag`` True
    #: when p < alpha AND the median shift is physically meaningful.  NaN /
    #: False until enough history exists.  Computed by the RUNNER (needs the
    #: run's capture history), NOT ``compute_metrics``.  See
    #: :func:`metrics.access_resistance_drift_mannwhitney`.
    access_resistance_drift_p: float = float("nan")
    access_resistance_drift_flag: bool = False
    #: PEAK-CURRENT access voltage / resistance for SMOOTH shaped phases
    #: (gaussian / sinusoidal with no current-step edge) — operator: "change
    #: the discontinuous gaussian and sinusoidal to peak current because there
    #: is no edge."  Parallel-to-phases (NaN where a phase doesn't qualify:
    #: rectangular, an offset/edge shaped phase, or a KHFAC continuous sinusoid
    #: handled by the Ghazavi metrics).  SEPARATE from the edge-based
    #: ``access_*`` fields — does NOT feed the E_pol / water-window decision.
    #: See :func:`metrics.shaped_peak_access`.
    shaped_access_v_per_phase: List[float] = field(default_factory=list)
    shaped_access_r_kohm_per_phase: List[float] = field(default_factory=list)
    return_shaped_access_v_per_phase: List[float] = field(default_factory=list)
    return_shaped_access_r_kohm_per_phase: List[float] = field(default_factory=list)
    polarization_per_phase_v: List[float] = field(default_factory=list)   # active electrode
    return_polarization_per_phase_v: List[float] = field(default_factory=list)
    charge_per_phase_nc: float = float("nan")
    charge_injection_mc_per_cm2: float = float("nan")   #: Q_inj
    #: Number of stimulation pulses delivered to ACQUIRE this capture =
    #: (pulsing duration of the acquisition) × (pulse rate).  For an
    #: averaged scope acquisition this equals the averaging count
    #: (N_avg pulse-triggered frames).  Operator: "Number of pulses
    #: should be the duration of the pulsing multiplied by the pulse
    #: rate" + "per-capture only (≈ the averaging count)".  NaN when the
    #: runner didn't record it (e.g. an empty / pre-acquisition capture).
    n_pulses: float = float("nan")
    #: Cumulative cathodic charge delivered to the electrode up to AND
    #: INCLUDING this capture, building on the previous captures of the
    #: same run: ``Σ |charge_per_phase_nc_i| × n_pulses_i`` over captures
    #: i = 0..this (nC).  Resets per ChannelRun (per channel / combo).
    #: Operator: "I also want cumulative charge at each capture, building
    #: upon previous captures."  NaN when not recorded.
    cumulative_charge_nc: float = float("nan")
    #: Cumulative pulse count delivered up to AND INCLUDING this capture,
    #: building on the previous captures of the same run: ``Σ n_pulses_i``
    #: over captures i = 0..this.  Resets per ChannelRun.  Operator: "add
    #: cumulative N_pulse to the metric table above cumulative Q".  NaN when
    #: n_pulses wasn't recorded (SP/CP/LP continuous pulsing).
    cumulative_n_pulses: float = float("nan")
    #: Scheduled ("fixed") sample time in seconds — the cadence-grid time
    #: this capture AIMED for (n × sampling_interval), anchored to the run
    #: start.  This is the precise-timing reference: a fixed grid the
    #: sampler targets regardless of how long the previous capture took
    #: (port of MATLAB ``runLongPulsing.m`` ``timestamp_arr = 0:periodic:
    #: duration`` checked against ``toc(startTime)`` — keeps the sampling
    #: precise WITHOUT the MATLAB ``rateControl`` object, which did NOT
    #: keep precise time).  Set by the periodic-sampling runners (PS, LP);
    #: NaN for adaptive runners (VT) that have no fixed cadence.  Operator:
    #: "have the fixed time and elapsed time columns."  Compare against
    #: :attr:`elapsed_time_s` to read scheduling jitter.
    scheduled_time_s: float = float("nan")
    #: Actual measured elapsed time in seconds when this capture was taken,
    #: from a MONOTONIC clock anchored at the run start.  The real-world
    #: counterpart to :attr:`scheduled_time_s`.  For LP the anchor is
    #: PULSING time (re-characterization windows are excluded, mirroring
    #: MATLAB's ``startTime = startTime + endPause`` pause compensation);
    #: for PS it is total run time across all amplitude steps.  NaN when
    #: not recorded.
    elapsed_time_s: float = float("nan")
    #: Pulse-derived effective capacitance C_eff = |I| / |dV/dt| (nF),
    #: computed from the linear charging-ramp slope of the excitation
    #: phase.  Populated ONLY when the response is classified
    #: ``"capacitive"`` (a clean linear ramp with NO resistive access
    #: step) or ``"open"`` (V_mon railed / electrode open/broken) — i.e.
    #: the cases where there is NO separable resistive+Faradaic component,
    #: so I/(dV/dt) IS meaningful (operator: "show capacitance since it is
    #: so linear … but only when the response is entirely capacitive, open
    #: circuit, or broken").  NaN for a NORMAL mixed electrode, where a
    #: galvanostatic VT cannot separate capacitive from Faradaic current
    #: (Harris 2024) so the value would be misleading.  When this is
    #: finite, ``access_*`` and ``polarization_per_phase_v`` are
    #: deliberately EMPTY (no access V/R or E_pol for a pure-capacitive /
    #: open response).
    effective_capacitance_nf: float = float("nan")
    #: Parallel-R‖C fit of a BROKEN (exponential) response
    #: ``V = V∞·(1 − e^(−t/τ))`` → the leakage/Faradaic resistance
    #: ``R = V∞/I`` (kΩ) and time constant ``τ = RC`` (µs).  Populated ONLY
    #: for ``response_class == "broken"`` (the exponential model fits well);
    #: NaN for open / capacitive (pure capacitance — a straight ramp, τ→∞,
    #: no resistance, per Ghazavi et al. 2025 parasitic-capacitance) and for
    #: normal.  ``effective_capacitance_nf`` holds the C in all three bad
    #: classes (parasitic C for open, electrode C for capacitive, RC-fit C
    #: for broken).
    rc_fit_resistance_kohm: float = float("nan")
    rc_fit_tau_us: float = float("nan")
    #: PER-PHASE bad-response metrics (operator: "for broken and open
    #: channels, compute the same metrics for other phases") — one entry
    #: per pattern phase, NaN where that phase's fit failed; EMPTY for a
    #: normal response.  Entry 0 mirrors the scalar fields above.  For
    #: BROKEN these are the per-phase R‖C fit values, computed on E_act
    #: when it's recorded (directly or derived V_mon+E_ret), else V_mon
    #: (operator: "broken channels should be based on Eact, if possible");
    #: for OPEN / CAPACITIVE only the C list is populated (per-phase linear
    #: ``I/(dV/dt)``; R / τ stay NaN — pure capacitance).
    effective_capacitance_per_phase_nf: List[float] = field(default_factory=list)
    rc_fit_resistance_per_phase_kohm: List[float] = field(default_factory=list)
    rc_fit_tau_per_phase_us: List[float] = field(default_factory=list)
    #: Response classification driving the conditional metrics above:
    #: ``"normal"`` (resistive + capacitive + Faradaic → access V/R + E_pol),
    #: ``"capacitive"`` / ``"open"`` (straight ramp → parasitic/electrode C
    #: only), or ``"broken"`` (exponential → R‖C fit: R + C + τ).
    response_class: str = "normal"
    driving_capacitance_mf_per_cm2: float = float("nan")  #: C_d
    #: Harris 2019 chronopotentiometry capacitive/Faradaic decomposition of the
    #: driving (excitation) phase (``metrics.chronopotentiometry_charge_transfer``).
    #: ``c_dl`` (double-layer capacitance, mF/cm²) is from the constant-dE/dt
    #: capacitive window ONLY (defensible, unlike the removed full-pulse C_eff);
    #: the charge split is FIRST-ORDER / APPROXIMATE.  All NaN unless the capture
    #: is NORMAL + rectangular + has a surface area.  ``faradaic_onset_*`` is
    #: where dE/dt first dips below the capacitive baseline (NaN = ~purely
    #: capacitive phase, no resolvable Faradaic onset).
    c_dl_mf_per_cm2: float = float("nan")            #: C_dl (double-layer)
    faradaic_onset_us: float = float("nan")
    faradaic_onset_v: float = float("nan")
    capacitive_charge_nc: float = float("nan")
    faradaic_charge_nc: float = float("nan")
    faradaic_fraction: float = float("nan")
    #: Driving impedance Z_d = |V_d| / |I_stim| (kΩ) — the total impedance
    #: the stimulator drives into during the driving phase (access
    #: resistance + electrode polarization), driving voltage ÷ programmed
    #: stimulus current.  Operator request.  NaN when V_d or I_stim is
    #: unavailable.
    driving_impedance_kohm: float = float("nan")
    #: Driving energy = ∫ V_mon·I_mon dt over the WHOLE pulse (all phases,
    #: µJ) — the electrical energy delivered to the electrode per pulse.
    #: Integrated across every phase window (the passive interphase /
    #: discharge gaps carry ~0 current so contribute ~0).  Operator
    #: request.  NaN when the traces are unavailable.
    driving_energy_uj: float = float("nan")
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
    #: Which polarization method produced ``polarization_per_phase_v``:
    #: ``"pulsed"`` (the default — access-voltage extrapolation / operator
    #: E_pol, for pulsed waveforms with recovery gaps) or ``"sinusoidal"``
    #: (the Ghazavi & Cogan 2018 phase decomposition, for a CONTINUOUS
    #: symmetric-biphasic sinusoid with no interphase / discharge / interpulse
    #: delays — KHFAC).  When ``"sinusoidal"``, ``polarization_per_phase_v`` =
    #: ``[E_mc, E_ma]`` (the max cathodic / anodic excursions) so the existing
    #: water-window limit + VT-max ramp find max charge injection unchanged,
    #: and the ``ghazavi_*`` fields below carry the decomposition.
    polarization_method: str = "pulsed"
    #: Depolarization time delay (µs) used by the operator E_pol TIME method
    #: (``phase_end + depol``) — the settling window after a phase before the
    #: interface potential is read.  Default = ``config.DEPOLARIZATION_TIME_US``
    #: (12 µs, IEEE NER / MATLAB); the operator can override it per run via the
    #: Setup-tab "custom E_pol time delay" toggle.  Stored per capture so the
    #: plot markers + POLARIS use the SAME delay the value was computed with.
    depolarization_us: float = 12.0
    #: Ghazavi max cathodic (E_mc) / max anodic (E_ma) electrode-potential
    #: excursions (V) from the sinusoidal phase decomposition — the KHFAC
    #: analogue of the pulsed E_mc/E_ma.  ``E_mc = E_off − E_io``,
    #: ``E_ma = E_off + E_io``.  NaN unless ``polarization_method ==
    #: "sinusoidal"``.
    ghazavi_e_mc_v: float = float("nan")
    ghazavi_e_ma_v: float = float("nan")
    #: Ghazavi interface polarization amplitude E_io (V) = the AC magnitude of
    #: the interface potential E_i(t) = V_m(t) − R_access·I(t).  Half the peak-
    #: to-peak interface excursion.
    ghazavi_e_io_v: float = float("nan")
    #: Ghazavi offset potential E_off (V) = mean measured electrode potential
    #: over the capture — the DC operating point of the interface under
    #: continuous excitation.
    ghazavi_e_off_v: float = float("nan")
    #: Ghazavi access resistance R_access (kΩ) = the least-squares resistive
    #: projection of V_m onto I (in-phase / electrolyte-resistance term),
    #: identical to their V_ro/I₀ under the ideal-capacitor (δ = −π/2)
    #: assumption.
    ghazavi_r_access_kohm: float = float("nan")
    #: Access-voltage amplitude V_ro = R_access·I_o (Ghazavi eq 4; = their
    #: in-phase V_mo·cos φ).  NaN unless sinusoidal.
    ghazavi_v_access_v: float = float("nan")
    #: Fundamental sinusoid frequency (kHz) used for the decomposition,
    #: measured from the I_mon trace (falls back to the pattern rate).
    ghazavi_freq_khz: float = float("nan")
    #: RETURN-electrode Ghazavi decomposition (E_ret trace vs I_mon) — the
    #: same quantities for the return/counter electrode, reported alongside
    #: the active set (like the pulsed active/return split).  NaN unless a
    #: continuous sinusoid AND E_ret is recorded.
    ghazavi_return_e_mc_v: float = float("nan")
    ghazavi_return_e_ma_v: float = float("nan")
    ghazavi_return_e_io_v: float = float("nan")
    ghazavi_return_e_off_v: float = float("nan")
    ghazavi_return_r_access_kohm: float = float("nan")
    ghazavi_return_v_access_v: float = float("nan")
    #: Phase angle (degrees) of each VOLTAGE waveform relative to I_mon at
    #: the drive frequency — the impedance/EIS phase (single-bin lock-in;
    #: ~0° = resistive, →−90° = capacitive, V lags I).  Populated ONLY for
    #: a continuous sinusoid (``polarization_method == "sinusoidal"``); NaN
    #: otherwise.  E_ret / E_act NaN unless those traces are recorded (or
    #: E_act is derivable from V_mon + E_ret).
    phase_angle_vmon_deg: float = float("nan")
    phase_angle_eret_deg: float = float("nan")
    phase_angle_eact_deg: float = float("nan")
    #: Galvanostatic-EIS point — the complex electrode impedance Z(f) at this
    #: capture's probe frequency (one capture per swept frequency).  Populated
    #: ONLY by the Galvanostatic EIS experiment; NaN otherwise.  ``z_mag`` in
    #: ohms, ``z_phase`` in degrees (EIS: ~0° resistive, →−90° capacitive),
    #: ``z_real``/``z_imag`` the Nyquist coordinates (Ω; Nyquist plots −z_imag).
    eis_freq_hz: float = float("nan")
    z_mag_ohm: float = float("nan")
    z_phase_deg: float = float("nan")
    z_real_ohm: float = float("nan")
    z_imag_ohm: float = float("nan")


@dataclass
class CaptureStatus:
    good: bool = True
    #: E_pol landed WITHIN the acceptance band centered on the water-window
    #: limit (|E_pol| within ±tol of |limit|) — a clean stop at the limit.
    reached_potential_limit: bool = False
    #: E_pol went PAST the band's far edge (|E_pol| > |limit| + tol) — an
    #: OVERSHOOT beyond the water window (operator: "I saw a channel stop
    #: at -0.644 V … and said limit reached" — that is 'exceeded', not
    #: 'reached').  Distinct flag so the operator can tell a clean stop
    #: from an overshoot that the ramp should have backed off from.
    exceeded_potential_limit: bool = False
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
    #: Optional sweep-point tag (e.g. "200pps_asym2x"). Empty for a
    #: plain single-parameter run. Set by the runner when a
    #: multi-parameter sweep produces several runs for the same
    #: configuration so they stay distinguishable in the saved session
    #: and the Results tab.
    label: str = ""

    @property
    def name(self) -> str:
        base = self.configuration.display_name()
        return f"{base} — {self.label}" if self.label else base

    @property
    def duration_s(self) -> float:
        """Wall-clock seconds to COMPLETE this channel/combo — from the run's
        ``started_at`` (stamped when its ``ChannelRun`` is constructed, right
        before its captures begin) to the ``finished_at`` stamp the runner
        writes on completion.  NaN until the run has finished (or when either
        timestamp is missing on a legacy archive).  Surfaced in the POLARIS
        channel/combo metric table + the Gamry electrode sheet (operator:
        "add time elapsed for complete channel/combo on the metric
        measurements")."""
        if self.finished_at is None or self.started_at is None:
            return float("nan")
        try:
            return (self.finished_at - self.started_at).total_seconds()
        except Exception:
            return float("nan")

    @staticmethod
    def _counts_toward_max_qinj(c: "Capture") -> bool:
        """A capture counts toward max(Q_inj) only if it's a good measurement
        AND its E_pol did NOT overshoot the water window.  An OVERSHOOT
        (``exceeded_potential_limit``) is PAST the safe operating range, so
        reporting its (higher) Q_inj as the achieved maximum would overstate
        the injectable capacity — the VT ramp backs off from it to land
        in-band (gotcha: bidirectional back-off), and only the in-band /
        below-band captures are valid operating points.  Legacy captures
        default ``exceeded_potential_limit = False`` → unchanged."""
        return bool(c.status.good) and not getattr(
            c.status, "exceeded_potential_limit", False)

    @property
    def max_q_inj(self) -> float:
        if not self.captures:
            return float("nan")
        good = [c.metrics.charge_injection_mc_per_cm2
                for c in self.captures if self._counts_toward_max_qinj(c)]
        return max(good) if good else float("nan")

    @property
    def max_q_inj_capture(self) -> Optional[Capture]:
        good = [c for c in self.captures if self._counts_toward_max_qinj(c)]
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
    counter_electrode_label: str = "Pt"        # operator: just "Pt", not "Pt counter"
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
    #: Operator-typed free-text notes attached to this session.  Set
    #: from the Test Parameters panel's "Notes" textarea before /
    #: during / after the run; persisted in the .npz so it's
    #: searchable in POLARIS.  Multi-line; no length cap on the
    #: Python side (JSON in meta.json scales fine into the KB range).
    notes: str = ""
    #: Operator-set categorical tags ("post-coating", "control",
    #: "discard", "pilot", etc.).  Stored as a list of normalized
    #: lowercase strings; POLARIS filters / groups sessions by tag.
    #: Set from the Test Parameters "Tags" line edit (comma-separated
    #: in the UI, list under the hood).
    tags: List[str] = field(default_factory=list)
    #: Reproducibility metadata captured at run time — PULSAR git
    #: hash, Python + key package versions, OS info, hardware
    #: serials/firmware, setup-snapshot hash.  Populated by
    #: :func:`stimtest.session_metadata.capture_system_metadata`
    #: at runner construction.  Stored as a flat str → str dict so
    #: JSON round-trip is trivial; values are stringified at capture
    #: time.  Months later, "what version of PULSAR produced this
    #: .npz?" is answerable without git archeology.
    system_metadata: Dict[str, str] = field(default_factory=dict)

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
