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
from typing import Dict, List, Optional, Set, Tuple

from PyQt6 import QtCore, QtWidgets

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
        names ``"none"`` or ``"admin"``.
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

    Idempotency
    -----------
    Re-registering the same ``(name, password_hash)`` pair is a no-op
    (handles accidental double-import of the same extension).
    Re-registering the same ``name`` with a DIFFERENT ``password_hash``
    raises :class:`ValueError`.

    Side effects
    ------------
    * ``name`` is added to the runtime profile registry so logins via
      :func:`prompt_login` can authenticate against ``password_hash``.
    * ``shapes`` are unioned into :data:`RESTRICTED_SHAPES` so
      PatternPanel filters pick them up on the next ``set_profile``
      call.
    * The login dialog grows a Username field on its next display
      (driven by :func:`_any_extension_registered`).

    Raises
    ------
    ValueError
        If ``name`` is empty, collides with a built-in profile
        (``"none"`` / ``"admin"``), or duplicates a prior registration
        with a different password hash.
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
    on any unrecognizable input."""
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
    """
    def __init__(self, parent=None, *, show_username: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Admin log in")
        self.setMinimumWidth(360)
        v = QtWidgets.QVBoxLayout(self)

        if show_username:
            v.addWidget(QtWidgets.QLabel(
                "Log in to unlock restricted features.\n"
                "Leave Username blank (or type 'admin') to log in "
                "as Admin."))
        else:
            v.addWidget(QtWidgets.QLabel("Enter the admin password:"))

        form = QtWidgets.QFormLayout()
        # Always construct the username widget so :meth:`username`
        # can read from it unconditionally — but only add it to the
        # form when ``show_username`` is True (so it's invisible in
        # Admin-only mode).
        self._user = QtWidgets.QLineEdit()
        if show_username:
            self._user.setPlaceholderText("(blank = admin)")
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
        if show_username:
            # Enter on the username field focuses password rather
            # than submitting an empty password.
            self._user.returnPressed.connect(self._pw.setFocus)

    def username(self) -> str:
        return self._user.text().strip()

    def password(self) -> str:
        return self._pw.text()


def prompt_login(parent, *, admin_hash: str) -> Tuple[str, bool]:
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

    The caller is responsible for showing an error message when
    ``ok`` is False and the user clicked OK (cancel returns the same
    tuple, so the caller has to track its own "dialog actually
    submitted vs cancelled" state if it wants to distinguish).
    """
    show_username = _any_extension_registered()
    dlg = _LoginDialog(parent, show_username=show_username)
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
