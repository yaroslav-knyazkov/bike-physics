"""cli/spec_cmds.py -- `bike spec show|presets|validate` (architecture.md
S9's `bike spec` block).

Registers its three commands directly onto `cli.app.spec_app` (imported
for its side effect by `cli.app`, which otherwise only creates the empty
sub-apps per T1). Every command here is read-only/diagnostic -- none of
them write a `runs/` directory (architecture.md S5.6: that machinery is
for commands that *compute* something; inspecting a config does not).

`--config`/`--set` are redeclared locally on each command (rather than
read from the root callback's `GlobalOptions` via `ctx.obj`) so that
architecture.md S9's own usage strings -- `bike spec validate
--config path/to/my.toml`, options *after* the subcommand name -- work as
written: Click/Typer only accepts a parent group's options *before* the
subcommand token, so a root-level-only `--config` could never satisfy
that usage string.
"""

from __future__ import annotations

from typing import Annotated

import typer
from pydantic import ValidationError

from bikephysics.cli.app import spec_app
from bikephysics.cli.common import apply_overrides, resolve_config_path
from bikephysics.core.params import (
    BIKE_PRESET_DIR,
    BikeSpec,
    bike_spec_from_dict,
    list_bike_presets,
    read_toml,
)
from bikephysics.core.road import list_road_presets

_ConfigOption = Annotated[
    str | None,
    typer.Option("--config", help="Preset name or path to a TOML bike spec."),
]
_SetOption = Annotated[
    list[str],
    typer.Option("--set", help="Dotted-path override, e.g. rear.c_2a_rr=0.008. Repeatable."),
]


def load_bike_spec(config: str | None, overrides: list[str]) -> BikeSpec:
    """Resolve `--config` (preset name or path, searched in
    `BIKE_PRESET_DIR`), apply `--set` overrides, and validate into a
    `BikeSpec`. The glue `core.params`'s own module docstring says belongs
    in `cli` (core must not import `cli`)."""
    path = resolve_config_path(config, [BIKE_PRESET_DIR])
    data: dict[str, object] = {}
    if path is not None:
        data = read_toml(path)
    data = apply_overrides(data, overrides)
    return bike_spec_from_dict(data)


def _iter_symbol_rows(spec: BikeSpec) -> list[tuple[str, str, str]]:
    """`(dotted_field_path, latex_symbol_description, formatted_value)`
    for every leaf field in `spec`, introspected generically from the
    pydantic model tree so this table can never drift from `BikeSpec`'s
    actual fields."""
    rows: list[tuple[str, str, str]] = []
    for section_name, section_field in type(spec).model_fields.items():
        section_model = getattr(section_field.annotation, "model_fields", None)
        if section_model is None:
            continue
        section_value = getattr(spec, section_name)
        for field_name, field_info in section_model.items():
            value = getattr(section_value, field_name)
            description = field_info.description or ""
            rows.append((f"{section_name}.{field_name}", description, repr(value)))
    return rows


@spec_app.command("show")
def show_cmd(config: _ConfigOption = None, set_: _SetOption = []) -> None:  # noqa: B006
    """Print the symbol<->field table and derived quantities for a bike
    spec (architecture.md S9)."""
    spec = load_bike_spec(config, list(set_))
    rows = _iter_symbol_rows(spec)
    path_width = max((len(r[0]) for r in rows), default=0)
    desc_width = max((len(r[1]) for r in rows), default=0)
    for path, description, value in rows:
        typer.echo(f"{path:<{path_width}}  {description:<{desc_width}}  = {value}")
    typer.echo("")
    typer.echo(f"M          = {spec.M!r}  kg  (total mass)")
    typer.echo(f"M_eq       = {spec.m_eq!r}  kg  (effective mass, forward_motion.tex)")
    typer.echo(f"G          = {spec.gear_ratio!r}  (gear_ratio = r_3b_cr / r_2a_cg)")
    typer.echo(
        "static load split = placeholder "
        "(the canonical formula is simplified/model.py's normal_loads, T3)"
    )


@spec_app.command("presets")
def presets_cmd() -> None:
    """List every shipped bike/road/problem preset (architecture.md S9)."""
    typer.echo("bikes:")
    for name in list_bike_presets():
        typer.echo(f"  {name}")
    typer.echo("roads:")
    for name in list_road_presets():
        typer.echo(f"  {name}")
    typer.echo("problems:")
    typer.echo("  (none yet -- optimization problems ship starting T8)")


@spec_app.command("validate")
def validate_cmd(config: _ConfigOption = None, set_: _SetOption = []) -> None:  # noqa: B006
    """Validate a bike spec, exiting non-zero and printing every pydantic
    validation error if it is invalid (architecture.md S9)."""
    if config is None:
        typer.echo("error: --config is required", err=True)
        raise typer.Exit(code=2)
    path = resolve_config_path(config, [BIKE_PRESET_DIR])
    data: dict[str, object] = read_toml(path) if path is not None else {}
    data = apply_overrides(data, list(set_))
    try:
        bike_spec_from_dict(data)
    except ValidationError as exc:
        typer.echo(f"INVALID: {config}", err=True)
        for error in exc.errors():
            loc = ".".join(str(part) for part in error["loc"])
            typer.echo(f"  {loc}: {error['msg']}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"OK: {config} is a valid bike spec")
