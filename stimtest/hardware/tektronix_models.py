"""Static specification and command-set database for Tektronix oscilloscope series.

Two complementary tables:

:class:`TekCommandSet`
    Every SCPI command string that differs across Tek series.  Two canonical
    instances are defined — :data:`MODERN_CMDS` and :data:`LEGACY_CMDS` — and
    each :class:`TekSeriesSpec` references one of them.

:class:`TekSeriesSpec`
    Hardware facts per series: EXT-trigger presence, valid record lengths, and
    a pointer to the command set.  Channel count is derived from the model
    string at runtime (the digit(s) after the bandwidth number encode channels).

Lookup helpers:

* :func:`get_series_spec` — look up by ``*IDN?`` model string (case-insensitive)
* :func:`get_model_spec`  — compatibility alias; returns a :class:`TekModelSpec`
  view assembled from the series entry
* :func:`snap_record_length` — round a requested length to the nearest valid
  value for a model before writing to the scope

Series groups
-------------
TBS2000/B
    TBS2074B, TBS2104B, TBS2204B, TBS2072B, TBS2102B, TBS2202B
    Modern commands, EXT trigger, 1k–5M record lengths.

TBS1000C
    TBS1052C, TBS1072C, TBS1102C, TBS1152C, TBS1202C
    Modern commands, no EXT trigger, 1k–5M record lengths.

LEGACY (TBS1000/B · TDS2000B/C · TDS1000B/C · TDS200 · TPS2000)
    All share WFMPre dialect, TRIGger:MAIn namespace, 2500-point max.
"""
from __future__ import annotations

import re
import dataclasses
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple


