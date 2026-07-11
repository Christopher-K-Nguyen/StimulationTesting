"""I_mon plotting-unit dropdown on the experiment-tab MultiChannelScope.

Operator: "Add a unit dropdown list by Imon in experiment tab to select
plotting in uA or A/cm2" + "Hide the A/cm2 option if there is no area
inputted".

The dropdown sits next to the I_mon axis combo.  Default is µA (matching the
app-wide "default current" preference).  The A/cm² row is hidden + disabled
until a surface area is set; picking A/cm² feeds the raw area to the render
layer (density presentation), and clearing the area snaps back to µA.
"""
from __future__ import annotations

import pytest

from PyQt6 import QtWidgets


@pytest.fixture(scope="module")
def _app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _scope(_app):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    return MultiChannelScope()


def test_dropdown_offers_uA_and_density(_app):
    sc = _scope(_app)
    combo = sc.imon_unit_combo
    texts = [combo.itemText(i) for i in range(combo.count())]
    assert any("µA" in t for t in texts)
    assert any("A/cm" in t for t in texts)
    # Default is µA (current).
    assert sc._imon_unit == "uA"


def test_density_hidden_without_area(_app):
    sc = _scope(_app)
    combo = sc.imon_unit_combo
    # No area → density row hidden + disabled, effective area None (µA).
    assert combo.view().isRowHidden(1) is True
    assert combo.model().item(1).isEnabled() is False
    assert sc._effective_area_um2() is None


def test_density_available_when_area_set(_app):
    sc = _scope(_app)
    sc.set_surface_area_um2(5000.0)
    combo = sc.imon_unit_combo
    assert combo.view().isRowHidden(1) is False
    assert combo.model().item(1).isEnabled() is True
    # Still µA by default → effective area None until the user picks density.
    assert sc._effective_area_um2() is None
    combo.setCurrentIndex(1)                 # pick A/cm²
    assert sc._imon_unit == "density"
    assert sc._effective_area_um2() == 5000.0


def test_clearing_area_snaps_back_to_uA(_app):
    sc = _scope(_app)
    sc.set_surface_area_um2(5000.0)
    sc.imon_unit_combo.setCurrentIndex(1)    # density
    assert sc._imon_unit == "density"
    sc.set_surface_area_um2(None)            # area cleared
    assert sc._imon_unit == "uA"
    assert sc._effective_area_um2() is None
    assert sc.imon_unit_combo.view().isRowHidden(1) is True


def test_unit_choice_round_trips_in_prefs(_app):
    sc = _scope(_app)
    sc.set_surface_area_um2(5000.0)
    sc.imon_unit_combo.setCurrentIndex(1)    # density
    p = sc.current_prefs()
    assert p.get("imon_unit") == "density"
    sc2 = _scope(_app)
    sc2.set_surface_area_um2(5000.0)
    sc2.restore_prefs(p)
    assert sc2._imon_unit == "density"


def test_raw_area_survives_unit_toggle(_app):
    """Toggling to µA must NOT clear the stored area — switching back to
    density restores it without the experiment tab re-pushing the area."""
    sc = _scope(_app)
    sc.set_surface_area_um2(5000.0)
    sc.imon_unit_combo.setCurrentIndex(1)    # density
    sc.imon_unit_combo.setCurrentIndex(0)    # back to µA
    assert sc.surface_area_um2() == 5000.0   # raw area intact
    assert sc._effective_area_um2() is None   # but presenting µA
    sc.imon_unit_combo.setCurrentIndex(1)    # density again
    assert sc._effective_area_um2() == 5000.0


# --- unit dropdown ALIGNMENT (operator: "aligned to the right with the other
# lists") + subtitle J_stim (operator: "show both Istim and Jstim when area
# is available") ---------------------------------------------------------------
def test_unit_dropdown_slot_matches_axis_combo_width(_app):
    """The unit dropdown lives in a fixed-width slot equal to the axis combos'
    width, so its RIGHT edge lines up with the axis dropdowns."""
    from stimtest.gui.multichannel_scope import TRACE_VMON
    sc = _scope(_app)
    slot = sc.imon_unit_combo.parentWidget()      # the fixed-width slot widget
    axis_w = sc.axis_combos[TRACE_VMON].maximumWidth()
    assert axis_w == 112                          # _COMBO_W
    assert slot.minimumWidth() == axis_w and slot.maximumWidth() == axis_w


def _cap(amp, area_metrics=False):
    import numpy as np
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    p = PulsePattern.biphasic(amplitude_ua=amp, polarity=-1 if amp < 0 else 1)
    t = np.linspace(-100.0, 500.0, 200)
    c = Capture(index=0, pattern=p)
    c.time_us = t
    c.v_mon_v = np.where((t >= 0) & (t < 200), abs(amp) / 200.0, 0.0)
    c.i_mon_ua = np.where((t >= 0) & (t < 200), amp, 0.0)
    return c


def test_subtitle_shows_jstim_when_area_set_even_in_ua_mode(_app):
    """J_stim must appear in the heading whenever a surface area is set — even
    while the I_mon TRACE is in µA mode (the µA/density toggle is trace-only;
    the title always uses the RAW area)."""
    import pytest as _pt
    _pt.importorskip("pyqtgraph")
    sc = _scope(_app)
    sc.set_surface_area_um2(5000.0)
    assert sc._imon_unit == "uA"                 # default → trace shown in µA
    assert sc._effective_area_um2() is None       # trace area gated off in µA
    sc.add_capture(_cap(-100.0), "CH01")
    title = sc._pages["CH01"]._title_label.text()
    assert "<i>I</i><sub>stim" in title, title
    assert "<i>J</i><sub>stim" in title, title    # <-- the fix
    # Toggling the trace to density keeps J_stim in the title too.
    sc.imon_unit_combo.setCurrentIndex(1)
    assert "<i>J</i><sub>stim" in sc._pages["CH01"]._title_label.text()


def test_subtitle_omits_jstim_without_area(_app):
    """No surface area → I_stim only (no J_stim)."""
    import pytest as _pt
    _pt.importorskip("pyqtgraph")
    sc = _scope(_app)                             # no area set
    sc.add_capture(_cap(-100.0), "CH01")
    title = sc._pages["CH01"]._title_label.text()
    assert "<i>I</i><sub>stim" in title
    assert "<i>J</i><sub>stim" not in title
