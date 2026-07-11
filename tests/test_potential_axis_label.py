"""Optional "Potential vs <reference electrode> [V]" left-axis label.

Operator: "In PULSAR experiment plot and POLARIS, give the option to use
Potential vs [Reference Electrode] [V] when only potential is on the axis
besides Voltage [V]."

V_mon is a driving VOLTAGE (active vs return); E_act / E_ret are POTENTIALS
measured against the reference electrode.  So when the left axis carries ONLY
electrode potentials (no V_mon) and the operator enables the option, the axis
is labelled ``Potential vs <ref> [V]`` (live plot / POLARIS overlay) or
``… (V)`` (the export, which uses parentheses).  Default OFF keeps the historic
``Voltage`` label; a V_mon on the axis always forces ``Voltage`` (it's a real
voltage).  The decision lives in one shared helper
(:func:`stimtest.plotting._voltage_axis_label`) used by all three plots.
"""
from __future__ import annotations

import matplotlib
matplotlib.use("Agg")
import numpy as np
import pytest

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.plotting import (
    _voltage_axis_label, _axis_unit_label, plot_capture, plot_overlay,
)
from stimtest.session import Capture, ChannelRun, Session, TestParameters
from stimtest.waveforms import PulsePattern


# ---------------------------------------------------------------------------
# 1. The shared decision helper (single source of truth)
# ---------------------------------------------------------------------------
def test_helper_potential_only_with_option():
    assert (_voltage_axis_label({"E_ret"}, potential_axis=True,
                                reference_label="Ag|AgCl")
            == "Potential vs Ag|AgCl [V]")
    assert (_voltage_axis_label({"E_act", "E_ret"}, potential_axis=True,
                                reference_label="Ag|AgCl")
            == "Potential vs Ag|AgCl [V]")


def test_helper_option_off_is_voltage():
    assert (_voltage_axis_label({"E_ret"}, potential_axis=False,
                                reference_label="Ag|AgCl") == "Voltage [V]")


def test_helper_vmon_present_forces_voltage():
    # A genuine voltage on the axis → "Voltage" even with the option on.
    assert (_voltage_axis_label({"V_mon", "E_ret"}, potential_axis=True,
                                reference_label="Ag|AgCl") == "Voltage [V]")
    assert (_voltage_axis_label({"V_mon"}, potential_axis=True,
                                reference_label="Ag|AgCl") == "Voltage [V]")


def test_helper_custom_reference_name():
    assert (_voltage_axis_label({"E_act"}, potential_axis=True,
                                reference_label="Pt-black")
            == "Potential vs Pt-black [V]")


def test_helper_blank_reference_falls_back_to_agcl():
    assert (_voltage_axis_label({"E_ret"}, potential_axis=True,
                                reference_label="")
            == "Potential vs Ag|AgCl [V]")
    assert (_voltage_axis_label({"E_ret"}, potential_axis=True,
                                reference_label=None)
            == "Potential vs Ag|AgCl [V]")


def test_helper_brackets_vs_parens():
    assert (_voltage_axis_label({"E_ret"}, potential_axis=True,
                                reference_label="Ag|AgCl", brackets=False)
            == "Potential vs Ag|AgCl (V)")


def test_axis_unit_label_threads_option():
    assert (_axis_unit_label({"E_ret"}, potential_axis=True,
                             reference_label="Ag|AgCl")
            == "Potential vs Ag|AgCl [V]")
    # Mixed voltage+current stays "Unit"; current-only unaffected; empty None.
    assert _axis_unit_label({"E_ret", "I_mon"}, potential_axis=True) == "Unit"
    assert _axis_unit_label({"I_mon"}, potential_axis=True) == "Current [µA]"
    assert _axis_unit_label(set(), potential_axis=True) is None


# ---------------------------------------------------------------------------
# 2. Export figure (plot_capture) — uses parentheses "(V)"
# ---------------------------------------------------------------------------
def _cap(v_mon=None, e_act=None, e_ret=None, n=400):
    t = np.linspace(-50.0, 250.0, n)
    i = 50.0 * np.sin(np.linspace(0, 6, n))
    return Capture(index=0,
                   pattern=PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1),
                   time_us=t, v_mon_v=v_mon, i_mon_ua=i,
                   e_act_v=e_act, e_ret_v=e_ret)


