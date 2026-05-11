# PULSAR — neural-stimulation electrode characterization

[![tests](https://github.com/Bortz1234/StimulationTesting/actions/workflows/tests.yml/badge.svg)](https://github.com/Bortz1234/StimulationTesting/actions/workflows/tests.yml)
[![python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](pyproject.toml)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**PULSAR** is a PyQt6 desktop application that drives a **Plexon PlexStim 2.0**
electrical stimulator and a **Tektronix** oscilloscope to characterize the
electrochemical performance of neural-stimulation microelectrode arrays
(SIROF, PtIr, IrOx, etc.) following the protocols in
[IEEE NER 2025](#references) and J. Neural Eng. 22 (2025) 066040.
**POLARIS** is the companion session viewer for offline review of saved runs.

> **Legacy-name note.** The Python package is still `stimtest`, the GitHub repo
> is still `Bortz1234/StimulationTesting`, the install path is still
> `Program Files\StimulationTesting`, and the launcher executables are still
> `StimulationTesting.exe` / `StimulationTestingViewer.exe` — all retained for
> back-compat with installed-base prefs / shortcuts / GitHub-redirect machinery.
> The user-facing brand is **PULSAR** (GUI) and **POLARIS** (viewer). See
> [`installer/README.md`](installer/README.md#rename-ledger) for the full
> rename ledger.

---

## Table of contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [Installation](#installation)
- [Features at a glance](#features-at-a-glance)
- [Experiments](#experiments)
- [Metrics computed per capture](#metrics-computed-per-capture)
- [Repository layout](#repository-layout)
- [Documentation](#documentation)
- [Development](#development)
- [Contributing](#contributing)
- [License](#license)
- [References](#references)

---

## What it does

PULSAR runs four standard neural-stimulation characterization experiments
end-to-end, from waveform programming to metric extraction and damage-screening:

| Mode | Code | Description |
|------|------|-------------|
| **Voltage Transient** | `VT` | Single-shot characterization sweep: ramp `I_stim` until polarisation reaches the water window, capture all metrics. |
| **Short-Term Pulsing** | `SP` | Continuous pulsing at fixed amplitude for a configured duration; logs metrics periodically. |
| **Long-Term Pulsing** | `LP` | Long-duration pulsing with **periodic re-characterization** to track drift in `Q_inj`, `V_d`, `R_a`, `C_d`, `E_pol`. |
| **Progressive Stress** | `PS` | Stepped-current staircase with frequent characterization and compliance-aware stopping. |

Every run produces a self-describing `.npz` session file with the full
captured waveforms + computed metrics + setup snapshot, openable in POLARIS
for later review or exportable to Gamry-DTA-style `.xlsx` for downstream
analysis.

## Quick start

**Run the GUI** (simulator mode — no hardware required):

```powershell
python run_gui.py --simulate
```

**Open POLARIS** to browse saved sessions:

```powershell
python run_viewer.py data/         # browse a folder of .npz files
python run_viewer.py data/vt_run.npz   # open a single session
```

**Run the test suite:**

```powershell
python -m pytest tests/ -q
```

## Installation

### From source (developer)

```powershell
# Clone + create a virtualenv
git clone https://github.com/Bortz1234/StimulationTesting.git
cd StimulationTesting
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install with dev extras (pytest, pytest-qt, ruff)
pip install -e ".[dev]"

# Verify
python -m pytest tests/ -q
python run_gui.py --simulate
```

### Production installer (Windows end users)

The `installer/` folder builds a single `.exe` installer that drops the GUI,
the viewer, and all their dependencies under `Program Files\StimulationTesting`.
It also detects (and offers to install) the Plexon PlexStim 2.0 SDK and any
missing Visual C++ / VISA runtimes.

```powershell
# From the repo root, on a Windows build machine with Inno Setup 6
pip install -e ".[build]"
python installer/build.py
# Output: installer/Output/PULSAR-Setup-<version>.exe
```

Full build documentation, prerequisite detection details, and the rename
ledger live in [`installer/README.md`](installer/README.md).

## Features at a glance

- 🧠 **Pulse-pattern designer** with biphasic / triphasic / arbitrary
  waveforms and per-phase shape control (rectangular, sinusoidal,
  exp-decay, halfpipe, bowtie, Gaussian, linear ramps, speedbumps)
- ⚡ **Hardware-aware** — auto-detects Tek scope dialect (modern
  `WFMOutpre:` vs legacy `WFMPre:`), channel count, EXT-trigger
  availability; PlexStim scaling validated by the calibration wizard
- 📈 **Live metrics** — Shannon k-value, Q_inj, V_d, V_a, R_a, E_pol,
  C_d, interpulse potential, NeurostimML damage classifier (Li et
  al. 2024 RF-Partial-19)
- 🛡 **Damage screening** — pre-run modal with environment-aware
  posture (PBS / mISF / cell culture / rat cortex); per-capture
  warnings throughout the run
- 💾 **Session persistence** — `.npz` (lossless), `.mat` (MATLAB
  parity), `.xlsx` (Gamry-DTA-style with curves + summary)
- 🔌 **Calibration wizard** — Plexon test-board (14-04-A-03-A)
  sweep with I_mon scaling validation, persists per-serial scaling
  to the prefs database
- 🔬 **POLARIS viewer** — channel map, per-capture metric tables,
  multi-channel overlays, Q_inj vs amplitude / V_d vs Q_inj
  analysis plots

## Experiments

Each experiment is implemented as an `ExperimentRunner` subclass that emits
`ExperimentEvent`s for the GUI to render. Headless / scripted runs use the
same runner classes directly — see [`run_cli.py`](run_cli.py) for examples.

| File | Purpose |
|---|---|
| [`stimtest/experiments/voltage_transient.py`](stimtest/experiments/voltage_transient.py) | VT sweep with incremental / regression / predictive (ML) strategies, voltage-compliance debouncing, water-window stopping |
| [`stimtest/experiments/short_pulsing.py`](stimtest/experiments/short_pulsing.py) | SP at fixed amplitude with periodic snapshots |
| [`stimtest/experiments/long_pulsing.py`](stimtest/experiments/long_pulsing.py) | LP with re-characterization intervals + pause windows |
| [`stimtest/experiments/progressive_stress.py`](stimtest/experiments/progressive_stress.py) | PS staircase with compliance-aware stopping |

## Metrics computed per capture

Per the IEEE NER 2025 paper conventions:

- **Charge** — `Q_ph` (per-phase, shape-aware), `Q_inj` (charge density mC/cm²),
  `Q_net` (residual after biphasic — imbalance %), `C_eff`, `C_d`
- **Voltage** — `V_d` (driving), `V_a` (access, per phase), `E_pol`
  (polarization), `E_ip` (interpulse rest potential)
- **Resistance** — `R_a` (access, per phase, on both active + return sides)
- **Damage screens** — Shannon k-value + classification, modified-Shannon
  macro/micro caps, NeurostimML probability

See [`stimtest/metrics.py`](stimtest/metrics.py) for the full implementation
and [`stimtest/session.py`](stimtest/session.py) for the `CaptureMetrics`
dataclass schema.

## Repository layout

```
StimulationTesting/
├── run_gui.py                   # PULSAR launcher
├── run_viewer.py                # POLARIS launcher
├── run_cli.py                   # scripted experiment entry point
├── pyproject.toml               # package metadata, deps, version
├── README.md                    # you are here
├── CHANGELOG.md                 # Keep-a-Changelog-style release log
├── CONTRIBUTING.md              # dev setup, test running, code style
├── LICENSE                      # MIT
│
├── stimtest/                    # main Python package (kept under legacy name)
│   ├── waveforms.py             # PulsePattern, Phase, shape generators, .pat writer
│   ├── metrics.py               # V_d, V_a, R_a, E_pol, Q_inj, Shannon, etc.
│   ├── session.py               # Session / ChannelRun / Capture / CaptureMetrics dataclasses
│   ├── persistence.py           # .npz / .mat round-trip
│   ├── gamry_export.py          # .xlsx Gamry-DTA-style export
│   ├── plotting.py              # matplotlib plot helpers (viewer + auto-save)
│   ├── damage_models.py         # Shannon + modified-Shannon caps + electrode bands
│   ├── damage_warnings.py       # pre-run damage screen + per-capture verdicts
│   ├── neurostimml.py           # RF-Partial-19 inference + model installer
│   ├── electrode_potential_history.py   # E_ret rest-potential learning store
│   ├── electrode.py             # ElectrodeArray, Configuration, ElectrodePosition
│   ├── environments.py          # PBS / mISF / cell-culture postures
│   ├── config.py                # hardware constants, defaults, coating catalog
│   ├── notifications.py         # email / SMS notifier + bug-report mailto
│   │
│   ├── gui/                     # PyQt6 GUI — every visible widget
│   │   ├── main_window.py       # MainWindow, menus, tab orchestration
│   │   ├── setup_tab.py         # Setup tab — array / environment / save path / identity
│   │   ├── experiment_tabs.py   # VT / SP / LP / PS experiment tabs (shared base)
│   │   ├── pattern_panel.py     # Pulse-pattern authoring widget
│   │   ├── pattern_preview.py   # Live preview with Desired vs Actual overlay
│   │   ├── connection_panel.py  # PlexStim + scope Initialize / Connect
│   │   ├── calibration.py       # Test-board calibration wizard
│   │   ├── combination_panel.py # Configuration kind + spacing chooser
│   │   ├── channel_selector.py  # Per-channel role grid
│   │   ├── multichannel_scope.py# Live V_mon / I_mon / E_act / E_ret scope view
│   │   ├── tracking_plot.py     # Metric-vs-time tracking (LP / PS)
│   │   ├── staircase_plot.py    # PS amplitude staircase
│   │   ├── results_tab.py       # POLARIS embedded into the main GUI
│   │   ├── viewer.py            # POLARIS standalone + ChannelMapPanel
│   │   ├── contribute_dialog.py # Help → Contribute electrode data flow
│   │   ├── device_view.py       # Array geometry preview
│   │   ├── prefs.py             # %APPDATA% JSON prefs round-trip
│   │   ├── rich.py              # Unicode subscript / unit helpers
│   │   ├── repeating_spinbox.py # Click-and-hold autorepeat spinbox
│   │   ├── usb_hotplug.py       # Cross-platform USB device add/remove signal
│   │   └── widgets.py           # ScopePlot, ChannelGrid, MetricTable, LogPane
│   │
│   ├── hardware/                # device drivers — abstract interface + 3 backends
│   │   ├── base.py              # Stimulator + Oscilloscope abstract base classes
│   │   ├── plexon.py            # Real PlexStim 2.0 (via vendored pyplexstim DLL)
│   │   ├── tektronix.py         # Real Tek scope (pyvisa, model-adaptive dialect)
│   │   ├── simulator.py         # Synthetic stim + scope for offline dev
│   │   ├── plexstim_detect.py   # Runtime SDK-presence check
│   │   ├── plexstim_lock.py     # Cross-process DLL lock (Sim-2 vs PULSAR)
│   │   └── pyplexstim/          # Vendored Plexon SDK Python wrapper + DLL
│   │
│   ├── experiments/             # ExperimentRunner subclasses
│   │   ├── base.py
│   │   ├── voltage_transient.py
│   │   ├── short_pulsing.py
│   │   ├── long_pulsing.py
│   │   └── progressive_stress.py
│   │
│   └── ml/                      # Machine-learning helpers
│       ├── qinj_model.py        # QinjPredictor (Q_inj ceiling forecast)
│       ├── ingest.py            # Append-only CSV training-set ingestion
│       └── weakness_analysis.py # Cohort-level analysis helpers
│
├── tests/                       # pytest suite (391 tests, runs in ~12 s)
│   ├── test_waveforms.py        # pulse pattern + .pat writer + charge balance
│   ├── test_metrics.py          # V_d / V_a / R_a / E_pol / Q_inj math
│   ├── test_persistence.py      # save / load round-trip (every metric field)
│   ├── test_ml_qinj.py          # QinjPredictor.load_default + feature builders
│   ├── test_voltage_transient_helpers.py  # _v_compliance_tripped debouncer
│   ├── test_tektronix_probes.py # Tek dialect / channel / EXT-trigger probes
│   ├── test_simulator.py        # Simulator + VT runner end-to-end
│   └── …
│
├── installer/                   # Windows installer build chain
│   ├── README.md                # Build docs + rename ledger
│   ├── build.py                 # End-to-end: PyInstaller → smoke test → Inno Setup
│   ├── StimulationTesting.iss   # Inno Setup script (produces PULSAR-Setup-*.exe)
│   └── StimulationTesting.spec  # PyInstaller spec
│
├── scripts/
│   ├── hardware_probe.py        # Hardware-detection CLI utility
│   └── train_qinj_model.py      # ML training script
│
├── data/                        # gitignored — session .npz files, ML pickles, etc.
└── matlab_reference/            # original MATLAB code for traceability
```

## Documentation

| Document | Purpose |
|---|---|
| [`installer/README.md`](installer/README.md) | Installer build + prerequisite detection + rename ledger |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Development setup, code style, how to run tests |
| [`CHANGELOG.md`](CHANGELOG.md) | Release history (Keep-a-Changelog format) |

## Development

```powershell
# Editable install with dev extras
pip install -e ".[dev]"

# Tests (391 of them; runs in ~12 s)
python -m pytest tests/ -q

# Tests with verbose output for a single file
python -m pytest tests/test_metrics.py -v

# Linter (optional)
ruff check stimtest/ tests/

# Run the GUI in simulator mode (no hardware required)
python run_gui.py --simulate

# Run POLARIS standalone
python run_viewer.py data/
```

The CI runs the test suite against Python 3.10 / 3.11 / 3.12 on
Ubuntu — see [`.github/workflows/tests.yml`](.github/workflows/tests.yml).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full dev-setup,
coding-style, and pull-request workflow.

Bug reports and feature requests are tracked on
[GitHub Issues](https://github.com/Bortz1234/StimulationTesting/issues).
Use the templates that pop up when you click "New issue."

## License

[MIT](LICENSE) © 2026 Christopher K. Nguyen.

## References

- Nguyen et al., *Charge-Injection Comparison of SIROF-Coated Utah
  Electrode Arrays*, IEEE NER 2025 (`IEEE_NER_Final_V2.pdf`).
- Nguyen et al., *Electrical characterization and accelerated aging
  of amorphous silicon carbide implantable encapsulation*, J. Neural
  Eng. **22** (2025) 066040.
- Cogan, *Neural Stimulation and Recording Electrodes*, Annu. Rev.
  Biomed. Eng. 2008.
- Merrill, Bikson, Jefferys, *Electrical stimulation of excitable
  tissue: design of efficacious and safe protocols*, J. Neurosci.
  Methods 141 (2005).
- Li, Chen, Liu et al., *Random-forest classifier for safe-stim
  prediction*, 2024 (NeurostimML / RF-Partial-19).
