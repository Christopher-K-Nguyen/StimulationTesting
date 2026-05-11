"""Tests for the Shannon + Modified Shannon tissue-damage screen.

Covers six behaviours that need to hold for the GUI to report
trustworthy verdicts:

1. **Shannon math**: ``log10(Q_d) + log10(Q_ph)`` matches the
   published formula on hand-calculable inputs.
2. **NaN propagation**: non-positive / non-finite charge inputs
   produce a NaN k-value rather than ``-inf`` or a Python error,
   so the metric table can rely on ``math.isfinite`` to gate
   display.
3. **Modified Shannon macro cap**: macro-band electrodes (GSA
   > 0.03 cm²) flag when charge density exceeds 30 µC/cm²/ph,
   regardless of the k-value verdict.
4. **Modified Shannon micro cap**: micro-band electrodes (GSA
   < 2000 µm²) flag when charge per phase exceeds 4 nC/ph,
   regardless of the k-value verdict.
5. **Band gating**: the macro cap NEVER fires on a microelectrode
   and vice versa. A 30 µC/cm²/ph density on a microelectrode is
   gated by the micro cap (charge per phase) instead.
6. **Unit conversion adapter** (``assess_from_capture_metrics``):
   the nC + mC/cm² + µm² → µC + µC/cm² + cm² conversion is
   correct, so the metrics layer's adapter call gives the same
   verdict as the explicit-units API.
"""
from __future__ import annotations

import math

import pytest


def test_shannon_k_matches_published_formula():
    """k = log10(Q_d) + log10(Q_ph). Hand-checkable on round inputs."""
    from stimtest.damage_models import shannon_k
    # Q_d = 100 µC/cm²/ph, Q_ph = 0.1 µC/ph → k = 2 + (-1) = 1.
    assert shannon_k(0.1, 100.0) == pytest.approx(1.0)
    # Q_d = 30, Q_ph = 1 → k = log10(30) + 0 ≈ 1.4771
    assert shannon_k(1.0, 30.0) == pytest.approx(math.log10(30.0))


def test_shannon_k_nan_on_nonpositive_inputs():
    """Non-positive / non-finite inputs return NaN, never -inf."""
    from stimtest.damage_models import shannon_k
    assert math.isnan(shannon_k(0.0, 100.0))
    assert math.isnan(shannon_k(0.1, 0.0))
    assert math.isnan(shannon_k(-0.1, 100.0))
    assert math.isnan(shannon_k(0.1, -100.0))
    assert math.isnan(shannon_k(float("nan"), 100.0))
    assert math.isnan(shannon_k(0.1, float("inf")))
    # Strings get caught by the try/except wrapper.
    assert math.isnan(shannon_k("nope", 100.0))  # type: ignore[arg-type]


def test_shannon_k_from_capture_metrics_unit_conversion():
    """Adapter converts nC → µC and mC/cm² → µC/cm² correctly."""
    from stimtest.damage_models import (
        shannon_k, shannon_k_from_capture_metrics,
    )
    # Q_ph = 100 nC = 0.1 µC; Q_inj = 0.1 mC/cm² = 100 µC/cm².
    # k = log10(100) + log10(0.1) = 2 - 1 = 1.
    direct = shannon_k(0.1, 100.0)
    via_adapter = shannon_k_from_capture_metrics(100.0, 0.1)
    assert direct == pytest.approx(1.0)
    assert via_adapter == pytest.approx(direct)
    # Sign on the charge per phase shouldn't matter (Shannon takes
    # the magnitude of the leading-phase charge).
    assert (shannon_k_from_capture_metrics(-100.0, 0.1)
            == pytest.approx(direct))


def test_classify_band_macro_micro_meso():
    """Band-classifier returns the right tag for each GSA range."""
    from stimtest.damage_models import classify_band
    # 0.06 cm² is a typical DBS macroelectrode → macro
    assert classify_band(0.06) == "macro"
    # 0.04 cm² is also above the 0.03 cutoff → macro
    assert classify_band(0.04) == "macro"
    # 1000 µm² = 1e-5 cm² → below the 2000-µm² cutoff → micro
    assert classify_band(1e-5) == "micro"
    # 0.001 cm² (10 000 µm²) is between the two cutoffs → meso
    assert classify_band(0.001) == "meso"
    # Edge / sentinel inputs return the safe meso default.
    assert classify_band(0.0) == "meso"
    assert classify_band(-1.0) == "meso"
    assert classify_band(float("nan")) == "meso"


