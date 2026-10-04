"""planar/forces/base.py -- ForceElement protocol (architecture.md S7.3).

```
class ForceElement(Protocol):
    name: str
    def accumulate(self, t, q, u, layout, h, xp) -> None:   # adds into h
    def diagnostics(self, t, q, u, layout) -> dict:          # optional
```

`accumulate` adds this element's contribution into the generalized force
vector `h` (shape `(layout.nq,)`) *in place*; it never returns a value or
replaces `h` wholesale, since `system.py` assembles `h` additively from
every registered element (architecture.md S7.7 step 2: "`h = 0`; for each
`ForceElement`: `element.accumulate(...)`"). `diagnostics` is optional
book-keeping for `Trajectory`/`ModeLog` (e.g. the aerodynamic state last
evaluated); an element with nothing to report returns `{}`.

`xp` is the `core.mathx.MathBackend` namespace (architecture.md S5.2):
most elements (`Weight`, `Aerodynamic`) are plain-numpy today and accept
but ignore it; it is part of the signature so a future CasADi-backed
element (T9) is a drop-in `ForceElement`, not a protocol change.

Implementations are plain classes satisfying this protocol structurally
(duck-typed, like `core.mathx.NumpyBackend` vs. `MathBackend`) -- they do
not subclass `ForceElement`.
"""

from __future__ import annotations

from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from bikephysics.core.mathx import MathBackend
from bikephysics.planar.state import SystemLayout

FloatArray = npt.NDArray[np.float64]


class ForceElement(Protocol):
    """architecture.md S7.3.

    `name` is declared as a read-only `@property` rather than a plain
    `name: str` class attribute: mypy treats a plain Protocol attribute as
    requiring a *settable* member, which every concrete element here (a
    frozen dataclass, per this project's convention) does not have -- a
    property-typed protocol member is satisfied by a frozen dataclass
    field without a false type error, with no change in how callers use
    `element.name`.
    """

    @property
    def name(self) -> str: ...

    def accumulate(
        self,
        t: float,
        q: FloatArray,
        u: FloatArray,
        layout: SystemLayout,
        h: FloatArray,
        xp: MathBackend,
    ) -> None:
        """Add this element's contribution into `h` (shape
        `(layout.nq,)`), mutated in place. Returns nothing."""
        ...

    def diagnostics(
        self, t: float, q: FloatArray, u: FloatArray, layout: SystemLayout
    ) -> dict[str, Any]:
        """Optional per-call diagnostics for `Trajectory`/`ModeLog`
        (e.g. the aerodynamic coefficients last evaluated). An element
        with nothing to report returns `{}`."""
        ...
