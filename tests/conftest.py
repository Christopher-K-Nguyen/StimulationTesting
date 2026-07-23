"""Shared pytest fixtures.

**Hermetic hardware-detection.**  The ``ConnectionPanel`` fires two
construction-time *detection probes* (scope VISA enumeration + stimulator
Windows-PnP query) on the next event-loop tick.  In the test suite those
must NOT touch the machine's live VISA / PnP layer:

* it makes the GUI-widget tests depend on whatever hardware happens to be
  on the bench, and
* when NI-VISA is wedged (a stale USB-TMC claim from a crashed session),
  ``pyvisa.ResourceManager().list_resources()`` can block for seconds per
  test — turning a ~2-minute suite into a ~20-minute one — and the
  background probe thread can outlive a short-lived test widget and try to
  emit its result on an already-deleted C++ object.

This autouse fixture no-ops the two detection TRIGGERS so no live probe
runs during tests.  The pure probe LOGIC is still covered directly by
``tests/test_scope_detection_timeout.py`` (which calls the classmethods
with a fake ``pyvisa``), and no test depends on the auto-probe having run
(verified: nothing calls ``_refresh_*_detection_indicator`` or asserts on
the ``*_detect_text`` labels).
"""
import pytest


@pytest.fixture(autouse=True)
def _no_live_hardware_detection(monkeypatch):
    try:
        from stimtest.gui.connection_panel import ConnectionPanel
    except Exception:
        # PyQt6 / the GUI stack isn't importable in this environment
        # (e.g. a pure-logic test run) — nothing to stub.
        return
    monkeypatch.setattr(
        ConnectionPanel, "_refresh_scope_detection_indicator",
        lambda self: None, raising=False)
    monkeypatch.setattr(
        ConnectionPanel, "_refresh_stim_detection_indicator",
        lambda self: None, raising=False)
