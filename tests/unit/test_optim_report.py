"""Tests for bikephysics.optim.report (architecture.md S8, T8 item 6)."""

from __future__ import annotations

from pathlib import Path

from bikephysics.optim.problem import OptimizationProblem, ResolvedProblem, resolve_problem
from bikephysics.optim.report import (
    active_constraints_table,
    convergence_history,
    format_text_report,
    limit_margin_trace,
    plot_convergence,
    save_optimal_vs_baseline_figure,
)
from bikephysics.optim.solvers.base import OptimResult
from bikephysics.optim.solvers.scipy_local import ScipyLocal

_FAST_PROBLEM: dict = {
    "name": "fast_report_test",
    "forward_model": "simplified",
    "bike": "road_bike",
    "road": "flat",
    "horizon": {"t_max": 60.0, "x_target": 200.0, "n_output": 50},
    "initial": {"v_0": 0.0},
    "parametrization": {"kind": "piecewise_constant_pedal", "n": 2},
    "objective": {"kind": "min_time_over_distance"},
    "constraints": [{"kind": "power_ceiling", "p_max": 300.0}],
    "solver": {"kind": "scipy_local", "method": "SLSQP", "multistart": 1},
}


def _solve() -> tuple[ResolvedProblem, OptimResult]:
    problem = resolve_problem(OptimizationProblem.model_validate(_FAST_PROBLEM))
    solver = ScipyLocal(method="SLSQP", multistart=1, seed=0, use_process_pool=False, maxiter=60)
    result = solver.solve(problem)
    return problem, result


def test_convergence_history_is_a_plain_list() -> None:
    _problem, result = _solve()
    history = convergence_history(result)
    assert isinstance(history, list)
    assert all("objective" in h for h in history)


def test_plot_convergence_returns_a_figure_when_history_exists() -> None:
    _problem, result = _solve()
    fig = plot_convergence(result)
    assert fig is not None


def test_active_constraints_table_marks_binding_vs_slack() -> None:
    _problem, result = _solve()
    rows = active_constraints_table(result)
    assert len(rows) == len(result.active_constraints)
    for _label, value, active in rows:
        assert active == (value >= -1e-6)


def test_limit_margin_trace_has_every_margin_name() -> None:
    _problem, result = _solve()
    trace = limit_margin_trace(result.trajectory)
    for key in ("t", "traction_rear", "traction_front", "wheelie", "stoppie", "freewheel"):
        assert key in trace
        assert len(trace[key]) == len(result.trajectory.t)


def test_format_text_report_includes_problem_name_and_constraints() -> None:
    problem, result = _solve()
    text = format_text_report(problem, result)
    assert problem.name in text
    assert "active constraints" in text
    assert "optimal control" in text


def test_save_optimal_vs_baseline_figure_writes_a_png(tmp_path: Path) -> None:
    problem, result = _solve()
    baseline_traj = problem.forward_model.rollout(
        problem.parametrization.build(problem.parametrization.initial_guess(problem.spec)),
        problem.horizon,
    )
    out_path = save_optimal_vs_baseline_figure(result, tmp_path / "compare.png", baseline_traj)
    assert out_path.is_file()


def test_plot_convergence_is_none_for_a_closed_form_result() -> None:
    from dataclasses import replace

    _problem, result = _solve()
    closed_form_result = replace(result, history=())
    assert plot_convergence(closed_form_result) is None
