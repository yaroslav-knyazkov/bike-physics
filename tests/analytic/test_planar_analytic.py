"""Analytic acceptance tests for the planar engine (task.md T5;
architecture.md S7's "Done when" criterion). These are the ticket's real
deliverable: every assertion here is an exact (or near-exact,
floating-point/integration-tolerance-limited) closed-form answer, not a
golden-numeric regression.

- a single free body under gravity reproduces `z = z0 - 1/2*g*t^2`,
  `theta` constant;
- a simple (compound) pendulum matches the small-angle period analytically
  and a high-accuracy reference integration of the equivalent 1-DOF
  minimal-coordinate pendulum ODE;
- a double pendulum conserves energy to < 0.1% over 60 s and keeps
  `max|c| < 1e-8` throughout;
- two welded bodies behave exactly as one body of the combined mass
  properties;
- a revolute joint's multiplier, for a body hanging statically from a pin,
  equals the analytic reaction force (its weight).

No post-step projection is used anywhere in this file -- drift stays
within bound from Baumgarte stabilization alone (architecture.md S7's
"Done when").
"""

from __future__ import annotations

import numpy as np
from scipy.integrate import solve_ivp

from bikephysics.planar.body import RigidBody, rotation_matrix
from bikephysics.planar.constraints.revolute import RevoluteJoint
from bikephysics.planar.constraints.weld import WeldJoint
from bikephysics.planar.integrate import run
from bikephysics.planar.solver import solve_accelerations
from bikephysics.planar.system import MultibodySystem

G = 9.81


# ---------------------------------------------------------------------------
# 1. Free body under gravity: z = z0 - 1/2 g t^2, theta constant.
# ---------------------------------------------------------------------------


def test_free_body_under_gravity_matches_closed_form() -> None:
    m, inertia = 4.0, 0.3
    z0, x0, theta0 = 10.0, 1.0, 0.7

    sys = MultibodySystem()
    sys.add_body(RigidBody("1", mass=m, inertia=inertia))
    sys.add_gravity(g=G)

    q0 = np.array([x0, z0, theta0])
    u0 = np.zeros(3)
    y0 = np.concatenate([q0, u0])

    t_end = 2.0
    t_eval = np.linspace(0.0, t_end, 50)
    result = run(sys, y0, (0.0, t_end), method="RK45", t_eval=t_eval, max_drift=1e-6)

    z_expected = z0 - 0.5 * G * result.t**2
    x_expected = np.full_like(result.t, x0)
    theta_expected = np.full_like(result.t, theta0)

    np.testing.assert_allclose(result.q[:, 1], z_expected, atol=1e-8)
    np.testing.assert_allclose(result.q[:, 0], x_expected, atol=1e-8)
    np.testing.assert_allclose(result.q[:, 2], theta_expected, atol=1e-8)
    assert result.drift.ok()


# ---------------------------------------------------------------------------
# 2. Simple (compound) pendulum: small-angle period + minimal-coordinate
#    reference-ODE cross-check.
# ---------------------------------------------------------------------------


def _pendulum_system(m: float, inertia_cog: float, length: float) -> MultibodySystem:
    """One body + one `RevoluteJoint` to ground (architecture.md S7's own
    phrasing): the pivot sits at body-frame point `(0, length)`, i.e. the
    CoG hangs `length` below the fixed pivot at `(0, 0)`."""
    sys = MultibodySystem()
    body = RigidBody("p", mass=m, inertia=inertia_cog)
    sys.add_body(body)
    sys.add_gravity(g=G)
    joint = RevoluteJoint.from_bodies("pivot", body, (0.0, length), None, (0.0, 0.0))
    sys.add_constraint(joint)
    return sys


def _pendulum_initial_state(theta0: float, length: float) -> np.ndarray:
    pivot_world = np.array([0.0, 0.0])
    cog0 = pivot_world - rotation_matrix(theta0) @ np.array([0.0, length])
    q0 = np.array([cog0[0], cog0[1], theta0])
    u0 = np.zeros(3)
    return np.concatenate([q0, u0])


