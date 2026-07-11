"""The bundled CWRU collaborator profile must be a valid, importable
``.pulsarprofile`` file.

Operator: "include the profile for them to import."  The installer ships
``installer/profiles/cwru.pulsarprofile.json``; collaborators import it
via Admin -> Import Profile…  This test guards the file against drift:
it must match the format PULSAR's importer validates
(``MainWindow._register_profile_payload``) — a 64-char SHA-256 hash, a
list of shape IDs that all exist in ``stimtest.waveforms.PHASE_SHAPES``,
and it must register cleanly + unlock those shapes.
"""
from __future__ import annotations

import json
import pathlib

import pytest

PROFILE = (pathlib.Path(__file__).resolve().parents[1]
           / "installer" / "profiles" / "cwru.pulsarprofile.json")

# The profile is gitignored (CWRU-private) — present in the operator's
# local build tree but absent on a public-repo checkout / CI.  Skip the
# content tests there rather than fail; they run wherever the file lives.
pytestmark = pytest.mark.skipif(
    not PROFILE.is_file(),
    reason="CWRU profile not present (gitignored; local build tree only)")


def test_bundled_cwru_profile_matches_importer_contract():
    from stimtest.waveforms import PHASE_SHAPES
    data = json.loads(PROFILE.read_text(encoding="utf-8"))
    assert data.get("name") == "cwru"
    assert data.get("display_name")                       # non-empty label
    h = str(data.get("password_hash", "")).lower()
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h), \
        "password_hash must be a 64-char SHA-256 hex digest"
    shapes = data.get("shapes")
    assert isinstance(shapes, list) and shapes, "shapes must be a non-empty list"
    unknown = [s for s in shapes if s not in PHASE_SHAPES]
    assert not unknown, f"profile references unknown shapes: {unknown}"


def test_bundled_cwru_profile_registers_and_unlocks_shapes():
    from stimtest.gui import admin
    data = json.loads(PROFILE.read_text(encoding="utf-8"))
    # Idempotent for the same (name, hash) — safe even if a pip-installed
    # stimtest_cwru already registered it at import time.
    admin.register_extension_profile(
        name=data["name"], password_hash=data["password_hash"],
        shapes=set(data["shapes"]), display_name=data.get("display_name"))
    prof = admin.get_extension_profile("cwru")
    assert prof is not None
    assert set(prof["shapes"]) == set(data["shapes"])
    assert set(data["shapes"]) <= admin.RESTRICTED_SHAPES, \
        "importing the profile must union its shapes into RESTRICTED_SHAPES"
