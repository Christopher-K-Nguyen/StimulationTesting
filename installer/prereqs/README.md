# Bundled prerequisite installers (optional)

Drop the vendor driver installers here **before** running
`installer/build.py` and the PULSAR installer's "Required hardware
drivers" wizard page will **auto-run them** (instead of downloading
Stim-2 / opening NI's website):

| Drop a file named… | …to bundle the installer for |
|---|---|
| `stim2-setup.exe`  | Plexon **Stim-2** (PlexStim 2.0 / Stimulator V2) |
| `nivisa-setup.exe` | **NI-VISA** (e.g. the NI-VISA *online* installer) |

Rename your vendor download to the exact name above.

These are referenced in `StimulationTesting.iss` `[Files]` with
`Flags: dontcopy skipifsourcedoesntexist`, so:

- **Present** → compiled into setup; the wizard extracts to `{tmp}` and
  runs it on click (full offline auto-run; this is the only way to make
  **NI-VISA** auto-run, since NI gates its web downloads behind a login).
- **Absent** → the build still succeeds; the wizard falls back to
  downloading `StimulatorV2Setup.exe` (Stim-2) / opening NI's download
  page (NI-VISA).

This folder is intentionally kept empty in git (the `.exe`s are large
vendor binaries that shouldn't be committed) — see the repo `.gitignore`.
