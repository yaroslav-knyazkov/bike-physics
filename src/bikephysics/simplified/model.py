"""simplified/model.py -- the Simplified tier's equations of motion
(architecture.md S6.1; verbatim transcription of
`latex/simplified/forward_motion.tex`'s Bike/Wheels/Drivetrain/Assembly
boxed equations).

**Sign convention (D-13, architecture.md S10):** `forward_motion.tex`
itself uses `hat z_i`/`hat z_0` pointing *down* (an explicit `\\ai{}`
axis-direction note in the document says so and keeps it that way). Every
formula in this module is transcribed in that same z-down convention --
e.g. `normal_loads`'s internal `K`/`l_C'`/`l_D'` algebra below reproduces
the boxed `{}^{z_0}R_{2a/0}`, `{}^{z_0}R_{2b/0}` expressions exactly as
written, which are *negative* numbers under normal (loaded) operation
("Limits": `{}^{z0}R <= 0` means "no wheel lift").

Every other module in this project (`core.results.Trajectory`, `core.limits`,
the eventual planar tier) uses the shared z-**up** convention instead
(`R_*_z >= 0` means loaded). D-13 option A resolves the clash with a single
named helper, `_z_flip`, applied at exactly the two places a z-signed
quantity crosses this module's public boundary:

- `normal_loads` applies it to its own boxed-formula result on the way
  **out** (doc z-down -> shared z-up);
- `traction` applies the same helper to its `R_2a_z`/`R_2b_z` *arguments*
  on the way **in** (shared z-up -> doc z-down), since those arguments are
  exactly `normal_loads`'s already-flipped output.

`_z_flip` is self-inverse (`_z_flip(_z_flip(v)) == v`), so both directions
are the same operation. **This is the only sign flip for a z-quantity
anywhere in this module** -- if you ever feel the need to write `-R_2a_z`
inline somewhere else in this file, that is a bug; route it through
`_z_flip` or reconsider.

`x`-components (longitudinal force/acceleration) are identical in both
conventions (only `z` flips), so `traction`'s returned `R_x` values and
every function dealing purely with `x_ddot` (`accel`, `chain_tension`,
`m_eq`, `wheelie_accel`/`stoppie_accel` in `analysis.py`) need no flip at
all.

Both wheels are assumed identical (`forward_motion.tex` "Bike" assumptions:
`r_2a == r_2b`, `c_2a_rr == c_2b_rr`) -- every public function here raises
`ValueError` if that assumption is violated, via `_check_equal_wheels`.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt

from bikephysics.core import limits as core_limits
from bikephysics.core.aero import AeroState
from bikephysics.core.mathx import MathBackend, numpy_backend
from bikephysics.core.params import BikeSpec
from bikephysics.core.results import LimitMargins

FloatArray = npt.NDArray[np.float64]

#: Tolerance for the "both wheels identical" assumption check
#: (`_check_equal_wheels`), matching `core.params.BikeSpec`'s own
#: `r_2a`-vs-`r_2b` warning tolerance.
_WHEEL_EQ_REL_TOL = 1e-9
_WHEEL_EQ_ABS_TOL = 1e-12


def _check_equal_wheels(spec: BikeSpec) -> None:
    """Raise `ValueError` if `rear.r_2a != front.r_2b` or
    `rear.c_2a_rr != front.c_2b_rr` -- the Simplified tier's derivation
    (`forward_motion.tex` "Bike" assumptions: "Both tyres share the same
    c_2a,rr=c_2b,rr and r_2a=r_2b") is not valid otherwise."""
    if not math.isclose(
        spec.rear.r_2a, spec.front.r_2b, rel_tol=_WHEEL_EQ_REL_TOL, abs_tol=_WHEEL_EQ_ABS_TOL
    ):
        raise ValueError(
            "simplified tier requires rear.r_2a == front.r_2b (forward_motion.tex "
            f"'Bike' assumptions); got r_2a={spec.rear.r_2a}, r_2b={spec.front.r_2b}"
        )
    if not math.isclose(
        spec.rear.c_2a_rr,
        spec.front.c_2b_rr,
        rel_tol=_WHEEL_EQ_REL_TOL,
        abs_tol=_WHEEL_EQ_ABS_TOL,
    ):
        raise ValueError(
            "simplified tier requires rear.c_2a_rr == front.c_2b_rr (forward_motion.tex "
            f"'Bike' assumptions); got c_2a_rr={spec.rear.c_2a_rr}, c_2b_rr={spec.front.c_2b_rr}"
        )


def _geometry(spec: BikeSpec) -> tuple[float, float, float, float]:
    """`(l_C, l_D, h, L)` (`forward_motion.tex` "Bike" > "Geometry"),
    derived from `core.params.FrameParams`'s axle-referenced fields
    (`latex/2d_systems/frame.tex`: `vv{Aa} = l_1,a*xhat_1 + h_1,a*zhat_1`,
    `vv{AB} = l_1,B*xhat_1`, both in the frame's own z-**up** body axes).

    `l_C` is the horizontal distance from the system CG `a` to the rear
    contact point -- equal to `l_1,a` because the rear axle `A` sits
    directly above its own ground contact on flat ground, matching
    `forward_motion.tex`'s own flat-road assumption. `l_D` is CG-to-front
    (`l_1,B - l_1,a`). `h` is the CG height above the *ground/contact
    plane* (`forward_motion.tex`'s own `h`), which is `h_1,a` (CG height
    above the *axle*, in frame body axes) plus `r_2a` (the axle's own
    height above the ground). None of `l_C`, `l_D`, `h` carry a sign
    ambiguity between the two tiers' z-conventions -- they are plain
    lengths, not signed z-force components, so `_z_flip` does not apply
    here."""
    l_c = spec.frame.l_1_a
    l_d = spec.frame.l_1_B - spec.frame.l_1_a
    h = spec.frame.h_1_a + spec.rear.r_2a
    return l_c, l_d, h, l_c + l_d


def _z_flip[Num: (float, FloatArray)](value: Num) -> Num:
    """The D-13 sign flip (see module docstring): converts a single
    z-component between `forward_motion.tex`'s own z-down convention and
    the shared z-up convention of `core.results` (`R_*_z >= 0` means
    loaded). Self-inverse. The only place a z-quantity's sign is flipped
    in this module."""
    return -value


def m_eq(spec: BikeSpec) -> float:
    """`M_eq` (`forward_motion.tex` "Assembly" > "Equation of motion",
    second boxed equation):

        M_eq = M + (I_2a + I_2b)/r_2a^2 + I_3b*r_2a_cg^2/(r_3b_cr^2*r_2a^2)

    No z-component anywhere in this formula -- no D-13 flip applies."""
    _check_equal_wheels(spec)
    r_2a = spec.rear.r_2a
    dt = spec.drivetrain
    return (
        spec.M
        + (spec.rear.I_2a + spec.front.I_2b) / r_2a**2
        + dt.I_3b * dt.r_2a_cg**2 / (dt.r_3b_cr**2 * r_2a**2)
    )


def chain_tension[Num: (float, FloatArray)](spec: BikeSpec, x_ddot: Num, P_D: Num, P_F: Num) -> Num:
    """Boxed `T_{2a/3b}` (`forward_motion.tex` "Assembly" > "Chain
    tension"):

        T_{2a/3b} = -(I_3b*r_2a_cg)/(r_3b_cr^2*r_2a) * x_ddot
                    + (r_1,pedal/r_3b_cr) * (P_D + P_F)

    No z-component -- no D-13 flip applies."""
    _check_equal_wheels(spec)
    dt = spec.drivetrain
    r_2a = spec.rear.r_2a
    inertial_term = -(dt.I_3b * dt.r_2a_cg) / (dt.r_3b_cr**2 * r_2a) * x_ddot
    pedal_term = (spec.frame.r_1_pedal / dt.r_3b_cr) * (P_D + P_F)
    return inertial_term + pedal_term


def traction[Num: (float, FloatArray)](
    spec: BikeSpec,
    x_ddot: Num,
    T: Num,
    R_2a_z: Num,
    R_2b_z: Num,
    B_2a: Num,
    B_2b: Num,
    sigma: Num,
) -> tuple[Num, Num]:
    """Boxed `{}^{x_0}R_{2a/0}`, `{}^{x_0}R_{2b/0}` (`forward_motion.tex`
    "Assembly" > "Traction"):

        R_2a_x = -(I_2a/r_2a^2)*x_ddot + c_2a,rr*sigma*R_2a_z
                 + (r_2a,cg/r_2a)*T - (sigma*r_2a,bk/r_2a)*B_2a
        R_2b_x = -(I_2b/r_2b^2)*x_ddot + c_2b,rr*sigma*R_2b_z
                 - (sigma*r_2b,bk/r_2b)*B_2b

    `R_2a_z`/`R_2b_z` are accepted in the **shared** z-up convention
    (`R_z >= 0` = loaded, exactly as `normal_loads` below returns them)
    and converted to the doc's own z-down convention via `_z_flip` before
    use -- the D-13 boundary flip, applied here at the point a z-signed
    quantity *enters* the module. The returned `R_x` values are
    x-components (identical in both conventions), so no flip applies on
    the way out.
    """
    _check_equal_wheels(spec)
    r_2a = spec.rear.r_2a
    r_2b = spec.front.r_2b
    r_2a_z_doc = _z_flip(R_2a_z)
    r_2b_z_doc = _z_flip(R_2b_z)
    r_2a_x = (
        -(spec.rear.I_2a / r_2a**2) * x_ddot
        + spec.rear.c_2a_rr * sigma * r_2a_z_doc
        + (spec.drivetrain.r_2a_cg / r_2a) * T
        - (sigma * spec.rear.r_2a_bk / r_2a) * B_2a
    )
    r_2b_x = (
        -(spec.front.I_2b / r_2b**2) * x_ddot
        + spec.front.c_2b_rr * sigma * r_2b_z_doc
        - (sigma * spec.front.r_2b_bk / r_2b) * B_2b
    )
    return r_2a_x, r_2b_x


def normal_loads[Num: (float, FloatArray)](
    spec: BikeSpec,
    x_ddot: Num,
    theta: Num,
    A_x: Num,
    A_m: Num,
    sigma: Num,
) -> tuple[Num, Num]:
    """Boxed `{}^{z_0}R_{2a/0}`, `{}^{z_0}R_{2b/0}` via `K`/`J`
    (`forward_motion.tex` "Assembly" > "Normal reactions"):

        J = I_2a + I_2b + (r_2a,cg/r_3b,cr)*I_3b
        K = -(J/r_2a)*x_ddot - h*M*(x_ddot + g*sin(theta)) - r_2a*A_x + A_m
        R_2a_z = (K - l_D'*M*g*cos(theta)) / L
        R_2b_z = -(K + l_C'*M*g*cos(theta)) / L

    where `l_C' = l_C - l_2a,rr*sigma`, `l_D' = l_D + l_2b,rr*sigma`,
    `l_2a,rr = c_2a,rr*r_2a`, `l_2b,rr = c_2b,rr*r_2b` (`forward_motion.tex`
    "Bike" > "Geometry").

    Computed above in the doc's own z-down convention (both results
    negative under normal, loaded operation -- "Limits": `R_z <= 0` means
    no lift), then converted to the shared z-up convention (`R_z >= 0` =
    loaded) via `_z_flip`, exactly once each, on the way out -- the
    primary D-13 sign-flip site (see module docstring)."""
    _check_equal_wheels(spec)
    l_c, l_d, h, length = _geometry(spec)
    l_2a_rr = spec.rear.c_2a_rr * spec.rear.r_2a
    l_2b_rr = spec.front.c_2b_rr * spec.front.r_2b
    l_c_p = l_c - l_2a_rr * sigma
    l_d_p = l_d + l_2b_rr * sigma
    r_2a = spec.rear.r_2a
    big_m = spec.M
    g = spec.env.g
    big_j = (
        spec.rear.I_2a
        + spec.front.I_2b
        + (spec.drivetrain.r_2a_cg / spec.drivetrain.r_3b_cr) * spec.drivetrain.I_3b
    )
    big_k = -(big_j / r_2a) * x_ddot - h * big_m * (x_ddot + g * np.sin(theta)) - r_2a * A_x + A_m
    r_2a_z_doc = (big_k - l_d_p * big_m * g * np.cos(theta)) / length
    r_2b_z_doc = -(big_k + l_c_p * big_m * g * np.cos(theta)) / length
    return _z_flip(r_2a_z_doc), _z_flip(r_2b_z_doc)


def accel(
    spec: BikeSpec,
    v: float,
    theta: float,
    P_D: float,
    P_F: float,
    B_2a: float,
    B_2b: float,
    aero: AeroState,
    *,
    xp: MathBackend = numpy_backend,
) -> float:
    """Boxed `M_eq*x_ddot = ...` (`forward_motion.tex` "Assembly" >
    "Equation of motion", first boxed equation):

        M_eq*x_ddot = (r_1,pedal*r_2a,cg)/(r_3b,cr*r_2a) * (P_D + P_F)
                      - (sigma/r_2a) * (r_2a,bk*B_2a + r_2b,bk*B_2b)
                      - M*g*sin(theta) - sigma*c_2a,rr*M*g*cos(theta)
                      - A_1,x

    `sigma = sign_smooth(v, v_eps)` from `core.mathx` (architecture.md
    S6.1); `A_1,x = aero.A_1_x`, an already-evaluated `core.aero.AeroState`
    -- this function does not itself call into `core.atmosphere`/
    `core.aero`, keeping it a pure, branch-free arithmetic expression (the
    optimization hot path, architecture.md S6.1: "accel is the only
    function in the whole codebase that may be called millions of times").
    Every operation below is plain arithmetic or `xp.sign_smooth`/`xp.sin`/
    `xp.cos` -- never a Python `if`. No z-component anywhere in this
    equation -- no D-13 flip applies.
    """
    _check_equal_wheels(spec)
    dt = spec.drivetrain
    r_2a = spec.rear.r_2a
    big_m = spec.M
    g = spec.env.g
    sigma = xp.sign_smooth(v, spec.contact.v_eps)
    pedal_term = (spec.frame.r_1_pedal * dt.r_2a_cg) / (dt.r_3b_cr * r_2a) * (P_D + P_F)
    brake_term = (sigma / r_2a) * (spec.rear.r_2a_bk * B_2a + spec.front.r_2b_bk * B_2b)
    gravity_term = big_m * g * xp.sin(theta)
    rolling_term = sigma * spec.rear.c_2a_rr * big_m * g * xp.cos(theta)
    rhs = pedal_term - brake_term - gravity_term - rolling_term - aero.A_1_x
    return rhs / m_eq(spec)


def limit_margins(
    spec: BikeSpec,
    R_2a_x: FloatArray,
    R_2a_z: FloatArray,
    R_2b_x: FloatArray,
    R_2b_z: FloatArray,
    T: FloatArray,
) -> LimitMargins:
    """The `Limits` block (`forward_motion.tex` "Assembly" > "Limits"), as
    signed margins (`<= 0` = satisfied), via `core.limits.limit_margins` --
    the implementation shared by both tiers (architecture.md S5.1, T2).
    All arguments are already in the shared z-up convention (as returned
    by `normal_loads`/`traction` above), so no further D-13 flip applies
    here. Array-typed (matching `core.limits.limit_margins`'s own
    signature): intended for a whole trajectory's time series, not a
    single instant -- for one instant, pass length-1 arrays."""
    _check_equal_wheels(spec)
    return core_limits.limit_margins(
        R_2a_x=R_2a_x,
        R_2a_z=R_2a_z,
        R_2b_x=R_2b_x,
        R_2b_z=R_2b_z,
        T=T,
        mu_s=spec.contact.mu_s,
    )
