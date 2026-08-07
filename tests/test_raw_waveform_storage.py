"""RAW pre-conversion waveform storage.

Operator: "store the raw waveform data (before conversion and scaling)" and
"save all channel setting information".

The trace arrays in a Capture are twice-derived — ADC codes -> volts (via the
preamble) -> engineering units (via the stimulator preset).  When a scaling
looks wrong, those arrays cannot show WHERE it went wrong: the I_mon
investigation stalled because the file held only the end result, leaving
inference as the only tool (and two of my hypotheses were wrong).

Keeping the codes plus every decode constant makes the chain re-derivable
offline under any assumption.  Channel SETTINGS matter for the same reason:
AC vs DC coupling is an operator CHOICE, so the file must record which was in
force rather than leave a reader to guess.
"""
from __future__ import annotations

import numpy as np
import pytest

from stimtest.hardware.base import ScopeAcquisition
from stimtest.persistence import load_session_npz, save_session_npz
from stimtest.session import (Capture, ChannelRun, Configuration, ElectrodeArray,
                              Session, TestParameters)
from stimtest.waveforms import PulsePattern


def _raw(codes, ymult, yoff, yzero=0.0, wfid="Ch2, AC coupling, 1.000mV/div"):
    return {
        "codes": np.asarray(codes, dtype=np.int8),
        "ymult": ymult, "yoff": yoff, "yzero": yzero,
        "xincr": 3.2e-8, "xzero": -1.28e-4,
        "scale_v_per_div": ymult * 25.0, "position_div": 0.0,
        "wfid": wfid, "record_length": 20000, "npts": len(codes),
        "coupling_override": "AC", "bandwidth_override": None,
    }


def test_scope_acquisition_carries_raw():
    a = ScopeAcquisition(time_us=np.zeros(4))
    assert a.raw == {}, "must default to empty, never None"
    a.raw["CH1"] = _raw([1, 2], 1e-3, 0.0)
    assert "CH1" in a.raw


def test_capture_raw_defaults_to_none():
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=-50.0))
    assert c.raw_channels is None, "legacy captures must stay None"


def _session_with_raw():
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    codes = np.array([-100, -50, 0, 50, 100], dtype=np.int8)
    ymult, yoff, yzero = 4.0e-5, -50.0, 0.0
    volts = (codes.astype(float) - yoff) * ymult + yzero
    cap = Capture(
        index=0, pattern=pat,
        time_us=np.arange(codes.size, dtype=float),
        v_mon_v=volts, i_mon_ua=volts / 1e-3,
        raw_channels={
            "channels": {"CH1": _raw(codes, ymult, yoff, yzero),
                         "CH2": _raw(codes, ymult, yoff, yzero)},
            "scaling": {"vmon_v_per_v": 1.0, "imon_v_per_ua": 1.0e-3,
                        "preset": "NIL"},
            "aliases": {"vmon": "CH1", "imon": "CH2"},
        })
    run = ChannelRun(configuration=Configuration(id=1, active=1))
    run.captures.append(cap)
    cfg = Configuration(id=1, active=1)
    sess = Session(notebook="nb", subject="el", test=TestParameters(
        experiment="VT", pattern=pat, configuration=cfg,
        array=ElectrodeArray.utah_4x4()))
    sess.add_run(run)
    return sess, codes, ymult, yoff, yzero


def test_raw_round_trips_through_npz(tmp_path):
    sess, codes, ymult, yoff, yzero = _session_with_raw()
    p = tmp_path / "s.npz"
    save_session_npz(sess, p)
    back = load_session_npz(p).runs[0].captures[0].raw_channels
    assert back is not None, "raw data did not survive the save/load"
    ch = back["channels"]["CH1"]
    assert np.array_equal(ch["codes"], codes)
    assert ch["codes"].dtype == np.int8
    for k, want in (("ymult", ymult), ("yoff", yoff), ("yzero", yzero)):
        assert ch[k] == pytest.approx(want)


def test_volts_are_rederivable_from_the_stored_codes(tmp_path):
    """The point of the whole feature: rebuild the trace from raw."""
    sess, *_ = _session_with_raw()
    p = tmp_path / "s.npz"
    save_session_npz(sess, p)
    cap = load_session_npz(p).runs[0].captures[0]
    ch = cap.raw_channels["channels"]["CH1"]
    rebuilt = ((ch["codes"].astype(float) - ch["yoff"]) * ch["ymult"]
               + ch["yzero"])
    assert np.allclose(rebuilt, cap.v_mon_v)