# ---------------------------------------------------------------------------
# Command-set definitions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TekCommandSet:
    """All SCPI command strings that vary between Tek series.

    Use the two canonical instances :data:`MODERN_CMDS` / :data:`LEGACY_CMDS`
    rather than constructing this directly.

    Field groups mirror the programmer-manual chapter structure so it is easy
    to verify each entry against the source document.
    """

    # ---- Waveform preamble -------------------------------------------------
    preamble: str
    """Namespace for waveform-preamble queries.

    Modern (TBS2000/B, TBS1000C): ``WFMOutpre``
    Legacy (TBS1000/B, TDS*):     ``WFMPre``

    Append ``:YMUlt?``, ``:YOFf?``, ``:YZEro?``, ``:XINcr?``, ``:XZEro?``,
    ``:NR_Pt?``, ``:PT_Fmt?``, ``:ENCdg?``, ``:BN_Fmt?``, ``:BYT_Or?``,
    ``:BYT_Nr?``.
    """

    # ---- Waveform data encoding --------------------------------------------
    data_encoding_cmd: str
    """Full command to set binary encoding before CURVe? readback.

    Modern: ``DATa:ENCdg RIBinary``
    Legacy: ``DATa:ENCdg RIBinary``   (same string, both support it)
    """

    data_width_cmd: str
    """Full command to set bytes-per-sample for CURVe? readback.

    Modern: ``DATa:WIDth 2``   (2-byte signed int, 16-bit ADC)
    Legacy: ``DATa:WIDth 1``   (1-byte signed int, 8-bit ADC)
    """

    # ---- Record length -----------------------------------------------------
    horiz_record: str
    """Command/query for the horizontal record length.

    Modern: ``HORizontal:RECOrdlength``
    Legacy: ``HORizontal:RECOrdLength``   (capitalisation differs)
    """

    # ---- Horizontal position -----------------------------------------------
    horiz_position: str
    """Command/query for the trigger position on screen.

    Modern: ``HORizontal:POSition``   (value in % of record, 0–100,
                                       percent of waveform left of center)
    Legacy: ``HORizontal:MAIn:POSition``   (value in seconds offset from
                                            center; equivalent alias)
    """
    horiz_position_unit: str
    """``"percent"`` (modern) or ``"seconds"`` (legacy)."""

    horiz_scale: str
    """Command/query for the horizontal scale (time-per-div).

    Modern: ``HORizontal:SCAle``
    Legacy: ``HORizontal:MAIn:SCAle``
    """

    # ---- Acquisition -------------------------------------------------------
    acq_mode: str
    """Command for acquisition mode.

    Modern: ``ACQuire:MODe {SAMple|PEAKdetect|HIRes|AVErage}``
    Legacy: ``ACQuire:MODe {SAMple|PEAKdetect|AVErage}``   (no HIRes)
    """

    acq_state: str
    """Command/query for acquisition run/stop state.

    Both: ``ACQuire:STATE {OFF|ON|RUN|STOP|<NR1>}``
    """

    acq_stop_after: str
    """Command for single-sequence mode.

    Both: ``ACQuire:STOPAfter {RUNSTop|SEQuence}``
    """

    has_acq_numavg: bool
    """True when ``ACQuire:NUMAVg <n>`` is supported."""

    acq_numavg_values: Tuple[int, ...]
    """Valid averaging counts.

    Modern: 2, 4, 8, 16, 32, 64, 128, 256, 512  (powers of two, 2–512)
    Legacy: 4, 16, 64, 128  (only these four)
    Empty tuple when ``has_acq_numavg`` is False.
    """

    has_hires: bool
    """True when ACQuire:MODe HIRes is supported (modern scopes only)."""

    # ---- Trigger -----------------------------------------------------------
    trig_type_edge_cmd: str
    """Full command to select edge-trigger type, or empty string if not needed.

    Modern: ``TRIGger:A:TYPe EDGE``
    Legacy: ``""``  (legacy scopes are always edge-triggered)
    """

    trig_edge_source: str
    """Command/query for the edge-trigger source channel.

    Modern: ``TRIGger:A:EDGE:SOUrce``
    Legacy: ``TRIGger:MAIn:EDGe:SOUrce``
    """

    trig_edge_slope: str
    """Command for the edge-trigger slope (RISe / FALL).

    Modern: ``TRIGger:A:EDGE:SLOpe``
    Legacy: ``TRIGger:MAIn:EDGe:SLOpe``
    """

    trig_edge_coupling: str
    """Command for the edge-trigger coupling (DC / AC / HFRej / …).

    Modern: ``TRIGger:A:EDGE:COUPling``
    Legacy: ``TRIGger:MAIn:EDGE:COUPling``
    """

    trig_level: str
    """Command for the trigger threshold level (volts).

    Modern: ``TRIGger:A:LEVel``
    Legacy: ``TRIGger:LEVel``
    """

    trig_mode: str
    """Command for trigger mode (NORMal / AUTO).

    Modern: ``TRIGger:A:MODe``
    Legacy: ``TRIGger:MODe``
    """

    # ---- Channel -----------------------------------------------------------
    ch_coupling: str
    """Format string for channel coupling.

    Both: ``"{ch}:COUPling {coupling}"``
    """

    ch_bandwidth: str
    """Format string for channel bandwidth limit.

    Modern: ``"{ch}:BANdwidth {bw}"``   values: TWEnty | FULl | <NR3>
    Legacy: ``"{ch}:BANdwidth {bw}"``   values: TWenty | FULl   (no <NR3>)
    """

    ch_scale: str
    """Format string for vertical scale (V/div).

    Both: ``"{ch}:SCAle {v}"``
    """

    ch_position: str
    """Format string for vertical position (divisions).

    Both: ``"{ch}:POSition {div}"``
    """

    ch_invert: str
    """Format string for channel inversion.

    Both: ``"{ch}:INVert {state}"``
    """

    ch_yunit: str
    r"""Format string for vertical unit label.

    Both: ``'{ch}:YUNit "{unit}"'``
    """

    probe_cmd_tmpl: str
    """Format string for setting 1× probe attenuation.

    Use ``tmpl.format(ch="CH1")`` → full SCPI command.
    Modern: ``"{ch}:PRObe:GAIN 1"``
    Legacy: ``"{ch}:PRObe 1"``
    """

    # ---- Select (display) --------------------------------------------------
    select_ch: str
    """Format string to turn a channel display on or off.

    Both: ``"SELect:{ch} {state}"``
    """

    # ---- Data source -------------------------------------------------------
    use_data_source: bool
    """True when ``DATa:SOUrce <ch>`` can select the readback channel.

    False on very old TDS firmware where the channel must be selected via
    the front-panel and ``DATa:SOUrce`` is read-only.
    """

    # ---- Horizontal SEC/DIV grid ------------------------------------------
    timebase_grid_mantissas: Tuple[float, ...] = (1.0, 2.0, 4.0)
    """Per-decade mantissas of the front-panel SEC/DIV knob.

    The scope silently quantises any off-grid ``HORizontal:SCAle`` write
    to the nearest entry on this knob, so we have to snap host-side to
    the *true* sequence — otherwise our cached scale drifts from what
    the scope is actually showing and downstream layout math
    (trigger-marker placement, window-width) goes wrong.

    Empirically verified per family:

    * **Modern** (TBS2000B / TBS2204B / MSO / MDO / DPO): ``(1, 2, 4)``
      — the programmer manual's "1-2-5 sequence" claim is **wrong**;
      the actual knob reads 1, 2, 4, 10, 20, 40, 100, 200, 400, 1000 …
    * **Legacy** (TBS1000B / TDS2000/3000): ``(1, 2.5, 5)``
      — the MATLAB ``setOscillocopeView.m`` candidate list
      ``[2.5 5 10 25 50 100 250 500 1000]`` µs/div was correct for
      this family, which is what the original collaborator scope was.

    Driver code combines these mantissas with decade exponents to build
    the full grid (see ``TektronixOscilloscope._timebase_grid_seconds``).
    """


