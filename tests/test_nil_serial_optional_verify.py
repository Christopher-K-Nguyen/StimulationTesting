"""CWRU stimulator PLX00178 is NIL-scaled; verification is optional (no popup).

Operator: "Copy their serial number into our list [of] NIL scaled stimulators.
Please have verification optional and do not make that pop up warning about not
having verified."
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")


def test_cwru_serial_in_nil_list():
    from stimtest.config import NIL_SERIAL_NUMBERS
    assert "PLX00178" in NIL_SERIAL_NUMBERS


def test_cwru_serial_resolves_to_nil_scaling():
    # Mirror plexon.open()'s ``is_nil`` gate + preset selection.
    from stimtest.config import (
        NIL_SERIAL_NUMBERS, VMON_SCALING_NIL, IMON_SCALING_NIL,
        VMON_SCALING_DEFAULT, IMON_SCALING_DEFAULT)
    serial = "PLX00178"
    is_nil = any(s in serial for s in NIL_SERIAL_NUMBERS)
    assert is_nil
    assert (VMON_SCALING_NIL if is_nil else VMON_SCALING_DEFAULT) \
        == VMON_SCALING_NIL
    assert (IMON_SCALING_NIL if is_nil else IMON_SCALING_DEFAULT) \
        == IMON_SCALING_NIL


def test_unverified_popup_method_removed():
    pytest.importorskip("PyQt6")
    from stimtest.gui.connection_panel import ConnectionPanel
    # The "Unverified stimulator" modal (and its call site) are gone —
    # verification is optional, no nag on a serial that isn't in the DB.
    assert not hasattr(ConnectionPanel, "_warn_uncalibrated_serial")
