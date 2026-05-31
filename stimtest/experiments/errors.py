"""Structured runner-side exceptions + classification helpers.

Closes Task #55 (mid-run hardware disconnect recovery).  The goal is
to convert the dozen+ different shapes of "USB cable just got
yanked" exceptions (pyvisa VisaIOError, libusb error strings,
Plexon DLL return codes, OSError variants) into a single
:class:`HardwareDisconnectError` that the worker-thread / GUI
layer can surface as a clear operator-facing message instead of a
cryptic VISA / DLL traceback.

The runner detects + raises; the worker thread catches in
``RunnerWorker.run()``; the experiment tab's ``_on_finished``
handler shows a tailored "Scope/Stim disconnected" dialog
instead of the generic "Run aborted (RuntimeError)" path.

Prior captures are already safely on disk via the per-capture
incremental save (Task #54), so the worst case here is "user
loses the rest of the planned sweep but keeps what completed."
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional


# Known disconnect-shaped error fingerprints.  String-matched against
# ``repr(exception).lower()``.  Pragmatic list — not exhaustive, but
# covers the common USB unplug + power-cycle + interface-lock cases
# we've actually hit on the bench.  Adding new ones is cheap: append
# a lowercased substring that uniquely identifies the failure mode.
#
# Conventions:
#   * Anything starting ``vi_error_`` is a pyvisa error code.
#   * Anything mentioning ``libusb`` is a libusb-win32 backend error.
#   * Anything mentioning ``ps_`` or ``plexon`` / ``plexstim`` is
#     the Plexon vendor DLL.
#   * Plain phrases ("no such device", "device disconnected") are
#     OS-level errors common to both classes.
_DISCONNECT_HINTS_SCOPE: tuple[str, ...] = (
    # pyvisa error codes
    "vi_error_inv_object",
    "vi_error_conn_lost",
    "vi_error_inv_session",
    "vi_error_rsrc_nfound",
    "vi_error_ncic",            # not controller-in-charge (USB-TMC drop)
    # libusb-win32 backend (Tek over libusb)
    "libusb0-dll:err",
    "semaphore timeout",        # libusb-win32 stale-handle signature
    # pyvisa wrapper exception names
    "visaioerror",
    "invalid_session",
)

_DISCONNECT_HINTS_STIM: tuple[str, ...] = (
    # Plexon DLL signatures
    "ps_initallstim",
    "ps_init_all_stim",
    "plexon stimulator",
    "no plexon stimulator",
    "plexstim",
    "stimulator not initialized",
    "ps_get_n_stim",
    # The HEAP_CORRUPTION fingerprint we hit earlier
    "0xc0000374",
    "status_heap_corruption",
)

_DISCONNECT_HINTS_GENERIC: tuple[str, ...] = (
    "no such device",
    "device disconnected",
    "device not found",
    "device removed",
    "broken pipe",
    "endpoint stalled",
)


class HardwareDisconnectError(RuntimeError):
    """Raised when a scope or stim USB disconnect is detected mid-run.

    Attributes
    ----------
    device : str
        ``"scope"`` or ``"stim"`` — which device class went away.
        Carried so the operator-facing dialog can say "Scope
        disconnected" rather than guessing.
    original : Exception
        The underlying exception that triggered the classification
        (VisaIOError / OSError / RuntimeError / etc.).  Preserved
        for the LogPane + crash-log path so post-mortem analysis
        sees the actual SCPI / DLL error.
    where : str
        Optional short label of what operation was in flight
        (``"single_capture"``, ``"load_channel"``, etc.).  Empty
        when the caller didn't have a label handy.
    """

    def __init__(self, device: str, original: Exception,
                 where: str = ""):
        if device not in ("scope", "stim"):
            raise ValueError(
                f"device must be 'scope' or 'stim', got {device!r}")
        self.device = device
        self.original = original
        self.where = where
        super().__init__(
            f"Hardware disconnect detected on {device}"
            f"{f' during {where}' if where else ''}: "
            f"{type(original).__name__}: {original}")


def looks_like_disconnect(exc: Exception) -> Optional[str]:
    """Classify ``exc`` as a disconnect-shaped error or not.

    Returns the device class (``"scope"`` or ``"stim"``) on a match,
    or ``None`` when the exception doesn't look like a disconnect.

    Matching is repr-string-based with the fingerprint lists above.
    Already-classified :class:`HardwareDisconnectError` is recognized
    and returns its ``.device`` directly (lets the helper be safely
    nested in re-raise chains).
    """
    if isinstance(exc, HardwareDisconnectError):
        return exc.device
    msg = repr(exc).lower()
    # Walk specific-device lists first so a libusb error doesn't get
    # mis-classified as stim or vice versa.
    for hint in _DISCONNECT_HINTS_SCOPE:
        if hint in msg:
            return "scope"
    for hint in _DISCONNECT_HINTS_STIM:
        if hint in msg:
            return "stim"
    # Generic hints alone aren't enough to classify — they could
    # come from anywhere.  Return None and let the caller decide
    # based on context (the runner knows which device it was talking
    # to when the error fired).
    for hint in _DISCONNECT_HINTS_GENERIC:
        if hint in msg:
            return None  # ambiguous; caller has context
    return None


@contextmanager
def reraise_as_disconnect(device: str, where: str = "") -> Iterator[None]:
    """Context manager that re-raises matching exceptions as
    :class:`HardwareDisconnectError` tagged with the known device.

    Wrap scope or stim calls in the runner when you want the
    disconnect path to short-circuit the normal exception flow.
    Non-disconnect-shaped exceptions pass through unchanged.

    Usage::

        with reraise_as_disconnect("scope", "single_capture"):
            acq = self.scope.single_capture(timeout_s=10)

    If the call site doesn't have a tidy device context (e.g., a
    generic helper that could be called for either), prefer
    catching at a higher level and using :func:`looks_like_disconnect`.
    """
    if device not in ("scope", "stim"):
        raise ValueError(
            f"device must be 'scope' or 'stim', got {device!r}")
    try:
        yield
    except HardwareDisconnectError:
        # Already classified — let it propagate.
        raise
    except Exception as e:
        classified = looks_like_disconnect(e)
        # Trust the call-site device label even if the message hints
        # at a different class — call-site context is more reliable
        # than fingerprint matching.  But only re-raise if SOMETHING
        # in the exception hints at a disconnect, so we don't
        # mis-classify a code bug (e.g. AttributeError) as a disconnect.
        if classified is not None or any(
                h in repr(e).lower() for h in _DISCONNECT_HINTS_GENERIC):
            raise HardwareDisconnectError(device, e, where) from e
        raise
