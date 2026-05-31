# STM32 Interpulse Bias Module — Protocol v1.0

Canonical protocol spec for the serial interface between PULSAR
(`stimtest`) and a NUCLEO-64 STM32G474RE running the **Interpulse
Potential Bias** firmware (IPB-001 and successors).

This is the **single source of truth** for both sides:

- The Python driver in `stimtest/hardware/stm32_bias.py` matches
  this spec exactly.
- Any firmware claiming compatibility with PULSAR MUST implement
  these commands and responses with the exact strings shown.

When the spec changes, bump the version number in `*IDN?` and bump
this document's title; PULSAR's driver `__init__` will refuse
to talk to firmware whose `*IDN?` version doesn't match its
supported range.

---

## 1. Physical layer

| Setting | Value |
|---|---|
| Connector | USB-B (the Nucleo's onboard ST-LINK USB) |
| Class | USB CDC ACM (virtual COM port) |
| Baud rate | **115200** |
| Frame | 8N1 (8 data bits, no parity, 1 stop bit) |
| Flow control | None (no RTS/CTS, no XON/XOFF) |
| Endianness | n/a — ASCII line protocol |
| Line ending | **`\n` (LF) only** — no CR, no CRLF |

**USB IDs.** The Nucleo's ST-LINK presents as
`VID:0x0483 PID:0x374B` (ST-LINK/V2.1 with VCP). PULSAR's
auto-discovery (`STM32BiasModule.auto_discover()`) enumerates
COM ports and prefers entries matching this VID/PID. Match is
non-fatal — operator can also pick a port manually from the
`BiasConnector` combo.

**Reset behaviour.** Opening the serial port toggles DTR on most
hosts, which can soft-reset the STM32. Firmware MUST tolerate this:
it should re-initialize cleanly and be ready to respond to `*IDN?`
within 500 ms of the port opening. PULSAR's driver waits up to
1000 ms for the first `*IDN?` reply on `open()`.

---

## 2. Command syntax

* All commands are ASCII text terminated by a single `\n`.
* Case-insensitive (firmware lowercases before matching).
  PULSAR sends uppercase.
* Commands with `?` are queries → device responds with one line
  terminated by `\n`.
* Commands without `?` are setters → device responds with the
  literal `OK\n` on success, or pushes an error onto the queue
  (queryable via `SYSTem:ERRor?`) and responds with `ERR\n`.
* Numeric arguments: SI units (V, A, s); scientific notation
  accepted (`1.5e-4`). Whitespace between command and argument;
  multiple arguments comma-separated.
* Maximum command length: 80 ASCII chars including `\n`.
* Maximum query response: 65 535 bytes (sized to fit a large
  `LOG:DATA?` dump).

---

## 3. Command reference

### 3.1 Identification & system

| Command | Response | Notes |
|---|---|---|
| `*IDN?` | `STMicroelectronics,Nucleo-G474RE,IPB-001,fw=0.1.0\n` | Comma-separated: manufacturer, model, hardware-id, fw-version |
| `*RST` | `OK\n` | Reset to safe state: bias OFF, voltage 0 V, holdoff 0 µs, trigger polarity LOW, log cleared. MUST NOT close the serial port. |
| `SYSTem:VERSion?` | `0.1.0\n` | Bare firmware version string |
| `SYSTem:ERRor?` | `<code>,"<msg>"\n` | Dequeue one error. `0,"No error"` when queue empty. See §4. |

### 3.2 Bias control

| Command | Response | Default | Notes |
|---|---|---|---|
| `BIAS:VOLTage <v>` | `OK\n` | 0.0 | Set bias voltage (V). Range device-dependent (typ. ±5 V). Out-of-range → error −200. |
| `BIAS:VOLTage?` | `<v>\n` | | Programmed value, NOT measured. Use `MEAS:VOLT?` for actual DAC output. |
| `BIAS:ENABle ON\|OFF` | `OK\n` | OFF | Master enable. OFF disconnects DAC output regardless of trigger state. |
| `BIAS:ENABle?` | `1\|0\n` | | |
| `BIAS:HOLDoff <us>` | `OK\n` | 0 | Microsecond delay after TTL falling edge before DAC engages. Used to let the post-pulse discharge settle. Range 0–10000. |
| `BIAS:HOLDoff?` | `<us>\n` | | |
| `BIAS:CURRent:LIMit <a>` | `OK\n` | 100e-6 | Compliance limit (A). On exceed, firmware folds DAC to 0 V, sets `BIAS:ENAB` to OFF, pushes error −310. |
| `BIAS:CURRent:LIMit?` | `<a>\n` | | |

### 3.3 Trigger config

| Command | Response | Default | Notes |
|---|---|---|---|
| `TRIGger:POLarity LOW\|HIGH` | `OK\n` | **LOW** | TTL level on which bias engages. LOW = bias during interpulse interval (PlexStim TTL is HIGH during pulse, LOW between). |
| `TRIGger:POLarity?` | `LOW\|HIGH\n` | | |
| `TRIGger:SOURce GPIO\|MAN\|CONT` | `OK\n` | GPIO | `GPIO` = react to TTL on the trigger input pin. `MAN` = ignore trigger, bias engages only when `BIAS:ENAB ON` (test mode). `CONT` = always engage when `BIAS:ENAB ON` (calibration). |
| `TRIGger:SOURce?` | `GPIO\|MAN\|CONT\n` | | |
| `TRIGger:WATCHdog <ms>` | `OK\n` | 5000 | If trigger line is stuck in the bias-engaging state for more than this duration without an opposing transition, firmware auto-disables bias and pushes error −330 ("Trigger watchdog"). Prevents indefinite bias if PlexStim crashes mid-train. Range 0 (disable) to 60000. |
| `TRIGger:WATCHdog?` | `<ms>\n` | | |

### 3.4 Measurement — live snapshot

Instantaneous reads. PULSAR uses these for status display + the
ConnectionPanel's live bias indicator.

| Command | Response | Notes |
|---|---|---|
| `MEASure:VOLTage?` | `<v>\n` | Actual DAC output voltage (V). May differ from `BIAS:VOLT?` if compliance fold-back fired, or if the DAC is currently disengaged (bias state = 0). |
| `MEASure:CURRent?` | `<a>\n` | Bias current through the output path (A). Zero when bias disengaged. |
| `MEASure:ELECtrode?` | `<v>\n` | Electrode potential as seen by STM32's ADC (V). Optional — firmware returns `NAN\n` if the hardware lacks an electrode-V sense path. |
| `MEASure:STATe?` | `1\|0\n` | `1` = DAC currently driving (bias engaged), `0` = high-impedance / disabled |

### 3.5 Measurement — buffered log

PULSAR uses this for per-capture readback into session `.npz`
files. Firmware maintains a circular buffer at `LOG:RATE` Hz with
one entry = `(t_us_relative, bias_v, bias_a, elec_v)`. PULSAR
arms the log just before triggering a capture, then drains the
log after the capture completes.

| Command | Response | Default | Notes |
|---|---|---|---|
| `LOG:RATE <hz>` | `OK\n` | 10000 | Sample rate. Range device-dependent (1–100000). Higher rates fill the buffer faster. |
| `LOG:RATE?` | `<hz>\n` | | |
| `LOG:CAPacity?` | `<n>\n` | | Buffer capacity in entries (firmware-defined, e.g. 4096 → 410 ms at 10 kSPS). |
| `LOG:CLEar` | `OK\n` | | Empty the buffer and reset the time origin. |
| `LOG:STARt` | `OK\n` | | Begin sampling. Idempotent if already running. |
| `LOG:STOP` | `OK\n` | | Stop sampling. Buffer contents preserved. Idempotent. |
| `LOG:STATe?` | `RUN\|STOP\n` | STOP | |
| `LOG:POINts?` | `<n>\n` | | Number of samples currently in buffer (≤ capacity). |
| `LOG:OVERflow?` | `1\|0\n` | | `1` if the buffer wrapped since last `LOG:CLEar` (oldest samples overwritten). |
| `LOG:DATA?` | CSV block, then `END\n` | | See §3.5.1 |

#### 3.5.1 `LOG:DATA?` response format

The response is a multi-line CSV block. First line is a fixed
header. Each subsequent line is one sample. Block terminator is
the literal line `END\n`.

```
t_us,bias_v,bias_a,elec_v\n
0,0.250,1.05e-5,0.241\n
100,0.250,1.04e-5,0.240\n
200,0.250,1.05e-5,0.241\n
...
END\n
```

* `t_us` is microseconds from `LOG:CLEar` (or from `LOG:STARt`
  after a clear), monotonic, may wrap to a 32-bit limit
  (~71 minutes) — PULSAR's driver detects wrap and stitches.
* Fields are comma-separated; no trailing comma; no spaces.
* Floating-point values use `%.4g`-style formatting (≤ 6
  significant digits). NaN is the literal `NAN` (uppercase).
* PULSAR reads lines until receiving `END\n`. If 65 535 bytes
  arrive without `END`, PULSAR raises a `TimeoutError` and the
  driver attempts `*RST`.

---

## 4. Error queue

Errors push onto a 16-deep FIFO queue inside the firmware. Each
`SYSTem:ERRor?` query pops one. Empty queue returns
`0,"No error"`. If the queue overflows, the firmware silently
drops the oldest entry and pushes error −350 to the head of the
remaining queue.

| Code | Class | Meaning |
|---|---|---|
| 0 | — | No error |
| −100 | Command | Unparseable command (syntax) |
| −110 | Command | Unknown command or alias |
| −200 | Execution | Argument out of range |
| −220 | Parameter | Argument format wrong (e.g., text where number expected) |
| −300 | Device | Generic hardware fault |
| −310 | Device | Compliance fault — bias current exceeded limit; bias auto-disabled |
| −330 | Device | Trigger watchdog — trigger line stuck; bias auto-disabled |
| −340 | Device | Calibration error at startup; DAC/ADC unusable |
| −350 | Queue | Error queue overflow (older errors lost) |
| −400 | Query | Query interrupted (PULSAR sent another command before reading the previous response) |

---

## 5. Operational sequence (canonical capture cycle)

This is the order PULSAR's experiment runners use the bias module
during a capture:

1. **At PULSAR startup** (or first Initialize press):
   - `*IDN?` → confirm firmware match
   - `*RST` → safe state
   - `SYSTem:ERRor?` → drain any startup errors
   - `LOG:CAPacity?` → record buffer size for runtime planning
2. **At Setup tab Initialize**:
   - `BIAS:VOLTage <v>` ← from Setup-tab bias-voltage spinbox
   - `BIAS:HOLDoff <us>` ← from spinbox
   - `BIAS:CURRent:LIMit <a>` ← from spinbox
   - `TRIGger:POLarity LOW` (default; settable from advanced UI)
   - `TRIGger:WATCHdog 5000`
   - `LOG:RATE 10000`
3. **Just before stim Start**:
   - `BIAS:ENABle ON`
   - `LOG:CLEar`
   - `LOG:STARt`
4. **Around each capture**:
   - (PlexStim fires pulse train; STM32 engages bias autonomously
     between pulses based on the TTL trigger)
   - After scope capture completes: `LOG:STOP`
   - `LOG:POINts?` → check fill level
   - `LOG:OVERflow?` → flag if overflowed
   - `LOG:DATA?` → drain buffer, parse, attach to session capture
   - `LOG:CLEar`, `LOG:STARt` → arm for next capture
5. **At stim Stop**:
   - `LOG:STOP`
   - `BIAS:ENABle OFF`
6. **At Disconnect / app exit**:
   - `*RST`
   - Close serial port

If any setter returns `ERR\n`, PULSAR queries `SYSTem:ERRor?`
repeatedly until `0,"No error"` and surfaces every popped error
in the LogPane.

---

## 6. Versioning

| Version | Date | Notes |
|---|---|---|
| 0.1.0 | 2026-05-30 | Initial draft. No firmware exists yet; PULSAR driver is the reference consumer. |

When extending the protocol:
* **Add new commands** without bumping major version (additive).
* **Change semantics of existing commands** → bump minor version
  and add a fallback path in the driver if backwards compat
  matters.
* **Remove commands** → bump major version. Driver refuses to
  open older firmware.

Driver-side version negotiation:
`STM32BiasModule._SUPPORTED_FW = (">=0.1.0", "<1.0.0")`.
