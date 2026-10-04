"""planar/forces/aerodynamic.py -- Aerodynamic force element
(architecture.md S7.3; latex/2d_models/forces.tex S Aerodynamic;
latex/2d_models/models.tex S aerodynamics-model).

`A_{1,f} = -A_1x*x_hat_1 - A_1z*z_hat_1`, `A_{1,m} = -A_1m*y_hat`
(forces.tex), applied to body `"1"` (frame+rider) at a configurable
reference point (default: the CoG). `core.aero.AeroModel` supplies the
three coefficients from the body-1-frame relative velocity; models.tex's
`beta_rel = atan2(z1_v_rel, x1_v_rel)` is defined in body-1-frame
components, so this element rotates the world CoG velocity (and wind)
into body 1's own axes before calling `AeroModel.evaluate` -- the one
place that rotation has to happen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from bikephysics.core.aero import AeroModel, AeroState
from bikephysics.core.mathx import MathBackend
from bikephysics.planar.body import PointSpec, apply_wrench, rotation_matrix
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]

_BODY_1 = "1"


@dataclass(frozen=True)
class Aerodynamic:
    """Quasi-steady aerodynamic drag/side-force/pitching-moment on body
    `"1"` (architecture.md S7.3's table: "at the aero reference point of
    body 1"). `aero_model` supplies `(A_1_x, A_1_z, A_1_m)` from the
    relative airspeed; `rho`/`mu` come from `core.atmosphere`; `point`
    (body-1-frame, `None` = CoG) is where the force `A_{1,f}` is taken to
    act -- the pitching moment `A_{1,m}` is a separate free moment, not a
    transport effect of this point.
    """

    name: str
    aero_model: AeroModel
    rho: float
    mu: float
    point: PointSpec = None
    wind_x: float = 0.0
    wind_z: float = 0.0
    xi_1: float = 0.0

    def accumulate(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        h: FloatArray,
        xp: MathBackend,
    ) -> None:
        state = self._evaluate(q, u, layout)
        theta_1 = layout.theta(q, _BODY_1)
        r_1 = rotation_matrix(theta_1)
        force_body1 = np.array([-state.A_1_x, -state.A_1_z])
        force_world = r_1 @ force_body1
        point_world = self._world_point(q, layout)
        apply_wrench(h, layout, _BODY_1, q, force_world, point_world, moment=-state.A_1_m)

    def _world_point(self, q: FloatArray, layout: SystemLayout) -> FloatArray:
        theta_1 = layout.theta(q, _BODY_1)
        cog = layout.position(q, _BODY_1)
        local = np.zeros(2) if self.point is None else np.asarray(self.point, dtype=float)
        return cog + rotation_matrix(theta_1) @ local

    def _evaluate(self, q: FloatArray, u: FloatArray, layout: SystemLayout) -> AeroState:
        theta_1 = layout.theta(q, _BODY_1)
        omega_1 = layout.omega(u, _BODY_1)
        r_1 = rotation_matrix(theta_1)
        v_world = layout.velocity(u, _BODY_1)
        v_body1 = r_1.T @ v_world
        wind_body1 = r_1.T @ np.array([self.wind_x, self.wind_z])
        return self.aero_model.evaluate(
            rho=self.rho,
            mu=self.mu,
            v1_x=float(v_body1[0]),
            v1_z=float(v_body1[1]),
            wind_x=float(wind_body1[0]),
            wind_z=float(wind_body1[1]),
            xi_1=self.xi_1,
            omega_1=omega_1,
        )

    def diagnostics(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> dict[str, Any]:
        state = self._evaluate(q, u, layout)
        return {
            "A_1_x": state.A_1_x,
            "A_1_z": state.A_1_z,
            "A_1_m": state.A_1_m,
            "beta_rel": state.beta_rel,
            "Re": state.Re,
            "q": state.q,
        }
