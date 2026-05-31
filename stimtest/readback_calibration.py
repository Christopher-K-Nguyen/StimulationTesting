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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import numpy as np

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
            channels[ch] = ChannelCoeffs(a=a, b=b)
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
    """Mean of the pre-trigger (idle) samples of one captured trace.

    Returns the DC level the channel was sitting at *just before* the
    pulse fired, so the caller can subtract it from the full trace and
    plot a waveform that starts at zero regardless of where the scope
    happens to be DC-biased on this particular capture.

    Strategy mirrors :func:`stimtest.gui.calibration._per_capture_baseline`
    so experiment plots and the calibration plot share one definition of
    "idle baseline" — both routines must agree or the two views of
    the same waveform disagree:

      * **Primary**: time-axis mask of samples with ``t_us < -1 µs``.
        Requires ``t_us`` to be the same length as ``arr`` and to
        contain ≥ 8 pre-trigger samples.
      * **Fallback**: first 10 % of the trace (capped to 200 samples),
        floored at 8.  Used when the time axis is degenerate or has no
        pre-trigger window.
      * **MAD-clip**: drop samples > 3 σ (estimated via MAD × 1.4826)
        from the median, so a stray pulse-leak sample doesn't drag
        the mean.

    Returns ``0.0`` when ``arr`` is None / too short to estimate — caller
    can then blanket-subtract without guarding.
    """
    import numpy as _np
    try:
        if arr is None or len(arr) < 8:
            return 0.0
        a_arr = _np.asarray(arr, dtype=float)
        samples = None
        if t_us is not None and len(t_us) == len(arr):
            t_arr = _np.asarray(t_us, dtype=float)
            pre_mask = t_arr < -1.0
            if int(_np.count_nonzero(pre_mask)) >= 8:
                samples = a_arr[pre_mask]
        if samples is None:
            n_first = max(8, min(200, len(arr) // 10))
            samples = a_arr[:n_first]
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
                 channel: int = 0) -> Capture:
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
        # V_mon: subtract idle offset, then apply actual (or nominal) scaling.
        vmon_offset = cal.vmon_offset_v if cal.vmon_offset_v is not None else 0.0
        vmon_scale  = cal.vmon_v_per_v_actual if cal.vmon_v_per_v_actual is not None else nom_vmon
        v_mon_v = (raw_vmon_arr - vmon_offset) / vmon_scale

        # I_mon: subtract idle offset, apply actual magnification factor,
        # then apply any per-channel linear correction.
        imon_offset = cal.imon_offset_v if cal.imon_offset_v is not None else 0.0
        imon_scale  = cal.imon_v_per_ua_actual if cal.imon_v_per_ua_actual is not None else nom_imon
        i_mon_ua = (raw_imon_arr - imon_offset) / imon_scale
        i_mon_ua = cal.apply_imon(i_mon_ua, channel)
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

    return Capture(
        index=index,
        pattern=pattern,
        time_us=np.asarray(acq.time_us),
        v_mon_v=v_mon_v,
        i_mon_ua=i_mon_ua,
        e_act_v=np.asarray(e_act) if e_act is not None and np.asarray(e_act).size else None,
        e_ret_v=np.asarray(e_ret) if e_ret is not None and np.asarray(e_ret).size else None,
    )
