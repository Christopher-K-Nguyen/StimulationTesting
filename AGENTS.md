# AGENTS.md — codebase guide for Codex agents

**Purpose.** This file is the canonical hand-off document for any Codex
Code instance picking up work on this repo on a fresh machine. It
captures the *living* conventions, the recent design decisions, and the
gotchas that aren't obvious from the public-facing `README.md`. Read this
**before** modifying any file under `stimtest/`.

If `README.md` is the user-facing pitch, this is the engineering
contract.

---

## 1. What this project is, in one paragraph

PyQt6 desktop app that drives a **Plexon PlexStim 2.0** stimulator and a
**Tektronix TBS-series** oscilloscope to characterize neural-stim
microelectrode arrays. Brand name: **PULSAR** (GUI),
**POLARIS** (companion viewer). The Python package and on-disk install
path keep the legacy name `stimtest` / `StimulationTesting` for
back-compat — never rename them. Public README has the user pitch; this
file is for agents.

Entry points:
- `run_gui.py` → `stimtest.gui.main_window.launch()` → `MainWindow`
- `run_viewer.py` → standalone POLARIS
- `installer/build.py` → end-to-end Windows installer build

---

## 2. Quick orientation map

```
stimtest/
  config.py              ← single source of truth for tunable constants
  waveforms.py           ← PulsePattern, Phase, shape primitives
  session.py             ← Session, ChannelRun, Capture, ExperimentResult
  metrics.py             ← MATLAB getAccess.m / getDriving.m ports
  persistence.py         ← .npz / .xlsx round-trip
  readback_calibration.py← per-stimulator I_mon / V_mon scaling fit
  hardware/
    base.py              ← Stimulator / Oscilloscope abstract bases
                            + shared fmt_elapsed (MATLAB getEndTime port)
    plexon.py            ← real PlexStim driver
    tektronix.py         ← real Tek scope driver
    tektronix_models.py  ← per-model SCPI dialect + bandwidth library
    simulator.py         ← fake stim + scope for offline dev
    pyplexstim/          ← vendored Plexon SDK Python wrapper + DLLs
  experiments/
    base.py              ← ExperimentRunner; shared trigger / V/div helpers
    voltage_transient.py ← VT — multi-channel sweep
    progressive_stress.py← PS — single-channel staircase
    short_pulsing.py     ← SP — single amp, single channel
    continuous_pulsing.py← CP — SP subclass, UNBOUNDED (manual Stop = the
                            normal end → result aborted=False); GUI tab
                            ContinuousPulsingTab subclasses ShortPulsingTab
                            (Duration row → "Snapshot every" cadence)
    long_pulsing.py      ← LP — single amp, periodic re-characterization
  gui/
    main_window.py
    setup_tab.py
    experiment_tabs.py   ← VT/PS/SP/LP tabs (share _BaseExperimentTab)
    pattern_panel.py     ← pulse-shape authoring widget
    pattern_preview.py   ← live waveform preview
    connection_panel.py  ← hardware init + scope/stim cmd_logger wiring
    calibration.py       ← test-board calibration wizard (long file)
    viewer.py            ← POLARIS embedded panel (matplotlib — LAZY)
    results_tab.py       ← thin shell around ViewerPanel (LAZY)
    camera.py            ← CameraService singleton + Connector + StreamPane (QtMultimedia — LAZY)
    widgets.py           ← LogPane + reusable bits
    admin.py             ← admin password + custom catalog
```

---

## 3. Hardware model — what you must know

### Stimulator (PlexStim 2.0)

- **Two scaling presets**, queried at connect and stored in
  `stim.info.imon_scaling_v_per_ua` and `vmon_scaling_v_per_v`:
  - **Default** preset: `I_mon = 2.5 mV/µA`, `V_mon = 0.25 V/V`
  - **NIL** preset: `I_mon = 1.0 mV/µA`, `V_mon = 1.0 V/V`
- The MATLAB-derived `imon_trigger_level` / `imon_vertical_scale`
  formulas were originally written assuming **1 mV/µA**. They still
  work on either device because the I_mon channel gets a **20 MHz
  bandwidth limit** at connect (`set_channel_bandwidth_for_purpose`)
  which keeps the realised peak clean enough to clear the threshold.
  **Never set CHx:BANdwidth FULl** — `set_channel_bandwidth_for_purpose`
  has a last-line guard rejecting `FULL` / `OFF` for this reason.
- DLL is **vendored** at `stimtest/hardware/pyplexstim/bin/`:
  `PlexStim.dll` (32-bit) and `PlexStim64.dll` (64-bit). Wrapper picks
  the right one via `platform.architecture()`. Bundle ships both.
- **Plexon's Sim-2 GUI app holds an exclusive USB lock.** If
  `PS_InitAllStim` returns "No Plexon Stimulator is detected," tell the
  user to close Sim-2 before reconnecting.
- `PS_LoadArbPattern` is ALWAYS used (even for biphasic) — never
  `PS_SetRectParam2`, which the firmware rejects for asymmetric phases
  or custom discharge delays.
- PlexStim DLL is **not thread-safe**. Single producer only.
- **Cable pinout is identity** for current devices (UTD MEA, Blackrock
  UEA, MicroProbes FMA). NeuroNexus future may need `cable_map`. No
  runtime channel translation today.
- **Do NOT turn off the stimulator between steps.** Only
  `stop_channel`; never `reinit` / power-cycle unless explicitly
  required (e.g. before a multipolar configuration with overlapping
  return sets).

### Oscilloscope (Tektronix TBS-series)

- **Connect-time SCPI flow** (`tektronix.py:open()`) is six labelled
  steps: pick resource → IDN? → probe dialect → defaults (HEADer OFF,
  VERBose ON) → read horizontal divisions → configure data transfer
  protocol. **Don't add per-channel scale / trigger / record-length
  writes here** — Setup / Experiment / Calibration tabs each set those
  per run. Anything overwritten by the tabs is pure log noise during
  connect.
- **Horizontal divisions count is model-dependent**: TBS2000B = 15,
  most other families = 10. Queried at open and stored in
  `self._n_horiz_divs`. All layout math must multiply by this.
- **Acquisition state: NEVER cycle mid-sweep.** The scope is armed
  once at setup (`set_trigger` writes `ACQuire:STAte RUN` last). Per
  capture, `capture_single_sequence` only re-sends `ACQuire:STAte RUN`
  (idempotent) and polls `NUMACq?` — **no `STOPAfter`, no `STATE
  STOP`**. Matches MATLAB `getWaveform.m` semantics.
- **NUMACq poll has a soft timeout**: on missing-frame timeout we log
  the shortfall and `CURVe?` the latest averaged frame anyway, mirroring
  MATLAB's never-raise behaviour. Errors during polling are throttled
  (first + every 50th logged, full count surfaced in the summary line).
- **Time axis is in microseconds** (`acq.time_us`). The `_read_channel`
  cross-checks `XZEro` against `PT_Off` because some TBS firmware
  reports `XZEro = 0` even with a non-zero horizontal position. Method
  A (XZEro) vs Method B (PT_Off) is logged per capture.
- **Batch preamble query** (`WFMOutpre?`) parsed once, with explicit
  named-field error if firmware omits any of `YMULT / YOFF / YZERO /
  XINCR / XZERO`. Falls back to per-field queries.
