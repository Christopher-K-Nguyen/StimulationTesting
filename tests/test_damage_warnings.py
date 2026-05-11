"""Tests for the synthesised damage-warning module.

Covers:

1. ``assess_planned_run`` returns ``None`` for safe parameters in
   info environments (no spurious dialogs at the bench).
2. ``assess_planned_run`` produces a warn-level Warning when
   Shannon fires in a warn environment (ex-vivo).
3. ``assess_planned_run`` escalates to alert when ≥2 criteria
   fire in an alert environment.
4. ``assess_planned_run`` returns ``None`` when no criteria fire
   even in an alert environment (no nag on safe in-vivo runs).
5. ``assess_finished_capture`` returns ``None`` on an info-posture
   environment regardless of the capture's verdict.
6. ``assess_finished_capture`` returns a Warning when a non-info
   environment's capture has Shannon flagged.
7. The Warning's ``should_block`` is True for warn / alert and
   False for info.
"""
from __future__ import annotations

import math

import pytest


# ---------------------------------------------------------------------------
# Helpers — synthesise a PulsePattern + Capture without scope traces
# ---------------------------------------------------------------------------

def _make_pattern(amplitude_ua: float = 100.0,
                  width_us: float = 200.0,
                  rate_hz: float = 50.0):
    """Two-phase symmetric biphasic pulse for the warning tests."""
    from stimtest.waveforms import Phase, PulsePattern
    return PulsePattern(
        phases=[
            Phase(amplitude_ua=-amplitude_ua, width_us=width_us,
                  delay_after_us=0.0),
            Phase(amplitude_ua=+amplitude_ua, width_us=width_us,
                  delay_after_us=0.0),
        ],
        rate_hz=rate_hz,
    )


def _make_capture_with_verdict(damage_classification: str,
                               *, criteria_fired=None):
    """Build a SimpleNamespace capture whose .metrics carries the
    given Shannon classification + per-criterion flags."""
    from types import SimpleNamespace
    if criteria_fired is None:
        criteria_fired = {}
    metrics = SimpleNamespace(
        shannon_k_value=2.0,
        damage_classification=damage_classification,
        damage_criteria=dict(criteria_fired),
        damage_band="meso",
        neurostimml_classification="model_not_installed",
        neurostimml_probability=float("nan"),
    )
    return SimpleNamespace(index=0, metrics=metrics)


# ---------------------------------------------------------------------------
# assess_planned_run
# ---------------------------------------------------------------------------

def test_planned_run_safe_pbs_returns_none():
    """Below-threshold params in a benchtop buffer → no warning."""
    from stimtest.damage_warnings import assess_planned_run
    pat = _make_pattern(amplitude_ua=10.0, width_us=200.0)
    # Q_ph = 10 µA × 200 µs = 2 nC. Site 0.06 cm² (= 6e6 µm²) →
    # Q_density = 2 nC / 0.06 cm² = ~33 nC/cm² = 0.033 µC/cm² →
    # k = log10(0.033) + log10(0.002) ≈ -4.18 — well below 1.85.
    result = assess_planned_run(
        pattern=pat,
        surface_area_um2=6e6,
        environment_short="pbs",
    )
    assert result is None


def test_planned_run_safe_in_vivo_still_returns_none():
    """No criterion fired = no warning, even in an alert env.
    We don't want click-through nags on safe in-vivo runs."""
    from stimtest.damage_warnings import assess_planned_run
    pat = _make_pattern(amplitude_ua=10.0, width_us=200.0)
    result = assess_planned_run(
        pattern=pat,
        surface_area_um2=6e6,
        environment_short="rat_cortex",
    )
    assert result is None


def test_planned_run_shannon_fires_in_ex_vivo_returns_warn():
    """Ex-vivo (warn posture) + Shannon above threshold → warn."""
    from stimtest.damage_warnings import assess_planned_run
    # Big pulse: 1000 µA × 1000 µs = 1000 nC = 1 µC; on a 5000 µm²
    # = 5e-9 cm² site → density 200 µC/cm². k = log10(200) +
    # log10(1) ≈ 2.30 — well above 1.85.
    pat = _make_pattern(amplitude_ua=1000.0, width_us=1000.0)
    result = assess_planned_run(
        pattern=pat,
        surface_area_um2=5000.0,
        environment_short="ex_vivo_brain_slice",
        run_neurostimml=False,  # keep the test independent of the model.
    )
    assert result is not None
    assert result.level == "warn"
    assert result.should_block is True
    # Shannon criterion appears in the criteria list.
    short_ids = [c[0] for c in result.criteria]
    assert "shannon" in short_ids


def test_planned_run_two_criteria_in_warn_env_escalates_to_alert():
    """Multiple criteria fire → posture escalates one tier."""
    from stimtest.damage_warnings import assess_planned_run
    # Macro electrode (1 cm² = 1e8 µm²) + huge density.
    # 1000 µA × 1000 µs = 1000 nC = 1 µC over 1e8 µm² = 0.01 µC/cm²
    # — too small. Let me bump the amplitude to push the macro
    # cap (30 µC/cm²) AND Shannon. Need both in the macro band:
    # GSA > 0.03 cm² = 3e6 µm². 5e6 µm² = 0.05 cm² is in the
    # macro band. We want density > 30 µC/cm² and k > 1.85.
    # Q_ph = 5 µC, density = 100 µC/cm² → k = log10(100) +
    # log10(5) = 2 + 0.699 = 2.699 (above shannon, above macro
    # cap).
    pat = _make_pattern(amplitude_ua=25000.0, width_us=200.0)
    # Q_ph = 25000 × 200 × 1e-3 = 5000 nC = 5 µC.
    # GSA 5e6 µm² = 0.05 cm² → density 100 µC/cm² ✓.
    result = assess_planned_run(
        pattern=pat,
        surface_area_um2=5e6,
        environment_short="ex_vivo_brain_slice",  # warn posture
        run_neurostimml=False,
    )
    assert result is not None
    assert result.level == "alert"  # escalated one tier
    assert result.should_block is True
    short_ids = [c[0] for c in result.criteria]
    # Both Shannon AND macro-cap should fire.
    assert "shannon" in short_ids
    assert "macro_cap" in short_ids


