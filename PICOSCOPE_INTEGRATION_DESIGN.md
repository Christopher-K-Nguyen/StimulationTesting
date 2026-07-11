# PicoScope oscilloscope backend — integration design

**Status:** design complete, implementation blocked on the operator's PicoScope
model/series + a short on-hardware verification spike (Phase 0). Grounded in the
official Pico Technology `picosdk` Python wrapper + PicoSDK programmer's guides.

## Why this is a clean fit

The whole app talks to the scope **only** through the abstract
`stimtest.hardware.base.Oscilloscope` class. A new backend just implements that
contract; **no experiment-runner, calibration, or plotting code changes.** The
Tektronix driver is already one `TektronixOscilloscope` class with a frozen
`TekCommandSet` dataclass injected for per-dialect differences — the PicoScope
API has the exact same shape (every series submodule `ps5000a`/`ps2000a`/… has
parallel functions with its own enum values, range ladder, and timebase
formula), so we mirror that precedent.

## Architecture

**One `PicoScopeOscilloscope(Oscilloscope)` class** parameterised by a frozen
`PicoSeriesAdapter` (like `TekCommandSet`), resolved from a `PICO_SERIES`
registry:

```python
@dataclass(frozen=True)
class PicoSeriesAdapter:
    module_name: str            # "picosdk.ps5000a"
    fn_prefix: str              # "ps5000a"
    RANGE_MV: tuple             # index == API range enum: (10,20,50,100,200,500,1000,2000,5000,10000,20000,50000)
    has_ext_trigger: bool
    supports_resolution: bool   # ps2000a: False (fixed 8-bit)
    n_vert_divs: int = 10
```

- **Virtual division grid.** A PicoScope has no physical divisions, but the
  app's rescale/clip machinery is expressed in `_n_vert_divs`/`_half_vert_divs`.
  The driver adopts a fixed virtual 10×10 face (`_half_vert_divs = 5`,
  `MAX_FACTOR = 4.95`) and defines `volts_per_div = range_volts /
  _half_vert_divs`, so the shared rescale loop works unchanged.
- **CH1..CH4 nomenclature kept** at the contract boundary; mapped to Pico A..D
  internally (`channel_aliases`, `make_capture`, prefs all untouched).
