"""core/brakes.py -- brake-force input models (architecture.md S5.4).

Mirrors `core.pedals`'s `PedalInput` shape: `BrakeInput.forces(t, state)`
returns `(B_2a_1, B_2b_1)`, the rear/front brake force magnitudes [N]
(`latex/simplified/forward_motion.tex`'s `B_{2a/1}`, `B_{2b/1}`). Every
implementation returns non-negative magnitudes -- the direction (always
resisting motion) is applied downstream via `sigma`, exactly like the
rolling-resistance shift in `latex/2d_assembly/wheels_ground.tex`, not
encoded here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple, Protocol

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


class KinematicState(NamedTuple):
    """The minimal kinematic state a `BrakeInput` may need beyond `t`:
    position `x` [m] (for `BrakeAtDistance`) and speed `v` [m/s]."""

    x: float
    v: float


class BrakeInput(Protocol):
    """architecture.md S5.4."""

    def forces(self, t: float, state: KinematicState) -> tuple[float, float]:
        """`(B_2a_1, B_2b_1)`, the rear/front brake force magnitudes [N],
        both `>= 0`."""
        ...


@dataclass(frozen=True)
class ConstantBrake:
    """A fixed brake force at each wheel, independent of `t`/`state`."""

    B_2a: float = 0.0
    B_2b: float = 0.0

    def __post_init__(self) -> None:
        if self.B_2a < 0 or self.B_2b < 0:
            raise ValueError(f"brake forces must be >= 0, got B_2a={self.B_2a}, B_2b={self.B_2b}")

    def forces(self, t: float, state: KinematicState) -> tuple[float, float]:
        return self.B_2a, self.B_2b


@dataclass(frozen=True)
class BrakeSchedule:
    """A piecewise-linear brake-force ramp over time: `t_knots` paired with
    `B_2a_knots`/`B_2b_knots`, linearly interpolated and held constant
    before the first / after the last knot."""

    t_knots: FloatArray
    B_2a_knots: FloatArray
    B_2b_knots: FloatArray

    def forces(self, t: float, state: KinematicState) -> tuple[float, float]:
        b_2a = float(np.interp(t, self.t_knots, self.B_2a_knots))
        b_2b = float(np.interp(t, self.t_knots, self.B_2b_knots))
        return b_2a, b_2b


@dataclass(frozen=True)
class TabulatedBrake:
    """Measured/data-driven brake forces indexed by time, linearly
    interpolated (same shape as `BrakeSchedule`; kept as a distinct class
    to mirror `core.pedals.TabulatedTime`'s "this came from data, not a
    hand-authored ramp" naming)."""

    t: FloatArray
    B_2a: FloatArray
    B_2b: FloatArray

    def forces(self, t: float, state: KinematicState) -> tuple[float, float]:
        b_2a = float(np.interp(t, self.t, self.B_2a))
        b_2b = float(np.interp(t, self.t, self.B_2b))
        return b_2a, b_2b


@dataclass(frozen=True)
class BrakeAtDistance:
    """A step trigger: full brake force `(B_2a, B_2b)` once `state.x >=
    x_trigger`, zero before it."""

    x_trigger: float
    B_2a: float = 0.0
    B_2b: float = 0.0

    def __post_init__(self) -> None:
        if self.B_2a < 0 or self.B_2b < 0:
            raise ValueError(f"brake forces must be >= 0, got B_2a={self.B_2a}, B_2b={self.B_2b}")

    def forces(self, t: float, state: KinematicState) -> tuple[float, float]:
        if state.x >= self.x_trigger:
            return self.B_2a, self.B_2b
        return 0.0, 0.0
