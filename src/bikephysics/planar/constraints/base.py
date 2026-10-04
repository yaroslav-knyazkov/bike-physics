"""planar/constraints/base.py -- Constraint protocol (architecture.md S7.4).

```
class Constraint(Protocol):
    name: str
    dim: int
    kind: Literal["bilateral", "unilateral"]
    def residual(self, q, layout, xp): ...           # c(q),      shape (dim,)
    def jacobian(self, q, layout, xp): ...           # J = dc/dq, shape (dim, nq)
    def rate_term(self, q, u, layout, xp): ...       # -J_dot u,  shape (dim,)
    def multiplier_bounds(self): ...                 # None | (lo, hi)
```

`residual`/`jacobian`/`rate_term` are the three position/velocity-level
quantities `system.py` needs to assemble one active-constraint block
(architecture.md S7.7 step 3: `zeta = rate_term - 2*alpha*(J @ u) -
beta^2*c`, Baumgarte-stabilized). `rate_term` returns `-J_dot u` directly
(already contracted with the current velocity), not `J_dot` itself --
every constraint can compute this in closed form from the current `(q,
u)` without ever forming the full `d(J)/dt` matrix (see
`planar/body.py::RigidBody.centripetal_term`, which both concrete
constraints below are built from).

`multiplier_bounds` returns `None` for a bilateral constraint (always
enforced, multiplier unconstrained) or `(lo, hi)` for a unilateral one
(e.g. the chain's `(0, +inf)`, architecture.md S7.4). `solver.py`'s
generic unilateral trial loop drops any active row whose solved
multiplier falls outside its bounds and re-solves.

Implementations are plain classes satisfying this protocol structurally
(duck-typed, like `core.mathx.NumpyBackend` vs. `MathBackend`) -- they do
not subclass `Constraint`.
"""

from __future__ import annotations

from typing import Literal, Protocol

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]

ConstraintKind = Literal["bilateral", "unilateral"]


class Constraint(Protocol):
    """architecture.md S7.4.

    `name`/`dim`/`kind` are declared as read-only `@property` members
    rather than plain class attributes: mypy treats a plain Protocol
    attribute as requiring a *settable* member, which every concrete
    constraint here (a frozen dataclass, per this project's convention)
    does not have -- a property-typed protocol member is satisfied by a
    frozen dataclass field without a false type error, with no change in
    how callers use `constraint.name`/`.dim`/`.kind`.
    """

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...

    @property
    def kind(self) -> ConstraintKind: ...

    def residual(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        """`c(q)`, shape `(dim,)`; `0` when satisfied."""
        ...

    def jacobian(self, q: FloatArray, layout: SystemLayout, xp: MathBackend) -> FloatArray:
        """`J = dc/dq`, shape `(dim, layout.nq)`."""
        ...

    def rate_term(
        self, q: FloatArray, u: FloatArray, layout: SystemLayout, xp: MathBackend
    ) -> FloatArray:
        """`-J_dot u`, shape `(dim,)` -- the part of `c_ddot` that is
        *not* linear in the unknown accelerations (see module docstring).
        """
        ...

    def multiplier_bounds(self) -> tuple[float, float] | None:
        """`None` for `kind == "bilateral"`; `(lo, hi)` for
        `kind == "unilateral"`."""
        ...
