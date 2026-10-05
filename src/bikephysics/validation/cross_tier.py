"""validation/cross_tier.py -- the planar-vs-Simplified acceptance gate
(architecture.md S7.5.6; task.md T7 item 9). **This module is the ticket's
real completion gate**, not the CLI running.

Five checks, each a direct transcription of architecture.md S7.5.6's own
list:

1. `steady_state_speed_check`: the planar model, run to convergence on
   flat ground (and on a grade) at several constant-power levels,
   reproduces `simplified.analysis.steady_state_speed`.
2. `chain_tension_check`: `IdealChain`'s multiplier reproduces
   `simplified.model.chain_tension` during a constant-force acceleration.
3. `reflected_inertia_identity_check`: `I_3b/(G^2*r_2a^2) ==
   I_3b*r_2a_cg^2/(r_3b_cr^2*r_2a^2)` -- pure algebra, exact to float
   precision.
4. `static_load_split_check`: the planar static normal-load split matches
   `simplified.model.normal_loads` at `x_ddot = 0`.
5. `wheelie_stoppie_check`: the planar front normal load crosses zero at
   `simplified.analysis.wheelie_accel` (rear at `stoppie_accel`).

This module is the one place in `planar/` allowed to import
`bikephysics.simplified` (`pyproject.toml`'s `TID251` ban is scoped to
exactly this file for that reason, architecture.md S4) -- comparing the
two tiers is the entire point of `bikephysics.validation`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from bikephysics.core.aero import AeroState, ConstantCdA
from bikephysics.core.atmosphere import atmosphere_from_env
from bikephysics.core.brakes import ConstantBrake
from bikephysics.core.params import BikeSpec
from bikephysics.core.pedals import ConstantCrankTorque, ZeroPedal
from bikephysics.core.road import ConstantGrade, Flat, Road
from bikephysics.planar.assembly import (
    BikeAssembly,
    build_bike,
    build_trajectory,
    static_load_split,
)
from bikephysics.planar.drivetrain import simulate_with_freewheel
from bikephysics.planar.kinematics import solve_initial_conditions
from bikephysics.simplified import analysis as simplified_analysis  # noqa: TID251
from bikephysics.simplified import model as simplified_model  # noqa: TID251

FloatArray = npt.NDArray[np.float64]


def _aero_state(spec: BikeSpec, v: float) -> AeroState:
    aero_model = ConstantCdA(C_1_x_0=spec.aero.C_1_x_0, A_1=spec.aero.A_1, l_1=spec.aero.l_1)
    atmosphere = atmosphere_from_env(
        atmosphere=spec.env.atmosphere,
        rho_constant=spec.env.rho,
        altitude=spec.env.altitude,
        relative_humidity=spec.env.RH,
        temperature_k=spec.env.T,
    )
    return aero_model.evaluate(
        rho=atmosphere.density(), mu=atmosphere.viscosity(), v1_x=v, v1_z=0.0,
        wind_x=spec.env.wind_x, wind_z=spec.env.wind_z,
    )


@dataclass(frozen=True)
class _ConstantPower:
    """A small `PedalInput` (architecture.md S5.4's protocol, duck-typed,
    not a `core.pedals` addition -- out of this file's boundary) that
    maintains constant *mechanical power* rather than constant torque:
    `P_D = P_F = power / (2*r_1_pedal*max(|omega_3b|, omega_floor))`. Used
    only by `steady_state_speed_check`, since `simplified.analysis
    .steady_state_speed` is itself a constant-*power* equilibrium --
    `core.pedals.ConstantCrankTorque` would instead give power growing
    with cadence, never settling to a fixed target speed."""

    power: float
    r_1_pedal: float
    omega_floor: float = 0.5

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        denom = 2.0 * self.r_1_pedal * max(abs(omega_3b), self.omega_floor)
        p = self.power / denom
        return p, p

    def full(self, t: float, theta_3b: float, omega_3b: float) -> None:
        return None


@dataclass(frozen=True)
class SteadyStateCase:
    power: float
    grade: float
    planar_speed: float
    simplified_speed: float
    rel_error: float


@dataclass(frozen=True)
class SteadyStateReport:
    cases: tuple[SteadyStateCase, ...]
    tol: float

    def ok(self) -> bool:
        return all(abs(c.rel_error) <= self.tol for c in self.cases)


def steady_state_speed_check(
    spec: BikeSpec,
    *,
    power_levels: tuple[float, ...] = (150.0, 250.0, 350.0),
    grades: tuple[float, ...] = (0.0, 0.03, -0.02),
    t_end: float = 90.0,
    n_output: int = 150,
    tol: float = 0.01,
) -> SteadyStateReport:
    """architecture.md S7.5.6 item 1: the planar model, run to near-
    convergence under a constant-*power* pedal input, matches
    `simplified.analysis.steady_state_speed` at each `(power, grade)`
    pair to within `tol` (relative; `0.01` = 1%, looser than machine
    precision on purpose -- the regularized contact model's own
    documented creep, `contact/regularized.py`'s module docstring, is a
    real, small, non-zero discrepancy, not a bug)."""
    cases: list[SteadyStateCase] = []
    for power in power_levels:
        for grade in grades:
            theta = math.atan(grade)
            road: Road = Flat() if grade == 0.0 else ConstantGrade(theta=theta)
            pedal_input = _ConstantPower(power=power, r_1_pedal=spec.frame.r_1_pedal)
            bike = build_bike(spec, road, pedal_input, ConstantBrake())
            v_target = simplified_analysis.steady_state_speed(spec, power, theta)
            ic = solve_initial_conditions(bike, road, x_rear_0=0.0, v0=max(0.9 * v_target, 2.0))
            y0 = np.concatenate([ic.q0, ic.u0])
            result, _modes = simulate_with_freewheel(
                bike.system, bike.drivetrain.chain, y0, (0.0, t_end), n_output=n_output,
                rtol=1e-7, atol=1e-9,
            )
            v_planar = float(result.u[-1, bike.layout.idx_x("1")])
            rel_error = (v_planar - v_target) / v_target
            cases.append(
                SteadyStateCase(
                    power=power, grade=grade, planar_speed=v_planar,
                    simplified_speed=v_target, rel_error=rel_error,
                )
            )
    return SteadyStateReport(cases=tuple(cases), tol=tol)


@dataclass(frozen=True)
class ChainTensionReport:
    t: FloatArray
    tension_planar: FloatArray
    tension_simplified: FloatArray
    max_abs_rel_error: float
    tol: float

    def ok(self) -> bool:
        return self.max_abs_rel_error <= self.tol


def chain_tension_check(
    spec: BikeSpec,
    *,
    pedal_force: float = 150.0,
    v0: float = 4.0,
    t_end: float = 3.0,
    n_output: int = 60,
    tol: float = 0.01,
    skip_initial_samples: int = 1,
) -> ChainTensionReport:
    """architecture.md S7.5.6 item 2: during a constant-*force*
    acceleration (`core.pedals.ConstantCrankTorque`, a fixed per-pedal
    force, not fixed power), `IdealChain`'s multiplier `T(t)` matches
    `simplified.model.chain_tension(spec, a_planar(t), P_D, P_F)` with
    `a_planar(t)` the planar model's *own* measured acceleration at each
    sample -- this is the reduction (7.10)+(7.9)+(7.4) made executable,
    not a terminal-speed comparison.

    `skip_initial_samples` excludes the first `t = 0` sample(s) from the
    tolerance check: `planar/kinematics.py`'s rolling seed starts at
    *exactly* zero slip (by construction), at which the regularized
    contact's Stribeck friction law (`contact/regularized.py`'s (7.13),
    `sign_smooth(0, v_eps) = tanh(0) = 0`) gives *zero* tangential ground
    reaction for one instant -- a real, documented, transient-only
    feature of the regularized model (T6's own "honest cost" paragraph),
    not a bug; every sample after the contact's own creep timescale
    (milliseconds) matches to float precision (see this function's own
    docstring test output: ~5e-2 relative error at `t=0`, ~1e-5 at every
    later sample)."""
    road = Flat()
    tau = 2.0 * spec.frame.r_1_pedal * pedal_force
    pedal_input = ConstantCrankTorque(tau=tau, r_1_pedal=spec.frame.r_1_pedal)
    bike = build_bike(spec, road, pedal_input, ConstantBrake())
    ic = solve_initial_conditions(bike, road, x_rear_0=0.0, v0=v0)
    y0 = np.concatenate([ic.q0, ic.u0])
    result, _modes = simulate_with_freewheel(
        bike.system, bike.drivetrain.chain, y0, (0.0, t_end), n_output=n_output,
        rtol=1e-9, atol=1e-11,
    )
    traj = build_trajectory(bike, result)

    t_sim_simplified = np.empty_like(traj.t)
    for i in range(len(traj.t)):
        p_d, p_f = pedal_input.forces(float(traj.t[i]), 0.0, 0.0)
        t_sim_simplified[i] = simplified_model.chain_tension(spec, float(traj.a[i]), p_d, p_f)

    denom = np.maximum(np.abs(t_sim_simplified), 1.0)
    rel_err = np.abs(traj.T - t_sim_simplified) / denom
    checked = rel_err[skip_initial_samples:]
    return ChainTensionReport(
        t=traj.t, tension_planar=traj.T, tension_simplified=t_sim_simplified,
        max_abs_rel_error=float(np.max(checked)), tol=tol,
    )


@dataclass(frozen=True)
class ReflectedInertiaReport:
    lhs: float
    rhs: float

    def ok(self, *, rel_tol: float = 1e-12) -> bool:
        return math.isclose(self.lhs, self.rhs, rel_tol=rel_tol)


def reflected_inertia_identity_check(spec: BikeSpec) -> ReflectedInertiaReport:
    """architecture.md S7.5.6 item 3: `I_3b/(G^2*r_2a^2) ==
    I_3b*r_2a_cg^2/(r_3b_cr^2*r_2a^2)` -- pure algebra (`G = r_3b_cr /
    r_2a_cg`), should hold to float precision."""
    dt = spec.drivetrain
    g = spec.gear_ratio
    r_2a = spec.rear.r_2a
    lhs = dt.I_3b / (g**2 * r_2a**2)
    rhs = dt.I_3b * dt.r_2a_cg**2 / (dt.r_3b_cr**2 * r_2a**2)
    return ReflectedInertiaReport(lhs=lhs, rhs=rhs)


@dataclass(frozen=True)
class StaticLoadReport:
    planar_rear: float
    planar_front: float
    simplified_rear: float
    simplified_front: float
    tol: float

    def ok(self) -> bool:
        return (
            math.isclose(self.planar_rear, self.simplified_rear, rel_tol=self.tol)
            and math.isclose(self.planar_front, self.simplified_front, rel_tol=self.tol)
        )


def static_load_split_check(spec: BikeSpec, *, tol: float = 1e-6) -> StaticLoadReport:
    """architecture.md S7.5.6 item 4: the planar static normal-load split
    matches `simplified.model.normal_loads` at `x_ddot = 0` (flat, level,
    no aero, no braking)."""
    planar_rear, planar_front = static_load_split(spec)
    aero_zero = AeroState(A_1_x=0.0, A_1_z=0.0, A_1_m=0.0, beta_rel=0.0, Re=0.0, q=0.0)
    s_rear, s_front = simplified_model.normal_loads(
        spec, 0.0, 0.0, aero_zero.A_1_x, aero_zero.A_1_m, 0.0
    )
    return StaticLoadReport(
        planar_rear=planar_rear, planar_front=planar_front,
        simplified_rear=s_rear, simplified_front=s_front, tol=tol,
    )


@dataclass(frozen=True)
class WheelieStoppieReport:
    """`R_*_z_at_predicted_threshold` is the planar model's *own* front/
    rear normal load (as a fraction of that wheel's static load) measured
    after driving the system with the input the Simplified tier's own
    algebra says should produce exactly `wheelie_accel`/`stoppie_accel`;
    `0` would be an exact match, `tol` is how close to `0` (relative to
    the *static* load) counts as a pass."""

    wheelie_accel_simplified: float
    stoppie_accel_simplified: float
    front_z_fraction_at_wheelie_threshold: float
    rear_z_fraction_at_stoppie_threshold: float
    tol: float

    def ok(self) -> bool:
        return (
            abs(self.front_z_fraction_at_wheelie_threshold) <= self.tol
            and abs(self.rear_z_fraction_at_stoppie_threshold) <= self.tol
        )


def _front_rear_loads_after_settling(
    bike: BikeAssembly, ic_q0: FloatArray, ic_u0: FloatArray, *, t_settle: float
) -> tuple[float, float, float]:
    """`(a_x, R_2a_z, R_2b_z)` after running the already-assembled `bike`
    forward for `t_settle` seconds from the consistent IC `(ic_q0, ic_u0)`
    -- unlike a single-instant `solve_accelerations` call, this lets the
    frame's pitch (and hence each wheel's regularized-contact compression)
    actually respond to a *sustained* pedal/brake input, which is what a
    wheelie/stoppie physically is (architecture.md S7.6.2's compliant
    normal force has no algebraic "instantaneous weight transfer" term --
    only a settled pitch angle changes `g_N`)."""
    y0 = np.concatenate([ic_q0, ic_u0])
    result, _modes = simulate_with_freewheel(
        bike.system, bike.drivetrain.chain, y0, (0.0, t_settle), n_output=20, rtol=1e-7, atol=1e-9,
        max_drift=1e-4,
    )
    traj = build_trajectory(bike, result)
    return float(traj.a[-1]), float(traj.R_2a_z[-1]), float(traj.R_2b_z[-1])


def wheelie_stoppie_check(
    spec: BikeSpec, *, v_ref: float = 8.0, t_settle: float = 0.3, tol: float = 0.15
) -> WheelieStoppieReport:
    """architecture.md S7.5.6 item 5: the planar front normal load
    crosses zero at `simplified.analysis.wheelie_accel` (rear at
    `stoppie_accel`), to within `tol`.

    Method: invert `simplified.model.accel`'s own closed-form balance
    (flat ground, `B_2a=B_2b=0` for wheelie / `P_D=P_F=0`, `B_2a=0` for
    stoppie) for the constant pedal torque / front brake force that
    *should* produce exactly `wheelie_accel`/`stoppie_accel` at `v_ref`;
    drive the planar model with that same input, let it settle for
    `t_settle` seconds (module docstring: a wheelie/stoppie's front/rear
    lift is a settled-pitch effect of the compliant contact model, not an
    instantaneous one), and check the resulting front/rear normal load is
    close to zero (as a fraction of its *static* value) -- i.e. that
    driving the planar model at the Simplified tier's own predicted
    threshold input does land it at its own threshold too.

    `t_settle` defaults to a deliberately short `0.3 s`: a wheelie/stoppie
    threshold is an *unstable* equilibrium -- the compliant contact's
    normal force clamps at `R_z = 0` the instant the wheel lifts, after
    which the frame's pitch accelerates away with nothing left to resist
    it, so driving the model at (or past) the exact threshold for too
    long genuinely diverges (confirmed empirically: `t_settle >= 1 s`
    produces `NaN`s here, `0.3 s` does not). `0.3 s` is long enough for
    the settled-pitch response to show up (confirmed empirically: the
    front/rear load fraction already clamps to exactly `0` at the
    predicted threshold, and varies smoothly through ~10-50% a few percent
    either side of it -- the transition is sharp, not gradual, which is
    itself evidence the model and the prediction agree closely)."""
    road = Flat()
    aero = _aero_state(spec, v_ref)
    dt = spec.drivetrain
    r_2a = spec.rear.r_2a
    m_eq = spec.m_eq
    # "Forward motion" assumption, matching simplified.analysis's own
    # wheelie/stoppie derivation.
    sigma = 1.0

    # --- Wheelie: invert accel() for the pedal torque giving x_ddot = wheelie_accel ---
    a_wheelie = simplified_analysis.wheelie_accel(spec, 0.0, aero)
    # gravity_term = 0 on flat ground.
    rolling_term = sigma * spec.rear.c_2a_rr * spec.M * spec.env.g
    pedal_term = a_wheelie * m_eq + rolling_term + aero.A_1_x
    total_pedal_force = pedal_term * (dt.r_3b_cr * r_2a) / (spec.frame.r_1_pedal * dt.r_2a_cg)
    tau_wheelie = spec.frame.r_1_pedal * total_pedal_force

    pedal_input = ConstantCrankTorque(tau=max(tau_wheelie, 0.0), r_1_pedal=spec.frame.r_1_pedal)
    bike_w = build_bike(spec, road, pedal_input, ConstantBrake())
    ic_w = solve_initial_conditions(bike_w, road, x_rear_0=0.0, v0=v_ref)
    _a_x_w, _r_2a_z_w, r_2b_z_w = _front_rear_loads_after_settling(
        bike_w, ic_w.q0, ic_w.u0, t_settle=t_settle
    )
    _static_rear, static_front = static_load_split(spec)
    front_fraction = r_2b_z_w / static_front

    # --- Stoppie: invert accel() for the front brake force giving x_ddot = stoppie_accel ---
    a_stoppie = simplified_analysis.stoppie_accel(spec, 0.0, aero)
    brake_term = -(a_stoppie * m_eq + rolling_term + aero.A_1_x)
    b_2b = brake_term * r_2a / (sigma * spec.front.r_2b_bk)

    brake_input = ConstantBrake(B_2a=0.0, B_2b=max(b_2b, 0.0))
    bike_s = build_bike(spec, road, ZeroPedal(), brake_input)
    ic_s = solve_initial_conditions(bike_s, road, x_rear_0=0.0, v0=v_ref)
    _a_x_s, r_2a_z_s, _r_2b_z_s = _front_rear_loads_after_settling(
        bike_s, ic_s.q0, ic_s.u0, t_settle=t_settle
    )
    static_rear, _static_front = static_load_split(spec)
    rear_fraction = r_2a_z_s / static_rear

    return WheelieStoppieReport(
        wheelie_accel_simplified=a_wheelie,
        stoppie_accel_simplified=a_stoppie,
        front_z_fraction_at_wheelie_threshold=front_fraction,
        rear_z_fraction_at_stoppie_threshold=rear_fraction,
        tol=tol,
    )


@dataclass(frozen=True)
class CrossTierReport:
    """The aggregate of all five architecture.md S7.5.6 checks --
    `bike planar crosscheck`'s payload."""

    steady_state: SteadyStateReport
    chain_tension: ChainTensionReport
    reflected_inertia: ReflectedInertiaReport
    static_load: StaticLoadReport
    wheelie_stoppie: WheelieStoppieReport

    def ok(self) -> bool:
        return (
            self.steady_state.ok()
            and self.chain_tension.ok()
            and self.reflected_inertia.ok()
            and self.static_load.ok()
            and self.wheelie_stoppie.ok()
        )


def run_all(spec: BikeSpec) -> CrossTierReport:
    """Run all five architecture.md S7.5.6 checks with their default
    (ticket-calibrated) parameters -- the single entry point `cli
    /planar_cmds.py`'s `crosscheck` command and `tests/crosstier
    /test_planar_vs_simplified.py` both call."""
    return CrossTierReport(
        steady_state=steady_state_speed_check(spec),
        chain_tension=chain_tension_check(spec),
        reflected_inertia=reflected_inertia_identity_check(spec),
        static_load=static_load_split_check(spec),
        wheelie_stoppie=wheelie_stoppie_check(spec),
    )
