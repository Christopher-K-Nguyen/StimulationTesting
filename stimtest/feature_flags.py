"""Runtime feature gates for optional / experimental PULSAR modules.

Right now this gates exactly one thing: **INTERSTELLAR**, the
experimental interpulse-bias module (an STM32 board that applies a DC
bias to the return electrode between stimulation pulses).  INTERSTELLAR
is *not* something general users need, so the public installer ships
with it turned OFF — it is only switched ON in an opt-in "experimental"
build (or automatically when you run PULSAR straight from a source
checkout, which is the developer's machine).

Naming convention (same idea as PULSAR / POLARIS, see CLAUDE.md §1 &
gotcha #8): **INTERSTELLAR** is the user-facing brand name for the
interpulse-bias module.  The internal Python code, prefs keys, and the
hardware driver classes keep the plain word ``bias`` — only the label
the operator reads says "INTERSTELLAR".

--------------------------------------------------------------------
How the ON/OFF decision is made (``interstellar_enabled()``)
--------------------------------------------------------------------
Resolution order — the first rule that applies wins:

1. **Explicit override** via the ``PULSAR_ENABLE_INTERSTELLAR``
   environment variable.  ``1`` / ``true`` / ``yes`` / ``on`` force it
   ON; anything else forces it OFF.  This is the escape hatch for
   developers and the automated tests, and it always beats the other
   rules.

2. **Frozen app** (a PyInstaller build, i.e. what end users install):
   ON only when the experimental build bundled the sentinel file
   :data:`INTERSTELLAR_FLAG_FILE` next to the frozen executable.  The
   public installer never bundles that file, so a normal install is
   OFF and shows no trace of INTERSTELLAR.  The experimental installer
   (built with ``build.py --with-interstellar``) does bundle it, so
   that install is ON.

3. **Running from source** (a git checkout — the developer's machine):
   ON by default, so whoever is actively working on the bias module
   sees it without having to set anything.

Keeping every gate in this one tiny module means there is exactly one
place to read when you are wondering "why is / isn't the bias UI
showing up?".
"""
from __future__ import annotations

import os
import sys

#: User-facing brand name for the interpulse-bias module.  Use this
#: constant wherever the module is named in the GUI so the label stays
#: consistent (the internal code keeps the word ``bias``).
INTERSTELLAR_DISPLAY_NAME = "INTERSTELLAR"

#: Filename of the sentinel that the OPT-IN experimental build bundles
#: next to the frozen executable.  Its mere presence flips INTERSTELLAR
#: ON in a frozen app (see rule 2 above).  The public build omits it.
#: Bundled by ``installer/StimulationTesting.spec`` when the build was
#: started with ``build.py --with-interstellar``.
INTERSTELLAR_FLAG_FILE = "interstellar_enabled.flag"

#: Environment variable name for the explicit developer/test override.
INTERSTELLAR_ENV_OVERRIDE = "PULSAR_ENABLE_INTERSTELLAR"

# Strings we accept as "true" for the env override (case-insensitive).
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def interstellar_enabled() -> bool:
    """Return ``True`` when the INTERSTELLAR interpulse-bias module
    should be visible / wired up.

    See the module docstring for the full resolution order.  The result
    is intentionally *not* cached: it is cheap to compute, and reading it
    fresh each time lets a test flip ``PULSAR_ENABLE_INTERSTELLAR`` and
    construct a widget to check either state.
    """
    # Rule 1 — explicit override always wins.
    override = os.environ.get(INTERSTELLAR_ENV_OVERRIDE)
    if override is not None:
        return override.strip().lower() in _TRUTHY

    # Rule 2 — a frozen (installed) app is ON only if the experimental
    # build bundled the sentinel flag file.
    if getattr(sys, "frozen", False):
        bundle_dir = getattr(sys, "_MEIPASS", None) or os.path.dirname(
            sys.executable)
        return os.path.exists(os.path.join(bundle_dir, INTERSTELLAR_FLAG_FILE))

    # Rule 3 — running from a source checkout (developer machine) → ON.
    return True