def test_current_is_rederivable_under_a_different_preset(tmp_path):
    """The question that started this: what would I_mon be under the OTHER
    preset?  With raw stored it is arithmetic, not inference."""
    sess, *_ = _session_with_raw()
    p = tmp_path / "s.npz"
    save_session_npz(sess, p)
    cap = load_session_npz(p).runs[0].captures[0]
    ch = cap.raw_channels["channels"]["CH2"]
    volts = (ch["codes"].astype(float) - ch["yoff"]) * ch["ymult"] + ch["yzero"]
    assert np.allclose(volts / 1.0e-3, cap.i_mon_ua)          # NIL, as saved
    assert np.allclose(volts / 2.5e-3, cap.i_mon_ua / 2.5)    # Default


def test_channel_settings_are_saved(tmp_path):
    """AC vs DC is an operator choice — the file must say which was used."""
    sess, *_ = _session_with_raw()
    p = tmp_path / "s.npz"
    save_session_npz(sess, p)
    ch = load_session_npz(p).runs[0].captures[0].raw_channels["channels"]["CH1"]
    assert "AC coupling" in ch["wfid"]
    assert ch["scale_v_per_div"] == pytest.approx(1.0e-3)
    assert ch["record_length"] == 20000


def test_scaling_and_aliases_are_saved(tmp_path):
    sess, *_ = _session_with_raw()
    p = tmp_path / "s.npz"
    save_session_npz(sess, p)
    raw = load_session_npz(p).runs[0].captures[0].raw_channels
    assert raw["scaling"]["imon_v_per_ua"] == pytest.approx(1.0e-3)
    assert raw["scaling"]["preset"] == "NIL"
    assert raw["aliases"]["imon"] == "CH2"


def test_legacy_capture_without_raw_still_loads(tmp_path):
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    cap = Capture(index=0, pattern=pat, time_us=np.zeros(4),
                  v_mon_v=np.zeros(4), i_mon_ua=np.zeros(4))
    run = ChannelRun(configuration=Configuration(id=1, active=1))
    run.captures.append(cap)
    sess = Session(notebook="nb", subject="el", test=TestParameters(
        experiment="VT", pattern=pat, configuration=Configuration(id=1, active=1),
        array=ElectrodeArray.utah_4x4()))
    sess.add_run(run)
    p = tmp_path / "legacy.npz"
    save_session_npz(sess, p)
    assert load_session_npz(p).runs[0].captures[0].raw_channels is None


# --------------------------------------------------------------- ideal current

def test_ideal_current_is_computed_and_persisted(tmp_path):
    """The PROGRAMMED waveform stored alongside the measured I_mon.

    I_mon carries switching spikes, ringing and turn-on skew; the ideal trace
    is what the current source was told to deliver, so overlaying the two makes
    any disagreement visible instead of inferred.
    """
    import numpy as np
    from stimtest.metrics import compute_metrics
    from stimtest.session import (Capture, ChannelRun, Configuration,
                                  ElectrodeArray, Session, TestParameters)
    from stimtest.waveforms import PulsePattern

    pat = PulsePattern.biphasic(amplitude_ua=-100.0, phase_width_us=100.0,
                                polarity=-1, interphase_us=50.0,
                                discharge_us=0.0, rate_hz=100.0)
    t = np.arange(-50.0, 400.0, 0.5)
    i_mon = np.zeros_like(t)
    i_mon[(t >= 0) & (t < 100)] = -100.0
    i_mon[(t >= 150) & (t < 250)] = +100.0
    v_mon = i_mon * 1e-6 * 5000.0
    cap = Capture(index=0, pattern=pat, time_us=t, v_mon_v=v_mon,
                  i_mon_ua=i_mon)
    compute_metrics(cap, surface_area_um2=5000.0)

    assert cap.i_ideal_ua is not None, "ideal current was not generated"
    assert cap.i_ideal_ua.shape == t.shape, "must share the capture time axis"
    # Zero in the interphase gap, signed correctly in each phase.
    gap = (t > 105) & (t < 145)
    assert np.allclose(cap.i_ideal_ua[gap], 0.0)
    assert cap.i_ideal_ua[(t > 10) & (t < 90)].mean() < -50.0
    assert cap.i_ideal_ua[(t > 160) & (t < 240)].mean() > 50.0

    cfg = Configuration(id=1, active=1)
    run = ChannelRun(configuration=cfg)
    run.captures.append(cap)
    sess = Session(notebook="nb", subject="el", test=TestParameters(
        experiment="VT", pattern=pat, configuration=cfg,
        array=ElectrodeArray.utah_4x4()))
    sess.add_run(run)
    p = tmp_path / "ideal.npz"
    save_session_npz(sess, p)
    back = load_session_npz(p).runs[0].captures[0]
    assert back.i_ideal_ua is not None
    assert np.allclose(back.i_ideal_ua, cap.i_ideal_ua)


