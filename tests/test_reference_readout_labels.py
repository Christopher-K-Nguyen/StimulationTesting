"""Reference-electrode readout: shorter tag + original-limits display.

Operator:
  * "reduce the elongated text from the 'recommended…'" (panel too wide),
  * "remove that parenthetical phrase of how many samples learned",
  * "Show the original potential limits so that the user can see how it is
    shifted, if it is shifted",
  * 'instead of "recommended", change it to "tested"'.
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


def test_tag_says_tested_and_drops_sample_count(_app):
    st = _setup(_app)
    st._learned_ocp = lambda short: 0.217
    # Stub the SD too (a ± term is a separate feature, tested elsewhere) so
    # this stays hermetic vs the machine's real learned-OCP store.
    st._learned_ocp_std = lambda short: None
    if hasattr(st, "remember_potential_chk"):
        st.remember_potential_chk.setChecked(True)
    tag = st._recommendation_tag("Pt")
    assert "tested" in tag
    assert "+0.217 V" in tag
    assert "recommended" not in tag
    assert "learned" not in tag
    assert "samples" not in tag


def test_reference_label_does_not_word_wrap(_app):
    # Operator: "Do not wrap this" — wrapping broke the readout mid-token
    # ("+0.000 V vs Ag|" / "AgCl").  The shortened tag (task #150) keeps it
    # to one line without forcing the panel wide.
    st = _setup(_app)
    assert st.reference_potential_label.wordWrap() is False


def test_original_limits_blank_without_shift(_app):
    st = _setup(_app)
    # Default: no reference selected → Ag|AgCl baseline → no shift → blank + hidden.
    assert st._current_ref_potential_v == 0.0
    st._refresh_limits_original_label()
    assert st.cathodic_original_label.text() == ""
    assert st.anodic_original_label.text() == ""
    assert st.cathodic_original_label.isHidden()
    assert st.anodic_original_label.isHidden()


def test_original_limits_shown_when_reference_shifts(_app):
    from stimtest.gui.setup_tab import REFERENCE_ELECTRODES_OCP_V
    st = _setup(_app)
    # Select Pt (+0.2 V vs Ag|AgCl) → limits shift DOWN by 0.2; each limit's
    # original (unshifted, vs Ag|AgCl) value shows UNDER that limit (operator:
    # "the value and shifted under each limit").
    if hasattr(st, "reference_enable"):
        st.reference_enable.setChecked(True)
    combo = st.reference_combo
    pt = next(i for i in range(combo.count())
              if REFERENCE_ELECTRODES_OCP_V.get(combo.itemData(i), 0.0) == 0.2)
    combo.setCurrentIndex(pt)
    st._on_reference_changed()
    assert abs(st._current_ref_potential_v - 0.2) < 1e-9
    ct = st.cathodic_original_label.text()
    at = st.anodic_original_label.text()
    # displayed − shifted = original: -0.8 + 0.2 = -0.6, 0.6 + 0.2 = 0.8
    oc = round(st.cathodic_limit_v.value() + 0.2, 3)
    oa = round(st.anodic_limit_v.value() + 0.2, 3)
    # Each limit's own original value under it — cathodic under cathodic, etc.
    # (operator: "Remove 'shifted' underneath the limits".)
    assert f"{oc:+.3f}" in ct and "vs Ag|AgCl" in ct and "shifted" not in ct
    assert f"{oa:+.3f}" in at and "vs Ag|AgCl" in at and "shifted" not in at
    # And they're VISIBLE (palette(mid) is invisible on dark themes, gotcha #87).
    assert not st.cathodic_original_label.isHidden()
    assert "palette(mid)" not in st.cathodic_original_label.styleSheet()
