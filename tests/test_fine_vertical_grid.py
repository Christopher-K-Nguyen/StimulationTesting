"""Fine-grained vertical-scale grid (port of MATLAB getWaveform3.m).

Operator corrected that the coarse-scaling values come from getWaveform.m
/ getWaveform3.m's FINE-grained list — NOT adjustScale.m's coarse 1-2-5
grid (which a prior pass mis-ported). The fine grid lets the fit loop fill
the screen far better ("minimize coarse scaling so the waveform best fits"),
and the TBS applies the fine V/div over SCPI as 3-sig-fig scientific
notation. These tests guard against a regression back to 1-2-5.
"""
from __future__ import annotations


def test_grid_is_fine_grained_not_1_2_5():
    from stimtest.hardware.tektronix import TektronixOscilloscope as T
    g = T._TEK_VERTICAL_GRID_VPD
    # The fine grid has ~100 entries, not 12 (the old 1-2-5 list).
    assert len(g) > 50, f"expected fine grid, got {len(g)} entries"
    # Strictly ascending, bounded 2 mV .. 5 V.
    assert all(g[i] < g[i + 1] for i in range(len(g) - 1))
    assert abs(g[0] - 0.002) < 1e-9
    assert abs(g[-1] - 5.0) < 1e-9
    # Intermediate fine values the 1-2-5 grid never had.
    for v in (0.0025, 0.075, 0.12, 0.3, 0.95):
        assert any(abs(x - v) < 1e-9 for x in g), f"{v} V/div missing"


def test_snap_picks_fine_value_for_best_fit():
    from stimtest.hardware.tektronix import TektronixOscilloscope as T
    g = T._TEK_VERTICAL_GRID_VPD
    # A ±73 mV trace lands on 75 mV/div (fine, ~97% fill), NOT 100 mV/div
    # (1-2-5, ~73% fill).
    assert abs(T._snap_to_grid(0.073, g, direction="ceil") - 0.075) < 1e-9
    # A ±310 mV trace lands on 350 mV/div, not 500 mV/div.
    assert abs(T._snap_to_grid(0.31, g, direction="ceil") - 0.35) < 1e-9


def test_set_channel_scale_writes_3sig_scientific():
    # set_channel_scale must format the V/div as 3-sig-fig SCIENTIFIC
    # notation so the TBS applies the fine value instead of quantizing to
    # a coarse 1-2-5 cell.
    from stimtest.hardware.tektronix import TektronixOscilloscope
    scope = TektronixOscilloscope.__new__(TektronixOscilloscope)
    sent = []
    scope._w = lambda cmd: sent.append(cmd)
    scope._adapt_state = {}
    scope._new_adapt_state = lambda: {"last_scale": None}
    scope._invalidate_preamble_cache = lambda ch: None

    scope.set_channel_scale("CH1", 0.075)
    assert sent == ["CH1:SCAle 7.50e-02"], sent
    # The cached last_scale is the exact float (not the string).
    assert abs(scope._adapt_state["CH1"]["last_scale"] - 0.075) < 1e-12
