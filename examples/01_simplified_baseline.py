"""examples/01_simplified_baseline.py -- baseline Simplified-tier run.

Replaces the deleted `main.py`'s `baseline_run`/`steady_state_check`
functions (task.md T4 step 5), ported onto the current `simplified/{model,
analysis,simulate,plots}.py` surface. Independently runnable:

    uv run python examples/01_simplified_baseline.py

Runs a constant-crank-torque effort on the `road_bike` preset, prints
`M_eq`, the simulated terminal speed against `analysis.steady_state_speed`'s
closed-form prediction, and the energy-balance audit's closure error, then
saves the full panel figure.
"""

from __future__ import annotations

from pathlib import Path

from bikephysics.core.params import BIKE_PRESET_DIR, bike_spec_from_dict, read_toml
from bikephysics.core.pedals import ConstantCrankTorque
from bikephysics.simplified import analysis, simulate
from bikephysics.simplified.plots import save_trajectory_figure
from bikephysics.validation import energy

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "runs" / "examples"


def baseline_run() -> None:
    """Simulate a constant 40 N*m crank-torque effort from a standing
    start and compare the terminal speed against the closed-form
    steady-state balance (`simplified/analysis.py`)."""
    spec = bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))
    tau = 40.0
    pedal = ConstantCrankTorque(tau=tau, r_1_pedal=spec.frame.r_1_pedal)

    traj = simulate.simulate(spec, pedal, t_span=(0.0, 300.0), n_output=3000)

    p_each = tau / (2.0 * spec.frame.r_1_pedal)
    pedal_force_equiv = (
        (spec.frame.r_1_pedal * spec.drivetrain.r_2a_cg)
        / (spec.drivetrain.r_3b_cr * spec.rear.r_2a)
        * (2.0 * p_each)
    )
    v_analytic = analysis.steady_state_speed(spec, pedal_force_equiv, 0.0, input_kind="force")

    print(f"M_eq = {spec.m_eq:.4f} kg (effective mass)")
    print(
        f"Simulated terminal speed:  v(T_END) = {traj.v[-1]:.4f} m/s "
        f"= {traj.v[-1] * 3.6:.2f} km/h"
    )
    print(
        f"Analytic steady-state speed: v* = {v_analytic:.4f} m/s "
        f"(diff = {abs(traj.v[-1] - v_analytic):.2e} m/s)"
    )

    audit = energy.audit(traj)
    print(f"Energy-balance closure error: {audit.closure_error_fraction * 100.0:.4f} %")

    out_path = save_trajectory_figure(
        traj, OUTPUT_DIR / "01_baseline.png", title="Baseline run (constant 40 N*m crank torque)"
    )
    print(f"Figure saved to {out_path}")


if __name__ == "__main__":
    baseline_run()
