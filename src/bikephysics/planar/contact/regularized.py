"""planar/contact/regularized.py -- the `ContactModel` protocol and the
default `RegularizedContact` implementation (architecture.md S7.6, S7.6.2;
D-9).

**Sign correction to (7.12), recorded for architect sign-off.** Architecture.md
S7.6.2 writes the Hunt-Crossley normal force as

    R_z = max(0, k_N * delta^e * (1 + 1.5 * c_HC * g_N_dot))                (7.12, as written)

with `delta = max(0, -g_N)` the penetration depth and `g_N_dot` the *gap*
rate (not the penetration rate). Linearizing this literal form about the
static equilibrium penetration `delta_target` (where `k_N * delta_target ==
M_total * g` exactly, by construction of `k_N`) gives the characteristic
equation `m*s^2 - 1.5*c_N*delta_target*s + k_N = 0` for the deviation `w =
z_axle - z_eq` -- a *negative* damping coefficient (one stable, one
unstable real root), not the decaying oscillation the acceptance tests
require ("a single wheel ... settles ... with the expected damped-
oscillation decay"). The standard Hunt-Crossley formula in the literature
uses the *penetration* rate `delta_dot` (positive while compressing), not
the gap rate: `F = k*delta^e*(1 + chi*delta_dot)`. Since `delta = -g_N`
while touching, `delta_dot = -g_N_dot`; substituting recovers a *minus*
sign in front of `g_N_dot`:

    R_z = max(0, k_N * delta^e * (1 - 1.5 * c_HC * g_N_dot))               (7.12, corrected)

which linearizes to `m*s^2 + 1.5*c_N*delta_target*s + k_N = 0` -- a
standard, stable, underdamped-for-small-`zeta_N` characteristic equation,
confirmed against the damped-oscillation acceptance test in
`tests/analytic/test_contact_regularized.py`. This file implements the
corrected sign; `+1.5*c_HC*g_N_dot` anywhere in this module is the defect
architecture.md's own S12.3 ("no magic numbers ... every tolerance ...
comes from a documented derivation rule") would flag. **This is a
candidate architecture.md erratum, not a liberty taken lightly** -- see
the developer's final report for T6 for the explicit flag to the architect.

Friction (7.13) has no such issue: Stribeck-smoothed Coulomb,
`mu(v_slip) = mu_k + (mu_s - mu_k) * exp(-(v_slip/v_stribeck)^2)`,
`R_x = -mu(v_slip) * R_z * sign_smooth(v_slip, v_eps)`, transcribed as
written.

**Second correction: `c_HC` must be scaled by `delta_target`, not just
`c_N / k_N`.** Expanding the (sign-corrected) force law,
`R_z = k_N*delta + 1.5*c_N*delta*delta_dot` -- the damping *coefficient*
multiplying `delta_dot` is itself `1.5*c_N*delta`, proportional to the
*current* penetration, not a constant `c_N`. At the system's actual
operating point `delta ~= delta_target` (a configured 1-3 mm, S7.6.2), the
literal `c_HC = c_N/k_N` therefore delivers an effective damping ratio of
only `1.5*zeta_N*delta_target` -- with `delta_target` a small SI length,
this is `zeta_N` scaled down by ~1e-3, i.e. *negligible* damping right
where the acceptance test looks (confirmed numerically: with the literal
`c_HC`, `zeta_N` in the documented 0.7-1.0 range produces an oscillation
that has not visibly decayed after 5 s of simulated time). Rescaling

    c_HC = c_N / (1.5 * k_N * delta_target)

makes the *effective* damping ratio at `delta == delta_target` equal
exactly `zeta_N` (the quantity S7.6.2 names and documents a recommended
range for), which is the only reading under which `zeta_N` means what its
own docstring in `core.params.ContactParams` says it means. Verified
numerically: `zeta_N in {0.1, 0.3, 0.5}` now produce visibly decaying
oscillations whose successive peak amplitudes shrink by the expected
per-period factor. **Flagged for architect sign-off alongside the sign
correction above** -- both are recorded in `architecture-notes.md` and the
developer's final report for this ticket.

**Stiffness is derived, never hardcoded** (architecture.md S7.6.2, S12.3):
`k_N = M_total * g / delta_target` (shared across every wheel of one
vehicle, sized so the *whole* vehicle settles to `delta_target` under its
own weight) and `c_N = 2 * zeta_N * sqrt(k_N * m_eff)` (`m_eff` = *this*
wheel's own static load share / g, so each wheel's damping ratio relative
to its own effective mass is `zeta_N`, even though `k_N` is shared).
"""

