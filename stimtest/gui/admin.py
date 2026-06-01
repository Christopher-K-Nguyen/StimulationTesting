"""Plugin-host admin module: login, profiles, custom-catalog management.

The admin catalog lets a privileged user persistently add or remove
entries from the Setup-tab combo-boxes (Coating, Return electrode,
Reference electrode) so they survive across sessions for all users.

Built-in profiles
-----------------
PULSAR ships with two built-in login profiles:

* ``Profile.NONE`` — anonymous (no login).  Default at startup.
* ``Profile.ADMIN`` — full access.  Username left blank (or
  ``"admin"``) + the admin password.  Unlocks the Manage Custom
  Catalog dialog and any extension-registered restricted shapes.

Extension profiles (plugin architecture)
----------------------------------------
Additional profiles can be registered at runtime by extension
packages via :func:`register_extension_profile`.  An extension is
any Python package that calls ``register_extension_profile(...)``
as an import side effect — see ``EXTENSION_PLUGIN_DESIGN.md`` (kept
out of the public repo) for the design contract.

MainWindow auto-discovers installed extensions by importing every
top-level ``stimtest_*`` package at startup.  Extensions add their
profile name to :data:`RESTRICTED_SHAPES` (which starts empty) and
to the internal ``_extension_profiles`` registry.  With no
extensions installed, PULSAR runs as a single-tier Admin-only
system with no restricted shapes — the plugin API is a complete
no-op.

Passwords
---------
The admin password is stored as a SHA-256 hex digest in
``prefs["admin"]["password_hash"]``.  Factory default ``"Neuron01"``;
an admin can change it from the Manage Custom Catalog dialog once
logged in.  Extension profile passwords are stored on a per-extension
basis (extension chooses where) — main PULSAR does not persist them.

Login dialog UX
---------------
The dialog adapts to the extension state:

* No extensions registered → Username field hidden, Password-only
  (looks like the classic admin login).
* Any extension registered → Username field shown so the operator
  can choose between Admin and the registered extension profile.

Restricted shapes
-----------------
The pulse shapes listed in :data:`RESTRICTED_SHAPES` are hidden
from the PatternPanel shape dropdowns unless the current profile
unlocks them (see :func:`is_restricted_unlocked`).  The set is
mutable and starts empty; extensions union shape IDs into it when
they register.

Persistence
-----------
Admin-catalog entries are kept in prefs under ``admin.catalog`` and
are never stripped on close (unlike session-level custom entries
which are removed in ``MainWindow.closeEvent``).  They are loaded
into the Setup tab at startup via :func:`apply_admin_catalog`.
"""
from __future__ import annotations

import enum
import hashlib
import keyword
import re
import threading
from typing import Dict, List, Optional, Set, Tuple

from PyQt6 import QtCore, QtWidgets

# Module-level lock guarding writes to the plugin registries
# (``_extension_profiles``, ``_extension_display_names``,
# ``RESTRICTED_SHAPES``).  MainWindow.__init__ runs on the GUI thread
# so the canonical extension-load path doesn't race, but an
# extension's ``__init__`` could legitimately spawn a worker thread
# that later calls ``register_extension_profile``.  This lock makes
# the two-dict-plus-set write block atomic, closing audit #15.
_registry_lock = threading.RLock()

# Allowed-character regex for extension profile names.  Names must
# start with a lowercase ASCII letter and contain only lowercase
# letters, digits, and underscores — keeps them well-behaved as
# Python identifiers / dict keys / future ``setattr`` targets.
# Audit #9: prevents reserved keywords like "class" from being
# accepted as profile names (the dict-key path doesn't care, but
# any future code that does ``setattr`` based on name would silently
# break).
_VALID_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# ---------------------------------------------------------------------------
# Built-in admin password
# ---------------------------------------------------------------------------
_DEFAULT_PASSWORD = "Neuron01"
_DEFAULT_HASH = hashlib.sha256(_DEFAULT_PASSWORD.encode()).hexdigest()

CATALOG_KEYS = ("coating", "return", "reference")
CATALOG_LABELS = ("Coating", "Return electrode", "Reference electrode")

# ---------------------------------------------------------------------------
# Plugin-host registries
# ---------------------------------------------------------------------------
#: Map of extension profile name → expected password hash.  Populated
#: by extension packages calling :func:`register_extension_profile`
#: as an import side effect.  Empty in a default install.
_extension_profiles: Dict[str, str] = {}

