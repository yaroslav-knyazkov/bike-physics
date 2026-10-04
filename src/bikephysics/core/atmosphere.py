"""core/atmosphere.py -- the ISA model, moist-air density correction, and
Sutherland's viscosity law (architecture.md S5.1's `env.atmosphere`;
`latex/appendix/atmosphere.tex` in full).

Three layers, matching the LaTeX appendix's own structure:
- `isa_temperature`/`isa_pressure`/`isa_density_dry`: the dry-air ISA
  relations (app:isa), troposphere only (`h <= 11 km`, well above any
  cyclable altitude -- no check is added for exceeding it, since the
  closed-form expressions simply keep extrapolating, which is adequate for
  this project's altitude range).
- `moist_air_density`: the humidity correction (app:humidity-density),
  applied on top of a dry-air `(T, p)` pair from any source, ISA or not.
- `sutherland_mu`: viscosity (app:sutherland), a function of `T` alone.

`rho(h, RH)` and `mu(T)` compose these into the two public entry points
`core.aero` needs. `ISAAtmosphere` and `ConstantAtmosphere` are the two
`Atmosphere` implementations `core.params.EnvParams.atmosphere` selects
between.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

# ISA sea-level reference constants (latex/appendix/atmosphere.tex, app:isa).
T0_ISA: float = 288.15  # K, sea-level standard temperature
P0_ISA: float = 101325.0  # Pa, sea-level standard pressure
LAPSE_RATE: float = 0.0065  # K/m, troposphere lapse rate
G0_STANDARD: float = 9.80665  # m/s^2, standard gravity used by the ISA pressure relation
MOLAR_MASS_AIR: float = 0.0289644  # kg/mol
R_UNIVERSAL: float = 8.31446  # J/(mol*K)
R_DRY_AIR: float = R_UNIVERSAL / MOLAR_MASS_AIR  # ~287.05 J/(kg*K)
R_VAPOR: float = 461.5  # J/(kg*K), water vapor specific gas constant

# Sutherland's law constants for air (app:sutherland).
MU0_SUTHERLAND: float = 1.716e-5  # Pa*s, reference viscosity
T0_SUTHERLAND: float = 273.15  # K, reference temperature
S_SUTHERLAND: float = 110.4  # K, Sutherland's constant for air

_ISA_PRESSURE_EXPONENT: float = (G0_STANDARD * MOLAR_MASS_AIR) / (R_UNIVERSAL * LAPSE_RATE)


def isa_temperature(h: float) -> float:
    """`T(h) = T0 - L*h` (app:isa), troposphere."""
    return T0_ISA - LAPSE_RATE * h


def isa_pressure(h: float) -> float:
    """`p(h) = p0*(1 - L*h/T0)^(g0*M/(R*L))` (app:isa)."""
    return P0_ISA * (1.0 - LAPSE_RATE * h / T0_ISA) ** _ISA_PRESSURE_EXPONENT


def isa_density_dry(h: float) -> float:
    """`rho(h) = p(h) / (R_d*T(h))` (app:isa), dry air only."""
    return isa_pressure(h) / (R_DRY_AIR * isa_temperature(h))


def saturation_vapor_pressure_kpa(temperature_k: float) -> float:
    """Tetens' formula, `e_s(T) = 0.61078*exp(17.27*Tc/(Tc+237.3))` [kPa]
    (app:humidity-density), `Tc` in Celsius."""
    t_celsius = temperature_k - 273.15
    return 0.61078 * math.exp(17.27 * t_celsius / (t_celsius + 237.3))


def moist_air_density(temperature_k: float, pressure_pa: float, relative_humidity: float) -> float:
    """`rho = p/(R_d*T) * (1 - (1 - R_d/R_v)*e/p)`, `e = RH*e_s(T)`
    (app:humidity-density). `relative_humidity` in `[0, 1]`.
    `relative_humidity = 0` recovers the dry-air density exactly."""
    e_s_pa = saturation_vapor_pressure_kpa(temperature_k) * 1000.0
    e_pa = relative_humidity * e_s_pa
    return (pressure_pa / (R_DRY_AIR * temperature_k)) * (
        1.0 - (1.0 - R_DRY_AIR / R_VAPOR) * (e_pa / pressure_pa)
    )


def sutherland_mu(temperature_k: float) -> float:
    """`mu(T) = mu0*(T/T0)^1.5 * (T0+S)/(T+S)` (app:sutherland)."""
    return (
        MU0_SUTHERLAND
        * (temperature_k / T0_SUTHERLAND) ** 1.5
        * (T0_SUTHERLAND + S_SUTHERLAND)
        / (temperature_k + S_SUTHERLAND)
    )


def rho(h: float, relative_humidity: float = 0.0) -> float:
    """Air density at altitude `h` [m], with the moist-air correction for
    `relative_humidity` in `[0, 1]` (`latex/2d_models/models.tex` S air-density:
    `rho = rho(h, RH)`). `relative_humidity = 0` is dry-air ISA density."""
    return moist_air_density(isa_temperature(h), isa_pressure(h), relative_humidity)


def mu(temperature_k: float) -> float:
    """Air dynamic viscosity at `temperature_k` [K]
    (`latex/2d_models/models.tex` S air-viscosity: `mu = mu(T)`)."""
    return sutherland_mu(temperature_k)


class Atmosphere(Protocol):
    """What `core.aero` needs from the environment: density, viscosity, and
    temperature at the bike's current location."""

    def density(self) -> float: ...
    def viscosity(self) -> float: ...
    def temperature(self) -> float: ...


