"""optim/constraints.py -- path constraints over a rollout `Trajectory`
(architecture.md §8.3).

`g(traj) <= 0` is feasible, elementwise over time (or over a small fixed
set of terminal residuals for `TerminalState`). `penalty(traj, weight)` is
the shared penalty-mode fallback every constraint gets for free from
`_DefaultPenaltyMixin` (`weight * sum(max(0, g)**2)`), used by solvers that
aggregate constraints into the objective (`ScipyGlobal`, and `ScipyLocal`'s
`L-BFGS-B` method, which has no native inequality-constraint support);
`SLSQP`/`trust-constr` instead call `g` directly as a native constraint.

**No hardcoded physical number** (architecture.md §8.3, the ticket's own
instruction, `latex/appendix/refinements.tex`): `PedalForceBound.P_max`/
`PowerCeiling.p_max` are required constructor arguments with no default --
every numeric limit comes from the problem TOML's `[[constraints]]` block
(architecture.md §8.5's own worked example: `p_max = 400.0` sits in the
TOML, not in this module) or from a caller-supplied `BikeSpec.rider` value,
never invented here. `PowerCeiling.p_max` additionally accepts a
`P_max(cadence)` callable (the rider power-curve hook for the future
Strategy track).

`PedalForceBound` reads `traj.meta["P_D"]`/`["P_F"]`, populated only by
`optim.forward.SimplifiedForwardModel.rollout` (see that module's
docstring) -- `Trajectory`'s own schema has no per-pedal force field, only
the combined `power_pedal`. Using this constraint against a `Trajectory`
without that `meta` augmentation raises a clear error naming the cause.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from bikephysics.core.results import Trajectory

FloatArray = npt.NDArray[np.float64]


class PathConstraint(Protocol):
    """architecture.md §8.3."""

    requires: frozenset[str]

    def g(self, traj: Trajectory) -> FloatArray: ...

    def penalty(self, traj: Trajectory, weight: float) -> float: ...


class _DefaultPenaltyMixin:
    """`penalty(traj, weight) = weight * sum(max(0, g(traj))**2)` -- the
    shared penalty-mode fallback every `PathConstraint` below reuses rather
    than re-implementing. Not itself a dataclass field holder; mixed in
    alongside `@dataclass` on each concrete class."""

    def g(self, traj: Trajectory) -> FloatArray:  # pragma: no cover - overridden
        raise NotImplementedError

    def penalty(self, traj: Trajectory, weight: float) -> float:
        g = np.atleast_1d(np.asarray(self.g(traj), dtype=float))
        return float(weight * np.sum(np.maximum(g, 0.0) ** 2))


def _require_meta_pedal_forces(traj: Trajectory) -> tuple[FloatArray, FloatArray]:
    try:
        return np.asarray(traj.meta["P_D"], dtype=float), np.asarray(traj.meta["P_F"], dtype=float)
    except KeyError as exc:
        raise KeyError(
            "PedalForceBound requires Trajectory.meta['P_D']/['P_F'], populated by "
            "optim.forward.SimplifiedForwardModel.rollout (architecture.md S8.1); "
            "this Trajectory does not carry them."
        ) from exc


@dataclass
class PowerCeiling(_DefaultPenaltyMixin):
    """`power_pedal(t) <= p_max` (or `p_max(cadence)` when `p_max` is a
    callable, taking `omega_3b` [rad/s] -- the rider power-curve hook for
    the future Strategy track). `p_max` has no default: it must come from
    the problem TOML or an explicit `BikeSpec.rider.P_max`, never invented
    here (module docstring)."""

    p_max: float | Callable[[float], float]

    requires: frozenset[str] = frozenset({"power_pedal", "omega_3b"})

    def _limit(self, traj: Trajectory) -> FloatArray:
        if callable(self.p_max):
            return np.array([self.p_max(float(w)) for w in traj.omega_3b], dtype=float)
        return np.full_like(traj.power_pedal, float(self.p_max))

    def g(self, traj: Trajectory) -> FloatArray:
        return traj.power_pedal - self._limit(traj)


@dataclass
class PedalForceBound(_DefaultPenaltyMixin):
    """`p_min <= P_D(t), P_F(t) <= p_max`, read from `traj.meta["P_D"]`/
    `["P_F"]` (module docstring). `p_max` has no default (required);
    `p_min=None` means no lower bound (not a hardcoded 0 -- a pedal force
    can legitimately be exactly 0, e.g. coasting mid-stroke). Field names
    are lowercase to match architecture.md §8.5's own TOML example
    (`[[constraints]] kind = "pedal_force_bound" \n p_max = 800.0`)."""

    p_max: float
    p_min: float | None = None

    requires: frozenset[str] = frozenset({"meta"})

    def g(self, traj: Trajectory) -> FloatArray:
        p_d, p_f = _require_meta_pedal_forces(traj)
        terms = [p_d - self.p_max, p_f - self.p_max]
        if self.p_min is not None:
            terms.extend([self.p_min - p_d, self.p_min - p_f])
        return np.concatenate(terms)


@dataclass
class CadenceBound(_DefaultPenaltyMixin):
    """`|omega_3b(t)| <= omega_max` (and `>= omega_min` when given) --
    `omega_max` has no default (required): `BikeSpec.rider.cadence_max`
    documents that this bound, like the pedal-force/power ones, is not
    specified anywhere in the physics."""

    omega_max: float
    omega_min: float | None = None

    requires: frozenset[str] = frozenset({"omega_3b"})

    def g(self, traj: Trajectory) -> FloatArray:
        speed = np.abs(traj.omega_3b)
        terms = [speed - self.omega_max]
        if self.omega_min is not None:
            terms.append(self.omega_min - speed)
        return np.concatenate(terms)


@dataclass
class TractionMargin(_DefaultPenaltyMixin):
    """Both contacts' traction margins (`Trajectory.margins.traction_rear`/
    `traction_front`), read directly -- already `<= 0` = feasible by
    construction (`core.limits`), so no extra parameter is needed."""

    requires: frozenset[str] = frozenset({"margins"})

    def g(self, traj: Trajectory) -> FloatArray:
        return np.concatenate([traj.margins.traction_rear, traj.margins.traction_front])


@dataclass
class WheelieMargin(_DefaultPenaltyMixin):
    """`Trajectory.margins.wheelie`, read directly."""

    requires: frozenset[str] = frozenset({"margins"})

    def g(self, traj: Trajectory) -> FloatArray:
        return np.asarray(traj.margins.wheelie, dtype=float)


@dataclass
class StoppieMargin(_DefaultPenaltyMixin):
    """`Trajectory.margins.stoppie`, read directly."""

    requires: frozenset[str] = frozenset({"margins"})

    def g(self, traj: Trajectory) -> FloatArray:
        return np.asarray(traj.margins.stoppie, dtype=float)


@dataclass
class ChainTensionNonNegative(_DefaultPenaltyMixin):
    """`Trajectory.margins.freewheel` (`<= 0` means `T >= 0`), read
    directly."""

    requires: frozenset[str] = frozenset({"margins"})

    def g(self, traj: Trajectory) -> FloatArray:
        return np.asarray(traj.margins.freewheel, dtype=float)


@dataclass
class SpeedBounds(_DefaultPenaltyMixin):
    """`v_min <= v(t) <= v_max`; at least one bound must be given."""

    v_min: float | None = None
    v_max: float | None = None

    requires: frozenset[str] = frozenset({"v"})

    def __post_init__(self) -> None:
        if self.v_min is None and self.v_max is None:
            raise ValueError("SpeedBounds needs at least one of v_min, v_max")

    def g(self, traj: Trajectory) -> FloatArray:
        terms = []
        if self.v_min is not None:
            terms.append(self.v_min - traj.v)
        if self.v_max is not None:
            terms.append(traj.v - self.v_max)
        return np.concatenate(terms)


@dataclass
class TerminalState(_DefaultPenaltyMixin):
    """Terminal-value residuals: `|x[-1] - x_target| <= tol` and/or
    `|v[-1] - v_target| <= tol`; at least one target must be given. `g`
    returns a length-1 or length-2 array (one residual per target given),
    not a time series -- this is the one `PathConstraint` whose natural
    shape is terminal rather than elementwise-over-time."""

    x_target: float | None = None
    v_target: float | None = None
    tol: float = 1e-6

    requires: frozenset[str] = frozenset({"x", "v"})

    def __post_init__(self) -> None:
        if self.x_target is None and self.v_target is None:
            raise ValueError("TerminalState needs at least one of x_target, v_target")

    def g(self, traj: Trajectory) -> FloatArray:
        terms = []
        if self.x_target is not None:
            terms.append(abs(float(traj.x[-1]) - self.x_target) - self.tol)
        if self.v_target is not None:
            terms.append(abs(float(traj.v[-1]) - self.v_target) - self.tol)
        return np.array(terms, dtype=float)
