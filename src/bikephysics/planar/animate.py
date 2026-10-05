"""planar/animate.py -- the D-16 2D replay (architecture.md S10 D-16,
"matplotlib.animation 2D replay (bike-on-road, force vectors, mode
shading)"; task.md T7 item 8).

Built directly on `(bike: BikeAssembly, t, q)` (the raw per-body state
array, not the reduced `core.results.Trajectory`): a wrong contact point
or a mis-drawn wheel needs the *actual* per-body positions, which
`Trajectory`'s shared schema deliberately does not carry for bodies other
than `"1"` (architecture.md S5.5 is tier-agnostic; a `theta_2a` time
series has no Simplified-tier counterpart). `cli/planar_cmds.py` is
responsible for having that `(t, q)` array available (either freshly
simulated, or reloaded from the extra `planar_state.npz` a `bike planar
run` call writes alongside the shared `result.npz`/`summary.json`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.animation as mpl_animation
import numpy as np
import numpy.typing as npt
from matplotlib.figure import Figure

from bikephysics.core.plotting import DEFAULT_DPI
from bikephysics.core.road import Road
from bikephysics.planar.assembly import BikeAssembly
from bikephysics.planar.body import rotation_matrix

FloatArray = npt.NDArray[np.float64]


def _wheel_circle(center: FloatArray, radius: float, n: int = 24) -> tuple[FloatArray, FloatArray]:
    angles = np.linspace(0.0, 2.0 * np.pi, n)
    return center[0] + radius * np.cos(angles), center[1] + radius * np.sin(angles)


def _road_profile(
    road: Road, x_lo: float, x_hi: float, n: int = 400
) -> tuple[FloatArray, FloatArray]:
    xs = np.linspace(x_lo, x_hi, n)
    zs = np.array([road.z(float(x)) for x in xs])
    return xs, zs


def build_animation(
    bike: BikeAssembly,
    t: FloatArray,
    q: FloatArray,
    *,
    stride: int = 1,
    margin: float = 3.0,
    interval_ms: int = 40,
) -> tuple[mpl_animation.FuncAnimation, Figure]:
    """The 2D replay: road profile, the bike's frame/wheels/crank at each
    (sub-sampled, `stride`) sample, the resolved ground-contact points,
    the contact force vectors, and freewheel-mode shading on the crank
    marker (orange = coasting/dropped, green = engaged).

    `stride` sub-samples `t`/`q` (D-16's own documented cost: "matplotlib
    animation is slow for long runs (sub-sample)") -- the default `1`
    animates every sample, which is fine for the short demo runs this
    ticket's examples use; `cli/planar_cmds.py` exposes `--stride` for
    longer runs.
    """
    import matplotlib.pyplot as plt

    layout = bike.layout
    spec = bike.spec
    frame_body = bike.system.body("1")

    t_sub = t[::stride]
    q_sub = q[::stride]
    n_frames = len(t_sub)

    x1 = q_sub[:, layout.idx_x("1")]
    x_lo, x_hi = float(np.min(x1)) - margin, float(np.max(x1)) + margin
    road_x, road_z = _road_profile(bike.road, x_lo, x_hi)

    fig, ax = plt.subplots(figsize=(10.0, 4.0))
    ax.plot(road_x, road_z, color="dimgray", linewidth=1.0, zorder=0)
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_xlabel("x [m]")
    ax.set_ylabel("z [m]")

    (frame_line,) = ax.plot([], [], "o-", color="tab:blue", linewidth=2.0, label="frame (A-C-B)")
    (rear_circle,) = ax.plot([], [], color="tab:orange", linewidth=1.5)
    (front_circle,) = ax.plot([], [], color="tab:green", linewidth=1.5)
    (rear_contact_pt,) = ax.plot([], [], "x", color="red", markersize=8)
    (front_contact_pt,) = ax.plot([], [], "x", color="red", markersize=8)
    rear_force_arrow = ax.annotate(
        "", xy=(0, 0), xytext=(0, 0), arrowprops={"arrowstyle": "->", "color": "red"}
    )
    front_force_arrow = ax.annotate(
        "", xy=(0, 0), xytext=(0, 0), arrowprops={"arrowstyle": "->", "color": "red"}
    )
    (chain_marker,) = ax.plot([], [], "s", color="green", markersize=10)
    time_text = ax.text(0.01, 0.95, "", transform=ax.transAxes, fontsize="small")
    ax.legend(loc="upper right", fontsize="x-small")

    force_scale = 0.02  # [m / N], arrow length per Newton

    def _frame_geometry(q_i: FloatArray) -> dict[str, Any]:
        theta_1 = float(q_i[layout.idx_theta("1")])
        cog_1 = np.array([q_i[layout.idx_x("1")], q_i[layout.idx_z("1")]])
        r1 = rotation_matrix(theta_1)
        a_world = cog_1 + r1 @ frame_body.local_point("A")
        b_world = cog_1 + r1 @ frame_body.local_point("B")
        c_world = cog_1 + r1 @ frame_body.local_point("C")
        rear_cog = np.array([q_i[layout.idx_x("2a")], q_i[layout.idx_z("2a")]])
        front_cog = np.array([q_i[layout.idx_x("2b")], q_i[layout.idx_z("2b")]])
        return {
            "A": a_world, "B": b_world, "C": c_world,
            "rear_cog": rear_cog, "front_cog": front_cog,
        }

    def _update(frame_idx: int) -> tuple[Any, ...]:
        q_i = q_sub[frame_idx]
        u_i = np.zeros_like(q_i)  # kinematic replay; velocities not needed for geometry
        geom = _frame_geometry(q_i)
        frame_line.set_data(
            [geom["A"][0], geom["C"][0], geom["B"][0]],
            [geom["A"][1], geom["C"][1], geom["B"][1]],
        )
        rx, rz = _wheel_circle(geom["rear_cog"], spec.rear.r_2a)
        rear_circle.set_data(rx, rz)
        fx, fz = _wheel_circle(geom["front_cog"], spec.front.r_2b)
        front_circle.set_data(fx, fz)

        cache: dict[str, float] = {}
        rear_wrench = bike.rear_contact.wrenches(
            float(t_sub[frame_idx]), q_i, u_i, layout, bike.road, spec, cache
        )[0]
        front_wrench = bike.front_contact.wrenches(
            float(t_sub[frame_idx]), q_i, u_i, layout, bike.road, spec, cache
        )[0]
        rear_contact_pt.set_data([rear_wrench.point_world[0]], [rear_wrench.point_world[1]])
        front_contact_pt.set_data([front_wrench.point_world[0]], [front_wrench.point_world[1]])
        rear_force_arrow.set_position(tuple(rear_wrench.point_world))
        rear_force_arrow.xy = tuple(
            rear_wrench.point_world + force_scale * rear_wrench.force_world
        )
        front_force_arrow.set_position(tuple(front_wrench.point_world))
        front_force_arrow.xy = tuple(
            front_wrench.point_world + force_scale * front_wrench.force_world
        )

        chain_marker.set_data([geom["C"][0]], [geom["C"][1]])
        from bikephysics.planar.solver import solve_accelerations

        alpha = beta = 1.0 / (10.0 * 1e-3)
        t_i = float(t_sub[frame_idx])
        solve = solve_accelerations(bike.system, t_i, q_i, u_i, alpha=alpha, beta=beta)
        engaged = bike.drivetrain.chain.name in solve.active_names
        chain_marker.set_color("green" if engaged else "orange")

        mode_label = "engaged" if engaged else "coasting"
        time_text.set_text(f"t = {t_i:.2f} s  (mode: {mode_label})")

        xlo, xhi = geom["A"][0] - margin, geom["B"][0] + margin
        ax.set_xlim(xlo, xhi)
        return (
            frame_line, rear_circle, front_circle, rear_contact_pt, front_contact_pt,
            rear_force_arrow, front_force_arrow, chain_marker, time_text,
        )

    anim = mpl_animation.FuncAnimation(
        fig, _update, frames=n_frames, interval=interval_ms, blit=False
    )
    return anim, fig


def save_animation(
    anim: mpl_animation.FuncAnimation, fig: Figure, path: str | Path, *, fps: int = 25
) -> Path:
    """Save `anim` to `path` -- `.gif` via Pillow (no `ffmpeg` dependency,
    matching architecture.md D-16's documented fallback), `.mp4` via
    `ffmpeg` if available. `fig` (the `Figure` `build_animation` also
    returns) is closed afterward."""
    import matplotlib.pyplot as plt

    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = "pillow" if out_path.suffix.lower() == ".gif" else "ffmpeg"
    anim.save(str(out_path), writer=writer, fps=fps, dpi=DEFAULT_DPI)
    plt.close(fig)
    return out_path
