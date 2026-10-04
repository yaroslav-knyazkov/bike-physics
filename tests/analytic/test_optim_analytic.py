"""Tests for bikephysics.optim.solvers.analytic (architecture.md S8.4,
D-11): `AnalyticSimplified` is the correctness oracle for the whole
optimization stack. Ticket T8's primary acceptance test lives here: the
numeric solvers (`ScipyLocal`/`ScipyGlobal`) must reproduce
`AnalyticSimplified`'s closed-form answer to < 0.5 % on the flat/constant-
power/limit-free min-time oracle problem.
"""

from __future__ import annotations

import pytest

from bikephysics.optim.constraints import PedalForceBound
from bikephysics.optim.problem import (
    ResolvedProblem,
    load_problem,
    resolve_problem,
    resolve_problem_path,
)
from bikephysics.optim.solvers.analytic import AnalyticSimplified, solve_closed_form
from bikephysics.optim.solvers.base import Solver
from bikephysics.optim.solvers.scipy_global import ScipyGlobal
from bikephysics.optim.solvers.scipy_local import ScipyLocal
from tests.unit.factories import make_minimal_bike_spec

_ORACLE_TOLERANCE_PCT = 0.5


def _oracle_problem() -> ResolvedProblem:
    return resolve_problem(load_problem(resolve_problem_path("oracle_constant_power")))


def test_solve_closed_form_is_self_consistent_with_its_own_rollout() -> None:
    """The closed-form quadrature answer and a real ODE rollout of the
    identical bang-constant-power control must agree closely -- both
    describe exactly the same physics (module docstring's derivation)."""
    problem = _oracle_problem()
    answer = solve_closed_form(problem.spec, p_max=300.0, x_target=500.0, v0=0.0)
    assert answer.t_star > 0.0
    assert 0.0 < answer.v_star < answer.v_inf

    solver = AnalyticSimplified()
    result = solver.solve(problem)
    assert result.success
    assert result.objective_value == pytest.approx(answer.t_star)
    rel_err = abs(result.extra["rollout_t_final"] - answer.t_star) / answer.t_star
    assert rel_err < 1e-4  # the rollout is a numerical cross-check, not the oracle itself


def test_analytic_simplified_rejects_a_graded_road() -> None:
    cfg = {
        "name": "bad_oracle",
        "forward_model": "simplified",
        "bike": "road_bike",
        "road": "grade_5pct",
        "horizon": {"t_max": 60.0, "x_target": 200.0},
        "parametrization": {"kind": "template_shape", "shape": "constant_power"},
        "objective": {"kind": "min_time_over_distance"},
        "constraints": [{"kind": "power_ceiling", "p_max": 300.0}],
        "solver": {"kind": "analytic"},
    }
    from bikephysics.optim.problem import OptimizationProblem

    problem = resolve_problem(OptimizationProblem.model_validate(cfg))
    with pytest.raises(ValueError, match="flat"):
        AnalyticSimplified().solve(problem)


def test_analytic_simplified_rejects_extra_constraints() -> None:
    from dataclasses import replace

    problem = _oracle_problem()
    extra = PedalForceBound(p_max=800.0)
    bad_problem = replace(problem, constraints=(*problem.constraints, extra))
    with pytest.raises(ValueError, match="limit-free"):
        AnalyticSimplified().solve(bad_problem)


@pytest.mark.parametrize(
    "solver",
    [
        ScipyLocal(method="SLSQP", multistart=2, seed=0, use_process_pool=False, maxiter=100),
        ScipyLocal(
            method="trust-constr", multistart=1, seed=0, use_process_pool=False, maxiter=100
        ),
        ScipyGlobal(method="differential_evolution", seed=0, maxiter=60),
    ],
    ids=["scipy_local_slsqp", "scipy_local_trust_constr", "scipy_global_de"],
)
def test_numeric_solvers_match_the_analytic_oracle_within_half_a_percent(solver: Solver) -> None:
    """Ticket T8's primary acceptance test."""
    problem = _oracle_problem()
    oracle = AnalyticSimplified().solve(problem)

    result = solver.solve(problem)
    rel_err = abs(result.objective_value - oracle.objective_value) / oracle.objective_value
    rel_err_pct = rel_err * 100.0
    assert rel_err_pct < _ORACLE_TOLERANCE_PCT, (
        f"{result.solver_name}: {result.objective_value}s vs oracle {oracle.objective_value}s "
        f"({rel_err_pct:.4f} %)"
    )


def test_oracle_derivation_uses_the_exact_m_eq_not_the_bare_mass() -> None:
    """Sanity check on the derivation itself (module docstring): using the
    bare mass instead of `model.m_eq` would give a measurably different
    (smaller) time, since M_eq > M whenever wheel/drivetrain inertia is
    present."""
    spec = make_minimal_bike_spec()
    from bikephysics.simplified import model

    assert model.m_eq(spec) > spec.M