def test_assess_capture_likely_safe_below_all_thresholds():
    """A capture below k=1.85 AND below all caps returns likely_safe."""
    from stimtest.damage_models import assess_capture
    # Q_d = 10 µC/cm²/ph, Q_ph = 0.1 µC/ph (= 100 nC/ph)
    # k = log10(10) + log10(0.1) = 1 + (-1) = 0  (well below 1.85).
    # GSA 0.06 cm² → macro band; charge density 10 < 30 → no cap fired.
    a = assess_capture(
        charge_per_phase_uc=0.1,
        charge_density_uc_per_cm2=10.0,
        gsa_cm2=0.06,
    )
    assert a.classification == "likely_safe"
    assert not a.above_shannon
    assert not a.above_macro_cap
    assert not a.above_micro_cap
    assert a.band == "macro"
    assert a.k_value == pytest.approx(0.0)


def test_assess_capture_above_shannon_only():
    """A capture above k=1.85 but inside both caps reports
    ``above_shannon`` (no cap collapse)."""
    from stimtest.damage_models import assess_capture
    # Q_d = 20 µC/cm²/ph, Q_ph = 1 µC/ph → k = log10(20) + 0 ≈ 1.301
    # That's BELOW 1.85, so let's bump higher: Q_d = 50, Q_ph = 1
    # → k = log10(50) ≈ 1.699 — still below 1.85. Need bigger.
    # Q_d = 100, Q_ph = 1 → k = 2.0 (above 1.85). On a 0.001 cm²
    # MESO electrode neither modified cap applies.
    a = assess_capture(
        charge_per_phase_uc=1.0,
        charge_density_uc_per_cm2=100.0,
        gsa_cm2=0.001,  # meso band (between cutoffs)
    )
    assert a.k_value == pytest.approx(2.0)
    assert a.above_shannon is True
    assert a.above_macro_cap is False
    assert a.above_micro_cap is False
    assert a.classification == "above_shannon"
    assert a.band == "meso"


def test_assess_capture_macro_cap_fires_on_macroelectrode():
    """Macro band + charge density > 30 µC/cm²/ph → above_macro_cap."""
    from stimtest.damage_models import assess_capture
    # Macro electrode (GSA 0.06 cm²); charge density 50 µC/cm²/ph
    # exceeds the 30 cap. Charge per phase = 0.5 µC/ph; k = log10(50)
    # + log10(0.5) ≈ 1.699 - 0.301 = 1.398 — BELOW 1.85.
    # So Shannon says safe, but the macro cap should still fire.
    a = assess_capture(
        charge_per_phase_uc=0.5,
        charge_density_uc_per_cm2=50.0,
        gsa_cm2=0.06,
    )
    assert a.band == "macro"
    assert a.above_shannon is False        # below k threshold
    assert a.above_macro_cap is True       # above 30 µC/cm²
    assert a.above_micro_cap is False
    # Most-conservative-wins: macro cap should appear in the
    # classification string even though shannon didn't fire.
    assert a.classification == "above_macro_cap"


def test_assess_capture_macro_cap_does_not_fire_on_microelectrode():
    """Microelectrode at 50 µC/cm²/ph DOESN'T get the macro flag —
    only the micro cap (charge/phase) is band-relevant."""
    from stimtest.damage_models import assess_capture
    # GSA 1000 µm² = 1e-5 cm² → micro band.
    # Charge density 50 µC/cm²/ph (well above macro cap, but
    # macro cap doesn't apply); charge per phase 0.5 nC/ph
    # (= 0.0005 µC/ph) — below the 4 nC micro cap.
    a = assess_capture(
        charge_per_phase_uc=0.0005,
        charge_density_uc_per_cm2=50.0,
        gsa_cm2=1e-5,
    )
    assert a.band == "micro"
    assert a.above_macro_cap is False     # not in macro band
    assert a.above_micro_cap is False     # below 4 nC/ph cap


def test_assess_capture_micro_cap_fires_on_microelectrode():
    """Micro band + charge per phase > 4 nC/ph → above_micro_cap."""
    from stimtest.damage_models import assess_capture
    # GSA 1000 µm² = 1e-5 cm² → micro band.
    # Charge per phase 10 nC/ph = 0.01 µC/ph; charge density 1000
    # µC/cm²/ph (just to make k high too, but the micro cap is the
    # one that should be reported because it's most specific).
    a = assess_capture(
        charge_per_phase_uc=0.01,
        charge_density_uc_per_cm2=1000.0,
        gsa_cm2=1e-5,
    )
    assert a.band == "micro"
    assert a.above_micro_cap is True
    assert a.above_macro_cap is False
    # Micro cap is the most specific verdict and should win the
    # classification string even though Shannon also fired.
    assert a.classification == "above_micro_cap"


