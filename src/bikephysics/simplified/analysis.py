"""simplified/analysis.py -- closed-form steady-state and sensitivity
analysis built on `simplified/model.py` (architecture.md S6.2; verbatim
transcription of `latex/simplified/forward_motion.tex`'s "Analysis"
subsection).

Two distinct equilibrium models coexist here, exactly as the LaTeX
document itself splits them:

- **Equilibrium** (full): `P/v = M*g*(c_rr*cos(theta) + sin(theta)) +
  (1/2)*rho*C_dA*v^2`, used by `steady_state_speed`'s default
  (`include_rolling_resistance=True`) -- this is the balance `accel`/
  `model.py` actually integrate against.
- **Sensitivity** (simplified): rolling resistance dropped ("an order of
  magnitude below gravity and drag at any sustained speed"), leaving
  `P = M*g*v*sin(theta) + k*v^3` with `k = (1/2)*rho*C_dA`. `elasticities`,
  `size_elasticity`, `gradient_limits`, and `climb_energy` all use this
  simpler model, matching the document's own "Sensitivity" subsection
  verbatim -- `steady_state_speed(..., include_rolling_resistance=False)`
  is the shared entry point into it.

`wheelie_accel`/`stoppie_accel`/`traction_limit_accel` all inherit the
"Analysis" Assumptions block's "Forward motion -- sigma = 1" (no braking,
`B_2a = B_2b = 0`, for `traction_limit_accel`'s coasting search -- see its
own docstring) rather than taking `sigma`/brake/pedal arguments.

No function in this module touches a z-signed quantity directly except by
calling into `model.py`'s already-D-13-flipped `normal_loads`/`traction` --
so no further sign flip is needed here (see `model.py`'s module docstring
for the one place the flip itself lives).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
from scipy.optimize import brentq

from bikephysics.core import limits as core_limits
from bikephysics.core.aero import AeroState
from bikephysics.core.atmosphere import atmosphere_from_env
from bikephysics.core.params import BikeSpec

# ruff's TID251 banned-api ban on "bikephysics.simplified" (pyproject.toml)
# is architecture.md S4's *cross-tier* rule ("core/simplified/planar must
# not [import a tier module]") -- it targets other tiers/core importing
# this one, not this tier's own internal module structure. analysis.py
# building on model.py (its sibling within the same `simplified` package)
# is exactly the dependency architecture.md S6.2 describes; there is no
# per-file-ignore carved out for `src/bikephysics/simplified/*` itself
# (out of this ticket's file boundary to add), so the single import below
# is excepted explicitly instead.
from bikephysics.simplified import model  # noqa: TID251

FloatArray = npt.NDArray[np.float64]

#: The two asymptotic elasticity triples `(E_M, E_k, E_P)` from
#: `forward_motion.tex` "Analysis" > "Sensitivity" > "Two gradient
#: limits" -- universal constants, not derived per-call.
FLAT_AERO_DOMINATED_LIMIT: tuple[float, float, float] = (0.0, -1.0 / 3.0, 1.0 / 3.0)
STEEP_GRAVITY_DOMINATED_LIMIT: tuple[float, float, float] = (-1.0, 0.0, 1.0)


def _air_density(spec: BikeSpec) -> float:
    """Air density from `spec.env` via `core.atmosphere` (ISA or
    constant, per `env.atmosphere`)."""
    atmosphere = atmosphere_from_env(
        atmosphere=spec.env.atmosphere,
        rho_constant=spec.env.rho,
        altitude=spec.env.altitude,
        relative_humidity=spec.env.RH,
        temperature_k=spec.env.T,
    )
    return atmosphere.density()


def _drag_power_coeff(spec: BikeSpec) -> float:
    """`k = (1/2)*rho*C_dA` (`forward_motion.tex` "Analysis" >
    "Sensitivity"), with the lumped `C_dA = C_1,x,0 * A_1`
    (`core.aero.ConstantCdA`'s `beta_rel = 0` intercept times frontal
    area) -- the document's own simplification, no linearization slopes
    enter this analysis layer."""
    return 0.5 * _air_density(spec) * spec.aero.C_1_x_0 * spec.aero.A_1


def steady_state_speed(
    spec: BikeSpec,
    power_or_force: float,
    theta: float,
    *,
    input_kind: Literal["power", "force"] = "power",
    include_rolling_resistance: bool = True,
) -> float:
    """The real positive root of `forward_motion.tex` "Analysis" >
    "Equilibrium":

        P/v = M*g*(c_rr*cos(theta) + sin(theta)) + (1/2)*rho*C_dA*v^2

    i.e. `k*v^3 + M*g*(c_rr*cos(theta) + sin(theta))*v - P = 0` for
    `input_kind="power"` (the default), solved via `numpy.roots` and
    selecting the positive real root; when multiple positive real roots
    exist, the one at a *stable* equilibrium (`dG/dv > 0`) is preferred.

    The force-input quadratic form of the same balance -- a propulsive
    force `F` instead of a power `P`:

        F = M*g*(c_rr*cos(theta) + sin(theta)) + k*v^2

    is exposed via `input_kind="force"`, solved directly (exact, no root
    selection beyond discarding the negative root).

    `include_rolling_resistance=False` drops the `c_rr` term, giving
    `forward_motion.tex`'s "Sensitivity" simplification instead --
    `elasticities`/`gradient_limits`/`climb_energy`/`size_elasticity`
    below all go through this flag rather than duplicating the cubic.
    """
    model._check_equal_wheels(spec)
    big_m = spec.M
    g = spec.env.g
    c_rr = spec.rear.c_2a_rr if include_rolling_resistance else 0.0
    k = _drag_power_coeff(spec)
    resistive_const = big_m * g * (c_rr * math.cos(theta) + math.sin(theta))

    if input_kind == "force":
        force = power_or_force
        delta = force - resistive_const
        if delta < 0:
            raise ValueError(
                "steady_state_speed: force "
                f"{force} cannot overcome the grade+rolling-resistance term "
                f"{resistive_const} at theta={theta}"
            )
        if k <= 0:
            raise ValueError("steady_state_speed: k (aero drag coefficient) must be > 0")
        return math.sqrt(delta / k)

    if input_kind != "power":
        raise ValueError(f"unknown input_kind {input_kind!r}; expected 'power' or 'force'")

    power = power_or_force
    if k == 0.0:
        if resistive_const <= 0.0:
            raise ValueError(
                "steady_state_speed: no aero drag (k=0) and a non-positive "
                "resistive term -- no finite positive-power equilibrium exists"
            )
        return power / resistive_const

    roots = np.roots([k, 0.0, resistive_const, -power])
    real_positive = [
        r.real for r in roots if abs(r.imag) < 1e-9 * max(1.0, abs(r.real)) and r.real > 0
    ]
    if not real_positive:
        raise ValueError(
            f"steady_state_speed: no positive real root for power={power}, theta={theta}"
        )
    stable = [v for v in real_positive if (resistive_const + 3.0 * k * v**2) > 0]
    candidates = stable if stable else real_positive
    return max(candidates)


@dataclass(frozen=True)
class Elasticities:
    """`(E_M, E_k, E_P)` (`forward_motion.tex` "Analysis" > "Sensitivity"
    > "Elasticities"), plus the shared denominator `D` they are all built
    from."""

    E_M: float
    E_k: float
    E_P: float
    D: float


def elasticities(
    spec: BikeSpec, v: float, theta: float, power: float | None = None
) -> Elasticities:
    """Boxed `E_M`, `E_k`, `E_P` (`forward_motion.tex` "Analysis" >
    "Sensitivity" > "Elasticities"):

        D = M*g*sin(theta) + 3*k*v^2
        E_M = -(M*g*sin(theta)) / D
        E_k = -(k*v^2) / D
        E_P = (M*g*sin(theta) + k*v^2) / D

    plus an internal assertion that `E_M + E_k + E_P == 0` to within
    1e-9 (Euler's theorem on `G`'s homogeneity in `(M, k, P)` at fixed
    `v`; architecture.md S6.2's "free, exact self-test" -- enforced here,
    not only in the test suite, since it is exact for *any* `(M, k,
    theta, v)`, not just ones consistent with a particular `power`).

    `power` is accepted for signature parity with architecture.md S6.2's
    table but does not enter the formulas above (which are derived purely
    from `v`, `theta`, `M`, `k` -- see the LaTeX); it is reserved for a
    future consistency diagnostic and currently unused.
    """
    del power  # see docstring: not used by the boxed formulas themselves
    model._check_equal_wheels(spec)
    big_m = spec.M
    g = spec.env.g
    k = _drag_power_coeff(spec)
    gravity_term = big_m * g * math.sin(theta)
    aero_term = k * v**2
    d = gravity_term + 3.0 * aero_term
    e_m = -gravity_term / d
    e_k = -aero_term / d
    e_p = (gravity_term + aero_term) / d
    identity = e_m + e_k + e_p
    assert math.isclose(identity, 0.0, abs_tol=1e-9), (
        f"Euler's-theorem identity E_M+E_k+E_P=0 violated: got {identity}"
    )
    return Elasticities(E_M=e_m, E_k=e_k, E_P=e_p, D=d)


def size_elasticity(
    spec: BikeSpec, v: float, theta: float, power: float, a: float, b: float
) -> float:
    """Boxed `dln(v)/dln(M)|_{across riders} = E_M + a*E_P + b*E_k`
    (`forward_motion.tex` "Analysis" > "Sensitivity" > "Is there a best
    rider weight?"), via `elasticities` above."""
    e = elasticities(spec, v, theta, power)
    return e.E_M + a * e.E_P + b * e.E_k


@dataclass(frozen=True)
class GradientLimits:
    """The current dominance ratio plus the two universal asymptotic
    elasticity triples (`forward_motion.tex` "Analysis" > "Sensitivity" >
    "Two gradient limits"), so the caller can see both the number and
    which regime it implies."""

    dominance_ratio: float
    flat_limit: tuple[float, float, float] = FLAT_AERO_DOMINATED_LIMIT
    steep_limit: tuple[float, float, float] = STEEP_GRAVITY_DOMINATED_LIMIT


def gradient_limits(spec: BikeSpec, v: float, theta: float) -> GradientLimits:
    """The dominance ratio `M*g*sin(theta) / (k*v^2)`
    (`forward_motion.tex` "Analysis" > "Sensitivity" > "Two gradient
    limits"): `-> 0` is "flat, aero-dominated"
    (`E ~= (0, -1/3, +1/3)`), `-> infinity` is "steep, gravity-dominated"
    (`E ~= (-1, 0, +1)`)."""
    model._check_equal_wheels(spec)
    big_m = spec.M
    g = spec.env.g
    k = _drag_power_coeff(spec)
    ratio = (big_m * g * math.sin(theta)) / (k * v**2)
    return GradientLimits(dominance_ratio=ratio)


def climb_energy(spec: BikeSpec, power: float, delta_h: float, thetas: FloatArray) -> FloatArray:
    """`E(theta) = P*delta_h / (v(theta)*sin(theta))`
    (`forward_motion.tex` "Analysis" > "Sensitivity" > "Is a steeper
    climb more energy-efficient for the same height gain?"), with
    `v(theta)` from the "Sensitivity"-simplified balance
    (`steady_state_speed(..., include_rolling_resistance=False)`, matching
    the LaTeX derivation this subparagraph itself builds on).

    Asserts `E` is strictly decreasing in `theta` when `thetas` is given
    strictly increasing (the document's own conclusion: "steeper is
    always more energy-efficient for a fixed height gain")."""
    thetas_arr = np.asarray(thetas, dtype=float)
    energies = np.empty_like(thetas_arr)
    for i, theta in enumerate(thetas_arr):
        v = steady_state_speed(spec, power, float(theta), include_rolling_resistance=False)
        energies[i] = power * delta_h / (v * math.sin(theta))
    if thetas_arr.size > 1 and np.all(np.diff(thetas_arr) > 0):
        diffs = np.diff(energies)
        assert np.all(diffs <= 1e-9), (
            f"climb_energy must be strictly decreasing in theta; got diffs {diffs}"
        )
    return energies


def stoppie_accel(spec: BikeSpec, theta: float, aero: AeroState) -> float:
    """Boxed `x_ddot_stoppie` (`forward_motion.tex` "Analysis" >
    "Stoppie limit"):

        x_ddot_stoppie = -(l_D'*M*g*cos(theta) + h*M*g*sin(theta)
                            + r_2a*A_1,x - A_1,m) / (J/r_2a + h*M)

    under the "Analysis" Assumptions block's `sigma = 1` (forward
    motion). No D-13 flip applies -- `x_ddot` has no z-component."""
    model._check_equal_wheels(spec)
    _l_c, l_d, h, _length = model._geometry(spec)
    sigma = 1.0
    l_2b_rr = spec.front.c_2b_rr * spec.front.r_2b
    l_d_p = l_d + l_2b_rr * sigma
    r_2a = spec.rear.r_2a
    big_m = spec.M
    g = spec.env.g
    big_j = (
        spec.rear.I_2a
        + spec.front.I_2b
        + (spec.drivetrain.r_2a_cg / spec.drivetrain.r_3b_cr) * spec.drivetrain.I_3b
    )
    numerator = -(
        l_d_p * big_m * g * math.cos(theta)
        + h * big_m * g * math.sin(theta)
        + r_2a * aero.A_1_x
        - aero.A_1_m
    )
    denominator = big_j / r_2a + h * big_m
    return numerator / denominator


def wheelie_accel(spec: BikeSpec, theta: float, aero: AeroState) -> float:
    """Boxed `x_ddot_wheelie` (`forward_motion.tex` "Analysis" >
    "Wheelie limit"):

        x_ddot_wheelie = (l_C'*M*g*cos(theta) - h*M*g*sin(theta)
                           - r_2a*A_1,x + A_1,m) / (J/r_2a + h*M)

    under the "Analysis" Assumptions block's `sigma = 1` (forward
    motion). No D-13 flip applies."""
    model._check_equal_wheels(spec)
    l_c, _l_d, h, _length = model._geometry(spec)
    sigma = 1.0
    l_2a_rr = spec.rear.c_2a_rr * spec.rear.r_2a
    l_c_p = l_c - l_2a_rr * sigma
    r_2a = spec.rear.r_2a
    big_m = spec.M
    g = spec.env.g
    big_j = (
        spec.rear.I_2a
        + spec.front.I_2b
        + (spec.drivetrain.r_2a_cg / spec.drivetrain.r_3b_cr) * spec.drivetrain.I_3b
    )
    numerator = (
        l_c_p * big_m * g * math.cos(theta)
        - h * big_m * g * math.sin(theta)
        - r_2a * aero.A_1_x
        + aero.A_1_m
    )
    denominator = big_j / r_2a + h * big_m
    return numerator / denominator


@dataclass(frozen=True)
class TractionLimitAccel:
    """The largest-magnitude `x_ddot` in each direction with both
    contacts inside the friction cone (`forward_motion.tex` "Analysis" >
    "Traction limit" -- a *check*, not a boxed closed-form equation, so
    this is a root-find over `model.traction`/`model.normal_loads`
    rather than a transcription)."""

    accel_limit: float
    decel_limit: float


def _bracketed_traction_root(margin: Callable[[float], float], lo: float, hi: float) -> float:
    f_lo, f_hi = margin(lo), margin(hi)
    if f_lo > 0:
        return lo
    if f_hi <= 0:
        return hi
    return float(brentq(margin, lo, hi))


def traction_limit_accel(spec: BikeSpec, theta: float, aero: AeroState) -> TractionLimitAccel:
    """The traction-limited `x_ddot` boundary in each direction
    (`forward_motion.tex` "Analysis" > "Traction limit": `|R_x| <=
    mu_s*(-R_z)` at each contact, checked, not closed-formed). Assumes
    **coasting** (`B_2a = B_2b = 0`, `P_D = P_F = 0`) -- the "Analysis"
    Assumptions block's `sigma=1`/no-braking plus no pedal input, since
    architecture.md S6.2's signature for this function carries no pedal/
    brake arguments; with those forces zero, both `R_x(x_ddot)` and
    `R_z(x_ddot)` are affine in `x_ddot` (via `model.traction`/
    `model.normal_loads`), so the search is bracketed by
    `stoppie_accel`/`wheelie_accel` (beyond which a wheel has already
    lifted and the no-slip model is moot) and solved with
    `scipy.optimize.brentq`.

    Returns the boundary in each direction separately: `accel_limit > 0`
    (forward acceleration) and `decel_limit < 0` (braking/deceleration),
    each the most restrictive wheel's crossing of
    `core.limits.traction_margin == 0`.
    """
    model._check_equal_wheels(spec)
    mu_s = spec.contact.mu_s

    def margin(x_ddot: float) -> float:
        sigma = 1.0
        r_2a_z, r_2b_z = model.normal_loads(spec, x_ddot, theta, aero.A_1_x, aero.A_1_m, sigma)
        tension = model.chain_tension(spec, x_ddot, 0.0, 0.0)
        r_2a_x, r_2b_x = model.traction(spec, x_ddot, tension, r_2a_z, r_2b_z, 0.0, 0.0, sigma)
        m_rear = core_limits.traction_margin(r_2a_x, r_2a_z, mu_s)
        m_front = core_limits.traction_margin(r_2b_x, r_2b_z, mu_s)
        return max(m_rear, m_front)

    hi = wheelie_accel(spec, theta, aero)
    lo = stoppie_accel(spec, theta, aero)
    accel_limit = _bracketed_traction_root(margin, 0.0, hi)
    decel_limit = _bracketed_traction_root(margin, lo, 0.0)
    return TractionLimitAccel(accel_limit=accel_limit, decel_limit=decel_limit)
