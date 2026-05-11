#!/usr/bin/env python
"""POLARIS — session viewer.

Standalone viewer for inspecting saved PULSAR sessions. Implements a
Gamry Echem Analyst-style browser (in ``stimtest.gui.viewer``). Open a
single ``.npz`` file or point at a folder of sessions and click through
the tree to inspect captures, run summaries, and session metadata.

Examples
--------
    python run_viewer.py
    python run_viewer.py data/vt_run.npz
    python run_viewer.py data/
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stimtest.gui.viewer import launch


def main() -> int:
    p = argparse.ArgumentParser(description="POLARIS — session viewer")
    p.add_argument("path", nargs="?", default=None,
                   help="Optional .npz file or folder to open at startup")
    args = p.parse_args()
    return launch(Path(args.path) if args.path else None)


if __name__ == "__main__":
    sys.exit(main())
