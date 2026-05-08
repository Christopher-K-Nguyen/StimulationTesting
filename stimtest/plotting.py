r"""Capture plots in the style of the MATLAB ``getPlot_Tek.m`` exporter.

Mirrors the look-and-feel of the MATLAB Tektronix plot:

* MATLAB's default 4-color palette (``#0072BD``, ``#D95319``, ``#EDB120``,
  ``#7E2F8E``).
* Voltage signals on the **left** Y axis (``Voltage (V)``); the current
  trace is plotted on the **right** Y axis as **current density**
  (``A/cm²``) per the MATLAB code (line 272 of ``getPlot_Tek.m``).
* Both Y axes are forced **symmetric around zero** with a tick at zero.
* Horizontal dashed lines mark the **min and max** of the monitor channel,
  with text labels; cursors at end-of-phase / depolarization sample points
  are scattered and labelled.
* Title = subject (``_`` escaped to a single underscore is fine here, the
  MATLAB ``\_`` was a TeX escape that's not needed in matplotlib).
* Subtitle = capture name (``CH09 v 05 @ 246 µA`` etc.).
* Figure size: 1024×576 px (MATLAB's ``FIG_WIDTH``/``FIG_HEIGHT``); DPI
  defaults to 600 on TIFF / PNG export to match the MATLAB ``-r600`` flag.

Public entry points:

* :func:`plot_capture` — return a matplotlib ``Figure`` for one capture.
* :func:`export_capture_plot` — save one plot to disk.
* :func:`export_session_plots` — write the **final capture** of every
  ChannelRun to a folder, one file each (mirrors what ``runVoltageTransient``
  saves at the end of a sweep).
* :func:`export_session_summary_plots` — additional analysis plots:
  ``Q_inj`` vs ``I_stim`` overlay, ``V_d`` vs ``Q_inj``, etc., aggregated
  across runs. Useful for the IEEE NER paper-style summary figures.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import numpy as np

import matplotlib
matplotlib.use("Agg")  # safe default for headless export; Qt overrides if needed
import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from .session import Capture, ChannelRun, Session


# ---------------------------------------------------------------------------
# Visual constants (matching MATLAB ``getPlot_Tek.m``)
# ---------------------------------------------------------------------------
MATLAB_COLORS = ("#0072BD", "#D95319", "#EDB120", "#7E2F8E",
                 "#77AC30", "#4DBEEE", "#A2142F")
FIG_W_PX, FIG_H_PX = 1024, 576
DEFAULT_DPI = 600
SCREEN_DPI = 100


def _figsize_in(width_px: int = FIG_W_PX, height_px: int = FIG_H_PX,
                dpi: int = SCREEN_DPI) -> Tuple[float, float]:
    """Convert pixel dimensions to matplotlib's inches × dpi sizing."""
    return width_px / dpi, height_px / dpi


# ---------------------------------------------------------------------------
# Y-axis helpers — match the symmetric-around-zero behavior in MATLAB
# ---------------------------------------------------------------------------
def _symmetric_ylim(ax) -> None:
    """Force a matplotlib axis Y-range to be symmetric around zero with a
    tick at zero. Mirrors the ``yLimL_min = -yLimL_big`` block in the
    MATLAB code (lines 371-398).
    """
    y0, y1 = ax.get_ylim()
    big = max(abs(y0), abs(y1))
    if big == 0:
        return
    # Add 5% headroom so traces don't kiss the spine
    big = big * 1.05
    ax.set_ylim(-big, big)
    # Aim for ~5 ticks symmetric about zero
    step = _round_significant(big / 2, 2)
    if step == 0:
        return
    n = int(np.ceil(big / step))
    ticks = [step * k for k in range(-n, n + 1)]
    ax.set_yticks([t for t in ticks if -big <= t <= big])


def _round_significant(x: float, sig: int = 2) -> float:
    """Round ``x`` to ``sig`` significant digits (positive number expected)."""
    if x == 0 or not np.isfinite(x):
        return 0.0
    d = sig - int(np.floor(np.log10(abs(x)))) - 1
    return float(round(x, d))


