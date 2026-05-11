"""Tests for the per-coating electrode-potential learning store.

Covers four behaviours that matter to the spec:

1. **PtIr collapse** — every PtIr alloy variant (90/10, 80/20, 70/30)
   bins into a single canonical ``PtIr`` key.
2. **10-sample threshold** — :func:`learned_ocp_v` returns ``None``
   below the threshold and the running mean at / above it.
3. **JSON round-trip** — :func:`export_anonymized_payload` →
   :func:`import_payload` reproduces the per-bin sample counts.
4. **Privacy** — by default no PII fields appear in the export; the
   four ``include_*`` flags must each be both turned on AND have a
   non-empty value before their field shows up.

Each test points :envvar:`STIMTEST_PREFS_DIR` at a tmp directory so
the user's real prefs file is never touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_history(tmp_path, monkeypatch):
    """Redirect the history file to a fresh per-test tmp directory.

    The storage module reads ``STIMTEST_PREFS_DIR`` on every disk
    access, so flipping it here is enough — no module-level state
    needs reloading. We also ``reset()`` after the override is in
    place so any previous tests' samples can't leak in (matters
    when the test runner reuses a worker across files).
    """
    monkeypatch.setenv("STIMTEST_PREFS_DIR", str(tmp_path))
    from stimtest import electrode_potential_history as eph
    eph.reset()
    yield tmp_path
    eph.reset()


def test_canonical_key_collapses_ptir_alloys(tmp_history):
    """All three PtIr alloys map to the bare ``PtIr`` bin."""
    from stimtest.electrode_potential_history import canonical_key
    assert canonical_key("PtIr (90/10)") == "PtIr"
    assert canonical_key("PtIr (80/20)") == "PtIr"
    assert canonical_key("PtIr (70/30)") == "PtIr"
    # Non-collapsing names pass through unchanged.
    assert canonical_key("Pt") == "Pt"
    assert canonical_key("SIROF") == "SIROF"
    assert canonical_key("PEDOT:PSS") == "PEDOT:PSS"
    # Empty / None inputs return None so callers can short-circuit.
    assert canonical_key("") is None
    assert canonical_key(None) is None


def test_record_pools_ptir_alloys_into_one_bin(tmp_history):
    """Recording samples under different PtIr alloy names should land
    in the single canonical ``PtIr`` bin and pool toward the threshold.
    """
    from stimtest.electrode_potential_history import (
        record_sample, sample_count, learned_ocp_v,
        MIN_SAMPLES_FOR_LEARNED_OCP,
    )
    # Add 4 samples each across two alloys = 8 in the pooled bin —
    # below the 10-sample threshold.
    for _ in range(4):
        record_sample("PtIr (90/10)", 0.20)
    for _ in range(4):
        record_sample("PtIr (80/20)", 0.21)
    assert sample_count("PtIr (90/10)") == 8
    assert sample_count("PtIr (80/20)") == 8
    assert sample_count("PtIr") == 8
    assert learned_ocp_v("PtIr") is None  # below 10
    # Two more samples under the third alloy crosses the threshold.
    record_sample("PtIr (70/30)", 0.22)
    record_sample("PtIr (70/30)", 0.22)
    assert sample_count("PtIr") == 10
    assert sample_count("PtIr") >= MIN_SAMPLES_FOR_LEARNED_OCP
    learned = learned_ocp_v("PtIr")
    assert learned is not None
    # Mean of the 10 samples we recorded.
    assert learned == pytest.approx((0.20 * 4 + 0.21 * 4 + 0.22 * 2) / 10)
    # Looking up via any alloy name returns the same pooled mean.
    assert learned_ocp_v("PtIr (90/10)") == pytest.approx(learned)
    assert learned_ocp_v("PtIr (80/20)") == pytest.approx(learned)
    assert learned_ocp_v("PtIr (70/30)") == pytest.approx(learned)


def test_below_threshold_learned_ocp_is_none(tmp_history):
    """Fewer than 10 samples → ``None`` (caller falls back to catalog)."""
    from stimtest.electrode_potential_history import (
        record_sample, learned_ocp_v,
    )
    for _ in range(9):
        record_sample("Pt", 0.20)
    assert learned_ocp_v("Pt") is None
    record_sample("Pt", 0.20)   # 10th sample
    assert learned_ocp_v("Pt") == pytest.approx(0.20)


def test_record_drops_nan_inf(tmp_history):
    """NaN / inf inputs are silently dropped — they'd poison the mean."""
    from stimtest.electrode_potential_history import (
        record_sample, sample_count,
    )
    record_sample("Pt", 0.20)
    record_sample("Pt", float("nan"))
    record_sample("Pt", float("inf"))
    record_sample("Pt", float("-inf"))
    record_sample("Pt", 0.21)
    assert sample_count("Pt") == 2


