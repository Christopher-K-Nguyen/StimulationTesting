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
        # persisted whenever the user changes the dropdown. Keyed by
        # the device's reported serial number so swapping in a
        # different physical PlexStim restores its own preset.
        self._scaling_by_serial: dict = self._load_scaling_memory()

        # ----- shared simulator toggle -----
        self.simulate = QtWidgets.QCheckBox("Use simulator")
        self.simulate.setChecked(simulate_default)
        self.simulate.toggled.connect(self._on_simulate_toggled)

        # ----- PyPlexStim SDK path -----
        # Loaded from prefs at startup; remembered across sessions so
        # the user only has to point the GUI at the SDK folder once.
        # Empty string means "use the vendored copy" — the existing
        # default behaviour.
        self.sdk_path = QtWidgets.QLineEdit()
        self.sdk_path.setPlaceholderText(
            "PyPlexStim SDK folder (containing PlexStim64.dll). "
            "Leave blank to use the vendored copy."
        )
        self.sdk_path.editingFinished.connect(self._on_sdk_path_changed)
        self.sdk_browse_btn = QtWidgets.QPushButton("Browse…")
        self.sdk_browse_btn.clicked.connect(self._on_sdk_browse)
        self.sdk_path.setText(self._load_sdk_path())

        # ----- stimulator: indicator + Initialize / Close -----
        # Two dots: ``stim_dot`` reflects the *connected* state (open
        # handle, simulator vs real), and ``detect_dot`` reflects
        # *detection* — whether ``plexstim_detect`` finds a usable SDK
        # + DLL on this machine independent of whether we've opened
        # it. The detection dot also drives a hover tooltip with the
        # detector's notes so the user can debug "why won't it open?"
        # at a glance.
        self.stim_dot = self._make_dot(_DOT_OFF)
        self.detect_dot = self._make_dot(_DOT_OFF)
        self.detect_dot.setToolTip("Stimulator detection: not yet checked.")
        # Plain-English status text right of the detection dot. Set
        # by ``_refresh_detection_indicator`` to one of:
        #   "Stimulator detected"          (SDK + DLL OK)
        #   "Stimulator DLL failed to load"   (SDK present, DLL won't load)
        #   "Stimulator not detected"      (no SDK on this machine)
        # The dot still carries the colour cue and a tooltip with the
        # detector's full diagnostic notes; this label exists so the
        # status reads at a glance without hovering.
        self.detect_text = _make_label("Stimulator detection pending…")
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
        self.scaling_label = _make_label("")
        self.scaling_label.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.scaling_label.setStyleSheet("color: #555; font-size: 9pt;")
        self.init_btn = QtWidgets.QPushButton("Initialize")
        self.close_btn = QtWidgets.QPushButton("Close")
        self.close_btn.setEnabled(False)
        self.init_btn.clicked.connect(self._do_initialize_stim)
        self.close_btn.clicked.connect(self._do_close_stim)

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

        # Stimulator section — two rows: the ID line (connected dot +
        # description + detection dot pinned to the right) and a single
        # action row that holds Scaling, the live scaling values, and
        # the Initialize / Close buttons. Init/Close inline with
        # Scaling drops one row of vertical space and keeps related
        # controls visually grouped.
        # Indicators bracket the description. Each dot has plain-text
        # status next to it so the user can read both prerequisites
        # ("is the SDK + DLL detected?") and live session state
        # ("has Initialize opened the device?") without hovering.
        # The middle stim_label keeps the device description (S/N,
        # FW, channel count) so detail is one row up from the
        # Scaling row.
        stim_row = QtWidgets.QHBoxLayout()
        stim_row.addWidget(self.detect_dot)
        stim_row.addWidget(self.detect_text)
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

        # First-time hardware detection probe — runs on the next event
        # loop tick so the panel finishes laying out before we touch
        # the registry / DLL loader. Cheap (no actual stim init).
        # Same pattern for the scope side: enumerate VISA resources to
        # see if anything that looks like a scope is on the bus.
        QtCore.QTimer.singleShot(0, self._refresh_detection_indicator)
        QtCore.QTimer.singleShot(0, self._refresh_scope_detection_indicator)

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
        preset = self._scaling_by_serial.get(info.serial_number, self.SCALE_AUTO)
        if preset not in (self.SCALE_AUTO, self.SCALE_DEFAULT, self.SCALE_NIL):
            preset = self.SCALE_AUTO
        self.scaling_combo.blockSignals(True)
        try:
            self.scaling_combo.setCurrentText(preset)
        finally:
            self.scaling_combo.blockSignals(False)
        self._apply_scaling_preset(preset)
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

    def _on_simulate_toggled(self, _checked: bool):
        # Drop the scope first because real-vs-sim isn't compatible
        # mid-flight, then close the stim. The user has to click
        # Initialize again to bring it up under the new mode.
        if self._scope is not None:
            self._do_disconnect_scope()
        if self._stim is not None:
            self._do_close_stim()
        # Detection state itself doesn't change with the simulate
        # toggle, but the user may have changed SDK path or plugged
        # / unplugged hardware while the toggle is on; re-probe both
        # sides so the dots stay fresh.
        self._refresh_detection_indicator()
        self._refresh_scope_detection_indicator()

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
    def _refresh_detection_indicator(self):
        """Light up the detection dot based on ``plexstim_detect`` output.

        The detector walks the registry and tries a ``ctypes.CDLL`` load
        without initialising the device, so we can show "yes, the SDK
        is here and the DLL is loadable" before the user hits
        Initialize. Tooltip carries the detector's diagnostic notes.
        """
        try:
            from ..hardware.plexstim_detect import detect_plexstim
            status = detect_plexstim()
        except Exception as e:
            self._set_dot(self.detect_dot, _DOT_OFF)
            self.detect_dot.setToolTip(f"Detection probe failed: {e}")
            return
        if status.installed and status.dll_loadable:
            self._set_dot(self.detect_dot, _DOT_OK)
            text = "Stimulator detected"
            tip = "Stimulator SDK detected and DLL loadable."
        elif status.installed:
            self._set_dot(self.detect_dot, _DOT_WARN)
            text = "Stimulator DLL failed to load"
            tip = ("Stimulator SDK present but DLL failed to load — "
                   "Visual C++ runtime may be missing.")
        else:
            self._set_dot(self.detect_dot, _DOT_OFF)
            text = "Stimulator not detected"
            tip = "Stimulator SDK not detected on this machine."
        self.detect_text.setText(text)
        if status.notes:
            tip = tip + "\n\n" + "\n".join(status.notes)
        self.detect_dot.setToolTip(tip)
        # Echo the same tooltip on the text label so a hover anywhere
        # in the indicator group surfaces the diagnostic notes.
        self.detect_text.setToolTip(tip)

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
