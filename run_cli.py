#!/usr/bin/env python
"""Headless / scripted entry point.

Run a voltage-transient characterization sweep on a single active+return pair
against the simulator (or real hardware if available). Useful for testing,
batch jobs, and CI smoke checks.

Example:
    python run_cli.py --simulate --active 9 --returns 5 13 \\
                      --phase-width 200 --interphase 20 --rate 50 \\
                      --polarity cathodic --pattern biphasic
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from stimtest.electrode import Configuration, ElectrodeArray
from stimtest.experiments.voltage_transient import VoltageTransientExperiment
from stimtest.hardware.simulator import SimulatedOscilloscope, SimulatedStimulator
from stimtest.persistence import save_session_npz
from stimtest.session import Session, TestParameters
from stimtest.waveforms import PulsePattern


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--simulate", action="store_true", default=True)
    p.add_argument("--active", type=int, required=True, help="Active electrode (1-16)")
    p.add_argument("--returns", type=int, nargs="+", required=True, help="Return electrode(s)")
    p.add_argument("--phase-width", type=float, default=200.0, help="Phase width in µs")
    p.add_argument("--interphase", type=float, default=20.0, help="Interphase delay in µs")
    p.add_argument("--rate", type=float, default=50.0, help="Pulses per second")
    p.add_argument("--polarity", choices=["cathodic", "anodic"], default="cathodic")
    p.add_argument("--pattern", choices=["biphasic", "triphasic"], default="biphasic")
    p.add_argument("--out", type=Path, default=Path("data") / "vt_run.npz",
                   help="Path to .npz output (raw arrays + JSON metadata)")
    p.add_argument("--xlsx", type=Path, default=None,
                   help="Optional Gamry-DTA-style .xlsx output "
                        "(Sheet 1=Instrumentation, Sheet 2=Parameters, then one sheet per channel)")
    p.add_argument("--dta-dir", type=Path, default=None,
                   help="Optional directory for tab-delimited .DTA files "
                        "(one per channel, Gamry Echem Analyst format)")
    args = p.parse_args()

    array = ElectrodeArray.utah_4x4()
    config = Configuration.from_active_returns(args.active, args.returns)

    pattern = PulsePattern.rect(
        polarity=-1 if args.polarity == "cathodic" else +1,
        triphasic=(args.pattern == "triphasic"),
        phase_width_us=args.phase_width,
        interphase_us=args.interphase,
        rate_hz=args.rate,
    )

    test = TestParameters(
        experiment="VT", pattern=pattern, configuration=config, array=array,
    )
    session = Session(notebook="cli_run", subject="test", test=test)

    stim = SimulatedStimulator()
    scope = SimulatedOscilloscope()
    stim.open()
    scope.open()

    runner = VoltageTransientExperiment(session, stim, scope)
    result = runner.run()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_session_npz(session, args.out)
    if args.xlsx is not None:
        from stimtest.persistence import save_session_xlsx
        save_session_xlsx(session, args.xlsx)
        print(f"Excel export written to {args.xlsx}")
    if args.dta_dir is not None:
        from stimtest.persistence import save_session_dta
        paths = save_session_dta(session, args.dta_dir)
        print(f"Wrote {len(paths)} .DTA file(s) under {args.dta_dir}")
    print(f"Done. Captures: {len(result.captures)}  Max Q_inj: {result.max_q_inj:.3f} mC/cm²")
    print(f"Saved to {args.out}")

    stim.close()
    scope.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