def test_pendulum_small_angle_period_matches_analytic_formula() -> None:
    m, inertia_cog, length = 1.5, 0.05, 0.8
    inertia_total = inertia_cog + m * length**2
    omega_small = np.sqrt(m * G * length / inertia_total)
    period_analytic = 2 * np.pi / omega_small

    theta0 = 0.01  # small angle
    sys = _pendulum_system(m, inertia_cog, length)
    y0 = _pendulum_initial_state(theta0, length)

    n_periods = 3
    t_eval = np.linspace(0.0, n_periods * period_analytic, 4000)
    result = run(
        sys, y0, (0.0, n_periods * period_analytic), method="Radau", max_drift=1e-6, t_eval=t_eval
    )
    assert result.drift.ok()

    theta = result.q[:, 2]
    t = result.t
    sign_changes = np.where(np.diff(np.sign(theta)) != 0)[0]
    assert len(sign_changes) >= 2
    # Zero-crossings occur every half period; linearly interpolate the exact
    # crossing time between the two bracketing samples rather than using the
    # (coarser) raw sample index, which is what the 2e-3 tolerance below
    # actually resolves.
    crossing_times = []
    for i in sign_changes:
        t0, t1 = t[i], t[i + 1]
        th0, th1 = theta[i], theta[i + 1]
        crossing_times.append(t0 - th0 * (t1 - t0) / (th1 - th0))
    half_periods = np.diff(crossing_times)
    np.testing.assert_allclose(half_periods, period_analytic / 2.0, rtol=2e-3)


def test_pendulum_matches_minimal_coordinate_reference_ode() -> None:
    """Cross-check the maximal-coordinate + multiplier formulation against
    the textbook minimal-coordinate compound-pendulum ODE
    `I_total * theta_ddot = -m*g*length*sin(theta)`, at a non-small angle
    so the comparison actually exercises the nonlinearity."""
    m, inertia_cog, length = 1.5, 0.05, 0.8
    inertia_total = inertia_cog + m * length**2
    theta0 = 0.6

    sys = _pendulum_system(m, inertia_cog, length)
    y0 = _pendulum_initial_state(theta0, length)

    t_end = 5.0
    result = run(
        sys, y0, (0.0, t_end), method="Radau", max_drift=1e-6, rtol=1e-12, atol=1e-12
    )
    assert result.drift.ok()

    def minimal_coordinate_rhs(t: float, y: np.ndarray) -> list[float]:
        theta, omega = y
        return [omega, -(m * G * length / inertia_total) * np.sin(theta)]

    reference = solve_ivp(
        minimal_coordinate_rhs,
        (0.0, t_end),
        [theta0, 0.0],
        method="DOP853",
        rtol=1e-12,
        atol=1e-12,
        dense_output=True,
    )
    theta_reference = reference.sol(result.t)[0]

    np.testing.assert_allclose(result.q[:, 2], theta_reference, atol=1e-6)


# ---------------------------------------------------------------------------
# 3. Double pendulum: energy conservation < 0.1% over 60 s, max|c| < 1e-8.
# ---------------------------------------------------------------------------


def test_double_pendulum_conserves_energy_and_satisfies_constraints() -> None:
    m, inertia_cog, length = 1.0, 0.01, 0.5

    sys = MultibodySystem()
    b1 = RigidBody("b1", mass=m, inertia=inertia_cog)
    b2 = RigidBody("b2", mass=m, inertia=inertia_cog)
    sys.add_body(b1)
    sys.add_body(b2)
    sys.add_gravity(g=G)

    # Pivot 1 is ground-fixed at the origin; pivot 2 connects b1's "lower"
    # point to b2's "upper" point, chaining the two pendulums.
    joint1 = RevoluteJoint.from_bodies("pivot1", b1, (0.0, length), None, (0.0, 0.0))
    joint2 = RevoluteJoint.from_bodies("pivot2", b1, (0.0, -length), b2, (0.0, length))
    sys.add_constraint(joint1)
    sys.add_constraint(joint2)

    theta1_0, theta2_0 = 0.4, -0.2
    pivot1_world = np.array([0.0, 0.0])
    cog1 = pivot1_world - rotation_matrix(theta1_0) @ np.array([0.0, length])
    pivot2_world = cog1 + rotation_matrix(theta1_0) @ np.array([0.0, -length])
    cog2 = pivot2_world - rotation_matrix(theta2_0) @ np.array([0.0, length])

    q0 = np.array([cog1[0], cog1[1], theta1_0, cog2[0], cog2[1], theta2_0])
    u0 = np.zeros(6)
    y0 = np.concatenate([q0, u0])

    t_end = 60.0
    result = run(
        sys, y0, (0.0, t_end), method="Radau", max_drift=1e-8, rtol=1e-10, atol=1e-10
    )
    assert result.drift.max_c < 1e-8
    assert result.drift.ok()

    def total_energy(q: np.ndarray, u: np.ndarray) -> float:
        energy = 0.0
        for i, body in enumerate((b1, b2)):
            sl = slice(3 * i, 3 * i + 3)
            vx, vz, omega = u[sl]
            z = q[sl][1]
            energy += 0.5 * body.mass * (vx**2 + vz**2) + 0.5 * body.inertia * omega**2
            energy += body.mass * G * z
        return energy

    e0 = total_energy(q0, u0)
    energies = np.array([total_energy(result.q[i], result.u[i]) for i in range(len(result.t))])
    max_relative_drift = np.max(np.abs((energies - e0) / e0))
    assert max_relative_drift < 1e-3  # < 0.1%


