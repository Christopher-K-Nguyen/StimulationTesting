"""Tests for :mod:`stimtest.ml.qinj_model`.

Audit-driven coverage. The original `features_from_run` Configuration-
vs-ChannelRun confusion (audit finding #4) and the
`load_default`/frozen-build-aware path resolver (#5) had zero test
coverage; both were caught in production by hand-tracing. These tests
pin the call signatures, the dataclass field names downstream code
reads, and the load-default fallback order so a future refactor
can't silently regress either.
"""
from __future__ import annotations

import csv
import sys
import tempfile
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

import pytest

from stimtest.electrode import Configuration
from stimtest.ml import features_from_run
from stimtest.ml.qinj_model import (
    DEFAULT_DATASET_PATH, DEFAULT_MODEL_PATH,
    PredictionResult, QinjFeatures, QinjPredictor,
    _resolve_data_dir,
)
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


# ---------------------------------------------------------------------------
# QinjFeatures
# ---------------------------------------------------------------------------
def test_qinjfeatures_from_pattern_reads_canonical_fields():
    """Construction from a (pattern, configuration) tuple populates the
    feature row with the canonical attribute names the predictor and
    its callers expect. Regression for audit finding #4 — earlier
    callers passed a ``Configuration`` to ``features_from_run`` which
    silently AttributeError'd on ``run.configuration``."""
    pat = PulsePattern.biphasic(amplitude_ua=100.0, phase_width_us=200.0,
                                 rate_hz=50.0, polarity=-1)
    cfg = Configuration(id="ch5_mp", active=5, returns=(6,))
    feats = QinjFeatures.from_pattern(
        pat, cfg, coating="SIROF", surface_area_um2=5000.0,
    )
    assert feats.coating == "SIROF"
    assert feats.surface_area_um2 == 5000.0
    assert feats.rate_hz == 50.0
    assert feats.phase_width_us == 200.0
    assert feats.config_id == "ch5_mp"
    assert feats.n_returns == 1
    assert feats.pattern_type == "biphasic"


def test_qinjfeatures_pattern_type_detection():
    """The ``pattern_type`` one-hot rolls up by phase count —
    1 phase → monophasic, 2 → biphasic, 3 → triphasic. Build each
    case explicitly via ``Phase`` so the test exercises the
    phase-count branching in :meth:`from_pattern`, not a particular
    constructor's default."""
    from stimtest.waveforms import Phase, SHAPE_RECTANGULAR
    cfg = Configuration(id="ch1_mp", active=1, returns=())
    mono = PulsePattern(phases=[
        Phase(amplitude_ua=-100.0, width_us=200.0, shape=SHAPE_RECTANGULAR),
    ], rate_hz=50.0)
    assert QinjFeatures.from_pattern(mono, cfg).pattern_type == "monophasic"
    bi = PulsePattern.biphasic(amplitude_ua=100.0, phase_width_us=200.0,
                                rate_hz=50.0, polarity=-1)
    assert QinjFeatures.from_pattern(bi, cfg).pattern_type == "biphasic"
    tri = PulsePattern.triphasic(amp_excite_ua=100.0, phase_width_us=200.0,
                                   rate_hz=50.0)
    assert QinjFeatures.from_pattern(tri, cfg).pattern_type == "triphasic"


# ---------------------------------------------------------------------------
# features_from_run — the helper VT's predictive ramp used to mis-call
# ---------------------------------------------------------------------------
def test_features_from_run_requires_channelrun():
    """``features_from_run`` reads ``run.configuration`` and
    ``run.surface_area_um2``. Passing a bare ``Configuration`` (the
    audit-#4 mis-call) must raise AttributeError, NOT silently
    return junk — so callers' try/except can detect the misuse."""
    from stimtest.electrode import ElectrodeArray, ElectrodePosition
    pat = PulsePattern.biphasic(amplitude_ua=100.0, phase_width_us=200.0,
                                 rate_hz=50.0, polarity=-1)
    cfg = Configuration(id="ch5_mp", active=5, returns=(6,))
    sites = [ElectrodePosition(number=1, row=0, col=0,
                                surface_area_um2=5000.0, coating="SIROF")]
    array = ElectrodeArray(name="t", rows=1, cols=1, sites=sites)
    test = TestParameters(experiment="VT", pattern=pat, configuration=cfg,
                          array=array)
    session = Session(notebook="t", subject="s", test=test)
    # Passing the Configuration where a ChannelRun is expected → AttributeError
    with pytest.raises(AttributeError):
        features_from_run(session, cfg)


