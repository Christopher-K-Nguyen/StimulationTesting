"""Bode + Nyquist plot widgets for the Galvanostatic EIS experiment.

Two read-only pyqtgraph widgets fed the impedance spectrum (parallel arrays
of frequency / |Z| / phase / Z′ / Z″) via :meth:`set_spectrum`:

* :class:`BodePlot` — two stacked, x-linked panels: **|Z| (Ω)** log-log on
  top, **phase (°)** linear-y over log-frequency on the bottom.  These are
  the repo's FIRST log axes (``PlotItem.setLogMode`` — pyqtgraph applies the
  log10 internally, so ``setData`` gets RAW Hz / Ω values).
* :class:`NyquistPlot` — **−Z″ vs Z′** (Ω), a linear aspect-locked X-Y plot
  (the classic complex-plane impedance locus; the capacitive arc opens
  upward because we plot −Z″).

Both mirror the ``StaircasePlot`` conventions: white background, wheel-zoom
disabled, black axes, a ``set_grid_visible`` toggle wired by the View menu.
The raw arrays are cached on the widget so a redraw / grid toggle / test can
read them back without depending on pyqtgraph's log-mode ``getData``.
"""
from __future__ import annotations

from typing import Sequence

import pyqtgraph as pg
from PyQt6 import QtCore, QtGui, QtWidgets

from .widgets import disable_plot_wheel_zoom

PLOT_FONT_PT = 11
OHM = "Ω"              # Ω
_MAG_COLOR = "#0072B2"      # blue  (colour-blind-safe, matches marker palette)
_PHASE_COLOR = "#CC79A7"    # reddish-purple
_NYQUIST_COLOR = "#0072B2"


def _style(pt: int = PLOT_FONT_PT) -> dict:
    return {"font-size": f"{pt}pt", "color": "#000"}


def _tick_font(pt: int = PLOT_FONT_PT) -> QtGui.QFont:
    f = QtGui.QFont()
    f.setPointSize(pt)
    return f


class BodePlot(QtWidgets.QWidget):
    """|Z| (log-log) over phase (linear-y, log-f), x-linked."""

    MIN_HEIGHT = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Preferred)
        pg.setConfigOptions(antialias=True)
        self._grid_visible = False
        self._freqs: list = []
        self._z_mag: list = []
        self._z_phase: list = []

        self.glw = pg.GraphicsLayoutWidget()
        self.glw.setBackground("w")
        self.mag_plot = self.glw.addPlot(row=0, col=0)
        self.phase_plot = self.glw.addPlot(row=1, col=0)
        # Log axes — the repo's first.  |Z| is log-log; phase is linear-y
        # over a shared log-frequency x.
        self.mag_plot.setLogMode(x=True, y=True)
        self.phase_plot.setLogMode(x=True, y=False)
        self.phase_plot.setXLink(self.mag_plot)

        for plt, ylabel, ycolor in (
                (self.mag_plot, f"|Z| ({OHM})", _MAG_COLOR),
                (self.phase_plot, "Phase (°)", _PHASE_COLOR)):
            plt.setLabel("left", ylabel, **_style())
            plt.setLabel("bottom", "Frequency (Hz)", **_style())
            plt.showGrid(x=False, y=False)
            plt.setMenuEnabled(False)
            plt.hideButtons()
            disable_plot_wheel_zoom(plt)
            for ax_name in ("bottom", "left"):
                ax = plt.getAxis(ax_name)
                if ax is not None:
                    ax.setStyle(tickFont=_tick_font())
                    ax.setTextPen("#000")
                    ax.setPen("#000")

        self.mag_curve = self.mag_plot.plot(
            [], [], pen=pg.mkPen(_MAG_COLOR, width=2),
            symbol="o", symbolSize=6, symbolBrush=_MAG_COLOR)
        self.phase_curve = self.phase_plot.plot(
            [], [], pen=pg.mkPen(_PHASE_COLOR, width=2),
            symbol="o", symbolSize=6, symbolBrush=_PHASE_COLOR)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.glw, stretch=1)

    def set_spectrum(self, freq_hz: Sequence[float], z_mag_ohm: Sequence[float],
                     z_phase_deg: Sequence[float], *_ignored) -> None:
        """Redraw from parallel arrays (extra positional args ignored so the
        tab can pass the full 5-array spectrum to both plots uniformly)."""
        self._freqs = list(freq_hz)
        self._z_mag = list(z_mag_ohm)
        self._z_phase = list(z_phase_deg)
        # pyqtgraph applies log10 for us (setLogMode) — pass RAW values.
        self.mag_curve.setData(self._freqs, self._z_mag)
        self.phase_curve.setData(self._freqs, self._z_phase)

    def clear(self) -> None:
        self.set_spectrum([], [], [])

    def set_grid_visible(self, on: bool) -> None:
        self._grid_visible = bool(on)
        self.mag_plot.showGrid(x=on, y=on)
        self.phase_plot.showGrid(x=on, y=on)


class NyquistPlot(QtWidgets.QWidget):
    """−Z″ vs Z′ (Ω) — the complex-plane impedance locus, aspect-locked."""

    MIN_HEIGHT = 220

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(self.MIN_HEIGHT)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                           QtWidgets.QSizePolicy.Policy.Preferred)
        pg.setConfigOptions(antialias=True)
        self._grid_visible = False
        self._z_real: list = []
        self._z_imag: list = []

        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        disable_plot_wheel_zoom(self.plot)
        self.plot.setLabel("left", f"−Z″ ({OHM})", **_style())
        self.plot.setLabel("bottom", f"Z′ ({OHM})", **_style())
        self.plot.showGrid(x=False, y=False)
        self.plot.setMenuEnabled(False)
        self.plot.hideButtons()
        # Impedance loci are read as SHAPES, so equal axis scales matter.
        self.plot.getViewBox().setAspectLocked(True)
        for ax_name in ("bottom", "left"):
            ax = self.plot.getAxis(ax_name)
            if ax is not None:
                ax.setStyle(tickFont=_tick_font())
                ax.setTextPen("#000")
                ax.setPen("#000")
        self.curve = self.plot.plot(
            [], [], pen=pg.mkPen(_NYQUIST_COLOR, width=2),
            symbol="o", symbolSize=6, symbolBrush=_NYQUIST_COLOR)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.plot, stretch=1)

    def set_spectrum(self, freq_hz: Sequence[float], z_mag_ohm: Sequence[float],
                     z_phase_deg: Sequence[float], z_real_ohm: Sequence[float],
                     z_imag_ohm: Sequence[float]) -> None:
        """Redraw the locus.  Same 5-array signature as :class:`BodePlot` so
        the tab feeds both identically; Nyquist uses Z′ and Z″."""
        self._z_real = list(z_real_ohm)
        self._z_imag = list(z_imag_ohm)
        # Plot −Z″ (upward) vs Z′ — the conventional EIS orientation.
        self.curve.setData(self._z_real, [-y for y in self._z_imag])

    def clear(self) -> None:
        self.set_spectrum([], [], [], [], [])

    def set_grid_visible(self, on: bool) -> None:
        self._grid_visible = bool(on)
        self.plot.showGrid(x=on, y=on)
