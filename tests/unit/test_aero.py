"""Tests for bikephysics.core.aero (`latex/2d_models/models.tex`
S aerodynamics-model, architecture.md S5.1, task.md T2)."""

from __future__ import annotations

import math

import pytest

from bikephysics.core.aero import (
    ConstantCdA,
    Linearized,
    TableLookup,
    angle_of_attack,
    dynamic_pressure,
    relative_velocity,
    reynolds_number,
)

RHO_SEA_LEVEL = 1.225
MU_SEA_LEVEL = 1.789e-5


def test_relative_velocity_is_wind_minus_frame_velocity() -> None:
    vx, vz = relative_velocity(v1_x=5.0, v1_z=0.0, wind_x=2.0, wind_z=1.0)
    assert vx == pytest.approx(-3.0)
    assert vz == pytest.approx(1.0)


def test_dynamic_pressure_formula() -> None:
    assert dynamic_pressure(1.225, 10.0) == pytest.approx(0.5 * 1.225 * 100.0)


def test_angle_of_attack_is_atan2_of_components() -> None:
    assert angle_of_attack(1.0, 0.0) == pytest.approx(0.0)
    assert angle_of_attack(0.0, 1.0) == pytest.approx(math.pi / 2)


def test_reynolds_number_formula() -> None:
    re = reynolds_number(1.2, 10.0, 0.5, 1.8e-5)
    assert re == pytest.approx(1.2 * 10.0 * 0.5 / 1.8e-5)


def test_reynolds_number_zero_viscosity_returns_zero_not_inf() -> None:
    assert reynolds_number(1.2, 10.0, 0.5, 0.0) == 0.0


def test_constant_cda_pure_headwind_matches_q_cda() -> None:
    model = ConstantCdA(C_1_x_0=0.9, A_1=0.4, l_1=1.0)
    v = 8.0
    state = model.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=v, v1_z=0.0)
    q = 0.5 * RHO_SEA_LEVEL * v**2
    assert state.A_1_x == pytest.approx(q * 0.9 * 0.4)
    assert state.A_1_z == 0.0
    assert state.A_1_m == 0.0


def test_constant_cda_exposes_beta_rel_and_re_even_though_unused() -> None:
    model = ConstantCdA(C_1_x_0=0.9, A_1=0.4, l_1=0.5)
    state = model.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=8.0, v1_z=1.0)
    # Pure headwind (no wind, z-component from the rider's own motion) ->
    # beta_rel/Re both computed from the true relative-velocity vector.
    vx, vz = relative_velocity(v1_x=8.0, v1_z=1.0)
    expected_beta = angle_of_attack(vx, vz)
    v_rel = math.hypot(vx, vz)
    expected_re = reynolds_number(RHO_SEA_LEVEL, v_rel, 0.5, MU_SEA_LEVEL)
    assert state.beta_rel == pytest.approx(expected_beta)
    assert state.Re == pytest.approx(expected_re)


def test_constant_cda_zero_relative_velocity_gives_zero_force() -> None:
    model = ConstantCdA(C_1_x_0=0.9, A_1=0.4)
    state = model.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=0.0, v1_z=0.0)
    assert state.A_1_x == 0.0
    assert state.q == 0.0


def test_linearized_reduces_to_constant_cda_when_all_slopes_zero() -> None:
    v = 8.0
    linear = Linearized(C_1_x_0=0.9, A_1=0.4, l_1=1.0)
    const = ConstantCdA(C_1_x_0=0.9, A_1=0.4, l_1=1.0)
    s_lin = linear.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=v, v1_z=0.0)
    s_const = const.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=v, v1_z=0.0)
    assert s_lin.A_1_x == pytest.approx(s_const.A_1_x)
    assert s_lin.A_1_z == pytest.approx(s_const.A_1_z)
    assert s_lin.A_1_m == pytest.approx(s_const.A_1_m)


def test_linearized_beta_rel_slope_changes_drag_coefficient() -> None:
    v = 8.0
    # A nonzero d(C_x)/d(beta_rel) slope must shift A_1_x away from the
    # beta_rel=0 (headwind) answer once there is a nonzero angle of attack.
    model = Linearized(C_1_x_0=0.9, A_1=0.4, l_1=1.0, dC_1_x_dbeta_rel=0.5)
    headwind = model.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=v, v1_z=0.0)
    crosswind = model.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=v, v1_z=2.0)
    assert crosswind.beta_rel != pytest.approx(0.0)
    assert headwind.A_1_x != pytest.approx(crosswind.A_1_x)


def test_linearized_z_and_m_use_their_own_intercepts() -> None:
    model = Linearized(C_1_x_0=0.5, C_1_z_0=0.2, C_1_m_0=0.1, A_1=0.4, l_1=0.8)
    state = model.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=8.0, v1_z=0.0)
    assert state.A_1_z == pytest.approx(state.q * 0.2 * 0.4)
    assert state.A_1_m == pytest.approx(state.q * 0.1 * 0.4 * 0.8)


def test_table_lookup_is_not_implemented() -> None:
    model = TableLookup()
    with pytest.raises(NotImplementedError):
        model.evaluate(rho=RHO_SEA_LEVEL, mu=MU_SEA_LEVEL, v1_x=5.0, v1_z=0.0)