# ---------------------------------------------------------------------------
# Cursor / annotation pickers
# ---------------------------------------------------------------------------
def _phase_end_times_us(capture: Capture) -> List[Tuple[str, float]]:
    """End-of-phase sample times (µs) — used for cursors on the plot.

    These are the cursor positions ``getPlot_Tek.m`` asks the user to set
    interactively. Here we pick them automatically since we know the pulse
    structure: end of each phase, plus a depolarization-time-after-phase
    sample. ``DEPOLARIZATION_TIME_US`` from ``config.py`` is the offset.
    """
    from .config import DEPOLARIZATION_TIME_US
    out: List[Tuple[str, float]] = []
    cursor = 0.0
    for k, ph in enumerate(capture.pattern.phases, start=1):
        cursor += ph.width_us
        out.append((f"Epol{k}", cursor + DEPOLARIZATION_TIME_US))
        cursor += ph.delay_after_us
    return out


def _channel_label(name: str) -> str:
    """Per-trace legend / axis label, with units in parentheses."""
    base = {
        "v_mon": "Voltage (V)",
        "i_mon": "Current Density (A/cm²)",
        "e_act": "Active Potential (V)",
        "e_ret": "Return Potential (V)",
    }
    return base.get(name, name)


# ---------------------------------------------------------------------------
# Core plot
# ---------------------------------------------------------------------------
def plot_capture(capture: Capture, run: ChannelRun, session: Session,
                 *, fig: Optional[Figure] = None,
                 show_cursors: bool = True,
                 show_minmax: bool = True) -> Figure:
    """Render one capture on a matplotlib Figure.

    Layout matches ``getPlot_Tek.m``:
      * Left axis: V_mon / E_act / E_ret traces (volts).
      * Right axis: current density (A/cm²), red, dashed-min/max horizontal lines.
      * Title = session subject; subtitle = ``CH active v returns @ amp µA``.
    """
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    ax_v = fig.add_subplot(111)
    ax_i = ax_v.twinx()

    time_us = np.asarray(capture.time_us)
    voltage_traces: List[Tuple[str, np.ndarray, str]] = []
    if capture.v_mon_v is not None and capture.v_mon_v.size:
        voltage_traces.append(("Voltage", capture.v_mon_v, MATLAB_COLORS[0]))
    if capture.e_act_v is not None and capture.e_act_v.size:
        voltage_traces.append(("Active", capture.e_act_v, MATLAB_COLORS[2]))
    if capture.e_ret_v is not None and capture.e_ret_v.size:
        voltage_traces.append(("Return", capture.e_ret_v, MATLAB_COLORS[3]))

    for name, y, color in voltage_traces:
        ax_v.plot(time_us, y, color=color, linewidth=1.4, label=name)

    # Current density on the right axis (same color as MATLAB: orange)
    if capture.i_mon_ua is not None and capture.i_mon_ua.size:
        area_cm2 = max(run.surface_area_um2 * 1e-8, 1e-12)
        j_density_a_cm2 = capture.i_mon_ua * 1e-6 / area_cm2
        ax_i.plot(time_us, j_density_a_cm2,
                  color=MATLAB_COLORS[1], linewidth=1.4,
                  label="Current Density")
        if show_minmax:
            jd_max = float(np.nanmax(j_density_a_cm2))
            jd_min = float(np.nanmin(j_density_a_cm2))
            ax_i.axhline(jd_max, color=MATLAB_COLORS[1], linestyle=":",
                         linewidth=1.0, alpha=0.7)
            ax_i.axhline(jd_min, color=MATLAB_COLORS[1], linestyle=":",
                         linewidth=1.0, alpha=0.7)
            ax_i.text(time_us[-1], jd_max, f"  {jd_max:.3g} A/cm²",
                      color=MATLAB_COLORS[1], va="center", ha="left", fontsize=9)
            ax_i.text(time_us[-1], jd_min, f"  {jd_min:.3g} A/cm²",
                      color=MATLAB_COLORS[1], va="center", ha="left", fontsize=9)

    # End-of-phase cursors on the V_mon trace
    if show_cursors and voltage_traces:
        cursors = _phase_end_times_us(capture)
        v_data = voltage_traces[0][1]
        for label, t_us in cursors:
            if t_us < time_us[0] or t_us > time_us[-1]:
                continue
            idx = int(np.searchsorted(time_us, t_us))
            idx = min(max(idx, 0), v_data.size - 1)
            v_at = float(v_data[idx])
            ax_v.scatter([t_us], [v_at], marker="x", s=70,
                         color="0.4", linewidths=2, zorder=10)
            ax_v.annotate(f"{label} = {v_at:.3f} V",
                          (t_us, v_at), xytext=(6, 6),
                          textcoords="offset points",
                          fontsize=10, color="0.2")

    # ---- Axes / cosmetics ----
    ax_v.set_xlabel("Time (µs)", fontsize=14)
    ax_v.set_ylabel("Voltage (V)", color=MATLAB_COLORS[0], fontsize=14)
    ax_i.set_ylabel("Current Density (A/cm²)",
                    color=MATLAB_COLORS[1], fontsize=14, rotation=-90,
                    labelpad=18, va="bottom")
    ax_v.tick_params(axis="both", labelsize=12)
    ax_i.tick_params(axis="y", labelsize=12, colors=MATLAB_COLORS[1])
    ax_v.spines["left"].set_color(MATLAB_COLORS[0])
    ax_i.spines["right"].set_color(MATLAB_COLORS[1])

    # X limits hugging the data; symmetric Y limits like MATLAB
    if time_us.size:
        ax_v.set_xlim(float(time_us[0]), float(time_us[-1]))
    _symmetric_ylim(ax_v)
    _symmetric_ylim(ax_i)
    ax_v.grid(True, axis="both", alpha=0.25, linestyle=":")
    ax_v.axhline(0, color="0.4", linewidth=0.6, zorder=0)
    ax_v.axvline(0, color="0.4", linewidth=0.6, zorder=0)

    # Title + subtitle (using suptitle for the session subject)
    subject = session.subject or session.name
    amp = capture.pattern.excitation_phase.amplitude_ua
    cap_label = f"{run.configuration.display_name()}  |  {amp:+.1f} µA  |  capture {capture.index}"
    fig.suptitle(subject, fontsize=15, fontweight="bold")
    ax_v.set_title(cap_label, fontsize=12, color="0.2")

    # Legend (combined left + right)
    h_v, l_v = ax_v.get_legend_handles_labels()
    h_i, l_i = ax_i.get_legend_handles_labels()
    if h_v or h_i:
        ax_v.legend(h_v + h_i, l_v + l_i, loc="best", fontsize=11,
                    framealpha=0.85)

    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return fig


