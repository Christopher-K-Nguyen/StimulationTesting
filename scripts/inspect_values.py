"""Print the actual values of important File-struct fields."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from scipy.io import loadmat


def _v(obj, attr, default=None):
    val = getattr(obj, attr, default)
    if isinstance(val, np.ndarray):
        if val.size == 0:
            return None
        if val.size == 1:
            return val.item()
        return val.tolist() if val.size <= 6 else f"<array {val.shape}>"
    return val


def _struct_fields(obj):
    """Return field-name list for a mat_struct, else None."""
    return getattr(obj, "_fieldnames", None)


def _show_struct(name, obj, indent="  "):
    """Print the named-field values of a mat_struct."""
    fnames = _struct_fields(obj)
    if not fnames:
        print(f"{indent}{name}: {obj!r}")
        return
    print(f"{indent}{name}: struct")
    for f in fnames:
        val = getattr(obj, f, None)
        if _struct_fields(val):
            print(f"{indent}  {f}: <struct fields={_struct_fields(val)}>")
        elif isinstance(val, np.ndarray) and val.size > 8:
            print(f"{indent}  {f}: ndarray[{val.shape}]")
        else:
            print(f"{indent}  {f}: {val!r}")


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: inspect_values.py <path>")
        return 1
    path = Path(sys.argv[1])
    raw = loadmat(str(path), struct_as_record=False, squeeze_me=True)
    file_obj = next(v for k, v in raw.items() if not k.startswith("__"))

    print(f"\n=== {path.name} ===")
    print(f"Top-level type: {type(file_obj).__name__}")
    fnames = _struct_fields(file_obj)
    if not fnames:
        print(f"NOT a struct. Value: {file_obj!r}")
        return 0
    print(f"Top-level fields: {fnames}")

    P = getattr(file_obj, "Parameters", None)
    if P is None:
        # Some older files might wrap things differently
        for f in fnames:
            v = getattr(file_obj, f)
            if _struct_fields(v) and "PhaseWidth1" in _struct_fields(v):
                P = v
                print(f"(Parameters found under field {f!r})")
                break
    if P is None:
        print("No Parameters field anywhere — dumping everything:")
        for f in fnames:
            _show_struct(f, getattr(file_obj, f))
        return 0

    print("\nParameters:")
    for f in P._fieldnames:
        val = getattr(P, f)
        if _struct_fields(val):
            print(f"  {f}: struct fields={_struct_fields(val)}")
            for sub in val._fieldnames[:4]:
                print(f"    .{sub} = {getattr(val, sub)!r}")
        elif isinstance(val, np.ndarray) and val.size > 6:
            print(f"  {f}: ndarray[{val.shape}] dtype={val.dtype}")
        else:
            print(f"  {f}: {val!r}")

    T = getattr(file_obj, "Test", None)
    if T is not None and _struct_fields(T):
        print("\nTest:")
        for f in T._fieldnames:
            val = getattr(T, f)
            if isinstance(val, np.ndarray) and val.size > 6:
                print(f"  {f}: ndarray[{val.shape}]")
            else:
                print(f"  {f}: {val!r}")

    print("\nData[0..n] summary:")
    data = getattr(file_obj, "Data", None)
    if data is None:
        return 0
    if not isinstance(data, np.ndarray):
        data = np.array([data])
    for i, d in enumerate(data.flat[:8]):
        if not _struct_fields(d):
            print(f"  [{i:02d}] {d!r}")
            continue
        ac = _v(d, "ActiveChannel")
        rc = _v(d, "ReturnChannel")
        amp = _v(d, "Amplitude")
        qph = _v(d, "ChargePhase")
        qinj = _v(d, "ChargeInjection")
        sa = _v(d, "SurfaceArea")
        st = _v(d, "Status")
        cap = getattr(d, "Capture", None)
        if cap is None:
            ncap = 0
        elif isinstance(cap, np.ndarray):
            ncap = cap.size
        else:
            ncap = 1
        print(f"  [{i:02d}] active={ac} ret={rc} A={amp} Qph={qph} Qinj={qinj} "
              f"SA={sa} status={st!r} captures={ncap}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