def _session(reference="Ag|AgCl", counter="Pt"):
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1)
    test = TestParameters(experiment="VT", pattern=pat,
                          configuration=Configuration.monopolar(1),
                          array=ElectrodeArray.utah_4x4(),
                          reference_electrode_label=reference,
                          counter_electrode_label=counter)
    run = ChannelRun(configuration=Configuration.monopolar(1))
    sess = Session(notebook="n", subject="s", test=test)
    return run, sess


def _left_ylabel(fig):
    # ax[0] is the voltage (left) axis in plot_capture.
    return fig.axes[0].get_ylabel()


def test_export_potential_only_with_option():
    n = 300
    e_ret = 0.02 * np.sin(np.linspace(0, 6, n))
    e_act = 0.05 * np.sin(np.linspace(0, 6, n))
    run, sess = _session()
    fig = plot_capture(_cap(v_mon=None, e_act=e_act, e_ret=e_ret, n=n),
                       run, sess, show_cursors=False, potential_axis=True)
    assert _left_ylabel(fig) == "Potential vs Ag|AgCl (V)"


def test_export_potential_only_option_off():
    n = 300
    e_ret = 0.02 * np.sin(np.linspace(0, 6, n))
    run, sess = _session()
    fig = plot_capture(_cap(v_mon=None, e_ret=e_ret, n=n),
                       run, sess, show_cursors=False, potential_axis=False)
    assert _left_ylabel(fig) == "Voltage (V)"


def test_export_vmon_present_stays_voltage():
    n = 300
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    e_ret = 0.02 * np.sin(np.linspace(0, 6, n))
    run, sess = _session()
    fig = plot_capture(_cap(v_mon=v, e_ret=e_ret, n=n),
                       run, sess, show_cursors=False, potential_axis=True)
    assert _left_ylabel(fig) == "Voltage (V)"


def test_export_custom_reference_from_session():
    n = 300
    e_ret = 0.02 * np.sin(np.linspace(0, 6, n))
    run, sess = _session(reference="Pt-black")
    fig = plot_capture(_cap(v_mon=None, e_ret=e_ret, n=n),
                       run, sess, show_cursors=False, potential_axis=True)
    assert _left_ylabel(fig) == "Potential vs Pt-black (V)"


# ---------------------------------------------------------------------------
# 3. POLARIS overlay (plot_overlay) — uses brackets "[V]"
# ---------------------------------------------------------------------------
def test_overlay_potential_only_with_option():
    n = 300
    cap = _cap(v_mon=None, e_ret=0.02 * np.sin(np.linspace(0, 6, n)), n=n)
    fig = plot_overlay({"CH01": cap},
                       axis_map={"E_ret": "left", "I_mon": "right"},
                       potential_axis=True, reference_label="Ag|AgCl")
    assert fig.axes[0].get_ylabel() == "Potential vs Ag|AgCl [V]"


def test_overlay_vmon_present_stays_voltage():
    n = 300
    cap = _cap(v_mon=0.1 * np.sin(np.linspace(0, 6, n)),
               e_ret=0.02 * np.sin(np.linspace(0, 6, n)), n=n)
    fig = plot_overlay({"CH01": cap},
                       axis_map={"V_mon": "left", "E_ret": "left",
                                 "I_mon": "right"},
                       potential_axis=True, reference_label="Ag|AgCl")
    assert fig.axes[0].get_ylabel() == "Voltage [V]"


def test_overlay_custom_reference():
    n = 300
    cap = _cap(v_mon=None, e_ret=0.02 * np.sin(np.linspace(0, 6, n)), n=n)
    fig = plot_overlay({"CH01": cap},
                       axis_map={"E_ret": "left", "I_mon": "right"},
                       potential_axis=True, reference_label="Pt")
    assert fig.axes[0].get_ylabel() == "Potential vs Pt [V]"


