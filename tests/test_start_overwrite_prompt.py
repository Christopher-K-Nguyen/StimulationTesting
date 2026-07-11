"""Start-time save-location guards: overwrite warning + missing-folder prompt.

Operator:
  * "Put a pop-up warning if files in a folder will be replaced (especially
    if the directory or filename was not changed). … Continue if the user
    wants to replace the files.  Cancel if the user wants to revert back to
    the original inputs so that the user can change stuff."
  * "if the directory does not exist, put a pop up that the directory does
    not exist and ask the user to confirm creating or cancel".

The dialogs are bypassed under ``PULSAR_SKIP_OVERWRITE_PROMPT=1`` (and simply
don't appear when there's nothing to replace / the folder exists), so these
tests exercise the decision helpers without a blocking modal.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")
os.environ.setdefault("PULSAR_ALLOW_MULTIPLE", "1")


@pytest.fixture(scope="module")
def _win():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    from stimtest.gui.main_window import MainWindow
    return MainWindow(simulate_default=True)


def _tab(_win):
    return _win.vt_tab


def test_files_that_would_be_replaced_lists_matching_stem(_win, tmp_path):
    tab = _tab(_win)
    tab._save_dir = tmp_path
    assert tab._files_that_would_be_replaced("sess.npz") == []   # nothing yet
    (tmp_path / "sess.npz").write_bytes(b"x")
    (tmp_path / "sess.xlsx").write_bytes(b"x")
    (tmp_path / "sess_CH01.tif").write_bytes(b"x")
    (tmp_path / "other.npz").write_bytes(b"x")     # different stem → ignored
    got = {p.name for p in tab._files_that_would_be_replaced("sess.npz")}
    assert got == {"sess.npz", "sess.xlsx", "sess_CH01.tif"}


def test_overwrite_confirm_no_dialog_when_nothing_to_replace(_win, tmp_path):
    tab = _tab(_win)
    tab._save_dir = tmp_path                        # exists, empty
    # Returns the (unchanged) name to use — no collision, no dialog.
    assert tab._confirm_overwrite_before_start("fresh.npz") == "fresh.npz"


def test_directory_confirm_true_when_folder_exists(_win, tmp_path):
    tab = _tab(_win)
    tab._save_dir = tmp_path
    assert tab._confirm_directory_before_start() is True


def test_env_bypass_missing_dir_and_existing_files(_win, tmp_path, monkeypatch):
    monkeypatch.setenv("PULSAR_SKIP_OVERWRITE_PROMPT", "1")
    tab = _tab(_win)
    # Missing folder → bypass proceeds.
    tab._save_dir = tmp_path / "nope"
    assert tab._confirm_directory_before_start() is True
    # Existing files → bypass proceeds (returns the unchanged name).
    tab._save_dir = tmp_path
    (tmp_path / "s.npz").write_bytes(b"x")
    assert tab._confirm_overwrite_before_start("s.npz") == "s.npz"


def test_combined_gate_passes_on_clean_location(_win, tmp_path, monkeypatch):
    monkeypatch.setenv("PULSAR_SKIP_OVERWRITE_PROMPT", "1")
    tab = _tab(_win)
    tab._save_dir = tmp_path
    assert tab._confirm_save_location_before_start("x.npz") == "x.npz"


# ------------------------------------------------------------ log file (.txt)
# Operator: "Even when writing to the log file should ask if the user wants to
# replace it if the directory and filename are the same."
def test_preexisting_log_listed_as_victim(_win, tmp_path):
    tab = _tab(_win)
    tab._save_dir = tmp_path
    logp = tmp_path / "sess_log.txt"
    logp.write_text("prior session content\n", encoding="utf-8")
    _win.log_pane.set_log_file(logp)                 # append-open → pre-existed
    assert _win.log_pane.log_file_preexisted() is True
    victims = {p.name for p in tab._files_that_would_be_replaced("sess.npz")}
    assert "sess_log.txt" in victims
    _win.log_pane.set_log_file(None)                 # release the handle


def test_fresh_log_not_listed(_win, tmp_path):
    tab = _tab(_win)
    tab._save_dir = tmp_path
    logp = tmp_path / "brandnew_log.txt"             # does NOT exist yet
    _win.log_pane.set_log_file(logp)
    assert _win.log_pane.log_file_preexisted() is False
    victims = {p.name for p in tab._files_that_would_be_replaced("brandnew.npz")}
    assert "brandnew_log.txt" not in victims
    _win.log_pane.set_log_file(None)


def test_set_log_file_does_not_create_missing_dir(_win, tmp_path):
    """Operator: "I wanted a pop up about a non-existent directory."  The log
    mustn't SILENTLY create a mistyped save folder (that would defeat the
    run-start directory-missing confirmation)."""
    missing = tmp_path / "does_not_exist" / "sess_log.txt"
    _win.log_pane.set_log_file(missing)
    assert not missing.parent.exists(), (
        "set_log_file must NOT create a missing save directory")
    _win.log_pane.log("best-effort line")          # must not crash or mkdir
    assert not missing.parent.exists()
    _win.log_pane.set_log_file(None)


def test_set_log_file_replace_truncates(_win, tmp_path):
    logp = tmp_path / "old_log.txt"
    logp.write_text("old content that should be wiped\n", encoding="utf-8")
    _win.log_pane.set_log_file(logp, replace=True)   # truncate
    # The prior content is gone; the file now carries a fresh wall-clock
    # "PULSAR session log created …" banner instead of being byte-empty.
    body = logp.read_text(encoding="utf-8")
    assert "old content that should be wiped" not in body
    assert "session log created" in body.lower()
    assert _win.log_pane.log_file_preexisted() is False
    _win.log_pane.set_log_file(None)


def test_own_session_log_not_flagged_as_preexisting(_win, tmp_path):
    """Operator: "I was asked to replace a file in the directory … when I have
    NOT run anything in that directory."  The victim was THIS session's own log
    — the LogPane opens ``<stem>_log.txt`` during setup and writes setup lines,
    so a later re-point (a filename-edit re-firing set_log_file) must NOT
    re-flag it as pre-existing just because this session grew it."""
    from stimtest.gui.widgets import LogPane
    lp = LogPane()
    p = tmp_path / "mine_log.txt"
    lp.set_log_file(p)                         # fresh file → not pre-existing
    assert lp.log_file_preexisted() is False
    lp.log("a setup line this session wrote")  # this session grows the file
    lp.set_log_file(p)                         # re-point at OUR OWN file
    assert lp.log_file_preexisted() is False, (
        "this session's own log must not be flagged for overwrite")
    # But a DIFFERENT session opening the now-populated file IS warned.
    lp2 = LogPane()
    lp2.set_log_file(p)
    assert lp2.log_file_preexisted() is True
    lp.set_log_file(None); lp2.set_log_file(None)


def test_preexisting_log_stays_flagged_across_repoints(_win, tmp_path):
    """A PRIOR session's log stays flagged as an overwrite victim even after the
    SAME path is re-pointed several times before Start.

    The real flow fires ``set_log_file`` at the same path multiple times —
    ``MainWindow.__init__``, then the prefs-restore save-path + filename signals
    — before the operator ever presses Start.  The OLD logic recomputed
    ``preexisted`` on every call and flipped it to False once the session
    "owned" the path (2nd call onward), so by run start the log looked
    un-pre-existing and the overwrite prompt SILENTLY APPENDED to a prior
    session's log instead of asking (operator: "the log file is never asked if
    it is replaced or appended").  The sticky per-path flag keeps it True."""
    from stimtest.gui.widgets import LogPane
    lp = LogPane()
    p = tmp_path / "prior_log.txt"
    p.write_text("PRIOR SESSION CONTENT\n", encoding="utf-8")
    lp.set_log_file(p)                      # call #1 (init)
    assert lp.log_file_preexisted() is True
    lp.set_log_file(p)                      # call #2 (prefs-restore save path)
    assert lp.log_file_preexisted() is True, (
        "a real prior log must STAY flagged across re-points (was the bug)")
    lp.set_log_file(p)                      # call #3 (filename edit, same path)
    assert lp.log_file_preexisted() is True
    # Continue → replace_log_file truncates → no longer a victim.
    lp.replace_log_file()
    assert lp.log_file_preexisted() is False
    lp.set_log_file(None)


