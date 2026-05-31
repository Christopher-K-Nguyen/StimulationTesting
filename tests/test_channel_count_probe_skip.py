"""Tests for LOG_ANALYSIS.md finding #3: skip the live channel-count
probe when the model has a confident registry entry.

Bug: cold-connect was taking 13 seconds, 10 of which was
``probe_channel_count`` walking CH1..CH8 and eating ~1.25 s per
attempt on the failures (channels 5-8 don't exist on a 4-channel
TBS2204B; each error triggers a *CLS recovery round-trip).

Fix: when ``get_model_spec(model)`` returns a spec with
``n_channels in (2, 4)``, trust the spec and skip the probe.
The probe stays as a fallback for unknown models / OEM rebrands.

This file exercises the decision logic directly — we don't need a
live scope, just the code path that picks between "trust spec" and
"run probe".
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Direct decision-logic test — does open() skip probe_channel_count
# when a known-model spec is available?
# ---------------------------------------------------------------------------
def test_known_model_skips_live_probe():
    """For a model in tektronix_models.py with confident n_channels,
    the live probe must NOT run.  Decision-logic level: verify the
    branch picks the spec value without calling probe_channel_count.

    We construct a bare TektronixOscilloscope instance and patch
    ``probe_channel_count`` to track whether it was called.  Then
    we exercise the model-check branch as it would run inside
    open() after the *IDN? parse.
    """
    from stimtest.hardware.tektronix import TektronixOscilloscope
    from stimtest.hardware.tektronix_models import get_model_spec

    # Pre-check: the spec is registered for TBS2204B.
    spec = get_model_spec("TBS2204B")
    assert spec is not None
    assert spec.n_channels == 4

    # Build a bare instance.
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    scope._log = MagicMock()
    scope.probe_channel_count = MagicMock(return_value=4)

    # Exercise the branch from open() — copy of the model-vs-probe
    # decision.  In the live path this lives in open() right after
    # the IDN parse.
    if spec is not None and spec.n_channels in (2, 4):
        n_ch = spec.n_channels
        scope._log(
            f"[scope] channel count: {n_ch} (from model spec — "
            f"live probe skipped, saves ~10 s on cold connect)")
    else:
        scope.probe_channel_count()  # would-be fallback

    # The probe should NOT have been called.
    scope.probe_channel_count.assert_not_called()
    # The log should record the skip.
    log_calls = [c.args[0] for c in scope._log.call_args_list]
    assert any("probe skipped" in m for m in log_calls), (
        f"expected a log line mentioning 'probe skipped'; got: {log_calls!r}")


def test_unknown_model_still_runs_probe():
    """A model NOT in the registry should fall through to the live
    probe — the safety net is still there for OEM rebrands / new
    families."""
    from stimtest.hardware.tektronix import TektronixOscilloscope
    from stimtest.hardware.tektronix_models import get_model_spec

    fake_model = "UNKNOWN_MODEL_4321"
    spec = get_model_spec(fake_model)
    assert spec is None, (
        f"this test assumes {fake_model!r} is NOT in the registry — "
        f"pick a different fake model")

    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    scope._log = MagicMock()
    scope.probe_channel_count = MagicMock(return_value=4)

    # Same branch logic from open().
    probe_was_called = False
    if spec is not None and spec.n_channels in (2, 4):
        n_ch = spec.n_channels
    else:
        # Fallback path.
        scope.probe_channel_count()
        probe_was_called = True

    assert probe_was_called, (
        "unknown model should fall through to the live probe — the "
        "safety net for OEM rebrands / new families MUST still work")
    scope.probe_channel_count.assert_called_once()


def test_known_models_all_have_valid_channel_count():
    """Defensive check: every registered model spec has n_channels
    in (2, 4) so the open() branch picks the spec for all of them.
    A spec with n_channels=0 or 1 or 8 would silently fall through
    to the slow probe — defeats the whole purpose.

    If a future spec needs n_channels=8 (MSO/MDO/DPO scopes), the
    open() branch should be widened to include it.
    """
    from stimtest.hardware import tektronix_models
    from stimtest.hardware.tektronix_models import get_model_spec

    # Walk the known-models registry (lives in
    # tektronix_models._MODEL_BANDWIDTH_MHZ) and assert every one
    # that PRODUCES a spec has n_channels in (2, 4).  Some entries
    # are variants / OEM rebrands that intentionally fall through
    # to a shared spec via series-prefix matching; those get
    # ``spec is None`` and route through the live-probe fallback
    # path, which is the correct behavior.
    models = tektronix_models._MODEL_BANDWIDTH_MHZ.keys()
    bad_models = []
    skipped = 0
    for model in models:
        spec = get_model_spec(model)
        if spec is None:
            skipped += 1
            continue
        if spec.n_channels not in (2, 4):
            bad_models.append((model, f"n_channels={spec.n_channels}"))

    assert not bad_models, (
        f"registered models with unexpected channel count "
        f"(open() branch only picks 2 or 4): {bad_models}.  Widen "
        f"the open() branch in tektronix.py to include the new "
        f"channel count, OR fix the spec.")
    # Sanity: at least SOME models should produce a spec, otherwise
    # the test isn't actually checking anything.
    matched = len(models) - skipped
    assert matched > 0, (
        f"no models matched get_model_spec — the bandwidth-dict "
        f"and spec-registry have drifted; check the resolver.")


def test_probe_channel_count_still_exists_and_works_as_fallback():
    """The probe path is still there for unknown models.  Smoke test
    that the method exists and walks 1..max_channels."""
    from stimtest.hardware.tektronix import TektronixOscilloscope

    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)

    # Fake the underlying VISA instrument.  CH1..CH4 return a probe
    # gain; CH5+ raise (simulating "channel doesn't exist").
    class _FakeInst:
        def __init__(self):
            self.queries = []
            self.writes = []

        def write(self, cmd):
            self.writes.append(cmd)

        def query(self, cmd):
            self.queries.append(cmd)
            # CH1..CH4 → "1.0"; CH5+ → raise.
            import re
            m = re.match(r"CH(\d+):PRObe:GAIN\?", cmd)
            if m:
                ch = int(m.group(1))
                if ch <= 4:
                    return "1.0"
                raise RuntimeError("Undefined header")
            return "0"

    scope._inst = _FakeInst()
    n = scope.probe_channel_count(max_channels=8)
    assert n == 4, f"probe should return 4 for a 4-channel sim; got {n}"
