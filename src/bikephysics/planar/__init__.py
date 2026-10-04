"""bikephysics.planar -- the generic 2D rigid-body engine (architecture.md
S7). Imports only `core`, never `simplified`/`optim`/`cli` (architecture.md
S4).

T5 builds the generic engine proper: `state.py` (index bookkeeping),
`body.py` (rigid bodies and point kinematics), `system.py`/`solver.py`
(element registry and the index-1 KKT solve, D-8), `integrate.py` (the
`solve_ivp` driver), plus the two element protocols
(`forces/base.py`, `constraints/base.py`) and their first concrete
implementations (`Weight`, `Aerodynamic`, `RevoluteJoint`, `WeldJoint`).
Deliberately bike-free: no wheel, road, tire, chain, or pedal reference
belongs in `system.py`/`solver.py` -- those are T6 (contact) and T7
(drivetrain)'s concern, built against these protocols without
re-implementing the engine.
"""

from __future__ import annotations
