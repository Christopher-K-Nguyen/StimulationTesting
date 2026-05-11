"""Per-experiment metric-evolution plot for Long-Term Pulsing and
Progressive Stress.

Layout::

    ┌──────────────────────────────────────────────────────┐
    │ Channels: [☑ CH05] [☐ CH06]  Metrics: [list + axes]  │  top bar
    ├──────────────────────────────────────────────────────┤
    │                  Pulses (top x-axis)                 │
    │                                                      │
    │   ┌── left y-axis ──────────── right y-axis ──┐      │
    │   │                                            │     │
    │   │           pyqtgraph plot                   │     │
    │   │                                            │     │
    │   └────────────────────────────────────────────┘     │
    │                  Time (bottom x-axis)                │
    └──────────────────────────────────────────────────────┘

The widget answers the user spec: *"Tracking is observing metrics
over time. Have the bottom x-axis be time, and the top x-axis be
pulses."* Each capture pushed in via :meth:`add_capture` becomes
one sample per active (key, metric) pair; the user multi-selects
which channels/combos to overlay and which metrics to show on the
left vs right Y axis.

Shared by :class:`LongPulsingTab` and :class:`ProgressiveStressTab`
— both wrap their experiment-page content in a QTabWidget with a
"Voltage Transient" tab (the existing :class:`MultiChannelScope`)
and a "Tracking" tab (this widget).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import math

import numpy as np
import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from ..session import Capture


# Unicode superscript digits (plus minus sign) so the top-axis
# "Number of Pulses" tick labels render as proper 10ⁿ powers
# without needing HTML rendering (pyqtgraph tick labels are
# plain text).
_SUPERSCRIPT_TR = str.maketrans("0123456789-", "⁰¹²³"
                                                "⁴⁵⁶⁷"
                                                "⁸⁹⁻")


def _format_scientific(value: float) -> str:
    """Format ``value`` (cumulative pulse count) in scientific
    notation using Unicode superscript exponents — e.g.
    ``3600`` → ``"3.6×10³"``. Small values (< 10) render as plain
    integers / one-decimal floats so the label doesn't clutter
    with a trivial ``10⁰`` exponent."""
    import math
    if not math.isfinite(value) or value <= 0:
        return "0"
    if value < 10.0:
        if value < 1.0:
            return f"{value:.2f}"
        return f"{value:.1f}".rstrip("0").rstrip(".") or "0"
    exp = int(math.floor(math.log10(value)))
    mant = value / (10.0 ** exp)
    mant_str = f"{mant:.1f}"
    if mant_str.endswith(".0"):
        mant_str = mant_str[:-2]
    return f"{mant_str}×10" + str(exp).translate(_SUPERSCRIPT_TR)


def _scientific_pulse_ticks(t_lo: float, t_hi: float,
                            rate_hz: float) -> list:
    """Generate ``[(t, label)]`` ticks evenly spaced across the
    visible time range ``[t_lo, t_hi]`` (seconds), each labelled
    with the cumulative pulse count in SCIENTIFIC NOTATION
    (Unicode-superscript exponents). Shared with the staircase
    plot's equivalent helper."""
    if rate_hz <= 0 or t_hi <= t_lo:
        return []
    step = (t_hi - t_lo) / 5.0
    ticks: list = []
    x = t_lo
    for _ in range(6):
        pulses = max(x * rate_hz, 0.0)
        ticks.append((x, _format_scientific(pulses)))
        x += step
    return ticks


# Track-able metrics. The display label appears in the GUI; the
# accessor pulls the value out of a Capture — either a scalar
# (pre/post-pulse rest potentials) or one entry of a per-phase list
# (V_a / R_a / E_pol). Per-phase lists carry index 0 = excitation
# (phase 1), index 1 = recharge (phase 2) for biphasic patterns;
# monophasic patterns leave index 1 absent, which produces a NaN
# sample that ``add_capture`` filters out — i.e. phase-2 series are
# "available when available" exactly as the user spec asks.
def _per_phase_at(values, idx: int) -> float:
    """Read ``values[idx]`` as a float, returning NaN when the index
    is out of range or the value isn't numeric. Used by the per-phase
    metric accessors so monophasic captures naturally drop their
    phase-2 sample (no entry → NaN → filtered by ``add_capture``)."""
    if isinstance(values, (list, tuple)) and len(values) > idx:
        try:
            return float(values[idx])
        except (TypeError, ValueError):
            return float("nan")
    return float("nan")


