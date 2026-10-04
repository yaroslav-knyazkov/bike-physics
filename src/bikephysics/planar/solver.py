"""planar/solver.py -- index-1 KKT saddle-point solve + the generic
unilateral trial loop (architecture.md S7.7 steps 1-6; D-8).

Convention (architecture.md S7.7's literal pseudocode):

    [ M   J^T ] [ a  ]   [ h    ]
    [ J   0   ] [ -L ] = [ zeta ]

i.e. the solved second block is `-lambda`; `lambda = -second_block` is the
constraint's reaction force/torque (D-8: "the multipliers *are* the
reaction forces"). Verified against the hanging-pin analytic test in
`tests/analytic/test_planar_analytic.py`: a body of mass `m` pinned at its
own CoG under gravity gives `lambda_z = +m*g`, the supporting reaction --
i.e. this is the *physical* reaction force, not its negative.

Generic unilateral trial (architecture.md S7.7 step 5, S7.4): try a solve
with every constraint active; if any `kind == "unilateral"` constraint's
multiplier falls outside its `multiplier_bounds()`, drop that row and
re-solve. T5's own tests are the only exercise this gets here (a dummy
unilateral constraint, see `tests/analytic/test_planar_analytic.py`);
future ground-contact and drivetrain tickets are the same loop with their
own constraints plugged in.

This file carries no domain-specific assumption of any kind -- future
tickets consume this solver through `Constraint`/`ForceElement` only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.linalg import lu_factor, lu_solve

from bikephysics.planar.constraints.base import Constraint
from bikephysics.planar.system import MultibodySystem

FloatArray = npt.NDArray[np.float64]

_MAX_TRIAL_ITERATIONS = 16  # generous: a real run drops O(n_unilateral) rows, not more
_BOUNDS_TOL = 1e-9


@dataclass(frozen=True)
class SolveResult:
    """One RHS evaluation's output: accelerations, the multiplier of
    every *active* constraint by name, which constraints ended up
    active/dropped, and the constraint-block diagnostics (`c`, `c_dot`)
    `integrate.py` needs for drift reporting."""

    a: FloatArray
    multipliers: dict[str, FloatArray]
    active_names: tuple[str, ...]
    dropped_names: tuple[str, ...]
    c: FloatArray
    c_dot: FloatArray
    row_map: dict[str, slice]


def kkt_solve(
    M: FloatArray, h: FloatArray, J: FloatArray, zeta: FloatArray
) -> tuple[FloatArray, FloatArray]:
    """One dense LU solve of the index-1 KKT system for a *fixed* active
    set (architecture.md S7.7 step 4, dense `scipy.linalg.lu_factor`/
    `lu_solve`, size `(nq + n_c) <= 19` for a 4-body system). Returns
    `(a, lam)` with `lam` the stacked multiplier vector for the rows of
    `J` as given.
    """
    nq = M.shape[0]
    n_c = J.shape[0]
    n = nq + n_c
    A = np.zeros((n, n))
    A[:nq, :nq] = M
    A[:nq, nq:] = J.T
    A[nq:, :nq] = J
    rhs = np.concatenate([h, zeta])
    lu, piv = lu_factor(A)
    sol = lu_solve((lu, piv), rhs)
    a = sol[:nq]
    lam = -sol[nq:]
    return a, lam


def _multiplier_out_of_bounds(con: Constraint, lam: FloatArray) -> bool:
    if con.kind != "unilateral":
        return False
    bounds = con.multiplier_bounds()
    if bounds is None:
        return False
    lo, hi = bounds
    # Every unilateral constraint this engine registers is scalar
    # (dim == 1) -- a vector-valued unilateral row is not a case
    # this engine needs to support.
    value = float(np.asarray(lam).reshape(-1)[0])
    return value < lo - _BOUNDS_TOL or value > hi + _BOUNDS_TOL


def solve_accelerations(
    system: MultibodySystem,
    t: float,
    q: FloatArray,
    u: FloatArray,
    *,
    alpha: float,
    beta: float,
) -> SolveResult:
    """Architecture.md S7.7 steps 1-6 for one RHS evaluation: assemble
    `h`, trial-solve with every constraint active, and drop any
    unilateral row whose multiplier violates its bounds until the active
    set is self-consistent (or the iteration bound is hit)."""
    h = system.generalized_force(t, q, u)
    M = system.mass_matrix()
    all_constraints = list(system.constraints)
    dropped: list[str] = []
    active = list(all_constraints)

    for _ in range(_MAX_TRIAL_ITERATIONS):
        block = system.constraint_block(q, u, active, alpha=alpha, beta=beta)
        a, lam_stacked = kkt_solve(M, h, block.J, block.zeta)
        multipliers = {name: lam_stacked[block.row_map[name]] for name in block.row_map}

        to_drop = [con for con in active if _multiplier_out_of_bounds(con, multipliers[con.name])]
        if not to_drop:
            return SolveResult(
                a=a,
                multipliers=multipliers,
                active_names=tuple(block.names),
                dropped_names=tuple(dropped),
                c=block.c,
                c_dot=block.c_dot,
                row_map=block.row_map,
            )
        for con in to_drop:
            active.remove(con)
            dropped.append(con.name)

    raise RuntimeError(
        "unilateral trial loop did not converge within "
        f"{_MAX_TRIAL_ITERATIONS} iterations; dropped so far: {dropped}"
    )
