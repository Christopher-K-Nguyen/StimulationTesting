"""Tests for the f_stim / T_pulse abbreviation constants.

Two-line module — really just verifies the constants exist, render
to non-empty strings, and embed the expected subscript characters
(so the rich-text label produced via ``field_label`` carries the
intended visual form rather than a literal ``f_stim`` ASCII string
that the user reads as code).
"""
from __future__ import annotations

import pytest


def test_f_stim_constant_exists_and_renders():
    """``F_STIM`` renders to a HTML string carrying ``f`` + a
    subscript of ``stim`` — the same shape the project's other
    var() constants use (e.g. I_STIM, E_LC) so labels render
    consistently across panels.
    """
    from stimtest.gui import rich
    assert hasattr(rich, "F_STIM")
    val = rich.F_STIM
    assert isinstance(val, str)
    # Italic-tagged base letter + subscript wrapper for the suffix.
    # Don't assert the exact tag set — rich.var() may switch
    # between Unicode subscripts and HTML <sub> depending on
    # which characters are available — but DO assert the
    # underlying letters are present in the right order.
    assert "f" in val
    assert "stim" in val
    # Should match the same shape as the established constants.
    assert val == rich.var("f", "stim")
    # Should not be the bare ASCII string.
    assert val != "f_stim"


def test_t_pulse_constant_exists_and_renders():
    """``T_PULSE`` renders to a HTML string carrying ``T`` + a
    subscript of ``pulse``."""
    from stimtest.gui import rich
    assert hasattr(rich, "T_PULSE")
    val = rich.T_PULSE
    assert isinstance(val, str)
    assert "T" in val
    assert "pulse" in val
    assert val == rich.var("T", "pulse")
    assert val != "T_pulse"


def test_field_label_with_f_stim_includes_abbreviation():
    """``field_label("Pulse rate", F_STIM, PPS)`` renders both the
    full name AND the variable, separated by parentheses or
    however the helper formats it."""
    from stimtest.gui import rich
    label = rich.field_label("Pulse rate", rich.F_STIM, rich.PPS)
    assert "Pulse rate" in label
    assert rich.F_STIM in label
    assert rich.PPS in label


def test_field_label_with_t_pulse_includes_abbreviation():
    """Same shape as above for the T_pulse / period flip."""
    from stimtest.gui import rich
    label = rich.field_label("Pulse period", rich.T_PULSE, "ms")
    assert "Pulse period" in label
    assert rich.T_PULSE in label
    assert "ms" in label
