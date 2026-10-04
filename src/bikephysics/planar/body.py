"""planar/body.py -- RigidBody: mass properties, body-frame named points,
rotation, and point kinematics (architecture.md S7.2, S7.3;
latex/2d_models/rigidbody.tex).

Every body is a planar rigid body: CoG position `(x_k, z_k)`, rotation
angle `theta_k`, scalar mass `m_k`, and scalar planar moment of inertia
`I_k` about `y_hat` through its own CoG (rigidbody.tex: "Since motion is
planar, `I_k` is always a scalar ... no inertia tensor needed").

Rotation convention (architecture.md S7.1): `x_hat` forward, `z_hat` up,
`y_hat = z_hat x x_hat` out of the sagittal plane, positive rotation about
`+y_hat`. For an in-plane vector `r = r_x*x_hat + r_z*z_hat`, the
right-handed triple gives `y_hat x r = r_z*x_hat - r_x*z_hat`, which is
what `rotation_matrix`/`point_velocity`/`apply_wrench` below all transcribe
consistently (cross-checked against the small-angle pendulum period and
the hanging-pin reaction-force tests in
`tests/analytic/test_planar_analytic.py`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]

PointSpec = str | tuple[float, float] | None


def rotation_matrix(theta: float) -> FloatArray:
    """`R(theta)`: maps a body-frame `(x, z)` offset to its world-frame
    `(x, z)` components, for architecture.md S7.1's right-handed
    `(x_hat, y_hat, z_hat)` convention with positive rotation about
    `+y_hat`."""
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, s], [-s, c]], dtype=float)


def rotation_matrix_dtheta(theta: float) -> FloatArray:
    """`dR/dtheta`, needed by every constraint Jacobian that involves a
    body-frame offset point (e.g. `RevoluteJoint`, `WeldJoint`)."""
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[-s, c], [-c, -s]], dtype=float)


def centripetal(theta: float, omega: float, local: tuple[float, float] | FloatArray) -> FloatArray:
    """`omega^2 * r`, `r = R(theta) @ local` -- the standalone form of
    `RigidBody.centripetal_term`, usable by a `Constraint` that only has
    plain body-frame offsets (not a `RigidBody` instance) to work with,
    which is the shape `constraints/base.py`'s protocol hands every
    constraint (see `constraints/revolute.py`, `constraints/weld.py`)."""
    r = rotation_matrix(theta) @ np.asarray(local, dtype=float)
    return (omega**2) * r


@dataclass(frozen=True)
class RigidBody:
    """A single planar rigid body. `points` maps a point name (e.g. `"A"`,
    the frame's rear-axle point of `frame_wheels.tex`) to its body-frame
    `(x, z)` offset from the CoG; the CoG itself is always available as
    the implicit point `(0.0, 0.0)` and need not be named.

    `name` must match the body name this body is registered under in the
    owning `MultibodySystem`/`SystemLayout` -- it is how every other
    planar module looks the body up (`SystemLayout.body_slice(name)`).
    """

    name: str
    mass: float
    inertia: float
    points: Mapping[str, tuple[float, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mass <= 0:
            raise ValueError(f"RigidBody {self.name!r}: mass must be > 0, got {self.mass}")
        if self.inertia <= 0:
            raise ValueError(f"RigidBody {self.name!r}: inertia must be > 0, got {self.inertia}")

    def local_point(self, point: PointSpec) -> FloatArray:
        """Resolve `point` into a body-frame `(x, z)` offset: `None` gives
        the CoG `(0.0, 0.0)`; a `str` looks up `points`; an explicit
        `(x, z)` pair is used as-is (e.g. a ground-fixed anchor expressed
        directly, or a point that has no reason to be named)."""
        if point is None:
            return np.zeros(2)
        if isinstance(point, str):
            try:
                return np.asarray(self.points[point], dtype=float)
            except KeyError as exc:
                raise KeyError(
                    f"RigidBody {self.name!r} has no named point {point!r}; "
                    f"known points: {sorted(self.points)}"
                ) from exc
        return np.asarray(point, dtype=float)

    def world_point(
        self, q: FloatArray, layout: SystemLayout, point: PointSpec = None
    ) -> FloatArray:
        """World `(x, z)` of a body-frame point: CoG position plus the
        rotated body-frame offset, `p_world = cog + R(theta) @ p_local`."""
        theta = layout.theta(q, self.name)
        cog = layout.position(q, self.name)
        offset = rotation_matrix(theta) @ self.local_point(point)
        return cog + offset

    def point_velocity(
        self, q: FloatArray, u: FloatArray, layout: SystemLayout, point: PointSpec = None
    ) -> FloatArray:
        """World `(vx, vz)` of a body-frame point: `v_cog + omega_hat x r`,
        with `r` the point's *current* world-frame offset from the CoG.
        Under this module's rotation convention, `omega*y_hat x (r_x*x_hat
        + r_z*z_hat) = omega*(r_z*x_hat - r_x*z_hat)`."""
        theta = layout.theta(q, self.name)
        omega = layout.omega(u, self.name)
        v_cog = layout.velocity(u, self.name)
        r = rotation_matrix(theta) @ self.local_point(point)
        v_rot = omega * np.array([r[1], -r[0]])
        return v_cog + v_rot

    def centripetal_term(
        self, q: FloatArray, u: FloatArray, layout: SystemLayout, point: PointSpec = None
    ) -> FloatArray:
        """`omega^2 * r`, the centripetal (velocity-squared) part of the
        point's acceleration: `a_point = a_cog + alpha_hat x r - omega^2 *
        r`. The `alpha_hat x r` term is linear in the unknown angular
        acceleration and is exactly what a constraint's Jacobian `J`
        captures; `-omega^2 * r` is the leftover quadratic-in-velocity
        term that is *not* linear in the unknowns -- precisely the
        per-body piece every point-coincidence constraint's `-J_dot u`
        (`rate_term`) is built from (see `constraints/revolute.py`,
        `constraints/weld.py`)."""
        theta = layout.theta(q, self.name)
        omega = layout.omega(u, self.name)
        return centripetal(theta, omega, self.local_point(point))


def apply_wrench(
    h: FloatArray,
    layout: SystemLayout,
    body: str,
    q: FloatArray,
    world_force: FloatArray | tuple[float, float],
    world_point: FloatArray | tuple[float, float],
    moment: float = 0.0,
) -> None:
    """Reduce a force `world_force` applied at `world_point` (plus an
    optional free moment `moment`, e.g. an aerodynamic pitching moment that
    is not the result of any offset force) to `(F_x, F_z, M)` about
    `body`'s CoG, and add it into the generalized force vector `h` in
    place (architecture.md S7.3). `q` supplies `body`'s current CoG
    position for the moment-transport term `r x F`
    (`latex/2d_models/rigidbody.tex`'s `M_P = M_{a_k} + r_{a_k/P} x
    F_total`, applied here in reverse: transporting a force at `P` back to
    the CoG). Every `ForceElement` goes through this helper rather than
    writing into `h`'s slices directly, so the force->generalized-force
    reduction lives in exactly one place.
    """
    sl = layout.body_slice(body)
    cog = layout.position(q, body)
    r = np.asarray(world_point, dtype=float) - cog
    f = np.asarray(world_force, dtype=float)
    torque = r[1] * f[0] - r[0] * f[1]  # (r x F)_y = r_z*F_x - r_x*F_z
    h[sl.start] += f[0]
    h[sl.start + 1] += f[1]
    h[sl.start + 2] += moment + torque
