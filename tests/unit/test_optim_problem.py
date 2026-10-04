"""Tests for bikephysics.optim.problem (architecture.md S8.5)."""

from __future__ import annotations

import pytest

from bikephysics.optim.forward import SimplifiedForwardModel
from bikephysics.optim.parametrization import Composite, PiecewiseConstantPedal, TemplateShape
from bikephysics.optim.problem import (
    build_constraint,
    build_objective,
    build_parametrization,
    list_problem_presets,
    load_problem,
    resolve_problem,
    resolve_problem_path,
)
from tests.unit.factories import make_minimal_bike_spec

_SHIPPED_DECKS = ("min_time_1km", "oracle_constant_power", "infeasible_steep_climb")


@pytest.mark.parametrize("name", _SHIPPED_DECKS)
def test_shipped_problem_decks_load_and_resolve(name: str) -> None:
    path = resolve_problem_path(name)
    problem = load_problem(path)
    resolved = resolve_problem(problem)
    assert resolved.name
    assert isinstance(resolved.forward_model, SimplifiedForwardModel)
    assert resolved.horizon.t_max > 0.0


def test_list_problem_presets_includes_every_shipped_deck() -> None:
    names = list_problem_presets()
    for name in _SHIPPED_DECKS:
        assert name in names


def test_min_time_1km_parametrization_is_piecewise_constant_pedal_n20() -> None:
    resolved = resolve_problem(load_problem(resolve_problem_path("min_time_1km")))
    assert isinstance(resolved.parametrization, PiecewiseConstantPedal)
    assert resolved.parametrization.n == 20


def test_oracle_problem_parametrization_is_constant_power_template() -> None:
    resolved = resolve_problem(load_problem(resolve_problem_path("oracle_constant_power")))
    assert isinstance(resolved.parametrization, TemplateShape)
    assert resolved.parametrization.kind == "constant_power"


def test_build_parametrization_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown parametrization"):
        build_parametrization({"kind": "nonexistent"}, spec=make_minimal_bike_spec(), t_max=10.0)


def test_build_objective_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown objective"):
        build_objective({"kind": "nonexistent"}, horizon_x_target=None, horizon_t_max=10.0)


def test_build_objective_min_time_without_x_target_raises() -> None:
    with pytest.raises(ValueError, match="x_target"):
        build_objective(
            {"kind": "min_time_over_distance"}, horizon_x_target=None, horizon_t_max=10.0
        )


def test_build_constraint_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="unknown constraint"):
        build_constraint({"kind": "nonexistent"})


def test_build_parametrization_composite_from_toml_like_dict() -> None:
    spec = make_minimal_bike_spec()
    cfg = {
        "kind": "composite",
        "parts": [
            {"kind": "piecewise_constant_pedal", "n": 3},
            {"kind": "piecewise_constant_brake", "n": 2},
        ],
    }
    param = build_parametrization(cfg, spec=spec, t_max=10.0)
    assert isinstance(param, Composite)
    assert param.n_vars == 5