# ---------------------------------------------------------------------------
# Canonical command-set instances
# ---------------------------------------------------------------------------

MODERN_CMDS = TekCommandSet(
    # Waveform preamble
    preamble                = "WFMOutpre",
    # Waveform data
    data_encoding_cmd       = "DATa:ENCdg RIBinary",
    data_width_cmd          = "DATa:WIDth 2",
    # Horizontal
    horiz_record            = "HORizontal:RECOrdlength",
    horiz_position          = "HORizontal:POSition",
    horiz_position_unit     = "percent",
    horiz_scale             = "HORizontal:SCAle",
    # Acquisition
    acq_mode                = "ACQuire:MODe",
    acq_state               = "ACQuire:STATE",
    acq_stop_after          = "ACQuire:STOPAfter",
    has_acq_numavg          = True,
    acq_numavg_values       = (2, 4, 8, 16, 32, 64, 128, 256, 512),
    has_hires               = True,
    # Trigger
    trig_type_edge_cmd      = "TRIGger:A:TYPe EDGE",
    trig_edge_source        = "TRIGger:A:EDGE:SOUrce",
    trig_edge_slope         = "TRIGger:A:EDGE:SLOpe",
    trig_edge_coupling      = "TRIGger:A:EDGE:COUPling",
    trig_level              = "TRIGger:A:LEVel",
    trig_mode               = "TRIGger:A:MODe",
    # Channel
    ch_coupling             = "{ch}:COUPling {coupling}",
    ch_bandwidth            = "{ch}:BANdwidth {bw}",
    ch_scale                = "{ch}:SCAle {v}",
    ch_position             = "{ch}:POSition {div}",
    ch_invert               = "{ch}:INVert {state}",
    ch_yunit                = '{ch}:YUNit "{unit}"',
    probe_cmd_tmpl          = "{ch}:PRObe:GAIN 1",
    # Select
    select_ch               = "SELect:{ch} {state}",
    # Data source
    use_data_source         = True,
    # SEC/DIV knob — TBS2204B is 1-2-4, not 1-2-5 (verified empirically)
    timebase_grid_mantissas = (1.0, 2.0, 4.0),
)

