"""validation/energy.py -- the energy-balance audit (architecture.md S12.3).

Written against `core.results.Trajectory` only, so it serves both the
Simplified and (eventually) Planar tiers without a tier branch: pedal work
in must equal the kinetic + potential energy change plus every dissipative
channel the trajectory records (`power_aero`, `power_rr`, `power_brake`,
`power_ground` -- the last identically zero for a Simplified-tier run,
since its no-slip rolling assumption means the ground contact does no
work; the field is reserved for the Planar tier's contact-penalty/chain-
engagement losses). No "friction" channel exists on `Trajectory` today --
the Simplified tier does not model bearing friction (`forward_motion.tex`
has no such term; `core.params.DrivetrainParams.mu_b` is unused by
`simplified/model.py`) -- so it does not appear in this identity; a future
tier/channel need only add itself to `_DISSIPATIVE_FIELDS` below.

Kinetic energy uses the trajectory's own equivalent mass (`spec.m_eq`,
`core.params.BikeSpec`), matching the lumped `M_eq*x_ddot = ...` equation
of motion every power channel here is already a term of
(`simplified/model.py`'s `accel`): `d(KE)/dt = M_eq*v*x_ddot` exactly, by
construction, for the Simplified tier (`M_eq` is constant: no suspension,
no time-varying inertia).

Potential energy is **not** computed independently from `core.road.Road.z
(x)` -- doing so would introduce a `sin(theta)`-vs-`tan(theta)` discrepancy
(a real, few-0.01%-scale error at realistic grades: `z'(x) = tan(theta)`,
but the dynamics' own gravity term is `M*g*sin(theta)*v`, exact only in
the along-road coordinate `forward_motion.tex` itself uses) against the
`power_gravity` term the dynamics actually integrate, which would corrupt
the very closure check this module exists to perform. Instead, `PE(t)` is
defined as the cumulative integral of `power_gravity`, which is exact by
construction (it is literally the same term the equation of motion uses)
and makes this module a drift/quadrature check on the integrator, not an
independent geometry cross-check -- a `Road.z(x)` cross-check belongs in
`validation/cross_tier.py` or a dedicated test fixture, not here.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from bikephysics.core.results import Trajectory

FloatArray = npt.NDArray[np.float64]

#: `Trajectory` power fields that are energy sinks (dissipative, or --
#: `power_ground`, Planar-only -- a Planar contact-compliance loss).
#: Kept as a tuple, not hardcoded inline, so a future tier/channel is a
#: one-line addition rather than a rewrite of `audit`.
_DISSIPATIVE_FIELDS: tuple[str, ...] = ("power_aero", "power_rr", "power_brake", "power_ground")


@dataclass(frozen=True)
class EnergyAudit:
    """One trajectory's energy-balance audit (architecture.md S12.3): the
    total work in from the pedals, the kinetic/potential energy change,
    each dissipative channel's total work, and the resulting closure
    error (absolute, and as a fraction of the total work exchanged)."""

    work_pedal_in: float
    delta_ke: float
    delta_pe: float
    work_aero: float
    work_rr: float
    work_brake: float
    work_ground: float
    closure_error: float
    closure_error_fraction: float


def _cumulative_work(t: FloatArray, power: FloatArray) -> float:
    """Total work done by a power channel over the trajectory
    (trapezoidal quadrature on the already-sampled time series)."""
    return float(np.trapezoid(power, t))


def kinetic_energy(trajectory: Trajectory) -> FloatArray:
    """`KE(t) = (1/2)*M_eq*v(t)^2`, the equivalent kinetic energy the
    lumped `M_eq*x_ddot` equation of motion is built on (`BikeSpec.m_eq`).
    Requires `trajectory.spec` to be a `core.params.BikeSpec` (not
    `None`) -- raises `ValueError` otherwise."""
    if trajectory.spec is None:
        raise ValueError("kinetic_energy requires trajectory.spec (a BikeSpec), got None")
    m_eq = trajectory.spec.m_eq
    return 0.5 * m_eq * np.asarray(trajectory.v, dtype=np.float64) ** 2


def potential_energy(trajectory: Trajectory) -> FloatArray:
    """`PE(t)`, the cumulative integral of `power_gravity` from `t[0]`
    (see module docstring: deliberately not `M*g*Road.z(x)`)."""
    t = np.asarray(trajectory.t, dtype=np.float64)
    power_gravity = np.asarray(trajectory.power_gravity, dtype=np.float64)
    increments = 0.5 * (power_gravity[1:] + power_gravity[:-1]) * np.diff(t)
    return np.concatenate(([0.0], np.cumsum(increments)))


def audit(trajectory: Trajectory) -> EnergyAudit:
    """The energy-balance audit of architecture.md S12.3: pedal work in
    must equal the kinetic + potential energy change plus every
    dissipative channel (`_DISSIPATIVE_FIELDS`). The caller decides what
    `closure_error_fraction` bound to enforce (< 0.1% for a Simplified-
    tier run, < 1% for Planar, per S12.3) -- this function only computes
    the number, it does not assert a tolerance itself."""
    trajectory.validate()
    t = np.asarray(trajectory.t, dtype=np.float64)
    ke = kinetic_energy(trajectory)
    pe = potential_energy(trajectory)
    delta_ke = float(ke[-1] - ke[0])
    delta_pe = float(pe[-1] - pe[0])

    work_pedal_in = _cumulative_work(t, np.asarray(trajectory.power_pedal, dtype=np.float64))
    sink_works = {
        name: _cumulative_work(t, np.asarray(getattr(trajectory, name), dtype=np.float64))
        for name in _DISSIPATIVE_FIELDS
    }
    total_sinks = sum(sink_works.values())

    closure_error = work_pedal_in - (delta_ke + delta_pe + total_sinks)
    abs_sinks = sum(abs(w) for w in sink_works.values())
    total_work = abs(work_pedal_in) + abs(delta_ke) + abs(delta_pe) + abs_sinks
    closure_error_fraction = abs(closure_error) / total_work if total_work > 0.0 else 0.0

    return EnergyAudit(
        work_pedal_in=work_pedal_in,
        delta_ke=delta_ke,
        delta_pe=delta_pe,
        work_aero=sink_works["power_aero"],
        work_rr=sink_works["power_rr"],
        work_brake=sink_works["power_brake"],
        work_ground=sink_works["power_ground"],
        closure_error=closure_error,
        closure_error_fraction=closure_error_fraction,
    )
