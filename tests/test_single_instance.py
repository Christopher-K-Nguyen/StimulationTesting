"""Single-instance guard — at most one PULSAR window per user.

Operator: "Prevent PULSAR from opening another window."  A second PULSAR
fights the PlexStim 2.0's exclusive USB lock (the SDK reports "No Plexon
Stimulator is detected") and two processes touching the single-producer DLL
risk a heap-corruption race.  ``launch()`` therefore claims a per-user
QLocalServer; a second launch detects it, pings the running window to the
front, and exits without opening a duplicate.
"""
from __future__ import annotations

import os
import sys

import pytest

pytest.importorskip("PyQt6")


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _clear(name: str) -> None:
    from PyQt6 import QtNetwork
    QtNetwork.QLocalServer.removeServer(name)


def test_first_instance_is_primary_second_is_secondary(qapp):
    from stimtest.gui.main_window import _acquire_single_instance
    # ISOLATED server name so the test never collides with a real PULSAR
    # that's actually running on this machine (which legitimately holds the
    # production server name).
    name = "PULSAR-stimtest-pytest-isolated"
    os.environ.pop("PULSAR_ALLOW_MULTIPLE", None)
    _clear(name)
    srv1 = None
    try:
        s1, srv1 = _acquire_single_instance(server_name=name)
        assert s1 == "primary" and srv1 is not None
        # A second acquisition (same name, server live) must NOT claim it.
        s2, srv2 = _acquire_single_instance(server_name=name)
        assert s2 == "secondary" and srv2 is None
    finally:
        if srv1 is not None:
            srv1.close()
        _clear(name)


def test_env_var_bypasses_guard(qapp):
    from stimtest.gui.main_window import _acquire_single_instance
    os.environ["PULSAR_ALLOW_MULTIPLE"] = "1"
    try:
        status, server = _acquire_single_instance()
        assert status == "disabled" and server is None
    finally:
        os.environ.pop("PULSAR_ALLOW_MULTIPLE", None)


def test_server_name_is_per_user_and_safe(qapp):
    from stimtest.gui.main_window import _single_instance_server_name
    n = _single_instance_server_name()
    assert n.startswith("PULSAR-stimtest-single-instance-")
    assert " " not in n  # no whitespace → valid local-server name