#: Map of extension profile name → optional human-readable label for
#: diagnostic logging / status-bar messages.  Same registration call
#: populates this.
_extension_display_names: Dict[str, str] = {}

#: Dynamic set of restricted shape IDs.  Extensions union their
#: shape IDs into this set when they register.  Empty in a default
#: install → PatternPanel filter is a no-op and all shapes appear in
#: dropdowns.  Type is mutable :class:`set` (not :class:`frozenset`)
#: so the plugin API can grow it at import time.
RESTRICTED_SHAPES: Set[str] = set()


class Profile(str, enum.Enum):
    """Built-in profile values.

    Defined as a str-enum so members compare equal to their string
    values (``Profile.ADMIN == "admin"`` is True) — this lets the
    rest of the codebase pass either an enum member or a raw string
    interchangeably.  Extension profiles are NOT enum members; they
    live only in :data:`_extension_profiles` as strings and the
    helpers below handle both kinds uniformly.
    """
    NONE = "none"
    ADMIN = "admin"


def register_extension_profile(
    *,
    name: str,
    password_hash: str,
    shapes: Optional[Set[str]] = None,
    display_name: Optional[str] = None,
) -> None:
    """Register a collaborator-only login profile and its gated shapes.

    Parameters
    ----------
    name :
        Profile identifier — also the username the operator types at
        the login dialog.  Case-insensitive (normalized to lowercase
        internally).  Must NOT collide with the built-in profile
        names ``"none"`` or ``"admin"`` and must match the regex
        ``^[a-z][a-z0-9_]*$`` (start with a lowercase letter; only
        lowercase letters, digits, and underscores allowed).  This
        keeps names well-behaved as Python identifiers / dict keys
        and matches the importable Python package name convention.
    password_hash :
        SHA-256 hex digest the entered password must match.  The
        extension is responsible for choosing / storing the password;
        main PULSAR does not persist it across sessions.
    shapes :
        Optional collection of shape IDs (matching the ``SHAPE_*``
        constants in :mod:`stimtest.waveforms`) that this profile
        unlocks.  Must be a set / list / tuple / frozenset of
        strings — **NOT a bare string**, which would iterate by
        character (e.g. ``"halfpipe"`` would register the seven
        per-character "shapes" ``{'a','e','f','h','i','l','p'}``).
        A bare-string ``shapes`` raises :class:`ValueError`.
        Unioned into :data:`RESTRICTED_SHAPES`.  Multiple extensions
        can register overlapping shape sets without conflict.
    display_name :
        Optional human-readable label for log lines / status
        messages (e.g., ``"My Lab collaborator"``).  Defaults to the
        uppercase ``name``.

    Idempotency contract
    --------------------
    The ``(name, password_hash)`` pair is hash-idempotent:
    re-registering the SAME ``(name, password_hash)`` does not
    raise.  Re-registering the same ``name`` with a DIFFERENT
    ``password_hash`` raises :class:`ValueError`.

    ``shapes`` and ``display_name`` are **last-write-wins** on
    re-register, not idempotent — the second call's ``shapes`` get
    unioned into ``RESTRICTED_SHAPES`` again (no-op since the set
    already contains them) and the second call's ``display_name``
    REPLACES the first.  This means an extension can change its
    display_name between releases by reimporting; useful for branding
    refreshes, occasionally confusing if you forget.  See audit #14.

    Side effects
    ------------
    * ``name`` is added to the runtime profile registry so logins via
      :func:`prompt_login` can authenticate against ``password_hash``.
    * ``shapes`` are unioned into :data:`RESTRICTED_SHAPES` so
      PatternPanel filters pick them up on the next ``set_profile``
      call.
    * The login dialog grows a Username field on its next display
      (driven by :func:`_any_extension_registered`).

    Thread safety
    -------------
    All registry writes go through ``_registry_lock`` (RLock) so
    extensions whose ``__init__`` spawns a worker thread that
    re-enters this function don't corrupt the registries.  The
    canonical extension-load path (MainWindow.__init__) is single-
    threaded; this lock exists for unusual cases.  See audit #15.

    Package naming convention
    -------------------------
    Distribution names should match the importable Python package
    name (``[a-z][a-z0-9_]*`` form).  PULSAR's ``_load_extensions``
    walks ``importlib.metadata.distributions()`` and converts the
    distribution name to a module name via lowercase + ``-`` → ``_``
    (so ``stimtest-cwru`` imports as ``stimtest_cwru``).  Avoid
    names with adjacent or mid-name hyphens — Python identifiers
    can't contain ``-`` so the conversion is one-way, and a
    distribution like ``stimtest-a-b`` would still import fine but
    only because its on-disk package directory is named
    ``stimtest_a_b``.  See audit #3.

    Raises
    ------
    ValueError
        If ``name`` is empty, fails the ``[a-z][a-z0-9_]*`` regex,
        collides with a built-in profile (``"none"`` / ``"admin"``),
        or duplicates a prior registration with a different
        password hash.
    """
    if not isinstance(name, str):
        raise ValueError(
            f"Extension profile name must be a string, got {type(name).__name__}")
    name_lower = name.strip().lower()
    if not name_lower:
        raise ValueError("Extension profile name must be non-empty")
    if name_lower in (Profile.NONE.value, Profile.ADMIN.value):
        raise ValueError(
            f"Cannot register extension profile {name!r}: collides "
            f"with built-in profile {name_lower!r}")
    if not _VALID_NAME_RE.match(name_lower):
        raise ValueError(
            f"Extension profile name {name!r} fails the "
            f"^[a-z][a-z0-9_]*$ regex.  Must start with a lowercase "
            f"letter and contain only lowercase letters, digits, "
            f"and underscores.")
    # Reject Python reserved words even though they'd match the
    # regex — ``class`` / ``def`` / ``if`` etc. work fine as dict
    # keys but break any future code that uses ``setattr`` to bind
    # dynamic menu actions or attributes named after the profile.
    # Cheap belt-and-braces gate.  Audit #9.
    if keyword.iskeyword(name_lower):
        raise ValueError(
            f"Extension profile name {name!r} is a reserved Python "
            f"keyword.  Pick a different name (e.g. {name_lower}_lab).")
    existing = _extension_profiles.get(name_lower)
    if existing is not None and existing != password_hash:
        raise ValueError(
            f"Extension profile {name!r} already registered with a "
            f"different password_hash; the new registration would "
            f"overwrite it.  Coordinate password changes via the "
            f"extension's own update mechanism rather than re-"
            f"registering.")
    # Reject a raw string for ``shapes`` — Python iterates strings by
    # character, so an extension that wrote ``shapes="halfpipe"``
    # (forgot the set braces) would silently register the seven
    # single-character "shape" IDs ``{'a','e','f','h','i','l','p'}``
    # and unlock nothing the PatternPanel actually filters on.  This
    # check has to come BEFORE the ``if shapes:`` truthy gate because
    # a non-empty string is truthy.  Strict isinstance — subclasses
    # of ``str`` are still iterables-by-character so they're rejected
    # too; if a caller has a real legitimate use for a single-shape
    # registration they can wrap in ``{shape_id}``.
    if isinstance(shapes, str):
        raise ValueError(
            f"shapes must be a set / list / tuple / frozenset of "
            f"shape-ID strings, not a single string {shapes!r}.  "
            f"Strings iterate by character — pass {{{shapes!r}}} to "
            f"register one shape, or wrap multiple IDs in a "
            f"collection.")
    # All registry writes happen under the module-level lock so
    # concurrent registration calls from extension worker threads
    # don't interleave (audit #15).  RLock so a re-entrant call from
    # the same thread (e.g. via an extension's own validation
    # callback) doesn't deadlock.
    with _registry_lock:
        _extension_profiles[name_lower] = password_hash
        if display_name:
            _extension_display_names[name_lower] = display_name
        if shapes:
            RESTRICTED_SHAPES.update(str(s) for s in shapes)


