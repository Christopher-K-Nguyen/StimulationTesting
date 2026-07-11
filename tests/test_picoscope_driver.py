"""PicoScope backend — model-agnostic logic + a full capture path against a
FAKE picosdk module (no hardware, no native SDK needed).

The driver's per-series SDK facts (enum values, arg lists) are PHASE-0-verified
on real hardware; these tests cover everything that does NOT depend on the real
SDK: the range-ladder snapping, the pre/post window math, the deterministic t=0
time axis, ADC→volts conversion, alias/channel mapping, factory dispatch,
import-safety, and — via a fake ``ps`` namespace — the rapid-block SOFTWARE
averaging + overflow path end to end.
"""
from __future__ import annotations

import ctypes
import sys
import types

import numpy as np
import pytest

from stimtest.hardware import open_oscilloscope
from stimtest.hardware.picoscope import (
    PICO_SERIES, PicoScopeOscilloscope, _PHYS_TO_IDX)


# ---------------------------------------------------------------------------
# import-safety + construction + factory (no SDK needed)
# ---------------------------------------------------------------------------
def test_module_imports_without_picosdk():
    """Importing the driver must NOT require picosdk (app must launch without
    the native SDK); the import already happened at module top — assert the
    class + registry are present."""
    assert "ps4000a" in PICO_SERIES
    assert PicoScopeOscilloscope is not None


def test_construct_default_is_ps4000a():
    d = PicoScopeOscilloscope()
    assert d._ad.key == "ps4000a"
    assert d.info.make == "Pico"
    assert d.channel_aliases["imon"] == "CH2"


def test_unknown_series_raises():
    with pytest.raises(ValueError):
        PicoScopeOscilloscope(series="ps9999z")


def test_factory_dispatches_to_pico_without_opening():
    """open_oscilloscope(backend='pico') returns the driver WITHOUT touching the
    SDK (construction is import-safe; open() is where picosdk is needed)."""
    scope = open_oscilloscope(backend="pico", pico_series="ps4000a")
    assert isinstance(scope, PicoScopeOscilloscope)


def test_factory_default_is_still_tektronix(monkeypatch):
    """The default backend is unchanged — every existing caller gets Tek."""
    import stimtest.hardware as hw
    created = {}

    class _FakeTek:
        def __init__(self, resource=None):
            created["tek"] = resource
    monkeypatch.setattr("stimtest.hardware.tektronix.TektronixOscilloscope",
                        _FakeTek, raising=False)
    hw.open_oscilloscope(resource="USB::0x1")
    assert created.get("tek") == "USB::0x1"


def test_open_without_picosdk_raises_clearly(monkeypatch):
    """open() must fail LOUDLY (clear RuntimeError) when picosdk is missing —
    never silently proceed on a safety-critical acquisition path."""
    d = PicoScopeOscilloscope()

    def _no_module(name):
        raise ImportError(f"No module named {name!r}")
    monkeypatch.setattr("importlib.import_module", _no_module)
    with pytest.raises(RuntimeError, match="PicoSDK"):
        d.open()


# ---------------------------------------------------------------------------
# range ladder (the biggest behavioural difference from Tek's continuous V/div)
# ---------------------------------------------------------------------------
def test_range_ladder_snaps_up_to_smallest_containing_rung():
    d = PicoScopeOscilloscope()
    # half-grid = 5 divs → peak = vpd * 5.  A 0.2 V/div → peak 1.0 V → the
    # 1000 mV rung.  A 0.25 V/div → peak 1.25 V → snaps UP to 2000 mV.
    assert d._range_for_peak_v(1.0) == d._ad.RANGE_MV.index(1000)
    assert d._range_for_peak_v(1.25) == d._ad.RANGE_MV.index(2000)
    assert d._range_for_peak_v(0.009) == d._ad.RANGE_MV.index(10)   # 9 mV → 10 mV
    # above the top rung → clamp to the coarsest
    assert d._range_for_peak_v(999.0) == len(d._ad.RANGE_MV) - 1


def test_set_channel_scale_records_range_and_vpd():
    d = PicoScopeOscilloscope()
    d.set_channel_scale("CH1", 0.2)          # peak 1.0 V → 1000 mV rung
    assert d._range["CH1"] == d._ad.RANGE_MV.index(1000)
    assert d._range_volts("CH1") == pytest.approx(1.0)
    assert d._vpd("CH1") == pytest.approx(1.0 / d._half_vert_divs)


# ---------------------------------------------------------------------------
# pre/post window + deterministic t=0 (the CWRU-tail fix, by construction)
# ---------------------------------------------------------------------------
def test_pre_post_split_from_position():
    d = PicoScopeOscilloscope()
    d.set_record_length(1000)
    d.set_horizontal_position(20.0)          # 20% pre-trigger
    pre, post = d._pre_post()
    assert (pre, post) == (200, 800)
    assert pre + post == 1000


