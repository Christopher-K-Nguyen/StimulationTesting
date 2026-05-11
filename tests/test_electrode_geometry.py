"""Tests for the electrode-geometry catalog and per-device defaults.

Six geometry options are supported:
  * circle, square, rectangle (planar pads)
  * cone (3-D etched / tapered tip — Blackrock UEA, MicroProbes FMA)
  * ring, band (cylindrical-shaft / paddle-lead pads)

Square and rectangle expose a Rounded toggle (filleted corners /
pill cap); other geometries hide it.

Per-device defaults:
  * Blackrock UEA / MicroProbes FMA → cone
  * UTD MEA / NeuroNexus → circle (the dataclass-level fallback)
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


# ---------------------------------------------------------------- catalog


def test_six_geometry_options_in_catalog():
    """Catalog exposes the six expected geometries."""
    from stimtest.config import (
        ELECTRODE_GEOMETRIES,
        ELECTRODE_GEOMETRY_CIRCLE, ELECTRODE_GEOMETRY_SQUARE,
        ELECTRODE_GEOMETRY_RECTANGLE, ELECTRODE_GEOMETRY_CONE,
        ELECTRODE_GEOMETRY_RING, ELECTRODE_GEOMETRY_BAND,
    )
    expected = {
        ELECTRODE_GEOMETRY_CIRCLE, ELECTRODE_GEOMETRY_SQUARE,
        ELECTRODE_GEOMETRY_RECTANGLE, ELECTRODE_GEOMETRY_CONE,
        ELECTRODE_GEOMETRY_RING, ELECTRODE_GEOMETRY_BAND,
    }
    assert set(ELECTRODE_GEOMETRIES.keys()) == expected


def test_supports_rounded_only_for_square_and_rectangle():
    """The Rounded toggle is meaningful only for square and
    rectangle. Other geometries (circle, cone, ring, band) flag
    ``supports_rounded = False``."""
    from stimtest.config import (
        ELECTRODE_GEOMETRIES,
        ELECTRODE_GEOMETRY_SQUARE, ELECTRODE_GEOMETRY_RECTANGLE,
        ELECTRODE_GEOMETRY_CIRCLE, ELECTRODE_GEOMETRY_CONE,
        ELECTRODE_GEOMETRY_RING, ELECTRODE_GEOMETRY_BAND,
    )
    rounded_supported = {code for code, geom
                         in ELECTRODE_GEOMETRIES.items()
                         if geom.supports_rounded}
    assert rounded_supported == {ELECTRODE_GEOMETRY_SQUARE,
                                 ELECTRODE_GEOMETRY_RECTANGLE}


def test_cone_is_only_non_planar_geometry():
    """Cone is the only 3-D geometry; the rest are planar pads."""
    from stimtest.config import (
        ELECTRODE_GEOMETRIES, ELECTRODE_GEOMETRY_CONE,
    )
    non_planar = {code for code, geom
                  in ELECTRODE_GEOMETRIES.items()
                  if not geom.is_planar}
    assert non_planar == {ELECTRODE_GEOMETRY_CONE}


# ---------------------------------------------------------------- per-device defaults


def test_blackrock_uea_defaults_to_cone():
    """Blackrock UEA (Omnetics + PCB variants) defaults to cone
    geometry — Utah arrays have etched pyramidal tips."""
    from stimtest.config import DEVICES, ELECTRODE_GEOMETRY_CONE
    assert DEVICES["Blackrock Omnetics (4×4)"].default_geometry == \
        ELECTRODE_GEOMETRY_CONE
    assert DEVICES["Blackrock PCB (4×4)"].default_geometry == \
        ELECTRODE_GEOMETRY_CONE


def test_microprobes_fma_defaults_to_cone():
    """MicroProbes FMA defaults to cone — tapered glass-coated
    tungsten with conical exposed tip."""
    from stimtest.config import DEVICES, ELECTRODE_GEOMETRY_CONE
    assert DEVICES["MicroProbes 16-channel FMA"].default_geometry == \
        ELECTRODE_GEOMETRY_CONE


def test_utd_mea_defaults_to_circle():
    """UTD MEA defaults to circle (planar disk pads)."""
    from stimtest.config import DEVICES, ELECTRODE_GEOMETRY_CIRCLE
    assert DEVICES["UTD MEA"].default_geometry == \
        ELECTRODE_GEOMETRY_CIRCLE


def test_neuronexus_defaults_to_circle():
    """NeuroNexus defaults to circle."""
    from stimtest.config import DEVICES, ELECTRODE_GEOMETRY_CIRCLE
    assert DEVICES["NeuroNexus A4×4"].default_geometry == \
        ELECTRODE_GEOMETRY_CIRCLE


# ---------------------------------------------------------------- ElectrodePosition


def test_electrode_position_geometry_round_trip():
    """``ElectrodePosition`` carries a geometry + rounded flag,
    defaulting to circle / not-rounded."""
    from stimtest.electrode import ElectrodePosition
    from stimtest.config import (
        ELECTRODE_GEOMETRY_CIRCLE, ELECTRODE_GEOMETRY_SQUARE,
    )
    p = ElectrodePosition(number=1, row=0, col=0)
    assert p.geometry == ELECTRODE_GEOMETRY_CIRCLE
    assert p.rounded is False
    p2 = ElectrodePosition(number=1, row=0, col=0,
                           geometry=ELECTRODE_GEOMETRY_SQUARE,
                           rounded=True)
    assert p2.geometry == ELECTRODE_GEOMETRY_SQUARE
    assert p2.rounded is True


def test_electrode_array_from_mapping_propagates_geometry():
    """``ElectrodeArray.from_mapping`` propagates the ``geometry``
    and ``rounded`` defaults to every site, with per-channel
    overrides taking priority."""
    from stimtest.electrode import ElectrodeArray
    from stimtest.config import (
        ELECTRODE_GEOMETRY_CONE, ELECTRODE_GEOMETRY_RECTANGLE,
    )
    arr = ElectrodeArray.from_mapping(
        name="test", mapping=[[1, 2], [3, 4]],
        geometry=ELECTRODE_GEOMETRY_CONE,
        rounded=False,
        per_channel={3: {"geometry": ELECTRODE_GEOMETRY_RECTANGLE,
                         "rounded": True}},
    )
    # Channels 1, 2, 4: device default (cone).
    for ch in (1, 2, 4):
        site = arr[ch]
        assert site.geometry == ELECTRODE_GEOMETRY_CONE
        assert site.rounded is False
    # Channel 3: per-channel override (rectangle, rounded).
    site_3 = arr[3]
    assert site_3.geometry == ELECTRODE_GEOMETRY_RECTANGLE
    assert site_3.rounded is True


# ---------------------------------------------------------------- GUI


def test_setup_tab_geometry_combo_populated(qapp):
    """The Setup tab's geometry combo has all 6 entries."""
    from stimtest.gui.setup_tab import SetupTab
    tab = SetupTab()
    assert tab.geometry_combo.count() == 6


