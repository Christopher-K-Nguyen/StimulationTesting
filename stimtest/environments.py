"""Experimental-environment presets + warning posture.

The user picks an Environment on the Setup tab — what's actually
being stimulated when the runner kicks off a sweep. The choices
range from passive bench-test buffers (PBS, mISF, aCSF) through
ex-vivo tissue (acute slices, retina explants) to in-vivo
preparations (rat cortex, NHP cochlea, human DBS). The choice
doesn't change which features feed the NeurostimML / Shannon
classifiers — the published model has no ``Environment`` feature
— but it DOES change how seriously the user wants to be warned
when those classifiers say "likely damaging".

Three warning postures
======================

``info``   — informational only.  Bench-test buffers (PBS, mISF, etc.)
              don't carry living tissue, so a "Shannon says damaging"
              verdict is a useful screening readout but not a
              call-to-action; we surface it as a single line in the
              log pane and never block the run.

``warn``   — moderate alert.  Ex-vivo tissue, custom / unknown
              environment.  The runner shows a confirmation dialog
              before starting and logs each crossing capture as a
              warning; default action on the dialog is *Cancel*.

``alert``  — strong alert.  In-vivo preparations (rodent / large
              animal / human).  The pre-run dialog uses the warning
              icon, lists every flagged criterion with prose, and
              defaults to *Cancel* with a deliberate "I understand"
              acknowledgement before *Continue*.

Why a fixed preset list instead of free-form?
    The supp database categorises every entry by Animal_model +
    Nervous_system, and mapping the preset short-codes onto those
    fields cleanly is what lets us give a posture without asking
    the user to encode it themselves.  The user can still pick
    ``custom`` and fill in a free-form description; the runner
    treats Custom as ``warn`` since it's safer to be loud than
    silent in unfamiliar territory.

Adding a new preset
    Append a new ``EnvironmentPreset`` to :data:`ENVIRONMENT_PRESETS`.
    Keep the short_code stable across releases — it's persisted in
    the user's prefs and stamped into saved sessions, so renaming
    forfeits old data.  ``category`` is a coarse bucket the
    contribute-data dialog uses to anonymize uploads; ``posture``
    drives the warning loudness.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Posture levels
# ---------------------------------------------------------------------------

#: Informational posture — log only, no pre-run dialog.  Use for
#: bench-test buffers where damage isn't a live concern.
POSTURE_INFO = "info"

#: Moderate-alert posture — pre-run dialog with Continue / Cancel
#: (default Cancel), per-capture warning log lines.
POSTURE_WARN = "warn"

#: Strong-alert posture — pre-run dialog with explicit acknowledge
#: button, per-capture warning log lines styled red.
POSTURE_ALERT = "alert"

#: Ordering used for "did anything escalate?" comparisons.
#: Higher index = more severe.  Used by :func:`merge_postures`.
POSTURE_ORDER = (POSTURE_INFO, POSTURE_WARN, POSTURE_ALERT)


def merge_postures(*postures: str) -> str:
    """Return the most-severe posture among ``postures``.

    Used when more than one signal contributes to the warning
    level — e.g. environment is ``warn`` but Shannon AND
    NeurostimML both fired, so the synthesis layer wants to bump
    to ``alert``.  Unknown postures default to ``warn`` so a
    typo'd config never silences a warning by accident.
    """
    worst_idx = 0
    for p in postures:
        try:
            idx = POSTURE_ORDER.index(p)
        except ValueError:
            idx = POSTURE_ORDER.index(POSTURE_WARN)
        worst_idx = max(worst_idx, idx)
    return POSTURE_ORDER[worst_idx]


# ---------------------------------------------------------------------------
# Category buckets — coarse classification used by the contribute
# dialog (we anonymise uploads at the category level, not the
# preset level, so a "rat cortex" upload doesn't single out a
# specific lab's preferred preparation).
# ---------------------------------------------------------------------------

CATEGORY_IN_VITRO_BUFFER = "in_vitro_buffer"
CATEGORY_IN_VITRO_CULTURE = "in_vitro_culture"
CATEGORY_EX_VIVO = "ex_vivo"
CATEGORY_IN_VIVO_RODENT = "in_vivo_rodent"
CATEGORY_IN_VIVO_LARGE_ANIMAL = "in_vivo_large_animal"
CATEGORY_IN_VIVO_HUMAN = "in_vivo_human"
CATEGORY_CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Preset record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnvironmentPreset:
    """One row in :data:`ENVIRONMENT_PRESETS`.

    Attributes
    ----------
    short_code : str
        Stable identifier persisted in prefs and saved sessions.
        Lowercase, underscored, no spaces.  Don't rename across
        releases — old sessions / contributed uploads use the value.
    display_name : str
        Human-readable label shown in the Setup-tab dropdown and
        any rendered warning body.
    category : str
        High-level bucket (one of the ``CATEGORY_*`` constants).
        Coarse enough that anonymised uploads don't reveal lab-
        specific preparations.
    posture : str
        Default warning posture for stimulation in this
        environment — one of ``POSTURE_INFO`` / ``POSTURE_WARN``
        / ``POSTURE_ALERT``.
    description : str
        One-line prose surfaced as a tooltip in the Setup-tab
        combo so the user knows which entry matches their setup
        without having to look it up.
    """
    short_code: str
    display_name: str
    category: str
    posture: str
    description: str


# ---------------------------------------------------------------------------
# Preset list — the canonical Environment options
# ---------------------------------------------------------------------------

#: All preset environments shown in the Setup-tab dropdown, in
#: display order.  The list is grouped roughly by posture (info →
#: alert) so users scrolling the combo see the safest options
#: first and the most-restricted options last; a final
#: ``Custom`` entry lets a user describe an environment that
#: doesn't match any preset.
ENVIRONMENT_PRESETS: Tuple[EnvironmentPreset, ...] = (
    # --- in-vitro buffers (info posture) ----------------------------------
    EnvironmentPreset(
        short_code="pbs",
        display_name="PBS (phosphate-buffered saline)",
        category=CATEGORY_IN_VITRO_BUFFER,
        posture=POSTURE_INFO,
        description="Standard 1× PBS at room temperature; pH ≈ 7.4. "
                    "Common for benchtop electrochemistry on "
                    "non-tissue substrates."),
    EnvironmentPreset(
        short_code="misf",
        display_name="mISF (modified interstitial fluid)",
        category=CATEGORY_IN_VITRO_BUFFER,
        posture=POSTURE_INFO,
        description="Modified interstitial fluid mimicking in-vivo "
                    "extracellular ionic composition. Used for "
                    "chronic-stress benchtop testing."),
    EnvironmentPreset(
        short_code="acsf",
        display_name="aCSF (artificial cerebrospinal fluid)",
        category=CATEGORY_IN_VITRO_BUFFER,
        posture=POSTURE_INFO,
        description="Artificial CSF; mimics brain extracellular "
                    "fluid for cortical / spinal cord work."),
    EnvironmentPreset(
        short_code="hbss",
        display_name="HBSS (Hanks' balanced salt solution)",
        category=CATEGORY_IN_VITRO_BUFFER,
        posture=POSTURE_INFO,
        description="Hanks' balanced salt solution; common buffer "
                    "for cell-culture-adjacent experiments."),
    EnvironmentPreset(
        short_code="saline_09",
        display_name="0.9% NaCl saline",
        category=CATEGORY_IN_VITRO_BUFFER,
        posture=POSTURE_INFO,
        description="Plain isotonic saline. Conductivity-match "
                    "for many tissue-equivalent benchtop tests."),
    # --- in-vitro culture (info posture) ----------------------------------
    EnvironmentPreset(
        short_code="cell_culture",
        display_name="Cell culture (DMEM / Neurobasal / etc.)",
        category=CATEGORY_IN_VITRO_CULTURE,
        posture=POSTURE_INFO,
        description="Cultured cells in growth medium. Damage "
                    "thresholds are unrelated to the in-vivo "
                    "literature; treat predictions as advisory."),
    # --- ex-vivo (warn posture) -------------------------------------------
    EnvironmentPreset(
        short_code="ex_vivo_brain_slice",
        display_name="Ex vivo brain slice (acute)",
        category=CATEGORY_EX_VIVO,
        posture=POSTURE_WARN,
        description="Acute brain slice in chamber. Tissue is "
                    "live but isolated; warning posture mirrors "
                    "in-vivo since damage is observable."),
    EnvironmentPreset(
        short_code="ex_vivo_retina",
        display_name="Ex vivo retina explant",
        category=CATEGORY_EX_VIVO,
        posture=POSTURE_WARN,
        description="Retinal explant. Visual-prosthesis-style "
                    "preparation; treat predictions seriously."),
    EnvironmentPreset(
        short_code="ex_vivo_other",
        display_name="Other ex-vivo preparation",
        category=CATEGORY_EX_VIVO,
        posture=POSTURE_WARN,
        description="Any ex-vivo tissue preparation not matching "
                    "the listed presets."),
    # --- in-vivo rodent (alert posture) -----------------------------------
    EnvironmentPreset(
        short_code="rat_cortex",
        display_name="Rat cortex (in vivo)",
        category=CATEGORY_IN_VIVO_RODENT,
        posture=POSTURE_ALERT,
        description="Live rat cortical stimulation. Cross-check "
                    "any 'likely damaging' verdict against the "
                    "McCreery / Agnew literature."),
    EnvironmentPreset(
        short_code="rat_sciatic",
        display_name="Rat sciatic nerve (in vivo)",
        category=CATEGORY_IN_VIVO_RODENT,
        posture=POSTURE_ALERT,
        description="Live rat peripheral nerve stimulation. "
                    "Same alert posture as cortex."),
    EnvironmentPreset(
        short_code="rat_spinal_cord",
        display_name="Rat spinal cord (in vivo)",
        category=CATEGORY_IN_VIVO_RODENT,
        posture=POSTURE_ALERT,
        description="Live rat spinal-cord stimulation."),
    EnvironmentPreset(
        short_code="mouse_cortex",
        display_name="Mouse cortex (in vivo)",
        category=CATEGORY_IN_VIVO_RODENT,
        posture=POSTURE_ALERT,
        description="Live mouse cortical stimulation."),
    EnvironmentPreset(
        short_code="mouse_other",
        display_name="Mouse — other target (in vivo)",
        category=CATEGORY_IN_VIVO_RODENT,
        posture=POSTURE_ALERT,
        description="Live mouse stimulation, target other than cortex."),
    # --- in-vivo large animal (alert posture) -----------------------------
    EnvironmentPreset(
        short_code="cat_cortex",
        display_name="Cat cortex (in vivo)",
        category=CATEGORY_IN_VIVO_LARGE_ANIMAL,
        posture=POSTURE_ALERT,
        description="The cat cortex preparation Shannon (1992) "
                    "originally used to draw the k = 1.85 boundary."),
    EnvironmentPreset(
        short_code="cat_cochlea",
        display_name="Cat cochlea (in vivo)",
        category=CATEGORY_IN_VIVO_LARGE_ANIMAL,
        posture=POSTURE_ALERT,
        description="Cat intracochlear stimulation."),
    EnvironmentPreset(
        short_code="nhp_cortex",
        display_name="NHP cortex (in vivo)",
        category=CATEGORY_IN_VIVO_LARGE_ANIMAL,
        posture=POSTURE_ALERT,
        description="Non-human-primate cortical stimulation."),
    EnvironmentPreset(
        short_code="nhp_other",
        display_name="NHP — other target (in vivo)",
        category=CATEGORY_IN_VIVO_LARGE_ANIMAL,
        posture=POSTURE_ALERT,
        description="Non-human-primate stimulation, target other "
                    "than cortex."),
    EnvironmentPreset(
        short_code="pig_spinal",
        display_name="Pig spinal cord (in vivo)",
        category=CATEGORY_IN_VIVO_LARGE_ANIMAL,
        posture=POSTURE_ALERT,
        description="Pig spinal-cord stimulation."),
    # --- in-vivo human (alert posture) ------------------------------------
    EnvironmentPreset(
        short_code="human_dbs",
        display_name="Human DBS",
        category=CATEGORY_IN_VIVO_HUMAN,
        posture=POSTURE_ALERT,
        description="Human deep-brain stimulation (clinical or "
                    "research). Cross-check against FDA limits."),
    EnvironmentPreset(
        short_code="human_scs",
        display_name="Human SCS",
        category=CATEGORY_IN_VIVO_HUMAN,
        posture=POSTURE_ALERT,
        description="Human spinal-cord stimulation."),
    EnvironmentPreset(
        short_code="human_cochlear",
        display_name="Human cochlear implant",
        category=CATEGORY_IN_VIVO_HUMAN,
        posture=POSTURE_ALERT,
        description="Human cochlear-implant stimulation."),
    EnvironmentPreset(
        short_code="human_other",
        display_name="Human — other target",
        category=CATEGORY_IN_VIVO_HUMAN,
        posture=POSTURE_ALERT,
        description="Human stimulation, target other than DBS / SCS / "
                    "cochlear."),
    # --- custom (warn posture) --------------------------------------------
    EnvironmentPreset(
        short_code="custom",
        display_name="Custom (specify…)",
        category=CATEGORY_CUSTOM,
        posture=POSTURE_WARN,
        description="Environment that doesn't match any preset. "
                    "The Setup tab pops up a text field to capture "
                    "the user's free-form description."),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

# Build keyed dictionaries once at import time so the per-call
# helpers are O(1).  Overhead is the size of the preset list (~22
# entries), trivial.
_BY_SHORT: Dict[str, EnvironmentPreset] = {
    p.short_code: p for p in ENVIRONMENT_PRESETS
}

#: Default short-code seeded into a fresh prefs file.  PBS is the
#: safest "I haven't picked anything yet" choice — it's a benchtop
#: buffer, info posture, and the most common starting point for
#: new electrode characterisation work.
DEFAULT_ENVIRONMENT = "pbs"


def get_preset(short_code: str) -> Optional[EnvironmentPreset]:
    """Return the :class:`EnvironmentPreset` for ``short_code``,
    or ``None`` if the short_code isn't known.

    Used by the Setup tab to render display labels, by the
    warning synthesizer to look up the posture, and by tests.
    Stale codes from old prefs files don't crash the GUI; the
    caller treats ``None`` as "fall back to the default".
    """
    return _BY_SHORT.get(short_code)


def posture_for(short_code: str) -> str:
    """Return the warning posture (``info`` / ``warn`` / ``alert``)
    for an environment short code.

    Falls back to :data:`POSTURE_WARN` for unknown codes — safer
    to be loud than silent when the environment is unfamiliar.
    Custom environments default to ``warn`` for the same reason.
    """
    p = _BY_SHORT.get(short_code)
    return p.posture if p is not None else POSTURE_WARN


def display_name_for(short_code: str,
                     custom_text: Optional[str] = None) -> str:
    """Render a display-friendly label for ``short_code``.

    ``custom_text`` is the user's free-form description used when
    the short_code is ``"custom"``; if non-empty it's surfaced
    inside the label so a glance at the Setup tab tells the user
    what they typed.  Empty / missing custom_text falls back to
    the bare ``"Custom"`` label.
    """
    p = _BY_SHORT.get(short_code)
    if p is None:
        return short_code or "(unknown)"
    if p.short_code == "custom" and custom_text:
        return f"Custom: {custom_text.strip()}"
    return p.display_name


def is_in_vivo(short_code: str) -> bool:
    """``True`` if the chosen environment involves a live subject.

    Used by the contribute-data dialog (in-vivo data is more
    valuable for refining the public catalog) and by the
    pre-run dialog (in-vivo defaults to a stricter
    "acknowledge-before-continue" UX).
    """
    p = _BY_SHORT.get(short_code)
    if p is None:
        return False
    return p.category in (
        CATEGORY_IN_VIVO_RODENT,
        CATEGORY_IN_VIVO_LARGE_ANIMAL,
        CATEGORY_IN_VIVO_HUMAN,
    )


def all_short_codes() -> List[str]:
    """Return every preset's short_code, in display order. Used by
    the Setup tab to populate the combo and by tests.
    """
    return [p.short_code for p in ENVIRONMENT_PRESETS]


# ---------------------------------------------------------------------------
# Gas sparging — atmosphere control over the electrolyte
# ---------------------------------------------------------------------------
#
# Why this matters for tissue-damage screening:
#   Dissolved O₂ in benchtop electrolyte drives the cathodic
#   oxygen-reduction reaction at the electrode interface. That
#   reaction is irreversible at the charges typical of stimulation
#   pulses, biases the OCP measurements collected by
#   :mod:`stimtest.electrode_potential_history`, and shifts the
#   effective water-window asymmetrically (cathodic limit moves
#   positive in the presence of O₂). Sparging with an inert gas
#   (N₂ or Ar) before / during the experiment removes dissolved O₂,
#   so anyone reading a saved session needs to know which sparge
#   regime was in effect to interpret the OCPs and current-voltage
#   curves correctly.
#
# This is metadata only — sparge gas does NOT feed the Shannon
# equation, the Modified Shannon caps, or the NeurostimML model
# (none of those models was trained on a sparging feature). It IS
# stamped into ``setup_snapshot`` so saved sessions, the contribute-
# electrode-data payload, and the electrode-potential learning
# store all carry the value as provenance for cohort analysis.

#: Short code stored in prefs and ``setup_snapshot``. Stable
#: across releases — don't rename without a migration shim.
SPARGE_NONE = "none"
#: Sparging with high-purity nitrogen — the most common
#: lab-default deoxygenation gas (cheap; widely available).
SPARGE_N2 = "n2"
#: Sparging with argon — denser than N₂ so it blankets the
#: electrolyte more stably, and inert against light-induced
#: nitride chemistry on some materials. More expensive than N₂.
SPARGE_AR = "ar"

#: Default short code used when a fresh prefs file is constructed.
#: ``"none"`` = ambient atmosphere (the default state of a freshly-
#: prepared electrochemistry cell).
DEFAULT_SPARGE_GAS = SPARGE_NONE

#: All allowed sparge-gas options as ``(short_code, display_name,
#: tooltip)`` tuples. Display names use Unicode subscripts where
#: appropriate (N₂, Ar) so the combo reads naturally without
#: needing rich-text. Tooltip text explains the chemistry impact
#: so a user new to electrochemistry knows when to enable it.
SPARGE_GAS_PRESETS: Tuple[Tuple[str, str, str], ...] = (
    (SPARGE_NONE, "None (ambient air)",
     "No sparging — electrolyte equilibrated with atmospheric "
     "oxygen. The default for quick benchtop checks where the "
     "O₂-driven cathodic reaction isn't a concern."),
    (SPARGE_N2, "N₂ (nitrogen)",
     "Sparge the electrolyte with high-purity nitrogen to remove "
     "dissolved oxygen. Standard practice for chronic-stress "
     "benchtop work and for any experiment where the cathodic "
     "potential limit needs to reflect water-only reduction."),
    (SPARGE_AR, "Ar (argon)",
     "Sparge with argon. Denser than N₂ so it forms a stable "
     "blanket over the electrolyte; preferred when sparging must "
     "continue throughout long experiments without the headspace "
     "drifting back to atmosphere."),
)


def get_sparge_gas(short_code: str) -> Optional[Tuple[str, str, str]]:
    """Return the ``(short_code, display_name, tooltip)`` tuple for
    ``short_code``, or ``None`` when the code isn't recognised.

    Used by the Setup tab to render display labels and tooltips,
    and by tests. Stale codes from old prefs files don't crash
    the GUI; the caller falls back to :data:`DEFAULT_SPARGE_GAS`
    on ``None``.
    """
    for preset in SPARGE_GAS_PRESETS:
        if preset[0] == short_code:
            return preset
    return None


def sparge_display_name_for(short_code: str) -> str:
    """Human-readable label for a sparge-gas short code (used by
    the saved-session export + contribute-data payload).

    Falls back to the bare short_code if the value isn't in
    :data:`SPARGE_GAS_PRESETS` — preserves whatever the saved
    session recorded even when the preset list has shifted.
    """
    p = get_sparge_gas(short_code)
    if p is None:
        return short_code or "(unknown)"
    return p[1]


__all__ = [
    "POSTURE_INFO",
    "POSTURE_WARN",
    "POSTURE_ALERT",
    "POSTURE_ORDER",
    "merge_postures",
    "CATEGORY_IN_VITRO_BUFFER",
    "CATEGORY_IN_VITRO_CULTURE",
    "CATEGORY_EX_VIVO",
    "CATEGORY_IN_VIVO_RODENT",
    "CATEGORY_IN_VIVO_LARGE_ANIMAL",
    "CATEGORY_IN_VIVO_HUMAN",
    "CATEGORY_CUSTOM",
    "EnvironmentPreset",
    "ENVIRONMENT_PRESETS",
    "DEFAULT_ENVIRONMENT",
    "get_preset",
    "posture_for",
    "display_name_for",
    "is_in_vivo",
    "all_short_codes",
    "SPARGE_NONE",
    "SPARGE_N2",
    "SPARGE_AR",
    "DEFAULT_SPARGE_GAS",
    "SPARGE_GAS_PRESETS",
    "get_sparge_gas",
    "sparge_display_name_for",
]
