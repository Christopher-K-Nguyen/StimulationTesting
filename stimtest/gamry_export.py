"""Excel export, modeled on the MATLAB ``saveVoltageTransientData.m`` layout.

Workbook layout
---------------
1. **Instrumentation** — stimulator + oscilloscope identity, channel routing.
   Tab-delimited Gamry ``.DTA`` preamble style (``TAG | KIND | VALUE | COMMENT``
   per row).
2. **Parameters** — pulse pattern, configuration, water-window limits, surface
   area. Same DTA-preamble style.
3. **Values** — MATLAB-style compiled metrics table: one column per channel
   ID, one row per metric (``Istim``, ``Qph``, ``Qinj``, ``Active Emc``,
   ``Active Ema``, ``Return Emc``, ``Vd1..Vd3``, ``Ceff``, ``Val1..Vat3``,
   ``Ral1..Rat3``, ``Cch``, optional ``Eda`` / ``Edr``). This is the sheet
   you copy-paste straight into GraphPad: rows = variables, columns = subjects.
4. **One sheet per ChannelRun** — MATLAB ``writetable`` layout: time-series
   columns (``Time``, ``Voltage``, ``Current``, ``Active``, ``Return``,
   ``Current Density``) on the left, per-capture metric columns
   (``Amplitude``, ``Qph``, ``Area``, ``Qinj``, ``Epola``, ``Epolr``, ``Vd``,
   ``Ceff``, ``Va``, ``Ra``, ``Cch``, optional ``Eda`` / ``Edr``,
   ``Date Time``, ``Status``) on the right. Each metric occupies the first
   one-to-six rows of its column (depends on phase count) and is blank
   thereafter, exactly like the MATLAB script.

A companion :func:`save_session_dta` writes genuine tab-delimited ``.DTA``
text files (one per ChannelRun) for downstream Gamry pipelines.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError as e:
    raise RuntimeError("openpyxl is required for Excel export; pip install openpyxl") from e

from .session import Capture, ChannelRun, Session


# ---------------------------------------------------------------------------
# Style constants
# ---------------------------------------------------------------------------
BOLD = Font(bold=True)
ITALIC_GRAY = Font(italic=True, color="595959")
BOLD_HEADER = Font(bold=True, color="FFFFFF")
HEADER_FILL = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
SECTION_FILL = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt(v, digits: int = 5) -> str:
    """Gamry-style scientific notation, e.g. ``-1.23456E-04``.

    Returns an empty string for NaN / None so blank cells stay blank. Used
    for the DTA preamble sheets *and* the per-channel waveform tables.
    """
    if v is None:
        return ""
    if isinstance(v, (list, tuple, np.ndarray)):
        return ", ".join(_fmt(x, digits) for x in v)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if np.isnan(f) or not np.isfinite(f):
        return ""
    return f"{f:.{digits}E}"


def _fmt_int(v) -> str:
    """Plain integer string for count-style fields, e.g. ``"16"``.

    ``_fmt(16, 0)`` rounds the mantissa and emits ``"2E+01"`` (= 20),
    which is wrong for integer counts. Use this helper instead for any
    field that's intrinsically an integer (channel counts, row/col
    counts, repetition counts). NaN / None / non-numeric inputs return
    an empty string so blank cells stay blank.
    """
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if np.isnan(f) or not np.isfinite(f):
        return ""
    return str(int(round(f)))


def _experiment_tag(expt: str) -> str:
    return {
        "VT": "VTRANSIENT", "TV": "VTRANSIENT_TRI",
        "SP": "SHORTPULSING", "LP": "LONGPULSING",
        "PS": "PROGSTRESS",
    }.get(expt, expt or "UNKNOWN")


def _meta_row(ws, row: int, tag: str, kind: str, value: str, comment: str = "") -> int:
    """Write one Gamry preamble row (used by sheets 1 & 2 only)."""
    ws.cell(row=row, column=1, value=tag).font = BOLD
    ws.cell(row=row, column=2, value=kind)
    ws.cell(row=row, column=3, value=value)
    if comment:
        ws.cell(row=row, column=4, value=comment).font = ITALIC_GRAY
    return row + 1


def _section_header(ws, row: int, name: str) -> int:
    c = ws.cell(row=row, column=1, value=name)
    c.font = BOLD
    c.fill = SECTION_FILL
    return row + 1


def _set_dta_column_widths(ws) -> None:
    """Match the width of a Gamry .DTA preamble for sheets 1 & 2."""
    for col, w in {1: 14, 2: 8, 3: 28, 4: 48}.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _safe_sheet_name(name: str, fallback: str, taken: set) -> str:
    """Excel sheet names: ≤31 chars, no ``[ ] / \\ ? * :``, must be unique."""
    forbidden = "[]/\\?*:"
    s = "".join(c for c in name if c not in forbidden).strip()
    if not s:
        s = fallback
    s = s[:31]
    if s in taken:
        for n in range(2, 1000):
            candidate = f"{s[:31 - len(str(n)) - 1]}_{n}"
            if candidate not in taken:
                return candidate
    return s


# ---------------------------------------------------------------------------
# Sheet 1: Instrumentation (DTA preamble style)
# ---------------------------------------------------------------------------
def _write_instrumentation_sheet(wb: Workbook, session: Session) -> None:
    ws = wb.create_sheet("Instrumentation")
    _set_dta_column_widths(ws)
    extras = session.test.extras or {}

    row = _section_header(ws, 1, "INSTRUMENTATION")
    row = _meta_row(ws, row, "TAG", "", _experiment_tag(session.test.experiment),
                    "Experiment tag")
    row = _meta_row(ws, row, "TITLE", "LABEL", session.name, "Test identifier")
    row = _meta_row(ws, row, "DATE", "LABEL",
                    session.created_at.strftime("%Y-%m-%d"), "Session date")
    row = _meta_row(ws, row, "TIME", "LABEL",
                    session.created_at.strftime("%H:%M:%S"), "Session time")
    if session.finished_at:
        row = _meta_row(ws, row, "ENDTIME", "LABEL",
                        session.finished_at.strftime("%H:%M:%S"), "Finished")
    row = _meta_row(ws, row, "USER", "LABEL", session.user_name or "", "Operator")
    row = _meta_row(ws, row, "EMAIL", "LABEL", session.user_email or "", "Operator email")
    row = _meta_row(ws, row, "NOTEBOOK", "LABEL", session.notebook, "Notebook ID")
    row = _meta_row(ws, row, "SUBJECT", "LABEL", session.subject, "Subject ID")
    row += 1

    row = _section_header(ws, row, "STIMULATOR")
    stim = extras.get("stimulator_info") or {}
    row = _meta_row(ws, row, "PSTAT", "PSTAT",
                    stim.get("description", "Plexon PlexStim"),
                    "Stimulator make/model")
    row = _meta_row(ws, row, "SERIAL", "LABEL",
                    stim.get("serial_number", ""), "Stimulator serial")
    row = _meta_row(ws, row, "FIRMWARE", "LABEL",
                    stim.get("firmware", ""), "Stimulator firmware")
    row = _meta_row(ws, row, "NCHAN", "QUANT",
                    _fmt_int(stim.get("n_channels")),
                    "Number of stimulator channels")
    row = _meta_row(ws, row, "VMONSCL", "QUANT",
                    _fmt(stim.get("vmon_scaling_v_per_v")),
                    "V_mon scaling (V output per stim V)")
    row = _meta_row(ws, row, "IMONSCL", "QUANT",
                    _fmt(stim.get("imon_scaling_v_per_ua")),
                    "I_mon scaling (V output per stim uA)")
    row = _meta_row(ws, row, "SIMULATED", "LABEL",
                    "yes" if stim.get("is_simulated") else "no",
                    "Simulated backend?")
    row += 1

    row = _section_header(ws, row, "OSCILLOSCOPE")
    scope = extras.get("oscilloscope_info") or {}
    row = _meta_row(ws, row, "MAKE", "LABEL", scope.get("make", ""),
                    "Oscilloscope make")
    row = _meta_row(ws, row, "MODEL", "LABEL", scope.get("model", ""),
                    "Oscilloscope model")
    row = _meta_row(ws, row, "SERIAL", "LABEL", scope.get("serial", ""),
                    "Oscilloscope serial")
    row = _meta_row(ws, row, "FIRMWARE", "LABEL", scope.get("firmware", ""),
                    "Oscilloscope firmware")
    row = _meta_row(ws, row, "RESOURCE", "LABEL", scope.get("resource", ""),
                    "VISA resource string")
    row = _meta_row(ws, row, "NCHAN", "QUANT", _fmt_int(scope.get("n_channels")),
                    "Number of scope channels")
    row = _meta_row(ws, row, "SIMULATED", "LABEL",
                    "yes" if scope.get("is_simulated") else "no",
                    "Simulated backend?")
    row += 1

    aliases = extras.get("channel_aliases") or {}
    if aliases:
        row = _section_header(ws, row, "CHANNEL MAPPING")
        descriptions = {
            "vmon": "Stimulator voltage monitor (V_mon)",
            "imon": "Stimulator current monitor (I_mon)",
            "eret": "Return potential vs Ag|AgCl",
            "eact": "Active potential vs Ag|AgCl",
        }
        for alias, phys in aliases.items():
            row = _meta_row(ws, row, alias.upper(), "LABEL", phys,
                            descriptions.get(alias, ""))


# ---------------------------------------------------------------------------
# Sheet 2: Parameters (DTA preamble style)
# ---------------------------------------------------------------------------
def _write_parameters_sheet(wb: Workbook, session: Session) -> None:
    ws = wb.create_sheet("Parameters")
    _set_dta_column_widths(ws)
    p = session.test.pattern
    cfg = session.test.configuration
    array = session.test.array

    row = _section_header(ws, 1, "EXPERIMENT")
    row = _meta_row(ws, row, "EXPERIMENT", "LABEL", session.test.experiment,
                    "VT/TV/SP/LP/PS")
    row = _meta_row(ws, row, "DURATION", "QUANT",
                    _fmt(session.test.duration_s),
                    "Pulsing duration (s) - SP/LP only")
    row = _meta_row(ws, row, "NPULSES", "QUANT",
                    _fmt_int(session.test.number_of_pulses),
                    "Number of pulses - SP/LP only")
    row = _meta_row(ws, row, "TARGETQPH", "QUANT",
                    _fmt(session.test.target_charge_phase_nc),
                    "Target Q_ph (nC); inf = ramp until limit")
    row += 1

    row = _section_header(ws, row, "PULSE PATTERN")
    row = _meta_row(ws, row, "TYPE", "LABEL",
                    "Triphasic" if p.is_triphasic else
                    ("Monophasic" if p.num_phases == 1 else "Biphasic"),
                    "Pattern type")
    row = _meta_row(ws, row, "POLARITY", "LABEL",
                    "Cathodic-first" if p.polarity == -1 else "Anodic-first",
                    "Sign of excitation phase")
    row = _meta_row(ws, row, "RATE", "QUANT", _fmt(p.rate_hz),
                    "Pulse repetition rate (Hz)")
    row = _meta_row(ws, row, "REPS", "QUANT", _fmt_int(p.repetitions),
                    "Repetitions per train (0 = infinite)")
    for i, ph in enumerate(p.phases, start=1):
        row = _meta_row(ws, row, f"AMP{i}", "QUANT",
                        _fmt(ph.amplitude_ua),
                        f"Phase {i} amplitude (uA)")
        row = _meta_row(ws, row, f"PHASEW{i}", "QUANT",
                        _fmt(ph.width_us * 1e-6), f"Phase {i} width (s)")
        if ph.delay_after_us > 0:
            label = "DISCHARGE" if i == len(p.phases) else f"DELAY{i}"
            comment = ("Discharge delay (s)" if i == len(p.phases)
                       else f"Inter-phase delay {i} (s)")
            row = _meta_row(ws, row, label, "QUANT",
                            _fmt(ph.delay_after_us * 1e-6), comment)
    row += 1

    row = _section_header(ws, row, "CONFIGURATION")
    row = _meta_row(ws, row, "CONFIG", "LABEL", cfg.id,
                    "MP / BP / TP / PBP / PTP / CG")
    row = _meta_row(ws, row, "ACTIVE", "QUANT", _fmt_int(cfg.active),
                    "Active electrode (1-based)")
    row = _meta_row(ws, row, "RETURNS", "LABEL",
                    ", ".join(str(r) for r in cfg.returns) if cfg.returns else "",
                    "Return electrode(s)")
    row = _meta_row(ws, row, "COUNTER", "LABEL", cfg.counter_electrode_label,
                    "Counter-electrode label")
    row = _meta_row(ws, row, "REFERENCE", "LABEL",
                    session.test.reference_electrode_label,
                    "Reference electrode")
    row += 1

    row = _section_header(ws, row, "ELECTRODE ARRAY")
    row = _meta_row(ws, row, "NAME", "LABEL", array.name, "Array geometry")
    row = _meta_row(ws, row, "ROWS", "QUANT", _fmt_int(array.rows), "Grid rows")
    row = _meta_row(ws, row, "COLS", "QUANT", _fmt_int(array.cols), "Grid columns")
    row = _meta_row(ws, row, "NSITES", "QUANT", _fmt_int(len(array.sites)),
                    "Number of electrode sites")
    if array.sites:
        first = array.sites[0]
        row = _meta_row(ws, row, "COATING", "LABEL", first.coating,
                        "Active electrode coating")
        row = _meta_row(ws, row, "AREA", "QUANT",
                        _fmt(first.surface_area_um2 * 1e-8),
                        "Geometric surface area (cm^2)")
    row += 1

    extras = session.test.extras or {}
    coating = extras.get("coating_props")
    if coating:
        row = _section_header(ws, row, "WATER WINDOW")
        row = _meta_row(ws, row, "ELC", "QUANT",
                        _fmt(coating.get("cathodic_limit_v")),
                        "Cathodic limit (V vs Ag|AgCl)")
        row = _meta_row(ws, row, "ELA", "QUANT",
                        _fmt(coating.get("anodic_limit_v")),
                        "Anodic limit (V vs Ag|AgCl)")
        row = _meta_row(ws, row, "TOLERANCE", "QUANT", "2.00000E-02",
                        "Limit-detection tolerance (V)")
        row = _meta_row(ws, row, "DEPOL", "QUANT",
                        _fmt(extras.get("depolarization_us", 12.0) * 1e-6),
                        "Depolarization sample time after phase end (s)")


# ---------------------------------------------------------------------------
# Sheet 3: Setup — every Setup-tab field the GUI snapshotted at start time
# ---------------------------------------------------------------------------
def _write_setup_sheet(wb: Workbook, session: Session) -> None:
    """Write a "Setup" sheet listing every Setup-tab field captured by
    :meth:`stimtest.gui.setup_tab.SetupTab.setup_snapshot`.

    The sheet is structured as Gamry .DTA preamble rows
    (``TAG | KIND | VALUE | COMMENT``) so it visually matches the
    Instrumentation and Parameters sheets above it. The preamble keys
    cover device + connector + grid type, acquisition mode, surface
    area + active-electrode coating, return / reference electrodes,
    water-window limits, the channel-mapping table, and per-channel
    role assignment (V_mon / I_mon / E_act / E_ret / Trigger).

    No-op when ``extras['setup_snapshot']`` is missing — sessions that
    weren't built through the GUI (unit tests, headless replays) just
    won't get the sheet.
    """
    extras = session.test.extras or {}
    snap = extras.get("setup_snapshot")
    if not snap:
        return
    ws = wb.create_sheet("Setup")
    _set_dta_column_widths(ws)

    row = _section_header(ws, 1, "TEST DEVICE")
    row = _meta_row(ws, row, "DEVICE", "LABEL",
                    str(snap.get("device", "")), "Catalog model")
    grid = str(snap.get("grid_type", "rect"))
    row = _meta_row(ws, row, "GRIDTYPE", "LABEL",
                    "Hexagonal (triangular)" if grid == "triangular"
                    else "Square (rectangular)",
                    "Physical packing of the array")
    row = _meta_row(ws, row, "CONNECTOR", "LABEL",
                    str(snap.get("connector", "")),
                    "Headstage connector / pinout")
    row += 1

    row = _section_header(ws, row, "ACQUISITION")
    row = _meta_row(ws, row, "MODE", "LABEL",
                    str(snap.get("acq_mode", "")),
                    "Single capture vs scope-side averaging")
    row = _meta_row(ws, row, "NAVG", "QUANT",
                    _fmt_int(snap.get("acq_n_avg")),
                    "Number of waveforms averaged on the scope")
    row += 1

    row = _section_header(ws, row, "SURFACE AREA")
    row = _meta_row(ws, row, "MODE", "LABEL",
                    str(snap.get("surface_area_mode", "")),
                    "Same / Different per electrode")
    row = _meta_row(ws, row, "VALUE", "QUANT",
                    _fmt(snap.get("surface_area_value")),
                    "Geometric area in display unit")
    row = _meta_row(ws, row, "UNIT", "LABEL",
                    str(snap.get("surface_area_unit", "")),
                    "Display unit (μm² / mm² / cm²)")
    row += 1

    row = _section_header(ws, row, "ACTIVE ELECTRODE COATING")
    row = _meta_row(ws, row, "MODE", "LABEL",
                    str(snap.get("coating_mode", "")),
                    "Same / Different per electrode")
    row = _meta_row(ws, row, "COATING", "LABEL",
                    str(snap.get("coating_short", "")), "Short tag")
    row = _meta_row(ws, row, "COATINGNAME", "LABEL",
                    str(snap.get("coating_label", "")),
                    "Spelled-out coating name")
    row += 1

    row = _section_header(ws, row, "RETURN / COUNTER ELECTRODE")
    row = _meta_row(ws, row, "ENABLED", "LABEL",
                    "yes" if snap.get("return_enable") else "no",
                    "Return-electrode metadata captured?")
    row = _meta_row(ws, row, "COATING", "LABEL",
                    str(snap.get("return_coating_short", "")), "Short tag")
    row = _meta_row(ws, row, "COATINGNAME", "LABEL",
                    str(snap.get("return_coating_label", "")),
                    "Spelled-out coating name")
    row += 1

    row = _section_header(ws, row, "REFERENCE ELECTRODE")
    row = _meta_row(ws, row, "ENABLED", "LABEL",
                    "yes" if snap.get("reference_enable") else "no",
                    "Reference-electrode metadata captured?")
    row = _meta_row(ws, row, "ELECTRODE", "LABEL",
                    str(snap.get("reference_electrode_short", "")), "Short tag")
    row = _meta_row(ws, row, "ELECTRODENAME", "LABEL",
                    str(snap.get("reference_electrode_label", "")),
                    "Spelled-out reference name")
    row += 1

    row = _section_header(ws, row, "POTENTIAL LIMITS")
    row = _meta_row(ws, row, "ELC", "QUANT",
                    _fmt(snap.get("cathodic_limit_v")),
                    "Cathodic limit (V vs reference)")
    row = _meta_row(ws, row, "ELA", "QUANT",
                    _fmt(snap.get("anodic_limit_v")),
                    "Anodic limit (V vs reference)")
    row = _meta_row(ws, row, "TOLERANCE", "QUANT",
                    _fmt(snap.get("polarization_tolerance_v")),
                    "Polarization grace band (V)")
    row += 1

    # Channel-role assignment — one row per role the user actually
    # assigned. The role-keyed view (V_mon / I_mon / etc. → channel)
    # reads naturally as "which channel was each instrument signal?".
    role_to_ch = snap.get("channel_roles_by_role") or {}
    if role_to_ch:
        row = _section_header(ws, row, "CHANNEL ROLES")
        for role, ch in role_to_ch.items():
            value = ch if isinstance(ch, str) else ", ".join(str(c) for c in ch)
            row = _meta_row(ws, row, str(role).upper(), "LABEL",
                            str(value), f"Scope channel(s) carrying {role}")
        row += 1

    # Channel mapping — render the table inline (rows × cols of
    # channel numbers, 0 = empty cell) below the preamble. Header row
    # uses bold; "0" cells are blanked so the array shape is visible.
    mapping = snap.get("channel_mapping")
    if mapping:
        row = _section_header(ws, row, "CHANNEL MAPPING")
        row = _meta_row(ws, row, "ROWS", "QUANT",
                        _fmt_int(len(mapping)), "Grid rows")
        row = _meta_row(ws, row, "COLS", "QUANT",
                        _fmt_int(len(mapping[0]) if mapping else 0),
                        "Grid columns")
        # Column header — col indices.
        for c in range(len(mapping[0]) if mapping else 0):
            cell = ws.cell(row=row, column=2 + c, value=f"col {c}")
            cell.font = BOLD
        ws.cell(row=row, column=1, value="ROW").font = BOLD
        row += 1
        for r, line in enumerate(mapping):
            ws.cell(row=row, column=1, value=f"row {r}").font = BOLD
            for c, ch in enumerate(line):
                # Render 0 (empty cell) as blank so the array shape
                # reads naturally. Non-zero cells get the channel
                # number as an int.
                if int(ch) > 0:
                    ws.cell(row=row, column=2 + c, value=int(ch))
            row += 1
        row += 1

    # Per-channel area / coating overrides — only present when the
    # user picked "Different per electrode" mode.
    overrides = snap.get("per_channel_overrides") or {}
    if overrides:
        row = _section_header(ws, row, "PER-CHANNEL OVERRIDES")
        ws.cell(row=row, column=1, value="CHANNEL").font = BOLD
        ws.cell(row=row, column=2, value="AREA (μm²)").font = BOLD
        ws.cell(row=row, column=3, value="COATING").font = BOLD
        row += 1
        # Sort by channel number for stable output.
        for ch in sorted(overrides.keys(), key=lambda x: int(x)):
            o = overrides[ch] or {}
            ws.cell(row=row, column=1, value=int(ch))
            if "area_um2" in o:
                ws.cell(row=row, column=2, value=_fmt(o["area_um2"]))
            if "coating" in o:
                ws.cell(row=row, column=3, value=str(o["coating"]))
            row += 1
        row += 1

    # Per-experiment params snapshot — pulled from the test-parameters
    # tab via :meth:`ExperimentTab.params_snapshot`. Each subclass
    # decides what extra fields to record (ramp policy, mode flags,
    # strategy choice, etc.). We render every key/value pair as a
    # generic preamble row so subclasses don't have to round-trip
    # through the exporter when adding a new field.
    params = extras.get("params_snapshot") or {}
    if params:
        row = _section_header(ws, row, "TEST-PARAMETERS TAB")
        for key, value in params.items():
            tag = str(key).upper().replace(" ", "_")[:24]
            kind = "QUANT" if isinstance(value, (int, float)) and not isinstance(value, bool) else "LABEL"
            row = _meta_row(
                ws, row, tag, kind,
                _fmt(value) if kind == "QUANT" else str(value),
                str(key))


# ---------------------------------------------------------------------------
# Per-channel scratch container — what we pull off the *final* (max-amplitude)
# capture of each ChannelRun. Mirrors the per-group temp variables in
# saveVoltageTransientData.m.
# ---------------------------------------------------------------------------
class _ChannelMetricSet:
    """Holds the metric values for one ChannelRun (final capture only).

    All attributes are scalars or short lists matching the per-phase /
    per-access counts the pulse actually has. Missing pieces stay as
    ``float('nan')`` so the writer can still emit a blank cell without
    needing branching everywhere.
    """
    __slots__ = (
        "channel_id", "is_good", "status_label",
        "amplitude_ua", "q_ph_nc", "q_inj_mc_per_cm2", "area_um2",
        "active_excursions", "return_excursions",   # E_pol per phase
        "driving_voltages",                          # V_d per phase
        "effective_capacitance_nf",
        "access_voltages_v", "access_resistances_kohm",  # 4 (biphasic) or 6 (triphasic)
        "charging_capacitance_nf",
        "active_driving_potentials", "return_driving_potentials",
        "date_time",
    )

    def __init__(self):
        nan = float("nan")
        self.channel_id = ""
        self.is_good = True
        self.status_label = ""
        self.amplitude_ua = nan
        self.q_ph_nc = nan
        self.q_inj_mc_per_cm2 = nan
        self.area_um2 = nan
        self.active_excursions: List[float] = []
        self.return_excursions: List[float] = []
        self.driving_voltages: List[float] = []
        self.effective_capacitance_nf = nan
        self.access_voltages_v: List[float] = []
        self.access_resistances_kohm: List[float] = []
        self.charging_capacitance_nf = nan
        self.active_driving_potentials: List[float] = []
        self.return_driving_potentials: List[float] = []
        self.date_time = ""


def _final_capture(run: ChannelRun) -> Optional[Capture]:
    """The capture used to populate per-channel cells (largest amplitude
    that is still ``status.good``; falls back to the last capture)."""
    if not run.captures:
        return None
    good = [c for c in run.captures if c.status.good]
    if good:
        return max(good, key=lambda c: abs(c.pattern.excitation_phase.amplitude_ua))
    return run.captures[-1]


def _channel_id(run: ChannelRun, idx: int, taken: set) -> str:
    """Channel ID as it appears in the Values sheet header and as the
    per-channel sheet name. MP/CG → just the active number; others →
    ``display_name`` like ``CH09 v 05,13``. ``_MAX`` / ``_BAD`` suffixes
    mirror the MATLAB code when the run failed."""
    cap = _final_capture(run)
    cfg = run.configuration
    if cfg.id in ("MP", "CG"):
        base = str(cfg.active)
    else:
        base = cfg.display_name()
    if cap is not None and not cap.status.good:
        if cap.status.voltage_compliance:
            base += "_MAX"
        elif cap.status.aborted:
            base += "_ABORT"
        else:
            base += "_BAD"
    return _safe_sheet_name(base, fallback=f"CH{idx + 1}", taken=taken)


def _gather_channel_metrics(run: ChannelRun, channel_id: str) -> _ChannelMetricSet:
    """Pull the final capture's metrics into a flat scratch container.

    The MATLAB script assigns NaN to the amplitude/charge fields when the
    sweep didn't successfully reach a limit; we do the same so downstream
    GraphPad cells stay empty rather than show stale numbers.
    """
    out = _ChannelMetricSet()
    out.channel_id = channel_id
    out.area_um2 = run.surface_area_um2
    cap = _final_capture(run)
    if cap is None:
        return out

    out.is_good = cap.status.good
    if cap.status.aborted:
        out.status_label = "Aborted"
    elif cap.status.voltage_compliance:
        out.status_label = "Voltage compliance"
    elif cap.status.reached_potential_limit:
        out.status_label = "Limit reached"
    elif not cap.status.good:
        out.status_label = "Bad"
    else:
        out.status_label = cap.status.notes or "OK"
    out.date_time = cap.timestamp.strftime("%Y-%m-%d %H:%M:%S")

    m = cap.metrics
    amp = cap.pattern.excitation_phase.amplitude_ua
    if not np.isfinite(amp) or amp == 0:
        # MATLAB: leave amplitude/Qph/Qinj as NaN when the sweep didn't
        # actually land on a usable amplitude
        pass
    else:
        out.amplitude_ua = float(amp)
        out.q_ph_nc = float(m.charge_per_phase_nc)
        out.q_inj_mc_per_cm2 = float(m.charge_injection_mc_per_cm2)

    out.active_excursions = list(m.polarization_per_phase_v)
    out.return_excursions = list(m.return_polarization_per_phase_v)
    out.driving_voltages = [float(m.driving_voltage_v)] * cap.pattern.num_phases
    out.effective_capacitance_nf = float(m.effective_capacitance_nf)
    out.access_voltages_v = [abs(v) for v in m.access_voltage_per_phase_v]
    out.access_resistances_kohm = list(m.access_resistance_per_phase_kohm)
    out.charging_capacitance_nf = float(m.driving_capacitance_mf_per_cm2)
    return out


# ---------------------------------------------------------------------------
# Metric labelling helpers — match MATLAB exactly
# ---------------------------------------------------------------------------
def _excursion_label(role: str, polarity: int, phase_index: int,
                     is_triphasic: bool, has_discharge: bool) -> str:
    """Active vs return × cathodic vs anodic × phase index → label string.

    MATLAB code (saveVoltageTransientData.m lines 332..380):
        Cathodic-first  → phase 1 = Emc, phase 2 = Ema
        Anodic-first    → phase 1 = Ema, phase 2 = Emc
    The return electrode flips polarity so the labels swap. Triphasic adds
    Emc2 / Ema2 for phase 3.
    """
    if role == "active":
        sign = polarity
    else:
        sign = -polarity
    # phase_index is 0-based; MATLAB labels phase 1 = no suffix, phase 3 = "2"
    suffix = ""
    if is_triphasic and phase_index == 2:
        suffix = "2"
    elif (not is_triphasic) and has_discharge and phase_index == 1:
        suffix = ""    # biphasic + discharge: just Ema/Emc on phase 2
    polarity_letter = "Emc" if (sign == -1) ^ (phase_index == 1 and not is_triphasic) else "Ema"
    # Reproduce MATLAB exactly: under cathodic-first biphasic the labels are
    # "Active Emc" then "Active Ema" (cathodic/anodic), regardless of the
    # phase number suffix.
    if not is_triphasic:
        if sign == -1:
            base = "Emc" if phase_index == 0 else "Ema"
        else:
            base = "Ema" if phase_index == 0 else "Emc"
    else:
        # Triphasic: phase 1 follows polarity, phase 2 flipped, phase 3 same
        # as phase 1 with "2" suffix
        if phase_index == 0:
            base = "Emc" if sign == -1 else "Ema"
        elif phase_index == 1:
            base = "Ema" if sign == -1 else "Emc"
        else:
            base = ("Emc" if sign == -1 else "Ema") + "2"
        suffix = ""    # already encoded
    label = base + suffix
    prefix = "Active " if role == "active" else "Return "
    return f"{prefix}{label} (V)"


def _va_labels(num_access: int) -> List[str]:
    """Access-voltage row labels matching MATLAB.

    MATLAB ordering for biphasic + interphase + discharge (4 access points):
        Val1, Vat1, Val2, Vat2  (leading/trailing, phase 1 / phase 2)
    Triphasic + interphase + discharge (6 access points):
        Val1, Vat1, Val2, Vat2, Val3, Vat3
    """
    labels: List[str] = []
    for k in range(1, (num_access // 2) + 1):
        labels.append(f"Val{k} (V)")
        labels.append(f"Vat{k} (V)")
    if num_access % 2 == 1:
        labels.append(f"Val{num_access // 2 + 1} (V)")
    return labels


def _ra_labels(num_access: int) -> List[str]:
    labels: List[str] = []
    for k in range(1, (num_access // 2) + 1):
        labels.append(f"Ral{k} (kOhm)")
        labels.append(f"Rat{k} (kOhm)")
    if num_access % 2 == 1:
        labels.append(f"Ral{num_access // 2 + 1} (kOhm)")
    return labels


# ---------------------------------------------------------------------------
# Sheet 3: Values (MATLAB-style metrics-as-rows)
# ---------------------------------------------------------------------------
def _write_values_sheet(wb: Workbook, session: Session,
                        metric_sets: List[_ChannelMetricSet]) -> None:
    """Compiled metrics, channels-as-columns / metrics-as-rows.

    Mirrors the ``Values`` sheet produced by ``saveVoltageTransientData.m``.
    The first column holds metric labels; subsequent columns hold the
    channel-by-channel values for the *final* capture of each run.
    """
    ws = wb.create_sheet("Values")
    p = session.test.pattern
    is_triphasic = p.is_triphasic
    polarity = p.polarity
    has_interphase = any(ph.delay_after_us > 0 for ph in p.phases[:-1])
    has_discharge = p.phases[-1].delay_after_us > 0
    has_return = any(ms.return_excursions for ms in metric_sets)
    n_phases = p.num_phases
    n_channels = len(metric_sets)

    # Build the row labels exactly in the MATLAB order
    rows: List[Tuple[str, List]] = []

    # --- stim ---
    rows.append(("Istim (uA)", [ms.amplitude_ua for ms in metric_sets]))
    rows.append(("Qph (nC/ph)", [ms.q_ph_nc for ms in metric_sets]))
    rows.append(("Qinj (mC/cm2)", [ms.q_inj_mc_per_cm2 for ms in metric_sets]))

    # --- excursions: active first, then return ---
    excursion_phase_indices: List[int] = [0]
    if is_triphasic and has_interphase:
        excursion_phase_indices.append(1)
    if (is_triphasic and has_discharge) or (not is_triphasic and has_discharge):
        excursion_phase_indices.append(n_phases - 1)
    for k in excursion_phase_indices:
        label = _excursion_label("active", polarity, k, is_triphasic, has_discharge)
        rows.append((label, [_safe_index(ms.active_excursions, k) for ms in metric_sets]))
    if has_return:
        for k in excursion_phase_indices:
            label = _excursion_label("return", polarity, k, is_triphasic, has_discharge)
            rows.append((label,
                         [_safe_index(ms.return_excursions, k) for ms in metric_sets]))

    # --- driving voltages ---
    for k in range(1, n_phases + 1):
        rows.append((f"Vd{k} (V)",
                     [_safe_index(ms.driving_voltages, k - 1) for ms in metric_sets]))

    # --- effective capacitance ---
    rows.append(("Ceff (mF/cm2)",
                 [ms.effective_capacitance_nf for ms in metric_sets]))

    # --- access voltages and resistances ---
    n_access = _expected_access_count(n_phases, has_interphase, has_discharge)
    for k, label in enumerate(_va_labels(n_access)):
        rows.append((label, [_safe_index(ms.access_voltages_v, k) for ms in metric_sets]))
    for k, label in enumerate(_ra_labels(n_access)):
        rows.append((label,
                     [_safe_index(ms.access_resistances_kohm, k) for ms in metric_sets]))

    # --- charging capacitance ---
    rows.append(("Cch (nF)", [ms.charging_capacitance_nf for ms in metric_sets]))

    # --- driving potentials (active and return) — only if the runner
    # populated them, which implies an instrumentation amp on the scope ---
    if any(ms.active_driving_potentials for ms in metric_sets):
        for k in range(1, n_phases + 1):
            rows.append((f"Eda{k} (V)", [_safe_index(ms.active_driving_potentials,
                                                     k - 1) for ms in metric_sets]))
    if any(ms.return_driving_potentials for ms in metric_sets):
        for k in range(1, n_phases + 1):
            rows.append((f"Edr{k} (V)", [_safe_index(ms.return_driving_potentials,
                                                     k - 1) for ms in metric_sets]))

    # --- write ---
    # Header row: blank, then channel IDs
    ws.cell(row=1, column=1, value="").font = BOLD
    for j, ms in enumerate(metric_sets, start=2):
        c = ws.cell(row=1, column=j, value=ms.channel_id)
        c.font = BOLD_HEADER
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center")

    # Data rows
    for i, (label, values) in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=label).font = BOLD
        for j, v in enumerate(values, start=2):
            ws.cell(row=i, column=j, value=_fmt(v))

    # Decent column widths
    ws.column_dimensions["A"].width = 18
    for j in range(2, n_channels + 2):
        ws.column_dimensions[get_column_letter(j)].width = 14


def _safe_index(seq: Sequence, idx: int):
    if 0 <= idx < len(seq):
        return seq[idx]
    return float("nan")


def _expected_access_count(n_phases: int, has_interphase: bool,
                           has_discharge: bool) -> int:
    """Number of access points the canonical algorithm produces.

    Biphasic + interphase + discharge → 4 (lead1, trail1, lead2, trail2)
    Biphasic + interphase only        → 3 (lead1, trail1, lead2)
    Biphasic + discharge only         → 2 (lead1, trail2)
    Triphasic + interphase + discharge → 6
    """
    if n_phases == 1:
        return 1 + (1 if has_discharge else 0)
    base = 1
    if has_interphase:
        base += 2 * (n_phases - 1)
    if has_discharge:
        base += 1
    return base


# ---------------------------------------------------------------------------
# Sheets 4+: per-electrode (MATLAB writetable layout)
# ---------------------------------------------------------------------------
def _write_electrode_sheet(wb: Workbook, session: Session, run: ChannelRun,
                           run_idx: int, metric_set: _ChannelMetricSet,
                           include_raw_traces: bool) -> None:
    """One sheet per channel, mirroring ``saveVoltageTransientData.m`` line 498-648.

    Columns (left → right):
        Time | Voltage | Current | Active | Return | Current Density |
        Amplitude | Qph | Area | Qinj | Epola | Epolr | Vd | Ceff |
        Va | Ra | Cch | [Eda] | [Edr] | Date Time | Status

    Metric columns hold one to three values in their first rows and are
    blank in the rest of the sheet, matching the MATLAB output.
    """
    ws = wb.create_sheet(_safe_sheet_name(metric_set.channel_id,
                                          fallback=f"CH{run_idx + 1}",
                                          taken=set(wb.sheetnames)))
    p = session.test.pattern
    cap = _final_capture(run) if include_raw_traces else None
    has_return_in_capture = cap is not None and cap.e_ret_v is not None and cap.e_ret_v.size
    has_active_in_capture = cap is not None and cap.e_act_v is not None and cap.e_act_v.size

    # ----- column layout ---------------------------------------------
    # Time-series columns first, then metric columns, mirroring MATLAB.
    ts_headers = ["Time (us)", "Voltage (V)", "Current (uA)"]
    if has_active_in_capture:
        ts_headers.append("Active (V)")
    if has_return_in_capture:
        ts_headers.append("Return (V)")
    ts_headers.append("Current Density (A/cm2)")

    metric_headers = [
        "Amplitude (uA)", "Qph (nC/ph)", "Area (um2)", "Qinj (mC/cm2)",
        "Epola (V)",
    ]
    if has_return_in_capture:
        metric_headers.append("Epolr (V)")
    metric_headers += ["Vd (V)", "Ceff (mF/cm2)", "Va (V)", "Ra (kOhm)",
                       "Cch (nF)"]
    if metric_set.active_driving_potentials:
        metric_headers.append("Eda (V)")
    if metric_set.return_driving_potentials:
        metric_headers.append("Edr (V)")
    metric_headers += ["Date Time", "Status"]

    headers = ts_headers + metric_headers
    for col, h in enumerate(headers, start=1):
        c = ws.cell(row=1, column=col, value=h)
        c.font = BOLD_HEADER
        c.fill = HEADER_FILL
        c.alignment = Alignment(horizontal="center")

    # ----- waveform rows ------------------------------------------------
    if include_raw_traces and cap is not None and cap.time_us.size:
        n = cap.time_us.size
        t_us = np.asarray(cap.time_us)
        v_mon = np.asarray(cap.v_mon_v)
        i_ua = np.asarray(cap.i_mon_ua)
        eact = np.asarray(cap.e_act_v) if has_active_in_capture else None
        eret = np.asarray(cap.e_ret_v) if has_return_in_capture else None
        area_cm2 = max(metric_set.area_um2 * 1e-8, 1e-12)
        # Current density = I (A) / area (cm^2)
        j_density = i_ua * 1e-6 / area_cm2

        for k in range(n):
            r = k + 2   # data starts at row 2
            ws.cell(row=r, column=1, value=_fmt(t_us[k]))
            ws.cell(row=r, column=2, value=_fmt(v_mon[k]))
            ws.cell(row=r, column=3, value=_fmt(i_ua[k]))
            col = 4
            if eact is not None:
                ws.cell(row=r, column=col, value=_fmt(eact[k])); col += 1
            if eret is not None:
                ws.cell(row=r, column=col, value=_fmt(eret[k])); col += 1
            ws.cell(row=r, column=col, value=_fmt(j_density[k]))

    # ----- metric columns (first few rows only) -------------------------
    n_ts = len(ts_headers)
    col = n_ts + 1   # first metric column

    # Single-cell metrics (row 2 only)
    ws.cell(row=2, column=col, value=_fmt(metric_set.amplitude_ua)); col += 1
    ws.cell(row=2, column=col, value=_fmt(metric_set.q_ph_nc)); col += 1
    ws.cell(row=2, column=col, value=_fmt(metric_set.area_um2, 0)); col += 1
    ws.cell(row=2, column=col, value=_fmt(metric_set.q_inj_mc_per_cm2)); col += 1

    # Per-phase excursions
    for k, val in enumerate(metric_set.active_excursions):
        ws.cell(row=2 + k, column=col, value=_fmt(val))
    col += 1
    if has_return_in_capture:
        for k, val in enumerate(metric_set.return_excursions):
            ws.cell(row=2 + k, column=col, value=_fmt(val))
        col += 1

    # Per-phase driving voltages (rows 2..N_phases+1)
    for k, val in enumerate(metric_set.driving_voltages):
        ws.cell(row=2 + k, column=col, value=_fmt(val))
    col += 1

    ws.cell(row=2, column=col, value=_fmt(metric_set.effective_capacitance_nf)); col += 1

    # Access voltages and resistances (one per access point)
    for k, val in enumerate(metric_set.access_voltages_v):
        ws.cell(row=2 + k, column=col, value=_fmt(val))
    col += 1
    for k, val in enumerate(metric_set.access_resistances_kohm):
        ws.cell(row=2 + k, column=col, value=_fmt(val))
    col += 1

    ws.cell(row=2, column=col, value=_fmt(metric_set.charging_capacitance_nf)); col += 1

    if metric_set.active_driving_potentials:
        for k, val in enumerate(metric_set.active_driving_potentials):
            ws.cell(row=2 + k, column=col, value=_fmt(val))
        col += 1
    if metric_set.return_driving_potentials:
        for k, val in enumerate(metric_set.return_driving_potentials):
            ws.cell(row=2 + k, column=col, value=_fmt(val))
        col += 1

    # Date Time and Status (text cells)
    ws.cell(row=2, column=col, value=metric_set.date_time); col += 1
    ws.cell(row=2, column=col, value=metric_set.status_label)

    # Reasonable column widths so headers don't clip
    for j in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(j)].width = 16


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def save_session_xlsx(session: Session, path: Path | str,
                      *, include_raw_traces: bool = True,
                      max_captures_per_sheet: int = 50) -> Path:
    """Save a session to an .xlsx workbook (Instrumentation, Parameters,
    Values, then one sheet per ChannelRun).

    Parameters
    ----------
    session : Session
        Experiment session to dump.
    path : Path | str
        Output ``.xlsx`` path. Parent directories are created.
    include_raw_traces : bool, default True
        If True, the per-electrode sheets include the raw waveform of the
        max-amplitude capture in the Time/Voltage/Current/Active/Return/
        Current Density columns. Disable for sweep-only summaries.
    max_captures_per_sheet : int, default 50
        Reserved for future use; the MATLAB-style layout shows only the
        final capture per channel, so this argument is currently a no-op.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    wb.remove(wb.active)

    _write_instrumentation_sheet(wb, session)
    _write_parameters_sheet(wb, session)
    _write_setup_sheet(wb, session)

    # Pre-compute metric sets so the Values sheet and the per-channel sheets
    # share exactly the same data and channel labels.
    used_ids: set = set()
    metric_sets: List[_ChannelMetricSet] = []
    for idx, run in enumerate(session.runs):
        cid = _channel_id(run, idx, used_ids)
        used_ids.add(cid)
        metric_sets.append(_gather_channel_metrics(run, cid))

    if metric_sets:
        _write_values_sheet(wb, session, metric_sets)
        for run_idx, (run, ms) in enumerate(zip(session.runs, metric_sets)):
            _write_electrode_sheet(wb, session, run, run_idx, ms,
                                   include_raw_traces=include_raw_traces)
    else:
        ws = wb.create_sheet("Values")
        ws.cell(row=1, column=1, value="Session has no recorded runs.")

    wb.save(str(path))
    return path


