"""PlexStim test-board calibration wizard.

Patterned after the Gamry calibration wizards: a multi-step flow that
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
    4. **Save** — write the per-channel calibration to
       ``~/.config/stimtest/calibration.json`` (or wherever the
       prefs system points). The runner reads this file at start
       and applies the inverse transform to every reported value.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from .prefs import prefs_dir, load_prefs, save_prefs
from ..config import (IMON_SCALING_DEFAULT, IMON_SCALING_NIL,
                      VMON_SCALING_DEFAULT, VMON_SCALING_NIL,
                      NIL_SERIAL_NUMBERS)


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
    source-of-truth — written by :meth:`CalibrationDialog._on_save_calibration`
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


class CalibrationDialog(QtWidgets.QDialog):
    """Wizard-style modal dialog for the PlexStim test-board flow.

    ``stim`` and ``scope`` are the live hardware handles (or
    ``None`` when the GUI hasn't connected yet). The dialog enables
    its action buttons only when the hardware is reachable; offline
    use shows the protocol read-only so the user can still review
    what calibration involves.
    """

    #: Default amplitude grid (µA) swept on every channel. Sparse
    #: enough to keep total wall-clock per-channel under ~5 s
    #: (5 capture cycles × ~1 s each) while dense enough to fit
    #: a linear gain + offset per channel cleanly. The 1000 µA
    #: endpoint is the PlexStim hardware ceiling.
    DEFAULT_AMPLITUDE_GRID_UA: tuple = (50.0, 100.0, 200.0, 500.0, 1000.0)
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
    PHASE_WIDTH_US: float = 200.0
    #: Acceptance threshold for flagging a channel as out-of-spec
    #: after the linear fit. PlexStim's spec is ±2 %; we use
    #: that as the default "red flag" threshold (channels with
    #: residual gain off by more than this get coloured red in
    #: the results table).
    ACCEPTANCE_PCT: float = 2.0

    def __init__(self,
                 parent: Optional[QtWidgets.QWidget] = None,
                 *,
                 stim=None, scope=None):
        super().__init__(parent)
        self.setWindowTitle("PlexStim test-board calibration")
        self.setMinimumWidth(720)
        self.setMinimumHeight(640)
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

        intro = QtWidgets.QLabel(
            "<h3>PlexStim test-board calibration</h3>"
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
        connected = (self._stim is not None and self._scope is not None)
        self.status_label = QtWidgets.QLabel()
        self.status_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        if connected:
            self.status_label.setText(
                "<b style='color: #009E73'>Hardware connected.</b> "
                "Ready to run the calibration sweep.")
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
            "calibration recovers I from the I·R edge step at "
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
        # Read-back of how many channels we'll sweep, derived from
        # the live stim info if available (fall back to "n/a").
        n_ch_label = QtWidgets.QLabel("n/a")
        if self._stim is not None and getattr(self._stim, "info", None) is not None:
            n_ch_label.setText(
                f"{int(self._stim.info.n_channels or 16)} channels")
        form.addRow("Channels:", n_ch_label)

        # Hard-gate the sweep behind an explicit "I confirm the
        # test-board (black Omnetics) is connected" checkbox. The
        # Run sweep button stays disabled until the user ticks
        # this, so they cannot accidentally drive 1 mA into a live
        # electrode array thinking they were calibrating.
        self.testboard_confirm = QtWidgets.QCheckBox(
            "I confirm the PlexStim channel array is connected to "
            "the Plexon 14-04-A-03-A test board via the BLACK "
            "OMNETICS STIMULATION CABLE (Plexon 14-03-A-03), and "
            "NOT to a live electrode array.")
        self.testboard_confirm.setToolTip(
            "Safety interlock — Run sweep stays disabled until "
            "this is ticked. Confirms the operator has verified "
            "the cable is plugged into the Plexon test board "
            "(passive 4.99 kΩ + 4700 pF series load per channel), "
            "not a live electrode array. The sweep delivers up "
            "to 1 mA pulses on every channel; if you mis-wire to "
            "a live array, you could damage electrodes or tissue.")
        self.testboard_confirm.setStyleSheet(
            "QCheckBox { font-weight: bold; color: #b71c1c; }")
        self.testboard_confirm.toggled.connect(
            self._on_testboard_confirm_toggled)
        form.addRow("Test-board check:", self.testboard_confirm)

        # ---- Sweep-progress panel ----
        # Visible from the start but inert until ``Run sweep`` is
        # clicked. Three layers:
        #   * status_progress: "Channel N / M — A µA" text
        #   * progress_bar: 0..100 % across all channel × amplitude
        #     steps
        #   * preview_plot: live V_mon trace from the most-recent
        #     capture (gives the user a "is the hardware actually
        #     doing something?" anchor)
        #   * results_table: per-channel fit summary, populated
        #     after the sweep completes
        self.status_progress = QtWidgets.QLabel(
            "Idle. Click Run sweep to begin.")
        self.status_progress.setStyleSheet("color: #555;")
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)

        # Live capture preview — a small pyqtgraph plot updated
        # after every scope capture so the user can see the V_mon
        # trace from the channel currently being tested. Dual y-
        # axes: V_mon on the left (CH1, blue) and I_mon on the
        # right (CH2, red). I_mon is the PlexStim's
        # current-monitor analog output — a voltage proportional
        # to the actual current the stimulator is sourcing — so
        # both traces share the time axis and the right axis is
        # labelled in volts. Hidden when pyqtgraph isn't
        # importable (defensive — pyqtgraph is a hard dep but
        # the import can fail in odd CI envs).
        try:
            import pyqtgraph as pg
            self._pg = pg
            self.preview_plot = pg.PlotWidget()
            self.preview_plot.setBackground("w")
            self.preview_plot.setLabel("bottom", "Time (µs)")
            self.preview_plot.setLabel("left", "V_mon (V, CH1)",
                                       color="#1976d2")
            self.preview_plot.showGrid(x=True, y=True, alpha=0.25)
            self.preview_plot.setMinimumHeight(180)
            self._preview_curve = self.preview_plot.plot(
                pen=pg.mkPen("#1976d2", width=2),
                name="V_mon (CH1)")
            # Right-axis ViewBox for I_mon. pyqtgraph doesn't
            # support twin y-axes directly, so we add a second
            # ViewBox linked to the main scene and forward
            # resize / x-pan events to keep the two views
            # synchronised.
            plot_item = self.preview_plot.getPlotItem()
            plot_item.showAxis("right")
            plot_item.setLabel("right", "I_mon (V, CH2)",
                               color="#d32f2f")
            self._imon_viewbox = pg.ViewBox()
            plot_item.scene().addItem(self._imon_viewbox)
            plot_item.getAxis("right").linkToView(self._imon_viewbox)
            self._imon_viewbox.setXLink(plot_item)
            self._imon_curve = pg.PlotDataItem(
                pen=pg.mkPen("#d32f2f", width=2),
                name="I_mon (CH2)")
            self._imon_viewbox.addItem(self._imon_curve)

            def _sync_imon_view():
                # Keep the I_mon ViewBox geometry locked to the
                # main plot's ViewBox geometry across window
                # resizes and zooms.
                self._imon_viewbox.setGeometry(
                    plot_item.vb.sceneBoundingRect())
                self._imon_viewbox.linkedViewChanged(
                    plot_item.vb, self._imon_viewbox.XAxis)

            self._sync_imon_view = _sync_imon_view
            plot_item.vb.sigResized.connect(_sync_imon_view)
            _sync_imon_view()
        except Exception:
            self._pg = None
            self.preview_plot = QtWidgets.QLabel(
                "(Live capture preview unavailable — pyqtgraph not loadable.)")

        # Results table — one row per channel, populated post-fit.
        self.results_table = QtWidgets.QTableWidget(0, 4)
        self.results_table.setHorizontalHeaderLabels(
            ["Channel", "Gain (a)", "Offset (b, µA)", "RMSD (µA)"])
        self.results_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setMinimumHeight(140)

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
        self._btn_save = QtWidgets.QPushButton("Save calibration…")
        self._btn_save.setEnabled(False)
        self._btn_save.clicked.connect(self._on_save_calibration)
        btn_close = QtWidgets.QPushButton("Close")
        btn_close.clicked.connect(self.accept)

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self._btn_run)
        btn_row.addWidget(self._btn_abort)
        btn_row.addWidget(self._btn_save)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_close)

        # ---- Assemble ----
        v = QtWidgets.QVBoxLayout(self)
        v.addWidget(intro)
        v.addWidget(self.status_label)
        v.addLayout(form)
        v.addWidget(self.status_progress)
        v.addWidget(self.progress_bar)
        v.addWidget(self.preview_plot, stretch=2)
        v.addWidget(self.results_table, stretch=1)
        v.addLayout(btn_row)

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
                "Cannot run calibration without both the PlexStim "
                "and the oscilloscope connected. Use the "
                "Connection panel on the Setup tab to attach them, "
                "then re-open this dialog.")
            return
        # Resolve channel count once at the start of the sweep.
        # Capped at 16 (the PlexStim's per-stim ceiling); user
        # spec defaults to 16 if the SDK didn't report a count.
        n_channels = int((getattr(self._stim, "info", None)
                          and self._stim.info.n_channels) or 16)
        load_ohm = float(self.DEFAULT_LOAD_OHM)
        amplitudes = list(self.DEFAULT_AMPLITUDE_GRID_UA)
        total_steps = n_channels * len(amplitudes)
        # ---- Instructions popup ----
        # Mirrors the checkbox gate above — repeated here so the
        # final go/no-go fires immediately before stim, when the
        # user's attention is highest. The black-Omnetics-only
        # language is repeated verbatim so the safety reminder is
        # unambiguous.
        load_cap_pf = float(self.DEFAULT_LOAD_CAP_PF)
        proceed = QtWidgets.QMessageBox.question(
            self, "Calibration — final check",
            f"<b>About to run the calibration sweep.</b><br><br>"
            f"This will deliver biphasic pulses on each of "
            f"<b>{n_channels} channels</b> at "
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
        # ---- Sweep ----
        self._aborted = False
        self._results.clear()
        self._fit.clear()
        self._scaling_validation = {}
        self._btn_run.setEnabled(False)
        self._btn_abort.setEnabled(True)
        self._btn_save.setEnabled(False)
        self.results_table.setRowCount(0)
        self.progress_bar.setValue(0)
        try:
            self._run_sweep_blocking(n_channels, amplitudes, load_ohm,
                                     total_steps)
        finally:
            self._btn_run.setEnabled(True)
            self._btn_abort.setEnabled(False)
        # ---- Fit ----
        if self._aborted:
            self.status_progress.setText(
                "Sweep aborted by user. Partial results discarded.")
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
                "Run the per-channel calibration sweep against "
                "the test board.")

    def _on_abort_clicked(self):
        """User clicked Abort during a running sweep. Setting the
        flag short-circuits the next iteration of the sweep loop
        — actual hardware shutdown happens there so we don't
        leave the device in a half-programmed state."""
        self._aborted = True
        self.status_progress.setText(
            "Abort requested — finishing current capture…")

    def _run_sweep_blocking(self, n_channels: int,
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

        step_idx = 0
        for ch in range(1, n_channels + 1):
            if self._aborted:
                return
            self._results[ch] = []
            for amp_ua in amplitudes:
                if self._aborted:
                    return
                step_idx += 1
                self.status_progress.setText(
                    f"Channel {ch} / {n_channels}  ·  "
                    f"{amp_ua:.0f} µA  ·  "
                    f"step {step_idx} / {total_steps}")
                pct = int(round(100.0 * step_idx / max(1, total_steps)))
                self.progress_bar.setValue(pct)
                QtWidgets.QApplication.processEvents()
                # Deliver one biphasic pulse + capture. Errors on
                # individual cells are reported via the status
                # label but don't abort — sweep continues so a
                # single bad channel doesn't kill the whole run.
                load_cap_pf = float(self.DEFAULT_LOAD_CAP_PF)
                try:
                    measured_ua, imon_peak_v = self._capture_one_amplitude(
                        ch, amp_ua, load_ohm, load_cap_pf)
                except Exception as e:
                    self.status_progress.setText(
                        f"Channel {ch} @ {amp_ua:.0f} µA: error — {e}")
                    QtWidgets.QApplication.processEvents()
                    continue
                self._results[ch].append(
                    (float(amp_ua), float(measured_ua),
                     float(imon_peak_v)))
            # Stop the channel after its sweep completes, so the
            # device doesn't keep stimulating into the test board
            # while we move on to the next channel.
            try:
                self._stim.stop_channel(ch)
            except Exception:
                pass
        self.status_progress.setText("Sweep complete. Fitting …")
        self.progress_bar.setValue(100)
        QtWidgets.QApplication.processEvents()

    def _capture_one_amplitude(self, channel: int, amp_ua: float,
                               load_ohm: float,
                               load_cap_pf: float
                               ) -> tuple:
        """Drive a single biphasic pulse on ``channel`` at the
        programmed amplitude, scope-capture V_mon + I_mon, and
        return ``(measured_ua_from_vmon, imon_peak_v)``.

        The V_mon-derived current is the primary calibration
        signal (the per-channel ``a · I_mon + b`` fit uses it).
        The I_mon peak voltage is collected so the post-sweep
        scaling validator can compare the device's actual I_mon
        gain to the configured ``imon_scaling_v_per_ua`` and
        detect Default-vs-NIL mismatches.

        Routes the scope's monitor channel to ``channel`` so V_mon
        / I_mon traces target the right pad. Stops the channel
        after capture so the next iteration doesn't see leftover
        stim. Updates the preview plot with the captured trace.

        **Why the edge step, not peak |V_mon|.** The Plexon
        14-04-A-03-A test board's load is an **RC series**
        (4.99 kΩ + 4700 pF per channel), not a pure resistor.
        During a constant-current phase of width W, the cap
        charges linearly: V_C(t) = I · t / C. At I = 100 µA,
        C = 4700 pF, W = 200 µs the cap reaches 4.26 V — eight
        times larger than the V_R contribution (0.499 V). So
        |V_mon|_peak is dominated by the cap-charge term and
        is NOT a clean measure of I·R.

        Across each phase transition (start of phase 1, end of
        phase 1, start of phase 2, end of phase 2) the cap
        voltage is continuous, so the V_mon **jump** at each
        edge is purely I·R. We extract the four largest
        sample-to-sample diffs in the trace (one per phase edge)
        and average them to recover I·R, then divide by R to
        get the measured current.
        """
        from ..waveforms import PulsePattern
        import numpy as np

        # Build a charge-balanced biphasic at the test amplitude.
        # Polarity = cathodic-first (-1) by convention; the
        # calibration just needs |I_mon| which is unsigned.
        pat = PulsePattern.biphasic(
            amplitude_ua=amp_ua,
            phase_width_us=self.PHASE_WIDTH_US,
            polarity=-1,
            interphase_us=20.0,
            discharge_us=20.0,
            rate_hz=50.0,
        )
        # Set the scope's monitor channel so the V_mon line tracks
        # the channel we're driving.
        try:
            self._scope.set_monitor_channel(channel)
        except Exception:
            pass
        # Program + fire + capture + stop. Wrap each call so a
        # transient SCPI / DLL hiccup surfaces clearly upstream.
        self._stim.load_channel(channel, pat)
        self._stim.start_channel(channel)
        try:
            acq = self._scope.single_capture()
        finally:
            try:
                self._stim.stop_channel(channel)
            except Exception:
                pass
        # Resolve V_mon (CH1) and I_mon (CH2) traces from the
        # acquisition. ``ScopeAcquisition.channels`` is keyed by
        # the physical channel id (CH1 / CH2 / ...) and the
        # scope's ``channel_aliases`` map translates logical
        # names ("vmon", "imon") to those ids. Default to
        # CH1/CH2 if the scope hasn't published an alias map.
        ch_aliases = getattr(self._scope, "channel_aliases",
                              {"vmon": "CH1", "imon": "CH2"}) or {}
        v_mon_phys = ch_aliases.get("vmon", "CH1")
        i_mon_phys = ch_aliases.get("imon", "CH2")
        chan_data = getattr(acq, "channels", {}) or {}
        v_mon = chan_data.get(v_mon_phys)
        i_mon = chan_data.get(i_mon_phys)
        t_us = getattr(acq, "time_us", None)
        # Update the live preview plot with both traces. V_mon on
        # the left axis (CH1, blue) and I_mon on the right axis
        # (CH2, red). Either trace can be missing if the user
        # didn't wire that scope channel; the other still
        # renders.
        if (t_us is not None and self._pg is not None
                and hasattr(self, "_preview_curve")):
            try:
                if v_mon is not None and len(v_mon):
                    self._preview_curve.setData(t_us, v_mon)
                if i_mon is not None and len(i_mon) and hasattr(self, "_imon_curve"):
                    self._imon_curve.setData(t_us, i_mon)
                # Refresh the right-axis ViewBox after data
                # changes so its y-range autoscales to the new
                # I_mon trace.
                if hasattr(self, "_sync_imon_view"):
                    self._sync_imon_view()
            except Exception:
                pass
        # I_mon peak voltage (CH2). Used downstream to validate
        # the stimulator's I_mon scaling against the configured
        # value. The PlexStim's I_mon line is a passive voltage
        # output proportional to the actual sourced current
        # (V_imon = I · imon_scaling_v_per_ua), so its peak is
        # the cleanest direct readback of the delivered current
        # and isn't affected by the test-board RC load at all.
        imon_peak_v = float("nan")
        if i_mon is not None and len(i_mon):
            imon_arr = np.asarray(i_mon, dtype=float)
            imon_peak_v = float(np.max(np.abs(imon_arr)))
        # Recover I·R from the edge-step in the V_mon trace.
        # See docstring above for why peak |V_mon| is wrong on
        # an RC load.
        if v_mon is None or len(v_mon) < 5:
            return (float("nan"), imon_peak_v)
        v_arr = np.asarray(v_mon, dtype=float)
        # Convolve |diff| with a length-3 boxcar so a multi-
        # sample edge (scope bandwidth smearing the I-step
        # across 2-3 samples) sums into a single peak instead
        # of being split. The 4 phase edges are separated by
        # hundreds of samples, so the convolution can't merge
        # them with each other.
        diffs = np.abs(np.diff(v_arr))
        kernel = np.ones(3, dtype=float)
        edges = np.convolve(diffs, kernel, mode="same")
        # Take the median of the top-4 peaks. Median (not mean)
        # because edges may be at the trace boundary — if only
        # 3 edges are captured, the 4th "top" value is a small
        # cap-ramp diff that drags down a mean but is ignored
        # by the median of 4 (= average of middle two, both
        # real edges).
        top_n = int(min(4, edges.size))
        if top_n <= 0:
            return float("nan")
        top = np.partition(edges, -top_n)[-top_n:]
        step_v = float(np.median(top))
        # The length-3 convolution sums the IR step with up to
        # one adjacent cap-ramp sample, biasing the recovered
        # current high by I·dt/C. Solve the exact relation
        #     step_v = I · (R + dt/C)
        # for I to remove that bias. dt comes from the trace's
        # time axis when available; we fall back to a 1 µs
        # default which is the scope's coarsest sane setting.
        if t_us is not None and len(t_us) >= 2:
            dt_us = float(t_us[1] - t_us[0])
        else:
            dt_us = 1.0
        dt_s = dt_us * 1e-6
        C_farad = max(1e-15, load_cap_pf * 1e-12)
        effective_ohm = load_ohm + dt_s / C_farad
        measured_ua = step_v / effective_ohm * 1e6   # V / Ω = A → µA
        return (measured_ua, imon_peak_v)

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
        self.results_table.setRowCount(0)
        flagged_count = 0
        # Accumulate every (programmed_ua, imon_peak_v) pair
        # across channels for the global scaling fit.
        scaling_programmed: list = []
        scaling_imon_v: list = []
        for ch, pairs in sorted(self._results.items()):
            if not pairs:
                # No captures recorded — channel skipped or every
                # capture errored. Still add a row so the user
                # sees the gap.
                self._fit[str(ch)] = {"a": float("nan"),
                                       "b": float("nan"),
                                       "rmsd_ua": float("nan")}
                self._append_results_row(ch, float("nan"),
                                         float("nan"), float("nan"),
                                         is_flagged=True)
                flagged_count += 1
                continue
            programmed = np.array([p[0] for p in pairs])
            measured = np.array([p[1] for p in pairs])
            # Pull I_mon peaks (3rd tuple element) and stash for
            # the global scaling fit. Drop NaN/non-finite values
            # so a single bad capture doesn't poison the fit.
            for p in pairs:
                if len(p) >= 3 and np.isfinite(p[2]) and np.isfinite(p[0]):
                    scaling_programmed.append(float(p[0]))
                    scaling_imon_v.append(float(p[2]))
            # Fit I_actual = a · I_mon + b. We have measured as
            # I_mon (the scope's reading) and programmed as the
            # true reference; ``polyfit(measured, programmed, 1)``
            # returns [a, b] with the right semantics.
            if len(pairs) >= 2 and np.std(measured) > 0:
                coef = np.polyfit(measured, programmed, 1)
                a_fit = float(coef[0])
                b_fit = float(coef[1])
                predicted = a_fit * measured + b_fit
                rmsd = float(np.sqrt(np.mean((programmed - predicted) ** 2)))
            else:
                # Single point or constant data — can't fit.
                a_fit = float("nan")
                b_fit = float("nan")
                rmsd = float("nan")
            self._fit[str(ch)] = {"a": a_fit, "b": b_fit, "rmsd_ua": rmsd}
            # Out-of-band if |a − 1| > acceptance %. NaN is also
            # flagged (channel couldn't be fit).
            if (not np.isfinite(a_fit)
                    or abs(a_fit - 1.0) * 100.0 > self.ACCEPTANCE_PCT):
                is_flagged = True
                flagged_count += 1
            else:
                is_flagged = False
            self._append_results_row(ch, a_fit, b_fit, rmsd,
                                     is_flagged=is_flagged)
        # Stimulator scaling validation. Fit V_imon = k · I_prog
        # across all captures and pick the closest known preset.
        self._scaling_validation = self._validate_scaling(
            scaling_programmed, scaling_imon_v)
        # Status line summary.
        n_total = len(self._results)
        if flagged_count == 0:
            self.status_progress.setText(
                f"Sweep complete. All {n_total} channels within "
                f"±{self.ACCEPTANCE_PCT:.1f} %.")
        else:
            self.status_progress.setText(
                f"Sweep complete. {flagged_count} of {n_total} "
                f"channels outside ±{self.ACCEPTANCE_PCT:.1f} % "
                f"(flagged red).")
        self._btn_save.setEnabled(True)
        # Final summary pop-up — pass/fail counts plus aggregate
        # accuracy + precision, with the per-channel breakdown
        # in the expandable details pane.
        self._show_summary_popup()

    def _append_results_row(self, ch: int, a_fit: float, b_fit: float,
                            rmsd_ua: float, *, is_flagged: bool) -> None:
        """Append one row to the results table. Flagged rows are
        coloured red for at-a-glance review."""
        import math
        row = self.results_table.rowCount()
        self.results_table.insertRow(row)
        def _cell(text):
            it = QtWidgets.QTableWidgetItem(text)
            it.setTextAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            if is_flagged:
                it.setForeground(QtGui.QColor("#c62828"))
            return it
        def _fmt(v, fmt):
            if not math.isfinite(v): return "—"
            return format(v, fmt)
        self.results_table.setItem(row, 0, _cell(f"CH{ch:02d}"))
        self.results_table.setItem(row, 1, _cell(_fmt(a_fit, ".4f")))
        self.results_table.setItem(row, 2, _cell(_fmt(b_fit, "+.3f")))
        self.results_table.setItem(row, 3, _cell(_fmt(rmsd_ua, ".2f")))

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
            if math.isfinite(a):
                dev_pct = abs(a - 1.0) * 100.0
                ok = dev_pct <= self.ACCEPTANCE_PCT
            else:
                dev_pct = float("nan")
                ok = False
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
        accept = self.ACCEPTANCE_PCT
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
        parts.append("<h3>Calibration sweep — summary</h3>")
        if n_fail == 0:
            parts.append(
                f"<p style='color:#2e7d32'><b>PASS — "
                f"{n_pass} / {n_total} channels</b> within "
                f"±{accept:.1f} % gain tolerance.</p>")
        else:
            parts.append(
                f"<p style='color:#c62828'><b>"
                f"{n_fail} / {n_total} channels FAILED</b> "
                f"(±{accept:.1f} % gain tolerance).<br>"
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

        parts.append(
            "<p><b>Precision</b> "
            "<span style='color:#666'>(scatter around the per-channel fit)</span><br>"
            f"&nbsp;&nbsp;Fit RMSD: "
            f"mean <b>{_f(_mean(rmsds_ua), '{:.3f}')} µA</b>, "
            f"max <b>{_f(_max(rmsds_ua), '{:.3f}')} µA</b>"
            + (f" (CH{worst_rmsd[0]:02d})" if worst_rmsd else "")
            + "</p>")

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
                "<p style='color:#555'>Click <i>Save calibration…</i> "
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
        box.setWindowTitle("Calibration sweep — summary")
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
                "No calibration coefficients to save yet. Run the "
                "sweep first.")
            return
        load_ohm = float(self.DEFAULT_LOAD_OHM)
        load_cap_pf = float(self.DEFAULT_LOAD_CAP_PF)
        notes = (f"PlexStim test-board calibration "
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
        try:
            path = write_calibration_payload(
                self._fit, notes=notes,
                stimulator=stim_payload,
                scaling_validation=self._scaling_validation,
                load={"r_ohm": load_ohm,
                      "c_pf": load_cap_pf,
                      "board": "Plexon 14-04-A-03-A",
                      "cable": "Plexon 14-03-A-03"})
        except OSError as e:
            QtWidgets.QMessageBox.critical(
                self, "Save failed",
                f"Could not write calibration file:\n{e}")
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
            self, "Calibration saved",
            f"Saved calibration for {len(self._fit)} channels to:<br>"
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
