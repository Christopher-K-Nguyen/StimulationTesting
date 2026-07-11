"""Run-end TEXT notifications (operator: "Allow for a phone number option
for text messages").

A phone + carrier on the Setup tab → at run end the RunnerWorker also sends
an SMS via the email-to-SMS gateway, gated by the same "Notify on finish"
toggle and the SMTP credentials.
"""
from __future__ import annotations

import sys

import pytest


@pytest.fixture(scope="module")
def qapp():
    from PyQt6 import QtWidgets
    return (QtWidgets.QApplication.instance()
            or QtWidgets.QApplication(sys.argv))


# ---- Setup-tab phone/carrier inputs ------------------------------------
def test_setup_phone_and_carrier_present(qapp):
    from stimtest.gui.setup_tab import SetupTab
    st = SetupTab()
    assert hasattr(st, "user_phone")
    assert st.user_carrier.count() > 1            # "(no text)" + carriers
    st.user_phone.setText("(801) 555-1234")
    idx = st.user_carrier.findData("verizon")
    assert idx > 0
    st.user_carrier.setCurrentIndex(idx)
    assert st.current_user_phone() == "8015551234"   # digits only
    assert st.current_user_carrier() == "verizon"


def test_sms_recipient_forwards_to_tabs(qapp):
    from stimtest.gui.main_window import MainWindow
    w = MainWindow(simulate_default=True)
    st = w.setup_tab
    st.user_phone.setText("8015551234")
    st.user_carrier.setCurrentIndex(st.user_carrier.findData("att"))
    st._emit_sms_recipient()
    vt = w._exp_tab_by_code["VT"][0]
    assert vt._sms_phone == "8015551234"
    assert vt._sms_carrier == "att"


def test_phone_carrier_pref_round_trip(qapp):
    from stimtest.gui.setup_tab import SetupTab
    st = SetupTab()
    st.user_phone.setText("8015551234")
    st.user_carrier.setCurrentIndex(st.user_carrier.findData("tmobile"))
    prefs = st.current_prefs()
    assert prefs["user_phone"] == "8015551234"
    assert prefs["user_carrier"] == "tmobile"
    st2 = SetupTab()
    st2.restore_prefs(prefs)
    assert st2.current_user_phone() == "8015551234"
    assert st2.current_user_carrier() == "tmobile"


# ---- RunnerWorker SMS send path ----------------------------------------
class _Sess:
    class test:
        experiment = "VT"


class _Runner:
    session = _Sess()

    def subscribe(self, *_):
        pass


def _worker(**kw):
    from stimtest.gui.experiment_tabs import RunnerWorker
    w = RunnerWorker(_Runner(), **kw)
    w._logs = []
    w.log_msg = type("S", (), {
        "emit": staticmethod(lambda m: w._logs.append(m))})()
    return w


def test_sms_gated_on_toggle_and_phone(qapp):
    # Notifications ON + phone + carrier (no SMTP) → graceful "skipped".
    w = _worker(email_notifications=True, sms_phone="8015551234",
                sms_carrier="verizon", session_subject="el_a1")
    w._maybe_send_completion_email(elapsed_seconds=42.0)
    assert any("text" in m.lower() for m in w._logs)
    # Notifications OFF → nothing fires.
    w2 = _worker(email_notifications=False, sms_phone="8015551234",
                 sms_carrier="verizon")
    w2._maybe_send_completion_email(elapsed_seconds=1.0)
    assert w2._logs == []
    # No carrier → no SMS attempt.
    w3 = _worker(email_notifications=True, sms_phone="8015551234",
                 sms_carrier="")
    w3._maybe_send_completion_email(elapsed_seconds=1.0)
    assert not any("text" in m.lower() for m in w3._logs)


def test_failure_sms_does_not_raise(qapp):
    w = _worker(email_notifications=True, sms_phone="8015551234",
                sms_carrier="att", session_subject="el_a1")
    # Must not raise even with no SMTP configured.
    w._maybe_send_failure_email(error_message="scope timeout\nline2",
                                elapsed_seconds=10.0)
    assert any("text" in m.lower() for m in w._logs)
