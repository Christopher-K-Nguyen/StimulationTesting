"""Startup scope-detection must be bounded — a wedged NI-VISA
``list_resources()`` (which can block forever) must never freeze the
launch.  These tests drive the pure ``@classmethod`` enumeration helpers
on :class:`ConnectionPanel` against a FAKE ``pyvisa`` module so no real
VISA layer / hardware is touched.

Regression guard for the startup-hang fix: the old code called
``pyvisa.ResourceManager().list_resources()`` synchronously on the GUI
thread at startup, so an unresponsive NI-VISA backend froze PULSAR's
window for as long as it hung.
"""
import sys
import time
import types

import pytest

from stimtest.gui.connection_panel import ConnectionPanel


class _FakeRM:
    def __init__(self, resources, delay):
        self._resources = tuple(resources)
        self._delay = delay

    def list_resources(self):
        if self._delay:
            time.sleep(self._delay)
        return self._resources


def _install_fake_pyvisa(monkeypatch, *, py=(), py_delay=0.0,
                         default=(), default_delay=0.0):
    fake = types.ModuleType("pyvisa")

    def ResourceManager(backend=None):
        if backend == "@py":
            return _FakeRM(py, py_delay)
        return _FakeRM(default, default_delay)

    fake.ResourceManager = ResourceManager
    monkeypatch.setitem(sys.modules, "pyvisa", fake)
    return fake


_TEK = "USB0::0x0699::0x03C7::SGVJ015026::INSTR"
_COM = "ASRL3::INSTR"


def test_finds_scope_across_backends(monkeypatch):
    # @py sees only a COM port; the default (NI-VISA) sees the scope.
    _install_fake_pyvisa(monkeypatch, py=(_COM,), default=(_COM, _TEK))
    r = ConnectionPanel._enumerate_scopes_bounded(6.0)
    assert r["no_visa"] is False
    assert r["timed_out"] is False
    assert _TEK in r["resources"]


def test_wedged_default_backend_is_bounded(monkeypatch):
    # This is the user's machine: @py returns a COM port fast, the
    # default NI-VISA backend HANGS.  The probe must return within the
    # budget (NOT wait out the 30 s hang) and flag timed_out.
    _install_fake_pyvisa(monkeypatch, py=(_COM,), default=(_TEK,),
                         default_delay=3.0)
    t0 = time.perf_counter()
    r = ConnectionPanel._enumerate_scopes_bounded(1.0)
    dt = time.perf_counter() - t0
    assert dt < 4.0, f"enumeration blocked for {dt:.1f}s — not bounded"
    assert r["timed_out"] is True
    # falls back to whatever the fast backend saw (the COM port)
    assert r["resources"] == (_COM,)


def test_list_resources_with_timeout_returns_on_hang(monkeypatch):
    _install_fake_pyvisa(monkeypatch, default=(_TEK,), default_delay=3.0)
    t0 = time.perf_counter()
    res, timed_out = ConnectionPanel._list_resources_with_timeout("", 0.5)
    dt = time.perf_counter() - t0
    assert timed_out is True
    assert res == ()
    assert dt < 2.5, f"timeout not honoured ({dt:.1f}s)"


def test_no_pyvisa_reports_no_visa(monkeypatch):
    # Simulate pyvisa import failing.
    import builtins
    real_import = builtins.__import__

    def _fake_import(name, *a, **k):
        if name == "pyvisa":
            raise ImportError("no pyvisa")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    r = ConnectionPanel._enumerate_scopes_bounded(2.0)
    assert r["no_visa"] is True
    assert r["resources"] == ()


def test_no_instruments_is_clean_not_detected(monkeypatch):
    _install_fake_pyvisa(monkeypatch, py=(), default=())
    r = ConnectionPanel._enumerate_scopes_bounded(3.0)
    assert r["no_visa"] is False
    assert r["timed_out"] is False
    assert r["resources"] == ()
