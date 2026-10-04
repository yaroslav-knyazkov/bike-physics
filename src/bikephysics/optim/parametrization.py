"""optim/parametrization.py -- decision-variable parametrizations
(architecture.md §8.2).

Each `ControlParametrization` maps a flat decision vector `x` onto a
`optim.forward.ControlInputs` (a `PedalInput` + `BrakeInput` + optional
`BikeSpec` override). `bounds`/`initial_guess` take the nominal `BikeSpec`
so a parametrization can size its defaults off it (e.g. `GearSelection`
recomputing a sprocket radius); `build`/`describe` take only `x` -- any
spec-derived constant a parametrization needs at `build()` time (crank
ratio, chain pitch) is captured once at *construction*, via a `spec`
constructor argument, not re-derived per call. `optim.problem.py`'s TOML
loader is what supplies that `spec` when building a parametrization from a
problem deck.

Six implementations plus `Composite`, per §8.2's table:
`TemplateShape`, `PiecewiseConstantPedal(N)`, `SplinePedal(N)`,
`PiecewiseConstantBrake(N)`, `GearSelection(cog_teeth_options)`,
`Composite([...])`.

`TemplateShape`'s `kind="constant_power"` is a deliberate extension beyond
the three named `core.pedals` periodic templates (`von_mises`,
`clipped_sine`, `cos_squared`): a 1-variable control that holds the
*propulsive* power constant (force = power / speed, via `ConstantPowerPedal`
below) rather than the pedal force shape. It exists so the numeric solvers
have a parametrization capable of representing the exact control structure
`optim.solvers.analytic.AnalyticSimplified`'s Pontryagin derivation predicts
is optimal for the flat/constant-power/limit-free min-time oracle problem
(architecture.md §8.4's "Pontryagin reference ... a genuine correctness
oracle for the whole optimization stack") -- without it, no numeric solver
in this ticket could ever match that oracle to the acceptance test's
< 0.5 % tolerance, since a periodic pedal-force template cannot represent a
speed-dependent force.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt
from scipy.interpolate import CubicSpline

from bikephysics.core.brakes import BrakeInput, ConstantBrake
from bikephysics.core.params import BikeSpec
from bikephysics.core.pedals import (
    ClippedSine,
    CosSquared,
    PedalInput,
    PiecewiseConstantTime,
    VonMises,
    ZeroPedal,
)
from bikephysics.optim.forward import ControlInputs

FloatArray = npt.NDArray[np.float64]

#: `PiecewiseConstantPedal`/`SplinePedal`'s per-interval initial guess, as
#: a fraction of `p_bounds`'s own range above `lo` -- deliberately *not*
#: the midpoint. `p_bounds` is typically wide (headroom for the solver to
#: find the real optimum), but a cold start near its midpoint is already
#: deep into power-ceiling-violating territory for any realistic cycling
#: speed (e.g. 500 N at 8 m/s is 4000 W, ten times a plausible ~400 W
#: ceiling), which measurably slows (and on some problems, within a
#: modest iteration budget, prevents) SLSQP's return to feasibility. A
#: low fraction near `lo` is a much better-conditioned cold start for a
#: control that is expected to saturate an *upper* bound, not center on
#: the range -- a numerical-tuning constant, not a physical claim.
_INITIAL_GUESS_FRACTION = 0.15


class ControlParametrization(Protocol):
    """architecture.md §8.2. `n_vars` is declared as a `@property` (not a
    plain field) so mypy accepts concrete classes that compute it (e.g.
    from `len(self.cog_teeth_options)` or `sum(p.n_vars for p in parts)`)
    rather than storing it -- a Protocol field declared as a plain
    attribute is structurally settable, which a read-only `@property`
    implementation does not satisfy."""

    @property
    def n_vars(self) -> int: ...

    def bounds(self, spec: BikeSpec) -> list[tuple[float, float]]: ...

    def initial_guess(self, spec: BikeSpec) -> FloatArray: ...

    def build(self, x: FloatArray) -> ControlInputs: ...

    def describe(self, x: FloatArray) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ConstantPowerPedal:
    """A `PedalInput` that delivers constant *propulsive* power: the
    wheel-frame driving force is `power / v`, reconstructed from `omega_3b`
    (since `forces(t, theta_3b, omega_3b)` is not given `v` directly) via
    the same crank-ratio algebra `simplified.simulate`'s module docstring
    documents, `v = -omega_3b / crank_ratio`. `gear_factor` is
    `model.accel`'s own pedal-term prefactor,
    `(r_1,pedal*r_2a,cg)/(r_3b,cr*r_2a)`, so `gear_factor*(P_D+P_F) ==
    power/v` exactly -- i.e. this class is the one place in `optim` that
    inverts `model.accel`'s pedal term, used both by
    `TemplateShape(kind="constant_power")` and by
    `optim.solvers.analytic.AnalyticSimplified` (which needs the identical
    control to turn its closed-form answer into a real `Trajectory`).

    `v_floor` regularizes the `v -> 0` singularity (infinite force at a
    standing start under literal constant power) the same way
    `core.mathx.sign_smooth`'s `v_eps` regularizes `sign(v)` elsewhere in
    this project -- a numerical floor, not a physical claim."""

    power: float
    crank_ratio: float
    gear_factor: float
    v_floor: float = 1e-6

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        v = -omega_3b / self.crank_ratio if self.crank_ratio != 0.0 else 0.0
        v_eff = max(abs(v), self.v_floor)
        wheel_force = self.power / v_eff
        total_pedal_force = wheel_force / self.gear_factor
        return total_pedal_force, 0.0

    def full(self, t: float, theta_3b: float, omega_3b: float) -> None:
        return None


def gear_factor(spec: BikeSpec) -> float:
    """`model.accel`'s pedal-term prefactor, `(r_1,pedal*r_2a,cg)/(r_3b,cr*
    r_2a)` -- public (not module-private) because `optim.solvers.analytic.
    AnalyticSimplified` needs the identical constant to build the same
    `ConstantPowerPedal` control its closed-form answer assumes."""
    dt = spec.drivetrain
    return (spec.frame.r_1_pedal * dt.r_2a_cg) / (dt.r_3b_cr * spec.rear.r_2a)


def crank_ratio(spec: BikeSpec) -> float:
    """`r_2a,cg/(r_3b,cr*r_2a)` -- public for the same reason as
    `gear_factor` above."""
    dt = spec.drivetrain
    return dt.r_2a_cg / (dt.r_3b_cr * spec.rear.r_2a)


@dataclass
class TemplateShape:
    """2-4 decision variables over a `core.pedals` periodic template
    (`P_0`, `theta_0`, and `n`/`kappa`), or 1 variable for the degenerate
    `kind="constant_power"` control -- "the cheap first milestone"
    (architecture.md §8.2). `fit_shape` toggles whether the template's own
    shape parameter (`n` for `clipped_sine`, `kappa` for `von_mises`,
    unused for `cos_squared`) is a free variable or held at
    `shape_bounds`'s midpoint; `fit_brake` adds one more shared
    constant-brake-force variable, split evenly front/rear."""

    kind: Literal["von_mises", "clipped_sine", "cos_squared", "constant_power"] = "von_mises"
    fit_shape: bool = True
    fit_brake: bool = False
    p_bounds: tuple[float, float] = (0.0, 1000.0)
    theta_bounds: tuple[float, float] = (0.0, 2.0 * math.pi)
    shape_bounds: tuple[float, float] = (0.5, 6.0)
    brake_bounds: tuple[float, float] = (0.0, 500.0)
    spec: BikeSpec | None = None

    def __post_init__(self) -> None:
        if self.kind == "constant_power" and self.spec is None:
            raise ValueError(
                "TemplateShape(kind='constant_power') requires a `spec` "
                "(needed to convert a power level into a pedal force); "
                "optim.problem's TOML loader supplies this automatically."
            )

    @property
    def n_vars(self) -> int:
        if self.kind == "constant_power":
            return 1
        return 2 + int(self.fit_shape) + int(self.fit_brake)

    def bounds(self, spec: BikeSpec) -> list[tuple[float, float]]:
        if self.kind == "constant_power":
            return [self.p_bounds]
        result = [self.p_bounds, self.theta_bounds]
        if self.fit_shape:
            result.append(self.shape_bounds)
        if self.fit_brake:
            result.append(self.brake_bounds)
        return result

    def initial_guess(self, spec: BikeSpec) -> FloatArray:
        if self.kind == "constant_power":
            return np.array([0.5 * (self.p_bounds[0] + self.p_bounds[1])])
        values = [0.5 * (self.p_bounds[0] + self.p_bounds[1]), 0.0]
        if self.fit_shape:
            values.append(0.5 * (self.shape_bounds[0] + self.shape_bounds[1]))
        if self.fit_brake:
            values.append(0.0)
        return np.array(values, dtype=float)

    def _unpack(self, x: FloatArray) -> tuple[float, float, float | None, float]:
        xs = np.asarray(x, dtype=float)
        idx = 0
        p0 = float(xs[idx])
        idx += 1
        theta0 = float(xs[idx])
        idx += 1
        shape: float | None = None
        if self.fit_shape:
            shape = float(xs[idx])
            idx += 1
        brake = 0.0
        if self.fit_brake:
            brake = float(xs[idx])
            idx += 1
        return p0, theta0, shape, brake

    def build(self, x: FloatArray) -> ControlInputs:
        if self.kind == "constant_power":
            assert self.spec is not None  # guarded by __post_init__
            power = float(np.asarray(x, dtype=float).reshape(-1)[0])
            constant_power_pedal = ConstantPowerPedal(
                power=power, crank_ratio=crank_ratio(self.spec), gear_factor=gear_factor(self.spec)
            )
            return ControlInputs(pedal=constant_power_pedal)

        p0, theta0, shape, brake = self._unpack(x)
        pedal: PedalInput
        if self.kind == "von_mises":
            pedal = VonMises(P_0=p0, theta_0=theta0, kappa=shape if shape is not None else 1.0)
        elif self.kind == "clipped_sine":
            pedal = ClippedSine(P_0=p0, theta_0=theta0, n=shape if shape is not None else 2.0)
        elif self.kind == "cos_squared":
            pedal = CosSquared(P_0=p0, phi=theta0)
        else:
            raise ValueError(f"unknown TemplateShape kind {self.kind!r}")
        brake_input = ConstantBrake(B_2a=brake * 0.5, B_2b=brake * 0.5)
        return ControlInputs(pedal=pedal, brake=brake_input)

    def describe(self, x: FloatArray) -> dict[str, Any]:
        if self.kind == "constant_power":
            return {"kind": self.kind, "power": float(np.asarray(x, dtype=float).reshape(-1)[0])}
        p0, theta0, shape, brake = self._unpack(x)
        out: dict[str, Any] = {"kind": self.kind, "P_0": p0, "theta_0": theta0}
        if shape is not None:
            out["shape"] = shape
        if self.fit_brake:
            out["brake_force"] = brake
        return out


@dataclass
class PiecewiseConstantPedal:
    """`N` decision variables, one combined (`P_D == P_F`) pedal force per
    `[knots[i], knots[i+1])` interval over `[0, t_max]` -- the §8.2 "Direct
    shooting control vector" built directly on `core.pedals.
    PiecewiseConstantTime`. `t_max` is this parametrization's own copy of
    the problem horizon's end time (injected by `optim.problem`'s builder),
    needed to place the `N+1` knots; it is not re-derived from `x`."""

    n: int
    t_max: float
    p_bounds: tuple[float, float] = (0.0, 1000.0)

    @property
    def n_vars(self) -> int:
        return self.n

    def _knots(self) -> FloatArray:
        return np.linspace(0.0, self.t_max, self.n + 1)

    def bounds(self, spec: BikeSpec) -> list[tuple[float, float]]:
        return [self.p_bounds] * self.n

    def initial_guess(self, spec: BikeSpec) -> FloatArray:
        lo, hi = self.p_bounds
        return np.full(self.n, lo + _INITIAL_GUESS_FRACTION * (hi - lo))

    def build(self, x: FloatArray) -> ControlInputs:
        values = np.clip(np.asarray(x, dtype=float), 0.0, None)
        pedal = PiecewiseConstantTime(knots=self._knots(), P_D_values=values, P_F_values=values)
        return ControlInputs(pedal=pedal)

    def describe(self, x: FloatArray) -> dict[str, Any]:
        values = np.clip(np.asarray(x, dtype=float), 0.0, None)
        return {"knots": self._knots().tolist(), "pedal_force": values.tolist()}


@dataclass(frozen=True)
class _SplinePedalInput:
    """A `PedalInput` driven by a `scipy.interpolate.CubicSpline` over
    `P_D(t)`, `P_F = 0` -- `SplinePedal`'s control carrier. Clipped at 0:
    every other pedal template in this project is non-negative
    everywhere, and a spline through non-negative control points can still
    dip negative between knots."""

    spline: CubicSpline

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        return max(0.0, float(self.spline(t))), 0.0

    def full(self, t: float, theta_3b: float, omega_3b: float) -> None:
        return None


@dataclass
class SplinePedal:
    """`N` decision variables, the pedal force at `N` evenly-spaced knots
    over `[0, t_max]`, interpolated by a `CubicSpline` -- "smoother,
    better-conditioned than piecewise-constant for gradient methods"
    (architecture.md §8.2)."""

    n: int
    t_max: float
    p_bounds: tuple[float, float] = (0.0, 1000.0)

    @property
    def n_vars(self) -> int:
        return self.n

    def _knots(self) -> FloatArray:
        return np.linspace(0.0, self.t_max, self.n)

    def bounds(self, spec: BikeSpec) -> list[tuple[float, float]]:
        return [self.p_bounds] * self.n

    def initial_guess(self, spec: BikeSpec) -> FloatArray:
        lo, hi = self.p_bounds
        return np.full(self.n, lo + _INITIAL_GUESS_FRACTION * (hi - lo))

    def build(self, x: FloatArray) -> ControlInputs:
        values = np.clip(np.asarray(x, dtype=float), 0.0, None)
        spline = CubicSpline(self._knots(), values, extrapolate=True)
        return ControlInputs(pedal=_SplinePedalInput(spline=spline))

    def describe(self, x: FloatArray) -> dict[str, Any]:
        values = np.clip(np.asarray(x, dtype=float), 0.0, None)
        return {"knots": self._knots().tolist(), "pedal_force": values.tolist()}


@dataclass(frozen=True)
class _PiecewiseConstantBrakeInput:
    """`BrakeInput` mirror of `core.pedals.PiecewiseConstantTime`, local to
    `optim` (not added to `core.brakes` -- out of this ticket's file
    boundary, and `BrakeInput` is a `Protocol`, open to any implementer)."""

    knots: FloatArray
    B_2a_values: FloatArray
    B_2b_values: FloatArray

    def _lookup(self, t: float, values: FloatArray) -> float:
        idx = int(np.searchsorted(self.knots, t, side="right")) - 1
        idx = max(0, min(idx, len(values) - 1))
        return float(values[idx])

    def forces(self, t: float, state: Any) -> tuple[float, float]:
        return self._lookup(t, self.B_2a_values), self._lookup(t, self.B_2b_values)


@dataclass
class PiecewiseConstantBrake:
    """`N` decision variables, one combined brake force per interval
    (split `front_bias`/`1-front_bias` front/rear) -- "needed for
    descent/corner-entry problems" (architecture.md §8.2). Carries no
    pedal input of its own (`ZeroPedal`); combine with a pedal
    parametrization via `Composite` for a pedal+brake problem."""

    n: int
    t_max: float
    b_bounds: tuple[float, float] = (0.0, 500.0)
    front_bias: float = 0.5

    @property
    def n_vars(self) -> int:
        return self.n

    def _knots(self) -> FloatArray:
        return np.linspace(0.0, self.t_max, self.n + 1)

    def bounds(self, spec: BikeSpec) -> list[tuple[float, float]]:
        return [self.b_bounds] * self.n

    def initial_guess(self, spec: BikeSpec) -> FloatArray:
        return np.zeros(self.n)

    def build(self, x: FloatArray) -> ControlInputs:
        total = np.clip(np.asarray(x, dtype=float), 0.0, None)
        b_2b = total * self.front_bias
        b_2a = total * (1.0 - self.front_bias)
        brake = _PiecewiseConstantBrakeInput(
            knots=self._knots(), B_2a_values=b_2a, B_2b_values=b_2b
        )
        return ControlInputs(pedal=ZeroPedal(), brake=brake)

    def describe(self, x: FloatArray) -> dict[str, Any]:
        total = np.clip(np.asarray(x, dtype=float), 0.0, None)
        return {"knots": self._knots().tolist(), "brake_force": total.tolist()}


@dataclass
class GearSelection:
    """1 (nominally discrete) decision variable: an index into
    `cog_teeth_options`, relaxed to a continuous `[0, len-1]` range and
    rounded in `build()` so it fits the shared `ControlParametrization`
    shape (bounds/initial_guess/build/describe). architecture.md §8.2 is
    explicit that the *intended* solve strategy is enumeration (an outer
    loop over `cog_teeth_options`, each iteration fixing the gear and
    optimizing the rest), not a relaxed continuous radius -- see
    `optim.solvers.base.enumerate_discrete` for that outer loop; this
    class's continuous relaxation exists so `ScipyLocal`/`ScipyGlobal` can
    still be pointed at a gear-inclusive problem directly, with the
    understanding that the rounding makes its own gradient uninformative
    (`enumerate_discrete` is the numerically trustworthy path).

    `spec` is needed at `build()` time to recompute `r_2a_cg` from the
    selected tooth count via the same pitch-radius formula
    `core.params.DrivetrainParams.radii_from_teeth` uses -- setting
    `drivetrain.n_teeth_cg` alone would not change the dynamics, since
    `simplified/model.py` reads `r_2a_cg` directly, never
    `radii_from_teeth()`."""

    cog_teeth_options: tuple[int, ...]
    spec: BikeSpec

    def __post_init__(self) -> None:
        if not self.cog_teeth_options:
            raise ValueError("GearSelection needs at least one cog_teeth_options entry")

    @property
    def n_vars(self) -> int:
        return 1

    def bounds(self, spec: BikeSpec) -> list[tuple[float, float]]:
        return [(0.0, float(len(self.cog_teeth_options) - 1))]

    def initial_guess(self, spec: BikeSpec) -> FloatArray:
        return np.array([0.0])

    def _index(self, x: FloatArray) -> int:
        idx = int(round(float(np.asarray(x, dtype=float).reshape(-1)[0])))
        return max(0, min(idx, len(self.cog_teeth_options) - 1))

    def _r_2a_cg(self, n_teeth: int) -> float:
        pitch = self.spec.drivetrain.chain_pitch
        return pitch / (2.0 * math.sin(math.pi / n_teeth))

    def build(self, x: FloatArray) -> ControlInputs:
        n_teeth = self.cog_teeth_options[self._index(x)]
        return ControlInputs(
            pedal=ZeroPedal(),
            spec_overrides={
                "drivetrain.n_teeth_cg": n_teeth,
                "drivetrain.r_2a_cg": self._r_2a_cg(n_teeth),
            },
        )

    def describe(self, x: FloatArray) -> dict[str, Any]:
        idx = self._index(x)
        n_teeth = self.cog_teeth_options[idx]
        return {"index": idx, "n_teeth_cg": n_teeth, "r_2a_cg": self._r_2a_cg(n_teeth)}


def _is_trivial_pedal(pedal: PedalInput) -> bool:
    return isinstance(pedal, ZeroPedal)


def _is_trivial_brake(brake: BrakeInput) -> bool:
    return isinstance(brake, ConstantBrake) and brake.B_2a == 0.0 and brake.B_2b == 0.0


@dataclass
class Composite:
    """Concatenates several parametrizations' decision vectors into one --
    "this is how 'pedal + brake + gear' problems are expressed"
    (architecture.md §8.2). Each part is expected to own a *disjoint*
    control channel (pedal, brake, or a `spec_overrides` key); `build()`
    merges the parts' `ControlInputs` by picking the one non-trivial pedal
    and the one non-trivial brake across all parts (raising `ValueError` if
    two parts both produce a non-trivial pedal or brake -- an ambiguous
    combination this class refuses to silently resolve) and unioning every
    part's `spec_overrides` (later parts win on a key collision)."""

    parts: Sequence[ControlParametrization]

    @property
    def n_vars(self) -> int:
        return sum(p.n_vars for p in self.parts)

    def bounds(self, spec: BikeSpec) -> list[tuple[float, float]]:
        result: list[tuple[float, float]] = []
        for p in self.parts:
            result.extend(p.bounds(spec))
        return result

    def initial_guess(self, spec: BikeSpec) -> FloatArray:
        if not self.parts:
            return np.array([])
        return np.concatenate([np.asarray(p.initial_guess(spec), dtype=float) for p in self.parts])

    def _split(self, x: FloatArray) -> list[FloatArray]:
        xs = np.asarray(x, dtype=float)
        pieces: list[FloatArray] = []
        i = 0
        for p in self.parts:
            pieces.append(xs[i : i + p.n_vars])
            i += p.n_vars
        return pieces

    def build(self, x: FloatArray) -> ControlInputs:
        controls = [p.build(xi) for p, xi in zip(self.parts, self._split(x), strict=True)]
        pedals = [c.pedal for c in controls if not _is_trivial_pedal(c.pedal)]
        brakes = [c.brake for c in controls if not _is_trivial_brake(c.brake)]
        if len(pedals) > 1:
            raise ValueError("Composite: more than one part produced a non-trivial pedal input")
        if len(brakes) > 1:
            raise ValueError("Composite: more than one part produced a non-trivial brake input")
        pedal = pedals[0] if pedals else ZeroPedal()
        brake = brakes[0] if brakes else ConstantBrake()
        overrides: dict[str, Any] = {}
        for c in controls:
            overrides.update(c.spec_overrides)
        return ControlInputs(pedal=pedal, brake=brake, spec_overrides=overrides)

    def describe(self, x: FloatArray) -> dict[str, Any]:
        return {
            f"part_{i}_{type(p).__name__}": p.describe(xi)
            for i, (p, xi) in enumerate(zip(self.parts, self._split(x), strict=True))
        }
