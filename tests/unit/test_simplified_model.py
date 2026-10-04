"""Tests for bikephysics.simplified.model (architecture.md S6.1, task.md T3;
`latex/simplified/forward_motion.tex` Bike/Wheels/Drivetrain/Assembly)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bikephysics.core.aero import AeroState
from bikephysics.simplified import model
from tests.unit.factories import make_minimal_bike_spec

ZERO_AERO = AeroState(A_1_x=0.0, A_1_z=0.0, A_1_m=0.0, beta_rel=0.0, Re=0.0, q=0.0)


def test_check_equal_wheels_raises_on_mismatched_radius() -> None:
    spec = make_minimal_bike_spec(
        front={"m_2b": 1.8, "I_2b": 0.14, "r_2b": 0.34, "c_2b_rr": 0.005, "r_2b_bk": 0.31}
    )
    with pytest.raises(ValueError, match="r_2a == front.r_2b"):
        model.m_eq(spec)


def test_check_equal_wheels_raises_on_mismatched_rolling_resistance() -> None:
    spec = make_minimal_bike_spec(
        front={"m_2b": 1.8, "I_2b": 0.14, "r_2b": 0.335, "c_2b_rr": 0.01, "r_2b_bk": 0.31}
    )
    with pytest.raises(ValueError, match="c_2a_rr == front.c_2b_rr"):
        model.m_eq(spec)


def test_m_eq_matches_boxed_formula() -> None:
    spec = make_minimal_bike_spec()
    r = spec.rear.r_2a
    expected = (
        spec.M
        + (spec.rear.I_2a + spec.front.I_2b) / r**2
        + spec.drivetrain.I_3b * spec.drivetrain.r_2a_cg**2 / (spec.drivetrain.r_3b_cr**2 * r**2)
    )
    assert model.m_eq(spec) == pytest.approx(expected)
    # BikeSpec.M_eq is the same boxed equation (core.params.py); must agree.
    assert model.m_eq(spec) == pytest.approx(spec.m_eq)


def test_chain_tension_matches_boxed_formula() -> None:
    spec = make_minimal_bike_spec()
    dt = spec.drivetrain
    r_2a = spec.rear.r_2a
    x_ddot, p_d, p_f = 0.5, 120.0, 80.0
    expected = -(dt.I_3b * dt.r_2a_cg) / (dt.r_3b_cr**2 * r_2a) * x_ddot + (
        spec.frame.r_1_pedal / dt.r_3b_cr
    ) * (p_d + p_f)
    assert model.chain_tension(spec, x_ddot, p_d, p_f) == pytest.approx(expected)


def test_chain_tension_is_array_safe() -> None:
    spec = make_minimal_bike_spec()
    x_ddot = np.array([0.0, 1.0, 2.0])
    p_d = np.full_like(x_ddot, 100.0)
    p_f = np.full_like(x_ddot, 100.0)
    out = model.chain_tension(spec, x_ddot, p_d, p_f)
    assert out.shape == (3,)


def test_normal_loads_static_case_matches_hand_derivation() -> None:
    """x_ddot=0, theta=0, no aero: the classic static load split, with the
    rolling-resistance-shift correction (`l_C'`/`l_D'`) included."""
    spec = make_minimal_bike_spec()
    l_c, l_d, h, length = model._geometry(spec)
    assert h == pytest.approx(spec.frame.h_1_a + spec.rear.r_2a)
    assert length == pytest.approx(spec.frame.l_1_B)

    r_2a_z, r_2b_z = model.normal_loads(spec, 0.0, 0.0, 0.0, 0.0, 1.0)

    l_2a_rr = spec.rear.c_2a_rr * spec.rear.r_2a
    l_2b_rr = spec.front.c_2b_rr * spec.front.r_2b
    l_c_p = l_c - l_2a_rr
    l_d_p = l_d + l_2b_rr
    m_g = spec.M * spec.env.g
    expected_r_2a_z = l_d_p * m_g / length
    expected_r_2b_z = l_c_p * m_g / length

    assert r_2a_z == pytest.approx(expected_r_2a_z)
    assert r_2b_z == pytest.approx(expected_r_2b_z)
    # Shared convention: both loaded wheels have R_z >= 0.
    assert r_2a_z > 0
    assert r_2b_z > 0


@pytest.mark.parametrize("x_ddot", [-5.0, -1.0, 0.0, 1.0, 3.0])
@pytest.mark.parametrize("theta", [-0.1, 0.0, 0.05, 0.2])
def test_normal_loads_sum_to_shared_Mg_cos_theta(x_ddot: float, theta: float) -> None:
    """Mandatory assertion (task.md T3): normal_loads sum to `+Mg*cos(theta)`
    in the shared (z-up) convention at every acceleration -- exact because
    rear/front rolling-resistance shifts cancel under the equal-wheels
    assumption (`l_C' + l_D' == L`)."""
    spec = make_minimal_bike_spec()
    aero = AeroState(A_1_x=3.0, A_1_z=0.0, A_1_m=0.5, beta_rel=0.0, Re=0.0, q=0.0)
    r_2a_z, r_2b_z = model.normal_loads(spec, x_ddot, theta, aero.A_1_x, aero.A_1_m, 1.0)
    assert r_2a_z + r_2b_z == pytest.approx(spec.M * spec.env.g * math.cos(theta), rel=1e-10)


def test_normal_loads_doc_convention_is_negative_internally() -> None:
    """The D-13 flip: the un-flipped (doc, z-down) values are negative
    under normal loaded operation ("Limits": R_z <= 0 means no lift),
    the opposite sign of the shared values this module actually returns."""
    spec = make_minimal_bike_spec()
    r_2a_z_shared, r_2b_z_shared = model.normal_loads(spec, 0.0, 0.0, 0.0, 0.0, 1.0)
    assert model._z_flip(r_2a_z_shared) < 0
    assert model._z_flip(r_2b_z_shared) < 0
    assert model._z_flip(model._z_flip(r_2a_z_shared)) == pytest.approx(r_2a_z_shared)


def test_traction_rolling_resistance_opposes_motion_at_rest_coast() -> None:
    """At x_ddot=0, theta=0, T=B=0, sigma=1: the only rear/front traction
    terms are the rolling-resistance ones, `c_rr*sigma*R_z_doc`, which must
    come out *negative* (opposing forward motion) -- this only happens if
    `traction` correctly flips its shared-convention `R_z` arguments back
    to doc convention before using them; using the raw shared (positive)
    values would flip the sign and give a forward-aiding force instead."""
    spec = make_minimal_bike_spec()
    r_2a_z, r_2b_z = model.normal_loads(spec, 0.0, 0.0, 0.0, 0.0, 1.0)
    r_2a_x, r_2b_x = model.traction(spec, 0.0, 0.0, r_2a_z, r_2b_z, 0.0, 0.0, 1.0)

    expected_r_2a_x = spec.rear.c_2a_rr * 1.0 * model._z_flip(r_2a_z)
    expected_r_2b_x = spec.front.c_2b_rr * 1.0 * model._z_flip(r_2b_z)
    assert r_2a_x == pytest.approx(expected_r_2a_x)
    assert r_2b_x == pytest.approx(expected_r_2b_x)
    assert r_2a_x < 0
    assert r_2b_x < 0


def test_traction_matches_boxed_formula_general_case() -> None:
    spec = make_minimal_bike_spec()
    x_ddot, tension, b_2a, b_2b, sigma = 0.7, 15.0, 2.0, 1.0, 1.0
    r_2a_z, r_2b_z = 300.0, 250.0  # shared convention, arbitrary but plausible

    r_2a_x, r_2b_x = model.traction(spec, x_ddot, tension, r_2a_z, r_2b_z, b_2a, b_2b, sigma)

    r_2a_z_doc = -r_2a_z
    r_2b_z_doc = -r_2b_z
    expected_r_2a_x = (
        -(spec.rear.I_2a / spec.rear.r_2a**2) * x_ddot
        + spec.rear.c_2a_rr * sigma * r_2a_z_doc
        + (spec.drivetrain.r_2a_cg / spec.rear.r_2a) * tension
        - (sigma * spec.rear.r_2a_bk / spec.rear.r_2a) * b_2a
    )
    expected_r_2b_x = (
        -(spec.front.I_2b / spec.front.r_2b**2) * x_ddot
        + spec.front.c_2b_rr * sigma * r_2b_z_doc
        - (sigma * spec.front.r_2b_bk / spec.front.r_2b) * b_2b
    )
    assert r_2a_x == pytest.approx(expected_r_2a_x)
    assert r_2b_x == pytest.approx(expected_r_2b_x)


def test_accel_zero_input_on_flat_ground_gives_zero() -> None:
    spec = make_minimal_bike_spec()
    x_ddot = model.accel(spec, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, ZERO_AERO)
    assert x_ddot == pytest.approx(0.0, abs=1e-9)


def test_accel_matches_boxed_formula() -> None:
    spec = make_minimal_bike_spec()
    v, theta, p_d, p_f, b_2a, b_2b = 4.0, 0.03, 150.0, 90.0, 5.0, 3.0
    aero = AeroState(A_1_x=12.0, A_1_z=0.0, A_1_m=0.0, beta_rel=0.0, Re=0.0, q=0.0)

    x_ddot = model.accel(spec, v, theta, p_d, p_f, b_2a, b_2b, aero)

    sigma = math.tanh(v / spec.contact.v_eps)
    dt = spec.drivetrain
    r_2a = spec.rear.r_2a
    big_m = spec.M
    g = spec.env.g
    rhs = (
        (spec.frame.r_1_pedal * dt.r_2a_cg) / (dt.r_3b_cr * r_2a) * (p_d + p_f)
        - (sigma / r_2a) * (spec.rear.r_2a_bk * b_2a + spec.front.r_2b_bk * b_2b)
        - big_m * g * math.sin(theta)
        - sigma * spec.rear.c_2a_rr * big_m * g * math.cos(theta)
        - aero.A_1_x
    )
    expected = rhs / model.m_eq(spec)
    assert x_ddot == pytest.approx(expected, rel=1e-9)


def test_accel_rolling_resistance_sign_flips_with_velocity_direction() -> None:
    """Coasting downhill, zero pedal/brake/aero: at a small positive v the
    rolling-resistance term resists forward motion (reduces acceleration);
    at a small negative v (rolling backward) it must resist the other way
    -- `sign_smooth` makes this a smooth, not a discontinuous, flip."""
    spec = make_minimal_bike_spec()
    theta = 0.05  # downhill-equivalent sign convention check: any nonzero theta works
    a_pos = model.accel(spec, 2.0, theta, 0.0, 0.0, 0.0, 0.0, ZERO_AERO)
    a_neg = model.accel(spec, -2.0, theta, 0.0, 0.0, 0.0, 0.0, ZERO_AERO)
    assert a_pos != pytest.approx(a_neg)


def test_limit_margins_delegates_to_core_limits() -> None:
    spec = make_minimal_bike_spec()
    margins = model.limit_margins(
        spec,
        np.array([50.0]),
        np.array([400.0]),
        np.array([10.0]),
        np.array([300.0]),
        np.array([20.0]),
    )
    assert margins.traction_rear[0] == pytest.approx(abs(50.0) - spec.contact.mu_s * 400.0)
    assert margins.wheelie[0] == pytest.approx(-300.0)
    assert margins.stoppie[0] == pytest.approx(-400.0)
    assert margins.freewheel[0] == pytest.approx(-20.0)
