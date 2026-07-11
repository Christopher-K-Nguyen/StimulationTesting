"""POLARIS plot-view controls (operator review list).

Covers the post-open interactive controls added to the viewer:

* **Clickable legend toggle** — each legend entry hides/shows its trace
  ("Let the legend have checkbox per entry to toggle for viewing").
* **Axis-range overrides** — user-settable X / Y limits, Auto by default.
* **Current vs current-density** right-axis toggle, defaulting to current.
* **Per-trace line-style + color** overrides that survive re-render
  ("Allow the choice of choosing the plot line style and color after
  opening").
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest


_CSV = """Time,Channel A,Channel B,Channel C,Channel D
(us),(V),(V),(V),(mV)

-50.00,0.00,0.30,0.00,0.0
0.00,-0.50,0.30,-0.70,-250.0
100.00,-0.62,0.30,-0.85,-250.0
220.00,0.50,0.30,0.70,250.0
320.00,0.62,0.30,0.85,250.0
420.00,0.00,0.30,0.00,0.0
"""


def _panel(tmp_path, name="pico.csv", text=_CSV):
    pytest.importorskip("pyqtgraph")
    pytest.importorskip("matplotlib")
    from PyQt6 import QtWidgets
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    from stimtest.gui.viewer import ViewerPanel
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    panel = ViewerPanel()
    panel.load_session_file(p)
    item = panel.tree.invisibleRootItem().child(0)
    panel.tree.setCurrentItem(item)
    app.processEvents()
    return app, panel


def test_legend_entries_are_pickable_and_toggle(tmp_path):
    app, panel = _panel(tmp_path)
    assert panel.figure.axes
    lm = panel._legend_map
    assert lm, "legend map empty — entries not pickable"

    art = next(iter(lm))
    origs = lm[art]
    before = origs[0].get_visible()

    class _E:
        pass
    ev = _E(); ev.artist = art
    panel._on_legend_pick(ev)
    assert origs[0].get_visible() != before          # toggled off
    panel._on_legend_pick(ev)
    assert origs[0].get_visible() == before           # toggled back on


def test_axis_range_override(tmp_path):
    app, panel = _panel(tmp_path)
    vb = panel.view_bar
    # default Auto → no override
    assert vb.xrange() is None and vb.yrange() is None
    vb.x_auto.setChecked(False)
    vb.x_min.setValue(-20.0); vb.x_max.setValue(300.0)
    vb.y_auto.setChecked(False)
    vb.y_min.setValue(-1.0); vb.y_max.setValue(1.0)
    app.processEvents()
    ax = panel.figure.axes[0]
    assert ax.get_xlim() == pytest.approx((-20.0, 300.0))
    assert ax.get_ylim() == pytest.approx((-1.0, 1.0))


def test_density_toggle_default_current(tmp_path):
    app, panel = _panel(tmp_path)
    # Assign roles + compute metrics so plot_capture (with a right axis) runs.
    bar = panel.pico_role_bar
    c = bar._combos
    c["Channel A"].setCurrentIndex(c["Channel A"].findData("v_mon"))
    c["Channel D"].setCurrentIndex(c["Channel D"].findData("i_mon"))
    bar.metrics_chk.setChecked(True)
    app.processEvents()
    assert panel.view_bar.density() is False           # default current
    labels = [a.get_ylabel() for a in panel.figure.axes]
    # Right axis is "Current (µA)" — NOT "Current monitor" (operator).
    assert any("Current (µA)" in s for s in labels)
    assert not any("monitor" in s.lower() for s in labels)
    # Switch to density
    panel.view_bar.unit_combo.setCurrentIndex(1)
    app.processEvents()
    labels = [a.get_ylabel() for a in panel.figure.axes]
    assert any("A/cm" in s for s in labels)


def test_trace_style_override_persists_across_rerender(tmp_path):
    app, panel = _panel(tmp_path)
    labels = panel._current_trace_labels()
    assert labels
    target = labels[0]
    panel.set_trace_style(target, color="#ff0000", linestyle="--")
    app.processEvents()

    def _line(lbl):
        for ax in panel.figure.axes:
            for ln in ax.get_lines():
                if ln.get_label() == lbl:
                    return ln
        return None
    ln = _line(target)
    assert ln.get_color() == "#ff0000"
    assert ln.get_linestyle() == "--"
    # Re-render and confirm the override is re-applied to the new lines.
    panel._finish_render()
    assert _line(target).get_color() == "#ff0000"


def _make_session_npz(tmp_path, name="sess.npz"):
    from stimtest.session import (Session, TestParameters, Capture, ChannelRun,
                                  CaptureMetrics, CaptureStatus)
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.persistence import save_session_npz
    pat = PulsePattern.biphasic(amplitude_ua=100.0)
    t = np.linspace(-50, 450, 400)
    sess = Session(
        notebook="nb", subject="subj",
        test=TestParameters(
            experiment="VT", duration_s=0.0, polarization_method="MP",
            counter_electrode_label="Pt", reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1),
            pattern=pat, array=ElectrodeArray.utah_4x4()),
        runs=[ChannelRun(
            configuration=Configuration.monopolar(1),
            captures=[Capture(
                index=0, pattern=pat, time_us=t,
                v_mon_v=np.sin(t / 50), i_mon_ua=np.cos(t / 50) * 100,
                metrics=CaptureMetrics(), status=CaptureStatus(good=True))])])
    p = tmp_path / name
    save_session_npz(sess, p)
    return p


def test_channel_toggles_are_a_column(tmp_path):
    app, panel = _panel(tmp_path)
    from PyQt6 import QtWidgets
    assert isinstance(panel.trace_toggles.channel_row, QtWidgets.QVBoxLayout)


def test_toggle_bar_hidden_for_pico_shown_for_session(tmp_path):
    from PyQt6 import QtWidgets
    from stimtest.gui.viewer import (ViewerPanel, KIND_SESSION, KIND_PICO,
                                     ROLE_KIND)
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    npz = _make_session_npz(tmp_path)
    pico = tmp_path / "p.csv"
    pico.write_text(_CSV, encoding="utf-8")
    panel = ViewerPanel(); panel.resize(1000, 600); panel.show()
    panel.load_session_file(npz)
    panel.load_session_file(pico)
    app.processEvents()
    root = panel.tree.invisibleRootItem()
    sess_item = next(root.child(i) for i in range(root.childCount())
                     if root.child(i).data(0, ROLE_KIND) == KIND_SESSION)
    pico_item = next(root.child(i) for i in range(root.childCount())
                     if root.child(i).data(0, ROLE_KIND) == KIND_PICO)
    panel.tree.setCurrentItem(sess_item); app.processEvents()
    assert not panel.trace_toggles.isHidden()           # shown for overlay
    panel.tree.setCurrentItem(pico_item); app.processEvents()
    assert panel.trace_toggles.isHidden()               # hidden for pico
    assert not panel.pico_role_bar.isHidden()


def test_open_adds_and_remove_drops(tmp_path):
    from PyQt6 import QtWidgets
    from stimtest.gui.viewer import ViewerPanel, KIND_PICO, ROLE_KIND
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    npz = _make_session_npz(tmp_path)
    pico = tmp_path / "p.csv"; pico.write_text(_CSV, encoding="utf-8")
    panel = ViewerPanel()
    panel.load_session_file(npz)
    panel.load_session_file(pico)
    root = panel.tree.invisibleRootItem()
    assert root.childCount() == 2                        # both added
    # Dedupe — re-opening doesn't add a third branch.
    panel.load_session_file(npz)
    assert root.childCount() == 2
    # Remove the pico branch.
    pico_item = next(root.child(i) for i in range(root.childCount())
                     if root.child(i).data(0, ROLE_KIND) == KIND_PICO)
    panel.tree.setCurrentItem(pico_item)
    panel._remove_selected_item()
    assert root.childCount() == 1
    assert str(pico) not in panel._pico


def test_open_style_dialog_builds_without_error(tmp_path, monkeypatch):
    """The Styles… dialog must BUILD (operator: "Pressing Styles.. does
    nothing" — `_LINESTYLE_CHOICES` was a ViewerWindow class attr the
    method-copy loop didn't transplant onto ViewerPanel, so the handler
    AttributeError'd silently)."""
    from PyQt6 import QtWidgets
    app, panel = _panel(tmp_path)
    built = {}

    def _fake_exec(self):
        built["combos"] = len(self.findChildren(QtWidgets.QComboBox))
        return QtWidgets.QDialog.DialogCode.Rejected

    monkeypatch.setattr(QtWidgets.QDialog, "exec", _fake_exec)
    panel._open_style_dialog()                     # must not raise
    assert built.get("combos", 0) >= 1             # one line-style combo per trace


def test_overlay_density_toggle_relabels_and_scales(tmp_path):
    """Density toggle converts the overlay's I_mon (µA) to A/cm² and
    relabels the right axis (operator: "choosing to do current density
    plotting instead of current")."""
    from PyQt6 import QtWidgets
    from stimtest.persistence import save_session_npz
    from stimtest.gui.viewer import ViewerPanel, KIND_SESSION, ROLE_KIND
    from stimtest.session import (Session, TestParameters, Capture, ChannelRun,
                                  CaptureMetrics, CaptureStatus)
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    pat = PulsePattern.biphasic(amplitude_ua=100.0)
    t = np.linspace(-50, 450, 200)
    runs = [ChannelRun(configuration=Configuration.monopolar(ch),
            surface_area_um2=2000.0,
            captures=[Capture(index=0, pattern=pat, time_us=t,
                v_mon_v=np.sin(t / 50), i_mon_ua=np.full_like(t, 100.0),
                metrics=CaptureMetrics(), status=CaptureStatus(good=True))])
            for ch in range(1, 3)]
    sess = Session(notebook="nb", subject="subj",
        test=TestParameters(experiment="VT", duration_s=0.0,
            polarization_method="MP", counter_electrode_label="Pt",
            reference_electrode_label="", target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1), pattern=pat,
            array=ElectrodeArray.utah_4x4()), runs=runs)
    npz = tmp_path / "s.npz"; save_session_npz(sess, npz)
    panel = ViewerPanel(); panel.load_session_file(npz)
    root = panel.tree.invisibleRootItem()
    s_item = next(root.child(i) for i in range(root.childCount())
                 if root.child(i).data(0, ROLE_KIND) == KIND_SESSION)
    panel.tree.setCurrentItem(s_item); app.processEvents()
    assert any("Current [µA]" in a.get_ylabel() for a in panel.figure.axes)
    panel.view_bar.unit_combo.setCurrentIndex(1)            # density
    app.processEvents()
    assert any("A/cm" in a.get_ylabel() for a in panel.figure.axes)
    imon = [ln for ax in panel.figure.axes for ln in ax.get_lines()
            if "I_mon" in ln.get_label()]
    # 100 µA / 2000 µm² = 5.0 A/cm²
    assert max(abs(ln.get_ydata()).max() for ln in imon) == pytest.approx(5.0, rel=0.05)


