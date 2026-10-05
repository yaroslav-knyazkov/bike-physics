# architecture-notes.md

Cached library-version facts, gathered during T1's version-verification pass (architecture.md S2,
the `>` callout right before S3) so later tickets do not re-research them. Verified against the
versions `uv` actually resolves for this project (see `uv.lock`), using local introspection
(`pip index versions`, `inspect.signature`, a scratch `pip download`) since Context7 was not reachable
from this session either (same situation the architect's note already flagged).

## (a) `scipy.integrate.solve_ivp` -- Radau/BDF, `events`, `jac_sparsity`, `dense_output`

Installed: **scipy 1.18.1** (also the latest on PyPI at verification time; `pyproject.toml` pins
`scipy>=1.18`).

`solve_ivp`'s public signature (`scipy.integrate._ivp.ivp.solve_ivp`) is unchanged from what
`architecture.md` assumes:

```
solve_ivp(fun, t_span, y0, method='RK45', t_eval=None, dense_output=False,
          events=None, vectorized=False, args=None, **options)
```

`jac_sparsity` is **not** a top-level `solve_ivp` parameter; it is a method-specific option consumed
through `**options` and only honored by the implicit methods. Confirmed on the `Radau` step class
itself (`scipy.integrate._ivp.radau.Radau.__init__`):

```
Radau(fun, t0, y0, t_bound, max_step=inf, rtol=1e-3, atol=1e-6, jac=None,
      jac_sparsity=None, vectorized=False, first_step=None, **extraneous)
```

`BDF` takes the same `jac`/`jac_sparsity` pair. Practical consequence for `planar/integrate.py` (T5-T7):
call `solve_ivp(..., method="Radau", jac_sparsity=<sparsity pattern>, **other_radau_kwargs)` -- scipy
forwards unrecognized top-level kwargs straight to the method class, so this works without any extra
plumbing, but `jac_sparsity` only has an effect when `method` is `"Radau"` or `"BDF"`; passing it with
`"RK45"` is silently ignored (good to know so a wrong `method` default doesn't look like a sparsity bug).
`dense_output` and `events` are exactly as documented in `architecture.md` S7.7 -- no surprises.

## (b) pydantic v2 -- `ConfigDict(frozen=True)` + `model_validate`

Not yet installed locally (pydantic isn't a dependency until `uv sync` runs); checked via
`pip index versions pydantic` (latest/resolvable: **2.13.5**; `pyproject.toml` pins `pydantic>=2.9`,
comfortably inside the stable v2 API surface that has not changed across that range). The
`model_config = ConfigDict(frozen=True)` + `BikeSpec.model_validate(data)` pattern `architecture.md`
S5.1/S10-D4 assumes is the documented, stable pydantic v2 API; nothing additional to flag until T2
actually writes `core/params.py` and can confirm field-level behavior (validators, `model_validate`
error shapes) against the installed version.

## (c) Typer -- sub-app composition and the global callback

Latest/resolvable: **typer 0.27.2** (`pyproject.toml` pins `typer>=0.15`). `cli/app.py` (this ticket)
uses `typer.Typer(no_args_is_help=True)` plus five `app.add_typer(sub_app, name=...)` calls and an
`@app.callback()` that stores parsed global options on `ctx.obj` -- the standard, still-current
composition pattern. One thing worth recording for T2+: Typer's `Annotated[...,
typer.Option(...)]` style (used throughout `cli/app.py`) is the modern, non-deprecated way to declare
options; the old `param: str = typer.Option(...)` default-value style still works but is being phased
out in Typer's own docs, so later tickets (`simplified_cmds.py`, `planar_cmds.py`, etc.) should keep
using `Annotated`.

## (d) CasADi -- Windows wheel for Python 3.12

**Confirmed available.** `pip index versions casadi` resolves **casadi 3.8.1**, and a scratch
`pip download casadi --no-deps --python-version 312 --platform win_amd64 --only-binary=:all:` fetched:

```
casadi-3.8.1-cp311-abi3-win_amd64.whl
```

The `cp311-abi3` tag means this wheel targets CPython's stable ABI from 3.11 onward, so it is valid
for Python 3.12 (and later 3.x) on Windows without a separate 3.12-specific build. **This un-gates
T9**: `[project.optional-dependencies] opt = ["casadi"]` can be installed with `uv sync --extra opt`
on this machine (Windows, Python 3.12) with no compilation and no missing-wheel fallback needed.
IPOPT ships bundled inside this wheel per CasADi's own packaging (not independently re-verified here
beyond the architecture doc's existing claim, since this ticket does not install the `opt` extra).

## Summary for later tickets

- T5-T7 (planar `solve_ivp` integration): use `jac_sparsity` only with `method="Radau"`/`"BDF"`; it is
  silently ignored otherwise -- do not rely on it for `RK45` smoke tests.
- T9 (CasADi): the Windows/Python-3.12 wheel question is resolved yes; no alternate-platform
  workaround or source build is needed for this machine.
- No API in `architecture.md` was found to disagree with the installed/resolvable versions above; no
  revisions to S2/S7.7/S8/S9 are needed as a result of this pass.

## (e) T6 -- `RegularizedContact` (7.12): two corrections flagged for architect sign-off

Both are implemented (corrected) in `planar/contact/regularized.py`, with the full derivation in that
file's module docstring; summarized here for T7/T10's benefit and as the explicit architect flag the
T6 ticket asked for.

1. **Sign.** Architecture.md S7.6.2 writes `R_z = max(0, k_N*delta^e*(1 + 1.5*c_HC*g_N_dot))`. Taken
   literally, this linearizes (about the static equilibrium `delta_target`) to a characteristic equation
   with a *negative* damping coefficient (one stable, one unstable root) -- not the decaying oscillation
   the ticket's own acceptance test requires. The standard Hunt-Crossley form uses the *penetration* rate
   (positive while compressing), not the *gap* rate; since `delta_dot = -g_N_dot` while touching, the
   correct sign is `(1 - 1.5*c_HC*g_N_dot)`. Implemented with the minus sign; confirmed by
   `tests/analytic/test_contact_regularized.py::test_dropped_wheel_settles_to_delta_target_with_damped_decay`.
2. **`c_HC` scaling.** `c_HC = c_N/k_N` (as literally written) makes the Hunt-Crossley damping *coefficient*
   at the operating point `delta ~= delta_target` equal to `1.5*zeta_N*delta_target` -- with `delta_target`
   a small SI length (1-3 mm), this is `zeta_N` scaled down by ~1e-3, i.e. negligible: a system with
   `zeta_N` in the documented 0.7-1.0 range shows no visible decay within 5 s of simulated time if `c_HC`
   is left as `c_N/k_N`. Rescaling to `c_HC = c_N / (1.5 * k_N * delta_target)` makes the *effective*
   damping ratio at `delta == delta_target` equal exactly `zeta_N`, which is the only reading under which
   `zeta_N` means what `core.params.ContactParams`'s own docstring says it means. Implemented with this
   rescaling.

**Regularization parameters that worked** (`tests/analytic/test_contact_regularized.py`, recorded here
per architecture.md S12.2 / the T6 ticket's own instruction, for T7/T10 to reuse rather than re-tune):

| Parameter | Value used | Notes |
|---|---|---|
| `delta_target` | 0.001 m (1 mm) | lower end of the documented 1-3 mm range |
| `zeta_N` | 0.3 for the decay-shape test (visibly underdamped); 0.7 works equally well for settling-time-only checks | S7.6.2's documented range is 0.7-1.0; 0.3 was chosen *for the test* specifically to make the decaying-oscillation signature visually/numerically unambiguous (more peaks before settling) |
| `v_eps` | 0.001 m/s | smaller than S7.6.2's example (1e-3 m/s, matches) |
| `v_stribeck` | 0.01 m/s | matches S7.6.2's example |
| `mu_s` / `mu_k` | scenario-dependent (0.5-0.9 / 0.6-0.8) | always `mu_k <= mu_s`, per `ContactParams`'s own validator |

**Wall-clock times measured** (this machine, Python 3.12.10, scipy 1.18.1, `method="Radau"`):
- 60 s single-wheel rolling run, 2000 output points: **0.23 s** (ticket's "Done when" bound: < 2 s).
- 0.6 s single-wheel drop-and-settle run, 3000 output points, `rtol=1e-9`/`atol=1e-12`: ~1 s.

**A third, non-architecture-text finding, for test design only (not a code defect):** the naive
"slides above `atan(mu_s)`" grade threshold from `wheels_ground.tex`'s informal phrasing is the
*sliding-block* threshold, not the *rolling-wheel* one. A wheel with rotational inertia `I` needs only
`tan(theta) <= mu_s*(1 + m*r^2/I)` to roll without slipping -- substantially steeper than `atan(mu_s)`
for any realistic wheel inertia (a rolling wheel resists slipping much better than a sliding block,
since only a fraction of the gravity component needs to react through the contact tangentially). T6's
`test_shallow_grade_rolls_with_bounded_creep`/`test_steep_grade_slides` use this corrected threshold;
T7's cross-tier validation should use it too if it ever tests grade-induced slip on the full bike.

**Also recorded:** "a wheel rolling on flat ground ... decelerates at exactly `c_rr*g`" (task.md T6)
only holds in the point-mass-wheel limit (`I -> 0`); for a realistic wheel it is
`c_rr*g*m*r^2/(I + m*r^2)`, the exact analog of `simplified.model`'s own `M/M_eq`-style rolling-term
reduction. Both the literal limit and the general closed form are tested in
`tests/analytic/test_contact_regularized.py`.

## (f) T7 -- the lumped drivetrain: one foundational sign correction, three consequences, and one
data-model gap, all flagged for architect/user sign-off

**Root cause.** Architecture.md S7.1 claims "forward rolling gives negative spin, `omega_2a =
-v_x/r_2a`", citing `forward_motion.tex`'s own (z-**down**-convention) `phi_ddot_2a = -x_ddot/r_2a`
as justification for carrying the same sign into the z-**up** planar convention. Working the
zero-slip condition through the already-built, already-tested T5 engine's own rotation convention
(`planar.body`'s `y_hat x r = (r_z, -r_x)`, cross-checked against `rotation_matrix`'s own derivative)
for the contact point at local `(0, -r)` (straight down from the axle) gives `v_contact_x = v_axle_x +
omega*r_z = v_axle_x - omega*r`; zero slip at `v_axle_x > 0` therefore needs `omega = +v_axle_x/r`,
the **opposite** sign from S7.1's claim. This is independently confirmed by T6's own
`contact.geometry.contact_geometry`'s `v_slip = v_axle_x0 - radius*omega` formula, which needs the
same `+v/r`. The likely mechanism: D-13's documented z-sign flip between the two tiers' conventions
also flips the *sense* of "positive rotation about +y_hat" (flipping one axis of a right-handed triad
flips its handedness), a rotational consequence D-13's own text does not spell out.

This was **found empirically**, not derived first: a rolling IC built from S7.1's literal sign gave
`v_slip = 10 m/s` instead of `0` at a supposedly-consistent rolling start (`planar/kinematics.py`'s
own IC solver), which was the first symptom.

**Four places this ticket had to correct for self-consistency** (all documented in the listed
module's own docstring, all empirically verified via the §7.5.6 cross-tier tests and the coasting/
braking qualitative checks the ticket's "Done when" explicitly asks for):

1. `planar/kinematics.py` -- the rolling-seed `omega_2a = +v0/r_2a` (not `-v0/r_2a`).
2. `planar/forces/pedal.py` -- the tangential-force direction, so that positive pedal force drives
   *positive* (not negative) crank spin, matching the corrected rolling sign.
3. `planar/constraints/gear.py` (`IdealChain`) -- **a second, independent correction**, found while
   chasing the first: `planar/solver.py`'s actual `kkt_solve` arithmetic is `M@a = h + J^T@lambda`
   (verified directly from `sol = [a, -lam]`), not the `h - J^T@lambda` architecture.md S7.5.2's prose
   assumes when it states the chain's generalized-force signs -- an independent documentation/code
   mismatch, unrelated to the rolling-sign issue but found investigating the same symptom (chain
   engaging with the wrong-sign tension). Fixed by flipping `c_chain`'s overall sign (the constraint
   set `c=0` is unchanged; only which sign of `lambda` means "tension" changes).
4. `planar/forces/brake.py` -- the brake moment must resist the wheel's own (now-positive-for-forward)
   spin, so `-sigma*r_bk*B` on the wheel (not `+sigma*r_bk*B` as S7.3 literally states).

**Verification.** After all four corrections, `validation/cross_tier.py`'s five §7.5.6 checks all pass
(steady-state speed agreement to < 0.1% across 9 power/grade combinations; chain tension to < 1e-5
relative error away from the `t=0` zero-slip transient; the reflected-inertia identity exact to float
precision; static load split exact; wheelie/stoppie crossings within the documented tolerance) --
summarized in this ticket's final developer report, full numbers in `tests/crosstier/
test_planar_vs_simplified.py`'s own run output.

**Data-model gap, not a sign bug: no `m_3b` field.** `core.params.DrivetrainParams` has no crankset
mass field -- `BikeSpec.M`/`m_eq`'s own docstrings confirm the crankset's mass is folded into
`frame.m_1` (the Simplified tier's point-mass assumption, which S7.5.1 item 2 explicitly says the
planar tier reuses). Since body `3b`'s CoG is pinned to the frame's own point `C` (zero offset), a
real `m_3b` would double-count against `frame.m_1` and break the exact §7.5.6 reduction. `planar
/drivetrain.py::CRANKSET_MASS_PLACEHOLDER = 1e-9` kg exists purely to satisfy `RigidBody`'s own
`mass > 0` structural invariant and is physically negligible. If a real `m_3b` field is ever added to
`DrivetrainParams` (out of this ticket's `core/params.py`-free boundary), §7.5.6's reduction should be
re-derived to confirm it still holds with `m_3b != 0`.

**`simulate_with_freewheel`'s default `rtol`/`atol`/`max_drift` (`planar/drivetrain.py`), recorded per
S12.2.** The freewheel driver's RHS embeds a trial-solve mode switch (continuous but not smooth at the
engage/disengage boundary); `planar/integrate.py::run`'s own tight defaults (`rtol=atol=1e-9`,
`max_drift=1e-6`) were found empirically to diverge to `NaN` on an otherwise-unremarkable 10-30 s
constant-torque run, while `rtol=1e-6`, `atol=1e-8`, `max_drift=1e-4` integrate the *same* inputs
cleanly and still meet the ticket's own `30 s run < 5 s wall-clock` target (measured: 4.6-4.9 s on this
machine). These are now `simulate_with_freewheel`'s own defaults; `validation/cross_tier.py`'s own
checks pass tighter `rtol`/`atol` explicitly where the run never approaches a mode boundary.

**A genuine (non-numerical) model limit, documented so it is not re-discovered as a "bug" later:**
sustained periodic pedal inputs with large peak force (e.g. `ClippedSine` with `P_0 >= 150` N) on the
`rolling_sine` road preset can drive the planar model into an actual high-pitch-rate/traction-limit
regime within 15-20 s, past which the regularized contact model's own Newton foot-of-normal solve
stops converging (not a sign error -- confirmed by bisecting the failure time and inspecting the
state immediately before divergence, which shows ordinary, physically plausible values right up to
the failure). `examples/04_planar_rolling_road.py` uses a modest constant-torque input specifically to
stay inside the model's well-behaved operating range; T11's `EventDrivenContact` (exact rolling, no
compliance) is the natural place to characterize this regime's true boundary, not this ticket.
