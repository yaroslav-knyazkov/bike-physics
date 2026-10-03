# Implementation tickets — bike-physics

## Summary

Thirteen sequential-ish tickets implementing `architecture.md`: scaffolding and shared core (T1–T2), the
Simplified-tier app (T3–T4), the planar 2D engine and app (T5–T7), the optimization app (T8–T10), and
fidelity/validation/port-back work (T11–T13). Ordered so each of the three deliverables is independently
shippable: the Simplified app is complete after T4, the 2D app after T7, and the optimization app after
T8 — and T8 depends only on the Simplified tier, so the project's primary goal is reachable without the
2D engine being finished.

**Every ticket names the exact spec slice it needs.** Do not read `architecture.md` end-to-end for a
ticket; `Grep -n` the named anchor and `Read` with `offset`/`limit`. Same for `latex/` — the files are
small, but read only the ones listed.

**Blocked until sign-off:** no ticket may start before the user approves §10 of `architecture.md`.
T5/T6/T7 additionally require sign-off on **D-8, D-9, D-10, D-10a, D-10b** specifically — T7 implements
new physics (the lumped drivetrain) and must not be built on an unapproved model.

Conventions for every ticket: SI units everywhere; field/identifier names derived from
`latex/appendix/notation.tex`; every new module gets a module docstring naming the LaTeX section it
implements; ruff + mypy clean; tests written in the same ticket, not after.

---

## T1 — Repository scaffolding, packaging, and the shared-core skeleton

- **Module/boundaries:** `pyproject.toml`, `uv.lock`, `.gitignore`, `README.md`, `architecture-notes.md`,
  `src/bikephysics/{__init__,core/*}.py` (skeletons + `units.py`, `mathx.py`, `results.py`, `runio.py`,
  `plotting.py` complete), `src/bikephysics/cli/{app,common}.py` (root app + global options, no physics
  commands), `tests/unit/test_core_*.py`, `tests/cli/test_app_help.py`, deletion of
  `bikephysics/{parameters,profiles,simulation,analysis}.py`, `main.py`, `requirements.txt`, the two root
  `*.png`; `git mv bikephysics/plotting.py src/bikephysics/simplified/plots.py`
- **Assignee:** developer
- **depends_on:** []
- **parallel_safe:** false
- **Spec slice:** `architecture.md` §2 (Chosen stack), §3 (Repository and package layout) incl. §3.1,
  §5.2, §5.5, §5.6, §12.2, §12.3

Set up the project per §3. Concretely:

