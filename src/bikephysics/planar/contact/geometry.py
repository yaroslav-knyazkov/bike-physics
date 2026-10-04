"""planar/contact/geometry.py -- the shared per-wheel contact geometry block
(architecture.md S7.6.1; latex/2d_assembly/wheels_ground.tex SS "Contact
point"/"Contact reaction"/"Rolling constraint").

Every `ContactModel` variant (the default `RegularizedContact` of S7.6.2
today; `EventDrivenContact`/`ComplementarityContact` in T11+) needs the same
per-wheel geometric facts at a given instant: the warm-started contact
reference `x*` (delegated to `core.road.contact_reference`, T2 -- this
module never re-implements road geometry), the gap `g_N`, the
rolling-resistance-shifted contact offset `A b_k`, the smoothed direction
indicator `sigma`, and the slip velocity `v_slip`. `contact_geometry()`
computes all of them once per wheel per RHS evaluation and returns a
`ContactState`; `RegularizedContact` (S7.6.2) and `RollingContact` (S7.4,
T11) are both built on top of this single function so the geometry is
never duplicated between the two force-law tiers.

Two derivations worth recording, since neither is spelled out step-by-step
in architecture.md's pseudocode block:

1. **`g_N_dot` needs no rotation term.** `x*` is *defined* by the tangency
   condition `(A - P(x*))` . `x_hat_0(x*) = 0` (`P(x*) = (x*, road.z(x*))`,
   the geometric foot-of-normal point -- `core.road.contact_reference`'s
   residual). Differentiating `g_N(t) = (A(t) - P(x*(t))) . z_hat_0(x*(t))
   - r_k` through time, using that the tangency condition holds at every
   instant (so the `x_hat_0`-component of `A - P(x*)` is identically zero,
   not just at `t`), both the `dP/dx* . z_hat_0` term and the
   `(A-P) . d(z_hat_0)/dt` term vanish exactly -- leaving `g_N_dot = v_A .
   z_hat_0(x*)` exactly, for *any* road curvature, with no dependence on
   `dx*/dt` or `omega_k`. (A spinning wheel sitting still does not change
   its own penetration depth from spin alone, which this result correctly
   reproduces: `g_N_dot` has no rotational term.)
2. **Only `v_slip` drops the `l_k_rr` term; `g_N_dot` and the contact-force
   application point do not.** `wheels_ground.tex`'s own instruction is to
   drop `l_{2a,rr}` from the arm used in the *rolling/stick constraint*
   specifically ("its contribution ... is a second-order correction to the
   constraint, not to the moment balance where it was kept") -- i.e. the
   full `A b_k` (with the forward `l_k_rr` shift) is still what the contact
   force is applied at (`regularized.py`'s `wrenches()`, via `contact_offset`
   below), and `g_N_dot` does not involve `A b_k` at all per point 1 above.
   Only `v_slip`'s *own* moment arm is shortened to the pure radial
   `-r_k * z_hat_0`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend, numpy_backend
from bikephysics.core.road import Road, contact_reference
from bikephysics.planar.body import rotation_matrix
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ContactState:
    """One wheel's resolved contact geometry at one instant (architecture.md
    S7.6's `ContactModel.state()` payload; also what `RegularizedContact`'s
    force law and the future `RollingContact`/`EventDrivenContact` (T11)
    both consume).

    `x_star` is the warm-start value a caller should pass as `x_guess` on
    the *next* call for the same wheel (architecture.md S12.1: "the
    foot-of-normal Newton is warm-started (1-2 iterations, not 10)").
    `contact_offset`/`contact_point_world` include the rolling-resistance
    `l_rr` shift (the force-application point, per `wheels_ground.tex`);
    `v_slip` does not (per the module docstring's point 2).
    """

    body: str
    t: float
    x_star: float
    x_hat_0: FloatArray
    z_hat_0: FloatArray
    axle_world: FloatArray
    v_axle_world: FloatArray
    radius: float
    l_rr: float
    sigma: float
    g_N: float
    g_N_dot: float
    contact_offset: FloatArray
    contact_point_world: FloatArray
    v_slip: float


def _world_point(
    q: FloatArray, layout: SystemLayout, body: str, local: tuple[float, float]
) -> FloatArray:
    """World `(x, z)` of a body-frame point, computed the same way as
    `planar.body.RigidBody.world_point` (duplicated here, not imported,
    since this module only needs a body name + a local offset tuple --
    no `RigidBody` instance -- matching `forces/aerodynamic.py`'s own
    `_world_point` helper's rationale)."""
    theta = layout.theta(q, body)
    cog = layout.position(q, body)
    return cog + rotation_matrix(theta) @ np.asarray(local, dtype=float)


def _point_velocity(
    q: FloatArray, u: FloatArray, layout: SystemLayout, body: str, local: tuple[float, float]
) -> FloatArray:
    """World `(vx, vz)` of a body-frame point, mirroring
    `planar.body.RigidBody.point_velocity` for the same reason as
    `_world_point` above."""
    theta = layout.theta(q, body)
    omega = layout.omega(u, body)
    v_cog = layout.velocity(u, body)
    r = rotation_matrix(theta) @ np.asarray(local, dtype=float)
    return v_cog + omega * np.array([r[1], -r[0]])


def contact_geometry(
    q: FloatArray,
    u: FloatArray,
    layout: SystemLayout,
    *,
    body: str,
    radius: float,
    c_rr: float,
    road: Road,
    v_eps: float,
    axle_point: tuple[float, float] = (0.0, 0.0),
    x_guess: float | None = None,
    t: float = 0.0,
    xp: MathBackend = numpy_backend,
) -> ContactState:
    """Resolve wheel `body`'s contact geometry at the current `(q, u)`
    (architecture.md S7.6.1). `axle_point` is the wheel's own axle `A` in
    its body frame (default `(0, 0)`: axle at the CoG, i.e.
    `r_k_a == 0`, the balanced-wheel default of `core.params`'
    `RearWheelParams.r_2a_a`/`FrontWheelParams.r_2b_a`); a non-zero wheel
    CoG offset from the hub is supported by passing the actual offset
    here. `x_guess` is the previous step's `x*` (warm start, S12.1);
    `None` lets `core.road.contact_reference` seed from the axle's own
    `x`-position instead (its own default behavior).
    """
    axle_world = _world_point(q, layout, body, axle_point)
    v_axle_world = _point_velocity(q, u, layout, body, axle_point)

    x_star = contact_reference(road, (float(axle_world[0]), float(axle_world[1])), radius, x_guess)
    x_hat_0_t, z_hat_0_t = road.frame0(x_star)
    x_hat_0 = np.array(x_hat_0_t, dtype=float)
    z_hat_0 = np.array(z_hat_0_t, dtype=float)

    road_point = np.array([x_star, road.z(x_star)], dtype=float)

    # Gap: `vv{b A} . z_hat_0 - r_k` (wheels_ground.tex "Contact reaction"),
    # `vv{b A} = axle_world - road_point` (the geometric foot-of-normal-to-
    # axle vector, not the rolling-resistance-shifted A-b_k below).
    b_to_a = axle_world - road_point
    g_N = float(np.dot(b_to_a, z_hat_0)) - radius

    # g_N_dot = v_A . z_hat_0(x*) exactly -- see module docstring point 1.
    g_N_dot = float(np.dot(v_axle_world, z_hat_0))

    v_axle_x0 = float(np.dot(v_axle_world, x_hat_0))
    sigma = float(xp.sign_smooth(v_axle_x0, v_eps))

    l_rr = c_rr * radius
    # A b_k = l_rr * sigma * x_hat_0 - r_k * z_hat_0 (the force-application
    # offset, full -- NOT the reduced arm used for v_slip below).
    contact_offset = l_rr * sigma * x_hat_0 - radius * z_hat_0
    contact_point_world = axle_world + contact_offset

    # v_slip: same cross-product kinematics, but with the reduced arm
    # `-r_k * z_hat_0` (l_rr dropped from the arm here only, per
    # wheels_ground.tex / architecture.md S7.6.1). In frame-0 components,
    # omega x (0, -r_k) = (-r_k * omega, 0), so the x_hat_0 component of
    # the resulting contact-point velocity is `v_axle_x0 - r_k * omega`.
    omega = layout.omega(u, body)
    v_slip = v_axle_x0 - radius * omega

    return ContactState(
        body=body,
        t=t,
        x_star=x_star,
        x_hat_0=x_hat_0,
        z_hat_0=z_hat_0,
        axle_world=axle_world,
        v_axle_world=v_axle_world,
        radius=radius,
        l_rr=l_rr,
        sigma=sigma,
        g_N=g_N,
        g_N_dot=g_N_dot,
        contact_offset=contact_offset,
        contact_point_world=contact_point_world,
        v_slip=v_slip,
    )
