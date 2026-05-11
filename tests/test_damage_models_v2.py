"""Tests for the post-paper-review additions to damage_models.

Covers the new constants and helpers introduced after going through
the references and supplementary materials of Li et al. 2024:

* New DOI constants (McCreery 2010, Vatsyayan-Dayeh 2022,
  GitHub repo URL).
* The ``DAMAGE_LEVELS`` 0-4 enum and its lookup tables.
* :func:`damage_level_from_classification` — binary → 0/2/None mapping.
* :data:`RECOMMENDED_REPORT_FIELDS` — at least the 11 fields from §4.
* :data:`SHANNON_FALSE_NEGATIVE_RATE_NOTE` actually mentioning the
  63–66% figure verbatim.
* The ``compute_metrics`` integration writes ``damage_level``.
"""
from __future__ import annotations

import pytest


def test_new_doi_constants_present():
    """All new DOIs are non-empty https URLs."""
    from stimtest.damage_models import (
        SHANNON_1992_DOI,
        MCCREERY_2010_DOI,
        COGAN_2016_DOI,
        VATSYAYAN_DAYEH_2022_DOI,
        LI_2024_DOI,
        NEUROSTIMML_WEB_URL,
        NEUROSTIMML_GITHUB_REPO,
    )
    for url in (SHANNON_1992_DOI, MCCREERY_2010_DOI, COGAN_2016_DOI,
                VATSYAYAN_DAYEH_2022_DOI, LI_2024_DOI,
                NEUROSTIMML_WEB_URL, NEUROSTIMML_GITHUB_REPO):
        assert isinstance(url, str)
        assert url.startswith("https://")


def test_damage_levels_table_shape():
    """0-4 enum is a 5-tuple of (level, label, description)."""
    from stimtest.damage_models import DAMAGE_LEVELS
    levels = [row[0] for row in DAMAGE_LEVELS]
    assert levels == [0, 1, 2, 3, 4]
    for lvl, label, desc in DAMAGE_LEVELS:
        assert isinstance(lvl, int)
        assert isinstance(label, str) and len(label) > 0
        assert isinstance(desc, str) and len(desc) > 10


def test_damage_level_lookup_tables_match_table():
    """LEVEL_LABELS / LEVEL_DESCRIPTIONS are derived from DAMAGE_LEVELS."""
    from stimtest.damage_models import (
        DAMAGE_LEVELS, DAMAGE_LEVEL_LABELS, DAMAGE_LEVEL_DESCRIPTIONS,
    )
    assert set(DAMAGE_LEVEL_LABELS) == {0, 1, 2, 3, 4}
    assert set(DAMAGE_LEVEL_DESCRIPTIONS) == {0, 1, 2, 3, 4}
    for lvl, label, desc in DAMAGE_LEVELS:
        assert DAMAGE_LEVEL_LABELS[lvl] == label
        assert DAMAGE_LEVEL_DESCRIPTIONS[lvl] == desc


def test_damage_level_from_classification_binary_mapping():
    """``"likely_safe"`` → 0, ``"above_*"`` → 2,
    ``"insufficient_data"`` → None."""
    from stimtest.damage_models import damage_level_from_classification
    assert damage_level_from_classification("likely_safe") == 0
    assert damage_level_from_classification("above_shannon") == 2
    assert damage_level_from_classification("above_macro_cap") == 2
    assert damage_level_from_classification("above_micro_cap") == 2
    assert damage_level_from_classification("insufficient_data") is None
    # Unknown strings → None (defensive).
    assert damage_level_from_classification("nonsense") is None


def test_recommended_report_fields_covers_paper_recs():
    """Li et al. 2024 §4 lists 11 disclosure parameters; we surface
    every one of them via short_key + prose label.
    """
    from stimtest.damage_models import RECOMMENDED_REPORT_FIELDS
    keys = [k for k, _label in RECOMMENDED_REPORT_FIELDS]
    expected = {
        "waveform_type", "frequency_hz", "pulse_width_us",
        "control_mode", "amplitude", "gsa", "duty_cycle",
        "daily_stim_duration", "days_stimulated",
        "electrode_shape", "electrode_material",
    }
    assert set(keys) == expected
    # Each entry has a non-empty prose label.
    for _key, label in RECOMMENDED_REPORT_FIELDS:
        assert isinstance(label, str)
        assert len(label) > 5


