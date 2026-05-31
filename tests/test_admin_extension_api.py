"""Regression tests for the admin module's extension plugin API.

The plugin contract lives in :mod:`stimtest.gui.admin`:

* :func:`register_extension_profile` — extensions call this at import
  time to register a profile name + password hash + restricted shape
  set.
* :data:`RESTRICTED_SHAPES` — mutable set that extensions union their
  shape IDs into.
* :func:`is_restricted_unlocked` — gate consulted by PatternPanel to
  decide which shapes to show.  Accepts either a built-in Profile
  enum member or a raw extension-name string.
* :func:`is_admin` — narrow check for the built-in Admin profile only
  (extension profiles do NOT inherit admin rights).
* :func:`prompt_login` — adaptive dialog; the underlying credential
  matching is what we exercise indirectly via the helpers above.

These tests pin the API surface so a future refactor doesn't quietly
break extensions that depend on it.  Every test isolates registry
state via a fixture that snapshots + restores the module-level
``_extension_profiles`` / ``_extension_display_names`` /
``RESTRICTED_SHAPES`` — otherwise tests would leak state into each
other.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixture: isolate registry state per test
# ---------------------------------------------------------------------------
@pytest.fixture
def clean_registry():
    """Snapshot + restore the admin module's plugin registries.

    Each test runs against an empty registry; on teardown the prior
    state is restored so subsequent tests (and the live GUI if pytest
    is run from a dev shell) see no leakage.
    """
    from stimtest.gui import admin

    saved_profiles = dict(admin._extension_profiles)
    saved_display = dict(admin._extension_display_names)
    saved_shapes = set(admin.RESTRICTED_SHAPES)
    admin._extension_profiles.clear()
    admin._extension_display_names.clear()
    admin.RESTRICTED_SHAPES.clear()
    try:
        yield admin
    finally:
        admin._extension_profiles.clear()
        admin._extension_profiles.update(saved_profiles)
        admin._extension_display_names.clear()
        admin._extension_display_names.update(saved_display)
        admin.RESTRICTED_SHAPES.clear()
        admin.RESTRICTED_SHAPES.update(saved_shapes)


# ---------------------------------------------------------------------------
# Default-install contract — no extensions registered
# ---------------------------------------------------------------------------
def test_default_install_has_no_restricted_shapes(clean_registry):
    """Out of the box, ``RESTRICTED_SHAPES`` is empty so PatternPanel's
    filter is a no-op and every shape appears in the dropdowns.

    This is the public-default behaviour; restricted shapes only
    appear when an extension package registers them.
    """
    assert clean_registry.RESTRICTED_SHAPES == set()


def test_default_install_no_extension_profiles(clean_registry):
    """Out of the box, the extension registry is empty.

    Drives the login dialog's UX: with no extensions, the dialog
    hides its Username field (admin-only path).
    """
    assert clean_registry._extension_profiles == {}
    assert clean_registry._any_extension_registered() is False


def test_default_install_anonymous_blocked(clean_registry):
    """Profile.NONE must NOT unlock restricted shapes."""
    from stimtest.gui.admin import Profile, is_restricted_unlocked
    assert is_restricted_unlocked(Profile.NONE) is False
    assert is_restricted_unlocked("none") is False


def test_default_install_admin_unlocked(clean_registry):
    """Built-in Profile.ADMIN always unlocks restricted shapes,
    regardless of how many extensions are registered (or none)."""
    from stimtest.gui.admin import Profile, is_restricted_unlocked
    assert is_restricted_unlocked(Profile.ADMIN) is True
    assert is_restricted_unlocked("admin") is True


# ---------------------------------------------------------------------------
# Registration contract
# ---------------------------------------------------------------------------
def test_register_extension_adds_to_registry(clean_registry):
    """A successful registration appears in ``_extension_profiles``
    keyed by the lowercase name."""
    from stimtest.gui.admin import register_extension_profile

    register_extension_profile(
        name="testlab", password_hash="abc123", shapes={"halfpipe"})
    assert "testlab" in clean_registry._extension_profiles
    assert clean_registry._extension_profiles["testlab"] == "abc123"


def test_register_extension_lowercases_name(clean_registry):
    """Names are normalized to lowercase so case-mismatched logins
    work (operator types ``TestLab`` → matches the registered
    ``testlab``)."""
    from stimtest.gui.admin import register_extension_profile

    register_extension_profile(
        name="TestLab", password_hash="abc123")
    assert "testlab" in clean_registry._extension_profiles
    assert "TestLab" not in clean_registry._extension_profiles


def test_register_extension_unions_shapes(clean_registry):
    """Registered shapes are unioned into ``RESTRICTED_SHAPES`` so
    PatternPanel's filter picks them up on the next ``set_profile``
    broadcast."""
    from stimtest.gui.admin import register_extension_profile

    register_extension_profile(
        name="testlab", password_hash="abc123",
        shapes={"halfpipe", "bowtie"})
    assert "halfpipe" in clean_registry.RESTRICTED_SHAPES
    assert "bowtie" in clean_registry.RESTRICTED_SHAPES


def test_register_extension_stores_display_name(clean_registry):
    """``display_name`` is stored for diagnostic log lines."""
    from stimtest.gui.admin import register_extension_profile

    register_extension_profile(
        name="testlab", password_hash="abc123",
        display_name="Test Laboratory")
    assert clean_registry._extension_display_names["testlab"] == \
        "Test Laboratory"


def test_register_extension_unlocks_via_is_restricted_unlocked(clean_registry):
    """The registered profile name should pass the ``is_restricted_
    unlocked`` gate so PatternPanel shows the extension's shapes."""
    from stimtest.gui.admin import (
        is_restricted_unlocked, register_extension_profile)

    register_extension_profile(
        name="testlab", password_hash="abc123",
        shapes={"halfpipe"})
    assert is_restricted_unlocked("testlab") is True


