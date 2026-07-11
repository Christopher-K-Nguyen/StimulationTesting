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
# - PLX00178: CWRU stimulator; bench data shows I_mon ~1 mV/µA (≈8 mV at 8 µA),
#   i.e. NIL scaling — added so it auto-applies NIL without needing a
#   verification sweep.
NIL_SERIAL_NUMBERS = ("PLX00078", "PLX00089", "PLX00161", "PLX00180",
                      "PLX00178")

#: Default voltage-monitor scaling, V/V (V_mon = stim_voltage * scale)
VMON_SCALING_DEFAULT = 0.25
VMON_SCALING_NIL = 1.0

#: Default current-monitor scaling, V/µA
IMON_SCALING_DEFAULT = 2.5e-3   # V_mon line shows 2.5 mV per µA of stim current
IMON_SCALING_NIL = 1e-3

#: Plexon stimulator output current resolution (μA per LSB) for
#: RECTANGULAR pulses.  Operator: "keep the current resolution at 0.1 µA
#: for rectangular shapes.  I do not trust the 30 nA resolution of the
#: stimulator but will use it for non-rectangular shapes."  Used as the
#: validation floor + GUI spinbox grid for rectangular patterns.
STIM_CURRENT_RESOLUTION_UA = 0.1

#: Per-shape current QUANTIZATION grid (nA) applied when a PulsePattern is
#: rendered to the device ``.pat``.  A pattern whose phases are ALL
#: rectangular is rounded to the trusted 0.1 µA grid
#: (:data:`STIM_CURRENT_STEP_RECT_NA`); any pattern containing a shaped
#: (ramp / sine / bowtie / …) phase uses the PlexStim 2.0 native 30 nA
#: resolution (:data:`STIM_CURRENT_STEP_FINE_NA`) so the curve renders
#: smoothly.  See :meth:`stimtest.waveforms.PulsePattern.device_current_step_nA`.
STIM_CURRENT_STEP_RECT_NA = 100    # 0.1 µA — trusted grid for rectangular pulses
STIM_CURRENT_STEP_FINE_NA = 30     # PlexStim 2.0 native resolution (non-rectangular)

#: Default GUI spinbox single-step for current / amplitude / offset
#: spinboxes. Distinct from :data:`STIM_CURRENT_RESOLUTION_UA` (the
#: hardware grid, 0.1 µA) so users can wheel / arrow-key through
#: 1 µA increments without 10 clicks per integer microamp — the
#: hardware resolution still applies on math + quantization, but
#: the UI step is coarser for ergonomic reasons. Users can still
#: type sub-1-µA values directly; this only affects increment /
#: decrement buttons + scroll-wheel.
STIM_CURRENT_UI_STEP_UA = 1.0

#: Plexon stimulator phase-width / interphase / discharge time resolution (μs)
STIM_TIME_RESOLUTION_US = 1.0

#: Plexon PlexStim 2.0 maximum stimulation amplitude per channel (µA).
#: From the PlexStim 2.0 datasheet — current source rails at ~1 mA/channel
#: regardless of compliance voltage. Used as the default ceiling for
#: amplitude ramps so the runner doesn't ask the device for current it
#: cannot deliver.
STIM_MAX_AMPLITUDE_UA = 1000.0

#: Plexon PlexStim 2.0 voltage compliance (V).
#: V_mon saturates at the device's output-compliance rail (~±9.6 V on the
#: PlexStim 2.0) when the load demands more voltage than the stimulator
#: can supply. Crossing it means the device is no longer actually
#: delivering the programmed current.
#:
#: Detection threshold is 9.0 V, set DELIBERATELY BELOW the ~9.6 V rail so
#: a trace that visibly rails (sits AT the rail) reliably clears it. The
#: previous 12.0 V value sat ABOVE the rail, so a clearly-compliant trace
#: that flat-topped at ~9–10 V never crossed 12 V and compliance went
#: undetected (operator: "Why is compliance not being detected when the
#: voltage clearly reaches it?"). 9.0 V is the MATLAB ground truth — the
#: voltage-compliance check in ``getCapacitance.m`` is ``abs(voltage) > 9``
#: (``getVoltageWaveform.m`` / ``getAcutePlot2.m`` use 10 for the distinct
#: BROKEN-electrode test). ``cap.v_mon_v`` is in ELECTRODE volts
#: (``raw_vmon / vmon_scaling``), the same space MATLAB compares in, so
#: the threshold is dimensionally consistent across the Default (0.25 V/V)
#: and NIL (1.0 V/V) presets.
STIM_VOLTAGE_COMPLIANCE_V = 9.0

