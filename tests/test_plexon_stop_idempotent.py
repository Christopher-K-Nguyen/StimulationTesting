"""Idempotent stop_all — collapse the sweep's redundant
PS_StopStimAllChannels calls to a single DLL round-trip.

Operator: "Why do you stop_stim_all_channels twice?" The sweep issues TWO
stops between steps — the per-capture ``finally`` stop + the next step's
explicit pre-load stop (gotcha #15, belt-and-suspenders).  On the normal
path the second is redundant.  A running-state flag (``_is_running``) makes
``stop_all`` skip the DLL call when the device is already stopped, while the
explicit stop STILL fires if the ``finally`` was skipped.

Invariant under test: ``_is_running == False`` ⇒ device genuinely stopped,
so skipping a stop can never leave it live.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


def _build_stim(n_channels: int = 16):
    from stimtest.hardware.plexon import PlexonStimulator, StimulatorInfo
    stim = PlexonStimulator.__new__(PlexonStimulator)
    stim._lib = MagicMock()
    stim._lib.ps_start_stim_all_channels = MagicMock(return_value=0)
    stim._lib.ps_stop_stim_all_channels = MagicMock(return_value=0)
    stim._lib.ps_start_stim_channel = MagicMock(return_value=0)
    stim._lib.ps_abort_all = MagicMock(return_value=0)
    stim._PS_OK = 0
    stim._stim_n = 1
    stim.info = StimulatorInfo()
    stim.info.n_channels = n_channels
    stim._is_running = False
    stim.cmd_logger = None
    stim._dll_lock = threading.RLock()
    return stim


def test_stop_all_skips_when_not_running():
    stim = _build_stim()
    stim.stop_all()  # fresh device → already stopped → skip
    assert stim._lib.ps_stop_stim_all_channels.call_count == 0


def test_start_then_stop_fires_once():
    stim = _build_stim()
    stim.start_all()
    assert stim._is_running is True
    stim.stop_all()
    assert stim._lib.ps_stop_stim_all_channels.call_count == 1
    assert stim._is_running is False


def test_double_stop_after_one_start_fires_once():
    """The exact sweep pattern: per-capture finally stop, then the next
    step's explicit pre-load stop, then the config-cleanup stop — only the
    FIRST issues a DLL call."""
    stim = _build_stim()
    stim.start_all()
    stim.stop_all()   # the finally stop — fires
    stim.stop_all()   # the explicit pre-load stop — no-op
    stim.stop_all()   # config cleanup — no-op
    assert stim._lib.ps_stop_stim_all_channels.call_count == 1


def test_explicit_stop_still_fires_if_finally_skipped():
    """If the per-capture finally was bypassed (still running), the next
    explicit pre-load stop MUST fire — the belt-and-suspenders safety the
    idempotency must NOT defeat."""
    stim = _build_stim()
    stim.start_all()
    # (no finally stop here — simulate it being skipped by an early return)
    stim.stop_all()   # explicit pre-load stop — fires because still running
    assert stim._lib.ps_stop_stim_all_channels.call_count == 1


def test_restart_after_stop_re_enables_the_stop():
    stim = _build_stim()
    stim.start_all(); stim.stop_all()   # 1 stop
    stim.start_all(); stim.stop_all()   # 2nd start re-arms → 2nd stop fires
    assert stim._lib.ps_stop_stim_all_channels.call_count == 2


def test_abort_all_marks_stopped():
    stim = _build_stim()
    stim.start_all()
    stim.abort_all()
    assert stim._is_running is False
    stim.stop_all()   # follow-up stop is a no-op
    assert stim._lib.ps_stop_stim_all_channels.call_count == 0


def test_start_channel_marks_running():
    stim = _build_stim()
    stim.start_channel(1)
    assert stim._is_running is True
    stim.stop_all()
    assert stim._lib.ps_stop_stim_all_channels.call_count == 1
