"""planar/assembly.py -- build_bike(): the full 12-DOF/7-constraint bike
(architecture.md S7.2) and build_trajectory(): the `core.results.Trajectory`
post-processor for a planar run.

**This is the only place bike topology may be hard-coded** (architecture.md
S7's charter, restated by this ticket): every body name (`"1"`, `"2a"`,
`"2b"`, `"3b"`), every joint point (`A`, `B`, `C`), and every force
element's wiring lives here; `planar/system.py`/`planar/solver.py`/
`planar/integrate.py` (T5) and `planar/contact/*` (T6) stay generic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt

from bikephysics.core.aero import ConstantCdA
from bikephysics.core.atmosphere import atmosphere_from_env
from bikephysics.core.brakes import BrakeInput
from bikephysics.core.limits import limit_margins
from bikephysics.core.params import BikeSpec
from bikephysics.core.pedals import PedalInput
from bikephysics.core.results import Trajectory
from bikephysics.core.road import Road
from bikephysics.planar.body import RigidBody, rotation_matrix
from bikephysics.planar.constraints.revolute import RevoluteJoint
from bikephysics.planar.contact.regularized import RegularizedContact
from bikephysics.planar.drivetrain import (
    CRANKSET_MASS_PLACEHOLDER,
    DrivetrainAssembly,
    build_drivetrain,
)
from bikephysics.planar.forces.aerodynamic import Aerodynamic
from bikephysics.planar.forces.bearing import BearingFriction
from bikephysics.planar.forces.brake import BrakeTorque
from bikephysics.planar.forces.contact import ContactForce
from bikephysics.planar.forces.pedal import PedalForce
from bikephysics.planar.integrate import PlanarRunResult, baumgarte_gains
from bikephysics.planar.solver import solve_accelerations
from bikephysics.planar.state import SystemLayout
from bikephysics.planar.system import MultibodySystem

FloatArray = npt.NDArray[np.float64]

ContactModelName = Literal["regularized"]


def static_load_split(spec: BikeSpec) -> tuple[float, float]:
    """Static normal-load split `(R_2a_z, R_2b_z)` [N] on flat, level,
    stationary ground (`x_ddot = 0`, `theta = 0`, no aero, no braking) --
    the plain two-point-support lever-arm balance about each contact
    point. Computed independently here (the planar tier must not import
    `bikephysics.simplified`, architecture.md S4's cross-tier import ban);
    it is algebraically identical to `simplified.model.normal_loads` at
    that same operating point (and the cross-tier gate, `validation
    .cross_tier`, checks exactly that, importing *both* tiers from its own
    validation-only vantage point). Used both to size each wheel's
    `RegularizedContact.m_eff` (`build_bike`) and by `planar/kinematics
    .py`'s static-load report (S7.8)."""
    l_c = spec.frame.l_1_a
    l_d = spec.frame.l_1_B - spec.frame.l_1_a
    length = l_c + l_d
    weight = spec.M * spec.env.g
    r_2a_z = weight * l_d / length
    r_2b_z = weight * l_c / length
    return r_2a_z, r_2b_z


@dataclass(frozen=True)
class BikeAssembly:
    """Everything `build_bike` constructs: the generic `MultibodySystem`
    plus named handles to every bike-specific element, for
    `build_trajectory`'s post-processing and for `cli/planar_cmds.py`/
    `validation/cross_tier.py` to query directly (e.g. `drivetrain.chain`
    for the chain-tension cross-check)."""

    system: MultibodySystem
    layout: SystemLayout
    spec: BikeSpec
    road: Road
    drivetrain: DrivetrainAssembly
    rear_contact: RegularizedContact
    front_contact: RegularizedContact
    pedal_force: PedalForce
    aero: Aerodynamic
    rear_brake: BrakeTorque
    front_brake: BrakeTorque
    rear_bearing: BearingFriction
    front_bearing: BearingFriction
    bb_bearing: BearingFriction


def build_bike(
    spec: BikeSpec,
    road: Road,
    pedal_input: PedalInput,
    brake_input: BrakeInput,
    contact_model: ContactModelName = "regularized",
) -> BikeAssembly:
    """The full 12-DOF/7-constraint bike (architecture.md S7.2): bodies
    `1` (frame+rider), `2a` (rear wheel), `2b` (front wheel), `3b`
    (crankset, via `planar/drivetrain.py::build_drivetrain`); constraints
    `RevoluteJoint(1,2a,A)`, `RevoluteJoint(1,2b,B)`,
    `RevoluteJoint(1,3b,C)`, `IdealChain(2a,3b)` (7 rows total); forces
    `Weight` (all 4 bodies), `Aerodynamic` (1), `PedalForce`, `BrakeTorque`
    x2, `BearingFriction` x3, `ContactForce` x2 (architecture.md S7.3).

    `contact_model` selects the `ContactModel` implementation
    (architecture.md S9's `--contact` flag); only `"regularized"` (T6)
    exists before T11's `EventDrivenContact`."""
    if contact_model != "regularized":
        raise NotImplementedError(
            f"contact_model={contact_model!r} is not implemented; only "
            "'regularized' (architecture.md S7.6.2, T6) exists before T11's "
            "EventDrivenContact."
        )

    fr = spec.frame
    frame_body = RigidBody(
        name="1",
        mass=fr.m_1,
        inertia=fr.I_1,
        points={
            "A": (-fr.l_1_a, -fr.h_1_a),
            "B": (fr.l_1_B - fr.l_1_a, -fr.h_1_a),
            "C": (fr.l_1_C - fr.l_1_a, fr.h_1_C - fr.h_1_a),
        },
    )
    rear_wheel = RigidBody(
        name="2a",
        mass=spec.rear.m_2a,
        inertia=spec.rear.I_2a,
        points={"axle": (-spec.rear.r_2a_a, 0.0)},
    )
    front_wheel = RigidBody(
        name="2b",
        mass=spec.front.m_2b,
        inertia=spec.front.I_2b,
        points={"axle": (-spec.front.r_2b_a, 0.0)},
    )
    drivetrain = build_drivetrain(spec, frame_body)

    system = MultibodySystem()
    system.add_body(frame_body)
    system.add_body(rear_wheel)
    system.add_body(front_wheel)
    system.add_body(drivetrain.crank_body)
    system.add_gravity(g=spec.env.g)

    aero_model = ConstantCdA(C_1_x_0=spec.aero.C_1_x_0, A_1=spec.aero.A_1, l_1=spec.aero.l_1)
    atmosphere = atmosphere_from_env(
        atmosphere=spec.env.atmosphere,
        rho_constant=spec.env.rho,
        altitude=spec.env.altitude,
        relative_humidity=spec.env.RH,
        temperature_k=spec.env.T,
    )
    aero = Aerodynamic(
        name="aero",
        aero_model=aero_model,
        rho=atmosphere.density(),
        mu=atmosphere.viscosity(),
        wind_x=spec.env.wind_x,
        wind_z=spec.env.wind_z,
    )
    system.add_force(aero)

    pedal_force = PedalForce(
        name="pedal", pedal_input=pedal_input, r_1_pedal=fr.r_1_pedal,
        crank_body="3b", frame_body="1",
    )
    system.add_force(pedal_force)

    rear_brake = BrakeTorque(
        name="brake_rear",
        wheel_body="2a",
        which="rear",
        r_bk=spec.rear.r_2a_bk,
        radius=spec.rear.r_2a,
        road=road,
        brake_input=brake_input,
        axle_point=(-spec.rear.r_2a_a, 0.0),
        v_eps=spec.contact.v_eps,
    )
    front_brake = BrakeTorque(
        name="brake_front",
        wheel_body="2b",
        which="front",
        r_bk=spec.front.r_2b_bk,
        radius=spec.front.r_2b,
        road=road,
        brake_input=brake_input,
        axle_point=(-spec.front.r_2b_a, 0.0),
        v_eps=spec.contact.v_eps,
    )
    system.add_force(rear_brake)
    system.add_force(front_brake)

    dt = spec.drivetrain
    rear_bearing = BearingFriction(
        name="bearing_rear",
        joint_name="joint_A",
        rotating_body="2a",
        mu_b=dt.mu_b,
        d_bearing=dt.d_bearing.get("rear_axle", 0.01),
    )
    front_bearing = BearingFriction(
        name="bearing_front",
        joint_name="joint_B",
        rotating_body="2b",
        mu_b=dt.mu_b,
        d_bearing=dt.d_bearing.get("front_axle", 0.01),
    )
    bb_bearing = BearingFriction(
        name="bearing_bb",
        joint_name="bb_joint",
        rotating_body="3b",
        mu_b=dt.mu_b,
        d_bearing=dt.d_bearing.get("bb", 0.024),
    )
    system.add_force(rear_bearing)
    system.add_force(front_bearing)
    system.add_force(bb_bearing)

    r_2a_z_static, r_2b_z_static = static_load_split(spec)
    m_total = spec.M + CRANKSET_MASS_PLACEHOLDER
    rear_contact = RegularizedContact(
        name="contact_rear",
        body="2a",
        radius=spec.rear.r_2a,
        c_rr=spec.rear.c_2a_rr,
        mu_s=spec.contact.mu_s,
        mu_k=spec.contact.mu_k,
        M_total=m_total,
        m_eff=r_2a_z_static / spec.env.g,
        g=spec.env.g,
        delta_target=spec.contact.delta_target,
        zeta_N=spec.contact.zeta_N,
        v_eps=spec.contact.v_eps,
        v_stribeck=spec.contact.v_stribeck,
        axle_point=(-spec.rear.r_2a_a, 0.0),
        k_N_override=spec.contact.k_N,
    )
    front_contact = RegularizedContact(
        name="contact_front",
        body="2b",
        radius=spec.front.r_2b,
        c_rr=spec.front.c_2b_rr,
        mu_s=spec.contact.mu_s,
        mu_k=spec.contact.mu_k,
        M_total=m_total,
        m_eff=r_2b_z_static / spec.env.g,
        g=spec.env.g,
        delta_target=spec.contact.delta_target,
        zeta_N=spec.contact.zeta_N,
        v_eps=spec.contact.v_eps,
        v_stribeck=spec.contact.v_stribeck,
        axle_point=(-spec.front.r_2b_a, 0.0),
        k_N_override=spec.contact.k_N,
    )
    system.add_force(
        ContactForce(name="contact_force_rear", model=rear_contact, road=road, spec=spec)
    )
    system.add_force(
        ContactForce(name="contact_force_front", model=front_contact, road=road, spec=spec)
    )

    system.add_constraint(
        RevoluteJoint.from_bodies("joint_A", frame_body, "A", rear_wheel, "axle")
    )
    system.add_constraint(
        RevoluteJoint.from_bodies("joint_B", frame_body, "B", front_wheel, "axle")
    )
    system.add_constraint(drivetrain.bb_joint)
    system.add_constraint(drivetrain.chain)

    return BikeAssembly(
        system=system,
        layout=system.layout,
        spec=spec,
        road=road,
        drivetrain=drivetrain,
        rear_contact=rear_contact,
        front_contact=front_contact,
        pedal_force=pedal_force,
        aero=aero,
        rear_brake=rear_brake,
        front_brake=front_brake,
        rear_bearing=rear_bearing,
        front_bearing=front_bearing,
        bb_bearing=bb_bearing,
    )


def _contact_reactions(
    assembly: BikeAssembly, t: float, q: FloatArray, u: FloatArray, cache: dict[str, float]
) -> tuple[float, float, float, float, float]:
    """`(R_2a_x, R_2a_z, R_2b_x, R_2b_z, power_ground_sink)` at one
    instant, in frame-0 (road-tangent) components (`core.results
    .Trajectory`'s own shared convention) -- recomputed directly from the
    `ContactModel` protocol (not read off a cached force-element diagnostic,
    since `RegularizedContact`/`forces/contact.py` -- both T6 files, out of
    this ticket's boundary -- do not themselves expose the resolved
    `(R_x, R_z)` scalars, only the geometry). `cache` is a local
    x*-warm-start scratch dict, independent of the one `ContactForce` uses
    during integration (both converge to the same `x*` regardless)."""
    layout = assembly.layout
    power_sink = 0.0
    results: list[tuple[float, float]] = []
    for contact in (assembly.rear_contact, assembly.front_contact):
        wrench = contact.wrenches(t, q, u, layout, assembly.road, assembly.spec, cache)[0]
        state = contact.state()
        r_x = float(np.dot(wrench.force_world, state.x_hat_0))
        r_z = float(np.dot(wrench.force_world, state.z_hat_0))
        results.append((r_x, r_z))

        # Mechanical power delivered BY the contact wrench to the wheel
        # body, at the force's *actual* application point
        # (`contact_point_world = axle_world + contact_offset`, not the
        # axle): `v_contact_point = v_axle + omega x contact_offset`
        # (architecture.md S7.1's rotation convention, `omega*(r_z,
        # -r_x)`). An earlier version of this function approximated the
        # normal force's power as `R_z * g_N_dot` (the rate of change of
        # the *gap*, which only depends on `v_axle`, per `contact.geometry
        # .contact_geometry`'s own derivation) -- correct for the
        # spring's own conservative PE bookkeeping, but it silently
        # dropped the `omega x contact_offset` term, which is *not*
        # negligible at realistic spin rates (confirmed empirically: the
        # dropped term closed an energy-audit gap of several percent on
        # a rolling-road run). The spring's own tiny elastic PE (~mm-scale
        # penetration, sub-joule) is not tracked as a separate potential-
        # energy channel and is negligible against the sink magnitudes
        # here, so the *total* wrench power (not just its damping/
        # friction components) is used directly: almost all of it is
        # genuine dissipation.
        omega = layout.omega(u, contact.body)
        offset = state.contact_offset
        v_contact_point = state.v_axle_world + omega * np.array([offset[1], -offset[0]])
        power_in = float(np.dot(wrench.force_world, v_contact_point))
        power_sink += -power_in

    (r_2a_x, r_2a_z), (r_2b_x, r_2b_z) = results
    return r_2a_x, r_2a_z, r_2b_x, r_2b_z, power_sink


def build_trajectory(
    assembly: BikeAssembly,
    result: PlanarRunResult,
    *,
    meta: dict[str, object] | None = None,
    h_typ: float = 1e-3,
) -> Trajectory:
    """`PlanarRunResult` (raw `(t, q, u)` + drift/mode bookkeeping) ->
    `core.results.Trajectory` (architecture.md S5.5), by re-evaluating the
    physics once per output sample (never inside an RHS -- this is
    post-processing, matching `simplified/simulate.py`'s own
    `_full_state`-after-the-fact pattern)."""
    layout = assembly.layout
    system = assembly.system
    spec = assembly.spec
    t = result.t
    q = result.q
    u = result.u
    n = len(t)

    x = q[:, layout.idx_x("1")].copy()
    z = q[:, layout.idx_z("1")].copy()
    v = u[:, layout.idx_x("1")].copy()
    pitch = q[:, layout.idx_theta("1")].copy()
    pitch_rate = u[:, layout.idx_theta("1")].copy()
    omega_2a = u[:, layout.idx_theta("2a")].copy()
    omega_2b = u[:, layout.idx_theta("2b")].copy()
    omega_3b = u[:, layout.idx_theta("3b")].copy()

    a = np.empty(n)
    tension = np.empty(n)
    r_2a_x = np.empty(n)
    r_2a_z = np.empty(n)
    r_2b_x = np.empty(n)
    r_2b_z = np.empty(n)
    power_pedal = np.empty(n)
    power_aero = np.empty(n)
    power_ground = np.empty(n)
    power_brake = np.empty(n)
    power_gravity = np.empty(n)

    alpha, beta = baumgarte_gains(h_typ)
    contact_cache: dict[str, float] = {}
    for i in range(n):
        t_i = float(t[i])
        q_i = q[i]
        u_i = u[i]
        solve = solve_accelerations(system, t_i, q_i, u_i, alpha=alpha, beta=beta)
        a[i] = float(solve.a[layout.idx_x("1")])
        lam_chain = solve.multipliers.get(assembly.drivetrain.chain.name)
        tension[i] = float(lam_chain[0]) if lam_chain is not None else 0.0

        rx_a, rz_a, rx_b, rz_b, ground_sink = _contact_reactions(
            assembly, t_i, q_i, u_i, contact_cache
        )
        r_2a_x[i], r_2a_z[i], r_2b_x[i], r_2b_z[i] = rx_a, rz_a, rx_b, rz_b
        power_ground[i] = ground_sink

        pedal_diag = assembly.pedal_force.diagnostics(t_i, q_i, u_i, layout)
        power_pedal[i] = pedal_diag["power"]

        power_aero[i] = -_aero_power_in(assembly.aero, q_i, u_i, layout)

        brake_power = (
            assembly.rear_brake.diagnostics(t_i, q_i, u_i, layout)["power"]
            + assembly.front_brake.diagnostics(t_i, q_i, u_i, layout)["power"]
        )
        power_brake[i] = brake_power

        # Gravity power (a genuine, signed, non-dissipative channel, unlike
        # the sink channels above): rate of potential-energy increase,
        # `sum_k m_k * g * v_z,k` over every registered body (the shared
        # z-up convention, so climbing -- v_z > 0 -- gives power_gravity
        # > 0, matching `simplified/simulate.py`'s own
        # `M*g*sin(theta)*v` sign convention).
        power_gravity[i] = _gravity_power(system, layout, u_i, spec.env.g)

    # No separate rolling-resistance power channel in the planar tier:
    # c_2a_rr/c_2b_rr only shift the contact geometry (contact_offset),
    # they do not add an extra additive force term the way the Simplified
    # tier's own rolling_term does -- whatever rolling-resistance-like
    # dissipation results is already folded into `power_ground` above.
    power_rr = np.zeros(n)

    margins = limit_margins(
        R_2a_x=r_2a_x, R_2a_z=r_2a_z, R_2b_x=r_2b_x, R_2b_z=r_2b_z,
        T=tension, mu_s=spec.contact.mu_s,
    )

    return Trajectory(
        t=t.copy(),
        x=x,
        z=z,
        v=v,
        a=a,
        pitch=pitch,
        pitch_rate=pitch_rate,
        omega_2a=omega_2a,
        omega_2b=omega_2b,
        omega_3b=omega_3b,
        T=tension,
        R_2a_x=r_2a_x,
        R_2a_z=r_2a_z,
        R_2b_x=r_2b_x,
        R_2b_z=r_2b_z,
        power_pedal=power_pedal,
        power_ground=power_ground,
        power_aero=power_aero,
        power_rr=power_rr,
        power_brake=power_brake,
        power_gravity=power_gravity,
        margins=margins,
        modes=result.modes,
        spec=spec,
        meta=dict(meta or {}),
    )


def _gravity_power(system: MultibodySystem, layout: SystemLayout, u: FloatArray, g: float) -> float:
    """`sum_k m_k * g * v_z,k` over every registered body -- the rate of
    potential-energy increase (the shared z-up convention), matching
    `simplified/simulate.py`'s own `power_gravity = M*g*sin(theta)*v` sign
    convention (positive while climbing)."""
    total = 0.0
    for name, body in system.bodies.items():
        total += body.mass * g * layout.vz(u, name)
    return total


def _aero_power_in(aero: Aerodynamic, q: FloatArray, u: FloatArray, layout: SystemLayout) -> float:
    """Mechanical power delivered *to* body `"1"` by the `Aerodynamic`
    force element: `F_world . v_point_world + moment * omega_1` (the
    element's own `apply_wrench` convention, S7.3), recomputed from
    `Aerodynamic`'s public `diagnostics()`/`point` fields directly rather
    than approximated as `A_1_x * v_1` (an earlier version of this
    function's shortcut, exact only at `theta_1 = 0` -- a non-flat road
    gives `theta_1 != 0`, so the shortcut dropped a `cos(theta_1)`
    correction; the direct dot product below has none)."""
    diag = aero.diagnostics(0.0, q, u, layout)
    theta_1 = layout.theta(q, "1")
    r1 = rotation_matrix(theta_1)
    force_body1 = np.array([-diag["A_1_x"], -diag["A_1_z"]])
    force_world = r1 @ force_body1
    local_point = np.zeros(2) if aero.point is None else np.asarray(aero.point, dtype=float)
    point_world = layout.position(q, "1") + r1 @ local_point
    v_point = _point_velocity_at(q, u, layout, "1", point_world)
    omega_1 = layout.omega(u, "1")
    return float(np.dot(force_world, v_point)) + (-diag["A_1_m"]) * omega_1


def _point_velocity_at(
    q: FloatArray, u: FloatArray, layout: SystemLayout, body: str, world_point: FloatArray
) -> FloatArray:
    """World velocity of the material point of `body` instantaneously at
    `world_point` (architecture.md S7.1's rotation convention)."""
    cog = layout.position(q, body)
    omega = layout.omega(u, body)
    v_cog = layout.velocity(u, body)
    r = world_point - cog
    return v_cog + omega * np.array([r[1], -r[0]])
