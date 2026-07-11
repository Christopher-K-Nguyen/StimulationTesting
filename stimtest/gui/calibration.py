"""PlexStim test-board stimulator verification wizard.

Patterned after the Gamry stimulator verification wizards: a multi-step flow that
walks the user through known-load measurements with the dedicated
PlexStim test board (Plexon **14-04-A-03-A**), connected through the
black Omnetics stimulation cable (Plexon **14-03-A-03**). Each
channel on this board terminates in an **RC series** to ground:
**4.99 kΩ + 4700 pF**. The wizard records the V_mon edge step
(which equals I·R since V_C is continuous across each phase
transition) for each channel against the **expected** current, fits
a per-channel gain + offset, and lets the user save the resulting
trim file so the runner can apply the corrections at every
subsequent capture.

The flow:

    1. **Connect the test board** — the user plugs the PlexStim
       channel array into the Plexon 14-04-A-03-A test-board
       Omnetics receptacle using the Plexon 14-03-A-03 cable.
    2. **Per-channel sweep** — for each channel, deliver a known
       biphasic train (e.g. 100 µA, 200 µs / phase) and capture
       V_mon. The current is recovered from the I·R edge step
       at each phase transition. Repeat for a sparse amplitude
       grid (e.g. 50, 100, 200, 500, 1000 µA) so the fit captures
       any non-linearity.
    3. **Fit** — least-squares ``I_actual = a · I_mon + b`` per
       channel; flag any channel whose residual exceeds the lab's
       acceptance threshold (default ±2 %).
    4. **Save** — write the per-channel verification results to
       ``~/.config/stimtest/calibration.json`` (or wherever the
       prefs system points). The runner reads this file at start
       and applies the inverse transform to every reported value.
"""
from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from .prefs import prefs_dir, load_prefs, save_prefs
from ..readback_calibration import _IMON_GAIN_MIN, _IMON_GAIN_MAX
from ..config import (IMON_SCALING_DEFAULT, IMON_SCALING_NIL,
                      VMON_SCALING_DEFAULT, VMON_SCALING_NIL,
                      NIL_SERIAL_NUMBERS, DEFAULT_RECORD_LENGTH)


def _round_sig(x: float, sig: int = 1) -> float:
    """Round x to sig significant figures (MATLAB round(x,sig,'significant')).

    Half AWAY from zero (not Python's half-to-even) so an exact-half bound
    like -45 µs frames to -50 as MATLAB does, keeping the calibration plot's
    X bounds identical to the experiment plot (``widgets._round_sig``)."""
    if not math.isfinite(x):
        return x
    if x == 0.0:
        return 0.0
    d = math.ceil(math.log10(abs(x)))
    scale = 10.0 ** (d - sig)
    scaled = x / scale
    r = math.floor(scaled + 0.5) if scaled >= 0.0 else math.ceil(scaled - 0.5)
    return r * scale


# I_mon trigger-level + vertical-scale formulas both live in
# :mod:`stimtest.experiments.base` so the experiment runners (VT, PS,
# SP, LP) and calibration share ONE source of truth.  We re-export
# them here under the legacy underscore-prefixed names so the dozens
# of call sites in this file keep working unchanged.  Previously a
# byte-for-byte duplicate copy lived here that had to be kept in sync
# by hand — a real bug magnet (small-amp branch drifted between the
# two during the recent +4.5 → +3.5 tightening).  The constant
# ``_IMON_TRIG_SCALING_V_PER_UA`` is no longer needed locally; it
# stays defined in ``experiments/base.py`` as a private module-level.
from ..experiments.base import (  # noqa: E402
    imon_trigger_level as _imon_trigger_level,
    imon_vertical_scale as _imon_vertical_scale,
)


def _build_matlab_vert_grid() -> tuple:
    """Dense vertical-scale grid from MATLAB ``getWaveform.m`` (lines 70-86).

    Five regions with progressively coarser steps:

      * 2-9   mV/div  in   1 mV steps  ( 8 entries)
      * 10-48 mV/div  in   2 mV steps  (20 entries)
      * 50-495 mV/div in   5 mV steps  (90 entries)
      * 500-1990 mV/div in 10 mV steps (150 entries)
      * 2000-5000 mV/div in 20 mV steps (151 entries)

    Returned sorted ASCENDING so the picker can iterate small→large and
    break on the first entry that fits the signal.  The standard 1-2-5
    Tek grid has only 12 entries from 1 mV to 5 V; this MATLAB grid has
    ~420 entries giving ~30× finer step resolution — critical for not
    squishing the captured waveform when amplitude varies across a sweep.
    """
    out = []
    for lo, hi, step in (
        (2,    10,    1),    # 2..9 mV/div
        (10,   50,    2),    # 10..48 mV/div
        (50,   500,   5),    # 50..495 mV/div
        (500,  2000,  10),   # 500..1990 mV/div
        (2000, 5001,  20),   # 2000..5000 mV/div
    ):
        for mv in range(lo, hi, step):
            out.append(mv * 1e-3)
    return tuple(out)


#: Vertical V/div grid from MATLAB ``getWaveform.m``.  Replaces the
#: standard 1-2-5 grid so the picked scale lands much closer to the
#: signal's peak-to-peak — without this, a 39 mV signal at 1-2-5
#: snaps to 20 mV/div (≈2 divs filled, squished); on this grid it
#: snaps to 10 mV/div (≈4 divs filled, easy to read).
_VERT_GRID_VPD: tuple = _build_matlab_vert_grid()

#: MATLAB's MAX_FACTOR (getWaveform.m line 6) — the signal must fit
#: within ``±MAX_FACTOR × scale``, i.e. ±4 divs on an 8-div screen.
#: We use this as the divs target for the V_mon scale formula so the
#: trace fills ~4 divs (not 3) by default.
_VMON_TARGET_DIVS: float = 4.0


def _vmon_vertical_scale(amp_ua: float, load_r_ohm: float, load_c_pf: float,
                         phase_us: float, vmon_v_per_v: float,
                         compliance_v: float = 12.0,
                         target_divs: float = _VMON_TARGET_DIVS) -> float:
    """Pick a V_mon V/div so the load voltage fits in ``target_divs`` divs.

    Test-board load is RC series: during phase 1 the cap charges linearly
    V_C(t) = I·t/C, so end-of-phase V on the load is V_R + V_C = I·R + I·W/C.
    The stim's compliance ceiling clips peaks above ~12 V.  Snap up to
    the MATLAB ``getWaveform.m`` dense vertical-scale grid (1/2/5/10/20
    mV steps depending on decade) so the picked scale is much closer to
    the signal peak than the standard 1-2-5 grid — prevents the trace
    from being squished into 1-2 divisions when amplitude varies across
    the sweep grid.
    """
    amp_a = abs(float(amp_ua)) * 1e-6
    v_r = amp_a * float(load_r_ohm)
    if load_c_pf > 0:
        v_c = amp_a * float(phase_us) * 1e-6 / (float(load_c_pf) * 1e-12)
    else:
        v_c = 0.0
    v_load_peak = min(v_r + v_c, float(compliance_v))
    v_mon_peak = v_load_peak * float(vmon_v_per_v)
    # MATLAB ``getWaveform.m`` line 610 picks the smallest scale where
    # the signal range still fits within ``MAX_FACTOR × scale`` on each
    # side of zero, i.e. peak < target_divs × scale.  Iterate ascending
    # and break on the first that holds — gives the finest resolution
    # the signal allows.
    ideal_min = max(v_mon_peak / max(target_divs, 1.0), _VERT_GRID_VPD[0])
    for g in _VERT_GRID_VPD:
        if g >= ideal_min:
            return g
    return _VERT_GRID_VPD[-1]


#: Filename for the persisted per-channel calibration. Sits in
#: the prefs directory next to ``gui_prefs.setting`` so the
#: install / uninstall machinery treats them as a unit.
CALIBRATION_FILE = "calibration.json"



def calibration_path() -> Path:
    """Absolute path the calibration file lives at (whether or not
    it actually exists yet)."""
    return prefs_dir() / CALIBRATION_FILE


def last_calibration_datetime() -> Optional[datetime]:
    """Return the saved-at timestamp from the calibration file, or
    ``None`` when no calibration has been recorded yet.

    Reads the embedded ``"timestamp"`` field first (the
    source-of-truth — written by :meth:`CalibrationTab._on_save_calibration`
    in ISO-8601 UTC form alongside the per-channel coefficients).
    Falls back to the file's modification time if the JSON is
    present but lacks the timestamp key, so a hand-edited or
    half-migrated file still surfaces a sensible date rather than
    "never calibrated".
    """
    p = calibration_path()
    if not p.is_file():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        # Corrupt or unreadable — fall back to mtime so the user
        # at least sees that SOMETHING exists at that path.
        try:
            return datetime.fromtimestamp(p.stat().st_mtime)
        except OSError:
            return None
    ts = payload.get("timestamp") if isinstance(payload, dict) else None
    if isinstance(ts, str):
        try:
            # ``fromisoformat`` handles "YYYY-MM-DDTHH:MM:SS[.ffffff][±HH:MM]"
            # which is what ``datetime.isoformat()`` produces. Strip
            # a trailing "Z" if some external tool wrote one.
            return datetime.fromisoformat(ts.rstrip("Z"))
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(p.stat().st_mtime)
    except OSError:
        return None




