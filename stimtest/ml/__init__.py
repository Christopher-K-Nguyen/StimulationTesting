"""Machine-learning helpers for predicting maximum charge-injection capacity.

Builds on top of :mod:`stimtest.session` to record measurement outcomes and
fit a small regression model that can predict the maximum injectable charge
density (``Q_inj``) from electrode + waveform parameters before a sweep is
run. Useful for picking a starting current that's already close to the
limit, which shortens characterization time considerably.
"""
from .qinj_model import (
    QinjFeatures, QinjPredictor, PredictionResult,
    DEFAULT_DATASET_PATH,
    record_observation, record_session, load_dataset,
    features_from_run,
)
from .weakness_analysis import (
    DesignFeatures, TargetSet, WeaknessReport,
    analyze_folder, report_to_json, report_to_text,
)

__all__ = [
    "QinjFeatures", "QinjPredictor", "PredictionResult",
    "DEFAULT_DATASET_PATH",
    "record_observation", "record_session", "load_dataset",
    "features_from_run",
    "DesignFeatures", "TargetSet", "WeaknessReport",
    "analyze_folder", "report_to_json", "report_to_text",
]
