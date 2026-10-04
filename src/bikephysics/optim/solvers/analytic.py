"""optim/solvers/analytic.py -- `AnalyticSimplified`, the closed-form/
Pontryagin reference solver (architecture.md §8.4, D-11). Its only purpose
is to be the correctness oracle the numeric solvers (`ScipyLocal`/
`ScipyGlobal`) are tested against for the flat-road, constant-power,
limit-free, minimum-time case; it is not a general-purpose solver and
raises a clear error on any problem outside that case.

**Derivation.** The only decision is how much propulsive power to deliver
at each instant, bounded above by a single `PowerCeiling(p_max)` (no
`PedalForceBound`, no traction/wheelie/stoppie/cadence limits -- the
"limit-free" assumption). Reaching a target distance sooner is always
achieved by delivering more power at every instant (the dynamics are
monotone in the control), so Pontryagin's minimum principle gives a
bang-constant optimal control: `P(t) = p_max` for the whole run. This is
the same "constant propulsive power" control `optim.parametrization.
ConstantPowerPedal` implements (`force = power / v`, inverting
`simplified/model.py`'s `accel` pedal term exactly).

Substituting that control into `model.accel`'s exact equation of motion
(flat road, no braking, forward motion `sigma = 1`) gives

    M_eq * dv/dt = p_max/v - c_2a,rr*M*g - k*v^2,    k = 0.5*rho*C_dA

(the same force balance `simplified.analysis.steady_state_speed` solves
for its equilibrium `v`, here used as a genuine ODE rather than just its
fixed point). Separating variables in `v` rather than `t` gives two
well-behaved definite integrals (no singularity at `v=0`, since the
integrands below multiply by `v`/divide by the still-finite driving term):

    dt/dv = M_eq / f(v),     dx/dv = v * M_eq / f(v),     f(v) = p_max/v - c_rr*M*g - k*v^2

`t(v) = integral[v0, v] dt/dv'`, `x(v) = integral[v0, v] dx/dv'`, both
evaluated by `scipy.integrate.quad` (accurate to quadrature tolerance, not
ODE step-size tolerance -- a legitimately higher-fidelity reference than
the numeric solvers' own rollouts). The minimum time to `x_target` is
`t(v*)` where `v* = x^{-1}(x_target)`, found by `scipy.optimize.brentq`
since `x(v)` is strictly increasing on `(v0, v_inf)` and diverges to
`+inf` as `v -> v_inf` (the finite-power asymptotic top speed,
`simplified.analysis.steady_state_speed(spec, p_max, 0.0)`).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import numpy as np
from scipy.integrate import quad
from scipy.optimize import brentq

from bikephysics.core.params import BikeSpec
from bikephysics.core.road import Flat
from bikephysics.optim.constraints import PowerCeiling
from bikephysics.optim.forward import ControlInputs, SimplifiedForwardModel
from bikephysics.optim.objectives import MinTimeOverDistance
from bikephysics.optim.parametrization import ConstantPowerPedal, crank_ratio, gear_factor
from bikephysics.optim.problem import ResolvedProblem
from bikephysics.optim.solvers.base import OptimResult, active_constraint_report
from bikephysics.simplified import analysis, model  # noqa: TID251 -- optim is exempt, S4


def _resistive_const(spec: BikeSpec) -> float:
    """`c_2a,rr*M*g` -- the flat-road (`theta=0`) rolling-resistance term
    of `model.accel`'s equation of motion; `M*g*sin(theta)` vanishes on a
    flat road."""
    return spec.rear.c_2a_rr * spec.M * spec.env.g


def _drag_coeff(spec: BikeSpec) -> float:
    """`k = 0.5*rho*C_dA`, reused directly from `simplified.analysis`'s own
    private helper rather than re-derived, so this oracle's physics is
    never a second, independently-maintained expression of the same
    coefficient (the risk architecture.md §8.4 flags for `CasadiCollocation`
    applies just as much to a hand-written duplicate here)."""
    return analysis._drag_power_coeff(spec)  # noqa: SLF001 -- see docstring


def _f(v: float, p_max: float, resistive_const: float, k: float) -> float:
    return p_max / v - resistive_const - k * v**2


def _dt_dv(v: float, p_max: float, m_eq_val: float, resistive_const: float, k: float) -> float:
    return m_eq_val / _f(v, p_max, resistive_const, k)


def _dx_dv(v: float, p_max: float, m_eq_val: float, resistive_const: float, k: float) -> float:
    return v * _dt_dv(v, p_max, m_eq_val, resistive_const, k)


def _integrate(
    v_lo: float, v_hi: float, integrand: Callable[..., float], args: tuple[float, ...]
) -> float:
    if v_hi <= v_lo:
        return 0.0
    value, _err = quad(integrand, v_lo, v_hi, args=args, limit=200)
    return float(value)


def _find_v_star(v0: float, v_inf: float, x_target: float, args: tuple[float, ...]) -> float:
    """The unique `v* in (v0, v_inf)` with `x(v*) == x_target`
    (module docstring); `x(v)` diverges as `v -> v_inf`, so the search
    bracket is pushed toward `v_inf` geometrically until it brackets a
    sign change."""

    def h(v: float) -> float:
        return _integrate(v0, v, _dx_dv, args) - x_target

    factor = 0.5
    v_hi = v0 + factor * (v_inf - v0)
    for _ in range(80):
        if h(v_hi) > 0.0:
            return brentq(h, v0, v_hi, xtol=1e-12, rtol=1e-12)
        factor = 1.0 - (1.0 - factor) * 0.5
        v_hi = v0 + factor * (v_inf - v0)
    raise RuntimeError(
        "AnalyticSimplified: could not bracket v_star -- x_target may be "
        "unreachable at finite time for this p_max/spec (v would need to "
        "exceed the asymptotic top speed)."
    )


@dataclass(frozen=True)
class AnalyticResult:
    """The closed-form answer, independent of any `ResolvedProblem`."""

    t_star: float
    v_star: float
    v_inf: float


def solve_closed_form(
    spec: BikeSpec, p_max: float, x_target: float, v0: float = 0.0
) -> AnalyticResult:
    """The pure closed-form computation (module docstring), usable without
    building a `ResolvedProblem` -- what `tests/analytic/
    test_optim_analytic.py` checks directly."""
    if p_max <= 0.0:
        raise ValueError(f"p_max must be > 0, got {p_max}")
    if x_target <= 0.0:
        raise ValueError(f"x_target must be > 0, got {x_target}")
    v_inf = analysis.steady_state_speed(spec, p_max, 0.0, include_rolling_resistance=True)
    if v0 >= v_inf:
        raise ValueError(
            f"AnalyticSimplified: v0={v0} is already at/above the asymptotic top "
            f"speed v_inf={v_inf} for p_max={p_max} -- no finite min-time solution."
        )
    m_eq_val = model.m_eq(spec)
    resistive_const = _resistive_const(spec)
    k = _drag_coeff(spec)
    args = (p_max, m_eq_val, resistive_const, k)
    v_star = _find_v_star(v0, v_inf, x_target, args)
    t_star = _integrate(v0, v_star, _dt_dv, args)
    return AnalyticResult(t_star=t_star, v_star=v_star, v_inf=v_inf)


@dataclass
class AnalyticSimplified:
    """`Solver` wrapper around `solve_closed_form` (architecture.md §8.4).
    Validates that `problem` actually is the oracle's narrow case (flat
    Simplified-tier forward model, `MinTimeOverDistance` objective, exactly
    one scalar `PowerCeiling` constraint and no others) before computing
    anything, so a mismatched problem fails loudly rather than silently
    returning a wrong-but-plausible number."""

    def _validate(self, problem: ResolvedProblem) -> tuple[float, float]:
        if not isinstance(problem.forward_model, SimplifiedForwardModel):
            raise ValueError("AnalyticSimplified requires a Simplified-tier forward model")
        if not isinstance(problem.forward_model.road, Flat):
            raise ValueError("AnalyticSimplified requires a flat road")
        if not isinstance(problem.objective, MinTimeOverDistance):
            raise ValueError("AnalyticSimplified only solves MinTimeOverDistance problems")
        power_ceilings = [c for c in problem.constraints if isinstance(c, PowerCeiling)]
        others = [c for c in problem.constraints if not isinstance(c, PowerCeiling)]
        if len(power_ceilings) != 1 or others:
            raise ValueError(
                "AnalyticSimplified requires exactly one PowerCeiling constraint and "
                "no other path constraints (the 'limit-free' assumption)"
            )
        p_max = power_ceilings[0].p_max
        if callable(p_max):
            raise ValueError(
                "AnalyticSimplified requires a scalar PowerCeiling.p_max, not a "
                "P_max(cadence) callable"
            )
        return float(p_max), float(problem.objective.x_target)

    def solve(self, problem: ResolvedProblem) -> OptimResult:
        t0 = time.monotonic()
        p_max, x_target = self._validate(problem)
        v0 = problem.horizon.v0
        answer = solve_closed_form(problem.spec, p_max, x_target, v0)

        pedal = ConstantPowerPedal(
            power=p_max,
            crank_ratio=crank_ratio(problem.spec),
            gear_factor=gear_factor(problem.spec),
        )
        horizon = replace(problem.horizon, x_target=x_target)
        traj = problem.forward_model.rollout(ControlInputs(pedal=pedal), horizon)

        wall_time_s = time.monotonic() - t0
        return OptimResult(
            x_opt=np.array([p_max]),
            trajectory=traj,
            objective_value=answer.t_star,
            history=(),
            status="closed_form",
            message="Pontryagin bang-constant control, quadrature-integrated",
            success=True,
            wall_time_s=wall_time_s,
            active_constraints=active_constraint_report(problem, traj),
            n_evaluations=1,
            solver_name="analytic_simplified",
            extra={
                "p_max": p_max,
                "v_star": answer.v_star,
                "v_inf": answer.v_inf,
                "rollout_t_final": float(traj.t[-1]),
                "rollout_x_final": float(traj.x[-1]),
            },
        )
