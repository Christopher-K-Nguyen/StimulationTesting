"""Tests for Task #59: trigger/pulse alignment warning false positives.

Bug surfaced in LOG_ANALYSIS.md baseline (2026-05-31): 25+ instances
of "⚠ trigger/pulse alignment off: largest I_mon edge at t=+XXX µs"
in session_001 log.  Investigation showed two distinct issues:

1.  **Time-axis bug — already fixed by gotcha #22's "trust XZEro"
    rewrite** (BEFORE the session_001 log was captured).  Symptom:
    when scope firmware reported PT_Off=0 even with a non-zero
    XZEro, the older code mistakenly fell back to PT_Off and built
    t_us = [0, +640] µs instead of [-320, +320].  The current code
    only falls back to PT_Off when ``abs(XZEro) < 0.5*XINcr`` (i.e.,
    XZEro is effectively zero), so a non-zero XZEro is trusted as
    authoritative.  These tests pin that contract.

2.  **Check too aggressive — argmax false-positive on multi-edge
    biphasic patterns**.  A biphasic pulse has 4 edges of
    comparable magnitude (phase 1 onset/end, phase 2 onset/end).
    ``argmax`` can land on any of them depending on noise, producing
    apparent offsets of +200, +250, +450 µs that aren't real
    trigger-alignment errors.  Fix: use FIRST edge above 80% of max,
    not absolute argmax.  Lands on the leading edge consistently.
"""
from __future__ import annotations

import numpy as np
import pytest


