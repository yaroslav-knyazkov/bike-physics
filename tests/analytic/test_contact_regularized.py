"""Analytic/physical acceptance tests for `planar.contact.regularized
.RegularizedContact` (task.md T6; architecture.md S7.6.2's "Done when").

Every test here drives a *standalone* single wheel (one `RigidBody`, no
chassis -- T7 assembles the full bike) through the generic planar engine
(`planar.system`/`planar.integrate`, T5) with exactly one `ContactForce`
registered, and checks a closed-form or first-principles-derived physical
prediction, not a golden-numeric regression.

Two things worth recording before the tests themselves, both already
flagged in `contact/regularized.py`'s own module docstring (duplicated
here briefly since they are *why* several of the numbers below are what
they are, not arbitrary tuning):

1. **(7.12)'s sign and `c_HC` scaling are corrected** in this
   implementation (see `regularized.py`). Both are flagged to the
   architect; this file's own passing tests are the evidence the
   correction is the one that actually produces the documented,
   acceptance-tested behavior (damped decay, controllable `zeta_N`).
2. **"decelerates at exactly `c_rr * g`" only holds in the point-mass-
   wheel limit** (`I_wheel -> 0`). For a wheel with non-negligible
   rotational inertia, the no-slip rolling-resistance deceleration is
   `c_rr * g * m * r^2 / (I + m * r^2)` -- the exact reduction of
   `wheels_ground.tex`'s moment balance, which is *structurally* the same
   `M / M_eq`-style scaling `simplified.model`'s own rolling term uses
   (task.md T6's own phrasing: "reproduces the Simplified tier's rolling
   term"). `test_rolling_resistance_*` below checks both: the literal
   `c_rr * g` claim in the `I -> 0` limit, and the general closed form
   for a realistic wheel inertia.

`R_z >= 0` (architecture.md S5.5's shared sign convention: a negative
ground-normal reaction is a solver/contact-model bug, not a result) is
asserted in *every* test below, not only the jump test, per the ticket's
own explicit instruction.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.signal import argrelextrema

from bikephysics.core.road import ROAD_PRESET_DIR, ConstantGrade, Flat, Road, StepJump, load_road
from bikephysics.planar.body import RigidBody
from bikephysics.planar.contact.geometry import ContactState, contact_geometry
from bikephysics.planar.contact.regularized import RegularizedContact
from bikephysics.planar.forces.contact import ContactForce
from bikephysics.planar.integrate import PlanarRunResult, run
from bikephysics.planar.system import MultibodySystem

G = 9.81

# Regularization parameters that worked for these tests (also recorded in
# architecture-notes.md for T7/T10, architecture.md S12.2's provenance
# requirement).
DELTA_TARGET = 0.001
ZETA_N = 0.3
V_EPS = 0.001
V_STRIBECK = 0.01


def _single_wheel_system(
    *,
    mass: float,
    inertia: float,
    radius: float,
    c_rr: float,
    mu_s: float,
    mu_k: float,
    road: Road,
    delta_target: float = DELTA_TARGET,
    zeta_N: float = ZETA_N,
    v_eps: float = V_EPS,
    v_stribeck: float = V_STRIBECK,
) -> tuple[MultibodySystem, RegularizedContact]:
    system = MultibodySystem()
    system.add_body(RigidBody("w", mass=mass, inertia=inertia))
    system.add_gravity(g=G)
    model = RegularizedContact(
        name="rear_contact",
        body="w",
        radius=radius,
        c_rr=c_rr,
        mu_s=mu_s,
        mu_k=mu_k,
        M_total=mass,
        m_eff=mass,
        g=G,
        delta_target=delta_target,
        zeta_N=zeta_N,
        v_eps=v_eps,
        v_stribeck=v_stribeck,
    )
    system.add_force(ContactForce(name="contact_w", model=model, road=road))
    return system, model


def _assert_no_negative_normal_reaction(
    system: MultibodySystem, model: RegularizedContact, road: Road, result: PlanarRunResult
) -> None:
    """architecture.md S5.5: `R_z >= 0` is a hard invariant, checked at
    every reported sample, in every test in this file."""
    layout = system.layout
    for i in range(len(result.t)):
        q, u = result.q[i], result.u[i]
        wrenches = model.wrenches(result.t[i], q, u, layout, road, None, {})
        R_z = float(np.dot(wrenches[0].force_world, model.state().z_hat_0))
        assert R_z >= -1e-9, f"R_z={R_z} < 0 at t={result.t[i]}"


# ---------------------------------------------------------------------------
# 1. Drop and settle: delta_static == delta_target, damped-oscillation decay,
#    steady-state R_z == weight.
# ---------------------------------------------------------------------------


def test_dropped_wheel_settles_to_delta_target_with_damped_decay() -> None:
    mass, inertia, radius = 2.0, 0.05, 0.33
    system, model = _single_wheel_system(
        mass=mass, inertia=inertia, radius=radius, c_rr=0.0, mu_s=0.8, mu_k=0.7, road=Flat()
    )

    # Start exactly at first touch (g_N = 0), zero velocity: continuous
    # contact throughout (no bounce/separation), the regime the damping
    # derivation in regularized.py's module docstring is linearized about.
    q0 = np.array([0.0, radius, 0.0])
    u0 = np.zeros(3)
    y0 = np.concatenate([q0, u0])
    t_end = 0.6
    t_eval = np.linspace(0.0, t_end, 3000)
    result = run(
        system, y0, (0.0, t_end), method="Radau", max_drift=1e9, raise_on_drift=False,
        rtol=1e-9, atol=1e-12, t_eval=t_eval,
    )
    _assert_no_negative_normal_reaction(system, model, Flat(), result)

    z = result.q[:, 1]
    z_expected = radius - DELTA_TARGET

    # Settled (last quarter of the run) within 1% of delta_target.
    assert math.isclose(float(z[-1]), z_expected, abs_tol=0.01 * DELTA_TARGET)

    # Damped-oscillation decay: successive peak amplitudes strictly decrease.
    peaks = argrelextrema(z, np.greater)[0]
    assert len(peaks) >= 2, "expected at least two oscillation peaks before settling"
    amplitudes = np.abs(z[peaks] - z_expected)
    assert np.all(np.diff(amplitudes) < 0), f"peak amplitudes did not decay: {amplitudes}"

    # Steady-state R_z == weight.
    layout = system.layout
    q_final, u_final = result.q[-1], result.u[-1]
    wrenches = model.wrenches(t_end, q_final, u_final, layout, Flat(), None, {})
    R_z_final = float(np.dot(wrenches[0].force_world, model.state().z_hat_0))
    assert math.isclose(R_z_final, mass * G, rel_tol=0.02)


# ---------------------------------------------------------------------------
# 2. Rolling resistance deceleration: c_rr*g in the point-mass limit;
#    c_rr*g*m*r^2/(I+m*r^2) in general (the Simplified-tier-reproducing
#    closed form).
# ---------------------------------------------------------------------------


def _rolling_deceleration(mass: float, inertia: float, radius: float, c_rr: float) -> float:
    """Closed-form no-slip rolling-resistance deceleration for a standalone
    wheel (derived from `wheels_ground.tex`'s moment balance; see this
    file's module docstring point 2)."""
    return c_rr * G * mass * radius**2 / (inertia + mass * radius**2)


def _measure_deceleration(
    mass: float, inertia: float, radius: float, c_rr: float, v0: float, t_end: float = 1.5
) -> float:
    system, model = _single_wheel_system(
        mass=mass, inertia=inertia, radius=radius, c_rr=c_rr, mu_s=0.9, mu_k=0.8, road=Flat()
    )
    omega0 = v0 / radius  # this engine's no-slip convention (see geometry.py)
    q0 = np.array([0.0, radius - DELTA_TARGET, 0.0])
    u0 = np.array([v0, 0.0, omega0])
    y0 = np.concatenate([q0, u0])
    t_eval = np.linspace(0.0, t_end, 300)
    result = run(
        system, y0, (0.0, t_end), method="Radau", max_drift=1e9, raise_on_drift=False,
        rtol=1e-10, atol=1e-12, t_eval=t_eval,
    )
    _assert_no_negative_normal_reaction(system, model, Flat(), result)
    slope = np.polyfit(result.t, result.u[:, 0], 1)[0]
    return -slope


def test_rolling_resistance_decel_matches_c_rr_g_in_point_mass_limit() -> None:
    mass, radius, c_rr = 2.0, 0.33, 0.01
    inertia_negligible = mass * radius**2 * 1e-6  # point-mass-wheel limit
    decel = _measure_deceleration(mass, inertia_negligible, radius, c_rr, v0=5.0)
    assert math.isclose(decel, c_rr * G, rel_tol=1e-3)


def test_rolling_resistance_decel_matches_closed_form_for_realistic_inertia() -> None:
    mass, radius, c_rr = 2.0, 0.33, 0.01
    for inertia in (0.02, 0.05, 0.14):
        decel = _measure_deceleration(mass, inertia, radius, c_rr, v0=5.0)
        expected = _rolling_deceleration(mass, inertia, radius, c_rr)
        assert math.isclose(decel, expected, rel_tol=1e-3), (inertia, decel, expected)
        # Also structurally distinct from the naive c_rr*g once I is not
        # negligible -- confirms the test is not vacuous.
        assert not math.isclose(decel, c_rr * G, rel_tol=1e-2)


# ---------------------------------------------------------------------------
# 3. Grade: below the rolling-wheel traction threshold -> small bounded
#    creep; above it -> substantial slip. The threshold for a *rolling*
#    wheel is tan(theta_crit) = mu_s*(1 + m*r^2/I) (steeper than the naive
#    sliding-block atan(mu_s) -- a rolling wheel needs much less friction
#    than a sliding block to avoid slipping, since only a fraction of the
#    gravity component has to react through the contact tangentially; the
#    rest accelerates the wheel's own rotation). Derived from the same
#    moment-balance reduction as `_rolling_deceleration` above, generalized
#    to an inclined, no-rolling-resistance grade. Using I = m*r^2 (a
#    thin-rim/hoop wheel) gives the clean threshold tan(theta_crit) =
#    2*mu_s.
# ---------------------------------------------------------------------------


def _grade_test(
    theta: float,
    mass: float,
    inertia: float,
    radius: float,
    c_rr: float,
    mu_s: float,
    mu_k: float,
    t_end: float = 2.0,
) -> tuple[PlanarRunResult, ContactState]:
    road = ConstantGrade(theta=theta)
    system, model = _single_wheel_system(
        mass=mass, inertia=inertia, radius=radius, c_rr=c_rr, mu_s=mu_s, mu_k=mu_k, road=road
    )
    x0 = 0.0
    z0 = road.z(x0) + radius / math.cos(theta)
    q0 = np.array([x0, z0, theta])
    u0 = np.zeros(3)
    y0 = np.concatenate([q0, u0])
    t_eval = np.linspace(0.0, t_end, 400)
    result = run(
        system, y0, (0.0, t_end), method="Radau", max_drift=1e9, raise_on_drift=False,
        rtol=1e-10, atol=1e-12, t_eval=t_eval,
    )
    _assert_no_negative_normal_reaction(system, model, road, result)
    layout = system.layout
    q_final, u_final = result.q[-1], result.u[-1]
    geom = contact_geometry(
        q_final, u_final, layout, body="w", radius=radius, c_rr=c_rr, road=road, v_eps=V_EPS
    )
    return result, geom


def test_shallow_grade_rolls_with_bounded_creep() -> None:
    mass, radius, inertia, mu_s = 2.0, 0.33, 2.0 * 0.33**2, 0.5
    theta_crit = math.atan(2.0 * mu_s)  # I = m*r^2 -> factor of 2
    result, geom = _grade_test(
        theta_crit * 0.5, mass, inertia, radius, c_rr=0.005, mu_s=mu_s, mu_k=mu_s * 0.9
    )
    v_final = result.u[-1, 0]
    creep_ratio = abs(geom.v_slip) / max(abs(v_final), 1e-9)
    assert creep_ratio < 0.01, f"creep ratio {creep_ratio} exceeds the stated 1% bound"


def test_steep_grade_slides() -> None:
    mass, radius, inertia, mu_s = 2.0, 0.33, 2.0 * 0.33**2, 0.5
    theta_crit = math.atan(2.0 * mu_s)
    result, geom = _grade_test(
        theta_crit * 1.3, mass, inertia, radius, c_rr=0.005, mu_s=mu_s, mu_k=mu_s * 0.9
    )
    v_final = result.u[-1, 0]
    creep_ratio = abs(geom.v_slip) / max(abs(v_final), 1e-9)
    assert creep_ratio > 0.5, f"expected substantial slip above threshold, got {creep_ratio}"


# ---------------------------------------------------------------------------
# 4. Skid: brake-locked wheel (spinning against zero/low forward speed)
#    gives |R_x| == mu_k * R_z exactly (fully into the kinetic regime,
#    v_slip >> v_stribeck).
# ---------------------------------------------------------------------------


def test_locked_wheel_skid_matches_kinetic_friction_bound() -> None:
    mass, inertia, radius = 2.0, 0.05, 0.33
    mu_s, mu_k = 0.8, 0.6
    system, model = _single_wheel_system(
        mass=mass, inertia=inertia, radius=radius, c_rr=0.005, mu_s=mu_s, mu_k=mu_k, road=Flat()
    )
    # Spinning wheel, zero forward speed (a locked-brake burnout): large
    # |v_slip|, deep in the kinetic (mu_k) regime.
    q0 = np.array([0.0, radius - DELTA_TARGET, 0.0])
    u0 = np.array([0.0, 0.0, -20.0])
    y0 = np.concatenate([q0, u0])
    t_eval = np.linspace(0.0, 0.02, 50)
    result = run(
        system, y0, (0.0, 0.02), method="Radau", max_drift=1e9, raise_on_drift=False,
        rtol=1e-10, atol=1e-13, t_eval=t_eval,
    )
    _assert_no_negative_normal_reaction(system, model, Flat(), result)

    layout = system.layout
    q_final, u_final = result.q[-1], result.u[-1]
    wrenches = model.wrenches(result.t[-1], q_final, u_final, layout, Flat(), None, {})
    state = model.state()
    assert abs(state.v_slip) > 10 * V_STRIBECK  # deep in the kinetic regime
    R_x = float(np.dot(wrenches[0].force_world, state.x_hat_0))
    R_z = float(np.dot(wrenches[0].force_world, state.z_hat_0))
    assert math.isclose(abs(R_x), mu_k * R_z, rel_tol=1e-3)


# ---------------------------------------------------------------------------
# 5. step_jump: zero contact force during flight; R_z >= 0 always.
# ---------------------------------------------------------------------------


def test_step_jump_zero_force_during_flight_and_nonnegative_normal_reaction() -> None:
    mass, inertia, radius = 0.3, 0.01, 0.1  # small wheel: keeps the
    # foot-of-normal construction well inside core.road's own
    # well-posedness bound (curvature_radius >= 5*radius) for this road's
    # sharp ramp -- a 0.33 m wheel violates that bound on this specific
    # preset's ramp_width and the integrator cannot resolve the resulting
    # near-singular contact geometry (not a T6 defect: core.road's own
    # warning already flags exactly this situation).
    c_rr, mu_s, mu_k = 0.005, 0.8, 0.7
    road = load_road(ROAD_PRESET_DIR / "step_jump.toml")
    assert isinstance(road, StepJump)
    system, model = _single_wheel_system(
        mass=mass, inertia=inertia, radius=radius, c_rr=c_rr, mu_s=mu_s, mu_k=mu_k,
        road=road, delta_target=0.0005,
    )
    v0 = 8.0
    omega0 = v0 / radius
    x0 = road.x_jump - 1.0
    z0 = road.z(x0) + radius - 0.0005
    q0 = np.array([x0, z0, 0.0])
    u0 = np.array([v0, 0.0, omega0])
    y0 = np.concatenate([q0, u0])
    t_end = 0.3
    t_eval = np.linspace(0.0, t_end, 1000)
    result = run(
        system, y0, (0.0, t_end), method="Radau", max_drift=1e9, raise_on_drift=False,
        rtol=1e-9, atol=1e-12, t_eval=t_eval,
    )

    layout = system.layout
    R_z_history = np.empty(len(result.t))
    g_N_history = np.empty(len(result.t))
    for i in range(len(result.t)):
        q, u = result.q[i], result.u[i]
        wrenches = model.wrenches(result.t[i], q, u, layout, road, None, {})
        state = model.state()
        R_z_history[i] = float(np.dot(wrenches[0].force_world, state.z_hat_0))
        g_N_history[i] = state.g_N

    assert np.all(R_z_history >= -1e-9), "R_z >= 0 invariant violated"

    airborne = g_N_history > 2.0 * 0.0005  # clearly separated, not mid-touchdown
    assert airborne.any(), "expected the wheel to leave the ground on this jump"
    assert np.allclose(R_z_history[airborne], 0.0, atol=1e-9), (
        "contact force must be exactly zero while airborne"
    )


# ---------------------------------------------------------------------------
# 6. Cumulative creep distance and friction-dissipation energy budget,
#    reported and bounded (architecture.md S7.6.2's "honest cost" section;
#    `validation/energy.py`'s own job, exercised here for one wheel).
# ---------------------------------------------------------------------------


def test_creep_distance_and_friction_dissipation_are_bounded() -> None:
    mass, inertia, radius, c_rr = 2.0, 0.05, 0.33, 0.005
    mu_s, mu_k = 0.8, 0.7
    system, model = _single_wheel_system(
        mass=mass, inertia=inertia, radius=radius, c_rr=c_rr, mu_s=mu_s, mu_k=mu_k, road=Flat()
    )
    v0 = 6.0
    omega0 = v0 / radius
    q0 = np.array([0.0, radius - DELTA_TARGET, 0.0])
    u0 = np.array([v0, 0.0, omega0])
    y0 = np.concatenate([q0, u0])
    t_end = 1.0
    n = 2000
    t_eval = np.linspace(0.0, t_end, n)
    result = run(
        system, y0, (0.0, t_end), method="Radau", max_drift=1e9, raise_on_drift=False,
        rtol=1e-10, atol=1e-12, t_eval=t_eval,
    )
    _assert_no_negative_normal_reaction(system, model, Flat(), result)

    layout = system.layout
    v_slip_hist = np.empty(n)
    power_hist = np.empty(n)
    for i in range(n):
        q, u = result.q[i], result.u[i]
        wrenches = model.wrenches(result.t[i], q, u, layout, Flat(), None, {})
        state = model.state()
        v_slip_hist[i] = state.v_slip
        R_x = float(np.dot(wrenches[0].force_world, state.x_hat_0))
        power_hist[i] = -R_x * state.v_slip  # friction dissipation power, >= 0

    creep_distance = float(np.trapezoid(np.abs(v_slip_hist), result.t))
    dissipation_energy = float(np.trapezoid(power_hist, result.t))

    distance_travelled = float(result.q[-1, 0] - result.q[0, 0])
    assert dissipation_energy >= -1e-9  # dissipation, never negative work
    # Honest-cost bound (architecture.md S7.6.2): creep is a tiny fraction
    # of the distance actually travelled, not a double-digit-percent error.
    assert creep_distance < 0.01 * abs(distance_travelled)
    print(
        f"[T6 creep report] distance={distance_travelled:.3f} m, "
        f"creep_distance={creep_distance:.6f} m, "
        f"friction_dissipation={dissipation_energy:.6f} J"
    )
