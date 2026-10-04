"""Unit tests for the generic planar engine (task.md T5; architecture.md
S7.1-S7.4, S7.7): `SystemLayout`'s index bookkeeping, `RigidBody`'s point
kinematics, the `Weight`/`Aerodynamic` force elements, the `RevoluteJoint`/
`WeldJoint` constraints, `MultibodySystem`'s assembly, and `solver.py`'s
KKT solve + generic unilateral trial loop. Full end-to-end physics
(free fall, pendulum, double pendulum, welded bodies, hanging-pin
reaction) lives in `tests/analytic/test_planar_analytic.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt
import pytest

from bikephysics.core.aero import ConstantCdA
from bikephysics.core.mathx import MathBackend, numpy_backend
from bikephysics.planar.body import RigidBody, apply_wrench, rotation_matrix, rotation_matrix_dtheta
from bikephysics.planar.constraints.base import Constraint, ConstraintKind
from bikephysics.planar.constraints.revolute import RevoluteJoint
from bikephysics.planar.constraints.weld import WeldJoint
from bikephysics.planar.forces.aerodynamic import Aerodynamic
from bikephysics.planar.forces.weight import Weight
from bikephysics.planar.solver import kkt_solve, solve_accelerations
from bikephysics.planar.state import SystemLayout
from bikephysics.planar.system import MultibodySystem

FloatArray = npt.NDArray[np.float64]

G = 9.81


# ---------------------------------------------------------------------------
# SystemLayout
# ---------------------------------------------------------------------------


def test_layout_body_slices_are_contiguous_and_ordered() -> None:
    layout = SystemLayout(("1", "2a", "2b"))
    assert layout.nq == 9
    assert layout.body_slice("1") == slice(0, 3)
    assert layout.body_slice("2a") == slice(3, 6)
    assert layout.body_slice("2b") == slice(6, 9)
    assert layout.idx_x("2b") == 6
    assert layout.idx_z("2b") == 7
    assert layout.idx_theta("2b") == 8


def test_layout_rejects_duplicate_or_empty_bodies() -> None:
    with pytest.raises(ValueError):
        SystemLayout(("1", "1"))
    with pytest.raises(ValueError):
        SystemLayout(())


def test_layout_unknown_body_raises_keyerror() -> None:
    layout = SystemLayout(("1",))
    with pytest.raises(KeyError):
        layout.body_slice("nope")


def test_layout_named_accessors_round_trip() -> None:
    layout = SystemLayout(("1", "2a"))
    q = np.array([1.0, 2.0, 0.3, 4.0, 5.0, 0.6])
    u = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6])
    assert layout.x(q, "2a") == pytest.approx(4.0)
    assert layout.z(q, "2a") == pytest.approx(5.0)
    assert layout.theta(q, "2a") == pytest.approx(0.6)
    np.testing.assert_allclose(layout.position(q, "2a"), [4.0, 5.0])
    assert layout.vx(u, "2a") == pytest.approx(0.4)
    assert layout.omega(u, "2a") == pytest.approx(0.6)


def test_layout_relative_spin_matches_frame_wheels_definition() -> None:
    # phi_k = theta_k - theta_1 (latex/2d_assembly/frame_wheels.tex)
    layout = SystemLayout(("1", "2a"))
    q = np.array([0.0, 0.0, 0.1, 0.0, 0.0, 0.4])
    u = np.array([0.0, 0.0, 0.5, 0.0, 0.0, 2.0])
    assert layout.phi(q, "2a") == pytest.approx(0.3)
    assert layout.phi_rate(u, "2a") == pytest.approx(1.5)


def test_layout_constraint_row_map_and_total_dim() -> None:
    @dataclass(frozen=True)
    class _Dummy:
        name: str
        dim: int

    cons = [_Dummy("a", 2), _Dummy("b", 1), _Dummy("c", 3)]
    rows = SystemLayout.constraint_row_map(cons)
    assert rows == {"a": slice(0, 2), "b": slice(2, 3), "c": slice(3, 6)}
    assert SystemLayout.total_dim(cons) == 6


def test_layout_jac_sparsity_kinematic_block_is_identity() -> None:
    layout = SystemLayout(("1", "2a"))
    pattern = layout.jac_sparsity().toarray()
    nq = layout.nq
    assert pattern.shape == (2 * nq, 2 * nq)
    np.testing.assert_array_equal(pattern[0:nq, 0:nq], np.zeros((nq, nq), dtype=bool))
    np.testing.assert_array_equal(pattern[0:nq, nq:], np.eye(nq, dtype=bool))
    assert pattern[nq:, :].all()


# ---------------------------------------------------------------------------
# body.py: rotation, points, wrench reduction
# ---------------------------------------------------------------------------


def test_rotation_matrix_is_orthogonal_and_identity_at_zero() -> None:
    for theta in (0.0, 0.3, -1.1, 2.7):
        R = rotation_matrix(theta)
        np.testing.assert_allclose(R @ R.T, np.eye(2), atol=1e-12)
        assert np.linalg.det(R) == pytest.approx(1.0)
    np.testing.assert_allclose(rotation_matrix(0.0), np.eye(2), atol=1e-12)


def test_rotation_matrix_dtheta_matches_finite_difference() -> None:
    theta = 0.37
    h = 1e-6
    numeric = (rotation_matrix(theta + h) - rotation_matrix(theta - h)) / (2 * h)
    np.testing.assert_allclose(rotation_matrix_dtheta(theta), numeric, atol=1e-6)


def test_world_point_and_point_velocity_consistent_with_finite_difference() -> None:
    """d/dt[world_point] must equal point_velocity along a rotating body's
    own motion (no translation, pure spin), cross-checking `rotation_matrix`,
    `point_velocity`, and the (r_z, -r_x) rotation-rate formula together."""
    layout = SystemLayout(("1",))
    body = RigidBody("1", mass=1.0, inertia=1.0, points={"P": (0.3, -0.2)})
    omega = 1.7
    theta0 = 0.5
    h = 1e-6

    def q_at(theta: float) -> np.ndarray:
        return np.array([0.0, 0.0, theta])

    u0 = np.array([0.0, 0.0, omega])
    p_plus = body.world_point(q_at(theta0 + omega * h), layout, "P")
    p_minus = body.world_point(q_at(theta0 - omega * h), layout, "P")
    numeric_velocity = (p_plus - p_minus) / (2 * h)

    analytic_velocity = body.point_velocity(q_at(theta0), u0, layout, "P")
    np.testing.assert_allclose(analytic_velocity, numeric_velocity, atol=1e-5)


def test_local_point_resolution() -> None:
    body = RigidBody("1", mass=1.0, inertia=1.0, points={"A": (1.0, 2.0)})
    np.testing.assert_allclose(body.local_point(None), [0.0, 0.0])
    np.testing.assert_allclose(body.local_point("A"), [1.0, 2.0])
    np.testing.assert_allclose(body.local_point((3.0, 4.0)), [3.0, 4.0])
    with pytest.raises(KeyError):
        body.local_point("missing")


def test_rigid_body_rejects_nonpositive_mass_or_inertia() -> None:
    with pytest.raises(ValueError):
        RigidBody("1", mass=0.0, inertia=1.0)
    with pytest.raises(ValueError):
        RigidBody("1", mass=1.0, inertia=-1.0)


def test_apply_wrench_force_at_cog_produces_no_torque() -> None:
    layout = SystemLayout(("1",))
    q = np.array([2.0, 3.0, 0.0])
    h = np.zeros(3)
    apply_wrench(h, layout, "1", q, world_force=(5.0, -7.0), world_point=(2.0, 3.0))
    np.testing.assert_allclose(h, [5.0, -7.0, 0.0])


def test_apply_wrench_offset_force_produces_torque() -> None:
    layout = SystemLayout(("1",))
    q = np.array([0.0, 0.0, 0.0])
    h = np.zeros(3)
    # Force (1, 0) applied 1 m above the CoG (r = (0, 1)):
    # torque = r_z*F_x - r_x*F_z = 1*1 - 0*0 = 1.
    apply_wrench(h, layout, "1", q, world_force=(1.0, 0.0), world_point=(0.0, 1.0))
    np.testing.assert_allclose(h, [1.0, 0.0, 1.0])


def test_apply_wrench_free_moment_adds_directly() -> None:
    layout = SystemLayout(("1",))
    q = np.array([0.0, 0.0, 0.0])
    h = np.zeros(3)
    apply_wrench(h, layout, "1", q, world_force=(0.0, 0.0), world_point=(0.0, 0.0), moment=2.5)
    np.testing.assert_allclose(h, [0.0, 0.0, 2.5])


def test_centripetal_term_magnitude_and_direction() -> None:
    layout = SystemLayout(("1",))
    body = RigidBody("1", mass=1.0, inertia=1.0)
    q = np.array([0.0, 0.0, 0.0])
    u = np.array([0.0, 0.0, 2.0])
    cent = body.centripetal_term(q, u, layout, (1.0, 0.0))
    # r = (1, 0), omega = 2 -> omega^2 * r = (4, 0)
    np.testing.assert_allclose(cent, [4.0, 0.0])


# ---------------------------------------------------------------------------
# Force elements
# ---------------------------------------------------------------------------


def test_weight_force_adds_minus_mg_in_z_only() -> None:
    layout = SystemLayout(("1",))
    q = np.array([0.0, 10.0, 0.4])
    u = np.zeros(3)
    h = np.zeros(3)
    w = Weight(name="w", body="1", mass=2.0, g=G)
    w.accumulate(0.0, q, u, layout, h, xp=numpy_backend)
    np.testing.assert_allclose(h, [0.0, -2.0 * G, 0.0])
    assert w.diagnostics(0.0, q, u, layout) == {}


def test_aerodynamic_drag_opposes_forward_motion_on_body_1() -> None:
    layout = SystemLayout(("1",))
    q = np.array([0.0, 0.0, 0.0])
    u = np.array([10.0, 0.0, 0.0])  # moving forward at 10 m/s, no pitch
    h = np.zeros(3)
    model = ConstantCdA(C_1_x_0=1.0, A_1=0.5)
    aero = Aerodynamic(name="aero", aero_model=model, rho=1.225, mu=1.8e-5)
    aero.accumulate(0.0, q, u, layout, h, xp=numpy_backend)
    # Drag must oppose forward motion: Fx < 0, no side/pitch effect for ConstantCdA.
    assert h[0] < 0.0
    assert h[1] == pytest.approx(0.0)
    assert h[2] == pytest.approx(0.0)
    diag = aero.diagnostics(0.0, q, u, layout)
    assert diag["A_1_x"] > 0.0


# ---------------------------------------------------------------------------
# Constraints: RevoluteJoint, WeldJoint
# ---------------------------------------------------------------------------


def _finite_difference_jacobian(
    con: Constraint, q: FloatArray, layout: SystemLayout, xp: MathBackend = numpy_backend
) -> FloatArray:
    h = 1e-6
    nq = layout.nq
    dim = con.dim
    J = np.zeros((dim, nq))
    for i in range(nq):
        dq = np.zeros(nq)
        dq[i] = h
        c_plus = con.residual(q + dq, layout, xp)
        c_minus = con.residual(q - dq, layout, xp)
        J[:, i] = (c_plus - c_minus) / (2 * h)
    return J


def test_revolute_joint_body_to_body_residual_and_jacobian() -> None:
    layout = SystemLayout(("1", "2a"))
    body1 = RigidBody("1", mass=1.0, inertia=1.0, points={"A": (0.5, -0.2)})
    body2a = RigidBody("2a", mass=1.0, inertia=1.0)
    joint = RevoluteJoint.from_bodies("A", body1, "A", body2a, None)

    q = np.array([0.0, 0.0, 0.1, 1.0, 2.0, 0.3])
    c = joint.residual(q, layout, numpy_backend)
    J = joint.jacobian(q, layout, numpy_backend)
    J_fd = _finite_difference_jacobian(joint, q, layout)
    np.testing.assert_allclose(J, J_fd, atol=1e-5)
    assert c.shape == (2,)
    assert joint.dim == 2
    assert joint.kind == "bilateral"
    assert joint.multiplier_bounds() is None


def test_revolute_joint_ground_pivot_residual_and_jacobian() -> None:
    layout = SystemLayout(("1",))
    body1 = RigidBody("1", mass=1.0, inertia=1.0)
    joint = RevoluteJoint.from_bodies("pivot", body1, None, None, (0.0, 5.0))

    q = np.array([0.1, 4.9, 0.2])
    J = joint.jacobian(q, layout, numpy_backend)
    J_fd = _finite_difference_jacobian(joint, q, layout)
    np.testing.assert_allclose(J, J_fd, atol=1e-5)


def test_revolute_joint_rate_term_matches_finite_difference_of_jacobian() -> None:
    """rate_term == -J_dot @ u; check by advancing q along u by a small dt
    and finite-differencing J(q(t)) @ u."""
    layout = SystemLayout(("1",))
    body1 = RigidBody("1", mass=1.0, inertia=1.0)
    joint = RevoluteJoint.from_bodies("pivot", body1, (0.2, 0.3), None, (0.0, 5.0))

    q = np.array([0.1, 4.9, 0.2])
    u = np.array([0.5, -0.3, 1.2])
    h = 1e-6
    Jp = joint.jacobian(q + h * u, layout, numpy_backend)
    Jm = joint.jacobian(q - h * u, layout, numpy_backend)
    J_dot_u = (Jp - Jm) / (2 * h) @ u
    rate = joint.rate_term(q, u, layout, numpy_backend)
    np.testing.assert_allclose(rate, -J_dot_u, atol=1e-4)


def test_weld_joint_residual_jacobian_and_rate_term() -> None:
    layout = SystemLayout(("1", "2"))
    joint = WeldJoint("weld", "1", "2", theta_offset=0.1)

    q = np.array([0.0, 0.0, 0.2, 0.5, -0.3, 0.3])
    c = joint.residual(q, layout, numpy_backend)
    assert c.shape == (3,)
    assert joint.dim == 3
    assert joint.kind == "bilateral"

    J = joint.jacobian(q, layout, numpy_backend)
    J_fd = _finite_difference_jacobian(joint, q, layout)
    np.testing.assert_allclose(J, J_fd, atol=1e-5)

    u = np.array([0.5, -0.3, 1.2, 0.1, 0.2, -0.4])
    h = 1e-6
    Jp = joint.jacobian(q + h * u, layout, numpy_backend)
    Jm = joint.jacobian(q - h * u, layout, numpy_backend)
    J_dot_u = (Jp - Jm) / (2 * h) @ u
    rate = joint.rate_term(q, u, layout, numpy_backend)
    np.testing.assert_allclose(rate, -J_dot_u, atol=1e-4)


def test_weld_joint_theta_offset_zero_residual_when_satisfied() -> None:
    layout = SystemLayout(("1", "2"))
    joint = WeldJoint("weld", "1", "2", theta_offset=0.25)
    # Coincident CoGs, theta_2 - theta_1 == theta_offset exactly.
    q = np.array([1.0, 2.0, 0.4, 1.0, 2.0, 0.65])
    c = joint.residual(q, layout, numpy_backend)
    np.testing.assert_allclose(c, [0.0, 0.0, 0.0], atol=1e-12)


# ---------------------------------------------------------------------------
# MultibodySystem assembly
# ---------------------------------------------------------------------------


def test_mass_matrix_is_block_diagonal_and_constant() -> None:
    sys = MultibodySystem()
    sys.add_body(RigidBody("1", mass=2.0, inertia=0.5))
    sys.add_body(RigidBody("2a", mass=1.0, inertia=0.1))
    M = sys.mass_matrix()
    expected = np.diag([2.0, 2.0, 0.5, 1.0, 1.0, 0.1])
    np.testing.assert_allclose(M, expected)
    # Cached: same array object/content on a second call.
    assert sys.mass_matrix() is M


def test_add_body_after_layout_built_raises() -> None:
    sys = MultibodySystem()
    sys.add_body(RigidBody("1", mass=1.0, inertia=1.0))
    _ = sys.layout
    with pytest.raises(RuntimeError):
        sys.add_body(RigidBody("2", mass=1.0, inertia=1.0))


def test_duplicate_body_name_raises() -> None:
    sys = MultibodySystem()
    sys.add_body(RigidBody("1", mass=1.0, inertia=1.0))
    with pytest.raises(ValueError):
        sys.add_body(RigidBody("1", mass=2.0, inertia=2.0))


def test_generalized_force_is_additive_across_elements() -> None:
    sys = MultibodySystem()
    sys.add_body(RigidBody("1", mass=1.0, inertia=1.0))
    sys.add_force(Weight(name="w1", body="1", mass=1.0, g=G))
    sys.add_force(Weight(name="w2", body="1", mass=1.0, g=G))
    q = np.array([0.0, 0.0, 0.0])
    u = np.zeros(3)
    h = sys.generalized_force(0.0, q, u)
    # Two 1 kg "weight" elements should sum like one 2 kg weight.
    np.testing.assert_allclose(h, [0.0, -2.0 * G, 0.0])


def test_add_gravity_uses_each_bodys_own_mass() -> None:
    sys = MultibodySystem()
    sys.add_body(RigidBody("1", mass=3.0, inertia=1.0))
    sys.add_body(RigidBody("2a", mass=5.0, inertia=1.0))
    sys.add_gravity(g=G)
    q = np.zeros(6)
    u = np.zeros(6)
    h = sys.generalized_force(0.0, q, u)
    np.testing.assert_allclose(h, [0.0, -3.0 * G, 0.0, 0.0, -5.0 * G, 0.0])


def test_constraint_block_zeta_applies_baumgarte_terms() -> None:
    sys = MultibodySystem()
    sys.add_body(RigidBody("1", mass=1.0, inertia=1.0))
    body1 = RigidBody("1", mass=1.0, inertia=1.0)
    joint = RevoluteJoint.from_bodies("pivot", body1, None, None, (0.0, 0.0))
    sys.add_constraint(joint)

    q = np.array([0.1, 0.2, 0.0])  # c = (0.1, 0.2) != 0: drift present
    u = np.array([0.3, 0.4, 0.0])
    alpha, beta = 2.0, 3.0
    block = sys.constraint_block(q, u, alpha=alpha, beta=beta)
    expected_c = np.array([0.1, 0.2])
    expected_cdot = np.array([0.3, 0.4])
    np.testing.assert_allclose(block.c, expected_c)
    np.testing.assert_allclose(block.c_dot, expected_cdot)
    expected_zeta = block.rate - 2.0 * alpha * expected_cdot - beta**2 * expected_c
    np.testing.assert_allclose(block.zeta, expected_zeta)


# ---------------------------------------------------------------------------
# solver.py: kkt_solve primitive + generic unilateral trial loop
# ---------------------------------------------------------------------------


def test_kkt_solve_matches_direct_dense_solve() -> None:
    rng = np.random.default_rng(0)
    M = np.diag(rng.uniform(0.5, 2.0, size=4))
    J = rng.normal(size=(2, 4))
    h = rng.normal(size=4)
    zeta = rng.normal(size=2)

    a, lam = kkt_solve(M, h, J, zeta)

    n = 4 + 2
    A = np.zeros((n, n))
    A[:4, :4] = M
    A[:4, 4:] = J.T
    A[4:, :4] = J
    rhs = np.concatenate([h, zeta])
    sol = np.linalg.solve(A, rhs)
    np.testing.assert_allclose(a, sol[:4], atol=1e-10)
    np.testing.assert_allclose(lam, -sol[4:], atol=1e-10)

    # Sanity: the solved acceleration must satisfy J @ a == zeta.
    np.testing.assert_allclose(J @ a, zeta, atol=1e-10)


@dataclass(frozen=True)
class _VerticalTether:
    """A dummy, non-physical "rope" constraint used purely to exercise
    `solver.py`'s generic unilateral trial loop (architecture.md S7.7 step
    5) in this ticket, ahead of T6's contact normals / T7's chain
    freewheel reusing the same mechanism. Tension-only: `multiplier_bounds
    == (0, inf)`."""

    name: str
    body: str
    z_anchor: float
    length: float
    dim: int = field(default=1, init=False)
    kind: ConstraintKind = field(default="unilateral", init=False)

    def residual(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        z_rest = self.z_anchor - self.length
        return np.array([layout.z(q, self.body) - z_rest])

    def jacobian(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        J = np.zeros((1, layout.nq))
        J[0, layout.idx_z(self.body)] = 1.0
        return J

    def rate_term(
        self, q: FloatArray, u: FloatArray, layout: SystemLayout, xp: MathBackend
    ) -> FloatArray:
        return np.zeros(1)  # constant Jacobian row -> J_dot == 0

    def multiplier_bounds(self) -> tuple[float, float] | None:
        return (0.0, np.inf)


def test_unilateral_trial_keeps_taut_tether_active_with_positive_tension() -> None:
    m = 2.0
    anchor_z, length = 5.0, 3.0
    z_rest = anchor_z - length

    sys = MultibodySystem()
    sys.add_body(RigidBody("b", mass=m, inertia=0.1))
    sys.add_gravity(g=G)
    sys.add_constraint(_VerticalTether("tether", "b", anchor_z, length))

    q = np.array([0.0, z_rest, 0.0])
    u = np.zeros(3)
    result = solve_accelerations(sys, 0.0, q, u, alpha=1.0, beta=1.0)

    assert result.active_names == ("tether",)
    assert result.dropped_names == ()
    np.testing.assert_allclose(result.a, [0.0, 0.0, 0.0], atol=1e-9)
    np.testing.assert_allclose(result.multipliers["tether"], [m * G], atol=1e-9)


def test_unilateral_trial_drops_slack_tether_and_body_falls_freely() -> None:
    """With gravity replaced by an upward push toward the anchor, the
    tether would need to go into compression to hold position -- the
    trial loop must drop it, leaving the body to accelerate freely under
    the applied force alone."""
    m = 2.0
    anchor_z, length = 5.0, 3.0
    z_rest = anchor_z - length
    F = 20.0

    sys = MultibodySystem()
    sys.add_body(RigidBody("b", mass=m, inertia=0.1))
    sys.add_force(Weight(name="push", body="b", mass=m, g=-F / m))
    sys.add_constraint(_VerticalTether("tether", "b", anchor_z, length))

    q = np.array([0.0, z_rest, 0.0])
    u = np.zeros(3)
    result = solve_accelerations(sys, 0.0, q, u, alpha=1.0, beta=1.0)

    assert result.active_names == ()
    assert result.dropped_names == ("tether",)
    np.testing.assert_allclose(result.a, [0.0, F / m, 0.0], atol=1e-9)
