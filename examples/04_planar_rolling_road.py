"""examples/04_planar_rolling_road.py -- full 2D bike on a rolling-sine
road (task.md T7 item 11).

Builds the full 12-DOF/7-constraint planar bike (`planar/assembly.py`),
solves a consistent initial condition on `data/roads/rolling_sine.toml`
(`planar/kinematics.py`), simulates 30 s with the §7.5.4 freewheel driver
(`planar/drivetrain.py`), reports the energy-balance audit and wall time
against the ticket's own performance target (< 5 s for a 30 s run), and
saves the trajectory figure plus the D-16 animated replay.

    uv run python examples/04_planar_rolling_road.py
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from bikephysics.core.brakes import ConstantBrake
from bikephysics.core.params import BIKE_PRESET_DIR, bike_spec_from_dict, read_toml
from bikephysics.core.pedals import ConstantCrankTorque
from bikephysics.core.road import ROAD_PRESET_DIR, load_road
from bikephysics.planar.animate import build_animation, save_animation
from bikephysics.planar.assembly import build_bike, build_trajectory
from bikephysics.planar.drivetrain import simulate_with_freewheel
from bikephysics.planar.kinematics import solve_initial_conditions
from bikephysics.planar.plots import save_trajectory_figure
from bikephysics.validation import energy

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "runs" / "examples"


def rolling_road_run() -> None:
    """A moderate, steady pedaling effort on `rolling_sine.toml` -- chosen
    (task.md T7's own "Done when": a 30 s run completes in < 5 s
    wall-clock) to stay well inside the regularized contact model's
    stable operating range; `core.pedals.ConstantCrankTorque` avoids the
    large instantaneous force swings a periodic template (e.g.
    `ClippedSine`) can produce at high peak force, which this ticket's
    own testing found can drive the model to a genuine (not numerical)
    wheelie/traction-limit edge case on a hilly profile."""
    spec = bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))
    road = load_road(ROAD_PRESET_DIR / "rolling_sine.toml")
    pedal = ConstantCrankTorque(
        tau=2.0 * spec.frame.r_1_pedal * 60.0, r_1_pedal=spec.frame.r_1_pedal
    )
    brake = ConstantBrake()

    bike = build_bike(spec, road, pedal, brake)
    ic = solve_initial_conditions(bike, road, x_rear_0=0.0, v0=7.0)
    print(f"IC position residual: {ic.position_residual_norm:.3e}")
    print(f"IC velocity residual: {ic.velocity_residual_norm:.3e}")
    print(
        f"Static load split (lever-arm): rear={ic.static_load_lever_arm[0]:.1f} N, "
        f"front={ic.static_load_lever_arm[1]:.1f} N"
    )

    y0 = np.concatenate([ic.q0, ic.u0])
    t_wall_start = time.monotonic()
    result, modes = simulate_with_freewheel(
        bike.system, bike.drivetrain.chain, y0, (0.0, 30.0), n_output=600
    )
    wall_time_s = time.monotonic() - t_wall_start
    print(f"30 s run wall time: {wall_time_s:.3f} s (target: < 5 s)")

    traj = build_trajectory(bike, result)
    audit = energy.audit(traj)
    print(f"Final speed: {traj.v[-1]:.3f} m/s")
    print(
        f"Energy-balance closure error: {audit.closure_error_fraction * 100.0:.4f} % "
        "(target: < 1 %)"
    )
    print(f"Mode switches logged: {len(modes.t)}")
    print(f"Max constraint drift: |c|={result.drift.max_c:.2e}, |J u|={result.drift.max_Ju:.2e}")

    fig_path = save_trajectory_figure(
        traj, OUTPUT_DIR / "04_rolling_road.png", title="Planar bike on rolling_sine road"
    )
    print(f"Figure saved to {fig_path}")

    anim, fig = build_animation(bike, result.t, result.q, stride=4)
    anim_path = save_animation(anim, fig, OUTPUT_DIR / "04_rolling_road.gif", fps=20)
    print(f"Animation saved to {anim_path}")


if __name__ == "__main__":
    rolling_road_run()