def test_features_from_run_with_channelrun_succeeds():
    """The correct invocation (session + a real ChannelRun) returns
    a populated QinjFeatures row."""
    from stimtest.electrode import ElectrodeArray, ElectrodePosition
    from stimtest.session import ChannelRun
    pat = PulsePattern.biphasic(amplitude_ua=100.0, phase_width_us=200.0,
                                 rate_hz=50.0, polarity=-1)
    cfg = Configuration(id="ch5_mp", active=5, returns=(6,))
    sites = [ElectrodePosition(number=1, row=0, col=0,
                                surface_area_um2=5000.0, coating="SIROF")]
    array = ElectrodeArray(name="t", rows=1, cols=1, sites=sites)
    test = TestParameters(experiment="VT", pattern=pat, configuration=cfg,
                          array=array)
    session = Session(notebook="t", subject="s", test=test)
    run = ChannelRun(configuration=cfg, surface_area_um2=5000.0)
    feats = features_from_run(session, run, coating="SIROF",
                               electrolyte="PBS")
    assert feats.coating == "SIROF"
    assert feats.electrolyte == "PBS"
    assert feats.surface_area_um2 == 5000.0
    assert feats.config_id == "ch5_mp"


# ---------------------------------------------------------------------------
# PredictionResult — attribute names the runner reads
# ---------------------------------------------------------------------------
def test_predictionresult_has_canonical_field_names():
    """The runner reads ``q_inj_predicted_mc_per_cm2`` (NOT the
    other way round — audit finding #4 had a stacked AttributeError
    on this exact name). Pin the dataclass schema here so a future
    rename is caught at test time, not silently inside a try/except
    in the runner."""
    expected = {
        "q_inj_predicted_mc_per_cm2",
        "q_inj_std_mc_per_cm2",
        "q_inj_low_mc_per_cm2",
        "q_inj_high_mc_per_cm2",
        "n_training_rows",
        "is_extrapolation",
    }
    actual = {f.name for f in fields(PredictionResult)}
    assert actual == expected


def test_unfit_predictor_returns_nan_extrapolation():
    """An unfit predictor must still return a ``PredictionResult``
    rather than raise — the runner relies on this so a bundle
    without a trained pickle falls back to regression instead of
    crashing on Start."""
    feats = QinjFeatures.from_pattern(
        PulsePattern.biphasic(amplitude_ua=100.0, phase_width_us=200.0,
                               rate_hz=50.0, polarity=-1),
        Configuration(id="ch5_mp", active=5, returns=(6,)),
        coating="SIROF", surface_area_um2=5000.0,
    )
    pred = QinjPredictor()
    result = pred.predict(feats)
    assert isinstance(result, PredictionResult)
    import math
    assert math.isnan(result.q_inj_predicted_mc_per_cm2)
    assert result.is_extrapolation is True


# ---------------------------------------------------------------------------
# Data-path resolution (audit #5)
# ---------------------------------------------------------------------------
def test_resolve_data_dir_returns_absolute_path():
    """The default data dir is an absolute path, not the broken
    ``Path("data")`` (process-CWD-relative) form. Regression for
    audit #5."""
    d = _resolve_data_dir()
    assert d.is_absolute()


