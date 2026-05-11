"""Round-trip tests for :mod:`stimtest.persistence`.

Closes the audit's #6 + #19 + test-coverage-gap findings in one place:
the save / load path used to silently drop nine ``CaptureMetrics``
fields and both ``ElectrodeArray.cable_map`` and ``layout`` — and
because there was no test file for ``persistence.py`` at all, the
losses were invisible. These tests assert every dataclass field that
:func:`stimtest.persistence.save_session_npz` writes is faithfully
reconstructed by :func:`stimtest.persistence.load_session_npz`.
"""
from __future__ import annotations

import math
from dataclasses import fields
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from stimtest.electrode import (
    Configuration, ElectrodeArray, ElectrodePosition,
)
from stimtest.persistence import load_session_npz, save_session_npz
from stimtest.session import (
    Capture, CaptureMetrics, CaptureStatus, ChannelRun, Session,
    TestParameters,
)
from stimtest.waveforms import PulsePattern


# ---------------------------------------------------------------------------
# Test fixtures — one fully-populated session with every nullable / list /
# scalar field set to a *distinct* sentinel so the round-trip can verify
# each one independently.
# ---------------------------------------------------------------------------
def _fully_populated_metrics() -> CaptureMetrics:
    """A CaptureMetrics with every field set to a unique recognisable
    value. Lists carry two entries (matching a biphasic pattern's
    phase-boundary count) so the round-trip exercises list-of-float
    serialisation as well as scalars."""
    return CaptureMetrics(
        driving_voltage_v=2.0,
        active_driving_voltage_per_phase_v=[1.1, 1.2],
        return_driving_voltage_per_phase_v=[0.91, 0.92],
        access_voltage_per_phase_v=[0.51, 0.52],
        access_resistance_per_phase_kohm=[10.1, 10.2],
        return_access_voltage_per_phase_v=[0.41, 0.42],
        return_access_resistance_per_phase_kohm=[9.1, 9.2],
        polarization_per_phase_v=[0.31, 0.32],
        return_polarization_per_phase_v=[0.21, 0.22],
        charge_per_phase_nc=20.0,
        charge_injection_mc_per_cm2=0.4,
        effective_capacitance_nf=1.5,
        driving_capacitance_mf_per_cm2=0.025,
        interpulse_potential_v=-0.05,
        return_pre_pulse_potential_v=-0.04,
        return_post_pulse_potential_v=-0.06,
        shannon_k_value=1.42,
        damage_classification="above_shannon",
        damage_criteria={"shannon": True, "macro_cap": False, "micro_cap": False},
        damage_band="meso",
        damage_level=2,
        neurostimml_classification="likely_damaging",
        neurostimml_probability=0.78,
    )


