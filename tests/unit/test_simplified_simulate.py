"""Tests for bikephysics.simplified.simulate (architecture.md S6.3, task.md
T4; `latex/simplified/forward_motion.tex`)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from bikephysics.core.aero import ConstantCdA
from bikephysics.core.brakes import BrakeAtDistance
from bikephysics.core.pedals import ClippedSine, ConstantCrankTorque, ZeroPedal
from bikephysics.core.road import ConstantGrade
from bikephysics.simplified import analysis
from bikephysics.simplified import simulate as sim
from tests.unit.factories import make_minimal_bike_spec


def test_simulate_returns_a_valid_trajectory() -> None:
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=20.0, r_1_pedal=spec.frame.r_1_pedal)
    traj = sim.simulate(spec, pedal, t_span=(0.0, 20.0), n_output=100)
    traj.validate()  # raises on any length mismatch
    assert traj.spec is spec
    assert traj.modes is None
    assert np.all(traj.z == 0.0)
    assert np.all(traj.pitch == 0.0)
    assert np.all(traj.pitch_rate == 0.0)


def test_simulate_terminal_speed_matches_steady_state_speed() -> None:
    """Done-when (task.md T4): the simulated terminal speed matches
    `steady_state_speed` to < 1e-4 m/s."""
    spec = make_minimal_bike_spec()
    tau = 30.0
    pedal = ConstantCrankTorque(tau=tau, r_1_pedal=spec.frame.r_1_pedal)
    p_each = tau / (2.0 * spec.frame.r_1_pedal)
    pedal_force_equiv = (
        (spec.frame.r_1_pedal * spec.drivetrain.r_2a_cg)
        / (spec.drivetrain.r_3b_cr * spec.rear.r_2a)
        * (2.0 * p_each)
    )
    v_expected = analysis.steady_state_speed(spec, pedal_force_equiv, 0.0, input_kind="force")

    traj = sim.simulate(spec, pedal, t_span=(0.0, 400.0), n_output=4000)

    assert traj.meta["termination_reason"] == "t_end"
    assert abs(traj.v[-1] - v_expected) < 1e-4
    assert abs(traj.a[-1]) < 1e-6  # at steady state, acceleration ~ 0


def test_simulate_energy_audit_closes_to_tenth_of_a_percent() -> None:
    """Done-when (task.md T4): the energy audit closes to < 0.1% for a
    Simplified-tier run."""
    from bikephysics.validation import energy

    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=25.0, r_1_pedal=spec.frame.r_1_pedal)
    traj = sim.simulate(spec, pedal, t_span=(0.0, 60.0), n_output=2000)

    audit = energy.audit(traj)
    assert audit.closure_error_fraction < 1e-3  # < 0.1%


def test_simulate_standing_start_zero_pedal_force_does_not_stall() -> None:
    """Done-when (task.md T4): a standing start with zero initial pedal
    force integrates without stalling -- `sign_smooth` (`core.mathx`) fixes
    the historical hard-`sign(v)`-at-v=0 RK45 footgun (the deleted
    `bikephysics/simulation.py`'s `if v == 0.0: F_roll = 0.0`)."""
    spec = make_minimal_bike_spec()
    traj = sim.simulate(spec, ZeroPedal(), x0=0.0, v0=0.0, t_span=(0.0, 10.0), n_output=50)
    assert traj.meta["solver_success"] is True
    assert np.all(np.isfinite(traj.v))
    assert np.all(traj.v == pytest.approx(0.0, abs=1e-9))


def test_simulate_standing_start_with_driving_force_integrates_through_v_zero() -> None:
    """The same footgun, but actually exercising the sign-flip region: a
    constant driving torque from a dead stop must accelerate the bike
    smoothly through v=0 with a modest number of RHS evaluations (no
    adaptive-step collapse), and converge to the analytic equilibrium."""
    spec = make_minimal_bike_spec()
    tau = 40.0
    pedal = ConstantCrankTorque(tau=tau, r_1_pedal=spec.frame.r_1_pedal)
    traj = sim.simulate(spec, pedal, x0=0.0, v0=0.0, t_span=(0.0, 200.0), n_output=1000)

    assert traj.meta["solver_success"] is True
    assert traj.v[-1] > 1.0  # it did accelerate away from rest
    # A collapsed/stalled integrator would need orders of magnitude more
    # RHS evaluations to cover the same span; this is a generous ceiling.
    assert traj.meta["n_rhs_evals"] < 20_000


def test_simulate_x_target_terminal_event_stops_at_target() -> None:
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=30.0, r_1_pedal=spec.frame.r_1_pedal)
    x_target = 50.0
    traj = sim.simulate(
        spec, pedal, x0=0.0, v0=5.0, t_span=(0.0, 60.0), x_target=x_target, n_output=500
    )
    assert traj.meta["termination_reason"] == "x_target"
    # `simulate()` appends the exact (dense-output) event state as the
    # final sample, so this is solver-tolerance-precise, not grid-spacing.
    assert traj.x[-1] == pytest.approx(x_target, abs=1e-6)


