"""simplified/plots.py -- Simplified-tier result figures (architecture.md
S3.1, S6; task.md T4 step 3).

Rebuilt from scratch against `core.results.Trajectory` (T1 left this
module an empty stub after `git mv`-ing the old `bikephysics/plotting.py`,
whose `BikePlotter` plotted the now-deleted `SimulationResult`'s stale
single-scalar-force schema -- see this module's own git history at its
pre-move path for that reference, and the module docstring T1 left behind
explaining why it could not be kept).

One multi-panel figure per run, built by `plot_trajectory`; `plot_trajectories`
overlays several runs on the same panel set (the "multi-run overlay"
capability of the original `BikePlotter.compare`), both built on the same
internal `_build_figure` so the single- and multi-run paths cannot drift
apart. Panel set, in order: position, velocity, acceleration, normal loads
(front/rear split), traction vs. the friction-cone bound (per wheel), chain
tension with the `T >= 0` line, limit margins, and the power split by
channel -- the original four time-series panels plus every new one
task.md T4 asks for. All styling goes through `core.plotting` (`style_axis`,
`new_stacked_figure`, `save_figure`, `DEFAULT_DPI`), so this module and the
eventual `planar/plots.py` stay visually consistent.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from matplotlib.axes import Axes
from matplotlib.figure import Figure

from bikephysics.core.plotting import new_stacked_figure, save_figure, style_axis
from bikephysics.core.results import Trajectory

#: Panel titles, in the fixed stacking order `_build_figure` draws them in.
_N_PANELS = 8


def _plot_position(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.x, label=label)
    style_axis(ax, ylabel="x [m]")


def _plot_velocity(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.v, label=label)
    style_axis(ax, ylabel="v [m/s]")


def _plot_acceleration(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.a, label=label)
    style_axis(ax, ylabel="a [m/s^2]")


def _plot_normal_loads(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.R_2a_z, label=f"{label} R_2a_z (rear)")
        ax.plot(traj.t, traj.R_2b_z, "--", label=f"{label} R_2b_z (front)")
    ax.axhline(0.0, color="k", linewidth=0.8)
    style_axis(ax, ylabel="normal load [N]")


def _plot_traction(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        mu_s = traj.spec.contact.mu_s if traj.spec is not None else None
        ax.plot(traj.t, traj.R_2a_x, label=f"{label} R_2a_x (rear)")
        ax.plot(traj.t, traj.R_2b_x, "--", label=f"{label} R_2b_x (front)")
        if mu_s is not None:
            ax.plot(traj.t, mu_s * traj.R_2a_z, ":", color="gray", linewidth=0.8)
            ax.plot(traj.t, -mu_s * traj.R_2a_z, ":", color="gray", linewidth=0.8)
    style_axis(ax, ylabel="traction [N]\n(dotted: +/- mu_s*R_2a_z bound)")


def _plot_chain_tension(
    ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]
) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.T, label=label)
    ax.axhline(0.0, color="r", linewidth=0.8, linestyle="--", label="T = 0 (freewheel)")
    style_axis(ax, ylabel="chain tension T [N]")


def _plot_margins(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    margin_names = ("traction_rear", "traction_front", "wheelie", "stoppie", "freewheel")
    for traj, label in zip(trajectories, labels, strict=True):
        for name in margin_names:
            ax.plot(traj.t, getattr(traj.margins, name), label=f"{label} {name}")
    ax.axhline(0.0, color="k", linewidth=0.8)
    style_axis(ax, ylabel="limit margins\n(<=0 satisfied)")


def _plot_power_split(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    channels = (
        "power_pedal",
        "power_ground",
        "power_aero",
        "power_rr",
        "power_brake",
        "power_gravity",
    )
    for traj, label in zip(trajectories, labels, strict=True):
        for name in channels:
            ax.plot(traj.t, getattr(traj, name), label=f"{label} {name}")
    ax.axhline(0.0, color="k", linewidth=0.8)
    style_axis(ax, ylabel="power [W]", xlabel="t [s]")


_PANEL_BUILDERS = (
    _plot_position,
    _plot_velocity,
    _plot_acceleration,
    _plot_normal_loads,
    _plot_traction,
    _plot_chain_tension,
    _plot_margins,
    _plot_power_split,
)


def _build_figure(
    trajectories: Sequence[Trajectory], labels: Sequence[str], *, title: str | None = None
) -> Figure:
    if len(trajectories) != len(labels):
        raise ValueError(
            f"trajectories and labels must have the same length, "
            f"got {len(trajectories)} and {len(labels)}"
        )
    fig, axes = new_stacked_figure(_N_PANELS)
    for ax, builder in zip(axes, _PANEL_BUILDERS, strict=True):
        builder(ax, trajectories, labels)
        ax.legend(fontsize="x-small", loc="upper right")
    if title is not None:
        fig.suptitle(title)
    fig.tight_layout()
    return fig


def plot_trajectory(
    trajectory: Trajectory, *, title: str | None = None, label: str = "run"
) -> Figure:
    """The full panel set (architecture.md S3.1, task.md T4 step 3) for one
    `Trajectory`: position, velocity, acceleration, normal-load split,
    traction vs. the friction-cone bound, chain tension with the `T >= 0`
    line, limit margins, and the power split by channel."""
    return _build_figure([trajectory], [label], title=title)


def plot_trajectories(
    trajectories: Sequence[Trajectory], labels: Sequence[str], *, title: str | None = None
) -> Figure:
    """The same panel set as `plot_trajectory`, overlaying several runs
    (the multi-run overlay capability of the original `BikePlotter.compare`)."""
    return _build_figure(trajectories, labels, title=title)


def save_trajectory_figure(
    trajectory: Trajectory, path: str | Path, *, title: str | None = None, label: str = "run"
) -> Path:
    """Build and save `plot_trajectory`'s figure in one call -- the
    convenience entry point `cli/simplified_cmds.py` uses for `--no-plot`-
    gated figure output."""
    fig = plot_trajectory(trajectory, title=title, label=label)
    return save_figure(fig, path)