#: Plexon's typical digital trigger / pattern start delay (µs) — the gap
#: between the digital-sync TTL rising edge and the actual phase-1 stim
#: onset. Used to shift the captured time axis so t=0 lands on the
#: current edge instead of the sync edge.
#:
#: **Applied whenever the trigger source is a TTL sync line** — that's
#: both the EXT BNC AND a scope channel the operator tagged with
#: Role=Trigger (typically CH3 / CH4 wired to the same digital sync
#: wire just routed to a different physical input).  The 1.2 µs offset
#: is a property of the stimulator's pattern-start latency, not of
#: which input the sync lands on, so the correction is identical for
#: either path.
#:
#: For I_mon channel triggering (the current edge IS the trigger) the
#: delay is zero — pass ``digital=False`` to ``set_trigger`` /
#: ``auto_layout_for_pulse`` in that case.
DIGITAL_DELAY_US = 1.2


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

#: Default oscilloscope record length used by experiments (samples).
#: 20 k is the sweet spot for TBS-series @ 50 ns/sample: a 1 ms window
#: with ~4000 samples per 200 µs phase — plenty for the edge-step + cap-
#: ramp fits, well below the scope's analog bandwidth, and avoids the
#: multi-second per-capture transfer cost of 200 k / 2 M / 5 M.  TBS2204B
#: snaps requests to {1k, 2k, 20k, 200k, 2M, 5M}; 20 k lands exactly.
#: Lives in :mod:`stimtest.config` so the GUI and driver share one
#: source of truth instead of cross-importing from ``hardware.tektronix``.
DEFAULT_RECORD_LENGTH = 20_000


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
    "Ti": Coating(
        name="Ti",
        display_name="Titanium (Ti)",
        # Bare Ti has a passive TiO2 layer that pins the OCP near 0 V
        # vs Ag|AgCl. Treat the safe window as the conservative
        # ±0.6/±0.8 V envelope used for SIROF / Pt; literature values
        # vary widely with surface preparation.
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=0.05,
        notes="Bare titanium (passive TiO2 surface); narrow CSC, "
              "commonly used as a counter electrode.",
    ),
    "SS": Coating(
        name="SS",
        display_name="Stainless steel (SS)",
        # 316L stainless — passive Cr-rich oxide surface; pinned near
        # 0 V vs Ag|AgCl in saline. Conservative water window.
        cathodic_limit_v=-0.6,
        anodic_limit_v=+0.8,
        typical_csc_mc_per_cm2=0.05,
        notes="Bare stainless steel (e.g. 316L); commonly used as a "
              "counter / return electrode.",
    ),
}