- **No VISA/SCPI/serial layer at all** — native USB via PicoSDK ctypes. The
  entire `_w`/`_q`/SCPI-error-queue/serial-transport machinery of `tektronix.py`
  disappears, along with the 10-30 s record-length buffer-realloc stall (Tek
  gotcha #26) and its GUI-thread event-pump.

## The four behavioural differences from Tektronix

1. **Fixed range ladder replaces continuous V/div.** Pico gain is a discrete
   ±full-scale range (±10 mV … ±20 V, 1-2-5 decade steps), not V/div and not
   the Tek fine 3-sig-fig grid. `set_channel_scale(vpd)` computes the needed
   peak (`vpd × _half_vert_divs`) and snaps **up** to the smallest containing
   rung. The rescale loop takes fewer, coarser steps (acceptable for the
   safety-critical clip/grow path; vertical fills are slightly less tight).
2. **No hardware multi-trigger averaging.** Tek `NUMAVg` (average of N
   *triggered* frames — used everywhere for SNR) has **no** block-mode
   equivalent; `RATIO_MODE_AVERAGE` only averages samples *within* one capture
   (decimation). **AVERAGE mode is built in software via rapid-block:**
   `MemorySegments(N)` + `SetNoOfCaptures(N)` + one `RunBlock` +
   `GetValuesBulk` + `numpy.mean`. Rapid-block arms the trigger **once** and
   captures N triggered frames back-to-back — critical at low pulse rates where
   a re-arm loop would pay N USB round-trips. Per-segment memory shrinks as N
   grows, so N is bounded by `GetMaxSegments` (→ `max_average_count`).
3. **Direct pre/post-sample window** replaces SEC/DIV + trigger-position +
   record-length + XZEro reconstruction. `RunBlock(preTrig, postTrig, timebase)`
   captures **exactly** pre+post samples with the trigger at index=pre, so t=0
   is deterministic — **no off-screen record tail, no `DATa:STOP`, no Method-P/
   XZEro firmware quirk** (Tek gotcha #22). `set_horizontal_scale` /
   `set_record_length` / `set_horizontal_position` collapse into a
   `(timebase, pre, post)` computation. Always trust `GetTimebase2`'s **returned**
   interval for the µs time axis (resolution-dependent; never the closed form).
4. **Analogue offset vs position.** Pico `analogueOffset` (volts, on
   `SetChannel`) is a **real hardware input shift before digitisation** — it
   genuinely re-centres a DC-biased E_ret/E_act onto a smaller/more sensitive
   range, unlike Tek `CHx:POSition` which only moves the display. This makes the
   DC-dominated regime (gotcha #13) and the DC→AC trick (gotcha #85) simpler and
   *better* on Pico.

## How this fixes the CWRU tail noise "by construction"

The Tek artifact (gotchas #81/#82): every `CURVe?` transfers the **full**
hardware record (which is longer than the on-screen window), so PULSAR pulls in
off-screen tail samples — the next-pulse onset / trigger re-arm the operator
never sees — contaminating the interpulse tail. On Tektronix the whole-record
transfer *is* the disease and `DATa:STOP` was the treatment.

PicoScope block mode makes it **impossible**: `RunBlock(preTrig, postTrig)`
captures exactly pre+post samples and **nothing else** — there is no record
beyond the window you ask for. The driver sizes `post = pulse + recovery`, so the
capture ends just after the pulse+discharge and never reaches the interpulse/
re-arm region. Plus the stable hardware trigger at sample index=pre removes the
I_mon-edge jitter that (under averaging) smears the Tek far tail.

## Contract mapping (highlights)

| `Oscilloscope` method | PicoScope implementation |
|---|---|
| `open()` | `psXOpenUnit` (+ `ChangePowerSource` on USB3 status 286/282); cache `MaximumValue` (ADC full-scale) + `GetMaxSegments`; `GetUnitInfo` → `ScopeInfo`. `resource` = Pico **serial** (None = first unit). |
| `set_channel_scale(vpd)` | peak = vpd×`_half_vert_divs`; pick smallest `RANGE_MV` rung ≥ peak; `SetChannel(enabled, coupling, range, analogueOffset)`. |
| `set_horizontal_scale(spd)` | search timebase index via `GetTimebase2` until returned interval ≤ target; cache the **returned** interval. |
| `set_horizontal_position(pct)` | `pre = round(pct/100 × record_length)`, `post = record_length − pre`. |
| `single_capture()` | `RunBlock(pre,post,tb)` → poll `IsReady` (timeout/abort here) → `SetDataBuffer` → `GetValues` → `raw×range_v/maxADC`; `time_us = (arange−pre)×dt`. |
| `capture_single_sequence(n)` | **rapid block**: `MemorySegments(N)`+`SetNoOfCaptures(N)`+one `RunBlock`+`GetValuesBulk`+`numpy.mean`. |
| `set_acquisition_mode(mode,n)` | SAMPLE→single block; AVERAGE→rapid-block N segments averaged (purely which path runs — no hardware register). |
| `set_average_count`/`max_average_count` | arbitrary N up to `GetMaxSegments`; base spinbox path is correct (no power-of-two grid). |
| `set_trigger(source,level,slope,mode)` | `SetSimpleTrigger(source_enum, mV2adc(level, channel_range), RISING/FALLING, delay=0, autoTrig)`. `EXT`→dedicated ±5 V BNC (frees all analog channels). |
| `set_channel_position(divs)` | `analogueOffset = −divs×vpd` (real hardware shift; sign to verify on HW). |
| `set_channel_coupling(AC/DC)` | `SetChannel` coupling enum. |
| `channel_is_clipped` | prefer the native per-channel **overflow** flag from `GetValues`/`GetValuesBulk` (hardware clip flag — cheaper + better than sample inspection). |
| `settle_one_acquisition` | arm a throwaway `RunBlock`, poll `IsReady`, discard without `GetValues`. |
| `adapt_channel_scale` | stateful ladder-stepping autorange (grow/shrink one rung) — same return contract; can start as base None (loop falls back to `set_channel_scale_and_position_for_range`). |

## Factory + GUI wiring (backend-agnostic elsewhere)

```python
def open_oscilloscope(simulate=False, resource=None, *, backend="tektronix",
                      pico_series="ps5000a", pico_resolution_bits=12):
    if simulate: return SimulatedOscilloscope()
    if backend == "pico":
        from .picoscope import PicoScopeOscilloscope
        return PicoScopeOscilloscope(series=pico_series, resource=resource,
                                     resolution_bits=pico_resolution_bits)
    from .tektronix import TektronixOscilloscope
    return TektronixOscilloscope(resource=resource)   # default unchanged
```

- **ConnectionPanel**: add a "Scope backend" dropdown (Tektronix / PicoScope);
  when PicoScope is picked, show "Series" (from `PICO_SERIES` keys) + "Resolution"
  (8/12/14/15/16-bit) dropdowns. Round-trip under new prefs keys
  `scope_backend` / `pico_series` / `pico_resolution_bits`; log via
  `settingChanged`. The resource field becomes the Pico **serial** (relabel
  dynamically); the "Refresh scopes" button branches to Pico enumeration.
- `cmd_logger`, `_emit_log_from_worker`, `scopeConnected`, and
  `apply_scope_capabilities` are all backend-agnostic — the Pico driver exposes
  the same `cmd_logger` attr + `ScopeInfo`, so they work unchanged.

## Deployment / packaging

`pip install picosdk` ships **only** the ctypes glue — the native PicoSDK C
libraries (`psX000a.dll`) must be installed separately and match the frozen
app's 64-bit process (analogous to the vendored PlexStim DLL / NI-VISA). This is
a real `build.py` preflight + `.spec` bundling task (Phase 4).

## Effort (core ≈ 1.5-2 weeks + bench time)

- **Phase 0 — spike on the unit (0.5-1 day, BLOCKED on model + hardware):**
  confirm `picosdk` installs + native SDK present/64-bit; run the vendor block +
  rapid-block examples; capture one real stim pulse; byte-verify the exact enum
  values / `SetDataBuffer(s)` arg lists / `analogueOffset` sign / EXT threshold
  scaling for **this** series (the research flagged these as inferred).
- **Phase 1 — core driver, single-capture path (2-3 days).**
- **Phase 2 — averaging + rescale parity (2-3 days).**
- **Phase 3 — factory + GUI wiring (1 day).**
- **Phase 4 — packaging + bench validation (1-2 days):** bundle native libs;
  full VT/PS/SP/LP bench run comparing metrics against the Tek path.
- Deferred: bias-feedback `gate_measurement_window`/`measure_mean` (slice-mean);
  pulse-width trigger via advanced-trigger API; `ps2000a`/`ps4000a` registry
  entries (each needs its own Phase-0 spike, ~2-3 days).

## Open questions (blocking, in priority order)

1. **Exact PicoScope model/series?** (e.g. 5244D vs 2408B vs 4824.) Picks the
   `picosdk` submodule, range ladder, resolution support, channel count,
   timebase formula, segment ceiling. Nothing downstream finalises without it.
2. **Channel count** — 2 or 4? (App maps up to 4 roles.) A dedicated EXT BNC
   lets the digital sync trigger without consuming a channel — solves the
   2-channel-Tek "I_mon must be the trigger" problem.
3. **Native PicoSDK C runtime installed on the bench + build machines, 64-bit?**
4. **Trigger wiring** — Plexon digital sync → EXT/AUX BNC (recommended, frees
   all analog channels) or trigger on the I_mon channel edge?
5. **Default vertical resolution** (5000A FlexRes) — I propose 12-14 bit; higher
   bits reduce max sample rate + channel count (15-bit=2ch, 16-bit=1ch).
6. **Expected record length + sample interval** for the stim windows (Tek path
   uses ~20k samples @ ~32 ns) — sets pre/post sizing + the per-segment memory
   budget vs `GetMaxSegments`.
