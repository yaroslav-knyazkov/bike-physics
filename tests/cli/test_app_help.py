"""Tests for bikephysics.cli.app (architecture.md S9, task.md T1 done-when:
"uv run bike --help lists the five groups")."""

from __future__ import annotations

from typer.testing import CliRunner

from bikephysics.cli.app import app

runner = CliRunner()


def test_root_help_lists_the_five_subcommand_groups() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for group in ("simplified", "planar", "opt", "spec", "validate"):
        assert group in result.stdout


def test_root_help_lists_the_global_options() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for option in ("--config", "--set", "--out", "--no-plot", "--json", "--log-level", "--seed"):
        assert option in result.stdout


def test_no_args_shows_help() -> None:
    # `bike` has no command of its own, only subcommand groups, so Click
    # treats a bare invocation like `git`/`docker` with no subcommand: it
    # still prints the help text (`no_args_is_help=True`) but exits 2,
    # since no actual command ran. This is normal dispatcher-group
    # behavior, not an error condition.
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "simplified" in result.stdout


def test_each_subcommand_group_help_is_reachable() -> None:
    for group in ("simplified", "planar", "opt", "spec", "validate"):
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0, f"{group} --help failed: {result.stdout}"
