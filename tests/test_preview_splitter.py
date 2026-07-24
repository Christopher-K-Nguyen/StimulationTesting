"""Pattern preview lives in its own scroll pane below a drag-resizable
vertical splitter (operator #7: "own scrollbar + a drag-resizable border,
like the experiment plot/inset splitter").  Tabs without a pattern preview
(EIS) keep the plain scroll column.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

from PyQt6 import QtCore, QtWidgets


def _mw():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    return app, MainWindow(simulate_default=True)


def test_vt_preview_in_splitter_with_own_scroll():
    app, w = _mw()
    try:
        tab = w._exp_tab_by_code["VT"][0]
        split = getattr(tab, "_params_preview_split", None)
        assert split is not None
        assert split.orientation() == QtCore.Qt.Orientation.Vertical
        assert split.count() == 2                    # controls + preview panes
        assert not split.isCollapsible(0)            # controls never collapse
        assert split.isCollapsible(1)                # preview can be dragged shut
        # the preview sits inside a QScrollArea (its own scrollbar).
        anc = tab.pattern_preview.parent()
        while anc is not None and not isinstance(anc, QtWidgets.QScrollArea):
            anc = anc.parent()
        assert isinstance(anc, QtWidgets.QScrollArea)
    finally:
        w.close()


def test_eis_tab_has_no_preview_splitter():
    app, w = _mw()
    try:
        eis = w._exp_tab_by_code.get("EIS")
        if eis is None:
            return
        # EIS never adds the pattern preview → plain scroll column, no splitter.
        assert getattr(eis[0], "_params_preview_split", None) is None
    finally:
        w.close()