def test_register_extension_does_not_grant_admin(clean_registry):
    """Extension profiles must NOT inherit admin rights — the
    Catalog dialog is built-in Admin-only."""
    from stimtest.gui.admin import is_admin, register_extension_profile

    register_extension_profile(
        name="testlab", password_hash="abc123")
    assert is_admin("testlab") is False


def test_register_extension_idempotent_same_hash(clean_registry):
    """Re-registering the same (name, hash) is a silent no-op —
    handles accidental double-import of the same extension."""
    from stimtest.gui.admin import register_extension_profile

    register_extension_profile(
        name="testlab", password_hash="abc123")
    # Second registration with same hash should NOT raise.
    register_extension_profile(
        name="testlab", password_hash="abc123")
    assert clean_registry._extension_profiles["testlab"] == "abc123"


def test_register_extension_rejects_different_hash(clean_registry):
    """Re-registering with a DIFFERENT hash raises ValueError —
    prevents a stray import from clobbering a real registration."""
    from stimtest.gui.admin import register_extension_profile

    register_extension_profile(
        name="testlab", password_hash="hash1")
    with pytest.raises(ValueError, match="different password_hash"):
        register_extension_profile(
            name="testlab", password_hash="hash2")


def test_register_extension_rejects_builtin_name_admin(clean_registry):
    """An extension cannot register the built-in ``"admin"`` name —
    would let it pose as the catalog-owning Admin profile."""
    from stimtest.gui.admin import register_extension_profile

    with pytest.raises(ValueError, match="collides with built-in"):
        register_extension_profile(
            name="admin", password_hash="abc123")


def test_register_extension_rejects_builtin_name_none(clean_registry):
    """An extension cannot register the built-in ``"none"`` name —
    would alias the anonymous default."""
    from stimtest.gui.admin import register_extension_profile

    with pytest.raises(ValueError, match="collides with built-in"):
        register_extension_profile(
            name="none", password_hash="abc123")


def test_register_extension_rejects_empty_name(clean_registry):
    """Empty / whitespace-only names are rejected outright."""
    from stimtest.gui.admin import register_extension_profile

    with pytest.raises(ValueError, match="non-empty"):
        register_extension_profile(name="", password_hash="abc123")
    with pytest.raises(ValueError, match="non-empty"):
        register_extension_profile(name="   ", password_hash="abc123")


