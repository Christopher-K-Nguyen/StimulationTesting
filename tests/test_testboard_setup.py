"""Plexon Test Board Setup-tab constraints + tested-OCP standard deviation.

Operator asks covered here:

* "Remove that checkbox for same for all on active/working electrode
  because they would never be different" — the coating "Same for all"
  checkbox is gone from the coating row (hidden + permanently checked so
  the single-coating path is always taken).
* "The Plexon Test Board has the same pinout as the Plexon Omnetics …
  only allow the 2x8 receptacle and [Large Black Omnetics] as choices" →
  clarified to "Large Black Omnetics (like the verification)" and scoped
  to the Test Board only: the cable dropdown offers just those two
  cables when the Test Board is selected, and the full connector list for
  real arrays.
* "When the Test Board is selected only Vmon, Imon, and Trigger are the
  only available oscilloscope channels" — E_act / E_ret drop out of the
  scope-role combos, INCLUDING when the test board is restored from prefs
  at startup (the bug: restore_prefs suppressed the device-change signal
  and never re-applied the constraint).
* "for the tested potential value, have standard deviation" — the
  learned-OCP annotation shows "tested +X ± Y V".
"""
from __future__ import annotations

import os
import statistics
import sys

import pytest

os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")


@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _setup(_app):
    from stimtest.gui.setup_tab import SetupTab
    return SetupTab()


def _role_items(st, ch):
    cb = st._role_combos[ch]
    return [cb.itemText(i) for i in range(cb.count())]


def _cable_items(st):
    c = st.connector_combo
    return [c.itemText(i) for i in range(c.count())]


# --------------------------------------------------------------------------- #
# #151 — coating "Same for all" removed from the UI
# --------------------------------------------------------------------------- #
def test_coating_same_for_all_hidden_and_checked(_app):
    st = _setup(_app)
    # Hidden (removed from the coating row) but permanently checked so every
    # downstream ``coating_mode.isChecked()`` read takes the single-coating
    # path.
    assert st.coating_mode.isHidden() is True
    assert st.coating_mode.isChecked() is True
    # The surface-area "Same for all" checkbox is a DIFFERENT widget and must
    # stay visible/usable (only the coating one was removed).
    assert st.area_mode.isHidden() is False


# --------------------------------------------------------------------------- #
# #152 — cable choices are Test-Board-scoped
# --------------------------------------------------------------------------- #
def test_testboard_cable_choices_restricted(_app):
    from stimtest.config import DEVICES
    st = _setup(_app)
    # Force a real transition (start elsewhere) so _on_device_changed fires.
    st.device_combo.setCurrentText("Linear")
    st.device_combo.setCurrentText("Plexon Test Board")
    expected = list(DEVICES["Plexon Test Board"].cable_choices)
    assert _cable_items(st) == expected
    assert "Large Black Omnetics" in expected
    assert st.connector_combo.currentText() == "Large Black Omnetics"


def test_real_array_keeps_full_cable_list(_app):
    from stimtest.config import CONNECTORS
    st = _setup(_app)
    st.device_combo.setCurrentText("Plexon Test Board")
    st.device_combo.setCurrentText("Linear")
    # A real array keeps the full connector catalog (incl. Omnetics NNX /
    # Plexon / Custom) so its channel routing is unaffected.
    assert _cable_items(st) == list(CONNECTORS.keys())


def test_testboard_cable_restricted_after_prefs_restore(_app):
    from stimtest.config import DEVICES
    st = _setup(_app)
    st.restore_prefs({"device": "Plexon Test Board"})
    assert _cable_items(st) == list(
        DEVICES["Plexon Test Board"].cable_choices)


