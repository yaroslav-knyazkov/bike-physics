# bike-physics

A small single-DOF longitudinal bike dynamics simulation. The physics is
derived in `main.tex` (equations of motion and kinematics for a bike+rider
system with wheel and drivetrain rotational inertia, rolling resistance,
gravity on an incline, and quadratic aerodynamic drag). The rider is
modeled as a prescribed propulsive force F(t) [N] applied at the contact
point (not a power source), which avoids any 1/v singularity and lets the
bike start cleanly from rest.

## Running it

```bash
pip install -r requirements.txt
python main.py
```

`main.py` is a playground, not a CLI: it defines a handful of small,
independently runnable example functions (`baseline_run`,
`steady_state_check`, `compare_rolling_resistance`,
`compare_force_profiles`) that print labeled numeric results to stdout and
save PNG plots to the project root. Read them, copy them, and adapt them
to explore your own "what if I change X" questions -- that's the intended
workflow. For example, deriving a modified parameter set with
`dataclasses.replace(BikeParameters(), c_rr=0.010)` and re-running
`steady_state_velocity(...)` is the fastest way to test a "what if"
without touching the ODE integration at all.

## Package organization

- `bikephysics/parameters.py` -- `BikeParameters`, a frozen dataclass of
  the physical constants (mass, wheel/drivetrain inertia, rolling
  resistance, aerodynamics, incline, gravity), plus the computed `m_eq`
  (equivalent mass) property.
- `bikephysics/profiles.py` -- rider drive-force time profiles
  (`ConstantForce`, `RampForce`, `StepForce`, `SinusoidalForce`), each a
  simple `__call__(t) -> F` callable that can be dropped straight into the
  simulator.
- `bikephysics/simulation.py` -- `BikeSimulator` (integrates the ODE with
  `scipy.integrate.solve_ivp` and reconstructs acceleration and the wheel /
  drivetrain rotation angles) and `SimulationResult` (the output bundle:
  time series plus the params/force profile that produced them).
- `bikephysics/analysis.py` -- `steady_state_velocity`, the closed-form
  analytic steady-state speed for a constant drive force, useful as a
  sanity check against the simulated terminal speed and for quick
  parameter-sweep experiments without running a full simulation.
- `bikephysics/plotting.py` -- `BikePlotter`, producing the standard
  6-panel time-series figure (`plot`) and multi-run v(t) overlays
  (`compare`) for side-by-side comparisons.

All public names are re-exported from `bikephysics/__init__.py`, so
`main.py` and any other caller can do
`from bikephysics import BikeParameters, BikeSimulator, ...` without
reaching into submodules.
