"""optim/solvers/base.py -- the `Solver` protocol, `OptimResult`, and the
shared rollout-caching `Evaluator` (architecture.md §8.4).

`OptimResult` "carries `x_opt`, the optimal `Trajectory`, the full iterate
history, status, wall time, and the active-constraint report -- all
written into the run dir so `bike opt compare` can overlay runs" (§8.4).

`Evaluator` exists because a single scipy iterate at `x` is typically
queried several times (objective, each constraint, sometimes a
finite-difference-gradient probe at the *same* `x` before `x` moves) --
caching the one rollout per distinct `x` avoids repeating the ODE
integration for every one of those queries.

`enumerate_gear_options` is the §8.2-mandated outer loop for
`optim.parametrization.GearSelection`: "Handled by enumeration (outer
loop) rather than a relaxed continuous radius, because cog teeth are
integers" -- for each candidate tooth count, it rebuilds the `BikeSpec`
and `ForwardModel` directly (bypassing `GearSelection`'s own continuous
relaxation entirely) and re-solves the *remaining* (gear-free)
parametrization, returning the best result across options.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from bikephysics.core.params import BikeSpec
from bikephysics.core.results import Trajectory
from bikephysics.optim.problem import ResolvedProblem

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class OptimResult:
    """architecture.md §8.4."""

    x_opt: FloatArray
    trajectory: Trajectory
    objective_value: float
    history: tuple[dict[str, float], ...]
    status: str
    message: str
    success: bool
    wall_time_s: float
    active_constraints: dict[str, float]
    n_evaluations: int
    solver_name: str
    extra: dict[str, Any] = field(default_factory=dict)


class Solver(Protocol):
    """architecture.md §8.4."""

    def solve(self, problem: ResolvedProblem) -> OptimResult: ...


@dataclass
class Evaluator:
    """Caches the single most recent `(x, Trajectory)` pair for a given
    `ResolvedProblem` (module docstring). Not thread/process-safe to share
    across workers -- each multi-start worker builds its own `Evaluator`."""

    problem: ResolvedProblem
    n_evaluations: int = 0
    _last_x: FloatArray | None = field(default=None, repr=False)
    _last_traj: Trajectory | None = field(default=None, repr=False)

    def trajectory(self, x: FloatArray) -> Trajectory:
        x_arr = np.asarray(x, dtype=float)
        if self._last_x is not None and np.array_equal(x_arr, self._last_x):
            assert self._last_traj is not None
            return self._last_traj
        controls = self.problem.parametrization.build(x_arr)
        traj = self.problem.forward_model.rollout(controls, self.problem.horizon)
        self._last_x = x_arr
        self._last_traj = traj
        self.n_evaluations += 1
        return traj

    def objective(self, x: FloatArray) -> float:
        return self.problem.objective.value(self.trajectory(x))

    def penalty(self, x: FloatArray, weight: float) -> float:
        traj = self.trajectory(x)
        return float(sum(c.penalty(traj, weight) for c in self.problem.constraints))

    def objective_with_penalty(self, x: FloatArray, weight: float) -> float:
        return self.objective(x) + self.penalty(x, weight)

    def constraint_vector(self, x: FloatArray) -> FloatArray:
        """All constraints' `g(x)` concatenated into one vector, `<= 0`
        feasible -- the full elementwise-over-time shape, used by the
        penalty-mode aggregation (`penalty`/`objective_with_penalty`) and
        for post-hoc violation reporting."""
        if not self.problem.constraints:
            return np.array([])
        traj = self.trajectory(x)
        return np.concatenate(
            [np.atleast_1d(np.asarray(c.g(traj), dtype=float)) for c in self.problem.constraints]
        )

    def constraint_scalars(self, x: FloatArray) -> FloatArray:
        """One scalar per `PathConstraint` -- `max(g(traj))` -- rather than
        one entry per time sample. Used for *native* gradient-based
        constraint handling (`SLSQP`/`trust-constr`): a `Trajectory` with
        `n_output ~ 2000` samples would otherwise hand SLSQP ~2000 heavily
        redundant per-timestep constraints per `PathConstraint`, which
        ill-conditions its QP subproblem's constraint Jacobian (observed
        empirically: SLSQP stopped short of the true optimum on the
        single-variable oracle problem until this scalarization was
        added). This conservative worst-case-over-time reduction is a
        deliberate approximation for the gradient-based path only --
        `penalty`/`constraint_vector` above stay elementwise, since the
        penalty-mode aggregation does not suffer the same conditioning
        problem."""
        if not self.problem.constraints:
            return np.array([])
        traj = self.trajectory(x)
        return np.array(
            [
                float(np.max(np.atleast_1d(np.asarray(c.g(traj), dtype=float))))
                for c in self.problem.constraints
            ]
        )


def active_constraint_report(problem: ResolvedProblem, traj: Trajectory) -> dict[str, float]:
    """`{constraint_label: max(g)}` for every constraint on `problem`,
    evaluated at `traj` -- `max(g) > 0` means violated, `== 0` (to
    tolerance) means active/binding, `< 0` means slack. Reported for every
    constraint, not only active ones, so a report can show the full
    limit-margin trace (architecture.md §8's `optim.report` item)."""
    report: dict[str, float] = {}
    for i, c in enumerate(problem.constraints):
        label = f"{i}_{type(c).__name__}"
        g = np.atleast_1d(np.asarray(c.g(traj), dtype=float))
        report[label] = float(np.max(g)) if g.size else float("-inf")
    return report


def reached_x_target(problem: ResolvedProblem, traj: Trajectory) -> bool | None:
    """`None` when `problem.horizon.x_target` is unset (no terminal-
    distance target to check); otherwise whether the rollout's own
    terminal event was `"x_target"` (`simplified.simulate.simulate`'s
    `meta["termination_reason"]`), as opposed to a coast-to-stop or simply
    running out of `t_max` -- the generic "did this solution actually
    arrive" diagnostic a min-time-style objective implicitly needs,
    independent of which `Objective` subclass is in play. The ticket's
    deliberately-infeasible acceptance test (a power ceiling below what
    the grade physically requires) is exactly the case this catches:
    without it, a non-arriving rollout would just report a large but
    plausible-looking `objective_value`, not a clear failure."""
    if problem.horizon.x_target is None:
        return None
    return traj.meta.get("termination_reason") == "x_target"


def max_constraint_violation(problem: ResolvedProblem, traj: Trajectory) -> float:
    """`max(0, max(g))` over every constraint -- `0.0` means every
    constraint is satisfied."""
    report = active_constraint_report(problem, traj)
    if not report:
        return 0.0
    return max(0.0, max(report.values()))


def _is_better_gear_candidate(candidate: OptimResult, current_best: OptimResult) -> bool:
    """A successful candidate always beats a failed current-best; between
    two successful candidates, the lower objective value wins; a failed
    candidate never replaces a successful current-best."""
    if candidate.success and not current_best.success:
        return True
    if candidate.success and current_best.success:
        return candidate.objective_value < current_best.objective_value
    return False


def enumerate_gear_options(
    problem: ResolvedProblem, solver: Solver, cog_teeth_options: Sequence[int]
) -> tuple[int, OptimResult]:
    """module docstring. `problem.parametrization` must already exclude
    `GearSelection` (it solves the *remaining* decision variables at each
    candidate gear); `problem.forward_model` must be a
    `optim.forward.SimplifiedForwardModel` (or any dataclass `ForwardModel`
    exposing a `spec` field `dataclasses.replace` can override)."""
    if not cog_teeth_options:
        raise ValueError("enumerate_gear_options: cog_teeth_options must not be empty")
    best: tuple[int, OptimResult] | None = None
    for n_teeth in cog_teeth_options:
        r_2a_cg = problem.spec.drivetrain.chain_pitch / (2.0 * math.sin(math.pi / n_teeth))
        data = problem.spec.model_dump()
        data["drivetrain"]["n_teeth_cg"] = n_teeth
        data["drivetrain"]["r_2a_cg"] = r_2a_cg
        spec_i = BikeSpec.model_validate(data)
        # `ForwardModel` is a Protocol (structurally typed), so mypy cannot
        # verify `replace` works on every conformer -- the docstring above
        # restricts this function to dataclass `ForwardModel`s that do.
        forward_model_i = replace(problem.forward_model, spec=spec_i)  # type: ignore[type-var]
        problem_i = replace(problem, forward_model=forward_model_i, spec=spec_i)
        result_i = solver.solve(problem_i)
        if best is None or _is_better_gear_candidate(result_i, best[1]):
            best = (n_teeth, result_i)
    assert best is not None
    return best
