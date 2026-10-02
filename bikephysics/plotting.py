"""
plotting.py -- Plotting helpers for BikeSimulator results.

The Agg backend is forced at import time so plotting works headlessly
(no display required) and never blocks on a GUI window -- this matches
the behavior of the original standalone script. If you want an
interactive window, comment out the `matplotlib.use("Agg")` line below
before importing this module (or delete it), and `BikePlotter.plot(...,
show=True)` will then open a real window instead of being a no-op.

`BikePlotter.plot()` now draws 7 stacked panels: F(t), P(t) (mechanical
power at the pedals, read from `result.power`), x(t), v(t), a(t),
theta_23(t), theta_4(t).
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # non-interactive backend: guarantees headless PNG
                        # output without ever blocking on a GUI window.
                        # Remove this line to get an interactive plt.show().
import matplotlib.pyplot as plt

from bikephysics.simulation import SimulationResult


class BikePlotter:
    """Produces the standard 7-panel figure (and optional multi-run
    comparison plots) for BikeSimulator results."""

    def plot(
        self,
        result: SimulationResult,
        title: str | None = None,
        save_path: str | None = None,
        show: bool = False,
    ) -> None:
        """Plot F(t), P(t), x(t), v(t), a(t), theta_23(t), theta_4(t) as 7
        stacked subplots sharing the time axis (reproduces the original
        script's figure layout, labels, and colors, plus a P(t) = F(t)*v(t)
        mechanical-power panel read from `result.power`)."""
        t = result.t
        F = [result.force_profile(ti) for ti in t]

        fig, axes = plt.subplots(7, 1, sharex=True, figsize=(9, 17))
        fig.suptitle(
            title or "Bike Longitudinal Dynamics Simulation (single-DOF model, main.tex)"
        )

        axes[0].plot(t, F, color="tab:orange")
        axes[0].set_ylabel("F(t) [N]")
        axes[0].grid(True)

        axes[1].plot(t, result.power, color="tab:cyan")
        axes[1].set_ylabel("Power at pedals P(t) [W]")
        axes[1].grid(True)

        axes[2].plot(t, result.x, color="tab:blue")
        axes[2].set_ylabel("x(t) [m]")
        axes[2].grid(True)

        axes[3].plot(t, result.v, color="tab:green")
        axes[3].set_ylabel(r"v(t) = $\dot{x}$ [m/s]")
        axes[3].grid(True)

        axes[4].plot(t, result.a, color="tab:red")
        axes[4].set_ylabel(r"a(t) = $\ddot{x}$ [m/s$^2$]")
        axes[4].grid(True)

        axes[5].plot(t, result.theta_23, color="tab:purple")
        axes[5].set_ylabel(r"$\theta_{2,3}(t)$ [rad]" "\n(wheel, rear=front)")
        axes[5].grid(True)

        axes[6].plot(t, result.theta_4, color="tab:brown")
        axes[6].set_ylabel(r"$\theta_4(t)$ [rad]" "\n(drivetrain/crank)")
        axes[6].set_xlabel("time [s]")
        axes[6].grid(True)

        fig.tight_layout(rect=[0, 0, 1, 0.97])

        if save_path is not None:
            fig.savefig(save_path, dpi=150)
            print(f"Saved plot to {save_path}")

        if show:
            try:
                plt.show()
            except Exception:
                # No display available (headless environment) -- if a
                # save_path was given, the PNG is already saved, so this is
                # safe to ignore. (Also moot with the Agg backend forced
                # above, which makes show() a no-op.)
                pass

        plt.close(fig)

    def compare(
        self,
        results: list[SimulationResult],
        labels: list[str],
        save_path: str | None = None,
        show: bool = False,
    ) -> None:
        """Overlay v(t) from multiple simulation runs on one figure, for
        quick side-by-side comparison (e.g. baseline vs. modified
        parameters, or different force profiles)."""
        fig, ax = plt.subplots(figsize=(9, 5))

        for result, label in zip(results, labels):
            ax.plot(result.t, result.v, label=label)

        ax.set_xlabel("time [s]")
        ax.set_ylabel(r"v(t) = $\dot{x}$ [m/s]")
        ax.set_title("Comparison: v(t) across runs")
        ax.grid(True)
        ax.legend()

        fig.tight_layout()

        if save_path is not None:
            fig.savefig(save_path, dpi=150)
            print(f"Saved comparison plot to {save_path}")

        if show:
            try:
                plt.show()
            except Exception:
                pass

        plt.close(fig)