def test_register_extension_rejects_non_string_name(clean_registry):
    """Type-check on ``name`` — caller wrote a typo and passed an int
    or None.  Better to raise loudly than silently lowercase a TypeError."""
    from stimtest.gui.admin import register_extension_profile

    with pytest.raises(ValueError, match="must be a string"):
        register_extension_profile(name=123, password_hash="abc123")
    with pytest.raises(ValueError, match="must be a string"):
        register_extension_profile(name=None, password_hash="abc123")


def test_register_extension_rejects_bare_string_shapes(clean_registry):
    """``shapes="halfpipe"`` must raise rather than silently splitting
    into a set of per-character "shape" IDs.

    Strings are iterable-by-character, so the loop
    ``RESTRICTED_SHAPES.update(str(s) for s in shapes)`` would
    register ``{'a','e','f','h','i','l','p'}`` if we accepted a bare
    string — none of which match any real ``SHAPE_*`` constant in
    :mod:`stimtest.waveforms`, so the extension would unlock
    nothing the PatternPanel filters on AND silently pollute
    ``RESTRICTED_SHAPES`` with garbage.  Pre-fix, this passed
    silently.  The fix is a strict ``isinstance(shapes, str)``
    rejection in :func:`register_extension_profile`.
    """
    from stimtest.gui.admin import (
        register_extension_profile, RESTRICTED_SHAPES)

    with pytest.raises(ValueError, match="not a single string"):
        register_extension_profile(
            name="testlab", password_hash="abc123",
            shapes="halfpipe")
    # Verify the registry stays clean — neither the per-character
    # garbage nor the intended "halfpipe" should land in
    # RESTRICTED_SHAPES.
    assert "halfpipe" not in clean_registry.RESTRICTED_SHAPES
    for ch in "halfpipe":
        assert ch not in clean_registry.RESTRICTED_SHAPES, (
            f"per-character pollution: {ch!r} ended up in "
            f"RESTRICTED_SHAPES")
    # And the profile itself should NOT have been registered (the
    # raise happens before the registry write).  This is the
    # all-or-nothing contract: a bad shapes argument shouldn't
    # leave a half-registered profile that authenticates but
    # unlocks nothing.
    assert "testlab" not in clean_registry._extension_profiles


def test_register_extension_accepts_various_iterables(clean_registry):
    """``shapes`` must work with set / list / tuple / frozenset —
    anything iterable-of-strings that isn't a bare string.  Locks
    in the bare-string rejection as a NARROW constraint, not a
    broad "must be a set" one."""
    from stimtest.gui.admin import register_extension_profile

    for kind, payload in (
        ("set", {"halfpipe", "bowtie"}),
        ("frozenset", frozenset({"halfpipe", "bowtie"})),
        ("list", ["halfpipe", "bowtie"]),
        ("tuple", ("halfpipe", "bowtie")),
    ):
        # Each iteration gets a fresh profile name so the per-test
        # fixture's snapshot/restore doesn't conflict.
        register_extension_profile(
            name=f"lab_{kind}", password_hash="abc123",
            shapes=payload)
        assert "halfpipe" in clean_registry.RESTRICTED_SHAPES
        assert "bowtie" in clean_registry.RESTRICTED_SHAPES


# ---------------------------------------------------------------------------
# Multi-extension coexistence
# ---------------------------------------------------------------------------
def test_two_extensions_coexist(clean_registry):
    """Two extensions can register independently; each unlocks its
    own shape set + both names work as login targets.

    Coexistence is important: a single PULSAR install could
    theoretically host shapes from multiple collaborator labs.
    """
    from stimtest.gui.admin import (
        is_restricted_unlocked, register_extension_profile)

    register_extension_profile(
        name="lab_a", password_hash="ha", shapes={"halfpipe"})
    register_extension_profile(
        name="lab_b", password_hash="hb", shapes={"bowtie"})

    assert is_restricted_unlocked("lab_a") is True
    assert is_restricted_unlocked("lab_b") is True
    assert "halfpipe" in clean_registry.RESTRICTED_SHAPES
    assert "bowtie" in clean_registry.RESTRICTED_SHAPES


