"""PlexStim setters read back what they set to confirm it took.

Operator: "Do the same checks with the PlexStim if the function/method is
available" + "Always check with commands to the oscilloscope about changing
settings by getting what is set to confirm".

The driver ALREADY read-back-confirms period / repetitions / pattern-type
(in ``load_channel``) and the monitor channel (in ``set_monitor_channel``),
raising on mismatch.  This closes the two remaining gaps — the STANDALONE
``set_repetitions`` and ``set_auto_discharge`` — using the DLL's
``PS_GetRepetitions`` / ``PS_GetAutoDischarge`` getters.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


def _build_stim(n_channels: int = 16):
    from stimtest.hardware.plexon import PlexonStimulator, StimulatorInfo
    stim = PlexonStimulator.__new__(PlexonStimulator)
    stim._lib = MagicMock()
    stim._lib.ps_set_repetitions = MagicMock(return_value=0)
    stim._lib.ps_get_repetitions = MagicMock(return_value=(0, 0))
    stim._lib.ps_set_auto_discharge = MagicMock(return_value=0)
    stim._lib.ps_get_auto_discharge = MagicMock(return_value=(True, 0))
    stim._PS_OK = 0
    stim._stim_n = 1
    stim.info = StimulatorInfo()
    stim.info.n_channels = n_channels
    stim._channel_reps = {}
    stim._auto_discharge_pref = None
    stim.cmd_logger = None
    stim._dll_lock = threading.RLock()
    return stim


# --------------------------------------------------------- set_repetitions
def test_set_repetitions_confirms_match():
    stim = _build_stim()
    stim._lib.ps_get_repetitions = MagicMock(return_value=(5, 0))
    stim.set_repetitions(1, 5)                       # device agrees → OK
    stim._lib.ps_set_repetitions.assert_called_once()
    stim._lib.ps_get_repetitions.assert_called_once()
    assert stim._channel_reps[1] == 5


def test_set_repetitions_raises_on_mismatch():
    stim = _build_stim()
    stim._lib.ps_get_repetitions = MagicMock(return_value=(3, 0))  # device says 3
    with pytest.raises(RuntimeError, match="repetitions mismatch"):
        stim.set_repetitions(1, 5)
    # A failed confirm must NOT cache a wrong "already set" value.
    assert stim._channel_reps.get(1) != 5


def test_set_repetitions_cache_hit_skips_readback():
    stim = _build_stim()
    stim._channel_reps[1] = 5                        # already at 5
    stim._lib.ps_get_repetitions = MagicMock(return_value=(5, 0))
    stim.set_repetitions(1, 5)                       # collapses to a no-op
    stim._lib.ps_set_repetitions.assert_not_called()
    stim._lib.ps_get_repetitions.assert_not_called()


# ------------------------------------------------------- set_auto_discharge
def test_set_auto_discharge_confirms_match():
    logs = []
    stim = _build_stim()
    stim.cmd_logger = logs.append
    stim._lib.ps_get_auto_discharge = MagicMock(return_value=(True, 0))
    stim.set_auto_discharge(True)
    assert not any("auto-discharge read-back" in m for m in logs)


def test_set_auto_discharge_logs_on_mismatch():
    logs = []
    stim = _build_stim()
    stim.cmd_logger = logs.append
    stim._lib.ps_get_auto_discharge = MagicMock(return_value=(False, 0))  # device off
    stim.set_auto_discharge(True)                    # requested on
    assert any("auto-discharge read-back" in m and "⚠" in m for m in logs)


def test_set_auto_discharge_tolerates_getter_failure():
    """A non-OK read-back result is ignored (no crash, no false warning)."""
    logs = []
    stim = _build_stim()
    stim.cmd_logger = logs.append
    stim._lib.ps_get_auto_discharge = MagicMock(return_value=(False, 7))  # error code
    stim.set_auto_discharge(True)
    assert not any("auto-discharge read-back" in m for m in logs)
