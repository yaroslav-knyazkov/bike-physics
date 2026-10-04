"""Tests for bikephysics.core.atmosphere (architecture.md S5.1,
`latex/appendix/atmosphere.tex`, task.md T2)."""

from __future__ import annotations

import math

import pytest

from bikephysics.core import atmosphere

# latex/appendix/atmosphere.tex's own published ISA table (app:isa).
ISA_TABLE = [
    # h [m], T [C], p [kPa], rho [kg/m^3]
    (0, 15.0, 101.3, 1.225),
    (1000, 8.5, 89.9, 1.112),
    (2000, 2.0, 79.5, 1.007),
    (3000, -4.5, 70.1, 0.909),
    (4000, -11.0, 61.6, 0.819),
    (5000, -17.5, 54.0, 0.736),
]


@pytest.mark.parametrize("h,t_celsius,p_kpa,rho_table", ISA_TABLE)
def test_isa_temperature_matches_published_table(
    h: float, t_celsius: float, p_kpa: float, rho_table: float
) -> None:
    t_k = atmosphere.isa_temperature(h)
    assert t_k - 273.15 == pytest.approx(t_celsius, abs=0.05)


@pytest.mark.parametrize("h,t_celsius,p_kpa,rho_table", ISA_TABLE)
def test_isa_pressure_matches_published_table(
    h: float, t_celsius: float, p_kpa: float, rho_table: float
) -> None:
    p_pa = atmosphere.isa_pressure(h)
    assert p_pa / 1000.0 == pytest.approx(p_kpa, rel=1e-3)


@pytest.mark.parametrize("h,t_celsius,p_kpa,rho_table", ISA_TABLE)
def test_isa_density_dry_matches_published_table(
    h: float, t_celsius: float, p_kpa: float, rho_table: float
) -> None:
    # RH=0 (dry air) reproduces the table's rho via rho(h, 0).
    assert atmosphere.rho(h, 0.0) == pytest.approx(rho_table, abs=2e-3)


def test_sea_level_reference_constants() -> None:
    assert atmosphere.isa_temperature(0.0) == pytest.approx(288.15)
    assert atmosphere.isa_pressure(0.0) == pytest.approx(101325.0)
    assert atmosphere.isa_density_dry(0.0) == pytest.approx(1.225, abs=1e-3)


def test_temperature_decreases_with_altitude() -> None:
    assert atmosphere.isa_temperature(1000.0) < atmosphere.isa_temperature(0.0)


def test_density_decreases_with_altitude() -> None:
    assert atmosphere.isa_density_dry(5000.0) < atmosphere.isa_density_dry(0.0)


def test_moist_air_is_less_dense_than_dry_air_same_t_p() -> None:
    t_k, p_pa = 288.15, 101325.0
    dry = atmosphere.moist_air_density(t_k, p_pa, 0.0)
    moist = atmosphere.moist_air_density(t_k, p_pa, 1.0)
    assert moist < dry


def test_rho_with_zero_humidity_equals_dry_isa_density() -> None:
    for h in (0.0, 1000.0, 3000.0):
        assert atmosphere.rho(h, 0.0) == pytest.approx(atmosphere.isa_density_dry(h), rel=1e-9)


def test_sutherland_mu_reference_value_at_273_15_k() -> None:
    # app:sutherland's own reference point: mu(T0) = mu0 exactly.
    assert atmosphere.sutherland_mu(273.15) == pytest.approx(1.716e-5, rel=1e-9)


def test_sutherland_mu_increases_with_temperature() -> None:
    # Unlike a liquid: viscosity rises with temperature (app:sutherland).
    assert atmosphere.mu(350.0) > atmosphere.mu(250.0)


def test_isa_atmosphere_matches_module_level_functions() -> None:
    env = atmosphere.ISAAtmosphere(altitude=1000.0, relative_humidity=0.4)
    assert env.temperature() == pytest.approx(atmosphere.isa_temperature(1000.0))
    assert env.pressure() == pytest.approx(atmosphere.isa_pressure(1000.0))
    assert env.density() == pytest.approx(
        atmosphere.moist_air_density(env.temperature(), env.pressure(), 0.4)
    )
    assert env.viscosity() == pytest.approx(atmosphere.sutherland_mu(env.temperature()))


def test_constant_atmosphere_uses_fixed_density_and_sutherland_viscosity() -> None:
    env = atmosphere.ConstantAtmosphere(density_value=1.2, temperature_k=300.0)
    assert env.density() == 1.2
    assert env.temperature() == 300.0
    assert env.viscosity() == pytest.approx(atmosphere.sutherland_mu(300.0))


def test_atmosphere_from_env_dispatches_constant() -> None:
    env = atmosphere.atmosphere_from_env(
        atmosphere="constant",
        rho_constant=1.1,
        altitude=0.0,
        relative_humidity=0.0,
        temperature_k=290.0,
    )
    assert isinstance(env, atmosphere.ConstantAtmosphere)
    assert env.density() == 1.1
    assert env.temperature() == 290.0


def test_atmosphere_from_env_dispatches_isa() -> None:
    env = atmosphere.atmosphere_from_env(
        atmosphere="isa",
        rho_constant=1.1,
        altitude=2000.0,
        relative_humidity=0.3,
        temperature_k=None,
    )
    assert isinstance(env, atmosphere.ISAAtmosphere)
    assert env.altitude == 2000.0


def test_atmosphere_from_env_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="unknown atmosphere mode"):
        atmosphere.atmosphere_from_env(
            atmosphere="weather_station",
            rho_constant=1.1,
            altitude=0.0,
            relative_humidity=0.0,
            temperature_k=None,
        )


def test_saturation_vapor_pressure_increases_with_temperature() -> None:
    assert atmosphere.saturation_vapor_pressure_kpa(
        303.15
    ) > atmosphere.saturation_vapor_pressure_kpa(273.15)


def test_isa_pressure_exponent_is_consistent_with_constants() -> None:
    expected = (atmosphere.G0_STANDARD * atmosphere.MOLAR_MASS_AIR) / (
        atmosphere.R_UNIVERSAL * atmosphere.LAPSE_RATE
    )
    assert math.isclose(atmosphere._ISA_PRESSURE_EXPONENT, expected, rel_tol=1e-12)
