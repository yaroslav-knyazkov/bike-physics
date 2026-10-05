"""planar/drivetrain.py -- the crankset sub-assembly + the freewheel
mode-switching driver (architecture.md S7.5.1 item 2, S7.5.4; D-10 option
B, D-10a).

`build_drivetrain` assembles body `3b` + `RevoluteJoint(1, 3b, C)` +
`IdealChain` -- the crankset-specific slice of `planar/assembly.py`'s full
12-DOF system, kept in its own module/tests since it is the one genuinely
*new* piece of physics this ticket adds (D-10 needed the user's sign-off;
`planar/assembly.py` itself is otherwise mechanical wiring of already-T5/T6
-approved elements).

`simulate_with_freewheel` is the §7.5.4 mode-switching driver: the
*disengagement* direction (`T -> 0`, chain goes slack) needs no special
handling at all -- `solver.py`'s already-generic unilateral trial loop
(reused unchanged, per this ticket's own instruction) drops the chain row
smoothly at every RHS evaluation, the same way it would drop any other
unilateral constraint. Only *re-engagement* needs explicit machinery: `u`
is generally inconsistent with `J_chain @ u = 0` the instant the dropped
constraint becomes satisfiable again (`lambda_chain >= 0` once more), so a
perfectly-plastic velocity-projection impulse is required (architecture.md
S7.5.4's own formula, the same Gauss-Newton projection `planar/integrate
.py::project_to_manifold` already uses for bilateral drift, specialized
here to the one unilateral row).

Implementation choice for locating the re-engagement instant: rather than
a `scipy.integrate.solve_ivp` zero-crossing `events=` (which would need a
*conditional* event -- only live while coasting, since while engaged the
chain's own velocity residual is held near zero by the solver and would
fire spuriously on floating-point/Baumgarte noise -- `solve_ivp` has no
such "arm this event only in mode X" primitive), this driver runs the
*whole* remaining span in one `integrate.run()` call, then scans the
*output* samples (at `t_eval`'s own resolution, user-configurable via
`n_output`) for the first coasting -> engaged transition, applies the
impulse there, and restarts only from that point. For a typical ride this
means O(1-10) restarts total (one per genuine freewheel engagement/
re-engagement cycle), not one restart per fixed small interval -- far
cheaper than naive fine-grained segmenting, and still correct to the
output grid's own time resolution (finer than any physically meaningful
engagement timescale once `n_output` is a few thousand over a run of tens
of seconds).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from bikephysics.core.params import BikeSpec
from bikephysics.core.results import ModeLog
from bikephysics.planar.body import RigidBody
from bikephysics.planar.constraints.gear import IdealChain
from bikephysics.planar.constraints.revolute import RevoluteJoint
from bikephysics.planar.integrate import IntegratorMethod, PlanarRunResult, baumgarte_gains, run
from bikephysics.planar.solver import solve_accelerations
from bikephysics.planar.system import MultibodySystem

FloatArray = npt.NDArray[np.float64]

#: The crankset's own translational mass is not a modeled quantity
#: anywhere in this project's parameter deck: `core.params.DrivetrainParams`
#: has no `m_3b` field, and `BikeSpec.M`/`m_eq` (the Simplified tier's own
#: lumped-mass formulas, which S7.5.6's reduction test requires this tier
#: to reproduce *exactly*) explicitly fold the crankset's mass into
#: `frame.m_1` (`core.params.BikeSpec.M`'s own docstring: "The drivetrain
#: has no separate mass field -- its mass is folded into frame.m_1 per the
#: point-mass assumption"). Since the crankset's CoG is pinned to the
#: frame's own point `C` by a zero-offset revolute joint (S7.5.1 item 2),
#: giving body `3b` any *non-negligible* translational mass of its own
#: would double-count that mass against `frame.m_1` and break S7.5.6's
#: exact reduction. `RigidBody` requires `mass > 0` (a structural engine
#: invariant, not a physical one -- a zero-mass rigid body makes the mass
#: matrix singular), so this placeholder exists purely to satisfy that
#: invariant: physically negligible (1 microgram), far below anything that
#: could be measured or that affects any acceptance test's tolerance.
#: **Flagged for architect/user sign-off**: if a real `m_3b` field is ever
#: added to `DrivetrainParams`, this placeholder should be replaced by it
#: and S7.5.6's reduction re-derived to confirm it still holds (today it
#: implicitly assumes `m_3b == 0`, consistent with `frame.m_1` already
#: carrying it).
CRANKSET_MASS_PLACEHOLDER = 1.0e-9


@dataclass(frozen=True)
class DrivetrainAssembly:
    """The crankset sub-assembly built by `build_drivetrain`: the body
    itself, the bottom-bracket revolute joint, and the chain constraint --
    everything `planar/assembly.py` needs to register onto the full
    `MultibodySystem`."""

    crank_body: RigidBody
    bb_joint: RevoluteJoint
    chain: IdealChain


def build_drivetrain(
    spec: BikeSpec, frame_body: RigidBody, *, frame_body_name: str = "1"
) -> DrivetrainAssembly:
    """Body `3b` (CoG at the bottom-bracket point `C`, per S7.5.1 item 2)
    + `RevoluteJoint(1, 3b, C)` + `IdealChain(2a, 3b)` (S7.5.2's (7.5)-(7.6)).

    `frame_body` supplies the already-constructed frame `RigidBody` (so
    this function can resolve its named point `"C"` via
    `RevoluteJoint.from_bodies`, mirroring every other joint in
    `planar/assembly.py`); it is not mutated or re-registered here."""
    dt = spec.drivetrain
    fr = spec.frame
    crank_body = RigidBody(
        name="3b",
        mass=CRANKSET_MASS_PLACEHOLDER,
        inertia=dt.I_3b,
        points={
            "C": (0.0, 0.0),
            "D": (fr.r_1_pedal, 0.0),
            "F": (-fr.r_1_pedal, 0.0),
        },
    )
    bb_joint = RevoluteJoint.from_bodies("bb_joint", frame_body, "C", crank_body, "C")
    chain = IdealChain(
        name="chain",
        r_chainring=dt.r_3b_cr,
        r_cog=dt.r_2a_cg,
        wheel_body="2a",
        crank_body="3b",
        frame_body=frame_body_name,
    )
    return DrivetrainAssembly(crank_body=crank_body, bb_joint=bb_joint, chain=chain)


def _project_velocity_onto_chain(
    system: MultibodySystem, chain: IdealChain, q: FloatArray, u: FloatArray
) -> tuple[FloatArray, float]:
    """The perfectly-plastic chain-engagement impulse (architecture.md
    S7.5.4): `u <- u - M^-1 J^T (J M^-1 J^T)^-1 J u`, specialized to the
    single chain row (the same formula `planar/integrate
    .py::project_to_manifold` uses for bilateral drift correction, but that
    function only ever touches `kind == "bilateral"` rows -- the chain is
    unilateral, so this driver owns its own copy of the same few lines of
    linear algebra rather than widening that generic function's contract).

    Returns `(u_projected, energy_loss)`, `energy_loss = KE_before -
    KE_after >= 0` (a real, physical loss, logged to `ModeLog`)."""
    layout = system.layout
    M = system.mass_matrix()
    M_inv = np.linalg.inv(M)
    J = chain.jacobian(q, layout, system.xp)  # shape (1, nq)
    JMJt = J @ M_inv @ J.T  # shape (1, 1)
    rhs = J @ u  # shape (1,)
    lam_u = np.linalg.solve(JMJt, rhs)
    u_new = u - M_inv @ J.T @ lam_u
    ke_before = float(0.5 * u @ M @ u)
    ke_after = float(0.5 * u_new @ M @ u_new)
    return u_new, max(0.0, ke_before - ke_after)


def _is_chain_engaged(
    system: MultibodySystem,
    chain: IdealChain,
    t: float,
    q: FloatArray,
    u: FloatArray,
    *,
    alpha: float,
    beta: float,
) -> bool:
    result = solve_accelerations(system, t, q, u, alpha=alpha, beta=beta)
    return chain.name in result.active_names


def simulate_with_freewheel(
    system: MultibodySystem,
    chain: IdealChain,
    y0: FloatArray,
    t_span: tuple[float, float],
    *,
    method: IntegratorMethod = "Radau",
    alpha: float | None = None,
    beta: float | None = None,
    h_typ: float = 1e-3,
    n_output: int = 2000,
    max_restarts: int = 64,
    rtol: float = 1e-6,
    atol: float = 1e-8,
    max_drift: float = 1e-4,
    **run_kwargs: object,
) -> tuple[PlanarRunResult, ModeLog]:
    """`planar/integrate.py::run`, plus the §7.5.4 freewheel re-engagement
    impulse (module docstring: engagement-detection via output-sample
    scanning, disengagement handled for free by `solver.py`'s existing
    trial loop). `t_eval` is fixed to one evenly-spaced global grid over
    the full `t_span` up front, so every restart's output stitches onto
    the same grid with no duplicate or missing samples at the seams.

    `rtol`/`atol`/`max_drift` default *looser* than `planar/integrate
    .py::run`'s own defaults (`1e-9`/`1e-9`/`1e-6`). This driver's RHS
    embeds a trial-solve mode switch (`solver.py`'s generic unilateral
    trial loop, evaluated fresh every call) that is continuous but not
    smooth exactly at the engage/disengage boundary; `scipy.integrate
    .solve_ivp`'s Radau step asks for a finite-difference Jacobian near
    that boundary, which the engine's own tight defaults made
    empirically fragile (confirmed: identical inputs diverge to `NaN`
    with `rtol=atol=1e-9` and integrate cleanly with the defaults here).
    Callers needing tighter accuracy (e.g. `validation.cross_tier`'s own
    checks, away from any mode boundary) may still pass `rtol`/`atol`
    explicitly."""
    if alpha is None or beta is None:
        default_alpha, default_beta = baumgarte_gains(h_typ)
        alpha = default_alpha if alpha is None else alpha
        beta = default_beta if beta is None else beta

    nq = system.layout.nq
    t0, t_end = t_span
    t_grid = np.linspace(t0, t_end, n_output)

    t_chunks: list[FloatArray] = []
    q_chunks: list[FloatArray] = []
    u_chunks: list[FloatArray] = []
    log_t: list[float] = []
    log_body: list[str] = []
    log_mode: list[str] = []
    log_energy: list[float] = []

    y_cur = np.asarray(y0, dtype=float)
    t_cur = t0
    was_engaged = _is_chain_engaged(
        system, chain, t_cur, y_cur[:nq], y_cur[nq:], alpha=alpha, beta=beta
    )
    raw_last = None

    for _restart in range(max_restarts + 1):
        eval_grid = t_grid[(t_grid >= t_cur) & (t_grid <= t_end)]
        if eval_grid.size == 0 or eval_grid[0] > t_cur + 1e-12:
            eval_grid = np.concatenate([[t_cur], eval_grid])
        seg = run(
            system,
            y_cur,
            (t_cur, t_end),
            method=method,
            alpha=alpha,
            beta=beta,
            h_typ=h_typ,
            t_eval=eval_grid,
            rtol=rtol,
            atol=atol,
            max_drift=max_drift,
            **run_kwargs,  # type: ignore[arg-type]
        )
        raw_last = seg.raw

        # Scan this segment's samples for the first coasting -> engaged
        # transition (skip index 0: that is `t_cur` itself, already known).
        engage_idx: int | None = None
        prev_engaged = was_engaged
        for i in range(1, len(seg.t)):
            now_engaged = _is_chain_engaged(
                system, chain, float(seg.t[i]), seg.q[i], seg.u[i], alpha=alpha, beta=beta
            )
            if now_engaged and not prev_engaged:
                engage_idx = i
                break
            prev_engaged = now_engaged

        if engage_idx is None:
            # No re-engagement in this segment: keep every sample, done.
            t_chunks.append(seg.t)
            q_chunks.append(seg.q)
            u_chunks.append(seg.u)
            break

        # Keep samples up to and including the engagement instant, apply
        # the impulse there, log it, and restart from the corrected state.
        t_chunks.append(seg.t[: engage_idx + 1])
        q_chunks.append(seg.q[: engage_idx + 1])
        u_pre = seg.u[: engage_idx + 1].copy()
        q_at = seg.q[engage_idx]
        u_at = seg.u[engage_idx]
        u_projected, energy_loss = _project_velocity_onto_chain(system, chain, q_at, u_at)
        u_pre[-1] = u_projected
        u_chunks.append(u_pre)

        log_t.append(float(seg.t[engage_idx]))
        log_body.append(chain.name)
        log_mode.append("chain_engage")
        log_energy.append(energy_loss)

        t_cur = float(seg.t[engage_idx])
        y_cur = np.concatenate([q_at, u_projected])
        was_engaged = True
    else:
        raise RuntimeError(
            f"simulate_with_freewheel: exceeded max_restarts={max_restarts} "
            "re-engagement events; the chain may be chattering between "
            "coasting and engaged modes faster than n_output's grid can "
            "resolve -- increase n_output or max_restarts."
        )

    t_all = np.concatenate(t_chunks)
    q_all = np.concatenate(q_chunks, axis=0)
    u_all = np.concatenate(u_chunks, axis=0)

    # De-duplicate any repeated boundary sample (the restart's first point
    # equals the previous chunk's last point by construction).
    keep = np.concatenate([[True], np.diff(t_all) > 1e-12])
    t_all = t_all[keep]
    q_all = q_all[keep]
    u_all = u_all[keep]

    modes = ModeLog(
        t=np.array(log_t),
        body=tuple(log_body),
        mode=tuple(log_mode),
        energy_loss=np.array(log_energy),
    )
    result = PlanarRunResult(
        t=t_all,
        q=q_all,
        u=u_all,
        drift=seg.drift,
        modes=modes,
        raw=raw_last,
    )
    return result, modes