# ---------------------------------------------------------------------------
# Multi-channel waveform overlay
# ---------------------------------------------------------------------------
# Names exactly as they should appear in the toggle UI and plot legend.
# Mapped to the corresponding ``Capture`` attribute so the plotter can
# fetch each trace generically.
WAVE_TYPES = ("V_mon", "I_mon", "E_act", "E_ret")
_WAVE_ATTR = {
    "V_mon": "v_mon_v",
    "I_mon": "i_mon_ua",
    "E_act": "e_act_v",
    "E_ret": "e_ret_v",
}


def plot_overlay(captures_by_channel: Dict,
                 *, fig: Optional[Figure] = None,
                 channels_enabled: Optional[Set] = None,
                 waves_enabled: Optional[Set[str]] = None,
                 title: str = "Channel waveform overlay") -> Figure:
    """Overlay one capture per entry onto a shared time axis.

    ``captures_by_channel`` maps an arbitrary hashable key to the
    Capture that key should contribute to the overlay (typically the
    final / representative capture of one ``ChannelRun``). The key
    doubles as the legend label, so callers should use a
    user-meaningful string — e.g. ``"CH05"`` for monopolar,
    ``"CH05 v 06"`` for bipolar, ``"CH05 v 06,09"`` for tripolar
    (matches :meth:`Configuration.display_name`). The legacy
    ``Dict[int, Capture]`` form is still accepted; integer keys
    render as ``"CHnn"``.

    Entries not in ``channels_enabled`` are skipped; waveforms not in
    ``waves_enabled`` are skipped per entry. Left voltage axis carries
    V_mon / E_act / E_ret (all sharing volts); right axis carries
    I_mon. Color encodes the entry; line style encodes waveform type.

    The user toggles entries and waveform types via the UI bar in
    :class:`stimtest.gui.viewer.ViewerPanel`; this function is the
    pure-matplotlib renderer that consumes those choices.
    """
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    ax_v = fig.add_subplot(111)
    ax_i = ax_v.twinx()
    if channels_enabled is None:
        channels_enabled = set(captures_by_channel.keys())
    if waves_enabled is None:
        waves_enabled = set(WAVE_TYPES)
    plotted_any = False
    # Stable order: sort by string repr so int keys order numerically
    # ("CH01", "CH02", ...) and string keys order lexicographically
    # ("CH05 v 06", "CH05 v 06,09", ...). tab20 wraps gracefully past
    # 20 entries.
    cmap = plt.get_cmap("tab20")
    keys = sorted((k for k in captures_by_channel if k in channels_enabled),
                  key=lambda x: (str(x)))
    for idx, key in enumerate(keys):
        cap = captures_by_channel[key]
        time_us = np.asarray(cap.time_us)
        if not time_us.size:
            continue
        color = cmap(idx % cmap.N)
        # Legend label: use the key as-is for strings; format ints
        # as "CHnn" for the legacy int-keyed callers.
        label_prefix = (f"CH{int(key):02d}"
                        if isinstance(key, int) else str(key))
        # Voltage traces share the left axis; pick a different line
        # style per wave-type so the same color = same entry and
        # the same dash = same waveform.
        v_styles = {"V_mon": "-", "E_act": "--", "E_ret": ":"}
        for wave in ("V_mon", "E_act", "E_ret"):
            if wave not in waves_enabled:
                continue
            data = getattr(cap, _WAVE_ATTR[wave], None)
            if data is None or not getattr(data, "size", 0):
                continue
            ax_v.plot(time_us, data, color=color,
                      linestyle=v_styles[wave], linewidth=1.2,
                      label=f"{label_prefix} {wave}")
            plotted_any = True
        if "I_mon" in waves_enabled and cap.i_mon_ua is not None and cap.i_mon_ua.size:
            ax_i.plot(time_us, cap.i_mon_ua, color=color,
                      linestyle="-.", linewidth=1.0,
                      label=f"{label_prefix} I_mon")
            plotted_any = True

    if not plotted_any:
        ax_v.text(0.5, 0.5,
                  "No traces to display.\n"
                  "Enable at least one channel and one waveform type.",
                  ha="center", va="center", color="#666",
                  transform=ax_v.transAxes, fontsize=10)
        ax_v.axis("off")
        ax_i.axis("off")
        return fig

    ax_v.set_xlabel("Time (µs)", fontsize=12)
    ax_v.set_ylabel("Voltage (V)", fontsize=12)
    ax_i.set_ylabel("Current monitor (µA)", fontsize=12,
                    rotation=-90, labelpad=18, va="bottom")
    ax_v.tick_params(axis="both", labelsize=10)
    ax_i.tick_params(axis="y", labelsize=10)
    ax_v.set_title(title)
    # Combined legend; place outside on the right when many channels
    # are visible so the trace area stays readable.
    h_v, l_v = ax_v.get_legend_handles_labels()
    h_i, l_i = ax_i.get_legend_handles_labels()
    handles = h_v + h_i
    labels = l_v + l_i
    if handles:
        ncol = 1 if len(handles) <= 8 else 2
        ax_v.legend(handles, labels, loc="upper left",
                    bbox_to_anchor=(1.10, 1.0), fontsize=8, ncol=ncol,
                    framealpha=0.85)
    fig.tight_layout(rect=(0, 0, 0.85, 1))
    return fig