1. `pyproject.toml` (PEP 621): package `bikephysics`, `src/` layout, `requires-python = ">=3.12"`,
   runtime deps numpy/scipy/matplotlib/pydantic/typer/tomli-w, `[project.optional-dependencies] opt =
   ["casadi"]`, dev group pytest/ruff/mypy, console script `bike = "bikephysics.cli.app:main"`. Configure
   ruff (incl. an import-boundary rule enforcing §4's dependency rule: `core` must not import the tiers)
   and mypy. `uv lock && uv sync`; commit `uv.lock`.
2. **Version-verification pass (do this first, it gates later tickets).** Confirm against current docs and
   write the findings into `architecture-notes.md`: scipy 1.18 `solve_ivp` Radau/BDF signature incl.
   `events`, `jac_sparsity`, `dense_output`; pydantic v2 frozen-model and `model_validate` usage;
   Typer sub-app composition and global-callback options; whether a CasADi wheel exists for
   Python 3.12 on Windows (record the answer — T9 depends on it). Note any API that differs from what
   `architecture.md` assumes.
3. `core/units.py`: SI constants, `g` default, km/h↔m/s, W↔N·m/s helpers, degree↔radian. No runtime unit
   library (see §14).
4. `core/mathx.py`: the backend namespace of §5.2, numpy backend only for now, with `sign_smooth(v, eps)`
   as a documented primitive. CasADi backend is a stub that raises a clear `NotImplementedError` naming T9.
5. `core/results.py`: `Trajectory`, `Reactions`, `LimitMargins`, `ModeLog`, `RunSummary` exactly as the
   §5.5 schema, frozen dataclasses, with a `validate()` that checks array lengths agree. This is the
   load-bearing seam of the whole project — get the field names right and do not change them later.
6. `core/runio.py`: run-directory creation, `summary.json` writer with ordered keys and the full
   provenance block of §12.2, `.npz` writer/reader, and a `load_run(path)` round-trip.
7. `core/plotting.py`: shared styling, axis helpers, `save_figure`.
8. `cli/app.py`: the `bike` root Typer app with the five empty sub-apps and all global options from §9
   wired (`--config`, `--set`, `--out`, `--no-plot`, `--json`, `--log-level`, `--seed`);
   `cli/common.py` resolves preset-name-or-path, applies `--set` overrides, and creates the run dir.
9. Rewrite `README.md`: three apps, install via `uv sync`, one quickstart command per app, pointer to
   `architecture.md` and `latex/`.

**Done when:** `uv run bike --help` lists the five groups; `uv run pytest` passes; ruff and mypy are
clean; `runio` round-trips a synthetic `Trajectory`; the old modules are gone and the tree imports.

---

## T2 — Shared physics core: atmosphere, aero, road, pedal/brake inputs, limits, parameters

- **Module/boundaries:** `src/bikephysics/core/{params,atmosphere,aero,road,pedals,brakes,limits}.py`,
  `src/bikephysics/data/{bikes,roads}/*.toml`, `src/bikephysics/cli/spec_cmds.py`,
  `tests/unit/test_{params,atmosphere,aero,road,pedals,limits}.py`
- **Assignee:** developer
- **depends_on:** [T1]
- **parallel_safe:** false
- **Spec slice:** `architecture.md` §5.1, §5.3, §5.4, §9 (`bike spec` block);
  `latex/2d_models/models.tex` (all of it), `latex/2d_models/forces.tex`,
  `latex/appendix/atmosphere.tex`, `latex/appendix/notation.tex`,
  `latex/2d_assembly/wheels_ground.tex` §"Contact point" only

1. `core/params.py`: the full pydantic model tree of §5.1, frozen, every field carrying its LaTeX symbol in
   `Field(description=...)`, all validators listed there, derived properties `M`, `m_eq`, `gear_ratio`,
   `radii_from_teeth()`. TOML loading + dotted-path override application.
2. `core/atmosphere.py`: ISA, `rho(h, RH)` with the moist-air correction, `mu(T)` via Sutherland, plus a
   `ConstantAtmosphere`. Transcribe from `latex/appendix/atmosphere.tex`; validate against the standard
   sea-level values and one or two published ISA table rows (put the reference values in the test).
3. `core/aero.py`: `AeroModel` protocol returning `(A_1_x, A_1_z, A_1_m)` per `§aerodynamics-model`;
   `ConstantCdA` (the `beta_rel = 0` intercept only — all that is calibratable today), `Linearized`
   (all four slopes present, defaulting to zero), `TableLookup` stub raising `NotImplementedError`.
   `beta_rel` and `Re` computed and exposed even when unused, so the linearized path is testable.
4. `core/road.py`: the `Road` protocol and all implementations of §5.3, plus `contact_reference(...)`
   (warm-started Newton on the §5.3 residual with bracketed fallback) and `curvature_radius`.
   **Tests must include:** flat-road `x* == x_axle`; constant-grade `x*` against the closed-form answer;
   a sine road checked against a brute-force minimum-distance search; and a low-curvature-radius case
   asserting the warning fires.
5. `core/pedals.py`, `core/brakes.py`: the protocols and all implementations of §5.4. Each template must be
   non-negative everywhere and `P_F` must be `P_D` offset by `pi` — assert both in tests across the full
   crank revolution.
6. `core/limits.py`: the signed-margin forms of the `Limits` block of
   `latex/simplified/forward_motion.tex` (`traction_*`, `wheelie`, `stoppie`, `freewheel`), written so both
   tiers call the same functions. Sign convention: `<= 0` means satisfied.
7. Preset decks: `data/bikes/road_bike.toml` (carry over the current `BikeParameters` defaults, re-mapped
   to current symbols, with the missing fields — `l_1_a`, `h_1_a`, `l_1_B`, `l_1_C`, `h_1_C`,
   `r_*_bk`, `mu_s`, `mu_k` — filled with clearly-labelled plausible road-bike values), plus `bmx.toml`
   and `mtb.toml` as structurally complete but explicitly provisional. `data/roads/{flat,grade_5pct,
   rolling_sine,step_jump}.toml`.
8. `cli/spec_cmds.py`: `bike spec show|presets|validate`. `show` prints the symbol↔field table and derived
   quantities (`M`, `M_eq`, `G`, static load split placeholder).

**Done when:** `uv run bike spec show --config road_bike` prints a complete, correct table;
`bike spec validate` rejects a deck with `mu_k > mu_s` and with a negative mass; all road/pedal/atmosphere
tests pass.

---

## T3 — Simplified tier: model and analysis (pure functions, no I/O)

- **Module/boundaries:** `src/bikephysics/simplified/{model,analysis}.py`,
  `tests/unit/test_simplified_model.py`, `tests/analytic/test_simplified_analysis.py`
- **Assignee:** developer
- **depends_on:** [T2]
- **parallel_safe:** true *(disjoint from T5's files; both depend only on T2)*
- **Spec slice:** `architecture.md` §6.1, §6.2, §10 D-13; `latex/simplified/forward_motion.tex`
  **in full** — this ticket is a transcription of that one file

Transcribe `latex/simplified/forward_motion.tex` verbatim, in its own `ẑ`-down convention, with the single
named sign flip at the public boundary per D-13 option A.

1. `simplified/model.py`: the six functions of §6.1's table, each a direct transcription of a boxed
   equation, each docstring quoting the equation and naming its subsection. `accel` must be branch-free
   and written through `core.mathx` (`sigma` from `sign_smooth`) — it is the optimization hot path.
   Raise a clear error if `r_2a != r_2b` or `c_2a_rr != c_2b_rr`, which this derivation assumes.
2. `simplified/analysis.py`: the eight functions of §6.2's table.
3. **Mandatory assertions, not optional tests:** `E_M + E_k + E_P == 0` to 1e-12 across a grid of
   `(M, k, theta, P)`; the two gradient limits reproduce `E ≈ (0, -1/3, +1/3)` and `(-1, 0, +1)` in the
   respective asymptotic regimes; `climb_energy` is monotonically decreasing in `theta`;
   `steady_state_speed` inverted through `accel` returns zero acceleration; `normal_loads` sum to
   `-Mg cos(theta)` (doc convention) / `+Mg cos(theta)` (shared convention) at every acceleration;
   `wheelie_accel`/`stoppie_accel` are exactly the accelerations at which the respective normal load
   crosses zero in `normal_loads`.

**Done when:** every boxed equation in `forward_motion.tex` has a named function and a test, and the
self-consistency assertions above all hold. No file outside the two listed modules is touched.

---

## T4 — Simplified app: simulation driver, CLI, plots, examples  → **DELIVERABLE 1**

- **Module/boundaries:** `src/bikephysics/simplified/{simulate,plots}.py`,
  `src/bikephysics/cli/simplified_cmds.py`, `src/bikephysics/validation/energy.py`,
  `examples/0{1,2,3}_*.py`, `tests/{unit/test_simplified_simulate,cli/test_simplified_cli}.py`
- **Assignee:** developer
- **depends_on:** [T3]
- **parallel_safe:** false
- **Spec slice:** `architecture.md` §6.3, §9 (`bike simplified` block), §12.1 (first two rows),
  §12.3 (energy audit)

1. `simplified/simulate.py`: the `solve_ivp` driver of §6.3 — state `[x, v]`, non-terminal limit-crossing
   events, terminal events (`x >= x_target`, `v <= 0`, `t_end`), algebraic crank-angle reconstruction, and
   full post-processing into a `core.results.Trajectory` (reactions, `T`, spins, the six power channels,
   margins). Returns the shared schema, nothing tier-specific.
2. `validation/energy.py`: the energy-balance audit of §12.3, written against `Trajectory` so it serves
   both tiers. Pedal work in must equal KE + PE change + aero + rolling + brake dissipation to < 0.1 % for
   the Simplified tier.
3. `simplified/plots.py`: extend the migrated 6-panel figure into the panel set the current derivation
   needs — add normal-load split, traction vs. friction-cone bound, chain tension with the `T >= 0` line,
   limit margins, and the power split. Keep the multi-run overlay. Shared styling from `core/plotting.py`.
4. `cli/simplified_cmds.py`: the five commands of §9 (`run`, `steady`, `sweep`, `limits`, `sensitivity`),
   each writing a run dir and honouring `--json` and `--no-plot`. `sensitivity` prints the elasticity
   table, the identity check, the dominance ratio, and the size-elasticity result.
5. `examples/01_simplified_baseline.py`, `02_simplified_limits.py`, `03_simplified_sensitivity.py` —
   replacing the deleted `main.py` functions, each runnable with `uv run python examples/...`.

**Done when:** `uv run bike simplified run --config road_bike` produces a complete run dir; the simulated
terminal speed matches `steady_state_speed` to < 1e-4 m/s; the energy audit closes to < 0.1 %;
a standing start with zero initial pedal force integrates without stalling (the old `RampForce` footgun,
designed out by `sign_smooth`); all five commands have a CLI smoke test.

---

## T5 — Planar engine core: bodies, constraints, KKT solver, integration (no contact, no drivetrain)

- **Module/boundaries:** `src/bikephysics/planar/{state,body,system,solver,integrate}.py`,
  `src/bikephysics/planar/forces/{base,weight,aerodynamic}.py`,
  `src/bikephysics/planar/constraints/{base,revolute,weld}.py`,
  `tests/{unit/test_planar_core,analytic/test_planar_analytic}.py`
- **Assignee:** developer
- **depends_on:** [T2]  *(plus sign-off on D-8)*
- **parallel_safe:** true *(disjoint from T3's files)*
- **Spec slice:** `architecture.md` §7.1, §7.2, §7.3 (first three rows only), §7.4 (revolute rows only),
  §7.7, §10 D-8; `latex/2d_models/rigidbody.tex`, `latex/2d_models/forces.tex`,
  `latex/2d_systems/overview.tex`, `latex/2d_assembly/frame_wheels.tex`

Build the generic engine and prove it on problems with analytic answers. **No bike, no contact, no
drivetrain in this ticket** — if a bike-specific assumption leaks into `system.py` or `solver.py`, the
extensibility requirement is already broken.

1. `planar/state.py`: `SystemLayout` — body→slice and constraint→row maps, named accessors, relative-spin
   derivation (`phi_k = theta_k - theta_1`), and the `jac_sparsity` pattern for Radau. **All index
   arithmetic lives here and nowhere else.**
2. `planar/body.py`: `RigidBody` — mass properties, body-frame named points, rotation matrix,
   `world_point`, `point_velocity`, and the `apply_wrench` helper of §7.3.
3. `planar/forces/base.py`, `planar/constraints/base.py`: the two protocols of §7.3 and §7.4, exactly as
   specified (signatures matter — T6 and T7 implement against them).
4. `Weight`, `Aerodynamic`; `RevoluteJoint`, `WeldJoint`.
5. `planar/system.py`: `MultibodySystem` — element registry, constant block-diagonal `M`, additive `h`
   assembly, active-constraint `J` and `zeta` assembly with Baumgarte terms.
6. `planar/solver.py`: the index-1 KKT saddle-point solve of §7.7 steps 1–6, including the generic
   unilateral trial loop (it will be used by the chain in T7 and by contact normals in T11 — write it
   generically now, exercise it with a dummy unilateral constraint in tests).
7. `planar/integrate.py`: `solve_ivp` driver (Radau default, RK45/BDF/LSODA selectable), event plumbing,
   drift reporting (`max|c|`, `max|J u|`) with a configurable failure bound, optional post-step projection,
   and `ModeLog` accumulation.
8. **Analytic acceptance tests** (this is the ticket's real deliverable):
   - a single free body under gravity vs. `z = z0 - ½gt²` and `theta` constant;
   - a simple pendulum (one body + one `RevoluteJoint` to ground) vs. the small-angle period and against
     a high-accuracy reference integration of the 1-DOF minimal-coordinate ODE;
   - a double pendulum, for energy conservation over 60 s (< 0.1 % drift) and for constraint residual
     staying < 1e-8;
   - two bodies welded together behaving exactly as one body of the combined mass properties;
   - the multiplier of a revolute joint supporting a static body equals the analytic reaction force.

**Done when:** all five analytic tests pass, constraint drift stays within bound without projection on the
pendulum cases, and `system.py`/`solver.py` contain no reference to wheels, roads, or bikes.

---

## T6 — Planar ground contact: geometry + regularized contact model

- **Module/boundaries:** `src/bikephysics/planar/contact/{geometry,regularized}.py`,
  `src/bikephysics/planar/forces/contact.py`, `src/bikephysics/planar/constraints/rolling.py`,
  `tests/{unit/test_contact_geometry,analytic/test_contact_regularized}.py`
- **Assignee:** developer
- **depends_on:** [T5]  *(plus sign-off on D-9)*
- **parallel_safe:** false
- **Spec slice:** `architecture.md` §7.6 (all of it, esp. §7.6.1 and §7.6.2), §12.1 (planar rows),
  §12.3; `latex/2d_assembly/wheels_ground.tex` **in full**

1. `contact/geometry.py`: the shared geometry block of §7.6.1 — warm-started `x*`, `g_N`, `A b_k` with the
   rolling-resistance offset, smoothed `sigma`, contact-point velocity, `v_slip` (with the `l_rr` term
   dropped from the slip arm only, as `wheels_ground.tex` prescribes), and a `ContactState` record.
2. `contact/regularized.py`: the `ContactModel` protocol plus `RegularizedContact` implementing (7.12) and
   (7.13). **Stiffness and damping must be derived from `delta_target` and `zeta_N`** per §7.6.2 — a
   literal `k_N = 1e6` anywhere in this file is a defect. `constraints/rolling.py` implements
   `RollingContact` for T11's use but is not instantiated here.
3. `forces/contact.py`: the thin bridge applying contact wrenches, with the `x*` warm-start cache.
4. **Analytic/physical acceptance tests:**
   - a single wheel dropped onto flat ground settles to `delta_static = M g / k_N` with the expected
     damped-oscillation decay, and the steady normal force equals the weight;
   - a wheel rolling on flat ground with no applied torque decelerates at exactly `c_rr g` (to within the
     creep tolerance), i.e. the rolling-resistance offset reproduces the Simplified tier's rolling term;
   - a wheel on a grade steeper than `atan(mu_s)` slides; shallower, it rolls with creep below a stated
     bound;
   - a wheel given spin with the brake locked skids with `|R_x| = mu_k R_z`;
   - a wheel launched off `step_jump` has exactly zero contact force during flight and non-negative `R_z`
     at all times in every test (`R_z >= 0` is a hard invariant — assert it everywhere);
   - cumulative creep distance and the friction-dissipation budget are reported and bounded.
5. Record in `architecture-notes.md` the `delta_target`/`zeta_N`/`v_eps`/`v_stribeck` values that actually
   worked, with the resulting wall times — T7 and T10 need them.

**Done when:** all the above pass; a 60 s single-wheel rolling run completes in < 2 s; and every
regularization parameter appears in the run summary.

---

## T7 — Lumped drivetrain + full bike assembly + planar app  → **DELIVERABLE 2**

- **Module/boundaries:** `src/bikephysics/planar/{drivetrain,assembly,kinematics,plots,animate}.py`,
  `src/bikephysics/planar/constraints/gear.py`,
  `src/bikephysics/planar/forces/{pedal,brake,bearing}.py`,
  `src/bikephysics/cli/planar_cmds.py`, `src/bikephysics/validation/cross_tier.py`,
  `examples/0{4,5}_planar_*.py`, `tests/{unit/test_drivetrain,crosstier/test_planar_vs_simplified,
  cli/test_planar_cli}.py`
- **Assignee:** developer
- **depends_on:** [T6]  *(plus sign-off on D-10, D-10a, D-10b)*
- **parallel_safe:** false
- **Spec slice:** `architecture.md` §7.3 (pedal/brake/bearing rows), §7.4 (`IdealChain` row),
  **§7.5 in full** (the equations to implement), §7.8, §9 (`bike planar` block), §10 D-10/D-10a/D-10b;
  `latex/2d_systems/{frame,wheel}.tex`, `latex/2d_assembly/frame_wheels.tex`,
  `latex/2d_models/models.tex` §pedal-force-model and §bearing-friction-model

**This ticket implements new physics.** Do not start before D-10 is approved. Implement exactly §7.5; if
anything there seems wrong, stop and report rather than improvising — the model is the user's to decide.

1. `constraints/gear.py`: `IdealChain` implementing (7.5)–(7.6), `kind="unilateral"`,
   `multiplier_bounds=(0, inf)`. Assert the two sanity properties of §7.5.2 as tests: zero generalized
   force under rigid whole-bike rotation, and the correct drive/resist sign of the two sprocket torques.
2. `forces/pedal.py`: the action/reaction pair per §7.3 and (7.7)–(7.9) — **both halves applied**, on `3b`
   and on `1`. Use `x̂_3b` for the crank axis (see `architecture.md` §11 item 1).
3. `forces/brake.py`: wheel torque **and** the equal-opposite caliper torque on body `1` (§7.3).
4. `forces/bearing.py`: `BearingFriction` with the one-step multiplier lag, defaulting to `mu_b = 0` (off).
5. `planar/drivetrain.py`: assembles body `3b` + `RevoluteJoint(1, 3b, C)` + `IdealChain` + the freewheel
   mode logic of §7.5.4, including the plastic engagement impulse and its `ModeLog` entry with energy loss.
6. `planar/assembly.py`: `build_bike(spec, road, pedal_input, brake_input, contact_model)` → the 12-DOF /
   7-constraint system of §7.2. The *only* place bike topology is hard-coded.
7. `planar/kinematics.py`: the consistent initial-condition solver of §7.8, with the flat-ground analytic
   seed and the residual/static-load report.
8. `planar/plots.py`: per-body states, ground reactions in frame 0 with the friction-cone envelope, chain
   tension with mode shading, limit margins, power split, constraint-residual trace.
   `planar/animate.py`: the §10 D-16 replay — bike geometry on the road with contact points, force
   vectors, and mode shading.
9. `validation/cross_tier.py`: the **acceptance gate** of §7.5.6 —
   - the planar model on flat ground reproduces `simplified.analysis.steady_state_speed` to within
     integration tolerance for several power levels and grades;
   - `IdealChain`'s multiplier reproduces `simplified.model.chain_tension` to within tolerance during a
     constant-force acceleration;
   - the reflected-inertia identity `I_3b/(G² r_2a²) == I_3b r_2a_cg²/(r_3b_cr² r_2a²)` holds exactly;
   - the planar static normal-load split matches `simplified.model.normal_loads` at `x_ddot = 0`;
   - the planar front normal load crosses zero at `simplified.analysis.wheelie_accel` (and rear at
     `stoppie_accel`) to within a stated tolerance.
10. `cli/planar_cmds.py`: the five commands of §9 (`run`, `initcheck`, `animate`, `crosscheck`, `modes`).
11. `examples/04_planar_rolling_road.py`, `05_planar_vs_simplified.py`.

**Done when:** every item of (9) passes — that is the ticket's completion gate, not the CLI running; a 30 s
run on `rolling_sine` completes in < 5 s; the energy audit closes to < 1 %; a coasting run and a braking
run both behave correctly (chain slack, load transfer forward); and the animation visibly shows the
contact point tracking the road.

---

## T8 — Optimization software, milestone 1: parametrization + scipy on the Simplified model  → **DELIVERABLE 3**

- **Module/boundaries:** `src/bikephysics/optim/{forward,parametrization,objectives,constraints,problem,
  report}.py`, `src/bikephysics/optim/solvers/{base,scipy_local,scipy_global,analytic}.py`,
  `src/bikephysics/cli/optim_cmds.py`, `src/bikephysics/data/problems/*.toml`,
  `examples/06_optimize_sprint.py`, `tests/{unit/test_optim_*,analytic/test_optim_analytic,
  cli/test_optim_cli}.py`
- **Assignee:** developer
- **depends_on:** [T4]  *(NOT T7 — this deliverable only needs the Simplified tier)*
- **parallel_safe:** true *(disjoint from T5/T6/T7 files; can run in parallel with the planar track)*
- **Spec slice:** `architecture.md` §8 (all of it), §9 (`bike opt` block), §10 D-11

1. `optim/forward.py`: the `ForwardModel` protocol and `SimplifiedForwardModel`. `PlanarForwardModel` is a
   stub raising a clear error naming T10 — the protocol must be designed for it now, implemented later.
2. `optim/parametrization.py`: all six implementations of §8.2 plus `Composite`.
3. `optim/objectives.py`, `optim/constraints.py`: §8.3 in full. `PedalForceBound` and `PowerCeiling` take
   their limits from config — **no hardcoded force or power number anywhere** (`latex/appendix/
   refinements.tex` records that these bounds are unspecified). `PowerCeiling` accepts either a scalar or a
   `P_max(cadence)` callable.
4. `optim/problem.py`: the pydantic problem model and the TOML loader of §8.5; the three shipped problem
   decks.
5. `optim/solvers/`: `ScipyLocal` (L-BFGS-B / SLSQP / trust-constr + multi-start via
   `ProcessPoolExecutor`), `ScipyGlobal` (differential_evolution, dual_annealing), and
   `AnalyticSimplified` — the closed-form/Pontryagin optimum for the flat, constant-power, limit-free
   min-time case, whose **only purpose is to be the oracle the numeric solvers are tested against.**
6. `optim/report.py`: convergence history, optimal control vs. baseline, active-constraint report,
   limit-margin trace of the optimum.
7. `cli/optim_cmds.py`: `bike opt solve|compare|baseline`.
8. **Acceptance tests:** the numeric solvers reproduce `AnalyticSimplified` to < 0.5 % on the oracle
   problem; on `min_time_1km` the optimum beats a constant-force baseline of equal total energy; every
   active constraint is satisfied to solver tolerance in the returned optimum; a deliberately infeasible
   problem (power ceiling below what the grade requires) fails with a clear diagnostic rather than
   returning a plausible-looking answer.

**Done when:** `uv run bike opt solve --problem min_time_1km` converges and writes a run dir with figures;
`bike opt compare` overlays local vs. global vs. analytic; the oracle test passes.

---

## T9 — Optimization milestone 2: CasADi direct collocation on the Simplified model

- **Module/boundaries:** `src/bikephysics/core/mathx.py` (CasADi backend only),
  `src/bikephysics/optim/solvers/casadi_collocation.py`, `pyproject.toml` (`opt` extra),
  `tests/analytic/test_casadi_collocation.py`, `architecture-notes.md`
- **Assignee:** developer
- **depends_on:** [T8]  *(plus sign-off on D-12)*
- **parallel_safe:** false
- **Spec slice:** `architecture.md` §5.2, §8.4 (CasADi rows), §10 D-12, §15 (CasADi risk rows)

**Gate first:** verify the CasADi Windows/Python 3.12 wheel actually installs (T1 recorded the research;
confirm it empirically). If it does not, stop and report — D-12 falls back to option D and this ticket is
cancelled rather than worked around.

1. Complete the CasADi backend in `core/mathx.py`. `simplified/model.py` must then run unchanged under it
   — if it does not, the fix belongs in `mathx.py`, not in `model.py`.
2. `casadi_collocation.py`: Radau collocation (3rd order, `N` configurable 50–200), states and controls as
   NLP variables, dynamics as equality constraints, path constraints native, free final time via time
   scaling, IPOPT. Document the variable scaling used — it is the difference between converging and not.
3. **Acceptance tests:** matches `AnalyticSimplified` to < 0.1 % on the oracle problem (tighter than T8's
   gradient-free solvers, since this one has exact derivatives); matches T8's `ScipyLocal` optimum to
   < 1 % on `min_time_1km`; solves a 200-interval problem with all path constraints active in < 60 s.
4. Record in `architecture-notes.md`: the CasADi version, the IPOPT options used, the scaling scheme, and
   the wall times.

**Done when:** the three acceptance tests pass and `bike opt compare --solvers scipy-local,casadi` produces
a like-for-like comparison. `uv sync` without `--extra opt` must still leave all three apps fully working.

---

## T10 — Optimization milestone 3 (stretch): optimize against the planar model

- **Module/boundaries:** `src/bikephysics/optim/forward.py` (`PlanarForwardModel`),
  `src/bikephysics/planar/*` (making the assembly `xp`-parametric — read-only elsewhere),
  `tests/crosstier/test_optim_planar.py`
- **Assignee:** developer
- **depends_on:** [T7, T9]
- **parallel_safe:** false
- **Spec slice:** `architecture.md` §8.1, §8.4 (last row), §15 (CasADi-portability risk)

Two stages, the first of which must work before the second is attempted:

1. **Gradient-free first (low risk, high value).** Implement `PlanarForwardModel` and run T8's
   `ScipyGlobal`/`ScipyLocal` with `TemplateShape` (2–4 variables) against the planar model. This alone
   satisfies "reusable against the 2D model later" and needs no CasADi. Verify the optimum is close to the
   Simplified-model optimum on flat ground — a large gap means either a modeling bug or a genuine 2D
   effect, and distinguishing the two is the test's point.
2. **CasADi through the planar dynamics (research task).** Make the force/constraint assembly `xp`-generic,
   express the KKT solve as `casadi.solve`, and handle the foot-of-normal iteration with
   `casadi.rootfinder` — or, failing that, restrict to roads with closed-form `x*` (flat, constant grade)
   and say so. **Time-box this.** If it does not converge, report the finding and stop: stage 1 is the
   deliverable, stage 2 is the bonus.

**Done when:** stage 1 produces a planar-model optimum with a report, and stage 2 either works (with
gradient verification against finite differences) or is documented as not working, with the reason.

---

## T11 — Event-driven exact contact (fidelity reference) + impact law

- **Module/boundaries:** `src/bikephysics/planar/contact/event_driven.py`,
  `src/bikephysics/planar/constraints/rolling.py`, `src/bikephysics/planar/integrate.py` (mode-restart
  loop), `tests/analytic/test_contact_event_driven.py`
- **Assignee:** developer
- **depends_on:** [T7]
- **parallel_safe:** true *(disjoint from T8/T9 files)*
- **Spec slice:** `architecture.md` §7.6.3, §10 D-9 (option B), §15 (chattering risk);
  `latex/2d_assembly/wheels_ground.tex` §"Contact reaction" and §"Rolling constraint"

Implement the document's exact hybrid contact model as the fidelity reference: per-wheel modes
`{airborne, stick, slip+, slip-}`, mode-local index-1 DAEs, the four event functions of §7.6.3, Newton
restitution plus a cone-bounded tangential impulse at touchdown, and hysteresis bands against chattering.

**Acceptance tests:** exact zero slip in stick (machine precision, not creep); an energy-conserving
bounce reproduces the restitution coefficient; stick→slip occurs at exactly the cone boundary; and — the
real deliverable — a **quantified comparison table** against `RegularizedContact` on three scenarios
(steady roll, hard acceleration near the traction limit, jump landing), reporting the divergence in speed,
reaction forces, and wall time. That table is what tells the user when the cheap default is good enough.

**Done when:** the comparison table exists in the run artifacts and the divergence on steady rolling is
within the creep bound predicted in §7.6.2.

---

## T12 — Validation suite, golden regressions, and LaTeX port-back

- **Module/boundaries:** `src/bikephysics/validation/{cross_tier,energy}.py` (extend),
  `src/bikephysics/validation/golden/*.json`, `src/bikephysics/cli/validate_cmds.py`,
  `latex/2d_systems/drivetrain.tex` (**new**), `latex/main.tex` (one `\input` line),
  `tests/crosstier/*`, `README.md`
- **Assignee:** developer
- **depends_on:** [T7, T8]
- **parallel_safe:** false
- **Spec slice:** `architecture.md` §7.5 (the equations to port), §11 (the doc findings), §12.3;
  `latex/main.tex` line ~126, `latex/2d_systems/wheel.tex` (for style and heading conventions)

1. `bike validate all`: one command running the whole guarantee set — analytic tests, cross-tier
   agreement, energy audits, constraint-residual bounds, and golden-summary comparison against committed
   `validation/golden/*.json` with physics-scaled tolerances (never exact float equality). Target
   wall time < 3 min (§12.1).
2. `bike validate energy --run <dir>`: the audit applied to any existing run.
3. **LaTeX port-back.** Write `latex/2d_systems/drivetrain.tex` containing the approved §7.5 model, in the
   document's own style and conventions: Assumptions / Geometry / Forces / Equations of motion
   subsubsubsections, `latex/appendix/notation.tex` symbol grammar, `\begin{aiblock}` for AI-written prose
   per the `main.tex` preamble convention, and the §7.5.6 reduction shown explicitly as the consistency
   link to `\S\ref{sec:simplified}`. Wire it into `main.tex`'s currently-empty
   `\subsection{Drivetrain}`. **Do not** fix any other `latex/` issue in this ticket — §11's findings are
   the user's to rule on, and this ticket touches only the drivetrain subsection plus one `\input`.
   Per the project's documentation rules: no self-referential or history-narrating prose, and state the
   rules plainly.
4. Update `README.md` with the validation story and the three apps' quickstarts.

**Done when:** `uv run bike validate all` is green; `latex/main.tex` compiles with the new drivetrain
section; and the ported section's equations are character-for-character consistent with the implemented
code's docstrings.

---

## T13 — Extensibility proof + developer documentation (stretch)

- **Module/boundaries:** `examples/07_extend_add_body.py`, `docs/extending.md`,
  `tests/unit/test_extension_example.py`
- **Assignee:** developer
- **depends_on:** [T7]
- **parallel_safe:** true
- **Spec slice:** `architecture.md` §13 (the seam table), §7.3, §7.4

Prove the extensibility claim instead of asserting it. Pick **one** item from §13 — recommended: a front
suspension fork (new `RigidBody` + prismatic/revolute joint + spring-damper force element), since it is
`latex/timeline.tex`'s 2D build-order step 3 — and implement it **entirely in `examples/` and new
`forces/`/`constraints/` files, touching zero lines of `planar/system.py`, `planar/solver.py`, or
`planar/integrate.py`.** A diff that touches the solver core is a failed ticket and a finding to report
against the architecture.

Then write `docs/extending.md`: how to add a body, a force element, a constraint, a contact model, a road,
a pedal model, an objective, and a solver — one worked snippet each, with the §13 seam table as the index.

**Done when:** the suspension example runs, the solver-core diff is empty, and `docs/extending.md` covers
all eight seams.

---

## Dependency graph

```
T1 ──► T2 ──┬──► T3 ──► T4 ──────────────► T8 ──► T9 ──┐
            │                               │          │
            └──► T5 ──► T6 ──► T7 ──────────┼──────────┴──► T10
                                │           │
                                ├──► T11    │
                                ├──► T12 ◄──┘
                                └──► T13
```

Parallelizable pairs (disjoint file boundaries, both ready after their dependency):
**T3 ∥ T5** (after T2) and **T8 ∥ {T6, T7}** (after T4) and **T11 ∥ T13 ∥ T9** (after T7/T8).
Everything else is sequential. Deliverable completion points: **T4** (Simplified app), **T7** (2D app),
**T8** (Optimization app).
