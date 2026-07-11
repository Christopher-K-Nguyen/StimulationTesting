"""Regression tests for the stuck "No pulse pattern set." preview.

Bug: the pulse-pattern preview lives in ``params_page``, which MainWindow
re-parents into a SEPARATE top-level "Test parameters" tab.  The only
initial-render trigger was ``_BaseExperimentTab.showEvent`` (the
EXPERIMENT-VIEW widget) — which never fires for a user who lands on
Setup / Test parameters at launch (the launch default focuses Setup).
Result: every pulse field populated, but the preview shows
"No pulse pattern set." until the operator nudges a control.

Fix: the first render is an idempotent ``ensure_preview_rendered()``
called from BOTH ``showEvent`` AND ``MainWindow._show_experiment`` (the
params-page-shown path, which runs after restore_prefs at launch).

These tests assert the tab-level contract for every experiment tab
type; the MainWindow wiring is a one-line ``ensure_preview_rendered()``
call verified by inspection + the headless launch smoke check.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _curve_points(tab) -> int:
    xs = tab.pattern_preview.curve.getData()[0]
    return 0 if xs is None else len(xs)


def _all_tab_classes():
    from stimtest.gui.experiment_tabs import (
        VoltageTransientTab, ShortPulsingTab,
        LongPulsingTab, ProgressiveStressTab,
    )
    return [VoltageTransientTab, ShortPulsingTab,
            LongPulsingTab, ProgressiveStressTab]


def _make(cls, qapp):
    from stimtest.electrode import ElectrodeArray
    return cls(ElectrodeArray.utah_4x4())


def test_preview_starts_blank_then_renders_once(qapp):
    # Mirrors launch: tab constructed, but neither the experiment-view
    # showEvent nor the params-page hook has fired yet.
    tab = _make(_all_tab_classes()[0], qapp)  # VoltageTransientTab
    assert tab._first_show_done is False
    assert _curve_points(tab) == 0, "preview should start on the banner"

    # The params-page hook (MainWindow._show_experiment) fires this.
    tab.ensure_preview_rendered()
    assert tab._first_show_done is True
    assert _curve_points(tab) > 0, "banner should be replaced by a real trace"


def test_ensure_preview_rendered_is_idempotent(qapp):
    tab = _make(_all_tab_classes()[0], qapp)
    tab.ensure_preview_rendered()
    pts1 = _curve_points(tab)
    tab.ensure_preview_rendered()  # second call → no-op
    assert tab._first_show_done is True
    assert _curve_points(tab) == pts1


def test_showevent_path_also_renders(qapp):
    from PyQt6 import QtGui
    tab = _make(_all_tab_classes()[0], qapp)
    assert _curve_points(tab) == 0
    # The experiment-VIEW path: showEvent must render too (the original
    # trigger, preserved).
    tab.showEvent(QtGui.QShowEvent())
    assert tab._first_show_done is True
    assert _curve_points(tab) > 0


@pytest.mark.parametrize("cls", _all_tab_classes())
def test_every_experiment_tab_renders_initial_preview(cls, qapp):
    # All four experiment tabs share _BaseExperimentTab, so each must
    # clear the banner via the shared idempotent renderer.
    tab = _make(cls, qapp)
    assert _curve_points(tab) == 0, f"{cls.__name__} should start blank"
    tab.ensure_preview_rendered()
    assert _curve_points(tab) > 0, f"{cls.__name__} preview never rendered"
