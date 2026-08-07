"""Connector widget for the STM32 interpulse-bias module.

Lives in :class:`ConnectionPanel`, **between** the oscilloscope and
camera sections, per the per-feature placement decision.

Single-device architecture: unlike :class:`CameraService` (multi-
consumer singleton for live preview + per-tab streamers), the bias
module has exactly one consumer at a time (the active experiment
runner) so this connector owns the driver directly.  No service
indirection.

API
---
The connector exposes:

* ``bias`` — the live :class:`BiasModule` after a successful Connect,
  or ``None`` before / after Disconnect.  ConnectionPanel publishes
  this as ``conn.bias`` so the rest of the app can pick it up.
* ``connected(BiasModuleInfo)`` signal — fires after a successful
  ``open()``; payload is the populated info snapshot.
* ``disconnected()`` signal — fires after ``close()``.
* ``log(str)`` signal — forwarded into ConnectionPanel's log pipe;
  every command-level log line lands here.
"""
from __future__ import annotations

from typing import List, Optional

from PyQt6 import QtCore, QtWidgets

from ..hardware import BiasModule, BiasModuleInfo, open_bias_module


class BiasConnector(QtWidgets.QWidget):
    """Port picker + Connect / Disconnect for the STM32 bias module."""

    #: Emitted after a successful connection.  Payload is the populated
    #: :class:`BiasModuleInfo` snapshot from the driver — same shape
    #: regardless of real-vs-simulated backend.
    connected = QtCore.pyqtSignal(object)

    #: Emitted after a clean disconnect.
    disconnected = QtCore.pyqtSignal()

    #: Forwarded log messages.  ConnectionPanel routes these to the
    #: MainWindow LogPane alongside scope / stim / camera traffic.
    log = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None,
                 *, host: Optional[object] = None):
        """Construct the connector widget.

        Parameters
        ----------
        parent :
            Standard Qt parent widget.
        host :
            Optional :class:`ConnectionPanel`-shaped object exposing
            ``open_bias_module(simulate, port)`` and
            ``close_bias_module()`` methods + ``biasConnected(info)``
            / ``biasDisconnected()`` signals.  When given, this
            connector becomes a thin UI shell that delegates
            open/close to the host (so multiple connectors in
            different experiment tabs share one driver instance).
            When ``None``, the connector owns its own driver
            (the legacy direct-ownership path; used by
            ConnectionPanel's in-panel bias group until that's
            removed in the per-tab migration).
        """
        super().__init__(parent)

        #: The live driver after Connect, ``None`` otherwise.  In
        #: host-delegated mode, this mirrors ``host.bias`` (kept in
        #: sync via the host's signals); in standalone mode it's the
        #: actual driver this connector owns.
        self.bias: Optional[BiasModule] = None

        #: Optional host that owns the actual driver.  See class
        #: docstring + ``__init__`` host param.
        self._host = host
        if host is not None:
            # Subscribe to the host's connect/disconnect signals so
            # this connector's UI state mirrors the shared driver
            # state.  Wired with UniqueConnection to tolerate repeat
            # construction (e.g. if a tab gets rebuilt) without
            # stacking duplicate slots.
            try:
                host.biasConnected.connect(
                    self._on_host_connected,
                    QtCore.Qt.ConnectionType.UniqueConnection)
                host.biasDisconnected.connect(
                    self._on_host_disconnected,
                    QtCore.Qt.ConnectionType.UniqueConnection)
            except Exception:
                # Host doesn't expose the expected signals — fall back
                # to standalone behaviour by clearing the host ref.
                self._host = None

        # ---- detection dot + status text (matches scope's pattern) ---
        from .connection_panel import _DOT_OFF, _DOT_OK
        self._dot_off = _DOT_OFF
        self._dot_on = _DOT_OK
        self.detect_dot = self._make_dot(_DOT_OFF)
        self.detect_dot.setToolTip(
            "Bias module detection: gray = no serial ports enumerated, "
            "green = at least one candidate port present (ST-LINK USB CDC "
            "device preferred).")
        self.status_label = QtWidgets.QLabel("Bias module detection pending…")
        # Detection messages can get long (e.g. "3 serial port(s)
        # available (ST-LINK first if present).") and the connector
        # sits in a splittable panel that the operator may narrow.
        # Reflow rather than truncate.
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #555;")
        self.connected_dot = self._make_dot(_DOT_OFF)
        self.connected_dot.setToolTip(
            "Bias module session: gray = not connected, green = open + "
            "ready to receive commands.")
        # The dot alone is unlabelled (operator: "there needs to be a label
        # for this indicator") — a bare coloured circle says nothing about
        # WHAT is or is not connected, and the detection dot right next to it
        # has its own text, so an unlabelled second dot reads as ambiguous.
        self.connected_label = QtWidgets.QLabel("Not connected")
        self.connected_label.setToolTip(self.connected_dot.toolTip())

        # ---- simulate checkbox + port combobox + buttons ------------
        self.simulate_check = QtWidgets.QCheckBox("Simulate")
        self.simulate_check.setToolTip(
            "Use the in-process bias-module simulator instead of opening "
            "a real serial port.  Useful for GUI development without a "
            "Nucleo attached.  When checked, the port combobox is "
            "ignored.")
        self.simulate_check.toggled.connect(self._on_simulate_toggled)

        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.setToolTip(
            "Pick a serial port.  ST-LINK VCP entries (Nucleo's onboard "
            "USB) sort to the top.  Click Refresh after plugging or "
            "unplugging the cable.")
        self.refresh_btn = QtWidgets.QToolButton()
        self.refresh_btn.setText("Refresh")
        self.refresh_btn.setToolTip("Re-enumerate available serial ports.")
        self.refresh_btn.clicked.connect(self.refresh_ports)

        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.clicked.connect(self._do_connect)
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._do_disconnect)
        self.disconnect_btn.setEnabled(False)

        # ---- layout -------------------------------------------------
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(self.detect_dot)
        row1.addWidget(self.status_label, stretch=1)
        row1.addWidget(QtWidgets.QLabel("Open"))
        row1.addWidget(self.connected_dot)
        row1.addWidget(self.connected_label)
        v.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("Port:"))
        row2.addWidget(self.port_combo, stretch=1)
        row2.addWidget(self.refresh_btn)
        row2.addWidget(self.simulate_check)
        row2.addWidget(self.connect_btn)
        row2.addWidget(self.disconnect_btn)
        v.addLayout(row2)

        # ---- initial enumeration -----------------------------------
        # Deferred to first event-loop tick so the panel finishes
        # laying out before we touch pyserial (cheap, but consistent
        # with the scope / stim detect indicators).
        QtCore.QTimer.singleShot(0, self.refresh_ports)

    # ============================================================ host
    def set_host(self, host: Optional[object]) -> None:
        """Attach a ConnectionPanel-shaped host AFTER construction.

        Mirrors the ``host=`` kwarg accepted by ``__init__`` — useful
        for the per-experiment-tab case where the tab is constructed
        before it knows about ConnectionPanel.  Idempotent + safe to
        call with ``None`` to detach.

        If the connector is currently in standalone mode with a live
        driver, the caller is responsible for closing that driver
        BEFORE swapping hosts — this method does not migrate live
        connections.
        """
        # No-op when re-setting the same host — both the disconnect
        # and re-connect would be wasted work, and the connect path
        # with UniqueConnection RAISES TypeError on a duplicate
        # connection (PyQt6 behaviour, not silently-suppress) which
        # would land in our except clause and incorrectly null out
        # _host.  Easier to short-circuit here.
        if self._host is host:
            return
        # If we already had a (different) host, drop its signal
        # bindings so the connector doesn't keep mirroring state from
        # the old one.
        if self._host is not None:
            try:
                self._host.biasConnected.disconnect(self._on_host_connected)
            except (TypeError, RuntimeError):
                pass
            try:
                self._host.biasDisconnected.disconnect(
                    self._on_host_disconnected)
            except (TypeError, RuntimeError):
                pass
        self._host = host
        if host is not None:
            try:
                host.biasConnected.connect(
                    self._on_host_connected,
                    QtCore.Qt.ConnectionType.UniqueConnection)
                host.biasDisconnected.connect(
                    self._on_host_disconnected,
                    QtCore.Qt.ConnectionType.UniqueConnection)
            except Exception:
                # Host doesn't expose the expected signals.  Drop the
                # ref so subsequent calls don't try to use it.
                self._host = None
                return
            # If the host already has an open driver (e.g. user
            # connected via another tab before this one was shown),
            # mirror that state immediately rather than waiting for
            # the next connect/disconnect signal.
            existing = getattr(host, "bias", None)
            if existing is not None:
                self.bias = existing
                self._apply_connected_ui(getattr(existing, "info", None))

    # ============================================================ public API
    def refresh_ports(self) -> None:
        """Re-enumerate serial ports and rebuild the combobox.

        Preserves the previous selection when possible.  Updates the
        detection dot: green when at least one port is found, gray
        otherwise.
        """
        prev = self.port_combo.currentData()
        try:
            from ..hardware.stm32_bias import auto_discover
            ports = auto_discover()
        except Exception as e:
            # Probably pyserial missing — degrade gracefully.
            self.log.emit(f"[bias] port enumeration failed: {e}")
            ports = []

        self.port_combo.blockSignals(True)
        try:
            self.port_combo.clear()
            for p in ports:
                self.port_combo.addItem(p, userData=p)
            # Restore prior selection if still present.
            if prev:
                idx = self.port_combo.findData(prev)
                if idx >= 0:
                    self.port_combo.setCurrentIndex(idx)
        finally:
            self.port_combo.blockSignals(False)

        if ports:
            self.detect_dot.setStyleSheet(_dot_qss(self._dot_on))
            self.status_label.setText(
                f"{len(ports)} serial port(s) available "
                f"(ST-LINK first if present).")
            self.status_label.setStyleSheet("color: #333;")
        else:
            self.detect_dot.setStyleSheet(_dot_qss(self._dot_off))
            self.status_label.setText(
                "No serial ports detected (or pyserial not installed).")
            self.status_label.setStyleSheet("color: #888;")

    # ============================================================ slots
    def _on_simulate_toggled(self, on: bool) -> None:
        """Gate the port combobox + Refresh on the simulate-checkbox state."""
        self.port_combo.setEnabled(not on)
        self.refresh_btn.setEnabled(not on)

    def _do_connect(self) -> None:
        # Refuse if already connected — regardless of which path owns
        # the driver.  Host mode: ``self.bias`` will already be set
        # via the host-signal handler.  Standalone mode: ``self.bias``
        # is set directly below.
        if self.bias is not None:
            self.log.emit("[bias] already connected; skipping")
            return
        simulate = self.simulate_check.isChecked()
        port = None
        if not simulate:
            port = self.port_combo.currentData()
            if not port:
                QtWidgets.QMessageBox.warning(
                    self, "No port selected",
                    "Pick a serial port from the combo box, or check "
                    "Simulate to use the in-process backend.")
                return

        # Host-delegated path: ConnectionPanel owns the driver.  We
        # call its facade method and let its signal callback flip our
        # UI state.  Errors raised by the host propagate to a
        # user-facing dialog here (parallel to the standalone path).
        if self._host is not None:
            try:
                self._host.open_bias_module(simulate=simulate, port=port)
            except Exception as e:
                self.log.emit(
                    f"[bias] connect FAILED: {type(e).__name__}: {e}")
                QtWidgets.QMessageBox.critical(
                    self, "Bias module connect failed",
                    f"Could not open the bias module:\n\n"
                    f"{type(e).__name__}: {e}")
            return  # state will update via _on_host_connected callback

        # Standalone path: this connector owns the driver itself.
        # Used by ConnectionPanel's in-panel bias group until the
        # per-experiment-tab migration is complete.
        try:
            bias = open_bias_module(simulate=simulate, port=port)
            # Mirror per-command traffic into the connector's log
            # signal so MainWindow's LogPane sees every send/receive
            # (matches the scope / stim cmd_logger wiring done in
            # ConnectionPanel before open()).
            try:
                bias.cmd_logger = self.log.emit
            except Exception:
                pass
            bias.open()
        except Exception as e:
            self.log.emit(f"[bias] connect FAILED: {type(e).__name__}: {e}")
            QtWidgets.QMessageBox.critical(
                self, "Bias module connect failed",
                f"Could not open the bias module:\n\n{type(e).__name__}: {e}")
            return

        self.bias = bias
        self._apply_connected_ui(bias.info)
        self.connected.emit(bias.info)

    def _do_disconnect(self) -> None:
        if self.bias is None:
            return

        # Host-delegated path: ask the host to close; its signal
        # callback updates our UI.
        if self._host is not None:
            try:
                self._host.close_bias_module()
            except Exception as e:
                self.log.emit(
                    f"[bias] disconnect raised: {type(e).__name__}: {e}")
            return  # state updates via _on_host_disconnected

        # Standalone path.
        try:
            self.bias.close()
        except Exception as e:
            self.log.emit(f"[bias] close raised: {type(e).__name__}: {e}")
        self.bias = None
        self._apply_disconnected_ui()
        self.log.emit("[bias] disconnected")
        self.disconnected.emit()

    # ---- host signal handlers (host-delegated mode only) ----------
    def _on_host_connected(self, info) -> None:
        """The shared host opened a driver — could be us pressing
        Connect, or another tab's connector.  Mirror the state."""
        if self._host is None:
            return
        self.bias = getattr(self._host, "bias", None)
        if self.bias is not None:
            self._apply_connected_ui(info)
            self.connected.emit(info)

    def _on_host_disconnected(self) -> None:
        if self._host is None:
            return
        self.bias = None
        self._apply_disconnected_ui()
        self.disconnected.emit()

    # ---- UI state appliers (shared between paths) ----------------
    def _apply_connected_ui(self, info) -> None:
        """Update the connector's widgets to the 'connected' look."""
        self.connect_btn.setEnabled(False)
        self.disconnect_btn.setEnabled(True)
        self.simulate_check.setEnabled(False)
        self.port_combo.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.connected_dot.setStyleSheet(_dot_qss(self._dot_on))
        try:
            _p = ""
            if getattr(self, "simulate_check", None) is not None                     and self.simulate_check.isChecked():
                _p = "simulator"
            else:
                _p = (self.port_combo.currentText() or "").strip()
            self.connected_label.setText(
                f"Connected — {_p}" if _p else "Connected")
        except Exception:
            self.connected_label.setText("Connected")

    def _apply_disconnected_ui(self) -> None:
        """Update the connector's widgets to the 'disconnected' look."""
        self.connect_btn.setEnabled(True)
        self.disconnect_btn.setEnabled(False)
        self.simulate_check.setEnabled(True)
        sim = self.simulate_check.isChecked()
        self.port_combo.setEnabled(not sim)
        self.refresh_btn.setEnabled(not sim)
        self.connected_dot.setStyleSheet(_dot_qss(self._dot_off))
        try:
            self.connected_label.setText("Not connected")
        except Exception:
            pass

    # ============================================================ helpers
    @staticmethod
    def _make_dot(color: str) -> QtWidgets.QLabel:
        """Small filled circle for the status row.  Mirrors
        :meth:`ConnectionPanel._make_dot` — kept inline to avoid a
        circular import at class-construction time."""
        dot = QtWidgets.QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(_dot_qss(color))
        return dot


def _dot_qss(color: str) -> str:
    return (f"background-color: {color}; border-radius: 6px; "
            f"border: 1px solid #555;")
