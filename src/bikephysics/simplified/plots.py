"""simplified/plots.py -- Simplified-tier result figures (architecture.md
S3.1, S6).

Migrated from the old `bikephysics/plotting.py` via `git mv` (ticket T1,
architecture.md S3.1: "the only module whose substance survives; the
6-panel layout is good and the panel set just grows"). That source plotted
a `bikephysics.simulation.SimulationResult` (deleted in T1 -- it modeled a
single scalar drive force `F(t)` that the current derivation does not
have); this module cannot keep importing it.

T1 therefore stops here: it moves the file and removes the broken import,
but does not invent the new panel set against `core.results.Trajectory`
ahead of schedule -- that is explicitly T4's job (task.md T4 step 3:
"extend the migrated 6-panel figure into the panel set the current
derivation needs"). The original 7-panel `BikePlotter.plot`/`compare` logic
(F(t)/P(t)/x/v/a/theta_23/theta_4 stacked subplots, and the multi-run v(t)
overlay) is preserved in git history at this file's pre-move path for T4 to
read when it rebuilds this module against `Trajectory`.
"""

from __future__ import annotations

__all__: list[str] = []
