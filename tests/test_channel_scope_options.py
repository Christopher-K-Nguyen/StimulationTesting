"""Per-channel scope bandwidth + coupling override dropdowns (Setup tab).

Operator: "put the bandwidth and coupling dropdown lists in line with the
oscilloscope channel dropdown lists."  Each scope channel row now carries, in
line with its Role dropdown, a Bandwidth (Auto / Full / 20 MHz) and a Coupling
(Auto / DC / AC) dropdown.  "Auto" preserves the automatic policies (full BW on
data / 20 MHz on the trigger channel — gotcha #162; role-based DC/AC coupling —
gotcha #83); a concrete choice overrides that channel at run start.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")


# ---------------------------------------------------------------------------
# Driver-side overrides (no Qt / hardware needed)
# ---------------------------------------------------------------------------
def _bare_scope():
    from stimtest.hardware.tektronix import TektronixOscilloscope
    return TektronixOscilloscope.__new__(TektronixOscilloscope)


def test_coupling_override_wins_over_role_default():
    s = _bare_scope()
    # I_mon-edge trigger → role default would be DC.
    s.channel_aliases = {"imon": "CH2", "trigger": "CH2"}
    s.set_channel_coupling_overrides({"CH2": "AC", "CH1": "Auto"})
    assert s._coupling_for("CH2") == "AC"    # override wins
    assert s._coupling_for("CH1") == "DC"    # Auto → role default (→ DC)


def test_coupling_override_filters_auto_and_junk():
    s = _bare_scope()
    s.channel_aliases = {"imon": "CH2"}
    s.set_channel_coupling_overrides({"CH2": "Auto", "CH3": "garbage"})
    assert s._channel_coupling_override == {}
    assert s._coupling_for("CH2") == "DC"    # role default applies


def test_bandwidth_overrides_normalize_and_filter():
    s = _bare_scope()
    s.set_channel_bandwidth_overrides(
        {"CH1": "Full", "CH2": "20 MHz", "CH3": "Auto", "CH4": "junk"})
    assert s._channel_bandwidth_override == {"CH1": "full", "CH2": "20mhz"}


def test_apply_bandwidths_honors_override():
    from stimtest.gui.experiment_tabs import _apply_channel_bandwidths

    class _S:
        def __init__(self):
            self.calls = []
            self._channel_bandwidth_override = {"CH1": "20mhz", "CH2": "full"}

        def set_channel_bandwidth_for_purpose(self, ch, p):
            self.calls.append((ch, "20", p)); return 20.0

        def set_channel_bandwidth_full(self, ch):
            self.calls.append((ch, "full")); return float("inf")

    s = _S()
    # CH1 is the trigger but overridden to 20 MHz; CH2 overridden to Full.
    res = _apply_channel_bandwidths(
        s, {"imon": "CH1", "vmon": "CH2", "trigger": "CH1"})
    kinds = {ch: kind for (_a, ch, _b, kind) in res}
    assert kinds["CH1"] == "manual-20MHz"
    assert kinds["CH2"] == "manual-full"


def _bw_scope(overrides):
    class _S:
        def __init__(self):
            self.calls = []
            self._channel_bandwidth_override = overrides
        def set_channel_bandwidth_for_purpose(self, ch, p):
            self.calls.append((ch, "20", p)); return 20.0
        def set_channel_bandwidth_full(self, ch):
            self.calls.append((ch, "full")); return float("inf")
    return _S()


def test_imon_as_trigger_auto_gets_20mhz():
    """No separate Trigger channel → I_mon is the trigger source → its channel
    is band-limited to 20 MHz (operator: 'if there is no Trigger channel, set
    Imon as trigger source and 20 MHz')."""
    from stimtest.gui.experiment_tabs import _apply_channel_bandwidths
    s = _bw_scope({})                                    # all Auto
    res = _apply_channel_bandwidths(
        s, {"vmon": "CH1", "imon": "CH2", "trigger": "CH2"})   # trigger == imon
    bw = {ch: b for (_a, ch, b, _k) in res}
    assert bw["CH2"] == 20.0                             # I_mon trigger → 20 MHz
    assert bw["CH1"] == float("inf")                     # data channel stays full


def test_imon_as_trigger_full_override_forced_to_20mhz():
    """A 'Full' override on the I_mon channel can't defeat the 20 MHz trigger
    requirement when I_mon is the trigger source."""
    from stimtest.gui.experiment_tabs import _apply_channel_bandwidths
    s = _bw_scope({"CH2": "full"})
    res = _apply_channel_bandwidths(
        s, {"vmon": "CH1", "imon": "CH2", "trigger": "CH2"})
    kinds = {ch: kind for (_a, ch, _b, kind) in res}
    bw = {ch: b for (_a, ch, b, _k) in res}
    assert kinds["CH2"] == "trigger-imon-forced20"
    assert bw["CH2"] == 20.0                             # forced, not full
    assert bw["CH1"] == float("inf")


def test_separate_trigger_channel_full_override_still_full():
    """The force is I_mon-as-trigger ONLY — a SEPARATE digital Trigger channel
    still honors a 'Full' override (the operator didn't ask to force that)."""
    from stimtest.gui.experiment_tabs import _apply_channel_bandwidths
    s = _bw_scope({"CH4": "full"})
    res = _apply_channel_bandwidths(
        s, {"vmon": "CH1", "imon": "CH2", "trigger": "CH4"})
    kinds = {ch: kind for (_a, ch, _b, kind) in res}
    assert kinds["CH4"] == "manual-full"


# ---------------------------------------------------------------------------
# Setup-tab UI + state + prefs + snapshot
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def _app():
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    app.setApplicationName("pulsar-pytest")
    return app


def _setup(_app):
    from stimtest.gui.setup_tab import SetupTab
    return SetupTab()


def test_combos_exist_and_default_auto(_app):
    st = _setup(_app)
    assert set(st._bw_combos) == {"CH1", "CH2", "CH3", "CH4"}
    assert set(st._coupling_combos) == {"CH1", "CH2", "CH3", "CH4"}
    assert all(cb.currentText() == "Auto" for cb in st._bw_combos.values())
    assert all(cb.currentText() == "Auto"
               for cb in st._coupling_combos.values())


def test_getters_reflect_selection(_app):
    st = _setup(_app)
    st._bw_combos["CH1"].setCurrentText("Full")
    st._coupling_combos["CH2"].setCurrentText("AC")
    assert st.current_channel_bandwidths()["CH1"] == "Full"
    assert st.current_channel_couplings()["CH2"] == "AC"


def test_prefs_round_trip(_app):
    st = _setup(_app)
    st._bw_combos["CH1"].setCurrentText("20 MHz")
    st._coupling_combos["CH3"].setCurrentText("DC")
    p = st.current_prefs()
    st2 = _setup(_app)
    st2.restore_prefs(p)
    assert st2._bw_combos["CH1"].currentText() == "20 MHz"
    assert st2._coupling_combos["CH3"].currentText() == "DC"


def test_snapshot_includes_overrides(_app):
    st = _setup(_app)
    st._bw_combos["CH2"].setCurrentText("Full")
    st._coupling_combos["CH4"].setCurrentText("AC")
    snap = st.setup_snapshot()
    assert snap["channel_bandwidths"]["CH2"] == "Full"
    assert snap["channel_couplings"]["CH4"] == "AC"


def test_coupling_offers_dc_plus_ac(_app):
    st = _setup(_app)
    opts = [st._coupling_combos["CH3"].itemText(i)
            for i in range(st._coupling_combos["CH3"].count())]
    assert opts == ["Auto", "DC", "AC", "DC + AC"]


def test_dc_plus_ac_round_trips_in_prefs(_app):
    st = _setup(_app)
    st._coupling_combos["CH3"].setCurrentText("DC + AC")
    p = st.current_prefs()
    st2 = _setup(_app)
    st2.restore_prefs(p)
    assert st2._coupling_combos["CH3"].currentText() == "DC + AC"


# ---------------------------------------------------------------------------
# Role = None → the channel's Bandwidth + Coupling dropdowns are disabled
# (operator: "if an oscilloscope channel is set to none, disable the bandwidth
# and coupling dropdown lists").
# ---------------------------------------------------------------------------
def test_none_role_disables_bw_and_coupling(_app):
    from stimtest.gui.setup_tab import ROLE_NONE, ROLE_VMON
    st = _setup(_app)
    # All roles start None → both dropdowns disabled on every channel.
    for ch in ("CH1", "CH2", "CH3", "CH4"):
        assert st._role_combos[ch].currentText() == ROLE_NONE
        assert not st._bw_combos[ch].isEnabled()
        assert not st._coupling_combos[ch].isEnabled()
    # Assign a role → that channel's dropdowns enable; others stay disabled.
    st._role_combos["CH1"].setCurrentText(ROLE_VMON)
    assert st._bw_combos["CH1"].isEnabled()
    assert st._coupling_combos["CH1"].isEnabled()
    assert not st._bw_combos["CH2"].isEnabled()
    assert not st._coupling_combos["CH2"].isEnabled()
    # Back to None → disabled again.
    st._role_combos["CH1"].setCurrentText(ROLE_NONE)
    assert not st._bw_combos["CH1"].isEnabled()
    assert not st._coupling_combos["CH1"].isEnabled()


def test_restore_prefs_greys_none_role_options(_app):
    from stimtest.gui.setup_tab import ROLE_VMON
    st = _setup(_app)
    st._role_combos["CH1"].setCurrentText(ROLE_VMON)     # CH1 used, rest None
    p = st.current_prefs()
    st2 = _setup(_app)
    st2.restore_prefs(p)
    assert st2._bw_combos["CH1"].isEnabled()             # V_mon → enabled
    assert st2._coupling_combos["CH1"].isEnabled()
    assert not st2._bw_combos["CH2"].isEnabled()         # None → disabled
    assert not st2._coupling_combos["CH2"].isEnabled()


def test_testboard_role_force_refreshes_enable(_app):
    """The test-board device switch forces E_act/E_ret roles to None under
    BLOCKED signals (so _on_role_changed doesn't fire); _apply_scope_role_options
    must still refresh the bw/coupling enabled-state directly — off when forced
    to None, back on when the real role is restored on switch-back."""
    from stimtest.gui.setup_tab import ROLE_ERET
    st = _setup(_app)
    st._role_combos["CH3"].setCurrentText(ROLE_ERET)     # real role → enabled
    assert st._bw_combos["CH3"].isEnabled()
    assert st._coupling_combos["CH3"].isEnabled()
    # No-electrode device: E_ret forced to None (blocked signals) → greyed.
    st._apply_scope_role_options(allow_electrode_roles=False)
    assert st._role_combos["CH3"].currentText() != ROLE_ERET
    assert not st._bw_combos["CH3"].isEnabled()
    assert not st._coupling_combos["CH3"].isEnabled()
    # Switch back to a real array: stashed E_ret restored → re-enabled.
    st._apply_scope_role_options(allow_electrode_roles=True)
    assert st._role_combos["CH3"].currentText() == ROLE_ERET
    assert st._bw_combos["CH3"].isEnabled()
    assert st._coupling_combos["CH3"].isEnabled()


def test_imon_bandwidth_locked_when_no_trigger_channel(_app):
    """No Trigger channel → I_mon is the trigger → its bandwidth is forced to
    20 MHz, so the dropdown is locked + pinned to 20 MHz (operator: "disable
    editing the Imon bandwidth if there is no trigger channel").  Coupling
    stays editable."""
    from stimtest.gui.setup_tab import ROLE_VMON, ROLE_IMON
    st = _setup(_app)
    st._role_combos["CH1"].setCurrentText(ROLE_VMON)
    st._role_combos["CH2"].setCurrentText(ROLE_IMON)    # no Trigger channel
    bw = st._bw_combos["CH2"]
    assert not bw.isEnabled()                           # locked
    assert bw.currentText() == "20 MHz"                 # pinned (honest display)
    assert st._coupling_combos["CH2"].isEnabled()       # coupling stays editable


def test_imon_bandwidth_unlocks_and_restores_with_trigger(_app):
    from stimtest.gui.setup_tab import ROLE_IMON, ROLE_TRIG, ROLE_NONE
    st = _setup(_app)
    st._role_combos["CH2"].setCurrentText(ROLE_IMON)
    st._role_combos["CH4"].setCurrentText(ROLE_TRIG)    # Trigger present
    bw = st._bw_combos["CH2"]
    assert bw.isEnabled()
    bw.setCurrentText("Full")                           # operator's choice
    # Remove the Trigger channel → I_mon becomes the trigger → bw locked.
    st._role_combos["CH4"].setCurrentText(ROLE_NONE)
    assert not bw.isEnabled()
    assert bw.currentText() == "20 MHz"
    # Re-add the Trigger channel → bw re-enabled + the "Full" choice restored.
    st._role_combos["CH4"].setCurrentText(ROLE_TRIG)
    assert bw.isEnabled()
    assert bw.currentText() == "Full"


# ---------------------------------------------------------------------------
# "DC + AC" per-channel → the DC→AC capture trick (gotcha #85), resolved by VT
# ---------------------------------------------------------------------------
def _bare_vt():
    from stimtest.experiments.voltage_transient import VoltageTransientExperiment
    vt = VoltageTransientExperiment.__new__(VoltageTransientExperiment)

    class _Scope:
        channel_aliases = {"vmon": "CH1", "imon": "CH2",
                           "eret": "CH3", "eact": "CH4"}
    vt.scope = _Scope()
    return vt


def test_per_channel_dcac_selects_that_role():
    vt = _bare_vt()
    snap = {"channel_couplings": {"CH3": "DC + AC"}}
    assert set(vt._resolve_dc_ac_roles(snap)) == {"eret"}


def test_legacy_global_eret_coupling_key_is_ignored():
    # The global "Electrode coupling" dropdown was removed; a legacy
    # ``eret_coupling`` in an old snapshot no longer opts electrodes into the
    # trick — only a per-channel "DC + AC" does.
    vt = _bare_vt()
    snap = {"channel_couplings": {}, "eret_coupling": "dc_ac"}
    assert vt._resolve_dc_ac_roles(snap) == ()


def test_per_channel_dc_excludes_that_role():
    # A channel forced to plain DC is not in the trick; another DC+AC channel is.
    vt = _bare_vt()
    snap = {"channel_couplings": {"CH3": "DC", "CH4": "DC + AC"}}
    assert set(vt._resolve_dc_ac_roles(snap)) == {"eact"}


def test_dcac_on_vmon_is_listed_but_safe():
    vt = _bare_vt()
    snap = {"channel_couplings": {"CH1": "DC + AC"}}
    # V_mon may be listed; the helper's bias-ratio gate no-ops it.
    assert "vmon" in vt._resolve_dc_ac_roles(snap)


def test_no_dcac_anywhere_is_empty():
    vt = _bare_vt()
    snap = {"channel_couplings": {}}
    assert vt._resolve_dc_ac_roles(snap) == ()


def test_dcac_not_pushed_to_scope_as_raw_coupling():
    s = _bare_scope()
    s.set_channel_coupling_overrides({"CH3": "DC + AC", "CH1": "DC"})
    # DC + AC is runner-managed, NOT a raw scope coupling → excluded.
    assert s._channel_coupling_override == {"CH1": "DC"}


# ---------------------------------------------------------------------------
# End-to-end: the VT runner calls the DC→AC helper only when a channel is set
# to "DC + AC" (migrated from the removed global-coupling test).
# ---------------------------------------------------------------------------
def _run_counting_helper_calls(channel_couplings):
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.experiments.voltage_transient import (
        RampPolicy, VoltageTransientExperiment)
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern

    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=1)   # anodic
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4())
    test.extras = {"setup_snapshot": {"channel_couplings": channel_couplings}}
    session = Session(notebook="t", subject="s", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    # E_ret on CH3 so a per-channel "DC + AC" there resolves to the eret role.
    scope.channel_aliases = {"vmon": "CH1", "imon": "CH2",
                             "eret": "CH3", "eact": "CH4"}
    # max_ua == pattern amplitude → the ramp ends after one capture, but that
    # capture still exercises _one_capture's DC→AC gate.
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(strategy="increment", coarse_step_ua=500.0, max_ua=10.0),
        cathodic_limit_v=-0.8, anodic_limit_v=0.6)
    calls = {"n": 0}

    def _spy(*a, **k):
        calls["n"] += 1
    runner.measure_electrode_dc_offsets_and_switch_to_ac = _spy
    try:
        runner.run()
    except Exception:
        pass  # the gate fires before any capture read
    finally:
        try: stim.close()
        except Exception: pass
        try: scope.close()
        except Exception: pass
    return calls["n"]


def test_no_dcac_channel_skips_the_helper():
    assert _run_counting_helper_calls({}) == 0
    assert _run_counting_helper_calls({"CH3": "DC"}) == 0


def test_dcac_channel_runs_the_helper():
    assert _run_counting_helper_calls({"CH3": "DC + AC"}) >= 1
