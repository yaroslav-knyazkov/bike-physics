"""cli/simplified_cmds.py -- `bike simplified run|steady|sweep|limits|
sensitivity` (architecture.md S9's `bike simplified` block).

Registers its five commands directly onto `cli.app.simplified_app`
(imported for its side effect by `cli.app`, mirroring `cli/spec_cmds.py`'s
own wiring). `--config`/`--set` are redeclared locally on each command, for
the same reason `spec_cmds.py` redeclares them (architecture.md S9's own
usage strings put every option *after* the subcommand name, which only a
leaf-command-level option can satisfy) -- and so are `--out`/`--no-plot`/
`--json`, since (unlike `spec`'s read-only commands) every command here
*does* compute something and must write a run directory (architecture.md
S5.6) honoring those two flags (S9: "each writing a run dir and honouring
--json and --no-plot").

Every command writes `summary.json` via `core.runio`; `run` additionally
writes `result.npz` (the full `Trajectory`) and, unless `--no-plot`, a
`trajectory.png` via `simplified/plots.py`. `steady`/`sweep`/`limits`/
`sensitivity` are closed-form (`simplified/analysis.py`), so they have no
`Trajectory` to persist -- `summary.json`'s `metrics` block is their only
artifact besides the human-readable table printed to stdout (or the JSON
blob on `--json`).
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import typer

from bikephysics.cli.app import simplified_app
from bikephysics.cli.common import apply_overrides, resolve_config_path
from bikephysics.cli.spec_cmds import load_bike_spec
from bikephysics.core.aero import ConstantCdA
from bikephysics.core.atmosphere import atmosphere_from_env
from bikephysics.core.brakes import BrakeAtDistance, BrakeInput, ConstantBrake
from bikephysics.core.params import BIKE_PRESET_DIR, BikeSpec, bike_spec_from_dict, read_toml
from bikephysics.core.pedals import (
    ClippedSine,
    ConstantCrankTorque,
    CosSquared,
    PedalInput,
    VonMises,
    ZeroPedal,
)
from bikephysics.core.road import ROAD_PRESET_DIR, Flat, Road, load_road
from bikephysics.core.runio import build_run_summary, make_run_dir, write_summary, write_trajectory
from bikephysics.simplified import analysis
from bikephysics.simplified.plots import save_trajectory_figure
from bikephysics.simplified.simulate import simulate
from bikephysics.validation import energy

_ConfigOption = Annotated[
    str | None,
    typer.Option("--config", help="Preset name or path to a TOML bike spec."),
]
_SetOption = Annotated[
    list[str],
    typer.Option("--set", help="Dotted-path override, e.g. rear.c_2a_rr=0.008. Repeatable."),
]
_OutOption = Annotated[Path, typer.Option("--out", help="Run output directory.")]
_NoPlotOption = Annotated[
    bool, typer.Option("--no-plot", help="Skip writing figures for this run.")
]
_JsonOption = Annotated[
    bool, typer.Option("--json", help="Print a machine-readable summary to stdout.")
]

_PEDAL_TEMPLATES: tuple[str, ...] = (
    "zero",
    "constant-torque",
    "cos-squared",
    "clipped-sine",
    "von-mises",
)
_BRAKE_MODES: tuple[str, ...] = ("none", "constant", "at-distance")


def _load_road(road: str | None) -> Road:
    """`--road`: a preset name/path (`core.road.ROAD_PRESET_DIR`), or
    `core.road.Flat()` when omitted."""
    path = resolve_config_path(road, [ROAD_PRESET_DIR])
    return load_road(path) if path is not None else Flat()


def _build_pedal_input(
    name: str, spec: BikeSpec, pedal_force: float, pedal_phase: float
) -> PedalInput:
    """`--pedal`: maps a template name onto a `core.pedals.PedalInput`.
    `pedal_force` is each pedal's peak tangential force `P_0` [N]
    (`constant-torque` reinterprets it as a constant per-pedal force, via
    `tau = pedal_force * 2 * r_1_pedal`, so `ConstantCrankTorque.forces`
    gives back exactly `pedal_force` at each pedal)."""
    if name == "zero":
        return ZeroPedal()
    if name == "constant-torque":
        tau = pedal_force * 2.0 * spec.frame.r_1_pedal
        return ConstantCrankTorque(tau=tau, r_1_pedal=spec.frame.r_1_pedal)
    if name == "cos-squared":
        return CosSquared(P_0=pedal_force, phi=pedal_phase)
    if name == "clipped-sine":
        return ClippedSine(P_0=pedal_force, theta_0=pedal_phase, n=2.0)
    if name == "von-mises":
        return VonMises(P_0=pedal_force, theta_0=pedal_phase, kappa=2.0)
    raise typer.BadParameter(f"unknown --pedal {name!r}; expected one of {_PEDAL_TEMPLATES}")


def _build_brake_input(
    mode: str, brake_force: float, front_bias: float, x_trigger: float | None
) -> BrakeInput:
    """`--brake`: `brake_force` [N] is split `front_bias`/`1-front_bias`
    between `B_2b` (front) and `B_2a` (rear)."""
    b_2b = brake_force * front_bias
    b_2a = brake_force * (1.0 - front_bias)
    if mode == "none":
        return ConstantBrake()
    if mode == "constant":
        return ConstantBrake(B_2a=b_2a, B_2b=b_2b)
    if mode == "at-distance":
        if x_trigger is None:
            raise typer.BadParameter("--brake at-distance requires --brake-x-trigger")
        return BrakeAtDistance(x_trigger=x_trigger, B_2a=b_2a, B_2b=b_2b)
    raise typer.BadParameter(f"unknown --brake {mode!r}; expected one of {_BRAKE_MODES}")


def _resolve_base_data(config: str | None, overrides: list[str]) -> dict[str, object]:
    """The `--config`/`--set` resolution `load_bike_spec` does, stopping
    short of validation -- `sweep` needs the raw dict so it can layer one
    more override per swept value before validating."""
    path = resolve_config_path(config, [BIKE_PRESET_DIR])
    data: dict[str, object] = read_toml(path) if path is not None else {}
    return apply_overrides(data, overrides)


def _write_analytic_run(
    out: Path,
    slug: str,
    command: str,
    metrics: dict[str, float],
    *,
    solver: dict[str, Any] | None = None,
) -> Path:
    """Write the run dir for a closed-form command (`steady`/`sweep`/
    `limits`/`sensitivity`): `summary.json` only, no `Trajectory`."""
    run_dir = make_run_dir(out, slug)
    summary = build_run_summary(
        run_id=run_dir.name, command=command, wall_time_s=0.0, solver=solver, metrics=metrics
    )
    write_summary(run_dir, summary)
    return run_dir


def _echo_table(rows: list[tuple[str, str]]) -> None:
    width = max((len(label) for label, _ in rows), default=0)
    for label, value in rows:
        typer.echo(f"{label:<{width}}  = {value}")


@simplified_app.command("run")
def run_cmd(
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    road: Annotated[str | None, typer.Option("--road", help="Road preset name or path.")] = None,
    pedal: Annotated[
        str, typer.Option("--pedal", help=f"Pedal template, one of {_PEDAL_TEMPLATES}.")
    ] = "clipped-sine",
    pedal_force: Annotated[
        float, typer.Option("--pedal-force", help="Peak per-pedal tangential force P_0 [N].")
    ] = 300.0,
    pedal_phase: Annotated[
        float, typer.Option("--pedal-phase", help="Template phase offset theta_0 [rad].")
    ] = 0.0,
    brake: Annotated[str, typer.Option("--brake", help=f"One of {_BRAKE_MODES}.")] = "none",
    brake_force: Annotated[
        float, typer.Option("--brake-force", help="Total brake force [N].")
    ] = 0.0,
    brake_front_bias: Annotated[
        float,
        typer.Option("--brake-front-bias", help="Fraction of --brake-force on the front wheel."),
    ] = 0.5,
    brake_x_trigger: Annotated[
        float | None,
        typer.Option("--brake-x-trigger", help="x [m] at which --brake at-distance engages."),
    ] = None,
    x0: Annotated[float, typer.Option("--x0")] = 0.0,
    v0: Annotated[float, typer.Option("--v0")] = 0.0,
    theta3b0: Annotated[
        float,
        typer.Option(
            "--theta3b0",
            help=(
                "Initial crank angle [rad]. Default pi/4, not 0: the default "
                "--pedal clipped-sine's dead zone sits at theta=0, which would "
                "give a standing start zero net force forever."
            ),
        ),
    ] = math.pi / 4.0,
    t_end: Annotated[float, typer.Option("--t-end", help="Simulation end time [s].")] = 120.0,
    x_target: Annotated[
        float | None, typer.Option("--x-target", help="Terminal event: stop once x >= x_target.")
    ] = None,
    n_output: Annotated[int, typer.Option("--n-output")] = 2000,
    method: Annotated[str, typer.Option("--method", help='"RK45" or "Radau".')] = "RK45",
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Simulate a Simplified-tier run and write its run directory
    (architecture.md S9, S6.3)."""
    spec = load_bike_spec(config, list(set_))
    road_obj = _load_road(road)
    pedal_input = _build_pedal_input(pedal, spec, pedal_force, pedal_phase)
    brake_input = _build_brake_input(brake, brake_force, brake_front_bias, brake_x_trigger)

    t_wall_start = time.monotonic()
    trajectory = simulate(
        spec,
        pedal_input,
        road=road_obj,
        brake_input=brake_input,
        x0=x0,
        v0=v0,
        theta_3b0=theta3b0,
        t_span=(0.0, t_end),
        x_target=x_target,
        n_output=n_output,
        method=method,  # type: ignore[arg-type]
    )
    wall_time_s = time.monotonic() - t_wall_start
    audit = energy.audit(trajectory)

    run_dir = make_run_dir(out, "simplified-run")
    write_trajectory(run_dir, trajectory)
    if not no_plot:
        save_trajectory_figure(trajectory, run_dir / "trajectory.png", title=run_dir.name)

    metrics = {
        "final_x": float(trajectory.x[-1]),
        "final_v": float(trajectory.v[-1]),
        "final_a": float(trajectory.a[-1]),
        "n_rhs_evals": float(trajectory.meta["n_rhs_evals"]),
        "energy_closure_error_fraction": audit.closure_error_fraction,
    }
    summary = build_run_summary(
        run_id=run_dir.name,
        command="bike simplified run",
        wall_time_s=wall_time_s,
        solver={
            "method": method,
            "termination_reason": trajectory.meta["termination_reason"],
            "solver_message": trajectory.meta["solver_message"],
        },
        tolerances={
            "rtol": trajectory.meta["rtol"],
            "atol": trajectory.meta["atol"],
            "v_eps": trajectory.meta["v_eps"],
        },
        metrics=metrics,
    )
    write_summary(run_dir, summary)

    if json_output:
        typer.echo(json.dumps({"run_dir": str(run_dir), **metrics}))
    else:
        typer.echo(f"wrote {run_dir}")
        _echo_table(
            [
                ("final x [m]", f"{trajectory.x[-1]:.3f}"),
                ("final v [m/s]", f"{trajectory.v[-1]:.4f}"),
                ("final a [m/s^2]", f"{trajectory.a[-1]:.4e}"),
                ("termination", str(trajectory.meta["termination_reason"])),
                ("energy closure error", f"{audit.closure_error_fraction * 100.0:.4f} %"),
            ]
        )


