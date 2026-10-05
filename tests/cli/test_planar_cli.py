"""Tests for bikephysics.cli.planar_cmds (architecture.md S9's `bike
planar` block; task.md T7 item 10): one smoke test per command."""

from __future__ import annotations

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
            "planar", "run",
            "--config", "road_bike",
            "--pedal", "constant-torque",
            "--pedal-force", "60",
            "--v0", "6",
            "--t-end", "3",
            "--n-output", "30",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "result.npz").is_file()
    assert (run_dir / "trajectory.png").is_file()
    assert (run_dir / "planar_state.npz").is_file()
    assert (run_dir / "mode_log.npz").is_file()


def test_run_cmd_no_plot_skips_the_figure(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "planar", "run",
            "--config", "road_bike",
            "--pedal", "zero",
            "--v0", "6",
            "--t-end", "2",
            "--n-output", "20",
            "--no-plot",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert not (run_dir / "trajectory.png").exists()
    assert (run_dir / "result.npz").is_file()


def test_run_cmd_json_output(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "planar", "run",
            "--config", "road_bike",
            "--pedal", "zero",
            "--v0", "5",
            "--t-end", "1",
            "--n-output", "10",
            "--no-plot",
            "--json",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert '"run_dir"' in result.stdout
    assert '"final_v"' in result.stdout


def test_initcheck_cmd_reports_small_residuals(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        ["planar", "initcheck", "--config", "road_bike", "--v0", "5", "--out", str(out)],
    )
    assert result.exit_code == 0, result.stdout
    assert "position residual norm" in result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "summary.json").is_file()


def test_animate_cmd_from_config_writes_a_gif(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "planar", "animate",
            "--config", "road_bike",
            "--pedal", "zero",
            "--v0", "5",
            "--t-end", "1",
            "--n-output", "10",
            "--stride", "2",
            "--out", str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "animation.gif").is_file()


def test_animate_cmd_from_existing_run(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    run_result = runner.invoke(
        app,
        [
            "planar", "run",
            "--config", "road_bike",
            "--pedal", "zero",
            "--v0", "5",
            "--t-end", "1",
            "--n-output", "10",
            "--no-plot",
            "--out", str(out),
        ],
    )
    assert run_result.exit_code == 0, run_result.stdout
    run_dir = _run_dirs(out)[0]

    anim_result = runner.invoke(
        app,
        [
            "planar", "animate",
            "--run", str(run_dir),
            "--config", "road_bike",
            "--pedal", "zero",
        ],
    )
    assert anim_result.exit_code == 0, anim_result.stdout
    assert (run_dir / "animation.gif").is_file()


def test_crosscheck_cmd_passes_for_road_bike(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app, ["planar", "crosscheck", "--config", "road_bike", "--out", str(out)]
    )
    assert result.exit_code == 0, result.stdout
    assert "ALL OK" in result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "summary.json").is_file()


def test_modes_cmd_on_a_run_with_no_switches(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    run_result = runner.invoke(
        app,
        [
            "planar", "run",
            "--config", "road_bike",
            "--pedal", "constant-torque",
            "--pedal-force", "60",
            "--v0", "6",
            "--t-end", "1",
            "--n-output", "10",
            "--no-plot",
            "--out", str(out),
        ],
    )
    assert run_result.exit_code == 0, run_result.stdout
    run_dir = _run_dirs(out)[0]

    modes_result = runner.invoke(app, ["planar", "modes", "--run", str(run_dir)])
    assert modes_result.exit_code == 0, modes_result.stdout