def test_setup_tab_rounded_visible_only_for_square_and_rect(qapp):
    """The Rounded checkbox is visible only when the geometry
    combo is on Square or Rectangle; hidden for circle / cone /
    ring / band."""
    from stimtest.gui.setup_tab import SetupTab
    from stimtest.config import (
        ELECTRODE_GEOMETRY_CIRCLE, ELECTRODE_GEOMETRY_SQUARE,
        ELECTRODE_GEOMETRY_RECTANGLE, ELECTRODE_GEOMETRY_CONE,
        ELECTRODE_GEOMETRY_RING, ELECTRODE_GEOMETRY_BAND,
    )
    tab = SetupTab()
    show_for = {ELECTRODE_GEOMETRY_SQUARE, ELECTRODE_GEOMETRY_RECTANGLE}
    hide_for = {ELECTRODE_GEOMETRY_CIRCLE, ELECTRODE_GEOMETRY_CONE,
                ELECTRODE_GEOMETRY_RING, ELECTRODE_GEOMETRY_BAND}
    for code in show_for | hide_for:
        idx = tab.geometry_combo.findData(code)
        tab.geometry_combo.setCurrentIndex(idx)
        # Visibility is set via ``setVisible`` which works without a
        # shown parent tree — check ``isVisibleTo(tab)``.
        if code in show_for:
            assert tab.geometry_rounded.isVisibleTo(tab), (
                f"Rounded should be visible for {code}")
        else:
            assert not tab.geometry_rounded.isVisibleTo(tab), (
                f"Rounded should be hidden for {code}")


