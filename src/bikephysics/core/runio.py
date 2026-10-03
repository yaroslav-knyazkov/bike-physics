"""core/runio.py -- run-directory artifacts and provenance (architecture.md
S5.6, S12.2).

Every command that computes something writes `runs/<UTC-ISO>-<slug>/`
containing `config.resolved.toml`, `result.npz`, `summary.json`, optional
`*.png` figures, and `log.txt`. This module owns the lifecycle of that
directory for the two machine-written artifacts: creating the directory,
serializing a `Trajectory`'s time series to `.npz`, building and writing a
`RunSummary` to `summary.json` with the full provenance block, and reading
both back (`load_run`) for `bike validate` and regression tests.

T1 scope note: `config.resolved.toml` and `log.txt` are not written here --
there is no `BikeSpec`/TOML config to resolve until T2, and no CLI command
producing log output yet. `write_trajectory`/`read_trajectory` persist only
the array fields of `Trajectory` that exist independently of a `BikeSpec`
(`spec` and `meta` are supplied by the caller on read, and `modes` is not
round-tripped since no tier populates it yet).
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

import numpy as np

from bikephysics.core.results import LimitMargins, RunSummary, Trajectory

_PACKAGE_NAME = "bikephysics"

#: Fixed key order for `summary.json` (architecture.md S5.6: "this file ...
#: must be float-stable and key-ordered").
_SUMMARY_KEY_ORDER = (
    "run_id",
    "created_utc",
    "package_version",
    "git_sha",
    "git_dirty",
    "numpy_version",
    "scipy_version",
    "casadi_version",
    "wall_time_s",
    "command",
    "seed",
    "solver",
    "tolerances",
    "metrics",
)


def _package_version() -> str:
    try:
        return importlib_metadata.version(_PACKAGE_NAME)
    except importlib_metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _optional_version(name: str) -> str | None:
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return None


def _git_sha_and_dirty(repo_root: Path | None = None) -> tuple[str, bool]:
    """Best-effort git provenance. Never raises: a run must still be
    writable outside a git checkout (e.g. from an installed sdist)."""
    root = repo_root or Path(__file__).resolve().parents[3]
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=root, text=True, stderr=subprocess.DEVNULL
        )
        return sha, bool(status.strip())
    except Exception:
        return "unknown", False


def make_run_dir(base: Path, slug: str) -> Path:
    """Create and return `base/<UTC-ISO>-<slug>/`. `base` is created if
    missing; the run directory itself must not already exist."""
    base.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = base / f"{stamp}-{slug}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def build_run_summary(
    *,
    run_id: str,
    command: str,
    wall_time_s: float,
    solver: dict[str, Any] | None = None,
    tolerances: dict[str, float] | None = None,
    metrics: dict[str, float] | None = None,
    seed: int | None = None,
    repo_root: Path | None = None,
) -> RunSummary:
    """Assemble a `RunSummary` with the full provenance block of
    architecture.md S12.2: package version, git SHA + dirty flag, and
    numpy/scipy/casadi versions, alongside the caller-supplied solver
    settings, regularization tolerances, and scalar metrics."""
    sha, dirty = _git_sha_and_dirty(repo_root)
    return RunSummary(
        run_id=run_id,
        created_utc=datetime.now(UTC).isoformat(),
        package_version=_package_version(),
        git_sha=sha,
        git_dirty=dirty,
        numpy_version=np.__version__,
        scipy_version=_optional_version("scipy"),
        casadi_version=_optional_version("casadi"),
        wall_time_s=wall_time_s,
        command=command,
        seed=seed,
        solver=dict(solver or {}),
        tolerances=dict(tolerances or {}),
        metrics=dict(metrics or {}),
    )


def write_summary(run_dir: Path, summary: RunSummary) -> Path:
    """Write `summary.json` with the fixed, ordered key set."""
    payload = asdict(summary)
    ordered = {key: payload[key] for key in _SUMMARY_KEY_ORDER}
    path = run_dir / "summary.json"
    path.write_text(json.dumps(ordered, indent=2) + "\n", encoding="utf-8")
    return path


def read_summary(run_dir: Path) -> RunSummary:
    """Read `summary.json` back into a `RunSummary`."""
    payload = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    return RunSummary(**payload)


#: `Trajectory` array fields stored flat in `result.npz` (architecture.md
#: S5.5/S5.6). Keep in sync with `Trajectory`'s field list. The four ground
#: reactions (`R_2a_x, R_2a_z, R_2b_x, R_2b_z`) are flat fields directly on
#: `Trajectory`, per S5.5 -- there is no nested `Reactions` type.
_TRAJECTORY_ARRAY_FIELDS = (
    "t",
    "x",
    "z",
    "v",
    "a",
    "pitch",
    "pitch_rate",
    "omega_2a",
    "omega_2b",
    "omega_3b",
    "T",
    "R_2a_x",
    "R_2a_z",
    "R_2b_x",
    "R_2b_z",
    "power_pedal",
    "power_ground",
    "power_aero",
    "power_rr",
    "power_brake",
    "power_gravity",
)
_MARGIN_FIELDS = ("traction_rear", "traction_front", "wheelie", "stoppie", "freewheel")


def _trajectory_arrays(trajectory: Trajectory) -> dict[str, np.ndarray]:
    arrays: dict[str, np.ndarray] = {
        name: np.asarray(getattr(trajectory, name)) for name in _TRAJECTORY_ARRAY_FIELDS
    }
    for name in _MARGIN_FIELDS:
        arrays[f"margins_{name}"] = np.asarray(getattr(trajectory.margins, name))
    return arrays


def write_trajectory(run_dir: Path, trajectory: Trajectory) -> Path:
    """Write the time-series arrays of a `Trajectory` to `result.npz`.
    `spec`, `meta`, and `modes` are not array data; `meta` is expected to be
    reconstructed from `summary.json`'s provenance block, `spec` is
    supplied by the caller on read, and `modes` round-trips once a planar
    run populates it (T5+)."""
    arrays = _trajectory_arrays(trajectory)
    path = run_dir / "result.npz"
    # numpy's stubs resolve **dict against an overload that expects the
    # `allow_pickle: bool` keyword positionally when unpacking a dict of
    # unknown-length; this is a typeshed limitation, not a real type error.
    np.savez(path, **arrays)  # type: ignore[arg-type]
    return path


def read_trajectory(
    run_dir: Path, *, spec: Any = None, meta: dict[str, Any] | None = None
) -> Trajectory:
    """Read `result.npz` back into a `Trajectory`. `spec`/`meta` are not
    persisted by this ticket's `.npz` schema and default to `None`/`{}`."""
    with np.load(run_dir / "result.npz") as data:
        values = {name: data[name] for name in _TRAJECTORY_ARRAY_FIELDS}
        margins = LimitMargins(**{name: data[f"margins_{name}"] for name in _MARGIN_FIELDS})
    return Trajectory(
        **values,
        margins=margins,
        modes=None,
        spec=spec,
        meta=meta or {},
    )


def load_run(run_dir: Path) -> tuple[Trajectory, RunSummary]:
    """Round-trip helper: read both `result.npz` and `summary.json` for an
    existing run directory."""
    summary = read_summary(run_dir)
    trajectory = read_trajectory(run_dir)
    return trajectory, summary
