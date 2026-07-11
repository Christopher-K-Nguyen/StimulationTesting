"""Progressive Stress stopping conditions are explicit CHOICES.

Operator: "Let the stopping conditions be choices: maximum current and
voltage compliance … and manual stop."

PS now presents three stop conditions in a "Stop the ramp when…" group:
  * Maximum current  — toggle; when OFF the ramp runs to the PlexStim
    hardware ceiling instead of the user's Maximum-current value.
  * Voltage compliance (±12 V rail) — toggle (unchanged).
  * Manual stop — always available (checked + disabled).
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


def _ps_tab(qapp):
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    return w, w._exp_tab_by_code["PS"][0]


def test_camera_group_hidden_without_camera(qapp):
    """Operator: "if no camera is connected, hide the camera parameters in
    test parameters tab" — the group is hidden at construction (no camera)
    and flips visible/hidden with the camera service's connect/disconnect."""
    w, ps = _ps_tab(qapp)
    grp = ps._camera_capture_group
    assert grp.isHidden(), "camera group should start hidden (no camera)"
    ps._on_camera_service_connected("dummy cam")
    assert not grp.isHidden()
    ps._on_camera_service_disconnected()
    assert grp.isHidden()
    w.close()


def test_three_stop_conditions_present(qapp):
    _, ps = _ps_tab(qapp)
    assert hasattr(ps, "stop_on_max_current")
    assert hasattr(ps, "stop_on_compliance")
    assert hasattr(ps, "stop_manual")
    # Manual stop is always on and cannot be turned off.
    assert ps.stop_manual.isChecked() is True
    assert ps.stop_manual.isEnabled() is False


def test_max_current_choice_controls_effective_ceiling(qapp):
    from stimtest.config import STIM_MAX_AMPLITUDE_UA
    _, ps = _ps_tab(qapp)
    ps.max_ua.setValue(250.0)
    ps.stop_on_max_current.setChecked(True)
    assert ps._effective_max_ua() == pytest.approx(250.0)
    assert ps.max_ua.isEnabled() is True            # spinbox active
    # Turning the max-current stop OFF falls back to the hardware ceiling
    # and greys the spinbox.
    ps.stop_on_max_current.setChecked(False)
    assert ps._effective_max_ua() == pytest.approx(float(STIM_MAX_AMPLITUDE_UA))
    assert ps.max_ua.isEnabled() is False


def test_policy_reflects_choices(qapp):
    """The StressPolicy handed to the runner carries the effective ceiling
    and the compliance choice."""
    from stimtest.config import STIM_MAX_AMPLITUDE_UA
    from stimtest.hardware.simulator import (SimulatedOscilloscope,
                                             SimulatedStimulator)
    w, ps = _ps_tab(qapp)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.bind_stimulator(stim); scope.open()
    w._on_connected(stim, scope)

    captured = {}

    def fake_start_runner(runner, save_name):
        captured["policy"] = runner.policy

    ps._start_runner = fake_start_runner
    ps._pre_run_warning_check = lambda *a, **k: True
    # Select a channel so a config exists.
    ps.channel_grid.set_actives([1])
    ps.max_ua.setValue(400.0)
    ps.stop_on_max_current.setChecked(True)
    ps.stop_on_compliance.setChecked(False)
    ps.start_clicked()
    pol = captured.get("policy")
    assert pol is not None
    assert pol.max_ua == pytest.approx(400.0)
    assert pol.stop_on_voltage_compliance is False

    # With the max-current stop OFF, the policy ceiling is the hardware limit.
    captured.clear()
    ps.stop_on_max_current.setChecked(False)
    ps.stop_on_compliance.setChecked(True)
    ps.start_clicked()
    pol = captured.get("policy")
    assert pol.max_ua == pytest.approx(float(STIM_MAX_AMPLITUDE_UA))
    assert pol.stop_on_voltage_compliance is True


def test_stop_on_max_current_pref_round_trips(qapp):
    _, ps = _ps_tab(qapp)
    ps.stop_on_max_current.setChecked(False)
    prefs = ps.current_prefs()
    assert prefs.get("stop_on_max_current") is False
    ps.stop_on_max_current.setChecked(True)
    ps.restore_prefs(prefs)
    assert ps.stop_on_max_current.isChecked() is False
