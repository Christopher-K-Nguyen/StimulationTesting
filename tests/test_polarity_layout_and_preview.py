"""Tests for the Polarity-row position and polarity-aware shape
preview added in the layout-reorder pass:

* In biphasic SYMMETRIC mode, the Polarity row stays in
  ``top_form`` (right after Pulse style) and the Phase-shape row
  sits at the top of ``sym_box`` — so the read order is
  ``Pulse style → Polarity → Phase shape``.
* In biphasic ASYMMETRIC mode, the Polarity row migrates into
  ``asym_box``'s form (directly below the Asymmetric-shape row)
  — so the read order is ``Asymmetric shape → Polarity → …``.
* Both shape-preview dropdowns (symmetric phase-shape combo +
  asymmetric shape combo) regenerate their per-entry icons when
  the polarity dropdown changes — so the icon's polarity matches
  the live polarity selection.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _row_index_of(form, widget):
    """Return the row index of ``widget`` (in the field role) in
    a ``QFormLayout``, or -1 if not found. ``QFormLayout`` has no
    direct ``rowOf(widget)`` API."""
    from PyQt6 import QtWidgets
    for i in range(form.rowCount()):
        item = form.itemAt(i, QtWidgets.QFormLayout.ItemRole.FieldRole)
        if item is not None and item.widget() is widget:
            return i
    return -1


# ---------------------------------------------------------------- polarity-row migration


def test_polarity_starts_in_top_form_in_symmetric_mode(qapp):
    """A freshly-constructed panel defaults to biphasic +
    symmetric, so the Polarity row should live in
    ``top_form`` (the historical location)."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    assert panel._polarity_location == "top"
    # And the widget is actually findable in top_form at the
    # field-role of some row.
    assert _row_index_of(panel.top_form, panel.polarity) >= 0


def test_polarity_migrates_to_asym_form_in_asymmetric_mode(qapp):
    """Switching the panel to biphasic asymmetric moves the
    Polarity row into ``_asym_form`` (the asym_box's form)."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    assert panel._polarity_location == "asym"
    # Polarity widget is in _asym_form.
    assert _row_index_of(panel._asym_form, panel.polarity) >= 0
    # And NOT in top_form anymore.
    assert _row_index_of(panel.top_form, panel.polarity) < 0


def test_polarity_migrates_back_to_top_when_switching_to_symmetric(qapp):
    """After moving to asymmetric and back to symmetric, the
    Polarity row returns to ``top_form``. Idempotent across
    multiple toggles."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC, SYMMETRIC,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    for _ in range(3):
        panel.symmetry.setCurrentText(ASYMMETRIC)
        assert panel._polarity_location == "asym"
        panel.symmetry.setCurrentText(SYMMETRIC)
        assert panel._polarity_location == "top"


def test_polarity_position_in_asym_form_is_below_shape_row(qapp):
    """The polarity row, after migration, sits directly below
    the Asymmetric-shape row. This is the user-spec layout
    ``Asymmetric shape → Polarity → …``."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    shape_idx = _row_index_of(panel._asym_form, panel.asym_shape_combo)
    polarity_idx = _row_index_of(panel._asym_form, panel.polarity)
    assert shape_idx >= 0
    assert polarity_idx == shape_idx + 1, (
        f"Asym-shape at row {shape_idx}, polarity at row "
        f"{polarity_idx}; polarity should be directly below "
        f"the shape row")


# ---------------------------------------------------------------- symmetric form order


def test_symmetric_phase_shape_is_first_row(qapp):
    """In ``sym_box`` (which is rendered below ``top_form``),
    the Phase-shape row should be the FIRST row — so it sits
    directly below the Polarity row in the visible read order.
    The user-spec is ``Polarity → Phase shape → Amplitude → …``."""
    from stimtest.gui.pattern_panel import PatternControlPanel
    panel = PatternControlPanel()
    # The shape combo should be at row 0 of _sym_form.
    shape_idx = _row_index_of(panel._sym_form, panel.shape_combo)
    amp_idx = _row_index_of(panel._sym_form, panel.amp_excite)
    width_idx = _row_index_of(panel._sym_form, panel.width_shared)
    assert shape_idx == 0
    assert amp_idx > shape_idx
    assert width_idx > amp_idx


# ---------------------------------------------------------------- polarity-aware previews


def test_symmetric_shape_combo_icons_flip_with_polarity(qapp):
    """Changing the Polarity dropdown regenerates the symmetric
    Phase-shape combo's per-entry icons. The new icons mirror
    the old ones across the y=0 axis (cathodic↔anodic), so
    pixmap content differs between the two polarities."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, SYMMETRIC,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(SYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    # Compare icons across the combo for both polarities. Use a
    # non-rectangular shape entry where the flip is visible
    # (rectangular icons look identical with sign flipped since
    # the y=0 baseline is symmetric).
    from stimtest.waveforms import SHAPE_LINEAR_INCREASING
    idx = panel.shape_combo.findData(SHAPE_LINEAR_INCREASING)
    cath_pm = panel.shape_combo.itemIcon(idx).pixmap(80, 28).toImage()
    panel.polarity.setCurrentText("Anodal-first")
    anod_pm = panel.shape_combo.itemIcon(idx).pixmap(80, 28).toImage()
    assert cath_pm != anod_pm


def test_asymmetric_shape_combo_icons_flip_with_polarity(qapp):
    """Same idea for the asymmetric-shape combo — its icons
    regenerate when polarity changes."""
    from stimtest.gui.pattern_panel import (
        PatternControlPanel, BIPHASIC, ASYMMETRIC,
        ASYM_SHAPE_MIX_MATCH,
    )
    panel = PatternControlPanel()
    panel.phase_count.setCurrentText(BIPHASIC)
    panel.symmetry.setCurrentText(ASYMMETRIC)
    panel.polarity.setCurrentText("Cathodal-first")
    idx = panel.asym_shape_combo.findData(ASYM_SHAPE_MIX_MATCH)
    cath_pm = panel.asym_shape_combo.itemIcon(idx).pixmap(
        80, 28).toImage()
    panel.polarity.setCurrentText("Anodal-first")
    anod_pm = panel.asym_shape_combo.itemIcon(idx).pixmap(
        80, 28).toImage()
    assert cath_pm != anod_pm


def test_render_shape_pixmap_polarity_param(qapp):
    """The low-level ``_render_shape_pixmap`` helper accepts a
    ``polarity`` parameter that produces flipped pixmaps."""
    from stimtest.gui.pattern_panel import _render_shape_pixmap
    from stimtest.waveforms import SHAPE_LINEAR_INCREASING
    cath = _render_shape_pixmap(SHAPE_LINEAR_INCREASING, w_px=80,
                                h_px=36, polarity=-1).toImage()
    anod = _render_shape_pixmap(SHAPE_LINEAR_INCREASING, w_px=80,
                                h_px=36, polarity=+1).toImage()
    assert cath != anod


def test_render_asym_shape_pixmap_polarity_param(qapp):
    """Same parameter on the asymmetric helper. Use the
    cap-coupled preview since it has clear asymmetry across the
    y=0 axis."""
    from stimtest.gui.pattern_panel import _render_asym_shape_pixmap
    cath = _render_asym_shape_pixmap("cap_coupled", w_px=80,
                                     h_px=36, polarity=-1).toImage()
    anod = _render_asym_shape_pixmap("cap_coupled", w_px=80,
                                     h_px=36, polarity=+1).toImage()
    assert cath != anod
