"""Tests for the incremental session-save helper.

Closes Task #54 (Mid-run crash recovery — write .npz per capture so a
crash leaves a recoverable file).  Verifies:

* ``save_session_npz`` accepts the new ``incomplete=`` kwarg and
  embeds the flag in ``meta.json``; defaults to False for the legacy
  one-shot end-of-run save.
* ``save_session_npz_incremental`` writes on first call, then
  throttles by time AND/OR by capture count.
* Throttled-skip returns the existing throttle state unchanged so
  caller can thread it back in on the next call.
* The same target file is overwritten each save (no leftover
  ``.partial`` files).
* Final ``save_session_npz`` overwrites the incomplete flag back to
  False so a successfully-finished run doesn't look like a crashed
  one when loaded later.
* Round-trip: load_session_meta surfaces the incomplete flag; load
  of legacy .npz (no flag) defaults to False without raising.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest


def _make_session_with_n_captures(n: int):
    """Build a minimal Session with ``n`` captures on a single channel
    run.  Bare minimum to exercise the persistence layer — real
    runners produce richer sessions but the file-layout contract
    doesn't care about the metric values themselves."""
    from stimtest.session import (
        Capture, CaptureMetrics, CaptureStatus, ChannelRun, Session,
        TestParameters,
    )
    from stimtest.waveforms import Phase, PulsePattern
    from stimtest.electrode import Configuration, ElectrodeArray

    pat = PulsePattern.biphasic(
        amplitude_ua=100.0, phase_width_us=200.0, rate_hz=50.0)
    arr = ElectrodeArray.utah_4x4()
    cfg = Configuration.monopolar(1)

    captures = []
    for i in range(n):
        captures.append(Capture(
            index=i,
            pattern=pat,
            time_us=np.arange(500.0, dtype=np.float64),
            v_mon_v=np.zeros(500, dtype=np.float64),
            i_mon_ua=np.zeros(500, dtype=np.float64),
            metrics=CaptureMetrics(),
            status=CaptureStatus(good=True),
        ))

    run = ChannelRun(configuration=cfg, captures=captures)
    sess = Session(
        notebook="test",
        subject="unit-test",
        user_name="pytest",
        user_email="",
        test=TestParameters(
            experiment="VT",
            duration_s=0.0,
            polarization_method="MP",
            counter_electrode_label="",
            reference_electrode_label="",
            target_charge_phase_nc=0.0,
            configuration=cfg,
            pattern=pat,
            array=arr,
        ),
        runs=[run],
    )
    return sess


# ---------------------------------------------------------------------------
# save_session_npz incomplete= kwarg
# ---------------------------------------------------------------------------
def test_save_session_default_marks_incomplete_false(tmp_path):
    """Default behavior (no incomplete= kwarg) writes incomplete=False
    so legacy callers and the end-of-run save produce 'finished' files."""
    from stimtest.persistence import save_session_npz, load_session_meta

    sess = _make_session_with_n_captures(2)
    path = tmp_path / "sess.npz"
    save_session_npz(sess, path)
    meta = load_session_meta(path)
    assert meta.get("incomplete") is False


def test_save_session_incomplete_true_embedded_in_meta(tmp_path):
    """When ``incomplete=True``, the flag lands in meta.json so loaders
    can surface a 'partial' badge."""
    from stimtest.persistence import save_session_npz, load_session_meta

    sess = _make_session_with_n_captures(2)
    path = tmp_path / "sess.npz"
    save_session_npz(sess, path, incomplete=True)
    meta = load_session_meta(path)
    assert meta.get("incomplete") is True


def test_load_session_meta_tolerates_missing_incomplete_key(tmp_path):
    """Legacy .npz files written before the flag was added MUST load
    without raising.  We simulate by writing then manually stripping
    the key, then reloading."""
    from stimtest.persistence import save_session_npz, load_session_meta

    sess = _make_session_with_n_captures(1)
    path = tmp_path / "legacy.npz"
    save_session_npz(sess, path)

    # Tamper: rewrite the .npz with meta.json missing the incomplete key.
    with np.load(path, allow_pickle=False) as z:
        meta = json.loads(z["meta.json"].tobytes().decode("utf-8"))
        arrays = {k: z[k] for k in z.files if k != "meta.json"}
    meta.pop("incomplete", None)
    arrays["meta.json"] = np.frombuffer(
        json.dumps(meta).encode("utf-8"), dtype=np.uint8)
    np.savez_compressed(path, **arrays)

    # Reload — should NOT raise, and incomplete should default to False
    # via the .get() at the call site.
    meta2 = load_session_meta(path)
    assert meta2.get("incomplete", False) is False


# ---------------------------------------------------------------------------
# save_session_npz_incremental — first call always writes
# ---------------------------------------------------------------------------
def test_incremental_first_call_writes(tmp_path):
    """First call (last_save_at=None) writes regardless of throttle."""
    from stimtest.persistence import save_session_npz_incremental, load_session_meta

    sess = _make_session_with_n_captures(1)
    path = tmp_path / "incr.npz"
    out_path, last_t, last_n, did_write = save_session_npz_incremental(
        sess, path)

    assert did_write is True
    assert out_path == path
    assert path.exists()
    assert last_t is not None
    assert last_n == 1
    # File has incomplete=True.
    assert load_session_meta(path).get("incomplete") is True