def test_time_axis_puts_zero_at_the_trigger_sample():
    d = PicoScopeOscilloscope()
    d._dt_us = 0.032                         # 32 ns/sample
    t = d._time_axis_us(1000, pre=200)
    assert t[200] == pytest.approx(0.0)      # t=0 exactly at sample=pre
    assert t[0] == pytest.approx(-200 * 0.032)
    assert t[-1] == pytest.approx((1000 - 1 - 200) * 0.032)


def test_adc_to_volts_uses_range_and_maxadc():
    d = PicoScopeOscilloscope()
    d._max_adc = 32767
    d.set_channel_scale("CH1", 0.4)          # peak 2.0 V → 2000 mV rung
    raw = np.array([0, 16383, 32767, -32767], dtype=np.int16)
    v = d._adc_to_volts("CH1", raw)
    # full-scale ADC → +range volts (2.0 V)
    assert v[2] == pytest.approx(2.0, rel=1e-3)
    assert v[3] == pytest.approx(-2.0, rel=1e-3)
    assert v[0] == pytest.approx(0.0)


def test_channel_alias_maps_to_pico_index():
    assert _PHYS_TO_IDX == {"CH1": 0, "CH2": 1, "CH3": 2, "CH4": 3}


def test_averaging_count_bounded_by_segments():
    d = PicoScopeOscilloscope()
    d._max_segments = 32
    assert d.set_average_count(64) == 32     # clamped to the segment ceiling
    assert d.set_average_count(16) == 16
    assert d.average_count_choices() is None  # arbitrary N (spinbox path)


# ---------------------------------------------------------------------------
# full capture path against a FAKE picosdk module
# ---------------------------------------------------------------------------
def _byref_obj(arg):
    """Recover the object a ctypes.byref(...) wraps (CArgObject._obj)."""
    return getattr(arg, "_obj", arg)


class _FakePs4000a:
    """A stand-in for picosdk.ps4000a: implements only the functions the driver
    calls, filling the ctypes out-params + the SetDataBuffer'd arrays with
    synthetic per-segment ADC data so the software-average is checkable."""

    # enum tables the driver looks up by name
    PS4000A_COUPLING = {"PS4000A_AC": 0, "PS4000A_DC": 1}
    PS4000A_THRESHOLD_DIRECTION = {"PS4000A_RISING": 2, "PS4000A_FALLING": 3}
    PS4000A_RATIO_MODE = {"PS4000A_RATIO_MODE_NONE": 0}

    def __init__(self):
        self._buffers = {}                    # (ch_idx, seg) -> ctypes array
        self.calls = []

    # -- lifecycle --
    def ps4000aOpenUnit(self, h_ref, serial):
        self.calls.append("OpenUnit")
        return 0

    def ps4000aMaximumValue(self, h, out_ref):
        _byref_obj(out_ref).value = 32767
        return 0

    def ps4000aGetMaxSegments(self, h, out_ref):
        _byref_obj(out_ref).value = 32
        return 0

    def ps4000aGetUnitInfo(self, h, buf, buflen, req_ref, kind):
        s = b"4824A" if int(getattr(kind, "value", kind)) == 3 else b"JY000/001"
        buf.value = s
        _byref_obj(req_ref).value = len(s)
        return 0

    def ps4000aStop(self, h):
        return 0

    def ps4000aCloseUnit(self, h):
        return 0

    # -- config --
    def ps4000aSetChannel(self, h, ch, en, coupling, rng, offset):
        self.calls.append(("SetChannel", int(ch.value), int(en.value)))
        return 0

    def ps4000aGetTimebase2(self, h, tb, n, dt_ref, mx_ref, seg):
        _byref_obj(dt_ref).value = 80.0       # 80 ns/sample
        _byref_obj(mx_ref).value = 1 << 20
        return 0

    def ps4000aSetSimpleTrigger(self, h, en, src, thr, direction, delay, auto):
        self.calls.append(("SetSimpleTrigger", int(src.value), int(thr.value),
                           int(direction.value)))
        return 0

    # -- rapid block --
    def ps4000aMemorySegments(self, h, n, cap_ref):
        _byref_obj(cap_ref).value = 1 << 20
        return 0

    def ps4000aSetNoOfCaptures(self, h, n):
        return 0

    def ps4000aSetDataBuffer(self, h, ch, buf_ref, length, seg, ratio):
        self._buffers[(int(ch.value), int(seg.value))] = _byref_obj(buf_ref)
        return 0

    def ps4000aRunBlock(self, h, pre, post, tb, tind_ref, seg, cb, param):
        self._pre = int(pre.value)
        self._total = int(pre.value) + int(post.value)
        return 0

    def ps4000aIsReady(self, h, ready_ref):
        _byref_obj(ready_ref).value = 1       # immediately ready
        return 0

    def _fill(self, n):
        """Fill each SetDataBuffer'd array: segment s of channel idx gets the
        constant ADC value (100 + 10*s), so the N-segment mean is a known
        value = 100 + 10*(N-1)/2."""
        for (ch_idx, seg), buf in self._buffers.items():
            val = 100 + 10 * seg
            for i in range(len(buf)):
                buf[i] = val

    def ps4000aGetValuesBulk(self, h, got_ref, frm, to, down, ratio, ovf_ref):
        n = int(to.value) - int(frm.value) + 1
        self._fill(n)
        _byref_obj(got_ref).value = self._total
        return 0

    def ps4000aGetValues(self, h, start, got_ref, down, ratio, seg, ovf_ref):
        self._fill(1)
        _byref_obj(got_ref).value = self._total
        return 0


