"""optim/solvers/scipy_global.py -- `ScipyGlobal` (architecture.md §8.4):
`differential_evolution`/`dual_annealing`, "robust for the 2-6-var
`TemplateShape` problems, and the fallback whenever the forward model is
non-differentiable". Neither global method gets native constraint support
here -- both are driven purely through the shared quadratic
`penalty(traj, weight)` (the same aggregation `ScipyLocal`'s `L-BFGS-B`
path uses), keeping the two global methods' code path identical regardless
of how many/which constraints a problem has.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import differential_evolution, dual_annealing

from bikephysics.optim.problem import ResolvedProblem
from bikephysics.optim.solvers.base import (
    Evaluator,
    OptimResult,
    active_constraint_report,
    reached_x_target,
)

FloatArray = npt.NDArray[np.float64]


@dataclass
class ScipyGlobal:
    """architecture.md §8.4."""

    method: Literal["differential_evolution", "dual_annealing"] = "differential_evolution"
    penalty_weight: float = 1e6
    seed: int | None = None
    maxiter: int = 200

    def solve(self, problem: ResolvedProblem) -> OptimResult:
        t0 = time.monotonic()
        evaluator = Evaluator(problem=problem)
        bounds_list = [tuple(b) for b in problem.parametrization.bounds(problem.spec)]

        def fun(x: FloatArray) -> float:
            return evaluator.objective_with_penalty(x, self.penalty_weight)

        if self.method == "differential_evolution":
            res = differential_evolution(
                fun, bounds_list, seed=self.seed, maxiter=self.maxiter, polish=True
            )
        elif self.method == "dual_annealing":
            res = dual_annealing(fun, bounds_list, seed=self.seed, maxiter=self.maxiter)
        else:
            raise ValueError(f"unknown ScipyGlobal method {self.method!r}")

        x_opt = np.asarray(res.x, dtype=float)
        traj = evaluator.trajectory(x_opt)
        g = evaluator.constraint_vector(x_opt)
        violation = float(max(0.0, float(np.max(g)))) if g.size else 0.0

        wall_time_s = time.monotonic() - t0

        arrived = reached_x_target(problem, traj)
        success = bool(res.success) and violation <= 1e-6
        message = str(res.message)
        if arrived is False:
            success = False
            message = (
                f"{message}; the rollout did not reach horizon.x_target="
                f"{problem.horizon.x_target} (it coasted to a stop or ran out of "
                "t_max first) -- check whether the available power/force can "
                "overcome the road grade and rolling resistance."
            )

        return OptimResult(
            x_opt=x_opt,
            trajectory=traj,
            objective_value=float(evaluator.objective(x_opt)),
            history=(),
            status=str(getattr(res, "status", "")),
            message=message,
            success=success,
            wall_time_s=wall_time_s,
            active_constraints=active_constraint_report(problem, traj),
            n_evaluations=evaluator.n_evaluations,
            solver_name=f"scipy_global[{self.method}]",
            extra={"violation": violation, "reached_x_target": arrived},
        )