LEGACY_CMDS = TekCommandSet(
    # Waveform preamble
    preamble                = "WFMPre",
    # Waveform data — legacy is 8-bit ADC
    data_encoding_cmd       = "DATa:ENCdg RIBinary",
    data_width_cmd          = "DATa:WIDth 1",
    # Horizontal
    horiz_record            = "HORizontal:RECOrdLength",
    horiz_position          = "HORizontal:MAIn:POSition",
    horiz_position_unit     = "seconds",
    horiz_scale             = "HORizontal:MAIn:SCAle",
    # Acquisition — no HIRes; NUMAVg restricted to {4, 16, 64, 128}
    acq_mode                = "ACQuire:MODe",
    acq_state               = "ACQuire:STATE",
    acq_stop_after          = "ACQuire:STOPAfter",
    has_acq_numavg          = True,
    acq_numavg_values       = (4, 16, 64, 128),
    has_hires               = False,
    # Trigger — no TYPe command; always edge; MAIn namespace
    trig_type_edge_cmd      = "",
    trig_edge_source        = "TRIGger:MAIn:EDGe:SOUrce",
    trig_edge_slope         = "TRIGger:MAIn:EDGe:SLOpe",
    trig_edge_coupling      = "TRIGger:MAIn:EDGE:COUPling",
    trig_level              = "TRIGger:LEVel",
    trig_mode               = "TRIGger:MODe",
    # Channel
    ch_coupling             = "{ch}:COUPling {coupling}",
    ch_bandwidth            = "{ch}:BANdwidth {bw}",
    ch_scale                = "{ch}:SCAle {v}",
    ch_position             = "{ch}:POSition {div}",
    ch_invert               = "{ch}:INVert {state}",
    ch_yunit                = '{ch}:YUNit "{unit}"',
    probe_cmd_tmpl          = "{ch}:PRObe 1",
    # Select
    select_ch               = "SELect:{ch} {state}",
    # Data source
    use_data_source         = True,
    # SEC/DIV knob — TBS1000B / TDS2000/3000 use 1-2.5-5 (matches the
    # original MATLAB setOscillocopeView.m candidate list)
    timebase_grid_mantissas = (1.0, 2.5, 5.0),
)


# ---------------------------------------------------------------------------
# Series specification
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TekBandwidthOption:
    """One ``CHx:BANdwidth`` SCPI value paired with its analog cutoff (MHz).

    Lets the GUI offer a "use 20 MHz limit on the I_mon channel" toggle
    without hard-coding scope-family-specific mnemonics.
    """
    scpi_value: str
    """The exact token to send, e.g. ``"FULl"``, ``"TWEnty"``, ``"ON"``."""

    cutoff_mhz: float
    """Analog −3 dB point at this setting, in MHz.  Use ``float('inf')``
    for "no software-imposed limit" if the model lacks the option."""

    label: str
    """Short human-readable label for the GUI dropdown,
    e.g. ``"Full (200 MHz)"`` or ``"20 MHz limit"``."""