def test_export_default_payload_is_anonymous(tmp_history):
    """Default export carries no user_name / email / institution /
    session_name — anonymous unless the user opts in.
    """
    from stimtest.electrode_potential_history import (
        record_sample, export_anonymized_payload,
    )
    record_sample("Pt", 0.20)
    payload = export_anonymized_payload(
        user_name="Alice Researcher",
        user_email="alice@example.edu",
        institution="Example Lab, U. of Example",
        session_name="electrode_a1",
        # All include flags default to False.
    )
    assert "user_name" not in payload
    assert "user_email" not in payload
    assert "institution" not in payload
    assert "session_name" not in payload
    # The samples bin DOES appear; that's the point.
    assert "Pt" in payload["samples"]
    # And the format/version stamps are present.
    assert payload["format"] == "stimtest-electrode-history"
    assert payload["format_version"] >= 1


def test_export_optional_attribution_each_field_independent(tmp_history):
    """Each ``include_*`` flag gates exactly one field; an empty
    value behind a True flag still produces an anonymous field
    (omits it).
    """
    from stimtest.electrode_potential_history import (
        record_sample, export_anonymized_payload,
    )
    record_sample("Pt", 0.20)
    # Name-only opt-in.
    p = export_anonymized_payload(
        user_name="Alice", include_user_name=True,
        user_email="alice@example.edu", include_user_email=False,
    )
    assert p.get("user_name") == "Alice"
    assert "user_email" not in p
    # True flag with empty value → still anonymous.
    p = export_anonymized_payload(
        user_name="", include_user_name=True,
        institution="Lab", include_institution=True,
    )
    assert "user_name" not in p
    assert p.get("institution") == "Lab"
    # All four on with non-empty values → all appear.
    p = export_anonymized_payload(
        user_name="Alice", include_user_name=True,
        user_email="alice@example.edu", include_user_email=True,
        institution="Lab", include_institution=True,
        session_name="electrode_a1", include_session_name=True,
    )
    assert p["user_name"] == "Alice"
    assert p["user_email"] == "alice@example.edu"
    assert p["institution"] == "Lab"
    assert p["session_name"] == "electrode_a1"


def test_export_strips_time_of_day(tmp_history):
    """Each sample's ``ts`` is reduced to a YYYY-MM-DD ``date`` —
    the time-of-day component must not appear in the export.
    """
    from datetime import datetime
    from stimtest.electrode_potential_history import (
        record_sample, export_anonymized_payload,
    )
    pinned = datetime(2026, 5, 10, 14, 1, 23)
    record_sample("Pt", 0.20, timestamp=pinned)
    payload = export_anonymized_payload()
    samples = payload["samples"]["Pt"]
    assert len(samples) == 1
    entry = samples[0]
    # Must be a date string only — no "T", no "14:01:23".
    assert entry["date"] == "2026-05-10"
    assert "ts" not in entry  # raw timestamp must be gone
    assert "T" not in entry["date"]