def test_log_records_creation_and_end_and_continue_banners(_win, tmp_path):
    """Operator: "be sure that the log file has the date and time of creation
    and ended.  When continuing with LP, add another date and time for
    continuing."  Each banner carries a wall-clock ``YYYY-MM-DD HH:MM:SS``."""
    import re
    from stimtest.gui.widgets import LogPane
    lp = LogPane()
    p = tmp_path / "banners_log.txt"
    lp.set_log_file(p)                    # → "created" banner
    lp.mark_session_ended()              # → "Session ended" banner
    lp.mark_session_continued("LP resume")   # → "Continuing" banner
    lp.set_log_file(None)
    body = p.read_text(encoding="utf-8")
    stamp = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}"
    assert re.search(rf"session log created {stamp}", body), body
    assert re.search(rf"Session ended {stamp}", body), body
    assert re.search(rf"Continuing \(LP resume\) {stamp}", body), body


def test_confirm_replaces_preexisting_log(_win, tmp_path, monkeypatch):
    monkeypatch.setenv("PULSAR_SKIP_OVERWRITE_PROMPT", "1")
    tab = _tab(_win)
    tab._save_dir = tmp_path
    logp = tmp_path / "sess_log.txt"
    logp.write_text("PRIOR SESSION\n" * 5, encoding="utf-8")
    _win.log_pane.set_log_file(logp)
    assert _win.log_pane.log_file_preexisted() is True
    # Confirming the save location (bypassed dialog) REPLACES the pre-existing
    # log → prior content wiped (a fresh creation banner replaces it), no
    # longer flagged pre-existing.
    assert tab._confirm_save_location_before_start("sess.npz") == "sess.npz"
    body = logp.read_text(encoding="utf-8")
    assert "PRIOR SESSION" not in body
    assert "session log created" in body.lower()
    assert _win.log_pane.log_file_preexisted() is False
    _win.log_pane.set_log_file(None)


