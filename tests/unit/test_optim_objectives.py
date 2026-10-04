"""Tests for bikephysics.optim.objectives (architecture.md S8.3)."""

from __future__ import annotations

import numpy as np
import pytest

from bikephysics.optim.objectives import (
    MaxDistanceInTime,
    MinEnergyWithTimeCap,
    MinMechanicalEnergy,
    MinTimeOverDistance,
    TrackSpeedProfile,
    Weighted,
)
from tests.unit.factories import make_synthetic_trajectory


def test_min_time_over_distance_returns_t_final_when_reached() -> None:
    traj = make_synthetic_trajectory()
    obj = MinTimeOverDistance(x_target=float(traj.x[-1]))
    assert obj.value(traj) == pytest.approx(float(traj.t[-1]))


def test_min_time_over_distance_penalizes_shortfall_quadratically() -> None:
    traj = make_synthetic_trajectory()
    obj = MinTimeOverDistance(x_target=10.0, penalty_weight=5.0)
    shortfall = 10.0 - float(traj.x[-1])
    assert obj.value(traj) == pytest.approx(float(traj.t[-1]) + 5.0 * shortfall**2)


def test_max_distance_in_time_is_negative_x_final() -> None:
    traj = make_synthetic_trajectory()
    obj = MaxDistanceInTime(t_end=float(traj.t[-1]))
    assert obj.value(traj) == pytest.approx(-float(traj.x[-1]))


def test_min_mechanical_energy_is_pure_energy_when_speed_matches_target() -> None:
    traj = make_synthetic_trajectory()
    mean_speed = float(traj.x[-1]) / float(traj.t[-1])
    obj = MinMechanicalEnergy(target_mean_speed=mean_speed)
    energy = float(np.trapezoid(traj.power_pedal, traj.t))
    assert obj.value(traj) == pytest.approx(energy)


def test_min_mechanical_energy_penalizes_speed_mismatch() -> None:
    traj = make_synthetic_trajectory()
    energy = float(np.trapezoid(traj.power_pedal, traj.t))
    obj_off_target = MinMechanicalEnergy(target_mean_speed=0.0, penalty_weight=2.0)
    assert obj_off_target.value(traj) > energy


def test_min_energy_with_time_cap_penalizes_shortfall_and_overtime() -> None:
    traj = make_synthetic_trajectory()
    energy = float(np.trapezoid(traj.power_pedal, traj.t))
    obj = MinEnergyWithTimeCap(x_target=10.0, t_cap=0.1, penalty_weight=2.0)
    assert obj.value(traj) > energy


def test_track_speed_profile_is_zero_when_matching_exactly() -> None:
    traj = make_synthetic_trajectory()
    obj = TrackSpeedProfile(v_ref=lambda t: 1.0)  # synthetic traj.v is all ones
    assert obj.value(traj) == pytest.approx(0.0, abs=1e-9)


def test_track_speed_profile_is_positive_when_mismatched() -> None:
    traj = make_synthetic_trajectory()
    obj = TrackSpeedProfile(v_ref=lambda t: 0.0)
    assert obj.value(traj) > 0.0


def test_weighted_sums_terms_and_unions_requires() -> None:
    traj = make_synthetic_trajectory()
    a = MaxDistanceInTime(t_end=1.0)
    b = MaxDistanceInTime(t_end=1.0)
    combined = Weighted(terms=[(a, 1.0), (b, 2.0)])
    assert combined.value(traj) == pytest.approx(3.0 * a.value(traj))
    assert combined.requires == a.requires
