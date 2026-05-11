"""Windows USB hot-plug detection.

Hooks into the Win32 ``WM_DEVICECHANGE`` broadcast so the GUI can
re-probe its connection-state indicators (and any caches that depend
on what's currently on the bus) whenever a USB device is plugged in
or unplugged. The connection panel lights up its "scope detected"
dot from this same probe, so wiring it to a hot-plug refresh means
the indicator always reflects reality without the user having to
click anything.

Why a native event filter?
    Qt itself doesn't surface device-arrival events — they're
    Windows-specific. ``QAbstractNativeEventFilter`` is the
    documented escape hatch for poking the underlying ``MSG`` pump.
    On macOS / Linux the filter is a harmless no-op (it never sees
    a Windows MSG), so the rest of the app code doesn't have to
    branch on platform.

Usage:

    from stimtest.gui.usb_hotplug import install_usb_hotplug_filter

    install_usb_hotplug_filter(
        QtWidgets.QApplication.instance(),
        on_change=lambda arrived: panel.refresh_hardware_detection(),
    )

The callback is invoked on the GUI thread (filters run inside the
Qt event loop), so it's safe to touch widgets directly.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional

from PyQt6 import QtCore


# WM_DEVICECHANGE = 0x0219; sent by Windows when a device is added
# or removed. The wParam tells you which sub-event occurred — we
# only care about arrival / removal-complete here. The other values
# (DBT_DEVNODES_CHANGED, DBT_QUERYREMOVE, etc.) are noisier and don't
# add useful refresh signal.
_WM_DEVICECHANGE = 0x0219
_DBT_DEVICEARRIVAL = 0x8000
_DBT_DEVICEREMOVECOMPLETE = 0x8004


class _DeviceChangeFilter(QtCore.QAbstractNativeEventFilter):
    """Native event filter that fires ``on_change(arrived)`` when a
    USB device is plugged in or unplugged.

    ``arrived`` is True for arrivals (DBT_DEVICEARRIVAL) and False for
    removals (DBT_DEVICEREMOVECOMPLETE). Other ``WM_DEVICECHANGE``
    sub-events are ignored — the noisy ones (DBT_DEVNODES_CHANGED,
    QUERY_REMOVE, etc.) don't actually correspond to a state change
    we'd want to re-probe on.
    """

    def __init__(self, on_change: Callable[[bool], None]):
        super().__init__()
        self._on_change = on_change

    def nativeEventFilter(self, event_type, message):
        # ``event_type`` is bytes on PyQt6 ("windows_generic_MSG" or
        # "windows_dispatcher_MSG"); on non-Windows platforms it
        # carries an X11 / Wayland / Cocoa tag and we just ignore it.
        if not isinstance(event_type, (bytes, bytearray)):
            return False, 0
        if not event_type.startswith(b"windows_"):
            return False, 0
        try:
            import ctypes
            from ctypes.wintypes import HWND, UINT, WPARAM, LPARAM, DWORD, LONG

            class _MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd", HWND),
                    ("message", UINT),
                    ("wParam", WPARAM),
                    ("lParam", LPARAM),
                    ("time", DWORD),
                    ("pt_x", LONG),
                    ("pt_y", LONG),
                ]
            msg = _MSG.from_address(int(message))
        except Exception:
            return False, 0
        if msg.message != _WM_DEVICECHANGE:
            return False, 0
        wp = int(msg.wParam)
        if wp not in (_DBT_DEVICEARRIVAL, _DBT_DEVICEREMOVECOMPLETE):
            return False, 0
        try:
            self._on_change(wp == _DBT_DEVICEARRIVAL)
        except Exception:
            # A buggy callback shouldn't poison the message pump.
            pass
        # Don't swallow — let downstream handlers (other filters,
        # default Qt processing) see the event too.
        return False, 0


def install_usb_hotplug_filter(
    app: "QtCore.QCoreApplication",
    on_change: Callable[[bool], None],
    *,
    debounce_ms: int = 200,
) -> Optional[_DeviceChangeFilter]:
    """Install the WM_DEVICECHANGE filter on ``app`` and return it.

    Plugging in a single device often fires several arrival events
    in quick succession (composite USB devices enumerate per
    interface). ``debounce_ms`` collapses bursts so the callback
    only runs once per quiet period — defaults to 200 ms, which is
    short enough to feel instant but long enough to coalesce a USB
    composite device's interface storm.

    Returns ``None`` (without installing anything) on non-Windows
    platforms — the call is a no-op there.
    """
    if sys.platform != "win32":
        return None

    # Debounce via a single-shot timer that re-arms on every event.
    # Owned by the filter so it gets garbage-collected with the
    # application instance.
    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.setInterval(max(0, int(debounce_ms)))
    state = {"arrived": False}

    def _fire():
        try:
            on_change(state["arrived"])
        except Exception:
            pass

    timer.timeout.connect(_fire)

    def _bounce(arrived: bool):
        state["arrived"] = arrived
        timer.start()

    flt = _DeviceChangeFilter(_bounce)
    app.installNativeEventFilter(flt)
    # Keep references alive on the app object so they don't get
    # garbage-collected while the filter is still installed.
    app._usb_hotplug_filter = flt          # type: ignore[attr-defined]
    app._usb_hotplug_timer = timer         # type: ignore[attr-defined]
    return flt
