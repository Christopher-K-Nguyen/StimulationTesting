"""BiasFeedbackPanel visibility gate — two conditions, in order.

The per-tab INTERSTELLAR closed-loop config panel is shown only when
BOTH are true (see ``_BaseExperimentTab._refresh_bias_visibility``):

1. **INTERSTELLAR is connected** (operator: "If INTERSTELLAR is not
   connected, then the interpulse bias option is hidden in the test
   parameters").  The tab tracks this in ``_bias_connected``, driven by
   the ConnectionPanel's biasConnected / biasDisconnected signals.
2. **At least one MONOPOLAR (``id == "MP"``) config is queued** — the
   bias module drives a dedicated counter electrode, so it has nothing
   to do for BP / TP / CG configs (which return current through array
   electrodes).

Tests use the concrete VoltageTransientTab (multi-config) and
ShortPulsingTab (single-config) so the gate is exercised on both
shapes of combo panel.
"""
from __future__ import annotations

import sys

import pytest

from PyQt6 import QtWidgets

from stimtest.electrode import Configuration, ElectrodeArray


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    yield app


@pytest.fixture
def array_4x4():
    """4x4 Utah-shaped array — enough channels to support BP / TP /
    MP enumeration without edge effects."""
    return ElectrodeArray.utah_4x4()


# ---------------------------------------------------------------------------
# Helper — bypass full GUI construction
# ---------------------------------------------------------------------------
class _ComboStub:
    """Minimal combo panel surface used by _refresh_bias_visibility."""

    def __init__(self, configs):
        self._configs = list(configs)

    def selected_configurations(self):
        return list(self._configs)


def _bare_tab_with_combo_and_panel(qapp, configs, *, connected):
    """Build just enough of the _BaseExperimentTab surface to
    exercise the visibility gate without spinning up the full
    Test-Parameters page (and its 1000-line construction chain).

    ``connected`` stamps the ``_bias_connected`` flag the gate reads —
    it stands in for "the operator has connected INTERSTELLAR from the
    Setup tab."
    """
    import types

    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    class _Stub:
        pass

    tab = _Stub()
    # The tabs build the panel WITHOUT the embedded connector (connection
    # lives in Setup); mirror that here.
    tab._bias_feedback_panel = BiasFeedbackPanel(include_connector=False)
    tab.combo_panel = _ComboStub(configs)
    tab._bias_connected = connected
    # Bind the real gate + connect/disconnect handlers onto the stub so
    # calling _on_bias_connected(tab) (which internally does
    # self._refresh_bias_visibility()) resolves against the stub.
    for name in ("_refresh_bias_visibility",
                 "_on_bias_connected", "_on_bias_disconnected"):
        setattr(tab, name,
                types.MethodType(getattr(_BaseExperimentTab, name), tab))
    return tab


# ---------------------------------------------------------------------------
# Gate 1 — connection
# ---------------------------------------------------------------------------
def test_hidden_when_not_connected_even_with_mp(qapp):
    """Not connected → HIDDEN even with a monopolar config queued.  This
    is the operator's core requirement."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.monopolar(1)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs, connected=False)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


# ---------------------------------------------------------------------------
# Gate 2 — config mix (evaluated only once connected)
# ---------------------------------------------------------------------------
def test_visible_when_connected_and_mp(qapp):
    """Connected + at least one MP config → VISIBLE."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.monopolar(1), Configuration.bipolar(2, 6)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs, connected=True)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert tab._bias_feedback_panel.isVisibleTo(tab._bias_feedback_panel)


def test_hidden_when_connected_but_only_bipolar(qapp):
    """Connected but BP-only → HIDDEN.  Bias module has nothing to do
    because the return current goes through an array electrode."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.bipolar(1, 5), Configuration.bipolar(2, 6)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs, connected=True)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


def test_hidden_when_connected_but_only_tripolar(qapp):
    """Connected but TP-only → HIDDEN.  Same rationale as bipolar."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.tripolar(6, 5, 7),
               Configuration.tripolar(10, 9, 11)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs, connected=True)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


def test_hidden_when_connected_but_no_configs(qapp):
    """Connected but empty combo list → HIDDEN.  Symmetric with the
    Start-button disabled state."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    tab = _bare_tab_with_combo_and_panel(qapp, [], connected=True)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


def test_visible_when_connected_and_mixed_mp_and_bp(qapp):
    """Connected + mixed MP + BP queue → VISIBLE (the MP iteration uses
    the bias module).  At least one MP is enough."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.bipolar(1, 5),
               Configuration.monopolar(7),       # the MP iteration
               Configuration.tripolar(10, 9, 11)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs, connected=True)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


# ---------------------------------------------------------------------------
# Connect / disconnect handlers flip the gate live
# ---------------------------------------------------------------------------
def test_connect_disconnect_handlers_toggle_visibility(qapp):
    """``_on_bias_connected`` / ``_on_bias_disconnected`` flip
    ``_bias_connected`` and re-run the gate — so connecting INTERSTELLAR
    reveals the panel (for an MP config) and disconnecting hides it."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    tab = _bare_tab_with_combo_and_panel(
        qapp, [Configuration.monopolar(1)], connected=False)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(tab._bias_feedback_panel)

    _BaseExperimentTab._on_bias_connected(tab)
    assert tab._bias_connected is True
    assert tab._bias_feedback_panel.isVisibleTo(tab._bias_feedback_panel)

    _BaseExperimentTab._on_bias_disconnected(tab)
    assert tab._bias_connected is False
    assert not tab._bias_feedback_panel.isVisibleTo(tab._bias_feedback_panel)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_no_op_when_panel_missing(qapp):
    """Construction-order guard: the visibility-refresh slot is wired to
    combinationsChanged BEFORE the panel exists (signal fires during
    initial combo_panel.set_array).  Method must no-op safely on a
    missing attribute rather than AttributeError."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    class _BareStub:
        """No Qt parentage, no _bias_feedback_panel attribute."""
        combo_panel = _ComboStub([Configuration.monopolar(1)])

    _BaseExperimentTab._refresh_bias_visibility(_BareStub())


def test_no_op_when_combo_panel_raises(qapp):
    """If the combo panel raises during selected_configurations()
    (transient state during teardown / partial construction), treat as
    'no configs' and hide the panel rather than letting the exception
    escape."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    class _ExplodingCombo:
        def selected_configurations(self):
            raise RuntimeError("transient")

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = BiasFeedbackPanel(include_connector=False)
    tab.combo_panel = _ExplodingCombo()
    tab._bias_connected = True   # get past gate 1 to exercise gate 2
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


# ---------------------------------------------------------------------------
# Full-construction smoke (one per tab shape)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("TabCls", [
    "VoltageTransientTab",  # multi-config
    "ShortPulsingTab",      # single-config
    "LongPulsingTab",
    "ProgressiveStressTab",
])
def test_full_tab_construction_starts_hidden(qapp, array_4x4, TabCls):
    """Fresh tab (INTERSTELLAR not connected) → bias panel hidden.  Smoke
    test that the wiring actually works in a real tab, not just a bare
    stub."""
    import stimtest.gui.experiment_tabs as et
    cls = getattr(et, TabCls)
    tab = cls(array_4x4)
    # The panel is built (feature enabled from source) but hidden — no
    # INTERSTELLAR connection yet.
    panel = getattr(tab, "_bias_feedback_panel", None)
    assert panel is not None, (
        f"{TabCls} should expose _bias_feedback_panel after construction")
    assert not panel.isVisibleTo(panel)