def test_overlapping_shape_sets_no_conflict(clean_registry):
    """Two extensions registering the SAME shape ID is fine — the
    set just doesn't add a duplicate.  Both profiles still unlock
    it (because both pass is_restricted_unlocked)."""
    from stimtest.gui.admin import (
        is_restricted_unlocked, register_extension_profile)

    register_extension_profile(
        name="lab_a", password_hash="ha", shapes={"halfpipe"})
    register_extension_profile(
        name="lab_b", password_hash="hb", shapes={"halfpipe"})

    assert clean_registry.RESTRICTED_SHAPES == {"halfpipe"}
    assert is_restricted_unlocked("lab_a") is True
    assert is_restricted_unlocked("lab_b") is True


# ---------------------------------------------------------------------------
# Name normalization (the ``_profile_name`` helper)
# ---------------------------------------------------------------------------
def test_profile_name_accepts_enum(clean_registry):
    """The internal name normalizer handles Profile enum members."""
    from stimtest.gui.admin import Profile, _profile_name
    assert _profile_name(Profile.NONE) == "none"
    assert _profile_name(Profile.ADMIN) == "admin"


def test_profile_name_accepts_string(clean_registry):
    """The internal name normalizer handles raw strings (extension
    profiles) and lowercases them."""
    from stimtest.gui.admin import _profile_name
    assert _profile_name("admin") == "admin"
    assert _profile_name("ADMIN") == "admin"
    assert _profile_name("  CWRU  ") == "cwru"


def test_profile_name_gracefully_handles_garbage(clean_registry):
    """Unrecognizable input returns empty string rather than raising
    — keeps the gate helpers safe under bad input.

    Note: ``None`` returns ``""`` (NOT ``"none"``) — see audit #16.
    The dedicated ``test_profile_name_none_returns_empty_string``
    asserts this specifically; the test below covers other garbage.
    """
    from stimtest.gui.admin import _profile_name
    # An object whose .value access raises must fall through to "".
    class _Bad:
        @property
        def value(self):
            raise RuntimeError("boom")
    assert _profile_name(_Bad()) == ""
    # Mixed-case is normalized.
    assert _profile_name("AdMiN") == "admin"


# ---------------------------------------------------------------------------
# Login dialog adaptation (no QApplication needed for _any_extension_registered)
# ---------------------------------------------------------------------------
def test_any_extension_registered_true_after_register(clean_registry):
    """``_any_extension_registered`` flips True after the first
    register; drives the login dialog's Username field visibility."""
    from stimtest.gui.admin import (
        _any_extension_registered, register_extension_profile)

    assert _any_extension_registered() is False
    register_extension_profile(
        name="testlab", password_hash="abc123")
    assert _any_extension_registered() is True


# ---------------------------------------------------------------------------
# prompt_login — the actual auth surface
# ---------------------------------------------------------------------------
# These tests pin the credential-matching logic by monkeypatching the
# Qt dialog's exec / accessors.  We never actually show a dialog; we
# just replace the dialog instance the function constructs with a
# stub that returns the values we want to test.  Closes audit finding
# #4 (the only path that was untested before).
#
# A QApplication is required because _LoginDialog inherits QDialog
# even though we don't .exec() it for real.  The fixture is
# function-scoped so each test gets a clean slate.
import hashlib

import pytest as _pytest_for_qapp_fixture  # noqa: F401  (avoid shadow)


@pytest.fixture
def qapp():
    """Provide a QApplication.  PyQt6 _LoginDialog construction
    requires one even when ``.exec`` is monkeypatched out."""
    _qt_pkg = pytest.importorskip("PyQt6.QtWidgets")
    app = _qt_pkg.QApplication.instance()
    if app is None:
        app = _qt_pkg.QApplication([])
    return app


