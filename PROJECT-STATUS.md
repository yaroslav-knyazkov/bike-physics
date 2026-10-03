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

## Current stage: Implementation

- [x] Architecture designed (`architecture.md`) and sequenced into tickets (`task.md`)
- [x] All 16 stack/physics decision points (D-1..D-16) presented to the user as comparison tables and
      approved — see `status.json`'s `decisions_log` for the full record
- [ ] T1 — Repository scaffolding, packaging, shared-core skeleton (next)
- [ ] T2 — Shared physics core (atmosphere, aero, road, pedal/brake inputs, limits, parameters)
- [ ] T3 — Simplified-tier model + analysis
- [ ] T4 — Simplified-tier CLI app (**Simplified app complete after this ticket**)
- [ ] T5 — 2D engine core (bodies, constraints, solver)
- [ ] T6 — 2D ground contact (regularized, default)
- [ ] T7 — 2D lumped drivetrain + full 2D app (**2D app complete after this ticket**; hard gate: must
      reproduce the Simplified tier's boxed chain-tension/M_eq equations, §7.5.6 of architecture.md)
- [ ] T8 — Optimization software, milestone 1: scipy-based control parametrization (**Optimization app
      complete after this ticket**; depends only on T4, not on the 2D engine)
- [ ] T9 — Optimization milestone 2: CasADi direct collocation (optional extra)
- [ ] T10 — Optimization against the 2D model (stretch)
- [ ] T11 — Event-driven exact contact model (fidelity reference for T6)
- [ ] T12 — Port the lumped drivetrain model back into `latex/2d_systems/drivetrain.tex` for user review
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