def test_shannon_fnr_note_mentions_specific_percentages():
    """The verbatim caveat must call out the 63-66% FNR + cite Li."""
    from stimtest.damage_models import SHANNON_FALSE_NEGATIVE_RATE_NOTE
    text = SHANNON_FALSE_NEGATIVE_RATE_NOTE
    assert "63" in text and "66" in text
    assert "Li et al" in text
    assert "Shannon" in text
    # Should also direct the user toward the higher-accuracy path.
    assert "NeurostimML" in text


def test_classification_explanation_likely_safe_includes_fnr_note():
    """The ``likely_safe`` explanation now embeds the FNR caveat."""
    from stimtest.damage_models import (
        classification_explanation, SHANNON_FALSE_NEGATIVE_RATE_NOTE,
    )
    text = classification_explanation("likely_safe")
    assert "63" in text and "66" in text
    # And contains the verbatim canonical note.
    assert SHANNON_FALSE_NEGATIVE_RATE_NOTE in text


def test_compute_metrics_populates_damage_level():
    """Capture-metrics integration writes the new ``damage_level``."""
    import math
    import numpy as np
    from stimtest.metrics import compute_metrics
    from stimtest.session import Capture
    from stimtest.waveforms import Phase, PulsePattern
    pat = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100.0, width_us=200.0, delay_after_us=0.0),
            Phase(amplitude_ua=+100.0, width_us=200.0, delay_after_us=0.0),
        ],
        rate_hz=50.0,
    )
    cap = Capture(
        index=0,
        pattern=pat,
        time_us=np.array([], dtype=float),
        v_mon_v=np.array([], dtype=float),
        i_mon_ua=np.array([], dtype=float),
    )
    m = compute_metrics(cap, surface_area_um2=5000.0)
    # damage_level is an int; -1 means "no verdict", 0/2 are valid mapped
    # values for the binary classifier.
    assert isinstance(m.damage_level, int)
    assert m.damage_level in (-1, 0, 2)
    # Shannon classification is consistent with damage_level.
    if m.damage_classification == "likely_safe":
        assert m.damage_level == 0
    elif m.damage_classification.startswith("above_"):
        assert m.damage_level == 2
    elif m.damage_classification == "insufficient_data":
        assert m.damage_level == -1


def test_compute_metrics_neurostimml_fields_default_to_not_installed(
        tmp_path, monkeypatch):
    """When the local model isn't on disk, the ML fields show
    ``model_not_installed`` and NaN probability — never crash."""
    import math
    import numpy as np
    monkeypatch.setenv("STIMTEST_PREFS_DIR", str(tmp_path))
    # Drop any prior in-memory cache so the env-var redirect bites.
    from stimtest import neurostimml as nm
    nm.clear_model_cache()
    from stimtest.metrics import compute_metrics
    from stimtest.session import Capture
    from stimtest.waveforms import Phase, PulsePattern
    pat = PulsePattern(
        phases=[
            Phase(amplitude_ua=-100.0, width_us=200.0, delay_after_us=0.0),
            Phase(amplitude_ua=+100.0, width_us=200.0, delay_after_us=0.0),
        ],
        rate_hz=50.0,
    )
    cap = Capture(
        index=0,
        pattern=pat,
        time_us=np.array([], dtype=float),
        v_mon_v=np.array([], dtype=float),
        i_mon_ua=np.array([], dtype=float),
    )
    m = compute_metrics(cap, surface_area_um2=5000.0)
    assert m.neurostimml_classification == "model_not_installed"
    assert math.isnan(m.neurostimml_probability)
