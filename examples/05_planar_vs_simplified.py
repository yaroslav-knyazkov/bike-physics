"""examples/05_planar_vs_simplified.py -- the §7.5.6 cross-tier reduction,
demonstrated end to end (task.md T7 item 11).

Runs `validation.cross_tier.run_all` (the ticket's own completion gate)
against the `road_bike` preset and prints every one of the five checks'
numbers, plus a coasting run (chain goes slack downhill) and a braking
run (forward load transfer) to show the qualitative "Done when" behaviors
alongside the quantitative cross-tier agreement.

    uv run python examples/05_planar_vs_simplified.py
"""

from __future__ import annotations

import math

import numpy as np

from bikephysics.core.brakes import ConstantBrake
from bikephysics.core.params import BIKE_PRESET_DIR, bike_spec_from_dict, read_toml
from bikephysics.core.pedals import ZeroPedal
from bikephysics.core.road import ConstantGrade, Flat
from bikephysics.planar.assembly import build_bike, build_trajectory
from bikephysics.planar.drivetrain import simulate_with_freewheel
from bikephysics.planar.kinematics import solve_initial_conditions
from bikephysics.validation import cross_tier as ct


def cross_tier_demo() -> None:
    spec = bike_spec_from_dict(read_toml(BIKE_PRESET_DIR / "road_bike.toml"))
    report = ct.run_all(spec)

    print("=== architecture.md S7.5.6 cross-tier reduction ===")
    print("1. Steady-state speed (planar vs. simplified.analysis.steady_state_speed):")
    for case in report.steady_state.cases:
        print(
            f"   power={case.power:6.0f} W  grade={case.grade:+.3f}  "
            f"planar={case.planar_speed:7.4f} m/s  simplified={case.simplified_speed:7.4f} m/s  "
            f"rel_err={case.rel_error * 100.0:+.4f} %"
        )
    print(f"   -> OK: {report.steady_state.ok()}")

    print("2. Chain tension (planar IdealChain multiplier vs. simplified.model.chain_tension):")
    print(
        f"   max relative error = {report.chain_tension.max_abs_rel_error:.2e}  "
        f"-> OK: {report.chain_tension.ok()}"
    )

    print("3. Reflected-inertia identity (I_3b/(G^2*r_2a^2) == I_3b*r_2a_cg^2/(r_3b_cr^2*r_2a^2)):")
    print(
        f"   lhs={report.reflected_inertia.lhs:.10f}  rhs={report.reflected_inertia.rhs:.10f}  "
        f"-> OK: {report.reflected_inertia.ok()}"
    )

    print("4. Static normal-load split (planar vs. simplified.model.normal_loads at x_ddot=0):")
    print(
        f"   rear: planar={report.static_load.planar_rear:.3f} N  "
        f"simplified={report.static_load.simplified_rear:.3f} N"
    )
    print(
        f"   front: planar={report.static_load.planar_front:.3f} N  "
        f"simplified={report.static_load.simplified_front:.3f} N  "
        f"-> OK: {report.static_load.ok()}"
    )

    ws = report.wheelie_stoppie
    print("5. Wheelie/stoppie crossing (planar front/rear load fraction at simplified threshold):")
    print(
        f"   wheelie: x_ddot={ws.wheelie_accel_simplified:.3f} m/s^2, "
        f"front load fraction at threshold={ws.front_z_fraction_at_wheelie_threshold:.3f}"
    )
    print(
        f"   stoppie: x_ddot={ws.stoppie_accel_simplified:.3f} m/s^2, "
        f"rear load fraction at threshold={ws.rear_z_fraction_at_stoppie_threshold:.3f}"
    )
    print(f"   -> OK: {report.wheelie_stoppie.ok()}")

    print(f"\nALL FIVE CHECKS OK: {report.ok()}")

    # --- Qualitative behaviors: coasting (chain slack) and braking (forward load transfer) ---
    print("\n=== Coasting downhill without pedaling: chain should go slack ===")
    road_down = ConstantGrade(theta=math.atan(-0.06))
    bike_coast = build_bike(spec, road_down, ZeroPedal(), ConstantBrake())
    ic_coast = solve_initial_conditions(bike_coast, road_down, x_rear_0=0.0, v0=4.0)
    y0_coast = np.concatenate([ic_coast.q0, ic_coast.u0])
    result_coast, _ = simulate_with_freewheel(
        bike_coast.system, bike_coast.drivetrain.chain, y0_coast, (0.0, 4.0), n_output=80
    )
    traj_coast = build_trajectory(bike_coast, result_coast)
    frac_slack = float(np.mean(traj_coast.T <= 1e-6))
    print(f"   chain tension range: [{traj_coast.T.min():.4f}, {traj_coast.T.max():.4f}] N")
    print(f"   fraction of samples with T == 0 (slack): {frac_slack * 100.0:.1f} %")

    print("\n=== Hard front braking: load should transfer forward ===")
    road_flat = Flat()
    bike_brake = build_bike(spec, road_flat, ZeroPedal(), ConstantBrake(B_2a=0.0, B_2b=150.0))
    ic_brake = solve_initial_conditions(bike_brake, road_flat, x_rear_0=0.0, v0=8.0)
    y0_brake = np.concatenate([ic_brake.q0, ic_brake.u0])
    result_brake, _ = simulate_with_freewheel(
        bike_brake.system, bike_brake.drivetrain.chain, y0_brake, (0.0, 1.0), n_output=40
    )
    traj_brake = build_trajectory(bike_brake, result_brake)
    print(
        f"   rear load:  {traj_brake.R_2a_z[0]:.1f} N -> {traj_brake.R_2a_z[-1]:.1f} N "
        "(should decrease)"
    )
    print(
        f"   front load: {traj_brake.R_2b_z[0]:.1f} N -> {traj_brake.R_2b_z[-1]:.1f} N "
        "(should increase)"
    )


if __name__ == "__main__":
    cross_tier_demo()
