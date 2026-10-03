"""bikephysics -- longitudinal and planar bike dynamics: simulation,
analysis, and trajectory optimization (see `architecture.md` and `latex/`
for the physics derivation).

architecture.md S3 reserves this module for "version + the 6 top-level
public names only". T1 only populates `core`, so there are no tier-level
public names to re-export yet; `__version__` is exposed now, and the
re-exports are added as each tier/app ships (T2 onward).
"""

from __future__ import annotations

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("bikephysics")
except _metadata.PackageNotFoundError:  # pragma: no cover - editable/unbuilt tree
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
