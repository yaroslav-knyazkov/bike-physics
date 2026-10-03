"""Tests for bikephysics.core.results (architecture.md S5.5, task.md T1)."""

from __future__ import annotations

import numpy as np
import pytest

from bikephysics.core.results import LimitMargins, ModeLog, RunSummary, Trajectory
from tests.unit.factories import make_synthetic_trajectory


def test_synthetic_trajectory_validates() -> None:
    make_synthetic_trajectory().validate()


def test_trajectory_is_frozen() -> None:
    import dataclasses

    trajectory = make_synthetic_trajectory()
    with pytest.raises(dataclasses.FrozenInstanceError):
        trajectory.x = trajectory.x  # type: ignore[misc]


def test_trajectory_validate_rejects_mismatched_array_length() -> None:
    trajectory = make_synthetic_trajectory(n=5)
    bad = Trajectory(**{**trajectory.__dict__, "v": np.ones(3)})
    with pytest.raises(ValueError, match="length mismatch"):
        bad.validate()


def test_limit_margins_validate_rejects_mismatched_length() -> None:
    bad = LimitMargins(
        traction_rear=np.ones(5),
        traction_front=np.ones(5),
        wheelie=np.ones(2),
        stoppie=np.ones(5),
        freewheel=np.ones(5),
    )
    with pytest.raises(ValueError, match="length mismatch"):
        bad.validate()


def test_trajectory_validate_rejects_mismatched_reaction_field_length() -> None:
    trajectory = make_synthetic_trajectory(n=5)
    bad = Trajectory(**{**trajectory.__dict__, "R_2a_x": np.ones(3)})
    with pytest.raises(ValueError, match="length mismatch"):
        bad.validate()


def test_mode_log_validate_checks_event_arrays() -> None:
    good = ModeLog(
        t=np.array([0.1, 0.2]),
        body=("2a", "2b"),
        mode=("stick", "slip"),
        energy_loss=np.array([0.0, 0.5]),
    )
    good.validate()

    bad = ModeLog(
        t=np.array([0.1, 0.2]),
        body=("2a",),
        mode=("stick", "slip"),
        energy_loss=np.array([0.0, 0.5]),
    )
    with pytest.raises(ValueError, match="matching length"):
        bad.validate()


def test_run_summary_round_trips_as_a_plain_dataclass() -> None:
    summary = RunSummary(
        run_id="20260101T000000Z-test",
        created_utc="2026-01-01T00:00:00+00:00",
        package_version="0.1.0",
        git_sha="abc123",
        git_dirty=False,
        numpy_version="2.5.3",
        scipy_version="1.18.1",
        casadi_version=None,
        wall_time_s=0.01,
        command="bike simplified run",
        seed=42,
        solver={"method": "Radau"},
        tolerances={"v_eps": 0.01},
        metrics={"steady_speed": 8.3},
    )
    assert summary.seed == 42
    assert summary.tolerances["v_eps"] == 0.01
