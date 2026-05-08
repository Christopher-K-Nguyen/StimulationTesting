# StimulationTesting (Python)

Python translation of the MATLAB `PlexStimTek` characterization suite for stimulation
electrodes, with a PyQt6 GUI. Drives a **Plexon PlexStim** electrical stimulator and a
**Tektronix** oscilloscope to characterize SIROF-coated microelectrode arrays.

## What it does

Runs four experiment modes, mirroring the MATLAB workflows in
`matlab_reference/`:

| Mode | Code | Description |
|------|------|-------------|
| Voltage Transient | `VT` / `TV` | Single-shot characterization sweep on each channel: ramp `I_stim` until active or return potential reaches the SIROF water window, capture all metrics. |
| Short-Term Pulsing | `SP` | Continuous pulsing for a fixed duration; logs metrics periodically. |
| Long-Term Pulsing  | `LP` | Long-duration pulsing with **periodic re-characterization** to track drift in `Q_inj`, `V_d`, `R_a`, `C_d`, `E_pol`. |
| Progressive Stress | `PS` | Stepped-current (or stepped-voltage) staircase with frequent characterization, Weibull failure analysis (per JNE 2025 paper). |

For each capture we compute, per the IEEE NER 2025 paper:

- Maximum charge-injection capacity (`Q_inj`) and charge-per-phase (`Q_ph`)
- Driving voltage `V_d` (max |V_mon| and max |E_act − E_ret|)
- Access voltage `V_a` per phase, leading and trailing (Cisnal-style derivative method)
- Access resistance `R_a` per phase, leading and trailing
- Driving capacitance `C_d = max(Q_inj) / V_d`
- Effective per-phase capacitance from linear `dV/dt` fit on the plateau
- Electrode polarization `E_pol` for active and return at 12 µs after each phase

## Quick start

```bash
# Create venv and install
python -m venv .venv
.venv\Scripts\activate              # Windows
pip install -e .

# Launch the GUI (uses simulator if no hardware connected)
python run_gui.py

# Force simulator mode for offline development
python run_gui.py --simulate
```

To work with real hardware on Windows you need:

- **Plexon PlexStim 2.0** (the PyPlexStim wrapper is vendored in
  `stimtest/hardware/pyplexstim/`; the 64-bit `PlexStim64.dll` is included).
- **NI-VISA** (or equivalent) installed for `pyvisa` to find the Tektronix scope over
  USB-TMC. The scope is auto-detected and parsed from `*IDN?` — works with TBS1000,
  TDS2000, MSO/MDO/DPO series.

## Project layout

```
stimtest/
  config.py         electrode catalog (SIROF E_lc / E_la), defaults
  waveforms.py      biphasic / triphasic / arbitrary pulse generators
  metrics.py        all waveform analysis (V_d, V_a, R_a, E_pol, C_d ...)
  electrode.py      ElectrodeArray, Channel, neighbor logic, group enumeration
  session.py        Session / Capture / TestParameters dataclasses (replaces MATLAB File struct)
  persistence.py    save/load (.npz, .json, .mat)
  hardware/
    base.py         Stimulator and Oscilloscope abstract base classes
    plexon.py       real Plexon driver (uses vendored PyPlexStim)
    tektronix.py    real Tek scope driver (pyvisa, auto-model detection)
    simulator.py    realistic mock devices for offline development
    pyplexstim/     vendored Plexon Python wrapper + DLL
  experiments/
    base.py         Experiment runner abstract base
    voltage_transient.py   VT: characterization sweep + max-Q_inj search
    short_pulsing.py
    long_pulsing.py        LP with periodic characterization
    progressive_stress.py  PS with Weibull analysis
  gui/
    main_window.py
    connection_panel.py
    setup_tab.py / voltage_transient_tab.py / pulsing_tab.py / stress_tab.py / results_tab.py
    widgets.py      ScopePlot, ChannelGrid, MetricTable, etc.
tests/                unit tests (pytest)
matlab_reference/     original MATLAB code for traceability
run_gui.py            GUI entry point
run_cli.py            scripted experiment entry point
```

## Status

Skeleton + simulator + canonical voltage-transient sweep are functional.
Long-term and progressive-stress runners are wired into the GUI and HAL but their
analysis routines are marked TODO for the next iteration.

## References

- Nguyen et al., *Charge-Injection Comparison of SIROF-Coated Utah Electrode Arrays*,
  IEEE NER 2025 (`IEEE_NER_Final_V2.pdf`)
- Nguyen et al., *Electrical characterization and accelerated aging of amorphous
  silicon carbide implantable encapsulation*, J. Neural Eng. 22 (2025) 066040.
