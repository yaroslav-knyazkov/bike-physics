"""planar/forces/weight.py -- Weight force element
(architecture.md S7.3; latex/2d_models/forces.tex S Weight).

`W_k = -m_k * g * z_hat`, applied at the body's own CoG (point `a`), so it
produces no moment about the CoG and `apply_wrench`'s transport term is
identically zero -- spelled out through `apply_wrench` anyway, for
consistency with every other force element.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.planar.body import apply_wrench
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Weight:
    """`W_k = -m_k * g * z_hat` on `body` (`latex/2d_models/forces.tex`).
    `mass` is supplied explicitly rather than looked up from a
    `RigidBody` -- `ForceElement.accumulate`'s signature has no body
    registry to query, by design (architecture.md S7.3's protocol takes
    only `t, q, u, layout, h, xp`). `MultibodySystem.add_gravity` is the
    convenience that derives `mass` from the registered `RigidBody` so
    callers never duplicate it by hand."""

    name: str
    body: str
    mass: float
    g: float

    def accumulate(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        h: FloatArray,
        xp: MathBackend,
    ) -> None:
        cog = layout.position(q, self.body)
        force = np.array([0.0, -self.mass * self.g])
        apply_wrench(h, layout, self.body, q, force, cog, moment=0.0)

    def diagnostics(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> dict[str, Any]:
        return {}