# ---------------------------------------------------------------------------
# 4. Two welded bodies behave exactly as one combined body.
# ---------------------------------------------------------------------------


def test_welded_bodies_behave_as_one_combined_body() -> None:
    m1, inertia1 = 2.0, 0.3
    m2, inertia2 = 1.0, 0.1

    welded = MultibodySystem()
    b1 = RigidBody("b1", mass=m1, inertia=inertia1)
    b2 = RigidBody("b2", mass=m2, inertia=inertia2)
    welded.add_body(b1)
    welded.add_body(b2)
    welded.add_gravity(g=G)
    welded.add_constraint(WeldJoint("weld", "b1", "b2"))

    combined = MultibodySystem()
    combined.add_body(RigidBody("c", mass=m1 + m2, inertia=inertia1 + inertia2))
    combined.add_gravity(g=G)

    # Coincident CoGs and orientations (offset 0): a valid "weld" state.
    q_welded = np.array([0.5, 3.0, 0.2, 0.5, 3.0, 0.2])
    u_welded = np.array([1.0, -0.5, 0.3, 1.0, -0.5, 0.3])
    q_combined = np.array([0.5, 3.0, 0.2])
    u_combined = np.array([1.0, -0.5, 0.3])

    res_welded = solve_accelerations(welded, 0.0, q_welded, u_welded, alpha=1.0, beta=1.0)
    res_combined = solve_accelerations(combined, 0.0, q_combined, u_combined, alpha=1.0, beta=1.0)

    np.testing.assert_allclose(res_welded.a[0:3], res_combined.a, atol=1e-10)
    np.testing.assert_allclose(res_welded.a[3:6], res_combined.a, atol=1e-10)

    # Also integrate both for a short time and check the trajectories agree.
    y0_welded = np.concatenate([q_welded, u_welded])
    y0_combined = np.concatenate([q_combined, u_combined])
    result_welded = run(welded, y0_welded, (0.0, 1.0), method="Radau", max_drift=1e-6)
    result_combined = run(combined, y0_combined, (0.0, 1.0), method="Radau", max_drift=1e-6)

    np.testing.assert_allclose(result_welded.q[-1, 0:3], result_combined.q[-1, :], atol=1e-6)
    np.testing.assert_allclose(result_welded.q[-1, 3:6], result_combined.q[-1, :], atol=1e-6)


# ---------------------------------------------------------------------------
# 5. A revolute joint's multiplier, for a statically-hanging body, equals
#    the analytic reaction force (its weight).
# ---------------------------------------------------------------------------


def test_hanging_pin_multiplier_equals_weight() -> None:
    m, inertia = 3.0, 0.2
    pivot_z = 5.0

    sys = MultibodySystem()
    body = RigidBody("1", mass=m, inertia=inertia)
    sys.add_body(body)
    sys.add_gravity(g=G)
    # Pivot at the body's own CoG: no moment arm, so the body hangs
    # perfectly still with zero angular acceleration regardless of theta.
    joint = RevoluteJoint.from_bodies("pivot", body, None, None, (0.0, pivot_z))
    sys.add_constraint(joint)

    q = np.array([0.0, pivot_z, 0.0])
    u = np.zeros(3)
    result = solve_accelerations(sys, 0.0, q, u, alpha=1.0, beta=1.0)

    np.testing.assert_allclose(result.a, [0.0, 0.0, 0.0], atol=1e-9)
    np.testing.assert_allclose(result.multipliers["pivot"], [0.0, m * G], atol=1e-9)
