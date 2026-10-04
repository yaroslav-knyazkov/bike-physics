"""planar/constraints/revolute.py -- RevoluteJoint (architecture.md S7.4
table's `RevoluteJoint(1, 2a, point=A)` row; latex/2d_assembly/
frame_wheels.tex).

Pins a body-frame point on `body_a` to a body-frame point on `body_b`
(`frame_wheels.tex`: "the same material points at all times"):

    c(q) = world_point(body_b, point_b) - world_point(body_a, point_a) = 0

`dim = 2`, `kind = "bilateral"`. The `Constraint` protocol
(`constraints/base.py`) takes only `(q, [u,] layout, xp)` -- no body
registry -- so `point_a`/`point_b` are plain body-frame `(x, z)` offsets,
already resolved to numbers; `RevoluteJoint.from_bodies` is the
convenience that resolves a *named* point (e.g. `"A"`) against each
body's own `RigidBody.points` once, at construction time, so call sites
don't have to.

`body_b = None` additionally supports a *ground* pivot -- `point_a` on
`body_a` pinned to the fixed world coordinate `point_b` -- which
`frame_wheels.tex`'s body-to-body joints don't need but a standalone
pendulum test (one body + one revolute joint to ground, architecture.md
S7's acceptance tests) does; this is a direct generalization of the same
residual (equivalent to a second body of infinite mass that never moves),
not a new kind of constraint.

Multiplier sign (D-8: "the multipliers *are* the reaction forces", see
`solver.py`'s module docstring for the exact KKT convention): for a
body-to-body joint, `c = p_b - p_a` means the *generalized* reaction
`J^T lambda` is `+lambda` on `body_b`'s slots and `-lambda` on `body_a`'s
-- an ordinary Newton's-third-law pair sharing one multiplier, direction
fixed by which side is labeled `a` vs `b`. For the ground case there is
only one real body, so this module uses `c = p_a - p_fixed` (the opposite
order) specifically so that `lambda` *is*, directly and without a sign
flip, the physical reaction force the ground exerts on `body_a` -- e.g.
the hanging-pin analytic test (`tests/analytic/test_planar_analytic.py`)
gets `lambda_z = +m*g` for a body of mass `m` pinned at its own CoG.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.planar.body import (
    PointSpec,
    RigidBody,
    centripetal,
    rotation_matrix,
    rotation_matrix_dtheta,
)
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
class RevoluteJoint:
    """`world_point(body_b, point_b) == world_point(body_a, point_a)` for
    all time. `body_b = None` pins `point_a` on `body_a` to the fixed
    world coordinate `point_b` instead (a ground pivot); `point_b` is then
    interpreted directly as world, not body-frame, coordinates."""

    name: str
    body_a: str
    point_a: tuple[float, float]
    body_b: str | None
    point_b: tuple[float, float] = (0.0, 0.0)
    dim: int = field(default=2, init=False)
    kind: ConstraintKind = field(default="bilateral", init=False)

    @classmethod
    def from_bodies(
        cls,
        name: str,
        body_a: RigidBody,
        point_a: PointSpec,
        body_b: RigidBody | None,
        point_b: PointSpec,
    ) -> RevoluteJoint:
        """Resolve named points (`RigidBody.points`) into the plain
        `(x, z)` tuples `RevoluteJoint` itself stores. `body_b = None`
        means a ground pivot: `point_b` is then a fixed *world* `(x, z)`
        coordinate (or `None` for the origin), not looked up against any
        body."""
        p_a = tuple(body_a.local_point(point_a))
        if body_b is None:
            p_b = (0.0, 0.0) if point_b is None else tuple(point_b)
            return cls(name, body_a.name, p_a, None, p_b)  # type: ignore[arg-type]
        p_b = tuple(body_b.local_point(point_b))
        return cls(name, body_a.name, p_a, body_b.name, p_b)  # type: ignore[arg-type]

    def residual(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        p_a = _world(q, layout, self.body_a, self.point_a)
        if self.body_b is None:
            # Ground case: `p_a - p_fixed`, the opposite order of the
            # body-to-body case below -- see the module docstring's
            # "Multiplier sign" paragraph for why.
            return p_a - np.asarray(self.point_b, dtype=float)
        p_b = _world(q, layout, self.body_b, self.point_b)
        return p_b - p_a

    def jacobian(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        J = np.zeros((2, layout.nq))
        sl_a = layout.body_slice(self.body_a)
        theta_a = layout.theta(q, self.body_a)
        p_a = np.asarray(self.point_a, dtype=float)

        if self.body_b is None:
            J[:, sl_a.start] = [1.0, 0.0]
            J[:, sl_a.start + 1] = [0.0, 1.0]
            J[:, sl_a.start + 2] = rotation_matrix_dtheta(theta_a) @ p_a
            return J

        J[:, sl_a.start] = [-1.0, 0.0]
        J[:, sl_a.start + 1] = [0.0, -1.0]
        J[:, sl_a.start + 2] = -(rotation_matrix_dtheta(theta_a) @ p_a)

        sl_b = layout.body_slice(self.body_b)
        J[:, sl_b.start] = [1.0, 0.0]
        J[:, sl_b.start + 1] = [0.0, 1.0]
        theta_b = layout.theta(q, self.body_b)
        p_b = np.asarray(self.point_b, dtype=float)
        J[:, sl_b.start + 2] = rotation_matrix_dtheta(theta_b) @ p_b

        return J

    def rate_term(
        self, q: FloatArray, u: FloatArray, layout: SystemLayout, xp: MathBackend
    ) -> FloatArray:
        theta_a = layout.theta(q, self.body_a)
        omega_a = layout.omega(u, self.body_a)
        cent_a = centripetal(theta_a, omega_a, self.point_a)

        if self.body_b is None:
            return cent_a  # matches the ground case's flipped residual order

        theta_b = layout.theta(q, self.body_b)
        omega_b = layout.omega(u, self.body_b)
        cent_b = centripetal(theta_b, omega_b, self.point_b)
        return cent_b - cent_a

    def multiplier_bounds(self) -> tuple[float, float] | None:
        return None
