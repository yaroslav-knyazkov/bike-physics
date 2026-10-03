"""core/results.py -- the shared results schema (architecture.md S5.5).

`Trajectory` is the load-bearing seam of the whole project (architecture.md
S4): both the Simplified and Planar tiers emit the same field names for the
same physical quantities, which is what lets `optim.forward.ForwardModel`
stay tier-agnostic. Field names are not to be improvised or renamed outside
an explicit architecture change.

Ground reactions (architect-confirmed, see `architecture-notes.md`): S5.5's
code block spells out `Trajectory` with four flat fields directly on it --
`R_2a_x, R_2a_z, R_2b_x, R_2b_z` -- with an explicit comment on them
("ground reactions in frame 0, SHARED sign convention"). S3's
directory-tree comment naming a `Reactions` type alongside `Trajectory` is
an imprecise one-line gloss in a file listing, not a second schema
definition; there is no other mention of a `Reactions` type anywhere in the
document. The four reaction fields are therefore flat fields on
`Trajectory`, exactly as S5.5 writes them -- no nested `Reactions`
dataclass.

`Trajectory.spec` is typed `Any` for now: `core.params.BikeSpec` does not
exist until T2, and this ticket must not invent that model's shape. Narrow
the annotation to `BikeSpec` once T2 lands.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


def _check_same_length(reference_len: int, **named_arrays: FloatArray) -> None:
    """Raise `ValueError` naming every array whose length disagrees with
    `reference_len`."""
    bad = {name: len(arr) for name, arr in named_arrays.items() if len(arr) != reference_len}
    if bad:
        raise ValueError(f"array length mismatch: expected {reference_len}, got {bad}")


@dataclass(frozen=True)
class LimitMargins:
    """Continuous, signed dynamic-limit margins (architecture.md S5.5): each
    field is `<= 0` where the corresponding limit is satisfied and `> 0`
    where it is violated. Deliberately not booleans, so the optimizer can
    use them directly as smooth inequality constraints.

    `traction_rear`/`traction_front` follow the example given in S5.5:
    `traction_rear = |R_x| - mu_s * R_z`. The exact `wheelie`/`stoppie`/
    `freewheel` formulas are defined in `core.limits` (T2); this dataclass
    only fixes their names and shape.
    """

    traction_rear: FloatArray
    traction_front: FloatArray
    wheelie: FloatArray
    stoppie: FloatArray
    freewheel: FloatArray

    def validate(self) -> None:
        """Check that all five margin arrays share the same length."""
        n = len(self.traction_rear)
        _check_same_length(
            n,
            traction_front=self.traction_front,
            wheelie=self.wheelie,
            stoppie=self.stoppie,
            freewheel=self.freewheel,
        )


@dataclass(frozen=True)
class ModeLog:
    """Planar-tier only (architecture.md S5.5): a sparse log of discrete
    contact/stick-slip/freewheel mode-switch events along a run, each with
    the energy it dissipated (e.g. the perfectly-plastic chain-engagement
    impulse of S7.5). `Trajectory.modes` is `None` for every Simplified-tier
    run. Unlike the other result arrays this is event-indexed, not
    sample-indexed: its length is the number of mode switches, not
    `len(Trajectory.t)`.
    """

    t: FloatArray
    body: tuple[str, ...]
    mode: tuple[str, ...]
    energy_loss: FloatArray

    def validate(self) -> None:
        """Check that `t`, `body`, `mode`, and `energy_loss` all have the
        same number of events."""
        n = len(self.t)
        if len(self.body) != n or len(self.mode) != n or len(self.energy_loss) != n:
            raise ValueError(
                "ModeLog arrays/tuples must have matching length: "
                f"t={n}, body={len(self.body)}, mode={len(self.mode)}, "
                f"energy_loss={len(self.energy_loss)}"
            )


@dataclass(frozen=True)
class Trajectory:
    """The shared results schema (architecture.md S5.5). Both tiers emit
    exactly these field names for the same physical quantities; see the
    module docstring for the `spec: Any` placeholder.

    `z`, `pitch`, and `pitch_rate` are 0/constant for the Simplified tier
    (it has no pitch or vertical DOF) but are always present so downstream
    code (plotting, `optim`, `validation`) does not need a tier branch.

    `R_2a_x, R_2a_z, R_2b_x, R_2b_z`: ground reactions at the rear (2a) and
    front (2b) wheel contacts, in frame 0 (road-tangent frame), SI units
    [N]. Shared sign convention (architecture.md S5.5): `R_*_z >= 0` means
    the wheel is loaded; a negative `R_*_z` is not physical and signals a
    solver/contact-model bug rather than a result.
    """

    t: FloatArray
    x: FloatArray
    z: FloatArray
    v: FloatArray
    a: FloatArray
    pitch: FloatArray
    pitch_rate: FloatArray
    omega_2a: FloatArray
    omega_2b: FloatArray
    omega_3b: FloatArray
    T: FloatArray
    R_2a_x: FloatArray
    R_2a_z: FloatArray
    R_2b_x: FloatArray
    R_2b_z: FloatArray
    power_pedal: FloatArray
    power_ground: FloatArray
    power_aero: FloatArray
    power_rr: FloatArray
    power_brake: FloatArray
    power_gravity: FloatArray
    margins: LimitMargins
    modes: ModeLog | None
    spec: Any
    meta: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Check that every time-series field (and the nested `margins`)
        has the same length as `t`. Does not touch `modes` (event-indexed,
        see `ModeLog`), `spec`, or `meta`."""
        n = len(self.t)
        _check_same_length(
            n,
            x=self.x,
            z=self.z,
            v=self.v,
            a=self.a,
            pitch=self.pitch,
            pitch_rate=self.pitch_rate,
            omega_2a=self.omega_2a,
            omega_2b=self.omega_2b,
            omega_3b=self.omega_3b,
            T=self.T,
            R_2a_x=self.R_2a_x,
            R_2a_z=self.R_2a_z,
            R_2b_x=self.R_2b_x,
            R_2b_z=self.R_2b_z,
            power_pedal=self.power_pedal,
            power_ground=self.power_ground,
            power_aero=self.power_aero,
            power_rr=self.power_rr,
            power_brake=self.power_brake,
            power_gravity=self.power_gravity,
        )
        self.margins.validate()
        if len(self.margins.traction_rear) != n:
            raise ValueError(
                f"margins length mismatch: expected {n}, got {len(self.margins.traction_rear)}"
            )
        if self.modes is not None:
            self.modes.validate()


@dataclass(frozen=True)
class RunSummary:
    """The structured content of `summary.json` (architecture.md S5.6,
    S12.2): provenance plus scalar metrics for one run directory. Keys are
    written in a fixed order by `core.runio.write_summary` so the file is
    diffable and usable as a golden regression fixture.
    """

    run_id: str
    created_utc: str
    package_version: str
    git_sha: str
    git_dirty: bool
    numpy_version: str
    scipy_version: str | None
    casadi_version: str | None
    wall_time_s: float
    command: str
    seed: int | None
    solver: dict[str, Any] = field(default_factory=dict)
    tolerances: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
