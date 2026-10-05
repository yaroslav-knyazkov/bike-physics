"""Tests for the T7 lumped-drivetrain pieces: `planar/constraints/gear.py`
(IdealChain), `planar/forces/{pedal,brake,bearing}.py`, and
`planar/drivetrain.py` (architecture.md S7.3-S7.5, task.md T7 items 1-5).

Every sign-convention assertion here checks the engine's *actual*,
verified behavior (confirmed against `planar.solver.kkt_solve`'s own
arithmetic and `contact.geometry.contact_geometry`'s zero-slip condition,
both already-tested T5/T6 code) rather than architecture.md's literal
prose, per this ticket's documented corrections (see `gear.py`,
`forces/pedal.py`, `forces/brake.py`, and `planar/kinematics.py`'s module
docstrings for the full derivations).
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from bikephysics.core.brakes import ConstantBrake
from bikephysics.core.mathx import numpy_backend
from bikephysics.core.params import BIKE_PRESET_DIR, BikeSpec, bike_spec_from_dict, read_toml
from bikephysics.core.pedals import ZeroPedal
from bikephysics.core.road import ConstantGrade, Flat
from bikephysics.planar.assembly import build_bike, build_trajectory
from bikephysics.planar.body import RigidBody
from bikephysics.planar.constraints.gear import IdealChain
from bikephysics.planar.drivetrain import build_drivetrain, simulate_with_freewheel
from bikephysics.planar.forces.bearing import BearingFriction
from bikephysics.planar.forces.brake import BrakeTorque
from bikephysics.planar.forces.pedal import PedalForce
from bikephysics.planar.kinematics import solve_initial_conditions
from bikephysics.planar.state import SystemLayout

G = 9.81


def _spec() -> BikeSpec:
    return bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))


class _FixedPedal:
    """A fixed-force `PedalInput` stub (not `ConstantCrankTorque`'s even
    split), so `P_D != P_F` exercises the full `(P_D + P_F)` combination
    in the tests below."""

    def __init__(self, p_d: float, p_f: float) -> None:
        self.p_d = p_d
        self.p_f = p_f

    def forces(self, t: float, theta_3b: float, omega_3b: float) -> tuple[float, float]:
        return self.p_d, self.p_f

    def full(self, t: float, theta_3b: float, omega_3b: float) -> None:
        return None


# ---------------------------------------------------------------------------
# 1. IdealChain -- the two S7.5.2 sanity properties.
# ---------------------------------------------------------------------------


def test_ideal_chain_zero_generalized_force_under_rigid_rotation() -> None:
    """architecture.md S7.5.2: under a rigid rotation of the whole bike
    (`dtheta_1 = dtheta_2a = dtheta_3b`), `dc = 0` -- the chain does no
    work and exerts no generalized force."""
    chain = IdealChain(name="chain", r_chainring=0.075, r_cog=0.03)
    layout = SystemLayout(("1", "2a", "3b"))
    q = np.zeros(layout.nq)
    J = chain.jacobian(q, layout, numpy_backend)

    rigid_rotation = np.zeros(layout.nq)
    rigid_rotation[layout.idx_theta("1")] = 1.0
    rigid_rotation[layout.idx_theta("2a")] = 1.0
    rigid_rotation[layout.idx_theta("3b")] = 1.0

    assert J @ rigid_rotation == pytest.approx(0.0, abs=1e-12)


def test_ideal_chain_drive_resist_sign() -> None:
    """The chain resists crank spin and drives wheel spin: using the
    engine's *actual* `M @ a = h + J^T @ lambda` convention (verified in
    `gear.py`'s module docstring directly from `solver.py::kkt_solve`'s
    arithmetic), a positive tension `T` must contribute `-r_chainring*T`
    to the crank's own `theta_3b` generalized force and `+r_cog*T` to the
    wheel's `theta_2a`."""
    r_chainring, r_cog = 0.075, 0.03
    chain = IdealChain(name="chain", r_chainring=r_chainring, r_cog=r_cog)
    layout = SystemLayout(("1", "2a", "3b"))
    q = np.zeros(layout.nq)
    J = chain.jacobian(q, layout, numpy_backend)

    tension = 1.0
    generalized_force = J.T * tension  # M @ a = h + J^T @ lambda (engine's actual convention)

    assert generalized_force[layout.idx_theta("3b")] == pytest.approx(-r_chainring)
    assert generalized_force[layout.idx_theta("2a")] == pytest.approx(+r_cog)


