"""core/params.py -- the parameter model (architecture.md S5.1).

`BikeSpec` is the frozen pydantic v2 model tree every bike-specific
computation in the project is built on. Field names are mechanically
derived from `latex/appendix/notation.tex`'s dimension grammar
(`q_{body,descriptor}` -> `q_body_descriptor`): every field name below is a
literal LaTeX symbol, cross-checked against `latex/simplified/forward_motion.tex`
(the Bike/Wheels/Drivetrain/Assembly derivation) and `latex/2d_models/{models,
forces}.tex` (aerodynamics, bearing friction). No field here is invented
physics; where a value is structural/config rather than a measured physical
quantity (`env.atmosphere`, `drivetrain.freewheel`), the docstring says so
explicitly instead of dressing it up as a symbol.

`rear`/`front` are two *distinct* classes (`RearWheelParams`/
`FrontWheelParams`), not one generic `WheelParams` reused for both: S5.1's
own worked example, `--set rear.c_2a_rr=0.008`, fixes the field name under
`rear.` as the literal, body-suffixed symbol `c_2a_rr` -- the ASCII tree's
"WheelParams" label for both rows is a type-shape gloss, not a shared Python
class. The two classes are deliberately near-duplicates of each other.

This module supplies only the TOML (de)serialization and the preset search
directory (`read_toml`, `bike_spec_from_dict`, `BIKE_PRESET_DIR`); applying
`--set` dotted-path overrides and resolving `--config` to a path reuse
`cli.common`'s existing logic unchanged, wired together in
`cli.spec_cmds.load_bike_spec` -- `core` must not import `cli`
(architecture.md S4, enforced by the `ruff` `TID251` banned-api rule), so
that half of the plumbing cannot live here.
"""

from __future__ import annotations

import math
import tomllib
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bikephysics.core.units import G_STANDARD

#: Shipped bike presets (architecture.md S5.1: "Presets ship in
#: bikephysics/data/bikes/*.toml"). Road presets are `core.road`'s concern
#: (`core.road.ROAD_PRESET_DIR`), not `BikeSpec`'s.
BIKE_PRESET_DIR = Path(__file__).resolve().parents[1] / "data" / "bikes"


