"""Regression tests for `PlexonStimulator._is_open` cascade suppression.

The vendor PlexStim DLL HEAP_CORRUPTs (Windows 0xC0000374) when
``ps_close_all_stim`` is called multiple times in close succession
on an already-closed device.  Before the fix, a single GUI
Stop → Start cycle could fire FOUR ``ps_close_all_stim`` calls
within one second:

  1. GUI ``_on_finished`` close()                           ← #1
  2. GUI ``_start_runner_body`` open()'s embedded close     ← #2
  3. Runner ``reinit()`` at top of run() — close()          ← #3
  4. Runner ``reinit()`` — open()'s embedded close          ← #4

The 4th call crashed the DLL.

The fix has two pieces — these tests pin both:

* ``_is_open`` flag in ``PlexonStimulator`` (this file's focus).
  ``open()`` skips its embedded ``ps_close_all_stim`` when the
  device is already-closed; ``close()`` skips when already-closed.
* Removed runner-side ``reinit()`` at the top of ``run()`` — tested
  separately via the runner test suite + integration coverage.

The contract these tests enforce, expressed in `ps_close_all_stim`
call counts:

  * Fresh ``open()`` (after construction):    0 close calls
  * ``close()`` then ``open()``:               1 close call (the close)
  * Two ``close()`` in a row:                  1 close call (second is no-op)
  * ``reinit()`` (close + open):              1 close call (open's embed skipped)
  * Three back-to-back full Stop/Start cycles: 3 close calls

Before the fix, every one of those scenarios had EXTRA calls (the
embedded close in ``open()`` always fired, even on an already-closed
device).  These tests fail loudly if anyone reintroduces that
behaviour.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


def _build_stim(ps_init_returns: int = 0,
                serial: str = "PLX_TEST_0001",
                n_channels: int = 16,
                fw: str = "1.0",
                desc: str = "TestStim"):
    """Construct a ``PlexonStimulator`` with a fully-mocked ``_lib``.

    Bypasses ``__init__`` so we don't need the vendored DLL on the
    test machine.  Sets up just enough state for ``open()`` /
    ``close()`` / ``reinit()`` to run without raising.

    Returns the stim instance — caller can inspect ``stim._lib``'s
    mock to count calls.
    """
    from stimtest.hardware.plexon import PlexonStimulator, StimulatorInfo

    stim = PlexonStimulator.__new__(PlexonStimulator)
    stim._lib = MagicMock()
    stim._lib.ps_init_all_stim = MagicMock(return_value=ps_init_returns)
    stim._lib.ps_close_all_stim = MagicMock()
    stim._lib.ps_get_n_stim = MagicMock(return_value=(1, 0))
    stim._lib.ps_get_n_channels = MagicMock(return_value=(n_channels, 0))
    stim._lib.ps_get_serial_number = MagicMock(return_value=(serial, 0))
    stim._lib.ps_get_fw_version = MagicMock(return_value=(fw, 0))
    stim._lib.ps_get_description = MagicMock(return_value=(desc, 0))
    stim._PS_OK = 0
    stim._stim_n = 1
    stim.info = StimulatorInfo()
    stim._pat_path = None
    stim._pat_content_signature = None
    stim._loaded_channels = set()
    stim._is_open = False  # the canonical initial state per __init__
    stim._validated_channels = set()
    stim._auto_discharge_pref = None
    stim.cmd_logger = None
    stim._dll_lock = threading.RLock()
    return stim


# ---------------------------------------------------------------------------
# Initial-state contract
# ---------------------------------------------------------------------------
def test_is_open_starts_false():
    """A fresh stim instance has ``_is_open = False`` so the first
    ``open()`` skips the embedded close (nothing to close yet).
    """
    stim = _build_stim()
    assert stim._is_open is False


# ---------------------------------------------------------------------------
# Single-call contracts
# ---------------------------------------------------------------------------
def test_first_open_skips_close():
    """The cascade fix: the first ``open()`` on a fresh device must
    NOT call ``ps_close_all_stim`` (the device isn't open, nothing
    to close)."""
    stim = _build_stim()
    stim.open()
    assert stim._lib.ps_close_all_stim.call_count == 0, (
        "fresh open() should skip ps_close_all_stim; "
        f"got {stim._lib.ps_close_all_stim.call_count} call(s)")
    assert stim._is_open is True


def test_close_when_open_fires_once():
    """A ``close()`` on a currently-open device fires exactly one
    ``ps_close_all_stim`` call and flips the flag."""
    stim = _build_stim()
    stim.open()
    stim._lib.ps_close_all_stim.reset_mock()

    stim.close()
    assert stim._lib.ps_close_all_stim.call_count == 1
    assert stim._is_open is False


def test_close_when_already_closed_is_noop():
    """Calling ``close()`` on a device that's already closed must
    skip ``ps_close_all_stim`` — the very condition the cascade
    fix addresses."""
    stim = _build_stim()
    # Fresh stim → already closed.
    stim.close()
    assert stim._lib.ps_close_all_stim.call_count == 0
    assert stim._is_open is False

    # Open, close, then close-again should keep call count at 1.
    stim.open()
    stim._lib.ps_close_all_stim.reset_mock()
    stim.close()
    stim.close()
    stim.close()
    assert stim._lib.ps_close_all_stim.call_count == 1, (
        "consecutive close() calls must be idempotent; "
        f"got {stim._lib.ps_close_all_stim.call_count} call(s)")


def test_open_after_close_skips_embedded_close():
    """A ``close()`` followed by an ``open()`` should fire exactly
    one ``ps_close_all_stim`` (the close itself) — NOT two (close +
    open's embedded close).  This is the cascade-suppression
    contract.

    Pre-fix, this scenario fired 2 close calls.  The fix uses
    ``_is_open == False`` in ``open()`` to skip the embedded close.
    """
    stim = _build_stim()
    stim.open()
    stim._lib.ps_close_all_stim.reset_mock()

    stim.close()
    stim.open()
    assert stim._lib.ps_close_all_stim.call_count == 1, (
        "close() + open() should fire 1 ps_close_all_stim (the close); "
        f"got {stim._lib.ps_close_all_stim.call_count}")
    assert stim._is_open is True


# ---------------------------------------------------------------------------
# reinit() — the runner's old start-of-run call
# ---------------------------------------------------------------------------
def test_reinit_fires_exactly_one_close():
    """``reinit()`` (= ``close()`` + ``open()``) must fire exactly
    ONE ``ps_close_all_stim`` call total — not two.

    Pre-fix, ``reinit()`` fired 2 close calls (one from close, one
    from open's embedded close).  Two of these in close succession
    (GUI close + runner reinit) gave the 4-call cascade that crashed
    the DLL.  The fix collapses ``reinit()`` to 1 call.
    """
    stim = _build_stim()
    stim.open()
    stim._lib.ps_close_all_stim.reset_mock()

    stim.reinit()
    assert stim._lib.ps_close_all_stim.call_count == 1, (
        "reinit() should fire 1 ps_close_all_stim total; "
        f"got {stim._lib.ps_close_all_stim.call_count}")
    assert stim._is_open is True


# ---------------------------------------------------------------------------
# Full Stop/Start cycle — the failure mode from the field
# ---------------------------------------------------------------------------
def test_three_stop_start_cycles_fire_three_closes_not_twelve():
    """Three full GUI Stop → Start cycles should fire 3 close calls
    total (one per Stop), NOT 12 (the pre-fix worst case of 4 calls
    per cycle).

    This pins the actual user-visible failure mode: the cascade that
    triggered HEAP_CORRUPTION (Windows 0xC0000374).
    """
    stim = _build_stim()
    stim.open()  # initial connect (e.g. ConnectionPanel "Initialize")
    stim._lib.ps_close_all_stim.reset_mock()

    for _ in range(3):
        # Stop press → close().
        stim.close()
        # Start press → open() (re-init).
        stim.open()

    assert stim._lib.ps_close_all_stim.call_count == 3, (
        "3 Stop/Start cycles should fire 3 ps_close_all_stim calls "
        f"(one per Stop); got {stim._lib.ps_close_all_stim.call_count}. "
        "If you're seeing 4+ per cycle, the _is_open guard has "
        "regressed — see plexon.py:open()/close() and CLAUDE.md "
        "gotcha #29c.")
    # Init count: 1 (initial) + 3 (each Start) = 4
    assert stim._lib.ps_init_all_stim.call_count == 4


def test_init_failure_leaves_flag_false():
    """If ``ps_init_all_stim`` returns non-OK, ``open()`` raises and
    leaves ``_is_open = False``.  This guarantees a subsequent
    ``close()`` will skip the close (nothing was successfully
    opened, so nothing to tear down) — preventing a follow-up
    crash on an init-failure path."""
    stim = _build_stim(ps_init_returns=1)  # device error
    with pytest.raises(RuntimeError, match="PS_InitAllStim failed"):
        stim.open()
    assert stim._is_open is False, (
        "failed open() must leave _is_open=False so subsequent "
        "close() correctly skips ps_close_all_stim")
    # Subsequent close should be a no-op.
    stim._lib.ps_close_all_stim.reset_mock()
    stim.close()
    assert stim._lib.ps_close_all_stim.call_count == 0


# ---------------------------------------------------------------------------
# Trigger mode — open() must assert PS_TRIG_SOFT for programmatic start/stop
# ---------------------------------------------------------------------------
# PULSAR drives start/stop PROGRAMMATICALLY via PS_StartStimAllChannels /
# PS_StopStimAllChannels, both of which return error 4 ("wrong trigger
# mode") unless the device is in PS_TRIG_SOFT (0).  PS_InitAllStim resets
# the trigger mode and the power-on default is not guaranteed soft across
# firmware revisions, so ``open()`` (re)asserts it on every connect.
# Field symptom of a device left in PS_TRIG_PULSE/LEVEL: the stim "starts"
# but waits for a hardware trigger that never arrives, so it never pulses.
def test_open_sets_trigger_mode_soft():
    """``open()`` explicitly sets the device to PS_TRIG_SOFT (0)."""
    from stimtest.hardware.pyplexstim.pyplexstimlib import PS_TRIG_SOFT
    stim = _build_stim()
    stim._lib.ps_set_trigger_mode = MagicMock(return_value=0)
    stim._lib.ps_get_trigger_mode = MagicMock(return_value=(PS_TRIG_SOFT, 0))

    stim.open()

    stim._lib.ps_set_trigger_mode.assert_called_once_with(
        stim._stim_n, PS_TRIG_SOFT)
    assert PS_TRIG_SOFT == 0  # the constant must stay 0 (SOFT)


def test_open_forces_soft_even_when_device_came_up_in_pulse_mode():
    """If the device powered up in PS_TRIG_PULSE (1), ``open()`` still
    forces it back to PS_TRIG_SOFT (0)."""
    from stimtest.hardware.pyplexstim.pyplexstimlib import PS_TRIG_SOFT
    stim = _build_stim()
    stim._lib.ps_set_trigger_mode = MagicMock(return_value=0)
    # First get() = prior mode PULSE(1); second get() = post-set read-back SOFT.
    stim._lib.ps_get_trigger_mode = MagicMock(
        side_effect=[(1, 0), (PS_TRIG_SOFT, 0)])

    stim.open()

    stim._lib.ps_set_trigger_mode.assert_called_once_with(
        stim._stim_n, PS_TRIG_SOFT)


def test_open_survives_trigger_mode_set_failure():
    """A trigger-mode set that returns non-OK must NOT abort ``open()``
    — the first ``start_all`` surfaces the hard error 4 instead.  open()
    still completes and flips ``_is_open`` True."""
    stim = _build_stim()
    stim._lib.ps_set_trigger_mode = MagicMock(return_value=-1)  # invalid arg
    stim._lib.ps_get_trigger_mode = MagicMock(return_value=(1, 0))
    stim._lib.ps_get_extended_error_info = MagicMock(
        return_value=("bad mode", 0))

    stim.open()  # must not raise
    assert stim._is_open is True


# ---------------------------------------------------------------------------
# load_all_channels — the MONOPOLAR commit (PS_LoadAllChannels)
# ---------------------------------------------------------------------------
def test_load_all_channels_maps_to_ps_load_all_channels():
    """``PlexonStimulator.load_all_channels()`` issues one
    ``PS_LoadAllChannels`` (the commit the MATLAB used for monopolar
    configs with no return channels)."""
    stim = _build_stim()
    stim._lib.ps_load_all_channels = MagicMock(return_value=0)

    stim.load_all_channels()

    stim._lib.ps_load_all_channels.assert_called_once_with(stim._stim_n)
