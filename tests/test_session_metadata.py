"""Tests for Task #57: richer session metadata + per-run notes / tags.

Verifies:

* ``Session`` dataclass gained ``notes`` (str), ``tags``
  (List[str]), and ``system_metadata`` (Dict[str, str]) fields with
  sane defaults.
* ``capture_system_metadata`` returns a dict with every documented
  PULSAR / runtime / OS / package field present (empty string
  rather than missing key when a probe fails).
* Hardware identity flattening — when stim/scope info is passed,
  serial / firmware / model land in the dict.
* Setup-snapshot SHA-256 is deterministic for identical input,
  different for differing input.
* persistence.save_session_npz / load_session_npz round-trip the
  three new fields.
* Loading a legacy .npz (no notes / tags / system_metadata keys
  in meta.json) doesn't raise — fields default to "", [], {}.
* Tag sanitization: non-string entries dropped, blanks dropped,
  case normalized to lowercase.
"""
from __future__ import annotations

import json

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Session dataclass shape
# ---------------------------------------------------------------------------
def test_session_has_new_fields_with_defaults():
    """New fields exist with sane defaults so existing constructors
    that don't pass them keep working."""
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray

    sess = Session(
        notebook="", subject="",
        test=TestParameters(
            experiment="VT", duration_s=0.0,
            polarization_method="MP",
            counter_electrode_label="",
            reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1),
            pattern=PulsePattern.biphasic(amplitude_ua=100.0),
            array=ElectrodeArray.utah_4x4()),
    )
    assert sess.notes == ""
    assert sess.tags == []
    assert sess.system_metadata == {}


def test_session_notes_tags_metadata_settable():
    """Caller can populate the new fields at construction."""
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray

    sess = Session(
        notebook="nb", subject="subj",
        test=TestParameters(
            experiment="VT", duration_s=0.0,
            polarization_method="MP",
            counter_electrode_label="",
            reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1),
            pattern=PulsePattern.biphasic(amplitude_ua=100.0),
            array=ElectrodeArray.utah_4x4()),
        notes="ran on bench A; electrode #2 acted strange",
        tags=["post-coating", "control"],
        system_metadata={"pulsar_version": "0.2.0"},
    )
    assert sess.notes.startswith("ran on bench")
    assert "control" in sess.tags
    assert sess.system_metadata["pulsar_version"] == "0.2.0"


# ---------------------------------------------------------------------------
# capture_system_metadata
# ---------------------------------------------------------------------------
def test_capture_metadata_has_pulsar_identity_fields():
    """PULSAR identity fields are always present (empty string when
    not a git checkout, etc.)."""
    from stimtest.session_metadata import capture_system_metadata

    md = capture_system_metadata()
    for field in ("pulsar_version", "pulsar_git_hash",
                  "pulsar_git_branch", "pulsar_git_dirty"):
        assert field in md, f"missing {field!r} in metadata"
        # Either a non-empty string or the explicit empty fallback.
        assert isinstance(md[field], str)


def test_capture_metadata_has_runtime_environment_fields():
    """Python + OS environment fields are present."""
    from stimtest.session_metadata import capture_system_metadata

    md = capture_system_metadata()
    for field in ("python_version", "python_implementation",
                  "os_name", "os_version", "os_platform", "machine"):
        assert field in md
        # Python version is the one field we KNOW is non-empty (we're
        # running Python right now).
        if field == "python_version":
            assert md[field], "python_version should be non-empty"


def test_capture_metadata_has_package_version_fields():
    """Common package version fields present; numpy is always
    installed in dev so we can assert non-empty there."""
    from stimtest.session_metadata import capture_system_metadata

    md = capture_system_metadata()
    for pkg in ("numpy", "scipy", "pyqt6", "pyvisa",
                "pyserial", "matplotlib", "openpyxl", "pandas",
                "scikit_learn"):
        key = f"pkg_{pkg}_version"
        assert key in md, f"missing {key!r}"
    # numpy is always installed in test env.
    assert md["pkg_numpy_version"], "numpy version should be non-empty"