from __future__ import annotations

import math
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import numpy_backend
from bikephysics.core.road import Road
from bikephysics.planar.constraints.base import Constraint
from bikephysics.planar.contact.geometry import ContactState, contact_geometry
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]

#: `solve_ivp`'s `events` shape (architecture.md S7.6's `events()` return
#: type): a zero-crossing-detecting scalar function of `(t, y)`.
EventFn = Callable[[float, FloatArray], float]


@dataclass(frozen=True)
class ContactWrench:
    """One wheel's resolved contact force, ready for
    `planar.body.apply_wrench` (architecture.md S7.6: "force `R_x x_hat_0 +
    R_z z_hat_0` applied at the world point `A + A b_k`")."""

    body: str
    force_world: FloatArray
    point_world: FloatArray
    moment: float = 0.0


class ContactModel(Protocol):
    """architecture.md S7.6. `spec` is accepted for interface conformance
    (a future model might need `BikeSpec`-level data at call time); the
    models in this file carry everything they need from their own
    constructor arguments instead and ignore it, matching
    `forces/weight.py`/`forces/aerodynamic.py`'s own established pattern
    of taking coefficients at construction rather than via a runtime
    spec object. `cache` is the x*-warm-start dict, owned and persisted
    across integration steps by the caller (`forces/contact.py`'s
    `ContactForce` bridge) and threaded through here by reference."""

    def wrenches(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        road: Road,
        spec: Any,
        cache: MutableMapping[str, float],
    ) -> list[ContactWrench]: ...

    def constraints(self) -> list[Constraint]:
        """Non-empty only for `EventDrivenContact` (T11): the regularized
        default model has no constraint rows (architecture.md S7.6)."""
        ...

    def events(self) -> list[EventFn]:
        """Mode-switch detectors for `solve_ivp`; empty for the
        regularized default (no discrete modes to detect)."""
        ...

    def state(self) -> ContactState:
        """The last-computed `ContactState`, for `Trajectory`/`ModeLog`
        diagnostics."""
        ...