def _make_session(*, cable_map=None, layout="rect") -> Session:
    """Build a single-run, two-capture session with the given array
    parameters. Both captures use the same pattern; only the second
    one's metrics fully populate every CaptureMetrics field so the
    test can also verify defaults-for-unset on the first capture."""
    pattern = PulsePattern.biphasic(
        amplitude_ua=100.0, phase_width_us=200.0,
        rate_hz=50.0, polarity=-1,
    )
    cfg = Configuration(id="ch5_mp", active=5, returns=(6,))
    sites = [ElectrodePosition(number=n, row=(n - 1) // 4, col=(n - 1) % 4,
                                surface_area_um2=5000.0, coating="SIROF")
             for n in range(1, 5)]
    array = ElectrodeArray(
        name="test-2x2", rows=2, cols=2, sites=sites,
        cable_map=cable_map, layout=layout,
    )
    test = TestParameters(
        experiment="VT", pattern=pattern, configuration=cfg, array=array,
        duration_s=60.0,
    )
    session = Session(
        notebook="test_nb", subject="electrode_a1",
        user_name="Operator", user_email="op@example.com",
        test=test,
    )
    run = ChannelRun(
        configuration=cfg, surface_area_um2=5000.0,
        started_at=datetime(2026, 5, 11, 12, 0, 0),
        finished_at=datetime(2026, 5, 11, 12, 30, 0),
    )
    # Capture 1 — minimal metrics (will exercise the default-value path)
    cap1 = Capture(
        index=0, pattern=pattern, timestamp=datetime(2026, 5, 11, 12, 0, 5),
        time_us=np.array([0.0, 1.0, 2.0]),
        v_mon_v=np.array([0.0, 0.5, 0.0]),
        i_mon_ua=np.array([0.0, 100.0, 0.0]),
        e_act_v=np.array([0.1, 0.2, 0.1]),
        e_ret_v=np.array([0.0, 0.05, 0.0]),
    )
    # Capture 2 — fully populated, distinct sentinel values everywhere
    cap2 = Capture(
        index=1, pattern=pattern, timestamp=datetime(2026, 5, 11, 12, 1, 0),
        time_us=np.array([0.0, 1.0, 2.0, 3.0]),
        v_mon_v=np.array([0.0, 0.5, -0.5, 0.0]),
        i_mon_ua=np.array([0.0, 100.0, -100.0, 0.0]),
        e_act_v=None,  # one capture without optional traces
        e_ret_v=None,
        metrics=_fully_populated_metrics(),
        status=CaptureStatus(good=True, reached_potential_limit=True,
                              voltage_compliance=False, aborted=False,
                              notes="hit potential limit"),
    )
    run.captures = [cap1, cap2]
    session.runs = [run]
    return session


# ---------------------------------------------------------------------------
# #6 — every CaptureMetrics field must round-trip
# ---------------------------------------------------------------------------
def test_capturemetrics_full_roundtrip(tmp_path: Path):
    """Save a session with every CaptureMetrics field set to a unique
    sentinel value, load it back, and verify each field survived.

    Regression coverage for audit finding #6: the loader previously
    restored only 12 of 20 metrics fields, silently resetting Shannon
    k-value, damage classification / criteria / band / level,
    NeurostimML verdict / probability, and the pre/post return-pulse
    rest potentials on every save → reload cycle."""
    session = _make_session()
    npz_path = tmp_path / "roundtrip.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)

    original = session.runs[0].captures[1].metrics
    restored = loaded.runs[0].captures[1].metrics

    # Walk every CaptureMetrics field and assert equality. Using
    # ``fields()`` rather than a hand-typed list means new fields
    # added in the future participate in the test automatically.
    for f in fields(CaptureMetrics):
        ov = getattr(original, f.name)
        rv = getattr(restored, f.name)
        if isinstance(ov, float) and math.isnan(ov):
            assert math.isnan(rv), f"{f.name}: expected NaN, got {rv!r}"
        elif isinstance(ov, list):
            assert list(rv) == ov, f"{f.name}: {rv!r} != {ov!r}"
        elif isinstance(ov, dict):
            assert dict(rv) == ov, f"{f.name}: {rv!r} != {ov!r}"
        else:
            assert rv == ov, f"{f.name}: {rv!r} != {ov!r}"


def test_capturemetrics_defaults_on_minimal_capture(tmp_path: Path):
    """A capture that left metrics at their dataclass defaults must
    round-trip those defaults faithfully (not get over-written by a
    sibling capture's populated values)."""
    session = _make_session()
    npz_path = tmp_path / "defaults.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)

    default_metrics = CaptureMetrics()
    restored = loaded.runs[0].captures[0].metrics

    for f in fields(CaptureMetrics):
        ov = getattr(default_metrics, f.name)
        rv = getattr(restored, f.name)
        if isinstance(ov, float) and math.isnan(ov):
            assert math.isnan(rv), f"{f.name}: expected NaN, got {rv!r}"
        elif isinstance(ov, list):
            assert list(rv) == ov, f"{f.name}: {rv!r} != {ov!r}"
        elif isinstance(ov, dict):
            assert dict(rv) == ov, f"{f.name}: {rv!r} != {ov!r}"
        else:
            assert rv == ov, f"{f.name}: {rv!r} != {ov!r}"