def _any_extension_registered() -> bool:
    """True when at least one extension profile has been registered.

    Drives whether :class:`_LoginDialog` shows the Username field.
    With no extensions, the dialog is password-only (classic admin
    login UX).
    """
    return bool(_extension_profiles)


def _profile_name(profile) -> str:
    """Normalize a profile (enum member, enum-value str, raw str) to
    its lowercase string form for registry lookups.  Returns ``""``
    on any unrecognizable input.

    Explicit ``None`` returns ``""`` rather than ``"none"`` — without
    this guard, ``str(None) == "None"`` lowercased to ``"none"``
    would silently match the anonymous-profile value, which is
    coincidence rather than intent.  Audit #16.
    """
    if profile is None:
        return ""
    try:
        if hasattr(profile, "value"):
            return str(profile.value).strip().lower()
        return str(profile).strip().lower()
    except Exception:
        return ""


def is_restricted_unlocked(profile) -> bool:
    """Return True if ``profile`` may use the restricted shapes.

    ADMIN always qualifies.  Any registered extension profile also
    qualifies (the very purpose of registering one is to unlock its
    shapes).  Anonymous / unknown profiles return False.

    Accepts either a :class:`Profile` enum member or a raw string
    (extensions identify themselves by string name).
    """
    name = _profile_name(profile)
    if name == Profile.ADMIN.value:
        return True
    return name in _extension_profiles


