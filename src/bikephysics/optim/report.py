"""optim/report.py -- convergence history, optimal-vs-baseline overlay,
active-constraint report, and limit-margin trace of an `OptimResult`
(architecture.md §8, T8 item 6).

The figure side reuses `simplified.plots.plot_trajectories` wholesale
(optim is one of the two packages, alongside `cli`, allowed to import a
tier module -- architecture.md §4) rather than re-implementing a second
panel set: that function already draws position, velocity, acceleration,
normal loads, traction vs. the friction-cone bound, chain tension, every
`core.limits` margin, and the power split by channel, overlaid across
however many `Trajectory`s it is given -- exactly "optimal control vs. a
baseline" and "limit-margin trace of the optimum" at once.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from matplotlib.figure import Figure

from bikephysics.core.plotting import save_figure, style_axis
from bikephysics.core.results import Trajectory
from bikephysics.optim.problem import ResolvedProblem
from bikephysics.optim.solvers.base import OptimResult
from bikephysics.simplified.plots import plot_trajectories  # noqa: TID251 -- optim is exempt, S4

_FEASIBILITY_TOL = 1e-6


def convergence_history(result: OptimResult) -> list[dict[str, float]]:
    """`OptimResult.history` as a plain list -- empty for closed-form
    solvers (`AnalyticSimplified`), which have nothing to converge."""
    return list(result.history)


def plot_convergence(result: OptimResult) -> Figure | None:
    """A single-panel objective-vs-iteration figure, or `None` when
    `result.history` is empty."""
    if not result.history:
        return None
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    values = [h.get("objective", float("nan")) for h in result.history]
    ax.plot(range(len(values)), values, marker="o")
    style_axis(ax, ylabel="objective", xlabel="iteration")
    fig.tight_layout()
    return fig


def active_constraints_table(result: OptimResult) -> list[tuple[str, float, bool]]:
    """`(label, max(g), active)` rows from `result.active_constraints`;
    `active` means `max(g) >= -tol` (binding or violated) at solver
    tolerance, matching `optim.solvers.scipy_local`/`scipy_global`'s own
    feasibility tolerance."""
    return [
        (label, value, value >= -_FEASIBILITY_TOL)
        for label, value in result.active_constraints.items()
    ]


def limit_margin_trace(traj: Trajectory) -> dict[str, list[float]]:
    """`{margin_name: [values over time]}` plus `t`, for every
    `core.limits` margin on `traj`."""
    return {
        "t": traj.t.tolist(),
        "traction_rear": traj.margins.traction_rear.tolist(),
        "traction_front": traj.margins.traction_front.tolist(),
        "wheelie": traj.margins.wheelie.tolist(),
        "stoppie": traj.margins.stoppie.tolist(),
        "freewheel": traj.margins.freewheel.tolist(),
    }


def plot_optimal_vs_baseline(
    result: OptimResult, baseline: Trajectory | None = None, *, title: str | None = None
) -> Figure:
    """module docstring: the optimum's `Trajectory`, optionally overlaid
    against a baseline run, through `simplified.plots.plot_trajectories`'
    full panel set."""
    if baseline is None:
        return plot_trajectories([result.trajectory], ["optimal"], title=title)
    return plot_trajectories([result.trajectory, baseline], ["optimal", "baseline"], title=title)


def save_optimal_vs_baseline_figure(
    result: OptimResult,
    path: str | Path,
    baseline: Trajectory | None = None,
    *,
    title: str | None = None,
) -> Path:
    fig = plot_optimal_vs_baseline(result, baseline, title=title)
    return save_figure(fig, path)


def format_text_report(problem: ResolvedProblem, result: OptimResult) -> str:
    """A human-readable multi-section summary: solver outcome, the
    optimal control (via `parametrization.describe(x_opt)`), and the
    active-constraint table -- `cli/optim_cmds.py`'s non-`--json` output."""
    lines = [
        f"problem: {problem.name}",
        f"solver: {result.solver_name}  success={result.success}  status={result.status}",
        f"objective_value: {result.objective_value:.6g}",
        f"wall_time_s: {result.wall_time_s:.3f}  n_evaluations: {result.n_evaluations}",
        f"message: {result.message}",
        "",
        "optimal control:",
    ]
    description: dict[str, Any] = problem.parametrization.describe(result.x_opt)
    for key, value in description.items():
        lines.append(f"  {key} = {value}")
    lines.append("")
    lines.append("active constraints (max(g), <=0 feasible):")
    if not result.active_constraints:
        lines.append("  (none)")
    for label, value, active in active_constraints_table(result):
        marker = "ACTIVE" if active else "slack"
        lines.append(f"  {label}: {value:.6g}  [{marker}]")
    return "\n".join(lines)
