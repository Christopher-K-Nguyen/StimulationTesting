"""Shared camera service + UI widgets for PULSAR.

Three pieces:

* :class:`CameraService` — singleton-style QObject that owns the
  ``QCamera`` lifecycle (one device at a time), exposes the live
  video sink as a Qt signal, and provides snapshot / recording
  APIs.  Multiple UI consumers (the connection panel, one per
  experiment tab's stream pane, the runner's periodic-snapshot
  worker) all use the SAME service instance via
  :func:`camera_service`.

* :class:`CameraConnector` — combo box + Connect / Disconnect button,
  lives in :class:`ConnectionPanel` under the oscilloscope section
  so the camera takes its place alongside the other bench
  instruments the operator wires up at session start.

* :class:`CameraStreamPane` — lightweight preview that subscribes to
  :attr:`CameraService.frameReady`, paints the current
  ``QVideoFrame`` into a ``QLabel`` via ``QVideoFrame.toImage()``.
  Embeddable in any layout — each experiment tab carries one beneath
  its multichannel scope plot so the operator sees the bench
  alongside the captured waveforms.

Files saved by the camera land in the project tree per the
``feedback_error_logs_in_project_tree`` memory convention
(``<repo>/test/camera_<YYYYMMDD-HHMMSS>.jpg`` / ``.mp4``) — NEVER
``%APPDATA%`` or ``%TEMP%``.

QtMultimedia imports are deferred to :meth:`CameraService._ensure_qtmm`
so the heavy multimedia backends don't hit the cold-launch path for
sessions that never engage the camera.
"""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import List, Optional

from PyQt6 import QtCore, QtGui, QtWidgets


# ===========================================================================
# Output-path helpers (project-tree convention; never APPDATA / TEMP).
# ===========================================================================
def _project_test_dir() -> Path:
    """Resolve ``<repo>/test/`` for snapshot / recording output.

    Same convention as :func:`run_gui._crash_log_path`: prefer
    ``<repo>/test/``; fall back to the project root.  NEVER
    ``%APPDATA%`` or ``%TEMP%`` per the user instruction captured in
    the ``feedback_error_logs_in_project_tree`` memory.
    """
    here = Path(__file__).resolve()
    # <repo>/stimtest/gui/camera.py → parents[2] = <repo>
    try:
        repo_root = here.parents[2]
        test_dir = repo_root / "test"
        if test_dir.is_dir():
            return test_dir
        return repo_root
    except Exception:
        return Path.home()


def _timestamped_path(stem: str, ext: str) -> Path:
    """``<repo>/test/<stem>_<YYYYMMDD-HHMMSS>.<ext>``."""
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    return _project_test_dir() / f"{stem}_{ts}.{ext}"