# Accessor signature: ``fn(capture: Capture) -> float``. Receives the
# whole Capture so the metric can pull from either ``capture.metrics``
# (post-acquisition derived quantities) or ``capture.pattern`` (the
# stimulus parameters at capture time — needed by I_stim and Q_ph
# which reflect the staircase step's amplitude / charge envelope
# rather than a captured-trace measurement).
def _excite_amplitude_ua(c: "Capture") -> float:
    """|I_stim| — magnitude of the excitation phase's amplitude at
    capture time. For staircase / ramp experiments this is the
    current step's level on the y-axis, which makes it the natural
    "x-axis label of the stim level" when plotted against time."""
    pat = getattr(c, "pattern", None)
    if pat is None or not getattr(pat, "phases", None):
        return float("nan")
    try:
        return abs(float(pat.phases[0].amplitude_ua))
    except (TypeError, ValueError, AttributeError):
        return float("nan")


def _excite_charge_per_phase_nc(c: "Capture") -> float:
    """Q_ph — charge per phase using the shape-aware integral
    (``Phase.charge_nc``, which multiplies amp × width × duty).
    For rectangular this equals amp × width / 1000; for curved
    shapes it picks up the natural duty factor (linear = 0.5,
    sin = 2/π, Gaussian ≈ 0.472, exp-decay ≈ 0.199, etc.)."""
    pat = getattr(c, "pattern", None)
    if pat is None or not getattr(pat, "phases", None):
        return float("nan")
    try:
        return abs(float(pat.phases[0].charge_nc))
    except (TypeError, ValueError, AttributeError):
        return float("nan")


#: Per-metric metadata: ``(display_label, short, accessor, unit)``.
#: The ``unit`` string is what the metric is stored in, read off
#: the field name suffix (``_v`` → V, ``_kohm`` → kΩ, etc.) — it
#: is appended to the GUI label so the operator can tell at a
#: glance what they're plotting, and it's surfaced via
#: :meth:`TrackingPlot.metric_unit` for the y-axis labels.
TRACKED_METRICS: Tuple[Tuple[str, str, callable, str], ...] = (
    ("I_stim (excitation amplitude)", "I_stim",
     _excite_amplitude_ua, "µA"),
    ("Q_ph (charge per phase)", "Q_ph",
     _excite_charge_per_phase_nc, "nC"),
    ("V_d (driving voltage)", "V_d",
     lambda c: float(c.metrics.driving_voltage_v), "V"),
    ("V_a (access voltage, ph1)", "V_a_ph1",
     lambda c: _per_phase_at(c.metrics.access_voltage_per_phase_v, 0), "V"),
    ("V_a (access voltage, ph2)", "V_a_ph2",
     lambda c: _per_phase_at(c.metrics.access_voltage_per_phase_v, 1), "V"),
    ("R_a (access resistance, ph1)", "R_a_ph1",
     lambda c: _per_phase_at(c.metrics.access_resistance_per_phase_kohm, 0), "kΩ"),
    ("R_a (access resistance, ph2)", "R_a_ph2",
     lambda c: _per_phase_at(c.metrics.access_resistance_per_phase_kohm, 1), "kΩ"),
    ("E_pol (polarization, ph1)", "E_pol_ph1",
     lambda c: _per_phase_at(c.metrics.polarization_per_phase_v, 0), "V"),
    ("E_pol (polarization, ph2)", "E_pol_ph2",
     lambda c: _per_phase_at(c.metrics.polarization_per_phase_v, 1), "V"),
    ("Q_inj (charge density)", "Q_inj",
     lambda c: float(c.metrics.charge_injection_mc_per_cm2), "mC/cm²"),
    ("C_d (driving capacitance)", "C_d",
     lambda c: float(c.metrics.driving_capacitance_mf_per_cm2), "mF/cm²"),
    ("E_ip, pre-pulse", "E_ip_pre",
     lambda c: float(c.metrics.return_pre_pulse_potential_v), "V"),
    ("E_ip, post-pulse", "E_ip_post",
     lambda c: float(c.metrics.return_post_pulse_potential_v), "V"),
)


