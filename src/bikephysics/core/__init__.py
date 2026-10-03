"""bikephysics.core -- shared physics/infrastructure (architecture.md S4,
S5).

Depends on nothing above it: not `simplified`, `planar`, `optim`, or `cli`
(enforced by the ruff import-boundary config in `pyproject.toml`). T1
populates `mathx`, `units`, `results`, `runio`, and `plotting`; `params`,
`atmosphere`, `aero`, `road`, `pedals`, `brakes`, and `limits` land in T2.
"""

from __future__ import annotations
