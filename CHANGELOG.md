# Changelog

All notable changes to PULSAR / POLARIS are recorded here. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

The Python package is named `stimtest` and the on-disk install path,
launcher .exe filenames, GitHub repo URL, and `%APPDATA%` cache directory
all retain the legacy "StimulationTesting" name for back-compat. See the
[rename ledger](installer/README.md#rename-ledger) for the full
what-is-and-isn't-renamed list.

## [Unreleased]

### Added

- **Plugin-host architecture for login profiles.**  The
  `stimtest.gui.admin` module is now an extension host: external
  packages whose distribution name starts with `stimtest_` are
  auto-discovered at MainWindow startup (via
  `importlib.metadata.distributions()`) and can register additional
  login profiles + restricted pulse shapes by calling
  `register_extension_profile(name=…, password_hash=…, shapes=…)` as
  an import side-effect.  Built-in profiles unchanged: anonymous
  (default) + Admin (Manage Custom Catalog access).  The login
  dialog adapts automatically — password-only when no extensions are
  installed, username+password when one or more are.  22 new tests
  in `tests/test_admin_extension_api.py` pin the contract.
- `CITATION.cff` — machine-readable academic citation metadata,
  rendered by GitHub as "Cite this repository" and consumed by
  Zenodo / JOSS.
- Top-level `README.md` rewritten with badges, table of contents,
  accurate file-tree, quick-start, installation, and explicit
  documentation links.
- `CONTRIBUTING.md` — dev-environment setup, test workflow, coding
  style, PR checklist, hardware notes.
- `.github/ISSUE_TEMPLATE/` — bug-report, feature-request, and
  question templates.
- `.github/pull_request_template.md`.
- 59 new tests across three new files (`tests/test_ml_qinj.py`,
  `tests/test_voltage_transient_helpers.py`,
  `tests/test_tektronix_probes.py`). Suite total: **397** (was 332).

### Changed

- **User-facing brand renamed**: GUI is now **PULSAR**, viewer is now
  **POLARIS**. All window titles, About-dialog text, installer
  Start-Menu / desktop shortcuts, installer .exe filename
  (`PULSAR-Setup-<version>.exe`), CLI `--help` descriptions, email /
  SMS subjects, bug-report subject, and README headers updated to
  the new branding. Legacy filesystem / repo identifiers retained
  for back-compat — see the rename ledger.
- Tektronix dialect-fallback regex (`stimtest/hardware/tektronix.py`)
  extended to accept the trailing suffix letter on MSO/MDO/DPO
  models (e.g. `MSO4054B`, `DPO4054B`). A test in
  `tests/test_tektronix_probes.py` pins the new behaviour.

### Fixed

- **Audit #1**: `launch()` no longer collapses `simulate=False` to
  `True`. The "Use simulator" checkbox now correctly defaults to
  unchecked on real-hardware launches.
- **Audit #2**: `Run → Calibrate` slot now reads hardware handles
  from `self.conn.stim` / `self.conn.scope` (the actual location)
  instead of the never-set `self._stim` / `self._scope`. The
  Calibrate menu action is intentionally `setVisible(False)` in
  this build until the wizard UI is GUI-ready.
- **Audit #3**: NeurostimML `encode_waveform_type` now matches the
  actual shape constants (`SHAPE_EXP_DECAY`,
  `SHAPE_EXP_INCREASING`) instead of the substring `"capacit"`.
  Cap-coupled biphasics are now correctly classified.
- **Audit #4**: VT predictive ramp's `_predict_target_ml` was
  silently degrading to regression on every call because of two
  stacked AttributeErrors swallowed by `try/except`
  (`Configuration` passed where `ChannelRun` expected;
  `result.predicted_q_inj_mc_per_cm2` should have been
  `result.q_inj_predicted_mc_per_cm2`). Both fixed.
- **Audit #5**: `QinjPredictor.load_default()` added, frozen-build-
  aware via `sys._MEIPASS` resolution. `DEFAULT_DATASET_PATH` and
  `DEFAULT_MODEL_PATH` are now absolute paths (were
  process-CWD-relative). Predictive mode hidden from the GUI for
  now via the strategy combo's `addItems`.
- **Audit #6**: `load_session_npz` now restores all 9 previously-
  dropped `CaptureMetrics` fields (Shannon k-value, damage
  classification / criteria / band / level, NeurostimML verdict /
  probability, return pre / post-pulse potentials). Closes a
  silent save → reload data loss.
- **Audit #7**: `MainWindow._on_save_path_changed` now forwards the
  new path to `ResultsTab.set_save_dir`, so the embedded POLARIS
  viewer re-indexes when the user changes the save folder.
- **Audit #9**, **#10**, **#11**: Hidden three UI promises whose
  runner-side support isn't built yet — the Pause button (all
  tabs), the SP pre/post-characterization rows, and the LP
  pause-after-snapshot options. Widget construction + prefs
  round-trip preserved so re-exposing each is a one-line restore.
