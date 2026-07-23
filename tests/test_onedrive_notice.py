"""OneDrive save location is a non-blocking heads-up, never a block
(operator: "do not tell the user not to save to OneDrive, just warn
them").  Tests the pure ``_is_onedrive_path`` detector against the
OneDrive env-var roots and the path-component fallback.
"""
import os
from pathlib import Path

from stimtest.gui.main_window import MainWindow


def test_detects_path_under_onedrive_env_root(monkeypatch, tmp_path):
    root = tmp_path / "OneDrive - University"
    (root / "data").mkdir(parents=True)
    monkeypatch.setenv("OneDrive", str(root))
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    assert MainWindow._is_onedrive_path(root / "data") is True


def test_component_fallback_recognizes_onedrive(monkeypatch, tmp_path):
    # No env var set → the literal "OneDrive" path component still counts.
    monkeypatch.delenv("OneDrive", raising=False)
    monkeypatch.delenv("OneDriveCommercial", raising=False)
    monkeypatch.delenv("OneDriveConsumer", raising=False)
    p = tmp_path / "OneDrive" / "PULSAR_data"
    p.mkdir(parents=True)
    assert MainWindow._is_onedrive_path(p) is True


def test_local_folder_is_not_onedrive(monkeypatch, tmp_path):
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    local = tmp_path / "PULSAR_data"
    local.mkdir()
    assert MainWindow._is_onedrive_path(local) is False


def test_detector_never_raises_on_bad_input():
    # A path that can't be resolved must not raise.
    assert MainWindow._is_onedrive_path(Path("Z:\\nonexistent\\x")) in (True, False)
