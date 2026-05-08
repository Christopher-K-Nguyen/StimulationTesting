#!/usr/bin/env python
"""Benchmark sequential vs parallel for the two parallel-friendly tasks."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def bench_plot_export():
    from stimtest.persistence import load_session_npz
    from stimtest.plotting import export_session_plots

    npz = Path("data") / "vt_run.npz"
    if not npz.exists():
        print("Need data/vt_run.npz; run run_cli.py --simulate first.")
        return
    session = load_session_npz(npz)
    n_caps = session.total_captures
    print(f"\n=== Plot export benchmark ({n_caps} captures) ===")

    out = Path("data") / "_bench_plots"
    for label, parallel in [("sequential", False), ("parallel(auto)", True)]:
        if out.exists():
            shutil.rmtree(out)
        t0 = time.time()
        paths = export_session_plots(session, out, fmt="png", dpi=100,
                                     every_capture=True, parallel=parallel)
        dt = time.time() - t0
        print(f"  {label:>16}: {dt:6.2f} s   ({len(paths)} files, "
              f"{dt / max(len(paths), 1) * 1000:.1f} ms/plot)")


def bench_ingest():
    from stimtest.ml.ingest import ingest_directory

    root = Path(r"E:\NIL\Projects and Data\0_ Personal Folders"
                r"\Christopher Nguyen\Projects"
                r"\03_Microprobes_FMA_Thomas\ME17678_3")
    if not root.exists():
        print(f"\nSkipping ingest benchmark — {root} not present.")
        return
    print(f"\n=== Legacy .mat ingest benchmark ===")

    csv = Path("data") / "_bench_qinj_dataset.csv"
    for label, parallel in [("sequential", False), ("parallel(auto)", True)]:
        if csv.exists():
            csv.unlink()
        t0 = time.time()
        result = ingest_directory(root, csv_path=csv, coating_override="AIROF",
                                   parallel=parallel)
        dt = time.time() - t0
        print(f"  {label:>16}: {dt:6.2f} s   "
              f"({result['files']} files, {result['rows_added']} rows)")
    if csv.exists():
        csv.unlink()


def main() -> int:
    bench_plot_export()
    bench_ingest()
    return 0


if __name__ == "__main__":
    sys.exit(main())