# ---------------------------------------------------------------------------
# Connector / cable catalog (mirrored from MATLAB getDeviceType.m +
# getCableType.m / selectChannels.m)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Connector:
    """A cable's **device→Plexon** channel map.

    ``pin_to_channel[i]`` (0-based ``i``) is the **Plexon stim channel** the
    cable wires the **device channel ``i+1``** to — selecting device CH ``i+1``
    commands Plexon CH ``pin_to_channel[i]``.  ``SetupTab.current_channel_map``
    turns this into the ``{device: plexon}`` map the runner installs
    (:meth:`ExperimentRunner.set_channel_map`); an identity array ⇒ pulse
    CH N stimulates device CH N (no translation).

    Catalogued cables mirror the MATLAB reference arrays:
    - ``omneticsUTD`` / 2×8 receptacle → identity (1..16) — ``getDeviceType.m``
      / ``getCableType.m`` "Receptacle".
    - ``omneticsNNX`` → NeuroNexus headstage reorder.
    - ``utd_plexon`` → PlexStim port-side interleave for UTD boards
      (device 8 → Plexon 1).
    - ``BLACKROCK_TO_PLEXON_OMNETICS`` → the Plexon-Omnetics cable's 8-channel
      **bank swap** (device 1-8 ↔ Plexon 9-16, so device 9 → Plexon 1) —
      ``selectChannels.m`` / ``getCableType.m`` "Omnetics".
    """
    name: str
    description: str
    pin_to_channel: tuple  #: Plexon stim channel for device channel i (1-based)


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
    # The two cables the MATLAB getCableType.m dialog offers, added verbatim
    # (operator: "see the cable types from my MATLAB on how device CH09
    # matches Plexon CH01").  Selecting one re-routes device→Plexon per its
    # array; the older "Omnetics UTD" entry stays identity so existing
    # device defaults don't silently re-route.
    "2×8 Pin Receptacle": Connector(
        name="2×8 Pin Receptacle",
        description="Straight-through 2×8 pin receptacle — device CH N → "
                    "Plexon CH N (MATLAB getCableType 'Receptacle')",
        pin_to_channel=tuple(range(1, 17)),
    ),
    "Plexon Omnetics": Connector(
        name="Plexon Omnetics",
        description="Plexon Omnetics cable — 8-channel bank swap, device "
                    "CH1–8 ↔ Plexon CH9–16 (device CH09 → Plexon CH01; "
                    "MATLAB BLACKROCK_TO_PLEXON_OMNETICS)",
        pin_to_channel=(9, 10, 11, 12, 13, 14, 15, 16, 1, 2, 3, 4, 5, 6, 7, 8),
    ),
    # The verification / test-board stimulation cable (Plexon
    # 14-03-A-03), the "large black Omnetics" cable the calibration
    # wizard uses to connect the PlexStim to the 14-04-A-03-A test
    # board.  IDENTITY (straight-through): board channel N ↔ Plexon
    # CH N, so the test board needs NO channel translation
    # (``current_channel_map()`` == {}) — matches the verified
    # test-board-is-identity behaviour.  It's the operator-chosen
    # cable option for the Plexon Test Board (alongside the 2×8
    # receptacle).
    "Large Black Omnetics": Connector(
        name="Large Black Omnetics",
        description="Plexon large black Omnetics stimulation cable "
                    "(14-03-A-03) — the verification / test-board cable. "
                    "Straight-through: device CH N → Plexon CH N (identity).",
        pin_to_channel=tuple(range(1, 17)),
    ),
    # ("Other" — a pass-through 1:1 connector — was REMOVED: it was
    # redundant with "Custom", which is the operator-editable pinout
    # (operator: "Remove other as a choice for cable type since that is
    # what custom is for").  Back-compat: a stale saved ``connector =
    # "Other"`` pref resolves through ``_cable_pin_to_channel`` → None →
    # ``current_channel_map()`` == {} (identity / no translation), which
    # is exactly what "Other" did, so no routing changes.)
    # User-editable pinout. Starts identity (pin N → CH N); the Setup
    # tab's cable-map tree lets the operator re-assign each pin's device
    # channel, and the edited map is stored per-session in prefs
    # (``custom_cable_map``). Selecting this connector unlocks the tree.
    "Custom": Connector(
        name="Custom",
        description="Operator-defined pinout (edit the map below)",
        pin_to_channel=tuple(range(1, 17)),
    ),
}


# ---------------------------------------------------------------------------
# Electrode geometry catalog
# ---------------------------------------------------------------------------
#: Electrode pad/tip geometries supported by the GUI. These are
#: short-code strings for the persistence side; the user-visible
#: labels live in :data:`ELECTRODE_GEOMETRIES` below.
#:
#: ``rounded`` is a separate flag that only applies to ``square`` /
#: ``rectangle`` geometries and means "filleted corners" — used to
#: distinguish the Blackrock/MicroProbes "rounded square" tip from a
#: literal sharp-cornered square pad.
ELECTRODE_GEOMETRY_CIRCLE    = "circle"
ELECTRODE_GEOMETRY_SQUARE    = "square"
ELECTRODE_GEOMETRY_RECTANGLE = "rectangle"
ELECTRODE_GEOMETRY_CONE      = "cone"
ELECTRODE_GEOMETRY_RING      = "ring"
ELECTRODE_GEOMETRY_BAND      = "band"


@dataclass(frozen=True)
class ElectrodeGeometry:
    """Display + storage metadata for one electrode geometry option.

    The ``code`` is what gets persisted in prefs / per-channel
    overrides (a short string so JSON round-trips cleanly).
    ``label`` is the human-readable dropdown text. ``supports_rounded``
    flags whether the "Rounded" toggle is meaningful — currently
    just square + rectangle, where rounded = filleted corners /
    pill shape; for circle/cone/ring/band the toggle is hidden.
    ``is_planar`` distinguishes pad-style geometries (circle / square
    / rectangle / ring / band — the 2-D footprint sets the GSA) from
    3-D tip geometries (cone — the GSA is the lateral surface area
    of the truncated cone, not its footprint).
    """
    code: str
    label: str
    supports_rounded: bool
    is_planar: bool
    notes: str


