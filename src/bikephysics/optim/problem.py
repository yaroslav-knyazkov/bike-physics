"""optim/problem.py -- the problem-definition model + TOML loader
(architecture.md §8.5).

`OptimizationProblem` is the pydantic model `experiments are files, not
code` is built on: every `[section]` in the TOML is kept as a plain
`dict[str, Any]` rather than its own strict pydantic sub-model, because
each section's actual shape is a `kind` tag plus kind-specific keyword
arguments for one of `optim.parametrization`/`optim.objectives`/
`optim.constraints`/`optim.solvers`' many implementations -- a registry
dispatch, not a fixed schema, mirroring how `core.road.load_road` already
dispatches a `kind` field onto a `Road` implementation.

This module must not import `bikephysics.cli` (architecture.md §4: "cli is
imported by nothing" -- `cli` already imports `optim.problem`, so the
reverse import would be a cycle); preset-name/path resolution is therefore
a small local helper (`_resolve_path`) rather than a reuse of
`cli.common.resolve_config_path`.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from bikephysics.core.params import BIKE_PRESET_DIR, BikeSpec, bike_spec_from_dict, read_toml
from bikephysics.core.road import ROAD_PRESET_DIR, Road, load_road
from bikephysics.optim.constraints import (
    CadenceBound,
    ChainTensionNonNegative,
    PathConstraint,
    PedalForceBound,
    PowerCeiling,
    SpeedBounds,
    StoppieMargin,
    TerminalState,
    TractionMargin,
    WheelieMargin,
)
from bikephysics.optim.forward import (
    ForwardModel,
    Horizon,
    PlanarForwardModel,
    SimplifiedForwardModel,
)
from bikephysics.optim.objectives import (
    MaxDistanceInTime,
    MinEnergyWithTimeCap,
    MinMechanicalEnergy,
    MinTimeOverDistance,
    Objective,
    Weighted,
)
from bikephysics.optim.parametrization import (
    Composite,
    ControlParametrization,
    GearSelection,
    PiecewiseConstantBrake,
    PiecewiseConstantPedal,
    SplinePedal,
    TemplateShape,
)

#: Shipped problem presets (architecture.md §9: "bike spec presets" /
#: "problems: (none yet -- optimization problems ship starting T8)").
PROBLEM_PRESET_DIR = Path(__file__).resolve().parents[1] / "data" / "problems"


class OptimizationProblem(BaseModel):
    """architecture.md §8.5's TOML schema. `horizon`/`initial`/
    `parametrization`/`objective`/`solver` are plain dicts (module
    docstring); `constraints` is the TOML `[[constraints]]` array of
    tables, mapped onto `list[dict]` directly."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    forward_model: Literal["simplified", "planar"] = "simplified"
    bike: str
    road: str = "flat"
    horizon: dict[str, Any]
    initial: dict[str, Any] = Field(default_factory=dict)
    parametrization: dict[str, Any]
    objective: dict[str, Any]
    constraints: list[dict[str, Any]] = Field(default_factory=list)
    solver: dict[str, Any]


@dataclass(frozen=True)
class ResolvedProblem:
    """The runtime bundle `optim.solvers.base.Solver.solve` actually
    consumes -- a resolved `OptimizationProblem`, with every `kind` string
    turned into a live object. Built by `resolve_problem` below."""

    name: str
    forward_model: ForwardModel
    parametrization: ControlParametrization
    objective: Objective
    constraints: tuple[PathConstraint, ...]
    horizon: Horizon
    spec: BikeSpec
    solver_config: dict[str, Any]


def _resolve_path(name_or_path: str, preset_dir: Path) -> Path:
    """A preset name (`<preset_dir>/<name_or_path>.toml`) or a direct file
    path -- the same two-step lookup `cli.common.resolve_config_path` does,
    duplicated locally (module docstring) since `optim` must not import
    `cli`."""
    direct = Path(name_or_path)
    if direct.is_file():
        return direct
    candidate = preset_dir / f"{name_or_path}.toml"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        f"no file or preset named {name_or_path!r} found (looked for {candidate})"
    )


