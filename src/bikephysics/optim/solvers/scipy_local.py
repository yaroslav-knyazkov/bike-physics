"""optim/solvers/scipy_local.py -- `ScipyLocal` (architecture.md §8.4,
D-11 option A): `L-BFGS-B`/`SLSQP`/`trust-constr` via
`scipy.optimize.minimize`, finite-difference gradients, with a multi-start
wrapper.

`SLSQP`/`trust-constr` get every `PathConstraint` passed natively (a
single vector-valued `NonlinearConstraint`, `g(x) <= 0` restated as
`-g(x) >= 0` for scipy's `lb <= fun(x) <= ub` convention); `L-BFGS-B` has
no native inequality-constraint support, so its constraints are folded
into the objective via `optim.constraints`'s shared quadratic
`penalty(traj, weight)`.

Multi-start runs each start in its own worker via
`concurrent.futures.ProcessPoolExecutor` (architecture.md §8.4's own
wording: "Multi-start wrapper"). `_single_start` is a module-level
function (not a closure) so it pickles for the pool; if pickling still
fails for a given problem (e.g. a `PowerCeiling(P_max(cadence))` built
from a lambda rather than a module-level function -- `Objective`/
`PathConstraint` callables are the one place a user-supplied closure can
sneak into a `ResolvedProblem`), the whole multistart loop falls back to
running sequentially in-process, logging a warning rather than crashing.

**Variable scaling.** `_single_start` optimizes over `u = (x - lo) /
(hi - lo) in [0, 1]^n`, not the raw physical `x`, rescaling back for every
`fun`/`constraint` evaluation. This project's decision variables span
wildly different physical units in the same vector (pedal force ~100s of
N, a crank-angle phase in `[0, 2*pi]`, a shape exponent ~1-6), and
`scipy.optimize.minimize`'s quasi-Newton methods (`SLSQP`, `L-BFGS-B`)
start from an identity Hessian approximation -- on an unscaled problem the
first step is sized as if one unit of (say) watts and one unit of radians
were equally significant, which measurably matters: confirmed empirically
on the single-variable oracle problem (`data/problems/
oracle_constant_power.toml`), where unscaled `SLSQP` converged after one
iteration to a point far from the true optimum (a tiny first step judged
"no further improvement" on the objective's natural, much larger scale),
while the same problem rescaled to `[0, 1]` converges correctly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import Bounds, NonlinearConstraint, minimize

from bikephysics.optim.problem import ResolvedProblem
from bikephysics.optim.solvers.base import (
    Evaluator,
    OptimResult,
    active_constraint_report,
    reached_x_target,
)

FloatArray = npt.NDArray[np.float64]

logger = logging.getLogger(__name__)

_NATIVE_CONSTRAINT_METHODS = frozenset({"SLSQP", "trust-constr"})
_FEASIBILITY_TOL = 1e-6


def _random_starts(
    bounds: list[tuple[float, float]], n: int, seed: int | None, first: FloatArray
) -> list[FloatArray]:
    starts = [first]
    if n <= 1 or not bounds:
        return starts
    rng = np.random.default_rng(seed)
    lo = np.array([b[0] for b in bounds])
    hi = np.array([b[1] for b in bounds])
    for _ in range(n - 1):
        starts.append(rng.uniform(lo, hi))
    return starts


def _max_violation(problem: ResolvedProblem, evaluator: Evaluator, x: FloatArray) -> float:
    if not problem.constraints:
        return 0.0
    g = evaluator.constraint_scalars(x)
    return float(max(0.0, float(np.max(g)))) if g.size else 0.0


def _single_start(
    problem: ResolvedProblem, x0: FloatArray, method: str, penalty_weight: float, maxiter: int
) -> dict[str, Any]:
    """Run one `scipy.optimize.minimize` from `x0`, internally rescaled to
    `u in [0, 1]^n` (module docstring)."""
    evaluator = Evaluator(problem=problem)
    bounds_list = problem.parametrization.bounds(problem.spec)
    lo = np.array([b[0] for b in bounds_list], dtype=float)
    hi = np.array([b[1] for b in bounds_list], dtype=float)
    scale = np.where(hi > lo, hi - lo, 1.0)

    def to_x(u: FloatArray) -> FloatArray:
        return lo + np.asarray(u, dtype=float) * scale

    def to_u(x: FloatArray) -> FloatArray:
        return (np.asarray(x, dtype=float) - lo) / scale

    bounds = Bounds(np.zeros_like(lo), np.ones_like(hi))
    u0 = to_u(x0)
    history: list[dict[str, float]] = []

    def record(uk: FloatArray, *_state: Any) -> None:
        history.append({"objective": evaluator.objective(to_x(uk))})

    fun: Callable[[FloatArray], float]
    constraints_arg: Any
    if method in _NATIVE_CONSTRAINT_METHODS and problem.constraints:
        constraints_arg = [
            NonlinearConstraint(lambda u: -evaluator.constraint_scalars(to_x(u)), 0.0, np.inf)
        ]

        def fun(u: FloatArray) -> float:
            return evaluator.objective(to_x(u))
    else:
        constraints_arg = []

        def fun(u: FloatArray) -> float:
            return evaluator.objective_with_penalty(to_x(u), penalty_weight)

    result = minimize(
        fun,
        u0,
        method=method,
        bounds=bounds,
        constraints=constraints_arg,
        callback=record,
        options={"maxiter": maxiter},
    )
    x_opt = to_x(np.asarray(result.x, dtype=float))
    return {
        "x": x_opt,
        "objective_value": float(evaluator.objective(x_opt)),
        "violation": _max_violation(problem, evaluator, x_opt),
        "success": bool(result.success),
        "message": str(result.message),
        "status": str(getattr(result, "status", "")),
        "history": history,
        "n_evaluations": evaluator.n_evaluations,
    }


@dataclass
class ScipyLocal:
    """architecture.md §8.4."""

    method: Literal["L-BFGS-B", "SLSQP", "trust-constr"] = "SLSQP"
    multistart: int = 1
    penalty_weight: float = 1e6
    seed: int | None = None
    maxiter: int = 200
    use_process_pool: bool = True

    def solve(self, problem: ResolvedProblem) -> OptimResult:
        t0 = time.monotonic()
        x0 = np.asarray(problem.parametrization.initial_guess(problem.spec), dtype=float)
        bounds_list = problem.parametrization.bounds(problem.spec)
        starts = _random_starts(bounds_list, self.multistart, self.seed, x0)

        if self.use_process_pool and len(starts) > 1:
            try:
                with ProcessPoolExecutor() as pool:
                    futures = [
                        pool.submit(
                            _single_start,
                            problem,
                            start,
                            self.method,
                            self.penalty_weight,
                            self.maxiter,
                        )
                        for start in starts
                    ]
                    results = [f.result() for f in futures]
            except Exception:
                logger.warning(
                    "ScipyLocal: multistart via ProcessPoolExecutor failed (often a "
                    "pickling issue with a user-supplied callable, e.g. a lambda "
                    "PowerCeiling.p_max); falling back to a sequential loop.",
                    exc_info=True,
                )
                results = [
                    _single_start(problem, start, self.method, self.penalty_weight, self.maxiter)
                    for start in starts
                ]
        else:
            results = [
                _single_start(problem, start, self.method, self.penalty_weight, self.maxiter)
                for start in starts
            ]

        feasible = [r for r in results if r["violation"] <= _FEASIBILITY_TOL]
        pool_ = feasible if feasible else results
        best = min(pool_, key=lambda r: r["objective_value"])

        traj = problem.forward_model.rollout(
            problem.parametrization.build(best["x"]), problem.horizon
        )
        wall_time_s = time.monotonic() - t0

        arrived = reached_x_target(problem, traj)
        success = bool(best["success"]) and best["violation"] <= _FEASIBILITY_TOL
        message = best["message"]
        if arrived is False:
            success = False
            message = (
                f"{message}; the rollout did not reach horizon.x_target="
                f"{problem.horizon.x_target} (it coasted to a stop or ran out of "
                "t_max first) -- check whether the available power/force can "
                "overcome the road grade and rolling resistance."
            )

        return OptimResult(
            x_opt=best["x"],
            trajectory=traj,
            objective_value=best["objective_value"],
            history=tuple(best["history"]),
            status=best["status"],
            message=message,
            success=success,
            wall_time_s=wall_time_s,
            active_constraints=active_constraint_report(problem, traj),
            n_evaluations=sum(r["n_evaluations"] for r in results),
            solver_name=f"scipy_local[{self.method}]",
            extra={
                "n_starts": len(starts),
                "violation": best["violation"],
                "reached_x_target": arrived,
            },
        )
