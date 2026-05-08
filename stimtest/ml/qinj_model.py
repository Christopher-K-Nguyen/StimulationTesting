"""Maximum charge-injection prediction.

A small machine-learning helper that predicts the maximum injectable charge
density (``Q_inj``, mC/cm²) for a given electrode + waveform + configuration
combination, based on prior measurements collected in your lab.

Workflow
--------
1. Run a few real (or simulated) characterization sweeps; each sweep ends with
   a measured ``max_q_inj``. Use :func:`record_session` to append these to a
   CSV training set.
2. Call :meth:`QinjPredictor.fit_from_csv` once you have ~20+ rows.
3. From the GUI/CLI, call :meth:`QinjPredictor.predict` with the *intended*
   experiment parameters to get a predicted Q_inj and uncertainty band — handy
   for picking a starting current that's already near the limit (saves time
   during a sweep).

The features deliberately stay close to what experimenters control: coating,
geometric surface area, electrolyte, phase widths, polarity, pattern type, and
configuration. Adding more features later (temperature, age, pulse rate) is
just a matter of widening the dataclass and the encoder.

Why a Gradient Boosting model?
------------------------------
With 20–500 training rows and a mix of categorical + continuous features, a
shallow GBM is the right default. It handles non-linearities (e.g. Q_inj
saturation at large GSA) without needing manual feature scaling, gives a
useful uncertainty estimate via per-tree variance, and trains in milliseconds.
"""
from __future__ import annotations

import csv
import json
import math
import pickle
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

try:
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.preprocessing import OneHotEncoder
    from sklearn.compose import ColumnTransformer
    from sklearn.pipeline import Pipeline
    _HAS_SKLEARN = True
except Exception:  # pragma: no cover
    _HAS_SKLEARN = False

from ..electrode import Configuration
from ..session import ChannelRun, Session
from ..waveforms import PulsePattern


# ---------------------------------------------------------------------------
# Feature schema
# ---------------------------------------------------------------------------
CATEGORICAL_FIELDS = ("coating", "electrolyte", "pattern_type", "config_id")
NUMERIC_FIELDS = (
    "surface_area_um2", "phase_width_us", "interphase_delay_us",
    "discharge_delay_us", "polarity", "n_returns", "rate_hz",
)


@dataclass
class QinjFeatures:
    """Inputs to the predictor — everything the experimenter sets up front."""
    coating: str = "SIROF"           # 'SIROF' | 'AIROF' | 'PtIr' | 'Au' | ...
    surface_area_um2: float = 5000.0
    electrolyte: str = "PBS"         # 'PBS' | 'cortex' | 'saline' | ...
    phase_width_us: float = 200.0
    interphase_delay_us: float = 20.0
    discharge_delay_us: float = 20.0
    polarity: int = -1               # -1 cathodic-first, +1 anodic-first
    pattern_type: str = "biphasic"   # 'biphasic' | 'triphasic' | 'monophasic'
    config_id: str = "MP"            # 'MP' | 'BP' | 'TP' | 'PBP' | 'PTP' | 'CG'
    n_returns: int = 0
    rate_hz: float = 50.0

    @classmethod
    def from_pattern(cls, pattern: PulsePattern, configuration: Configuration,
                     coating: str = "SIROF", electrolyte: str = "PBS",
                     surface_area_um2: float = 5000.0) -> "QinjFeatures":
        """Build a feature row from a live ``PulsePattern`` + ``Configuration``."""
        if pattern.num_phases == 3:
            ptype = "triphasic"
        elif pattern.num_phases == 1:
            ptype = "monophasic"
        else:
            ptype = "biphasic"
        # Use the first phase to read widths and the trailing phase's delay
        # as the discharge delay. Interphase = delay after first phase.
        first = pattern.phases[0]
        last = pattern.phases[-1]
        return cls(
            coating=coating,
            surface_area_um2=surface_area_um2,
            electrolyte=electrolyte,
            phase_width_us=first.width_us,
            interphase_delay_us=first.delay_after_us,
            discharge_delay_us=last.delay_after_us,
            polarity=pattern.polarity,
            pattern_type=ptype,
            config_id=configuration.id,
            n_returns=configuration.num_returns,
            rate_hz=pattern.rate_hz,
        )

    def to_row(self) -> dict:
        return asdict(self)