# ---------------------------------------------------------------------------
# #19 — ElectrodeArray.cable_map and .layout must round-trip
# ---------------------------------------------------------------------------
def test_electrodearray_layout_roundtrip_triangular(tmp_path: Path):
    """A hex / triangular layout must NOT come back as ``"rect"`` —
    that bug silently corrupted Channel Map rendering on hex arrays.
    """
    session = _make_session(layout="triangular")
    npz_path = tmp_path / "triangular.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)
    assert loaded.test.array.layout == "triangular"


def test_electrodearray_layout_roundtrip_rect(tmp_path: Path):
    """The common-case ``rect`` layout must also round-trip (sanity
    check that the new default doesn't accidentally drift)."""
    session = _make_session(layout="rect")
    npz_path = tmp_path / "rect.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)
    assert loaded.test.array.layout == "rect"


def test_electrodearray_cable_map_roundtrip_with_mapping(tmp_path: Path):
    """A non-identity ``cable_map`` (the NeuroNexus use case) must
    survive save → load with int keys preserved. JSON serialises int
    keys as strings; the loader must cast back so the runtime
    contract ``Dict[int, int]`` holds."""
    cable_map = {1: 3, 2: 1, 3: 2, 4: 4}
    session = _make_session(cable_map=cable_map)
    npz_path = tmp_path / "cable.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)
    assert loaded.test.array.cable_map == cable_map
    # And the keys really are ints (would fail if the loader left
    # them as JSON-style strings).
    assert all(isinstance(k, int) for k in loaded.test.array.cable_map)


def test_electrodearray_cable_map_roundtrip_identity(tmp_path: Path):
    """``cable_map=None`` (identity routing, the production default)
    must come back as ``None``, not an empty dict."""
    session = _make_session(cable_map=None)
    npz_path = tmp_path / "identity.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)
    assert loaded.test.array.cable_map is None


# ---------------------------------------------------------------------------
# Belt-and-braces — make sure waveform arrays + session-level metadata
# also survive. These aren't tied to the audit findings but they're the
# bulk of what a session contains and the test suite previously had
# zero coverage of any round-trip path.
# ---------------------------------------------------------------------------
def test_session_metadata_roundtrip(tmp_path: Path):
    session = _make_session()
    npz_path = tmp_path / "meta.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)
    assert loaded.notebook == "test_nb"
    assert loaded.subject == "electrode_a1"
    assert loaded.user_name == "Operator"
    assert loaded.user_email == "op@example.com"
    assert loaded.test.experiment == "VT"
    assert loaded.test.duration_s == 60.0


def test_capture_arrays_roundtrip(tmp_path: Path):
    """Time / V_mon / I_mon / E_act / E_ret arrays survive intact."""
    session = _make_session()
    npz_path = tmp_path / "arrays.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)

    cap1 = loaded.runs[0].captures[0]
    cap2 = loaded.runs[0].captures[1]

    np.testing.assert_array_equal(cap1.time_us, [0.0, 1.0, 2.0])
    np.testing.assert_array_equal(cap1.v_mon_v, [0.0, 0.5, 0.0])
    np.testing.assert_array_equal(cap1.i_mon_ua, [0.0, 100.0, 0.0])
    np.testing.assert_array_equal(cap1.e_act_v, [0.1, 0.2, 0.1])
    np.testing.assert_array_equal(cap1.e_ret_v, [0.0, 0.05, 0.0])

    # cap2 stored ``None`` for the optional E_* traces — those should
    # come back as ``None``, not zero-length arrays.
    assert cap2.e_act_v is None
    assert cap2.e_ret_v is None


def test_capture_status_roundtrip(tmp_path: Path):
    session = _make_session()
    npz_path = tmp_path / "status.npz"
    save_session_npz(session, npz_path)
    loaded = load_session_npz(npz_path)
    status = loaded.runs[0].captures[1].status
    assert status.good is True
    assert status.reached_potential_limit is True
    assert status.voltage_compliance is False
    assert status.aborted is False
    assert status.notes == "hit potential limit"


