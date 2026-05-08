"""Results tab — embeds the full session viewer.

The Results tab is now a thin shell around :class:`ViewerPanel` (the
embeddable body of the standalone Echem-Analyst-style Viewer). Loading
a session, browsing runs/captures, viewing the channel map, and
exporting plots all happen in the same UI as the standalone viewer —
no more separate window.

A small toolbar above the panel keeps the existing per-session export
shortcuts (Excel + per-channel TIFFs) that don't have a viewer-side
equivalent. The "Open in standalone Viewer" button is gone — the Tab
*is* the viewer now.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6 import QtCore, QtWidgets

from ..persistence import load_session_npz
from .viewer import ViewerPanel


class ResultsTab(QtWidgets.QWidget):
    """Results tab — a save-folder picker on top of an embedded viewer."""

    def __init__(self, save_dir: Path, parent=None):
        super().__init__(parent)
        self.save_dir = Path(save_dir)

        # Embedded viewer body — does the heavy lifting (tree, plot
        # canvas, channel map, info tables, export menu actions).
        self.viewer = ViewerPanel(initial_path=self.save_dir, parent=self)

        # Toolbar above the viewer with quick actions that aren't part
        # of the viewer's own UI: refresh the folder index, copy the
        # save path, and run the session-level Excel + plot exports
        # (the viewer has its own File→Save plot menu equivalents).
        self.refresh_btn = QtWidgets.QPushButton("Refresh folder")
        self.refresh_btn.setToolTip("Re-scan the save directory for new "
                                    ".npz session files.")
        self.copy_path_btn = QtWidgets.QPushButton("Copy save path")
        self.copy_path_btn.setToolTip("Copy the current save folder path "
                                      "to the clipboard.")
        self.export_xlsx_btn = QtWidgets.QPushButton("Export .xlsx")
        self.export_xlsx_btn.setToolTip(
            "Export the currently-selected session to a Gamry-DTA-style "
            ".xlsx workbook (one sheet per channel).")
        # Plot-export tool button — dropdown lets the user pick the
        # output image format. The four entries cover the formats the
        # underlying ``export_session_plots`` / ``export_session_summary_plots``
        # know how to write (matplotlib infers from the extension);
        # all four use the same per-channel + summary layout, just
        # different on-disk encoding.
        self.export_plots_btn = QtWidgets.QToolButton()
        self.export_plots_btn.setText("Export plot")
        self.export_plots_btn.setPopupMode(
            QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.export_plots_btn.setToolTip(
            "Save one image per channel (final capture) plus the "
            "Q_inj-vs-I_stim and V_d-vs-Q_inj summaries. Pick the "
            "output format from the menu.")
        export_menu = QtWidgets.QMenu(self.export_plots_btn)
        for label, fmt in (("PNG",  "png"),
                           ("JPEG", "jpeg"),
                           ("TIF",  "tif"),
                           ("SVG",  "svg")):
            act = export_menu.addAction(label)
            # Default-arg lambda captures fmt by value, not by reference,
            # so each menu entry exports in its own format rather than
            # all of them collapsing to the last loop iteration.
            act.triggered.connect(
                lambda _checked=False, _fmt=fmt: self._export_plots(_fmt))
        self.export_plots_btn.setMenu(export_menu)

        self.refresh_btn.clicked.connect(self.refresh)
        self.copy_path_btn.clicked.connect(self._copy_path)
        self.export_xlsx_btn.clicked.connect(self._export_xlsx)

        # Status line — surfaces the viewer's status_message signal so
        # the user can see "Indexed N sessions" / "Loaded foo.npz" / etc.
        self.status_label = QtWidgets.QLabel(f"Save path: {self.save_dir}")
        self.status_label.setStyleSheet("color: #555; padding: 2px;")
        self.viewer.status_message.connect(self.status_label.setText)

        toolbar = QtWidgets.QHBoxLayout()
        toolbar.setContentsMargins(4, 4, 4, 0)
        toolbar.addWidget(self.refresh_btn)
        toolbar.addWidget(self.copy_path_btn)
        toolbar.addSpacing(12)
        toolbar.addWidget(self.export_xlsx_btn)
        toolbar.addWidget(self.export_plots_btn)
        toolbar.addStretch(1)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)
        outer.addLayout(toolbar)
        outer.addWidget(self.viewer, stretch=1)
        outer.addWidget(self.status_label)

    # ----------------------------------------------------------- API
    def set_save_dir(self, p: Path):
        """Repoint the tab at a new save directory and re-index it."""
        self.save_dir = Path(p)
        self.status_label.setText(f"Save path: {self.save_dir}")
        self.refresh()

    @QtCore.pyqtSlot()
    def refresh(self):
        """Re-scan the save folder for ``.npz`` files."""
        if self.save_dir.exists():
            self.viewer.load_folder(self.save_dir)
        else:
            self.status_label.setText(f"Save path missing: {self.save_dir}")

    # ----- Quick actions on the currently-selected tree item ---------
    def _selected_npz_path(self):
        """Return the .npz file path the user has selected in the tree,
        or ``None`` when no session-level item is active.

        Walks the viewer's QTreeWidget item up to whichever ancestor
        carries a session-level path (the ROLE_PATH user-data role)
        and returns it as a :class:`Path`. Returns ``None`` if the
        user hasn't picked any session yet.
        """
        from .viewer import ROLE_PATH, ROLE_KIND, KIND_SESSION
        item = self.viewer.tree.currentItem()
        while item is not None:
            kind = item.data(0, ROLE_KIND)
            if kind == KIND_SESSION:
                p = item.data(0, ROLE_PATH)
                return Path(p) if p else None
            item = item.parent()
        return None

    def _copy_path(self):
        QtWidgets.QApplication.clipboard().setText(str(self.save_dir.resolve()))
        self.status_label.setText(
            f"Copied to clipboard: {self.save_dir.resolve()}")

    def _export_xlsx(self):
        """Export the selected session as a Gamry-DTA-style .xlsx."""
        npz_path = self._selected_npz_path()
        if npz_path is None:
            QtWidgets.QMessageBox.information(
                self, "No selection",
                "Select a saved session in the tree first.")
            return
        xlsx_path = npz_path.with_suffix(".xlsx")
        try:
            session = load_session_npz(npz_path)
            from ..persistence import save_session_xlsx
            save_session_xlsx(session, xlsx_path)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Excel export failed", str(e))
            return
        self.status_label.setText(f"Wrote {xlsx_path.resolve()}")

    def _export_plots(self, fmt: str = "tif"):
        """Save one image per channel + summary plots for the selected
        session in the requested format. Mirrors the end-of-sweep
        folder ``runVoltageTransient.m`` writes after a real run.
        ``fmt`` is the file extension the plotting helpers infer from
        — matplotlib's ``savefig`` reads it directly, so any of the
        four menu entries (PNG / JPEG / TIF / SVG) is supported.
        """
        npz_path = self._selected_npz_path()
        if npz_path is None:
            QtWidgets.QMessageBox.information(
                self, "No selection",
                "Select a saved session in the tree first.")
            return
        out_dir = npz_path.with_suffix("")
        try:
            session = load_session_npz(npz_path)
            from ..plotting import (export_session_plots,
                                    export_session_summary_plots)
            paths = export_session_plots(session, out_dir, fmt=fmt)
            paths += export_session_summary_plots(session, out_dir, fmt=fmt)
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Plot export failed", str(e))
            return
        self.status_label.setText(
            f"Wrote {len(paths)} {fmt.upper()} file(s) to "
            f"{out_dir.resolve()}")