- **Horizontal (trigger) position is written at FULL precision — NOT
  snapped to a 10% grid** (operator: "forget about my requirement of
  trigger percentage rounded"). The old floor-to-nearest-10% defeated the
  asymmetric pre/post framing (a wide pulse needs only a few-% leading
  offset, which the floor collapsed to 0% → leading edge jammed against
  the trigger). `set_horizontal_position` now applies the clamped exact
  value and caches it verbatim in `_expected_horiz_position_pct` (Method P
  t=0). Don't re-add the `int(pct/10)*10` snap.
- Always `DELay:MODe OFF` before any `HORizontal:POSition` write —
  TBS firmware otherwise uses `DELay:TIMe` as the trigger offset and
  silently ignores `POSition`.
- Resource cache is cleared on `close()` so reconnect after USB
  hot-unplug re-enumerates.
- **Vertical divisions are PER-SERIES, not a fixed family
  constant.** There is no `VERTical:DIVisions?` SCPI query (unlike
  `HORizontal:DIVisions?` which we DO query and store in
  `self._n_horiz_divs`).  The count is a fixed PHYSICAL DISPLAY
  property of each Tek series, stored in `TekSeriesSpec.n_vert_divs`
  (registry in `hardware/tektronix_models.py`):

  | Series | Horizontal | Vertical | Notes |
  |---|---|---|---|
  | **TBS2000 / TBS2000B / TBS2000C** | **15** | **10** | redesigned 2-series display |
  | TBS1000 / TBS1000B / TBS1000C | 10 | 8 | classic Tek layout |
  | TDS200, TDS1000B/C, TDS2000B/C | 10 | 8 | classic Tek layout |
  | TPS2000 | 10 | 8 | isolated 4-channel |

  At connect time `TektronixOscilloscope.open()` step 5/6 queries
  the SCPI horizontal count AND looks up `n_vert_divs` from the
  per-series spec, setting `self._n_vert_divs` /
  `self._half_vert_divs`.  Don't hardcode 8 anywhere downstream —
  use these attributes.

  **Critical impact**: the MATLAB original baked in 4.0 for
  `MAX_FACTOR` because it targeted the TBS1104B (8 vert divs).
  Running the same code on a TBS2204B (10 vert divs) without
  adapting would treat 25 % of the usable screen as off-limits.
  The runner now computes `MAX_FACTOR = scope._half_vert_divs - 0.05`
  so a TBS2204B gets **4.95** and a TBS1104B gets **3.95** (operator
  request — was −0.1 / 4.9 / 3.9; the tighter 0.05-div leave-room lets
  the waveform use more of the screen before tripping out-of-view).
  `channel_in_view` / `channel_clip_sides` default `margin_divs` is
  3.95 to match. (Older notes/diagnostics may still say 3.9/4.9.)
- **In-view check / iterative fit-the-view loop**
  (`channel_in_view`, `channel_is_clipped`,
  `set_channel_scale_and_position_for_range`).
  Port of MATLAB `getWaveform2.m` lines 290-465. The runner captures
  once, then for each voltage channel decides between two paths:

  1. **CLIP detected** (`channel_is_clipped` — observed range sits
     within 5 % of the `±4·vpd` rail around `vertPos =
     -pos_divs * vpd`): the trace is saturating the ADC, the true
     peak is HIGHER than the captured data shows. Sizing the new
     V/div from the observed range (which equals the rail) would
     produce the same V/div as before and never expand. **COARSE
     PATH**: read current `CHx:SCAle?`, multiply by
     `CLIP_COARSE_FACTOR` (2.5×, ≈ 2 stops on the 1-2-5 grid),
     write back, set position to 0, re-capture. Next iteration
     sees the un-clipped data and fine-fits.
  2. **Not clipped + out-of-view** (`channel_in_view` returns
     False with `MAX_FACTOR = 3.9` divs of visible budget): the
     trace fits ON the rails but extends beyond the 3.9-div margin
     — fine-fit via `set_channel_scale_and_position_for_range`
     (port of `setFineScalePos2.m`, range/(2·divs) + mean-offset
     position) and re-capture.
  3. **Not clipped + in-view**: converged, no rescale, loop exits.

  Bounded at 3 captures total (matches MATLAB
  `fineScale_count > 2 → done`). `channel_in_view` and
  `channel_is_clipped` both return **True / False / None** (last on
  SCPI error or malformed reply — caller treats None as "leave
  alone").

  **I_mon is INCLUDED in this loop** (was historically excluded on
  the assumption that the analytical
  `update_imon_vertical_scale(amp)` always sized it right; real
  hardware proved otherwise — under-sized I_mon scales clip at the
  ±4-div rail and the analytical formula has no way to know). The
  clip detector catches it and the coarse-step path expands.

  See `experiments/voltage_transient.py:_one_capture` block 2b for
  the canonical use; the per-attempt diagnostic log lines emitted
  by that loop show clip/in-view/result per role per attempt and
  land in the session `.txt` log for post-hoc analysis.

---

## 4. Calibration tab — current contract

`gui/calibration.py` is the largest file in the repo (~3000 lines) and
the most heavily iterated. Key invariants:

- **Acquisition is hardcoded**: AVERAGE mode, NUMAVg = 64. The
  "Oscilloscope setup" UI group was removed. V_mon / I_mon channel
  choices come from the scope's `channel_aliases`. Constants:
  `CAL_ACQ_MODE`, `CAL_N_AVERAGES`.
- **One-time setup discard**: a single throwaway acquisition fires
  right before the main sweep loop to absorb the
  settings-change averager transient. Per-amplitude captures
  themselves take exactly **one** averaged frame (not two — that
  doubled sweep time).
- **Per-channel retry loop**: if the joint R-fit deviates from
  `DEFAULT_LOAD_OHM` by more than 10 %, the channel's amplitude sweep
  is re-run up to 2 more times (3 total attempts). Final attempt is
  accepted. See `_R_TOLERANCE_PCT`, `_R_RETRY_MAX`.
- **V_mon clipped-equilibrium guard in `_capture_one_amplitude`** (an
  adversarial review found the historical R-retry trips were often THIS,
  not noise): the test-board V_mon is cathodic-heavy (|v_min| ≈
  (R + W/C)/R × v_max — ~3.1× at 50 µs phases), and at POSition 0 the
  rail truncated the observed half-range until `adapt_channel_scale`
  settled (ideal == current) with the cathodic peak STILL railed — the
  `_clipped` 5 %-of-samples doubling never fired (< 5 % rail dwell), and
  the I·R edge step biased −13…−18 %. Three-part fix: (1) ONE-SIDED rail
  detection via the position-aware `channel_is_clipped` → ×2 doubling +
  `force_grow=True` (rail reads are extrapolations; the fits-now veto
  must not block the escape); (2) faithful captures get the asymmetric
  excursion midpoint POSITION-centred at the current scale (POSition is
  ADC-centering only — reconstructed volts, edge-step extraction, and
  the per-capture offset are unaffected), with `scale_changed = True` so
  the stored waveform is re-captured centred; (3) **I_mon's `force_grow`
  keys on the RAIL check ONLY — never on `_clipped`**, which
  false-positives on I_mon's square plateaus (the whole phase width sits
  at the array extremes by definition); keying it there would
  reintroduce the per-step I_mon coarsening the fits-now gate was
  verified to remove. Tests:
  `tests/test_calibration_vmon_clip_equilibrium.py`.
- **R extraction uses the access-voltage method** from
  `metrics.access_voltage_and_resistance` (MATLAB `getAccess.m` port),
  with a **post-edge linear extrapolation** to the |dV/dt| peak. The
  metrics localizer alone overshoots by ~30 % because it lands in the
  cap-ramp region; we use it only to find the post-edge clean window,
  then linear-fit V_mon there and extrapolate back to the edge moment
  to get pure IR.
- **Per-capture baseline** (`_per_capture_baseline`) is a MAD-clipped
  mean of pre-trigger samples (`t < -1 µs`). Falls back to the first
  10 % of the trace if the time mask yields < 8 samples; falls back to
  `0.0` (not the global idle baseline) on any other failure — propagating
  a bad global value across every channel is a known antipattern.
- **`I_mon Gain (a)` column is misleadingly named**: it's actually
  `R_actual / R_nominal` (the fit slope of V_mon-derived current vs.
  programmed current under nominal `DEFAULT_LOAD_OHM`). Users have
  been told. Do not "fix" it unless explicitly asked.
- **Pass / fail criterion** is now just *"finite, positive R_load AND
  C_load fit"*. RMSD-based gates and the `ACCEPTANCE_PCT` /
  `MODEL_RMSD_LIMIT_MV` constants are gone. RMSD columns are hidden
  from the table (still saved in the JSON payload for forensic use).
- **Results table column order** (6 cols, NOT 8):
  ```
  Channel | V_mon Offset (mV) | R_load Fit (Ω) | C_load Fit (pF)
        | I_mon Gain (a) | I_mon Offset (b, µA)
  ```
- **Plot title is a real `QLabel`**, not pyqtgraph's PlotItem title.
  pyqtgraph's title-row visibility is flaky across versions; we render
  the title above the PlotWidget via the layout. Color uses the Qt
  palette (`palette().windowText()`) so it's visible on dark and light
  themes.
- **Capture-time plot updates** use `setData` then a forced
  `repaint() / update()` because the sweep blocks the GUI thread and
  natural event-loop repaints would lag many seconds.

---

## 5. Experiment runners — convention checklist

Every experiment runner in `experiments/*.py` must, in this order:

1. `apply_default_scope_view(pattern, amp_ua, reason, is_multipolar=…,
   environment_short=…)` at the start of each new channel (VT loops
   over channels; PS/SP/LP are single-channel, so once at run start).
   The two new kwargs select the right MATLAB V/div from the full
   4-case decision tree of `setOscillocopeView.m` lines 300-317:
   - phase1 ≥ 100 µs + MP: **1.0 V/div**
   - phase1 ≥ 100 µs + multipolar: **2.0 V/div**
   - phase1 < 100 µs + animal env: **2.0 V/div**
   - phase1 < 100 µs + MP non-animal: **0.2 V/div**
   - phase1 < 100 µs + multipolar non-animal: **0.5 V/div**

   The picked V/div + `POSition 0` is written to **every voltage
   channel** the scope is mapped to (V_mon, E_act, E_ret) — not just
   V_mon. Without this, E_act / E_ret carry the previous channel's
   fine-scaler-converged scale and the new channel's first capture
   clips. Matches MATLAB's `for groupNum = 1:numOfChannels` loop.
2. Per amplitude (VT, PS): `update_imon_vertical_scale(amp_ua)` and
   `update_imon_trigger_level(amp_ua_signed, phase_width_us)` BEFORE
   the capture call. Use `pattern.phases[0]` (NOT `excitation_phase`)
   for the amplitude / width — the scope trigger fires on whichever
   phase comes first in time, and the two diverge on anodic-first or
   triphasic patterns.
3. NEVER cycle `ACQuire:STOPAfter` / `STATE STOP/RUN` directly.
   `capture_while_running` / `capture_single_sequence` handle that.
4. Match every `start_channel` / `start_all` with a `stop_channel` in
   a `finally`.
5. **Three-state channel handling at every `load_channel(active,
   pattern)` call** — call `self.load_zero_unused_channels(pattern,
   config)` right after loading the active. Port of MATLAB
   `setPattern.m` Zero Current block:
   - **active** (`config.active`): real pattern.
   - **returns** (`config.returns`): left UNLOADED — passive sink.
     Loading anything (even zero) makes returns active drivers and
     breaks the multipolar config.
   - **unused** (everything else): zero-amplitude same-duration copy
     (`pattern.scaled(0.0)`) + `set_repetitions(ch, 0)`, so each
     unused channel TICKS in cadence with the active pulse cycle
     but delivers no current.

   The helper is a no-op for CG (`Configuration.enumerate_combinations`
   builds `returns = tuple(every-other-channel)`, so unused is empty)
   — matches MATLAB's explicit `~isCG` skip without a special-case
   branch.
6. **Stim runs continuously inside ONE `_one_capture` (VT) or
   amplitude step (PS).** The capture + iterative in-view re-capture
   loop must all see live pulses, so the runner wraps the whole
   capture cycle in ONE `try / finally` and the `stop_channel` fires
   ONCE on the way out. Stopping between captures (the previous bug)
   makes every re-capture acquire noise floor — the fine-scaler then
   sizes V/div for noise and the V_mon trace clips to the noise
   envelope regardless of the programmed amplitude.
7. **Stop / restart between amplitude steps** (VT, PS) per MATLAB
   `runProgressiveStress.m` line 371: stim is on while capturing,
   stop before the next iteration's `load_channel`, restart with
   `start_all` after the new pattern is loaded. Within a step the
   stim is continuous across the iterative re-captures (see #6).

   **Use `stop_all` (= PS_StopStimAllChannels), NOT
   `stop_channel(active)`.** Matches MATLAB `stopStimulation()`.
   Critical: every `start_all` brings up the active channel **and**
   the unused zero-amplitude channels we loaded via
   `load_zero_unused_channels`. Stopping just the active leaves
   the unused channels ticking their zero patterns through the
   `load_channel` window — load takes 50-200 ms for an arb
   pattern, and the unused channels need to be quieted too before
   the next iteration's `load_zero_unused_channels` reloads them.

   **The stop must be EXPLICIT right before each `load_channel`,
   not implicit via the previous iteration's `finally`.** Pattern:

   ```python
   try:
       self.stim.stop_all()    # ← explicit, idempotent
   except Exception:
       pass
   self.stim.set_monitor_channel(active)
   self.stim.load_channel(active, pattern)         # ← slow (50-200 ms)
   self.stim.set_repetitions(active, 0)
   self.load_zero_unused_channels(pattern, config) # ← also slow
   self.stim.start_all()
   ```

   Reason: a `finally`-only stop is fragile — if the previous
   iteration's finally was bypassed (early return on a stim-
   program error, an aborted re-capture, an exception escaping
   to the outer try), the next `load_channel` lands on a still-
   running stim. The user-spec quote: *"It takes time to load
   the pattern, so it would be better to stop before stimulating
   again with a new pattern."*
8. **SP / LP keep stim running for the entire run** — load once,
   `start_all` once, `capture_while_running` per snapshot, stop only
   at end or user-initiated pause (LP also stops + restarts around
   `_characterize()` and re-loads the zero-unused-channel patterns
   on the way back so unused channels resume their cadence).
9. Use `_v_compliance_tripped(min_consecutive=3)` from
   `voltage_transient.py` for compliance checks — single-sample
   threshold checks trip on noise transients.
10. Long Pulsing: after `_characterize()` returns, **check
    `sub_result.aborted`** and break out if true. The user can abort
    during a re-characterization; the outer loop must respect it.
    `_characterize()` returns `bool` (aborted) for this reason. After
    resume, call `apply_default_scope_view` again to restore trigger /
    V_div that the sub-VT clobbered.
11. Long Pulsing snapshot captures: set
    `policy.trim_snapshot_arrays=True` (default) so the raw V_mon /
    I_mon / E_act / E_ret arrays are dropped from `cap` after metrics
    are extracted. Otherwise a 30 min run leaks ~120 MB of stale
    waveforms. Characterization captures are never trimmed.
12. **The per-capture rescale loop is the SHARED
    `ExperimentRunner.rescale_to_fit` (base.py) — used by ALL FOUR
    runners (VT / PS / SP / LP), not just VT** (operator: "I want this
    same coarse/fine scaling (and positioning) for the other
    experiments").  The full fit-the-view machinery — clip detection,
    in-view check, percentile trim + stale-frame reject, baseline-centred
    E_ret/E_act positioning, asymmetric V_mon/I_mon positioning, the
    fits-now no-coarsen gate, final in-view verification + per-attempt
    diagnostics — lives in that ONE method.
    Each runner passes its own `recapture` callback: VT =
    `settle_one_acquisition` + `single_capture` (gotcha #40); PS/SP/LP =
    `capture_while_running(wait_s=navg/rate)` (its sleep IS the
    fresh-frame settle for that path).  On a converged stationary signal
    the loop exits after ONE pass with zero writes and zero re-captures,
    so calling it per snapshot (SP/LP cadence) costs nothing extra.
    `_RESCALE_TRIM_PCT` / `_RESCALE_STALE_FACTOR` also live in base.py
    now (re-exported from voltage_transient for back-compat).  **Don't
    re-add a one-shot `adapt_channel_scale` call in a runner** — route
    through `rescale_to_fit` so scaling AND positioning stay uniform.
    Tests: `tests/test_shared_rescale_loop.py`.

    Inside the loop, scaling uses `scope.adapt_channel_scale`
    — the SAME calibration-proven primitive — rather than a
    homegrown loop.  Per user feedback ("look at how the
    calibration is changing coarse vertical scales"), the
    earlier hand-rolled clip / in-view / under-utilized logic
    was replaced with a per-role call to
    `self.scope.adapt_channel_scale(ch, v_min, v_max, divs=...,
    shrink_stable_count=1)` — same call shape `calibration.py`
    line 2017 uses.

    **The fits-now no-coarsen gate + `force_grow` (operator: "the third
    waveform has larger vertical scaling despite that the second fits
    the screen").**  `adapt_channel_scale` grows ONLY when the signal
    genuinely overflows the visible budget (`half_range > fit_divs ×
    current`, `fit_divs = max(divs, _half_vert_divs − 0.05)`) — a
    waveform that already FITS keeps its finer scale instead of being
    coarsened to the `divs` fill target (MATLAB getWaveform3.m accepts
    on `isInView && ~isTooTight`).  `divs` remains the SIZING target
    when an adjustment IS needed.  Adversarially verified: calibration
    byte-identical for V_mon + improved for I_mon; convergence strictly
    stabilizing.  TWO mandatory companions (adversarial findings —
    don't remove either):
    * **`force_grow=True` whenever the caller's clip / out-of-view
      detection fired** (the loop's `_overflowed`): the ×2-doubled
      range is an extrapolation, not a faithful observation, and an
      OFFSET-driven clip (E_act at a ~800 mV rest railing ONE side at
      position 0) can "fit" the offset-blind half-range while railing —
      without the bypass the doubling-escape contract breaks and a
      clipped capture gets saved.  The veto must only ever apply to
      faithful in-view observations.
    * **Centring must not depend on a V/div change**: when adapt
      settles (returns None) on faithful data, the loop still applies a
      coordinated POSITION at the kept scale when `bias_ratio ≥ 0.1`
      and the current position is > 0.25 div off — otherwise gotcha
      #41's asymmetric centring silently degrades to "only when the
      V/div changed".
    Tests: `tests/test_adapt_no_coarsen_when_fits.py`.

    What `adapt_channel_scale` does that the homegrown loop
    didn't:
    - **Stateful per channel**: maintains a history of every
      picked scale across captures, hysteresis on shrink (to
      prevent flicker on noisy frames), repeat-detection (locks
      after `_ADAPT_REPEAT_LIMIT` re-picks of the same value),
      and a hard try-cap (`_ADAPT_MAX_TRIES`).  None of these
      existed in VT's pre-refactor loop.
    - **Handles BOTH directions**: clip → upscale, small signal
      → downscale.  Eliminates the **"squished waveform"** mode
      where the trace fits with too much headroom and 8-bit ADC
      quantization (~16 mV/step at 500 mV/div) becomes visible
      as stair-stepping in the captured data.
    - **Grid-boundary clamps**: at min or max of the
      `_TEK_VERTICAL_GRID_VPD` (fine getWaveform3.m grid, gotcha
      #42) grid, accepts current
      rather than retrying — the homegrown loop didn't, so a
      genuinely too-large signal could pin the loop on the
      max grid cell.
    - **Cached `current_scale`**: one USB-TMC round-trip per
      open, not per capture.

    The calibration "clipped → extrapolate" trick is preserved:
    `_clipped_arr(arr)` checks if >5 % of samples sit at the
    min OR max sample value (ADC-rail saturation); if so,
    `v_min`/`v_max` are doubled before passing to `adapt` so
    the new scale sizes for a larger range on the next
    iteration (otherwise observed-range == rail, and adapt
    would re-pick the same too-tight scale forever).

    **In-view check using MATLAB margins** (`MAX_FACTOR`):
    after clip detection, the loop ALSO calls
    `scope.channel_in_view(ch, v_lo, v_hi, margin_divs=MAX_FACTOR)`
    where `MAX_FACTOR` is derived per-scope-series from the
    actual vertical division count:

    * **8-vert-div scopes** (TBS1000 / TDS / TPS):
      `_half_vert_divs = 4` → `MAX_FACTOR = 3.9`.  This is the
      original MATLAB `getWaveform2.m` value.
    * **10-vert-div scopes** (TBS2000 / TBS2000B / TBS2000C):
      `_half_vert_divs = 5` → `MAX_FACTOR = 4.9`.  Using the
      hardcoded 3.9 here would treat 25 % of the visible
      screen as off-limits.

    The in-view check catches the subtle "trace exceeds the
    visible budget but doesn't fully saturate the ADC rail"
    case — e.g. a peak at 4.0-4.2 div on a 4.9-div budget.
    `_clipped_arr` misses this (samples don't settle at the
    rail); `channel_in_view` returns False because
    `|peak| > MAX_FACTOR`.  Same extrapolation as the clipped
    path: double `_adapt_lo`/`_adapt_hi` before passing to
    `adapt` so the V/div grows on the next iteration rather
    than re-picking the same too-tight scale based on the
    truncated observed range.

    `channel_in_view` returns True / False / None (last on
    SCPI failure or simulator no-op).  None is treated as
    "can't check, leave alone" — the existing observed range
    flows through to adapt unchanged.  Per-attempt diagnostic
    log line includes the `fit=in-view` / `fit=out-of-view(±X.Xdiv)`
    field so post-mortem analysis is unambiguous.

    Per-role `divs` budget (`vmon` = 3, `imon` = **4**,
    `eret`/`eact` = 4) passed through.  `imon` was bumped 3 → 4
    (operator: "I_mon is too small") — it has no big compliance
    transient so it doesn't need V_mon's headroom, and at divs=3 the
    1-2-5 grid-ceil snapped a small NIL-preset I_mon (~±60 mV) up to
    50 mV/div (~25 % screen fill); divs=4 lands it on 20 mV/div
    (~63 % fill).  For DC-biased
    eret/eact, the GUI ALSO calls `set_channel_position(ch,
    -mean/scale)` after adapt so the trace centres around its
    mean rather than sitting at the top of screen — `adapt`
    only manages the V/div; the position is the GUI's
    responsibility for biased signals.  Position clamped to
    ±5 div (Tek hardware limit).

    Per-attempt diagnostic log line preserved in same format
    as before (so existing grep tooling still works), with the
    `in-view` field replaced by `adapt`'s own `[scope]` log
    lines (which `adapt_channel_scale` emits internally).
    `MAX_RECAPTURE = 2` cap on iterations preserved.

    **Don't reintroduce the homegrown clip/in-view/coarse-step
    loop.**  The state-loss + lack of hysteresis was the
    underlying cause of the operator-visible "vertical scaling
    not adjusted" complaints.  If a new rescale heuristic is
    needed, add it to `adapt_channel_scale` itself so both
    calibration and experiments benefit.
13. **`compute_scale_position_targets` uses bias_ratio
    `2|mean|/Vpp` as the explicit scale+position decision
    variable** — the cleanest answer to the classic MATLAB autoscale
    pain point ("POSition is in divisions, so adjusting position
    and V/div together is iterative and unstable").  The static
    method on `TektronixOscilloscope` returns
    `(vpd, pos_divs, bias_ratio, regime)` from observed
    `(v_min, v_max)` and a per-role `divs` budget, decoupling the
    two decisions via three regime classes:

    * **R < 0.1 — `AC-centered`**: V/div sized for swing only
      (`Vpp / (2 × divs)`), position stays at 0.  Typical:
      V_mon / I_mon at any amplitude (zero-centered current /
      monitor signals).
    * **0.1 ≤ R ≤ 1.0 — `moderate-bias`**: coordinate both axes.
      V/div sized for swing, position offsets the mean.  Typical:
      E_act during a polarized pulse.
    * **R > 1.0 — `DC-dominated`**: V/div MUST be coarsened to
      `|mean| / position_limit_divs` so the position offset fits
      within ±5 div of the hardware front-panel limit; otherwise
      POSition clamps and the signal goes off-screen.  Typical:
      E_ret at a SIROF electrode (rest potential 0.3-0.5 V,
      swing only ±25 mV → R ≈ 12-20).

    The DC-dominated branch sacrifices swing resolution
    (centred-small beats off-screen-tight) but guarantees the
    trace lands on-screen.  Hardcoded `POSITION_LIMIT_DIVS = 5.0`
    is the front-panel `CHx:POSition` knob range — NOT tied to
    screen visible divs (`_half_vert_divs = 4` or 5 depending on
    series).

    `set_channel_scale_and_position_for_range` is the apply-side
    wrapper that delegates to `compute_scale_position_targets`
    and writes the SCPI commands (back-compat: still returns
    `(vpd, pos_divs)` 2-tuple for callers that don't need the
    diagnostic ratio).

    **VT's rescale loop integrates this with `adapt_channel_scale`'s
    stateful hysteresis**: for V_mon / I_mon, `adapt` alone is
    fine (bias_ratio ≈ 0, position stays 0).  For E_act / E_ret,
    after `adapt` picks a swing-only V/div, VT *conditionally*
    calls `compute_scale_position_targets` to derive the
    coordinated target; if the coordinated V/div is coarser than
    adapt's pick (DC-dominated case), VT **overrides** adapt's
    V/div with the coarsened value so position doesn't clamp.
    The per-attempt diagnostic log line includes `R=X.XX
    (regime)` so post-mortem analysis is unambiguous about which
    branch fired.

    **Positioning runs at ANY magnitude for off-zero signals
    (REVISED — the old "fine = small-magnitude only" gate was
    wrong for DC-biased roles).**
    `compute_scale_position_targets` is called for E_ret / E_act
    (baseline-synthetic input) AND V_mon / I_mon (raw observed
    range) on every iteration where `adapt` changed the V/div —
    NOT only when it SHRUNK it.

    History: an earlier user-spec gated the helper on a SHRINK
    (`_new_scale < _pre_adapt_vpd`), reasoning that fine
    positioning is a "zoom in on small signals" tool and large
    signals already fill the screen at position 0.  That holds
    for ZERO-CENTRED signals (V_mon / I_mon symmetric — position
    0 IS centred at any size) but is WRONG for DC-BIASED E_ret /
    E_act: a large swing on a non-zero rest potential still has
    to be centred or its baseline rides off-screen.  Operator
    caught this — "Is the same protocol for [a] symmetric
    waveform applied to E_ret when the magnitude is not small?".
    So the `_adapt_shrunk` shrink test is now INFORMATIONAL ONLY
    (it annotates the per-attempt result with `[large
    magnitude]`); it NO LONGER skips positioning.

    The `bias_ratio` inside `compute_scale_position_targets` is
    the real gate: R < 0.1 (zero-centred) → position stays 0
    (so a symmetric V_mon / a zero-biased Ag/AgCl E_ret is a
    no-op at any magnitude); 0.1 ≤ R ≤ 1 → offset the mean;
    R > 1 (DC-dominated) → coarsen V/div so the offset fits the
    ±5-div limit.  `_pre_adapt_vpd` (read via `CHx:SCAle?` before
    `adapt`) still feeds the shrink/grow annotation only.  V_mon /
    I_mon use the RAW observed range (centre the excursion
    midpoint, gotcha #41); E_ret / E_act use the baseline-synthetic
    input below (centre the rest potential).

    **Baseline-centred input synthesis for E_ret / E_act** —
    real VT data showed E_ret as mostly FLAT at the electrode
    rest potential (median = 0 mV in one capture) with
    TRANSIENT spikes during the pulse (peaks to +40 mV).  The
    data mean (+8.8 mV) was pulled UP by the spikes;
    `(min+max)/2 = +20 mV` was even worse as a position
    reference because it sat halfway between baseline and the
    spike peak, leaving baseline at the BOTTOM of the screen.

    Fix: for `_role in ("eret", "eact")`, before calling
    `compute_scale_position_targets`, VT extracts the
    **pre-trigger baseline** (MAD-clipped mean of samples with
    `t < -1 µs` — robust to spikes that leak past the
    interpulse boundary).  It computes the **one-sided swing**
    `max(|max - baseline|, |baseline - min|)` and synthesizes a
    symmetric input `(baseline ± swing)` that gets passed to
    the helper.

    Result: `mid = baseline`, `Vpp = 2*swing`, and
    `compute_scale_position_targets` chooses V/div + position
    so that **baseline lands at screen centre** rather than the
    midpoint of the spike envelope.  Across SIROF electrodes
    (rest ~350 mV), gold electrodes (~800 mV), or zero-biased
    Ag/AgCl (~0 mV), the baseline consistently lands at 0 div
    (or at the position clamp ±5 div when DC-dominated, with
    V/div coarsened correspondingly so the trace still fits
    on-screen).  V_mon / I_mon are NOT affected — they're
    zero-centred AC signals, baseline = 0 already, so the
    synthetic input is identical to the raw observed range.

    Per-attempt diagnostic log surfaces `baseline=+X.XXmV,
    swing±Y.YYmV (one-sided)` when this path fires so the
    operator can verify the right position reference was used.
    Skipped when the pre-trigger window has < 8 samples (very
    short record lengths or trigger near the start of capture).

    **Directional out-of-view diagnostic (not action-driving)** —
    `scope.channel_clip_sides(ch, v_min, v_max, margin_divs)`
    returns a 5-tuple `(below_out, above_out, pos_shift_divs,
    headroom_below_div, headroom_above_div)` exposing WHICH
    SIDE of the screen the trace exceeds.  This is used for the
    diagnostic log line ONLY:

    `fit=out-ABOVE(hr_b=+2.90, hr_a=-1.10, shift=-2.00div)` /
    `fit=out-BELOW(...)` / `fit=out-BOTH(...)`

    so post-mortem analysis shows whether the trace went off
    the top, bottom, or both.  The directional info is NOT used
    to drive a position-only nudge.  Per user-spec / MATLAB
    convention, **out-of-view is treated as "probably
    saturated" regardless of direction**:

    The 0.1 div gap between MAX_FACTOR (3.9 or 4.9) and the
    true rail (4.0 or 5.0) exists BECAUSE out-of-view almost
    always means the trace is at or near the ADC rail.  The
    captured `(v_min, v_max)` is then **truncated** — the true
    peak exceeds the observed value by an unknown amount.  Any
    position nudge derived from the (biased) observed midpoint
    `(v_min + v_max) / 2` can land the trace right back at the
    rail.

    Therefore VT's loop treats `_is_clipped is True` AND
    `_in_view is False` identically: both trigger the
    **symmetric-doubling V/div-grow path**.  The next attempt
    then captures with the trace fully visible and uses a
    FAITHFUL observed range to do any fine recentering through
    the coordinated scale+position helper.

    **Don't reintroduce a position-only nudge based on
    `channel_clip_sides`** — the 0.1 div safety margin is
    designed to absorb the saturation ambiguity; spending it
    on a fast nudge that might land back at the rail defeats
    the design.

    **Loop guarantees the saved `acq` reflects the FINAL
    scope state, and verifies in-view per role.**  Two
    contract additions per user-spec:

    1. **Final-iteration recapture**: the loop recaptures
       AFTER the writes on the last iteration too, not just
       between iterations.  Without this, iter `MAX_RECAPTURE`'s
       writes wouldn't be reflected in the saved `acq` —
       `make_capture` would see data from the V/div the loop
       explicitly walked away from.  Implemented as a recapture
       BEFORE the `_attempt >= MAX_RECAPTURE` break (so the
       break fires AFTER the recapture, not before).

    2. **Post-loop in-view verification per role**: after the
       loop exits, each voltage role's FINAL captured data is
       checked one last time against `±MAX_FACTOR` divs of the
       current scope window.  Per-role result appears in the
       "final scope state" diagnostic block as ``✓ fits in
       ±X.Xdiv`` or ``⚠ STILL OUT-OF-VIEW``.  A summary
       warning is also inserted at the TOP of the rescale log
       block (``⚠ At least one role's FINAL capture is still
       out-of-view…``) so a grep for ``STILL OUT-OF-VIEW``
       surfaces every unconverged capture without having to
       read per-role lines.

       The data is saved either way (a partial capture is
       better than dropping the step), but the warning makes
       it impossible to silently save sub-optimal traces — the
       operator can grep the log to confirm "every saved
       waveform fits the MATLAB-faithful ±MAX_FACTOR budget".

    **Don't reintroduce the implicit-coarsen logic in
    `set_channel_scale_and_position_for_range`** — it's already
    factored into the static helper.  Any new caller that wants
    coordinated targets should use the helper directly so they
    get the regime label + bias_ratio for free.

---

## 6. GUI — convention checklist

- **`MainWindow` constructs experiment tabs eagerly.** The
  Calibration tab is lazy (created on "Run Calibration" press),
  and `ResultsTab.viewer` (matplotlib + ViewerPanel) is lazy
  (constructed on first `showEvent`, saving ~500-1200 ms cold launch).
  The pattern-preview first render is deferred to each experiment
  tab's `showEvent` for the same reason.
- **`cmd_logger` wiring**: `ConnectionPanel` sets `scope.cmd_logger` /
  `stim.cmd_logger` **before** `open()` runs so the connect handshake
  is logged. `MainWindow._on_connected` re-binds these to
  `log_pane.log` **only if not already set** — prevents mid-session
  identity churn.
- **Worker-thread → GUI logging** uses
  `QMetaObject.invokeMethod(panel, "_emit_log_from_worker",
  Qt.QueuedConnection, Q_ARG(str, msg))`. Direct `pyqtSignal.emit`
  from a `threading.Thread` has been observed to silently drop in
  this stack; the QueuedConnection path is the documented Qt pattern.
- **Result-handler signals** (`_stimDetectResult`,
  `_scopeConnectResult`) use `Qt.ConnectionType.UniqueConnection` to
  prevent stacking on rapid re-presses of Initialize / Connect.
- **`LogPane` mirrors to disk** via a long-lived line-buffered file
  handle (opened once in `set_log_file`, not per write). Falls back to
  per-write open only if the handle becomes stale (drive ejected
  mid-session).
- **Spinbox prefs restore** goes through
  `pattern_panel._safe_set_spinbox_value(spinbox, value)`. It
  auto-detects `QSpinBox` vs `QDoubleSpinBox` (the former rejects
  `float`) and swallows `TypeError` / `ValueError` from malformed
  prefs. Never inline `try: sp.setValue(float(v)); except: pass` —
  use the helper.
- **VT tab Q_ph lock UI**: `_on_pattern_amp_or_width_changed` and
  `_push_qph_to_pattern` are both gated on `mode_combo.currentText()
  == MODE_FIXED_QPH`. Outside Fixed-Q_ph mode the lock UI is hidden
  and its stale values must NOT propagate to the pattern panel —
  otherwise typing into `width_shared` instantly reverts via
  `W = Q × 1000 / I`.
- **VT Q_ph lock has a master ENABLE checkbox**
  (`qph_lock_enable_chk`, default CHECKED = historical behaviour).
  Unchecking it frees current AND phase width to be edited
  independently — the radios are disabled and charge/phase becomes a
  read-only `I×W` display. Implemented by forcing the derived target
  to `QPH_LOCK_QPH` inside `_recompute_locked_qph` when the box is
  unchecked, and short-circuiting `_on_qph_lock_changed` (so a stray
  programmatic radio toggle during prefs restore can't re-impose lock
  styling). State round-trips under the `qph_lock_enabled` pref key
  (absent → defaults True). `_on_qph_lock_enable_changed` is the
  single apply point (called from the toggle, from `_on_mode_changed`
  on entering Fixed-Q_ph, and from `restore_prefs`). Tests:
  `tests/test_vt_lock_enable.py`.
- **Setup-tab parameters are snapshotted at runner construction**.
  Edits to Setup after Start are ignored; `_start_runner` logs a
  one-liner reminding the user. Don't change this contract.
- **Setup params are pulled into the active experiment tab on entering
  the Test parameters page** (`_on_top_tab_changed` →
  `_sync_setup_into_active_tab`), guarded by the `_setup_dirty_for_test`
  flag (init True for first-entry; set True in `_log_setup_change` on
  every Setup change; cleared after the pull). This closes the gap where
  **aliases / water-window limits don't re-fire their change signal on
  prefs restore** (unlike array / environment / identity, which the
  startup code explicitly re-publishes) and so reached the experiment
  tab stale. The pull re-applies environment / aliases / limits /
  identity / session **AND the oscilloscope settings — acquisition
  (mode + NUMAVg via `SetupTab.current_acquisition()`), trigger source,
  and the digital-trigger flag** (operator: "setup settings, including
  oscilloscope, was not set when I moved to Test parameters").
  **After the cache pulls it ALSO pre-applies the scope HARDWARE
  settings** via `tab.apply_scope_settings_on_entry()` — record length
  (`DEFAULT_RECORD_LENGTH`), acquisition mode + NUMAVg, and the trigger
  (operator: "set all necessary oscilloscope settings when entering the
  Test parameters tab, including record length"). This pre-pays the
  10-30 s TBS2000 record-length buffer reallocation (gotcha #26) at tab
  entry instead of after Start. Pattern-DEPENDENT layout (horizontal
  window, vertical default scales) stays at Start — it follows the
  pattern still being edited. The driver setters are idempotent
  (confirmed-value caches) so re-entry / Start re-assert cost no SCPI
  writes; the method no-ops without a scope or while a run is starting /
  in flight, and never raises. `_on_connected` re-arms
  `_setup_dirty_for_test` so the first entry AFTER hardware connect
  applies the settings. Trigger resolution is SHARED with Start via
  `_BaseExperimentTab._resolve_trigger_settings()` (single source of
  truth for the EXT / channel-Trigger / I_mon paths — don't fork it).
  **It deliberately does NOT call
  `set_array`**, which `clear()`s the operator's channel selections
  (`channel_selector._GridCanvas.set_array`); genuine array changes
  propagate via the `arrayChanged` forwarder. Don't add `set_array` to
  the entry-sync. Tests: `tests/test_setup_into_test_params_sync.py`.
- **Plot minor ticks**: y-axis minor ticks are suppressed on both the
  pattern preview and the calibration plot via a `tickValues` wrapper
  that returns only `levels[:1]`. x-axis minor ticks are kept (they
  help judge phase widths).
- **Scope + calibration plots use MATLAB-style "nice multiples"
  tick step** — `widgets._matlab_nice_tick_step` /
  `_matlab_major_tick_values` / `_make_matlab_tick_override`.
  pyqtgraph's default tick algorithm targets ~10 major ticks per
  axis and picks step 200 for a 0-2000 µs range (11 ticks);
  MATLAB targets ~5 ticks and rounds step to a multiple of
  `{1, 2, 2.5, 5} × 10ⁿ` — picking step 500 for the same range
  (5 ticks: `0, 500, 1000, 1500, 2000`).  The operator (who reads
  MATLAB-style plots all day) found pyqtgraph's denser default
  unreadable.  Use `_make_matlab_tick_override(orig_tickValues,
  target_count=5)` on every axis (bottom, left, right + inset
  bottom + inset left) to install the override — same call site
  in `ScopePlot.__init__` (experiment) and
  `CalibrationDialog._build_plot` (calibration), so the two plots
  produce identical tick layouts for the same range.  Don't roll
  another `_major_only` wrapper that just returns `levels[:1]` —
  that was the old approach and reverts to pyqtgraph's denser
  step.  See gotcha #22 for the symptom.
- **Scope plot axes must use bracket-style labels**
  (`Time [µs]`, `Voltage [V]`) NOT `setLabel(..., units="µs")`.
  Passing `units=` engages pyqtgraph's auto-SI-prefix scaler, which
  appends `(×0.001)` to the y label and rewrites the x tick column
  as `kµs` when the visible range is much smaller than the nominal
  unit — operator then has to do mental arithmetic to read the real
  values. Every axis (main `_plot` + inset `_inset`) explicitly calls
  `enableAutoSIPrefix(False)` AND uses the bracket-style label.
  Easy to miss on the inset specifically — see `widgets.py` ScopePlot
  inset construction.
- **Axis TITLES are Qt widgets, NOT pyqtgraph axis labels** — the
  operator reported the axis labels MISSING on the bench REPEATEDLY
  (4×), even though the title text was set and rendered in headless
  tests on this exact pyqtgraph 0.14 / venv, and reserving axis space
  (`setHeight`/`setWidth`) didn't help. The tick numbers paint directly
  and always survive; pyqtgraph's separately-positioned title
  `QGraphicsTextItem` just doesn't render on that display. So
  `ScopePlot` STOPPED calling `setLabel` for the titles and instead
  paints its own around the plot (`_build_plot_box`): an `_AxisTitle`
  widget on the LEFT (`Voltage [V]`, rotated −90), the RIGHT (dynamic
  density label, +90), AND the BOTTOM (`Time [µs]`, `side="bottom"`,
  angle 0 — NOT a QLabel anymore). **All three are `_AxisTitle`s sharing
  ONE render path**, so the bottom title is the exact same font size /
  weight as the sides (operator: "Time axis label is not the same font
  size as the y axis labels" — a QLabel's direct-render path looked
  different from the sides' pixmap-rotate path at the same point size).
  `set_axis_labels(left=/right=)` routes to the `_AxisTitle.setText`
  (flattening `<br/>` + stripping tags — the rotated single-line widget
  can't wrap). **`_AxisTitle` rotates the LEFT −90 (reads bottom-to-top)
  and the RIGHT +90 (top-to-bottom) — opposite-facing** (operator
  preference). The trick: it renders the text HORIZONTALLY to a
  `QPixmap` then rotates the PIXMAP (`QPixmap.transformed`). A direct
  `painter.rotate(+90) + drawText` silently FAILED to paint the +90
  right title under offscreen rendering (the −90 left one rendered) —
  rotating the rendered pixmap works for both signs. Verified the +90
  image is exactly the 180° rotation of the −90 image (IoU 1.0), and
  both render with ink. The Height +/− controls still resize `self._plot`
  (it's the stretchy cell of the grid) and the inset is unaffected.
  Don't go back to pyqtgraph `setLabel` titles, and don't switch
  `_AxisTitle` back to direct `painter.rotate` — the +90 case won't paint.
  **The source pixmap is built at the display's `devicePixelRatioF()`**
  (size `tw·dpr × th·dpr`, `setDevicePixelRatio(dpr)`, painted in LOGICAL
  coords, positioned by the rotated result's device-INDEPENDENT size
  `rpm.width()/dpr`) so the rotated y-titles render at the SAME physical
  size on any display (operator: "the x axis label is not the same size as
  the y axis labels"). A DPR-1 pixmap on a 125 %/150 %-scaled Windows
  display came out smaller AND blurrier. **Don't drop back to a
  `QPixmap(tw, th)` at ratio 1** — the size mismatch returns on any scaled
  display (it's invisible in the offscreen test env, where DPR is always
  1.0). The bottom title (`side="bottom"`, angle 0 → identity transform)
  uses the SAME pixmap path so it matches the rotated sides exactly; all
  three use `_AXIS_LABEL_PT`. `_AxisTitle.__init__` also `setFont`s the
  widget to `pt` so `font().pointSize()` reports it (tests query it).
- **`_ChannelPage` carries the scope plot only** — there is no
  inline `MetricTable` below the per-channel plot. Per-capture
  metric numbers are rendered by the experiment tab's right-side
  `metrics_side` panel (`_BaseExperimentTab.metrics_side`), which
  is the SINGLE home for the metric table. The earlier inline table
  duplicated the side panel and was removed in
  `gui/multichannel_scope.py`.
- **The metrics table FOLLOWS the capture shown in the plot** via
  `MultiChannelScope.captureChanged` (a `pyqtSignal(object)` connected in
  `_BaseExperimentTab.__init__` to `metrics_side.show_capture`). Emitted
  whenever the displayed capture changes — `_ChannelPage.set_index`
  (per-capture dropdown / prev / next, via its `_scope_parent` back-ref set
  in `ensure_page`), `MultiChannelScope._on_entry_changed` (entry-list /
  Latest switch, with the new page's `current_capture()`), AND
  `MultiChannelScope.add_capture` (live) — but the live emit is GUARDED on
  `page is self.content_stack.currentWidget()` so a capture for a
  NON-visible page never re-points the table. **`captureChanged` is the
  SINGLE source of truth for the table; `_on_capture` does NOT push the raw
  capture** (it used to call `metrics_side.show_capture(capture)`
  unconditionally). Why the guard + removal (operator: "why are the plot
  values not matching the table values", with a multi-config VT screenshot
  showing the plot on a completed channel's high-amplitude capture while the
  table read a 5 µA capture): in a multi-config sweep, while the user PINS a
  completed channel (auto-follow off, viewing CH01 #4), the NEXT channel's
  early low-amplitude captures stream in via `_on_capture`; the old
  unconditional `show_capture(capture)` overwrote the table with that
  non-visible channel's live capture → every value (I_stim, V_a/R_a, V_d,
  E_pol, limit-reached) mismatched the pinned plot. Routing the table
  exclusively through the visible-page-guarded `captureChanged` makes it
  impossible for the table to show a different capture than the plot.
  **Don't re-add a direct `metrics_side.show_capture(capture)` in
  `_on_capture`** — it bypasses the visible-page guard and reintroduces the
  desync. Tests:
  `test_experiment_plot_view.py::test_capture_changed_emitted_on_*`.
- **`MetricTable` first row = NUMBER OF PULSES, plus a CUMULATIVE CHARGE
  row** (operator: "Number of pulses should be the duration of the pulsing
  multiplied by the pulse rate" + "per-capture only (≈ the averaging
  count)" + "I also want cumulative charge at each capture, building upon
  previous captures"). The old first row was `("Pulse #", str(c.index))` —
  the capture INDEX, which read "0" for the first capture and the operator
  (reasonably) expected a pulse count. Now:
  * `metrics.n_pulses` = pulses delivered for this capture = the MEASURED
    pulsing elapsed time × rate (operator: "The number of pulses must be
    calculated from the elapsed time of starting and stopping the
    pulsing"). The VT runner stamps `time.monotonic()` right after
    `start_all()` and in the `stop_all()` `finally` (covering the initial
    acquisition AND every rescale re-capture — the stim pulses
    continuously across them), then sets
    `cap.metrics.n_pulses = round((t1 - t0) × pattern.rate_hz)` after
    `make_capture`. `_record_capture_dose` HONOURS that pre-set value and
    only falls back to the scope averaging count (`_expected_acq_navg`, or
    1) when it wasn't measured — i.e. the SIMULATOR (captures return
    instantly → elapsed×rate rounds to 0) or an early abort. **Don't make
    the helper overwrite a runner-measured `n_pulses`.** Table row "Number
    of pulses"; falls back to "Capture #" = index when NaN
    (continuous-pulsing SP/CP/LP don't call the helper).
  * `metrics.cumulative_charge_nc` (also set by `_record_capture_dose`) =
    `Σ |Q_ph_i| × n_pulses_i` over the run's captures up to + including
    this one — the cathodic charge delivered so far, resetting per
    ChannelRun. Table row "Cumulative charge", auto-scaled nC/µC/mC by
    `widgets._fmt_cumulative_charge`. Shown only when finite.
  `_record_capture_dose(run, cap)` lives in `experiments/base.py` and is
  called RIGHT AFTER `run.captures.append(cap)` (so the cumulative sum
  includes this capture) by VT and PS — the discrete per-amplitude-burst
  runners, where each capture IS the pulsing and the cumulative is the true
  delivered dose. **SP/CP/LP deliberately do NOT call it** (they pulse
  CONTINUOUSLY between snapshots, so per-capture N_avg would under-report
  the dose); their fields stay NaN and the table omits the cumulative row.
  Both fields round-trip in `persistence.py` (NaN default for legacy npz).
  Tests: `tests/test_capture_dose.py`.
- **`MetricTable` drops the "active" qualifier when there's no E_act** —
  without an instrumentation amp (no E_act/E_ret captured, every `return_*`
  list empty) V_mon IS the active-vs-return voltage, so the table shows a
  SINGLE `V_d [V]` = `m.driving_voltage_v` (= `max|V_mon|`, matching the
  plot's V_d marker) and labels `V_a` / `R_a` / `E_pol` WITHOUT " active"
  (operator: "There should not be any 'Vd active' because Vd is active
  versus return, so the Vmon gives Vd"). With an E_act the per-phase
  `V_d active` + `V_d return` rows return. `E_pol` itself uses the
  operator method on V_mon (gotcha #60) — so the table value equals the
  plot's Emc/Ema for the same capture. Tests:
  `test_experiment_plot_view.py::test_metric_table_*_eact`.
- **`ScopePlot` view controls** (`widgets.py`) — the experiment plot
  carries the SAME zoom/height/reset affordances as the Test-parameters
  pulse preview (operator request). `_build_view_controls` adds a row
  above the plot: **X+/X−/Y+/Y−** (`_zoom_axis` scales the visible range
  about its centre — X = shared time axis, Y = BOTH left/right view-boxes
  together so the dual scales stay aligned), **Height +/−**
  (`_change_height` steps `minimumHeight` so the plot grows within the
  scope/camera splitter — accumulate off `minimumHeight()`, NOT the
  rendered `height()`, which doesn't change until the layout re-flows),
  and **Reset view** (`reset_view` restores the cached pulse-framed
  `_default_xrange` from the last `set_traces` + re-auto-fits Y via
  `align_y_zeros`). Fonts are bumped above pyqtgraph's defaults for
  bench readability (operator: "Increase the font size on the experiment
  plot") — `_AXIS_LABEL_PT` (14) on axis labels via the `font-size`
  style on every `setLabel` (incl. `set_axis_labels`), `_TICK_PT` (12)
  on tick numbers via `axis.setStyle(tickFont=…)`, `_LEGEND_PT` (12) via
  `legend.setLabelTextSize`, and the `_ChannelPage` title QLabel at
  13pt. Tests: `tests/test_experiment_plot_view.py`.
- **Mouse wheel does NOT zoom any pyqtgraph plot** (operator: "Prevent
  mouse scrolling from zooming in and out of plots"). pyqtgraph's
  ViewBox zooms on wheel by default; `widgets.disable_plot_wheel_zoom`
  overrides a ViewBox's `wheelEvent` to `ev.ignore()` (so the wheel
  bubbles to an enclosing scroll area instead of zooming — drag-pan, the
  X±/Y± buttons, and scrollbars still work). Applied to EVERY interactive
  plot: `ScopePlot` (main + right-axis ViewBox + inset), `pattern_preview`,
  `calibration`, `staircase_plot`, `tracking_plot`. Add the call to any
  new `pg.PlotWidget` you introduce. Tests:
  `tests/test_experiment_plot_view.py`.
- **Live-plot metric cursors** (`ScopePlot.set_markers`, fed by
  `plotting.compute_metric_markers(capture)` in
  `multichannel_scope._refresh_traces`) show electrode polarization
  (Emc/Ema) AND **access voltage + driving voltage** (operator: "I want
  access voltage and driving voltage indicated on the experiment plot").
  **`compute_metric_markers` is the SINGLE source of truth** for the
  V_a / V_d / Emc-Ema points (kind + label + t_us + y + text); the
  exported matplotlib figure (`plotting.plot_capture`) consumes the SAME
  helper so the live and saved plots never drift (live maps kind→pyqtgraph
  symbol, export maps kind→matplotlib `"_"`/`"+"`). Don't recompute the
  marker geometry in either consumer — extend the helper.
  `set_markers` accepts `(label, x, y)` (tag = `label = y V`),
  `(label, x, y, text)` (explicit tag — used because V_a / V_d are
  DERIVED values, not the y the marker sits at), `(…, text, color)`,
  `(…, text, color, symbol)`, or `(…, text, color, symbol, html)`.
  **Variable typography** (operator: "Use proper variable formatting on
  the plot markers"): the `html` element carries an italic-variable /
  upright-subscript tag built by `plotting.marker_label_html` (which uses
  `gui.rich.var` → `<i>V</i><sub>a1</sub>`); the live plot renders it via
  `TextItem(html=…)` (wrapped in a `<span style="color:…">` so the colour
  applies). The exported matplotlib figure uses the parallel
  `plotting.marker_label_mathtext` (`$V_{\mathrm{a1}}$` — italic var via
  math mode, upright subscript via `\mathrm`). Both come from each
  marker's structured `clauses` list (`(var, sub, value)` tuples; access
  has TWO — V_a and R_a), so the live and exported labels stay identical.
  **Label placement — CANDIDATE-SCORING, NO leader lines** (operator:
  "position the labels left, right, or one of the four corners of the
  marker … on opposite sides if necessary"). `set_markers` runs AFTER
  `align_y_zeros` (so it sees the FINAL view range) and, for each glyph,
  scores SIX candidate placements — the four corners (UR/UL/DR/DL) + pure
  L/R — picking the lowest-penalty one. Penalties: running OFF-SCREEN
  (30×, hard), OVERLAPPING an already-placed tag (90×, the dominant term —
  this is what spreads a CLUSTER of markers onto OPPOSITE sides), and
  INTERSECTING the trace — **GRADED** via `_trace_overlap(bl,br,bb,bt)`
  (the data-unit VERTICAL overlap of the label box with the local trace in
  its x-range) × 30, NOT the old binary 3.0. The binary form couldn't tell
  "barely clips" from "fully buried", so when several candidates all touched
  the trace it kept a clustered tag (e.g. V_a3) ON the trace; the graded
  penalty makes the scorer pick the side that clips LEAST — sending the tag
  LEFT when the right side is buried (operator: "the third access voltage
  clearly needs to be on the left side of the marker because it is
  intersecting the plot"). A small bias keeps an isolated tag on the
  trace's open side / to the right. **TIER STACKING (small-signal clusters):**
  each candidate may escalate its offset OUTWARD in tiers — one label-height
  per tier vertically (corners), one label-width per tier horizontally (pure
  L/R), up to `_MAX_LABEL_TIER` (6) — so when a LOW-CURRENT capture squeezes
  every marker into a thin ±20 mV band (operator: "small current … the
  marker labels intersect with each other and the plot") the 4-5 colliding
  tags step apart instead of piling up. A flat **+1.0 per overlapping box**
  (on top of the graded 90× area term) makes escalating cheaper than
  tolerating ANY overlap; a `tier × 0.20` cost keeps the CLOSEST free tier
  preferred. **OFF-SCREEN is a flat +8.0** (well above the graded
  trace/overlap terms) + the graded 30× amount, so a tag NEVER prefers a
  tiny clip over an on-trace spot — fixes the first access label being
  clipped by the left axis (operator: "the first access voltage and
  resistance label is being clipped by the left axis"); the old purely-
  graded 30× was negligible for a few-px clip and lost to the trace term.
  Tests:
  `test_experiment_plot_view.py::test_set_markers_picks_less_intersecting_side`,
  `…::test_small_signal_cluster_labels_do_not_overlap`.
  Tags are placed first→last sorted by
  x, accumulating `placed_boxes`. The offset is a SMALL `(gap_x, gap_y)`
  from the GLYPH'S OWN position (verified on a sloping-charge synthetic:
  every tag `hits_trace=False`, `dist≈0.02` of the view — close AND off
  the trace). `ax = 0/1` (text right/left of pos), `ay = 1/0` (above/
  below). Box geometry uses rough data-coord tag-size estimates
  (`line_h`/`char_w`) × the per-tag line count (2 for access — R_a is a
  second line). **NO dashed leader lines.** The matplotlib export
  (`plotting.plot_capture`) uses the SAME idea with `va="top"/"bottom"` +
  `ha` left/right edge-awareness + offset tiers. **Don't revert to the
  fixed above/below placement — clustered tags (V_a2 / Emc / V_d around
  the phase-1 boundary) collided.**
  **Access tag = TWO lines: V_a then R_a** (operator: "Make the access
  resistance as a second line") — `marker_label_html` joins clauses with
  `<br/>`, `marker_label_mathtext`/`_clauses_text` with `\n`; this also
  halves the tag's horizontal extent, easing intersection. **ALL marker
  items use `addItem(…, ignoreBounds=True)`** so an off-trace label can't
  expand the ViewBox auto-range and squash the waveform flat (was a real
  bug — looked like "missing axis labels"). Glyph+text stay adjacent in
  `_marker_items` (adjacency-based tooling/tests).
  **Marker tag font matches the plot** (operator: "Match all texts on the
  experiment plot the same … match the marker font size with the plot"):
  the tag renders at `ScopePlot._MARKER_PT` (12 = the tick / legend size,
  up from pyqtgraph's ~9 pt default) via a `font-size:Npt` in the HTML
  span (Qt rich text honours CSS `font-size` — verified) and
  `textItem.setFont` on the plain fallback.
- **Legend / trace names are SUBSCRIPTED, not underscored** (operator:
  "If V_mon, I_mon, E_ret, and E_act are not going to be with subscripts,
  then remove the underscore"). `multichannel_scope._subscript_trace_name`
  turns `"V_mon"` → `"<i>V</i><sub>mon</sub>"`; the pyqtgraph legend's
  `LabelItem` renders the HTML, matching the subscripted markers + title.
  This string is ALSO the ScopePlot **curve key** (`_curve_data` /
  `_curve_axis` / `_curve_colours_cache`), so it's stable + consistent —
  but any test/tooling that hard-codes the bare `"V_mon"` curve key must
  resolve it through `_subscript_trace_name` instead. The trace VISIBILITY
  CHECKBOXES use plain text (no HTML render), so the SECOND `_trace_label`
  drops the underscore (`"V_mon"` → `"Vmon"`) rather than subscripting.
  **Waveform never clips the screen edge** (operator: "ensure that the
  waveform stays within the screen"): `align_y_zeros` used
  `setYRange(padding=0)`, putting the larger excursion flush against the
  edge and clipping the cathodic plateau + its (ignoreBounds) V_d label.
  It now scales BOTH left limits by `1 + _Y_MARGIN_FRAC` (0.12) — which
  preserves the lo/hi ratio so the zero stays aligned with the right axis
  — giving headroom for the trace AND the outside-the-envelope labels.
  Only applied when the range straddles zero.
  **Right-axis density→current conversion** (operator: "include the
  current density to current scale in the axis label"): in density mode
  (`_use_density`, i.e. a surface area is set on the scope widget) the
  right-axis label is `Current Density [A/cm² = X µA]` where
  `X = area_um2 × 0.01` (operator follow-up: "I want the current density
  scale in brackets, e.g., [A/cm2 = 50 uA]" — the conversion now lives
  INSIDE the bracketed unit, not in a trailing parenthetical); raw-current
  mode shows `Current [µA]`. Tests:
  `test_experiment_plot_view.py::test_density_right_axis_label_uses_brackets`.
  **Axis-title gap = matched, content-fitted reserve.** The rotated
  `_AxisTitle` widgets hug the plot, but the perceived gap is the SLACK
  pyqtgraph reserves between the tick numbers and the plot edge. With
  `autoExpandTextSpace=True` that reserve FLOORS at ~30 px and never
  shrinks for short numbers, so the right-axis density ticks (`2`, `−2`)
  sat far from the title while the longer left-axis voltage ticks (`−0.2`)
  hugged (operator: "right axis label too far — match the gap like the
  left"). `ScopePlot._fit_axis_text_space(which)` — called for BOTH axes at
  the end of `align_y_zeros` (after ranges are final, before `set_markers`)
  — measures the ACTUAL rendered tick strings for the current range
  (`ax.tickValues(lo, hi, size_px)` where `size_px` is the axis HEIGHT in
  px, NOT the data span — pass the data span and pyqtgraph picks absurd
  spacing and renders 11-digit strings) and pins
  `autoExpandTextSpace=False, tickTextWidth=wmax+6`. Measured from the
  strings that actually show, so it never clips (wide raw-current `−2000`
  reserves more, short density `2` reserves less) and both axes end with
  the same residual slack → symmetric gap. Tests:
  `test_experiment_plot_view.py::test_align_y_zeros_fits_axis_text_space`.
  **Glyph convention** (operator: "For access
  and driving voltage plotting, use a horizontal bar symbol. For
  electrode polarization, use plus symbols, and indicate if Emc or Ema"):
  * **V_a / V_d → `symbol="hbar"`** — a horizontal-bar `QPainterPath`
    (`widgets._hbar_symbol`, lazy + cached; pyqtgraph accepts a
    `QPainterPath` wherever a symbol-name string goes).
  * **Polarization → `symbol="+"`** — rendered via a CUSTOM
    `widgets._plus_symbol` `QPainterPath` (two crossing LINE strokes, NOT
    pyqtgraph's filled "+" polygon). Both the bar and the plus are
    stroked line paths drawn with the SAME pen width (`pen_w = 3` in
    `set_markers`), so they read at MATCHING thickness — the bar thicker
    than its old 2 px, the plus thinner than the filled built-in glyph
    (operator: "horizontal bar thicker and plus thinner — matching
    thickness"). Tests identify the two by IDENTITY against the cached
    paths (both are `QPainterPath`, so `elementCount` 2=bar / 4=plus or
    `is _hbar_symbol()` / `is _plus_symbol()`). The generic
    "Epol{k}" label is RELABELLED by phase polarity: a cathodic phase
    (`amplitude_ua < 0`) → **Emc**, anodic → **Ema** (matches MATLAB
    `getAcutePlot3.m` `{\itE}_{mc}` / `{\itE}_{ma}`). A phase-number
    suffix (Emc1/Emc2) is added only when the same polarity repeats
    (triphasic); biphasic stays bare Emc/Ema.
  * **`symbol="o"` → an EMPTY (unfilled) circle** — `ScatterPlotItem`
    `symbol="o"` with a TRANSPARENT brush (`mkBrush(0,0,0,0)`), outline
    only. Used for the ending-interphase-potential marker (matplotlib
    export: `marker="o", facecolors="none", edgecolors=…`).
  * Default (no 6th element) → `"x"` for back-compat.
  * **NO-LABEL glyphs** — `set_markers` tuples take an optional 8th element
    `draw_label` (default True); `False` draws the GLYPH ONLY (no text tag,
    skipped in both the placement scorer and the draw loop).  The
    `multichannel_scope`/`plotting` mappers set it from a marker dict's
    `no_label` flag.  Used by the two annotation-free markers below.
  * **Ending interphase potential marker — REMOVED** (operator: "remove the
    ending interphase potential").  It went empty-circle → small-filled-
    circle → gone.  `compute_metric_markers` no longer emits a
    `kind="interphase"` marker; don't re-add one.
  * **Other driving potentials — REMOVED; only the single V_d is drawn**
    (operator: "I changed my mind about the other driving points — only show
    the driving voltage").  No `kind="driving_other"` bars on the
    non-driving phases.  Tests: `tests/test_driving_voltage_marker.py`.
  * **V_d (and the value) sits on the ACTUAL PEAK excursion of the driving
    phase, NOT the nominal `phase_end − 1 µs` sample** (operator: "the
    driving voltage/potentials … are not on the peaks").  `_phase_peak_idx`
    finds `argmax|v − base|` in the phase window (LAST occurrence — so a
    flat/settled plateau resolves to the phase END per MATLAB getDriving2.m,
    while a real charge finds its unique peak), with a small leading guard to
    skip the IR-step/switching transient.  The nominal phase timing can be
    ~1-2 µs off the real current switch (onset-detection skew), which slid
    the old sample onto the declining edge for an anodic charge that peaks
    then rolls into the transition; the cathodic trough happened to sit at
    the end so it looked fine.  Don't revert to a nominal-`phase_end` sample.
  * **All SETTLED-value reads (V_d, V_a, E_pol) go through `metrics._despike`
    — a MEDIAN filter (~4 µs) that rejects switching SPIKES + ringing**
    (operator: "spikes and ringing … We need to ensure that the spike does
    not mislead … the driving voltage … this is also important for access
    voltage").  At small currents the phase-boundary transient (sharp
    overshoot + a few µs ringing) is LARGE vs the signal, so a plain
    max/argmin / a raw `v_trace[acc]` read lands on the spike and over-
    reports V_d / V_a.  MEDIAN (not the mean `_smooth`) rejects a spike
    narrower than half the window without smearing toward it, and a window
    spanning ≳ one ringing period averages the oscillation to its centre.
    Applied to: `access_voltage_and_resistance` (driving extrema `v_filt =
    v_ds` + every `v_ds[acc]` value read; the `|dV/dt|` EDGE LOCALIZATION
    still runs on the lightly-smoothed `v_smooth`, since despiking blurs the
    edge it needs), `compute_metrics`' `driving_voltage_from_vmon/_potentials`
    inputs, and `compute_metric_markers` (marker POSITIONS read `v_ds`).
    Safe for normal captures: values are read on plateaus far longer than
    4 µs, so a clean trace is unchanged (median of a flat plateau = the
    plateau).  Tests: `tests/test_spike_rejection.py`.
  * **All glyphs + labels are BLACK** (`#000000`) (operator: "Have the
    marker and label be black") — the glyph SHAPE (─ vs +) already
    distinguishes the kind, so colour isn't needed and black reads
    cleanly on the white plot. Both the live `_style` map
    (`multichannel_scope`) and the export `_mpl_style` map (`plotting`)
    use black; change them together if a colour is ever reintroduced.
  **V_a is plotted at EVERY access point — leading AND trailing**, NOT
  one-per-phase (operator: "You are forgetting to plot the trailing
  access voltage. … the number of access voltage and resistance depends
  on the existences of interphase delay and discharge delay"). The set
  comes from `metrics.access_voltage_and_resistance` / `access_index_labels`
  (order `[lead-ph1, trail-ph1, lead-ph2, trail-ph2, …]`; trail/lead
  entries present only where the current steps to/from zero — interphase
  delay adds trail-ph1 + lead-ph2, discharge delay adds trail-ph(last)).
  Positions use the **data-driven `access_idx`** (the |dV/dt| edge
  localizer), so markers land exactly where each access was measured; the
  tag carries V_a AND R_a, numbered sequentially V_a1…V_aN. The picker
  RECOMPUTES `access_voltage_and_resistance` on V_mon (not the stored
  `access_voltage_per_phase_v`) so va/idx stay parallel. **Bug fixed
  alongside**: `access_voltage_and_resistance` step 6 was hard-coded
  6a/6b/6c gated on `has_iph`/`has_dd`; a delay-less ph1→ph2 boundary —
  which `access_index_labels` reports as a fused `(k+1,'lead')` — skipped
  step 6b, leaving `va` SHORTER than `access_idx` and misaligned. Step 6
  is now **label-driven** (one entry per label, in order), byte-identical
  to the old code for the common interphase+discharge biphasic, plus
  correct delay-less and monophasic-trailing handling.
  **V_d is a SINGLE marker at
  the END of the DRIVING phase** — the phase with the largest
  `|amplitude_ua|`, ties resolved to the FIRST phase so a symmetric
  biphasic uses phase 1, an asymmetric/triphasic uses the largest-current
  phase (operator: "Plot the largest voltage change in V_mon. For
  biphasic symmetric, V_d should be the end [of the] first phase. For
  asymmetric, V_d should be at the end of the largest current phase").
  V_d is sampled 1 µs INSIDE the phase end (before the current switches)
  so it reads the SETTLED driving voltage; its value is the V_mon change
  from the pre-pulse baseline — the MATLAB `getDriving2.m` definition
  (`|prePulseVoltage − voltage(afterPhaseWidth_idx)|`, at the phase end).
  Epol/V_d anchored at `metrics.pulse_onset_us` (gotcha #44); access
  markers are data-driven (no anchor needed). Tests:
  `tests/test_driving_voltage_marker.py`.
- **Time-axis tick density is SPAN-AWARE** (operator: "too few x ticks
  for a 440-us pulse"). `_make_matlab_tick_override` takes
  `short_span_target` / `short_span_threshold`; the ScopePlot bottom
  axis passes `(10, 2000.0)` so a short pulse window (≤ 2000 µs) gets a
  denser 10-tick target while longer windows keep the sparse MATLAB
  5-tick layout (the two prefs can't be met by a single fixed target).
  Left / right axes stay at 5.
- **`MetricTable` word-wraps** (`setWordWrap(True)` +
  `resizeRowsToContents()` after each `show_capture`) so the multi-value
  per-phase rows (four access voltages, etc.) aren't clipped (operator:
  "allow for word wrapping because additional lines are cut off").
- **Channel/combo follow + "Latest" button** (`MultiChannelScope`) —
  the entry-list view FOLLOWS the live channel/combo (auto-selects each
  new capture's row) UNTIL the user pins a different entry, then stays
  put (operator: "do not automatically go to the new latest … include a
  latest button … move to the latest until a different channel/combo is
  selected"). State: `_entry_auto_follow` (default True), `_latest_key`
  (most-recent capture's key — what Latest jumps to). `add_capture` only
  auto-selects when following; `_on_entry_changed` flips follow OFF on a
  USER selection of a non-latest row (re-arms when they pick the latest)
  — programmatic selections route through `_select_entry_row`, which
  sets the `_programmatic_select` guard so they don't read as a user
  pin. The ⤓ Latest button (`_jump_latest_entry`) re-arms follow + jumps
  to `_latest_key`; it's enabled only while pinned off the latest
  (`_refresh_latest_btn`). Mirrors the existing per-capture follow on
  `_ChannelPage` (within-page prev/next/Latest). Tests:
  `tests/test_experiment_plot_view.py`.
- **Run-progress bar** (`_BaseExperimentTab._build_run_progress` /
  `begin_run_progress` / `_tick_run_progress` / `_end_run_progress`) —
  a thin `QProgressBar` + label above the scope on the experiment page,
  port of MATLAB `updateWaitbar.m`. Shows `elapsed / total s` +
  `(pulses / total pulses)` (pulses = elapsed × `pattern.rate_hz`,
  comma-grouped like `addCommas`), updated once a second by a QTimer
  while a run is live. **Self-gates on the runner's
  `policy.duration_s`**: finite (SP / LP) → fractional bar; `inf`
  (Continuous Pulsing) → Qt busy bar (`range 0,0`) + running counts, no
  total; `None` (VT / PS have no `policy.duration_s`) → bar stays HIDDEN
  and those tabs use the status-bar channel progress instead. Wired in
  `_start_runner_body` right after `_worker_thread.start()` and torn
  down in `_on_finished` (so a multi-config SP sweep re-shows it per
  config). The "Acquire waveform" button was removed from the SP tab —
  captures happen at the runner cadence, so a manual single-shot was
  redundant. Tests: `tests/test_run_progress_bar.py`.
- **Camera architecture** (`gui/camera.py`) — three-piece split,
  all sharing ONE singleton `CameraService` so a single QCamera
  pipeline serves the whole GUI:

  * **`CameraService`** (singleton via `camera_service()`) — owns
    the `QCamera` + `QMediaCaptureSession` + `QImageCapture` +
    `QMediaRecorder` + `QVideoSink`.  Exposes signals (`connected`,
    `disconnected`, `frameReady(QVideoFrame)`, `statusChanged`,
    `captureSaved`) and methods (`connect_to`, `disconnect`,
    `take_snapshot`, `start_recording`, `stop_recording`,
    `enumerate_devices`).  Frame fan-out via `QVideoSink` instead
    of `QVideoWidget` — multiple subscribers paint their own copies
    of each frame from the `frameReady` signal.
  * **`CameraConnector`** — combobox + Connect/Disconnect button.
    Lives in **`ConnectionPanel`** under the oscilloscope section
    (`conn.camera_connector`) so the operator wires up the camera
    alongside the stim + scope at session start.
  * **`CameraStreamPane`** — lightweight preview that subscribes to
    `CameraService.frameReady` and paints via
    `QVideoFrame.toImage()` + `QPixmap.fromImage()`.  HIDDEN by
    default; auto-shows when the service connects, auto-hides on
    disconnect.  Carries a prominent **LIVE / RECORDING status
    badge** above the video that flips colour based on
    `CameraService.recordingChanged`:

    * GREEN `LIVE PREVIEW  ·  not recording` — frames go to screen
      only; no file is written.
    * RED `● RECORDING` — frames are ALSO being saved to MP4 in
      `<repo>\test\`.  This badge is the canonical UX guarantee
      that the operator never confuses live preview for active
      recording.

    Embedded in **two** places sharing the same singleton service:
    1. **`ConnectionPanel.camera_preview`** — appears right under
       the connector as soon as the camera connects, so the bench
       view is immediately visible without navigating to an
       experiment tab.
    2. **`tab.camera_stream_pane`** in every experiment tab,
       beneath the multichannel scope in a vertical splitter, for
       use during a run.

  **Singleton parenting** — `camera_service()` parents the
  `CameraService` to `QCoreApplication.instance()` (when one
  exists at first-call time) so the C++ QObject lives as long as
  the Qt event loop.  Without this, pytest tore the singleton's
  C++ side down between modules and the next `camera_service()`
  call returned a stale wrapper raising `RuntimeError: wrapped
  C/C++ object of type CameraService has been deleted`.  The
  function also defensively re-instantiates if the cached
  wrapper's underlying QObject has been deleted.

  **Per-run capture toggles** — each experiment tab's Test
  Parameters page has a "Camera capture during run" group with:
  *Periodic snapshot every N seconds* (floor 1 s, ceiling 1 hr,
  default 30 s) and *Record MP4 video for the entire run*.  Both
  default OFF; round-tripped under `camera_capture` key.  At run
  start, `_BaseExperimentTab._arm_camera_for_run` reads these
  toggles, optionally starts a QTimer firing `take_snapshot` and
  / or calls `start_recording`; `_disarm_camera_after_run` (in
  `_on_finished`) tears them down on EVERY exit path (normal end,
  abort, exception).  No-op when no camera is connected (the user
  can pre-configure the toggles).

  **Output files**: snapshots and recordings land in
  `<repo>\test\camera_<YYYYMMDD-HHMMSS>.jpg / .mp4` per the
  project-tree convention (NEVER `%APPDATA%` or `%TEMP%`).
  Resolution lives in `camera._project_test_dir()`.

  **Optional "Camera Monitor" dock** (`View → Camera Monitor`,
  Ctrl+Shift+C) — additional floatable preview pane that shares
  the same singleton service.  Useful when the operator wants the
  camera on a second monitor independent of which experiment tab
  is active.  Lazy-constructed via `MainWindow._ensure_camera_dock`.

  **Lazy QtMultimedia import** — neither the `camera` module's
  top-level import nor MainWindow / ConnectionPanel construction
  touches `QtMultimedia` directly.  The heavy backends land inside
  `CameraService._ensure_qtmm()`, called only when something asks
  the service to enumerate / connect.  Cold launch is unaffected
  for sessions that never engage the camera.

  Smoke-tested in `tests/test_camera_panel.py` (12 tests covering
  singleton sharing, enumeration, no-camera-present guards on
  snapshot / record / disconnect, prefs round-trip + malformed-
  payload tolerance, stream-pane visibility tracking, and the
  project-tree output-dir invariant).
- **Pulse-pattern preview discharge annotation** has two modes
  (mirrors the interpulse annotation in the same file):
  - `last_dd < 1 ms` → diagonal-arrow callout, `dx_mag` capped at
    80 µs so even ~1-ms discharges don't produce screen-spanning
    leaders.
  - `last_dd ≥ 1 ms` → `skip_arrow=True`, text pinned near band
    start, small `dy_factor = 0.7`. Without the cap an `last_dd`
    of, e.g., 15 ms produced a 22.5-ms arrow that crossed the entire
    plot and a text endpoint that landed inside the interphase-delay
    label's x range. See `pattern_preview.py` discharge block for
    the placement matrix.

---

## 7. Single-source-of-truth registry

These have ONE canonical definition; everything else is a re-export
or import alias. Don't duplicate.

| Symbol | Canonical location | Notes |
|---|---|---|
| `fmt_elapsed` | `hardware/base.py` | MATLAB `getEndTime.m` port. Re-exported as `_fmt_elapsed` from `hardware/tektronix.py` and `hardware/plexon.py`. |
| `imon_trigger_level` | `experiments/base.py` | Re-exported as `_imon_trigger_level` from `gui/calibration.py`. Constant `_IMON_TRIG_SCALING_V_PER_UA` lives here too. |
| `imon_vertical_scale` | `experiments/base.py` | Re-exported as `_imon_vertical_scale` from `gui/calibration.py`. |
| `DEFAULT_RECORD_LENGTH` | `config.py` | Re-exported from `hardware/tektronix.py` for the `from ..hardware.tektronix import DEFAULT_RECORD_LENGTH` import in `gui/experiment_tabs.py`. |
| `DIGITAL_DELAY_US` | `config.py` | 1.2 µs. Applied ONLY when trigger source is EXT. |
| `IMON_SCALING_DEFAULT` / `_NIL` | `config.py` | 2.5e-3 / 1.0e-3 V/µA |
| `VMON_SCALING_DEFAULT` / `_NIL` | `config.py` | 0.25 / 1.0 V/V |
| `_safe_set_spinbox_value(sp, value)` | `gui/pattern_panel.py` | Used from `setup_tab.py` via deferred import to avoid pulling pattern_panel at import time. |
| `channel_in_view(channel, v_min, v_max, margin_divs=3.9)` | `hardware/base.py` Oscilloscope (default returns `None`) | Tektronix override in `hardware/tektronix.py` queries `CHx:SCAle?` + `CHx:POSition?`, computes visible window. Port of MATLAB `getWaveform2.m` in-view check. Simulator inherits the `None`-returning default. |
| `channel_is_clipped(channel, v_min, v_max, margin_pct=0.05)` | `hardware/base.py` Oscilloscope (default returns `None`) | Tek override computes the physical rail voltages (`±_half_vert_divs · vpd + vertPos`) and tests whether the observed (min, max) sits within `margin_pct` of either rail. Returns True for ADC saturation. The in-view loop uses this to decide between coarse-step UP (×2.5 V/div) and fine-fit. |
| `set_channel_scale_and_position_for_range(channel, *, v_min, v_max, divs=4.0)` | `hardware/base.py` Oscilloscope (default no-op) | Tektronix override in `hardware/tektronix.py` implements port of MATLAB `setFineScalePos2.m` (range/(2·divs) + mean-offset position). Base no-op so simulator-style drivers work without their own copy. |
| `set_channel_position(channel, divisions)` | `hardware/base.py` Oscilloscope (default no-op) | Tek override writes `CHx:POSition`. Same default-on-base pattern as the scale helpers. |
| `load_zero_unused_channels(pattern, config)` | `experiments/base.py` ExperimentRunner | Loads `pattern.scaled(0.0)` on every channel that is NOT `config.active` and NOT in `config.returns`. Returns left UNLOADED (passive sink). CG auto-skips. Called by VT, PS, SP, LP right after `load_channel(active, pattern)`. |
| `_crash_log_path()` | `run_gui.py` | Picks `<repo>/test/pulsar_launch_error.log` (when `test/` exists and is non-empty), else `<repo>/pulsar_launch_error.log`, else `~/.stimtest/`. **NEVER `%APPDATA%` or `%TEMP%`** — per user convention, error logs live in the project tree alongside bench-test session files. Don't reuse `gui.prefs.prefs_dir` here (it returns APPDATA, which is correct for prefs but wrong for error logs). |

---

## 8. Build & test workflow

```powershell
# Editable install
pip install -e ".[dev]"

# Full test suite (~369 tests, ~20 s — 2 pre-existing failures + 1 collection error in test_tektronix_probes.py)
python -m pytest tests/ -q

# One file, verbose
python -m pytest tests/test_metrics.py -v

# Run GUI without hardware
python run_gui.py --simulate

# Profile cold-launch imports
python -X importtime -c "import stimtest.gui.main_window" 2>&1 | sort -t'|' -k1 -n -r | head -30

# Build the Windows installer
python installer/build.py
```

Conventions:
- **Python floor is 3.10** (PEP 604 union syntax, `tomllib` in build
  script). `pyproject.toml` declares `requires-python = ">=3.10,<4"`.
- **Bundled runtime is Python 3.13**.
- **No third-party deps without checking `requirements.txt` first.**
  Adding a heavy dep (scipy / sklearn / matplotlib equivalent) must
  go through `installer/build.py` preflight + `StimulationTesting.spec`
  + `pyproject.toml`. matplotlib is **lazy** — keep it that way.
- **Keep the installer current with program edits** (operator: "Do
  always update the installer when we edit the program"). Per shippable
  change: bump the patch version in ALL THREE locations —
  `stimtest/__init__.py:__version__`, `pyproject.toml:version`, and the
  `installer/StimulationTesting.iss` `#define AppVersion` fallback
  (`build.py:assert_version_consistency` hard-fails if the first two
  drift). The `.spec` bundles the whole `stimtest/` package, so ordinary
  source edits need NO config change — only NEW bundled resources require
  a `[Files]`/`datas` entry. The actual `python installer/build.py` runs
  on a machine with PyInstaller + Inno Setup (ISCC.exe); an agent sandbox
  typically has neither, so it keeps version + config in sync and REMINDS
  the operator to rebuild rather than producing the `.exe` itself.
- **First REAL compile (2026-06, Inno Setup 6.7.3) surfaced latent `.iss`
  bugs** — the script had been kept "in sync" but never actually run
  through ISCC, so several errors only appeared on the first build. Fixed
  + recorded so they don't recur:
  * **`FILE_ATTRIBUTE_DIRECTORY` duplicate-identifier** — Inno 6.3+
    predeclares the Win32 file-attribute constants, so the script's own
    `const FILE_ATTRIBUTE_DIRECTORY = $10` collided. Renamed the private
    const to `FILE_ATTR_DIRECTORY` (+ its one use in
    `PlexonSdkFolderExists`) — a private name compiles on every Inno
    version. **Don't re-add a `const` named after a Win32 built-in.**
  * **Brace-constant inside a `{ }` comment** — an Inno `{ }` comment ends
    at the FIRST `}`, so `{tmp}` (or any `{...}`/`{ }`) written inside a
    brace comment terminates it early → "'BEGIN' expected" / "Syntax
    error". The `RunBundledInstaller` docstring now uses `(* *)` (where
    braces are inert). **Never write a brace-constant in a `{ }` comment**
    — reword, or use `(* *)`.
  * **`IsComponentSelected` → `WizardIsComponentSelected`** — deprecation
    HINT (still works, but renamed). Updated to the modern name.
  * **`installer/app.ico` is required for a release build** — the `.iss`
    `#error MISSING_APP_ICON` guard hard-fails without it. For a working
    build without the brand icon, pass **`/DBUILD_ALLOW_NO_ICON=1`** to
    ISCC (falls back to Inno's default icon). Commit a real `app.ico`
    before a branded release. The icon is gitignored/absent today.
  Build invocation that succeeds today (dist already frozen via
  `build.py --skip-installer`):
  `ISCC.exe /DAppVersion=<v> /DBUILD_ALLOW_NO_ICON=1 installer\StimulationTesting.iss`
  (cwd = repo root) → `installer\Output\PULSAR-Setup-<v>.exe` (~91 MB,
  compressed from the ~343 MB PyInstaller dist). Driver auto-install
  (Stim-2 / NI-VISA+NI MAX) only runs offline when `installer\prereqs\`
  holds `stim2-setup.exe` / `nivisa-setup.exe`; otherwise the wizard
  downloads Stim-2 and opens NI's page.
  - **Regular vs CWRU build (the CWRU profile is OPT-IN as of 0.2.13).**
    The DEFAULT build above is the REGULAR / PUBLIC installer
    (`PULSAR-Setup-<v>.exe`) and does NOT bundle the CWRU login profile —
    share it with anyone (a public install has no "cwru" login mystery).
    For the CWRU collaborator build, add **`/DWITH_CWRU_PROFILE=1`**:
    `ISCC.exe /DAppVersion=<v> /DBUILD_ALLOW_NO_ICON=1 /DWITH_CWRU_PROFILE=1
    installer\StimulationTesting.iss` → `installer\Output\PULSAR-Setup-<v>-CWRU.exe`
    (the `-CWRU` suffix keeps the two artifacts from colliding; the .iss
    `#ifdef WITH_CWRU_PROFILE` guards BOTH the `[Files]` profile line and
    `OutputBaseFilename`). `build.py`'s `run_inno` passes no flag, so a
    plain `python installer/build.py` now produces the PUBLIC installer.
  - **Code-signing (Authenticode) — the fix for AV false positives.** An
    UNSIGNED PyInstaller `.exe` is the #1 cause of Windows Defender /
    SmartScreen false positives ("contains a virus or potentially unwanted
    software") — the bootloader stub self-extracts + launches a bundled
    Python, which trips heuristic engines, and an unsigned binary has no
    publisher trust.  A real CWRU collaborator was BLOCKED by this (IT
    security got alerted).  `build.py` now signs OPT-IN via `--sign`:
    it signs the two app exes (`StimulationTesting.exe`,
    `StimulationTestingViewer.exe`) AFTER PyInstaller (so the installed
    binaries are signed too) AND the final installer AFTER Inno Setup, with
    SHA-256 + an RFC-3161 timestamp.  Certificate source (one of):
    `--cert-thumbprint <SHA1>` (cert in the Windows store — institutional
    cert / EV token / imported `.pfx`; RECOMMENDED), `--cert-file <pfx>`
    [`--cert-password`], or `--signtool-extra "/dlib … /dmdf …"` (Azure
    Trusted Signing).  Env equivalents: `PULSAR_SIGN_THUMBPRINT` /
    `PULSAR_SIGN_PFX` (+ `_PASSWORD`) / `PULSAR_SIGN_EXTRA` /
    `PULSAR_SIGNTOOL` / `PULSAR_SIGN_TIMESTAMP`.  `signtool.exe` is
    auto-detected from the Windows SDK.  `resolve_signing` fails FAST
    (before the long PyInstaller run) if `--sign` is given without a usable
    cert.  Dev builds stay unsigned (no `--sign` → no cert needed).
    Example signed build:
    `python installer/build.py --sign --cert-thumbprint <hex>` (add
    `--iscc …` if ISCC isn't on PATH).  Don't sign with UPX enabled (the
    `.spec` already has `upx=False` — UPX-packed exes flag far more).  The
    UNINSTALLER (`unins000.exe`, generated at install time) isn't signed by
    this path; if needed, use Inno's `SignedUninstaller=yes` + a registered
    `SignTool` — but a signed installer + signed app exes already clears the
    AV/SmartScreen block end users hit.

---

## 9. User-data file locations

**Configuration / state** lives in `%APPDATA%\StimulationTesting\`
(survives uninstall, follows the user across machines on a roaming
profile):
- `prefs.json` — GUI prefs (window geometry, last save dir, admin
  hash, pattern presets, …)
- `calibration.json` — per-stimulator I_mon gain/offset + V_mon
  scaling actual
- `electrode_potential_history.json` — E_ret rest-potential bin per
  coating

**Bench-test session files** live in `<repo>\test\` (per the
`reference_test_files_dir` memory — user's explicit convention):
- `*.npz` — per-run scope-capture arrays (V_mon, I_mon, E_act,
  E_ret time series)
- `*.txt` — per-session log (rotating buffer in LogPane; on-disk
  copy is line-buffered, append-only)
- `*.xlsx` — auto-exported metric tables
- `*.tif` / `*.png` — per-channel plot snapshots when the operator
  saved figures

**Crash / launch error logs** live in the project tree as well —
NEVER in `%APPDATA%` or `%TEMP%`:
- `<repo>\test\pulsar_launch_error.log` (preferred when `test/`
  exists and is non-empty)
- `<repo>\pulsar_launch_error.log` (fallback when `test/` is
  missing / empty)
- `~/.stimtest/pulsar_launch_error.log` (last resort if the
  project tree is read-only — e.g. a Program Files install)

The placement rule: error logs go where the operator already
looks for session data, so a crash trace lands in the same
directory they were opening to read `.npz` / `.txt` from a run.
Implemented by `run_gui.py:_crash_log_path()`.

The installer requires admin to write Program Files but the runtime
NEVER writes there. User-data path for prefs / calibration uses
`os.environ["APPDATA"]`; error logs use `_crash_log_path()`.

---

## 10. Profile login + admin password (plugin host architecture)

PULSAR ships with **two built-in profiles** (`Profile` enum in
`gui/admin.py`):

| Profile | Username | Password | Unlocks |
|---|---|---|---|
| `Profile.NONE` | — | — | (anonymous; default at startup) |
| `Profile.ADMIN` | blank or `admin` | `Neuron01` | Manage Custom Catalog **AND** any extension-registered restricted shapes |

**Admin password hash** is
`e6141f1cd2a7d087bbb3fd0cfc78934828160c04835ff434a9b40c258273f365`
(SHA-256 of `"Neuron01"`).  Stored in
`prefs["admin"]["password_hash"]`.  Legacy migration: the old default
`"admin"` (hash `8c6976e5…448a918`) is auto-upgraded to `"Neuron01"`
on first launch IF the user hadn't set a custom password.

### Extension profiles (plugin host)

Additional login profiles are registered at runtime by external
packages — the `stimtest.gui.admin` module is a **plugin host**.
Any installed Python package whose distribution name starts with
`stimtest_` (e.g. `stimtest_cwru`) is auto-discovered and imported
at `MainWindow.__init__` via `_load_extensions()` (uses
`importlib.metadata.distributions()` so editable installs work
too — NOT `pkgutil.iter_modules`, which misses PEP 660 editables).

Extensions register via:

```python
from stimtest.gui.admin import register_extension_profile

register_extension_profile(
    name="my_lab",                  # lowercase, becomes login username
    password_hash="<sha256 hex>",   # SHA-256 of the chosen password
    shapes={"halfpipe", "bowtie"},  # IDs unioned into RESTRICTED_SHAPES
    display_name="My Lab",           # optional human label for logs
)
```

The registration is **idempotent for same hash** and **raises
ValueError** on a same-name / different-hash collision (prevents
quiet clobber from a stray re-import).  Registering the reserved
built-in names `"none"` or `"admin"` raises.

### Restricted shapes

`stimtest.gui.admin.RESTRICTED_SHAPES` is a **mutable set** that
starts empty in a default install.  Extensions union their shape
IDs into it at registration time.  PatternPanel filters these out
of both the symmetric `shape_combo` AND the asymmetric mix-and-
match `mix_phase_shape_combo` dropdowns when the active profile
fails the `admin.is_restricted_unlocked(profile)` gate.  ADMIN
always passes the gate; any registered extension profile passes
the gate for its own session.

### Profile import / export (file-based, no pip install)

A profile is **pure data** — `{name, display_name, password_hash,
shapes}` where the shape IDs already exist as `SHAPE_*` constants in
`waveforms.py` (the extension only flips the visibility gate; the
geometry is public).  So profiles round-trip as JSON with **no code
execution**, unlike importing a `stimtest_*` Python package.

- **Admin → Import Profile…** (`MainWindow._on_import_profile`) — OPEN
  to all users.  Reads a `.json`/`.pulsarprofile` file, validates via
  `_register_profile_payload` (64-char hex hash; shapes must be a list;
  unknown shape IDs are filtered + warned, not fatal), calls
  `register_extension_profile`, persists to prefs key
  `imported_profiles`, and re-broadcasts the profile so panels +
  the login dialog pick it up.  Importing only ADDS the profile —
  logging in to USE it still needs the password.
- **Admin → Export Profile…** (`_on_export_profile`) — ADMIN-gated
  (enabled in `_set_profile` alongside the catalog).  Writes a chosen
  registered profile to JSON via `get_extension_profile`.  The file
  carries the **hash**, not the password, so distributing it is safe.
- **Per-profile shape tracking**: `RESTRICTED_SHAPES` is the global
  UNION and can't be decomposed, so `register_extension_profile` ALSO
  records each profile's own set in `admin._extension_shapes`;
  `get_extension_profile(name)` reads that for export.
- **Persistence**: imported profiles live in prefs `imported_profiles`
  and re-register at launch via `MainWindow._load_imported_profiles`,
  called AFTER `_load_extensions` so a same-name pip extension wins a
  hash collision.  Tests: `tests/test_profile_import_export.py`.

### Profile login flow

1. Admin menu → "Log In…" → `_LoginDialog`.
2. Dialog adapts:
   * **No extensions registered** → password-only (classic admin
     login UX; no Username field shown).
   * **Any extension registered** → Username + Password fields.
3. `prompt_login(parent, *, admin_hash)` resolves credentials to a
   `(profile_name: str, ok: bool)` tuple.  Blank or `"admin"`
   username takes the Admin path; any other username looks up in
   the extension registry.
4. `MainWindow._set_profile(profile_name)` updates the
   `_current_profile` STRING (not an enum — extension names aren't
   enum members), syncs the derived `_admin_logged_in` bool (for
   back-compat with the catalog dialog gate), and **broadcasts to
   every PatternPanel** via `pp.set_profile(name)` so the shape
   dropdowns rebuild.
5. The PatternPanel preserves the user's current shape selection
   when possible; if the selection becomes hidden (an extension
   user logs out while a restricted shape was selected), falls
   back to Rectangular and re-emits `patternChanged`.

### Login dialog text convention

The user-visible strings in `_LoginDialog` and the failure popup
mention **only the Admin profile**.  This is by design — extension
profiles are NOT advertised in the UI; collaborators receive the
credential separately along with the extension package.  A
public-facing PULSAR install has no extensions and thus no
restricted-shape "missing options" mystery for the operator.

### Admin catalog access

Manage Custom Catalog remains gated on `Profile.ADMIN` specifically
(via `is_admin(profile)`).  **Extension profiles do NOT inherit
admin rights** — `is_admin` returns False for them.  This is
asymmetric on purpose: an extension can unlock its own shapes
without also unlocking the catalog (which can change the admin
password).

### Where is the CWRU extension?

It lives in a **separate, private repository**:
`C:\Users\chris\OneDrive\GitHub\stimtest_cwru\` (sibling to this
repo, gitignored as `stimtest_*/` in `.gitignore`).  Distributed
to CWRU collaborators only; never pushed to the public PULSAR
repo.  See `EXTENSION_PLUGIN_DESIGN.md` (also gitignored) for the
distribution workflow.

---

## 11. Terminology & writing style

- Use **"neural interfaces"** as the umbrella; avoid BCI / BMI / BMBI
  (user finds them contested).
- For comparison tables: **every row needs a citation column.** Never
  drop the Source / Representative-paper(s) entry when summarizing.
- Cite preprints as "(preprint)" inline; re-check published status
  before manuscript submission.
- Prefer **maximum Q_inj / Q_stor / A_gsa** (explicit-quantity-name
  + subscript) over the relative-rate CIC / CSC / GSA acronyms.

---

## 12. Gotchas (sorted by how likely they are to trip you up)

1. **Don't add scope SCPI calls to `tektronix.open()`** for anything
   that experiments / calibration overwrite. The connect log is
   already labelled "Steps 1-6"; new commands should be tab-time, not
   connect-time.
2. **The calibration tab's `cal_vmon_combo` and `cal_imon_combo` are
   off-screen.** They exist as programmatic holders only — the
   "Oscilloscope setup" QGroupBox was removed. Don't try to lay them
   out again; channel choices come from the scope's `channel_aliases`.
3. **`_imon_trigger_level` / `_imon_vertical_scale` in
   `gui/calibration.py` are re-exports** from `experiments/base.py`,
   not local copies. Don't fork them again.
4. **`pyqtSignal.emit` from a non-Qt thread silently drops** in
   some PyQt6 builds we've hit. Always use
   `QMetaObject.invokeMethod` with `Qt.QueuedConnection` to invoke a
   GUI-thread slot from a `threading.Thread`.
5. **PyQt6 `QSpinBox.setValue` rejects `float`** — use
   `_safe_set_spinbox_value`, which auto-detects.
6. **MATLAB `setTriggerLevel.m` assumes 1 mV/µA**. The formula
   `(amp + 3.5) × 1 mV/µA` (small amp) / `amp × scale × 1 mV/µA`
   (large amp) IS faithful to MATLAB after the +4.5 → +3.5
   tightening. Don't reintroduce a `imon_v_per_ua` clamp — the right
   axis to fix is bandwidth.
7. **VT tab's Q_ph lock can lock `width_shared` invisibly** if the
   user saved prefs with `qph_lock_target=QPH_LOCK_WIDTH` from a
   prior Fixed-Q_ph session. The gate in
   `_on_pattern_amp_or_width_changed` / `_push_qph_to_pattern`
   prevents this; don't remove it.
8. **Don't rename `stimtest` → `pulsar` in the Python package.**
   On-disk prefs, installer AppId GUID, GitHub URL all bake in the
   legacy name. See `installer/README.md` rename ledger.
9. **The first pattern-preview render fires from `ensure_preview_
   rendered()`, called from TWO places — keep both.** Don't move it
   back to `__init__` or `singleShot(0)`; it was deliberately deferred
   for cold-launch speed (only the ACTIVE experiment's preview builds,
   and only once). The two triggers:
   * `_BaseExperimentTab.showEvent` — the EXPERIMENT-VIEW tab is shown.
   * `MainWindow._show_experiment` — this experiment's `params_page`
     (which actually HOSTS the preview) becomes the active "Test
     parameters" content.

   The second trigger is load-bearing: `params_page` was promoted to a
   SEPARATE top-level "Test parameters" tab (re-parented by
   `_show_experiment`), so the experiment-view `showEvent` never fires
   for a user who lands on Setup / Test parameters at launch (the launch
   default focuses Setup). Without the `_show_experiment` call the
   preview is stuck on "No pulse pattern set." with every pulse field
   populated. `ensure_preview_rendered()` is idempotent (`_first_show_
   done`) and leaves the flag False if the render raises (retry on next
   show, don't wedge the banner). On the launch path `_show_experiment`
   runs AFTER `restore_prefs`, so it renders the restored values.
   Tests: `tests/test_preview_first_render.py`.
10. **Don't stop stim between iterative re-captures in VT.**
    `_one_capture` runs the first capture, then up to 2 more inside
    the in-view rescale loop. ALL of those captures must see live
    pulses, so the entire block is wrapped in ONE outer `try /
    finally` with `stop_channel` only on the way out. A previous
    revision had a `finally` right after the first capture; every
    iterative re-capture then acquired the noise floor, the
    fine-scaler sized V/div for that noise, and V_mon clipped to
    ~±25 mV regardless of the programmed amplitude. The user-spec
    quote: *"You can stop pulsing after the waveform is acquired,
    but you then need to stimulate again when trying to get a new
    waveform."*
11. **Unused channels need a zero-amplitude same-duration pattern —
    NOT to be left unloaded.** Three-state channel handling at every
    pattern load: active = real pattern; returns = unloaded; unused
    = `pattern.scaled(0.0)` so the channel ticks in cadence with
    the active pulse cycle but delivers no current. Use the
    `load_zero_unused_channels(pattern, config)` helper on
    `ExperimentRunner` right after `load_channel(active, pattern)`.
    Easy to forget when adding a new experiment runner; the helper
    is a single line. CG configs auto-skip because their `returns`
    span every other channel (unused set is empty).
12. **MATLAB's V/div default-view rule has 4 cases, not 2.** The
    full `setOscillocopeView.m` matrix is MP×multipolar crossed
    with long-phase×short-phase + animal-environment override.
    `apply_default_scope_view` takes `is_multipolar` and
    `environment_short` kwargs from the caller. VT reads
    `config.returns` (non-empty → multipolar) and
    `extras["setup_snapshot"]["environment_short"]` and passes both
    in. Don't fall back to the MP-only branch — multipolar configs
    need 2.0 V/div (long phase) or 0.5 V/div (short phase) to keep
    the leading edge on screen.
13. **The pulse-pattern preview discharge label has TWO placement
    modes** keyed on `last_dd >= 1 ms`. Below the threshold: diagonal
    arrow with `dx_mag` capped at 80 µs. At/above: `skip_arrow=True`,
    text pinned near the band start. Without these caps a multi-ms
    discharge delay produces a multi-ms arrow that crosses the entire
    plot and lands the text endpoint inside the interphase-delay
    label. See `pattern_preview.py` discharge block.
14. **PULSAR.vbs uses 4-quotes-on-each-side per command string.**
    Every constructed-command line has exactly 8 double-quotes
    total (4 + path + 4). Odd-quote counts fail VBScript
    compilation with "Unterminated string constant" — the launcher
    then silently does nothing. The pattern is
    `"prefix """ & path & """"` (open + `""` literal + close on
    each side).

    **PULSAR.vbs's PyQt6 probe MUST use `shell.Run(cmd, 0, True)`
    — NOT `shell.Exec(cmd)`.**  Exec on a windowless launcher
    (`pyw.exe`) returns unreliable exit codes because the child
    detaches in a way that defeats the exit-code read; the probe
    would say "no PyQt6" even when PyQt6 was installed, the VBS
    would fall through to plain `pythonw.exe` on PATH, and on
    machines where that's Python 3.14 the GUI launched into a
    broken / non-existent PyQt6 install. `shell.Run` with
    `windowStyle=0` (hidden) and `waitOnReturn=True` is BOTH
    synchronous AND silent AND gives a reliable exit code — use
    that. The probe runs `py.exe` (console variant); the actual
    GUI launches under `pyw.exe -<version>` (windowless) after the
    probe selects.

    PULSAR.vbs also writes a per-launch diagnostic line to
    `<repo>\test\pulsar_launcher.log` (or `<repo>\pulsar_launcher.log`)
    recording which Python was probed, which one was selected, and
    a timestamp. Read this when the silent VBS launcher "did
    nothing" — without it the only way to diagnose was to switch
    to the visible PULSAR.bat and watch the console.
15. **Explicit `stop_all` before every `load_channel` — not just in
    a previous-iteration `finally`.** `PS_LoadChannel` for an
    arbitrary pattern is a 50-200 ms USB upload; the stim MUST be
    quiescent during that window. Relying on the prior iteration's
    finally is fragile (early return / exception path skips it),
    so every load sequence starts with an explicit `stop_all()`
    wrapped in try/except. Also: prefer `stop_all` over
    `stop_channel(active)` because `start_all` brings up the
    unused zero-amplitude channels too, and `stop_channel` only
    quiets the active. Matches MATLAB `stopStimulation()` →
    `PS_StopStimAllChannels` exactly. See VT `_one_capture`, PS
    amplitude loop, LP post-characterize restart, and LP
    `_characterize` for canonical use sites.

    **`stop_all` is IDEMPOTENT — it no longer double-fires
    `PS_StopStimAllChannels`** (operator: "why do you
    stop_stim_all_channels twice?"). The per-capture `finally`
    stop + the next step's explicit pre-load stop previously
    issued TWO back-to-back DLL calls between steps. `PlexonStimulator`
    now tracks `_is_running` (set by `start_all`/`start_channel`,
    cleared by `stop_all`/`abort_all`/`open`/`close`); `stop_all`
    early-returns when `_is_running` is False, so the redundant
    second call is a silent no-op. The explicit pre-load stop STILL
    fires when the `finally` was skipped (running would still be
    True), so the safety above is intact. Invariant: `_is_running
    == False` ⇒ device genuinely stopped (only real-stop paths
    clear it, any start sets it), so a skipped stop can never leave
    the device live. **Don't bypass the guard** by calling
    `self._lib.ps_stop_stim_all_channels` directly — route through
    `stop_all` so `_is_running` stays coherent. Tests:
    `tests/test_plexon_stop_idempotent.py`.
16. **The Python interpreter is selected at the LAUNCHER level, not
    runtime.**  Both PULSAR.bat and PULSAR.vbs probe `py -3.13`
    first, then `-3.12`, `-3.11`, `-3.10`, then `py -3`, then plain
    `python`/`pythonw` on PATH — running `<py> -c "import PyQt6"`
    against each and accepting the first that exits 0.  First version
    with a working PyQt6 import wins.

    **Why launcher-level, not runtime-level**: `run_gui.py` ALSO has
    a `_find_python_with_pyqt6()` self-rescue (see
    `_relaunch_with()`), but that only catches Python-level
    `ImportError`.  A C-level crash inside a native module (PyQt6,
    numpy, plexon DLL) bypasses the Python try/except entirely —
    exit code `-1073740940` / `0xC0000374` =
    `STATUS_HEAP_CORRUPTION` — and the rescue never gets a chance to
    re-exec.  PyQt6 wheels for Python 3.14 are very new and on some
    installs heap-corrupt during module init; the only reliable fix
    is to not start that interpreter at all.

    The runtime self-rescue is still present as a safety net for
    `ImportError`-level failures (e.g. when the user runs
    `python run_gui.py` directly from a venv without PyQt6).  It
    uses `PULSAR_RESCUED=1` in the child's env to prevent infinite
    recursion if the child also fails — **don't remove that env-var
    check**.

    The crash-handler tk dialog in `_report_startup_error` includes
    targeted advice for `ImportError` mentioning `PyQt6`, pointing
    at `py -3.13 -m pip install PyQt6`.

    PULSAR.bat additionally detects the `-1073740940` exit code on
    its own and prints HEAP_CORRUPTION-specific guidance (don't look
    for the launch error log; try a different Python version).
17. **Error / crash / launch logs go in the PROJECT TREE, not
    `%APPDATA%` or `%TEMP%`.** Per user convention (see section 9
    and the `reference_test_files_dir` memory): error logs land
    next to bench-test session files so the operator finds them in
    the same directory they're already opening to read `.npz` /
    `.txt` from a run. Resolution order in
    `run_gui.py:_crash_log_path()`:
    1. `<repo>/test/pulsar_launch_error.log` — preferred when
       `test/` exists and is non-empty (active bench-testing).
    2. `<repo>/pulsar_launch_error.log` — fallback when `test/`
       is missing / empty.
    3. `~/.stimtest/pulsar_launch_error.log` — last resort only
       when the project tree refuses writes (Program Files
       install, read-only mount).

    **`%APPDATA%` and `%TEMP%` are explicitly excluded.** Don't
    "consolidate" error logs into `prefs_dir()` thinking it's
    cleaner — the user told us not to. Prefs / calibration /
    electrode-potential-history STILL live in `%APPDATA%`; only
    error logs move out.

    Don't reuse `stimtest.gui.prefs.prefs_dir` from
    `_crash_log_path` either: (1) it returns `%APPDATA%` which
    breaks the rule, and (2) importing `stimtest` is exactly what
    may have just failed, so the crash handler has to work without
    it. PULSAR.bat's failure-pause echo and PULSAR.vbs's launch-
    error docstring both reflect the project-tree convention too —
    keep them in sync if you change `_crash_log_path()`.
18. **`faulthandler` is enabled at `run_gui.py` import time** with
    output to `<repo>/test/pulsar_faulthandler.log` (or
    `<repo>/pulsar_faulthandler.log` when `test/` is empty). This
    catches C-level crashes (HEAP_CORRUPTION inside PyQt6 / numpy
    / plexon DLL — exit code -1073740940 with no Python exception)
    by writing a Python stack frame to the log file BEFORE the
    process gets fully cleaned up. Without it, the regular crash
    handler can't see SIGSEGV / SIGABRT and the session log just
    abruptly ends without a trace. The handler is armed via
    `_enable_faulthandler()` called at module top level (BEFORE
    any other import) so it's live during PyQt6 / numpy / scope-
    driver imports — the most common crash sites. The file handle
    is intentionally leaked into a module global so the GC can't
    close it.
19. **`LogPane.log()` does explicit `flush()` + `os.fsync()`
    per line write.** The on-disk session log is opened
    line-buffered (Python flushes on `\n`), but the OS page cache
    holds those writes for seconds. When the GUI dies abruptly
    (HEAP_CORRUPTION inside a native module), the kernel doesn't
    always flush before process cleanup, and the LAST few logged
    scope SCPI commands that would pinpoint the crash site never
    reach disk. Explicit fsync per line costs ~1 ms but guarantees
    the on-disk log shows everything up to the very last write —
    essential for post-mortem analysis of C-level crashes.
20. **Slow scope operations (`set_record_length`,
    `set_acquisition_mode`) emit BEFORE/AFTER heartbeat log lines**
    via the scope's `_log()`. On TBS2000-series with large records
    (20k points), the SCPI write itself returns in ~3 ms but the
    scope INTERNALLY re-allocates its capture buffer for 10-30 s
    AFTER the OPC response. Without the heartbeat, the operator
    sees `[scope] > HORizontal:RECOrdlength 20000` then 20 s of
    silence and assumes the GUI hung. The post-op log line
    includes the total elapsed time so the operator can verify
    the gap matches the expected scope latency, not a Python
    hang.
21. **Don't roll a fresh `_major_only` `tickValues` override —
    use `_make_matlab_tick_override` from `widgets.py`.** The
    naive `return levels[:1] if levels else levels` shim reverts
    to pyqtgraph's denser default step (200 instead of MATLAB's
    500 for a 0-2000 µs range), producing 11 ticks instead of 5.
    The user explicitly compared the experiment plot to the
    calibration / MATLAB plots and found the dense layout wrong.
    `_make_matlab_tick_override` rounds the step to a multiple of
    `{1, 2, 2.5, 5} × 10ⁿ` targeting ~5 ticks — the MATLAB
    `xlim`-style layout.  Calibration + ScopePlot + the inset
    axes all use the shared helper; any new plot widget should
    too.
22. **Time-axis t=0 = the TRIGGER, located in the record from the
    HORIZONTAL POSITION on percent-position models (Method P); the
    `XZEro` readback is authoritative only on the legacy time-position
    families.** Operator rule: "The trigger location is where zero is.
    You need to account where zero is based on trigger percentage on
    [record] length or trigger horizontal position time."

    `_read_channel`'s decision tree:

    1. **Percent-position model (TBS2000\*) → Method P**:
       `xzero = −(position% / 100) × record_length × XINcr`, position
       from the cached `_expected_horiz_position_pct` (query fallback).
       The TBS2204B firmware reports `XZEro = −record/2`
       (trigger-symmetric) REGARDLESS of the programmed position —
       confirmed by arithmetic on the operator's session: position
       20 % of a 20k × 32 ns record → true zero −128 µs; with the
       firmware's −320 µs the pulse onset DISPLAYED at −210 µs even
       though it sits at the trigger on the scope screen.  A ⚠
       cross-check line logs every disagreement between the readback
       and the position-derived zero.
    2. **Time-position model (TBS1104B / TDS / TPS), `XZEro`
       non-zero** → use the readback (there record = screen and XZEro
       IS the position expressed as time — exactly what the
       operator's MATLAB `getSettings.m` 'time' case relied on).
    3. **`XZEro` exactly zero AND `PT_Off` non-zero** → PT_Off-derived
       `xzero` (legacy TDS1000-era XZEro=0 quirk).
    4. **Both zero** → no pre-trigger; `t_us` starts at 0.

    History: an EARLIER position-derived attempt was reverted with the
    claim "TBS2000 captures records symmetric around the trigger
    regardless of POSition" — that conclusion mistook the firmware's
    misreported XZEro for ground truth (the GUI symptom it 'fixed'
    was real, the explanation wasn't).  Don't re-revert Method P based
    on the XZEro readback alone; the scope SCREEN (where the pulse
    sits relative to the T marker) is the ground truth.  "Trust
    PT_Off over XZEro on disagreement" remains wrong (TBS2204B
    PT_Off always reports 0).
23. **Two `QMediaRecorder` signals are toxic in PyQt6 — don't
    connect to either.**

    * `recorderStateChanged(RecorderState)` — Python connect
      succeeds, but Qt's C++ bridge rejects with `No such signal
      QMediaRecorder::recorderStateChanged(RecorderState)` because
      the namespace qualifier is missing.  Dangling slot ptr; the
      SECOND state transition corrupts the heap
      (STATUS_HEAP_CORRUPTION 0xC0000374).
    * `errorOccurred(Error, QString)` — raises immediately at
      connect time with `TypeError: connect() failed between
      (QMediaRecorder::Error,QString) and unislot()`.  The exception
      aborts `connect_to`, so the camera **never opens at all** —
      every Connect press logs `Camera connect FAILED: TypeError:
      …` and the preview never appears.

    Probe (`py -3.13 ...`):
    ```
    QCamera.errorOccurred           connect OK
    QImageCapture.errorOccurred     connect OK
    QMediaRecorder.errorOccurred    FAILED: TypeError (QMediaRecorder::Error,QString)
    QMediaRecorder.errorChanged     connect OK
    ```

    `QCamera.errorOccurred` and `QImageCapture.errorOccurred` bridge
    cleanly so we keep those.  For the recorder, use the no-arg
    `errorChanged` signal and pull details from `recorder.error()` /
    `recorder.errorString()` inside the slot — same diagnostic info,
    no broken bridge.

    For state changes, we don't connect at all — `stop_recording`
    queries `recorder.actualLocation()` synchronously and emits
    `captureSaved` from there.  PyQt6 exposes `recorderStateChanged` as a
    `pyqtSignal` so `mr.recorderStateChanged.connect(slot)` LOOKS
    like it works, but Qt's C++ side rejects the bridge with:

    ```
    qt.core.qobject.connect: QObject::connect:
        No such signal QMediaRecorder::recorderStateChanged(RecorderState)
    ```

    See the consolidated note above — `recorderStateChanged` and
    `errorOccurred` are BOTH broken on PyQt6.  Use the no-arg
    `errorChanged` signal + state-poll for error reporting; for
    save-path notification call `actualLocation()` synchronously
    after `stop()` returns.
24. **`CameraStreamPane` in an experiment tab needs an explicit
    minimum height + a splitter-resize hook** so it actually
    appears below the scope when the camera connects.  `QSplitter`
    doesn't redistribute heights when a child flips from
    `setVisible(False)` to `setVisible(True)` — it preserves
    whatever sizes it had with the hidden child at 0.  Without the
    hook the operator sees a green "LIVE PREVIEW" badge with NO
    video below it.

    `_BaseExperimentTab` connects to
    `camera_service().connected` / `.disconnected` and calls
    `_on_camera_service_connected` / `_on_camera_service_disconnected`
    which force `_scope_cam_split.setSizes([scope_h, cam_h])` with a
    3:1 scope:camera ratio (with `cam_h` floored at the pane's
    `minimumHeight()`, set to 220 px in the constructor).  Result:
    on first connect, the splitter divides into roughly `[260, 220]`
    on a 480 px total area — pane is visible at its minimum and the
    operator can drag the divider to give it more.
25. **`QMediaDevices.videoInputsChanged` requires a
    `QMediaDevices()` INSTANCE — connecting via the class raises
    `AttributeError`.**  PyQt6 exposes the class-level
    `QMediaDevices.videoInputsChanged` as a `pyqtSignal`
    descriptor with NO `.connect()` method; only an instance gives
    you a `pyqtBoundSignal` you can subscribe to.  Symptom of
    forgetting this (silently caught `AttributeError`): the
    operator plugs in a USB camera AFTER PULSAR launches and the
    combobox never auto-refreshes — they have to click Refresh
    manually even though the OS reports the camera immediately.

    `CameraService._ensure_qtmm` builds the instance
    (`self._qmedia_devices = QMediaDevices(self)`) on first need,
    connects `videoInputsChanged` to a forwarding slot, and
    re-emits as `CameraService.devicesChanged` (a regular
    `pyqtSignal` on a real `QObject`).  UI consumers subscribe to
    `devicesChanged` instead of poking the static QMediaDevices
    signal directly.

    `CameraConnector` also stages a multi-shot retry sequence
    (`singleShot(0, 250, 1500)` ms) for the initial enumeration
    because FFmpeg's USB-backend init takes a variable amount of
    time after `QApplication` starts — a single `singleShot(0)`
    sometimes fires before enumeration completes, leaving "No
    cameras detected" stuck even when the camera IS plugged in.
26. **Scope setup runs on the GUI thread and pumps the event loop
    via `_tick()` to keep the GUI responsive.** When the operator
    presses Start, `_start_runner` reads from the Setup tab widgets
    and configures the scope synchronously BEFORE building the
    `RunnerWorker`. The TBS2204B's
    `HORizontal:RECOrdlength 20000` write returns in ~3 ms but
    triggers an internal 10-30 s buffer reallocation; the NEXT
    SCPI call (`set_acquisition_mode`) blocks waiting for the
    reallocation to finish. Without event-loop pumping, the entire
    GUI freezes for that whole window — no log-pane redraw, no
    button responsiveness — even though the on-disk session log
    (line-buffered + fsync) is being written in real time.

    Pattern: define a local `def _tick(): QApplication.processEvents()`
    at the top of the scope-setup try block, then call `_tick()`
    after every `_log(...)` line AND after every slow scope call
    (`set_record_length`, `set_acquisition_mode`, `set_trigger`,
    `auto_layout_for_pulse`, etc.).  Status-bar message
    "Configuring scope (this can take 10-30 s on TBS2000 with
    large records)…" is shown for the duration and cleared right
    before `_worker_thread.start()` so the operator knows what's
    happening.

    Don't move the `_tick()` calls into the scope driver methods —
    `hardware/tektronix.py` shouldn't depend on QApplication.  Keep
    the pump on the GUI-thread caller side.  A proper fix would
    move scope setup to a worker thread, but the signal-wiring
    cost is much higher than this one-line pump and the symptom
    (frozen GUI for 20 s) is gone with `_tick()` alone.
27. **Long Pulsing snapshot captures drop their raw arrays after
    metrics are computed.** If a future feature needs the raw
    samples (e.g. re-running metrics with a new algorithm), the
    `trim_snapshot_arrays=False` opt-out is there for that.
28. **`_start_runner` re-entry causes PlexStim DLL HEAP_CORRUPTION
    — keep `_start_in_progress` guard at the top.** The scope-setup
    block (gotcha #26) pumps the event loop via `_tick()`.  A
    queued double-click on Start that lands during a `_tick()` call
    re-enters `_start_runner` BEFORE the Start button has been
    disabled (the disable happens AFTER scope setup, ~lines past
    the `_tick()` calls).  Both invocations then race through the
    PlexStim DLL — `stim.reinit` → `PS_CloseAllStim` from one
    thread, `single_capture` → preamble query from the other —
    and the heap corrupts:

    ```
    Thread d560: voltage_transient.run → reinit → ps_close_all_stim
    Thread 4568: voltage_transient.run → _one_capture → single_capture
    PULSAR exited with code -1073740940 (0xC0000374 = STATUS_HEAP_CORRUPTION)
    ```

    (Caught by `faulthandler`, see gotcha #18.)

    The fix in `_start_runner` is a two-line guard at the very top
    of the method — BEFORE any `_tick()` / `processEvents` / modal
    dialog:

    ```python
    if getattr(self, "_start_in_progress", False):
        self.log_pane.log("Start re-entry blocked …")
        return
    self._start_in_progress = True
    ```

    Cleared in `_on_finished` (normal exit) and in the wrapper
    `_start_runner`'s except clause (setup failed mid-stride).
    The body is split into `_start_runner_body` so the wrapper can
    own the flag-clear + UI-rollback without indenting ~450 lines
    inside a try block.

    **Don't remove the guard even if you "fix" the underlying race
    by moving scope setup off the GUI thread** — the guard is the
    correctness guarantee; the button-disable is just a UX hint.
    PyQt6's signal queue can still land a re-entry from any source
    that briefly enables the button (programmatic Start calls,
    Stop→restart flows, the Run Calibration → finish sequence).
29. **Every Setup-tab / PatternPanel input change emits a
    "Setup: <field> = <value>" log line — gated by
    `_log_setup_changes`.**  User-spec quote: *"Remember to print
    everytime something is inputted or changed, including setup
    channels, parameters, etc."*  The implementation lives in
    `gui/main_window.py`:

    * `self._log_setup_changes = False` set early in `__init__`
      (before signal wiring) — gates the helper so the startup
      signal burst from `_load_prefs_into_tabs()` doesn't flood
      the log with restored values the operator didn't touch.
    * Flipped to `True` at the END of `__init__` after the prefs-
      restore + downstream-tab priming calls finish.
    * `_log_setup_change(msg)` emits exactly one line with the
      "Setup: " prefix when the gate is open; no-op otherwise.
    * Every `_on_*_changed` forwarder for a Setup-tab signal
      (`arrayChanged`, `environmentChanged`, `acquisitionChanged`,
      `triggerSourceChanged`, `digitalTriggerChanged`,
      `aliasesChanged`, `potentialLimitsChanged`, `savePathChanged`,
      `sessionFilenameChanged`, etc.) calls `_log_setup_change`
      at the top, BEFORE the forwarding loop.
    * Log-only signals with no other subscriber
      (`spargeGasChanged`, `sameAreaChanged`) are wired with a
      lambda-based connection that just calls the helper.
    * `_on_pattern_changed_log` slot connects to every experiment
      tab's `patternChanged` signal, debounced at 400 ms via a
      single-shot QTimer so a spinbox-tick burst collapses into
      one final "Setup: pattern = …" line.
    * `_log_initial_setup_snapshot()` fires once at the end of
      `__init__` to record the resolved post-restore state
      (array, environment, acquisition, aliases, operator,
      session subject) — without this, the session .txt log
      starts mid-stream with runner messages and there's no
      record of what was in effect at run start.

    **Adding a new Setup-tab signal**: connect it to a new
    `_on_*_changed` forwarder OR a log-only lambda, and call
    `_log_setup_change` from inside.  **Don't** add `log_pane.log`
    calls inside SetupTab itself — SetupTab doesn't own a
    LogPane reference (deliberately, to keep it standalone-
    testable), and direct `log_pane.log` calls there would
    bypass the prefs-restore gate.

29b. **Stop tears down the stim; Start re-initializes it
    (user-spec lifecycle).**  Quote: *"When you stop, abort
    stim and close the stimulator.  When you restart,
    initialize the stimulator."*  Implemented in
    `gui/experiment_tabs.py`:

    * **`stop_clicked`** sets the runner's abort flag (existing),
      then immediately calls `self._stim.abort_all()` (= PS_AbortAll,
      DLL-safe in any trigger mode, halts a pulse mid-flight),
      then sets `self._stim_needs_close_after_run = True`.  The
      actual `close()` is DEFERRED to `_on_finished` — closing
      here would race the runner's final DLL call (its `finally`
      block's `stop_all`, which may still fire as the runner
      unwinds from a slow USB-TMC read).
    * **`_on_finished`** does `self._worker_thread.wait()` first
      (guarantees the runner thread is fully dead), then if
      `_stim_needs_close_after_run` is set, calls
      `self._stim.close()` (= PS_CloseAllStim) and flips
      `self._stim_needs_init = True`.
    * **`_start_runner_body`** at the top, AFTER the re-entry
      guard but BEFORE the scope setup, checks
      `_stim_needs_init`.  If set, calls `self._stim.open()`
      (= PS_InitAllStim with `ps_close_all_stim()` prelude) and
      clears the flag.  A failure raises through to the wrapper's
      except clause, which rolls back the UI and surfaces an
      operator-facing message.

    **Why this sidesteps gotcha #28's HEAP_CORRUPTION**: the
    re-entry guard prevents the GUI-side race (two
    `_start_runner` calls), but the device-side race
    (close-while-pulsing inside the runner's own
    `self.stim.reinit()` at the top of `run()`) was the second-
    order failure mode the user hit after Stop → Start.  This
    spec eliminates that race at the source: by the time the
    runner calls `reinit()`, the device has already been
    `abort_all`'d + `close`'d + freshly `open`'d by the GUI, so
    `reinit()`'s internal `close()+open()` lands on a known-
    quiet device with no in-flight pulse.

    **Idempotent on the "no Stop pressed" path**: a normal
    end-of-run does NOT set `_stim_needs_close_after_run` (only
    `stop_clicked` does), so `_on_finished` skips the close
    and the next Start press skips the re-init.  The stim stays
    initialized across back-to-back normal runs, matching the
    pre-change behaviour.

    **Don't bypass the wait()**: the close()-after-wait() order
    in `_on_finished` is load-bearing.  Calling close() before
    the runner thread is dead lets the runner's final DLL call
    land on a closed device — the `_dll_lock` (RLock) prevents
    a HEAP race, but the runner's `stop_all` in its `finally`
    will raise and pollute the log with a spurious
    "device not initialized" error.  The wait() is unbounded
    because the GUI is supposed to be a quiet bystander between
    Stop and the runner's natural exit.

29d. **Multi-config runners (VT) MUST set
    `self.current_configuration = config` per iteration** so each
    capture lands on the right page in the Experiment-tab's
    per-channel list.  Without this, `_capture_key` (in
    `gui/experiment_tabs.py`) falls back to
    `runner.session.test.configuration` — a STATIC reference to
    the FIRST configuration set at runner construction — and
    every capture from every subsequent config gets routed to
    the first config's page.  Symptom: operator sees one plot
    that gets replaced with each new channel's data; configs 2+
    never appear as their own rows in the entry list; their
    metrics never make it into the side panel.

    Fix in `voltage_transient.run()`:
    ```python
    self.current_configuration = None
    for cfg_idx, config in enumerate(self.configurations):
        self.current_configuration = config
        # ... rest of loop ...
    ```

    `_capture_key`'s lookup order (`current_configuration`,
    `configuration`, `session.test.configuration`) means
    single-config runners (PS, SP, LP) keep working unchanged
    because they set `self.configuration` (singular) at runner
    construction and the second tier hits.  Only multi-config
    sweeps (VT) need the per-iteration update.

    **Companion fix in `_on_finished`**: mark EVERY ChannelRun's
    config as ✓ in the entry list, not just
    `session.test.configuration`.  Iterate
    `result.session.runs` and call `mark_completed` for each
    `run.configuration.display_name()`.  Falls back to the
    static config only when `runs` is empty (preflight failure).

    **LIVE per-config ✓ (operator: "I do not see check marks by the
    channel/combo … to indicate that they are complete").**  The
    `_on_finished` pass only marks ✓ at the END of the whole run — so a
    long MULTI-config VT sweep showed NO completion marks until the entire
    sweep finished (each channel runs inside ONE runner; only `_on_finished`
    fired).  Fix: `RunnerWorker` emits a `run_completed(key)` signal on each
    `run_end` event (keyed by `str(ev.run.configuration.display_name())`,
    same key as `captured`/`_on_capture`), wired to
    `_BaseExperimentTab._on_run_completed` → `multichan_scope.mark_completed`.
    So each channel/combo ticks ✓ the moment ITS config finishes, mid-sweep.
    `mark_completed` is idempotent (set-backed), so the end-of-run
    `_on_finished` re-mark is harmless.  SP/CP (separate chained runners)
    already marked per-config at the chain point; this covers VT's
    single-runner multi-config sweep.  Tests:
    `tests/test_vt_lock_enable.py` neighbours + the `_on_run_completed`
    path.

29c. **`PlexonStimulator` tracks device-level open/close state via
    `_is_open` to break the `ps_close_all_stim` cascade.**  The
    vendor DLL HEAP_CORRUPTs (Windows 0xC0000374) when
    `ps_close_all_stim` fires multiple times in ~1 second.  Pre-
    fix, the GUI's Stop / Start lifecycle plus the runner's
    start-of-run `reinit()` produced FOUR calls per cycle:

    1. GUI `_on_finished` close() — `ps_close_all_stim` #1
    2. GUI `_start_runner_body` open() — open() internally calls
       `ps_close_all_stim` again before `ps_init_all_stim` (#2)
    3. Runner `reinit()` at top of `run()` — close() (#3)
    4. Runner `reinit()` — open() embedded close (#4)

    The 4th call crashed the DLL.  Two fixes in concert:

    * **`_is_open` flag in `PlexonStimulator`** (set after
      successful `ps_init_all_stim`, cleared in `close()` and at
      the top of `open()`).  `open()` skips its internal
      `ps_close_all_stim` when `_is_open == False`; `close()`
      skips when already `False`.  Result: a Stop → Start cycle
      that previously fired calls #1+#2 now fires only #1
      (Start's open sees `_is_open=False` and skips).

    * **Runner start-of-run `reinit()` REMOVED** in all four
      runners (VT / SP / PS / LP).  The GUI Stop / Start
      lifecycle now guarantees a fresh device, and stale
      patterns from a previous run are overwritten by the
      `_one_capture`'s `load_channel` before stim starts.  Per-
      config reinit BEFORE multipolar configs (VT line ~291)
      is KEPT — multipolar requires `returns` channels
      unloaded, which is what reinit accomplishes.

    Net per Stop/Start cycle: **1** `ps_close_all_stim` + **1**
    `ps_init_all_stim` (down from 4 + 2).

    **Don't bypass the `_is_open` guard by calling
    `self._lib.ps_close_all_stim()` directly.**  One legitimate
    bypass exists in `open()`'s auto-recover-from-Plexon-GUI-
    lock branch (line ~333) — that path force-resets after
    killing the Plexon GUI, and the explicit close is required
    to fully drop the USB handle the GUI was holding.  Any new
    bypass must update `_is_open` consistently.

30. **`tektronix.open()` auto-retries the libusb-win32 stale-
    handle error** ("The semaphore timeout period has expired" via
    `libusb0-dll:err [control_msg]`).  Trigger: a previous PULSAR
    process — typically one that crashed mid-run (gotcha #28 /
    HEAP_CORRUPTION) — left the scope's USB control endpoint
    claimed at the kernel level.  Windows eventually drops the
    claim, but the operator hits Connect long before that happens.

    Retry policy in `open()`:
    * **3 attempts total** with **1 s / 2 s / 4 s exponential
      backoff** between them.  Total worst-case wall time ≈ 7 s,
      bounded so a genuinely broken device still fails fast.
    * Retries fire ONLY when the exception's repr matches the
      libusb signature (`_is_libusb_stale_handle` — checks for
      `"semaphore timeout"`, `"libusb0-dll:err"`, or `"libusb"` +
      `"control_msg"`).  Non-libusb errors (bad resource string,
      missing device, scope-side SCPI violation) propagate
      immediately — no point retrying those.
    * Between retries: `_release_partial_visa_session()` closes
      the half-opened VISA handle + clears the resource cache so
      the next `_pick_resource()` re-enumerates fresh.
    * After all 3 fail: raises `RuntimeError` with operator-
      facing guidance baked into the message ("Power-cycle the
      scope + replug USB, then retry") rather than the cryptic
      pyvisa traceback.
    * Success logs `✓ recovered on attempt N/3 after the libusb
      stale-handle cleared` so a post-mortem can confirm the
      retry was the fix vs. a coincidental scope restart.

    **The retry MASKS the symptom; the underlying race is the
    HEAP_CORRUPTION in gotcha #28.**  Don't relax the
    `_start_in_progress` guard thinking the retry covers it —
    the retry has a 7 s budget, while the kernel's stale-handle
    garbage-collection can take minutes if the dead process is
    still holding open file handles to the libusb backend.  Both
    fixes are needed: prevent the crash, AND recover from any
    stale handle that slipped through.
31a. **PyQt6 `UniqueConnection` RAISES on duplicate connect — not a
    silent no-op.**  Hit while building `BiasConnector.set_host`
    (an idempotent setter that calls
    `signal.connect(slot, Qt.ConnectionType.UniqueConnection)` a
    second time with the same args).  In Qt's C++ semantics
    UniqueConnection returns `false` when the slot is already
    connected.  PyQt6's binding surfaces that as a Python exception
    (TypeError-shaped, message includes `"connect() failed between
    …"`).  If the surrounding code catches `Exception` (e.g. to
    fall back gracefully when the host doesn't expose the expected
    signal), the duplicate-connect raise looks indistinguishable
    from a real wiring failure — the code silently nulls out the
    host reference and the next signal emit goes nowhere.

    Fix: short-circuit the idempotent path with
    `if self._host is host: return` BEFORE the disconnect / re-connect
    dance.  Saves the wasted work + sidesteps the dup-connect raise
    entirely.  Same pattern applies to any idempotent setter using
    UniqueConnection.

    Don't try to narrow the exception type — PyQt6's error class
    isn't stable across minor releases.  The short-circuit is the
    correct fix.

32. **`single_capture` accepts a `timeout_s` override — pass
    `n_avg / rate_hz + 6 s` from low-pulse-rate experiments.** The
    driver default falls back to `self._timeout_ms / 1000` (10 s),
    which is fine at 100 Hz × 64 averages (6.4 s) but exhausts mid-
    accumulation at 1 Hz × 64 (needs 64 s).  The MATLAB
    `getWaveform.m` soft-timeout reads the rolling-average frame on
    expiry regardless, but that average is INCOMPLETE if the budget
    fires before the full N frames have stacked.  Symptom: the
    operator sees a clean trace on the scope screen but the saved
    capture is noisier than expected, and the log shows
    `poll deadline (10.0 s) reached, NUMACq = 12/64 — reading the
    latest averaged frame anyway`.

    VT (`_one_capture`) computes `(n_avg / rate_hz) + 6 s` from
    `self.scope._expected_acq_navg` and `pattern.rate_hz`, passes
    it as `timeout_s=` to both the primary capture AND the
    iterative rescale re-captures.  The 6 s headroom covers
    trigger latency + USB-TMC round-trip jitter (5 s minimum,
    +1 s extra per operator request after a first-frame tight-
    margin capture).  Mirror this in any new experiment that
    calls `single_capture()` directly.
    `capture_while_running` (PS / SP / LP) doesn't have this
    problem because it just sleeps for `wait_s` then reads — no
    NUMACq poll budget.

33. **Fast scaling mode was REMOVED — don't re-add it.** A "fast
    scaling" path once sized V/div from cursor-gated `MEASUrement:IMMed`
    MIN/MAX queries (`channel_min_max_fast`) instead of a full `CURVe?`
    transfer, to save USB time on the per-capture rescale loop. The
    operator's own bench logs (`exp_vt_max`) proved it a NET LOSS on
    real hardware:
    * **Slower, not faster.** `MEASUrement:IMMed:VALue?` averaged ~59 ms
      because the scope must analyze the gated record to compute each
      statistic — and the rescale needs BOTH min and max, so 2 × ~59 ms
      + the SOURce/TYPe handshaking ≈ 145-230 ms per channel, vs a
      single `CURVe?` at ~94 ms that yields the WHOLE waveform (and from
      it min, max, clip detection, baseline, AND the saved trace, all
      computed locally for free). The fast path then did a `CURVe?` for
      the final capture ANYWAY.
    * **Unsafe.** With only MIN/MAX scalars (no sample array) it could
      not run the full-array ADC-rail saturation check (`_clipped_arr`,
      >5 % of samples at the rail), and its stale-frame guard couldn't
      tell a genuinely large excursion from a stale coarse-scale frame
      — so a real out-of-view E_act/E_ret read was rejected (→ NaN → the
      loop kept the too-tight scale), the trace stayed CLIPPED, and the
      electrode polarization was UNDER-reported. VT bounds its amplitude
      ramp on that polarization (water-window limit), so under-reading it
      would ramp PAST the safe limit → electrode/tissue damage.

    Removed across the board: the GUI toggle, the
    `ExperimentRunner.fast_scaling` attr, the `fast` / `full_capture`
    params + branches in `rescale_to_fit`, and `channel_min_max_fast` /
    `_measure_immed_value` / `_STALE_FRAME_FACTOR` on the scope drivers.
    **KEPT**: `gate_measurement_window` / `clear_measurement_gating` /
    `measure_mean` (closed-loop bias feedback) and
    `settle_one_acquisition` (slow-mode recapture — gotcha #40). The
    full-capture rescale is now the ONLY path. If a future USB-speed
    optimization is ever needed, do NOT resurrect MIN/MAX scaling — make
    fast mode fall back to a full `CURVe?` whenever a read is rejected,
    so clip detection stays reliable.

34. **`per_capture_baseline` must sample the LEADING EDGE, not the
    whole pre-trigger window — the cathodic-in-pretrigger "wrong
    offset" bug.** The experiment plot (`gui/multichannel_scope.py`)
    and the calibration fit baseline-subtract each trace via
    `readback_calibration.per_capture_baseline(arr, t_us)` so the idle
    interpulse starts at zero. The OLD implementation averaged every
    pre-trigger sample (`t < -1 µs`). But with the **I_mon trigger
    protocol** (no digital sync channel — `[[scope-trigger-source-selection]]`),
    the scope fires on the **anodic** current edge, so for a
    cathodic-first pulse the **cathodic phase fills the pre-trigger
    window**. Averaging it returned the cathodic level (≈ −amplitude),
    and subtracting that shifted the WHOLE trace up by ~one pulse
    amplitude (interpulse 0 → +amplitude, cathodic → 0, anodic →
    +2·amplitude). Operator saw V_mon/I_mon "offset from zero" even
    though the saved `.npz` readback was correctly centered — the
    offset was injected by the DISPLAY's baseline subtraction, not the
    acquisition.

    Fix: sample the **earliest `min(n//16, 800)` samples** (time-ordered
    first). `auto_layout_for_pulse` always positions 3-4 divisions of
    idle baseline BEFORE the first phase, so the leading edge is idle
    regardless of where the trigger sits within the pulse. Verified:
    I_mon-triggered synthetic baseline −50 → −0.02; digital-triggered
    real data unchanged; a genuine +0.6 V DC bias still fully removed.
    The calibration nested `_per_capture_baseline` (`gui/calibration.py`)
    is kept in lock-step (same leading-edge logic) — they MUST agree or
    the experiment and calibration plots disagree on "idle".

    **FINAL state: NO baseline subtraction on ANY trace — the display
    plots the raw int8→double conversion + the optional moving average,
    nothing else.** Operator spec (final): "There should be no
    subtraction in V_mon and I_mon.  You should be able to convert the
    int8 data to double without any other processing, besides the
    optional moving average." The raw `_read_channel` conversion already
    yields the true voltage (the `apply_channel_defaults` preamble-cache
    fix removed the stale-`YOFf` acquisition offset), so V_mon / I_mon
    idle at ~0 on their own — no subtraction needed; E_ret / E_act keep
    their real rest potential; the derived E_act is the pure identity
    `V_mon_RAW + E_ret_RAW`. `per_capture_baseline` is no longer called
    by the display at all (kept for the calibration mirror only).
    **Do NOT reintroduce `per_capture_baseline` or the `t < -1 µs` mask
    in `multichannel_scope.set_capture`.** (History: the original
    cathodic-contaminated `t < -1 µs` subtraction caused a "+amplitude"
    offset → switched to leading-edge → then to V_mon/I_mon-only → and
    finally to no-subtraction-anywhere per operator request.) Tests:
    `tests/test_baseline_cathodic_pretrigger.py`,
    `tests/test_scope_eret_raw_and_centering.py`.

    Separately, `apply_channel_defaults` (`hardware/tektronix.py`)
    writes `CHx:POSition 0` and now invalidates the preamble cache —
    it was the one vertical-state write site that skipped invalidation,
    which could decode a later `CURVe?` with a stale `YOFf` and inject
    a constant per-channel DC offset into the SAVED data (a distinct,
    intermittent acquisition-side offset — see the `.npz` with
    V_mon ≈ +1.24 V and a 2.7× scale error).

35. **VT slow-mode rescale sizes V/div from a TRIMMED, stale-rejected
    array range — not the absolute min/max.** The V_mon compliance-
    switching transients (±1.5 V spikes at the phase boundaries vs the
    ±150 mV settled plateau) made the absolute-min/max rescale loop
    OSCILLATE 50↔1000 mV/div and never converge ("STILL OUT-OF-VIEW",
    final V/div squishing the real signal). Reads of ±1520 mV on a
    100 mV/div screen (rail ±500 mV) with `clip=not-clipped` are the
    tell — physically impossible, so either a transient at a coarser
    scale or a stale frame. Fix in the shared `rescale_to_fit` loop
    (`experiments/base.py`): (1) size from `np.percentile(_a,
    [_RESCALE_TRIM_PCT, 100-_RESCALE_TRIM_PCT])` (1.0 %) so the brief
    transients clip instead of blowing up the scale (drops the briefest
    ~1 % of samples at each end); (2) if the trimmed range still exceeds
    `_RESCALE_STALE_FACTOR` (1.6) × the channel on-screen half-window
    (`scope._channel_screen_window_v`), REJECT the read and keep the
    current scale (the `_RESCALE_STALE_FACTOR` backstop). Clip detection
    and the pre-trigger baseline still use the full array. Don't revert
    to `_a.min()/_a.max()`.

36. **Asymmetric pulse centering: the GUI plot frames the pulse with a
    SMALLER preceding interpulse than proceeding** (operator
    preference), via `ScopePlot._asymmetric_pulse_xrange`. The captured
    record is SYMMETRIC around the trigger (TBS2000 — POSition can't
    shift the data, gotcha #22), so this is a DISPLAY crop only: detect
    the active pulse span (largest-p2p trace, samples > 10 % of its
    1/99-percentile p2p from its median), then frame it with
    `_PULSE_PRE_MARGIN_FRAC` (0.15) × width leading and
    `_PULSE_POST_MARGIN_FRAC` (0.55) × width trailing, clamped to the
    data extent — never crops the pulse. Falls back to the full extent
    when no pulse is detected. Tests:
    `tests/test_scope_eret_raw_and_centering.py`.

37. **The time axis is taken AS-IS from the acquisition — NO active
    software shift for "zero placement" (operator: "There should not be
    an active adjustment of time for proper zero placement. You should be
    converting the x values from acquisition / record length + interval +
    offset like my MATLAB code").** The array is built ONCE in
    `TektronixOscilloscope._read_channel` as
    `t_us = (XZEro + n·XINcr)·1e6` (record length + sample interval +
    first-sample offset) — a direct port of `matlab_reference/getTime.m`
    (`XUNits = (idx-1)·XINcr + XZEro`; PT_Off is commented out there and
    is only our fallback when XZEro≈0, gotcha #22) — plus the
    `getTime2.m` 1.2 µs digital-sync correction (`DIGITAL_DELAY_US`) for
    digital triggers so t=0 lands on the stim phase-1 ONSET. So **proper
    t=0 placement is the acquisition's job**: with a tagged digital
    Trigger channel (`[[scope-trigger-source-selection]]`) the trigger −
    1.2 µs = phase-1 onset, so t=0 is at the pulse beginning naturally;
    with the I_mon-trigger FALLBACK the scope fires on the anodic edge so
    t=0 lands there (use the digital trigger to get onset-aligned t=0).
    `ScopePlot.set_traces` plots `time_us` unchanged. **Do NOT
    reintroduce an active pulse-detection time shift** — a prior revision
    used `_detect_pulse_span` to slide the axis so the onset hit t=0; the
    operator rejected that as an active adjustment and it was reverted.
    (`_detect_pulse_span` itself stays — it still drives the asymmetric
    x-range WINDOW crop in #36, which is an xlim choice, not a t=0
    shift.) Tests: `tests/test_scope_eret_raw_and_centering.py`
    (`test_set_traces_uses_acquisition_time_axis_unchanged`).

38. **PlexStim rate (PS_SetPeriod) + repetitions (PS_SetRepetitions)
    are programmed ONCE per channel per value, not every sweep step**
    (operator: "rate and repetitions do not need to be set for every
    iteration if all involved channels are set at the beginning").
    They're SEPARATE device state — NOT embedded in the `.pat` file
    (see `_load_arbitrary`'s `content_signature`) — and the device
    PERSISTS them across `.pat` reloads + stop/start cycles. The driver
    keeps a **value-keyed per-channel cache** (`_channel_period_ms`,
    `_channel_reps`): `load_channel` skips `ps_set_period` /
    `ps_set_repetitions` when the channel already holds the requested
    value (but ALWAYS issues the `ps_load_channel` commit so a changed
    `.pat` is applied); `set_repetitions` skips when the channel already
    holds `n` — so the sweep runners' `set_repetitions(active, 0)` right
    after `load_channel` (which already programmed reps=0 from the
    default-0 pattern) collapses to a no-op. A genuine rate/reps CHANGE
    (different value) re-programs + re-caches. **Both caches are wiped
    in `open()` AND `close()`** because PS_InitAllStim resets the
    device-side timing — keep them next to the `_validated_channels`
    reset. Don't bypass the cache by calling `ps_set_period` /
    `ps_set_repetitions` directly; route through `load_channel` /
    `set_repetitions` so the cache stays coherent. Tests:
    `tests/test_plexon_timing_cache.py`.

39. **VT's post-loop in-view verification uses the TRIMMED (percentile)
    range, not absolute min/max** — same `_RESCALE_TRIM_PCT` (1 %) the
    slow-mode sizing loop uses (#35). The brief compliance-switching
    transient (±1.5 V spikes at the phase boundaries) is EXPECTED to
    clip slightly and must NOT trip a "STILL OUT-OF-VIEW" warning when
    the SETTLED signal fits the screen (operator saw `observed [-1600,
    +1040] mV vs window [-495, +495]` flagged out-of-view at 100 mV/div
    even though the real ±250 mV signal fit fine, and asked "why did it
    stop / say out-of-view when it's scaled properly"). Sizing already
    used the trim; the verification now matches it. The runner still
    saves the capture + advances regardless of the warning (bounded at
    MAX_RECAPTURE) — the warning is diagnostic, not a hard gate (a hard
    gate would risk an unbounded re-capture loop). See
    `voltage_transient.py` `_one_capture` final in-view block.

40. **VT's rescale recapture (and first capture) MUST
    `settle_one_acquisition` before `single_capture` — the stale-frame
    bug that froze the V/div.** Root cause (confirmed by log arithmetic —
    the bad read magnitude = the previous read × exactly
    `old_vpd/new_vpd`, and the reads ALTERNATE small/large within one
    loop, ruling out electrode polarization): after a `CHx:SCAle` write
    the scope's free-running NUMACq is NOT reset, so `single_capture`'s
    AVERAGE-mode poll (`tektronix.py` `if count >= n_avg_target: break`,
    NOT entry-anchored) exits IMMEDIATELY on a STALE averaged frame still
    accumulated at the OLD scale. Its reconstructed magnitude is inflated
    by `old/new`, so the rescale loop ping-pongs (read stale-coarse →
    looks clipped → downscale → read stale-fine → looks tiny → upscale →
    …) and freezes at a too-fine scale — operator: "the vertical was not
    adjusted". `settle_one_acquisition` is ENTRY-ANCHORED (waits
    `entry + n_avg` FRESH frames so the rolling-average window is fully
    repopulated post-change; if NUMACq drops because the averager DID
    reset, it retargets to a plain `n_avg`) — but `single_capture`'s own
    poll is not. Fix: in `voltage_transient.py` `_one_capture`, call
    `self.scope.settle_one_acquisition(timeout_s=_capture_timeout_s)`
    BEFORE `single_capture` at BOTH the re-capture site AND the first
    capture (a new channel/step's first read can carry the prior
    channel's V/div). `settle → single_capture` does NOT double-cost:
    settle waits one fresh average, then `single_capture`'s poll passes
    instantly and only transfers `CURVe?`.
    **Use the call-site settle, NOT a driver-wide entry-anchor in
    `single_capture`** — that would lengthen EVERY PS/SP/LP/calibration
    capture (NUMACq never resets mid-sweep → +n_avg/rate per capture,
    e.g. +64 s at 1 Hz, regressing gotcha #32). **KEEP the
    `_RESCALE_STALE_FACTOR` (1.6×) reject guard** — it doubles as the
    gotcha-#35 backstop for real compliance transients that exceed the
    1 % percentile trim at short records; with fresh frames it fires far
    less often and no longer freezes (a real over-range now RAILS the ADC
    → clip-detected → coarsens, instead of reading "impossible"). Tests:
    the VT runner suite + `tests/test_shared_rescale_loop.py`.

41. **Asymmetric biphasic + triphasic pulses get V_mon/I_mon VERTICAL
    POSITION adjustment, not just V/div** (operator: "For asymmetric (and
    triphasic) waveforms, the vertical positioning must be adjusted as
    well"). A symmetric biphasic V_mon swings evenly about 0, so position
    stays 0; an asymmetric / triphasic pulse is lopsided (excursion
    midpoint ≠ 0) and must be POSITION-shifted to sit centred rather than
    clip the larger excursion. VT's rescale loop now runs V_mon/I_mon
    through the SAME `compute_scale_position_targets` bias-ratio helper it
    uses for E_ret/E_act — but on the **RAW observed range** (centre on
    the excursion midpoint `(min+max)/2`), NOT the baseline-synthetic
    E_ret/E_act path, and NOT gated on `_adapt_shrunk` (asymmetry needs
    centring at any magnitude). For a symmetric pulse `bias_ratio < 0.1` →
    AC-centered regime → `pos = 0`, so it's a NO-OP for the common case;
    the runner SKIPS the `set_channel_position` write when `|pos| ≤ 0.05`
    div to avoid needlessly invalidating the preamble cache every
    capture. The position is a scope `CHx:POSition` (ADC-centering)
    change — it does NOT add any offset to the reconstructed data, so it
    stays consistent with "no subtraction on V_mon/I_mon" (#34). See
    `voltage_transient.py` `_one_capture` `elif _role in ("vmon",
    "imon")` branch. Tests: `tests/test_vmon_asymmetric_position.py`.

42. **`_TEK_VERTICAL_GRID_VPD` is the FINE-GRAINED `getWaveform3.m` grid,
    NOT the 1-2-5 `adjustScale.m` list** (operator corrected: "I thought
    getWaveform.m had more"). A prior pass mis-ported `adjustScale.m`'s
    coarse 1-2-5 sequence; the operator's actual coarse-scaling grid is
    `getWaveform3.m`'s 6-part list (mV/div): `2–4.5 step 0.5`, `5–19 step
    1`, `20–95 step 5`, `100–280 step 20`, `300–950 step 50`, `1000–5000
    step 100` (≈102 entries, built via `_frange_incl`). The fine grid is
    what makes "minimize coarse scaling for best fit"
    (`[[scope-vertical-scaling-minimize-fit]]`) actually work: a ±73 mV
    trace snaps to **75 mV/div** (~97 % fill) instead of 100 mV/div (~73
    %) on 1-2-5. The **TBS applies fine V/div over SCPI**, so
    `set_channel_scale` writes **3-sig-fig scientific notation**
    (`{vpd:.2e}` → `7.50E-02`) — NOT the old `:g` decimal — so the scope
    doesn't quantize back to a coarse cell. **Do NOT revert to 1-2-5** and
    don't reinstate the "scope silently rounds off-grid writes" claim (it
    was wrong — the operator's MATLAB writes fine values and the TBS
    honors them at 3-sig-fig resolution). The grid is div-count-AGNOSTIC:
    the fit budget (`DIVS` / `MAX_FACTOR`) lives in the per-role `divs` +
    per-scope `_half_vert_divs` (operator's MATLAB targeted the 8-div
    TBS1104B; the Python computes the budget from the connected scope).
    Tests: `tests/test_fine_vertical_grid.py`.

43. **Transport-aware SCPI robustness — serial (ASRL/COM) gets
    per-command checks + tuned link; USB-TMC stays on the plain fast
    path.** Verified against the legacy Tek programmer manual
    (TBS1000/B · TDS2000/B/C · TDS1000/B/C · TDS200 · TPS2000/B —
    `download.tek.com/manual/TBS1000-B-EDU-TDS2000-B-C-TDS1000-B-C-EDU-
    TDS200-TPS2000-Programmer.pdf`), after the operator hit "poor
    communication despite matching baud rate" on a TPS2014B over
    RS232-to-USB. Key facts from the manual + what's implemented:

    * **`SYSTem:ERRor?` does NOT exist on the legacy families** (zero
      occurrences in the manual) — the old `_w_checked` silently no-oped
      there. The error check is now the IEEE-488.2 **`*ESR?`** (portable
      across every Tek family, self-clearing, error bits
      CME|EXE|DDE|QYE = `0x3C`), with detail from **`ALLEv?` → `EVMsg?`**
      (the manual's documented event-handling sequence — they dequeue
      events for "the last `*ESR?` read"); `SYSTem:ERRor?` is last-resort
      only — NEVER move it before the event queries (on legacy it queues
      ANOTHER error).
    * **`_is_serial`** (resource starts `ASRL`/`COM`, set in `open()`)
      gates: `_w` drains `*ESR?` after EVERY write; `_q` validates +
      retries (3×, `*CLS` + backoff) on empty/garbled/timeout reads.
      USB-TMC `_w`/`_q` are untouched — don't add per-command checks to
      the USB hot loop; the capture path self-validates.
    * **Serial link tuning** (`_configure_serial_transport`): timeout
      floor 5 s, `query_delay` 0.10 s, `send_end`, and **hard flagging
      (RTS/CTS) on BOTH ends** — host VISA `flow_control` + scope-side
      `RS232:HARDFlagging ON`. The manual documents the operator's exact
      symptom ("if no flow control is used, commands may be received
      faster than the oscilloscope can process them" → input-buffer
      overrun despite matched baud) AND warns **soft flagging (XON/XOFF)
      locks up on binary transfers whose payload contains XON/XOFF
      bytes** — `CURVe?` int8 data can contain any byte, so NEVER default
      to soft flagging. Operator overrides: `scope.serial_flow_control` /
      `scope.serial_baud_rate` before `open()`. Max legacy baud is 19200.
    * **`HORizontal:RECOrdlength` is QUERY-ONLY on the legacy families
      (fixed 2500 points)** — `set_record_length` skips the SET on any
      family whose registry `record_lengths` is a single value (writes
      only `DATa:STOP`, confirms by query). Without the skip, the SET
      queues a CME error that the `*ESR?` check now correctly surfaces —
      i.e. the new checks EXPOSED a long-latent bad write.
    * **TPS2000B registry**: the series pattern is `TPS2\d{3}B?` and the
      bandwidth table includes TPS2012B/2014B/2024B — the B variants
      (operator's TPS2014B) previously matched NOTHING and fell to the
      unknown-model defaults.

    Tests: `tests/test_tektronix_serial_transport.py`.

44. **Every phase-TIME chain anchors at the DETECTED pulse onset
    (`metrics.pulse_onset_us`), NOT at t=0.** The time axis is
    trigger-relative (#37): with the I_mon-trigger fallback the scope
    fires MID-pulse, so chains assuming "t=0 = stim start" sampled the
    WRONG phase — operator saw Epol1 drawn where Epol2 belongs and
    Epol2 pushed off the record end. Anchored consumers: `phase_windows`
    (→ `effective_capacitance_nf`), `_driving_voltage_per_phase` (incl.
    its pre-pulse baseline mask `t < onset`), `polarization_per_phase`'s
    time-method fallback, `_interpulse_potential(_split)` (rest windows
    `t < onset` / `t ≥ onset + total`), the exported plot's Epol cursors
    (`plotting._phase_end_times_us`), and the LIVE plot markers
    (`ScopePlot.set_markers`, wired in `multichannel_scope._refresh_
    traces` — MATLAB getPlot-style "metrics indicated on the plot").
    `compute_metrics` detects ONCE per capture (I_mon first — cleanest
    edges — then V_mon; 10 % of robust p2p from the median) and threads
    `onset_us=` through. Digital-sync captures detect ≈0 → unchanged.
    The access-voltage |dV/dt| method needs no anchor (data-driven).
    **Don't add a new phase-time chain anchored at 0.** Tests:
    `tests/test_pulse_onset_anchoring.py`.

45. **Per-channel figures save SYNCHRONOUSLY at `run_end`; only the .xlsx
    export runs on the BACKGROUND thread (`_ExportWorker`).** History: the
    plot render was ORIGINALLY enqueued to the background QThread (operator:
    "parallel processing… during the experiment") — but on a real
    16-channel run that thread was STARVED of GIL time by the per-capture
    rescale loop (numpy/CPU-heavy), so every plot — and even its log line —
    only flushed at the END (operator: "the plot saves occur after the
    experiment.  I want it in real time"; the log showed "Back-filling N
    channel plot(s)…" + all plots timestamped at run end).  A 600-DPI
    render is only ~1.8 s and `run_end` fires in the gap BETWEEN channels
    (stim already stopped by the VT/PS stop→load→start lifecycle), so the
    plot is now rendered RIGHT THERE on the runner thread
    (`_on_event` `run_end` → `export_run_plot(...)`, `RunnerWorker`),
    guaranteeing it's on disk the instant the channel completes — at the
    cost of delaying the next channel ~2 s (does NOT block pulsing).
    Successful renders add `id(run)` to `_export.exported_run_ids` so the
    end-of-run pass back-fills only a genuinely-failed render.  **Don't
    move the plot render back to the background thread** — it's GIL-starved
    during a run; if true parallel rendering is ever wanted, use a
    SUBPROCESS (`plotting._parallel_export_plots`), not a thread.  The
    XLSX still goes through the background worker (lag there is harmless —
    it's coalesced + atomically replaced).  The runner thread still
    ENQUEUES `submit_xlsx` (`run_end` + throttled per-capture); the
    workbook tracks SP/CP/LP snapshot streams live. Key invariants:
    * **XLSX submissions COALESCE per path** (a snapshot burst → one
      rewrite of the freshest session state — the queue can't grow
      unboundedly) and write ATOMICALLY (`.part` side-file +
      `os.replace`) so a crash never leaves a truncated workbook.
    * **One serial consumer** — matplotlib Agg figures must not be
      shared across threads, and two writers must never race the same
      .xlsx. Don't "speed it up" with a thread pool.
    * Successful plot writes land in `_ExportWorker.exported_run_ids`;
      the end-of-run pass back-fills ONLY misses, and
      `RunnerWorker._drain_exports()` (finish + wait) runs before BOTH
      `finished.emit` sites so completion never races an in-flight
      write and the GUI teardown is safe.
    * TIFs are LZW-compressed (was ~83 MB/figure uncompressed).
    * **Save DPI is operator-configurable** — the Setup tab's auto-save
      group has a DPI **dropdown** (`300 / 600 / 900 / 1200`, default 600 =
      `plotting.DEFAULT_DPI` / MATLAB `-r600`) beside the format combo.
      `SetupTab.autoSavePlotsChanged` carries `(bool, str, int)` =
      `(on, fmt, dpi)` (was `(bool, str)`); it threads
      `MainWindow._on_auto_save_plots_changed` →
      `tab.set_auto_save_plots(on, fmt, dpi)` → `RunnerWorker(auto_save_plots_dpi=)`
      → `_ExportWorker.submit_plot(…, dpi)` → `export_run_plot(dpi=)`.
      The dropdown is DISABLED when auto-save is off OR the format is the
      vector `svg` (DPI is meaningless there) — `SetupTab._update_dpi_enabled`
      is the single apply point for that combined gate. Round-trips under
      the `auto_save_plots_dpi` pref key (combo `findData`, not `setValue`).
      If you add a new `autoSavePlotsChanged` consumer, take the 3rd arg.
    Tests: `tests/test_background_export_worker.py`.

    The capture→page key is computed ON
    THE WORKER at emission (`captured = pyqtSignal(object, object)`,
    key = the event's own `run.configuration.display_name()`) — never
    re-read `runner.current_configuration` at GUI-processing time (race;
    suspected blank-CH01 cause). Session identity: `Session(notebook,
    subject)` + the `.npz`/`.xlsx` stem come from the Setup tab's
    notebook/session fields (`set_session_identity`, stem =
    `[notebook]_[session]` — same composition as the log filename);
    legacy `VT_<config>` only when the fields are blank.

46. **Multi-config SP / CP runs accumulate every configuration into ONE
    shared `Session` — don't give each config its own session saved to
    the same filename.** Operator bug: "running monopolar on all
    channels, but it only captured CH01." Each SP/CP configuration runs
    as its OWN runner (the between-channel rewiring pauses + the
    Stop/Start stim lifecycle need that — unlike VT, which loops all
    configs inside one runner). The old `ShortPulsingTab._start_next_
    pending` built a FRESH `Session` per config and saved each to the
    SAME `{session_stem}.npz` — so they overwrote one another and only
    the LAST channel's data survived on disk (the operator saw "CH01"
    because the GUI/first file showed it; the mechanism keeps the last
    writer). The per-config `.tif`s were fine (they carry a `_CHnn`
    suffix), masking the loss.

    Fix: the FIRST config creates the session + captures the save
    filename once (`self._sp_session` / `self._sp_save_name`); later
    configs REUSE it and repoint `session.test.configuration = config`
    (`TestParameters` is a mutable `@dataclass`; pattern / array /
    duration / extras are identical across the sweep, so only the
    configuration field changes). The runner appends its `ChannelRun`
    via `add_run`, so the session GROWS CH01 → CH01+CH02 → …, and the
    worker re-saves the cumulative session to the constant path each
    config — final `.npz` / `.xlsx` hold EVERY channel (matches VT).
    `start_clicked` resets `_sp_session` / `_sp_save_name` /
    `_export_carryover_ids` so each Start begins a fresh accumulation.

    **Companion: `_export_carryover_ids`.** Because the shared session's
    `runs` list grows, each successive config's background `_ExportWorker`
    would see all PRIOR channels' runs as "not yet exported" and
    re-render every one at end-of-run (wasteful + a misleading
    "Back-filling N plot(s)" log). `_start_runner` seeds the new
    worker's `exported_run_ids` from the tab-level carryover set (run
    `id()`s are stable — same objects in the shared session), and
    `_on_finished` accumulates the just-finished worker's set into the
    carryover before chaining. No-op for VT (one worker) and single-
    config SP/PS/LP. Tests: `tests/test_sp_multichannel_save.py`.

47. **Start-time stim re-init keys on the SHARED device's `is_open`, NOT
    the per-tab `_stim_needs_init` flag.** Operator: "wanting to run a
    different experiment after I aborted one … the stimulator stays
    closed and won't let another experiment run." The stim/scope objects
    are SHARED across every experiment tab (`MainWindow._on_connected`
    hands the SAME instances to each `tab.set_hardware`). The Stop/abort
    lifecycle (gotcha #29b) closes the shared stim and sets
    `_stim_needs_close_after_run` / `_stim_needs_init` — but those flags
    live on `self` (the TAB). Aborting in tab A closed the device and set
    tab A's flag; switching to tab B and pressing Start checked tab B's
    flag (still False) → skipped re-init → the run failed on a closed
    device. Fix: `_BaseExperimentTab._reinit_stim_if_closed()` (called at
    the top of `_start_runner_body`) decides on `stim.is_open` — the
    device's own state — so ANY tab's Start re-opens a device ANY tab
    closed. The per-tab flag is a fallback only for stims whose `is_open`
    can't be read (returns None). It also CLEARS a stale flag when the
    device is already open, so a normal end-of-run → next-Start never
    redundantly re-opens (which would re-trip the `ps_close_all_stim`
    cascade, gotcha #29c). `SimulatedStimulator` overrides `is_open` to
    return `_is_open` (the base property infers it from
    `info.n_channels`, which the simulator keeps populated across
    `close()`). Tests: `tests/test_stim_reinit_cross_tab.py`.

48. **(Superseded by #33 — fast scaling was removed entirely.)** This
    gotcha originally force-disabled fast scaling for Voltage Transient
    because it under-reported electrode polarization (clipped E_act /
    E_ret), which is unsafe for the water-window ramp limit. The
    operator's bench logs then showed fast scaling was ALSO slower than
    a plain `CURVe?` transfer, so it was removed wholesale rather than
    just gated off for VT. See gotcha #33 for the full removal record and
    the data behind it.

49. **`clear_scope_mapping` (scope disconnect) must NOT wipe the
    channel→role assignments — that lost the user's choices.** Operator:
    "the choices for oscilloscope channels is not being remembered. CH3
    is constantly set to Eret." The Setup tab maps each scope channel
    (CH1-CH4) to a waveform role (V_mon / I_mon / E_ret / E_act /
    Trigger / None) via `_role_combos`; these persist under the
    `channel_roles` prefs key and round-trip through `current_prefs` /
    `restore_prefs`. The bug: on scope disconnect (`scopeConnected(False)`
    → `MainWindow._on_scope_connected(False)` → `clear_scope_mapping`),
    EVERY role was reset to `None`; the next prefs save (auto-save on
    Start, post-run, or close) then persisted those `None`s, and on the
    following connect `apply_default_scope_mapping` re-applied the
    CATALOG default (`DEFAULT_CHANNEL_ROLES`, CH3 = E_ret) because it
    fills any `None` row. The map is only ever consumed at RUN time
    (which requires a connected scope), so a stale map while disconnected
    is harmless — `clear_scope_mapping` now KEEPS the assignments (only
    resets row visibility + the trigger hint). **`_set_visible_scope_channels`
    ALSO no longer wipes** (second fix, same operator complaint recurring):
    it used to `setCurrentText(ROLE_NONE)` on the hidden CH3/CH4 rows of a
    2-channel scope — but a transient channel-count mis-probe (or a genuine
    2-ch scope session) then persisted those `None`s and lost the user's
    choice across restarts. It now HIDES the rows but PRESERVES the stored
    role, recording the physical count in `_n_visible_scope_channels`;
    `current_aliases()` SKIPS channels beyond that count so a hidden role
    never leaks onto a nonexistent channel, yet the choice survives and
    reappears when a 4-channel scope reconnects. **Don't re-add the
    `setCurrentText(ROLE_NONE)` wipe to EITHER `clear_scope_mapping` or
    `_set_visible_scope_channels`** — the runner-safety it provided is now
    handled by the `current_aliases()` skip. Tests:
    `tests/test_scope_alias_persistence.py`.

    **THIRD recurrence (the connect-time override): `apply_default_scope_
    mapping` re-filled a DELIBERATE `None` on every connect.** Operator
    (emphatic): "The oscilloscope channel choices are not being remembered
    … It keeps resetting CH1-4 to V_mon/I_mon/E_ret/Trigger … I have been
    using NONE for CH3." Root cause: `MainWindow._on_scope_connected(True)`
    calls `apply_default_scope_mapping()` on EVERY connect, and the old
    version filled ANY combo at `ROLE_NONE` with the catalog default —
    indistinguishable from a user's deliberate `None`, so CH3's `None` →
    `E_ret` on every reconnect (the other three matched the default, so
    only CH3 looked "wrong"). Fix: a `SetupTab._scope_roles_user_configured`
    flag. `apply_default_scope_mapping` is now a NO-OP once configured (it
    only fills on a genuinely fresh setup — a first-ever connect with
    nothing set — then sets the flag). The flag flips True via (a)
    `_on_role_changed` (any user combo change OR a `restore_prefs`
    `setCurrentText`; NOT fired during construction — the combos connect
    the slot AFTER their initial `setCurrentText(None)`), and (b) an
    explicit set in `restore_prefs` when a real (≥1 non-None) mapping is
    restored. Gated on "≥1 non-None" so a degenerate all-None prefs blob
    still lets the first-connect default apply. **Don't make
    `apply_default_scope_mapping` fill `None` rows unconditionally again** —
    a deliberate `None` is a valid, must-persist choice. NOTE: a user whose
    prefs were already corrupted (CH3 saved as `E_ret` because the bug kept
    overwriting + a later prefs save captured it) must set CH3=None ONCE
    more and re-save (Start a run / close PULSAR); from then on it sticks.
    Tests: `tests/test_scope_alias_persistence.py`
    (`test_apply_default_is_noop_once_configured`,
    `test_restored_roles_block_default_override_on_connect`).

50. **STOP responsiveness: the scope's blocking loops honour an
    abort-check hook so a capture bails promptly.** Operator: "When
    pressing STOP, the program should immediately stop when it is safely
    possible." The stim already halts instantly on STOP
    (`stop_clicked` → `abort_all` = PS_AbortAll, gotcha #29b); the
    remaining latency was the runner thread waiting out a blocking scope
    operation. `Oscilloscope.set_abort_check(fn)` installs a no-arg
    predicate; the driver's wait loops — `single_capture`'s NUMACq poll
    (both AVERAGE + SAMPLE), `settle_one_acquisition`, and
    `capture_while_running`'s wait — call `self._should_abort()` and
    break early (reading whatever's averaged so far, never raising on
    the SAMPLE-mode timeout). `ExperimentRunner.__init__` wires the hook
    to `lambda: self.aborted`, so EVERY runner gets it automatically; the
    active runner overwrites it on construction and only one runs at a
    time. **Don't make the scope driver import the runner** — the hook
    is a plain callback, the only coupling. The rescale loop already
    checks abort per attempt (Task #56), so with the wait loops now
    abort-aware STOP exits within one short poll interval. Tests:
    `tests/test_experiment_ui_batch.py`.

51. **Reset learned electrode potentials: Help → "Reset learned
    electrode potentials…".** Early PULSAR builds recorded WRONG E_ret
    rest potentials into the per-coating learned-OCP store
    (`electrode_potential_history`) via the baseline-subtraction bugs
    (gotcha #34, since fixed), so the Pt bin was contaminated and the
    operator turned the reference-electrode toggle OFF to avoid it. The
    menu action (`MainWindow._on_help_reset_potentials`) shows the
    current per-coating sample counts and offers **Reset ALL** or
    **Reset Pt only**, calling `electrode_potential_history.reset(None)`
    / `reset("Pt")`. The `reset()` function already existed (it was the
    long-planned "Reset learned OCP" entry); this just surfaces it. The
    store re-learns cleanly from subsequent captures.

52. **`ExperimentRunner.preflight` SELF-HEALS a closed stimulator.**
    Operator: "After I aborted the experiment, I still get errors about
    the stimulator, likely because it is still closed and not
    initialized." A Stop/abort closes the stim (gotcha #29b); the GUI
    re-opens it on Start (`_reinit_stim_if_closed`), but any path that
    reaches a runner with the device still CLOSED would fire a flood of
    cryptic `Communication to Stimulator failed` DLL errors
    (`set_monitor_channel`, `load_channel`, …) on a dead handle — the
    old `preflight` only checked `stim is None`, not `stim.is_open`.
    `preflight` now re-opens a closed stim (a clean single
    `PS_InitAllStim`, no cascade — gotcha #29c) and raises a clear,
    actionable error if the re-open fails. `is_open` is the device's own
    state; drivers without it (base property returns True) are left
    alone. Tests: `tests/test_capture_nav_and_abort_recovery.py`.

53. **The XZEro-disagree diagnostic logs ONCE per scope session, not per
    capture.** On the TBS2000-series (percent-position, Method P, gotcha
    #22) the firmware ALWAYS reports `XZEro = −record/2` while the true
    zero follows the position %, so the cross-check fired every capture
    and spammed the log (operator: "Stop with the XZero disagree …
    you are going to keep doing that with the TBS2000B series"). It's
    expected, not a fault, so it's logged once (gated by
    `_xzero_disagree_logged`, re-armed in `open()`). Don't restore the
    per-capture line.

54. **Learned OCP is a RECOMMENDATION, never auto-applied — keep the
    user's default potentials.** Operator: "Show the recommended
    potentials based on what has been learned, but keep the default
    values that I had set originally." Earlier builds had the learned
    per-coating OCP (running mean of recorded E_ret rest potentials)
    WIN over the catalog/user value in `_effective_ref_potential_v`,
    which shifted the water-window limits by the learned value. Now
    `_effective_ref_potential_v` always returns the catalog / user
    default (so the limits stay where the operator set them), and the
    learned mean is surfaced ONLY as a ` · recommended X V (learned, N
    samples)` annotation appended to the reference / return readout
    labels (`_recommendation_tag`). **Don't reinstate the learned-wins
    branch.** Companion toggle: **"Remember return-electrode potential"**
    (`remember_potential_chk`) gates whether finished captures record
    E_ret into the store at all — `electrode_potential_history.
    record_capture` early-returns when the setup snapshot's
    `remember_return_potential` is False (absent → True, legacy
    default), and the recommendation tag only shows while the toggle is
    on. Round-trips through `setup_snapshot` + prefs
    (`remember_return_potential`). Tests:
    `tests/test_return_potential_toggle.py`.

55. **Per-channel/combo xlsx sheets use the Gamry ``.DTA`` VERTICAL
    layout, not the side-by-side MATLAB ``writetable`` layout** (operator:
    "Have the excel sheets for each channel/combo in the same style as
    Gamry DTA with the data table at the end"). `gamry_export.
    _write_electrode_sheet` now writes, top → bottom: (1) a metadata
    preamble (`ELECTRODE` section, `TAG | KIND | VALUE | COMMENT` rows —
    same style as the Instrumentation/Parameters/Setup sheets); (2) a
    `SUMMARY` table with ONE ROW PER CAPTURE; (3) the raw-waveform `DATA`
    table (`Pt | Time | Voltage | Current | [Active] | [Return] | Current
    Density`) at the END of the sheet. The old layout put time-series
    columns on the left and metric columns on the right (each metric in
    the first 1-6 rows of its column) — that's gone. The SUMMARY
    header/units/row come from the SHARED `_summary_headers_units()` /
    `_summary_row(cap)` helpers, which `save_session_dta`'s `.DTA` text
    writer ALSO uses — so the xlsx sheet and the `.DTA` file never drift.
    The `DATA` table shows the REPRESENTATIVE capture (`_final_capture` —
    largest good amplitude); the per-capture sweep evolution lives in the
    SUMMARY rows, and the full per-access-point breakdown (all lead/trail
    Va/Ra) stays on the `Values` sheet. `include_raw_traces=False` skips
    the DATA table. Tests: `tests/test_gamry_electrode_sheet.py`.

56. **VT adaptive ramp uses a CONSERVATIVE proportional SEED jump before
    it has enough points to regress** (operator: "Why is adaptive ramping
    so slow? It takes over a dozen captures to reach the potential
    limit"). The `adaptive`/`predictive` strategies fit polarization-ratio
    vs amplitude and solve for the ratio=1 (water-window) crossover — but
    until `min_points_for_regression` samples exist they used to crawl up
    by a fixed `coarse_step_ua` (5 µA), so reaching a ~100 µA limit from a
    low start took ~20 captures. Now `_next_step`'s pre-regression branch
    SEEDS a jump: assuming polarization ≈ linear through the origin, the
    amplitude that reaches the limit is `current/ratio`; it jumps to
    `seed_fraction` (0.7) × that. Because `seed_fraction < 1` the next
    capture lands WELL INSIDE the water window (a safe UNDERSHOOT — never
    an overshoot that could damage the electrode) AND gives the regression
    a high-amplitude anchor, so it converges in ~3-4 captures. Clamped to
    60 % of the remaining headroom so a tiny first-capture ratio can't
    fling the amplitude at the ceiling; falls back to the coarse step when
    already near the limit. `min_points_for_regression` dropped 4 → 2.
    The limit itself is E_pol crossing the Setup `cathodic`/`anodic`
    limits (for SIROF + Pt-return WITHOUT an Ag|AgCl reference these are
    V_mon-based, e.g. −0.8 / +0.6 V). Tests: `tests/test_vt_ramp_seed.py`.

    **LOCAL-SECANT fast approach (the seed gets you ~halfway; this gets you
    the rest fast).** Operator (with `exp_vt_max` data): "taking WAY too long
    to reach the potential limits." Diagnosis from the data: 13/16 channels
    DID reach the limit; the late ones were cut off by the operator's own
    Stop (frustration). The slowness was the APPROACH — SIROF E_pol-vs-I is
    SATURATING (concave-down), so the global poly fit (`min(candidates)`,
    ×0.9 dampening) UNDER-predicts the crossover and the step collapsed to a
    `fine_step` creep (~10 µA/capture for the last few %), burning 6-10
    captures on the final climb (real run0: 9 captures, with 5 of them
    creeping 763→803 µA). Fix: `_local_secant_target` projects the amplitude
    that reaches `ramp.aim_ratio` (default 1.0) × limit from the LAST TWO
    captures' slope; `_next_step` prefers it when it's more aggressive than
    the regression. **Safe for concave-down data**: the local slope is
    shallower than the chord to the true crossover, so the linear projection
    UNDERSHOOTS the true ratio=1 amplitude → the step lands below the limit;
    the delta is still clamped to half the remaining headroom and the
    per-capture `_potential_limit_hit` stops the sweep the instant a capture
    crosses. Simulated on the real run0 curve: 9 → 6 captures (~⅓ fewer; the
    final 2-3 fine steps to PIN the crossing are irreducible). The
    regression crossover ceiling also now tracks `ramp.max_ua` (was a
    hardcoded 1000); **the PlexStim hardware max is 1000 µA**, so set
    `max_ua=1000` — a channel that reaches 1000 µA without crossing the water
    window (e.g. exp_vt_max run12: 991 µA at −0.802 V) is hardware-limited,
    NOT a ramp failure. Remaining slowness is per-capture TIME (~8-14 s: the
    rescale loop's averager settles + 15-channel zero-pattern reload) — a
    separate optimization, untouched here. Tests:
    `tests/test_vt_ramp_seed.py` (`test_secant_takes_a_real_step_not_a_fine_creep`,
    `test_secant_undershoots_for_saturating_data`).

57. **Access-point localization is TIME-ANCHORED when an onset is given —
    region-partitioned, NOT global-peak-in-time-order.** Operator: "the
    second access voltage/resistance is floating near the beginning of the
    pulse!!" Under the I_mon trigger the scope fires mid-pulse (pulse onset
    ≠ 0) and the leading cathodic edge throws a big compliance transient,
    sometimes a |dV/dt| DOUBLE-BUMP. The legacy `access_voltage_and_
    resistance` found the N tallest global |dV/dt| peaks and mapped them to
    the labelled access points (`access_index_labels`) in TIME ORDER — so
    two clustered leading-edge peaks shoved the trailing-phase-1 point
    (V_a2) back to the pulse start. Fix: `access_voltage_and_resistance(...,
    onset_us=...)` — when an onset is passed, each label's EXPECTED boundary
    time is derived from `onset + cumulative phase timing`, and each
    boundary takes the largest |dV/dt| peak in the trace region it OWNS
    (between its midpoints with the adjacent boundaries). Peak *i* ↔ label
    *i* by construction, so leading-edge clutter can't steal a later slot.
    The pre-pulse baseline mask also anchors to `onset` (`time <
    onset−1`) instead of `time < 0`. `compute_metric_markers` (plotting) and
    `compute_metrics` (the saved per-capture metrics) BOTH pass
    `onset_us=onset` so the live markers, exported plot, and saved metrics
    agree. `onset_us=None` (default) keeps the LEGACY global-peak path —
    used ONLY by the calibration R-extraction (`gui/calibration.py`), which
    feeds clean test-board signals and has its own verified post-edge window
    fit; don't pass onset there. On clean onset≈0 data the two paths agree
    (verified), so saved metrics don't regress. Tests:
    `tests/test_access_anchored_localization.py`.

58. **Pulse-derived "effective capacitance" (C_eff = I/(dV/dt)) is shown
    ONLY for a capacitive / open / broken response — NOT for a normal
    mixed electrode.** (PARTIAL REVERSAL of the original blanket removal,
    per a later operator decision — read the whole entry.)

    *Original removal (still the rule for NORMAL electrodes):* Operator
    (domain expert, after reviewing the
    literature in their Zotero library): a galvanostatic voltage transient
    cannot separate the ohmic, capacitive, and Faradaic contributions. The
    total current is `i = i_c + i_f` (Harris et al. 2019, *Front. Neurosci.*
    13:380, eq. 1: `i_c = A·C_d·dE/dt`), so the ramp slope reflects BOTH and
    `C_eff = I/|dV/dt|` is not a meaningful double-layer capacitance — it
    silently lumps Faradaic charge in. Harris 2024 (*J. Neural Eng.*
    21:013003, "Limitations in the electrochemical analysis of voltage
    transients") shows the VT shape has no clean iRu/capacitance/Faradaic
    boundary and that even applying Ohm's law to the polarisation/total
    voltage is invalid (report them as VOLTAGES). The lab's own Nguyen et al.
    VT paper reports `Q_ph`/`Q_inj`/`max(Q_inj)`/access/polarisation/driving
    — no pulse capacitance. So `effective_capacitance_nf`, its compute call,
    the `CaptureMetrics` field, the live `MetricTable` + viewer rows, the
    Gamry SUMMARY/Values columns, and the persistence save/load were all
    removed. The defensible pulse metrics are the voltages + `max(Q_inj)`
    (already computed); a real capacitance/charge-storage number needs CV
    (CSC) or EIS (CPE), which this rig doesn't run. **`C_d`
    (`driving_capacitance_mf_per_cm2`) is a SEPARATE charge/voltage
    efficiency ratio (max Q_inj ÷ V_d), NOT a double-layer capacitance — it
    was KEPT.**

    *Later reversal (operator, looking at `exp_vt_max`): "Show capacitance
    since it is so linear … but only when the response is entirely
    capacitive, open circuit, or broken.  Do not include access voltage or
    resistance [or electrode polarization]."*  The Harris-2024 objection
    only bites for a MIXED response (resistive + capacitive + Faradaic).
    When there's NO separable mix — a pure linear capacitive ramp, or an
    open/broken/high-Z electrode — `I/(dV/dt)` IS meaningful, and the
    access V/R + E_pol are NOT.  So `effective_capacitance_nf` is back, but
    GATED: `metrics.classify_response_and_ceff` classifies each capture as
    `normal` / `capacitive` / `open` / `broken`, and `compute_metrics`:
    * **normal** → access V/R + E_pol as before; `effective_capacitance_nf`
      = NaN (the table omits the C_eff row).
    * **capacitive / open / broken** → set `effective_capacitance_nf`
      (= |I|/|dV/dt| from the cathodic charging-ramp slope, nF) and CLEAR
      `access_*` + `polarization_per_phase_v` + their return-side lists, so
      the table/plot/limit-check don't report meaningless V_a/R_a/E_pol.
    Classifier discriminators (onset-tolerant, off the V_mon excursion):
    `open` = railed near `STIM_VOLTAGE_COMPLIANCE_V` AND effective |V|/|I| ≥
    50 kΩ; `broken` = effective |V|/|I| ≥ 200 kΩ but not railed; `capacitive`
    = linear ramp (R² ≥ 0.97) with NO instantaneous IR step (the first
    sample to clear 2 % of the peak is still ≤ 8 % of the peak — a series-R
    electrode jumps PAST that in one sample).  **Detection is deliberately
    CONSERVATIVE** (a real SIROF's ~30-50 % IR step → `normal`, keeping its
    E_pol + water-window limit): misclassifying a normal electrode as
    capacitive would suppress its E_pol and disable the limit trip — the
    backstop is that open/broken/capacitive captures then stop on voltage
    COMPLIANCE / `max_ua` instead (no Faradaic water window applies).
    Verified on `exp_vt_max`: CH03 → `open`, CH04/06/09 → `broken` (all
    sub-nF C_eff, flagging the dead electrodes), the 12 healthy channels →
    `normal`.  Both `effective_capacitance_nf` and `response_class`
    round-trip in persistence (NaN / "normal" defaults for legacy npz).
    **Don't add C_eff to a NORMAL capture, and don't drop the conservative
    gate.** Tests: `tests/test_response_classification.py`.

    **The PLOT and the metrics TABLE must INDICATE the bad response, not show
    misleading metrics** (operator: "CH03 etc. still tries to show other
    metrics and not indicate that it is open").  `compute_metrics` cleared
    the per-phase lists, but `compute_metric_markers` RE-derives access/E_pol
    from V_mon and `driving_voltage_v` (= max|V_mon|, railed at compliance
    for an open channel) was never cleared — so the plot drew access/E_pol/V_d
    and the table showed a railed V_d.  Now:
    * **Plot** — `compute_metric_markers` early-returns a SINGLE
      `kind="badclass"` marker (an "x" glyph + `"<class>  ·  C_eff = X nF"`
      text) for any non-normal `capture.metrics.response_class`; no
      access/E_pol/V_d markers.
    * **Table** — `MetricTable.show_capture` early-returns a FOCUSED view for
      a non-normal class: pulses, I_stim, a **`Response` = OPEN/BROKEN/
      CAPACITIVE** row, Q_ph, C_eff, Compliance — and DROPS V_d / C_d / E_ip /
      access / E_pol.
    Both key off `capture.metrics.response_class`.  Tests:
    `tests/test_driving_voltage_marker.py::test_bad_channel_plot_shows_only_response_annotation`,
    `…::test_metric_table_indicates_open_channel`.

    **The VT RAMP now STOPS EARLY on a non-normal classification**
    (operator: "the very apparent bad channel/combo — clearly capacitive
    response with no resistance — STILL continues to be tested.  You can
    easily test this by just linear regression of the first phase").  A
    capacitive / open / broken electrode has NO Faradaic water window to
    ramp toward: no resistance ⇒ no IR drop ⇒ E_pol is cleared (so
    `_potential_limit_hit` never trips), and a clean low-amplitude
    capacitive ramp never reaches voltage compliance — so the loop used to
    ramp it to `max_ua`, wasting captures on a dead/degenerate electrode.
    `voltage_transient.run()` now breaks the sweep the moment
    `cap.metrics.response_class in ("capacitive","open","broken")`
    (right after the abort check, before the limit/compliance checks),
    logging the class + C_eff and noting it on `cap.status`.  Safe because
    the classification is amplitude-INDEPENDENT (IR-step/peak = R/(R+W/C),
    the current cancels) — a single bad-classed capture is a reliable stop
    and a real SIROF keeps its IR step → stays `normal`.  Tests:
    `tests/test_vt_bad_response_stop.py`.

59. **Single-instance guard — at most ONE PULSAR window per user.**
    Operator hit `PS_InitAllStim failed: No Plexon Stimulator is detected`
    on a freshly-opened PULSAR while an OLDER PULSAR (left running ~1.5 h)
    still held the stimulator. The PlexStim 2.0 allows only one client
    (exclusive USB lock; the DLL is single-producer, gotcha #29c), so a
    second window can't init AND two processes touching the DLL risk a heap
    race. The built-in `plexstim_lock --close` recovery does NOT catch this
    — it only force-closes Plexon's OWN Sim-2/Stim-2 GUI by name/install-
    path, not a duplicate PULSAR. Fix: `gui/main_window.launch()` claims a
    per-user `QLocalServer` (`_acquire_single_instance` /
    `_single_instance_server_name`, name `PULSAR-stimtest-single-instance-
    <user>`). A second launch's `QLocalSocket` connects to it → pings
    `b"raise"` → the running instance's `newConnection` handler calls
    `_bring_to_front(win)` (un-minimise + raise + activateWindow) → the
    second process returns 0 WITHOUT building a window. Set
    `PULSAR_ALLOW_MULTIPLE=1` to bypass (dev/test — running two simulator
    instances). **The guard lives in `launch()`, NOT `run_gui.py` top-level
    or a module import**, because (a) the venv `python.exe` is a SHIM that
    spawns the real interpreter as a child (parent+child both show
    `python run_gui.py`; only the child reaches `launch()`), and (b) the
    PyQt6-rescue relaunch (gotcha #16) exits the parent before `launch()` —
    so exactly one process per launch claims the lock; a module-level guard
    would false-trip on the shim/relaunch parent. `removeServer(name)`
    before `listen` clears a stale socket from a crashed instance; the guard
    fails OPEN (QtNetwork missing / `listen` fails → proceed without it).
    Tests construct `MainWindow(...)` directly (not `launch()`), so they're
    unaffected. Tests: `tests/test_single_instance.py`.
    (`_acquire_single_instance(server_name=…)` takes an optional name so
    tests use an ISOLATED server and never collide with a real PULSAR that's
    actually running on the bench — which legitimately holds the production
    name.)

60. **E_pol uses the OPERATOR method, and the plot marker + table + limit
    all read the SAME value.** Operator: "use the time method when there is
    an interphase or discharge delay available, otherwise use driving
    potential − leading access voltage per phase." Bug it fixed: the metrics
    table + water-window limit used `polarization_per_phase(method="auto")`
    (the DERIVATIVE method — trailing-access plateau at phase end), while the
    on-plot Emc/Ema marker used the TIME method (V at phase_end+depol). Same
    capture, two different E_pol numbers (e.g. plot −0.720 V vs table/limit
    −0.835 V) → the plot looked like it never reached the limit even though
    the runner stopped on the derivative value. Fix:
    `polarization_per_phase(method="operator", driving_per_phase=…,
    leading_access_per_phase=…)` — TIME sample for phases with a trailing
    delay (the IEEE NER `E_mc` convention; the common biphasic-with-
    interphase+discharge case), `sign × (|driving| − |leading_access|)` for
    delay-less phases. `compute_metrics` computes it (building leading-access
    magnitudes from `access_index_labels` + `va_list`) for both active and
    return; `compute_metric_markers` now READS
    `capture.metrics.polarization_per_phase_v[k]` for the Emc/Ema value (drawn
    on the trace at phase_end+depol / phase_end) so the plot can never
    disagree with the table or `_potential_limit_hit` (which already uses
    `polarization_per_phase_v`). **Safety implication the operator chose
    knowingly**: the time method (recovered, +12 µs) is SMALLER-magnitude
    than the derivative (peak at phase end), so the limit now trips at a
    HIGHER amplitude — the ramp climbs further before stopping, and
    max(Q_inj) is larger. That matches the lab's published E_mc definition.
    Limit reference stays **V_mon vs Pt, −0.8/+0.6 V** (no Ag|AgCl channel is
    actually read — `available_roles` is almost always just vmon/imon, so
    E_pol falls back to V_mon; the code never auto-switches to the −0.6/+0.8
    Ag|AgCl window unless E_act is genuinely digitised and the limits are set
    to it). Tests: `tests/test_operator_epol.py`.

61. **The water-window limit trips on an acceptance BAND centered on the
    limit — `polarization_tolerance_v` is the "acceptable difference",
    NOT an outward grace band.** Operator: "Emc reached ~-0.8 V by
    capture #4, but it kept increasing and stopped at -0.843 V by
    capture #10" → "The tolerance is the acceptable difference. Look at
    how my MATLAB code used the tolerance." Port of
    `getAcuteWaveformData2.m` lines 289-301:
    ```matlab
    acceptLimitMin = limitPotential - 2*ACCEPTABLE_EMC_RANGE  % -0.82 for a -0.80 limit
    acceptLimitMax = limitPotential + 2*ACCEPTABLE_EMC_RANGE  % -0.78
    isPotentialLimitReached = acceptLimitMin <= E_mc <= acceptLimitMax
    ```
    The limit is "reached" when E_mc lands WITHIN ±tol of the limit
    (`2 × ACCEPTABLE_EMC_RANGE = 0.020 V` = the Python GUI default
    `polarization_tolerance_v`). A cathodic ramp climbing from above
    (less negative) first enters the band at the NEAR edge
    `acceptLimitMax = cathodic_limit + tol` (-0.78 V) — so THAT is the
    trip threshold, NOT `cathodic_limit - tol` (-0.82, the band's FAR
    edge / `acceptLimitMin`). The old code tripped at the far edge, so
    the adaptive secant (which aims AT the limit, `aim_ratio = 1.0`)
    reached -0.8 by cap #4, then had to creep the extra 0.02 V to -0.82
    by ~10 µA fine-steps — and because SIROF polarization saturates
    (concave-down) near the limit each step moves E_mc only ~0.01 V, so
    it burned ~6 captures and overshot the water window to -0.843 V
    (electrode damage — the exact thing the limit prevents). Fix in
    `voltage_transient._potential_limit_hit`: `cath = cathodic_limit_v +
    tol`, `anod = anodic_limit_v - tol`. **For a ONE-WAY ramp** (the
    Python runner only increases current; it can't back off like
    MATLAB's bidirectional inc/dec targeting in `changeCurrent.m`),
    `v <= acceptLimitMax` also catches a step that overshoots PAST the
    band's far edge (`v < acceptLimitMin`), so the ramp always stops at
    or beyond the near edge instead of running away. **Don't flip the
    tolerance back to `limit - tol` (cathodic) / `limit + tol`
    (anodic)** — that's the band's far edge and reintroduces the
    overshoot+creep. The secant `aim_ratio = 1.0` (aim at the band
    CENTER, the limit) is correct: concave-down data undershoots to ~the
    near edge and trips, landing E_mc within the band. `changeCurrent.m`
    itself uses the bare limit (no tol) for its over/under step direction
    (the `ACCEPT_DIFF` lines there are commented out); the ±tol band
    lives in the "is the limit reached" decision
    (`getAcuteWaveformData2.m`), which is what `_potential_limit_hit`
    ports. Tests: `tests/test_vt_limit_band.py`.

62. **The adaptive max-Q_inj predictor fits EVERY excursion separately and
    takes `min` — it does NOT collapse to a single worst-case ratio.**
    Operator: "Look at how my MATLAB code tried to consider all potential
    excursions when predicting the maximum charge injection capacity."
    Port of `changeCurrent_Fit.m` lines 754-886 — the MATLAB fit loops
    `excursion_idx × electrode_idx (Active/Return) × limit_idx
    (lower/upper)`, fits each location's E_pol-vs-current, solves for that
    location's water-window crossing, and takes `min(currentStim_guess)`:
    the ceiling is whichever polarization location, on EITHER electrode,
    reaches its limit FIRST. The earlier Python predictor collapsed all
    excursions into one worst-case `_polarization_ratio` scalar per capture
    and fit that single envelope — which LOSES each location's trajectory
    and mispredicts when the dominant excursion SWITCHES as current rises
    (a steeply-climbing cathodic phase hidden under a flat, high anodic
    phase: the worst-case ratio stays pinned to the flat anodic and never
    projects the cathodic crossing, so the ramp blows past it). Fix in
    `voltage_transient.py`: `_excursion_series(captures)` groups
    per-(electrode, phase) amplitude→E_pol trajectories;
    `_predict_target_regression` and `_local_secant_target` now fit/secant
    EACH trajectory and return the smallest crossover.
    `_excursion_limit_for(volt)` solves each excursion against ONLY its
    sign-matching limit (cathodic if E_pol<0, anodic if >0; `None` for a
    near-zero phase) — avoids a wrong-limit root landing at a small
    positive amplitude and dragging the `min` down to a needless fine-step
    crawl. `_fit_and_solve(..., target=)` was generalized to solve
    `y = target` (the limit voltage) instead of a hardcoded ratio = 1.
    `_polarization_ratio` (worst-case) is KEPT for the proportional SEED
    jump and the `increment` strategy (worst-case is the right conservative
    choice there). **Don't re-collapse the prediction to a single
    worst-case ratio** — fit each excursion. Tests:
    `tests/test_vt_all_excursions.py`.

63. **Marker-label placement must avoid the RIGHT-axis (I_mon) trace, not
    only the left-axis voltage traces.** Operator (screenshot): "The first
    access voltage/resistance is intersecting with the plot" — the R_a1
    line sat exactly on the teal I_mon trace. V_mon is golden-yellow on the
    LEFT axis; **I_mon is teal-cyan (`#00B4C8`) on the RIGHT axis** (a
    separate, zero-aligned `ViewBox` sharing the screen). `set_markers`'
    avoidance scorer built its trace list with `if self._curve_axis... !=
    "left": continue`, so it was BLIND to I_mon and placed a tag right on
    it. Fix in `widgets.py` `set_markers`: the avoidance set (now
    `avoid_traces`, was `left_traces`) includes right-axis curves too,
    MAPPING their y into the left-axis coordinate frame — both ViewBoxes
    share the screen with zeros aligned by `align_y_zeros` (which runs
    before `set_markers`), so a right value `r` maps to left
    `l_lo + (r-r_lo)/(r_hi-r_lo)·(l_hi-l_lo)`. All the box math is in
    left-axis data units, so the mapped right trace is scored on equal
    footing. **Don't re-add the `!= "left": continue` skip** — labels would
    sit on I_mon again. This ALSO fixed the recurring "third access
    voltage/resistance intersects the plot" complaint: the clustered access
    labels (V_a2 / V_a3 / Emc at a phase-2 leading edge) were being placed
    onto spots the scorer thought were clear because it couldn't see I_mon;
    with I_mon in the avoidance set they route around BOTH traces.
    `tests/test_marker_right_axis_avoid.py` includes a
    `test_clustered_access_labels_clear_of_traces` parametrized over
    cathodic-first AND anodic-first (operator: "consider different polarity
    and patterns") asserting every access label clears the waveform. Tests:
    `tests/test_marker_right_axis_avoid.py` (also forces a 1:1 axis range so
    a right value lands at a known left position, asserts the box clears it).

64. **Current quantization is PER-PHASE-SHAPE: 0.1 µA (100 nA) for a
    RECTANGULAR phase, 30 nA for a shaped phase.** Operator: "keep the
    current resolution at 0.1 µA for rectangular shapes.  I do not trust
    the 30 nA resolution of the stimulator but will use it for
    non-rectangular shapes."  The PlexStim 2.0 native grid is 30 nA and the
    `.pat` format accepts any integer nA, but the operator only trusts the
    device to deliver clean values on the 0.1 µA grid for rectangular
    pulses; shaped phases (ramp / sine / bowtie / halfpipe / speedbumps /
    exp / gaussian) need the finer 30 nA to render the curve smoothly.
    Constants: `config.STIM_CURRENT_STEP_RECT_NA = 100`,
    `STIM_CURRENT_STEP_FINE_NA = 30`.  Applied at:
    * `waveforms.build_pat_pairs` — the device amplitude: `amp_nA =
      round(a_k·1000 / step) · step` where `step = 100 if ph.shape ==
      SHAPE_RECTANGULAR else 30`, computed **per phase inside the loop**
      (NOT per-pattern) so the rect cathodic of a rect+exp-decay
      cap-coupled pair still lands on the 0.1 µA grid while the exp-decay
      uses 30 nA.  (Was `round(a_k·1000)` — the raw 1 nA grid.)
    * `PulsePattern.validate` — the sub-resolution-amplitude REJECT floor
      is per-phase: 0.1 µA for a rect phase, 30 nA for a shaped one (so a
      shaped phase may legitimately carry a sub-0.1-µA peak).
    * `PulsePattern.auto_balance(current_step_nA=None)` — defaults to
      `device_current_step_nA()` (a PATTERN-level helper: 100 if ALL phases
      rect, else 30) so charge balance is computed on the grid the device
      renders.  All-rect (rect-asym) and all-shaped patterns are fully
      consistent with the per-phase render; a MIXED pattern balances on the
      finer 30 nA (the shaped/adjusted phase's grid) and the rect head
      carries a negligible ≤½-step charge residual.
    **Don't revert `build_pat_pairs` to `round(a_k·1000)`** (raw 1 nA) —
    rectangular amplitudes must snap to 0.1 µA.  Tests:
    `tests/test_current_resolution.py`.

65. **PlexStim `load_channel` has a PER-CHANNEL content cache — an
    identical reload of an already-loaded channel is a no-op (Opt #1,
    efficiency).** A 16-channel monopolar VT sweep spent ~120 s (13 % of
    a 15-min run) in `load_channel`: every amplitude step reloaded the
    active channel (changes) PLUS the 15 static zero-amplitude unused
    channels (don't change — VT scales only amplitude, so
    `pattern.scaled(0.0)` is byte-identical every step). The device
    RETAINS each channel's loaded arbitrary pattern + period + reps
    across stop/start cycles (only `open()`/`close()`/`reinit()` =
    `PS_InitAllStim` clears it), so re-issuing `ps_set_pattern_type` /
    `ps_load_arb_pattern` / `ps_load_channel` for an unchanged channel is
    pure redundant USB-TMC traffic. `PlexonStimulator._channel_content_sig`
    (channel → `_content_signature(pattern)` tuple) gates an early
    `return` in `load_channel` when `_ch in _loaded_channels` AND content
    + period (`_channel_period_ms`) + reps (`_channel_reps`) ALL match.
    Naturally correct across config transitions: only channels whose ROLE
    changed (active↔zero) miss the cache and reload; the rest are skipped.
    The subsequent monopolar `PS_LoadAllChannels` commit (gotcha #29c /
    Task #26 — still ~2 s, still every step, REQUIRED for arming, faithful
    to `runVoltageTransient.m:513`) re-arms the retained patterns, so the
    unused channels keep ticking in cadence. **Invariants (don't break):**
    `_channel_content_sig` MUST be wiped in `open()` AND `close()`
    alongside `_channel_period_ms`/`_channel_reps` (the device-reset
    clears patterns); the cache entry is set LAST in `load_channel`, only
    after a fully successful load + read-back, so a partial/failed load
    never leaves a stale "already loaded" entry; `_content_signature` (the
    static method) must stay in lock-step with the pairs `build_pat_pairs`
    renders (it covers ONLY the .pat phase geometry — rate/reps are
    separate). Tests: `tests/test_plexon_timing_cache.py`
    (`test_load_channel_skips_redundant_reload_of_same_pattern`,
    `…_reloads_on_content_change`, `…_content_cache_is_per_channel`).

66. **The in-view / clip / screen-window checks read the CONFIRMED-VALUE
    (V/div, POSition) caches, not live `CHx:SCAle?`/`POSition?` (Opt #3,
    efficiency).** The shared rescale loop calls `channel_in_view` /
    `channel_is_clipped` / `_channel_screen_window_v` ~45×/capture — 2693
    `CHx:SCAle?`/`POSition?` round-trips (~42 s) on the operator's
    16-channel run. Every vertical scale/position WRITE already funnels
    through `set_channel_scale` (caches `_adapt_state[ch]["last_scale"]`)
    and `set_channel_position` (now caches `last_pos`); the ONLY other
    direct vertical-position write is `apply_channel_defaults`'
    `CHx:POSition 0` (also cached, via `_cache_channel_pos`).
    `set_channel_scale_and_position_for_range` funnels through both
    setters; `apply_default_scope_view` / `update_imon_vertical_scale` use
    `set_channel_scale`/`set_channel_position`. So `_cached_scale_pos(ch)`
    serves `(vpd, pos)` from cache and the four checks trust it, falling
    back to a live query ONLY when the cache is cold (first use, or a
    simulator without `_adapt_state`). `adapt_channel_scale` keeps
    `last_scale` coherent; it never writes POSition. The rescale loop's
    two remaining direct reads (`_pre_adapt_vpd` `CHx:SCAle?`, the
    recentre `_cur_pos` `CHx:POSition?`) also route through
    `_cached_scale_pos`, so a CONVERGED stationary pass (the loop already
    breaks on `not _any_rescaled`) now costs ZERO vertical-state
    round-trips. **Invariant:** if you add a NEW direct `CHx:POSition`
    write, it MUST call `_cache_channel_pos` (or the cache goes stale →
    wrong in-view decision → mis-sized V/div → under-reported
    polarization → unsafe water-window over-ramp). Tests:
    `tests/test_scope_scale_pos_cache.py`.

67. **The EXPORTED capture plot (`plotting.plot_capture`, matplotlib) has
    its OWN candidate-scoring label placer + an OUTSIDE legend — it is NOT
    the live pyqtgraph `ScopePlot.set_markers` scorer (that one is
    pyqtgraph-coordinate-bound).** Operator on a saved `.tif`: "the labels
    are intersecting with each other, the plot, and the axis … The legend
    itself is intersecting with the plot … indicate the voltage in the
    legend as voltage monitor." Three fixes, all in `plot_capture`:
    * **Legend label** "Voltage" → **"Voltage monitor"** (V_mon IS the
      voltage monitor); E_act/E_ret → "Active electrode" / "Return
      electrode".
    * **Legend moved OUTSIDE** the data area — a `fig.legend(loc="lower
      center", bbox_to_anchor=(0.5,0.005))` in a RESERVED bottom band
      (`tight_layout(rect=(0,0.09,1,0.94))`), NOT `ax.legend(loc="best")`
      (which sat over the rising trace). Must be `fig.legend` + reserved
      rect because `plot_capture` also feeds the INTERACTIVE POLARIS viewer
      (`gui/viewer.py`), which has no `bbox_inches="tight"` crop — relying
      on save-time cropping would clip it there.
    * **`_place_marker_labels_mpl`** replaces the old x-tier stacking:
      candidate scoring (4 diagonals + up/down × outward tiers) with the
      SAME three penalties as the live scorer — off-axes (hard, keeps tags
      off the spine), label↔label overlap (dominant), and GRADED
      label↔trace intersection. It avoids BOTH the left-axis voltage
      trace(s) AND the right-axis current density mapped into left-axis
      coords (zeros aligned + symmetric `_symmetric_ylim`, so the map is a
      half-range ratio — gotcha #63's both-axes rule, ported to matplotlib).
      X/Y limits are set BEFORE placement (the scorer needs the final
      bounds); `_symmetric_ylim(margin=0.18)` gives label headroom.
      **Gotcha within the gotcha:** label-box WIDTH is measured on the
      mathtext with markup STRIPPED (`_vis_len` removes `$ { } _ ^
      \mathrm`) — counting `len("$V_{\mathrm{a4}}$ = 1.403 V")` ≈ 27 vs the
      ~13 rendered glyphs inflated every box ~2× and shoved left-anchored
      tags far from their markers. Tests: `tests/test_export_plot_labels.py`
      (figure-legend + "Voltage monitor", pairwise non-overlap via rendered
      window extents, in-bounds). **Don't revert to `ax.legend(loc="best")`
      or the x-tier placer.**
    * **Marker-label placement is GEOMETRY-DRIVEN, NOT hardcoded per marker**
      (operator: "I am not saying lock to my specification … understand how
      to effectively place the labels … these placements could change based
      on polarity, number of phases, phase widths, interphase / discharge
      delay, and interpulse").  A short-lived experiment forced explicit
      corners per marker (V_a1 UR, V_a2 UL, …) keyed by phase polarity — it
      was REMOVED because it can't adapt.  The candidate scorer adapts to all
      those parameters because it reads the actual capture: **up/down follows
      the OPEN side of the trace** (the graded trace-intersection penalty
      pushes the box off the waveform), so when the polarity flips the trace
      body flips and the labels flip up↔down automatically (operator: "if we
      flip the polarity, all upper and lower become switched"); **left/right +
      cluster-spreading** come from the label↔label overlap penalty; and
      **proximity** keeps each tag next to its glyph.  **Both placers anchor
      the label to the marker GLYPH `y`, NOT the trace-envelope edge** — the
      export's `_label_reqs` passes `y=my` (the glyph), so the proximity term
      measures distance to the glyph and E_mc (whose marker sits on the steep
      interphase→anodic edge, with the nearest clear space far up-left) lands
      right next to its marker instead of fleeing across the plot.  Don't
      re-add a fixed-corner `placement` lookup; tune the scorer's penalties
      instead.  To SEE placement changes on an existing capture, the saved
      `.tif` must be REGENERATED (`plotting.export_session_plots(load_session_
      npz(...), out_dir)`) or re-opened in POLARIS — editing the plot code
      never rewrites an already-saved `.tif`.

68. **Plot axis ranges + ticks are MATLAB-faithful and IDENTICAL for the
    POLARIS export (matplotlib) and the live PULSAR plot (pyqtgraph)**
    (operator: "look at how my MATLAB code decided on x/y axis range … same
    for PULSAR and POLARIS … the axes need to begin and end with a tick …
    one tick before x = 0 … the current (density) plot should be in the
    back"). Ground truth = `getPlot_Tek.m` / `getAcutePlot3.m`:
    * **Y range** — `ylim('tickaligned')` → SYMMETRIC about 0 (`±max|lim|`)
      → step from the tick spacing → **extend the limit by one step if the
      last tick ≠ the limit** so the axis ENDS on a tick.  Export:
      `plotting._symmetric_ylim` (range = outermost tick `±n·step`,
      `_nice_step(big/3)`).  Live: `align_y_zeros` now forces BOTH axes
      symmetric + snaps each to `_snap_range_to_ticks` (two symmetric ranges
      keep the zeros coincident — the alignment it always guaranteed — AND
      both begin/end on a tick).  This REPLACED the old ratio-based
      zero-alignment.
    * **X range — limits snap OUTWARD to the HALF-tick (step/2) grid**
      (tick step from `_nice_step`/`_matlab_nice_tick_step` of the
      `round(min/max(time),1sig)` extent).  `xmin = floor(min(time)/half)·
      half`, `xmax = ceil(max(time)/half)·half` where `half = step/2`.  This
      pads each side by AT MOST half a tick and NEVER crops the data
      (operator: "allow padding the limits with 50 us" for a 100 µs tick);
      when a limit lands on a half-tick (x.5·step) the outermost full tick is
      EXCLUDED (operator: "I will allow for excluding the first and last
      ticks if half a length can fit").  Ticks stay on the full-step grid, so
      a half-tick limit just has no tick at the very edge.  Export:
      `plotting._matlab_x_limits`; live: `widgets._matlab_x_range`
      (`set_traces`).  Tests: `tests/test_x_range_halftick.py`.  This
      REPLACED the live plot's old `_asymmetric_pulse_xrange` framing (gotcha
      #36 — `_detect_pulse_span` and the #37 no-active-time-shift rule still
      stand).  **History (don't re-add either dead approach):** (1) a `−step`
      hardcoded left edge (one full tick before 0) left a >½-tick empty gap
      whenever the pre-pulse baseline was shorter than a tick (exp_vt_max:
      data from −45 µs framed to −100 µs); (2) a `round(max/step)·step` right
      edge CROPPED data that overshot a tick by <½ tick (data to 540 → axis
      to 500, losing 40 µs — a recurrence of "missing data after 500 µs");
      (3) an even earlier `…+ step` EXTRA trailing tick ran the axis into
      EMPTY space past the last sample.  The half-tick floor/ceil fixes all
      three.  The pre<post interpulse asymmetry the operator wants is created
      at the ACQUISITION level (trigger position — see below), NOT in the
      plot framing; the plot just frames whatever the record holds.
    * **Trigger position = SHORT pre / LONG post, at FULL precision (not
      snapped to 10%).**  `auto_layout_for_pulse` allocates the non-pulse
      part of the window ~¼ pre-trigger / ~¾ post (`offset_divs = max(1.0,
      free_divs·0.25)`, `free_divs = n_horiz_divs·(1−fill)`), replacing the
      MATLAB flat 3-4-div leading baseline (which left LESS room after a wide
      pulse than before → the "missing data" symptom).  `set_horizontal_
      position` no longer floors the % to a 10% grid (operator: "forget
      about my requirement of trigger percentage rounded") — the floor
      collapsed a few-% leading offset to 0% and jammed the leading edge
      against the trigger.  See §3 + gotcha #22 (Method P t=0 reads the
      cached exact %).
    * **Current density BEHIND voltage** — export: `ax_v.set_zorder(
      ax_i.get_zorder()+1)` + `ax_v.patch.set_visible(False)` (twinned axes
      draw the 2nd on top by default); live: `self._right_vb.setZValue(
      left_vb.zValue()-1)`.
    * **Removed** the two dashed right-axis min/max RAILS + their A/cm²
      labels (`show_minmax` is now ignored) and the x=0 / y=0 reference
      lines.
    * **Cosmetics (operator):** (1) **title + subtitle are one TIGHT block** —
      two `fig.text(va="top")` lines at y≈0.985/0.952 (NOT `suptitle` +
      axes-title, which left a wide gap); (2) **axis titles, spines (the
      "box"), tick marks AND tick numbers are all BLACK** — only the TRACES
      carry colour (the axis titles + legend disambiguate the two scales);
      (3) **both y-axes use the SAME `labelpad`** so the number→title gap
      matches left-vs-right (the right was 18 vs the left's ~4); (4) the
      ending-interphase marker is a **small FILLED ("closed") circle** (export
      `scatter(marker="o", s=16, facecolors=color)`; live `"o"` size 6 with a
      colour brush) — was an empty/outline circle.
    * **Marker-label avoidance is x-span-sensitive** (the candidate-box
      width scales with the x-span): a tiny feature in a very wide window
      can mis-place a tag, but real captures (pulse fills the view) are
      fine.  `tests/test_marker_right_axis_avoid.py` pins a feature-fills-
      view x-range for determinism.  Don't widen it.
    * **PROXIMITY penalty keeps each label NEAR its marker** (operator:
      "make sure the labels are near their markers … Emc and the second
      access voltage/resistance very far away from their intended
      locations").  Both placers (live `widgets.set_markers`, export
      `plotting._place_marker_labels_mpl`) add a distance-from-marker term
      to the candidate score — `6.0·(vertical gap / y_span) + 4.0·
      (horizontal gap / x_span)` — so among otherwise-clear candidates the
      CLOSEST wins, and a tag is never flung across the plot to find open
      space.  The trace/overlap penalties still dominate (labels never sit
      ON the trace), but they only DECIDE among near spots now.  **Companion
      fix**: `_trace_overlap` (live) floors a FLAT (zero-height-band) trace
      by how CENTRALLY the line sits in the box — without it the proximity
      pull parked a tag right on a horizontal trace (the band-overlap LENGTH
      is 0 for a line, so the trace penalty was 0).  Don't drop the
      centrality floor or the proximity weights.

69. **POLARIS opens PicoScope CSV captures (external-scope import).**
    Operator brought 4-channel PicoScope CSV exports (`Time,Channel A..D` +
    a units row `(us),(V),(mV)…`, blank line, data) — units VARY per file
    (a channel is V on one export, mV on another).  Pieces:
    * **`persistence.load_picoscope`** → `PicoScopeRecording` (time in µs,
      every channel normalised to **volts**, role-FREE — channels keep their
      `Channel A`… names).  MULTI-FORMAT (operator: "allow for opening
      picoscope data, CSV, TSV, and XLS(X)"): dispatches by extension —
      `.csv`/`.tsv`/other text → `_read_delimited_rows` (delimiter
      auto-detected: tab / comma / semicolon), `.xls`/`.xlsx`/`.xlsm` →
      `_read_excel_rows` (openpyxl; `.xls` needs pandas+xlrd).  All formats
      share `_picoscope_from_rows` (the units row, blank separators, BOM,
      mV/V + ms/s/ns units, trailing-NaN pad, variable channel count).
      `PICO_EXTENSIONS` is the canonical supported-extension tuple;
      `load_picoscope_csv` is a back-compat alias.
    * **`plotting.plot_picoscope`** → raw multi-channel plot (all channels on
      one V axis, MATLAB ticks, black axes, bottom legend).
    * **POLARIS** (`gui/viewer.py`): `KIND_PICO` tree node; the File→**Open…**
      filter + folder indexing accept every `PICO_EXTENSIONS` format (button/
      menu say "Open…", NOT "Open .npz"); `_show_picoscope` renders it.  By
      DEFAULT it shows the RAW channels (operator: "raw traces, no roles").
    * **Optional metrics** (operator: "also compute metrics"): the
      `_PicoRoleBar` (shown only for a pico node) maps each channel → role
      (V_mon / I_mon / E_act / E_ret), with a current-monitor scale
      (mV/µA, default 2.5 = PlexStim) + a "Compute metrics" toggle.  When a
      V_mon is assigned + the toggle is on, `persistence.picoscope_to_session`
      INFERS the pulse pattern from the current channel
      (`_infer_pattern_from_current`: square-wave segments → phases,
      gaps → interphase delays, trailing discharge mirrored) and runs
      `compute_metrics`, so the normal `plot_capture` (V_a/V_d/E_pol markers
      + metric table) renders.  The CSV has NO pattern metadata, so the
      current channel is what makes metrics possible; the **voltage** metrics
      (V_a/V_d/E_pol) only need the pattern TIMING (scale-free), the current
      SCALE only affects charge / R_a.  The channel→role map is per-file
      (units/roles vary), remembered within the session by channel name.
    * **PULSAR's OWN .xlsx exports are NOT scope captures — detect + reject**
      (operator: "Nothing is opening with the XLSX file" — it was a PULSAR
      session export, misparsed into garbage channels "LABEL"/"20:25:12"
      with nan time).  `.xlsx` is ambiguous: it's either an external
      PicoScope workbook OR a PULSAR `gamry_export` of a `.npz`.
      `persistence.is_pulsar_session_xlsx(path)` opens the workbook and
      returns True iff ≥2 of the gamry sheet names are present
      (`_PULSAR_XLSX_SHEETS` = Instrumentation / Parameters / Setup /
      Values).  `load_picoscope` raises a clear ValueError on one;
      `_picoscope_from_rows` ALSO guards on an all-NaN time column (defence
      in depth).  In POLARIS: `load_folder` SKIPS any scope file whose stem
      matches a sibling `.npz` (PULSAR writes `<stem>.npz` + `<stem>.xlsx`
      per session) AND any `.xlsx` detected as a PULSAR export — so the tree
      shows only genuine sessions + external captures (operator: "only show
      what is opened/imported"); explicit `Open…` of a PULSAR `.xlsx`
      redirects to the sibling `.npz` (or a "this is an export, open the
      .npz" dialog).  A REAL PicoScope `.xlsx` (no PULSAR sheets, no sibling
      `.npz`) still loads normally.  **Don't add `.xlsx` back to the
      folder-index without the PULSAR-export skip** — it re-floods the tree
      with derived exports.
    Tests: `tests/test_picoscope_import.py`,
    `tests/test_polaris_view_controls.py`.

70. **POLARIS review fixes (tables / grid / legend).**
    * **Gridline toggle was a no-op** — `ax.grid(False, linestyle=…)`
      RE-ENABLES the grid (matplotlib: "line properties supplied → grid will
      be enabled"), so every POLARIS plot always showed gridlines and the
      checkbox did nothing.  `plotting._grid(ax, on)` passes the style ONLY
      when enabling; route ALL grid calls through it (or the inline
      if/else used in the channel-map panel).  Don't write
      `ax.grid(show_grid, linestyle=…)` again.
    * **Three info tables** (operator: "Metric table is all wrong … another
      table for file info"): **Metric** (waveform metrics — a channel/combo
      shows the representative capture's V_a/R_a/V_d/E_pol/Q + run **max Q_inj**
      + **accumulated charge**, NOT just the file summary), **Parameters**
      (experiment only; "Counter" → **Return electrode**; reference →
      **"N/A (not used)"** when no E_act/E_ret recorded — `_session_uses_
      reference`; **Rate moved to the bottom**), and **File info**
      (`_session_file_rows`: notebook / subject / total runs / total captures
      / created).  Counter-electrode default label is now **"Pt"** (not "Pt
      counter").
    * **Legend below the axes** (operator: "legend overlapping the right y
      axis label … outside the figure"): `plot_capture` reserves a bottom
      band (`tight_layout(rect=(0,0.12,1,0.935))`) and anchors the legend
      `loc="upper center", bbox_to_anchor=(0.5, 0.11)` so it grows DOWN into
      that band — never up into the axes / right-axis title.  The OVERLAY
      legend stays on the RIGHT (many entries) as a **FIGURE legend, MEASURED
      then fitted**: `fig.legend(loc="upper right", bbox_to_anchor=(0.995,
      0.99))`, then `fig.canvas.draw()`, read the legend's `x0` (figure
      fraction), and `fig.subplots_adjust(right=x0 − gap)` (gap 0.12 with a
      right axis, 0.03 without).  This is the only version-robust way to kill
      BOTH the right-edge whitespace AND the right-axis-label overlap — the
      legend's true width (channel count × ncol) isn't known until it's
      drawn, so a fixed `tight_layout(rect=…)` / `bbox_to_anchor=(1.16,…)`
      always over- or under-reserves (operator hit it TWICE: "too much white
      space on the right … legend overlapping the right axis").  The `gap`
      is the reserve for the right-axis ticks + rotated label (filled by
      them, not whitespace).  **Don't revert to an axes legend + fixed
      rect.**
    * **Overlay right axis is HIDDEN when nothing is routed to it** (operator
      screenshot: empty "Current monitor (µA)" axis when I_mon is set to
      N/A): `plot_overlay` tracks `left_waves`/`right_waves` actually drawn
      and labels each axis via `_axis_unit_label` ("Voltage [V]" all-voltage,
      "Current [µA]" all-current, "Unit" mixed, None empty → hide ticks +
      label + right spine).  Don't hardcode "Current monitor".

70b. **POLARIS interactive view controls (all post-open, in `gui/viewer.py`).**
    Every render routes through `_finish_render()` (replaced the bare
    `canvas.draw_idle()` — there are NO direct `draw_idle` calls left in the
    render path except inside `_finish_render`/`_on_legend_pick`
    themselves; re-adding one bypasses every control below).  New methods
    defined on `ViewerWindow` MUST be appended to the
    `for _name in (...)` method-copy loop that mirrors them onto
    `ViewerPanel` — easy to forget, and the symptom is an `AttributeError`
    only on the embedded Results-tab panel (not the standalone window).
    **The loop copies only CALLABLES — a class-level DATA attribute on
    `ViewerWindow` is NOT transplanted onto `ViewerPanel`.**  This bit the
    Styles… dialog: `_LINESTYLE_CHOICES` was a `ViewerWindow` class attr, so
    `_open_style_dialog` (running on a `ViewerPanel`) raised `AttributeError`
    — SILENTLY, because Qt swallows exceptions in a clicked-signal slot, so
    "Styles… does nothing".  Fix: put shared constants at MODULE level
    (`_LINESTYLE_CHOICES` is now module-global) and reference them without
    `self.`.  Don't add a class data attr that the dialog/handlers read.
    * **Clickable legend toggle** (operator: "legend … checkbox per entry
      to toggle for viewing"): `_finish_render` builds `_legend_map`
      (legend line + text → data line(s), matched by label across ALL
      `fig.axes`), `set_picker(6)`, connects `pick_event` ONCE (guarded by
      `_legend_pick_connected`).  `_on_legend_pick` flips `set_visible` on
      the matched data line(s) + dims the entry (alpha 0.3).  It also syncs
      each legend proxy's color/linestyle FROM its data line so the swatch
      reflects style overrides.
    * **Axis-range overrides** (`_PlotViewBar`, item 2): X (all axes, shared),
      **Left Y** (voltage axis) AND **Right Y** (current/density axis) min/max,
      Auto by default (None = leave the plotter's MATLAB-faithful range).
      Applied in `_finish_render` AFTER the plot builds its own ranges.  The
      RIGHT axis is found by `ax.yaxis.get_label_position() == "right"` (the
      twin) — robust to the inset's extra axes (operator: "Allow for changing
      the right axis range").  Prefs: `ry_auto`/`ry_min`/`ry_max`.
    * **Current vs current-density toggle** (default CURRENT): `plot_capture`
      gained `density: bool = True` (the SAVED-figure default stays density —
      MATLAB-faithful; the `export_*` paths don't pass it).  POLARIS passes
      `density=self.view_bar.density()` (combo default index 0 = current),
      so its right axis defaults to "Current monitor (µA)".  `right_y` is
      computed once and reused for the marker-avoidance mapping.  **The
      OVERLAY honors the toggle too** (operator: "what happened to my request
      for … current density … instead of current" — it was wired only into
      `plot_capture`, NOT the overlay the user was viewing).  `plot_overlay`
      gained `density` + `areas_by_key` (key→`surface_area_um2`, built by
      `_set_overlay_for_session`/`_run` into `_overlay_areas_by_key`); on
      density it converts each I_mon trace `µA → A/cm²` per its key's area
      and relabels the right axis via `_axis_unit_label(right_waves,
      density=True)`.  Falls back to raw µA when a key has no/zero area.
      **`plot_capture`'s raw-current label is "Current (µA)" / legend
      "Current" — NOT "Current monitor"** (operator: "the capture view …
      should not be called 'Current monitor'"); only V_mon keeps the
      "Voltage monitor" legend name.
    * **POLARIS capture numbers are 1-BASED** (operator: "stop counting start
      at 0"): the tree capture labels (`_load_session_into_tree`), the
      run-overlay entry keys/labels + legend (`_set_overlay_for_run`,
      `#{cap.index + 1:03d}`), and the metric table "Capture #"
      (`_capture_metric_rows`) all add 1.  `cap.index` itself stays the
      0-based array index for data lookup; ONLY the human-facing text adds 1
      (same rule as `plot_capture`'s subtitle).
    * **Per-trace line-style + color** (operator, asked 3×: "choose the plot
      line style and color after opening"): `set_trace_style(label, color=,
      linestyle=)` stores into `_trace_styles` (keyed by legend label);
      `_finish_render` re-applies on EVERY render so overrides survive a
      capture/overlay switch.  `_open_style_dialog` is the UI ("Styles…"
      button); tests drive `set_trace_style` directly.
    * Prefs: `_PlotViewBar.prefs/restore` + `_PicoRoleBar.prefs/restore`
      round-trip under `view_bar` / `pico_role_bar` keys in the panel's
      `current_prefs`/`restore_prefs`.  `_trace_styles` is in-memory only
      (labels are file-specific).
    Tests: `tests/test_polaris_view_controls.py`.

70c. **POLARIS Gamry-style layout (channel column + add/remove files).**
    Operator (with the Gamry Echem Analyst 2 operator guide as the model):
    "the channel toggle messed up my viewing of the CSV … only have such
    toggles for multiple sheets in an Excel file"; "[the CH01-CH16 row]
    needs to be a column"; "Allow for add or remove files from view".
    * **Channel/waveform toggle bar is a vertical COLUMN to the LEFT of the
      canvas** (was a horizontal row above it).  `ChannelTraceToggleBar`'s
      `channel_row` is now a `QVBoxLayout` (kept the name for the
      insert-before-trailing-stretch code) inside a `QScrollArea`
      (`_channel_scroll`); the waveform axis dropdowns stack below it as
      compact label+combo HBox pairs; `setMaximumWidth(220)`.  The plot
      panel lays out `[trace_toggles | canvas]` in an HBox (`mid`).
    * **The toggle column is shown ONLY for the multi-channel OVERLAY**
      (`KIND_SESSION` / `KIND_RUN`) — `_on_tree_item` calls
      `trace_toggles.setVisible(kind in (SESSION, RUN))`; hidden for single
      captures + PicoScope (where it doesn't apply and cluttered the CSV
      view).  Starts hidden.  Per-trace VISIBILITY for non-overlay views is
      handled by the clickable legend toggle (#70b) instead.
    * **Add / remove files** (Gamry accumulates open files): `Open…`
      (`load_session_file`) ADDS to the tree + dedupes by resolved path
      (`_find_top_level_item_for_path`); `Open folder…` is now ADDITIVE
      (`load_folder(clear=False)`).  **`load_folder` keeps `clear=True` as
      its DEFAULT** so the embedded Results tab (`results_tab.py`, which
      re-points at the save dir) still REPLACES — only the standalone
      `on_open_folder` passes `clear=False`.  Remove: the "Remove" button,
      a right-click "Remove from view" (`_on_tree_context_menu`), and the
      Delete key all call `_remove_tree_item`, which walks up to the
      top-level branch, drops its `_sessions`/`_pico` cache entries
      (recursively), takes it out of the tree, and clears the plot/tables
      if nothing remains selected.  All four new methods
      (`_on_tree_context_menu`, `_remove_selected_item`,
      `_remove_tree_item`, `_find_top_level_item_for_path`) are in the
      `ViewerWindow → ViewerPanel` method-copy loop.
    Tests: `tests/test_polaris_view_controls.py`
    (`test_channel_toggles_are_a_column`,
    `test_toggle_bar_hidden_for_pico_shown_for_session`,
    `test_open_adds_and_remove_drops`).  Test gotcha: a `ViewerPanel` that
    was never `.show()`n reports `isVisible()==False` for every child
    (ancestor not shown) — assert on `isHidden()` (explicit-hide state) or
    `panel.show()` first.

71. **Run-lock is VIEW-ONLY: Setup + Test-parameters stay scrollable
    during a run** (operator: "allow for scrolling through the other tabs …
    Setup and Test Parameters" while an experiment runs).  The old lock
    `setEnabled(False)` on the WHOLE `setup_tab` / `params_page` propagated
    the disabled state to their `QScrollArea`s and FROZE scrolling (Qt
    computes effective-enabled as the AND of the ancestor chain — you can't
    re-enable a child of a disabled parent).  Fix: disable only the INNER
    content, leaving the tab + scroll area enabled so scrollbars stay live:
    * `SetupTab.set_run_locked(locked)` disables `_run_lock_content` (the
      scroll-inner form, which holds the Hardware/Connection panel, session,
      device, scope, acquisition, experiment groups) + `device_view` — so
      the operator still can't reconfigure device / coating / scope-mapping
      / hardware mid-run, but CAN scroll & read.  `MainWindow._on_run_state_
      changed` calls it (fallback to `setEnabled` for test stubs).
    * `_BaseExperimentTab._set_locked` disables the two scroll-inner widgets
      `_params_run_lock_content = (left_inner, right_inner)` instead of
      `params_page`.
    `res_tab` is still fully disabled (a click on a saved file could race
    the live capture pipeline — different reason; operator only asked for
    Setup + Test parameters).  **Don't go back to `setEnabled(False)` on the
    whole tab/page.**  Tests: `tests/test_run_view_and_sp_allchannels.py`.

72. **SP runs the SELECTED channel only + takes a FINAL capture at the
    end.** Two operator clarifications:
    * **Single-config** (operator: "do not capture all channels, just the
      selected channel") — `ShortPulsingTab.SINGLE_CONFIG = True` (and
      `ContinuousPulsingTab` too).  An earlier pass flipped SP to `False`
      ("capture all channels") then the operator reversed it.  The
      multi-config SAVE path (gotcha #46) still EXISTS but isn't driven from
      the UI (the combination panel stays single-select).  VT remains the
      "all channels" experiment (`SINGLE_CONFIG = False`).
    * **End capture** (operator: "When SP ends, add another capture") —
      `ShortPulsingExperiment.run` takes ONE extra snapshot after the
      duration loop exits, **guarded by `if not self.aborted`**.  The
      capture body is factored into a `_snapshot(idx, reset_before_run,
      context)` closure shared by the cadence loop + the final capture so
      they never drift.  For a typical short SP (cadence
      `max(2·duration, 60 s)` > duration) the loop takes only the OPENING
      snapshot, so this guarantees a start-vs-end pair.  Continuous Pulsing
      inherits SP's `run()` but exits ONLY via the abort flag (inf
      duration), so `if not self.aborted` correctly SKIPS the extra capture
      for CP (and for an operator Stop on SP).  Tests:
      `tests/test_run_view_and_sp_allchannels.py`.

73. **PS stopping conditions are explicit CHOICES** (operator: "Let the
    stopping conditions be choices: maximum current and voltage compliance …
    and manual stop").  The Progressive Stress tab has a "Stop the ramp
    when…" group with three rows:
    * **Maximum current** (`stop_on_max_current`, default ON) — stop at the
      "Maximum current" spinbox value.  When OFF, the spinbox is greyed and
      the ramp runs to the PlexStim hardware ceiling
      (`STIM_MAX_AMPLITUDE_UA`, 1000 µA) — `_effective_max_ua()` returns the
      spinbox value when on, the hardware limit when off, and is used for
      BOTH the `StressPolicy.max_ua` AND the staircase-plot y-extent AND the
      pre-run damage screen's worst-case amplitude.  (The hardware limit is
      ALWAYS the ultimate backstop — the ramp can never exceed it — so
      "disabling" the max-current stop just means "ramp to 1000 µA.")
    * **Voltage compliance** (`stop_on_compliance`, default ON) — unchanged;
      → `StressPolicy.stop_on_voltage_compliance`.
    * **Manual stop** (`stop_manual`) — checked + DISABLED (always available
      via the Stop button; shown for completeness, can't be turned off).
    `stop_on_max_current` round-trips in `PREF_FIELDS`.  There is still NO
    automatic electrode-failure stop (the capacitive→faradaic detector was
    removed — it's for DC encapsulation breakdown, not AC stim-electrode
    failure).  Tests: `tests/test_ps_stop_conditions.py`.

74. **Run-end notifications do EMAIL + TEXT, gated by ONE toggle** (operator:
    "Allow for a phone number option for text messages").  The Setup tab's
    "Notify on finish (email / text)" checkbox (`email_notifications`, the
    legacy attribute/pref name — don't rename) gates BOTH:
    * **Email** → the User-email recipient (`send_completion_email` /
      `send_error_email`), can attach the saved `.npz`/`.xlsx`.
    * **Text** → the User-phone + Carrier via the email-to-SMS gateway
      (`send_sms_via_gateway`; `SMS_GATEWAYS` maps carrier-key → domain,
      e.g. verizon → `vtext.com`).  Setup-tab `user_phone` (digits-only via
      `current_user_phone()`) + `user_carrier` combo (display label →
      gateway-key `itemData`); a new `smsRecipientChanged(phone, carrier)`
      signal forwards to each tab's `set_sms_recipient` (mirrors
      `userIdentityChanged`/`set_user_identity`), cached as
      `_sms_phone`/`_sms_carrier` and passed to `RunnerWorker`.
    Both share the SMTP creds (env vars or `~/.stimtest/email_config.json`,
    NEVER hardcoded/bundled — a shared lab Gmail needs a per-machine App
    Password).  `RunnerWorker._maybe_send_{completion,failure}_email` now
    gate on `email_notifications` ALONE, then send the email when a
    `user_email` exists AND the text when phone+carrier exist (so phone-only
    users still get texted).  Every send is wrapped so a missing recipient /
    unconfigured SMTP logs a "skipped" line and never raises.  `user_phone`
    / `user_carrier` round-trip in Setup prefs.  Tests:
    `tests/test_sms_notifications.py`.

75. **PS is "periodic VT capture at fixed charge per step" — it does NOT
    pause to ramp** (operator: "There is no pausing for PS experiment.  It
    is essentially capturing the VT at the fixed charge periodically at each
    current step.  I want to track all metrics over time…").  A short-lived
    attempt added an LP-style per-step `_characterize` sub-VT (which
    `stop_all`s + ramps); it was REVERTED because PS must pulse continuously
    at the step's fixed amplitude and only CAPTURE periodically.  PS's
    existing snapshot loop already does exactly this: every
    `sampling_period_s` it grabs one averaged capture, runs the FULL
    `compute_metrics`, tags it `step=<amp>uA`, records the dose, and appends
    to the time-ordered `run.captures` — so every metric is tracked over
    time (across steps) for post-hoc failure-marker analysis.  Do NOT
    reintroduce a pausing sub-VT characterization in PS.  The future
    failure-marker becomes a STOPPING CONDITION once the operator's
    data analysis identifies it (gotcha #73 / the deferred failure detector).

PULSAR writes per-session log files to `<repo>/test/` (see §9
for the full inventory).  These accumulate observations the
operator never sees in real time — slow scope calls, repeated
warnings, ineffective fixes, USB stalls, mid-run errors that
get swallowed by retries.

**Discipline:** at the start of any conversation that touches
runners / scope / stim / GUI behavior, check whether the log
files have changed mtime since the last analysis pass.  If they
have, read them, surface findings.

**Where findings live:**

* **Critical bugs** (correctness, data loss, hardware contention)
  → file as tasks in the tracker so they can't be forgotten.
* **Performance issues + UX papercuts** → append to
  `LOG_ANALYSIS.md` at the repo root (gitignored alongside
  AGENTS.md).  Most-recent-first dated sections.
* **Fixed items that taught us something durable** → distill
  into a AGENTS.md gotcha (§12), then remove the
  LOG_ANALYSIS.md entry (the gotcha is the permanent record).

**What to grep for in a session log** (rough priority order):

1. `Traceback` / `Exception` / `Error:` / `ERROR` / `KeyError`
   — actual exceptions that escaped a try/except
2. `FAILED` / `failed:` — operations that errored under our
   own try/except (often more informative than raw exceptions)
3. `⚠` — warnings we deliberately emitted (per-channel probe
   complaints, STILL OUT-OF-VIEW, USB stalls, etc.).  NOTE: the old
   "trigger/pulse alignment off" ⚠ was REMOVED — operator triggers on
   the digital sync (not I_mon), and at small phase widths there is a
   real V_mon/I_mon edge skew, so the I_mon-edge position was a
   false-positive signal.  `check_trigger_alignment` still returns the
   edge time but no longer warns.  Don't re-add it.
4. `\(\d+\.\d+ s\)` or `\([5-9]\d{2,}\.\d+ ms\)` — operations
   slower than 500 ms; common culprits: USB stalls, scope-side
   buffer reallocs, slow CURVe? transfers
5. `[partial-save] failed` (introduced Task #54) — disk / path
   problems during incremental save
6. `HARDWARE DISCONNECT` (introduced Task #55) — confirmed
   mid-run hardware unplugs

**The faulthandler log** (`pulsar_faulthandler.log`) is
append-only across sessions — every C-level crash leaves a
stack frame.  Check the LATEST timestamp and compare against
the commit graph: a HEAP_CORRUPTION on a date BEFORE the
`_is_open` fix is historical; one AFTER is a regression to
chase immediately.

**The launcher log** (`pulsar_launcher.log`) is overwritten per
launch; just confirms which Python version got selected.  Useful
when a launch silently failed and you want to know whether the
VBS script even ran.

---

## 14. When in doubt

- Read the docstring at the top of the file you're editing — most
  files have a multi-paragraph "what this is and why" header.
- Search this repo's git log for the file path; recent commits
  usually carry the rationale for non-obvious choices.
- MATLAB ground truth lives in `matlab_reference/`. Cite the .m file
  name + line number in code comments when porting (
  `# Port of getAccess.m lines 200-265`).
- Cross-check against the test suite at every step.
  `tests/test_*.py` cover every metric formula, persistence round-trip,
  and the simulator's behaviour. If you change a metric, add or update
  a test.

---

*This file is the source of truth for "how do I work on this codebase."
Keep it in sync with `README.md` (user-facing) and `CONTRIBUTING.md`
(developer workflow). When a recurring convention emerges in
conversation, codify it here so the next agent finds it on first read.*

## Imported Claude Cowork project instructions

Translate this MATLAB program to Python. Develop a GUI to operate the program, too.  This program is to operate a Plexon PlexStim Electrical Stimulation System and a Tektronix oscilloscope (automatically know model and operate commands). This program is for characterizing and testing stimulation electrodes: determine the maximum charge-injection capacity, driving voltage, access voltage (every phase, leading and trailing), access resistance (every phase, leading and trailing), driving capacitance (maximum charge-injection capacity / driving voltage), electrode polarization (12 us after each phase), short-term pulsing, long-term pulsing with customizable periodic characterization to track metrics, and stepped-current pulsing with frequent characterization to track metrics.