- **Audit #13**: Tracking-plot prefs now round-trip via
  `_BaseExperimentTab.current_prefs` / `restore_prefs`.
- **Audit #14**: Viewer's `_on_grid_toggled` fallback path now
  calls `_on_tree_item` directly (was emitting `tree.itemClicked`,
  which had no listener).
- **Audit #15**: `ChannelMapPanel._refresh` now honours the viewer's
  `_show_grid` toggle instead of hardcoding gridlines.
- **Audit #16**: View-menu state (font preset, zoom factor, gridlines
  toggle) now persisted in prefs.
- **Audit #18**: Speedbumps `bump_count` is now respected by both
  `_shape_duty` and `shape_breakpoints` (was hardcoded N=2). N=3
  pattern-preview accuracy went from ~61 % to ~99.98 %.
- **Audit #19**: `ElectrodeArray.cable_map` and `layout` now save /
  load through the .npz path. Hex / triangular arrays no longer
  silently revert to `"rect"`.
- **Audit #20**: Voltage-compliance check now requires 3 consecutive
  samples above the rail before tripping. Single noisy spikes no
  longer abort otherwise-good ramp steps.
- **Audit #24**: `_acquire_one_capture` now assigns `cap.i_mon_ua`
  (was dropped, so the multichan scope silently skipped the I_mon
  trace on single-shot acquisitions).
- **Audit #25**: Viewer's metric tables now use a `_fmt_or_dash`
  helper that turns NaN / non-finite values into `"—"` instead of
  the literal `"nan nC"`.
- **Audit #28-#33**: Installer rebranded — `AppName`,
  `ViewerDisplayName`, `OutputBaseFilename`, Start-Menu / desktop
  shortcut labels, README header, [Icons] block. URLs filled in.
  Missing-icon path now emits a compile-time warning instead of
  silently falling back to the default Inno icon.
- **Audit #32**: `installer/build.py` now asserts
  `pyproject.toml`'s `version =` matches `stimtest.__version__`
  before invoking PyInstaller. Aborts with a clear message on
  drift.

### Removed

- `metrics.access_indices_for_phase` — dead backward-compat shim
  that only raised `NotImplementedError`.
- Unused imports in `metrics.py` (`Phase`), `plotting.py`
  (`Iterable`, `Sequence`).
- Dead `cls_str` read in `damage_warnings.py`.
- Two dead `last_good_amp = amp` writes in `voltage_transient.py`.

### Documentation

- Top-level `README.md`, `CONTRIBUTING.md`, `CHANGELOG.md`,
  `installer/README.md` rename ledger, GitHub Issue / PR
  templates.
- Every input widget across the GUI (104 of them) carries a
  `setToolTip` description.
- The pattern-preview header now includes an Actual-vs-Desired
  accuracy percentage and a charge-balance imbalance percentage.

## [0.2.0] — pre-history

Initial Python port of the MATLAB `PlexStimTek` characterization
suite. Full GUI (VT / SP / LP / PS / Results), simulator backend,
real PlexStim 2.0 + Tektronix TBS2204B drivers, .npz persistence,
Gamry-DTA-style .xlsx export, Shannon damage screen, NeurostimML
(Li et al. 2024 RF-Partial-19) inference, calibration wizard
(behind a setVisible gate until GUI-ready).

[Unreleased]: https://github.com/Bortz1234/StimulationTesting/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/Bortz1234/StimulationTesting/releases/tag/v0.2.0
