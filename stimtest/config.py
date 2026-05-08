"""Project-wide constants and electrode catalog.

Mirrors the implicit defaults sprinkled through the MATLAB code (SIROF water
window, Plexon scaling factors, depolarization measurement time, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict


# ---------------------------------------------------------------------------
# Plexon PlexStim hardware constants
# ---------------------------------------------------------------------------
# From initializeStimulator.m: certain serial numbers belong to the "NIL"
# devices which use different monitor scaling.
# - PLX00078, PLX00089, PLX00161: original NIL list inherited from MATLAB.
# - PLX00180: confirmed bench-tested 2026-05; outputs 1 V/V on V_mon and
#   1 mV/µA on I_mon (matches the NIL constants below).
NIL_SERIAL_NUMBERS = ("PLX00078", "PLX00089", "PLX00161", "PLX00180")

#: Default voltage-monitor scaling, V/V (V_mon = stim_voltage * scale)
VMON_SCALING_DEFAULT = 0.25
VMON_SCALING_NIL = 1.0

#: Default current-monitor scaling, V/µA
IMON_SCALING_DEFAULT = 2.5e-3   # V_mon line shows 2.5 mV per µA of stim current
IMON_SCALING_NIL = 1e-3

#: Plexon stimulator output current resolution (μA per LSB)
STIM_CURRENT_RESOLUTION_UA = 0.1

#: Plexon stimulator phase-width / interphase / discharge time resolution (μs)
STIM_TIME_RESOLUTION_US = 1.0

#: Plexon PlexStim 2.0 maximum stimulation amplitude per channel (µA).
#: From the PlexStim 2.0 datasheet — current source rails at ~1 mA/channel
#: regardless of compliance voltage. Used as the default ceiling for
#: amplitude ramps so the runner doesn't ask the device for current it
#: cannot deliver.
STIM_MAX_AMPLITUDE_UA = 1000.0

#: Plexon PlexStim 2.0 voltage compliance (V).
#: V_mon will saturate at ~±12 V when the load demands more voltage than
#: the stimulator can supply. Crossing this means the device is no longer
#: actually delivering the programmed current.
STIM_VOLTAGE_COMPLIANCE_V = 12.0

#: Plexon's typical digital trigger / pattern start delay (µs)
DIGITAL_DELAY_US = 1.5


# ---------------------------------------------------------------------------
# Measurement defaults
# ---------------------------------------------------------------------------
#: Time after end of phase at which E_pol is sampled (µs).
DEPOLARIZATION_TIME_US = 12.0

#: Default discharge delay between trains (µs) — gives time to capture the
#: final-phase polarization before auto-discharge.
DEFAULT_DISCHARGE_DELAY_US = 20.0

#: Default interphase delay (µs)
DEFAULT_INTERPHASE_DELAY_US = 20.0

#: Default phase width (µs) used in the IEEE NER paper
DEFAULT_PHASE_WIDTH_US = 200.0

#: Default pulse rate (pulses per second)
DEFAULT_RATE_PPS = 50.0


# ---------------------------------------------------------------------------
# Electrode coating catalog (vs Ag|AgCl water-window limits)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Coating:
    """Electrochemical safety limits and per-area properties for a coating.

    Conventions:
    * ``name`` is the **short / abbreviated** form (``"SIROF"``,
      ``"Pt"``, ``"PtIr (90/10)"`` etc.) — that's what gets persisted to
      ``ElectrodePosition.coating`` and shows up in saved data files.
    * ``display_name`` is the **spelled-out** form with the abbreviation
      in parentheses (``"Sputtered iridium oxide film (SIROF)"``) — used
      as the visible label in the GUI dropdown. If ``display_name`` is
      empty the GUI falls back to ``name``.
    """
    name: str
    cathodic_limit_v: float       #: E_lc, V vs Ag|AgCl
    anodic_limit_v: float         #: E_la, V vs Ag|AgCl
    typical_csc_mc_per_cm2: float = 0.0  #: charge-storage capacity (slow CV)
    notes: str = ""
    display_name: str = ""


COATINGS: Dict[str, Coating] = {
    "SIROF": Coating(
        name="SIROF",
        display_name="Sputtered iridium oxide film (SIROF)",
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=36.5,
        notes="Sputtered iridium oxide film; per IEEE NER 2025 paper",
    ),
    "AIROF": Coating(
        name="AIROF",
        display_name="Activated iridium oxide film (AIROF)",
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=25.0,
        notes="Activated iridium oxide film; similar window to SIROF",
    ),
    "TiN": Coating(
        name="TiN",
        display_name="Titanium nitride (TiN)",
        cathodic_limit_v=-0.9,
        anodic_limit_v=+0.9,
        typical_csc_mc_per_cm2=2.5,
        notes="Titanium nitride; high CSC via porous morphology",
    ),
    "Pt": Coating(
        name="Pt",
        display_name="Platinum (Pt)",
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=0.3,
        notes="Bare platinum",
    ),
    "PtIr (90/10)": Coating(
        name="PtIr (90/10)",
        display_name="Platinum-iridium (PtIr) 90/10",
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=0.4,
        notes="Pt-Ir alloy 90:10",
    ),
    "PtIr (80/20)": Coating(
        name="PtIr (80/20)",
        display_name="Platinum-iridium (PtIr) 80/20",
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=0.5,
        notes="Pt-Ir alloy 80:20",
    ),
    "PtIr (70/30)": Coating(
        name="PtIr (70/30)",
        display_name="Platinum-iridium (PtIr) 70/30",
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=0.6,
        notes="Pt-Ir alloy 70:30",
    ),
    "PEDOT:PSS": Coating(
        name="PEDOT:PSS",
        display_name="Poly(3,4-ethylenedioxythiophene) (PEDOT:PSS)",
        cathodic_limit_v=-0.9,
        anodic_limit_v=+0.6,
        typical_csc_mc_per_cm2=15.0,
        notes="Conductive polymer; mixed ionic/electronic conduction",
    ),
    "Au": Coating(
        name="Au",
        display_name="Gold (Au)",
        cathodic_limit_v=-0.8,
        anodic_limit_v=+1.1,
        notes="Bare gold",
    ),
    "W": Coating(
        name="W",
        display_name="Tungsten (W)",
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=0.05,
        notes="Bare tungsten — narrow water window, low CSC",
    ),
}


# ---------------------------------------------------------------------------
# Connector catalog (Omnetics pinouts mirrored from MATLAB getDeviceType.m)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Connector:
    """Maps a connector pin index (1..N) to the logical device channel.

    Reproduces the channel-permutation arrays in ``getDeviceType.m``:
    - ``omneticsUTD`` is the identity (1..16).
    - ``omneticsNNX`` reorders the same pins for NeuroNexus headstages.
    - ``utd_plexon`` is the PlexStim port-side mapping for UTD-style boards.
    """
    name: str
    description: str
    pin_to_channel: tuple  #: 1-based logical channel for connector pin i (1-based)


CONNECTORS: Dict[str, Connector] = {
    "Omnetics UTD": Connector(
        name="Omnetics UTD",
        description="Standard 16-pin Omnetics for UTD/Blackrock/MicroProbes boards",
        pin_to_channel=tuple(range(1, 17)),
    ),
    "Omnetics NNX": Connector(
        name="Omnetics NNX",
        description="NeuroNexus pinout (re-ordered Omnetics 16)",
        pin_to_channel=(1, 2, 3, 4, 13, 14, 15, 16, 5, 6, 7, 8, 9, 10, 11, 12),
    ),
    "Plexon (UTD)": Connector(
        name="Plexon (UTD)",
        description="PlexStim port-side ordering for UTD-style boards",
        pin_to_channel=(15, 13, 11, 9, 7, 5, 3, 1, 16, 14, 12, 10, 8, 6, 4, 2),
    ),
    "Other": Connector(
        name="Other",
        description="Pass-through (1:1) mapping for custom connectors",
        pin_to_channel=tuple(range(1, 17)),
    ),
}


# ---------------------------------------------------------------------------
# Test-device catalog (mirrors MATLAB getDeviceType.m)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DeviceDef:
    """Definition of a test device — array geometry + channel mapping.

    ``mapping`` is a 2-D grid of 1-based channel numbers. ``0`` means
    *empty* (large gap) and is preserved as 0 in the resulting
    :class:`ElectrodeArray` so it can be drawn correctly. Mirrors the
    ``channelMapping`` matrices in ``matlab_reference/getDeviceType.m``.

    ``layout`` controls how the GUI renders the geometry — the table is
    always rectangular (it's the only sane way to author the mapping by
    hand) but real devices may be packed differently:

      * ``"rect"`` — standard square/rectangular grid, every cell on the
        same x/y lattice. Default.
      * ``"triangular"`` — equilateral triangular packing. Odd rows are
        offset by half a cell horizontally and the vertical row spacing
        is ``cell × √3/2`` so adjacent disks are equidistant. Used for
        MicroProbes FMA-style arrays where the tabular form is a
        best-effort stand-in for a triangular layout.
    """
    name: str
    description: str
    mapping: tuple              #: tuple of tuples, 1-based ch numbers; 0 = empty
    default_connector: str = "Omnetics UTD"
    default_surface_area_um2: float = 5000.0   # SIROF UEA, IEEE NER paper
    default_coating: str = "SIROF"
    layout: str = "rect"        #: "rect" | "triangular"


DEVICES: Dict[str, DeviceDef] = {
    # Order: simplest geometry first, then named arrays, UTD MEA right
    # before Other (it's a research-only stand-in that's rarely the
    # default pick).
    "Linear": DeviceDef(
        name="Linear",
        description="Linear test array — single column of electrodes "
                    "rendered top-to-bottom. Use the row-count spinner "
                    "to set the number of electrodes (the column count "
                    "stays at 1).",
        mapping=tuple((n,) for n in range(1, 17)),   # 16 rows × 1 col
        default_connector="Omnetics UTD",
        default_surface_area_um2=5000.0,
        default_coating="SIROF",
    ),
    "Blackrock Omnetics (4×4)": DeviceDef(
        name="Blackrock Omnetics (4×4)",
        description="16-channel 4×4 SIROF Utah Electrode Array (UEA), "
                    "viewed from pad side, wire bundle at bottom. "
                    "Used in IEEE NER 2025 paper.",
        mapping=(
            (13, 14, 15, 16),
            ( 9, 10, 11, 12),
            ( 5,  6,  7,  8),
            ( 1,  2,  3,  4),
        ),
        default_connector="Omnetics UTD",
        default_surface_area_um2=5000.0,
        default_coating="SIROF",
    ),
    "Blackrock PCB (4×4)": DeviceDef(
        name="Blackrock PCB (4×4)",
        description="16-channel 4×4 PCB-mounted Blackrock array, "
                    "from pad side, wire bundle at bottom.",
        mapping=(
            (8, 16,  9, 1),
            (7, 15, 10, 2),
            (6, 14, 11, 3),
            (5, 13, 12, 4),
        ),
        default_connector="Omnetics UTD",
        default_surface_area_um2=5000.0,
        default_coating="SIROF",
    ),
    "MicroProbes 16-channel FMA": DeviceDef(
        name="MicroProbes 16-channel FMA",
        description="16-channel MicroProbes FMA. The physical electrode "
                    "spacing is an equilateral triangular grid; the table "
                    "below is the closest rectangular approximation, and "
                    "the geometry view renders it with the correct "
                    "row-offset packing. Zeros mark empty cells.",
        mapping=(
            (16, 15, 14,  0,  0,  0),
            (13, 12, 11, 10,  9,  0),
            ( 0,  8,  7,  6,  5,  0),
            ( 0,  0,  4,  3,  2,  1),
        ),
        default_connector="Omnetics UTD",
        default_surface_area_um2=2000.0,
        default_coating="AIROF",
        layout="triangular",
    ),
    "NeuroNexus A4×4": DeviceDef(
        name="NeuroNexus A4×4",
        description="16-channel 4-shank NeuroNexus A4×4 array, "
                    "top-to-bottom of shank. 0 = inter-shank gap.",
        mapping=(
            (1, 0,  7, 0, 13, 0, 14),
            (3, 0,  4, 0, 10, 0, 16),
            (2, 0,  8, 0,  2, 0, 11),
            (6, 0,  5, 0,  9, 0, 15),
        ),
        default_connector="Omnetics NNX",
        default_surface_area_um2=413.0,
        default_coating="AIROF",
    ),
    "UTD MEA": DeviceDef(
        name="UTD MEA",
        description="UTD 16-channel, 4-shank array (top to bottom of shank). "
                    "Zeros denote large inter-shank gaps.",
        mapping=(
            (15, 0, 13, 0, 11, 0,  9),
            ( 8, 0,  6, 0,  4, 0,  2),
            ( 7, 0,  5, 0,  3, 0,  1),
            (16, 0, 14, 0, 12, 0, 10),
        ),
        default_connector="Omnetics UTD",
        default_surface_area_um2=2000.0,
        default_coating="AIROF",
    ),
    "Other (custom grid)": DeviceDef(
        name="Other (custom grid)",
        description="User-defined N×M grid; edit the channel-map table directly.",
        mapping=((1, 2, 3, 4),
                 (5, 6, 7, 8),
                 (9, 10, 11, 12),
                 (13, 14, 15, 16)),
        default_connector="Other",
        default_surface_area_um2=5000.0,
        default_coating="SIROF",
    ),
}


# ---------------------------------------------------------------------------
# Experiment catalog — drives the "next tab" picker on the Setup tab
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExperimentDef:
    """One row in the experiment dropdown on the Setup tab."""
    code: str          #: "VT" / "SP" / "LP" / "PS"
    label: str         #: dropdown label (plain text)
    tab_title: str     #: matching tab title in the QTabWidget
    blurb: str         #: one-line description shown next to the dropdown


EXPERIMENTS: Dict[str, ExperimentDef] = {
    "VT": ExperimentDef("VT", "Voltage Transient (characterization)",
                        "Voltage Transient",
                        "Iterative I_stim sweep to find max Q_inj per electrode."),
    "SP": ExperimentDef("SP", "Short-Term Pulsing",
                        "Short-Term Pulsing",
                        "Fixed-amplitude pulsing for a fixed duration."),
    "LP": ExperimentDef("LP", "Long-Term Pulsing (with re-characterization)",
                        "Long-Term Pulsing",
                        "Long pulsing with periodic VT snapshots to track drift."),
    "PS": ExperimentDef("PS", "Progressive Stress (stepped current)",
                        "Progressive Stress",
                        "Stepped-current ramp with frequent characterization."),
}


@dataclass(frozen=True)
class ScopeChannelMap:
    """Default mapping of scope channels to signal names."""
    vmon: str = "CH1"      # stimulator voltage monitor (E_act - E_ret)
    imon: str = "CH2"      # stimulator current monitor
    eret: str = "CH3"      # return potential via instrumentation amp
    eact: str = "CH4"      # active potential via instrumentation amp (optional)


DEFAULT_SCOPE_MAP = ScopeChannelMap()


# ---------------------------------------------------------------------------
# UI defaults
# ---------------------------------------------------------------------------
DEFAULT_SAVE_DIR = "data"
PLOT_REFRESH_HZ = 10.0
