"""Unit tests for `planar.contact.geometry.contact_geometry` (task.md T6;
architecture.md S7.6.1).

These are closed-form geometry checks at a single instant (no integration):
the gap, the rolling-resistance-shifted contact offset, the smoothed
direction indicator, and the slip velocity, each checked against a
hand-derived expected value for simple, exactly-solvable configurations
(flat ground, a constant grade). `test_contact_regularized.py` (the
ticket's analytic/physical acceptance suite) builds on top of this file's
correctness.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from bikephysics.core.road import ConstantGrade, Flat
from bikephysics.planar.body import RigidBody
from bikephysics.planar.contact.geometry import contact_geometry
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


def _layout_q_u(
    x: float, z: float, theta: float, vx: float, vz: float, omega: float
) -> tuple[SystemLayout, FloatArray, FloatArray]:
    layout = SystemLayout(("w",))
    q = np.array([x, z, theta])
    u = np.array([vx, vz, omega])
    return layout, q, u


def test_flat_ground_touching_wheel_has_zero_gap() -> None:
    radius = 0.33
    layout, q, u = _layout_q_u(0.0, radius, 0.0, 0.0, 0.0, 0.0)
    geom = contact_geometry(
        q, u, layout, body="w", radius=radius, c_rr=0.0, road=Flat(), v_eps=0.01
    )
    assert math.isclose(geom.g_N, 0.0, abs_tol=1e-12)
    assert math.isclose(geom.x_star, 0.0, abs_tol=1e-9)
    np.testing.assert_allclose(geom.x_hat_0, [1.0, 0.0], atol=1e-12)
    np.testing.assert_allclose(geom.z_hat_0, [0.0, 1.0], atol=1e-12)


def test_flat_ground_gap_matches_axle_height_minus_radius() -> None:
    radius = 0.33
    for z in (radius + 0.05, radius - 0.002, radius):
        layout, q, u = _layout_q_u(1.5, z, 0.0, 0.0, 0.0, 0.0)
        geom = contact_geometry(
            q, u, layout, body="w", radius=radius, c_rr=0.0, road=Flat(), v_eps=0.01
        )
        np.testing.assert_allclose(geom.g_N, z - radius, atol=1e-9)


def test_contact_offset_includes_rolling_resistance_shift_forward_of_travel() -> None:
    """`A b_k = l_rr*sigma*x_hat_0 - r*z_hat_0` (architecture.md S7.6.1):
    moving forward (`v_x > 0`) gives `sigma ~= +1`, shifting the contact
    offset's `x_hat_0` component forward (positive) by `l_rr = c_rr*r`."""
    radius, c_rr = 0.33, 0.01
    l_rr = c_rr * radius
    layout, q, u = _layout_q_u(0.0, radius, 0.0, 5.0, 0.0, 0.0)
    geom = contact_geometry(
        q, u, layout, body="w", radius=radius, c_rr=c_rr, road=Flat(), v_eps=0.01
    )
    assert geom.sigma > 0.99  # v_x = 5 >> v_eps = 0.01, sign_smooth saturates
    expected_offset = l_rr * geom.sigma * geom.x_hat_0 - radius * geom.z_hat_0
    np.testing.assert_allclose(geom.contact_offset, expected_offset, atol=1e-12)
    expected_point = geom.axle_world + expected_offset
    np.testing.assert_allclose(geom.contact_point_world, expected_point, atol=1e-12)


def test_contact_offset_shift_flips_sign_with_travel_direction() -> None:
    radius, c_rr = 0.33, 0.01
    layout, q, u_fwd = _layout_q_u(0.0, radius, 0.0, 5.0, 0.0, 0.0)
    _, _, u_bwd = _layout_q_u(0.0, radius, 0.0, -5.0, 0.0, 0.0)
    geom_fwd = contact_geometry(
        q, u_fwd, layout, body="w", radius=radius, c_rr=c_rr, road=Flat(), v_eps=0.01
    )
    geom_bwd = contact_geometry(
        q, u_bwd, layout, body="w", radius=radius, c_rr=c_rr, road=Flat(), v_eps=0.01
    )
    assert geom_fwd.sigma > 0.99
    assert geom_bwd.sigma < -0.99
    np.testing.assert_allclose(geom_fwd.contact_offset[0], -geom_bwd.contact_offset[0], atol=1e-12)


def test_v_slip_reduced_arm_drops_l_rr_but_matches_zero_for_pure_rolling() -> None:
    """With the reduced (`l_rr`-dropped) arm, `v_slip = v_x - r*omega`
    (this engine's convention, cross-checked against
    `planar.body`'s validated point-velocity kinematics -- see
    `contact/geometry.py`'s module docstring). Zero exactly for a wheel
    rolling without slip under that convention, *regardless* of `c_rr`
    (confirming the `l_rr` term never reaches `v_slip`'s `x_hat_0`
    component, consistent with wheels_ground.tex's drop-l_rr prescription
    being a no-op for this particular component, not just a simplifying
    approximation)."""
    radius = 0.33
    v_x = 4.0
    omega = v_x / radius  # this engine's rolling-without-slip relation
    for c_rr in (0.0, 0.02):
        layout, q, u = _layout_q_u(0.0, radius, 0.0, v_x, 0.0, omega)
        geom = contact_geometry(
            q, u, layout, body="w", radius=radius, c_rr=c_rr, road=Flat(), v_eps=0.01
        )
        assert math.isclose(geom.v_slip, 0.0, abs_tol=1e-10)