def test_ideal_chain_is_unilateral_with_nonnegative_bounds() -> None:
    chain = IdealChain(name="chain", r_chainring=0.075, r_cog=0.03)
    assert chain.kind == "unilateral"
    assert chain.multiplier_bounds() == (0.0, math.inf)
    assert chain.dim == 1


def test_ideal_chain_gear_ratio() -> None:
    chain = IdealChain(name="chain", r_chainring=0.075, r_cog=0.03)
    assert chain.gear_ratio == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# 2. PedalForce -- the action/reaction pair, eq. (7.9)'s corrected sign.
# ---------------------------------------------------------------------------


def test_pedal_force_crank_torque_matches_corrected_eq_7_9() -> None:
    """`tau_pedal = +r_1,pedal*(P_D+P_F)` (the corrected sign -- see
    `forces/pedal.py`'s module docstring); checked at `theta_3b = 0`
    directly against the generalized force the element adds to `h`."""
    r_1_pedal = 0.1725
    p_d, p_f = 120.0, 90.0

    pedal = PedalForce(name="pedal", pedal_input=_FixedPedal(p_d, p_f), r_1_pedal=r_1_pedal)
    layout = SystemLayout(("1", "3b"))
    q = np.zeros(layout.nq)
    u = np.zeros(layout.nq)
    h = np.zeros(layout.nq)
    pedal.accumulate(0.0, q, u, layout, h, numpy_backend)

    expected_tau = r_1_pedal * (p_d + p_f)
    assert h[layout.idx_theta("3b")] == pytest.approx(expected_tau, rel=1e-9)


def test_pedal_force_both_halves_applied_as_action_reaction() -> None:
    """architecture.md S7.3: `+P` on `3b`, `-P` on `1`, at the same world
    points -- the net force (translational) each pedal point contributes
    must cancel between the two bodies (a pure internal action/reaction
    pair contributes zero net external force to the 2-body system)."""
    r_1_pedal = 0.1725

    pedal = PedalForce(
        name="pedal", pedal_input=_FixedPedal(100.0, 80.0), r_1_pedal=r_1_pedal
    )
    layout = SystemLayout(("1", "3b"))
    q = np.array([0.0, 0.0, 0.0, 0.3, 0.2, 0.7])  # theta_3b = 0.7, arbitrary
    u = np.zeros(layout.nq)
    h = np.zeros(layout.nq)
    pedal.accumulate(0.0, q, u, layout, h, numpy_backend)

    fx_total = h[layout.idx_x("1")] + h[layout.idx_x("3b")]
    fz_total = h[layout.idx_z("1")] + h[layout.idx_z("3b")]
    assert fx_total == pytest.approx(0.0, abs=1e-9)
    assert fz_total == pytest.approx(0.0, abs=1e-9)


def test_pedal_force_diagnostics_power_matches_tau_times_relative_omega() -> None:
    """`PedalForce.diagnostics`'s mechanical power equals `tau_pedal *
    (omega_3b - omega_1)` when only the crank (not the frame) is
    spinning -- the work rate of a pure internal torque pair."""
    r_1_pedal = 0.1725
    p_d, p_f = 100.0, 100.0

    pedal = PedalForce(name="pedal", pedal_input=_FixedPedal(p_d, p_f), r_1_pedal=r_1_pedal)
    layout = SystemLayout(("1", "3b"))
    q = np.zeros(layout.nq)
    u = np.zeros(layout.nq)
    omega_3b = 2.0
    u[layout.idx_theta("3b")] = omega_3b
    diag = pedal.diagnostics(0.0, q, u, layout)

    tau_pedal = r_1_pedal * (p_d + p_f)
    assert diag["power"] == pytest.approx(tau_pedal * omega_3b, rel=1e-6)