# ---------------------------------------------------------------------------
# 4. Live experiment plot (MultiChannelScope) — state + prefs + rendered title
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def _app():
    from PyQt6 import QtWidgets
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _scope(_app):
    from stimtest.gui.multichannel_scope import MultiChannelScope
    return MultiChannelScope()


def test_scope_reference_label_default(_app):
    # No checkboxes any more — the relabel is AUTOMATIC; only the electrode
    # NAME is state (defaults to Ag|AgCl).
    sc = _scope(_app)
    assert sc.reference_label() == "Ag|AgCl"


def test_scope_set_reference_label(_app):
    sc = _scope(_app)
    sc.set_reference_label("Pt-black")
    assert sc.reference_label() == "Pt-black"
    sc.set_reference_label(None)                 # blank → Ag|AgCl fallback
    assert sc.reference_label() == "Ag|AgCl"


def _cap_for_scope(v_mon=None, e_ret=None, n=200):
    t = np.linspace(-50.0, 250.0, n)
    i = 50.0 * np.sin(np.linspace(0, 6, n))
    return Capture(index=0,
                   pattern=PulsePattern.biphasic(amplitude_ua=-50.0, polarity=-1),
                   time_us=t, v_mon_v=v_mon, i_mon_ua=i, e_ret_v=e_ret)


def test_scope_rendered_title_potential_only(_app):
    """A potential-only capture (E_ret, no V_mon) AUTOMATICALLY renders the
    left title as 'Potential vs <ref> [V]' — no toggle needed."""
    n = 200
    e_ret = 0.02 * np.sin(np.linspace(0, 6, n))
    sc = _scope(_app)
    sc.set_reference_label("Ag|AgCl")
    sc.add_capture(_cap_for_scope(v_mon=None, e_ret=e_ret, n=n), "CH01")
    page = sc.ensure_page("CH01")
    assert page.scope._left_title.text() == "Potential vs Ag|AgCl [V]"


def test_scope_rendered_title_vmon_mixed_stays_voltage(_app):
    # V_mon + E_ret share the left axis (MIXED) → plain "Voltage [V]".
    n = 200
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    e_ret = 0.02 * np.sin(np.linspace(0, 6, n))
    sc = _scope(_app)
    sc.add_capture(_cap_for_scope(v_mon=v, e_ret=e_ret, n=n), "CH02")
    page = sc.ensure_page("CH02")
    assert page.scope._left_title.text() == "Voltage [V]"


# ---------------------------------------------------------------------------
# 5. "Voltage vs <return> [V]" — the V_mon-only mirror of the above
# ---------------------------------------------------------------------------
def test_return_helper_vmon_only_with_option():
    assert (_voltage_axis_label({"V_mon"}, return_axis=True,
                                return_label="Pt") == "Voltage vs Pt [V]")


def test_return_helper_option_off_is_voltage():
    assert (_voltage_axis_label({"V_mon"}, return_axis=False,
                                return_label="Pt") == "Voltage [V]")


def test_return_helper_potential_present_forces_plain_voltage():
    # A potential shares the axis → neither relabel applies (mixed content).
    assert (_voltage_axis_label({"V_mon", "E_ret"}, return_axis=True,
                                return_label="Pt") == "Voltage [V]")


def test_return_helper_custom_and_fallback():
    assert (_voltage_axis_label({"V_mon"}, return_axis=True,
                                return_label="PtIr") == "Voltage vs PtIr [V]")
    assert (_voltage_axis_label({"V_mon"}, return_axis=True,
                                return_label="") == "Voltage vs Pt [V]")
    assert (_voltage_axis_label({"V_mon"}, return_axis=True,
                                return_label=None, brackets=False)
            == "Voltage vs Pt (V)")


def test_both_options_pick_the_right_one_per_axis_content():
    # V_mon-only → return label; potential-only → reference label; both on.
    assert (_voltage_axis_label({"V_mon"}, potential_axis=True,
                                reference_label="Ag|AgCl", return_axis=True,
                                return_label="Pt") == "Voltage vs Pt [V]")
    assert (_voltage_axis_label({"E_ret"}, potential_axis=True,
                                reference_label="Ag|AgCl", return_axis=True,
                                return_label="Pt")
            == "Potential vs Ag|AgCl [V]")