# ---------------------------------------------------------------------------
# Companion writer: actual .DTA-style tab-delimited text files
# ---------------------------------------------------------------------------
def save_session_dta(session: Session, out_dir: Path | str,
                     *, include_raw_traces: bool = True,
                     max_captures: int = 50) -> List[Path]:
    """Emit one Gamry-style ``.DTA`` text file per ChannelRun.

    The .DTA format is genuine tab-delimited: every line is either a
    ``TAG\\tKIND\\tVALUE\\tCOMMENT`` preamble row or a row inside a TABLE
    block. Returns the list of files written.
    """
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    used_ids: set = set()
    for idx, run in enumerate(session.runs):
        cid = _channel_id(run, idx, used_ids)
        used_ids.add(cid)
        fname = cid.replace(" ", "_").replace(",", "") + ".DTA"
        path = out_dir / f"{session.name}_{fname}"
        with path.open("w", encoding="utf-8") as f:
            _dta_write(f, session, run, cid, include_raw_traces, max_captures)
        paths.append(path)
    return paths


def _dta_write(f, session: Session, run: ChannelRun, channel_id: str,
               include_raw_traces: bool, max_captures: int) -> None:
    p = session.test.pattern
    cfg = run.configuration
    f.write("EXPLAIN\n")
    rows = [
        ("TAG", "", _experiment_tag(session.test.experiment),
         "Gamry-style experiment tag"),
        ("TITLE", "LABEL", session.name, "Test Identifier"),
        ("DATE", "LABEL", session.created_at.strftime("%Y-%m-%d"), "Date"),
        ("TIME", "LABEL", session.created_at.strftime("%H:%M:%S"), "Time"),
        ("USER", "LABEL", session.user_name or "", "Operator"),
        ("PSTAT", "PSTAT", "Plexon PlexStim", "Stimulator"),
        ("CHANNEL", "LABEL", channel_id, "Active vs return electrodes"),
        ("AREA", "QUANT", _fmt(run.surface_area_um2 * 1e-8),
         "Geometric surface area (cm^2)"),
        ("POLARITY", "LABEL",
         "Cathodic-first" if p.polarity == -1 else "Anodic-first",
         "Pulse polarity"),
        ("PATTERN", "LABEL",
         "Triphasic" if p.is_triphasic else "Biphasic", "Pulse pattern"),
        ("RATE", "QUANT", _fmt(p.rate_hz), "Repetition rate (Hz)"),
    ]
    for tag, kind, val, comment in rows:
        f.write(f"{tag}\t{kind}\t{val}\t{comment}\n")
    for k, ph in enumerate(p.phases, start=1):
        f.write(f"AMP{k}\tQUANT\t{_fmt(ph.amplitude_ua)}\t"
                f"Phase {k} amplitude (uA)\n")
        f.write(f"PHASEW{k}\tQUANT\t{_fmt(ph.width_us * 1e-6)}\t"
                f"Phase {k} width (s)\n")
        if ph.delay_after_us > 0:
            label = "DISCHARGE" if k == len(p.phases) else f"DELAY{k}"
            comment = ("Discharge delay (s)" if k == len(p.phases)
                       else f"Inter-phase delay {k} (s)")
            f.write(f"{label}\tQUANT\t{_fmt(ph.delay_after_us * 1e-6)}\t"
                    f"{comment}\n")

    f.write("\nSUMMARY\tTABLE\t" + str(len(run.captures)) +
            "\tPer-capture metrics\n")
    # Use Unicode subscripts / superscripts in the column headers so
    # Excel renders pretty labels straight from the tsv. Plain-text
    # ``Vd``/``Ceff`` fallback whenever a subscript letter (d, f, c)
    # has no Unicode codepoint — see ``rich.plain_label``.
    from .gui.rich import plain_label as _L
    f.write("\t".join([
        "Capture", "Amplitude",
        _L("Q", "ph"), _L("Q", "inj"),
        f"{_L('V','d')} act", f"{_L('V','d')} ret",
        _L("E", "ip"),
        f"{_L('V','a')} act", f"{_L('R','a')} act",
        f"{_L('V','a')} ret", f"{_L('R','a')} ret",
        _L("C", "eff"), _L("C", "d"),
        "Status",
    ]) + "\n")
    f.write("\t".join([
        "#", "µA",
        "nC", _L("mC/cm", sup="2"),
        "V", "V",
        "V",
        "V", "kΩ", "V", "kΩ",
        "nF", _L("mF/cm", sup="2"),
        "",
    ]) + "\n")
    for cap in run.captures:
        m = cap.metrics
        if cap.status.reached_potential_limit:
            status = "limit"
        elif cap.status.voltage_compliance:
            status = "compliance"
        elif not cap.status.good:
            status = "bad"
        else:
            status = "ok"
        va  = m.access_voltage_per_phase_v
        ra  = m.access_resistance_per_phase_kohm
        var = m.return_access_voltage_per_phase_v
        rar = m.return_access_resistance_per_phase_kohm
        vd_act = m.active_driving_voltage_per_phase_v
        vd_ret = m.return_driving_voltage_per_phase_v
        f.write("\t".join([
            str(cap.index),
            _fmt(cap.pattern.excitation_phase.amplitude_ua),
            _fmt(m.charge_per_phase_nc),
            _fmt(m.charge_injection_mc_per_cm2),
            _fmt(vd_act[0] if vd_act else m.driving_voltage_v),
            _fmt(vd_ret[0] if vd_ret else None),
            _fmt(m.interpulse_potential_v),
            _fmt(va[0] if va else None),
            _fmt(ra[0] if ra else None),
            _fmt(var[0] if var else None),
            _fmt(rar[0] if rar else None),
            _fmt(m.effective_capacitance_nf),
            _fmt(m.driving_capacitance_mf_per_cm2),
            status,
        ]) + "\n")

    if not include_raw_traces:
        return
    for cap in run.captures[:max_captures]:
        n = cap.time_us.size
        amp = cap.pattern.excitation_phase.amplitude_ua
        f.write(f"\nCURVE_{cap.index:03d}\tTABLE\t{n}\t"
                f"Capture {cap.index} @ {amp:.1f} uA\n")
        has_eact = cap.e_act_v is not None and cap.e_act_v.size == n
        has_eret = cap.e_ret_v is not None and cap.e_ret_v.size == n
        headers = ["Pt", "T", "Vf", "Im"]
        units = ["#", "s", "V", "A"]
        if has_eact:
            headers.append("Eact"); units.append("V")
        if has_eret:
            headers.append("Eret"); units.append("V")
        f.write("\t".join(headers) + "\n")
        f.write("\t".join(units) + "\n")
        t_s = np.asarray(cap.time_us) * 1e-6
        v = np.asarray(cap.v_mon_v)
        i_a = np.asarray(cap.i_mon_ua) * 1e-6
        eact = np.asarray(cap.e_act_v) if has_eact else None
        eret = np.asarray(cap.e_ret_v) if has_eret else None
        for k in range(n):
            row = [str(k), _fmt(t_s[k]), _fmt(v[k]), _fmt(i_a[k])]
            if has_eact:
                row.append(_fmt(eact[k]))
            if has_eret:
                row.append(_fmt(eret[k]))
            f.write("\t".join(row) + "\n")
