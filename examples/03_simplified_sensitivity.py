"""examples/03_simplified_sensitivity.py -- elasticities, gradient limits,
size elasticity, and climb energy.

Replaces the deleted `main.py`'s `compare_rolling_resistance` example
(task.md T4 step 5), repointed at `simplified/analysis.py`'s "Sensitivity"
machinery. Independently runnable:

    uv run python examples/03_simplified_sensitivity.py

Prints the elasticity triple `(E_M, E_k, E_P)` with its exact
`E_M+E_k+E_P=0` identity check, the dominance ratio against the two
asymptotic regimes, a worked size-elasticity answer ("is there a best
rider weight?"), and the climb-energy-efficiency trend across grades --
then sweeps the climbing grade and saves an overlay figure of the
resulting steady-state-speed runs (the multi-run overlay capability of
`simplified/plots.py`).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from bikephysics.core.params import BIKE_PRESET_DIR, bike_spec_from_dict, read_toml
from bikephysics.core.pedals import ConstantCrankTorque
from bikephysics.core.plotting import save_figure
from bikephysics.core.road import ConstantGrade
from bikephysics.simplified import analysis, simulate
from bikephysics.simplified.plots import plot_trajectories

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "runs" / "examples"


def sensitivity_report() -> None:
    spec = bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))
    v, theta, power = 8.0, 0.03, 200.0

    e = analysis.elasticities(spec, v, theta, power)
    identity = e.E_M + e.E_k + e.E_P
    print(f"Elasticities at v={v} m/s, grade={math.tan(theta):.2%}, P={power} W:")
    print(f"  E_M = {e.E_M:.6f}, E_k = {e.E_k:.6f}, E_P = {e.E_P:.6f}")
    print(f"  identity E_M+E_k+E_P = {identity:.3e} (exact, Euler's theorem on G)")

    gl = analysis.gradient_limits(spec, v, theta)
    print(f"\nDominance ratio M*g*sin(theta)/(k*v^2) = {gl.dominance_ratio:.4f}")
    print(f"  flat, aero-dominated limit  -> {gl.flat_limit}")
    print(f"  steep, gravity-dominated limit -> {gl.steep_limit}")

    # "Is there a best rider weight?" -- a=0.75 (physiological, <1),
    # b=2/3 (geometric similarity), per forward_motion.tex's own "Is there
    # a best rider weight?" subparagraph.
    size_e = analysis.size_elasticity(spec, v, theta, power, a=0.75, b=2.0 / 3.0)
    print(f"\nSize elasticity dln(v)/dln(M) (a=0.75, b=2/3): {size_e:.6f}")
    print("  negative => a lighter rider is faster at this grade/power")

    thetas = np.array([math.atan(g) for g in (0.02, 0.05, 0.08, 0.12)])
    energies = analysis.climb_energy(spec, power, delta_h=200.0, thetas=thetas)
    print("\nClimb energy for a fixed 200 m height gain (steeper is always cheaper):")
    for theta_i, energy_i in zip(thetas, energies, strict=True):
        print(f"  grade={math.tan(theta_i):.2%}  E={energy_i:.1f} J")


def steady_speed_grade_sweep_overlay() -> None:
    """Simulate the same constant effort on three grades and overlay the
    resulting runs (the multi-run overlay capability of `simplified/
    plots.py`'s `plot_trajectories`)."""
    spec = bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))
    pedal = ConstantCrankTorque(tau=35.0, r_1_pedal=spec.frame.r_1_pedal)

    grades = (0.0, 0.03, 0.06)
    trajectories = []
    labels = []
    for grade in grades:
        road = ConstantGrade(theta=math.atan(grade))
        traj = simulate.simulate(spec, pedal, road=road, t_span=(0.0, 200.0), n_output=1000)
        trajectories.append(traj)
        labels.append(f"grade={grade:.0%}")
        print(f"grade={grade:.0%}: terminal v = {traj.v[-1]:.4f} m/s")

    fig = plot_trajectories(trajectories, labels, title="Constant effort across grades")
    out_path = save_figure(fig, OUTPUT_DIR / "03_sensitivity_grade_sweep.png")
    print(f"Figure saved to {out_path}")


if __name__ == "__main__":
    sensitivity_report()
    steady_speed_grade_sweep_overlay()