@dataclass(frozen=True)
class RegularizedContact:
    """Hunt-Crossley normal force (sign-corrected, see module docstring) +
    Stribeck-smoothed Coulomb friction, one instance per wheel
    (architecture.md S7.6.2, D-9).

    `k_N_override` lets a caller pin an explicit stiffness (matching
    `core.params.ContactParams.k_N`'s `None = derive at runtime` contract);
    `None` (the default) derives `k_N` from `delta_target` as S7.6.2
    requires. `m_eff` is this wheel's own static load share, in kg (i.e.
    this wheel's static normal reaction divided by `g` -- the caller
    supplies it since only the full-system static-load split, computed
    once from the assembled bike's geometry, knows it; a standalone-wheel
    test simply passes that wheel's own mass).
    """

    name: str
    body: str
    radius: float
    c_rr: float
    mu_s: float
    mu_k: float
    M_total: float
    m_eff: float
    g: float
    delta_target: float = 0.001
    zeta_N: float = 1.0
    v_eps: float = 0.01
    v_stribeck: float = 0.01
    e: float = 1.0
    axle_point: tuple[float, float] = (0.0, 0.0)
    k_N_override: float | None = None

    k_N: float = field(init=False)
    c_N: float = field(init=False)
    c_HC: float = field(init=False)
    _last_state: ContactState | None = field(init=False, default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.mu_k > self.mu_s:
            raise ValueError(f"mu_k ({self.mu_k}) must be <= mu_s ({self.mu_s})")
        k_N = (
            self.k_N_override
            if self.k_N_override is not None
            else self.M_total * self.g / self.delta_target
        )
        c_N = 2.0 * self.zeta_N * math.sqrt(k_N * self.m_eff)
        # Rescaled by delta_target -- see module docstring's "Second
        # correction" for the derivation (c_HC = c_N/k_N alone makes
        # zeta_N meaningless at the system's actual operating point).
        c_HC = c_N / (1.5 * k_N * self.delta_target)
        object.__setattr__(self, "k_N", k_N)
        object.__setattr__(self, "c_N", c_N)
        object.__setattr__(self, "c_HC", c_HC)

    # -- ContactModel protocol -------------------------------------------

    def wrenches(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        road: Road,
        spec: Any,
        cache: MutableMapping[str, float],
    ) -> list[ContactWrench]:
        geom = contact_geometry(
            q,
            u,
            layout,
            body=self.body,
            radius=self.radius,
            c_rr=self.c_rr,
            road=road,
            v_eps=self.v_eps,
            axle_point=self.axle_point,
            x_guess=cache.get(self.body),
            t=t,
        )
        cache[self.body] = geom.x_star
        object.__setattr__(self, "_last_state", geom)

        delta = max(0.0, -geom.g_N)
        if delta <= 0.0:
            R_z = 0.0
        else:
            # Corrected sign (module docstring): `-1.5*c_HC*g_N_dot`, not
            # `+1.5*c_HC*g_N_dot` as architecture.md S7.6.2 literally reads.
            R_z = max(0.0, self.k_N * delta**self.e * (1.0 - 1.5 * self.c_HC * geom.g_N_dot))

        mu = self.mu_k + (self.mu_s - self.mu_k) * math.exp(-((geom.v_slip / self.v_stribeck) ** 2))
        sigma_slip = float(numpy_backend.sign_smooth(geom.v_slip, self.v_eps))
        R_x = -mu * R_z * sigma_slip

        force_world = R_x * geom.x_hat_0 + R_z * geom.z_hat_0
        wrench = ContactWrench(
            body=self.body,
            force_world=force_world,
            point_world=geom.contact_point_world,
        )
        return [wrench]

    def constraints(self) -> list[Constraint]:
        return []

    def events(self) -> list[EventFn]:
        return []

    def state(self) -> ContactState:
        if self._last_state is None:
            raise RuntimeError(
                f"RegularizedContact {self.name!r}: state() called before the first wrenches() call"
            )
        return self._last_state

    # -- provenance (architecture.md S12.2: "every regularization
    # parameter ... recorded") ------------------------------------------

    def regularization_params(self) -> dict[str, float]:
        """Every regularization constant this model derived or was given,
        keyed for direct use in `core.runio.build_run_summary`'s
        `tolerances`/`solver` dicts (architecture.md S12.2: "a result whose
        regularization parameters are not recorded is not a result")."""
        return {
            f"{self.body}.k_N": self.k_N,
            f"{self.body}.c_N": self.c_N,
            f"{self.body}.c_HC": self.c_HC,
            f"{self.body}.delta_target": self.delta_target,
            f"{self.body}.zeta_N": self.zeta_N,
            f"{self.body}.v_eps": self.v_eps,
            f"{self.body}.v_stribeck": self.v_stribeck,
            f"{self.body}.mu_s": self.mu_s,
            f"{self.body}.mu_k": self.mu_k,
        }


def merged_regularization_params(models: Sequence[RegularizedContact]) -> dict[str, float]:
    """Merge several wheels' `regularization_params()` into one dict (one
    `RegularizedContact` per wheel, architecture.md S7.6's per-wheel model;
    a multi-wheel bike (T7) has one of these per wheel body)."""
    merged: dict[str, float] = {}
    for model in models:
        merged.update(model.regularization_params())
    return merged