def test_planned_run_in_info_environment_logs_only():
    """Info posture: warning is returned but classified as info,
    so the caller emits a log line and never blocks."""
    from stimtest.damage_warnings import assess_planned_run
    # Same big pulse as the ex-vivo Shannon test, but in PBS.
    pat = _make_pattern(amplitude_ua=1000.0, width_us=1000.0)
    result = assess_planned_run(
        pattern=pat,
        surface_area_um2=5000.0,
        environment_short="pbs",
        run_neurostimml=False,
    )
    # Single criterion + info env → no blocking warning. The
    # synthesizer suppresses the "info posture, single criterion"
    # case to avoid noise; the per-capture log line covers the
    # caveat instead.
    assert result is None or result.level == "info"
    if result is not None:
        assert result.should_block is False


def test_planned_run_max_amplitude_used_for_worst_case():
    """The ``max_amplitude_ua`` kwarg drives the worst-case Shannon
    check, not the pattern's bare amplitude."""
    from stimtest.damage_warnings import assess_planned_run
    # Pattern amp = 10 µA (safe); ramp ceiling = 1000 µA (unsafe).
    # The check should use the ceiling and fire.
    pat = _make_pattern(amplitude_ua=10.0, width_us=1000.0)
    safe = assess_planned_run(
        pattern=pat,
        surface_area_um2=5000.0,
        environment_short="ex_vivo_brain_slice",
        run_neurostimml=False,
    )
    assert safe is None
    flagged = assess_planned_run(
        pattern=pat,
        surface_area_um2=5000.0,
        environment_short="ex_vivo_brain_slice",
        max_amplitude_ua=1000.0,
        run_neurostimml=False,
    )
    assert flagged is not None
    assert flagged.level in ("warn", "alert")


# ---------------------------------------------------------------------------
# assess_finished_capture
# ---------------------------------------------------------------------------

def test_finished_capture_info_environment_suppresses_log():
    """PBS-tier environment → no per-capture log emit even if the
    capture's Shannon classification fired."""
    from stimtest.damage_warnings import assess_finished_capture
    cap = _make_capture_with_verdict(
        "above_shannon",
        criteria_fired={"shannon": True})
    assert assess_finished_capture(cap, environment_short="pbs") is None


def test_finished_capture_warn_environment_emits_warning():
    """Ex-vivo + Shannon-fired capture → warn-level Warning."""
    from stimtest.damage_warnings import assess_finished_capture
    cap = _make_capture_with_verdict(
        "above_shannon",
        criteria_fired={"shannon": True})
    w = assess_finished_capture(
        cap, environment_short="ex_vivo_brain_slice")
    assert w is not None
    assert w.level == "warn"
    assert w.should_block is False  # capture is already done
    short_ids = [c[0] for c in w.criteria]
    assert "shannon" in short_ids


def test_finished_capture_alert_environment_two_criteria_escalates():
    """Two criteria + alert env → still alert (already top tier)."""
    from stimtest.damage_warnings import assess_finished_capture
    cap = _make_capture_with_verdict(
        "above_macro_cap",
        criteria_fired={"shannon": True, "macro_cap": True})
    w = assess_finished_capture(cap, environment_short="rat_cortex")
    assert w is not None
    assert w.level == "alert"


def test_finished_capture_no_criteria_returns_none():
    """A capture with classification=likely_safe → no log emit."""
    from stimtest.damage_warnings import assess_finished_capture
    cap = _make_capture_with_verdict(
        "likely_safe", criteria_fired={})
    assert assess_finished_capture(
        cap, environment_short="rat_cortex") is None


def test_finished_capture_neurostimml_alone_can_fire():
    """If only the ML model fired (Shannon below threshold), the
    log warning should still appear."""
    from stimtest.damage_warnings import assess_finished_capture
    from types import SimpleNamespace
    metrics = SimpleNamespace(
        shannon_k_value=1.0,                         # below threshold
        damage_classification="likely_safe",
        damage_criteria={"shannon": False,
                         "macro_cap": False,
                         "micro_cap": False},
        damage_band="meso",
        neurostimml_classification="likely_damaging",
        neurostimml_probability=0.85,
    )
    cap = SimpleNamespace(index=12, metrics=metrics)
    w = assess_finished_capture(cap, environment_short="rat_cortex")
    assert w is not None
    short_ids = [c[0] for c in w.criteria]
    assert "neurostimml" in short_ids


def test_warning_carries_environment_short():
    """The synthesised warning records which environment drove
    the posture so a downstream log entry can include it."""
    from stimtest.damage_warnings import assess_finished_capture
    cap = _make_capture_with_verdict(
        "above_shannon",
        criteria_fired={"shannon": True})
    w = assess_finished_capture(
        cap, environment_short="ex_vivo_brain_slice")
    assert w is not None
    assert w.environment_short == "ex_vivo_brain_slice"


def test_finished_capture_unknown_env_defaults_to_warn():
    """Unknown environment short_code → warn posture (safety
    default), criteria fire, warning emitted."""
    from stimtest.damage_warnings import assess_finished_capture
    cap = _make_capture_with_verdict(
        "above_shannon",
        criteria_fired={"shannon": True})
    w = assess_finished_capture(cap, environment_short="totally_unknown")
    assert w is not None
    assert w.level == "warn"