def test_default_dataset_path_is_absolute():
    """Same property for the public constants the codebase + dev
    tools (record_observation, scripts/train_qinj_model.py)
    consume."""
    assert DEFAULT_DATASET_PATH.is_absolute()
    assert DEFAULT_MODEL_PATH.is_absolute()
    assert DEFAULT_DATASET_PATH.name == "qinj_dataset.csv"
    assert DEFAULT_MODEL_PATH.name == "qinj_model.pkl"


def test_resolve_data_dir_prefers_meipass_when_frozen(tmp_path):
    """In a frozen build (sys._MEIPASS set) the resolver returns
    ``_MEIPASS / "data"`` so PyInstaller-bundled assets are
    findable. Simulate the frozen state with a tmp dir + monkeypatch
    so the test runs against real disk layout without needing an
    actual frozen build."""
    meipass = tmp_path / "frozen_root"
    data_dir = meipass / "data"
    data_dir.mkdir(parents=True)
    # Drop a stub file so the resolver's ``is_dir`` check passes.
    (data_dir / "qinj_dataset.csv").touch()

    # ``_resolve_data_dir`` reads ``sys._MEIPASS`` via ``getattr``;
    # ``patch.object`` is the clean way to inject it for one test
    # without leaking into others.
    with patch.object(sys, "_MEIPASS", str(meipass), create=True):
        resolved = _resolve_data_dir()
    assert resolved == data_dir


