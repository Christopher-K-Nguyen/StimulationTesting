"""Tissue-damage predictive models for neural stimulation.

This module exposes the Shannon equation and the "Modified Shannon"
boundaries that are the de-facto industry baseline for screening
stimulation parameters against likely-damaging vs. likely-non-damaging
regions of the parameter space, plus a thin classification helper that
combines all three boundaries into a single per-capture verdict.

References
----------
**Shannon, R. V. (1992).** *A model of safe levels for electrical
stimulation.* IEEE Trans. Biomed. Eng. 39(4):424-426.
DOI: 10.1109/10.126616

    The original 2-D log-log boundary
    ``k = log10(Q_d) + log10(Q_ph)``  with k = 1.5–2.0 separating
    damaging from non-damaging cat-cortex stimulation. Built on
    epidural macroelectrodes with 0.01–0.5 cm² GSA. Shannon himself
    flagged in the 1992 paper that the 2-D model omits pulse rate,
    pulse duration, duty cycle, and exposure duration — i.e. the
    model is a coarse screen, not a definitive yes/no.

**McCreery, D., Pikov, V., Troyk, P. R. (2010).** *Neuronal loss
due to prolonged controlled-current stimulation with chronically
implanted microelectrodes in the cat cerebral cortex.* J. Neural
Eng. 7(3):036005. DOI: 10.1088/1741-2560/7/3/036005

    Original source of the **microelectrode 4 nC/ph cap**.
    Demonstrated that microelectrodes (GSA < 2000 µm²) can elicit
    neural-tissue damage at charge-per-phase levels well below the
    Shannon line and below the 30 µC/cm² macroelectrode cap. The
    Cogan 2016 review (next reference) consolidated this into the
    Modified Shannon framework we apply here.

**Cogan, S. F., Ludwig, K. A., Welle, C. G., Takmakov, P. (2016).**
*Tissue damage thresholds during therapeutic electrical stimulation.*
J. Neural Eng. 13(2):021001. DOI: 10.1088/1741-2560/13/2/021001

    Source of the "Modified Shannon" boundaries used here:
    macroelectrodes (GSA > 0.03 cm²) capped at 30 µC/cm²/ph
    regardless of k-value; microelectrodes (GSA < 2000 µm²)
    capped at 4 nC/ph regardless of k-value (per McCreery 2010
    above). The 30 µC/cm²/ph macro-cap matches the FDA approval
    for DBS leads on 0.06 cm² macroelectrodes.

**Vatsyayan, R., Dayeh, S. A. (2022).** *A universal model of
electrochemical safety limits in vivo for electrophysiological
stimulation.* Front. Neurosci. 16:972252.
DOI: 10.3389/fnins.2022.972252

    Modern extension of the Shannon framework that incorporates
    electrode site material, electrode size, and inter-electrode
    spacing through electrochemical-impedance and charge-injection-
    capacity terms. Per Li et al. 2024 the Vatsyayan-Dayeh model
    "still does not incorporate stimulation parameters related to
    the cumulative nature or time scale of charge delivery (e.g.
    frequency or duty cycle)" — so it sits between Shannon and the
    NeurostimML ML model. Not implemented in this module today
    (no code change here), but flagged as a concrete next
    refinement when material-aware screening is wanted.

**Li, Y., et al. (2024).** *NeurostimML: a machine learning model
for predicting neurostimulation-induced tissue damage.* J. Neural
Eng. 21(3):036054. DOI: 10.1088/1741-2552/ad593e

    Builds a 385-entry curated database from 58 publications and
    trains four ML algorithms (Logistic Regression, K-Nearest
    Neighbor, Random Forest, Multilayer Perceptron) on three
    feature sets (Shannon: 2 features; partial: 13; semi-complete:
    25). Standalone Shannon accuracy on the full database is only
    ~64% (just 14% above chance, AUC 0.64); the partial-feature-set
    Random Forest model that powers the public web portal
    ("RF-Partial-19", 19 trees) achieves **88% accuracy**, with a
    **3% false-negative rate** vs Shannon's **63–66% FNR**.

    **The most damning finding for the Shannon screen specifically:
    63–66% of damaging stimulation parameters are misclassified by
    the standalone Shannon equation as safe.** The
    :data:`SHANNON_FALSE_NEGATIVE_RATE_NOTE` constant below
    surfaces this caveat verbatim so the GUI can render it next to
    every "likely_safe" verdict.

    The most predictive features (random-forest importance scoring,
    in descending order):

    1. Waveform shape (e.g. monophasic, biphasic-balanced,
       biphasic-asymmetric, biphasic-capacitive)
    2. Daily accumulated charge
    3. Charge density per phase
    4. Charge per phase
    5. Total pulses

    A web-hosted version of the RF-Partial-19 model accepting 7
    user inputs (waveform shape, frequency, pulse width, current
    or voltage amplitude, GSA, duty cycle, stimulation duration)
    is available at:
        https://neurostimml.utdallas.edu

    A local inference path is provided in
    :mod:`stimtest.neurostimml`, which loads the same RF-Partial-19
    scikit-learn model from disk when the user runs
    ``Help → Install NeurostimML model…``. The Shannon /
    Modified-Shannon screen here always runs (no external
    dependency); the ML screen is opportunistic and reports
    "model not installed" cleanly when the pickle isn't present.

    Source data and code:
        https://github.com/UTDyxl121030/BlerisLab/tree/Neuromodulation/

    Standardized reporting recommendation from §4 of the paper —
    see :data:`RECOMMENDED_REPORT_FIELDS` for the machine-readable
    list.

Why this module is "warn-only" by default
-----------------------------------------
Per Li et al., **Shannon misclassifies 63-66% of damaging stimulation
parameters as safe** (the false-negative rate of the standalone
Shannon equation at k = 1.85). The runner side does NOT enforce a
hard-stop on Shannon classification — it would create a false sense
of safety while letting many damaging configurations through, which
is worse than no enforcement at all.

The classification we surface is therefore framed as guidance:

* ``"likely_safe"`` — both Shannon (k < 1.85) AND the modified
  caps say OK. Don't take this as proof of safety; the underlying
  model is wrong ~36% of the time.
* ``"above_shannon"`` — Shannon's k-value exceeds 1.85. The 2-D
  boundary says probably damaging.
* ``"above_macro_cap"`` — macroelectrode (GSA > 0.03 cm²) and
  charge density > 30 µC/cm²/ph. Above the FDA DBS limit and
  the modified-Shannon hard cap.
* ``"above_micro_cap"`` — microelectrode (GSA < 2000 µm²) and
  charge per phase > 4 nC/ph. Above the McCreery et al. micro-
  electrode hard cap.

When multiple criteria fire, the most conservative one wins (every
``above_*`` ranks worse than ``likely_safe``). The corresponding
status is also surfaced as a per-criterion bool dict so a downstream
caller can render multiple badges (e.g. "above Shannon AND above
macro cap") rather than collapsing to one string.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Module constants — boundary thresholds + descriptive labels
# ---------------------------------------------------------------------------

#: Shannon equation k-value at which the 1992 paper's 2-D log-log
#: boundary is conventionally drawn. Cogan et al. note the literature
#: range is 1.5–2.0; we default to 1.85 because that's the value
#: Li et al. 2024 use as their reference for accuracy comparisons,
#: matching the de-facto community convention.
SHANNON_K_THRESHOLD = 1.85

#: GSA cutoff (cm²) above which the modified-Shannon macroelectrode
#: cap applies. Cogan et al. 2016 set this at 0.03 cm² so a 0.06 cm²
#: macro DBS lead falls inside the macro band, while sub-mm² /
#: tissue-scale electrodes fall outside.
MACROELECTRODE_GSA_CM2_MIN = 0.03

#: Charge density cap (µC/cm²/ph) for macroelectrodes (GSA above
#: :data:`MACROELECTRODE_GSA_CM2_MIN`). Above this the modified
#: Shannon classifies as damaging regardless of k-value.
#: Matches the FDA DBS approval level on 0.06 cm² leads.
MACROELECTRODE_CHARGE_DENSITY_LIMIT_UC_PER_CM2 = 30.0

#: GSA cutoff (µm²) below which the modified-Shannon microelectrode
#: cap applies. Cogan et al. set this at 2000 µm² so cellular-scale
#: penetrating sites are treated separately from tissue-scale pads.
MICROELECTRODE_GSA_UM2_MAX = 2000.0

#: Charge-per-phase cap (nC/ph) for microelectrodes (GSA below
#: :data:`MICROELECTRODE_GSA_UM2_MAX`). Above this the modified
#: Shannon classifies as damaging regardless of k-value, even when
#: the charge density is low.
MICROELECTRODE_CHARGE_PER_PHASE_LIMIT_NC_PER_PHASE = 4.0

#: URL of the NeurostimML web app (Li et al. 2024). Surfaced from the
#: Help menu so a user who wants the higher-accuracy ML prediction
#: can plug their parameters in directly. Bundling the model itself
#: would require shipping scikit-learn weights the paper doesn't
#: distribute.
NEUROSTIMML_WEB_URL = "https://neurostimml.utdallas.edu"

#: DOI links for every reference in the module docstring; surfaced
#: in the Help → Tissue damage info dialog so a user can read the
#: source material in context. Kept as plain strings rather than
#: ``rich`` HTML so this module remains GUI-free.
SHANNON_1992_DOI = "https://doi.org/10.1109/10.126616"
MCCREERY_2010_DOI = "https://doi.org/10.1088/1741-2560/7/3/036005"
COGAN_2016_DOI = "https://doi.org/10.1088/1741-2560/13/2/021001"
VATSYAYAN_DAYEH_2022_DOI = "https://doi.org/10.3389/fnins.2022.972252"
LI_2024_DOI = "https://doi.org/10.1088/1741-2552/ad593e"

#: Public Bleris Lab GitHub branch carrying the trained ML pickles
#: + supplementary Excel data. Exposed so the
#: :mod:`stimtest.neurostimml` installer dialog can drop a link
#: in front of the user when fetching the model fails.
NEUROSTIMML_GITHUB_REPO = (
    "https://github.com/UTDyxl121030/BlerisLab/tree/Neuromodulation/"
)

#: Verbatim caveat about the Shannon equation's high false-
#: negative rate, used by the GUI to render a warning tooltip on
#: every "likely_safe" Shannon verdict. Sourced from §3.2 of
#: Li et al. 2024 (figure 1, confusion matrix at k = 1.85). Single
#: source so the prose stays consistent across the metric tooltip,
#: the Help dialog, and any future damage-summary report.
SHANNON_FALSE_NEGATIVE_RATE_NOTE = (
    "Per Li et al. 2024 (J. Neural Eng. 21:036054), the standalone "
    "Shannon equation misclassifies 63–66% of damaging stimulation "
    "parameters as safe at the conventional k = 1.85 threshold. "
    "Treat a 'likely safe' verdict as guidance only; cross-check "
    "with the NeurostimML web tool or the local model in "
    "stimtest.neurostimml when designing experiments."
)

# ---------------------------------------------------------------------------
# 0–4 damage-level convention
# ---------------------------------------------------------------------------

#: Discrete histological / functional damage level convention used
#: by McCreery, Shepherd, and Li et al. The binary ``damaging`` /
#: ``non_damaging`` mapping derived from this scale collapses
#: levels 0–1 into "non-damaging" and levels 2–4 into "damaging"
#: — matching the binarisation Li et al. 2024 used for their
#: machine-learning training (and the convention this module's
#: :class:`DamageAssessment` uses for ``classification`` strings).
#:
#: Stored as a tuple of ``(level, short_label, prose_description)``
#: so a downstream UI can render either form: a one-character
#: integer pill ("3") for compact summaries, or a full prose label
#: ("Moderate damage") in tooltips and reports. The long
#: descriptions echo Shepherd 2020 / Li 2024 verbatim so a paper
#: cross-reading the application stays consistent with the
#: literature's terminology.
DAMAGE_LEVELS = (
    (0, "No damage",
     "No histological evidence of damage; tissue indistinguishable "
     "from unstimulated controls."),
    (1, "Minimal damage",
     "Damage similar to that observed around implanted-but-unpulsed "
     "electrodes (insertion / encapsulation response only). Below "
     "the binary 'damaging' threshold."),
    (2, "Mild damage",
     "Histologically detectable changes attributable to stimulation, "
     "above the implanted-control baseline. Above the binary "
     "'damaging' threshold."),
    (3, "Moderate damage",
     "Clearly stimulation-attributable damage with measurable "
     "neuronal loss or tissue disruption."),
    (4, "Severe damage",
     "Extensive neural-tissue damage, including frank neuronal loss, "
     "necrosis, or substantial functional impairment."),
)

#: Convenience: index → short label.
DAMAGE_LEVEL_LABELS: Dict[int, str] = {
    level: short for (level, short, _desc) in DAMAGE_LEVELS
}

#: Convenience: index → full prose description.
DAMAGE_LEVEL_DESCRIPTIONS: Dict[int, str] = {
    level: desc for (level, _short, desc) in DAMAGE_LEVELS
}


def damage_level_from_classification(classification: str) -> Optional[int]:
    """Map a Shannon classification string to a coarse 0–4 damage
    level for cross-format compatibility with literature reports.

    Shannon + Modified-Shannon classifies into a binary "safe"
    vs "above-threshold" verdict; the literature uses the 0–4
    histological scale. We can't recover a non-binary level from a
    binary classifier, but we CAN translate the verdict into a
    plausible level so the GUI / exports can render either form:

    * ``"likely_safe"`` → level **0** (no damage). The historical
      Shannon-safe region; below the binarisation threshold.
    * ``"above_shannon"`` / ``"above_macro_cap"`` /
      ``"above_micro_cap"`` → level **2** (mild damage). The
      threshold above which Li et al. 2024 binarise into
      "damaging"; without finer-grained inputs we can't say it's
      moderate or severe.
    * ``"insufficient_data"`` → ``None`` so the caller can render
      "—" rather than a default-zero "no damage" verdict that
      could be misread as a positive safety claim.
    """
    if classification == "likely_safe":
        return 0
    if classification in ("above_shannon", "above_macro_cap",
                          "above_micro_cap"):
        return 2
    return None


# ---------------------------------------------------------------------------
# Recommended-reporting fields from Li et al. 2024 §4 Discussion
# ---------------------------------------------------------------------------

#: Stimulation parameters that Li et al. recommend every neural-
#: stimulation publication explicitly disclose. We surface this as
#: a constant so the Setup tab can warn (non-blocking) when the
#: user's session metadata is missing one of these — encouraging
#: better reproducibility without dictating workflow. Each entry
#: is ``(short_key, prose_label)`` where ``short_key`` matches the
#: setup-snapshot field name when one already exists in the
#: codebase, so a downstream check can do a simple ``if
#: snap.get(key)`` lookup.
RECOMMENDED_REPORT_FIELDS = (
    ("waveform_type",
     "Waveform type (mono- / biphasic-balanced / biphasic-asymmetric / "
     "biphasic-capacitive)"),
    ("frequency_hz",
     "Stimulation frequency (Hz)"),
    ("pulse_width_us",
     "Leading-phase pulse width (µs)"),
    ("control_mode",
     "Whether stimulation was current-controlled or voltage-controlled"),
    ("amplitude",
     "Pulse amplitude (current in µA / voltage in V)"),
    ("gsa",
     "Geometric surface area of the stimulating electrode (µm² or cm²)"),
    ("duty_cycle",
     "Duty cycle, OR pulse-train duration plus the inter-train rest interval"),
    ("daily_stim_duration",
     "Total stimulation duration per day (s or h)"),
    ("days_stimulated",
     "Number of days the subject received stimulation"),
    ("electrode_shape",
     "Geometric shape of the stimulating electrode"),
    ("electrode_material",
     "Stimulating-electrode material / coating"),
)


# ---------------------------------------------------------------------------
# Classification dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DamageAssessment:
    """Per-capture tissue-damage screening verdict.

    Returned by :func:`assess_capture` so a single call delivers all
    the boundary checks a downstream UI needs to render. Frozen so
    a Capture's metric set can hold one immutably.

    Attributes
    ----------
    k_value : float
        Shannon equation result (``log10(Q_d) + log10(Q_ph)``) for
        the leading phase, with charge density in µC/cm²/ph and
        charge in µC/ph. NaN when either input is non-positive
        (Shannon's logarithm is undefined for zero-charge captures
        or pre-acquisition empties).
    above_shannon : bool
        ``k_value >= SHANNON_K_THRESHOLD``. NaN k-values count as
        ``False`` because we have no evidence either way.
    above_macro_cap : bool
        Charge density exceeds the macroelectrode cap and the
        electrode is in the macro band (GSA above the cutoff).
        ``False`` for microelectrodes regardless of charge density.
    above_micro_cap : bool
        Charge per phase exceeds the microelectrode cap and the
        electrode is in the micro band (GSA below the cutoff).
        ``False`` for macroelectrodes regardless of charge per phase.
    classification : str
        Single most-conservative verdict for compact UI display.
        One of: ``"likely_safe"``, ``"above_shannon"``,
        ``"above_macro_cap"``, ``"above_micro_cap"``,
        ``"insufficient_data"`` (when the inputs were non-finite).
    band : str
        ``"macro"`` (GSA > 0.03 cm²), ``"micro"`` (GSA < 2000 µm²),
        or ``"meso"`` (between the two cutoffs — neither modified
        cap applies; only the Shannon k-value gates the result).
    """
    k_value: float
    above_shannon: bool
    above_macro_cap: bool
    above_micro_cap: bool
    classification: str
    band: str


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def shannon_k(charge_per_phase_uc: float,
              charge_density_uc_per_cm2: float) -> float:
    """Compute the Shannon equation k-value.

    ``k = log10(charge_density) + log10(charge_per_phase)`` with the
    units the 1992 paper used: µC/cm²/ph and µC/ph respectively.

    Parameters
    ----------
    charge_per_phase_uc : float
        Charge in the leading phase of the stimulus pulse (µC/ph).
        For a rectangular biphasic pulse this is
        ``|amplitude_µA| × phase_width_µs × 1e-6``.
    charge_density_uc_per_cm2 : float
        Same charge divided by the geometric surface area of the
        active electrode (µC/cm²/ph).

    Returns
    -------
    float
        The k-value, or NaN when either input is non-positive (the
        log of a non-positive number is undefined).

    Notes
    -----
    Both inputs are expected to be POSITIVE — the Shannon model
    treats biphasic pulses by their leading-phase charge, so the
    polarity is irrelevant. Callers should pass absolute values.
    The metrics module exposes ``charge_per_phase_nc`` (nC, signed)
    and ``charge_injection_mc_per_cm2`` (mC/cm², unsigned); the
    helper :func:`shannon_k_from_capture_metrics` below converts
    units and signs in one place.
    """
    try:
        q_ph = float(charge_per_phase_uc)
        q_d = float(charge_density_uc_per_cm2)
    except (TypeError, ValueError):
        return float("nan")
    # The Shannon model is defined for FINITE positive magnitudes of
    # the leading-phase charge and density. A zero, negative, NaN, or
    # infinite input means we have no usable measurement (e.g. the
    # scope missed the trigger and Q_inj came out as 0, or the user
    # passed a sentinel value); returning NaN propagates the "no
    # evidence" state cleanly through the rest of the pipeline. We
    # explicitly reject ``inf`` here rather than letting ``log10(inf)``
    # produce ``+inf`` because downstream UI checks
    # ``math.isfinite(k_value)`` to gate display, and an infinite
    # k-value would look like a real (extreme) measurement.
    if not (math.isfinite(q_ph) and math.isfinite(q_d)):
        return float("nan")
    if q_ph <= 0.0 or q_d <= 0.0:
        return float("nan")
    return math.log10(q_d) + math.log10(q_ph)


def shannon_k_from_capture_metrics(charge_per_phase_nc: float,
                                   charge_injection_mc_per_cm2: float
                                   ) -> float:
    """Adapter: compute the Shannon k from the units the metrics
    module emits.

    The metrics layer stores ``charge_per_phase_nc`` (signed nC) and
    ``charge_injection_mc_per_cm2`` (unsigned mC/cm²) — neither of
    those matches the Shannon paper's µC/ph + µC/cm²/ph units, so
    this helper does the conversion + absolute value step in one
    place. Used by :func:`compute_metrics` so the math stays out of
    the metrics module proper.
    """
    try:
        q_ph_nc = abs(float(charge_per_phase_nc))
        q_d_mc_per_cm2 = abs(float(charge_injection_mc_per_cm2))
    except (TypeError, ValueError):
        return float("nan")
    # nC → µC: divide by 1000.  mC/cm² → µC/cm²: multiply by 1000.
    q_ph_uc = q_ph_nc / 1000.0
    q_d_uc_per_cm2 = q_d_mc_per_cm2 * 1000.0
    return shannon_k(q_ph_uc, q_d_uc_per_cm2)


def classify_band(gsa_cm2: float) -> str:
    """Return ``"macro"``, ``"micro"``, or ``"meso"`` for the GSA.

    ``"macro"`` ↔ GSA > 0.03 cm² → modified-Shannon macro cap
    applies. ``"micro"`` ↔ GSA < 2000 µm² (= 2e-5 cm²) → modified-
    Shannon micro cap applies. ``"meso"`` ↔ in between, where
    neither modified-Shannon cap applies and only the k-value gates
    the result. Non-positive / NaN GSA returns ``"meso"`` as a
    safe default (no caps applied; only Shannon).
    """
    try:
        gsa = float(gsa_cm2)
    except (TypeError, ValueError):
        return "meso"
    if not math.isfinite(gsa) or gsa <= 0.0:
        return "meso"
    micro_cutoff_cm2 = MICROELECTRODE_GSA_UM2_MAX * 1e-8  # µm² → cm²
    if gsa < micro_cutoff_cm2:
        return "micro"
    if gsa > MACROELECTRODE_GSA_CM2_MIN:
        return "macro"
    return "meso"


def assess_capture(*,
                   charge_per_phase_uc: float,
                   charge_density_uc_per_cm2: float,
                   gsa_cm2: float,
                   k_threshold: float = SHANNON_K_THRESHOLD,
                   ) -> DamageAssessment:
    """Run the Shannon + Modified Shannon screen on one capture.

    Parameters
    ----------
    charge_per_phase_uc : float
        Charge in the leading phase, µC/ph (use absolute value;
        polarity doesn't matter to Shannon).
    charge_density_uc_per_cm2 : float
        Same charge divided by GSA, µC/cm²/ph.
    gsa_cm2 : float
        Geometric surface area of the active electrode, cm². Used
        by :func:`classify_band` to decide which modified-Shannon
        cap applies.
    k_threshold : float, optional
        Shannon k-value threshold. Defaults to
        :data:`SHANNON_K_THRESHOLD` (1.85). Pass 1.5 for the
        conservative end of Shannon's original range.

    Returns
    -------
    DamageAssessment
        Frozen dataclass with the k-value, the per-criterion bools,
        a single most-conservative ``classification`` string for
        compact UI display, and the electrode band so the UI can
        explain WHY each cap did or didn't fire.

    Notes
    -----
    Designed to be called from :func:`stimtest.metrics.compute_metrics`
    once per capture; the GSA comes from the capture's surface area
    parameter (the metrics layer already takes it).
    """
    k_val = shannon_k(charge_per_phase_uc, charge_density_uc_per_cm2)
    band = classify_band(gsa_cm2)

    # Per-criterion checks. NaN k counts as "not above" so we don't
    # spuriously flag empty captures as damaging.
    above_shannon = (
        math.isfinite(k_val) and k_val >= k_threshold
    )
    # Modified caps are band-gated. Macro-cap only applies in the
    # macro band; micro-cap only applies in the micro band. The
    # "meso" band gets only the Shannon check.
    above_macro_cap = False
    above_micro_cap = False
    try:
        q_d = float(charge_density_uc_per_cm2)
        q_ph = float(charge_per_phase_uc)
    except (TypeError, ValueError):
        q_d = float("nan")
        q_ph = float("nan")
    if band == "macro" and math.isfinite(q_d):
        above_macro_cap = q_d > MACROELECTRODE_CHARGE_DENSITY_LIMIT_UC_PER_CM2
    if band == "micro" and math.isfinite(q_ph):
        # The micro-cap is in nC/ph; convert from the µC/ph input.
        q_ph_nc = q_ph * 1000.0
        above_micro_cap = q_ph_nc > MICROELECTRODE_CHARGE_PER_PHASE_LIMIT_NC_PER_PHASE

    # Single-string verdict for compact display. Order matters: we
    # report the most "specific" boundary first so a UI that shows
    # only one badge picks up the most actionable information.
    # ``insufficient_data`` is reserved for the all-NaN / empty
    # capture case so the user sees "no verdict" instead of a
    # default-False "likely safe".
    if not math.isfinite(k_val):
        classification = "insufficient_data"
    elif above_micro_cap:
        classification = "above_micro_cap"
    elif above_macro_cap:
        classification = "above_macro_cap"
    elif above_shannon:
        classification = "above_shannon"
    else:
        classification = "likely_safe"

    return DamageAssessment(
        k_value=float(k_val),
        above_shannon=bool(above_shannon),
        above_macro_cap=bool(above_macro_cap),
        above_micro_cap=bool(above_micro_cap),
        classification=classification,
        band=band,
    )


def assess_from_capture_metrics(charge_per_phase_nc: float,
                                charge_injection_mc_per_cm2: float,
                                surface_area_um2: float,
                                k_threshold: float = SHANNON_K_THRESHOLD,
                                ) -> DamageAssessment:
    """Adapter that takes the units :class:`CaptureMetrics` already
    stores and returns a :class:`DamageAssessment`.

    The metrics module emits charge per phase in nC (signed) and
    charge injection in mC/cm² (unsigned); GSA is passed in µm².
    This wrapper does the conversions in one place so callers don't
    sprinkle ``* 1000`` / ``* 1e-8`` factors throughout.
    """
    # nC → µC, take absolute value (polarity doesn't matter).
    q_ph_uc = abs(float(charge_per_phase_nc)) / 1000.0
    # mC/cm² → µC/cm². The metrics layer already returned an
    # unsigned magnitude so no abs() needed, but we still defend
    # against a stray sign just in case.
    q_d_uc_per_cm2 = abs(float(charge_injection_mc_per_cm2)) * 1000.0
    # µm² → cm² (1 cm² = 1e8 µm²)
    try:
        gsa_cm2 = float(surface_area_um2) * 1e-8
    except (TypeError, ValueError):
        gsa_cm2 = float("nan")
    return assess_capture(
        charge_per_phase_uc=q_ph_uc,
        charge_density_uc_per_cm2=q_d_uc_per_cm2,
        gsa_cm2=gsa_cm2,
        k_threshold=k_threshold,
    )


# ---------------------------------------------------------------------------
# Human-readable label / explanation helpers
# ---------------------------------------------------------------------------

#: Short labels for each ``DamageAssessment.classification`` value.
#: Plain text (no markup) so the UI can wrap them in whatever
#: rich-text container it likes — :func:`classification_html_badge`
#: below adds the colour styling for HTML labels.
CLASSIFICATION_LABELS: Dict[str, str] = {
    "likely_safe":       "Likely safe (Shannon)",
    "above_shannon":     "Above Shannon limit",
    "above_macro_cap":   "Above macroelectrode cap",
    "above_micro_cap":   "Above microelectrode cap",
    "insufficient_data": "Insufficient data",
}

#: Okabe-Ito colour-blind-safe palette already used elsewhere in
#: the GUI. ``likely_safe`` → green, ``above_*`` → vermilion,
#: ``insufficient_data`` → grey. Kept here so the GUI side doesn't
#: have to grow a parallel mapping.
CLASSIFICATION_COLORS: Dict[str, str] = {
    "likely_safe":       "#009E73",
    "above_shannon":     "#D55E00",
    "above_macro_cap":   "#D55E00",
    "above_micro_cap":   "#D55E00",
    "insufficient_data": "#777777",
}


def classification_html_badge(classification: str) -> str:
    """Render a small inline-HTML badge for a classification label.

    Returns a ``<span style="color: …">…</span>`` that the Viewer's
    metric table can drop straight into a QTableWidgetItem text. The
    metric table renders as plain text by default (no rich-text
    flag), so callers wanting the colour pill must enable it
    explicitly per row — see ``viewer.py`` for the wiring.
    """
    label = CLASSIFICATION_LABELS.get(classification, classification)
    color = CLASSIFICATION_COLORS.get(classification, "#000")
    return f"<span style='color: {color}; font-weight: bold;'>{label}</span>"


def classification_explanation(classification: str) -> str:
    """Return a short prose explanation of a classification verdict.

    Used by the Viewer's metric tooltip + the Help → Tissue damage
    info dialog. Keeps the rationale next to the result so a user
    who's seeing the verdict for the first time understands what
    "above macro cap" actually means.
    """
    return {
        "likely_safe": (
            "Both Shannon's 2-D boundary (k < {k_thresh:.2f}) and the "
            "modified-Shannon macro/micro caps say this capture is in "
            "the historically-safe region of the parameter space.\n\n"
            "{fnr_note}"
        ).format(k_thresh=SHANNON_K_THRESHOLD,
                 fnr_note=SHANNON_FALSE_NEGATIVE_RATE_NOTE),
        "above_shannon": (
            "Shannon's k-value exceeds the {k_thresh:.2f} threshold. "
            "The 1992 model classifies this as likely damaging. The "
            "Shannon model alone has ~64% accuracy (Li et al. 2024); "
            "consider the NeurostimML web tool for a higher-accuracy "
            "prediction with more features."
        ).format(k_thresh=SHANNON_K_THRESHOLD),
        "above_macro_cap": (
            "Macroelectrode (GSA > {gsa:.3f} cm²) with charge density "
            "above {cap:.0f} µC/cm²/ph. The modified-Shannon "
            "macroelectrode cap (Cogan et al. 2016 / FDA DBS limit) "
            "marks this as likely damaging regardless of k-value."
        ).format(gsa=MACROELECTRODE_GSA_CM2_MIN,
                 cap=MACROELECTRODE_CHARGE_DENSITY_LIMIT_UC_PER_CM2),
        "above_micro_cap": (
            "Microelectrode (GSA < {gsa:.0f} µm²) with charge per "
            "phase above {cap:.0f} nC/ph. The modified-Shannon "
            "microelectrode cap (Cogan et al. 2016) marks this as "
            "likely damaging regardless of charge density — micro-"
            "electrodes can damage tissue below the Shannon line."
        ).format(gsa=MICROELECTRODE_GSA_UM2_MAX,
                 cap=MICROELECTRODE_CHARGE_PER_PHASE_LIMIT_NC_PER_PHASE),
        "insufficient_data": (
            "Charge or charge density was non-positive — most likely "
            "the capture frame missed the active pulse. No Shannon "
            "k-value could be computed."
        ),
    }.get(classification, "")


__all__ = [
    "SHANNON_K_THRESHOLD",
    "MACROELECTRODE_GSA_CM2_MIN",
    "MACROELECTRODE_CHARGE_DENSITY_LIMIT_UC_PER_CM2",
    "MICROELECTRODE_GSA_UM2_MAX",
    "MICROELECTRODE_CHARGE_PER_PHASE_LIMIT_NC_PER_PHASE",
    "NEUROSTIMML_WEB_URL",
    "NEUROSTIMML_GITHUB_REPO",
    "SHANNON_1992_DOI",
    "MCCREERY_2010_DOI",
    "COGAN_2016_DOI",
    "VATSYAYAN_DAYEH_2022_DOI",
    "LI_2024_DOI",
    "SHANNON_FALSE_NEGATIVE_RATE_NOTE",
    "DAMAGE_LEVELS",
    "DAMAGE_LEVEL_LABELS",
    "DAMAGE_LEVEL_DESCRIPTIONS",
    "RECOMMENDED_REPORT_FIELDS",
    "DamageAssessment",
    "shannon_k",
    "shannon_k_from_capture_metrics",
    "classify_band",
    "assess_capture",
    "assess_from_capture_metrics",
    "damage_level_from_classification",
    "CLASSIFICATION_LABELS",
    "CLASSIFICATION_COLORS",
    "classification_html_badge",
    "classification_explanation",
]