def test_setup_tab_loads_blackrock_geometry_default(qapp):
    """Switching to a Blackrock UEA device sets the geometry combo
    to cone."""
    from stimtest.gui.setup_tab import SetupTab
    from stimtest.config import ELECTRODE_GEOMETRY_CONE
    tab = SetupTab()
    tab.device_combo.setCurrentText("Blackrock Omnetics (4×4)")
    assert tab.geometry_combo.currentData() == ELECTRODE_GEOMETRY_CONE


def test_setup_tab_loads_utd_mea_geometry_default(qapp):
    """Switching to UTD MEA sets the geometry combo to circle."""
    from stimtest.gui.setup_tab import SetupTab
    from stimtest.config import ELECTRODE_GEOMETRY_CIRCLE
    tab = SetupTab()
    tab.device_combo.setCurrentText("UTD MEA")
    assert tab.geometry_combo.currentData() == ELECTRODE_GEOMETRY_CIRCLE


def test_setup_tab_loads_neuronexus_geometry_default(qapp):
    """Switching to NeuroNexus A4×4 sets the geometry combo to circle."""
    from stimtest.gui.setup_tab import SetupTab
    from stimtest.config import ELECTRODE_GEOMETRY_CIRCLE
    tab = SetupTab()
    tab.device_combo.setCurrentText("NeuroNexus A4×4")
    assert tab.geometry_combo.currentData() == ELECTRODE_GEOMETRY_CIRCLE


def test_geometry_flows_into_electrode_array(qapp):
    """``current_array()`` returns an ``ElectrodeArray`` whose
    sites carry the geometry / rounded values currently selected
    in the Setup tab combo."""
    from stimtest.gui.setup_tab import SetupTab
    from stimtest.config import (
        ELECTRODE_GEOMETRY_RECTANGLE, ELECTRODE_GEOMETRY_CIRCLE,
    )
    tab = SetupTab()
    # Pick UTD MEA first (circle) then explicitly switch to rect-
    # rounded to verify both paths.
    tab.device_combo.setCurrentText("UTD MEA")
    arr = tab.current_array()
    for site in arr.sites:
        assert site.geometry == ELECTRODE_GEOMETRY_CIRCLE
    # Switch to rectangle + rounded.
    idx = tab.geometry_combo.findData(ELECTRODE_GEOMETRY_RECTANGLE)
    tab.geometry_combo.setCurrentIndex(idx)
    tab.geometry_rounded.setChecked(True)
    arr2 = tab.current_array()
    for site in arr2.sites:
        assert site.geometry == ELECTRODE_GEOMETRY_RECTANGLE
        assert site.rounded is True


def test_geometry_prefs_round_trip(qapp):
    """The geometry combo + rounded toggle round-trip through
    ``current_prefs`` / ``restore_prefs``."""
    from stimtest.gui.setup_tab import SetupTab
    from stimtest.config import ELECTRODE_GEOMETRY_RING
    tab1 = SetupTab()
    idx = tab1.geometry_combo.findData(ELECTRODE_GEOMETRY_RING)
    tab1.geometry_combo.setCurrentIndex(idx)
    # Rounded toggle is hidden for ring but its STATE persists.
    tab1.geometry_rounded.setChecked(True)
    prefs = tab1.current_prefs()
    assert prefs["geometry"] == ELECTRODE_GEOMETRY_RING
    assert prefs["geometry_rounded"] is True
    tab2 = SetupTab()
    tab2.restore_prefs(prefs)
    assert tab2.geometry_combo.currentData() == ELECTRODE_GEOMETRY_RING
    assert tab2.geometry_rounded.isChecked() is True


def test_legacy_prefs_without_geometry_keys_load_cleanly(qapp):
    """Pre-geometry prefs files don't carry ``geometry`` /
    ``geometry_rounded`` keys; ``restore_prefs`` should fall back
    to the panel default (circle, not rounded)."""
    from stimtest.gui.setup_tab import SetupTab
    from stimtest.config import ELECTRODE_GEOMETRY_CIRCLE
    tab = SetupTab()
    legacy = {"area_value": 5000.0, "coating": "SIROF"}
    tab.restore_prefs(legacy)
    assert tab.geometry_combo.currentData() == ELECTRODE_GEOMETRY_CIRCLE
    assert tab.geometry_rounded.isChecked() is False
