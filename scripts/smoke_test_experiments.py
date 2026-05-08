#!/usr/bin/env python
"""Run all four experiment runners briefly against the simulator to verify
they execute end-to-end without errors. Each is given a short duration so
the smoke test finishes in a few seconds.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.long_pulsing import (
    LongPulsingExperiment, LongPulsingPolicy)
from stimtest.experiments.progressive_stress import (
    ProgressiveStressExperiment, StressPolicy)
from stimtest.experiments.short_pulsing import (
    ShortPulsingExperiment, ShortPulsingPolicy)
from stimtest.experiments.voltage_transient import (
    RampPolicy, VoltageTransientExperiment)
from stimtest.hardware.simulator import (
    SimulatedOscilloscope, SimulatedStimulator)
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


def _new_session(name: str, expt_code: str) -> Session:
    array = ElectrodeArray.utah_4x4()
    cfg = Configuration.bipolar(9, 5)
    pattern = PulsePattern.biphasic(amplitude_ua=10.0, polarity=-1, rate_hz=50.0)
    test = TestParameters(
        experiment=expt_code, pattern=pattern, configuration=cfg, array=array,
        duration_s=2.0,
    )
    return Session(notebook="smoke", subject=name, test=test)


def _open_hw():
    s = SimulatedStimulator(); s.open()
    o = SimulatedOscilloscope(); o.open()
    return s, o


def main() -> int:
    print("=" * 50)
    print("VT (Voltage Transient)")
    print("=" * 50)
    s = _new_session("vt", "VT")
    stim, scope = _open_hw()
    runner = VoltageTransientExperiment(
        s, stim, scope,
        ramp=RampPolicy(starting_ua=5.0, coarse_step_ua=20.0, max_ua=100.0,
                        settle_pulses=1),
    )
    t0 = time.time()
    result = runner.run()
    print(f"  runs={len(s.runs)}  captures={len(result.captures)}  "
          f"max Q_inj={result.max_q_inj:.3f} mC/cm²  "
          f"({time.time() - t0:.1f}s)")
    stim.close(); scope.close()

    print("\n" + "=" * 50)
    print("SP (Short-Term Pulsing)")
    print("=" * 50)
    s = _new_session("sp", "SP")
    stim, scope = _open_hw()
    runner = ShortPulsingExperiment(
        s, stim, scope, amplitude_ua=10.0,
        policy=ShortPulsingPolicy(duration_s=2.0, capture_interval_s=0.5),
    )
    t0 = time.time()
    result = runner.run()
    print(f"  runs={len(s.runs)}  captures={len(result.captures)}  "
          f"({time.time() - t0:.1f}s)")
    stim.close(); scope.close()

    print("\n" + "=" * 50)
    print("LP (Long-Term Pulsing with periodic characterization)")
    print("=" * 50)
    s = _new_session("lp", "LP")
    stim, scope = _open_hw()
    runner = LongPulsingExperiment(
        s, stim, scope, amplitude_ua=10.0,
        policy=LongPulsingPolicy(
            duration_s=4.0,
            capture_during_pulsing_every_s=1.0,
            characterize_every_s=2.0,
        ),
        ramp=RampPolicy(starting_ua=5.0, coarse_step_ua=20.0, max_ua=80.0,
                        settle_pulses=1),
    )
    t0 = time.time()
    result = runner.run()
    snap = sum(1 for c in result.captures if c.status.notes == "snapshot")
    char = sum(1 for c in result.captures if c.status.notes.startswith("char@"))
    print(f"  runs={len(s.runs)}  captures={len(result.captures)} "
          f"(snapshot={snap}, char={char})  ({time.time() - t0:.1f}s)")
    stim.close(); scope.close()

    print("\n" + "=" * 50)
    print("PS (Progressive Stress — stepped current to PlexStim limit)")
    print("=" * 50)
    s = _new_session("ps", "PS")
    stim, scope = _open_hw()
    runner = ProgressiveStressExperiment(
        s, stim, scope,
        policy=StressPolicy(starting_ua=5.0, step_ua=20.0, t_step_s=1.0,
                            max_ua=80.0, sampling_period_s=0.5,
                            stop_on_voltage_compliance=False),
    )
    t0 = time.time()
    result = runner.run()
    print(f"  runs={len(s.runs)}  captures={len(result.captures)}  "
          f"({time.time() - t0:.1f}s)")
    stim.close(); scope.close()

    print("\n" + "=" * 50)
    print("All four experiments completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
