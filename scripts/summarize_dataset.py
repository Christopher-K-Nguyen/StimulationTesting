#!/usr/bin/env python
"""Print a summary of the ingested Q_inj training dataset."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from stimtest.ml import load_dataset


def main() -> int:
    feats, targets = load_dataset()
    if not feats:
        print("Dataset is empty — run scripts/ingest_legacy_data.py first.")
        return 1
    df = pd.DataFrame([f.__dict__ for f in feats])
    df["q_inj_mc_per_cm2"] = targets

    print(f"Total observations : {len(df)}")
    print()
    print("--- Numerical summary ---")
    print(df[[
        "surface_area_um2", "phase_width_us", "interphase_delay_us",
        "discharge_delay_us", "polarity", "rate_hz", "n_returns",
        "q_inj_mc_per_cm2",
    ]].describe().round(3).to_string())
    print()
    print("--- Categorical breakdowns ---")
    for col in ("coating", "config_id", "pattern_type", "electrolyte"):
        print(f"\n{col}:")
        print(df[col].value_counts().to_string())
    print()
    print("--- Q_inj by coating + config ---")
    g = df.groupby(["coating", "config_id"])["q_inj_mc_per_cm2"]
    summary = g.agg(["count", "mean", "std", "min", "max"]).round(3)
    print(summary.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
