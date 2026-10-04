"""cli/optim_cmds.py -- `bike opt solve|compare|baseline` (architecture.md
S9's `bike opt` block).

Registers its three commands directly onto `cli.app.opt_app` (imported for
its side effect by `cli.app`, mirroring `cli/simplified_cmds.py`'s own
wiring). `--problem` resolves a preset name under `optim.problem.
PROBLEM_PRESET_DIR` or a direct TOML path, exactly like `--config` resolves
a bike/road preset elsewhere in this CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import numpy as np
import typer

from bikephysics.cli.app import opt_app
from bikephysics.core.plotting import save_figure
from bikephysics.core.runio import build_run_summary, make_run_dir, write_summary, write_trajectory
from bikephysics.optim.problem import (
    ResolvedProblem,
    load_problem,
    resolve_problem,
    resolve_problem_path,
)
from bikephysics.optim.report import format_text_report, save_optimal_vs_baseline_figure
from bikephysics.optim.solvers.analytic import AnalyticSimplified
from bikephysics.optim.solvers.base import OptimResult, Solver
from bikephysics.optim.solvers.scipy_global import ScipyGlobal
from bikephysics.optim.solvers.scipy_local import ScipyLocal
from bikephysics.simplified.plots import plot_trajectories  # noqa: TID251 -- cli is exempt, S4

_SOLVER_NAMES: tuple[str, ...] = ("scipy-local", "scipy-global", "analytic")

_ProblemOption = Annotated[
    str, typer.Option("--problem", help="Problem preset name or path to a TOML problem deck.")
]
_MethodOption = Annotated[
    str | None, typer.Option("--method", help="Solver-specific method override.")
]
_MultistartOption = Annotated[
    int, typer.Option("--multistart", help="ScipyLocal multi-start count.")
]
_SeedOption = Annotated[int | None, typer.Option("--seed")]
_MaxiterOption = Annotated[int, typer.Option("--maxiter")]
_OutOption = Annotated[Path, typer.Option("--out", help="Run output directory.")]
_NoPlotOption = Annotated[
    bool, typer.Option("--no-plot", help="Skip writing figures for this run.")
]
_JsonOption = Annotated[
    bool, typer.Option("--json", help="Print a machine-readable summary to stdout.")
]


def _load_resolved_problem(problem: str) -> ResolvedProblem:
    try:
        path = resolve_problem_path(problem)
    except FileNotFoundError as exc:
        raise typer.BadParameter(str(exc)) from exc
    return resolve_problem(load_problem(path))


def _build_solver(
    name: str, *, method: str | None, multistart: int, seed: int | None, maxiter: int
) -> Solver:
    if name == "scipy-local":
        return ScipyLocal(
            method=method or "SLSQP",  # type: ignore[arg-type]
            multistart=multistart,
            seed=seed,
            maxiter=maxiter,
        )
    if name == "scipy-global":
        return ScipyGlobal(
            method=method or "differential_evolution",  # type: ignore[arg-type]
            seed=seed,
            maxiter=maxiter,
        )
    if name == "analytic":
        return AnalyticSimplified()
    raise typer.BadParameter(f"unknown solver {name!r}; expected one of {_SOLVER_NAMES}")


def _write_optim_run(
    out: Path,
    slug: str,
    command: str,
    result: OptimResult,
    resolved: ResolvedProblem,
    *,
    no_plot: bool,
) -> Path:
    run_dir = make_run_dir(out, slug)
    write_trajectory(run_dir, result.trajectory)
    if not no_plot:
        save_optimal_vs_baseline_figure(result, run_dir / "trajectory.png", title=run_dir.name)
    summary = build_run_summary(
        run_id=run_dir.name,
        command=command,
        wall_time_s=result.wall_time_s,
        solver={
            "name": result.solver_name,
            "status": result.status,
            "message": result.message,
            **resolved.solver_config,
        },
        metrics={
            "objective_value": result.objective_value,
            "n_evaluations": float(result.n_evaluations),
            "success": float(result.success),
        },
    )
    write_summary(run_dir, summary)
    (run_dir / "report.txt").write_text(format_text_report(resolved, result), encoding="utf-8")
    return run_dir


@opt_app.command("solve")
def solve_cmd(
    problem: _ProblemOption,
    solver: Annotated[
        str, typer.Option("--solver", help=f"One of {_SOLVER_NAMES}.")
    ] = "scipy-local",
    method: _MethodOption = None,
    multistart: _MultistartOption = 1,
    seed: _SeedOption = None,
    maxiter: _MaxiterOption = 200,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Solve one optimization problem with one solver and write its run
    directory (architecture.md S9, S8.4)."""
    resolved = _load_resolved_problem(problem)
    solver_obj = _build_solver(
        solver, method=method, multistart=multistart, seed=seed, maxiter=maxiter
    )
    result = solver_obj.solve(resolved)

    run_dir = _write_optim_run(
        out, "opt-solve", "bike opt solve", result, resolved, no_plot=no_plot
    )

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "run_dir": str(run_dir),
                    "objective_value": result.objective_value,
                    "success": result.success,
                    "wall_time_s": result.wall_time_s,
                }
            )
        )
    else:
        typer.echo(f"wrote {run_dir}")
        typer.echo(format_text_report(resolved, result))


