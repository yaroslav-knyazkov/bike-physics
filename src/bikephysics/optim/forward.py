"""optim/forward.py -- the `ForwardModel` seam (architecture.md S8.1).

`ForwardModel` is the one abstraction every other module in `optim`
(`parametrization`, `objectives`, `constraints`, `solvers`, `report`) is
written against: none of them import `bikephysics.simplified` or
`bikephysics.planar` directly, only this protocol. Two adapters implement
it: `SimplifiedForwardModel` (this ticket, T8) wraps `simplified.simulate`/
`simplified.model`; `PlanarForwardModel` is a stub (T10) that raises
`NotImplementedError` naming that ticket -- the protocol itself is already
general enough for T10 to fill in without a signature change.

`ControlInputs`/`Horizon` are the two small value types `rollout()` takes.
`ControlInputs` bundles the pedal/brake inputs a `ControlParametrization`
builds plus an optional `spec_overrides` mapping (dotted `BikeSpec` paths
-> values), the "+ spec override" §8.2 mentions for e.g. `GearSelection`
swapping `drivetrain.r_2a_cg`/`r_3b_cr`. `Horizon` is the TOML `[horizon]`/
`[initial]` tables of §8.5, merged into one dataclass since both are needed
together by every `rollout()` call.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, Protocol

import numpy as np

from bikephysics.core.aero import AeroModel
from bikephysics.core.brakes import BrakeInput, ConstantBrake
from bikephysics.core.params import BikeSpec
from bikephysics.core.pedals import PedalInput
from bikephysics.core.results import Trajectory
from bikephysics.core.road import Flat, Road
from bikephysics.simplified.simulate import simulate  # noqa: TID251 -- optim is exempt, S4


@dataclass(frozen=True)
class ControlInputs:
    """The decision a `ControlParametrization.build(x)` produces: a pedal
    input (mandatory), an optional brake input (defaults to no braking),
    and an optional `spec_overrides` mapping of dotted `core.params.BikeSpec`
    paths to replacement values -- the mechanism `GearSelection` uses to pick
    a discrete gear ratio without threading a second decision channel
    through every other parametrization."""

    pedal: PedalInput
    brake: BrakeInput = field(default_factory=ConstantBrake)
    spec_overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Horizon:
    """The merged `[horizon]`/`[initial]` TOML tables of architecture.md
    §8.5: how long/far to roll out, and the initial state to roll out from.
    `t_max` is a cap, not a target -- the rollout still stops early on a
    coast-to-stop or (when `x_target` is set) on reaching it, exactly as
    `simplified.simulate.simulate`'s own terminal events do."""

    t_max: float
    x_target: float | None = None
    x0: float = 0.0
    v0: float = 0.0
    theta_3b0: float = 0.0
    n_output: int = 2000


class ForwardModel(Protocol):
    """architecture.md §8.1. `spec` is the nominal `BikeSpec` this model
    rolls out against before any `ControlInputs.spec_overrides` is applied;
    `rollout` and `metrics` are the only two operations every downstream
    `optim` module needs, and `differentiable` lets a solver pick a
    finite-difference vs. exact-gradient code path (`True` only for the
    CasADi-backed variants of milestone 2+, T9)."""

    spec: BikeSpec

    def rollout(self, controls: ControlInputs, horizon: Horizon) -> Trajectory: ...

    def metrics(self, traj: Trajectory) -> dict[str, float]: ...

    @property
    def differentiable(self) -> bool: ...


def _apply_spec_overrides(spec: BikeSpec, overrides: Mapping[str, Any]) -> BikeSpec:
    """Apply a dotted-path override mapping (already-typed values, not
    `--set`'s raw strings) onto a deep-copied dump of `spec` and
    re-validate. Mirrors `cli.common._set_dotted`'s traversal, kept as a
    private duplicate here rather than imported: `cli.common` operates on
    raw CLI strings via `coerce_scalar`, which would mangle an
    already-typed override value (e.g. an `int` tooth count)."""
    if not overrides:
        return spec
    data: dict[str, Any] = spec.model_dump()
    for dotted_path, value in overrides.items():
        keys = dotted_path.split(".")
        node = data
        for key in keys[:-1]:
            node = node[key]
        node[keys[-1]] = value
    return BikeSpec.model_validate(data)