def load_problem(path: Path) -> OptimizationProblem:
    """Load and validate a problem TOML file."""
    with Path(path).open("rb") as f:
        data = tomllib.load(f)
    return OptimizationProblem.model_validate(data)


def resolve_problem_path(name_or_path: str) -> Path:
    """Resolve `--problem` to a concrete file path (preset name under
    `PROBLEM_PRESET_DIR`, or a direct path)."""
    return _resolve_path(name_or_path, PROBLEM_PRESET_DIR)


def list_problem_presets() -> list[str]:
    """Names (without `.toml`) of every shipped problem preset."""
    if not PROBLEM_PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in PROBLEM_PRESET_DIR.glob("*.toml"))


def _build_forward_model(problem: OptimizationProblem, spec: BikeSpec, road: Road) -> ForwardModel:
    if problem.forward_model == "simplified":
        return SimplifiedForwardModel(spec=spec, road=road)
    return PlanarForwardModel(spec=spec)


_PARAM_KINDS_NEEDING_T_MAX = frozenset(
    {"piecewise_constant_pedal", "spline_pedal", "piecewise_constant_brake"}
)


def build_parametrization(
    cfg: Mapping[str, Any], *, spec: BikeSpec, t_max: float
) -> ControlParametrization:
    """`[parametrization]` -> a `ControlParametrization`. `TemplateShape`'s
    own `kind` field (which of `core.pedals`' templates to fit) is spelled
    `shape` in TOML to avoid colliding with this registry's own `kind` key
    (which *class* to build)."""
    kind = cfg.get("kind")
    kwargs = {k: v for k, v in cfg.items() if k != "kind"}
    if kind == "template_shape":
        if "shape" in kwargs:
            kwargs["kind"] = kwargs.pop("shape")
        return TemplateShape(spec=spec, **kwargs)
    if kind in _PARAM_KINDS_NEEDING_T_MAX:
        kwargs.setdefault("t_max", t_max)
        if kind == "piecewise_constant_pedal":
            return PiecewiseConstantPedal(**kwargs)
        if kind == "spline_pedal":
            return SplinePedal(**kwargs)
        return PiecewiseConstantBrake(**kwargs)
    if kind == "gear_selection":
        options = tuple(int(v) for v in kwargs.pop("cog_teeth_options"))
        return GearSelection(cog_teeth_options=options, spec=spec, **kwargs)
    if kind == "composite":
        parts_cfg = kwargs.pop("parts")
        parts = [build_parametrization(p, spec=spec, t_max=t_max) for p in parts_cfg]
        return Composite(parts=parts)
    raise ValueError(f"unknown parametrization kind {kind!r}")


def build_objective(
    cfg: Mapping[str, Any], *, horizon_x_target: float | None, horizon_t_max: float
) -> Objective:
    """`[objective]` -> an `Objective`. `x_target`/`t_end`/`t_cap` default
    to the problem's own `horizon` fields when the objective config omits
    them -- architecture.md §8.5's own example never repeats `x_target` on
    `min_time_over_distance`, relying on `[horizon]`'s."""
    kind = cfg.get("kind")
    kwargs = dict({k: v for k, v in cfg.items() if k != "kind"})
    if kind == "min_time_over_distance":
        kwargs.setdefault("x_target", horizon_x_target)
        if kwargs.get("x_target") is None:
            raise ValueError(
                "min_time_over_distance needs x_target (set [horizon].x_target or "
                "[objective].x_target)"
            )
        return MinTimeOverDistance(**kwargs)
    if kind == "max_distance_in_time":
        kwargs.setdefault("t_end", horizon_t_max)
        return MaxDistanceInTime(**kwargs)
    if kind == "min_mechanical_energy":
        return MinMechanicalEnergy(**kwargs)
    if kind == "min_energy_with_time_cap":
        kwargs.setdefault("x_target", horizon_x_target)
        kwargs.setdefault("t_cap", horizon_t_max)
        if kwargs.get("x_target") is None:
            raise ValueError("min_energy_with_time_cap needs x_target")
        return MinEnergyWithTimeCap(**kwargs)
    if kind == "weighted":
        terms_cfg = kwargs.pop("terms")
        terms = []
        for term in terms_cfg:
            term = dict(term)
            weight = term.pop("weight", 1.0)
            term_objective = build_objective(
                term, horizon_x_target=horizon_x_target, horizon_t_max=horizon_t_max
            )
            terms.append((term_objective, weight))
        return Weighted(terms=terms)
    if kind == "track_speed_profile":
        raise ValueError(
            "track_speed_profile needs a Python v_ref(t) callable and cannot be "
            "built from TOML -- construct optim.objectives.TrackSpeedProfile directly."
        )
    raise ValueError(f"unknown objective kind {kind!r}")