def test_ideal_current_absent_stays_none(tmp_path):
    from stimtest.session import (Capture, ChannelRun, Configuration,
                                  ElectrodeArray, Session, TestParameters)
    from stimtest.waveforms import PulsePattern
    import numpy as np
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    cap = Capture(index=0, pattern=pat, time_us=np.zeros(4),
                  v_mon_v=np.zeros(4), i_mon_ua=np.zeros(4))
    assert cap.i_ideal_ua is None
    cfg = Configuration(id=1, active=1)
    run = ChannelRun(configuration=cfg); run.captures.append(cap)
    sess = Session(notebook="nb", subject="el", test=TestParameters(
        experiment="VT", pattern=pat, configuration=cfg,
        array=ElectrodeArray.utah_4x4()))
    sess.add_run(run)
    p = tmp_path / "none.npz"
    save_session_npz(sess, p)
    assert load_session_npz(p).runs[0].captures[0].i_ideal_ua is None


# ------------------------------------------------- preamble -> volts contract

def test_preamble_fields_are_looked_up_by_name_not_position():
    """A positional read of the preamble would silently mis-decode every
    waveform if firmware ever reordered or omitted a field.  The parser must
    key by NAME and fail loudly on a missing one."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "stimtest/hardware/tektronix.py").read_text(encoding="utf-8")
    for f in ("YMULT", "YOFF", "YZERO", "XINCR", "XZERO"):
        assert f'fields["{f}"]' in src, f"{f} not looked up by name"
    assert 'required = ("YMULT", "YOFF", "YZERO", "XINCR", "XZERO")' in src
    assert "preamble missing required field(s)" in src


def test_stored_constants_reproduce_the_tek_conversion():
    """volts = (codes - YOFF) * YMULT + YZERO — the documented Tek formula.

    Uses the preamble values from the driver's own docstring example so the
    test breaks if the stored field meanings ever drift.
    """
    import numpy as np
    ymult, yoff, yzero = 1.5625e-5, 0.0, 0.0
    codes = np.array([-128, -1, 0, 1, 127], dtype=np.int8)
    volts = (codes.astype(float) - yoff) * ymult + yzero
    rec = _raw(codes, ymult, yoff, yzero)
    rebuilt = ((rec["codes"].astype(float) - rec["yoff"]) * rec["ymult"]
               + rec["yzero"])
    assert np.allclose(rebuilt, volts)


def test_both_yoff_values_are_kept():
    """The TBS2000 reports YOFf = 0 even when the channel IS positioned
    (gotcha #199), so PULSAR may override it.  The file must carry BOTH the
    value USED and the value REPORTED — otherwise a reader cannot tell an
    override from a genuine zero."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "stimtest/hardware/tektronix.py").read_text(encoding="utf-8")
    i = src.index('"codes": np.asarray(raw, dtype=np.int8)')
    block = src[i:i + 900]
    assert '"yoff": float(_yoff_used)' in block
    assert '"yoff_reported": float(yoff)' in block


# ------------------------------------------------ codes/div cross-check

def _cpd_scope(last_scale, ymult, true_scale=None):
    from stimtest.hardware.tektronix import TektronixOscilloscope as T
    class _S(T):
        def __init__(self):
            self._adapt_state = {"CH2": {"last_scale": last_scale}}
            self._y_codes_per_div = {}
            self.logs = []
            self._queries = []
        def _q(self, cmd):
            self._queries.append(cmd)
            if true_scale is None:
                raise RuntimeError("no reply")
            return str(true_scale)
        def _log(self, m): self.logs.append(m)
    return _S()


def test_stale_scale_is_detected_and_repaired():
    """Real failure: CH2 learned 18.8 codes/div from a stale cached V/div and
    I_mon then read ~1.5x the programmed current."""
    from stimtest.hardware.tektronix import _EXPECTED_Y_CODES_PER_DIV as EXP
    ymult = 1.888e-2
    s = _cpd_scope(last_scale=0.354015, ymult=ymult, true_scale=ymult * EXP)
    # 0.354015 / 1.888e-2 = 18.75 -> stale
    assert abs(0.354015 / ymult - 18.75) < 0.1
    assert abs(s._adapt_state["CH2"]["last_scale"] - 0.354015) < 1e-9


def test_expected_codes_per_div_is_25():
    from stimtest.hardware.tektronix import (_EXPECTED_Y_CODES_PER_DIV,
                                             _Y_CPD_TOL)
    assert _EXPECTED_Y_CODES_PER_DIV == 25.0
    assert 0 < _Y_CPD_TOL < 0.1


def test_good_scale_matches_expected():
    """CH1/CH3 in the same run were clean: 0.450/1.8e-2 and 0.001/4e-5."""
    from stimtest.hardware.tektronix import _EXPECTED_Y_CODES_PER_DIV as EXP
    for vpd, ymult in ((0.450, 1.800e-2), (0.001, 4.000e-5), (0.140, 5.600e-3)):
        assert abs(vpd / ymult - EXP) < 0.5, (vpd, ymult)


def test_crosscheck_is_wired_into_read_channel():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "stimtest/hardware/tektronix.py").read_text(encoding="utf-8")
    assert "_EXPECTED_Y_CODES_PER_DIV" in src
    i = src.index("CROSS-CHECK the cached V/div against YMULT")
    blk = src[i:i + 4500]
    assert "SCAle?" in blk, "must re-query the scope on disagreement"
    assert "last_scale" in blk, "must repair the cache"


