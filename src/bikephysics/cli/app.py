"""cli/app.py -- the `bike` root Typer app (architecture.md S9).

One console script (`bike = "bikephysics.cli.app:main"` in `pyproject.toml`)
with five subcommand groups -- `simplified`, `planar`, `opt`, `spec`,
`validate` -- sharing one set of global options (`--config`, `--set`,
`--out`, `--no-plot`, `--json`, `--log-level`, `--seed`). T1 wires the
skeleton only: no physics commands exist yet. Each group gains commands in
its own ticket: `spec` in T2, `simplified` in T3/T4, `planar` in T5-T7,
`opt` in T8-T10, `validate` in T13.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from bikephysics.cli.common import GlobalOptions

app = typer.Typer(
    name="bike",
    help="Bike longitudinal/planar dynamics: simulate, analyze, optimize.",
    no_args_is_help=True,
)

simplified_app = typer.Typer(help="Single-DOF longitudinal tier (architecture.md S6).")
planar_app = typer.Typer(help="Planar 2D multibody tier (architecture.md S7).")
opt_app = typer.Typer(help="Control/trajectory optimization (architecture.md S8).")
spec_app = typer.Typer(help="Inspect, list, and validate bike/road/problem specs.")
validate_app = typer.Typer(help="Cross-tier, analytic, and golden regression checks.")

app.add_typer(simplified_app, name="simplified")
app.add_typer(planar_app, name="planar")
app.add_typer(opt_app, name="opt")
app.add_typer(spec_app, name="spec")
app.add_typer(validate_app, name="validate")

# Each subcommand module decorates its commands directly onto the sub-app
# instances above (`spec_app.command(...)`, etc.), so it must be imported
# for that side effect -- after the sub-apps exist, hence not at the top of
# the file (bikephysics.cli.spec_cmds imports `spec_app` back from this
# module). `spec` lands in T2; `simplified`/`planar`/`opt`/`validate` gain
# the same kind of import in their own tickets.
from bikephysics.cli import optim_cmds, simplified_cmds, spec_cmds  # noqa: E402,F401


@app.callback()
def global_options(
    ctx: typer.Context,
    config: Annotated[
        str | None,
        typer.Option("--config", help="Preset name or path to a TOML bike/road/problem spec."),
    ] = None,
    set_: Annotated[
        list[str],
        typer.Option("--set", help="Dotted-path override, e.g. rear.c_2a_rr=0.008. Repeatable."),
    ] = [],  # noqa: B006 - Typer reads this as the option's default, not a mutated default.
    out: Annotated[Path, typer.Option("--out", help="Run output directory.")] = Path("runs"),
    no_plot: Annotated[
        bool, typer.Option("--no-plot", help="Skip writing figures for this run.")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Print a machine-readable summary to stdout.")
    ] = False,
    log_level: Annotated[
        str, typer.Option("--log-level", help="Python logging level name.")
    ] = "INFO",
    seed: Annotated[
        int | None, typer.Option("--seed", help="Seed threaded into every stochastic solver.")
    ] = None,
) -> None:
    """Global options shared by every subcommand (architecture.md S9).
    Config-file-first with CLI overrides, never the other way round."""
    ctx.obj = GlobalOptions(
        config=config,
        set_overrides=list(set_),
        out=out,
        no_plot=no_plot,
        json_output=json_output,
        log_level=log_level,
        seed=seed,
    )


def main() -> None:
    """Console-script entry point (`pyproject.toml`: `bike = "bikephysics.cli.app:main"`)."""
    app()


if __name__ == "__main__":
    main()