def _make_session_obj():
    from stimtest.session import (Session, TestParameters, Capture, ChannelRun,
                                  CaptureMetrics, CaptureStatus)
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    pat = PulsePattern.biphasic(amplitude_ua=100.0)
    t = np.linspace(-50, 450, 300)
    return Session(
        notebook="session_001", subject="electrode_a1",
        test=TestParameters(
            experiment="VT", duration_s=0.0, polarization_method="MP",
            counter_electrode_label="Pt", reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1),
            pattern=pat, array=ElectrodeArray.utah_4x4()),
        runs=[ChannelRun(
            configuration=Configuration.monopolar(1),
            captures=[Capture(
                index=0, pattern=pat, time_us=t,
                v_mon_v=np.sin(t / 50), i_mon_ua=np.cos(t / 50) * 100,
                metrics=CaptureMetrics(), status=CaptureStatus(good=True))])])


def test_pulsar_xlsx_export_is_not_loaded_as_scope(tmp_path):
    """A PULSAR session .xlsx EXPORT must be recognised and rejected by the
    PicoScope loader (operator: "Nothing is opening with the XLSX file" —
    it was a PULSAR export misparsed as a scope capture)."""
    pytest.importorskip("openpyxl")
    from stimtest.persistence import is_pulsar_session_xlsx, load_picoscope, save_session_npz
    from stimtest.gamry_export import save_session_xlsx
    sess = _make_session_obj()
    xlsx = tmp_path / "session_001_electrode_a1.xlsx"
    save_session_xlsx(sess, xlsx)
    assert is_pulsar_session_xlsx(xlsx) is True
    with pytest.raises(ValueError):
        load_picoscope(xlsx)
    # A real PicoScope xlsx is NOT flagged.
    import openpyxl
    real = tmp_path / "real.xlsx"
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Time", "Channel A"]); ws.append(["(us)", "(V)"])
    ws.append([None, None]); ws.append([0, 0.0]); ws.append([1, -0.5])
    wb.save(real)
    assert is_pulsar_session_xlsx(real) is False