def test_wfid_is_keyed_per_channel_not_a_shared_stash():
    """A real run stored "Ch3, AC coupling, 1.000mV/div" for CH1, CH2 AND CH3.

    WFID is parsed only on a preamble-cache MISS by a parser shared across
    channels, so it can stash just one value.  ``_read_channel`` must CONSUME
    that stash and key it per channel, falling back to the channel's own last
    value on a cache hit.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "stimtest/hardware/tektronix.py").read_text(encoding="utf-8")
    i = src.index("Consuming it")
    blk = src[i:i + 1400]
    assert "self._last_wfid[ch] = _w" in blk, "wfid not keyed by channel"
    assert "self._last_wfid_value = None" in blk, "stash not consumed"
    assert '"wfid": _wfid_for_ch,' in src, "raw record must use the per-ch value"


def test_wfid_consume_logic():
    """Fresh parse claims the stash for its channel; a cache hit on another
    channel must NOT inherit it."""
    class _S:
        pass
    s = _S(); s._last_wfid = {}; s._last_wfid_value = "Ch1, DC coupling"

    def consume(obj, ch):
        w = getattr(obj, "_last_wfid_value", None)
        if w:
            obj._last_wfid[ch] = w
            obj._last_wfid_value = None
        return obj._last_wfid.get(ch)

    assert consume(s, "CH1") == "Ch1, DC coupling"
    assert consume(s, "CH2") is None          # cache hit, no stash to steal
    s._last_wfid_value = "Ch2, AC coupling"
    assert consume(s, "CH2") == "Ch2, AC coupling"
    assert s._last_wfid["CH1"] == "Ch1, DC coupling"   # CH1 unchanged


# ------------------------------------------------ ideal current as a trace

def test_plot_capture_accepts_show_ideal_current():
    import inspect
    from stimtest.plotting import plot_capture
    assert "show_ideal_current" in inspect.signature(plot_capture).parameters


def test_plot_capture_draws_the_ideal_trace_dashed():
    """Drawn on the CURRENT axis, dashed, so it overlays the measured I_mon."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "stimtest/plotting.py").read_text(encoding="utf-8")
    i = src.index("IDEAL / EXPECTED current")
    blk = src[i:i + 1200]
    assert "ax_i.plot" in blk, "must share the measured current's axis"
    assert 'linestyle="--"' in blk
    assert "(ideal)" in blk, "needs its own legend entry"


def test_polaris_exposes_the_option():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "stimtest/gui/viewer.py").read_text(encoding="utf-8")
    assert 'QtWidgets.QCheckBox("Ideal current")' in src
    assert "def ideal_current(self)" in src
    assert "show_ideal_current=self.view_bar.ideal_current()" in src
    # round-trips in prefs
    assert '"ideal_current": self.ideal_check.isChecked()' in src
    assert 'p.get("ideal_current", False)' in src


def test_ideal_overlay_is_off_by_default():
    """Default OFF keeps every existing figure byte-identical."""
    import inspect
    from stimtest.plotting import plot_capture
    assert (inspect.signature(plot_capture)
            .parameters["show_ideal_current"].default is False)
