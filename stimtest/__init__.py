"""PULSAR — Python characterization suite for neural-stimulation electrodes.

The Python package on disk is still named ``stimtest`` (its module
path) so the codebase's imports don't need to change. The user-facing
program is **PULSAR** and its companion viewer is **POLARIS**.

The legacy "StimulationTesting" name persists in:

* the GitHub repo URL (``github.com/Bortz1234/StimulationTesting``),
* the on-disk prefs / cache directory
  (``%APPDATA%\\StimulationTesting``) — left as-is so existing
  installations don't lose their calibration / setup data on upgrade,
* the standalone-viewer launcher filename
  (``StimulationTestingViewer.exe``), and
* the Inno Setup installer script.

Renaming any of those is a deployment migration, separate from the
window-title / About-text rename."""

# Single source of truth for the app version. The installer
# (``installer/build.py`` → ``installer/StimulationTesting.iss``)
# reads this string at build time and passes it to Inno Setup as
# ``/DAppVersion=…``, and ``pyproject.toml`` should be kept in
# sync with it. The Help menu's About / Check-for-updates dialogs
# read this value too, so make sure the three locations agree
# whenever you bump it.
__version__ = "0.2.208"
