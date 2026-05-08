#!/usr/bin/env python
"""Quick smoke test: load a saved session, export per-channel + summary plots."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stimtest.persistence import load_session_npz
from stimtest.plotting import export_session_plots, export_session_summary_plots


def main() -> int:
    npz = Path("data") / "vt_run.npz"
    if not npz.exists():
        print(f"Run scripts/run_cli.py first to create {npz}.")
        return 1
    print(f"Loading {npz} ...")
    session = load_session_npz(npz)
    print(f"  runs           : {len(session.runs)}")
    print(f"  total captures : {session.total_captures}")

    out = Path("data") / "vt_run_plots"
    print(f"\nExporting per-channel plots to {out}/ ...")
    paths = export_session_plots(session, out, fmt="png", dpi=150)
    print(f"  {len(paths)} plot(s) written")

    print(f"\nExporting summary plots ...")
    paths += export_session_summary_plots(session, out, fmt="png", dpi=150)
    for p in paths[-2:]:
        print(f"  {p.name}")
    print(f"\nDone. Open {out}/ to inspect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