def test_axis_unit_label_threads_return_option():
    assert (_axis_unit_label({"V_mon"}, return_axis=True, return_label="Pt")
            == "Voltage vs Pt [V]")


def test_export_vmon_only_with_return_option():
    n = 300
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    run, sess = _session(counter="Pt")
    fig = plot_capture(_cap(v_mon=v, n=n), run, sess,
                       show_cursors=False, return_axis=True)
    assert _left_ylabel(fig) == "Voltage vs Pt (V)"


def test_export_vmon_only_return_option_off():
    n = 300
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    run, sess = _session()
    fig = plot_capture(_cap(v_mon=v, n=n), run, sess,
                       show_cursors=False, return_axis=False)
    assert _left_ylabel(fig) == "Voltage (V)"


def test_export_return_option_custom_counter_from_session():
    n = 300
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    run, sess = _session(counter="PtIr")
    fig = plot_capture(_cap(v_mon=v, n=n), run, sess,
                       show_cursors=False, return_axis=True)
    assert _left_ylabel(fig) == "Voltage vs PtIr (V)"


def test_export_potential_present_ignores_return_option():
    n = 300
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    e_ret = 0.02 * np.sin(np.linspace(0, 6, n))
    run, sess = _session()
    fig = plot_capture(_cap(v_mon=v, e_ret=e_ret, n=n), run, sess,
                       show_cursors=False, return_axis=True)
    assert _left_ylabel(fig) == "Voltage (V)"


def test_overlay_vmon_only_with_return_option():
    n = 300
    cap = _cap(v_mon=0.1 * np.sin(np.linspace(0, 6, n)), n=n)
    fig = plot_overlay({"CH01": cap},
                       axis_map={"V_mon": "left", "I_mon": "right"},
                       return_axis=True, return_label="Pt")
    assert fig.axes[0].get_ylabel() == "Voltage vs Pt [V]"


def test_scope_return_label_default(_app):
    sc = _scope(_app)
    assert sc.return_label() == "Pt"


def test_scope_set_return_label(_app):
    sc = _scope(_app)
    sc.set_return_label("PtIr")
    assert sc.return_label() == "PtIr"
    sc.set_return_label(None)                    # blank → Pt fallback
    assert sc.return_label() == "Pt"


def test_scope_rendered_title_vmon_only_is_voltage_vs_return(_app):
    # V_mon-only left axis AUTOMATICALLY renders "Voltage vs <return> [V]".
    n = 200
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    sc = _scope(_app)
    sc.set_return_label("Pt")
    sc.add_capture(_cap_for_scope(v_mon=v, e_ret=None, n=n), "CH04")
    page = sc.ensure_page("CH04")
    assert page.scope._left_title.text() == "Voltage vs Pt [V]"


# ---------------------------------------------------------------------------
# 6. Inset — the live-plot inset mirrors the main-axis options
# ---------------------------------------------------------------------------
def test_scope_inset_potential_label(_app):
    from stimtest.gui.multichannel_scope import TRACE_ERET
    n = 200
    e_ret = 0.02 * np.sin(np.linspace(0, 6, n))
    sc = _scope(_app)
    sc.set_reference_label("Ag|AgCl")
    sc.inset_check.setChecked(True)              # enable the inset
    sc.set_inset_traces([TRACE_ERET])            # inset shows E_ret only
    sc.add_capture(_cap_for_scope(v_mon=None, e_ret=e_ret, n=n), "CHi1")
    page = sc.ensure_page("CHi1")
    if page.scope._inset is None:
        pytest.skip("pyqtgraph inset unavailable")
    assert page.scope._inset_left_title.text() == "Potential vs Ag|AgCl [V]"


def test_scope_inset_return_label(_app):
    from stimtest.gui.multichannel_scope import TRACE_VMON
    n = 200
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    sc = _scope(_app)
    sc.set_return_label("Pt")
    sc.inset_check.setChecked(True)
    sc.set_inset_traces([TRACE_VMON])            # inset shows V_mon only
    sc.add_capture(_cap_for_scope(v_mon=v, e_ret=None, n=n), "CHi2")
    page = sc.ensure_page("CHi2")
    if page.scope._inset is None:
        pytest.skip("pyqtgraph inset unavailable")
    assert page.scope._inset_left_title.text() == "Voltage vs Pt [V]"


