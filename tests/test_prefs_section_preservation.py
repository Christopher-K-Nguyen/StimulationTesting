"""An auto-save must not delete prefs sections it doesn't own.

``_write_prefs_atomic`` is a WHOLESALE REPLACE, but several sections are
maintained by a read-modify-write against ``load_prefs`` somewhere else
entirely:

* ``theme``                  -- View menu (main_window ``_on_theme_selected``)
* ``stim_scaling_by_serial`` -- Verification + ConnectionPanel

``MainWindow._collect_prefs_payload`` returns a literal dict that lists only
setup / vt / sp / cp / lp / ps / results / view / admin / imported_profiles,
so every ``save_prefs(self._collect_prefs_payload())`` silently DELETED those
sections -- and one of those calls fires on ``closeEvent``.  Net effect: the
theme could not survive an app close, and a verification's serial->preset
record was lost on the next save.
"""
from __future__ import annotations

import json

import pytest

from stimtest.gui import prefs as prefs_mod


@pytest.fixture()
def prefs_file(tmp_path, monkeypatch):
    p = tmp_path / "gui_prefs.setting"
    monkeypatch.setattr(prefs_mod, "prefs_path", lambda: p)
    return p


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def test_theme_survives_a_save_that_omits_it(prefs_file):
    """The regression: the theme switcher could not survive an app close."""
    prefs_file.write_text(json.dumps({
        "setup": {"a": 1},
        "theme": {"mode": "dark"},
    }), encoding="utf-8")

    # A payload shaped like _collect_prefs_payload -- no "theme" key.
    prefs_mod.save_prefs({"setup": {"a": 2}, "vt": {}})

    data = _read(prefs_file)
    assert data["theme"] == {"mode": "dark"}, "theme was wiped by the save"
    assert data["setup"] == {"a": 2}, "the payload must still win"


def test_serial_scaling_survives_a_save_that_omits_it(prefs_file):
    """Verification's serial->preset record is written by read-modify-write
    and was deleted by the next auto-save."""
    prefs_file.write_text(json.dumps({
        "setup": {},
        "stim_scaling_by_serial": {"PLX00178": "NIL"},
    }), encoding="utf-8")

    prefs_mod.save_prefs({"setup": {"x": 1}})

    assert _read(prefs_file)["stim_scaling_by_serial"] == {"PLX00178": "NIL"}


def test_payload_wins_over_the_on_disk_copy(prefs_file):
    """Preservation must never shadow a genuine update."""
    prefs_file.write_text(json.dumps({"theme": {"mode": "dark"}}),
                          encoding="utf-8")
    prefs_mod.save_prefs({"theme": {"mode": "light"}})
    assert _read(prefs_file)["theme"] == {"mode": "light"}


def test_meta_is_not_carried_forward_as_a_section(prefs_file):
    """``_meta`` is written fresh by the writer; it must not be resurrected
    from the previous file as if it were a user section."""
    prefs_file.write_text(json.dumps({
        "_meta": {"version": 0, "path": "somewhere/else"},
        "setup": {},
    }), encoding="utf-8")
    prefs_mod.save_prefs({"setup": {}})
    meta = _read(prefs_file)["_meta"]
    assert meta["path"] == str(prefs_file)
    assert meta["version"] == prefs_mod.PREFS_VERSION


def test_first_save_with_no_existing_file(prefs_file):
    assert not prefs_file.exists()
    prefs_mod.save_prefs({"setup": {"a": 1}})
    assert _read(prefs_file)["setup"] == {"a": 1}


def test_corrupt_existing_file_does_not_block_the_save(prefs_file):
    prefs_file.write_text("{not json", encoding="utf-8")
    prefs_mod.save_prefs({"setup": {"a": 1}})
    assert _read(prefs_file)["setup"] == {"a": 1}


def test_non_dict_top_level_does_not_block_the_save(prefs_file):
    prefs_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    prefs_mod.save_prefs({"setup": {"a": 1}})
    assert _read(prefs_file)["setup"] == {"a": 1}


def test_imported_profiles_survive_a_round_trip(prefs_file):
    """``_collect_prefs_payload`` writes ``imported_profiles`` as a LIST, but
    ``load_prefs`` filtered to dict-valued sections only — so an imported
    profile (e.g. CWRU) was written correctly and then silently dropped on
    load, and ``_load_imported_profiles`` re-registered nothing."""
    profiles = [{"name": "cwru", "display_name": "CWRU",
                 "password_hash": "0" * 64, "shapes": ["bowtie"]}]
    prefs_mod.save_prefs({"setup": {}, "imported_profiles": profiles})
    loaded = prefs_mod.load_prefs()
    assert loaded.get("imported_profiles") == profiles


def test_load_prefs_still_strips_scalars_and_meta(prefs_file):
    """Widening to lists must not start returning scalars or metadata."""
    prefs_file.write_text(json.dumps({
        "setup": {"a": 1},
        "imported_profiles": [],
        "_meta": {"version": 1},
        "stray_scalar": 7,
        "stray_string": "nope",
    }), encoding="utf-8")
    loaded = prefs_mod.load_prefs()
    assert set(loaded) == {"setup", "imported_profiles"}


def test_export_profile_is_not_merged(tmp_path):
    """``save_prefs_to`` is an EXPLICIT export: it must be exactly what was
    snapshotted, not a union with whatever file the user picked."""
    dest = tmp_path / "profile.setting"
    dest.write_text(json.dumps({"theme": {"mode": "dark"}, "setup": {}}),
                    encoding="utf-8")
    prefs_mod.save_prefs_to(dest, {"setup": {"a": 1}})
    data = _read(dest)
    assert "theme" not in data
    assert data["setup"] == {"a": 1}