# ===========================================================================
# CameraService — singleton lifecycle owner
# ===========================================================================
class CameraService(QtCore.QObject):
    """Owns the QCamera lifecycle for the whole PULSAR session.

    Exactly ONE instance exists per process (enforced by
    :func:`camera_service`).  Multiple UI consumers connect to its
    signals; only one camera is open at a time.

    Architectural rationale: Qt's ``QMediaCaptureSession`` only
    accepts ONE video output (``setVideoOutput``) and one
    ``QImageCapture`` / ``QMediaRecorder`` per session.  To show the
    same live feed in BOTH the connection panel preview AND the
    experiment-tab stream pane (AND potentially the View dock if the
    user opens it), we wire a single ``QVideoSink`` and forward each
    received frame via the :attr:`frameReady` signal.  Every
    ``CameraStreamPane`` subscribes independently and paints its
    own copy of the frame.
    """

    #: Emitted with the device description string when ``connect_to``
    #: succeeds.  ConnectionPanel listens to flip its dot green and
    #: experiment tabs listen to show their embedded stream pane.
    connected = QtCore.pyqtSignal(str)

    #: Emitted when the camera is closed (explicit ``disconnect()``,
    #: device unplugged, application shutdown).  Consumers hide UI
    #: that's only meaningful when the feed is live.
    disconnected = QtCore.pyqtSignal()

    #: Emitted for every received video frame (typically 30 Hz for
    #: webcams).  Carries a ``QVideoFrame``; subscribers convert to
    #: ``QImage`` via ``frame.toImage()`` and paint into their own
    #: widget.  Multiple subscribers are supported — the service
    #: holds the only ``QVideoSink`` and re-broadcasts.
    frameReady = QtCore.pyqtSignal(object)   # QVideoFrame

    #: One-line status changes — surfaced in the LogPane and the
    #: status line of every camera widget.
    statusChanged = QtCore.pyqtSignal(str)

    #: Emitted with the saved filesystem path when a snapshot lands
    #: or a recording finishes.
    captureSaved = QtCore.pyqtSignal(str)

    #: Emitted with the current recording state whenever it
    #: transitions.  Live-preview UI elements use this to flip
    #: between a calm "LIVE PREVIEW" badge and a prominent "🔴
    #: RECORDING" badge, so the operator never confuses "I'm
    #: watching the bench" with "I'm saving every frame to disk".
    recordingChanged = QtCore.pyqtSignal(bool)

    #: Forwarded from the underlying ``QMediaDevices.videoInputsChanged``
    #: signal — fires whenever the OS reports a USB camera plug or
    #: unplug.  ``CameraConnector`` subscribes here to auto-rebuild
    #: its dropdown without the operator having to click Refresh.
    devicesChanged = QtCore.pyqtSignal()

    def __init__(self, parent: Optional[QtCore.QObject] = None):
        super().__init__(parent)
        # ---- QtMultimedia handles (created lazily on first connect) ----
        self._qtmm_imported: bool = False
        self._camera = None          # QCamera
        self._session = None         # QMediaCaptureSession
        self._video_sink = None      # QVideoSink (frame fanout)
        self._image_capture = None   # QImageCapture
        self._recorder = None        # QMediaRecorder
        # ``QMediaDevices`` INSTANCE — needed to receive the
        # ``videoInputsChanged`` signal when a USB camera is plugged
        # in or unplugged.  Accessing the signal via the class
        # (``QMediaDevices.videoInputsChanged``) returns a
        # ``pyqtSignal`` descriptor that lacks ``.connect()``, so
        # connections fail silently with ``AttributeError`` (the user
        # symptom: after plugging in a camera, the combobox never
        # auto-refreshed — clicking Refresh manually was required
        # even when the OS reported the camera immediately).  An
        # instance gives us a ``pyqtBoundSignal`` we can subscribe
        # to.  Instantiated lazily in ``_ensure_qtmm`` because
        # building it requires QtMultimedia to be importable.
        self._qmedia_devices = None
        #: One-shot guard so ``devicesChanged`` is wired exactly once
        #: across the service lifetime.
        self._devices_signal_connected: bool = False
        #: Forwarded by the service whenever the OS reports a change
        #: in available cameras (cable plug/unplug).  Subscribers
        #: re-enumerate via :meth:`enumerate_devices`.
        # See definition below.
        # Remember which device the user picked so prefs can round-trip.
        self._connected_device_id: Optional[bytes] = None
        self._connected_description: str = ""
        # Recording state — separate from QMediaRecorder.recorderState()
        # because Qt's state transitions are asynchronous and we want
        # an immediate yes/no for UI / runner queries.
        self._is_recording: bool = False

    # ----- public API ------------------------------------------------
    def is_connected(self) -> bool:
        return self._camera is not None

    def is_recording(self) -> bool:
        return self._is_recording

    def connected_description(self) -> str:
        """Human-readable name of the currently-open camera, or empty
        when disconnected.  Used by status lines and log mirrors."""
        return self._connected_description

    def connected_device_id(self) -> Optional[bytes]:
        """Bytes id of the currently-open camera (for prefs save) or
        ``None`` when disconnected."""
        return self._connected_device_id

    def enumerate_devices(self) -> List[tuple]:
        """List the cameras the OS reports right now.

        Returns ``[(description, device_id_bytes, is_default), …]``.
        Returns ``[]`` if QtMultimedia isn't importable on this
        Python (e.g. a stripped headless install) — callers handle
        the empty list as "no cameras detected".
        """
        if not self._ensure_qtmm():
            return []
        from PyQt6.QtMultimedia import QMediaDevices
        out = []
        for dev in QMediaDevices.videoInputs():
            desc = dev.description() or "(unnamed camera)"
            out.append((desc, bytes(dev.id()), dev.isDefault()))
        return out

    def connect_to(self, device_id: bytes) -> bool:
        """Open the camera with the given byte-id and start streaming.

        Idempotent on the same id (returns True without rewiring); a
        request for a different id swaps cleanly (disconnect → open).
        Returns True on success, False on any failure (status emitted
        via :attr:`statusChanged`).
        """
        if not self._ensure_qtmm():
            return False
        from PyQt6.QtMultimedia import (
            QCamera, QImageCapture, QMediaCaptureSession,
            QMediaDevices, QMediaRecorder, QVideoSink,
        )
        # Already on the right device?  Idempotent.
        if (self._camera is not None
                and self._connected_device_id == device_id):
            return True
        # Switching devices: tear the old one down cleanly first.
        if self._camera is not None:
            self.disconnect()
        # Find the device object by id.
        dev_obj = None
        for dev in QMediaDevices.videoInputs():
            if bytes(dev.id()) == device_id:
                dev_obj = dev
                break
        if dev_obj is None:
            self._emit_status(
                "Selected camera no longer present — was it unplugged?  "
                "Refresh and try again.")
            return False
        try:
            cam = QCamera(dev_obj)
            session = QMediaCaptureSession()
            session.setCamera(cam)
            # Use a QVideoSink (not QVideoWidget) so we can fan out
            # the live frames to multiple subscribers (connection-
            # panel preview + per-experiment stream pane).  Each
            # subscriber paints its own copy on receipt of the
            # frameReady signal below.
            sink = QVideoSink()
            sink.videoFrameChanged.connect(self._on_frame)
            session.setVideoSink(sink)
            ic = QImageCapture()
            session.setImageCapture(ic)
            ic.imageSaved.connect(self._on_image_saved)
            ic.errorOccurred.connect(self._on_image_capture_error)
            mr = QMediaRecorder()
            session.setRecorder(mr)
            # DO NOT connect to ``QMediaRecorder.recorderStateChanged``
            # OR ``QMediaRecorder.errorOccurred`` — both raise PyQt6
            # binding TypeErrors that prevent the camera from
            # connecting at all.
            #
            #   * ``recorderStateChanged(RecorderState)`` — Qt's C++
            #     signal is namespaced as ``QMediaRecorder::
            #     RecorderState`` but PyQt6's bridge sends plain
            #     ``RecorderState``.  The failed bridge leaves a
            #     dangling slot pointer; the SECOND state transition
            #     corrupts the heap (STATUS_HEAP_CORRUPTION,
            #     0xC0000374).
            #   * ``errorOccurred(QMediaRecorder::Error, QString)``
            #     — fails with ``TypeError: connect() failed
            #     between (QMediaRecorder::Error,QString) and
            #     unislot()`` AT CONNECT TIME, aborting
            #     ``connect_to`` and leaving the user unable to open
            #     the camera at all.  Symptom in the operator's log:
            #     repeated "Camera connect FAILED: TypeError: …"
            #     entries on every Connect press.
            #
            # ``QCamera.errorOccurred`` and ``QImageCapture.errorOccurred``
            # both connect cleanly (their enums bridge correctly),
            # so we keep those.  For the recorder we use the no-arg
            # ``errorChanged`` signal instead and query ``error()`` +
            # ``errorString()`` from inside the slot — same diagnostic
            # info, no broken bridge.
            mr.errorChanged.connect(self._on_recorder_error_changed)
            cam.errorOccurred.connect(self._on_camera_error)
            cam.start()
            # Stash handles AFTER start so a start-failure leaves
            # ``self._camera`` None and ``is_connected()`` honest.
            self._camera = cam
            self._session = session
            self._video_sink = sink
            self._image_capture = ic
            self._recorder = mr
            self._connected_device_id = bytes(device_id)
            self._connected_description = dev_obj.description() or "(unnamed)"
            self._emit_status(f"Connected: {self._connected_description}")
            self.connected.emit(self._connected_description)
            return True
        except Exception as e:
            self._emit_status(
                f"Camera connect FAILED: {type(e).__name__}: {e}")
            # Best-effort cleanup in case partial wiring landed.
            self._safe_teardown()
            return False

    def disconnect(self) -> None:
        """Close the active camera and release the OS device handle.

        Safe to call when nothing is connected.  Stops any active
        recording first.  Emits :attr:`disconnected` so UI hides.
        """
        if self._is_recording and self._recorder is not None:
            try:
                self._recorder.stop()
            except Exception:
                pass
        was_recording = self._is_recording
        self._is_recording = False
        if was_recording:
            try:
                self.recordingChanged.emit(False)
            except Exception:
                pass
        if self._camera is not None:
            try:
                self._camera.stop()
            except Exception:
                pass
        self._safe_teardown()
        self._connected_device_id = None
        self._connected_description = ""
        self._emit_status("Camera disconnected.")
        self.disconnected.emit()

    def take_snapshot(self) -> Optional[Path]:
        """Save a single JPEG to ``<repo>/test/``.  Returns the
        target path, or ``None`` on failure / not-connected."""
        if self._image_capture is None or self._camera is None:
            self._emit_status("Snapshot ignored — camera not connected.")
            return None
        try:
            out = _timestamped_path("camera", "jpg")
            self._image_capture.captureToFile(str(out))
            return out
        except Exception as e:
            self._emit_status(
                f"Snapshot FAILED: {type(e).__name__}: {e}")
            return None

    def start_recording(self) -> Optional[Path]:
        """Begin an MP4 recording to ``<repo>/test/``.  Returns the
        target path, or ``None`` if not connected / already recording."""
        if self._is_recording:
            return None
        if self._recorder is None or self._camera is None:
            self._emit_status("Recording ignored — camera not connected.")
            return None
        try:
            out = _timestamped_path("camera", "mp4")
            self._recorder.setOutputLocation(
                QtCore.QUrl.fromLocalFile(str(out)))
            self._recorder.record()
            self._is_recording = True
            self._emit_status(f"Recording → {out}")
            try:
                self.recordingChanged.emit(True)
            except Exception:
                pass
            return out
        except Exception as e:
            self._emit_status(
                f"Recording start FAILED: {type(e).__name__}: {e}")
            return None

    def stop_recording(self) -> None:
        """Stop the active recording.  No-op if not recording.

        Emits :attr:`captureSaved` with the final file path AFTER the
        recorder reports it can stop.  We don't use the async
        ``recorderStateChanged`` signal for this (see the comment in
        :meth:`connect_to` for the PyQt6 binding bug + crash that
        relying on it triggered) — instead we read
        ``recorder.actualLocation()`` synchronously right after
        ``stop()`` returns.
        """
        if not self._is_recording or self._recorder is None:
            return
        out_path = None
        try:
            self._recorder.stop()
            # ``stop()`` is synchronous in Qt 6 — by the time it
            # returns the file is finalised and ``actualLocation()``
            # has the path we should report.  (If a future Qt version
            # makes it async, the worst case is we report the
            # EXPECTED path slightly before the file is closed — still
            # useful for the operator's log.)
            try:
                url = self._recorder.actualLocation()
                if url.isValid() and url.isLocalFile():
                    out_path = url.toLocalFile()
            except Exception:
                pass
        except Exception as e:
            self._emit_status(
                f"Recording stop FAILED: {type(e).__name__}: {e}")
        finally:
            self._is_recording = False
            try:
                self.recordingChanged.emit(False)
            except Exception:
                pass
            if out_path:
                self._emit_status(f"Recording saved → {out_path}")
                try:
                    self.captureSaved.emit(out_path)
                except Exception:
                    pass

    # ----- internals -------------------------------------------------
    def _emit_status(self, msg: str) -> None:
        try:
            self.statusChanged.emit(msg)
        except Exception:
            pass

    def _ensure_qtmm(self) -> bool:
        if self._qtmm_imported:
            return True
        # ESCAPE HATCH — ``PULSAR_DISABLE_CAMERA=1`` skips ALL camera /
        # QtMultimedia access.  A camera or its driver in a bad state can make
        # the FFmpeg backend's ``QMediaDevices.videoInputs()`` HANG the GUI
        # thread at startup (blank window / "Not Responding" right after the
        # ``qt.multimedia.ffmpeg`` line).  Every camera entry point
        # (:meth:`enumerate_devices`, :meth:`connect_to`) gates on
        # ``_ensure_qtmm``, so returning False here short-circuits them all
        # BEFORE any ``videoInputs()`` call — the operator can launch and run
        # experiments without the camera.  Set the env var, relaunch; unset it
        # (and fix / unplug the camera) to restore the camera feature.
        import os as _os
        if _os.environ.get("PULSAR_DISABLE_CAMERA"):
            self._emit_status("Camera disabled (PULSAR_DISABLE_CAMERA set).")
            return False
        try:
            from PyQt6 import QtMultimedia  # noqa: F401
            from PyQt6 import QtMultimediaWidgets  # noqa: F401
            self._qtmm_imported = True
        except Exception as e:
            self._emit_status(
                f"QtMultimedia import failed: "
                f"{type(e).__name__}: {e}  "
                f"(camera feature requires PyQt6.QtMultimedia)")
            return False
        # Build the QMediaDevices INSTANCE and wire its
        # ``videoInputsChanged`` signal exactly once.  Class-level
        # ``QMediaDevices.videoInputsChanged`` is a ``pyqtSignal``
        # descriptor with no ``.connect()`` method — connecting via
        # the class raises ``AttributeError``, so the hot-plug auto-
        # refresh never fired on the previous version.  An instance
        # gives us a ``pyqtBoundSignal`` that subscribes correctly.
        if not self._devices_signal_connected:
            try:
                from PyQt6.QtMultimedia import QMediaDevices
                # Parent the QMediaDevices to the service so it
                # stays alive as long as the service does.
                self._qmedia_devices = QMediaDevices(self)
                self._qmedia_devices.videoInputsChanged.connect(
                    self._on_videoInputsChanged)
                self._devices_signal_connected = True
            except Exception:
                pass
        return True

    def _on_videoInputsChanged(self) -> None:
        """Re-emit the underlying device-change signal as our own
        :attr:`devicesChanged`.  UI consumers subscribe to
        ``devicesChanged`` (a normal `pyqtSignal` on a real `QObject`)
        rather than poking at the static QMediaDevices signal which
        doesn't bind in PyQt6.
        """
        try:
            self.devicesChanged.emit()
        except Exception:
            pass

    def _safe_teardown(self) -> None:
        """Drop all QtMultimedia handles.  Each in its own try so a
        partial-wiring error during connect_to() still leaves the
        service in a sane all-None state."""
        for attr in ("_camera", "_session", "_video_sink",
                     "_image_capture", "_recorder"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.deleteLater()
                except Exception:
                    pass
            setattr(self, attr, None)

    # ---- QtMultimedia signal handlers ----------------------------
    def _on_frame(self, frame) -> None:
        # Re-broadcast to every CameraStreamPane subscriber.  Cheap —
        # Qt signals are direct calls when emitter and slot live on
        # the same thread, which is true here (QVideoSink fires on
        # the GUI thread).
        try:
            self.frameReady.emit(frame)
        except Exception:
            pass

    def _on_image_saved(self, _id: int, file_name: str) -> None:
        self._emit_status(f"Snapshot saved → {file_name}")
        try:
            self.captureSaved.emit(file_name)
        except Exception:
            pass

    def _on_image_capture_error(self, _id: int, _err, msg: str) -> None:
        self._emit_status(f"Snapshot ERROR: {msg}")

    # NOTE: ``_on_recorder_state_changed`` removed deliberately — see
    # the comment in ``connect_to`` for the PyQt6 binding bug that
    # made connecting to ``recorderStateChanged`` corrupt the heap.
    # ``stop_recording`` now reports the saved path synchronously.

    def _on_recorder_error_changed(self) -> None:
        """No-arg slot for ``QMediaRecorder.errorChanged``.

        ``errorOccurred(Error, QString)`` bridges incorrectly in
        PyQt6 (see :meth:`connect_to` for the full diagnosis), so we
        use the no-arg ``errorChanged`` signal and pull the details
        out of the recorder via ``error()`` / ``errorString()``.
        Suppress the ``NoError`` transition so the operator's log
        doesn't fill with spurious "ERROR: NoError" entries during
        normal start/stop cycles (the recorder clears its error
        state to ``NoError`` after a clean stop).
        """
        try:
            from PyQt6.QtMultimedia import QMediaRecorder
            if self._recorder is None:
                return
            err = self._recorder.error()
            if err == QMediaRecorder.Error.NoError:
                return
            msg = self._recorder.errorString() or str(err)
            self._emit_status(f"Recorder ERROR: {msg}")
        except Exception:
            # Defensive: a slot called during teardown might find
            # the recorder already deleted.  Swallow.
            pass

    def _on_camera_error(self, _err, msg: str) -> None:
        self._emit_status(f"Camera ERROR: {msg}")


# ---------------------------------------------------------------------------
# Process-wide singleton accessor.
# ---------------------------------------------------------------------------
_SERVICE_INSTANCE: Optional[CameraService] = None


def camera_service() -> CameraService:
    """Return the process-wide :class:`CameraService` singleton.

    Lazy-initialized on first call so importing this module doesn't
    drag in QtMultimedia until something actually asks for the
    service.  Every UI consumer (ConnectionPanel's CameraConnector,
    every ExperimentTab's CameraStreamPane, the runner's snapshot
    timer) calls this to get the SAME instance — only one camera
    pipeline exists per process.

    **Parent**: when a QApplication exists at first-call time, the
    service is parented to it so the C++ QObject lives as long as
    the Qt event loop.  Without this the QObject can be reaped by
    Qt's GC between test modules (the Python global stays bound,
    but the C++ object becomes invalid — accessing it raises
    ``RuntimeError: wrapped C/C++ object of type CameraService has
    been deleted``).  We also defensively re-instantiate if the
    cached object's underlying C++ side has been deleted, to keep
    test reuse robust.
    """
    global _SERVICE_INSTANCE
    # Defensive guard: detect a stale wrapper whose C++ object got
    # reaped (happens in pytest when a previous test's QApplication
    # event loop tore down the parent).  Treat as if uninitialised.
    if _SERVICE_INSTANCE is not None:
        try:
            # Cheap property read on the QObject — raises
            # RuntimeError if the underlying C++ object has been
            # deleted, returns harmlessly otherwise.
            _ = _SERVICE_INSTANCE.objectName()
        except RuntimeError:
            _SERVICE_INSTANCE = None
    if _SERVICE_INSTANCE is None:
        # Parent to the QApplication when one exists, so the C++
        # QObject lives as long as the Qt event loop.  Falls back
        # to parent=None when called before QApplication construction
        # (e.g. during module-import probes).
        from PyQt6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        _SERVICE_INSTANCE = CameraService(parent=app)
    return _SERVICE_INSTANCE


# ===========================================================================
# CameraConnector — UI for the ConnectionPanel
# ===========================================================================
class CameraConnector(QtWidgets.QWidget):
    """Device picker + Connect / Disconnect for :class:`ConnectionPanel`.

    Lives under the oscilloscope section.  Wires every action through
    the singleton :func:`camera_service` so the actual camera I/O is
    shared with the experiment-tab preview panes.
    """

    #: Re-emitted from the service for any owner that wants to mirror
    #: status into its own log pane.  ConnectionPanel forwards this
    #: to MainWindow.log_pane.
    log = QtCore.pyqtSignal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._svc = camera_service()

        # ----- detection dot + status text (matches scope's pattern) --
        from .connection_panel import _DOT_OFF, _DOT_OK
        self._dot_off = _DOT_OFF
        self._dot_on = _DOT_OK
        self.detect_dot = self._make_dot(_DOT_OFF)
        self.detect_dot.setToolTip(
            "Camera detection: gray = not yet checked / none detected, "
            "green = at least one camera enumerated.")
        self.status_label = QtWidgets.QLabel("Camera detection pending…")
        self.status_label.setStyleSheet("color: #555;")
        self.connected_dot = self._make_dot(_DOT_OFF)
        self.connected_dot.setToolTip(
            "Camera session: gray = not connected, green = streaming.")

        # ----- device combobox + Connect/Disconnect ------------------
        self.device_combo = QtWidgets.QComboBox()
        self.device_combo.setToolTip(
            "Pick a connected camera.  Click Refresh after plugging "
            "in / unplugging a USB camera.")
        self.refresh_btn = QtWidgets.QToolButton()
        self.refresh_btn.setText("Refresh")
        self.refresh_btn.setToolTip("Re-enumerate available cameras.")
        self.refresh_btn.clicked.connect(self.refresh_devices)
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.clicked.connect(self._do_connect)
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        self.disconnect_btn.clicked.connect(self._do_disconnect)
        self.disconnect_btn.setEnabled(False)

        # ----- layout ------------------------------------------------
        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)

        row1 = QtWidgets.QHBoxLayout()
        row1.addWidget(self.detect_dot)
        row1.addWidget(self.status_label, stretch=1)
        row1.addWidget(QtWidgets.QLabel("Streaming"))
        row1.addWidget(self.connected_dot)
        v.addLayout(row1)

        row2 = QtWidgets.QHBoxLayout()
        row2.addWidget(QtWidgets.QLabel("Camera:"))
        row2.addWidget(self.device_combo, stretch=1)
        row2.addWidget(self.refresh_btn)
        row2.addWidget(self.connect_btn)
        row2.addWidget(self.disconnect_btn)
        v.addLayout(row2)

        # ----- service signals ---------------------------------------
        self._svc.connected.connect(self._on_service_connected)
        self._svc.disconnected.connect(self._on_service_disconnected)
        self._svc.statusChanged.connect(self._on_service_status)
        self._svc.captureSaved.connect(
            lambda path: self.log.emit(f"[camera] saved → {path}"))
        # Hot-plug auto-refresh: the service forwards
        # ``QMediaDevices.videoInputsChanged`` via its own
        # ``devicesChanged`` signal (because the class-level
        # ``QMediaDevices.videoInputsChanged`` is a ``pyqtSignal``
        # descriptor with no ``.connect()`` method — see the
        # ``_ensure_qtmm`` comment on why instance vs class
        # matters).  Subscribing to the service signal means a USB
        # camera plugged in mid-session auto-populates the combobox
        # without the operator clicking Refresh.
        self._svc.devicesChanged.connect(self.refresh_devices)

        # ----- initial enumeration -----------------------------------
        # Stage three refresh attempts so a slow USB enumeration
        # doesn't leave the combobox stuck at "No cameras detected"
        # when a camera IS plugged in:
        #
        #   * 0 ms   — immediate after layout (works in most cases)
        #   * 250 ms — covers FFmpeg's USB-backend init latency
        #   * 1500 ms — covers slow USB enumeration on first connect
        #               after a fresh QApplication
        #
        # All three call ``refresh_devices`` which is idempotent
        # (preserves prior selection, just rebuilds the combobox
        # entries).  The operator symptom that motivated this:
        # camera was plugged in BEFORE PULSAR launched, but the
        # combobox showed "No cameras detected" until the user
        # clicked Refresh.  Multiple retries beat a single Refresh
        # button click as the "fix".
        for delay_ms in (0, 250, 1500):
            QtCore.QTimer.singleShot(delay_ms, self.refresh_devices)

    # ----- public API ----------------------------------------------
    def refresh_devices(self) -> None:
        """Re-enumerate cameras and rebuild the combobox.  Preserves
        the previous selection when possible."""
        cur_id = self.device_combo.currentData()
        if cur_id is not None:
            cur_id = bytes(cur_id)
        cams = self._svc.enumerate_devices()
        self.device_combo.blockSignals(True)
        try:
            self.device_combo.clear()
            self.device_combo.addItem("— No camera —", userData=None)
            for (desc, dev_id, is_default) in cams:
                label = f"{desc} *" if is_default else desc
                self.device_combo.addItem(label, userData=dev_id)
            # Restore previous selection if still present.
            if cur_id is not None:
                for i in range(self.device_combo.count()):
                    if self.device_combo.itemData(i) == cur_id:
                        self.device_combo.setCurrentIndex(i)
                        break
        finally:
            self.device_combo.blockSignals(False)
        n = len(cams)
        self._set_dot(self.detect_dot,
                     self._dot_on if n > 0 else self._dot_off)
        self.detect_dot.setToolTip(
            f"Camera detection: {n} camera{'s' if n != 1 else ''} "
            f"enumerated.")
        if n == 0:
            self.status_label.setText("No cameras detected.")
        else:
            self.status_label.setText(
                f"{n} camera{'s' if n != 1 else ''} detected.")
        self._refresh_button_states()

    # ----- prefs round-trip ---------------------------------------
    def current_prefs(self) -> dict:
        """Snapshot the selected-but-not-necessarily-connected device
        id so re-launching restores the dropdown choice."""
        cur = self.device_combo.currentData()
        if cur is None:
            return {}
        try:
            return {"selected_device_id_hex": bytes(cur).hex()}
        except Exception:
            return {}

    def restore_prefs(self, p: dict) -> None:
        if not isinstance(p, dict):
            return
        hex_id = p.get("selected_device_id_hex")
        if not isinstance(hex_id, str):
            return
        try:
            dev_id = bytes.fromhex(hex_id)
        except ValueError:
            return
        for i in range(self.device_combo.count()):
            if self.device_combo.itemData(i) == dev_id:
                self.device_combo.setCurrentIndex(i)
                return

    # ----- internals ----------------------------------------------
    def _make_dot(self, color: str) -> QtWidgets.QLabel:
        dot = QtWidgets.QLabel()
        dot.setFixedSize(14, 14)
        dot.setStyleSheet(
            f"background:{color}; border-radius: 7px; border: 1px solid #555;")
        return dot

    def _set_dot(self, dot: QtWidgets.QLabel, color: str) -> None:
        dot.setStyleSheet(
            f"background:{color}; border-radius: 7px; border: 1px solid #555;")

    def _refresh_button_states(self) -> None:
        connected = self._svc.is_connected()
        cur_id = self.device_combo.currentData()
        has_device = cur_id is not None
        self.connect_btn.setEnabled(has_device and not connected)
        self.disconnect_btn.setEnabled(connected)
        self.device_combo.setEnabled(not connected)
        self.refresh_btn.setEnabled(not connected)
        self._set_dot(self.connected_dot,
                      self._dot_on if connected else self._dot_off)

    def _do_connect(self) -> None:
        cur_id = self.device_combo.currentData()
        if cur_id is None:
            return
        self._svc.connect_to(bytes(cur_id))

    def _do_disconnect(self) -> None:
        self._svc.disconnect()

    # ---- service signal handlers --------------------------------
    def _on_service_connected(self, desc: str) -> None:
        self._refresh_button_states()
        self.log.emit(f"[camera] connected: {desc}")

    def _on_service_disconnected(self) -> None:
        self._refresh_button_states()
        self.log.emit("[camera] disconnected")

    def _on_service_status(self, msg: str) -> None:
        # Mirror to the connection-panel log.  Strip the duplicate
        # log lines for connect/disconnect (those have dedicated
        # handlers above).
        if msg.startswith("Connected:") or msg.startswith("Camera disconnected"):
            return
        self.log.emit(f"[camera] {msg}")