def test_capture_metadata_flattens_stim_info():
    """When a stimulator with .info is passed, its fields land in
    the metadata dict."""
    from stimtest.hardware.base import StimulatorInfo
    from stimtest.session_metadata import capture_system_metadata

    class _StubStim:
        info = StimulatorInfo(
            serial_number="PLX99999",
            firmware="1.2.3",
            description="TestStim",
            n_channels=16,
            is_simulated=False,
        )

    md = capture_system_metadata(stimulator=_StubStim())
    assert md.get("stim_serial") == "PLX99999"
    assert md.get("stim_firmware") == "1.2.3"
    assert md.get("stim_n_channels") == "16"
    assert md.get("stim_is_simulated") == "False"


def test_capture_metadata_flattens_scope_info():
    """When a scope with .info + channel_aliases is passed, fields
    land in the metadata dict."""
    from stimtest.hardware.base import ScopeInfo
    from stimtest.session_metadata import capture_system_metadata

    class _StubScope:
        info = ScopeInfo(
            make="TEKTRONIX", model="TBS2204B",
            serial="ABC123", firmware="v3.1.4",
            n_channels=4, is_simulated=False,
        )
        channel_aliases = {"vmon": "CH1", "imon": "CH2"}

    md = capture_system_metadata(oscilloscope=_StubScope())
    assert md.get("scope_make") == "TEKTRONIX"
    assert md.get("scope_model") == "TBS2204B"
    assert md.get("scope_serial") == "ABC123"
    assert md.get("scope_firmware") == "v3.1.4"
    # Aliases stringified as "alias=physical, alias=physical".
    aliases_str = md.get("scope_channel_aliases", "")
    assert "vmon=CH1" in aliases_str
    assert "imon=CH2" in aliases_str


def test_capture_metadata_handles_missing_drivers():
    """No stim / scope passed → no stim_* / scope_* fields, but
    everything else still present."""
    from stimtest.session_metadata import capture_system_metadata

    md = capture_system_metadata()
    assert "stim_serial" not in md
    assert "scope_serial" not in md
    assert "pulsar_version" in md  # other fields still there


def test_setup_snapshot_hash_deterministic():
    """Two sessions with the same test params produce the same
    setup_snapshot_sha256.  Required for cross-session 'did the
    setup change?' comparison."""
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.session_metadata import capture_system_metadata

    def _make_session():
        return Session(
            notebook="nb", subject="subj",
            test=TestParameters(
                experiment="VT", duration_s=0.0,
                polarization_method="MP",
                counter_electrode_label="",
                reference_electrode_label="",
                target_charge_phase_nc=0.0,
                configuration=Configuration.monopolar(1),
                pattern=PulsePattern.biphasic(
                    amplitude_ua=100.0, phase_width_us=200.0),
                array=ElectrodeArray.utah_4x4()),
        )

    md1 = capture_system_metadata(_make_session())
    md2 = capture_system_metadata(_make_session())
    assert md1.get("setup_snapshot_sha256") == md2.get("setup_snapshot_sha256")
    assert md1.get("setup_snapshot_sha256"), "hash should be non-empty"


def test_setup_snapshot_hash_changes_when_params_differ():
    """Different test params → different hash.  Required so the
    operator can spot config drift between two sessions."""
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    from stimtest.session_metadata import capture_system_metadata

    def _make(amp):
        return Session(
            notebook="", subject="",
            test=TestParameters(
                experiment="VT", duration_s=0.0,
                polarization_method="MP",
                counter_electrode_label="",
                reference_electrode_label="",
                target_charge_phase_nc=0.0,
                configuration=Configuration.monopolar(1),
                pattern=PulsePattern.biphasic(amplitude_ua=amp),
                array=ElectrodeArray.utah_4x4()),
        )

    h1 = capture_system_metadata(_make(100.0)).get("setup_snapshot_sha256")
    h2 = capture_system_metadata(_make(200.0)).get("setup_snapshot_sha256")
    assert h1 != h2, (
        "different amplitude → setup_snapshot_sha256 must differ; "
        "got identical hashes")