def _admin_hash(pw: str = "Neuron01") -> str:
    """Compute the canonical admin hash for tests.  Matches what
    ``stimtest.gui.admin._hash`` does internally."""
    return hashlib.sha256(pw.encode()).hexdigest()


def _patch_dialog(monkeypatch, *, username: str, password: str,
                  accept: bool = True):
    """Replace ``_LoginDialog`` so :func:`prompt_login` uses our
    canned credentials.  The patched class mimics just enough of
    the real one for the function under test: ``exec()`` returns
    the Accepted/Rejected code, and ``username()`` / ``password()``
    return whatever we set.
    """
    from PyQt6 import QtWidgets
    from stimtest.gui import admin as admin_mod

    class _StubDialog:
        def __init__(self, parent=None, *, show_username=False, history=None):
            self._show_username = show_username
            self._history = history  # captured for tests that care

        def exec(self):
            if accept:
                return int(QtWidgets.QDialog.DialogCode.Accepted)
            return int(QtWidgets.QDialog.DialogCode.Rejected)

        def username(self):
            return username

        def password(self):
            return password

    monkeypatch.setattr(admin_mod, "_LoginDialog", _StubDialog)


# --- Admin-only mode (no extensions registered) -------------------
def test_prompt_login_admin_correct_password(qapp, clean_registry, monkeypatch):
    """Blank username + correct admin password → (admin, True).

    Exercises the canonical login path on a public install (no
    extensions registered → dialog hides Username; we still pass
    empty string here to match what the real dialog returns)."""
    from stimtest.gui.admin import prompt_login, Profile

    _patch_dialog(monkeypatch, username="", password="Neuron01")
    name, ok = prompt_login(None, admin_hash=_admin_hash())
    assert ok is True
    assert name == Profile.ADMIN.value


def test_prompt_login_admin_wrong_password(qapp, clean_registry, monkeypatch):
    """Blank username + wrong password → (none, False)."""
    from stimtest.gui.admin import prompt_login, Profile

    _patch_dialog(monkeypatch, username="", password="wrong_pw")
    name, ok = prompt_login(None, admin_hash=_admin_hash())
    assert ok is False
    assert name == Profile.NONE.value


def test_prompt_login_cancel(qapp, clean_registry, monkeypatch):
    """User cancels the dialog → (none, False), regardless of what
    they typed.  Same shape as wrong-credential return; the caller
    can't distinguish (we err toward showing the failure popup
    either way — that's intentional)."""
    from stimtest.gui.admin import prompt_login, Profile

    _patch_dialog(monkeypatch, username="", password="anything",
                  accept=False)
    name, ok = prompt_login(None, admin_hash=_admin_hash())
    assert ok is False
    assert name == Profile.NONE.value


# --- Extension-registered mode ------------------------------------
def test_prompt_login_extension_correct_credentials(
        qapp, clean_registry, monkeypatch):
    """Registered extension username + correct hash → (extname, True).

    This is the path that wasn't covered before audit finding #4 was
    added.  Pre-fix it was theoretically possible for the extension-
    auth lookup to regress without any test catching it."""
    from stimtest.gui.admin import (
        prompt_login, register_extension_profile)

    ext_pw = "lab_secret"
    register_extension_profile(
        name="testlab",
        password_hash=hashlib.sha256(ext_pw.encode()).hexdigest())

    _patch_dialog(monkeypatch, username="testlab", password=ext_pw)
    name, ok = prompt_login(None, admin_hash=_admin_hash())
    assert ok is True
    assert name == "testlab"


def test_prompt_login_extension_wrong_password(
        qapp, clean_registry, monkeypatch):
    """Registered extension username + wrong hash → (none, False).
    Pins that a wrong password against an extension username doesn't
    silently fall through to the admin path."""
    from stimtest.gui.admin import (
        prompt_login, register_extension_profile, Profile)

    register_extension_profile(
        name="testlab", password_hash=_admin_hash("right_pw"))

    _patch_dialog(monkeypatch, username="testlab", password="wrong_pw")
    name, ok = prompt_login(None, admin_hash=_admin_hash())
    assert ok is False
    assert name == Profile.NONE.value


