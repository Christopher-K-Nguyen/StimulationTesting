#!/usr/bin/env python
"""Train the Q_inj predictor and evaluate it on a held-out test set.

Run after ``scripts/ingest_legacy_data.py``. Saves the trained model to
``data/qinj_model.pkl`` so the GUI / CLI can reload it.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split

from stimtest.ml import QinjPredictor, load_dataset


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=Path("data") / "qinj_model.pkl")
    p.add_argument("--test-size", type=float, default=0.2)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    feats, targets = load_dataset()
    if not feats:
        print("Dataset empty — run scripts/ingest_legacy_data.py first.")
        return 1
    print(f"Loaded {len(feats)} observations")

    f_train, f_test, y_train, y_test = train_test_split(
        feats, targets, test_size=args.test_size, random_state=args.seed,
    )
    print(f"Train: {len(f_train)}  Test: {len(f_test)}")

    pred = QinjPredictor(n_estimators=300, max_depth=4, learning_rate=0.05)
    pred.fit(f_train, y_train)

    # Evaluate
    y_hat = np.array([pred.predict(f).q_inj_predicted_mc_per_cm2 for f in f_test])
    mae = mean_absolute_error(y_test, y_hat)
    r2 = r2_score(y_test, y_hat)
    print(f"\nHeld-out performance:")
    print(f"  MAE  : {mae:.3f} mC/cm²  (target std = {np.std(y_test):.3f})")
    print(f"  R²   : {r2:.3f}")
    print(f"  bias : {(y_hat - y_test).mean():+.3f} mC/cm²")

    # Per-config breakdown
    df = pd.DataFrame([f.__dict__ for f in f_test])
    df["actual"] = y_test
    df["predicted"] = y_hat
    df["abs_err"] = np.abs(y_hat - y_test)
    print("\nMAE by config:")
    print(df.groupby("config_id")["abs_err"].agg(["count", "mean"]).round(3).to_string())

    # Save the model fitted on the FULL dataset (not just train)
    pred_full = QinjPredictor(n_estimators=300, max_depth=4, learning_rate=0.05)
    pred_full.fit(feats, targets)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    pred_full.save(args.out)
    print(f"\nFinal model trained on all {len(feats)} rows and saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
