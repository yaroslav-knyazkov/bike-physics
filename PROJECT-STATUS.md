# bike-physics — Project Status

## What this is

A solo research/engineering project, not a multi-stakeholder product. `latex/` holds the physics
derivation (Simplified tier fully worked, 2D tier in progress). This tracker covers the new software
work: three applications built on top of that physics — a Simplified-tier simulator, a 2D planar
multibody simulator, and optimization software — per the user's request to "spend tokens on the
simulation" now that the LaTeX is in a good state.

Given the nature of the project (no external users, the LaTeX *is* the requirements spec), the
`vision` / `product-manager` / `product` pipeline stages were deliberately skipped — see
`status.json`'s `modules_skipped` for the reasoning. `architect`, `developer`, `qa`, and
`documentation` are the active modules.

## STATUS: all three requested deliverables complete (T1-T8 done; T9-T13 optional, not started)

## Current stage: Implementation

- [x] Architecture designed (`architecture.md`) and sequenced into tickets (`task.md`)
- [x] All 16 stack/physics decision points (D-1..D-16) presented to the user as comparison tables and
      approved — see `status.json`'s `decisions_log` for the full record
- [x] T1 — Repository scaffolding, packaging, shared-core skeleton (commit f02cf68)
- [x] T2 — Shared physics core (atmosphere, aero, road, pedal/brake inputs, limits, parameters) (commit 2518617)
- [x] T3 — Simplified-tier model + analysis (commit 02b7b8d)
- [x] T4 — Simplified-tier CLI app (commit d2b20f2) -- **DELIVERABLE 1 (Simplified app) COMPLETE**
- [x] T5 — 2D engine core (bodies, constraints, solver) (commit 6ee3543)
- [x] T6 — 2D ground contact (regularized, default) (commit cdfc3cd; two architecture.md Sec7.6.2 erratum
      fixes found and documented, see status.json)
- [x] T7 — 2D lumped drivetrain + full 2D app (commit 8577ced) --
      **DELIVERABLE 2 (2D app) COMPLETE** (four architecture.md sign-convention errata found and fixed,
      see status.json)
- [x] T8 — Optimization software, milestone 1: scipy-based control parametrization (commit a7dd897) --
      **DELIVERABLE 3 (Optimization software) COMPLETE**

**All three originally requested deliverables are done.** 548 tests pass project-wide, ruff/mypy clean
across 110 source files. Remaining tickets are optional stretch/fidelity/future-proofing work, not yet
started, pending user direction:

- [ ] T9 — Optimization milestone 2: CasADi direct collocation (optional extra)
- [ ] T10 — Optimization against the 2D model (stretch)
- [ ] T11 — Event-driven exact contact model (fidelity reference for T6)
- [ ] T12 — Port the corrected lumped drivetrain model + sign-convention fixes back into
      `latex/2d_systems/drivetrain.tex` for user review
- [ ] T13 — Remaining polish/future-proofing per architecture.md §13

Full ticket detail, module boundaries, and spec slices: `task.md`. Full architecture rationale and all
16 decision tables: `architecture.md` §10.

## Known spec findings (informational, not blocking — see `status.json`)

A handful of small inconsistencies in `latex/` were surfaced during architecture design (a likely
`x̂_2b`→`x̂_3b` typo in `frame.tex`, the intentionally-empty `Drivetrain` placeholder in `main.tex`, an
"Cassette modeled today" inconsistency in `overview.tex`, and two pre-existing open `\todo{}`s in
`notation.tex`). None block implementation; flagged for whenever the user next touches the LaTeX.

## Next step

Dispatch `developer` for ticket T1 (scaffolding — no physics decisions in it, safe to start
immediately).