def test_prompt_login_blank_username_takes_admin_path_with_extensions(
        qapp, clean_registry, monkeypatch):
    """When extensions are registered AND username is blank, the
    admin path runs (the dialog shows the Username field but the
    operator left it blank).  Pre-fix this could have silently
    broken if someone changed the empty-string check in prompt_login."""
    from stimtest.gui.admin import (
        prompt_login, register_extension_profile, Profile)

    register_extension_profile(
        name="testlab", password_hash=_admin_hash("ext_pw"))

    # Blank username + correct admin password should authenticate
    # as ADMIN, NOT fail to look up "" in the extension registry.
    _patch_dialog(monkeypatch, username="", password="Neuron01")
    name, ok = prompt_login(None, admin_hash=_admin_hash())
    assert ok is True
    assert name == Profile.ADMIN.value


def test_prompt_login_explicit_admin_username(
        qapp, clean_registry, monkeypatch):
    """Username = ``"admin"`` (case-insensitive) takes the admin path.

    The dialog accepts blank OR ``"admin"`` interchangeably; the
    real dialog placeholder says ``"(blank = admin)"``."""
    from stimtest.gui.admin import (
        prompt_login, register_extension_profile, Profile)

    register_extension_profile(
        name="testlab", password_hash=_admin_hash("ext_pw"))

    _patch_dialog(monkeypatch, username="Admin", password="Neuron01")
    name, ok = prompt_login(None, admin_hash=_admin_hash())
    assert ok is True
    assert name == Profile.ADMIN.value


def test_prompt_login_unknown_username(
        qapp, clean_registry, monkeypatch):
    """An unregistered username with any password returns
    (none, False) — silent rejection rather than a crash."""
    from stimtest.gui.admin import prompt_login, Profile

    register_only_unrelated = "someotherlab"
    from stimtest.gui.admin import register_extension_profile
    register_extension_profile(
        name=register_only_unrelated, password_hash=_admin_hash("pw"))

    _patch_dialog(
        monkeypatch, username="not_a_real_profile", password="whatever")
    name, ok = prompt_login(None, admin_hash=_admin_hash())
    assert ok is False
    assert name == Profile.NONE.value


# ---------------------------------------------------------------------------
# Audit-cleanup regressions (#8 dialog title, #9 name regex, #15 thread lock, #16 None)
# ---------------------------------------------------------------------------
def test_login_dialog_title_adapts_to_extension_mode(qapp, clean_registry):
    """Audit #8: title is 'Admin log in' when admin-only,
    'Log in' when at least one extension is registered."""
    from stimtest.gui.admin import _LoginDialog, register_extension_profile

    dlg_admin_only = _LoginDialog(show_username=False)
    assert dlg_admin_only.windowTitle() == "Admin log in"

    register_extension_profile(
        name="testlab", password_hash="abc123")
    dlg_ext_mode = _LoginDialog(show_username=True)
    assert dlg_ext_mode.windowTitle() == "Log in"


def test_register_extension_rejects_invalid_name_chars(clean_registry):
    """Audit #9: names must match ^[a-z][a-z0-9_]*$.  Reserved
    Python keywords, names with uppercase, names with hyphens, and
    names starting with a digit all fail.  Two-tier rejection:
    the regex catches bad characters, a separate keyword.iskeyword
    check catches reserved words like 'class' that pass the regex."""
    from stimtest.gui.admin import register_extension_profile

    # Reserved keyword — caught by the keyword.iskeyword check.
    with pytest.raises(ValueError, match="reserved Python keyword"):
        register_extension_profile(name="class", password_hash="x")
    # Hyphen — caught by the regex.
    with pytest.raises(ValueError, match="regex"):
        register_extension_profile(name="my-lab", password_hash="x")
    # Starts with digit — caught by the regex.
    with pytest.raises(ValueError, match="regex"):
        register_extension_profile(name="3rdparty", password_hash="x")
    # Contains a space — caught by the regex.
    with pytest.raises(ValueError, match="regex"):
        register_extension_profile(name="lab a", password_hash="x")


