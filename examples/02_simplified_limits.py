"""examples/02_simplified_limits.py -- dynamic limits (wheelie, stoppie,
traction, freewheel).

Replaces the deleted `main.py`'s force-profile comparison example
(task.md T4 step 5), repointed at the current limits machinery:
`simplified/analysis.py`'s closed-form `wheelie_accel`/`stoppie_accel`/
`traction_limit_accel`, and `simplified/simulate.py`'s margin-crossing
events (`Trajectory.meta["limit_crossings"]`). Independently runnable:

    uv run python examples/02_simplified_limits.py

Prints the closed-form critical-acceleration table, then runs a sudden
hard front-braking scenario that actually crosses several limits (rear
wheel traction, front wheel, and a stoppie) and reports when each one
fired, before saving a figure with the margins/traction/normal-load panels.
"""

from __future__ import annotations

import math
from pathlib import Path

from bikephysics.core.aero import AeroState
from bikephysics.core.brakes import BrakeAtDistance
from bikephysics.core.params import BIKE_PRESET_DIR, bike_spec_from_dict, read_toml
from bikephysics.core.pedals import ZeroPedal
from bikephysics.simplified import analysis, simulate
from bikephysics.simplified.plots import save_trajectory_figure

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "runs" / "examples"

ZERO_AERO = AeroState(A_1_x=0.0, A_1_z=0.0, A_1_m=0.0, beta_rel=0.0, Re=0.0, q=0.0)


def closed_form_limits() -> None:
    """The wheelie/stoppie/traction critical accelerations at a few
    grades, on flat ground, coasting (no aero drag -- see `ZERO_AERO`,
    matching `analysis.*_accel`'s own "forward motion, sigma=1" context)."""
    spec = bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))
    print("Closed-form critical accelerations (simplified/analysis.py):")
    print(f"{'grade':>8}  {'wheelie':>10}  {'stoppie':>10}  {'traction+':>10}  {'traction-':>10}")
    for grade in (0.0, 0.03, 0.08):
        theta = math.atan(grade)
        wheelie = analysis.wheelie_accel(spec, theta, ZERO_AERO)
        stoppie = analysis.stoppie_accel(spec, theta, ZERO_AERO)
        traction = analysis.traction_limit_accel(spec, theta, ZERO_AERO)
        print(
            f"{grade:8.3f}  {wheelie:10.4f}  {stoppie:10.4f}  "
            f"{traction.accel_limit:10.4f}  {traction.decel_limit:10.4f}"
        )


def hard_braking_crosses_limits() -> None:
    """Cruise at 12 m/s, then slam the front brake at x=5m: the rear
    wheel's traction margin and the stoppie margin both cross into
    violation (a real rear-wheel-lift/skid event, not just a closed-form
    check) -- `simplify.simulate`'s non-terminal margin events log exactly
    when."""
    spec = bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))
    brake = BrakeAtDistance(x_trigger=5.0, B_2a=0.0, B_2b=2000.0)
    traj = simulate.simulate(
        spec, ZeroPedal(), brake_input=brake, x0=0.0, v0=12.0, t_span=(0.0, 5.0), n_output=1000
    )

    print("\nHard front-brake-at-distance run: limit crossings (meta['limit_crossings']):")
    for name, times in traj.meta["limit_crossings"].items():
        if times:
            print(f"  {name}: crossed into violation at t = {[f'{t:.3f}' for t in times]} s")
        else:
            print(f"  {name}: never violated")

    out_path = save_trajectory_figure(traj, OUTPUT_DIR / "02_limits.png", title="Hard front brake")
    print(f"Figure saved to {out_path}")


if __name__ == "__main__":
    closed_form_limits()
    hard_braking_crosses_limits()