def test_assess_capture_micro_cap_does_not_fire_on_macroelectrode():
    """Macro electrode at 10 nC/ph DOESN'T get the micro flag."""
    from stimtest.damage_models import assess_capture
    # GSA 0.06 cm² → macro band.
    # Charge per phase 0.01 µC/ph = 10 nC/ph (above micro cap),
    # but the cap doesn't apply on a macroelectrode.
    # Charge density 0.167 µC/cm²/ph (10 nC over 0.06 cm² →
    # 1e-8 / 6e-2 = 1.67e-7 µC/cm²/ph, way below macro cap)
    a = assess_capture(
        charge_per_phase_uc=0.01,
        charge_density_uc_per_cm2=0.167,
        gsa_cm2=0.06,
    )
    assert a.band == "macro"
    assert a.above_micro_cap is False     # not in micro band


def test_assess_capture_insufficient_data_on_zero_charge():
    """Zero charge + zero density → insufficient_data verdict."""
    from stimtest.damage_models import assess_capture
    a = assess_capture(
        charge_per_phase_uc=0.0,
        charge_density_uc_per_cm2=0.0,
        gsa_cm2=0.06,
    )
    assert a.classification == "insufficient_data"
    assert math.isnan(a.k_value)
    assert a.above_shannon is False
    assert a.above_macro_cap is False
    assert a.above_micro_cap is False


def test_assess_from_capture_metrics_matches_explicit_units():
    """Adapter agrees with explicit-units call on equivalent inputs."""
    from stimtest.damage_models import (
        assess_capture, assess_from_capture_metrics,
    )
    # Capture-metrics units: 50 nC/ph, 0.05 mC/cm², GSA 1e6 µm² (= 0.01 cm²).
    explicit = assess_capture(
        charge_per_phase_uc=0.05,        # 50 nC = 0.05 µC
        charge_density_uc_per_cm2=50.0,  # 0.05 mC/cm² = 50 µC/cm²
        gsa_cm2=0.01,
    )
    adapted = assess_from_capture_metrics(
        charge_per_phase_nc=50.0,
        charge_injection_mc_per_cm2=0.05,
        surface_area_um2=1e6,            # 1e6 µm² = 1e-2 cm²
    )
    assert adapted.k_value == pytest.approx(explicit.k_value)
    assert adapted.classification == explicit.classification
    assert adapted.above_shannon == explicit.above_shannon
    assert adapted.above_macro_cap == explicit.above_macro_cap
    assert adapted.above_micro_cap == explicit.above_micro_cap
    assert adapted.band == explicit.band


def test_assess_capture_custom_k_threshold():
    """k_threshold parameter overrides the 1.85 default."""
    from stimtest.damage_models import assess_capture
    # k = 1.7 — below the 1.85 default but above a 1.5 threshold.
    # Q_d = 50, Q_ph = 1 → k ≈ 1.699
    a_default = assess_capture(
        charge_per_phase_uc=1.0,
        charge_density_uc_per_cm2=50.0,
        gsa_cm2=0.001,  # meso band so caps don't interfere
    )
    a_strict = assess_capture(
        charge_per_phase_uc=1.0,
        charge_density_uc_per_cm2=50.0,
        gsa_cm2=0.001,
        k_threshold=1.5,
    )
    assert a_default.above_shannon is False
    assert a_strict.above_shannon is True


def test_classification_labels_cover_every_classification():
    """Every classification value has a label and a colour."""
    from stimtest.damage_models import (
        CLASSIFICATION_LABELS, CLASSIFICATION_COLORS,
    )
    expected = {"likely_safe", "above_shannon", "above_macro_cap",
                "above_micro_cap", "insufficient_data"}
    assert set(CLASSIFICATION_LABELS).issuperset(expected)
    assert set(CLASSIFICATION_COLORS).issuperset(expected)


def test_classification_explanation_returns_nonempty_for_known():
    """Each known classification has a non-empty prose explanation."""
    from stimtest.damage_models import classification_explanation
    for cls in ("likely_safe", "above_shannon", "above_macro_cap",
                "above_micro_cap", "insufficient_data"):
        text = classification_explanation(cls)
        assert isinstance(text, str)
        assert len(text) > 20  # not just a placeholder


def test_compute_metrics_populates_damage_fields():
    """The metrics pipeline writes the new damage fields onto
    ``CaptureMetrics``."""
    import numpy as np
    from stimtest.metrics import compute_metrics
    from stimtest.session import Capture
    from stimtest.waveforms import Phase, PulsePattern
    # Symmetric biphasic 100 µA / 200 µs / 50 Hz on a 5000 µm² site.
    # Q_ph = 100 µA × 200 µs = 20 nC. The damage screen runs in a
    # band determined by the GSA — 5000 µm² > 2000 µm² so we get
    # the "meso" band (below the macro 0.03 cm² cutoff and above
    # the micro 2000 µm² cutoff), which means only the Shannon
    # k-value gates the verdict.
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
    assert math.isfinite(m.shannon_k_value)
    assert m.damage_classification in (
        "likely_safe", "above_shannon",
        "above_macro_cap", "above_micro_cap")
    assert isinstance(m.damage_criteria, dict)
    assert m.damage_band in ("macro", "micro", "meso")
