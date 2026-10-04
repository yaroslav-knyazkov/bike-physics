"""core/limits.py -- the signed-margin forms of the `Limits` block
(`latex/simplified/forward_motion.tex` S Limits; architecture.md S5.1/S5.5).

Both tiers compute ground reactions `R_2a_x, R_2a_z, R_2b_x, R_2b_z` and
chain tension `T` in the shared, `z`-up convention of `core.results`
(`R_*_z >= 0` means loaded). Given those already-computed quantities, the
three margin families below are literal transcriptions of the LaTeX
boxed `Limits` conditions, rewritten as continuous signed margins
(`<= 0` means satisfied) instead of booleans, per
`core.results.LimitMargins`'s own docstring:

- "no wheel lift": `{}^{z0}R_{2a/0} <= 0` / `{}^{z0}R_{2b/0} <= 0` in the
  LaTeX's own `z`-down convention is `R_z >= 0` here (shared convention),
  so the margin is `-R_z`.
- "no slip": `|{}^{x0}R_{k/0}| <= mu_s*(-{}^{z0}R_{k/0})` in the LaTeX's
  `z`-down convention is `|R_x| <= mu_s*R_z` here, so the margin is
  `|R_x| - mu_s*R_z` -- the exact example `LimitMargins`'s own docstring
  gives for `traction_rear`.
- "no freewheel": `T_{2a/3b} >= 0` so the margin is `-T`.

Which wheel's lift margin is named `wheelie` vs `stoppie` follows
forward_motion.tex's own subsections: "Wheelie limit" is the *front*
wheel lifting (hard acceleration), "Stoppie limit" is the *rear* wheel
lifting (hard front braking).
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from bikephysics.core.results import LimitMargins

FloatArray = npt.NDArray[np.float64]


#: Every margin function below is a plain arithmetic expression, so it
#: works unchanged on a scalar `float` (one instant) or a `FloatArray`
#: (a whole `Trajectory`'s time series) -- the same functions both tiers
#: call, at whichever granularity they have reactions available.
def traction_margin[Num: (float, FloatArray)](R_x: Num, R_z: Num, mu_s: float) -> Num:
    """`|R_x| - mu_s*R_z` (forward_motion.tex "Traction limit" /
    "Limits"): `<= 0` means the contact is within the friction cone."""
    return abs(R_x) - mu_s * R_z


def wheel_lift_margin[Num: (float, FloatArray)](R_z: Num) -> Num:
    """`-R_z` (forward_motion.tex "Limits", `z`-up convention): `<= 0`
    means the wheel is still loaded (`R_z >= 0`); `> 0` means it has
    lifted."""
    return -R_z


def freewheel_margin[Num: (float, FloatArray)](T: Num) -> Num:
    """`-T` (forward_motion.tex "Limits": `T_{2a/3b} >= 0`): `<= 0` means
    the chain is in tension; `> 0` means the drivetrain has gone
    freewheel/slack."""
    return -T


def limit_margins(
    *,
    R_2a_x: FloatArray,
    R_2a_z: FloatArray,
    R_2b_x: FloatArray,
    R_2b_z: FloatArray,
    T: FloatArray,
    mu_s: float,
) -> LimitMargins:
    """Assemble the full `core.results.LimitMargins` from a trajectory's
    ground-reaction and chain-tension time series. Shared by both tiers:
    neither `simplified/model.py` nor the planar tier needs its own copy
    of this formula."""
    return LimitMargins(
        traction_rear=traction_margin(R_2a_x, R_2a_z, mu_s),
        traction_front=traction_margin(R_2b_x, R_2b_z, mu_s),
        wheelie=wheel_lift_margin(R_2b_z),
        stoppie=wheel_lift_margin(R_2a_z),
        freewheel=freewheel_margin(T),
    )
