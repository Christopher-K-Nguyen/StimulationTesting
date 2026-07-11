"""POLARIS opens PicoScope CSV exports (raw multi-channel scope captures).

PicoScope CSVs have a two-row header (``Time,Channel A,…`` + a units row
``(us),(V),(mV),…``), a blank line, then data.  Units VARY per file (a
channel may be V on one export, mV on another), so the loader normalises
every channel to volts and time to microseconds.  Role-free: channels keep
their PicoScope names; POLARIS shows them as raw traces.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest


_CSV = """Time,Channel A,Channel B,Channel C,Channel D
(us),(V),(V),(V),(mV)

-2.00,0.00,0.30,0.000,1.0
-1.00,0.00,0.30,0.000,1.0
0.00,-0.50,0.30,-1.000,250.0
1.00,-0.60,0.31,-1.100,250.0
2.00,-0.62,0.30,-1.120,250.0
"""


def _write_csv(tmp_path, text=_CSV, name="pico.csv"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_loader_parses_channels_and_units(tmp_path):
    from stimtest.persistence import load_picoscope_csv
    rec = load_picoscope_csv(_write_csv(tmp_path))
    assert list(rec.channels) == ["Channel A", "Channel B",
                                  "Channel C", "Channel D"]
    assert rec.time_us.size == 5
    assert rec.time_us[0] == pytest.approx(-2.0)
    # Channel D was exported in mV → normalised to V (250 mV → 0.25 V).
    assert rec.source_units["Channel D"] == "mV"
    assert rec.channels["Channel D"][2] == pytest.approx(0.25)
    assert rec.source_units["Channel A"] == "V"
    assert rec.channels["Channel A"][2] == pytest.approx(-0.5)


def test_loader_reads_tsv(tmp_path):
    """TSV (tab-delimited) export — same layout, auto-detected delimiter."""
    from stimtest.persistence import load_picoscope
    txt = _CSV.replace(",", "\t")
    p = tmp_path / "cap.tsv"
    p.write_text(txt, encoding="utf-8")
    rec = load_picoscope(p)
    assert list(rec.channels) == ["Channel A", "Channel B",
                                  "Channel C", "Channel D"]
    assert rec.channels["Channel D"][2] == pytest.approx(0.25)   # 250 mV → V


def test_loader_reads_xlsx(tmp_path):
    """Excel (.xlsx) export via openpyxl."""
    openpyxl = pytest.importorskip("openpyxl")
    from stimtest.persistence import load_picoscope
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Time", "Channel A", "Channel B"])
    ws.append(["(us)", "(V)", "(mV)"])
    ws.append([None, None, None])
    for t, a, b in [(-1.0, 0.0, 100.0), (0.0, -0.5, 250.0), (1.0, -0.6, 250.0)]:
        ws.append([t, a, b])
    p = tmp_path / "cap.xlsx"
    wb.save(p)
    rec = load_picoscope(p)
    assert list(rec.channels) == ["Channel A", "Channel B"]
    assert rec.time_us.size == 3
    assert rec.channels["Channel B"][1] == pytest.approx(0.25)   # mV → V


def test_polaris_opens_tsv(tmp_path):
    pytest.importorskip("pyqtgraph")
    import sys
    from PyQt6 import QtWidgets
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    from stimtest.gui.viewer import ViewerPanel, KIND_PICO, ROLE_KIND
    p = tmp_path / "cap.tsv"
    p.write_text(_CSV.replace(",", "\t"), encoding="utf-8")
    panel = ViewerPanel()
    panel.load_session_file(p)
    item = panel.tree.invisibleRootItem().child(0)
    assert item.data(0, ROLE_KIND) == KIND_PICO
    panel.tree.setCurrentItem(item)
    app.processEvents()
    assert panel.figure.axes and len(panel.figure.axes[0].lines) == 4


def test_loader_handles_ms_time_units(tmp_path):
    from stimtest.persistence import load_picoscope_csv
    txt = _CSV.replace("(us)", "(ms)")
    rec = load_picoscope_csv(_write_csv(tmp_path, txt, "ms.csv"))
    # ms → µs: −2 ms = −2000 µs
    assert rec.time_us[0] == pytest.approx(-2000.0)


def test_loader_rejects_non_picoscope(tmp_path):
    from stimtest.persistence import load_picoscope_csv
    bad = tmp_path / "bad.csv"
    bad.write_text("foo,bar\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_picoscope_csv(bad)


def test_plot_picoscope_renders(tmp_path):
    pytest.importorskip("matplotlib")
    import matplotlib
    matplotlib.use("Agg")
    from stimtest.persistence import load_picoscope_csv
    from stimtest import plotting
    rec = load_picoscope_csv(_write_csv(tmp_path))
    fig = plotting.plot_picoscope(rec)
    ax = fig.axes[0]
    assert len(ax.lines) == 4            # one per channel
    assert ax.get_ylabel() == "Voltage [V]"


def test_polaris_opens_csv(tmp_path):
    pytest.importorskip("pyqtgraph")
    import sys
    from PyQt6 import QtWidgets
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    from stimtest.gui.viewer import ViewerPanel, KIND_PICO, ROLE_KIND
    csv = _write_csv(tmp_path)
    panel = ViewerPanel()
    panel.load_session_file(csv)
    item = panel.tree.invisibleRootItem().child(0)
    assert item is not None
    assert item.data(0, ROLE_KIND) == KIND_PICO
    panel.tree.setCurrentItem(item)
    app.processEvents()
    assert panel.figure.axes, "PicoScope CSV did not render"
    assert len(panel.figure.axes[0].lines) == 4


def _synthetic_recording():
    """A PicoScopeRecording: V_mon transient, a square current channel, and a
    flat reference — enough for pattern inference + metrics."""
    from stimtest.persistence import PicoScopeRecording
    t = np.linspace(-50.0, 460.0, 4000)
    vmon = np.zeros_like(t)
    cur = np.zeros_like(t)
    eret = np.full_like(t, 0.30)
    # cathodic phase 0-200, anodic 220-420 (20 µs interphase)
    m1 = (t >= 0) & (t < 200)
    m2 = (t >= 220) & (t < 420)
    vmon[m1] = np.linspace(-1.0, -1.6, m1.sum())
    vmon[m2] = np.linspace(1.0, 1.6, m2.sum())
    cur[m1] = -0.25                        # current monitor (V) cathodic
    cur[m2] = 0.25                         # anodic
    return PicoScopeRecording(
        path=Path("synthetic_capture.csv"), time_us=t,
        channels={"Channel A": vmon, "Channel B": cur, "Channel C": eret},
        source_units={"Channel A": "V", "Channel B": "V", "Channel C": "V"})


def test_picoscope_to_session_infers_pattern_and_metrics():
    from stimtest.persistence import picoscope_to_session
    rec = _synthetic_recording()
    sess = picoscope_to_session(
        rec, {"Channel A": "v_mon", "Channel B": "i_mon", "Channel C": "e_ret"},
        current_scale_mv_per_ua=2.5)
    cap = sess.runs[0].captures[0]
    # pattern inferred from the current channel: two phases (cathodic/anodic)
    assert cap.pattern.num_phases == 2
    amps = [ph.amplitude_ua for ph in cap.pattern.phases]
    assert amps[0] < 0 and amps[1] > 0                 # cathodic then anodic
    # 0.25 V / 2.5 mV/µA = 100 µA
    assert abs(abs(amps[0]) - 100.0) < 15.0
    # voltage metrics computed (driving voltage finite + positive)
    assert np.isfinite(cap.metrics.driving_voltage_v)
    assert cap.metrics.driving_voltage_v > 0.5


def test_picoscope_metrics_path_in_polaris(tmp_path):
    pytest.importorskip("pyqtgraph")
    import sys
    from PyQt6 import QtWidgets
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    from stimtest.gui.viewer import ViewerPanel
    csv = _write_csv(tmp_path)            # V_mon=A, current=D (mV)
    panel = ViewerPanel()
    panel.load_session_file(csv)
    item = panel.tree.invisibleRootItem().child(0)
    panel.tree.setCurrentItem(item)
    app.processEvents()
    # raw first: only the "assign roles" hint, no real metric rows
    assert panel.metric_table.rowCount() <= 1
    bar = panel.pico_role_bar
    combos = bar._combos
    combos["Channel A"].setCurrentIndex(combos["Channel A"].findData("v_mon"))
    combos["Channel D"].setCurrentIndex(combos["Channel D"].findData("i_mon"))
    combos["Channel C"].setCurrentIndex(combos["Channel C"].findData("e_ret"))
    bar.metrics_chk.setChecked(True)
    app.processEvents()
    # metrics now populate the table with real waveform rows
    assert panel.metric_table.rowCount() > 3


def test_polaris_folder_indexes_csv(tmp_path):
    pytest.importorskip("pyqtgraph")
    import sys
    from PyQt6 import QtWidgets
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    from stimtest.gui.viewer import ViewerPanel, KIND_PICO, ROLE_KIND
    _write_csv(tmp_path, name="a.csv")
    _write_csv(tmp_path, name="b.csv")
    panel = ViewerPanel()
    panel.load_folder(tmp_path)
    root = panel.tree.invisibleRootItem().child(0)
    kinds = [root.child(i).data(0, ROLE_KIND) for i in range(root.childCount())]
    assert kinds.count(KIND_PICO) == 2