# Distinct hues across the metric list so the user can visually
# separate overlapping channels. Index-into via the metric position
# in TRACKED_METRICS; channel discrimination comes from line style
# (solid / dashed / dotted / dash-dot) layered on top of the colour.
_METRIC_COLOURS = (
    "#1976d2",  # blue — V_d
    "#e57373",  # red — V_a
    "#26a69a",  # teal — R_a
    "#ab47bc",  # purple — E_pol
    "#ff8f00",  # orange — Q_inj
    "#5e35b1",  # deep purple — C_eff
    "#43a047",  # green — E_rest_pre
    "#00897b",  # darker teal — E_rest_post
    "#6d4c41",  # brown — k_Shannon
)

_LINE_STYLES = (
    QtCore.Qt.PenStyle.SolidLine,
    QtCore.Qt.PenStyle.DashLine,
    QtCore.Qt.PenStyle.DotLine,
    QtCore.Qt.PenStyle.DashDotLine,
    QtCore.Qt.PenStyle.DashDotDotLine,
)


# Axis-assignment sentinels — same naming as the multichannel scope
# so the user sees consistent terminology.
AXIS_NA = "off"
AXIS_LEFT = "left"
AXIS_RIGHT = "right"


class TrackingPlot(QtWidgets.QWidget):
    """Metric-over-time plot with multi-channel / multi-metric overlay.

    Construction is cheap — no data until :meth:`add_capture` starts
    streaming. The plot starts empty (axis ranges set on first sample).
    A capture's wall-clock seconds-from-start are derived from its
    ``timestamp`` field; the pulse count comes from the capture's
    pattern rate × seconds-from-start, or from
    :attr:`Capture.metrics.pulses_so_far` if a future runner pushes
    it.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        # ---- per-(key, metric) sample storage ----
        # ``_series[(key, short)] = list of (t_seconds, n_pulses, value)``
        # tuples appended in arrival order. Plot curves rebuild from this
        # on every selection / capture event.
        self._series: Dict[Tuple[object, str], List[Tuple[float, float, float]]] = {}
        # Map of (key, short) → live ``pg.PlotDataItem`` so we can
        # update existing curves with ``setData`` instead of clearing
        # and re-adding (avoids legend churn + repaint storms).
        self._curves: Dict[Tuple[object, str], pg.PlotDataItem] = {}
        # ``_t0`` — wall-clock seconds anchor. The first incoming
        # capture defines t = 0; subsequent captures are relative
        # to it. Reset by :meth:`clear`.
        self._t0: Optional[float] = None
        # Cumulative pulse count per key — derived from rate × dt
        # when the capture itself doesn't carry an explicit count.
        self._cum_pulses: Dict[object, float] = {}
        # Track every (key, last_t_seconds) seen so we can compute the
        # delta-time for pulse-count accumulation.
        self._last_t: Dict[object, float] = {}
        # Electrode area used to convert I_stim µA → A/cm² when
        # the user toggles I_stim's unit. Set via
        # :meth:`set_array` (the experiment tab forwards array
        # changes here); fall back to the small-electrode default
        # of 5000 µm² (= 5e-5 cm²) the rest of the GUI uses when
        # no array is loaded.
        self._area_cm2: float = 5e-5
        # I_stim unit toggle. "µA" plots the raw amplitude in
        # microamps; "A/cm²" divides by ``self._area_cm2`` to
        # plot current density. Only I_stim has multiple
        # plotting units — every other metric is stored in one
        # canonical unit (see ``TRACKED_METRICS``).
        self._i_stim_unit: str = "µA"

        # ---- top bar: channel multi-select + metric axes ----
        self._key_checks: Dict[object, QtWidgets.QCheckBox] = {}
        self._metric_axes: Dict[str, str] = {short: AXIS_NA
                                             for _, short, _, _ in TRACKED_METRICS}
        # Default: enable a sensible starter set on the left axis so the
        # plot isn't blank on first launch. R_a phase 1 is the canonical
        # "is the electrode-tissue interface stable?" metric; V_d
        # complements it as the overall driving voltage. E_ip pre-pulse
        # goes on the right axis where the polarization-friendly scale
        # doesn't compete with the V/Ω readouts on the left.
        for short in ("V_d", "R_a_ph1"):
            self._metric_axes[short] = AXIS_LEFT
        for short in ("E_ip_pre",):
            self._metric_axes[short] = AXIS_RIGHT

        self._keys_row = QtWidgets.QHBoxLayout()
        self._keys_row.setContentsMargins(0, 0, 0, 0)
        self._keys_row.setSpacing(6)
        self._keys_row_label = QtWidgets.QLabel("Channels/combos:")
        self._keys_row_label.setStyleSheet("color: #555;")
        self._keys_row.addWidget(self._keys_row_label)
        self._keys_row.addStretch(1)
        keys_w = QtWidgets.QWidget()
        keys_w.setLayout(self._keys_row)

        # Per-metric axis combo. ``self._metric_combos[short]`` =
        # QComboBox with three entries (Off / Left / Right). User
        # change triggers a full curve rebuild via
        # :meth:`_on_axis_changed`.
        self._metric_combos: Dict[str, QtWidgets.QComboBox] = {}
        # Per-row metric label widgets — stashed so the I_stim
        # row can update its label text when the unit toggle
        # flips between "µA" and "A/cm²".
        self._metric_labels: Dict[str, QtWidgets.QLabel] = {}
        metrics_grid = QtWidgets.QGridLayout()
        metrics_grid.setContentsMargins(0, 0, 0, 0)
        metrics_grid.setHorizontalSpacing(6)
        metrics_grid.setVerticalSpacing(2)
        metrics_grid.addWidget(QtWidgets.QLabel("Metric"), 0, 0)
        metrics_grid.addWidget(QtWidgets.QLabel("Axis"), 0, 1)
        metrics_grid.addWidget(QtWidgets.QLabel("Unit"), 0, 2)
        for row, (label, short, _accessor, unit) in enumerate(
                TRACKED_METRICS, start=1):
            # Display label appends the unit so the operator can
            # see units alongside the metric name (e.g.
            # ``"R_a (access resistance, ph1) [kΩ]"``).
            lbl_widget = QtWidgets.QLabel(f"{label} [{unit}]")
            metrics_grid.addWidget(lbl_widget, row, 0)
            self._metric_labels[short] = lbl_widget
            cb = QtWidgets.QComboBox()
            cb.addItem("Off", userData=AXIS_NA)
            cb.addItem("Left", userData=AXIS_LEFT)
            cb.addItem("Right", userData=AXIS_RIGHT)
            default = self._metric_axes.get(short, AXIS_NA)
            cb.setCurrentIndex({AXIS_NA: 0, AXIS_LEFT: 1, AXIS_RIGHT: 2}[default])
            cb.currentIndexChanged.connect(
                lambda _i, s=short: self._on_axis_changed(s))
            self._metric_combos[short] = cb
            cb.setToolTip(
                f"Plot this metric on the <b>Off</b> (hidden), "
                f"<b>Left</b>, or <b>Right</b> y-axis. Multiple "
                f"metrics can share an axis — units in the axis "
                f"label are deduplicated. Off keeps the data "
                f"recorded (the metric still streams in from new "
                f"captures) so you can toggle it back on later "
                f"without re-running the experiment.")
            metrics_grid.addWidget(cb, row, 1)
            # I_stim gets a unit-toggle button (µA ↔ A/cm²).
            # Other metrics have a static unit shown alongside
            # the label, so the unit column is empty for them.
            if short == "I_stim":
                self._i_stim_unit_btn = QtWidgets.QPushButton("µA")
                self._i_stim_unit_btn.setToolTip(
                    "Toggle I_stim plotting unit between current "
                    "(µA) and current density (A/cm²). Current "
                    "density uses the active electrode area set "
                    "via Setup → Array.")
                self._i_stim_unit_btn.clicked.connect(
                    self._toggle_i_stim_unit)
                metrics_grid.addWidget(
                    self._i_stim_unit_btn, row, 2)
        # Wrap the grid in a VBox with a trailing stretch so the metric
        # rows pack at the TOP of the GroupBox instead of getting
        # spread out across the full available height. Without this,
        # the HBoxLayout that hosts (GroupBox, plot) hands the box
        # the full container height — and a bare QGridLayout
        # distributes its rows across that height with extra padding,
        # which makes the column header drift down toward the middle
        # of the box and the rows slide toward the bottom. The
        # stretch absorbs the surplus vertical space cleanly.
        metrics_w = QtWidgets.QGroupBox("Metrics")
        metrics_v = QtWidgets.QVBoxLayout(metrics_w)
        metrics_v.setContentsMargins(8, 6, 8, 6)
        metrics_v.setSpacing(4)
        metrics_v.addLayout(metrics_grid)
        metrics_v.addStretch(1)
        # Belt-and-braces: cap the box's vertical size policy at
        # Maximum so it never expands past its natural height even
        # if a future layout change drops the inner stretch.
        metrics_w.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred,
                                QtWidgets.QSizePolicy.Policy.Maximum)

        # ---- plot ----
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        # Gridlines default OFF on experiment plots so subtle
        # trace features aren't obscured. The main window's View
        # → Gridlines action toggles them on/off for every
        # experiment plot at once via :meth:`set_grid_visible`.
        self._grid_visible: bool = False
        self.plot.showGrid(x=False, y=False)
        # Axis labels — bottom shows time in seconds, top shows
        # cumulative pulse count (synthesised from time × rate),
        # left shows the metric value (linear axis), right shows
        # the second metric axis (also linear). Matches the
        # staircase plot's "Time (s) / Number of Pulses" labelling
        # so the two figures read consistently.
        self.plot.setLabel("bottom", "Time (s)")
        self.plot.setLabel("left", "Metric value")
        # Dual y-axis: create a second ViewBox sharing the bottom x.
        # Standard pyqtgraph idiom — add an AxisItem on the right
        # and link a second ViewBox.
        self._right_viewbox = pg.ViewBox()
        self.plot.scene().addItem(self._right_viewbox)
        right_axis = self.plot.getPlotItem().getAxis("right")
        right_axis.linkToView(self._right_viewbox)
        self._right_viewbox.setXLink(self.plot.getPlotItem())
        self.plot.showAxis("right")
        self.plot.getPlotItem().getAxis("right").setLabel("Metric value (right)")
        # Keep the right viewbox's geometry in sync with the main
        # plot's so the two axes overlay. pyqtgraph emits this on
        # resize / range change.
        self.plot.getPlotItem().vb.sigResized.connect(self._sync_right_vb)
        # Top axis = pulse count, shown as a label-only axis (no
        # auto-scale of its own — pulse-count grows monotonically
        # alongside time, so we synthesise the tick labels from
        # ``time × rate`` when ticks are requested).
        self.plot.showAxis("top")
        top_axis = self.plot.getPlotItem().getAxis("top")
        top_axis.setLabel("Number of Pulses")
        # When the bottom range changes, recompute the top-axis tick
        # labels to reflect the matching pulse counts. The conversion
        # uses the most-recently-recorded rate across all keys (best-
        # effort; pulses depend on the pattern rate, which can differ
        # by key but typically doesn't in LP/PS sweeps).
        self.plot.getPlotItem().vb.sigXRangeChanged.connect(
            self._refresh_top_axis_ticks)
        self._latest_rate_hz: float = 0.0

        # ---- assemble ----
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(4)
        v.addWidget(keys_w)
        h = QtWidgets.QHBoxLayout()
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(metrics_w, stretch=0)
        h.addWidget(self.plot, stretch=1)
        h_w = QtWidgets.QWidget(); h_w.setLayout(h)
        v.addWidget(h_w, stretch=1)
        # Initial y-axis labels reflect the default-active
        # metrics from above (``V_d``, ``R_a_ph1`` on left;
        # ``E_ip_pre`` on right).
        self._refresh_y_axis_labels()

    # ---- public API ----
    def set_grid_visible(self, visible: bool) -> None:
        """Toggle the plot's gridlines. Called by the main
        window's View → Gridlines action so every experiment
        plot's grid flips together."""
        self._grid_visible = bool(visible)
        self.plot.showGrid(x=self._grid_visible, y=self._grid_visible,
                           alpha=0.25 if self._grid_visible else 0.0)

    def set_array(self, array):
        """Receive the current electrode array from the parent
        experiment tab. Used to convert I_stim µA → A/cm² when
        the user toggles I_stim's display unit. ``array.sites[0]``
        is treated as the "active" site (same convention as
        ``ExperimentTab`` for the damage-screening modal and the
        Q_inj computation).
        """
        if array is None or not getattr(array, "sites", None):
            self._area_cm2 = 5e-5
        else:
            try:
                area_um2 = float(array.sites[0].surface_area_um2)
                self._area_cm2 = max(1e-12, area_um2 / 1e8)
            except (AttributeError, TypeError, ValueError):
                self._area_cm2 = 5e-5
        # If I_stim is currently in A/cm² mode, rebuild so the
        # curve picks up the new area.
        if self._i_stim_unit != "µA":
            self._rebuild_curves()
            self._refresh_y_axis_labels()

    def metric_unit(self, short: str) -> str:
        """Return the unit string for a metric — usually the
        static unit declared in :data:`TRACKED_METRICS`, except
        for I_stim where the active toggle (µA / A/cm²) wins.
        Used by y-axis label rendering.
        """
        if short == "I_stim":
            return self._i_stim_unit
        for _label, s, _acc, unit in TRACKED_METRICS:
            if s == short:
                return unit
        return ""

    def clear(self):
        """Drop all stored samples + curves; reset axes."""
        self._series.clear()
        self._curves.clear()
        self._t0 = None
        self._cum_pulses.clear()
        self._last_t.clear()
        self.plot.getPlotItem().clear()
        self._right_viewbox.clear()

    def add_capture(self, capture: Capture, key):
        """Record metric samples for ``capture`` under ``key``.

        ``key`` matches the multichannel-scope routing key (an int
        channel number or a string combo label) so the user sees
        the same identifier in both panes. The capture's wall-clock
        ``timestamp`` anchors the time axis; the cumulative pulse
        count is derived from the previous capture's offset × the
        pattern rate.
        """
        # Register the key in the channel-multi-select bar on first
        # arrival. Pre-checked so new keys participate in the plot
        # immediately; the user can untick to hide a series without
        # losing the underlying samples.
        if key not in self._key_checks:
            cb = QtWidgets.QCheckBox(self._key_label(key))
            cb.setChecked(True)
            cb.toggled.connect(self._on_key_toggled)
            cb.setToolTip(
                f"Show / hide every metric series belonging to "
                f"{self._key_label(key)}. Unticking hides the "
                f"curves but the underlying samples stay in the "
                f"plot's series store, so re-ticking restores "
                f"them without re-running the experiment.")
            self._key_checks[key] = cb
            # Insert before the trailing stretch (last item in the
            # row). Layout's count - 1 is the stretch; insert at
            # that index so the stretch stays last.
            insert_at = self._keys_row.count() - 1
            self._keys_row.insertWidget(insert_at, cb)

        # Time axis: seconds from t0. First capture defines t0; all
        # subsequent ones are relative to it. Pattern rate is taken
        # from the capture; used for the top-axis pulse-count ticks.
        t_now = capture.timestamp.timestamp() if capture.timestamp else 0.0
        if self._t0 is None:
            self._t0 = t_now
        t_s = max(t_now - self._t0, 0.0)
        rate_hz = float(capture.pattern.rate_hz) if capture.pattern else 0.0
        if rate_hz > 0:
            self._latest_rate_hz = rate_hz

        # Cumulative pulses for this key: prior_count + (dt × rate).
        # First capture for a key starts at 0; subsequent captures
        # accumulate by their elapsed gap × the rate.
        dt = t_s - self._last_t.get(key, t_s)
        prior = self._cum_pulses.get(key, 0.0)
        cum = max(prior + max(dt, 0.0) * rate_hz, 0.0)
        self._cum_pulses[key] = cum
        self._last_t[key] = t_s

        # Append one sample per metric. Even metrics the user has
        # currently set to "Off" get stored so toggling them on later
        # back-fills the curve without losing data. Accessors take
        # the WHOLE Capture (not just ``.metrics``) so stimulus-side
        # quantities like ``I_stim`` and ``Q_ph`` can read from
        # ``capture.pattern`` while post-acquisition metrics read
        # from ``capture.metrics``.
        for _label, short, accessor, _unit in TRACKED_METRICS:
            try:
                val = float(accessor(capture))
            except (TypeError, ValueError, AttributeError):
                val = float("nan")
            if not math.isnan(val):
                self._series.setdefault((key, short), []).append((t_s, cum, val))

        self._rebuild_curves()
        self._refresh_top_axis_ticks()

    # ---- internal ----
    def _key_label(self, key) -> str:
        if isinstance(key, int):
            return f"CH{key:02d}"
        return str(key)

    def _on_key_toggled(self, *_):
        self._rebuild_curves()

    def _on_axis_changed(self, short: str):
        cb = self._metric_combos.get(short)
        if cb is None: return
        self._metric_axes[short] = cb.currentData() or AXIS_NA
        self._rebuild_curves()
        self._refresh_y_axis_labels()

    def _toggle_i_stim_unit(self):
        """Cycle I_stim between µA and A/cm² display units. Raw
        µA samples stay in ``self._series`` untouched — the
        conversion happens at curve-build time, so toggling is a
        cheap UI flip rather than a data invalidation."""
        if self._i_stim_unit == "µA":
            self._i_stim_unit = "A/cm²"
        else:
            self._i_stim_unit = "µA"
        # Update the toggle button face + the metric row label
        # so both reflect the new unit.
        if hasattr(self, "_i_stim_unit_btn"):
            self._i_stim_unit_btn.setText(self._i_stim_unit)
        lbl_widget = self._metric_labels.get("I_stim")
        if lbl_widget is not None:
            lbl_widget.setText(
                f"I_stim (excitation amplitude) "
                f"[{self._i_stim_unit}]")
        self._rebuild_curves()
        self._refresh_y_axis_labels()

    def _convert_sample(self, short: str, raw_value: float) -> float:
        """Apply unit conversion at draw time. Only I_stim under
        the A/cm² toggle is non-identity; everything else just
        returns the stored value untouched."""
        if (short == "I_stim" and self._i_stim_unit == "A/cm²"
                and self._area_cm2 > 0):
            # µA → A: ÷1e6 ; A → A/cm² : ÷ area_cm2.
            return raw_value * 1e-6 / self._area_cm2
        return raw_value

    def _refresh_y_axis_labels(self):
        """Compose y-axis label strings from the units of the
        metrics currently routed to each axis.

        Multiple metrics on the same axis are joined with ``,``;
        empty axes get a placeholder. Concretely:

          * Left/Right with only Q_inj → ``"Q_inj [mC/cm²]"``
          * Left with V_d + R_a_ph1 →
            ``"V_d, R_a_ph1 [V, kΩ]"``

        The unit list lets the operator confirm what scale the
        axis is reading without hovering over each curve.
        """
        left_shorts: list = []
        right_shorts: list = []
        for short, axis in self._metric_axes.items():
            if axis == AXIS_LEFT:
                left_shorts.append(short)
            elif axis == AXIS_RIGHT:
                right_shorts.append(short)

        def _compose(shorts):
            if not shorts:
                return "Metric value"
            units = []
            for s in shorts:
                u = self.metric_unit(s)
                if u and u not in units:
                    units.append(u)
            unit_part = f" [{', '.join(units)}]" if units else ""
            return f"{', '.join(shorts)}{unit_part}"

        self.plot.setLabel("left", _compose(left_shorts))
        self.plot.getPlotItem().getAxis("right").setLabel(
            _compose(right_shorts) + " (right)")

    def _rebuild_curves(self):
        """Re-derive which (key, metric) curves should be drawn and
        update or create the corresponding PlotDataItems. Cheap:
        ``setData`` reuses the existing curve when the (key, metric)
        pair was already on the plot."""
        # Tear down any curve whose (key, metric) is no longer
        # visible (key unchecked OR metric Off). The pyqtgraph plot
        # holds a strong ref; remove from the appropriate ViewBox.
        active_keys = {k for k, cb in self._key_checks.items()
                       if cb.isChecked()}
        wanted = set()
        for (key, short) in self._series:
            if key not in active_keys: continue
            if self._metric_axes.get(short, AXIS_NA) == AXIS_NA: continue
            wanted.add((key, short))
        # Remove stale curves
        for (key, short) in list(self._curves):
            if (key, short) in wanted: continue
            curve = self._curves.pop((key, short))
            try:
                curve.getViewBox().removeItem(curve)
            except Exception:
                # Best-effort — pg sometimes raises during removal
                # of an already-orphaned item.
                pass
        # Add / update active curves
        key_order = list(self._key_checks.keys())
        metric_order = [short for _, short, _, _ in TRACKED_METRICS]
        for (key, short) in sorted(wanted, key=lambda p: (key_order.index(p[0])
                                                          if p[0] in key_order else 99,
                                                          metric_order.index(p[1])
                                                          if p[1] in metric_order else 99)):
            samples = self._series.get((key, short), [])
            if not samples: continue
            ts = np.array([s[0] for s in samples])
            # Apply unit conversion at draw time. For most
            # metrics this is identity; for I_stim it picks up
            # the A/cm² toggle when active.
            vals = np.array([self._convert_sample(short, s[2])
                             for s in samples])
            colour = _METRIC_COLOURS[metric_order.index(short) % len(_METRIC_COLOURS)]
            style = _LINE_STYLES[(key_order.index(key) if key in key_order else 0)
                                  % len(_LINE_STYLES)]
            pen = pg.mkPen(colour, width=2, style=style)
            axis = self._metric_axes.get(short, AXIS_LEFT)
            curve = self._curves.get((key, short))
            if curve is None:
                name = f"{self._key_label(key)} – {short}"
                curve = pg.PlotDataItem(ts, vals, pen=pen, name=name,
                                        symbol="o", symbolSize=4,
                                        symbolBrush=colour)
                self._curves[(key, short)] = curve
                if axis == AXIS_RIGHT:
                    self._right_viewbox.addItem(curve)
                else:
                    self.plot.getPlotItem().addItem(curve)
            else:
                curve.setData(ts, vals)
                curve.setPen(pen)

    def _sync_right_vb(self):
        """Keep the right-axis ViewBox geometry locked to the main
        plot's so the two y-axes overlay cleanly. Wired to
        ``sigResized`` from pyqtgraph."""
        self._right_viewbox.setGeometry(self.plot.getPlotItem().vb.sceneBoundingRect())

    def _bottom_axis_tick_positions(self) -> list:
        """Return the major-tick positions pyqtgraph would place
        on the bottom (Time) axis for the current view range.

        Used by :meth:`_refresh_top_axis_ticks` to mirror the
        positions on the top (Number of Pulses) axis so every
        top tick mark lands at the same x-coordinate as a bottom
        tick mark — the two axes stay aligned at every zoom
        level. ``AxisItem.tickValues`` is the same stateless
        calculator pyqtgraph uses internally, so this is the
        canonical positions list without depending on the bottom
        axis having already repainted.
        """
        bottom_axis = self.plot.getPlotItem().getAxis("bottom")
        vb = self.plot.getPlotItem().vb
        if bottom_axis is None or vb is None:
            return []
        x_lo, x_hi = vb.viewRange()[0]
        if x_hi <= x_lo:
            return []
        # Fall back to a sane size hint before the first paint —
        # ``tickValues`` returns nothing for size=0.
        size_px = max(50.0, float(bottom_axis.size().width()))
        layers = bottom_axis.tickValues(float(x_lo), float(x_hi), size_px)
        if not layers:
            return []
        # ``tickValues`` returns layers densest-spacing-first;
        # the first layer is the major (labelled) one.
        return list(layers[0][1])

    def _refresh_top_axis_ticks(self, *_):
        """Recompute the top-axis tick labels — same x-positions
        as the bottom (Time) axis's major ticks, each labelled
        with the cumulative pulse count in scientific notation
        (Unicode-superscript exponents). Mirroring bottom-tick
        positions (rather than generating independent ones)
        guarantees the two axes' tick marks stay visually
        aligned at every zoom level. Uses
        ``self._latest_rate_hz`` (the most-recent capture's
        pattern rate) to convert time → pulse count."""
        top_axis = self.plot.getPlotItem().getAxis("top")
        if top_axis is None:
            return
        if self._latest_rate_hz <= 0:
            top_axis.setTicks(None)
            return
        positions = self._bottom_axis_tick_positions()
        if not positions:
            top_axis.setTicks(None)
            return
        ticks = [(float(x),
                  _format_scientific(max(float(x) * self._latest_rate_hz, 0.0)))
                 for x in positions]
        top_axis.setTicks([ticks])

    # ---- prefs round-trip ----
    def current_prefs(self) -> dict:
        return {
            "metric_axes": dict(self._metric_axes),
            "keys_visible": {self._key_label(k): cb.isChecked()
                             for k, cb in self._key_checks.items()},
        }

    def restore_prefs(self, p: dict):
        if not isinstance(p, dict): return
        axes = p.get("metric_axes")
        if isinstance(axes, dict):
            for short, axis in axes.items():
                if axis not in (AXIS_NA, AXIS_LEFT, AXIS_RIGHT): continue
                self._metric_axes[short] = axis
                cb = self._metric_combos.get(short)
                if cb is not None:
                    cb.setCurrentIndex({AXIS_NA: 0, AXIS_LEFT: 1,
                                        AXIS_RIGHT: 2}[axis])
        # Re-render y-axis labels so they reflect the restored
        # metric/axis assignment.
        self._refresh_y_axis_labels()
        # Key visibility is restored on demand when keys appear via
        # ``add_capture`` — saved here only for completeness.
