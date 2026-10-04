"""bikephysics.planar.contact -- ground-contact geometry and force laws
(architecture.md S7.6; latex/2d_assembly/wheels_ground.tex).

`geometry.py` is the shared per-wheel geometry block (S7.6.1): the
warm-started contact-point search, gap, rolling-resistance offset, and
slip velocity, used by every contact model variant. `regularized.py` is
the default `RegularizedContact` force law (S7.6.2, D-9). `EventDrivenContact`
(S7.6.3) and `ComplementarityContact` (S7.6.4) are later tickets (T11+).
"""

from __future__ import annotations
