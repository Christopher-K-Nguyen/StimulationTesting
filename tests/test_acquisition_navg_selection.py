"""Average count (NUMAVg) is an input SPINBOX capped at the scope model's limit.

Operator: "Let average count be an input number with up and down arrows.  Be
sure to set the limit based on the oscilloscope model."  The acquisition
NUMAVg widget is now ALWAYS a spinbox (no dropdown); ``apply_scope_capabilities``
sets its range from the connected model (``average_count_choices`` min/max, or
``max_average_count``).  The scope snaps a typed value to its nearest supported
count on apply.

Also pins the original NUMAVg-selection bug (operator: "I set numavg to 64 but
the log sets it to 32"): ``current_acquisition()`` must read the spinbox value
regardless of the Setup page being the active/visible tab — a widget that is
never ``show()``n reports ``isVisible() == False`` for every child, reproducing
the entry-sync condition.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


class _Info:
    n_channels = 4
    has_ext_trigger = True


class _MockScope:
    """Minimal scope capability surface ``apply_scope_capabilities`` reads."""
    info = _Info()

    def acquisition_modes(self):
        return ["SAMPLE", "AVERAGE"]

    def average_count_choices(self):
        return [2, 4, 8, 16, 32, 64, 128, 256, 512]

    def max_average_count(self):
        return 512


def _setup_tab():
    from stimtest.gui.setup_tab import SetupTab
    return SetupTab()


def test_navg_is_spinbox_capped_at_model_limit(qapp):
    """After applying scope capabilities the average-count widget is a SPINBOX
    (never a dropdown), and its max is the connected model's NUMAVg ceiling."""
    tab = _setup_tab()
    tab.apply_scope_capabilities(_MockScope())
    assert tab.acq_navg_combo is None, "no dropdown — always a spinbox now"
    assert tab.acq_navg_spin.maximum() == 512, "max = model's NUMAVg limit"
    assert tab.acq_navg_spin.minimum() == 2, "min = model's NUMAVg floor"


def test_navg_limit_tracks_the_model(qapp):
    """A lower-limit model caps the spinbox lower (limit is model-based)."""
    class _SmallScope(_MockScope):
        def average_count_choices(self):
            return [4, 8, 16, 32, 64, 128, 256]

        def max_average_count(self):
            return 256
    tab = _setup_tab()
    tab.apply_scope_capabilities(_SmallScope())
    assert tab.acq_navg_spin.maximum() == 256
    assert tab.acq_navg_spin.minimum() == 4


def test_navg_spinbox_value_survives_hidden_setup_page(qapp):
    """The original NUMAVg bug: ``current_acquisition()`` must read the
    spinbox value even when the Setup page isn't the visible tab (a
    never-shown widget reports isVisible()==False for every child, which is
    the exact entry-sync condition)."""
    tab = _setup_tab()
    tab.apply_scope_capabilities(_MockScope())
    assert tab.acq_navg_combo is None
    tab.acq_navg_spin.setValue(64)
    assert tab.acq_navg_spin.isVisible() is False   # never shown
    mode, n_avg = tab.current_acquisition()
    assert mode == "AVERAGE"
    assert n_avg == 64, (
        f"expected the spinbox value 64, got {n_avg} — "
        f"_current_n_avg must read the spin regardless of visibility")


def test_navg_spin_is_the_source_without_scope(qapp):
    # No scope capabilities applied → still a spinbox, still the source of
    # truth (read regardless of visibility).
    tab = _setup_tab()
    assert tab.acq_navg_combo is None
    tab.acq_navg_spin.setValue(128)
    _mode, n_avg = tab.current_acquisition()
    assert n_avg == 128


def test_navg_tooltip_lists_model_choices(qapp):
    """Hover tip includes the connected model's typical NUMAVg choices
    (operator: "the hover message should include the typical choices for the
    oscilloscope model")."""
    tab = _setup_tab()
    tab.apply_scope_capabilities(_MockScope())      # choices 2..512
    tip = tab.acq_navg_spin.toolTip()
    for v in (2, 16, 64, 512):
        assert str(v) in tip, f"tooltip missing choice {v}: {tip!r}"
    assert "snap" in tip.lower()                    # explains off-grid snapping
