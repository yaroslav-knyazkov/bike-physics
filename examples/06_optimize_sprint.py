"""examples/06_optimize_sprint.py -- optimize a 1 km sprint on the
Simplified model (architecture.md S8, task.md T8 "DELIVERABLE 3").
Independently runnable:

    uv run python examples/06_optimize_sprint.py

Solves the shipped `min_time_1km` problem deck (a flat-road, 20-interval
piecewise-constant pedal-force schedule, bounded by a power ceiling, a
pedal-force bound, and a traction margin -- architecture.md S8.5's own
worked example) with `ScipyLocal`/SLSQP, then compares the optimum against
a constant-force baseline of equal total mechanical energy, mirroring the
ticket's own acceptance test: "on `min_time_1km` the optimizer's answer
beats a constant-force baseline of equal total energy."
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from bikephysics.core.pedals import ConstantCrankTorque
from bikephysics.optim.forward import ControlInputs
from bikephysics.optim.problem import load_problem, resolve_problem, resolve_problem_path
from bikephysics.optim.report import format_text_report, save_optimal_vs_baseline_figure
from bikephysics.optim.solvers.scipy_local import ScipyLocal

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "runs" / "examples"


def optimize_sprint() -> None:
    problem = resolve_problem(load_problem(resolve_problem_path("min_time_1km")))

    solver = ScipyLocal(method="SLSQP", multistart=4, seed=0)
    result = solver.solve(problem)
    print(format_text_report(problem, result))

    # Equal-total-energy constant-force baseline: integrate the optimum's
    # mechanical energy, then size a single constant pedal torque that
    # delivers the same total energy over the optimum's own finish time.
    optimal_energy = float(np.trapezoid(result.trajectory.power_pedal, result.trajectory.t))
    t_final = float(result.trajectory.t[-1])
    mean_power = optimal_energy / t_final if t_final > 0 else 0.0
    # mean_power = gear_factor * tau / r_1_pedal * mean(v) (model.accel's
    # pedal term, time-averaged) -- solved for tau against the optimum's
    # own mean speed so both runs spend the same mechanical energy.
    mean_speed = float(result.trajectory.x[-1]) / t_final if t_final > 0 else 0.0
    gear_factor = (problem.spec.frame.r_1_pedal * problem.spec.drivetrain.r_2a_cg) / (
        problem.spec.drivetrain.r_3b_cr * problem.spec.rear.r_2a
    )
    baseline_force = mean_power / (gear_factor * mean_speed) if mean_speed > 0 else 0.0
    # tau = baseline_force * r_1_pedal (not *2): ConstantCrankTorque splits
    # tau evenly between both pedals, so P_D + P_F = tau / r_1_pedal already
    # equals baseline_force -- an extra factor of 2 here would silently
    # double the baseline's energy relative to the optimum's.
    baseline_pedal = ConstantCrankTorque(
        tau=baseline_force * problem.spec.frame.r_1_pedal, r_1_pedal=problem.spec.frame.r_1_pedal
    )
    baseline_traj = problem.forward_model.rollout(
        ControlInputs(pedal=baseline_pedal), problem.horizon
    )
    baseline_energy = float(np.trapezoid(baseline_traj.power_pedal, baseline_traj.t))

    print()
    print(
        f"optimal time to 1 km:   {result.objective_value:.3f} s  (energy {optimal_energy:.1f} J)"
    )
    print(f"baseline time to 1 km:  {baseline_traj.t[-1]:.3f} s  (energy {baseline_energy:.1f} J)")

    out_path = save_optimal_vs_baseline_figure(
        result, OUTPUT_DIR / "06_optimize_sprint.png", baseline_traj, title="min_time_1km"
    )
    print(f"Figure saved to {out_path}")


if __name__ == "__main__":
    optimize_sprint()
