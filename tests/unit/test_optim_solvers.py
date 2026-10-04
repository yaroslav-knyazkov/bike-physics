"""Tests for bikephysics.optim.solvers.{base,scipy_local,scipy_global}
(architecture.md S8.4). Deliberately built on small, fast synthetic
problems (not the shipped `min_time_1km` deck's 20-variable, multi-start-8
configuration, which is a real-world/CLI scale, not a unit-test scale) so
the suite stays fast; `ScipyLocal`/`ScipyGlobal` run with
`use_process_pool=False` to avoid subprocess spawn overhead in tests.
"""

from __future__ import annotations

import numpy as np
import pytest

from bikephysics.core.pedals import ConstantCrankTorque
from bikephysics.optim.forward import ControlInputs
from bikephysics.optim.parametrization import gear_factor
from bikephysics.optim.problem import OptimizationProblem, ResolvedProblem, resolve_problem
from bikephysics.optim.solvers.base import (
    Evaluator,
    active_constraint_report,
    enumerate_gear_options,
    max_constraint_violation,
    reached_x_target,
)
from bikephysics.optim.solvers.scipy_global import ScipyGlobal
from bikephysics.optim.solvers.scipy_local import ScipyLocal

_FAST_PROBLEM: dict = {
    "name": "fast_min_time_test",
    "forward_model": "simplified",
    "bike": "road_bike",
    "road": "flat",
    "horizon": {"t_max": 60.0, "x_target": 200.0, "n_output": 100},
    "initial": {"v_0": 0.0},
    "parametrization": {"kind": "piecewise_constant_pedal", "n": 4},
    "objective": {"kind": "min_time_over_distance"},
    "constraints": [{"kind": "power_ceiling", "p_max": 300.0}],
    "solver": {"kind": "scipy_local", "method": "SLSQP", "multistart": 1},
}


def _fast_problem() -> ResolvedProblem:
    return resolve_problem(OptimizationProblem.model_validate(_FAST_PROBLEM))


def test_evaluator_caches_the_rollout_for_a_repeated_x() -> None:
    problem = _fast_problem()
    evaluator = Evaluator(problem=problem)
    x = problem.parametrization.initial_guess(problem.spec)
    evaluator.objective(x)
    evaluator.objective(x)
    assert evaluator.n_evaluations == 1  # same x, second call hits the cache
    evaluator.objective(x * 1.5)
    assert evaluator.n_evaluations == 2


def test_reached_x_target_is_none_without_a_horizon_target() -> None:
    cfg = dict(_FAST_PROBLEM)
    cfg["horizon"] = {"t_max": 60.0, "n_output": 50}
    cfg["objective"] = {"kind": "max_distance_in_time", "t_end": 60.0}
    problem = resolve_problem(OptimizationProblem.model_validate(cfg))
    traj = problem.forward_model.rollout(
        ControlInputs(pedal=ConstantCrankTorque(tau=10.0, r_1_pedal=problem.spec.frame.r_1_pedal)),
        problem.horizon,
    )
    assert reached_x_target(problem, traj) is None


def test_reached_x_target_true_when_the_rollout_arrives() -> None:
    problem = _fast_problem()
    traj = problem.forward_model.rollout(
        ControlInputs(pedal=ConstantCrankTorque(tau=200.0, r_1_pedal=problem.spec.frame.r_1_pedal)),
        problem.horizon,
    )
    assert reached_x_target(problem, traj) is True


def test_active_constraint_report_labels_every_constraint() -> None:
    problem = _fast_problem()
    traj = problem.forward_model.rollout(
        ControlInputs(pedal=ConstantCrankTorque(tau=10.0, r_1_pedal=problem.spec.frame.r_1_pedal)),
        problem.horizon,
    )
    report = active_constraint_report(problem, traj)
    assert set(report) == {"0_PowerCeiling"}
    assert max_constraint_violation(problem, traj) >= 0.0


