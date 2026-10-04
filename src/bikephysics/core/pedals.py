"""core/pedals.py -- pedal-force input models
(`latex/2d_models/models.tex` S pedal-force-model; architecture.md S5.4).

`PedalInput.forces(t, theta_3b, omega_3b)` returns `(P_D, P_F)`, the two
pedals' tangential force magnitudes [N]. Every periodic-analytical and
shape-constrained template (`CosSquared`, `HarmonicSeries`, `ClippedSine`,
`VonMises`) is built on `_PeriodicTemplate`, which computes `P_D` from a
single `_shape(theta)` function and derives `P_F = _shape(theta + pi)`
mechanically -- the models.tex invariant "the two pedals offset by half a
revolution" is enforced by construction, not duplicated per template.

`ZeroPedal`/`ConstantCrankTorque` are the two non-periodic trivial cases;
`TabulatedTime`/`PiecewiseConstantTime`/`TabulatedCrankAngle` are the
data-driven/decision-variable carriers (models.tex "Data-driven"), which
are not periodic-template-shaped and so are not required to hold the
pi-offset invariant structurally (their `P_D`/`P_F` are independent inputs,
exactly like it would be for free per-pedal measured data or a per-interval
optimization decision variable).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class PedalWrench:
    """Optional radial+tangential pedal force decomposition
    (`latex/2d_models/forces.tex` S Pedal force: `{}^{x_3b}P`, `{}^{z_3b}P`).
    Not populated by the tangential-only templates below (`full()` returns
    `None` for them); reserved for a future radial-component model."""

    x_3b: float
    z_3b: float


class PedalInput(Protocol):
    """architecture.md S5.4."""

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        """`(P_D, P_F)`, the drive- and far-side pedal tangential force
        magnitudes [N]."""
        ...

    def full(self, t: float, theta_3b: float, omega_3b: float) -> PedalWrench | None:
        """Optional radial+tangential decomposition; `None` when not
        modeled (the default for every template below)."""
        return None


@dataclass(frozen=True)
class ZeroPedal:
    """No pedal input (coasting)."""

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        return 0.0, 0.0

    def full(self, t: float, theta_3b: float, omega_3b: float) -> PedalWrench | None:
        return None


@dataclass(frozen=True)
class ConstantCrankTorque:
    """A constant total crank torque `tau` [N*m], split evenly between the
    two pedals at the pedal radius `r_1_pedal` [m]: `P_D = P_F = tau /
    (2*r_1_pedal)`, independent of crank angle. `tau` must be `>= 0`
    (non-negative everywhere, like every other template here)."""

    tau: float
    r_1_pedal: float

    def __post_init__(self) -> None:
        if self.tau < 0:
            raise ValueError(f"tau must be >= 0, got {self.tau}")

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        p = self.tau / (2.0 * self.r_1_pedal)
        return p, p

    def full(self, t: float, theta_3b: float, omega_3b: float) -> PedalWrench | None:
        return None


class _PeriodicTemplate:
    """Shared machinery for every crank-angle-indexed template: `P_D =
    _shape(theta_3b)`, `P_F = _shape(theta_3b + pi)` (models.tex: "the two
    pedals offset by half a revolution"). Subclasses implement `_shape`
    only."""

    def _shape(self, theta: float) -> float:
        raise NotImplementedError

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        return self._shape(theta_3b), self._shape(theta_3b + math.pi)

    def full(self, t: float, theta_3b: float, omega_3b: float) -> PedalWrench | None:
        return None


@dataclass(frozen=True)
class CosSquared(_PeriodicTemplate):
    """`P_D(theta) = P_0*cos^2(theta - phi)` (models.tex "Periodic
    analytical"). Squared, so never negative."""

    P_0: float
    phi: float = 0.0

    def _shape(self, theta: float) -> float:
        return self.P_0 * math.cos(theta - self.phi) ** 2


@dataclass(frozen=True)
class HarmonicSeries(_PeriodicTemplate):
    """`P_D(theta) = (a_0 + sum_n a_n*cos(n*theta - phi_n))^2` (models.tex).
    `a_n`/`phi_n` are `n = 1..N` harmonic coefficients/phases; `a_0` is the
    DC term. Squared, so never negative regardless of the inner sum's
    sign."""

    a_0: float
    a_n: tuple[float, ...] = ()
    phi_n: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if len(self.a_n) != len(self.phi_n):
            raise ValueError(
                f"a_n and phi_n must have the same length, "
                f"got {len(self.a_n)} and {len(self.phi_n)}"
            )

    def _shape(self, theta: float) -> float:
        inner = self.a_0 + sum(
            a * math.cos(n * theta - phi)
            for n, (a, phi) in enumerate(zip(self.a_n, self.phi_n, strict=True), start=1)
        )
        return inner**2


@dataclass(frozen=True)
class ClippedSine(_PeriodicTemplate):
    """`P_D(theta) = P_0*[max(0, sin(theta - theta_0))]^n` (models.tex
    "Shape-constrained templates"): a powered, dead-zone-clipped sine."""

    P_0: float
    theta_0: float = 0.0
    n: float = 2.0

    def _shape(self, theta: float) -> float:
        return self.P_0 * max(0.0, math.sin(theta - self.theta_0)) ** self.n


@dataclass(frozen=True)
class VonMises(_PeriodicTemplate):
    """`P_D(theta) = P_0*exp(kappa*(cos(theta - theta_0) - 1))` (models.tex):
    a circular-Gaussian bump, smooth and non-negative everywhere without a
    hard clip."""

    P_0: float
    theta_0: float = 0.0
    kappa: float = 1.0

    def _shape(self, theta: float) -> float:
        return self.P_0 * math.exp(self.kappa * (math.cos(theta - self.theta_0) - 1.0))


@dataclass(frozen=True)
class TabulatedTime:
    """Measured/data-driven pedal forces indexed by time `t` (models.tex
    "Data-driven"), linearly interpolated; `theta_3b`/`omega_3b` are
    ignored."""

    t: FloatArray
    P_D: FloatArray
    P_F: FloatArray

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        return float(np.interp(t, self.t, self.P_D)), float(np.interp(t, self.t, self.P_F))

    def full(self, t: float, theta_3b: float, omega_3b: float) -> PedalWrench | None:
        return None


@dataclass(frozen=True)
class PiecewiseConstantTime:
    """The optimizer's decision-variable carrier: a value held constant
    over each `[knots[i], knots[i+1])` interval, independently for `P_D`
    and `P_F`. `t < knots[0]` or `t >= knots[-1]` holds the first/last
    value."""

    knots: FloatArray
    P_D_values: FloatArray
    P_F_values: FloatArray

    def __post_init__(self) -> None:
        if (
            len(self.P_D_values) != len(self.knots) - 1
            or len(self.P_F_values) != len(self.knots) - 1
        ):
            raise ValueError("P_D_values/P_F_values must have len(knots) - 1 entries")

    def _lookup(self, t: float, values: FloatArray) -> float:
        idx = int(np.searchsorted(self.knots, t, side="right")) - 1
        idx = max(0, min(idx, len(values) - 1))
        return float(values[idx])

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        return self._lookup(t, self.P_D_values), self._lookup(t, self.P_F_values)

    def full(self, t: float, theta_3b: float, omega_3b: float) -> PedalWrench | None:
        return None


@dataclass(frozen=True)
class TabulatedCrankAngle:
    """Measured/data-driven pedal forces indexed by crank angle
    `theta_3b in [0, 2*pi)` (models.tex "Data-driven": "a normalized
    torque-curve shape ... as a fixed template"), linearly interpolated
    with wraparound; `t`/`omega_3b` are ignored."""

    theta: FloatArray
    P_D: FloatArray
    P_F: FloatArray

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        wrapped = theta_3b % (2.0 * math.pi)
        p_d = float(np.interp(wrapped, self.theta, self.P_D, period=2.0 * math.pi))
        p_f = float(np.interp(wrapped, self.theta, self.P_F, period=2.0 * math.pi))
        return p_d, p_f

    def full(self, t: float, theta_3b: float, omega_3b: float) -> PedalWrench | None:
        return None
