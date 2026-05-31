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
from typing import Optional

from PyQt6 import QtCore, QtWidgets

from ..persistence import load_session_npz
# NOTE: ``ViewerPanel`` is NOT imported at module load.  It pulls in
# matplotlib (~600 ms cold-start) plus a chain of plot helpers, and
# the Results tab is rarely the first thing the user touches — they
# pick a save folder, click a channel, click Start on an experiment,
# THEN open Results once data exists.  Deferring the import + the
# ``ViewerPanel`` construction to first-show of this tab cuts roughly
# 0.8-1.2 s off cold launch.  The lazy ``self.viewer`` property below
# does the import + construct on demand and caches the panel.


class ResultsTab(QtWidgets.QWidget):
    """Results tab — a save-folder picker on top of an embedded viewer.

    The embedded :class:`ViewerPanel` is built lazily: it's not
    constructed (and matplotlib is not imported) until the user first
    shows this tab.  A lightweight placeholder is shown until then.
    """

    def __init__(self, save_dir: Path, parent=None):
        super().__init__(parent)
        self.save_dir = Path(save_dir)

        # ---- Lazy ViewerPanel placeholder -----------------------
        # Stand-in widget shown while the user hasn't opened the
        # Results tab yet (or before the lazy construct has run).
        # Replaced in-place by the real ``ViewerPanel`` on the first
        # ``showEvent`` (see :meth:`_ensure_viewer`).
        self._viewer: Optional[QtWidgets.QWidget] = None
        self._pending_viewer_prefs: Optional[dict] = None
        self._viewer_placeholder = QtWidgets.QLabel(
            "Loading viewer…", parent=self)
        self._viewer_placeholder.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignCenter)
        self._viewer_placeholder.setStyleSheet(
            "QLabel { color: #888; font-style: italic; padding: 24px; }")

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

        # Status line — placeholder text until the ViewerPanel is
        # constructed and its ``status_message`` signal can be wired
        # up (lazy on first ``showEvent``).
        self.status_label = QtWidgets.QLabel(f"Save path: {self.save_dir}")
        self.status_label.setStyleSheet("color: #555; padding: 2px;")

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
        # Placeholder first; real ViewerPanel swapped in by
        # :meth:`_ensure_viewer` on showEvent.
        outer.addWidget(self._viewer_placeholder, stretch=1)
        outer.addWidget(self.status_label)
        self._outer_layout = outer

    # ------------------------------------------------------------- lazy load
    def _ensure_viewer(self) -> None:
        """Construct the real :class:`ViewerPanel` on first call.

        Imports :mod:`stimtest.gui.viewer` (which pulls matplotlib);
        builds the panel; swaps it into the layout in place of the
        placeholder; wires the status_message signal; and applies any
        prefs that were queued via :meth:`restore_prefs` before the
        viewer existed.  Idempotent — subsequent calls no-op.
        """
        if self._viewer is not None:
            return
        from .viewer import ViewerPanel
        self._viewer = ViewerPanel(
            initial_path=self.save_dir, parent=self)
        # Swap the placeholder out of the layout and the viewer in.
        idx = self._outer_layout.indexOf(self._viewer_placeholder)
        self._outer_layout.removeWidget(self._viewer_placeholder)
        self._viewer_placeholder.deleteLater()
        self._outer_layout.insertWidget(idx, self._viewer, stretch=1)
        # Wire the status pass-through (deferred from __init__).
        self._viewer.status_message.connect(self.status_label.setText)
        # Apply any prefs that arrived before the viewer was built.
        if self._pending_viewer_prefs is not None:
            try:
                self._viewer.restore_prefs(self._pending_viewer_prefs)
            except Exception as e:
                self.status_label.setText(f"Viewer prefs ignored: {e}")
            self._pending_viewer_prefs = None
        # If the user previously called ``refresh()`` while the
        # viewer was still a placeholder, trigger the load now.
        if self.save_dir.exists():
            try:
                self._viewer.load_folder(self.save_dir)
            except Exception:
                pass

    @property
    def viewer(self):
        """Backwards-compat alias.  Ensures the ViewerPanel is
        constructed before returning it — any code touching
        ``self.viewer.*`` triggers the lazy load.
        """
        self._ensure_viewer()
        return self._viewer

    def showEvent(self, event):
        """Trigger lazy viewer construction on first show.

        Subsequent shows are no-ops because :meth:`_ensure_viewer`
        is idempotent.  Running here (after ``__init__`` has
        returned) means the cold-launch path doesn't pay for
        matplotlib + ViewerPanel before the main window is paintable.
        """
        super().showEvent(event)
        self._ensure_viewer()

    # ----------------------------------------------------------- API
    def set_save_dir(self, p: Path):
        """Repoint the tab at a new save directory and re-index it."""
        self.save_dir = Path(p)
        self.status_label.setText(f"Save path: {self.save_dir}")
        self.refresh()

    @QtCore.pyqtSlot()
    def refresh(self):
        """Re-scan the save folder for ``.npz`` files.

        If the viewer hasn't been lazily constructed yet (user never
        showed the Results tab), this is a no-op — the next
        :meth:`_ensure_viewer` call will trigger ``load_folder`` for
        us, so we don't waste time loading data the user hasn't asked
        to look at yet.
        """
        if self._viewer is None:
            return
        if self.save_dir.exists():
            self._viewer.load_folder(self.save_dir)
        else:
            self.status_label.setText(f"Save path missing: {self.save_dir}")

    # ----------------------------------------------------------- prefs
    def current_prefs(self) -> dict:
        """Snapshot the embedded viewer's prefs so MainWindow can
        persist them alongside the rest of the GUI state.

        ResultsTab itself doesn't carry any user-visible state worth
        remembering — the toolbar buttons are stateless, and the
        save_dir is owned by the Setup tab. The interesting bits
        (axis map, last-opened session, splitter sizes) live inside
        :class:`ViewerPanel`; we just forward.

        If the viewer hasn't been constructed yet (user never opened
        the Results tab), echo back the prefs we received in
        ``restore_prefs`` so they survive a launch-then-close-with-
        no-viewer-touch round trip.
        """
        if self._viewer is None:
            return ({"viewer": self._pending_viewer_prefs}
                    if self._pending_viewer_prefs else {})
        try:
            return {"viewer": self._viewer.current_prefs()}
        except Exception:
            return {}

    def restore_prefs(self, p: dict) -> None:
        """Apply a previously-saved snapshot to the embedded viewer.

        Called during MainWindow's prefs restore on cold launch —
        BEFORE the Results tab is shown, so the viewer hasn't been
        lazily constructed yet.  Queue the prefs and apply them in
        :meth:`_ensure_viewer` when the user first opens this tab.
        """
        if not isinstance(p, dict) or not p:
            return
        sub = p.get("viewer")
        if not isinstance(sub, dict):
            return
        if self._viewer is None:
            # Defer — viewer will pick this up on first show.
            self._pending_viewer_prefs = sub
            return
        try:
            self._viewer.restore_prefs(sub)
        except Exception as e:
            self.status_label.setText(f"Viewer prefs ignored: {e}")

    # ----- Quick actions on the currently-selected tree item ---------
    def _selected_npz_path(self):
        """Return the .npz file path the user has selected in the tree,
        or ``None`` when no session-level item is active.

        Walks the viewer's QTreeWidget item up to whichever ancestor
        carries a session-level path (the ROLE_PATH user-data role)
        and returns it as a :class:`Path`. Returns ``None`` if the
        user hasn't picked any session yet (or the viewer hasn't
        been lazily constructed — same outcome).
        """
        if self._viewer is None:
            return None
        from .viewer import ROLE_PATH, ROLE_KIND, KIND_SESSION
        item = self._viewer.tree.currentItem()
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