def test_folder_index_excludes_pulsar_exports(tmp_path):
    """Folder open shows the .npz session + genuine external captures, but
    NOT PULSAR's own .xlsx export (operator: "only show what is
    opened/imported")."""
    pytest.importorskip("openpyxl")
    from PyQt6 import QtWidgets
    from stimtest.persistence import save_session_npz
    from stimtest.gamry_export import save_session_xlsx
    from stimtest.gui.viewer import ViewerPanel, ROLE_PATH
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    sess = _make_session_obj()
    save_session_npz(sess, tmp_path / "session_001_electrode_a1.npz")
    save_session_xlsx(sess, tmp_path / "session_001_electrode_a1.xlsx")
    (tmp_path / "zach.csv").write_text(
        "Time,Channel A\n(us),(V)\n\n0,0\n1,0.5\n", encoding="utf-8")
    panel = ViewerPanel()
    panel.load_folder(tmp_path)
    root = panel.tree.invisibleRootItem().child(0)
    names = [Path(root.child(i).data(0, ROLE_PATH)).name
             for i in range(root.childCount())]
    assert "session_001_electrode_a1.npz" in names
    assert "zach.csv" in names                       # real external capture
    assert "session_001_electrode_a1.xlsx" not in names   # PULSAR export


def test_open_pulsar_xlsx_redirects_to_npz(tmp_path):
    pytest.importorskip("openpyxl")
    from PyQt6 import QtWidgets
    from stimtest.persistence import save_session_npz
    from stimtest.gamry_export import save_session_xlsx
    from stimtest.gui.viewer import ViewerPanel, ROLE_PATH
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    sess = _make_session_obj()
    save_session_npz(sess, tmp_path / "session_001_electrode_a1.npz")
    save_session_xlsx(sess, tmp_path / "session_001_electrode_a1.xlsx")
    panel = ViewerPanel()
    panel.load_session_file(tmp_path / "session_001_electrode_a1.xlsx")
    root = panel.tree.invisibleRootItem()
    names = [Path(root.child(i).data(0, ROLE_PATH)).name
             for i in range(root.childCount())]
    assert names == ["session_001_electrode_a1.npz"]   # redirected, no .xlsx


def test_view_bar_prefs_round_trip(tmp_path):
    app, panel = _panel(tmp_path)
    vb = panel.view_bar
    vb.x_auto.setChecked(False)
    vb.x_min.setValue(-10.0); vb.x_max.setValue(250.0)
    vb.unit_combo.setCurrentIndex(1)                   # density
    saved = panel.current_prefs()
    assert "view_bar" in saved

    from stimtest.gui.viewer import ViewerPanel
    panel2 = ViewerPanel()
    panel2.restore_prefs(saved)
    assert panel2.view_bar.density() is True
    assert panel2.view_bar.xrange() == pytest.approx((-10.0, 250.0))
