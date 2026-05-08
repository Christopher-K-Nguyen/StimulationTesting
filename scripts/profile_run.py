#!/usr/bin/env python
"""Profile a representative VT sweep + XLSX export to find hot spots."""
from __future__ import annotations

import cProfile
import io
import pstats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import (
    SimulatedOscilloscope, SimulatedStimulator)
from stimtest.persistence import save_session_xlsx
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


def workload():
    array = ElectrodeArray.utah_4x4()
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1, rate_hz=50.0)
    test = TestParameters(experiment="VT", pattern=pattern,
                          configuration=Configuration.bipolar(9, 5),
                          array=array, duration_s=2.0)
    session = Session(notebook="prof", subject="sim", test=test)
    stim = SimulatedStimulator(); stim.open()
    scope = SimulatedOscilloscope(); scope.open()
    runner = VoltageTransientExperiment(
        session, stim, scope,
        ramp=RampPolicy(starting_ua=5.0, coarse_step_ua=5.0, max_ua=200.0,
                        settle_pulses=1),
    )
    runner.run()
    save_session_xlsx(session, Path("data") / "prof_export.xlsx")
    stim.close(); scope.close()
    return session


def main() -> int:
    pr = cProfile.Profile()
    pr.enable()
    session = workload()
    pr.disable()

    print(f"Captured {session.total_captures} captures")
    s = io.StringIO()
    ps = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    ps.print_stats(30)
    print(s.getvalue())

    print("\n--- Sorted by tottime (self time, not children) ---\n")
    s2 = io.StringIO()
    pstats.Stats(pr, stream=s2).sort_stats("tottime").print_stats(20)
    print(s2.getvalue())
    return 0


if __name__ == "__main__":
    sys.exit(main())