def test_incremental_throttled_skip_returns_unchanged_state(tmp_path):
    """Second call within the min interval AND no new captures should
    skip the write and return the existing throttle state unchanged."""
    from stimtest.persistence import save_session_npz_incremental

    sess = _make_session_with_n_captures(1)
    path = tmp_path / "incr.npz"
    _, t1, n1, did_write_1 = save_session_npz_incremental(
        sess, path)
    assert did_write_1 is True

    # Immediate retry — same session, no time elapsed.  Should skip.
    _, t2, n2, did_write_2 = save_session_npz_incremental(
        sess, path,
        last_save_at=t1, last_capture_count=n1,
        min_interval_s=60.0,         # high time threshold
        min_capture_interval=10,     # high capture threshold
    )
    assert did_write_2 is False
    assert t2 == t1
    assert n2 == n1


def test_incremental_new_capture_forces_write(tmp_path):
    """New capture arriving since last save bypasses the time throttle
    when ``min_capture_interval=1`` (the default)."""
    from stimtest.persistence import save_session_npz_incremental, load_session_meta

    sess = _make_session_with_n_captures(1)
    path = tmp_path / "incr.npz"
    _, t1, n1, _ = save_session_npz_incremental(sess, path)
    assert n1 == 1

    # Add a capture, retry — should write even though no time has elapsed.
    sess.runs[0].captures.append(sess.runs[0].captures[0])
    _, t2, n2, did_write_2 = save_session_npz_incremental(
        sess, path,
        last_save_at=t1, last_capture_count=n1,
        min_interval_s=60.0,         # time throttle would skip
        min_capture_interval=1,      # 1 new capture is enough
    )
    assert did_write_2 is True
    assert n2 == 2
    # File should now contain the second capture in its meta.
    meta = load_session_meta(path)
    assert len(meta["runs"][0]["captures"]) == 2


def test_incremental_no_rewrite_without_new_data(tmp_path):
    """Time elapsed must NOT force a rewrite when NO new capture has landed
    since the last save (operator #6): a long PCC run's multi-second capture
    cycle otherwise re-wrote a fresh ~10 MB npz of IDENTICAL data every
    ``min_interval_s`` (~14 back-to-back writes at the same capture count).
    The time throttle only CAPS the rate; new data is required to write."""
    from stimtest.persistence import save_session_npz_incremental

    sess = _make_session_with_n_captures(1)
    path = tmp_path / "incr.npz"
    _, t1, n1, _ = save_session_npz_incremental(sess, path)

    # Lots of time elapsed but NO new captures → SKIP (unchanged data).
    fake_old_t = t1 - 100.0  # 100 s in the past
    _, t2, n2, did_write = save_session_npz_incremental(
        sess, path,
        last_save_at=fake_old_t, last_capture_count=n1,
        min_interval_s=2.0,          # 100 s > 2 s — but no new data
        min_capture_interval=10,
    )
    assert did_write is False
    assert t2 == fake_old_t and n2 == n1   # throttle state unchanged

    # WITH a new capture + elapsed time → write.
    sess.runs[0].captures.append(sess.runs[0].captures[0])
    _, _, n3, did_write2 = save_session_npz_incremental(
        sess, path,
        last_save_at=fake_old_t, last_capture_count=n1,
        min_interval_s=2.0, min_capture_interval=10,
    )
    assert did_write2 is True
    assert n3 == 2


def test_incremental_overwrites_same_file(tmp_path):
    """Repeated calls write to the SAME path — no .partial suffix
    or rotation.  Operator sees the latest state when opening the
    .npz mid-run."""
    from stimtest.persistence import save_session_npz_incremental

    sess = _make_session_with_n_captures(1)
    path = tmp_path / "incr.npz"

    _, t1, n1, _ = save_session_npz_incremental(sess, path)
    # Add a few more captures and force re-saves.
    for i in range(3):
        sess.runs[0].captures.append(sess.runs[0].captures[0])
        _, t1, n1, _ = save_session_npz_incremental(
            sess, path,
            last_save_at=None,  # force write
            last_capture_count=0,
        )

    # Only the target file should exist — no .partial sidecars.
    npz_files = list(tmp_path.glob("*.npz"))
    assert len(npz_files) == 1
    assert npz_files[0] == path


# ---------------------------------------------------------------------------
# End-of-run final save overwrites incomplete=True with =False
# ---------------------------------------------------------------------------
def test_final_save_overwrites_incomplete_flag(tmp_path):
    """After the runner finishes successfully and calls
    ``save_session_npz`` (no incomplete= kwarg), the file should
    have ``incomplete=False`` even though prior incremental saves
    set it to True."""
    from stimtest.persistence import (
        save_session_npz, save_session_npz_incremental, load_session_meta,
    )

    sess = _make_session_with_n_captures(2)
    path = tmp_path / "run.npz"

    # Simulate mid-run incremental saves.
    save_session_npz_incremental(sess, path)
    assert load_session_meta(path).get("incomplete") is True

    # End-of-run final save (default incomplete=False).
    save_session_npz(sess, path)
    assert load_session_meta(path).get("incomplete") is False


def test_round_trip_after_incremental_save(tmp_path):
    """Loading a partial-saved session should round-trip all the
    captured data without losing arrays — incomplete-marked files
    are valid sessions, just flagged as in-progress."""
    from stimtest.persistence import (
        save_session_npz_incremental, load_session_npz,
    )

    sess = _make_session_with_n_captures(3)
    path = tmp_path / "incr.npz"
    save_session_npz_incremental(sess, path)

    loaded = load_session_npz(path)
    assert len(loaded.runs) == 1
    assert len(loaded.runs[0].captures) == 3
    # Each capture's arrays should be present and the right shape.
    for c in loaded.runs[0].captures:
        assert c.time_us.shape == (500,)
        assert c.v_mon_v.shape == (500,)