def test_simulate_v_zero_terminal_event_coasts_to_a_stop() -> None:
    """Coasting uphill with no pedal/brake input: rolling resistance and
    braking are both `sign_smooth`-modulated and vanish at `v=0` (an
    asymptotic, never-quite-reached fixed point under that regularization
    alone), but gravity is not -- `-M*g*sin(theta)` keeps acting right
    through `v=0`, so an uphill coast genuinely decelerates past zero into
    rolling backward, giving the `v <= 0` terminal event something real to
    cross in finite time."""
    spec = make_minimal_bike_spec()
    road = ConstantGrade(theta=math.radians(8.0))
    traj = sim.simulate(
        spec, ZeroPedal(), road=road, x0=0.0, v0=5.0, t_span=(0.0, 60.0), n_output=300
    )
    assert traj.meta["termination_reason"] == "v_zero"
    assert traj.v[-1] == pytest.approx(0.0, abs=1e-6)


def test_simulate_margin_crossing_events_are_nonterminal_and_logged() -> None:
    """Sudden hard front braking while cruising crosses several limit
    margins into violation; the run must keep going (non-terminal) and the
    crossing times must land in `meta["limit_crossings"]`."""
    spec = make_minimal_bike_spec()
    brake = BrakeAtDistance(x_trigger=5.0, B_2a=0.0, B_2b=2000.0)
    traj = sim.simulate(
        spec, ZeroPedal(), brake_input=brake, x0=0.0, v0=12.0, t_span=(0.0, 5.0), n_output=1000
    )
    assert traj.meta["solver_success"] is True
    crossings = traj.meta["limit_crossings"]
    assert set(crossings) == {"traction_rear", "traction_front", "wheelie", "stoppie", "freewheel"}
    assert len(crossings["stoppie"]) >= 1
    assert len(traj.t) == len(traj.v)  # the run kept going past the crossing


def test_crank_angle_reconstruction_matches_the_boxed_algebraic_formula() -> None:
    """`theta_3b(t) = theta_3b(0) - (x(t)-x(0)) * r_2a_cg/(r_3b_cr*r_2a)`
    (architecture.md S6.3), checked indirectly via `omega_3b`: for a
    constant-torque pedal input (crank-angle independent), `omega_3b`
    returned in the trajectory must equal `-v * ratio` exactly."""
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=15.0, r_1_pedal=spec.frame.r_1_pedal)
    traj = sim.simulate(spec, pedal, t_span=(0.0, 30.0), n_output=200)
    ratio = spec.drivetrain.r_2a_cg / (spec.drivetrain.r_3b_cr * spec.rear.r_2a)
    expected_omega_3b = -traj.v * ratio
    np.testing.assert_allclose(traj.omega_3b, expected_omega_3b, rtol=1e-9)


def test_power_channels_balance_the_equivalent_kinetic_energy_rate_exactly() -> None:
    """`M_eq*a*v == power_pedal - power_brake - power_gravity - power_rr -
    power_aero` at every sample, exactly (to floating-point precision) --
    the instantaneous form of the energy identity `validation/energy.py`
    checks in integrated form."""
    spec = make_minimal_bike_spec()
    pedal = ClippedSine(P_0=250.0, theta_0=0.0, n=2.0)
    road = ConstantGrade(theta=0.03)
    traj = sim.simulate(
        spec, pedal, road=road, x0=0.0, v0=3.0, theta_3b0=math.pi / 4.0,
        t_span=(0.0, 20.0), n_output=200,
    )
    m_eq = spec.m_eq
    lhs = m_eq * traj.a * traj.v
    rhs = (
        traj.power_pedal - traj.power_brake - traj.power_gravity - traj.power_rr - traj.power_aero
    )
    np.testing.assert_allclose(lhs, rhs, rtol=1e-9, atol=1e-9)
    assert np.all(traj.power_ground == 0.0)


def test_simulate_raises_on_unequal_wheels() -> None:
    spec = make_minimal_bike_spec(
        front={"m_2b": 1.8, "I_2b": 0.14, "r_2b": 0.34, "c_2b_rr": 0.005, "r_2b_bk": 0.31}
    )
    with pytest.raises(ValueError, match="r_2a == front.r_2b"):
        sim.simulate(spec, ZeroPedal())


def test_simulate_default_road_is_flat_and_default_brake_is_zero() -> None:
    spec = make_minimal_bike_spec()
    traj = sim.simulate(spec, ZeroPedal(), v0=5.0, t_span=(0.0, 1.0), n_output=10)
    # On a flat road with no pedal/brake, the only resistive forces are
    # rolling resistance and aero drag -- acceleration must be <= 0.
    assert np.all(traj.a <= 1e-9)


def test_simulate_honors_explicit_aero_model() -> None:
    spec = make_minimal_bike_spec()
    aero_model = ConstantCdA(C_1_x_0=0.0, A_1=spec.aero.A_1, l_1=spec.aero.l_1)
    traj = sim.simulate(
        spec, ZeroPedal(), aero_model=aero_model, v0=5.0, t_span=(0.0, 1.0), n_output=5
    )
    assert np.all(traj.power_aero == 0.0)
