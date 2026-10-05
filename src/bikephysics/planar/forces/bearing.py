"""planar/forces/bearing.py -- BearingFriction: axle/BB bearing friction
torque with the one-step multiplier lag (architecture.md S7.3's
`BearingFriction` row; `latex/2d_models/models.tex` S bearing-friction-model).

    Q_{k/l} = -(1/2) * mu_b * d_{k/l} * |R_{k/l}| * sgn_smooth(omega_{k/l}) * y_hat

applied as a torque pair between the rotating body `k` (a wheel or the
crankset) and the body it is journaled in, `l` (the frame, `"1"`): `+Q` on
`k`, `-Q` on `l` -- the same "pure couple, zero net force" shape as
`forces/brake.py`'s `BrakeTorque` (a plain bearing seal/race does not
displace either body).

**Depends on the constraint reaction `R_{k/l}`, a solver output** --
architecture.md S7.3's own words: "a one-step lag (use the previous
accepted step's multiplier) is the default". This class does not, and
structurally cannot, read that reaction from inside its own `accumulate`
call (the reaction is produced *by* the same KKT solve `accumulate`'s own
`h` is an input to -- reading it here would be circular). Instead it reads
a plain `reaction_cache: MutableMapping[str, float]` (mirroring
`forces/contact.py::ContactForce`'s own warm-start cache field, which is
the established pattern in this engine for state that must persist and be
written *from outside* a `ForceElement`'s own `accumulate`), keyed by the
bearing's `RevoluteJoint.name`. `update_reaction` is the one write path;
a caller (a custom step-driver, e.g. `planar/drivetrain.py`'s mode-switch
loop) invokes it once per accepted integration step with that step's
solved multiplier magnitude -- the lag the architecture calls for.

`mu_b` defaults to `0.0` here as well as on `core.params.DrivetrainParams`
(architecture.md S7.3: "off by default in milestone 1, because the
one-step lag is only defensible for a term this small and it must be
justified before being switched on"); with `mu_b = 0.0` this element
contributes exactly zero regardless of what is or is not in
`reaction_cache`, so the default `planar/assembly.py` wiring needs no live
lag-update driver at all to be exact.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend, numpy_backend
from bikephysics.planar.body import apply_wrench
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class BearingFriction:
    """One bearing (rear axle, front axle, or bottom bracket) between the
    rotating body `rotating_body` and the journaling body `frame_body`
    (default `"1"`). `joint_name` is the name of the `RevoluteJoint` this
    bearing sits at -- the key `update_reaction`/`accumulate` use into
    `reaction_cache`."""

    name: str
    joint_name: str
    rotating_body: str
    mu_b: float
    d_bearing: float
    frame_body: str = "1"
    omega_eps: float = 0.01  # rad/s regularization scale for sgn_smooth(omega_rel)
    reaction_cache: MutableMapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.mu_b < 0.0:
            raise ValueError(f"mu_b must be >= 0, got {self.mu_b}")
        if self.d_bearing <= 0.0:
            raise ValueError(f"d_bearing must be > 0, got {self.d_bearing}")

    def update_reaction(self, reaction_magnitude: float) -> None:
        """Write this step's accepted `|R_{k/l}|` into the shared cache --
        the one-step-lag write path (see module docstring). Mutates the
        `reaction_cache` mapping in place; not a frozen-dataclass
        violation (only reassigning the field itself would be), matching
        `forces/contact.py::ContactForce`'s own established pattern."""
        self.reaction_cache[self.joint_name] = reaction_magnitude

    def _torque(self, u: FloatArray, layout: SystemLayout, xp: MathBackend) -> float:
        r_mag = self.reaction_cache.get(self.joint_name, 0.0)
        omega_rel = layout.phi_rate(u, self.rotating_body, reference=self.frame_body)
        sgn = float(xp.sign_smooth(omega_rel, self.omega_eps))
        return -0.5 * self.mu_b * self.d_bearing * abs(r_mag) * sgn

    def accumulate(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        h: FloatArray,
        xp: MathBackend,
    ) -> None:
        q_torque = self._torque(u, layout, xp)
        zero_force = np.zeros(2)
        cog_rot = layout.position(q, self.rotating_body)
        cog_frame = layout.position(q, self.frame_body)
        apply_wrench(h, layout, self.rotating_body, q, zero_force, cog_rot, moment=q_torque)
        apply_wrench(h, layout, self.frame_body, q, zero_force, cog_frame, moment=-q_torque)

    def diagnostics(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> dict[str, Any]:
        omega_rel = layout.phi_rate(u, self.rotating_body, reference=self.frame_body)
        q_torque = self._torque(u, layout, xp=numpy_backend)
        return {
            "Q": q_torque,
            "omega_rel": omega_rel,
            "R_cached": self.reaction_cache.get(self.joint_name, 0.0),
        }