_CONSTRAINT_REGISTRY: dict[str, type] = {
    "power_ceiling": PowerCeiling,
    "pedal_force_bound": PedalForceBound,
    "cadence_bound": CadenceBound,
    "traction_margin": TractionMargin,
    "wheelie_margin": WheelieMargin,
    "stoppie_margin": StoppieMargin,
    "chain_tension_non_negative": ChainTensionNonNegative,
    "speed_bounds": SpeedBounds,
    "terminal_state": TerminalState,
}


def build_constraint(cfg: Mapping[str, Any]) -> PathConstraint:
    """One `[[constraints]]` entry -> a `PathConstraint`."""
    kind = cfg.get("kind")
    cls = _CONSTRAINT_REGISTRY.get(str(kind))
    if cls is None:
        raise ValueError(
            f"unknown constraint kind {kind!r}; expected one of {sorted(_CONSTRAINT_REGISTRY)}"
        )
    kwargs = {k: v for k, v in cfg.items() if k != "kind"}
    return cls(**kwargs)


def resolve_problem(problem: OptimizationProblem) -> ResolvedProblem:
    """Turn a validated `OptimizationProblem` into a `ResolvedProblem`:
    resolve `bike`/`road` presets, build the `ForwardModel`,
    `ControlParametrization`, `Objective`, and every `PathConstraint`."""
    bike_path = _resolve_path(problem.bike, BIKE_PRESET_DIR)
    spec = bike_spec_from_dict(read_toml(bike_path))
    road_path = _resolve_path(problem.road, ROAD_PRESET_DIR)
    road = load_road(road_path)

    horizon_kwargs: dict[str, Any] = {
        "t_max": float(problem.horizon["t_max"]),
        "x_target": (
            float(problem.horizon["x_target"])
            if problem.horizon.get("x_target") is not None
            else None
        ),
        "x0": float(problem.initial.get("x_0", 0.0)),
        "v0": float(problem.initial.get("v_0", 0.0)),
        "theta_3b0": float(problem.initial.get("theta_3b_0", 0.0)),
    }
    if "n_output" in problem.horizon:
        # Not part of architecture.md S8.5's own worked example, but a
        # harmless performance knob: fewer output samples per rollout only
        # coarsens the *reported* time resolution (margins, the returned
        # Trajectory), never solve_ivp's own rtol/atol-controlled dynamics
        # accuracy -- valuable since a solver calls rollout() thousands of
        # times.
        horizon_kwargs["n_output"] = int(problem.horizon["n_output"])
    horizon = Horizon(**horizon_kwargs)

    forward_model = _build_forward_model(problem, spec, road)
    parametrization = build_parametrization(problem.parametrization, spec=spec, t_max=horizon.t_max)
    objective = build_objective(
        problem.objective, horizon_x_target=horizon.x_target, horizon_t_max=horizon.t_max
    )
    constraints = tuple(build_constraint(c) for c in problem.constraints)

    return ResolvedProblem(
        name=problem.name,
        forward_model=forward_model,
        parametrization=parametrization,
        objective=objective,
        constraints=constraints,
        horizon=horizon,
        spec=spec,
        solver_config=dict(problem.solver),
    )
