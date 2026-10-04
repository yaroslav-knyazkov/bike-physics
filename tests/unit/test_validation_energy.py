"""Tests for bikephysics.validation.energy (architecture.md S12.3, task.md
T4)."""

from __future__ import annotations

import pytest

from bikephysics.core.pedals import ClippedSine, ConstantCrankTorque, ZeroPedal
from bikephysics.core.road import ConstantGrade
from bikephysics.simplified import simulate as sim
from bikephysics.validation import energy
from tests.unit.factories import make_minimal_bike_spec, make_synthetic_trajectory


def test_kinetic_energy_requires_a_bike_spec() -> None:
    traj = make_synthetic_trajectory()  # spec=None
    with pytest.raises(ValueError, match="requires trajectory.spec"):
        energy.kinetic_energy(traj)


def test_kinetic_energy_matches_half_m_eq_v_squared() -> None:
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=20.0, r_1_pedal=spec.frame.r_1_pedal)
    traj = sim.simulate(spec, pedal, t_span=(0.0, 10.0), n_output=20)
    ke = energy.kinetic_energy(traj)
    assert ke[0] == pytest.approx(0.5 * spec.m_eq * traj.v[0] ** 2)
    assert ke[-1] == pytest.approx(0.5 * spec.m_eq * traj.v[-1] ** 2)


def test_potential_energy_is_zero_on_a_flat_road_with_no_grade() -> None:
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=20.0, r_1_pedal=spec.frame.r_1_pedal)
    traj = sim.simulate(spec, pedal, t_span=(0.0, 10.0), n_output=20)
    pe = energy.potential_energy(traj)
    assert pe[0] == pytest.approx(0.0)
    assert pe[-1] == pytest.approx(0.0, abs=1e-9)


def test_potential_energy_increases_climbing_a_grade() -> None:
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=40.0, r_1_pedal=spec.frame.r_1_pedal)
    road = ConstantGrade(theta=0.05)
    traj = sim.simulate(spec, pedal, road=road, v0=3.0, t_span=(0.0, 20.0), n_output=200)
    pe = energy.potential_energy(traj)
    assert pe[-1] > pe[0]


def test_audit_closes_for_a_level_cruise() -> None:
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=25.0, r_1_pedal=spec.frame.r_1_pedal)
    traj = sim.simulate(spec, pedal, t_span=(0.0, 60.0), n_output=2000)
    result = energy.audit(traj)
    assert result.closure_error_fraction < 1e-3  # < 0.1%, task.md T4 done-when
    assert result.work_ground == 0.0  # Simplified tier: no-slip rolling, zero ground work


def test_audit_closes_for_a_hill_climb_with_periodic_pedal_input() -> None:
    spec = make_minimal_bike_spec()
    pedal = ClippedSine(P_0=250.0, theta_0=0.0, n=2.0)
    road = ConstantGrade(theta=0.04)
    traj = sim.simulate(
        spec, pedal, road=road, v0=2.0, theta_3b0=0.9, t_span=(0.0, 90.0), n_output=2000
    )
    result = energy.audit(traj)
    assert result.closure_error_fraction < 1e-3


def test_audit_closes_for_a_coast_with_zero_pedal_force() -> None:
    spec = make_minimal_bike_spec()
    traj = sim.simulate(spec, ZeroPedal(), v0=6.0, t_span=(0.0, 30.0), n_output=1000)
    result = energy.audit(traj)
    assert result.closure_error_fraction < 1e-3
    assert result.work_pedal_in == 0.0


def test_energy_audit_dataclass_fields_sum_correctly() -> None:
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=25.0, r_1_pedal=spec.frame.r_1_pedal)
    traj = sim.simulate(spec, pedal, t_span=(0.0, 60.0), n_output=500)
    result = energy.audit(traj)
    reconstructed = (
        result.delta_ke + result.delta_pe + result.work_aero + result.work_rr + result.work_brake
        + result.work_ground
    )
    assert result.work_pedal_in == pytest.approx(
        reconstructed + result.closure_error, abs=1e-6
    )
