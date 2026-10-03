"""bikephysics.simplified -- the single-DOF longitudinal tier (architecture.md
S6). Imports only `core`, never `planar`/`optim`/`cli` (architecture.md S4).

T1 only migrates `plots.py` (via `git mv`, see its module docstring);
`model.py`, `analysis.py`, and `simulate.py` land in T3/T4.
"""

from __future__ import annotations
