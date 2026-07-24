"""Metric-table N_pulse as a variable + Q_inj auto-scale to µC/cm².

Operator:
  * "N_pulse should be a variable, and fix the font color."
  * "If the charge injection capacity is 0 mC/cm2 in the subtitle due to low
    precision, then use uC/cm2."
"""
from __future__ import annotations

import sys

import numpy as np
import pytest


# ---- Q_inj auto-scale (pure) ---------------------------------------------
def test_qinj_use_micro_threshold():
    from stimtest.plotting import qinj_use_micro
    assert qinj_use_micro(0.0004) is True     # rounds to 0.000 mC → µC
    assert qinj_use_micro(0.0152) is False     # 0.015 mC displays fine
    assert qinj_use_micro(4.0) is False
    assert qinj_use_micro(0.0) is False        # exactly zero stays mC
    assert qinj_use_micro(float("nan")) is False


def test_subtitle_uses_microcoulomb_for_tiny_qinj():
    from stimtest.plotting import _capture_subtitle_mathtext
    from stimtest.session import Capture, ChannelRun, Configuration
    from stimtest.waveforms import PulsePattern
    cap = Capture(index=0,
                  pattern=PulsePattern.biphasic(amplitude_ua=-1.0, rate_hz=100.0))
    cap.metrics.charge_injection_mc_per_cm2 = 0.0004    # 0.4 µC/cm²
    run = ChannelRun(configuration=Configuration(id=0, active=1))
    txt = _capture_subtitle_mathtext(cap, run, "CH01")
    assert "µC/cm" in txt and "0.400" in txt
    assert "mC/cm" not in txt.split("inj")[1].split("·")[0]


# ---- N_pulse as a variable (metric table) --------------------------------
@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _capture_with_pulses():
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    from stimtest.metrics import compute_metrics
    cap = Capture(index=3,
                  pattern=PulsePattern.biphasic(amplitude_ua=-50.0, rate_hz=100.0))
    cap.time_us = np.linspace(-100.0, 600.0, 1400)
    cap.v_mon_v = np.zeros_like(cap.time_us)
    cap.i_mon_ua = np.zeros_like(cap.time_us)
    compute_metrics(cap, surface_area_um2=5000.0)
    cap.metrics.n_pulses = 128
    cap.metrics.cumulative_n_pulses = 256
    return cap


def test_metric_table_n_pulse_is_a_variable(_app):
    from stimtest.gui.widgets import MetricTable
    mt = MetricTable()
    mt.show_capture(_capture_with_pulses())
    labels = [mt.item(r, 0).text() for r in range(mt.rowCount())
              if mt.item(r, 0)]
    # N_pulse rendered as an italic variable + subscript (HTML), NOT plain text.
    assert "<i>N</i><sub>pulse</sub>" in labels
    assert "Cumulative <i>N</i><sub>pulse</sub>" in labels
    assert "N_pulse" not in labels                 # no plain-text version


def test_html_delegate_paints_theme_text_colour(_app):
    # The rich-text delegate must pull the text colour from the item's palette
    # (not QTextDocument's default black) so HTML labels match the plain rows
    # on a dark theme (operator: "fix the font color").  Assert the paint path
    # references option.palette for the Text role.
    import inspect
    from stimtest.gui.widgets import _HtmlItemDelegate
    src = inspect.getsource(_HtmlItemDelegate.paint)
    assert "option.palette.color" in src
    assert "ColorRole.Text" in src


