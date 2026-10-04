"""Tests for bikephysics.simplified.plots (architecture.md S3.1, task.md T4
step 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from matplotlib.figure import Figure

from bikephysics.core.pedals import ConstantCrankTorque
from bikephysics.core.results import Trajectory
from bikephysics.simplified import plots
from bikephysics.simplified import simulate as sim
from tests.unit.factories import make_minimal_bike_spec


def _make_trajectory(*, v0: float = 0.0) -> Trajectory:
    spec = make_minimal_bike_spec()
    pedal = ConstantCrankTorque(tau=20.0, r_1_pedal=spec.frame.r_1_pedal)
    return sim.simulate(spec, pedal, v0=v0, t_span=(0.0, 20.0), n_output=50)


def test_plot_trajectory_returns_a_figure_with_eight_panels() -> None:
    traj = _make_trajectory()
    fig = plots.plot_trajectory(traj)
    assert isinstance(fig, Figure)
    assert len(fig.axes) == 8


def test_plot_trajectories_overlays_multiple_runs_on_the_same_panels() -> None:
    traj_a = _make_trajectory(v0=0.0)
    traj_b = _make_trajectory(v0=5.0)
    fig = plots.plot_trajectories([traj_a, traj_b], ["a", "b"])
    assert len(fig.axes) == 8
    # each panel's legend should mention both run labels at least once
    legend = fig.axes[1].get_legend()
    assert legend is not None
    legend_text = " ".join(t.get_text() for t in legend.get_texts())
    assert "a" in legend_text
    assert "b" in legend_text


def test_plot_trajectories_rejects_mismatched_lengths() -> None:
    traj = _make_trajectory()
    with pytest.raises(ValueError, match="same length"):
        plots.plot_trajectories([traj, traj], ["only-one-label"])


def test_save_trajectory_figure_writes_a_png(tmp_path: Path) -> None:
    traj = _make_trajectory()
    path = plots.save_trajectory_figure(traj, tmp_path / "fig.png")
    assert path.is_file()
    assert path.stat().st_size > 0
