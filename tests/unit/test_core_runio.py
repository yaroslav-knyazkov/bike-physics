"""Tests for bikephysics.core.runio (architecture.md S5.6, S12.2; task.md T1
done-when: "runio round-trips a synthetic Trajectory")."""

from __future__ import annotations

from pathlib import Path

from bikephysics.core import runio
from tests.unit.factories import make_synthetic_trajectory


def test_make_run_dir_creates_timestamped_slug_directory(tmp_path: Path) -> None:
    run_dir = runio.make_run_dir(tmp_path, "my-run")
    assert run_dir.exists()
    assert run_dir.parent == tmp_path
    assert run_dir.name.endswith("-my-run")
    assert "T" in run_dir.name and run_dir.name.endswith("Z-my-run")


def test_build_run_summary_has_the_s12_2_provenance_block() -> None:
    summary = runio.build_run_summary(
        run_id="run-1",
        command="bike simplified run",
        wall_time_s=0.5,
        solver={"method": "Radau"},
        tolerances={"v_eps": 0.01},
        metrics={"steady_speed": 8.3},
        seed=7,
    )
    assert summary.package_version
    assert summary.git_sha  # "unknown" is an acceptable fallback outside a git checkout
    assert isinstance(summary.git_dirty, bool)
    assert summary.numpy_version
    assert summary.wall_time_s == 0.5
    assert summary.seed == 7


def test_summary_json_round_trip_preserves_values(tmp_path: Path) -> None:
    summary = runio.build_run_summary(
        run_id="run-1",
        command="bike simplified run",
        wall_time_s=1.25,
        solver={"method": "Radau"},
        tolerances={"v_eps": 0.01},
        metrics={"steady_speed": 8.3},
        seed=None,
    )
    run_dir = runio.make_run_dir(tmp_path, "summary-test")
    path = runio.write_summary(run_dir, summary)
    assert path.name == "summary.json"

    loaded = runio.read_summary(run_dir)
    assert loaded == summary


def test_summary_json_has_ordered_keys(tmp_path: Path) -> None:
    summary = runio.build_run_summary(run_id="run-1", command="cmd", wall_time_s=0.1, seed=None)
    run_dir = runio.make_run_dir(tmp_path, "order-test")
    path = runio.write_summary(run_dir, summary)

    import json

    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert list(payload.keys()) == list(runio._SUMMARY_KEY_ORDER)


def test_trajectory_npz_round_trip(tmp_path: Path) -> None:
    trajectory = make_synthetic_trajectory(n=6)
    run_dir = runio.make_run_dir(tmp_path, "trajectory-test")
    runio.write_trajectory(run_dir, trajectory)

    loaded = runio.read_trajectory(run_dir)
    loaded.validate()

    import numpy as np

    np.testing.assert_allclose(loaded.t, trajectory.t)
    np.testing.assert_allclose(loaded.v, trajectory.v)
    np.testing.assert_allclose(loaded.R_2a_x, trajectory.R_2a_x)
    np.testing.assert_allclose(loaded.margins.wheelie, trajectory.margins.wheelie)
    assert loaded.modes is None


def test_load_run_combines_summary_and_trajectory(tmp_path: Path) -> None:
    trajectory = make_synthetic_trajectory(n=4)
    summary = runio.build_run_summary(
        run_id="run-2", command="bike simplified run", wall_time_s=0.2, seed=1
    )
    run_dir = runio.make_run_dir(tmp_path, "load-run-test")
    runio.write_trajectory(run_dir, trajectory)
    runio.write_summary(run_dir, summary)

    loaded_trajectory, loaded_summary = runio.load_run(run_dir)
    loaded_trajectory.validate()
    assert loaded_summary == summary
