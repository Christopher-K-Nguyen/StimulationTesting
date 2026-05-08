#!/usr/bin/env python
"""Run a small simulator sweep across several BP combinations, then export.

Used to exercise the new MATLAB-style XLSX layout (Instrumentation /
Parameters / Values / one sheet per channel) against a session that has more
than one run, so the Values sheet has multiple columns to populate.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import VoltageTransientExperiment
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.persistence import save_session_xlsx
from stimtest.session import ChannelRun, Session, TestParameters
from stimtest.waveforms import PulsePattern


def main() -> int:
    array = ElectrodeArray.utah_4x4()
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1, rate_hz=50.0)
    test_combinations = [
        Configuration.bipolar(9, 5),
        Configuration.bipolar(9, 13),
        Configuration.tripolar(9, 5, 13),
        Configuration.monopolar(7),
    ]

    test = TestParameters(
        experiment="VT", pattern=pattern,
        configuration=test_combinations[0], array=array,
    )
    session = Session(notebook="demo_multi", subject="sim", test=test)

    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()

    for cfg in test_combinations:
        session.test.configuration = cfg
        runner = VoltageTransientExperiment(session, stim, scope)
        runner.run()
        # The runner appends the new run to session.runs, so each iteration
        # adds another column to the Values sheet.

    out = Path("data") / "demo_multichannel.xlsx"
    save_session_xlsx(session, out)
    print(f"Wrote {out} with {len(session.runs)} ChannelRun sheets")
    print(f"Total captures across runs: {session.total_captures}")
    stim.close(); scope.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
