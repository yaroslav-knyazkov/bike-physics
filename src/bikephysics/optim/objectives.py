"""optim/objectives.py -- scalar objectives over a rollout `Trajectory`
(architecture.md §8.3).

Every `Objective.value(traj)` returns the quantity a `Solver` *minimizes*
-- `MaxDistanceInTime` therefore returns `-x_final`, documented at each
call site rather than inventing a separate maximize/minimize flag.
`requires` names the `Trajectory` fields the objective reads, per
§8.1/§8.3's own framing ("lets the rollout skip unused post-processing");
no `ForwardModel` in this ticket actually implements that skip (every
field is always computed), so `requires` is informational only for now.

`MinTimeOverDistance`/`MinEnergyWithTimeCap`'s non-arrival penalty and
`MinMechanicalEnergy`'s speed-tracking penalty use a `penalty_weight`
constructor argument with a documented numerical-tuning default -- these
are solver-regularization constants (how hard to push the search back
toward feasibility), not physical quantities, so they are not subject to
the "no hardcoded physical number" rule `optim.constraints.PedalForceBound`/
`PowerCeiling` must follow (architecture.md §8.3, `latex/appendix/
refinements.tex`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from bikephysics.core.results import Trajectory

FloatArray = npt.NDArray[np.float64]


class Objective(Protocol):
    """architecture.md §8.3. `requires` is a `@property` (see
    `optim.parametrization.ControlParametrization`'s `n_vars` docstring
    for why): `Weighted.requires` computes it from its terms rather than
    storing it."""

    @property
    def requires(self) -> frozenset[str]: ...

    def value(self, traj: Trajectory) -> float: ...


def _mechanical_energy(traj: Trajectory) -> float:
    """`integral(power_pedal dt)` -- the mechanical (pedal) energy
    delivered over the rollout, shared by every energy-minimizing
    objective below."""
    return float(np.trapezoid(traj.power_pedal, traj.t))


@dataclass
class MinTimeOverDistance:
    """Minimize elapsed time to cover `x_target`. Uses the terminal
    `x >= x_target` event `simplified.simulate.simulate` already produces
    (`traj.x[-1] == x_target` to solver tolerance when reached); when the
    rollout did not reach `x_target` (coasted to a stop, or hit `t_span`'s
    end first), the returned value is `t_final` plus a smooth quadratic
    slack term in the shortfall, so gradient-based solvers still see a
    useful descent direction instead of a flat, uninformative plateau."""

    x_target: float
    penalty_weight: float = 10.0

    requires: frozenset[str] = frozenset({"t", "x"})

    def value(self, traj: Trajectory) -> float:
        t_final = float(traj.t[-1])
        x_final = float(traj.x[-1])
        shortfall = max(0.0, self.x_target - x_final)
        return t_final + self.penalty_weight * shortfall**2


@dataclass
class MaxDistanceInTime:
    """Maximize distance covered by `t_end` -- returns `-x_final` (the
    quantity to *minimize*); `t_end` is informational here (the horizon
    itself, not re-applied by this objective -- the caller's `Horizon.t_max`
    is what actually bounds the rollout)."""

    t_end: float

    requires: frozenset[str] = frozenset({"x"})

    def value(self, traj: Trajectory) -> float:
        return -float(traj.x[-1])


@dataclass
class MinMechanicalEnergy:
    """Minimize mechanical (pedal) energy while tracking a target mean
    speed `x_final/t_final`, via a quadratic penalty on the shortfall/
    excess -- there is no separate `TerminalState`-style hard constraint
    wired in by default, since this objective's whole point is a smooth
    single-number trade-off a gradient solver can descend."""

    target_mean_speed: float
    penalty_weight: float = 1.0

    requires: frozenset[str] = frozenset({"t", "x", "power_pedal"})

    def value(self, traj: Trajectory) -> float:
        t_final = float(traj.t[-1])
        mean_speed = float(traj.x[-1]) / t_final if t_final > 0 else 0.0
        energy = _mechanical_energy(traj)
        return energy + self.penalty_weight * (mean_speed - self.target_mean_speed) ** 2


@dataclass
class MinEnergyWithTimeCap:
    """Minimize mechanical energy subject to reaching `x_target` within
    `t_cap`, via the same smooth-slack strategy as `MinTimeOverDistance`:
    a quadratic penalty on (a) not reaching `x_target` at all and (b)
    finishing after `t_cap`."""

    x_target: float
    t_cap: float
    penalty_weight: float = 1.0

    requires: frozenset[str] = frozenset({"t", "x", "power_pedal"})

    def value(self, traj: Trajectory) -> float:
        t_final = float(traj.t[-1])
        x_final = float(traj.x[-1])
        energy = _mechanical_energy(traj)
        shortfall = max(0.0, self.x_target - x_final)
        overtime = max(0.0, t_final - self.t_cap)
        return energy + self.penalty_weight * (shortfall**2 + overtime**2)


@dataclass
class TrackSpeedProfile:
    """Minimize the time-integrated squared tracking error against a
    reference speed profile `v_ref(t)`."""

    v_ref: Callable[[float], float]

    requires: frozenset[str] = frozenset({"t", "v"})

    def value(self, traj: Trajectory) -> float:
        ref = np.array([self.v_ref(float(t)) for t in traj.t])
        error_sq = (traj.v - ref) ** 2
        return float(np.trapezoid(error_sq, traj.t))


@dataclass
class Weighted:
    """`sum(weight * objective.value(traj))` over a list of
    `(Objective, weight)` pairs -- the general way to trade off several
    named objectives without inventing a bespoke combined class per
    combination."""

    terms: Sequence[tuple[Objective, float]]

    @property
    def requires(self) -> frozenset[str]:
        result: set[str] = set()
        for objective, _weight in self.terms:
            result |= set(objective.requires)
        return frozenset(result)

    def value(self, traj: Trajectory) -> float:
        return sum(weight * objective.value(traj) for objective, weight in self.terms)
