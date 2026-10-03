# Architecture — bike-physics simulation, 2D multibody engine, and optimization

## Summary

This document designs three runnable Python applications on one shared core, implementing the physics
already derived in `latex/`: (1) a **Simplified** closed-form longitudinal simulator rebuilt against the
current `latex/simplified/forward_motion.tex`, (2) a **planar (2D) multibody simulator** for
Frame+Rider / rear wheel / front wheel / lumped drivetrain with unilateral ground contact, and (3) an
**optimization** application that treats either simulator as an interchangeable forward model.

Key decisions proposed (all require user sign-off — see §10):
- One monorepo, one installable package `bikephysics` under `src/`, with subpackages `core`,
  `simplified`, `planar`, `optim`, `cli`, `validation`. Existing `bikephysics/` is **migrated in place**
  (only `plotting.py` survives in substance); `main.py` is retired into `examples/`.
- 2D engine in **maximal coordinates** (3 DOF per body) with constraints as **Lagrange multipliers** —
  a one-to-one map onto the LaTeX free-body equations, so adding a body/force/constraint never touches
  the solver core.
- Ground contact: **regularized (penalty + Stribeck friction) contact as the default**, with an
  event-driven exact-mode model and a complementarity model behind the same interface.
- Lumped 2D drivetrain: **crankset as a real rigid body `3b` + revolute joint at the bottom bracket +
  one scalar "ideal chain" constraint whose Lagrange multiplier *is* the chain tension `T`**, with the
  freewheel as the unilateral condition `T >= 0`. Provably reduces to the boxed Simplified chain-tension
  and `M_eq` equations — that reduction is the acceptance test.
- Optimization milestone 1: control **parametrization + scipy** (template shape fitting and piecewise-constant
  shooting) against the Simplified model; milestone 2: **CasADi direct collocation**; milestone 3: CasADi
  through the planar model.
- Stack: `uv` + `pyproject.toml`, pydantic v2 for config, TOML config files, Typer for one `bike` CLI with
  subcommand groups, pytest + ruff + mypy, matplotlib.

Nothing here changes the physics in `latex/`; §11 lists inconsistencies found in the spec for the user to
rule on separately.

---

## Table of contents