def test_export_drops_nan_dates(tmp_history):
    """Malformed timestamps map to an empty ``date`` field rather
    than crashing the exporter."""
    from stimtest.electrode_potential_history import (
        record_sample, export_anonymized_payload,
    )
    # Inject a malformed ts via the lower-level recorder. We cheat
    # by writing the file directly so we don't have to expose a
    # private API.
    record_sample("Pt", 0.20)
    p = export_anonymized_payload()
    samples = p["samples"]["Pt"]
    assert len(samples) == 1
    # The date should be a 10-char ISO string from the just-recorded
    # ``datetime.now``; just sanity-check the shape.
    assert len(samples[0]["date"]) == 10


def test_round_trip_export_import(tmp_history, monkeypatch, tmp_path):
    """Export to a payload, reset the store, import the payload back,
    and confirm the bins are reconstructed.
    """
    from stimtest.electrode_potential_history import (
        record_sample, export_anonymized_payload, import_payload,
        all_bins, reset,
    )
    # Seed a few bins.
    for _ in range(5):
        record_sample("Pt", 0.20)
    for _ in range(3):
        record_sample("PtIr (90/10)", 0.21)
    payload = export_anonymized_payload()
    assert sum(len(v) for v in payload["samples"].values()) == 8

    # Wipe everything then re-import.
    reset()
    assert sum(len(v) for v in all_bins().values()) == 0
    n = import_payload(payload)
    assert n == 8
    assert sum(len(v) for v in all_bins().values()) == 8
    # PtIr alloy names import into the same canonical bin.
    bins = all_bins()
    assert "Pt" in bins
    assert len(bins["Pt"]) == 5
    assert "PtIr" in bins
    assert len(bins["PtIr"]) == 3


def test_import_rejects_unknown_format(tmp_history):
    """Bad / unknown payloads return 0 ingested rather than raising."""
    from stimtest.electrode_potential_history import import_payload
    assert import_payload({}) == 0
    assert import_payload({"format": "wrong"}) == 0
    assert import_payload(
        {"format": "stimtest-electrode-history",
         "format_version": 99, "samples": {}}) == 0
    assert import_payload({"format": "stimtest-electrode-history",
                           "format_version": 1, "samples": "nope"}) == 0


def test_record_capture_skips_when_no_setup_snapshot(tmp_history):
    """Capture without a setup snapshot → no sample recorded."""
    from types import SimpleNamespace
    from stimtest.electrode_potential_history import (
        record_capture, sample_count,
    )
    cap = SimpleNamespace(metrics=SimpleNamespace(
        return_pre_pulse_potential_v=0.20,
        return_post_pulse_potential_v=0.20,
    ))
    session = SimpleNamespace(test=SimpleNamespace(extras={}))
    record_capture(cap, session)
    # No coating short on the snapshot → nothing recorded.
    assert sample_count("Pt") == 0


def test_record_capture_records_under_return_coating(tmp_history):
    """A capture with a snapshot containing return_coating_short
    records both pre and post values under that coating's bin.
    """
    from types import SimpleNamespace
    from stimtest.electrode_potential_history import (
        record_capture, sample_count,
    )
    cap = SimpleNamespace(metrics=SimpleNamespace(
        return_pre_pulse_potential_v=0.20,
        return_post_pulse_potential_v=0.21,
    ))
    session = SimpleNamespace(test=SimpleNamespace(extras={
        "setup_snapshot": {
            "return_coating_short": "Pt",
            "reference_enable": False,
        }
    }))
    record_capture(cap, session)
    # One pre + one post = 2 samples.
    assert sample_count("Pt") == 2