def test_legacy_npz_without_new_fields(tmp_path: Path):
    """A .npz written before the audit-#6/#19 fixes will lack the new
    fields. The loader must accept it gracefully with sensible
    defaults rather than raising KeyError."""
    import json
    import numpy as _np
    # Hand-craft a minimal meta blob that mimics the OLD schema —
    # one capture, minimal CaptureMetrics fields only, no
    # cable_map / layout on the array.
    legacy_meta = {
        "notebook": "legacy", "subject": "old_e1",
        "user_name": "", "user_email": "",
        "created_at": "2026-01-01T00:00:00",
        "test": {
            "experiment": "VT", "duration_s": 60.0,
            "polarization_method": "time",
            "counter_electrode_label": "Pt counter",
            "reference_electrode_label": "Ag|AgCl",
            "target_charge_phase_nc": 1e9,
            "configuration": {"id": "ch5_mp", "active": 5,
                               "returns": [6], "counter_electrode_label": "Pt counter"},
            "pattern": {
                "rate_hz": 50.0, "repetitions": 0,
                "phases": [
                    {"amplitude_ua": -100.0, "width_us": 200.0,
                     "delay_after_us": 20.0, "shape": "rectangular",
                     "bump_count": 1, "tau_us": 0.0,
                     "tail_zero_us": 0.0, "offset_ua": 0.0},
                    {"amplitude_ua": 100.0, "width_us": 200.0,
                     "delay_after_us": 20.0, "shape": "rectangular",
                     "bump_count": 1, "tau_us": 0.0,
                     "tail_zero_us": 0.0, "offset_ua": 0.0},
                ],
            },
            "array": {
                "name": "legacy-array", "rows": 1, "cols": 1,
                "sites": [{"number": 1, "row": 0, "col": 0,
                           "surface_area_um2": 5000.0, "coating": "SIROF",
                           "geometry": "circle", "rounded": False}],
                # Note: no "layout" or "cable_map" keys — this is the
                # whole point of the legacy fixture.
            },
        },
        "runs": [{
            "configuration": {"id": "ch5_mp", "active": 5,
                              "returns": [6], "counter_electrode_label": "Pt counter"},
            "surface_area_um2": 5000.0,
            "started_at": "2026-01-01T00:00:00",
            "finished_at": None,
            "captures": [{
                "index": 0, "timestamp": "2026-01-01T00:00:00",
                # ``pattern`` key intentionally omitted — the loader
                # falls back to the test-level pattern via
                # ``cap_meta.get("pattern", p)``. This is the legacy
                # path the test is here to exercise.
                # metrics also intentionally MISSING — exercises
                # the all-defaults path in the loader.
                "status": {"good": True, "reached_potential_limit": False,
                           "voltage_compliance": False, "aborted": False,
                           "notes": ""},
            }],
        }],
    }
    legacy_path = tmp_path / "legacy.npz"
    payload = json.dumps(legacy_meta).encode("utf-8")
    _np.savez(legacy_path, **{
        "meta.json": _np.frombuffer(payload, dtype=_np.uint8),
        "r0_c0_time_us": _np.array([0.0]),
        "r0_c0_v_mon_v": _np.array([0.0]),
        "r0_c0_i_mon_ua": _np.array([0.0]),
    })

    # Loader must succeed and surface sensible defaults for every
    # field the legacy archive omitted.
    loaded = load_session_npz(legacy_path)
    assert loaded.test.array.layout == "rect"
    assert loaded.test.array.cable_map is None
    m = loaded.runs[0].captures[0].metrics
    assert math.isnan(m.shannon_k_value)
    assert m.damage_classification == "insufficient_data"
    assert m.damage_criteria == {}
    assert m.damage_band == ""
    assert m.damage_level == -1
    assert m.neurostimml_classification == "model_not_installed"
    assert math.isnan(m.neurostimml_probability)
    assert math.isnan(m.return_pre_pulse_potential_v)
    assert math.isnan(m.return_post_pulse_potential_v)