# ===========================================================================
# CameraStreamPane — embedded preview for experiment tabs
# ===========================================================================
class CameraStreamPane(QtWidgets.QWidget):
    """Lightweight live preview.

    Subscribes to :attr:`CameraService.frameReady`; each frame is
    converted to ``QImage`` and painted into the contained ``QLabel``.
    The pane is HIDDEN by default and auto-shows when the service
    connects; auto-hides on disconnect.  Owners that want it always
    visible can call :meth:`set_always_visible` (e.g. a future
    dockable monitor variant).

    Multi-instance safe: each pane subscribes to ``frameReady``
    independently and paints its own copy.  Qt signals are direct
    method calls when emitter + slot share a thread, so the cost is
    one extra ``frame.toImage()`` + ``QPixmap.fromImage`` per pane
    per frame (~1-2 ms at 1280×720 on modern hardware).
    """

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None,
                 *, allow_snapshot_button: bool = True):
        super().__init__(parent)
        self._svc = camera_service()
        # ---- preview label ------------------------------------------
        self._video_label = QtWidgets.QLabel()
        self._video_label.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter)
        self._video_label.setMinimumHeight(120)
        self._video_label.setStyleSheet("background: #000; color: #888;")
        self._video_label.setText("Camera not connected.")
        self._video_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Expanding)
        self._last_pixmap: Optional[QtGui.QPixmap] = None

        # ---- LIVE / RECORDING status badge --------------------------
        # Critical UX rule from the user: the operator must NEVER be
        # confused about whether the camera is recording.  When the
        # service is connected but recording is OFF, show a calm
        # "LIVE PREVIEW · not recording" badge.  When recording IS
        # on (toggled either by the per-experiment Test Parameters
        # group, or programmatically), flip to a prominent red
        # "● RECORDING" badge.  The badge sits ABOVE the video
        # label so it's always in the operator's line of sight.
        self._status_badge = QtWidgets.QLabel("LIVE PREVIEW  ·  not recording")
        self._status_badge.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter)
        self._status_badge.setStyleSheet(
            "QLabel { "
            "background: #2e7d32; color: white; font-weight: bold; "
            "padding: 3px 8px; border-radius: 3px; "
            "}")
        self._status_badge.setToolTip(
            "Camera live-preview status.\n\n"
            "GREEN 'LIVE PREVIEW · not recording' — frames are streaming "
            "to the screen only.  No file is being written.\n\n"
            "RED '● RECORDING' — frames are ALSO being saved to an MP4 "
            "clip in <repo>\\test\\.  Recording is enabled via the "
            "'Record MP4 video for the entire run' toggle in the "
            "Test Parameters group, or programmatically.")
        # Hidden until the service connects (matches the rest of
        # this widget's lifecycle).
        self._status_badge.setVisible(False)

        # ---- optional inline snapshot button ------------------------
        self._snapshot_btn: Optional[QtWidgets.QPushButton] = None
        if allow_snapshot_button:
            self._snapshot_btn = QtWidgets.QPushButton("Snapshot")
            self._snapshot_btn.setToolTip(
                "Save a still frame to <repo>\\test\\ "
                "(camera_<timestamp>.jpg).  This is independent of "
                "recording — works whether or not recording is "
                "active.")
            self._snapshot_btn.clicked.connect(self._svc.take_snapshot)
            self._snapshot_btn.setEnabled(self._svc.is_connected())

        v = QtWidgets.QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(2)
        v.addWidget(self._status_badge)
        v.addWidget(self._video_label, stretch=1)
        if self._snapshot_btn is not None:
            btn_row = QtWidgets.QHBoxLayout()
            btn_row.addWidget(self._snapshot_btn)
            btn_row.addStretch(1)
            v.addLayout(btn_row)

        # ---- visibility default + service hookup --------------------
        self._always_visible: bool = False
        self.setVisible(self._svc.is_connected())

        self._svc.frameReady.connect(self._on_frame)
        self._svc.connected.connect(self._on_service_connected)
        self._svc.disconnected.connect(self._on_service_disconnected)
        self._svc.recordingChanged.connect(self._on_recording_changed)
        # Initial badge state — service may already be recording if
        # the pane is constructed mid-recording (e.g. opening the
        # optional View dock while a run is already in progress).
        self._on_recording_changed(self._svc.is_recording())

    # ----- public API ---------------------------------------------
    def set_always_visible(self, always: bool) -> None:
        """Force the pane to stay visible regardless of connection
        state.  Used by an always-on monitor dock; experiment tabs
        leave this False so the pane only takes screen space when
        the camera is actually streaming."""
        self._always_visible = bool(always)
        if self._always_visible:
            self.setVisible(True)

    # ----- service signal handlers --------------------------------
    def _on_frame(self, frame) -> None:
        # ``frame`` is a QVideoFrame.  ``toImage()`` returns a QImage
        # in the frame's pixel format (mapped to ARGB for display).
        # Cheap on modern hardware (~1-2 ms at 1280×720).
        try:
            img = frame.toImage()
            if img.isNull():
                return
            pix = QtGui.QPixmap.fromImage(img)
            # Scale to fit while preserving aspect — KeepAspectRatio
            # leaves letterbox bars but never distorts the bench
            # photograph.  Use FastTransformation (rather than
            # SmoothTransformation) because the user is watching a
            # 30 fps stream, not reading text — sharp pixels are
            # less important than no-stutter.
            scaled = pix.scaled(
                self._video_label.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.FastTransformation)
            self._video_label.setPixmap(scaled)
            self._last_pixmap = pix
        except Exception:
            pass

    def _on_service_connected(self, _desc: str) -> None:
        self.setVisible(True)
        self._status_badge.setVisible(True)
        if self._snapshot_btn is not None:
            self._snapshot_btn.setEnabled(True)
        # Refresh the badge in case the service was already recording
        # when this pane was constructed (mid-run dock open, etc.).
        self._on_recording_changed(self._svc.is_recording())

    def _on_service_disconnected(self) -> None:
        if not self._always_visible:
            self.setVisible(False)
        self._status_badge.setVisible(False)
        if self._snapshot_btn is not None:
            self._snapshot_btn.setEnabled(False)
        self._video_label.setText("Camera not connected.")
        self._video_label.setPixmap(QtGui.QPixmap())
        self._last_pixmap = None

    def _on_recording_changed(self, is_recording: bool) -> None:
        """Flip the LIVE / RECORDING badge.  Critical UX rule: the
        operator should NEVER mistake the live preview for an active
        recording, OR vice versa.

        * Calm green badge ("LIVE PREVIEW · not recording") = frames
          go to the screen only; no file is being written.
        * Prominent red badge ("● RECORDING") = frames are also
          being saved to an MP4 clip in <repo>\\test\\.
        """
        if is_recording:
            self._status_badge.setText("● RECORDING")
            self._status_badge.setStyleSheet(
                "QLabel { "
                "background: #c62828; color: white; font-weight: bold; "
                "padding: 3px 8px; border-radius: 3px; "
                "}")
        else:
            self._status_badge.setText("LIVE PREVIEW  ·  not recording")
            self._status_badge.setStyleSheet(
                "QLabel { "
                "background: #2e7d32; color: white; font-weight: bold; "
                "padding: 3px 8px; border-radius: 3px; "
                "}")

    # Re-scale on resize so the preview fills the new size.
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._last_pixmap is not None and not self._last_pixmap.isNull():
            scaled = self._last_pixmap.scaled(
                self._video_label.size(),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.FastTransformation)
            self._video_label.setPixmap(scaled)
