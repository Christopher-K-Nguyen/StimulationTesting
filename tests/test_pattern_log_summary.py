"""The ``Setup: pattern = …`` log block is multi-line + tab-indented.

Operator: "separated lines (tabbed) for each phase, interphase delay, and
discharge delay".  ``MainWindow._describe_pattern`` returns a tabbed BODY
(the caller prepends the ``pattern =`` header): one ``\tPhase N: …`` line
per phase (with its shape), a ``\tInterphase delay: …`` / ``\tDischarge
delay: …`` line after each phase that has a following gap, and a
``\tRate: … pps`` line.  A zero delay is omitted.

Earlier layers of this feature (now folded in): the delays ARE shown at
all (they used to be missing), the rate is "pps" not "Hz", and there are
no arrows anywhere.

The method is pure (reads only the pattern), so these tests call it as an
unbound function with a stub ``self`` — no heavy ``MainWindow`` /
``ScopePlot`` construction (which can segfault on Qt teardown).
"""
from __future__ import annotations

import pytest

from stimtest.gui.main_window import MainWindow
from stimtest.waveforms import (
    Phase, PulsePattern,
    SHAPE_RECTANGULAR, SHAPE_SINUSOIDAL, SHAPE_HALFPIPE,
    SHAPE_EXP_DECAY, SHAPE_LINEAR_INCREASING,
)


_describe = MainWindow._describe_pattern


class _Stub:
    """Minimal stand-in for the bound ``self`` — ``_describe_pattern``
    reads nothing off the instance."""


def _d(pat) -> str:
    return _describe(_Stub(), pat)


def _lines(pat):
    return _d(pat).split("\n")


def test_biphasic_tabbed_lines_with_shape():
    pat = PulsePattern(
        phases=[Phase(-50.0, 200.0, 20.0, SHAPE_SINUSOIDAL),
                Phase(+50.0, 200.0, 20.0, SHAPE_SINUSOIDAL)],
        rate_hz=50.0,
    )
    assert _lines(pat) == [
        "\tPhase 1: -50.0 µA × 200 µs, sinusoidal",
        "\tInterphase delay: 20 µs",
        "\tPhase 2: +50.0 µA × 200 µs, sinusoidal",
        "\tDischarge delay: 20 µs",
        "\tRate: 50 pps",
    ]


def test_every_line_is_tab_indented():
    pat = PulsePattern(
        phases=[Phase(-50.0, 200.0, 20.0), Phase(+50.0, 200.0, 20.0)],
        rate_hz=50.0,
    )
    for ln in _lines(pat):
        assert ln.startswith("\t"), f"line not tab-indented: {ln!r}"


def test_shape_distinguishes_otherwise_identical_patterns():
    """Rectangular vs sinusoidal vs halfpipe used to print identically —
    the per-phase shape now separates them."""
    def _p(shape):
        return PulsePattern(
            phases=[Phase(-50.0, 200.0, 20.0, shape),
                    Phase(+50.0, 200.0, 20.0, shape)],
            rate_hz=50.0)
    rect = _d(_p(SHAPE_RECTANGULAR))
    sine = _d(_p(SHAPE_SINUSOIDAL))
    hp = _d(_p(SHAPE_HALFPIPE))
    assert rect != sine != hp and rect != hp
    assert "rectangular" in rect
    assert "sinusoidal" in sine
    assert "halfpipe" in hp


def test_shape_underscore_renders_as_hyphen():
    pat = PulsePattern(
        phases=[Phase(-100.0, 200.0, 20.0, SHAPE_RECTANGULAR),
                Phase(+50.0, 800.0, 20.0, SHAPE_LINEAR_INCREASING)],
        rate_hz=50.0,
    )
    out = _d(pat)
    assert "linear-increasing" in out   # underscore -> hyphen
    assert "linear_increasing" not in out


def test_exp_decay_shape_shown():
    pat = PulsePattern(
        phases=[Phase(-100.0, 200.0, 20.0, SHAPE_RECTANGULAR),
                Phase(+40.0, 500.0, 20.0, SHAPE_EXP_DECAY)],
        rate_hz=50.0,
    )
    assert "exp-decay" in _d(pat)


def test_zero_delays_omit_their_lines():
    pat = PulsePattern(
        phases=[Phase(-50.0, 200.0, 0.0), Phase(+50.0, 200.0, 0.0)],
        rate_hz=50.0,
    )
    out = _d(pat)
    assert "Interphase delay" not in out
    assert "Discharge delay" not in out
    # Still shows both phase lines + the rate line.
    assert _lines(pat) == [
        "\tPhase 1: -50.0 µA × 200 µs, rectangular",
        "\tPhase 2: +50.0 µA × 200 µs, rectangular",
        "\tRate: 50 pps",
    ]


def test_last_phase_delay_is_discharge_even_monophasic():
    pat = PulsePattern(phases=[Phase(-100.0, 300.0, 50.0)], rate_hz=100.0)
    out = _d(pat)
    assert "\tDischarge delay: 50 µs" in out
    assert "Interphase delay" not in out


def test_triphasic_two_interphase_one_discharge():
    pat = PulsePattern(
        phases=[
            Phase(-40.0, 150.0, 20.0),
            Phase(+60.0, 150.0, 20.0),
            Phase(-20.0, 150.0, 30.0),
        ],
        rate_hz=40.0,
    )
    out = _d(pat)
    assert out.count("Interphase delay") == 2
    assert out.count("Discharge delay") == 1
    assert _lines(pat)[0].startswith("\tPhase 1:")
    assert _lines(pat)[-1] == "\tRate: 40 pps"


def test_rate_is_pps_never_hz_and_no_arrows():
    pat = PulsePattern(
        phases=[Phase(-50.0, 200.0, 20.0), Phase(+50.0, 200.0, 20.0)],
        rate_hz=50.0,
    )
    out = _d(pat)
    assert "pps" in out
    assert "Hz" not in out
    assert "→" not in out


def test_none_pattern_is_safe():
    assert _d(None) == "\t(none)"
