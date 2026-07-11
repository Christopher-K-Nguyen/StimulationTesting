"""Pattern log fires on COMMIT (Enter / focus-out / discrete change), not per
keystroke (operator: "when typing in the input, do not print in the log pane
with every key.  Only print after return/enter is pressed or clicked out").

``patternChanged`` (live preview) still fires on every ``valueChanged`` tick;
``patternCommitted`` (the log-pane trigger) fires only on ``editingFinished`` /
combo / checkbox change.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def _panel():
    from stimtest.gui.pattern_panel import PatternControlPanel
    return PatternControlPanel()


def test_typing_a_value_does_not_commit(qapp):
    p = _panel()
    live = {"n": 0}
    commit = {"n": 0}
    p.patternChanged.connect(lambda _pat: live.__setitem__("n", live["n"] + 1))
    p.patternCommitted.connect(lambda _pat: commit.__setitem__("n", commit["n"] + 1))
    # setValue fires valueChanged (→ live preview) but NOT editingFinished.
    p.amp_excite.setValue(abs(p.amp_excite.value()) + 25.0)
    assert live["n"] >= 1, "live preview should update per keystroke"
    assert commit["n"] == 0, "no commit until Enter / focus-out"
    # editingFinished (Enter / focus-out) → commit fires.
    p.amp_excite.editingFinished.emit()
    assert commit["n"] >= 1, "commit fires on editingFinished"


def test_discrete_control_change_commits(qapp):
    p = _panel()
    commit = {"n": 0}
    p.patternCommitted.connect(lambda _pat: commit.__setitem__("n", commit["n"] + 1))
    before = commit["n"]
    # A polarity combo change is a discrete commit (like a click).
    cur = p.polarity.currentIndex()
    p.polarity.setCurrentIndex(1 - cur)
    assert commit["n"] > before, "combo change commits immediately"


def test_commit_suppressed_during_suspend(qapp):
    p = _panel()
    commit = {"n": 0}
    p.patternCommitted.connect(lambda _pat: commit.__setitem__("n", commit["n"] + 1))
    p._suspend_signals = True
    try:
        p.amp_excite.editingFinished.emit()
        assert commit["n"] == 0, "no commit while signals suspended (prefs restore)"
    finally:
        p._suspend_signals = False
