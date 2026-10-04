"""planar/integrate.py -- the `solve_ivp` driver: RHS wiring, event
plumbing, drift reporting, optional post-step projection, and `ModeLog`
accumulation (architecture.md S7.7's last three paragraphs).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from scipy.integrate import solve_ivp

from bikephysics.core.results import ModeLog
from bikephysics.planar.solver import solve_accelerations
from bikephysics.planar.system import MultibodySystem

FloatArray = npt.NDArray[np.float64]

IntegratorMethod = Literal["Radau", "RK45", "BDF", "LSODA"]
_SPARSITY_AWARE_METHODS = frozenset({"Radau", "BDF", "LSODA"})


@dataclass(frozen=True)
class DriftReport:
    """`max|c|`/`max|J u|` over every reported sample of a run, checked
    against `bound` (architecture.md S7.7: "a run whose constraint
    residual exceeds a configured bound fails loudly")."""

    max_c: float
    max_Ju: float
    bound: float

    def ok(self) -> bool:
        return self.max_c <= self.bound and self.max_Ju <= self.bound


@dataclass(frozen=True)
class PlanarRunResult:
    """One `run()` call's output. `q`/`u` have shape `(n_samples, nq)`;
    `raw` is the last `scipy.integrate.OdeResult` segment, kept for
    callers that need `dense_output`/`t_events`/`y_events`."""

    t: FloatArray
    q: FloatArray
    u: FloatArray
    drift: DriftReport
    modes: ModeLog
    raw: Any


def baumgarte_gains(h_typ: float) -> tuple[float, float]:
    """`alpha = beta = 1/(10*h_typ)` (architecture.md S7.7's default)."""
    g = 1.0 / (10.0 * h_typ)
    return g, g


def make_rhs(
    system: MultibodySystem, *, alpha: float, beta: float
) -> Callable[[float, FloatArray], FloatArray]:
    """Build the `solve_ivp`-compatible `f(t, y)` for `y = [q, u]`
    (architecture.md S7.7 step 6: `return [u, a]`)."""
    nq = system.layout.nq

    def rhs(t: float, y: FloatArray) -> FloatArray:
        q = y[:nq]
        u = y[nq:]
        result = solve_accelerations(system, t, q, u, alpha=alpha, beta=beta)
        return np.concatenate([u, result.a])

    return rhs


def project_to_manifold(
    system: MultibodySystem, q: FloatArray, u: FloatArray, *, iterations: int = 1
) -> tuple[FloatArray, FloatArray]:
    """Optional post-step projection (architecture.md S7.7): a
    Gauss-Newton correction of `q` onto `c(q) = 0` in the `M`-norm
    (`dq = -M^-1 J^T (J M^-1 J^T)^-1 c`), then a projection of `u` onto
    `J u = 0` (`u <- u - M^-1 J^T (J M^-1 J^T)^-1 J u`) -- the same
    formula architecture.md S7.5.4 uses for the chain-engagement impulse,
    applied here to every *bilateral* constraint. Unilateral rows are a
    per-caller modeling decision (which ones are currently "engaged") that
    this generic projector does not guess at.
    """
    layout = system.layout
    constraints = [con for con in system.constraints if con.kind == "bilateral"]
    if not constraints:
        return q.copy(), u.copy()

    M_inv = np.linalg.inv(system.mass_matrix())
    row_map = layout.constraint_row_map(constraints)
    n_c = layout.total_dim(constraints)

    q_proj = q.copy()
    for _ in range(iterations):
        J = np.zeros((n_c, layout.nq))
        c = np.zeros(n_c)
        for con in constraints:
            rows = row_map[con.name]
            J[rows, :] = con.jacobian(q_proj, layout, system.xp)
            c[rows] = con.residual(q_proj, layout, system.xp)
        JMJt = J @ M_inv @ J.T
        dq = -M_inv @ J.T @ np.linalg.solve(JMJt, c)
        q_proj = q_proj + dq

    J = np.zeros((n_c, layout.nq))
    for con in constraints:
        J[row_map[con.name], :] = con.jacobian(q_proj, layout, system.xp)
    JMJt = J @ M_inv @ J.T
    u_proj = u - M_inv @ J.T @ np.linalg.solve(JMJt, J @ u)

    return q_proj, u_proj


def _drift_over_samples(
    system: MultibodySystem, t: FloatArray, y: FloatArray, *, alpha: float, beta: float
) -> tuple[float, float]:
    """`max|c|`/`max|J u|` over every column of `y` (shape
    `(2*nq, n_samples)`), by re-running the constraint block at each
    reported sample -- decoupled from whatever internal stages the
    integrator took, so it reflects the trajectory actually returned."""
    nq = system.layout.nq
    max_c = 0.0
    max_Ju = 0.0
    for i in range(y.shape[1]):
        q_i = y[:nq, i]
        u_i = y[nq:, i]
        result = solve_accelerations(system, float(t[i]), q_i, u_i, alpha=alpha, beta=beta)
        if result.c.size:
            max_c = max(max_c, float(np.max(np.abs(result.c))))
            max_Ju = max(max_Ju, float(np.max(np.abs(result.c_dot))))
    return max_c, max_Ju


def run(
    system: MultibodySystem,
    y0: FloatArray,
    t_span: tuple[float, float],
    *,
    method: IntegratorMethod = "Radau",
    alpha: float | None = None,
    beta: float | None = None,
    h_typ: float = 1e-3,
    max_drift: float = 1e-6,
    raise_on_drift: bool = True,
    project_interval: float | None = None,
    project_iterations: int = 1,
    t_eval: FloatArray | None = None,
    events: Sequence[Callable[[float, FloatArray], float]] | None = None,
    rtol: float = 1e-9,
    atol: float = 1e-9,
    dense_output: bool = False,
    **solve_ivp_kwargs: Any,
) -> PlanarRunResult:
    """The planar engine's `solve_ivp` driver (architecture.md S7.7).

    `method` defaults to `"Radau"` (stiff-capable, the default for the
    contact-regularized tiers built on top of this engine, D-9); `RK45`/
    `BDF`/`LSODA` are selectable for T5's own non-stiff analytic tests.
    `alpha`/`beta` default to `baumgarte_gains(h_typ)` if not given.

    `project_interval`, if given, is a *time* interval (seconds): the
    integration is run in back-to-back segments of that length, with
    `project_to_manifold` applied to the state at each segment boundary
    before continuing -- `solve_ivp`'s adaptive stepping does not expose
    a stable notion of "every N internal steps", so this engine's
    "optional post-step projection" is phrased in simulated time instead.
    `None` (the default) disables projection; T5's own pendulum tests
    stay within `max_drift` without it (architecture.md S7's "Done when"
    criterion).

    After integration, `max|c|`/`max|J u|` are recomputed at every
    reported sample (not just the accepted internal steps) and checked
    against `max_drift`; `raise_on_drift=True` (the default) fails loudly
    rather than returning a trajectory that silently violated its
    constraints (architecture.md S7.7).
    """
    if alpha is None or beta is None:
        default_alpha, default_beta = baumgarte_gains(h_typ)
        alpha = default_alpha if alpha is None else alpha
        beta = default_beta if beta is None else beta

    nq = system.layout.nq
    rhs = make_rhs(system, alpha=alpha, beta=beta)
    jac_sparsity = system.layout.jac_sparsity() if method in _SPARSITY_AWARE_METHODS else None

    def _integrate(
        span: tuple[float, float], y_start: FloatArray, eval_times: FloatArray | None
    ) -> Any:
        kwargs: dict[str, Any] = dict(solve_ivp_kwargs)
        if jac_sparsity is not None:
            kwargs["jac_sparsity"] = jac_sparsity
        return solve_ivp(
            rhs,
            span,
            y_start,
            method=method,
            t_eval=eval_times,
            events=events,
            rtol=rtol,
            atol=atol,
            dense_output=dense_output,
            **kwargs,
        )

    if project_interval is None:
        sol = _integrate(t_span, y0, t_eval)
        if not sol.success:
            raise RuntimeError(f"planar integration failed: {sol.message}")
        t_all = sol.t
        y_all = sol.y
        raw = sol
    else:
        t0, t_end = t_span
        t_chunks: list[FloatArray] = []
        y_chunks: list[FloatArray] = []
        t_cur = t0
        y_cur = np.asarray(y0, dtype=float)
        raw = None
        while t_cur < t_end - 1e-12:
            t_next = min(t_cur + project_interval, t_end)
            sol = _integrate((t_cur, t_next), y_cur, None)
            if not sol.success:
                raise RuntimeError(
                    f"planar integration failed in segment [{t_cur}, {t_next}]: {sol.message}"
                )
            raw = sol
            t_chunks.append(sol.t)
            y_chunks.append(sol.y)
            q_end = sol.y[:nq, -1]
            u_end = sol.y[nq:, -1]
            q_proj, u_proj = project_to_manifold(
                system, q_end, u_end, iterations=project_iterations
            )
            y_cur = np.concatenate([q_proj, u_proj])
            t_cur = t_next
        t_all = np.concatenate(t_chunks)
        y_all = np.concatenate(y_chunks, axis=1)

    max_c, max_Ju = _drift_over_samples(system, t_all, y_all, alpha=alpha, beta=beta)
    drift = DriftReport(max_c=max_c, max_Ju=max_Ju, bound=max_drift)
    if raise_on_drift and not drift.ok():
        raise RuntimeError(
            f"constraint drift exceeded bound: max|c|={max_c:.3e}, "
            f"max|J u|={max_Ju:.3e}, bound={max_drift:.3e}"
        )

    # T5: no mode switches to log (no contact, no drivetrain in this ticket).
    modes = ModeLog(t=np.array([]), body=(), mode=(), energy_loss=np.array([]))

    return PlanarRunResult(
        t=t_all,
        q=y_all[:nq, :].T,
        u=y_all[nq:, :].T,
        drift=drift,
        modes=modes,
        raw=raw,
    )
