# Contributing to PULSAR / POLARIS

Thanks for working on this. The codebase is small enough that conventions
are mostly enforced by review rather than tooling, but the items below
keep changes consistent and reviewable.

> The user-facing brand is **PULSAR** (GUI) and **POLARIS** (viewer); the
> Python package is `stimtest` and the repo / installer / launcher
> filenames retain the legacy `StimulationTesting` name. See the
> [rename ledger](installer/README.md#rename-ledger) for the full
> what-is-and-isn't-renamed list.

## Table of contents

- [Dev environment setup](#dev-environment-setup)
- [Running the test suite](#running-the-test-suite)
- [Coding style](#coding-style)
- [Pull-request checklist](#pull-request-checklist)
- [Working with hardware](#working-with-hardware)
- [Where to find things](#where-to-find-things)
- [Reporting issues](#reporting-issues)

## Dev environment setup

```powershell
git clone https://github.com/Bortz1234/StimulationTesting.git
cd StimulationTesting
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

The `dev` extra brings pytest, pytest-qt, and ruff. The `build` extra
adds PyInstaller — only needed if you're producing a Windows installer.

Python 3.10+ is required; the CI matrix is 3.10 / 3.11 / 3.12.

## Running the test suite

```powershell
# Full suite (~12 s, 391 tests)
python -m pytest tests/ -q

# One file, verbose
python -m pytest tests/test_metrics.py -v

# A single test
python -m pytest tests/test_metrics.py::test_v_d_against_known_trace -v

# Last-failed only
python -m pytest tests/ --lf
```

The Qt-based tests use the offscreen platform plugin
(`QT_QPA_PLATFORM=offscreen`), so no display is needed; CI runs the
same way on a headless Ubuntu runner.

## Coding style

- **PEP 8** for layout (line length 79–100 is fine; no hard limit).
- **PEP 257** docstrings on public functions and classes. Multi-line
  docstrings preferred — the project relies on them as inline
  documentation since there's no Sphinx build yet.
- **Type hints** on every new function signature. Use `from __future__
  import annotations` at the top of modules that need forward refs.
- **`from __future__ import annotations`** is the standard top of every
  Python file in this repo.
- **No emojis in code** unless the user explicitly requested them.
- **Explain the *why* in comments**, not the *what*. Most existing
  modules have multi-paragraph comments at the site of subtle
  decisions (charge-balance solver, dialect probing, calibration
  flow); follow that template.
- **Imports** sorted top → bottom: stdlib, third-party, then `from
  .module import Foo` first-party. No automatic isort enforced.
- **Tooltips for every widget** — see existing widgets in
  `stimtest/gui/` for the conventions (multi-line HTML, units stated
  explicitly, couplings called out).

You can run `ruff check stimtest/ tests/` for a light lint pass. We
don't gate PRs on a clean ruff today, but new code should not add
warnings.

## Pull-request checklist

Before opening a PR:

- [ ] `python -m pytest tests/ -q` passes.
- [ ] If you added a feature, added at least one test for it.
- [ ] If you fixed a bug, added a regression test that fails before
      and passes after your fix.
- [ ] Public functions / classes have docstrings.
- [ ] If you touched the installer (`installer/*.iss`, `installer/*.spec`,
      `installer/build.py`), the rename ledger in
      `installer/README.md` still reflects reality.
- [ ] If you bumped `__version__` in `stimtest/__init__.py`, also bumped
      the matching `version =` in `pyproject.toml` (the version-drift
      guard in `installer/build.py` aborts the build otherwise).
- [ ] Added a `CHANGELOG.md` entry under `## [Unreleased]`.
- [ ] PR description matches the template (see
      `.github/pull_request_template.md`).

## Working with hardware

Most development can happen in simulator mode:

```powershell
python run_gui.py --simulate
```

If you have real hardware:

- **PlexStim** — install the Plexon PlexStim 2.0 SDK (the GUI installer
  prompts you on first launch; `scripts/hardware_probe.py` is a
  standalone diagnostic). Close Plexon's Sim-2 application before
  running PULSAR — it holds an exclusive USB lock that blocks the
  SDK.
- **Tektronix scope** — pyvisa-py works for most USB-TMC scopes
  out-of-the-box; NI-VISA / TekVISA give better performance and
  broader instrument support but aren't required.

The calibration wizard (Run → Calibrate, currently hidden behind a
`setVisible(False)` flip in this build — see audit history) validates
PlexStim scaling against the Plexon test-board (Plexon 14-04-A-03-A
via the black Omnetics stimulation cable 14-03-A-03).

## Where to find things

- **Adding a new pulse shape?** → `stimtest/waveforms.py` (`shape_breakpoints`,
  `_shape_duty`, plus the `SHAPE_*` constant list).
- **Adding a new metric?** → `stimtest/metrics.py` for the math,
  `stimtest/session.py:CaptureMetrics` for the schema. Add a save / load
  round-trip test in `tests/test_persistence.py`.
- **Adding a new experiment mode?** → subclass `ExperimentRunner` in
  `stimtest/experiments/base.py`, add a tab in
  `stimtest/gui/experiment_tabs.py`, wire into `MainWindow._exp_tab_by_code`.
- **Adding scope support for a new vendor?** → implement the
  `Oscilloscope` abstract interface in `stimtest/hardware/base.py`. The
  `simulator.py` and `tektronix.py` files are the reference implementations.
- **Adding a new GUI panel?** → see `stimtest/gui/widgets.py` for the
  shared widgets (ScopePlot, MetricTable, ChannelGrid, LogPane); follow
  the existing tab pattern in `experiment_tabs.py`.
- **Bumping the version?** → edit `__version__` in
  `stimtest/__init__.py` AND `version =` in `pyproject.toml`. The
  installer's `build.py` aborts if they disagree.

## Reporting issues

Bug reports and feature requests are tracked on
[GitHub Issues](https://github.com/Bortz1234/StimulationTesting/issues).
Use the templates that pop up when you click "New issue." If you have
hardware diagnostics to share, run `python scripts/hardware_probe.py`
and paste its output into the issue body — it captures SDK + VISA
state in one place.
