"""Tests for the mid-run hardware-disconnect detection layer.

Closes Task #55.  Verifies:

* ``HardwareDisconnectError`` carries the device + original
  exception + where label correctly.
* ``looks_like_disconnect`` classifies common pyvisa / libusb /
  PlexStim / OSError fingerprints correctly; returns None for
  unrelated exceptions (so code bugs aren't mis-flagged as
  disconnects).
* ``reraise_as_disconnect`` context manager: passes through
  HardwareDisconnectError, wraps disconnect-shaped errors,
  passes through code bugs unchanged.
* Round-trip: known fingerprints from real failures we've seen
  on the bench all classify correctly.

We don't unit-test the GUI ``_on_finished`` dialog flow here —
QMessageBox is hard to assert on; the dialog branching is
straightforward enough that a structural source check
suffices.  Integration verification happens at run time the
first time someone yanks the USB.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# HardwareDisconnectError
# ---------------------------------------------------------------------------
def test_disconnect_error_carries_metadata():
    from stimtest.experiments.errors import HardwareDisconnectError

    original = OSError("no such device")
    err = HardwareDisconnectError("scope", original, where="single_capture")
    assert err.device == "scope"
    assert err.original is original
    assert err.where == "single_capture"
    # The repr / str should mention the device + the original error type.
    assert "scope" in str(err)
    assert "OSError" in str(err)
    assert "no such device" in str(err)


def test_disconnect_error_rejects_invalid_device():
    from stimtest.experiments.errors import HardwareDisconnectError

    with pytest.raises(ValueError, match="must be 'scope' or 'stim'"):
        HardwareDisconnectError("camera", RuntimeError())


# ---------------------------------------------------------------------------
# looks_like_disconnect — fingerprint matching
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("exc, expected_device", [
    # pyvisa / libusb (scope)
    (Exception("VI_ERROR_INV_OBJECT during read"),                  "scope"),
    (Exception("VI_ERROR_CONN_LOST: connection lost"),              "scope"),
    (Exception("VI_ERROR_RSRC_NFOUND: Resource not found"),         "scope"),
    (Exception("VI_ERROR_NCIC: Not controller in charge"),          "scope"),
    (Exception("libusb0-dll:err [control_msg] semaphore timeout"),  "scope"),
    (Exception("VisaIOError: timeout on session 12345"),            "scope"),
    # Plexon DLL (stim)
    (Exception("ps_init_all_stim returned -1"),                     "stim"),
    (Exception("PS_InitAllStim failed: No Plexon Stimulator"),      "stim"),
    (Exception("plexstim DLL not initialized"),                     "stim"),
    (Exception("0xC0000374 STATUS_HEAP_CORRUPTION"),                "stim"),
])
def test_looks_like_disconnect_classifies(exc, expected_device):
    """Known fingerprints classify to the right device."""
    from stimtest.experiments.errors import looks_like_disconnect

    assert looks_like_disconnect(exc) == expected_device


@pytest.mark.parametrize("exc", [
    # Code bugs — should NOT be flagged as disconnects.
    NameError("name 'x' is not defined"),
    AttributeError("'NoneType' object has no attribute 'foo'"),
    KeyError("missing_key"),
    TypeError("expected int, got str"),
    ZeroDivisionError("division by zero"),
    # Domain errors that aren't hardware-related.
    ValueError("amplitude must be non-negative"),
    RuntimeError("Run aborted by user"),
])
def test_looks_like_disconnect_ignores_code_bugs(exc):
    """Generic exceptions without disconnect fingerprints return
    None so they hit the normal abort path, not the reconnect dialog."""
    from stimtest.experiments.errors import looks_like_disconnect

    assert looks_like_disconnect(exc) is None


def test_looks_like_disconnect_recognizes_already_classified():
    """If the exception is ALREADY a HardwareDisconnectError, return
    its device directly — lets the helper safely nest in re-raise
    chains without losing the device tag."""
    from stimtest.experiments.errors import (
        HardwareDisconnectError, looks_like_disconnect,
    )

    inner = OSError("VI_ERROR_INV_OBJECT")
    err = HardwareDisconnectError("scope", inner)
    assert looks_like_disconnect(err) == "scope"


def test_looks_like_disconnect_generic_hints_return_none():
    """Pure generic hints (no device-specific tokens) return None —
    caller is expected to know the device context from where the
    error came from."""
    from stimtest.experiments.errors import looks_like_disconnect

    assert looks_like_disconnect(OSError("no such device")) is None
    assert looks_like_disconnect(OSError("broken pipe")) is None


# ---------------------------------------------------------------------------
# reraise_as_disconnect context manager
# ---------------------------------------------------------------------------
def test_reraise_passes_through_non_disconnect_exceptions():
    """A NameError (code bug) should pass through unchanged — we
    don't want to accidentally label every error as a disconnect."""
    from stimtest.experiments.errors import reraise_as_disconnect

    with pytest.raises(NameError):
        with reraise_as_disconnect("scope", "test"):
            raise NameError("name 'undefined_thing' is not defined")