# ------------------------------------------------------ Append to filename
# Operator: "Give the option to append filenames if files of the same name
# already exist in the directory" — a third choice beside Continue/Cancel that
# saves ALONGSIDE the existing files under a unique <stem>_N name.
def test_unique_save_name_appends_next_free_suffix(_win, tmp_path):
    tab = _tab(_win)
    tab._save_dir = tmp_path
    # Nothing there → first append candidate is _2.
    assert tab._unique_save_name("sess.npz") == "sess_2.npz"
    # _2 taken (by ANY artifact of that stem) → skip to _3.
    (tmp_path / "sess.npz").write_bytes(b"x")
    (tmp_path / "sess_2.xlsx").write_bytes(b"x")      # xlsx collision counts
    assert tab._unique_save_name("sess.npz") == "sess_3.npz"
    # A per-channel plot OR a same-named log also blocks a candidate.
    (tmp_path / "sess_3_CH01.tif").write_bytes(b"x")
    (tmp_path / "sess_4_log.txt").write_text("x", encoding="utf-8")
    assert tab._unique_save_name("sess.npz") == "sess_5.npz"


def test_apply_appended_name_repoints_stem_and_log(_win, tmp_path):
    tab = _tab(_win)
    tab._save_dir = tmp_path
    # Point the log at the ORIGINAL stem first (as a run would).
    _win.log_pane.set_log_file(tmp_path / "sess_log.txt")
    tab._apply_appended_save_name("sess_2.npz")
    # Session stem follows the appended name…
    assert tab._session_stem == "sess_2"
    # …and the log is re-pointed to the fresh <new_stem>_log.txt (so the
    # prior session's log is NOT touched), still not flagged pre-existing.
    lp = _win.log_pane
    assert lp.log_file_path() == tmp_path / "sess_2_log.txt"
    assert lp.log_file_preexisted() is False
    lp.set_log_file(None)


def test_combined_gate_returns_appended_name(_win, tmp_path):
    """The full gate returns the RENAMED save name when the (stubbed) dialog
    picks Append, and re-points the session stem to it."""
    tab = _tab(_win)
    tab._save_dir = tmp_path
    (tmp_path / "sess.npz").write_bytes(b"x")         # force a collision
    # Stub the modal: simulate the operator picking "Append to filename" so
    # the overwrite step returns the unique name instead of the original.
    orig = tab._unique_save_name("sess.npz")
    tab._confirm_overwrite_before_start = lambda name: orig   # type: ignore
    resolved = tab._confirm_save_location_before_start("sess.npz")
    assert resolved == orig == "sess_2.npz"
    assert tab._session_stem == "sess_2"              # stem was re-pointed
    _win.log_pane.set_log_file(None)