@opt_app.command("baseline")
def baseline_cmd(
    problem: _ProblemOption,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Evaluate a non-optimized control -- `parametrization.
    initial_guess(spec)` rolled out as-is -- for reference against `bike
    opt solve`'s optimum (architecture.md S9)."""
    resolved = _load_resolved_problem(problem)
    x0 = np.asarray(resolved.parametrization.initial_guess(resolved.spec), dtype=float)
    controls = resolved.parametrization.build(x0)
    traj = resolved.forward_model.rollout(controls, resolved.horizon)
    objective_value = resolved.objective.value(traj)
    metrics = {**resolved.forward_model.metrics(traj), "objective_value": objective_value}

    run_dir = make_run_dir(out, "opt-baseline")
    write_trajectory(run_dir, traj)
    if not no_plot:
        fig = plot_trajectories([traj], ["baseline"], title=run_dir.name)
        save_figure(fig, run_dir / "trajectory.png")
    summary = build_run_summary(
        run_id=run_dir.name, command="bike opt baseline", wall_time_s=0.0, metrics=metrics
    )
    write_summary(run_dir, summary)

    if json_output:
        typer.echo(json.dumps({"run_dir": str(run_dir), **metrics}))
    else:
        typer.echo(f"wrote {run_dir}")
        for key, value in metrics.items():
            typer.echo(f"  {key} = {value:.6g}")


@opt_app.command("compare")
def compare_cmd(
    problem: _ProblemOption,
    solvers: Annotated[
        str,
        typer.Option(
            "--solvers", help="Comma-separated, e.g. scipy-local,scipy-global,analytic."
        ),
    ] = "scipy-local,scipy-global",
    method: _MethodOption = None,
    multistart: _MultistartOption = 1,
    seed: _SeedOption = None,
    maxiter: _MaxiterOption = 200,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Solve the same problem with several solvers and overlay the results
    (architecture.md S9: "overlays local vs. global vs. analytic")."""
    resolved = _load_resolved_problem(problem)
    names = [s.strip() for s in solvers.split(",") if s.strip()]
    results: dict[str, OptimResult] = {}
    for name in names:
        solver_obj = _build_solver(
            name, method=method, multistart=multistart, seed=seed, maxiter=maxiter
        )
        try:
            results[name] = solver_obj.solve(resolved)
        except Exception as exc:  # a solver legitimately rejecting a mismatched problem (e.g.
            # AnalyticSimplified on a non-oracle problem) must not abort the whole comparison.
            typer.echo(f"warning: solver {name!r} failed: {exc}", err=True)

    if not results:
        typer.echo("error: every requested solver failed", err=True)
        raise typer.Exit(code=1)

    run_dir = make_run_dir(out, "opt-compare")
    if not no_plot:
        fig = plot_trajectories(
            [r.trajectory for r in results.values()], list(results.keys()), title=run_dir.name
        )
        save_figure(fig, run_dir / "comparison.png")

    comparison = {
        name: {
            "objective_value": r.objective_value,
            "wall_time_s": r.wall_time_s,
            "success": r.success,
            "n_evaluations": r.n_evaluations,
        }
        for name, r in results.items()
    }
    (run_dir / "comparison.json").write_text(json.dumps(comparison, indent=2), encoding="utf-8")
    summary = build_run_summary(
        run_id=run_dir.name,
        command="bike opt compare",
        wall_time_s=sum(r.wall_time_s for r in results.values()),
        metrics={f"{name}_objective_value": r.objective_value for name, r in results.items()},
    )
    write_summary(run_dir, summary)

    if json_output:
        typer.echo(json.dumps({"run_dir": str(run_dir), **comparison}))
    else:
        typer.echo(f"wrote {run_dir}")
        for name, metrics_ in comparison.items():
            typer.echo(
                f"  {name}: objective={metrics_['objective_value']:.6g}  "
                f"wall_time={metrics_['wall_time_s']:.3f}s  success={metrics_['success']}"
            )
