"""planar/forces/pedal.py -- PedalForce: the rider's pedal-force
action/reaction pair (architecture.md S7.3's `PedalForce` row; S7.5.3's
(7.7)-(7.9); `latex/2d_systems/frame.tex`).

**Both halves of the pair are applied**, at the same two world material
points `D`, `F` (architecture.md S7.3): `+P_{3b/1,X}` on the crankset body
`3b` and `-P_{3b/1,X}` on the frame+rider body `1`, `X in {D, F}`. The
planar tier needs the frame-side reaction explicitly (unlike the
Simplified tier, which has no second body to apply it to) because it
drives pedal-induced pitch -- the same reasoning architecture.md S7.3
gives for `BrakeTorque`'s frame reaction (see `forces/brake.py`).

Geometry per `frame.tex`, with the correction architecture.md S11 item 1
flags (`x_hat_2b` -> `x_hat_3b`: the crank's *own* axis, not the front
wheel's):

    CD = +r_1,pedal * x_hat_3b,    CF = -r_1,pedal * x_hat_3b             (7.7)

**Sign correction, recorded for architect sign-off (same spirit as
`contact/regularized.py`'s two corrections for T6) -- and tied to a
*second*, more foundational correction in `planar/kinematics.py`.**
Architecture.md S7.5.3 states

    tau_pedal = -r_1,pedal * (P_D + P_F)                                  (7.9)

negative "because positive pedal force drives forward (= negative spin)
under S7.1's convention" (S7.1: "forward rolling gives negative spin,
omega_2a = -v_x/r_2a"). `planar/kinematics.py`'s own module docstring
works the *zero-slip* condition through this engine's already-built
rotation convention (`planar.body`'s `y_hat x r = (r_z, -r_x)`) and finds
the *opposite*: forward rolling (`v_axle_x > 0`) requires `omega = +v/r`,
not `-v/r` -- confirmed independently by T6's own `contact.geometry
.contact_geometry`'s `v_slip = v_axle_x0 - radius*omega` formula, which
needs the same `+v/r` to give zero slip. Since `omega_3b` is tied to
`omega_2a` by the fixed gear ratio (same sign, S7.5.2 eq. 7.3), *positive*
pedal force must drive *positive* crank spin once the corrected rolling
sign is used -- i.e. `tau_pedal` must be **positive**, the opposite of
(7.9)'s literal sign.

Reworking the tangential-force geometry for this corrected target
(`tau_pedal = +r_1,pedal*(P_D+P_F)`, via the engine's own
`apply_wrench` moment convention `torque = r_z*F_x - r_x*F_z`, checked
symbolically at general `theta_3b` and numerically at `theta_3b = 0`):
local (zero-radial, i.e. "tangential-only", `^{x_3b}P = 0`) forces

    F_D (local) = (0, -P_D),    F_F (local) = (0, +P_F)

at `D`, `F` respectively give exactly `tau_pedal = +r_1,pedal*(P_D+P_F)`
about `C` -- consistent with the corrected rolling-direction sign. This
file implements that corrected pair; architect/user sign-off on *both*
corrections (this one and `kinematics.py`'s) is requested in this
ticket's final report, exactly as T6 requested sign-off on (7.12)'s two
corrections.

`^{x_3b}P = 0` (the "tangential-only" default, S7.5.3): the local-frame
force at each pedal point has zero radial (local-x, along the crank arm)
component -- `F_D`/`F_F` above are purely in the crank's local z-direction,
which *is* "tangential" since `D`/`F` sit on the local x-axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.core.pedals import PedalInput
from bikephysics.planar.body import apply_wrench, rotation_matrix
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class PedalForce:
    """The pedal-force action/reaction pair (architecture.md S7.3,
    S7.5.3). `pedal_input.forces(t, theta_3b, omega_3b)` supplies `(P_D,
    P_F)`, the two per-pedal tangential magnitudes [N] (`core.pedals
    .PedalInput`, already shared with the Simplified tier).

    `crank_body`/`frame_body` default to this project's fixed names
    (`"3b"`, `"1"`); kept as fields, not hardcoded, per `planar/assembly
    .py`'s "only place topology is hard-coded" charter."""

    name: str
    pedal_input: PedalInput
    r_1_pedal: float
    crank_body: str = "3b"
    frame_body: str = "1"

    def __post_init__(self) -> None:
        if self.r_1_pedal <= 0.0:
            raise ValueError(f"r_1_pedal must be > 0, got {self.r_1_pedal}")

    def _points_and_forces(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> tuple[FloatArray, FloatArray, FloatArray, FloatArray, float, float]:
        """`(world_D, world_F, force_D_world, force_F_world, P_D, P_F)` --
        shared by `accumulate` (both halves of the pair) and `diagnostics`
        (the mechanical power into the system)."""
        theta_3b = layout.theta(q, self.crank_body)
        omega_3b = layout.omega(u, self.crank_body)
        p_d, p_f = self.pedal_input.forces(t, theta_3b, omega_3b)

        cog_3b = layout.position(q, self.crank_body)
        r = rotation_matrix(theta_3b)
        local_d = np.array([self.r_1_pedal, 0.0])
        local_f = np.array([-self.r_1_pedal, 0.0])
        world_d = cog_3b + r @ local_d
        world_f = cog_3b + r @ local_f

        # Local (tangential-only) force: (0, -P_D) at D, (0, +P_F) at F --
        # see module docstring's sign-correction note.
        force_d_world = r @ np.array([0.0, -p_d])
        force_f_world = r @ np.array([0.0, p_f])
        return world_d, world_f, force_d_world, force_f_world, p_d, p_f

    def accumulate(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        h: FloatArray,
        xp: MathBackend,
    ) -> None:
        world_d, world_f, force_d, force_f, _p_d, _p_f = self._points_and_forces(t, q, u, layout)
        # +P on the crankset (3b), at D and F.
        apply_wrench(h, layout, self.crank_body, q, force_d, world_d)
        apply_wrench(h, layout, self.crank_body, q, force_f, world_f)
        # -P reaction on the frame+rider (1), at the same world points.
        apply_wrench(h, layout, self.frame_body, q, -force_d, world_d)
        apply_wrench(h, layout, self.frame_body, q, -force_f, world_f)

    def diagnostics(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> dict[str, Any]:
        world_d, world_f, force_d, force_f, p_d, p_f = self._points_and_forces(t, q, u, layout)
        # Mechanical power actually delivered to the system by this
        # action/reaction pair: F . (v_point_on_3b - v_point_on_1), the
        # relative-point-velocity work rate (not F . v_point_on_3b alone --
        # the reaction on body 1 does negative work at the same point at
        # the same instant, and only the *relative* motion between the two
        # material points is where the rider's muscles actually do work).
        v_d_3b = _point_velocity(q, u, layout, self.crank_body, world_d)
        v_f_3b = _point_velocity(q, u, layout, self.crank_body, world_f)
        v_d_1 = _point_velocity(q, u, layout, self.frame_body, world_d)
        v_f_1 = _point_velocity(q, u, layout, self.frame_body, world_f)
        power = float(np.dot(force_d, v_d_3b - v_d_1) + np.dot(force_f, v_f_3b - v_f_1))
        return {"P_D": p_d, "P_F": p_f, "power": power}


def _point_velocity(
    q: FloatArray, u: FloatArray, layout: SystemLayout, body: str, world_point: FloatArray
) -> FloatArray:
    """World velocity of the material point of `body` that is
    *instantaneously* located at `world_point` -- `v_cog + omega * (r_z,
    -r_x)` with `r = world_point - cog` (architecture.md S7.1's rotation
    convention, matching `planar.body.RigidBody.point_velocity`)."""
    cog = layout.position(q, body)
    omega = layout.omega(u, body)
    v_cog = layout.velocity(u, body)
    r = world_point - cog
    return v_cog + omega * np.array([r[1], -r[0]])
