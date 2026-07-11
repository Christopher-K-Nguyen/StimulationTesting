"""Tests for plugin-host profile import/export (Admin → Import/Export
Profile).

A profile is pure DATA — ``{name, display_name, password_hash, shapes}``
where the shapes are IDs already built into ``stimtest.waveforms``.
Import/export therefore needs no code execution: it just registers
validated data against the plugin host.

Covers:
  * ``register_extension_profile`` records each profile's OWN shapes
    (``_extension_shapes``) separately from the global
    ``RESTRICTED_SHAPES`` union → export can reconstruct one profile.
  * ``get_extension_profile`` / ``list_extension_profiles`` accessors.
  * ``MainWindow._register_profile_payload`` validation (the import
    path): valid, missing name, bad hash, string-shapes, unknown-shape
    filtering.
  * ``MainWindow._persist_imported_profile`` de-dups by name.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest

from stimtest.gui import admin

H = hashlib.sha256(b"pw").hexdigest()
H2 = hashlib.sha256(b"other").hexdigest()


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Save/clear/restore the global extension registry so these tests
    neither see nor leak state to other test modules."""
    with admin._registry_lock:
        saved = (dict(admin._extension_profiles),
                 dict(admin._extension_display_names),
                 dict(admin._extension_shapes),
                 set(admin.RESTRICTED_SHAPES))
        admin._extension_profiles.clear()
        admin._extension_display_names.clear()
        admin._extension_shapes.clear()
        admin.RESTRICTED_SHAPES.clear()
    yield
    with admin._registry_lock:
        admin._extension_profiles.clear()
        admin._extension_profiles.update(saved[0])
        admin._extension_display_names.clear()
        admin._extension_display_names.update(saved[1])
        admin._extension_shapes.clear()
        admin._extension_shapes.update(saved[2])
        admin.RESTRICTED_SHAPES.clear()
        admin.RESTRICTED_SHAPES.update(saved[3])


# ----------------------------------------------------------------------
# admin registry accessors (export side)
# ----------------------------------------------------------------------
def test_get_profile_returns_its_own_shapes():
    admin.register_extension_profile(
        name="cwru", password_hash=H,
        shapes={"halfpipe", "bowtie"}, display_name="CWRU")
    prof = admin.get_extension_profile("cwru")
    assert prof == {
        "name": "cwru", "display_name": "CWRU",
        "password_hash": H, "shapes": ["bowtie", "halfpipe"]}  # sorted


def test_per_profile_shapes_isolated_from_global_union():
    admin.register_extension_profile(name="a", password_hash=H,
                                     shapes={"halfpipe"})
    admin.register_extension_profile(name="b", password_hash=H2,
                                     shapes={"gaussian"})
    assert admin.get_extension_profile("a")["shapes"] == ["halfpipe"]
    assert admin.get_extension_profile("b")["shapes"] == ["gaussian"]
    # The global union carries BOTH; the per-profile records don't bleed.
    assert {"halfpipe", "gaussian"} <= admin.RESTRICTED_SHAPES


def test_list_profiles_sorted_and_get_unknown_is_none():
    admin.register_extension_profile(name="zeta", password_hash=H)
    admin.register_extension_profile(name="alpha", password_hash=H)
    assert admin.list_extension_profiles() == ["alpha", "zeta"]
    assert admin.get_extension_profile("missing") is None
    assert admin.get_extension_profile("") is None


def test_export_snapshot_is_json_serializable():
    admin.register_extension_profile(name="cwru", password_hash=H,
                                     shapes={"bowtie"}, display_name="CWRU")
    prof = admin.get_extension_profile("cwru")
    round_tripped = json.loads(json.dumps(prof))  # must not raise
    assert round_tripped["name"] == "cwru"


def test_case_insensitive_re_register_keeps_one_profile():
    admin.register_extension_profile(name="cwru", password_hash=H,
                                     shapes={"bowtie"})
    admin.register_extension_profile(name="CWRU", password_hash=H,
                                     shapes={"halfpipe"})  # same hash, last-wins
    assert admin.list_extension_profiles() == ["cwru"]
    assert admin.get_extension_profile("cwru")["shapes"] == [
        "halfpipe"]  # last write wins on shapes


# ----------------------------------------------------------------------
# MainWindow import-validation path (called with a stub self — the
# method touches no instance state, so no GUI construction needed)
# ----------------------------------------------------------------------
def _validate(data):
    from stimtest.gui.main_window import MainWindow
    return MainWindow._register_profile_payload(
        SimpleNamespace(), data, source="t")


def test_import_accepts_valid_profile():
    ok, msg = _validate({"name": "cwru", "password_hash": H,
                         "shapes": ["bowtie", "halfpipe"]})
    assert ok and "2 shape" in msg


def test_import_rejects_missing_name():
    ok, msg = _validate({"password_hash": H, "shapes": []})
    assert not ok and "name" in msg.lower()


def test_import_rejects_bad_hash():
    ok, msg = _validate({"name": "x", "password_hash": "abc", "shapes": []})
    assert not ok and "hash" in msg.lower()
    assert "x" not in admin.list_extension_profiles()  # not registered


def test_import_rejects_string_shapes():
    ok, msg = _validate({"name": "y", "password_hash": H,
                         "shapes": "halfpipe"})
    assert not ok and "list" in msg.lower()


def test_import_filters_unknown_shapes_but_succeeds():
    ok, msg = _validate({"name": "z", "password_hash": H,
                         "shapes": ["bowtie", "not_a_real_shape"]})
    assert ok and "unknown" in msg.lower()
    # Only the known shape was registered into the global union.
    assert "bowtie" in admin.RESTRICTED_SHAPES
    assert "not_a_real_shape" not in admin.RESTRICTED_SHAPES


# ----------------------------------------------------------------------
# Persistence de-dup
# ----------------------------------------------------------------------
def test_persist_dedups_by_name_case_insensitively():
    from stimtest.gui.main_window import MainWindow
    fake = SimpleNamespace(
        _imported_profiles=[],
        _save_prefs_from_tabs=lambda: None,
        log_pane=SimpleNamespace(log=lambda m: None))
    MainWindow._persist_imported_profile(
        fake, {"name": "cwru", "password_hash": H, "shapes": ["bowtie"]})
    MainWindow._persist_imported_profile(
        fake, {"name": "CWRU", "password_hash": H, "shapes": ["halfpipe"]})
    assert len(fake._imported_profiles) == 1
    assert fake._imported_profiles[0]["shapes"] == ["halfpipe"]  # replaced