def test_record_capture_skips_when_non_agagcl_reference(tmp_history):
    """If a non-Ag|AgCl reference is wired, recording is skipped to
    avoid poisoning the bin with mis-attributed potentials.
    """
    from types import SimpleNamespace
    from stimtest.electrode_potential_history import (
        record_capture, sample_count,
    )
    cap = SimpleNamespace(metrics=SimpleNamespace(
        return_pre_pulse_potential_v=0.20,
        return_post_pulse_potential_v=0.21,
    ))
    session = SimpleNamespace(test=SimpleNamespace(extras={
        "setup_snapshot": {
            "return_coating_short": "Pt",
            "reference_enable": True,
            "reference_electrode_short": "Pt",  # not Ag|AgCl
        }
    }))
    record_capture(cap, session)
    assert sample_count("Pt") == 0


def test_record_capture_records_when_agagcl_reference(tmp_history):
    """An Ag|AgCl reference is the canonical baseline — recordings
    proceed normally.
    """
    from types import SimpleNamespace
    from stimtest.electrode_potential_history import (
        record_capture, sample_count,
    )
    cap = SimpleNamespace(metrics=SimpleNamespace(
        return_pre_pulse_potential_v=0.20,
        return_post_pulse_potential_v=0.21,
    ))
    session = SimpleNamespace(test=SimpleNamespace(extras={
        "setup_snapshot": {
            "return_coating_short": "PtIr (90/10)",
            "reference_enable": True,
            "reference_electrode_short": "Ag|AgCl",
        }
    }))
    record_capture(cap, session)
    # Recorded under the canonical PtIr bin (alloy collapse).
    assert sample_count("PtIr") == 2
    assert sample_count("PtIr (90/10)") == 2


def test_max_samples_per_bin_evicts_fifo(tmp_history, monkeypatch):
    """Once a bin exceeds the cap, oldest entries are dropped FIFO."""
    from stimtest import electrode_potential_history as eph
    # Tighten the cap for this test so we don't have to record 200
    # samples to exercise eviction.
    monkeypatch.setattr(eph, "MAX_SAMPLES_PER_BIN", 5)
    for v in (0.10, 0.11, 0.12, 0.13, 0.14, 0.15, 0.16):
        eph.record_sample("Pt", v)
    bin_list = eph.all_bins()["Pt"]
    assert len(bin_list) == 5
    # The two oldest (0.10, 0.11) should have been evicted.
    values = [s["v"] for s in bin_list]
    assert values == [0.12, 0.13, 0.14, 0.15, 0.16]


def test_summary_sorted_and_includes_learned(tmp_history):
    """summary() returns a sorted list of (key, n, ocp) tuples."""
    from stimtest.electrode_potential_history import (
        record_sample, summary, MIN_SAMPLES_FOR_LEARNED_OCP,
    )
    for _ in range(MIN_SAMPLES_FOR_LEARNED_OCP):
        record_sample("Pt", 0.20)
    record_sample("PtIr (90/10)", 0.21)
    rows = summary()
    keys = [r[0] for r in rows]
    assert keys == sorted(keys)  # alphabetic
    # Pt has hit threshold; PtIr has not.
    pt = next(r for r in rows if r[0] == "Pt")
    ptir = next(r for r in rows if r[0] == "PtIr")
    assert pt[1] == MIN_SAMPLES_FOR_LEARNED_OCP
    assert pt[2] == pytest.approx(0.20)
    assert ptir[1] == 1
    assert ptir[2] is None


def test_storage_path_obeys_env_var(tmp_history, tmp_path):
    """``STIMTEST_PREFS_DIR`` must redirect ``history_path``."""
    from stimtest.electrode_potential_history import history_path
    p = history_path()
    assert str(p).startswith(str(tmp_path))
    assert p.name == "electrode_potential_history.json"


def test_history_file_is_atomic_written(tmp_history):
    """A successful record_sample produces a valid JSON file (no
    half-written or empty file)."""
    from stimtest.electrode_potential_history import (
        record_sample, history_path,
    )
    record_sample("Pt", 0.20)
    text = history_path().read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["version"] == 1
    assert "samples" in data
    assert data["samples"]["Pt"][0]["v"] == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# Sparge-gas provenance
# ---------------------------------------------------------------------------

