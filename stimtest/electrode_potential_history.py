"""Per-coating electrode-potential learning storage.

The runner records the **interpulse potential** (E_ip) measured off
the E_ret trace before and after every active pulse, binned by the
coating of whichever electrode produced the measurement (return
electrode for E_ret samples; reference electrode for the same
samples viewed against the reference baseline). Once a coating
accumulates **at least :data:`MIN_SAMPLES_FOR_LEARNED_OCP` samples**
(currently 10) the GUI starts using the running mean of those samples
as the live open-circuit potential (OCP) for that coating, replacing
the static catalog value from
:mod:`stimtest.config.COATING_OCP_VS_AG_AG_CL_V`. Until the threshold
is reached the catalog value is used unchanged, so a clean install
behaves identically to the pre-feature build until enough data has
been collected to differ.

Bin keys are **canonical short codes** (see :func:`canonical_key`),
not raw coating names. The catalog distinguishes three PtIr alloys
(``PtIr (90/10)`` / ``PtIr (80/20)`` / ``PtIr (70/30)``) but their
electrochemistry overlaps within typical instrumentation noise; per
the user spec, we average them into a single ``PtIr`` bin until
enough data emerges to show the alloys are statistically distinct.
That collapse can be reversed by tightening the canonical mapping
once the experimental record argues for it.

Storage format
==============

JSON file, atomic-written, kept alongside the rest of the prefs:

::

    {
      "version": 1,
      "samples": {
        "PtIr": [
          {"v": 0.198, "ts": "2026-05-10T14:01:23",
           "phase": "pre",  "source": "return"},
          {"v": 0.201, "ts": "2026-05-10T14:01:24",
           "phase": "post", "source": "return"},
          ...
        ],
        "Pt":   [...],
        "SIROF": [...]
      }
    }

* ``v`` — the measurement (volts vs Ag|AgCl).
* ``ts`` — ISO timestamp; informational, never used for math.
* ``phase`` — ``"pre"`` (pre-pulse baseline) or ``"post"``
  (post-discharge tail). Stored separately so a future analysis can
  watch for drift between the two windows.
* ``source`` — ``"return"`` or ``"reference"``; lets a downstream
  audit tool tell apart "this was measured at the return terminal"
  vs "this was measured at the reference terminal".

Each bin is capped at :data:`MAX_SAMPLES_PER_BIN` so the file size
stays bounded across long deployments — older samples are dropped
FIFO-style. The threshold for "enough data to use the learned mean"
is well below the cap, so the bin behaves like a rolling window of
the most recent measurements rather than a lifetime average.

Concurrency
===========

The same JSON file may be touched by two processes — the main GUI
and the standalone Viewer — so writes go through an atomic
``replace`` after a temp-file write, the same pattern
:mod:`stimtest.gui.prefs` uses. Reads tolerate a missing or malformed
file (return an empty bin map without raising).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

#: File the JSON state is persisted to. Lives in the same directory
#: as ``gui_prefs.json`` so a power user can grep both files in one
#: place when debugging — the GUI's prefs dir resolution
#: (:func:`stimtest.gui.prefs.prefs_dir`) hands back the same path on
#: every supported platform.
HISTORY_FILE = "electrode_potential_history.json"

#: Schema version. Bump when the on-disk shape changes; the loader
#: silently discards entries from an unknown version rather than
#: crashing — the user always recovers by re-collecting samples.
HISTORY_VERSION = 1

#: Minimum samples per coating before the running mean is used in
#: place of the catalog OCP. The user spec calls for 10; tuned so a
#: short calibration sweep (≈5 captures across two configurations on
#: a freshly-coated electrode) doesn't accidentally swap the catalog
#: value for an under-sampled estimate.
MIN_SAMPLES_FOR_LEARNED_OCP = 10

#: Cap on the per-coating bin so a long deployment doesn't grow the
#: file without bound. With ~10 captures per session and ~50
#: sessions per month, a 200-sample cap holds roughly a month of
#: rolling data per coating. Older entries fall off FIFO-style.
MAX_SAMPLES_PER_BIN = 200

# ---------------------------------------------------------------------------
# Canonical-key collapse — multiple coating names → one bin
# ---------------------------------------------------------------------------

#: Coating-name → canonical bin key. Currently collapses every PtIr
#: alloy into a single ``PtIr`` bin; everything else is identity. We
#: keep the table explicit (rather than e.g. regex-stripping
#: parentheticals) so a future experiment that DOES distinguish
#: alloys can simply remove the relevant entries here without having
#: to also update the parser.
_CANONICAL_OVERRIDE: Dict[str, str] = {
    "PtIr (90/10)": "PtIr",
    "PtIr (80/20)": "PtIr",
    "PtIr (70/30)": "PtIr",
}


def canonical_key(name: Optional[str]) -> Optional[str]:
    """Map a coating / electrode short code to its canonical bin key.

    The canonical key is what we use to bin samples, so two coatings
    that share electrochemistry (e.g. the three PtIr alloys) pool
    their measurements rather than each taking 10 captures to learn
    independently. Returns ``None`` for empty / falsy inputs so
    callers can do ``if canonical_key(x) is None: skip`` without
    catching exceptions.

    The mapping intentionally does NOT lowercase or strip whitespace
    — short codes are case-sensitive in the catalog
    (``PtIr`` vs ``Ptir`` is a typo, not an alias) and we want a
    clean failure when one of those slips through.
    """
    if not name:
        return None
    return _CANONICAL_OVERRIDE.get(str(name), str(name))


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

# Module-private lock — every read-modify-write goes through it so a
# burst of captures from the experiment runner can't interleave their
# JSON writes and corrupt the file. ``threading.Lock`` is plenty for
# our single-process use; cross-process safety is provided by the
# atomic ``os.replace`` at the bottom of ``_write_atomic``.
_LOCK = threading.Lock()


def _default_prefs_dir() -> Path:
    """Return the per-user prefs directory.

    Mirrors :func:`stimtest.gui.prefs.prefs_dir` but uses only the
    standard library so this module stays import-light (no Qt
    pulled into the metrics path). Override via the
    ``STIMTEST_PREFS_DIR`` environment variable for tests / sandboxed
    runs that don't want to touch the user's real prefs directory.
    """
    env = os.environ.get("STIMTEST_PREFS_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        # %APPDATA% always exists on a healthy Windows session; fall
        # through to the home directory if for some reason it doesn't.
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "StimulationTesting"
        return Path.home() / "AppData" / "Roaming" / "StimulationTesting"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "StimulationTesting"
    # Linux / *BSD — XDG Base Dir spec.
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "StimulationTesting"


def history_path() -> Path:
    """Resolve the JSON file path; creates parent dirs on demand.

    Called every read / write rather than cached so the
    ``STIMTEST_PREFS_DIR`` override is honoured even when set after
    module import (typical in pytest fixtures).
    """
    d = _default_prefs_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / HISTORY_FILE


# ---------------------------------------------------------------------------
# Low-level read / write
# ---------------------------------------------------------------------------

def _load_raw() -> dict:
    """Read the JSON file. Returns ``{}`` on any failure — a missing
    or malformed file should never block a capture from completing.
    """
    p = history_path()
    if not p.is_file():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    if data.get("version") != HISTORY_VERSION:
        # Future-proofing: if a newer client writes a version we
        # don't understand, ignore the file rather than corrupting
        # it. The user re-accumulates samples in the new format.
        return {}
    return data


def _write_atomic(data: dict) -> None:
    """Write ``data`` to ``history_path()`` atomically.

    Same pattern as :mod:`stimtest.gui.prefs`: write to a sibling
    ``.tmp`` file then ``os.replace`` over the target so a
    concurrent reader never sees a partial JSON document. Errors are
    swallowed because the data we'd be losing is best-effort
    measurement history — never block a capture on a flaky disk.
    """
    p = history_path()
    tmp = p.with_suffix(p.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
        os.replace(tmp, p)
    except Exception:
        # Best-effort cleanup of the temp file; ignore if it's gone.
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_sample(coating_name: str, value_v: float, *,
                  phase: str = "pre",
                  source: str = "return",
                  timestamp: Optional[datetime] = None,
                  environment: Optional[str] = None,
                  sparge_gas: Optional[str] = None) -> None:
    """Append one E_ip measurement to the bin for ``coating_name``.

    Parameters
    ----------
    coating_name : str
        Catalog short code of the electrode whose potential was
        measured (e.g. ``"Pt"``, ``"PtIr (90/10)"``, ``"SIROF"``).
        The bin key is :func:`canonical_key` of this name, so the
        three PtIr alloys land in the same ``PtIr`` bin.
    value_v : float
        The measurement, in volts vs Ag|AgCl. NaN / inf samples are
        silently dropped — they'd poison the running mean.
    phase : {"pre", "post"}
        Which side of the active pulse the sample came from. Stored
        for downstream analysis but does NOT change the bin —
        learning treats both pre- and post-pulse rest values as
        observations of the same OCP.
    source : {"return", "reference"}
        Whether this came off the return-electrode terminal or the
        reference-electrode terminal. Stored alongside the value for
        provenance; the math doesn't branch on it.
    timestamp : datetime, optional
        Override the recorded timestamp. Defaults to ``now``. Useful
        for backfilling historical data; tests pin a fixed
        timestamp so the JSON output is deterministic.
    environment : str, optional
        Canonical environment short_code from
        :data:`stimtest.environments.ENVIRONMENT_PRESETS` (e.g.
        ``"pbs"``, ``"rat_cortex"``). Stored alongside the value
        as provenance metadata so a future analysis can cohort
        the bin by environment (e.g. compare PtIr in PBS vs PtIr
        in rat cortex). Empty / None records the sample without
        an environment tag.
    sparge_gas : str, optional
        Canonical sparge-gas short_code from
        :data:`stimtest.environments.SPARGE_GAS_PRESETS`
        (``"none"``, ``"n2"``, or ``"ar"``). Stored alongside
        the value because dissolved O₂ shifts the OCP measurably
        and a downstream cohort study would want to compare
        N₂-sparged vs ambient samples without re-tagging legacy
        data. ``None`` / empty / ``"none"`` records the sample
        without a sparge-gas tag — and the export pass treats
        a missing tag as "ambient" by convention.
    """
    import math
    if not isinstance(value_v, (int, float)) or math.isnan(float(value_v)) \
            or math.isinf(float(value_v)):
        return
    key = canonical_key(coating_name)
    if not key:
        return
    if phase not in ("pre", "post"):
        phase = "pre"
    if source not in ("return", "reference"):
        source = "return"
    ts = (timestamp or datetime.now()).isoformat(timespec="seconds")
    entry = {
        "v": float(value_v),
        "ts": ts,
        "phase": phase,
        "source": source,
    }
    if environment:
        entry["environment"] = str(environment).strip()
    # Sparge gas — only persist non-default values. ``"none"``
    # is the most common state and recording it explicitly on
    # every entry would inflate the on-disk file without adding
    # cohort-relevant information; downstream readers default a
    # missing ``sparge_gas`` field to ``"none"``.
    if sparge_gas and str(sparge_gas).strip().lower() not in ("", "none"):
        entry["sparge_gas"] = str(sparge_gas).strip().lower()
    with _LOCK:
        data = _load_raw()
        if "samples" not in data or not isinstance(data.get("samples"), dict):
            data = {"version": HISTORY_VERSION, "samples": {}}
        bins = data["samples"]
        bin_list = bins.get(key)
        if not isinstance(bin_list, list):
            bin_list = []
        bin_list.append(entry)
        # FIFO bound — drop the oldest entries once we exceed the cap.
        if len(bin_list) > MAX_SAMPLES_PER_BIN:
            bin_list = bin_list[-MAX_SAMPLES_PER_BIN:]
        bins[key] = bin_list
        data["version"] = HISTORY_VERSION
        data["samples"] = bins
        _write_atomic(data)


def record_pre_post_pair(coating_name: str,
                         pre_v: Optional[float],
                         post_v: Optional[float],
                         *, source: str = "return",
                         environment: Optional[str] = None,
                         sparge_gas: Optional[str] = None) -> None:
    """Record both pre- and post-pulse rest values in one call.

    Convenience wrapper for the runner — every capture has at most
    one pre-value and one post-value, so the typical hook is "got a
    new capture; record whichever sides are finite". NaN / None
    values are skipped silently. ``environment`` and ``sparge_gas``
    are forwarded to each :func:`record_sample` call so both
    halves carry the same provenance tags.
    """
    if pre_v is not None:
        record_sample(coating_name, float(pre_v),
                      phase="pre", source=source,
                      environment=environment,
                      sparge_gas=sparge_gas)
    if post_v is not None:
        record_sample(coating_name, float(post_v),
                      phase="post", source=source,
                      environment=environment,
                      sparge_gas=sparge_gas)


def record_capture(capture, session) -> None:
    """Record E_ret pre/post-pulse rest values from a finished capture.

    The runner calls this immediately after :func:`compute_metrics`
    completes for a capture; ``capture.metrics.return_pre_pulse_potential_v``
    and ``capture.metrics.return_post_pulse_potential_v`` are read,
    NaN values are skipped, and the remainder are pushed into the
    canonical per-coating bin keyed by the return-electrode coating
    recorded in the session's ``setup_snapshot``.

    Why bin only by the return coating?
        E_ret is measured at the return terminal, so each rest-window
        mean is a sample of *that* electrode's OCP — this is the
        physical interpretation the user is paying for. The reference
        and return roles share the bin because the GUI's OCP lookup
        is keyed by coating, not by role; a Pt reference and a Pt
        return both consult the same ``Pt`` bin. For non-Ag|AgCl
        references the absolute interpretation breaks down (E_ret is
        then "vs the wired reference" rather than "vs Ag|AgCl"), so
        we only record when the reference is Ag|AgCl OR when the
        snapshot is missing entirely (the default benchtop config).

    No-op (silent) on:

    * Capture with no E_ret-derived rest values (NaN).
    * Session whose ``setup_snapshot`` doesn't carry a return coating.
    * Reference electrode set to anything other than Ag|AgCl, where
      the absolute potential interpretation isn't valid.
    * Custom / out-of-catalog coatings (the canonical key still goes
      in, so the user can later inspect their own samples — but the
      learned-OCP lookup naturally returns ``None`` until 10 samples
      have been collected).
    """
    metrics = getattr(capture, "metrics", None)
    if metrics is None:
        return
    pre = getattr(metrics, "return_pre_pulse_potential_v", float("nan"))
    post = getattr(metrics, "return_post_pulse_potential_v", float("nan"))
    # Cheap NaN check that avoids importing numpy here. The values
    # come straight from CaptureMetrics defaults so they're always
    # plain Python floats (or numpy scalars that compare cleanly to
    # themselves).
    pre_ok = isinstance(pre, (int, float)) and pre == pre  # NaN != NaN
    post_ok = isinstance(post, (int, float)) and post == post
    if not (pre_ok or post_ok):
        return
    extras = getattr(getattr(session, "test", None), "extras", None) or {}
    snap = extras.get("setup_snapshot") or {}
    if not isinstance(snap, dict):
        return
    return_coating = snap.get("return_coating_short") or ""
    if not return_coating:
        return
    # Reference-electrode safety check. The instrumentation amp is
    # nominally wired to give "E_ret vs Ag|AgCl"; if the user has
    # explicitly told us a different reference is in the bath then
    # the absolute interpretation breaks down (we'd be recording
    # "Pt's OCP vs the user's Pt reference" → near-zero, useless
    # for learning). Skip recording in that case rather than poison
    # the bin.
    ref_short = snap.get("reference_electrode_short") or ""
    ref_enabled = bool(snap.get("reference_enable", False))
    if ref_enabled and ref_short and ref_short != "Ag|AgCl":
        return
    # Carry the user's chosen Environment through as provenance so
    # a downstream analysis can cohort by buffer / cell culture /
    # in vivo. The setup snapshot stores both ``environment_short``
    # and ``environment_custom``; we record the short_code (stable
    # across releases) plus, when present, the custom text in
    # parentheses for reader-friendly forensics.
    env_short = (snap.get("environment_short")
                 if isinstance(snap.get("environment_short"), str)
                 else "")
    env_custom = (snap.get("environment_custom")
                  if isinstance(snap.get("environment_custom"), str)
                  else "")
    if env_short and env_custom and env_short == "custom":
        env_tag: Optional[str] = f"custom: {env_custom}"
    elif env_short:
        env_tag = env_short
    else:
        env_tag = None
    # Sparge-gas provenance — read from the same setup snapshot.
    # Default ``"none"`` is the ambient bench state; we only
    # record an explicit tag when the user picked N₂ / Ar so the
    # on-disk file stays compact and a missing field can be
    # interpreted as "ambient" by downstream cohort tools.
    sparge_tag: Optional[str] = None
    if isinstance(snap, dict):
        sg_raw = snap.get("sparge_gas")
        if isinstance(sg_raw, str) and sg_raw.strip().lower() not in (
                "", "none"):
            sparge_tag = sg_raw.strip().lower()
    record_pre_post_pair(
        return_coating,
        pre if pre_ok else None,
        post if post_ok else None,
        source="return",
        environment=env_tag,
        sparge_gas=sparge_tag,
    )


def sample_count(coating_name: str) -> int:
    """Number of samples currently in the bin for ``coating_name``.

    Returns 0 for unknown coatings or if the file can't be read.
    Useful for the GUI to surface a "X / 10 samples collected"
    progress string next to the OCP readout.
    """
    key = canonical_key(coating_name)
    if not key:
        return 0
    data = _load_raw()
    bin_list = (data.get("samples") or {}).get(key) or []
    return len(bin_list) if isinstance(bin_list, list) else 0


def learned_ocp_v(coating_name: str) -> Optional[float]:
    """Running-mean OCP for ``coating_name``, or ``None`` if not yet
    enough samples.

    Returns the arithmetic mean of every recorded ``v`` in the
    coating's bin once at least :data:`MIN_SAMPLES_FOR_LEARNED_OCP`
    samples are present. Below that threshold the caller should
    fall back to the catalog value — the rationale being that a
    handful of samples can be skewed by an odd capture (low SNR,
    early-life electrode, bench fluid not yet equilibrated) and
    blindly using the mean would make the limits the user sees
    jitter unhelpfully across the first few captures of a session.
    """
    key = canonical_key(coating_name)
    if not key:
        return None
    data = _load_raw()
    bin_list = (data.get("samples") or {}).get(key) or []
    if not isinstance(bin_list, list):
        return None
    values: List[float] = []
    for entry in bin_list:
        try:
            values.append(float(entry["v"]))
        except (KeyError, TypeError, ValueError):
            continue
    if len(values) < MIN_SAMPLES_FOR_LEARNED_OCP:
        return None
    return sum(values) / float(len(values))


def all_bins() -> Dict[str, List[dict]]:
    """Snapshot every bin currently on disk, keyed by canonical name.

    Used by tests and by the (future) "Calibration data" inspector
    in the GUI. Returns a defensive copy — the caller can mutate
    the result freely without affecting the on-disk state.
    """
    data = _load_raw()
    raw = data.get("samples") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): list(v) for k, v in raw.items() if isinstance(v, list)}


def export_anonymized_payload(
    *,
    user_name: Optional[str] = None,
    user_email: Optional[str] = None,
    institution: Optional[str] = None,
    session_name: Optional[str] = None,
    include_user_name: bool = False,
    include_user_email: bool = False,
    include_institution: bool = False,
    include_session_name: bool = False,
    note: Optional[str] = None,
) -> dict:
    """Build a JSON-serialisable, anonymised payload of all bins.

    Used by the **Help → Contribute electrode data…** flow to ship
    the user's accumulated calibration samples to the project's
    GitHub repository. Anonymous by default — the four ``include_*``
    flags are opt-ins for academic-setting attribution where the
    contributor wants their lab credited.

    Privacy guarantees baked into the format:

    * Notebook names, save-path locations, raw scope timestamps,
      stimulator serial numbers, and per-capture metadata are
      **never** included. We only ship the bin keys, the ``v``
      values, the ``phase`` tag (``"pre"`` / ``"post"``), the
      ``source`` tag (``"return"`` / ``"reference"``), and a
      DAY-ONLY date — time-of-day is dropped from each sample's
      timestamp because high-resolution timestamps would let an
      attacker correlate uploads with publicly-known lab
      activity. The runner stores ISO-second timestamps in the
      on-disk file; the export pass truncates them.
    * The four optional attribution fields (``user_name``,
      ``user_email``, ``institution``, ``session_name``) are
      included **only** when both their corresponding
      ``include_*`` flag is True AND the supplied value is
      non-empty. Missing values are dropped silently — the
      contributor stays anonymous if they leave a flag on but
      don't fill in the field.
    * The ``note`` field, if supplied, is a free-form text the
      contributor can include (e.g. "fresh SIROF coatings, lot
      #123"). Always shipped as-is when non-empty.

    The schema is versioned so the maintainer's ingest tool can
    reject older / unknown formats cleanly.

    Returns
    -------
    dict
        A pure-Python ``dict`` ready for ``json.dumps`` (or
        ``QUrl.toPercentEncoding`` for the GitHub-issue URL).
        Empty ``"samples"`` map when the local store is empty;
        callers should bail in that case rather than open an
        Issue with nothing in it.
    """
    # Read the local store and rewrite each entry's timestamp into a
    # day-only ISO date string. We strip time-of-day (hours / minutes
    # / seconds) because high-resolution timestamps make individual
    # uploads correlatable with publicly-known lab schedules. The
    # day is still useful for the maintainer's drift / seasonality
    # analyses without identifying the contributor's work pattern.
    sanitised_bins: Dict[str, List[dict]] = {}
    for key, samples in all_bins().items():
        clean: List[dict] = []
        for entry in samples:
            try:
                v = float(entry["v"])
            except (KeyError, TypeError, ValueError):
                continue
            phase = entry.get("phase", "pre")
            phase = phase if phase in ("pre", "post") else "pre"
            source = entry.get("source", "return")
            source = source if source in ("return", "reference") else "return"
            ts = entry.get("ts", "")
            # Take the leading ``YYYY-MM-DD`` slice if the on-disk
            # value is a well-formed ISO string; otherwise drop it.
            ts_date = ""
            if isinstance(ts, str) and len(ts) >= 10:
                head = ts[:10]
                if (head[4] == "-" and head[7] == "-"
                        and head[:4].isdigit()
                        and head[5:7].isdigit()
                        and head[8:10].isdigit()):
                    ts_date = head
            clean_entry = {"v": v, "phase": phase, "source": source,
                           "date": ts_date}
            # Environment tag — emit only if present so a sample
            # collected before the environment feature shipped
            # round-trips cleanly without a fake "unknown" string.
            env = entry.get("environment")
            if isinstance(env, str) and env:
                # Custom environments include free-form user text;
                # collapse to "custom" only (the bare category) for
                # the contributed payload to keep the upload from
                # leaking lab-specific phrasing.
                if env.startswith("custom"):
                    clean_entry["environment"] = "custom"
                else:
                    clean_entry["environment"] = env
            # Sparge-gas tag — likewise forwarded as-is (it's a
            # canonical short_code with no PII concern). Missing
            # field → "ambient" by convention; recorded entries
            # only carry the tag when N₂ / Ar was selected.
            sg = entry.get("sparge_gas")
            if isinstance(sg, str) and sg:
                clean_entry["sparge_gas"] = sg
            clean.append(clean_entry)
        if clean:
            sanitised_bins[key] = clean

    # Stamp the payload with versions / OS labels so the maintainer
    # can correlate data quality with build / Python version /
    # platform. None of these are PII — every Python build of the
    # same release produces the same triple.
    try:
        from . import __version__ as _stimtest_version
    except Exception:
        _stimtest_version = "unknown"
    import platform as _plat
    payload: dict = {
        "format": "stimtest-electrode-history",
        "format_version": HISTORY_VERSION,
        "stimtest_version": _stimtest_version,
        "python_version": _plat.python_version(),
        "platform": _plat.system(),  # "Windows" / "Linux" / "Darwin"
        "samples": sanitised_bins,
    }

    # Attribution: each include-flag must be True AND the value must
    # be non-empty. Both halves matter — a True flag with an empty
    # string still produces an anonymous payload.
    if include_user_name and user_name and user_name.strip():
        payload["user_name"] = user_name.strip()
    if include_user_email and user_email and user_email.strip():
        payload["user_email"] = user_email.strip()
    if include_institution and institution and institution.strip():
        payload["institution"] = institution.strip()
    if include_session_name and session_name and session_name.strip():
        payload["session_name"] = session_name.strip()
    if note and str(note).strip():
        payload["note"] = str(note).strip()

    return payload


def import_payload(payload: dict) -> int:
    """Round-trip helper: ingest an anonymised payload back into
    the local store.

    Used by the project maintainer to absorb contributed datasets
    back onto a development machine — the GUI accumulates the
    pooled samples and the catalog defaults can be re-derived
    from the running mean. Returns the number of samples actually
    ingested. Silent on a missing or malformed payload (returns 0).

    The format check rejects payloads with an unfamiliar
    ``format_version``; missing date strings are accepted but
    stored as the import-time date so the FIFO eviction in
    ``record_sample`` still has a meaningful ordering.
    """
    if not isinstance(payload, dict):
        return 0
    if payload.get("format") != "stimtest-electrode-history":
        return 0
    if payload.get("format_version") != HISTORY_VERSION:
        return 0
    samples = payload.get("samples")
    if not isinstance(samples, dict):
        return 0
    n = 0
    for key, entries in samples.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            try:
                v = float(entry["v"])
            except (KeyError, TypeError, ValueError):
                continue
            phase = entry.get("phase", "pre")
            source = entry.get("source", "return")
            # Reconstruct a minimal ISO timestamp from the day-only
            # ``date`` field so the on-disk schema (which expects
            # ``ts``) round-trips cleanly. Adds noon UTC so the
            # FIFO ordering is stable and the time-of-day strip on
            # the next export keeps the same date.
            date = entry.get("date") or ""
            if isinstance(date, str) and len(date) == 10:
                try:
                    ts = datetime.fromisoformat(date + "T12:00:00")
                except ValueError:
                    ts = None
            else:
                ts = None
            record_sample(str(key), v, phase=phase, source=source,
                          timestamp=ts)
            n += 1
    return n


def reset(coating_name: Optional[str] = None) -> None:
    """Wipe the stored history.

    ``coating_name=None`` clears every bin (full factory-reset);
    passing a specific name only clears that one canonical bin.
    Used by tests and by the (future) "Reset learned OCP" entry in
    the calibration menu. Silent on read failure — there's nothing
    to reset, in that case.
    """
    with _LOCK:
        data = _load_raw()
        bins = data.get("samples") or {}
        if not isinstance(bins, dict):
            bins = {}
        if coating_name is None:
            bins = {}
        else:
            key = canonical_key(coating_name)
            if key and key in bins:
                bins.pop(key, None)
        data["version"] = HISTORY_VERSION
        data["samples"] = bins
        _write_atomic(data)


def summary() -> List[Tuple[str, int, Optional[float]]]:
    """Return ``[(coating_key, n_samples, learned_ocp_or_None), …]``.

    Sorted by coating key for stable output. Convenience for status
    panels and the upcoming "what has the GUI learned about my
    electrodes?" inspector.
    """
    out: List[Tuple[str, int, Optional[float]]] = []
    for key, samples in sorted(all_bins().items()):
        n = len(samples)
        ocp = None
        if n >= MIN_SAMPLES_FOR_LEARNED_OCP:
            try:
                vals = [float(s["v"]) for s in samples]
                ocp = sum(vals) / float(len(vals))
            except (KeyError, TypeError, ValueError):
                ocp = None
        out.append((key, n, ocp))
    return out


# Re-export an alias of ``record_sample`` for callers that prefer
# the verb-first form. Keeps the public surface flexible without
# committing to one naming convention.
record = record_sample


__all__ = [
    "MIN_SAMPLES_FOR_LEARNED_OCP",
    "MAX_SAMPLES_PER_BIN",
    "HISTORY_VERSION",
    "HISTORY_FILE",
    "canonical_key",
    "history_path",
    "record_sample",
    "record_pre_post_pair",
    "record_capture",
    "record",
    "sample_count",
    "learned_ocp_v",
    "all_bins",
    "reset",
    "summary",
    "export_anonymized_payload",
    "import_payload",
]
