"""planar/system.py -- MultibodySystem: element registry and M/h/J/zeta
assembly (architecture.md S7.2, S7.3, S7.4, S7.7 steps 1-3).

Generic 2D rigid-body engine, built and proven on analytic toy problems
only (a free body, a pendulum, a double pendulum, two welded bodies, a
hanging pin): this module and `solver.py` deliberately carry no
domain-specific assumption of any kind -- extensibility would already be
broken the moment a vehicle-specific detail leaked into either file
(architecture.md S7). Ground contact and the drivetrain are future
tickets, consuming this module only through the `ForceElement`/
`Constraint` protocols.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend, numpy_backend
from bikephysics.planar.body import RigidBody
from bikephysics.planar.constraints.base import Constraint
from bikephysics.planar.forces.base import ForceElement
from bikephysics.planar.forces.weight import Weight
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True)
class ConstraintBlock:
    """One assembled active-constraint block (architecture.md S7.7 step 3):
    stacked Jacobian `J` (`n_c x nq`), position residual `c`, velocity
    residual `c_dot = J @ u`, the `-J_dot u` rate term, the Baumgarte-
    stabilized `zeta`, and the row map used to build all of them."""

    names: tuple[str, ...]
    row_map: dict[str, slice]
    J: FloatArray
    c: FloatArray
    c_dot: FloatArray
    rate: FloatArray
    zeta: FloatArray


class MultibodySystem:
    """Generic element registry: bodies, force elements, constraints.
    Owns the constant block-diagonal mass matrix and the `SystemLayout`
    derived from body-registration order. Nothing here may assume
    anything about what the bodies/forces/constraints *represent* --
    that is T6/T7/`assembly.py`'s job, built against `ForceElement`/
    `Constraint` only.
    """

    def __init__(self, xp: MathBackend = numpy_backend) -> None:
        self.xp = xp
        self._bodies: dict[str, RigidBody] = {}
        self._forces: list[ForceElement] = []
        self._constraints: list[Constraint] = []
        self._layout: SystemLayout | None = None
        self._mass_matrix: FloatArray | None = None

    # -- registration --------------------------------------------------

    def add_body(self, body: RigidBody) -> None:
        if self._layout is not None:
            raise RuntimeError(
                f"cannot add body {body.name!r}: the layout is already built "
                "(a body must be registered before the first mass_matrix()/"
                "layout access)"
            )
        if body.name in self._bodies:
            raise ValueError(f"body {body.name!r} already registered")
        self._bodies[body.name] = body

    def add_force(self, element: ForceElement) -> None:
        self._forces.append(element)

    def add_gravity(self, g: float, bodies: Sequence[str] | None = None) -> None:
        """Convenience: register a `Weight` element for every body (or the
        named subset `bodies`), reading each body's own `RigidBody.mass`
        as the single source of truth -- callers never duplicate a mass
        value by hand (architecture.md S7.3's `Weight` row)."""
        targets = bodies if bodies is not None else tuple(self._bodies)
        for name in targets:
            mass = self._bodies[name].mass
            self.add_force(Weight(name=f"weight_{name}", body=name, mass=mass, g=g))

    def add_constraint(self, constraint: Constraint) -> None:
        self._constraints.append(constraint)

    # -- layout / registry access ---------------------------------------

    @property
    def layout(self) -> SystemLayout:
        if self._layout is None:
            if not self._bodies:
                raise RuntimeError("MultibodySystem has no registered bodies")
            self._layout = SystemLayout(tuple(self._bodies))
        return self._layout

    def body(self, name: str) -> RigidBody:
        return self._bodies[name]

    @property
    def bodies(self) -> Mapping[str, RigidBody]:
        return dict(self._bodies)

    @property
    def constraints(self) -> tuple[Constraint, ...]:
        return tuple(self._constraints)

    # -- mass matrix (architecture.md S7.2: constant) --------------------

    def mass_matrix(self) -> FloatArray:
        """Constant block-diagonal `M = diag(m_1, m_1, I_1, m_2, m_2, I_2,
        ...)` (architecture.md S7.2: "a planar body's mass matrix in CoG
        coordinates has no configuration dependence"). Built once and
        cached."""
        if self._mass_matrix is None:
            layout = self.layout
            M = np.zeros((layout.nq, layout.nq))
            for name, b in self._bodies.items():
                sl = layout.body_slice(name)
                M[sl.start, sl.start] = b.mass
                M[sl.start + 1, sl.start + 1] = b.mass
                M[sl.start + 2, sl.start + 2] = b.inertia
            self._mass_matrix = M
        return self._mass_matrix

    # -- h assembly (architecture.md S7.7 step 2) -------------------------

    def generalized_force(self, t: float, q: FloatArray, u: FloatArray) -> FloatArray:
        """Additive `h` assembly: every registered `ForceElement.accumulate`
        adds into the same vector, starting from zero."""
        layout = self.layout
        h = np.zeros(layout.nq)
        for element in self._forces:
            element.accumulate(t, q, u, layout, h, self.xp)
        return h

    # -- constraint block assembly (architecture.md S7.7 step 3) ---------

    def constraint_block(
        self,
        q: FloatArray,
        u: FloatArray,
        active: Sequence[Constraint] | None = None,
        *,
        alpha: float,
        beta: float,
    ) -> ConstraintBlock:
        """Stack the *active* constraints' `J`, `c`, `c_dot`, and `rate`,
        and assemble the Baumgarte-stabilized `zeta = rate_term -
        2*alpha*c_dot - beta^2*c` (architecture.md S7.7 step 3). `active`
        defaults to every registered constraint; `solver.py`'s unilateral
        trial loop passes a trimmed subset back in on each retry."""
        layout = self.layout
        cons = tuple(active) if active is not None else self._constraints
        row_map = layout.constraint_row_map(cons)
        n_c = layout.total_dim(cons)
        J = np.zeros((n_c, layout.nq))
        c = np.zeros(n_c)
        rate = np.zeros(n_c)
        for con in cons:
            rows = row_map[con.name]
            J[rows, :] = con.jacobian(q, layout, self.xp)
            c[rows] = con.residual(q, layout, self.xp)
            rate[rows] = con.rate_term(q, u, layout, self.xp)
        c_dot = J @ u
        zeta = rate - 2.0 * alpha * c_dot - beta**2 * c
        return ConstraintBlock(
            names=tuple(con.name for con in cons),
            row_map=row_map,
            J=J,
            c=c,
            c_dot=c_dot,
            rate=rate,
            zeta=zeta,
        )
