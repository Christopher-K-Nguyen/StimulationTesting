#!/usr/bin/env python
"""Walk a directory tree of legacy MATLAB ``.mat`` files and seed the ML CSV.

Example:
    python scripts/ingest_legacy_data.py \\
        --root "E:\\NIL\\Projects and Data\\.../ME17678_3" \\
        --root "E:\\NIL\\Projects and Data\\.../ME17678_4" \\
        --coating AIROF
    python scripts/ingest_legacy_data.py \\
        --root "E:\\NIL\\Projects and Data\\Stanford Retinal Prosthesis\\SIROF" \\
        --coating SIROF
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow `python scripts/ingest_legacy_data.py` from the project root without
# installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stimtest.ml.ingest import ingest_directory
from stimtest.ml.qinj_model import DEFAULT_DATASET_PATH


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--root", action="append", required=True,
                   help="Directory to walk recursively (repeatable)")
    p.add_argument("--coating", default=None,
                   help="Override the coating field for every observation "
                        "(e.g. AIROF, SIROF). Useful when the .mat lacks it.")
    p.add_argument("--csv", type=Path, default=DEFAULT_DATASET_PATH,
                   help=f"Output CSV (default: {DEFAULT_DATASET_PATH})")
    p.add_argument("--filter", default="VT",
                   help="Only ingest .mat files whose name contains this "
                        "substring. Default 'VT' to exclude OCP/EIS/CV.")
    p.add_argument("--parallel", type=int, default=0,
                   help="Worker processes to use for .mat parsing. "
                        "0 = sequential; -1 = auto (min(cpu_count, 8)). "
                        "Default 0.")
    args = p.parse_args()
    parallel: bool | int = (True if args.parallel == -1
                            else (False if args.parallel == 0 else args.parallel))

    total = {"files": 0, "rows_added": 0, "skipped": 0, "errors": []}
    for r in args.root:
        print(f"\n--- Walking {r} ---")
        result = ingest_directory(
            Path(r), csv_path=args.csv,
            coating_override=args.coating, name_filter=args.filter,
            parallel=parallel,
        )
        print(f"  files seen      : {result['files']}")
        print(f"  rows added      : {result['rows_added']}")
        print(f"  files w/o data  : {result['skipped']}")
        if result["errors"]:
            print(f"  errors          : {len(result['errors'])}")
            for path, err in result["errors"][:5]:
                print(f"    {path.name}: {err}")
        for k in ("files", "rows_added", "skipped"):
            total[k] += result[k]
        total["errors"].extend(result["errors"])

    print(f"\n=== TOTAL ===")
    print(f"  files seen      : {total['files']}")
    print(f"  rows appended   : {total['rows_added']}")
    print(f"  CSV file        : {args.csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