# ---------------------------------------------------------------------------
# Static export (single capture)
# ---------------------------------------------------------------------------
def export_capture_plot(capture: Capture, run: ChannelRun, session: Session,
                        path: Path | str, *, dpi: int = DEFAULT_DPI) -> Path:
    """Save a single capture's plot to disk (TIFF/PNG/PDF/SVG by extension).

    Default DPI is 600, matching MATLAB's ``-r600`` TIFF export. To match the
    pixel size at 600 dpi (10.24″ × 5.76″ × 600 = 6144 × 3456 px) the figure
    is created at 100 dpi screen size and matplotlib rescales on save.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    try:
        plot_capture(capture, run, session, fig=fig)
        fig.savefig(str(path), dpi=dpi, bbox_inches="tight",
                    facecolor="white")
    finally:
        plt.close(fig)
    return path


# ---------------------------------------------------------------------------
# Batch export across an entire session
# ---------------------------------------------------------------------------
def _final_capture(run: ChannelRun) -> Optional[Capture]:
    if not run.captures:
        return None
    good = [c for c in run.captures if c.status.good]
    if good:
        return max(good, key=lambda c: abs(c.pattern.excitation_phase.amplitude_ua))
    return run.captures[-1]


def export_session_plots(session: Session, out_dir: Path | str,
                         *, fmt: str = "tif", dpi: int = DEFAULT_DPI,
                         every_capture: bool = False,
                         parallel: bool | int = False) -> List[Path]:
    """Write per-channel plots from a session to ``out_dir``.

    Default behaviour mirrors the MATLAB ``runVoltageTransient`` end-of-sweep
    saving: one file per ChannelRun, showing the *final* (max-amplitude)
    capture. Set ``every_capture=True`` to write one file per capture
    (potentially hundreds for a long sweep).

    Parameters
    ----------
    session, out_dir, fmt, dpi, every_capture
        As before — single-process behaviour is unchanged.
    parallel : bool | int, default False
        ``False`` = sequential rendering on the calling thread.
        ``True`` = use ``min(os.cpu_count(), 8)`` worker processes.
        Integer = use exactly that many workers.

        Each worker holds its own matplotlib state (Agg backend, fork-safe)
        and produces one file. Useful for ``every_capture=True`` exports of
        large sessions (hundreds of plots) — typical 4–8× speedup. For the
        default "one plot per channel" case the overhead of spawning
        workers usually outweighs the saving, so leave it at ``False``.
    """
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lstrip(".").lower()

    # Build the (capture, run, output_path) work list once
    tasks: List[Tuple[Capture, ChannelRun, Path]] = []
    for run in session.runs:
        if run.configuration.id in ("MP", "CG"):
            stem_base = f"CH{run.configuration.active:02d}"
        else:
            rets = "_".join(f"{r:02d}" for r in run.configuration.returns)
            stem_base = f"CH{run.configuration.active:02d}_v_{rets}"
        if every_capture:
            for cap in run.captures:
                fname = f"{session.name}_{stem_base}_cap{cap.index:03d}.{fmt}"
                tasks.append((cap, run, out_dir / fname))
        else:
            cap = _final_capture(run)
            if cap is None:
                continue
            fname = f"{session.name}_{stem_base}.{fmt}"
            tasks.append((cap, run, out_dir / fname))

    if not parallel or len(tasks) <= 1:
        return [export_capture_plot(c, r, session, p, dpi=dpi)
                for c, r, p in tasks]
    return _parallel_export_plots(tasks, session, dpi, parallel)


def _parallel_export_plots(tasks, session: Session, dpi: int,
                           parallel: bool | int) -> List[Path]:
    """Run capture-plot exports across a ProcessPoolExecutor.

    Why ProcessPool not ThreadPool? matplotlib renders are CPU-bound
    (font shaping, AA rasterisation, PNG/TIFF compression) and Python
    holds the GIL for most of it. Processes give true parallelism and the
    Agg backend is fork-safe.

    Each task carries enough data that the worker doesn't need to import
    the experiment runner or hardware drivers — just the plotting module.
    """
    import os
    from concurrent.futures import ProcessPoolExecutor, as_completed

    n_workers = parallel if isinstance(parallel, int) and parallel > 1 \
        else min(os.cpu_count() or 4, 8)

    payloads = [_capture_to_payload(c, r, session, p, dpi)
                for c, r, p in tasks]
    written: List[Path] = []
    with ProcessPoolExecutor(max_workers=n_workers) as ex:
        futures = [ex.submit(_render_plot_payload, p) for p in payloads]
        for fut in as_completed(futures):
            try:
                written.append(fut.result())
            except Exception as e:
                # Surface but don't kill the batch — the caller still gets
                # whatever plots succeeded.
                print(f"[plot worker] {e}")
    return sorted(written)


def _capture_to_payload(cap: Capture, run: ChannelRun, session: Session,
                        path: Path, dpi: int) -> dict:
    """Slim payload for a worker — only the data the plotter needs.

    Avoids pickling the full ``Session`` (with all its runs and captures)
    over to every worker; each task gets just its own capture's arrays plus
    a few scalars for the title.
    """
    return {
        "time_us": np.asarray(cap.time_us),
        "v_mon_v": np.asarray(cap.v_mon_v),
        "i_mon_ua": np.asarray(cap.i_mon_ua),
        "e_act_v": (np.asarray(cap.e_act_v) if cap.e_act_v is not None else None),
        "e_ret_v": (np.asarray(cap.e_ret_v) if cap.e_ret_v is not None else None),
        "phase_widths_us": [ph.width_us for ph in cap.pattern.phases],
        "phase_amps_ua": [ph.amplitude_ua for ph in cap.pattern.phases],
        "phase_delays_us": [ph.delay_after_us for ph in cap.pattern.phases],
        "rate_hz": cap.pattern.rate_hz,
        "polarity": cap.pattern.polarity,
        "surface_area_um2": run.surface_area_um2,
        "config_label": run.configuration.display_name(),
        "subject": session.subject or session.name,
        "capture_index": cap.index,
        "out_path": str(path),
        "dpi": dpi,
    }


def _render_plot_payload(payload: dict) -> Path:
    """Worker entry point — render one capture and save it.

    ``plot_capture`` reads only a handful of attributes off ``run`` and
    ``session`` (``run.configuration.display_name()``,
    ``run.surface_area_um2``, ``session.subject``, ``session.name``), so we
    feed it duck-typed ``SimpleNamespace`` stand-ins instead of pickling
    the full ChannelRun and Session over from the parent process. ``Capture``
    and ``PulsePattern`` are real dataclasses and dirt cheap to rebuild.

    Lives at module top level so ``ProcessPoolExecutor`` (which uses
    ``spawn`` on Windows) can pickle the function reference.
    """
    from types import SimpleNamespace

    # Lazy imports inside the worker so each spawn pays them once and the
    # parent process doesn't drag matplotlib into the import graph just for
    # being a parent.
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from .session import Capture
    from .waveforms import Phase, PulsePattern

    pattern = PulsePattern(
        phases=[Phase(a, w, d) for a, w, d in zip(
            payload["phase_amps_ua"], payload["phase_widths_us"],
            payload["phase_delays_us"])],
        rate_hz=payload["rate_hz"],
    )
    cap = Capture(
        index=payload["capture_index"], pattern=pattern,
        time_us=payload["time_us"], v_mon_v=payload["v_mon_v"],
        i_mon_ua=payload["i_mon_ua"],
        e_act_v=payload["e_act_v"], e_ret_v=payload["e_ret_v"],
    )
    run = SimpleNamespace(
        configuration=SimpleNamespace(
            display_name=lambda label=payload["config_label"]: label),
        surface_area_um2=payload["surface_area_um2"],
    )
    session = SimpleNamespace(
        subject=payload["subject"], name=payload["subject"],
    )

    fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    try:
        plot_capture(cap, run, session, fig=fig)
        path = Path(payload["out_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(path), dpi=payload["dpi"], bbox_inches="tight",
                    facecolor="white")
        return path
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# Sweep / summary plots — Q_inj vs I_stim overlay, V_d vs Q_inj, etc.
# ---------------------------------------------------------------------------
def plot_qinj_vs_amplitude(session: Session, *, fig: Optional[Figure] = None,
                           ) -> Figure:
    """Overlay ``Q_inj`` vs ``I_stim`` for every ChannelRun in the session.

    Useful for picking out which channels saturate early. Each run is one
    coloured line; the colour cycles through the MATLAB palette.
    """
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    ax = fig.add_subplot(111)
    for k, run in enumerate(session.runs):
        if not run.captures:
            continue
        amps = [c.pattern.excitation_phase.amplitude_ua for c in run.captures]
        qinjs = [c.metrics.charge_injection_mc_per_cm2 for c in run.captures]
        color = MATLAB_COLORS[k % len(MATLAB_COLORS)]
        ax.plot(np.abs(amps), qinjs, "-o", color=color, markersize=4,
                linewidth=1.2, label=run.configuration.display_name())
    ax.set_xlabel("|I_stim| (µA)", fontsize=14)
    ax.set_ylabel("Q_inj (mC/cm²)", fontsize=14)
    ax.tick_params(axis="both", labelsize=12)
    ax.grid(True, alpha=0.25, linestyle=":")
    ax.set_title(f"Charge injection sweep — {session.name}", fontsize=13)
    if session.runs:
        ax.legend(loc="best", fontsize=10, framealpha=0.85)
    fig.tight_layout()
    return fig


def plot_vd_vs_qinj(session: Session, *, fig: Optional[Figure] = None) -> Figure:
    """``V_d`` vs ``Q_inj`` scatter — the IEEE NER 2025 paper's Fig 4-style plot."""
    if fig is None:
        fig = plt.figure(figsize=_figsize_in(), dpi=SCREEN_DPI)
    fig.clear()
    ax = fig.add_subplot(111)
    for k, run in enumerate(session.runs):
        if not run.captures:
            continue
        qinjs = [c.metrics.charge_injection_mc_per_cm2 for c in run.captures
                 if np.isfinite(c.metrics.charge_injection_mc_per_cm2)]
        vds = [c.metrics.driving_voltage_v for c in run.captures
               if np.isfinite(c.metrics.driving_voltage_v)]
        n = min(len(qinjs), len(vds))
        if n == 0:
            continue
        color = MATLAB_COLORS[k % len(MATLAB_COLORS)]
        ax.plot(qinjs[:n], vds[:n], "o", color=color, markersize=5,
                label=run.configuration.display_name())
    ax.set_xlabel("Q_inj (mC/cm²)", fontsize=14)
    ax.set_ylabel("V_d (V)", fontsize=14)
    ax.tick_params(axis="both", labelsize=12)
    ax.grid(True, alpha=0.25, linestyle=":")
    ax.set_title(f"Driving voltage vs charge injection — {session.name}",
                 fontsize=13)
    if session.runs:
        ax.legend(loc="best", fontsize=10, framealpha=0.85)
    fig.tight_layout()
    return fig


def export_session_summary_plots(session: Session, out_dir: Path | str,
                                 *, fmt: str = "tif",
                                 dpi: int = DEFAULT_DPI) -> List[Path]:
    """Write the Q_inj-vs-I and V_d-vs-Q_inj overlays. Returns paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lstrip(".").lower()
    written: List[Path] = []

    fig = plot_qinj_vs_amplitude(session)
    p1 = out_dir / f"{session.name}_qinj_vs_istim.{fmt}"
    fig.savefig(str(p1), dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    written.append(p1)

    fig = plot_vd_vs_qinj(session)
    p2 = out_dir / f"{session.name}_vd_vs_qinj.{fmt}"
    fig.savefig(str(p2), dpi=dpi, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    written.append(p2)
    return written