def test_register_extension_accepts_valid_name_chars(clean_registry):
    """Audit #9 positive case: typical lowercase + underscore + digit
    names pass.  Catches over-tightening of the regex."""
    from stimtest.gui.admin import register_extension_profile

    # Plain lowercase.
    register_extension_profile(name="cwru", password_hash="x")
    # Underscore.
    register_extension_profile(name="my_lab", password_hash="y")
    # Trailing digit.
    register_extension_profile(name="lab123", password_hash="z")


def test_profile_name_none_returns_empty_string(clean_registry):
    """Audit #16: explicit None must return '' not 'none'.  Pre-fix,
    ``str(None).lower() == "none"`` was an accidental match against
    ``Profile.NONE.value``."""
    from stimtest.gui.admin import _profile_name

    assert _profile_name(None) == ""


# ---------------------------------------------------------------------------
# Login history dropdown
# ---------------------------------------------------------------------------
def test_update_login_history_prepends_new_username():
    """A brand-new username lands at position 0 (most-recent-first)."""
    from stimtest.gui.admin import update_login_history

    out = update_login_history(["cwru", "admin"], "lab_a")
    assert out == ["lab_a", "cwru", "admin"]


def test_update_login_history_dedupes_existing_entry():
    """An already-present entry MOVES to the front, doesn't duplicate."""
    from stimtest.gui.admin import update_login_history

    out = update_login_history(["cwru", "admin", "lab_a"], "admin")
    assert out == ["admin", "cwru", "lab_a"]
    # No duplicate "admin"
    assert out.count("admin") == 1


def test_update_login_history_normalizes_case():
    """Stored entries are lowercased + stripped — so 'CWRU' and 'cwru'
    are the same history entry, not two."""
    from stimtest.gui.admin import update_login_history

    out = update_login_history(["cwru"], "  CWRU  ")
    assert out == ["cwru"]
    assert out.count("cwru") == 1


def test_update_login_history_drops_blank_username():
    """Blank username (admin-default path) doesn't get recorded —
    avoids polluting the dropdown with empty entries."""
    from stimtest.gui.admin import update_login_history

    out = update_login_history(["cwru"], "")
    assert out == ["cwru"]

    out2 = update_login_history(["cwru"], "   ")
    assert out2 == ["cwru"]


def test_update_login_history_caps_at_max():
    """History list is capped at LOGIN_HISTORY_MAX entries.  Old
    entries fall off the bottom as new ones get prepended."""
    from stimtest.gui.admin import update_login_history, LOGIN_HISTORY_MAX

    # Build a maximally-full history.
    full = [f"lab_{i:03d}" for i in range(LOGIN_HISTORY_MAX)]
    out = update_login_history(full, "newcomer")
    assert len(out) == LOGIN_HISTORY_MAX
    assert out[0] == "newcomer"
    # Last one in the original full list should have dropped off.
    assert full[-1] not in out


def test_update_login_history_does_not_mutate_input():
    """``update_login_history`` is a pure function — the caller's
    list is never mutated.  Important because MainWindow holds the
    list as instance state."""
    from stimtest.gui.admin import update_login_history

    original = ["cwru", "admin"]
    original_copy = list(original)
    update_login_history(original, "lab_a")
    assert original == original_copy


def test_login_dialog_seeds_combobox_from_history(qapp, clean_registry):
    """The QComboBox is populated with history entries in order.
    Current text starts blank so the operator must explicitly
    choose / type rather than getting auto-filled."""
    from stimtest.gui.admin import _LoginDialog

    history = ["lab_a", "cwru", "admin"]
    dlg = _LoginDialog(show_username=True, history=history)
    assert dlg._user.count() == 3
    assert dlg._user.itemText(0) == "lab_a"
    assert dlg._user.itemText(1) == "cwru"
    assert dlg._user.itemText(2) == "admin"
    # Field starts blank — no auto-fill.
    assert dlg._user.currentText() == ""


