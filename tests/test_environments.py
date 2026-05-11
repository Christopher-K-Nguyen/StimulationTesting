"""Tests for the environment-preset module.

Covers:

1. Preset list shape — every entry has the required fields and a
   non-empty short_code.
2. Lookup helpers — short_code → preset, posture, display name,
   in-vivo predicate.
3. Custom-text rendering by ``display_name_for``.
4. Posture merging — ``merge_postures`` returns the most-severe
   level among its inputs, even when they're out of order.
5. ``DEFAULT_ENVIRONMENT`` resolves to a real preset.
"""
from __future__ import annotations

import pytest


def test_every_preset_has_complete_fields():
    """Each preset has a non-empty short_code / display_name /
    category / posture / description and a unique short_code.
    """
    from stimtest.environments import (
        ENVIRONMENT_PRESETS, POSTURE_ORDER,
    )
    seen = set()
    for p in ENVIRONMENT_PRESETS:
        assert p.short_code, p
        assert p.short_code not in seen, f"duplicate {p.short_code}"
        seen.add(p.short_code)
        assert p.display_name
        assert p.category
        assert p.posture in POSTURE_ORDER, (
            f"unknown posture {p.posture} on {p.short_code}")
        assert p.description and len(p.description) > 5


def test_default_environment_resolves():
    """The default short_code (PBS) is a real preset."""
    from stimtest.environments import (
        DEFAULT_ENVIRONMENT, get_preset,
    )
    p = get_preset(DEFAULT_ENVIRONMENT)
    assert p is not None
    assert p.short_code == DEFAULT_ENVIRONMENT
    assert p.posture == "info"  # PBS is benchtop


def test_posture_for_known_codes():
    """``posture_for`` returns the right level for each category."""
    from stimtest.environments import posture_for
    assert posture_for("pbs") == "info"
    assert posture_for("misf") == "info"
    assert posture_for("ex_vivo_brain_slice") == "warn"
    assert posture_for("rat_cortex") == "alert"
    assert posture_for("cat_cortex") == "alert"
    assert posture_for("human_dbs") == "alert"


def test_posture_for_unknown_falls_back_to_warn():
    """Unknown short_code → warn (safer than info)."""
    from stimtest.environments import posture_for
    assert posture_for("nonsense_value") == "warn"
    assert posture_for("") == "warn"


def test_display_name_for_custom_includes_user_text():
    """Custom preset prepends the user's free-form text."""
    from stimtest.environments import display_name_for
    assert display_name_for("custom", "modified PBS at 37 °C") \
        == "Custom: modified PBS at 37 °C"
    # Custom without text falls back to the bare label.
    assert display_name_for("custom", "") == "Custom (specify…)"
    # Non-custom presets ignore custom_text.
    assert display_name_for("pbs", "ignored text") \
        == "PBS (phosphate-buffered saline)"


def test_is_in_vivo_classifies_correctly():
    """``is_in_vivo`` returns True for in-vivo presets only."""
    from stimtest.environments import is_in_vivo
    # In-vivo:
    assert is_in_vivo("rat_cortex")
    assert is_in_vivo("cat_cortex")
    assert is_in_vivo("human_dbs")
    # Not in-vivo:
    assert not is_in_vivo("pbs")
    assert not is_in_vivo("ex_vivo_brain_slice")
    assert not is_in_vivo("custom")
    assert not is_in_vivo("")
    assert not is_in_vivo("unknown_code")


def test_merge_postures_returns_most_severe():
    """``merge_postures`` picks the highest level."""
    from stimtest.environments import merge_postures
    assert merge_postures("info") == "info"
    assert merge_postures("info", "warn") == "warn"
    assert merge_postures("warn", "info") == "warn"
    assert merge_postures("info", "warn", "alert") == "alert"
    assert merge_postures("alert", "info") == "alert"
    # Unknown postures default to warn — so a typo'd config
    # never silences a warning.
    assert merge_postures("info", "garbage") == "warn"
    assert merge_postures("alert", "garbage") == "alert"


def test_all_short_codes_returns_full_list():
    """``all_short_codes`` mirrors ENVIRONMENT_PRESETS length."""
    from stimtest.environments import (
        ENVIRONMENT_PRESETS, all_short_codes,
    )
    codes = all_short_codes()
    assert len(codes) == len(ENVIRONMENT_PRESETS)
    assert codes[0] == ENVIRONMENT_PRESETS[0].short_code


# ---------------------------------------------------------------------------
# Sparge-gas presets
# ---------------------------------------------------------------------------

def test_sparge_gas_presets_shape():
    """Three sparge options: None / N₂ / Ar, each with a non-empty
    display name and tooltip."""
    from stimtest.environments import (
        SPARGE_GAS_PRESETS, SPARGE_NONE, SPARGE_N2, SPARGE_AR,
        DEFAULT_SPARGE_GAS,
    )
    assert len(SPARGE_GAS_PRESETS) == 3
    short_codes = [p[0] for p in SPARGE_GAS_PRESETS]
    assert short_codes == [SPARGE_NONE, SPARGE_N2, SPARGE_AR]
    for short, display, tooltip in SPARGE_GAS_PRESETS:
        assert short
        assert display
        assert tooltip and len(tooltip) > 5
    assert DEFAULT_SPARGE_GAS == SPARGE_NONE


def test_get_sparge_gas_lookup():
    """``get_sparge_gas`` returns the matching tuple, or None."""
    from stimtest.environments import (
        get_sparge_gas, SPARGE_NONE, SPARGE_N2, SPARGE_AR,
    )
    p = get_sparge_gas(SPARGE_N2)
    assert p is not None
    assert p[0] == SPARGE_N2
    assert "N" in p[1]  # display includes nitrogen
    p = get_sparge_gas(SPARGE_AR)
    assert p is not None
    assert p[0] == SPARGE_AR
    p = get_sparge_gas(SPARGE_NONE)
    assert p is not None
    assert p[0] == SPARGE_NONE
    # Unknown → None.
    assert get_sparge_gas("xenon") is None
    assert get_sparge_gas("") is None


def test_sparge_display_name_for_falls_back_to_short_code():
    """Unknown short_code echoes back through the display helper."""
    from stimtest.environments import sparge_display_name_for
    assert sparge_display_name_for("n2")  # non-empty
    assert "argon" in sparge_display_name_for("ar").lower()
    assert sparge_display_name_for("xenon") == "xenon"
    assert sparge_display_name_for("") == "(unknown)"
