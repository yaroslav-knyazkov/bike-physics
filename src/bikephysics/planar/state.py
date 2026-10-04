"""planar/state.py -- SystemLayout: all index bookkeeping for the planar
engine (architecture.md S7.2, S7.4; latex/2d_systems/overview.tex;
latex/2d_assembly/frame_wheels.tex).

Maximal coordinates (D-8): every body carries its own 3 DOF,
`q_k = (x_k, z_k, theta_k)`, stacked as `q = [q_1, q_2, ...]` in a fixed
body order, with `u = q_dot` sharing the same layout. `SystemLayout` is the
*only* place that turns a body name (or a constraint name, for the active
rows of a KKT solve) into a numeric index or slice -- architecture.md S7.2:
"No raw index arithmetic anywhere else." Every other planar module -- body,
forces, constraints, system, solver, integrate -- asks a `SystemLayout`
instance for indices rather than computing an offset inline.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt
from scipy import sparse

FloatArray = npt.NDArray[np.float64]

_DOF_PER_BODY = 3  # (x, z, theta) -- latex/2d_models/rigidbody.tex


class _NamedDim(Protocol):
    """The minimal shape `constraint_row_map`/`total_dim` need from a
    constraint: a `name` and a `dim`. Spelled out locally (rather than
    importing `constraints.base.Constraint`) so `state.py` has no
    dependency on the constraint/force protocols -- it is the other way
    around. Read-only `@property` members, not plain attributes, so a
    frozen-dataclass `Constraint` (this project's convention) structurally
    satisfies both this protocol and `constraints.base.Constraint`
    consistently (see that module's docstring for why)."""

    @property
    def name(self) -> str: ...

    @property
    def dim(self) -> int: ...


@dataclass(frozen=True)
class SystemLayout:
    """`q = [x_1,z_1,theta_1, x_2a,z_2a,theta_2a, ...]`, `u = q_dot`, both
    length `nq = 3 * n_bodies` (architecture.md S7.2). Body order is fixed
    at construction (by `MultibodySystem`, in registration order) and
    determines every body's slice.

    Constraint rows are deliberately *not* a fixed attribute here: which
    constraints are active can change at runtime (a dropped unilateral
    row, a contact mode switch), so the row map is a pure function of
    *whichever* constraint list is active this solve
    (`constraint_row_map`), never a cached assumption that could go stale.
    """

    body_names: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.body_names:
            raise ValueError("SystemLayout needs at least one body")
        if len(set(self.body_names)) != len(self.body_names):
            raise ValueError(f"duplicate body names in layout: {self.body_names}")

    @property
    def n_bodies(self) -> int:
        return len(self.body_names)

    @property
    def nq(self) -> int:
        """Size of `q` (and of `u`; `y = [q, u]` has size `2 * nq`)."""
        return _DOF_PER_BODY * self.n_bodies

    def _body_index(self, body: str) -> int:
        try:
            return self.body_names.index(body)
        except ValueError as exc:
            raise KeyError(f"unknown body {body!r}; known bodies: {self.body_names}") from exc

    def body_slice(self, body: str) -> slice:
        """The 3 contiguous `q`/`u` indices `(x, z, theta)` of `body`."""
        i = self._body_index(body)
        start = _DOF_PER_BODY * i
        return slice(start, start + _DOF_PER_BODY)

    def idx_x(self, body: str) -> int:
        return self.body_slice(body).start

    def idx_z(self, body: str) -> int:
        return self.body_slice(body).start + 1

    def idx_theta(self, body: str) -> int:
        return self.body_slice(body).start + 2

    # -- named accessors into q (position-level) ------------------------

    def x(self, q: FloatArray, body: str) -> float:
        return float(q[self.idx_x(body)])

    def z(self, q: FloatArray, body: str) -> float:
        return float(q[self.idx_z(body)])

    def theta(self, q: FloatArray, body: str) -> float:
        return float(q[self.idx_theta(body)])

    def position(self, q: FloatArray, body: str) -> FloatArray:
        """`(x, z)` CoG world position of `body`."""
        sl = self.body_slice(body)
        return np.asarray(q[sl.start : sl.start + 2], dtype=float)

    # -- named accessors into u (velocity-level); same slice as q -------

    def vx(self, u: FloatArray, body: str) -> float:
        return float(u[self.idx_x(body)])

    def vz(self, u: FloatArray, body: str) -> float:
        return float(u[self.idx_z(body)])

    def omega(self, u: FloatArray, body: str) -> float:
        return float(u[self.idx_theta(body)])

    def velocity(self, u: FloatArray, body: str) -> FloatArray:
        """`(vx, vz)` CoG world velocity of `body`."""
        sl = self.body_slice(body)
        return np.asarray(u[sl.start : sl.start + 2], dtype=float)

    # -- relative spin, latex/2d_assembly/frame_wheels.tex ---------------

    def phi(self, q: FloatArray, body: str, reference: str = "1") -> float:
        """`phi_k = theta_k - theta_1` (frame_wheels.tex): `body`'s spin
        relative to `reference` (frame+rider, body `"1"`, by default). A
        *derived* quantity, not a DOF of `q` -- no state slice is spent on
        it, matching architecture.md S7.4's "not a constraint -- it is the
        definition of phi"."""
        return self.theta(q, body) - self.theta(q, reference)

    def phi_rate(self, u: FloatArray, body: str, reference: str = "1") -> float:
        """`phi_dot_k = omega_k - omega_1`, the rate form of `phi`."""
        return self.omega(u, body) - self.omega(u, reference)

    # -- constraint row bookkeeping ---------------------------------------

    @staticmethod
    def constraint_row_map(constraints: Sequence[_NamedDim]) -> dict[str, slice]:
        """Row range of each constraint in the *active* list `constraints`,
        in the order given. Recomputed every time the active set changes
        (never persisted on `SystemLayout` itself -- see the class
        docstring)."""
        rows: dict[str, slice] = {}
        offset = 0
        for c in constraints:
            rows[c.name] = slice(offset, offset + c.dim)
            offset += c.dim
        return rows

    @staticmethod
    def total_dim(constraints: Sequence[_NamedDim]) -> int:
        """Total active-constraint row count `n_c` for the KKT system's
        `(nq + n_c) x (nq + n_c)` size."""
        return sum(c.dim for c in constraints)

    # -- Radau/BDF/LSODA sparsity pattern ---------------------------------

    def jac_sparsity(self) -> sparse.csr_matrix:
        """Sparsity pattern of `d(y_dot)/dy` for `y = [q, u]`, shape
        `(2*nq, 2*nq)`, for `scipy.integrate.solve_ivp`'s `jac_sparsity`
        argument (Radau/BDF/LSODA).

        The kinematic half is exactly sparse: `q_dot = u`, so
        `d(q_dot)/dq = 0` and `d(q_dot)/du = I`. The dynamic half (`u_dot =
        a(t, q, u)`) is *not* sparse in general for this engine: `a` comes
        from one global KKT solve (`M a + J^T lambda = h`, `J a = zeta`)
        whose dense inverse couples every body through whichever
        constraints are active, so the conservative -- and correct --
        pattern for that half is fully dense. A future per-element
        sparsity contribution could tighten this once there are enough
        bodies for it to matter; at this engine's size (a handful of
        bodies) it is not worth the complexity.
        """
        nq = self.nq
        n = 2 * nq
        pattern = np.zeros((n, n), dtype=bool)
        pattern[0:nq, nq:n] = np.eye(nq, dtype=bool)  # d(q_dot)/du = I
        pattern[nq:n, :] = True  # d(a)/d(q, u): dense (global KKT coupling)
        return sparse.csr_matrix(pattern)
