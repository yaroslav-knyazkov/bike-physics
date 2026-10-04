"""planar/forces/contact.py -- ContactForce: the thin `ForceElement` bridge
between a `ContactModel` (architecture.md S7.6, `planar.contact.*`) and the
engine's generic `h` assembly (S7.3's `ContactForce` row: "Thin bridge:
asks the active `ContactModel` for each wheel's contact wrench and applies
it. All the physics is in `contact/` (S7.6)").

This is the one force element in the engine that carries genuine mutable
state across calls: the `x*` warm-start cache (architecture.md S12.1,
"the foot-of-normal Newton is warm-started (1-2 iterations, not 10)")
must persist between `solve_ivp` RHS evaluations, not be rebuilt from
scratch each time. The cache is a plain `dict[str, float]` (body name ->
last `x*`), created once per `ContactForce` instance and threaded into
`ContactModel.wrenches()` by reference every call -- mutating the dict
object a frozen dataclass field points to is not a frozen-dataclass
violation (only *reassigning* the field itself would be), matching this
project's existing pattern for lazily-built cached state
(`core.road.PiecewiseGrade`'s `_spline`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.core.road import Road
from bikephysics.planar.body import apply_wrench
from bikephysics.planar.contact.regularized import ContactModel
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ContactForce:
    """`ForceElement` wrapping one `ContactModel` (architecture.md S7.3's
    `ContactForce` row). One instance per wheel, matching
    `RegularizedContact`'s own per-wheel granularity (`planar.assembly`,
    T7, registers one `ContactForce` per wheel body)."""

    name: str
    model: ContactModel
    road: Road
    spec: Any = None
    _cache: dict[str, float] = field(default_factory=dict, init=False, repr=False, compare=False)

    def accumulate(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        h: FloatArray,
        xp: MathBackend,
    ) -> None:
        for wrench in self.model.wrenches(t, q, u, layout, self.road, self.spec, self._cache):
            apply_wrench(
                h, layout, wrench.body, q, wrench.force_world, wrench.point_world, wrench.moment
            )

    def diagnostics(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> dict[str, Any]:
        state = self.model.state()
        return {
            "x_star": state.x_star,
            "g_N": state.g_N,
            "g_N_dot": state.g_N_dot,
            "v_slip": state.v_slip,
            "sigma": state.sigma,
        }
