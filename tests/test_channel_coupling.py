"""Channel coupling default: DC for I_mon + V_mon/E_ret/E_act, AC for Trigger.

Operator: "Change Imon to DC coupled" (REVERSED from the earlier AC default —
the I_mon trigger levels were designed against the DC-coupled signal).  Only a
DISTINCT digital-sync Trigger channel stays AC (a large TTL edge still crosses
the 1.4 V level).  I_mon wins when it IS the trigger (the I_mon-edge fallback).
Roles are resolved from ``channel_aliases`` (imon / trigger keys, populated per
run by SetupTab.current_aliases → configure_channels before
apply_channel_defaults).  The small-swing DC-dominated electrode channels
(monopolar E_ret / E_act) are additionally switched DC→AC at RUN time by the
runner's DC→AC helper (gotcha #85), independent of this default.
"""
from __future__ import annotations

from unittest.mock import MagicMock


def _bare():
    from stimtest.hardware.tektronix import TektronixOscilloscope
    return TektronixOscilloscope.__new__(TektronixOscilloscope)


def test_coupling_dc_for_imon_and_electrodes_ac_for_trigger():
    s = _bare()
    s.channel_aliases = {"vmon": "CH1", "imon": "CH2",
                         "eret": "CH3", "eact": "CH4", "trigger": "CH5"}
    assert s._coupling_for("CH1") == "DC"    # V_mon
    assert s._coupling_for("CH2") == "DC"    # I_mon → DC (operator)
    assert s._coupling_for("ch2") == "DC"    # case-insensitive
    assert s._coupling_for("CH3") == "DC"    # E_ret
    assert s._coupling_for("CH4") == "DC"    # E_act
    assert s._coupling_for("CH5") == "AC"    # digital-sync Trigger → AC
    assert s._coupling_for("CH6") == "DC"    # unmapped → DC


def test_coupling_follows_alias_remap():
    s = _bare()
    s.channel_aliases = {"vmon": "CH3", "imon": "CH4", "trigger": "CH1"}
    assert s._coupling_for("CH3") == "DC"    # V_mon remapped, still DC
    assert s._coupling_for("CH4") == "DC"    # I_mon remapped → DC
    assert s._coupling_for("CH1") == "AC"    # Trigger → AC
    assert s._coupling_for("CH2") == "DC"    # unmapped → DC


def test_coupling_imon_edge_trigger_fallback_is_dc():
    # I_mon-edge fallback: current_aliases sets trigger == imon channel.
    # I_mon WINS → DC (a DC-coupled edge is what the comparator wants).
    s = _bare()
    s.channel_aliases = {"vmon": "CH1", "imon": "CH2", "trigger": "CH2"}
    assert s._coupling_for("CH2") == "DC"    # imon == trigger → DC (imon wins)
    assert s._coupling_for("CH1") == "DC"


def test_coupling_missing_trigger_alias_is_safe():
    # At connect the default aliases carry no "trigger" key — must not crash;
    # I_mon resolves to DC.
    s = _bare()
    s.channel_aliases = {"vmon": "CH1", "imon": "CH2",
                         "eret": "CH3", "eact": "CH4"}
    assert s._coupling_for("CH2") == "DC"    # imon → DC
    assert s._coupling_for("CH1") == "DC"
    assert s._coupling_for("CH3") == "DC"


def test_apply_channel_defaults_writes_role_coupling():
    s = _bare()
    s.channel_aliases = {"vmon": "CH1", "imon": "CH2",
                         "eret": "CH3", "trigger": "CH4"}
    s._w = MagicMock()
    s._cmds = MagicMock()
    s._cmds.probe_cmd_tmpl = "{ch}:PRObe 1"
    s.probe_info = lambda ch: {}
    s._invalidate_preamble_cache = MagicMock()
    s._cache_channel_pos = MagicMock()
    s._log = lambda *a, **k: None

    # V_mon / E_ret / I_mon → DC
    for ch in ("CH1", "CH3", "CH2"):
        s._w.reset_mock()
        s.apply_channel_defaults(ch, probe_warning=False)
        writes = [c.args[0] for c in s._w.call_args_list]
        assert f"{ch}:COUPling DC" in writes
        assert f"{ch}:COUPling AC" not in writes
    # Trigger → AC
    for ch in ("CH4",):
        s._w.reset_mock()
        s.apply_channel_defaults(ch, probe_warning=False)
        writes = [c.args[0] for c in s._w.call_args_list]
        assert f"{ch}:COUPling AC" in writes
        assert f"{ch}:COUPling DC" not in writes


def test_default_commands_do_not_hardcode_coupling():
    from stimtest.hardware.tektronix import TektronixOscilloscope
    # COUPling is written from _coupling_for in apply_channel_defaults, NOT
    # from the fixed default-command list.
    joined = " ".join(t for t, _ in
                      TektronixOscilloscope._CHANNEL_DEFAULT_COMMANDS)
    assert "COUPling" not in joined
