"""planar/forces/brake.py -- BrakeTorque: wheel brake torque + the frame
caliper reaction (architecture.md S7.3's `BrakeTorque` row).

Architecture.md S7.3 writes `+sigma * r_k,bk * B_{k/1} y_hat` on the
wheel and the opposite on the frame. **Sign correction, recorded for
architect sign-off (the fourth instance of the same root cause as
`forces/pedal.py`, `planar/constraints/gear.py`, and `planar/kinematics
.py` -- see those modules' docstrings for the full derivation).** A
brake torque must *resist* the wheel's own spin (`sgn(moment) =
-sgn(omega_wheel)`, the definition of a dissipative brake); for forward
motion (`sigma = +1`), `planar/kinematics.py`'s corrected rolling-without-
slip sign gives `omega_wheel > 0`, so the resisting moment must be
*negative*. S7.3's literal `+sigma*r_bk*B` is positive for `sigma=+1`,
`B>0` -- the wrong sign under the corrected rolling convention (and
empirically confirmed: with the literal sign, braking *accelerates* the
bike and *increases* the braked wheel's own normal load instead of
transferring it off, the opposite of a stoppie). This file implements
`-sigma*r_bk*B` on the wheel and `+sigma*r_bk*B` on the frame.

Both halves are pure couples (no net force, hence no moment-arm/
transport term needed): a disc/rim caliper clamps the rotor without
displacing it, so `apply_wrench`'s `world_force` is `(0, 0)` for both and
the whole contribution is the `moment=` argument.

`sigma = sign_smooth(v_{k,A}.x_hat_0, v_eps)` (architecture.md S7.6.1,
shared with the rolling-resistance/contact-force direction convention) is
recomputed fresh from `contact.geometry.contact_geometry` every call (no
lag -- unlike `BearingFriction`'s dependence on a *solved* reaction force,
`sigma` is a pure function of the current `(q, u)` and the road, so there
is nothing to lag). `c_rr` is passed as `0.0`: `contact_geometry`'s other
outputs (`g_N`, `contact_offset`, ...) are not used here, only `.sigma`,
and `c_rr` does not enter that field's formula.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from bikephysics.core.brakes import BrakeInput, KinematicState
from bikephysics.core.mathx import MathBackend
from bikephysics.core.road import Road
from bikephysics.planar.body import apply_wrench
from bikephysics.planar.contact.geometry import contact_geometry
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class BrakeTorque:
    """`wheel_body in {"2a", "2b"}` selects which of `brake_input`'s two
    returned magnitudes (`B_2a`, `B_2b`) this instance applies -- one
    `BrakeTorque` per wheel, matching `RegularizedContact`'s own per-wheel
    granularity (architecture.md S7.6.2)."""

    name: str
    wheel_body: str
    which: str  # "rear" -> B_2a, "front" -> B_2b
    r_bk: float
    radius: float
    road: Road
    brake_input: BrakeInput
    frame_body: str = "1"
    distance_body: str = "1"
    v_eps: float = 0.01
    axle_point: tuple[float, float] = (0.0, 0.0)
    _cache: dict[str, float] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.which not in ("rear", "front"):
            raise ValueError(f"which must be 'rear' or 'front', got {self.which!r}")
        if self.r_bk <= 0.0:
            raise ValueError(f"r_bk must be > 0, got {self.r_bk}")

    def _moment(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> tuple[float, float]:
        """`(moment_on_wheel, sigma)`. `moment_on_wheel` is the signed
        `y_hat`-torque applied to `wheel_body`; the frame gets its negative."""
        geom = contact_geometry(
            q,
            u,
            layout,
            body=self.wheel_body,
            radius=self.radius,
            c_rr=0.0,
            road=self.road,
            v_eps=self.v_eps,
            axle_point=self.axle_point,
            x_guess=self._cache.get(self.wheel_body),
            t=t,
        )
        self._cache[self.wheel_body] = geom.x_star

        x_ref = layout.x(q, self.distance_body)
        v_ref = layout.vx(u, self.distance_body)
        b_2a, b_2b = self.brake_input.forces(t, KinematicState(x=x_ref, v=v_ref))
        b = b_2a if self.which == "rear" else b_2b
        # Negative: resists the wheel's own (corrected-sign-convention)
        # positive spin under forward motion -- see module docstring.
        moment = -geom.sigma * self.r_bk * b
        return moment, geom.sigma

    def accumulate(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        h: FloatArray,
        xp: MathBackend,
    ) -> None:
        moment, _sigma = self._moment(t, q, u, layout)
        zero_force = np.zeros(2)
        cog_wheel = layout.position(q, self.wheel_body)
        cog_frame = layout.position(q, self.frame_body)
        apply_wrench(h, layout, self.wheel_body, q, zero_force, cog_wheel, moment=moment)
        apply_wrench(h, layout, self.frame_body, q, zero_force, cog_frame, moment=-moment)

    def diagnostics(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> dict[str, Any]:
        moment, sigma = self._moment(t, q, u, layout)
        omega_wheel = layout.omega(u, self.wheel_body)
        omega_frame = layout.omega(u, self.frame_body)
        # Positive-magnitude dissipated-power convention (matching
        # `simplified/simulate.py`'s power_brake/power_aero/power_rr, all
        # positive "energy removed" channels, not signed work-on-system):
        # the torque pair does work `moment*omega_wheel + (-moment)*
        # omega_frame = moment*(omega_wheel - omega_frame)` on the system,
        # which is <= 0 for a physically dissipative brake, so the
        # positive-sink magnitude is its negative.
        power = -(moment * (omega_wheel - omega_frame))
        return {"moment": moment, "sigma": sigma, "power": power}
