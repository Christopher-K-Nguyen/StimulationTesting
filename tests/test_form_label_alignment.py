"""Setup-tab form label alignment (operator: "I want the input labels left
aligned" + "align the middles").

Two contracts:
  * ``rich.make_form`` left-aligns label text (app-wide).
  * The stacked left-column data-entry forms (Session / Test device parameters
    / Oscilloscope acquisition / Experiment) share ONE label-column width so
    their field columns line up across the group boxes; the Oscilloscope
    channel-mapping mini-table is deliberately EXCLUDED (its short CHx labels
    stay narrow so the 3 combos use the full width).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")

from PyQt6 import QtCore, QtWidgets

from stimtest.gui import rich


def test_make_form_left_aligns_labels():
    f = rich.make_form()
    assert f.labelAlignment() & QtCore.Qt.AlignmentFlag.AlignLeft
    assert not (f.labelAlignment() & QtCore.Qt.AlignmentFlag.AlignRight)


def _setup_tab():
    app = (QtWidgets.QApplication.instance()
           or QtWidgets.QApplication(sys.argv))
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    return app, w, w.setup_tab


def _label_widgets(form):
    LabelRole = QtWidgets.QFormLayout.ItemRole.LabelRole
    out = []
    for r in range(form.rowCount()):
        item = form.itemAt(r, LabelRole)
        wdg = item.widget() if item is not None else None
        if isinstance(wdg, QtWidgets.QWidget):
            out.append(wdg)
    return out


def test_aligned_forms_share_one_label_column_width():
    app, w, tab = _setup_tab()
    try:
        forms = tab._label_align_forms
        assert len(forms) >= 4                       # sess / dev / acq / exp
        widths = set()
        for form in forms:
            for lab in _label_widgets(form):
                widths.add(lab.minimumWidth())
        # Every label-role widget across the grouped forms shares one min
        # width → the field columns start at the same x.
        assert len(widths) == 1, widths
        shared = widths.pop()
        assert shared > 0
    finally:
        w.close()


def test_scope_mapping_table_is_not_forced_to_the_shared_width():
    app, w, tab = _setup_tab()
    try:
        shared = _label_widgets(tab._label_align_forms[0])[0].minimumWidth()
        # The CHx labels in the channel-mapping form keep their natural (much
        # narrower) width so the Role/Bandwidth/Coupling combos get the room.
        ch_labels = [lab for lab in _label_widgets(tab._scope_role_form)
                     if lab.text().startswith("CH")]
        assert ch_labels
        assert all(lab.minimumWidth() < shared for lab in ch_labels)
    finally:
        w.close()


def test_setup_forms_have_tight_vertical_spacing():
    """Operator #1: "remove the vertical space between inputs."  The stacked
    Setup forms use a 1-px row gap, and each group box hugs its title/border
    (top+bottom content margins <= 2)."""
    app, w, tab = _setup_tab()
    try:
        for form in tab._label_align_forms:                 # sess/dev/acq/exp
            assert form.verticalSpacing() <= 1, form.verticalSpacing()
        # Every stacked group box's inner layout hugs the title (top) and
        # bottom border — the per-group chrome is where the removable air
        # lived.  (Only the top-level Setup groups are tightened; nested
        # group boxes inside the connection panel keep their own margins.)
        for box in tab._compact_group_boxes:
            lay = box.layout()
            if lay is None:
                continue
            m = lay.contentsMargins()
            assert m.top() <= 2, (box.title(), m.top())
            assert m.bottom() <= 2, (box.title(), m.bottom())
    finally:
        w.close()
