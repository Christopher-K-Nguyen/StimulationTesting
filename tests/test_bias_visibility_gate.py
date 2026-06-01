"""Tests for Task #47: BiasFeedbackPanel visibility gate based on
queued configuration mix.

The STM32 bias module only makes sense for monopolar (``id == "MP"``)
configs — every other configuration kind uses an array electrode as
the return path, so the bias module would have nothing to do.  The
panel hides itself when no MP configs are queued so operators on
bipolar / common-ground / tripolar sessions aren't tempted to flip
the closed-loop checkbox.

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


def _bare_tab_with_combo_and_panel(qapp, configs):
    """Build just enough of the _BaseExperimentTab surface to
    exercise the visibility gate without spinning up the full
    Test-Parameters page (and its 1000-line construction chain).

    We use a plain stub class instead of ``_BaseExperimentTab.__new__``
    because QWidget's metaclass needs ``super().__init__()`` to have
    run before any attribute access via ``getattr(self, ...)`` works
    — and the gate method only touches ``self._bias_feedback_panel``
    + ``self.combo_panel``, both of which we can stamp on a plain
    object."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = BiasFeedbackPanel()
    tab.combo_panel = _ComboStub(configs)
    return tab


# ---------------------------------------------------------------------------
# Decision matrix
# ---------------------------------------------------------------------------
def test_visible_when_at_least_one_mp_config(qapp):
    """At least one MP config queued → panel VISIBLE.  Operator can
    pre-configure setpoint / tolerance even before that MP iteration
    is reached."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.monopolar(1), Configuration.bipolar(2, 6)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert tab._bias_feedback_panel.isVisibleTo(tab._bias_feedback_panel)


def test_hidden_when_only_bipolar(qapp):
    """BP-only mix → panel HIDDEN.  Bias module has nothing to do
    because the return current goes through an array electrode."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.bipolar(1, 5), Configuration.bipolar(2, 6)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


def test_hidden_when_only_tripolar(qapp):
    """TP-only mix → panel HIDDEN.  Same rationale as bipolar."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.tripolar(6, 5, 7),
               Configuration.tripolar(10, 9, 11)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


def test_hidden_when_no_configs(qapp):
    """Empty combo list → panel HIDDEN.  Symmetric with the
    Start-button disabled state."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    tab = _bare_tab_with_combo_and_panel(qapp, [])
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert not tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


def test_visible_when_mixed_mp_and_bp(qapp):
    """Mixed MP + BP queue → VISIBLE (the MP iteration uses the
    bias module).  At least one MP is enough."""
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    configs = [Configuration.bipolar(1, 5),
               Configuration.monopolar(7),       # the MP iteration
               Configuration.tripolar(10, 9, 11)]
    tab = _bare_tab_with_combo_and_panel(qapp, configs)
    _BaseExperimentTab._refresh_bias_visibility(tab)
    assert tab._bias_feedback_panel.isVisibleTo(
        tab._bias_feedback_panel)


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_no_op_when_panel_missing(qapp):
    """Construction-order guard: the visibility-refresh slot is
    wired to combinationsChanged BEFORE the panel exists (signal
    fires during initial combo_panel.set_array).  Method must no-op
    safely on missing attribute rather than AttributeError.

    Uses a plain object stub instead of ``_BaseExperimentTab.__new__``
    because the QWidget metaclass requires ``super().__init__()`` to
    have run before any attribute access via ``getattr(self, ...)``
    works — and the whole point of this test is the missing-
    attribute path, which we can exercise on any callable target.
    """
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    class _BareStub:
        """No Qt parentage, no _bias_feedback_panel attribute."""
        combo_panel = _ComboStub([Configuration.monopolar(1)])

    # Calling the unbound method on the stub exercises the
    # getattr(self, "_bias_feedback_panel", None) early-out path
    # without the QWidget construction.
    _BaseExperimentTab._refresh_bias_visibility(_BareStub())


def test_no_op_when_combo_panel_raises(qapp):
    """If the combo panel raises during selected_configurations()
    (transient state during teardown / partial construction), treat
    as 'no configs' and hide the panel rather than letting the
    exception escape."""
    from stimtest.gui.bias_feedback_panel import BiasFeedbackPanel
    from stimtest.gui.experiment_tabs import _BaseExperimentTab

    class _ExplodingCombo:
        def selected_configurations(self):
            raise RuntimeError("transient")

    class _Stub:
        pass

    tab = _Stub()
    tab._bias_feedback_panel = BiasFeedbackPanel()
    tab.combo_panel = _ExplodingCombo()
    # Must not raise — falls back to no-configs path.
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
    """Fresh tab with no configs queued → bias panel hidden.  Smoke
    test that the wiring (combinationsChanged → _refresh_bias_visibility
    + initial call after panel construction) actually works in a real
    tab, not just a bare stub."""
    import stimtest.gui.experiment_tabs as et
    cls = getattr(et, TabCls)
    tab = cls(array_4x4)
    # No configs selected by default at construction.
    panel = getattr(tab, "_bias_feedback_panel", None)
    assert panel is not None, (
        f"{TabCls} should expose _bias_feedback_panel after construction")
    # Visibility on a widget that's not yet shown is determined by
    # isVisibleTo(itself).  Initial state: hidden (no MP configs).
    assert not panel.isVisibleTo(panel)