@dataclass(frozen=True)
class TekSeriesSpec:
    """Hardware facts shared by all models in a series.

    Channel count is NOT stored here — derive it from the model string with
    :func:`channel_count_from_model`.
    """
    series_name: str
    """Human-readable series label, e.g. ``"TBS2000B"``."""

    pattern: str
    """Case-insensitive regex that matches the model strings in this series.

    Applied to the raw ``*IDN?`` model field (stripped, upper-cased).
    """

    commands: TekCommandSet
    """Command-set instance for this series."""

    has_ext_trigger: bool
    """True when the scope has a dedicated EXT BNC trigger input."""

    record_lengths: Tuple[int, ...]
    """All valid horizontal record lengths, ascending."""

    bandwidth_options: Tuple[TekBandwidthOption, ...] = ()
    """Available ``CHx:BANdwidth`` filter values for this series, in
    "full → most-limited" order.  Empty tuple means the scope rejects
    the BANdwidth command entirely (older firmware)."""

    recommended_imon_bw: Optional[TekBandwidthOption] = None
    """The bandwidth setting we recommend for the I_mon channel during
    stim sweeps.  MATLAB convention: use the *limited* preset, not Full,
    so the trigger comparator sees a clean signal that comfortably
    exceeds the MATLAB ``setTriggerLevel.m`` threshold."""

    n_horiz_divs: int = 10
    """Number of horizontal divisions on the scope display.

    Most Tek families (TBS1000, TDS, TPS) use 10; the TBS2000* family
    (TBS2000, TBS2000B, TBS2000C) uses 15.  This is queryable on
    TBS2000* via ``HORizontal:DIVisions?`` but we keep the per-series
    default here as a fallback when the SCPI query fails and as
    documentation.
    """

    n_vert_divs: int = 8
    """Number of vertical divisions on the scope display.

    There is NO SCPI query for this on the Tek TBS/TDS/TPS families —
    it's a fixed display property per series:

    * **TBS2000 / TBS2000B / TBS2000C** — **10 vertical divs**
      (±5 from screen centre).  The screen layout was redesigned for
      the 2-series and matches the 15-horizontal-div change.
    * **TBS1000 / TBS1000B / TBS1000C, TDS200, TDS1000B/C,
      TDS2000B/C, TPS2000** — **8 vertical divs** (±4 from screen
      centre).  Classic Tek layout.

    Used by the in-view rescale loop's clip detector (the rail sits
    at ``±(n_vert_divs/2) · vpd + vertPos``) and by the runner's
    ``MAX_FACTOR`` budget (``n_vert_divs/2 − 0.1``).  The original
    MATLAB ``getWaveform2.m`` baked in 4.0 (and used 3.5-4.5 as
    ``MAX_FACTOR`` candidates) because it targeted the TBS1104B
    (8-div); the same code on a TBS2204B (10-div) was implicitly
    under-using the screen.
    """


# ---------------------------------------------------------------------------
# Bandwidth-option canonical instances
# ---------------------------------------------------------------------------
# TBS2000B/C and TBS1000B/C accept BANdwidth FULl | TWEnty
# (per programmer manual).  ``cutoff_mhz`` for FULl is filled in by
# :func:`get_series_spec` at lookup time based on the model's analog BW.
_BW_FULL_GENERIC = TekBandwidthOption(
    scpi_value="FULl",
    cutoff_mhz=float("inf"),  # placeholder; replaced by model-specific BW below
    label="Full (scope max)",
)
_BW_TWENTY = TekBandwidthOption(
    scpi_value="TWEnty",
    cutoff_mhz=20.0,
    label="20 MHz limit",
)
# Legacy TDS / TBS1000 (pre-C) use BANdwidth ON | OFF where ON = 20 MHz.
_BW_LEGACY_ON  = TekBandwidthOption(
    scpi_value="ON",   cutoff_mhz=20.0,        label="20 MHz limit (ON)",
)
_BW_LEGACY_OFF = TekBandwidthOption(
    scpi_value="OFF",  cutoff_mhz=float("inf"), label="Full (OFF)",
)


# ---------------------------------------------------------------------------
# Per-model analog bandwidth (MHz)
# ---------------------------------------------------------------------------
# Tek's naming convention encodes BW in the digits before the channel-count
# digit (e.g. TBS2204B → "220" + "4" + "B" → 200 MHz, 4 channels).  We keep an
# explicit table so unusual cases (TPS / MSO / MDO) don't get mis-parsed.
_MODEL_BANDWIDTH_MHZ: Dict[str, int] = {
    # TBS2000/B (2-series)
    "TBS2074B": 70,   "TBS2104B": 100,  "TBS2204B": 200,
    "TBS2072B": 70,   "TBS2102B": 100,  "TBS2202B": 200,
    "TBS2074":  70,   "TBS2104":  100,  "TBS2204":  200,
    "TBS2072":  70,   "TBS2102":  100,  "TBS2202":  200,
    # TBS2000C — same BW as B revision
    "TBS2074C": 70,   "TBS2104C": 100,  "TBS2204C": 200,
    "TBS2072C": 70,   "TBS2102C": 100,  "TBS2202C": 200,
    # TBS1000C
    "TBS1052C":  50,  "TBS1072C":  70,  "TBS1102C": 100,
    "TBS1152C": 150,  "TBS1202C": 200,
    # TBS1000B
    "TBS1052B":  50,  "TBS1072B":  70,  "TBS1102B": 100,
    "TBS1152B": 150,  "TBS1202B": 200,
    # TBS1000 (no suffix)
    "TBS1052":   50,  "TBS1072":   70,  "TBS1102": 100,
    # TDS2000B/C
    "TDS2002B":  60,  "TDS2004B":  60,  "TDS2012B": 100,
    "TDS2014B": 100,  "TDS2022B": 200,  "TDS2024B": 200,
    "TDS2002C":  70,  "TDS2004C":  70,  "TDS2012C": 100,
    "TDS2014C": 100,  "TDS2022C": 200,  "TDS2024C": 200,
    # TDS1000B/C
    "TDS1001B":  40,  "TDS1002B":  60,  "TDS1012B": 100,
    "TDS1001C":  40,  "TDS1002C":  50,  "TDS1012C": 100,
    # TDS200
    "TDS210":    60,  "TDS220":   100,  "TDS224":  100,
    # TPS2000
    "TPS2012": 100,   "TPS2014": 100,   "TPS2024": 200,
}


