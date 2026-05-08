#!/usr/bin/env python
"""Headless smoke test: instantiate ViewerWindow and load a session into it
without actually starting the Qt event loop. Verifies the tree-population
and plotting paths work end-to-end."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Run Qt headlessly
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6 import QtWidgets
from stimtest.gui.viewer import ViewerWindow


def main() -> int:
    npz = Path("data") / "vt_run.npz"
    if not npz.exists():
        print(f"Need {npz}. Run run_cli.py --simulate first.")
        return 1
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    win = ViewerWindow(initial_path=npz)
    # Walk the tree and select the first capture to exercise the plot path
    root = win.tree.invisibleRootItem()
    if root.childCount() == 0:
        print("No sessions loaded into tree."); return 2
    sess_item = root.child(0)
    win.tree.setCurrentItem(sess_item)
    if sess_item.childCount() == 0:
        print("Session has no runs."); return 3
    run_item = sess_item.child(0)
    win.tree.setCurrentItem(run_item)
    if run_item.childCount() == 0:
        print("Run has no captures."); return 4
    cap_item = run_item.child(run_item.childCount() - 1)
    win.tree.setCurrentItem(cap_item)
    app.processEvents()
    print("ViewerWindow constructed and rendered first capture OK")
    print(f"  session: {sess_item.text(0)}  runs: {sess_item.childCount()}")
    print(f"  selected: {cap_item.text(0)} ({cap_item.text(1)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