# --------------------------------------------------------------------------- #
# scope-role restriction — V_mon / I_mon / Trigger (+ None) only
# --------------------------------------------------------------------------- #
def test_testboard_scope_roles_drop_electrodes(_app):
    from stimtest.gui.setup_tab import ROLE_EACT, ROLE_ERET, ROLE_VMON, \
        ROLE_IMON, ROLE_TRIG
    st = _setup(_app)
    st.device_combo.setCurrentText("Linear")
    st.device_combo.setCurrentText("Plexon Test Board")
    for ch in ("CH1", "CH2", "CH3", "CH4"):
        items = _role_items(st, ch)
        assert ROLE_EACT not in items
        assert ROLE_ERET not in items
        # The three real roles remain (plus None).
        assert ROLE_VMON in items and ROLE_IMON in items and ROLE_TRIG in items


def test_testboard_scope_roles_restricted_after_prefs_restore(_app):
    """The operator's actual bug: restore the test board from prefs at
    startup and E_act/E_ret must NOT reappear in the scope-role combos."""
    from stimtest.gui.setup_tab import ROLE_EACT, ROLE_ERET
    st = _setup(_app)
    st.restore_prefs({"device": "Plexon Test Board"})
    for ch in ("CH1", "CH2", "CH3", "CH4"):
        items = _role_items(st, ch)
        assert ROLE_EACT not in items and ROLE_ERET not in items


def test_real_array_restores_electrode_roles(_app):
    from stimtest.gui.setup_tab import ROLE_EACT, ROLE_ERET
    st = _setup(_app)
    st.device_combo.setCurrentText("Plexon Test Board")
    st.device_combo.setCurrentText("Linear")
    # Switching back to a real array brings E_act / E_ret back.
    for ch in ("CH1", "CH2", "CH3", "CH4"):
        items = _role_items(st, ch)
        assert ROLE_EACT in items and ROLE_ERET in items


# --------------------------------------------------------------------------- #
# #153 — standard deviation on the tested OCP
# --------------------------------------------------------------------------- #
@pytest.fixture
def temp_history(tmp_path, monkeypatch):
    import stimtest.electrode_potential_history as eph
    p = tmp_path / "hist.json"
    monkeypatch.setattr(eph, "history_path", lambda: p)
    return eph


def test_learned_ocp_std_matches_sample_stdev(temp_history):
    eph = temp_history
    vals = [0.20, 0.21, 0.19, 0.22, 0.18, 0.205, 0.215, 0.195, 0.225, 0.185,
            0.20, 0.21]
    for v in vals:
        eph.record_sample("Pt", v)
    got = eph.learned_ocp_std_v("Pt")
    assert got is not None
    assert abs(got - statistics.stdev(vals)) < 1e-9


def test_learned_ocp_std_none_below_threshold(temp_history):
    eph = temp_history
    for v in (0.20, 0.21, 0.19):     # < MIN_SAMPLES_FOR_LEARNED_OCP (10)
        eph.record_sample("Pt", v)
    assert eph.learned_ocp_std_v("Pt") is None


def test_learned_ocp_std_zero_for_identical(temp_history):
    eph = temp_history
    for _ in range(12):
        eph.record_sample("Pt", 0.20)
    assert eph.learned_ocp_std_v("Pt") == pytest.approx(0.0)


def test_recommendation_tag_includes_std(_app):
    st = _setup(_app)
    st._learned_ocp = lambda short: 0.217
    st._learned_ocp_std = lambda short: 0.012
    if hasattr(st, "remember_potential_chk"):
        st.remember_potential_chk.setChecked(True)
    tag = st._recommendation_tag("Pt")
    assert "tested" in tag
    assert "+0.217" in tag
    assert "± 0.012" in tag


def test_recommendation_tag_omits_std_when_unavailable(_app):
    st = _setup(_app)
    st._learned_ocp = lambda short: 0.217
    st._learned_ocp_std = lambda short: None
    if hasattr(st, "remember_potential_chk"):
        st.remember_potential_chk.setChecked(True)
    tag = st._recommendation_tag("Pt")
    assert "tested" in tag and "+0.217" in tag
    assert "±" not in tag
