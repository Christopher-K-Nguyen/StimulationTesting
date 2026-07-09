"""INTERSTELLAR (experimental interpulse-bias module) is gated behind an
opt-in build flag.

The public installer ships with it OFF, so no bias UI appears anywhere.
The experimental build (or a source checkout / an explicit env override)
turns it ON: a connector appears in the Setup tab below the oscilloscope,
and each experiment tab's closed-loop config panel appears once
INTERSTELLAR is connected.

Covers:
  * ``feature_flags.interstellar_enabled()`` resolution order.
  * ConnectionPanel builds / omits the ``bias_connector`` accordingly.
  * Experiment tabs build / omit the ``_bias_feedback_panel``.
  * Connecting INTERSTELLAR from the Setup panel reveals the per-tab
    config panel (for a monopolar config) and disconnecting hides it.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6 import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


# ---------------------------------------------------------------------------
# feature_flags.interstellar_enabled() — pure resolution logic (no Qt)
# ---------------------------------------------------------------------------
def test_env_override_true(monkeypatch):
    from stimtest.feature_flags import interstellar_enabled
    for value in ("1", "true", "YES", "On"):
        monkeypatch.setenv("PULSAR_ENABLE_INTERSTELLAR", value)
        assert interstellar_enabled() is True, value


def test_env_override_false(monkeypatch):
    from stimtest.feature_flags import interstellar_enabled
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("PULSAR_ENABLE_INTERSTELLAR", value)
        assert interstellar_enabled() is False, value


def test_default_from_source_is_enabled(monkeypatch):
    """A source checkout / test run (not frozen, no override) → ON, so a
    developer sees the module without setting anything."""
    from stimtest import feature_flags
    monkeypatch.delenv("PULSAR_ENABLE_INTERSTELLAR", raising=False)
    # A pytest process is not a PyInstaller build.
    assert getattr(sys, "frozen", False) is False
    assert feature_flags.interstellar_enabled() is True


def test_frozen_requires_sentinel(monkeypatch, tmp_path):
    """A frozen app is ON only when the experimental build bundled the
    sentinel flag file next to the executable."""
    from stimtest import feature_flags
    monkeypatch.delenv("PULSAR_ENABLE_INTERSTELLAR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    # No sentinel yet → public build → OFF.
    assert feature_flags.interstellar_enabled() is False
    # Experimental build bundles the sentinel → ON.
    (tmp_path / feature_flags.INTERSTELLAR_FLAG_FILE).write_text("x")
    assert feature_flags.interstellar_enabled() is True


# ---------------------------------------------------------------------------
# ConnectionPanel — Setup connector present only when enabled
# ---------------------------------------------------------------------------
def test_connection_panel_connector_present_when_enabled(qapp, monkeypatch):
    monkeypatch.setenv("PULSAR_ENABLE_INTERSTELLAR", "1")
    from stimtest.gui.connection_panel import ConnectionPanel
    conn = ConnectionPanel()
    assert hasattr(conn, "bias_connector")
    conn.deleteLater()


def test_connection_panel_connector_absent_when_disabled(qapp, monkeypatch):
    monkeypatch.setenv("PULSAR_ENABLE_INTERSTELLAR", "0")
    from stimtest.gui.connection_panel import ConnectionPanel
    conn = ConnectionPanel()
    assert not hasattr(conn, "bias_connector")
    # The driver facade still exists (harmless dead code in the public
    # build) — it's the UI that's gone.
    assert hasattr(conn, "open_bias_module")
    conn.deleteLater()


# ---------------------------------------------------------------------------
# Experiment tab — config panel built only when enabled
# ---------------------------------------------------------------------------
def test_tab_bias_panel_built_when_enabled(qapp, monkeypatch):
    monkeypatch.setenv("PULSAR_ENABLE_INTERSTELLAR", "1")
    from stimtest.electrode import ElectrodeArray
    from stimtest.gui.experiment_tabs import VoltageTransientTab
    tab = VoltageTransientTab(ElectrodeArray.utah_4x4())
    assert tab._bias_feedback_panel is not None
    # Built but hidden — INTERSTELLAR isn't connected yet.
    assert not tab._bias_feedback_panel.isVisibleTo(tab._bias_feedback_panel)
    tab.deleteLater()


def test_tab_bias_panel_absent_when_disabled(qapp, monkeypatch):
    monkeypatch.setenv("PULSAR_ENABLE_INTERSTELLAR", "0")
    from stimtest.electrode import ElectrodeArray
    from stimtest.gui.experiment_tabs import VoltageTransientTab
    tab = VoltageTransientTab(ElectrodeArray.utah_4x4())
    assert tab._bias_feedback_panel is None
    tab.deleteLater()


# ---------------------------------------------------------------------------
# Integration — Setup connect/disconnect drives per-tab visibility
# ---------------------------------------------------------------------------
class _ComboStub:
    def __init__(self, configs):
        self._configs = configs

    def selected_configurations(self):
        return list(self._configs)


def test_setup_connect_reveals_and_disconnect_hides_tab_panel(qapp, monkeypatch):
    monkeypatch.setenv("PULSAR_ENABLE_INTERSTELLAR", "1")
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.gui.connection_panel import ConnectionPanel
    from stimtest.gui.experiment_tabs import VoltageTransientTab

    conn = ConnectionPanel()
    tab = VoltageTransientTab(ElectrodeArray.utah_4x4())
    # Force a monopolar config so gate 2 (config mix) passes once connected.
    tab.combo_panel = _ComboStub([Configuration.monopolar(1)])
    tab.set_bias_host(conn)
    panel = tab._bias_feedback_panel
    # The panel is parented but the tab isn't shown, so isVisible() is
    # always False — assert on isHidden() (explicit hide state) instead.

    # Not connected yet → hidden.
    assert tab._bias_connected is False
    assert panel.isHidden()

    # Connect INTERSTELLAR from the Setup panel (simulated driver).
    conn.open_bias_module(simulate=True)
    assert tab._bias_connected is True
    assert not panel.isHidden()

    # Disconnect → hidden again.
    conn.close_bias_module()
    assert tab._bias_connected is False
    assert panel.isHidden()

    tab.deleteLater()
    conn.deleteLater()
