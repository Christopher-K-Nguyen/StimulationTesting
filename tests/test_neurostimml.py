"""Tests for the local NeurostimML inference module.

Covers:

1. **Path resolution** obeys ``STIMTEST_PREFS_DIR``.
2. **No-model path** — every entry point returns ``None`` /
   ``model_not_installed`` cleanly when the pickle isn't present.
3. **Waveform one-hot encoding** matches the upstream
   ``randomForest_training.py`` column order.
4. **Feature-vector builder** produces the right 16-element row
   shape with paper-matching units.
5. **Inference adapter** integrates with a fake stub model.
6. **Installer** writes / overwrites / removes the file atomically.

The tests never download the real upstream pickle (the sandbox would
block it); we substitute a fake model that mimics scikit-learn's
``predict_proba`` API. This validates the call shape + unit handling
without requiring the actual training data.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Module-level fake-model classes
# ---------------------------------------------------------------------------
# Joblib's pickler walks the import graph to find the class definition;
# nested-in-test-function classes can't be pickled because their
# ``__qualname__`` references a closure path that doesn't survive
# import. Defining the stubs here keeps the pickle round-trip clean
# while still letting individual tests focus on specific behaviour.

class _StubModelEcho:
    """``predict_proba`` returns ``[1-x[0], x[0]]`` per row.

    Lets a test verify both the ``likely_safe`` (column 0 = 0)
    and ``likely_damaging`` (column 0 = 1) branches by toggling
    the first feature in the input row.
    """

    def predict_proba(self, rows):
        import numpy as np
        arr = np.asarray(rows, dtype=float)
        p_dmg = arr[:, 0]
        return np.column_stack([1.0 - p_dmg, p_dmg])


class _StubModelZeros:
    """``predict_proba`` returns all-zero (bias toward "safe")."""

    def predict_proba(self, rows):
        import numpy as np
        return np.zeros((len(rows), 2))


class _StubModelTiny:
    """Constant 50/50 prediction; used for installer tests where the
    model's actual numeric output doesn't matter."""

    def predict_proba(self, rows):
        return [[0.5, 0.5] for _ in rows]


@pytest.fixture
def tmp_prefs(tmp_path, monkeypatch):
    """Redirect the model directory + clear in-memory cache."""
    monkeypatch.setenv("STIMTEST_PREFS_DIR", str(tmp_path))
    from stimtest import neurostimml as nm
    nm.clear_model_cache()
    yield tmp_path
    nm.clear_model_cache()


def test_model_path_obeys_env_var(tmp_prefs, tmp_path):
    """``STIMTEST_PREFS_DIR`` redirects ``model_path``."""
    from stimtest.neurostimml import model_path
    p = model_path()
    assert str(p).startswith(str(tmp_path))
    assert p.name == "RF-Partial-19.pkl"


def test_model_not_installed_returns_clean_none(tmp_prefs):
    """Every API entry point handles the no-model case gracefully."""
    from stimtest.neurostimml import (
        model_is_installed, get_model, predict, predict_from_capture,
    )
    assert model_is_installed() is False
    assert get_model() is None
    # 16-element zero row — ``predict`` should still no-op cleanly.
    assert predict([0.0] * 16) is None
    # And the capture-shaped path no-ops too.
    from types import SimpleNamespace
    cap = SimpleNamespace(
        metrics=SimpleNamespace(
            charge_per_phase_nc=20.0,
            charge_injection_mc_per_cm2=0.04,
        ),
        pattern=None,
    )
    assert predict_from_capture(cap, surface_area_um2=5000.0) is None


def test_feature_order_matches_training_script():
    """16 columns in the upstream training-script order."""
    from stimtest.neurostimml import FEATURE_ORDER
    expected = (
        "Waveform_type_biphasic_asymmetric",
        "Waveform_type_biphasic_balanced",
        "Waveform_type_biphasic_capacitive",
        "Waveform_type_monophasic",
        "GSA",
        "Pulse_width",
        "Frequency",
        "Current",
        "Voltage",
        "Charge_per_phase",
        "Charge_density",
        "Current_density",
        "stim_on",
        "stim_total_day",
        "daily_pulses",
        "daily_accumulated_charge",
    )
    assert FEATURE_ORDER == expected


def test_encode_waveform_type_monophasic():
    """One phase → monophasic one-hot is 1.0 in column 3."""
    from stimtest.neurostimml import encode_waveform_type
    from stimtest.waveforms import Phase, PulsePattern
    p = PulsePattern(
        phases=[Phase(amplitude_ua=-100.0, width_us=200.0,
                       delay_after_us=0.0)],
        rate_hz=50.0,
    )
    out = encode_waveform_type(p)
    assert out == [0.0, 0.0, 0.0, 1.0]


def test_encode_waveform_type_biphasic_balanced():
    """Equal-amplitude / equal-width / same-shape biphasic → balanced."""
    from stimtest.neurostimml import encode_waveform_type
    from stimtest.waveforms import Phase, PulsePattern
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100.0, width_us=200.0, delay_after_us=0.0,
                  shape="rect"),
            Phase(amplitude_ua=+100.0, width_us=200.0, delay_after_us=0.0,
                  shape="rect"),
        ],
        rate_hz=50.0,
    )
    out = encode_waveform_type(p)
    # Balanced is column index 1.
    assert out == [0.0, 1.0, 0.0, 0.0]


def test_encode_waveform_type_biphasic_asymmetric():
    """Different amplitudes → biphasic_asymmetric."""
    from stimtest.neurostimml import encode_waveform_type
    from stimtest.waveforms import Phase, PulsePattern
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100.0, width_us=200.0, delay_after_us=0.0),
            Phase(amplitude_ua=+50.0, width_us=400.0, delay_after_us=0.0),
        ],
        rate_hz=50.0,
    )
    out = encode_waveform_type(p)
    # Asymmetric is column index 0.
    assert out == [1.0, 0.0, 0.0, 0.0]


def test_build_feature_vector_basic_shape():
    """Returns a 16-element list with the right unit conversions."""
    from stimtest.neurostimml import build_feature_vector, FEATURE_ORDER
    from stimtest.waveforms import Phase, PulsePattern
    p = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100.0, width_us=200.0, delay_after_us=0.0),
            Phase(amplitude_ua=+100.0, width_us=200.0, delay_after_us=0.0),
        ],
        rate_hz=50.0,
    )
    feats = build_feature_vector(
        pattern=p,
        charge_per_phase_nc=20.0,         # 100 µA × 200 µs = 20 nC
        charge_injection_mc_per_cm2=0.04, # 20 nC / 5000 µm² = 0.04 mC/cm²
        surface_area_um2=5000.0,
    )
    assert feats is not None
    assert len(feats) == len(FEATURE_ORDER)
    # Pulse width column should be 200.0.
    pw_idx = FEATURE_ORDER.index("Pulse_width")
    assert feats[pw_idx] == pytest.approx(200.0)
    # Charge density column should be 0.04 mC/cm² × 1000 = 40 µC/cm².
    cd_idx = FEATURE_ORDER.index("Charge_density")
    assert feats[cd_idx] == pytest.approx(40.0)
    # Current column = 100 µA (absolute value of leading phase).
    cur_idx = FEATURE_ORDER.index("Current")
    assert feats[cur_idx] == pytest.approx(100.0)


def test_build_feature_vector_returns_none_on_bad_input():
    """Non-finite charge or zero GSA → None."""
    from stimtest.neurostimml import build_feature_vector
    from stimtest.waveforms import Phase, PulsePattern
    p = PulsePattern(
        phases=[Phase(amplitude_ua=-100.0, width_us=200.0,
                       delay_after_us=0.0)],
        rate_hz=50.0,
    )
    # Zero surface area is rejected.
    assert build_feature_vector(
        pattern=p, charge_per_phase_nc=20.0,
        charge_injection_mc_per_cm2=0.04, surface_area_um2=0.0) is None
    # NaN charge is rejected.
    assert build_feature_vector(
        pattern=p, charge_per_phase_nc=float("nan"),
        charge_injection_mc_per_cm2=0.04, surface_area_um2=5000.0) is None
    # No pattern returns None.
    assert build_feature_vector(
        pattern=None, charge_per_phase_nc=20.0,
        charge_injection_mc_per_cm2=0.04, surface_area_um2=5000.0) is None


def test_predict_with_fake_stub_model(tmp_prefs):
    """Drop a fake pickle that exposes the ``predict_proba`` API and
    confirm ``predict()`` returns the expected NeurostimMLResult.

    The fake model echoes back probabilities derived from the input
    row's first column so the test can verify the unit-conversion
    didn't accidentally mangle the row before reaching the predictor.
    """
    import joblib
    from stimtest.neurostimml import (
        FEATURE_ORDER, predict, model_path, clear_model_cache,
        PROBABILITY_THRESHOLD,
    )

    # Use the module-level _StubModelEcho — joblib needs the class
    # to be reachable via its qualified import path or it won't
    # pickle. Inline (nested-in-test-function) classes fail with
    # ``PicklingError: Can't pickle <class …<locals>>``.
    joblib.dump(_StubModelEcho(), model_path())
    clear_model_cache()

    # Row that would yield probability 0.0 → likely_safe.
    row_safe = [0.0] * len(FEATURE_ORDER)
    res_safe = predict(row_safe)
    assert res_safe is not None
    assert res_safe.classification == "likely_safe"
    assert res_safe.probability == pytest.approx(0.0)

    # Row that would yield probability 1.0 → likely_damaging.
    row_damaging = list(row_safe)
    row_damaging[0] = 1.0
    res_dmg = predict(row_damaging)
    assert res_dmg is not None
    assert res_dmg.classification == "likely_damaging"
    assert res_dmg.probability == pytest.approx(1.0)
    assert res_dmg.probability >= PROBABILITY_THRESHOLD


def test_predict_rejects_wrong_length_row(tmp_prefs):
    """``predict`` returns None and logs (doesn't raise) on bad shape."""
    import joblib
    from stimtest.neurostimml import predict, model_path, clear_model_cache

    joblib.dump(_StubModelZeros(), model_path())
    clear_model_cache()
    # 5 columns, expected 16 → None.
    assert predict([0.0] * 5) is None
    # 16 columns → real result, just to confirm the fixture works.
    out = predict([0.0] * 16)
    assert out is not None


def test_install_model_from_url_local_file(tmp_prefs, tmp_path):
    """``install_model_from_url`` accepts a ``file://`` URL so we can
    test the install path without touching the network."""
    import joblib
    from stimtest.neurostimml import (
        install_model_from_url, model_is_installed, model_path,
        compute_sha256,
    )

    # Build a tiny dummy "model" and write it to a temp .pkl file.
    src = tmp_path / "src.pkl"
    joblib.dump(_StubModelTiny(), src)
    src_digest = compute_sha256(src)
    file_url = src.resolve().as_uri()

    # Run the installer.
    target, digest = install_model_from_url(file_url)
    assert target == model_path()
    assert digest == src_digest
    assert model_is_installed()
    # Re-download with the right expected_sha256 succeeds.
    target2, digest2 = install_model_from_url(
        file_url, expected_sha256=src_digest)
    assert digest2 == src_digest
    # Re-download with a wrong expected hash raises and leaves the
    # original file intact.
    with pytest.raises(RuntimeError):
        install_model_from_url(file_url,
                               expected_sha256="0" * 64)
    assert model_is_installed()
    # On-disk hash should still match the original.
    assert compute_sha256(model_path()) == src_digest


def test_install_model_rejects_oversized_download(tmp_prefs, tmp_path):
    """The 5 MB cap kicks in when the source is too large."""
    from stimtest.neurostimml import install_model_from_url
    src = tmp_path / "huge.pkl"
    # 200 KB of zeros — well under the default cap, but we'll set the
    # cap below this and confirm the install rejects.
    src.write_bytes(b"\x00" * 200_000)
    with pytest.raises(RuntimeError):
        install_model_from_url(src.resolve().as_uri(),
                               max_size_bytes=10_000)


def test_uninstall_removes_file(tmp_prefs):
    """``uninstall_model`` returns True when removing an existing
    file, False when nothing was there."""
    from stimtest.neurostimml import (
        uninstall_model, model_path, model_is_installed,
    )
    # Empty state.
    assert uninstall_model() is False
    # Plant a fake file.
    p = model_path()
    p.write_bytes(b"not a real pickle but still a file")
    assert model_is_installed()
    assert uninstall_model() is True
    assert not model_is_installed()


# ---------------------------------------------------------------------------
# Supplementary-table cross-reference + hyperparameter constants
# ---------------------------------------------------------------------------

def test_supp_table_column_units_matches_database():
    """The supp-table column-unit list ends with the binary label,
    starts with ``Reference``, and contains every numerical column
    we expect."""
    from stimtest.neurostimml import SUPP_TABLE_COLUMN_UNITS
    names = [n for n, _u in SUPP_TABLE_COLUMN_UNITS]
    assert names[0] == "Reference"
    assert names[-1] == "Avg_Damage_binary"
    # Spot-check key numerical columns.
    for required in ("GSA", "Pulse_width", "Frequency",
                     "Charge_per_phase",
                     "Charge_density_per_phase",
                     "Daily_accumulated_charge"):
        assert required in names


def test_partial_feature_set_supp_names_has_expected_count():
    """13 features in the partial set per Supp Table 5: 1
    categorical + 12 numerical."""
    from stimtest.neurostimml import PARTIAL_FEATURE_SET_SUPP_NAMES
    assert len(PARTIAL_FEATURE_SET_SUPP_NAMES) == 13
    assert PARTIAL_FEATURE_SET_SUPP_NAMES[0] == "Waveform_type"
    # The Stim_time_daily / Daily_pulses_count alias names are
    # what the supp table prints (vs the training-script's
    # stim_total_day / daily_pulses).
    assert "Stim_time_daily" in PARTIAL_FEATURE_SET_SUPP_NAMES
    assert "Daily_pulses_count" in PARTIAL_FEATURE_SET_SUPP_NAMES


def test_semi_complete_feature_set_supp_names_has_25_entries():
    """25 features in the semi-complete set per Supp Table 4:
    10 categorical + 15 numerical."""
    from stimtest.neurostimml import SEMI_COMPLETE_FEATURE_SET_SUPP_NAMES
    assert len(SEMI_COMPLETE_FEATURE_SET_SUPP_NAMES) == 25
    # Spot-check a categorical and a numerical entry.
    assert "Nervous_system" in SEMI_COMPLETE_FEATURE_SET_SUPP_NAMES
    assert "Interphase_delay" in SEMI_COMPLETE_FEATURE_SET_SUPP_NAMES


def test_hyperparameter_constants_match_supp_table_1():
    """RF / k-NN / MLP hyperparameter ranges agree with Supp Table 1."""
    from stimtest.neurostimml import (
        RF_HYPERPARAMETERS, KNN_HYPERPARAMETERS, MLP_HYPERPARAMETERS,
        LOGISTIC_REGRESSION_HYPERPARAMETERS,
    )
    assert RF_HYPERPARAMETERS["n_estimators_range"] == (1, 100)
    assert RF_HYPERPARAMETERS["selected_partial_19_n_estimators"] == 19
    assert KNN_HYPERPARAMETERS["n_neighbors_range"] == (1, 100)
    assert MLP_HYPERPARAMETERS["hidden_layer_count"] == 2
    assert MLP_HYPERPARAMETERS["nodes_first_layer_range"] == (1, 30)
    assert MLP_HYPERPARAMETERS["nodes_second_layer_range"] == (1, 30)
    assert "tanh" in MLP_HYPERPARAMETERS["activation_options"]
    assert "adam" in MLP_HYPERPARAMETERS["solver_options"]
    assert LOGISTIC_REGRESSION_HYPERPARAMETERS["search_grid"] is None


def test_feature_training_ranges_covers_all_numerical_columns():
    """Every numerical column in FEATURE_ORDER (i.e. everything
    except the four one-hot columns) has a training range."""
    from stimtest.neurostimml import (
        FEATURE_ORDER, FEATURE_TRAINING_RANGES,
    )
    one_hot = {n for n in FEATURE_ORDER if n.startswith("Waveform_type_")}
    numerical = [n for n in FEATURE_ORDER if n not in one_hot]
    for n in numerical:
        assert n in FEATURE_TRAINING_RANGES, (
            f"{n} missing a training range")


def test_features_outside_training_range_flags_extrapolation():
    """A feature row with one out-of-range value is flagged."""
    from stimtest.neurostimml import (
        features_outside_training_range, FEATURE_ORDER,
    )
    # Build a baseline in-range row: zeros are accepted by the
    # validator, so a row of zeros has no flagged features.
    row = [0.0] * len(FEATURE_ORDER)
    assert features_outside_training_range(row) == []
    # Set GSA to 1e9 µm² (= 10 cm², way above the 0.5 cm² /
    # 5e7 µm² training cap).
    gsa_idx = FEATURE_ORDER.index("GSA")
    row[gsa_idx] = 1e9
    flagged = features_outside_training_range(row)
    assert "GSA" in flagged
    # In-range value (1e6 µm² = 0.01 cm²) flag clears.
    row[gsa_idx] = 1e6
    flagged = features_outside_training_range(row)
    assert "GSA" not in flagged


def test_features_outside_training_range_handles_bad_input():
    """Wrong-length / non-numeric inputs return an empty list,
    never raise."""
    from stimtest.neurostimml import features_outside_training_range
    # Wrong length → empty.
    assert features_outside_training_range([0.0]) == []
    # Empty → empty.
    assert features_outside_training_range([]) == []
