"""Tests for bikephysics.optim.forward (architecture.md S8.1)."""

from __future__ import annotations

import pytest

from bikephysics.core.params import BikeSpec
from bikephysics.core.pedals import ConstantCrankTorque
from bikephysics.optim.forward import (
    ControlInputs,
    Horizon,
    PlanarForwardModel,
    SimplifiedForwardModel,
)
from tests.unit.factories import make_minimal_bike_spec


def _pedal(spec: BikeSpec) -> ConstantCrankTorque:
    return ConstantCrankTorque(tau=40.0, r_1_pedal=spec.frame.r_1_pedal)


def test_simplified_forward_model_rollout_returns_valid_trajectory_with_pedal_force_meta() -> None:
    spec = make_minimal_bike_spec()
    model = SimplifiedForwardModel(spec=spec)
    traj = model.rollout(ControlInputs(pedal=_pedal(spec)), Horizon(t_max=20.0, n_output=200))
    traj.validate()
    assert traj.t[-1] <= 20.0 + 1e-6
    assert "P_D" in traj.meta
    assert "P_F" in traj.meta
    assert len(traj.meta["P_D"]) == len(traj.t)
    assert all(p >= 0.0 for p in traj.meta["P_D"])


def test_simplified_forward_model_metrics() -> None:
    spec = make_minimal_bike_spec()
    model = SimplifiedForwardModel(spec=spec)
    traj = model.rollout(ControlInputs(pedal=_pedal(spec)), Horizon(t_max=20.0, n_output=200))
    metrics = model.metrics(traj)
    assert metrics["t_final"] == pytest.approx(float(traj.t[-1]))
    assert metrics["x_final"] == pytest.approx(float(traj.x[-1]))
    assert metrics["mechanical_energy"] > 0.0


def test_simplified_forward_model_spec_override_changes_dynamics() -> None:
    spec = make_minimal_bike_spec()
    model = SimplifiedForwardModel(spec=spec)
    horizon = Horizon(t_max=20.0, n_output=200)
    traj_default = model.rollout(ControlInputs(pedal=_pedal(spec)), horizon)
    traj_heavier = model.rollout(
        ControlInputs(pedal=_pedal(spec), spec_overrides={"frame.m_1": 200.0}), horizon
    )
    assert traj_default.v[-1] != pytest.approx(traj_heavier.v[-1])
    assert traj_heavier.v[-1] < traj_default.v[-1]  # heavier bike accelerates slower


def test_simplified_forward_model_is_not_differentiable() -> None:
    model = SimplifiedForwardModel(spec=make_minimal_bike_spec())
    assert model.differentiable is False


def test_planar_forward_model_rollout_raises_not_implemented_naming_t10() -> None:
    spec = make_minimal_bike_spec()
    model = PlanarForwardModel(spec=spec)
    with pytest.raises(NotImplementedError, match="T10"):
        model.rollout(ControlInputs(pedal=_pedal(spec)), Horizon(t_max=1.0))


def test_planar_forward_model_metrics_raises_not_implemented_naming_t10() -> None:
    model = PlanarForwardModel(spec=make_minimal_bike_spec())
    with pytest.raises(NotImplementedError, match="T10"):
        model.metrics(None)  # type: ignore[arg-type]
