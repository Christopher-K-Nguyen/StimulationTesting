"""Setup tab: cable channel-mapping tree + subscripted remember-potential label.

Operator: "We need a map/tree of channel mapping of the cable" and "Use
subscript for 'Remember return-electrode potential (learn E_oc from E_ret)'".
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _rows(tr):
    return [(tr.topLevelItem(i).text(0), tr.topLevelItem(i).text(1))
            for i in range(tr.topLevelItemCount())]


def test_cable_map_tree_shows_connector_mapping(qapp):
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    tr = t.cable_map_tree
    # Identity cable — pulse CHnn → Plexon CHnn, header flags "(identity)".
    t.connector_combo.setCurrentText("Omnetics UTD")
    assert tr.topLevelItemCount() == 16
    assert _rows(tr)[0] == ("CH01", "CH01")
    assert _rows(tr)[15] == ("CH16", "CH16")
    assert "identity" in tr.headerItem().text(1)
    # Re-ordered cable — device 5 → Plexon 13 (array index 4 = 13).
    t.connector_combo.setCurrentText("Omnetics NNX")
    assert _rows(tr)[4] == ("CH05", "CH13")
    assert "re-mapped" in tr.headerItem().text(1)


def test_matlab_cable_types_receptacle_and_omnetics(qapp):
    """The two cables from MATLAB getCableType.m (operator: "see the cable
    types from my MATLAB on how device CH09 matches Plexon CH01").

    * "2×8 Pin Receptacle" → identity (straight-through).
    * "Plexon Omnetics" → 8-channel bank swap, device CH09 → Plexon CH01
      (MATLAB BLACKROCK_TO_PLEXON_OMNETICS = [9,10,…,16,1,…,8]).
    The older "Omnetics UTD" entry stays identity (nothing re-routes unless
    the new cable is picked).
    """
    from stimtest.config import CONNECTORS
    from stimtest.gui.setup_tab import SetupTab

    # Catalog: both cables present with the right arrays.
    assert CONNECTORS["2×8 Pin Receptacle"].pin_to_channel == tuple(range(1, 17))
    assert CONNECTORS["Plexon Omnetics"].pin_to_channel == (
        9, 10, 11, 12, 13, 14, 15, 16, 1, 2, 3, 4, 5, 6, 7, 8)
    # Omnetics UTD untouched — still identity.
    assert CONNECTORS["Omnetics UTD"].pin_to_channel == tuple(range(1, 17))

    t = SetupTab()
    tr = t.cable_map_tree

    # Receptacle: identity in the tree + no translation.
    t.connector_combo.setCurrentText("2×8 Pin Receptacle")
    assert _rows(tr)[8] == ("CH09", "CH09")
    assert "identity" in tr.headerItem().text(1)
    assert t.current_channel_map() == {}          # identity ⇒ runner never wraps

    # Plexon Omnetics: device CH09 → Plexon CH01 (the operator's data point).
    t.connector_combo.setCurrentText("Plexon Omnetics")
    assert _rows(tr)[8] == ("CH09", "CH01")
    assert _rows(tr)[0] == ("CH01", "CH09")
    assert "re-mapped" in tr.headerItem().text(1)
    cmap = t.current_channel_map()                # {device: plexon}, non-identity
    assert cmap[9] == 1 and cmap[1] == 9 and cmap[16] == 8
    assert len(cmap) == 16                        # full bank swap, every ch moves


def test_remember_label_uses_subscripts(qapp):
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    lbl = t.remember_potential_label.text()
    assert "<sub>oc</sub>" in lbl and "<sub>ret</sub>" in lbl
    # The checkbox API is unchanged (default ON; toggles).
    assert t.remember_potential_chk.isChecked()
    t.remember_potential_chk.setChecked(False)
    assert not t.remember_potential_chk.isChecked()


# ---------------------------------------------------------------- Custom map


def _find_ancestor_titled(widget, title):
    """Walk up the parent chain looking for a QGroupBox with ``title``."""
    from PyQt6 import QtWidgets
    p = widget.parent()
    while p is not None:
        if isinstance(p, QtWidgets.QGroupBox) and p.title() == title:
            return p
        p = p.parent()
    return None


def test_cable_map_tree_lives_on_right_below_device_view(qapp):
    # Operator: "show the cable mapping below the channel mapping on the right
    # side".  The tree sits inside a "Cable channel mapping" group, which is a
    # sibling *below* the device_view in the right-hand column (not the
    # left-hand device form).
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    box = _find_ancestor_titled(t.cable_map_tree, "Cable channel mapping")
    assert box is not None
    right_col = box.parent()
    # device_view and the cable group share the same right-column parent.
    assert t.device_view.parent() is right_col


def test_custom_connector_makes_tree_editable(qapp):
    from PyQt6 import QtCore, QtWidgets
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    # Catalogued cable → read-only, no editable flag.
    t.connector_combo.setCurrentText("Omnetics UTD")
    it0 = t.cable_map_tree.topLevelItem(0)
    assert not (it0.flags() & QtCore.Qt.ItemFlag.ItemIsEditable)
    assert (t.cable_map_tree.editTriggers()
            == QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    # Custom → device-channel column editable; header + hint flip.
    t.connector_combo.setCurrentText("Custom")
    it0 = t.cable_map_tree.topLevelItem(0)
    assert it0.flags() & QtCore.Qt.ItemFlag.ItemIsEditable
    assert (t.cable_map_tree.editTriggers()
            != QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    assert "custom" in t.cable_map_tree.headerItem().text(1).lower()


def _rows_map(tr):
    """{device:int -> plexon:int} read off the tree rows (both cols 'CHnn')."""
    out = {}
    for i in range(tr.topLevelItemCount()):
        it = tr.topLevelItem(i)
        dev = int(it.text(0).replace("CH", ""))
        plx = int(it.text(1).replace("CH", ""))
        out[dev] = plx
    return out


def test_custom_edit_swaps_to_keep_permutation(qapp):
    # Editing pin 1 → CH05 must SWAP with pin 5 (which had CH05), so the map
    # stays a bijection: pin1→CH05 AND pin5→CH01.
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    t.connector_combo.setCurrentText("Custom")
    it0 = t.cable_map_tree.topLevelItem(0)      # Pin 1
    it0.setText(1, "CH05")                       # fires itemChanged → swap
    assert t._custom_cable_map.get(1) == 5
    assert t._custom_cable_map.get(5) == 1
    m = _rows_map(t.cable_map_tree)
    assert m[1] == 5 and m[5] == 1
    # Still a permutation (every channel 1..16 used exactly once).
    assert sorted(m.values()) == list(range(1, 17))


def test_custom_edit_rejects_out_of_range(qapp):
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    t.connector_combo.setCurrentText("Custom")
    it0 = t.cable_map_tree.topLevelItem(0)      # Pin 1 (identity → CH01)
    it0.setText(1, "CH99")                       # invalid → revert
    assert it0.text(1) == "CH01"
    assert t._custom_cable_map.get(1, 1) == 1
    it0.setText(1, "banana")                     # non-numeric → revert
    assert it0.text(1) == "CH01"


def test_custom_map_round_trips_prefs(qapp):
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    t.connector_combo.setCurrentText("Custom")
    t.cable_map_tree.topLevelItem(0).setText(1, "CH03")   # swap 1<->3
    p = t.current_prefs()
    assert p["connector"] == "Custom"
    assert p["custom_cable_map"]["1"] == 3 and p["custom_cable_map"]["3"] == 1
    # Fresh tab restores the same map + connector.
    t2 = SetupTab()
    t2.restore_prefs(p)
    assert t2.connector_combo.currentText() == "Custom"
    assert t2._custom_cable_map.get(1) == 3 and t2._custom_cable_map.get(3) == 1
    assert _rows_map(t2.cable_map_tree)[1] == 3


def test_other_connector_removed(qapp):
    """"Other" was removed (redundant with "Custom") — operator: "Remove
    other as a choice for cable type since that is what custom is for"."""
    from stimtest.config import CONNECTORS
    from stimtest.gui.setup_tab import SetupTab
    assert "Other" not in CONNECTORS
    t = SetupTab()
    # A real array's cable dropdown never lists "Other" any more.
    t.device_combo.setCurrentText("Linear")
    items = [t.connector_combo.itemText(i)
             for i in range(t.connector_combo.count())]
    assert "Other" not in items
    assert "Custom" in items


def test_stale_other_pref_falls_back_to_identity(qapp):
    """A prefs blob saved with the old ``connector = "Other"`` must not
    crash and must resolve to identity (no channel translation) — exactly
    what "Other" did before removal."""
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    t.restore_prefs({"connector": "Other"})
    # Unknown connector → identity map (no translation).
    assert t.current_channel_map() == {}


def test_current_channel_map_identity_and_custom(qapp):
    from stimtest.gui.setup_tab import SetupTab
    t = SetupTab()
    # Identity cable → empty map (no translation; pulse CHnn = device CHnn).
    t.connector_combo.setCurrentText("Omnetics UTD")
    assert t.current_channel_map() == {}
    t.connector_combo.setCurrentText("2×8 Pin Receptacle")
    assert t.current_channel_map() == {}
    # Custom edit: device 1 → Plexon 9 swaps with device 9 → Plexon 1.
    t.connector_combo.setCurrentText("Custom")
    t.cable_map_tree.topLevelItem(0).setText(1, "CH09")
    m = t.current_channel_map()
    assert m.get(1) == 9 and m.get(9) == 1
    assert all(k != v for k, v in m.items())   # identity entries excluded
