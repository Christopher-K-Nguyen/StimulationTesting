"""Quick inspector for the MATLAB File-struct .mat files.

Run as: python scripts/inspect_mat.py <path-to-mat>

Prints the top-level structure so we can see how Parameters, Data, Capture,
ChargeInjection etc. are nested.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import numpy as np
from scipy.io import loadmat


def _summarize(obj: Any, depth: int = 0, max_depth: int = 4, prefix: str = "") -> None:
    pad = "  " * depth
    if depth > max_depth:
        print(f"{pad}{prefix}…(truncated)")
        return
    if isinstance(obj, np.ndarray):
        if obj.dtype.names:                            # struct array
            print(f"{pad}{prefix}struct[{obj.shape}] fields={list(obj.dtype.names)}")
            if obj.size <= 4 and depth < max_depth:
                for i, x in enumerate(obj.flat):
                    if isinstance(x, np.ndarray) and x.dtype.names:
                        print(f"{pad}  [{i}]")
                        for name in x.dtype.names:
                            try:
                                _summarize(x[name], depth + 2, max_depth, f"{name}: ")
                            except Exception as e:
                                print(f"{pad}    {name}: <err {e}>")
        elif obj.dtype == object:
            print(f"{pad}{prefix}object[{obj.shape}]")
            if obj.size and obj.size <= 4 and depth < max_depth:
                for i, x in enumerate(obj.flat):
                    _summarize(x, depth + 1, max_depth, f"[{i}] ")
        else:
            shape_str = "x".join(str(d) for d in obj.shape) or "scalar"
            try:
                preview = str(obj).strip()[:80]
            except Exception:
                preview = ""
            print(f"{pad}{prefix}ndarray[{shape_str}] dtype={obj.dtype} {preview!r}")
    elif isinstance(obj, dict):
        print(f"{pad}{prefix}dict keys={list(obj.keys())}")
        for k, v in obj.items():
            if k.startswith("__"):
                continue
            _summarize(v, depth + 1, max_depth, f"{k}: ")
    else:
        print(f"{pad}{prefix}{type(obj).__name__}: {obj!r:.80s}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: inspect_mat.py <path>")
        return 1
    path = Path(sys.argv[1])
    print(f"\n=== {path.name} ===")
    raw = loadmat(str(path), struct_as_record=False, squeeze_me=True)
    for k, v in raw.items():
        if k.startswith("__"):
            continue
        print(f"\n# Top-level: {k}")
        # struct_as_record=False returns mat_struct objects; convert to dict view
        try:
            from scipy.io.matlab.mio5_params import mat_struct
            if isinstance(v, mat_struct):
                print(f"  fields: {v._fieldnames}")
                for fname in v._fieldnames:
                    val = getattr(v, fname)
                    if isinstance(val, mat_struct):
                        print(f"    {fname}: struct fields={val._fieldnames}")
                    elif isinstance(val, np.ndarray):
                        if val.dtype == object and val.size and isinstance(val.flat[0], mat_struct):
                            inner = val.flat[0]
                            print(f"    {fname}: array of struct[{val.shape}] inner_fields={inner._fieldnames}")
                        else:
                            print(f"    {fname}: ndarray[{val.shape}] dtype={val.dtype}")
                    else:
                        print(f"    {fname}: {type(val).__name__} = {str(val)[:60]!r}")
                continue
        except Exception:
            pass
        _summarize(v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