# ---------------------------------------------------------------------------
# 3. BrakeTorque -- resists the wheel's own (corrected-sign) spin.
# ---------------------------------------------------------------------------


def test_brake_torque_resists_forward_spin() -> None:
    """For forward motion (`sigma = +1`), the brake moment on the wheel
    must be *negative* (it resists the wheel's own positive spin under
    the corrected rolling convention -- see `forces/brake.py`'s module
    docstring)."""
    brake = BrakeTorque(
        name="brake_rear", wheel_body="2a", which="rear", r_bk=0.31, radius=0.335,
        road=Flat(), brake_input=ConstantBrake(B_2a=100.0, B_2b=0.0),
    )
    layout = SystemLayout(("1", "2a"))
    q = np.array([0.0, 0.0, 0.0, 0.0, 0.335, 0.0])
    u = np.zeros(layout.nq)
    u[layout.idx_x("2a")] = 5.0  # moving forward (+x)
    h = np.zeros(layout.nq)
    brake.accumulate(0.0, q, u, layout, h, numpy_backend)

    assert h[layout.idx_theta("2a")] == pytest.approx(-0.31 * 100.0, rel=1e-6)
    # Equal-opposite reaction on the frame.
    assert h[layout.idx_theta("1")] == pytest.approx(+0.31 * 100.0, rel=1e-6)


# ---------------------------------------------------------------------------
# 4. BearingFriction -- one-step lag, mu_b = 0 contributes nothing.
# ---------------------------------------------------------------------------


def test_bearing_friction_default_mu_b_zero_contributes_nothing() -> None:
    bearing = BearingFriction(
        name="bearing_rear", joint_name="joint_A", rotating_body="2a", mu_b=0.0, d_bearing=0.01
    )
    bearing.update_reaction(500.0)  # even with a large cached reaction...
    layout = SystemLayout(("1", "2a"))
    q = np.zeros(layout.nq)
    u = np.zeros(layout.nq)
    u[layout.idx_theta("2a")] = 10.0
    h = np.zeros(layout.nq)
    bearing.accumulate(0.0, q, u, layout, h, numpy_backend)
    assert h[layout.idx_theta("2a")] == pytest.approx(0.0)  # ...mu_b=0 gives zero torque.


def test_bearing_friction_formula_with_nonzero_mu_b() -> None:
    mu_b, d_bearing, r_mag = 0.0015, 0.01, 300.0
    bearing = BearingFriction(
        name="bearing_rear", joint_name="joint_A", rotating_body="2a",
        mu_b=mu_b, d_bearing=d_bearing, omega_eps=0.01,
    )
    bearing.update_reaction(r_mag)
    layout = SystemLayout(("1", "2a"))
    q = np.zeros(layout.nq)
    u = np.zeros(layout.nq)
    omega_rel = 5.0  # >> omega_eps, so sign_smooth ~= 1
    u[layout.idx_theta("2a")] = omega_rel
    h = np.zeros(layout.nq)
    bearing.accumulate(0.0, q, u, layout, h, numpy_backend)

    expected = -0.5 * mu_b * d_bearing * r_mag * math.tanh(omega_rel / 0.01)
    assert h[layout.idx_theta("2a")] == pytest.approx(expected, rel=1e-6)
    assert h[layout.idx_theta("1")] == pytest.approx(-expected, rel=1e-6)


def test_bearing_friction_update_reaction_mutates_shared_cache() -> None:
    cache: dict[str, float] = {}
    bearing = BearingFriction(
        name="b", joint_name="joint_A", rotating_body="2a", mu_b=0.001, d_bearing=0.01,
        reaction_cache=cache,
    )
    bearing.update_reaction(42.0)
    assert cache["joint_A"] == 42.0


