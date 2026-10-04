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