# ---- HTML metric cells word-wrap (Cumulative N_pulse) ---------------------
def test_html_delegate_sizehint_wraps_to_column_width():
    """_HtmlItemDelegate.sizeHint must WRAP to the column width so a long HTML
    label (e.g. "Cumulative N_pulse") reports its 2-line height instead of a
    single line — else the 2nd line is clipped (operator: "The cumulative
    number of pulses is not wrapping … make sure that the tables allow for
    text wrapping")."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PyQt6")
    from PyQt6 import QtWidgets, QtCore
    from stimtest.gui.widgets import _HtmlItemDelegate
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    table = QtWidgets.QTableWidget(1, 1)
    delg = _HtmlItemDelegate()
    item = QtWidgets.QTableWidgetItem("Cumulative <i>N</i><sub>pulse</sub>")
    table.setItem(0, 0, item)
    idx = table.model().index(0, 0)
    opt = QtWidgets.QStyleOptionViewItem()
    opt.font = table.font()
    opt.rect = QtCore.QRect(0, 0, 400, 20)          # WIDE → one line
    h_wide = delg.sizeHint(opt, idx).height()
    opt.rect = QtCore.QRect(0, 0, 64, 20)           # NARROW → must wrap
    h_narrow = delg.sizeHint(opt, idx).height()
    assert h_narrow > h_wide, (h_narrow, h_wide)


# ---- units live in the METRIC (label) column, not the value column --------
# operator: "have the units in the metric column and not value column".
def test_split_charge_and_energy_separate_the_unit():
    from stimtest.gui.widgets import (_split_cumulative_charge, _split_energy)
    assert _split_cumulative_charge(504460.0) == ("504.46", "µC")
    assert _split_cumulative_charge(159.76) == ("159.8", "nC")
    assert _split_cumulative_charge(5.04e6) == ("5.040", "mC")
    assert _split_energy(0.34262) == ("342.62", "nJ")   # matches the bench screenshot
    assert _split_energy(1.5) == ("1.500", "µJ")
    assert _split_energy(2e-6) == ("2.0", "pJ")
    assert _split_energy(float("nan")) == ("", "")


def test_voltage_list_auto_picks_v_mv_uv():
    from stimtest.gui.widgets import _fmt_voltage_list_auto
    assert _fmt_voltage_list_auto([1.6, 1.7]) == ("1.600, 1.700", "V")
    assert _fmt_voltage_list_auto([0.0003, 0.00025]) == ("0.300, 0.250", "mV")
    assert _fmt_voltage_list_auto([3e-5, 2e-5]) == ("30.0, 20.0", "µV")
    assert _fmt_voltage_list_auto([0.0, 0.0]) == ("0.000, 0.000", "V")
    # a small value that would render 0.000 in V now shows a real number.
    val, unit = _fmt_voltage_list_auto([0.00012])
    assert unit in ("mV", "µV") and val != "0.000"


def _cap_with_return():
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    cap = Capture(index=1,
                  pattern=PulsePattern.biphasic(amplitude_ua=-800.0, rate_hz=200.0))
    m = cap.metrics
    m.response_class = "normal"
    m.n_pulses = 330
    m.cumulative_n_pulses = 8495
    m.cumulative_charge_nc = 504460.0        # → 504.46 µC
    m.driving_energy_uj = 0.34262            # → 342.62 nJ
    m.charge_per_phase_nc = 159.76
    m.charge_injection_mc_per_cm2 = 3.195
    m.driving_voltage_v = -0.7988
    m.driving_impedance_kohm = 2.283
    # normal (large) active access voltage + a TINY return access voltage that
    # would render 0.000 in V but still has a real R_a.
    m.access_voltage_per_phase_v = [1.601, 1.700]
    m.access_resistance_per_phase_kohm = [2.00, 2.12]
    m.return_access_voltage_per_phase_v = [0.00030, 0.00025]
    m.return_access_resistance_per_phase_kohm = [0.038, 0.031]
    return cap


def test_metric_table_units_in_label_and_small_access_voltage_scaled(_app):
    from stimtest.gui.widgets import MetricTable
    mt = MetricTable()
    mt.show_capture(_cap_with_return())
    pairs = [(mt.item(r, 0).text(), mt.item(r, 1).text())
             for r in range(mt.rowCount())
             if mt.item(r, 0) and mt.item(r, 1)]
    labels = [k for k, _ in pairs]
    by_label = dict(pairs)

    # (1) Cumulative Q + Driving energy carry the UNIT in the label; the value
    #     cell is unit-free.
    assert "Cumulative Q [µC]" in labels
    assert by_label["Cumulative Q [µC]"] == "504.46"
    _de = [k for k in labels if k.startswith("Driving energy")]
    assert _de == ["Driving energy [nJ]"]
    assert by_label["Driving energy [nJ]"] == "342.62"

    # (2) the tiny RETURN access voltage auto-scales to mV/µV (not 0.000);
    #     the normal active access voltage stays V.
    _ret_va = [(k, v) for k, v in pairs
               if "return" in k and k.startswith("<i>V</i><sub>a</sub>")]
    assert _ret_va, labels
    (rk, rv) = _ret_va[0]
    assert "[mV]" in rk or "[µV]" in rk
    assert "0.000" not in rv                       # the whole point
    _act_va = [(k, v) for k, v in pairs
               if k.startswith("<i>V</i><sub>a</sub>") and "return" not in k]
    assert _act_va and "[V]" in _act_va[0][0]


def test_metric_table_has_charge_imbalance_and_total_driving_voltage(_app):
    from stimtest.gui.widgets import MetricTable
    mt = MetricTable()
    mt.show_capture(_cap_with_return())
    labels = [mt.item(r, 0).text() for r in range(mt.rowCount()) if mt.item(r, 0)]
    # (#2) charge imbalance Q_net [nC] row present.
    assert any(k.startswith("<i>Q</i><sub>net</sub>") and "[nC]" in k
               for k in labels), labels
    # (#29) with a reference electrode, the TOTAL driving voltage (V_mon, all
    # electrodes) is shown distinctly from the per-electrode active/return.
    assert any("total [V]" in k and k.startswith("<i>V</i><sub>d</sub>")
               for k in labels), labels


def test_time_constant_labels_say_time_constant(_app):
    from stimtest.gui.pattern_panel import PatternControlPanel
    from stimtest.gui import rich
    pp = PatternControlPanel()
    for lab in (pp._sym_tau_label, pp._cap_tau_label):
        assert "Time constant" in lab.text()
        assert "τ" in lab.text() and rich.US in lab.text()
    # +/- 1 µs increment (operator).
    assert pp.sym_tau_us.singleStep() == 1.0
    assert pp.tau_us.singleStep() == 1.0


def test_tau_input_changes_the_exp_decay_shape(_app):
    """The time-constant input must actually change the exp-decay curve, not
    just its label (operator: "be sure the time constant input does indeed
    affect the exponential shape")."""
    import numpy as np
    from stimtest.gui.pattern_panel import PatternControlPanel
    from stimtest.waveforms import SHAPE_EXP_DECAY, shape_breakpoints
    pp = PatternControlPanel()
    idx = pp.shape_combo.findData(SHAPE_EXP_DECAY)
    assert idx >= 0, "exp-decay shape not in the combo"
    pp.shape_combo.setCurrentIndex(idx)

    def _exp_tau():
        pat = pp.pattern()
        assert pat is not None
        for ph in pat.phases:
            if ph.shape == SHAPE_EXP_DECAY:
                return ph.tau_us
        return None

    # (1) the GUI τ value flows into the built pattern's exp-decay phase.
    pp.sym_tau_us.setValue(50.0)
    assert _exp_tau() == 50.0
    pp.sym_tau_us.setValue(200.0)
    assert _exp_tau() == 200.0

    # (2) the rendered curve genuinely differs for the two time constants
    #     (I(t) = A·exp(-t/τ)) — proving the input changes the SHAPE.
    a50 = [a for _t, a in shape_breakpoints(
        amplitude_ua=100.0, width_us=200.0, shape=SHAPE_EXP_DECAY, tau_us=50.0)]
    a200 = [a for _t, a in shape_breakpoints(
        amplitude_ua=100.0, width_us=200.0, shape=SHAPE_EXP_DECAY, tau_us=200.0)]
    assert not np.allclose(a50, a200)
