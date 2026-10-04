"""Tests for bikephysics.core.brakes (architecture.md S5.4, task.md T2).

Not named in task.md T2's own test-file list (`test_{params,atmosphere,
aero,road,pedals,limits}.py` omits `brakes`, likely an oversight since the
module list two lines above it does include `core/brakes.py`), but every
new module gets tests per this project's standing convention
(task.md's own "Conventions for every ticket": "tests written in the same
ticket, not after").
"""

from __future__ import annotations

import numpy as np
import pytest

from bikephysics.core.brakes import (
    BrakeAtDistance,
    BrakeSchedule,
    ConstantBrake,
    KinematicState,
    TabulatedBrake,
)


def test_constant_brake_returns_fixed_forces_regardless_of_state() -> None:
    model = ConstantBrake(B_2a=50.0, B_2b=100.0)
    for state in (KinematicState(x=0.0, v=5.0), KinematicState(x=1000.0, v=0.0)):
        assert model.forces(0.0, state) == (50.0, 100.0)


def test_constant_brake_default_is_zero() -> None:
    model = ConstantBrake()
    assert model.forces(0.0, KinematicState(x=0.0, v=0.0)) == (0.0, 0.0)


def test_constant_brake_rejects_negative_forces() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        ConstantBrake(B_2a=-1.0, B_2b=0.0)


def test_brake_at_distance_triggers_at_threshold() -> None:
    model = BrakeAtDistance(x_trigger=10.0, B_2a=20.0, B_2b=30.0)
    assert model.forces(0.0, KinematicState(x=5.0, v=5.0)) == (0.0, 0.0)
    assert model.forces(0.0, KinematicState(x=10.0, v=5.0)) == (20.0, 30.0)
    assert model.forces(0.0, KinematicState(x=15.0, v=5.0)) == (20.0, 30.0)


def test_brake_at_distance_rejects_negative_forces() -> None:
    with pytest.raises(ValueError, match="must be >= 0"):
        BrakeAtDistance(x_trigger=10.0, B_2a=-1.0, B_2b=0.0)


def test_brake_schedule_interpolates_linearly() -> None:
    model = BrakeSchedule(
        t_knots=np.array([0.0, 1.0, 2.0]),
        B_2a_knots=np.array([0.0, 50.0, 0.0]),
        B_2b_knots=np.array([0.0, 20.0, 0.0]),
    )
    b_2a, b_2b = model.forces(0.5, KinematicState(x=0.0, v=0.0))
    assert b_2a == pytest.approx(25.0)
    assert b_2b == pytest.approx(10.0)


def test_brake_schedule_holds_edge_values_outside_knot_range() -> None:
    model = BrakeSchedule(
        t_knots=np.array([1.0, 2.0]),
        B_2a_knots=np.array([10.0, 20.0]),
        B_2b_knots=np.array([5.0, 15.0]),
    )
    assert model.forces(-5.0, KinematicState(x=0.0, v=0.0)) == (10.0, 5.0)
    assert model.forces(50.0, KinematicState(x=0.0, v=0.0)) == (20.0, 15.0)


def test_tabulated_brake_interpolates() -> None:
    model = TabulatedBrake(
        t=np.array([0.0, 1.0, 2.0]),
        B_2a=np.array([0.0, 40.0, 0.0]),
        B_2b=np.array([0.0, 10.0, 0.0]),
    )
    b_2a, b_2b = model.forces(1.5, KinematicState(x=0.0, v=0.0))
    assert b_2a == pytest.approx(20.0)
    assert b_2b == pytest.approx(5.0)