class _FrozenModel(BaseModel):
    """Shared base: every S5.1 model is frozen (immutable after
    construction), matching `core.results`'s frozen dataclasses."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class FrameParams(_FrozenModel):
    """Frame+rider rigid body (`latex/2d_systems/frame.tex`; lumped per
    `forward_motion.tex`'s "Bike" point-mass assumption). Point `A` (rear
    axle) is this body's own origin; point `a` is its CoG, `B` the front
    axle, `C` the drivetrain/BB bearing point."""

    m_1: float = Field(gt=0, description="m_1 -- frame+rider mass [kg]")
    I_1: float = Field(
        gt=0,
        description="I_1 -- frame+rider planar inertia about hat-y, through its own CoG [kg*m^2]",
    )
    l_1_a: float = Field(
        description="l_1,a -- CoG x-offset from rear axle A, in frame body axes [m]"
    )
    h_1_a: float = Field(
        description="h_1,a -- CoG z-offset from rear axle A, in frame body axes [m]"
    )
    l_1_B: float = Field(
        gt=0,
        description="l_1,B -- front axle B x-offset from rear axle A (wheelbase component) [m]",
    )
    l_1_C: float = Field(
        description="l_1,C -- drivetrain bearing point C x-offset from rear axle A [m]"
    )
    h_1_C: float = Field(
        description="h_1,C -- drivetrain bearing point C z-offset from rear axle A [m]"
    )
    r_1_pedal: float = Field(
        gt=0, description="r_1,pedal -- pedal spindle radius about C (crank length) [m]"
    )


class RearWheelParams(_FrozenModel):
    """Rear wheel (body 2a), cassette folded in per `latex/2d_systems/wheel.tex`.
    Field names match `forward_motion.tex`'s "Wheels"/"Assembly" sections
    verbatim."""

    m_2a: float = Field(gt=0, description="m_2a -- rear wheel mass [kg]")
    I_2a: float = Field(
        gt=0, description="I_2a -- rear wheel planar inertia about its axle [kg*m^2]"
    )
    r_2a: float = Field(gt=0, description="r_2a -- rear wheel radius [m]")
    r_2a_a: float = Field(
        default=0.0,
        ge=0,
        description="r_2a,a -- rear wheel CoG offset from the hub [m] (0 = balanced wheel)",
    )
    c_2a_rr: float = Field(
        ge=0, lt=0.1, description="c_2a,rr -- rear rolling resistance coefficient [-]"
    )
    r_2a_bk: float = Field(gt=0, description="r_2a,bk -- rear effective brake-pad radius [m]")


class FrontWheelParams(_FrozenModel):
    """Front wheel (body 2b). Carries no chain/cog radius -- the front
    wheel is undriven (`forward_motion.tex` "Front wheel" subsection)."""

    m_2b: float = Field(gt=0, description="m_2b -- front wheel mass [kg]")
    I_2b: float = Field(
        gt=0, description="I_2b -- front wheel planar inertia about its axle [kg*m^2]"
    )
    r_2b: float = Field(gt=0, description="r_2b -- front wheel radius [m]")
    r_2b_a: float = Field(
        default=0.0,
        ge=0,
        description="r_2b,a -- front wheel CoG offset from the hub [m] (0 = balanced wheel)",
    )
    c_2b_rr: float = Field(
        ge=0, lt=0.1, description="c_2b,rr -- front rolling resistance coefficient [-]"
    )
    r_2b_bk: float = Field(gt=0, description="r_2b,bk -- front effective brake-pad radius [m]")


class DrivetrainParams(_FrozenModel):
    """Crankset + chain (body 3b), `forward_motion.tex` "Drivetrain" section.
    `eta_chain`/`mu_b`/`d_bearing`/`freewheel` are modeling-mode selectors
    and the bearing-friction model's inputs (`latex/2d_models/models.tex`
    S bearing-friction-model), not themselves boxed symbols from the
    forward-motion derivation, which assumes a lossless chain."""

    I_3b: float = Field(
        gt=0, description="I_3b -- crankset planar inertia about the BB axle E [kg*m^2]"
    )
    r_3b_cr: float = Field(gt=0, description="r_3b,cr -- chainring radius (|EG|) [m]")
    r_2a_cg: float = Field(gt=0, description="r_2a,cg -- rear cog/sprocket radius (|AF|) [m]")
    n_teeth_cr: int | None = Field(
        default=None,
        gt=0,
        description="chainring tooth count, alternate input to r_3b_cr via radii_from_teeth()",
    )
    n_teeth_cg: int | None = Field(
        default=None,
        gt=0,
        description="rear cog tooth count, alternate input to r_2a_cg via radii_from_teeth()",
    )
    chain_pitch: float = Field(
        default=0.0127,
        gt=0,
        description="chain pitch [m] (standard bicycle chain: 1/2 inch = 0.0127 m)",
    )
    eta_chain: float = Field(
        default=1.0,
        gt=0,
        le=1.0,
        description=(
            "chain mechanical efficiency [-] (forward_motion.tex assumes 1.0: lossless chain)"
        ),
    )
    mu_b: float = Field(
        default=0.0015,
        ge=0,
        description=(
            "mu_b -- bearing friction coefficient [-] "
            "(latex/2d_models/models.tex S bearing-friction-model; "
            "~0.0015 for deep-groove ball bearings)"
        ),
    )
    d_bearing: dict[str, float] = Field(
        default_factory=lambda: {"rear_axle": 0.01, "front_axle": 0.01, "bb": 0.024},
        description="d_{k/l} -- bearing bore/shaft diameter at each joint [m], keyed by joint name",
    )
    freewheel: bool = Field(
        default=True,
        description=(
            "whether the rear hub has a freewheel/ratchet mechanism "
            "(structural, not a symbol); gates how core.limits's "
            "freewheel margin is interpreted downstream"
        ),
    )

    def radii_from_teeth(self) -> tuple[float, float]:
        """Standard chain-sprocket pitch-radius geometry, `r = p / (2 sin(pi/N))`
        -- off-the-shelf drivetrain geometry, not from this project's own
        derivation. Returns `(r_3b_cr, r_2a_cg)` computed from
        `n_teeth_cr`/`n_teeth_cg` and `chain_pitch` when both tooth counts
        are given; otherwise returns the directly-specified `r_3b_cr`,
        `r_2a_cg` unchanged. This does not mutate the model -- it is a pure
        helper for preset authoring / cross-checking, not an implicit
        override of the stored radii."""
        if self.n_teeth_cr is not None and self.n_teeth_cg is not None:
            r_cr = self.chain_pitch / (2.0 * math.sin(math.pi / self.n_teeth_cr))
            r_cg = self.chain_pitch / (2.0 * math.sin(math.pi / self.n_teeth_cg))
            return r_cr, r_cg
        return self.r_3b_cr, self.r_2a_cg


class AeroParams(_FrozenModel):
    """Aerodynamic coefficients and their linearization slopes
    (`latex/2d_models/models.tex` S aerodynamics-model). `C_1_x_0` is the
    `beta_rel = 0` intercept that "recovers exactly the lumped C_dA" --
    the only coefficient `core.aero.ConstantCdA` uses. The twelve
    `d*_d*` slopes are the four partial derivatives (`dxi`, `domega`,
    `dbeta_rel`, `dRe`) of each of the three coefficients (`x`, `z`, `m`)
    from the linearization equation; all default to 0 (uncalibrated) per
    `core.aero.Linearized`."""

    C_1_x_0: float = Field(description="C_1,x,0 -- drag coefficient intercept [-]")
    C_1_z_0: float = Field(
        default=0.0,
        description=(
            "C_1,z,0 -- side/lift coefficient intercept [-] (lift neglected by forward_motion.tex)"
        ),
    )
    C_1_m_0: float = Field(
        default=0.0, description="C_1,m,0 -- pitching-moment coefficient intercept [-]"
    )
    A_1: float = Field(gt=0, description="A_1 -- frontal reference area [m^2]")
    l_1: float = Field(
        default=1.0,
        gt=0,
        description="l_1 -- reference length for the moment arm and Reynolds number [m]",
    )

    dC_1_x_dxi: float = Field(
        default=0.0, description="dC_1,x/dxi_1 -- drag slope w.r.t. rider posture/configuration [-]"
    )
    dC_1_x_domega: float = Field(
        default=0.0, description="dC_1,x/domega_1 -- drag slope w.r.t. frame pitch rate [-]"
    )
    dC_1_x_dbeta_rel: float = Field(
        default=0.0, description="dC_1,x/dbeta_rel -- drag slope w.r.t. angle of attack [1/rad]"
    )
    dC_1_x_dRe: float = Field(
        default=0.0, description="dC_1,x/dRe -- drag slope w.r.t. Reynolds number [-]"
    )
    dC_1_z_dxi: float = Field(
        default=0.0, description="dC_1,z/dxi_1 -- side-force slope w.r.t. rider posture [-]"
    )
    dC_1_z_domega: float = Field(
        default=0.0, description="dC_1,z/domega_1 -- side-force slope w.r.t. frame pitch rate [-]"
    )
    dC_1_z_dbeta_rel: float = Field(
        default=0.0,
        description="dC_1,z/dbeta_rel -- side-force slope w.r.t. angle of attack [1/rad]",
    )
    dC_1_z_dRe: float = Field(
        default=0.0, description="dC_1,z/dRe -- side-force slope w.r.t. Reynolds number [-]"
    )
    dC_1_m_dxi: float = Field(
        default=0.0, description="dC_1,m/dxi_1 -- pitching-moment slope w.r.t. rider posture [-]"
    )
    dC_1_m_domega: float = Field(
        default=0.0,
        description="dC_1,m/domega_1 -- pitching-moment slope w.r.t. frame pitch rate [-]",
    )
    dC_1_m_dbeta_rel: float = Field(
        default=0.0,
        description="dC_1,m/dbeta_rel -- pitching-moment slope w.r.t. angle of attack [1/rad]",
    )
    dC_1_m_dRe: float = Field(
        default=0.0, description="dC_1,m/dRe -- pitching-moment slope w.r.t. Reynolds number [-]"
    )


class ContactParams(_FrozenModel):
    """Ground-contact friction and the planar tier's regularized unilateral
    normal-contact parameters (`latex/2d_assembly/wheels_ground.tex`;
    `mu_s`/`mu_k` also feed `core.limits`'s traction margins). `k_N`/
    `delta_target`/`zeta_N`/`v_eps`/`v_stribeck` are solver-regularization
    inputs for the planar tier's contact model (architecture.md S7, out of
    this ticket's reading scope) -- named here because S5.1 places them on
    `BikeSpec`, unused by the Simplified tier."""

    mu_s: float = Field(
        gt=0,
        description="mu_s -- static (no-slip) friction coefficient [-], shared by both contacts",
    )
    mu_k: float = Field(
        gt=0,
        description="mu_k -- kinetic (sliding) friction coefficient [-], shared by both contacts",
    )
    k_N: float | None = Field(
        default=None,
        gt=0,
        description=(
            "k_N -- normal contact stiffness [N/m] (planar tier "
            "regularized contact); None = derive from delta_target at runtime"
        ),
    )
    delta_target: float = Field(
        default=0.001,
        gt=0,
        description="target normal-contact penetration depth at a reference load [m] (planar tier)",
    )
    zeta_N: float = Field(
        default=1.0, gt=0, description="zeta_N -- normal-contact damping ratio [-] (planar tier)"
    )
    v_eps: float = Field(
        default=0.01,
        gt=0,
        description="velocity regularization scale [m/s] for sign_smooth's sigma = sgn(x_dot)",
    )
    v_stribeck: float = Field(
        default=0.01,
        gt=0,
        description=(
            "Stribeck velocity scale [m/s] for the static/kinetic friction transition (planar tier)"
        ),
    )

    @model_validator(mode="after")
    def _check_mu_order(self) -> ContactParams:
        if self.mu_k > self.mu_s:
            raise ValueError(f"mu_k ({self.mu_k}) must be <= mu_s ({self.mu_s})")
        return self


class EnvParams(_FrozenModel):
    """Environment: gravity, atmosphere, and wind (`latex/2d_models/models.tex`
    S gravity/atmosphere; `latex/appendix/atmosphere.tex`). `atmosphere`
    selects which `core.atmosphere` model `rho`/`mu` are computed from -- a
    modeling-mode selector, not a symbol."""

    g: float = Field(
        default=G_STANDARD, gt=0, description="g -- gravitational acceleration [m/s^2]"
    )
    atmosphere: Literal["constant", "isa"] = Field(
        default="constant",
        description="atmosphere model selector: constant rho/T, or altitude-based ISA",
    )
    rho: float = Field(
        default=1.225,
        gt=0,
        description="rho -- air density [kg/m^3], used when atmosphere == 'constant'",
    )
    altitude: float = Field(
        default=0.0,
        ge=0,
        description="h -- altitude above sea level [m], used when atmosphere == 'isa'",
    )
    RH: float = Field(default=0.0, ge=0, le=1, description="RH -- relative humidity [-], in [0,1]")
    T: float | None = Field(
        default=None,
        gt=0,
        description=(
            "T -- air temperature override [K]; None = use the atmosphere "
            "model's own T(h) (isa) or the ISA sea-level reference (constant)"
        ),
    )
    wind_x: float = Field(default=0.0, description="v_wind's x_i-component [m/s]")
    wind_z: float = Field(default=0.0, description="v_wind's z_i-component [m/s]")


class RiderParams(_FrozenModel):
    """Optimization bounds on the rider's control input
    (`latex/2d_models/models.tex` S pedal-force-model's "physical pedaling
    limits"). All optional: `None` means unbounded for that quantity."""

    P_max: float | None = Field(
        default=None, gt=0, description="maximum sustained total pedal power [W]"
    )
    P_pedal_max: float | None = Field(
        default=None, gt=0, description="maximum force at a single pedal [N]"
    )
    cadence_max: float | None = Field(
        default=None, gt=0, description="omega_3b,max -- maximum crank angular speed [rad/s]"
    )


class BikeSpec(_FrozenModel):
    """The complete bike+rider parameter deck (architecture.md S5.1)."""

    frame: FrameParams
    rear: RearWheelParams
    front: FrontWheelParams
    drivetrain: DrivetrainParams
    aero: AeroParams
    contact: ContactParams
    env: EnvParams
    rider: RiderParams = Field(default_factory=RiderParams)

    @model_validator(mode="after")
    def _warn_on_unequal_wheel_radii(self) -> BikeSpec:
        # forward_motion.tex's own assumption list: "Both tyres share the
        # same c_2a_rr=c_2b_rr and r_2a=r_2b". The Simplified tier *requires*
        # this (simplified/model.py raises, T3); the Planar tier does not, so
        # this is a warning here, not a validation error.
        if not math.isclose(self.rear.r_2a, self.front.r_2b, rel_tol=1e-9, abs_tol=1e-12):
            warnings.warn(
                "rear.r_2a != front.r_2b: the Simplified tier assumes equal "
                "wheel radii and will raise; the Planar tier tolerates this.",
                stacklevel=2,
            )
        return self

    @property
    def M(self) -> float:
        """Total lumped mass `M` (forward_motion.tex "Bike" assumptions):
        frame+rider plus both wheels. The drivetrain has no separate mass
        field -- its mass is folded into `frame.m_1` per the point-mass
        assumption."""
        return self.frame.m_1 + self.rear.m_2a + self.front.m_2b

    @property
    def m_eq(self) -> float:
        """The boxed `M_eq` (forward_motion.tex "Equation of motion"):
        `M + (I_2a+I_2b)/r_2a^2 + I_3b*r_2a_cg^2/(r_3b_cr^2*r_2a^2)`.
        Uses `rear.r_2a` for both wheel terms, per the shared-radius
        assumption this formula itself is derived under."""
        r = self.rear.r_2a
        return (
            self.M
            + (self.rear.I_2a + self.front.I_2b) / r**2
            + self.drivetrain.I_3b
            * self.drivetrain.r_2a_cg**2
            / (self.drivetrain.r_3b_cr**2 * r**2)
        )

    @property
    def gear_ratio(self) -> float:
        """`G = r_3b_cr / r_2a_cg` (forward_motion.tex's fixed gear ratio)."""
        return self.drivetrain.r_3b_cr / self.drivetrain.r_2a_cg


def read_toml(path: Path) -> dict[str, object]:
    """Read a TOML file into a plain dict (stdlib `tomllib`, binary mode)."""
    with path.open("rb") as f:
        return tomllib.load(f)


def bike_spec_from_dict(data: Mapping[str, object]) -> BikeSpec:
    """Validate a plain dict (as loaded from TOML, optionally with `--set`
    overrides already applied) into a `BikeSpec`."""
    return BikeSpec.model_validate(data)


def list_bike_presets() -> list[str]:
    """Names (without `.toml`) of every shipped bike preset."""
    if not BIKE_PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in BIKE_PRESET_DIR.glob("*.toml"))
