"""Tests for the ConnectionPanel bias-module facade + the host-
delegated BiasConnector path.

These tests don't need a real STM32 — they exercise the simulator
backend through the facade.  Verified behaviors:

* ConnectionPanel.open_bias_module/close_bias_module open/close the
  simulator driver and toggle ``self.bias`` correctly.
* Signals biasConnected / biasDisconnected fire with the right
  payload and at the right time.
* open_bias_module is idempotent (calling it twice doesn't open two
  drivers or fire two signals).
* A host-delegated BiasConnector reflects host-driver state — if
  the host's driver is opened by ANY caller, the connector's
  ``self.bias`` updates and its dot turns on.
* Two host-delegated BiasConnectors stay in sync (single-shared-
  driver guarantee — the user's chosen architecture).

QApplication is required because BiasConnector is a QWidget.
"""
from __future__ import annotations

import pytest

# Skip the whole module if PyQt6 isn't importable (CI env without GUI).
pytest.importorskip("PyQt6")
pytest.importorskip("PyQt6.QtWidgets")


@pytest.fixture(scope="module")
def qapp():
    """Module-scope QApplication so multiple tests share one."""
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([])
    return app


# ---------------------------------------------------------------------------
# Minimal host stub
# ---------------------------------------------------------------------------
# A real ConnectionPanel pulls in stim + scope detection + the entire
# Setup-tab adjacency — way more than we need to test the facade.
# This stub exposes JUST the facade surface the BiasConnector
# depends on (the two methods + two signals + the ``bias`` attribute)
# so the tests run fast and isolate the contract.
class _FacadeHostStub:
    """In-place reimplementation of ConnectionPanel's bias facade,
    minimum surface for BiasConnector to delegate to."""

    def __init__(self):
        from PyQt6 import QtCore
        # Build via a dynamic QObject subclass so we can attach
        # pyqtSignals (which require class-level declaration).
        class _Q(QtCore.QObject):
            biasConnected = QtCore.pyqtSignal(object)
            biasDisconnected = QtCore.pyqtSignal()
            log = QtCore.pyqtSignal(str)
        self._q = _Q()
        # Forward signal accessors so external code can do
        # ``host.biasConnected.connect(...)`` directly.
        self.biasConnected = self._q.biasConnected
        self.biasDisconnected = self._q.biasDisconnected
        self.log = self._q.log
        self.bias = None
        self.log_lines = []
        self.log.connect(self.log_lines.append)

    def open_bias_module(self, *, simulate: bool, port=None) -> None:
        from stimtest.hardware import open_bias_module
        if self.bias is not None:
            return  # idempotent
        bias = open_bias_module(simulate=simulate, port=port)
        bias.open()
        self.bias = bias
        self.biasConnected.emit(bias.info)

    def close_bias_module(self) -> None:
        if self.bias is None:
            return
        self.bias.close()
        self.bias = None
        self.biasDisconnected.emit()


# ---------------------------------------------------------------------------
# Facade behavior
# ---------------------------------------------------------------------------
def test_facade_open_close_lifecycle(qapp):
    host = _FacadeHostStub()
    assert host.bias is None

    seen_connected = []
    seen_disconnected = []
    host.biasConnected.connect(seen_connected.append)
    host.biasDisconnected.connect(lambda: seen_disconnected.append(True))

    host.open_bias_module(simulate=True)
    assert host.bias is not None
    assert host.bias.is_open
    assert len(seen_connected) == 1
    assert seen_connected[0].is_simulated

    host.close_bias_module()
    assert host.bias is None
    assert len(seen_disconnected) == 1


def test_facade_open_is_idempotent(qapp):
    """Opening twice doesn't open two drivers or fire two signals."""
    host = _FacadeHostStub()
    seen = []
    host.biasConnected.connect(seen.append)

    host.open_bias_module(simulate=True)
    first_driver = host.bias
    host.open_bias_module(simulate=True)
    assert host.bias is first_driver, (
        "second open should be a no-op, not replace the driver")
    assert len(seen) == 1, (
        f"biasConnected should fire once across two open calls; "
        f"got {len(seen)}")


def test_facade_close_is_idempotent(qapp):
    """Calling close before open and twice after open are both no-ops."""
    host = _FacadeHostStub()
    seen = []
    host.biasDisconnected.connect(lambda: seen.append(True))

    host.close_bias_module()  # never opened
    assert host.bias is None
    assert seen == []

    host.open_bias_module(simulate=True)
    host.close_bias_module()
    host.close_bias_module()
    assert host.bias is None
    assert len(seen) == 1


# ---------------------------------------------------------------------------
# Host-delegated BiasConnector
# ---------------------------------------------------------------------------
def test_connector_host_delegate_propagates_open(qapp):
    """When the host opens a driver, the connector's ``self.bias`` and
    UI state should reflect it without the connector calling
    open_bias_module itself."""
    from stimtest.gui.bias_panel import BiasConnector

    host = _FacadeHostStub()
    conn = BiasConnector(host=host)

    assert conn.bias is None
    host.open_bias_module(simulate=True)
    assert conn.bias is not None
    assert conn.bias is host.bias  # shared instance
    # Connect button disabled when connected, Disconnect enabled.
    assert conn.connect_btn.isEnabled() is False
    assert conn.disconnect_btn.isEnabled() is True


def test_connector_host_delegate_propagates_close(qapp):
    from stimtest.gui.bias_panel import BiasConnector

    host = _FacadeHostStub()
    conn = BiasConnector(host=host)

    host.open_bias_module(simulate=True)
    assert conn.bias is not None

    host.close_bias_module()
    assert conn.bias is None
    assert conn.connect_btn.isEnabled() is True
    assert conn.disconnect_btn.isEnabled() is False


def test_two_connectors_share_state_via_host(qapp):
    """The user's chosen architecture: two BiasConnectors in two
    different experiment tabs both bind to the same host and reflect
    the same driver state.  Pressing Connect in one updates both."""
    from stimtest.gui.bias_panel import BiasConnector

    host = _FacadeHostStub()
    conn_a = BiasConnector(host=host)
    conn_b = BiasConnector(host=host)

    assert conn_a.bias is None and conn_b.bias is None

    host.open_bias_module(simulate=True)
    assert conn_a.bias is not None
    assert conn_b.bias is not None
    assert conn_a.bias is conn_b.bias  # exact same driver instance

    host.close_bias_module()
    assert conn_a.bias is None and conn_b.bias is None


def test_connector_standalone_mode_still_works(qapp):
    """The legacy direct-ownership path (no host) must still function
    so the in-panel ConnectionPanel bias group keeps working until
    the per-tab migration removes it."""
    from stimtest.gui.bias_panel import BiasConnector

    conn = BiasConnector()  # no host = standalone
    assert conn._host is None
    # Simulating + clicking Connect should open the driver directly.
    conn.simulate_check.setChecked(True)
    conn._do_connect()
    assert conn.bias is not None
    assert conn.bias.is_open
    conn._do_disconnect()
    assert conn.bias is None


def test_connector_delegate_connect_button_triggers_host(qapp):
    """In host mode, pressing Connect on the connector should drive
    the host's open_bias_module (not call the factory directly)."""
    from stimtest.gui.bias_panel import BiasConnector

    host = _FacadeHostStub()
    conn = BiasConnector(host=host)
    # In host mode the connector should call host.open_bias_module;
    # the simulate path doesn't need a port.
    conn.simulate_check.setChecked(True)
    conn._do_connect()
    assert host.bias is not None, (
        "host.open_bias_module should have been called by the "
        "connector's Connect click")
    assert conn.bias is host.bias
