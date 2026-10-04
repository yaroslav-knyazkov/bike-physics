"""core/aero.py -- the aerodynamic force model
(`latex/2d_models/models.tex` S aerodynamics-model; `latex/2d_models/forces.tex`
S Aerodynamic).

`AeroModel` implementations return `(A_1_x, A_1_z, A_1_m)`, the signed
scalar coefficients of `forces.tex`'s `A_{1,f} = -A_{1,x} x_hat_1 -
A_{1,z} z_hat_1`, `A_{1,m} = -A_{1,m} y_hat`. `beta_rel` and `Re` are always
computed and returned alongside the forces (architecture.md S5.1: "computed
and exposed even when unused"), so `Linearized`'s slopes are testable even
though `ConstantCdA` ignores them.

Three implementations, in increasing generality:
- `ConstantCdA`: the `beta_rel = 0` intercept only (`C_1_x_0`) -- "all that
  is calibratable today" per the architecture ticket; `C_1_z`/`C_1_m` are
  forced to 0.
- `Linearized`: all three coefficients, each a first-order expansion in
  `(xi_1, omega_1, beta_rel, Re)` around the `C_*_0` intercepts (models.tex's
  generic linearization equation), all four slopes defaulting to 0.
- `TableLookup`: a stub (models.tex's own "wind-tunnel or CFD lookup table"
  coefficient source) -- not implemented in this ticket.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AeroState:
    """One `AeroModel` evaluation: the three force/moment scalars plus the
    intermediate quantities `beta_rel`, `Re`, and the dynamic pressure `q`,
    all exposed regardless of which model computed them."""

    A_1_x: float
    A_1_z: float
    A_1_m: float
    beta_rel: float
    Re: float
    q: float


class AeroModel(Protocol):
    """`latex/2d_models/models.tex` S aerodynamics-model: drag, side force,
    and pitching moment from relative airflow, quasi-steady."""

    def evaluate(
        self,
        *,
        rho: float,
        mu: float,
        v1_x: float,
        v1_z: float,
        wind_x: float = 0.0,
        wind_z: float = 0.0,
        xi_1: float = 0.0,
        omega_1: float = 0.0,
    ) -> AeroState: ...


def relative_velocity(
    *, v1_x: float, v1_z: float, wind_x: float = 0.0, wind_z: float = 0.0
) -> tuple[float, float]:
    """`v_rel = v_wind - v_1` (models.tex)."""
    return wind_x - v1_x, wind_z - v1_z


def dynamic_pressure(rho: float, v_rel: float) -> float:
    """`q = (1/2)*rho*v_rel^2` (models.tex)."""
    return 0.5 * rho * v_rel**2


def angle_of_attack(v_rel_x: float, v_rel_z: float) -> float:
    """`beta_rel = atan2(z1_v_rel, x1_v_rel)` (models.tex)."""
    return math.atan2(v_rel_z, v_rel_x)


def reynolds_number(rho: float, v_rel: float, l_ref: float, mu: float) -> float:
    """`Re = rho*v_rel*l_k / mu` (models.tex)."""
    if mu == 0.0:
        return 0.0
    return rho * v_rel * l_ref / mu


def _common_kinematics(
    *, rho: float, mu: float, v1_x: float, v1_z: float, wind_x: float, wind_z: float, l_ref: float
) -> tuple[float, float, float, float]:
    """Shared `(q, beta_rel, Re, v_rel)` computation every `AeroModel`
    implementation needs, factored out so `ConstantCdA` and `Linearized`
    cannot silently diverge on how `beta_rel`/`Re` are derived."""
    v_rel_x, v_rel_z = relative_velocity(v1_x=v1_x, v1_z=v1_z, wind_x=wind_x, wind_z=wind_z)
    v_rel = math.hypot(v_rel_x, v_rel_z)
    q = dynamic_pressure(rho, v_rel)
    beta_rel = angle_of_attack(v_rel_x, v_rel_z)
    re = reynolds_number(rho, v_rel, l_ref, mu)
    return q, beta_rel, re, v_rel


@dataclass(frozen=True)
class ConstantCdA:
    """The `beta_rel = 0` intercept only: `A_1_x = q*C_1_x_0*A_1`,
    `A_1_z = A_1_m = 0`. This is the classic lumped `C_dA` Data acquisition
    calibrates from ordinary ride data (models.tex)."""

    C_1_x_0: float
    A_1: float
    l_1: float = 1.0

    def evaluate(
        self,
        *,
        rho: float,
        mu: float,
        v1_x: float,
        v1_z: float,
        wind_x: float = 0.0,
        wind_z: float = 0.0,
        xi_1: float = 0.0,
        omega_1: float = 0.0,
    ) -> AeroState:
        q, beta_rel, re, _ = _common_kinematics(
            rho=rho, mu=mu, v1_x=v1_x, v1_z=v1_z, wind_x=wind_x, wind_z=wind_z, l_ref=self.l_1
        )
        return AeroState(
            A_1_x=q * self.C_1_x_0 * self.A_1,
            A_1_z=0.0,
            A_1_m=0.0,
            beta_rel=beta_rel,
            Re=re,
            q=q,
        )


@dataclass(frozen=True)
class Linearized:
    """All three coefficients, each linearized in `(xi_1, omega_1,
    beta_rel, Re)` around its own `C_*_0` intercept (models.tex's generic
    linearization equation). All twelve slopes default to 0, so this
    reduces to `ConstantCdA`-equivalent behavior (plus the `C_1_z_0`/
    `C_1_m_0` intercepts) when uncalibrated."""

    C_1_x_0: float
    C_1_z_0: float = 0.0
    C_1_m_0: float = 0.0
    A_1: float = 0.0
    l_1: float = 1.0
    dC_1_x_dxi: float = 0.0
    dC_1_x_domega: float = 0.0
    dC_1_x_dbeta_rel: float = 0.0
    dC_1_x_dRe: float = 0.0
    dC_1_z_dxi: float = 0.0
    dC_1_z_domega: float = 0.0
    dC_1_z_dbeta_rel: float = 0.0
    dC_1_z_dRe: float = 0.0
    dC_1_m_dxi: float = 0.0
    dC_1_m_domega: float = 0.0
    dC_1_m_dbeta_rel: float = 0.0
    dC_1_m_dRe: float = 0.0

    def evaluate(
        self,
        *,
        rho: float,
        mu: float,
        v1_x: float,
        v1_z: float,
        wind_x: float = 0.0,
        wind_z: float = 0.0,
        xi_1: float = 0.0,
        omega_1: float = 0.0,
    ) -> AeroState:
        q, beta_rel, re, _ = _common_kinematics(
            rho=rho, mu=mu, v1_x=v1_x, v1_z=v1_z, wind_x=wind_x, wind_z=wind_z, l_ref=self.l_1
        )
        c_x = (
            self.C_1_x_0
            + self.dC_1_x_dxi * xi_1
            + self.dC_1_x_domega * omega_1
            + self.dC_1_x_dbeta_rel * beta_rel
            + self.dC_1_x_dRe * re
        )
        c_z = (
            self.C_1_z_0
            + self.dC_1_z_dxi * xi_1
            + self.dC_1_z_domega * omega_1
            + self.dC_1_z_dbeta_rel * beta_rel
            + self.dC_1_z_dRe * re
        )
        c_m = (
            self.C_1_m_0
            + self.dC_1_m_dxi * xi_1
            + self.dC_1_m_domega * omega_1
            + self.dC_1_m_dbeta_rel * beta_rel
            + self.dC_1_m_dRe * re
        )
        return AeroState(
            A_1_x=q * c_x * self.A_1,
            A_1_z=q * c_z * self.A_1,
            A_1_m=q * c_m * self.A_1 * self.l_1,
            beta_rel=beta_rel,
            Re=re,
            q=q,
        )


@dataclass(frozen=True)
class TableLookup:
    """Wind-tunnel/CFD coefficient lookup table (models.tex: "generally
    only available as a wind-tunnel or CFD lookup table"). Not implemented
    in this ticket."""

    def evaluate(
        self,
        *,
        rho: float,
        mu: float,
        v1_x: float,
        v1_z: float,
        wind_x: float = 0.0,
        wind_z: float = 0.0,
        xi_1: float = 0.0,
        omega_1: float = 0.0,
    ) -> AeroState:
        raise NotImplementedError(
            "TableLookup is a stub (architecture.md S5.1); no lookup-table "
            "data source is wired up yet."
        )