@pytest.fixture
def opened_fake(monkeypatch):
    """A PicoScopeOscilloscope opened against the fake ps4000a + a fake
    picosdk.functions module."""
    fake_ps = _FakePs4000a()
    fake_functions = types.ModuleType("picosdk.functions")
    fake_pkg = types.ModuleType("picosdk")
    monkeypatch.setitem(sys.modules, "picosdk", fake_pkg)
    monkeypatch.setitem(sys.modules, "picosdk.functions", fake_functions)

    real_import = __import__

    def _fake_import_module(name):
        if name == "picosdk.ps4000a":
            return fake_ps
        raise ImportError(name)
    monkeypatch.setattr("importlib.import_module", _fake_import_module)

    d = PicoScopeOscilloscope(series="ps4000a")
    d.open()
    return d, fake_ps


def test_open_reads_maxadc_and_segments(opened_fake):
    d, _ = opened_fake
    assert d._max_adc == 32767
    assert d._max_segments == 32
    assert "4824A" in d.info.model
    assert d.info.n_channels == 8            # 4824A → 8 channels


def test_single_capture_builds_time_axis_and_volts(opened_fake):
    d, fake = opened_fake
    d.configure_channels({"vmon": "CH1", "imon": "CH2"})
    d.set_channel_scale("CH1", 0.4)          # 2000 mV rung
    d.set_channel_scale("CH2", 0.4)
    d.set_horizontal_scale(80e-9 * 10 / 1000 * 100)  # dt→80 ns via GetTimebase2
    d._dt_us = 0.08                          # (set_horizontal_scale caches it)
    d.set_record_length(1000)
    d.set_horizontal_position(20.0)
    d.set_acquisition_mode("SAMPLE", 1)

    acq = d.single_capture(timeout_s=1.0)
    assert set(acq.channels) == {"CH1", "CH2"}
    assert acq.record_length == 1000
    # SAMPLE mode (N=1): the single frame's constant ADC 100 → volts
    v = acq.channels["CH1"]
    assert v[0] == pytest.approx(100 * 2.0 / 32767, rel=1e-3)
    # t=0 at the pre-trigger boundary (200 of 1000 at 80 ns)
    assert acq.time_us[200] == pytest.approx(0.0)
    assert acq.trigger_position_us == pytest.approx(-200 * 0.08)


def test_rapid_block_software_averages_segments(opened_fake):
    d, fake = opened_fake
    d.configure_channels({"vmon": "CH1"})
    d.set_channel_scale("CH1", 0.4)
    d._dt_us = 0.08
    d.set_record_length(500)
    d.set_horizontal_position(10.0)
    d.set_acquisition_mode("AVERAGE", 4)     # 4 segments

    acq = d.capture_single_sequence(n_acq=4, timeout_s=1.0)
    # per-segment ADC = 100,110,120,130 → mean 115 → volts 115*2.0/32767
    v = acq.channels["CH1"]
    assert np.allclose(v, 115 * 2.0 / 32767, rtol=1e-3)


def test_trigger_threshold_uses_imon_channel_range(opened_fake):
    d, fake = opened_fake
    d.configure_channels({"vmon": "CH1", "imon": "CH2"})
    d.set_channel_scale("CH2", 0.4)          # I_mon on 2000 mV rung
    d.set_trigger(source="CH2", level_v=0.5, slope="RISE", mode="NORMAL")
    # threshold in ADC counts of CH2's ±2 V range: 0.5/2.0 * 32767
    src, thr, direction = next(
        c[1:] for c in fake.calls if c[0] == "SetSimpleTrigger")
    assert src == _PHYS_TO_IDX["CH2"]
    assert thr == pytest.approx(int(0.5 / 2.0 * 32767), abs=1)
    assert direction == 2                     # RISING