def test_reraise_wraps_disconnect_shaped_visa_error():
    """A VISA-style exception inside a 'scope' context becomes a
    HardwareDisconnectError tagged with device='scope'."""
    from stimtest.experiments.errors import (
        HardwareDisconnectError, reraise_as_disconnect,
    )

    with pytest.raises(HardwareDisconnectError) as exc_info:
        with reraise_as_disconnect("scope", "single_capture"):
            raise RuntimeError("VI_ERROR_INV_OBJECT")
    assert exc_info.value.device == "scope"
    assert exc_info.value.where == "single_capture"
    assert isinstance(exc_info.value.original, RuntimeError)


def test_reraise_wraps_generic_no_such_device_with_call_site_device():
    """A generic 'no such device' error in a 'stim' context gets
    tagged as stim because the call site knew which device."""
    from stimtest.experiments.errors import (
        HardwareDisconnectError, reraise_as_disconnect,
    )

    with pytest.raises(HardwareDisconnectError) as exc_info:
        with reraise_as_disconnect("stim", "load_channel"):
            raise OSError("no such device")
    assert exc_info.value.device == "stim"


def test_reraise_passes_already_classified_unchanged():
    """If the inner code already raised HardwareDisconnectError,
    don't re-wrap — preserves the original device/where labels."""
    from stimtest.experiments.errors import (
        HardwareDisconnectError, reraise_as_disconnect,
    )

    original = HardwareDisconnectError(
        "stim", OSError("inner"), where="inner_op")
    with pytest.raises(HardwareDisconnectError) as exc_info:
        with reraise_as_disconnect("scope", "outer_op"):
            raise original
    # Should still be the original instance (not re-wrapped with
    # device='scope').
    assert exc_info.value is original
    assert exc_info.value.device == "stim"
    assert exc_info.value.where == "inner_op"


def test_reraise_rejects_invalid_device_label():
    from stimtest.experiments.errors import reraise_as_disconnect

    with pytest.raises(ValueError, match="must be 'scope' or 'stim'"):
        with reraise_as_disconnect("camera"):
            pass