@dataclass(frozen=True)
class ISAAtmosphere:
    """Altitude-based ISA atmosphere, with the moist-air correction and
    Sutherland viscosity both applied through the altitude-derived
    temperature. `core.params.EnvParams.atmosphere == "isa"`."""

    altitude: float
    relative_humidity: float = 0.0

    def temperature(self) -> float:
        return isa_temperature(self.altitude)

    def pressure(self) -> float:
        return isa_pressure(self.altitude)

    def density(self) -> float:
        return moist_air_density(self.temperature(), self.pressure(), self.relative_humidity)

    def viscosity(self) -> float:
        return sutherland_mu(self.temperature())


@dataclass(frozen=True)
class ConstantAtmosphere:
    """Fixed density and temperature, no altitude dependence.
    `core.params.EnvParams.atmosphere == "constant"` (the default).
    Viscosity is still derived from `temperature_k` via Sutherland's law
    rather than hardcoded, so `Re` stays physically consistent even in the
    constant-atmosphere mode."""

    density_value: float
    temperature_k: float = T0_ISA

    def density(self) -> float:
        return self.density_value

    def temperature(self) -> float:
        return self.temperature_k

    def viscosity(self) -> float:
        return sutherland_mu(self.temperature_k)


def atmosphere_from_env(
    *,
    atmosphere: str,
    rho_constant: float,
    altitude: float,
    relative_humidity: float,
    temperature_k: float | None,
) -> Atmosphere:
    """Build the `Atmosphere` implementation selected by
    `core.params.EnvParams` (passed as plain scalars, not the pydantic model
    itself, to avoid a `core.atmosphere` -> `core.params` import: both are
    leaf `core` modules and architecture.md S4 does not order them relative
    to each other, but keeping atmosphere.py free of the params import
    keeps this module trivially unit-testable in isolation)."""
    if atmosphere == "isa":
        return ISAAtmosphere(altitude=altitude, relative_humidity=relative_humidity)
    if atmosphere == "constant":
        t = temperature_k if temperature_k is not None else T0_ISA
        return ConstantAtmosphere(density_value=rho_constant, temperature_k=t)
    raise ValueError(f"unknown atmosphere mode {atmosphere!r}; expected 'constant' or 'isa'")