def bandwidth_mhz_for_model(model: str) -> Optional[int]:
    """Return the analog bandwidth in MHz for *model*, or None if unknown.

    Useful for log messages and for filling in the ``cutoff_mhz`` of the
    "Full" :class:`TekBandwidthOption` at runtime.
    """
    return _MODEL_BANDWIDTH_MHZ.get((model or "").strip().upper())


# ---------------------------------------------------------------------------
# Series database
# ---------------------------------------------------------------------------
# Ordered from most-specific to least-specific so the first match wins.
_SERIES: Sequence[TekSeriesSpec] = [

    # ---- TBS2000/B — 4- and 2-channel, modern, NO EXT trigger -------------
    # Models: TBS2074B TBS2104B TBS2204B  TBS2072B TBS2102B TBS2202B
    # NUMAVg valid set confirmed on TBS2204B: 2, 4, 16, 32, 64, 128, 256, 512
    # (8 is not accepted; 32 works in practice even though not in the manual).
    #
    # NOTE: ``has_ext_trigger`` was originally True because Tek's
    # SCPI namespace lists ``AUX`` as a valid trigger source on
    # neighbouring series, and the bench's TBS2204B has a rear-panel
    # connector labelled ``AUX OUT``.  That connector is the **probe-
    # compensation output** (a ~5 V square wave) — NOT a trigger
    # input.  Per Tek's TBS2000B programming manual the valid
    # ``TRIGger:A:EDGE:SOURce`` values are only ``CH1, CH2, CH3,
    # CH4, AC LINE`` — no EXT, no AUX.  Flipped to False on bench
    # confirmation from the operator that no AUX input exists.
    TekSeriesSpec(
        series_name     = "TBS2000B",
        pattern         = r"TBS2\d{3}B",
        commands        = dataclasses.replace(
            MODERN_CMDS,
            acq_numavg_values=(2, 4, 16, 32, 64, 128, 256, 512),
        ),
        has_ext_trigger = False,
        record_lengths  = (1_000, 2_000, 20_000, 200_000, 2_000_000, 5_000_000),
        bandwidth_options    = (_BW_FULL_GENERIC, _BW_TWENTY),
        recommended_imon_bw  = _BW_TWENTY,
        # TBS2000B redesigned screen: 15 horizontal × 10 vertical divs
        # (vs the classic 10 × 8 layout on every other Tek family in
        # this registry).
        n_horiz_divs    = 15,
        n_vert_divs     = 10,
    ),

    # ---- TBS1000C — 2-channel, modern, NO EXT trigger ---------------------
    # Models: TBS1052C TBS1072C TBS1102C TBS1152C TBS1202C
    TekSeriesSpec(
        series_name     = "TBS1000C",
        pattern         = r"TBS1\d{3}C",
        commands        = MODERN_CMDS,
        has_ext_trigger = False,
        record_lengths  = (1_000, 2_000, 20_000, 200_000, 2_000_000, 5_000_000),
        bandwidth_options    = (_BW_FULL_GENERIC, _BW_TWENTY),
        recommended_imon_bw  = _BW_TWENTY,
    ),

    # ---- TBS1000B — 2-channel, legacy, NO EXT trigger, max 2500 pts -------
    # Models: TBS1052B TBS1072B TBS1102B TBS1152B TBS1202B
    TekSeriesSpec(
        series_name     = "TBS1000B",
        pattern         = r"TBS1\d{3}B",
        commands        = LEGACY_CMDS,
        has_ext_trigger = False,
        record_lengths  = (2_500,),
        bandwidth_options    = (_BW_LEGACY_OFF, _BW_LEGACY_ON),
        recommended_imon_bw  = _BW_LEGACY_ON,
    ),

    # ---- TBS1000 (original) — 2-channel, legacy, max 2500 pts -------------
    # Models: TBS1052 TBS1072 TBS1102
    TekSeriesSpec(
        series_name     = "TBS1000",
        pattern         = r"TBS1\d{3}$",
        commands        = LEGACY_CMDS,
        has_ext_trigger = False,
        record_lengths  = (2_500,),
        bandwidth_options    = (_BW_LEGACY_OFF, _BW_LEGACY_ON),
        recommended_imon_bw  = _BW_LEGACY_ON,
    ),

    # ---- TDS2000B/C — 2-channel, legacy, max 2500 pts ---------------------
    # Models: TDS2002B TDS2004B TDS2012B TDS2014B TDS2022B TDS2024B
    #         TDS2002C TDS2004C TDS2012C TDS2014C TDS2022C TDS2024C
    TekSeriesSpec(
        series_name     = "TDS2000B/C",
        pattern         = r"TDS2\d{3}[BC]",
        commands        = LEGACY_CMDS,
        has_ext_trigger = True,
        record_lengths  = (2_500,),
        bandwidth_options    = (_BW_LEGACY_OFF, _BW_LEGACY_ON),
        recommended_imon_bw  = _BW_LEGACY_ON,
    ),

    # ---- TDS1000B/C — 2-channel, legacy, max 2500 pts ---------------------
    # Models: TDS1001B TDS1002B TDS1012B  TDS1001C TDS1002C TDS1012C
    TekSeriesSpec(
        series_name     = "TDS1000B/C",
        pattern         = r"TDS1\d{3}[BC]",
        commands        = LEGACY_CMDS,
        has_ext_trigger = False,
        record_lengths  = (2_500,),
        bandwidth_options    = (_BW_LEGACY_OFF, _BW_LEGACY_ON),
        recommended_imon_bw  = _BW_LEGACY_ON,
    ),

    # ---- TDS200 — 2-channel, legacy, max 2500 pts -------------------------
    # Models: TDS210 TDS220 TDS224
    TekSeriesSpec(
        series_name     = "TDS200",
        pattern         = r"TDS2\d{2}$",
        commands        = LEGACY_CMDS,
        has_ext_trigger = False,
        record_lengths  = (2_500,),
        bandwidth_options    = (_BW_LEGACY_OFF, _BW_LEGACY_ON),
        recommended_imon_bw  = _BW_LEGACY_ON,
    ),

    # ---- TPS2000 — 4-channel isolated, legacy, max 2500 pts ---------------
    # Models: TPS2012 TPS2014 TPS2024
    TekSeriesSpec(
        series_name     = "TPS2000",
        pattern         = r"TPS2\d{3}",
        commands        = LEGACY_CMDS,
        has_ext_trigger = False,
        record_lengths  = (2_500,),
        bandwidth_options    = (_BW_LEGACY_OFF, _BW_LEGACY_ON),
        recommended_imon_bw  = _BW_LEGACY_ON,
    ),
]

