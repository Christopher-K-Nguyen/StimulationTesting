"""Per-experiment-tab widget for closed-loop interpulse-bias feedback.

Combines three concerns into one Test-Parameters QGroupBox:

  * **Connector** — a :class:`BiasConnector` in host-delegated mode,
    sharing the driver opened by :class:`ConnectionPanel`.  All
    per-tab connectors see the same underlying STM32 driver; pressing
    Connect from any tab updates every tab's UI in lockstep.
  * **Feedback config** — setpoint / tolerance / k_i / V_mon sanity /
    gating window spinboxes.  Build a :class:`BiasFeedbackConfig` via
    :meth:`feedback_config` when the operator enables the feature
    via the master checkbox.
  * **Live status badge** — surfaces the most recent
    :class:`BiasFeedbackStep` while a run is active.  Three states:
    DISARMED (gray), ARMED (green), SATURATED / VMON-FAIL (red).
    Updated from the runner via :meth:`set_status`.

Why per-tab and not global?  Different experiment types have
different natural setpoints and gating windows (LP's slow drift loop
vs SP's short transient), and persisting per-experiment is cleaner
than one shared field that gets clobbered when the user switches
experiments.  Prefs round-trip lives in :meth:`prefs_dict` /
:meth:`restore_prefs` under the ``bias_feedback`` key.

This widget knows NOTHING about whether the closed-loop controller
is actually wired into the runner yet (that's #43).  It just
captures the operator's intent + surfaces status if a runner
chooses to push status updates here.  When unwired, the master
checkbox can still be flipped — :meth:`feedback_config` returns
``None`` when unchecked, so the runner side ignores it cleanly.
"""
from __future__ import annotations

from typing import Optional

from PyQt6 import QtCore, QtWidgets

from ..experiments.bias_feedback import BiasFeedbackConfig, BiasFeedbackStep
from .bias_panel import BiasConnector


