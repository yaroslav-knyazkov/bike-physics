"""Tests for bikephysics.cli.simplified_cmds (architecture.md S9's `bike
simplified` block, task.md T4 done-when: "all five commands have a CLI
smoke test")."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bikephysics.cli.app import app

runner = CliRunner()


def _run_dirs(out: Path) -> list[Path]:
    return sorted(p for p in out.iterdir() if p.is_dir())


def test_run_cmd_writes_a_complete_run_dir(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "simplified",
            "run",
            "--config",
            "road_bike",
            "--out",
            str(out),
            "--t-end",
            "20",
            "--n-output",
            "100",
        ],
    )
    assert result.exit_code == 0, result.stdout
    dirs = _run_dirs(out)
    assert len(dirs) == 1
    run_dir = dirs[0]
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "result.npz").is_file()
    assert (run_dir / "trajectory.png").is_file()


def test_run_cmd_no_plot_skips_the_figure(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "simplified",
            "run",
            "--config",
            "road_bike",
            "--out",
            str(out),
            "--t-end",
            "5",
            "--n-output",
            "20",
            "--no-plot",
        ],
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert not (run_dir / "trajectory.png").exists()
    assert (run_dir / "result.npz").is_file()


def test_run_cmd_json_output_is_valid_json(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "simplified",
            "run",
            "--config",
            "road_bike",
            "--out",
            str(out),
            "--t-end",
            "60",
            "--n-output",
            "2000",
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert "final_v" in payload
    assert "energy_closure_error_fraction" in payload
    # task.md T4 done-when: the energy audit closes to < 0.1% at the
    # architecture.md S12.1 target sample density (2000 output points).
    assert payload["energy_closure_error_fraction"] < 1e-3


def test_steady_cmd_writes_a_run_dir_and_prints_a_speed(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "simplified",
            "steady",
            "--config",
            "road_bike",
            "--out",
            str(out),
            "--power",
            "200",
            "--grade",
            "0.02",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "steady speed" in result.stdout
    assert (_run_dirs(out)[0] / "summary.json").is_file()


def test_steady_cmd_sweep_grade_writes_a_sweep_table(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "simplified",
            "steady",
            "--config",
            "road_bike",
            "--out",
            str(out),
            "--power",
            "200",
            "--sweep-grade",
            "0:0.08:5",
        ],
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "sweep.npz").is_file()
    assert (run_dir / "sweep.png").is_file()


def test_sweep_cmd_sweeps_a_bike_spec_field(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "simplified",
            "sweep",
            "--config",
            "road_bike",
            "--out",
            str(out),
            "--param",
            "frame.m_1",
            "--range",
            "55",
            "70",
            "--n",
            "5",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "frame.m_1=" in result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "sweep.npz").is_file()


def test_sweep_cmd_requires_param(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(app, ["simplified", "sweep", "--config", "road_bike", "--out", str(out)])
    assert result.exit_code != 0


def test_limits_cmd_prints_wheelie_stoppie_traction_table(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        ["simplified", "limits", "--config", "road_bike", "--out", str(out), "--grade", "0.0"],
    )
    assert result.exit_code == 0, result.stdout
    assert "wheelie accel" in result.stdout
    assert "stoppie accel" in result.stdout
    assert "traction accel limit" in result.stdout
    assert (_run_dirs(out)[0] / "summary.json").is_file()


def test_sensitivity_cmd_prints_elasticities_and_identity_check(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "simplified",
            "sensitivity",
            "--config",
            "road_bike",
            "--out",
            str(out),
            "--speed",
            "8",
            "--grade",
            "0.03",
            "--power",
            "200",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert "E_M" in result.stdout
    assert "identity" in result.stdout
    assert "dominance ratio" in result.stdout
    assert "size elasticity" in result.stdout
    assert (_run_dirs(out)[0] / "summary.json").is_file()
