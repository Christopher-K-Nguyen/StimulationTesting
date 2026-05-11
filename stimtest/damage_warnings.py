"""Synthesize Shannon + NeurostimML + Environment posture into a
user-facing warning.

The pieces this module composes:

* :mod:`stimtest.damage_models` — Shannon equation + Modified
  Shannon caps; produces :class:`damage_models.DamageAssessment`.
* :mod:`stimtest.neurostimml` — local RF-Partial-19 inference
  when the user has installed the model; produces
  :class:`neurostimml.NeurostimMLResult` (or ``None`` when the
  pickle isn't present).
* :mod:`stimtest.environments` — picks the warning POSTURE
  (``info`` / ``warn`` / ``alert``) from the user-selected
  Environment preset.

The output is a :class:`Warning` with:

* ``level``     — final posture after merging environment + verdicts.
* ``title``     — short headline for a dialog or log line.
* ``body``      — multi-line prose body suitable for a QMessageBox.
* ``criteria``  — list of (criterion, prose) pairs that fired.
* ``should_block`` — True if the runner should require explicit
                     user acknowledgement before proceeding.

Two entry points
================

:func:`assess_planned_run` is called BEFORE a sweep starts — it
takes the planned pattern + Setup-tab snapshot and runs the
classifiers against the worst-case capture in the planned ramp
(highest expected charge per phase). The returned warning, if
any, drives a pre-run modal dialog.

:func:`assess_finished_capture` is called AFTER each capture
during a live run — it reads the just-populated CaptureMetrics
and produces a warning suitable for the log pane. The runner
emits the body via the existing ``log_msg`` signal.

Why a single synthesis module rather than ad-hoc rendering?
    Both call sites (pre-run dialog and per-capture log) need
    identical logic: Shannon classification + NeurostimML verdict
    + environment posture → merged warning. Factoring the
    composition here keeps the two surface points consistent and
    makes tests trivially easy — one ``assess_*`` call and one
    assertion on the returned :class:`Warning`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ---------------------------------------------------------------------------
# Warning record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Warning:
    """One synthesised warning ready for UI rendering.

    Attributes
    ----------
    level : str
        ``"info"`` / ``"warn"`` / ``"alert"`` — the merged posture.
    title : str
        One-line headline. Used as the dialog's window title and as
        the log-pane line prefix.
    body : str
        Multi-line plain-text explanation of every criterion that
        fired and the recommended action. Suitable for ``setText``
        on a QMessageBox; the GUI side may wrap with HTML if it
        wants colour or hyperlinks.
    criteria : List[Tuple[str, str]]
        Per-criterion ``(short_id, prose)`` pairs. ``short_id``
        is one of ``"shannon"`` / ``"macro_cap"`` / ``"micro_cap"``
        / ``"neurostimml"`` / ``"extrapolation"``; ``prose`` is the
        human-readable reason. Lets the GUI render badges per
        criterion or expand into an audit log.
    should_block : bool
        True when the runner should require an explicit user
        confirmation before proceeding. Currently mirrors
        ``level != "info"``, but kept as a separate field so a
        future opt-out preference (e.g. "always block on alert
        even on rerun") can override the level → block mapping
        without breaking the level semantics.
    environment_short : str
        The short_code of the environment preset that drove the
        posture. Stamped here so a downstream caller (log entry,
        contribute payload) can record it without a second lookup.
    """
    level: str
    title: str
    body: str
    criteria: List[Tuple[str, str]] = field(default_factory=list)
    should_block: bool = False
    environment_short: str = ""


# ---------------------------------------------------------------------------
# Environment-posture extraction
# ---------------------------------------------------------------------------

def _resolve_posture(environment_short: str) -> str:
    """Look up the environment's warning posture, falling back
    safely. Used by both planned-run and finished-capture paths.
    """
    try:
        from .environments import posture_for, POSTURE_WARN
    except Exception:
        return "warn"
    if not environment_short:
        # No environment selected at all — treat as ``warn`` so we
        # don't silently swallow alerts on a default-config session.
        return POSTURE_WARN
    return posture_for(environment_short)


# ---------------------------------------------------------------------------
# Worst-case-capture parameter extraction
# ---------------------------------------------------------------------------

def _planned_worst_case_charge(pattern,
                               *,
                               max_amplitude_ua: Optional[float] = None
                               ) -> Optional[Tuple[float, float]]:
    """Return ``(worst_charge_per_phase_nc,
    worst_charge_density_uc_per_cm2)`` for a planned ramp.

    The pre-run check needs to evaluate Shannon against the
    HIGHEST expected charge in the sweep, not the starting
    amplitude — otherwise a low-amplitude first capture passes
    the screen even though the user is about to push a much
    larger pulse later. We treat ``max_amplitude_ua`` as the
    upper bound of the ramp (defaults to the pattern's leading-
    phase amplitude when the runner doesn't supply one — i.e.
    the user is running a single-shot capture with no ramp).

    Returns ``None`` when the pattern's per-phase width / amp /
    surface area are missing or zero (no usable measurement).
    """
    if pattern is None:
        return None
    try:
        leading = pattern.phases[0]
        width_us = float(leading.width_us)
        amp_ua = abs(float(
            max_amplitude_ua
            if max_amplitude_ua is not None
            else leading.amplitude_ua
        ))
    except (AttributeError, IndexError, TypeError, ValueError):
        return None
    if width_us <= 0 or amp_ua <= 0:
        return None
    # Charge per phase: |I| × dt → charge in nC.
    # 1 µA × 1 µs = 1 pC, so |µA| × |µs| × 1e-3 = nC.
    charge_per_phase_nc = amp_ua * width_us * 1e-3
    return charge_per_phase_nc, float("nan")  # density resolved by caller


# ---------------------------------------------------------------------------
# Public synthesis: planned-run check
# ---------------------------------------------------------------------------

def assess_planned_run(*,
                       pattern,
                       surface_area_um2: float,
                       environment_short: str,
                       max_amplitude_ua: Optional[float] = None,
                       run_neurostimml: bool = True,
                       ) -> Optional[Warning]:
    """Evaluate a planned sweep BEFORE it starts.

    Parameters
    ----------
    pattern : PulsePattern
        The base pattern the runner will load onto the stimulator.
    surface_area_um2 : float
        Active electrode geometric surface area in µm² (matches
        what :class:`Capture` uses).
    environment_short : str
        Short code of the user-selected environment preset; used
        only to pick the warning posture, never feeds the
        classifiers.
    max_amplitude_ua : float, optional
        Upper bound of the planned ramp's leading-phase amplitude
        (µA). Defaults to the pattern's bare leading amplitude
        (single-shot capture). Pass the ramp's planned ceiling
        for a sweep-style experiment.
    run_neurostimml : bool
        If True (default), also run the local NeurostimML model
        when installed. The Setup tab can turn this off for users
        on slow hardware who only want the Shannon screen.

    Returns
    -------
    Optional[Warning]
        ``None`` when no warning is needed (every classifier said
        "likely safe" and we're not in an in-vivo environment).
        Otherwise a fully-populated :class:`Warning` ready for the
        pre-run dialog.

    Notes
    -----
    The function is GUI-free (no Qt imports); the caller renders
    the returned ``Warning`` however it wants (modal dialog,
    inline banner, log line, …). All failures are caught and
    converted to ``None`` so the runner never aborts a Start due
    to a buggy warning module.
    """
    # ---- charge math (worst-case) ---------------------------------------
    charges = _planned_worst_case_charge(
        pattern, max_amplitude_ua=max_amplitude_ua)
    if charges is None:
        return None
    charge_per_phase_nc, _ = charges
    try:
        # Q_inj = Q_ph_nC / area_cm² → mC/cm² (the units
        # CaptureMetrics stores). area_cm² = um² × 1e-8.
        gsa_cm2 = float(surface_area_um2) * 1e-8
        if gsa_cm2 <= 0:
            return None
        # nC / cm² × 1e-6 = mC/cm²
        charge_inj_mc_per_cm2 = (charge_per_phase_nc * 1e-6) / gsa_cm2
    except (TypeError, ValueError):
        return None

    # ---- Shannon assessment ----------------------------------------------
    try:
        from .damage_models import (
            assess_from_capture_metrics, classification_explanation,
        )
        shannon = assess_from_capture_metrics(
            charge_per_phase_nc=charge_per_phase_nc,
            charge_injection_mc_per_cm2=charge_inj_mc_per_cm2,
            surface_area_um2=surface_area_um2,
        )
    except Exception:
        return None

    criteria: List[Tuple[str, str]] = []
    if shannon.above_shannon:
        criteria.append((
            "shannon",
            classification_explanation("above_shannon")))
    if shannon.above_macro_cap:
        criteria.append((
            "macro_cap",
            classification_explanation("above_macro_cap")))
    if shannon.above_micro_cap:
        criteria.append((
            "micro_cap",
            classification_explanation("above_micro_cap")))

    # ---- NeurostimML assessment (optional) -------------------------------
    ml_verdict: Optional[str] = None
    ml_proba: float = float("nan")
    if run_neurostimml:
        try:
            from . import neurostimml as nm
            from .session import Capture
            # Build a synthetic Capture so we can reuse the
            # ``predict_from_capture`` path. The runner-side
            # adapter already handles missing rate / duty cycle
            # by filling sane defaults.
            from dataclasses import replace
            # Synthetic capture metrics for the planned worst case.
            class _SyntheticMetrics:
                charge_per_phase_nc = float(charge_per_phase_nc)
                charge_injection_mc_per_cm2 = float(charge_inj_mc_per_cm2)
            class _SyntheticCapture:
                def __init__(self, pattern):
                    self.pattern = pattern
                    self.metrics = _SyntheticMetrics()
            synth = _SyntheticCapture(pattern)
            result = nm.predict_from_capture(synth, surface_area_um2)
            if result is not None:
                ml_verdict = result.classification
                ml_proba = float(result.probability)
        except Exception:
            ml_verdict = None
    if ml_verdict == "likely_damaging":
        criteria.append((
            "neurostimml",
            f"NeurostimML (Li et al. 2024 RF-Partial-19) predicts "
            f"likely damaging at the planned worst-case amplitude "
            f"(probability of damage = {ml_proba:.2f})."))

    # ---- Environment posture ---------------------------------------------
    env_posture = _resolve_posture(environment_short)
    try:
        from .environments import (
            display_name_for, is_in_vivo, merge_postures,
            POSTURE_INFO, POSTURE_WARN, POSTURE_ALERT,
        )
        env_label = display_name_for(environment_short)
    except Exception:
        env_label = environment_short or "(unknown)"
        POSTURE_INFO = "info"
        POSTURE_WARN = "warn"
        POSTURE_ALERT = "alert"
        def merge_postures(*args):  # type: ignore[no-redef]
            return env_posture

    # If no criteria fired AND we're in a low-stakes (info)
    # environment, the silent path is fine — return None.
    if not criteria and env_posture == POSTURE_INFO:
        return None
    # Unflagged but in-vivo: still no warning. We don't want
    # a "looks safe but you're in a rat" dialog every Start
    # click — that becomes click-through noise.
    if not criteria:
        return None

    # ---- Posture merge ---------------------------------------------------
    # Shannon-only criterion under in-vivo posture stays at the
    # environment level. Two-or-more criteria firing escalates
    # one tier (info→warn, warn→alert).
    base_posture = env_posture
    if len(criteria) >= 2:
        try:
            idx = ("info", "warn", "alert").index(base_posture)
            base_posture = ("info", "warn", "alert")[min(idx + 1, 2)]
        except ValueError:
            base_posture = "warn"

    # ---- Body composition ------------------------------------------------
    title = {
        POSTURE_INFO:  "Pre-run damage screen — informational",
        POSTURE_WARN:  "Pre-run damage screen — warning",
        POSTURE_ALERT: "Pre-run damage screen — ALERT",
    }.get(base_posture, "Pre-run damage screen")

    body_lines: List[str] = []
    body_lines.append(f"Environment: {env_label}")
    body_lines.append("")
    body_lines.append(
        f"Planned worst-case charge per phase: "
        f"{charge_per_phase_nc:.2f} nC")
    body_lines.append(
        f"Planned worst-case charge density: "
        f"{charge_inj_mc_per_cm2 * 1000.0:.2f} µC/cm²")
    body_lines.append(
        f"Shannon k-value: {shannon.k_value:.3f} "
        f"(threshold {1.85:.2f})")
    if ml_verdict is not None:
        body_lines.append(
            f"NeurostimML probability of damage: {ml_proba:.2f}")
    body_lines.append("")
    body_lines.append("Flagged criteria:")
    for _short, prose in criteria:
        body_lines.append(f"  • {prose}")
    body_lines.append("")
    if base_posture == POSTURE_INFO:
        body_lines.append(
            "This environment is a benchtop buffer / culture; the "
            "warning is informational. The runner will proceed "
            "without further confirmation.")
    elif base_posture == POSTURE_WARN:
        body_lines.append(
            "Review the criteria above. Confirm to proceed or "
            "cancel and adjust the parameters / surface area.")
    else:  # alert
        body_lines.append(
            "This is a live preparation. Cross-check the planned "
            "parameters against the published literature for your "
            "tissue / electrode combination before proceeding. "
            "Confirm explicitly to continue, or cancel.")

    body = "\n".join(body_lines)
    should_block = base_posture in (POSTURE_WARN, POSTURE_ALERT)
    return Warning(
        level=base_posture,
        title=title,
        body=body,
        criteria=criteria,
        should_block=should_block,
        environment_short=environment_short,
    )


# ---------------------------------------------------------------------------
# Public synthesis: per-capture log warning
# ---------------------------------------------------------------------------

def assess_finished_capture(capture,
                            *,
                            environment_short: str,
                            ) -> Optional[Warning]:
    """Generate a warning for a just-completed capture.

    The runner calls this after :func:`compute_metrics`; the
    returned :class:`Warning` (if any) is emitted as a log line
    via the runner's ``log_msg`` signal. Unlike
    :func:`assess_planned_run`, this never blocks — the capture
    has already completed; we're just announcing what we saw.

    Returns ``None`` when:

    * No criterion fired (Shannon below threshold AND NeurostimML
      ``likely_safe`` AND no caps crossed), OR
    * The environment is ``info`` posture (we don't spam the log
      pane with per-capture info entries; the metric table
      already carries the per-capture verdict).
    """
    if capture is None or getattr(capture, "metrics", None) is None:
        return None
    m = capture.metrics

    criteria: List[Tuple[str, str]] = []
    crit = getattr(m, "damage_criteria", {}) or {}
    if crit.get("shannon"):
        criteria.append((
            "shannon",
            f"Shannon k-value {getattr(m, 'shannon_k_value', float('nan')):.3f} "
            f"≥ 1.85"))
    if crit.get("macro_cap"):
        criteria.append((
            "macro_cap",
            "Charge density above macroelectrode 30 µC/cm² cap"))
    if crit.get("micro_cap"):
        criteria.append((
            "micro_cap",
            "Charge per phase above microelectrode 4 nC/ph cap"))
    ml_class = getattr(m, "neurostimml_classification", "model_not_installed")
    ml_proba = getattr(m, "neurostimml_probability", float("nan"))
    if ml_class == "likely_damaging" and math.isfinite(ml_proba):
        criteria.append((
            "neurostimml",
            f"NeurostimML probability of damage = {ml_proba:.2f}"))

    if not criteria:
        return None

    env_posture = _resolve_posture(environment_short)
    try:
        from .environments import display_name_for
        env_label = display_name_for(environment_short)
    except Exception:
        env_label = environment_short or "(unknown)"

    # Suppress per-capture spam in info environments — the per-
    # capture metric table already shows the verdict; logging it
    # for every capture in a 50-step ramp would just be noise.
    if env_posture == "info":
        return None

    # In warn / alert environments, escalate a multi-criterion
    # firing one tier (matches assess_planned_run).
    base_posture = env_posture
    if len(criteria) >= 2:
        try:
            idx = ("info", "warn", "alert").index(base_posture)
            base_posture = ("info", "warn", "alert")[min(idx + 1, 2)]
        except ValueError:
            base_posture = "warn"

    cap_idx = getattr(capture, "index", "?")
    title = (f"Capture #{cap_idx} damage warning"
             if base_posture == "warn"
             else f"Capture #{cap_idx} DAMAGE ALERT")
    body_lines: List[str] = []
    body_lines.append(
        f"Capture #{cap_idx} flagged in {env_label} preparation:")
    for _short, prose in criteria:
        body_lines.append(f"  • {prose}")
    body = "\n".join(body_lines)

    return Warning(
        level=base_posture,
        title=title,
        body=body,
        criteria=criteria,
        should_block=False,  # capture is already done — never block
        environment_short=environment_short,
    )


__all__ = [
    "Warning",
    "assess_planned_run",
    "assess_finished_capture",
]
