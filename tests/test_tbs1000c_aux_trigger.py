"""TBS1000C external trigger via the front-panel "Aux In" (SCPI source AUX).

The TBS1000C user manual (§"Trigger on an external signal using the Aux
input") selects the ``AUX`` source for Edge + Pulse-Width triggers — so unlike
the codebase's old ``has_ext_trigger = False``, the TBS1000C HAS an external
trigger input.  Enabling it lets a 2-channel TBS1072C trigger on the Plexon
digital sync via Aux In (clean, no sacrificed channel) instead of the flaky
I_mon edge (gotchas #160/#162).

The external-trigger BNC is named differently per dialect — "AUX" on modern
(TBS1000C / TBS2000), "EXT" on legacy — so ``set_trigger`` writes the dialect's
``ext_trigger_scpi_source`` while keeping the LOGICAL source "EXT" for the
driver's EXT-vs-channel logic.
"""
from __future__ import annotations

import types


# ---------------------------------------------------------------------------
# Spec DB
# ---------------------------------------------------------------------------
def test_tbs1000c_has_ext_trigger_via_aux():
    from stimtest.hardware.tektronix_models import get_series_spec
    spec = get_series_spec("TBS1202C")
    assert spec is not None
    assert spec.has_ext_trigger is True
    assert spec.commands.ext_trigger_scpi_source == "AUX"


def test_all_tbs1000c_models_match():
    from stimtest.hardware.tektronix_models import get_series_spec
    for m in ("TBS1052C", "TBS1072C", "TBS1102C", "TBS1152C", "TBS1202C"):
        spec = get_series_spec(m)
        assert spec is not None and spec.has_ext_trigger is True, m


def test_modern_legacy_ext_scpi_names():
    from stimtest.hardware.tektronix_models import MODERN_CMDS, LEGACY_CMDS
    assert MODERN_CMDS.ext_trigger_scpi_source == "AUX"
    assert LEGACY_CMDS.ext_trigger_scpi_source == "EXT"


# ---------------------------------------------------------------------------
# set_trigger writes the dialect's external-trigger source name
# ---------------------------------------------------------------------------
def _mock_scope(cmds, readback="AUX"):
    from stimtest.hardware.tektronix import TektronixOscilloscope
    s = TektronixOscilloscope.__new__(TektronixOscilloscope)
    s._cmds = cmds
    s.info = types.SimpleNamespace(n_channels=2)
    writes = []
    s._w = lambda c: writes.append(c)
    s._q = lambda c: readback
    s._log = lambda *a, **k: None
    s._trig_pulse_source = None
    return s, writes


def test_modern_ext_trigger_writes_aux():
    from stimtest.hardware.tektronix_models import MODERN_CMDS
    s, writes = _mock_scope(MODERN_CMDS)
    s.set_trigger(source="EXT", level_v=1.0, slope="RISE", mode="NORMAL")
    src_writes = [w for w in writes
                  if w.startswith(MODERN_CMDS.trig_edge_source)]
    assert src_writes, writes
    assert any("AUX" in w for w in src_writes)
    assert not any(w.strip().endswith("EXT") for w in src_writes)
    # The LOGICAL source stays "EXT" so the driver's EXT-vs-channel logic
    # (level-skip, digital detection) is unaffected.
    assert s._expected_trigger_source == "EXT"
    assert s._expected_trigger_is_digital is True


def test_legacy_ext_trigger_writes_ext():
    from stimtest.hardware.tektronix_models import LEGACY_CMDS
    s, writes = _mock_scope(LEGACY_CMDS, readback="EXT")
    s.set_trigger(source="EXT", level_v=1.0, slope="RISE", mode="NORMAL")
    src_writes = [w for w in writes
                  if w.startswith(LEGACY_CMDS.trig_edge_source)]
    assert any(w.strip().endswith("EXT") for w in src_writes), src_writes
    assert not any("AUX" in w for w in src_writes)


def test_channel_trigger_unaffected_by_translation():
    from stimtest.hardware.tektronix_models import MODERN_CMDS
    s, writes = _mock_scope(MODERN_CMDS, readback="CH2")
    # A channel source must be written verbatim (no EXT/AUX translation).
    s.apply_channel_defaults = lambda *a, **k: None
    s.set_channel_bandwidth_full = lambda *a, **k: None
    s.set_trigger(source="CH2", level_v=0.05, slope="FALL", mode="NORMAL")
    src_writes = [w for w in writes
                  if w.startswith(MODERN_CMDS.trig_edge_source)]
    assert any(w.strip().endswith("CH2") for w in src_writes)
    assert not any("AUX" in w for w in src_writes)


def test_ext_trigger_level_write_is_skipped():
    from stimtest.hardware.tektronix_models import MODERN_CMDS
    s, writes = _mock_scope(MODERN_CMDS)
    s._expected_trigger_source = "EXT"   # digital sync owns the level
    s.set_trigger_level(1.23)
    assert not any(MODERN_CMDS.trig_level in w for w in writes)