def test_scipy_local_slsqp_stays_within_bounds() -> None:
    problem = _fast_problem()
    solver = ScipyLocal(method="SLSQP", multistart=1, seed=0, use_process_pool=False, maxiter=80)
    result = solver.solve(problem)
    bounds = problem.parametrization.bounds(problem.spec)
    for value, (lo, hi) in zip(result.x_opt, bounds, strict=True):
        assert lo - 1e-6 <= value <= hi + 1e-6


def test_scipy_local_respects_the_power_ceiling_constraint() -> None:
    problem = _fast_problem()
    solver = ScipyLocal(method="SLSQP", multistart=1, seed=0, use_process_pool=False, maxiter=80)
    result = solver.solve(problem)
    assert max_constraint_violation(problem, result.trajectory) <= 1e-3


def test_scipy_global_runs_and_returns_a_result() -> None:
    # A smaller/cheaper problem than _fast_problem(): differential_evolution's
    # population scales with n_vars, so this keeps the test's wall time
    # reasonable while still exercising ScipyGlobal's code path.
    cfg = dict(_FAST_PROBLEM)
    cfg["parametrization"] = {"kind": "piecewise_constant_pedal", "n": 2}
    cfg["horizon"] = {"t_max": 60.0, "x_target": 200.0, "n_output": 50}
    problem = resolve_problem(OptimizationProblem.model_validate(cfg))
    solver = ScipyGlobal(method="differential_evolution", seed=0, maxiter=10)
    result = solver.solve(problem)
    assert result.trajectory is not None
    assert result.n_evaluations > 0


def test_optimizer_beats_an_equal_energy_constant_force_baseline() -> None:
    """Acceptance test (task.md T8): on a min-time-to-distance problem,
    the optimizer's answer beats a constant-force baseline spending the
    same total mechanical energy."""
    problem = _fast_problem()
    solver = ScipyLocal(method="SLSQP", multistart=1, seed=0, use_process_pool=False, maxiter=120)
    result = solver.solve(problem)
    assert result.success

    optimal_energy = float(np.trapezoid(result.trajectory.power_pedal, result.trajectory.t))
    t_final = float(result.trajectory.t[-1])
    mean_speed = float(result.trajectory.x[-1]) / t_final
    gf = gear_factor(problem.spec)
    baseline_force = (optimal_energy / t_final) / (gf * mean_speed)
    baseline_pedal = ConstantCrankTorque(
        tau=baseline_force * problem.spec.frame.r_1_pedal, r_1_pedal=problem.spec.frame.r_1_pedal
    )
    baseline_traj = problem.forward_model.rollout(
        ControlInputs(pedal=baseline_pedal), problem.horizon
    )
    baseline_energy = float(np.trapezoid(baseline_traj.power_pedal, baseline_traj.t))

    assert baseline_energy == pytest.approx(optimal_energy, rel=1e-3)
    assert t_final < float(baseline_traj.t[-1])


def test_infeasible_problem_fails_with_a_clear_diagnostic_not_a_crash() -> None:
    """Acceptance test (task.md T8): a power ceiling below what the grade
    physically requires fails with a clear diagnostic, not a crash or a
    silently-plausible answer."""
    from bikephysics.optim.problem import load_problem, resolve_problem_path

    problem = resolve_problem(load_problem(resolve_problem_path("infeasible_steep_climb")))
    solver = ScipyLocal(method="SLSQP", multistart=1, seed=0, use_process_pool=False, maxiter=50)
    result = solver.solve(problem)  # must not raise
    assert result.success is False
    assert "x_target" in result.message


def test_enumerate_gear_options_picks_the_best_candidate() -> None:
    cfg = dict(_FAST_PROBLEM)
    cfg["parametrization"] = {"kind": "piecewise_constant_pedal", "n": 1}
    cfg["objective"] = {"kind": "max_distance_in_time", "t_end": 5.0}
    cfg["horizon"] = {"t_max": 5.0, "n_output": 50}
    cfg["constraints"] = []
    problem = resolve_problem(OptimizationProblem.model_validate(cfg))
    solver = ScipyLocal(method="L-BFGS-B", multistart=1, seed=0, use_process_pool=False, maxiter=30)

    n_teeth, result = enumerate_gear_options(problem, solver, (14, 16, 18))
    assert n_teeth in (14, 16, 18)
    assert result.success
