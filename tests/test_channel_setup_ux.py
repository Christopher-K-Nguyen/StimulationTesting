"""Tests for LOG_ANALYSIS.md findings #9 (4× probe-attached warning)
and #10 (duplicate channel SELect:ON/OFF at session start).

#9 — Per-channel "scope reports a probe attached" warning was firing
     once per configured channel (4× per connect on a typical 4-
     channel setup), producing ~600 chars of redundant log noise.
     Fix: coalesce into ONE summary line at the end of
     ``configure_channels``.

#10 — Channel SELect:ON/OFF was firing twice at session start
     because both the GUI-side (experiment_tabs.py) and the Tek-
     side (tektronix.configure_channels) ran their own
     OFF→ON pass.  The Tek-side is the canonical owner; the GUI-
     side has been removed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Finding #9 — coalesced probe warning
# ---------------------------------------------------------------------------
def _bare_tek_with_aliases(probe_gains: dict):
    """Construct a TektronixOscilloscope stub where probe_info
    returns the gains specified in ``probe_gains``."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    log_lines: list = []
    scope._w = MagicMock()
    scope._q = MagicMock(return_value="0.0")
    scope._log = log_lines.append
    scope._adapt_state = {}
    scope._preamble_cache = {}
    scope.info = MagicMock()
    scope.info.n_channels = 4
    scope.channel_aliases = {}
    scope._cmds = MagicMock()
    scope._cmds.probe_cmd_tmpl = "{ch}:PRObe:GAIN 1"

    # Stub probe_info to return the gain we specify per channel.
    def _fake_probe_info(channel):
        gain = probe_gains.get(channel, 1.0)
        is_probe = abs(gain - 1.0) > 1e-3
        return {"gain": gain, "type": "" if not is_probe else "10X",
                "is_probe": is_probe}
    scope.probe_info = _fake_probe_info

    return scope, log_lines


def test_configure_channels_emits_no_probe_warning_when_all_direct_bnc():
    """Bench convention: every input is direct BNC (gain=1.0).  No
    probe warning should fire — this is the common case."""
    scope, log_lines = _bare_tek_with_aliases(
        {f"CH{i}": 1.0 for i in range(1, 5)})

    scope.configure_channels(
        {"vmon": "CH1", "imon": "CH2", "eret": "CH3", "trigger": "CH4"})

    warnings = [m for m in log_lines if "probe" in m.lower()
                and "attenuating" in m.lower()]
    assert warnings == [], (
        f"no probe warning should fire when every channel is direct "
        f"BNC; got: {warnings!r}")


def test_configure_channels_emits_single_summary_for_multiple_probes():
    """When MULTIPLE channels report attenuating probes, ONE summary
    line should fire — not N per-channel warnings.  Closes the
    LOG_ANALYSIS.md #9 noise complaint."""
    scope, log_lines = _bare_tek_with_aliases({
        "CH1": 0.1,   # 10x probe
        "CH2": 0.01,  # 100x probe
        "CH3": 1.0,   # direct BNC
        "CH4": 0.1,   # another 10x probe
    })

    scope.configure_channels(
        {"vmon": "CH1", "imon": "CH2", "eret": "CH3", "trigger": "CH4"})

    # Coalesced summary line — should mention all 3 attenuating probes.
    warnings = [m for m in log_lines
                if "attenuating probe" in m]
    assert len(warnings) == 1, (
        f"expected exactly 1 coalesced probe-warning line; got "
        f"{len(warnings)}: {warnings!r}")
    summary = warnings[0]
    # All three attenuating channels should be named.
    assert "CH1" in summary
    assert "CH2" in summary
    assert "CH4" in summary
    # The non-attenuating CH3 should NOT be in the summary.
    assert "CH3" not in summary


def test_configure_channels_no_per_channel_probe_warning():
    """Per-channel "scope reports a probe attached" warnings (the
    pre-fix noise) should be SUPPRESSED inside configure_channels.
    Only the coalesced summary may fire."""
    scope, log_lines = _bare_tek_with_aliases({
        "CH1": 0.1, "CH2": 0.1, "CH3": 0.1, "CH4": 0.1,
    })

    scope.configure_channels(
        {"vmon": "CH1", "imon": "CH2", "eret": "CH3", "trigger": "CH4"})

    # Per-channel warnings (singular "scope reports a probe attached
    # ({ch}:" pattern) should NOT appear.
    per_channel = [m for m in log_lines
                   if "scope reports a probe attached" in m
                   and "channel(s)" not in m]
    assert per_channel == [], (
        f"per-channel probe warnings should be suppressed inside "
        f"configure_channels (only the coalesced summary should "
        f"fire); got: {per_channel!r}")


