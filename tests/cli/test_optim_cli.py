"""Tests for bikephysics.cli.optim_cmds (architecture.md S9's `bike opt`
block). Uses the shipped `oracle_constant_power` deck (1 decision
variable) wherever runtime matters, and `infeasible_steep_climb` for the
ticket's "fails with a clear diagnostic, not a crash" acceptance test.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bikephysics.cli.app import app

runner = CliRunner()


def _run_dirs(out: Path) -> list[Path]:
    return sorted(p for p in out.iterdir() if p.is_dir())


def test_solve_cmd_writes_a_complete_run_dir(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "opt",
            "solve",
            "--problem",
            "oracle_constant_power",
            "--solver",
            "scipy-local",
            "--multistart",
            "1",
            "--maxiter",
            "80",
            "--seed",
            "0",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "result.npz").is_file()
    assert (run_dir / "trajectory.png").is_file()
    assert (run_dir / "report.txt").is_file()


def test_solve_cmd_json_output_is_valid_json(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "opt",
            "solve",
            "--problem",
            "oracle_constant_power",
            "--solver",
            "analytic",
            "--out",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["success"] is True
    assert payload["objective_value"] > 0.0


def test_solve_cmd_no_plot_skips_the_figure(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "opt",
            "solve",
            "--problem",
            "oracle_constant_power",
            "--solver",
            "analytic",
            "--out",
            str(out),
            "--no-plot",
        ],
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert not (run_dir / "trajectory.png").exists()


def test_baseline_cmd_writes_a_run_dir(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app, ["opt", "baseline", "--problem", "oracle_constant_power", "--out", str(out)]
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "summary.json").is_file()
    assert (run_dir / "result.npz").is_file()


def test_compare_cmd_overlays_analytic_and_scipy_local(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "opt",
            "compare",
            "--problem",
            "oracle_constant_power",
            "--solvers",
            "analytic,scipy-local",
            "--multistart",
            "1",
            "--maxiter",
            "80",
            "--seed",
            "0",
            "--out",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout
    run_dir = _run_dirs(out)[0]
    assert (run_dir / "comparison.png").is_file()
    assert (run_dir / "comparison.json").is_file()
    payload = json.loads(result.stdout)
    assert "analytic" in payload
    assert "scipy-local" in payload


def test_solve_cmd_on_an_infeasible_problem_reports_failure_not_a_crash(tmp_path: Path) -> None:
    """Acceptance test (task.md T8): a power ceiling below what the grade
    physically requires fails with a clear diagnostic, not a crash."""
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "opt",
            "solve",
            "--problem",
            "infeasible_steep_climb",
            "--solver",
            "scipy-local",
            "--multistart",
            "1",
            "--maxiter",
            "50",
            "--seed",
            "0",
            "--out",
            str(out),
            "--json",
        ],
    )
    assert result.exit_code == 0, result.stdout  # a clear diagnostic, not a crash
    payload = json.loads(result.stdout)
    assert payload["success"] is False


def test_unknown_solver_name_is_a_clean_cli_error(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app,
        [
            "opt",
            "solve",
            "--problem",
            "oracle_constant_power",
            "--solver",
            "nonexistent",
            "--out",
            str(out),
        ],
    )
    assert result.exit_code != 0


def test_unknown_problem_name_is_a_clean_cli_error(tmp_path: Path) -> None:
    out = tmp_path / "runs"
    result = runner.invoke(
        app, ["opt", "solve", "--problem", "no_such_problem_deck", "--out", str(out)]
    )
    assert result.exit_code != 0