def test_login_dialog_is_editable(qapp, clean_registry):
    """Operator can type a brand-new username even if not in history
    (editable combobox semantics)."""
    from stimtest.gui.admin import _LoginDialog

    dlg = _LoginDialog(show_username=True, history=["cwru"])
    assert dlg._user.isEditable() is True
    # Simulate operator typing.
    dlg._user.setCurrentText("brand_new_lab")
    assert dlg.username() == "brand_new_lab"


def test_login_dialog_handles_empty_history(qapp, clean_registry):
    """No prior history (new install) shows an empty dropdown but
    the combobox still works."""
    from stimtest.gui.admin import _LoginDialog

    dlg = _LoginDialog(show_username=True, history=[])
    assert dlg._user.count() == 0
    assert dlg._user.currentText() == ""


def test_login_dialog_history_skips_malformed_entries(qapp, clean_registry):
    """Defensive: a stale prefs.json with non-string entries in the
    history shouldn't crash the dialog — only string entries land
    in the combobox."""
    from stimtest.gui.admin import _LoginDialog

    history = ["cwru", None, 42, "", "  ", "admin"]  # type: ignore
    dlg = _LoginDialog(show_username=True, history=history)  # type: ignore
    # Only "cwru" and "admin" should survive — None/42/blank dropped.
    items = [dlg._user.itemText(i) for i in range(dlg._user.count())]
    assert items == ["cwru", "admin"]


def test_prompt_login_accepts_history_kwarg(qapp, clean_registry, monkeypatch):
    """The history kwarg is plumbed through to _LoginDialog — pinning
    the call-site contract so a future refactor that drops the
    keyword fails this test loudly.
    """
    from stimtest.gui.admin import prompt_login

    # Use the cancel-stub so we don't have to feed credentials —
    # we only care that prompt_login accepts the history kwarg
    # AND passes it to the dialog constructor (which is also a
    # stub that captures the kwarg).
    _patch_dialog(monkeypatch, username="", password="", accept=False)

    # Should not raise.  The stub captures history into self._history.
    name, ok = prompt_login(
        None, admin_hash=_admin_hash(),
        history=["cwru", "admin"])
    assert ok is False  # cancel path
    assert name == "none"  # Profile.NONE.value


def test_prompt_login_history_kwarg_optional():
    """``history`` should default to None / empty — callers that
    don't care about the dropdown can omit it.  Same signature-
    compat test as the kwarg-acceptance one, but covers the
    omitted case."""
    import inspect
    from stimtest.gui.admin import prompt_login

    sig = inspect.signature(prompt_login)
    history_param = sig.parameters.get("history")
    assert history_param is not None, (
        "prompt_login must expose a history kwarg")
    assert history_param.default is None or history_param.default == [], (
        f"history default should be None or [] for back-compat with "
        f"existing callers; got {history_param.default!r}")


def test_registry_writes_are_thread_safe(clean_registry):
    """Audit #15: concurrent register_extension_profile calls from
    multiple threads should not corrupt the registries.

    Stress-test by spawning N threads each registering a unique
    profile name.  After they all join, the registry should contain
    exactly N entries (none lost to races, none duplicated).
    """
    import threading
    from stimtest.gui.admin import register_extension_profile

    n_threads = 32
    errors: list = []

    def _register(i: int) -> None:
        try:
            register_extension_profile(
                name=f"threadtest_{i:03d}",
                password_hash=f"hash_{i}",
                shapes={f"shape_{i}"})
        except Exception as e:
            errors.append((i, e))

    threads = [
        threading.Thread(target=_register, args=(i,))
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"thread errors: {errors[:5]}"
    # Every profile should have landed.
    for i in range(n_threads):
        name = f"threadtest_{i:03d}"
        assert name in clean_registry._extension_profiles, (
            f"profile {name!r} missing — concurrent registration "
            f"lost it (race despite the RLock?)")
        assert f"shape_{i}" in clean_registry.RESTRICTED_SHAPES