# ---------------------------------------------------------------------------
# QinjPredictor.load_default — the runner's entry point (audit #5)
# ---------------------------------------------------------------------------
def test_load_default_returns_none_when_nothing_available(monkeypatch,
                                                          tmp_path):
    """No pickle, no dataset → returns None (runner falls back to
    adaptive regression)."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    monkeypatch.setattr("stimtest.ml.qinj_model.DEFAULT_MODEL_PATH",
                         empty_dir / "qinj_model.pkl")
    monkeypatch.setattr("stimtest.ml.qinj_model.DEFAULT_DATASET_PATH",
                         empty_dir / "qinj_dataset.csv")
    assert QinjPredictor.load_default() is None


def test_load_default_prefers_pickle_over_csv(monkeypatch, tmp_path):
    """When both a pre-fit pickle AND a CSV dataset are available,
    ``load_default`` MUST pick the pickle — fitting from CSV pays
    the GBM-training cost on every Start which is what the audit
    flagged as wasteful."""
    pkl = tmp_path / "qinj_model.pkl"
    csv_p = tmp_path / "qinj_dataset.csv"
    # Build a minimal trained predictor and save it as the pickle.
    # The fit() method requires ≥3 observations; vary surface area
    # so the GBM has gradient to learn against.
    feats = [
        QinjFeatures(coating="SIROF", surface_area_um2=5000.0,
                     electrolyte="PBS", phase_width_us=200.0,
                     interphase_delay_us=20.0, discharge_delay_us=20.0,
                     polarity=-1, pattern_type="biphasic",
                     config_id="ch1_mp", n_returns=1, rate_hz=50.0),
        QinjFeatures(coating="SIROF", surface_area_um2=7500.0,
                     electrolyte="PBS", phase_width_us=200.0,
                     interphase_delay_us=20.0, discharge_delay_us=20.0,
                     polarity=-1, pattern_type="biphasic",
                     config_id="ch1_mp", n_returns=1, rate_hz=50.0),
        QinjFeatures(coating="SIROF", surface_area_um2=10000.0,
                     electrolyte="PBS", phase_width_us=200.0,
                     interphase_delay_us=20.0, discharge_delay_us=20.0,
                     polarity=-1, pattern_type="biphasic",
                     config_id="ch1_mp", n_returns=1, rate_hz=50.0),
    ]
    fitted = QinjPredictor().fit(feats, [0.3, 0.4, 0.5])
    fitted.save(pkl)
    # Drop a CSV with a DIFFERENT signal so we can tell which path
    # was used. The CSV has only the header; if load_default
    # picked CSV over the pickle it'd raise FileNotFoundError on
    # the empty CSV. If it picks pickle (correct), the predictor
    # comes back fit.
    csv_p.write_text("coating,surface_area_um2,electrolyte,phase_width_us,"
                      "interphase_delay_us,discharge_delay_us,polarity,"
                      "pattern_type,config_id,n_returns,rate_hz,"
                      "q_inj_mc_per_cm2,notes\n", encoding="utf-8")
    monkeypatch.setattr("stimtest.ml.qinj_model.DEFAULT_MODEL_PATH", pkl)
    monkeypatch.setattr("stimtest.ml.qinj_model.DEFAULT_DATASET_PATH", csv_p)

    loaded = QinjPredictor.load_default()
    assert loaded is not None
    # If we picked the pickle, the predictor's pipeline matches the
    # fitted one. ``predict`` should return finite values.
    test_feat = feats[0]
    result = loaded.predict(test_feat)
    import math
    assert math.isfinite(result.q_inj_predicted_mc_per_cm2)


def test_load_default_falls_back_to_csv_when_no_pickle(monkeypatch,
                                                       tmp_path):
    """Pickle absent, CSV present → fit from CSV (the dev-checkout
    path). Verifies the second branch of the load_default fallback
    order."""
    csv_p = tmp_path / "qinj_dataset.csv"
    # Write a minimal valid dataset — header + 2 rows
    with csv_p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["coating", "surface_area_um2", "electrolyte",
                    "phase_width_us", "interphase_delay_us",
                    "discharge_delay_us", "polarity", "pattern_type",
                    "config_id", "n_returns", "rate_hz",
                    "q_inj_mc_per_cm2", "notes"])
        w.writerow(["SIROF", "5000.0", "PBS", "200.0", "20.0", "20.0",
                    "-1", "biphasic", "ch1_mp", "1", "50.0",
                    "0.3", ""])
        w.writerow(["SIROF", "7500.0", "PBS", "200.0", "20.0", "20.0",
                    "-1", "biphasic", "ch1_mp", "1", "50.0",
                    "0.4", ""])
        w.writerow(["SIROF", "10000.0", "PBS", "200.0", "20.0", "20.0",
                    "-1", "biphasic", "ch1_mp", "1", "50.0",
                    "0.5", ""])
    # No pickle on disk.
    monkeypatch.setattr("stimtest.ml.qinj_model.DEFAULT_MODEL_PATH",
                         tmp_path / "nonexistent.pkl")
    monkeypatch.setattr("stimtest.ml.qinj_model.DEFAULT_DATASET_PATH",
                         csv_p)
    loaded = QinjPredictor.load_default()
    assert loaded is not None
    # Returned predictor must produce finite predictions.
    feats = QinjFeatures(coating="SIROF", surface_area_um2=5000.0,
                          electrolyte="PBS", phase_width_us=200.0,
                          interphase_delay_us=20.0, discharge_delay_us=20.0,
                          polarity=-1, pattern_type="biphasic",
                          config_id="ch1_mp", n_returns=1, rate_hz=50.0)
    result = loaded.predict(feats)
    import math
    assert math.isfinite(result.q_inj_predicted_mc_per_cm2)


def test_load_default_handles_corrupt_pickle(monkeypatch, tmp_path):
    """A corrupt pickle must NOT propagate the unpickle error — the
    runner relies on ``load_default`` returning None gracefully.
    Falls through to the CSV-or-None path."""
    corrupt_pkl = tmp_path / "qinj_model.pkl"
    corrupt_pkl.write_bytes(b"not a real pickle, just bytes")
    # No CSV either — should land at None.
    monkeypatch.setattr("stimtest.ml.qinj_model.DEFAULT_MODEL_PATH",
                         corrupt_pkl)
    monkeypatch.setattr("stimtest.ml.qinj_model.DEFAULT_DATASET_PATH",
                         tmp_path / "nonexistent.csv")
    loaded = QinjPredictor.load_default()
    assert loaded is None
