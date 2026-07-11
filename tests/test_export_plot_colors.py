"""Exported capture figure uses the SAME trace colours as the live experiment
plot, and the title/subtitle are subject/channel (operator: "move the name as
title and channel as subtitle and have the same colors as the experiment
plot").
"""
from __future__ import annotations

import numpy as np


def test_export_trace_colours_match_experiment_plot():
    # gui.multichannel_scope imports plotting (not vice-versa), so plotting
    # keeps its own copy — this test is the guard against drift.
    from stimtest.plotting import _EXP_TRACE_COLORS
    from stimtest.gui.multichannel_scope import (
        TRACE_COLOURS, TRACE_VMON, TRACE_IMON, TRACE_EACT, TRACE_ERET)
    assert _EXP_TRACE_COLORS[0] == TRACE_COLOURS[TRACE_VMON]   # golden V_mon
    assert _EXP_TRACE_COLORS[1] == TRACE_COLOURS[TRACE_IMON]   # teal I_mon
    assert _EXP_TRACE_COLORS[2] == TRACE_COLOURS[TRACE_EACT]   # bluish-green
    assert _EXP_TRACE_COLORS[3] == TRACE_COLOURS[TRACE_ERET]   # vermillion


def test_subtitle_leads_with_channel_not_subject():
    # The subtitle now LEADS with the channel/config; the subject is the title.
    from stimtest.plotting import _capture_subtitle_mathtext
    from stimtest.session import Capture, ChannelRun, Configuration
    from stimtest.waveforms import PulsePattern
    pat = PulsePattern.biphasic(amplitude_ua=-50.0, rate_hz=100.0)
    run = ChannelRun(configuration=Configuration(id=0, active=1))
    cap = Capture(index=0, pattern=pat)   # default metrics (NaN) is enough
    txt = _capture_subtitle_mathtext(cap, run, "CH07")
    assert txt.startswith("CH07")                      # channel leads
    assert "capture 1" in txt
