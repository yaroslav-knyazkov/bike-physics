"""planar/constraints/rolling.py -- RollingContact: the exact no-slip
constraint (architecture.md S7.4 table's `RollingContact(wheel)` row;
`latex/2d_assembly/wheels_ground.tex` "Rolling constraint" S, "Stick"
case).

**Not instantiated by T6.** The default `RegularizedContact`
(`planar.contact.regularized`) enforces the rolling/sliding transition as
a smooth Stribeck force law, not a hard constraint -- it never touches
this file. `RollingContact` exists for `EventDrivenContact` (T11,
architecture.md S7.6.3), which instantiates it only *while* a wheel is in
"stick" mode (dropping it the instant `|R_x| > mu_s * R_z` would be
required to hold `v_slip = 0`, per `wheels_ground.tex`'s own stick/slip
switch) and removes it again on a slip-to-stick event.

`dim = 1`, `kind = "bilateral"`: whenever this constraint is *present* in
the active set it is unconditionally enforced (not multiplier-bounds-
dropped by `solver.py`'s generic unilateral trial loop) -- the decision of
*when* to add/remove it belongs entirely to `EventDrivenContact`'s own
event detection (`wheels_ground.tex`: "within each mode the system is a
smooth index-1 DAE"), not to this constraint's own `multiplier_bounds()`.

`v_slip = v_cog . x_hat_0 + omega * (r_z*x0_x - r_x*x0_z - r_k)` (the same
reduced-arm formula `contact.geometry.contact_geometry` computes, with
`r = R(theta) @ axle_point` the axle's world-frame offset from the body's
own CoG) is linear in `u` for fixed `q`, giving `jacobian()` directly.
`residual()` is deliberately `0.0` always: unlike a holonomic joint,
`v_slip = 0` does not integrate to a simple closed-form `c(q)` once the
road is curved (the contact reference `x*` itself moves along the curve
in a way tied to the wheel's own rotation history, i.e. an arc-length
integral, not a point-coincidence difference) -- this constraint is
enforced at the velocity level only (through `jacobian`/`rate_term`), with
no Baumgarte position correction. `rate_term` additionally treats
`x_hat_0`/`x*` as locally constant (the same "quadratic-in-velocity
leftover restricted to this body's own rotation" scope `planar.body
.centripetal` already uses elsewhere) -- a T11-scope simplification to
revisit if its own drift-bound acceptance test needs a sharper derivative
through the road's curvature.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.planar.body import rotation_matrix
from bikephysics.planar.constraints.base import ConstraintKind
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class RollingContact:
    """`v_slip = 0` for wheel `body`, using the ground-frame basis
    `(x_hat_0, z_hat_0)` fixed at construction time (the caller -- T11's
    `EventDrivenContact` -- re-instantiates this constraint with a fresh
    `x_hat_0`/`z_hat_0`/`radius` each time it re-enters stick mode, since
    those are resolved once per stick-phase entry from
    `contact.geometry.contact_geometry`, not re-solved mid-constraint)."""

    name: str
    body: str
    radius: float
    x_hat_0: tuple[float, float]
    axle_point: tuple[float, float] = (0.0, 0.0)
    dim: int = field(default=1, init=False)
    kind: ConstraintKind = field(default="bilateral", init=False)

    def residual(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        # Velocity-level only -- see module docstring.
        return np.zeros(1)

    def jacobian(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        J = np.zeros((1, layout.nq))
        sl = layout.body_slice(self.body)
        theta = layout.theta(q, self.body)
        r = rotation_matrix(theta) @ np.asarray(self.axle_point, dtype=float)
        x0_x, x0_z = self.x_hat_0
        J[0, sl.start] = x0_x
        J[0, sl.start + 1] = x0_z
        J[0, sl.start + 2] = r[1] * x0_x - r[0] * x0_z - self.radius
        return J

    def rate_term(
        self, q: FloatArray, u: FloatArray, layout: SystemLayout, xp: MathBackend
    ) -> FloatArray:
        # Quadratic-in-velocity leftover from this body's own rotation
        # only (x_hat_0/x* held locally constant, per module docstring).
        theta = layout.theta(q, self.body)
        omega = layout.omega(u, self.body)
        r = rotation_matrix(theta) @ np.asarray(self.axle_point, dtype=float)
        x0_x, x0_z = self.x_hat_0
        # d/dt[omega * (r_z*x0_x - r_x*x0_z)] linear-in-accel part is
        # captured by jacobian(); the rate_term is -J_dot @ u, i.e. the
        # remaining quadratic-in-omega term from r's own rotation:
        # d(r)/dt = omega*(r_z, -r_x) (planar.body's convention), so
        # J_dot's angular entry is omega*(dr_z/dt*x0_x - dr_x/dt*x0_z)
        # = omega*(-r_x*x0_x - r_z*x0_z); rate_term = -J_dot@u contributes
        # -omega^2*(-r_x*x0_x - r_z*x0_z) = omega^2*(r_x*x0_x + r_z*x0_z)
        # for the angular column times omega, plus the linear columns'
        # J_dot is zero (x0 held constant). No separate centripetal term
        # from the translational columns since x_hat_0/x0_z are constants
        # here, not state-dependent.
        return np.array([(omega**2) * (r[0] * x0_x + r[1] * x0_z)])

    def multiplier_bounds(self) -> tuple[float, float] | None:
        return None