# ---------------------------------------------------------------------------
# persistence round-trip
# ---------------------------------------------------------------------------
def _make_session_with_metadata():
    """Build a session with notes / tags / system_metadata populated."""
    from stimtest.session import (
        Session, TestParameters, Capture, ChannelRun, CaptureMetrics,
        CaptureStatus,
    )
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray

    pat = PulsePattern.biphasic(amplitude_ua=100.0)
    sess = Session(
        notebook="nb", subject="subj",
        test=TestParameters(
            experiment="VT", duration_s=0.0,
            polarization_method="MP",
            counter_electrode_label="",
            reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1),
            pattern=pat,
            array=ElectrodeArray.utah_4x4()),
        notes="multi-line\nnotes\nwith newlines",
        tags=["post-coating", "pilot"],
        system_metadata={"pulsar_version": "0.2.0",
                         "pulsar_git_hash": "abc1234",
                         "scope_serial": "TEK99999"},
        runs=[ChannelRun(
            configuration=Configuration.monopolar(1),
            captures=[Capture(
                index=0, pattern=pat,
                time_us=np.arange(100.0, dtype=np.float64),
                v_mon_v=np.zeros(100, dtype=np.float64),
                i_mon_ua=np.zeros(100, dtype=np.float64),
                metrics=CaptureMetrics(),
                status=CaptureStatus(good=True))])],
    )
    return sess


def test_round_trip_notes(tmp_path):
    """notes round-trips through save → load including newlines."""
    from stimtest.persistence import save_session_npz, load_session_npz

    sess = _make_session_with_metadata()
    path = tmp_path / "sess.npz"
    save_session_npz(sess, path)
    loaded = load_session_npz(path)
    assert loaded.notes == sess.notes
    # Verify newlines preserved.
    assert "\n" in loaded.notes


def test_round_trip_tags(tmp_path):
    """tags round-trip as a list of strings."""
    from stimtest.persistence import save_session_npz, load_session_npz

    sess = _make_session_with_metadata()
    path = tmp_path / "sess.npz"
    save_session_npz(sess, path)
    loaded = load_session_npz(path)
    assert isinstance(loaded.tags, list)
    assert set(loaded.tags) == set(sess.tags)


def test_round_trip_system_metadata(tmp_path):
    """system_metadata round-trips as a flat str → str dict."""
    from stimtest.persistence import save_session_npz, load_session_npz

    sess = _make_session_with_metadata()
    path = tmp_path / "sess.npz"
    save_session_npz(sess, path)
    loaded = load_session_npz(path)
    assert loaded.system_metadata == sess.system_metadata


def test_load_legacy_npz_without_new_fields(tmp_path):
    """Loading a .npz whose meta.json predates the notes / tags /
    system_metadata fields should NOT raise — defaults to '', [], {}."""
    from stimtest.persistence import save_session_npz, load_session_npz

    sess = _make_session_with_metadata()
    path = tmp_path / "legacy.npz"
    save_session_npz(sess, path)

    # Hack the .npz to strip the new keys from meta.json.
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(z["meta.json"].tobytes().decode("utf-8"))
        arrays = {k: z[k] for k in z.files if k != "meta.json"}
    for key in ("notes", "tags", "system_metadata"):
        meta.pop(key, None)
    arrays["meta.json"] = np.frombuffer(
        json.dumps(meta).encode("utf-8"), dtype=np.uint8)
    np.savez_compressed(path, **arrays)

    # Reload — should default the missing fields without error.
    loaded = load_session_npz(path)
    assert loaded.notes == ""
    assert loaded.tags == []
    assert loaded.system_metadata == {}


def test_load_sanitizes_malformed_tags(tmp_path):
    """Tags list with non-string entries / blanks gets sanitized
    on load.  Defensive against hand-edited prefs / corrupted JSON."""
    from stimtest.persistence import save_session_npz, load_session_npz

    sess = _make_session_with_metadata()
    path = tmp_path / "sess.npz"
    save_session_npz(sess, path)

    # Inject garbage tags into meta.json.
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(z["meta.json"].tobytes().decode("utf-8"))
        arrays = {k: z[k] for k in z.files if k != "meta.json"}
    meta["tags"] = ["valid", "  ", "", None, 42, "  UPPER  ", "another"]
    arrays["meta.json"] = np.frombuffer(
        json.dumps(meta).encode("utf-8"), dtype=np.uint8)
    np.savez_compressed(path, **arrays)

    loaded = load_session_npz(path)
    # None / int / blanks dropped; "UPPER" lowercased + stripped.
    assert "valid" in loaded.tags
    assert "upper" in loaded.tags
    assert "another" in loaded.tags
    for bad in ("", "  ", "UPPER  "):
        assert bad not in loaded.tags
    # No None or int entries.
    assert all(isinstance(t, str) for t in loaded.tags)
