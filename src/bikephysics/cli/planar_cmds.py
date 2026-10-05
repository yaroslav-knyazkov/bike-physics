"""cli/planar_cmds.py -- `bike planar run|initcheck|animate|crosscheck|
modes` (architecture.md S9's `bike planar` block; task.md T7 item 10).

Mirrors `cli/simplified_cmds.py`'s own wiring and conventions (registers
directly onto `cli.app.planar_app`, redeclares `--config`/`--set`/`--out`/
`--no-plot`/`--json` locally per command, writes `summary.json` via
`core.runio` plus a `result.npz` `Trajectory`). Two small additions
specific to this tier: `planar_state.npz` (the full per-body `(t, q, u)`
array `planar/animate.py` needs -- `core.runio`'s `Trajectory` schema is
tier-agnostic and does not carry per-wheel/-crank positions) and
`mode_log.npz` (`core.results.ModeLog`, not round-tripped by `core.runio`
either, per that module's own docstring) are written alongside the shared
artifacts so `animate --run <dir>` and `modes --run <dir>` work without
re-simulating.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any

import numpy as np
import typer

from bikephysics.cli.app import planar_app
from bikephysics.cli.common import resolve_config_path
from bikephysics.cli.spec_cmds import load_bike_spec
from bikephysics.core.brakes import BrakeAtDistance, BrakeInput, ConstantBrake
from bikephysics.core.params import BikeSpec
from bikephysics.core.pedals import (
    ClippedSine,
    ConstantCrankTorque,
    CosSquared,
    PedalInput,
    VonMises,
    ZeroPedal,
)
from bikephysics.core.results import ModeLog
from bikephysics.core.road import ROAD_PRESET_DIR, Flat, Road, load_road
from bikephysics.core.runio import build_run_summary, make_run_dir, write_summary, write_trajectory
from bikephysics.planar.animate import build_animation, save_animation
from bikephysics.planar.assembly import BikeAssembly, build_bike, build_trajectory
from bikephysics.planar.drivetrain import simulate_with_freewheel
from bikephysics.planar.kinematics import solve_initial_conditions
from bikephysics.planar.plots import save_trajectory_figure
from bikephysics.validation import cross_tier as ct
from bikephysics.validation import energy

FloatArray = Any

_ConfigOption = Annotated[
    str | None, typer.Option("--config", help="Preset name or path to a TOML bike spec.")
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
    "zero", "constant-torque", "cos-squared", "clipped-sine", "von-mises",
)
_BRAKE_MODES: tuple[str, ...] = ("none", "constant", "at-distance")
_CONTACT_MODELS: tuple[str, ...] = ("regularized",)


def _load_road(road: str | None) -> Road:
    path = resolve_config_path(road, [ROAD_PRESET_DIR])
    return load_road(path) if path is not None else Flat()


def _build_pedal_input(
    name: str, spec: BikeSpec, pedal_force: float, pedal_phase: float
) -> PedalInput:
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


def _echo_table(rows: list[tuple[str, str]]) -> None:
    width = max((len(label) for label, _ in rows), default=0)
    for label, value in rows:
        typer.echo(f"{label:<{width}}  = {value}")


def _write_mode_log(run_dir: Path, modes: ModeLog) -> None:
    np.savez(
        run_dir / "mode_log.npz",
        t=modes.t,
        body=np.array(modes.body, dtype=object),
        mode=np.array(modes.mode, dtype=object),
        energy_loss=modes.energy_loss,
    )


def _read_mode_log(run_dir: Path) -> ModeLog:
    path = run_dir / "mode_log.npz"
    if not path.is_file():
        return ModeLog(t=np.array([]), body=(), mode=(), energy_loss=np.array([]))
    with np.load(path, allow_pickle=True) as data:
        return ModeLog(
            t=data["t"],
            body=tuple(data["body"].tolist()),
            mode=tuple(data["mode"].tolist()),
            energy_loss=data["energy_loss"],
        )


def _build_and_run(
    *,
    config: str | None,
    set_: list[str],
    road: str | None,
    pedal: str,
    pedal_force: float,
    pedal_phase: float,
    brake: str,
    brake_force: float,
    brake_front_bias: float,
    brake_x_trigger: float | None,
    contact: str,
    x0: float,
    v0: float,
    t_end: float,
    n_output: int,
    method: str,
    max_drift: float,
) -> tuple[BikeAssembly, Any, ModeLog, float]:
    """Shared setup+simulate path for `run` and `animate --config ...`.
    Returns `(bike, planar_run_result, modes, wall_time_s)`."""
    spec = load_bike_spec(config, set_)
    road_obj = _load_road(road)
    pedal_input = _build_pedal_input(pedal, spec, pedal_force, pedal_phase)
    brake_input = _build_brake_input(brake, brake_force, brake_front_bias, brake_x_trigger)
    bike = build_bike(spec, road_obj, pedal_input, brake_input, contact_model=contact)  # type: ignore[arg-type]
    ic = solve_initial_conditions(bike, road_obj, x_rear_0=x0, v0=v0)
    y0 = np.concatenate([ic.q0, ic.u0])

    t_wall_start = time.monotonic()
    result, modes = simulate_with_freewheel(
        bike.system, bike.drivetrain.chain, y0, (0.0, t_end),
        method=method,  # type: ignore[arg-type]
        n_output=n_output, max_drift=max_drift,
    )
    wall_time_s = time.monotonic() - t_wall_start
    return bike, result, modes, wall_time_s


@planar_app.command("run")
def run_cmd(
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    road: Annotated[str | None, typer.Option("--road", help="Road preset name or path.")] = None,
    pedal: Annotated[
        str, typer.Option("--pedal", help=f"One of {_PEDAL_TEMPLATES}.")
    ] = "clipped-sine",
    pedal_force: Annotated[float, typer.Option("--pedal-force")] = 150.0,
    pedal_phase: Annotated[float, typer.Option("--pedal-phase")] = 0.0,
    brake: Annotated[str, typer.Option("--brake", help=f"One of {_BRAKE_MODES}.")] = "none",
    brake_force: Annotated[float, typer.Option("--brake-force")] = 0.0,
    brake_front_bias: Annotated[float, typer.Option("--brake-front-bias")] = 0.5,
    brake_x_trigger: Annotated[float | None, typer.Option("--brake-x-trigger")] = None,
    contact: Annotated[
        str, typer.Option("--contact", help=f"One of {_CONTACT_MODELS}.")
    ] = "regularized",
    x0: Annotated[float, typer.Option("--x0", help="Rear-axle starting x [m].")] = 0.0,
    v0: Annotated[float, typer.Option("--v0")] = 5.0,
    t_end: Annotated[float, typer.Option("--t-end")] = 30.0,
    n_output: Annotated[int, typer.Option("--n-output")] = 1000,
    method: Annotated[str, typer.Option("--method")] = "Radau",
    max_drift: Annotated[float, typer.Option("--max-drift")] = 1e-4,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """Simulate a planar-tier run and write its run directory
    (architecture.md S9, S7)."""
    bike, result, modes, wall_time_s = _build_and_run(
        config=config, set_=list(set_), road=road, pedal=pedal, pedal_force=pedal_force,
        pedal_phase=pedal_phase, brake=brake, brake_force=brake_force,
        brake_front_bias=brake_front_bias, brake_x_trigger=brake_x_trigger, contact=contact,
        x0=x0, v0=v0, t_end=t_end, n_output=n_output, method=method, max_drift=max_drift,
    )
    traj = build_trajectory(bike, result)
    audit = energy.audit(traj)

    run_dir = make_run_dir(out, "planar-run")
    write_trajectory(run_dir, traj)
    np.savez(run_dir / "planar_state.npz", t=result.t, q=result.q, u=result.u)
    _write_mode_log(run_dir, modes)
    if not no_plot:
        save_trajectory_figure(traj, run_dir / "trajectory.png", title=run_dir.name)

    metrics = {
        "final_x": float(traj.x[-1]),
        "final_v": float(traj.v[-1]),
        "final_a": float(traj.a[-1]),
        "energy_closure_error_fraction": audit.closure_error_fraction,
        "n_mode_switches": float(len(modes.t)),
        "max_drift_c": result.drift.max_c,
        "max_drift_Ju": result.drift.max_Ju,
    }
    summary = build_run_summary(
        run_id=run_dir.name, command="bike planar run", wall_time_s=wall_time_s,
        solver={"method": method, "contact_model": contact},
        tolerances={"max_drift": max_drift},
        metrics=metrics,
    )
    write_summary(run_dir, summary)

    if json_output:
        typer.echo(json.dumps({"run_dir": str(run_dir), **metrics}))
    else:
        typer.echo(f"wrote {run_dir}")
        _echo_table(
            [
                ("final x [m]", f"{traj.x[-1]:.3f}"),
                ("final v [m/s]", f"{traj.v[-1]:.4f}"),
                ("energy closure error", f"{audit.closure_error_fraction * 100.0:.4f} %"),
                ("mode switches", str(len(modes.t))),
                ("wall time [s]", f"{wall_time_s:.3f}"),
            ]
        )


@planar_app.command("initcheck")
def initcheck_cmd(
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    road: Annotated[str | None, typer.Option("--road")] = None,
    x0: Annotated[float, typer.Option("--x0")] = 0.0,
    v0: Annotated[float, typer.Option("--v0")] = 5.0,
    out: _OutOption = Path("runs"),
    no_plot: _NoPlotOption = False,
    json_output: _JsonOption = False,
) -> None:
    """IC solve, constraint residuals, static load split vs. simplified
    (architecture.md S7.8, S9)."""
    spec = load_bike_spec(config, list(set_))
    road_obj = _load_road(road)
    bike = build_bike(spec, road_obj, ZeroPedal(), ConstantBrake())
    ic = solve_initial_conditions(bike, road_obj, x_rear_0=x0, v0=v0)

    metrics = {
        "position_residual_norm": ic.position_residual_norm,
        "velocity_residual_norm": ic.velocity_residual_norm,
        "static_load_rear_lever_arm": ic.static_load_lever_arm[0],
        "static_load_front_lever_arm": ic.static_load_lever_arm[1],
        "static_load_rear_full_solve": ic.static_load_full_solve[0],
        "static_load_front_full_solve": ic.static_load_full_solve[1],
        "delta_static_rear": ic.delta_static_rear,
        "delta_static_front": ic.delta_static_front,
    }
    run_dir = make_run_dir(out, "planar-initcheck")
    summary = build_run_summary(
        run_id=run_dir.name, command="bike planar initcheck", wall_time_s=0.0, metrics=metrics
    )
    write_summary(run_dir, summary)

    if json_output:
        typer.echo(json.dumps({"run_dir": str(run_dir), **metrics}))
    else:
        typer.echo(f"wrote {run_dir}")
        _echo_table(
            [
                ("position residual norm", f"{ic.position_residual_norm:.3e}"),
                ("velocity residual norm", f"{ic.velocity_residual_norm:.3e}"),
                ("static R_2a_z (lever-arm)", f"{ic.static_load_lever_arm[0]:.3f} N"),
                ("static R_2b_z (lever-arm)", f"{ic.static_load_lever_arm[1]:.3f} N"),
                ("static R_2a_z (full KKT solve)", f"{ic.static_load_full_solve[0]:.3f} N"),
                ("static R_2b_z (full KKT solve)", f"{ic.static_load_full_solve[1]:.3f} N"),
                ("delta_static rear [m]", f"{ic.delta_static_rear:.6f}"),
                ("delta_static front [m]", f"{ic.delta_static_front:.6f}"),
            ]
        )


@planar_app.command("animate")
def animate_cmd(
    run: Annotated[Path | None, typer.Option("--run", help="Existing run dir to animate.")] = None,
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    road: Annotated[str | None, typer.Option("--road")] = None,
    pedal: Annotated[str, typer.Option("--pedal")] = "clipped-sine",
    pedal_force: Annotated[float, typer.Option("--pedal-force")] = 150.0,
    pedal_phase: Annotated[float, typer.Option("--pedal-phase")] = 0.0,
    brake: Annotated[str, typer.Option("--brake")] = "none",
    brake_force: Annotated[float, typer.Option("--brake-force")] = 0.0,
    brake_front_bias: Annotated[float, typer.Option("--brake-front-bias")] = 0.5,
    brake_x_trigger: Annotated[float | None, typer.Option("--brake-x-trigger")] = None,
    contact: Annotated[str, typer.Option("--contact")] = "regularized",
    x0: Annotated[float, typer.Option("--x0")] = 0.0,
    v0: Annotated[float, typer.Option("--v0")] = 5.0,
    t_end: Annotated[float, typer.Option("--t-end")] = 10.0,
    n_output: Annotated[int, typer.Option("--n-output")] = 200,
    method: Annotated[str, typer.Option("--method")] = "Radau",
    max_drift: Annotated[float, typer.Option("--max-drift")] = 1e-4,
    stride: Annotated[int, typer.Option("--stride", help="Sub-sample factor for the replay.")] = 1,
    fps: Annotated[int, typer.Option("--fps")] = 25,
    out: _OutOption = Path("runs"),
) -> None:
    """The D-16 2D replay: load `--run <dir>` (`planar_state.npz`, written
    by `run`), or simulate fresh from `--config` (architecture.md S9,
    S10 D-16)."""
    if run is not None:
        spec = load_bike_spec(config, list(set_))
        road_obj = _load_road(road)
        pedal_input = _build_pedal_input(pedal, spec, pedal_force, pedal_phase)
        brake_input = _build_brake_input(brake, brake_force, brake_front_bias, brake_x_trigger)
        bike = build_bike(
            spec, road_obj, pedal_input, brake_input, contact_model=contact  # type: ignore[arg-type]
        )
        state_path = run / "planar_state.npz"
        if not state_path.is_file():
            raise typer.BadParameter(
                f"{state_path} not found -- re-run `bike planar run` "
                "(writes planar_state.npz) first"
            )
        with np.load(state_path) as data:
            t_arr, q_arr = data["t"], data["q"]
        run_dir = run
    else:
        bike, result, _modes, _wall = _build_and_run(
            config=config, set_=list(set_), road=road, pedal=pedal, pedal_force=pedal_force,
            pedal_phase=pedal_phase, brake=brake, brake_force=brake_force,
            brake_front_bias=brake_front_bias, brake_x_trigger=brake_x_trigger, contact=contact,
            x0=x0, v0=v0, t_end=t_end, n_output=n_output, method=method, max_drift=max_drift,
        )
        t_arr, q_arr = result.t, result.q
        run_dir = make_run_dir(out, "planar-animate")

    anim, fig = build_animation(bike, t_arr, q_arr, stride=stride)
    out_path = save_animation(anim, fig, run_dir / "animation.gif", fps=fps)
    typer.echo(f"wrote {out_path}")


@planar_app.command("crosscheck")
def crosscheck_cmd(
    config: _ConfigOption = None,
    set_: _SetOption = [],  # noqa: B006
    out: _OutOption = Path("runs"),
    json_output: _JsonOption = False,
) -> None:
    """Steady-state + chain-tension + reflected-inertia + static-load +
    wheelie/stoppie agreement with the Simplified tier (architecture.md
    S7.5.6, S9) -- `validation.cross_tier.run_all`."""
    spec = load_bike_spec(config, list(set_))
    t_wall_start = time.monotonic()
    report = ct.run_all(spec)
    wall_time_s = time.monotonic() - t_wall_start

    metrics = {
        "steady_state_ok": float(report.steady_state.ok()),
        "chain_tension_ok": float(report.chain_tension.ok()),
        "chain_tension_max_rel_error": report.chain_tension.max_abs_rel_error,
        "reflected_inertia_ok": float(report.reflected_inertia.ok()),
        "static_load_ok": float(report.static_load.ok()),
        "wheelie_stoppie_ok": float(report.wheelie_stoppie.ok()),
        "all_ok": float(report.ok()),
    }
    run_dir = make_run_dir(out, "planar-crosscheck")
    summary = build_run_summary(
        run_id=run_dir.name, command="bike planar crosscheck",
        wall_time_s=wall_time_s, metrics=metrics,
    )
    write_summary(run_dir, summary)

    if json_output:
        typer.echo(json.dumps({"run_dir": str(run_dir), **metrics}))
    else:
        typer.echo(f"wrote {run_dir}")
        for case in report.steady_state.cases:
            typer.echo(
                f"  steady: power={case.power:.0f} grade={case.grade:.3f}  "
                f"planar={case.planar_speed:.4f}  simplified={case.simplified_speed:.4f}  "
                f"rel_err={case.rel_error * 100.0:.3f}%"
            )
        _echo_table(
            [
                ("steady-state OK", str(report.steady_state.ok())),
                (
                    "chain tension OK",
                    f"{report.chain_tension.ok()} "
                    f"(max rel err {report.chain_tension.max_abs_rel_error:.2e})",
                ),
                ("reflected inertia OK", str(report.reflected_inertia.ok())),
                ("static load OK", str(report.static_load.ok())),
                ("wheelie/stoppie OK", str(report.wheelie_stoppie.ok())),
                ("ALL OK", str(report.ok())),
            ]
        )
    if not report.ok():
        raise typer.Exit(code=1)


@planar_app.command("modes")
def modes_cmd(
    run: Annotated[Path, typer.Option("--run", help="Existing run dir (from `bike planar run`).")],
    json_output: _JsonOption = False,
) -> None:
    """Mode timeline: freewheel engagement/disengagement events, with
    their logged energy loss (architecture.md S9, S7.5.4)."""
    modes = _read_mode_log(run)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "t": modes.t.tolist(),
                    "body": list(modes.body),
                    "mode": list(modes.mode),
                    "energy_loss": modes.energy_loss.tolist(),
                }
            )
        )
        return
    if len(modes.t) == 0:
        typer.echo("no mode-switch events logged for this run")
        return
    for t_event, body, mode, loss in zip(
        modes.t, modes.body, modes.mode, modes.energy_loss, strict=True
    ):
        typer.echo(f"t={t_event:.4f}  {body}  {mode}  energy_loss={loss:.4f} J")
