"""Tests for bikephysics.simplified.analysis (architecture.md S6.2,
task.md T3; `latex/simplified/forward_motion.tex` "Analysis").

These are analytic/self-consistency tests, not golden-numeric regressions:
every assertion here is an exact (or near-exact, floating-point-limited)
closed-form identity from the LaTeX document itself, per task.md T3's
"mandatory assertions" list."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bikephysics.core.aero import AeroState, ConstantCdA
from bikephysics.simplified import analysis, model
from tests.unit.factories import make_minimal_bike_spec

ZERO_AERO = AeroState(A_1_x=0.0, A_1_z=0.0, A_1_m=0.0, beta_rel=0.0, Re=0.0, q=0.0)


# --------------------------------------------------------------------------
# E_M + E_k + E_P == 0 (Euler's theorem), across a grid of (M, k, theta, P).
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mass_kg", [50.0, 65.0, 90.0])
@pytest.mark.parametrize("cda", [0.2, 0.35, 0.5])
@pytest.mark.parametrize("theta", [-0.1, 0.0, 0.05, 0.3])
@pytest.mark.parametrize("v", [1.0, 5.0, 12.0])
def test_elasticities_identity_holds_exactly_on_grid(
    mass_kg: float, cda: float, theta: float, v: float
) -> None:
    spec = make_minimal_bike_spec(
        frame={
            "m_1": mass_kg,
            "I_1": 1.2,
            "l_1_a": 0.55,
            "h_1_a": 0.65,
            "l_1_B": 1.0,
            "l_1_C": 0.42,
            "h_1_C": -0.07,
            "r_1_pedal": 0.1725,
        },
        aero={"C_1_x_0": cda, "A_1": 1.0},
    )
    e = analysis.elasticities(spec, v, theta)
    assert e.E_M + e.E_k + e.E_P == pytest.approx(0.0, abs=1e-12)


def test_elasticities_identity_is_asserted_internally() -> None:
    # The function itself enforces the identity (architecture.md S6.2's
    # "free, exact self-test") -- calling it successfully is already part
    # of the proof; this test exists to name that behavior explicitly.
    spec = make_minimal_bike_spec()
    analysis.elasticities(spec, 8.0, 0.1)  # must not raise


# --------------------------------------------------------------------------
# The two gradient-limit asymptotics.
# --------------------------------------------------------------------------


def test_flat_aero_dominated_limit_is_exact_at_theta_zero() -> None:
    """At theta=0, M*g*sin(theta) is identically 0, so the flat/
    aero-dominated limit is exact, not merely approximate."""
    spec = make_minimal_bike_spec()
    e = analysis.elasticities(spec, 10.0, 0.0)
    assert (e.E_M, e.E_k, e.E_P) == pytest.approx(analysis.FLAT_AERO_DOMINATED_LIMIT, abs=1e-12)

    limits = analysis.gradient_limits(spec, 10.0, 0.0)
    assert limits.dominance_ratio == pytest.approx(0.0, abs=1e-12)
    assert limits.flat_limit == analysis.FLAT_AERO_DOMINATED_LIMIT


def test_steep_gravity_dominated_limit_reproduced_at_small_v() -> None:
    """As v -> 0 (aero term -> 0 while the gradient term stays finite),
    the elasticities approach the steep/gravity-dominated limit."""
    spec = make_minimal_bike_spec()
    theta = 0.3
    v = 1e-7
    e = analysis.elasticities(spec, v, theta)
    assert (e.E_M, e.E_k, e.E_P) == pytest.approx(analysis.STEEP_GRAVITY_DOMINATED_LIMIT, abs=1e-5)

    limits = analysis.gradient_limits(spec, v, theta)
    assert limits.dominance_ratio > 1e6  # deep in the gravity-dominated regime
    assert limits.steep_limit == analysis.STEEP_GRAVITY_DOMINATED_LIMIT


# --------------------------------------------------------------------------
# climb_energy monotonicity.
# --------------------------------------------------------------------------


def test_climb_energy_is_monotonically_decreasing_in_theta() -> None:
    spec = make_minimal_bike_spec()
    thetas = np.linspace(0.03, 0.35, 8)
    energies = analysis.climb_energy(spec, power=200.0, delta_h=100.0, thetas=thetas)
    assert np.all(np.diff(energies) < 0.0)


def test_climb_energy_matches_boxed_formula() -> None:
    spec = make_minimal_bike_spec()
    power, delta_h, theta = 200.0, 50.0, 0.15
    energies = analysis.climb_energy(spec, power, delta_h, np.array([theta]))
    v = analysis.steady_state_speed(spec, power, theta, include_rolling_resistance=False)
    expected = power * delta_h / (v * math.sin(theta))
    assert energies[0] == pytest.approx(expected)


# --------------------------------------------------------------------------
# steady_state_speed inverted through accel returns zero acceleration.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("power", [80.0, 200.0, 350.0])
@pytest.mark.parametrize("theta", [0.0, 0.02, -0.03])
def test_steady_state_speed_inverted_through_accel_gives_zero(power: float, theta: float) -> None:
    spec = make_minimal_bike_spec(contact={"mu_s": 0.8, "mu_k": 0.7, "v_eps": 1e-6})
    v = analysis.steady_state_speed(spec, power, theta)

    dt = spec.drivetrain
    r_2a = spec.rear.r_2a
    # Equivalent single-pedal force reproducing `power/v` as the pedal_term
    # in `model.accel` (see forward_motion.tex "Analysis" > "Equilibrium":
    # P/v is exactly the pedal driving force once the gear geometry is
    # eliminated via omega_3b = r_2a_cg*v/(r_3b_cr*r_2a)).
    p_d = (power / v) * (dt.r_3b_cr * r_2a) / (spec.frame.r_1_pedal * dt.r_2a_cg)

    aero_model = ConstantCdA(C_1_x_0=spec.aero.C_1_x_0, A_1=spec.aero.A_1, l_1=spec.aero.l_1)
    aero_state = aero_model.evaluate(rho=spec.env.rho, mu=1.8e-5, v1_x=v, v1_z=0.0)

    x_ddot = model.accel(spec, v, theta, p_d, 0.0, 0.0, 0.0, aero_state)
    assert x_ddot == pytest.approx(0.0, abs=1e-6)


def test_steady_state_speed_force_input_quadratic_form() -> None:
    spec = make_minimal_bike_spec()
    theta = 0.05
    v = analysis.steady_state_speed(spec, 120.0, theta)
    force = 120.0 / v  # P/v, the force equivalent to this power at this v
    v_from_force = analysis.steady_state_speed(spec, force, theta, input_kind="force")
    assert v_from_force == pytest.approx(v, rel=1e-8)


def test_steady_state_speed_rejects_infeasible_force() -> None:
    spec = make_minimal_bike_spec()
    with pytest.raises(ValueError, match="cannot overcome"):
        analysis.steady_state_speed(spec, 0.1, 0.5, input_kind="force")


# --------------------------------------------------------------------------
# wheelie_accel/stoppie_accel are exactly where normal_loads crosses zero.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("theta", [0.0, 0.08, -0.05])
def test_wheelie_accel_is_where_front_normal_load_crosses_zero(theta: float) -> None:
    spec = make_minimal_bike_spec()
    aero = AeroState(A_1_x=5.0, A_1_z=0.0, A_1_m=1.0, beta_rel=0.0, Re=0.0, q=0.0)
    x_ddot_wheelie = analysis.wheelie_accel(spec, theta, aero)

    _r_2a_z, r_2b_z = model.normal_loads(spec, x_ddot_wheelie, theta, aero.A_1_x, aero.A_1_m, 1.0)
    assert r_2b_z == pytest.approx(0.0, abs=1e-8)

    # just below the critical acceleration, the front wheel is still loaded.
    _r_2a_z_lo, r_2b_z_lo = model.normal_loads(
        spec, x_ddot_wheelie - 0.5, theta, aero.A_1_x, aero.A_1_m, 1.0
    )
    assert r_2b_z_lo > 0


@pytest.mark.parametrize("theta", [0.0, 0.08, -0.05])
def test_stoppie_accel_is_where_rear_normal_load_crosses_zero(theta: float) -> None:
    spec = make_minimal_bike_spec()
    aero = AeroState(A_1_x=5.0, A_1_z=0.0, A_1_m=1.0, beta_rel=0.0, Re=0.0, q=0.0)
    x_ddot_stoppie = analysis.stoppie_accel(spec, theta, aero)

    r_2a_z, _r_2b_z = model.normal_loads(spec, x_ddot_stoppie, theta, aero.A_1_x, aero.A_1_m, 1.0)
    assert r_2a_z == pytest.approx(0.0, abs=1e-8)

    # just above the critical (less negative) deceleration, rear still loaded.
    r_2a_z_hi, _r_2b_z_hi = model.normal_loads(
        spec, x_ddot_stoppie + 0.5, theta, aero.A_1_x, aero.A_1_m, 1.0
    )
    assert r_2a_z_hi > 0


# --------------------------------------------------------------------------
# traction_limit_accel: basic sanity (not one of the mandatory assertions,
# but exercises the root-find against the same margin core.limits uses).
# --------------------------------------------------------------------------


def test_traction_limit_accel_is_within_wheelie_stoppie_bracket() -> None:
    spec = make_minimal_bike_spec()
    theta = 0.0
    limits = analysis.traction_limit_accel(spec, theta, ZERO_AERO)
    hi = analysis.wheelie_accel(spec, theta, ZERO_AERO)
    lo = analysis.stoppie_accel(spec, theta, ZERO_AERO)
    assert lo <= limits.decel_limit <= 0.0
    assert 0.0 <= limits.accel_limit <= hi


def test_size_elasticity_matches_boxed_formula() -> None:
    spec = make_minimal_bike_spec()
    v, theta, a, b = 6.0, 0.05, 0.8, 2.0 / 3.0
    e = analysis.elasticities(spec, v, theta)
    expected = e.E_M + a * e.E_P + b * e.E_k
    assert analysis.size_elasticity(spec, v, theta, power=0.0, a=a, b=b) == pytest.approx(expected)