1. [Scope, constraints, and non-goals](#1-scope-constraints-and-non-goals)
2. [Chosen stack](#2-chosen-stack)
3. [Repository and package layout](#3-repository-and-package-layout)
4. [Components and relations](#4-components-and-relations)
5. [Shared core design](#5-shared-core-design)
6. [Simplified tier design](#6-simplified-tier-design)
7. [Planar (2D) engine design](#7-planar-2d-engine-design)
   - 7.1 [Conventions](#71-conventions)
   - 7.2 [Bodies, state, and degrees of freedom](#72-bodies-state-and-degrees-of-freedom)
   - 7.3 [Force elements](#73-force-elements)
   - 7.4 [Constraints](#74-constraints)
   - 7.5 [Lumped drivetrain model — full equations](#75-lumped-drivetrain-model--full-equations)
   - 7.6 [Ground contact](#76-ground-contact)
   - 7.7 [Solver and integration strategy](#77-solver-and-integration-strategy)
   - 7.8 [Initial conditions](#78-initial-conditions)
8. [Optimization software design](#8-optimization-software-design)
9. [CLI design](#9-cli-design)
10. [Decision points requiring sign-off](#10-decision-points-requiring-sign-off)
11. [Findings in the LaTeX spec](#11-findings-in-the-latex-spec)
12. [Non-functional decisions](#12-non-functional-decisions)
13. [Extension seams vs. the refinements list](#13-extension-seams-vs-the-refinements-list)
14. [Rejected options](#14-rejected-options)
15. [Open risks](#15-open-risks)

---

## 1. Scope, constraints, and non-goals

### 1.1 What the requirements are

`latex/` is the requirements specification. The authoritative slices:

| Spec slice | What it fixes |
|---|---|
| `latex/simplified/forward_motion.tex` | Complete closed-form Simplified tier: boxed `T`, `R_x`, `R_z`, `M_eq` EOM, steady state, elasticities, wheelie/stoppie/traction limits |
| `latex/2d_models/rigidbody.tex` | Per-body planar template: `sum F = m a`, `sum M(a) = I alpha`, moment transport |
| `latex/2d_models/forces.tex` | Force catalogue: `W`, `A`, `R`, `P`, `Q` |
| `latex/2d_models/models.tex` | Functional forms: gravity, aero coefficients, atmosphere, pedal-force templates, bearing friction |
| `latex/2d_systems/{overview,frame,wheel}.tex` | Global assumptions; free-body equations for bodies `1`, `2a`, `2b` |
| `latex/2d_assembly/frame_wheels.tex` | Revolute axle joints, `theta_2a = theta_1 + phi_2a` |
| `latex/2d_assembly/wheels_ground.tex` | Road `z(x)`, frame `0`, foot-of-normal contact, rolling-resistance offset, unilateral contact + stick/slip |
| `latex/appendix/notation.tex` | Symbol grammar — code identifiers must be derivable from it |
| `latex/appendix/atmosphere.tex` | ISA, humidity correction, Sutherland |
| `latex/appendix/refinements.tex` | The explicit future-work menu that §13 maps to extension seams |
| `latex/timeline.tex` | Track priorities; 2D build order; Optimization is the primary goal |

**Gap in the spec:** `main.tex` has an empty `\subsection{Drivetrain}` under 2D-Systems, and 2D-Assembly
has no section reducing the system to a solvable set. The drivetrain model in §7.5 of this document is
therefore **new modeling work** and is a decision point (§10, D-10), not received physics. It is written
in the LaTeX symbol conventions so it can be ported back verbatim (ticket T12).

### 1.2 Constraints

- Solo research project, one user, local Windows machine. No multi-user, no server, no auth, no DB.
- Python 3.12 present; `uv 0.12.17` present; numpy 2.5.3 / scipy 1.18.1 / matplotlib 3.11.2 / pytest 9.1.1
  installed. casadi, pydantic, typer, ruff, mypy, rich **not** installed yet.
- Zero paid services. Every dependency proposed is free and permissively licensed (see §12.5).
- The documentation (`latex/`) stays the source of truth; code never silently diverges from it.

### 1.3 Non-goals (explicitly out of scope now)

- 3D / spatial dynamics (Timeline tier 3).
- Timeline tracks (3) R&D tool and (4) Data acquisition. Not designed for, but not blocked:
  `core/params.py` and the run-artifact format are the two seams those tracks will attach to.
- The "Strategy" half of Timeline track (2) (energy model, drafting, race outcomes). One stub hook only:
  `optim/constraints.py::PowerCeiling` accepts a callable `P_max(cadence)` so a rider power curve can be
  dropped in later.
- Any item from `latex/appendix/refinements.tex`. §13 shows where each one *would* plug in; none is built.
- Turning / lateral dynamics. `latex/main.tex` keeps turning Simplified-only and it has no content yet.

---

## 2. Chosen stack

Every row here is a decision point with options in §10; this table is the recommendation.

| Concern | Choice | Rationale (short) |
|---|---|---|
| Language / runtime | Python 3.12 | Already installed; matches existing code; scipy/CasADi ecosystem |
| Env + packaging | `uv` + `pyproject.toml` (PEP 621), `uv.lock` committed | `uv` already on the machine; lockfile gives reproducible numeric results, which matters for a physics regression suite |
| Numerics | numpy + scipy | Already in use; `solve_ivp` (Radau/BDF) covers the stiff contact; `optimize` covers milestone-1 optimization |
| Config model | pydantic v2 | File loading + validation + bounds (`m > 0`, `mu_k <= mu_s`) + JSON-schema export for later tracks, in one place |
| Config format | TOML (`tomllib` read, `tomli-w` write) | stdlib read, comment-friendly parameter decks, no YAML ambiguity footguns |
| CLI | Typer | Type-hint-driven, native subcommand groups, pairs cleanly with pydantic models |
| CLI topology | one console script `bike` with subcommand groups | Shared global flags (`--config`, `--out`, `--json`); discoverable; one place for run-artifact plumbing |
| Autodiff / NLP | CasADi + IPOPT (milestone 2+) | Direct collocation with exact derivatives and a real constrained NLP solver; ships prebuilt Windows wheels with IPOPT bundled |
| Plotting | matplotlib (+ `matplotlib.animation` for the 2D replay) | Already in use; publication-grade static output next to the LaTeX |
| Results on disk | `.npz` time series + `summary.json` manifest per run dir | Zero extra deps, exact float round-trip, trivially diffable summaries for regression tests |
| Tests | pytest (+ `pytest-regressions`-style golden JSON, hand-rolled) | Analytic cross-checks and golden regressions are the whole validation strategy |
| Lint / types | ruff (lint + format) + mypy | Catches unit/sign mix-ups early; this codebase is almost entirely numeric glue where a transposed index is silent |
| Console output | rich (via Typer's dependency) | Tables for limit/sensitivity reports |

**Dependency budget:** 6 runtime deps (numpy, scipy, matplotlib, pydantic, typer, tomli-w) + 1 optional
extra (`casadi`, under `[project.optional-dependencies] opt`) + 3 dev deps (pytest, ruff, mypy).

> **Version-verification task for the developer (T1):** Context7 was not reachable from the architecture
> session, so no version-specific API facts were cached here. Before writing code, confirm against current
> docs: (a) `scipy.integrate.solve_ivp` Radau `jac_sparsity`/`events` signature in scipy 1.18;
> (b) pydantic v2 `model_config = ConfigDict(frozen=True)` + `model_validate` usage;
> (c) Typer's current `typer.Typer(no_args_is_help=True)` sub-app composition API;
> (d) CasADi's current `rootfinder` / `integrator("idas"|"cvodes")` / `nlpsol("ipopt")` signatures and
> whether a Windows wheel for Python 3.12 exists. Record the findings in `architecture-notes.md` next to
> this file so later tickets do not re-research them.

---

## 3. Repository and package layout

```
bike-physics/
├── pyproject.toml                  # PEP 621 metadata, deps, ruff/mypy/pytest config
├── uv.lock
├── README.md                       # rewritten: three apps, quickstart per app
├── architecture.md                 # this file
├── architecture-notes.md           # cached library-version facts (T1 fills it)
├── task.md                         # implementation tickets
├── .gitignore                      # + runs/, .venv/, *.npz
├── latex/                          # UNCHANGED, except T12 adds 2d_systems/drivetrain.tex
├── src/
│   └── bikephysics/
│       ├── __init__.py             # version + the 6 top-level public names only
│       ├── core/
│       │   ├── mathx.py            # backend-neutral math namespace (numpy | casadi)
│       │   ├── units.py            # SI constants, conversion helpers, no runtime unit lib
│       │   ├── params.py           # pydantic models: BikeSpec and friends
│       │   ├── atmosphere.py       # ISA, rho(h, RH), mu(T)  [appendix/atmosphere.tex]
│       │   ├── aero.py             # AeroModel protocol + ConstantCdA / Linearized / Table
│       │   ├── road.py             # Road protocol + Flat / PiecewiseGrade / Spline / Sine / Jump
│       │   ├── pedals.py           # PedalInput protocol + templates from §pedal-force-model
│       │   ├── brakes.py           # BrakeInput protocol + Constant / Schedule / Tabulated
│       │   ├── limits.py           # wheelie / stoppie / traction / freewheel margin checks
│       │   ├── results.py          # Trajectory, Reactions, LimitMargins, RunSummary
│       │   ├── runio.py            # run-directory writer/reader, provenance capture
│       │   └── plotting.py         # shared figure helpers (axes, styling, save)
│       ├── data/                   # shipped presets, read via importlib.resources
│       │   ├── bikes/{road_bike,bmx,mtb}.toml
│       │   ├── roads/{flat,grade_5pct,rolling_sine,step_jump}.toml
│       │   └── problems/{min_time_1km,max_distance_60s,min_energy_30kmh}.toml
│       ├── simplified/
│       │   ├── model.py            # the boxed EOM, T, R_x, R_z, M_eq  (verbatim from the .tex)
│       │   ├── analysis.py         # steady state, elasticities, size elasticity, climb energy
│       │   ├── simulate.py         # solve_ivp driver + event/limit logging
│       │   └── plots.py
│       ├── planar/
│       │   ├── state.py            # q/u packing, BodyIndex, SystemLayout
│       │   ├── body.py             # RigidBody: mass props, body-frame points, kinematics
│       │   ├── system.py           # MultibodySystem: assemble M, h, J, zeta
│       │   ├── solver.py           # index-1 KKT saddle-point solve, unilateral trial logic
│       │   ├── integrate.py        # time stepping, events, drift projection, mode log
│       │   ├── kinematics.py       # consistent initial-condition solver
│       │   ├── assembly.py         # build_bike(spec, road) -> MultibodySystem
│       │   ├── drivetrain.py       # the lumped drivetrain assembly (§7.5)
│       │   ├── forces/
│       │   │   ├── base.py         # ForceElement protocol
│       │   │   ├── weight.py  aerodynamic.py  pedal.py  brake.py
│       │   │   ├── bearing.py      # Q_{k/l}, load-proportional
│       │   │   └── contact.py      # bridges ContactModel -> generalized forces
│       │   ├── constraints/
│       │   │   ├── base.py         # Constraint protocol (bilateral | unilateral)
│       │   │   ├── revolute.py  gear.py  rolling.py  weld.py
│       │   ├── contact/
│       │   │   ├── geometry.py     # foot-of-normal, gap, frame 0, rr offset, curvature check
│       │   │   ├── regularized.py  # DEFAULT: penalty normal + Stribeck friction
│       │   │   ├── event_driven.py # exact mode switching (fidelity reference)
│       │   │   └── complementarity.py  # LCP time-stepping (stretch, same interface)
│       │   ├── plots.py
│       │   └── animate.py
│       ├── optim/
│       │   ├── forward.py          # ForwardModel protocol + Simplified/Planar adapters
│       │   ├── parametrization.py  # ControlParametrization protocol + impls
│       │   ├── objectives.py       # MinTime / MaxDistance / MinEnergy / TrackSpeed
│       │   ├── constraints.py      # power, pedal-force, traction, wheelie, stoppie bounds
│       │   ├── problem.py          # OptimizationProblem (pydantic) + TOML loading
│       │   ├── report.py           # convergence + optimal-control figures, baseline compare
│       │   └── solvers/
│       │       ├── base.py  scipy_local.py  scipy_global.py
│       │       ├── casadi_collocation.py
│       │       └── analytic.py     # closed-form reference for the simplest min-time case
│       ├── validation/
│       │   ├── cross_tier.py       # planar steady state vs simplified closed form
│       │   ├── energy.py           # energy-balance audit for any Trajectory
│       │   └── golden/             # committed golden JSON summaries
│       └── cli/
│           ├── app.py              # the `bike` Typer root + global options
│           ├── common.py           # --config/--set resolution, run-dir creation
│           ├── simplified_cmds.py  planar_cmds.py  optim_cmds.py
│           └── spec_cmds.py  validate_cmds.py
├── examples/                       # replaces main.py; each file is runnable, prints + plots
│   ├── 01_simplified_baseline.py       ├── 04_planar_rolling_road.py
│   ├── 02_simplified_limits.py         ├── 05_planar_vs_simplified.py
│   ├── 03_simplified_sensitivity.py    └── 06_optimize_sprint.py
├── tests/
│   ├── unit/            # per-module, fast
│   ├── analytic/        # closed-form ground truths (free fall, pendulum, steady state)
│   ├── crosstier/       # Simplified <-> planar agreement
│   └── cli/             # each CLI command runs and writes a valid run dir
└── runs/                # gitignored output
```

### 3.1 Fate of existing code

| Existing | Action | Why |
|---|---|---|
| `bikephysics/parameters.py` | **Delete**, replaced by `core/params.py` | Stale symbols (`I2`,`I3`,`I4`,`r_cg`,`r_cr`,`theta1`), no front/rear split, no brake/limit params. `m_eq` formula is correct and carries over as `BikeSpec.m_eq` |
| `bikephysics/profiles.py` | **Delete**, superseded by `core/pedals.py` + `core/brakes.py` | It models a single scalar drive force `F(t)`, which does not exist in the current derivation; the pedal-force templates from `§pedal-force-model` replace it. The `RampForce` `v=0` discontinuity caveat is designed out (§7.6, smoothed `sigma`) |
| `bikephysics/simulation.py` | **Rewrite** as `simplified/{model,simulate}.py` | Same `solve_ivp` skeleton, but the RHS must become the boxed `M_eq` EOM with `P_D`/`P_F`, brakes, and reaction post-processing |
| `bikephysics/analysis.py` | **Rewrite/extend** as `simplified/analysis.py` | `steady_state_velocity` keeps its shape but gains the `P/v` power form, elasticities, and the limit accelerations |
| `bikephysics/plotting.py` | **Migrate** (`git mv`) to `simplified/plots.py` + extract shared helpers to `core/plotting.py` | The only module whose substance survives; the 6-panel layout is good and the panel set just grows |
| `main.py` | **Delete**; its four functions become `examples/01..03` and the `bike simplified` subcommands | The user asked for real applications, not a playground |
| `bike_simulation.png`, `bike_force_profile_comparison.png` | **Delete** from git, regenerated into `runs/` | Build artifacts at repo root |
| `requirements.txt` | **Delete**, replaced by `pyproject.toml` + `uv.lock` | |
| `README.md` | **Rewrite** | Describes the old single-DOF playground |

Migration is **in place on `master`**, as one scaffolding commit (T1) that leaves the tree importable but
with only `core` populated, followed by per-tier tickets. Rationale for in-place over a parallel
`bikephysics2/`: the old package has exactly one module worth keeping, and a parallel package would
guarantee a dead-code cleanup ticket nobody runs. The old code stays recoverable via git history.

---

## 4. Components and relations

```
                         ┌──────────────────────────────────────────┐
                         │                CLI  (`bike`)             │
                         │  simplified │ planar │ opt │ spec │ validate
                         └───┬──────────────┬─────────┬─────────────┘
                             │              │         │
            ┌────────────────▼───┐   ┌──────▼──────┐  │
            │  simplified        │   │  planar     │  │
            │  ┌──────────────┐  │   │ ┌─────────┐ │  │
            │  │ model.py     │  │   │ │assembly │ │  │
            │  │ (boxed EOM)  │  │   │ └────┬────┘ │  │
            │  ├──────────────┤  │   │ ┌────▼────┐ │  │
            │  │ analysis.py  │  │   │ │ system  │◄┼──┼── forces/*  (ForceElement)
            │  ├──────────────┤  │   │ │ M,h,J,ζ │ │  │   constraints/* (Constraint)
            │  │ simulate.py  │  │   │ └────┬────┘ │  │   contact/*  (ContactModel)
            │  └──────────────┘  │   │ ┌────▼────┐ │  │
            └────────┬───────────┘   │ │ solver  │ │  │
                     │               │ │ (KKT)   │ │  │
                     │               │ └────┬────┘ │  │
                     │               │ ┌────▼────┐ │  │
                     │               │ │integrate│ │  │
                     │               │ └─────────┘ │  │
                     │               └──────┬──────┘  │
                     │                      │         │
                     │   ┌──────────────────▼──────┐  │
                     └──►│   core.results.Trajectory│◄─┘        ONE schema, both tiers
                         │   (t, x, v, a, T, R_x,   │
                         │    R_z, power, margins)  │
                         └────────────┬─────────────┘
                                      │
                 ┌────────────────────▼───────────────────┐
                 │  optim.forward.ForwardModel  (protocol) │
                 │   SimplifiedForwardModel │ PlanarForwardModel
                 └────┬──────────────┬─────────────┬───────┘
                      │              │             │
             parametrization     objectives    constraints
                      └──────────────┼─────────────┘
                              ┌──────▼──────┐
                              │  solvers/*  │ scipy_local │ scipy_global │ casadi │ analytic
                              └─────────────┘

  core (shared, depends on nothing above it):
    mathx · units · params · atmosphere · aero · road · pedals · brakes · limits · results · runio · plotting
```

**Dependency rule (enforced by a ruff import-linter config in T1):**
`core` imports nothing from `simplified`/`planar`/`optim`/`cli`. `simplified` and `planar` import only
`core` — **never each other** (the one cross-tier comparison lives in `validation/cross_tier.py`, which may
import both). `optim` imports `core` + the two tiers through the `ForwardModel` adapters only. `cli` imports
everything and is imported by nothing.

**The load-bearing seam** is `core.results.Trajectory`: both tiers emit the same field names for the same
physical quantities, including the post-processed reactions and limit margins. That is what makes
`optim` tier-agnostic. If the two tiers' `Trajectory` ever diverge, the optimizer stops being reusable —
so `Trajectory` is a frozen, documented schema and both tiers have a conformance test against it.

---

## 5. Shared core design

### 5.1 `core/params.py` — the parameter model

pydantic v2, all models `frozen=True`. Field names are mechanically derived from
`latex/appendix/notation.tex`: a symbol `q_{body,descriptor}` becomes `q_body_descriptor`
(`r_2a` → `r_2a`, `l_2a_rr` → `l_2a_rr`, `r_3b_cr` → `r_3b_cr`, `c_2a_rr` → `c_2a_rr`). Every field carries
the LaTeX symbol in its `Field(description=...)` so `bike spec show` can print a symbol↔field table.

```
BikeSpec
├── frame: FrameParams      m_1, I_1, l_1_a, h_1_a, l_1_B, l_1_C, h_1_C, r_1_pedal
├── rear:  WheelParams      m_2a, I_2a, r_2a, r_2a_a, c_2a_rr, r_2a_bk
├── front: WheelParams      m_2b, I_2b, r_2b, r_2b_a, c_2b_rr, r_2b_bk
├── drivetrain: DrivetrainParams
│                           I_3b, r_3b_cr, r_2a_cg  (or n_teeth_cr / n_teeth_cg + chain pitch)
│                           eta_chain (=1.0), mu_b, d_bearing per joint, freewheel: bool
├── aero:  AeroParams       C_1_x_0, A_1, C_1_z_0, C_1_m_0, l_1 (ref length), slopes (default 0)
├── contact: ContactParams  mu_s, mu_k, k_N or delta_target, zeta_N, v_eps, v_stribeck
├── env:    EnvParams       g, atmosphere mode (constant | ISA), rho or altitude+RH, T, wind vector
└── rider:  RiderParams     P_max, P_pedal_max, cadence_max  (optimization bounds, all optional)
```

Derived properties live on the models, not in free functions: `BikeSpec.M` (total mass),
`BikeSpec.m_eq` (the boxed `M_eq`), `BikeSpec.gear_ratio` (`G = r_3b_cr / r_2a_cg`),
`DrivetrainParams.radii_from_teeth()`.

Validators: all masses/inertias/radii `> 0`; `mu_k <= mu_s`; `0 <= c_rr < 0.1`;
`r_2a == r_2b` emits a warning-not-error when violated (the Simplified tier *requires* equality, the planar
tier does not — `simplified/model.py` raises on inequality, planar does not).

Presets ship in `bikephysics/data/bikes/*.toml`. `--config` accepts either a preset name or a path.
`--set rear.c_2a_rr=0.008` applies dotted-path overrides after load, and the **resolved** spec is written
into every run dir.

### 5.2 `core/mathx.py` — backend-neutral math

A thin namespace object (`numpy` backend by default, `casadi` backend selected explicitly) exposing only
what the physics needs: `sin cos tan atan atan2 sqrt exp tanh abs maximum minimum where sign_smooth
hypot zeros stack solve`. Used by:
- `simplified/model.py` — **mandatory** from day one. It is ~20 smooth operations, so CasADi portability
  is nearly free here and is what unlocks optimization milestone 2.
- `planar/*` — **not** mandatory in milestone 1 (plain numpy), but the force/constraint assembly functions
  take `xp` as an argument so milestone 3 is a mechanical change rather than a rewrite.

`sign_smooth(v, eps)` is a single documented primitive (`tanh(v/eps)`) so the `sigma = sgn(x_dot)` of the
LaTeX appears in exactly one place and its regularization is reported in every run summary.

### 5.3 `core/road.py` — the road

```
class Road(Protocol):
    def z(self, x): ...
    def dz_dx(self, x): ...
    def d2z_dx2(self, x): ...
    def theta(self, x): ...            # atan(z')
    def frame0(self, x): ...           # (x_hat_0, z_hat_0) per wheels_ground.tex
    def curvature_radius(self, x): ... # rho = (1+z'^2)^{3/2}/|z''|, for the well-posedness check
```
Implementations: `Flat`, `ConstantGrade(theta)`, `PiecewiseGrade(breakpoints)` (C1-smoothed),
`CubicSplineRoad.from_csv/from_gpx` (scipy `CubicSpline`, C2 — the real-elevation-data path),
`SineRoad(amplitude, wavelength)`, `StepJump(...)` (for airborne-phase testing).

`contact_reference(road, center, radius, x_guess)` solves the foot-of-normal condition from
`wheels_ground.tex`. With `b A = (x_A - x) x̂_i + (z_A - z(x)) ẑ_i` and `x̂_0 ∝ (1, z'(x))`, the tangency
condition `b A · x̂_0 = 0` is the scalar residual

```
f(x) = (x_A - x) + (z_A - z(x)) * z'(x) = 0
f'(x) = -1 - z'(x)^2 + (z_A - z(x)) * z''(x)
```

solved by Newton, warm-started from the previous step's `x*` (so it costs 1–2 iterations inside the
integrator RHS), with a bracketed-bisection fallback on `[x_A - r, x_A + r]`. `curvature_radius(x*) < 5*r`
raises a logged warning — the well-posedness caveat from `refinements.tex` made observable instead of
silently wrong.

### 5.4 `core/pedals.py`, `core/brakes.py` — inputs

```
class PedalInput(Protocol):
    def forces(self, t, theta_3b, omega_3b) -> tuple[float, float]:   # (P_D, P_F) tangential, N
    def full(self, t, theta_3b, omega_3b) -> PedalWrench | None       # optional radial+tangential
```
Implementations map 1:1 onto `§pedal-force-model`: `ZeroPedal`, `ConstantCrankTorque(tau)`,
`CosSquared(P_0, phi)`, `HarmonicSeries(a_n, phi_n, N)`, `ClippedSine(P_0, theta_0, n)`,
`VonMises(P_0, theta_0, kappa)`, `TabulatedTime(t, P_D, P_F)`, `PiecewiseConstantTime(knots, values)`
(the optimizer's decision-variable carrier), `Tabulated CrankAngle` (the data-driven path).
`P_F` is always the `P_D` template offset by `pi` in `theta_3b`, as the spec requires.

Brakes mirror this: `BrakeInput.forces(t, state) -> (B_2a_1, B_2b_1)`, with `ConstantBrake`,
`BrakeSchedule(ramps)`, `TabulatedBrake`, and `BrakeAtDistance(x_trigger, B)`.

### 5.5 `core/results.py` — the shared schema

```
@dataclass(frozen=True)
class Trajectory:
    t, x, z, v, a                 # frame+rider CoG motion (z, and pitch below, are 0/const in Simplified)
    pitch, pitch_rate             # theta_1
    omega_2a, omega_2b, omega_3b  # absolute spins
    T                             # chain tension
    R_2a_x, R_2a_z, R_2b_x, R_2b_z  # ground reactions in frame 0, SHARED sign convention: R_z >= 0 = load
    power_pedal, power_ground, power_aero, power_rr, power_brake, power_gravity
    margins: LimitMargins         # traction / wheelie / stoppie / freewheel, each <= 0 means satisfied
    modes: ModeLog | None         # planar only: contact/stick/slip/freewheel intervals
    spec: BikeSpec
    meta: dict                    # solver, tolerances, every regularization parameter
```

`LimitMargins` encodes the Simplified-tier `Limits` block as *continuous* signed margins
(e.g. `traction_rear = |R_x| - mu_s * R_z`) rather than booleans, because the optimizer needs them as
smooth inequality constraints, not pass/fail flags.

### 5.6 `core/runio.py` — run artifacts and provenance

Every command that computes something writes `runs/<UTC-ISO>-<slug>/`:
`config.resolved.toml`, `result.npz`, `summary.json`, `*.png`, `log.txt`. `summary.json` captures package
version, git SHA + dirty flag, numpy/scipy/casadi versions, wall time, solver settings, every tolerance
and regularization parameter, and the scalar metrics. This file is both the human report and the golden
fixture format for regression tests, which is why it must be float-stable and key-ordered.

---

## 6. Simplified tier design

### 6.1 `simplified/model.py`

Implements `latex/simplified/forward_motion.tex` **verbatim**, in that section's own convention
(`ẑ_i` points *down*), with one explicit, single-point sign flip at the public boundary so everything
leaving the module is in the shared `ẑ`-up convention of `core.results`. The module docstring states this
and names the flip; the flip is one named constant, not scattered minus signs. (Decision D-13.)

Public surface, each a direct transcription of a boxed equation:

| Function | Equation |
|---|---|
| `m_eq(spec)` | `M + (I_2a+I_2b)/r_2a^2 + I_3b r_2a_cg^2/(r_3b_cr^2 r_2a^2)` |
| `chain_tension(spec, x_ddot, P_D, P_F)` | boxed `T_{2a/3b}` |
| `traction(spec, x_ddot, R_z, B, sigma)` | boxed `^{x_0}R_{2a/0}`, `^{x_0}R_{2b/0}` |
| `normal_loads(spec, x_ddot, theta, A_x, A_m, sigma)` | boxed `^{z_0}R` pair via `K` and `J` |
| `accel(spec, v, theta, P_D, P_F, B_2a, B_2b, aero)` | boxed `M_eq x_ddot = ...` |
| `limit_margins(...)` | the `Limits` block as signed margins |

`accel` is the only function in the whole codebase that may be called millions of times (optimization
milestone 1), so it is written through `core.mathx` and is free of Python branching: `sigma` comes from
`sign_smooth`, and `A_1_x` from `core.aero`.

### 6.2 `simplified/analysis.py`

| Function | Equation |
|---|---|
| `steady_state_speed(spec, P_sum_or_power, theta)` | `P/v = Mg(c_rr cos θ + sin θ) + ½ρC_dA v²` — solved as the real positive root of the cubic `k v³ + Mg(...)v - P = 0` by `numpy.roots` + root selection, with the force-input quadratic form also exposed |
| `elasticities(spec, v, theta, power)` | boxed `E_M`, `E_k`, `E_P`, **plus** an assertion that `E_M + E_k + E_P == 0` to 1e-12 (Euler's theorem) — a free, exact self-test |
| `size_elasticity(..., a, b)` | boxed `dlnv/dlnM = E_M + a E_P + b E_k` |
| `gradient_limits(spec, v, theta)` | the two asymptotic regimes, with the dominance ratio `Mg sin θ / k v²` reported so the user sees which regime they are in |
| `climb_energy(spec, power, delta_h, thetas)` | `E(θ) = P Δh /(v sin θ)` and the monotonicity check |
| `wheelie_accel(spec, theta, aero)` / `stoppie_accel(...)` | boxed critical decelerations |
| `traction_limit_accel(spec, theta, aero)` | largest `|x_ddot|` with both contacts inside the cone |

### 6.3 `simplified/simulate.py`

`solve_ivp` on `state = [x, v]`, `method="RK45"` default (`"Radau"` option), with **non-terminal** events
logging the first crossing of each limit margin, and terminal events for `x >= x_target` (needed by the
min-time objective) and `v <= 0`. Post-processing reconstructs `a`, the reactions, `T`, the spins, the
power split, and the margins, and returns a `Trajectory`. Crank angle is reconstructed algebraically:
`theta_3b(t) = theta_3b(0) - x(t) * r_2a_cg / (r_3b_cr * r_2a)` — no extra state, same trick the current
`simulation.py` uses, updated to current symbols. This matters because the pedal templates are functions
of crank angle, so a closed loop (`theta_3b -> P_D -> accel -> x -> theta_3b`) exists and must be resolved
by taking `theta_3b` from the integrator's current `x`, which it is.

---

## 7. Planar (2D) engine design

### 7.1 Conventions

Fixed per `latex/2d_systems/overview.tex` and used everywhere in `planar/`:

- Inertial frame `i`: `x̂_i` horizontal (forward positive), `ẑ_i` **up** (opposite gravity),
  `ŷ = ẑ_i × x̂_i` out of the sagittal plane. Positive rotation is about `+ŷ`.
- Consequence, inherited from both doc tiers: **forward rolling gives negative spin**,
  `omega_2a = -v_x / r_2a` (matches `forward_motion.tex`'s `phi_ddot_2a = -x_ddot / r_2a`).
  Positive pedal force `P_D > 0` therefore drives *negative* crank spin and positive chain tension
  `T >= 0`. Every sign in §7.5 follows from this and is cross-checked by the reduction test in §7.5.6.
- Body indices from the spec: `1` Frame+Rider, `2a` Rear wheel, `2b` Front wheel, `3b` Crankset.
  Cassette (`3a`) is folded into `2a`, per `latex/2d_systems/wheel.tex`.
- Relative spin: `phi_k = theta_k - theta_1` for `k ∈ {2a, 2b, 3b}`, per `frame_wheels.tex`.

### 7.2 Bodies, state, and degrees of freedom

Maximal coordinates: each body carries its own 3 DOF, constraints are explicit.

```
q = [ x_1, z_1, theta_1,  x_2a, z_2a, theta_2a,  x_2b, z_2b, theta_2b,  x_3b, z_3b, theta_3b ]   (12)
u = q_dot                                                                                        (12)
y = [q, u]                                                                                       (24 ODE states)
```

**Mass matrix `M = diag(m_1, m_1, I_1, m_2a, m_2a, I_2a, m_2b, m_2b, I_2b, m_3b, m_3b, I_3b)` is
constant** — a planar body's mass matrix in CoG coordinates has no configuration dependence. This is the
single biggest reason to pick maximal coordinates here: all configuration dependence is pushed into `J`
(geometry) and `h` (loads), both of which are assembled additively from independent elements, so a new
body or force never touches the solver.

Bilateral constraints (7 total) → 12 − 7 = **5 independent DOF**: 3 chassis (surge, heave, pitch) + rear
spin + front spin, with the crank spin geared to the rear. Exactly right for a rigid, unsuspended bike.

`planar/state.py` owns `SystemLayout`: body → slice mapping, constraint → row mapping, and named accessors
(`layout.theta(body="2a")`). No raw index arithmetic anywhere else — this is where index bugs would
otherwise live.

### 7.3 Force elements

```
class ForceElement(Protocol):
    name: str
    def accumulate(self, t, q, u, layout, h, xp) -> None:   # adds into generalized force vector h
    def diagnostics(self, t, q, u, layout) -> dict:          # optional, for Trajectory/ModeLog
```
A helper `apply_wrench(h, layout, body, world_force, world_point, moment=0.0)` does the
force→(F_x, F_z, M about CoG) reduction in one place, so every element is 5–15 lines.

| Element | Spec source | Notes |
|---|---|---|
| `Weight` | `forces.tex` `W_k = -m_k g ẑ_i` | per body; `g` from `core.units`/`EnvParams` |
| `Aerodynamic` | `forces.tex` + `§aerodynamics-model` | `A_1_f = -A_1x x̂_1 - A_1z ẑ_1` at the aero reference point of body 1, plus `A_1_m = -A_1m ŷ`. `AeroModel` supplies the coefficients; default `ConstantCdA` uses only the `beta_rel = 0` intercept `C_1_x_0` (all that is calibratable today), with the linearized-slope path wired but zeroed |
| `PedalForce` | `forces.tex` `P`, `frame.tex` geometry | **Applies both halves of the action/reaction pair**: `+P_{3b/1,X}` on body `3b` at pedal points `D`, `F` (at `C ± r_1_pedal x̂_3b`), and `-P_{3b/1,X}` on body `1` at the same material points. Default reduction: tangential only, giving ŷ-moment `tau_pedal = -r_1_pedal (P_D + P_F)` on `3b` about `C` |
| `BrakeTorque` | `wheel.tex` + `forward_motion.tex` | On wheel `2a`: `+sigma * r_2a_bk * B_{2a/1} ŷ`; **and the equal-opposite `-sigma r_2a_bk B_{2a/1} ŷ` on body `1`** (the caliper is frame-mounted). The Simplified single-body tier cannot see that reaction; the planar tier must, since it drives brake-induced pitch. Same for `2b` |
| `BearingFriction` | `§bearing-friction-model` | `Q_{k/l} = -½ mu_b d_{k/l} |R_{k/l}| sgn_smooth(omega_{k/l}) ŷ`, applied as a torque pair. **Depends on the constraint reaction `R_{k/l}`**, which is a solver output → one-step lag (use the previous accepted step's multiplier) is the default; a single fixed-point re-solve is the option. Default `mu_b = 0` (off) in milestone 1, because the one-step lag is only defensible for a term this small and it must be justified before being switched on |
| `ContactForce` | `wheels_ground.tex` | Thin bridge: asks the active `ContactModel` for each wheel's contact wrench and applies it. All the physics is in `contact/` (§7.6) |

### 7.4 Constraints

```
class Constraint(Protocol):
    name: str
    dim: int
    kind: Literal["bilateral", "unilateral"]
    def residual(self, q, layout, xp): ...           # c(q),            shape (dim,)
    def jacobian(self, q, layout, xp): ...           # J = dc/dq,       shape (dim, nq)
    def rate_term(self, q, u, layout, xp): ...       # -J_dot u,        shape (dim,)
    def multiplier_bounds(self): ...                 # None | (lo, hi), for unilateral
```

| Constraint | dim | Spec | Residual |
|---|---|---|---|
| `RevoluteJoint(1, 2a, point=A)` | 2 | `frame_wheels.tex` | `x_2a + R(theta_2a) a_2a^{(2a)} - (x_1 + R(theta_1) A^{(1)}) = 0`, i.e. the two bodies' material point `A` coincides (written through each body's CoG offset) |
| `RevoluteJoint(1, 2b, point=B)` | 2 | `frame_wheels.tex` | as above with `B` |
| `RevoluteJoint(1, 3b, point=C)` | 2 | new (BB axle) | `C` is `frame.tex`'s bottom-bracket point; the crankset CoG sits at `C` per the Simplified assumption, so its offset is zero |
| `IdealChain(2a, 3b)` | 1 | **new, §7.5** | `c = r_3b_cr * phi_3b - r_2a_cg * phi_2a`, multiplier **is** chain tension `T`, `kind="unilateral"`, bounds `(0, +inf)` — the freewheel |
| `RollingContact(wheel)` | 1 | `wheels_ground.tex` stick case | `v_slip = 0` at velocity level. **Only used by the event-driven contact model**; the default regularized model does not instantiate it |

Note how little is special here: the chain and the freewheel are *the same abstraction* as a pin joint
and a unilateral contact normal. That is the payoff of the multiplier formulation and the reason D-10's
recommendation is structured this way.

`theta_2a = theta_1 + phi_2a` from `frame_wheels.tex` is not a constraint — it is the *definition* of
`phi`, a derived quantity (`planar/state.py` computes it). No DOF is spent on it.

### 7.5 Lumped drivetrain model — full equations

> **DECISION REQUIRED (D-10).** This is new modeling work, not transcription. The spec's
> `\subsection{Drivetrain}` under 2D-Systems is empty and `latex/timeline.tex` step 1 only says
> "simplified (lumped) drivetrain". Three candidate lumpings are compared in §10 D-10; the one specified
> below is the recommendation. It needs the user's sign-off before implementation, and ticket T12 ports it
> back into `latex/2d_systems/drivetrain.tex`.

#### 7.5.1 Modeling choices being made

1. **Cassette folded into the rear wheel.** `I_2a` includes the cassette; `r_2a_cg` is the engaged cog's
   pitch radius, a geometric property of `2a`. Matches `latex/2d_systems/wheel.tex` ("Carries the
   drivetrain (cassette) reaction at the hub") and `forward_motion.tex`.
2. **Crankset `3b` kept as a real rigid body**, not eliminated — 3 extra DOF, with CoG at the bottom
   bracket `C` (the Simplified assumption, reused) so its weight and the BB reaction produce no moment
   about `C`.
3. **Chain is massless, inextensible, non-slipping, lossless, and single-speed** — one scalar holonomic
   constraint. No chain body, no chainline geometry, no compliance, no derailleur.
4. **Chain tension is the constraint's Lagrange multiplier.** No chain force element is written.
5. **Freewheel is the unilateral condition `T >= 0`** on that multiplier, resolved as a mode switch, not
   a post-check (the Simplified tier post-checks it; the planar tier can afford to do it properly, and
   must, because coasting and braking runs are a primary use case).
6. **Pedal force is the rider's control input**, applied as an action/reaction pair between `1` and `3b`
   (§7.3), tangential-only by default with the full planar vector available.
7. **Drivetrain efficiency `eta_chain = 1`.** The parameter exists in `DrivetrainParams` and is asserted
   to be 1 in milestone 1; applying it is a `refinements.tex` item.

#### 7.5.2 Gear ratio and kinematics

Chain linear speed is common to both sprockets, so with the spec's symbols:

```
r_3b_cr * phi_dot_3b  =  r_2a_cg * phi_dot_2a                                             (7.1)

G  ≡  r_3b_cr / r_2a_cg        (dimensionless, > 1 for a normal drivetrain)                (7.2)

phi_dot_2a = G * phi_dot_3b,        phi_2a = G * phi_3b  (+ const)                         (7.3)
```

i.e. the rear wheel turns `G` times faster than the cranks. In absolute angles, using
`phi_k = theta_k - theta_1`:

```
theta_3b = theta_1 + phi_2a / G
alpha_3b = alpha_1 + (alpha_2a - alpha_1) / G  =  alpha_1 (1 - 1/G) + alpha_2a / G         (7.4)
```

The holonomic constraint, in the form the engine consumes:

```
c_chain(q) = r_3b_cr * (theta_3b - theta_1) - r_2a_cg * (theta_2a - theta_1)  =  0         (7.5)

J_chain = dc/dq :   dc/dtheta_3b = +r_3b_cr
                    dc/dtheta_2a = -r_2a_cg
                    dc/dtheta_1  = r_2a_cg - r_3b_cr
                    (all translational entries zero)                                       (7.6)
```

`c_chain` has units of length, so its multiplier has units of force and **is** the chain tension:
`lambda_chain = T_{2a/3b}`. Three sanity properties of (7.5)–(7.6):

- Under a rigid rotation of the whole bike (`dtheta_1 = dtheta_2a = dtheta_3b`), `dc = 0` — the chain does
  no work and exerts no generalized force, as a true internal element must not.
- The generalized forces the multiplier produces are `-r_3b_cr T` on `theta_3b` and `+r_2a_cg T` on
  `theta_2a` (sign convention: `h_constraint = -J^T lambda`), i.e. the chain resists crank spin and drives
  wheel spin in the forward (negative-spin) sense. Correct.
- The non-zero `dc/dtheta_1` term is physically real, not an artifact: holding the wheel and crank at fixed
  *absolute* angles while pitching the frame changes the *relative* angles, which a rigid chain resists.
  The alternative "pure torque pair at the hub" lumping (option D-10-A) throws exactly this term away.

#### 7.5.3 Pedal input

Per `frame.tex` geometry, with the correction noted in §11 (`x̂_2b` → `x̂_3b`):

```
C D = + r_1_pedal * x̂_3b,        C F = - r_1_pedal * x̂_3b                                 (7.7)
P_{3b/1,X} = ^{x_3b}P_X x̂_3b + ^{z_3b}P_X ẑ_3b ,     X ∈ {D, F}                           (7.8)
```

Default tangential-only reduction (`^{x_3b}P = 0`, tangential unit vector `ê_t = ŷ × x̂_3b`), giving the
ŷ-moment on `3b` about `C`:

```
tau_pedal = - r_1_pedal * (P_D + P_F)                                                      (7.9)
```

negative because positive pedal force drives forward (= negative spin) under §7.1's convention — identical
to `forward_motion.tex`'s `I_3b phi_ddot_3b = r_3b_cr T - r_1_pedal (P_D + P_F)`.

The crankset's own equation, which the engine assembles rather than solves by hand (CoG at `C`, so no
transport term, and the BB reaction and crankset weight pass through `C`):

```
I_3b * alpha_3b  =  r_3b_cr * T  +  tau_pedal  +  Q_{3b/1}                                (7.10)
```

#### 7.5.4 Freewheel (unilateral chain)

```
Engaged:  c_chain = 0,   T = lambda_chain >= 0
Coasting: constraint row dropped,   T = 0,   crankset free:  I_3b alpha_3b = tau_pedal + Q_{3b/1}
Complementarity:   T >= 0,   c_dot_chain <= 0 (chain cannot be pushed),   T * c_dot_chain = 0   (7.11)
```

Resolution inside the solver RHS (§7.7): trial-solve with the chain row active; if `lambda_chain < 0`,
re-solve with it dropped. At most two linear solves of a 19×19 dense system — negligible. When re-engaging
from coast, the velocity-level constraint is generally violated, so engagement is an **impulsive** event:
project `u` onto the constraint manifold (`u <- u - M^-1 J^T (J M^-1 J^T)^-1 J u`), which is the correct
perfectly-plastic chain-engagement impulse and is logged as a `ModeLog` event with its energy loss.

#### 7.5.5 Where the drivetrain is *not* modeled

Deliberately absent, each a named future extension (§13): chain-span geometry and chainline force
(subsumed by the multiplier, §7.5.2), chain mass and inertia, chain compliance, chain/sprocket slip,
derailleur shifting dynamics, drivetrain efficiency loss, freehub ratchet engagement angle, crank-arm
flex, and left/right crank asymmetry beyond the `P_D`/`P_F` split already present.

#### 7.5.6 Reduction test — the acceptance criterion

Under the Simplified tier's own assumptions (constant grade, `theta_1 = const` so `alpha_1 = 0`, no-slip
rolling so `alpha_2a = -x_ddot / r_2a`, chain engaged), equation (7.10) with (7.4) and (7.9) gives

```
r_3b_cr * T = I_3b * alpha_3b + r_1_pedal (P_D + P_F)
            = I_3b * (-x_ddot / (G * r_2a)) + r_1_pedal (P_D + P_F)

  =>   T = - ( I_3b * r_2a_cg / (r_3b_cr^2 * r_2a) ) * x_ddot  +  ( r_1_pedal / r_3b_cr ) (P_D + P_F)
```

which is **exactly** the boxed chain-tension equation of `latex/simplified/forward_motion.tex`. Likewise
the reflected crankset inertia `I_3b / (G^2 r_2a^2) = I_3b r_2a_cg^2 / (r_3b_cr^2 r_2a^2)` is exactly the
third term of the boxed `M_eq`.

**Therefore:** ticket T7 is not complete until a test asserts that the planar model, run on flat ground
with the rolling constraint enforced and the limits satisfied, reproduces `simplified.analysis.
steady_state_speed` and `simplified.model.chain_tension` to within integration tolerance. This is the
"2D must be sanity-checked against Simplified" requirement from `latex/timeline.tex`, made executable.

### 7.6 Ground contact

Three interchangeable implementations behind one interface:

```
class ContactModel(Protocol):
    def wrenches(self, t, q, u, layout, road, spec, cache) -> list[ContactWrench]: ...
    def constraints(self) -> list[Constraint]: ...      # non-empty only for event_driven
    def events(self) -> list[EventFn]: ...              # mode-switch detectors
    def state(self) -> ContactState: ...                # per-wheel mode, gap, slip, for ModeLog
```

#### 7.6.1 Shared geometry (`contact/geometry.py`)

Per wheel `k ∈ {2a, 2b}`, transcribing `wheels_ground.tex`:

```
x*            = contact_reference(road, axle_world, r_k, warm_start)       # §5.3 Newton
x̂_0, ẑ_0      = road.frame0(x*)
g_N           = (b_k A) · ẑ_0(x*) - r_k                                    # gap
A b_k         = l_k_rr * sigma * x̂_0 - r_k * ẑ_0 ,   l_k_rr = c_k_rr * r_k
sigma         = sign_smooth( v_{k,A} · x̂_0 , v_eps )
v_{k,b_k}     = v_{k,A} + omega_k × (A b_k)
v_slip        = v_{k,b_k} · x̂_0        (with the l_k_rr term dropped from the arm here only,
                                        exactly as wheels_ground.tex prescribes)
```

The smoothed `sigma` is not a liberty — it replaces a `sgn` discontinuity at `v = 0` that is the direct
cause of the integrator stall documented in the current `profiles.py::RampForce` docstring. `v_eps` is
reported in every run summary.

#### 7.6.2 Default: `RegularizedContact` (recommended, D-9)

Normal force — Hunt–Crossley with a clamp, so it is continuous and zero on both approach and release:

```
delta   = max(0, -g_N)
R_z     = max( 0,  k_N * delta^e * (1 + 1.5 * c_HC * g_N_dot) )            (7.12)
```

with `e = 1` (linear) by default for predictability. **Stiffness is derived, not guessed** — this is the
single thing that makes penalty contact usable instead of a tuning nightmare:

```
k_N  = M_total * g / delta_target                   with delta_target ≈ 1–3 mm (configurable)
c_N  = 2 * zeta_N * sqrt(k_N * m_eff),  zeta_N ≈ 0.7–1.0,  m_eff = static load share / g
c_HC = c_N / k_N
```

Tangential force — Stribeck-regularized Coulomb, which reproduces the static>kinetic transition of
`wheels_ground.tex` **without any mode switching**:

```
mu(v_slip) = mu_k + (mu_s - mu_k) * exp( -(|v_slip| / v_stribeck)^2 )
R_x        = - mu(v_slip) * R_z * sign_smooth(v_slip, v_eps)               (7.13)
```

Contact wrench on wheel `k`: force `R_x x̂_0 + R_z ẑ_0` applied at the world point `A + A b_k`.

**Honest cost of this choice.** "Stick" becomes a small creep rather than exact zero slip. Quantify it
for the developer: at `mu_s = 1.0`, `R_z ≈ 600 N`, `v_eps = 1e-3 m/s`, a 100 N tractive demand is met at
`v_slip = v_eps * atanh(0.167) ≈ 1.7e-4 m/s` — about 0.2 mm/s, which accumulates to ~1 cm over a minute.
Negligible against riding speeds, non-zero against an exact rolling constraint. Mitigations, all built:
`validation/energy.py` reports cumulative creep distance and the friction-dissipation budget;
`v_eps` is configurable; and `EventDrivenContact` exists for runs that need exact rolling.

Integration: the normal spring makes the system stiff (`sqrt(k_N/m) ≈ 500–2000 rad/s`), so the default
integrator is **`solve_ivp(method="Radau")`** with finite-difference Jacobians and a supplied
`jac_sparsity` pattern from `SystemLayout`. `BDF` and `LSODA` are selectable fallbacks.

#### 7.6.3 Fidelity reference: `EventDrivenContact`

Enumerates per-wheel modes `{airborne, stick, slip+, slip-}`, and within each mode the system is a smooth
index-1 DAE (stick instantiates `RollingContact` as a real constraint; slip applies
`R_x = -mu_k R_z sgn(v_slip)` as a force with `R_z` from the normal constraint multiplier). `solve_ivp`
`events` detect `g_N = 0` (touchdown/liftoff), `R_z = 0` (liftoff), `|R_x| - mu_s R_z = 0` (stick→slip),
`v_slip = 0` (slip→stick); on each, integration stops, the mode flips, impacts apply a Newton restitution
law (`e_n` configurable, default 0 = perfectly plastic) plus a tangential impulse inside the friction cone,
and integration restarts. This is the exact model in `wheels_ground.tex`. Its costs are real: 9 mode
combinations to test, chattering near grazing transitions needing a hysteresis band, and a
non-differentiable trajectory (so it is unusable for gradient-based optimization). Built in T11, not T6.

#### 7.6.4 Stretch: `ComplementarityContact`

Moreau–Jean fixed-step time-stepping with a per-step LCP (normal complementarity + a polygonalized
friction cone, which in 1D tangential is just a box). Needs an LCP/QP solver — either a vendored Lemke
implementation or `qpsolvers`/`osqp` as an optional extra. Most robust for impact-heavy BMX/MTB work later.
Interface-compatible, implemented only if T11 reveals the event-driven model chattering badly.

### 7.7 Solver and integration strategy

`planar/solver.py`, one RHS evaluation:

```
1. unpack y -> q, u ;  compute per-body rotation matrices, world points, point velocities
2. h  = 0 ;  for each ForceElement: element.accumulate(t, q, u, layout, h, xp)
             (ContactForce consults the active ContactModel; warm-started x* from `cache`)
3. assemble active constraint rows:  J (n_c x 12),  and
   zeta = -J_dot u  -  2*alpha*c_dot  -  beta^2 * c            (Baumgarte stabilization)
4. solve the index-1 KKT saddle-point system
        [ M    J^T ] [  a  ]   [ h    ]
        [ J    0   ] [ -λ  ] = [ zeta ]
   dense LU (scipy.linalg.lu_factor / lu_solve), size (12 + n_c) <= 19
5. unilateral trial: if λ_chain < 0 -> drop the chain row, go to 4 once more
   (regularized contact needs no trial; event_driven does the same for each contact normal)
6. return [u, a] ;  stash λ, contact state, x* into `cache` for diagnostics and bearing friction
```

Why this formulation (D-8): the LaTeX derivation is *written* in maximal coordinates — per-body
`sum F = m a` / `sum M = I alpha` plus explicitly separate joint and contact relations. The code is then a
transcription, reviewable line-by-line against the document, and the extension cost of a new body is "one
`RigidBody` + one `Constraint`", with the solver untouched. A hand-reduced minimal-coordinate model would
be faster per step and is what the Simplified tier already is, but every new body means re-deriving the
whole thing by hand — precisely the thing `latex/timeline.tex`'s six-step 2D build order forbids.

Drift control: Baumgarte with `alpha = beta = 1/(10 * h_typ)` by default, plus an optional post-step
position/velocity projection (Gauss–Newton on `c(q) = 0` in the `M`-norm, then `J u = 0`) every `N` steps.
`integrate.py` reports `max|c|` and `max|J u|` over the run in `summary.json`; a run whose constraint
residual exceeds a configured bound fails loudly rather than producing plausible-looking nonsense.

Events: terminal (`x >= x_target` — required by min-time optimization; frame CoG below the road =
"crash"; `t_end`) and non-terminal/logging (liftoff, touchdown, stick↔slip, freewheel engage/disengage,
each limit-margin sign change). Modes and events land in `Trajectory.modes`.

### 7.8 Initial conditions

Non-trivial and a frequent source of garbage runs, so it gets its own module. `planar/kinematics.py`
solves, given `(spec, road, x_rear_0, v_0, pedal/brake inputs)`:

- **Position:** `scipy.optimize.root` on the 7 bilateral constraint residuals plus two contact conditions
  (`g_N = -delta_static` per wheel for the regularized model, `g_N = 0` for the exact models), unknowns
  `(x_1, z_1, theta_1, x_2a, z_2a, x_2b, z_2b, x_3b, z_3b)` with the spins free. On flat ground an
  analytic initial guess is available and used as the seed.
- **Velocity:** rolling seed `v_1 = v_0 x̂_0`, `omega_2a = -v_0 / r_2a`, `omega_2b = -v_0 / r_2b`,
  `omega_3b` from (7.3); then projected onto the constraint null space (`u <- u - J^+ (J u)`) so the
  velocity-level constraints are satisfied to machine precision at `t = 0`.
- **Report:** `bike planar initcheck` prints the residuals, the static normal-load split (which should
  match the Simplified tier's `normal_loads` at `x_ddot = 0`), and the derived penetration — a 5-second
  smoke test before any long run.

---

## 8. Optimization software design

### 8.1 The seam

```
class ForwardModel(Protocol):
    spec: BikeSpec
    def rollout(self, controls: ControlInputs, horizon: Horizon) -> Trajectory: ...
    def metrics(self, traj: Trajectory) -> dict[str, float]: ...
    @property
    def differentiable(self) -> bool: ...        # True only for the CasADi-backed variants
```

Two adapters, `SimplifiedForwardModel` and `PlanarForwardModel`, both returning the *same*
`core.results.Trajectory`. Everything downstream — parametrization, objectives, constraints, solvers,
reports — is written against `ForwardModel` only and never imports a tier. Swapping the forward model is a
one-line config change, which is the explicit requirement from `latex/timeline.tex` ("build it to be
reusable against the 2D model later").

### 8.2 Decision variables

```
class ControlParametrization(Protocol):
    n_vars: int
    def bounds(self, spec) -> list[tuple[float, float]]: ...
    def initial_guess(self, spec) -> ndarray: ...
    def build(self, x: ndarray) -> ControlInputs: ...     # -> PedalInput + BrakeInput (+ spec override)
    def describe(self, x: ndarray) -> dict: ...           # human-readable optimum
```

| Impl | Vars | Notes |
|---|---|---|
| `TemplateShape` | 2–4 | `P_0`, `theta_0`, and `n` or `kappa` from `§pedal-force-model`. The cheap first milestone |
| `PiecewiseConstantPedal(N)` | `N` | Direct shooting control vector, `N ≈ 10–50` |
| `SplinePedal(N)` | `N` | Smoother, better-conditioned than piecewise-constant for gradient methods |
| `PiecewiseConstantBrake(N)` | `N` | Needed for descent/corner-entry problems |
| `GearSelection(cog_teeth_options)` | 1 (discrete) | Gear ratio as a decision variable, per the user's ask. Handled by enumeration (outer loop) rather than a relaxed continuous radius, because cog teeth are integers |
| `Composite([...])` | sum | Concatenates; this is how "pedal + brake + gear" problems are expressed |

### 8.3 Objectives and constraints

```
class Objective(Protocol):
    def value(self, traj: Trajectory) -> float: ...
    requires: frozenset[str]        # Trajectory fields it needs -> lets the rollout skip unused post-processing
```
`MinTimeOverDistance(x_target)` (uses the terminal event; penalizes non-arrival with a slack term so the
search stays smooth), `MaxDistanceInTime(t_end)`, `MinMechanicalEnergy(target_mean_speed)`,
`MinEnergyWithTimeCap(x_target, t_cap)`, `TrackSpeedProfile(v_ref(t))`, `Weighted([...])`.

```
class PathConstraint(Protocol):
    def g(self, traj: Trajectory) -> ndarray: ...     # <= 0 is feasible, elementwise over time
    def penalty(self, traj, weight) -> float: ...     # for penalty-mode solvers
    requires: frozenset[str]
```
`PowerCeiling(P_max | P_max(cadence))` — the rider power-curve hook for the future Strategy track;
`PedalForceBound(P_min, P_max)` — **configurable, no hardcoded number**, because
`latex/appendix/refinements.tex` explicitly records that pedal-force bounds are not specified anywhere
yet; `CadenceBound`; `TractionMargin`, `WheelieMargin`, `StoppieMargin` (reading
`Trajectory.margins` directly, so the same code constrains both tiers);
`ChainTensionNonNegative`; `SpeedBounds`; `TerminalState(...)`.

### 8.4 Solvers

```
class Solver(Protocol):
    def solve(self, problem: OptimizationProblem) -> OptimResult: ...
```
`OptimResult` carries `x_opt`, the optimal `Trajectory`, the full iterate history, status, wall time,
and the active-constraint report — all written into the run dir so `bike opt compare` can overlay runs.

| Solver | Milestone | Approach |
|---|---|---|
| `ScipyLocal` | **1** | `L-BFGS-B` / `SLSQP` / `trust-constr`, finite-difference gradients, hard constraints via SLSQP or penalty aggregation. Multi-start wrapper |
| `ScipyGlobal` | **1** | `differential_evolution` / `dual_annealing` — robust for the 2–6-var `TemplateShape` problems, and the fallback whenever the forward model is non-differentiable (event-driven contact) |
| `AnalyticSimplified` | 1 (cheap) | Closed-form/Pontryagin reference for the flat, constant-power, limit-free min-time case. Its only job is to be a ground truth the numeric solvers must match — a genuine correctness oracle for the whole optimization stack |
| `CasadiCollocation` | **2** | Direct collocation (Radau, 3rd order, `N = 50–200` intervals) over the Simplified dynamics expressed through `core/mathx.py`'s CasADi backend; states and controls as NLP variables, dynamics as equality constraints, path constraints native, free final time via time scaling; IPOPT. Exact first and second derivatives |
| `CasadiCollocation` on planar | **3 (stretch)** | Feasible *because* the regularized contact is smooth and `M` is constant: the KKT solve becomes `casadi.solve(...)`, which CasADi differentiates. Blocker to research: the foot-of-normal Newton iteration — use `casadi.rootfinder` (differentiable implicit function) or restrict to roads with closed-form `x*` (flat / constant grade) |

**Why milestone 1 is parametrization + scipy, not collocation (D-11):** a Simplified rollout is ~ms, so
10³–10⁵ objective evaluations are free; a 2–6-variable template fit or a 10–50-variable shooting problem
is solved in seconds with no new dependency; and it works against *any* forward model including the
non-differentiable ones. Collocation is strictly better at scale but requires the CasADi dependency, a
second expression of the dynamics (mitigated but not eliminated by `core/mathx.py`), and careful
scaling/initialization to converge. Shipping milestone 1 first also gives milestone 2 a reference answer
to be validated against, which is worth more than the two weeks it costs.

### 8.5 Problem definition

`optim/problem.py` is a pydantic model loaded from TOML, so experiments are files, not code:

```toml
# bikephysics/data/problems/min_time_1km.toml
name = "min_time_1km_flat"
forward_model = "simplified"          # | "planar"
bike = "road_bike"                    # preset name or path
road = "flat"
horizon = { t_max = 300.0, x_target = 1000.0 }
initial = { v_0 = 0.0 }

[parametrization]
kind = "piecewise_constant_pedal"
n = 20

[objective]
kind = "min_time_over_distance"

[[constraints]]
kind = "power_ceiling"
p_max = 400.0

[[constraints]]
kind = "pedal_force_bound"
p_max = 800.0                         # NOT a physical claim - see refinements.tex

[[constraints]]
kind = "traction_margin"

[solver]
kind = "scipy_local"
method = "SLSQP"
multistart = 8
```

---

## 9. CLI design

One console script, `bike` (Typer), with five subcommand groups. Rationale over three separate scripts
(D-7): global flags, run-directory plumbing, config resolution, and `--json` output are written once; the
cross-tier commands (`planar crosscheck`, `validate`) have no natural home under a per-app script; and
discoverability via `bike --help` matters for a tool the user will return to months later.

```
bike simplified run           --config road_bike --road grade_5pct --pedal von-mises --t-end 120
bike simplified steady        --power 250 --grade 0.05 [--sweep-grade 0:0.12:25]
bike simplified sweep         --param rear.c_2a_rr --range 0.003 0.010 --n 20 --metric steady_speed
bike simplified limits        # wheelie / stoppie / traction critical accelerations, as a table
bike simplified sensitivity   # E_M, E_k, E_P + identity check + the two gradient regimes + size elasticity

bike planar run               --config road_bike --road rolling_sine --contact regularized --t-end 30
bike planar initcheck         # IC solve, constraint residuals, static load split vs. simplified
bike planar animate           --run runs/<dir>            # or --config ... to simulate then animate
bike planar crosscheck        # steady-state + chain-tension agreement with the Simplified tier
bike planar modes             --run runs/<dir>            # mode timeline: contact / stick / slip / freewheel

bike opt solve                --problem min_time_1km [--solver scipy-local] [--multistart 8]
bike opt compare              --problem min_time_1km --solvers scipy-local,scipy-global,casadi
bike opt baseline             --problem min_time_1km      # evaluate a non-optimized control for reference

bike spec show                --config road_bike          # symbol <-> field table, derived quantities
bike spec presets             # list shipped bikes / roads / problems
bike spec validate            --config path/to/my.toml

bike validate all             # the full cross-tier + analytic + golden regression harness
bike validate energy          --run runs/<dir>            # energy-balance audit of an existing run
```

Global options on every command: `--config` (preset name or path), `--set key.path=value` (repeatable),
`--out DIR` (default `runs/`), `--no-plot`, `--json` (machine-readable summary on stdout, so later tracks
and the orchestrator can script it), `--log-level`, `--seed`.

Config-file-first with CLI overrides, never the other way round: anything physical lives in TOML, and the
CLI exposes only run control plus `--set`. This keeps the argument surface small and makes every run
reproducible from its own `config.resolved.toml`.

---

## 10. Decision points requiring sign-off

Each option's `cost_impact` is "what it costs in dependencies, implementation effort, and money". Money is
zero for every option in this document — no paid service is proposed anywhere.

### D-1 Repository / package topology

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. Monorepo, one package `bikephysics` with subpackages (`core`,`simplified`,`planar`,`optim`,`cli`), `src/` layout** ← recommended | One install, one version, one test run; shared `Trajectory`/`BikeSpec` with no cross-package release dance; `src/` prevents accidental imports of the uninstalled tree | A single version number for three apps; nothing stops a lazy import across tier boundaries except the lint rule | 1 `pyproject.toml`; ~1h scaffolding |
| B. Monorepo with three installable packages (`uv` workspace: `bike-core`, `bike-sim`, `bike-opt`) | Hard dependency boundaries enforced by packaging; could publish `bike-core` alone | 4 `pyproject.toml`s, 4 version numbers, editable-install churn on every refactor; heavy for one user | ~4h scaffolding + ongoing friction |
| C. Keep the current flat `bikephysics/` package, add modules | Zero migration | 25+ modules in one flat namespace; no boundary between tiers; the import-cycle risk the `Trajectory` seam exists to avoid | 0 now, high later |

**Recommendation: A.** B's boundaries are real, but their cost is paid every day by a single developer for
a benefit (independent release) that will never be exercised.

### D-2 Fate of existing `bikephysics/` and `main.py`

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. In-place migration: `git mv` `plotting.py`, rewrite the rest, delete `main.py` (its content → `examples/` + CLI)** ← recommended | One importable tree at all times; no dead code; git history preserved for the deleted modules | A window during T1–T4 where the Simplified app is being rebuilt and the old entry point is already gone | ~2h in T1 |
| B. New package alongside, retire the old one later | Old code keeps running during the rebuild | Two parallel parameter models (`BikeParameters` vs `BikeSpec`) invite silent divergence; the cleanup ticket historically never happens | ~1h now, ~2h cleanup later (optimistically) |
| C. Keep `bikephysics/` as a frozen `legacy/` subpackage | Reference implementation stays callable for comparison | The current code does not match the current derivation, so it is a *misleading* reference, not a useful one | ~0.5h |

**Recommendation: A.** The old code's physics is superseded (single scalar `F(t)`, stale symbols), so
keeping it runnable has negative value. Its one good module migrates; git keeps the rest.

### D-3 Environment and packaging tooling

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. `uv` + `pyproject.toml` + committed `uv.lock`** ← recommended | Already installed (0.12.17); very fast; lockfile pins exact numeric library versions, which a physics regression suite genuinely needs; `uv run bike ...` with no activation ritual | One more tool concept; lockfile churn in diffs | ~0.5h |
| B. `pip` + `requirements.txt` (status quo) | Zero new concepts | No lockfile → a scipy minor bump can silently shift golden-test values; no project metadata, so no `bike` console script | ~0.2h |
| C. Poetry / PDM / Hatch | Mature, batteries-included | Not installed; slower; nothing `uv` does not already cover here | ~1h + install |

**Recommendation: A.**

### D-4 Config and parameter modeling

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. pydantic v2 frozen models** ← recommended | Validation (`m > 0`, `mu_k <= mu_s`) and file loading in one place; dotted-path overrides; JSON-schema export for the future R&D/Data-acquisition tracks; good error messages for a hand-edited TOML deck | 1 runtime dependency; a tiny amount of attribute-access overhead on hot paths (irrelevant — hot paths take floats, not models) | 1 dep, ~0.5h setup |
| B. Frozen `dataclasses` + hand-written validation and TOML loading | Zero dependencies; matches the existing `BikeParameters` style | Validation and loading get written by hand for ~8 nested models and will be incomplete; no schema export | 0 deps, ~4h of boilerplate that pydantic does for free |
| C. `attrs` + `cattrs` | Fast, flexible converters | Two deps instead of one; less familiar; no schema export story | 2 deps, ~1h |

**Recommendation: A.** Decisive factor: the parameter deck is large (~50 fields across 8 models),
hand-edited in TOML, and *silently wrong* values produce plausible-looking physics. Validation at the
boundary is worth one dependency.

### D-5 Config file format

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. TOML** ← recommended | `tomllib` in stdlib for reading; comments (essential for a parameter deck annotating LaTeX symbols); unambiguous scalars | Nested arrays-of-tables are verbose; needs `tomli-w` (tiny) for writing the resolved config | 1 small dep |
| B. YAML | Most compact for nested structures; familiar | `pyyaml`/`ruamel` dependency; the classic footguns (`0.005` vs `.005`, bare `on`/`no` as booleans, tabs) in a file where a wrong number is a wrong physics result | 1 dep |
| C. JSON | Stdlib, exact round-trip | No comments — disqualifying for a deck where every field wants its LaTeX symbol annotated | 0 deps |

**Recommendation: A**, with JSON retained for machine-written artifacts (`summary.json`).

### D-6 CLI framework

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. Typer** ← recommended | Signatures come from type hints, so commands stay short; sub-apps compose naturally into the five groups; `rich` help output; pairs with pydantic | 2 deps (typer → click, rich) | 2 deps, ~1h |
| B. Click | Very mature and stable; full control | Decorator boilerplate per option; ~25 commands × 6 global options is a lot of repetition | 1 dep, ~2h |
| C. argparse | Stdlib | Nested subcommand groups with shared global options is genuinely painful; grows into a hand-rolled framework | 0 deps, ~4h |

**Recommendation: A.**

### D-7 CLI topology

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. One entry point `bike` with subcommand groups** ← recommended | Config/run-dir/`--json` plumbing written once; cross-tier commands have a home; one `--help` tree to rediscover months later | A single command's help output is large (mitigated by grouping) | baseline |
| B. Three console scripts (`bike-simplified`, `bike-2d`, `bike-opt`) | Each app is independently obvious; matches "three deliverables" literally | Triplicated global-option plumbing; `validate`/`crosscheck` fit nowhere; three things to remember | ~+2h |
| C. One script per app plus a shared `bike` for cross-cutting commands | Best of both | Four entry points; "which one do I run?" ambiguity | ~+3h |

**Recommendation: A.** The three deliverables stay clearly separated as `bike simplified` /
`bike planar` / `bike opt`.

### D-8 2D dynamics formulation

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. Maximal coordinates (3 DOF/body) + constraints as Lagrange multipliers, index-1 KKT solve** ← recommended | Transcribes the LaTeX one-to-one (per-body FBD + explicit joint/contact relations), so code is reviewable against the document; constant `M`; new body/force/constraint = new element, solver untouched; multipliers *are* the reaction forces `R` the document asks for; the chain and the freewheel reuse the same abstraction | 24 ODE states instead of ~10; needs drift stabilization (Baumgarte/projection); a 19×19 linear solve per RHS evaluation | ~2 weeks (T5+T6) |
| B. Minimal coordinates, hand-derived (e.g. 5 generalized coordinates) | Fastest per step; no drift; no constraint solve | Every one of the six future 2D build-order steps means re-deriving by hand; reactions require extra back-substitution; contact mode switches change the coordinate set | ~1 week now, re-derivation forever |
| C. Symbolic generation (`sympy.physics.mechanics` Kane/Lagrange) → codegen | Minimal-coordinate speed with automatic re-derivation; symbolic EOM could be exported into LaTeX | Heavy dependency; hybrid contact mode switching sits badly with a pre-generated EOM (one codegen per mode); generated code is opaque to review; slow symbolic setup | 1 dep, ~2–3 weeks, high risk |

**Recommendation: A.** The deciding factor is `latex/timeline.tex`'s six-step 2D build order
(complex drivetrain → suspension → tire stiffness → rider legs → full rider). Option A makes each step
additive; B makes each step a rewrite.

### D-9 Contact handling and integration strategy

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. Regularized/penalty contact (Hunt–Crossley normal + Stribeck friction) with a stiff integrator (Radau)** ← recommended as the default | No mode logic at all — one smooth ODE; robust; **differentiable**, which is what makes optimization milestone 3 possible; stiffness derived from a target penetration rather than guessed; the smoothed `sgn` also fixes the existing `v=0` integrator stall | Not the exact rigid contact of `wheels_ground.tex`: ~1–3 mm penetration and ~0.2 mm/s stick creep (quantified in §7.6.2); stiff → Radau, ~3–10× more RHS evaluations than a non-stiff problem; three regularization parameters to report | ~1 week (T6) |
| B. Event-driven exact mode switching (airborne / stick / slip per wheel, `solve_ivp` events + restarts + impact law) | Exactly the document's model; exact rolling; physically interpretable mode timeline | 9 mode combinations; chattering near grazing transitions needs hysteresis; impacts need a separate impulsive law; **non-differentiable**, so gradient-based optimization is out; the most bug-prone option | ~2 weeks (T11) |
| C. Complementarity time-stepping (Moreau–Jean + per-step LCP) | Most robust for impacts and simultaneous contacts; no mode enumeration; the standard for serious multibody contact | Needs an LCP/QP solver (vendored Lemke or `osqp`/`qpsolvers`); fixed-step, so accuracy control is manual; least familiar formulation; overkill for two contacts on a smooth road | 1–2 deps, ~2–3 weeks |

**Recommendation: A as the default, B as the fidelity reference (T11), C behind the same interface
(stretch).** All three satisfy one `ContactModel` protocol, so this is a default choice, not a lock-in.
Decisive factor: A is the only option that keeps the forward model differentiable, and `latex/timeline.tex`
names optimization as the primary goal.

### D-10 Lumped 2D drivetrain model — PHYSICS MODELING CHOICE

> Not a tech choice. §7.5 specifies option B in full. The spec's drivetrain subsection is empty, so
> whichever option is chosen becomes new modeling content and is ported back into
> `latex/2d_systems/drivetrain.tex` by ticket T12.

| Option | Pros | Cons | Cost |
|---|---|---|---|
| A. **Hub-torque pair, no crankset body.** Pedal input collapses to `tau_d = r_1_pedal (P_D+P_F) / G` applied to wheel `2a`, with `-tau_d` on frame `1`; crankset inertia added to `2a` as `I_3b / G^2`; crank angle derived from wheel spin | Simplest possible: no extra body, no extra constraint, 9 DOF instead of 12; closest in spirit to the Simplified tier; cheapest to implement | Discards the chain's real coupling to frame pitch (the `dc/dtheta_1` term of eq. 7.6) — i.e. loses the chain-force contribution to load transfer under hard pedaling; reflected inertia must be hand-injected into `I_2a`, which is an off-document fudge; freewheel becomes awkward (the reflected inertia has to be removed on coast); the crankset gains no path to becoming a real body later | ~2 days |
| B. **Crankset as a real body `3b` + revolute at the bottom bracket `C` + one scalar ideal-chain constraint whose multiplier is `T`; freewheel as `T >= 0`** ← recommended | Chain tension `T` comes out as a solved quantity with the document's own meaning, not a derived substitution; pedal forces act on the bodies the document says they act on; the chain's frame-pitch coupling is exact; freewheel is a clean unilateral mode, not a post-check; **provably reduces to the boxed Simplified `T` and `M_eq`** (§7.5.6), so the cross-tier test is exact rather than approximate; the step-2 "complex drivetrain" future work is then *relaxing* this constraint (chain compliance) and *adding* bodies (cassette, chain), not a rewrite; needs zero new engine machinery — reuses `Constraint` | 3 more DOF and 3 more constraint rows (12 DOF / 7 constraints vs 9 / 4) — ~30% more linear-solve work on a system small enough that this is microseconds; one more body's mass properties (`I_3b`, already in the Simplified parameter set) to supply | ~4 days |
| C. **Full chain representation now**: chainring and cog as geometric circles, explicit tangent-span geometry, collinear chain force pair at the two tangent points, optional chain mass/compliance | Highest fidelity; direct path to chain-compliance and chainline studies | Span geometry (tangent line between two circles, top/bottom span selection, slack-side handling) is real work for a result that, for a rigid chain, is *provably identical* to option B by virtual work; `latex/timeline.tex` explicitly defers per-component drivetrain bodies to build-order step 2 | ~2 weeks, out of scope now |

**Recommendation: B.** It is the only option that is simultaneously (i) exactly reducible to the already-
derived Simplified closed form, which makes the mandated cross-tier sanity check rigorous, (ii) free of
off-document fudges, and (iii) a strict subset of the eventual complex drivetrain rather than a detour.

**Sub-decision D-10a — freewheel handling**

| Option | Pros | Cons |
|---|---|---|
| **A. Unilateral mode switch (`T >= 0`), trial-solve + plastic engagement impulse** ← recommended | Coasting, braking, and back-pedalling all simulate correctly; costs at most one extra 19×19 solve per RHS evaluation | Engagement is an impulsive event with a small energy loss to log; a mode to test |
| B. Post-check only, as the Simplified tier does | Zero implementation | Any coast or brake run is silently wrong (the chain would push); unusable for the braking and descent scenarios that are a primary use case |
| C. Smooth one-way clutch (stiff tension-only spring) | No mode switch, differentiable | Another stiffness to tune; blurs the exact `T` the document wants |

**Sub-decision D-10b — pedal input default**

| Option | Pros | Cons |
|---|---|---|
| **A. All three available behind `PedalInput`; default to a shape template (`ClippedSine`)** ← recommended | Matches `§pedal-force-model`'s tiering; the template default produces realistic crank-angle-dependent force out of the box; `PiecewiseConstantTime` is the optimizer's carrier; `ConstantCrankTorque` is the debugging/cross-check mode | Three code paths to test (each is ~15 lines) |
| B. Free control only (tabulated), no analytic templates | Simplest; the optimizer is the only consumer | No sensible default for a plain `bike planar run`; cannot reproduce the Simplified tier's constant-force cross-check conveniently |
| C. Lumped constant crank torque only | Simplest of all; closest to the existing code | Throws away the crank-angle dependence the spec spends a page on, and with it the pedal-phase effects that make a 2D model worth having |

### D-11 Optimization first-milestone approach

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. Control parametrization + scipy (template shape fitting, then piecewise-constant shooting)** ← recommended for milestone 1 | No new dependency; 2–50 decision variables; a Simplified rollout is ~ms so 10³–10⁵ evaluations are free; works against *any* forward model, including non-differentiable ones; delivers a working answer in days and becomes the reference for milestone 2 | Finite-difference gradients cost `n+1` rollouts; does not scale past ~100 variables; path constraints are approximate unless SLSQP handles them directly | 0 deps, ~4 days |
| B. Direct multiple shooting with scipy | Better conditioned than single shooting over long horizons; still dependency-free | Needs state-continuity constraints, so most of the collocation machinery without the exact gradients that justify it | 0 deps, ~1 week |
| C. Direct collocation with CasADi + IPOPT | Scales to fine discretizations; exact derivatives; path constraints native; free final time; the right long-term tool | 1 substantial dependency; the dynamics must be expressed in CasADi symbolics (mitigated by `core/mathx.py`, but the planar tier needs real work); scaling/initialization sensitivity means the first convergence is days of fiddling with nothing to validate against | 1 dep, ~1.5–2 weeks |

**Recommendation: A for milestone 1, C for milestone 2** (tickets T8 then T9), with an
`AnalyticSimplified` Pontryagin solution as a correctness oracle for both. Deciding factor: A produces a
trustworthy answer before C can produce any answer, and C's convergence problems are far easier to
diagnose when a known-good optimum already exists.

### D-12 Autodiff / NLP stack (for milestone 2+)

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. CasADi (+ bundled IPOPT)** ← recommended | Purpose-built for exactly this (optimal control, collocation, NLP); IPOPT ships in the wheel, so no separate solver install on Windows; `rootfinder`/`integrator` give differentiable implicit functions, which is the escape hatch for the foot-of-normal solve; mature, stable API | Its own symbolic type system, so the dynamics must be written backend-neutrally; no GPU; debugging symbolic graphs is unpleasant | 1 dep, ~1.5 weeks |
| B. JAX + diffrax + optimistix | `grad` through the ODE solve; JIT; the most "Pythonic" autodiff; good for gradient-based *unconstrained* or penalty-form problems | Constrained NLP support is weak compared to IPOPT (no good interior-point story), which hurts because traction/wheelie/power limits are the interesting constraints; functional-purity discipline (no in-place `h` accumulation) conflicts with the force-element design; Windows CPU-only; heavier install | 3 deps, ~2 weeks |
| C. Pyomo(.dae) + IPOPT | Clean algebraic-modeling syntax; proper DAE discretization support | IPOPT must be installed separately on Windows (conda or a binary), which is the one real friction point; another full modeling layer to learn; weaker fit for simulate-then-optimize workflows | 1 dep + external solver, ~2 weeks |
| D. scipy only, forever | Zero new dependencies | Caps the optimization track at ~100 decision variables with finite-difference gradients — fine for template fitting, limiting for fine-grained optimal control over a real route | 0 deps |

**Recommendation: A**, as an **optional extra** (`uv sync --extra opt`) so the core three apps never
require it. Install verification is the first task of ticket T9 — if no Python 3.12 Windows wheel exists,
the fallback is D plus a revisit.

### D-13 Sign-convention handling between the two tiers

`latex/simplified/forward_motion.tex` uses `ẑ` pointing **down**; `latex/2d_systems/overview.tex` uses
`ẑ` **up**. The document itself flags this (an `\ai{}` axis-direction note) and keeps it.

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. Implement each tier in its own document's convention; flip signs at the `simplified` module's public boundary only** ← recommended | Each module is a line-by-line transcription of its own LaTeX section, so review against the document is mechanical; one named constant marks the flip; the shared `Trajectory` has one unambiguous convention (`R_z >= 0` = load on ground) | A reader of `simplified/model.py` must hold "`z` is down in here" in mind; the flip is a real (if single) place a sign bug can hide | ~0.5h + a test asserting both tiers agree on the static normal-load split |
| B. Implement both tiers `ẑ`-up; `simplified/model.py` diverges in sign from its own LaTeX section | One convention everywhere in code | Every boxed equation in `forward_motion.tex` must be re-signed by hand during transcription — the single highest-risk activity in the whole project, repeated ~8 times, with no document to check against afterwards | ~2h + permanent review friction |
| C. Re-sign the LaTeX (`forward_motion.tex` → `ẑ` up) first, then transcribe verbatim | Permanently removes the inconsistency; the document becomes internally uniform | Edits a document the user has just declared finished; touches ~15 equations in a derivation the user wrote by hand; the user may have reasons for keeping it | ~2h of LaTeX editing, **user's call, not the architect's** |

**Recommendation: A** for the code now, with **C offered separately** as a documentation task if the user
wants the inconsistency gone at the source.

### D-14 Results persistence format

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. `result.npz` (time series) + `summary.json` (scalars, provenance, config)** ← recommended | Zero dependencies; exact float round-trip; `summary.json` doubles as the golden-regression fixture and the human report; trivially diffable | Not columnar/queryable; no multi-run concatenation story | 0 deps |
| B. Parquet (pyarrow) | Columnar, compressed, queryable; good when many runs get analyzed together (the future R&D track) | 1 large dependency; awkward for ragged per-mode event data | 1 dep |
| C. HDF5 / netCDF (h5py / xarray) | Hierarchical, self-describing, handles ragged data and metadata natively; the scientific-computing standard | 1–2 heavy dependencies; binary blobs are opaque to git and to diffing | 1–2 deps |

**Recommendation: A**, with B reconsidered when the R&D track starts doing multi-run parameter studies
(`runio.py` is the single seam that would change).

### D-15 Test, lint, and type tooling

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. pytest + ruff (lint+format) + mypy** ← recommended | Already have pytest; ruff is one fast binary replacing flake8+black+isort+an import-boundary linter; mypy catches exactly the class of bug this codebase is made of (wrong array shape, swapped argument order, `None` leaking from an optional model) | 2 dev deps; mypy on numeric code needs some `ndarray` typing discipline | 2 dev deps, ~1h config |
| B. pytest only | Nothing to configure | The §4 import-boundary rule becomes unenforceable; formatting drift; no type checking on a codebase whose bugs are overwhelmingly silent-wrong-number bugs | 0 deps |
| C. A + hypothesis (property-based tests) | Property tests are a genuinely good fit here (energy conservation, constraint residuals, `E_M+E_k+E_P=0`, time-reversibility of a conservative run) | A third dev dep and a new idiom to learn; the hand-written analytic tests already cover most of the value | 3 dev deps, ~+3h |

**Recommendation: A**, and revisit C after T12 — the validation suite is where property testing would pay
off, so it is a natural T12+ addition rather than a T1 commitment.

### D-16 Visualization scope

| Option | Pros | Cons | Cost |
|---|---|---|---|
| **A. matplotlib static figures + a `matplotlib.animation` 2D replay (bike-on-road, force vectors, mode shading)** ← recommended | Already a dependency; the animation is the single highest-value debugging tool for a 2D contact model (a wrong contact point is obvious in one glance and invisible in six time-series panels); output sits next to the LaTeX figures stylistically | Animation export needs `ffmpeg` or falls back to GIF; matplotlib animation is slow for long runs (sub-sample) | 0 new deps, ~1 day for `animate.py` |
| B. Static matplotlib only | Least work | Debugging contact geometry from numbers alone is genuinely hard; the user asked for volume and "even base for future stuff" | 0 deps |
| C. Add an interactive backend (plotly / pyqtgraph) | Zoomable, shareable HTML; smooth real-time replay | 1 heavy dependency; a second plotting idiom; no benefit the static+animation combination does not already give for a single-user research tool | 1 dep, ~3 days |

**Recommendation: A.**

---

## 11. Findings in the LaTeX spec

Raised for the user's decision; **no LaTeX is edited by this architecture work**. None of these blocks
implementation; each is a small, independent documentation fix.

1. **`latex/2d_systems/frame.tex`, pedal-point geometry — probable typo.** `AD` and `AF` place the pedal
   points at `± r_1_pedal x̂_2b`, i.e. along the **front wheel's** body axis. For a crankset body this
   should be `x̂_3b` (the crank's own axis). §7.5.3 assumes `x̂_3b`. Worth confirming.
2. **Empty `\subsection{Drivetrain}`** (`latex/main.tex` line 126) and no 2D-Assembly reduction section.
   §7.5 fills the first gap; ticket T12 ports it back. The second gap is effectively filled by the
   solver design (§7.7) rather than by closed-form algebra, which is the honest answer for a hybrid system.
3. **`latex/2d_systems/overview.tex` lists Drivetrain subsystems as "Casette / Crankset"**, while
   `wheel.tex` folds the cassette into `2a`. Consistent once `3a` is understood as "deferred to the
   complex-drivetrain step", but the overview list reads as if `3a` were modeled today. (Also: "Casette"
   → "Cassette".)
4. **Brake reaction on the frame is absent from the Simplified tier** — correctly, since it is a one-body
   model. §7.3 notes that the planar tier must apply the equal-and-opposite caliper torque to body `1`,
   since it drives brake-induced pitch. Not an error in the document, but a difference between tiers that
   the eventual `drivetrain`/`assembly` LaTeX sections should state explicitly.
5. **`forward_motion.tex`'s `ẑ`-down convention** vs. 2D's `ẑ`-up. See D-13 option C if the user wants
   this resolved at the source.
6. **`appendix/notation.tex` carries two open `\todo{}`s** (consistency sweep; self-referential-prose
   sweep). Independent of this work; noted so they are not forgotten.

---

## 12. Non-functional decisions

### 12.1 Performance targets (and what to do when they are missed)

| Workload | Target | Diagnosis if missed |
|---|---|---|
| `simplified` rollout, 120 s sim, 2000 output points | < 20 ms | `accel` has Python branching or allocates per call; it must be branch-free and scalar |
| `simplified` objective evaluations for optimization | > 3000 / s | as above; also check `solve_ivp` is not re-creating the input objects per call |
| `planar` rollout, 60 s sim, regularized contact, Radau | < 5 s | almost always mis-scaled contact stiffness: check `delta_target` and `zeta_N` before blaming the solver; then check that the foot-of-normal Newton is warm-started (1–2 iterations, not 10) |
| `planar` rollout, event-driven contact | < 30 s | mode chattering; widen the hysteresis band |
| Full `bike validate all` | < 3 min | — |

Single-process, single-threaded by default. The only concurrency is optional multi-start optimization,
parallelized with `concurrent.futures.ProcessPoolExecutor` (no new dependency) — and only after
profiling shows it matters.

### 12.2 Reproducibility

Every run writes the resolved config, the package version, the git SHA + dirty flag, the numpy/scipy/
casadi versions, every solver tolerance, and **every regularization parameter** (`v_eps`, `k_N`, `c_N`,
`v_stribeck`, Baumgarte `alpha`/`beta`, `delta_target`) into `summary.json`. A result whose regularization
parameters are not recorded is not a result. `--seed` is threaded into every stochastic solver.

### 12.3 Numerical hygiene

- No magic numbers in physics code. Every tolerance and regularization constant comes from `BikeSpec`
  or a solver-settings model, with a documented default and a derivation rule (§7.6.2).
- Constraint residuals (`max|c|`, `max|J u|`) are computed and reported for every planar run; exceeding a
  configured bound fails the run loudly.
- `validation/energy.py` audits every trajectory: pedal work in = kinetic + potential change + aero +
  rolling + brake + friction dissipation + (contact penalty and chain-engagement losses, planar only),
  and reports the closure error as a fraction of total work. A planar run that does not close to < 1 % is
  a bug, not a result.
- The exact `E_M + E_k + E_P = 0` identity and the Simplified reduction of §7.5.6 are permanent assertions,
  not one-off checks.

### 12.4 Caching / scaling / queues

None, deliberately. No cache layer, no job queue, no database, no network I/O. The only "scaling" axis is
the number of optimization rollouts, addressed by making a rollout cheap (§12.1) and by optional
process-pool multi-start. If parameter sweeps ever outgrow this, the seam is `runio.py` (switch D-14 to
Parquet) plus a sweep driver — not an architectural change.

### 12.5 Licensing and cost

numpy / scipy / matplotlib (BSD), pydantic / typer / click / rich (MIT), CasADi (LGPL-3.0, used as an
unmodified library), IPOPT (EPL-2.0, bundled in the CasADi wheel), pytest / ruff / mypy (MIT). All free,
all permissive enough for a private research project and for later publication of results. **Total
monetary cost: $0.** No account, API key, or hosted service is required by any part of this design.

---

## 13. Extension seams vs. the refinements list

Each `latex/appendix/refinements.tex` item mapped to the seam it plugs into — the concrete sense in which
this design is "a base for future stuff". **None of these is implemented now.**

| Refinement | Seam | Rough shape of the change |
|---|---|---|
| Frame equivalent stiffness | new `Constraint` | Replace one `RevoluteJoint` with a compliant variant (force element, not constraint): a spring-damper between the two bodies' material points |
| Rim/wheel stiffness | new `RigidBody` + force element | Split a wheel into hub and rim bodies joined by a radial/torsional spring |
| Chain and drivetrain compliance | **relax `IdealChain`** | Drop the constraint, add a force element `T = k_chain * c_chain + c_damp * c_dot_chain` with `T >= 0`. The §7.5 formulation was chosen so this is a one-file change |
| Dedicated brake subsystem | new force element + inputs | `BrakeTorque` gains a pad/rotor friction model and hydraulic lag; `BrakeInput` already exists as the control seam |
| Viscous bearing-friction correction | existing `BearingFriction` | Add an `omega`-proportional term to `|Q|`; already a force element with the right inputs |
| Pedal-force bounds | existing `optim/constraints.py` | `PedalForceBound` exists and is configurable; this refinement supplies the numbers, not the code |
| Pedal-force model selection | existing `PedalInput` | All candidate templates are already implemented; selection is config + a data-fitting script |
| Weather-driven mass changes | `BikeSpec` | Make `m_1` time- or state-dependent via an optional callable; everything downstream reads `m_1` through the body |
| Non-rigid rider mass properties | new `RigidBody` + joints | Build-order steps 5–6; the whole point of maximal coordinates (D-8) is that this is additive |
| Aerodynamic configuration `xi_k` | existing `AeroModel` | `LinearizedCoefficients` already has the `d/d xi` slot, zeroed; needs research into what `xi` is, then a table lookup |
| Raindrop impact | new force element | A velocity-dependent drag term on body `1` |
| Combined-slip tire model | new `ContactModel` | Needs lateral dynamics first (3D or a planar-lateral extension); the `ContactModel` protocol gains a lateral component |
| Steering/trail, gyroscopic effects | out of plane | Genuinely needs the 3D tier; not a planar seam |
| Road parametrization for BMX/MTB | existing `Road` protocol | `Road` is already a protocol; an arc-length `OP(s)` variant means a second protocol method set, and the contact search generalizes from foot-of-normal-on-a-graph to closest-point-on-a-curve |
| Contact well-posedness under curvature | `contact/geometry.py` | `curvature_radius(x*) < 5 r` already warns; a strict mode would raise, and a multi-point contact search would handle crests/dips properly |

Plus `latex/timeline.tex`'s own 2D build order: step 2 (complex drivetrain) = relax `IdealChain` + add
cassette/chain bodies; step 3 (suspension) = add fork/swingarm bodies + joints + spring-damper elements;
step 4 (tire stiffness) = a new `ContactModel` with a finite patch; steps 5–6 (rider) = new bodies +
actuated joints, where `PedalInput` is replaced by joint torque actuators.

---

## 14. Rejected options

Beyond the per-decision tables in §10, these were considered and dropped outright:

- **An existing multibody library (pydy, PyChrono, MuJoCo, Drake, Brax) instead of a hand-written engine.**
  Each would give contact and constraints for free, and MuJoCo/Drake are vastly better engines than
  anything written here. Rejected because the deliverable is *this derivation*, implemented so that each
  equation is traceable to a line in `latex/`. A general engine's contact model, coordinate choice, and
  solver are not the document's, so cross-checking code against the derivation — the project's whole
  purpose — becomes impossible. Worth revisiting only if the 3D tier ever needs to be fast more than it
  needs to be legible.
- **`pint` or another runtime unit library.** Catches the exact bug class this project is prone to
  (mixing `N` with `N·m`, degrees with radians), but imposes per-operation overhead on a hot path that is
  called millions of times during optimization, and infects every signature. Replaced by discipline: SI
  everywhere, units in every docstring and pydantic field description, conversions confined to
  `core/units.py` and the CLI boundary.
- **A notebook-first workflow (Jupyter + papermill).** A natural fit for exploratory physics, and the user's
  current `main.py` is effectively a notebook. Rejected as the *primary* interface because the user asked
  for applications, and because notebooks make the regression-test suite that guards the physics
  impractical. `examples/` plus the `--json` CLI output keeps the exploratory path open, and notebooks can
  import the package at any time.
- **A GUI / web dashboard (Streamlit, Dash).** Large surface area, a second UI to maintain, and nothing a
  single researcher gains over a CLI plus matplotlib. Revisit if the R&D track (Timeline 3) ever needs to
  be handed to a non-programmer.
- **Writing the Simplified tier as a special case of the planar engine** (one body, locked DOFs) instead of
  as its own closed-form module. Appealingly DRY, and it would guarantee the two tiers agree. Rejected
  because the Simplified tier's value is precisely that it is *closed-form, independently derived, and
  fast* — it is the cross-check, and a cross-check that shares its implementation with the thing it checks
  checks nothing. It must also stay ~ms-fast for optimization milestone 1.
- **Symbolic generation of the LaTeX from the code (or vice versa).** Tempting, and sympy could in
  principle emit the EOM as LaTeX. Rejected as a research project of its own with a high chance of
  producing unreadable equations; the chosen direction (code transcribes the document, tests enforce
  agreement) is lower-risk and gives the same guarantee.
- **Making `optim` depend directly on the two tiers' concrete classes** instead of the `ForwardModel`
  protocol. Simpler by one indirection. Rejected because `latex/timeline.tex` makes tier-portability of
  the optimizer an explicit requirement.

---

## 15. Open risks

| Risk | Severity | Mitigation |
|---|---|---|
| **No Context7 / no library-docs access during architecture.** Version-specific API claims here (scipy Radau options, Typer sub-app composition, pydantic v2 config, CasADi wheels) are from model knowledge, not verified against current docs | Medium | T1 verifies each and records findings in `architecture-notes.md` before any code depends on them. None of the architecture's *structure* depends on these details |
| **The `grilling` / `codebase-design` / `domain-modeling` skills were unavailable** in this session, so the broad option survey was done by direct analysis rather than by the mandated interview | Medium | §10's 16 decision tables are the substitute and are deliberately exhaustive. If the orchestrator can reach the skill, a grilling pass over §10 before sign-off is still worth doing — the highest-value targets are D-8, D-9, and D-10 |
| **Regularized contact's stick creep and penetration** are a real departure from the document's rigid contact; a user reading "2D simulator" may expect exact rolling | Medium | Quantified in §7.6.2; reported in every run summary; `EventDrivenContact` (T11) provides the exact reference and a quantified comparison |
| **Contact stiffness tuning** is the classic way penalty contact becomes unusable | Medium | Stiffness is *derived* from `delta_target` and `zeta_N`, never typed in as a number; §12.1 names mis-scaled stiffness as the first thing to check when a run is slow |
| **CasADi portability of the planar dynamics** (milestone 3) hinges on making the foot-of-normal Newton solve differentiable | Medium | Deliberately deferred to T10 as a stretch; two escape routes identified (`casadi.rootfinder`, or analytic-`x*` roads); milestones 1–2 do not depend on it |
| **D-10 is new physics**, so an error in it is an error in the project's content, not just its code | High | The §7.5.6 reduction to the boxed Simplified equations is an exact, falsifiable test, and is a T7 completion gate. T12 ports the model back into LaTeX for the user's own review |
| **Mode chattering** in the event-driven contact model near grazing liftoff or at `v_slip = 0` | Medium | Hysteresis bands; only affects T11, which is explicitly the fidelity reference and not the default |
| **Scope**: this is a large design (~13 tickets). Risk of stalling after the 2D engine and never reaching the optimization track, which `latex/timeline.tex` names as the primary goal | Medium | Ticket order in `task.md` makes each of the three deliverables independently shippable, and T8 (optimization milestone 1) depends only on the *Simplified* tier — so the primary-goal track can be reached without the 2D engine being finished |
| **Drift in maximal coordinates** over long runs | Low | Baumgarte + optional projection + a reported residual bound that fails the run |
| **Golden-test brittleness** across numpy/scipy versions | Low | `uv.lock` pins versions; golden comparisons use physics-scaled tolerances, not exact float equality |
