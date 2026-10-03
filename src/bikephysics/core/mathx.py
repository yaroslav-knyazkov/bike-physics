"""core/mathx.py -- backend-neutral math namespace (architecture.md S5.2).

A thin namespace object exposing only the operations the physics needs
(`sin cos tan atan atan2 sqrt exp tanh abs maximum minimum where sign_smooth
hypot zeros stack solve`), so `simplified/model.py` and (eventually)
`planar/*` can be written once against a backend argument `xp` and later
retargeted at a symbolic backend (CasADi, T9) without a rewrite.

`numpy_backend` is the only implementation available today. `casadi_backend`
is a stub: every attribute access returns a callable that raises
`NotImplementedError` naming the ticket that fills it in (T9, architecture.md
S8.4 / D-12).
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np


class MathBackend(Protocol):
    """The namespace physics code is written against. Both backends
    implement exactly this surface; nothing outside this list is
    guaranteed to exist on either backend."""

    sin: Any
    cos: Any
    tan: Any
    atan: Any
    atan2: Any
    sqrt: Any
    exp: Any
    tanh: Any
    abs: Any
    maximum: Any
    minimum: Any
    where: Any
    hypot: Any
    zeros: Any
    stack: Any
    solve: Any

    def sign_smooth(self, v: Any, eps: float) -> Any: ...


class NumpyBackend:
    """numpy-backed `MathBackend`. The default and, until T9, the only
    backend; mandatory for `simplified/model.py` from day one (architecture.md
    S5.2) and usable, but not required, by `planar/*`."""

    sin = staticmethod(np.sin)
    cos = staticmethod(np.cos)
    tan = staticmethod(np.tan)
    atan = staticmethod(np.arctan)
    atan2 = staticmethod(np.arctan2)
    sqrt = staticmethod(np.sqrt)
    exp = staticmethod(np.exp)
    tanh = staticmethod(np.tanh)
    abs = staticmethod(np.abs)
    maximum = staticmethod(np.maximum)
    minimum = staticmethod(np.minimum)
    where = staticmethod(np.where)
    hypot = staticmethod(np.hypot)
    zeros = staticmethod(np.zeros)
    stack = staticmethod(np.stack)
    solve = staticmethod(np.linalg.solve)

    @staticmethod
    def sign_smooth(v: Any, eps: float) -> Any:
        """Smoothed sign function `tanh(v / eps)` -- the single, documented
        regularization of the LaTeX `sigma = sgn(x_dot)` (architecture.md
        S5.2). `eps` must come from `BikeSpec` / solver settings (no magic
        numbers, S12.3) and is reported in every run's `summary.json`
        (S12.2); never hardcode it at a call site."""
        return np.tanh(np.asarray(v) / eps)


class CasadiBackend:
    """CasADi-backed `MathBackend`, deferred to ticket T9 (architecture.md
    S8.4, D-11, D-12). Every attribute lookup returns a callable that raises
    `NotImplementedError` so a premature call fails loudly and names the
    ticket that will implement it, rather than silently falling through to
    numpy semantics."""

    def __getattr__(self, name: str) -> Any:
        def _not_implemented(*_args: Any, **_kwargs: Any) -> Any:
            raise NotImplementedError(
                f"CasadiBackend.{name} is not implemented yet. CasADi direct-"
                "collocation support (and this backend) lands in ticket T9 "
                "(architecture.md S8.4 / D-11, D-12)."
            )

        return _not_implemented


numpy_backend: MathBackend = NumpyBackend()
casadi_backend: MathBackend = CasadiBackend()

_BACKENDS: dict[str, MathBackend] = {"numpy": numpy_backend, "casadi": casadi_backend}


def get_backend(name: str = "numpy") -> MathBackend:
    """Return the math backend namespace by name (`"numpy"` default, or
    `"casadi"`). Raises `ValueError` for anything else."""
    try:
        return _BACKENDS[name]
    except KeyError as exc:
        known = sorted(_BACKENDS)
        raise ValueError(f"Unknown math backend {name!r}; expected one of {known}") from exc
