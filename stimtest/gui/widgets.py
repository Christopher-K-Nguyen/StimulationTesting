"""Reusable Qt widgets used by the experiment tabs."""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from PyQt6 import QtCore, QtGui, QtWidgets

try:
    import pyqtgraph as pg
    HAS_PYQTGRAPH = True
except Exception:
    HAS_PYQTGRAPH = False

from ..electrode import ElectrodeArray
from ..session import Capture


# ---------------------------------------------------------------------------
# Live oscilloscope plot
# ---------------------------------------------------------------------------
class ScopePlot(QtWidgets.QWidget):
    """Multi-trace scope view (pyqtgraph if available, else QPainter fallback)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if HAS_PYQTGRAPH:
            pg.setConfigOptions(antialias=True, background="w", foreground="k")
            self._plot = pg.PlotWidget()
            self._plot.showGrid(x=True, y=True, alpha=0.3)
            self._plot.setLabel("bottom", "Time", units="µs")
            self._plot.setLabel("left", "Voltage", units="V")
            self._plot.addLegend()
            layout.addWidget(self._plot)
            self._curves: Dict[str, pg.PlotDataItem] = {}
        else:
            label = QtWidgets.QLabel("pyqtgraph not installed; install for live plots.")
            label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(label)
            self._plot = None
            self._curves = {}

    def clear(self):
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        self._plot.clear()
        self._curves = {}

    def set_traces(self, time_us: np.ndarray,
                   traces: Dict[str, np.ndarray],
                   colors: Optional[Dict[str, str]] = None):
        if not HAS_PYQTGRAPH or self._plot is None:
            return
        colors = colors or {}
        for name, y in traces.items():
            color = colors.get(name, "k")
            if name in self._curves:
                self._curves[name].setData(time_us, y)
            else:
                pen = pg.mkPen(color=color, width=2)
                self._curves[name] = self._plot.plot(time_us, y, name=name, pen=pen)


# ---------------------------------------------------------------------------
# Channel grid (interactive electrode picker)
# ---------------------------------------------------------------------------
class ChannelGrid(QtWidgets.QWidget):
    """Click-to-pick visual representation of an electrode array."""
    selectionChanged = QtCore.pyqtSignal(int, list)   # active, returns

    def __init__(self, array: ElectrodeArray, parent=None):
        super().__init__(parent)
        self.array = array
        self._active: Optional[int] = None
        self._returns: List[int] = []
        self._buttons: Dict[int, QtWidgets.QPushButton] = {}
        grid = QtWidgets.QGridLayout(self)
        grid.setSpacing(4)
        for s in array.sites:
            btn = QtWidgets.QPushButton(f"{s.number}")
            btn.setFixedSize(48, 48)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _, n=s.number: self._on_click(n))
            self._buttons[s.number] = btn
            grid.addWidget(btn, s.row, s.col)
        self._refresh_styles()

    def _on_click(self, n: int):
        mods = QtWidgets.QApplication.keyboardModifiers()
        shift = bool(mods & QtCore.Qt.KeyboardModifier.ShiftModifier)
        if not shift:
            self._active = n
            self._returns = [r for r in self._returns if r != n]
        else:
            if n == self._active:
                return
            if n in self._returns:
                self._returns.remove(n)
            else:
                self._returns.append(n)
        self._refresh_styles()
        self.selectionChanged.emit(self._active or -1, list(self._returns))

    def _refresh_styles(self):
        for n, btn in self._buttons.items():
            if n == self._active:
                btn.setStyleSheet("background:#e57373;color:white;font-weight:bold;")
            elif n in self._returns:
                btn.setStyleSheet("background:#81c784;color:white;font-weight:bold;")
            else:
                btn.setStyleSheet("")
            btn.setChecked(n == self._active or n in self._returns)

    def selection(self):
        return self._active, list(self._returns)

    def set_selection(self, active: int, returns: List[int]) -> None:
        self._active = active
        self._returns = list(returns)
        self._refresh_styles()


# ---------------------------------------------------------------------------
# Metric table
# ---------------------------------------------------------------------------
class MetricTable(QtWidgets.QTableWidget):
    """Tabular view of the most recent capture metrics."""

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Metric", "Value"])
        self.horizontalHeader().setStretchLastSection(True)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)

    def show_capture(self, c: Capture) -> None:
        m = c.metrics
        rows = [
            ("Pulse #", str(c.index)),
            ("Excitation amp (µA)", f"{c.pattern.excitation_phase.amplitude_ua:.2f}"),
            ("Q_ph (nC)", f"{m.charge_per_phase_nc:.2f}"),
            ("Q_inj (mC/cm²)", f"{m.charge_injection_mc_per_cm2:.3f}"),
            ("V_d (V)", f"{m.driving_voltage_v:.3f}"),
            ("C_eff (nF)", f"{m.effective_capacitance_nf:.2f}"),
            ("C_d (mF/cm²)", f"{m.driving_capacitance_mf_per_cm2:.3f}"),
        ]
        for k, vlist in (("V_a (V)", m.access_voltage_per_phase_v),
                         ("R_a (kΩ)", m.access_resistance_per_phase_kohm),
                         ("E_pol active (V)", m.polarization_per_phase_v),
                         ("E_pol return (V)", m.return_polarization_per_phase_v)):
            if vlist:
                rows.append((k, ", ".join(f"{x:.3f}" for x in vlist)))
        rows.append(("Limit reached?", "yes" if c.status.reached_potential_limit else "no"))
        rows.append(("Compliance?", "yes" if c.status.voltage_compliance else "no"))
        self.setRowCount(len(rows))
        for i, (k, v) in enumerate(rows):
            self.setItem(i, 0, QtWidgets.QTableWidgetItem(k))
            self.setItem(i, 1, QtWidgets.QTableWidgetItem(v))


# ---------------------------------------------------------------------------
# Status bar / log widget
# ---------------------------------------------------------------------------
class LogPane(QtWidgets.QPlainTextEdit):
    """Read-only message log shown at the bottom of the GUI.

    Every line written via :meth:`log` is shown in the pane AND
    appended (with timestamp) to a configurable ``log.txt`` on disk
    so the user has a permanent record of what happened during a
    session — useful for debugging "why did this run abort?" the
    next morning. ``set_log_file(path)`` repoints the file when the
    save directory changes; passing ``None`` disables disk logging.
    The pane keeps only the last 1000 lines in memory but the file
    on disk is unbounded.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(1000)
        self._log_file_path = None  # type: Optional[Path]

    def set_log_file(self, path) -> None:
        """Direct subsequent log writes to ``path`` (or ``None`` to
        disable disk logging). Existing content of the file is left
        alone — logs from past sessions in the same folder are
        appended to, not overwritten.
        """
        from pathlib import Path
        if path is None:
            self._log_file_path = None
            return
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            # Don't blow up the GUI just because the save path is
            # bad — disk logging is best-effort.
            self._log_file_path = None
            return
        self._log_file_path = p

    @QtCore.pyqtSlot(str)
    def log(self, msg: str) -> None:
        ts = QtCore.QTime.currentTime().toString("HH:mm:ss")
        line = f"[{ts}] {msg}"
        self.appendPlainText(line)
        # Best-effort disk mirror. Append mode + per-line flush so
        # a crash mid-session still leaves a partial log on disk.
        # Errors are silently swallowed because the pane is the
        # source of truth — the file is just a convenience copy.
        if self._log_file_path is not None:
            try:
                with self._log_file_path.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except Exception:
                pass