# Pre-compile patterns once at import time.
_SERIES_COMPILED = [(re.compile(s.pattern, re.IGNORECASE), s) for s in _SERIES]


# ---------------------------------------------------------------------------
# Compatibility shim — keep TekModelSpec for code that still imports it
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TekModelSpec:
    """Thin view assembled from a :class:`TekSeriesSpec` for a specific model.

    Prefer :func:`get_series_spec` for new code.  This exists so that callers
    that import ``TekModelSpec`` continue to work unchanged.
    """
    n_channels: int
    bandwidth_mhz: int
    record_lengths: Tuple[int, ...]
    has_ext_trigger: bool
    commands: TekCommandSet


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def channel_count_from_model(model: str) -> int:
    """Return the channel count encoded in *model*, or 2 if unrecognisable.

    Tektronix encodes the channel count as the last digit of the model number
    (before any suffix letter).  Examples:

    * ``TBS2074B`` → digit **4** → 4 channels
    * ``TBS1072C`` → digit **2** → 2 channels
    * ``TDS2004C`` → digit **4** → 4 channels
    """
    m = re.search(r"(\d)(?:[A-Z]*)$", (model or "").strip().upper())
    if m:
        d = int(m.group(1))
        if d in (2, 4):
            return d
    return 2


def _bandwidth_from_model(model: str) -> int:
    """Return the bandwidth (MHz) from the numeric part of *model*, or 0."""
    m = re.search(r"(\d{3,4})(?:\d[A-Z]*)?$", (model or "").strip().upper())
    if m:
        raw = m.group(1)
        # The first 2–3 digits encode bandwidth; last digit is channel count.
        # e.g. TBS2074B → "207" → bw digits "20" → 20? No — Tek encodes bw
        # differently: TBS2074B = 70 MHz, so "07" is the bw prefix.
        # Pattern: the numeric block before the channel digit is the bw * 10
        # if it ends in the channel digit.  Simplest heuristic: strip last
        # digit, parse as bandwidth.
        bw_str = raw[:-1]  # drop channel-count digit
        try:
            return int(bw_str)
        except ValueError:
            pass
    return 0


