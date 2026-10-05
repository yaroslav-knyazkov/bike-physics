"""planar/constraints/gear.py -- IdealChain: the lumped-drivetrain gear
constraint (architecture.md S7.4's `IdealChain(2a, 3b)` row; S7.5.2's
(7.5)-(7.6); D-10 option B).

A single scalar holonomic constraint (dim=1) tying the rear wheel's spin
to the crankset's spin through the fixed gear ratio `G = r_3b_cr /
r_2a_cg` (S7.5.1 item 3: massless, inextensible, non-slipping, lossless,
single-speed chain -- no chain body, no chainline geometry).

**Sign correction, recorded for architect sign-off (the third correction
of this kind in this ticket; see `forces/pedal.py` and
`planar/kinematics.py` for the first two, both stemming from the same
root cause).** Architecture.md S7.5.2 writes

    c_chain(q) = r_3b_cr*(theta_3b - theta_1) - r_2a_cg*(theta_2a - theta_1) = 0   (7.5, as written)

and claims the resulting generalized forces are `-r_3b_cr*T` on
`theta_3b` and `+r_2a_cg*T` on `theta_2a` ("the chain resists crank spin
and drives wheel spin"), attributing this to the convention "`h_constraint
= -J^T lambda`". But `planar/solver.py`'s own `kkt_solve` (D-8, already
built and tested) actually implements `M @ a = h + J^T @ lambda` --
confirmed directly from its arithmetic (`sol = [a, -lam]`, so
`M@a + J^T@(-lam) = h`) and independently by `constraints/revolute.py`'s
own docstring ("the generalized reaction `J^T lambda` is `+lambda` on
`body_b`'s slots"). With the *actual* `+J^T @ lambda` convention, (7.5)'s
literal `J` gives `+r_3b_cr*T` on `theta_3b` and `-r_2a_cg*T` on
`theta_2a` -- backward from the stated (and physically correct) "chain
resists crank, drives wheel".

The fix is a single overall sign flip of `c_chain` (everything else about
(7.5)-(7.6) -- units, which multiplier is the chain tension, the
unilateral `(0, +inf)` bound -- is unaffected, since negating a holonomic
constraint's residual does not change the set `c = 0` it describes, only
which sign of `J^T @ lambda` physically means "tension"):

    c_chain(q) = r_2a_cg*(theta_2a - theta_1) - r_3b_cr*(theta_3b - theta_1) = 0   (7.5, corrected)

    dc/dtheta_2a = +r_2a_cg
    dc/dtheta_3b = -r_3b_cr
    dc/dtheta_1  = r_3b_cr - r_2a_cg

which, under the engine's actual `+J^T @ lambda` convention, gives
`-r_3b_cr*T` on `theta_3b` and `+r_2a_cg*T` on `theta_2a` -- exactly
(7.5)'s own stated physical intent, now achieved with the opposite
literal sign. `lambda_chain` remains the chain tension `T >= 0`
(`multiplier_bounds = (0, +inf)`, unchanged).

`J = dc/dq` is constant in `q` (every nonzero entry is a fixed geometric
coefficient on a `theta` column), so `rate_term` (`-J_dot @ u`) is
identically zero -- the same reasoning `constraints/weld.py`'s
`theta_offset` row already uses for its own constant-Jacobian-row
component.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.planar.constraints.base import ConstraintKind
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class IdealChain:
    """`c_chain = r_2a_cg*(theta_2a - theta_1) - r_3b_cr*(theta_3b -
    theta_1)` (architecture.md S7.5.2 eq. 7.5, corrected -- see module
    docstring). `wheel_body`/`crank_body`/`frame_body` default to this
    project's fixed body names (`"2a"`, `"3b"`, `"1"`) but are kept as
    fields (not hardcoded strings inside the methods) so this constraint
    stays a generic multi-body primitive -- `planar/assembly.py` is the
    only place bike topology may be hard-coded (architecture.md S7's own
    charter), not this file.

    `multiplier_bounds = (0, +inf)`: the freewheel condition `T >= 0`
    (S7.5.4 eq. 7.11), resolved by `solver.py`'s existing generic
    unilateral trial loop -- this class contributes no mode-switching
    logic of its own; that lives in `planar/drivetrain.py` (the impulsive
    re-engagement event, which the trial loop alone cannot express since
    it never changes `u`).
    """

    name: str
    r_chainring: float  # r_3b,cr
    r_cog: float  # r_2a,cg
    wheel_body: str = "2a"
    crank_body: str = "3b"
    frame_body: str = "1"
    dim: int = field(default=1, init=False)
    kind: ConstraintKind = field(default="unilateral", init=False)

    def __post_init__(self) -> None:
        if self.r_chainring <= 0.0:
            raise ValueError(f"r_chainring must be > 0, got {self.r_chainring}")
        if self.r_cog <= 0.0:
            raise ValueError(f"r_cog must be > 0, got {self.r_cog}")

    @property
    def gear_ratio(self) -> float:
        """`G = r_3b_cr / r_2a_cg` (S7.5.2 eq. 7.2)."""
        return self.r_chainring / self.r_cog

    def residual(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        phi_crank = layout.phi(q, self.crank_body, reference=self.frame_body)
        phi_wheel = layout.phi(q, self.wheel_body, reference=self.frame_body)
        c = self.r_cog * phi_wheel - self.r_chainring * phi_crank
        return np.array([c])

    def jacobian(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        J = np.zeros((1, layout.nq))
        J[0, layout.idx_theta(self.wheel_body)] += self.r_cog
        J[0, layout.idx_theta(self.crank_body)] += -self.r_chainring
        # `theta_1`'s column is `+r_3b_cr - r_2a_cg`, written as two `+=`
        # so this still works if `frame_body` ever coincided with
        # `wheel_body`/`crank_body` (not a configuration this bike uses,
        # but this is the generic-engine convention everywhere else).
        J[0, layout.idx_theta(self.frame_body)] += self.r_chainring
        J[0, layout.idx_theta(self.frame_body)] += -self.r_cog
        return J

    def rate_term(
        self, q: FloatArray, u: FloatArray, layout: SystemLayout, xp: MathBackend
    ) -> FloatArray:
        # J is constant in q (every entry is a fixed geometric coefficient
        # on a theta column) -- J_dot = 0 exactly, so -J_dot @ u = 0.
        return np.zeros(1)

    def multiplier_bounds(self) -> tuple[float, float] | None:
        return (0.0, math.inf)