# ---------------------------------------------------------------------------
# RunnerWorker integration — error string carries the marker
# ---------------------------------------------------------------------------
def test_runner_worker_marks_disconnect_in_result_error_string():
    """When the runner raises a disconnect-shaped error, the
    worker's ExperimentResult.error should start with the canonical
    ``[DISCONNECT:<device>]`` prefix so _on_finished can branch."""
    # We need to construct a minimal runner that raises a
    # disconnect-shaped exception when .run() is called, then
    # invoke RunnerWorker.run() and inspect the emitted result.
    pytest.importorskip("PyQt6.QtCore")
    from PyQt6 import QtCore
    from stimtest.gui.experiment_tabs import RunnerWorker

    # Need a QCoreApplication for signal emission to work in tests.
    app = QtCore.QCoreApplication.instance()
    if app is None:
        app = QtCore.QCoreApplication([])

    # Stub runner: minimal interface (session + subscribe + run).
    class _StubRunner:
        def __init__(self):
            from stimtest.session import (
                Session, TestParameters, Capture, ChannelRun,
            )
            from stimtest.waveforms import PulsePattern
            from stimtest.electrode import Configuration, ElectrodeArray

            pat = PulsePattern.biphasic(
                amplitude_ua=100.0, phase_width_us=200.0, rate_hz=50.0)
            cfg = Configuration.monopolar(1)
            self.session = Session(
                notebook="", subject="", user_name="", user_email="",
                test=TestParameters(
                    experiment="VT", duration_s=0.0,
                    polarization_method="MP",
                    counter_electrode_label="",
                    reference_electrode_label="",
                    target_charge_phase_nc=0.0,
                    configuration=cfg, pattern=pat,
                    array=ElectrodeArray.utah_4x4()),
                runs=[],
            )
            self._subs = []

        def subscribe(self, cb):
            self._subs.append(cb)

        def request_continue(self):
            pass

        def run(self):
            # Simulate a real disconnect during single_capture:
            # raise the exception the runner would have hit when
            # the scope USB cable was yanked.
            raise RuntimeError("VI_ERROR_INV_OBJECT — connection lost")

    runner = _StubRunner()
    worker = RunnerWorker(runner, save_path=None)
    captured_results = []
    captured_logs = []
    worker.finished.connect(captured_results.append)
    worker.log_msg.connect(captured_logs.append)
    worker.run()

    # Drive queued connections.
    app.processEvents()

    assert len(captured_results) == 1
    result = captured_results[0]
    assert result.aborted is True
    assert result.error.startswith("[DISCONNECT:scope]"), (
        f"expected error to start with [DISCONNECT:scope] marker; "
        f"got: {result.error!r}")
    # The log should also flag the disconnect prominently.
    assert any("HARDWARE DISCONNECT" in msg for msg in captured_logs), (
        f"expected a HARDWARE DISCONNECT log line; got: {captured_logs!r}")


def test_runner_worker_non_disconnect_error_uses_legacy_path():
    """A code-bug exception (NameError) shouldn't get the disconnect
    marker — the original 'Run aborted (NameError)' log message
    + plain error string should remain."""
    pytest.importorskip("PyQt6.QtCore")
    from PyQt6 import QtCore
    from stimtest.gui.experiment_tabs import RunnerWorker

    app = QtCore.QCoreApplication.instance()
    if app is None:
        app = QtCore.QCoreApplication([])

    class _StubRunner:
        def __init__(self):
            from stimtest.session import Session, TestParameters
            from stimtest.waveforms import PulsePattern
            from stimtest.electrode import Configuration, ElectrodeArray
            pat = PulsePattern.biphasic(amplitude_ua=100.0)
            cfg = Configuration.monopolar(1)
            self.session = Session(
                notebook="", subject="", user_name="", user_email="",
                test=TestParameters(
                    experiment="VT", duration_s=0.0,
                    polarization_method="MP",
                    counter_electrode_label="",
                    reference_electrode_label="",
                    target_charge_phase_nc=0.0,
                    configuration=cfg, pattern=pat,
                    array=ElectrodeArray.utah_4x4()),
                runs=[],
            )

        def subscribe(self, cb): pass
        def request_continue(self): pass
        def run(self):
            raise NameError("name 'undefined_thing' is not defined")

    runner = _StubRunner()
    worker = RunnerWorker(runner, save_path=None)
    captured = []
    worker.finished.connect(captured.append)
    worker.run()
    app.processEvents()

    assert len(captured) == 1
    assert captured[0].aborted is True
    # No DISCONNECT marker — code bug took the legacy abort path.
    assert not captured[0].error.startswith("[DISCONNECT:")
    assert "NameError" in captured[0].error