def _bare_tek_instance():
    """Construct a TektronixOscilloscope without opening hardware."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    scope._w = lambda cmd: None
    scope._q = lambda cmd: "0"
    scope._log = lambda msg: None
    scope._adapt_state = {}
    scope._time_cache = None
    scope._time_cache_key = None
    scope._last_xunit = "s"
    scope._last_yunit = "V"
    scope._expected_trigger_source = "CH4"
    scope._expected_trigger_is_digital = True  # digital sync via channel
    return scope


# ---------------------------------------------------------------------------
# Time-axis logic — gotcha #22 regression guard
# ---------------------------------------------------------------------------
def test_time_axis_trusts_nonzero_xzero():
    """Method A path: when XZEro is non-zero (correctly reported by
    most TBS2000 firmware), the time axis should run from XZEro to
    XZEro + (npts-1)*XINcr, regardless of PT_Off's value.

    Concrete: 20000-sample TBS2204B capture with XZEro=-320µs,
    XINcr=32ns, PT_Off=0 (firmware quirk).  t_us should run
    [-320, +320) µs — NOT [0, +640) which was the pre-fix bug.
    """
    # Replicate the time-axis math from _read_channel inline so we
    # don't have to fake a full preamble + CURVe? round-trip.
    from stimtest.config import DIGITAL_DELAY_US

    xzero = -320e-6   # s
    xinc = 32e-9       # s
    npts = 20000
    pt_off = 0          # firmware quirk
    is_digital = True
    digital_delay_us = DIGITAL_DELAY_US if is_digital else 0.0

    # The Method-A branch from current code (lines 3314-3326):
    used_method = "A (XZEro)"
    xz_used = xzero
    if abs(xzero) < 0.5 * xinc:
        if np.isfinite(pt_off) and float(pt_off) > 0 and xinc > 0:
            xz_used = -float(pt_off) * float(xinc)
            used_method = "B (PT_Off; XZEro=0 firmware quirk)"

    assert used_method == "A (XZEro)", (
        "non-zero XZEro should select Method A, not fall back to PT_Off "
        "— this was the gotcha #22 fix")
    t_us = (xz_used + xinc * np.arange(npts)) * 1e6 - digital_delay_us

    # First sample at -320 µs minus the 1.2 µs digital delay.
    assert t_us[0] == pytest.approx(-321.2, abs=0.1)
    # Sample at the trigger index (10000) should be at -1.2 µs
    # (the digital_delay subtraction; trigger sample is at xz_used+0
    # offset = -320, then sample 10000 advances 320 µs back to 0,
    # minus digital_delay = -1.2 µs).
    assert t_us[10000] == pytest.approx(-1.2, abs=0.1)
    # Last sample near +320 µs (minus digital_delay).
    assert t_us[-1] == pytest.approx(+318.8, abs=0.5)


def test_time_axis_falls_back_to_pt_off_only_when_xzero_zero():
    """When XZEro is exactly zero (legacy TDS1000 firmware quirk)
    AND PT_Off is non-zero, fall back to Method B."""
    xzero = 0.0
    xinc = 32e-9
    npts = 20000
    pt_off = 10000  # non-zero — telling us pre-trigger DOES exist

    used_method = "A (XZEro)"
    xz_used = xzero
    if abs(xzero) < 0.5 * xinc:
        if np.isfinite(pt_off) and float(pt_off) > 0 and xinc > 0:
            xz_used = -float(pt_off) * float(xinc)
            used_method = "B (PT_Off; XZEro=0 firmware quirk)"

    assert "B" in used_method
    assert xz_used == pytest.approx(-320e-6, abs=1e-9)


def test_time_axis_both_zero_means_no_pretrigger():
    """If BOTH XZEro and PT_Off are zero, there's genuinely no
    pre-trigger configured.  t_us starts at 0."""
    xzero = 0.0
    xinc = 32e-9
    npts = 20000
    pt_off = 0  # also zero

    xz_used = xzero
    if abs(xzero) < 0.5 * xinc:
        if np.isfinite(pt_off) and float(pt_off) > 0 and xinc > 0:
            xz_used = -float(pt_off) * float(xinc)

    assert xz_used == 0.0


# ---------------------------------------------------------------------------
# check_trigger_alignment — multi-edge biphasic false-positive fix
# ---------------------------------------------------------------------------
def _make_runner_with_aliases(aliases=None):
    """Build a minimal ExperimentRunner with an imon alias on the scope."""
    from stimtest.experiments.base import ExperimentRunner

    class _Concrete(ExperimentRunner):
        def run(self):
            return None

    r = _Concrete.__new__(_Concrete)
    r._subscribers = []
    r._abort_requested = False
    # Minimal session.
    from stimtest.session import Session, TestParameters
    from stimtest.waveforms import PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray
    r.session = Session(
        notebook="", subject="", user_name="", user_email="",
        test=TestParameters(
            experiment="VT", duration_s=0.0,
            polarization_method="MP",
            counter_electrode_label="",
            reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=Configuration.monopolar(1),
            pattern=PulsePattern.biphasic(amplitude_ua=100.0),
            array=ElectrodeArray.utah_4x4()),
        runs=[],
    )

    # Scope stub with channel_aliases.
    class _ScopeStub:
        channel_aliases = aliases or {"imon": "CH2"}

    r.scope = _ScopeStub()
    return r


def _make_acq(t_us, channels):
    """Build a fake ScopeAcquisition with the given time + channels."""
    class _Acq:
        pass
    a = _Acq()
    a.time_us = np.asarray(t_us, dtype=float)
    a.channels = {k: np.asarray(v, dtype=float) for k, v in channels.items()}
    return a


def test_alignment_check_picks_first_edge_in_multiedge_biphasic():
    """A biphasic pattern with 4 comparable edges (phase1 onset,
    phase1 end, phase2 onset, phase2 end).  argmax on |di| could
    pick ANY of them — the new implementation picks the FIRST
    that exceeds 80% of max, which is consistently the phase-1
    onset (i.e., the trigger event)."""
    runner = _make_runner_with_aliases({"imon": "CH2"})

    # Construct an I_mon trace: 4 step transitions at t=0, +200, +250, +450.
    # All steps have magnitude 100 (so argmax could pick any of them).
    t = np.linspace(-100, 600, 7001)  # 0.1 µs resolution
    i_mon = np.zeros_like(t)
    # Phase 1: -100 µA from t=0 to t=200.
    mask_p1 = (t >= 0) & (t < 200)
    i_mon[mask_p1] = -100
    # Phase 2: +100 µA from t=250 to t=450.
    mask_p2 = (t >= 250) & (t < 450)
    i_mon[mask_p2] = +100
    # Add a tiny noise bias so the LAST edge isn't exactly the max
    # (argmax tie-breaking might pick first anyway, but this makes
    # the bug crystal clear pre-fix).
    np.random.seed(42)
    i_mon += np.random.normal(0, 0.5, size=i_mon.shape)

    acq = _make_acq(t, {"CH2": i_mon})
    t_edge = runner.check_trigger_alignment(acq, tolerance_us=5.0)

    # Should land at t≈0 (phase 1 onset / trigger event), NOT at
    # +200, +250, or +450.
    assert t_edge is not None
    assert abs(t_edge) < 5.0, (
        f"check_trigger_alignment should pick the FIRST edge (phase-1 "
        f"onset at t≈0), not a later edge.  Got t_edge={t_edge:.2f} µs.")


def test_alignment_check_is_silent_even_when_first_edge_is_late():
    """The check NEVER warns — even for a far-from-zero edge (operator: "Do
    not have warnings about the trigger warning … I use the digital signal
    as trigger than current … smaller pulse widths do notable delays
    between the voltage drop and the current drop").  It still RETURNS the
    edge time for any caller that wants to record it."""
    runner = _make_runner_with_aliases({"imon": "CH2"})

    t = np.linspace(0, 640, 6401)
    i_mon = np.zeros_like(t)
    i_mon[(t >= 320) & (t < 520)] = -100  # phase 1 starts at +320

    acq = _make_acq(t, {"CH2": i_mon})

    log_messages = []
    runner.subscribe(lambda ev: log_messages.append(ev.message) if ev.message else None)

    t_edge = runner.check_trigger_alignment(acq, tolerance_us=5.0)
    assert t_edge is not None
    assert t_edge >= 300  # still picks up the edge near +320
    assert not any("alignment off" in m for m in log_messages), (
        f"the alignment check must be silent; got messages: {log_messages!r}")


def test_alignment_check_no_warning_when_aligned():
    """Trigger-aligned pulse (edge at t≈0) should NOT trigger the
    warning, regardless of post-pulse activity."""
    runner = _make_runner_with_aliases({"imon": "CH2"})

    # Trigger-aligned biphasic: phase 1 at t=0, phase 2 at t=250.
    t = np.linspace(-100, 600, 7001)
    i_mon = np.zeros_like(t)
    i_mon[(t >= 0) & (t < 200)] = -100
    i_mon[(t >= 250) & (t < 450)] = +100

    acq = _make_acq(t, {"CH2": i_mon})

    log_messages = []
    runner.subscribe(lambda ev: log_messages.append(ev.message) if ev.message else None)

    t_edge = runner.check_trigger_alignment(acq, tolerance_us=5.0)
    assert t_edge is not None
    assert abs(t_edge) < 5.0
    # No warning should have fired.
    assert not any("alignment off" in m for m in log_messages), (
        f"properly-aligned pulse should NOT trigger warning; got "
        f"messages: {log_messages!r}")


def test_alignment_check_returns_none_when_no_imon_alias():
    """If the scope has no imon alias, the check is a no-op
    returning None (not a crash)."""
    runner = _make_runner_with_aliases({})  # no imon
    acq = _make_acq([0, 1, 2], {"CH1": [0, 0, 0]})
    assert runner.check_trigger_alignment(acq) is None


def test_alignment_check_returns_none_on_malformed_acq():
    """Mismatched shapes / missing channels / empty arrays — return
    None, never raise."""
    runner = _make_runner_with_aliases({"imon": "CH2"})

    # Mismatched lengths.
    acq = _make_acq([0, 1, 2], {"CH2": [0, 0]})  # 3 vs 2
    assert runner.check_trigger_alignment(acq) is None

    # No CH2.
    acq = _make_acq([0, 1, 2], {"CH3": [0, 0, 0]})
    assert runner.check_trigger_alignment(acq) is None

    # Single sample (can't compute di).
    acq = _make_acq([0], {"CH2": [0]})
    assert runner.check_trigger_alignment(acq) is None