def is_admin(profile) -> bool:
    """Return True only for the built-in ``Profile.ADMIN`` profile.

    Used to gate admin-only features (Manage Custom Catalog, password
    change).  Extension profiles do NOT inherit admin rights.
    """
    return _profile_name(profile) == Profile.ADMIN.value


def _hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Login history helpers (prefs-backed convenience for the login dialog)
# ---------------------------------------------------------------------------
#: Maximum number of usernames retained in the dropdown history.  Old
#: entries fall off the bottom as new ones get prepended.  Ten is
#: enough to cover a handful of regularly-rotated extensions plus
#: the built-in admin without becoming unwieldy.
LOGIN_HISTORY_MAX = 10


def update_login_history(history: List[str], new_username: str) -> List[str]:
    """Return a new history list with ``new_username`` recorded.

    * Lowercased and stripped for storage consistency.
    * Empty username is silently dropped (the anonymous-admin path
      doesn't deserve a history entry — operator left the field
      blank).
    * Already-present entry is **moved to the front** (most-recent-
      first ordering), not duplicated.
    * Cap at :data:`LOGIN_HISTORY_MAX`; entries past the cap fall off.

    Pure function — does not touch prefs or the file system.  Caller
    is responsible for persisting the returned list.

    Parameters
    ----------
    history :
        Current history list (most-recent-first).  Treated as read-
        only; the input list is never mutated.
    new_username :
        Username that just successfully authenticated.  Will be
        normalized to lowercase + stripped before storage.

    Returns
    -------
    list[str]
        New history list with ``new_username`` at position 0,
        deduplicated, capped at ``LOGIN_HISTORY_MAX``.
    """
    name = (new_username or "").strip().lower()
    if not name:
        # Blank username (admin-default path) — don't pollute history.
        return list(history)[:LOGIN_HISTORY_MAX]
    # Build the new list by removing any prior copy then prepending.
    out = [name] + [u for u in history if u.strip().lower() != name]
    return out[:LOGIN_HISTORY_MAX]


# ---------------------------------------------------------------------------
# Login dialog
# ---------------------------------------------------------------------------
class _LoginDialog(QtWidgets.QDialog):
    """Adaptive login dialog.

    Two presentation modes:

    * **Admin-only** (``show_username=False``, the default when no
      extension profile is registered): single Password field.  Looks
      like the pre-plugin admin login.
    * **Profile-aware** (``show_username=True``): Username + Password
      fields.  Operator types either the admin username (blank or
      ``"admin"``) or an extension profile's username.

    The caller (:func:`prompt_login`) decides which mode based on
    :func:`_any_extension_registered`.

    Username history
    ----------------
    When ``show_username`` is True, the Username widget is an
    **editable QComboBox** seeded from the caller's ``history`` list
    (most-recent-first).  Operator can pick from the dropdown OR
    type a brand new name.  History persists in prefs under
    ``admin.login_history`` — see :func:`update_login_history` for
    the pure-function update + cap logic, and ``MainWindow.
    _on_admin_login`` for the read/write integration.
    """
    def __init__(self, parent=None, *, show_username: bool = False,
                 history: Optional[List[str]] = None):
        super().__init__(parent)
        # Title adapts: in admin-only mode (no extensions) the dialog
        # is about Admin specifically; in extension-aware mode it
        # could be either Admin or an extension profile, so the
        # generic "Log in" is more accurate.  Audit #8.
        self.setWindowTitle("Log in" if show_username else "Admin log in")
        self.setMinimumWidth(360)
        v = QtWidgets.QVBoxLayout(self)

        if show_username:
            _intro = QtWidgets.QLabel(
                "Log in to unlock restricted features.\n"
                "Leave Username blank (or type 'admin') to log in "
                "as Admin.")
            _intro.setWordWrap(True)
            v.addWidget(_intro)
        else:
            v.addWidget(QtWidgets.QLabel("Enter the admin password:"))

        form = QtWidgets.QFormLayout()
        # Username widget: editable QComboBox so prior successful
        # logins appear in a dropdown the operator can pick from
        # without retyping.  Always construct it so :meth:`username`
        # can read uniformly; only add it to the form when
        # ``show_username`` is True so it's invisible in Admin-only
        # mode.  An empty current text is the canonical "blank =
        # admin" signal — same as the old QLineEdit behavior.
        self._user = QtWidgets.QComboBox()
        self._user.setEditable(True)
        # Populate from history (already lowercased/deduped by
        # update_login_history — but be defensive about external
        # input here: a stale prefs.json could contain non-string
        # entries, ints, None, etc.).
        seen: set[str] = set()
        for entry in (history or []):
            if not isinstance(entry, str):
                continue
            norm = entry.strip().lower()
            if norm and norm not in seen:
                seen.add(norm)
                self._user.addItem(norm)
        # Start with the field blank regardless of history — the
        # operator should explicitly choose, not get auto-filled
        # with whatever they typed last time (which could be wrong
        # for the current session).
        self._user.setCurrentText("")
        # Placeholder hint inside the inner line edit.  setEditable
        # constructs a child QLineEdit accessible via ``lineEdit()``.
        _le = self._user.lineEdit()
        if _le is not None:
            _le.setPlaceholderText("(blank = admin)")
        if show_username:
            form.addRow("Username:", self._user)
        self._pw = QtWidgets.QLineEdit()
        self._pw.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._pw.setPlaceholderText("Password")
        form.addRow("Password:", self._pw)
        v.addLayout(form)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

        self._pw.returnPressed.connect(self.accept)
        if show_username and _le is not None:
            # Enter on the username's inner line edit focuses
            # password rather than submitting an empty password.
            # QComboBox itself has no ``returnPressed``; the inner
            # ``lineEdit()`` does.
            _le.returnPressed.connect(self._pw.setFocus)

    def username(self) -> str:
        # ``currentText()`` returns whatever's typed OR selected from
        # the dropdown.  Strip so trailing-space typos don't blow
        # up the lookup.
        return self._user.currentText().strip()

    def password(self) -> str:
        return self._pw.text()


