"""Value-keyed timing cache for PS_SetPeriod (rate) + PS_SetRepetitions.

Operator request: *"The number of repetition and pulse rate do not need
to be set for every iteration if all involved channels will be set at
the beginning of the experiment before the first iteration."*

PlexStim programs the pulse RATE (PS_SetPeriod) and REPETITIONS
(PS_SetRepetitions) as device state SEPARATE from the .pat waveform file,
and the device PERSISTS them across .pat reloads and stop/start cycles.
So re-programming them on every sweep step is redundant.  The driver now
keeps a value-keyed per-channel cache:

* ``load_channel`` skips ``ps_set_period`` / ``ps_set_repetitions`` when
  the channel already holds the requested value (but ALWAYS issues the
  ``ps_load_channel`` commit so a changed .pat is applied).
* ``set_repetitions`` skips when the channel already holds ``n`` — so the
  sweep runners' ``set_repetitions(active, 0)`` after ``load_channel``
  (which already programmed reps=0 from the default-0 pattern) collapses
  to a no-op.
* A genuine rate / reps CHANGE (different value) re-programs + re-caches.
* ``open()`` / ``close()`` (PS_InitAllStim resets the device) wipe the
  cache so the next load re-programs from scratch.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest


def _build_stim(n_channels: int = 16, ps_init_returns: int = 0):
    """Construct a ``PlexonStimulator`` with a fully-mocked ``_lib`` —
    enough state for ``open()`` / ``close()`` / ``load_channel`` /
    ``set_repetitions`` to run without the vendored DLL."""
    from stimtest.hardware.plexon import PlexonStimulator, StimulatorInfo

    stim = PlexonStimulator.__new__(PlexonStimulator)
    stim._lib = MagicMock()
    stim._lib.ps_init_all_stim = MagicMock(return_value=ps_init_returns)
    stim._lib.ps_close_all_stim = MagicMock()
    stim._lib.ps_get_n_stim = MagicMock(return_value=(1, 0))
    stim._lib.ps_get_n_channels = MagicMock(return_value=(n_channels, 0))
    stim._lib.ps_get_serial_number = MagicMock(return_value=("PLX_TEST", 0))
    stim._lib.ps_get_fw_version = MagicMock(return_value=("1.0", 0))
    stim._lib.ps_get_description = MagicMock(return_value=("TestStim", 0))
    stim._lib.ps_set_period = MagicMock(return_value=0)
    # ``set_repetitions`` now read-back-confirms via ``ps_get_repetitions``
    # (operator: "Do the same checks with the PlexStim … getting what is
    # set to confirm").  Make the fake getter ECHO the last-set value so
    # the confirm passes and the cache-behaviour assertions below still hold.
    _reps_state = {"n": 0}

    def _set_reps(_stim_n, _ch, n, *a, **k):
        _reps_state["n"] = int(n)
        return 0

    stim._lib.ps_set_repetitions = MagicMock(side_effect=_set_reps)
    stim._lib.ps_get_repetitions = MagicMock(
        side_effect=lambda *a, **k: (_reps_state["n"], 0))
    stim._lib.ps_load_channel = MagicMock(return_value=0)
    stim._PS_OK = 0
    stim._stim_n = 1
    stim.info = StimulatorInfo()
    stim.info.n_channels = n_channels
    stim._pat_path = None
    stim._pat_content_signature = None
    stim._loaded_channels = set()
    stim._is_open = True
    stim._validated_channels = set()
    stim._channel_period_ms = {}
    stim._channel_reps = {}
    stim._channel_content_sig = {}
    stim._auto_discharge_pref = None
    stim.cmd_logger = None
    stim._dll_lock = threading.RLock()
    return stim


def _biphasic(rate_hz: float = 100.0, amp: float = 50.0):
    from stimtest.waveforms import PulsePattern
    return PulsePattern.biphasic(amplitude_ua=amp, rate_hz=rate_hz)


# ---------------------------------------------------------------------------
# set_repetitions value-keyed cache
# ---------------------------------------------------------------------------
def test_set_repetitions_skips_when_unchanged():
    stim = _build_stim()
    stim.set_repetitions(5, 0)
    stim.set_repetitions(5, 0)   # cache hit → skip
    stim.set_repetitions(5, 0)   # cache hit → skip
    assert stim._lib.ps_set_repetitions.call_count == 1


def test_set_repetitions_reprograms_on_change():
    stim = _build_stim()
    stim.set_repetitions(5, 0)
    stim.set_repetitions(5, 3)   # different value → re-program
    assert stim._lib.ps_set_repetitions.call_count == 2
    stim.set_repetitions(5, 3)   # back to cache hit
    assert stim._lib.ps_set_repetitions.call_count == 2


def test_set_repetitions_cache_is_per_channel():
    stim = _build_stim()
    stim.set_repetitions(5, 0)
    stim.set_repetitions(6, 0)   # different channel → own entry
    assert stim._lib.ps_set_repetitions.call_count == 2


# ---------------------------------------------------------------------------
# load_channel per-channel CONTENT cache (Opt #1): an identical reload of an
# already-loaded channel skips the whole DLL sequence; a content/rate/reps
# CHANGE re-loads.  This is what collapses a VT sweep's per-step reload of
# the 15 static zero-amplitude unused channels to once per configuration.
# ---------------------------------------------------------------------------
def test_load_channel_skips_redundant_reload_of_same_pattern():
    stim = _build_stim()
    stim._validated_channels.add(13)   # skip the read-back round trips
    stim._load_arbitrary = MagicMock()  # skip the .pat write + ARB load

    pat = _biphasic(rate_hz=100.0)      # default repetitions=0
    stim.load_channel(13, pat)
    stim.load_channel(13, pat)          # identical → device already holds it
    stim.load_channel(13, pat)

    # First load programs everything once; the next two are cache hits that
    # return BEFORE any DLL traffic (the device retains the pattern across
    # stop/start; commit_loaded_channels re-arms it).
    assert stim._lib.ps_set_period.call_count == 1
    assert stim._lib.ps_set_repetitions.call_count == 1
    assert stim._lib.ps_load_channel.call_count == 1
    assert stim._load_arbitrary.call_count == 1


def test_load_channel_reloads_on_content_change():
    """A genuine pattern change (e.g. VT ramping the active channel's
    amplitude) re-loads — the content signature differs so the cache misses."""
    stim = _build_stim()
    stim._validated_channels.add(13)
    stim._load_arbitrary = MagicMock()

    stim.load_channel(13, _biphasic(amp=50.0))
    stim.load_channel(13, _biphasic(amp=80.0))   # amplitude change → reload
    stim.load_channel(13, _biphasic(amp=80.0))   # back to a cache hit

    assert stim._lib.ps_load_channel.call_count == 2
    assert stim._load_arbitrary.call_count == 2


def test_load_channel_reloads_on_sub_microamp_change():
    """CRITICAL (audit): a fine VT/PS ramp step below 1 µA (the 0.1 µA testing
    resolution + the back-off/oscillate increments) MUST re-load.  The content
    signature is quantised to the DEVICE nA grid, so 50.0 → 50.4 µA is a genuine
    change — NOT an int-µA collision that would skip ``ps_load_channel`` and
    leave the electrode delivering the stale lower amplitude while the runner
    believes the new one was applied (→ over-ramp past the water window)."""
    stim = _build_stim()
    stim._validated_channels.add(13)
    stim._load_arbitrary = MagicMock()

    stim.load_channel(13, _biphasic(amp=50.0))
    stim.load_channel(13, _biphasic(amp=50.4))   # +0.4 µA → device-distinct → reload
    stim.load_channel(13, _biphasic(amp=50.4))   # identical → cache hit
    stim.load_channel(13, _biphasic(amp=50.1))   # +0.1 µA (100 nA grid) → reload

    assert stim._lib.ps_load_channel.call_count == 3
    assert stim._load_arbitrary.call_count == 3


def test_content_signature_tracks_device_grid_not_integer_ua():
    """The signature is identical iff the rendered .pat amplitude is identical.
    Two amplitudes that round to the same INTEGER µA but differ on the 0.1 µA
    device grid produce DIFFERENT signatures (no false cache hit); a SUB-grid
    difference (< 100 nA on a rect phase, rendered identically) collapses to the
    same signature (preserves the efficiency skip, never a false MISS)."""
    from stimtest.hardware.plexon import PlexonStimulator
    sig = PlexonStimulator._content_signature
    assert sig(_biphasic(amp=50.0)) != sig(_biphasic(amp=50.4))   # int-µA collision fixed
    assert sig(_biphasic(amp=50.4)) == sig(_biphasic(amp=50.4))   # identical → equal
    assert sig(_biphasic(amp=50.00)) == sig(_biphasic(amp=50.02))  # sub-grid → same .pat


def test_load_channel_content_cache_is_per_channel():
    """Loading the same zero pattern on many channels (the unused-channel
    case) loads each once; re-running the set is all cache hits."""
    stim = _build_stim()
    for ch in (2, 3, 4, 5):
        stim._validated_channels.add(ch)
    stim._load_arbitrary = MagicMock()

    zero = _biphasic(amp=0.0)
    for _round in range(3):              # 3 amplitude steps' worth of reloads
        for ch in (2, 3, 4, 5):
            stim.load_channel(ch, zero)

    # 4 channels loaded once each; the 2nd and 3rd rounds are all skipped.
    assert stim._lib.ps_load_channel.call_count == 4
    assert stim._load_arbitrary.call_count == 4


def test_load_channel_reprograms_on_rate_change():
    stim = _build_stim()
    stim._validated_channels.add(13)
    stim._load_arbitrary = MagicMock()

    stim.load_channel(13, _biphasic(rate_hz=100.0))
    stim.load_channel(13, _biphasic(rate_hz=200.0))   # rate change → re-program
    assert stim._lib.ps_set_period.call_count == 2


def test_load_channel_then_runner_set_reps_zero_is_one_program():
    """The canonical sweep pattern: ``load_channel`` (pattern reps=0)
    followed by the runner's ``set_repetitions(active, 0)`` must program
    reps exactly ONCE — the second call is a cache no-op."""
    stim = _build_stim()
    stim._validated_channels.add(13)
    stim._load_arbitrary = MagicMock()

    stim.load_channel(13, _biphasic())   # programs reps=0 from the pattern
    stim.set_repetitions(13, 0)          # runner override → cache hit → skip
    assert stim._lib.ps_set_repetitions.call_count == 1


# ---------------------------------------------------------------------------
# Cache reset on reinit (open / close = PS_InitAllStim)
# ---------------------------------------------------------------------------
def test_close_resets_timing_cache():
    stim = _build_stim()
    stim._channel_period_ms[13] = 10.0
    stim._channel_reps[13] = 0
    stim.close()
    assert stim._channel_period_ms == {}
    assert stim._channel_reps == {}


def test_open_resets_timing_cache():
    stim = _build_stim()
    # Trigger-mode setters open() touches.
    from stimtest.hardware.pyplexstim.pyplexstimlib import PS_TRIG_SOFT
    stim._lib.ps_set_trigger_mode = MagicMock(return_value=0)
    stim._lib.ps_get_trigger_mode = MagicMock(return_value=(PS_TRIG_SOFT, 0))
    stim._channel_period_ms[13] = 10.0
    stim._channel_reps[13] = 0
    stim._is_open = False  # so open() proceeds cleanly

    stim.open()
    assert stim._channel_period_ms == {}
    assert stim._channel_reps == {}


def test_reprogram_after_reset():
    """After a cache reset, the next load_channel re-programs the rate."""
    stim = _build_stim()
    stim._validated_channels.add(13)
    stim._load_arbitrary = MagicMock()

    stim.load_channel(13, _biphasic(rate_hz=100.0))
    assert stim._lib.ps_set_period.call_count == 1
    # Simulate a reinit wiping the cache.
    stim._channel_period_ms = {}
    stim._channel_reps = {}
    stim.load_channel(13, _biphasic(rate_hz=100.0))
    assert stim._lib.ps_set_period.call_count == 2


def test_load_channel_return_reports_cache_hit_vs_load():
    """``load_channel`` returns True when it actually (re)uploads a pattern and
    False on a content-cache hit — so ``load_zero_unused_channels`` can log /
    count only genuine loads and NOT re-report the unchanged zero channels
    every step (operator: "the zero channels do not change … not applied every
    step")."""
    stim = _build_stim()
    stim._validated_channels.add(13)
    stim._load_arbitrary = MagicMock()
    pat = _biphasic(rate_hz=100.0)
    assert stim.load_channel(13, pat) is True     # first load = real upload
    assert stim.load_channel(13, pat) is False    # identical = cache hit
    assert stim.load_channel(13, _biphasic(amp=80.0)) is True   # changed = load


def test_load_zero_unused_skips_when_device_already_holds_it():
    """load_zero_unused_channels must NOT re-load the unused channels when only
    the active amplitude changes — the device already holds their (unchanged)
    zero pattern (operator: "the zero/unused channels do not need to be loaded
    again").  A reinit (content cache wiped) forces a reload."""
    from stimtest.experiments.base import ExperimentRunner
    from stimtest.hardware.plexon import PlexonStimulator
    from stimtest.electrode import Configuration
    from stimtest.waveforms import PulsePattern

    class _Stim:
        _content_signature = staticmethod(PlexonStimulator._content_signature)

        def __init__(self):
            self._channel_content_sig = {}
            self.info = type("I", (), {"n_channels": 4})()
            self.load_calls = []

        def load_channel(self, ch, pat):
            self._channel_content_sig[ch] = self._content_signature(pat)
            self.load_calls.append(ch)
            return True

        def set_repetitions(self, ch, n):
            pass

    class _R(ExperimentRunner):
        def run(self):
            pass
    r = _R.__new__(_R)
    r.stim = _Stim()
    r.session = None
    r._emit = lambda ev: None
    cfg = Configuration.monopolar(1)              # active 1, returns () → unused 2,3,4
    pat = PulsePattern.biphasic(amplitude_ua=50.0)

    r.load_zero_unused_channels(pat, cfg)
    assert sorted(r.stim.load_calls) == [2, 3, 4]  # loaded once

    # Active amplitude changed (pat×2) but the zero pattern is identical →
    # device already holds it → the whole call is skipped.
    r.stim.load_calls.clear()
    r.load_zero_unused_channels(pat.scaled(2.0), cfg)
    assert r.stim.load_calls == [], "unused channels must NOT reload on an amp change"

    # After a reinit (device content cache wiped) they DO reload.
    r.stim._channel_content_sig.clear()
    r.stim.load_calls.clear()
    r.load_zero_unused_channels(pat, cfg)
    assert sorted(r.stim.load_calls) == [2, 3, 4], "reinit must force a reload"
