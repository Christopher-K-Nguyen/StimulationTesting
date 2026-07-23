"""Hardware connect / status widget.

Layout:

    ☐ Use simulator
    ●  Stimulator: PlexStim 64-bit  ·  S/N 12345  ·  FW 2.0.4  ·  16 ch
       Scaling: [Default ▾]  ·  V_mon×0.25, I_mon×2.5 V/mA
       [ Initialize ] [ Close ]
    ●  Oscilloscope: Tektronix TBS2204B  ·  USB::…::INSTR
       VISA resource: [______________]
       [ Connect ] [ Disconnect ]
       [ Run Stimulator Verification ]  Last verified: 2026-05-11 14:32

Both halves now have explicit Initialize/Close (or Connect/Disconnect)
buttons — the stimulator no longer auto-opens at startup so the user
can pick a different simulator-vs-real mode without immediately
locking in the DLL handle.

Stimulator scaling preset (Default / NIL) overrides the auto-detected
``v_mon_scaling`` / ``i_mon_scaling`` and is **remembered per serial
number** via the shared prefs JSON, so the next time the user opens
that same physical device the preset they picked last time is applied
automatically.

The widget emits the same signals the rest of the GUI already
subscribes to:

* ``connected(stim, scope)`` — fires when both are open.
* ``disconnected()``         — fires when the scope is closed.
* ``log(str)``               — status messages routed to MainWindow's
                               status bar / shared log pane.
"""
from __future__ import annotations

from typing import Optional

from PyQt6 import QtCore, QtGui, QtWidgets

from ..config import (
    IMON_SCALING_DEFAULT, IMON_SCALING_NIL,
    VMON_SCALING_DEFAULT, VMON_SCALING_NIL,
)
from ..hardware import open_oscilloscope, open_stimulator
from ..hardware.base import Oscilloscope, Stimulator
from . import rich
from .prefs import load_prefs, save_prefs

# Module-local alias: ``rich.make_label`` builds a QLabel that renders
# its text on the widget's vertical centerline so labels share a
# baseline with neighbouring spinboxes / combos.
_make_label = rich.make_label


_DOT_OK   = "#43a047"   # green
_DOT_WARN = "#fb8c00"   # amber (sim mode)
_DOT_OFF  = "#9e9e9e"   # grey


def _scan_scope_probes(scope) -> list:
    """Query every channel's probe info, return the list of channels
    that report a real probe (not a plain BNC cable).

    Each entry is a ``(channel_name, type_token, gain)`` tuple, e.g.
    ``("CH4", "10X", 0.1)``.  Empty list when every input looks like
    direct BNC.  Silent on any per-channel query failure so a
    partially-responsive scope still returns whatever it can answer.
    """
    out = []
    if scope is None:
        return out
    info = getattr(scope, "info", None)
    n_ch = int(getattr(info, "n_channels", 4) or 4)
    probe_info_fn = getattr(scope, "probe_info", None)
    if not callable(probe_info_fn):
        return out
    for i in range(1, n_ch + 1):
        ch = f"CH{i}"
        try:
            pinfo = probe_info_fn(ch) or {}
        except Exception:
            continue
        if pinfo.get("is_probe"):
            out.append((
                ch,
                str(pinfo.get("type") or "?"),
                float(pinfo.get("gain", 1.0)),
            ))
    return out


class _ProbeVerifyDialog(QtWidgets.QDialog):
    """Modal that asks the operator to swap probes for plain BNC and
    re-checks on demand.  Closes automatically when no probes remain.

    Loops until either (a) every channel reports a plain BNC cable —
    the dialog auto-accepts — or (b) the operator clicks *Skip* to
    proceed with probes attached (logged as a warning by the caller).
    """

    def __init__(self, scope, *, parent=None):
        super().__init__(parent)
        self._scope = scope
        self.setWindowTitle("Probe attached on oscilloscope")
        self.setModal(True)
        layout = QtWidgets.QVBoxLayout(self)

        self._intro = QtWidgets.QLabel(
            "<p>The oscilloscope reports a probe attached on one or "
            "more inputs.  This bench expects <b>plain BNC → BNC "
            "cables</b> on every channel — an attenuating probe will "
            "make the displayed volts disagree with the BNC-tip "
            "voltage once the driver forces <tt>PRObe:GAIN 1</tt>.</p>"
            "<p>Swap the affected cables to plain BNC, then click "
            "<b>Re-check</b>.</p>")
        self._intro.setWordWrap(True)
        self._intro.setTextFormat(QtCore.Qt.TextFormat.RichText)
        layout.addWidget(self._intro)

        self._list = QtWidgets.QLabel("")
        self._list.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self._list.setStyleSheet(
            "QLabel { background: #fff3e0; padding: 8px; "
            "border: 1px solid #fb8c00; border-radius: 4px; }")
        layout.addWidget(self._list)

        btn_row = QtWidgets.QHBoxLayout()
        self._btn_recheck = QtWidgets.QPushButton("Re-check")
        self._btn_recheck.setDefault(True)
        self._btn_recheck.clicked.connect(self._on_recheck)
        self._btn_skip = QtWidgets.QPushButton("Skip — proceed with probes")
        self._btn_skip.clicked.connect(self.reject)
        btn_row.addWidget(self._btn_recheck)
        btn_row.addStretch(1)
        btn_row.addWidget(self._btn_skip)
        layout.addLayout(btn_row)

        # Track every re-check outcome so the caller can summarise in
        # the log: e.g. "verified after 3 re-checks" or "skipped with
        # CH3 still attached".
        self.recheck_count: int = 0
        self.final_attached: list = []
        self._refresh_list(initial=True)

    def _refresh_list(self, *, initial: bool = False) -> None:
        attached = _scan_scope_probes(self._scope)
        self.final_attached = attached
        if not attached:
            html = ("<b style='color:#388e3c;'>All inputs now look like "
                    "plain BNC.</b><br>The dialog will close "
                    "automatically.")
            self._list.setText(html)
            # Defer accept() one event-loop tick so the user sees the
            # green confirmation before the dialog disappears.
            QtCore.QTimer.singleShot(400, self.accept)
            return
        rows = "".join(
            f"<li><b>{ch}</b> — type=<tt>{tok}</tt>, "
            f"gain=<tt>{gain:g}</tt></li>"
            for ch, tok, gain in attached
        )
        prefix = "" if initial else "Still detected:<br>"
        self._list.setText(prefix + f"<ul style='margin:0;'>{rows}</ul>")

    def _on_recheck(self) -> None:
        self.recheck_count += 1
        self._refresh_list()


def _serial_has_saved_calibration(serial_number: str) -> bool:
    """Return True iff the on-disk calibration.json was written for
    *serial_number*.  Used by :meth:`ConnectionPanel._do_init_stim` to
    suppress the "Unverified stimulator" warning when a calibration
    payload exists even though the prefs scaling map doesn't have an
    entry yet (e.g. saved on a prior install / by a teammate).
    """
    if not serial_number:
        return False
    try:
        from ..readback_calibration import load_calibration
        cal = load_calibration(stim_serial=serial_number)
        return cal is not None and bool(cal.channels)
    except Exception:
        return False


