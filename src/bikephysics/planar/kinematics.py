"""planar/kinematics.py -- the consistent initial-condition solver
(architecture.md S7.8).

**Pragmatic simplification of S7.8's literal unknown count, documented
here rather than guessed at silently.** S7.8's prose lists 9 position
unknowns (`x_1, z_1, theta_1, x_2a, z_2a, x_2b, z_2b, x_3b, z_3b`) solved
against "the 7 bilateral constraint residuals plus two contact
conditions" -- but the three `RevoluteJoint`s (architecture.md S7.4) only
ever constrain point *coincidence*, never relative orientation, so once
`(x_1, z_1, theta_1)` are fixed, `(x_2a, z_2a)`, `(x_2b, z_2b)`, `(x_3b,
z_3b)` are each the closed-form solution of their own 2x2 linear system
(`world_point(frame, axle) == world_point(wheel, axle)`), not independent
unknowns a general-purpose root-find needs to discover. This module
therefore solves the genuinely-coupled 3x3 system directly --
`(x_1, z_1, theta_1)` against `(A_x == x_rear_0, g_N_rear == -delta_static
_rear, g_N_front == -delta_static_front)` -- and derives every other
body's position in closed form from the result, which is algebraically
identical to running a 9-unknown root-find to convergence (the extra 6
equations would be satisfied to machine precision at the same solution)
but avoids asking `scipy.optimize.root` to discover a Jacobian structure
that is already known in closed form. `theta_2a = theta_2b = 0` (an
arbitrary wheel-spin reference, since both wheels are balanced by
default, `r_*_a = 0`, so spin angle does not affect axle position at all);
`theta_3b` either satisfies the chain exactly (`engage_chain=True`, the
default) or is taken directly from the caller's `theta_3b0` (useful for
intentionally starting mid-coast with the chain already slack).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
from scipy.optimize import root

from bikephysics.core.road import Road
from bikephysics.planar.assembly import BikeAssembly, static_load_split
from bikephysics.planar.body import rotation_matrix
from bikephysics.planar.solver import solve_accelerations

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class InitialConditionReport:
    """`bike planar initcheck`'s payload (architecture.md S7.8): the
    position/velocity residuals at the solved state, the static normal-
    load split from both this module's own flat-support lever-arm formula
    and from a full static KKT solve of the assembled planar system (which
    should agree with each other, and -- per `validation/cross_tier.py` --
    with `simplified.model.normal_loads` at `x_ddot = 0`), and the derived
    static penetration depth at each wheel."""

    q0: FloatArray
    u0: FloatArray
    position_residual_norm: float
    velocity_residual_norm: float
    static_load_lever_arm: tuple[float, float]
    static_load_full_solve: tuple[float, float]
    delta_static_rear: float
    delta_static_front: float


def _delta_static(assembly: BikeAssembly, r_z_static: float, contact: object) -> float:
    """Static penetration depth implied by the Hunt-Crossley spring alone
    (`R_z = k_N * delta^e` at `delta_dot = 0`), inverted for `delta`."""
    k_n = contact.k_N  # type: ignore[attr-defined]
    e = contact.e  # type: ignore[attr-defined]
    if r_z_static <= 0.0:
        return 0.0
    return (r_z_static / k_n) ** (1.0 / e)


def solve_initial_conditions(
    assembly: BikeAssembly,
    road: Road,
    *,
    x_rear_0: float = 0.0,
    v0: float = 0.0,
    theta_3b0: float | None = None,
    engage_chain: bool = True,
) -> InitialConditionReport:
    """Solve the consistent planar initial condition (architecture.md
    S7.8): position via a 3x3 root-find on `(x_1, z_1, theta_1)` (module
    docstring), seeded analytically on flat ground; velocity via the
    rolling seed `v_1 = v0`, `omega_2a = -v0/r_2a`, `omega_2b = -v0/r_2b`,
    `omega_3b` from the gear ratio (S7.5.2 eq. 7.3), projected onto the
    active constraints' null space so `J @ u0 = 0` to machine precision.
    """
    layout = assembly.layout
    frame = assembly.system.body("1")
    r_2a = assembly.spec.rear.r_2a
    r_2b = assembly.spec.front.r_2b

    r_2a_z_static, r_2b_z_static = static_load_split(assembly.spec)
    delta_rear = _delta_static(assembly, r_2a_z_static, assembly.rear_contact)
    delta_front = _delta_static(assembly, r_2b_z_static, assembly.front_contact)

    a_local = frame.local_point("A")

    def residuals(x: FloatArray) -> FloatArray:
        x1, z1, theta1 = x
        r = rotation_matrix(theta1)
        a_world = np.array([x1, z1]) + r @ a_local
        b_world = np.array([x1, z1]) + r @ frame.local_point("B")

        theta_a = road.theta(a_world[0])
        _, z_hat_a = road.frame0(a_world[0])
        g_n_a = (a_world[1] - road.z(a_world[0])) * np.cos(theta_a) - r_2a

        theta_b = road.theta(b_world[0])
        g_n_b = (b_world[1] - road.z(b_world[0])) * np.cos(theta_b) - r_2b

        return np.array(
            [
                a_world[0] - x_rear_0,
                g_n_a - (-delta_rear),
                g_n_b - (-delta_front),
            ]
        )

    theta0 = road.theta(x_rear_0)
    z0 = road.z(x_rear_0) + r_2a - a_local[1] * np.cos(theta0) - delta_rear
    x0_guess = np.array([x_rear_0 - a_local[0], z0, theta0])

    sol = root(residuals, x0_guess, tol=1e-12)
    x1, z1, theta1 = sol.x
    position_residual_norm = float(np.linalg.norm(sol.fun))

    r1 = rotation_matrix(theta1)
    cog_a = np.array([x1, z1]) + r1 @ a_local
    cog_b = np.array([x1, z1]) + r1 @ frame.local_point("B")
    cog_c = np.array([x1, z1]) + r1 @ frame.local_point("C")

    theta_2a = 0.0
    theta_2b = 0.0
    chain = assembly.drivetrain.chain
    if theta_3b0 is not None:
        theta_3b = theta_3b0
    elif engage_chain:
        # c_chain = r_cr*(theta3b - theta1) - r_cg*(theta2a - theta1) = 0
        # => theta3b = theta1 + (r_cg/r_cr)*(theta2a - theta1)
        theta_3b = theta1 + (chain.r_cog / chain.r_chainring) * (theta_2a - theta1)
    else:
        theta_3b = 0.0

    q0 = np.zeros(layout.nq)
    q0[layout.body_slice("1")] = [x1, z1, theta1]
    q0[layout.body_slice("2a")] = [cog_a[0], cog_a[1], theta_2a]
    q0[layout.body_slice("2b")] = [cog_b[0], cog_b[1], theta_2b]
    q0[layout.body_slice("3b")] = [cog_c[0], cog_c[1], theta_3b]

    # Rolling-without-slip sign: **a corrected reading of architecture.md
    # S7.1**, recorded for architect sign-off (same spirit as
    # `contact/regularized.py`'s two corrections for T6). S7.1 claims
    # "forward rolling gives negative spin, omega_2a = -v_x/r_2a",
    # citing `forward_motion.tex`'s own (z-down-convention) `phi_ddot_2a
    # = -x_ddot/r_2a`. Working the *zero-slip* condition through this
    # engine's own already-built, already-tested rotation convention
    # (`planar.body`'s `y_hat x r = (r_z, -r_x)`, verified self-
    # consistent with `rotation_matrix`'s own derivative) for the contact
    # point at local `(0, -r)` (straight down from the axle) gives
    # `v_contact_x = v_axle_x + omega*r_z = v_axle_x - omega*r`, so
    # `v_contact_x = 0` (no slip) requires `omega = +v_axle_x/r`, the
    # *opposite* sign from S7.1's claim. This is also exactly what T6's
    # own `contact.geometry.contact_geometry`'s `v_slip = v_axle_x0 -
    # radius*omega` formula requires for zero slip -- i.e. T6's contact
    # model (independently built, D-9) and this derivation agree; S7.1's
    # prose does not. The likely cause: `forward_motion.tex`'s `z`-down
    # convention also flips the *sense* of "positive rotation about
    # +y_hat" relative to this engine's `z`-up convention (D-13 notes
    # the translational `z`-sign flip explicitly but not this rotational
    # consequence of it). This file implements the corrected sign;
    # `forces/pedal.py`'s own sign derivation is the other place this
    # matters (see its module docstring) -- both are flagged together
    # for architect/user sign-off.
    omega_2a = v0 / r_2a
    omega_2b = v0 / r_2b
    omega_3b = omega_2a / chain.gear_ratio if engage_chain else 0.0

    # Every body's CoG translates at the same `(v0, 0)` as the frame's own
    # point (the rolling seed assumes level riding, `omega_1 = 0`, so
    # `v_point = v_1 + omega_1 x r = v_1` for every body-frame offset) --
    # this already satisfies every revolute joint's velocity-level
    # residual (and the chain's, via the gear-ratio-consistent spins
    # above) to within floating-point precision, so `_project_velocity`
    # below is a near-identity cleanup, not a large, under-constrained
    # correction (leaving the translational seeds at zero, as an earlier
    # version of this module did, let a minimum-norm projection land on a
    # mathematically-valid but physically nonsensical nearby point on the
    # constraint manifold instead of the intended rolling state).
    u0_raw = np.zeros(layout.nq)
    u0_raw[layout.body_slice("1")] = [v0, 0.0, 0.0]
    u0_raw[layout.body_slice("2a")] = [v0, 0.0, omega_2a]
    u0_raw[layout.body_slice("2b")] = [v0, 0.0, omega_2b]
    u0_raw[layout.body_slice("3b")] = [v0, 0.0, omega_3b]

    active = list(assembly.system.constraints) if engage_chain else [
        c for c in assembly.system.constraints if c.name != chain.name
    ]
    u0 = _project_velocity(assembly, active, q0, u0_raw)

    # Velocity-level residual, re-derived from whichever constraints were
    # actually projected onto (module docstring: engage_chain toggles the
    # chain row in/out).
    J_rows = [c.jacobian(q0, layout, assembly.system.xp) for c in active]
    J = np.vstack(J_rows) if J_rows else np.zeros((0, layout.nq))
    velocity_residual_norm = float(np.linalg.norm(J @ u0)) if J.size else 0.0

    alpha = beta = 1.0 / (10.0 * 1e-3)
    static_u0 = np.zeros(layout.nq)
    solve = solve_accelerations(assembly.system, 0.0, q0, static_u0, alpha=alpha, beta=beta)
    wrench_rear = assembly.rear_contact.wrenches(
        0.0, q0, static_u0, layout, road, assembly.spec, {}
    )[0]
    wrench_front = assembly.front_contact.wrenches(
        0.0, q0, static_u0, layout, road, assembly.spec, {}
    )[0]
    r_z_rear_full = float(np.dot(wrench_rear.force_world, assembly.rear_contact.state().z_hat_0))
    r_z_front_full = float(np.dot(wrench_front.force_world, assembly.front_contact.state().z_hat_0))
    del solve  # only used to force one consistent evaluation; accelerations not reported here

    return InitialConditionReport(
        q0=q0,
        u0=u0,
        position_residual_norm=position_residual_norm,
        velocity_residual_norm=velocity_residual_norm,
        static_load_lever_arm=(r_2a_z_static, r_2b_z_static),
        static_load_full_solve=(r_z_rear_full, r_z_front_full),
        delta_static_rear=delta_rear,
        delta_static_front=delta_front,
    )


def _project_velocity(
    assembly: BikeAssembly, constraints: list, q: FloatArray, u: FloatArray
) -> FloatArray:
    """`u <- u - M^-1 J^T (J M^-1 J^T)^-1 J u` for the given active
    `constraints` (architecture.md S7.8's velocity-projection seed; the
    same formula `planar/integrate.py::project_to_manifold` and
    `planar/drivetrain.py::_project_velocity_onto_chain` both use,
    specialized here to whichever row set the caller chose)."""
    layout = assembly.layout
    if not constraints:
        return u.copy()
    M_inv = np.linalg.inv(assembly.system.mass_matrix())
    J = np.vstack([c.jacobian(q, layout, assembly.system.xp) for c in constraints])
    JMJt = J @ M_inv @ J.T
    return u - M_inv @ J.T @ np.linalg.solve(JMJt, J @ u)
