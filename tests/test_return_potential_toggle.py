"""'Remember return-electrode potential' toggle + learned-OCP as a
RECOMMENDATION (operator: "Have a toggle about asking to remember return
electrode's potential" / "Show the recommended potentials based on what
has been learned, but keep the default values I had set originally").

* The toggle gates ``record_capture`` (off → no new samples recorded).
* The learned OCP is NO LONGER auto-applied — the effective potential
  stays at the catalog/user default; the learned mean is surfaced only
  as a "recommended …" annotation, and only while the toggle is on.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


@pytest.fixture
def temp_history(tmp_path, monkeypatch):
    """Isolate the learned-OCP store in a temp file so tests don't touch
    the real ``electrode_potential_history.json``."""
    import stimtest.electrode_potential_history as eph
    p = tmp_path / "hist.json"
    monkeypatch.setattr(eph, "history_path", lambda: p)
    return eph


def _capture_with_return_rest():
    from stimtest.session import Capture
    from stimtest.waveforms import PulsePattern
    c = Capture(index=0, pattern=PulsePattern.biphasic(amplitude_ua=50.0))
    c.metrics.return_pre_pulse_potential_v = 0.30
    c.metrics.return_post_pulse_potential_v = 0.31
    return c


def _session(remember: bool, coating: str = "SIROF"):
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    test = TestParameters(experiment="SP",
                          pattern=PulsePattern.biphasic(amplitude_ua=50.0),
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4(), duration_s=1.0)
    test.extras = {"setup_snapshot": {
        "return_coating_short": coating, "reference_enable": False,
        "remember_return_potential": remember}}
    return Session(notebook="t", subject="s", test=test)


def test_record_capture_gated_by_toggle(temp_history):
    eph = temp_history
    cap = _capture_with_return_rest()
    eph.record_capture(cap, _session(remember=False))
    assert eph.sample_count("SIROF") == 0, "toggle OFF must skip recording"
    eph.record_capture(cap, _session(remember=True))
    assert eph.sample_count("SIROF") > 0, "toggle ON must record"


def test_record_capture_defaults_on_for_legacy_snapshot(temp_history):
    # A snapshot predating the toggle (no key) keeps recording.
    eph = temp_history
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    test = TestParameters(experiment="SP",
                          pattern=PulsePattern.biphasic(amplitude_ua=50.0),
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4(), duration_s=1.0)
    test.extras = {"setup_snapshot": {"return_coating_short": "SIROF",
                                      "reference_enable": False}}
    eph.record_capture(_capture_with_return_rest(),
                       Session(notebook="t", subject="s", test=test))
    assert eph.sample_count("SIROF") > 0


def test_effective_potential_keeps_default_not_learned(qapp, temp_history):
    eph = temp_history
    for _ in range(12):
        eph.record_sample("Pt", 0.42)           # learned mean ≈ 0.42
    from stimtest.gui.main_window import MainWindow
    from stimtest.gui.setup_tab import COATING_OCP_VS_AG_AG_CL_V
    st = MainWindow(simulate_default=True).setup_tab
    st.reference_enable.setChecked(False)
    st.return_enable.setChecked(True)
    st.return_coating.setCurrentIndex(st.return_coating.findData("Pt"))
    st.remember_potential_chk.setChecked(True)
    eff = st._effective_ref_potential_v()
    cat = COATING_OCP_VS_AG_AG_CL_V["Pt"]
    assert abs(eff - cat) < 1e-6, \
        "effective potential must stay at the catalog default, not learned"
    assert abs(eff - 0.42) > 0.05, "must NOT be the learned value"


def test_recommendation_shown_only_when_toggle_on(qapp, temp_history):
    eph = temp_history
    for _ in range(12):
        eph.record_sample("Pt", 0.42)
    from stimtest.gui.main_window import MainWindow
    st = MainWindow(simulate_default=True).setup_tab
    st.reference_enable.setChecked(False)
    st.return_enable.setChecked(True)
    st.return_coating.setCurrentIndex(st.return_coating.findData("Pt"))

    st.remember_potential_chk.setChecked(True)
    st._refresh_return_potential_label()
    # The learned-OCP tag is now "tested +X V" (operator renamed it from
    # "recommended" and dropped the "(learned, N samples)" parenthetical).
    assert "tested" in st.return_potential_label.text()
    assert "samples" not in st.return_potential_label.text()

    st.remember_potential_chk.setChecked(False)
    st._refresh_return_potential_label()
    assert "tested" not in st.return_potential_label.text()


def test_toggle_round_trips(qapp):
    from stimtest.gui.main_window import MainWindow
    st = MainWindow(simulate_default=True).setup_tab
    st.remember_potential_chk.setChecked(False)
    assert st.setup_snapshot()["remember_return_potential"] is False
    prefs = st.current_prefs()
    assert prefs["remember_return_potential"] is False
    st2 = MainWindow(simulate_default=True).setup_tab
    st2.restore_prefs(prefs)
    assert st2.remember_potential_chk.isChecked() is False
