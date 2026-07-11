"""Save/export options are disabled when the save directory isn't viable.

Operator: "There is no viable directory path for saving, then disable saving
options" — the sin_cont run saved into an accidental non-existent nested
folder (``…\\GitHub\\test\\StimulationTesting\\…``).  Because the runner
``mkdir``s missing folders, a typo'd path silently creates a wrong location;
so viability requires the folder to ALREADY EXIST (+ be writable), which
forces the operator to notice the destination before a run arms its saves.
"""
from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("PULSAR_SKIP_FIRST_LAUNCH_SETUP", "1")


@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    app.setApplicationName("pulsar-pytest")
    return app


def _setup(_app):
    from stimtest.gui.setup_tab import SetupTab
    return SetupTab()


def test_existing_dir_is_viable_and_enables_saves(_app, tmp_path):
    st = _setup(_app)
    st.save_path.setText(str(tmp_path))
    st._refresh_save_options()
    assert st.save_dir_viable() is True
    assert st.auto_save_plots.isEnabled() is True
    assert st.auto_export_xlsx.isEnabled() is True


def test_nonexistent_dir_disables_saves(_app, tmp_path):
    """A non-existent path — even one whose PARENT exists (the accidental
    case) — is NOT viable, so the save options are disabled."""
    st = _setup(_app)
    st.save_path.setText(str(tmp_path / "does_not_exist_yet"))
    st._refresh_save_options()
    assert st.save_dir_viable() is False
    assert st.auto_save_plots.isEnabled() is False
    assert st.auto_export_xlsx.isEnabled() is False
    assert st.auto_save_plots_dpi.isEnabled() is False


def test_empty_path_is_not_viable(_app):
    st = _setup(_app)
    st.save_path.setText("")
    st._refresh_save_options()
    assert st.save_dir_viable() is False
    assert st.auto_save_plots.isEnabled() is False


def test_file_path_is_not_viable(_app, tmp_path):
    f = tmp_path / "afile.txt"
    f.write_text("x")
    st = _setup(_app)
    st.save_path.setText(str(f))
    st._refresh_save_options()
    assert st.save_dir_viable() is False


def test_re_enabling_after_fixing_the_path(_app, tmp_path):
    """Switching from a bad path back to a good one re-enables the options."""
    st = _setup(_app)
    st.save_path.setText(str(tmp_path / "nope"))
    st._refresh_save_options()
    assert st.auto_export_xlsx.isEnabled() is False
    st.save_path.setText(str(tmp_path))
    st._refresh_save_options()
    assert st.auto_export_xlsx.isEnabled() is True
