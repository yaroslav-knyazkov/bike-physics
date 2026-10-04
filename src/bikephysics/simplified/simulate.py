"""simplified/simulate.py -- the Simplified tier's `solve_ivp` driver
(architecture.md S6.3).

State `y = [x, v]`. The RHS calls `simplified.model.accel` -- the project's
one true hot-path function (architecture.md S6.1) -- after resolving the
control inputs (`core.pedals.PedalInput`, `core.brakes.BrakeInput`), the
road grade (`core.road.Road`), and the aero state (`core.aero.AeroModel`)
at the current `(t, x, v)`. `simulate()`'s own Python-level overhead is not
itself the S12.1 hot path (`accel` is), but is kept to one lean helper
(`_forces`) in the RHS regardless, so solve_ivp's adaptive-step RHS calls
stay cheap; the fuller per-instant computation needed for events and
post-processing (`_full_state`: reactions, chain tension, margins) is
evaluated only once per accepted solver step (events) or once per output
point (post-processing), never inside the RHS itself.

**Crank-angle reconstruction** (architecture.md S6.3): no third ODE state.
`theta_3b(t) = theta_3b(0) - (x(t) - x(0)) * r_2a_cg / (r_3b_cr * r_2a)`,
the algebraic consequence of the fixed gear ratio and `forward_motion.tex`'s
own `phi_ddot_2a = -x_ddot / r_2a` (forward motion gives negative wheel/
crank spin -- see `architecture.md` S7.1, which states the planar tier
inherits the same relation from this document). This resolves the
`theta_3b -> P_D -> accel -> x -> theta_3b` loop by always reading
`theta_3b` off the integrator's *current* `x`, exactly as the architecture
note requires.

**Events** (architecture.md S6.3): two terminal conditions --
`v <= 0` (crossing downward through zero: a coast-to-stop) and, when
`x_target` is given, `x >= x_target` (crossing upward: the min-time
objective's stopping condition) -- plus five **non-terminal** events, one
per `core.limits` margin, each firing on the margin's *entry* into
violation (`direction=+1`: satisfied -> violated), logged into
`Trajectory.meta["limit_crossings"]` rather than `Trajectory.modes` (that
field is Planar-tier only, per its own docstring). The exact root
`solve_ivp` finds for a terminal event is appended to the output arrays as
the final sample (it almost never lands on the `t_eval` grid otherwise),
so `x[-1] == x_target` to solver tolerance, not grid spacing.

`v <= 0`'s crossing is a real sign change only when something *not*
modulated by `sign_smooth` keeps acting at `v=0` -- gravity
(`-M*g*sin(theta)`), not rolling resistance or braking (both `sigma`-
scaled, hence exactly zero at `v=0`). A pure brake-to-a-stop on flat
ground, with no grade and no pedal, makes `v=0` an attracting fixed point
of the regularized model: `v` decays towards it asymptotically and never
actually crosses, so this event will not fire and the run reaches
`t_end` instead -- the correct consequence of smoothing the sign
function (D-9), not a bug. An uphill coast (or any run with a net force at
`v=0`) crosses cleanly.

**The `v=0`/RK45 footgun** (task.md T4 done-when): the old, deleted
`simulation.py` used a hard `sign(v)` for the rolling-resistance/brake
direction term, which is discontinuous at `v=0` and made RK45's adaptive
step size collapse for a standing start. `simplified/model.py`'s `accel`
already uses `core.mathx.sign_smooth` (a `tanh`, C-infinity) for exactly
this term, so a standing start with zero initial pedal force integrates
through `v=0` without any special-casing here -- `simulate()` does not
itself need to guard against this; the fix lives entirely in `model.py`
and this module inherits it for free.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import numpy.typing as npt
from scipy.integrate import solve_ivp

from bikephysics.core import limits as core_limits
from bikephysics.core.aero import AeroModel, AeroState, ConstantCdA
from bikephysics.core.atmosphere import atmosphere_from_env
from bikephysics.core.brakes import BrakeInput, ConstantBrake, KinematicState
from bikephysics.core.mathx import MathBackend, numpy_backend
from bikephysics.core.params import BikeSpec
from bikephysics.core.pedals import PedalInput
from bikephysics.core.results import Trajectory
from bikephysics.core.road import Flat, Road
from bikephysics.simplified import model

FloatArray = npt.NDArray[np.float64]

#: `core.limits.limit_margins`' five margin names, in the fixed order every
#: `LimitMargins`/event/`meta["limit_crossings"]` below uses.
_MARGIN_NAMES: tuple[str, ...] = (
    "traction_rear",
    "traction_front",
    "wheelie",
    "stoppie",
    "freewheel",
)


@dataclass(frozen=True)
class _SimContext:
    """Everything the RHS/event/post-processing helpers below need, bound
    once per `simulate()` call so none of them re-derive the atmosphere or
    the fixed gear-ratio constant on every evaluation."""

    spec: BikeSpec
    road: Road
    pedal_input: PedalInput
    brake_input: BrakeInput
    aero_model: AeroModel
    xp: MathBackend
    x0_ref: float
    theta_3b0: float
    crank_ratio: float  # r_2a_cg / (r_3b_cr * r_2a); see module docstring
    rho: float
    mu: float


@dataclass(frozen=True)
class _Instant:
    """One instant's full physical state -- the fuller computation
    `_full_state` produces, consumed by the margin events and by
    post-processing (never by the RHS; see module docstring)."""

    theta: float
    theta_3b: float
    omega_3b: float
    omega_2a: float
    omega_2b: float
    P_D: float
    P_F: float
    B_2a: float
    B_2b: float
    aero: AeroState
    a: float
    T: float
    R_2a_x: float
    R_2a_z: float
    R_2b_x: float
    R_2b_z: float
    margins: dict[str, float]


def _crank_state(ctx: _SimContext, x: float, v: float) -> tuple[float, float]:
    """`(theta_3b, omega_3b)` from the current `(x, v)` -- the algebraic
    reconstruction of the module docstring, no ODE state spent."""
    theta_3b = ctx.theta_3b0 - (x - ctx.x0_ref) * ctx.crank_ratio
    omega_3b = -v * ctx.crank_ratio
    return theta_3b, omega_3b


def _forces(
    ctx: _SimContext, t: float, x: float, v: float
) -> tuple[float, float, float, float, float, float, float, AeroState]:
    """The lean subset of per-instant physics the RHS actually needs:
    `(theta, theta_3b, omega_3b, P_D, P_F, B_2a, B_2b, aero_state)`. Does
    not compute reactions/tension/margins -- see `_full_state` for those."""
    theta = ctx.road.theta(x)
    theta_3b, omega_3b = _crank_state(ctx, x, v)
    p_d, p_f = ctx.pedal_input.forces(t, theta_3b, omega_3b)
    b_2a, b_2b = ctx.brake_input.forces(t, KinematicState(x=x, v=v))
    aero_state = ctx.aero_model.evaluate(
        rho=ctx.rho,
        mu=ctx.mu,
        v1_x=v,
        v1_z=0.0,
        wind_x=ctx.spec.env.wind_x,
        wind_z=ctx.spec.env.wind_z,
    )
    return theta, theta_3b, omega_3b, p_d, p_f, b_2a, b_2b, aero_state


def _full_state(ctx: _SimContext, t: float, x: float, v: float) -> _Instant:
    """The full per-instant computation (reactions, chain tension, spins,
    margins) built on `_forces`/`model.accel` -- used by the non-terminal
    margin events and by post-processing, never by the RHS itself."""
    theta, theta_3b, omega_3b, p_d, p_f, b_2a, b_2b, aero_state = _forces(ctx, t, x, v)
    a = model.accel(ctx.spec, v, theta, p_d, p_f, b_2a, b_2b, aero_state, xp=ctx.xp)
    sigma = float(ctx.xp.sign_smooth(v, ctx.spec.contact.v_eps))
    r_2a_z, r_2b_z = model.normal_loads(
        ctx.spec, a, theta, aero_state.A_1_x, aero_state.A_1_m, sigma
    )
    tension = model.chain_tension(ctx.spec, a, p_d, p_f)
    r_2a_x, r_2b_x = model.traction(ctx.spec, a, tension, r_2a_z, r_2b_z, b_2a, b_2b, sigma)
    mu_s = ctx.spec.contact.mu_s
    margins = {
        "traction_rear": core_limits.traction_margin(r_2a_x, r_2a_z, mu_s),
        "traction_front": core_limits.traction_margin(r_2b_x, r_2b_z, mu_s),
        "wheelie": core_limits.wheel_lift_margin(r_2b_z),
        "stoppie": core_limits.wheel_lift_margin(r_2a_z),
        "freewheel": core_limits.freewheel_margin(tension),
    }
    return _Instant(
        theta=theta,
        theta_3b=theta_3b,
        omega_3b=omega_3b,
        omega_2a=-v / ctx.spec.rear.r_2a,
        omega_2b=-v / ctx.spec.front.r_2b,
        P_D=p_d,
        P_F=p_f,
        B_2a=b_2a,
        B_2b=b_2b,
        aero=aero_state,
        a=a,
        T=tension,
        R_2a_x=r_2a_x,
        R_2a_z=r_2a_z,
        R_2b_x=r_2b_x,
        R_2b_z=r_2b_z,
        margins=margins,
    )


def _make_rhs(ctx: _SimContext) -> Any:
    def _rhs(t: float, y: FloatArray) -> list[float]:
        x, v = y
        theta, _, _, p_d, p_f, b_2a, b_2b, aero_state = _forces(ctx, t, x, v)
        a = model.accel(ctx.spec, v, theta, p_d, p_f, b_2a, b_2b, aero_state, xp=ctx.xp)
        return [v, a]

    return _rhs


def _event_v_zero(t: float, y: FloatArray) -> float:
    return float(y[1])


_event_v_zero.terminal = True  # type: ignore[attr-defined]
_event_v_zero.direction = -1.0  # type: ignore[attr-defined]


def _make_event_x_target(x_target: float) -> Any:
    def _event(t: float, y: FloatArray) -> float:
        return float(y[0] - x_target)

    _event.terminal = True  # type: ignore[attr-defined]
    _event.direction = 1.0  # type: ignore[attr-defined]
    return _event


def _make_margin_event(ctx: _SimContext, name: str) -> Any:
    def _event(t: float, y: FloatArray) -> float:
        return _full_state(ctx, t, float(y[0]), float(y[1])).margins[name]

    _event.terminal = False  # type: ignore[attr-defined]
    _event.direction = 1.0  # type: ignore[attr-defined]  # satisfied (<=0) -> violated (>0)
    return _event


def simulate(
    spec: BikeSpec,
    pedal_input: PedalInput,
    *,
    road: Road | None = None,
    brake_input: BrakeInput | None = None,
    aero_model: AeroModel | None = None,
    x0: float = 0.0,
    v0: float = 0.0,
    theta_3b0: float = 0.0,
    t_span: tuple[float, float] = (0.0, 120.0),
    x_target: float | None = None,
    n_output: int = 2000,
    method: Literal["RK45", "Radau"] = "RK45",
    rtol: float = 1e-8,
    atol: float = 1e-10,
    max_step: float = np.inf,
    xp: MathBackend = numpy_backend,
) -> Trajectory:
    """Simulate the Simplified tier forward in time and return a
    `core.results.Trajectory` (architecture.md S6.3). `road` defaults to
    `core.road.Flat()`, `brake_input` to `core.brakes.ConstantBrake()` (no
    braking), `aero_model` to `core.aero.ConstantCdA` built from `spec.aero`
    -- all three are themselves protocols, so any implementation works.

    `x_target`, when given, adds the terminal `x >= x_target` event (the
    min-time objective's stopping condition); the simulation otherwise runs
    to `t_span[1]` or a coast-to-stop (`v <= 0`), whichever comes first.
    """
    model._check_equal_wheels(spec)
    road = road if road is not None else Flat()
    brake_input = brake_input if brake_input is not None else ConstantBrake()
    aero_model = (
        aero_model
        if aero_model is not None
        else ConstantCdA(C_1_x_0=spec.aero.C_1_x_0, A_1=spec.aero.A_1, l_1=spec.aero.l_1)
    )
    atmosphere = atmosphere_from_env(
        atmosphere=spec.env.atmosphere,
        rho_constant=spec.env.rho,
        altitude=spec.env.altitude,
        relative_humidity=spec.env.RH,
        temperature_k=spec.env.T,
    )
    ctx = _SimContext(
        spec=spec,
        road=road,
        pedal_input=pedal_input,
        brake_input=brake_input,
        aero_model=aero_model,
        xp=xp,
        x0_ref=x0,
        theta_3b0=theta_3b0,
        crank_ratio=spec.drivetrain.r_2a_cg / (spec.drivetrain.r_3b_cr * spec.rear.r_2a),
        rho=atmosphere.density(),
        mu=atmosphere.viscosity(),
    )

    event_names: list[str] = ["v_zero"]
    events: list[Any] = [_event_v_zero]
    if x_target is not None:
        event_names.append("x_target")
        events.append(_make_event_x_target(x_target))
    terminal_names = tuple(event_names)  # every event appended so far is terminal
    for name in _MARGIN_NAMES:
        event_names.append(name)
        events.append(_make_margin_event(ctx, name))

    t0, t1 = t_span
    t_eval = np.linspace(t0, t1, n_output)

    sol = solve_ivp(
        _make_rhs(ctx),
        (t0, t1),
        [x0, v0],
        method=method,
        events=events,
        t_eval=t_eval,
        dense_output=False,
        rtol=rtol,
        atol=atol,
        max_step=max_step,
    )
    if not sol.success:
        raise RuntimeError(f"simplified.simulate: solve_ivp failed: {sol.message}")

    termination_reason = "t_end"
    for name in terminal_names:
        idx = event_names.index(name)
        if len(sol.t_events[idx]) > 0:
            termination_reason = name
            break

    t_list = list(sol.t)
    x_list = list(sol.y[0])
    v_list = list(sol.y[1])
    if termination_reason != "t_end":
        # `t_eval` is a fixed grid, so the exact root `solve_ivp` finds for
        # a terminal event (e.g. `x == x_target`) almost never lands on one
        # of its points; append the exact (dense-output) event state as the
        # final sample so `x[-1] == x_target` to solver tolerance, not to
        # grid spacing -- architecture.md S6.3 notes this event exists for
        # the min-time objective, which needs that precision.
        idx = event_names.index(termination_reason)
        t_event = float(sol.t_events[idx][-1])
        y_event = sol.y_events[idx][-1]
        if not t_list or not math.isclose(t_list[-1], t_event, rel_tol=0.0, abs_tol=1e-12):
            t_list.append(t_event)
            x_list.append(float(y_event[0]))
            v_list.append(float(y_event[1]))

    t = np.asarray(t_list)
    x = np.asarray(x_list)
    v = np.asarray(v_list)
    n = len(t)

    a = np.empty(n)
    omega_2a = np.empty(n)
    omega_2b = np.empty(n)
    omega_3b = np.empty(n)
    tension = np.empty(n)
    r_2a_x = np.empty(n)
    r_2a_z = np.empty(n)
    r_2b_x = np.empty(n)
    r_2b_z = np.empty(n)
    power_pedal = np.empty(n)
    power_aero = np.empty(n)
    power_rr = np.empty(n)
    power_brake = np.empty(n)
    power_gravity = np.empty(n)

    dt = spec.drivetrain
    r_2a = spec.rear.r_2a
    big_m = spec.M
    g = spec.env.g
    for i in range(n):
        inst = _full_state(ctx, float(t[i]), float(x[i]), float(v[i]))
        a[i] = inst.a
        omega_2a[i] = inst.omega_2a
        omega_2b[i] = inst.omega_2b
        omega_3b[i] = inst.omega_3b
        tension[i] = inst.T
        r_2a_x[i] = inst.R_2a_x
        r_2a_z[i] = inst.R_2a_z
        r_2b_x[i] = inst.R_2b_x
        r_2b_z[i] = inst.R_2b_z

        sigma_i = float(xp.sign_smooth(v[i], spec.contact.v_eps))
        pedal_term = (
            (spec.frame.r_1_pedal * dt.r_2a_cg) / (dt.r_3b_cr * r_2a) * (inst.P_D + inst.P_F)
        )
        brake_term = (sigma_i / r_2a) * (
            spec.rear.r_2a_bk * inst.B_2a + spec.front.r_2b_bk * inst.B_2b
        )
        gravity_term = big_m * g * np.sin(inst.theta)
        rolling_term = sigma_i * spec.rear.c_2a_rr * big_m * g * np.cos(inst.theta)

        power_pedal[i] = pedal_term * v[i]
        power_brake[i] = brake_term * v[i]
        power_gravity[i] = gravity_term * v[i]
        power_rr[i] = rolling_term * v[i]
        power_aero[i] = inst.aero.A_1_x * v[i]

    margins = core_limits.limit_margins(
        R_2a_x=r_2a_x,
        R_2a_z=r_2a_z,
        R_2b_x=r_2b_x,
        R_2b_z=r_2b_z,
        T=tension,
        mu_s=spec.contact.mu_s,
    )

    meta: dict[str, Any] = {
        "method": method,
        "rtol": rtol,
        "atol": atol,
        "v_eps": spec.contact.v_eps,
        "x0": x0,
        "v0": v0,
        "theta_3b0": theta_3b0,
        "x_target": x_target,
        "t_span": list(t_span),
        "solver_success": bool(sol.success),
        "solver_message": sol.message,
        "n_rhs_evals": int(sol.nfev),
        "termination_reason": termination_reason,
        "limit_crossings": {
            name: [float(te) for te in sol.t_events[event_names.index(name)]]
            for name in _MARGIN_NAMES
        },
    }

    return Trajectory(
        t=t,
        x=x,
        z=np.zeros(n),
        v=v,
        a=a,
        pitch=np.zeros(n),
        pitch_rate=np.zeros(n),
        omega_2a=omega_2a,
        omega_2b=omega_2b,
        omega_3b=omega_3b,
        T=tension,
        R_2a_x=r_2a_x,
        R_2a_z=r_2a_z,
        R_2b_x=r_2b_x,
        R_2b_z=r_2b_z,
        power_pedal=power_pedal,
        power_ground=np.zeros(n),
        power_aero=power_aero,
        power_rr=power_rr,
        power_brake=power_brake,
        power_gravity=power_gravity,
        margins=margins,
        modes=None,
        spec=spec,
        meta=meta,
    )
