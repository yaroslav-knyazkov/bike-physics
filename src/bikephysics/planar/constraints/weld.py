"""planar/constraints/weld.py -- WeldJoint: a generic rigid connection
between two bodies (architecture.md S7.2/S7.4's engine machinery; not in
the S7.4 constraint table itself, which only lists the bike-specific
joints -- `WeldJoint` is the generic "lock all 3 relative DOF" primitive
every multibody engine needs, used here to prove two welded bodies behave
as one combined rigid body, architecture.md S7's acceptance tests).

Locks all 3 relative DOF between `body_a` and `body_b`: the position rows
are exactly `RevoluteJoint`'s (a shared material point), plus one more row
fixing the relative orientation:

    c_pos(q)   = world_point(body_b, point_b) - world_point(body_a, point_a)   (2)
    c_theta(q) = theta_b - theta_a - theta_offset                              (1)

`dim = 3`, `kind = "bilateral"`. `theta_offset` defaults to `0.0` (the two
bodies' frames aligned at weld time); the point-coincidence rows default
to each body's own CoG (`(0, 0)`), which is the natural choice for "weld
two bodies into one" -- any other offset is also valid (a weld away from
either CoG), it just stops being the trivial combined-body case.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.planar.body import centripetal, rotation_matrix, rotation_matrix_dtheta
from bikephysics.planar.constraints.base import ConstraintKind
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


def _world(
    q: FloatArray, layout: SystemLayout, body: str, point_local: tuple[float, float]
) -> FloatArray:
    theta = layout.theta(q, body)
    cog = layout.position(q, body)
    return cog + rotation_matrix(theta) @ np.asarray(point_local, dtype=float)


@dataclass(frozen=True)
class WeldJoint:
    """Rigidly connects `body_a` and `body_b`: `point_a`/`point_b`
    (body-frame `(x, z)` offsets, default CoG) coincide, and the two
    bodies' orientations differ by the fixed `theta_offset` (default
    `0.0`) forever."""

    name: str
    body_a: str
    body_b: str
    point_a: tuple[float, float] = (0.0, 0.0)
    point_b: tuple[float, float] = (0.0, 0.0)
    theta_offset: float = 0.0
    dim: int = field(default=3, init=False)
    kind: ConstraintKind = field(default="bilateral", init=False)

    def residual(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        p_a = _world(q, layout, self.body_a, self.point_a)
        p_b = _world(q, layout, self.body_b, self.point_b)
        c_pos = p_b - p_a
        c_theta = layout.theta(q, self.body_b) - layout.theta(q, self.body_a) - self.theta_offset
        return np.array([c_pos[0], c_pos[1], c_theta])

    def jacobian(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        J = np.zeros((3, layout.nq))

        sl_a = layout.body_slice(self.body_a)
        J[0:2, sl_a.start] = [-1.0, 0.0]
        J[0:2, sl_a.start + 1] = [0.0, -1.0]
        theta_a = layout.theta(q, self.body_a)
        p_a = np.asarray(self.point_a, dtype=float)
        J[0:2, sl_a.start + 2] = -(rotation_matrix_dtheta(theta_a) @ p_a)
        J[2, sl_a.start + 2] = -1.0

        sl_b = layout.body_slice(self.body_b)
        J[0:2, sl_b.start] = [1.0, 0.0]
        J[0:2, sl_b.start + 1] = [0.0, 1.0]
        theta_b = layout.theta(q, self.body_b)
        p_b = np.asarray(self.point_b, dtype=float)
        J[0:2, sl_b.start + 2] = rotation_matrix_dtheta(theta_b) @ p_b
        J[2, sl_b.start + 2] = 1.0

        return J

    def rate_term(
        self, q: FloatArray, u: FloatArray, layout: SystemLayout, xp: MathBackend
    ) -> FloatArray:
        theta_a = layout.theta(q, self.body_a)
        omega_a = layout.omega(u, self.body_a)
        theta_b = layout.theta(q, self.body_b)
        omega_b = layout.omega(u, self.body_b)
        cent_a = centripetal(theta_a, omega_a, self.point_a)
        cent_b = centripetal(theta_b, omega_b, self.point_b)
        pos_rate = cent_b - cent_a
        theta_rate = 0.0  # c_theta's Jacobian row is constant: J_dot = 0
        return np.array([pos_rate[0], pos_rate[1], theta_rate])

    def multiplier_bounds(self) -> tuple[float, float] | None:
        return None