def test_apply_channel_defaults_still_warns_when_called_standalone():
    """apply_channel_defaults called OUTSIDE configure_channels (e.g.
    for a one-off channel reset) should still emit the per-channel
    warning — the suppression only happens inside configure_channels."""
    scope, log_lines = _bare_tek_with_aliases({"CH1": 0.1})
    scope.apply_channel_defaults("CH1")  # default probe_warning=True

    per_channel = [m for m in log_lines
                   if "scope reports a probe attached" in m]
    assert len(per_channel) == 1, (
        f"standalone apply_channel_defaults should emit a per-channel "
        f"warning when an attenuating probe is detected; got: {per_channel!r}")


def test_apply_channel_defaults_suppression_kwarg():
    """apply_channel_defaults(..., probe_warning=False) suppresses
    the per-channel warning — used by configure_channels."""
    scope, log_lines = _bare_tek_with_aliases({"CH1": 0.1})
    scope.apply_channel_defaults("CH1", probe_warning=False)

    per_channel = [m for m in log_lines
                   if "scope reports a probe attached" in m]
    assert per_channel == [], (
        f"probe_warning=False should suppress the per-channel "
        f"warning; got: {per_channel!r}")


# ---------------------------------------------------------------------------
# Finding #10 — duplicate channel SELect:ON/OFF at session start
# ---------------------------------------------------------------------------
def test_gui_no_longer_runs_its_own_select_pass():
    """Source-level check: the GUI's "turn OFF unused channels"
    pre-pass in experiment_tabs.py was producing redundant SCPI
    traffic on every connect (configure_channels did the same
    work).  Pre-fix the experiment-tab worker had a manual
    SELect:ON/OFF loop; post-fix only the bare
    ``configure_channels`` call should remain in that code path.
    """
    src_path = (Path(__file__).resolve().parent.parent /
                "stimtest/gui/experiment_tabs.py")
    src = src_path.read_text(encoding="utf-8")
    # The pre-fix pattern was a tight loop:
    #   for _ci in range(1, _n_ch + 1):
    #       try:
    #           self._scope._w(f"SELect:CH{_ci} OFF")
    # Make sure THIS exact loop is gone.
    assert 'self._scope._w(f"SELect:CH{_ci} OFF")' not in src, (
        "experiment_tabs.py still has the redundant SELect:CHx OFF "
        "loop — configure_channels handles this now.  LOG_ANALYSIS "
        "#10.")
    # And the matching ON loop:
    assert 'self._scope._w(f"SELect:{_ch} ON")' not in src, (
        "experiment_tabs.py still has the redundant SELect:CHx ON "
        "loop.")
    # The bare configure_channels call should still be there.
    assert "self._scope.configure_channels(self._aliases)" in src, (
        "the canonical configure_channels call is missing — the GUI "
        "still needs to inform the scope which channels are mapped")


def test_configure_channels_writes_select_for_every_channel_once():
    """configure_channels should issue exactly one SELect:CHx
    write per physical channel (ON or OFF based on whether it's
    in ``used``)."""
    scope, log_lines = _bare_tek_with_aliases(
        {f"CH{i}": 1.0 for i in range(1, 5)})

    scope.configure_channels(
        {"vmon": "CH1", "imon": "CH3"})  # CH1 + CH3 used; CH2 + CH4 unused

    # Inspect the _w mock calls.
    select_calls = [
        c.args[0] for c in scope._w.call_args_list
        if isinstance(c.args[0], str) and c.args[0].startswith("SELect:CH")
    ]
    # 4 channels × 1 SELect per channel = 4 SELect writes.
    assert len(select_calls) == 4, (
        f"expected exactly 4 SELect writes (one per channel); got "
        f"{len(select_calls)}: {select_calls!r}")
    # The mapped channels (CH1, CH3) should be ON; the unmapped
    # (CH2, CH4) should be OFF.
    for ch in ("CH1", "CH3"):
        assert f"SELect:{ch} ON" in select_calls
    for ch in ("CH2", "CH4"):
        assert f"SELect:{ch} OFF" in select_calls