def prompt_login(parent, *, admin_hash: str,
                 history: Optional[List[str]] = None) -> Tuple[str, bool]:
    """Show the login dialog and resolve to a profile name.

    Returns ``(profile_name, ok)``:

    * ``profile_name`` — on success: ``"admin"`` or the lowercase
      name of a registered extension profile (e.g., ``"cwru"`` if
      the ``stimtest_cwru`` extension is installed).  On failure /
      cancel: ``"none"``.
    * ``ok`` — True only when credentials matched.  Cancel and wrong-
      password both return False.

    Dialog adapts to the extension state automatically:

    * No extensions registered → password-only dialog matched against
      ``admin_hash``.
    * Any extension registered → username + password dialog.  Blank
      or ``"admin"`` username matches against ``admin_hash`` (Admin
      path); any other username is looked up in the extension
      registry.

    Parameters
    ----------
    parent :
        Qt parent widget.
    admin_hash :
        SHA-256 hex digest of the admin password.
    history :
        Optional list of past successful usernames (most-recent-
        first) — populates the dialog's Username dropdown.  Pass
        ``prefs["admin"].get("login_history", [])`` from the caller.
        After a successful login, the caller should call
        :func:`update_login_history` with the returned profile_name
        and persist the result.

    The caller is responsible for showing an error message when
    ``ok`` is False and the user clicked OK (cancel returns the same
    tuple, so the caller has to track its own "dialog actually
    submitted vs cancelled" state if it wants to distinguish).
    """
    show_username = _any_extension_registered()
    dlg = _LoginDialog(parent, show_username=show_username, history=history)
    if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
        return (Profile.NONE.value, False)
    pw_hash = _hash(dlg.password())
    user = dlg.username().lower() if show_username else ""
    # Admin path: blank or "admin" username.
    if user == "" or user == Profile.ADMIN.value:
        if pw_hash == admin_hash:
            return (Profile.ADMIN.value, True)
        return (Profile.NONE.value, False)
    # Extension path: look up the username in the registry.
    expected = _extension_profiles.get(user)
    if expected is not None and pw_hash == expected:
        return (user, True)
    return (Profile.NONE.value, False)


