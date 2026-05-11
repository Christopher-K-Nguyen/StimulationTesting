#!/usr/bin/env python
"""PULSAR — GUI entry point.

Launches the PULSAR neural-stimulation characterization GUI. The
companion session viewer is launched separately via ``run_viewer.py``
(POLARIS).
"""
from __future__ import annotations

import argparse
import sys

from stimtest.gui.main_window import launch


def main() -> int:
    parser = argparse.ArgumentParser(description="PULSAR — GUI")
    parser.add_argument(
        "--simulate", action="store_true",
        help="Force simulator backends (no hardware required)",
    )
    parser.add_argument(
        "--save-dir", default=None,
        help="Default folder to save session data (defaults to ./data)",
    )
    parser.add_argument(
        "--skip-prereq-check", action="store_true",
        help="Don't show the PlexStim SDK warning dialog at startup.",
    )
    args = parser.parse_args()
    return launch(simulate=args.simulate, save_dir=args.save_dir,
                  skip_prereq_check=args.skip_prereq_check)


if __name__ == "__main__":
    sys.exit(main())