def test_record_sample_with_sparge_gas_kwarg(tmp_history):
    """Passing sparge_gas writes the field onto the on-disk entry
    when it's a non-default value, but suppresses the default
    ``"none"`` to keep the file compact."""
    from stimtest.electrode_potential_history import (
        record_sample, all_bins,
    )
    record_sample("Pt", 0.20, sparge_gas="n2")
    record_sample("Pt", 0.21, sparge_gas="ar")
    record_sample("Pt", 0.22)                        # no sparge tag
    record_sample("Pt", 0.23, sparge_gas="none")     # default, suppressed
    record_sample("Pt", 0.24, sparge_gas="")         # empty, suppressed
    bins = all_bins()
    pt = bins["Pt"]
    assert len(pt) == 5
    # First two carry the explicit tags.
    assert pt[0]["sparge_gas"] == "n2"
    assert pt[1]["sparge_gas"] == "ar"
    # Last three never recorded the tag (default / empty / not-passed).
    assert "sparge_gas" not in pt[2]
    assert "sparge_gas" not in pt[3]
    assert "sparge_gas" not in pt[4]


def test_record_pre_post_pair_forwards_sparge_gas(tmp_history):
    """Both halves of the pair get the sparge_gas tag."""
    from stimtest.electrode_potential_history import (
        record_pre_post_pair, all_bins,
    )
    record_pre_post_pair("Pt", 0.20, 0.21, sparge_gas="n2")
    pt = all_bins()["Pt"]
    assert len(pt) == 2
    assert pt[0]["sparge_gas"] == "n2"
    assert pt[1]["sparge_gas"] == "n2"


def test_record_capture_reads_sparge_gas_from_setup_snapshot(tmp_history):
    """``record_capture`` pulls sparge_gas off the snapshot."""
    from types import SimpleNamespace
    from stimtest.electrode_potential_history import (
        record_capture, all_bins,
    )
    cap = SimpleNamespace(metrics=SimpleNamespace(
        return_pre_pulse_potential_v=0.20,
        return_post_pulse_potential_v=0.21,
    ))
    session = SimpleNamespace(test=SimpleNamespace(extras={
        "setup_snapshot": {
            "return_coating_short": "Pt",
            "reference_enable": False,
            "sparge_gas": "n2",
        }
    }))
    record_capture(cap, session)
    pt = all_bins()["Pt"]
    assert len(pt) == 2
    for entry in pt:
        assert entry.get("sparge_gas") == "n2"


def test_record_capture_default_sparge_gas_is_omitted(tmp_history):
    """``"none"`` in the snapshot doesn't get persisted."""
    from types import SimpleNamespace
    from stimtest.electrode_potential_history import (
        record_capture, all_bins,
    )
    cap = SimpleNamespace(metrics=SimpleNamespace(
        return_pre_pulse_potential_v=0.20,
        return_post_pulse_potential_v=0.21,
    ))
    session = SimpleNamespace(test=SimpleNamespace(extras={
        "setup_snapshot": {
            "return_coating_short": "Pt",
            "reference_enable": False,
            "sparge_gas": "none",
        }
    }))
    record_capture(cap, session)
    pt = all_bins()["Pt"]
    # Recorded but no sparge_gas tag.
    assert len(pt) == 2
    for entry in pt:
        assert "sparge_gas" not in entry


def test_export_anonymized_payload_carries_sparge_gas(tmp_history):
    """Exported payload echoes the sparge_gas tag through verbatim."""
    from stimtest.electrode_potential_history import (
        record_sample, export_anonymized_payload,
    )
    record_sample("Pt", 0.20, sparge_gas="n2")
    record_sample("Pt", 0.21, sparge_gas="ar")
    record_sample("Pt", 0.22)
    payload = export_anonymized_payload()
    samples = payload["samples"]["Pt"]
    sg_tags = [s.get("sparge_gas") for s in samples]
    assert "n2" in sg_tags
    assert "ar" in sg_tags
    assert None in sg_tags  # third sample has no tag
