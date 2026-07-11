"""The electrode-polarization (Emc/Ema, "+" glyph) label stays NEAR its marker
even when access labels cluster around it.

Operator: "be sure that the electrode polarization marker label is near its
marker."  The scorer gives the "+" tag a doubled proximity pull.
"""
from __future__ import annotations

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")
pg = pytest.importorskip("pyqtgraph")

from PyQt6 import QtWidgets  # noqa: E402
from stimtest.gui.widgets import ScopePlot  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _emc_label_pos(sp):
    for it in sp._marker_items:
        if isinstance(it, pg.TextItem):
            try:
                if "Emc" in it.toPlainText():
                    p = it.pos()
                    return float(p.x()), float(p.y())
            except Exception:
                pass
    return None


def test_emc_label_near_its_marker_in_a_cluster(_app):
    sp = ScopePlot()
    sp.resize(560, 340)
    sp.show()
    t = np.linspace(-60.0, 600.0, 600)
    v = np.zeros_like(t)
    v[(t >= 0) & (t < 200)] = -0.5      # cathodic phase 1
    sp.set_traces(t, {"Vmon": v}, colors={"Vmon": "#E6B800"},
                  axis={"Vmon": "left"})
    sp._plot.getViewBox().setYRange(-1.0, 1.0, padding=0)
    sp._plot.getViewBox().setXRange(-60.0, 600.0, padding=0)
    QtWidgets.QApplication.processEvents()

    mx, my = 205.0, -0.5
    # A CLUSTER of access tags right around the Emc marker (phase-1 end),
    # competing for the nearby placements.
    markers = [
        ("V_a1", 2.0, -0.5, "V_a1 = 0.5 V\nR_a1 = 5 kΩ", "#000000", "hbar", None, True),
        ("V_a2", 198.0, -0.5, "V_a2 = 0.5 V\nR_a2 = 5 kΩ", "#000000", "hbar", None, True),
        ("Emc", mx, my, "Emc = -0.5 V", "#000000", "+", None, True),
    ]
    sp.set_markers(markers)
    QtWidgets.QApplication.processEvents()

    pos = _emc_label_pos(sp)
    assert pos is not None, "Emc label not found"
    (x0, x1), (y0, y1) = sp._plot.getViewBox().viewRange()
    dx = abs(pos[0] - mx) / (x1 - x0)
    dy = abs(pos[1] - my) / (y1 - y0)
    # Near its marker — not flung across the plot.  (The label anchor sits
    # within ~a quarter of the view of the "+" glyph.)
    assert dx <= 0.25, f"Emc label too far in x: {dx:.2f} of view"
    assert dy <= 0.25, f"Emc label too far in y: {dy:.2f} of view"


def test_all_labels_stay_near_markers_on_noisy_dense_capture(_app):
    """Operator: "The placement of the labels need to be better."  On a
    noisy full-range capture (sine V_mon + noisy I_mon) with 7 markers, the
    45x trace-overlap penalty used to fling the access tags into the few
    empty corners — far from their markers.  The quadratic proximity pull
    now caps how far ANY label can drift: every tag stays within ~25 % of
    the plot diagonal of its marker (they were ~50 %+ away before)."""
    import numpy as np
    sp = ScopePlot(); sp.resize(900, 640); sp.show(); _app.processEvents()
    t = np.linspace(-250.0, 950.0, 1500)
    rng = np.random.RandomState(0)
    vmon = np.where((t >= 0) & (t < 200),
                    -1.2 * np.sin(np.pi * np.clip(t, 0, 200) / 200), 0.0)
    vmon += np.where((t >= 220) & (t < 430),
                     1.3 * np.sin(np.pi * np.clip(t - 220, 0, 210) / 210), 0.0)
    imon = (np.where((t >= 0) & (t < 200), -1.0, 0.0)
            + np.where((t >= 220) & (t < 430), 1.0, 0.0)
            + rng.randn(t.size) * 0.6)
    eact = vmon + 0.22
    sp.set_traces(t, {"V_mon": vmon, "I_mon": imon,
                      "E_ret": np.full_like(t, 0.22), "E_act": eact},
                  colors={"V_mon": "#E6B800", "I_mon": "#00B4C8",
                          "E_ret": "#D55E00", "E_act": "#1B7F5C"},
                  axis={"V_mon": "left", "I_mon": "right",
                        "E_ret": "left", "E_act": "left"})
    _app.processEvents()
    markers = [
        ("Va1", 2.0, -1.35, "V_a1=1.5 V\nR_a1=24 kOhm", "#006197", "hbar", None, True),
        ("Va2", 198.0, -1.35, "V_a2=0.08 V\nR_a2=1.3 kOhm", "#006197", "hbar", None, True),
        ("Va3", 222.0, 1.9, "V_a3=0.46 V\nR_a3=7.5 kOhm", "#006197", "hbar", None, True),
        ("Va4", 428.0, 1.4, "V_a4=1.18 V\nR_a4=19 kOhm", "#006197", "hbar", None, True),
        ("Emc", 190.0, -1.22, "E_mc=-1.22 V", "#6B2879", "+", None, True),
        ("Ema", 430.0, 1.42, "E_ma=1.42 V", "#6B2879", "+", None, True),
        ("Vd", 110.0, -1.54, "V_d=-1.54 V", "#AD678E", "+", None, True),
    ]
    sp.set_markers(markers)
    _app.processEvents()
    (x0, x1), (y0, y1) = sp._plot.getViewBox().viewRange()
    xs, ys = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    mm = {m[0]: (m[1], m[2]) for m in markers}
    texts = [it for it in sp._marker_items if isinstance(it, pg.TextItem)]
    assert len(texts) == 7
    worst = 0.0
    for it in texts:
        head = it.toPlainText().split("=")[0].split("\n")[0].strip().replace("_", "")
        key = next((k for k in mm if k.replace("_", "").startswith(head[:3])), None)
        if key is None:
            continue
        mx, my = mm[key]
        p = it.pos()
        d = (((p.x() - mx) / xs) ** 2 + ((p.y() - my) / ys) ** 2) ** 0.5
        worst = max(worst, d)
    assert worst <= 0.25, f"a label drifted {worst:.2f} of the diagonal from its marker"
