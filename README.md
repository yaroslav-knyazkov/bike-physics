# bike-physics

Bike longitudinal and planar dynamics: simulation, analysis, and trajectory optimization. The physics
is derived in `latex/` (see `latex/main.tex` for the full 2D multibody derivation and
`latex/simplified/forward_motion.tex` for the single-DOF reduction); the implementation plan and
design rationale live in `architecture.md` and `task.md`.

This package is being built as three applications sharing one `core`:

1. **Simplified** -- the single-DOF longitudinal model: fast rollouts, closed-form steady-state checks,
   sensitivity/elasticity analysis.
2. **Planar (2D)** -- the full rigid-multibody model (frame + two wheels + crankset + chain), with
   regularized ground contact and a stiff integrator.
3. **Optimization** -- control/trajectory optimization (min-time, max-distance, min-energy) built on
   top of whichever tier's `ForwardModel` adapter is cheapest for the question being asked.

All three are reachable through one console script, `bike`, with five subcommand groups:
`simplified`, `planar`, `opt`, `spec`, `validate`.

## Status

As of ticket **T1** (this commit), only the repository scaffolding and the shared `core` skeleton
exist: `core/units.py`, `core/mathx.py`, `core/results.py`, `core/runio.py`, `core/plotting.py`, and
the `bike` CLI's five *empty* subcommand groups. **None of the quickstart commands below work yet** --
they are the target shape of each app once its tickets land (Simplified: T2-T4; Planar: T2, T5-T7;
Optimization: T2, T4, T8-T10). Until then, `bike --help` and `bike <group> --help` are the only
commands that do anything.

## Install

This project uses [`uv`](https://docs.astral.sh/uv/) for environment and dependency management.

```bash
uv sync            # core apps: numpy, scipy, matplotlib, pydantic, typer, tomli-w
uv sync --extra opt  # also installs casadi, for the T9+ optimization milestone
uv run bike --help
```

## Quickstart (target shape -- see "Status" above)

```bash
# Simplified: roll out a 120 s run on the road-bike preset and a 5% grade.
uv run bike simplified run --config road_bike --road grade_5pct --pedal von-mises --t-end 120

# Planar: roll out the full 2D model on a rolling-sine road with regularized contact.
uv run bike planar run --config road_bike --road rolling_sine --contact regularized --t-end 30

# Optimization: solve a minimum-time 1 km sprint against the Simplified tier.
uv run bike opt solve --problem min_time_1km --solver scipy-local
```

Every run writes its resolved config, full solver/regularization provenance, and output arrays to
`runs/<UTC-timestamp>-<slug>/` (`summary.json` + `result.npz`); see `architecture.md` S5.6/S12.2.

## Where things live

- `architecture.md` -- the full design: chosen stack, repository layout, module-by-module design,
  CLI surface, decision log, non-functional decisions.
- `task.md` -- the ordered implementation tickets this codebase is being built against.
- `latex/` -- the physics derivation (unchanged by the Python rewrite).
- `src/bikephysics/core/` -- shared, tier-agnostic code (units, backend-neutral math, parameter
  model, results schema, run I/O, plotting). Depends on nothing else in this package.
- `src/bikephysics/simplified/`, `src/bikephysics/planar/`, `src/bikephysics/optim/` -- the three
  tiers, each depending only on `core` (and, for `optim`, the other two tiers through an adapter).
- `src/bikephysics/cli/` -- the `bike` command-line app.
- `tests/` -- unit, analytic, cross-tier, and CLI tests.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run mypy
```
