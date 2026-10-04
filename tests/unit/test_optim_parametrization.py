"""Tests for bikephysics.optim.parametrization (architecture.md S8.2)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bikephysics.optim.parametrization import (
    Composite,
    ConstantPowerPedal,
    GearSelection,
    PiecewiseConstantBrake,
    PiecewiseConstantPedal,
    SplinePedal,
    TemplateShape,
    crank_ratio,
    gear_factor,
)
from tests.unit.factories import make_minimal_bike_spec


def test_constant_power_pedal_force_delivers_the_configured_power() -> None:
    spec = make_minimal_bike_spec()
    pedal = ConstantPowerPedal(
        power=200.0, crank_ratio=crank_ratio(spec), gear_factor=gear_factor(spec)
    )
    v = 5.0
    omega_3b = -v * crank_ratio(spec)
    p_d, p_f = pedal.forces(0.0, 0.0, omega_3b)
    assert p_f == 0.0
    wheel_force = gear_factor(spec) * p_d
    assert wheel_force * v == pytest.approx(200.0, rel=1e-6)
    # `full()` always returns None for this carrier; mypy flags comparing
    # a None-returning call's result on purpose, so this is explicit.
    assert pedal.full(0.0, 0.0, omega_3b) is None  # type: ignore[func-returns-value]


def test_template_shape_von_mises_bounds_initial_guess_and_describe() -> None:
    spec = make_minimal_bike_spec()
    param = TemplateShape(kind="von_mises")
    assert param.n_vars == 3
    bounds = param.bounds(spec)
    assert len(bounds) == 3
    x0 = param.initial_guess(spec)
    assert x0.shape == (3,)
    controls = param.build(x0)
    assert controls.pedal is not None
    desc = param.describe(x0)
    assert desc["kind"] == "von_mises"
    assert "shape" in desc


def test_template_shape_constant_power_requires_spec() -> None:
    with pytest.raises(ValueError, match="constant_power"):
        TemplateShape(kind="constant_power")


def test_template_shape_constant_power_builds_constant_power_pedal() -> None:
    spec = make_minimal_bike_spec()
    param = TemplateShape(kind="constant_power", spec=spec)
    assert param.n_vars == 1
    assert len(param.bounds(spec)) == 1
    controls = param.build(np.array([150.0]))
    assert isinstance(controls.pedal, ConstantPowerPedal)
    assert controls.pedal.power == pytest.approx(150.0)


def test_piecewise_constant_pedal_shapes_and_knots() -> None:
    spec = make_minimal_bike_spec()
    param = PiecewiseConstantPedal(n=5, t_max=10.0)
    assert param.n_vars == 5
    assert len(param.bounds(spec)) == 5
    x0 = param.initial_guess(spec)
    assert x0.shape == (5,)
    desc = param.describe(x0)
    assert len(desc["knots"]) == 6  # n+1 knots for n intervals


def test_piecewise_constant_pedal_clips_negative_decision_values() -> None:
    param = PiecewiseConstantPedal(n=3, t_max=9.0)
    controls = param.build(np.array([-5.0, 10.0, -1.0]))
    p_d, _p_f = controls.pedal.forces(1.0, 0.0, 0.0)
    assert p_d >= 0.0


def test_spline_pedal_is_non_negative() -> None:
    spec = make_minimal_bike_spec()
    param = SplinePedal(n=4, t_max=10.0, p_bounds=(0.0, 100.0))
    x0 = param.initial_guess(spec)
    controls = param.build(x0)
    for t in np.linspace(0.0, 10.0, 20):
        p_d, p_f = controls.pedal.forces(float(t), 0.0, 0.0)
        assert p_d >= 0.0
        assert p_f == 0.0


def test_piecewise_constant_brake_splits_front_rear_and_zero_pedal() -> None:
    from bikephysics.core.brakes import KinematicState
    from bikephysics.core.pedals import ZeroPedal

    param = PiecewiseConstantBrake(n=2, t_max=4.0, front_bias=0.7)
    controls = param.build(np.array([100.0, 100.0]))
    assert isinstance(controls.pedal, ZeroPedal)
    b_2a, b_2b = controls.brake.forces(1.0, KinematicState(x=0.0, v=0.0))
    assert b_2b == pytest.approx(70.0)
    assert b_2a == pytest.approx(30.0)


def test_gear_selection_overrides_n_teeth_and_recomputes_r_2a_cg() -> None:
    spec = make_minimal_bike_spec()
    param = GearSelection(cog_teeth_options=(14, 16, 18), spec=spec)
    controls = param.build(np.array([1.0]))
    assert controls.spec_overrides["drivetrain.n_teeth_cg"] == 16
    expected_r = spec.drivetrain.chain_pitch / (2.0 * math.sin(math.pi / 16))
    assert controls.spec_overrides["drivetrain.r_2a_cg"] == pytest.approx(expected_r)


def test_gear_selection_rejects_empty_options() -> None:
    with pytest.raises(ValueError):
        GearSelection(cog_teeth_options=(), spec=make_minimal_bike_spec())


def test_gear_selection_rounds_and_clamps_index() -> None:
    spec = make_minimal_bike_spec()
    param = GearSelection(cog_teeth_options=(14, 16, 18), spec=spec)
    assert param.describe(np.array([-5.0]))["index"] == 0
    assert param.describe(np.array([50.0]))["index"] == 2


def test_composite_concatenates_bounds_and_merges_controls() -> None:
    spec = make_minimal_bike_spec()
    pedal_part = PiecewiseConstantPedal(n=2, t_max=4.0)
    brake_part = PiecewiseConstantBrake(n=2, t_max=4.0)
    gear_part = GearSelection(cog_teeth_options=(14, 16), spec=spec)
    composite = Composite(parts=[pedal_part, brake_part, gear_part])
    assert composite.n_vars == 5
    assert len(composite.bounds(spec)) == 5
    x0 = composite.initial_guess(spec)
    assert x0.shape == (5,)
    controls = composite.build(x0)
    assert "drivetrain.n_teeth_cg" in controls.spec_overrides
    desc = composite.describe(x0)
    assert len(desc) == 3


def test_composite_rejects_two_non_trivial_pedal_parts() -> None:
    composite = Composite(
        parts=[PiecewiseConstantPedal(n=1, t_max=1.0), PiecewiseConstantPedal(n=1, t_max=1.0)]
    )
    with pytest.raises(ValueError, match="pedal"):
        composite.build(np.array([10.0, 10.0]))
