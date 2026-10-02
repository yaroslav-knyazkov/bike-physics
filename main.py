"""
main.py -- Playground for experimenting with the bike-physics model.

Not a rigid CLI: a handful of independently runnable example functions
demonstrating typical experiments. Read them, copy them, adapt them to
answer your own "what if I change X" questions.

See main.tex for the physics derivation and bikephysics/ for the
implementation (parameters.py, profiles.py, simulation.py, analysis.py,
plotting.py).
"""

from dataclasses import replace

from bikephysics import (
    BikeParameters,
    BikePlotter,
    BikeSimulator,
    ConstantForce,
    RampForce,
    StepForce,
    steady_state_velocity,
)


def baseline_run() -> None:
    """Run and plot the default configuration."""
    params = BikeParameters()
    force = ConstantForce(40.0)

    sim = BikeSimulator(params=params, force_profile=force)
    result = sim.run()

    print(f"m_eq = {params.m_eq:.4f} kg (equivalent mass)")
    print(
        f"Simulated terminal speed (last sample): v(T_END) = {result.v[-1]:.4f} m/s "
        f"= {result.v[-1] * 3.6:.2f} km/h"
    )

    plotter = BikePlotter()
    plotter.plot(result, title="Baseline run (constant 40N)", save_path="bike_simulation.png")


def steady_state_check() -> None:
    """Print analytic steady-state velocity for a few force levels."""
    params = BikeParameters()

    print("\nAnalytic steady-state velocity vs. drive force (theta1=0):")
    for F in (25.0, 40.0, 55.0, 70.0):
        v = steady_state_velocity(params, F)
        print(f"  F = {F:5.1f} N -> v = {v:6.3f} m/s = {v * 3.6:6.2f} km/h")

    # Cross-check against a full simulation at the default 40N: the
    # simulated terminal speed should match the analytic prediction closely
    # once the transient has died out (T_END=120s is plenty of time here).
    sim = BikeSimulator(params=params, force_profile=ConstantForce(40.0))
    result = sim.run()
    analytic_v = steady_state_velocity(params, 40.0)
    print(
        f"\nSanity check @ F=40N: analytic v = {analytic_v:.4f} m/s, "
        f"simulated v(T_END) = {result.v[-1]:.4f} m/s "
        f"(diff = {abs(analytic_v - result.v[-1]):.2e} m/s)"
    )


def compare_rolling_resistance() -> None:
    """Example: how does steady-state velocity change if c_rr doubles?
    Uses dataclasses.replace() to derive a modified BikeParameters."""
    baseline_params = BikeParameters()
    higher_crr_params = replace(baseline_params, c_rr=baseline_params.c_rr * 2)

    F = 40.0
    v_baseline = steady_state_velocity(baseline_params, F)
    v_higher_crr = steady_state_velocity(higher_crr_params, F)
    pct_change = (v_higher_crr - v_baseline) / v_baseline * 100.0

    print(f"\nBaseline steady-state v = {v_baseline:.2f} m/s ({v_baseline * 3.6:.1f} km/h)")
    print(
        f"With c_rr={higher_crr_params.c_rr:.4f}: v = {v_higher_crr:.2f} m/s "
        f"({v_higher_crr * 3.6:.1f} km/h) [{pct_change:+.1f}%]"
    )


def compare_force_profiles() -> None:
    """Run baseline vs a ramp vs a step force profile, overlay v(t)."""
    params = BikeParameters()

    # Note: f_start is 5.0N here rather than 0.0N. Starting exactly at
    # F=0 while at rest (v=0) puts the very first integration step exactly
    # on the discontinuity in the rolling-resistance term (it is defined
    # as 0 at v=0 but jumps to +-c_rr*m*g*cos(theta1) for any v != 0, per
    # the model's v==0 special case) -- with the tight rtol/atol used by
    # BikeSimulator, the adaptive-step RK45 solver can stall trying to
    # resolve that instantaneous kink. A small nonzero f_start avoids the
    # issue without changing the physics; see RampForce's docstring.
    profiles = {
        "constant 40N": ConstantForce(40.0),
        "ramp 5->55N over 30s": RampForce(f_start=5.0, f_end=55.0, ramp_time=30.0),
        "step 25N->55N @ t=20s": StepForce(f_before=25.0, f_after=55.0, t_step=20.0),
    }

    results = []
    labels = []
    for label, profile in profiles.items():
        result = BikeSimulator(params=params, force_profile=profile).run()
        results.append(result)
        labels.append(label)
        print(f"\n{label}: v(T_END) = {result.v[-1]:.4f} m/s = {result.v[-1] * 3.6:.2f} km/h")

    plotter = BikePlotter()
    plotter.compare(results, labels, save_path="bike_force_profile_comparison.png")


if __name__ == "__main__":
    baseline_run()
    steady_state_check()
    compare_rolling_resistance()
    compare_force_profiles()