def test_v_slip_nonzero_for_pure_slip_no_rotation() -> None:
    radius = 0.33
    v_x = 3.0
    layout, q, u = _layout_q_u(0.0, radius, 0.0, v_x, 0.0, 0.0)
    geom = contact_geometry(
        q, u, layout, body="w", radius=radius, c_rr=0.0, road=Flat(), v_eps=0.01
    )
    assert math.isclose(geom.v_slip, v_x, abs_tol=1e-10)


def test_g_N_dot_equals_axle_normal_velocity_exactly() -> None:
    """Derived exactly (module docstring point 1): `g_N_dot = v_A .
    z_hat_0(x*)`, with no rotational/l_rr dependence at all."""
    radius = 0.33
    for omega in (0.0, 5.0, -8.0):
        for c_rr in (0.0, 0.03):
            layout, q, u = _layout_q_u(0.0, radius, 0.0, 1.0, -0.4, omega)
            geom = contact_geometry(
                q, u, layout, body="w", radius=radius, c_rr=c_rr, road=Flat(), v_eps=0.01
            )
            expected = float(np.dot(u[:2], geom.z_hat_0))
            assert math.isclose(geom.g_N_dot, expected, abs_tol=1e-10)


def test_g_N_dot_is_zero_for_pure_horizontal_motion() -> None:
    """A wheel sliding/spinning with no vertical axle velocity has
    `g_N_dot = 0`, regardless of spin rate (spin alone does not change
    penetration depth)."""
    radius = 0.33
    for omega in (0.0, 50.0, -50.0):
        layout, q, u = _layout_q_u(0.0, radius, 0.0, 2.0, 0.0, omega)
        geom = contact_geometry(
            q, u, layout, body="w", radius=radius, c_rr=0.0, road=Flat(), v_eps=0.01
        )
        assert math.isclose(geom.g_N_dot, 0.0, abs_tol=1e-12)


def test_constant_grade_contact_frame_matches_grade_angle() -> None:
    theta_grade = 0.2
    road = ConstantGrade(theta=theta_grade)
    radius = 0.33
    x0 = 0.0
    z0 = road.z(x0) + radius / math.cos(theta_grade)
    layout, q, u = _layout_q_u(x0, z0, theta_grade, 0.0, 0.0, 0.0)
    geom = contact_geometry(
        q, u, layout, body="w", radius=radius, c_rr=0.0, road=road, v_eps=0.01
    )
    expected_x_hat_0 = [math.cos(theta_grade), math.sin(theta_grade)]
    np.testing.assert_allclose(geom.x_hat_0, expected_x_hat_0, atol=1e-8)
    assert math.isclose(geom.g_N, 0.0, abs_tol=1e-6)


def test_warm_start_reduces_newton_iterations_to_a_no_op_at_fixed_point() -> None:
    """Passing the previous `x*` back in as `x_guess` (the warm-start
    contract `contact/geometry.py`'s docstring and architecture.md S12.1
    both describe) is idempotent once converged: re-resolving from the
    converged `x*` returns the same value immediately."""
    radius = 0.33
    layout, q, u = _layout_q_u(2.3, radius + 0.01, 0.0, 1.0, 0.0, 0.0)
    geom1 = contact_geometry(
        q, u, layout, body="w", radius=radius, c_rr=0.0, road=Flat(), v_eps=0.01
    )
    geom2 = contact_geometry(
        q, u, layout, body="w", radius=radius, c_rr=0.0, road=Flat(), v_eps=0.01,
        x_guess=geom1.x_star,
    )
    assert math.isclose(geom1.x_star, geom2.x_star, abs_tol=1e-12)


def test_rigid_body_axle_offset_is_honored() -> None:
    """A non-zero wheel CoG offset from the hub (`r_k_a != 0`,
    `core.params.RearWheelParams.r_2a_a`) is supported via `axle_point`."""
    radius = 0.33
    axle_point = (0.0, 0.02)  # CoG sits 2cm below the hub/axle point
    layout, q, u = _layout_q_u(0.0, radius + 0.02, 0.0, 0.0, 0.0, 0.0)
    geom = contact_geometry(
        q, u, layout, body="w", radius=radius, c_rr=0.0, road=Flat(), v_eps=0.01,
        axle_point=axle_point,
    )
    # axle_world should be CoG + (0, 0.02) = (0, radius + 0.04) -> gap = 0.04
    np.testing.assert_allclose(geom.axle_world, [0.0, radius + 0.04], atol=1e-12)
    assert math.isclose(geom.g_N, 0.04, abs_tol=1e-9)


def test_rigid_body_axle_point_also_usable_with_a_real_rigid_body() -> None:
    """Sanity cross-check: `contact_geometry`'s own local `_world_point`
    helper agrees with `planar.body.RigidBody.world_point` for the same
    offset (confirms the duplication documented in the module docstring
    is faithful, not a drifted copy)."""
    radius = 0.33
    body = RigidBody("w", mass=1.0, inertia=1.0, points={"A": (0.0, 0.02)})
    layout, q, u = _layout_q_u(0.7, radius + 0.02, 0.3, 0.0, 0.0, 0.0)
    geom = contact_geometry(
        q, u, layout, body="w", radius=radius, c_rr=0.0, road=Flat(), v_eps=0.01,
        axle_point=(0.0, 0.02),
    )
    expected = body.world_point(q, layout, "A")
    np.testing.assert_allclose(geom.axle_world, expected, atol=1e-12)
