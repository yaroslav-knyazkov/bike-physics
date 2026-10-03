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
