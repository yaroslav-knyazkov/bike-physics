"""planar/plots.py -- Planar-tier result figures (architecture.md S3.1,
S7; task.md T7 item 8).

Extends `simplified/plots.py`'s panel set with the planar-tier-specific
additions task.md asks for: per-body states (pitch/pitch-rate, in
addition to the shared x/v/a panels), chain tension with freewheel-mode
shading (from `Trajectory.modes`), and a constraint-residual trace (from
`Trajectory.meta`, populated by `cli/planar_cmds.py`'s run command).
Ground reactions/traction/limit-margins/power-split panels are reused
unchanged from `simplified/plots.py` -- `core.results.Trajectory` is the
same schema for both tiers, so there is nothing tier-specific about them.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes
from matplotlib.figure import Figure

from bikephysics.core.plotting import new_stacked_figure, save_figure, style_axis
from bikephysics.core.results import Trajectory

_N_PANELS = 9


def _plot_normal_loads(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    """Ground reactions in frame 0 (`core.results.Trajectory`'s shared
    convention) -- duplicated from `simplified/plots.py`'s own panel
    rather than importing its private helper, since `Trajectory`'s shape
    is tier-agnostic but that module's functions are not part of its
    public API."""
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.R_2a_z, label=f"{label} R_2a_z (rear)")
        ax.plot(traj.t, traj.R_2b_z, "--", label=f"{label} R_2b_z (front)")
    ax.axhline(0.0, color="k", linewidth=0.8)
    style_axis(ax, ylabel="normal load [N]")


def _plot_traction(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    """Traction vs. the friction-cone bound -- see `_plot_normal_loads`'s
    docstring for why this is duplicated, not imported."""
    for traj, label in zip(trajectories, labels, strict=True):
        mu_s = traj.spec.contact.mu_s if traj.spec is not None else None
        ax.plot(traj.t, traj.R_2a_x, label=f"{label} R_2a_x (rear)")
        ax.plot(traj.t, traj.R_2b_x, "--", label=f"{label} R_2b_x (front)")
        if mu_s is not None:
            ax.plot(traj.t, mu_s * traj.R_2a_z, ":", color="gray", linewidth=0.8)
            ax.plot(traj.t, -mu_s * traj.R_2a_z, ":", color="gray", linewidth=0.8)
    style_axis(ax, ylabel="traction [N]\n(dotted: +/- mu_s*R_2a_z bound)")


def _plot_margins(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    """Limit margins -- see `_plot_normal_loads`'s docstring for why this
    is duplicated, not imported."""
    margin_names = ("traction_rear", "traction_front", "wheelie", "stoppie", "freewheel")
    for traj, label in zip(trajectories, labels, strict=True):
        for name in margin_names:
            ax.plot(traj.t, getattr(traj.margins, name), label=f"{label} {name}")
    ax.axhline(0.0, color="k", linewidth=0.8)
    style_axis(ax, ylabel="limit margins\n(<=0 satisfied)")


def _plot_power_split(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    """Power split by channel -- see `_plot_normal_loads`'s docstring for
    why this is duplicated, not imported."""
    channels = (
        "power_pedal", "power_ground", "power_aero", "power_rr", "power_brake", "power_gravity",
    )
    for traj, label in zip(trajectories, labels, strict=True):
        for name in channels:
            ax.plot(traj.t, getattr(traj, name), label=f"{label} {name}")
    ax.axhline(0.0, color="k", linewidth=0.8)
    style_axis(ax, ylabel="power [W]", xlabel="t [s]")


def _plot_position_velocity(
    ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]
) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.v, label=f"{label} v")
    style_axis(ax, ylabel="v [m/s]")


def _plot_pitch(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.pitch, label=f"{label} pitch")
        ax.plot(traj.t, traj.pitch_rate, "--", label=f"{label} pitch_rate")
    style_axis(ax, ylabel="pitch [rad], pitch_rate [rad/s]")


def _plot_spins(ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.omega_2a, label=f"{label} omega_2a")
        ax.plot(traj.t, traj.omega_2b, "--", label=f"{label} omega_2b")
        ax.plot(traj.t, traj.omega_3b, ":", label=f"{label} omega_3b")
    style_axis(ax, ylabel="spin rate [rad/s]")


def _plot_chain_tension_with_modes(
    ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]
) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        ax.plot(traj.t, traj.T, label=label)
        if traj.modes is not None and len(traj.modes.t):
            for t_event, mode in zip(traj.modes.t, traj.modes.mode, strict=True):
                ax.axvline(float(t_event), color="orange", linewidth=0.8, linestyle=":")
                ax.annotate(
                    mode,
                    (float(t_event), ax.get_ylim()[1] if ax.get_ylim()[1] else 0.0),
                    fontsize="xx-small",
                    rotation=90,
                    va="top",
                )
        coasting = traj.T <= 1e-9
        if np.any(coasting):
            ax.fill_between(
                traj.t, 0, 1, where=coasting, transform=ax.get_xaxis_transform(),
                color="gray", alpha=0.15, step="mid", label="_coasting",
            )
    ax.axhline(0.0, color="r", linewidth=0.8, linestyle="--", label="T = 0 (freewheel)")
    style_axis(ax, ylabel="chain tension T [N]\n(gray = coasting, orange = mode switch)")


def _plot_residual_trace(
    ax: Axes, trajectories: Sequence[Trajectory], labels: Sequence[str]
) -> None:
    for traj, label in zip(trajectories, labels, strict=True):
        max_c = traj.meta.get("max_c_trace")
        max_ju = traj.meta.get("max_Ju_trace")
        if max_c is not None:
            ax.semilogy(traj.t, np.abs(np.asarray(max_c)) + 1e-300, label=f"{label} |c|")
        if max_ju is not None:
            ax.semilogy(traj.t, np.abs(np.asarray(max_ju)) + 1e-300, "--", label=f"{label} |J u|")
        if max_c is None and max_ju is None:
            ax.text(0.5, 0.5, "no residual trace in meta", transform=ax.transAxes, ha="center")
    style_axis(ax, ylabel="constraint residual\n(log scale)", xlabel="t [s]")


_PANEL_BUILDERS = (
    _plot_position_velocity,
    _plot_pitch,
    _plot_spins,
    _plot_normal_loads,
    _plot_traction,
    _plot_chain_tension_with_modes,
    _plot_margins,
    _plot_power_split,
    _plot_residual_trace,
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
        handles, _labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(fontsize="x-small", loc="upper right")
    if title is not None:
        fig.suptitle(title)
    fig.tight_layout()
    return fig


def plot_trajectory(
    trajectory: Trajectory, *, title: str | None = None, label: str = "run"
) -> Figure:
    """The full planar-tier panel set (architecture.md S7, task.md T7
    item 8): per-body states (v, pitch/pitch-rate, spins), ground
    reactions with the friction-cone envelope, chain tension with
    freewheel-mode shading, limit margins, power split, and the
    constraint-residual trace."""
    return _build_figure([trajectory], [label], title=title)


def plot_trajectories(
    trajectories: Sequence[Trajectory], labels: Sequence[str], *, title: str | None = None
) -> Figure:
    """The same panel set as `plot_trajectory`, overlaying several runs."""
    return _build_figure(trajectories, labels, title=title)


def save_trajectory_figure(
    trajectory: Trajectory, path: str | Path, *, title: str | None = None, label: str = "run"
) -> Path:
    """Build and save `plot_trajectory`'s figure in one call."""
    fig = plot_trajectory(trajectory, title=title, label=label)
    return save_figure(fig, path)
