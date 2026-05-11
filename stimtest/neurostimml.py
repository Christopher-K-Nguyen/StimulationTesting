"""Local inference for the NeurostimML Random-Forest tissue-damage model.

Loads and runs the **RF-Partial-19** classifier from Li et al. 2024
(``J. Neural Eng. 21:036054``) — a 19-tree scikit-learn RandomForest
trained on the 13-feature partial set (16 columns after one-hot
encoding of ``Waveform_type``) — against any per-capture stimulation
parameters this codebase already records.

Why local inference, not the web tool only?
    The web tool at ``https://neurostimml.utdallas.edu`` accepts the
    same RF-Partial-19 model but requires the user to type each
    capture's parameters by hand. We already have all 16 features
    available in :class:`stimtest.session.Capture` (or derivable from
    the pattern + acquisition rate + surface area), so wiring local
    inference means every capture gets the higher-accuracy ML
    verdict alongside the Shannon screen — for free, in batch.

Model provenance + trust posture
--------------------------------
The ``.pkl`` file is downloaded by the user via
``Help → Install NeurostimML model…`` from the public Bleris Lab
GitHub repository
(:data:`stimtest.damage_models.NEUROSTIMML_GITHUB_REPO`). This module
**does not** auto-download or auto-update the model; it only loads
what's already on disk. Pickle deserialisation runs arbitrary code,
so:

* The install path is gated on the user clicking through a
  warn-and-confirm dialog that surfaces the source URL +
  SHA256 fingerprint.
* The on-disk path lives under :func:`model_path`, which resolves
  to the user's stimtest prefs directory — sandboxed away from the
  rest of the install tree so an inadvertent corruption of the
  pickle doesn't take down the whole application.
* A feature-vector adapter sanitises every input before passing it
  to ``predict_proba``, so a ``NaN`` capture or a missing pulse
  rate produces a clean ``None`` rather than a stack trace.

Feature set
-----------
Per ``randomForest_training.py`` in the upstream repo (and confirmed
verbatim against **Supplementary Table 5** + the
**Supplementary_Data2.xlsx** training-set headers), the model expects
exactly 16 columns in this order (the four ``Waveform_type_*``
columns are one-hot encoded; only one is 1.0 per row):

    [Waveform_type_biphasic_asymmetric,    # one-hot
     Waveform_type_biphasic_balanced,      # one-hot
     Waveform_type_biphasic_capacitive,    # one-hot
     Waveform_type_monophasic,             # one-hot
     GSA,                                  # µm²
     Pulse_width,                          # µs
     Frequency,                            # Hz
     Current,                              # µA (absolute |leading phase|)
     Voltage,                              # mV
     Charge_per_phase,                     # nC/phase (absolute value)
     Charge_density,                       # µC/cm² per phase
     Current_density,                      # mA/cm²
     stim_on,                              # seconds per pulse-train ON
     stim_total_day,                       # seconds per day
     daily_pulses,                         # pulses per day
     daily_accumulated_charge]             # Coulombs per day

These are the same units the upstream ``Supplementary_Data1.xlsx``
uses (column 12 = ``GSA (µm²)``, column 19 = ``Charge_per_phase
(nC/ph)``, column 20 = ``Charge_density_per_phase (µC/cm²/ph)``,
…); see :data:`SUPP_TABLE_COLUMN_UNITS` below for the verbatim
header → unit mapping. The adapter in :func:`build_feature_vector`
does the conversion from the units :class:`Capture` records
natively (``charge_per_phase_nc``, ``charge_injection_mc_per_cm2``,
``surface_area_um2``, etc.) before any inference call.

**Naming gotcha**: Supplementary Table 5 (the partial-set field
list in the published supplements) labels the time-aggregate
features ``Stim_time_daily`` / ``Daily_pulses_count``, while the
training script that produced the .pkl model uses
``stim_total_day`` / ``daily_pulses``. Same fields, different
spellings — the model expects the training-script names, which is
what :data:`FEATURE_ORDER` uses. The Supp-Table-5 form lives in
:data:`PARTIAL_FEATURE_SET_SUPP_NAMES` for cross-reference only.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Filename the installer dialog drops the model under, inside the
#: prefs directory. Pinned to the upstream paper's name so a user
#: who downloaded the file manually from GitHub can move it into
#: place without renaming.
MODEL_FILENAME = "RF-Partial-19.pkl"

#: Public URL the installer fetches the model from. Hosted on the
#: paper's data repo on GitHub. We use ``raw.githubusercontent.com``
#: (not ``github.com/.../blob/...``) so the bytes downloaded are the
#: actual binary file, not the HTML wrapper.
MODEL_DOWNLOAD_URL = (
    "https://raw.githubusercontent.com/UTDyxl121030/BlerisLab/"
    "Neuromodulation/Supplementary_Scripts/RF-Partial-19.pkl"
)

#: Maximum allowed pickle size in bytes. The actual upstream pickle
#: is well under 1 MB; we cap at 5 MB so a corrupt or attacker-
#: replaced URL serving a giant blob can't exhaust memory before
#: we even load it. Anything larger fails the installer dialog
#: with "Unexpected file size — check the URL" rather than
#: silently writing the bytes to disk.
MODEL_MAX_SIZE_BYTES = 5 * 1024 * 1024

#: One-hot column order for the ``Waveform_type`` feature. Order
#: must match :data:`FEATURE_ORDER` below so the encoded row
#: lines up with what the trained Random Forest expects.
WAVEFORM_TYPE_CATEGORIES: Tuple[str, ...] = (
    "biphasic_asymmetric",
    "biphasic_balanced",
    "biphasic_capacitive",
    "monophasic",
)

#: Full input feature order expected by the RF-Partial-19
#: classifier — verbatim from
#: ``Supplementary_Scripts/randomForest_training.py`` in the
#: upstream repo. Must match the column order the model was fit
#: on or ``predict_proba`` will silently return wrong probabilities.
FEATURE_ORDER: Tuple[str, ...] = (
    "Waveform_type_biphasic_asymmetric",
    "Waveform_type_biphasic_balanced",
    "Waveform_type_biphasic_capacitive",
    "Waveform_type_monophasic",
    "GSA",                         # µm²
    "Pulse_width",                 # µs
    "Frequency",                   # Hz
    "Current",                     # µA
    "Voltage",                     # mV
    "Charge_per_phase",            # nC / phase (abs)
    "Charge_density",              # µC / cm² per phase
    "Current_density",             # mA / cm²
    "stim_on",                     # seconds per pulse train ON
    "stim_total_day",              # seconds / day
    "daily_pulses",                # pulses / day
    "daily_accumulated_charge",    # Coulombs / day
)

#: Threshold at which the model's predict_proba ``1`` class is
#: rounded to "likely_damaging". RF-Partial-19's confusion matrix
#: in Li et al. 2024 was reported at the default 0.5 cutoff; we
#: keep that here so the local inference matches the published
#: numbers (88% accuracy, 3% FNR, 18% FPR).
PROBABILITY_THRESHOLD = 0.5

# ---------------------------------------------------------------------------
# Cross-reference tables — supp-table column names + units
# ---------------------------------------------------------------------------

#: Verbatim header → unit mapping from
#: ``Supplementary_Data1.xlsx`` (the 385-row curated database that
#: trained every model in the paper). Values are the unit substring
#: as it appears in the spreadsheet header. Used only for
#: documentation + the training-range validator below — the runtime
#: feature ordering uses :data:`FEATURE_ORDER`, which mirrors the
#: training script's column names rather than these display headers.
SUPP_TABLE_COLUMN_UNITS: Tuple[Tuple[str, str], ...] = (
    ("Reference", ""),
    ("Animal_model", ""),
    ("Nervous_system", ""),
    ("Specific_target", ""),
    ("Geometry", ""),
    ("Location", ""),
    ("Material", ""),
    ("Charge_mechanism", ""),
    ("Waveform_type", ""),
    ("First_ph_direction", ""),
    ("Polarity", ""),
    ("Control", ""),
    ("GSA", "µm²"),
    ("Bias", ""),
    ("Pulse_width", "µs"),
    ("Frequency", "Hz"),
    ("Interpulse_delay", "µs"),
    ("Current", "µA"),
    ("Voltage", "mV"),
    ("Charge_per_phase", "nC/ph"),
    ("Charge_density_per_phase", "µC/cm²/ph"),
    ("Current_density", "mA/cm²"),
    ("Duty_cycle", "%"),
    ("Stim_on", "s"),
    ("Stim_off", "s"),
    ("Stim_total_day", "s"),
    ("Days_stim", "days"),
    ("Stim_total_study", "h"),
    ("Daily_pulses", "pulses"),
    ("Total_pulses", "pulses"),
    ("Daily_accumulated_charge", "C"),
    ("Total_accumulated_charge", "C"),
    ("Avg_Damage_binary", ""),
)

#: Partial feature set names AS PRINTED IN SUPPLEMENTARY TABLE 5.
#: Provided so a user reading the paper alongside this module can
#: cross-check the field list without having to translate the
#: training-script names. Use :data:`FEATURE_ORDER` (training-
#: script names) for actual model input — Supp Table 5 uses
#: ``Stim_time_daily`` and ``Daily_pulses_count`` while the model
#: was fit on ``stim_total_day`` / ``daily_pulses``.
PARTIAL_FEATURE_SET_SUPP_NAMES: Tuple[str, ...] = (
    "Waveform_type",      # categorical → 4 one-hot columns
    "GSA",
    "Pulse_width",
    "Frequency",
    "Current",
    "Voltage",
    "Charge_per_phase",
    "Charge_density",
    "Current_density",
    "Stim_on",
    "Stim_time_daily",            # alias for stim_total_day in code
    "Daily_pulses_count",         # alias for daily_pulses in code
    "Daily_accumulated_charge",
)

#: Semi-complete feature set names AS PRINTED IN SUPPLEMENTARY
#: TABLE 4. Reserved for a future opt-in path that loads the
#: ``MLP-Total-5-26.pkl`` model (90% accuracy in Li et al. 2024).
#: Categorical entries one-hot expand the same way; numerical
#: entries match the supp database units (see
#: :data:`SUPP_TABLE_COLUMN_UNITS`).
SEMI_COMPLETE_FEATURE_SET_SUPP_NAMES: Tuple[str, ...] = (
    # Categorical (10):
    "Nervous_system", "Electrode_type", "Location", "Material",
    "Charge_mechanism", "Waveform_type", "First_ph_direction",
    "Polarity", "Control", "Bias",
    # Numerical (15):
    "GSA", "Pulse_width", "Interphase_delay", "Voltage",
    "Charge_density", "Stim_on", "Stim_time_daily",
    "Daily_accumulated_charge", "Current_density", "Frequency",
    "Current", "Charge_per_phase", "Duty_cycle", "Stim_off",
    "Daily_pulses_count",
)


# ---------------------------------------------------------------------------
# Hyperparameter constants from Supplementary Table 1
# ---------------------------------------------------------------------------

#: Hyperparameter search ranges Li et al. 2024 used to tune each
#: model family during grid search. Reproduced here for
#: documentation + so a future re-training pipeline can mirror the
#: paper's protocol exactly. Not consumed by the runtime — these
#: are reference constants only.
#:
#: NOTE: Supplementary Table 1 contains a typo — the k-NN row is
#: labelled "Number of trees", which is the Random Forest
#: hyperparameter. The k-NN scan range is over the number of
#: neighbours (``k``), 1 through 100. We use the corrected
#: interpretation here.
RF_HYPERPARAMETERS: Dict[str, object] = {
    "n_estimators_range": (1, 100),
    "selected_partial_19_n_estimators": 19,
    "selected_total_89_n_estimators": 89,
}

KNN_HYPERPARAMETERS: Dict[str, object] = {
    # Supp Table 1 mislabels this as "Number of trees: 1-100";
    # the column header on subsequent ML rows confirms it scans
    # over k (number of neighbours).
    "n_neighbors_range": (1, 100),
}

MLP_HYPERPARAMETERS: Dict[str, object] = {
    "hidden_layer_count": 2,
    "nodes_first_layer_range": (1, 30),
    "nodes_second_layer_range": (1, 30),
    "activation_options": ("tanh", "relu", "logistic"),
    "solver_options": ("adam", "lbfgs", "sgd"),
    "selected_partial_10_16_layers": (10, 16),
    "selected_total_5_26_layers": (5, 26),
}

LOGISTIC_REGRESSION_HYPERPARAMETERS: Dict[str, object] = {
    # Per Supp Table 1 — no hyperparameter scanning; default
    # scikit-learn settings were used.
    "search_grid": None,
}


# ---------------------------------------------------------------------------
# Per-feature training-range bounds (from Supplementary_Data1.xlsx)
# ---------------------------------------------------------------------------

#: Numerical-feature min / max from Table 2 of Li et al. 2024 §3.1
#: (overall medians and ranges across the 385-entry database).
#: Used by :func:`features_outside_training_range` to flag inputs
#: that fall OUTSIDE the range the model has actually seen — a
#: prediction on far-extrapolated parameters is technically valid
#: but should be taken with a much larger grain of salt than a
#: prediction near the training distribution.
#:
#: Format: ``{feature_name: (min, max)}``. Names match
#: :data:`FEATURE_ORDER` (training-script names). Categorical
#: one-hot columns are excluded — they're always 0.0 / 1.0 so
#: range-checking is trivially true.
FEATURE_TRAINING_RANGES: Dict[str, Tuple[float, float]] = {
    # Per Table 2 row "GSA": 0.0 - 0.50 cm². Convert to µm²
    # (1 cm² = 1e8 µm²) so the range matches FEATURE_ORDER units.
    "GSA":                       (0.0, 5.0e7),
    "Pulse_width":               (25.0, 1000.0),
    "Frequency":                 (1.0, 2750.0),
    # Current amplitude reported in mA (Table 2); 1.0 mA = 1000 µA.
    "Current":                   (0.0, 48000.0),
    "Voltage":                   (0.0, 60000.0),  # 60 V = 60000 mV
    # Charge per phase reported in µC; 1 µC = 1000 nC.
    "Charge_per_phase":          (0.0, 48000.0),
    "Charge_density":            (0.3, 5217.0),   # µC/cm²/ph
    "Current_density":           (0.0, 32000.0),
    # Stim-on duration is reported as "Pulse train ON" hours
    # (0-50 h); convert to seconds.
    "stim_on":                   (0.0, 50.0 * 3600.0),
    "stim_total_day":            (8.0 * 3600.0, 24.0 * 3600.0),  # 8-24 h
    "daily_pulses":              (0.0, 22.5e7),
    "daily_accumulated_charge":  (0.0, 22.7),  # Coulombs/day
}


def features_outside_training_range(features: Iterable[float]
                                     ) -> List[str]:
    """Return the FEATURE_ORDER names whose values fall outside the
    Li et al. 2024 training-data ranges (per :data:`FEATURE_TRAINING_RANGES`).

    Used by the metric tooltip to flag captures whose stimulation
    parameters are extrapolated relative to what the model saw at
    training time — an out-of-range prediction is technically still
    valid, but the user should know they're pushing past the
    bounds of the curated dataset.

    Returns an empty list when every feature is within bounds (or
    when the range table doesn't list a particular feature, e.g.
    the four one-hot ``Waveform_type_*`` columns).
    """
    row = list(features)
    if len(row) != len(FEATURE_ORDER):
        return []
    out: List[str] = []
    for name, value in zip(FEATURE_ORDER, row):
        rng = FEATURE_TRAINING_RANGES.get(name)
        if rng is None:
            continue
        lo, hi = rng
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        # Allow zero values through — many entries in the database
        # use 0 as a sentinel for "not applicable" (e.g. Voltage
        # for current-controlled experiments) so 0 always counts
        # as "in range" even when the actual data range starts
        # above zero. Real out-of-range cases are still caught
        # because non-zero values must satisfy lo ≤ v ≤ hi.
        if v == 0.0:
            continue
        if v < lo or v > hi:
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NeurostimMLResult:
    """One capture's NeurostimML inference output.

    Attributes
    ----------
    probability : float
        Probability of damage (the ``1`` class) from the RF's
        ``predict_proba``. Range 0.0–1.0.
    classification : str
        ``"likely_damaging"`` if probability ≥
        :data:`PROBABILITY_THRESHOLD`, else ``"likely_safe"``.
    """
    probability: float
    classification: str


# ---------------------------------------------------------------------------
# Path resolution + caching
# ---------------------------------------------------------------------------

def model_dir() -> Path:
    """Return the directory where the installer drops the model
    pickle. Mirrors :func:`stimtest.gui.prefs.prefs_dir` but uses
    only the standard library so this module stays GUI-free.
    """
    env = os.environ.get("STIMTEST_PREFS_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "StimulationTesting"
        return Path.home() / "AppData" / "Roaming" / "StimulationTesting"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StimulationTesting"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "StimulationTesting"


def model_path() -> Path:
    """Resolve the on-disk path of the model pickle.

    Idempotent — creates the parent directory on first call but
    doesn't touch the file itself. Callers should use
    :func:`model_is_installed` to check presence; loading is
    handled by :func:`get_model`.
    """
    d = model_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / MODEL_FILENAME


def model_is_installed() -> bool:
    """``True`` if the pickle is present at :func:`model_path`."""
    try:
        return model_path().is_file()
    except Exception:
        return False


# Module-private model cache. Lazy-loaded on first inference call so
# the import-graph cost is paid only when the user actually has
# the pickle installed and is running an experiment.
_model_lock = threading.Lock()
_model_cache: Optional[object] = None
_model_path_cache: Optional[str] = None
_model_mtime_cache: Optional[float] = None


def get_model():
    """Load and return the RF-Partial-19 classifier, or ``None``.

    Caches the loaded model in module memory so subsequent inference
    calls don't pay the joblib-load cost. Cache is keyed on the
    pickle's path + mtime, so re-running the installer (which
    overwrites the file) automatically invalidates the cache on the
    next call without requiring an application restart.

    Returns ``None`` (silently) when:

    * The pickle isn't present on disk yet.
    * scikit-learn or joblib aren't importable.
    * The pickle fails to load (corrupted file, version mismatch).

    The metrics layer treats ``None`` as "ML screen not available";
    the GUI surfaces "model not installed" to the user via the
    Help menu's status check.
    """
    if not model_is_installed():
        return None
    p = model_path()
    try:
        mtime = p.stat().st_mtime
    except OSError:
        return None
    global _model_cache, _model_path_cache, _model_mtime_cache
    with _model_lock:
        # Fast path: cached model still matches the on-disk file.
        if (_model_cache is not None
                and _model_path_cache == str(p)
                and _model_mtime_cache == mtime):
            return _model_cache
        # Slow path: load (or re-load) from disk.
        try:
            import joblib  # joblib re-exports sklearn's persistence API
        except ImportError:
            _log.warning("joblib not available; cannot load NeurostimML model")
            return None
        try:
            _model_cache = joblib.load(p)
        except Exception as e:
            # A version-mismatched pickle (sklearn upgrade between
            # the user's install and the upstream training run) is
            # the most common failure here. Log and fall back —
            # the Shannon screen still runs.
            _log.warning("Failed to load NeurostimML model from %s: %s", p, e)
            _model_cache = None
            _model_path_cache = None
            _model_mtime_cache = None
            return None
        _model_path_cache = str(p)
        _model_mtime_cache = mtime
        return _model_cache


def clear_model_cache() -> None:
    """Drop the in-memory model cache. Used by the installer dialog
    after a successful download / replacement so the next inference
    picks up the new file without an application restart.
    """
    global _model_cache, _model_path_cache, _model_mtime_cache
    with _model_lock:
        _model_cache = None
        _model_path_cache = None
        _model_mtime_cache = None


# ---------------------------------------------------------------------------
# Feature-vector builder
# ---------------------------------------------------------------------------

def encode_waveform_type(pattern) -> List[float]:
    """One-hot-encode the pattern's waveform type into the four
    columns the model expects.

    Mapping rules (derived from the upstream training data
    conventions and ``stimtest.waveforms.PulsePattern``):

    * ``num_phases == 1`` → ``"monophasic"``
    * ``num_phases == 2`` AND symmetric (same width / amplitude /
      shape on both phases) → ``"biphasic_balanced"``
    * ``num_phases == 2`` AND ANY phase shape is ``"capacitive"``
      / ``"capacitive_decay"`` (i.e. RC-discharge) →
      ``"biphasic_capacitive"``
    * Anything else with ≥2 phases → ``"biphasic_asymmetric"``

    Returns a length-4 ``List[float]`` of 0.0 / 1.0 values. NaN
    pattern → all zeros (unknown waveform).
    """
    out = [0.0, 0.0, 0.0, 0.0]
    if pattern is None:
        return out
    n_phases = getattr(pattern, "num_phases", 0) or 0
    phases = getattr(pattern, "phases", None) or ()
    if n_phases <= 1:
        out[WAVEFORM_TYPE_CATEGORIES.index("monophasic")] = 1.0
        return out
    # 2+ phases — branch on shape / symmetry.
    shapes = [getattr(p, "shape", "") for p in phases]
    # Cap-coupled / capacitively-balanced biphasic pulses are
    # produced by the pattern panel's cap-coupled solver, which
    # writes the recharge phase as an exponential decay (or
    # mirror-image rising exp) — the shape constants are
    # ``"exp_decay"`` and ``"exp_increasing"`` (see
    # :mod:`stimtest.waveforms`). The earlier substring check
    # for ``"capacit"`` never matched because the literal
    # string doesn't appear in either constant, so every
    # cap-coupled biphasic fell through to the
    # ``biphasic_asymmetric`` branch — silently losing one of
    # the published RF-Partial-19 model's most-predictive
    # features. Match against the actual constants instead.
    from .waveforms import SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING
    _CAP_SHAPES = {SHAPE_EXP_DECAY, SHAPE_EXP_INCREASING}
    if any(str(s) in _CAP_SHAPES for s in shapes):
        out[WAVEFORM_TYPE_CATEGORIES.index("biphasic_capacitive")] = 1.0
        return out
    # Symmetric biphasic = same shape, same |amplitude|, same width
    # on both phases (interphase delay doesn't break symmetry).
    if (len(phases) >= 2
            and shapes[0] == shapes[1]
            and abs(getattr(phases[0], "amplitude_ua", 0))
                == abs(getattr(phases[1], "amplitude_ua", 0))
            and getattr(phases[0], "width_us", 0)
                == getattr(phases[1], "width_us", 0)):
        out[WAVEFORM_TYPE_CATEGORIES.index("biphasic_balanced")] = 1.0
        return out
    out[WAVEFORM_TYPE_CATEGORIES.index("biphasic_asymmetric")] = 1.0
    return out


def build_feature_vector(*,
                         pattern,
                         charge_per_phase_nc: float,
                         charge_injection_mc_per_cm2: float,
                         surface_area_um2: float,
                         pulse_rate_hz: Optional[float] = None,
                         duty_cycle_fraction: Optional[float] = None,
                         daily_stim_seconds: Optional[float] = None,
                         days_stimulated: Optional[float] = None,
                         voltage_mv: Optional[float] = None,
                         ) -> Optional[List[float]]:
    """Assemble the 16-column input vector for RF-Partial-19.

    Parameters that aren't directly available on a bare
    :class:`Capture` (pulse rate, duty cycle, daily duration, days
    stimulated, applied voltage) come through as keyword arguments;
    the metrics-layer adapter :func:`predict_from_capture` reads
    what it can off the pattern and the session and passes the
    rest as ``None``. Missing values are filled in with sensible
    defaults that don't bias the prediction:

    * ``pulse_rate_hz`` defaults to the pattern's ``rate_hz``.
    * ``duty_cycle_fraction`` defaults to ``rate_hz × total_pulse_us
      × 1e-6`` if not supplied. Conservative — assumes back-to-back
      pulsing.
    * ``daily_stim_seconds`` defaults to one second (single-shot
      capture).
    * ``days_stimulated`` defaults to 1.
    * ``voltage_mv`` defaults to 0 (current-controlled experiment).

    Returns ``None`` when the pattern is missing or the per-phase
    charge / surface area are non-finite — predictions on a
    bad-input row would be nonsense.
    """
    import math
    if pattern is None:
        return None
    try:
        q_ph_nc = abs(float(charge_per_phase_nc))
        q_inj_mc = abs(float(charge_injection_mc_per_cm2))
        gsa_um2 = float(surface_area_um2)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(q_ph_nc) and math.isfinite(q_inj_mc)
            and math.isfinite(gsa_um2) and gsa_um2 > 0):
        return None

    # Charge density: paper uses µC/cm². Capture metrics has
    # mC/cm² — convert.
    charge_density_uc_per_cm2 = q_inj_mc * 1000.0
    # Charge per phase: paper uses nC; capture metrics already nC.
    charge_per_phase_nc_abs = q_ph_nc

    # Pulse width (µs) — leading phase by convention.
    pulse_width_us = float(getattr(pattern.phases[0], "width_us", 0.0))

    # Current amplitude (µA) — leading phase, absolute value.
    current_ua = abs(float(getattr(pattern.phases[0], "amplitude_ua", 0.0)))

    # Frequency (Hz) — pattern's pulse rate.
    rate_hz = (pulse_rate_hz
               if pulse_rate_hz is not None
               else float(getattr(pattern, "rate_hz", 0.0)))
    if not math.isfinite(rate_hz) or rate_hz <= 0.0:
        rate_hz = float(getattr(pattern, "rate_hz", 0.0))

    # Voltage (mV) — only used in voltage-controlled experiments;
    # default to 0 for current-controlled (PlexStim default).
    voltage_mv_val = 0.0 if voltage_mv is None else float(voltage_mv)

    # Current density (mA / cm²). gsa is in µm² → cm² is gsa * 1e-8.
    gsa_cm2 = gsa_um2 * 1e-8
    current_density_ma_per_cm2 = (
        (current_ua * 1e-3) / gsa_cm2 if gsa_cm2 > 0 else 0.0
    )

    # Duty cycle. Default = rate_hz × total_pulse_us × 1e-6 (i.e.
    # what fraction of each second the active pulse is on, assuming
    # back-to-back pulsing). Treat as a fraction (0-1).
    if duty_cycle_fraction is None:
        total_pulse_us = float(getattr(pattern, "total_pulse_us",
                                       pulse_width_us))
        duty_cycle_fraction = (rate_hz * total_pulse_us * 1e-6
                               if math.isfinite(rate_hz) and rate_hz > 0
                               else 0.0)
    duty_cycle_fraction = max(0.0, min(1.0, float(duty_cycle_fraction)))

    # Stim ON / total_per_day / daily_pulses / daily_accumulated_charge.
    if daily_stim_seconds is None:
        # Single-shot capture default — one second of effective stim.
        daily_stim_seconds = 1.0
    daily_stim_seconds = max(0.0, float(daily_stim_seconds))
    stim_on_seconds = daily_stim_seconds * duty_cycle_fraction

    daily_pulses_count = (rate_hz * daily_stim_seconds * duty_cycle_fraction
                          if math.isfinite(rate_hz) and rate_hz > 0
                          else 0.0)

    # Daily accumulated charge: nC/phase × pulses/day × 1 phase
    # (taking the leading-phase charge as the per-pulse charge).
    # Convert nC to Coulombs (× 1e-9).
    daily_accumulated_charge_c = (
        charge_per_phase_nc_abs * daily_pulses_count * 1e-9
    )

    # Build the row in FEATURE_ORDER. Caller doesn't pass days
    # stimulated through to the model (it's not a partial-set
    # input), but we keep ``days_stimulated`` in the function
    # signature for future extensions toward the semi-complete
    # model that does include it.
    _ = days_stimulated  # currently unused by the partial-set model

    waveform_one_hot = encode_waveform_type(pattern)
    return [
        waveform_one_hot[0],
        waveform_one_hot[1],
        waveform_one_hot[2],
        waveform_one_hot[3],
        gsa_um2,
        pulse_width_us,
        rate_hz,
        current_ua,
        voltage_mv_val,
        charge_per_phase_nc_abs,
        charge_density_uc_per_cm2,
        current_density_ma_per_cm2,
        stim_on_seconds,
        daily_stim_seconds,
        daily_pulses_count,
        daily_accumulated_charge_c,
    ]


# ---------------------------------------------------------------------------
# Inference entry points
# ---------------------------------------------------------------------------

def predict(features: Iterable[float]) -> Optional[NeurostimMLResult]:
    """Run the model on a pre-built 16-feature row.

    Returns ``None`` when the model isn't loaded (so the caller
    doesn't have to wrap every call in ``model_is_installed``);
    otherwise returns a :class:`NeurostimMLResult` with the damage
    probability and the binary classification.
    """
    model = get_model()
    if model is None:
        return None
    row = list(features)
    if len(row) != len(FEATURE_ORDER):
        _log.warning(
            "NeurostimML predict() got %d features; expected %d",
            len(row), len(FEATURE_ORDER))
        return None
    try:
        # ``predict_proba`` returns shape (1, 2) — column 0 is the
        # ``0`` (safe) class probability, column 1 is the ``1``
        # (damaging) class. We surface the ``1`` probability so the
        # UI can render a "X% likely damaging" badge.
        proba = model.predict_proba([row])[0]
        p_damaging = float(proba[1])
    except Exception as e:
        _log.warning("NeurostimML predict_proba failed: %s", e)
        return None
    classification = ("likely_damaging"
                      if p_damaging >= PROBABILITY_THRESHOLD
                      else "likely_safe")
    return NeurostimMLResult(probability=p_damaging,
                             classification=classification)


def predict_from_capture(capture, surface_area_um2: float,
                         *,
                         pulse_rate_hz: Optional[float] = None,
                         duty_cycle_fraction: Optional[float] = None,
                         daily_stim_seconds: Optional[float] = None,
                         voltage_mv: Optional[float] = None,
                         ) -> Optional[NeurostimMLResult]:
    """Run the local NeurostimML inference on one capture.

    Adapter that pulls the per-capture inputs from a
    :class:`stimtest.session.Capture` (pattern, charge metrics,
    surface area) and combines them with the optional session-
    level inputs (pulse rate, duty cycle, daily duration, applied
    voltage) before calling :func:`predict`. Returns ``None``
    silently when the model isn't installed or the feature vector
    can't be assembled.

    Called from :func:`stimtest.metrics.compute_metrics` once per
    capture; the result populates two new
    :class:`CaptureMetrics` fields (``neurostimml_classification``
    and ``neurostimml_probability``) which the Viewer surfaces in
    the per-capture metric table next to the Shannon verdict.
    """
    if not model_is_installed():
        return None
    metrics = getattr(capture, "metrics", None)
    if metrics is None:
        return None
    pattern = getattr(capture, "pattern", None)
    features = build_feature_vector(
        pattern=pattern,
        charge_per_phase_nc=getattr(metrics, "charge_per_phase_nc", float("nan")),
        charge_injection_mc_per_cm2=getattr(
            metrics, "charge_injection_mc_per_cm2", float("nan")),
        surface_area_um2=surface_area_um2,
        pulse_rate_hz=pulse_rate_hz,
        duty_cycle_fraction=duty_cycle_fraction,
        daily_stim_seconds=daily_stim_seconds,
        voltage_mv=voltage_mv,
    )
    if features is None:
        return None
    return predict(features)


# ---------------------------------------------------------------------------
# Installer helpers
# ---------------------------------------------------------------------------

def compute_sha256(path: Path) -> str:
    """SHA-256 hex digest of a file. Used by the installer dialog
    to surface the on-disk fingerprint to the user as a tamper-
    evidence cue. We don't pin a single expected hash because the
    upstream repo may legitimately re-train the model.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def install_model_from_url(url: str,
                           *,
                           expected_sha256: Optional[str] = None,
                           max_size_bytes: int = MODEL_MAX_SIZE_BYTES,
                           ) -> Tuple[Path, str]:
    """Download the model pickle from ``url`` and write it to
    :func:`model_path` atomically.

    Parameters
    ----------
    url : str
        Where to fetch the bytes from. Defaults from the GUI side
        come from :data:`MODEL_DOWNLOAD_URL`; an offline lab can
        override to ``file:///`` or an internal mirror.
    expected_sha256 : str, optional
        If supplied, the download is rejected (and the temp file
        deleted) when the digest doesn't match. The Help menu's
        installer dialog passes this when the user has copy-pasted
        a known-good fingerprint from a release; it's optional
        because GitHub also serves the integrity-evidence (HTTPS +
        upstream commit hash) the user can verify out-of-band.
    max_size_bytes : int
        Hard cap on the download size, in bytes. Defaults to
        :data:`MODEL_MAX_SIZE_BYTES`.

    Returns
    -------
    (Path, str)
        The on-disk path of the installed pickle and its SHA-256.

    Raises
    ------
    RuntimeError
        On any failure (network, integrity, oversized download).
        The temp file is always cleaned up before the exception
        propagates.
    """
    from urllib.request import Request, urlopen
    target = model_path()
    tmp = target.with_suffix(target.suffix + ".tmp")
    # Stream to a temp file so a half-finished download never
    # leaves a corrupt model in place. We move ``tmp → target``
    # only after the size + hash checks pass.
    req = Request(url, headers={"User-Agent": "stimtest-neurostimml"})
    h = hashlib.sha256()
    total = 0
    try:
        with urlopen(req, timeout=20) as resp, tmp.open("wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size_bytes:
                    raise RuntimeError(
                        f"NeurostimML model download exceeded "
                        f"{max_size_bytes} bytes — aborting.")
                f.write(chunk)
                h.update(chunk)
        digest = h.hexdigest()
        if expected_sha256 and digest != expected_sha256:
            raise RuntimeError(
                f"NeurostimML model SHA256 mismatch:\n"
                f"  expected: {expected_sha256}\n"
                f"  got:      {digest}")
        # Atomic move into place; clear the in-memory model cache
        # so the next inference call picks up the new file.
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise
    finally:
        # Defensive cleanup — if the move succeeded the temp is
        # already gone, but unlink(missing_ok=True) is cheap.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
    clear_model_cache()
    return target, digest


def uninstall_model() -> bool:
    """Delete the local model pickle, if present.

    Used by the installer dialog's "Uninstall" button. Returns
    ``True`` if a file was removed, ``False`` if there was nothing
    to remove. Failures are logged and converted to a return-False
    so the caller can surface a clean message rather than a
    stack trace.
    """
    p = model_path()
    if not p.exists():
        return False
    try:
        p.unlink()
        clear_model_cache()
        return True
    except OSError as e:
        _log.warning("Failed to remove NeurostimML model at %s: %s", p, e)
        return False


__all__ = [
    "MODEL_FILENAME",
    "MODEL_DOWNLOAD_URL",
    "MODEL_MAX_SIZE_BYTES",
    "WAVEFORM_TYPE_CATEGORIES",
    "FEATURE_ORDER",
    "PROBABILITY_THRESHOLD",
    "SUPP_TABLE_COLUMN_UNITS",
    "PARTIAL_FEATURE_SET_SUPP_NAMES",
    "SEMI_COMPLETE_FEATURE_SET_SUPP_NAMES",
    "RF_HYPERPARAMETERS",
    "KNN_HYPERPARAMETERS",
    "MLP_HYPERPARAMETERS",
    "LOGISTIC_REGRESSION_HYPERPARAMETERS",
    "FEATURE_TRAINING_RANGES",
    "NeurostimMLResult",
    "model_dir",
    "model_path",
    "model_is_installed",
    "get_model",
    "clear_model_cache",
    "encode_waveform_type",
    "build_feature_vector",
    "features_outside_training_range",
    "predict",
    "predict_from_capture",
    "compute_sha256",
    "install_model_from_url",
    "uninstall_model",
]
