"""Readback calibration — apply per-channel correction coefficients to
captured waveforms.

The stimulator verification wizard (``stimtest.gui.calibration``) sweeps
known amplitudes against the test board and fits a linear model per channel:

    I_actual (µA) = a · I_mon_raw (µA) + b

where ``I_mon_raw`` is derived from the raw I_mon scope voltage using the
nominal ``imon_scaling_v_per_ua``, and ``I_actual`` is the independently
recovered true current (from the V_mon edge step on the known resistive
load).

Applying the inverse of that model during experiments corrects the I_mon
readback so stored ``Capture.i_mon_ua`` traces reflect the true delivered
current rather than the uncorrected hardware readout.

The V_mon readback can also be corrected: the verification sweep estimates
the actual ``vmon_v_per_v`` from the edge step, which may differ slightly
from the nominal value burned into ``StimulatorInfo``.  A per-device
``vmon_v_per_v_actual`` scalar is applied the same way.

Usage
-----
Coefficients are loaded once per session and stored on the runner:

    cal = load_calibration(stim_serial="PX00123")
    cap = make_capture(idx, pattern, acq, scope, stim, cal=cal)

If ``cal`` is ``None`` (no calibration file found, or no entry for this
serial) ``make_capture`` falls back to the nominal hardware scalings and
behaves identically to the pre-calibration code path.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import numpy as np


#: Plausible bounds for the per-channel I_mon correction ``I = a·I_raw + b``.
#: A legitimate gain ``a`` is a SMALL correction (~O(1)); a legitimate offset
#: ``b`` is at most a few thousand µA.  A verification run on BAD captures
#: (railing V_mon / a mis-triggered I_mon on a 2-channel scope) can fit a
#: DEGENERATE ``np.polyfit`` slope — ``a ≈ 1e14`` was observed at CWRU —
#: which ``apply_imon`` would then multiply every I_mon reading by, turning
#: all current data into physical nonsense (±1e8 A).  ``ChannelCoeffs.
#: is_plausible`` rejects such coefficients so a single bad verification can
#: never corrupt current data.  Kept deliberately GENEROUS (100× / 1⁄100×
#: corrections are already implausibly large) so only clear garbage is
#: dropped, never a legitimately unusual calibration.
_IMON_GAIN_MIN = 0.1
_IMON_GAIN_MAX = 10.0
_IMON_OFFSET_MAX_UA = 1.0e4

from .session import Capture
from .waveforms import PulsePattern


# ---------------------------------------------------------------------------
# Coefficient container
# ---------------------------------------------------------------------------

@dataclass
class ChannelCoeffs:
    """Correction coefficients for one stimulator channel.

    ``I_corrected = a * I_raw + b``  (both in µA)

    ``a`` defaults to 1.0 and ``b`` to 0.0 so an entry with no
    measured deviation is a no-op.
    """
    a: float = 1.0   # gain correction
    b: float = 0.0   # offset correction (µA)

    def is_plausible(self) -> bool:
        """True when the coefficients are a physically-sensible correction.

        A degenerate verification fit (e.g. a ~1e14 gain from bad captures)
        must be IGNORED rather than applied — applying it turns every I_mon
        reading into nonsense (the CWRU "current not read correctly" bug).
        See the module-level ``_IMON_*`` bounds."""
        return (math.isfinite(self.a) and math.isfinite(self.b)
                and _IMON_GAIN_MIN <= abs(self.a) <= _IMON_GAIN_MAX
                and abs(self.b) <= _IMON_OFFSET_MAX_UA)


@dataclass
class ReadbackCalibration:
    """All per-channel coefficients for one stimulator serial number,
    plus global offset and scaling corrections for V_mon and I_mon.

    ``channels`` is keyed by 1-based channel number (int).

    Correction pipeline (applied in ``make_capture``):

    V_mon:
        v_mon_v = (raw_scope_v - vmon_offset_v) / vmon_v_per_v_actual

    I_mon:
        i_mon_ua = (raw_scope_v - imon_offset_v) / imon_v_per_ua_actual
        then per-channel: i_mon_ua = a * i_mon_ua + b
    """
    serial: str = ""
    channels: Dict[int, ChannelCoeffs] = field(default_factory=dict)
    vmon_v_per_v_actual: Optional[float] = None   # None → use nominal
    imon_v_per_ua_actual: Optional[float] = None  # None → use nominal
    vmon_offset_v: Optional[float] = None          # DC bias subtracted from raw V_mon
    imon_offset_v: Optional[float] = None          # DC bias subtracted from raw I_mon

    def coeffs(self, channel: int) -> ChannelCoeffs:
        """Return coefficients for *channel*, or identity if not found."""
        return self.channels.get(channel, ChannelCoeffs())

    def apply_imon(self, i_mon_ua: np.ndarray, channel: int) -> np.ndarray:
        """Apply per-channel gain + offset to an I_mon trace (µA array).

        The global offset and magnification are applied upstream in
        ``make_capture`` before this is called.
        """
        c = self.coeffs(channel)
        if not c.is_plausible():
            # Degenerate / corrupt per-channel calibration (e.g. a bad
            # verification fit producing a ~1e14 gain) — IGNORE it rather
            # than multiply I_mon into physical nonsense.  A bad calibration
            # must never destroy current data; the channel falls back to the
            # preset scaling (identity).  This is the CWRU root-cause guard.
            return i_mon_ua
        if c.a == 1.0 and c.b == 0.0:
            return i_mon_ua
        # Skip the np.asarray wrap when the input is already float64
        # — saves a needless array copy/wrap on the hot capture path
        # (thousands of captures per long-pulsing session).
        arr = i_mon_ua if (isinstance(i_mon_ua, np.ndarray)
                           and i_mon_ua.dtype == np.float64
                           ) else np.asarray(i_mon_ua, dtype=float)
        return c.a * arr + c.b

    def apply_vmon(self, v_mon_v: np.ndarray,
                   nominal_vmon_v_per_v: float) -> np.ndarray:
        """Re-scale a V_mon trace (V array) using the actual vmon factor
        if known, otherwise leave it unchanged.

        Offset correction is applied upstream in ``make_capture``.
        """
        actual = self.vmon_v_per_v_actual
        if actual is None or actual == 0.0 or nominal_vmon_v_per_v == 0.0:
            return v_mon_v
        arr = v_mon_v if (isinstance(v_mon_v, np.ndarray)
                          and v_mon_v.dtype == np.float64
                          ) else np.asarray(v_mon_v, dtype=float)
        return arr * (nominal_vmon_v_per_v / actual)


# ---------------------------------------------------------------------------
# File location  (mirrors gui/calibration.py's calibration_path())
# ---------------------------------------------------------------------------

_CALIBRATION_FILE = "calibration.json"


def _calibration_path() -> Path:
    """Return the path to calibration.json without importing any Qt module."""
    from .gui.prefs import prefs_dir
    return prefs_dir() / _CALIBRATION_FILE


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_calibration(stim_serial: str = "",
                     path: Optional[Path] = None) -> Optional[ReadbackCalibration]:
    """Load per-channel coefficients from *calibration.json*.

    Parameters
    ----------
    stim_serial:
        Serial number of the connected stimulator.  The JSON payload is
        keyed by serial; if *stim_serial* is empty or not present in the
        file the function returns ``None``.
    path:
        Override the default file location (useful for testing).

    Returns
    -------
    :class:`ReadbackCalibration` if a matching record is found, else ``None``.
    """
    try:
        p = path or _calibration_path()
        if not p.exists():
            return None
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    # Schema written by write_calibration_payload() in gui/calibration.py:
    #
    #   {
    #     "schema_version": 2,
    #     "stimulator": {"serial_number": "PLX00180", ...},
    #     "coefficients": {"1": {"a": 1.002, "b": -0.3}, ...},
    #     "vmon_v_per_v_actual": 0.248   (optional, added by this module)
    #   }
    serial_key = stim_serial or ""
    file_serial = payload.get("stimulator", {}).get("serial_number", "")

    # Refuse to apply a calibration recorded against a different device.
    if serial_key and file_serial and serial_key != file_serial:
        return None

    coef_block = payload.get("coefficients")
    if not isinstance(coef_block, dict):
        return None

    channels: Dict[int, ChannelCoeffs] = {}
    for ch_str, coef in coef_block.items():
        try:
            ch = int(ch_str)
            a = float(coef.get("a", 1.0))
            b = float(coef.get("b", 0.0))
            cc = ChannelCoeffs(a=a, b=b)
            if not cc.is_plausible():
                # A corrupt/degenerate saved gain (e.g. the ~1e14 CWRU
                # verification fit) — DROP it so it can't corrupt data; the
                # channel falls back to the preset scaling (identity default
                # from ``coeffs()``).  A single bad verification must never
                # poison every subsequent session's current readings.
                continue
            channels[ch] = cc
        except Exception:
            continue

    def _optional_float(key):
        v = payload.get(key)
        try:
            return float(v) if v is not None else None
        except Exception:
            return None

    return ReadbackCalibration(
        serial=serial_key,
        channels=channels,
        vmon_v_per_v_actual=_optional_float("vmon_v_per_v_actual"),
        imon_v_per_ua_actual=_optional_float("imon_v_per_ua_actual"),
        vmon_offset_v=_optional_float("vmon_offset_v"),
        imon_offset_v=_optional_float("imon_offset_v"),
    )


# ---------------------------------------------------------------------------
# Shared make_capture — replaces the duplicate in each experiment file
# ---------------------------------------------------------------------------

def per_capture_baseline(arr, t_us) -> float:
    """Idle (pre-pulse) DC level of one captured trace.

    Returns the DC level the channel was sitting at while idle, so the
    caller can subtract it and plot a waveform whose interpulse baseline
    starts at zero regardless of the scope's per-capture DC bias.

    **Use the LEADING-EDGE samples (earliest in the record), NOT the
    whole pre-trigger window.**  ``auto_layout_for_pulse`` always places
    3-4 divisions of idle baseline BEFORE the first pulse phase, so the
    earliest samples are reliably idle EVEN WHEN the scope trigger sits
    mid-pulse.

    This is the fix for the "wrong offset" bug: when the trigger is the
    I_mon protocol (no digital sync channel), it fires on the ANODIC
    current edge, so for a cathodic-first pulse the CATHODIC phase
    occupies the whole pre-trigger window (``t < 0``).  Averaging over
    ``t < -1 µs`` then returns the CATHODIC level (≈ −amplitude), not
    the idle level — and subtracting that shifts the entire trace UP by
    ~one pulse amplitude (interpulse 0 → +amplitude, cathodic → 0,
    anodic → +2·amplitude).  Sampling only the leading edge avoids the
    cathodic phase entirely.

      * **Window**: the first ``min(n//16, 800)`` samples (≈ 0.4 div on
        a 20k-point record), floored at 8 — small enough to stay inside
        even a ~1-div leading-idle margin, large enough for a clean
        mean.  Samples are time-ordered first (when ``t_us`` is usable)
        so "earliest" means earliest-in-time.
      * **MAD-clip**: drop samples > 3 σ (MAD × 1.4826) from the median
        so a stray edge sample doesn't drag the mean.

    Returns ``0.0`` when ``arr`` is None / too short to estimate.

    NOTE: kept in lock-step with
    :func:`stimtest.gui.calibration._per_capture_baseline` — both views
    of the same waveform must define "idle baseline" identically.
    """
    import numpy as _np
    try:
        if arr is None or len(arr) < 8:
            return 0.0
        a_arr = _np.asarray(arr, dtype=float)
        n = a_arr.size
        # Order by time so the leading window is the earliest-in-time
        # samples even if the raw array isn't stored in time order.
        if t_us is not None and len(t_us) == n:
            order = _np.argsort(_np.asarray(t_us, dtype=float))
            a_arr = a_arr[order]
        n_lead = max(8, min(800, n // 16))
        samples = a_arr[:n_lead]
        med = float(_np.median(samples))
        mad = float(_np.median(_np.abs(samples - med)))
        if mad > 0:
            sigma = 1.4826 * mad
            keep = _np.abs(samples - med) <= 3.0 * sigma
            if int(_np.count_nonzero(keep)) >= 4:
                samples = samples[keep]
        return float(_np.mean(samples))
    except Exception:
        return 0.0


def make_capture(index: int,
                 pattern: PulsePattern,
                 acq,
                 scope,
                 stim,
                 *,
                 cal: Optional[ReadbackCalibration] = None,
                 channel: int = 0,
                 electrode_dc_offsets: Optional[dict] = None,
                 apply_current_offset: bool = False) -> Capture:
    """Convert a raw :class:`~stimtest.hardware.base.ScopeAcquisition` into a
    :class:`~stimtest.session.Capture`, applying readback calibration when
    available.

    Parameters
    ----------
    index:
        Sequential capture index within the run.
    pattern:
        The :class:`~stimtest.waveforms.PulsePattern` that was active.
    acq:
        The scope acquisition returned by ``single_capture()`` /
        ``capture_while_running()``.
    scope:
        Live :class:`~stimtest.hardware.base.Oscilloscope` (used for
        ``channel_aliases``).
    stim:
        Live :class:`~stimtest.hardware.base.Stimulator` (used for
        ``info.vmon_scaling_v_per_v`` and ``info.imon_scaling_v_per_ua``).
    cal:
        Optional :class:`ReadbackCalibration`.  When ``None`` the function
        behaves identically to the pre-calibration code path (no correction).
    channel:
        1-based stimulator channel number — used to look up per-channel
        I_mon coefficients.  0 means "unknown / use global identity".
    """
    aliases = scope.channel_aliases
    raw_vmon = acq.channels.get(aliases.get("vmon", ""), np.zeros(0))
    raw_imon = acq.channels.get(aliases.get("imon", ""), np.zeros(0))
    e_ret    = acq.channels.get(aliases.get("eret", ""), None)
    e_act    = acq.channels.get(aliases.get("eact", ""), None)

    info = stim.info
    nom_vmon = info.vmon_scaling_v_per_v or 1.0
    nom_imon = info.imon_scaling_v_per_ua or 1.0

    raw_vmon_arr = np.asarray(raw_vmon, dtype=float)
    raw_imon_arr = np.asarray(raw_imon, dtype=float)

    if cal is not None:
        # Operator (CWRU testboard): REMOVE the calibration DC-offset
        # subtraction on captures.  The per-channel ``vmon_offset_v`` /
        # ``imon_offset_v`` (measured by the calibration wizard) were
        # SHIFTING every captured waveform — on the testboard the
        # calibration reported V_mon offsets of −24 … −107 mV and those
        # got subtracted from each capture, so the stored + displayed
        # trace rode that far off zero (the display plots ``cap.v_mon_v``
        # verbatim, gotcha #34, so the subtraction here lands straight on
        # screen).  The raw int8→double conversion already idles near
        # zero (the ``apply_channel_defaults`` preamble-cache fix), so no
        # offset correction is needed.  Only SCALING (V_mon actual factor,
        # I_mon magnification + the per-channel gain/offset CURRENT
        # calibration ``apply_imon``) is applied now.  The offset fields
        # stay in calibration.json for diagnostics but are no longer
        # applied to captures.
        # ⚠ VERIFICATION DOES NOT CALIBRATE THE SYSTEM (operator, 0.2.226:
        # "Do not calibrate the system based on the verification test — only
        # the scaling whether it is default or NIL").
        #
        # Captures are reconstructed with the STIMULATOR PRESET scaling ONLY
        # (``nom_vmon`` / ``nom_imon`` = Default 0.25 V/V + 2.5 mV/µA, or NIL
        # 1.0 V/V + 1.0 mV/µA).  NONE of the verification's FITTED quantities
        # are applied any more:
        #
        #   * ``vmon_v_per_v_actual`` / ``imon_v_per_ua_actual`` — fitted
        #     scalings, no longer substituted for the preset;
        #   * ``apply_imon`` (per-channel gain ``a`` + offset ``b``);
        #   * ``imon_offset_v`` (and the ``apply_current_offset`` flag, now a
        #     no-op — the parameter is kept so existing callers still import
        #     and run).
        #
        # WHY.  These fitted values are derived from the very captures the
        # verification is judging, so a bad sweep silently corrupts every
        # later measurement — and did: a degenerate ``np.polyfit`` slope of
        # ~1e14 multiplied real ±3 µA readings into ±1e14 µA (gotcha #161),
        # and a 1.2073 gain alone would rescale every current by 21 %.  The
        # sanity guards added for #161 stay as belt-and-braces, but the
        # structural fix is not to apply the coefficients at all.  What the
        # verification IS for: confirming the load and telling the operator
        # which PRESET (Default vs NIL) the stimulator is on — that choice is
        # recorded per serial and applied upstream, not here.
        #
        # The coefficients remain in calibration.json for forensics, and
        # ``apply_imon`` / ``is_plausible`` stay defined + tested but dormant
        # (same pattern as the removed current-offset toggle, gotcha #132).
        # **Don't re-wire any fitted coefficient back into this path.**
        v_mon_v  = raw_vmon_arr / nom_vmon
        i_mon_ua = raw_imon_arr / nom_imon
    else:
        v_mon_v  = raw_vmon_arr / nom_vmon
        i_mon_ua = raw_imon_arr / nom_imon

    # NOTE: per-capture baseline subtraction is deliberately NOT
    # done here.  ``cap.v_mon_v`` is consumed downstream by the
    # voltage-compliance check, by metrics that depend on absolute
    # potential (E_act / E_ret polarization, water-window crossings),
    # and by the on-disk session payload — subtracting the idle
    # offset at acquisition time would erase the slowly-drifting
    # polarization signal those readers need.  Instead, the
    # experiment plot subtracts the per-capture baseline at
    # *render* time (see ``_ChannelPage._refresh_traces`` in
    # multichannel_scope.py) so the displayed waveform starts at
    # zero without disturbing the stored data.

    # ---- Electrode DC offset add-back (E_ret / E_act) ------------------
    # Operator: "Let's try AC coupled after capturing the offset from DC
    # coupled."  A DC-biased electrode potential (E_ret ~+248 mV rest with
    # an ±8 mV pulse swing) can't be fine-scaled while DC-coupled — the
    # ±5-div POSition limit forces a coarse V/div (gotcha #13), so the
    # swing is nearly flat.  The runner instead measures the DC rest
    # potential ONCE (DC-coupled), AC-couples the channel so the swing
    # centres at 0 and the rescale loop fine-scales it, and passes the
    # measured offset here so the SAVED trace carries the absolute level
    # (offset) AT the fine swing resolution.  ``electrode_dc_offsets`` is
    # ``{role: volts}``; empty / None (the default, and any DC-coupled
    # run) leaves the raw reading untouched — fully back-compatible.
    _off = electrode_dc_offsets or {}

    def _with_offset(arr, role):
        if arr is None:
            return None
        a = np.asarray(arr)
        if not a.size:
            return None
        d = _off.get(role)
        if d is not None and np.isfinite(d) and d != 0.0:
            a = a + float(d)
        return a

    return Capture(
        index=index,
        pattern=pattern,
        time_us=np.asarray(acq.time_us),
        v_mon_v=v_mon_v,
        i_mon_ua=i_mon_ua,
        e_act_v=_with_offset(e_act, "eact"),
        e_ret_v=_with_offset(e_ret, "eret"),
    )