def test_falling_slope_for_cathodal_imon_trigger(opened_fake):
    d, fake = opened_fake
    d.configure_channels({"imon": "CH2"})
    d.set_channel_scale("CH2", 0.4)
    d.set_trigger(source="CH2", level_v=-0.3, slope="FALL", mode="NORMAL")
    _, _, direction = next(
        c[1:] for c in fake.calls if c[0] == "SetSimpleTrigger")
    assert direction == 3                     # FALLING


# ---------------------------------------------------------------------------
# analogue-offset round-trip (audit HIGH: the offset must be subtracted back so
# a DC-biased centred channel returns TRUE input volts, not the shifted ADC)
# ---------------------------------------------------------------------------
def test_adc_to_volts_subtracts_the_applied_offset():
    """A channel positioned with a non-zero analogueOffset must have that offset
    REMOVED in reconstruction — the ADC digitises (V_in + offset), so the
    returned trace is V_in, not V_in + offset (the bug that shifted E_ret/E_act
    → corrupted E_pol → wrong water-window check)."""
    d = PicoScopeOscilloscope()
    d._max_adc = 32767
    d.set_channel_scale("CH1", 0.4)          # ±2 V rung, vpd 0.4 V/div
    # centre a +0.8 V DC-biased channel: offset = -2 div * 0.4 = -0.8 V
    d.set_channel_position("CH1", 2.0)
    off = d._applied_offset("CH1")
    assert off == pytest.approx(-0.8)         # within the ±(0.5*2 V) clamp
    # the ADC reads ~0 (V_in 0.8 shifted by -0.8); reconstruction adds it back
    adc_at_zero = np.array([0], dtype=np.int16)
    v = d._adc_to_volts("CH1", adc_at_zero)
    assert v[0] == pytest.approx(-off)        # 0*scale - (-0.8) = +0.8 V


def test_offset_round_trip_recovers_true_volts_in_capture(opened_fake):
    """End-to-end: with a channel offset set, the captured trace equals the
    fake's ADC volts MINUS the applied offset (true input volts)."""
    d, fake = opened_fake
    d.configure_channels({"vmon": "CH1"})
    d.set_channel_scale("CH1", 0.4)          # ±2 V
    d.set_channel_position("CH1", 1.0)       # offset = -0.4 V
    d._dt_us = 0.08
    d.set_record_length(500)
    d.set_acquisition_mode("SAMPLE", 1)
    acq = d.single_capture(timeout_s=1.0)
    adc_volts = 100 * 2.0 / 32767            # fake fills constant ADC 100
    assert np.allclose(acq.channels["CH1"], adc_volts - (-0.4), rtol=1e-3)


def test_channel_in_view_centres_window_on_negative_offset():
    """With data now in TRUE volts, the representable window centres on -offset
    (the physical mid), not +offset (the old sign the offset bug masked)."""
    d = PicoScopeOscilloscope()
    d._max_adc = 32767
    d.set_channel_scale("CH1", 0.4)          # ±2 V, vpd 0.4, half-grid 5 div
    d.set_channel_position("CH1", 2.0)       # offset -0.8 → window centre +0.8
    # a small excursion around +0.8 V fits; the SAME excursion around -0.8 does
    # not (the window moved to +0.8, not -0.8).
    assert d.channel_in_view("CH1", 0.7, 0.9) is True
    assert d.channel_in_view("CH1", -0.9, -0.7) is False


def test_clamp_offset_is_conservative_not_full_range():
    """`_clamp_offset` bounds to a fraction of the rung (the real device offset
    window is smaller than ±full-scale near coarse rungs) — audit LOW."""
    d = PicoScopeOscilloscope()
    d.set_channel_scale("CH1", 0.4)          # ±2 V rung
    assert d._clamp_offset("CH1", 5.0) == pytest.approx(1.0)   # 0.5 * 2 V
    assert d._clamp_offset("CH1", -5.0) == pytest.approx(-1.0)
    assert d._clamp_offset("CH1", 0.3) == pytest.approx(0.3)   # in-bounds


def test_ps4000_legacy_series_flags_oversample_block_args():
    """The older `ps4000` (non-'a') series takes the legacy `oversample` arg in
    GetTimebase2 + RunBlock; the adapter flags it so the shared path inserts it
    (ps4000a does not)."""
    assert PICO_SERIES["ps4000"].block_takes_oversample is True
    assert PICO_SERIES["ps4000a"].block_takes_oversample is False
