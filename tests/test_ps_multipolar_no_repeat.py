"""Progressive Stress multipolar NON-OVERLAP (operator, 0.2.152):

  "PS … tests the selected channel/combo sequentially.  For multipolar, a
   channel cannot be repeated as active or return."

PS lets several (partial) multipolar combos (BP/TP/PBP/PTP) be selected +
stressed sequentially, but the combination panel greys out any combo that
shares a channel (active OR return) with an already-selected combo, so no
channel is stressed twice.  Monopolar / Common Ground stay multi-select
(each combo is a distinct active electrode — no on-array return overlap that
matters here), per the operator's "BP/TP/PBP/PTP only".
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

import pytest

pytest.importorskip("PyQt6")
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from stimtest.gui.combination_panel import CombinationPanel, KIND_BP, KIND_MONO
from stimtest.electrode import ElectrodeArray

_app = QApplication.instance() or QApplication([])


def _chanset(c):
    return {int(c.active)} | {int(r) for r in c.returns}


def _disjoint(cfgs):
    s = [_chanset(c) for c in cfgs]
    return all(not (s[i] & s[j])
               for i in range(len(s)) for j in range(i + 1, len(s)))


def _ps_panel(kind=KIND_BP, glob=False, actives=None):
    p = CombinationPanel(single_mode=True, multipolar_no_repeat=True)   # PS
    p.set_array(ElectrodeArray.utah_4x4())
    p.set_global_return(glob)
    p.set_actives(actives or list(range(1, 17)))
    p.kind_combo.setCurrentText(kind)
    return p


def test_ps_multipolar_is_multiselect_default_one():
    """PS multipolar is MULTI-select (not locked to one like LP) but defaults
    to a single combo — the user adds channel-disjoint combos one at a time."""
    p = _ps_panel()
    assert p._single_mode is False
    assert len(p.selected_configurations()) == 1


def test_ps_select_all_is_channel_disjoint():
    """'Select all' → a greedy MAXIMAL channel-disjoint set (not literally
    every combo, which would reuse channels)."""
    p = _ps_panel()
    p._on_select_all_toggled(True)
    cfgs = p.selected_configurations()
    assert len(cfgs) > 1                       # 4×4 bipolar → up to 8 pairs
    assert _disjoint(cfgs)


def test_ps_picking_one_greys_channel_sharers():
    p = _ps_panel()
    p._on_select_all_toggled(False)                       # clear
    p.combos_list.item(0).setCheckState(Qt.CheckState.Checked)
    first = _chanset(p.selected_configurations()[0])
    for i in range(1, p.combos_list.count()):
        if _chanset(p._combos[i].config) & first:
            enabled = bool(p.combos_list.item(i).flags()
                           & Qt.ItemFlag.ItemIsEnabled)
            assert not enabled, f"combo {i} shares a channel yet is selectable"


def test_ps_selection_never_overlaps_after_manual_adds():
    """Checking every still-enabled row in turn must keep the selected set
    channel-disjoint the whole way (the greying gates each add)."""
    p = _ps_panel()
    p._on_select_all_toggled(False)
    for i in range(p.combos_list.count()):
        item = p.combos_list.item(i)
        if item.flags() & Qt.ItemFlag.ItemIsEnabled:
            item.setCheckState(Qt.CheckState.Checked)   # greying updates live
    assert _disjoint(p.selected_configurations())


def test_ps_deselect_reenables_freed_combos():
    """Unchecking a combo frees its channels — combos that only clashed with
    it become selectable again."""
    p = _ps_panel()
    p._on_select_all_toggled(False)
    p.combos_list.item(0).setCheckState(Qt.CheckState.Checked)
    disabled_after_pick = [i for i in range(p.combos_list.count())
                           if not (p.combos_list.item(i).flags()
                                   & Qt.ItemFlag.ItemIsEnabled)]
    assert disabled_after_pick                          # something got greyed
    p.combos_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    # With nothing selected, every combo is selectable again.
    all_enabled = all(p.combos_list.item(i).flags() & Qt.ItemFlag.ItemIsEnabled
                      for i in range(p.combos_list.count()))
    assert all_enabled


def test_ps_monopolar_stays_multiselect():
    """BP/TP/PBP/PTP only — Monopolar is unaffected (multi-active, all
    selected by default)."""
    p = _ps_panel(kind=KIND_MONO, glob=True, actives=[1, 2, 3])
    assert p._single_mode is False
    assert len(p.selected_configurations()) == 3


def test_ps_tab_flag_and_scope():
    from stimtest.gui.experiment_tabs import (ProgressiveStressTab,
                                             _BaseExperimentTab,
                                             LongPulsingTab)
    assert ProgressiveStressTab.MULTIPOLAR_NO_REPEAT is True
    assert _BaseExperimentTab.MULTIPOLAR_NO_REPEAT is False   # VT/SP default
    # LP uses single-combo multipolar, NOT the no-repeat multi mode.
    assert LongPulsingTab.MULTIPOLAR_NO_REPEAT is False
    assert LongPulsingTab.MULTIPOLAR_SINGLE is True