@dataclass
class SimplifiedForwardModel:
    """`ForwardModel` adapter onto `simplified.simulate.simulate` /
    `simplified.model` (architecture.md §8.1). `road`/`aero_model` are
    bound once per problem (the TOML `road = "..."` field), not re-specified
    per rollout -- only the control and the horizon vary across
    optimization iterations."""

    spec: BikeSpec
    road: Road = field(default_factory=Flat)
    aero_model: AeroModel | None = None
    method: Literal["RK45", "Radau"] = "RK45"
    rtol: float = 1e-8
    atol: float = 1e-10
    n_output: int = 2000

    @property
    def differentiable(self) -> bool:
        return False

    def rollout(self, controls: ControlInputs, horizon: Horizon) -> Trajectory:
        """Build the effective `BikeSpec` (applying
        `controls.spec_overrides`, if any) and call `simulate()`. The
        returned `Trajectory`'s `meta` is augmented with `"P_D"`/`"P_F"`
        (the per-instant pedal tangential forces), reconstructed from the
        returned `(t, x, omega_3b)` via the same crank-angle algebra
        `simplified.simulate`'s own module docstring documents
        (`theta_3b(t) = theta_3b0 - (x(t)-x0)*crank_ratio`) -- `Trajectory`
        itself carries no `P_D`/`P_F` field (architecture.md S5.5 fixes its
        schema), but `optim.constraints.PedalForceBound` needs the exact
        per-pedal force, not the combined wheel-frame `power_pedal` field,
        so this is the one place in `optim` allowed to reach into `meta`
        for an extension beyond the shared schema."""
        spec = _apply_spec_overrides(self.spec, controls.spec_overrides)
        traj = simulate(
            spec,
            controls.pedal,
            road=self.road,
            brake_input=controls.brake,
            aero_model=self.aero_model,
            x0=horizon.x0,
            v0=horizon.v0,
            theta_3b0=horizon.theta_3b0,
            t_span=(0.0, horizon.t_max),
            x_target=horizon.x_target,
            n_output=horizon.n_output or self.n_output,
            method=self.method,
            rtol=self.rtol,
            atol=self.atol,
        )
        crank_ratio = spec.drivetrain.r_2a_cg / (spec.drivetrain.r_3b_cr * spec.rear.r_2a)
        theta_3b = horizon.theta_3b0 - (traj.x - horizon.x0) * crank_ratio
        p_d = np.empty_like(traj.t)
        p_f = np.empty_like(traj.t)
        for i in range(len(traj.t)):
            p_d[i], p_f[i] = controls.pedal.forces(
                float(traj.t[i]), float(theta_3b[i]), float(traj.omega_3b[i])
            )
        return replace(traj, meta={**traj.meta, "P_D": p_d, "P_F": p_f})

    def metrics(self, traj: Trajectory) -> dict[str, float]:
        """Scalar summary of a rollout: terminal time/position/speed and
        the mechanical (pedal) energy expended -- the handful of numbers
        every `optim.objectives`/`optim.report` consumer wants without
        re-deriving them from the full time series."""
        mechanical_energy = float(np.trapezoid(traj.power_pedal, traj.t))
        return {
            "t_final": float(traj.t[-1]),
            "x_final": float(traj.x[-1]),
            "v_final": float(traj.v[-1]),
            "mechanical_energy": mechanical_energy,
            "mean_speed": float(traj.x[-1] / traj.t[-1]) if traj.t[-1] > 0 else 0.0,
        }


@dataclass
class PlanarForwardModel:
    """`ForwardModel` stub for the Planar tier (architecture.md §8.1, §8.4
    "`CasadiCollocation` on planar"). Deferred to ticket T10 -- implementing
    it needs the differentiable foot-of-normal solve (`casadi.rootfinder`)
    research flagged in architecture.md §8.4's "Blocker to research" note,
    which is out of this ticket's scope (T8 depends only on T4). The
    `ForwardModel` protocol above is already general enough that T10 can
    fill this in without touching any other `optim` module."""

    spec: BikeSpec

    @property
    def differentiable(self) -> bool:
        return False

    def rollout(self, controls: ControlInputs, horizon: Horizon) -> Trajectory:
        raise NotImplementedError(
            "PlanarForwardModel.rollout is not implemented yet -- see ticket T10 "
            "(architecture.md S8.4 'CasadiCollocation on planar' / S8.1)."
        )

    def metrics(self, traj: Trajectory) -> dict[str, float]:
        raise NotImplementedError(
            "PlanarForwardModel.metrics is not implemented yet -- see ticket T10 "
            "(architecture.md S8.4 'CasadiCollocation on planar' / S8.1)."
        )
