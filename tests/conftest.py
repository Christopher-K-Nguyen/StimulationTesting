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


@pytest.fixture(autouse=True, scope="module")
def _reap_module_widgets():
    """Destroy the Qt widgets a test MODULE leaves behind.

    **Why this exists.**  The GUI tests build real widgets and many call
    ``.show()``; almost none destroy them.  Nothing collects them either — a
    ``QWidget`` with no parent stays alive as a top-level window for the whole
    session.  Measured across the suite: top-level widgets climbed past
    **10 900** live objects, with individual modules leaking 700-1500 each.

    That is not merely untidy.  Past a few thousand live ``QGraphicsView``
    backing stores the offscreen platform plugin runs out of graphics
    resources and pyqtgraph's ``GraphicsView.paintEvent`` dies with a Windows
    **access violation** — a hard interpreter crash, not a test failure, so
    pytest reports "no tests collected" (exit 5) and every remaining test is
    lost.  It surfaced as an apparently-unrelated failure in
    ``test_experiment_plot_view.py`` that passed in isolation and passed
    adjacent to the newest files, but crashed once the suite grew enough to
    cross the threshold.  Adding any new test file could re-trigger it.

    **Why the MODULE boundary and not per-test.**  Dozens of modules use
    ``@pytest.fixture(scope="module")`` to build one MainWindow / panel and
    share it across their tests, so a per-test sweep would delete a widget the
    next test still needs.  A module-scoped autouse fixture is set up BEFORE
    the module's own fixtures and therefore torn down AFTER them, so by the
    time this runs the module's shared widgets are already finalised and
    nothing later can reference them.

    Only widgets that did NOT exist when the module started are destroyed;
    strong references to the pre-existing ones are held for the duration so
    their ``id()`` cannot be recycled by a new object and mistaken for old.
    Entirely best-effort — a failure here must never fail a test.
    """
    try:
        from PyQt6 import QtWidgets
    except Exception:
        # No GUI stack in this environment (pure-logic run) — nothing to do.
        yield
        return

    app = QtWidgets.QApplication.instance()
    # Hold real references, not just ids: a dead wrapper's id can be reused.
    pre_existing = list(app.topLevelWidgets()) if app is not None else []
    pre_ids = {id(w) for w in pre_existing}

    yield

    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        if id(w) in pre_ids:
            continue
        try:
            w.close()
            # ``destroy()`` releases the PLATFORM WINDOW + BACKING STORE while
            # leaving the C++/Python object intact.  That is precisely the
            # resource that runs out and kills ``paintEvent`` — and because
            # nothing is deleted, there is no double-free for Qt to abort on.
            w.destroy(True, True)
        except Exception:
            pass                        # already-dead C++ object, etc.

    # ⚠ Do NOT force the deferred deletions here.  Calling
    # ``sendPostedEvents(None, DeferredDelete)`` (or setParent(None) +
    # deleteLater) tears the widget tree down mid-flight and Qt aborts the
    # PROCESS: "Fatal Python error: Aborted" raised from inside this very
    # fixture, which is strictly worse than the leak it was fixing.  close()
    # alone frees the graphics resources; Python/Qt reclaim the objects on
    # their own schedule.
    try:
        app.processEvents()
    except Exception:
        pass

    # matplotlib figures leak the same way (the suite warns "More than 20
    # figures have been opened").  Only touch pyplot if something already
    # imported it — it is deliberately a LAZY import in the app.
    import sys
    plt = sys.modules.get("matplotlib.pyplot")
    if plt is not None:
        try:
            plt.close("all")
        except Exception:
            pass


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