def get_series_spec(model: str) -> Optional[TekSeriesSpec]:
    """Return the :class:`TekSeriesSpec` for *model*, or ``None`` if unknown.

    The series spec stores ``cutoff_mhz=float('inf')`` for its "Full"
    bandwidth option as a placeholder; this lookup fills in the model's
    actual analog bandwidth (e.g. 200 MHz for TBS2204B, 70 MHz for
    TBS2072B) so log messages and downstream consumers see the real
    cutoff instead of an opaque "inf".
    """
    key = (model or "").strip().upper()
    for pattern, spec in _SERIES_COMPILED:
        if pattern.fullmatch(key):
            bw_max = bandwidth_mhz_for_model(key)
            if bw_max is None or not spec.bandwidth_options:
                return spec
            # Rebuild the bandwidth_options tuple with the model-specific
            # max BW substituted into any "inf" placeholder.  Pure
            # function: original spec instance is untouched.
            new_options = tuple(
                dataclasses.replace(
                    opt, cutoff_mhz=float(bw_max),
                    label=f"Full ({bw_max} MHz)")
                if opt.cutoff_mhz == float("inf")
                else opt
                for opt in spec.bandwidth_options
            )
            return dataclasses.replace(spec, bandwidth_options=new_options)
    return None


def get_model_spec(model: str) -> Optional[TekModelSpec]:
    """Return a :class:`TekModelSpec` view for *model*, or ``None`` if unknown.

    Assembles channel count and bandwidth from the model string; all other
    fields come from the matching :class:`TekSeriesSpec`.
    """
    series = get_series_spec(model)
    if series is None:
        return None
    return TekModelSpec(
        n_channels      = channel_count_from_model(model),
        bandwidth_mhz   = _bandwidth_from_model(model),
        record_lengths  = series.record_lengths,
        has_ext_trigger = series.has_ext_trigger,
        commands        = series.commands,
    )


def snap_record_length(n: int, model: str) -> int:
    """Round *n* to the nearest valid record length for *model*.

    If the model is unknown, returns *n* unchanged (the scope will silently
    round it, and ``set_record_length`` reads back the actual value).
    Snapping on the host avoids surprises: e.g. requesting 2500 on a
    TBS2204B (valid set: 1000/2000/20000/…) snaps to 2000 before the write.
    """
    series = get_series_spec(model)
    if series is None:
        return n
    return min(series.record_lengths, key=lambda v: abs(v - n))