@simplified_app.command("steady")
def steady_cmd(
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    power: Annotated[float, typer.Option("--power", help="Pedal power P [W].")] = 200.0,
    grade: Annotated[float, typer.Option("--grade", help="Road grade dz/dx [-].")] = 0.0,
    sweep_grade: Annotated[
        str | None,
        typer.Option("--sweep-grade", help="start:stop:n grade sweep, e.g. 0:0.12:25."),
    ] = None,
    include_rolling_resistance: Annotated[bool, typer.Option("--rolling/--no-rolling")] = True,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Closed-form steady-state speed (`simplified/analysis.py`
    `steady_state_speed`) at a fixed grade, or swept over a grade range."""
    spec = load_bike_spec(config, list(set_))

    if sweep_grade is None:
        theta = math.atan(grade)
        v = analysis.steady_state_speed(
            spec, power, theta, include_rolling_resistance=include_rolling_resistance
        )
        run_dir = _write_analytic_run(
            out,
            "simplified-steady",
            "bike simplified steady",
            {"grade": grade, "power": power, "steady_speed": v},
        )
        if json_output:
            typer.echo(json.dumps({"run_dir": str(run_dir), "grade": grade, "steady_speed": v}))
        else:
            typer.echo(f"wrote {run_dir}")
            _echo_table([("grade", f"{grade:.4f}"), ("steady speed [m/s]", f"{v:.4f}")])
        return

    start_s, stop_s, n_s = sweep_grade.split(":")
    grades = np.linspace(float(start_s), float(stop_s), int(n_s))
    speeds = [
        analysis.steady_state_speed(
            spec, power, math.atan(float(g)), include_rolling_resistance=include_rolling_resistance
        )
        for g in grades
    ]
    run_dir = _write_analytic_run(
        out,
        "simplified-steady-sweep",
        "bike simplified steady --sweep-grade",
        {"power": power, "n_grades": float(len(grades))},
    )
    np.savez(run_dir / "sweep.npz", grade=grades, steady_speed=np.array(speeds))
    if not no_plot:
        _save_xy_plot(
            run_dir / "sweep.png", grades, speeds, xlabel="grade [-]", ylabel="steady speed [m/s]"
        )
    if json_output:
        typer.echo(
            json.dumps(
                {"run_dir": str(run_dir), "grade": grades.tolist(), "steady_speed": speeds}
            )
        )
    else:
        typer.echo(f"wrote {run_dir}")
        for g, v in zip(grades, speeds, strict=True):
            typer.echo(f"  grade={g:.4f}  steady_speed={v:.4f} m/s")


@simplified_app.command("sweep")
def sweep_cmd(
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    param: Annotated[
        str | None, typer.Option("--param", help="Dotted BikeSpec path, e.g. rear.c_2a_rr.")
    ] = None,
    range_: Annotated[tuple[float, float], typer.Option("--range", help="lo hi")] = (0.0, 1.0),
    n: Annotated[int, typer.Option("--n")] = 20,
    metric: Annotated[
        str, typer.Option("--metric", help='Currently only "steady_speed".')
    ] = "steady_speed",
    power: Annotated[float, typer.Option("--power")] = 200.0,
    grade: Annotated[float, typer.Option("--grade")] = 0.0,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Sweep one `BikeSpec` field over a range and report a metric at each
    value (architecture.md S9)."""
    if param is None:
        raise typer.BadParameter("--param is required, e.g. --param rear.c_2a_rr")
    if metric != "steady_speed":
        raise typer.BadParameter(f"unknown --metric {metric!r}; expected 'steady_speed'")

    base_data = _resolve_base_data(config, list(set_))
    lo, hi = range_
    values = np.linspace(lo, hi, n)
    theta = math.atan(grade)
    results: list[float] = []
    for value in values:
        spec_i = bike_spec_from_dict(apply_overrides(base_data, [f"{param}={value}"]))
        results.append(analysis.steady_state_speed(spec_i, power, theta))

    run_dir = _write_analytic_run(
        out,
        "simplified-sweep",
        "bike simplified sweep",
        {"n": float(n), "range_lo": lo, "range_hi": hi},
        solver={"param": param, "metric": metric},
    )
    np.savez(run_dir / "sweep.npz", value=values, metric=np.array(results))
    if not no_plot:
        _save_xy_plot(run_dir / "sweep.png", values, results, xlabel=param, ylabel=metric)

    if json_output:
        payload = {
            "run_dir": str(run_dir),
            "param": param,
            "value": values.tolist(),
            metric: results,
        }
        typer.echo(json.dumps(payload))
    else:
        typer.echo(f"wrote {run_dir}")
        for value, result in zip(values, results, strict=True):
            typer.echo(f"  {param}={value:.6g}  {metric}={result:.4f}")


@simplified_app.command("limits")
def limits_cmd(
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    grade: Annotated[float, typer.Option("--grade")] = 0.0,
    speed: Annotated[
        float, typer.Option("--speed", help="Speed [m/s] for the aero state the limits use.")
    ] = 0.0,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Wheelie/stoppie/traction critical accelerations, as a table
    (architecture.md S9; `simplified/analysis.py`)."""
    spec = load_bike_spec(config, list(set_))
    theta = math.atan(grade)
    aero_model = ConstantCdA(C_1_x_0=spec.aero.C_1_x_0, A_1=spec.aero.A_1, l_1=spec.aero.l_1)
    atmosphere = atmosphere_from_env(
        atmosphere=spec.env.atmosphere,
        rho_constant=spec.env.rho,
        altitude=spec.env.altitude,
        relative_humidity=spec.env.RH,
        temperature_k=spec.env.T,
    )
    aero_state = aero_model.evaluate(
        rho=atmosphere.density(),
        mu=atmosphere.viscosity(),
        v1_x=speed,
        v1_z=0.0,
        wind_x=spec.env.wind_x,
        wind_z=spec.env.wind_z,
    )
    wheelie = analysis.wheelie_accel(spec, theta, aero_state)
    stoppie = analysis.stoppie_accel(spec, theta, aero_state)
    traction = analysis.traction_limit_accel(spec, theta, aero_state)

    metrics = {
        "wheelie_accel": wheelie,
        "stoppie_accel": stoppie,
        "traction_accel_limit": traction.accel_limit,
        "traction_decel_limit": traction.decel_limit,
    }
    run_dir = _write_analytic_run(out, "simplified-limits", "bike simplified limits", metrics)

    if json_output:
        typer.echo(json.dumps({"run_dir": str(run_dir), **metrics}))
    else:
        typer.echo(f"wrote {run_dir}")
        _echo_table(
            [
                ("wheelie accel [m/s^2]", f"{wheelie:.4f}"),
                ("stoppie accel [m/s^2]", f"{stoppie:.4f}"),
                ("traction accel limit [m/s^2]", f"{traction.accel_limit:.4f}"),
                ("traction decel limit [m/s^2]", f"{traction.decel_limit:.4f}"),
            ]
        )


@simplified_app.command("sensitivity")
def sensitivity_cmd(
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    speed: Annotated[float, typer.Option("--speed")] = 8.0,
    grade: Annotated[float, typer.Option("--grade")] = 0.03,
    power: Annotated[float, typer.Option("--power")] = 200.0,
    size_a: Annotated[
        float,
        typer.Option(
            "--size-a",
            help="Power-vs-mass scaling exponent a (forward_motion.tex: physiological, <1).",
        ),
    ] = 0.75,
    size_b: Annotated[
        float,
        typer.Option(
            "--size-b", help="Frontal-area-vs-mass scaling exponent b (forward_motion.tex: ~2/3)."
        ),
    ] = 2.0 / 3.0,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Elasticities `E_M, E_k, E_P`, the `E_M+E_k+E_P=0` identity check,
    the dominance ratio, and the size elasticity (architecture.md S9;
    `simplified/analysis.py`)."""
    spec = load_bike_spec(config, list(set_))
    theta = math.atan(grade)
    e = analysis.elasticities(spec, speed, theta, power)
    identity = e.E_M + e.E_k + e.E_P
    gl = analysis.gradient_limits(spec, speed, theta)
    size_e = analysis.size_elasticity(spec, speed, theta, power, size_a, size_b)

    metrics = {
        "E_M": e.E_M,
        "E_k": e.E_k,
        "E_P": e.E_P,
        "identity_E_sum": identity,
        "dominance_ratio": gl.dominance_ratio,
        "size_elasticity": size_e,
    }
    run_dir = _write_analytic_run(
        out, "simplified-sensitivity", "bike simplified sensitivity", metrics
    )

    if json_output:
        typer.echo(json.dumps({"run_dir": str(run_dir), **metrics}))
    else:
        typer.echo(f"wrote {run_dir}")
        _echo_table(
            [
                ("E_M", f"{e.E_M:.6f}"),
                ("E_k", f"{e.E_k:.6f}"),
                ("E_P", f"{e.E_P:.6f}"),
                ("E_M + E_k + E_P (identity, ~0)", f"{identity:.3e}"),
                ("dominance ratio M*g*sin(theta)/(k*v^2)", f"{gl.dominance_ratio:.4f}"),
                ("flat-limit (E_M,E_k,E_P)", str(gl.flat_limit)),
                ("steep-limit (E_M,E_k,E_P)", str(gl.steep_limit)),
                (f"size elasticity (a={size_a:.3f}, b={size_b:.3f})", f"{size_e:.6f}"),
            ]
        )


def _save_xy_plot(
    path: Path, x: Any, y: Any, *, xlabel: str, ylabel: str
) -> None:
    """A minimal `x`-vs-`y` figure for the non-`Trajectory` commands
    (`steady --sweep-grade`, `sweep`) -- `simplified/plots.py`'s panel
    builders are `Trajectory`-shaped and do not apply here, so this uses
    `core.plotting`'s save/style helpers directly on an ad hoc figure."""
    import matplotlib.pyplot as plt

    from bikephysics.core.plotting import save_figure, style_axis

    fig, ax = plt.subplots(figsize=(7.0, 4.0))
    ax.plot(x, y, marker="o")
    style_axis(ax, ylabel=ylabel, xlabel=xlabel)
    save_figure(fig, path)