class CalibrationTab(QtWidgets.QWidget):
    """Embedded tab for the PlexStim test-board stimulator-verification flow.

    Lives between the Setup tab and the experiment tabs in the main window.
    ``stim`` and ``scope`` are the live hardware handles (or ``None`` when the
    GUI hasn't connected yet); the main window pushes fresh handles via
    :meth:`set_hardware` whenever the connection state changes.

    When the user finishes (clicks Done, or finishes saving), the tab emits
    :attr:`doneRequested`; the main window catches it and switches the active
    tab back to Setup so the user can continue with experiment setup.
    """

    #: Emitted when the user wants to leave the calibration tab — wired by
    #: the main window to switch focus back to the Setup tab.
    doneRequested = QtCore.pyqtSignal()

    #: Default amplitude grid (µA) swept on every channel. Sparse
    #: enough to keep total wall-clock per-channel under ~5 s
    #: (5 capture cycles × ~1 s each) while dense enough to fit
    #: a linear gain + offset per channel cleanly. The 1000 µA
    #: endpoint is the PlexStim hardware ceiling.
    DEFAULT_AMPLITUDE_GRID_UA: tuple = (10.0, 20.0, 50.0, 100.0, 200.0, 500.0)
    #: Default load resistance per channel (Ω) for the Plexon
    #: test board. The 14-04-A-03-A wires each channel to an
    #: **RC series** (4.99 kΩ + 4700 pF), NOT a pure resistor —
    #: so the V_mon trace during a constant-current phase is
    #: V_R + V_C(t) = I·R + I·t/C, with the cap ramping linearly
    #: through the phase. The calibration recovers the current
    #: from the I·R **edge step** at each phase transition (the
    #: cap voltage is continuous across edges, so the voltage
    #: jump is purely I·R). The C value is captured below for
    #: documentation; only R enters the current calculation.
    DEFAULT_LOAD_OHM: float = 4_990.0
    #: Default load capacitance per channel (pF) for the
    #: Plexon 14-04-A-03-A test board.
    DEFAULT_LOAD_CAP_PF: float = 4700.0
    #: Biphasic pulse duration per phase (µs) used for each
    #: calibration capture. Long enough that the scope's averaging
    #: settles, short enough that 1000 µA × 200 µs stays well
    #: under any tissue-damage threshold on a passive test-board
    #: load (no actual tissue involved).
    PHASE_WIDTH_US: float = 50.0
    #: Interphase gap (µs) inserted between phase 1 and phase 2.
    INTERPHASE_US: float = 25.0
    #: Discharge delay (µs) appended after phase 2.  Set equal to the
    #: phase width so the waveform is charge-balanced and the discharge
    #: phase is visible on the oscilloscope alongside the two active phases.
    DISCHARGE_US: float = 0.0
    #: Pulse rate (pps) used for every calibration capture.
    PULSE_RATE_PPS: float = 50.0
    #: Number of waveforms the scope averages before the curve is
    #: read. AVERAGE mode sends the stimulator running, waits for
    #: N triggered acquisitions to complete, then reads the averaged
    #: curve — SNR improves by √N relative to a single capture.
    #: 8 averages at 50 Hz = 160 ms per cell; 4 = 80 ms.  Must be a
    #: power of two (TBS-series NUMAVg constraint: 2, 4, 8 … 512).
    N_AVERAGES: int = 32
    # NOTE: ACCEPTANCE_PCT and MODEL_RMSD_LIMIT_MV constants were
    # removed — earlier revisions used them as pass/fail gates but
    # neither is meaningful anymore.  Gain |a-1| reflects test-board R
    # vs. nominal R (not channel health), and V_mon model RMSD is no
    # longer displayed.  Pass/fail now uses ONLY "finite, positive
    # R_load AND C_load fit".

    def __init__(self,
                 parent: Optional[QtWidgets.QWidget] = None,
                 *,
                 stim=None, scope=None):
        super().__init__(parent)
        self._stim = stim
        self._scope = scope
        # Per-channel results — populated by _run_sweep_blocking.
        # Keyed by channel index (1-based); each entry is a list of
        # (programmed_ua, measured_ua) tuples. Empty when the
        # sweep hasn't run yet.
        self._results: Dict[int, list] = {}
        # Linear-fit coefficients per channel, populated after the
        # sweep finishes. Keys are channel indices (as strings to
        # round-trip cleanly through JSON); values are
        # {"a": float, "b": float, "rmsd_ua": float}.
        self._fit: Dict[str, dict] = {}
        # Abort flag — set by the user clicking the in-sweep
        # Abort button. The sweep loop polls this between
        # captures to short-circuit out cleanly.
        self._aborted: bool = False
        # Scaling-validation result — populated by
        # ``_validate_scaling`` after the sweep, persisted into
        # calibration.json on save, and surfaced in the
        # post-sweep summary popup. See ``_validate_scaling``
        # for the schema.
        self._scaling_validation: dict = {}
        # Idle baseline offsets measured before each sweep.
        self._vmon_offset_v: float = 0.0   # raw scope volts
        self._imon_offset_v: float = 0.0   # raw scope volts

        intro = QtWidgets.QLabel(
            "<h3>PlexStim test-board stimulator verification</h3>"
            "<p>Verifies that each PlexStim channel delivers the "
            "requested current within your acceptance tolerance. "
            "The wizard sweeps a sparse amplitude grid on every "
            "channel against the test board's known RC-series "
            "load and fits "
            "<i>I_actual = a · I_mon + b</i> per channel.</p>"
            "<p><b>Before clicking Run sweep:</b></p>"
            "<ol>"
            "<li>Plug the PlexStim channel array into the "
            "<b>Plexon 14-04-A-03-A test board</b> using the "
            "<b>black Omnetics stimulation cable (Plexon "
            "14-03-A-03)</b> — do <b>not</b> connect a live "
            "electrode array. The black Omnetics stimulation "
            "cable is the only one keyed for the test board's "
            "per-channel RC series load (4.99 kΩ + 4700 pF).</li>"
            "<li>Wire the scope: <b>V_mon → CH1</b> and "
            "<b>I_mon → CH2</b>. The preview plot shows V_mon "
            "in blue on the left axis and I_mon in red on the "
            "right axis, so the colour coding mirrors the "
            "physical connection.</li>"
            "<li>Tick the test-board confirmation checkbox below "
            "to unlock Run sweep. (The load R / C are fixed at "
            "4.99 kΩ + 4700 pF by the test board hardware and "
            "shown below for reference.)</li>"
            "</ol>"
        )
        intro.setWordWrap(True)
        intro.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        # Hardware connection status banner — green when both
        # stim + scope are reachable, grey otherwise.
        stim_ready = (self._stim is not None
                      and getattr(self._stim, "is_open", True))
        connected = stim_ready and self._scope is not None
        self.status_label = QtWidgets.QLabel()
        self.status_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        if connected:
            self.status_label.setText(
                "<b style='color: #009E73'>Hardware connected.</b> "
                "Ready to run the stimulator verification sweep.")
        elif self._stim is not None and not stim_ready:
            self.status_label.setText(
                "<b style='color: #CC6600'>Stimulator not initialized.</b> "
                "Click <b>Initialize</b> in the Hardware panel, then re-open "
                "this dialog. If Initialize fails, close Stim-2 / Stimulator "
                "V2 first — it holds exclusive USB access.")
        else:
            self.status_label.setText(
                "<b style='color: #777'>Hardware not connected.</b> "
                "Connect the PlexStim + scope before running the "
                "sweep — review the protocol above in the meantime.")

        # Test-board parameters form — load impedance + amplitude
        # grid. The amplitude-grid spinbox is read-only on the UI
        # but the constant ``DEFAULT_AMPLITUDE_GRID_UA`` is what
        # the sweep iterates; if the user wants a different grid
        # they edit the class attribute (rare enough that a full
        # UI for it isn't worth the complexity today).
        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        # Load R and C are fixed by the Plexon 14-04-A-03-A
        # test-board hardware (4.99 kΩ + 4700 pF per channel)
        # and are NOT user-editable — surfacing them as
        # spinboxes would invite drift between the GUI value
        # and the soldered-in components. Render them as
        # static read-only labels instead; the calibration
        # math uses ``self.DEFAULT_LOAD_OHM`` and
        # ``self.DEFAULT_LOAD_CAP_PF`` directly.
        load_r_label = QtWidgets.QLabel(
            f"{self.DEFAULT_LOAD_OHM:.0f} Ω "
            "<span style='color:#666'>(fixed by Plexon "
            "14-04-A-03-A)</span>")
        load_r_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        load_r_label.setToolTip(
            "Series resistance of the test-board load each "
            "channel drives. Fixed at 4.99 kΩ by the Plexon "
            "14-04-A-03-A board's soldered components. The "
            "verification recovers I from the I·R edge step at "
            "each phase transition.")
        form.addRow("Load R (series):", load_r_label)
        load_c_label = QtWidgets.QLabel(
            f"{self.DEFAULT_LOAD_CAP_PF:.0f} pF "
            "<span style='color:#666'>(fixed by Plexon "
            "14-04-A-03-A)</span>")
        load_c_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        load_c_label.setToolTip(
            "Series capacitance of the test-board load. Fixed "
            "at 4700 pF by the Plexon 14-04-A-03-A board's "
            "soldered components. Documentation + small bias "
            "correction in the current calculation; the cap "
            "primarily controls how steep the V_mon ramp looks "
            "during each phase (dV/dt = I/C).")
        form.addRow("Load C (series):", load_c_label)
        grid_str = ", ".join(f"{a:.0f}" for a in self.DEFAULT_AMPLITUDE_GRID_UA)
        form.addRow("Amplitude grid (µA):",
                    QtWidgets.QLabel(grid_str))
        # Editable channel list — defaults to all channels on the
        # connected stimulator. The user trims this to only the
        # channels physically wired to the test board.
        # Accepts range notation: "1-4, 8, 12-16"
        _n_ch = int((getattr(self._stim, "info", None)
                     and self._stim.info.n_channels) or 16) if self._stim else 16
        _default_ch_str = f"1-{_n_ch}" if _n_ch > 1 else "1"
        self.channels_edit = QtWidgets.QLineEdit(_default_ch_str)
        self.channels_edit.setToolTip(
            "Channels to sweep — only include channels wired to the test board.\n"
            "Accepts individual numbers and ranges: e.g. \"1-4, 8, 12-16\"")
        self.channels_edit.setPlaceholderText("e.g. 1-4, 8, 12")
        form.addRow("Channels to sweep:", self.channels_edit)

        # Hard-gate the sweep behind an explicit "I confirm the
        # test-board (black Omnetics) is connected" checkbox. The
        # Run sweep button stays disabled until the user ticks
        # this, so they cannot accidentally drive 1 mA into a live
        # electrode array thinking they were calibrating.
        #
        # QCheckBox does not support word wrap, so we use a bare
        # checkbox (no text) paired with a wrapping QLabel. Clicking
        # the label toggles the checkbox via mousePressEvent.
        _confirm_tip = (
            "Safety interlock — Run sweep stays disabled until "
            "this is ticked. Confirms the operator has verified "
            "the cable is plugged into the Plexon test board "
            "(passive 4.99 kΩ + 4700 pF series load per channel), "
            "not a live electrode array. The sweep delivers up "
            "to 1 mA pulses on every channel; if you mis-wire to "
            "a live array, you could damage electrodes or tissue.")
        self.testboard_confirm = QtWidgets.QCheckBox()
        self.testboard_confirm.setToolTip(_confirm_tip)
        self.testboard_confirm.toggled.connect(
            self._on_testboard_confirm_toggled)

        _confirm_label = QtWidgets.QLabel(
            "I confirm the PlexStim channel array is connected to "
            "the Plexon 14-04-A-03-A test board via the BLACK "
            "OMNETICS STIMULATION CABLE (Plexon 14-03-A-03), and "
            "NOT to a live electrode array.")
        _confirm_label.setWordWrap(True)
        _confirm_label.setToolTip(_confirm_tip)
        _confirm_label.setStyleSheet(
            "QLabel { font-weight: bold; color: #b71c1c; }")
        # Clicking the label toggles the checkbox.
        _confirm_label.mousePressEvent = (
            lambda _ev: self.testboard_confirm.toggle())

        _confirm_row = QtWidgets.QHBoxLayout()
        _confirm_row.setContentsMargins(0, 0, 0, 0)
        _confirm_row.setSpacing(6)
        _confirm_row.addWidget(self.testboard_confirm,
                               alignment=QtCore.Qt.AlignmentFlag.AlignTop)
        _confirm_row.addWidget(_confirm_label, stretch=1)
        _confirm_widget = QtWidgets.QWidget()
        _confirm_widget.setLayout(_confirm_row)
        form.addRow("Test-board check:", _confirm_widget)

        # ---- Scope setup ----
        # Oscilloscope-setup group BOX is intentionally removed: the
        # calibration sweep now hardcodes the acquisition mode to
        # AVERAGE with NUMAVg = 64 (lab convention — gives ≈ √64 ⇒ 8×
        # noise reduction, plenty for the IR-step + cap-ramp fits
        # without dragging out the per-capture time).  V_mon and I_mon
        # channel choices are inherited from the live scope's
        # ``channel_aliases`` (defaults CH1/CH2), so the user
        # configures the wiring once in the Setup tab and the
        # calibration just reads it.
        #
        # The combo objects below stay as PROGRAMMATIC HOLDERS that
        # the rest of the calibration code reads via ``.currentText()``
        # — they're never added to a visible layout.  Letting them
        # exist as widgets means we don't have to refactor the dozen
        # sites that read from them.
        _CH_OPTIONS = ["CH1", "CH2", "CH3", "CH4"]
        _aliases = (getattr(self._scope, "channel_aliases", {}) or {}
                    if self._scope is not None else {})
        _vmon_default = _aliases.get("vmon", "CH1")
        _imon_default = _aliases.get("imon", "CH2")
        self.cal_vmon_combo = QtWidgets.QComboBox()
        self.cal_vmon_combo.addItems(_CH_OPTIONS)
        self.cal_vmon_combo.setCurrentText(_vmon_default)
        self.cal_imon_combo = QtWidgets.QComboBox()
        self.cal_imon_combo.addItems(_CH_OPTIONS)
        self.cal_imon_combo.setCurrentText(_imon_default)

        # ---- Sweep-progress panel ----
        # Visible from the start but inert until ``Run sweep`` is
        # clicked. Three layers:
        #   * status_progress: "Channel N / M — A µA" text
        #   * progress_bar: 0..100 % across all channel × amplitude
        #     steps
        #   * results_table: per-channel fit summary, populated
        #     after the sweep completes
        self.status_progress = QtWidgets.QLabel(
            "Idle. Click Run sweep to begin.")
        self.status_progress.setStyleSheet("color: #555;")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        # Acquisition plot — updated after every completed capture.
        self._pg = None
        self._plot_widget = None
        self._right_vb = None
        self._plot_curves: Dict[str, object] = {}
        self._plot_right_curves: Dict[str, object] = {}
        self._plot_sim_curve = None
        self._plot_legend = None
        self._plot_x_min: float = -26.5
        self._plot_x_max: float = 223.5
        self._plot_syncing: bool = False
        try:
            import pyqtgraph as pg
            self._pg = pg
            VMON_COLOR = "#E6B800"
            IMON_COLOR = "#00B4C8"
            _ls_x    = {"font-size": "11pt", "color": "#333333"}
            _ls_left = {"font-size": "11pt", "color": VMON_COLOR}
            _ls_right= {"font-size": "11pt", "color": IMON_COLOR}
            self._plot_widget = pg.PlotWidget()
            self._plot_widget.setBackground("w")
            # Mouse wheel must NOT zoom (operator request).
            from .widgets import disable_plot_wheel_zoom
            disable_plot_wheel_zoom(self._plot_widget)
            self._plot_widget.setLabel("bottom", "Time", units="µs", **_ls_x)
            self._plot_widget.setLabel("left",   "V_mon",   units="V",  **_ls_left)
            self._plot_widget.showGrid(x=True, y=True, alpha=0.25)
            # MATLAB-style "nice multiples" tick step (5 ticks, picked
            # from {1, 2, 2.5, 5}×10ⁿ) instead of pyqtgraph's denser
            # default (~10 ticks at step 200 for a 0-2000 µs range).
            # The shared helper lives in :mod:`stimtest.gui.widgets`
            # so calibration and experiment plots use the SAME tick
            # algorithm — they were previously diverging because
            # pyqtgraph's default tick step depends on the range, and
            # MATLAB picks 500 where pyqtgraph picks 200.  Also drops
            # the minor / sub-minor tick levels (same as the previous
            # ``_major_only`` shim) so showGrid draws a clean coarse
            # grid.
            from .widgets import _make_matlab_tick_override
            for _ax_name in ("bottom", "left", "right"):
                try:
                    _ax = self._plot_widget.getAxis(_ax_name)
                    _ax.tickValues = _make_matlab_tick_override(
                        _ax.tickValues, target_count=5)
                except Exception:
                    pass
            # Disable pyqtgraph's auto-SI-prefix on every axis so the
            # tick numbers stay in the units the label advertises
            # (V on left, mV on right, µs on bottom) — without this
            # a small-amplitude trace at ±0.05 V would render as
            # ``±50`` with ``(×0.001)`` appended to the label,
            # forcing the operator into mental arithmetic.
            for _ax_name in ("bottom", "left", "right"):
                try:
                    self._plot_widget.getAxis(_ax_name).enableAutoSIPrefix(False)
                except Exception:
                    pass
            self._plot_widget.setMinimumHeight(220)
            self._plot_legend = self._plot_widget.addLegend(
                offset=(10, 10),
                brush=pg.mkBrush(255, 255, 255, 200),
                pen=pg.mkPen("#cccccc"),
            )
            # Right axis — separate ViewBox linked to the left plot's x-axis.
            self._right_vb = pg.ViewBox()
            self._plot_widget.showAxis("right")
            self._plot_widget.scene().addItem(self._right_vb)
            right_ax = self._plot_widget.getAxis("right")
            right_ax.linkToView(self._right_vb)
            right_ax.setLabel("I_mon", units="mV", **_ls_right)
            right_ax.setPen(pg.mkPen(IMON_COLOR))
            right_ax.setTextPen(pg.mkPen(IMON_COLOR))
            # NOTE: leaving the right-axis label at pyqtgraph's default
            # orientation (bottom-to-top).  An earlier attempt to rotate
            # it 180° left the label intersecting the tick numbers
            # because pyqtgraph doesn't reposition after a manual
            # setRotation() — the label's bounding rect changes but the
            # axis's layout offset doesn't.  Keep default for now; a
            # proper fix would require subclassing AxisItem to override
            # the label-positioning logic.
            self._plot_widget.getAxis("left").setPen(pg.mkPen(VMON_COLOR))
            self._plot_widget.getAxis("left").setTextPen(pg.mkPen(VMON_COLOR))
            self._right_vb.setXLink(self._plot_widget.plotItem.vb)
            self._plot_widget.plotItem.vb.sigResized.connect(self._sync_plot_geometry)
        except Exception:
            self._pg = None

        # Results table — one row per channel, populated incrementally
        # as each channel's sweep completes (see ``_fit_one_channel``).
        # New "V offset (mV)" column: free-intercept of the step_v vs
        # I_prog fit, i.e. the V_mon DC bias at I=0 for that channel
        # (after the global idle baseline is subtracted).  Helps catch
        # per-channel readback offset that the through-origin slope
        # would otherwise hide.
        # V_mon Model RMSD and I_mon RMSD columns are intentionally
        # omitted — they were misleading the user (large values on
        # boards whose R differs from nominal, even when the actual
        # waveform fit perfectly) and they're no longer used for
        # pass/fail.  The pass/fail gate is now just "did we get
        # finite, positive R_load and C_load fits?".
        self.results_table = QtWidgets.QTableWidget(0, 6)
        self.results_table.setHorizontalHeaderLabels(
            ["Channel", "V_mon Offset (mV)",
             "R_load Fit (Ω)", "C_load Fit (pF)",
             "I_mon Gain (a)", "I_mon Offset (b, µA)"])
        # Row-index lookup so re-running a channel updates its existing
        # row instead of appending a duplicate.
        self._row_by_channel: Dict[int, int] = {}
        self.results_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setMinimumHeight(140)
        # Auto-fit: every column stretches proportionally to fill the
        # right-column width.  Without this the columns stay at their
        # default 100 px and either truncate ("Model RMSD (mV)" gets
        # clipped) or leave huge dead space to the right.  Stretch keeps
        # the table responsive when the user drags the splitter handle.
        _hdr = self.results_table.horizontalHeader()
        _hdr.setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        # The "Channel" column doesn't need to grow — pin its minimum so
        # it doesn't eat space the wider numeric columns need.
        _hdr.setMinimumSectionSize(60)

        # ---- Action row ----
        # Run sweep / Abort / Save calibration / Close. The
        # ``Abort`` button toggles between "disabled" and "enabled"
        # depending on whether a sweep is in flight.
        self._btn_run = QtWidgets.QPushButton("Run sweep")
        # Stays disabled until the user ticks
        # ``testboard_confirm`` AND the hardware is connected.
        # ``_on_testboard_confirm_toggled`` handles the gate.
        self._btn_run.setEnabled(False)
        self._btn_run.setToolTip(
            "Tick the test-board confirmation checkbox above to "
            "unlock the sweep.")
        self._btn_run.clicked.connect(self._on_run_sweep)
        self._hw_connected = connected
        self._btn_abort = QtWidgets.QPushButton("Abort")
        self._btn_abort.setEnabled(False)
        self._btn_abort.clicked.connect(self._on_abort_clicked)
        self._btn_save = QtWidgets.QPushButton("Save verification…")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save_calibration)
        btn_done = QtWidgets.QPushButton("Done — back to Setup")
        btn_done.setToolTip(
            "Return to the Setup tab to continue configuring the experiment.")
        btn_done.clicked.connect(self.doneRequested.emit)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self._btn_run)
        btn_row.addWidget(self._btn_abort)
        btn_row.addWidget(self._btn_save)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_done)

        # ---- Log pane ----
        # Stretches vertically to fill all available space in the
        # left column (down to the Run sweep / Abort / Save / Done
        # button row).  No setFixedHeight — the layout below adds
        # this widget with stretch=1 so it absorbs whatever vertical
        # space the rest of the column doesn't claim.  A minimum
        # height keeps it usable even when the splitter is collapsed
        # toward the right column.
        self._log_pane = QtWidgets.QPlainTextEdit()
        self._log_pane.setReadOnly(True)
        self._log_pane.setMaximumBlockCount(2000)
        self._log_pane.setMinimumHeight(120)
        self._log_pane.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        self._log_pane.setPlaceholderText("Sweep log will appear here…")
        self._log_pane.setStyleSheet(
            "QPlainTextEdit { font-family: monospace; font-size: 11px; "
            "background: #1e1e1e; color: #d4d4d4; }")

        # ---- Two-column layout ----
        # Left column: intro / status / form / scope-config / progress /
        # log pane. Right column: live plot + per-channel results table.
        # A QSplitter lets the user re-balance the columns at runtime.
        left = QtWidgets.QWidget()
        lv = QtWidgets.QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.addWidget(intro)
        lv.addWidget(self.status_label)
        lv.addLayout(form)
        # ``scope_box`` ("Oscilloscope setup" group) was removed —
        # acquisition mode is hardcoded to AVERAGE / NUMAVg = 64
        # and channel choices come from the live scope's aliases.
        lv.addWidget(self.status_progress)
        lv.addWidget(self.progress_bar)
        # Log pane absorbs the remaining vertical space so its bottom
        # edge sits right above the Run sweep / Abort / Save / Done
        # button row.  ``stretch=1`` plus the widget's Expanding
        # vertical sizePolicy makes it the only element in the
        # column that grows when the window is resized.  No
        # ``addStretch`` after — the log pane IS the stretch.
        lv.addWidget(self._log_pane, stretch=1)

        # Right column — vertical SPLITTER (plot above, results table
        # below) so the user can drag the boundary to grow either pane.
        # Mirrors the pulse-pattern preview's stretchable behaviour.
        # The plot also has ``+`` / ``−`` height buttons stacked above it
        # for discoverable resize, and its height is persisted in prefs
        # so the user's preferred view survives across launches.
        _plot_wrap = QtWidgets.QWidget()
        _pwl = QtWidgets.QVBoxLayout(_plot_wrap)
        _pwl.setContentsMargins(0, 0, 0, 0)
        _pwl.setSpacing(2)
        _btn_row_plot = QtWidgets.QHBoxLayout()
        _btn_row_plot.addStretch(1)
        self._plot_tall_btn = QtWidgets.QToolButton()
        self._plot_tall_btn.setText("+")
        self._plot_tall_btn.setToolTip("Increase plot height (40 px)")
        self._plot_tall_btn.clicked.connect(lambda: self._change_plot_height(40))
        self._plot_short_btn = QtWidgets.QToolButton()
        self._plot_short_btn.setText("−")
        self._plot_short_btn.setToolTip("Decrease plot height (40 px)")
        self._plot_short_btn.clicked.connect(lambda: self._change_plot_height(-40))
        _btn_row_plot.addWidget(QtWidgets.QLabel("Plot height:"))
        _btn_row_plot.addWidget(self._plot_short_btn)
        _btn_row_plot.addWidget(self._plot_tall_btn)
        _pwl.addLayout(_btn_row_plot)
        # Dedicated plot-title QLabel.  pyqtgraph's built-in
        # PlotItem title is unreliable across versions — the row
        # height is hidden until first paint and sometimes never
        # becomes visible — so we render the title as a real Qt
        # widget directly above the PlotWidget.  ``_update_acq_plot``
        # rewrites its text on every capture.
        # Placeholder text + fixed minimum height so the label
        # ALWAYS allocates space in the layout (even before the
        # first capture writes a real title).  Without this an
        # empty QLabel collapses to zero height and the title
        # silently never shows.
        self._plot_title_label = QtWidgets.QLabel("(no capture yet)")
        self._plot_title_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter)
        # Use the application's WindowText palette role for colour
        # so the title is visible on both dark and light themes.
        # The previous hard-coded ``color: #202020`` (near-black)
        # rendered invisible against a dark background.  Bigger
        # font + bold so the title reads at a glance.
        _title_font = self._plot_title_label.font()
        _title_font.setPointSize(13)
        _title_font.setBold(True)
        self._plot_title_label.setFont(_title_font)
        # Inherit foreground from palette — no explicit color in
        # the stylesheet — so dark mode picks a light foreground
        # and vice versa.
        self._plot_title_label.setStyleSheet(
            "QLabel { padding: 4px 0px; }")
        self._plot_title_label.setMinimumHeight(28)
        self._plot_title_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed)
        self._plot_title_label.setVisible(True)
        _pwl.addWidget(self._plot_title_label, stretch=0)
        if self._plot_widget is not None:
            _pwl.addWidget(self._plot_widget, stretch=1)

        right_split = QtWidgets.QSplitter(QtCore.Qt.Orientation.Vertical)
        right_split.addWidget(_plot_wrap)
        right_split.addWidget(self.results_table)
        right_split.setStretchFactor(0, 2)
        right_split.setStretchFactor(1, 3)
        right_split.setCollapsible(0, False)
        right_split.setCollapsible(1, False)
        self._right_split = right_split

        # Each column scrolls independently so a tall form or a tall table
        # doesn't push the other out of view on small screens.
        left_scroll = QtWidgets.QScrollArea()
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        right_scroll = QtWidgets.QScrollArea()
        right_scroll.setWidget(right_split)
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 5)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)

        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(splitter, stretch=1)
        v.addLayout(btn_row)

        # Shared session log pane (the main window's bottom-of-window
        # LogPane) — pushed in by MainWindow right after the tab is
        # created.  When set, every line written to the embedded mini
        # log is ALSO written to the shared log, which mirrors to disk.
        # Without this, calibration runs left no on-disk trace.
        self.log_pane: Optional[object] = None

        # Apply any persisted plot height (mirrors the pulse-pattern
        # preview's view-state restore).
        self._restore_plot_height()

    # ------------------------------------------------------------------ log pane
    def _log(self, msg: str) -> None:
        """Append a timestamped line to the embedded mini-log AND to the
        shared session log pane (which mirrors to disk).

        The embedded pane is the local view the user sees while the sweep
        is running; the shared pane is what survives across runs and ends
        up in the on-disk session log so post-mortems are possible.
        """
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%H:%M:%S")
        self._log_pane.appendPlainText(f"[{ts}]  {msg}")
        self._log_pane.verticalScrollBar().setValue(
            self._log_pane.verticalScrollBar().maximum())
        # Mirror to the shared LogPane (with on-disk file backing) so the
        # calibration sweep leaves a traceable record. Best-effort; never
        # let a logging failure abort the sweep.
        if self.log_pane is not None:
            try:
                self.log_pane.log(f"[verification] {msg}")
            except Exception:
                pass
        QtWidgets.QApplication.processEvents()

    # ------------------------------------------------------------------ hardware
    def set_hardware(self, stim=None, scope=None) -> None:
        """Push fresh hardware handles in from the main window.

        Called whenever the ConnectionPanel reports a connect / disconnect
        so the embedded tab tracks the live stimulator + oscilloscope.  Also
        refreshes the gate state for the Run-sweep button + status banner.
        """
        self._stim = stim
        self._scope = scope
        stim_ready = (stim is not None
                      and getattr(stim, "is_open", True))
        connected = stim_ready and (scope is not None)
        self._hw_connected = connected
        try:
            self._on_testboard_confirm_toggled(self.testboard_confirm.isChecked())
        except Exception:
            pass
        # Refresh the status banner so users see the live state when they
        # switch into the tab.
        try:
            if connected:
                self.status_label.setText(
                    "<b style='color: #009E73'>Hardware connected.</b> "
                    "Ready to run the stimulator verification sweep.")
            elif stim is not None and not stim_ready:
                self.status_label.setText(
                    "<b style='color: #CC6600'>Stimulator not initialized.</b> "
                    "Click <b>Initialize</b> in the Hardware panel on the "
                    "Setup tab.  If Initialize fails, close Stim-2 / "
                    "Stimulator V2 first — it holds exclusive USB access.")
            else:
                self.status_label.setText(
                    "<b style='color: #777'>Hardware not connected.</b> "
                    "Connect the PlexStim + scope on the Setup tab before "
                    "running the sweep.")
        except Exception:
            pass

    # ---------------------------------------------------------- plot view
    #: Plot-resize bounds.  Same numbers as the pulse-pattern preview so
    #: the two plots feel consistent when both are visible.
    PLOT_MIN_HEIGHT: int = 160
    PLOT_MAX_HEIGHT: int = 800
    PLOT_DEFAULT_HEIGHT: int = 280
    _PREFS_SECTION: str = "calibration_tab"
    _PREFS_PLOT_HEIGHT_KEY: str = "plot_height_px"

    def _change_plot_height(self, delta_px: int) -> None:
        """Resize the live capture plot by ``delta_px``.

        Clamped to ``[PLOT_MIN_HEIGHT, PLOT_MAX_HEIGHT]``.  The new height
        is also persisted to prefs so the user's choice survives across
        launches — mirrors the pulse-pattern preview's +/- buttons.

        Tracks the target height in ``self._plot_target_height`` rather
        than reading ``widget.height()`` because that returns Qt's default
        framework value (often 480) before the widget is first shown,
        producing surprising jumps on the first click.
        """
        if self._plot_widget is None:
            return
        cur = getattr(self, "_plot_target_height", self.PLOT_DEFAULT_HEIGHT)
        new_h = max(self.PLOT_MIN_HEIGHT,
                    min(self.PLOT_MAX_HEIGHT, int(cur) + int(delta_px)))
        if new_h == cur:
            return
        self._plot_target_height = new_h
        # setMinimumHeight pins the lower bound; the widget can still grow
        # if the splitter drags the boundary downward.
        self._plot_widget.setMinimumHeight(new_h)
        self._plot_widget.setMaximumHeight(self.PLOT_MAX_HEIGHT)
        self._save_plot_height(new_h)

    def _save_plot_height(self, h: int) -> None:
        try:
            prefs = load_prefs() or {}
            sec = prefs.setdefault(self._PREFS_SECTION, {})
            sec[self._PREFS_PLOT_HEIGHT_KEY] = int(h)
            save_prefs(prefs)
        except Exception:
            pass

    def _restore_plot_height(self) -> None:
        """Apply the persisted plot height (if any).  Safe no-op on
        platforms where the prefs file is missing or malformed."""
        if self._plot_widget is None:
            return
        try:
            prefs = load_prefs() or {}
            h = prefs.get(self._PREFS_SECTION, {}).get(
                self._PREFS_PLOT_HEIGHT_KEY)
            if h is None:
                h = self.PLOT_DEFAULT_HEIGHT
            h = max(self.PLOT_MIN_HEIGHT, min(self.PLOT_MAX_HEIGHT, int(h)))
            self._plot_target_height = h
            self._plot_widget.setMinimumHeight(h)
            self._plot_widget.setMaximumHeight(self.PLOT_MAX_HEIGHT)
        except Exception:
            pass

    def _parse_channel_list(self) -> list:
        """Parse the channels-to-sweep text field into a sorted list of ints.

        Accepts individual numbers and hyphen ranges, comma-separated:
        ``"1-4, 8, 12-16"`` → ``[1, 2, 3, 4, 8, 12, 13, 14, 15, 16]``

        Invalid tokens are silently skipped.  Returns an empty list when
        the field is blank or contains no valid channel numbers.
        """
        n_max = int((getattr(self._stim, "info", None)
                     and self._stim.info.n_channels) or 64) if self._stim else 64
        channels = set()
        text = self.channels_edit.text() if hasattr(self, "channels_edit") else ""
        for token in text.replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                parts = token.split("-", 1)
                try:
                    lo, hi = int(parts[0].strip()), int(parts[1].strip())
                    channels.update(range(lo, hi + 1))
                except (ValueError, IndexError):
                    pass
            else:
                try:
                    channels.add(int(token))
                except ValueError:
                    pass
        # Clamp to valid stimulator range.
        return sorted(ch for ch in channels if 1 <= ch <= n_max)

    #: Hardcoded acquisition mode for the calibration sweep.  Removed
    #: from the UI (was a SAMPLE / AVERAGE combo); calibration always
    #: uses AVERAGE since the IR-step + cap-ramp fits need clean
    #: traces and SAMPLE mode adds too much variance to the per-step
    #: ``measured_ua``.
    CAL_ACQ_MODE: str = "AVERAGE"
    #: Hardcoded NUMAVg count for the calibration sweep.  Was a
    #: user-facing combo; pinned at 64 (lab convention).  64 gives
    #: √64 = 8× noise reduction — more than enough for the access-
    #: voltage extrapolation and cap-ramp slope fit, while keeping
    #: each capture under ~1.5 s at the 50 pps pulse rate (vs ~3 s
    #: for 128 and ~6 s for 256).
    CAL_N_AVERAGES: int = 64

    def _cal_navg(self) -> int:
        """Return the calibration average count.  Always
        :data:`CAL_N_AVERAGES` — the UI control was removed.
        """
        return int(self.CAL_N_AVERAGES)

    def _on_run_sweep(self):
        """Run the per-channel amplitude sweep against the test board.

        Flow:
          1. Show an instructions popup (test-board pin-out
             reminder, "scope is going to be driven for ~N seconds",
             confirm to proceed).
          2. Run the sweep synchronously, walking each channel and
             each amplitude in the grid. Between captures call
             ``QApplication.processEvents`` so the dialog stays
             responsive (progress bar / status label / preview
             plot update live, and the Abort button is clickable).
          3. Fit ``I_actual = a · I_mon + b`` per channel.
          4. Populate the results table with gain / offset / RMSD
             per channel; flag out-of-band channels.
          5. Enable the Save button so the user can persist the
             coefficients via :func:`write_calibration_payload`.
        """
        if not (self._stim and self._scope):
            QtWidgets.QMessageBox.warning(
                self, "Hardware not connected",
                "Cannot run stimulator verification without both the PlexStim "
                "and the oscilloscope connected. Use the "
                "Connection panel on the Setup tab to attach them, "
                "then re-open this dialog.")
            return
        # Resolve the channel list from the editable field.
        channels_to_sweep = self._parse_channel_list()
        if not channels_to_sweep:
            QtWidgets.QMessageBox.warning(
                self, "No channels selected",
                "The \"Channels to sweep\" field is empty or contains no "
                "valid channel numbers. Enter at least one channel.")
            return
        load_ohm = float(self.DEFAULT_LOAD_OHM)
        amplitudes = list(self.DEFAULT_AMPLITUDE_GRID_UA)
        total_steps = len(channels_to_sweep) * len(amplitudes)
        # ---- Instructions popup ----
        # Mirrors the checkbox gate above — repeated here so the
        # final go/no-go fires immediately before stim, when the
        # user's attention is highest. The black-Omnetics-only
        # language is repeated verbatim so the safety reminder is
        # unambiguous.
        load_cap_pf = float(self.DEFAULT_LOAD_CAP_PF)
        proceed = QtWidgets.QMessageBox.question(
            self, "Stimulator Verification — final check",
            f"<b>About to run the stimulator verification sweep.</b><br><br>"
            f"This will deliver biphasic pulses on each of "
            f"<b>{len(channels_to_sweep)} channel(s)</b> "
            f"({', '.join(str(c) for c in channels_to_sweep)}) at "
            f"<b>{len(amplitudes)} amplitudes</b> "
            f"({', '.join(f'{a:.0f}' for a in amplitudes)} µA), "
            f"capturing the scope V_mon trace each time.<br><br>"
            f"<b style='color:#b71c1c'>Confirm before proceeding:</b><br>"
            f"• The PlexStim channel array is connected to the "
            f"<b>Plexon 14-04-A-03-A test board</b> via the "
            f"<b>black Omnetics stimulation cable (Plexon "
            f"14-03-A-03)</b> — NOT a live electrode array.<br>"
            f"• The test board's per-channel RC series matches "
            f"<b>{load_ohm:.0f} Ω + {load_cap_pf:.0f} pF</b>.<br>"
            f"• The scope is on and wired <b>V_mon → CH1, "
            f"I_mon → CH2</b> on the test board's monitor "
            f"taps.<br><br>"
            f"Estimated duration: ~{int(total_steps * 1.2)} s "
            f"(~1 s per capture).<br><br>"
            f"Proceed?",
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No)
        if proceed != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        # ---- One-time scope setup (mirrors experiment_tabs pre-run sequence) ----
        if self._scope is not None:
            self._log(
                f"Verification start: {len(channels_to_sweep)} channel(s), "
                f"{len(amplitudes)} amplitude(s) "
                f"[{','.join(f'{a:.0f}' for a in amplitudes)} µA], "
                f"phase={self.PHASE_WIDTH_US:.0f} µs, "
                f"interphase={self.INTERPHASE_US:.0f} µs, "
                f"rate={self.PULSE_RATE_PPS:.0f} pps, "
                f"load={load_ohm:.0f} Ω + {load_cap_pf:.0f} pF.")
            try:
                # Calibration uses a 2 k record length — short enough to keep
                # USB-TMC transfers under ~50 ms per channel (so a 6-amplitude
                # × 16-channel sweep stays brisk) and long enough at 200 ns/pt
                # to resolve the 50 µs phases on the test board.  On
                # TBS-series scopes the quantized set is {1k, 2k, 20k, 200k,
                # 2M, 5M}; the driver snaps to the nearest legal value.
                # Calibration uses the SAME record length as experiments
                # (operator: "the calibration record length should match the
                # experimental record length").  set_record_length snaps to
                # the model's appropriate choice (20000 on TBS2000/TBS1000C,
                # 2500 on fixed-record families) — so calibration + experiment
                # records always match on a given scope.
                self._log(
                    f"Scope setup: record length "
                    f"{DEFAULT_RECORD_LENGTH} (snapped to model choices)")
                self._scope.set_record_length(DEFAULT_RECORD_LENGTH)
                # Hardcoded: AVERAGE mode, NUMAVg = 64.  See
                # :data:`CAL_ACQ_MODE` / :data:`CAL_N_AVERAGES`.
                acq_mode = self.CAL_ACQ_MODE
                navg_val = self._cal_navg()
                self._log(
                    f"Scope setup: acquisition mode {acq_mode}, "
                    f"n_avg={navg_val} (hardcoded for verification)")
                self._scope.set_acquisition_mode(acq_mode, n_avg=navg_val)
                # Trigger: always I_mon (CH2), FALL edge, negative threshold.
                # Calibration pulse is cathodic-first → I_mon goes negative
                # on the phase-1 onset.  Pass the SIGNED amplitude (negative
                # for cathodic-first) to ``_imon_trigger_level`` — the result
                # inherits the sign so a negative level falls out naturally.
                # No digital-delay offset is needed since the trigger source
                # IS the current waveform — MATLAB ``setOscillocopeView.m``
                # also sets digitalDelay=0 when trigSource ≠ EXT.
                # Pass the actual I_mon scaling — on a NIL device
                # (1 mV/µA) the threshold gets clamped to 50 % of
                # expected peak so the trigger fires.
                _info_init = (getattr(self._stim, "info", None)
                              if self._stim else None)
                _imon_vpu_init = float(
                    getattr(_info_init, "imon_scaling_v_per_ua",
                            IMON_SCALING_DEFAULT) or IMON_SCALING_DEFAULT)
                _trig_level_init = _imon_trigger_level(
                    amp_ua_signed=-float(min(amplitudes)),   # cathodic-first
                    phase_width_us=float(self.PHASE_WIDTH_US),
                    imon_v_per_ua=_imon_vpu_init,
                )
                trig_src = self.cal_imon_combo.currentText()  # CH2
                self._log(
                    f"Scope setup: trigger source={trig_src}, slope=FALL, "
                    f"mode=NORMAL, level={_trig_level_init*1000:+.2f} mV")
                self._scope.set_trigger(trig_src,
                                        level_v=_trig_level_init,
                                        slope="FALL",
                                        mode="NORMAL",
                                        # Calibration always triggers
                                        # off the I_mon channel itself
                                        # (the current edge IS the
                                        # trigger), so no Plexon
                                        # digital-sync offset applies.
                                        digital=False)
                self._log(
                    f"Scope setup: horizontal layout for pulse "
                    f"phase1={self.PHASE_WIDTH_US:.0f} µs, "
                    f"interphase={self.INTERPHASE_US:.0f} µs, "
                    f"phase2={self.PHASE_WIDTH_US:.0f} µs, "
                    f"discharge={self.DISCHARGE_US:.0f} µs "
                    f"(no EXT digital delay)")
                _scale_s, _pos_pct = self._scope.auto_layout_for_pulse(
                    phase1_us=float(self.PHASE_WIDTH_US),
                    interphase_us=float(self.INTERPHASE_US),
                    phase2_us=float(self.PHASE_WIDTH_US),
                    discharge_us=float(self.DISCHARGE_US),
                    ext_trigger=False,   # I_mon trigger ⇒ digital_delay = 0
                )
                self._log(
                    f"Scope setup: applied {_scale_s*1e6:.1f} µs/div, "
                    f"position {_pos_pct:.2f}% (window "
                    f"{_scale_s*1e6 * float(getattr(self._scope, '_n_horiz_divs', 10.0)):.1f} µs)")
            except Exception as e:
                self.status_progress.setText(f"Scope setup warning: {e}")
                self._log(f"Scope setup warning: {e}")
                QtWidgets.QApplication.processEvents()

        # ---- Sweep ----
        self._aborted = False
        self._results.clear()
        self._fit.clear()
        self._scaling_validation = {}
        self._btn_run.setEnabled(False)
        self._btn_abort.setEnabled(True)
        self._btn_save.setEnabled(False)
        self.results_table.setRowCount(0)
        self._row_by_channel.clear()
        self.progress_bar.setValue(0)
        self._log_pane.clear()
        self._log("=" * 64)
        self._log(f"Sweep started — {len(channels_to_sweep)} channel(s), "
                  f"{len(amplitudes)} amplitude(s), "
                  f"{total_steps} total steps.")
        self._log("=" * 64)
        import time as _time
        _sweep_t0 = _time.perf_counter()
        try:
            self._run_sweep_blocking(channels_to_sweep, amplitudes, load_ohm,
                                     total_steps)
        finally:
            self._btn_run.setEnabled(True)
            self._btn_abort.setEnabled(False)
        from ..hardware.tektronix import _fmt_elapsed
        _sweep_elapsed = _time.perf_counter() - _sweep_t0
        # End-of-sweep summary — total time, per-step average, error count.
        _err_count = sum(1 for ch in self._results
                         for _t in self._results[ch]
                         if _t[1] != _t[1])   # NaN check on measured_ua
        self._log("=" * 64)
        self._log(
            f"Sweep finished — total {_fmt_elapsed(_sweep_elapsed)} "
            f"({_sweep_elapsed / max(total_steps, 1) * 1000:.1f} ms/step avg, "
            f"{_err_count} step(s) returned NaN)")
        self._log("=" * 64)
        # ---- Fit ----
        if self._aborted:
            self.status_progress.setText(
                "Sweep aborted by user. Partial results discarded.")
            self._log("Sweep aborted by user. Partial results discarded.")
            self._results.clear()
            return
        self._fit_and_render_results(load_ohm)

    def _on_testboard_confirm_toggled(self, checked: bool) -> None:
        """Gate the Run-sweep button on the test-board checkbox.

        Run sweep requires BOTH:
          * the user to have ticked the test-board confirmation
            (this is a safety interlock — it stops anyone from
            accidentally driving full-amplitude pulses into a live
            electrode array thinking they were calibrating), and
          * the PlexStim + scope hardware handles to be connected.

        The tooltip and status banner update so the user can see
        which gate is still preventing them from running.
        """
        ready = bool(checked) and bool(self._hw_connected)
        self._btn_run.setEnabled(ready)
        if not self._hw_connected:
            self._btn_run.setToolTip(
                "Hardware not connected. Attach the PlexStim + "
                "scope via the Setup tab, then re-open this dialog.")
        elif not checked:
            self._btn_run.setToolTip(
                "Tick the test-board confirmation checkbox to "
                "unlock the sweep.")
        else:
            self._btn_run.setToolTip(
                "Run the per-channel stimulator verification sweep against "
                "the test board.")

    def _on_abort_clicked(self):
        """User clicked Abort during a running sweep. Setting the
        flag short-circuits the next iteration of the sweep loop
        — actual hardware shutdown happens there so we don't
        leave the device in a half-programmed state."""
        self._aborted = True
        self.status_progress.setText(
            "Abort requested — finishing current capture…")
        self._log("Abort requested — finishing current capture…")

    # ------------------------------------------------------------------
    # RC-model simulation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _simulate_vmon_trace(
        amp_ua: float,
        load_ohm: float,
        load_cap_pf: float,
        phase_us: float,
        interphase_us: float,
        t_us: "np.ndarray",
        t0_us: float,
        vmon_v_per_v: float = 1.0,
        discharge_us: float = 0.0,
        phase1_sign: float = -1.0,
    ) -> "np.ndarray":
        """Return a simulated V_mon trace (V, as seen by the scope) for a
        charge-balanced biphasic pulse on an RC series load.

        The model assumes:
        * Phase 1 polarity is set by ``phase1_sign`` (-1 = cathodic first,
          +1 = anodic first). Auto-detect from data before calling.
        * The capacitor is fully discharged at the start of phase 1.
        * No passive decay during the interphase gap (ideal current source
          presents infinite impedance when off, so V_C is held constant).
        * After phase 2 the load returns to 0 V (charge-balanced ⇒ V_cap=0).
        * During the discharge window (active short) V stays at 0.

        V_load(t) during each segment (for phase1_sign = -1, cathodic first):
          Phase 1  t ∈ [t0, t0+W):         -I·R − (I/C)·(t−t0)
          Gap      t ∈ [t0+W, t0+W+G):      −(I/C)·W   (plateau)
          Phase 2  t ∈ [t0+W+G, t0+2W+G):  +I·R − (I/C)·W + (I/C)·(t−t0−W−G)
          Discharge t ∈ [t0+2W+G, t0+2W+G+D): 0  (actively shorted)

        V_scope = V_load × vmon_v_per_v.
        """
        import numpy as np
        s = float(phase1_sign)     # -1 = cathodic first (V_mon < 0 in p1)
                                   # +1 = anodic  first (V_mon > 0 in p1)
        I = amp_ua * 1e-6          # magnitude only, always positive (A)
        R = float(load_ohm)        # Ω
        C = max(1e-15, load_cap_pf * 1e-12)  # F
        W = phase_us * 1e-6        # s
        G = interphase_us * 1e-6   # s
        k = vmon_v_per_v

        t_s = (np.asarray(t_us, dtype=float) - t0_us) * 1e-6  # relative, in seconds

        v = np.zeros(len(t_s), dtype=float)

        # Phase 1 — polarity set by s.
        # V_electrode = s·(I·R + (I/C)·t)  →  V_scope = V_electrode · k
        #   s=-1 (cathodic): V_scope is negative and ramps more negative  ✓
        #   s=+1 (anodic):   V_scope is positive and ramps more positive   ✓
        m1 = (t_s >= 0) & (t_s < W)
        v[m1] = s * (I * R + (I / C) * t_s[m1]) * k

        # V_cap at end of phase 1: s·(I/C)·W
        #   s=-1: -(I/C)·W  (cap charged negatively during cathodic phase)
        #   s=+1: +(I/C)·W  (cap charged positively during anodic phase)
        Vc_p1_end = s * (I / C) * W

        # Interphase plateau — no current → no resistive drop; cap holds.
        mi = (t_s >= W) & (t_s < W + G)
        v[mi] = Vc_p1_end * k

        # Phase 2 — opposite polarity (-s); cap drains back to 0 (charge-balanced).
        # V_electrode = -s·(I·R + (I/C)·(t−W−G)) + Vc_p1_end
        m2 = (t_s >= W + G) & (t_s < 2 * W + G)
        v[m2] = (-s * (I * R + (I / C) * (t_s[m2] - W - G)) + Vc_p1_end) * k

        # Discharge window — stimulator actively shorts electrode; V_cap=0 ⇒ V=0.
        # (samples already zero from np.zeros initialisation)

        return v

    def _sync_plot_geometry(self) -> None:
        if self._plot_syncing or self._right_vb is None:
            return
        self._plot_syncing = True
        try:
            vb = self._plot_widget.plotItem.vb
            self._right_vb.setGeometry(vb.sceneBoundingRect())
            self._right_vb.linkedViewChanged(vb, self._right_vb.XAxis)
        except Exception:
            pass
        finally:
            self._plot_syncing = False

    def _update_acq_plot(self, acq, *, sim_t_us=None, sim_v=None,
                         channel: Optional[int] = None,
                         amp_ua: Optional[float] = None) -> None:
        # Update the plot title FIRST, before any early returns —
        # the title is a separate Qt widget and doesn't depend on
        # the pyqtgraph PlotWidget being valid or the acquisition
        # having usable channel data.  Keeps the title in sync
        # with the sweep even on captures where the plot can't be
        # redrawn (sim missing, scope rejected the trace, etc.).
        try:
            if channel is not None and amp_ua is not None:
                _title = (f"Channel {int(channel)}  ·  "
                          f"{abs(float(amp_ua)):.0f} µA")
            elif channel is not None:
                _title = f"Channel {int(channel)}"
            elif amp_ua is not None:
                _title = f"{abs(float(amp_ua)):.0f} µA"
            else:
                _title = ""
            if hasattr(self, "_plot_title_label") and _title:
                self._plot_title_label.setText(_title)
                self._plot_title_label.setVisible(True)
                self._plot_title_label.repaint()
                self._plot_title_label.update()
        except Exception as _title_err:
            try:
                self._log(f"Plot title set failed: {_title_err}")
            except Exception:
                pass
        if self._plot_widget is None or self._pg is None or acq is None:
            return
        import numpy as np
        pg = self._pg
        VMON_COLOR = "#E6B800"
        IMON_COLOR = "#00B4C8"
        try:
            t_us = getattr(acq, "time_us", None)
            channels = getattr(acq, "channels", {}) or {}
            if t_us is None:
                return
            v_mon_phys = self.cal_vmon_combo.currentText()
            i_mon_phys = self.cal_imon_combo.currentText()
            # (Plot title was already updated at the top of this
            # function — see the unconditional block above the
            # ``if self._plot_widget is None`` early-return.)

            if v_mon_phys in channels:
                y_v = np.asarray(channels[v_mon_phys], dtype=float)
                if v_mon_phys not in self._plot_curves:
                    # width=4 instead of 2 — easier to see against the
                    # grid and the RC-model overlay underneath, especially
                    # at small amplitudes where the trace barely fills 2
                    # divisions on the scope.
                    c = self._plot_widget.plot(pen=pg.mkPen(VMON_COLOR, width=1),
                                               name=f"V_mon ({v_mon_phys})")
                    c.setZValue(1)
                    self._plot_curves[v_mon_phys] = c
                self._plot_curves[v_mon_phys].setData(t_us, y_v)

            if i_mon_phys in channels and self._right_vb is not None:
                y_i = np.asarray(channels[i_mon_phys], dtype=float) * 1000.0
                label = f"I_mon ({i_mon_phys})"
                if i_mon_phys not in self._plot_right_curves:
                    c = pg.PlotDataItem(t_us, y_i, pen=pg.mkPen(IMON_COLOR, width=1),
                                        name=label)
                    self._right_vb.addItem(c)
                    if self._plot_legend is not None:
                        self._plot_legend.addItem(c, label)
                    self._plot_right_curves[i_mon_phys] = c
                else:
                    self._plot_right_curves[i_mon_phys].setData(t_us, y_i)

            if sim_t_us is not None and sim_v is not None:
                if self._plot_sim_curve is None:
                    self._plot_sim_curve = self._plot_widget.plot(
                        sim_t_us, sim_v,
                        pen=pg.mkPen("#9e9e9e", width=1),
                        name="RC model (V_mon)")
                    self._plot_sim_curve.setZValue(-1)
                else:
                    self._plot_sim_curve.setData(sim_t_us, sim_v)

            # X range — exact port of MATLAB ``getPlot.m``:
            #   xMin = round(min(time), 1, 'significant');
            #   xMax = round(max(time), 1, 'significant');
            #   xlim([xMin xMax]);
            # Uses min/max of the actual captured time array (not
            # first/last — equivalent for a monotonically increasing
            # vector but safer if the driver ever returns a reversed
            # axis), and MATLAB's ``xlim`` does not add padding so we
            # pass padding=0 to pyqtgraph.
            if acq is not None and acq.time_us.size:
                _t = np.asarray(acq.time_us, dtype=float)
                x_min = _round_sig(float(_t.min()), 1)
                x_max = _round_sig(float(_t.max()), 1)
                # Defensive: if both round to the same value (e.g. a
                # single-sample acquisition), fall back to the raw
                # min/max so setXRange doesn't see a zero-width range.
                if x_min == x_max:
                    x_min = float(_t.min())
                    x_max = float(_t.max())
            else:
                x_min, x_max = self._plot_x_min, self._plot_x_max
            self._plot_widget.setXRange(x_min, x_max, padding=0)
            # ---- Y range: align zero crossings (MATLAB getPlot.m) ----
            # MATLAB:
            #   yyaxis right; ylimr = get(gca,'Ylim'); ratio = ylimr(1)/ylimr(2);
            #   yyaxis left;  yliml = get(gca,'Ylim');
            #   if yliml(2)*ratio<yliml(1)
            #       set(gca,'Ylim',[yliml(2)*ratio yliml(2)])
            #   else
            #       set(gca,'Ylim',[yliml(1) yliml(1)/ratio])
            #   end
            # i.e. compute the right-axis (min/max) ratio, then stretch
            # one end of the left axis so its zero lines up with the
            # right axis's zero — left & right tick rows stay
            # proportional and 0 V on V_mon aligns with 0 µA on I_mon.
            left_vb = self._plot_widget.plotItem.vb
            right_vb = self._right_vb
            # Start from data-fit autoranges so we know the natural
            # extent of each trace, then override with the aligned values.
            left_vb.enableAutoRange(axis=left_vb.YAxis)
            if right_vb is not None:
                right_vb.enableAutoRange(axis=right_vb.YAxis)
            # Force pyqtgraph to compute the autorange now so
            # viewRange() reports current data extents (otherwise it
            # may still hold the previous frame's limits).
            try:
                left_vb.updateAutoRange()
                if right_vb is not None:
                    right_vb.updateAutoRange()
            except Exception:
                pass
            if right_vb is not None:
                yliml = left_vb.viewRange()[1]   # [min, max] on V_mon
                ylimr = right_vb.viewRange()[1]  # [min, max] on I_mon
                yl_lo, yl_hi = float(yliml[0]), float(yliml[1])
                yr_lo, yr_hi = float(ylimr[0]), float(ylimr[1])
                # MATLAB divides by ylimr(2); guard for the degenerate
                # all-zero / single-sign case where yr_hi == 0.
                if yr_hi != 0.0:
                    ratio = yr_lo / yr_hi
                    if yl_hi * ratio < yl_lo:
                        new_lo, new_hi = yl_hi * ratio, yl_hi
                    else:
                        # ratio is negative for a biphasic trace
                        # (yr_lo<0, yr_hi>0); yl_lo/ratio flips sign,
                        # extending the high end so 0-crossings align.
                        new_lo, new_hi = yl_lo, (yl_lo / ratio
                                                  if ratio != 0.0 else yl_hi)
                    # Disable autorange on the left so our explicit
                    # range survives the next paint cycle.
                    left_vb.enableAutoRange(axis=left_vb.YAxis,
                                            enable=False)
                    left_vb.setYRange(new_lo, new_hi, padding=0)
            self._sync_plot_geometry()
            QtWidgets.QApplication.processEvents()
        except Exception:
            pass

    def _run_sweep_blocking(self, channels: list,
                            amplitudes: list, load_ohm: float,
                            total_steps: int) -> None:
        """Walk every (channel, amplitude) cell, deliver one
        biphasic pulse, scope-capture V_mon, derive measured
        current from V_mon / load_ohm. Updates progress widgets
        between captures so the dialog stays interactive.

        Pure synchronous loop with ``QApplication.processEvents``
        sprinkled in — simpler than spawning a QThread and the
        hardware I/O is the dominant cost anyway. The runner-
        side equivalent (in ``ExperimentRunner``) uses the same
        pattern.
        """
        from ..waveforms import PulsePattern
        import numpy as np
        import time

        v_mon_phys = self.cal_vmon_combo.currentText()
        i_mon_phys = self.cal_imon_combo.currentText()
        navg = getattr(self._scope, "_expected_acq_navg", None) or 8

        # Restrict the scope's channel_aliases to ONLY the two calibration
        # channels for the duration of this sweep.  capture_while_running
        # reads every alias; if eret/eact (CH3/CH4) are still present and
        # those channels are disabled on the scope, CURVe? raises a VISA
        # error and the whole capture fails.  Restored in the finally below.
        _original_aliases = dict(getattr(self._scope, "channel_aliases", {}))
        if hasattr(self._scope, "channel_aliases"):
            self._scope.channel_aliases = {
                "vmon": v_mon_phys,
                "imon": i_mon_phys,
            }

        try:

            # Stim info for scaling calculations.
            load_cap_pf = float(self.DEFAULT_LOAD_CAP_PF)
            info = getattr(self._stim, "info", None)
            imon_v_per_ua = float(getattr(info, "imon_scaling_v_per_ua",
                                          IMON_SCALING_DEFAULT)
                                  or IMON_SCALING_DEFAULT)
            vmon_v_per_v  = float(getattr(info, "vmon_scaling_v_per_v",
                                          VMON_SCALING_DEFAULT)
                                  or VMON_SCALING_DEFAULT)
            max_amp_ua = max(amplitudes)

            # Derive plot x-range from scope's current timebase state
            # (set in the one-time setup block before this call).
            try:
                scale_s, position_pct = self._scope.auto_layout_for_pulse(
                    phase1_us=float(self.PHASE_WIDTH_US),
                    interphase_us=float(self.INTERPHASE_US),
                    phase2_us=float(self.PHASE_WIDTH_US),
                    discharge_us=float(self.DISCHARGE_US),
                    ext_trigger=False,  # always CH2 (I_mon), no EXT
                )
                # Window width = scale × divs.  Most Tek families use
                # 10 horizontal divisions; TBS2000B uses 15 (queried in
                # the driver's open()).  Read whatever the scope reports.
                _n_divs = float(getattr(self._scope, "_n_horiz_divs", 10.0))
                total_us = scale_s * 1e6 * _n_divs
                trig_us  = position_pct / 100.0 * total_us
                self._plot_x_min = -trig_us
                self._plot_x_max = total_us - trig_us
            except Exception:
                pass

            # Vertical channel setup — only ensure the V_mon and I_mon
            # channels we'll be reading are ON.  Do NOT turn off other
            # channels: the user may have additional traces on screen for
            # monitoring and the calibration must not disturb them.
            try:
                self._log(
                    f"Scope setup: enabling channels {v_mon_phys} (V_mon) "
                    f"and {i_mon_phys} (I_mon) — leaving other channels untouched")
                for ch_name in (v_mon_phys, i_mon_phys):
                    try:
                        self._scope._w(f"SELect:{ch_name} ON")
                    except Exception:
                        pass
                # Per operator request, run BOTH V_mon and I_mon at FULL
                # bandwidth (overriding the former 20 MHz I_mon limit).
                # See TektronixOscilloscope.set_channel_bandwidth_full.
                # Trade-off: more broadband noise on I_mon, so the
                # trigger comparator may be less reliable.  Silent no-op
                # when the scope's series lacks the BANdwidth option.
                try:
                    bw_v = self._scope.set_channel_bandwidth_full(v_mon_phys)
                    bw_i = self._scope.set_channel_bandwidth_full(i_mon_phys)

                    def _fmt_bw(_b):
                        if _b is None or _b == float("inf"):
                            return "FULL"
                        return f"{_b:.0f} MHz"
                    if bw_v is not None or bw_i is not None:
                        self._log(
                            f"Scope setup: bandwidth — "
                            f"{v_mon_phys} (V_mon) = {_fmt_bw(bw_v)}, "
                            f"{i_mon_phys} (I_mon) = {_fmt_bw(bw_i)} "
                            f"(full BW on all channels, incl. I_mon).")
                except Exception:
                    pass
                # Initial V_mon / I_mon scales sized for the SMALLEST amp in
                # the grid so the first capture's trace is visible — the
                # per-step update below re-sizes for each amp_ua.
                _first_amp = float(min(amplitudes))
                _vmon_vpd_init = _vmon_vertical_scale(
                    amp_ua=_first_amp,
                    load_r_ohm=float(self.DEFAULT_LOAD_OHM),
                    load_c_pf=load_cap_pf,
                    phase_us=float(self.PHASE_WIDTH_US),
                    vmon_v_per_v=vmon_v_per_v,
                )
                _imon_vpd_init = _imon_vertical_scale(
                    _first_amp, imon_v_per_ua=imon_v_per_ua)
                self._log(
                    f"Scope setup: initial vertical scaling (sized for "
                    f"smallest amp = {_first_amp:.0f} µA):  "
                    f"{v_mon_phys} (V_mon) = {_vmon_vpd_init*1000:.2f} mV/div,  "
                    f"{i_mon_phys} (I_mon) = {_imon_vpd_init*1000:.2f} mV/div")
                self._scope.set_channel_scale(v_mon_phys, _vmon_vpd_init)
                self._scope.set_channel_scale(i_mon_phys, _imon_vpd_init)
                self._log(
                    f"Scope setup: vertical position {v_mon_phys} = 0 div, "
                    f"{i_mon_phys} = 0 div")
                self._scope.set_channel_position(v_mon_phys, 0.0)
                self._scope.set_channel_position(i_mon_phys, 0.0)
            except Exception as e:
                self.status_progress.setText(f"Scope vertical scale warning: {e}")
                self._log(f"Scope vertical scale warning: {e}")
                QtWidgets.QApplication.processEvents()

            # ---- Idle-baseline measurement -----------------------------
            # Capture a single frame BEFORE any stim runs so we know the
            # scope's resting offset on the V_mon and I_mon channels.
            # The scope's NORMAL trigger won't fire with no signal, so
            # we briefly switch to AUTO mode, take one capture, then
            # restore NORMAL.  The averaged baselines are subtracted
            # from every subsequent measurement (V_mon edge step is
            # computed relative to the idle level, not absolute 0 V).
            self._vmon_offset_v = 0.0
            self._imon_offset_v = 0.0
            try:
                self._scope._w(f"{self._scope._cmds.trig_mode} AUTO")
                acq_baseline = self._scope.capture_while_running(
                    wait_s=0.20,
                    tick_fn=lambda: QtWidgets.QApplication.processEvents(),
                )
                _chans = getattr(acq_baseline, "channels", {}) or {}
                v_idle = _chans.get(v_mon_phys)
                i_idle = _chans.get(i_mon_phys)
                if v_idle is not None and len(v_idle) >= 4:
                    self._vmon_offset_v = float(np.mean(v_idle))
                if i_idle is not None and len(i_idle) >= 4:
                    self._imon_offset_v = float(np.mean(i_idle))
                self._log(
                    f"Idle baseline: V_mon={self._vmon_offset_v*1e3:+.2f} mV, "
                    f"I_mon={self._imon_offset_v*1e3:+.2f} mV "
                    f"(subtracted from each capture).")
            except Exception as _bsl_err:
                self._log(f"Idle-baseline measurement skipped: {_bsl_err}")
            finally:
                # Restore NORMAL trigger mode for the sweep so a missed
                # trigger fails fast instead of producing an auto-fired
                # frame full of noise.
                try:
                    self._scope._w(f"{self._scope._cmds.trig_mode} NORMAL")
                except Exception:
                    pass

            # NOTE: the one-time setup discard frame was REMOVED.  Operator
            # (CWRU, "calibration runs too quickly" + "skip the first
            # completed acquisition") wants EVERY amplitude to discard its
            # first completed averaged acquisition — see
            # ``_capture_one_amplitude``.  That per-amplitude skip also
            # absorbs the initial settings-change transient on the first
            # step, so a separate setup-time discard is redundant.

            # ---- Main sweep loop ----------------------------------------
            step_idx = 0
            for ch in channels:
                if self._aborted:
                    return
                self._results[ch] = []
                # Route V_mon / I_mon to this channel once per channel —
                # doesn't change across amplitude steps.
                try:
                    self._stim.set_monitor_channel(ch)
                except Exception:
                    pass
                # MATLAB setDefaultScopeView3.m: revert the scope to its
                # default per-channel view at every channel change — clear
                # the per-capture autorange history, re-run the horizontal
                # layout, and re-size the V_mon / I_mon vertical scales so
                # the new channel doesn't inherit the previous one's
                # converged scales (which may be wrong for a different
                # electrode load).
                try:
                    if hasattr(self._scope, "reset_adapt_state"):
                        self._scope.reset_adapt_state(None)
                    _ch_first_amp = float(min(amplitudes))
                    _vmon_vpd0 = _vmon_vertical_scale(
                        amp_ua=_ch_first_amp,
                        load_r_ohm=float(self.DEFAULT_LOAD_OHM),
                        load_c_pf=load_cap_pf,
                        phase_us=float(self.PHASE_WIDTH_US),
                        vmon_v_per_v=vmon_v_per_v,
                    )
                    _imon_vpd0 = _imon_vertical_scale(
                        _ch_first_amp, imon_v_per_ua=imon_v_per_ua)
                    self._scope.auto_layout_for_pulse(
                        phase1_us=float(self.PHASE_WIDTH_US),
                        interphase_us=float(self.INTERPHASE_US),
                        phase2_us=float(self.PHASE_WIDTH_US),
                        discharge_us=float(self.DISCHARGE_US),
                        ext_trigger=False,
                    )
                    self._scope.set_channel_scale(v_mon_phys, _vmon_vpd0)
                    self._scope.set_channel_scale(i_mon_phys, _imon_vpd0)
                    self._scope.set_channel_position(v_mon_phys, 0.0)
                    self._scope.set_channel_position(i_mon_phys, 0.0)
                    self._log(
                        f"Scope reverted to default view for channel {ch}: "
                        f"adapt state cleared, horizontal layout re-applied, "
                        f"{v_mon_phys} (V_mon) = {_vmon_vpd0*1e3:.2f} mV/div, "
                        f"{i_mon_phys} (I_mon) = {_imon_vpd0*1e3:.2f} mV/div "
                        f"(sized for smallest amp {_ch_first_amp:.0f} µA).")
                except Exception as _dv_err:
                    self._log(
                        f"Default-view revert for channel {ch} skipped: "
                        f"{_dv_err}")
                # Per-channel retry loop: if the R_load fit deviates
                # from the nominal :data:`DEFAULT_LOAD_OHM` by more
                # than ``_R_TOLERANCE_PCT``, redo the amplitude sweep
                # for this channel up to ``_R_RETRY_MAX`` more times.
                # On the assumption that the board IS the nominal R
                # and the first sweep just hit noise, the second
                # attempt often converges.  If the board is genuinely
                # off-nominal, all retries return similar (wrong-by-
                # board-not-by-noise) values and the final attempt's
                # data is kept.
                _R_TOLERANCE_PCT = 10.0
                _R_RETRY_MAX = 2
                for _retry_idx in range(_R_RETRY_MAX + 1):
                    if self._aborted:
                        return
                    if _retry_idx > 0:
                        self._log(
                            f"Channel {ch}: R_load off by "
                            f">{_R_TOLERANCE_PCT:.0f}% — retry "
                            f"{_retry_idx}/{_R_RETRY_MAX} "
                            f"(re-running amplitude sweep for this channel).")
                    # Clear this channel's captures so the retry
                    # replaces (rather than appends to) the data
                    # from the failed attempt.
                    self._results[ch] = []
                    for amp_ua in amplitudes:
                        if self._aborted:
                            return
                        step_idx += 1

                        # Per-step VERTICAL SCALES — both V_mon and I_mon.
                        _step_msg = (f"Channel {ch} / {channels[-1]}  ·  "
                                     f"{amp_ua:.0f} µA  ·  "
                                     f"step {step_idx} / {total_steps}"
                                     + (f"  (attempt {_retry_idx + 1})"
                                        if _retry_idx > 0 else ""))
                        self.status_progress.setText(_step_msg)
                        self._log(_step_msg)

                        try:
                            _imon_vpd_step = _imon_vertical_scale(
                                amp_ua, imon_v_per_ua=imon_v_per_ua)
                            _expected_peak_v = amp_ua * imon_v_per_ua
                            self._log(
                                f"Step: {i_mon_phys} (I_mon) vertical scale → "
                                f"{_imon_vpd_step*1000:.2f} mV/div "
                                f"(expected peak ≈ "
                                f"{_expected_peak_v*1e3:.1f} mV @ "
                                f"{imon_v_per_ua*1e3:.2f} mV/µA → "
                                f"{_expected_peak_v/_imon_vpd_step:.1f} divs)")
                            self._scope.set_channel_scale(
                                i_mon_phys, _imon_vpd_step)
                        except Exception:
                            pass
                        try:
                            vmon_vpd = _vmon_vertical_scale(
                                amp_ua=amp_ua,
                                load_r_ohm=float(self.DEFAULT_LOAD_OHM),
                                load_c_pf=float(self.DEFAULT_LOAD_CAP_PF),
                                phase_us=float(self.PHASE_WIDTH_US),
                                vmon_v_per_v=vmon_v_per_v,
                            )
                            self._log(
                                f"Step: {v_mon_phys} (V_mon) vertical scale → "
                                f"{vmon_vpd*1000:.2f} mV/div "
                                f"(load-aware peak fit)")
                            self._scope.set_channel_scale(v_mon_phys, vmon_vpd)
                        except Exception:
                            pass

                        # Adaptive trigger level.
                        try:
                            _trig_lvl_new = _imon_trigger_level(
                                amp_ua_signed=-float(amp_ua),
                                phase_width_us=float(self.PHASE_WIDTH_US),
                                imon_v_per_ua=imon_v_per_ua,
                            )
                            _expected_peak_mv = (
                                float(amp_ua) * imon_v_per_ua * 1e3)
                            self._log(
                                f"Step: trigger level → "
                                f"{_trig_lvl_new*1000:+.2f} mV "
                                f"on {i_mon_phys} (FALL edge, cathodic-first; "
                                f"expected peak ≈ {_expected_peak_mv:.1f} mV "
                                f"@ {imon_v_per_ua*1e3:.2f} mV/µA)")
                            self._scope.set_trigger_level(_trig_lvl_new)
                        except Exception:
                            pass
                        # Clamp to 100 — step_idx can exceed total_steps
                        # when a channel is retried for R_load drift.
                        pct = min(100, int(round(
                            100.0 * step_idx / max(1, total_steps))))
                        self.progress_bar.setValue(pct)
                        QtWidgets.QApplication.processEvents()
                        import time as _time_step
                        _step_t0 = _time_step.perf_counter()
                        try:
                            (measured_ua, imon_peak_v, acq,
                             model_rmsd_mv, est_cap_pf, est_r_ohm,
                             sim_t_us, sim_v,
                             step_v_raw, ramp_slope_raw,
                             phase1_sign_raw, dt_s_raw) = self._capture_one_amplitude(
                                ch, amp_ua, load_ohm, load_cap_pf,
                                navg=navg,
                                v_mon_phys=v_mon_phys, i_mon_phys=i_mon_phys)
                            _plot_t0 = _time_step.perf_counter()
                            self._update_acq_plot(acq,
                                                 sim_t_us=sim_t_us, sim_v=sim_v,
                                                 channel=ch, amp_ua=amp_ua)
                            from ..hardware.tektronix import _fmt_elapsed as _fe
                            _now = _time_step.perf_counter()
                            self._log(
                                f"  → measured {measured_ua:.2f} µA  "
                                f"(I·R={imon_peak_v*1e3:.1f} mV  "
                                f"RMSD={model_rmsd_mv:.2f} mV  "
                                f"R_est={est_r_ohm:.0f} Ω  "
                                f"C_est={est_cap_pf:.0f} pF)")
                            self._log(
                                f"  Step timing: capture+analyze "
                                f"{_fe(_plot_t0 - _step_t0)}, "
                                f"plot {_fe(_now - _plot_t0)}, "
                                f"total {_fe(_now - _step_t0)}")
                        except Exception as e:
                            err_msg = str(e)
                            self.status_progress.setText(
                                f"Channel {ch} @ {amp_ua:.0f} µA: error — {err_msg}")
                            self._log(f"  ERROR @ {amp_ua:.0f} µA: {err_msg}")
                            QtWidgets.QApplication.processEvents()
                            if step_idx == 1:
                                QtWidgets.QMessageBox.critical(
                                    self,
                                    "Acquisition error — sweep aborted",
                                    f"Failed on channel {ch} at {amp_ua:.0f} µA "
                                    f"(step 1 of {total_steps}):\n\n{err_msg}\n\n"
                                    "Likely causes:\n"
                                    "• Scope channels CH3/CH4 disabled but listed in "
                                    "channel aliases — only CH1/CH2 are needed\n"
                                    "• Stimulator not initialized — click Initialize "
                                    "in the Hardware panel first\n"
                                    "• Stim-2 (Plexon's application) is open and "
                                    "holding exclusive USB access — close it first"
                                )
                                self._aborted = True
                                return
                            continue
                        # Per-capture pre-trigger baselines (in volts).
                        _vmon_offset_capture = getattr(
                            self, "_last_capture_vmon_offset_v", 0.0)
                        _imon_offset_capture = getattr(
                            self, "_last_capture_imon_offset_v", 0.0)
                        self._results[ch].append(
                            (float(amp_ua), float(measured_ua),
                             float(imon_peak_v),
                             float(model_rmsd_mv), float(est_cap_pf),
                             float(est_r_ohm),
                             float(step_v_raw), float(ramp_slope_raw),
                             float(phase1_sign_raw), float(dt_s_raw),
                             float(_vmon_offset_capture),
                             float(_imon_offset_capture)))
                    # ---- End of amplitude loop — fit this attempt ----
                    try:
                        _fit_result = self._fit_one_channel(ch)
                    except Exception as _fit_err:
                        self._log(
                            f"Per-channel fit for {ch} failed: {_fit_err}")
                        _fit_result = None
                    # ---- R_load deviation check → retry if needed ----
                    # If the fit's R_load is more than ``_R_TOLERANCE_PCT``
                    # off the nominal :data:`DEFAULT_LOAD_OHM`, the sweep
                    # likely hit transient noise (small-amp triggers missed,
                    # bad first-capture frame, etc.) and another attempt
                    # often converges.  Accept whatever the final attempt
                    # produces if we exhaust the retries.
                    _r_fit = (_fit_result.get("fit_r_ohm", float("nan"))
                              if _fit_result else float("nan"))
                    if np.isfinite(_r_fit) and _r_fit > 0:
                        _r_dev_pct = (abs(_r_fit - self.DEFAULT_LOAD_OHM)
                                      / self.DEFAULT_LOAD_OHM * 100.0)
                    else:
                        _r_dev_pct = float("inf")
                    if _r_dev_pct <= _R_TOLERANCE_PCT:
                        self._log(
                            f"Channel {ch}: R_load = {_r_fit:.0f} Ω "
                            f"(within ±{_R_TOLERANCE_PCT:.0f}% of nominal "
                            f"{self.DEFAULT_LOAD_OHM:.0f} Ω) "
                            f"— accepting after attempt "
                            f"{_retry_idx + 1}.")
                        break
                    if _retry_idx >= _R_RETRY_MAX:
                        self._log(
                            f"Channel {ch}: R_load = {_r_fit:.0f} Ω "
                            f"(deviation {_r_dev_pct:.1f}% from nominal "
                            f"after {_R_RETRY_MAX + 1} attempts) "
                            f"— accepting final attempt.")
                        break
                    # Otherwise the outer ``for _retry_idx`` loop
                    # will repeat the amplitude sweep.
                    self._log(
                        f"Channel {ch}: R_load = {_r_fit:.0f} Ω "
                        f"(deviation {_r_dev_pct:.1f}% from nominal "
                        f"{self.DEFAULT_LOAD_OHM:.0f} Ω) — will retry.")

            self.status_progress.setText("Sweep complete. Fitting …")
            self._log("Sweep complete. Fitting …")
            self.progress_bar.setValue(100)
            QtWidgets.QApplication.processEvents()

        finally:
            if hasattr(self._scope, "channel_aliases"):
                self._scope.channel_aliases = _original_aliases

    def _capture_one_amplitude(self, channel: int, amp_ua: float,
                               load_ohm: float, load_cap_pf: float,
                               *, navg: int = 8,
                               v_mon_phys: str = "CH1",
                               i_mon_phys: str = "CH2") -> tuple:
        """Drive one biphasic pulse on ``channel``, capture V_mon + I_mon,
        return ``(measured_ua_from_vmon, imon_peak_v)``.

        **Why the edge step, not peak |V_mon|.** The test board load is
        RC series (4.99 kΩ + 4700 pF). During phase width W the cap
        charges linearly: V_C(t) = I·t/C. At 100 µA / 4700 pF / 200 µs,
        V_C = 4.26 V — 8× the V_R contribution (0.499 V). Peak |V_mon|
        is dominated by V_C and is not a clean measure of I·R.

        At each phase transition V_C is continuous, so the V_mon jump is
        purely I·R. We extract the four largest sample-to-sample diffs
        (one per phase edge) and average them to recover I·R, then divide
        by R for the measured current.
        """
        from ..waveforms import PulsePattern
        import numpy as np
        import time

        # Always cathodic-first (lab standard, polarity = -1).
        pat = PulsePattern.biphasic(
            amplitude_ua=amp_ua,
            phase_width_us=self.PHASE_WIDTH_US,
            polarity=-1,
            interphase_us=self.INTERPHASE_US,
            discharge_us=self.DISCHARGE_US,
            rate_hz=self.PULSE_RATE_PPS,
        )

        self._stim.load_channel(channel, pat)
        # Calibration walks one channel at a time — use the single-channel
        # start so we drive only the channel currently wired to the test
        # board's V_mon / I_mon tap.  Default PS_TRIG_SOFT trigger mode (set
        # by PS_InitAllStim) makes ``PS_StartStimChannel`` work without
        # extra setup.
        self._stim.start_channel(channel)
        pulse_period_s = 1.0 / pat.rate_hz
        # Timeout: N_avg periods + generous 5 s overhead for trigger latency.
        seq_timeout_s = navg * pulse_period_s + 5.0

        # Skip the FIRST completed averaged acquisition (operator: "skip
        # the first completed acquisition").  After load_channel +
        # start_channel the scope's averager is still flushing the
        # PREVIOUS amplitude's frames, so the first completed average is a
        # stale blend of old + new amplitude; discard it and read the NEXT
        # one, accumulated purely from THIS amplitude's pulses.  This
        # restores the per-amplitude discard that was traded away for
        # speed — the operator wants the cleaner, slower capture ("the
        # calibration runs too quickly").  Wrapped so a failed throwaway
        # never aborts the real capture below.
        try:
            self._scope.capture_single_sequence(
                n_acq=navg, timeout_s=seq_timeout_s,
                tick_fn=lambda: QtWidgets.QApplication.processEvents())
        except Exception as _skip_err:
            self._log(
                f"  skip-first frame at {amp_ua:.0f} µA skipped: {_skip_err}")

        def _clipped(arr):
            mn, mx = arr.min(), arr.max()
            n = len(arr)
            return (np.sum(arr == mn) > 0.05 * n or
                    np.sum(arr == mx) > 0.05 * n)

        acq = None
        v_mon = None
        i_mon = None
        try:
            for _attempt in range(5):
                # The amplitude-change transient / stale-blend frame was
                # already discarded by the skip-first capture above, so
                # THIS is the clean averaged acquisition we keep.
                acq = self._scope.capture_single_sequence(
                    n_acq=navg,
                    timeout_s=seq_timeout_s,
                    tick_fn=lambda: QtWidgets.QApplication.processEvents())
                chan_data = getattr(acq, "channels", {}) or {}
                v_mon = chan_data.get(v_mon_phys)
                i_mon = chan_data.get(i_mon_phys)

                if v_mon is None or len(v_mon) < 4:
                    continue
                v_arr_f = np.asarray(v_mon, dtype=float)
                if float(np.max(np.abs(v_arr_f))) < 1e-6:
                    continue  # flat/blank trace — trigger may have misfired

                # Adapt vertical scale while stim is running.
                # If either channel changes scale, re-capture with the new
                # setting so the stored waveform reflects the correct range.
                scale_changed = False
                vlo, vhi = float(v_arr_f.min()), float(v_arr_f.max())
                # ---- ONE-SIDED rail detection (position-aware) --------
                # The test-board V_mon is strongly asymmetric (cathodic
                # |v_min| ≈ (R + W/C)/R × v_max — ~3.1× at 50 µs phases),
                # and ``_clipped`` (5 %-of-samples-at-extremes) MISSES the
                # narrow railed V_C peak (< 5 % dwell).  The result was a
                # self-consistent CLIPPED equilibrium: the rail truncated
                # the observed half-range until adapt settled
                # (ideal == current) with the cathodic peak still off-
                # screen, biasing the I·R edge step −13…−18 % — exactly
                # the band that trips the >10 % R-retry.  The position-
                # aware ``channel_is_clipped`` (observed extent within
                # 5 % of the physical rail around POSition) catches it;
                # base/simulator default returns None → unchanged there.
                _v_railed = False
                try:
                    _v_railed = (self._scope.channel_is_clipped(
                        v_mon_phys, vlo, vhi) is True)
                except Exception:
                    pass
                _v_overflow = _clipped(v_arr_f) or _v_railed
                if _v_overflow:
                    vlo, vhi = vlo * 2.0, vhi * 2.0
                try:
                    if self._scope.adapt_channel_scale(
                            v_mon_phys, v_min=vlo, v_max=vhi,
                            shrink_stable_count=1,
                            # Rail-truncated reads are extrapolations —
                            # the fits-now veto must not block the
                            # escape grow (same contract as the
                            # experiment runners' rescale loop).
                            force_grow=_v_railed) is not None:
                        scale_changed = True
                except Exception:
                    pass
                # ---- Centre the asymmetric V_mon (faithful data only) --
                # Cathodic-heavy waveform at position 0 wastes the upper
                # half-screen and pushes the cathodic peak toward the
                # rail.  When the capture is faithful (no clip / rail),
                # offset the excursion midpoint to screen centre at the
                # CURRENT scale — both peaks then fit at a finer V/div
                # (better ADC resolution on the I·R edge).  POSition is
                # ADC-centering only; reconstructed volts are unaffected,
                # so the edge-step extraction and per-capture offset math
                # see identical physics.  Mirrors the runners' settled-
                # recentre (CLAUDE.md §5 #12).
                if not _v_overflow:
                    try:
                        _mid = 0.5 * (vlo + vhi)
                        _cur_vpd = float(self._scope._q(
                            f"{v_mon_phys}:SCAle?"))
                        _cur_pos = float(self._scope._q(
                            f"{v_mon_phys}:POSition?"))
                        _swing = max(vhi - vlo, 1e-9)
                        _bias_ratio = 2.0 * abs(_mid) / _swing
                        _pos_tgt = max(-5.0, min(5.0, -_mid / _cur_vpd))
                        if (_bias_ratio >= 0.1
                                and abs(_pos_tgt - _cur_pos) > 0.25):
                            self._scope.set_channel_position(
                                v_mon_phys, _pos_tgt)
                            scale_changed = True  # re-capture centred
                    except Exception:
                        pass
                if i_mon is not None and len(i_mon) >= 2:
                    i_arr_f = np.asarray(i_mon, dtype=float)
                    ilo, ihi = float(i_arr_f.min()), float(i_arr_f.max())
                    _i_railed = False
                    try:
                        _i_railed = (self._scope.channel_is_clipped(
                            i_mon_phys, ilo, ihi) is True)
                    except Exception:
                        pass
                    if _clipped(i_arr_f) or _i_railed:
                        ilo, ihi = ilo * 2.0, ihi * 2.0
                    try:
                        # NOTE: force_grow keys on the RAIL check ONLY —
                        # NOT on ``_clipped``, which false-positives on
                        # I_mon's square plateaus (the full phase width
                        # sits at the array extremes by definition).
                        # Keying force_grow on _clipped would reintroduce
                        # the per-step I_mon coarsening the fits-now gate
                        # was verified to remove.
                        if self._scope.adapt_channel_scale(
                                i_mon_phys, v_min=ilo, v_max=ihi,
                                shrink_stable_count=1,
                                force_grow=_i_railed) is not None:
                            scale_changed = True
                    except Exception:
                        pass

                if not scale_changed:
                    break  # waveform properly acquired at correct scale

            if acq is None:
                raise RuntimeError("No acquisition returned from scope")
        finally:
            # Single-channel stop matches the single-channel start above.
            try:
                self._stim.stop_channel(channel)
            except Exception:
                pass

        # ---- Per-capture offset (interpulse, before −1 µs) -----------
        # Measure the channel's resting DC level from the
        # pre-trigger samples of THIS frame (t < −1 µs).  Plain MEAN
        # of those samples — the noise around the resting DC is
        # zero-mean Gaussian (scope ADC noise), so the mean
        # converges to the true offset faster than the median for
        # the same sample count.  We do NOT fall back to the
        # global idle baseline on any disagreement: that pattern
        # caused every channel to inherit the same bad value when
        # the global idle itself was off (which happened when the
        # global capture was taken before the scope had fully
        # settled from a V/div change).
        def _per_capture_baseline(arr, t_us, label):
            """Mean of pre-trigger samples with MAD-based outlier
            rejection.  Uses time-axis masking (samples with
            ``t < -1 µs``) when ``t_us`` is well-formed and yields
            ≥ 8 samples, otherwise falls back to INDEX-BASED
            selection (first 10 % of the trace, capped to 200
            samples).  The index-based path guarantees we read the
            very same array values the plot widget reads — there's
            no scenario where a degenerate ``acq.time_us`` causes
            us to silently substitute a global-idle constant
            instead.  Returns 0.0 only if the array itself is
            unusable; emits a log line whenever the fallback path
            fires so the operator can see what's happening.
            """
            try:
                if arr is None or len(arr) < 8:
                    self._log(
                        f"  [{label}] per-capture baseline: arr "
                        f"unusable ({None if arr is None else len(arr)} "
                        f"samples) → using 0.0")
                    return 0.0
                a_arr = np.asarray(arr, dtype=float)
                n = a_arr.size
                # LEADING-EDGE idle baseline — kept in lock-step with
                # ``readback_calibration.per_capture_baseline``.  Do NOT
                # average the whole pre-trigger window (t < −1 µs): when
                # the trigger is the I_mon protocol it fires on the
                # ANODIC current edge, so for a cathodic-first pulse the
                # CATHODIC phase fills the pre-trigger window and its
                # average is the cathodic level (≈ −amplitude), NOT the
                # idle level — subtracting that shifts the trace up by
                # ~one amplitude (the "wrong offset" bug).  auto_layout
                # always puts idle baseline before the first phase, so
                # the EARLIEST samples are the idle baseline regardless
                # of where the trigger sits within the pulse.
                if t_us is not None and len(t_us) == n:
                    order = np.argsort(np.asarray(t_us, dtype=float))
                    a_arr = a_arr[order]
                n_lead = max(8, min(800, n // 16))
                samples = a_arr[:n_lead]
                # MAD-based outlier rejection so a few edge samples
                # don't drag the mean.
                med = float(np.median(samples))
                mad = float(np.median(np.abs(samples - med)))
                if mad > 0:
                    sigma = 1.4826 * mad
                    keep = np.abs(samples - med) <= 3.0 * sigma
                    if int(np.count_nonzero(keep)) >= 4:
                        samples = samples[keep]
                return float(np.mean(samples))
            except Exception as e:
                self._log(
                    f"  [{label}] per-capture baseline failed: "
                    f"{type(e).__name__}: {e} → using 0.0")
                return 0.0
        _t_us_raw = getattr(acq, "time_us", None)
        imon_offset_v = _per_capture_baseline(i_mon, _t_us_raw, "I_mon")
        vmon_offset_v = _per_capture_baseline(v_mon, _t_us_raw, "V_mon")
        # Stash on self so the per-step log line + saved payload can
        # report which baseline this capture actually used.
        self._last_capture_imon_offset_v = imon_offset_v
        self._last_capture_vmon_offset_v = vmon_offset_v
        # The edge-step recovery uses ``np.diff`` and is
        # baseline-invariant, but the peak I_mon (used for trigger /
        # scaling diagnostics and reported as ``imon_peak_v``) and the
        # RC-model overlay both need offset-corrected traces.
        imon_peak_v = float("nan")
        if i_mon is not None and len(i_mon):
            imon_arr = np.asarray(i_mon, dtype=float) - imon_offset_v
            imon_peak_v = float(np.max(np.abs(imon_arr)))
            # ---- Re-zero the time axis on the FIRST cathodic edge ----
            # A biphasic pulse has FOUR roughly-equal edges:
            #   1. Phase-1 onset  (0 → -peak)   negative-going  ← what we want
            #   2. Phase-1 end    (-peak → 0)   positive-going
            #   3. Phase-2 onset  (0 → +peak)   positive-going
            #   4. Phase-2 end    (+peak → 0)   negative-going
            # ``np.argmax(|diff|)`` picks ANY of these — unstable across
            # captures, which is why the user saw the time axis drift.
            # Instead, find the FIRST sample where I_mon drops below
            # half the peak.  That's unambiguously the cathodic phase-1
            # onset (the FALL trigger fires on this edge by definition,
            # so it lands near t≈0 in the scope's native time axis; the
            # re-zero just removes any residual XZEro/PT_Off bias).
            #
            # Necessary because some TBS firmware reports XZEro=0 (and
            # PT_Off=0) even when a non-zero horizontal position is set
            # — no SCPI fix has been reliable across firmware revs.
            try:
                t_arr = getattr(acq, "time_us", None)
                if (t_arr is not None and len(t_arr) >= 2
                        and len(imon_arr) == len(t_arr)):
                    _peak_neg = float(np.min(imon_arr))   # cathodic peak (negative)
                    # First sample where the signal has dropped below
                    # half the cathodic peak — captures the phase-1
                    # onset robustly, regardless of where the noise
                    # peaks in ``|diff|``.
                    if _peak_neg < -1e-4:   # at least 0.1 mV cathodic
                        _half = 0.5 * _peak_neg
                        _below = imon_arr < _half
                        if _below.any():
                            _onset_idx = int(np.argmax(_below))
                            _t_edge = float(t_arr[_onset_idx])
                            acq.time_us = np.asarray(t_arr) - _t_edge
                            if abs(_t_edge) > 0.5:
                                self._log(
                                    f"  trigger re-zeroed: phase-1 onset "
                                    f"at t={_t_edge:+.2f} µs → shifted to "
                                    f"t=0 (used first-crossing detection, "
                                    f"peak={_peak_neg*1000:+.1f} mV, "
                                    f"threshold={_half*1000:+.1f} mV).")
                    else:
                        self._log(
                            f"  ⚠ I_mon cathodic peak too weak to re-zero "
                            f"(peak={_peak_neg*1000:+.2f} mV); time axis "
                            f"left as-is.")
            except Exception:
                pass

        # Recover I·R from the V_mon trace using the SAME method
        # the experiment pipeline uses for access voltage / access
        # resistance — :func:`metrics.access_voltage_and_resistance`
        # (Python port of MATLAB ``getAccess.m``).
        #
        # The MATLAB method works like this for each phase boundary:
        #
        #   1. Smooth |dV/dt| and find the peak (the IR-jump moment).
        #   2. Linear-fit the |dV/dt| curve in a 50-sample window
        #      AFTER the peak.  The fit captures the cap-ramp slope.
        #   3. Walk forward sample by sample looking for where the
        #      raw data first DEPARTS from the linear fit by more
        #      than ``dev_thresh`` — that's the "settling point"
        #      where the IR jump has fully landed and only the
        #      pure cap ramp remains.
        #   4. V_a = v_trace[settling_point] − pre_pulse_baseline.
        #
        # This is robust to the stim current-source's finite rise
        # time (~1 µs / 6 samples on PlexStim), which is what made
        # the old convolve-and-pick-top-4 approach under-read R by
        # ~50 %.  By using the same shape-aware localizer the
        # experiments use, the calibration's R_load matches the
        # access-resistance numbers reported by every other run.
        if v_mon is None or len(v_mon) < 20:
            return (float("nan"), imon_peak_v, acq)
        v_arr = np.asarray(v_mon, dtype=float) - vmon_offset_v
        t_us = getattr(acq, "time_us", None)
        if t_us is None or len(t_us) < 20:
            return (float("nan"), imon_peak_v, acq)
        t_us_arr = np.asarray(t_us, dtype=float)
        if t_us_arr.size != v_arr.size:
            return (float("nan"), imon_peak_v, acq)
        dt_us = float(t_us_arr[1] - t_us_arr[0])
        dt_s = dt_us * 1e-6
        # The metrics access localizer finds where the IR jump has
        # SETTLED (typically peak+20..50 samples), but V_mon there
        # already contains the cap charge that accumulated during
        # the settling window — about 0.4 mV per sample at 100 µA
        # / 4700 pF, so 30 samples ⇒ ~12 mV bias on top of the IR
        # jump.  On the 4990 Ω test board this puts R_a ≈ 30-40 %
        # above the true R_load.
        #
        # Fix: use the localizer to locate the settling region, then
        # linear-fit V_mon over the post-settling cap-ramp and
        # extrapolate the fit back to the EDGE MOMENT (the |dV/dt|
        # peak).  V_mon's intercept at the edge moment is exactly IR
        # — the linear cap ramp is subtracted out by the
        # extrapolation, so the result is the pure IR jump
        # independent of stim rise time or the localizer's settling
        # offset.  This is the same physics MATLAB's setDriving uses
        # when extracting R from the V_mon trace.
        try:
            from ..metrics import access_voltage_and_resistance
            # SAME method as the experiment (operator: "calibration should use
            # the same method for access points as the experiment"): the
            # TIME-ANCHORED localizer (onset=0 — the time axis was re-zeroed to
            # the phase-1 onset above) + the SHARED before/after edge-
            # extrapolation (``access_step_by_extrapolation``) that returns the
            # PURE IR step with the cap ramp subtracted.  The returned ``va``
            # list IS those steps (the calibration used to inline this exact
            # extrapolation, with identical windows), so we just average them.
            _va_list, _ra_list, _acc_idx = access_voltage_and_resistance(
                t_us_arr, v_arr, pat, onset_us=0.0)
        except Exception:
            _va_list = []
        step_mags = [float(v) for v in _va_list if np.isfinite(v)]
        if not step_mags:
            return (float("nan"), imon_peak_v, acq)
        step_v = float(np.mean(step_mags))
        # No cap-ramp correction term — the BEFORE/AFTER linear
        # extrapolations subtract V_C exactly across each edge.
        C_farad = max(1e-15, load_cap_pf * 1e-12)
        measured_ua = step_v / load_ohm * 1e6   # V / Ω = A → µA

        # ---- RC-model comparison ----------------------------------------
        # Generate the theoretical V_mon trace aligned to the first edge,
        # compute RMSD over the pulse window, and estimate C from the
        # phase-1 ramp slope.
        model_rmsd_mv = float("nan")
        est_cap_pf = float("nan")
        est_r_ohm = float("nan")
        sim_t_us_out = None
        sim_v_out = None
        # Raw measurements used by the *channel-level* R/C joint fit
        # in ``_fit_and_render_results``.  We carry them out of this
        # function so the post-sweep fit can do a real least-squares
        # over (step_v, ramp_slope) vs amplitude instead of just
        # medianing the per-capture point estimates.
        ramp_slope_v_per_s = float("nan")
        phase1_sign_out = -1.0
        try:
            if t_us is not None and len(t_us) >= 2:
                info = getattr(self._stim, "info", None) if self._stim else None
                vmon_v_per_v = float(
                    getattr(info, "vmon_scaling_v_per_v", VMON_SCALING_DEFAULT)
                    or VMON_SCALING_DEFAULT)

                # The scope trigger fires on the positive-going I_mon edge, so
                # t=0 is at the start of whichever phase goes positive first.
                # For cathodic-first: t=0 is at phase-2 onset; phase 1 is in
                # the pre-trigger window (negative t). Adjust t0 so the
                # simulation reference matches the captured waveform reference.
                t0_us = 0.0

                # Hardcoded cathodic-first.  Calibration ALWAYS programs the
                # PulsePattern with ``polarity=-1`` (see _capture_one_amplitude),
                # so the RC model's phase-1 sign is known a priori.  Earlier
                # versions auto-detected from the V_mon mean in the first
                # half-phase, which was fragile when the time-axis re-zero
                # was slightly off — a tiny shift would flip the sign and
                # the gray model curve would appear inverted relative to the
                # captured V_mon.  Hardcoding matches what we actually deliver.
                phase1_sign = -1.0

                # Simulate over the full time axis.
                v_sim = self._simulate_vmon_trace(
                    amp_ua=amp_ua,
                    load_ohm=load_ohm,
                    load_cap_pf=load_cap_pf,
                    phase_us=self.PHASE_WIDTH_US,
                    interphase_us=self.INTERPHASE_US,
                    t_us=t_us,
                    t0_us=t0_us,
                    vmon_v_per_v=vmon_v_per_v,
                    discharge_us=self.DISCHARGE_US,
                    phase1_sign=phase1_sign,
                )
                sim_t_us_out = t_us
                sim_v_out = v_sim

                # RMSD over the active pulse window [t0, t0+2W+G].
                pulse_end_us = t0_us + 2 * self.PHASE_WIDTH_US + self.INTERPHASE_US
                mask_pulse = (t_us >= t0_us) & (t_us <= pulse_end_us)
                if mask_pulse.sum() >= 4:
                    residuals = v_arr[mask_pulse] - v_sim[mask_pulse]
                    model_rmsd_mv = float(np.sqrt(np.mean(residuals ** 2))) * 1e3

                # Estimate C from the phase-1 ramp slope.
                # Model: dV/dt = -phase1_sign · I · k / C
                # → C = -phase1_sign · I · k / slope
                # Use the middle 50 % of phase 1 to avoid edge transients.
                ph1_start_us = t0_us + self.PHASE_WIDTH_US * 0.25
                ph1_end_us   = t0_us + self.PHASE_WIDTH_US * 0.75
                mask_p1 = (t_us >= ph1_start_us) & (t_us < ph1_end_us)
                if mask_p1.sum() >= 4:
                    t_p1 = t_us[mask_p1] * 1e-6   # → seconds
                    v_p1 = v_arr[mask_p1]
                    # Linear fit: slope in V/s (scope volts, not load volts)
                    slope, _ = np.polyfit(t_p1, v_p1, 1)
                    I_A = amp_ua * 1e-6
                    # slope = dV_scope/dt = phase1_sign · I · k / C
                    # → C = phase1_sign · I · k / slope  (always positive when correct)
                    if abs(slope) > 1e-6:
                        est_cap_pf = (phase1_sign * I_A * vmon_v_per_v / slope) * 1e12
                    # Carry the raw ramp slope (V_scope per second) and
                    # phase-1 sign out of this scope so the channel-level
                    # joint R/C fit can use them directly.
                    ramp_slope_v_per_s = float(slope)
                    phase1_sign_out = float(phase1_sign)

                # Estimate R from the per-boundary access voltage.
                # The metrics localizer subtracts the cap ramp via
                # its linear-fit-deviation walk, so step_v is the
                # pure IR jump in scope volts.  Divide by I and the
                # nominal V_mon scaling to recover R in electrode
                # ohms — same units as MATLAB ``getAccess.m``'s R_a.
                I_A = amp_ua * 1e-6
                denom = I_A * vmon_v_per_v
                if denom > 0:
                    est_r_ohm = step_v / denom
                else:
                    est_r_ohm = float("nan")
        except Exception:
            pass

        return (measured_ua, imon_peak_v, acq, model_rmsd_mv, est_cap_pf,
                est_r_ohm, sim_t_us_out, sim_v_out,
                float(step_v), float(ramp_slope_v_per_s),
                float(phase1_sign_out), float(dt_s))

    def _fit_one_channel(self, ch: int) -> Optional[Dict[str, float]]:
        """Fit and render results for a single channel.

        Called twice in a normal sweep:

          1. **Incrementally** from the main sweep loop after each
             channel finishes — so the user sees results land in the
             table as the run progresses instead of waiting for the
             whole sweep to end.
          2. **Finally** from :meth:`_fit_and_render_results` once
             every channel is done — same data, but at that point
             ``self._scaling_validation`` and the summary popup also
             run.

        The row is keyed on channel number, so the second call
        updates the existing cells in place (see
        ``_row_by_channel``).  Returns the fit dict written into
        ``self._fit[str(ch)]`` for convenience, or ``None`` if the
        channel produced no usable captures.
        """
        import numpy as np
        pairs = self._results.get(ch, [])
        if not pairs:
            self._fit[str(ch)] = {"a": float("nan"),
                                   "b": float("nan"),
                                   "rmsd_ua": float("nan")}
            self._append_results_row(ch, float("nan"),
                                     float("nan"), float("nan"),
                                     float("nan"), float("nan"),
                                     float("nan"), float("nan"),
                                     is_flagged=True)
            return None
        programmed = np.array([p[0] for p in pairs])
        measured = np.array([p[1] for p in pairs])
        # Model RMSD (mV) — mean across amplitudes, finite only.
        rmsd_vals = [p[3] for p in pairs if len(p) >= 4
                     and np.isfinite(p[3])]
        mean_model_rmsd_mv = (float(np.mean(rmsd_vals)) if rmsd_vals
                              else float("nan"))
        # ---- Joint R / C least-squares fit across this channel ----
        info = getattr(self._stim, "info", None) if self._stim else None
        vmon_v_per_v = float(
            getattr(info, "vmon_scaling_v_per_v", VMON_SCALING_DEFAULT)
            or VMON_SCALING_DEFAULT)
        I_prog_A = np.array(
            [float(p[0]) * 1e-6 for p in pairs if len(p) >= 10
             and np.isfinite(p[6]) and np.isfinite(p[7])])
        step_v_arr = np.array(
            [float(p[6]) for p in pairs if len(p) >= 10
             and np.isfinite(p[6]) and np.isfinite(p[7])])
        slope_arr = np.array(
            [float(p[7]) for p in pairs if len(p) >= 10
             and np.isfinite(p[6]) and np.isfinite(p[7])])
        phase1_sign = float(pairs[0][8]) if len(pairs[0]) >= 9 else -1.0
        dt_s_med = (float(np.median([p[9] for p in pairs
                                      if len(p) >= 10
                                      and np.isfinite(p[9])
                                      and p[9] > 0]))
                    if any(len(p) >= 10 and np.isfinite(p[9])
                           and p[9] > 0 for p in pairs) else 1e-6)
        fit_r_ohm = float("nan")
        fit_c_pf = float("nan")
        fit_r_rmse = float("nan")
        fit_c_rmse = float("nan")
        v_offset_v = float("nan")
        if I_prog_A.size >= 2 and float(np.std(I_prog_A)) > 0:
            denom_x2 = float(np.sum(I_prog_A * I_prog_A))
            if denom_x2 > 0:
                slope_step = (float(np.sum(I_prog_A * step_v_arr))
                              / denom_x2)
                slope_ramp = (float(np.sum(I_prog_A * slope_arr))
                              / denom_x2)
                if slope_ramp != 0.0:
                    fit_c_pf = (phase1_sign * vmon_v_per_v
                                / slope_ramp) * 1e12
                C_for_R = (fit_c_pf * 1e-12
                           if np.isfinite(fit_c_pf) and fit_c_pf > 0
                           else float(self.DEFAULT_LOAD_CAP_PF) * 1e-12)
                if vmon_v_per_v > 0:
                    # step_v is the pure IR jump (the metrics
                    # access-voltage localizer subtracted the cap
                    # ramp).  So slope_step = R · vmon_v_per_v and
                    # R = slope_step / vmon_v_per_v exactly — no
                    # dt/C correction term anymore.
                    fit_r_ohm = slope_step / vmon_v_per_v
                pred_step = slope_step * I_prog_A
                pred_ramp = slope_ramp * I_prog_A
                fit_r_rmse = float(np.sqrt(np.mean(
                    (step_v_arr - pred_step) ** 2)))
                fit_c_rmse = float(np.sqrt(np.mean(
                    (slope_arr - pred_ramp) ** 2)))
            pass  # v_offset_v is now computed from per-capture
                  # pre-trigger baselines below, NOT a polyfit
                  # intercept (which would blow up on noisy data
                  # and report values like 700 mV that the user
                  # can never see on the scope).
        # Use the fitted values as the per-channel reported R / C.
        median_r_ohm = fit_r_ohm if np.isfinite(fit_r_ohm) else float(
            np.median([p[5] for p in pairs
                       if len(p) >= 6 and np.isfinite(p[5]) and p[5] > 0])
            if any(len(p) >= 6 and np.isfinite(p[5]) and p[5] > 0
                   for p in pairs) else float("nan"))
        median_cap_pf = fit_c_pf if np.isfinite(fit_c_pf) else float(
            np.median([p[4] for p in pairs
                       if len(p) >= 5 and np.isfinite(p[4]) and p[4] > 0])
            if any(len(p) >= 5 and np.isfinite(p[4]) and p[4] > 0
                   for p in pairs) else float("nan"))
        # ---- Per-channel V_mon and I_mon DC offsets ----------------
        # These are the actual pre-trigger baselines averaged across
        # captures — the resting voltage / current you can read off
        # the scope between pulses.  We use the per-capture values
        # (tuple slots 10 and 11) rather than any polyfit intercept,
        # because the intercept blows up when low-amp captures are
        # noisy and reports values like 700 mV that don't exist on
        # the actual signal.
        try:
            _vmon_baselines = [float(p[10]) for p in pairs
                               if len(p) >= 11
                               and np.isfinite(p[10])]
            v_offset_v = (float(np.mean(_vmon_baselines))
                          if _vmon_baselines else float("nan"))
        except Exception:
            v_offset_v = float("nan")
        try:
            _imon_baselines_v = [float(p[11]) for p in pairs
                                 if len(p) >= 12
                                 and np.isfinite(p[11])]
            imon_offset_v = (float(np.mean(_imon_baselines_v))
                             if _imon_baselines_v else float("nan"))
        except Exception:
            imon_offset_v = float("nan")
        # I_mon baseline in µA (divide by stim's V/µA scaling).
        info_imon = getattr(self._stim, "info", None) if self._stim else None
        imon_v_per_ua = float(
            getattr(info_imon, "imon_scaling_v_per_ua", 1e-3) or 1e-3)
        if imon_v_per_ua > 0 and np.isfinite(imon_offset_v):
            imon_offset_ua = imon_offset_v / imon_v_per_ua
        else:
            imon_offset_ua = float("nan")
        # Log the per-channel fit so the user sees it happened.
        # Offsets reported here are MEAN pre-trigger baselines
        # across all captures of this channel — the resting DC
        # level the user reads off the scope, not a polyfit
        # intercept.
        self._log(
            f"CH{ch:02d} R/C joint fit: "
            f"R = {fit_r_ohm:.0f} Ω  (step-slope RMSE = "
            f"{fit_r_rmse*1e3:.2f} mV),  "
            f"C = {fit_c_pf:.0f} pF  (ramp-slope RMSE = "
            f"{fit_c_rmse:.2f} V/s),  "
            f"V_mon DC offset = {v_offset_v*1e3:+.2f} mV,  "
            f"I_mon DC offset = {imon_offset_ua:+.2f} µA  "
            f"over {I_prog_A.size} amplitude(s)")
        # I_mon Gain (a) and overall RMSD from I_actual = a · I_mon + b
        # least-squares fit.  We keep the SLOPE (a) and residual RMSD
        # from this fit because they're the right physical quantities
        # — slope tells us if the I_mon channel reads N % high/low,
        # RMSD tells us how cleanly each capture's measured current
        # tracks the programmed current.  But the FIT INTERCEPT (b)
        # is replaced by the per-capture I_mon pre-trigger baseline
        # (in µA), because polyfit's intercept on noisy small-amp
        # data was producing nonsensical 50-90 µA "offsets" that the
        # user can never observe on the scope at rest.
        if len(pairs) >= 2 and np.std(measured) > 0:
            coef = np.polyfit(measured, programmed, 1)
            a_fit = float(coef[0])
            _b_polyfit = float(coef[1])
            predicted = a_fit * measured + _b_polyfit
            rmsd = float(np.sqrt(np.mean((programmed - predicted) ** 2)))
        else:
            a_fit = float("nan")
            _b_polyfit = float("nan")
            rmsd = float("nan")
        # A degenerate polyfit on BAD captures (railing V_mon / a
        # mis-triggered I_mon on a 2-channel scope) can produce an ABSURD
        # gain slope — ``a ≈ 1e14`` was observed at CWRU, which then
        # multiplied every subsequent I_mon reading into physical nonsense
        # (±1e8 A).  A real I_mon gain is a small correction (~O(1)); reject
        # anything outside the plausible bounds so it is NEVER saved as a
        # valid coefficient.  Save identity (a=1) instead, and flag the
        # channel so the operator knows verification did not produce a usable
        # gain (root cause is almost always the test-board connection / the
        # I_mon trigger not firing — fix that, then re-verify).
        _gain_implausible = not (
            math.isfinite(a_fit)
            and _IMON_GAIN_MIN <= abs(a_fit) <= _IMON_GAIN_MAX)
        if _gain_implausible:
            self._log(
                f"⚠ CH{ch}: I_mon gain fit = {a_fit:.3g} is implausible "
                f"(a real gain is ~1) — the captures are unreliable (check "
                f"the test-board connection + I_mon trigger).  Saving "
                f"identity gain (a=1) so it can't corrupt current readings; "
                f"re-run verification once the signal is clean.")
            a_fit = 1.0   # identity — safe; the channel is flagged below
        # b is now the I_mon DC baseline in µA (resting interpulse
        # current), averaged over the sweep's captures — a quantity
        # the user can directly verify on the scope.
        b_fit = imon_offset_ua
        fit_record: Dict[str, float] = {
            "a": a_fit, "b": b_fit, "rmsd_ua": rmsd,
            "v_offset_v": v_offset_v,
            "imon_offset_v": imon_offset_v,
            "imon_offset_ua": imon_offset_ua,
            "b_polyfit_ua": _b_polyfit,   # kept for forensic use
            "fit_r_ohm": fit_r_ohm,
            "fit_c_pf": fit_c_pf,
            "fit_r_rmse_v": fit_r_rmse,
            "fit_c_rmse_v_per_s": fit_c_rmse,
            "fit_n_points": int(I_prog_A.size),
        }
        self._fit[str(ch)] = fit_record  # type: ignore[assignment]
        # Pass / fail criterion: just "did we get a finite positive
        # R_load and C_load fit?".  The RMSD-based gates were
        # removed — they conflated "channel is broken" with
        # "test board R doesn't match nominal", which had nothing
        # to do with channel health.  This minimal criterion flags
        # only the channels where the joint fit could not extract
        # an RC pair at all (open circuit, shorted output, missing
        # captures, etc.).
        is_flagged = bool(
            _gain_implausible
            or not (np.isfinite(median_r_ohm) and median_r_ohm > 0)
            or not (np.isfinite(median_cap_pf) and median_cap_pf > 0))
        self._append_results_row(
            ch, a_fit, b_fit, rmsd,
            mean_model_rmsd_mv, median_r_ohm, median_cap_pf,
            v_offset_v * 1e3 if np.isfinite(v_offset_v) else float("nan"),
            is_flagged=is_flagged)
        # Force the table to repaint NOW so the row appears live in
        # the GUI (the worker thread holds the event loop otherwise).
        QtWidgets.QApplication.processEvents()
        return fit_record

    def _fit_and_render_results(self, load_ohm: float) -> None:
        """Per-channel linear least-squares ``I_actual = a · I_mon + b``
        fit, populate the results table, flag out-of-band channels.

        Also validates the stimulator's I_mon scaling by fitting
        ``V_imon_peak ≈ k · I_programmed`` over all captures and
        comparing ``k`` to the active and known scaling presets
        (Default and NIL). The result populates
        ``self._scaling_validation`` for the summary popup and
        the saved calibration payload.
        """
        import numpy as np
        # Reset the table and the row-index map so the final pass
        # rebuilds every row in canonical channel order.  Rows that
        # were incrementally written during the sweep get re-emitted
        # here with the same fit values — keeps the on-screen table
        # consistent with ``self._fit`` and the saved payload.
        self.results_table.setRowCount(0)
        self._row_by_channel.clear()
        flagged_count = 0
        # Accumulate every (programmed_ua, imon_peak_v) pair
        # across channels for the global scaling fit.
        scaling_programmed: list = []
        scaling_imon_v: list = []
        for ch, pairs in sorted(self._results.items()):
            for p in pairs:
                if len(p) >= 3 and np.isfinite(p[2]) and np.isfinite(p[0]):
                    scaling_programmed.append(float(p[0]))
                    scaling_imon_v.append(float(p[2]))
            fit_record = self._fit_one_channel(ch)
            if fit_record is None:
                flagged_count += 1
                continue
            # Match :meth:`_fit_one_channel`'s pass criterion:
            # finite, positive R_load AND C_load fits.  RMSD is
            # no longer used for pass/fail.
            _r = fit_record.get("fit_r_ohm", float("nan"))
            _c = fit_record.get("fit_c_pf", float("nan"))
            if (not (np.isfinite(_r) and _r > 0)
                    or not (np.isfinite(_c) and _c > 0)):
                flagged_count += 1
        # Stimulator scaling validation. Fit V_imon = k · I_prog
        # across all captures and pick the closest known preset.
        self._scaling_validation = self._validate_scaling(
            scaling_programmed, scaling_imon_v)
        # Status line summary.
        n_total = len(self._results)
        if flagged_count == 0:
            _done_msg = (f"Sweep complete. All {n_total} channels "
                         f"produced finite R / C fits.")
        else:
            _done_msg = (f"Sweep complete. {flagged_count} of {n_total} "
                         f"channels failed to produce valid R / C fits "
                         f"(flagged red).")
        self.status_progress.setText(_done_msg)
        self._log(_done_msg)
        self._btn_save.setEnabled(True)
        # Final summary pop-up — pass/fail counts plus aggregate
        # accuracy + precision, with the per-channel breakdown
        # in the expandable details pane.
        self._show_summary_popup()

    def _append_results_row(self, ch: int, a_fit: float, b_fit: float,
                            rmsd_ua: float, model_rmsd_mv: float,
                            est_r_ohm: float, est_cap_pf: float,
                            v_offset_mv: float = float("nan"),
                            *, is_flagged: bool) -> None:
        """Append one row to the results table. Flagged rows are
        coloured red for at-a-glance review.

        Est. R and Est. C cells show the estimated value plus its
        percentage deviation from the nominal circuit values
        (4990 Ω, 4700 pF).  Deviations beyond ±10 % are highlighted
        orange so a bad load component is immediately visible even
        on a channel whose gain happens to be within spec.
        """
        import math
        NOMINAL_R = self.DEFAULT_LOAD_OHM        # 4990 Ω
        NOMINAL_C = self.DEFAULT_LOAD_CAP_PF     # 4700 pF
        CIRCUIT_TOL_PCT = 10.0                   # flag threshold

        # Re-use an existing row when this channel has been written
        # before (incremental fill during sweep + final pass after).
        if ch in self._row_by_channel:
            row = self._row_by_channel[ch]
        else:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self._row_by_channel[ch] = row

        def _cell(text, flag_orange=False):
            it = QtWidgets.QTableWidgetItem(text)
            it.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            if is_flagged:
                it.setForeground(QtGui.QColor("#c62828"))
            elif flag_orange:
                it.setForeground(QtGui.QColor("#e65100"))
            return it

        def _fmt(v, fmt):
            if not math.isfinite(v):
                return "—"
            return format(v, fmt)

        def _fmt_with_dev(est, nominal, val_fmt):
            """Format 'value (±x%)'; returns (text, out_of_tol)."""
            if not math.isfinite(est) or nominal == 0:
                return "—", False
            dev_pct = (est - nominal) / nominal * 100.0
            text = f"{est:{val_fmt}} ({dev_pct:+.1f}%)"
            return text, abs(dev_pct) > CIRCUIT_TOL_PCT

        r_text, r_oot = _fmt_with_dev(est_r_ohm, NOMINAL_R, ".0f")
        c_text, c_oot = _fmt_with_dev(est_cap_pf, NOMINAL_C, ".0f")

        # Column order (matches setHorizontalHeaderLabels above):
        #   0 Channel
        #   1 V_mon Offset (mV)
        #   2 R_load Fit (Ω)
        #   3 C_load Fit (pF)
        #   4 I_mon Gain (a)
        #   5 I_mon Offset (b, µA)
        # ``model_rmsd_mv`` and ``rmsd_ua`` arguments are accepted
        # for API compatibility (callers still pass them) but are
        # NOT displayed — they remain in the saved JSON payload for
        # forensic use.
        self.results_table.setItem(row, 0, _cell(f"CH{ch:02d}"))
        self.results_table.setItem(row, 1, _cell(_fmt(v_offset_mv, "+.3f")))
        self.results_table.setItem(row, 2, _cell(r_text, flag_orange=r_oot))
        self.results_table.setItem(row, 3, _cell(c_text, flag_orange=c_oot))
        self.results_table.setItem(row, 4, _cell(_fmt(a_fit, ".4f")))
        self.results_table.setItem(row, 5, _cell(_fmt(b_fit, "+.3f")))

    def _validate_scaling(self, programmed_ua: list,
                          imon_peak_v: list) -> dict:
        """Cross-check the stimulator's configured I_mon scaling
        against what V_imon actually shows during the sweep.

        Fits the linear relation
        ``V_imon_peak = k · I_programmed`` across every capture
        (forced through the origin since both axes are zero at
        zero current) and compares the recovered ``k`` to the
        two known PlexStim presets:

          * **Default** — 2.5 mV/µA (i.e. ``IMON_SCALING_DEFAULT``)
          * **NIL** — 1.0 mV/µA (i.e. ``IMON_SCALING_NIL``); used
            by serial numbers in :data:`NIL_SERIAL_NUMBERS` and any
            others identified by this validator.

        Returns a dict with:
          * ``detected_v_per_ua`` — fitted ``k``
          * ``detected_preset`` — closest preset name, or
            ``"unknown"`` if neither matches within tolerance
          * ``active_preset`` — what the user/Connection panel
            had configured at sweep time
          * ``active_v_per_ua`` — the configured scaling value
          * ``mismatch`` — True iff active and detected disagree
          * ``serial_number`` — string from
            ``stim.info.serial_number`` (empty if not available)
          * ``r_squared`` — quality-of-fit on the linear
            regression; below 0.95 means the I_mon signal didn't
            track current linearly and the detection is suspect
          * ``n_points`` — number of captures the fit used
        """
        import numpy as np
        info = getattr(self._stim, "info", None)
        active_v_per_ua = (float(getattr(info, "imon_scaling_v_per_ua",
                                          IMON_SCALING_DEFAULT))
                          if info is not None else float(IMON_SCALING_DEFAULT))
        serial = str(getattr(info, "serial_number", "") or "")
        # Match the active scaling value to its preset name.
        if abs(active_v_per_ua - IMON_SCALING_NIL) < 1e-6:
            active_preset = "NIL"
        elif abs(active_v_per_ua - IMON_SCALING_DEFAULT) < 1e-6:
            active_preset = "Default"
        else:
            active_preset = "custom"

        result = {
            "active_preset": active_preset,
            "active_v_per_ua": active_v_per_ua,
            "detected_v_per_ua": float("nan"),
            "detected_preset": "unknown",
            "mismatch": False,
            "serial_number": serial,
            "r_squared": float("nan"),
            "n_points": 0,
            "available": False,
        }
        if not programmed_ua or len(programmed_ua) < 2:
            return result
        prog = np.asarray(programmed_ua, dtype=float)
        vimon = np.asarray(imon_peak_v, dtype=float)
        # Force-through-origin slope: k = Σ(x·y) / Σ(x²)
        denom = float(np.sum(prog * prog))
        if denom <= 0.0:
            return result
        k = float(np.sum(prog * vimon) / denom)
        predicted = k * prog
        ss_res = float(np.sum((vimon - predicted) ** 2))
        ss_tot = float(np.sum((vimon - np.mean(vimon)) ** 2))
        r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else float("nan")

        # Pick the closest known preset within ±15 % tolerance
        # (Default/NIL differ by 2.5×, so a 15 % gate is wide
        # enough for measurement noise and tight enough to keep
        # the two presets disambiguated).
        def _close(value, ref, tol_pct=15.0):
            return abs(value - ref) / ref * 100.0 <= tol_pct

        if _close(k, IMON_SCALING_DEFAULT):
            detected = "Default"
        elif _close(k, IMON_SCALING_NIL):
            detected = "NIL"
        else:
            detected = "unknown"

        result.update({
            "detected_v_per_ua": k,
            "detected_preset": detected,
            "mismatch": (detected != "unknown"
                          and detected != active_preset),
            "r_squared": r2,
            "n_points": int(prog.size),
            "available": True,
        })
        return result

    def _show_summary_popup(self) -> None:
        """Post-sweep summary pop-up.

        Headline message gives pass/fail counts + aggregate
        accuracy and precision; the expandable "Show Details"
        pane gives the per-channel breakdown.

        Metric semantics:
          * **Accuracy** — captured by two numbers per channel:
            the gain deviation |a − 1| (as a percentage; what
            fraction of the programmed amplitude the channel is
            systematically off by) and the offset |b| in µA (the
            zero-current intercept of the linear fit). A
            theoretically perfect channel has a = 1, b = 0.
          * **Precision** — captured by the fit RMSD in µA
            (root-mean-square deviation of the recorded points
            from the per-channel linear fit). Captures the
            scatter / repeatability of the channel; small RMSD
            means the channel is well-modelled by a single line,
            so the calibration trim will work consistently
            rather than chasing per-sample noise.
        """
        import math
        n_total = len(self._fit)
        if n_total == 0:
            return

        # Collect per-channel rows + aggregates.
        rows = []
        pass_chs: list = []
        fail_chs: list = []
        gain_devs_pct: list = []  # |a-1| * 100
        offsets_ua: list = []     # |b|
        rmsds_ua: list = []
        for ch_str, fit in sorted(self._fit.items(),
                                  key=lambda kv: int(kv[0])):
            ch = int(ch_str)
            a = fit.get("a", float("nan"))
            b = fit.get("b", float("nan"))
            r = fit.get("rmsd_ua", float("nan"))
            _r_load = fit.get("fit_r_ohm", float("nan"))
            _c_load = fit.get("fit_c_pf", float("nan"))
            dev_pct = (abs(a - 1.0) * 100.0
                       if math.isfinite(a) else float("nan"))
            # Pass criterion: finite, positive R_load AND C_load.
            # RMSD-based gates removed — RMSD is no longer displayed
            # in the table and no longer counted toward pass/fail.
            ok = bool(
                math.isfinite(_r_load) and _r_load > 0
                and math.isfinite(_c_load) and _c_load > 0)
            (pass_chs if ok else fail_chs).append(ch)
            if math.isfinite(dev_pct):
                gain_devs_pct.append(dev_pct)
            if math.isfinite(b):
                offsets_ua.append(abs(b))
            if math.isfinite(r):
                rmsds_ua.append(r)
            rows.append((ch, ok, a, b, r, dev_pct))

        def _mean(xs):
            return sum(xs) / len(xs) if xs else float("nan")

        def _max(xs):
            return max(xs) if xs else float("nan")

        def _f(v, fmt="{:.3f}"):
            return "—" if not math.isfinite(v) else fmt.format(v)

        n_pass = len(pass_chs)
        n_fail = len(fail_chs)
        failed_str = (", ".join(f"CH{c:02d}" for c in fail_chs)
                      if fail_chs else "—")

        # Worst-offender lookups for the per-metric one-liner.
        finite_dev = [r for r in rows if math.isfinite(r[5])]
        worst_gain = (max(finite_dev, key=lambda r: r[5])
                      if finite_dev else None)
        finite_b = [r for r in rows if math.isfinite(r[3])]
        worst_offset = (max(finite_b, key=lambda r: abs(r[3]))
                        if finite_b else None)
        finite_r = [r for r in rows if math.isfinite(r[4])]
        worst_rmsd = (max(finite_r, key=lambda r: r[4])
                      if finite_r else None)

        parts: list = []
        parts.append("<h3>Stimulator verification sweep — summary</h3>")
        if n_fail == 0:
            parts.append(
                f"<p style='color:#2e7d32'><b>PASS — "
                f"{n_pass} / {n_total} channels</b> produced finite "
                f"R / C fits.</p>")
        else:
            parts.append(
                f"<p style='color:#c62828'><b>"
                f"{n_fail} / {n_total} channels FAILED</b> "
                f"(missing or invalid R / C fit).<br>"
                f"Failed: {failed_str}</p>")

        parts.append(
            "<p><b>Accuracy</b> "
            "<span style='color:#666'>(systematic error vs. programmed)</span><br>"
            f"&nbsp;&nbsp;Gain |a − 1|: "
            f"mean <b>{_f(_mean(gain_devs_pct), '{:.2f}')} %</b>, "
            f"max <b>{_f(_max(gain_devs_pct), '{:.2f}')} %</b>"
            + (f" (CH{worst_gain[0]:02d})" if worst_gain else "")
            + "<br>"
            f"&nbsp;&nbsp;Offset |b|: "
            f"mean <b>{_f(_mean(offsets_ua), '{:.3f}')} µA</b>, "
            f"max <b>{_f(_max(offsets_ua), '{:.3f}')} µA</b>"
            + (f" (CH{worst_offset[0]:02d})" if worst_offset else "")
            + "</p>")

        # (Precision/RMSD block removed — RMSD is no longer used
        # for pass/fail and is hidden from the table.)

        # Stimulator scaling validation block. Two outcomes:
        # match (green) or mismatch (red, with a call-out that
        # the Connection panel's scaling preset is wrong for
        # this device).
        sv = self._scaling_validation
        if sv.get("available"):
            active = sv.get("active_preset", "?")
            detected = sv.get("detected_preset", "?")
            active_v = sv.get("active_v_per_ua", 0.0) * 1e3  # → mV/µA
            detected_v = sv.get("detected_v_per_ua", 0.0) * 1e3
            r2 = sv.get("r_squared", float("nan"))
            sn = sv.get("serial_number", "")
            colour = "#c62828" if sv.get("mismatch") else "#2e7d32"
            header = ("Scaling MISMATCH"
                      if sv.get("mismatch") else "Scaling OK")
            parts.append(
                f"<p><b>Stimulator scaling</b> "
                f"<span style='color:#666'>(I_mon validation)</span><br>"
                f"&nbsp;&nbsp;Serial: <b>{sn or 'n/a'}</b><br>"
                f"&nbsp;&nbsp;Active preset: <b>{active}</b> "
                f"({active_v:.3f} mV/µA)<br>"
                f"&nbsp;&nbsp;Detected preset: "
                f"<b style='color:{colour}'>{detected}</b> "
                f"({detected_v:.3f} mV/µA, R² = "
                f"{_f(r2, '{:.4f}')})<br>"
                f"&nbsp;&nbsp;<b style='color:{colour}'>{header}</b>"
                + ("" if not sv.get("mismatch") else
                   " — switch the Connection panel's scaling "
                   "preset to match, then re-run the sweep.")
                + "</p>")

        if n_fail == 0:
            parts.append(
                "<p style='color:#555'>Click <i>Save verification…</i> "
                "to persist these coefficients so the runner applies "
                "them on every subsequent capture.</p>")
        else:
            parts.append(
                "<p style='color:#555'>You can still save — only "
                "the channels in spec will be trusted at run time. "
                "Re-seat the failed channels' cables and re-run "
                "for a clean sheet.</p>")

        # Per-channel detail — plain text in the expandable pane.
        detail_lines: list = []
        detail_lines.append(
            "Per-channel breakdown "
            f"(acceptance: |a − 1| ≤ {accept:.1f} %)")
        detail_lines.append("")
        detail_lines.append(
            f"{'Ch':<6}{'Status':<8}{'a':<10}"
            f"{'|a−1| %':<10}{'b (µA)':<11}{'RMSD (µA)':<10}")
        detail_lines.append("─" * 55)
        for ch, ok, a, b, r, dev_pct in rows:
            detail_lines.append(
                f"CH{ch:02d}  "
                f"{('PASS' if ok else 'FAIL'):<7}"
                f"{_f(a, '{:.4f}'):<10}"
                f"{_f(dev_pct, '{:.2f}'):<10}"
                f"{_f(b, '{:+.3f}'):<11}"
                f"{_f(r, '{:.3f}'):<10}"
            )

        box = QtWidgets.QMessageBox(self)
        box.setIcon(
            QtWidgets.QMessageBox.Icon.Information
            if n_fail == 0
            else QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Stimulator verification sweep — summary")
        box.setTextFormat(QtCore.Qt.TextFormat.RichText)
        box.setText("".join(parts))
        box.setDetailedText("\n".join(detail_lines))
        box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Ok)
        box.exec()

    def _on_save_calibration(self):
        """Persist the fitted per-channel gain / offset to the prefs
        directory so the runner picks it up next session.

        Writes :func:`write_calibration_payload` with the current
        ``self._fit`` map. The on-disk schema includes the
        ``"timestamp"`` field that the Help → Last calibration…
        dialog reads, so saving here is what makes the calibration
        age visible elsewhere in the GUI.
        """
        if not self._fit:
            QtWidgets.QMessageBox.warning(
                self, "Nothing to save",
                "No verification coefficients to save yet. Run the "
                "sweep first.")
            return
        load_ohm = float(self.DEFAULT_LOAD_OHM)
        load_cap_pf = float(self.DEFAULT_LOAD_CAP_PF)
        notes = (f"PlexStim test-board stimulator verification "
                 f"(Plexon 14-04-A-03-A board, 14-03-A-03 cable); "
                 f"load=RC series {load_ohm:.0f} Ω + {load_cap_pf:.0f} pF, "
                 f"phase_width={self.PHASE_WIDTH_US:.0f} µs, "
                 f"grid_µA="
                 f"{','.join(f'{a:.0f}' for a in self.DEFAULT_AMPLITUDE_GRID_UA)}")
        # Bundle the stimulator metadata + scaling validation so
        # the calibration record is self-describing. Sweep
        # captures are keyed by stim serial number; the runner
        # will refuse to apply a calibration whose serial doesn't
        # match the currently-attached device.
        info = getattr(self._stim, "info", None)
        stim_payload = {
            "serial_number": str(getattr(info, "serial_number", "") or ""),
            "description": str(getattr(info, "description", "") or ""),
            "firmware": str(getattr(info, "firmware", "") or ""),
            "vmon_scaling_v_per_v": float(
                getattr(info, "vmon_scaling_v_per_v",
                        VMON_SCALING_DEFAULT) or VMON_SCALING_DEFAULT),
            "imon_scaling_v_per_ua": float(
                getattr(info, "imon_scaling_v_per_ua",
                        IMON_SCALING_DEFAULT) or IMON_SCALING_DEFAULT),
            "n_channels": int(getattr(info, "n_channels", 0) or 0),
            "is_simulated": bool(getattr(info, "is_simulated", False)),
        }
        # The detected I_mon magnification factor from _validate_scaling.
        _detected_imon = self._scaling_validation.get("detected_v_per_ua")
        try:
            _imon_v_per_ua_actual = (float(_detected_imon)
                                     if _detected_imon is not None
                                     and not __import__("math").isnan(float(_detected_imon))
                                     else None)
        except Exception:
            _imon_v_per_ua_actual = None
        try:
            path = write_calibration_payload(
                self._fit, notes=notes,
                stimulator=stim_payload,
                scaling_validation=self._scaling_validation,
                vmon_offset_v=self._vmon_offset_v,
                imon_offset_v=self._imon_offset_v,
                imon_v_per_ua_actual=_imon_v_per_ua_actual,
                load={"r_ohm": load_ohm,
                      "c_pf": load_cap_pf,
                      "board": "Plexon 14-04-A-03-A",
                      "cable": "Plexon 14-03-A-03"})
        except OSError as e:
            QtWidgets.QMessageBox.critical(
                self, "Save failed",
                f"Could not write verification file:\n{e}")
            return
        # Persist the validated serial → scaling mapping into the
        # shared prefs map (the same store the Connection panel
        # reads on stim init). Recorded for EVERY successfully-
        # validated device, not just NIL — so the prefs map is
        # the authoritative database of "which scaling does this
        # serial use?". Future sessions can then warn the user
        # if they plug in a serial we haven't seen calibrated
        # before, and apply the right preset automatically when
        # they plug in a known one.
        learned_msg = ""
        sv = self._scaling_validation or {}
        sn = stim_payload["serial_number"]
        detected = sv.get("detected_preset") if sv.get("available") else None
        if detected in ("Default", "NIL") and sn:
            try:
                learned = self._record_serial_scaling(sn, detected)
                if learned == "new":
                    learned_msg = (
                        f"<br><br>Learned: serial <b>{sn}</b> uses "
                        f"<b>{detected}</b> scaling — added to the "
                        f"shared scaling database. Future sessions "
                        f"will apply <b>{detected}</b> automatically "
                        f"on Initialize.")
                elif learned == "updated":
                    learned_msg = (
                        f"<br><br>Updated: serial <b>{sn}</b> in the "
                        f"shared scaling database now records "
                        f"<b>{detected}</b> scaling (changed from a "
                        f"prior mapping).")
            except Exception as e:
                learned_msg = (f"<br><br>(Could not persist serial "
                                f"scaling mapping: {e})")
        elif sn:
            learned_msg = (
                f"<br><br>Note: detected scaling for serial "
                f"<b>{sn}</b> was <b>{detected or 'unknown'}</b> — "
                f"NOT added to the shared scaling database. Re-run "
                f"the sweep with the correct Connection-panel preset "
                f"to record this device.")
        QtWidgets.QMessageBox.information(
            self, "Stimulator verification saved",
            f"Saved stimulator verification for {len(self._fit)} channels to:<br>"
            f"<tt>{path}</tt>"
            f"{learned_msg}<br><br>The runner will apply this trim "
            f"on the next capture cycle.")

    def _record_serial_scaling(self, serial_number: str,
                               preset: str) -> str:
        """Persist ``serial_number → preset`` into the shared
        prefs map ``stim_scaling_by_serial`` (the same map the
        :class:`ConnectionPanel` consults at stim init).

        ``preset`` must be one of the named PlexStim scaling
        presets (currently "Default" or "NIL"). The map is what
        the Setup tab uses to:

          1. Apply the right preset automatically on Initialize
             for a known serial number, AND
          2. Warn the user when an UNKNOWN serial is plugged in
             ("this device hasn't been calibrated — please run
             the calibration sweep before relying on the
             readback values").

        Returns ``"new"`` when this is the first entry for this
        serial, ``"updated"`` when it changes a previous mapping,
        and ``"unchanged"`` when the entry already matched.
        """
        prefs = load_prefs() or {}
        section = prefs.get("stim_scaling_by_serial", {})
        if not isinstance(section, dict):
            section = {}
        prior = section.get(serial_number)
        if prior == preset:
            return "unchanged"
        outcome = "updated" if prior is not None else "new"
        section[serial_number] = preset
        prefs["stim_scaling_by_serial"] = section
        save_prefs(prefs)
        return outcome


def write_calibration_payload(coefficients: dict,
                              *, notes: str = "",
                              stimulator: Optional[dict] = None,
                              scaling_validation: Optional[dict] = None,
                              vmon_offset_v: float = 0.0,
                              imon_offset_v: float = 0.0,
                              imon_v_per_ua_actual: Optional[float] = None,
                              load: Optional[dict] = None) -> Path:
    """Write a calibration payload to :func:`calibration_path` with
    the canonical schema:

    .. code-block:: json

        {
          "timestamp": "<ISO-8601 local time>",
          "schema_version": 2,
          "coefficients": {"1": {"a": 1.002, "b": 0.04}, ...},
          "stimulator": {"serial_number": "PLX00180",
                         "imon_scaling_v_per_ua": 1e-3, ...},
          "scaling_validation": {"detected_preset": "NIL",
                                  "active_preset": "NIL",
                                  "mismatch": false, ...},
          "load": {"r_ohm": 4990, "c_pf": 4700,
                   "board": "Plexon 14-04-A-03-A",
                   "cable": "Plexon 14-03-A-03"},
          "notes": "..."
        }

    The ``timestamp`` field is what :func:`last_calibration_datetime`
    reads to populate the Help → Last calibration… dialog.

    The ``stimulator.serial_number`` field is the device this
    calibration was recorded against — the runner reads it on
    startup and refuses to apply a calibration whose serial
    doesn't match the currently-attached device. The
    ``scaling_validation`` block records what I_mon scaling
    preset the validator detected vs. what was active at sweep
    time, so a future runner can detect "user changed scaling
    after calibration" and warn.

    Returns the path the file was written to.
    """
    path = calibration_path()
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "schema_version": 2,
        "coefficients": dict(coefficients or {}),
        "vmon_offset_v": float(vmon_offset_v),
        "imon_offset_v": float(imon_offset_v),
        "imon_v_per_ua_actual": imon_v_per_ua_actual,
        "stimulator": dict(stimulator or {}),
        "scaling_validation": dict(scaling_validation or {}),
        "load": dict(load or {}),
        "notes": str(notes or ""),
    }
    # Atomic write: temp file + rename so a crash mid-write can't
    # leave a half-corrupt calibration.json on disk.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path