class ConnectionPanel(QtWidgets.QGroupBox):
    """Stimulator (auto-attached, indicator-only) + oscilloscope (Connect/Disconnect)."""

    connected = QtCore.pyqtSignal(object, object)   # (Stimulator, Oscilloscope)
    disconnected = QtCore.pyqtSignal()
    # Internal signal used to safely marshal the stim-detection result
    # from the background probe thread back to the GUI thread.
    # ``object`` carries bool | None | Exception from the probe.
    _stimDetectResult = QtCore.pyqtSignal(object)
    # Internal signal for marshalling the stim-OPEN result from the
    # background init thread back to the GUI thread.  Carries the opened
    # Stimulator on success, or an Exception on failure.  ``PS_InitAllStim``
    # can hang for many seconds (Sim-2 USB lock / wedged device), so the
    # open() runs off the GUI thread — see ``_do_initialize_stim``.
    _stimInitResult = QtCore.pyqtSignal(object)
    # Watchdog: if a stim init is still running after this long, it's
    # almost always the Plexon Sim-2 / Stim-2 USB lock — log an actionable
    # hint (we can't interrupt the blocking DLL call, so we don't fail).
    _STIM_INIT_WATCHDOG_MS = 15000
    # Internal signal for marshalling scope-connect results from the
    # background thread back to the GUI thread. Carries the opened
    # Oscilloscope on success, or an Exception on failure.
    _scopeConnectResult = QtCore.pyqtSignal(object)
    # Internal signal for marshalling the scope-DETECTION result from the
    # background enumeration thread back to the GUI thread.  Carries a
    # dict describing what VISA enumeration found.  The enumeration runs
    # OFF the GUI thread with a hard timeout because
    # ``pyvisa.ResourceManager().list_resources()`` on the NI-VISA
    # backend can BLOCK INDEFINITELY when the VISA layer is wedged (a
    # stale USB-TMC claim from a crashed process, a scope on the in-box
    # Microsoft usbtmc driver rather than NI-VISA's, or NI-VISA mid-scan)
    # — running it synchronously on the GUI thread froze PULSAR at
    # startup for as long as NI-VISA hung.  See ``_SCOPE_DETECT_TIMEOUT_S``.
    _scopeDetectResult = QtCore.pyqtSignal(object)
    # Hard per-run budget for the whole scope-detection enumeration.  A
    # wedged NI-VISA ``list_resources()`` never returns, so we cap the
    # wait and report "detection timed out" rather than blocking.  The
    # daemon enumeration thread is abandoned (it dies with the process);
    # the GUI never waits on it.
    _SCOPE_DETECT_TIMEOUT_S = 6.0
    # Fires whenever scope connection state flips. Setup tab listens
    # so it can blank out the channel-mapping rows when no scope is up.
    scopeConnected = QtCore.pyqtSignal(bool)
    log = QtCore.pyqtSignal(str)
    # Fires when the user clicks "Run Stimulator Verification". The main
    # window catches it and switches to the embedded Calibration tab — the
    # calibration flow no longer opens a modal dialog.
    calibrationRequested = QtCore.pyqtSignal()

    # ---- bias-module facade signals --------------------------------
    # ConnectionPanel is the single owner of the bias driver (per the
    # user's "Single shared driver, but owned by MainWindow" choice).
    # Per-tab BiasConnector widgets in each experiment's Test Parameters
    # delegate to ``open_bias_module`` / ``close_bias_module`` below and
    # subscribe to these signals to mirror state.  ``self.bias`` is the
    # live driver attribute, ``None`` when not connected.
    biasConnected = QtCore.pyqtSignal(object)   # payload: BiasModuleInfo
    biasDisconnected = QtCore.pyqtSignal()

    # Scaling presets exposed in the dropdown. The "auto" preset
    # picks Default vs NIL based on the substring match against the
    # device's serial number that PlexonStimulator already uses; the
    # other two override the auto-detection.
    SCALE_AUTO    = "Auto-detect"
    SCALE_DEFAULT = "Default (PlexStim 2.0) (V_mon 0.25 V/V · I_mon 2.5 mV/µA)"
    SCALE_NIL     = "NIL (V_mon 1.0 V/V · I_mon 1.0 mV/µA)"

    def __init__(self, simulate_default: bool = True, parent=None):
        super().__init__("Hardware", parent)
        self._stim: Optional[Stimulator] = None
        self._scope: Optional[Oscilloscope] = None
        # Per-serial scaling-preset memory, loaded from prefs and
        # persisted whenever the user changes the dropdown or
        # finishes a calibration sweep. Keyed by the device's
        # reported serial number so swapping in a different
        # physical PlexStim restores its own preset. The map is
        # ALSO consulted on every Initialize: if the connecting
        # serial isn't present, the user gets a one-time warning
        # ("uncalibrated stimulator") and is prompted to run the
        # calibration wizard or manually confirm the preset.
        self._scaling_by_serial: dict = self._load_scaling_memory()
        # True while a background stim init (``PS_InitAllStim``) is in
        # flight.  Guards against a second, concurrent init — the PlexStim
        # DLL is single-producer and the driver's _dll_lock would serialize
        # (deadlock) a second open() anyway.
        self._stim_init_in_flight: bool = False

        # ----- shared simulator toggle -----
        self.simulate = QtWidgets.QCheckBox("Use simulator")
        self.simulate.setChecked(simulate_default)
        self.simulate.toggled.connect(self._on_simulate_toggled)
        self.simulate.setToolTip(
            "Drive a built-in simulated stimulator + oscilloscope "
            "instead of real hardware. Useful for offline GUI work "
            "and protocol design — every experiment runs end-to-end "
            "but no current is delivered. Toggle disconnects any "
            "live hardware first.")

        # ----- stimulator: detection + Initialize / Close -----
        # Two dots in the stimulator section, mirroring the scope
        # half below:
        #   * ``stim_detect_dot`` — *hardware presence*. Driven by
        #     :func:`stimtest.hardware.plexstim_detect.plexstim_device_present`,
        #     which probes Windows PnP for a Plexon-named USB
        #     device. Refreshed on every USB hot-plug event by the
        #     filter in :mod:`stimtest.gui.usb_hotplug`. This is
        #     SOFTWARE-installed-detection-independent — the
        #     launch-time SDK warning still covers the "no DLL on
        #     this machine" case separately.
        #   * ``stim_dot`` — *connected / initialized state*. Reflects
        #     whether ``Initialize`` has actually opened a session
        #     against the device.
        # The two indicators answer different questions: "is the
        # cable plugged in?" vs "have we opened a session?". A user
        # debugging "why doesn't Initialize work?" looks at
        # detect_dot first; a user wondering "is my run actually
        # using real hardware?" looks at stim_dot.
        self.stim_detect_dot = self._make_dot(_DOT_OFF)
        self.stim_detect_dot.setToolTip(
            "Stimulator detection: not yet checked.")
        self.stim_detect_text = _make_label("Stimulator detection pending…")
        self.stim_dot = self._make_dot(_DOT_OFF)
        # Description label — empty until ``_refresh_stim_label`` fills
        # it with S/N + FW + channel count after a successful Initialize.
        # Closing the device clears it again.
        self.stim_label = _make_label("")
        self.stim_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        # Dynamic content (S/N + firmware + channel count + scaling
        # preset) can run long; reflow when the splitter narrows the
        # Hardware panel rather than truncating mid-string.
        self.stim_label.setWordWrap(True)
        self.scaling_combo = QtWidgets.QComboBox()
        for preset in (self.SCALE_AUTO, self.SCALE_DEFAULT, self.SCALE_NIL):
            self.scaling_combo.addItem(preset)
        self.scaling_combo.setEnabled(False)   # only after init
        self.scaling_combo.currentTextChanged.connect(self._on_scaling_changed)
        self.scaling_combo.setToolTip(
            "PlexStim V_mon / I_mon scaling preset.<br><br>"
            "<b>Auto-detect</b> — pick from serial-number "
            "match against the known NIL-class list, falling "
            "back to Default. Safe initial choice; the "
            "stimulator verification tightens this into a verified "
            "entry.<br>"
            "<b>Default (PlexStim 2.0)</b> — 0.25 V/V V_mon, "
            "2.5 mV/µA I_mon. Standard production PlexStim "
            "2.0 devices.<br>"
            "<b>NIL</b> — 1.0 V/V V_mon, 1.0 mV/µA I_mon. "
            "Used by specific NIL-class units. Pick this only "
            "if you've verified the device with stimulator verification or "
            "are certain of its scaling — the readout will be "
            "off by 2.5× if you guess wrong.")
        self.scaling_label = _make_label("")
        self.scaling_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.scaling_label.setStyleSheet("color: #555; font-size: 9pt;")
        # Scaling text ("V_mon = 0.250 V/V, I_mon = 2.5 mV/µA, source:
        # auto-detected from serial") can be a full sentence on a
        # NIL device — reflow on narrow panel widths.
        self.scaling_label.setWordWrap(True)
        self.init_btn = QtWidgets.QPushButton("Initialize")
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.init_btn.clicked.connect(self._do_initialize_stim)
        self.close_btn.clicked.connect(self._do_close_stim)

        # ----- auto-discharge mode toggle -----
        # PlexStim devices ACTIVELY short the electrode to a recovery
        # rail during the post-pulse interval when "auto-discharge" is
        # enabled (the safe default). Disabling it is occasionally
        # useful for measuring open-circuit potential between pulses,
        # but it also means residual charge from any per-pulse
        # imbalance accumulates pulse-to-pulse — drifting the
        # electrode-tissue interface DC offset and risking
        # irreversible faradaic reactions. The toggle starts CHECKED;
        # the toggled-off handler raises a confirmation dialog the
        # FIRST time per session before letting the change take
        # effect. After Initialize, the current state is pushed down
        # to the device.
        # Auto-discharge UI moved to the pattern panel (below the
        # pulse rate row, per user spec). Connection panel still owns
        # the persisted preference + device push, exposed as
        # :meth:`apply_auto_discharge` and :meth:`auto_discharge_pref`
        # so the main window can wire the pattern panel's toggle here.
        # Cache the active state so re-Initialize knows what to push
        # to the freshly-opened device.
        self._auto_discharge_state: bool = self._load_auto_discharge()

        # ----- oscilloscope: detection + connect/disconnect indicators -----
        # Two dots, mirroring the stimulator section: ``scope_detect_dot``
        # is a *detection* indicator (does pyvisa see anything that
        # looks like a scope on the bus?) and ``scope_dot`` is the
        # live *connected* indicator (did Connect actually open a
        # session?). The detection probe runs lazily so a slow VISA
        # backend doesn't block the panel from rendering.
        self.scope_detect_dot = self._make_dot(_DOT_OFF)
        self.scope_detect_dot.setToolTip("Oscilloscope detection: not yet checked.")
        self.scope_detect_text = _make_label("Oscilloscope detection pending…")
        # Guard so repeated refresh calls (hot-plug bursts, tab re-entry)
        # don't stack multiple enumeration threads while one is still
        # running — important because a wedged NI-VISA enumeration leaks
        # its daemon thread, and we don't want to leak one per refresh.
        self._scope_detect_in_flight = False
        self.scope_dot = self._make_dot(_DOT_OFF)
        # Description label — empty until Connect succeeds and fills
        # it with make + model + VISA resource. Cleared on Disconnect.
        self.scope_label = _make_label("")
        # Description text combines vendor + model + VISA resource
        # string (e.g. "TEKTRONIX TBS2204B (USB0::0x0699::0x03C0::...)").
        # Easily 60-80 chars — reflow when the panel narrows.
        self.scope_label.setWordWrap(True)
        # VISA resource picker — editable combo populated with whichever
        # scope-shaped resources the detection probe finds.  Kept editable
        # so users can paste a non-USB resource (e.g. TCPIP) that isn't
        # auto-discovered.  Width-capped so the field can't sprawl across
        # the row and visually overrun the "VISA resource:" label on the
        # left.
        self.scope_resource = QtWidgets.QComboBox()
        self.scope_resource.setEditable(True)
        self.scope_resource.setInsertPolicy(
            QtWidgets.QComboBox.InsertPolicy.NoInsert)
        self.scope_resource.lineEdit().setPlaceholderText(
            "Auto-pick first detected scope (or type a VISA resource)")
        self.scope_resource.setToolTip(
            "Optional VISA resource identifier for the "
            "oscilloscope. Leave blank to let pyvisa pick the "
            "first Tek scope it discovers (the usual case). Set "
            "explicitly when you have multiple scopes on the same "
            "bus and need to pin which one Connect targets. "
            "Format: USB0::0x0699::&lt;model&gt;::&lt;serial&gt;::INSTR "
            "or TCPIP0::&lt;ip&gt;::inst0::INSTR for LAN scopes.")
        # Pin both VISA-resource and the Scaling combo (on the stim row) to
        # the SAME fixed width so the two dropdowns line up vertically and
        # don't sprawl across the row.  Reference string is a long-form
        # TCPIP VISA resource (the worst-case practical length) plus a wide
        # padding so the placeholder text isn't truncated by the dropdown
        # arrow.  Stored on self so the scaling combo can reuse it below.
        _res_fm = self.scope_resource.fontMetrics()
        self._combo_w = (
            _res_fm.horizontalAdvance(
                "TCPIP0::192.168.100.123::inst0::INSTR  (typical placeholder)")
            + 60)
        self.scope_resource.setFixedWidth(self._combo_w)
        self.scaling_combo.setFixedWidth(self._combo_w)
        self.connect_btn = QtWidgets.QPushButton("Initialize")
        self.disconnect_btn = QtWidgets.QPushButton("Close")
        self.disconnect_btn.setEnabled(False)
        self.connect_btn.clicked.connect(self._do_connect_scope)
        self.disconnect_btn.clicked.connect(self._do_disconnect_scope)

        # Fix all four action buttons to the same width so the layout
        # never shifts when button states change or the connect button's
        # text temporarily becomes "Initializing…". Measure via
        # fontMetrics so the width tracks whatever font the platform
        # applies to QPushButtons.
        # Widest TRANSIENT label is "Initializing…".  Size to the LARGER of a
        # fontMetrics-advance estimate and the style's real ``sizeHint()``
        # (which includes the platform button chrome), plus margin.  The
        # advance-only estimate under-measured on the real (DPI-scaled) Windows
        # display and clipped "Initializing…" (operator: "Initializing is not
        # fitting"); sizeHint tracks the real font + content margins so the
        # button is a touch wider and the transient label always fits.
        _fm = self.init_btn.fontMetrics()
        _labels = ("Initializing…", "Initialize", "Close")
        _text_w = max(_fm.horizontalAdvance(_t) for _t in _labels)
        _sh_w = 0
        for _t in _labels:
            self.init_btn.setText(_t)
            _sh_w = max(_sh_w, self.init_btn.sizeHint().width())
        self.init_btn.setText("Initialize")
        _btn_w = max(_text_w + 24, _sh_w) + 16
        for _b in (self.init_btn, self.close_btn,
                   self.connect_btn, self.disconnect_btn):
            _b.setFixedWidth(_btn_w)

        # ----- calibration row -----
        self.calibrate_btn = QtWidgets.QPushButton("Run Verification")
        self.calibrate_btn.setEnabled(False)   # unlocks when stim+scope both connected
        self.calibrate_btn.setToolTip(
            "Initialize the stimulator and connect the oscilloscope first.")
        self.calibrate_btn.clicked.connect(self.calibrationRequested.emit)
        self.cal_label = _make_label("")
        # Calibration status text (date / fit RMSE summary / "not yet
        # calibrated") is dynamic and can run long when multiple
        # channels are reported.
        self.cal_label.setWordWrap(True)
        self._refresh_cal_label()

        # ----- layout -----
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(8, 4, 8, 6)
        v.addWidget(self.simulate)

        # Stimulator section — two rows: the ID / detection line and
        # a single action row that holds Scaling, the live scaling
        # values, and the Initialize / Close buttons. Init/Close
        # inline with Scaling drops one row of vertical space and
        # keeps related controls visually grouped.
        # The ID row reads left-to-right as: hardware-detection dot
        # + status text + (post-Initialize) device description (S/N,
        # FW, channel count) + ``Initialized:`` tag + connected dot.
        # That pairs the same convention used by the scope section
        # below — the "is it on the bus?" indicator on the left, the
        # "is it open?" indicator on the right.
        stim_row = QtWidgets.QHBoxLayout()
        stim_row.addWidget(self.stim_detect_dot)
        stim_row.addWidget(self.stim_detect_text)
        stim_row.addWidget(self.stim_label, stretch=1)
        stim_row.addWidget(_make_label("Initialized"))
        stim_row.addWidget(self.stim_dot)
        v.addLayout(stim_row)
        # Build the two row labels first so we can pin them to the same
        # width, left-aligning the scaling combo with the VISA resource field.
        _scaling_lbl = _make_label("Scaling:")
        _visa_lbl    = _make_label("VISA resource:")
        _lbl_fm = _scaling_lbl.fontMetrics()
        _lbl_w = max(_lbl_fm.horizontalAdvance("Scaling:"),
                     _lbl_fm.horizontalAdvance("VISA resource:")) + 4
        _scaling_lbl.setFixedWidth(_lbl_w)
        _visa_lbl.setFixedWidth(_lbl_w)

        # Indent both dropdowns slightly to the right of the labels so the
        # combos don't sit flush against the label text.  Same gap on both
        # rows (Scaling and VISA resource) keeps them aligned vertically.
        _COMBO_INDENT_PX = 24

        scale_row = QtWidgets.QHBoxLayout()
        scale_row.addWidget(_scaling_lbl)
        scale_row.addSpacing(_COMBO_INDENT_PX)
        scale_row.addWidget(self.scaling_combo)
        scale_row.addWidget(self.scaling_label)
        scale_row.addStretch(1)
        scale_row.addWidget(self.init_btn)
        scale_row.addWidget(self.close_btn)
        v.addLayout(scale_row)

        # (Auto-discharge UI is now in the pattern panel — no row here.)

        # Oscilloscope rows. Same indicator pattern as the stimulator:
        # detection state (dot + plain-text status) on the left, live
        # connection state (label + dot + "Initialized" tag) on the
        # right of the same row.
        scope_row = QtWidgets.QHBoxLayout()
        scope_row.addWidget(self.scope_detect_dot)
        scope_row.addWidget(self.scope_detect_text)
        scope_row.addWidget(self.scope_label, stretch=1)
        scope_row.addWidget(_make_label("Initialized"))
        scope_row.addWidget(self.scope_dot)
        v.addLayout(scope_row)

        scope_ctrls = QtWidgets.QHBoxLayout()
        scope_ctrls.addWidget(_visa_lbl)
        scope_ctrls.addSpacing(_COMBO_INDENT_PX)
        scope_ctrls.addWidget(self.scope_resource)
        scope_ctrls.addStretch(1)
        scope_ctrls.addWidget(self.connect_btn)
        scope_ctrls.addWidget(self.disconnect_btn)
        v.addLayout(scope_ctrls)

        cal_row = QtWidgets.QHBoxLayout()
        cal_row.addWidget(self.calibrate_btn)
        cal_row.addWidget(self.cal_label, stretch=1)
        v.addLayout(cal_row)

        # ----- INTERSTELLAR (STM32 interpulse-bias module) ----------
        # INTERSTELLAR is the experimental interpulse-bias module: an
        # STM32 board that applies a DC bias to the return electrode
        # between stimulation pulses.  Its CONNECT / DISCONNECT control
        # lives HERE, directly below the oscilloscope, so the operator
        # brings up every bench data source (stim, scope, camera, bias)
        # from this one Hardware panel.  The closed-loop FEEDBACK CONFIG
        # (setpoint / tolerance / gating window) stays per-experiment in
        # each tab's "Test parameters" page — those parameters are
        # per-run, so they belong with the run, whereas the connection
        # is a bench-setup concern that belongs here.
        #
        # ConnectionPanel OWNS the single shared driver: this connector
        # runs in host-delegated mode against the facade methods +
        # signals defined further down (:meth:`open_bias_module`,
        # :meth:`close_bias_module`, ``biasConnected`` /
        # ``biasDisconnected``).  Every per-tab BiasFeedbackPanel
        # subscribes to those same signals to hide/show its config when
        # INTERSTELLAR connects/disconnects.  ``self.bias`` is the live
        # driver mirror (``None`` when disconnected) that main_window +
        # experiment runners read.
        self.bias = None
        # OPT-IN BUILD GATE — INTERSTELLAR is experimental, so the whole
        # connector is built ONLY when the feature flag is enabled.  The
        # public installer ships with it OFF, so a normal install shows
        # no bias UI at all (stimtest.feature_flags — see gotcha #103).
        from ..feature_flags import (
            INTERSTELLAR_DISPLAY_NAME, interstellar_enabled)
        if interstellar_enabled():
            from .bias_panel import BiasConnector
            # ``host=self`` → the connector delegates Connect/Disconnect
            # to THIS panel's open_bias_module/close_bias_module facade
            # and mirrors state from its biasConnected/biasDisconnected
            # signals.  Exposed as ``self.bias_connector`` so tests and
            # main_window can reach it.
            self.bias_connector = BiasConnector(self, host=self)
            # Route the connector's command-level log lines into the
            # panel's log pipe (same as the camera / scope / stim
            # traffic), so they land in the MainWindow LogPane.
            self.bias_connector.log.connect(self.log.emit)
            interstellar_group = QtWidgets.QGroupBox(
                f"{INTERSTELLAR_DISPLAY_NAME} (interpulse-bias module)")
            interstellar_group.setToolTip(
                "Experimental interpulse-bias module.  Connect it here, "
                "then the per-experiment closed-loop feedback options "
                "appear on each Test Parameters page.  When it is not "
                "connected, those options stay hidden.")
            _ig = QtWidgets.QVBoxLayout(interstellar_group)
            _ig.setContentsMargins(8, 4, 8, 4)
            _ig.setSpacing(6)
            _ig.addWidget(self.bias_connector)
            v.addWidget(interstellar_group)

        # ----- Camera (bench monitor) -------------------------------
        # The camera is part of the bench-instrument cluster (alongside
        # the stim and the scope) per the user's spec — connecting it
        # here means the operator initialises ALL the data sources
        # they'll use during a run from one panel.  The actual live-
        # preview widget is embedded in each experiment tab beneath
        # the scope plot (see ``CameraStreamPane`` in
        # :mod:`stimtest.gui.camera`) so the bench view sits next to
        # the captured waveforms during a run.
        #
        # Lazy import — ``camera`` pulls in QtMultimedia indirectly
        # via ``camera_service()``'s first call, NOT at module-import
        # time.  Putting the import here means ConnectionPanel.__init__
        # is the first place to touch QtMultimedia, but only when the
        # user has opened the Setup tab.  Cold launch is unaffected
        # for sessions that never open Setup (rare but possible —
        # e.g. running from a saved-prefs profile via CLI).
        from .camera import CameraConnector, CameraStreamPane
        self.camera_connector = CameraConnector(parent=self)
        # Mirror camera log lines to the connection-panel's log signal
        # so they land in the MainWindow LogPane alongside scope /
        # stim traffic.
        self.camera_connector.log.connect(self.log.emit)
        # Live preview pane — appears AS SOON AS the camera connects
        # (not just when the operator navigates to an experiment tab),
        # so the bench view is visible right next to the connect
        # controls.  The pane shares the same singleton
        # ``camera_service()`` as the per-experiment stream panes —
        # no additional camera I/O.  Carries a LIVE / RECORDING
        # status badge that flips colour to make recording state
        # unambiguous: the operator should NEVER mistake the live
        # preview for an active recording.
        self.camera_preview = CameraStreamPane(
            parent=self, allow_snapshot_button=True)
        # Pin a comfortable preview height so the pane doesn't
        # collapse to a one-line strip on smaller screens.  Operator
        # can still resize the parent dock / window; this just sets
        # the default footprint.
        self.camera_preview.setMinimumHeight(180)
        camera_group = QtWidgets.QGroupBox("Camera")
        camera_group.setToolTip(
            "Optional bench-monitor camera.  When connected, the live "
            "preview below shows the streaming feed and a LIVE / "
            "RECORDING badge tells you whether frames are being "
            "saved to disk.  Recording is OFF by default — turn it "
            "on per-experiment via 'Record MP4 video for the entire "
            "run' in the Test Parameters tab.  Snapshot stays "
            "available regardless.")
        _cg = QtWidgets.QVBoxLayout(camera_group)
        _cg.setContentsMargins(8, 4, 8, 4)
        _cg.setSpacing(6)
        _cg.addWidget(self.camera_connector)
        _cg.addWidget(self.camera_preview, stretch=1)
        v.addWidget(camera_group)

        # First-time hardware-presence probes — both run on the next
        # event-loop tick so the panel finishes laying out before
        # we touch the VISA backend / shell out to PowerShell.
        # Cheap on both sides (descriptor enumeration only, no
        # device open). Stim-2 SOFTWARE detection (the SDK / DLL
        # warning) is handled at launch by the dialog in
        # ``_check_plexstim_prereq`` — these in-panel dots are for
        # live hardware presence only.
        QtCore.QTimer.singleShot(0, self._refresh_scope_detection_indicator)
        QtCore.QTimer.singleShot(0, self._refresh_stim_detection_indicator)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _make_dot(color: str) -> QtWidgets.QLabel:
        """Small filled circle drawn via QSS background. Fixed size so
        it never expands and pushes the label off-screen. The dot is
        constructed with the bare ``QtWidgets.QLabel`` (no rich-text
        helper) because it carries no text — only a styled background
        shape."""
        dot = QtWidgets.QLabel()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(
            f"background:{color}; border-radius: 7px; "
            f"border: 1px solid #555;"
        )
        return dot

    def _set_dot(self, dot: QtWidgets.QLabel, color: str):
        dot.setStyleSheet(
            f"background:{color}; border-radius: 7px; border: 1px solid #555;"
        )

    # ------------------------------------------------------------- stim
    def _do_initialize_stim(self):
        """Open the stimulator on a WORKER THREAD, then apply scaling.

        ``PS_InitAllStim`` is a blocking DLL call that can hang for many
        seconds — or indefinitely — when the Plexon Sim-2 / Stim-2 GUI is
        holding the exclusive USB lock (or the device is wedged after that
        GUI was force-killed mid-session).  Running it on the GUI thread
        froze the whole app ("Not Responding") — a CWRU operator hit
        exactly this, then force-quit + relaunched 13 times.  So the
        open() runs on a background thread (mirroring
        :meth:`_do_connect_scope`); the result is marshalled back on the
        GUI thread via ``_stimInitResult``, and a watchdog logs a
        "close Sim-2 / power-cycle" hint if the init is still running
        after ~15 s.  The window stays responsive throughout.
        """
        if self._stim_init_in_flight:
            # A previous init is still running on the worker thread.  The
            # PlexStim DLL is single-producer and the driver's _dll_lock
            # would serialize (deadlock) a second open() — never start a
            # concurrent init; just remind the user how to clear a stall.
            self.log.emit(
                "Stimulator initialization already in progress — if it's "
                "stuck, close the Plexon Stim-2 application and "
                "power-cycle the stimulator, then retry.")
            return
        if self._stim is not None:
            # Already open — close first so a re-init refreshes info.
            self._do_close_stim()
        sim = self.simulate.isChecked()
        import time as _time
        self._stim_init_t0 = _time.perf_counter()
        self.log.emit(
            f"Initializing stimulator ({'simulator' if sim else 'PlexStim hardware'})…")

        # Lock out a concurrent init + show a visible "working" state.
        self._stim_init_in_flight = True
        self.init_btn.setEnabled(False)
        self.init_btn.setText("Initializing…")
        self._set_dot(self.stim_dot, _DOT_WARN)

        # Wire the result handler.  UniqueConnection → exactly one binding
        # even if a previous disconnect silently failed (mirrors
        # ``_do_connect_scope`` — prevents an N-times fire on rapid
        # re-press).
        try:
            self._stimInitResult.disconnect(self._on_stim_init_result)
        except (RuntimeError, TypeError):
            pass
        try:
            self._stimInitResult.connect(
                self._on_stim_init_result,
                QtCore.Qt.ConnectionType.UniqueConnection)
        except TypeError:
            pass

        # Thread-safe log emitter for the worker thread.  Emitting
        # ``self.log`` directly from a Python thread is documented as
        # thread-safe but has been observed to silently drop in this
        # stack, so route SDK-call logging through ``_emit_log_from_worker``
        # via QMetaObject.invokeMethod (QueuedConnection) — same as
        # ``_do_connect_scope``.
        panel = self

        def _log_emit(msg: str) -> None:
            try:
                QtCore.QMetaObject.invokeMethod(
                    panel, "_emit_log_from_worker",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, str(msg)))
            except Exception:
                pass

        def _thread():
            try:
                # The PlexStim DLL is vendored inside the package
                # (``stimtest/hardware/pyplexstim/bin/``); the loader
                # picks it up automatically.  Hook the SDK-call logger
                # BEFORE open() so every PS_InitAllStim / PS_GetNStim /
                # probe during the handshake reaches the LogPane + .txt
                # mirror.
                stim = open_stimulator(simulate=sim)
                try:
                    stim.cmd_logger = _log_emit
                except Exception:
                    pass
                stim.open()
            except Exception as exc:
                stim = exc
            self._stimInitResult.emit(stim)

        import threading
        threading.Thread(target=_thread, daemon=True).start()

        # Watchdog: if the init is still running after ~15 s, it's almost
        # always the Sim-2 USB lock.  Log an actionable hint — we can't
        # interrupt the blocking DLL call, and starting a second init
        # would race the DLL, so we do NOT declare failure.  The button
        # stays disabled via ``_stim_init_in_flight`` until the worker
        # actually returns.
        QtCore.QTimer.singleShot(
            self._STIM_INIT_WATCHDOG_MS, self._on_stim_init_slow)

    @QtCore.pyqtSlot()
    def _on_stim_init_slow(self):
        """Watchdog fired ~15 s after an init began.  If it's STILL
        running, tell the operator what almost always causes it."""
        if not self._stim_init_in_flight:
            return   # init already finished — nothing to warn about
        self.log.emit(
            "Stimulator initialization is taking longer than expected. "
            "This almost always means the Plexon Stim-2 "
            "application is holding the USB lock. Close it (via Task "
            "Manager if needed), then power-cycle the stimulator. The "
            "window stays responsive — you can retry once it's cleared.")

    @QtCore.pyqtSlot(object)
    def _on_stim_init_result(self, result):
        """Apply the background stim-open result on the GUI thread.

        ``result`` is the opened Stimulator on success, or the Exception
        the worker caught.  All Qt-widget + SDK follow-up work (scaling
        preset, auto-discharge, ``connected`` emit) stays here on the GUI
        thread; only the blocking ``open()`` ran on the worker.
        """
        self._stim_init_in_flight = False
        self.init_btn.setText("Initialize")
        try:
            self._stimInitResult.disconnect(self._on_stim_init_result)
        except (RuntimeError, TypeError):
            pass
        import time as _time
        from ..hardware.tektronix import _fmt_elapsed
        _t0 = getattr(self, "_stim_init_t0", _time.perf_counter())
        _elapsed = _fmt_elapsed(_time.perf_counter() - _t0)
        if isinstance(result, Exception):
            self._stim = None
            self._set_dot(self.stim_dot, _DOT_OFF)
            self.stim_label.setText(f"Stimulator: failed — {result}")
            self.log.emit(f"Stimulator open failed: {result}   ({_elapsed})")
            self.scaling_combo.setEnabled(False)
            self.close_btn.setEnabled(False)
            self.init_btn.setEnabled(True)
            return
        self._stim = result
        info = self._stim.info
        # Apply any remembered scaling preset for this serial. The
        # combo is set FIRST (signals blocked) so we can then call
        # _apply_scaling_preset without re-entering the slot.
        # ``known_serial`` records whether this device has a
        # validated entry in the shared scaling database — if not,
        # the user gets a warning popup below telling them to
        # calibrate (or to confirm the preset manually if they are
        # certain). Serials are added to the database either by
        # the calibration wizard (via ``_record_serial_scaling``)
        # or by the user explicitly picking a preset from the
        # combo (via ``_on_scaling_changed``).
        sn = (info.serial_number or "").strip()
        preset = self._scaling_by_serial.get(sn, self.SCALE_AUTO)
        if preset not in (self.SCALE_AUTO, self.SCALE_DEFAULT, self.SCALE_NIL):
            preset = self.SCALE_AUTO
        self.scaling_combo.blockSignals(True)
        try:
            self.scaling_combo.setCurrentText(preset)
        finally:
            self.scaling_combo.blockSignals(False)
        self._apply_scaling_preset(preset)
        # Push the user's auto-discharge preference down to the device
        # — the SDK resets this on PS_InitAllStim, so we have to apply
        # it after every Initialize. Sim backend's set_auto_discharge
        # is a no-op so this is safe in either mode.
        try:
            self._stim.set_auto_discharge(self._auto_discharge_state)
        except Exception as e:
            self.log.emit(f"Auto-discharge apply failed: {e}")
        self._set_dot(self.stim_dot, _DOT_WARN if info.is_simulated else _DOT_OK)
        # A successful open is DEFINITIVE proof the device is present (we just
        # enumerated it and read its S/N), so reconcile the pre-init PnP
        # DETECTION indicator too.  Otherwise a machine where the
        # ``Get-PnpDevice`` probe timed out / couldn't match the USB
        # InstanceId leaves a stale amber "Stimulator detection unavailable"
        # sitting next to the green "Initialized" — a contradictory display
        # that reads as "the stimulator is broken" even though it opened fine
        # (observed at CWRU).  Real hardware only; the simulator has no
        # physical presence to confirm.
        if not info.is_simulated:
            self._set_dot(self.stim_detect_dot, _DOT_OK)
            _dtip = ("Stimulator opened successfully (S/N "
                     f"{info.serial_number or 'n/a'}) — device confirmed "
                     "present by the driver.  (The pre-init OS PnP probe is "
                     "advisory only and does not gate Initialize.)")
            self.stim_detect_text.setText("Stimulator detected")
            self.stim_detect_dot.setToolTip(_dtip)
            self.stim_detect_text.setToolTip(_dtip)
        self._refresh_stim_label()
        self.scaling_label.setVisible(False)
        self.scaling_combo.setEnabled(True)
        self.init_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        self._update_calibrate_btn()
        self.log.emit(
            f"Stimulator initialized: {info.description or 'sim stim'} "
            f"(S/N {info.serial_number or 'n/a'})   "
            f"({_elapsed})")
        # If a scope is already connected, refresh the connected signal
        # so subscribers see both halves.
        if self._scope is not None:
            self.connected.emit(self._stim, self._scope)
        # Stimulator VERIFICATION IS OPTIONAL (operator) — no modal nag when a
        # serial isn't in the shared scaling database.  The scaling preset is
        # auto-detected (a serial in ``NIL_SERIAL_NUMBERS`` applies NIL, else
        # Default) or restored from the remembered per-serial choice; a
        # verification sweep is not required to run.  Log the applied preset so
        # the operator still has a record of what scaling is in effect.
        if not info.is_simulated:
            self.log.emit(
                f"Stimulator scaling: {self.scaling_combo.currentText()} "
                f"(verification optional — not required to run).")

    def _do_close_stim(self):
        """Close the stimulator handle and reset the indicator."""
        if self._stim is None:
            return
        # The scope can't run without the stimulator's monitor outputs,
        # so drop the scope side first if it's up.
        if self._scope is not None:
            self._do_disconnect_scope()
        try:
            self._stim.close()
        except Exception as e:
            self.log.emit(f"Stimulator close raised: {e}")
        finally:
            self._stim = None
            self._set_dot(self.stim_dot, _DOT_OFF)
            self.stim_label.setText("")
            self.scaling_label.setText("")
            self.scaling_label.setVisible(True)
            self.scaling_combo.setEnabled(False)
            self.scaling_combo.blockSignals(True)
            try:
                self.scaling_combo.setCurrentText(self.SCALE_AUTO)
            finally:
                self.scaling_combo.blockSignals(False)
            self.init_btn.setEnabled(True)
            self.close_btn.setEnabled(False)
            self._update_calibrate_btn()
            self.log.emit("Stimulator closed.")

    def _on_simulate_toggled(self, _checked: bool):
        # Drop the scope first because real-vs-sim isn't compatible
        # mid-flight, then close the stim. The user has to click
        # Initialize again to bring it up under the new mode.
        if self._scope is not None:
            self._do_disconnect_scope()
        if self._stim is not None:
            self._do_close_stim()
        # Re-probe both detection indicators — the user may have
        # changed VISA resources or plugged / unplugged hardware
        # while the toggle was on. (Stim-2 software detection is
        # launch-time only; it's not affected by the simulate
        # toggle.)
        self._refresh_scope_detection_indicator()
        self._refresh_stim_detection_indicator()

    # ------- auto-discharge mode -----------------------------------
    def auto_discharge_pref(self) -> bool:
        """Return the persisted auto-discharge preference.

        Used by the main window at startup to seed every pattern
        panel's checkbox before any UI is shown. Defaults to True
        (the safe state) on a clean install.
        """
        return self._auto_discharge_state

    def apply_auto_discharge(self, checked: bool) -> None:
        """Apply a new auto-discharge state to the live device and
        persist it. Called by the main window when the user toggles
        the checkbox in any pattern panel.

        The user-facing warning dialog lives in the pattern panel
        (where the UI is now); by the time this slot runs the user
        has already confirmed the change. We just push to hardware
        + save the prefs.
        """
        self._auto_discharge_state = bool(checked)
        # Push to the live device. Sim backend's set_auto_discharge is
        # a no-op so this is safe whether or not real hardware is open.
        if self._stim is not None:
            try:
                self._stim.set_auto_discharge(self._auto_discharge_state)
            except Exception as e:
                self.log.emit(f"Auto-discharge apply failed: {e}")
        # Persist so a restart reopens with the same preference.
        try:
            self._save_auto_discharge(self._auto_discharge_state)
        except Exception:
            pass
        self.log.emit(
            f"Auto-discharge: "
            f"{'ON' if self._auto_discharge_state else 'OFF (warning acknowledged)'}")

    # ------- scaling preset (per-serial memory) ---------------------
    def _refresh_stim_label(self):
        """Render the multiline stimulator readout from ``self._stim.info``."""
        if self._stim is None:
            self.stim_label.setText("")
            self.scaling_label.setText("")
            return
        info = self._stim.info
        bits = [info.description or info.serial_number or "Stimulator"]
        if info.serial_number:
            bits.append(f"S/N <b>{info.serial_number}</b>")
        if info.firmware:
            bits.append(f"FW <b>{info.firmware}</b>")
        if info.is_simulated:
            bits.append("(<i>sim</i>)")
        self.stim_label.setText(" · ".join(bits))
        # I_mon scaling is stored as V/µA; show V/mA (×1000) since
        # that's the spec-sheet unit and matches the prior label.
        self.scaling_label.setText(
            f"V_mon × <b>{info.vmon_scaling_v_per_v}</b> V/V "
            f"&nbsp;·&nbsp; "
            f"I_mon × <b>{info.imon_scaling_v_per_ua * 1e3:g}</b> V/mA"
        )

    def _apply_scaling_preset(self, preset: str):
        """Push the chosen scaling onto the live stimulator info.

        ``Auto-detect`` keeps whatever ``PlexonStimulator.open()`` chose
        from the serial-number prefix match, then resolves which named
        preset that corresponds to and switches the combo to that entry
        so the user sees the actual scaling in use — not just "Auto-detect".

        The other two presets force the values regardless of serial.
        """
        if self._stim is None:
            return
        info = self._stim.info
        if preset == self.SCALE_DEFAULT:
            info.vmon_scaling_v_per_v = VMON_SCALING_DEFAULT
            info.imon_scaling_v_per_ua = IMON_SCALING_DEFAULT
        elif preset == self.SCALE_NIL:
            info.vmon_scaling_v_per_v = VMON_SCALING_NIL
            info.imon_scaling_v_per_ua = IMON_SCALING_NIL
        else:
            # Auto-detect: the driver already wrote the scaling into info
            # during open(). Resolve which named preset it matches and
            # switch the combo to that entry so the user can see what
            # was detected rather than a generic "Auto-detect" label.
            if (info.vmon_scaling_v_per_v == VMON_SCALING_NIL
                    and info.imon_scaling_v_per_ua == IMON_SCALING_NIL):
                resolved = self.SCALE_NIL
            else:
                resolved = self.SCALE_DEFAULT
            self.scaling_combo.blockSignals(True)
            try:
                self.scaling_combo.setCurrentText(resolved)
            finally:
                self.scaling_combo.blockSignals(False)

    def _on_scaling_changed(self, preset: str):
        """User picked a different scaling preset from the combo."""
        if self._stim is None:
            return
        self._apply_scaling_preset(preset)
        self._refresh_stim_label()
        # Persist the choice keyed by the current serial so the next
        # session of the same physical device reapplies it.
        sn = self._stim.info.serial_number
        if sn:
            self._scaling_by_serial[sn] = preset
            self._save_scaling_memory()
        self.log.emit(f"Stimulator scaling preset → {preset}")

    # ------- detection indicator ------------------------------------
    def refresh_hardware_detection(self):
        """Re-probe what's on the bus and refresh both detection
        indicators. Public so the USB hot-plug filter can call it
        as devices arrive / disappear without poking private
        methods.

        Two probes run on every refresh:

        * **Scope** — :meth:`_refresh_scope_detection_indicator`
          (pyvisa enumeration; matches scope vendor IDs).
        * **Stimulator** — :meth:`_refresh_stim_detection_indicator`
          (PowerShell PnP query for a Plexon-named USB device).

        Stim-2 SOFTWARE detection (registry / DLL loadability) is
        unrelated and runs once at launch (see
        ``_check_plexstim_prereq``); re-running it on every hot-
        plug would just reload the registry for no benefit, since
        the SDK install state doesn't flip with USB activity.
        """
        self._refresh_scope_detection_indicator()
        self._refresh_stim_detection_indicator()

    # ---- bias-module facade ------------------------------------------
    # Per the architecture decision ("Single shared driver, but owned
    # by MainWindow"), ConnectionPanel owns the bias-module lifecycle
    # and exposes open/close methods + Connected/Disconnected signals.
    # Per-tab BiasConnector widgets in each experiment's Test
    # Parameters call these methods and subscribe to the signals.
    #
    # Threading: open/close run synchronously on the GUI thread.  The
    # STM32 ``*IDN?`` handshake is short (<1 s; the protocol has a
    # 1-second timeout) so we don't background-thread it.  If that
    # ever becomes a UX problem, mirror the scope's
    # ``_scopeConnectResult`` background-thread pattern.
    def open_bias_module(self, *, simulate: bool,
                         port: Optional[str] = None) -> None:
        """Open the bias-module driver and publish it as ``self.bias``.

        Raises on failure (caller catches + shows the error message).
        Idempotent: if ``self.bias`` is already non-None, this is a
        silent no-op (caller's intent of "make sure it's open" is
        satisfied).

        After a successful open, ``biasConnected(info)`` is emitted
        and every per-tab BiasConnector that subscribed updates its
        state dot.
        """
        if self.bias is not None:
            self.log.emit("[bias] already connected; open_bias_module ignored")
            return
        from ..hardware import open_bias_module as _factory
        bias = _factory(simulate=simulate, port=port)
        # Wire per-command log forwarding BEFORE open() so the
        # connect handshake itself shows in the LogPane.
        try:
            bias.cmd_logger = self.log.emit
        except Exception:
            pass
        bias.open()
        self.bias = bias
        info = bias.info
        self.log.emit(
            f"[bias] connected: {info.manufacturer} {info.model} "
            f"{info.hardware_id} fw={info.firmware} "
            f"port={info.port} log_cap={info.log_capacity}"
            f"{' (SIMULATED)' if info.is_simulated else ''}")
        self.biasConnected.emit(info)

    def close_bias_module(self) -> None:
        """Close the bias-module driver.  Idempotent — silent no-op
        if not currently connected."""
        if self.bias is None:
            return
        try:
            self.bias.close()
        except Exception as e:
            self.log.emit(
                f"[bias] close raised: {type(e).__name__}: {e}")
        self.bias = None
        self.log.emit("[bias] disconnected")
        self.biasDisconnected.emit()

    def _refresh_stim_detection_indicator(self):
        """Drive the stimulator detect dot from a Windows-PnP probe.

        :func:`stimtest.hardware.plexstim_detect.plexstim_device_present`
        returns:

        * ``True``  → green dot, "Stimulator detected".
        * ``False`` → grey dot, "Stimulator not detected".
        * ``None``  → amber dot, "Stimulator detection unavailable
          on this platform" — non-Windows, missing PowerShell, or
          PnP timeout. The amber state is intentionally distinct
          from "not detected" so a user on Linux / macOS isn't
          told their device is absent when really we just can't
          check.

        Called on every USB hot-plug event (via
        :meth:`refresh_hardware_detection`) and once lazily on
        panel construction. The probe runs in a daemon thread so
        the GUI doesn't block during the 3–6 s that
        ``Get-PnpDevice -PresentOnly`` can take on a loaded
        Windows 11 machine.
        """
        # Set a "checking…" state immediately while the background
        # probe runs (Get-PnpDevice can take 3-6 s on a loaded machine).
        self._set_dot(self.stim_detect_dot, _DOT_WARN)
        self.stim_detect_text.setText("Stimulator detection…")

        # Wire the result signal once (idempotent: disconnect first to
        # avoid double-connecting when refresh_hardware_detection is
        # called repeatedly from the hot-plug filter).
        # UniqueConnection guarantees the slot is connected exactly
        # once — even if a previous disconnect failed silently (which
        # the broad try/except would otherwise hide), we don't end up
        # with the slot bound multiple times and firing N times per
        # detect.  The TypeError raised by UniqueConnection-on-already-
        # connected is the success case we want.
        try:
            self._stimDetectResult.disconnect(self._on_stim_detect_result)
        except (RuntimeError, TypeError):
            pass
        try:
            self._stimDetectResult.connect(
                self._on_stim_detect_result,
                QtCore.Qt.ConnectionType.UniqueConnection)
        except TypeError:
            # Already connected (UniqueConnection conflict).  That's
            # what we want — leave the existing single connection
            # in place rather than stacking another.
            pass

        import threading
        def _thread():
            try:
                from ..hardware.plexstim_detect import plexstim_device_present
                result = plexstim_device_present()
            except Exception as exc:
                result = exc
            # Emit the signal — PyQt6 signals are thread-safe and will
            # deliver the payload on the GUI thread via the event queue.
            # Guard against the panel's C++ object having been deleted
            # while the probe ran (a short-lived widget / test torn down
            # mid-probe): emitting on a deleted QObject raises RuntimeError.
            try:
                self._stimDetectResult.emit(result)
            except RuntimeError:
                pass
        threading.Thread(target=_thread, daemon=True).start()

    @QtCore.pyqtSlot(object)
    def _on_stim_detect_result(self, present):
        """Apply the result from the background PnP probe. Runs on the
        GUI thread (delivered via the ``_stimDetectResult`` signal)."""
        if isinstance(present, Exception):
            self._set_dot(self.stim_detect_dot, _DOT_OFF)
            text = "Stimulator detection failed"
            tip = (f"Detection probe raised: {present}\n\n"
                   "Click Initialize to test the connection directly.")
        elif present is True:
            self._set_dot(self.stim_detect_dot, _DOT_OK)
            text = "Stimulator detected"
            tip = ("A Plexon PlexStim USB device is currently "
                   "enumerated by the operating system. Click "
                   "Initialize to open a session.")
        elif present is False:
            self._set_dot(self.stim_detect_dot, _DOT_OFF)
            text = "Stimulator not detected"
            tip = ("No Plexon stimulator USB device found.\n\n"
                   "If one IS plugged in, check Device Manager for an "
                   "unknown / yellow-flag FTDI entry (VID 0403 PID 6011).")
        else:  # None
            self._set_dot(self.stim_detect_dot, _DOT_WARN)
            text = "Stimulator detection unavailable"
            tip = ("PnP probe timed out or isn't available on this "
                   "platform. Click Initialize to test the connection "
                   "directly.")
        self.stim_detect_text.setText(text)
        self.stim_detect_dot.setToolTip(tip)
        self.stim_detect_text.setToolTip(tip)

    # Vendor-ID prefixes for VISA USB resources.
    # NI-VISA returns hex:  "USB0::0x0699::0x03C7::SERIAL::INSTR"
    # pyvisa-py returns decimal: "USB0::1689::967::SERIAL::0::INSTR"
    # Include both forms so detection works with either backend.
    _SCOPE_VENDOR_IDS = (
        "0x0699", "1689",   # Tektronix (hex / decimal)
        "0x0957", "2391",   # Keysight / Agilent
        "0x0AAD", "2733",   # Rohde & Schwarz
    )

    def _refresh_scope_detection_indicator(self):
        """Light up the scope detection dot from the VISA resource list.

        The actual ``pyvisa.ResourceManager().list_resources()`` call
        runs **on a daemon thread with a hard timeout**
        (:data:`_SCOPE_DETECT_TIMEOUT_S`).  This is deliberate: the
        NI-VISA backend's ``list_resources()`` can **block
        indefinitely** when the VISA layer is wedged (a stale USB-TMC
        claim left by a crashed process, a scope bound to the in-box
        Microsoft ``usbtmc`` driver instead of NI-VISA, or NI-VISA
        mid-scan).  Running it synchronously on the GUI thread — as the
        old code did, via ``singleShot(0)`` at startup — froze PULSAR's
        launch for the entire time NI-VISA hung.  Now the panel renders
        immediately, shows a "checking…" state, and resolves the dot
        when the bounded enumeration returns (or times out).

        We don't *open* any resource here (that would block and could
        steal a device the user is about to talk to from the bench); we
        just look at descriptors.  Heuristic: anything matching a known
        scope vendor ID (Tek ``0x0699``, Keysight ``0x0957``, R&S
        ``0x0AAD``) is "scope detected".  A VISA runtime with resources
        but no scope → amber; none → grey; enumeration timed out → amber
        with a distinct message so the user knows it wasn't a clean
        "no scope" answer.
        """
        # Skip if an enumeration is already running — a wedged NI-VISA
        # call leaks its daemon thread, so we must not stack one per
        # refresh (hot-plug bursts / tab re-entry can fire this rapidly).
        if getattr(self, "_scope_detect_in_flight", False):
            return
        # Immediate "checking…" state so the panel never looks frozen.
        self._set_dot(self.scope_detect_dot, _DOT_WARN)
        self.scope_detect_text.setText("Oscilloscope detection…")

        # Wire the result signal exactly once (UniqueConnection — same
        # idempotent pattern as the stim detect probe).
        try:
            self._scopeDetectResult.disconnect(self._on_scope_detect_result)
        except (RuntimeError, TypeError):
            pass
        try:
            self._scopeDetectResult.connect(
                self._on_scope_detect_result,
                QtCore.Qt.ConnectionType.UniqueConnection)
        except TypeError:
            pass

        self._scope_detect_in_flight = True
        import threading

        def _thread():
            result = self._enumerate_scopes_bounded(
                self._SCOPE_DETECT_TIMEOUT_S)
            # PyQt6 signals are thread-safe; the payload is delivered on
            # the GUI thread via the event queue.  Guard against the panel's
            # C++ object having been deleted while we were enumerating (a
            # short-lived widget / test torn down mid-probe — likely because
            # this enumeration can take up to _SCOPE_DETECT_TIMEOUT_S when
            # VISA is wedged): emitting on a deleted QObject raises
            # RuntimeError.
            try:
                self._scopeDetectResult.emit(result)
            except RuntimeError:
                pass
        threading.Thread(target=_thread, daemon=True).start()

    @classmethod
    def _enumerate_scopes_bounded(cls, budget_s: float) -> dict:
        """Enumerate VISA resources across backends within ``budget_s``.

        Returns a plain dict (marshalled to the GUI thread):
        ``{"no_visa": bool, "resources": tuple, "timed_out": bool,
        "error": str|None}``.  Never raises.  Each backend's
        ``list_resources()`` runs in its own daemon thread so a backend
        that hangs (NI-VISA when wedged) is abandoned at the deadline
        instead of blocking the whole probe.
        """
        try:
            import pyvisa  # noqa: F401
        except Exception as e:  # pragma: no cover - env dependent
            return {"no_visa": True, "resources": (), "timed_out": False,
                    "error": str(e)}

        import time
        deadline = time.monotonic() + max(0.5, float(budget_s))
        resources: tuple = ()
        fallback: tuple = ()
        timed_out = False
        for backend in ("@py", ""):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                timed_out = True
                break
            res, hit_timeout = cls._list_resources_with_timeout(
                backend, remaining)
            if hit_timeout:
                # This backend wedged; note it and try the next one only
                # if there's budget left (the outer loop re-checks).
                timed_out = True
                continue
            if not res:
                continue
            # Prefer the backend that actually finds a scope. @py may see
            # only a serial COM port while NI-VISA sees the USB-TMC scope
            # (or vice-versa when NI-VISA is wedged).
            if any(vid in r for r in res for vid in cls._SCOPE_VENDOR_IDS):
                resources = res
                timed_out = False
                break
            if not fallback:
                fallback = res
        if not resources:
            resources = fallback
        return {"no_visa": False, "resources": tuple(resources),
                "timed_out": timed_out, "error": None}

    @classmethod
    def _list_resources_with_timeout(cls, backend: str, timeout_s: float):
        """Run one backend's ``list_resources()`` in a daemon thread and
        wait at most ``timeout_s``.  Returns ``(resources, timed_out)``.
        A hung enumeration leaves its daemon thread running (it dies with
        the process); we never join it past the deadline."""
        import pyvisa
        import warnings
        import threading
        box: dict = {"res": (), "done": False}

        def _run():
            try:
                # ``list_resources()`` MUST be inside catch_warnings — the
                # pyvisa-py TCPIP discovery UserWarnings fire during
                # enumeration, not during ResourceManager construction.
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    rm = (pyvisa.ResourceManager(backend) if backend
                          else pyvisa.ResourceManager())
                    box["res"] = tuple(rm.list_resources())
            except Exception:
                box["res"] = ()
            finally:
                box["done"] = True

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(max(0.1, float(timeout_s)))
        if not box["done"]:
            return (), True   # wedged — abandon this backend
        return box["res"], False

    @QtCore.pyqtSlot(object)
    def _on_scope_detect_result(self, result):
        """Apply the bounded-enumeration result on the GUI thread."""
        self._scope_detect_in_flight = False
        if not isinstance(result, dict):
            result = {"no_visa": False, "resources": (),
                      "timed_out": False, "error": None}
        if result.get("no_visa"):
            self._set_dot(self.scope_detect_dot, _DOT_OFF)
            text = "VISA backend not available"
            tip = (f"Could not import pyvisa: {result.get('error')}\n\n"
                   "Install the pyvisa runtime (NI-VISA, TekVISA, or the "
                   "bundled pyvisa-py) before connecting a scope.")
            self.scope_detect_text.setText(text)
            self.scope_detect_dot.setToolTip(tip)
            self.scope_detect_text.setToolTip(tip)
            return
        resources = tuple(result.get("resources") or ())
        timed_out = bool(result.get("timed_out"))
        scope_resources = [r for r in resources
                           if any(vid in r for vid in self._SCOPE_VENDOR_IDS)]
        if scope_resources:
            self._set_dot(self.scope_detect_dot, _DOT_OK)
            # Show the VISA address inline so the user can see it
            # without hovering the dot. If multiple scope-shaped
            # resources are on the bus, append "(+N more)" and put
            # the full list in the tooltip.
            primary = scope_resources[0]
            extras = len(scope_resources) - 1
            text = ("Oscilloscope detected"
                    + (f"  (+{extras} more)" if extras else ""))
            tip = ("Detected scope-like VISA resource(s):\n  "
                   + "\n  ".join(scope_resources))
            # Populate the dropdown with every detected scope-shaped
            # resource so the user can pick between multiple scopes on
            # the bus.  Preserve any user-typed entry if they've already
            # entered something not in the list.
            _typed = self.scope_resource.currentText().strip()
            self.scope_resource.blockSignals(True)
            self.scope_resource.clear()
            self.scope_resource.addItems(scope_resources)
            if _typed and _typed not in scope_resources:
                self.scope_resource.addItem(_typed)
                self.scope_resource.setCurrentText(_typed)
            else:
                self.scope_resource.setCurrentText(primary)
            self.scope_resource.blockSignals(False)
        elif timed_out:
            # A backend (typically NI-VISA) did not answer within the
            # budget — the VISA layer is wedged.  Amber + a distinct
            # message so the user knows this is NOT a clean "no scope"
            # answer and Connect may still work (or may need a reboot).
            self._set_dot(self.scope_detect_dot, _DOT_WARN)
            text = "Oscilloscope detection timed out"
            tip = (
                "VISA resource enumeration did not finish within "
                f"{self._SCOPE_DETECT_TIMEOUT_S:.0f}s — the VISA layer is "
                "wedged (a common cause is a stale USB-TMC claim left by a "
                "previous crashed session, or the scope being bound to the "
                "in-box Microsoft usbtmc driver rather than NI-VISA).\n\n"
                "Startup was NOT blocked by this. You can still click "
                "Initialize to try connecting; if it also hangs, reboot "
                "the PC (clears the stale claim) or power-cycle the scope "
                "and replug USB.")
            if resources:
                tip += "\n\nResources seen so far:\n  " + "\n  ".join(resources)
        elif resources:
            # VISA backend works but nothing scope-shaped is on the bus.
            self._set_dot(self.scope_detect_dot, _DOT_WARN)
            text = "Oscilloscope not detected"
            tip = ("VISA enumeration returned resources but none match a "
                   "known scope vendor ID (Tek 0x0699, Keysight 0x0957, "
                   "R&S 0x0AAD). Resources seen:\n  "
                   + "\n  ".join(resources))
        else:
            self._set_dot(self.scope_detect_dot, _DOT_OFF)
            text = "Oscilloscope not detected"
            tip = ("VISA backend is loaded but no instruments are on the "
                   "bus. Check that the scope is powered on and "
                   "USB-TMC enabled.")
        self.scope_detect_text.setText(text)
        self.scope_detect_dot.setToolTip(tip)
        self.scope_detect_text.setToolTip(tip)

    # ------- stim prefs ---------------------------------------------
    @staticmethod
    def _stim_prefs_section() -> str:
        return "stim_settings"

    def _load_auto_discharge(self) -> bool:
        """Read the persisted auto-discharge preference. Defaults to
        True (the safe state) so a fresh install / wiped prefs always
        starts with the protective shorting enabled."""
        prefs = load_prefs() or {}
        section = prefs.get(self._stim_prefs_section(), {})
        if not isinstance(section, dict):
            return True
        return bool(section.get("auto_discharge", True))

    def _save_auto_discharge(self, enabled: bool):
        prefs = load_prefs() or {}
        section = prefs.get(self._stim_prefs_section(), {})
        if not isinstance(section, dict):
            section = {}
        section["auto_discharge"] = bool(enabled)
        prefs[self._stim_prefs_section()] = section
        save_prefs(prefs)

    @staticmethod
    def _scaling_prefs_section() -> str:
        return "stim_scaling_by_serial"

    def _load_scaling_memory(self) -> dict:
        """Read the per-serial scaling map from the shared prefs JSON."""
        prefs = load_prefs() or {}
        section = prefs.get(self._scaling_prefs_section(), {})
        return dict(section) if isinstance(section, dict) else {}

    def _save_scaling_memory(self):
        """Persist the per-serial scaling map back into the prefs JSON.

        Reads the current prefs (so we don't clobber other tabs'
        sections), updates only ours, and writes atomically.
        """
        prefs = load_prefs() or {}
        prefs[self._scaling_prefs_section()] = dict(self._scaling_by_serial)
        save_prefs(prefs)

    # ------------------------------------------------------------- scope
    def _do_connect_scope(self):
        if self._stim is None:
            QtWidgets.QMessageBox.warning(
                self, "Stimulator not ready",
                "The stimulator hasn't opened yet — fix that first.")
            return
        # Disable the button immediately so the user can't double-click.
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText("Initializing…")
        import time as _time
        self._scope_connect_t0 = _time.perf_counter()
        self.log.emit("Scope connecting…")
        sim = self.simulate.isChecked()
        res = self.scope_resource.currentText().strip() or None

        # Wire up the result handler.  UniqueConnection guarantees
        # we end up with exactly one binding even if a previous
        # disconnect silently failed — prevents the slot from firing
        # N times on a rapid re-press of "Connect".  See the
        # ``_stimDetectResult`` setup above for the rationale.
        try:
            self._scopeConnectResult.disconnect(self._on_scope_connect_result)
        except (RuntimeError, TypeError):
            pass
        try:
            self._scopeConnectResult.connect(
                self._on_scope_connect_result,
                QtCore.Qt.ConnectionType.UniqueConnection)
        except TypeError:
            pass

        # Thread-safe log emitter for the worker thread.  Routes
        # through ``_logFromWorker`` via QMetaObject.invokeMethod with
        # an explicit QueuedConnection so the log line is guaranteed
        # to land on the GUI thread's event loop and reach the
        # LogPane / .txt mirror — emitting ``self.log`` directly from
        # a Python thread is documented as thread-safe but has been
        # observed to silently drop in this stack.  Capturing
        # ``panel`` keeps the closure independent of how
        # ``self`` resolves inside the worker.
        panel = self

        def _log_emit(msg: str) -> None:
            try:
                QtCore.QMetaObject.invokeMethod(
                    panel, "_emit_log_from_worker",
                    QtCore.Qt.ConnectionType.QueuedConnection,
                    QtCore.Q_ARG(str, str(msg)))
            except Exception:
                pass

        def _thread():
            try:
                scope = open_oscilloscope(simulate=sim, resource=res)
                # Hook the logger BEFORE open() so the initial
                # handshake commands appear in the log.
                try:
                    scope.cmd_logger = _log_emit
                except Exception:
                    pass
                scope.open()
            except Exception as exc:
                scope = exc
            self._scopeConnectResult.emit(scope)

        import threading
        threading.Thread(target=_thread, daemon=True).start()

    @QtCore.pyqtSlot(str)
    def _emit_log_from_worker(self, msg: str) -> None:
        """Slot invoked via QMetaObject.invokeMethod from a worker
        thread.  Forwards the line to the public ``log`` signal so the
        MainWindow's existing connection to the LogPane (and the
        .txt mirror) picks it up.  This is the receiving end of the
        scope/stim cmd_logger callback set up in
        :meth:`_initialize_scope`.
        """
        self.log.emit(msg)

    @QtCore.pyqtSlot(object)
    def _on_scope_connect_result(self, result):
        """Called on the GUI thread when the background connect finishes."""
        self.connect_btn.setText("Initialize")
        try:
            self._scopeConnectResult.disconnect(self._on_scope_connect_result)
        except (RuntimeError, TypeError):
            pass
        import time as _time
        from ..hardware.tektronix import _fmt_elapsed
        _t0 = getattr(self, "_scope_connect_t0", _time.perf_counter())
        _elapsed = _fmt_elapsed(_time.perf_counter() - _t0)
        if isinstance(result, Exception):
            self._scope = None
            self.connect_btn.setEnabled(True)
            QtWidgets.QMessageBox.critical(self, "Scope connect failed", str(result))
            self.log.emit(f"Scope connect failed: {result}   ({_elapsed})")
            return
        self._scope = result
        info = self._scope.info
        self._set_dot(self.scope_dot, _DOT_WARN if info.is_simulated else _DOT_OK)
        parts = [f"{info.make} {info.model}".strip()]
        if info.serial:
            parts.append(f"S/N <b>{info.serial}</b>")
        if info.n_channels:
            parts.append(f"<b>{info.n_channels}</b> ch")
        if info.is_simulated:
            parts.append("(<i>sim</i>)")
        self.scope_label.setText(" · ".join(parts))
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self._update_calibrate_btn()
        self.connected.emit(self._stim, self._scope)
        self.scopeConnected.emit(True)
        self.log.emit(
            f"Scope connected: {info.make} {info.model}   ({_elapsed})")
        # ---- Probe verification ------------------------------------
        # If the scope reports a real probe on any input, prompt the
        # operator to swap to plain BNC and loop until clean.  Skipped
        # on simulated scopes (they have no physical inputs) and on
        # any scope where the probe_info query isn't supported.
        if not info.is_simulated:
            self._verify_no_probes_attached()

    def _verify_no_probes_attached(self) -> None:
        """Pop a modal asking the operator to swap probes for plain BNC.

        Returns immediately when the initial scan finds no probes (the
        common case).  Otherwise the dialog stays up until either every
        input reports BNC (auto-accept) or the operator clicks Skip.
        """
        if self._scope is None:
            return
        initial = _scan_scope_probes(self._scope)
        if not initial:
            return
        ch_list = ", ".join(f"{ch} ({tok}, {gain:g}x)"
                             for ch, tok, gain in initial)
        self.log.emit(
            f"Probe(s) detected on scope inputs: {ch_list} — "
            f"prompting operator to swap to plain BNC.")
        dlg = _ProbeVerifyDialog(self._scope, parent=self)
        accepted = bool(dlg.exec())
        if accepted:
            self.log.emit(
                f"Probe verification: all inputs now plain BNC "
                f"(after {dlg.recheck_count} re-check"
                f"{'s' if dlg.recheck_count != 1 else ''}).")
        else:
            still = ", ".join(f"{ch} ({tok}, {gain:g}x)"
                               for ch, tok, gain in dlg.final_attached)
            self.log.emit(
                f"Probe verification skipped by operator — proceeding "
                f"with: {still or 'no further info'}.  Displayed "
                f"voltages on the affected channels will be off by the "
                f"probe gain factor.")

    def _do_disconnect_scope(self):
        had_scope = self._scope is not None
        try:
            if self._scope is not None:
                self._scope.close()
        finally:
            self._scope = None
            self._set_dot(self.scope_dot, _DOT_OFF)
            self.scope_label.setText("")
            self.connect_btn.setEnabled(True)
            self.disconnect_btn.setEnabled(False)
            self._update_calibrate_btn()
            if had_scope:
                self.disconnected.emit()
                self.scopeConnected.emit(False)
                self.log.emit("Scope disconnected.")

    # ------------------------------------------------- calibration helpers
    def _update_calibrate_btn(self):
        """Enable the calibration button only when both stim and scope
        are live (mirrors the condition the CalibrationTab requires
        before it allows a sweep to run)."""
        ready = self._stim is not None and self._scope is not None
        self.calibrate_btn.setEnabled(ready)
        self.calibrate_btn.setToolTip(
            "Open the Verification tab to run the PlexStim test-board "
            "stimulator verification sweep."
            if ready else
            "Initialize the stimulator and connect the oscilloscope first."
        )

    def _refresh_cal_label(self):
        """Read the last stimulator verification timestamp from disk and update the
        inline label next to the Run Stimulator Verification button."""
        try:
            from .calibration import last_calibration_datetime
            ts = last_calibration_datetime()
        except Exception:
            ts = None
        if ts is None:
            text = "Last verified: <i>never</i>"
        else:
            text = f"Last verified: {ts.strftime('%Y-%m-%d %H:%M')}"
        self.cal_label.setText(text)

    # _do_run_calibration was removed: the verification flow is now an
    # embedded tab in MainWindow (CalibrationTab), reached by emitting
    # ``calibrationRequested``.  MainWindow refreshes the "last verified"
    # label on its own after the user returns from the tab.

    # ------------------------------------------------------------- props
    @property
    def stim(self): return self._stim
    @property
    def scope(self): return self._scope
