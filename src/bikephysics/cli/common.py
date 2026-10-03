"""cli/common.py -- shared CLI plumbing (architecture.md S9).

Resolves `--config` (preset name or path), applies `--set key.path=value`
overrides, and creates the run directory. T1 provides this plumbing
generically, against plain dicts/paths: `--config`'s preset lookup and
`--set`'s target schema both resolve against `core.params.BikeSpec` and the
shipped `bikephysics/data/` presets, neither of which exist before T2. The
functions here are written so T2 only needs to supply the concrete search
directories and the `BikeSpec` (de)serialization, not rewrite the override
logic.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from bikephysics.core.runio import make_run_dir


@dataclass(frozen=True)
class GlobalOptions:
    """The parsed global flags from `bike`'s root callback (architecture.md
    S9), threaded into every subcommand via `typer.Context.obj`."""

    config: str | None
    set_overrides: list[str] = field(default_factory=list)
    out: Path = Path("runs")
    no_plot: bool = False
    json_output: bool = False
    log_level: str = "INFO"
    seed: int | None = None


def parse_set_override(expr: str) -> tuple[str, str]:
    """Split a `--set key.path=value` expression into
    `(dotted_path, raw_value)`. Raises `ValueError` on a malformed
    expression (no `=`, or an empty key)."""
    if "=" not in expr:
        raise ValueError(f"--set expects key.path=value, got: {expr!r}")
    key, _, value = expr.partition("=")
    key = key.strip()
    if not key:
        raise ValueError(f"--set expects a non-empty key, got: {expr!r}")
    return key, value.strip()


def coerce_scalar(raw: str) -> bool | int | float | str:
    """Best-effort scalar coercion for a raw `--set` value string: bool,
    then int, then float, falling back to the original string."""
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        pass
    return raw


def _set_dotted(data: dict[str, Any], dotted_path: str, value: object) -> None:
    keys = dotted_path.split(".")
    node = data
    for key in keys[:-1]:
        child = node.setdefault(key, {})
        if not isinstance(child, dict):
            raise ValueError(f"cannot descend into non-dict value at {key!r} of {dotted_path!r}")
        node = child
    node[keys[-1]] = value


def apply_overrides(data: Mapping[str, Any], overrides: Iterable[str]) -> dict[str, Any]:
    """Apply a list of `key.path=value` expressions onto a deep copy of
    `data` and return the copy. This is the override-application half of
    `--set rear.c_2a_rr=0.008` (architecture.md S5.1); the full TOML
    loading + validation against `BikeSpec` lands in T2."""
    result: dict[str, Any] = copy.deepcopy(dict(data))
    for expr in overrides:
        path, raw_value = parse_set_override(expr)
        _set_dotted(result, path, coerce_scalar(raw_value))
    return result


def resolve_config_path(config: str | None, search_dirs: Iterable[Path]) -> Path | None:
    """Resolve `--config` to a concrete file path: a direct path if it
    exists, else the first `<dir>/<config>.toml` found in `search_dirs` (the
    preset lookup; T2 wires the actual preset directories under
    `bikephysics/data/`). Returns `None` if `config` is `None`. Raises
    `FileNotFoundError` if neither resolves."""
    if config is None:
        return None
    direct = Path(config)
    if direct.is_file():
        return direct
    search_dirs = list(search_dirs)
    for directory in search_dirs:
        candidate = directory / f"{config}.toml"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        f"no config file or preset named {config!r} found (searched {search_dirs!r})"
    )


def make_run_dir_for(options: GlobalOptions, slug: str) -> Path:
    """Create this command's run directory under `options.out`."""
    return make_run_dir(options.out, slug)
