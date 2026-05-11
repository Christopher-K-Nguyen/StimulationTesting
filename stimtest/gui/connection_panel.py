"""Hardware connect / status widget.

Layout:

    ☐ Use simulator
    ●  Stimulator: PlexStim 64-bit  ·  S/N 12345  ·  FW 2.0.4  ·  16 ch
       Scaling: [Default ▾]  ·  V_mon×0.25, I_mon×2.5 V/mA
       [ Initialize ] [ Close ]
    ●  Oscilloscope: Tektronix TBS2204B  ·  USB::…::INSTR
       VISA resource: [______________]
       [ Connect ] [ Disconnect ]

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


class ConnectionPanel(QtWidgets.QGroupBox):
    """Stimulator (auto-attached, indicator-only) + oscilloscope (Connect/Disconnect)."""

    connected = QtCore.pyqtSignal(object, object)   # (Stimulator, Oscilloscope)
    disconnected = QtCore.pyqtSignal()
    # Fires whenever scope connection state flips. Setup tab listens
    # so it can blank out the channel-mapping rows when no scope is up.
    scopeConnected = QtCore.pyqtSignal(bool)
    log = QtCore.pyqtSignal(str)

    # Scaling presets exposed in the dropdown. The "auto" preset
    # picks Default vs NIL based on the substring match against the
    # device's serial number that PlexonStimulator already uses; the
    # other two override the auto-detection.
    SCALE_AUTO    = "Auto-detect"
    SCALE_DEFAULT = "Default (PlexStim 2.0)"
    SCALE_NIL     = "NIL"

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
        # One-shot flag so the uncalibrated-serial warning fires
        # at most once per stim lifecycle (cleared on
        # ``_do_close_stim``).
        self._uncalibrated_warned: bool = False

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

        # ----- PyPlexStim SDK path -----
        # Loaded from prefs at startup; remembered across sessions so
        # the user only has to point the GUI at the SDK folder once.
        # Empty string means "use the vendored copy" — the existing
        # default behaviour.
        self.sdk_path = QtWidgets.QLineEdit()
        self.sdk_path.setToolTip(
            "Folder containing PlexStim64.dll. Leave blank to use "
            "the copy vendored with the GUI. Set this only if you "
            "have a different SDK version you want to drive the "
            "stimulator with — and remember the on-disk DLL is "
            "independent from whether Plexon's Sim-2 GUI is "
            "installed; the GUI app must be CLOSED during "
            "automated runs (it holds an exclusive USB lock).")
        self.sdk_path.setPlaceholderText(
            "PyPlexStim SDK folder (containing PlexStim64.dll). "
            "Leave blank to use the vendored copy."
        )
        self.sdk_path.editingFinished.connect(self._on_sdk_path_changed)
        self.sdk_browse_btn = QtWidgets.QPushButton("Browse…")
        self.sdk_browse_btn.clicked.connect(self._on_sdk_browse)
        self.sdk_path.setText(self._load_sdk_path())

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
            "calibration wizard tightens this into a verified "
            "entry.<br>"
            "<b>Default (PlexStim 2.0)</b> — 0.25 V/V V_mon, "
            "2.5 mV/µA I_mon. Standard production PlexStim "
            "2.0 devices.<br>"
            "<b>NIL</b> — 1.0 V/V V_mon, 1.0 mV/µA I_mon. "
            "Used by specific NIL-class units. Pick this only "
            "if you've verified the device with calibration or "
            "are certain of its scaling — the readout will be "
            "off by 2.5× if you guess wrong.")
        self.scaling_label = _make_label("")
        self.scaling_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.scaling_label.setStyleSheet("color: #555; font-size: 9pt;")
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
        self.scope_dot = self._make_dot(_DOT_OFF)
        # Description label — empty until Connect succeeds and fills
        # it with make + model + VISA resource. Cleared on Disconnect.
        self.scope_label = _make_label("")
        self.scope_resource = QtWidgets.QLineEdit()
        self.scope_resource.setToolTip(
            "Optional VISA resource identifier for the "
            "oscilloscope. Leave blank to let pyvisa pick the "
            "first Tek scope it discovers (the usual case). Set "
            "explicitly when you have multiple scopes on the same "
            "bus and need to pin which one Connect targets. "
            "Format: USB0::0x0699::&lt;model&gt;::&lt;serial&gt;::INSTR "
            "or TCPIP0::&lt;ip&gt;::inst0::INSTR for LAN scopes.")
        self.scope_resource.setPlaceholderText(
            "Optional VISA resource (e.g. USB0::0x0699::0x03B0::C012345::INSTR)"
        )
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.setEnabled(False)
        self.connect_btn.clicked.connect(self._do_connect_scope)
        self.disconnect_btn.clicked.connect(self._do_disconnect_scope)

        # ----- layout -----
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(8, 4, 8, 6)
        v.addWidget(self.simulate)

        # SDK path row — sits above the stimulator section because it
        # affects what Initialize will load.
        sdk_row = QtWidgets.QHBoxLayout()
        sdk_row.addWidget(_make_label("PyPlexStim SDK:"))
        sdk_row.addWidget(self.sdk_path, stretch=1)
        sdk_row.addWidget(self.sdk_browse_btn)
        v.addLayout(sdk_row)

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
        stim_row.addWidget(_make_label("Initialized:"))
        stim_row.addWidget(self.stim_dot)
        v.addLayout(stim_row)
        scale_row = QtWidgets.QHBoxLayout()
        scale_row.addWidget(_make_label("Scaling:"))
        scale_row.addWidget(self.scaling_combo)
        scale_row.addWidget(self.scaling_label, stretch=1)
        scale_row.addWidget(self.init_btn)
        scale_row.addWidget(self.close_btn)
        v.addLayout(scale_row)

        # (Auto-discharge UI is now in the pattern panel — no row here.)

        # Oscilloscope rows. Same indicator pattern as the stimulator:
        # detection state (dot + plain-text status) on the left, live
        # connection state (label + dot + "Connected:" tag) on the
        # right of the same row.
        scope_row = QtWidgets.QHBoxLayout()
        scope_row.addWidget(self.scope_detect_dot)
        scope_row.addWidget(self.scope_detect_text)
        scope_row.addWidget(self.scope_label, stretch=1)
        scope_row.addWidget(_make_label("Connected:"))
        scope_row.addWidget(self.scope_dot)
        v.addLayout(scope_row)

        scope_ctrls = QtWidgets.QHBoxLayout()
        scope_ctrls.addWidget(_make_label("VISA resource:"))
        scope_ctrls.addWidget(self.scope_resource, stretch=1)
        scope_ctrls.addWidget(self.connect_btn)
        scope_ctrls.addWidget(self.disconnect_btn)
        v.addLayout(scope_ctrls)

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
        """Open the stimulator, fetch its info, and apply remembered scaling."""
        if self._stim is not None:
            # Already open — close first so a re-init refreshes info.
            self._do_close_stim()
        sim = self.simulate.isChecked()
        try:
            # ``dll_path`` is the user-remembered SDK folder, drilled
            # into the ``bin/`` subfolder if needed. Empty string =
            # use the vendored copy in ``stimtest.hardware.pyplexstim.bin``.
            dll_path = self._effective_dll_path() if not sim else None
            self._stim = open_stimulator(simulate=sim, dll_path=dll_path)
            self._stim.open()
        except Exception as e:
            self._stim = None
            self._set_dot(self.stim_dot, _DOT_OFF)
            self.stim_label.setText(f"Stimulator: failed — {e}")
            self.log.emit(f"Stimulator open failed: {e}")
            self.scaling_combo.setEnabled(False)
            self.close_btn.setEnabled(False)
            self.init_btn.setEnabled(True)
            return
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
        known_serial = bool(sn) and sn in self._scaling_by_serial
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
        self._refresh_stim_label()
        self.scaling_combo.setEnabled(True)
        self.init_btn.setEnabled(False)
        self.close_btn.setEnabled(True)
        self.log.emit(f"Stimulator initialized: {info.description or 'sim stim'} "
                      f"(S/N {info.serial_number or 'n/a'})")
        # If a scope is already connected, refresh the connected signal
        # so subscribers see both halves.
        if self._scope is not None:
            self.connected.emit(self._stim, self._scope)
        # Warn the user if this device's scaling hasn't been
        # validated yet. Simulated devices are exempt — their
        # scaling is whatever the simulator hard-codes and has
        # nothing to do with real hardware. The warning is
        # non-blocking (just a message box) so the user can still
        # use the stimulator if they're confident in the manually-
        # picked preset.
        if (not info.is_simulated and not known_serial
                and not getattr(self, "_uncalibrated_warned",
                                  False)):
            self._warn_uncalibrated_serial(sn or "(no serial)")
            # Stash a per-session flag so we don't re-warn for the
            # same device every time the user closes and re-opens
            # the connection in one session. Cleared on
            # ``_do_close_stim``.
            self._uncalibrated_warned = True

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
            self.scaling_combo.setEnabled(False)
            self.scaling_combo.blockSignals(True)
            try:
                self.scaling_combo.setCurrentText(self.SCALE_AUTO)
            finally:
                self.scaling_combo.blockSignals(False)
            self.init_btn.setEnabled(True)
            self.close_btn.setEnabled(False)
            self.log.emit("Stimulator closed.")
            # Reset the "uncalibrated serial" one-shot so the
            # next Initialize re-evaluates the database
            # membership and re-warns if appropriate.
            self._uncalibrated_warned = False

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
        if info.n_channels:
            bits.append(f"<b>{info.n_channels}</b> channels")
        bits.append("(<i>sim</i>)" if info.is_simulated else "(connected)")
        self.stim_label.setText("Stimulator: " + " · ".join(bits))
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
        from the serial-number prefix match. The other two presets
        force the values regardless of serial.
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
        # Auto-detect: leave the values that PlexonStimulator already
        # set during open() — they reflect the serial-number heuristic.

    def _warn_uncalibrated_serial(self, serial_number: str) -> None:
        """Pop a modal warning when an Initialize completed for a
        stimulator whose serial isn't in the shared scaling
        database.

        The shared database (``stim_scaling_by_serial`` in the
        prefs JSON) is populated by:

          * the calibration wizard's ``_record_serial_scaling``
            after a successful sweep — the trusted, R²-validated
            path; and
          * the user explicitly picking Default / NIL from the
            combo in this panel (``_on_scaling_changed``) — the
            "I'm certain" override path.

        Until one of those has happened, the device's I_mon
        scaling is effectively unknown. Until calibration, the
        readback values from V_mon / I_mon may be wildly wrong
        if Auto-detect picks the wrong preset for this serial.

        This is informational, not blocking — the user can still
        proceed if they're confident.
        """
        text = (
            f"<h3>Uncalibrated stimulator</h3>"
            f"<p>Serial <b>{serial_number}</b> isn't in the shared "
            f"scaling database — its I_mon scaling hasn't been "
            f"validated for this installation.</p>"
            f"<p>Until the validation has run, V_mon / I_mon "
            f"readback values may be off by 2.5× if Auto-detect "
            f"picked the wrong preset for this device.</p>"
            f"<p><b>Recommended:</b> connect the Plexon test "
            f"board and run <i>Run → Calibrate…</i>. The "
            f"calibration wizard verifies the scaling and "
            f"records this serial in the database so future "
            f"sessions apply the right preset automatically.</p>"
            f"<p><b>If you are CERTAIN of the scaling</b> for "
            f"this device (e.g. you've confirmed it on the bench "
            f"by other means), pick <b>Default</b> or <b>NIL</b> "
            f"from the scaling combo on this panel — that also "
            f"records the choice in the database. <b>Auto-detect</b> "
            f"alone does NOT count as a calibration.</p>"
            f"<p>The scaling combo currently reads "
            f"<b>{self.scaling_combo.currentText()}</b>.</p>"
        )
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Icon.Warning)
        box.setWindowTitle("Uncalibrated stimulator")
        box.setTextFormat(QtCore.Qt.TextFormat.RichText)
        box.setText(text)
        box.setStandardButtons(
            QtWidgets.QMessageBox.StandardButton.Ok)
        box.exec()

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
        panel construction. The probe runs synchronously on the
        GUI thread; PnP enumeration is fast (sub-second on a
        healthy box) so the UI lag is imperceptible.
        """
        try:
            from ..hardware.plexstim_detect import plexstim_device_present
            present = plexstim_device_present()
        except Exception as e:
            self._set_dot(self.stim_detect_dot, _DOT_OFF)
            text = "Stimulator detection failed"
            tip = (f"Detection probe raised an exception: {e}\n\n"
                   "The 'Initialized' indicator below still works "
                   "— click Initialize to try opening the device "
                   "directly.")
            self.stim_detect_text.setText(text)
            self.stim_detect_dot.setToolTip(tip)
            self.stim_detect_text.setToolTip(tip)
            return
        if present is True:
            self._set_dot(self.stim_detect_dot, _DOT_OK)
            text = "Stimulator detected"
            tip = ("A Plexon stimulator USB device is currently "
                   "enumerated by the operating system. Click "
                   "Initialize to open a session.")
        elif present is False:
            self._set_dot(self.stim_detect_dot, _DOT_OFF)
            text = "Stimulator not detected"
            tip = ("No Plexon stimulator USB device is currently "
                   "plugged in (or the Plexon USB driver isn't "
                   "installed yet — see the Stim-2 software "
                   "warning at launch).\n\n"
                   "If a stimulator IS plugged in but isn't being "
                   "detected, check Device Manager for an unknown / "
                   "yellow-flag entry, and confirm the Stim-2 / "
                   "Stimulator V2 driver installed cleanly.")
        else:
            # ``None`` = couldn't run the probe (non-Windows,
            # PowerShell missing, PnP timeout). Use the amber
            # "warning" colour so the user reads it as
            # indeterminate rather than negative.
            self._set_dot(self.stim_detect_dot, _DOT_WARN)
            text = "Stimulator detection unavailable"
            tip = ("Hardware-presence detection isn't available "
                   "on this platform (Windows-only via PowerShell "
                   "PnP enumeration). Click Initialize to test the "
                   "device connection directly.")
        self.stim_detect_text.setText(text)
        self.stim_detect_dot.setToolTip(tip)
        self.stim_detect_text.setToolTip(tip)

    def _refresh_scope_detection_indicator(self):
        """Light up the scope detection dot from the VISA resource list.

        Tries pyvisa's ``ResourceManager.list_resources()`` to see if
        anything is on the USB-TMC / VISA bus. We don't *open* any
        resource here (that would block the panel and may steal a
        device the user is about to talk to from the bench); we just
        look at descriptors. Heuristic: anything matching a known
        scope vendor ID (Tek = ``0x0699``, Keysight = ``0x0957``,
        R&S = ``0x0AAD``) is treated as "scope detected". A bare
        VISA runtime with no matching device → amber. No VISA at
        all → grey.
        """
        try:
            import pyvisa
        except Exception as e:
            self._set_dot(self.scope_detect_dot, _DOT_OFF)
            text = "VISA backend not available"
            tip = (f"Could not import pyvisa: {e}\n\n"
                   "Install the pyvisa runtime (NI-VISA, TekVISA, or the "
                   "bundled pyvisa-py) before connecting a scope.")
            self.scope_detect_text.setText(text)
            self.scope_detect_dot.setToolTip(tip)
            self.scope_detect_text.setToolTip(tip)
            return
        # Vendor-ID prefixes for VISA USB resources. Each VISA USB
        # descriptor looks like "USB0::0xVVVV::0xPPPP::SERIAL::INSTR".
        scope_vendor_ids = ("0x0699",  # Tektronix
                            "0x0957",  # Keysight / Agilent
                            "0x0AAD")  # Rohde & Schwarz
        try:
            rm = pyvisa.ResourceManager()
            resources = rm.list_resources()
        except Exception as e:
            self._set_dot(self.scope_detect_dot, _DOT_OFF)
            text = "Oscilloscope not detected"
            tip = (f"VISA resource enumeration failed: {e}\n\n"
                   "On a clean machine without NI-VISA the bundled "
                   "pyvisa-py backend may not enumerate USB-TMC devices "
                   "until libusb is installed.")
            self.scope_detect_text.setText(text)
            self.scope_detect_dot.setToolTip(tip)
            self.scope_detect_text.setToolTip(tip)
            return
        scope_resources = [r for r in resources
                           if any(vid in r for vid in scope_vendor_ids)]
        if scope_resources:
            self._set_dot(self.scope_detect_dot, _DOT_OK)
            text = "Oscilloscope detected"
            tip = ("Detected scope-like VISA resource(s):\n  "
                   + "\n  ".join(scope_resources))
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

    # ------- SDK path memory ----------------------------------------
    @staticmethod
    def _sdk_prefs_section() -> str:
        return "pyplexstim_sdk"

    @staticmethod
    def _stim_prefs_section() -> str:
        return "stim_settings"

    def _load_sdk_path(self) -> str:
        prefs = load_prefs() or {}
        section = prefs.get(self._sdk_prefs_section(), {})
        if not isinstance(section, dict):
            return ""
        return str(section.get("dll_path", ""))

    def _save_sdk_path(self, path: str):
        prefs = load_prefs() or {}
        prefs[self._sdk_prefs_section()] = {"dll_path": str(path)}
        save_prefs(prefs)

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

    def _effective_dll_path(self) -> Optional[str]:
        """Resolve the user-typed path into a usable DLL folder.

        Empty string → ``None`` (the loader uses the vendored bin dir).
        Otherwise: if the path is a folder containing ``PlexStim.dll``
        or ``PlexStim64.dll``, return it as-is. If it's a parent of a
        ``bin/`` subfolder that does, drill into ``bin``. Anything
        else is returned verbatim and let the loader surface the
        error (the user will see it in the status label).
        """
        from pathlib import Path
        text = self.sdk_path.text().strip()
        if not text:
            return None
        p = Path(text)
        if (p / "PlexStim64.dll").exists() or (p / "PlexStim.dll").exists():
            return str(p)
        # Common case: user picked the SDK root, the DLLs live in `bin/`.
        nested = p / "bin"
        if (nested / "PlexStim64.dll").exists() or (nested / "PlexStim.dll").exists():
            return str(nested)
        # Or one level deeper: PyPlexStim/bin under a "PlexStim SDKs" root.
        nested2 = p / "PyPlexStim" / "bin"
        if (nested2 / "PlexStim64.dll").exists() or (nested2 / "PlexStim.dll").exists():
            return str(nested2)
        return str(p)

    def _on_sdk_path_changed(self):
        """User finished editing the path field — persist + log."""
        path = self.sdk_path.text().strip()
        self._save_sdk_path(path)
        if path:
            self.log.emit(f"PyPlexStim SDK path set: {path}")
        else:
            self.log.emit("PyPlexStim SDK path cleared (using vendored copy).")

    def _on_sdk_browse(self):
        """Open a folder picker, accept any folder containing the DLL
        (directly or via ``bin/`` / ``PyPlexStim/bin/`` subfolder)."""
        start = self.sdk_path.text().strip() or ""
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose PyPlexStim SDK folder", start)
        if not chosen:
            return
        self.sdk_path.setText(chosen)
        self._save_sdk_path(chosen)
        self.log.emit(f"PyPlexStim SDK path set: {chosen}")

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
        sim = self.simulate.isChecked()
        try:
            res = self.scope_resource.text().strip() or None
            self._scope = open_oscilloscope(simulate=sim, resource=res)
            self._scope.open()
        except Exception as e:
            self._scope = None
            QtWidgets.QMessageBox.critical(self, "Scope connect failed", str(e))
            self.log.emit(f"Scope connect failed: {e}")
            return
        info = self._scope.info
        self._set_dot(self.scope_dot, _DOT_WARN if sim else _DOT_OK)
        self.scope_label.setText(
            f"Oscilloscope: {info.make} {info.model}  ·  {info.resource}"
        )
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.connected.emit(self._stim, self._scope)
        self.scopeConnected.emit(True)
        self.log.emit(f"Scope connected: {info.make} {info.model}")

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
            if had_scope:
                self.disconnected.emit()
                self.scopeConnected.emit(False)
                self.log.emit("Scope disconnected.")

    # ------------------------------------------------------------- props
    @property
    def stim(self): return self._stim
    @property
    def scope(self): return self._scope