class BiasFeedbackPanel(QtWidgets.QGroupBox):
    """QGroupBox-shaped Test-Parameters widget for closed-loop bias.

    The widget is constructed without a host (so the experiment tab
    can build it eagerly in __init__) and the MainWindow plumbs in
    the ConnectionPanel via :meth:`set_bias_host` after tab
    construction completes.

    Signal-API surface mirrors the camera-capture group:

    * :meth:`feedback_config` returns a populated
      :class:`BiasFeedbackConfig` when the master checkbox is on,
      or ``None`` when off.  Called by the experiment runner at
      run start.
    * :meth:`set_status` (slot) updates the live status badge from
      a :class:`BiasFeedbackStep`.  Connected from the runner's
      per-step progress signal.
    * :meth:`prefs_dict` / :meth:`restore_prefs` round-trip the
      operator-visible values to the GUI prefs file.
    """

    #: Emitted whenever a feedback parameter changes.  Same role as
    #: SetupTab's per-field signals: MainWindow can log every change
    #: to the LogPane (gotcha #29).
    feedbackChanged = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__("Interpulse Bias Module (closed-loop)", parent)
        self.setToolTip(
            "Optional closed-loop feedback that drives the STM32-applied "
            "DC bias voltage to keep the return-electrode potential at a "
            "configured setpoint between pulses.  The connector (top row) "
            "shares the bias driver with every other experiment tab; the "
            "feedback config below applies to THIS experiment only.  When "
            "the master 'Enable feedback' checkbox is off, the bias module "
            "is still usable as a manually-set DAC, just without the "
            "closed loop.")
        self.setCheckable(False)

        # ---- row 1: shared connector (host-delegated) -----------------
        # Construct WITHOUT a host — set_bias_host() will wire it up
        # after the experiment tab is added to MainWindow.  Until then
        # the connector is in standalone mode but Connect is disabled
        # via _enable_connector_for_host (below) so the operator can't
        # accidentally open a parallel driver instance.
        self._connector = BiasConnector(self, host=None)
        self._connector.log.connect(self._on_connector_log)
        # Until set_bias_host() runs, hide the Connect button — there's
        # no host to delegate to, and we don't want to expose the
        # standalone-mode path to operators (that's only for tests).
        self._connector.connect_btn.setEnabled(False)
        self._connector.simulate_check.setEnabled(False)

        # ---- row 2: master Enable checkbox + status badge ------------
        self.enable_check = QtWidgets.QCheckBox("Enable closed-loop feedback")
        self.enable_check.setChecked(False)
        self.enable_check.setToolTip(
            "Master gate for the feedback loop.  When OFF the bias "
            "module behaves as a static DAC (whatever voltage the "
            "controller last wrote stays applied); no scope reads, "
            "no setpoint tracking.  When ON the runner reads E_ret "
            "every iteration and adjusts the bias DAC toward the "
            "setpoint below.  Default OFF so a run without bias intent "
            "is a pure no-op.")
        self.enable_check.toggled.connect(self._on_enable_toggled)
        self.enable_check.toggled.connect(self.feedbackChanged.emit)

        self.status_badge = QtWidgets.QLabel("● DISARMED")
        # ARMED text expands to e.g. "● ARMED  bias=+0.300 V
        # err=+5.20 mV" (~35 chars).  The Test Parameters page sits
        # in a splitter so the panel can be narrowed — reflow rather
        # than truncate the per-step diagnostic text.
        self.status_badge.setWordWrap(True)
        self.status_badge.setToolTip(
            "Closed-loop status.  DISARMED = checkbox off / runner "
            "hasn't started.  ARMED = runner is calling step() and "
            "applying adjustments.  SATURATED = bias DAC pinned at "
            "its rail (need a different setpoint or hardware range).  "
            "V_MON FAIL = V_mon sanity check tripped (bias delivery "
            "suspect).")
        self._apply_status_style("disarmed")

        # ---- row 3+ : feedback parameters -----------------------------
        self.setpoint_spin = self._make_voltage_spin(
            value=0.30, decimals=3, suffix=" V", lo=-5.0, hi=5.0,
            step=0.01,
            tooltip="Target return-electrode potential between pulses.  "
                    "Driven by the integral controller toward this value.  "
                    "SIROF rest is typically 0.3-0.5 V; gold ~0.8 V; "
                    "Ag/AgCl ~0 V.  Adjust to your electrode chemistry.")
        self.tolerance_spin = self._make_voltage_spin(
            value=5.0, decimals=2, suffix=" mV", lo=0.1, hi=500.0,
            step=1.0,
            tooltip="±deadband around the setpoint.  Errors within this "
                    "window do not trigger DAC writes — prevents the loop "
                    "from chasing measurement noise.  Default ±5 mV "
                    "matches the interpulse-bias tolerance spec.")
        self.ki_spin = QtWidgets.QDoubleSpinBox()
        self.ki_spin.setRange(0.001, 1.0)
        self.ki_spin.setSingleStep(0.05)
        self.ki_spin.setDecimals(3)
        self.ki_spin.setValue(0.10)
        self.ki_spin.setToolTip(
            "Integral gain (dimensionless per step).  0.10 = each "
            "iteration removes ~10 % of the error → converges in "
            "~10 steps.  Higher = faster but risks oscillation around "
            "the deadband edge.")
        self.vmon_sanity_spin = self._make_voltage_spin(
            value=50.0, decimals=1, suffix=" mV", lo=1.0, hi=1000.0,
            step=5.0,
            tooltip="V_mon sanity threshold.  When |V_mon| over the "
                    "gated interpulse window exceeds this, the bias is "
                    "presumed unbalanced / broken and the iteration is "
                    "skipped (logged).  Set very high to disable.  "
                    "Real wiring should keep V_mon below ~5 mV.")
        self.gate_lo_spin = QtWidgets.QDoubleSpinBox()
        self.gate_lo_spin.setRange(-1000.0, 100000.0)
        self.gate_lo_spin.setSingleStep(10.0)
        self.gate_lo_spin.setDecimals(0)
        self.gate_lo_spin.setSuffix(" µs")
        self.gate_lo_spin.setValue(300.0)
        self.gate_lo_spin.setToolTip(
            "Start of the scope's MEASUrement gating window, in µs "
            "from the trigger.  Should be AFTER the post-pulse RC "
            "discharge settles.  Default 300 µs suits the standard "
            "biphasic ~200 µs pulse at 100 Hz.")
        self.gate_hi_spin = QtWidgets.QDoubleSpinBox()
        self.gate_hi_spin.setRange(-1000.0, 100000.0)
        self.gate_hi_spin.setSingleStep(10.0)
        self.gate_hi_spin.setDecimals(0)
        self.gate_hi_spin.setSuffix(" µs")
        self.gate_hi_spin.setValue(450.0)
        self.gate_hi_spin.setToolTip(
            "End of the scope's MEASUrement gating window, in µs "
            "from the trigger.  Should be BEFORE the next pulse "
            "trigger.  Width = hi - lo; bigger = more averaging, "
            "noisier per-iteration estimate.")
        for spin in (self.setpoint_spin, self.tolerance_spin, self.ki_spin,
                     self.vmon_sanity_spin, self.gate_lo_spin,
                     self.gate_hi_spin):
            spin.valueChanged.connect(self.feedbackChanged.emit)

        # ---- layout ---------------------------------------------------
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        outer.addWidget(self._connector)

        row_enable = QtWidgets.QHBoxLayout()
        row_enable.addWidget(self.enable_check)
        row_enable.addStretch(1)
        row_enable.addWidget(self.status_badge)
        outer.addLayout(row_enable)

        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)
        form.addRow("Setpoint:", self.setpoint_spin)
        form.addRow("Tolerance (±):", self.tolerance_spin)
        form.addRow("Gain (k_i):", self.ki_spin)
        form.addRow("V_mon sanity:", self.vmon_sanity_spin)
        # Gating window — pack lo + hi onto one row to save vertical
        # space.  Operator reads it as "from X µs to Y µs after
        # trigger."
        gate_row = QtWidgets.QHBoxLayout()
        gate_row.addWidget(self.gate_lo_spin)
        gate_row.addWidget(QtWidgets.QLabel("to"))
        gate_row.addWidget(self.gate_hi_spin)
        gate_row.addStretch(1)
        form.addRow("Gating window:", gate_row)
        outer.addLayout(form)

        # ---- initial enable-gate -------------------------------------
        # When the master checkbox starts OFF, gray out the per-config
        # spinboxes — clearer than just-ignored values.  The connector
        # row stays enabled either way (operator may want to wire up
        # the driver before they decide to engage the loop).
        self._on_enable_toggled(False)

    # ============================================================ host

    def set_bias_host(self, host: Optional[object]) -> None:
        """Plumb in the shared ConnectionPanel that owns the bias
        driver.  Called by MainWindow after tab construction.

        Delegates to :meth:`BiasConnector.set_host` and re-enables
        the Connect button so the operator can actually open the
        driver from this tab.  Idempotent + safe with ``None`` to
        detach (e.g. ConnectionPanel teardown during test cleanup).
        """
        self._connector.set_host(host)
        is_attached = host is not None
        self._connector.connect_btn.setEnabled(is_attached)
        self._connector.simulate_check.setEnabled(is_attached)

    # ============================================================ public API

    def feedback_config(self) -> Optional[BiasFeedbackConfig]:
        """Return a populated config from current widget state, or
        ``None`` when the master checkbox is off.

        The runner calls this at run start.  Returning ``None`` is
        the canonical "skip the feedback loop entirely" signal — no
        controller is constructed, no scope MEASUrement gating is
        configured, no per-iteration overhead.
        """
        if not self.enable_check.isChecked():
            return None
        return BiasFeedbackConfig(
            setpoint_v=float(self.setpoint_spin.value()),
            tolerance_v=float(self.tolerance_spin.value()) * 1e-3,
            k_i=float(self.ki_spin.value()),
            vmon_sanity_threshold_v=float(self.vmon_sanity_spin.value()) * 1e-3,
            gating_window_us=(float(self.gate_lo_spin.value()),
                              float(self.gate_hi_spin.value())),
            channel="eret",
            vmon_channel="vmon",
        )

    def set_status(self, step: Optional[BiasFeedbackStep]) -> None:
        """Update the live status badge from a controller step.

        Pass ``None`` to reset to DISARMED (e.g. at run end).  No-op
        when the runner hasn't pushed any step yet — the badge stays
        at DISARMED from construction.
        """
        if step is None:
            self.status_badge.setText("● DISARMED")
            self._apply_status_style("disarmed")
            return
        if not step.vmon_sane:
            self.status_badge.setText("● V_MON FAIL")
            self._apply_status_style("bad")
            return
        if step.saturated:
            self.status_badge.setText(
                f"● SATURATED @ {step.bias_v_after:+.3f} V")
            self._apply_status_style("bad")
            return
        # Normal arming.  Show the current bias + most recent error
        # so the operator gets per-iteration insight at a glance.
        err_mv = step.error_v * 1e3
        self.status_badge.setText(
            f"● ARMED  bias={step.bias_v_after:+.3f} V  "
            f"err={err_mv:+.2f} mV")
        self._apply_status_style("good")

    def prefs_dict(self) -> dict:
        """Snapshot operator-visible state for prefs round-trip.

        Saved under the ``bias_feedback`` key in the experiment tab's
        prefs.  Same naming convention as
        :meth:`camera_capture_prefs_dict`.
        """
        return {
            "enabled": bool(self.enable_check.isChecked()),
            "setpoint_v": float(self.setpoint_spin.value()),
            "tolerance_mv": float(self.tolerance_spin.value()),
            "k_i": float(self.ki_spin.value()),
            "vmon_sanity_mv": float(self.vmon_sanity_spin.value()),
            "gate_lo_us": float(self.gate_lo_spin.value()),
            "gate_hi_us": float(self.gate_hi_spin.value()),
        }

    def restore_prefs(self, payload: dict) -> None:
        """Restore widget state from a prefs payload.

        Tolerant of missing keys (uses current defaults) and of bad
        types (falls back silently rather than raising — prefs files
        from older builds may not have every field).
        """
        if not isinstance(payload, dict):
            return
        try:
            self.enable_check.setChecked(bool(payload.get("enabled", False)))
        except Exception:
            pass
        # Each field guarded separately — partial-restore beats
        # all-or-nothing.
        for key, widget in (
            ("setpoint_v", self.setpoint_spin),
            ("tolerance_mv", self.tolerance_spin),
            ("k_i", self.ki_spin),
            ("vmon_sanity_mv", self.vmon_sanity_spin),
            ("gate_lo_us", self.gate_lo_spin),
            ("gate_hi_us", self.gate_hi_spin),
        ):
            if key in payload:
                try:
                    widget.setValue(float(payload[key]))
                except (TypeError, ValueError):
                    pass

    # ============================================================ slots
    def _on_enable_toggled(self, on: bool) -> None:
        """Gate the config spinboxes on the master Enable checkbox.

        Connector row stays enabled either way — operator may want
        to bring up the bias driver before deciding to engage the
        loop (e.g. to manually verify the DAC).
        """
        for spin in (self.setpoint_spin, self.tolerance_spin, self.ki_spin,
                     self.vmon_sanity_spin, self.gate_lo_spin,
                     self.gate_hi_spin):
            spin.setEnabled(on)
        if not on:
            # Reset badge to DISARMED when the operator flips the
            # checkbox off mid-run — avoids stale ARMED text after a
            # cancel.
            self.set_status(None)

    def _on_connector_log(self, msg: str) -> None:
        """Pass connector log messages through unchanged.

        The MainWindow doesn't subscribe to BiasFeedbackPanel.log;
        it subscribes to the connector's signal via ConnectionPanel
        for the SINGLE driver instance.  Per-tab connectors are
        mirrors and shouldn't double-log every command.  Kept as
        a slot for hand-off symmetry; presently a no-op.
        """
        # Intentional no-op.  See docstring.
        del msg

    # ============================================================ helpers
    @staticmethod
    def _make_voltage_spin(
        *, value: float, decimals: int, suffix: str,
        lo: float, hi: float, step: float, tooltip: str,
    ) -> QtWidgets.QDoubleSpinBox:
        """Build a QDoubleSpinBox preconfigured for a voltage-style
        field.  Factors out the boilerplate from the 4 voltage knobs."""
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(decimals)
        spin.setSuffix(suffix)
        spin.setValue(value)
        spin.setToolTip(tooltip)
        return spin

    def _apply_status_style(self, kind: str) -> None:
        """Stylesheet swap for the status badge — three states.

        Colours match the camera LIVE / RECORDING badges (gotcha
        about "GREEN = live, RED = active write") so the operator
        reads the badge the same way across panels.
        """
        if kind == "good":
            qss = ("color: white; background-color: #2c7a2c; "
                   "border-radius: 4px; padding: 2px 6px; font-weight: 600;")
        elif kind == "bad":
            qss = ("color: white; background-color: #a13030; "
                   "border-radius: 4px; padding: 2px 6px; font-weight: 600;")
        else:  # disarmed
            qss = ("color: #555; background-color: #d4d4d4; "
                   "border-radius: 4px; padding: 2px 6px;")
        self.status_badge.setStyleSheet(qss)