ELECTRODE_GEOMETRIES: Dict[str, ElectrodeGeometry] = {
    ELECTRODE_GEOMETRY_CIRCLE: ElectrodeGeometry(
        code=ELECTRODE_GEOMETRY_CIRCLE,
        label="Circle",
        supports_rounded=False,
        is_planar=True,
        notes="Circular pad. UTD MEA / NeuroNexus default. "
              "GSA = π · r² where r is the disk radius.",
    ),
    ELECTRODE_GEOMETRY_SQUARE: ElectrodeGeometry(
        code=ELECTRODE_GEOMETRY_SQUARE,
        label="Square",
        supports_rounded=True,
        is_planar=True,
        notes="Square pad. Toggle Rounded for filleted corners "
              "(pill-cap variant). GSA = side²; rounded variant "
              "subtracts ~0.86·r² of corner area for fillet "
              "radius r ≈ 0.2·side.",
    ),
    ELECTRODE_GEOMETRY_RECTANGLE: ElectrodeGeometry(
        code=ELECTRODE_GEOMETRY_RECTANGLE,
        label="Rectangle",
        supports_rounded=True,
        is_planar=True,
        notes="Rectangular pad. Toggle Rounded for filleted "
              "ends (pill / stadium variant). GSA = w · h.",
    ),
    ELECTRODE_GEOMETRY_CONE: ElectrodeGeometry(
        code=ELECTRODE_GEOMETRY_CONE,
        label="Cone",
        supports_rounded=False,
        is_planar=False,
        notes="Conical tip — Blackrock UEA / MicroProbes FMA "
              "default. GSA = lateral area of truncated cone "
              "= π · (r₁ + r₂) · slant_height.",
    ),
    ELECTRODE_GEOMETRY_RING: ElectrodeGeometry(
        code=ELECTRODE_GEOMETRY_RING,
        label="Ring",
        supports_rounded=False,
        is_planar=True,
        notes="Annular ring (cylindrical-shaft electrodes; "
              "common in DBS leads). GSA = circumference · height "
              "= 2π · r · h.",
    ),
    ELECTRODE_GEOMETRY_BAND: ElectrodeGeometry(
        code=ELECTRODE_GEOMETRY_BAND,
        label="Band",
        supports_rounded=False,
        is_planar=True,
        notes="Band (partial ring) — paddle-lead style. GSA = "
              "arc-length × height = (2π · r · arc_fraction) · h.",
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
    #: Optional whitelist of cable / connector names the Setup tab's
    #: cable dropdown offers WHEN THIS DEVICE IS SELECTED. Empty tuple
    #: (the default) = the full :data:`CONNECTORS` list. The Plexon
    #: Test Board pins this to just ("Large Black Omnetics", "2×8 Pin
    #: Receptacle") per operator request — the two physical cables it's
    #: ever wired with; every real array keeps the full list so their
    #: Omnetics UTD / NNX / Plexon / Custom routing is unaffected.
    cable_choices: tuple = ()
    default_surface_area_um2: float = 5000.0   # SIROF UEA, IEEE NER paper
    default_coating: str = "SIROF"
    layout: str = "rect"        #: "rect" | "triangular"
    #: Per-device default electrode geometry. ``circle`` is a
    #: sensible fallback for planar / disk-pad arrays (UTD MEA,
    #: NeuroNexus, generic test boards); ``cone`` matches the
    #: pyramidal etched tips of the Utah / FMA microelectrode
    #: families. The user can override per-device in the Setup tab.
    default_geometry: str = ELECTRODE_GEOMETRY_CIRCLE
    #: Whether the corners / ends of square / rectangle pads are
    #: rounded. Has no effect for non-square / non-rectangle
    #: geometries; the GUI hides the toggle in those cases.
    default_rounded: bool = False
    #: Whether this "device" carries real electrodes. ``False`` for a
    #: bare test board (e.g. the Plexon test board — 16 resistive test
    #: points, no electrodes). When ``False`` the Setup tab HIDES the
    #: electrode-specific options (coating / geometry / surface area /
    #: return-electrode coating) and :meth:`SetupTab.current_array`
    #: forces ``surface_area_um2 = 0`` so area-normalised metrics
    #: (current density, Q_inj density) are disabled and the plot shows
    #: raw current instead of current density.
    has_electrodes: bool = True


DEVICES: Dict[str, DeviceDef] = {
    # Plexon test board goes FIRST (operator: "top of the list").  It's a
    # bare 16-channel resistive test board — no electrodes — so
    # ``has_electrodes=False`` hides the coating/geometry/area options and
    # disables area-normalised metrics.  Channels are the PlexStim outputs
    # 1–16 laid out 4×4 for a compact selection grid; the physical Omnetics
    # cable wiring (Plexon↔device renumbering) is chosen via the Connector /
    # cable map — see matlab_reference + the "Electrode Mapping" pinout doc.
    "Plexon Test Board": DeviceDef(
        name="Plexon Test Board",
        description="Plexon 16-channel resistive test board (no "
                    "electrodes). Used to verify PlexStim output + scope "
                    "wiring. 2×8 layout — top row even channels (2–16), "
                    "bottom row odd channels (1–15). The board channel "
                    "numbers MATCH the PlexStim (Plexon) channel numbers, "
                    "so no cable translation is needed (identity). Electrode "
                    "options (coating / geometry / area) are hidden and "
                    "area-based metrics are disabled.",
        # 2×8: bottom row odds 1,3,…,15; top row evens 2,4,…,16 (operator).
        mapping=(
            ( 2,  4,  6,  8, 10, 12, 14, 16),
            ( 1,  3,  5,  7,  9, 11, 13, 15),
        ),
        # Test board is wired with the large black Omnetics verification
        # cable (identity) — default to it, and restrict the cable
        # dropdown to just the two cables it's ever used with (operator).
        default_connector="Large Black Omnetics",
        cable_choices=("Large Black Omnetics", "2×8 Pin Receptacle"),
        has_electrodes=False,
    ),
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
        # Utah Electrode Array tips are etched to a pyramidal /
        # truncated-cone profile; the active SIROF site sits at the
        # exposed tip. ``cone`` captures that 3-D geometry.
        default_geometry=ELECTRODE_GEOMETRY_CONE,
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
        default_geometry=ELECTRODE_GEOMETRY_CONE,
    ),
    "MicroProbes 16-channel FMA": DeviceDef(
        name="MicroProbes 16-channel FMA",
        description="16-channel MicroProbes FMA. The 16 stimulation "
                    "channels sit on an equilateral triangular grid "
                    "with 0.4 mm pitch; row 1 is the widest (5 "
                    "channels: 13–9), row 0 has 3 channels centered "
                    "above 12–10, row 2 has 4 channels offset under "
                    "12–9, and row 3 has 4 channels shifted further "
                    "right under 11–9. Even rows (0 / 2) align and "
                    "odd rows (1 / 3) are offset by half a cell. The "
                    "manufacturer's reference electrode (R) and "
                    "external counter / ground (G) are NOT part of "
                    "this mapping — they're treated as the external "
                    "return / reference electrodes elsewhere in the "
                    "GUI. Zeros mark empty cells.",
        mapping=(
            ( 0, 16, 15, 14,  0),
            (13, 12, 11, 10,  9),
            ( 0,  8,  7,  6,  5),
            ( 0,  4,  3,  2,  1),
        ),
        default_connector="Omnetics UTD",
        default_surface_area_um2=2000.0,
        default_coating="AIROF",
        layout="triangular",
        # FMA shafts are tapered glass-coated tungsten with a
        # spherical / conical exposed tip — geometry "cone" is the
        # closest match in our catalog.
        default_geometry=ELECTRODE_GEOMETRY_CONE,
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
        # Was "Other" (now removed) → the identity-equivalent "2×8 Pin
        # Receptacle" (also 1:1, so routing is unchanged).  Pick "Custom"
        # from the dropdown to hand-edit this device's pinout.
        default_connector="2×8 Pin Receptacle",
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
    "CP": ExperimentDef("CP", "Continuous Pulsing (manual start/stop)",
                        "Continuous Pulsing",
                        "Pulse until you press Stop — no fixed duration or "
                        "pulse count; snapshot captures at a set cadence."),
    "LP": ExperimentDef("LP", "Long-Term Pulsing (with re-characterization)",
                        "Long-Term Pulsing",
                        "Long pulsing with periodic VT snapshots to track drift."),
    "PS": ExperimentDef("PS", "Progressive Stress (stepped current)",
                        "Progressive Stress",
                        "Stepped-current ramp with frequent characterization."),
    "EIS": ExperimentDef("EIS",
                         "Galvanostatic Electrochemical Impedance Spectroscopy",
                         "Galvanostatic Electrochemical Impedance Spectroscopy",
                         "Small-signal current sweep 1 Hz–100 kHz → complex "
                         "impedance Z(f); Bode + Nyquist."),
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
