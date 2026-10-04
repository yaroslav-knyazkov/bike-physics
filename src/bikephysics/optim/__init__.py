"""bikephysics.optim -- control/trajectory optimization (architecture.md S8).

Milestone 1 (ticket T8): control parametrization + scipy on the Simplified
model (D-11 option A). Everything in this package is written against
`optim.forward.ForwardModel` only, never against a tier module directly
(architecture.md S4: `optim` is one of the two packages, alongside `cli`,
permitted to import a tier -- see `pyproject.toml`'s `TID251` exemption),
so swapping the forward model is a one-line config change.
"""

from __future__ import annotations
