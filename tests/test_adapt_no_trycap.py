"""adapt_channel_scale must NEVER lock via a lifetime "try N/10" cap.

Operator (exp_vt_max CH14): "remove the N/10 counter."  CH14's V_mon was
railed on the last 3 captures — pinned at [-1156,+876] mV with 11-13 % of
samples at the ADC rail while the programmed current kept climbing
(730→1102 µA).  Root cause: the removed ``_ADAPT_MAX_TRIES`` counted DISTINCT
scales tried over the CHANNEL'S LIFETIME, but its ``history`` list accumulates
across EVERY capture in the run — so a long VT ramp exhausted the cap
(try 10/10), set ``locked=True``, and froze the V/div for the remaining higher-
amplitude captures, clipping them.  A growing signal must keep upscaling.
"""
from __future__ import annotations


def _scope(current_scale: float, half_vert_divs: float = 5.0):
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._half_vert_divs = half_vert_divs
    s._log = lambda *a, **k: None
    writes = []
    s.set_channel_scale = lambda ch, vpd: writes.append((ch, vpd))
    s._adapt_state = {"CH1": s._new_adapt_state()}
    s._adapt_state["CH1"]["last_scale"] = current_scale
    return s, writes


def test_growing_signal_never_locks_via_try_cap():
    # Simulate a long VT amplitude ramp: the signal grows every capture and
    # each is CLIP-detected (force_grow, as the real rescale loop passes on a
    # railed capture), so the adapt must upscale WELL PAST the old 10-write
    # cap without ever locking.
    s, writes = _scope(current_scale=0.002)      # 2 mV/div start
    last = None
    for i in range(16):
        half = 0.010 * (1.5 ** i)                # grows each "capture"
        if half > 12.0:
            break                                # stay under the 5 V/div grid max
        out = s.adapt_channel_scale("CH1", v_min=-half, v_max=half,
                                    divs=3.0, shrink_stable_count=1,
                                    force_grow=True)
        if out is not None:
            last = out

    st = s._adapt_state["CH1"]
    assert not st["locked"], "adapt must not lock via a lifetime try-cap"
    # It kept writing well past the old 10-try cap …
    assert len(writes) >= 12, f"adapt froze early: only {len(writes)} writes"
    # … and the scale genuinely climbed the grid (no freeze mid-ramp).
    assert last is not None and last >= 1.0, f"scale did not climb: {last}"
    # history is allowed to grow; the point is it no longer TRIGGERS a lock.
    assert len(st["history"]) >= 12


def test_max_tries_constant_is_gone():
    from stimtest.hardware.tektronix import TektronixOscilloscope
    assert not hasattr(TektronixOscilloscope, "_ADAPT_MAX_TRIES")


def test_no_persistent_lock_after_oscillation_then_growing_ramp():
    """Operator: "never should have a cap."

    The N/10 lifetime cap was gone, but a ``st["locked"] = True`` flag still
    survived on the adjacent-cell oscillation path (and the grid-min/max
    accepts).  Once set, the top-of-method short-circuit froze the V/div for
    EVERY later capture — even ``force_grow`` clipped ones — so a channel that
    briefly oscillated at low amplitude then RAILED as the ramp climbed
    (exactly CH14).  This reproduces that: force a two-cell oscillation, then
    drive a growing clipped ramp and assert the scale keeps climbing.
    """
    s, writes = _scope(current_scale=0.045)      # 45 mV/div, mid-grid

    # Phase 1 — provoke the adjacent-cell oscillation guard by re-picking the
    # neighbouring 50 mV/div cell several times on a ~stationary signal.  In
    # the OLD code this set locked=True.
    for _ in range(6):
        # half_range ≈ 150 mV → ideal ceil-snaps just ABOVE 45 mV/div (an
        # adjacent cell), the classic two-cell flip.
        s.adapt_channel_scale("CH1", v_min=-0.150, v_max=+0.150,
                              divs=3.0, shrink_stable_count=1)
    assert not s._adapt_state["CH1"]["locked"], \
        "oscillation guard must not set a persistent lock"

    # Phase 2 — the ramp climbs and every capture is CLIP-detected
    # (force_grow, as the real rescale loop passes on a railed read).  The
    # scale MUST keep growing; the old lock would have frozen it here.
    last = s._adapt_state["CH1"]["last_scale"]
    for i in range(10):
        half = 0.30 * (1.4 ** i)                  # 300 mV → grows each capture
        if half > 12.0:
            break
        out = s.adapt_channel_scale("CH1", v_min=-half, v_max=half,
                                    divs=3.0, shrink_stable_count=1,
                                    force_grow=True)
        if out is not None:
            assert out >= last, f"scale went DOWN mid-grow: {last} → {out}"
            last = out
    assert not s._adapt_state["CH1"]["locked"], "must never lock mid-ramp"
    # It climbed well past the oscillation region toward the grid ceiling.
    assert last >= 1.0, f"growing clipped ramp did not upscale: {last} V/div"


def test_grid_min_accept_does_not_freeze_a_later_grow():
    """A tiny start-of-ramp capture that hits grid min must not strand the
    channel there for the rest of the ramp (no grid-min lock)."""
    s, writes = _scope(current_scale=0.001)      # 1 mV/div = grid min-ish
    # Tiny signal → wants to shrink below grid min → accept (return None),
    # but MUST NOT lock.
    s.adapt_channel_scale("CH1", v_min=-0.0005, v_max=+0.0005,
                          divs=3.0, shrink_stable_count=1)
    assert not s._adapt_state["CH1"]["locked"]
    # Now the ramp grows large + clips → must upscale off the grid min.
    out = s.adapt_channel_scale("CH1", v_min=-0.90, v_max=+0.90,
                                divs=3.0, shrink_stable_count=1,
                                force_grow=True)
    assert out is not None and out > 0.001, \
        f"grid-min accept froze a later grow: {out}"
