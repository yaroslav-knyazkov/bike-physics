"""core/plotting.py -- shared matplotlib styling and figure helpers
(architecture.md S3, S16 visualization scope).

Both `simplified/plots.py` and `planar/plots.py` build on this module; it
owns the one place that forces the headless `Agg` backend (so plotting never
blocks on a GUI window in CI or a plain script run) and the one place that
defines shared axis/figure/save conventions, so figures stay visually
consistent across tiers.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # non-interactive backend: headless PNG output only.
# Remove this line locally for an interactive plt.show().

import matplotlib.pyplot as plt
from matplotlib.axes import Axes
from matplotlib.figure import Figure

#: Default save resolution for every figure this project produces.
DEFAULT_DPI = 150


def style_axis(ax: Axes, *, ylabel: str, xlabel: str | None = None, grid: bool = True) -> None:
    """Apply the shared label/grid styling used by every time-series
    panel."""
    ax.set_ylabel(ylabel)
    if xlabel is not None:
        ax.set_xlabel(xlabel)
    ax.grid(grid)


def new_stacked_figure(
    n_panels: int, *, panel_size: tuple[float, float] = (9.0, 2.4)
) -> tuple[Figure, tuple[Axes, ...]]:
    """Create `n_panels` vertically stacked, x-axis-shared subplots -- the
    layout every tier's multi-panel time-series figure uses (migrated from
    the original 6/7-panel `BikePlotter.plot` layout)."""
    width, height_per_panel = panel_size
    fig, axes = plt.subplots(n_panels, 1, sharex=True, figsize=(width, height_per_panel * n_panels))
    axes_tuple: tuple[Axes, ...] = (axes,) if n_panels == 1 else tuple(axes)
    return fig, axes_tuple


def save_figure(fig: Figure, path: str | Path, *, dpi: int = DEFAULT_DPI) -> Path:
    """Save `fig` to `path` at the shared dpi, creating parent directories
    as needed, and close the figure."""
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)
    return out_path