def features_from_run(session: Session, run: ChannelRun,
                      coating: str = "SIROF",
                      electrolyte: str = "PBS") -> QinjFeatures:
    """Reconstruct the feature row from a completed session run."""
    return QinjFeatures.from_pattern(
        session.test.pattern, run.configuration,
        coating=coating, electrolyte=electrolyte,
        surface_area_um2=run.surface_area_um2,
    )


# ---------------------------------------------------------------------------
# Dataset I/O — a flat CSV with one observation per finished sweep
# ---------------------------------------------------------------------------
DEFAULT_DATASET_PATH = Path("data") / "qinj_dataset.csv"


def record_observation(features: QinjFeatures, q_inj_mc_per_cm2: float,
                       *, path: Path | str = DEFAULT_DATASET_PATH,
                       notes: str = "") -> Path:
    """Append a single (features, q_inj) observation to the training CSV.

    Creates the file with a header row on first call. Does *not* refit the
    model — call :meth:`QinjPredictor.fit_from_csv` once you've added a batch.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    row = features.to_row()
    row["q_inj_mc_per_cm2"] = float(q_inj_mc_per_cm2)
    row["notes"] = notes
    fieldnames = list(row.keys())
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            w.writeheader()
        w.writerow(row)
    return path


def record_session(session: Session, *, path: Path | str = DEFAULT_DATASET_PATH,
                   coating: str = "SIROF",
                   electrolyte: str = "PBS",
                   notes: str = "") -> int:
    """Record every finished run in a session. Returns count appended."""
    n = 0
    for run in session.runs:
        q = run.max_q_inj
        if not math.isfinite(q) or q <= 0:
            continue
        feats = features_from_run(session, run, coating=coating,
                                  electrolyte=electrolyte)
        record_observation(feats, q, path=path,
                           notes=f"{notes} session={session.name}".strip())
        n += 1
    return n


def load_dataset(path: Path | str = DEFAULT_DATASET_PATH
                 ) -> Tuple[List[QinjFeatures], np.ndarray]:
    """Read the training CSV back into memory."""
    path = Path(path)
    feats: List[QinjFeatures] = []
    targets: List[float] = []
    if not path.exists():
        return feats, np.zeros(0)
    valid_keys = {f.name for f in fields(QinjFeatures)}
    with path.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                kwargs = {}
                for k in valid_keys:
                    if k not in row:
                        continue
                    v = row[k]
                    # Cast numeric fields
                    if k in NUMERIC_FIELDS:
                        kwargs[k] = float(v) if k != "n_returns" and k != "polarity" else int(float(v))
                    else:
                        kwargs[k] = v
                feats.append(QinjFeatures(**kwargs))
                targets.append(float(row["q_inj_mc_per_cm2"]))
            except (ValueError, KeyError):
                continue
    return feats, np.asarray(targets, dtype=float)


# ---------------------------------------------------------------------------
# Predictor
# ---------------------------------------------------------------------------
@dataclass
class PredictionResult:
    """Output of :meth:`QinjPredictor.predict`."""
    q_inj_predicted_mc_per_cm2: float
    q_inj_std_mc_per_cm2: float
    q_inj_low_mc_per_cm2: float        # 5th percentile across trees
    q_inj_high_mc_per_cm2: float       # 95th percentile across trees
    n_training_rows: int
    is_extrapolation: bool             # True when category never seen before


class QinjPredictor:
    """Wraps a sklearn pipeline (one-hot + GBM) with simple persistence.

    Notes
    -----
    The uncertainty estimate is the per-tree spread of a GradientBoosting
    ensemble. It is **not** a calibrated confidence interval — treat it as a
    relative measure of model agreement: a wide band means "I haven't seen
    this combination very often, take the prediction with a grain of salt."
    """

    def __init__(self, n_estimators: int = 200, max_depth: int = 3,
                 learning_rate: float = 0.05, random_state: int = 0):
        if not _HAS_SKLEARN:
            raise ImportError("scikit-learn is required for QinjPredictor")
        self._cat_fields = list(CATEGORICAL_FIELDS)
        self._num_fields = list(NUMERIC_FIELDS)
        # OneHotEncoder API differs across sklearn versions; handle both.
        try:
            ohe = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
        except TypeError:  # sklearn < 1.2
            ohe = OneHotEncoder(handle_unknown="ignore", sparse=False)
        # ColumnTransformer requires *integer* column indices when fed a
        # plain numpy ndarray. Categorical columns are written first by
        # _features_to_matrix(), numeric columns after.
        n_cat = len(self._cat_fields)
        self._encoder = ColumnTransformer(
            [("cat", ohe, list(range(n_cat)))],
            remainder="passthrough",
        )
        self._model = GradientBoostingRegressor(
            n_estimators=n_estimators,
            max_depth=max_depth,
            learning_rate=learning_rate,
            random_state=random_state,
        )
        self._pipeline = Pipeline([
            ("encoder", self._encoder),
            ("model", self._model),
        ])
        self._is_fit = False
        self._n_train = 0
        self._seen_categories: dict[str, set[str]] = {f: set() for f in self._cat_fields}

    # ----- training ------------------------------------------------------
    def _features_to_matrix(self, feats: Sequence[QinjFeatures]) -> np.ndarray:
        rows = []
        for f in feats:
            row = []
            for k in self._cat_fields:
                row.append(getattr(f, k))
            for k in self._num_fields:
                row.append(float(getattr(f, k)))
            rows.append(row)
        return np.asarray(rows, dtype=object)

    def fit(self, feats: Sequence[QinjFeatures], targets: Sequence[float]) -> "QinjPredictor":
        if len(feats) < 3:
            raise ValueError(f"Need at least 3 observations to fit (got {len(feats)})")
        X = self._features_to_matrix(feats)
        y = np.asarray(targets, dtype=float)
        self._pipeline.fit(X, y)
        self._is_fit = True
        self._n_train = len(feats)
        # Remember which categorical values we've actually trained on
        for k in self._cat_fields:
            self._seen_categories[k] = {getattr(f, k) for f in feats}
        return self

    def fit_from_csv(self, path: Path | str = DEFAULT_DATASET_PATH) -> "QinjPredictor":
        feats, targets = load_dataset(path)
        if not feats:
            raise FileNotFoundError(f"No observations in {path}")
        return self.fit(feats, targets)

    # ----- inference -----------------------------------------------------
    def predict(self, features: QinjFeatures) -> PredictionResult:
        if not self._is_fit:
            return PredictionResult(
                float("nan"), float("nan"), float("nan"), float("nan"),
                self._n_train, is_extrapolation=True,
            )
        X = self._features_to_matrix([features])
        # GBM is *additive* — individual tree outputs don't sum to the total
        # in a useful way, so we use the *staged* cumulative predictions over
        # boosting rounds for the uncertainty band. The final element is the
        # model's full prediction; the spread across the last ~25 % of rounds
        # is a poor-man's confidence band ("does the prediction still wobble
        # near the end of training?").
        X_enc = self._pipeline.named_steps["encoder"].transform(X)
        staged = np.fromiter(
            (p[0] for p in self._model.staged_predict(X_enc)),
            dtype=float,
        )
        mean = float(staged[-1])
        # Use spread across the final 25% of boosting rounds
        tail = staged[-max(5, len(staged) // 4):]
        std = float(np.std(tail))
        low = float(np.percentile(tail, 5))
        high = float(np.percentile(tail, 95))
        # Detect categorical extrapolation
        extrapolating = any(
            getattr(features, k) not in self._seen_categories[k]
            for k in self._cat_fields
        )
        return PredictionResult(
            q_inj_predicted_mc_per_cm2=mean,
            q_inj_std_mc_per_cm2=std,
            q_inj_low_mc_per_cm2=low,
            q_inj_high_mc_per_cm2=high,
            n_training_rows=self._n_train,
            is_extrapolation=extrapolating,
        )

    # ----- persistence ---------------------------------------------------
    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as f:
            pickle.dump({
                "pipeline": self._pipeline,
                "is_fit": self._is_fit,
                "n_train": self._n_train,
                "seen_categories": {k: list(v) for k, v in self._seen_categories.items()},
            }, f)
        return path

    @classmethod
    def load(cls, path: Path | str) -> "QinjPredictor":
        path = Path(path)
        with path.open("rb") as f:
            blob = pickle.load(f)
        obj = cls()
        obj._pipeline = blob["pipeline"]
        obj._is_fit = blob["is_fit"]
        obj._n_train = blob["n_train"]
        obj._seen_categories = {k: set(v) for k, v in blob["seen_categories"].items()}
        # Recover model handle for staged_predict access
        obj._model = obj._pipeline.named_steps["model"]
        obj._encoder = obj._pipeline.named_steps["encoder"]
        return obj