# ---------------------------------------------------------------------------
# First-launch admin password setup
# ---------------------------------------------------------------------------
class _FirstLaunchSetupDialog(QtWidgets.QDialog):
    """One-time "set up admin password" dialog.

    Shown by MainWindow on the first launch of PULSAR when:

    * The prefs file is missing OR has no ``admin.setup_completed``
      flag, AND
    * The stored ``admin.password_hash`` matches the factory
      :data:`_DEFAULT_HASH`.

    Either set a real password (Save and continue) OR explicitly
    accept the publicly-known default for now (Use default for
    now → confirmation popup → ``setup_completed = True`` so the
    dialog never nags again).  ESC / window-close is treated as the
    skip-with-confirm path; the dialog is non-skippable in the
    sense that you either set a password or affirmatively accept
    the default — there's no "next time" deferral.

    Result accessors:

    * :meth:`new_password` — the typed password, or empty string
      when the operator skipped.
    * :meth:`chose_skip` — True when the operator pressed "Use
      default for now" (or cancelled via ESC and confirmed) and
      thus accepted the default.

    The wrapper :func:`prompt_first_launch_setup` is the
    recommended entry point; it returns ``(new_hash | None, ok)``
    and handles the confirmation popup.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PULSAR — set up admin password")
        # Width matches the regular login dialog (360 px).  The
        # welcome label sets wordWrap=True so the paragraph reflows
        # to fit; the password fields + button row need ~320 px
        # comfortably, so 360 px gives a balanced shape without
        # the operator-reported "too wide" feel.
        self.setMinimumWidth(360)
        # We control the close path ourselves so ESC routes through
        # the same skip-with-confirm flow as the button.  Disable
        # the system close button cooperation.
        self.setWindowFlags(
            self.windowFlags() & ~QtCore.Qt.WindowType.WindowContextHelpButtonHint)

        self._chose_skip = False

        v = QtWidgets.QVBoxLayout(self)
        _welcome = QtWidgets.QLabel(
            "<b>Welcome to PULSAR.</b><br>"
            "Choose an admin password to protect the custom catalog "
            "and (when installed) extension-profile management.  You "
            "can change it later from <i>Admin → Manage Custom "
            "Catalog → Change password…</i>")
        _welcome.setWordWrap(True)
        v.addWidget(_welcome)

        form = QtWidgets.QFormLayout()
        self._pw1 = QtWidgets.QLineEdit()
        self._pw1.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._pw1.setPlaceholderText("New password")
        form.addRow("New password:", self._pw1)
        self._pw2 = QtWidgets.QLineEdit()
        self._pw2.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self._pw2.setPlaceholderText("Confirm new password")
        form.addRow("Confirm password:", self._pw2)
        v.addLayout(form)

        #: Inline status label — turns red when the two fields don't
        #: match or one is empty.  Cleared when state is OK.
        self._status = QtWidgets.QLabel("")
        self._status.setStyleSheet("color: #c0392b;")
        v.addWidget(self._status)

        btns = QtWidgets.QHBoxLayout()
        self._skip_btn = QtWidgets.QPushButton("Use default for now")
        self._skip_btn.setToolTip(
            "Accept the publicly-known factory default password.  "
            "The dialog won't show again, but you should change the "
            "password via Admin → Manage Custom Catalog → Change "
            "password… when you have a moment.")
        self._skip_btn.clicked.connect(self._on_skip)
        self._save_btn = QtWidgets.QPushButton("Save and continue")
        self._save_btn.setDefault(True)
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._on_save)
        btns.addWidget(self._skip_btn)
        btns.addStretch(1)
        btns.addWidget(self._save_btn)
        v.addLayout(btns)

        # Live validation: enable Save only when both fields are
        # non-empty AND match.  Status label gives inline feedback
        # so the operator isn't guessing why the button is greyed.
        self._pw1.textChanged.connect(self._validate)
        self._pw2.textChanged.connect(self._validate)
        # Enter on the second field submits when valid.
        self._pw2.returnPressed.connect(self._maybe_submit_on_enter)

    # ---- state predicates ----
    def new_password(self) -> str:
        """Typed password (empty when the operator skipped)."""
        if self._chose_skip:
            return ""
        return self._pw1.text()

    def chose_skip(self) -> bool:
        return self._chose_skip

    # ---- slots ----
    def _validate(self) -> None:
        a = self._pw1.text()
        b = self._pw2.text()
        if not a or not b:
            self._status.setText("")
            self._save_btn.setEnabled(False)
            return
        if a != b:
            self._status.setText("Passwords do not match.")
            self._save_btn.setEnabled(False)
            return
        self._status.setText("")
        self._save_btn.setEnabled(True)

    def _maybe_submit_on_enter(self) -> None:
        if self._save_btn.isEnabled():
            self._on_save()

    def _on_save(self) -> None:
        # Defensive re-check (button shouldn't be enabled otherwise).
        if not self._pw1.text() or self._pw1.text() != self._pw2.text():
            return
        self._chose_skip = False
        self.accept()

    def _on_skip(self) -> None:
        # Confirmation popup — accepting the public default is a
        # one-way door (setup_completed gets persisted), worth a
        # second click.
        ok = QtWidgets.QMessageBox.question(
            self, "Use default password?",
            "The factory default admin password is publicly known "
            "and shouldn't be relied on for any real security.\n\n"
            "Continue with the default password?  This dialog "
            "won't show again — you can change the password from "
            "Admin → Manage Custom Catalog → Change password… at "
            "any time.",
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if ok != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._chose_skip = True
        self.accept()

    def closeEvent(self, event):
        # Route window-close (X button or ESC) through the same
        # skip-with-confirm flow as the button.  If the operator
        # cancels the confirmation, we keep the dialog open.
        if self._chose_skip:
            # Already going through accept(); let the close finish.
            return super().closeEvent(event)
        ok = QtWidgets.QMessageBox.question(
            self, "Use default password?",
            "Closing this dialog without setting a password will "
            "use the publicly-known factory default.  Continue?",
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if ok != QtWidgets.QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self._chose_skip = True
        super().closeEvent(event)


def prompt_first_launch_setup(parent) -> Tuple[Optional[str], bool]:
    """Show the first-launch setup dialog; return ``(new_hash, ok)``.

    Returns
    -------
    (new_hash, ok) : tuple
        * ``(hash_hex, True)`` — operator set a custom password.
          Caller should persist ``new_hash`` as the admin
          password_hash and mark ``setup_completed = True``.
        * ``(None, False)`` — operator pressed "Use default for
          now" (or cancelled via ESC and confirmed).  Caller
          should mark ``setup_completed = True`` so the dialog
          doesn't re-prompt on the next launch.

    The function NEVER returns "user cancelled without confirming"
    — closeEvent forces the confirmation path, so either the
    operator set a password or affirmatively accepted the default.
    """
    dlg = _FirstLaunchSetupDialog(parent)
    dlg.exec()  # blocks; closeEvent enforces a definitive answer
    if dlg.chose_skip():
        return (None, False)
    pw = dlg.new_password()
    if not pw:
        # Shouldn't happen — Save button is disabled on empty —
        # but defensive return path so the caller can recover.
        return (None, False)
    return (_hash(pw), True)


# ---------------------------------------------------------------------------
# Catalog management dialog
# ---------------------------------------------------------------------------
class AdminCatalogDialog(QtWidgets.QDialog):
    """Let the admin add / remove entries in the Setup-tab combo-boxes.

    Shows one list per category (Coating / Return / Reference).
    Changes take effect immediately on the live Setup tab and are
    persisted to the prefs file on Accept.
    """

    def __init__(self, parent, *, catalog: Dict[str, List[str]],
                 current_hash: str):
        super().__init__(parent)
        self.setWindowTitle("Admin — Manage Custom Catalog")
        self.setMinimumWidth(560)
        self.setMinimumHeight(440)

        # Working copy — we edit this and commit on Accept.
        self._catalog: Dict[str, List[str]] = {
            k: list(catalog.get(k, [])) for k in CATALOG_KEYS
        }
        self._current_hash = current_hash

        v = QtWidgets.QVBoxLayout(self)

        # One group per category
        self._lists: Dict[str, QtWidgets.QListWidget] = {}
        for key, label in zip(CATALOG_KEYS, CATALOG_LABELS):
            grp = QtWidgets.QGroupBox(label)
            gl = QtWidgets.QVBoxLayout(grp)

            lw = QtWidgets.QListWidget()
            lw.addItems(self._catalog[key])
            gl.addWidget(lw)
            self._lists[key] = lw

            row = QtWidgets.QHBoxLayout()
            add_btn = QtWidgets.QPushButton("Add…")
            rem_btn = QtWidgets.QPushButton("Remove")
            row.addWidget(add_btn)
            row.addWidget(rem_btn)
            row.addStretch(1)
            gl.addLayout(row)

            # Capture key in closure
            add_btn.clicked.connect(lambda checked, k=key: self._add(k))
            rem_btn.clicked.connect(lambda checked, k=key: self._remove(k))

            v.addWidget(grp)

        # Change password
        v.addSpacing(4)
        chpw_btn = QtWidgets.QPushButton("Change password…")
        chpw_btn.clicked.connect(self._change_password)
        pw_row = QtWidgets.QHBoxLayout()
        pw_row.addWidget(chpw_btn)
        pw_row.addStretch(1)
        v.addLayout(pw_row)

        # Dialog buttons
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok |
            QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    # ---- result accessors ----
    def catalog(self) -> Dict[str, List[str]]:
        """Return the edited catalog (call after accept())."""
        return {k: list(self._catalog[k]) for k in CATALOG_KEYS}

    def new_password_hash(self) -> str:
        """Return the (possibly changed) password hash."""
        return self._current_hash

    # ---- slots ----
    def _add(self, key: str) -> None:
        name, ok = QtWidgets.QInputDialog.getText(
            self, f"Add {key}", f"New {key} name:")
        name = name.strip()
        if not ok or not name:
            return
        if name in self._catalog[key]:
            QtWidgets.QMessageBox.information(
                self, "Already exists",
                f'"{name}" is already in the {key} catalog.')
            return
        self._catalog[key].append(name)
        self._lists[key].addItem(name)

    def _remove(self, key: str) -> None:
        lw = self._lists[key]
        row = lw.currentRow()
        if row < 0:
            return
        name = lw.item(row).text()
        reply = QtWidgets.QMessageBox.question(
            self, "Remove entry",
            f'Remove "{name}" from the {key} catalog?',
            QtWidgets.QMessageBox.StandardButton.Yes |
            QtWidgets.QMessageBox.StandardButton.No)
        if reply != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        lw.takeItem(row)
        if name in self._catalog[key]:
            self._catalog[key].remove(name)

    def _change_password(self) -> None:
        new_pw, ok = QtWidgets.QInputDialog.getText(
            self, "Change password", "New admin password:",
            QtWidgets.QLineEdit.EchoMode.Password)
        if not ok or not new_pw:
            return
        confirm, ok = QtWidgets.QInputDialog.getText(
            self, "Confirm password", "Re-enter new password:",
            QtWidgets.QLineEdit.EchoMode.Password)
        if not ok:
            return
        if new_pw != confirm:
            QtWidgets.QMessageBox.warning(
                self, "Mismatch", "Passwords do not match. Password not changed.")
            return
        self._current_hash = _hash(new_pw)
        QtWidgets.QMessageBox.information(
            self, "Password changed", "Admin password updated.")


# ---------------------------------------------------------------------------
# Catalog ↔ SetupTab helpers
# ---------------------------------------------------------------------------
def apply_admin_catalog(setup_tab, catalog: Dict[str, List[str]]) -> None:
    """Inject admin catalog entries into the live Setup tab combos.

    Maps catalog keys to the correct combo widget and tracker list.
    Safe to call at startup or after the catalog is edited.
    """
    mapping = {
        "coating":   ("coating_combo",  "_coating_custom_entries"),
        "return":    ("return_coating", "_return_custom_entries"),
        "reference": ("reference_combo","_reference_custom_entries"),
    }
    for key, (combo_attr, tracker_attr) in mapping.items():
        combo: QtWidgets.QComboBox = getattr(setup_tab, combo_attr, None)
        tracker: list = getattr(setup_tab, tracker_attr, None)
        if combo is None or tracker is None:
            continue
        for name in catalog.get(key, []):
            name = str(name).strip()
            if not name:
                continue
            if combo.findData(name) >= 0:
                continue          # already present
            combo.blockSignals(True)
            try:
                combo.addItem(name, userData=name)
            finally:
                combo.blockSignals(False)
            if name not in tracker:
                tracker.append(name)


def remove_from_admin_catalog(setup_tab, catalog: Dict[str, List[str]],
                              key: str, name: str) -> None:
    """Remove *name* from the combo and tracker for *key*.

    The catalog dict is mutated in place so the caller can persist it.
    """
    mapping = {
        "coating":   ("coating_combo",  "_coating_custom_entries"),
        "return":    ("return_coating", "_return_custom_entries"),
        "reference": ("reference_combo","_reference_custom_entries"),
    }
    combo_attr, tracker_attr = mapping.get(key, (None, None))
    if combo_attr is None:
        return
    combo: QtWidgets.QComboBox = getattr(setup_tab, combo_attr, None)
    tracker: list = getattr(setup_tab, tracker_attr, None)
    if combo is not None:
        idx = combo.findData(name)
        if idx >= 0:
            combo.removeItem(idx)
    if tracker is not None and name in tracker:
        tracker.remove(name)
    entries = catalog.get(key, [])
    if name in entries:
        entries.remove(name)