def _bottom_axis(fig):
    # The overlay inset (ax_in) is the bottom-most subplot.
    return min(fig.axes, key=lambda a: a.get_position().y0)


def test_overlay_inset_potential_label():
    n = 300
    cap = _cap(v_mon=None, e_ret=0.02 * np.sin(np.linspace(0, 6, n)), n=n)
    fig = plot_overlay({"CH01": cap},
                       axis_map={"E_ret": "left", "I_mon": "right"},
                       inset_enabled=True, inset_traces={"E_ret"},
                       potential_axis=True, reference_label="Ag|AgCl")
    assert _bottom_axis(fig).get_ylabel() == "Potential vs Ag|AgCl [V]"


def test_overlay_inset_return_label():
    n = 300
    cap = _cap(v_mon=0.1 * np.sin(np.linspace(0, 6, n)), n=n)
    fig = plot_overlay({"CH01": cap},
                       axis_map={"V_mon": "left", "I_mon": "right"},
                       inset_enabled=True, inset_traces={"V_mon"},
                       return_axis=True, return_label="Pt")
    assert _bottom_axis(fig).get_ylabel() == "Voltage vs Pt [V]"


def test_overlay_inset_stays_generic_without_option():
    n = 300
    cap = _cap(v_mon=None, e_ret=0.02 * np.sin(np.linspace(0, 6, n)), n=n)
    fig = plot_overlay({"CH01": cap},
                       axis_map={"E_ret": "left", "I_mon": "right"},
                       inset_enabled=True, inset_traces={"E_ret"})
    assert _bottom_axis(fig).get_ylabel() == "Inset"


# ---------------------------------------------------------------------------
# 7. Plexon Test Board — no electrodes → voltage axis stays plain "Voltage"
# (operator: "If the Test Board is connected, the unit for the voltage
# channels can only be Voltage [V]").
# ---------------------------------------------------------------------------
def test_scope_test_board_forces_plain_voltage(_app):
    """With the reference-aware relabel DISABLED (no-electrode device), a
    V_mon-only axis stays 'Voltage [V]' instead of 'Voltage vs <return>'."""
    n = 200
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    sc = _scope(_app)
    sc.set_return_label("Pt")
    sc.set_reference_aware_labels(False)         # Test Board connected
    sc.add_capture(_cap_for_scope(v_mon=v, e_ret=None, n=n), "CHtb")
    page = sc.ensure_page("CHtb")
    assert page.scope._left_title.text() == "Voltage [V]"
    # Re-enabling (a real electrode device) re-renders via _rerender_all_pages
    # and restores the reference-aware label.
    sc.set_reference_aware_labels(True)
    assert page.scope._left_title.text() == "Voltage vs Pt [V]"


def test_export_test_board_forces_plain_voltage():
    """A session whose setup snapshot says has_electrodes=False (Test Board)
    forces the exported/POLARIS capture axis to plain 'Voltage (V)' even with
    the reference-aware options on."""
    n = 300
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    run, sess = _session(counter="Pt")
    sess.test.extras["setup_snapshot"] = {"has_electrodes": False}
    fig = plot_capture(_cap(v_mon=v, n=n), run, sess,
                       show_cursors=False, return_axis=True)
    assert _left_ylabel(fig) == "Voltage (V)"


def test_export_real_device_keeps_reference_aware():
    """Sanity: with has_electrodes True (or no snapshot), the reference-aware
    relabel still fires — the Test-Board gate must not suppress real devices."""
    n = 300
    v = 0.1 * np.sin(np.linspace(0, 6, n))
    run, sess = _session(counter="Pt")
    sess.test.extras["setup_snapshot"] = {"has_electrodes": True}
    fig = plot_capture(_cap(v_mon=v, n=n), run, sess,
                       show_cursors=False, return_axis=True)
    assert _left_ylabel(fig) == "Voltage vs Pt (V)"