# ---------------------------------------------------------------------------
# 5. build_drivetrain -- the crankset sub-assembly.
# ---------------------------------------------------------------------------


def test_build_drivetrain_wires_body_joint_and_chain() -> None:
    spec = _spec()
    frame_body = RigidBody(
        name="1", mass=spec.frame.m_1, inertia=spec.frame.I_1,
        points={"C": (spec.frame.l_1_C - spec.frame.l_1_a, spec.frame.h_1_C - spec.frame.h_1_a)},
    )
    drivetrain = build_drivetrain(spec, frame_body)

    assert drivetrain.crank_body.name == "3b"
    assert drivetrain.crank_body.mass > 0.0  # RigidBody's own invariant
    assert drivetrain.crank_body.inertia == pytest.approx(spec.drivetrain.I_3b)
    assert drivetrain.crank_body.points["C"] == (0.0, 0.0)  # CoG at the BB point, S7.5.1 item 2
    assert drivetrain.crank_body.points["D"] == (spec.frame.r_1_pedal, 0.0)
    assert drivetrain.crank_body.points["F"] == (-spec.frame.r_1_pedal, 0.0)

    assert drivetrain.bb_joint.body_a == "1"
    assert drivetrain.bb_joint.body_b == "3b"
    assert drivetrain.bb_joint.point_b == (0.0, 0.0)

    assert drivetrain.chain.r_chainring == pytest.approx(spec.drivetrain.r_3b_cr)
    assert drivetrain.chain.r_cog == pytest.approx(spec.drivetrain.r_2a_cg)


# ---------------------------------------------------------------------------
# 6. Freewheel mode switching, in the full assembled bike.
# ---------------------------------------------------------------------------


def test_freewheel_disengages_while_coasting_downhill_without_pedaling() -> None:
    """architecture.md S7.5.4: coasting downhill without pedaling
    accelerates the bike, which (via the gear ratio) would need the
    crankset to accelerate too -- requiring *negative* tension, which is
    outside `(0, +inf)`, so the trial loop drops the chain row and `T`
    reads `0`."""
    spec = _spec()
    road = ConstantGrade(theta=math.atan(-0.06))
    bike = build_bike(spec, road, ZeroPedal(), ConstantBrake())
    ic = solve_initial_conditions(bike, road, x_rear_0=0.0, v0=4.0)
    y0 = np.concatenate([ic.q0, ic.u0])

    result, _modes = simulate_with_freewheel(
        bike.system, bike.drivetrain.chain, y0, (0.0, 3.0), n_output=60
    )
    traj = build_trajectory(bike, result)

    assert np.min(traj.T) == pytest.approx(0.0, abs=1e-6)
    assert np.mean(traj.T <= 1e-6) > 0.8  # mostly coasting (freewheel engaged)


def test_braking_transfers_load_forward() -> None:
    """architecture.md T7's own "Done when": a braking run shows forward
    load transfer -- the rear load decreases and the front load
    increases under hard front braking."""
    spec = _spec()
    road = Flat()
    bike = build_bike(spec, road, ZeroPedal(), ConstantBrake(B_2a=0.0, B_2b=150.0))
    ic = solve_initial_conditions(bike, road, x_rear_0=0.0, v0=8.0)
    y0 = np.concatenate([ic.q0, ic.u0])

    result, _modes = simulate_with_freewheel(
        bike.system, bike.drivetrain.chain, y0, (0.0, 1.0), n_output=20
    )
    traj = build_trajectory(bike, result)

    assert traj.R_2a_z[-1] < traj.R_2a_z[0]
    assert traj.R_2b_z[-1] > traj.R_2b_z[0]
    assert np.all(traj.R_2a_z >= -1e-6)  # architecture.md S5.5: R_z >= 0 is a hard invariant
    assert np.all(traj.R_2b_z >= -1e-6)
